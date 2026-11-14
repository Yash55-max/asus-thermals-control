# Changelog

All notable changes to **Thermals** (`asus-thermals-control`) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-11-14

### Security & Hardening
- **Privileged Helper Overhaul (`asus-thermal-profile`)**: Completely rewritten with strict POSIX path canonicalization via `readlink -f` and strict regex whitelisting (`^/sys/class/hwmon/hwmon[0-9]+/pwm[1-9]$`).
- **Input Sanitization**: Replaced arbitrary file arguments with strict enum validation (`profile`, `boost`, `epp`) and bounded integer validation (`charge-limit` 50–100, `throttle-policy` 0–2, `fan` 50–255).
- **Hardened Permissions**: Sudoers installer enforces root-ownership (`0755` directory, `0755` executable, `0440` sudoers) and validates sudoers syntax using `visudo -cf` prior to installation.
- **Threat Model**: Documented full threat model, privilege boundaries, attack surface analysis, and vulnerability reporting procedures in `SECURITY.md`.

### Safety Rails & Failsafes
- **Fan Motor Safety Floor**: Enforced minimum safety floor of 50 PWM (~20%) in both CLI helper and Python runtime to prevent motor stalling and heat accumulation.
- **Emergency Thermal Override**: Added automated fail-safe that restores fans to firmware dynamic curve (`pwm*_enable = 2`) immediately if temperatures reach $\ge 80^\circ\text{C}$.
- **Exit & Signal Restoration**: Registered signal handlers (`SIGINT`, `SIGTERM`, `SIGHUP`) and `atexit` hooks to guarantee firmware dynamic curves are restored on exit, crash, or stop.
- **Sensor Staleness Failsafe**: Automatic recovery if sensor reads fail or become unreadable.

### Thermal Architecture & Features
- **Graduated Thermal Limiter**: Replaced binary flapping with a 4-stage graduated regulation pipeline (Normal $\rightarrow$ EPP power scaling $\rightarrow$ Turbo Boost disabled $\rightarrow$ Quiet profile) protected by a 30-second minimum dwell timer and 5°C hysteresis.
- **Headless Daemon & Systemd Service**: Added `--daemon` flag and systemd unit (`thermals.service`) so the thermostat limiter runs reliably in the background without requiring the Tkinter GUI.
- **Battery Longevity Control**: Added support for setting hardware battery charge thresholds (`charge_control_end_threshold`) to prolong battery lifespan (e.g. 60%, 80%, 100%).
- **CPU Throttling & Resource Attribution**: Added real-time CPU throttling detection based on frequency drops under thermal load, plus top CPU consumer process sampling.
- **Tactile UI Controls**: Redesigned dials and knobs with clean Dieter Rams/Braun-inspired slate palette; removed emojis across tabs, buttons, and status indicators.
- **Desktop Push Notifications**: Anti-spam rate-limited desktop notifications via `notify-send` for critical temperatures, plus `--test-alert` flag.
- **Config Migration**: Automatic transparent migration from legacy `~/.config/asus-thermal-control-center` to `~/.config/thermals`.

### Testing & Infrastructure
- **Automated Test Suite**: Added mock `/sys` fixtures in `tests/conftest.py` with 100% test coverage across sensor parsing, controls, safety rails, and graduated limiter logic.
- **GitHub Actions CI**: Added continuous integration workflow running flake8 linting, pytest matrix, and package builds.
- **Packaging Consolidation**: Consolidated install scripts, added `--enable-service` and `--uninstall` to `install.sh`, and dropped claims of untested distributions.

---

## [1.0.0] - 2026-11-12

### Added
- Initial release featuring multi-category hardware detection (`hwmon`, `thermal_zone`, `power_supply`, NVMe, Wi-Fi, battery).
- Dual graphical (Tkinter) and terminal (CLI/JSON) interfaces.
- ACPI platform profile switching (`quiet`, `balanced`, `performance`).
- CPU Turbo Boost switch and basic threshold limiter.
- Packaging support for Debian (`.deb`), Arch Linux (`PKGBUILD`), and RPM (`.spec`).
