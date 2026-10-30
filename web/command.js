/* WHOPR command center. Real data only: every tile, feed line and answer comes from the
   local runtime (/api/v1 read views, /api/os/state, /api/chat). No mock data, no innerHTML. */
'use strict';
(() => {
const $ = (s) => document.querySelector(s);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
const TAU = Math.PI * 2;
const lerp = (a, b, k) => a + (b - a) * k;

/* ---------- state ---------- */
let snap = null, v1 = {}, csrf = '', openDomain = null;
const dataChip = $('#data-chip');

async function getJSON(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error(path + ' ' + r.status);
  return r.json();
}
async function refresh() {
  try {
    const [state, ...rest] = await Promise.all([
      getJSON('/api/os/state'),
      ...['status', 'apps', 'agents', 'tasks', 'schedules', 'runs', 'events', 'approvals', 'conversations', 'themes']
        .map((r) => getJSON('/api/v1/' + r).catch(() => null)),
    ]);
    snap = state;
    ['status', 'apps', 'agents', 'tasks', 'schedules', 'runs', 'events', 'approvals', 'conversations', 'themes']
      .forEach((r, i) => { if (rest[i]) v1[r] = rest[i].data; });
    dataChip.textContent = 'live';
    dataChip.className = 'chip ok';
    renderTiles(); renderFeed(); layout(); openHash();
  } catch (e) {
    dataChip.textContent = 'API offline';
    dataChip.className = 'chip ex';
  }
  setTimeout(refresh, 10000);
}

/* ---------- domains from real data ---------- */
const BAD = new Set(['failed', 'error', 'rejected', 'missing', 'unavailable']);
const WARN = new Set(['paused', 'idle', 'awaiting_approval', 'pending', 'warn']);
function stOf(v) { v = String(v || '').toLowerCase(); return BAD.has(v) ? 'error' : WARN.has(v) ? 'warn' : 'ok'; }

function domains() {
  const tasks = (v1.tasks && v1.tasks.tasks) || [];
  const schedules = (v1.schedules && v1.schedules.schedules) || [];
  const approvals = (v1.approvals && v1.approvals.approvals) || [];
  const agents = (v1.agents && v1.agents.agents) || [];
  const events = (v1.events && v1.events.events) || [];
  const activity = (v1.events && v1.events.activity) || [];
  const rawConn = snap && snap.connectors;
  const connectors = Array.isArray(rawConn) ? rawConn : ((rawConn && rawConn.connectors) || []);
  const apps = (v1.apps && v1.apps.apps) || [];
  const pending = approvals.filter((a) => a.status === 'pending');
  const failed = tasks.filter((t) => t.status === 'failed');
  return [
    { id: 'apps', name: 'APPS', color: '#3FD8FF', panel: 'apps',
      st: apps.some((a) => a.status === 'error' || a.error) ? 'error' : 'ok',
      kpis: [['Module apps', apps.length], ['Records', apps.reduce((n, a) => n + Object.values(a.counts || {}).reduce((m, c) => m + (Number(c) || 0), 0), 0)]],
      rows: apps.map((a) => ({ name: a.name || a.id, st: a.error ? 'error' : 'ok', link: '#apps' })) },
    { id: 'agents', name: 'AGENTS', color: '#8EE7C8', panel: 'agents',
      st: agents.some((a) => a.last_error) ? 'warn' : 'ok',
      kpis: [['Agents', agents.length], ['Ready', agents.filter((a) => !a.last_error).length]],
      rows: agents.map((a) => ({ name: a.label || a.name || a.id, st: a.last_error ? 'warn' : 'ok', detail: a.role || a.adapter, link: '#agents' })) },
    { id: 'approvals', name: 'DECISIONS', color: '#FFB02E', panel: 'tasks',
      st: pending.length ? 'warn' : 'ok',
      kpis: [['Pending', pending.length], ['Decided', approvals.length - pending.length]],
      rows: approvals.slice(0, 12).map((a) => ({ name: a.title || a.id, st: a.status === 'pending' ? 'warn' : 'ok', detail: a.status, link: '#tasks' })) },
    { id: 'tasks', name: 'TASKS', color: '#FF7A8A', panel: 'tasks',
      st: failed.length ? 'error' : 'ok',
      kpis: [['Total', tasks.length], ['Failed', failed.length]],
      rows: tasks.slice(0, 15).map((t) => ({ name: t.title || t.id, st: stOf(t.status), detail: t.status, link: '#tasks' })) },
    { id: 'schedules', name: 'SCHEDULES', color: '#9AD7FF', panel: 'schedules',
      st: schedules.some((s) => (s.history || [])[0] && s.history[0].status === 'failed') ? 'warn' : 'ok',
      kpis: [['Schedules', schedules.length], ['Enabled', schedules.filter((s) => s.enabled).length]],
      rows: schedules.map((s) => ({ name: s.name || s.id, st: s.enabled ? 'ok' : 'warn', detail: (s.history || [])[0] ? s.history[0].status : '', link: '#schedules' })) },
    { id: 'activity', name: 'ACTIVITY', color: '#B7C4DC', panel: 'activity',
      st: 'ok',
      kpis: [['Events', events.length], ['Actions', activity.length]],
      rows: (activity.length ? activity : events).slice(0, 15).map((e) => ({
        name: e.sentence || e.message || e.event, st: 'ok', detail: e.time || '', link: '#activity' })) },
    { id: 'connectors', name: 'CONNECTORS', color: '#D8A6FF', panel: 'connectors',
      st: connectors.some((c) => c.status === 'error' || c.error) ? 'warn' : 'ok',
      kpis: [['Connectors', connectors.length], ['OK', connectors.filter((c) => !c.error && c.status !== 'error').length]],
      rows: connectors.map((c) => ({ name: c.name || c.id, st: c.error ? 'warn' : 'ok', detail: c.detail || c.status || '', link: '#connectors' })) },
    { id: 'system', name: 'SYSTEM', color: '#FFD27A', panel: 'system',
      st: (v1.status && v1.status.brain && v1.status.brain.drift) ? 'warn' : 'ok',
      kpis: [['Storage', (snap && snap.storage) || '-'], ['Brain drift', (v1.status && v1.status.brain && v1.status.brain.drift) ? 'yes' : 'no']],
      rows: ((v1.status && v1.status.capabilities) || []).map((c) => ({ name: c.name, st: stOf(c.status), detail: c.status, link: '#system' })) },
  ];
}

/* ---------- tiles ---------- */
const tilesEl = $('#tiles');
function renderTiles() {
  const ds = domains();
  tilesEl.replaceChildren();
  ds.forEach((d, i) => {
    const t = el('button', undefined, 'tile st-' + d.st);
    t.type = 'button';
    t.dataset.i = i;
    const th = el('div', undefined, 'th');
    const dot = el('i', undefined, 'dot');
    dot.style.background = d.color;
    th.append(dot, el('b', d.name));
    const k = el('div', undefined, 'k');
    d.kpis.forEach(([label, val]) => k.append(el('span', label), el('b', String(val))));
    t.append(th, k);
    t.addEventListener('click', () => openDetail(d));
    tilesEl.append(t);
  });
}
function layout() {
  const W = innerWidth, H = innerHeight, n = tilesEl.children.length;
  const cols = W < 700 ? 2 : (n <= 4 ? n : Math.ceil(n / 2));
  const rows = Math.ceil(n / cols);
  const cx = W / 2, cy = H * 0.52;
  [...tilesEl.children].forEach((t, i) => {
    const col = i % cols, row = (i / cols) | 0;
    t.style.left = (cx + (col - (cols - 1) / 2) * Math.min(240, W / (cols + 1))) + 'px';
    t.style.top = (cy + (row - (rows - 1) / 2) * 130) + 'px';
  });
}
addEventListener('resize', layout);

/* ---------- detail ---------- */
const detail = $('#detail');
function openDetail(d) {
  openDomain = d;
  $('#detail-title').textContent = d.name;
  const kpis = $('#detail-kpis'); kpis.replaceChildren();
  d.kpis.forEach(([label, val]) => {
    const k = el('div', undefined, 'kpi');
    k.append(el('span', label), el('b', String(val)));
    kpis.append(k);
  });
  const rows = $('#detail-rows'); rows.replaceChildren();
  if (!d.rows.length) rows.append(el('div', 'No records yet.', 'row'));
  d.rows.forEach((r) => {
    const row = r.link ? el('a', undefined, 'row') : el('div', undefined, 'row');
    if (r.link) { row.href = '/' + r.link; row.title = 'Open in dashboard'; }
    const st = el('span', r.detail || r.st || '', 'st ' + (r.st === 'error' ? 'bad' : r.st === 'ok' ? 'good' : ''));
    row.append(el('span', r.name), st);
    rows.append(row);
  });
  detail.hidden = false;
}
function openHash() {
  const wanted = location.hash.slice(1);
  if (!wanted) return;
  const d = domains().find((x) => x.id === wanted);
  if (d) openDetail(d);
}
$('#detail-back').addEventListener('click', () => { detail.hidden = true; openDomain = null; });
addEventListener('keydown', (e) => { if (e.key === 'Escape' && !detail.hidden) { detail.hidden = true; openDomain = null; } });

/* ---------- feed ---------- */
const feedEl = $('#feed');
function renderFeed() {
  feedEl.replaceChildren();
  const items = ((v1.events && (v1.events.activity.length ? v1.events.activity : v1.events.events)) || []).slice(0, 7);
  items.forEach((e) => {
    const f = el('div', undefined, 'f');
    f.append(el('b', (e.time || '').slice(11, 16) + ' '), document.createTextNode(e.sentence || e.message || e.event || ''));
    feedEl.append(f);
  });
}

/* ---------- chat ---------- */
const whoprEl = $('#whopr'), histEl = $('#hist'), inp = $('#inp');
let speaking = false, listening = false;

function addHist(cls, text) {
  const h = el('div', text, 'h ' + cls);
  histEl.append(h);
  while (histEl.children.length > 9) histEl.firstChild.remove();
}
function say(t) {
  const prev = whoprEl.textContent;
  if (prev && !whoprEl.dataset.done) { addHist('w', prev); }
  whoprEl.dataset.done = '';
  whoprEl.textContent = t;
}
async function send(text) {
  const raw = (text || '').trim();
  if (!raw) return;
  addHist('you', raw);
  const low = raw.toLowerCase().replace(/^(hey|ok|okay)\s+whopr[,.!\s]*/, '').replace(/^(open|show me|show|go to|zoom to|zoom in on)\s+/, '').trim();
  const d = domains().find((x) => x.id === low || x.name.toLowerCase() === low || (x.id + 's') === low);
  if (d) { openDetail(d); say(d.name + ' on screen.'); return; }
  whoprEl.classList.add('think');
  say('Thinking...');
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-HQ-Token': csrf },
      body: JSON.stringify({ text: raw }),
    });
    const j = await r.json();
    whoprEl.classList.remove('think');
    if (!r.ok) { say(j.error || ('Error ' + r.status)); return; }
    say(j.message || j.answer || '(no answer)');
  } catch (e) {
    whoprEl.classList.remove('think');
    say('The local OS is not answering. Is ./hq start running?');
  }
}
whoprEl.addEventListener('click', () => { inp.hidden = false; inp.focus(); });
inp.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { const v = inp.value; inp.value = ''; inp.hidden = true; send(v); }
  if (e.key === 'Escape') { inp.value = ''; inp.hidden = true; }
});
addEventListener('keydown', (e) => {
  if (inp.hidden && e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey && e.target === document.body) {
    inp.hidden = false; inp.focus();
  }
});

