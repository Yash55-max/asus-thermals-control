#!/usr/bin/env bash
# Install the existing dashboard as a desktop application named "Thermals".
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/opt/thermals"
APP_LAUNCHER_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$APP_LAUNCHER_DIR/thermals.desktop"

mkdir -p "$APP_DIR" "$APP_LAUNCHER_DIR"

install -m 0644 "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
install -m 0644 "$SCRIPT_DIR/asus-thermal-profile" "$APP_DIR/asus-thermal-profile"
install -m 0755 "$SCRIPT_DIR/run.sh" "$APP_DIR/run.sh"
install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$APP_DIR/install-helper.sh"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Thermals
GenericName=Thermal Monitor
Comment=Monitor temperatures and manage ASUS thermal profiles
Exec=python3 $APP_DIR/app.py
Icon=utilities-system-monitor
Terminal=false
Categories=System;Monitor;Utility;
StartupNotify=true
EOF

chmod 0644 "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_LAUNCHER_DIR" >/dev/null 2>&1 || true
fi

echo "Installed: Thermals"
echo "Application menu: search for 'Thermals'"
echo "Launch command: python3 \"$APP_DIR/app.py\""
echo "Application files: $APP_DIR"
echo
echo "No autostart entry was created."
