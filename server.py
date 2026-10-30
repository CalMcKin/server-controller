#!/usr/bin/env python3

import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
CONFIG_KEYS = (
    "CONTROLLER_BIND",
    "CONTROLLER_PORT",
    "CONTROLLER_HOST",
    "DEPLOY_ROLE",
    "CONTROL_MODE",
    "REMOTE_API_URL",
    "REMOTE_API_TOKEN",
    "AGENT_BIND",
    "AGENT_PORT",
    "SERVER_NAME",
    "SERVER_IP",
    "SERVER_PORT",
    "SERVER_IMAGE",
    "PAPER_SERVICE",
    "PAPER_DIR",
    "TMUX_SOCKET",
    "TMUX_SESSION",
    "TMUX_TARGET",
)
DEFAULT_CONFIG = {
    "CONTROLLER_BIND": "0.0.0.0",
    "CONTROLLER_PORT": "8765",
    "CONTROLLER_HOST": "127.0.0.1",
    "DEPLOY_ROLE": "all",
    "CONTROL_MODE": "local",
    "REMOTE_API_URL": "",
    "REMOTE_API_TOKEN": "",
    "AGENT_BIND": "127.0.0.1",
    "AGENT_PORT": "8766",
    "SERVER_NAME": "Minecraft",
    "SERVER_IP": "127.0.0.1",
    "SERVER_PORT": "25565",
    "SERVER_IMAGE": "grandell.png",
    "PAPER_SERVICE": "paper.service",
    "PAPER_DIR": "/home/calmc/paper",
    "TMUX_SOCKET": "paper",
    "TMUX_SESSION": "paper",
    "TMUX_TARGET": "paper:0.0",
}
ENV_COMMENTS = {
    "CONTROLLER_BIND": "Address the web UI/API listens on; use 0.0.0.0 to accept LAN traffic.",
    "CONTROLLER_PORT": "TCP port for the web UI/API.",
    "CONTROLLER_HOST": "IP address or hostname users enter in their browser.",
    "DEPLOY_ROLE": "Install role: controller, game-server, or all on one machine.",
    "CONTROL_MODE": "local runs Paper commands here; remote sends them to the Minecraft host API.",
    "REMOTE_API_URL": "Base URL of the Minecraft host API, for example https://mc-host:8766.",
    "REMOTE_API_TOKEN": "Shared random bearer token; set the same value on controller and game host.",
    "AGENT_BIND": "Address the Minecraft host API listens on; firewall this port to the controller host.",
    "AGENT_PORT": "TCP port for the Minecraft host API.",
    "SERVER_NAME": "Display name shown in the web UI.",
    "SERVER_IP": "Minecraft Java status address. Set to 127.0.0.1 in the game-server .env.",
    "SERVER_PORT": "Minecraft Java status port; not editable in the Settings tab.",
    "SERVER_IMAGE": "Image filename in the repository root, shown as the server logo.",
    "PAPER_SERVICE": "systemd unit name for the Paper server.",
    "PAPER_DIR": "Existing Paper server directory containing an executable start.sh.",
    "TMUX_SOCKET": "tmux socket name used by the Paper server and agent.",
    "TMUX_SESSION": "tmux session name used by the Paper server and agent.",
    "TMUX_TARGET": "tmux pane target for console logs and commands.",
}
HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?\Z"
)
TMUX_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
IMAGE_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
CONFIG_LOCK = threading.RLock()


def parse_env_file():
    values = {}
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    except OSError as error:
        raise RuntimeError(f"Could not read {ENV_PATH}: {error}") from error

    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or key not in CONFIG_KEYS:
            raise ValueError(f"Invalid .env entry on line {line_number}")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid quoted value on .env line {line_number}") from error
            if not isinstance(value, str):
                raise ValueError(f"Invalid value on .env line {line_number}")
        elif value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise ValueError(f"Invalid quoted value on .env line {line_number}")
            value = value[1:-1]
        values[key] = value
    return values


def validate_host(value, field_name):
    if not isinstance(value, str) or not HOST_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be an IPv4 address or hostname")
    if value.replace(".", "").isdigit():
        try:
            ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError as error:
            raise ValueError(f"{field_name} must be a valid IPv4 address") from error
    return value.lower().rstrip(".")


