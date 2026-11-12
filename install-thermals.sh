#!/usr/bin/env bash
# Install Thermals desktop application into the user's home directory.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/opt/thermals"
BIN_DIR="$HOME/.local/bin"
APP_LAUNCHER_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_FILE="$APP_LAUNCHER_DIR/thermals.desktop"

mkdir -p "$APP_DIR" "$BIN_DIR" "$APP_LAUNCHER_DIR" "$ICON_DIR"

# Install application files with correct execution permissions
install -m 0755 "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
install -m 0755 "$SCRIPT_DIR/asus-thermal-profile" "$APP_DIR/asus-thermal-profile"
install -m 0755 "$SCRIPT_DIR/run.sh" "$APP_DIR/run.sh"
install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$APP_DIR/install-helper.sh"

# Install scalable application icon
if [[ -f "$SCRIPT_DIR/assets/thermals.svg" ]]; then
  install -m 0644 "$SCRIPT_DIR/assets/thermals.svg" "$ICON_DIR/thermals.svg"
fi

# Add CLI launcher to ~/.local/bin/thermals
ln -sf "$APP_DIR/run.sh" "$BIN_DIR/thermals"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Thermals
GenericName=Hardware Thermal Monitor
Comment=Monitor temperatures and manage Linux platform thermal profiles
Exec=python3 $APP_DIR/app.py
Icon=thermals
Terminal=false
Categories=System;Monitor;Utility;HardwareSettings;
Keywords=thermal;temperature;fan;cpu;gpu;profile;acpi;sensors;
StartupNotify=true
EOF

chmod 0644 "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_LAUNCHER_DIR" >/dev/null 2>&1 || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "================================================="
echo "  Thermals installed successfully!"
echo "================================================="
echo "  CLI Command:      thermals (or python3 $APP_DIR/app.py)"
echo "  Desktop Launcher: $DESKTOP_FILE"
echo "  Application Dir:  $APP_DIR"
echo
echo "To enable passwordless automated thermal switching, optionally run:"
echo "  bash $APP_DIR/install-helper.sh"
echo "================================================="
