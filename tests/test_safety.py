"""Tests for fan safety rails, failsafes, emergency thermal overrides, and graduated limiter."""

from pathlib import Path
import time
import app
from app import (
    FanSafetyController,
    GraduatedThermalLimiter,
    get_registry,
    FAN_SAFETY_FLOOR_PWM,
    FAN_EMERGENCY_OVERRIDE_C,
)


def test_fan_safety_floor(mock_sysfs: Path):
    """Test that fan speeds below safety floor or 0% are rejected or clamped."""
    get_registry("AMD Ryzen 5 7520U")
    pwm_file = mock_sysfs / "sys/class/hwmon/hwmon4/pwm1"

    # Rejection of 0% fan speed to protect against motor stalls and overheating
    ok, err = FanSafetyController.set_fan_speed_safe(str(pwm_file), 0)
    assert not ok
    assert "Refusing 0%" in err

    # Setting a low percentage (e.g. 5% = 12.75 PWM) must be clamped to safe minimum floor (50 PWM)
    ok, msg = FanSafetyController.set_fan_speed_safe(str(pwm_file), 5)
    assert ok
    assert FanSafetyController._manual_active is True
    # Verify that value written on disk is at least FAN_SAFETY_FLOOR_PWM (50)
    actual_pwm = int(pwm_file.read_text().strip())
    assert actual_pwm == FAN_SAFETY_FLOOR_PWM
    assert actual_pwm >= 50

    # Setting 100% must clamp to 255
    ok, msg = FanSafetyController.set_fan_speed_safe(str(pwm_file), 100)
    assert ok
    actual_pwm = int(pwm_file.read_text().strip())
    assert actual_pwm == 255


def test_emergency_thermal_override(mock_sysfs: Path):
    """Test that emergency thermal override forces fans to auto when hardware hits critical temp."""
    get_registry("AMD Ryzen 5 7520U")
    pwm_file = mock_sysfs / "sys/class/hwmon/hwmon4/pwm1"
    enable_file = mock_sysfs / "sys/class/hwmon/hwmon4/pwm1_enable"

    # Activate manual fan mode
    FanSafetyController.set_fan_speed_safe(str(pwm_file), 40)
    assert FanSafetyController._manual_active is True
    assert enable_file.read_text().strip() == "1"

    # Below emergency override: 75°C
    overridden = FanSafetyController.check_emergency_override(75.0)
    assert not overridden
    assert FanSafetyController._manual_active is True

    # At or above emergency override: 80.5°C >= FAN_EMERGENCY_OVERRIDE_C
    overridden = FanSafetyController.check_emergency_override(FAN_EMERGENCY_OVERRIDE_C + 0.5)
    assert overridden is True
    assert FanSafetyController._manual_active is False
    # pwm1_enable must be restored to 2 (firmware automatic dynamic curve)
    assert enable_file.read_text().strip() == "2"


def test_restore_all_fans_failsafe(mock_sysfs: Path):
    """Test failsafe restoration to automatic curve on shutdown/exit."""
    get_registry("AMD Ryzen 5 7520U")
    pwm_file = mock_sysfs / "sys/class/hwmon/hwmon4/pwm1"
    enable_file = mock_sysfs / "sys/class/hwmon/hwmon4/pwm1_enable"

    # Set manual fan mode
    FanSafetyController.set_fan_speed_safe(str(pwm_file), 60)
    assert FanSafetyController._manual_active is True
    assert enable_file.read_text().strip() == "1"

    # Trigger restore
    FanSafetyController.restore_all_fans()
    assert FanSafetyController._manual_active is False
    assert enable_file.read_text().strip() == "2"


def test_graduated_thermal_limiter_stages_and_dwell():
    """Test multi-stage graduated thermal limiter behavior and dwell timer protection."""
    limiter = GraduatedThermalLimiter()
    limiter.dwell_seconds = 1.0  # 1s dwell for testing

    target = 70.0
    hysteresis = 5.0

    # Normal: temp 65°C (< target)
    msg = limiter.evaluate(65.0, target, hysteresis)
    assert limiter.stage == 0

    # Over target by 1°C -> Stage 1 (EPP scale-down)
    msg = limiter.evaluate(71.0, target, hysteresis)
    assert limiter.stage == 1
    assert "Stage 1" in msg

    # Immediate second evaluation before dwell time expires should NOT transition
    msg = limiter.evaluate(78.0, target, hysteresis)
    assert limiter.stage == 1  # Still Stage 1 due to dwell timer

    # Wait for dwell time to expire
    time.sleep(1.05)

    # Now evaluate with delta >= 6°C (77°C > 70°C) -> Escalates to Stage 3 (Quiet mode)
    msg = limiter.evaluate(77.0, target, hysteresis)
    assert limiter.stage == 3
    assert "Stage 3" in msg

    # Wait for dwell time to expire
    time.sleep(1.05)

    # Cool down below target - hysteresis (64°C <= 70 - 5 = 65°C) -> Returns to Stage 0
    msg = limiter.evaluate(64.0, target, hysteresis)
    assert limiter.stage == 0
    assert "Target Maintained" in msg
