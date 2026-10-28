/* Infinite Arch headquarters shell. It reads the local HQ API and is not the system of record. */
const $ = (id) => document.getElementById(id);
let token = "";
let view = { domain: null, selected: null };
let booted = false;
let listening = false;
let voiceOut = false;
let rec = null;
let state = { prefs: null, themes: [], status: null, board: null, provenance: "live" };
let lastRender = "";
let opener = null;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function clear(node) { node.replaceChildren(); }

function notice(text) {
  $("notice").textContent = text || "";
}

async function call(method, path, body) {
  const headers = {};
  const opts = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-HQ-Token"] = token;
    opts.body = JSON.stringify(body);
  }
  const response = await fetch(path, opts);
  let payload = {};
  try { payload = await response.json(); } catch (_error) { payload = {}; }
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

function applyTheme(id) {
  document.documentElement.dataset.theme = id || "hud";
  const colors = { hud: "#05080c", dark: "#12171c", light: "#f7f9fb" };
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", colors[id] || colors.hud);
}

function domainById(id) {
  return (state.board.domains || []).find((item) => item.id === id) || null;
}

async function refresh() {
  const prefs = await call("GET", "/api/v1/preferences");
  const themes = await call("GET", "/api/v1/themes");
  const workspace = encodeURIComponent(prefs.data.workspace_id || "local");
  const [status, board] = await Promise.all([
    call("GET", "/api/v1/system/status?workspace=" + workspace),
    call("GET", "/api/v1/universe?workspace=" + workspace),
  ]);
  state = { prefs: prefs.data, themes: themes.data, status: status.data, board: board.data, provenance: board.provenance };
  if (!booted) {
    applyTheme(prefs.data.theme || "hud");
    voiceOut = !!prefs.data.voice_out;
    view = {
      domain: (prefs.data.layout && prefs.data.layout.domain) || null,
      selected: (prefs.data.layout && prefs.data.layout.selected) || null,
    };
    fillThemes();
    booted = true;
  }
  syncControls();
  lastRender = "";
  render();
  placeOrbit();
}

function fillThemes() {
  const theme = $("theme");
  clear(theme);
  state.themes.forEach((item) => {
    const option = el("option", item.label);
    option.value = item.id;
    theme.append(option);
  });
}

function syncControls() {
  if (document.activeElement !== $("theme")) $("theme").value = state.prefs.theme;
  $("vout").textContent = voiceOut ? "Voice out on" : "Voice out off";
  $("vout").setAttribute("aria-pressed", voiceOut ? "true" : "false");
  const demo = !!state.prefs.demo;
  $("demo").textContent = demo ? "Demo mode on" : "Demo mode off";
  $("demo").setAttribute("aria-pressed", demo ? "true" : "false");
  $("banner").textContent = state.board.banner || "";
  $("banner").hidden = !state.board.banner;
  const hermes = (state.status.adapters || []).find((item) => item.id === "hermes");
  if (!listening) {
    $("stateChip").textContent = demo ? "Demo" : (hermes && hermes.status === "offline" ? "Hermes offline" : "Idle");
  }
    $("modeChip").textContent = state.provenance === "demo" ? "Demo fixtures" : "Live local data";
    $("new-action").disabled = demo;
  const workspace = $("workspace");
  if (document.activeElement !== workspace) {
    const current = state.prefs.demo ? (state.board.workspaces[0] || {}).id : state.prefs.workspace_id;
    clear(workspace);
    (state.board.workspaces || []).forEach((item) => {
      const option = el("option", item.name);
      option.value = item.id;
      workspace.append(option);
    });
    if (current) workspace.value = current;
    workspace.disabled = demo;
  }
  const provider = $("provider");
  if (document.activeElement !== provider) {
    const current = provider.value || "local-hq";
    clear(provider);
    (state.status.adapters || []).forEach((item) => {
      const option = el("option", item.label + " (" + item.status + ")");
      option.value = item.id;
      provider.append(option);
    });
    const values = [...provider.options].map((option) => option.value);
    provider.value = values.includes(current) ? current : (values.includes("local-hq") ? "local-hq" : values[0] || "");
  }
}

