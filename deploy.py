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
    if args.controller_host:
        updates["CONTROLLER_HOST"] = args.controller_host
    elif config["CONTROLLER_HOST"] == "127.0.0.1":
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
    for path in (install_dir, paper_dir):
        if not re.fullmatch(r"/[A-Za-z0-9._/-]+", str(path)):
            raise ValueError(f"Deployment path contains unsupported characters: {path}")
    if not paper_dir.is_dir():
        raise ValueError(
            f"Paper server directory does not exist: {paper_dir}. "
            "Install the server before running deployment."
        )
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

    for required in ("tmux", "pgrep", "systemctl"):
        if not shutil.which(required):
            raise RuntimeError(f"Required command not found: {required}")
    if group.gr_name != account.pw_name:
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
    paper_unit = render_template(ROOT / "deploy/systemd/paper.service.in", values)
    controller_unit = render_template(
        ROOT / "deploy/systemd/paper-controller.service.in", values
    )
    sudoers_rule = render_template(ROOT / "deploy/sudoers/paper-controller.in", values)

    if args.check:
        print(f"Configuration valid for {config['SERVER_NAME']}")
        print(f"Paper directory: {config['PAPER_DIR']}")
        print(f"Controller URL: http://{config['CONTROLLER_HOST']}:{config['CONTROLLER_PORT']}")
        print(f"Would install {service_name} and paper-controller.service")
        print(f"Would run both services as {account.pw_name}:{group.gr_name}")
        return

    paper_script = ROOT / "deploy/paper-service.sh"
    paper_script.chmod(0o755)
    os.chown(paper_script, 0, 0)
    os.chmod(CONFIG_PATH, 0o660)
    os.chown(CONFIG_PATH, 0, group.gr_gid)

    atomic_install(
        UNIT_DIR / service_name,
        paper_unit,
        0o644,
        0,
        0,
    )
    atomic_install(
        UNIT_DIR / "paper-controller.service",
        controller_unit,
        0o644,
        0,
        0,
    )
    atomic_install(
        SUDOERS_DIR / "paper-controller",
        sudoers_rule + "\n",
        0o440,
        0,
        0,
        validator=validate_sudoers,
    )

    command(["/usr/bin/systemctl", "daemon-reload"])
    command(["/usr/bin/systemctl", "enable", service_name, "paper-controller.service"])
    if args.start_paper:
        command(["/usr/bin/systemctl", "start", service_name])
    command(["/usr/bin/systemctl", "restart", "paper-controller.service"])

    print(f"Installed and enabled {service_name} and paper-controller.service.")
    if not args.start_paper:
        print(f"Paper remains stopped. Start it with: systemctl start {service_name}")
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
