"""OpenCat application — tkinter floating cat + customtkinter chat window.

tkinter is used for the floating cat because it supports true pixel-level
transparent click-through on Windows via -transparentcolor.
customtkinter is used for the chat window for a modern dark UI.
GIF animations are loaded from ui/assets/ via Pillow.
"""

from __future__ import annotations

import base64
import datetime as _dt
import io
import json
import logging
import os
import random
import threading
import time as _time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageSequence, ImageTk

from opencat import config
from opencat.history import SessionManager
from opencat.platform_utils import (
    IS_WIN,
    IS_MAC,
    apply_borderless,
    apply_borderless_shadow,
    apply_transparent_color,
    dwm_flush,
    get_work_area,
    set_dpi_awareness,
)
from opencat.state import CatState
from opencat.ws_client import OpenClawClient

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent / "ui"
ASSETS_DIR = UI_DIR / "assets"
MANIFEST_PATH = ASSETS_DIR / "manifest.json"

MAX_CAT_DIM = 220
TRANSPARENT_COLOR = "#010101"
ANIMATION_INTERVAL_MS = 150  # ms per frame
DONE_DISPLAY_MS = 3000
IDLE_BEHAVIOR_INTERVAL_MS = (15000, 30000)  # ms between idle behavior switches
DEFAULT_CHAT_WIDTH = 600
DEFAULT_CHAT_HEIGHT = 820
MIN_CHAT_WIDTH = 420
MIN_CHAT_HEIGHT = 560
DEFAULT_FONT_SIZE = 14
MIN_FONT_SIZE = 10
MAX_FONT_SIZE = 20
MIN_CAT_SCALE = 0.6
MAX_CAT_SCALE = 2.2
CAT_SCALE_STEP = 0.1

# Chat UI theme (warm, cat-friendly pastel)
CHAT_BG = "#fdf4ec"
CHAT_SURFACE = "#fffaf6"
CHAT_SURFACE_ELEVATED = "#f9e3d0"
CHAT_BORDER = "#eac7aa"
CHAT_TEXT_PRIMARY = "#5b3f2b"
CHAT_TEXT_MUTED = "#9a7a63"
CHAT_ACCENT = "#88c98f"
CHAT_PINK = "#f6bfd8"
CHAT_LAVENDER = "#c9bcff"
CHAT_MAUVE = "#d79ac4"
CHAT_USER_BUBBLE = "#ffe0eb"
CHAT_ASSISTANT_BUBBLE = "#fff4d7"
CHAT_ERROR_BUBBLE = "#ffd9de"
CHAT_INPUT_BG = "#fffdfa"
CHAT_BUTTON_BG = "#f4a261"
CHAT_BUTTON_HOVER = "#eb8f4a"
CHAT_BUTTON_TEXT = "#3f2a1a"
CHAT_PAW = "#e88fb2"
CHAT_SCROLL = "#d7b79b"
CHAT_SCROLL_HOVER = "#caa486"
CHAT_GREEN_STRONG = "#2f8f5b"
BUBBLE_ANIM_STEPS = 4
BUBBLE_ANIM_DELAY_MS = 22
_CHAT_TRANSPARENT = "#010102"


def _rounded_rect(cvs, x1, y1, x2, y2, r, **kw):
    """Draw a rounded rectangle on a tk.Canvas using smooth polygon."""
    points = [
        x1 + r, y1, x2 - r, y1,
        x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]
    return cvs.create_polygon(points, smooth=True, **kw)


# ── GIF loader ──

def _load_gif_frames(gif_path: Path) -> list[Image.Image]:
    """Load an animated GIF and return RGBA PIL frames."""
    try:
        img = Image.open(gif_path)
    except Exception as e:
        log.warning("Failed to load %s: %s", gif_path, e)
        return []

    frames: list[Image.Image] = []
    for frame in ImageSequence.Iterator(img):
        f = frame.copy().convert("RGBA")

        # Keep aspect ratio; only shrink large frames.
        fw, fh = f.size
        if fw > MAX_CAT_DIM or fh > MAX_CAT_DIM:
            scale = min(MAX_CAT_DIM / fw, MAX_CAT_DIM / fh)
            f = f.resize((max(1, int(fw * scale)), max(1, int(fh * scale))), Image.NEAREST)
        frames.append(f)
    return frames


def _load_all_gifs() -> tuple[dict[str, list[Image.Image]], list[list[Image.Image]]]:
    """Load GIFs from manifest.json.

    Returns (state_gifs, idle_pool):
      - state_gifs: maps state name -> frames for fixed states
      - idle_pool: list of frame-lists for random idle behaviors
    """
    mapping: dict = {}
    if MANIFEST_PATH.exists():
        mapping = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    state_gifs: dict[str, list[Image.Image]] = {}
    idle_pool: list[list[Image.Image]] = []

    for state_name, value in mapping.items():
        if state_name == "idle" and isinstance(value, list):
            for rel_path in value:
                gif_path = UI_DIR / rel_path
                if gif_path.exists():
                    frames = _load_gif_frames(gif_path)
                    if frames:
                        idle_pool.append(frames)
                        log.info("Loaded idle behavior: %s (%d frames)", rel_path, len(frames))
        else:
            gif_path = UI_DIR / value
            if gif_path.exists():
                frames = _load_gif_frames(gif_path)
                if frames:
                    state_gifs[state_name] = frames
                    log.info("Loaded %s: %d frames", state_name, len(frames))

    return state_gifs, idle_pool


def _get_max_frame_size(state_gifs: dict[str, list[Image.Image]],
                        idle_pool: list[list[Image.Image]]) -> tuple[int, int]:
    max_w, max_h = 120, 120
    for frames in state_gifs.values():
        for frame in frames:
            max_w = max(max_w, frame.width)
            max_h = max(max_h, frame.height)
    for frames in idle_pool:
        for frame in frames:
            max_w = max(max_w, frame.width)
            max_h = max(max_h, frame.height)
    return max_w, max_h


# ── Custom context menu ──


class _CatContextMenu:
    """Styled right-click context menu matching the warm theme."""

    def __init__(self, parent):
        self._parent = parent
        self._win: tk.Toplevel | None = None
        self._dismiss_id: str | None = None

    def show(self, x: int, y: int, items):
        """Show menu at screen (x, y). items: list of (label, cb) or None for separator."""
        self.dismiss()

        self._win = tk.Toplevel(self._parent)
        apply_borderless(self._win)
        self._win.attributes("-topmost", True)
        apply_transparent_color(self._win, TRANSPARENT_COLOR)
        bg_color = TRANSPARENT_COLOR if IS_WIN else ("systemTransparent" if IS_MAC else "#fff0e8")
        self._win.configure(bg=bg_color)
        self._win.withdraw()

        item_h, sep_h, pad_y, w = 38, 11, 10, 160
        content_h = pad_y * 2
        for it in items:
            content_h += sep_h if it is None else item_h

        cvs = tk.Canvas(self._win, width=w, height=content_h,
                        bg=bg_color, highlightthickness=0)
        cvs.pack()

        # Rounded background
        _rounded_rect(cvs, 2, 2, w - 2, content_h - 2, 14,
                      fill="#fff0e8", outline="#eac7aa", width=2)

        # Build hit zones for hover / click
        hit_zones: list[tuple[int, int, int, object]] = []  # (y0, y1, rect_id, callback)

        y_off = pad_y
        for it in items:
            if it is None:
                cvs.create_line(18, y_off + 5, w - 18, y_off + 5,
                                fill="#eac7aa", width=1)
                y_off += sep_h
            else:
                label, callback = it
                hr = cvs.create_rectangle(
                    8, y_off + 2, w - 8, y_off + item_h - 2,
                    fill="#fff0e8", outline="", width=0)
                cvs.create_text(
                    w // 2, y_off + item_h // 2,
                    text=label, font=("Segoe UI", 12), fill="#5b3f2b")
                hit_zones.append((y_off, y_off + item_h, hr, callback))
                y_off += item_h

        def _motion(event):
            for ys, ye, hr, _ in hit_zones:
                cvs.itemconfigure(
                    hr, fill="#ffdfc0" if ys <= event.y <= ye else "#fff0e8")

        def _leave(_event):
            for _, _, hr, _ in hit_zones:
                cvs.itemconfigure(hr, fill="#fff0e8")

        def _click(event):
            for ys, ye, _, cb in hit_zones:
                if ys <= event.y <= ye:
                    self._invoke(cb)
                    break

        cvs.bind("<Motion>", _motion)
        cvs.bind("<Leave>", _leave)
        cvs.bind("<Button-1>", _click)

        self._win.geometry(f"{w}x{content_h}+{x}+{y}")
        self._win.deiconify()
        self._win.focus_set()
        self._win.bind("<FocusOut>", lambda _e: self._schedule_dismiss())
        self._win.bind("<Escape>", lambda _e: self.dismiss())

    def _schedule_dismiss(self):
        if self._win:
            self._dismiss_id = self._win.after(120, self.dismiss)

    def _invoke(self, callback):
        if self._dismiss_id and self._win:
            self._win.after_cancel(self._dismiss_id)
            self._dismiss_id = None
        self.dismiss()
        callback()

    def dismiss(self):
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
            self._dismiss_id = None


# ── Controller (shared state between cat + chat + websocket) ──
#
# WebSocket callbacks arrive on a background thread.  All UI-touching work
# is dispatched to the main tkinter thread via root.after / after_idle to
# avoid flicker, race conditions and crashes.

DELTA_FLUSH_MS = 40  # batch streaming deltas, flush to UI at this interval

