# Grandell Control Room

Single pane of glass for Minecraft server hosting. A small, dependency-free web
interface for the Paper Minecraft system service.

## Open the interface

On the local network, visit <http://192.168.1.151:8765>. The controller binds
to that address and accepts requests for that host only.

The interface has no login. Anyone who can access it on the local network can
view the console, manage the Paper service, and send commands with the Paper
console's permissions. Do not forward port 8765 to the public internet.

## Features

- Start or gracefully stop `paper.service`.
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
  sudo -u calmc tmux -L paper attach -t paper
  ```

Detach from tmux without stopping the server using `Ctrl+B`, then `D`.

## Service management

The controller starts at boot as `paper-controller.service`:

```sh
sudo systemctl status paper-controller.service
sudo systemctl restart paper-controller.service
```

The controller runs as `calmc`. Its sudoers rule permits only starting and
stopping `paper.service`; it cannot run arbitrary commands as root. The web
controller listens on `192.168.1.151:8765`.

The Online tab checks the Java server status locally and does not enable
Minecraft's separate UDP query service or send commands into the tmux console.
Paper's status response includes up to 12 player names.
