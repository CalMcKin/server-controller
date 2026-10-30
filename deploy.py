#!/usr/bin/env python3

import argparse
import grp
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UNIT_DIR = Path("/etc/systemd/system")
SUDOERS_DIR = Path("/etc/sudoers.d")
CONFIG_PATH = ROOT / ".env"


def command(args, *, check=True):
    return subprocess.run(args, check=check, text=True, capture_output=True)


def detect_controller_host():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("192.0.2.1", 80))
            return connection.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def render_template(path, values):
    content = path.read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace(f"@{key}@", value)
    if re.search(r"@[A-Z_]+@", content):
        raise ValueError(f"Unexpanded template token in {path}")
    return content


def atomic_install(path, content, mode, owner, group, validator=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.chown(temporary_path, owner, group)
        os.chmod(temporary_path, mode)
        if validator:
            validator(temporary_path)
        os.replace(temporary_path, path)
    except OSError:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise
    except Exception:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise


def validate_sudoers(path):
    visudo = shutil.which("visudo")
    if not visudo:
        raise RuntimeError("visudo is required to safely install the sudoers rule")
    result = command([visudo, "-cf", str(path)], check=False)
    if result.returncode:
        raise RuntimeError(f"Invalid sudoers rule: {result.stderr.strip() or result.stdout.strip()}")


def ensure_config(args, account):
    if not CONFIG_PATH.is_file():
        example = ROOT / ".env.example"
        if not example.is_file():
            raise FileNotFoundError(f"Missing deployment template: {example}")
        shutil.copyfile(example, CONFIG_PATH)

    sys.path.insert(0, str(ROOT))
    import server

    server.ENV_PATH = CONFIG_PATH
    config = server.load_config()
    updates = dict(config)
    if args.role:
        updates["DEPLOY_ROLE"] = args.role
    if args.controller_host:
        updates["CONTROLLER_HOST"] = args.controller_host
    elif updates["DEPLOY_ROLE"] in {"controller", "all"} and config["CONTROLLER_HOST"] == "127.0.0.1":
        updates["CONTROLLER_HOST"] = detect_controller_host()

    paper_dir = args.paper_dir
    if paper_dir:
        updates["PAPER_DIR"] = paper_dir
    elif args.user != "calmc" and updates["PAPER_DIR"] == "/home/calmc/paper":
        updates["PAPER_DIR"] = str(Path(account.pw_dir) / "paper")

    updates = server.validate_config(updates)
    if updates != config:
        server.write_config(updates)
    return server, updates


def validate_paths(config, account, group, install_dir):
    paper_dir = Path(config["PAPER_DIR"])
    if not install_dir.is_dir() or not (install_dir / "server.py").is_file():
        raise ValueError(f"Controller source directory is incomplete: {install_dir}")
    role = config["DEPLOY_ROLE"]
    installs_paper = role in {"game-server", "all"}
    installs_controller = role in {"controller", "all"}
    installs_agent = role == "game-server" or (
        role == "all" and config["CONTROL_MODE"] == "remote"
    )
    if installs_controller and config["CONTROL_MODE"] == "local" and role == "controller":
        raise ValueError("DEPLOY_ROLE=controller requires CONTROL_MODE=remote")
    if installs_agent and len(config["REMOTE_API_TOKEN"]) < 32:
        raise ValueError("REMOTE_API_TOKEN must be at least 32 characters to install the game-server API")

    paths = [install_dir]
    if installs_paper:
        paths.append(paper_dir)
    for path in paths:
        if not re.fullmatch(r"/[A-Za-z0-9._/-]+", str(path)):
            raise ValueError(f"Deployment path contains unsupported characters: {path}")
    if installs_paper and not paper_dir.is_dir():
        raise ValueError(
            f"Paper server directory does not exist: {paper_dir}. "
            "Install the server before running deployment."
        )
    if installs_agent and not (install_dir / "minecraft_agent.py").is_file():
        raise ValueError(f"Minecraft host API is missing: {install_dir / 'minecraft_agent.py'}")
    if installs_paper:
        start_script = paper_dir / "start.sh"
        if not start_script.is_file():
            raise ValueError(f"Paper start script does not exist: {start_script}")
        runuser = shutil.which("runuser")
        if not runuser:
            raise RuntimeError("runuser is required to verify the Paper service account")
        result = command(
            [runuser, "-u", account.pw_name, "--", "/usr/bin/test", "-x", str(start_script)],
            check=False,
        )
        if result.returncode:
            raise PermissionError(f"{account.pw_name} cannot execute {start_script}")

    required_commands = ["systemctl"]
    if installs_paper:
        required_commands.extend(["tmux", "pgrep"])
    for required in required_commands:
        if not shutil.which(required):
            raise RuntimeError(f"Required command not found: {required}")
    if installs_paper and group.gr_name != account.pw_name:
        result = command(
            [runuser, "-u", account.pw_name, "--", "/usr/bin/test", "-x", str(paper_dir)],
            check=False,
        )
        if result.returncode:
            raise PermissionError(f"{account.pw_name} cannot access {paper_dir}")


def main():
    parser = argparse.ArgumentParser(
        description="Install the Paper and Minecraft Control Room system services."
    )
    parser.add_argument("--user", default="calmc", help="Linux account that owns and runs Paper")
    parser.add_argument("--group", help="Service group (defaults to the user's primary group)")
    parser.add_argument("--paper-dir", help="Paper server directory (defaults to .env PAPER_DIR)")
    parser.add_argument("--controller-host", help="Browser address; updates CONTROLLER_HOST in .env")
    parser.add_argument(
        "--role",
        choices=("controller", "game-server", "all"),
        help="Install only the controller, only the game-server API/Paper, or both",
    )
    parser.add_argument(
        "--start-paper",
        action="store_true",
        help="Start Paper immediately after enabling its service",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and paths without installing services",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise PermissionError("Run deploy.py as root, for example: sudo ./deploy.py")
    if not (ROOT / "deploy/systemd/paper.service.in").is_file():
        raise FileNotFoundError("Run this script from the complete controller repository")

    account = pwd.getpwnam(args.user)
    if account.pw_uid == 0:
        raise ValueError("The controller and Paper services must not run as root")
    group = grp.getgrnam(args.group) if args.group else grp.getgrgid(account.pw_gid)
    install_dir = ROOT
    _, config = ensure_config(args, account)
    validate_paths(config, account, group, install_dir)
    role = config["DEPLOY_ROLE"]
    installs_paper = role in {"game-server", "all"}
    installs_controller = role in {"controller", "all"}
    installs_agent = role == "game-server" or (
        role == "all" and config["CONTROL_MODE"] == "remote"
    )
    if args.start_paper and not installs_paper:
        raise ValueError("--start-paper is only valid for DEPLOY_ROLE=game-server or all")

    service_name = config["PAPER_SERVICE"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,126}\.service", service_name):
        raise ValueError("PAPER_SERVICE must be a simple .service unit name")

    values = {
        "SERVICE_USER": account.pw_name,
        "SERVICE_GROUP": group.gr_name,
        "PAPER_DIR": config["PAPER_DIR"],
        "INSTALL_DIR": str(install_dir),
        "PAPER_SERVICE": service_name,
    }
    paper_unit = (
        render_template(ROOT / "deploy/systemd/paper.service.in", values)
        if installs_paper
        else None
    )
    controller_unit = (
        render_template(ROOT / "deploy/systemd/paper-controller.service.in", values)
        if installs_controller
        else None
    )
    agent_unit = (
        render_template(ROOT / "deploy/systemd/minecraft-agent.service.in", values)
        if installs_agent
        else None
    )
    sudoers_rule = (
        render_template(ROOT / "deploy/sudoers/paper-controller.in", values)
        if installs_paper
        else None
    )

    if args.check:
        print(f"Configuration valid for {config['SERVER_NAME']}")
        if installs_paper:
            print(f"Paper directory: {config['PAPER_DIR']}")
        if installs_controller:
            print(
                f"Controller URL: http://{config['CONTROLLER_HOST']}:"
                f"{config['CONTROLLER_PORT']}"
            )
        services = []
        if installs_paper:
            services.append(service_name)
        if installs_agent:
            services.append("minecraft-agent.service")
        if installs_controller:
            services.append("paper-controller.service")
        print(f"Would install: {', '.join(services)}")
        print(f"Services run as {account.pw_name}:{group.gr_name}")
        return

    os.chmod(CONFIG_PATH, 0o660)
    os.chown(CONFIG_PATH, 0, group.gr_gid)

    if installs_paper:
        paper_script = ROOT / "deploy/paper-service.sh"
        paper_script.chmod(0o755)
        os.chown(paper_script, 0, 0)
        atomic_install(UNIT_DIR / service_name, paper_unit, 0o644, 0, 0)
        atomic_install(
            SUDOERS_DIR / "paper-controller",
            sudoers_rule + "\n",
            0o440,
            0,
            0,
            validator=validate_sudoers,
        )
    if installs_agent:
        agent_script = ROOT / "minecraft_agent.py"
        agent_script.chmod(0o755)
        os.chown(agent_script, 0, 0)
        atomic_install(
            UNIT_DIR / "minecraft-agent.service",
            agent_unit,
            0o644,
            0,
            0,
        )
    if installs_controller:
        atomic_install(
            UNIT_DIR / "paper-controller.service",
            controller_unit,
            0o644,
            0,
            0,
        )

    command(["/usr/bin/systemctl", "daemon-reload"])
    services_to_enable = []
    if installs_paper:
        services_to_enable.append(service_name)
    if installs_agent:
        services_to_enable.append("minecraft-agent.service")
    if installs_controller:
        services_to_enable.append("paper-controller.service")
    command(["/usr/bin/systemctl", "enable", *services_to_enable])
    if args.start_paper and installs_paper:
        command(["/usr/bin/systemctl", "start", service_name])
    if installs_agent:
        command(["/usr/bin/systemctl", "restart", "minecraft-agent.service"])
    if installs_controller:
        command(["/usr/bin/systemctl", "restart", "paper-controller.service"])

    print(f"Installed and enabled: {', '.join(services_to_enable)}.")
    if installs_paper and not args.start_paper:
        print(f"Paper remains stopped. Start it with: systemctl start {service_name}")
    if installs_controller:
        print(
            f"Control Room: http://{config['CONTROLLER_HOST']}:"
            f"{config['CONTROLLER_PORT']}"
        )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        print(f"Deployment command failed: {detail}", file=sys.stderr)
        sys.exit(1)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
