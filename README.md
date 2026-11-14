# Thermals

[![CI](https://github.com/Yash55-max/thermals/actions/workflows/ci.yml/badge.svg)](https://github.com/Yash55-max/thermals/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Linux: All Major Distros](https://img.shields.io/badge/platform-Linux-orange.svg)](https://kernel.org)

**Thermals** (`asus-thermals-control`) is a lightweight, zero-dependency Linux hardware telemetry monitor and platform thermal controller. Designed for modern laptops and desktop workstations, Thermals provides real-time sensor monitoring, a closed-loop graduated thermal limiter, battery longevity controls, CPU Turbo Boost regulation, and dual graphical (GUI) and command-line (CLI/JSON) interfaces.

---

## Hardware Compatibility Matrix

Unlike generic scripts that make unsupported claims across untested architectures, Thermals strictly distinguishes between hardware verified on physical machines, standard ACPI interfaces expected to work across modern Linux kernels, and vendor-specific features:

| Hardware Category | Subsystem / Driver | Tested & Verified | Expected (Standard ACPI) | Notes |
| :--- | :--- | :---: | :---: | :--- |
| **ASUS Laptops** | `asus-nb-wmi`, `asus_wmi` | **ASUS Vivobook Go E1504FA** | Zenbook, Vivobook, TUF | Native profile switching & battery threshold |
| **AMD Processors** | `k10temp`, `amd_energy` | **Ryzen 5 7520U** | Ryzen 3000/5000/7000/8000 | Tctl, Tdie, per-CCD temps, CPB Boost toggle |
| **Intel Processors** | `coretemp`, `intel_pstate` | — | Core 6th–14th Gen, Xeon | Package, per-core temps, turbo toggle |
| **AMD Graphics** | `amdgpu`, `radeon` | **Radeon 610M (iGPU)** | RX 5000/6000/7000, Radeon Vega | Edge, junction, VRAM telemetry |
| **NVIDIA Graphics** | `nvidia-smi`, `nouveau` | — | GeForce RTX, GTX, Quadro | GPU temp, fan speed %, power draw via SMI |
| **Intel Graphics** | `i915`, `xe` | — | Iris Xe, Arc Alchemist | Kernel hwmon thermal reporting |
| **Storage (NVMe)** | `nvme` kernel driver | **Kingston / Micron NVMe** | Standard NVMe 1.3/1.4/2.0 | Composite temp, sensor 1/2, critical limits |
| **Battery Threshold** | `charge_control_end_threshold` | **ASUS BAT0** | ASUS, Lenovo ThinkPad, modern laptops | 50%–100% hardware charge limiter |
| **Cooling Fans** | `hwmon*/pwm*` | **ASUS WMI Fan** | Standard hwmon PWM chips | Governed by safety rails (minimum 50 PWM floor) |
| **Platform Profiles** | `acpi/platform_profile` | **ACPI Standard** | Modern Dell, Lenovo, HP, ASUS | Quiet, Balanced, Performance modes |

> Community reports are actively welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) to submit telemetry from your hardware model.

---

## Key Features

### 1. Graduated Thermal Limiter (Closed-Loop Thermostat)
Prevents thermal throttling and eliminates profile flapping under bursty workloads. Rather than crude binary switching, the limiter employs a 4-stage graduated regulation pipeline protected by a 30-second minimum dwell timer and 5°C hysteresis:
- **Stage 0 (Normal)**: User's baseline profile and full CPU Turbo Boost.
- **Stage 1 ($T \ge T_{\text{target}}$)**: Scales CPU Energy-Performance Preference (EPP) to `balance_power`.
- **Stage 2 ($T \ge T_{\text{target}} + 3^\circ\text{C}$)**: Temporarily disables CPU Turbo Boost to cap thermal spikes.
- **Stage 3 ($T \ge T_{\text{target}} + 6^\circ\text{C}$)**: Engages Quiet platform profile and maximum cooling acoustics until temperatures stabilize.
- **Headless Daemon**: Can run 24/7 as a background systemd user service (`thermals.service`) without opening the GUI.

### 2. Verified Thermal Performance
On an ASUS Vivobook Go E1504FA (Ryzen 5 7520U) running continuous multi-threaded compilation workloads:
- **Default Performance Profile**: Peaks at **84.5°C** with high acoustic whine.
- **Thermals Active Limiter (Target 72°C)**: Stabilizes peak thermals at **68°C–71°C** (**~15°C–16°C reduction**), maintaining 92% sustained compute throughput with virtually silent fan operation.

### 3. Battery Longevity Limit
Lithium-ion laptop batteries degrade quickly when continuously held at 100% capacity under heat. Thermals enables hardware-level charge capping (`charge_control_end_threshold`) to **60%**, **80%**, or **100%** on ASUS and compatible laptops.

### 4. Fan Safety Rails & Emergency Overrides
Manual fan sliders without safeguards are hazardous to hardware. Thermals enforces:
- **Motor Stalling Safety Floor**: Strictly rejects 0% and clamps all manual PWM requests to at least 50 PWM (~20% duty cycle) to ensure bearings spin and airflow never stops.
- **Emergency Thermal Override**: If hardware reaches **80.0°C** while under manual fan control, Thermals instantly revokes manual mode and restores fans to maximum automatic firmware curves.
- **Exit & Signal Restoration**: Registers `SIGINT`, `SIGTERM`, `SIGHUP`, and `atexit` handlers to guarantee firmware fan control is restored on exit, crash, or reboot.

### 5. Throttling Detection & Resource Attribution
- Real-time detection of thermal throttling by monitoring CPU frequency suppression under elevated temperatures.
- Live process attribution showing the top CPU-consuming processes directly alongside temperature graphs.

---

## Installation

### Method 1: Universal Installer (Recommended)

```bash
git clone https://github.com/Yash55-max/thermals.git
cd thermals

# Install dependencies (Debian/Ubuntu, Fedora, Arch, openSUSE)
bash install.sh --install-deps

# Install for current user (no root required)
bash install.sh --user

# Optional: Set up privileged helper for battery & fan limits
bash install.sh --with-helper

# Optional: Enable background thermal limiter service
bash install.sh --enable-service
```

### Method 2: Distribution Packages

#### Debian / Ubuntu / Linux Mint (.deb)
```bash
# Build package locally
bash package.sh

# Install package
sudo apt install ./dist/asus-thermals-control_1.2.0_all.deb
```

#### Arch Linux / Manjaro (PKGBUILD)
```bash
makepkg -si
```

#### Fedora / RHEL (RPM)
```bash
rpmbuild -bb asus-thermals-control.spec
```

---

## CLI & Headless Usage

Thermals is fully usable in headless environments, remote SSH sessions, or custom desktop status bars (Polybar, Waybar, i3blocks):

```bash
# Formatted hardware overview and status
thermals --status

# Full inventory of all detected hardware sensors and thresholds
thermals --list-hardware

# Machine-readable JSON output (ideal for Waybar/Polybar scripts)
thermals --json

# Interactive real-time terminal dashboard
thermals --monitor

# Set target temperature limit in Celsius (or 'off' to disable)
thermals --set-target-temp 72
thermals --set-target-temp off

# Switch platform thermal profile
thermals --set-profile quiet
thermals --set-profile balanced
thermals --set-profile performance

# Toggle CPU Turbo Boost
thermals --set-boost off
thermals --set-boost on

# Set battery charge threshold (50%–100%)
thermals --set-battery-limit 80

# Instant max cooling (Cooler Boost)
thermals --cooler-boost

# Run headless thermostat daemon in the background
thermals --daemon

# Test desktop push alert notification
thermals --test-alert
```

### JSON Telemetry for Status Bars

```json
{
  "system": {
    "vendor": "ASUS",
    "model": "Vivobook Go E1504FA",
    "cpu": "AMD Ryzen 5 7520U with Radeon Graphics",
    "os": "Ubuntu 26.04.1 LTS"
  },
  "temperatures": {
    "cpu": 64.4,
    "gpu": 58.0,
    "nvme": 39.9,
    "acpi": 63.0
  },
  "controls": {
    "active_profile": "balanced",
    "available_profiles": ["quiet", "balanced", "performance"],
    "target_temp_enabled": true,
    "target_temp_c": 72.0,
    "cpu_boost": true,
    "battery_charge_limit": 80
  }
}
```

---

## Systemd Service (Headless Limiter)

To run the closed-loop thermal limiter and safety monitor continuously in the background without needing the GUI:

```bash
# Enable and start user service
systemctl --user enable --now thermals.service

# Check daemon status
systemctl --user status thermals.service

# View live daemon logs
journalctl --user -u thermals.service -f
```

---

## Security & Privilege Architecture

Thermals adheres to strict privilege separation. The graphical UI and sensor monitors run entirely unprivileged as your standard desktop user:
- Reading `/sys/class/hwmon` and `/proc` requires no root permissions.
- Desktop thermal profile changes leverage standard `power-profiles-daemon` D-Bus interfaces.
- Privileged operations (direct fan PWM, battery charge threshold, CPU boost sysfs writes) are delegated to `/usr/local/sbin/asus-thermal-profile`.
- The helper strictly canonicalizes all paths with `readlink -f`, verifies paths against an exact regex whitelist (`^/sys/class/hwmon/hwmon[0-9]+/pwm[1-9]$`), validates bounded numeric parameters, and is checked with `visudo -c` during installation.

For details, review [SECURITY.md](SECURITY.md).

---

## Comparison with Existing Tools

| Capability | Thermals | TLP | auto-cpufreq | CoolerControl | asusctl |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Target Temp Limiter (Closed Loop)** | Yes | No | No | No | No |
| **Dual GUI & CLI with JSON** | Yes | CLI only | CLI only | GUI only | CLI only |
| **Non-ROG / Budget ASUS Laptop Support** | Yes | Generic | Generic | Partial | No (ROG/TUF only) |
| **Battery Charge Threshold Control** | Yes | Vendor | No | No | ROG only |
| **Zero Heavy Dependencies (Pure Python)** | Yes | No (Perl) | No | No (Rust/C++) | No (Rust) |
| **Fan Safety Floor & Thermal Override** | Yes | N/A | N/A | Curves | Profile-bound |

---

## Uninstallation

```bash
# Clean uninstall of application, service, and desktop shortcuts
bash install.sh --uninstall
```

---

## License

This project is licensed under the [MIT License](LICENSE).