function render() {
  const key = JSON.stringify({ view, board: state.board, demo: state.prefs.demo, theme: state.prefs.theme });
  if (key === lastRender) return;
  lastRender = key;
  renderDock();
  renderOrbit();
  renderTiles();
  renderFeed();
  $("stage").classList.toggle("focused", !!view.domain);
}

function renderDock() {
  const dock = $("dock");
  clear(dock);
  const home = el("button", "Universe", "dk home");
  home.type = "button";
  home.setAttribute("aria-current", view.domain ? "false" : "page");
  home.addEventListener("click", () => openDomain(null));
  dock.append(home);
  (state.board.domains || []).forEach((domain) => {
    const button = el("button", "", "dk");
    button.type = "button";
    const label = el("span", domain.label);
    const mark = domain.provenance === "demo" ? "demo" : domain.provenance;
    label.append(el("em", mark, "src " + (domain.provenance === "live" ? "live" : "sample")));
    button.append(label);
    button.setAttribute("aria-current", view.domain === domain.id ? "page" : "false");
    button.addEventListener("click", () => openDomain(domain.id));
    dock.append(button);
  });
}

function renderOrbit() {
  const orbit = $("orbit");
  clear(orbit);
  (state.board.domains || []).forEach((domain) => {
    const button = el("button", domain.label, "orbit-btn");
    button.type = "button";
    button.tabIndex = -1;
    button.style.setProperty("--c", domain.color);
    button.addEventListener("click", () => openDomain(domain.id));
    orbit.append(button);
  });
}

function placeOrbit() {
  const stage = $("stage").getBoundingClientRect();
  const buttons = $("orbit").querySelectorAll("button");
  const cx = stage.width / 2;
  const cy = stage.height * 0.46;
  const rx = Math.min(stage.width * 0.28, 280);
  const ry = Math.min(stage.height * 0.28, 160);
  buttons.forEach((button, index) => {
    const angle = -Math.PI / 2 + (index / Math.max(buttons.length, 1)) * Math.PI * 2;
    button.style.left = (cx + Math.cos(angle) * rx) + "px";
    button.style.top = (cy + Math.sin(angle) * ry) + "px";
  });
}

function kpiList(kpis) {
  const wrap = el("div", null, "mini");
  (kpis || []).forEach((pair) => {
    const cell = el("div");
    cell.append(el("i", pair[0]), el("b", String(pair[1])));
    wrap.append(cell);
  });
  return wrap;
}

