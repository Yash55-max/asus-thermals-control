# Thermals

**Thermals** (Thermal Control Center) is a native Linux application for monitoring all types of hardware telemetry and actively controlling system temperatures, cooling policies, and power profiles across **all Linux environments** and hardware architectures.

It provides comprehensive sensor telemetry across all hardware categories (CPU, GPU, NVMe/SATA storage, motherboard, wireless network, battery, and cooling fans), an active **Target Temperature Limiter (smart thermostat)**, one-click **Cooler Boost (100% cooling)**, CPU Turbo Boost toggling, automated workload-aware profile switching, and **dual graphical (GUI) and terminal (CLI)** interfaces.

![Thermals Dashboard](assets/thermals-dashboard.png)

---

## Universal Linux Hardware Detection

Thermals dynamically detects and exposes telemetry for **all types of hardware** present on your system:

### 1. Processors (CPUs & SoCs)
- **Intel Core / Xeon / Pentium / Celeron**: Package temperature, individual core thermals, RAPL power envelopes (`coretemp`, `intel_rapl`).
- **AMD Ryzen / EPYC / Athlon / Threadripper**: Tctl, Tdie, per-CCD temperatures, energy sensors (`k10temp`, `zenpower`, `amd_energy`).
- **ARM / SBCs / Apple Silicon**: Broadcom (`bcm2835`), Rockchip, Allwinner, Qualcomm, Apple Silicon (`macsmc`).
- **Linux Thermal Zones**: Generic ACPI and sysfs thermal zones (`/sys/class/thermal/thermal_zone*`).
- **CPU Metrics**: Live CPU utilization percentage, scaling governor, driver, EPP, and Turbo Boost status.

### 2. Graphics (GPUs & Accelerators)
- **AMD Radeon**: Discrete & integrated GPUs (`amdgpu`, `radeon`), edge/core temperature, junction/hotspot, memory/VRAM thermals, PPT power draw (Watts), engine clock (sclk).
- **NVIDIA GeForce / Quadro / RTX**: Proprietary driver via `nvidia-smi` (core temp, memory temp, power draw, power limit, fan speed %) and open-source `nouveau`.
- **Intel Arc / Iris Xe / UHD**: Direct hwmon and driver telemetry (`i915`, `xe`).
- **Embedded GPUs**: ARM Mali, Panfrost, Adreno.
- **Multi-GPU Systems**: Simultaneously monitors both integrated (iGPU) and discrete (dGPU) adapters.

### 3. Storage Drives (NVMe & SATA / SAS)
- **NVMe SSDs**: Composite temperature, individual controller/NAND sensors, high/critical temperature trip points (`nvme`).
- **SATA / SAS SSDs & HDDs**: Kernel `drivetemp` hwmon interface and SCSI/block device mapping.

### 4. Motherboard, Chipset & Ambient
- **ACPI Ambient & Thermal Zones**: `acpitz`, generic thermal zones.
- **Hardware Monitor Super I/O Chips**: ITE (`it87*`), Nuvoton (`nct677*`), Winbond (`w83627*`), Fintek.
- **Vendor WMI Interfaces**: ASUS (`asus_wmi` / `asus-nb-wmi`), Lenovo ThinkPad (`thinkpad_acpi`), Dell (`dell_smm`), HP (`hp_wmi`).

### 5. Wireless & Network Adapters
- **Wi-Fi & Bluetooth**: MediaTek (`mt7921`), Intel (`iwlwifi`), Qualcomm Atheros (`ath10k`, `ath11k`), Realtek (`rtw89`, `rtw88`).
- **Network Interface Controllers (NICs)**: 10GbE and high-speed network interfaces.

### 6. Power Supply & Battery
- **System Batteries**: Charge percentage, charge/discharge status, battery cell temperature, pack voltage, current, and wattage.
- **AC Adapter**: Real-time power supply online/offline status.

