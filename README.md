<p align="center">
  <img src="docs/idle.gif" width="120" />
</p>

<h1 align="center">OpenCat</h1>

<p align="center">
  <b>A cute floating desktop cat that talks to your AI.</b><br>
  Lightweight chat companion for <a href="https://github.com/nicepkg/openclaw">OpenClaw</a> — no browser, no Electron, just a cat on your screen.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" />
  <img src="https://img.shields.io/badge/license-MIT-green" />
</p>

<table align="center">
  <tr>
    <td align="center" colspan="4"><b>Idle</b> (random rotation)</td>
    <td align="center"><b>Thinking</b></td>
    <td align="center"><b>Done</b></td>
    <td align="center"><b>Sleeping</b></td>
  </tr>
  <tr>
    <td align="center"><img src="docs/idle.gif" width="90" /><br><sub>yarn ball</sub></td>
    <td align="center"><img src="docs/error.gif" width="90" /><br><sub>scratching</sub></td>
    <td align="center"><img src="docs/connecting.gif" width="90" /><br><sub>rolling</sub></td>
    <td align="center"><img src="docs/talking.gif" width="90" /><br><sub>resting</sub></td>
    <td align="center"><img src="docs/thinking.gif" width="90" /><br><sub>eating</sub></td>
    <td align="center"><img src="docs/done.gif" width="90" /><br><sub>pooping</sub></td>
    <td align="center"><img src="docs/sleeping.gif" width="90" /><br><sub>pushing box</sub></td>
  </tr>
</table>

---

## Why OpenCat?

If you're running [OpenClaw](https://github.com/nicepkg/openclaw) as your AI gateway, you might find:

- **The official web panel is too cluttered** — you just want a quick, clean chat window
- **Telegram / WhatsApp bots need a VPN** if you're in mainland China
- **You want something that feels alive** — not another chat tab buried in your browser

OpenCat puts a pixel-art cat on your desktop. Click it, and a warm-toned chat window pops up. That's it. No browser, no VPN, no noise.

## Features

- **Floating cat widget** — always on top, draggable, with animated states (idle, thinking, talking, sleeping...)
- **Warm pastel chat UI** — clean, minimal, purpose-built for quick conversations
- **Streaming responses** — see the AI reply in real-time, token by token
- **Conversation history** — sessions are saved locally and browsable from the sidebar
- **Image attachments** — paste or drag images into the chat (clipboard + file picker)
- **Cross-platform** — Windows, macOS, Linux (native transparency on Windows, graceful fallback elsewhere)
- **Remote connection** — connect to an OpenClaw gateway on another machine via Tailscale or any network
- **Lightweight** — pure Python, ~100 KB installed, no Electron, no web runtime

## Quick Start

### Prerequisites

You need a running [OpenClaw](https://github.com/nicepkg/openclaw) gateway. OpenCat connects to it via WebSocket.

### Install

```bash
pip install git+https://github.com/Jacobzwj/opencat.git
```

### Run

```bash
opencat
```

OpenCat reads your gateway config from `~/.openclaw/openclaw.json` automatically.

### CLI Options

```bash
opencat --host 100.64.0.3    # Connect to a remote OpenClaw (e.g. via Tailscale)
opencat --port 18789          # Override gateway port
opencat --token your-token    # Override gateway token
opencat --debug               # Enable debug logging
```

## Development

```bash
git clone https://github.com/Jacobzwj/opencat.git
cd opencat
pip install -e .
opencat
```

## How It Works

```
┌──────────┐   WebSocket    ┌──────────────┐
│  OpenCat  │ ◄───────────► │  OpenClaw    │ ───► LLM API
│ (desktop) │   streaming   │  (gateway)   │
└──────────┘                └──────────────┘
```

OpenCat is a pure client — it connects to your self-hosted OpenClaw gateway over WebSocket, sends messages, and streams back responses. All AI logic, model selection, and API keys stay on the gateway side.

## Customizing the Cat

Cat animations live in `opencat/ui/assets/` and are mapped via `manifest.json`. Replace them with your own pixel art:

| State | What happens | Default GIF |
|-------|-------------|-------------|
| **Idle** | Connected, waiting for user. Randomly rotates through a pool. | `idle.gif`, `error.gif`, `connecting.gif`, `talking.gif` |
| **Thinking** | User sent a message, waiting for AI + streaming reply | `thinking.gif` (eating) |
| **Done** | Response complete, stays until next message | `done.gif` (pooping) |
| **Sleeping** | Not connected or idle for a long time | `sleeping.gif` (pushing box) |

## License

MIT
