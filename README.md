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

The repository includes the Paper service template, the combined Control Room
web UI/API service template, the Paper tmux start/stop helper, and a restricted
sudoers rule. The API and web UI run together in `paper-controller.service`;
there is no separate API process.

The Paper server itself must already be installed with an executable
`start.sh`. By default the deploy script expects `/home/calmc/paper`.

```sh
sudo ./deploy.py --check
sudo ./deploy.py
```

Set the deployment values in `.env` before installing. The deploy script keeps
an existing `.env`; if none exists, it copies `.env.example` and detects a
local controller address. You can provide a different Paper account or server
directory:

```sh
sudo ./deploy.py --user minecraft --group minecraft --paper-dir /home/minecraft/paper
```

The script installs/enables the configured Paper unit and
`paper-controller.service`, installs a sudoers rule that permits only starting
and stopping that Paper unit, and restarts the controller. It does not start
Paper unless requested:

```sh
sudo ./deploy.py --start-paper
```

The controller service account owns the running Paper/tmux session and gets
group write access to `.env`. The installer does not recursively change
project ownership, so keep the application files and their parent directories
unwritable by the service account. Systemd units and the sudoers rule are
installed root-owned. The script does not copy or modify the Minecraft server
files.

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

- `CONTROLLER_BIND`, `CONTROLLER_PORT`, and `CONTROLLER_HOST`
- `SERVER_NAME`, `SERVER_IP`, `SERVER_PORT`, and `SERVER_IMAGE`
- `PAPER_SERVICE`, `PAPER_DIR`, `TMUX_SOCKET`, `TMUX_SESSION`, and `TMUX_TARGET`

The Settings tab updates the server identity and address fields. `SERVER_PORT`
remains configurable through `.env`. The service unit and sudoers policy must
be adapted to the target host; see the handoff notes in [HANDOFF.md](./HANDOFF.md).
