#!/usr/bin/env bash
# Universal Installer for Thermals / asus-thermals-control
# Supports Debian/Ubuntu, Fedora/RHEL, Arch/Manjaro, and openSUSE.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="user"
INSTALL_DEPS=false
INSTALL_HELPER=false
ENABLE_SERVICE=false
UNINSTALL=false

show_help() {
  cat <<EOF
Thermals - Linux Thermal & Battery Management Center Installer

Usage: bash install.sh [OPTIONS]

Options:
  --user            Install for the current user in ~/.local (default, no root needed)
  --system          Install system-wide to /usr/local (requires sudo)
  --install-deps    Install system dependencies via apt, dnf, pacman, or zypper
  --with-helper     Install privileged hardware helper for fan & battery limits (requires sudo)
  --enable-service  Install and enable the background thermal limiter systemd service
  --uninstall       Remove installed application files, shortcuts, and service
  --check           Run environment and hardware sensor diagnostics
  -h, --help        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) MODE="user"; shift ;;
    --system) MODE="system"; shift ;;
    --install-deps) INSTALL_DEPS=true; shift ;;
    --with-helper) INSTALL_HELPER=true; shift ;;
    --enable-service) ENABLE_SERVICE=true; shift ;;
    --uninstall) UNINSTALL=true; shift ;;
    --check)
      echo "=== Thermals Environment Diagnostic ==="
      python3 "$SCRIPT_DIR/app.py" --status || true
      exit 0
      ;;
    -h|--help) show_help; exit 0 ;;
    *) echo "Unknown option: $1" >&2; show_help; exit 1 ;;
  esac
done

detect_pkg_manager() {
  if command -v apt-get >/dev/null 2>&1; then echo "apt"
  elif command -v dnf >/dev/null 2>&1; then echo "dnf"
  elif command -v pacman >/dev/null 2>&1; then echo "pacman"
  elif command -v zypper >/dev/null 2>&1; then echo "zypper"
  else echo "unknown"
  fi
}

# -----------------------------------------------------------------------------
# UNINSTALLATION
# -----------------------------------------------------------------------------
if [[ "$UNINSTALL" == true ]]; then
  echo "Uninstalling Thermals..."
  # User uninstall
  systemctl --user stop thermals.service 2>/dev/null || true
  systemctl --user disable thermals.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/thermals.service"
  rm -rf "$HOME/.local/opt/thermals"
  rm -f "$HOME/.local/bin/thermals"
  rm -f "$HOME/.local/share/applications/thermals.desktop"
  rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/thermals.svg"

  # System uninstall (if root or sudo available)
  if [[ "$EUID" -eq 0 ]] || command -v sudo >/dev/null 2>&1; then
    SUDO_CMD=""
    if [[ "$EUID" -ne 0 ]]; then SUDO_CMD="sudo"; fi
    $SUDO_CMD systemctl stop thermals.service 2>/dev/null || true
    $SUDO_CMD systemctl disable thermals.service 2>/dev/null || true
    $SUDO_CMD rm -f /etc/systemd/system/thermals.service
    $SUDO_CMD rm -rf /usr/local/share/thermals /usr/share/asus-thermals-control
    $SUDO_CMD rm -f /usr/local/bin/thermals /usr/bin/thermals
    $SUDO_CMD rm -f /usr/share/applications/thermals.desktop
    $SUDO_CMD rm -f /usr/share/icons/hicolor/scalable/apps/thermals.svg
    $SUDO_CMD rm -f /usr/local/sbin/asus-thermal-profile
    $SUDO_CMD rm -f /etc/sudoers.d/asus-thermal-control
  fi

  echo "Uninstallation complete."
  exit 0
fi

# -----------------------------------------------------------------------------
# DEPENDENCY INSTALLATION
# -----------------------------------------------------------------------------
if [[ "$INSTALL_DEPS" == true ]]; then
  PKG_MGR="$(detect_pkg_manager)"
  echo "Detected package manager: $PKG_MGR"
  echo "Installing system dependencies..."
  case "$PKG_MGR" in
    apt)
      sudo apt update
      sudo apt install -y python3 python3-tk lm-sensors libnotify-bin
      ;;
    dnf)
      sudo dnf install -y python3 python3-tkinter lm_sensors libnotify
      ;;
    pacman)
      sudo pacman -S --noconfirm --needed python tk lm_sensors libnotify
      ;;
    zypper)
      sudo zypper in -y python3 python3-tk sensors libnotify
      ;;
    *)
      echo "Package manager not recognized automatically. Required packages:" >&2
      echo "  - python3" >&2
      echo "  - python3-tk (or tk)" >&2
      echo "  - lm-sensors (or lm_sensors)" >&2
      echo "  - libnotify-bin (or libnotify)" >&2
      ;;
  esac
