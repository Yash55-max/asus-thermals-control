# Contributing to Thermals

Thank you for your interest in contributing to **Thermals** (`asus-thermals-control`)! This document provides guidelines for development, testing, and hardware telemetry reporting.

---

## Code of Conduct & Principles

1. **Safety First**: Any code touching fan controls, power limits, or sysfs nodes must uphold strict safety rails (minimum fan floors, emergency thermal overrides, and failsafe restoration to firmware control on exit).
2. **Privilege Separation**: Never execute unvalidated commands as root. The privileged helper (`asus-thermal-profile`) must strictly validate all paths against canonical sysfs regexes and bounded numeric values.
3. **Truth in Claims**: Do not claim hardware support without hardware verification. Categorize hardware as *Verified*, *Expected (Standard ACPI)*, or *Untested*.

---

## Development Setup

Clone the repository and install test dependencies:

```bash
git clone https://github.com/Yash55-max/asus-thermals-control.git
cd asus-thermals-control

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate

# Install test dependencies
pip install pytest flake8
```

---

## Running Tests

Thermals includes an automated test suite with synthetic `/sys` fixtures. No hardware or root privileges are required to run tests:

```bash
# Run all tests
pytest -v tests/

# Run flake8 linter
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
```

### Adding New Test Fixtures

When adding support for new sensors or controller chips, add a corresponding sysfs mockup to `tests/conftest.py` and write unit tests in `tests/test_sensors.py` or `tests/test_controls.py`.

---

## Submitting Hardware Compatibility Reports

If you test Thermals on a new laptop model or hardware configuration, please open an issue using the [Hardware Report Template](.github/ISSUE_TEMPLATE/hardware-report.md) with:

```bash
# Output of status command
thermals --status

# Full hardware inventory
thermals --list-hardware
```

Include whether ACPI platform profiles, battery limits, or fan controls worked on your machine.

---

## Submitting Pull Requests

1. Create a feature branch: `git checkout -b feature/my-feature`.
2. Ensure all tests pass: `pytest -v tests/`.
3. Keep commits atomic and descriptive.
4. Update `CHANGELOG.md` with your changes.
5. Open a Pull Request against the `main` branch.