/* ---------- voice out: browser speechSynthesis only ---------- */
let voiceOut = false;
try { voiceOut = localStorage.getItem('whopr.voice') === '1'; } catch (e) {}
const voutBtn = $('#vout');
function setVout() { voutBtn.textContent = 'Voice out: ' + (voiceOut ? 'on' : 'off'); voutBtn.classList.toggle('on', voiceOut); }
setVout();
voutBtn.addEventListener('click', () => {
  voiceOut = !voiceOut;
  try { localStorage.setItem('whopr.voice', voiceOut ? '1' : '0'); } catch (e) {}
  setVout();
  if (!voiceOut && 'speechSynthesis' in window) { try { speechSynthesis.cancel(); } catch (e) {} speaking = false; }
});
const origSay = say;
say = function (t) {
  origSay(t);
  if (voiceOut && 'speechSynthesis' in window && t && !t.startsWith('Thinking')) {
    try {
      const u = new SpeechSynthesisUtterance(t);
      u.rate = 1.02;
      u.onend = () => { speaking = false; };
      speaking = true;
      speechSynthesis.speak(u);
    } catch (e) {}
  }
};

/* ---------- mic: SpeechRecognition, off until pressed, hidden if unsupported ---------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const micBtn = $('#mic');
let rec = null;
if (SR) {
  micBtn.hidden = false;
  micBtn.addEventListener('click', () => {
    if (listening) { try { rec.stop(); } catch (e) {} return; }
    rec = new SR();
    rec.lang = 'en-US'; rec.interimResults = false; rec.maxAlternatives = 1;
    rec.onresult = (e) => { const t = e.results[0][0].transcript; listening = false; micBtn.classList.remove('rec'); send(t); };
    rec.onend = () => { listening = false; micBtn.classList.remove('rec'); };
    rec.onerror = () => { listening = false; micBtn.classList.remove('rec'); say('Mic did not catch that.'); };
    try { rec.start(); listening = true; micBtn.classList.add('rec'); say('Listening...'); }
    catch (e) { say('Mic is not available here.'); }
  });
}

/* ---------- orb canvas ---------- */
const cv = $('#stage'), ctx = cv.getContext('2d');
let W = 0, H = 0, DPR = 1;
const NP = 1600, P = [];
for (let i = 0; i < NP; i++) {
  const y = 1 - 2 * (i + .5) / NP, r = Math.sqrt(1 - y * y), th = i * 2.399963;
  P.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r });
}
const LINKS = [];
for (let n = 0; n < NP; n += 4) {
  const a = P[n]; let best = null, bd = 0;
  for (let j = n + 4; j < Math.min(n + 220, NP); j += 4) {
    const d = a.x * P[j].x + a.y * P[j].y + a.z * P[j].z;
    if (d > bd) { bd = d; best = j; }
  }
  if (best !== null && bd > .985) LINKS.push([n, best]);
}
function resize() {
  DPR = Math.min(2, window.devicePixelRatio || 1);
  W = innerWidth; H = innerHeight;
  cv.width = W * DPR; cv.height = H * DPR;
}
addEventListener('resize', resize); resize();
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
let rot = 0;
function frame() {
  ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  ctx.clearRect(0, 0, W, H);
  const cx = W / 2, cy = H * 0.52, R = Math.min(W, H) * 0.42;
  rot += reduced ? 0 : 0.0016;
  const co = Math.cos(rot), si = Math.sin(rot);
  const pr = P.map((p) => {
    const x = p.x * co - p.z * si, z = p.x * si + p.z * co;
    return { sx: cx + x * R, sy: cy + p.y * R * 0.92, z };
  });
  ctx.strokeStyle = 'rgba(63,216,255,0.10)'; ctx.lineWidth = 1;
  for (const [a, b] of LINKS) {
    if (pr[a].z < -0.2 && pr[b].z < -0.2) continue;
    ctx.beginPath(); ctx.moveTo(pr[a].sx, pr[a].sy); ctx.lineTo(pr[b].sx, pr[b].sy); ctx.stroke();
  }
  for (const q of pr) {
    const front = (q.z + 1) / 2;
    ctx.fillStyle = `rgba(63,216,255,${0.06 + front * 0.5})`;
    ctx.beginPath(); ctx.arc(q.sx, q.sy, 0.7 + front * 1.2, 0, TAU); ctx.fill();
  }
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.9);
  g.addColorStop(0, 'rgba(255,176,46,0.10)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  if (listening || speaking) {
    ctx.strokeStyle = `rgba(255,176,46,${0.25 + 0.15 * Math.sin(rot * 60)})`;
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.6, 0, TAU); ctx.stroke();
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

/* ---------- boot ---------- */
getJSON('/api/csrf').then((j) => { csrf = j.token || ''; }).catch(() => {});
refresh();
})();
