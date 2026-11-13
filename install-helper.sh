#!/usr/bin/env bash
# Installs a secure NOPASSWD helper so thermal profile switching, target temperature regulation,
# and cooling management work seamlessly without spawning password prompts.
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

echo "Installing privileged hardware thermal helper to $HELPER_DEST..."
sudo install -d -m 0755 "/usr/local/sbin"
sudo install -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_DEST"

# Allow the strictly-validated root helper script to be executed by target user
printf '%s ALL=(root) NOPASSWD: %s\n' "$TARGET_USER" "$HELPER_DEST" \
  | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"

if command -v visudo >/dev/null 2>&1; then
  if ! sudo visudo -cf "$SUDOERS_FILE"; then
    sudo rm -f "$SUDOERS_FILE"
    echo "Sudoers validation failed; rule was removed." >&2
    exit 1
  fi
fi

echo "Installed secure thermal helper for user: $TARGET_USER"
echo "Thermal profiles, CPU thermal tuning, and cooling controls can now operate seamlessly."
