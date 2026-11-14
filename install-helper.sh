#!/usr/bin/env bash
# Thermals Helper Installer
# Installs the strictly-validated root helper and configures sudoers securely.
set -euo pipefail

TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$TARGET_USER" ]]; then
  TARGET_USER="$(id -un 2>/dev/null || whoami 2>/dev/null || echo "")"
fi

if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  echo "Error: Please run this script as your desktop user (e.g. bash install-helper.sh)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HELPER_SOURCE="$SCRIPT_DIR/asus-thermal-profile"
HELPER_DEST="/usr/local/sbin/asus-thermal-profile"
SUDOERS_FILE="/etc/sudoers.d/asus-thermal-control"

if [[ ! -f "$HELPER_SOURCE" ]]; then
  echo "Error: Missing helper source at $HELPER_SOURCE" >&2
  exit 1
fi

echo "Installing privileged hardware helper to $HELPER_DEST..."
sudo install -d -m 0755 -o root -g root "/usr/local/sbin"
sudo install -m 0755 -o root -g root "$HELPER_SOURCE" "$HELPER_DEST"

# Ensure /usr/local/sbin and the binary are strictly not writable by unprivileged users
sudo chmod 0755 "/usr/local/sbin" "$HELPER_DEST"
sudo chown root:root "/usr/local/sbin" "$HELPER_DEST"

# Write strictly scoped sudoers rule naming the exact binary
TMP_SUDOERS="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s\n' "$TARGET_USER" "$HELPER_DEST" > "$TMP_SUDOERS"
chmod 0440 "$TMP_SUDOERS"

# Validate with visudo before moving into /etc/sudoers.d
if command -v visudo >/dev/null 2>&1; then
  if ! sudo visudo -cf "$TMP_SUDOERS"; then
    rm -f "$TMP_SUDOERS"
    echo "Error: Sudoers validation failed; rule was not installed." >&2
    exit 1
  fi
fi

sudo install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"

echo "✓ Sudoers rule validated and installed successfully for user: $TARGET_USER"
echo "✓ Secure hardware helper is active at $HELPER_DEST."
