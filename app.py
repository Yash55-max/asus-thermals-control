#!/usr/bin/env python3
"""Thermals - Cross-Distribution Linux Hardware Telemetry and Thermal Profile Manager.

Supports all Linux environments:
- Multi-architecture CPU/GPU/Storage/ACPI telemetry (Intel, AMD, ARM64, NVIDIA, Apple Silicon)
- ACPI platform_profile sysfs and power-profiles-daemon backends
- Dual GUI (Tkinter) and CLI (terminal monitor, status, JSON, one-shot) operation
- Dynamic desktop font discovery and High-DPI support
- Automated profile switching with anti-flapping hysteresis
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
__version__ = "1.1.0"

# Filesystem paths
PROFILE_STD_PATH = Path("/sys/firmware/acpi/platform_profile")
PROFILE_STD_CHOICES = Path("/sys/firmware/acpi/platform_profile_choices")
HW_MON_PATH = Path("/sys/class/hwmon")
THERMAL_CLASS_PATH = Path("/sys/class/thermal")
HELPER_PATH = Path("/usr/local/sbin/asus-thermal-profile")
CONFIG_DIR = Path.home() / ".config" / "asus-thermal-control-center"
CONFIG_PATH = CONFIG_DIR / "config.json"

POLL_MS = 2000
HISTORY_LIMIT = 60
AUTO_CONFIRM_SAMPLES = 3
AUTO_MIN_DWELL_SECONDS = 60
ALERT_RESET_HYSTERESIS = 5.0

# UI Theme Colors
BG, PANEL, PANEL_ALT = "#10151d", "#18212d", "#202b39"
FG, MUTED, ACCENT = "#e8edf4", "#9aa8b8", "#63a6ff"
GREEN, AMBER, RED = "#42c88a", "#f4b860", "#ff6b6b"

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
}

PROFILE_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "quiet": ("Quiet", "Lower noise & heat"),
    "low-power": ("Low Power", "Energy saving"),
    "power-saver": ("Power Saver", "Battery optimized"),
    "cool": ("Cool", "Optimized cooling"),
    "balanced": ("Balanced", "Standard everyday use"),
    "balanced-performance": ("Balanced+", "Higher responsiveness"),
    "performance": ("Performance", "Maximum throughput"),
}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def get_system_info() -> dict[str, str]:
    """Dynamically detect host vendor, product model, CPU model, and OS release."""
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

    # Device Tree model for ARM / Raspberry Pi / Chromebooks
    if not model:
        raw_dt = read_text(Path("/proc/device-tree/model"))
        if raw_dt:
            model = raw_dt.replace("\x00", "").strip()

    # Normalize vendor name
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

    # 2. CPU Model detection
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if any(line.lower().startswith(k) for k in ("model name", "hardware", "processor")):
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip():
                        cpu = parts[1].strip()
                        break
    except OSError:
        pass

    if not cpu:
        cpu = platform.processor() or platform.machine() or "Generic CPU"

    # Clean redundant CPU markings
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


def parse_sensor_json(node: Any, parent: str = "") -> list[tuple[str, str, float]]:
    records: list[tuple[str, str, float]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, dict):
                records.extend(parse_sensor_json(value, f"{parent}/{key}"))
            elif key.endswith("_input") and isinstance(value, (int, float)):
                chip = parent.split("/")[0] if parent else "generic"
                label = parent.split("/")[-1] if parent else key
                records.append((chip, label, float(value)))
    return records


def classify_sensor(chip: str, label: str) -> str | None:
    """Classify a hardware sensor reading into cpu, gpu, nvme, or acpi."""
    chip_l, label_l = chip.lower(), label.lower()

    # CPU Sensors (Intel coretemp, AMD k10temp/zenpower, ARM soc/cpu, Apple Silicon macsmc)
    if any(k in chip_l for k in (
        "coretemp", "k10temp", "zenpower", "amd_energy", "cpu_thermal",
        "soc_thermal", "bcm2835", "rockchip", "sunxi", "imx", "macsmc",
        "apple_soc", "intel_rapl"
    )):
        return "cpu"
    if any(k in label_l for k in ("package id", "tctl", "tdie", "cpu", "core 0", "soc", "tccd")):
        return "cpu"

    # GPU Sensors (AMD amdgpu/radeon, NVIDIA nouveau/nvidia, Intel i915/xe, ARM mali/panfrost)
    if any(k in chip_l for k in ("amdgpu", "radeon", "nouveau", "i915", "xe", "panfrost", "mali", "nvidia")):
        return "gpu"
    if any(k in label_l for k in ("edge", "junction", "gpu", "gt", "vram")):
        return "gpu"

    # NVMe / Storage Sensors
    if any(k in chip_l for k in ("nvme", "drivetemp")):
        return "nvme"
    if any(k in label_l for k in ("composite", "drive")):
        return "nvme"

    # ACPI / Motherboard Sensors
    if any(k in chip_l for k in ("acpitz", "asus", "thinkpad", "dell", "hp", "it87", "nct677", "w83627")):
        return "acpi"
    if any(k in label_l for k in ("acpitz", "system", "ambient", "motherboard", "board")):
        return "acpi"

    return None


def collect_sensors() -> dict[str, float]:
    """Collect hardware telemetry across Intel, AMD, ARM, and NVIDIA platforms."""
    found: dict[str, float] = {}

    # 1. Primary extraction via lm-sensors JSON
    sensors_bin = shutil.which("sensors")
    if sensors_bin:
        try:
            result = subprocess.run(
                [sensors_bin, "-j"], check=True, capture_output=True,
                text=True, timeout=4,
            )
            for chip, label, value in parse_sensor_json(json.loads(result.stdout)):
                if not (0.0 < value < 145.0):
                    continue
                category = classify_sensor(chip, label)
                if category:
                    # Prefer package / tctl over individual cores if available
                    if category == "cpu" and any(k in label.lower() for k in ("package id", "tctl", "tdie")):
                        found["cpu"] = value
                    else:
                        found.setdefault(category, value)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass

    # 2. Sysfs hwmon extraction (direct kernel hwmon interface)
    if HW_MON_PATH.exists():
        for hwmon in HW_MON_PATH.glob("hwmon*"):
            name = (read_text(hwmon / "name") or "").lower()
            for input_file in hwmon.glob("temp*_input"):
                raw = read_text(input_file)
                if raw is None:
                    continue
                try:
                    value = float(raw) / 1000.0
                except ValueError:
                    continue
                if not (0.0 < value < 145.0):
                    continue
                label = (read_text(hwmon / input_file.name.replace("_input", "_label")) or "").lower()
                category = classify_sensor(name, label or input_file.name)
                if category:
                    if category == "cpu" and any(k in label for k in ("package id", "tctl", "tdie")):
                        found["cpu"] = value
                    else:
                        found.setdefault(category, value)

    # 3. Kernel Thermal Zones extraction (/sys/class/thermal/thermal_zone*)
    if THERMAL_CLASS_PATH.exists():
        for zone in THERMAL_CLASS_PATH.glob("thermal_zone*"):
            ztype = (read_text(zone / "type") or "").lower()
            raw = read_text(zone / "temp")
            if not raw:
                continue
            try:
                raw_val = float(raw)
                val = raw_val / 1000.0 if raw_val > 1000.0 else raw_val
            except ValueError:
                continue
            if not (0.0 < val < 145.0):
                continue
            if any(k in ztype for k in ("x86_pkg", "cpu", "soc", "core", "bcm2835", "k10temp", "coretemp")):
                found.setdefault("cpu", val)
            elif any(k in ztype for k in ("gpu", "mali", "panfrost")):
                found.setdefault("gpu", val)
            elif "acpi" in ztype:
                found.setdefault("acpi", val)

    # 4. Proprietary NVIDIA Driver Fallback (via nvidia-smi)
    if "gpu" not in found and shutil.which("nvidia-smi"):
        try:
            res = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpu_val = float(res.stdout.strip().splitlines()[0])
                if 0.0 < gpu_val < 135.0:
                    found["gpu"] = gpu_val
        except (subprocess.SubprocessError, OSError, ValueError):
            pass

    return found


def fan_capabilities() -> tuple[list[tuple[str, int]], list[str]]:
    """Return detected fan RPM telemetry and PWM control attributes."""
    rpm_data: list[tuple[str, int]] = []
    pwm_files: list[str] = []

    if HW_MON_PATH.exists():
        for hwmon in HW_MON_PATH.glob("hwmon*"):
            hw_name = read_text(hwmon / "name") or "Fan"
            for path in hwmon.glob("fan*_input"):
                raw = read_text(path)
                speed = 0
                if raw and raw.isdigit():
                    speed = int(raw)
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
        # Fallback when /proc/stat has restricted permissions
        try:
            if hasattr(os, "getloadavg"):
                avg1 = os.getloadavg()[0]
                cores = os.cpu_count() or 1
                estimated_load = min(100.0, max(0.0, (avg1 / cores) * 100.0))
                # Return dummy ticks representing estimated usage
                return 100.0, 100.0 - estimated_load
        except Exception:
            pass
        return None


class ProfileManager:
    """Manages thermal and power profiles across Linux kernels and desktops."""

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

        # Fallback to power-profiles-daemon if active
        if shutil.which("powerprofilesctl"):
            try:
                res = subprocess.run(
                    ["powerprofilesctl", "list"],
                    capture_output=True, text=True, timeout=2,
                )
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
                res = subprocess.run(
                    ["powerprofilesctl", "get"],
                    capture_output=True, text=True, timeout=2,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                pass

        return None

    @classmethod
    def set_profile(cls, profile: str, automatic: bool = False) -> tuple[bool, str]:
        """Apply a thermal profile using direct sysfs, sudo helper, powerprofilesctl, or pkexec."""
        prof_path, choices_path = cls.find_sysfs_paths()

        # 1. Direct sysfs write (if running as root or node is writable)
        if prof_path and os.access(prof_path, os.W_OK):
            try:
                prof_path.write_text(profile + "\n", encoding="utf-8")
                return True, f"Set active profile to {profile}"
            except OSError as exc:
                return False, f"Direct write failed: {exc}"

        # 2. Privileged helper script
        if HELPER_PATH.exists() and shutil.which("sudo"):
            try:
                result = subprocess.run(
                    ["sudo", "-n", str(HELPER_PATH), profile],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    return True, (result.stdout or "Profile applied successfully").strip()
                err = (result.stderr or result.stdout).strip()
                if "password is required" in err.lower() and automatic:
                    return False, "Helper requires password. Run install-helper.sh once."
            except (subprocess.SubprocessError, OSError) as exc:
                return False, str(exc)

        # 3. Desktop powerprofilesctl (works without password for logged-in user)
        if shutil.which("powerprofilesctl"):
            try:
                # Map standard profile names to powerprofilesctl equivalents
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
                    return True, f"Applied {daemon_prof} via power-profiles-daemon"
            except (subprocess.SubprocessError, OSError):
                pass

        # 4. Polkit graphical prompt for manual changes
        if not automatic and prof_path and shutil.which("pkexec"):
            try:
                res = subprocess.run(
                    ["pkexec", "/usr/bin/tee", str(prof_path)],
                    input=profile + "\n", capture_output=True, text=True, timeout=35,
                )
                if res.returncode == 0:
                    return True, f"Thermal profile switched to {profile}"
                return False, (res.stderr or res.stdout).strip()
            except (subprocess.SubprocessError, OSError) as exc:
                return False, str(exc)

        return False, "Profile control interface requires privileges. Run install-helper.sh."


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
    """Tkinter Desktop GUI for Thermals."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x820")
        self.root.minsize(850, 720)
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

        self.auto_var = tk.BooleanVar(value=bool(self.config["auto_enabled"]))
        self.alerts_var = tk.BooleanVar(value=bool(self.config["alerts_enabled"]))
        self.cpu_alert_var = tk.StringVar(value=str(self.config["cpu_alert_c"]))
        self.gpu_alert_var = tk.StringVar(value=str(self.config["gpu_alert_c"]))
        self.cpu_load_var = tk.StringVar(value="CPU load: —")
        self.auto_status_var = tk.StringVar(value="Automatic switching is disabled.")
        self.status_var = tk.StringVar(value="Initializing hardware interfaces...")
        self.last_update_var = tk.StringVar(value="Waiting for first sensor read")
        self.temp_vars = {key: tk.StringVar(value="— °C") for key in ("cpu", "gpu", "nvme", "acpi")}

        self._configure_style()
        self._build_ui()
        self._update_profile_controls()
        self.root.after(300, self.refresh)
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        base_font = (self.font_family, 10)
        style.configure("TButton", font=base_font, padding=(10, 8),
                        background=PANEL_ALT, foreground=FG, borderwidth=0)
        style.map("TButton", background=[("active", "#34465c"), ("disabled", "#26313e")])
        style.configure("Profile.TButton", font=(self.font_family, 10, "bold"), padding=(12, 11),
                        background=PANEL_ALT, foreground=FG)
        style.configure("Selected.Profile.TButton", font=(self.font_family, 10, "bold"),
                        padding=(12, 11), background="#174d3b", foreground="#d9ffec")

    def _section(self, parent: tk.Widget, title: str, subtitle: str | None = None) -> tk.Frame:
        frame = tk.Frame(parent, bg=PANEL, padx=16, pady=13)
        frame.pack(fill="x", padx=24, pady=7)
        tk.Label(frame, text=title, bg=PANEL, fg=FG,
                 font=(self.font_family, 10, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(frame, text=subtitle, bg=PANEL, fg=MUTED,
                     font=(self.font_family, 8)).pack(anchor="w", pady=(3, 9))
        return frame

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=25, pady=(17, 8))
        tk.Label(header, text="THERMAL CONTROL CENTER", bg=BG, fg=FG,
                 font=(self.font_family, 18, "bold")).pack(side="left")
        self.profile_badge = tk.Label(header, text="PROFILE: —", bg=PANEL_ALT, fg=ACCENT,
                                      font=(self.font_family, 9, "bold"), padx=12, pady=8)
        self.profile_badge.pack(side="right")

        # Dynamic System Information Header
        subtitle = tk.Label(
            self.root,
            text=self.sys_info["summary"],
            bg=BG, fg=MUTED, font=(self.font_family, 9)
        )
        subtitle.pack(anchor="w", padx=27)

        # Telemetry Cards
        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=18, pady=8)
        specs = [
            ("cpu", "CPU TEMPERATURE", GREEN),
            ("gpu", "GPU TEMPERATURE", ACCENT),
            ("nvme", "STORAGE DRIVE", AMBER),
            ("acpi", "ACPI / AMBIENT", MUTED),
        ]
        for index, (key, title, color) in enumerate(specs):
            card = tk.Frame(cards, bg=PANEL, padx=13, pady=12)
            card.grid(row=0, column=index, sticky="nsew", padx=5)
            cards.grid_columnconfigure(index, weight=1)
            tk.Label(card, text=title, bg=PANEL, fg=MUTED,
                     font=(self.font_family, 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.temp_vars[key], bg=PANEL, fg=color,
                     font=(self.font_family, 20, "bold")).pack(anchor="w", pady=(7, 1))

        # Dynamic Thermal Profiles Section
        available = ProfileManager.get_available_profiles()
        if available:
            profile_sub = "Firmware-managed thermal policies configured via Linux ACPI platform_profile."
        else:
            profile_sub = "Platform profiles are not exposed by the current hardware/kernel (telemetry active)."

        profile_sec = self._section(self.root, "THERMAL PROFILES", profile_sub)
        self.profile_row = tk.Frame(profile_sec, bg=PANEL)
        self.profile_row.pack(fill="x")

        # Fallback default choices if system hasn't populated choices yet
        display_profiles = available or ["quiet", "balanced", "performance"]
        for key in display_profiles:
            title, desc = PROFILE_DESCRIPTIONS.get(key, (key.replace("-", " ").title(), "Operational profile"))
            button = ttk.Button(
                self.profile_row,
                text=f"{title}\n{desc}",
                style="Profile.TButton",
                command=lambda value=key: self.set_profile(value, automatic=False),
            )
            button.pack(side="left", fill="x", expand=True, padx=(0, 7))
            self.profile_buttons[key] = button

        # Automation Section
        automation = self._section(
            self.root, "AUTOMATIC PROFILE SWITCHING",
            "Monitors CPU load and thermals. High temperatures automatically step down to conservative cooling."
        )
        tk.Checkbutton(
            automation, text="Enable automatic thermal profile selection",
            variable=self.auto_var, command=self.toggle_auto,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=(self.font_family, 9),
        ).pack(anchor="w")
        tk.Label(automation, textvariable=self.cpu_load_var, bg=PANEL, fg=MUTED,
                 font=(self.font_family, 8)).pack(anchor="w", pady=(3, 0))
        tk.Label(automation, textvariable=self.auto_status_var, bg=PANEL, fg=ACCENT,
                 font=(self.font_family, 8)).pack(anchor="w", pady=(3, 0))

        # Temperature Alerts Section
        alerts = self._section(
            self.root, "TEMPERATURE ALERTS",
            "Dispatches desktop alerts when thresholds are exceeded. Alerts re-arm after cooling by 5°C."
        )
        alert_row = tk.Frame(alerts, bg=PANEL)
        alert_row.pack(fill="x")
        tk.Checkbutton(
            alert_row, text="Enable alerts", variable=self.alerts_var,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=(self.font_family, 9),
        ).pack(side="left", padx=(0, 18))
        tk.Label(alert_row, text="CPU °C", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.cpu_alert_var, width=7).pack(side="left", padx=(5, 15))
        tk.Label(alert_row, text="GPU °C", bg=PANEL, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.gpu_alert_var, width=7).pack(side="left", padx=5)
        ttk.Button(alert_row, text="Save settings", command=self.save_settings).pack(side="right")

        # Rolling Chart
        chart = self._section(self.root, "TEMPERATURE HISTORY", "Rolling CPU and GPU telemetry trends")
        self.chart = tk.Canvas(chart, height=140, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, pady=(4, 0))
        self.chart.bind("<Configure>", lambda _event: self.draw_chart())

        # Footer
        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=26, pady=(3, 12))
        self.capability_var = tk.StringVar(value="Checking hardware telemetry...")
        for variable, color in [
            (self.capability_var, AMBER),
            (self.status_var, MUTED),
            (self.last_update_var, MUTED),
        ]:
            tk.Label(footer, textvariable=variable, bg=BG, fg=color,
                     font=(self.font_family, 8)).pack(anchor="w", pady=1)

    def _update_profile_controls(self) -> None:
        current = ProfileManager.get_current_profile()
        available = ProfileManager.get_available_profiles()

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
        })
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(self.config, indent=2) + "\n", encoding="utf-8")
            self.status_var.set(f"Settings saved to {CONFIG_PATH}")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save settings: {exc}")

    def toggle_auto(self) -> None:
        self.config["auto_enabled"] = bool(self.auto_var.get())
        self.auto_candidate = None
        self.auto_candidate_count = 0
        self.save_settings()
        if self.auto_var.get():
            self.auto_status_var.set("Automatic mode enabled; waiting for stable sensor readings.")
        else:
            self.auto_status_var.set("Automatic switching is disabled.")

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
                self._notify(f"{label} temperature high", f"{label} is {value:.1f}°C (alert threshold {threshold:.1f}°C).")
            elif value <= threshold - ALERT_RESET_HYSTERESIS:
                self.alert_latched[sensor] = False

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

    def _resolve_profile_target(self, role: str) -> str | None:
        """Map generic intent (quiet/balanced/performance) to system's actual profiles."""
        avail = ProfileManager.get_available_profiles()
        if not avail:
            return None

        if role == "quiet":
            for cand in ("quiet", "low-power", "power-saver", "cool"):
                if cand in avail:
                    return cand
        elif role == "performance":
            for cand in ("performance", "balanced-performance"):
                if cand in avail:
                    return cand
        elif role == "balanced":
            if "balanced" in avail:
                return "balanced"

        return avail[0]

    def _automatic_target(self, temperatures: dict[str, float], load: float | None) -> str | None:
        cpu = temperatures.get("cpu")
        gpu = temperatures.get("gpu")
        if cpu is None:
            return None

        # Thermal safety overrides performance targets
        hottest = max(cpu, gpu if gpu is not None else cpu)
        if hottest >= float(self.config["auto_critical_c"]):
            return self._resolve_profile_target("quiet")
        if cpu >= float(self.config["auto_hot_cpu_c"]):
            return self._resolve_profile_target("quiet" if cpu >= 82 else "balanced")
        if load is not None and load >= float(self.config["auto_performance_load"]) and cpu < 75 and (gpu is None or gpu < 78):
            return self._resolve_profile_target("performance")
        if load is not None and load <= float(self.config["auto_quiet_load"]) and cpu < float(self.config["auto_cool_cpu_c"]):
            return self._resolve_profile_target("quiet")
        return self._resolve_profile_target("balanced")

    def evaluate_automatic_profile(self, temperatures: dict[str, float], load: float | None) -> None:
        if not self.auto_var.get():
            return
        available = ProfileManager.get_available_profiles()
        if not available:
            self.auto_status_var.set("Automatic mode: thermal profiles are not supported on this platform.")
            return

        target = self._automatic_target(temperatures, load)
        if target is None:
            self.auto_status_var.set("Automatic mode: waiting for CPU temperature/load readings.")
            return

        current = ProfileManager.get_current_profile()
        if target == current:
            self.auto_candidate = None
            self.auto_candidate_count = 0
            self.auto_status_var.set(f"Automatic mode: '{current}' is optimal for current workload.")
            return

        if target == self.auto_candidate:
            self.auto_candidate_count += 1
        else:
            self.auto_candidate = target
            self.auto_candidate_count = 1

        if self.auto_candidate_count < AUTO_CONFIRM_SAMPLES:
            self.auto_status_var.set(f"Automatic mode: verifying switch to {target} ({self.auto_candidate_count}/{AUTO_CONFIRM_SAMPLES}).")
            return

        if time.monotonic() - self.last_auto_change < AUTO_MIN_DWELL_SECONDS:
            self.auto_status_var.set("Automatic mode: holding profile to prevent oscillation (dwell timer).")
            return

        self.auto_candidate = None
        self.auto_candidate_count = 0
        self.set_profile(target, automatic=True)

    def set_profile(self, profile: str, automatic: bool = False) -> None:
        available = ProfileManager.get_available_profiles()
        if available and profile not in available:
            self.status_var.set(f"Profile '{profile}' is not advertised by this system.")
            return
        if self.profile_change_busy:
            return

        self.profile_change_busy = True
        self.status_var.set(f"Switching to {profile} profile...")
        self.root.update_idletasks()

        def worker() -> None:
            success, detail = ProfileManager.set_profile(profile, automatic=automatic)
            self.root.after(0, lambda: self._profile_result(profile, automatic, success, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _profile_result(self, profile: str, automatic: bool, success: bool, detail: str) -> None:
        self.profile_change_busy = False
        if success:
            self.status_var.set(f"Thermal profile changed to {profile}.")
            if automatic:
                self.last_auto_change = time.monotonic()
                self.auto_status_var.set(f"Automatic mode active: {profile}.")
        else:
            message = detail or "The profile change request was rejected."
            self.status_var.set(f"Could not change profile: {message}")
            if automatic:
                self.auto_status_var.set(f"Automatic switch failed: {message}")
            else:
                messagebox.showwarning(APP_NAME, f"Could not change profile.\n{message}")
        self._update_profile_controls()

    def refresh(self) -> None:
        if self.busy:
            self.root.after(POLL_MS, self.refresh)
            return
        self.busy = True

        def worker() -> None:
            temperatures = collect_sensors()
            fan_rpm, fan_pwm = fan_capabilities()
            load = self._cpu_load()
            self.root.after(0, lambda: self._apply_refresh(temperatures, fan_rpm, fan_pwm, load))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_refresh(self, temperatures: dict[str, float], fan_rpm: list[tuple[str, int]],
                       fan_pwm: list[str], load: float | None) -> None:
        for key, variable in self.temp_vars.items():
            variable.set(f"{temperatures[key]:.1f} °C" if key in temperatures else "Unavailable")
        for key in ("cpu", "gpu"):
            if key in temperatures:
                self.history[key].append(temperatures[key])
                self.history[key] = self.history[key][-HISTORY_LIMIT:]

        if load is not None:
            self.cpu_load_var.set(f"CPU load: {load:.0f}%")
        else:
            self.cpu_load_var.set("CPU load: sampling…")

        self.check_alerts(temperatures)
        self.evaluate_automatic_profile(temperatures, load)

        # Fan telemetry description
        if fan_rpm:
            speeds = [f"{label}: {speed} RPM" for label, speed in fan_rpm if speed > 0]
            if speeds:
                cap_text = f"Fans: {', '.join(speeds)}"
            else:
                cap_text = f"Fans: {len(fan_rpm)} tachometer(s) detected (idle or passive cooling)."
        else:
            cap_text = "Fan RPM: not exposed by this hardware/driver."
        if fan_pwm:
            cap_text += f" ({len(fan_pwm)} PWM attribute(s) visible; direct writes disabled)."
        self.capability_var.set(cap_text)

        # Status text
        current_prof = ProfileManager.get_current_profile()
        avail_profs = ProfileManager.get_available_profiles()
        if not avail_profs:
            self.status_var.set("Hardware monitoring active (ACPI profiles not exposed).")
        elif not self.profile_change_busy:
            self.status_var.set(f"Platform profile active: {current_prof or 'unknown'}.")

        self.last_update_var.set(f"Last update: {datetime.now().strftime('%H:%M:%S')}  •  Refresh: 2s")
        self._update_profile_controls()
        self.draw_chart()
        self.busy = False
        self.root.after(POLL_MS, self.refresh)

    def draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 110)
        left, right, top, bottom = 38, width - 12, 14, height - 22
        values = self.history["cpu"] + self.history["gpu"]
        if not values:
            canvas.create_text(width / 2, height / 2, text="Collecting temperature history…",
                               fill=MUTED, font=(self.font_family, 9))
            return

        low, high = min(20.0, min(values) - 5), max(90.0, max(values) + 5)
        if high <= low:
            high = low + 10

        for tick in range(20, 101, 20):
            y = bottom - ((tick - low) / (high - low)) * (bottom - top)
            if top <= y <= bottom:
                canvas.create_line(left, y, right, y, fill="#2b3949", dash=(2, 4))
                canvas.create_text(18, y, text=str(tick), fill=MUTED, font=(self.font_family, 7))

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
# CLI and Terminal Operation (for Headless / SSH / Minimal Linux Environments)
# ============================================================================

def get_telemetry_snapshot() -> dict[str, Any]:
    sys_info = get_system_info()
    temperatures = collect_sensors()
    fans, _ = fan_capabilities()
    load_sample = cpu_load_percent()
    cpu_load: float | None = None
    if load_sample:
        total, idle = load_sample
        # If total is 100.0, it's the loadavg fallback
        if total == 100.0:
            cpu_load = 100.0 - idle
        else:
            cpu_load = None

    active_profile = ProfileManager.get_current_profile()
    available_profiles = ProfileManager.get_available_profiles()
    config = load_config()

    return {
        "timestamp": datetime.now().isoformat(),
        "system": sys_info,
        "temperatures": temperatures,
        "cpu_load_percent": cpu_load,
        "fans": [{"label": label, "rpm": rpm} for label, rpm in fans],
        "active_profile": active_profile,
        "available_profiles": available_profiles,
        "config": config,
    }


def print_cli_status(snapshot: dict[str, Any] | None = None) -> None:
    data = snapshot or get_telemetry_snapshot()
    sys_info = data["system"]
    temps = data["temperatures"]
    fans = data["fans"]
    active = data["active_profile"] or "N/A"
    available = data["available_profiles"]

    print("=" * 64)
    print(f"  {APP_NAME} v{__version__} - Hardware & Thermal Telemetry")
    print("=" * 64)
    print(f"Host Device : {sys_info['summary']}")
    print(f"Operating Sys: {sys_info['os']}")
    print("-" * 64)

    cpu_val = f"{temps['cpu']:.1f}°C" if "cpu" in temps else "Unavailable"
    gpu_val = f"{temps['gpu']:.1f}°C" if "gpu" in temps else "Unavailable"
    nvme_val = f"{temps['nvme']:.1f}°C" if "nvme" in temps else "Unavailable"
    acpi_val = f"{temps['acpi']:.1f}°C" if "acpi" in temps else "Unavailable"

    print(f"CPU Temp    : {cpu_val:<16}  GPU Temp     : {gpu_val}")
    print(f"Storage Temp: {nvme_val:<16}  ACPI Ambient : {acpi_val}")

    if data.get("cpu_load_percent") is not None:
        print(f"CPU Load    : {data['cpu_load_percent']:.1f}%")

    if fans:
        fan_strs = [f"{f['label']}: {f['rpm']} RPM" for f in fans if f["rpm"] > 0]
        if fan_strs:
            print(f"Cooling Fans: {', '.join(fan_strs)}")
        else:
            print(f"Cooling Fans: {len(fans)} sensor(s) detected (0 RPM / passive)")
    else:
        print("Cooling Fans: Telemetry not exposed by firmware")

    print("-" * 64)
    print(f"Thermal Profile: {active.upper()}")
    if available:
        print(f"Available Modes: {', '.join(available)}")
    else:
        print("Available Modes: None (Hardware profiles not supported)")
    print("=" * 64)


def cli_monitor() -> None:
    """Live updating terminal telemetry monitor."""
    print(f"Starting {APP_NAME} terminal monitor (Press Ctrl+C to exit)...")
    try:
        while True:
            # Clear terminal screen
            sys.stdout.write("\033[2J\033[H")
            print_cli_status()
            print("\nUpdating every 2 seconds. Press Ctrl+C to exit.")
            time.sleep(2)
    except KeyboardInterrupt:
        print("\nExited monitor.")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="thermals",
        description="Thermals - Native Linux Telemetry Monitor and Thermal Profile Manager",
    )
    parser.add_argument("-s", "--status", action="store_true", help="Print current telemetry and exit")
    parser.add_argument("-j", "--json", action="store_true", help="Print telemetry as JSON")
    parser.add_argument("-m", "--monitor", action="store_true", help="Run real-time terminal monitor")
    parser.add_argument("-l", "--list-profiles", action="store_true", help="List supported thermal profiles")
    parser.add_argument("-p", "--set-profile", metavar="PROFILE", help="Set the thermal profile")
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

    if args.list_profiles:
        profs = ProfileManager.get_available_profiles()
        current = ProfileManager.get_current_profile()
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
        success, msg = ProfileManager.set_profile(target, automatic=False)
        print(f"[{'SUCCESS' if success else 'FAILED'}] {msg}")
        return 0 if success else 1

    if args.monitor or args.cli:
        cli_monitor()
        return 0

    # Graphical Interface Execution
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if not has_display and not args.gui:
        # Graceful fallback when executed in headless / container / SSH session without X11
        print(f"[{APP_NAME}] No graphical display detected (DISPLAY or WAYLAND_DISPLAY not set).")
        print(f"[{APP_NAME}] Running in terminal status mode:\n")
        print_cli_status()
        print("\nTip: Run 'thermals --monitor' for live terminal monitoring, or 'thermals --help' for options.")
        return 0

    if not TKINTER_AVAILABLE:
        print("Error: Tkinter is required for the graphical interface, but python3-tk is not installed.", file=sys.stderr)
        print("\nInstall Tkinter for your Linux distribution:\n"
              "  Debian / Ubuntu / Kali / Mint:  sudo apt install python3-tk\n"
              "  Fedora / RHEL / CentOS:        sudo dnf install python3-tkinter\n"
              "  Arch Linux / Manjaro:          sudo pacman -S tk\n"
              "  openSUSE:                      sudo zypper in python3-tk\n"
              "  Alpine Linux:                  sudo apk add py3-tkinter\n"
              "  Void Linux:                    sudo xbps-install -S python3-tkinter\n", file=sys.stderr)
        print("Alternatively, use CLI mode: thermals --status or thermals --monitor", file=sys.stderr)
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
