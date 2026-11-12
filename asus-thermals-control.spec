Name:           asus-thermals-control
Version:        1.1.0
Release:        1%{?dist}
Summary:        Native Linux hardware telemetry monitor and platform profile manager
License:        MIT
URL:            https://github.com/Yash55-max/asus-thermals-control
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.8
Requires:       python3-tkinter
Requires:       lm_sensors
Requires:       libnotify

%description
Thermals is a native Linux desktop and command-line application for monitoring
hardware temperatures (CPU, GPU, NVMe, ACPI) and managing firmware thermal profiles
(Quiet, Low Power, Balanced, Performance) via Linux ACPI platform_profile.

%prep
%autosetup

%build
# Pure python application

%install
rm -rf %{buildroot}
install -d -m 0755 %{buildroot}%{_datadir}/%{name}
install -d -m 0755 %{buildroot}%{_bindir}
install -d -m 0755 %{buildroot}%{_datadir}/applications
install -d -m 0755 %{buildroot}%{_datadir}/icons/hicolor/scalable/apps

install -m 0755 app.py %{buildroot}%{_datadir}/%{name}/app.py
install -m 0755 asus-thermal-profile %{buildroot}%{_datadir}/%{name}/asus-thermal-profile
install -m 0755 run.sh %{buildroot}%{_datadir}/%{name}/run.sh
install -m 0755 install-helper.sh %{buildroot}%{_datadir}/%{name}/install-helper.sh
install -m 0644 assets/thermals.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/thermals.svg

cat > %{buildroot}%{_bindir}/thermals <<'EOF'
#!/usr/bin/env bash
exec python3 /usr/share/asus-thermals-control/app.py "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/thermals
ln -sf thermals %{buildroot}%{_bindir}/%{name}

cat > %{buildroot}%{_datadir}/applications/thermals.desktop <<'EOF'
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
chmod 0644 %{buildroot}%{_datadir}/applications/thermals.desktop

%files
%license LICENSE
%doc README.md
%{_bindir}/thermals
%{_bindir}/%{name}
%{_datadir}/%{name}
%{_datadir}/applications/thermals.desktop
%{_datadir}/icons/hicolor/scalable/apps/thermals.svg

%changelog
* Wed Nov 12 2026 Yashwanth Ponnam <yashwanthponnam55@gmail.com> - 1.0.0-1
- Initial release with multi-architecture support and dual GUI/CLI interface
