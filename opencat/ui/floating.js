const catWrap = document.getElementById("cat-wrap");
const catGif = document.getElementById("cat-gif");
const menu = document.getElementById("menu");
const quitBtn = document.getElementById("quit-btn");

let assetMap = {};
let quitting = false;
const fallbackSvg =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
      <circle cx="64" cy="70" r="34" fill="#4a4a5a"/>
      <polygon points="42,50 50,30 58,52" fill="#4a4a5a"/>
      <polygon points="86,50 78,30 70,52" fill="#4a4a5a"/>
      <circle cx="52" cy="68" r="5" fill="#7fe0a0"/>
      <circle cx="76" cy="68" r="5" fill="#7fe0a0"/>
    </svg>`
  );

function setState(state) {
  catGif.src = assetMap[state] || fallbackSvg;
}

function handleEvent(event) {
  if (event.type === "state") {
    setState(event.payload.state);
  }
}

async function pollLoop() {
  if (quitting) return;
  try {
    const events = await window.pywebview.api.poll_events();
    for (const event of events) {
      handleEvent(event);
    }
  } catch (_) {
    // Window may be closing; ignore.
  } finally {
    if (!quitting) {
      window.setTimeout(pollLoop, 120);
    }
  }
}

async function bootstrap() {
  assetMap = await window.pywebview.api.get_asset_map();
  const init = await window.pywebview.api.get_initial();
  setState(init.state);
  pollLoop();
}

catWrap.addEventListener("click", () => {
  menu.classList.add("hidden");
  window.pywebview.api.toggle_chat();
});

catWrap.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  menu.style.left = `${Math.max(4, e.clientX)}px`;
  menu.style.top = `${Math.max(4, e.clientY)}px`;
  menu.classList.remove("hidden");
});

quitBtn.addEventListener("click", () => {
  quitting = true;
  menu.classList.add("hidden");
  window.pywebview.api.quit_app();
});

window.addEventListener("click", () => {
  menu.classList.add("hidden");
});

window.addEventListener("pywebviewready", bootstrap);
