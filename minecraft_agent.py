#!/usr/bin/env python3

import hmac
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import server


class MinecraftAgentHandler(BaseHTTPRequestHandler):
    server_version = "MinecraftHostAgent/1.0"

    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorize(self):
        try:
            config = server.load_config()
        except (OSError, RuntimeError, ValueError) as error:
            self.send_json(500, {"error": f"Could not load agent settings: {error}"})
            return False

        authorization = self.headers.get("Authorization", "")
        scheme, separator, supplied_token = authorization.partition(" ")
        expected_token = config["REMOTE_API_TOKEN"]
        if (
            not separator
            or scheme.lower() != "bearer"
            or not expected_token
            or not hmac.compare_digest(supplied_token, expected_token)
        ):
            self.send_json(401, {"error": "A valid bearer token is required"})
            return False
        self.config = config
        return True

    def do_GET(self):
        if not self.authorize():
            return
        route = urlparse(self.path).path
        if route == "/health":
            self.send_json(200, {"ok": True})
        elif route == "/api/status":
            self.send_status()
        elif route == "/api/logs":
            self.send_logs()
        elif route == "/api/players":
            self.send_players()
        elif route == "/api/resources":
            self.send_resources()
        else:
            self.send_json(404, {"error": "Not found"})

    def send_status(self):
        try:
            result = subprocess.run(
                ["/usr/bin/systemctl", "is-active", self.config["PAPER_SERVICE"]],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
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
            self.send_json(503, {"error": f"Could not query Paper status: {error}"})
            return
        status = result.stdout.strip() or "unknown"
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
            result = server.run_command(
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
            self.send_json(503, {"error": f"Could not read Paper console: {error}"})
            return
        self.send_json(200, {"available": True, "logs": result.stdout})

    def send_players(self):
        try:
            result = server.minecraft_players(
                self.config["SERVER_IP"],
                int(self.config["SERVER_PORT"]),
            )
        except (OSError, RuntimeError, ValueError) as error:
            result = {
                "available": False,
                "online": 0,
                "maximum": 0,
                "players": [],
                "error": str(error),
            }
        self.send_json(200, result)

    def send_resources(self):
        try:
            result = server.system_resources()
        except (OSError, ValueError, IndexError) as error:
            self.send_json(503, {"error": f"Could not read host resources: {error}"})
            return
        self.send_json(200, result)

    def do_POST(self):
        if not self.authorize():
            return
        route = urlparse(self.path).path
        if route in {"/api/start", "/api/stop"}:
            self.run_service_action(route)
        elif route == "/api/console/command":
            self.send_console_command()
        else:
            self.send_json(404, {"error": "Not found"})

    def run_service_action(self, route):
        action = route.rsplit("/", 1)[-1]
        try:
            server.run_command(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "/usr/bin/systemctl",
                    action,
                    self.config["PAPER_SERVICE"],
                ],
                timeout=100,
            )
        except subprocess.CalledProcessError as error:
            detail = error.stderr.strip() or error.stdout.strip() or str(error)
            self.send_json(500, {"error": f"Could not {action} Paper: {detail}"})
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

        tmux = [
            "/usr/bin/tmux",
            "-L",
            self.config["TMUX_SOCKET"],
        ]
        try:
            session = subprocess.run(
                tmux + ["has-session", "-t", self.config["TMUX_SESSION"]],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if session.returncode:
                self.send_json(409, {"error": "The Paper console is not running"})
                return
            server.run_command(
                tmux + ["send-keys", "-l", "-t", self.config["TMUX_TARGET"], "--", command],
                timeout=5,
            )
            server.run_command(
                tmux + ["send-keys", "-t", self.config["TMUX_TARGET"], "Enter"],
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            self.send_json(503, {"error": f"Could not send command to Paper: {error}"})
            return
        self.send_json(200, {"ok": True})

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}", flush=True)


if __name__ == "__main__":
    config = server.STARTUP_CONFIG
    address = (config["AGENT_BIND"], int(config["AGENT_PORT"]))
    print(
        f"Minecraft host API listening on {address[0]}:{address[1]}",
        flush=True,
    )
    ThreadingHTTPServer(address, MinecraftAgentHandler).serve_forever()