function renderTiles() {
  const left = $("colL");
  const right = $("colR");
  clear(left);
  clear(right);
  const domain = domainById(view.domain);
  if (!domain) return;
  const summary = el("article", null, "tile sum");
  const head = el("div", null, "th");
  head.append(el("i", "", "dot"), el("b", domain.label), el("span", domain.provenance));
  summary.append(head, el("p", domain.summary), kpiList(domain.kpis));
  left.append(summary);
  if (domain.provenance === "unavailable") {
    left.append(el("p", "This module is unavailable. Nothing here is a count of your records.", "empty"));
    return;
  }
  if (!domain.children.length) left.append(el("p", "No records yet.", "empty"));
  domain.children.forEach((child) => {
    const card = el("button", null, "tile card" + (view.selected === child.id ? " sel" : ""));
    card.type = "button";
    const cardHead = el("div", null, "th");
    cardHead.append(el("i", "", "dot " + child.status), el("b", child.name), el("span", child.status));
    card.append(cardHead, el("p", child.summary));
    card.addEventListener("click", () => selectChild(child.id));
    left.append(card);
  });
  const selected = domain.children.find((child) => child.id === view.selected);
  if (!selected) {
    right.append(el("p", "Select a record to see timestamps, status, and actions.", "empty"));
    return;
  }
  const detail = el("article", null, "tile");
  detail.id = "detail";
  detail.tabIndex = -1;
  const detailHead = el("div", null, "th");
  detailHead.append(el("i", "", "dot " + selected.status), el("h2", selected.name), el("span", selected.provenance));
  detail.append(detailHead, el("p", selected.summary));
  const list = el("dl");
  (selected.fields || []).forEach((field) => {
    list.append(el("dt", field[0]), el("dd", field[1]));
  });
  detail.append(list);
  const actions = el("div", null, "actions");
  (selected.actions || []).forEach((action) => {
    const button = el("button", actionLabel(action), "btn");
    button.type = "button";
    button.addEventListener("click", () => runAction(action, selected));
    actions.append(button);
  });
  if (actions.childElementCount) detail.append(actions);
  if (selected.history && selected.history.length) {
    detail.append(el("h3", "History"));
    const history = el("ul", null, "history");
    selected.history.forEach((item) => {
      const line = el("li");
      line.append(el("span", (item.finished_at || item.started_at || "") + " " + (item.status || "")));
      if (item.error) line.append(el("span", item.error, "err"));
      else if (item.outcome && String(item.outcome).length < 240) line.append(el("span", item.outcome));
      history.append(line);
    });
    detail.append(history);
  }
  right.append(detail);
}

function actionLabel(action) {
  const labels = {
    run: "Run now",
    pause: "Pause",
    resume: "Resume",
    approve: "Approve",
    deny: "Deny",
    "accept-demo": "Accept demo",
    "request-changes": "Request changes",
  };
  return labels[action] || action;
}

function renderFeed() {
  const feed = $("feed");
  clear(feed);
  const events = state.board.events || [];
  if (!events.length) {
    feed.append(el("p", state.prefs.demo ? "No fixture events." : "No local events yet.", "ev"));
    return;
  }
  events.slice(0, 5).forEach((item) => {
    const line = el("p", null, "ev");
    line.append(el("span", item.provenance === "demo" ? "demo" : "live", "tag"));
    line.append(document.createTextNode(" " + (item.created_at || "") + " " + (item.message || "")));
    feed.append(line);
  });
}

function addHist(kind, text) {
  const hist = $("hist");
  const line = el("p", (kind === "you" ? "You: " : "HQ: ") + text, "h " + kind);
  hist.append(line);
  while (hist.children.length > 6) hist.firstElementChild.remove();
}

