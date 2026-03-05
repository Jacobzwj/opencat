"""OpenClaw WebSocket protocol message builders."""

import sys
import uuid

from opencat import config


def make_connect_message() -> dict:
    return {
        "type": "req",
        "method": "connect",
        "id": str(uuid.uuid4()),
        "params": {
            "minProtocol": config.PROTOCOL_VERSION,
            "maxProtocol": config.PROTOCOL_VERSION,
            "client": {
                "id": config.CLIENT_ID,
                "mode": config.CLIENT_MODE,
                "version": config.CLIENT_VERSION,
                "platform": sys.platform,
            },
            "auth": {
                "token": config.gateway_token,
            },
            "role": "operator",
            "scopes": ["operator.write", "operator.read"],
        },
    }


def make_chat_send(content, session_key: str) -> dict:
    """Build a chat.send request.

    content may be a plain str (text-only) or a list of content blocks
    (e.g. [{"type": "image", "source": {...}}, {"type": "text", "text": "..."}]).
    """
    msg_id = str(uuid.uuid4())
    return {
        "type": "req",
        "method": "chat.send",
        "id": msg_id,
        "params": {
            "message": content,
            "sessionKey": session_key,
            "deliver": False,
            "idempotencyKey": msg_id,
        },
    }


def make_chat_history(session_key: str, limit: int = 5) -> dict:
    """Build a chat.history request to fetch recent messages."""
    return {
        "type": "req",
        "method": "chat.history",
        "id": str(uuid.uuid4()),
        "params": {
            "sessionKey": session_key,
            "limit": limit,
        },
    }