def validate_port(value, field_name):
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a number from 1 to 65535") from error
    if not 1 <= port <= 65535:
        raise ValueError(f"{field_name} must be a number from 1 to 65535")
    return str(port)


def validate_config(values):
    config = {**DEFAULT_CONFIG, **values}
    config["CONTROLLER_BIND"] = validate_host(config["CONTROLLER_BIND"], "CONTROLLER_BIND")
    config["CONTROLLER_PORT"] = validate_port(config["CONTROLLER_PORT"], "CONTROLLER_PORT")
    config["CONTROLLER_HOST"] = validate_host(config["CONTROLLER_HOST"], "CONTROLLER_HOST")
    config["AGENT_BIND"] = validate_host(config["AGENT_BIND"], "AGENT_BIND")
    config["AGENT_PORT"] = validate_port(config["AGENT_PORT"], "AGENT_PORT")
    config["SERVER_IP"] = validate_host(config["SERVER_IP"], "SERVER_IP")
    config["SERVER_PORT"] = validate_port(config["SERVER_PORT"], "SERVER_PORT")
    if config["DEPLOY_ROLE"] not in {"controller", "game-server", "all"}:
        raise ValueError("DEPLOY_ROLE must be controller, game-server, or all")
    if config["CONTROL_MODE"] not in {"local", "remote"}:
        raise ValueError("CONTROL_MODE must be local or remote")
    if config["CONTROL_MODE"] == "remote":
        remote_url = config["REMOTE_API_URL"]
        parsed_remote_url = urlparse(remote_url)
        if (
            parsed_remote_url.scheme not in {"http", "https"}
            or not parsed_remote_url.hostname
            or parsed_remote_url.username
            or parsed_remote_url.password
            or parsed_remote_url.path not in {"", "/"}
            or parsed_remote_url.query
            or parsed_remote_url.fragment
        ):
            raise ValueError("REMOTE_API_URL must be an HTTP(S) base URL without credentials or a path")
        if len(config["REMOTE_API_TOKEN"]) < 32:
            raise ValueError("REMOTE_API_TOKEN must contain at least 32 characters in remote mode")
    elif config["REMOTE_API_URL"] and len(config["REMOTE_API_TOKEN"]) < 32:
        raise ValueError("REMOTE_API_TOKEN must contain at least 32 characters when configured")

    name = config["SERVER_NAME"].strip()
    if not name or len(name) > 64 or any(ord(character) < 32 for character in name):
        raise ValueError("SERVER_NAME must contain 1 to 64 printable characters")
    config["SERVER_NAME"] = name

    image = config["SERVER_IMAGE"]
    if not isinstance(image, str) or not image:
        raise ValueError("SERVER_IMAGE must name an existing image file in the project directory")
    image_path = (ROOT / image).resolve()
    if (
        Path(image).name != image
        or image_path.parent != ROOT.resolve()
        or image_path.suffix.lower() not in IMAGE_TYPES
        or not image_path.is_file()
    ):
        raise ValueError("SERVER_IMAGE must name an existing image file in the project directory")

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}", config["PAPER_SERVICE"]):
        raise ValueError("PAPER_SERVICE is not a valid systemd unit name")
    if not Path(config["PAPER_DIR"]).is_absolute():
        raise ValueError("PAPER_DIR must be an absolute path")
    if not TMUX_NAME_PATTERN.fullmatch(config["TMUX_SOCKET"]):
        raise ValueError("TMUX_SOCKET contains unsupported characters")
    if not TMUX_NAME_PATTERN.fullmatch(config["TMUX_SESSION"]):
        raise ValueError("TMUX_SESSION contains unsupported characters")
    if not TMUX_NAME_PATTERN.fullmatch(config["TMUX_TARGET"]):
        raise ValueError("TMUX_TARGET contains unsupported characters")
    return config


def load_config():
    with CONFIG_LOCK:
        return validate_config(parse_env_file())