function maybeSpeak(text) {
  if (!voiceOut || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

async function saveLayout() {
  try {
    const saved = await call("PUT", "/api/v1/preferences", { layout: { domain: view.domain, selected: view.selected } });
    state.prefs = saved.data;
  } catch (error) {
    notice(error.message);
  }
}

function openDomain(id) {
  view = { domain: id, selected: null };
  lastRender = "";
  render();
  placeOrbit();
  saveLayout();
}

function selectChild(id) {
  view.selected = id;
  lastRender = "";
  render();
  const detail = $("detail");
  if (detail) detail.focus();
  saveLayout();
}

async function runAction(action, selected) {
  const workspace = encodeURIComponent(state.prefs.workspace_id || "local");
  try {
    if (action === "run") {
      const result = await call("POST", "/api/v1/schedules/" + selected.id + "/run?workspace=" + workspace, {});
      notice("Run " + result.data.status + " at " + (result.data.finished_at || ""));
    } else if (action === "pause" || action === "resume") {
      await call("PATCH", "/api/v1/schedules/" + selected.id + "?workspace=" + workspace, { enabled: action === "resume" });
      notice(action === "resume" ? "Schedule resumed." : "Schedule paused.");
    } else if (action === "approve" || action === "deny" || action === "accept-demo" || action === "request-changes") {
      const decision = { approve: "approved", deny: "denied", "accept-demo": "accepted", "request-changes": "changes_requested" }[action];
      const warning = action === "accept-demo" || action === "request-changes"
        ? "This records an owner decision and does not merge or deploy."
        : "This records your decision. The headquarters shell will not send or execute an external action.";
      if (!window.confirm(warning)) return;
      await call("POST", "/api/v1/approvals/" + selected.id + "/decision?workspace=" + workspace, { decision });
      notice("Decision recorded.");
    }
    lastRender = "";
    await refresh();
  } catch (error) {
    notice(error.message);
  }
}

function labeled(text, control) {
  const label = el("label");
  label.append(document.createTextNode(text), control);
  return label;
}
function fieldInput(id, required) {
  const node = el("input");
  node.id = id;
  if (required) node.required = true;
  return node;
}
function fieldArea(id) {
  const node = el("textarea");
  node.id = id;
  node.required = true;
  node.rows = 4;
  return node;
}

function buildEditorFields(kind) {
  const fields = $("editor-fields");
  while (fields.children.length > 1) fields.lastElementChild.remove();
  if (kind === "agent") {
    fields.append(labeled("Name", fieldInput("agent-name", true)));
    fields.append(labeled("Purpose", fieldArea("agent-purpose")));
    const adapters = el("select");
    adapters.id = "agent-adapter";
    (state.status.adapters || []).forEach((item) => {
      const option = el("option", item.label);
      option.value = item.id;
      adapters.append(option);
    });
    fields.append(labeled("Adapter", adapters));
    const locality = el("select");
    locality.id = "agent-locality";
    [["local", "Local"], ["provider-choice", "Provider choice"]].forEach(([value, label]) => {
      const option = el("option", label);
      option.value = value;
      locality.append(option);
    });
    fields.append(labeled("Locality", locality));
  } else if (kind === "task") {
    fields.append(labeled("Title", fieldInput("task-title", true)));
    fields.append(labeled("Detail", fieldArea("task-detail")));
  } else {
    fields.append(labeled("Name", fieldInput("sched-name", true)));
    const cadence = fieldInput("sched-cadence", false);
    cadence.inputMode = "numeric";
    cadence.placeholder = "Seconds, or leave empty for manual";
    fields.append(labeled("Cadence seconds", cadence));
  }
  const confirm = fieldInput("editor-confirm", true);
  confirm.type = "checkbox";
  const confirmLabel = el("label");
  confirmLabel.append(confirm, document.createTextNode(" I understand this does not install software, send messages, or push Git."));
  fields.append(confirmLabel);
  const help = {
    external: "An external action waits for your approval and is still not executed by this shell.",
    agent: "This writes a proposed definition for you to review in Git. It does not install the agent.",
  };
  $("editor-help").textContent = help[kind] || "This writes a local record on this machine.";
}

function openEditor(from) {
  opener = from;
  const fields = $("editor-fields");
  clear(fields);
  const kind = el("select");
  kind.id = "editor-kind";
  [
    ["task", "Task"],
    ["schedule", "Scheduled local check"],
    ["note", "Scheduled local note"],
    ["external", "External action (approval only)"],
    ["agent", "Agent proposal"],
  ].forEach(([value, label]) => {
    const option = el("option", label);
    option.value = value;
    kind.append(option);
  });
  fields.append(labeled("What do you want to add?", kind));
  kind.addEventListener("change", () => buildEditorFields(kind.value));
  buildEditorFields("task");
  $("editor").showModal();
  kind.focus();
}

async function saveEditor(event) {
  event.preventDefault();
  const kind = $("editor-kind").value;
  if (!$("editor-confirm").checked) {
    notice("Confirm that this stays on the machine.");
    return;
  }
  const workspace = state.prefs.workspace_id || "local";
  try {
    if (kind === "task") {
      await call("POST", "/api/v1/tasks", {
        title: $("task-title").value,
        detail: $("task-detail").value,
        workspace_id: workspace,
      });
      view.domain = "tasks";
    } else if (kind === "agent") {
      await call("POST", "/api/v1/agents", {
        name: $("agent-name").value,
        purpose: $("agent-purpose").value,
        adapter: $("agent-adapter").value,
        locality: $("agent-locality").value,
        workspace_id: workspace,
      });
      view.domain = "agents";
      notice("Proposal written for Git review. Nothing was installed or pushed.");
    } else {
      const payload = {
        name: $("sched-name").value,
        kind: kind === "note" ? "local-note" : (kind === "external" ? "external-action" : "adapter-status"),
        workspace_id: workspace,
      };
      const cadence = $("sched-cadence").value.trim();
      if (cadence) {
        const parsed = Number(cadence);
        if (!Number.isInteger(parsed)) throw new Error("Cadence must be a whole number of seconds.");
        payload.cadence_seconds = parsed;
      }
      await call("POST", "/api/v1/schedules", payload);
      view.domain = "schedules";
    }
    $("editor").close();
    lastRender = "";
    await refresh();
  } catch (error) {
    notice(error.message);
  }
}

function setRecording(active) {
  listening = active;
  $("mic").classList.toggle("recording", active);
  $("mic").setAttribute("aria-pressed", active ? "true" : "false");
  $("mic").textContent = active ? "Recording" : "Microphone off";
  $("stateChip").textContent = active ? "Recording" : $("stateChip").textContent;
}

function toggleMic() {
  const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Speech) {
    $("voiceNote").textContent = "This browser has no speech recognition. Type the command. Audio is not uploaded.";
    return;
  }
  if (listening && rec) {
    rec.stop();
    return;
  }
  rec = new Speech();
  rec.lang = "en-US";
  rec.interimResults = false;
  rec.continuous = false;
  rec.onresult = (event) => {
    const text = event.results[0][0].transcript;
    $("inp").value = text;
    $("voiceNote").textContent = "Transcript stays in the command box until you send it. Audio was not uploaded by HQ.";
  };
  rec.onerror = (event) => {
    setRecording(false);
    $("voiceNote").textContent = event.error === "not-allowed"
      ? "Microphone permission was denied. Nothing was recorded."
      : "Microphone stopped (" + event.error + "). You can type instead.";
  };
  rec.onend = () => setRecording(false);
  try {
    rec.start();
    setRecording(true);
    $("voiceNote").textContent = "Microphone is recording on this device. HQ does not upload audio.";
  } catch (_error) {
    setRecording(false);
    $("voiceNote").textContent = "Microphone did not start. You can type instead.";
  }
}

function tickClock() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  $("clock").setAttribute("datetime", now.toISOString());
}

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function draw(time) {
  const canvas = $("cv");
  const rect = canvas.parentElement.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const cx = rect.width / 2;
  const cy = rect.height * 0.46;
  const radius = Math.min(rect.width, rect.height) * 0.16;
  const cyan = getComputedStyle(document.documentElement).getPropertyValue("--cyan").trim() || "#7ee7ff";
  ctx.strokeStyle = cyan;
  ctx.globalAlpha = 0.55;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cy, Math.max(radius, 20), 0, Math.PI * 2);
  ctx.stroke();
  const spin = reducedMotion ? 0 : time / 12000;
  const domains = (state.board && state.board.domains) || [];
  domains.forEach((domain, index) => {
    const angle = spin + (index / Math.max(domains.length, 1)) * Math.PI * 2 - Math.PI / 2;
    ctx.fillStyle = domain.color || cyan;
    ctx.beginPath();
    ctx.arc(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius * 0.72, 4, 0, Math.PI * 2);
    ctx.fill();
  });
  ctx.globalAlpha = 1;
  if (!reducedMotion) requestAnimationFrame(draw);
}

