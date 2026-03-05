"""Cross-platform utilities for OpenCat.

Centralises all platform-specific calls (DWM, DPI, transparency, work-area)
so the rest of the app remains platform-agnostic.
"""

from __future__ import annotations

import sys

PLATFORM = sys.platform  # "win32", "darwin", "linux"
IS_WIN = PLATFORM == "win32"
IS_MAC = PLATFORM == "darwin"
IS_LINUX = PLATFORM.startswith("linux")


def get_work_area(root) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the usable screen area.

    On Windows this excludes the taskbar; on other platforms it returns
    the full screen dimensions (good enough for positioning).
    """
    if IS_WIN:
        try:
            import ctypes
            import ctypes.wintypes
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0,
            )
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
    # Fallback: full screen via tkinter
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def apply_transparent_color(win, color: str) -> None:
    """Apply pixel-level transparency via -transparentcolor (Windows only).

    On macOS / Linux the call is silently skipped — the window will simply
    show its background colour.  Callers should set a suitable fallback
    bg if cross-platform look matters.
    """
    if IS_WIN:
        try:
            win.attributes("-transparentcolor", color)
        except Exception:
            pass


def apply_borderless_shadow(win) -> None:
    """Apply native rounded corners + shadow on a borderless window (Win11+).

    No-op on non-Windows platforms.
    """
    if not IS_WIN:
        return
    try:
        import ctypes
        import ctypes.wintypes as wintypes
        hwnd = wintypes.HWND(win.winfo_id())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        value = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
    except Exception:
        pass


def set_dpi_awareness() -> None:
    """Set per-monitor DPI awareness on Windows.  No-op elsewhere."""
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def dwm_flush() -> None:
    """Call DwmFlush() on Windows to prevent ghost artefacts after resize.

    No-op on other platforms.
    """
    if not IS_WIN:
        return
    try:
        import ctypes
        ctypes.windll.dwmapi.DwmFlush()
    except Exception:
        pass
