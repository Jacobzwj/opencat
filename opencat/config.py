"""Auto-detect OpenClaw config from ~/.openclaw/openclaw.json."""

import json
import os
import sys
import logging

log = logging.getLogger(__name__)

# Defaults
PROTOCOL_VERSION = 3
CLIENT_ID = "opencat"
CLIENT_MODE = "operator"
CLIENT_VERSION = "0.1.0"

# Resolved at runtime
gateway_host: str = "127.0.0.1"
gateway_port: int = 18789
gateway_token: str = ""
ws_url: str = ""


def _find_openclaw_dir() -> str | None:
    """Find the .openclaw directory."""
    home = os.path.expanduser("~")
    d = os.path.join(home, ".openclaw")
    if os.path.isdir(d):
        return d
    return None


def load(port_override: int | None = None, token_override: str | None = None,
         host_override: str | None = None):
    """Load config from ~/.openclaw/openclaw.json, with optional CLI overrides."""
    global gateway_host, gateway_port, gateway_token, ws_url

    oc_dir = _find_openclaw_dir()
    if oc_dir:
        config_path = os.path.join(oc_dir, "openclaw.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                gw = data.get("gateway", {})
                gateway_host = gw.get("host", "127.0.0.1")
                gateway_port = gw.get("port", 18789)
                auth = gw.get("auth", {})
                gateway_token = auth.get("token", "")
                log.info("Loaded config from %s (host=%s, port=%s)",
                         config_path, gateway_host, gateway_port)
            except Exception as e:
                log.warning("Failed to read %s: %s", config_path, e)

    # CLI overrides take precedence
    if host_override is not None:
        gateway_host = host_override
    if port_override is not None:
        gateway_port = port_override
    if token_override is not None:
        gateway_token = token_override

    ws_url = f"ws://{gateway_host}:{gateway_port}/__openclaw__/gateway"

    if not gateway_token:
        log.error("No gateway token found. Pass --token or check ~/.openclaw/openclaw.json")
        sys.exit(1)
