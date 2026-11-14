"""Tests for hardware sensor discovery, polling, and throttling detection."""

from pathlib import Path
import app
from app import get_registry, check_cpu_throttling


def test_sensor_discovery(mock_sysfs: Path):
    """Verify that all hardware categories and sensor thresholds are correctly discovered."""
    reg = get_registry("AMD Ryzen 5 7520U")
    assert len(reg.sensors) == 6

    # Verify sensor categories
    categories = {s.category for s in reg.sensors}
    assert "motherboard" in categories
    assert "cpu" in categories
    assert "gpu" in categories
    assert "storage" in categories
    assert "fan" in categories
    assert "battery" in categories

    # Verify specific sensor values and parsed limits
    cpu_sensor = next(s for s in reg.sensors if s.category == "cpu")
    assert cpu_sensor.sensor_name == "Tctl"
    assert cpu_sensor.val == 64.5
    assert cpu_sensor.crit_temp == 100.0

    nvme_sensor = next(s for s in reg.sensors if s.category == "storage")
    assert nvme_sensor.sensor_name == "Composite"
    assert nvme_sensor.val == 38.0
    assert nvme_sensor.max_temp == 82.0
    assert nvme_sensor.crit_temp == 85.0

    fan_sensor = next(s for s in reg.sensors if s.category == "fan")
    assert fan_sensor.sensor_name == "CPU Fan"
    assert fan_sensor.val == 2800.0
    assert fan_sensor.unit == "RPM"

    assert reg.has_battery is True
    assert reg.battery_charge_limit == 80


def test_polling_summary(mock_sysfs: Path):
    """Verify that single-pass polling returns accurate aggregate metrics."""
    reg = get_registry("AMD Ryzen 5 7520U")
    summary, all_hw = reg.poll_all()

    assert summary["cpu"] == 64.5
    assert summary["gpu"] == 55.0
    assert summary["nvme"] == 38.0
    assert len(all_hw) == 6

    # Update sensor input on disk and poll again
    k10_input = mock_sysfs / "sys/class/hwmon/hwmon1/temp1_input"
    k10_input.write_text("72100\n")

    summary2, _ = reg.poll_all()
    assert summary2["cpu"] == 72.1


def test_throttling_detection(mock_sysfs: Path):
    """Test CPU thermal throttling detection logic."""
    # Under normal condition: 64.5°C, 30% load, 2800 MHz
    is_throttling, status = check_cpu_throttling(64.5, 30.0)
    assert not is_throttling
    assert "Normal" in status

    # Simulate thermal throttling: temp >= 82°C, load >= 60%, freq clamped below 50% of 4.3 GHz (e.g. 1.2 GHz)
    freq_file = mock_sysfs / "sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq"
    freq_file.write_text("1200000\n")

    is_throttling, status = check_cpu_throttling(84.0, 75.0)
    assert is_throttling
    assert "Thermal Throttling Active!" in status
    assert "1200 MHz" in status
