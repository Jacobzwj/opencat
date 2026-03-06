"""Cross-platform utilities for OpenCat.

Centralises all platform-specific calls (DWM, DPI, transparency, work-area)
so the rest of the app remains platform-agnostic.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

PLATFORM = sys.platform  # "win32", "darwin", "linux"
IS_WIN = PLATFORM == "win32"
IS_MAC = PLATFORM == "darwin"
IS_LINUX = PLATFORM.startswith("linux")

_mac_tkwindow_patched = False


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


def apply_mac_transparent(win) -> None:
    """Apply native macOS window transparency for canvas-primitive windows.

    Uses -transparent attribute + systemTransparent background.  Works for
    canvas shapes (create_oval, create_polygon, create_text) but NOT for
    create_image (PhotoImage).  For image-based windows use
    setup_mac_image_window() instead.
    No-op on non-macOS platforms.
    """
    if not IS_MAC:
        return
    try:
        win.attributes("-transparent", True)
        win.config(bg="systemTransparent")
    except Exception:
        pass


_PassthroughImageView = None  # created once, reused


def _get_passthrough_class():
    """Return (and cache) an NSImageView subclass that ignores mouse events."""
    global _PassthroughImageView
    if _PassthroughImageView is not None:
        return _PassthroughImageView
    from AppKit import NSImageView

    class _Passthrough(NSImageView):
        def hitTest_(self, point):
            return None

    _PassthroughImageView = _Passthrough
    return _PassthroughImageView


def setup_mac_image_window(win, width: int, height: int):
    """Set up a macOS transparent window with a native NSImageView.

    Tkinter's ``create_image`` is invisible on transparent macOS windows.
    This function adds a native AppKit NSImageView behind the (transparent)
    tkinter canvas so that RGBA images render with proper alpha compositing.

    Returns the NSImageView instance (or None on failure / non-macOS).
    Caller should use ``update_mac_image()`` to push frames into it.
    """
    if not IS_MAC:
        return None
    try:
        from AppKit import (
            NSApplication, NSColor,
            NSImageScaleProportionallyUpOrDown,
        )
        from Foundation import NSMakeRect

        win.attributes("-transparent", True)
        win.config(bg="systemTransparent")
        win.update_idletasks()

        Passthrough = _get_passthrough_class()

        ns_app = NSApplication.sharedApplication()
        wid = win.winfo_id()
        ns_win = None
        for nw in ns_app.windows():
            if nw.windowNumber() == wid:
                ns_win = nw
                break
        if ns_win is None:
            for nw in ns_app.windows():
                f = nw.frame()
                if (abs(int(f.size.width) - width) < 20
                        and abs(int(f.size.height) - height) < 20):
                    ns_win = nw
                    break
        if ns_win is None:
            log.warning("setup_mac_image_window: NSWindow not found")
            return None

        ns_win.setBackgroundColor_(NSColor.clearColor())
        ns_win.setOpaque_(False)
        ns_win.setHasShadow_(False)

        cv = ns_win.contentView()
        cv_h = int(cv.frame().size.height)
        iv = Passthrough.alloc().initWithFrame_(NSMakeRect(0, 0, width, cv_h))
        iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
        iv.setEditable_(False)
        # Insert below tkinter canvas so mouse events reach the canvas
        cv.addSubview_positioned_relativeTo_(iv, 1, None)  # NSWindowBelow
        log.debug("NSImageView added to cat window (%dx%d)", width, cv_h)
        return iv
    except ImportError:
        log.warning("pyobjc-framework-Cocoa not installed; "
                     "cat transparency unavailable on macOS")
    except Exception as exc:
        log.warning("setup_mac_image_window failed: %s", exc)
    return None


def update_mac_image(image_view, pil_frame) -> None:
    """Push a PIL RGBA frame into a native NSImageView.

    No-op if *image_view* is None (non-macOS or setup failed).
    """
    if image_view is None:
        return
    try:
        import io
        from AppKit import NSImage
        from Foundation import NSData

        buf = io.BytesIO()
        pil_frame.save(buf, format="PNG")
        raw = buf.getvalue()
        ns_data = NSData.dataWithBytes_length_(raw, len(raw))
        ns_img = NSImage.alloc().initWithData_(ns_data)
        image_view.setImage_(ns_img)
        image_view.setNeedsDisplay_(True)
    except Exception:
        pass


def resize_mac_image_view(image_view, width: int, height: int) -> None:
    """Resize the native NSImageView after a cat scale change."""
    if image_view is None or not IS_MAC:
        return
    try:
        from Foundation import NSMakeRect
        image_view.setFrame_(NSMakeRect(0, 0, width, height))
    except Exception:
        pass


def apply_mac_clear_bg(win) -> None:
    """Make a macOS window's background fully transparent via PyObjC.

    Unlike ``apply_mac_transparent`` (which uses Tk's -transparent attribute
    and hides *all* content including images), this only clears the native
    NSWindow background so that customtkinter rounded-corner frames show
    through without a rectangular backdrop.
    No-op on non-macOS platforms.
    """
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication, NSColor
        win.update_idletasks()
        wid = win.winfo_id()
        ns_app = NSApplication.sharedApplication()
        for nw in ns_app.windows():
            if nw.windowNumber() == wid:
                nw.setBackgroundColor_(NSColor.clearColor())
                nw.setOpaque_(False)
                nw.setHasShadow_(True)
                return
        # Fallback: match by size
        tw = win.winfo_width()
        th = win.winfo_height()
        for nw in ns_app.windows():
            f = nw.frame()
            if (abs(int(f.size.width) - tw) < 10
                    and abs(int(f.size.height) - th) < 10):
                nw.setBackgroundColor_(NSColor.clearColor())
                nw.setOpaque_(False)
                nw.setHasShadow_(True)
                return
    except Exception:
        pass


def apply_transparent_color(win, color: str) -> None:
    """Apply pixel-level transparency.

    Windows: -transparentcolor makes the exact colour fully transparent.
    macOS/Linux: silently skipped (no reliable cross-platform equivalent).
    """
    if IS_WIN:
        try:
            win.attributes("-transparentcolor", color)
        except Exception:
            pass


def apply_borderless(win) -> None:
    """Make a window borderless (all platforms use overrideredirect)."""
    win.overrideredirect(True)


def _ensure_mac_tkwindow_patched() -> None:
    """Patch the ObjC TKWindow class so borderless windows can get keyboard focus.

    macOS borderless (overrideredirect) windows return NO from
    canBecomeKeyWindow, which prevents keyboard input.  This one-time
    class-level patch makes ALL TKWindow instances return YES.
    Requires pyobjc-framework-Cocoa.
    """
    global _mac_tkwindow_patched
    if _mac_tkwindow_patched:
        return
    try:
        import objc
        from AppKit import NSApplication

        ns_app = NSApplication.sharedApplication()
        for w in ns_app.windows():
            if w.__class__.__name__ == "TKWindow":
                TKWindowClass = type(w)

                def canBecomeKeyWindow(self):
                    return True

                def canBecomeMainWindow(self):
                    return True

                objc.classAddMethods(TKWindowClass, [
                    objc.selector(canBecomeKeyWindow,
                                  selector=b"canBecomeKeyWindow",
                                  signature=b"Z@:"),
                    objc.selector(canBecomeMainWindow,
                                  selector=b"canBecomeMainWindow",
                                  signature=b"Z@:"),
                ])
                _mac_tkwindow_patched = True
                log.debug("Patched TKWindow.canBecomeKeyWindow -> True")
                return
        log.warning("No TKWindow instance found for patching")
    except ImportError:
        log.warning("pyobjc-framework-Cocoa not installed; "
                     "borderless keyboard focus unavailable on macOS")
    except Exception as exc:
        log.warning("TKWindow patch failed: %s", exc)


def mac_make_key_window(tk_win) -> None:
    """Promote a tkinter window to the macOS key window (receives keyboard).

    Call after the window is mapped and patched.  No-op on non-macOS.
    """
    if not IS_MAC:
        return
    try:
        from AppKit import NSApplication
        tk_win.update_idletasks()
        wid = tk_win.winfo_id()
        ns_app = NSApplication.sharedApplication()
        for w in ns_app.windows():
            if w.windowNumber() == wid:
                w.makeKeyAndOrderFront_(None)
                return
        # Fallback: match by size
        target_w = tk_win.winfo_width()
        target_h = tk_win.winfo_height()
        for w in ns_app.windows():
            frame = w.frame()
            if (abs(int(frame.size.width) - target_w) < 10
                    and abs(int(frame.size.height) - target_h) < 10):
                w.makeKeyAndOrderFront_(None)
                return
    except Exception:
        pass


def apply_borderless_focusable(win) -> None:
    """Make a window borderless while keeping keyboard focus ability.

    Windows/Linux: overrideredirect(True) — keyboard works fine.
    macOS: overrideredirect(True) + PyObjC patch on TKWindow class so the
    window can become the key window and receive keyboard events.
    """
    win.overrideredirect(True)
    if IS_MAC:
        win.update_idletasks()
        _ensure_mac_tkwindow_patched()


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
