"""Local session history persistence for OpenCat.

Stores chat messages as JSON files under ~/.opencat/history/.
Thread-safe for use from WebSocket callback threads.
"""

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

_MAX_THUMB = 400  # max dimension for saved image thumbnails


class SessionManager:
    """Manages local chat history: sessions index + per-session message files."""

    def __init__(self, history_dir: str | None = None):
        if history_dir is None:
            history_dir = os.path.join(os.path.expanduser("~"), ".opencat", "history")
        self._dir = Path(history_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / "images").mkdir(exist_ok=True)
        self._index_path = self._dir / "sessions.json"
        self._lock = threading.Lock()
        self._sessions: list[dict] = self._load_index()
        self._current_id: str | None = None

    # ── Properties ──────────────────────────────────────────────

    @property
    def current_session_id(self) -> str | None:
        return self._current_id

    @current_session_id.setter
    def current_session_id(self, sid: str | None):
        self._current_id = sid

    # ── Public API ──────────────────────────────────────────────

    def create_session(self) -> str:
        """Create a new empty session, return its id."""
        sid = f"sess_{uuid.uuid4().hex[:12]}"
        entry = {
            "id": sid,
            "title": "新对话",
            "created": time.time(),
            "updated": time.time(),
            "msg_count": 0,
        }
        with self._lock:
            self._sessions.insert(0, entry)
            self._save_index()
            self._save_messages(sid, [])
        self._current_id = sid
        log.info("Created session %s", sid)
        return sid

    def append_message(self, session_id: str, role: str, text: str,
                       image: "Image.Image | None" = None) -> None:
        """Append a message to the session file. Optionally saves an image thumbnail."""
        image_rel: str | None = None
        if image is not None:
            image_rel = self._save_image(session_id, image)

        msg = {
            "role": role,
            "text": text,
            "ts": time.time(),
        }
        if image_rel:
            msg["image"] = image_rel

        with self._lock:
            messages = self._load_messages(session_id)
            messages.append(msg)
            self._save_messages(session_id, messages)

            # Update index
            entry = self._find_entry(session_id)
            if entry:
                entry["updated"] = msg["ts"]
                entry["msg_count"] = len(messages)
                # Auto-title from first user message
                if entry["title"] == "新对话" and role == "user" and text:
                    entry["title"] = text[:20].replace("\n", " ")
                self._move_to_top(session_id)
                self._save_index()

    def load_session(self, session_id: str) -> list[dict]:
        """Load all messages for a session."""
        with self._lock:
            return self._load_messages(session_id)

    def list_sessions(self) -> list[dict]:
        """Return session index (most recent first)."""
        with self._lock:
            return list(self._sessions)

    def update_title(self, session_id: str, title: str) -> None:
        with self._lock:
            entry = self._find_entry(session_id)
            if entry:
                entry["title"] = title
                self._save_index()

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its message file."""
        with self._lock:
            self._sessions = [e for e in self._sessions if e["id"] != session_id]
            self._save_index()
            # Remove message file
            p = self._msg_path(session_id)
            if p.is_file():
                try:
                    p.unlink()
                except Exception as e:
                    log.warning("Failed to delete session file %s: %s", p, e)
            if self._current_id == session_id:
                self._current_id = None

    def ensure_current_session(self) -> str:
        """Return the current session id, creating one if needed."""
        if self._current_id is None:
            # Try to resume the most recent session
            if self._sessions:
                self._current_id = self._sessions[0]["id"]
            else:
                self.create_session()
        return self._current_id

    def resolve_image_path(self, relative: str) -> Path:
        """Resolve a relative image path to an absolute one."""
        return self._dir / relative

    # ── Internal ────────────────────────────────────────────────

    def _load_index(self) -> list[dict]:
        if self._index_path.is_file():
            try:
                with open(self._index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Failed to load sessions index: %s", e)
        return []

    def _save_index(self):
        try:
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log.warning("Failed to save sessions index: %s", e)

    def _msg_path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def _load_messages(self, session_id: str) -> list[dict]:
        p = self._msg_path(session_id)
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                log.warning("Failed to load session %s: %s", session_id, e)
        return []

    def _save_messages(self, session_id: str, messages: list[dict]):
        try:
            with open(self._msg_path(session_id), "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=1)
        except Exception as e:
            log.warning("Failed to save session %s: %s", session_id, e)

    def _find_entry(self, session_id: str) -> dict | None:
        for e in self._sessions:
            if e["id"] == session_id:
                return e
        return None

    def _move_to_top(self, session_id: str):
        for i, e in enumerate(self._sessions):
            if e["id"] == session_id:
                if i > 0:
                    self._sessions.insert(0, self._sessions.pop(i))
                return

    def _save_image(self, session_id: str, img: "Image.Image") -> str:
        """Save image thumbnail and return relative path."""
        try:
            thumb = img.copy()
            thumb.thumbnail((_MAX_THUMB, _MAX_THUMB), Image.LANCZOS)
            if thumb.mode not in ("RGB", "RGBA", "L"):
                thumb = thumb.convert("RGBA")
            ts = int(time.time() * 1000)
            fname = f"{session_id}_{ts}.png"
            rel = f"images/{fname}"
            thumb.save(self._dir / rel, format="PNG")
            return rel
        except Exception as e:
            log.warning("Failed to save image thumbnail: %s", e)
            return ""
