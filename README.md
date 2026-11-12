# Thermals

**Thermals** (Thermal Control Center) is a native Linux application for monitoring hardware temperatures and managing platform thermal/power profiles across **all Linux environments** and hardware architectures.

It provides real-time sensor telemetry, automated workload-aware profile switching, desktop alerts for critical thresholds, and **dual graphical (GUI) and terminal (CLI)** interfaces for desktop and headless systems alike.

![Thermals Dashboard](assets/thermals-dashboard.png)

---

## Universal Linux Compatibility

Thermals is engineered to run seamlessly across all Linux distributions, display servers, hardware vendors, and execution environments:

### Distributions Supported
- **Debian / Ubuntu / Kali / Linux Mint / Pop!_OS**: `.deb` release package, APT installer.
- **Fedora / RHEL / CentOS / Rocky Linux / AlmaLinux**: RPM package, DNF installer.
- **Arch Linux / Manjaro / EndeavourOS**: `PKGBUILD`, Pacman.
- **openSUSE (Tumbleweed & Leap)**: Zypper installer and RPM package.
- **Alpine Linux / Void Linux / Gentoo / NixOS / Slackware**: Universal Python wheel and standard source installer.

### Hardware & Architecture Supported
- **CPU Architectures**: `x86_64`, `aarch64` (ARM64), `armv7l`, `riscv64`.
- **CPU Telemetry**:
  - Intel Core / Xeon / Pentium (`coretemp`, `intel_rapl`, package & core thermals)
  - AMD Ryzen / EPYC / Athlon (`k10temp`, `zenpower`, `amd_energy`, Tctl / Tdie / Tccd)
  - ARM / Raspberry Pi / Apple Silicon (`bcm2835_thermal`, `cpu-thermal`, `soc-thermal`, `macsmc`, `panfrost`)
  - Generic Linux ACPI and Sysfs thermal zones (`/sys/class/thermal/thermal_zone*`)
- **GPU Telemetry**:
  - AMD Radeon (`amdgpu`, `radeon`, edge/junction/VRAM metrics)
  - NVIDIA GeForce / Quadro / RTX (proprietary driver via `nvidia-smi` and open-source `nouveau`)
  - Intel Iris Xe / Arc / UHD (`i915`, `xe`)
  - ARM Mali / Panfrost / Adreno
- **Storage & Ambient Telemetry**:
  - NVMe SSDs (`nvme`, composite sensors)
  - SATA / SAS SSDs and HDDs (kernel `drivetemp`)
  - Motherboard / ACPI thermal zones (`acpitz`, `asus_wmi`, `thinkpad_acpi`, `dell_smm`, `hp_wmi`)

### Display & Execution Environments
- **Desktop Environments**: GNOME, KDE Plasma, XFCE, Cinnamon, MATE, LXQt, Sway, Hyprland, i3.
- **Display Servers**: Wayland, X11, Xwayland with dynamic font detection and High-DPI scaling.
- **Headless / Server / SSH / Docker / WSL**: Automatic headless detection with full-featured CLI dashboard and JSON telemetry.

---

## Key Features

- **Multi-Sensor Telemetry Monitoring**
  - Real-time temperature monitoring for CPU, GPU, NVMe SSD, and ACPI thermal zones.
  - Multi-tier metric collection: `lm-sensors` JSON -> direct kernel `hwmon` sysfs -> kernel `thermal_zone` -> `nvidia-smi`.
  - Non-blocking 2-second background polling cycle.

- **Dual GUI & Terminal Interfaces**
  - **Tkinter GUI**: Dark-mode dashboard with rolling temperature history charts and dynamic platform profile controls.
  - **CLI Terminal Monitor**: Live ANSI terminal dashboard (`thermals --monitor`), one-shot report (`thermals --status`), or machine-readable JSON (`thermals --json`).

- **Firmware-Managed Platform Profiles**
  - Dynamic discovery of supported kernel ACPI profiles:
    - **Quiet / Low Power / Power Saver**: Silent operation, reduced acoustic footprint, and energy efficiency.
    - **Balanced**: Standard operational profile for everyday workloads.
    - **Performance / Balanced+**: Maximizes fan curves, power limits, and processor throughput.
  - Works with ACPI `platform_profile` (Linux 5.8 through 6.12+ class devices) and `power-profiles-daemon` (`powerprofilesctl`).

- **Automated Workload & Temperature-Aware Profile Switching**
  - Continuous CPU load sampling (`/proc/stat` with `os.getloadavg` fallback) and thermal tracking.
  - Thermal override protection: forces conservative cooling modes under elevated thermal stress.
  - Multi-sample confirmation (3 consecutive cycles) and minimum dwell timer (60s) to eliminate profile flapping.

- **Desktop Temperature Alerts**
  - Configurable alert thresholds for CPU and GPU temperatures.
  - Dispatches native desktop notifications via `notify-send`.
  - Built-in 5°C reset hysteresis to avoid notification spam.

---

## Installation

### Option 1: Universal Installer (Recommended for All Distros)

Clone this repository and run the universal installer:

