# Minecraft Control Room

Single pane of glass for Minecraft server hosting. A small, dependency-free web
interface for the Paper Minecraft system service.

## Configure a deployment

Copy `.env.example` to `.env` and customize the values for this host:

```sh
cp .env.example .env
```

Set `CONTROLLER_HOST` to the IP or hostname used to open the controller,
`SERVER_NAME` to the server's display name, and `SERVER_IMAGE` to an image
filename in this project folder. Set `SERVER_IP` and `SERVER_PORT` in `.env`
to the address the controller can use to query the Minecraft Java status
endpoint. The listener binds to `CONTROLLER_BIND:CONTROLLER_PORT`; by default
it listens on all IPv4 interfaces and port 8765.

The Settings tab can update the server name, controller address, Minecraft
address, and image filename. Set `SERVER_PORT` in `.env`; it is not editable
in the Settings tab. Changes are saved to the local `.env` file and apply
immediately. Upload an image into the project directory before selecting it.
Bind address and controller port are changed directly in `.env` and require
restarting the controller.

`.env` is intentionally ignored by Git so each installation keeps its own
settings. `.env.example` is the tracked template. The service account needs
write access to `.env` for the Settings tab to save changes; for example, set
the file's group to the controller service group and grant group read/write.
Do not make the whole project directory writable by that account.

## Open the interface

Visit `http://<CONTROLLER_HOST>:<CONTROLLER_PORT>` from the local network.

The interface has no login. Anyone who can access it can view the console,
manage the Paper service, edit settings, and send commands with the Paper
console's permissions. Do not forward the controller port to the public
internet.

## Features

- Start or gracefully stop the configured Paper systemd unit.
- View service and tmux-session status.
- See online player names and counts in the Online tab, refreshed from Paper's
  Minecraft server status response.
- Monitor live CPU and RAM usage for the host in the Resources tab.
- Follow the latest 500 lines from the `paper` tmux console; output refreshes
  automatically.
- Send Paper console commands from the Live console tab. Commands run with the
  Paper server's permissions, not as system shell commands.
- Attach directly to tmux for a full interactive console:

  ```sh
  tmux -L paper attach -t paper
  ```

Run the command as the account that owns the tmux session. Detach from tmux
without stopping the server using `Ctrl+B`, then `D`.

## Install system services

The repository includes the Paper service template, the Control Room web
UI/API service template, the authenticated game-host API, the Paper tmux
start/stop helper, and a restricted sudoers rule. In a single-host deployment,
the web UI and its API run together in `paper-controller.service`. In a
two-host deployment, `minecraft-agent.service` runs the authenticated API on
the game host.

The Paper server itself must already be installed with an executable
`start.sh`. By default the deploy script expects `/home/calmc/paper`.

```sh
sudo ./deploy.py --check
sudo ./deploy.py
```

Set the deployment values in `.env` before installing. The deploy script keeps
an existing `.env`; if none exists, it copies `.env.example` and detects a
local controller address. For a single-machine installation, use
`DEPLOY_ROLE=all` and `CONTROL_MODE=local`. You can provide a different Paper
account or server directory:

```sh
sudo ./deploy.py --user minecraft --group minecraft --paper-dir /home/minecraft/paper
```

The script installs/enables the services for the selected deployment role,
installs a sudoers rule on the game host that permits only starting and
stopping the Paper unit, and restarts the applicable web/API service. It does
not start Paper unless requested:

```sh
sudo ./deploy.py --start-paper
```

On the game host, the Paper service account owns the running Paper/tmux
session. The service account gets group write access to `.env`. The installer
does not recursively change project ownership, so keep application files and
their parent directories unwritable by the service account. Systemd units and
the sudoers rule are installed root-owned. The script does not copy or modify
the Minecraft server files.

## Run the web UI and Minecraft on separate machines

The web UI can run on a control machine while a small authenticated API agent
runs beside Paper on the game machine. The agent handles service status,
console output and commands, player status, and resource readings. The
controller forwards these operations to it.

1. On the game machine, install this repository and configure `.env`:

   ```dotenv
   DEPLOY_ROLE="game-server"
   CONTROL_MODE="local"
   AGENT_BIND="0.0.0.0"
   AGENT_PORT="8766"
   REMOTE_API_TOKEN="paste-the-generated-shared-token-here"
   PAPER_DIR="/home/calmc/paper"
   SERVER_IP="127.0.0.1"
   ```

   Generate one strong token and use the exact same value on both machines:

   ```sh
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

   Then install the Paper service and authenticated agent:

   ```sh
   sudo ./deploy.py --check
   sudo ./deploy.py
   ```

2. On the control machine, install the repository and configure `.env`:

   ```dotenv
   DEPLOY_ROLE="controller"
   CONTROL_MODE="remote"
   REMOTE_API_URL="http://GAME_HOST_PRIVATE_IP:8766"
   REMOTE_API_TOKEN="the-same-generated-shared-token"
   SERVER_NAME="Your server name"
   SERVER_IP="GAME_HOST_PUBLIC_OR_DNS_NAME"
   ```

   Set `CONTROLLER_HOST` to the address used to open the UI. Install only the
   web UI/API:

   ```sh
   sudo ./deploy.py --check
   sudo ./deploy.py
   ```

3. Restrict inbound TCP port `8766` on the game machine to the control
   machine. The agent uses bearer-token authentication but plain HTTP by
   default; connect over a trusted private network/VPN or put it behind a
   correctly configured HTTPS reverse proxy. Never expose the agent API
   directly to the public internet.

The `REMOTE_API_TOKEN` must be at least 32 characters. Keep it secret; it
grants Paper service control and console-command access. The game machine's
`SERVER_IP` is the address the agent uses for its local Minecraft status
query (normally `127.0.0.1`). The control machine's `SERVER_IP` is the
join/display address stored with that UI installation. Resource readings in
remote mode come from the game machine.

When changing deployment roles, update `DEPLOY_ROLE` and run the deploy script
on the intended host. The game-server installation does not install the web
UI service, and the controller installation does not install or start Paper.

## Service management

The controller can start at boot as `paper-controller.service`:

```sh
sudo systemctl status paper-controller.service
sudo systemctl restart paper-controller.service
```

The controller runs as the configured service account. Its sudoers rule
permits only starting and stopping the configured Paper unit; it does not
permit arbitrary commands as root. Configure the unit name, `PAPER_DIR`,
`CONTROLLER_BIND`, and `CONTROLLER_PORT` in `.env` for each host. The deploy
script applies the service-account access needed for browser-based settings.

The Online tab checks the configured Minecraft address and port using the Java
server status protocol. It does not enable Minecraft's separate UDP query
service or send commands into the tmux console. Paper's status response
includes up to 12 player names.

## Deployment-specific values

`.env` supports:

- `DEPLOY_ROLE` (`controller`, `game-server`, or `all`) and `CONTROL_MODE`
- `CONTROLLER_BIND`, `CONTROLLER_PORT`, and `CONTROLLER_HOST`
- `REMOTE_API_URL`, `REMOTE_API_TOKEN`, `AGENT_BIND`, and `AGENT_PORT`
- `SERVER_NAME`, `SERVER_IP`, `SERVER_PORT`, and `SERVER_IMAGE`
- `PAPER_SERVICE`, `PAPER_DIR`, `TMUX_SOCKET`, `TMUX_SESSION`, and `TMUX_TARGET`

The Settings tab updates the server identity and address fields. `SERVER_PORT`
remains configurable through `.env`. The service unit and sudoers policy must
be adapted to the target host; see the handoff notes in [HANDOFF.md](./HANDOFF.md).
