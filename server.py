#!/usr/bin/env python3

import json
import os
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = "192.168.1.151"
PORT = 8765
ROOT = Path(__file__).resolve().parent
SERVER_PROPERTIES = Path("/home/calmc/paper/server.properties")
ALLOWED_HOSTS = {f"{HOST}:{PORT}"}


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


def minecraft_players():
    port = 25565
    try:
        for line in SERVER_PROPERTIES.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == "server-port":
                port = int(value.strip())
                break
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Could not read Minecraft server port: {error}") from error
    if not 1 <= port <= 65535:
        raise ValueError("Configured Minecraft server port is invalid")

    host = "127.0.0.1"
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


class ControllerHandler(BaseHTTPRequestHandler):
    server_version = "PaperController/1.0"

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
        return self.headers.get("Host", "").lower() in ALLOWED_HOSTS

    def valid_origin(self):
        origin = urlparse(self.headers.get("Origin", ""))
        return (
            origin.scheme == "http"
            and origin.netloc.lower() in ALLOWED_HOSTS
            and origin.netloc.lower() == self.headers.get("Host", "").lower()
            and not origin.path
        )

    def do_GET(self):
        if not self.valid_host():
            self.send_json(400, {"error": "Unsupported host"})
            return

        route = urlparse(self.path).path
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.css": ("app.css", "text/css; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/grandell.png": ("grandell.png", "image/png"),
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

        self.send_json(404, {"error": "Not found"})

    def send_resources(self):
        try:
            result = system_resources()
        except (OSError, ValueError, IndexError) as error:
            self.send_json(503, {"error": f"Could not read system resources: {error}"})
            return
        self.send_json(200, result)

    def send_players(self):
        try:
            result = minecraft_players()
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
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", "paper.service"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = result.stdout.strip() or "unknown"
            session = subprocess.run(
                ["/usr/bin/tmux", "-L", "paper", "has-session", "-t", "paper"],
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
            },
        )

    def send_logs(self):
        try:
            session = subprocess.run(
                ["/usr/bin/tmux", "-L", "paper", "has-session", "-t", "paper"],
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
                    "paper",
                    "capture-pane",
                    "-p",
                    "-J",
                    "-S",
                    "-500",
                    "-t",
                    "paper:0.0",
                ],
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.send_json(503, {"error": f"Could not read tmux console: {error}"})
            return

        self.send_json(200, {"available": True, "logs": result.stdout})

    def do_POST(self):
        if not self.valid_host() or not self.valid_origin():
            self.send_json(403, {"error": "Request origin is not allowed"})
            return

        route = urlparse(self.path).path
        if route == "/api/console/command":
            self.send_console_command()
            return

        actions = {
            "/api/start": ["sudo", "-n", "/usr/bin/systemctl", "start", "paper.service"],
            "/api/stop": ["sudo", "-n", "/usr/bin/systemctl", "stop", "paper.service"],
        }
        if route not in actions:
            self.send_json(404, {"error": "Not found"})
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

        try:
            session = subprocess.run(
                ["/usr/bin/tmux", "-L", "paper", "has-session", "-t", "paper"],
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
                    "paper",
                    "send-keys",
                    "-l",
                    "-t",
                    "paper:0.0",
                    "--",
                    command,
                ],
                timeout=5,
            )
            run_command(
                [
                    "/usr/bin/tmux",
                    "-L",
                    "paper",
                    "send-keys",
                    "-t",
                    "paper:0.0",
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
    print(f"Paper Controller listening on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer((HOST, PORT), ControllerHandler).serve_forever()
