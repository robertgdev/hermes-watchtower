#!/usr/bin/env bash
# Install Hermes Watchtower for a Hermes user: copy the app into the skills
# directory (so the agent can load the skill), write a desktop menu entry, and
# optionally install a systemd user service.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKILL_DIR="$HERMES_HOME/skills/hermes-watchtower"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$DESKTOP_DIR/hermes-watchtower.desktop"
PORT="${WATCHTOWER_PORT:-8790}"
DO_SKILL=1
DO_DESKTOP=1
DO_SYSTEMD=0
FORCE=0

usage() {
  cat <<EOF
Install Hermes Watchtower (app + Hermes skill + desktop launcher).

Usage: ./install.sh [options]

  --skill-dir DIR   where to copy the app (default: $SKILL_DIR)
  --port N          default port for the desktop launcher (default: $PORT)
  --no-skill        don't copy the app into the skills directory (run in place)
  --no-desktop      don't write a desktop menu entry
  --systemd         also install and enable a systemd user service
  --force           overwrite an existing copy at --skill-dir
  -h, --help        this help

After installing, restart your Hermes session so the skill is indexed, then
click "Hermes Watchtower" in your app menu or run: $SKILL_DIR/open.sh
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --skill-dir) SKILL_DIR="${2:?missing DIR}"; shift 2 ;;
    --port) PORT="${2:?missing N}"; shift 2 ;;
    --no-skill) DO_SKILL=0; shift ;;
    --no-desktop) DO_DESKTOP=0; shift ;;
    --systemd) DO_SYSTEMD=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# 1) the app itself (never clobber the user's config.json)
if [ "$DO_SKILL" = 1 ] && [ "$HERE" != "$SKILL_DIR" ]; then
  if [ -e "$SKILL_DIR" ] && [ "$FORCE" != 1 ]; then
    echo "refusing to overwrite $SKILL_DIR — rerun with --force to upgrade" >&2
    exit 1
  fi
  mkdir -p "$SKILL_DIR"
  for f in server.py index.html start.sh open.sh install.sh config.example.json \
           README.md SKILL.md LICENSE references tests docs .gitignore; do
    [ -e "$HERE/$f" ] || continue
    cp -R "$HERE/$f" "$SKILL_DIR/"
  done
  chmod +x "$SKILL_DIR/start.sh" "$SKILL_DIR/open.sh" 2>/dev/null || true
  echo "app copied to   $SKILL_DIR"
else
  SKILL_DIR="$HERE"
  echo "running in place: $SKILL_DIR"
fi

# 2) desktop menu entry -> open.sh (starts the server if needed, opens the browser)
if [ "$DO_DESKTOP" = 1 ]; then
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Hermes Watchtower
Comment=Observability dashboard for Hermes agent profiles
Exec=$SKILL_DIR/open.sh $PORT
Icon=utilities-system-monitor
Terminal=false
Categories=Development;Utility;
EOF
  chmod 644 "$DESKTOP_FILE"
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
  fi
  echo "desktop entry:  $DESKTOP_FILE"
fi

# 3) optional always-on service
if [ "$DO_SYSTEMD" = 1 ]; then
  UNIT_DIR="$HOME/.config/systemd/user"
  mkdir -p "$UNIT_DIR"
  PY="$(command -v python3)"
  cat > "$UNIT_DIR/hermes-watchtower.service" <<EOF
[Unit]
Description=Hermes Watchtower dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$SKILL_DIR
Environment=HERMES_HOME=$HERMES_HOME
ExecStart=$PY $SKILL_DIR/server.py --port $PORT
Restart=on-failure

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now hermes-watchtower.service
  echo "systemd unit:   $UNIT_DIR/hermes-watchtower.service (enabled)"
fi

cat <<EOF

Next steps
  1. Restart your Hermes session (the skill index is built at session start),
     then just ask: "open the watchtower".
  2. Or click "Hermes Watchtower" in your app menu (open.sh starts the server
     if needed and opens the browser).
  3. Or run: $SKILL_DIR/open.sh
EOF
