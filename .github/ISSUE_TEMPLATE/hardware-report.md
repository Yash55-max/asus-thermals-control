---
name: Hardware Compatibility Report
about: Submit telemetry from your hardware to expand the compatibility table
title: '[Hardware Report] <Vendor> <Model>'
labels: ['hardware-compatibility']
assignees: ''
---

### Device Information
- **Manufacturer / Vendor:**
- **Product Model:**
- **Processor (CPU):**
- **Graphics (GPU):**
- **Linux Distribution & Version:**
- **Kernel Version (`uname -r`):**

### Sensor Discovery Output
Please paste the output of `thermals --status`:
```text
(paste thermals --status output here)
```

### Full Hardware Inventory
Please paste the output of `thermals --list-hardware`:
```text
(paste thermals --list-hardware output here)
```

### Control Verification
Please check which controls work on your hardware:
- [ ] Platform profile switching (`thermals --set-profile <quiet|balanced|performance>`)
- [ ] Target temperature limiter (`thermals --set-target-temp <celsius>`)
- [ ] CPU Turbo Boost toggling (`thermals --set-boost <on|off>`)
- [ ] Battery charge threshold (`thermals --set-battery-limit <pct>`)
- [ ] Fan control (`thermals --cooler-boost`)

### Additional Notes or Sysfs Anomalies
(Any quirks, missing sensors, or custom vendor WMI behaviors)
