#!/usr/bin/env python3
"""Thermals - Universal Linux Hardware Telemetry & Thermal Control Center.

Supports all Linux environments:
- Comprehensive telemetry: CPU, GPU, Storage (NVMe/SATA), Motherboard/ACPI, Wireless, Battery, Fans
- Hardware temperature controls:
  * Platform thermal profiles (Quiet / Power-Saver, Balanced, Performance)
  * Target temperature limiter (active closed-loop thermal regulation)
  * CPU Turbo Boost toggle and Energy-Performance Preference (EPP)
  * Fan PWM control and Cooler Boost (maximum airflow cooling)
- Dual graphical (Tkinter GUI) and terminal (CLI monitor, status, JSON, one-shot) interfaces
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Optional Tkinter import for headless / non-graphical Linux environments
try:
    import tkinter as tk
    from tkinter import font as tkfont
    from tkinter import messagebox, ttk
    TKINTER_AVAILABLE = True
except ImportError:
    tk = None  # type: ignore[assignment]
    tkfont = None  # type: ignore[assignment]
    messagebox = None  # type: ignore[assignment]
    ttk = None  # type: ignore[assignment]
    TKINTER_AVAILABLE = False

APP_NAME = "Thermals"
__version__ = "1.2.0"

# Filesystem paths
PROFILE_STD_PATH = Path("/sys/firmware/acpi/platform_profile")
PROFILE_STD_CHOICES = Path("/sys/firmware/acpi/platform_profile_choices")
HW_MON_PATH = Path("/sys/class/hwmon")
THERMAL_CLASS_PATH = Path("/sys/class/thermal")
POWER_SUPPLY_PATH = Path("/sys/class/power_supply")
HELPER_PATH = Path("/usr/local/sbin/asus-thermal-profile")
CONFIG_DIR = Path.home() / ".config" / "asus-thermal-control-center"
CONFIG_PATH = CONFIG_DIR / "config.json"

# CPU Control paths
AMD_BOOST_PATH = Path("/sys/devices/system/cpu/cpufreq/boost")
INTEL_NO_TURBO_PATH = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
ASUS_THROTTLE_PATH = Path("/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy")

POLL_MS = 2000
HISTORY_LIMIT = 60
AUTO_CONFIRM_SAMPLES = 3
AUTO_MIN_DWELL_SECONDS = 60
ALERT_RESET_HYSTERESIS = 5.0
TARGET_TEMP_HYSTERESIS = 5.0

# UI Theme Colors
BG, PANEL, PANEL_ALT, BORDER = "#0e131b", "#161e29", "#1f2a3a", "#29384d"
FG, MUTED, ACCENT = "#e6edf3", "#8b9bb4", "#58a6ff"
GREEN, AMBER, RED = "#3fb950", "#d29922", "#f85149"
CYAN, PURPLE = "#38d9a9", "#bc8cff"

DEFAULT_CONFIG: dict[str, Any] = {
    "cpu_alert_c": 85.0,
    "gpu_alert_c": 85.0,
    "alerts_enabled": True,
    "auto_enabled": False,
    "auto_performance_load": 70.0,
    "auto_quiet_load": 20.0,
    "auto_cool_cpu_c": 58.0,
    "auto_hot_cpu_c": 75.0,
    "auto_critical_c": 85.0,
    "target_temp_enabled": False,
    "target_temp_c": 75.0,
    "boost_enabled": True,
    "preferred_profile": "balanced",
}

PROFILE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "quiet": ("Quiet", "Lower noise & heat (reduced power limit)"),
    "low-power": ("Low Power", "Energy saving cooling"),
    "power-saver": ("Power Saver", "Battery optimized cooling"),
    "cool": ("Cool", "Optimized cooling policy"),
    "balanced": ("Balanced", "Standard everyday dynamic cooling"),
    "balanced-performance": ("Balanced+", "Higher responsiveness"),
    "performance": ("Performance", "Max fan throughput & peak clocks"),
}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def get_system_info() -> dict[str, str]:
    """Dynamically detect host vendor, product model, accurate CPU model, and OS release."""
    vendor = ""
    model = ""
    cpu = ""

    # 1. System Vendor & Product from DMI / Sysfs
    for vpath in (
        Path("/sys/class/dmi/id/sys_vendor"),
        Path("/sys/devices/virtual/dmi/id/sys_vendor"),
    ):
        raw = read_text(vpath)
        if raw and raw not in {"System manufacturer", "Default string", "To Be Filled By O.E.M."}:
            vendor = raw
            break

    for mpath in (
        Path("/sys/class/dmi/id/product_name"),
        Path("/sys/devices/virtual/dmi/id/product_name"),
        Path("/sys/class/dmi/id/product_version"),
    ):
        raw = read_text(mpath)
        if raw and raw not in {"System Product Name", "Default string", "None"}:
            model = raw
            break

    if not model:
        raw_dt = read_text(Path("/proc/device-tree/model"))
        if raw_dt:
            model = raw_dt.replace("\x00", "").strip()

    v_low = vendor.lower()
    if "asus" in v_low:
        vendor_clean = "ASUS"
    elif "lenovo" in v_low:
        vendor_clean = "Lenovo"
    elif "dell" in v_low:
        vendor_clean = "Dell"
    elif "hp" in v_low or "hewlett" in v_low:
        vendor_clean = "HP"
    elif "apple" in v_low:
        vendor_clean = "Apple"
    elif "framework" in v_low:
        vendor_clean = "Framework"
    elif "system76" in v_low:
        vendor_clean = "System76"
    elif "msi" in v_low or "micro-star" in v_low:
        vendor_clean = "MSI"
    else:
        vendor_clean = vendor

    # 2. Accurate CPU Model detection (avoids processor: 0 bug)
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith("model name") or line.lower().startswith("hardware"):
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip():
                        cpu = parts[1].strip()
                        break
    except OSError:
        pass

    if not cpu:
        cpu = platform.processor() or platform.machine() or "Generic CPU"

    cpu = cpu.replace("(R)", "").replace("(TM)", "").replace(" CPU", "")
    cpu = re.sub(r"\s*@\s*[\d\.]+\s*GHz", "", cpu)
    cpu = " ".join(cpu.split())

    # 3. OS Distribution
    os_name = "Linux"
    os_release = Path("/etc/os-release")
    if os_release.exists():
        raw_os = read_text(os_release) or ""
        for line in raw_os.splitlines():
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"\'')
                break

    sys_prefix = f"{vendor_clean} {model}".strip() if (vendor_clean or model) else f"{platform.system()} ({platform.machine()})"
    summary = f"{sys_prefix}  •  {cpu}"
    return {
        "vendor": vendor_clean,
        "model": model,
        "cpu": cpu,
        "os": os_name,
        "arch": platform.machine(),
        "summary": summary,
    }


def load_config() -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            for key in config:
                if key in stored:
                    config[key] = stored[key]
    except (OSError, json.JSONDecodeError):
        pass
    return config


def save_config(config: dict[str, Any]) -> bool:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


# ============================================================================
# Comprehensive Universal Hardware Telemetry
# ============================================================================

@dataclass
class HardwareSensor:
    id: str
    category: str  # "cpu", "gpu", "storage", "motherboard", "network", "battery", "fan"
    device_name: str
    sensor_name: str
    chip: str
    sensor_type: str  # "temperature", "fan", "battery", "power", "clock", "voltage"
    value: float
    unit: str  # "°C", "RPM", "%", "W", "MHz", "V"
    crit_temp: float | None = None
    max_temp: float | None = None
    status: str = "normal"  # "normal", "warm", "hot", "critical", "idle"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_all_hardware(cpu_name: str = "CPU") -> list[HardwareSensor]:
    """Detect all types of hardware across the host system: CPU, GPU, Storage, Motherboard, Wireless, Battery, Fans."""
    sensors: list[HardwareSensor] = []
    seen_ids: set[str] = set()

    # 1. Sysfs hwmon enumeration (direct kernel hardware monitoring subsystem)
    if HW_MON_PATH.exists():
        for hw in sorted(HW_MON_PATH.glob("hwmon*")):
            chip_raw = read_text(hw / "name") or "generic"
            chip_l = chip_raw.lower()

            for inp in sorted(hw.glob("*_input")):
                base = inp.name[:-6]  # strip '_input'
                raw_val = read_text(inp)
                if not raw_val:
                    continue
                try:
                    val = float(raw_val)
                except ValueError:
                    continue

                label = read_text(hw / f"{base}_label") or ""
                crit_val: float | None = None
                crit_raw = read_text(hw / f"{base}_crit")
                if crit_raw:
                    try:
                        c_num = float(crit_raw)
                        crit_val = round(c_num / 1000.0 if c_num > 1000.0 else c_num, 1)
                    except ValueError:
                        pass

                max_val: float | None = None
                max_raw = read_text(hw / f"{base}_max")
                if max_raw:
                    try:
                        m_num = float(max_raw)
                        m_cand = m_num / 1000.0 if m_num > 1000.0 else m_num
                        # Ignore ridiculous placeholder values like 65261°C
                        if 0.0 < m_cand < 150.0:
                            max_val = round(m_cand, 1)
                    except ValueError:
                        pass

                sensor_id = f"{hw.name}_{inp.name}"
                if sensor_id in seen_ids:
                    continue
                seen_ids.add(sensor_id)

                # A. Temperature Sensors
                if inp.name.startswith("temp"):
                    temp_c = val / 1000.0 if val > 1000.0 else val
                    if not (-20.0 <= temp_c <= 145.0):
                        continue

                    category = "motherboard"
                    device_name = f"Motherboard ({chip_raw})"
                    s_name = label or f"Temp {base[4:] or '1'}"

                    label_l = label.lower()

                    # Classify hardware category
                    if any(k in chip_l for k in (
                        "coretemp", "k10temp", "zenpower", "amd_energy", "cpu_thermal",
                        "soc_thermal", "bcm2835", "intel_rapl", "rockchip", "sunxi", "macsmc", "apple_soc"
                    )) or any(k in label_l for k in ("tctl", "tdie", "package", "core", "tccd", "cpu")):
                        category = "cpu"
                        device_name = cpu_name
                        s_name = label or "CPU Temperature"
                    elif any(k in chip_l for k in (
                        "amdgpu", "radeon", "nouveau", "i915", "xe", "panfrost", "mali", "nvidia"
                    )) or any(k in label_l for k in ("edge", "junction", "gpu", "gt", "vram")):
                        category = "gpu"
                        device_name = f"GPU ({chip_raw.upper()})"
                        s_name = label or "GPU Temperature"
                    elif any(k in chip_l for k in ("nvme", "drivetemp")) or any(k in label_l for k in ("composite", "drive")):
                        category = "storage"
                        device_name = "Storage Drive (NVMe/SSD)" if "nvme" in chip_l else f"Storage ({chip_raw})"
                        s_name = label or "Storage Temp"
                    elif any(k in chip_l for k in ("mt79", "iwl", "ath1", "rtw", "wlan", "wifi", "phy")):
                        category = "network"
                        device_name = f"Wireless Network ({chip_raw})"
                        s_name = label or "Radio Temperature"
                    elif any(k in chip_l for k in ("bat", "power_supply", "ac0")):
                        category = "battery"
                        device_name = f"Battery ({chip_raw})"
                        s_name = label or "Battery Temp"
                    elif any(k in chip_l for k in ("acpitz", "asus", "thinkpad", "dell", "hp", "it87", "nct", "w83")):
                        category = "motherboard"
                        device_name = f"Motherboard / System ({chip_raw})"
                        s_name = label or "ACPI Ambient"

                    status = "normal"
                    if crit_val and temp_c >= crit_val:
                        status = "critical"
                    elif max_val and temp_c >= max_val:
                        status = "hot"
                    elif temp_c >= 85.0:
                        status = "critical"
                    elif temp_c >= 75.0:
                        status = "hot"
                    elif temp_c >= 65.0:
                        status = "warm"

                    sensors.append(HardwareSensor(
                        id=sensor_id,
                        category=category,
                        device_name=device_name,
                        sensor_name=s_name,
                        chip=chip_raw,
                        sensor_type="temperature",
                        value=round(temp_c, 1),
                        unit="°C",
                        crit_temp=crit_val,
                        max_temp=max_val,
                        status=status,
                    ))

                # B. Fan Tachometers
                elif inp.name.startswith("fan"):
                    rpm = int(val)
                    category = "fan"
                    device_name = f"Cooling Fan ({chip_raw})"
                    s_name = label or f"Fan {base[3:] or '1'}"
                    sensors.append(HardwareSensor(
                        id=sensor_id,
                        category=category,
                        device_name=device_name,
                        sensor_name=s_name,
                        chip=chip_raw,
                        sensor_type="fan",
                        value=float(rpm),
                        unit="RPM",
                        status="normal" if rpm > 0 else "idle",
                    ))

    # 2. Kernel Thermal Zones (/sys/class/thermal/thermal_zone*)
    if THERMAL_CLASS_PATH.exists():
        for tz in sorted(THERMAL_CLASS_PATH.glob("thermal_zone*")):
            ztype = (read_text(tz / "type") or "").lower()
            temp_raw = read_text(tz / "temp")
            if not temp_raw:
                continue
            try:
                tval = float(temp_raw)
                temp_c = tval / 1000.0 if tval > 1000.0 else tval
            except ValueError:
                continue
            if not (0.0 < temp_c < 145.0):
                continue

            sensor_id = f"tz_{tz.name}"
            if sensor_id in seen_ids:
                continue

            # Add if category not yet covered or provides extra motherboard telemetry
            if "acpi" in ztype and not any(s.category == "motherboard" for s in sensors):
                seen_ids.add(sensor_id)
                sensors.append(HardwareSensor(
                    id=sensor_id,
                    category="motherboard",
                    device_name="ACPI Thermal Zone",
                    sensor_name=ztype.upper(),
                    chip="acpi",
                    sensor_type="temperature",
                    value=round(temp_c, 1),
                    unit="°C",
                    status="normal" if temp_c < 75.0 else ("warm" if temp_c < 85.0 else "hot"),
                ))
            elif any(k in ztype for k in ("x86_pkg", "cpu", "soc")) and not any(s.category == "cpu" for s in sensors):
                seen_ids.add(sensor_id)
                sensors.append(HardwareSensor(
                    id=sensor_id,
                    category="cpu",
                    device_name=cpu_name,
                    sensor_name=ztype.upper(),
                    chip="thermal_zone",
                    sensor_type="temperature",
                    value=round(temp_c, 1),
                    unit="°C",
                    status="normal" if temp_c < 75.0 else ("warm" if temp_c < 85.0 else "hot"),
                ))

    # 3. Dedicated NVIDIA GPU Driver Telemetry (via nvidia-smi)
    if shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,power.draw,fan.speed", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        idx, gpu_name, gpu_temp_raw = parts[0], parts[1], parts[2]
                        try:
                            gpu_temp = float(gpu_temp_raw)
                            sensor_id = f"nvidia_gpu_{idx}"
                            if sensor_id not in seen_ids:
                                seen_ids.add(sensor_id)
                                extra_info: dict[str, Any] = {}
                                if len(parts) >= 4 and parts[3] and parts[3] != "[N/A]":
                                    try:
                                        extra_info["power_watts"] = float(parts[3])
                                    except ValueError:
                                        pass
                                if len(parts) >= 5 and parts[4] and parts[4] != "[N/A]":
                                    try:
                                        extra_info["fan_speed_pct"] = float(parts[4])
                                    except ValueError:
                                        pass

                                sensors.append(HardwareSensor(
                                    id=sensor_id,
                                    category="gpu",
                                    device_name=gpu_name,
                                    sensor_name="GPU Core Temp",
                                    chip="nvidia",
                                    sensor_type="temperature",
                                    value=round(gpu_temp, 1),
                                    unit="°C",
                                    status="normal" if gpu_temp < 75.0 else ("warm" if gpu_temp < 85.0 else "hot"),
                                    extra=extra_info,
                                ))
                        except ValueError:
                            pass
        except (subprocess.SubprocessError, OSError):
            pass

    # 4. Power Supply & Battery Telemetry
    if POWER_SUPPLY_PATH.exists():
        for ps in sorted(POWER_SUPPLY_PATH.glob("BAT*")):
            cap_raw = read_text(ps / "capacity")
            status = read_text(ps / "status") or "Battery"
            temp_raw = read_text(ps / "temp")
            volt_raw = read_text(ps / "voltage_now")
            curr_raw = read_text(ps / "current_now")

            if cap_raw and cap_raw.isdigit():
                cap = float(cap_raw)
                sensor_id = f"bat_{ps.name}_cap"
                if sensor_id not in seen_ids:
                    seen_ids.add(sensor_id)
                    extra: dict[str, Any] = {"status": status}
                    if temp_raw and temp_raw.isdigit():
                        extra["temp_c"] = float(temp_raw) / 10.0
                    if volt_raw and volt_raw.isdigit():
                        extra["voltage_v"] = round(float(volt_raw) / 1000000.0, 2)
                    if curr_raw and curr_raw.isdigit():
                        extra["current_a"] = round(float(curr_raw) / 1000000.0, 2)

                    sensors.append(HardwareSensor(
                        id=sensor_id,
                        category="battery",
                        device_name=f"System Battery ({ps.name})",
                        sensor_name=f"Charge ({status})",
                        chip=ps.name,
                        sensor_type="battery",
                        value=cap,
                        unit="%",
                        status="normal",
                        extra=extra,
                    ))

    return sensors


def collect_sensors() -> dict[str, float]:
    """Summary sensor collection (maintains backward compatibility for CPU/GPU/NVMe/ACPI dict)."""
    hardware = detect_all_hardware()
    summary: dict[str, float] = {}

    for sensor in hardware:
        if sensor.sensor_type != "temperature":
            continue
        cat = sensor.category
        if cat == "cpu":
            if any(k in sensor.sensor_name.lower() for k in ("tctl", "tdie", "package")):
                summary["cpu"] = sensor.value
            else:
                summary.setdefault("cpu", sensor.value)
        elif cat == "gpu":
            if any(k in sensor.sensor_name.lower() for k in ("edge", "core")):
                summary["gpu"] = sensor.value
            else:
                summary.setdefault("gpu", sensor.value)
        elif cat == "storage":
            if "composite" in sensor.sensor_name.lower():
                summary["nvme"] = sensor.value
            else:
                summary.setdefault("nvme", sensor.value)
        elif cat == "motherboard":
            summary.setdefault("acpi", sensor.value)

    return summary


def fan_capabilities() -> tuple[list[tuple[str, int]], list[str]]:
    """Return detected cooling fan tachometers and controllable PWM sysfs files."""
    rpm_data: list[tuple[str, int]] = []
    pwm_files: list[str] = []

    if HW_MON_PATH.exists():
        for hwmon in HW_MON_PATH.glob("hwmon*"):
            hw_name = read_text(hwmon / "name") or "Fan"
            for path in hwmon.glob("fan*_input"):
                raw = read_text(path)
                speed = int(raw) if raw and raw.isdigit() else 0
                label = read_text(hwmon / path.name.replace("_input", "_label")) or f"{hw_name} {path.name}"
                rpm_data.append((label, speed))
            for path in hwmon.glob("pwm[0-9]*"):
                if not path.name.endswith("_enable"):
                    pwm_files.append(str(path))

    return rpm_data, pwm_files


def cpu_load_percent() -> tuple[float, float] | None:
    """Read aggregate CPU utilization from /proc/stat; falls back to loadavg if restricted."""
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return float(total), float(idle)
    except (OSError, ValueError, IndexError):
        try:
            if hasattr(os, "getloadavg"):
                avg1 = os.getloadavg()[0]
                cores = os.cpu_count() or 1
                estimated_load = min(100.0, max(0.0, (avg1 / cores) * 100.0))
                return 100.0, 100.0 - estimated_load
        except Exception:
            pass
        return None


# ============================================================================
# Thermal & Hardware Control Manager
# ============================================================================

class ThermalControlManager:
    """Unified hardware temperature and thermal control manager."""

    # -------------------------------------------------------------------------
    # Platform Profile Backends
    # -------------------------------------------------------------------------

    @staticmethod
    def find_sysfs_paths() -> tuple[Path | None, Path | None]:
        if PROFILE_STD_PATH.exists():
            choices = PROFILE_STD_CHOICES if PROFILE_STD_CHOICES.exists() else None
            return PROFILE_STD_PATH, choices

        # Linux 6.12+ class devices (/sys/class/platform_profile/*/profile)
        class_dir = Path("/sys/class/platform_profile")
        if class_dir.exists():
            for cand in class_dir.glob("*/profile"):
                if cand.exists():
                    choices = cand.parent / "choices"
                    return cand, choices if choices.exists() else None

        return None, None

    @classmethod
    def get_available_profiles(cls) -> list[str]:
        prof_path, choices_path = cls.find_sysfs_paths()
        if choices_path and choices_path.exists():
            raw = read_text(choices_path) or ""
            choices = [p for p in raw.split() if p in PROFILE_DESCRIPTIONS]
            if choices:
                return choices

        if shutil.which("powerprofilesctl"):
            try:
                res = subprocess.run(["powerprofilesctl", "list"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0:
                    found: list[str] = []
                    for line in res.stdout.splitlines():
                        line = line.strip()
                        if line.startswith(("*", " ")):
                            parts = line.lstrip("* ").split(":", 1)
                            name = parts[0].strip()
                            if name in PROFILE_DESCRIPTIONS:
                                found.append(name)
                    if found:
                        return found
            except (subprocess.SubprocessError, OSError):
                pass

        if ASUS_THROTTLE_PATH.exists():
            return ["quiet", "balanced", "performance"]

        return []

    @classmethod
    def get_current_profile(cls) -> str | None:
        prof_path, _ = cls.find_sysfs_paths()
        if prof_path and prof_path.exists():
            val = read_text(prof_path)
            if val:
                return val

        if shutil.which("powerprofilesctl"):
            try:
                res = subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                pass

        if ASUS_THROTTLE_PATH.exists():
            mode = read_text(ASUS_THROTTLE_PATH)
            if mode == "1":
                return "performance"
            elif mode == "2":
                return "quiet"
            elif mode == "0":
                return "balanced"

        return None

    @classmethod
    def set_profile(cls, profile: str, automatic: bool = False) -> tuple[bool, str]:
        """Apply a thermal profile using powerprofilesctl, direct sysfs, sudo helper, or pkexec."""
        prof_path, choices_path = cls.find_sysfs_paths()

        # 1. Desktop powerprofilesctl (works without password for logged-in desktop user)
        if shutil.which("powerprofilesctl"):
            try:
                daemon_prof = profile
                if profile in {"quiet", "low-power", "cool"}:
                    daemon_prof = "power-saver"
                elif profile in {"performance", "balanced-performance"}:
                    daemon_prof = "performance"
                elif profile == "balanced":
                    daemon_prof = "balanced"

                res = subprocess.run(
                    ["powerprofilesctl", "set", daemon_prof],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0:
                    return True, f"Thermal profile switched to {profile} (via power-profiles-daemon)"
            except (subprocess.SubprocessError, OSError):
                pass

        # 2. Direct sysfs write (if running as root or node is writable)
        if prof_path and os.access(prof_path, os.W_OK):
            try:
                prof_path.write_text(profile + "\n", encoding="utf-8")
                # Also sync Asus throttle policy if present
                if ASUS_THROTTLE_PATH.exists() and os.access(ASUS_THROTTLE_PATH, os.W_OK):
                    val = "1" if profile in ("performance", "balanced-performance") else ("2" if profile in ("quiet", "low-power", "cool") else "0")
                    ASUS_THROTTLE_PATH.write_text(val + "\n", encoding="utf-8")
                return True, f"Set active profile to {profile}"
            except OSError as exc:
                return False, f"Direct write failed: {exc}"

        # 3. Privileged helper script via sudo -n
        if HELPER_PATH.exists() and shutil.which("sudo"):
            try:
                result = subprocess.run(
                    ["sudo", "-n", str(HELPER_PATH), profile],
                    capture_output=True, text=True, timeout=8,
                )
                if result.returncode == 0:
                    return True, (result.stdout or f"Switched to {profile}").strip()
                err = (result.stderr or result.stdout).strip()
                if "password is required" in err.lower() and automatic:
                    return False, "Helper requires password. Run install-helper.sh once."
            except (subprocess.SubprocessError, OSError) as exc:
                return False, str(exc)

        # 4. Polkit graphical prompt for manual changes
        if not automatic and prof_path and shutil.which("pkexec"):
            try:
                res = subprocess.run(
                    ["pkexec", "/usr/bin/tee", str(prof_path)],
                    input=profile + "\n", capture_output=True, text=True, timeout=30,
                )
                if res.returncode == 0:
                    return True, f"Thermal profile switched to {profile}"
                return False, (res.stderr or res.stdout).strip()
            except (subprocess.SubprocessError, OSError) as exc:
                return False, str(exc)

        return False, "Profile control requires privileges. Run: bash install-helper.sh"

    # -------------------------------------------------------------------------
    # CPU Thermal Controls (Turbo Boost & EPP)
    # -------------------------------------------------------------------------

    @classmethod
    def get_cpu_boost(cls) -> bool | None:
        """Check whether CPU Turbo Boost is currently enabled."""
        if AMD_BOOST_PATH.exists():
            val = read_text(AMD_BOOST_PATH)
            return val == "1"
        if INTEL_NO_TURBO_PATH.exists():
            val = read_text(INTEL_NO_TURBO_PATH)
            return val == "0"
        return None

    @classmethod
    def set_cpu_boost(cls, enabled: bool, automatic: bool = False) -> tuple[bool, str]:
        """Enable or disable CPU Turbo Boost to immediately regulate thermals."""
        # AMD boost (1 = enabled, 0 = disabled)
        target_path = AMD_BOOST_PATH if AMD_BOOST_PATH.exists() else (INTEL_NO_TURBO_PATH if INTEL_NO_TURBO_PATH.exists() else None)
        if not target_path:
            return False, "CPU Boost interface not exposed on this CPU architecture."

        val_str = ("1" if enabled else "0") if target_path == AMD_BOOST_PATH else ("0" if enabled else "1")

        # 1. Direct write
        if os.access(target_path, os.W_OK):
            try:
                target_path.write_text(val_str + "\n", encoding="utf-8")
                return True, f"CPU Turbo Boost {'enabled' if enabled else 'disabled'}."
            except OSError as exc:
                return False, f"Write failed: {exc}"

        # 2. Privileged helper via sudo -n
        if HELPER_PATH.exists() and shutil.which("sudo"):
            try:
                cmd_arg = "on" if enabled else "off"
                res = subprocess.run(
                    ["sudo", "-n", str(HELPER_PATH), "boost", cmd_arg],
                    capture_output=True, text=True, timeout=5,
                )
                if res.returncode == 0:
                    return True, f"CPU Turbo Boost {'enabled' if enabled else 'disabled'}."
            except (subprocess.SubprocessError, OSError):
                pass

        # 3. pkexec fallback for manual desktop interactions
        if not automatic and shutil.which("pkexec"):
            try:
                res = subprocess.run(
                    ["pkexec", "/usr/bin/tee", str(target_path)],
                    input=val_str + "\n", capture_output=True, text=True, timeout=20,
                )
                if res.returncode == 0:
                    return True, f"CPU Turbo Boost {'enabled' if enabled else 'disabled'}."
            except (subprocess.SubprocessError, OSError):
                pass

        return False, "Controlling CPU boost requires root privileges. Run: bash install-helper.sh"

    @classmethod
    def get_cpu_epp(cls) -> str | None:
        """Get CPU Energy-Performance Preference (EPP)."""
        epp_path = Path("/sys/devices/system/cpu/cpufreq/policy0/energy_performance_preference")
        if epp_path.exists():
            return read_text(epp_path)
        gov_path = Path("/sys/devices/system/cpu/cpufreq/policy0/scaling_governor")
        if gov_path.exists():
            return read_text(gov_path)
        return None

    @classmethod
    def set_cpu_epp(cls, preference: str) -> tuple[bool, str]:
        """Set CPU Energy-Performance Preference (power, balance_power, balance_performance, performance)."""
        if shutil.which("powerprofilesctl"):
            daemon_map = {
                "power": "power-saver",
                "balance_power": "power-saver",
                "balance_performance": "balanced",
                "performance": "performance",
            }
            if preference in daemon_map:
                try:
                    res = subprocess.run(
                        ["powerprofilesctl", "set", daemon_map[preference]],
                        capture_output=True, text=True, timeout=3,
                    )
                    if res.returncode == 0:
                        return True, f"Set CPU power preference to {preference}"
                except (subprocess.SubprocessError, OSError):
                    pass

        # Direct write to all policies
        policies = list(Path("/sys/devices/system/cpu/cpufreq").glob("policy*/energy_performance_preference"))
        if policies and all(os.access(p, os.W_OK) for p in policies):
            try:
                for p in policies:
                    p.write_text(preference + "\n", encoding="utf-8")
                return True, f"Applied CPU EPP {preference} to all cores."
            except OSError as exc:
                return False, str(exc)

        return False, "Setting CPU EPP requires privileges or power-profiles-daemon."

    # -------------------------------------------------------------------------
    # Fan & Cooling Controls (Cooler Boost & PWM)
    # -------------------------------------------------------------------------

    @classmethod
    def set_cooler_boost(cls) -> tuple[bool, str]:
        """Activate maximum cooling (Cooler Boost: Performance profile + max fans)."""
        # 1. Switch profile to performance
        cls.set_profile("performance", automatic=True)

        # 2. Set Asus throttle policy to 1 (overboost)
        if ASUS_THROTTLE_PATH.exists() and os.access(ASUS_THROTTLE_PATH, os.W_OK):
            try:
                ASUS_THROTTLE_PATH.write_text("1\n", encoding="utf-8")
            except OSError:
                pass

        # 3. Ramp all PWM fans to 255 (100%)
        _, pwm_files = fan_capabilities()
        for pwm in pwm_files:
            cls.set_fan_pwm(pwm, 255)

        return True, "Cooler Boost activated (aggressive fan curves and maximum cooling applied)."

    @classmethod
    def set_silent_cooling(cls) -> tuple[bool, str]:
        """Activate silent cooling (Quiet profile + reduced power envelopes)."""
        cls.set_profile("quiet", automatic=True)
        if ASUS_THROTTLE_PATH.exists() and os.access(ASUS_THROTTLE_PATH, os.W_OK):
            try:
                ASUS_THROTTLE_PATH.write_text("2\n", encoding="utf-8")
            except OSError:
                pass
        return True, "Silent cooling engaged (Quiet profile active, lower temperatures and noise)."

    @classmethod
    def set_fan_pwm(cls, pwm_path: str, value_or_auto: int | str) -> tuple[bool, str]:
        """Set fan speed directly on a hwmon PWM file (0-255 or 'auto')."""
        path = Path(pwm_path)
        enable_path = Path(f"{pwm_path}_enable")

        if value_or_auto == "auto":
            if enable_path.exists() and os.access(enable_path, os.W_OK):
                try:
                    enable_path.write_text("2\n", encoding="utf-8")
                    return True, "Fan set to automatic firmware thermal curve."
                except OSError as exc:
                    return False, str(exc)
        else:
            try:
                val = max(0, min(255, int(value_or_auto)))
                if enable_path.exists() and os.access(enable_path, os.W_OK):
                    enable_path.write_text("1\n", encoding="utf-8")
                if path.exists() and os.access(path, os.W_OK):
                    path.write_text(f"{val}\n", encoding="utf-8")
                    return True, f"Fan speed set to PWM {val} ({int(val / 255.0 * 100.0)}%)."
            except (ValueError, OSError) as exc:
                return False, str(exc)

        return False, "Direct PWM fan control requires root privileges."


# ============================================================================
# Desktop GUI (Tkinter)
# ============================================================================

def resolve_system_font() -> str:
    """Find the best matching modern UI font available on the host system."""
    if not TKINTER_AVAILABLE:
        return "sans-serif"
    preferred_fonts = (
        "Ubuntu", "DejaVu Sans", "Liberation Sans", "Cantarell",
        "Noto Sans", "Segoe UI", "Helvetica", "Arial"
    )
    try:
        available = set(tkfont.families())
        for name in preferred_fonts:
            if name in available:
                return name
    except Exception:
        pass
    return "TkDefaultFont"


class ThermalApp:
    """Universal Linux Hardware Telemetry and Temperature Control Center."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1000x820")
        self.root.minsize(880, 700)
        self.root.configure(bg=BG)

        self.sys_info = get_system_info()
        self.font_family = resolve_system_font()
        self.config = load_config()
        self.history: dict[str, list[float]] = {"cpu": [], "gpu": []}
        self.busy = False
        self.profile_change_busy = False
        self.profile_buttons: dict[str, ttk.Button] = {}
        self.alert_latched = {"cpu": False, "gpu": False}
        self.cpu_stat_previous: tuple[float, float] | None = None
        self.auto_candidate: str | None = None
        self.auto_candidate_count = 0
        self.last_auto_change = 0.0

        # Thermal Target Limiter state
        self.target_temp_regulating = False
        self.base_profile_before_target: str | None = None

        # GUI reactive variables
        self.auto_var = tk.BooleanVar(value=bool(self.config["auto_enabled"]))
        self.alerts_var = tk.BooleanVar(value=bool(self.config["alerts_enabled"]))
        self.target_temp_var = tk.BooleanVar(value=bool(self.config["target_temp_enabled"]))
        self.target_temp_val_var = tk.DoubleVar(value=float(self.config["target_temp_c"]))
        self.target_temp_label_var = tk.StringVar(value=f"{float(self.config['target_temp_c']):.0f} °C")

        self.cpu_alert_var = tk.StringVar(value=str(self.config["cpu_alert_c"]))
        self.gpu_alert_var = tk.StringVar(value=str(self.config["gpu_alert_c"]))
        self.cpu_load_var = tk.StringVar(value="CPU load: —")
        self.auto_status_var = tk.StringVar(value="Workload switching is disabled.")
        self.target_status_var = tk.StringVar(value="Target limiter: disabled.")
        self.status_var = tk.StringVar(value="Initializing hardware telemetry interfaces...")
        self.last_update_var = tk.StringVar(value="Waiting for first telemetry poll...")
        self.boost_status_var = tk.StringVar(value="CPU Turbo Boost: Detecting...")

        # Summary temp labels
        self.temp_vars = {
            "cpu": tk.StringVar(value="— °C"),
            "gpu": tk.StringVar(value="— °C"),
            "nvme": tk.StringVar(value="— °C"),
            "acpi": tk.StringVar(value="— °C"),
            "net": tk.StringVar(value="— °C"),
            "bat": tk.StringVar(value="— %"),
        }

        self._configure_style()
        self._build_ui()
        self._update_profile_controls()
        self._update_boost_display()
        try:
            init_hw = detect_all_hardware(self.sys_info["cpu"])
            init_temps = collect_sensors()
            init_fans, init_pwm = fan_capabilities()
            self._apply_refresh(init_hw, init_temps, init_fans, init_pwm, None)
        except Exception:
            pass
        self.root.after(POLL_MS, self.refresh)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        base_font = (self.font_family, 10)

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=PANEL, foreground=FG, font=base_font)
        style.configure("TButton", font=base_font, padding=(10, 8), background=PANEL_ALT, foreground=FG, borderwidth=0)
        style.map("TButton", background=[("active", "#2c3b4f"), ("disabled", "#19222e")])

        # Notebook tabs
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, font=(self.font_family, 10, "bold"),
                        padding=(18, 9), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL_ALT)], foreground=[("selected", FG)])

        # Profile buttons
        style.configure("Profile.TButton", font=(self.font_family, 10, "bold"), padding=(12, 10),
                        background=PANEL_ALT, foreground=FG)
        style.configure("Selected.Profile.TButton", font=(self.font_family, 10, "bold"), padding=(12, 10),
                        background="#1a4f38", foreground="#b6fcd5")

        # Treeview styling
        style.configure("Treeview", background=PANEL, foreground=FG, fieldbackground=PANEL,
                        font=(self.font_family, 9), rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL_ALT, foreground=FG,
                        font=(self.font_family, 9, "bold"), padding=(6, 5))
        style.map("Treeview.Heading", background=[("active", "#2b3b50")])

    def _section(self, parent: tk.Widget, title: str, subtitle: str | None = None) -> tk.Frame:
        frame = tk.Frame(parent, bg=PANEL, padx=16, pady=12, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x", padx=16, pady=6)
        tk.Label(frame, text=title, bg=PANEL, fg=FG, font=(self.font_family, 10, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(frame, text=subtitle, bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(anchor="w", pady=(2, 8))
        return frame

    def _build_ui(self) -> None:
        # Header
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=20, pady=(14, 6))

        left_hdr = tk.Frame(header, bg=BG)
        left_hdr.pack(side="left", fill="y")
        tk.Label(left_hdr, text="THERMAL CONTROL CENTER", bg=BG, fg=FG,
                 font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(left_hdr, text=f"{self.sys_info['summary']}  •  {self.sys_info['os']}",
                 bg=BG, fg=MUTED, font=(self.font_family, 9)).pack(anchor="w", pady=(2, 0))

        right_hdr = tk.Frame(header, bg=BG)
        right_hdr.pack(side="right")
        self.profile_badge = tk.Label(right_hdr, text="PROFILE: —", bg=PANEL_ALT, fg=ACCENT,
                                      font=(self.font_family, 9, "bold"), padx=12, pady=7)
        self.profile_badge.pack(side="right", padx=(6, 0))
        self.target_badge = tk.Label(right_hdr, text="TARGET: OFF", bg=PANEL_ALT, fg=MUTED,
                                     font=(self.font_family, 9, "bold"), padx=12, pady=7)
        self.target_badge.pack(side="right")

        # Tabbed Layout
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(4, 8))

        # Tab 1: All Hardware Telemetry
        self.tab_hardware = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_hardware, text="  🖥️  All Hardware Telemetry  ")
        self._build_tab_hardware()

        # Tab 2: Temperature & Cooling Controls
        self.tab_controls = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_controls, text="  🎛️  Temperature Controls  ")
        self._build_tab_controls()

        # Tab 3: History & Automation
        self.tab_automation = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_automation, text="  📈  History & Automation  ")
        self._build_tab_automation()

        # Footer
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=20, pady=(2, 10))
        self.capability_var = tk.StringVar(value="Detecting hardware sensors...")

        for var, color in [
            (self.capability_var, CYAN),
            (self.status_var, MUTED),
            (self.last_update_var, MUTED),
        ]:
            tk.Label(footer, textvariable=var, bg=BG, fg=color, font=(self.font_family, 8)).pack(anchor="w", pady=0)

    def _build_tab_hardware(self) -> None:
        # Top summary cards (6 hardware categories)
        cards = tk.Frame(self.tab_hardware, bg=BG)
        cards.pack(fill="x", padx=2, pady=(8, 10))

        specs = [
            ("cpu", "CPU PROCESSOR", GREEN),
            ("gpu", "GPU GRAPHICS", ACCENT),
            ("nvme", "STORAGE SSD", AMBER),
            ("acpi", "MOTHERBOARD", MUTED),
            ("net", "WIRELESS / NET", CYAN),
            ("bat", "BATTERY / PWR", PURPLE),
        ]
        for index, (key, title, color) in enumerate(specs):
            card = tk.Frame(cards, bg=PANEL, padx=10, pady=9, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=0, column=index, sticky="nsew", padx=3)
            cards.grid_columnconfigure(index, weight=1)
            tk.Label(card, text=title, bg=PANEL, fg=MUTED, font=(self.font_family, 7, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.temp_vars[key], bg=PANEL, fg=color,
                     font=(self.font_family, 16, "bold")).pack(anchor="w", pady=(4, 0))

        # Hardware Inventory Section
        inv_frame = tk.Frame(self.tab_hardware, bg=PANEL, padx=14, pady=10, highlightbackground=BORDER, highlightthickness=1)
        inv_frame.pack(fill="both", expand=True, padx=2, pady=(0, 4))

        tk.Label(inv_frame, text="DETECTED HARDWARE SENSORS & TELEMETRY", bg=PANEL, fg=FG,
                 font=(self.font_family, 10, "bold")).pack(anchor="w")
        tk.Label(inv_frame, text="Live readings across all hardware components with kernel warning and critical thermal limits.",
                 bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(anchor="w", pady=(1, 8))

        # Treeview table
        tree_container = tk.Frame(inv_frame, bg=PANEL)
        tree_container.pack(fill="both", expand=True)

        cols = ("category", "component", "sensor", "reading", "limits", "status")
        self.hw_tree = ttk.Treeview(tree_container, columns=cols, show="headings", height=10)
        self.hw_tree.heading("category", text="Category")
        self.hw_tree.heading("component", text="Hardware Component")
        self.hw_tree.heading("sensor", text="Sensor / Channel")
        self.hw_tree.heading("reading", text="Live Reading")
        self.hw_tree.heading("limits", text="Max / Crit Limits")
        self.hw_tree.heading("status", text="Health Status")

        self.hw_tree.column("category", width=110, anchor="w")
        self.hw_tree.column("component", width=220, anchor="w")
        self.hw_tree.column("sensor", width=140, anchor="w")
        self.hw_tree.column("reading", width=110, anchor="center")
        self.hw_tree.column("limits", width=140, anchor="center")
        self.hw_tree.column("status", width=100, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.hw_tree.yview)
        self.hw_tree.configure(yscrollcommand=tree_scroll.set)
        self.hw_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # Color tags for rows
        self.hw_tree.tag_configure("normal", foreground="#e6edf3")
        self.hw_tree.tag_configure("warm", foreground="#f4b860")
        self.hw_tree.tag_configure("hot", foreground="#ff7b72")
        self.hw_tree.tag_configure("critical", foreground="#ff4d4d")
        self.hw_tree.tag_configure("idle", foreground="#8b9bb4")

    def _build_tab_controls(self) -> None:
        # 1. Target Temperature Limiter (Smart Thermal Regulator)
        sec_target = self._section(
            self.tab_controls, "TARGET TEMPERATURE LIMITER (SMART THERMOSTAT)",
            "Maintain hardware within your desired thermal ceiling. Automatically downshifts power and engages cooling if temps spike."
        )
        row_target = tk.Frame(sec_target, bg=PANEL)
        row_target.pack(fill="x", pady=(2, 6))

        tk.Checkbutton(
            row_target, text="Maintain Target Temperature Limit",
            variable=self.target_temp_var, command=self.toggle_target_temp,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=(self.font_family, 9, "bold"),
        ).pack(side="left", padx=(0, 16))

        tk.Label(row_target, text="Max Temp Target:", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left")

        self.target_slider = ttk.Scale(
            row_target, from_=50.0, to=90.0, orient="horizontal",
            variable=self.target_temp_val_var, command=self._on_target_slider_change,
        )
        self.target_slider.pack(side="left", fill="x", expand=True, padx=12)

        tk.Label(row_target, textvariable=self.target_temp_label_var, bg=PANEL, fg=ACCENT,
                 font=(self.font_family, 10, "bold"), width=7).pack(side="left")

        tk.Label(sec_target, textvariable=self.target_status_var, bg=PANEL, fg=AMBER,
                 font=(self.font_family, 8)).pack(anchor="w")

        # 2. Platform Thermal Profiles
        sec_profile = self._section(
            self.tab_controls, "PLATFORM THERMAL PROFILES",
            "Firmware-managed thermal policies configured via Linux ACPI platform_profile and power-profiles-daemon."
        )
        self.profile_row = tk.Frame(sec_profile, bg=PANEL)
        self.profile_row.pack(fill="x", pady=(3, 2))

        available = ThermalControlManager.get_available_profiles()
        display_profiles = available or ["quiet", "balanced", "performance"]

        for key in display_profiles:
            title, desc = PROFILE_DESCRIPTIONS.get(key, (key.replace("-", " ").title(), "Operational profile"))
            btn = ttk.Button(
                self.profile_row,
                text=f"{title}\n{desc}",
                style="Profile.TButton",
                command=lambda value=key: self.set_profile(value, automatic=False),
            )
            btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self.profile_buttons[key] = btn

        # 3. CPU Thermal Tuning & Turbo Boost
        sec_cpu = self._section(
            self.tab_controls, "CPU THERMAL MANAGEMENT",
            "Disabling Turbo Boost can reduce peak CPU temperatures by 15°C–20°C with minimal impact on everyday workloads."
        )
        cpu_row = tk.Frame(sec_cpu, bg=PANEL)
        cpu_row.pack(fill="x", pady=2)

        self.btn_boost = ttk.Button(cpu_row, text="Toggle CPU Boost", command=self.toggle_cpu_boost)
        self.btn_boost.pack(side="left", padx=(0, 14))

        tk.Label(cpu_row, textvariable=self.boost_status_var, bg=PANEL, fg=FG,
                 font=(self.font_family, 9, "bold")).pack(side="left", padx=(0, 20))

        tk.Label(cpu_row, text="Power Preference:", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left", padx=(10, 6))
        for pref, label in (("power", "Power Saver"), ("balance_performance", "Balanced"), ("performance", "Performance")):
            ttk.Button(cpu_row, text=label, command=lambda p=pref: self.set_cpu_epp(p)).pack(side="left", padx=3)

        # 4. Cooling & Fans Control (Cooler Boost)
        sec_fan = self._section(
            self.tab_controls, "COOLING PRESETS & FAN CONTROL",
            "Instant one-click cooling actions and direct PWM fan controls."
        )
        fan_row = tk.Frame(sec_fan, bg=PANEL)
        fan_row.pack(fill="x", pady=2)

        ttk.Button(fan_row, text="❄️ Cooler Boost (100% Fans)", command=self.activate_cooler_boost).pack(side="left", padx=(0, 8))
        ttk.Button(fan_row, text="🤫 Silent Cooling", command=self.activate_silent_cooling).pack(side="left", padx=(0, 8))
        ttk.Button(fan_row, text="🔄 Auto Cooling Curve", command=lambda: self.set_profile("balanced", automatic=False)).pack(side="left", padx=(0, 14))

    def _build_tab_automation(self) -> None:
        # 1. Temperature History Rolling Chart
        chart_sec = self._section(self.tab_automation, "ROLLING TEMPERATURE TRENDS", "Live CPU and GPU temperature history")
        self.chart = tk.Canvas(chart_sec, height=135, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, pady=(3, 0))
        self.chart.bind("<Configure>", lambda _event: self.draw_chart())

        # 2. Workload Automation
        auto_sec = self._section(
            self.tab_automation, "WORKLOAD-AWARE PROFILE SWITCHING",
            "Monitors CPU load and thermals. Automatically scales down to quiet/balanced cooling when hot."
        )
        tk.Checkbutton(
            auto_sec, text="Enable workload-based automatic profile switching",
            variable=self.auto_var, command=self.toggle_auto,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=(self.font_family, 9),
        ).pack(anchor="w")
        tk.Label(auto_sec, textvariable=self.cpu_load_var, bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(anchor="w", pady=(2, 0))
        tk.Label(auto_sec, textvariable=self.auto_status_var, bg=PANEL, fg=ACCENT, font=(self.font_family, 8)).pack(anchor="w", pady=(2, 0))

        # 3. Desktop Temperature Alerts
        alerts_sec = self._section(
            self.tab_automation, "DESKTOP TEMPERATURE ALERTS",
            "Dispatches notifications via notify-send when thresholds are breached. Rearms after cooling by 5°C."
        )
        alert_row = tk.Frame(alerts_sec, bg=PANEL)
        alert_row.pack(fill="x", pady=2)

        tk.Checkbutton(
            alert_row, text="Enable alerts", variable=self.alerts_var,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=(self.font_family, 9),
        ).pack(side="left", padx=(0, 16))

        tk.Label(alert_row, text="CPU Alert °C:", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.cpu_alert_var, width=6).pack(side="left", padx=(5, 14))

        tk.Label(alert_row, text="GPU Alert °C:", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.gpu_alert_var, width=6).pack(side="left", padx=(5, 14))

        ttk.Button(alert_row, text="Save Settings", command=self.save_settings).pack(side="left", padx=8)
        ttk.Button(alert_row, text="Test Notification", command=lambda: self._notify("Thermals Alert Test", "Hardware notification system is operational.")).pack(side="left")

    def _on_target_slider_change(self, value: str) -> None:
        val = float(value)
        self.target_temp_label_var.set(f"{val:.0f} °C")
        self.config["target_temp_c"] = round(val, 1)

    def toggle_target_temp(self) -> None:
        enabled = bool(self.target_temp_var.get())
        self.config["target_temp_enabled"] = enabled
        self.config["target_temp_c"] = round(float(self.target_temp_val_var.get()), 1)
        save_config(self.config)

        if enabled:
            self.target_badge.configure(text=f"TARGET: {self.config['target_temp_c']:.0f}°C", fg=AMBER)
            self.target_status_var.set(f"Target Limiter: active (maintaining ceiling at {self.config['target_temp_c']:.0f}°C).")
        else:
            self.target_badge.configure(text="TARGET: OFF", fg=MUTED)
            self.target_status_var.set("Target Limiter: disabled.")
            if self.target_temp_regulating:
                self.target_temp_regulating = False
                base = self.base_profile_before_target or "balanced"
                ThermalControlManager.set_profile(base, automatic=True)

    def toggle_cpu_boost(self) -> None:
        current = ThermalControlManager.get_cpu_boost()
        target = not current if current is not None else False
        success, msg = ThermalControlManager.set_cpu_boost(target)
        if success:
            self.status_var.set(msg)
            self._update_boost_display()
        else:
            messagebox.showwarning(APP_NAME, f"Could not toggle CPU Boost.\n{msg}")

    def set_cpu_epp(self, preference: str) -> None:
        success, msg = ThermalControlManager.set_cpu_epp(preference)
        if success:
            self.status_var.set(msg)
        else:
            messagebox.showwarning(APP_NAME, f"Could not set CPU power preference.\n{msg}")

    def activate_cooler_boost(self) -> None:
        success, msg = ThermalControlManager.set_cooler_boost()
        self.status_var.set(msg)
        self._update_profile_controls()

    def activate_silent_cooling(self) -> None:
        success, msg = ThermalControlManager.set_silent_cooling()
        self.status_var.set(msg)
        self._update_profile_controls()

    def _update_boost_display(self) -> None:
        boost = ThermalControlManager.get_cpu_boost()
        if boost is True:
            self.boost_status_var.set("CPU Turbo Boost: ENABLED")
            self.btn_boost.configure(text="Disable Boost (Cooler CPU)")
        elif boost is False:
            self.boost_status_var.set("CPU Turbo Boost: DISABLED (Cooling Mode)")
            self.btn_boost.configure(text="Enable Boost")
        else:
            self.boost_status_var.set("CPU Turbo Boost: N/A")

    def _update_profile_controls(self) -> None:
        current = ThermalControlManager.get_current_profile()
        available = ThermalControlManager.get_available_profiles()

        for profile, button in self.profile_buttons.items():
            is_avail = profile in available if available else False
            button.state(["!disabled"] if is_avail else ["disabled"])
            button.configure(style="Selected.Profile.TButton" if profile == current else "Profile.TButton")

        if current:
            self.profile_badge.configure(text=f"PROFILE: {current.upper()}")
        else:
            self.profile_badge.configure(text="PROFILE: N/A" if not available else "PROFILE: UNKNOWN")

    def save_settings(self) -> None:
        try:
            cpu_alert = float(self.cpu_alert_var.get())
            gpu_alert = float(self.gpu_alert_var.get())
            if not (40.0 <= cpu_alert <= 115.0 and 40.0 <= gpu_alert <= 115.0):
                raise ValueError("Alert thresholds must be between 40°C and 115°C.")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"Invalid temperature threshold: {exc}")
            return

        self.config.update({
            "cpu_alert_c": cpu_alert,
            "gpu_alert_c": gpu_alert,
            "alerts_enabled": bool(self.alerts_var.get()),
            "auto_enabled": bool(self.auto_var.get()),
            "target_temp_enabled": bool(self.target_temp_var.get()),
            "target_temp_c": round(float(self.target_temp_val_var.get()), 1),
        })
        if save_config(self.config):
            self.status_var.set(f"Settings saved to {CONFIG_PATH}")
        else:
            messagebox.showerror(APP_NAME, "Could not save settings.")

    def toggle_auto(self) -> None:
        self.config["auto_enabled"] = bool(self.auto_var.get())
        self.auto_candidate = None
        self.auto_candidate_count = 0
        save_config(self.config)
        if self.auto_var.get():
            self.auto_status_var.set("Workload switching enabled; waiting for stable readings.")
        else:
            self.auto_status_var.set("Workload switching is disabled.")

    def _notify(self, title: str, body: str) -> None:
        notify = shutil.which("notify-send")
        if notify:
            try:
                subprocess.Popen(
                    [notify, "--app-name", APP_NAME, title, body],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                return
            except OSError:
                pass
        self.status_var.set(f"{title}: {body}")

    def check_alerts(self, temperatures: dict[str, float]) -> None:
        if not self.alerts_var.get():
            return
        for sensor, label in (("cpu", "CPU"), ("gpu", "GPU")):
            value = temperatures.get(sensor)
            if value is None:
                continue
            threshold = float(self.config[f"{sensor}_alert_c"])
            if value >= threshold and not self.alert_latched[sensor]:
                self.alert_latched[sensor] = True
                self._notify(f"{label} Temperature High", f"{label} is {value:.1f}°C (alert threshold {threshold:.1f}°C).")
            elif value <= threshold - ALERT_RESET_HYSTERESIS:
                self.alert_latched[sensor] = False

    def evaluate_target_temperature(self, temperatures: dict[str, float]) -> None:
        """Enforce the user-configured maximum target temperature ceiling."""
        if not self.target_temp_var.get():
            return

        target_c = float(self.config["target_temp_c"])
        cpu_t = temperatures.get("cpu", 0.0)
        gpu_t = temperatures.get("gpu", 0.0)
        hottest = max(cpu_t, gpu_t)

        if hottest <= 0.0:
            return

        if hottest >= target_c:
            if not self.target_temp_regulating:
                self.target_temp_regulating = True
                self.base_profile_before_target = ThermalControlManager.get_current_profile()
                # Apply cooling downshift
                ThermalControlManager.set_profile("quiet", automatic=True)
                ThermalControlManager.set_cpu_epp("power")

            self.target_status_var.set(
                f"⚠️ Regulating: Hottest sensor is {hottest:.1f}°C (exceeds {target_c:.1f}°C target). Cooling applied."
            )
        elif hottest <= (target_c - TARGET_TEMP_HYSTERESIS):
            if self.target_temp_regulating:
                self.target_temp_regulating = False
                base = self.base_profile_before_target or "balanced"
                ThermalControlManager.set_profile(base, automatic=True)
                ThermalControlManager.set_cpu_epp("balance_performance")

            self.target_status_var.set(
                f"✓ Target Maintained: Hottest sensor is {hottest:.1f}°C (within {target_c:.1f}°C ceiling)."
            )

    def _cpu_load(self) -> float | None:
        sample = cpu_load_percent()
        if sample is None:
            return None
        total, idle = sample
        if self.cpu_stat_previous is None:
            self.cpu_stat_previous = (total, idle)
            return None
        old_total, old_idle = self.cpu_stat_previous
        self.cpu_stat_previous = (total, idle)
        delta_total, delta_idle = total - old_total, idle - old_idle
        if delta_total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (delta_total - delta_idle) / delta_total))

    def evaluate_automatic_profile(self, temperatures: dict[str, float], load: float | None) -> None:
        if not self.auto_var.get() or self.target_temp_regulating:
            return
        available = ThermalControlManager.get_available_profiles()
        if not available:
            self.auto_status_var.set("Workload mode: thermal profiles are not supported on this platform.")
            return

        cpu = temperatures.get("cpu")
        gpu = temperatures.get("gpu")
        if cpu is None:
            return

        target: str = "balanced"
        hottest = max(cpu, gpu if gpu is not None else cpu)
        if hottest >= float(self.config["auto_critical_c"]):
            target = "quiet"
        elif cpu >= float(self.config["auto_hot_cpu_c"]):
            target = "quiet" if cpu >= 82.0 else "balanced"
        elif load is not None and load >= float(self.config["auto_performance_load"]) and cpu < 75.0:
            target = "performance"
        elif load is not None and load <= float(self.config["auto_quiet_load"]) and cpu < float(self.config["auto_cool_cpu_c"]):
            target = "quiet"

        if target not in available:
            target = available[0]

        current = ThermalControlManager.get_current_profile()
        if target == current:
            self.auto_candidate = None
            self.auto_candidate_count = 0
            self.auto_status_var.set(f"Workload mode: '{current}' is optimal.")
            return

        if target == self.auto_candidate:
            self.auto_candidate_count += 1
        else:
            self.auto_candidate = target
            self.auto_candidate_count = 1

        if self.auto_candidate_count < AUTO_CONFIRM_SAMPLES:
            self.auto_status_var.set(f"Workload mode: verifying {target} ({self.auto_candidate_count}/{AUTO_CONFIRM_SAMPLES}).")
            return

        if time.monotonic() - self.last_auto_change < AUTO_MIN_DWELL_SECONDS:
            self.auto_status_var.set("Workload mode: holding profile to prevent oscillation.")
            return

        self.auto_candidate = None
        self.auto_candidate_count = 0
        self.set_profile(target, automatic=True)

    def set_profile(self, profile: str, automatic: bool = False) -> None:
        if self.profile_change_busy:
            return

        self.profile_change_busy = True
        self.status_var.set(f"Switching to {profile} profile...")
        self.root.update_idletasks()

        def worker() -> None:
            success, detail = ThermalControlManager.set_profile(profile, automatic=automatic)
            self.root.after(0, lambda: self._profile_result(profile, automatic, success, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _profile_result(self, profile: str, automatic: bool, success: bool, detail: str) -> None:
        self.profile_change_busy = False
        if success:
            self.status_var.set(f"Thermal profile changed to {profile}.")
            if automatic:
                self.last_auto_change = time.monotonic()
                self.auto_status_var.set(f"Workload mode active: {profile}.")
        else:
            message = detail or "Profile change rejected."
            self.status_var.set(f"Could not change profile: {message}")
            if not automatic:
                messagebox.showwarning(APP_NAME, f"Could not change profile.\n{message}")
        self._update_profile_controls()

    def refresh(self) -> None:
        if self.busy:
            self.root.after(POLL_MS, self.refresh)
            return
        self.busy = True

        def worker() -> None:
            all_hw = detect_all_hardware(self.sys_info["cpu"])
            temps = collect_sensors()
            fan_rpm, fan_pwm = fan_capabilities()
            load = self._cpu_load()
            self.root.after(0, lambda: self._apply_refresh(all_hw, temps, fan_rpm, fan_pwm, load))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_refresh(
        self,
        all_hw: list[HardwareSensor],
        temperatures: dict[str, float],
        fan_rpm: list[tuple[str, int]],
        fan_pwm: list[str],
        load: float | None,
    ) -> None:
        # Update summary cards
        self.temp_vars["cpu"].set(f"{temperatures.get('cpu', 0.0):.1f} °C" if "cpu" in temperatures else "Unavailable")
        self.temp_vars["gpu"].set(f"{temperatures.get('gpu', 0.0):.1f} °C" if "gpu" in temperatures else "Unavailable")
        self.temp_vars["nvme"].set(f"{temperatures.get('nvme', 0.0):.1f} °C" if "nvme" in temperatures else "Unavailable")
        self.temp_vars["acpi"].set(f"{temperatures.get('acpi', 0.0):.1f} °C" if "acpi" in temperatures else "Unavailable")

        # Network & Battery cards
        net_sensor = next((s for s in all_hw if s.category == "network"), None)
        self.temp_vars["net"].set(f"{net_sensor.value:.1f} °C" if net_sensor else "Offline")

        bat_sensor = next((s for s in all_hw if s.category == "battery"), None)
        if bat_sensor:
            st = bat_sensor.extra.get("status", "")
            self.temp_vars["bat"].set(f"{bat_sensor.value:.0f}% ({st[:4]})")
        else:
            self.temp_vars["bat"].set("AC Mains")

        # History rolling trends
        for key in ("cpu", "gpu"):
            if key in temperatures:
                self.history[key].append(temperatures[key])
                self.history[key] = self.history[key][-HISTORY_LIMIT:]

        if load is not None:
            self.cpu_load_var.set(f"CPU load: {load:.0f}%")
        else:
            self.cpu_load_var.set("CPU load: sampling…")

        # Update Treeview rows
        self._update_hardware_tree(all_hw)

        # Regulate target temp & check alerts
        self.evaluate_target_temperature(temperatures)
        self.check_alerts(temperatures)
        self.evaluate_automatic_profile(temperatures, load)

        # Fan & sensor count status
        active_fans = [f"{lbl}: {rpm} RPM" for lbl, rpm in fan_rpm if rpm > 0]
        if active_fans:
            fan_msg = f"Fans: {', '.join(active_fans)}"
        elif fan_rpm:
            fan_msg = f"Fans: {len(fan_rpm)} tachometer(s) detected (idle or passive cooling)"
        else:
            fan_msg = "Fans: Firmware-managed ACPI cooling policy"

        unique_cats = len({s.category for s in all_hw})
        self.capability_var.set(f"✓ Detected {len(all_hw)} hardware sensors across {unique_cats} component classes • {fan_msg}")

        self.last_update_var.set(f"Last update: {datetime.now().strftime('%H:%M:%S')}  •  Poll: 2s")
        self._update_profile_controls()
        self._update_boost_display()
        self.draw_chart()
        self.busy = False
        self.root.after(POLL_MS, self.refresh)

    def _update_hardware_tree(self, all_hw: list[HardwareSensor]) -> None:
        """Update hardware inventory treeview with live values."""
        existing_items = set(self.hw_tree.get_children())
        updated_items = set()

        category_labels = {
            "cpu": "Processor",
            "gpu": "Graphics",
            "storage": "Storage Drive",
            "motherboard": "Motherboard",
            "network": "Wireless/NIC",
            "battery": "Power Supply",
            "fan": "Cooling Fan",
        }

        for s in all_hw:
            cat_display = category_labels.get(s.category, s.category.title())
            reading_str = f"{s.value:.1f} {s.unit}" if s.unit != "%" else f"{s.value:.0f} %"

            limits_str = "—"
            if s.crit_temp:
                limits_str = f"Crit: {s.crit_temp:.1f}°C"
                if s.max_temp:
                    limits_str = f"Max: {s.max_temp:.1f} / {limits_str}"
            elif s.max_temp:
                limits_str = f"Max: {s.max_temp:.1f}°C"

            status_str = s.status.upper()
            row_id = s.id

            values = (cat_display, s.device_name, s.sensor_name, reading_str, limits_str, status_str)

            if row_id in existing_items:
                self.hw_tree.item(row_id, values=values, tags=(s.status,))
            else:
                self.hw_tree.insert("", "end", iid=row_id, values=values, tags=(s.status,))
            updated_items.add(row_id)

        # Remove stale items
        for old_id in existing_items - updated_items:
            self.hw_tree.delete(old_id)

    def draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 100)
        left, right, top, bottom = 38, width - 12, 12, height - 20
        values = self.history["cpu"] + self.history["gpu"]
        if not values:
            canvas.create_text(width / 2, height / 2, text="Collecting temperature telemetry history…",
                               fill=MUTED, font=(self.font_family, 9))
            return

        low, high = min(20.0, min(values) - 5), max(90.0, max(values) + 5)
        if high <= low:
            high = low + 10

        for tick in range(20, 101, 20):
            y = bottom - ((tick - low) / (high - low)) * (bottom - top)
            if top <= y <= bottom:
                canvas.create_line(left, y, right, y, fill="#243345", dash=(2, 4))
                canvas.create_text(18, y, text=str(tick), fill=MUTED, font=(self.font_family, 7))

        # Target temperature line if set
        if self.target_temp_var.get():
            target_val = float(self.config["target_temp_c"])
            if low <= target_val <= high:
                t_y = bottom - ((target_val - low) / (high - low)) * (bottom - top)
                canvas.create_line(left, t_y, right, t_y, fill=AMBER, dash=(4, 2), width=1)
                canvas.create_text(right - 24, t_y - 8, text=f"Target {target_val:.0f}°C", fill=AMBER,
                                   font=(self.font_family, 7, "bold"))

        for key, color in (("cpu", GREEN), ("gpu", ACCENT)):
            series = self.history[key]
            if len(series) < 2:
                continue
            points = []
            for index, value in enumerate(series):
                x = left + index / max(HISTORY_LIMIT - 1, 1) * (right - left)
                y = bottom - ((value - low) / (high - low)) * (bottom - top)
                points.extend((x, y))
            canvas.create_line(*points, fill=color, width=2, smooth=True)


# ============================================================================
# CLI and Terminal Operation
# ============================================================================

def get_telemetry_snapshot() -> dict[str, Any]:
    sys_info = get_system_info()
    all_hw = detect_all_hardware(sys_info["cpu"])
    temperatures = collect_sensors()
    fans, pwm_files = fan_capabilities()
    load_sample = cpu_load_percent()
    cpu_load: float | None = None
    if load_sample:
        total, idle = load_sample
        cpu_load = (100.0 - idle) if total == 100.0 else None

    active_profile = ThermalControlManager.get_current_profile()
    available_profiles = ThermalControlManager.get_available_profiles()
    config = load_config()

    return {
        "timestamp": datetime.now().isoformat(),
        "system": sys_info,
        "temperatures": temperatures,
        "cpu_load_percent": cpu_load,
        "all_hardware": [s.to_dict() for s in all_hw],
        "fans": [{"label": label, "rpm": rpm} for label, rpm in fans],
        "fan_pwm_files": pwm_files,
        "controls": {
            "active_profile": active_profile,
            "available_profiles": available_profiles,
            "target_temp_enabled": bool(config.get("target_temp_enabled", False)),
            "target_temp_c": float(config.get("target_temp_c", 75.0)),
            "cpu_boost": ThermalControlManager.get_cpu_boost(),
            "cpu_epp": ThermalControlManager.get_cpu_epp(),
        },
        "config": config,
    }


def print_cli_status(snapshot: dict[str, Any] | None = None) -> None:
    data = snapshot or get_telemetry_snapshot()
    sys_info = data["system"]
    all_hw = data.get("all_hardware", [])
    controls = data["controls"]
    fans = data["fans"]
    active = controls["active_profile"] or "N/A"
    available = controls["available_profiles"]

    print("=" * 68)
    print(f"  {APP_NAME} v{__version__} - Universal Linux Hardware & Thermal Control")
    print("=" * 68)
    print(f"Host Device   : {sys_info['summary']}")
    print(f"Operating Sys : {sys_info['os']} ({sys_info.get('arch', platform.machine())})")
    print("-" * 68)

    print(f"HARDWARE SENSORS DETECTED ({len(all_hw)} total):")
    for s in all_hw:
        cat_str = f"[{s['category'].upper()}]"
        lim_str = f"(Crit: {s['crit_temp']:.1f}°C)" if s.get("crit_temp") else ""
        val_str = f"{s['value']:.1f} {s['unit']}" if s['unit'] != "%" else f"{s['value']:.0f}%"
        status_str = f"[{s['status'].upper()}]"
        print(f"  {cat_str:<13} {s['device_name']} | {s['sensor_name']}: {val_str} {status_str} {lim_str}")

    print("-" * 68)
    print("THERMAL & COOLING CONTROLS:")
    print(f"  Platform Profile  : {active.upper()} (Available: {', '.join(available) if available else 'None'})")

    target_on = controls.get("target_temp_enabled", False)
    target_c = controls.get("target_temp_c", 75.0)
    print(f"  Target Temp Limit : {target_c:.1f}°C [{'ACTIVE' if target_on else 'DISABLED'}]")

    boost_val = controls.get("cpu_boost")
    boost_str = "ENABLED" if boost_val is True else ("DISABLED (Cooling)" if boost_val is False else "N/A")
    print(f"  CPU Turbo Boost   : {boost_str}")

    epp_val = controls.get("cpu_epp")
    if epp_val:
        print(f"  CPU Power Pref    : {epp_val}")

    if fans:
        fan_strs = [f"{f['label']}: {f['rpm']} RPM" for f in fans if f["rpm"] > 0]
        if fan_strs:
            print(f"  Cooling Fans      : {', '.join(fan_strs)}")
        else:
            print(f"  Cooling Fans      : {len(fans)} tachometer(s) detected (idle / passive cooling)")
    else:
        print("  Cooling Fans      : Firmware-managed dynamic cooling curve")
    print("=" * 68)


def cli_monitor() -> None:
    """Live updating terminal telemetry monitor."""
    print(f"Starting {APP_NAME} terminal monitor (Press Ctrl+C to exit)...")
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")
            print_cli_status()
            print("\nUpdating every 2 seconds. Press Ctrl+C to exit.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nExited monitor.")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="thermals",
        description="Thermals - Universal Linux Hardware Telemetry & Thermal Control Center",
    )
    parser.add_argument("-s", "--status", action="store_true", help="Print current telemetry and exit")
    parser.add_argument("-j", "--json", action="store_true", help="Print telemetry as JSON")
    parser.add_argument("-m", "--monitor", action="store_true", help="Run real-time terminal monitor")
    parser.add_argument("-l", "--list-profiles", action="store_true", help="List supported thermal profiles")
    parser.add_argument("--list-hardware", action="store_true", help="List all detected hardware sensors")
    parser.add_argument("-p", "--set-profile", metavar="PROFILE", help="Set platform thermal profile")
    parser.add_argument("--set-target-temp", metavar="CELSIUS|off", help="Set target temperature limit (e.g. 70 or off)")
    parser.add_argument("--set-boost", choices=["on", "off"], help="Enable or disable CPU Turbo Boost")
    parser.add_argument("--set-epp", choices=["power", "balance_power", "balance_performance", "performance"], help="Set CPU Energy-Performance Preference")
    parser.add_argument("--set-fan", metavar="PWM_OR_AUTO", help="Set fan speed (0-255 or auto)")
    parser.add_argument("--cooler-boost", action="store_true", help="Activate Cooler Boost (100%% cooling & fans)")
    parser.add_argument("--silent", action="store_true", help="Activate silent cooling mode")
    parser.add_argument("--cli", action="store_true", help="Force CLI terminal mode")
    parser.add_argument("--gui", action="store_true", help="Force graphical desktop interface")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    args = parser.parse_args()

    # CLI Actions
    if args.json:
        print(json.dumps(get_telemetry_snapshot(), indent=2))
        return 0

    if args.status:
        print_cli_status()
        return 0

    if args.list_hardware:
        sys_info = get_system_info()
        sensors = detect_all_hardware(sys_info["cpu"])
        print(f"Detected {len(sensors)} hardware sensors on {sys_info['summary']}:")
        for s in sensors:
            print(f"  [{s.category.upper():<11}] {s.device_name:<30} | {s.sensor_name:<20}: {s.value} {s.unit} ({s.status})")
        return 0

    if args.list_profiles:
        profs = ThermalControlManager.get_available_profiles()
        current = ThermalControlManager.get_current_profile()
        if profs:
            print("Supported thermal profiles on this hardware:")
            for p in profs:
                mark = "*" if p == current else " "
                desc = PROFILE_DESCRIPTIONS.get(p, ("", ""))[1]
                print(f" {mark} {p:<15} {desc}")
        else:
            print("No platform thermal profiles advertised by this hardware/kernel.")
        return 0

    if args.set_profile:
        target = args.set_profile.strip().lower()
        success, msg = ThermalControlManager.set_profile(target, automatic=False)
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.set_target_temp:
        val = args.set_target_temp.strip().lower()
        cfg = load_config()
        if val in ("off", "disable", "false", "0"):
            cfg["target_temp_enabled"] = False
            save_config(cfg)
            print("[SUCCESS] Target temperature limiter disabled.")
        else:
            try:
                t_val = float(val)
                if not (45.0 <= t_val <= 95.0):
                    raise ValueError("Target must be between 45°C and 95°C")
                cfg["target_temp_enabled"] = True
                cfg["target_temp_c"] = round(t_val, 1)
                save_config(cfg)
                print(f"[SUCCESS] Target temperature ceiling set to {t_val:.1f}°C.")
            except ValueError as exc:
                print(f"[FAILED] Invalid temperature: {exc}")
                return 1
        return 0

    if args.set_boost:
        enable = args.set_boost == "on"
        success, msg = ThermalControlManager.set_cpu_boost(enable)
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.set_epp:
        success, msg = ThermalControlManager.set_cpu_epp(args.set_epp)
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.cooler_boost:
        success, msg = ThermalControlManager.set_cooler_boost()
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.silent:
        success, msg = ThermalControlManager.set_silent_cooling()
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.set_fan:
        _, pwm_files = fan_capabilities()
        if not pwm_files:
            print("[FAILED] No controllable PWM fans detected on this system.")
            return 1
        success, msg = ThermalControlManager.set_fan_pwm(pwm_files[0], args.set_fan)
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.monitor or args.cli:
        cli_monitor()
        return 0

    # Graphical Interface Execution
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if not has_display and not args.gui:
        print(f"[{APP_NAME}] No graphical display detected (DISPLAY or WAYLAND_DISPLAY not set).")
        print(f"[{APP_NAME}] Running in terminal status mode:\n")
        print_cli_status()
        print("\nTip: Run 'thermals --monitor' for live terminal monitoring, or 'thermals --help' for options.")
        return 0

    if not TKINTER_AVAILABLE:
        print("Error: Tkinter is required for GUI, but python3-tk is not installed.", file=sys.stderr)
        print("Use CLI mode: thermals --status or thermals --monitor", file=sys.stderr)
        return 1

    try:
        root = tk.Tk()
        ThermalApp(root)
        root.mainloop()
        return 0
    except tk.TclError as exc:
        print(f"[{APP_NAME}] Could not initialize graphical interface: {exc}", file=sys.stderr)
        print(f"[{APP_NAME}] Falling back to terminal mode:\n", file=sys.stderr)
        print_cli_status()
        return 0


if __name__ == "__main__":
    sys.exit(main())