```bash
git clone https://github.com/Yash55-max/asus-thermals-control.git
cd asus-thermals-control
bash install.sh
```

**Installer Options:**
- `bash install.sh --install-deps` : Automatically installs dependencies using your distro's native package manager (`apt`, `dnf`, `pacman`, `zypper`, `apk`, `xbps`).
- `bash install.sh --user` : Installs to `~/.local` (default, no root password required).
- `bash install.sh --system` : Installs system-wide to `/usr/local` (requires sudo).
- `bash install.sh --with-helper` : Installs the privileged helper for unattended automated switching.
- `bash install.sh --check` : Prints a diagnostic report of your system's telemetry and profile backends.

---

### Option 2: Distribution Packages

#### Debian / Ubuntu / Kali / Linux Mint (.deb)
```bash
sudo apt install ./dist/asus-thermals-control_1.1.0_all.deb
```
Or build from source:
```bash
bash package.sh
sudo apt install ./dist/asus-thermals-control_1.1.0_all.deb
```

#### Fedora / RHEL / openSUSE (RPM)
Using the provided RPM spec file:
```bash
rpmbuild -bb asus-thermals-control.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/asus-thermals-control-1.1.0-1*.noarch.rpm
```

#### Arch Linux / Manjaro (PKGBUILD)
```bash
makepkg -si
```

#### Python PIP / PIPX (Any Linux Distribution)
```bash
pip install .
# or using pipx
pipx install .
```

---

## CLI & Headless Usage

Thermals can be used in servers, SSH terminals, scripts, and status bars without any graphical display:

```bash
# Print current telemetry snapshot and exit
thermals --status

# Output complete system telemetry in JSON format (ideal for scripts & Polybar/Waybar)
thermals --json

# Run real-time interactive terminal monitor (updates every 2 seconds)
thermals --monitor

# List platform thermal profiles supported on your hardware
thermals --list-profiles

# Set system thermal profile from the terminal
thermals --set-profile performance
```

Example JSON output (`thermals --json`):
```json
{
  "system": {
    "summary": "ASUS VivoBook Go E1504FA • AMD Ryzen 5 7520U",
    "os": "Ubuntu 24.04 LTS"
  },
  "temperatures": {
    "cpu": 48.5,
    "gpu": 44.0,
    "nvme": 38.0,
    "acpi": 47.0
  },
  "cpu_load_percent": 14.2,
  "active_profile": "balanced",
  "available_profiles": ["quiet", "balanced", "performance"]
}
```

---

## Privileged Helper Setup (Unattended Automated Switching)

When using automatic profile switching or unprivileged profile transitions:

1. **Power Profiles Daemon**: If `power-profiles-daemon` is running, profile switches execute without passwords for active desktop sessions.
2. **ACPI Sysfs Helper**: To allow passwordless direct switching of kernel ACPI platform profiles, run:

```bash
bash install-helper.sh
```

This installs `/usr/local/sbin/asus-thermal-profile` and configures a strictly scoped `/etc/sudoers.d/asus-thermal-control` rule allowing only valid thermal profile commands (`quiet`, `balanced`, `performance`, `low-power`, `cool`, `balanced-performance`).

---

## Configuration

User settings are saved in standard JSON format:
```
~/.config/asus-thermal-control-center/config.json
```

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `alerts_enabled` | boolean | `true` | Enables or disables desktop notification alerts. |
| `cpu_alert_c` | float | `85.0` | CPU temperature threshold in Celsius for alert triggers. |
| `gpu_alert_c` | float | `85.0` | GPU temperature threshold in Celsius for alert triggers. |
| `auto_enabled` | boolean | `false` | Enables automatic profile switching based on load and thermals. |
| `auto_performance_load` | float | `70.0` | CPU load percentage required to step up to Performance mode. |
| `auto_quiet_load` | float | `20.0` | CPU load percentage threshold for stepping down to Quiet mode. |
| `auto_cool_cpu_c` | float | `58.0` | Maximum CPU temperature allowed to permit Quiet mode downshift. |
| `auto_hot_cpu_c` | float | `75.0` | CPU temperature threshold that triggers Balanced downshift. |
| `auto_critical_c` | float | `85.0` | Critical threshold overriding performance to force cooling. |

---

## Uninstallation

To remove Thermals from your system:

### User Installation Removal:
```bash
rm -rf ~/.local/opt/thermals ~/.local/bin/thermals
rm -f ~/.local/share/applications/thermals.desktop
rm -f ~/.local/share/icons/hicolor/scalable/apps/thermals.svg
update-desktop-database ~/.local/share/applications/ >/dev/null 2>&1 || true
```

### System Installation Removal:
```bash
sudo rm -rf /usr/local/share/thermals /usr/local/bin/thermals
sudo rm -f /usr/share/applications/thermals.desktop
sudo rm -f /usr/share/icons/hicolor/scalable/apps/thermals.svg
```

### Remove Sudoers Helper (If installed):
```bash
sudo rm -f /usr/local/sbin/asus-thermal-profile
sudo rm -f /etc/sudoers.d/asus-thermal-control
```

---

## License

This project is licensed under the [MIT License](LICENSE).
