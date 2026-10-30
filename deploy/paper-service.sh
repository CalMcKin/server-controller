#!/bin/sh

set -eu

TMUX_BIN=/usr/bin/tmux

case "${1:-}" in
  start)
    "$TMUX_BIN" -L "$TMUX_SOCKET" new-session -d \
      -s "$TMUX_SESSION" \
      -c "$PAPER_DIR" \
      "$PAPER_DIR/start.sh"
    while "$TMUX_BIN" -L "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null; do
      sleep 1
    done
    exit 1
    ;;
  stop)
    pane_pid=$("$TMUX_BIN" -L "$TMUX_SOCKET" display-message \
      -p -t "$TMUX_SESSION" "#{pane_pid}") || exit 0
    "$TMUX_BIN" -L "$TMUX_SOCKET" send-keys -t "$TMUX_TARGET" C-c
    while /usr/bin/pgrep -P "$pane_pid" -x java >/dev/null; do
      sleep 1
    done
    "$TMUX_BIN" -L "$TMUX_SOCKET" kill-session -t "$TMUX_SESSION"
    ;;
  *)
    printf 'Usage: %s {start|stop}\n' "$0" >&2
    exit 2
    ;;
esac