fi

# -----------------------------------------------------------------------------
# APPLICATION INSTALLATION
# -----------------------------------------------------------------------------
if [[ "$MODE" == "system" ]]; then
  TARGET_OPT="/usr/local/share/thermals"
  TARGET_BIN="/usr/local/bin"
  TARGET_APPS="/usr/share/applications"
  TARGET_ICONS="/usr/share/icons/hicolor/scalable/apps"

  echo "Installing system-wide to $TARGET_OPT (requires sudo)..."
  sudo install -d -m 0755 "$TARGET_OPT" "$TARGET_BIN" "$TARGET_APPS" "$TARGET_ICONS"
  sudo install -m 0755 "$SCRIPT_DIR/app.py" "$TARGET_OPT/app.py"
  sudo install -m 0755 "$SCRIPT_DIR/asus-thermal-profile" "$TARGET_OPT/asus-thermal-profile"
  sudo install -m 0755 "$SCRIPT_DIR/run.sh" "$TARGET_OPT/run.sh"
  sudo install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$TARGET_OPT/install-helper.sh"

  if [[ -f "$SCRIPT_DIR/assets/thermals.svg" ]]; then
    sudo install -m 0644 "$SCRIPT_DIR/assets/thermals.svg" "$TARGET_ICONS/thermals.svg"
  fi

  sudo ln -sf "$TARGET_OPT/run.sh" "$TARGET_BIN/thermals"

  sudo tee "$TARGET_APPS/thermals.desktop" >/dev/null <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Thermals
GenericName=Hardware Thermal Monitor
Comment=Monitor temperatures and manage Linux platform thermal profiles
Exec=thermals
Icon=thermals
Terminal=false
Categories=System;Monitor;Utility;HardwareSettings;
Keywords=thermal;temperature;fan;cpu;gpu;profile;acpi;sensors;battery;
StartupNotify=true
EOF
  sudo chmod 0644 "$TARGET_APPS/thermals.desktop"

  if command -v update-desktop-database >/dev/null 2>&1; then
    sudo update-desktop-database "$TARGET_APPS" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
  fi

  if [[ "$ENABLE_SERVICE" == true ]]; then
    echo "Installing systemd system service..."
    sudo install -m 0644 "$SCRIPT_DIR/thermals.service" /etc/systemd/system/thermals.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now thermals.service
    echo "Thermals background service enabled and started."
  fi

else
  # User mode (default)
  APP_DIR="$HOME/.local/opt/thermals"
  BIN_DIR="$HOME/.local/bin"
  APP_LAUNCHER_DIR="$HOME/.local/share/applications"
  ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
  DESKTOP_FILE="$APP_LAUNCHER_DIR/thermals.desktop"

  mkdir -p "$APP_DIR" "$BIN_DIR" "$APP_LAUNCHER_DIR" "$ICON_DIR"

  install -m 0755 "$SCRIPT_DIR/app.py" "$APP_DIR/app.py"
  install -m 0755 "$SCRIPT_DIR/asus-thermal-profile" "$APP_DIR/asus-thermal-profile"
  install -m 0755 "$SCRIPT_DIR/run.sh" "$APP_DIR/run.sh"
  install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$APP_DIR/install-helper.sh"

  if [[ -f "$SCRIPT_DIR/assets/thermals.svg" ]]; then
    install -m 0644 "$SCRIPT_DIR/assets/thermals.svg" "$ICON_DIR/thermals.svg"
  fi

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
Keywords=thermal;temperature;fan;cpu;gpu;profile;acpi;sensors;battery;
StartupNotify=true
EOF
  chmod 0644 "$DESKTOP_FILE"

  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_LAUNCHER_DIR" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
  fi

  if [[ "$ENABLE_SERVICE" == true ]]; then
    echo "Installing systemd user service..."
    USER_SVC_DIR="$HOME/.config/systemd/user"
    mkdir -p "$USER_SVC_DIR"
    install -m 0644 "$SCRIPT_DIR/thermals.service" "$USER_SVC_DIR/thermals.service"
    systemctl --user daemon-reload
    systemctl --user enable --now thermals.service
    echo "Thermals user background service enabled and started."
  fi
fi

if [[ "$INSTALL_HELPER" == true ]]; then
  echo
  echo "Installing privileged hardware helper..."
  bash "$SCRIPT_DIR/install-helper.sh"
fi

echo
echo "================================================="
echo "  Thermals installed successfully!"
echo "================================================="
echo "  Run 'thermals --status' in your terminal"
echo "  Or launch 'Thermals' from your desktop application launcher."
if [[ "$ENABLE_SERVICE" == false ]]; then
  echo
  echo "To enable background thermostat limiter independently of the GUI:"
  echo "  systemctl --user enable --now thermals.service"
fi
echo "================================================="
