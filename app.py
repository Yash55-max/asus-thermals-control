#!/usr/bin/env python3
"""Thermals - Universal Linux Hardware Telemetry & Thermal Control Center.

Features:
- High-efficiency telemetry engine (cached sensor registry, direct sysfs I/O, < 0.1% CPU)
- Hardware temperature controls with interactive rotary knobs:
  * Target Temperature Limiter (Smart closed-loop thermostat)
  * Platform thermal profiles (Quiet / Power Saver, Balanced, Performance)
  * CPU Turbo Boost switch and Energy-Performance Preference (EPP)
  * Fan PWM speed knob and one-click Cooler Boost (100% airflow)
- Critical desktop push notifications via native notify-send / freedesktop notifications
- Dual graphical (Tkinter GUI with tactile knobs) and terminal (CLI monitor, status, JSON) interfaces
"""
from __future__ import annotations

import argparse
import json
import math
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
from typing import Any, Callable

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
ICON_PATH = Path.home() / ".local/share/icons/hicolor/scalable/apps/thermals.svg"

# CPU Control paths
AMD_BOOST_PATH = Path("/sys/devices/system/cpu/cpufreq/boost")
INTEL_NO_TURBO_PATH = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
ASUS_THROTTLE_PATH = Path("/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy")

POLL_MS = 2000
HISTORY_LIMIT = 60
AUTO_CONFIRM_SAMPLES = 3
AUTO_MIN_DWELL_SECONDS = 60
ALERT_RESET_HYSTERESIS = 5.0
ALERT_COOLDOWN_SECONDS = 60.0
TARGET_TEMP_HYSTERESIS = 5.0

# Professional Slate Theme Palette
BG = "#0d1117"            # Main Window Background
PANEL = "#161b22"         # Card / Section Background
PANEL_ALT = "#21262d"     # Inner Control / Button Background
BORDER = "#30363d"        # Subtle Border
FG = "#f0f6fc"            # Primary High-Contrast Text
MUTED = "#8b949e"         # Secondary Description Text
ACCENT = "#58a6ff"        # Accent Blue
GREEN = "#3fb950"         # Normal / Safe Green
AMBER = "#d29922"         # Warning / Warm Amber
RED = "#f85149"           # Critical / Hot Red
CYAN = "#39c5cf"          # Network / Fan Cyan
PURPLE = "#bc8cff"        # Battery Purple

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
    "fan_manual": False,
    "fan_speed_pct": 80,
    "boost_enabled": True,
    "preferred_profile": "balanced",
}

PROFILE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "quiet": ("Quiet", "Lower noise & heat (reduced power limit)"),
    "low-power": ("Low Power", "Energy saving cooling"),
    "power-saver": ("Power Saver", "Battery optimized cooling"),
    "cool": ("Cool", "Optimized cooling policy"),
    "balanced": ("Balanced", "Standard dynamic everyday cooling"),
    "balanced-performance": ("Balanced+", "Higher responsiveness"),
    "performance": ("Performance", "Max fan throughput & peak clocks"),
}


