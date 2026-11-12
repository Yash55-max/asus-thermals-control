#!/usr/bin/env bash
# Installs a narrowly scoped NOPASSWD helper so automatic switching can work
# without spawning an authentication dialog every time a profile changes.
set -euo pipefail

TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$TARGET_USER" ]]; then
  TARGET_USER="$(id -un 2>/dev/null || whoami 2>/dev/null || echo "")"
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  echo "Please run this script as your normal desktop user: bash install-helper.sh"
  echo "Or specify user: SUDO_USER=username bash install-helper.sh"
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HELPER_SOURCE="$SCRIPT_DIR/asus-thermal-profile"
HELPER_DEST="/usr/local/sbin/asus-thermal-profile"
SUDOERS_FILE="/etc/sudoers.d/asus-thermal-control"

if [[ ! -f "$HELPER_SOURCE" ]]; then
  echo "Missing helper source: $HELPER_SOURCE" >&2
  exit 1
fi

echo "Installing privileged profile helper to $HELPER_DEST..."
sudo install -d -m 0755 "/usr/local/sbin"
sudo install -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_DEST"

# Allow all kernel platform profile options
PROFILES=("quiet" "balanced" "performance" "low-power" "cool" "balanced-performance")
SUDO_CMDS=""
for p in "${PROFILES[@]}"; do
  if [[ -n "$SUDO_CMDS" ]]; then
    SUDO_CMDS+=", "
  fi
  SUDO_CMDS+="$HELPER_DEST $p"
done

printf '%s ALL=(root) NOPASSWD: %s\n' "$TARGET_USER" "$SUDO_CMDS" \
  | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"

if command -v visudo >/dev/null 2>&1; then
  if ! sudo visudo -cf "$SUDOERS_FILE"; then
    sudo rm -f "$SUDOERS_FILE"
    echo "Sudoers validation failed; rule was removed." >&2
    exit 1
  fi
fi

echo "Installed secure profile helper for user: $TARGET_USER"
echo "Automatic and manual profile switching can now change platform profiles seamlessly."
