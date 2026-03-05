"""WebSocket connection manager for OpenClaw gateway."""

import json
import threading
import logging
from typing import Callable

import websocket

from opencat import config, protocol

log = logging.getLogger(__name__)


class OpenClawClient:
    def __init__(
        self,
        on_connected: Callable,
        on_disconnected: Callable,
        on_error: Callable[[str], None],
        on_delta: Callable[[str], None],
        on_final: Callable[[str], None],
        on_chat_error: Callable[[str], None],
    ):
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_error = on_error
        self.on_delta = on_delta
        self.on_final = on_final
        self.on_chat_error = on_chat_error

        self.ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self.session_key: str | None = None
        self.connected = False
        self._connect_req_id: str | None = None

    def connect(self):
        self.ws = websocket.WebSocketApp(
            config.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        self._thread = threading.Thread(
            target=self.ws.run_forever,
            kwargs={"ping_interval": 30, "ping_timeout": 10},
            daemon=True,
        )
        self._thread.start()

    def _on_open(self, ws):
        log.info("WebSocket opened, sending handshake")
        msg = protocol.make_connect_message()
        self._connect_req_id = msg["id"]
        ws.send(json.dumps(msg))

    def _on_message(self, ws, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type")

        if msg_type == "res":
            req_id = data.get("id")
            if req_id == self._connect_req_id:
                if data.get("ok"):
                    payload = data.get("payload", {})
                    self.session_key = payload.get("sessionKey", "agent:main:main")
                    self.connected = True
                    log.info("Connected, session=%s", self.session_key)
                    self.on_connected()
                else:
                    err = data.get("error", {})
                    log.error("Connect failed: %s", err)
                    self.on_error(str(err.get("message", err)))
                self._connect_req_id = None
            else:
                if not data.get("ok"):
                    err = data.get("error", {})
                    self.on_chat_error(str(err.get("message", err)))
            return

        if msg_type == "event" and data.get("event") == "chat":
            payload = data.get("payload", {})
            state = payload.get("state")
            text = self._extract_text(payload)
            if state == "delta" and text:
                self.on_delta(text)
            elif state == "final":
                self.on_final(text)
            elif state == "error":
                self.on_chat_error(payload.get("errorMessage", "Unknown error"))

    def _extract_text(self, payload: dict) -> str:
        message = payload.get("message", {})
        content = message.get("content", [])
        if isinstance(content, str):
            return content
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )

    def _on_close(self, ws, code, reason):
        self.connected = False
        log.info("WebSocket closed: code=%s reason=%s", code, reason)
        self.on_disconnected()

    def _on_error(self, ws, error):
        log.error("WebSocket error: %s", error)
        self.on_error(str(error))

    def send_message(self, content):
        if self.ws and self.connected and self.session_key:
            msg = protocol.make_chat_send(content, self.session_key)
            self.ws.send(json.dumps(msg))

    def disconnect(self):
        if self.ws:
            self.ws.close()
            self.connected = False