### 7. Cooling Fans & Tachometers
- **Fan Tachometers**: Real-time RPM telemetry across CPU, GPU, and chassis fans.
- **PWM Controllers**: Direct kernel hwmon PWM fan control attributes (`pwm1`–`pwmN`).

---

## Hardware Temperature Control Suite

Thermals empowers users to actively regulate and manage temperatures via software:

### 1. Target Temperature Limiter (Smart Thermostat)
- Set your preferred maximum hardware temperature ceiling (e.g., 65°C, 70°C, 75°C, 80°C).
- When enabled, Thermals acts as a closed-loop supervisor:
  - **Over Target**: Automatically applies conservative cooling (downshifts to Quiet profile, lowers CPU EPP, ramps fans).
  - **Under Target**: Automatically relaxes back to standard balanced operation with built-in 5°C hysteresis.
- Provides real-time visual feedback on whether temperatures are within the target or actively being cooled.

### 2. Platform Thermal Profiles
- Instant switching between Linux ACPI platform profiles:
  - **Quiet / Power Saver**: Limits power envelopes and fan acoustics for whisper-quiet and cool operation.
  - **Balanced**: Standard dynamic firmware thermal and acoustic curve.
  - **Performance / Boost**: Maximizes processor power limits and cooling throughput.
- Seamless backend integration: `power-profiles-daemon` (`powerprofilesctl`), direct sysfs, ASUS WMI `throttle_thermal_policy`, privileged helper, or Polkit (`pkexec`).

### 3. CPU Thermal Tuning (Turbo Boost & EPP)
- **CPU Turbo Boost Switch**: Disabling CPU Turbo Boost drops peak CPU temperatures by **15°C to 20°C** under load with minimal impact on everyday responsiveness.
- **Energy-Performance Preference (EPP)**: One-click selection of `Power Saver`, `Balanced`, or `Performance` CPU energy preferences.

### 4. Cooling Presets & Fan Control
- **❄️ Cooler Boost / Max Cooling**: Instant one-click action that engages 100% fan speeds and performance cooling to rapidly expel heat from hot hardware.
- **🤫 Silent Cooling**: Immediately silences fans and engages aggressive power capping.
- **Direct PWM Fan Sliders**: Adjust manual fan speeds from 0% to 100% on systems with controllable PWM hardware.

---

## Dual GUI & Terminal Interfaces

### 1. Graphical Interface (GUI)
- **Tab 1: 🖥️ All Hardware Telemetry**: Summary cards across 6 hardware classes + detailed inventory table displaying every detected sensor, live readings, kernel limits, and color-coded health badges.
- **Tab 2: 🎛️ Temperature Controls**: Target Temperature Limiter, platform profiles, CPU Turbo Boost toggle, CPU power preferences, and Cooler Boost.
- **Tab 3: 📈 History & Automation**: Rolling temperature trends chart, workload-aware auto switching, and desktop alert notifications.

### 2. Terminal & CLI Controls (Headless / SSH / Scripts)
```bash
# Print formatted terminal status of all hardware and controls
thermals --status

# Output machine-readable JSON telemetry (Polybar, Waybar, scripts)
thermals --json

# List all detected hardware sensors and thresholds
thermals --list-hardware

# Run interactive real-time terminal monitor
thermals --monitor

# Set target temperature limit in Celsius (or 'off' to disable)
thermals --set-target-temp 70
thermals --set-target-temp off

# Switch platform thermal profile
thermals --set-profile quiet
thermals --set-profile balanced
thermals --set-profile performance

# Toggle CPU Turbo Boost (drops CPU temps immediately)
thermals --set-boost off
thermals --set-boost on

# Set CPU power preference
thermals --set-epp power
thermals --set-epp balance_performance
thermals --set-epp performance

# One-click instant max cooling (Cooler Boost)
thermals --cooler-boost

# One-click silent cooling
thermals --silent
```