def write_config(config):
    content = "".join(
        f"# {ENV_COMMENTS[key]}\n{key}={json.dumps(config[key])}\n"
        for key in CONFIG_KEYS
    )
    with CONFIG_LOCK:
        try:
            previous = ENV_PATH.read_bytes()
        except FileNotFoundError:
            previous = None

        try:
            with ENV_PATH.open("wb") as env_file:
                env_file.write(content.encode("utf-8"))
                env_file.flush()
                os.fsync(env_file.fileno())
        except OSError as error:
            if previous is not None:
                try:
                    with ENV_PATH.open("wb") as env_file:
                        env_file.write(previous)
                        env_file.flush()
                        os.fsync(env_file.fileno())
                except OSError as restore_error:
                    raise RuntimeError(
                        f"Could not save .env ({error}) or restore its previous contents "
                        f"({restore_error})"
                    ) from restore_error
            raise RuntimeError(f"Could not save {ENV_PATH}: {error}") from error


def configured_hosts(config):
    return {
        f"{host}:{config['CONTROLLER_PORT']}"
        for host in {
            config["CONTROLLER_HOST"],
            config["CONTROLLER_BIND"],
            "localhost",
            "127.0.0.1",
        }
    }


STARTUP_CONFIG = load_config()


def run_command(args, timeout=10):
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def minecraft_varint(value):
    encoded = bytearray()
    while value & ~0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def read_varint(connection):
    value = 0
    for index in range(5):
        byte = connection.recv(1)
        if not byte:
            raise ConnectionError("Minecraft server closed the status connection")
        current = byte[0]
        value |= (current & 0x7F) << (7 * index)
        if current & 0x80 == 0:
            return value
    raise ValueError("Invalid Minecraft status packet length")


def read_exact(connection, length):
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise ConnectionError("Minecraft server closed the status connection")
        result.extend(chunk)
    return bytes(result)


def minecraft_players(host, port):
    encoded_host = host.encode("utf-8")
    handshake = (
        b"\x00"
        + minecraft_varint(765)
        + minecraft_varint(len(encoded_host))
        + encoded_host
        + port.to_bytes(2, "big")
        + b"\x01"
    )
    request = minecraft_varint(len(handshake)) + handshake + b"\x01\x00"

    with socket.create_connection((host, port), timeout=3) as connection:
        connection.settimeout(3)
        connection.sendall(request)
        packet_length = read_varint(connection)
        if not 1 <= packet_length <= 1_048_576:
            raise ValueError("Minecraft server returned an invalid status packet")
        packet = read_exact(connection, packet_length)

    packet_id = packet[0]
    if packet_id != 0:
        raise ValueError("Minecraft server returned an unexpected status packet")
    response = memoryview(packet)[1:]
    string_length = 0
    shift = 0
    for index in range(5):
        if index >= len(response):
            raise ValueError("Minecraft server returned an incomplete status response")
        byte = response[index]
        string_length |= (byte & 0x7F) << shift
        shift += 7
        if byte & 0x80 == 0:
            response = response[index + 1 :]
            break
    else:
        raise ValueError("Minecraft server returned an invalid status response")
    if string_length != len(response):
        raise ValueError("Minecraft server returned an invalid status response length")

    status = json.loads(response.tobytes().decode("utf-8"))
    if not isinstance(status, dict):
        raise ValueError("Minecraft server returned invalid status data")
    players = status.get("players", {})
    if not isinstance(players, dict):
        raise ValueError("Minecraft server returned invalid player status")
    online = players.get("online", 0)
    maximum = players.get("max", 0)
    sample = players.get("sample", [])
    if not isinstance(online, int) or not isinstance(maximum, int):
        raise ValueError("Minecraft server returned invalid player counts")
    if not isinstance(sample, list):
        sample = []
    names = [
        player["name"]
        for player in sample
        if isinstance(player, dict) and isinstance(player.get("name"), str)
    ]
    return {"available": True, "online": online, "maximum": maximum, "players": names}


def cpu_counters():
    line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
    fields = line.split()
    if not fields or fields[0] != "cpu" or len(fields) < 6:
        raise ValueError("Could not read CPU counters from /proc/stat")
    values = [int(value) for value in fields[1:9]]
    total = sum(values)
    idle = values[3] + values[4]
    if total <= 0:
        raise ValueError("CPU counters from /proc/stat were empty")
    return total, idle


