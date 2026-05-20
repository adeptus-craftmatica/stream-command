# Stream Control

Stream Control is a Python desktop application for running a professional streaming control center across Windows, macOS, and Linux.

## Current foundation

- Native desktop shell with PySide6
- Plugin host architecture for feature isolation
- Built-in plugins for dashboard, integrations, music, soundboard, and hotkeys
- OBS service boundary via obs-websocket
- Streamlabs Desktop service boundary via PySLOBS
- Music library and queue management
- Local "now playing" overlay server for browser sources
- Soundboard pad management
- Global hotkey registration

## Plugin model

Each feature lives inside its own plugin package under `stream_control/plugins`:

- Each plugin owns its own page UI, settings model, runtime logic, and lifecycle hooks.
- Shared coordination happens through the plugin host and a small service registry, instead of `MainWindow` hardcoding feature relationships.
- New features can be added by creating a plugin that registers its page, services, and optional hotkey actions.

## Quick start

### Windows

```powershell
.\.venv\Scripts\pip.exe install -e .[dev]
.\.venv\Scripts\python.exe main.py
```

### macOS and Linux

```bash
python3 -m venv .venv
./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python main.py
```

## macOS notes

- Global hotkeys need macOS privacy approval before they can listen outside the app.
- If Hotkeys shows a permission warning, allow your `python` interpreter or packaged app under `System Settings -> Privacy & Security -> Accessibility`.
- Some macOS versions also require the same app under `Input Monitoring` before background shortcuts will fire reliably.

## Connection notes

- OBS Studio: enable `Tools -> obs-websocket Server Settings`, then use the host, port, and password in the app.
- Streamlabs Desktop: open `Settings -> Remote Control`, reveal the token, and use the host, port, and token in the app.

## Overlay URL

When the app is running, use this browser source in OBS or Streamlabs Desktop:

`http://127.0.0.1:18181/overlay/now-playing`