def read_text(path: Path | str) -> str | None:
    """Fast, safe reading of a sysfs text attribute."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
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


def dispatch_push_notification(title: str, body: str, urgency: str = "critical") -> bool:
    """Send a native Linux desktop push notification with high priority."""
    notify = shutil.which("notify-send")
    if not notify:
        return False
    try:
        icon_arg = str(ICON_PATH) if ICON_PATH.exists() else "dialog-warning"
        cmd = [
            notify,
            "--app-name=Thermals",
            f"--urgency={urgency}",
            f"--icon={icon_arg}",
            "--category=device.error",
            "--hint=int:transient:0",
            title,
            body,
        ]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ============================================================================
# High-Efficiency Cached Hardware Telemetry Engine
# ============================================================================

class CachedSensor:
    """Lightweight sensor descriptor with cached paths and minimal memory footprint."""
    __slots__ = (
        "id", "category", "device_name", "sensor_name", "chip", "path",
        "unit", "crit_temp", "max_temp", "is_temp", "val", "status", "last_raw",
    )

    def __init__(
        self,
        sid: str,
        category: str,
        device_name: str,
        sensor_name: str,
        chip: str,
        path: str,
        unit: str,
        crit_temp: float | None,
        max_temp: float | None,
        is_temp: bool = True,
    ) -> None:
        self.id = sid
        self.category = category
        self.device_name = device_name
        self.sensor_name = sensor_name
        self.chip = chip
        self.path = path
        self.unit = unit
        self.crit_temp = crit_temp
        self.max_temp = max_temp
        self.is_temp = is_temp
        self.val = 0.0
        self.status = "normal"
        self.last_raw = ""

    def poll(self) -> float:
        """Fast-path read of the numeric sensor file."""
        try:
            with open(self.path, "r", encoding="utf-8", errors="ignore", buffering=512) as f:
                raw = f.read().strip()
            if raw == self.last_raw:
                return self.val
            self.last_raw = raw
            v = float(raw)
            if self.is_temp:
                v = v / 1000.0 if v > 1000.0 else v
                self.val = round(v, 1)
                if self.crit_temp and v >= self.crit_temp:
                    self.status = "critical"
                elif self.max_temp and v >= self.max_temp:
                    self.status = "hot"
                elif v >= 85.0:
                    self.status = "critical"
                elif v >= 75.0:
                    self.status = "hot"
                elif v >= 65.0:
                    self.status = "warm"
                else:
                    self.status = "normal"
            else:
                self.val = v
            return self.val
        except (OSError, ValueError):
            return self.val

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "device_name": self.device_name,
            "sensor_name": self.sensor_name,
            "chip": self.chip,
            "sensor_type": "temperature" if self.is_temp else ("fan" if self.unit == "RPM" else "metric"),
            "value": self.val,
            "unit": self.unit,
            "crit_temp": self.crit_temp,
            "max_temp": self.max_temp,
            "status": self.status,
        }


class HardwareRegistry:
    """Manages sensor discovery and single-pass cached polling."""

    def __init__(self, cpu_name: str) -> None:
        self.cpu_name = cpu_name
        self.sensors: list[CachedSensor] = []
        self.nvidia_available = bool(shutil.which("nvidia-smi") and (
            Path("/dev/nvidia0").exists() or Path("/sys/bus/pci/drivers/nvidia").exists()
        ))
        self.has_battery = False
        self.battery_path: Path | None = None
        self.battery_status = "AC Mains"
        self.battery_pct = 100.0
        self.fan_pwm_files: list[str] = []
        self._discover_hardware()

    def _discover_hardware(self) -> None:
        """One-time scan that discovers all sensors and caches descriptors."""
        self.sensors.clear()
        self.fan_pwm_files.clear()
        seen: set[str] = set()

        # 1. Hwmon devices
        if HW_MON_PATH.exists():
            for hw in sorted(HW_MON_PATH.glob("hwmon*")):
                chip = (read_text(hw / "name") or "generic").lower()
                chip_raw = read_text(hw / "name") or "Generic"

                for inp in sorted(hw.glob("*_input")):
                    base = inp.name[:-6]
                    label = read_text(hw / f"{base}_label") or ""
                    crit_val: float | None = None
                    crit_raw = read_text(hw / f"{base}_crit")
                    if crit_raw:
                        try:
                            c = float(crit_raw)
                            crit_val = round(c / 1000.0 if c > 1000.0 else c, 1)
                        except ValueError:
                            pass

                    max_val: float | None = None
                    max_raw = read_text(hw / f"{base}_max")
                    if max_raw:
                        try:
                            m = float(max_raw)
                            m_cand = m / 1000.0 if m > 1000.0 else m
                            if 0.0 < m_cand < 150.0:
                                max_val = round(m_cand, 1)
                        except ValueError:
                            pass

                    sid = f"{hw.name}_{inp.name}"
                    if sid in seen:
                        continue
                    seen.add(sid)

                    if inp.name.startswith("temp"):
                        category = "motherboard"
                        dev_name = f"Motherboard ({chip_raw})"
                        s_name = label or f"Temp {base[4:] or '1'}"
                        label_l = label.lower()

                        if any(k in chip for k in (
                            "coretemp", "k10temp", "zenpower", "amd_energy", "cpu_thermal",
                            "soc_thermal", "bcm2835", "intel_rapl", "rockchip", "sunxi", "macsmc"
                        )) or any(k in label_l for k in ("tctl", "tdie", "package", "core", "tccd", "cpu")):
                            category = "cpu"
                            dev_name = self.cpu_name
                            s_name = label or "CPU Temperature"
                        elif any(k in chip for k in (
                            "amdgpu", "radeon", "nouveau", "i915", "xe", "panfrost", "mali", "nvidia"
                        )) or any(k in label_l for k in ("edge", "junction", "gpu", "gt", "vram")):
                            category = "gpu"
                            dev_name = f"GPU ({chip_raw.upper()})"
                            s_name = label or "GPU Temperature"
                        elif any(k in chip for k in ("nvme", "drivetemp")) or any(k in label_l for k in ("composite", "drive")):
                            category = "storage"
                            dev_name = "Storage Drive (NVMe/SSD)" if "nvme" in chip else f"Storage ({chip_raw})"
                            s_name = label or "Storage Temp"
                        elif any(k in chip for k in ("mt79", "iwl", "ath1", "rtw", "wlan", "wifi", "phy")):
                            category = "network"
                            dev_name = f"Wireless Network ({chip_raw})"
                            s_name = label or "Radio Temperature"
                        elif any(k in chip for k in ("acpitz", "asus", "thinkpad", "dell", "hp", "it87", "nct", "w83")):
                            category = "motherboard"
                            dev_name = f"Motherboard / System ({chip_raw})"
                            s_name = label or "ACPI Ambient"

                        sensor = CachedSensor(
                            sid=sid, category=category, device_name=dev_name, sensor_name=s_name,
                            chip=chip_raw, path=str(inp), unit="°C", crit_temp=crit_val, max_temp=max_val,
                            is_temp=True,
                        )
                        sensor.poll()
                        self.sensors.append(sensor)

                    elif inp.name.startswith("fan"):
                        sensor = CachedSensor(
                            sid=sid, category="fan", device_name=f"Cooling Fan ({chip_raw})",
                            sensor_name=label or f"Fan {base[3:] or '1'}",
                            chip=chip_raw, path=str(inp), unit="RPM", crit_temp=None, max_temp=None,
                            is_temp=False,
                        )
                        sensor.poll()
                        self.sensors.append(sensor)

                for path in hw.glob("pwm[0-9]*"):
                    if not path.name.endswith("_enable"):
                        self.fan_pwm_files.append(str(path))

        # 2. Battery / Power Supply
        if POWER_SUPPLY_PATH.exists():
            for ps in sorted(POWER_SUPPLY_PATH.glob("BAT*")):
                self.has_battery = True
                self.battery_path = ps
                cap_file = ps / "capacity"
                if cap_file.exists():
                    bsensor = CachedSensor(
                        sid=f"bat_{ps.name}",
                        category="battery",
                        device_name=f"System Battery ({ps.name})",
                        sensor_name="Battery Charge",
                        chip=ps.name,
                        path=str(cap_file),
                        unit="%",
                        crit_temp=None,
                        max_temp=None,
                        is_temp=False,
                    )
                    bsensor.poll()
                    self.sensors.append(bsensor)
                break

    def poll_all(self) -> tuple[dict[str, float], list[dict[str, Any]]]:
        """High-speed single pass reading of all cached sensors."""
        for s in self.sensors:
            s.poll()

        summary: dict[str, float] = {}
        for s in self.sensors:
            if not s.is_temp:
                continue
            cat = s.category
            if cat == "cpu":
                if any(k in s.sensor_name.lower() for k in ("tctl", "tdie", "package")):
                    summary["cpu"] = s.val
                else:
                    summary.setdefault("cpu", s.val)
            elif cat == "gpu":
                if any(k in s.sensor_name.lower() for k in ("edge", "core")):
                    summary["gpu"] = s.val
                else:
                    summary.setdefault("gpu", s.val)
            elif cat == "storage":
                if "composite" in s.sensor_name.lower():
                    summary["nvme"] = s.val
                else:
                    summary.setdefault("nvme", s.val)
            elif cat == "motherboard":
                summary.setdefault("acpi", s.val)

        # Update battery if present
        if self.has_battery and self.battery_path:
            cap_raw = read_text(self.battery_path / "capacity")
            st_raw = read_text(self.battery_path / "status")
            if cap_raw and cap_raw.isdigit():
                self.battery_pct = float(cap_raw)
            if st_raw:
                self.battery_status = st_raw

        return summary, [s.to_dict() for s in self.sensors]


# Global Hardware Registry
_REGISTRY: HardwareRegistry | None = None

def get_registry(cpu_name: str = "CPU") -> HardwareRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = HardwareRegistry(cpu_name)
    return _REGISTRY


def cpu_load_percent() -> tuple[float, float] | None:
    """Read aggregate CPU utilization from /proc/stat."""
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            fields = f.readline().split()[1:]
        values = [int(v) for v in fields]
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

    @staticmethod
    def find_sysfs_paths() -> tuple[Path | None, Path | None]:
        if PROFILE_STD_PATH.exists():
            choices = PROFILE_STD_CHOICES if PROFILE_STD_CHOICES.exists() else None
            return PROFILE_STD_PATH, choices

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
        target_path = AMD_BOOST_PATH if AMD_BOOST_PATH.exists() else (INTEL_NO_TURBO_PATH if INTEL_NO_TURBO_PATH.exists() else None)
        if not target_path:
            return False, "CPU Boost interface not exposed on this CPU architecture."

        val_str = ("1" if enabled else "0") if target_path == AMD_BOOST_PATH else ("0" if enabled else "1")

        if os.access(target_path, os.W_OK):
            try:
                target_path.write_text(val_str + "\n", encoding="utf-8")
                return True, f"CPU Turbo Boost {'enabled' if enabled else 'disabled'}."
            except OSError as exc:
                return False, f"Write failed: {exc}"

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

        policies = list(Path("/sys/devices/system/cpu/cpufreq").glob("policy*/energy_performance_preference"))
        if policies and all(os.access(p, os.W_OK) for p in policies):
            try:
                for p in policies:
                    p.write_text(preference + "\n", encoding="utf-8")
                return True, f"Applied CPU EPP {preference} to all cores."
            except OSError as exc:
                return False, str(exc)

        return False, "Setting CPU EPP requires privileges or power-profiles-daemon."

    @classmethod
    def set_cooler_boost(cls) -> tuple[bool, str]:
        """Activate maximum cooling (Cooler Boost: Performance profile + max fans)."""
        cls.set_profile("performance", automatic=True)
        if ASUS_THROTTLE_PATH.exists() and os.access(ASUS_THROTTLE_PATH, os.W_OK):
            try:
                ASUS_THROTTLE_PATH.write_text("1\n", encoding="utf-8")
            except OSError:
                pass

        reg = get_registry()
        for pwm in reg.fan_pwm_files:
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
# Professional Rotary Knob UI Component
# ============================================================================

class KnobControl(tk.Canvas):
    """Sleek, minimalist tactile rotary knob for thermal and fan management."""

    def __init__(
        self,
        parent: tk.Widget,
        min_val: float = 45.0,
        max_val: float = 90.0,
        initial: float = 70.0,
        step: float = 1.0,
        unit: str = "°C",
        label: str = "TARGET",
        size: int = 120,
        command: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(parent, width=size, height=size, bg=PANEL, highlightthickness=0)
        self.min_val = float(min_val)
        self.max_val = float(max_val)
        self.val = float(initial)
        self.step = float(step)
        self.unit = unit
        self.label_text = label
        self.size = size
        self.command = command
        self.enabled = True
        self.cx = size / 2.0
        self.cy = size / 2.0
        self.r = size / 2.0 - 13.0
        self.start_ang = 225.0
        self.total_span = 270.0

        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_scroll)
        self.bind("<Button-4>", lambda _e: self.step_val(1))
        self.bind("<Button-5>", lambda _e: self.step_val(-1))
        self.draw()

    def set_value(self, val: float, trigger: bool = True) -> None:
        self.val = max(self.min_val, min(self.max_val, val))
        self.draw()
        if trigger and self.command:
            self.command(self.val)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        self.draw()

    def step_val(self, direction: int) -> None:
        if not self.enabled:
            return
        self.set_value(self.val + direction * self.step)

    def _angle_to_val(self, rad: float) -> float:
        deg = math.degrees(rad) % 360.0
        diff = (self.start_ang - deg) % 360.0
        if diff > self.total_span:
            diff = 0.0 if diff > 315.0 else self.total_span
        frac = diff / self.total_span
        return self.min_val + frac * (self.max_val - self.min_val)

    def _on_click(self, event: tk.Event) -> None:
        if not self.enabled:
            return
        dx = event.x - self.cx
        dy = self.cy - event.y
        rad = math.atan2(dy, dx)
        val = self._angle_to_val(rad)
        rounded = round(val / self.step) * self.step
        self.set_value(rounded)

    def _on_drag(self, event: tk.Event) -> None:
        self._on_click(event)

    def _on_scroll(self, event: tk.Event) -> None:
        if not self.enabled:
            return
        delta = 1 if event.delta > 0 else -1
        self.step_val(delta)

    def draw(self) -> None:
        self.delete("all")
        bbox = (self.cx - self.r, self.cy - self.r, self.cx + self.r, self.cy + self.r)

        # Background Track Arc
        self.create_arc(bbox, start=self.start_ang, extent=-self.total_span, style="arc", width=7, outline="#222c3a")

        frac = (self.val - self.min_val) / max(self.max_val - self.min_val, 1.0)
        active_extent = -self.total_span * frac

        # Professional Dynamic Color Accent
        color = GREEN if frac < 0.55 else (AMBER if frac < 0.8 else RED)
        if not self.enabled:
            color = "#2c3b4d"

        # Active Progress Arc
        if frac > 0.01:
            self.create_arc(bbox, start=self.start_ang, extent=active_extent, style="arc", width=7, outline=color)

        # Thumb Pointer Dot
        cur_ang_deg = self.start_ang + active_extent
        cur_rad = math.radians(cur_ang_deg)
        tx = self.cx + self.r * math.cos(cur_rad)
        ty = self.cy - self.r * math.sin(cur_rad)
        self.create_oval(
            tx - 5, ty - 5, tx + 5, ty + 5,
            fill="#ffffff" if self.enabled else MUTED, outline=color, width=2,
        )

        # Center Numerical Readout
        val_str = f"{int(self.val)}{self.unit}" if self.enabled else "OFF"
        self.create_text(
            self.cx, self.cy - 7, text=val_str,
            fill=FG if self.enabled else MUTED, font=("sans-serif", 15, "bold"),
        )
        self.create_text(
            self.cx, self.cy + 13, text=self.label_text,
            fill=MUTED, font=("sans-serif", 7, "bold"),
        )


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
        self.root.geometry("980x790")
        self.root.minsize(860, 680)
        self.root.configure(bg=BG)

        self.sys_info = get_system_info()
        self.font_family = resolve_system_font()
        self.config = load_config()
        self.registry = get_registry(self.sys_info["cpu"])

        self.history: dict[str, list[float]] = {"cpu": [], "gpu": []}
        self.busy = False
        self.profile_change_busy = False
        self.profile_buttons: dict[str, ttk.Button] = {}
        self.cpu_stat_previous: tuple[float, float] | None = None
        self.last_poll_ms = 0.0

        # Push Notification state
        self.alert_latched = {"cpu": False, "gpu": False, "crit": False}
        self.last_alert_time = {"cpu": 0.0, "gpu": 0.0, "crit": 0.0}

        # Thermal Target Limiter state
        self.target_temp_regulating = False
        self.base_profile_before_target: str | None = None

        # GUI reactive variables
        self.target_temp_var = tk.BooleanVar(value=bool(self.config["target_temp_enabled"]))
        self.target_temp_val = float(self.config["target_temp_c"])
        self.alerts_var = tk.BooleanVar(value=bool(self.config["alerts_enabled"]))
        self.cpu_alert_var = tk.StringVar(value=str(self.config["cpu_alert_c"]))
        self.gpu_alert_var = tk.StringVar(value=str(self.config["gpu_alert_c"]))
        self.cpu_load_var = tk.StringVar(value="CPU load: —")
        self.target_status_var = tk.StringVar(value="Target Limiter: disabled.")
        self.status_var = tk.StringVar(value="Hardware telemetry active.")
        self.last_update_var = tk.StringVar(value="Initializing hardware interfaces...")
        self.boost_status_var = tk.StringVar(value="CPU Turbo Boost: Detecting...")
        self.alert_badge_var = tk.StringVar(value="🔔 ALERTS: ON" if self.config["alerts_enabled"] else "🔕 ALERTS: OFF")

        # Telemetry summary chips
        self.chip_vars = {
            "cpu": tk.StringVar(value="CPU: — °C"),
            "gpu": tk.StringVar(value="GPU: — °C"),
            "nvme": tk.StringVar(value="SSD: — °C"),
            "acpi": tk.StringVar(value="ACPI: — °C"),
            "net": tk.StringVar(value="Wi-Fi: — °C"),
            "bat": tk.StringVar(value="Battery: — %"),
        }

        self._configure_style()
        self._build_ui()
        self._update_profile_controls()
        self._update_boost_display()

        # Immediate synchronous first read to eliminate any startup latency
        try:
            init_temps, init_all = self.registry.poll_all()
            self._apply_telemetry(init_temps, init_all, None, 0.0)
        except Exception:
            pass

        self.root.after(POLL_MS, self.refresh)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        base_font = (self.font_family, 9)

        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=PANEL, foreground=FG, font=base_font)
        style.configure("TButton", font=(self.font_family, 9), padding=(8, 6),
                        background=PANEL_ALT, foreground=FG, borderwidth=0)
        style.map("TButton", background=[("active", "#2c3b4f"), ("disabled", "#19222e")])

        # Step buttons under knobs
        style.configure("Step.TButton", font=(self.font_family, 9, "bold"), padding=(6, 2),
                        background=PANEL_ALT, foreground=FG)

        # Segmented Profile buttons
        style.configure("Profile.TButton", font=(self.font_family, 9, "bold"), padding=(12, 9),
                        background=PANEL_ALT, foreground=FG)
        style.configure("Selected.Profile.TButton", font=(self.font_family, 9, "bold"), padding=(12, 9),
                        background="#1a4f38", foreground="#b6fcd5")

        # Tabs
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, font=(self.font_family, 9, "bold"),
                        padding=(16, 7), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL_ALT)], foreground=[("selected", FG)])

        # Treeview
        style.configure("Treeview", background=PANEL, foreground=FG, fieldbackground=PANEL,
                        font=(self.font_family, 8), rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading", background=PANEL_ALT, foreground=FG,
                        font=(self.font_family, 8, "bold"), padding=(5, 4))
        style.map("Treeview.Heading", background=[("active", "#283446")])

    def _card(self, parent: tk.Widget, title: str, subtitle: str | None = None) -> tk.Frame:
        card = tk.Frame(parent, bg=PANEL, padx=14, pady=10, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=14, pady=4)
        tk.Label(card, text=title, bg=PANEL, fg=FG, font=(self.font_family, 9, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(card, text=subtitle, bg=PANEL, fg=MUTED, font=(self.font_family, 7)).pack(anchor="w", pady=(1, 6))
        return card

    def _build_ui(self) -> None:
        # Header Bar
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=(10, 4))

        left = tk.Frame(header, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="THERMAL CONTROL CENTER", bg=BG, fg=FG,
                 font=(self.font_family, 16, "bold")).pack(anchor="w")
        tk.Label(left, text=f"{self.sys_info['summary']}  •  {self.sys_info['os']}",
                 bg=BG, fg=MUTED, font=(self.font_family, 8)).pack(anchor="w")

        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        self.profile_badge = tk.Label(right, text="PROFILE: —", bg=PANEL_ALT, fg=ACCENT,
                                      font=(self.font_family, 8, "bold"), padx=10, pady=5)
        self.profile_badge.pack(side="right", padx=(6, 0))
        self.alert_badge = tk.Label(right, textvariable=self.alert_badge_var, bg=PANEL_ALT, fg=GREEN,
                                    font=(self.font_family, 8, "bold"), padx=10, pady=5)
        self.alert_badge.pack(side="right")

        # Tabbed Deck
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=14, pady=(2, 6))

        self.tab_dashboard = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_dashboard, text="  🎛️  Dashboard & Controls  ")
        self._build_tab_dashboard()

        self.tab_sensors = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_sensors, text="  🖥️  All Hardware Sensors  ")
        self._build_tab_sensors()

        self.tab_alerts = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_alerts, text="  🔔  Push Alerts & History  ")
        self._build_tab_alerts()

        # Footer Status Bar
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=16, pady=(2, 8))
        self.perf_var = tk.StringVar(value="⚡ Poll latency: — ms • Sensors: —")
        tk.Label(footer, textvariable=self.perf_var, bg=BG, fg=CYAN, font=(self.font_family, 8)).pack(side="left")
        tk.Label(footer, textvariable=self.last_update_var, bg=BG, fg=MUTED, font=(self.font_family, 8)).pack(side="right")

    def _build_tab_dashboard(self) -> None:
        # 1. Rotary Knob Deck (Two Professional Knobs Side-by-Side)
        knob_deck = tk.Frame(self.tab_dashboard, bg=PANEL, padx=14, pady=10, highlightbackground=BORDER, highlightthickness=1)
        knob_deck.pack(fill="x", padx=14, pady=(6, 4))

        tk.Label(knob_deck, text="HARDWARE TEMPERATURE & COOLING CONTROLLERS", bg=PANEL, fg=FG,
                 font=(self.font_family, 9, "bold")).pack(anchor="w")

        knob_cols = tk.Frame(knob_deck, bg=PANEL)
        knob_cols.pack(fill="x", pady=(6, 2))

        # Left Knob: Target Temperature Limiter
        left_col = tk.Frame(knob_cols, bg=PANEL)
        left_col.pack(side="left", fill="both", expand=True, padx=8)

        self.target_knob = KnobControl(
            left_col, min_val=45, max_val=90, initial=self.target_temp_val, step=1,
            unit="°C", label="TARGET", command=self._on_target_knob_change,
        )
        self.target_knob.pack(anchor="center")

        step_row1 = tk.Frame(left_col, bg=PANEL)
        step_row1.pack(pady=(2, 4))
        ttk.Button(step_row1, text=" - ", style="Step.TButton", command=lambda: self.target_knob.step_val(-1)).pack(side="left", padx=2)
        ttk.Button(step_row1, text=" + ", style="Step.TButton", command=lambda: self.target_knob.step_val(1)).pack(side="left", padx=2)

        tk.Checkbutton(
            left_col, text="Maintain Target Limit", variable=self.target_temp_var,
            command=self.toggle_target_temp, bg=PANEL, fg=FG, activebackground=PANEL,
            activeforeground=FG, selectcolor=PANEL_ALT, font=(self.font_family, 8, "bold"),
        ).pack(anchor="center")
        tk.Label(left_col, textvariable=self.target_status_var, bg=PANEL, fg=AMBER, font=(self.font_family, 7)).pack(anchor="center")

        # Right Knob: Fan & Cooling Control
        right_col = tk.Frame(knob_cols, bg=PANEL)
        right_col.pack(side="right", fill="both", expand=True, padx=8)

        self.fan_knob = KnobControl(
            right_col, min_val=0, max_val=100, initial=float(self.config.get("fan_speed_pct", 80)),
            step=5, unit="%", label="FAN PWM", command=self._on_fan_knob_change,
        )
        self.fan_knob.pack(anchor="center")
        self.fan_knob.set_enabled(bool(self.config.get("fan_manual", False)))

        step_row2 = tk.Frame(right_col, bg=PANEL)
        step_row2.pack(pady=(2, 4))
        ttk.Button(step_row2, text=" - ", style="Step.TButton", command=lambda: self.fan_knob.step_val(-1)).pack(side="left", padx=2)
        ttk.Button(step_row2, text=" + ", style="Step.TButton", command=lambda: self.fan_knob.step_val(1)).pack(side="left", padx=2)

        self.fan_manual_var = tk.BooleanVar(value=bool(self.config.get("fan_manual", False)))
        tk.Checkbutton(
            right_col, text="Manual Fan Speed", variable=self.fan_manual_var,
            command=self.toggle_fan_manual, bg=PANEL, fg=FG, activebackground=PANEL,
            activeforeground=FG, selectcolor=PANEL_ALT, font=(self.font_family, 8, "bold"),
        ).pack(anchor="center")

        btn_boost = ttk.Button(right_col, text="❄️ Cooler Boost (100% Cooling)", command=self.activate_cooler_boost)
        btn_boost.pack(anchor="center", pady=(2, 0))

        # 2. Platform Profiles & CPU Tuning Card
        ctrl_card = self._card(self.tab_dashboard, "PLATFORM PROFILES & CPU THERMAL TUNING",
                               "Firmware-managed thermal policies and CPU Turbo Boost control.")

        prof_row = tk.Frame(ctrl_card, bg=PANEL)
        prof_row.pack(fill="x", pady=(2, 6))

        available = ThermalControlManager.get_available_profiles() or ["quiet", "balanced", "performance"]
        for p in available:
            title, desc = PROFILE_DESCRIPTIONS.get(p, (p.title(), "Operating mode"))
            btn = ttk.Button(
                prof_row, text=f"{title}\n{desc}", style="Profile.TButton",
                command=lambda val=p: self.set_profile(val),
            )
            btn.pack(side="left", fill="x", expand=True, padx=(0, 6))
            self.profile_buttons[p] = btn

        cpu_row = tk.Frame(ctrl_card, bg=PANEL)
        cpu_row.pack(fill="x", pady=(2, 0))

        self.btn_boost = ttk.Button(cpu_row, text="Toggle CPU Boost", command=self.toggle_cpu_boost)
        self.btn_boost.pack(side="left", padx=(0, 10))
        tk.Label(cpu_row, textvariable=self.boost_status_var, bg=PANEL, fg=FG, font=(self.font_family, 8, "bold")).pack(side="left", padx=(0, 16))

        tk.Label(cpu_row, text="Power Preference:", bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(side="left", padx=(10, 4))
        for pref, lbl in (("power", "Power Saver"), ("balance_performance", "Balanced"), ("performance", "Performance")):
            ttk.Button(cpu_row, text=lbl, command=lambda p=pref: self.set_cpu_epp(p)).pack(side="left", padx=2)

        # 3. Live Hardware Telemetry Summary Chips
        chip_card = self._card(self.tab_dashboard, "LIVE TELEMETRY SUMMARY", "Real-time metrics across all hardware components.")
        chip_row = tk.Frame(chip_card, bg=PANEL)
        chip_row.pack(fill="x", pady=2)

        colors = [GREEN, ACCENT, AMBER, MUTED, CYAN, PURPLE]
        for idx, (key, var) in enumerate(self.chip_vars.items()):
            chip = tk.Frame(chip_row, bg=PANEL_ALT, padx=10, pady=6, highlightbackground=BORDER, highlightthickness=1)
            chip.grid(row=0, column=idx, sticky="nsew", padx=3)
            chip_row.grid_columnconfigure(idx, weight=1)
            tk.Label(chip, textvariable=var, bg=PANEL_ALT, fg=colors[idx], font=(self.font_family, 9, "bold")).pack()

    def _build_tab_sensors(self) -> None:
        table_card = tk.Frame(self.tab_sensors, bg=PANEL, padx=12, pady=10, highlightbackground=BORDER, highlightthickness=1)
        table_card.pack(fill="both", expand=True, padx=14, pady=8)

        tk.Label(table_card, text="SYSTEM HARDWARE SENSOR REGISTRY", bg=PANEL, fg=FG,
                 font=(self.font_family, 9, "bold")).pack(anchor="w")
        tk.Label(table_card, text="Kernel telemetry readings, warning points, and critical thermal shutdown limits.",
                 bg=PANEL, fg=MUTED, font=(self.font_family, 7)).pack(anchor="w", pady=(1, 6))

        tree_container = tk.Frame(table_card, bg=PANEL)
        tree_container.pack(fill="both", expand=True)

        cols = ("cat", "dev", "sensor", "reading", "limits", "status")
        self.hw_tree = ttk.Treeview(tree_container, columns=cols, show="headings", height=12)
        self.hw_tree.heading("cat", text="Category")
        self.hw_tree.heading("dev", text="Component")
        self.hw_tree.heading("sensor", text="Sensor / Channel")
        self.hw_tree.heading("reading", text="Live Value")
        self.hw_tree.heading("limits", text="Warning / Critical Limits")
        self.hw_tree.heading("status", text="Health Status")

        self.hw_tree.column("cat", width=100, anchor="w")
        self.hw_tree.column("dev", width=220, anchor="w")
        self.hw_tree.column("sensor", width=140, anchor="w")
        self.hw_tree.column("reading", width=100, anchor="center")
        self.hw_tree.column("limits", width=150, anchor="center")
        self.hw_tree.column("status", width=90, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.hw_tree.yview)
        self.hw_tree.configure(yscrollcommand=tree_scroll.set)
        self.hw_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.hw_tree.tag_configure("normal", foreground="#e6edf3")
        self.hw_tree.tag_configure("warm", foreground="#f4b860")
        self.hw_tree.tag_configure("hot", foreground="#ff7b72")
        self.hw_tree.tag_configure("critical", foreground="#ff4d4d")

    def _build_tab_alerts(self) -> None:
        # Chart Card
        chart_card = self._card(self.tab_alerts, "ROLLING THERMAL TRENDS", "Continuous rolling temperature history.")
        self.chart = tk.Canvas(chart_card, height=125, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, pady=(2, 0))
        self.chart.bind("<Configure>", lambda _e: self.draw_chart())

        # Push Notification Configuration Card
        alert_card = self._card(
            self.tab_alerts, "DESKTOP PUSH NOTIFICATIONS FOR TEMPERATURE ALERTS",
            "Dispatches high-priority desktop push notifications via notify-send when hardware exceeds thresholds."
        )

        row1 = tk.Frame(alert_card, bg=PANEL)
        row1.pack(fill="x", pady=3)

        tk.Checkbutton(
            row1, text="Enable High-Temperature Push Notifications", variable=self.alerts_var,
            command=self.toggle_alerts, bg=PANEL, fg=FG, activebackground=PANEL,
            activeforeground=FG, selectcolor=PANEL_ALT, font=(self.font_family, 8, "bold"),
        ).pack(side="left", padx=(0, 16))

        tk.Label(row1, text="CPU Alert Threshold (°C):", bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(side="left")
        ttk.Entry(row1, textvariable=self.cpu_alert_var, width=5).pack(side="left", padx=(4, 12))

        tk.Label(row1, text="GPU Alert Threshold (°C):", bg=PANEL, fg=MUTED, font=(self.font_family, 8)).pack(side="left")
        ttk.Entry(row1, textvariable=self.gpu_alert_var, width=5).pack(side="left", padx=(4, 14))

        ttk.Button(row1, text="Save Settings", command=self.save_settings).pack(side="left", padx=4)
        ttk.Button(row1, text="🔔 Test Push Notification", command=self.test_push_notification).pack(side="left", padx=4)

        row2 = tk.Frame(alert_card, bg=PANEL)
        row2.pack(fill="x", pady=(6, 2))
        tk.Label(
            row2,
            text="Automatic trip point protection: Critical push notifications fire if any hardware reaches its kernel-reported trip limit.\n"
                 "Notifications automatically rearm once the hardware has cooled safely by 5°C (anti-spam hysteresis).",
            bg=PANEL, fg=MUTED, font=(self.font_family, 7), justify="left",
        ).pack(anchor="w")

    # -------------------------------------------------------------------------
    # Interactions & Callbacks
    # -------------------------------------------------------------------------

    def _on_target_knob_change(self, val: float) -> None:
        self.target_temp_val = round(val, 1)
        self.config["target_temp_c"] = self.target_temp_val
        if self.target_temp_var.get():
            self.target_status_var.set(f"Maintaining ceiling at {self.target_temp_val:.0f}°C.")

    def _on_fan_knob_change(self, val: float) -> None:
        speed_pct = int(val)
        self.config["fan_speed_pct"] = speed_pct
        if self.fan_manual_var.get():
            pwm_val = int(speed_pct / 100.0 * 255.0)
            for p in self.registry.fan_pwm_files:
                ThermalControlManager.set_fan_pwm(p, pwm_val)

    def toggle_target_temp(self) -> None:
        enabled = bool(self.target_temp_var.get())
        self.config["target_temp_enabled"] = enabled
        self.target_knob.set_enabled(enabled)
        save_config(self.config)

        if enabled:
            self.target_status_var.set(f"Target Limiter: active ({self.target_temp_val:.0f}°C).")
        else:
            self.target_status_var.set("Target Limiter: disabled.")
            if self.target_temp_regulating:
                self.target_temp_regulating = False
                base = self.base_profile_before_target or "balanced"
                ThermalControlManager.set_profile(base, automatic=True)

    def toggle_fan_manual(self) -> None:
        manual = bool(self.fan_manual_var.get())
        self.config["fan_manual"] = manual
        self.fan_knob.set_enabled(manual)
        save_config(self.config)

        if manual:
            self._on_fan_knob_change(self.fan_knob.val)
        else:
            for p in self.registry.fan_pwm_files:
                ThermalControlManager.set_fan_pwm(p, "auto")

    def toggle_alerts(self) -> None:
        enabled = bool(self.alerts_var.get())
        self.config["alerts_enabled"] = enabled
        self.alert_badge_var.set("🔔 ALERTS: ON" if enabled else "🔕 ALERTS: OFF")
        self.alert_badge.configure(fg=GREEN if enabled else MUTED)
        save_config(self.config)

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

    def set_profile(self, profile: str) -> None:
        if self.profile_change_busy:
            return
        self.profile_change_busy = True
        self.status_var.set(f"Switching to {profile} profile...")
        self.root.update_idletasks()

        def worker() -> None:
            success, detail = ThermalControlManager.set_profile(profile, automatic=False)
            self.root.after(0, lambda: self._on_profile_result(profile, success, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _on_profile_result(self, profile: str, success: bool, detail: str) -> None:
        self.profile_change_busy = False
        if success:
            self.status_var.set(f"Thermal profile switched to {profile}.")
        else:
            self.status_var.set(f"Could not switch profile: {detail}")
            messagebox.showwarning(APP_NAME, f"Could not change profile.\n{detail}")
        self._update_profile_controls()

    def test_push_notification(self) -> None:
        ok = dispatch_push_notification(
            "🔥 Thermal Alert Test",
            "Push notification system is working! You will be alerted when temperatures rise.",
            urgency="critical",
        )
        if ok:
            self.status_var.set("Test push notification dispatched to desktop.")
        else:
            messagebox.showerror(APP_NAME, "Could not dispatch push notification (ensure notify-send is installed).")

    def save_settings(self) -> None:
        try:
            cpu_a = float(self.cpu_alert_var.get())
            gpu_a = float(self.gpu_alert_var.get())
            if not (40.0 <= cpu_a <= 115.0 and 40.0 <= gpu_a <= 115.0):
                raise ValueError("Thresholds must be between 40°C and 115°C.")
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"Invalid temperature threshold: {exc}")
            return

        self.config.update({
            "cpu_alert_c": cpu_a,
            "gpu_alert_c": gpu_a,
            "alerts_enabled": bool(self.alerts_var.get()),
            "target_temp_enabled": bool(self.target_temp_var.get()),
            "target_temp_c": self.target_temp_val,
        })
        if save_config(self.config):
            self.status_var.set(f"Settings saved to {CONFIG_PATH}")
        else:
            messagebox.showerror(APP_NAME, "Could not save settings.")

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

        for p, btn in self.profile_buttons.items():
            btn.configure(style="Selected.Profile.TButton" if p == current else "Profile.TButton")

        if current:
            self.profile_badge.configure(text=f"PROFILE: {current.upper()}")

    # -------------------------------------------------------------------------
    # High-Performance Polling Loop
    # -------------------------------------------------------------------------

    def refresh(self) -> None:
        if self.busy:
            self.root.after(POLL_MS, self.refresh)
            return
        self.busy = True

        def worker() -> None:
            t0 = time.perf_counter()
            temps, all_hw = self.registry.poll_all()
            load = self._cpu_load()
            duration_ms = (time.perf_counter() - t0) * 1000.0
            self.root.after(0, lambda: self._apply_telemetry(temps, all_hw, load, duration_ms))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_telemetry(
        self,
        temperatures: dict[str, float],
        all_hw: list[dict[str, Any]],
        load: float | None,
        duration_ms: float,
    ) -> None:
        # 1. Update Chips
        self.chip_vars["cpu"].set(f"CPU: {temperatures.get('cpu', 0.0):.1f}°C" if "cpu" in temperatures else "CPU: —")
        self.chip_vars["gpu"].set(f"GPU: {temperatures.get('gpu', 0.0):.1f}°C" if "gpu" in temperatures else "GPU: —")
        self.chip_vars["nvme"].set(f"SSD: {temperatures.get('nvme', 0.0):.1f}°C" if "nvme" in temperatures else "SSD: —")
        self.chip_vars["acpi"].set(f"ACPI: {temperatures.get('acpi', 0.0):.1f}°C" if "acpi" in temperatures else "ACPI: —")

        net_s = next((s for s in all_hw if s["category"] == "network"), None)
        self.chip_vars["net"].set(f"Wi-Fi: {net_s['value']:.1f}°C" if net_s else "Wi-Fi: —")

        if self.registry.has_battery:
            self.chip_vars["bat"].set(f"Battery: {self.registry.battery_pct:.0f}% ({self.registry.battery_status[:4]})")
        else:
            self.chip_vars["bat"].set("Power: AC Mains")

        # 2. History trends
        for key in ("cpu", "gpu"):
            if key in temperatures:
                self.history[key].append(temperatures[key])
                self.history[key] = self.history[key][-HISTORY_LIMIT:]

        # 3. Target Temperature Limiter
        self.evaluate_target_temperature(temperatures)

        # 4. Push Alerts Evaluation
        self.evaluate_push_alerts(temperatures, all_hw)

        # 5. Treeview Update (Selective)
        self._update_treeview_selective(all_hw)

        # 6. Performance & Footprint Indicator
        self.perf_var.set(f"⚡ Telemetry I/O: {duration_ms:.1f} ms • Sensors: {len(all_hw)} • Load: {load or 0:.0f}%")
        self.last_update_var.set(f"Last update: {datetime.now().strftime('%H:%M:%S')} • Poll: 2s")

        self._update_profile_controls()
        self._update_boost_display()
        self.draw_chart()
        self.busy = False
        self.root.after(POLL_MS, self.refresh)

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

    def evaluate_target_temperature(self, temperatures: dict[str, float]) -> None:
        """Regulate platform cooling to keep temps within the target ceiling."""
        if not self.target_temp_var.get():
            return

        cpu_t = temperatures.get("cpu", 0.0)
        gpu_t = temperatures.get("gpu", 0.0)
        hottest = max(cpu_t, gpu_t)
        if hottest <= 0.0:
            return

        target_c = self.target_temp_val
        if hottest >= target_c:
            if not self.target_temp_regulating:
                self.target_temp_regulating = True
                self.base_profile_before_target = ThermalControlManager.get_current_profile()
                ThermalControlManager.set_profile("quiet", automatic=True)
                ThermalControlManager.set_cpu_epp("power")

            self.target_status_var.set(
                f"⚠️ Regulating: Hottest sensor is {hottest:.1f}°C (exceeds {target_c:.0f}°C). Cooling active."
            )
        elif hottest <= (target_c - TARGET_TEMP_HYSTERESIS):
            if self.target_temp_regulating:
                self.target_temp_regulating = False
                base = self.base_profile_before_target or "balanced"
                ThermalControlManager.set_profile(base, automatic=True)
                ThermalControlManager.set_cpu_epp("balance_performance")

            self.target_status_var.set(
                f"✓ Target Maintained: Hottest sensor is {hottest:.1f}°C (within {target_c:.0f}°C limit)."
            )

    def evaluate_push_alerts(self, temperatures: dict[str, float], all_hw: list[dict[str, Any]]) -> None:
        """Evaluate high-temperature push notification triggers with anti-spam cooldown."""
        if not self.alerts_var.get():
            return

        now = time.monotonic()
        cpu_t = temperatures.get("cpu")
        gpu_t = temperatures.get("gpu")
        cpu_limit = float(self.config["cpu_alert_c"])
        gpu_limit = float(self.config["gpu_alert_c"])

        # CPU Threshold Alert
        if cpu_t and cpu_t >= cpu_limit:
            if not self.alert_latched["cpu"] or (now - self.last_alert_time["cpu"] > ALERT_COOLDOWN_SECONDS):
                self.alert_latched["cpu"] = True
                self.last_alert_time["cpu"] = now
                dispatch_push_notification(
                    "🔥 High CPU Temperature Alert!",
                    f"CPU temperature reached {cpu_t:.1f}°C (Threshold: {cpu_limit:.1f}°C). Active cooling recommended.",
                    urgency="critical",
                )
        elif cpu_t and cpu_t <= (cpu_limit - ALERT_RESET_HYSTERESIS):
            if self.alert_latched["cpu"]:
                self.alert_latched["cpu"] = False
                dispatch_push_notification(
                    "✅ CPU Temperature Normalized",
                    f"CPU cooled down to {cpu_t:.1f}°C (Safe thermal limits restored).",
                    urgency="normal",
                )

        # GPU Threshold Alert
        if gpu_t and gpu_t >= gpu_limit:
            if not self.alert_latched["gpu"] or (now - self.last_alert_time["gpu"] > ALERT_COOLDOWN_SECONDS):
                self.alert_latched["gpu"] = True
                self.last_alert_time["gpu"] = now
                dispatch_push_notification(
                    "🔥 High GPU Temperature Alert!",
                    f"GPU temperature reached {gpu_t:.1f}°C (Threshold: {gpu_limit:.1f}°C).",
                    urgency="critical",
                )
        elif gpu_t and gpu_t <= (gpu_limit - ALERT_RESET_HYSTERESIS):
            if self.alert_latched["gpu"]:
                self.alert_latched["gpu"] = False
                dispatch_push_notification(
                    "✅ GPU Temperature Normalized",
                    f"GPU cooled down to {gpu_t:.1f}°C.",
                    urgency="normal",
                )

        # Hardware Critical Trip Point Alert (Kernel-reported crit limits)
        for s in all_hw:
            c = s.get("crit_temp")
            v = s.get("value")
            if c and v and v >= c:
                if not self.alert_latched["crit"] or (now - self.last_alert_time["crit"] > ALERT_COOLDOWN_SECONDS):
                    self.alert_latched["crit"] = True
                    self.last_alert_time["crit"] = now
                    dispatch_push_notification(
                        "🚨 Hardware Critical Temperature Alert!",
                        f"{s['device_name']} ({s['sensor_name']}) hit {v:.1f}°C! (Kernel critical limit: {c:.1f}°C).",
                        urgency="critical",
                    )
                break

    def _update_treeview_selective(self, all_hw: list[dict[str, Any]]) -> None:
        """Update treeview rows only when values change to eliminate layout reflow overhead."""
        existing = set(self.hw_tree.get_children())
        updated = set()

        cat_names = {
            "cpu": "Processor", "gpu": "Graphics", "storage": "Storage Drive",
            "motherboard": "Motherboard", "network": "Wireless/NIC", "fan": "Cooling Fan",
        }

        for s in all_hw:
            row_id = s["id"]
            cat_str = cat_names.get(s["category"], s["category"].title())
            val_str = f"{s['value']:.1f} {s['unit']}" if s["unit"] != "%" else f"{s['value']:.0f}%"

            lim_str = "—"
            if s.get("crit_temp"):
                lim_str = f"Crit: {s['crit_temp']:.1f}°C"
                if s.get("max_temp"):
                    lim_str = f"Max: {s['max_temp']:.1f} / {lim_str}"
            elif s.get("max_temp"):
                lim_str = f"Max: {s['max_temp']:.1f}°C"

            status_str = s["status"].upper()
            values = (cat_str, s["device_name"], s["sensor_name"], val_str, lim_str, status_str)

            if row_id in existing:
                self.hw_tree.item(row_id, values=values, tags=(s["status"],))
            else:
                self.hw_tree.insert("", "end", iid=row_id, values=values, tags=(s["status"],))
            updated.add(row_id)

        for old in existing - updated:
            self.hw_tree.delete(old)

    def draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 100)
        left, right, top, bottom = 38, width - 12, 10, height - 18
        values = self.history["cpu"] + self.history["gpu"]
        if not values:
            canvas.create_text(width / 2, height / 2, text="Collecting temperature history…",
                               fill=MUTED, font=(self.font_family, 8))
            return

        low, high = min(20.0, min(values) - 5), max(90.0, max(values) + 5)
        if high <= low:
            high = low + 10

        for tick in range(20, 101, 20):
            y = bottom - ((tick - low) / (high - low)) * (bottom - top)
            if top <= y <= bottom:
                canvas.create_line(left, y, right, y, fill="#1e2530", dash=(2, 4))
                canvas.create_text(18, y, text=str(tick), fill=MUTED, font=(self.font_family, 7))

        if self.target_temp_var.get():
            t_val = self.target_temp_val
            if low <= t_val <= high:
                t_y = bottom - ((t_val - low) / (high - low)) * (bottom - top)
                canvas.create_line(left, t_y, right, t_y, fill=AMBER, dash=(4, 2), width=1)
                canvas.create_text(right - 28, t_y - 7, text=f"Target {t_val:.0f}°C", fill=AMBER,
                                   font=(self.font_family, 7, "bold"))

        for key, color in (("cpu", GREEN), ("gpu", ACCENT)):
            series = self.history[key]
            if len(series) < 2:
                continue
            pts = []
            for idx, val in enumerate(series):
                x = left + idx / max(HISTORY_LIMIT - 1, 1) * (right - left)
                y = bottom - ((val - low) / (high - low)) * (bottom - top)
                pts.extend((x, y))
            canvas.create_line(*pts, fill=color, width=2, smooth=True)


# ============================================================================
# CLI and Terminal Operation
# ============================================================================

def get_telemetry_snapshot() -> dict[str, Any]:
    sys_info = get_system_info()
    reg = get_registry(sys_info["cpu"])
    temperatures, all_hw = reg.poll_all()
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
        "all_hardware": all_hw,
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
        val_str = f"{s['value']:.1f} {s['unit']}" if s["unit"] != "%" else f"{s['value']:.0f}%"
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

    fan_sensors = [s for s in all_hw if s["category"] == "fan"]
    if fan_sensors:
        fan_strs = [f"{f['sensor_name']}: {int(f['value'])} RPM" for f in fan_sensors if f["value"] > 0]
        if fan_strs:
            print(f"  Cooling Fans      : {', '.join(fan_strs)}")
        else:
            print(f"  Cooling Fans      : {len(fan_sensors)} tachometer(s) detected (idle / passive cooling)")
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
    parser.add_argument("--test-alert", action="store_true", help="Send a test desktop push notification")
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

    if args.test_alert:
        ok = dispatch_push_notification(
            "🔥 Thermal Alert Test",
            "This is a test high-temperature push notification from Thermals.",
            urgency="critical",
        )
        print("[SUCCESS] Dispatched test push notification." if ok else "[FAILED] Could not send notification.")
        return 0 if ok else 1

    if args.list_hardware:
        sys_info = get_system_info()
        reg = get_registry(sys_info["cpu"])
        _, sensors = reg.poll_all()
        print(f"Detected {len(sensors)} hardware sensors on {sys_info['summary']}:")
        for s in sensors:
            print(f"  [{s['category'].upper():<11}] {s['device_name']:<30} | {s['sensor_name']:<20}: {s['value']} {s['unit']} ({s['status']})")
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
        reg = get_registry()
        if not reg.fan_pwm_files:
            print("[FAILED] No controllable PWM fans detected on this system.")
            return 1
        success, msg = ThermalControlManager.set_fan_pwm(reg.fan_pwm_files[0], args.set_fan)
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
