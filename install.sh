#!/usr/bin/env bash
# Installs the systemd user unit, the autostart entry and the menu launcher,
# resolving every path from wherever this repository happens to sit. Re-run it
# after editing flm.service, since the installed copy is a copy and not a
# symlink. Re-run it after moving the repository too: the launcher and autostart
# entry carry this path in their Exec line.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT="$CONFIG/systemd/user/flm.service"
WEBUI_UNIT="$CONFIG/systemd/user/open-webui.service"
AUTOSTART="$CONFIG/autostart/flm-tray.desktop"
# The autostart directory is read only at login, so the menu needs its own copy.
LAUNCHER="$DATA/applications/flm-tray.desktop"
# Not `python3`: PyQt6 is a distribution package, and an active conda env or
# venv shadows the interpreter that actually has it.
PYTHON=/usr/bin/python3

die() { printf 'install.sh: %s\n' "$1" >&2; exit 1; }

FLM="$(command -v flm || true)"
[ -n "$FLM" ] || die "flm is not in PATH. Install FastFlowLM first: https://fastflowlm.com"

"$PYTHON" -c 'import PyQt6' 2>/dev/null ||
  die "$PYTHON cannot import PyQt6. Install your distribution's python3-pyqt6 package."

case "$REPO" in
  *\ *) die "this path contains a space, which the .desktop Exec line cannot carry: $REPO" ;;
esac

mkdir -p "$(dirname "$UNIT")" "$(dirname "$AUTOSTART")" "$(dirname "$LAUNCHER")"
sed "s|^ExecStart=/usr/bin/flm |ExecStart=$FLM |" "$REPO/flm.service" > "$UNIT"
# Optional: without docker compose the tray shows Open WebUI as not installed.
ENGINE="$(command -v docker || true)"
"$ENGINE" compose version >/dev/null 2>&1 || ENGINE=""
if [ -n "$ENGINE" ]; then
  sed "s|/usr/bin/docker |$ENGINE |g; s|@COMPOSE@|$REPO/compose.yaml|g" "$REPO/open-webui.service" > "$WEBUI_UNIT"
  # Generated once, so a reinstall keeps it.
  SECRET="$CONFIG/flm-tray/searxng.env"
  mkdir -p "$(dirname "$SECRET")"
  [ -f "$SECRET" ] || echo "SEARXNG_SECRET=$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')" > "$SECRET"
fi
sed "s|@EXEC@|$PYTHON $REPO/flm_tray.py|" "$REPO/flm-tray.desktop.in" > "$AUTOSTART"
# Same rendered entry in both places; launching it twice is harmless, because
# the tray takes a QLockFile and a second instance exits without a window.
cp "$AUTOSTART" "$LAUNCHER"
systemctl --user daemon-reload

cat <<EOF
Installed
  $UNIT
  $AUTOSTART
  $LAUNCHER
  using flm at $FLM

The unit is not enabled and has no [Install] section, so nothing starts at
login. The tray starts the server when you ask it to.

Start the tray now:
  $PYTHON $REPO/flm_tray.py &

Check that memlock is unlimited under systemd:
  flm validate
EOF

if [ -z "$ENGINE" ]; then
  echo "Skipped Open WebUI: docker compose is not available."
  exit 0
fi
# Pulled once here, where progress shows; updating is a deliberate `pull`.
printf '\nInstalled %s\n' "$WEBUI_UNIT"
for IMAGE in $("$ENGINE" compose -f "$REPO/compose.yaml" config --images); do
  "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1 || "$ENGINE" pull "$IMAGE"
done
