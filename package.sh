#!/usr/bin/env bash
# Build release packages for asus-thermals-control across Linux distributions
set -euo pipefail

VERSION="1.1.0"
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
cp "$SCRIPT_DIR/install.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/install-helper.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/install-thermals.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/run.sh" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/setup.py" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/pyproject.toml" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/PKGBUILD" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/asus-thermals-control.spec" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/README.md" "$TARBALL_DIR/"
cp "$SCRIPT_DIR/LICENSE" "$TARBALL_DIR/"
cp -r "$SCRIPT_DIR/assets" "$TARBALL_DIR/"

tar -czf "$DIST_DIR/${PKG_NAME}-${VERSION}.tar.gz" -C "$BUILD_DIR" "${PKG_NAME}-${VERSION}"

# Copy packaging specs to dist for direct distribution
cp "$SCRIPT_DIR/PKGBUILD" "$DIST_DIR/"
cp "$SCRIPT_DIR/asus-thermals-control.spec" "$DIST_DIR/"

# 2. Debian package (.deb)
DEB_ROOT="$BUILD_DIR/deb_root"
mkdir -p \
  "$DEB_ROOT/DEBIAN" \
  "$DEB_ROOT/usr/bin" \
  "$DEB_ROOT/usr/share/${PKG_NAME}" \
  "$DEB_ROOT/usr/share/applications" \
  "$DEB_ROOT/usr/share/icons/hicolor/scalable/apps" \
  "$DEB_ROOT/usr/share/doc/${PKG_NAME}"

cat > "$DEB_ROOT/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Yashwanth Ponnam <yashwanthponnam55@gmail.com>
Depends: python3 (>= 3.8), python3-tk, lm-sensors, libnotify-bin
Suggests: policykit-1, power-profiles-daemon
Homepage: https://github.com/Yash55-max/asus-thermals-control
Description: Native Linux thermal control center and platform profile manager
 Thermals is a native Linux desktop and CLI application for monitoring hardware
 temperatures (CPU, GPU, NVMe, ACPI) and managing firmware thermal profiles
 (Quiet, Low Power, Balanced, Performance) via Linux ACPI platform_profile.
EOF

cat > "$DEB_ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "configure" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF

cat > "$DEB_ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
    fi
fi

exit 0
EOF

install -m 0755 "$SCRIPT_DIR/app.py" "$DEB_ROOT/usr/share/${PKG_NAME}/app.py"
install -m 0755 "$SCRIPT_DIR/asus-thermal-profile" "$DEB_ROOT/usr/share/${PKG_NAME}/asus-thermal-profile"
install -m 0755 "$SCRIPT_DIR/run.sh" "$DEB_ROOT/usr/share/${PKG_NAME}/run.sh"
install -m 0755 "$SCRIPT_DIR/install-helper.sh" "$DEB_ROOT/usr/share/${PKG_NAME}/install-helper.sh"

if [[ -f "$SCRIPT_DIR/assets/thermals.svg" ]]; then
  install -m 0644 "$SCRIPT_DIR/assets/thermals.svg" "$DEB_ROOT/usr/share/icons/hicolor/scalable/apps/thermals.svg"
fi

cat > "$DEB_ROOT/usr/bin/thermals" <<'EOF'
#!/usr/bin/env bash
exec python3 /usr/share/asus-thermals-control/app.py "$@"
EOF

ln -sf thermals "$DEB_ROOT/usr/bin/${PKG_NAME}"

cat > "$DEB_ROOT/usr/share/applications/thermals.desktop" <<EOF
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

install -m 0644 "$SCRIPT_DIR/README.md" "$DEB_ROOT/usr/share/doc/${PKG_NAME}/README.md"
install -m 0644 "$SCRIPT_DIR/LICENSE" "$DEB_ROOT/usr/share/doc/${PKG_NAME}/copyright"

# Standardize permissions for Debian packaging (requires 0755 dirs, 0644 files, 0755 executables)
find "$DEB_ROOT" -type d -exec chmod 0755 {} +
find "$DEB_ROOT" -type f -exec chmod 0644 {} +
chmod 0755 "$DEB_ROOT/DEBIAN/postinst" "$DEB_ROOT/DEBIAN/postrm"
chmod 0755 "$DEB_ROOT/usr/bin/thermals"
chmod 0755 "$DEB_ROOT/usr/share/${PKG_NAME}/app.py"
chmod 0755 "$DEB_ROOT/usr/share/${PKG_NAME}/asus-thermal-profile"
chmod 0755 "$DEB_ROOT/usr/share/${PKG_NAME}/run.sh"
chmod 0755 "$DEB_ROOT/usr/share/${PKG_NAME}/install-helper.sh"

dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DIST_DIR/${PKG_NAME}_${VERSION}_all.deb"

# 3. Build Python Wheel / Sdist if python3-build or setuptools wheel is available
if python3 -c "import build" 2>/dev/null; then
  python3 -m build --outdir "$DIST_DIR" "$SCRIPT_DIR" >/dev/null 2>&1 || true
elif python3 setup.py --help bdist_wheel >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/setup.py" bdist_wheel --dist-dir "$DIST_DIR" >/dev/null 2>&1 || true
fi

# 4. Generate RPM package if rpmbuild is available
if command -v rpmbuild >/dev/null 2>&1; then
  RPM_TOP="$BUILD_DIR/rpmbuild"
  mkdir -p "$RPM_TOP"/{BUILD,RPMS,SOURCES,SPECS,SRPMS}
  cp "$DIST_DIR/${PKG_NAME}-${VERSION}.tar.gz" "$RPM_TOP/SOURCES/"
  rpmbuild --define "_topdir $RPM_TOP" -bb "$SCRIPT_DIR/asus-thermals-control.spec" >/dev/null 2>&1 || true
  find "$RPM_TOP/RPMS" -name "*.rpm" -exec cp {} "$DIST_DIR/" \; || true
fi

# 5. Generate SHA256 checksums
cd "$DIST_DIR"
find . -maxdepth 1 -type f ! -name "SHA256SUMS" ! -name "PKGBUILD" ! -name "*.spec" -exec sha256sum {} + > SHA256SUMS

rm -rf "$BUILD_DIR"

echo "Packages generated successfully in $DIST_DIR:"
ls -lh "$DIST_DIR"