$("theme").addEventListener("change", async () => {
  const theme = $("theme").value;
  applyTheme(theme);
  try {
    const saved = await call("PUT", "/api/v1/preferences/theme", { theme });
    state.prefs = saved.data;
    notice("Theme saved on this machine.");
  } catch (error) {
    notice(error.message);
  }
});

$("workspace").addEventListener("change", async () => {
  if (state.prefs.demo) return;
  try {
    await call("PUT", "/api/v1/preferences", { workspace_id: $("workspace").value });
    view = { domain: null, selected: null };
    lastRender = "";
    await refresh();
  } catch (error) {
    notice(error.message);
  }
});

$("demo").addEventListener("click", async () => {
  try {
    await call("PUT", "/api/v1/preferences", { demo: !state.prefs.demo });
    view = { domain: view.domain, selected: null };
    lastRender = "";
    await refresh();
  } catch (error) {
    notice(error.message);
  }
});

$("vout").addEventListener("click", async () => {
  voiceOut = !voiceOut;
  $("voiceNote").textContent = voiceOut
    ? "Voice out uses this browser speech engine. Some browsers send speech to their own service. HQ does not upload audio."
    : "Voice out is off.";
  $("vout").textContent = voiceOut ? "Voice out on" : "Voice out off";
  $("vout").setAttribute("aria-pressed", voiceOut ? "true" : "false");
  if (!voiceOut && window.speechSynthesis) window.speechSynthesis.cancel();
  try {
    const saved = await call("PUT", "/api/v1/preferences", { voice_out: voiceOut });
    state.prefs = saved.data;
  } catch (error) {
    notice(error.message);
  }
});