class Controller:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.state = CatState.SLEEPING  # Start sleeping until connected
        self.status_text = "Connecting..."
        self.status_color = "#f0c040"
        self._streaming_active = False
        self._reconnect_delay = 5.0
        self._reconnect_timer: threading.Timer | None = None
        self._done_timer: threading.Timer | None = None
        self._idle_sleep_timer: threading.Timer | None = None

        # Delta buffering (written by WS thread, read by main thread)
        self._delta_lock = threading.Lock()
        self._delta_buf: list[str] = []
        self._delta_flush_id: str | None = None

        # Callbacks set by UI components
        self.on_state_changed: list = []
        self.on_status_changed: list = []
        self.on_stream_begin: list = []
        self.on_stream_delta: list = []
        self.on_stream_final: list = []
        self.on_stream_error: list = []

        self.client = OpenClawClient(
            on_connected=self._ws_connected,
            on_disconnected=self._ws_disconnected,
            on_error=self._ws_error,
            on_delta=self._ws_delta,
            on_final=self._ws_final,
            on_chat_error=self._ws_chat_error,
        )

    # ── Thread dispatch helper ──

    def _to_main(self, fn, *args):
        """Schedule *fn(*args)* on the main tkinter thread."""
        try:
            self.root.after_idle(fn, *args)
        except Exception:
            pass

    # ── Public API (called from main thread) ──

    def start(self):
        self._set_status("Connecting...", "#f0c040")
        self.client.connect()

    def shutdown(self):
        self._cancel_idle_sleep_timer()
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        if self._done_timer:
            self._done_timer.cancel()
        with self._delta_lock:
            if self._delta_flush_id is not None:
                self.root.after_cancel(self._delta_flush_id)
                self._delta_flush_id = None
            self._delta_buf.clear()
        self.client.disconnect()

    def send_message(self, content, quiet=False):
        if not self.client.connected:
            for cb in self.on_stream_error:
                cb("Not connected to OpenClaw yet.")
            return
        self._cancel_idle_sleep_timer()
        if not quiet:
            self._set_state(CatState.THINKING)
        self.client.send_message(content)

    # ── State / status (always called on main thread) ──

    def _set_state(self, new_state: CatState):
        self.state = new_state
        # No idle→sleeping timer: sleeping is only for disconnected state,
        # handled directly in _handle_disconnected / _handle_connected.
        for cb in self.on_state_changed:
            cb(new_state)

    def _set_status(self, text: str, color: str):
        self.status_text = text
        self.status_color = color
        for cb in self.on_status_changed:
            cb(text, color)

    # ── Timers (fire on background threads → dispatch to main) ──

    def _cancel_idle_sleep_timer(self):
        if self._idle_sleep_timer:
            self._idle_sleep_timer.cancel()
            self._idle_sleep_timer = None

    def _schedule_reconnect(self):
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        self._reconnect_timer = threading.Timer(
            self._reconnect_delay, self._try_reconnect,
        )
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()
        self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    def _try_reconnect(self):
        if not self.client.connected:
            self._to_main(self._set_status, "Connecting...", "#f0c040")
            self.client.connect()

    # ── WebSocket callbacks (called from WS background thread) ──

    def _ws_connected(self):
        self._to_main(self._handle_connected)

    def _ws_disconnected(self):
        self._to_main(self._handle_disconnected)

    def _ws_error(self, msg: str):
        self._to_main(self._handle_error, msg)

    def _ws_delta(self, text: str):
        """Buffer deltas and flush to UI at a fixed interval."""
        with self._delta_lock:
            self._delta_buf.append(text)
            if self._delta_flush_id is None:
                try:
                    self._delta_flush_id = self.root.after(
                        DELTA_FLUSH_MS, self._flush_deltas,
                    )
                except Exception:
                    pass

    def _ws_final(self, text: str):
        self._to_main(self._handle_final, text)

    def _ws_chat_error(self, msg: str):
        self._to_main(self._handle_chat_error, msg)

    # ── Handlers (always run on main thread) ──

    def _handle_connected(self):
        self._reconnect_delay = 5.0
        self._set_status("Connected", "#7fe0a0")
        self._cancel_idle_sleep_timer()
        if self.state in (CatState.ERROR, CatState.SLEEPING):
            self._set_state(CatState.IDLE)

    def _handle_disconnected(self):
        self._streaming_active = False
        self._cancel_idle_sleep_timer()
        self._set_status("Disconnected", "#888888")
        self._set_state(CatState.SLEEPING)
        self._schedule_reconnect()

    def _handle_error(self, msg: str):
        self._streaming_active = False
        self._set_status("Error", "#ff6b6b")
        self._set_state(CatState.ERROR)
        self._schedule_reconnect()

    def _flush_deltas(self):
        """Flush buffered deltas to UI (runs on main thread)."""
        with self._delta_lock:
            buf = self._delta_buf[:]
            self._delta_buf.clear()
            self._delta_flush_id = None
        if not buf:
            return
        combined = "".join(buf)
        if not self._streaming_active:
            self._streaming_active = True
            for cb in self.on_stream_begin:
                cb()
        for cb in self.on_stream_delta:
            cb(combined)

    def _handle_final(self, text: str):
        # Flush any pending deltas first
        with self._delta_lock:
            buf = self._delta_buf[:]
            self._delta_buf.clear()
            if self._delta_flush_id is not None:
                try:
                    self.root.after_cancel(self._delta_flush_id)
                except Exception:
                    pass
                self._delta_flush_id = None
        if buf:
            combined = "".join(buf)
            if not self._streaming_active:
                self._streaming_active = True
                for cb in self.on_stream_begin:
                    cb()
            for cb in self.on_stream_delta:
                cb(combined)
        # Now handle final
        self._streaming_active = False
        for cb in self.on_stream_final:
            cb(text)
        self._set_state(CatState.DONE)
        if self._done_timer:
            self._done_timer.cancel()
        self._done_timer = threading.Timer(
            DONE_DISPLAY_MS / 1000,
            lambda: self._to_main(self._set_state, CatState.IDLE),
        )
        self._done_timer.daemon = True
        self._done_timer.start()

    def _handle_chat_error(self, msg: str):
        self._streaming_active = False
        self._set_state(CatState.ERROR)
        for cb in self.on_stream_error:
            cb(msg)
        self.root.after(3000, lambda: self._set_state(CatState.IDLE))


# ── Floating Cat Window (tkinter, transparent) ──

