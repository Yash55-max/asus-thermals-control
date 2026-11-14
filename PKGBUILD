# Maintainer: Yashwanth Ponnam <yashwanthponnam55@gmail.com>
pkgname=asus-thermals-control
pkgver=1.2.0
pkgrel=1
pkgdesc="Native Linux hardware telemetry monitor and platform profile manager"
arch=('any')
url="https://github.com/Yash55-max/thermals"
license=('MIT')
depends=('python>=3.8' 'tk' 'lm_sensors' 'libnotify')
optdepends=(
    'polkit: Graphical authorization for manual profile changes'
    'sudo: Passwordless unattended thermal profile switching'
    'power-profiles-daemon: Alternative desktop power management backend'
)
source=("${pkgname}-${pkgver}.tar.gz::https://github.com/Yash55-max/${pkgname}/archive/v${pkgver}.tar.gz")
sha256sums=('SKIP')

package() {
    cd "${srcdir}/${pkgname}-${pkgver}"

    install -d -m 0755 "${pkgdir}/usr/share/${pkgname}"
    install -d -m 0755 "${pkgdir}/usr/bin"
    install -d -m 0755 "${pkgdir}/usr/share/applications"
    install -d -m 0755 "${pkgdir}/usr/share/icons/hicolor/scalable/apps"
    install -d -m 0755 "${pkgdir}/usr/share/licenses/${pkgname}"

    install -m 0755 app.py "${pkgdir}/usr/share/${pkgname}/app.py"
    install -m 0755 asus-thermal-profile "${pkgdir}/usr/share/${pkgname}/asus-thermal-profile"
    install -m 0755 run.sh "${pkgdir}/usr/share/${pkgname}/run.sh"
    install -m 0755 install-helper.sh "${pkgdir}/usr/share/${pkgname}/install-helper.sh"

    if [ -f assets/thermals.svg ]; then
        install -m 0644 assets/thermals.svg "${pkgdir}/usr/share/icons/hicolor/scalable/apps/thermals.svg"
    fi

    cat > "${pkgdir}/usr/bin/thermals" <<'EOF'
#!/usr/bin/env bash
exec python3 /usr/share/asus-thermals-control/app.py "$@"
EOF
    chmod 0755 "${pkgdir}/usr/bin/thermals"
    ln -sf thermals "${pkgdir}/usr/bin/${pkgname}"

    cat > "${pkgdir}/usr/share/applications/thermals.desktop" <<'EOF'
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
    chmod 0644 "${pkgdir}/usr/share/applications/thermals.desktop"

    install -m 0644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
}