def system_resources():
    previous_total, previous_idle = cpu_counters()
    time.sleep(0.1)
    current_total, current_idle = cpu_counters()
    total_delta = current_total - previous_total
    idle_delta = current_idle - previous_idle
    if total_delta <= 0:
        raise ValueError("CPU counters did not advance while sampling")
    cpu_percent = round(100 * (total_delta - idle_delta) / total_delta, 1)

    memory = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"MemTotal", "MemAvailable"}:
            memory[key] = int(value.strip().split()[0]) * 1024
    total_bytes = memory.get("MemTotal")
    available_bytes = memory.get("MemAvailable")
    if total_bytes is None or available_bytes is None or total_bytes <= 0:
        raise ValueError("Could not read total and available memory from /proc/meminfo")
    available_bytes = min(total_bytes, max(0, available_bytes))

    return {
        "cpu_percent": max(0, min(100, cpu_percent)),
        "cpu_count": os.cpu_count() or 1,
        "memory_total_bytes": total_bytes,
        "memory_used_bytes": total_bytes - available_bytes,
        "memory_available_bytes": available_bytes,
    }


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def remote_api_request(config, path, method="GET", payload=None, timeout=10):
    url = f"{config['REMOTE_API_URL'].rstrip('/')}{path}"
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {config['REMOTE_API_TOKEN']}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            response_body = response.read(1_048_577)
            status = response.status
    except urllib.error.HTTPError as error:
        response_body = error.read(1_048_577)
        status = error.code
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise RuntimeError(f"Could not reach Minecraft host API: {reason}") from error

    if len(response_body) > 1_048_576:
        raise RuntimeError("Minecraft host API response exceeded 1 MiB")
    try:
        data = json.loads(response_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("Minecraft host API returned invalid JSON") from error
    if not isinstance(data, dict):
        raise RuntimeError("Minecraft host API returned an invalid response")
    return status, data


class ControllerHandler(BaseHTTPRequestHandler):
    server_version = "MinecraftController/1.0"

    def send_body(self, status, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        self.send_body(status, "application/json; charset=utf-8", json.dumps(data))

    def valid_host(self):
        return self.headers.get("Host", "").lower() in configured_hosts(self.config)

    def valid_origin(self):
        origin = urlparse(self.headers.get("Origin", ""))
        return (
            origin.scheme == "http"
            and origin.netloc.lower() in configured_hosts(self.config)
            and origin.netloc.lower() == self.headers.get("Host", "").lower()
            and not origin.path
        )

    def do_GET(self):
        try:
            self.config = load_config()
        except (OSError, RuntimeError, ValueError) as error:
            self.send_json(500, {"error": f"Could not load controller settings: {error}"})
            return
        if not self.valid_host():
            self.send_json(400, {"error": "Unsupported host"})
            return

        route = urlparse(self.path).path
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/server-image": (
                self.config["SERVER_IMAGE"],
                IMAGE_TYPES[Path(self.config["SERVER_IMAGE"]).suffix.lower()],
            ),
        }

        if route in files:
            filename, content_type = files[route]
            try:
                body = (ROOT / filename).read_bytes()
            except OSError as error:
                self.send_json(500, {"error": f"Could not read {filename}: {error}"})
                return
            self.send_body(200, content_type, body)
            return

        if route == "/api/status":
            self.send_status()
            return

        if route == "/api/logs":
            self.send_logs()
            return

        if route == "/api/players":
            self.send_players()
            return

        if route == "/api/resources":
            self.send_resources()
            return

        if route == "/api/settings":
            self.send_settings()
            return

        self.send_json(404, {"error": "Not found"})

    def send_settings(self):
        self.send_json(
            200,
            {
                "server_name": self.config["SERVER_NAME"],
                "controller_host": self.config["CONTROLLER_HOST"],
                "server_ip": self.config["SERVER_IP"],
                "server_image": self.config["SERVER_IMAGE"],
                "tmux_socket": self.config["TMUX_SOCKET"],
                "tmux_session": self.config["TMUX_SESSION"],
            },
        )

    def send_resources(self):
        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote("/api/resources")
            return
        try:
            result = system_resources()
        except (OSError, ValueError, IndexError) as error:
            self.send_json(503, {"error": f"Could not read system resources: {error}"})
            return
        self.send_json(200, result)

    def send_players(self):
        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote("/api/players")
            return
        try:
            result = minecraft_players(self.config["SERVER_IP"], int(self.config["SERVER_PORT"]))
        except ConnectionRefusedError:
            result = {
                "available": False,
                "online": 0,
                "maximum": 0,
                "players": [],
                "error": "The Paper server is offline or not accepting connections.",
            }
        except (OSError, RuntimeError, ValueError) as error:
            result = {
                "available": False,
                "online": 0,
                "maximum": 0,
                "players": [],
                "error": str(error),
            }
        self.send_json(200, result)

    def send_status(self):
        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote("/api/status")
            return
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", self.config["PAPER_SERVICE"]],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = result.stdout.strip() or "unknown"
            session = subprocess.run(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "has-session",
                    "-t",
                    self.config["TMUX_SESSION"],
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            self.send_json(503, {"error": f"Could not query server status: {error}"})
            return

        self.send_json(
            200,
            {
                "status": status,
                "running": status == "active",
                "console_available": session.returncode == 0,
                "server_name": self.config["SERVER_NAME"],
            },
        )

    def send_logs(self):
        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote("/api/logs")
            return
        try:
            session = subprocess.run(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "has-session",
                    "-t",
                    self.config["TMUX_SESSION"],
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if session.returncode != 0:
                self.send_json(200, {"available": False, "logs": ""})
                return

            result = run_command(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "capture-pane",
                    "-p",
                    "-J",
                    "-S",
                    "-500",
                    "-t",
                    self.config["TMUX_TARGET"],
                ],
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.send_json(503, {"error": f"Could not read tmux console: {error}"})
            return

        self.send_json(200, {"available": True, "logs": result.stdout})

    def proxy_remote(self, path, method="GET", payload=None, timeout=10):
        try:
            status, data = remote_api_request(
                self.config,
                path,
                method=method,
                payload=payload,
                timeout=timeout,
            )
        except RuntimeError as error:
            self.send_json(503, {"error": str(error)})
            return
        self.send_json(status, data)

    def do_POST(self):
        try:
            self.config = load_config()
        except (OSError, RuntimeError, ValueError) as error:
            self.send_json(500, {"error": f"Could not load controller settings: {error}"})
            return
        if not self.valid_host() or not self.valid_origin():
            self.send_json(403, {"error": "Request origin is not allowed"})
            return

        route = urlparse(self.path).path
        if route == "/api/settings":
            self.save_settings()
            return
        if route == "/api/console/command":
            self.send_console_command()
            return

        actions = {
            "/api/start": [
                "sudo",
                "-n",
                "/usr/bin/systemctl",
                "start",
                self.config["PAPER_SERVICE"],
            ],
            "/api/stop": [
                "sudo",
                "-n",
                "/usr/bin/systemctl",
                "stop",
                self.config["PAPER_SERVICE"],
            ],
        }
        if route not in actions:
            self.send_json(404, {"error": "Not found"})
            return

        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote(route, method="POST", payload={}, timeout=110)
            return

        try:
            run_command(actions[route], timeout=100)
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or str(error)
            self.send_json(500, {"error": f"Could not {route.rsplit('/', 1)[-1]} Paper: {detail}"})
            return
        except (OSError, subprocess.TimeoutExpired) as error:
            self.send_json(500, {"error": f"Could not run Paper service action: {error}"})
            return

        self.send_json(200, {"ok": True})

    def save_settings(self):
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_json(400, {"error": "A valid Content-Length is required"})
            return
        if content_length < 1:
            self.send_json(400, {"error": "Settings are required"})
            return
        if content_length > 4096:
            self.send_json(413, {"error": "Settings request is too large"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Settings request must be valid JSON"})
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "Settings must be an object"})
            return

        try:
            updated = {
                **self.config,
                "SERVER_NAME": payload["server_name"],
                "CONTROLLER_HOST": payload["controller_host"],
                "SERVER_IP": payload["server_ip"],
                "SERVER_IMAGE": payload["server_image"],
            }
            if any(
                not isinstance(updated[key], str)
                for key in (
                    "SERVER_NAME",
                    "CONTROLLER_HOST",
                    "SERVER_IP",
                    "SERVER_IMAGE",
                )
            ):
                raise ValueError("All settings must be text values")
            updated["SERVER_NAME"] = updated["SERVER_NAME"].strip()
            updated["CONTROLLER_HOST"] = validate_host(
                updated["CONTROLLER_HOST"].strip(), "Controller address"
            )
            updated["SERVER_IP"] = validate_host(updated["SERVER_IP"].strip(), "Minecraft address")
            updated = validate_config(updated)
            write_config(updated)
        except KeyError:
            self.send_json(400, {"error": "All settings fields are required"})
            return
        except (OSError, RuntimeError, ValueError) as error:
            self.send_json(400, {"error": str(error)})
            return

        self.config = updated
        self.send_json(
            200,
            {
                "ok": True,
                "server_name": updated["SERVER_NAME"],
                "controller_host": updated["CONTROLLER_HOST"],
                "server_ip": updated["SERVER_IP"],
                "server_image": updated["SERVER_IMAGE"],
                "tmux_socket": updated["TMUX_SOCKET"],
                "tmux_session": updated["TMUX_SESSION"],
            },
        )

    def send_console_command(self):
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_json(400, {"error": "A valid Content-Length is required"})
            return
        if content_length < 1:
            self.send_json(400, {"error": "A command is required"})
            return
        if content_length > 4096:
            self.send_json(413, {"error": "Command request is too large"})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "Command request must be valid JSON"})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
            self.send_json(400, {"error": "Command must be a string"})
            return

        command = payload["command"].strip()
        if command.startswith("/"):
            command = command[1:].lstrip()
        if not command:
            self.send_json(400, {"error": "Enter a command to send"})
            return
        if len(command) > 1024:
            self.send_json(400, {"error": "Commands must be 1024 characters or fewer"})
            return
        if any(ord(character) < 32 or ord(character) == 127 for character in command):
            self.send_json(400, {"error": "Commands cannot contain control characters"})
            return

        if self.config["CONTROL_MODE"] == "remote":
            self.proxy_remote(
                "/api/console/command",
                method="POST",
                payload={"command": command},
                timeout=10,
            )
            return

        try:
            session = subprocess.run(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "has-session",
                    "-t",
                    self.config["TMUX_SESSION"],
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if session.returncode != 0:
                self.send_json(409, {"error": "The Paper console is not running"})
                return

            run_command(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "send-keys",
                    "-l",
                    "-t",
                    self.config["TMUX_TARGET"],
                    "--",
                    command,
                ],
                timeout=5,
            )
            run_command(
                [
                    "/usr/bin/tmux",
                    "-L",
                    self.config["TMUX_SOCKET"],
                    "send-keys",
                    "-t",
                    self.config["TMUX_TARGET"],
                    "Enter",
                ],
                timeout=5,
            )
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or str(error)
            self.send_json(503, {"error": f"Could not send command to Paper: {detail}"})
            return
        except (OSError, subprocess.TimeoutExpired) as error:
            self.send_json(503, {"error": f"Could not send command to Paper: {error}"})
            return

        self.send_json(200, {"ok": True})

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}", flush=True)


if __name__ == "__main__":
    bind_address = (STARTUP_CONFIG["CONTROLLER_BIND"], int(STARTUP_CONFIG["CONTROLLER_PORT"]))
    print(
        f"Minecraft Controller listening on "
        f"http://{STARTUP_CONFIG['CONTROLLER_HOST']}:{STARTUP_CONFIG['CONTROLLER_PORT']} "
        f"(bound to {bind_address[0]}:{bind_address[1]})",
        flush=True,
    )
    ThreadingHTTPServer(bind_address, ControllerHandler).serve_forever()