class FloatingCat:
    def __init__(self, root: tk.Tk, controller: Controller,
                 state_gifs: dict[str, list[Image.Image]],
                 idle_pool: list[list[Image.Image]],
                 on_click,
                 cat_width: int,
                 cat_height: int):
        self.root = root
        self.controller = controller
        self.state_gifs = state_gifs
        self.idle_pool = idle_pool
        self.on_click = on_click
        self._base_cat_width = cat_width
        self._base_cat_height = cat_height
        self.cat_width = cat_width
        self.cat_height = cat_height
        self._frame_idx = 0
        self._current_frames: list[ImageTk.PhotoImage] = []
        self._current_source_frames: list[Image.Image] = []
        self._render_cache: dict[tuple[int, int], list[ImageTk.PhotoImage]] = {}
        self._scale = 1.0
        self._image_ref = None
        self._dragged = False
        self._drag_sx = 0
        self._drag_sy = 0
        self._idle_behavior_idx = -1
        self._idle_cycle_after_id: str | None = None
        self._on_hide_chat = None  # callback set by run_app
        self._mini_win: tk.Toplevel | None = None
        self._tooltip: tk.Toplevel | None = None
        self._ctx_menu = _CatContextMenu(root)

        # Separate toplevel for the cat — keeps root hidden so CTkToplevel
        # creation won't resize or reposition the cat window.
        self.win = tk.Toplevel(root)
        apply_borderless(self.win)
        self.win.attributes("-topmost", True)
        apply_transparent_color(self.win, TRANSPARENT_COLOR)
        cat_bg = TRANSPARENT_COLOR if IS_WIN else ("systemTransparent" if IS_MAC else "#f0f0f0")
        self.win.configure(bg=cat_bg)

        self.canvas = tk.Canvas(self.win, width=self.cat_width, height=self.cat_height,
                                bg=cat_bg, highlightthickness=0)
        self.canvas.pack()

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._right_click)
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        self.win.bind("<MouseWheel>", self._mouse_wheel)
        self.canvas.bind("<Enter>", self._show_tooltip)
        self.canvas.bind("<Leave>", self._hide_tooltip)

        self._position()
        # Set initial GIF based on controller's current state
        self._on_state(controller.state)
        self._animate()

        controller.on_state_changed.append(self._on_state)

    def _position(self):
        _left, _top, right, bottom = get_work_area(self.root)
        x = right - self.cat_width - 20
        y = bottom - self.cat_height - 10
        self.win.geometry(f"{self.cat_width}x{self.cat_height}+{x}+{y}")

    def _on_state(self, new_state: CatState):
        if new_state == CatState.IDLE:
            self._start_idle_cycling()
        else:
            self._stop_idle_cycling()
            frames = self.state_gifs.get(new_state.value, [])
            if frames:
                self._set_source_frames(frames, reset_idx=True)

    def _start_idle_cycling(self):
        self._stop_idle_cycling()
        if self.idle_pool:
            self._idle_behavior_idx = random.randint(0, len(self.idle_pool) - 1)
            self._set_source_frames(self.idle_pool[self._idle_behavior_idx], reset_idx=True)
            self._schedule_next_idle_behavior()

    def _schedule_next_idle_behavior(self):
        delay = random.randint(*IDLE_BEHAVIOR_INTERVAL_MS)
        self._idle_cycle_after_id = self.win.after(delay, self._next_idle_behavior)

    def _next_idle_behavior(self):
        self._idle_cycle_after_id = None
        if self.controller.state != CatState.IDLE or not self.idle_pool:
            return
        old_idx = self._idle_behavior_idx
        if len(self.idle_pool) > 1:
            while self._idle_behavior_idx == old_idx:
                self._idle_behavior_idx = random.randint(0, len(self.idle_pool) - 1)
        else:
            self._idle_behavior_idx = 0
        self._set_source_frames(self.idle_pool[self._idle_behavior_idx], reset_idx=True)
        self._schedule_next_idle_behavior()

    def _stop_idle_cycling(self):
        if self._idle_cycle_after_id is not None:
            self.win.after_cancel(self._idle_cycle_after_id)
            self._idle_cycle_after_id = None

    def _animate(self):
        if self._current_frames:
            frame = self._current_frames[self._frame_idx % len(self._current_frames)]
            self.canvas.delete("all")
            self.canvas.create_image(self.cat_width // 2, self.cat_height // 2, image=frame)
            self._image_ref = frame
            self._frame_idx += 1
        self.win.after(ANIMATION_INTERVAL_MS, self._animate)

    def _render_frames(self, frames: list[Image.Image]) -> list[ImageTk.PhotoImage]:
        scale_key = int(round(self._scale * 100))
        cache_key = (id(frames), scale_key)
        cached = self._render_cache.get(cache_key)
        if cached:
            return cached
        rendered: list[ImageTk.PhotoImage] = []
        for frame in frames:
            if scale_key == 100:
                img = frame
            else:
                target_w = max(1, int(frame.width * self._scale))
                target_h = max(1, int(frame.height * self._scale))
                img = frame.resize((target_w, target_h), Image.NEAREST)
            rendered.append(ImageTk.PhotoImage(img))
        self._render_cache[cache_key] = rendered
        return rendered

    def _set_source_frames(self, frames: list[Image.Image], reset_idx: bool):
        self._current_source_frames = frames
        self._current_frames = self._render_frames(frames)
        if reset_idx:
            self._frame_idx = 0

    def _resize_window_keep_center(self):
        new_w = max(1, int(self._base_cat_width * self._scale))
        new_h = max(1, int(self._base_cat_height * self._scale))
        old_w = self.cat_width
        old_h = self.cat_height
        center_x = self.win.winfo_x() + old_w // 2
        center_y = self.win.winfo_y() + old_h // 2
        new_x = max(0, center_x - new_w // 2)
        new_y = max(0, center_y - new_h // 2)
        self.cat_width = new_w
        self.cat_height = new_h
        self.canvas.configure(width=new_w, height=new_h)
        self.win.geometry(f"{new_w}x{new_h}+{new_x}+{new_y}")

    def _mouse_wheel(self, event):
        step = 0
        if event.delta > 0:
            step = CAT_SCALE_STEP
        elif event.delta < 0:
            step = -CAT_SCALE_STEP
        if step == 0:
            return
        next_scale = round(self._scale + step, 2)
        next_scale = max(MIN_CAT_SCALE, min(MAX_CAT_SCALE, next_scale))
        if next_scale == self._scale:
            return
        self._scale = next_scale
        self._resize_window_keep_center()
        if self._current_source_frames:
            self._set_source_frames(self._current_source_frames, reset_idx=False)

    def _press(self, e):
        self._drag_sx = e.x_root
        self._drag_sy = e.y_root
        self._dragged = False

    def _drag(self, e):
        dx = e.x_root - self._drag_sx
        dy = e.y_root - self._drag_sy
        if abs(dx) > 5 or abs(dy) > 5:
            self._dragged = True
        if self._dragged:
            x = self.win.winfo_x() + dx
            y = self.win.winfo_y() + dy
            self.win.geometry(f"+{x}+{y}")
            self._drag_sx = e.x_root
            self._drag_sy = e.y_root

    def _release(self, e):
        if not self._dragged:
            self.on_click()

    def _right_click(self, e):
        self._ctx_menu.show(e.x_root, e.y_root, [
            ("隐藏猫猫", self._hide_self),
            None,
            ("退出", self.root.quit),
        ])

    def _hide_self(self):
        """Hide cat and show a tiny pink dot to restore."""
        self._hide_tooltip(None)
        if self._on_hide_chat:
            self._on_hide_chat()
        self.win.withdraw()
        self._show_mini_indicator()

    def _show_mini_indicator(self):
        if self._mini_win is None:
            self._mini_win = tk.Toplevel(self.root)
            apply_borderless(self._mini_win)
            self._mini_win.attributes("-topmost", True)
            sz = 26
            cvs = tk.Canvas(self._mini_win, width=sz, height=sz,
                            bg="#e88fb2", highlightthickness=0, cursor="hand2")
            cvs.pack()
            cvs.create_oval(3, 3, sz - 3, sz - 3, fill="#f4cfda",
                            outline="#d07098", width=2)
            cvs.create_text(sz // 2, sz // 2, text="\u2022",
                            font=("Segoe UI", 8), fill="#d07098")
            cvs.bind("<Button-1>", lambda e: self._restore_from_mini())
        cx = self.win.winfo_x() + self.cat_width // 2 - 13
        cy = self.win.winfo_y() + self.cat_height // 2 - 13
        self._mini_win.geometry(f"26x26+{cx}+{cy}")
        self._mini_win.deiconify()

    def _restore_from_mini(self):
        if self._mini_win:
            self._mini_win.withdraw()
        self.win.deiconify()

    def _show_tooltip(self, event):
        if self._tooltip:
            return
        self._tooltip = tk.Toplevel(self.win)
        self._tooltip.overrideredirect(True)
        self._tooltip.attributes("-topmost", True)
        apply_transparent_color(self._tooltip, TRANSPARENT_COLOR)
        tip_bg = TRANSPARENT_COLOR if IS_WIN else ("systemTransparent" if IS_MAC else "#fff0e8")
        self._tooltip.configure(bg=tip_bg)

        text = "滚轮缩放  |  左键聊天  |  右键菜单"
        tip_font = tkfont.Font(family="Segoe UI", size=12)
        text_w = tip_font.measure(text)
        w = text_w + 40  # padding on both sides
        h, tail_h = 44, 10
        total_h = h + tail_h
        cvs = tk.Canvas(self._tooltip, width=w, height=total_h,
                        bg=tip_bg, highlightthickness=0)
        cvs.pack()

        # Rounded bubble body (smooth polygon)
        r = 14
        body = [
            r, 1, w - r, 1,
            w - 1, 1, w - 1, r,
            w - 1, h - r, w - 1, h,
            w - r, h, r, h,
            1, h, 1, h - r,
            1, r, 1, 1, r, 1,
        ]
        cvs.create_polygon(body, fill="#fff0e8", outline="#eac7aa",
                           width=2, smooth=True)
        # Speech-bubble tail pointing down
        tx = w // 2
        cvs.create_polygon(
            tx - 10, h - 1, tx + 10, h - 1, tx, total_h - 2,
            fill="#fff0e8", outline="#eac7aa", width=2,
        )
        cvs.create_line(tx - 9, h, tx + 9, h, fill="#fff0e8", width=3)
        # Text
        cvs.create_text(w // 2, h // 2, text=text,
                        font=tip_font, fill="#5b3f2b")

        x = event.x_root - w // 2
        y = event.y_root - total_h - 8
        self._tooltip.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _hide_tooltip(self, event):
        if self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None


# ── Chat Window (customtkinter) ──

_KAOMOJI_THINKING = [
    "( •̀ ω •́ )✧  思考中...",
    "(ง •̀_•́)ง  努力想呢...",
    "(｀・ω・´)ゞ  分析中...",
    "( ˘ω˘ )  嗯嗯嗯...",
    "(*・ω・)ﾉ  好好想想...",
    "ε(•́ω•̀)з  动脑筋...",
]
_KAOMOJI_DONE = "( ≧▽≦)/  好嘞！"
_KAOMOJI_ERROR = "(╥_╥)  出了点问题..."


class ChatWindow:
    def __init__(self, root: tk.Tk, controller: Controller):
        self.root = root
        self.controller = controller
        self.cat_win: tk.Toplevel | None = None  # set after FloatingCat is created
        self.cat_height = 120
        self.visible = False
        self.window: ctk.CTkToplevel | None = None
        self._streaming_bubble = None
        self._streaming_text = ""
        self._message_labels: list = []   # tk.Text or ctk.CTkLabel widgets
        self._input_inner: ctk.CTkFrame | None = None
        self._empty_state_label: ctk.CTkLabel | None = None
        self._shell: ctk.CTkFrame | None = None
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._resize_start_x = 0
        self._resize_start_y = 0
        self._resize_start_w = 0
        self._resize_start_h = 0
        self._font_size = DEFAULT_FONT_SIZE
        self._input_has_placeholder = True
        # Attachment state
        self._pending_attachment: dict | None = None
        self._attach_container: ctk.CTkFrame | None = None
        self._attach_thumb_label: ctk.CTkLabel | None = None
        self._attach_name_label: ctk.CTkLabel | None = None
        # Thinking bubble state
        self._think_row: ctk.CTkFrame | None = None
        self._think_kaomoji_label: ctk.CTkLabel | None = None
        self._think_after_id: str | None = None
        self._think_kaomoji_idx: int = 0
        self._think_visible: bool = False
        # History
        self._history = SessionManager()
        self._history_panel: ctk.CTkFrame | None = None
        self._history_list: ctk.CTkScrollableFrame | None = None
        self._history_visible = False
        self._history_items: list[ctk.CTkFrame] = []
        self._active_session_id: str | None = None   # the session being chatted with server
        self._viewing_history = False                 # True when viewing a past (read-only) session
        self._history_banner: ctk.CTkFrame | None = None  # "回到当前对话" banner
        self._input_frame: ctk.CTkFrame | None = None

        controller.on_state_changed.append(self._on_cat_state)
        controller.on_status_changed.append(self._on_status)
        controller.on_stream_begin.append(self._on_begin)
        controller.on_stream_delta.append(self._on_delta)
        controller.on_stream_final.append(self._on_final)
        controller.on_stream_error.append(self._on_error)

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def pre_create(self):
        """Create the chat window at startup (hidden) so later show/hide
        won't trigger CTkToplevel creation which disturbs sibling windows."""
        self._create()
        self.window.withdraw()
        self._needs_restore = True  # deferred to first show()

    def show(self):
        self._update_status(self.controller.status_text, self.controller.status_color)
        self._position_near_cat()
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.visible = True
        # Restore history on first show (window must be visible for text measurement)
        if self._needs_restore:
            self._needs_restore = False
            self.window.update_idletasks()
            self._startup_restore()
        self._refresh_message_wraplengths()
        self.window.focus_force()
        self.input_entry.focus_set()
        # Keep cat visible above the chat
        if self.cat_win:
            self.cat_win.lift()

    def hide(self):
        if self.window:
            self.window.withdraw()
        self.visible = False

    def _position_near_cat(self):
        if self.cat_win is None:
            return
        cx = self.cat_win.winfo_x()
        cy = self.cat_win.winfo_y()
        self.window.update_idletasks()
        chat_w = self.window.winfo_width()
        chat_h = self.window.winfo_height()
        # Before first display, winfo returns 1 for unmapped windows
        if chat_w < MIN_CHAT_WIDTH:
            chat_w = DEFAULT_CHAT_WIDTH
        if chat_h < MIN_CHAT_HEIGHT:
            chat_h = DEFAULT_CHAT_HEIGHT
        # Chat pops up to the upper-left of the cat, no overlap
        x = max(0, cx - chat_w - 8)
        y = max(0, cy - chat_h + 20)
        # Use wm_geometry to bypass CTkToplevel's DPI scaling
        self.window.wm_geometry(f"{chat_w}x{chat_h}+{x}+{y}")

    def _create(self):
        self.window = ctk.CTkToplevel(self.root)
        apply_borderless(self.window)
        self.window.geometry(f"{DEFAULT_CHAT_WIDTH}x{DEFAULT_CHAT_HEIGHT}")
        # On Windows the transparent colour trick gives true rounded corners;
        # on other platforms we fall back to the surface colour.
        self.window.configure(fg_color=_CHAT_TRANSPARENT if IS_WIN else CHAT_SURFACE_ELEVATED)
        self.window.attributes("-topmost", True)
        self.window.bind("<Configure>", self._on_window_resize)
        self.window.update_idletasks()
        # Transparent corners → true irregular rounded shape (Windows only)
        apply_transparent_color(self.window, _CHAT_TRANSPARENT)
        apply_borderless_shadow(self.window)

        # ── Main shell — rounded card that IS the visible window ──
        self._shell = ctk.CTkFrame(
            self.window,
            fg_color=CHAT_SURFACE_ELEVATED,
            border_width=2,
            border_color=CHAT_BORDER,
            corner_radius=16,
        )
        self._shell.pack(fill="both", expand=True, padx=6, pady=6)

        # ── Cat ears (decorative, draggable) ──
        ear_row = ctk.CTkFrame(self._shell, fg_color="transparent", height=14, corner_radius=0)
        ear_row.pack(fill="x", padx=12, pady=(6, 0))
        ear_row.pack_propagate(False)
        self._bind_titlebar_drag(ear_row)
        ctk.CTkFrame(ear_row, width=24, height=14, corner_radius=8,
                     fg_color="#f4cfda").pack(side="left", padx=(32, 0))
        ctk.CTkFrame(ear_row, width=24, height=14, corner_radius=8,
                     fg_color="#f4cfda").pack(side="right", padx=(0, 32))

        # ── Custom title bar (draggable) ──
        title_bar = ctk.CTkFrame(self._shell, fg_color="transparent", height=46, corner_radius=0)
        title_bar.pack(fill="x", padx=10, pady=(2, 0))
        title_bar.pack_propagate(False)
        self._bind_titlebar_drag(title_bar)

        # Left: paw + title + status
        left = ctk.CTkFrame(title_bar, fg_color="transparent", corner_radius=0)
        left.pack(side="left", fill="y", padx=4, pady=2)
        self._bind_titlebar_drag(left)

        paw_lbl = ctk.CTkLabel(
            left, text="🐾", font=("Segoe UI Emoji", 15),
            text_color=CHAT_PAW, width=20,
        )
        paw_lbl.pack(side="left", padx=(0, 6))
        self._bind_titlebar_drag(paw_lbl)

        title_col = ctk.CTkFrame(left, fg_color="transparent", corner_radius=0)
        title_col.pack(side="left", fill="y")
        self._bind_titlebar_drag(title_col)

        title_lbl = ctk.CTkLabel(
            title_col, text="OpenCat Chat",
            font=("Segoe UI", 14, "bold"), text_color=CHAT_TEXT_PRIMARY,
        )
        title_lbl.pack(anchor="w")
        self._bind_titlebar_drag(title_lbl)

        status_row = ctk.CTkFrame(title_col, fg_color="transparent", corner_radius=0)
        status_row.pack(anchor="w")
        self._bind_titlebar_drag(status_row)

        self.status_dot = ctk.CTkLabel(
            status_row, text="\u25cf", font=("Segoe UI", 10),
            text_color=CHAT_TEXT_MUTED, width=12,
        )
        self.status_dot.pack(side="left", padx=(0, 3))

        self.status_label = ctk.CTkLabel(
            status_row, text="Disconnected",
            font=("Segoe UI", 11), text_color=CHAT_TEXT_MUTED,
        )
        self.status_label.pack(side="left")

        # Right: close button
        self._close_btn = ctk.CTkButton(
            title_bar, text="\u2715", width=28, height=28,
            fg_color="transparent", hover_color="#ffd4d4",
            text_color=CHAT_TEXT_MUTED, font=("Segoe UI", 14, "bold"),
            corner_radius=8, command=self.hide,
        )
        self._close_btn.pack(side="right", padx=(0, 2), pady=6)
        self._bind_tooltip(self._close_btn, "关闭窗口")

        # History toggle button
        hist_btn = ctk.CTkButton(
            title_bar, text="\U0001f4d6", width=28, height=24,
            fg_color="#ffeedd", hover_color="#ffe0c0",
            text_color=CHAT_TEXT_PRIMARY, font=("Segoe UI Emoji", 12),
            corner_radius=6, command=self._toggle_history_panel,
        )
        hist_btn.pack(side="right", padx=(0, 2), pady=10)
        self._bind_tooltip(hist_btn, "对话历史")

        # New chat button
        new_btn = ctk.CTkButton(
            title_bar, text="\u2795", width=28, height=24,
            fg_color="#ffeedd", hover_color="#ffe0c0",
            text_color=CHAT_TEXT_PRIMARY, font=("Segoe UI", 11),
            corner_radius=6, command=self._new_chat,
        )
        new_btn.pack(side="right", padx=(0, 2), pady=10)
        self._bind_tooltip(new_btn, "新建对话")

        # Compact context button
        compact_btn = ctk.CTkButton(
            title_bar, text="\U0001f4e6", width=28, height=24,
            fg_color="#ffeedd", hover_color="#ffe0c0",
            text_color=CHAT_TEXT_PRIMARY, font=("Segoe UI Emoji", 11),
            corner_radius=6, command=self._compact_context,
        )
        compact_btn.pack(side="right", padx=(0, 2), pady=10)
        self._bind_tooltip(compact_btn, "压缩上下文")

        # Font size buttons
        font_up = ctk.CTkButton(
            title_bar, text="A+", width=28, height=24,
            fg_color="#ffeedd", hover_color="#ffe0c0",
            text_color=CHAT_TEXT_PRIMARY, font=("Segoe UI", 11, "bold"),
            corner_radius=6, command=self._increase_font,
        )
        font_up.pack(side="right", padx=(0, 2), pady=10)
        self._bind_tooltip(font_up, "放大字体")

        font_down = ctk.CTkButton(
            title_bar, text="A\u2013", width=28, height=24,
            fg_color="#ffeedd", hover_color="#ffe0c0",
            text_color=CHAT_TEXT_MUTED, font=("Segoe UI", 11, "bold"),
            corner_radius=6, command=self._decrease_font,
        )
        font_down.pack(side="right", padx=(2, 0), pady=10)
        self._bind_tooltip(font_down, "缩小字体")

        # ── Content area: history panel (left) + chat (right) ──
        # padx/pady must keep this rectangular canvas outside the shell's
        # corner-radius zone so the rounded border stays visible.
        self._content_hbox = ctk.CTkFrame(self._shell, fg_color="transparent", corner_radius=0)
        self._content_hbox.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        # History bookmark strip (left, hidden by default)
        self._history_panel = ctk.CTkFrame(
            self._content_hbox, width=140,
            fg_color="#fff7ee", corner_radius=12,
            border_width=1, border_color=CHAT_BORDER,
        )
        # Don't pack yet — starts hidden
        self._history_panel.pack_propagate(False)
        self._build_history_panel()

        # Chat column (right, takes all remaining space)
        self._chat_vbox = ctk.CTkFrame(self._content_hbox, fg_color="transparent", corner_radius=0)
        self._chat_vbox.pack(side="left", fill="both", expand=True, padx=0, pady=0)

        # ── Messages area ──
        msg_shell = ctk.CTkFrame(
            self._chat_vbox,
            fg_color="#fffdfb",
            corner_radius=14,
            border_width=1,
            border_color=CHAT_BORDER,
        )
        msg_shell.pack(fill="both", expand=True, padx=4, pady=(6, 6))

        self.msg_frame = ctk.CTkScrollableFrame(
            msg_shell,
            fg_color="#fffdfb",
            corner_radius=10,
            scrollbar_button_color=CHAT_SCROLL,
            scrollbar_button_hover_color=CHAT_SCROLL_HOVER,
        )
        self.msg_frame.pack(fill="both", expand=True, padx=5, pady=5)
        self._empty_state_label = ctk.CTkLabel(
            self.msg_frame,
            text="🐾 和 OpenCat 聊聊今天的想法吧",
            font=("Segoe UI", self._font_size + 1),
            text_color=CHAT_TEXT_MUTED,
        )
        self._empty_state_label.pack(pady=(26, 6))

        # ── Input area ──
        self._input_frame = input_frame = ctk.CTkFrame(
            self._chat_vbox,
            fg_color=CHAT_SURFACE_ELEVATED, corner_radius=12,
        )
        input_frame.pack(fill="x", padx=4, pady=(0, 4))

        # ── Attachment preview strip (collapsible, appears above input box) ──
        self._attach_container = ctk.CTkFrame(
            input_frame,
            fg_color="#ffeedd",
            corner_radius=10,
            border_width=1,
            border_color=CHAT_BORDER,
        )
        self._attach_container.pack(fill="x", padx=10, pady=(8, 0))
        self._attach_container.pack_propagate(False)
        self._attach_container.configure(height=0)  # hidden initially

        self._attach_thumb_label = ctk.CTkLabel(
            self._attach_container, text="", fg_color="transparent",
        )
        self._attach_thumb_label.pack(side="left", padx=(8, 4), pady=4)

        self._attach_name_label = ctk.CTkLabel(
            self._attach_container, text="",
            font=("Segoe UI", 11), text_color=CHAT_TEXT_MUTED,
            fg_color="transparent", anchor="w",
        )
        self._attach_name_label.pack(side="left", padx=4, pady=4, fill="x", expand=True)

        ctk.CTkButton(
            self._attach_container, text="✕", width=22, height=22,
            fg_color="transparent", hover_color="#ffd4d4",
            text_color=CHAT_TEXT_MUTED, font=("Segoe UI", 11, "bold"),
            corner_radius=4, command=self._clear_attachment,
        ).pack(side="right", padx=(0, 8), pady=4)

        self._input_inner = ctk.CTkFrame(
            input_frame,
            fg_color="#fff8f3",
            border_width=1,
            border_color=CHAT_BORDER,
            corner_radius=18,
        )
        self._input_inner.pack(fill="x", padx=10, pady=10)

        self.input_entry = ctk.CTkTextbox(
            self._input_inner,
            font=("Segoe UI", self._font_size + 1),
            height=36,
            fg_color=CHAT_INPUT_BG,
            border_width=0,
            text_color=CHAT_TEXT_PRIMARY,
            corner_radius=12,
            wrap="word",
            activate_scrollbars=False,
        )
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(8, 6), pady=6)
        # Insert placeholder
        self.input_entry.insert("0.0", "和猫猫聊点什么...")
        self.input_entry._textbox.configure(foreground=CHAT_TEXT_MUTED)
        self.input_entry.bind("<Return>", self._on_input_return)
        self.input_entry.bind("<Shift-Return>", lambda e: None)  # allow newline
        self.input_entry.bind("<FocusIn>", self._on_input_focus_in)
        self.input_entry.bind("<FocusOut>", self._on_input_focus_out)
        self.input_entry.bind("<KeyRelease>", self._auto_resize_input)
        # Intercept Paste event to detect clipboard images before text paste
        self.input_entry._textbox.bind("<<Paste>>", self._on_paste)

        # Paw-shaped send button (Canvas with toe beans + main pad)
        _pw, _ph = 70, 50
        self._paw_cvs = tk.Canvas(
            self._input_inner, width=_pw, height=_ph,
            bg="#fff8f3", highlightthickness=0, cursor="hand2",
        )
        self._paw_cvs.pack(side="right", padx=(0, 8), pady=2)
        _bc, _bo = CHAT_BUTTON_BG, "#d88a40"
        _t1 = self._paw_cvs.create_oval(3, 6, 17, 18, fill=_bc, outline=_bo, width=1.5)
        _t2 = self._paw_cvs.create_oval(19, 1, 33, 13, fill=_bc, outline=_bo, width=1.5)
        _t3 = self._paw_cvs.create_oval(37, 1, 51, 13, fill=_bc, outline=_bo, width=1.5)
        _t4 = self._paw_cvs.create_oval(53, 6, 67, 18, fill=_bc, outline=_bo, width=1.5)
        _mp = self._paw_cvs.create_oval(10, 20, 60, 48, fill=_bc, outline=_bo, width=1.5)
        self._paw_cvs.create_text(35, 34, text="Enter",
                                  font=("Segoe UI", 10, "bold"), fill=CHAT_BUTTON_TEXT)
        self._paw_ids = (_t1, _t2, _t3, _t4, _mp)
        self._paw_cvs.bind("<Enter>", self._paw_enter)
        self._paw_cvs.bind("<Leave>", self._paw_leave)
        self._paw_cvs.bind("<Button-1>", self._send)

        # 📎 Attachment button — packed after paw so it appears to paw's left
        self._attach_icon = ctk.CTkLabel(
            self._input_inner, text="📎",
            font=("Segoe UI Emoji", 15),
            text_color=CHAT_TEXT_MUTED,
            fg_color="transparent",
            cursor="hand2",
            width=28, height=28,
        )
        self._attach_icon.pack(side="right", padx=(2, 6), pady=2)
        self._attach_icon.bind("<Button-1>", lambda e: self._pick_attachment())

        # ── Resize grip (bottom-right) ──
        self._grip = ctk.CTkLabel(
            self._shell, text="\u25e2", font=("Segoe UI", 10),
            text_color=CHAT_SCROLL, width=14, height=14,
            fg_color="transparent", cursor="size_nw_se",
        )
        self._grip.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-10)
        self._grip.bind("<ButtonPress-1>", self._grip_press)
        self._grip.bind("<B1-Motion>", self._grip_drag)

    def _bind_tooltip(self, widget, text: str):
        """Bind a hover tooltip to any widget."""
        tip_win = [None]

        def _show(_e):
            if tip_win[0]:
                return
            tw = tk.Toplevel(self.window)
            tw.wm_overrideredirect(True)
            tw.wm_attributes("-topmost", True)
            x = widget.winfo_rootx() + widget.winfo_width() // 2 - 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw.wm_geometry(f"+{x}+{y}")
            frame = tk.Frame(tw, bg="#5b3f2b", padx=1, pady=1)
            frame.pack()
            inner = tk.Frame(frame, bg="#fff8f0", padx=6, pady=3)
            inner.pack()
            tk.Label(inner, text=text, bg="#fff8f0", fg="#5b3f2b",
                     font=("Segoe UI", 9)).pack()
            tip_win[0] = tw

        def _hide(_e):
            if tip_win[0]:
                tip_win[0].destroy()
                tip_win[0] = None

        widget.bind("<Enter>", _show, add="+")
        widget.bind("<Leave>", _hide, add="+")

    def _current_wraplength(self) -> int:
        # Base wraplength on window width for reliable measurement.
        w = DEFAULT_CHAT_WIDTH
        if self.window:
            ww = self.window.winfo_width()
            if ww >= MIN_CHAT_WIDTH:
                w = ww
        # 80px: shell padding + bubble margin + label padding
        return max(280, w - 80)

    def _refresh_message_wraplengths(self):
        wraplength = self._current_wraplength()
        alive: list = []
        for w in self._message_labels:
            if w.winfo_exists():
                if isinstance(w, ctk.CTkLabel):
                    w.configure(wraplength=wraplength)
                # tk.Text wraps automatically; <Configure> binding handles height
                alive.append(w)
        self._message_labels = alive

    def _on_window_resize(self, event):
        if event.widget is not self.window:
            return
        self._refresh_message_wraplengths()

    def _animate_bubble_in(self, bubble: ctk.CTkFrame, is_user: bool):
        if self.window is None:
            return
        start_pad = (70, 24) if is_user else (24, 70)
        end_pad = (70, 6) if is_user else (6, 70)

        def _step(i: int):
            if not bubble.winfo_exists():
                return
            t = i / BUBBLE_ANIM_STEPS
            left = round(start_pad[0] + (end_pad[0] - start_pad[0]) * t)
            right = round(start_pad[1] + (end_pad[1] - start_pad[1]) * t)
            bubble.pack_configure(padx=(left, right))
            if i < BUBBLE_ANIM_STEPS:
                self.window.after(BUBBLE_ANIM_DELAY_MS, lambda: _step(i + 1))

        _step(0)

    def _on_input_return(self, event):
        if event.state & 0x0001:  # Shift held → allow newline
            return
        self._send()
        return "break"

    def _on_input_focus_in(self, _event):
        if self._input_inner:
            self._input_inner.configure(border_color=CHAT_LAVENDER)
        if self._input_has_placeholder:
            self.input_entry.delete("0.0", "end")
            self.input_entry._textbox.configure(foreground=CHAT_TEXT_PRIMARY)
            self._input_has_placeholder = False

    def _on_input_focus_out(self, _event):
        if self._input_inner:
            self._input_inner.configure(border_color=CHAT_BORDER)
        content = self.input_entry.get("0.0", "end-1c").strip()
        if not content:
            self.input_entry.delete("0.0", "end")
            self.input_entry.insert("0.0", "和猫猫聊点什么...")
            self.input_entry._textbox.configure(foreground=CHAT_TEXT_MUTED)
            self._input_has_placeholder = True
            self.input_entry.configure(height=36)

    def _auto_resize_input(self, event=None):
        if self._input_has_placeholder:
            return
        content = self.input_entry.get("0.0", "end-1c")
        lines = max(1, min(5, content.count("\n") + 1))
        new_h = max(36, lines * (self._font_size + 12) + 10)
        try:
            if int(self.input_entry.cget("height")) != new_h:
                self.input_entry.configure(height=new_h)
        except Exception:
            pass

    def _bind_titlebar_drag(self, widget):
        """Make a widget part of the draggable title bar."""
        widget.bind("<ButtonPress-1>", self._titlebar_press)
        widget.bind("<B1-Motion>", self._titlebar_drag)

    def _titlebar_press(self, event):
        self._drag_offset_x = event.x_root - self.window.winfo_x()
        self._drag_offset_y = event.y_root - self.window.winfo_y()

    def _titlebar_drag(self, event):
        x = event.x_root - self._drag_offset_x
        y = event.y_root - self._drag_offset_y
        self.window.wm_geometry(f"+{x}+{y}")

    def _grip_press(self, event):
        self._resize_start_x = event.x_root
        self._resize_start_y = event.y_root
        self._resize_start_w = self.window.winfo_width()
        self._resize_start_h = self.window.winfo_height()

    def _grip_drag(self, event):
        dx = event.x_root - self._resize_start_x
        dy = event.y_root - self._resize_start_y
        new_w = max(MIN_CHAT_WIDTH, self._resize_start_w + dx)
        new_h = max(MIN_CHAT_HEIGHT, self._resize_start_h + dy)
        x = self.window.winfo_x()
        y = self.window.winfo_y()
        self.window.wm_geometry(f"{new_w}x{new_h}+{x}+{y}")
        # Force immediate redraw to prevent ghost artifacts from -transparentcolor
        self.window.update_idletasks()
        dwm_flush()

    def _increase_font(self):
        if self._font_size < MAX_FONT_SIZE:
            self._font_size += 1
            self._apply_font_size()

    def _decrease_font(self):
        if self._font_size > MIN_FONT_SIZE:
            self._font_size -= 1
            self._apply_font_size()

    def _apply_font_size(self):
        """Update fonts on all existing message widgets and input."""
        wraplength = self._current_wraplength()
        for w in self._message_labels:
            if w.winfo_exists():
                if isinstance(w, ctk.CTkLabel):
                    w.configure(font=("Segoe UI", self._font_size), wraplength=wraplength)
                elif isinstance(w, tk.Text):
                    w.configure(font=("Segoe UI", self._font_size))
                    self.window.after(20, lambda t=w: self._fit_text_height(t))
        if not self._input_has_placeholder:
            self.input_entry.configure(font=("Segoe UI", self._font_size + 1))
        if self._empty_state_label and self._empty_state_label.winfo_exists():
            self._empty_state_label.configure(font=("Segoe UI", self._font_size + 1))

    def _paw_enter(self, _event):
        for oid in self._paw_ids:
            self._paw_cvs.itemconfigure(oid, fill=CHAT_BUTTON_HOVER)

    def _paw_leave(self, _event):
        for oid in self._paw_ids:
            self._paw_cvs.itemconfigure(oid, fill=CHAT_BUTTON_BG)

    # ── Bubble inline action helpers ──

    def _fit_text_height(self, txt: tk.Text):
        """Resize a tk.Text bubble widget to show all content without scrollbar."""
        if getattr(txt, "_fitting", False):
            return  # prevent recursive <Configure> → fit → <Configure> loop
        try:
            txt._fitting = True
            n = txt.count("1.0", "end", "displaylines")
            if n:
                new_h = max(1, n[0])
                if txt.cget("height") != new_h:
                    txt.configure(height=new_h)
        except Exception:
            pass
        finally:
            txt._fitting = False

    def _make_bubble_btn(self, parent, icon: str, tooltip_text: str, cmd):
        """Create an icon-only bubble action button with a hover tooltip."""
        btn = ctk.CTkLabel(
            parent, text=icon,
            font=("Segoe UI Emoji", 11),
            text_color=CHAT_TEXT_MUTED,
            fg_color="transparent",
            cursor="hand2",
            width=22, height=20,
            corner_radius=4,
            padx=0,
        )
        btn.pack(side="left", padx=1)

        _tip_win: list = [None]

        def _show_tip(e):
            if _tip_win[0]:
                return
            tip = tk.Toplevel(self.root)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            # Warm-themed border + inner bg
            outer = tk.Frame(tip, bg=CHAT_BORDER)
            outer.pack()
            inner = tk.Frame(outer, bg="#fff8f2")
            inner.pack(padx=1, pady=1)
            tk.Label(inner, text=tooltip_text,
                     font=("Segoe UI", 10), bg="#fff8f2", fg=CHAT_TEXT_PRIMARY,
                     padx=6, pady=3).pack()
            tip.update_idletasks()
            tw = tip.winfo_reqwidth()
            th = tip.winfo_reqheight()
            tx = e.x_root - tw // 2
            ty = e.y_root - th - 6
            tip.geometry(f"+{max(0, tx)}+{max(0, ty)}")
            _tip_win[0] = tip
            btn.configure(text_color=CHAT_TEXT_PRIMARY)

        def _hide_tip(e=None):
            if _tip_win[0]:
                try:
                    _tip_win[0].destroy()
                except Exception:
                    pass
                _tip_win[0] = None
            btn.configure(text_color=CHAT_TEXT_MUTED)

        btn.bind("<Enter>", _show_tip)
        btn.bind("<Leave>", _hide_tip)
        btn.bind("<Button-1>", lambda e: (_hide_tip(), cmd()))
        return btn

    def _copy_text_from_widget(self, txt: tk.Text):
        """Copy the full text of a tk.Text bubble to the clipboard."""
        try:
            content = txt.get("1.0", "end-1c")
        except Exception:
            content = ""
        self.root.clipboard_clear()
        self.root.clipboard_append(content)

    def _reedit_from_widget(self, txt: tk.Text):
        """Load text from a user bubble back into the input box for editing."""
        try:
            content = txt.get("1.0", "end-1c")
        except Exception:
            content = ""
        self._reedit_message(content)

    def _copy_text(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _reedit_message(self, text: str):
        self.input_entry.delete("0.0", "end")
        self.input_entry.insert("0.0", text)
        self.input_entry._textbox.configure(foreground=CHAT_TEXT_PRIMARY)
        self._input_has_placeholder = False
        self._auto_resize_input()
        self.input_entry.focus_set()

    def _send(self, event=None):
        if self._viewing_history:
            return  # read-only when viewing past sessions
        text = "" if self._input_has_placeholder else self.input_entry.get("0.0", "end-1c").strip()
        attachment = self._pending_attachment
        if not text and not attachment:
            return

        self.input_entry.delete("0.0", "end")
        self.input_entry.configure(height=36)
        self._clear_attachment()

        # Local-only commands (text-only messages, no attachment)
        if text and not attachment:
            cmd = text.split()[0].lower() if text.startswith("/") else ""
            if cmd == "/clear":
                self._clear_messages()
                return
            if cmd == "/help":
                self._show_help()
                return
            # /new clears local history then falls through to chat.send
            # Gateway handles /new, /reset, /status, /compact, /think, /stop natively
            if cmd in ("/new", "/reset"):
                self._clear_messages()
                new_sid = self._history.create_session()
                self._active_session_id = new_sid
                if self._history_visible:
                    self._refresh_history_list()

        # Ensure a session exists for persistence
        sid = self._history.ensure_current_session()
        self._active_session_id = sid

        self._add_bubble("user", text, image=attachment)

        # Persist user message
        img_for_save = attachment["image"] if attachment else None
        self._history.append_message(sid, "user", text, image=img_for_save)

        # Build protocol content
        if attachment:
            content_blocks: list = []
            img = attachment["image"]
            buf = io.BytesIO()
            # Normalise mode so PIL can save as PNG
            if img.mode not in ("RGB", "RGBA", "L"):
                img = img.convert("RGBA")
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            content_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": b64},
            })
            if text:
                content_blocks.append({"type": "text", "text": text})
            content = content_blocks
        else:
            content = text

        self.controller.send_message(content)

    def _show_help(self):
        help_text = (
            "  Gateway 原生指令（发给服务器）:\n"
            "  /status   — 查看 session 状态和 token 用量\n"
            "  /new      — 开始新对话（服务端 reset session）\n"
            "  /reset    — 同 /new\n"
            "  /compact  — 压缩上下文\n"
            "  /think <level>  — 设置思考深度\n"
            "  /stop     — 中止当前回复\n"
            "\n"
            "  本地指令（不发给服务器）:\n"
            "  /clear    — 清空本地聊天记录（不重置 session）\n"
            "  /help     — 显示此帮助"
        )
        self._add_system_bubble(help_text)

    def _new_chat(self):
        """Start a new chat — same as /new but via button."""
        self._clear_messages()
        new_sid = self._history.create_session()
        self._active_session_id = new_sid
        self._set_viewing_history(False)
        if self._history_visible:
            self._refresh_history_list()
        # Show temp status (auto-removed when server responds via _on_begin/_on_final)
        self._think_show_custom("\u2728 正在创建新对话...")
        self.controller.send_message("/new", quiet=True)

    def _compact_context(self):
        """Compact the server context — same as /compact."""
        self._think_show_custom("\U0001f4e6 正在压缩上下文...")
        self.controller.send_message("/compact", quiet=True)

    def _add_system_bubble(self, text: str):
        """Add a local system info bubble (not sent to server)."""
        if self._empty_state_label and self._empty_state_label.winfo_exists():
            self._empty_state_label.pack_forget()
        row = ctk.CTkFrame(self.msg_frame, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=6, pady=4)
        bubble = ctk.CTkFrame(
            row, corner_radius=12,
            fg_color="#f0e6ff", border_width=1, border_color="#d4c4f0",
        )
        bubble.pack(anchor="center", padx=(40, 40))
        label = ctk.CTkLabel(
            bubble, text=text, wraplength=self._current_wraplength(),
            font=("Segoe UI", self._font_size),
            text_color="#6b5a8e", justify="left", anchor="w",
        )
        label.pack(padx=12, pady=8)
        self._message_labels.append(label)
        self._scroll_bottom()

    def _clear_messages(self):
        """Remove all message bubbles."""
        for widget in self.msg_frame.winfo_children():
            widget.destroy()
        self._message_labels.clear()
        self._streaming_bubble = None
        self._streaming_text = ""
        self._empty_state_label = ctk.CTkLabel(
            self.msg_frame,
            text="\U0001f43e 和 OpenCat 聊聊今天的想法吧",
            font=("Segoe UI", self._font_size + 1),
            text_color=CHAT_TEXT_MUTED,
        )
        self._empty_state_label.pack(pady=(26, 6))

    def _add_bubble(self, role: str, text: str, is_error=False, image=None,
                    timestamp: float | None = None, animate: bool = True):
        is_user = role == "user"
        bg = CHAT_USER_BUBBLE if is_user else (CHAT_ERROR_BUBBLE if is_error else CHAT_ASSISTANT_BUBBLE)
        text_color = "#8b3346" if is_error else CHAT_TEXT_PRIMARY
        ts = timestamp or _time.time()

        if self._empty_state_label and self._empty_state_label.winfo_exists():
            self._empty_state_label.pack_forget()
        row = ctk.CTkFrame(self.msg_frame, fg_color="transparent", corner_radius=0)
        row.pack(fill="x", padx=6, pady=2)

        bubble = ctk.CTkFrame(
            row, corner_radius=12, fg_color=bg,
            border_width=1, border_color=CHAT_BORDER,
        )
        if is_user:
            bubble.pack(side="right", anchor="e", padx=(60, 10))
        else:
            bubble.pack(fill="x", padx=(10, 10))

        # ── Header: role label + action icon buttons ──
        role_text = "😺 You" if is_user else "🐾 OpenCat"
        role_color = "#907de8" if is_user else ("#dd6b84" if is_error else CHAT_GREEN_STRONG)
        header_row = ctk.CTkFrame(bubble, fg_color="transparent", corner_radius=0)
        header_row.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(header_row, text=role_text,
                     font=("Segoe UI", self._font_size - 1, "bold"),
                     text_color=role_color, anchor="w").pack(side="left")

        # Timestamp
        time_str = _dt.datetime.fromtimestamp(ts).strftime("%H:%M")
        ctk.CTkLabel(header_row, text=time_str,
                     font=("Segoe UI", self._font_size - 3),
                     text_color=CHAT_TEXT_MUTED, anchor="w",
                     fg_color="transparent").pack(side="left", padx=(6, 0))

        # Right-side action buttons — hidden until hover
        btn_frame = ctk.CTkFrame(header_row, fg_color="transparent", corner_radius=0)
        # btn_frame is NOT packed initially — shown on hover
        _txt_ref: list = [None]  # will be set to txt after it's created

        self._make_bubble_btn(
            btn_frame, "📋", "复制文字",
            lambda: self._copy_text_from_widget(_txt_ref[0]) if _txt_ref[0] else None,
        )
        if is_user:
            self._make_bubble_btn(
                btn_frame, "✏️", "重新编辑",
                lambda: self._reedit_from_widget(_txt_ref[0]) if _txt_ref[0] else None,
            )

        # Show/hide action buttons on bubble hover.
        # Use after() + winfo_containing() so that moving between child widgets
        # (e.g. text → copy button) does NOT hide the buttons prematurely.
        def _show_btns(_e, bf=btn_frame):
            try:
                if bf.winfo_exists():
                    bf.pack(side="right")
            except Exception:
                pass

        def _hide_btns(_e, bf=btn_frame, bbl=bubble):
            def _check():
                try:
                    if not bf.winfo_exists():
                        return
                    x, y = bbl.winfo_pointerxy()
                    w = bbl.winfo_containing(x, y)
                    if w is not None:
                        w_path = str(w)
                        bbl_path = str(bbl)
                        if w_path == bbl_path or w_path.startswith(bbl_path + "."):
                            return  # cursor still inside bubble hierarchy
                    bf.pack_forget()
                except Exception:
                    pass
            bbl.after(80, _check)

        bubble.bind("<Enter>", _show_btns)
        bubble.bind("<Leave>", _hide_btns)

        # ── Attachment image thumbnail (user bubble only) ──
        if image and is_user:
            try:
                thumb = image["image"].copy()
                thumb.thumbnail((200, 150), Image.LANCZOS)
                ctk_img = ctk.CTkImage(
                    light_image=thumb, dark_image=thumb,
                    size=(thumb.width, thumb.height),
                )
                img_lbl = ctk.CTkLabel(
                    bubble, image=ctk_img, text="", fg_color="transparent",
                )
                img_lbl._ctk_image = ctk_img  # prevent GC
                img_lbl.pack(padx=10, pady=(4, 2), anchor="w")
            except Exception as _e:
                log.warning("Failed to display attachment thumbnail: %s", _e)

        # ── Selectable text content (tk.Text, readonly) ──
        # Skip text widget entirely for image-only user bubbles
        if not text and image and is_user:
            _txt_ref[0] = None
            if animate:
                self._animate_bubble_in(bubble, is_user=is_user)
                self._scroll_bottom()
            return None

        txt = tk.Text(
            bubble,
            font=("Segoe UI", self._font_size),
            bg=bg,
            fg=text_color,
            relief="flat",
            bd=0,
            highlightthickness=0,
            wrap="word",
            cursor="xterm",
            selectbackground="#d8e8ff",
            selectforeground=text_color,
            state="normal",
            height=1,
            width=1,
            spacing1=2,
            spacing3=2,
            padx=10,
            pady=2,
            exportselection=True,
        )
        txt.insert("1.0", text)
        _txt_ref[0] = txt  # wire up action button callbacks

        # Keep widget in "normal" state so text is selectable, but block
        # all keyboard modifications (allow Ctrl+C copy, Ctrl+A select-all).
        def _readonly_key(e):
            ctrl = e.state & 0x4
            if ctrl and e.keysym.lower() in ("c", "a"):
                return  # allow copy / select-all
            return "break"
        txt.bind("<Key>", _readonly_key)
        txt.bind("<<Paste>>", lambda e: "break")
        txt.bind("<<Cut>>", lambda e: "break")

        if is_user:
            # User bubbles: measure actual pixel width (handles CJK/mixed text correctly)
            try:
                f = tkfont.Font(family="Segoe UI", size=self._font_size)
                char_0_w = max(1, f.measure("0"))
                if image:
                    # Width follows the thumbnail (capped at 200px + padx)
                    thumb_w = min(200, image["image"].width)
                    bubble_px = thumb_w + 22
                else:
                    lines_px = [f.measure(l) for l in text.split("\n")]
                    natural_px = max(lines_px, default=0) + 22  # +22 for padx
                    max_px = min(self._current_wraplength(), 420)
                    bubble_px = min(natural_px, max_px)
                char_width = max(5, round(bubble_px / char_0_w))
            except Exception:
                char_width = 30
            txt.configure(width=char_width)
            txt.pack(anchor="w", padx=0, pady=(0, 8))
        else:
            txt.pack(fill="x", pady=(0, 8))

        self._message_labels.append(txt)

        # On subsequent resizes only refit; don't force-scroll (user may be reading history)
        txt.bind("<Configure>", lambda e, t=txt: self._fit_text_height(t))

        if animate:
            # Fit height once rendered, then re-scroll so the full bubble is visible.
            def _init_bubble(t=txt):
                self._fit_text_height(t)
                self._scroll_bottom()
            self.window.after(20, _init_bubble)
            self._animate_bubble_in(bubble, is_user=is_user)
            self._scroll_bottom()
        else:
            # Immediate fit, no animation (used during history restore)
            self._fit_text_height(txt)

        return txt

    def _scroll_bottom(self):
        """Scroll messages to bottom. Debounced — multiple calls within a
        short window collapse into a single delayed scroll so tkinter has
        time to finish layout before we reposition the viewport."""
        if getattr(self, "_scroll_pending", False):
            return
        self._scroll_pending = True
        try:
            self.window.after(30, self._do_scroll)
        except Exception:
            self._scroll_pending = False

    def _do_scroll(self):
        self._scroll_pending = False
        try:
            canvas = self.msg_frame._parent_canvas
            # Sync scrollregion with actual content size before scrolling,
            # otherwise yview_moveto(1.0) can overshoot during rapid streaming.
            bbox = canvas.bbox("all")
            if bbox:
                canvas.configure(scrollregion=bbox)
            canvas.yview_moveto(1.0)
        except Exception:
            pass

    def _update_status(self, text, color):
        if self.window is None:
            return
        if text == "Connected":
            status_color = CHAT_GREEN_STRONG
        elif text == "Connecting...":
            status_color = "#e7a86a"
        elif text == "Disconnected":
            status_color = CHAT_TEXT_MUTED
        elif text == "Error":
            status_color = "#d66f7d"
        else:
            status_color = color
        self.status_label.configure(text=text, text_color=status_color)
        self.status_dot.configure(text_color=status_color)
        # Dim input when not connected
        connected = text == "Connected"
        self._update_input_state(connected)

    def _update_input_state(self, connected: bool):
        """Enable/disable input area based on connection status."""
        if self._viewing_history:
            return  # input already hidden
        if not self.input_entry:
            return
        if connected:
            self.input_entry.configure(state="normal", fg_color=CHAT_INPUT_BG)
            if self._input_has_placeholder:
                self.input_entry._textbox.configure(foreground=CHAT_TEXT_MUTED)
            else:
                self.input_entry._textbox.configure(foreground=CHAT_TEXT_PRIMARY)
        else:
            self.input_entry.configure(state="disabled", fg_color="#f0e8e0")
            self.input_entry._textbox.configure(foreground=CHAT_TEXT_MUTED)

    # ── Callbacks ──

    def _on_status(self, text, color):
        self._update_status(text, color)

    def _on_begin(self):
        self._destroy_think_bubble()   # remove thinking bubble before streaming starts
        self._streaming_text = ""
        self._streaming_bubble = self._add_bubble("assistant", "")

    def _on_delta(self, text):
        self._streaming_text += text
        if self._streaming_bubble:
            txt = self._streaming_bubble
            # Append only — avoids delete+reinsert flicker
            txt.insert("end", text)
            # Throttle expensive height-refit during streaming; _scroll_bottom
            # is already debounced so just schedule a combined refit+scroll.
            if not getattr(self, "_delta_fit_pending", False):
                self._delta_fit_pending = True
                def _deferred_fit(t=txt):
                    self._delta_fit_pending = False
                    self._fit_text_height(t)
                    self._scroll_bottom()
                self.window.after(50, _deferred_fit)

    def _on_final(self, text):
        self._destroy_think_bubble()   # no-op if already gone
        if self._streaming_bubble:
            if text:
                txt = self._streaming_bubble
                txt.delete("1.0", "end")
                txt.insert("1.0", text)
                self.window.after(20, lambda t=txt: self._fit_text_height(t))
        elif text:
            # Server responded without prior delta (e.g. /status, /new system replies)
            self._add_bubble("assistant", text)
        self._streaming_bubble = None
        self._streaming_text = ""
        self._scroll_bottom()
        # Persist assistant reply
        if text:
            sid = self._history.current_session_id
            if sid:
                self._history.append_message(sid, "assistant", text)
                if self._history_visible:
                    self._refresh_history_list()

    def _on_error(self, msg):
        self._destroy_think_bubble()   # no-op if already gone
        self._streaming_bubble = None
        self._streaming_text = ""
        self._add_bubble("assistant", msg, is_error=True)
        # Persist error
        sid = self._history.current_session_id
        if sid:
            self._history.append_message(sid, "assistant", msg)

    # ── Thinking bubble (inline in conversation) ──

    def _on_cat_state(self, state: CatState):
        """Called when the cat's state changes (may come from a non-main thread)."""
        if not self.window:
            return
        if state == CatState.THINKING:
            self._think_show()
        elif state in (CatState.DONE, CatState.IDLE, CatState.ERROR):
            # _on_begin / _on_final / _on_error already clean up; this is a safety net
            if self._think_visible:
                self._destroy_think_bubble()

    def _think_show_custom(self, text: str):
        """Show a temporary status bubble that auto-removes when server responds."""
        if not self.window or not hasattr(self, "msg_frame"):
            return
        self._destroy_think_bubble()
        if self._empty_state_label and self._empty_state_label.winfo_exists():
            self._empty_state_label.pack_forget()
        self._think_row = ctk.CTkFrame(self.msg_frame, fg_color="transparent", corner_radius=0)
        self._think_row.pack(fill="x", padx=6, pady=4)
        bubble = ctk.CTkFrame(
            self._think_row, corner_radius=12,
            fg_color="#f0e6ff", border_width=1, border_color="#d4c4f0",
        )
        bubble.pack(anchor="center", padx=(40, 40))
        ctk.CTkLabel(
            bubble, text=text,
            font=("Segoe UI", self._font_size),
            text_color="#6b5a8e", fg_color="transparent",
        ).pack(padx=12, pady=8)
        self._think_visible = True
        self._scroll_bottom()

    def _think_show(self):
        """Insert an animated kaomoji bubble at the bottom of the chat as a thinking indicator."""
        if not self.window or not hasattr(self, "msg_frame"):
            return
        self._destroy_think_bubble()   # clear any previous one

        if self._empty_state_label and self._empty_state_label.winfo_exists():
            self._empty_state_label.pack_forget()

        self._think_row = ctk.CTkFrame(self.msg_frame, fg_color="transparent", corner_radius=0)
        self._think_row.pack(fill="x", padx=6, pady=4)

        bubble = ctk.CTkFrame(
            self._think_row, corner_radius=12,
            fg_color=CHAT_ASSISTANT_BUBBLE,
            border_width=1, border_color=CHAT_BORDER,
        )
        bubble.pack(fill="x", padx=(10, 10))

        header = ctk.CTkFrame(bubble, fg_color="transparent", corner_radius=0)
        header.pack(fill="x", padx=10, pady=(6, 0))
        ctk.CTkLabel(
            header, text="🐾 OpenCat",
            font=("Segoe UI", self._font_size - 1, "bold"),
            text_color=CHAT_GREEN_STRONG, anchor="w",
        ).pack(side="left")

        self._think_kaomoji_label = ctk.CTkLabel(
            bubble, text=_KAOMOJI_THINKING[0],
            font=("Segoe UI", self._font_size),
            text_color=CHAT_TEXT_MUTED,
            fg_color="transparent", anchor="w",
            wraplength=self._current_wraplength(),
        )
        self._think_kaomoji_label.pack(fill="x", padx=12, pady=(4, 10))

        self._think_visible = True
        self._think_kaomoji_idx = 0
        self._think_after_id = self.window.after(1600, self._think_animate)
        self._scroll_bottom()

    def _think_animate(self):
        self._think_after_id = None
        if not self._think_visible or not self._think_kaomoji_label or not self.window:
            return
        try:
            if not self._think_kaomoji_label.winfo_exists():
                return
        except Exception:
            return
        self._think_kaomoji_idx = (self._think_kaomoji_idx + 1) % len(_KAOMOJI_THINKING)
        self._think_kaomoji_label.configure(text=_KAOMOJI_THINKING[self._think_kaomoji_idx])
        self._think_after_id = self.window.after(1600, self._think_animate)

    def _destroy_think_bubble(self):
        """Stop the kaomoji animation and remove the thinking bubble row."""
        self._think_cancel_anim()
        self._think_visible = False
        if self._think_row:
            try:
                self._think_row.destroy()
            except Exception:
                pass
            self._think_row = None
            self._think_kaomoji_label = None

    def _think_cancel_anim(self):
        if self._think_after_id and self.window:
            try:
                self.window.after_cancel(self._think_after_id)
            except Exception:
                pass
            self._think_after_id = None

    # ── History panel ──

    def _build_history_panel(self):
        """Populate the history bookmark panel (already created in _create)."""
        panel = self._history_panel

        # Header row
        hdr = ctk.CTkFrame(panel, fg_color="transparent", corner_radius=0, height=32)
        hdr.pack(fill="x", padx=6, pady=(8, 2))
        hdr.pack_propagate(False)
        ctk.CTkLabel(
            hdr, text="\U0001f516 历史",
            font=("Segoe UI", 11, "bold"), text_color=CHAT_TEXT_PRIMARY,
        ).pack(side="left", padx=2)
        ctk.CTkButton(
            hdr, text="+", width=24, height=22,
            fg_color=CHAT_BUTTON_BG, hover_color=CHAT_BUTTON_HOVER,
            text_color="white", font=("Segoe UI", 13, "bold"),
            corner_radius=6, command=self._new_session_from_panel,
        ).pack(side="right", padx=2)

        # Scrollable bookmark list
        self._history_list = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color=CHAT_SCROLL,
            scrollbar_button_hover_color=CHAT_SCROLL_HOVER,
        )
        self._history_list.pack(fill="both", expand=True, padx=4, pady=(0, 6))

    _BOOKMARK_STRIP_W = 148  # width of the bookmark panel

    def _toggle_history_panel(self):
        """Show or hide the bookmark panel, resizing the window."""
        if not self.window or not self._history_panel:
            return
        bw = self._BOOKMARK_STRIP_W
        if self._history_visible:
            self._history_panel.pack_forget()
            self._history_visible = False
            # Shrink window
            w = self.window.winfo_width() - bw
            h = self.window.winfo_height()
            self.window.wm_geometry(f"{max(MIN_CHAT_WIDTH, w)}x{h}")
        else:
            self._refresh_history_list()
            self._history_panel.pack(side="left", fill="y", padx=(6, 0), pady=(0, 10))
            # Make sure chat_vbox stays on the right
            self._chat_vbox.pack_configure(side="left")
            self._history_visible = True
            # Expand window
            w = self.window.winfo_width() + bw
            h = self.window.winfo_height()
            self.window.wm_geometry(f"{w}x{h}")

    # Bookmark tab colors — cycle through for visual variety
    _BOOKMARK_COLORS = [
        "#f4a261", "#e76f51", "#88c98f", "#c9bcff",
        "#f6bfd8", "#6bbcd4", "#d79ac4", "#e8c468",
    ]

    def _refresh_history_list(self):
        """Rebuild the bookmark tabs in the history panel."""
        if not self._history_list:
            return
        for w in self._history_list.winfo_children():
            w.destroy()
        self._history_items.clear()

        sessions = self._history.list_sessions()
        viewing = self._history.current_session_id
        colors = self._BOOKMARK_COLORS
        for i, sess in enumerate(sessions):
            sid = sess["id"]
            is_active = sid == self._active_session_id
            is_viewing = sid == viewing
            color = colors[i % len(colors)]
            title = sess.get("title", "新对话")
            if len(title) > 10:
                title = title[:9] + "…"

            # Bookmark tab — looks like a paper tab
            tab_bg = "#fff8f0" if is_viewing else "#fff2e4"
            tab = ctk.CTkFrame(
                self._history_list, fg_color=tab_bg,
                corner_radius=10, height=44,
                border_width=2 if is_viewing else 0,
                border_color=color if is_viewing else tab_bg,
                cursor="hand2",
            )
            tab.pack(fill="x", padx=1, pady=2)
            tab.pack_propagate(False)

            # Color strip on the left (like a bookmark ribbon)
            ribbon = ctk.CTkFrame(tab, fg_color=color, width=4, corner_radius=2)
            ribbon.pack(side="left", fill="y", padx=(4, 0), pady=6)

            # Delete button packed BEFORE txt_frame so it reserves space
            # on the right and doesn't get pushed off-screen by expand.
            del_btn = None
            if not is_active:
                del_btn = ctk.CTkLabel(
                    tab, text="\u2715", width=16, height=16,
                    font=("Segoe UI", 9), text_color=CHAT_TEXT_MUTED,
                    fg_color="transparent", cursor="hand2",
                )
                del_btn.pack(side="right", padx=(0, 4), pady=2)

                def _on_delete(_e, s=sid):
                    self._delete_session(s)
                del_btn.bind("<Button-1>", _on_delete)

            # Text area (packed after del_btn so it fills remaining space)
            txt_frame = ctk.CTkFrame(tab, fg_color="transparent", corner_radius=0)
            txt_frame.pack(side="left", fill="both", expand=True, padx=(6, 4))

            # Title
            title_lbl = ctk.CTkLabel(
                txt_frame, text=title,
                font=("Segoe UI", 10, "bold" if is_viewing else "normal"),
                text_color=CHAT_TEXT_PRIMARY, anchor="w",
                fg_color="transparent",
            )
            title_lbl.pack(fill="x", anchor="w", pady=(4, 0))

            # Subtitle
            count = sess.get("msg_count", 0)
            updated = sess.get("updated", 0)
            age = _time.time() - updated
            if age < 60:
                time_str = "刚刚"
            elif age < 3600:
                time_str = f"{int(age // 60)}分前"
            elif age < 86400:
                time_str = f"{int(age // 3600)}时前"
            else:
                time_str = f"{int(age // 86400)}天前"
            sub = f"{count}条 · {time_str}"
            # Active session indicator
            if is_active:
                sub = "\u25cf " + sub  # green dot prefix
            sub_lbl = ctk.CTkLabel(
                txt_frame, text=sub,
                font=("Segoe UI", 9),
                text_color=CHAT_GREEN_STRONG if is_active else CHAT_TEXT_MUTED,
                anchor="w", fg_color="transparent",
            )
            sub_lbl.pack(fill="x", anchor="w", pady=(0, 2))

            # Click handler — bind to all children
            def _on_click(_e, s=sid):
                self._switch_to_session(s)
            click_targets = [tab, ribbon, txt_frame, title_lbl, sub_lbl]
            if del_btn is None:
                # Only bind click-to-switch on non-delete widgets
                pass
            for w in click_targets:
                w.bind("<Button-1>", _on_click)

            self._history_items.append(tab)

    def _switch_to_session(self, session_id: str):
        """Load a different session's messages into the chat area."""
        if session_id == self._history.current_session_id:
            return
        self._history.current_session_id = session_id
        self._clear_messages()
        messages = self._history.load_session(session_id)
        self._restore_messages(messages)
        # Check if viewing a historical (non-active) session
        is_history = session_id != self._active_session_id
        self._set_viewing_history(is_history)
        self._refresh_history_list()

    def _new_session_from_panel(self):
        """Create a new session from the history panel '+' button."""
        sid = self._history.create_session()
        self._active_session_id = sid
        self._clear_messages()
        self._set_viewing_history(False)
        self._refresh_history_list()

    def _delete_session(self, session_id: str):
        """Delete a session from history."""
        self._history.delete_session(session_id)
        # If we were viewing the deleted session, go back to active
        if self._history.current_session_id is None or \
           self._history.current_session_id == session_id:
            if self._active_session_id:
                self._history.current_session_id = self._active_session_id
                self._clear_messages()
                messages = self._history.load_session(self._active_session_id)
                self._restore_messages(messages)
                self._set_viewing_history(False)
        self._refresh_history_list()

    def _back_to_active_session(self):
        """Return to the active (current) session from history view."""
        if self._active_session_id:
            self._history.current_session_id = self._active_session_id
            self._clear_messages()
            messages = self._history.load_session(self._active_session_id)
            self._restore_messages(messages)
        self._set_viewing_history(False)
        self._refresh_history_list()

    def _set_viewing_history(self, viewing: bool):
        """Enable/disable read-only mode for historical sessions."""
        self._viewing_history = viewing
        if viewing:
            # Hide input, show banner
            if self._input_frame:
                self._input_frame.pack_forget()
            self._show_history_banner()
        else:
            # Hide banner, show input
            self._hide_history_banner()
            if self._input_frame:
                self._input_frame.pack(fill="x", padx=10, pady=(0, 10))

    def _show_history_banner(self):
        """Show a 'back to current chat' banner at the bottom."""
        self._hide_history_banner()
        self._history_banner = ctk.CTkFrame(
            self._chat_vbox, fg_color="#ffeedd",
            corner_radius=12, border_width=1, border_color=CHAT_BORDER,
        )
        self._history_banner.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkLabel(
            self._history_banner, text="\U0001f4d6 正在查看历史对话",
            font=("Segoe UI", 11), text_color=CHAT_TEXT_MUTED,
        ).pack(side="left", padx=(12, 6), pady=8)
        ctk.CTkButton(
            self._history_banner, text="回到当前对话 \u2192",
            width=120, height=28,
            fg_color=CHAT_BUTTON_BG, hover_color=CHAT_BUTTON_HOVER,
            text_color="white", font=("Segoe UI", 11, "bold"),
            corner_radius=8, command=self._back_to_active_session,
        ).pack(side="right", padx=8, pady=6)

    def _hide_history_banner(self):
        if self._history_banner:
            try:
                self._history_banner.destroy()
            except Exception:
                pass
            self._history_banner = None

    def _startup_restore(self):
        """On app startup, restore the most recent session's messages."""
        sessions = self._history.list_sessions()
        if not sessions:
            return
        sid = sessions[0]["id"]
        self._history.current_session_id = sid
        self._active_session_id = sid
        messages = self._history.load_session(sid)
        if messages:
            self._restore_messages(messages)

    def _restore_messages(self, messages: list[dict]):
        """Render saved messages into the chat instantly (no animation)."""
        for msg in messages:
            role = msg.get("role", "assistant")
            text = msg.get("text", "")
            img_rel = msg.get("image")
            image = None
            if img_rel:
                try:
                    img_path = self._history.resolve_image_path(img_rel)
                    if img_path.is_file():
                        pil_img = Image.open(img_path)
                        pil_img.load()
                        image = {"image": pil_img, "filename": img_path.name}
                except Exception as e:
                    log.warning("Failed to load history image %s: %s", img_rel, e)
            ts = msg.get("ts")
            if role == "system":
                self._add_system_bubble(text)
            else:
                is_error = msg.get("is_error", False)
                self._add_bubble(role, text, is_error=is_error, image=image,
                                 timestamp=ts, animate=False)
        # Deferred batch: let tkinter lay out all widgets, then fit heights + scroll
        if messages:
            def _batch_fit():
                self.window.update_idletasks()
                for w in self._message_labels:
                    if isinstance(w, tk.Text) and w.winfo_exists():
                        self._fit_text_height(w)
                self._scroll_bottom()
            self.window.after(50, _batch_fit)

    # ── Attachment handling ──

    def _pick_attachment(self):
        """Open a file dialog to pick an image attachment."""
        import tkinter.filedialog as fd
        path = fd.askopenfilename(
            title="选择图片",
            parent=self.window,
            filetypes=[
                ("图片文件", "*.png *.jpg *.jpeg *.gif *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        try:
            img = Image.open(path)
            img.load()
            self._set_attachment(img, Path(path).name)
        except Exception as e:
            log.warning("Failed to open image attachment: %s", e)

    def _on_paste(self, event):
        """Intercept <<Paste>> to detect clipboard images before text paste runs."""
        try:
            from PIL import ImageGrab
            clip = ImageGrab.grabclipboard()
            if clip is not None:
                if hasattr(clip, "size"):
                    # PIL Image object (screenshot, copy from browser, etc.)
                    self._set_attachment(clip, "截图.png")
                    return "break"  # suppress default text-paste
                elif isinstance(clip, list):
                    # List of file paths — try the first image file
                    for p in clip:
                        path = Path(str(p))
                        if path.suffix.lower() in (".png", ".jpg", ".jpeg",
                                                   ".gif", ".webp", ".bmp"):
                            try:
                                img = Image.open(path)
                                img.load()
                                self._set_attachment(img, path.name)
                                return "break"
                            except Exception:
                                pass
        except Exception:
            pass
        return None  # proceed with default text paste

    def _set_attachment(self, img: Image.Image, filename: str):
        """Store a pending attachment and show the preview strip."""
        self._pending_attachment = {"image": img.copy(), "filename": filename}
        try:
            thumb = img.copy()
            thumb.thumbnail((48, 48), Image.LANCZOS)
            ctk_img = ctk.CTkImage(
                light_image=thumb, dark_image=thumb,
                size=(thumb.width, thumb.height),
            )
            if self._attach_thumb_label:
                self._attach_thumb_label.configure(image=ctk_img)
                self._attach_thumb_label._ctk_image = ctk_img  # prevent GC
        except Exception:
            pass
        if self._attach_name_label:
            name = filename if len(filename) <= 26 else filename[:23] + "..."
            self._attach_name_label.configure(text=name)
        if self._attach_container:
            self._attach_container.configure(height=52)

    def _clear_attachment(self):
        """Remove the pending attachment and collapse the preview strip."""
        self._pending_attachment = None
        if self._attach_container:
            self._attach_container.configure(height=0)
        if self._attach_thumb_label:
            self._attach_thumb_label.configure(image=None)
            if hasattr(self._attach_thumb_label, "_ctk_image"):
                self._attach_thumb_label._ctk_image = None


# ── Main entry ──

def run_app(debug: bool = False):
    set_dpi_awareness()

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = tk.Tk()
    root.withdraw()

    controller = Controller(root)

    # Load GIFs
    state_gifs, idle_pool = _load_all_gifs()
    cat_width, cat_height = _get_max_frame_size(state_gifs, idle_pool)
    if not state_gifs and not idle_pool:
        log.warning("No GIF assets found in %s, cat will be invisible", ASSETS_DIR)

    chat = ChatWindow(root, controller)
    chat.cat_height = cat_height
    chat.pre_create()  # create CTkToplevel NOW (hidden), before cat window

    cat = FloatingCat(
        root,
        controller,
        state_gifs,
        idle_pool,
        on_click=chat.toggle,
        cat_width=cat_width,
        cat_height=cat_height,
    )
    chat.cat_win = cat.win  # wire up cat window reference for positioning
    cat._on_hide_chat = chat.hide  # hide chat when cat hides

    # root stays hidden — cat.win is the visible floating window
    root.after(500, controller.start)

    log.info("OpenCat started — click the cat to chat, right-click to quit")
    root.mainloop()
    controller.shutdown()
