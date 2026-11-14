"""Tests for thermal profiles, CPU boost, battery limits, and configuration persistence."""

from pathlib import Path
import json
import app
from app import (
    ThermalControlManager,
    get_registry,
    load_config,
    save_config,
)


def test_platform_profile_read_and_set(mock_sysfs: Path):
    """Test reading active profile, listing available choices, and applying profile changes."""
    assert ThermalControlManager.read_active_profile() == "balanced"
    choices = ThermalControlManager.get_available_profiles()
    assert choices == ["quiet", "balanced", "performance"]

    # Directly set profile (sysfs file in mock_sysfs is user-writable)
    ok, msg = ThermalControlManager.set_platform_profile("quiet")
    assert ok
    assert "quiet" in msg
    assert ThermalControlManager.read_active_profile() == "quiet"

    profile_file = mock_sysfs / "sys/firmware/acpi/platform_profile"
    assert profile_file.read_text().strip() == "quiet"


def test_cpu_boost_control(mock_sysfs: Path):
    """Test reading and toggling CPU Turbo Boost."""
    assert ThermalControlManager.read_cpu_boost() is True

    # Disable boost
    ok, msg = ThermalControlManager.set_cpu_boost(False)
    assert ok
    assert "disabled" in msg
    assert ThermalControlManager.read_cpu_boost() is False

    boost_file = mock_sysfs / "sys/devices/system/cpu/cpufreq/boost"
    assert boost_file.read_text().strip() == "0"

    # Re-enable boost
    ok, msg = ThermalControlManager.set_cpu_boost(True)
    assert ok
    assert "enabled" in msg
    assert ThermalControlManager.read_cpu_boost() is True
    assert boost_file.read_text().strip() == "1"


def test_battery_charge_limit(mock_sysfs: Path):
    """Test battery charge longevity threshold reading, validation, and writing."""
    get_registry("AMD Ryzen 5 7520U")
    assert ThermalControlManager.get_battery_charge_limit() == 80

    # Validation: reject out-of-range values (< 50 or > 100)
    ok, err = ThermalControlManager.set_battery_charge_limit(40)
    assert not ok
    assert "between 50% and 100%" in err

    ok, err = ThermalControlManager.set_battery_charge_limit(110)
    assert not ok
    assert "between 50% and 100%" in err

    # Valid set to 60%
    ok, msg = ThermalControlManager.set_battery_charge_limit(60)
    assert ok
    assert "60%" in msg
    assert ThermalControlManager.get_battery_charge_limit() == 60

    thresh_file = mock_sysfs / "sys/class/power_supply/BAT0/charge_control_end_threshold"
    assert thresh_file.read_text().strip() == "60"


def test_cpu_epp_control(mock_sysfs: Path):
    """Test reading and writing CPU Energy Performance Preference."""
    assert ThermalControlManager.get_cpu_epp() == "balance_performance"

    # Write preference directly via sysfs
    epp_file = mock_sysfs / "sys/devices/system/cpu/cpufreq/policy0/energy_performance_preference"
    epp_file.write_text("power\n")
    assert ThermalControlManager.get_cpu_epp() == "power"


def test_config_save_and_load(mock_sysfs: Path):
    """Test saving and loading user configuration."""
    cfg = load_config()
    assert cfg["target_temp_c"] == 75.0
    assert cfg["battery_charge_limit"] == 100

    cfg["target_temp_c"] = 68.0
    cfg["battery_charge_limit"] = 80
    assert save_config(cfg) is True

    loaded = load_config()
    assert loaded["target_temp_c"] == 68.0
    assert loaded["battery_charge_limit"] == 80


def test_legacy_config_migration(mock_sysfs: Path):
    """Test automatic migration from legacy config directory ~/.config/asus-thermal-control-center."""
    legacy_dir = mock_sysfs / "legacy_config"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = legacy_dir / "config.json"
    legacy_file.write_text(json.dumps({
        "target_temp_c": 65.0,
        "fan_speed_pct": 85,
        "preferred_profile": "performance"
    }))

    # New config file does not exist yet
    config_file = mock_sysfs / "config/config.json"
    if config_file.exists():
        config_file.unlink()

    migrated = load_config()
    assert migrated["target_temp_c"] == 65.0
    assert migrated["fan_speed_pct"] == 85
    assert migrated["preferred_profile"] == "performance"
    assert config_file.exists()