$("mic").addEventListener("click", toggleMic);
$("new-action").addEventListener("click", (event) => openEditor(event.currentTarget));
$("editor-cancel").addEventListener("click", () => $("editor").close());
$("editor").addEventListener("close", () => { if (opener) opener.focus(); });
$("editor-form").addEventListener("submit", saveEditor);

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("inp").value.trim();
  if (!text) return;
  $("inp").value = "";
  addHist("you", text);
  const lower = text.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
  if (["home", "universe", "back"].includes(lower)) {
    openDomain(null);
    addHist("hq", "Back to the universe.");
    return;
  }
  const domain = (state.board.domains || []).find((item) => lower === item.id || lower === item.label.toLowerCase() || lower === "open " + item.label.toLowerCase());
  if (domain) {
    openDomain(domain.id);
    addHist("hq", "Opened " + domain.label + ".");
    return;
  }
  if (state.prefs.demo) {
    addHist("hq", "Demo mode only navigates fixtures. Turn demo mode off to store a command.");
    return;
  }
  try {
    const result = await call("POST", "/api/v1/conversations/command/messages", {
      text,
      provider: $("provider").value || "local-hq",
      locality: "local",
    });
    addHist("hq", result.data.result.text);
    maybeSpeak(result.data.result.text);
  } catch (error) {
    addHist("hq", error.message);
    notice(error.message);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if ($("editor").open) return;
    if (view.selected) selectChild(null);
    else if (view.domain) openDomain(null);
    return;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const tag = event.target && event.target.tagName;
  if (tag && ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag)) return;
  if (event.key.length === 1) {
    $("inp").focus();
    $("inp").value += event.key;
    event.preventDefault();
  }
});

window.addEventListener("resize", placeOrbit);
tickClock();
setInterval(tickClock, 1000);
requestAnimationFrame(draw);
setInterval(() => {
  const active = document.activeElement;
  if ($("editor").open || listening) return;
  if (active && active !== document.body && active !== $("inp")) return;
  refresh().catch((error) => notice(error.message));
}, 8000);

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

(async () => {
  try {
    const csrf = await call("GET", "/api/csrf");
    token = csrf.token;
    await refresh();
  } catch (_error) {
    notice("Could not reach the local headquarters service.");
  }
})();