Example JSON output (`thermals --json`):
```json
{
  "system": {
    "vendor": "ASUS",
    "model": "Vivobook Go E1504FA",
    "cpu": "AMD Ryzen 5 7520U with Radeon Graphics",
    "os": "Ubuntu 24.04 LTS",
    "summary": "ASUS Vivobook Go E1504FA • AMD Ryzen 5 7520U with Radeon Graphics"
  },
  "temperatures": {
    "cpu": 58.5,
    "gpu": 52.0,
    "nvme": 39.0,
    "acpi": 55.0
  },
  "all_hardware": [
    {
      "id": "hwmon4_temp1_input",
      "category": "cpu",
      "device_name": "AMD Ryzen 5 7520U with Radeon Graphics",
      "sensor_name": "Tctl",
      "value": 58.5,
      "unit": "°C",
      "status": "normal"
    },
    {
      "id": "hwmon7_temp1_input",
      "category": "gpu",
      "device_name": "GPU (AMDGPU)",
      "sensor_name": "edge",
      "value": 52.0,
      "unit": "°C",
      "status": "normal"
    }
  ],
  "controls": {
    "active_profile": "balanced",
    "available_profiles": ["quiet", "balanced", "performance"],
    "target_temp_enabled": true,
    "target_temp_c": 70.0,
    "cpu_boost": true,
    "cpu_epp": "balance_performance"
  }
}
```

---

## Installation

### Option 1: Universal Installer (Recommended for All Distros)

```bash
git clone https://github.com/Yash55-max/asus-thermals-control.git
cd asus-thermals-control
bash install.sh
```

**Installer Options:**
- `bash install.sh --install-deps` : Automatically installs dependencies using your distro's package manager (`apt`, `dnf`, `pacman`, `zypper`, `apk`, `xbps`).
- `bash install.sh --user` : Installs to `~/.local` (default, no root password required).
- `bash install.sh --system` : Installs system-wide to `/usr/local` (requires sudo).
- `bash install.sh --with-helper` : Installs the privileged thermal helper for passwordless switching.
- `bash install.sh --check` : Prints a diagnostic report of your hardware sensors and control interfaces.

---

### Option 2: Distribution Packages

#### Debian / Ubuntu / Kali / Linux Mint (.deb)
```bash
sudo apt install ./dist/asus-thermals-control_1.2.0_all.deb
```
Or build from source:
```bash
bash package.sh
sudo apt install ./dist/asus-thermals-control_1.2.0_all.deb
```

#### Fedora / RHEL / openSUSE (RPM)
```bash
rpmbuild -bb asus-thermals-control.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/asus-thermals-control-1.2.0-1*.noarch.rpm
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

## Privileged Helper Setup

Desktop thermal profile changes via `power-profiles-daemon` execute without password prompts out of the box.

To enable passwordless direct sysfs profile switching, fan PWM adjustments, and CPU boost regulation for background daemons or automated switching, run:

```bash
bash install-helper.sh
```

This installs `/usr/local/sbin/asus-thermal-profile` and configures a strictly scoped `/etc/sudoers.d/asus-thermal-control` rule allowing passwordless hardware management.

---

## Configuration

Settings are stored in standard JSON format:
```
~/.config/asus-thermal-control-center/config.json
```

| Key | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `target_temp_enabled` | boolean | `false` | Enables active target temperature ceiling regulation. |
| `target_temp_c` | float | `75.0` | Maximum target temperature in Celsius. |
| `boost_enabled` | boolean | `true` | Preferred CPU Turbo Boost status. |
| `alerts_enabled` | boolean | `true` | Enables desktop notification alerts. |
| `cpu_alert_c` | float | `85.0` | CPU temperature threshold for alert triggers. |
| `gpu_alert_c` | float | `85.0` | GPU temperature threshold for alert triggers. |
| `auto_enabled` | boolean | `false` | Workload-aware automatic profile switching. |
| `auto_performance_load` | float | `70.0` | CPU load percentage required to step up to Performance. |
| `auto_quiet_load` | float | `20.0` | CPU load threshold for stepping down to Quiet. |

---

## License

This project is licensed under the [MIT License](LICENSE).
