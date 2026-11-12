#!/usr/bin/env bash
# Universal Cross-Distribution Installer for Thermals
# Supports Debian/Ubuntu, Fedora/RHEL, Arch/Manjaro, openSUSE, Alpine, Void, etc.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="user"
INSTALL_DEPS=false
INSTALL_HELPER=false

show_help() {
  cat <<EOF
Thermals - Universal Linux Installer

Usage: bash install.sh [OPTIONS]

Options:
  --user            Install for the current user in ~/.local (default, no root needed)
  --system          Install system-wide to /usr/local (requires sudo)
  --install-deps    Automatically detect package manager and install required dependencies
  --with-helper     Also install the privileged thermal profile helper (requires sudo)
  --check           Check system requirements and thermal interface support
  -h, --help        Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) MODE="user"; shift ;;
    --system) MODE="system"; shift ;;
    --install-deps) INSTALL_DEPS=true; shift ;;
    --with-helper) INSTALL_HELPER=true; shift ;;
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
  elif command -v apk >/dev/null 2>&1; then echo "apk"
  elif command -v xbps-install >/dev/null 2>&1; then echo "xbps"
  else echo "unknown"
  fi
}

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
    apk)
      sudo apk add python3 py3-tkinter lm-sensors libnotify
      ;;
    xbps)
      sudo xbps-install -Sy python3 python3-tkinter lm_sensors libnotify
      ;;
    *)
      echo "Package manager not automatically recognized. Please ensure the following are installed:" >&2
      echo "  - python3" >&2
      echo "  - python3-tk (or tk)" >&2
      echo "  - lm-sensors (or lm_sensors)" >&2
      echo "  - libnotify-bin (or libnotify)" >&2
      ;;
  esac
fi

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
Keywords=thermal;temperature;fan;cpu;gpu;profile;acpi;sensors;
StartupNotify=true
EOF
  sudo chmod 0644 "$TARGET_APPS/thermals.desktop"

  if command -v update-desktop-database >/dev/null 2>&1; then
    sudo update-desktop-database "$TARGET_APPS" >/dev/null 2>&1 || true
  fi
  if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
  fi

else
  # User mode (default)
  bash "$SCRIPT_DIR/install-thermals.sh"
fi

if [[ "$INSTALL_HELPER" == true ]]; then
  echo
  echo "Setting up privileged platform profile helper..."
  bash "$SCRIPT_DIR/install-helper.sh"
fi

echo
echo "Installation complete!"
echo "Run 'thermals --status' in your terminal or launch 'Thermals' from your application menu."
