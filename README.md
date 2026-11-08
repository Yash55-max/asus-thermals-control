# Thermals

The existing ASUS thermal dashboard packaged as a desktop application named **Thermals**.

## Install

Extract the archive, then run:

```bash
cd thermals-app
bash install-thermals.sh
```

Open the Ubuntu application menu and search for **Thermals**.

## Launch from terminal

```bash
python3 ~/.local/opt/thermals/app.py
```

## Notes

- This only installs a desktop launcher for the existing dashboard.
- It does not create an autostart entry.
- Existing thermal monitoring, alerts, and profile-switching behavior is unchanged.
- To enable the already-existing automatic profile switching permission helper, run:

```bash
bash ~/.local/opt/thermals/install-helper.sh
```
