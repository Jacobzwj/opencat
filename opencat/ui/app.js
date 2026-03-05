const stateLabel = document.getElementById("state-label");
const catGif = document.getElementById("cat-gif");
const statusDot = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");
const messages = document.getElementById("messages");
const input = document.getElementById("msg-input");
const sendBtn = document.getElementById("send-btn");

let assetMap = {};
let streamingEl = null;
const fallbackSvg =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220" viewBox="0 0 220 220">
      <rect width="220" height="220" fill="#12122a"/>
      <circle cx="110" cy="112" r="64" fill="#4a4a5a"/>
      <polygon points="66,74 86,40 102,76" fill="#4a4a5a"/>
      <polygon points="154,74 134,40 118,76" fill="#4a4a5a"/>
      <circle cx="88" cy="108" r="8" fill="#7fe0a0"/>
      <circle cx="132" cy="108" r="8" fill="#7fe0a0"/>
      <polygon points="110,124 102,132 118,132" fill="#ff8fa3"/>
      <text x="110" y="168" text-anchor="middle" fill="#9ba0c8" font-size="14">GIF MISSING</text>
    </svg>`
  );

function scrollBottom() {
  messages.scrollTop = messages.scrollHeight;
}

function addMessage(role, content, isError = false) {
  const el = document.createElement("div");
  el.className = `msg ${role}${isError ? " error" : ""}`;
  el.textContent = content;
  messages.appendChild(el);
  scrollBottom();
  return el;
}

function setStatus(kind, text) {
  statusDot.className = `status-dot status-${kind}`;
  statusText.textContent = text;
}

function setCatState(state) {
  stateLabel.textContent = state;
  const src = assetMap[state];
  catGif.src = src || fallbackSvg;
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  await window.pywebview.api.send_message(text);
}

function handleEvent(event) {
  const t = event.type;
  const payload = event.payload;

  if (t === "status") {
    setStatus(payload.kind, payload.text);
    return;
  }
  if (t === "state") {
    setCatState(payload.state);
    return;
  }
  if (t === "assistant_begin") {
    streamingEl = addMessage("assistant", "...");
    return;
  }
  if (t === "assistant_delta") {
    if (!streamingEl) {
      streamingEl = addMessage("assistant", "");
    }
    streamingEl.textContent += payload.text;
    scrollBottom();
    return;
  }
  if (t === "assistant_final") {
    if (streamingEl) {
      streamingEl.textContent = payload.text || streamingEl.textContent;
      streamingEl = null;
      scrollBottom();
    } else if (payload.text) {
      addMessage("assistant", payload.text);
    }
    return;
  }
  if (t === "assistant_error") {
    streamingEl = null;
    addMessage("assistant", payload.message || "error", true);
  }
}

async function pollLoop() {
  try {
    const events = await window.pywebview.api.poll_events();
    for (const event of events) {
      handleEvent(event);
    }
  } catch (err) {
    addMessage("assistant", `bridge error: ${String(err)}`, true);
  } finally {
    window.setTimeout(pollLoop, 80);
  }
}

async function bootstrap() {
  assetMap = await window.pywebview.api.get_asset_map();
  const init = await window.pywebview.api.get_initial();
  setStatus(init.statusKind, init.statusText);
  setCatState(init.state);
  pollLoop();
}

sendBtn.addEventListener("click", sendMessage);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    sendMessage();
  }
});

window.addEventListener("pywebviewready", bootstrap);
