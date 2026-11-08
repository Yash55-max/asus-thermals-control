#!/usr/bin/env bash
# Build release packages for asus-thermals-control
set -euo pipefail

VERSION="1.0.0"
PKG_NAME="asus-thermals-control"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
BUILD_DIR="$DIST_DIR/build"

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR" "$BUILD_DIR"

# 1. Source release tarball
TARBALL_DIR="$BUILD_DIR/${PKG_NAME}-${VERSION}"
mkdir -p "$TARBALL_DIR"
cp "$SCRIPT_DIR/app.py" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/asus-thermal-profile" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/install-helper.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/install-thermals.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/run.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/README.md" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/LICENSE" "$TARBALL_DIR/"
cp -r "$SCRIPT_DIR/assets" "$TARBALL_DIR/"

tar -czf "$DIST_DIR/${PKG_NAME}-${VERSION}.tar.gz" -C "$BUILD_DIR" "${PKG_NAME}-${VERSION}"

# 2. Debian package (.deb)
DEB_ROOT="$BUILD_DIR/deb_root"
mkdir -p \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/${PKG_NAME}" \
  "$DEB_ROOT/usr/share/applications" \
  "$DEB_ROOT/usr/share/doc/${PKG_NAME}"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Yashwanth Ponnam <yashwanthponnam55@gmail.com>
Depends: python3 (>= 3.8), python3-tk, lm-sensors, libnotify-bin
Homepage: https://github.com/Yash55-max/asus-thermals-control
Description: Native Linux thermal control center and platform profile manager
 Thermals is a native Linux desktop application for monitoring hardware
 temperatures (CPU, GPU, NVMe, ACPI) and managing firmware thermal profiles
 (Quiet, Balanced, Performance) via Linux ACPI platform_profile.
EOF

cat > "$DEB_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF
chmod 0755 "$DEB_ROOT/DEBIAN/postinst"

cat > "$DEB_ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF
chmod 0755 "$DEB_ROOT/DEBIAN/postrm"

install -m 0755 "$SCRIPT_DIR/app.py" "$DEB_ROOT/usr/share/${PKG_NAME}/app.py"
install -m 0755 "$SCRIPT_DIR/asus-thermal-profile" "$DEB_ROOT/usr/share/${PKG_NAME}/asus-thermal-profile"
install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$DEB_ROOT/usr/share/${PKG_NAME}/install-helper.sh"

cat > "$DEB_ROOT/usr/bin/thermals" <<'EOF'
#!/usr/bin/env bash
exec python3 /usr/share/asus-thermals-control/app.py "$@"
EOF
chmod 0755 "$DEB_ROOT/usr/bin/thermals"

ln -sf thermals "$DEB_ROOT/usr/bin/${PKG_NAME}"

cat > "$DEB_ROOT/usr/share/applications/thermals.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Thermals
GenericName=Thermal Monitor
Comment=Monitor temperatures and manage ASUS thermal profiles
Exec=thermals
Icon=utilities-system-monitor
Terminal=false
Categories=System;Monitor;Utility;
StartupNotify=true
EOF
chmod 0644 "$DEB_ROOT/usr/share/applications/thermals.desktop"

install -m 0644 "$SCRIPT_DIR/README.md" "$DEB_ROOT/usr/share/doc/${PKG_NAME}/README.md"
install -m 0644 "$SCRIPT_DIR/LICENSE" "$DEB_ROOT/usr/share/doc/${PKG_NAME}/copyright"

dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DIST_DIR/${PKG_NAME}_${VERSION}_all.deb"

# 3. Generate SHA256 checksums
cd "$DIST_DIR"
sha256sum "${PKG_NAME}-${VERSION}.tar.gz" "${PKG_NAME}_${VERSION}_all.deb" > SHA256SUMS

rm -rf "$BUILD_DIR"

echo "Packages generated successfully in $DIST_DIR:"
ls -lh "$DIST_DIR"
