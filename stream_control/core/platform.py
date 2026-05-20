from __future__ import annotations

import ctypes
import sys
from ctypes import util
from dataclasses import dataclass
from functools import lru_cache


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform.startswith("win")


@dataclass(frozen=True, slots=True)
class MacOSHotkeyPermissions:
    accessibility: bool | None
    input_monitoring: bool | None

    @property
    def missing_items(self) -> list[str]:
        missing: list[str] = []
        if self.accessibility is False:
            missing.append("Accessibility")
        if self.input_monitoring is False:
            missing.append("Input Monitoring")
        return missing

    @property
    def is_ready(self) -> bool:
        return not self.missing_items

    def summary(self) -> str:
        if not self.missing_items:
            return "macOS permissions are ready for global hotkeys."
        missing = " and ".join(self.missing_items)
        return (
            f"Global hotkeys need macOS {missing} access. Add Stream Control or the Python interpreter in "
            "System Settings > Privacy & Security, then relaunch the app."
        )


@lru_cache(maxsize=1)
def _application_services() -> ctypes.CDLL | None:
    library_path = util.find_library("ApplicationServices")
    if library_path is None:
        return None
    try:
        return ctypes.CDLL(library_path)
    except OSError:
        return None


def _call_platform_bool(symbol: str) -> bool | None:
    library = _application_services()
    if library is None:
        return None
    function = getattr(library, symbol, None)
    if function is None:
        return None
    try:
        function.restype = ctypes.c_bool
        return bool(function())
    except Exception:
        return None


def macos_hotkey_permissions() -> MacOSHotkeyPermissions | None:
    if not is_macos():
        return None
    return MacOSHotkeyPermissions(
        accessibility=_call_platform_bool("AXIsProcessTrusted"),
        input_monitoring=_call_platform_bool("CGPreflightListenEventAccess"),
    )
