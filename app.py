#!/usr/bin/env python3
"""ASUS Thermal Control Center for Ubuntu.

Controls only Linux's platform_profile interface. It does not write PWM or EC
registers. Automatic switching uses CPU load + temperature, hysteresis and a
minimum dwell time to avoid profile oscillation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "Thermals"
PROFILE_PATH = Path("/sys/firmware/acpi/platform_profile")
PROFILE_CHOICES_PATH = Path("/sys/firmware/acpi/platform_profile_choices")
HW_MON_PATH = Path("/sys/class/hwmon")
HELPER_PATH = Path("/usr/local/sbin/asus-thermal-profile")
CONFIG_DIR = Path.home() / ".config" / "asus-thermal-control-center"
CONFIG_PATH = CONFIG_DIR / "config.json"

POLL_MS = 2000
HISTORY_LIMIT = 60
AUTO_CONFIRM_SAMPLES = 3
AUTO_MIN_DWELL_SECONDS = 60
ALERT_RESET_HYSTERESIS = 5.0

BG, PANEL, PANEL_ALT = "#10151d", "#18212d", "#202b39"
FG, MUTED, ACCENT = "#e8edf4", "#9aa8b8", "#63a6ff"
GREEN, AMBER, RED = "#42c88a", "#f4b860", "#ff6b6b"

DEFAULT_CONFIG = {
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


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


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
                records.append((parent.split("/")[0], parent.split("/")[-1], float(value)))
    return records


def collect_sensors() -> dict[str, float]:
    """Collect best-effort CPU, GPU, NVMe and ACPI temperatures in Celsius."""
    found: dict[str, float] = {}
    sensors_bin = shutil.which("sensors")
    if sensors_bin:
        try:
            result = subprocess.run(
                [sensors_bin, "-j"], check=True, capture_output=True,
                text=True, timeout=4,
            )
            for chip, label, value in parse_sensor_json(json.loads(result.stdout)):
                chip_l, label_l = chip.lower(), label.lower()
                if "k10temp" in chip_l or "tctl" in label_l or "tdie" in label_l:
                    found.setdefault("cpu", value)
                elif "amdgpu" in chip_l or "edge" in label_l:
                    found.setdefault("gpu", value)
                elif "nvme" in chip_l or "composite" in label_l:
                    found.setdefault("nvme", value)
                elif "acpitz" in chip_l:
                    found.setdefault("acpi", value)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass

    # Sysfs fallback for sensors that lm-sensors did not report.
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
            label = read_text(hwmon / input_file.name.replace("_input", "_label")) or ""
            label_l = label.lower()
            if "k10temp" in name or "tctl" in label_l or "tdie" in label_l:
                found.setdefault("cpu", value)
            elif "amdgpu" in name or "edge" in label_l:
                found.setdefault("gpu", value)
            elif "nvme" in name or "composite" in label_l:
                found.setdefault("nvme", value)
            elif "acpitz" in name:
                found.setdefault("acpi", value)
    return found


def fan_capabilities() -> tuple[list[str], list[str]]:
    rpm, pwm = [], []
    for hwmon in HW_MON_PATH.glob("hwmon*"):
        rpm.extend(str(path) for path in hwmon.glob("fan*_input"))
        pwm.extend(
            str(path) for path in hwmon.glob("pwm[0-9]*")
            if not path.name.endswith("_enable")
        )
    return rpm, pwm


def cpu_load_percent() -> tuple[float, float] | None:
    """Read aggregate CPU utilization from /proc/stat; caller manages sampling."""
    try:
        fields = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return float(total), float(idle)  # type: ignore[return-value]
    except (OSError, ValueError, IndexError):
        return None


class ThermalApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x820")
        self.root.minsize(850, 720)
        self.root.configure(bg=BG)

        self.config = load_config()
        self.history = {"cpu": [], "gpu": []}
        self.busy = False
        self.profile_change_busy = False
        self.profile_buttons: dict[str, ttk.Button] = {}
        self.alert_latched = {"cpu": False, "gpu": False}
        self.cpu_stat_previous: tuple[float, float] | None = None
        self.auto_candidate: str | None = None
        self.auto_candidate_count = 0
        self.last_auto_change = 0.0
        self.last_profile_error = ""
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
        style.configure("TButton", font=("Ubuntu", 10), padding=(10, 8),
                        background=PANEL_ALT, foreground=FG, borderwidth=0)
        style.map("TButton", background=[("active", "#34465c"), ("disabled", "#26313e")])
        style.configure("Profile.TButton", font=("Ubuntu", 10, "bold"), padding=(12, 11),
                        background=PANEL_ALT, foreground=FG)
        style.configure("Selected.Profile.TButton", font=("Ubuntu", 10, "bold"),
                        padding=(12, 11), background="#174d3b", foreground="#d9ffec")

    def _section(self, parent: tk.Widget, title: str, subtitle: str | None = None) -> tk.Frame:
        frame = tk.Frame(parent, bg=PANEL, padx=16, pady=13)
        frame.pack(fill="x", padx=24, pady=7)
        tk.Label(frame, text=title, bg=PANEL, fg=FG,
                 font=("Ubuntu", 10, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(frame, text=subtitle, bg=PANEL, fg=MUTED,
                     font=("Ubuntu", 8)).pack(anchor="w", pady=(3, 9))
        return frame

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=25, pady=(17, 8))
        tk.Label(header, text="THERMAL CONTROL CENTER", bg=BG, fg=FG,
                 font=("Ubuntu", 18, "bold")).pack(side="left")
        self.profile_badge = tk.Label(header, text="PROFILE: —", bg=PANEL_ALT, fg=ACCENT,
                                      font=("Ubuntu", 9, "bold"), padx=12, pady=8)
        self.profile_badge.pack(side="right")

        subtitle = tk.Label(self.root, text="ASUS VivoBook Go E1504FA  •  Ryzen 5 7520U",
                            bg=BG, fg=MUTED, font=("Ubuntu", 9))
        subtitle.pack(anchor="w", padx=27)

        cards = tk.Frame(self.root, bg=BG)
        cards.pack(fill="x", padx=18, pady=8)
        specs = [
            ("cpu", "CPU TEMPERATURE", GREEN),
            ("gpu", "GPU TEMPERATURE", ACCENT),
            ("nvme", "NVME DRIVE", AMBER),
            ("acpi", "ACPI THERMAL", MUTED),
        ]
        for index, (key, title, color) in enumerate(specs):
            card = tk.Frame(cards, bg=PANEL, padx=13, pady=12)
            card.grid(row=0, column=index, sticky="nsew", padx=5)
            cards.grid_columnconfigure(index, weight=1)
            tk.Label(card, text=title, bg=PANEL, fg=MUTED,
                     font=("Ubuntu", 8, "bold")).pack(anchor="w")
            tk.Label(card, textvariable=self.temp_vars[key], bg=PANEL, fg=color,
                     font=("Ubuntu", 20, "bold")).pack(anchor="w", pady=(7, 1))

        profile = self._section(
            self.root, "THERMAL PROFILES",
            "These are firmware-managed profiles. Fan RPM and PWM are not exposed by the current driver."
        )
        row = tk.Frame(profile, bg=PANEL)
        row.pack(fill="x")
        for key, label, detail in [
            ("quiet", "Quiet", "Lower noise"),
            ("balanced", "Balanced", "Everyday use"),
            ("performance", "Performance", "Higher performance"),
        ]:
            button = ttk.Button(row, text=f"{label}\n{detail}", style="Profile.TButton",
                                command=lambda value=key: self.set_profile(value, automatic=False))
            button.pack(side="left", fill="x", expand=True, padx=(0, 7))
            self.profile_buttons[key] = button

        automation = self._section(
            self.root, "AUTOMATIC PROFILE SWITCHING",
            "Uses CPU load and temperature. High temperature overrides performance and selects Quiet."
        )
        tk.Checkbutton(
            automation, text="Enable automatic thermal profile selection",
            variable=self.auto_var, command=self.toggle_auto,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=("Ubuntu", 9),
        ).pack(anchor="w")
        tk.Label(automation, textvariable=self.cpu_load_var, bg=PANEL, fg=MUTED,
                 font=("Ubuntu", 8)).pack(anchor="w", pady=(3, 0))
        tk.Label(automation, textvariable=self.auto_status_var, bg=PANEL, fg=ACCENT,
                 font=("Ubuntu", 8)).pack(anchor="w", pady=(3, 0))

        alerts = self._section(
            self.root, "TEMPERATURE ALERTS",
            "A desktop notification is sent when a threshold is crossed. Alerts re-arm after cooling by 5°C."
        )
        alert_row = tk.Frame(alerts, bg=PANEL)
        alert_row.pack(fill="x")
        tk.Checkbutton(
            alert_row, text="Enable alerts", variable=self.alerts_var,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL_ALT, font=("Ubuntu", 9),
        ).pack(side="left", padx=(0, 18))
        tk.Label(alert_row, text="CPU °C", bg=PANEL, fg=MUTED).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.cpu_alert_var, width=7).pack(side="left", padx=(5, 15))
        tk.Label(alert_row, text="GPU °C", bg=PANEL, fg=MUTED).pack(side="left")
        ttk.Entry(alert_row, textvariable=self.gpu_alert_var, width=7).pack(side="left", padx=5)
        ttk.Button(alert_row, text="Save settings", command=self.save_settings).pack(side="right")

        chart = self._section(self.root, "TEMPERATURE HISTORY", "Rolling CPU and GPU readings")
        self.chart = tk.Canvas(chart, height=145, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="both", expand=True, pady=(4, 0))
        self.chart.bind("<Configure>", lambda _event: self.draw_chart())

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill="x", padx=26, pady=(3, 12))
        self.capability_var = tk.StringVar(value="Checking fan telemetry...")
        for variable, color in [
            (self.capability_var, AMBER),
            (self.status_var, MUTED),
            (self.last_update_var, MUTED),
        ]:
            tk.Label(footer, textvariable=variable, bg=BG, fg=color,
                     font=("Ubuntu", 8)).pack(anchor="w", pady=1)

    def _available_profiles(self) -> list[str]:
        raw = read_text(PROFILE_CHOICES_PATH) or ""
        return [p for p in raw.split() if p in {"quiet", "balanced", "performance"}]

    def _current_profile(self) -> str | None:
        value = read_text(PROFILE_PATH)
        return value if value in {"quiet", "balanced", "performance"} else None

    def _update_profile_controls(self) -> None:
        current = self._current_profile()
        for profile, button in self.profile_buttons.items():
            available = profile in self._available_profiles()
            button.state(["!disabled"] if available else ["disabled"])
            button.configure(style="Selected.Profile.TButton" if profile == current else "Profile.TButton")
        self.profile_badge.configure(text=f"PROFILE: {current.upper()}" if current else "PROFILE: UNKNOWN")

    def save_settings(self) -> None:
        try:
            cpu_alert = float(self.cpu_alert_var.get())
            gpu_alert = float(self.gpu_alert_var.get())
            if not (50 <= cpu_alert <= 110 and 50 <= gpu_alert <= 110):
                raise ValueError("Alert thresholds must be between 50°C and 110°C.")
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
                subprocess.Popen([notify, "--app-name", APP_NAME, title, body],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def _automatic_target(self, temperatures: dict[str, float], load: float | None) -> str | None:
        cpu = temperatures.get("cpu")
        gpu = temperatures.get("gpu")
        if cpu is None:
            return None

        # Thermal safety takes priority over performance.
        hottest = max(cpu, gpu if gpu is not None else cpu)
        if hottest >= float(self.config["auto_critical_c"]):
            return "quiet"
        if cpu >= float(self.config["auto_hot_cpu_c"]):
            return "quiet" if cpu >= 82 else "balanced"
        if load is not None and load >= float(self.config["auto_performance_load"]) and cpu < 75 and (gpu is None or gpu < 78):
            return "performance"
        if load is not None and load <= float(self.config["auto_quiet_load"]) and cpu < float(self.config["auto_cool_cpu_c"]):
            return "quiet"
        return "balanced"

    def evaluate_automatic_profile(self, temperatures: dict[str, float], load: float | None) -> None:
        if not self.auto_var.get():
            return
        if not HELPER_PATH.exists():
            self.auto_status_var.set("Automatic mode needs the one-time privileged helper. Run install-helper.sh.")
            return
        target = self._automatic_target(temperatures, load)
        if target is None:
            self.auto_status_var.set("Automatic mode: waiting for CPU temperature/load readings.")
            return
        current = self._current_profile()
        if target == current:
            self.auto_candidate = None
            self.auto_candidate_count = 0
            self.auto_status_var.set(f"Automatic mode: {current} is appropriate for current load/temperature.")
            return

        if target == self.auto_candidate:
            self.auto_candidate_count += 1
        else:
            self.auto_candidate = target
            self.auto_candidate_count = 1

        if self.auto_candidate_count < AUTO_CONFIRM_SAMPLES:
            self.auto_status_var.set(f"Automatic mode: confirming {target} ({self.auto_candidate_count}/{AUTO_CONFIRM_SAMPLES}).")
            return

        import time
        if time.monotonic() - self.last_auto_change < AUTO_MIN_DWELL_SECONDS:
            self.auto_status_var.set("Automatic mode: waiting for profile dwell timer to prevent rapid switching.")
            return

        self.auto_candidate = None
        self.auto_candidate_count = 0
        self.set_profile(target, automatic=True)

    def set_profile(self, profile: str, automatic: bool = False) -> None:
        if profile not in self._available_profiles():
            self.status_var.set(f"Profile '{profile}' is not advertised by this firmware.")
            return
        if self.profile_change_busy:
            return
        if automatic and not HELPER_PATH.exists():
            self.auto_status_var.set("Install the privileged helper before enabling unattended profile changes.")
            return

        self.profile_change_busy = True
        self.status_var.set(f"Switching to {profile} profile...")
        self.root.update_idletasks()

        def worker() -> None:
            success, detail = False, ""
            try:
                if HELPER_PATH.exists() and shutil.which("sudo"):
                    result = subprocess.run(
                        ["sudo", "-n", str(HELPER_PATH), profile],
                        capture_output=True, text=True, timeout=10,
                    )
                    success = result.returncode == 0
                    detail = (result.stdout or result.stderr).strip()
                elif not automatic and shutil.which("pkexec"):
                    result = subprocess.run(
                        ["pkexec", "/usr/bin/tee", str(PROFILE_PATH)],
                        input=profile + "\n", capture_output=True, text=True, timeout=35,
                    )
                    success = result.returncode == 0
                    detail = (result.stdout or result.stderr).strip()
                else:
                    detail = "Privileged helper is not installed. Run install-helper.sh once."
            except (OSError, subprocess.SubprocessError) as exc:
                detail = str(exc)

            self.root.after(0, lambda: self._profile_result(profile, automatic, success, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _profile_result(self, profile: str, automatic: bool, success: bool, detail: str) -> None:
        import time
        self.profile_change_busy = False
        if success:
            self.status_var.set(f"Thermal profile changed to {profile}.")
            if automatic:
                self.last_auto_change = time.monotonic()
                self.auto_status_var.set(f"Automatic mode selected {profile}.")
        else:
            message = detail or "The profile change was rejected."
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
            rpm, pwm = fan_capabilities()
            load = self._cpu_load()
            self.root.after(0, lambda: self._apply_refresh(temperatures, rpm, pwm, load))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_refresh(self, temperatures: dict[str, float], rpm: list[str],
                       pwm: list[str], load: float | None) -> None:
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

        capability = f"Fan RPM: {len(rpm)} sensor(s) detected." if rpm else "Fan RPM: not exposed by this firmware/driver."
        if pwm:
            capability += f" {len(pwm)} PWM attribute(s) visible; direct writes are disabled."
        self.capability_var.set(capability)
        if not PROFILE_PATH.exists():
            self.status_var.set("Platform profile interface unavailable.")
        elif not self._available_profiles():
            self.status_var.set("No supported platform profiles advertised.")
        elif not self.profile_change_busy:
            self.status_var.set(f"Firmware profile active: {self._current_profile() or 'unknown'}.")
        self.last_update_var.set(f"Last update: {datetime.now().strftime('%H:%M:%S')}  •  Refresh: 2 seconds")
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
                               fill=MUTED, font=("Ubuntu", 9))
            return
        low, high = min(20.0, min(values) - 5), max(90.0, max(values) + 5)
        if high <= low:
            high = low + 10
        for tick in range(20, 101, 20):
            y = bottom - ((tick - low) / (high - low)) * (bottom - top)
            if top <= y <= bottom:
                canvas.create_line(left, y, right, y, fill="#2b3949", dash=(2, 4))
                canvas.create_text(18, y, text=str(tick), fill=MUTED, font=("Ubuntu", 7))

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


def main() -> None:
    root = tk.Tk()
    ThermalApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
