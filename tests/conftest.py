"""Pytest fixtures providing a synthetic /sys filesystem tree for hardware testing."""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import app


@pytest.fixture
def mock_sysfs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Sets up a realistic mock /sys and configuration hierarchy.

    Simulates:
      - ACPI ambient temperature sensor
      - AMD k10temp CPU sensor with critical threshold
      - AMDGPU edge temperature sensor with critical threshold
      - NVMe composite temperature sensor with max & critical thresholds
      - ASUS cooling fan PWM and RPM sensors
      - Battery BAT0 with charge capacity, status, and charge threshold control
      - ACPI platform profile interfaces
      - CPU boost and cpufreq policy0 (frequency and EPP)
      - App configuration and daemon state directories
    """
    sys_dir = tmp_path / "sys"
    hwmon = sys_dir / "class" / "hwmon"
    power_supply = sys_dir / "class" / "power_supply"
    firmware_acpi = sys_dir / "firmware" / "acpi"
    cpufreq = sys_dir / "devices" / "system" / "cpu" / "cpufreq"
    policy0 = cpufreq / "policy0"
    config_dir = tmp_path / "config"
    legacy_config = tmp_path / "legacy_config"

    # 1. Hwmon sensors
    # hwmon0: ACPI Motherboard ambient
    hw0 = hwmon / "hwmon0"
    hw0.mkdir(parents=True)
    (hw0 / "name").write_text("acpitz\n")
    (hw0 / "temp1_input").write_text("62000\n")
    (hw0 / "temp1_label").write_text("ACPI Ambient\n")

    # hwmon1: CPU (k10temp)
    hw1 = hwmon / "hwmon1"
    hw1.mkdir(parents=True)
    (hw1 / "name").write_text("k10temp\n")
    (hw1 / "temp1_input").write_text("64500\n")
    (hw1 / "temp1_label").write_text("Tctl\n")
    (hw1 / "temp1_crit").write_text("100000\n")

    # hwmon2: GPU (amdgpu)
    hw2 = hwmon / "hwmon2"
    hw2.mkdir(parents=True)
    (hw2 / "name").write_text("amdgpu\n")
    (hw2 / "temp1_input").write_text("55000\n")
    (hw2 / "temp1_label").write_text("edge\n")
    (hw2 / "temp1_crit").write_text("95000\n")

    # hwmon3: NVMe SSD
    hw3 = hwmon / "hwmon3"
    hw3.mkdir(parents=True)
    (hw3 / "name").write_text("nvme\n")
    (hw3 / "temp1_input").write_text("38000\n")
    (hw3 / "temp1_label").write_text("Composite\n")
    (hw3 / "temp1_max").write_text("82000\n")
    (hw3 / "temp1_crit").write_text("85000\n")

    # hwmon4: ASUS Fan with PWM
    hw4 = hwmon / "hwmon4"
    hw4.mkdir(parents=True)
    (hw4 / "name").write_text("asus\n")
    (hw4 / "pwm1").write_text("128\n")
    (hw4 / "pwm1_enable").write_text("1\n")
    (hw4 / "fan1_input").write_text("2800\n")
    (hw4 / "fan1_label").write_text("CPU Fan\n")

    # 2. Battery (BAT0)
    bat0 = power_supply / "BAT0"
    bat0.mkdir(parents=True)
    (bat0 / "capacity").write_text("85\n")
    (bat0 / "status").write_text("Discharging\n")
    (bat0 / "charge_control_end_threshold").write_text("80\n")

    # 3. Platform Profile
    firmware_acpi.mkdir(parents=True)
    (firmware_acpi / "platform_profile").write_text("balanced\n")
    (firmware_acpi / "platform_profile_choices").write_text("quiet balanced performance\n")

    # 4. CPU Controls
    policy0.mkdir(parents=True)
    (cpufreq / "boost").write_text("1\n")
    (policy0 / "scaling_cur_freq").write_text("2800000\n")
    (policy0 / "cpuinfo_max_freq").write_text("4300000\n")
    (policy0 / "energy_performance_preference").write_text("balance_performance\n")

    # 5. Config Directories
    config_dir.mkdir(parents=True)

    # Monkeypatch app constants to point to mock tree
    monkeypatch.setattr(app, "HW_MON_PATH", hwmon)
    monkeypatch.setattr(app, "POWER_SUPPLY_PATH", power_supply)
    monkeypatch.setattr(app, "PROFILE_STD_PATH", firmware_acpi / "platform_profile")
    monkeypatch.setattr(app, "PROFILE_STD_CHOICES", firmware_acpi / "platform_profile_choices")
    monkeypatch.setattr(app, "AMD_BOOST_PATH", cpufreq / "boost")
    monkeypatch.setattr(app, "CPUFREQ_DIR", cpufreq)
    monkeypatch.setattr(app, "CPU_CUR_FREQ_PATH", policy0 / "scaling_cur_freq")
    monkeypatch.setattr(app, "CPU_MAX_FREQ_PATH", policy0 / "cpuinfo_max_freq")
    monkeypatch.setattr(app, "CPU_EPP_PATH", policy0 / "energy_performance_preference")
    monkeypatch.setattr(app, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(app, "CONFIG_PATH", config_dir / "config.json")
    monkeypatch.setattr(app, "LEGACY_CONFIG_DIR", legacy_config)
    monkeypatch.setattr(app, "DAEMON_STATE_PATH", config_dir / "daemon_state.json")

    # Reset registry singleton before and after test
    app._REGISTRY = None
    yield tmp_path
    app._REGISTRY = None
