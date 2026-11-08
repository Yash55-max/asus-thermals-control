#!/usr/bin/env bash
# Installs a narrowly scoped NOPASSWD helper so automatic switching can work
# without spawning an authentication dialog every time a profile changes.
set -euo pipefail

TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  echo "Run this script as your normal desktop user: bash install-helper.sh"
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

sudo install -o root -g root -m 0755 "$HELPER_SOURCE" "$HELPER_DEST"
printf '%s ALL=(root) NOPASSWD: %s quiet, %s balanced, %s performance\n' \
  "$TARGET_USER" "$HELPER_DEST" "$HELPER_DEST" "$HELPER_DEST" \
  | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 0440 "$SUDOERS_FILE"

if ! sudo visudo -cf "$SUDOERS_FILE"; then
  sudo rm -f "$SUDOERS_FILE"
  echo "Sudoers validation failed; rule was removed." >&2
  exit 1
fi

echo "Installed secure profile helper for user: $TARGET_USER"
echo "Automatic profile switching can now change only quiet/balanced/performance."
