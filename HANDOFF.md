# Minecraft Control Room — Handoff Notes

Updated: 2026-10-29

## Repository and deployment

- Repository: `/opt/server-controller`
- Branch: `main`, tracking `origin/main`
- Remote: `git@github.com:CalMcKin/server-controller.git`
- Runtime settings live in `/opt/server-controller/.env`, which is ignored by
  Git and writable by the controller service group. Do not make the project
  directory writable by the service account.
- `.env.example` is the tracked deployment template.

## Configuration model

`server.py` reads and validates `.env` values. The Settings tab persists
`SERVER_NAME`, `CONTROLLER_HOST`, `SERVER_IP`, and `SERVER_IMAGE` there. The
name/logo update the page; the Minecraft address controls the Java status query.
`SERVER_PORT` stays configurable directly in `.env`, not in the Settings tab.
Controller host controls the accepted browser host.

The following can be changed in `.env` for each deployment:

- `CONTROLLER_BIND` and `CONTROLLER_PORT` (restart the controller after edits)
- `PAPER_SERVICE`, `PAPER_DIR`, `TMUX_SOCKET`, `TMUX_SESSION`, and `TMUX_TARGET`
- The identity, address, and image fields editable in Settings

`SERVER_IMAGE` must be a supported image in the project root (PNG, JPG, GIF,
or WebP). The controller only serves the configured image filename.

## Runtime and operations

The repository contains deployable templates in `deploy/systemd/`, the
Paper/tmux lifecycle helper in `deploy/paper-service.sh`, and the installer in
`deploy.py`. Run `sudo ./deploy.py --check` and then `sudo ./deploy.py` to
install/update services for the configured `DEPLOY_ROLE`. Use `all` for one
host, `game-server` to install Paper and the authenticated API agent, or
`controller` to install only the web UI/API. In two-host mode the controller
uses `CONTROL_MODE=remote` and `REMOTE_API_URL`; both hosts must share a strong
`REMOTE_API_TOKEN`. The game-server role expects the preinstalled Paper
directory from `PAPER_DIR` and never copies or changes its files.

The installed units are `/etc/systemd/system/paper.service` and
`/etc/systemd/system/paper-controller.service`. On this host the controller
runs `/usr/bin/python3 /opt/server-controller/server.py` as user/group
`calmc:calmc`. Other deployments can set a different service user, group, and
Paper path through installer options.
The game-server role also installs `/etc/systemd/system/minecraft-agent.service`
to run `minecraft_agent.py`. Restrict its `AGENT_PORT` to the controller host
using the firewall; use a private VPN or HTTPS reverse proxy because the agent
does not provide TLS itself.

Useful commands:

```sh
sudo systemctl status paper-controller.service
sudo systemctl restart paper-controller.service
sudo journalctl -u paper-controller.service -n 100 --no-pager
sudo -u calmc tmux -L paper attach -t paper
```

`server.py` serves both the web UI and its JSON API from one service.
`minecraft_agent.py` is the authenticated remote API hosted on the Paper
machine; it uses a shared bearer token and executes only configured Paper
service/tmux operations.
The deploy script does not recursively change ownership of project files;
ensure the application directory remains unwritable by the service account.

Detach from tmux without stopping the server with `Ctrl+B`, then `D`. The
controller uses the configured systemd unit for start/stop and configured tmux
socket/session/target for console access.

## Important safety notes

- The controller has **no login**. Anyone who can reach the configured
  controller address can manage the Paper service, view the console, edit
  deployment settings, and submit commands with Minecraft console
  permissions. Do not expose the controller port to the public internet.
- Console commands are passed to tmux as literal text, never through a shell.
- Keep sudoers permissions limited to starting/stopping the configured Paper
  unit; never grant arbitrary root commands.
- The Settings tab can edit `.env`, so preserve its file permissions and do not
  put secrets in it.
