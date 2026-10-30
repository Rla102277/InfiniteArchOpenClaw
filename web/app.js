'use strict';

const byId = (id) => document.getElementById(id);
const panelNames = { today: 'Today', apps: 'Applications', agents: 'Agents', tasks: 'Tasks & approvals', skills: 'Skills', recos: 'Recos', schedules: 'Schedules', routines: 'Routines', memory: 'Memory', usage: 'Usage', activity: 'Activity', system: 'System' };
let snapshot = null;
let csrfToken = '';
let refreshPromise = null;
let refreshAgain = false;
let mutationPending = false;
let hasConnected = false;
let noticeTimer;
const moduleState = new Map();   // app id -> { mounted, loading, failed, error, script, style, section }
let activeModule = null;         // { id, view } while an app view is on screen
const MODULE_LOAD_TIMEOUT = 5000;
let themeRuleIndex = null;
let overviewChat = null;
let themeSignature = '';
const viewSignatures = new Map();
const displayText = (value) => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value, null, 2);
const list = (value) => Array.isArray(value) ? value : [];

function element(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = displayText(text);
  if (className) result.className = className;
  return result;
}

function badge(status) {
  const text = displayText(status) || 'unknown';
  const pill = element('span', text.replaceAll('_', ' '), 'badge');
  const positive = ['enabled', 'approved', 'completed', 'succeeded', 'success', 'available', 'ready', 'detected', 'installed', 'connected', 'supported', 'ok'];
  const negative = ['failed', 'error', 'rejected', 'unavailable', 'missing', 'blocked'];
  pill.dataset.tone = positive.includes(text) ? 'positive' : negative.includes(text) ? 'negative' : 'neutral';
  return pill;
}

function button(label, action, className = 'secondary') {
  const control = element('button', label, `button ${className}`);
  control.type = 'button';
  control.addEventListener('click', () => action(control));
  return control;
}

function showError(id, text) {
  const target = byId(id);
  target.textContent = text;
  target.hidden = !text;
}

function announce(text, error = false) {
  clearTimeout(noticeTimer);
  byId('notice').textContent = text;
  byId('notice').dataset.tone = error ? 'negative' : 'positive';
  byId('notice').hidden = false;
  if (!error) noticeTimer = setTimeout(() => { byId('notice').hidden = true; }, 9000);
}

function dateLabel(value) {
  if (!value) return 'Not reported';
  const date = new Date(typeof value === 'number' && value < 100000000000 ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? displayText(value) : date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function detail(label, value) {
  const row = element('div', undefined, 'detail-row');
  row.append(element('dt', label), element('dd', value || 'Not reported'));
  return row;
}

function renderList(id, values, create, emptyText, preserveFocus = true) {
  const container = byId(id);
  const signature = JSON.stringify(values);
  if (viewSignatures.get(id) === signature || (preserveFocus && container.contains(document.activeElement))) return;
  const opened = Array.from(container.querySelectorAll('details[open]'), (item) => item.dataset.key);
  container.replaceChildren(...(values.length ? values.map(create) : [element('p', emptyText, 'empty')]));
  container.querySelectorAll('details').forEach((item) => { item.open = opened.includes(item.dataset.key); });
  viewSignatures.set(id, signature);
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  let data;
  try { data = await response.json(); } catch { throw new Error(`The workspace returned an unreadable response (${response.status}).`); }
  if (!response.ok || data.error || data.ok === false) {
    const error = new Error(displayText(data.error) || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

async function loadToken() {
  const data = await request('/api/csrf');
  if (typeof data.token !== 'string' || !data.token) throw new Error('The workspace did not provide a session token. Refresh and try again.');
  csrfToken = data.token;
}

async function refresh() {
  if (refreshPromise) {
    refreshAgain = true;
    return refreshPromise;
  }
  refreshPromise = (async () => {
    try {
      const data = await request('/api/os/state');
      const state = withFakes(data.ok === true ? data.result : data);
      if (!state || !Array.isArray(state.apps) || !state.brain) throw new Error('The OS state is not available. Check that the OS backend is running.');
      snapshot = state;
      render();
      byId('connection-status').textContent = 'Workspace connected';
      byId('connection-dot').dataset.connected = 'true';
      byId('last-updated').textContent = `Synced ${new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' })} · refreshes every 5s`;
      if (!hasConnected && byId('notice').dataset.connectionError === 'true') {
        byId('notice').hidden = true;
        delete byId('notice').dataset.connectionError;
      }
      hasConnected = true;
      return true;
    } catch (error) {
      byId('connection-status').textContent = snapshot ? 'Connection lost · showing last sync' : 'Workspace unavailable';
      byId('connection-dot').dataset.connected = 'false';
      if (hasConnected || !snapshot) {
        announce(`${error.message} Your unsaved inputs are kept. Retrying every 5 seconds.`, true);
        byId('notice').dataset.connectionError = 'true';
      }
      hasConnected = false;
      return false;
    } finally {
      refreshPromise = null;
      if (refreshAgain) {
        refreshAgain = false;
        queueMicrotask(refresh);
      }
    }
  })();
  return refreshPromise;
}

async function mutate(action, values = {}) {
  if (!csrfToken) await loadToken();
  try {
    const response = await request('/api/os/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Token': csrfToken },
      body: JSON.stringify({ action, ...values })
    });
    if (response.ok !== true) throw new Error('The action was not acknowledged by the workspace.');
    return response.result;
  } catch (error) {
    if (error.status === 403) csrfToken = '';
    throw error;
  }
}

async function run(control, work, errorId) {
  if (mutationPending) return;
  mutationPending = true;
  if (control) { control.disabled = true; control.setAttribute('aria-busy', 'true'); }
  if (errorId) showError(errorId, '');
  try { await work(); } catch (error) {
    if (errorId) showError(errorId, error.message);
    else announce(error.message, true);
  } finally {
    mutationPending = false;
    if (control) { control.disabled = false; control.removeAttribute('aria-busy'); }
  }
}

async function perform(control, action, values, message) {
  await run(control, async () => {
    await mutate(action, values);
    announce(message);
    control.blur();
    await refresh();
  });
}

function luminance(color) {
  const context = document.createElement('canvas').getContext('2d');
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const channels = Array.from(context.getImageData(0, 0, 1, 1).data).slice(0, 3).map((channel) => {
    const normalized = channel / 255;
    return normalized <= .04045 ? normalized / 12.92 : ((normalized + .055) / 1.055) ** 2.4;
  });
  return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722;
}

function applyTheme() {
  const theme = list(snapshot.themes).find((item) => item.id === snapshot.preferences?.theme);
  const signature = JSON.stringify(theme);
  if (!theme?.colors || !document.styleSheets[0] || signature === themeSignature) return;
  const colorNames = { background: '--background', panel: '--panel', text: '--text', muted: '--muted', accent: '--accent' };
  const declarations = [];
  const accepted = {};
  for (const [name, variable] of Object.entries(colorNames)) {
    const value = theme.colors[name];
    if (typeof value === 'string' && /^(#[a-f\d]{3,8}|[a-z]+|(?:rgb|hsl)a?\([\d\s.,%+\-/]+\))$/i.test(value) && CSS.supports('color', value)) {
      declarations.push(`${variable}:${value}`);
      accepted[name] = value;
    }
  }
  if (!declarations.length) return;
  if (accepted.accent) declarations.push(`--accent-text:${luminance(accepted.accent) > .18 ? '#101a18' : '#ffffff'}`);
  if (accepted.background) {
    const light = luminance(accepted.background) > .35;
    declarations.push(`color-scheme:${light ? 'light' : 'dark'}`, `--error:${light ? '#8a2810' : '#ffc2b0'}`);
  }
  const sheet = document.styleSheets[0];
  if (themeRuleIndex !== null) sheet.deleteRule(themeRuleIndex);
  themeRuleIndex = sheet.insertRule(`:root{${declarations.join(';')}}`, sheet.cssRules.length);
  themeSignature = signature;
}


// ---------- module apps (system/contracts/MODULES.md) ----------

function moduleEntries() {
  const modules = snapshot && snapshot.modules && typeof snapshot.modules === 'object' ? snapshot.modules : {};
  const fake = window.__fakeModules && typeof window.__fakeModules === 'object' ? window.__fakeModules : null;
  const source = fake && !Object.keys(modules).length ? fake : modules;
  return Object.entries(source).filter(([id, entry]) => /^[a-z][a-z0-9-]{0,63}$/.test(id) && entry && typeof entry === 'object' && entry.manifest);
}

function moduleEntry(id) {
  return moduleEntries().find(([key]) => key === id)?.[1] || null;
}

function moduleViews(manifest) {
  return list(manifest.views).filter((view) => view && typeof view.id === 'string' && /^[a-z][a-z0-9-]{0,63}$/.test(view.id));
}

function moduleCount(entry, view) {
  if (!view.count) return null;
  const counts = entry.snapshot && entry.snapshot.counts && typeof entry.snapshot.counts === 'object' ? entry.snapshot.counts : {};
  const value = counts[view.count];
  return Number.isFinite(value) ? value : null;
}

function parseAppHash(hash) {
  const match = /^#app\/([a-z][a-z0-9-]{0,63})(?:\/([a-z][a-z0-9-]{0,63}))?$/.exec(hash || '');
  return match ? { id: match[1], view: match[2] || null } : null;
}

async function moduleRequest(id, path, body) {
  const safe = String(path || '').replace(/^\/+/, '');
  if (!/^[a-zA-Z0-9_./-]+$/.test(safe)) throw new Error('Invalid module path.');
  if (body === undefined) return request(`/api/apps/${id}/${safe}`);
  if (!csrfToken) await loadToken();
  try {
    const response = await request(`/api/apps/${id}/${safe}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Token': csrfToken },
      body: JSON.stringify(body || {})
    });
    return response.ok === true && 'result' in response ? response.result : response;
  } catch (error) {
    if (error.status === 403) csrfToken = '';
    throw error;
  }
}

function moduleHost(id) {
  return {
    appId: id,
    get: (path) => moduleRequest(id, path),
    post: (path, body) => moduleRequest(id, path, body || {}),
    action: (payload) => moduleRequest(id, 'action', payload || {}),
    ask: (text, agent) => moduleRequest(id, 'ask', { text, agent: agent || null }),
    el: element,
    button,
    badge,
    announce,
    refresh: () => refresh(),
    navigate: (view) => { location.hash = `#app/${id}/${view}`; },
    os: () => snapshot || {},
    chat: OSChat
  };
}

function moduleSection(id) {
  let section = byId(`module-${id}`);
  if (!section) {
    section = element('section', undefined, 'panel full-width module-host');
    section.id = `module-${id}`;
    section.hidden = true;
    section.setAttribute('aria-label', `${moduleEntry(id)?.manifest?.name || id} app`);
    byId('module-hosts').append(section);
  }
  return section;
}

function moduleErrorCard(section, title, message) {
  section.replaceChildren();
  const card = element('article', undefined, 'entry module-error');
  card.append(element('h3', title), element('p', message, 'error-text'));
  card.append(button('Retry', () => { const id = section.id.replace(/^module-/, ''); moduleState.delete(id); section.replaceChildren(); showModule(id, activeModule?.view || null); }, 'subtle'));
  section.append(card);
}

function loadModuleAssets(id, manifest) {
  const state = moduleState.get(id) || {};
  if (state.loading || state.mounted || state.failed || state.loaded) return;
  if (window.OSModules && window.OSModules[id]) { moduleState.set(id, { ...state, loaded: true }); return; }
  if (manifest.kind === 'agent') {
    // Agent apps share one generic module script served by the shell itself.
    if (window.OSModules && window.OSModules.__agentApp) { window.OSModules[id] = window.OSModules.__agentApp(id); moduleState.set(id, { ...state, loaded: true }); return; }
    if (!document.querySelector('script[data-module="__agent-app"]')) {
      state.loading = true; moduleState.set(id, state);
      if (!document.querySelector('link[data-module="__agent-app"]')) { const link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '/agent-app.css'; link.dataset.module = '__agent-app'; document.head.append(link); }
      const tag = document.createElement('script');
      tag.src = '/agent-app.js'; tag.dataset.module = '__agent-app';
      tag.addEventListener('load', () => {
        moduleEntries().filter(([, entry]) => entry.manifest.kind === 'agent').forEach(([appId]) => {
          if (window.OSModules && window.OSModules.__agentApp) { window.OSModules[appId] = window.OSModules.__agentApp(appId); moduleState.set(appId, { ...(moduleState.get(appId) || {}), loading: false, loaded: true }); }
        });
        renderActiveModule();
      });
      tag.addEventListener('error', () => { moduleState.set(id, { ...(moduleState.get(id) || {}), loading: false, failed: true, error: 'The generic agent-app script could not be loaded from /agent-app.js.' }); renderActiveModule(); });
      document.head.append(tag);
    } else {
      state.loading = true; moduleState.set(id, state);
    }
    return;
  }
  const web = manifest.web && typeof manifest.web === 'object' ? manifest.web : {};
  const script = typeof web.script === 'string' ? web.script.replace(/^\/+/, '') : '';
  if (!script || !/^[a-zA-Z0-9_./-]+$/.test(script)) {
    moduleState.set(id, { ...state, failed: true, error: 'The app manifest names no web script.' });
    return;
  }
  state.loading = true;
  moduleState.set(id, state);
  if (typeof web.style === 'string' && /^[a-zA-Z0-9_./-]+$/.test(web.style.replace(/^\/+/, '')) && !document.querySelector(`link[data-module="${id}"]`)) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `/apps/${id}/${web.style.replace(/^\/+/, '')}`;
    link.dataset.module = id;
    document.head.append(link);
  }
  const tag = document.createElement('script');
  tag.src = `/apps/${id}/${script}`;
  tag.dataset.module = id;
  const fail = (message) => { moduleState.set(id, { ...(moduleState.get(id) || {}), loading: false, failed: true, error: message }); renderActiveModule(); };
  const timer = setTimeout(() => { if (!window.OSModules || !window.OSModules[id]) fail(`The ${manifest.name || id} app did not register within 5 seconds.`); }, MODULE_LOAD_TIMEOUT);
  tag.addEventListener('load', () => {
    clearTimeout(timer);
    if (!window.OSModules || !window.OSModules[id]) { fail(`The ${manifest.name || id} app script loaded but registered nothing.`); return; }
    moduleState.set(id, { ...(moduleState.get(id) || {}), loading: false, loaded: true });
    renderActiveModule();
  });
  tag.addEventListener('error', () => { clearTimeout(timer); fail(`The ${manifest.name || id} app script could not be loaded from /apps/${id}/${script}.`); });
  document.head.append(tag);
}

function showModule(id, view) {
  const entry = moduleEntry(id);
  document.body.classList.add('app-mode');
  byId('module-hosts').querySelectorAll('.module-host').forEach((section) => { section.hidden = section.id !== `module-${id}`; });
  const section = moduleSection(id);
  section.hidden = false;
  if (!entry) {
    activeModule = { id, view };
    moduleErrorCard(section, 'App not found', `No app called "${id}" is reported by the OS. Check Applications.`);
    setModuleCrumb(id, view);
    return;
  }
  const views = moduleViews(entry.manifest);
  const resolved = views.some((item) => item.id === view) ? view : (views[0]?.id || null);
  activeModule = { id, view: resolved };
  if (resolved && resolved !== view) { history.replaceState(null, '', `#app/${id}/${resolved}`); }
  setModuleCrumb(entry.manifest.name || id, views.find((item) => item.id === resolved)?.label || resolved);
  loadModuleAssets(id, entry.manifest);
  renderActiveModule();
}

function hideModules() {
  document.body.classList.remove('app-mode');
  byId('module-hosts').querySelectorAll('.module-host').forEach((section) => { section.hidden = true; });
  activeModule = null;
  const crumb = document.querySelector('.breadcrumb');
  if (crumb) crumb.replaceChildren('Workspace ', Object.assign(element('span', '/'), { ariaHidden: 'true' }), ' Overview');
}

function setModuleCrumb(app, view) {
  const crumb = document.querySelector('.breadcrumb');
  if (!crumb) return;
  const slash = () => { const s = element('span', '/'); s.setAttribute('aria-hidden', 'true'); return s; };
  crumb.replaceChildren('Workspace ', slash(), ` ${displayText(app)} `, slash(), ` ${displayText(view) || ''}`);
}

function renderActiveModule() {
  if (!activeModule) return;
  const { id, view } = activeModule;
  const entry = moduleEntry(id);
  const section = moduleSection(id);
  const state = moduleState.get(id) || {};
  if (!entry) return;
  if (entry.snapshot && entry.snapshot.error) { moduleErrorCard(section, `${entry.manifest.name || id} is not responding`, displayText(entry.snapshot.error)); return; }
  if (state.failed) { moduleErrorCard(section, `${entry.manifest.name || id} could not load`, state.error || 'Unknown error.'); return; }
  const module = window.OSModules && window.OSModules[id];
  if (!module) {
    if (!section.querySelector('.module-loading')) { section.replaceChildren(element('p', `Loading ${entry.manifest.name || id}…`, 'empty module-loading')); }
    return;
  }
  try {
    if (!state.mounted) {
      section.replaceChildren();
      if (typeof module.mount === 'function') module.mount(section, moduleHost(id));
      moduleState.set(id, { ...state, mounted: true });
    }
    if (typeof module.render === 'function') module.render(entry.snapshot || {}, view);
  } catch (error) {
    moduleErrorCard(section, `${entry.manifest.name || id} failed while rendering`, error && error.message ? error.message : String(error));
  }
}

function renderModuleNav() {
  const nav = byId('apps-navigation');
  const entries = moduleEntries();
  byId('apps-nav-label').hidden = !entries.length;
  const signature = JSON.stringify(entries.map(([id, entry]) => [id, entry.manifest, entry.snapshot?.counts || null]));
  if (nav.dataset.signature !== signature) {
    nav.replaceChildren(...entries.flatMap(([id, entry]) => {
      const manifest = entry.manifest;
      const views = moduleViews(manifest);
      const head = element('a', undefined, 'app-nav-head');
      head.href = `#app/${id}/${views[0]?.id || ''}`.replace(/\/$/, '');
      head.dataset.app = id;
      const icon = element('span', displayText(manifest.icon) || (manifest.name || id).slice(0, 1).toUpperCase());
      icon.setAttribute('aria-hidden', 'true');
      head.append(icon, element('span', manifest.name || id));
      if (entry.snapshot && entry.snapshot.error) head.append(element('span', '!', 'nav-count nav-warn'));
      return [head, ...views.map((view) => {
        const link = element('a', undefined, 'app-nav-view');
        link.href = `#app/${id}/${view.id}`;
        link.dataset.app = id;
        link.dataset.view = view.id;
        link.append(element('span', '·'), element('span', view.label || view.id));
        link.firstChild.setAttribute('aria-hidden', 'true');
        const count = moduleCount(entry, view);
        if (count !== null) link.append(element('span', String(count), 'nav-count'));
        return link;
      })];
    }));
    nav.dataset.signature = signature;
  }
  nav.querySelectorAll('a').forEach((link) => {
    const current = activeModule && link.dataset.app === activeModule.id && (link.dataset.view ? link.dataset.view === activeModule.view : true);
    if (current) link.setAttribute('aria-current', 'location'); else link.removeAttribute('aria-current');
  });
  if (activeModule) document.querySelectorAll('#navigation a').forEach((item) => item.removeAttribute('aria-current'));
}

function renderModuleAskLinks() {
  const target = byId('module-ask-links');
  const entries = moduleEntries().filter(([, entry]) => entry.manifest.kind !== 'agent' && moduleViews(entry.manifest).some((view) => view.id === 'ask' || view.id === 'chat'));
  target.hidden = !entries.length;
  const signature = entries.map(([id]) => id).join(',');
  if (target.dataset.signature === signature) return;
  target.replaceChildren(...entries.map(([id, entry]) => {
    const view = moduleViews(entry.manifest).find((item) => item.id === 'ask' || item.id === 'chat');
    const link = element('a', `Chat with the ${entry.manifest.name || id} →`);
    link.href = `#app/${id}/${view ? view.id : 'ask'}`;
    return link;
  }));
  target.dataset.signature = signature;
}

function moduleAppCard(id, entry) {
  const manifest = entry.manifest;
  const card = element('article', undefined, 'app-card module-card');
  const top = element('div', undefined, 'card-top');
  top.append(element('span', displayText(manifest.icon) || (manifest.name || id).slice(0, 1).toUpperCase(), 'app-icon'), badge(entry.snapshot && entry.snapshot.error ? 'error' : (manifest.kind === 'agent' ? 'agent' : 'enabled')));
  card.append(top, element('h3', manifest.name || id), element('p', manifest.description || 'An app that runs inside the OS.', 'card-description'));
  const chips = element('div', undefined, 'chips');
  moduleViews(manifest).forEach((view) => {
    const link = element('a', view.label || view.id, 'chip chip-link');
    link.href = `#app/${id}/${view.id}`;
    const count = moduleCount(entry, view);
    if (count) link.textContent += ` ${count}`;
    chips.append(link);
  });
  const where = manifest.kind === 'agent' ? `agent app · hq/os/apps/${id}` : `apps/${id} · v${displayText(manifest.version) || '1'}`;
  const counts = entry.snapshot && entry.snapshot.counts && typeof entry.snapshot.counts === 'object' ? entry.snapshot.counts : {};
  const extra = manifest.kind === 'agent' ? ` · ${Number(counts.records) || 0} records${entry.snapshot && entry.snapshot.last_run ? ' · last run ' + chatTimeLabel(entry.snapshot.last_run) : ''}` : '';
  card.append(chips, element('small', `${where}${extra}${entry.snapshot && entry.snapshot.error ? ' · ' + displayText(entry.snapshot.error).slice(0, 120) : ''}`, 'muted'));
  const actions = element('div', undefined, 'card-actions');
  const first = moduleViews(manifest)[0];
  actions.append(button('Open ↗', () => { location.hash = `#app/${id}/${first ? first.id : ''}`.replace(/\/$/, ''); }, 'secondary'));
  card.append(actions);
  return card;
}

function ago(value) {
  if (!value) return 'never';
  const then = new Date(value).getTime();
  if (!Number.isFinite(then)) return String(value);
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

// Is the OS actually working: the worker, the GitHub sync, and every app's last run.
function renderHealth() {
  const box = byId('overview-health');
  if (!box) return;
  const tasks = list(snapshot.tasks);
  const parts = [];
  const sync = tasks.find((t) => t.operation === 'brain.sync' && ['succeeded', 'failed'].includes(t.status));
  if (!sync) parts.push(['warn', 'GitHub sync: has not run yet (it runs every 15 min while the OS is up)']);
  else if (sync.status === 'failed') parts.push(['bad', `GitHub sync failed ${ago(sync.updated)}: ${String(sync.error || '').slice(0, 160)}`]);
  else {
    let detail = '';
    try { const r = JSON.parse(sync.result || '{}'); detail = r.pushed ? ' · pushed' : (r.changed ? ' · committed' : ' · nothing new'); } catch (e) { detail = ''; }
    parts.push(['ok', `GitHub sync ${ago(sync.updated)}${detail}`]);
  }
  for (const [id, entry] of Object.entries(snapshot.modules || {})) {
    const manifest = entry.manifest || {};
    if (manifest.kind !== 'agent') continue;
    const last = entry.snapshot && entry.snapshot.last_run;
    const name = manifest.name || id;
    if (!last) parts.push(['warn', `${name}: not run yet`]);
    else if (last.error) parts.push(['bad', `${name}: last run failed ${ago(last.finished)}`]);
    else {
      const ch = (last.changes && (last.changes.counts || last.changes)) || {};
      parts.push(['ok', `${name} ${ago(last.finished)}: ${ch.new ?? 0} new`]);
    }
  }
  const telegram = list(snapshot.connectors).find((c) => c.id === 'telegram');
  if (telegram) {
    const ok = telegram.enabled !== false && telegram.status && telegram.status.ok;
    parts.push([ok ? 'ok' : 'warn', ok ? 'Telegram connected' : 'Telegram: needs setup →', ok ? null : '#connectors']);
  }
  box.replaceChildren(...parts.map(([level, text, href]) => {
    const item = element(href ? 'a' : 'span', text, `health-item health-${level}`);
    if (href) item.href = href;
    return item;
  }));
}

function renderOverview(curatorSnap, darkroomSnap) {
  renderHealth();
  const today = curatorSnap.today && typeof curatorSnap.today === 'object' ? curatorSnap.today : {};
  const top = list(today.top).slice(0, 3);
  byId('overview-built').textContent = today.built ? `HQ built ${today.built}` : '';
  renderList('overview-top3', top.length ? top : [{ id: 'none', text: moduleEntry('curator') ? 'Nothing ranked yet. Run the morning routine in the Curator.' : 'The Curator app is not mounted.', status: 'empty' }], (card) => {
    const article = element('article', undefined, 'entry');
    const heading = element('div', undefined, 'entry-heading');
    heading.append(badge(card.status === 'top' ? 'now' : card.status), element('p', card.text));
    article.append(heading);
    return article;
  }, 'Nothing ranked yet.');
  const open = Number(curatorSnap.counts?.inbox_open) || 0;
  byId('overview-inbox-link').textContent = open ? `Inbox: ${open} waiting →` : 'Inbox →';
  const status = darkroomSnap.status && typeof darkroomSnap.status === 'object' ? darkroomSnap.status : null;
  const dkApprovals = Number(darkroomSnap.counts?.approvals) || 0;
  let darkroomText = 'Darkroom →';
  if (status) {
    const running = status.reachable ? 'running' : (status.cli ? 'not running' : 'not built');
    darkroomText = `Darkroom: ${running}${dkApprovals ? ` · ${dkApprovals} to approve` : ''} →`;
  }
  byId('overview-darkroom-link').textContent = darkroomText;
  const roles = list(curatorSnap.agents && curatorSnap.agents.roles);
  const signature = roles.map((r) => r.id).join(',');
  if (!overviewChat && moduleEntry('curator')) {
    overviewChat = OSChat.mount(byId('overview-chat'), { app: null, agent: null, agents: roles, compact: true, title: 'Curator', placeholder: 'Which open ruling should I settle first and why? Or drop a photo in.' });
    overviewChat.container.dataset.signature = signature;
  } else if (overviewChat && overviewChat.container.dataset.signature !== signature) {
    overviewChat.setAgents(roles);
    overviewChat.container.dataset.signature = signature;
  }
}

function miniMarkdown(text) {
  const container = element('div', undefined, 'answer-body');
  String(text || '').split(/\n{2,}/).forEach((block) => {
    const lines = block.split('\n');
    if (lines.every((line) => /^\s*[-*] /.test(line))) {
      const ul = element('ul');
      lines.forEach((line) => ul.append(element('li', line.replace(/^\s*[-*] /, ''))));
      container.append(ul);
    } else if (/^#{1,3} /.test(lines[0])) {
      container.append(element('h4', lines[0].replace(/^#{1,3} /, '')));
      if (lines.length > 1) container.append(element('p', lines.slice(1).join(' ')));
    } else {
      container.append(element('p', block));
    }
  });
  return container;
}



// ---------- OSChat: the shared conversation component ----------
// Threads live in the brain (hq/os/chats, via /api/chats and /api/chat). Images are sent as
// base64 and kept in memory for this page session; older turns show a photo chip (the server
// never serves hq/ files). Mount with: OSChat.mount(container, { app, agent, agents, compact, title })
const chatFakes = () => (window.__fakeChats && typeof window.__fakeChats === 'object') ? window.__fakeChats : null;
const sentImages = new Map(); // "<thread>:<index>" -> dataURL, for turns sent in this session

async function chatRequest(path, body) {
  const fake = chatFakes();
  if (fake && typeof fake.request === 'function') return fake.request(path, body);
  if (body === undefined) return request(path);
  if (!csrfToken) await loadToken();
  try {
    const response = await request(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Token': csrfToken }, body: JSON.stringify(body) });
    return response && response.ok === true && 'result' in response ? response.result : response;
  } catch (error) {
    if (error.status === 403) csrfToken = '';
    throw error;
  }
}

function chatMarkdown(text) {
  const box = element('div', undefined, 'chat-md');
  const lines = String(text || '').split('\n');
  let i = 0; let listNode = null;
  const inline = (parent, s) => {
    for (const part of s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)) {
      if (!part) continue;
      if (part.startsWith('**') && part.endsWith('**')) parent.append(element('strong', part.slice(2, -2)));
      else if (part.startsWith('`') && part.endsWith('`')) parent.append(element('code', part.slice(1, -1)));
      else parent.append(document.createTextNode(part));
    }
  };
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) { const buf = []; i++; while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++]); i++; listNode = null; box.append(element('pre', buf.join('\n'))); continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { listNode = null; const node = element('h' + Math.min(h[1].length + 2, 5)); inline(node, h[2]); box.append(node); i++; continue; }
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/);
    if (li) { if (!listNode) { listNode = element('ul'); box.append(listNode); } const item = element('li'); inline(item, li[1]); listNode.append(item); i++; continue; }
    if (!line.trim()) { listNode = null; i++; continue; }
    listNode = null; const para = element('p'); inline(para, line); box.append(para); i++;
  }
  return box;
}

function chatTimeLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => { const result = String(reader.result || ''); resolve(result.slice(result.indexOf(',') + 1)); };
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

const OSChat = {
  mount(container, options = {}) {
    const opts = { app: options.app || null, agent: options.agent || null, agents: list(options.agents), compact: !!options.compact, title: options.title || null, placeholder: options.placeholder || 'Ask, or drop a photo in and ask about it.' };
    const state = { thread: null, threads: [], turns: [], pending: [], busy: false, title: '' };
    const storageKey = `oschat:${opts.app || 'os'}:${opts.compact ? 'compact' : 'full'}`;
    container.classList.add('chat', opts.compact ? 'chat-compact' : 'chat-full');
    container.replaceChildren();

    const side = element('aside', undefined, 'chat-threads');
    const threadList = element('div', undefined, 'chat-thread-list');
    const newButton = button('+ New thread', () => openThread(null), 'subtle');
    side.append(newButton, threadList);
    const main = element('div', undefined, 'chat-main');
    const header = element('div', undefined, 'chat-header');
    const titleNode = element('h3', opts.title || 'New thread');
    const headerRight = element('div', undefined, 'chat-header-right');
    header.append(titleNode, headerRight);
    const log = element('div', undefined, 'chat-log');
    log.setAttribute('aria-live', 'polite');
    const composer = element('form', undefined, 'chat-composer');
    const drop = element('div', undefined, 'chat-drop');
    const previews = element('div', undefined, 'chat-previews');
    const textarea = element('textarea');
    textarea.rows = opts.compact ? 2 : 3;
    textarea.placeholder = opts.placeholder;
    textarea.maxLength = 20000;
    textarea.setAttribute('aria-label', 'Message');
    const row = element('div', undefined, 'chat-row');
    const picker = element('input');
    picker.type = 'file'; picker.accept = 'image/jpeg,image/png'; picker.multiple = true; picker.hidden = true;
    const attach = button('Photo', () => picker.click(), 'subtle');
    attach.title = 'Attach a JPEG or PNG, up to 5 MB each';
    const agentSelect = element('select');
    agentSelect.setAttribute('aria-label', 'Agent');
    const refreshAgents = (agents) => {
      const current = agentSelect.value;
      agentSelect.replaceChildren(new Option('Auto (Operator routes)', 'auto'), ...list(agents).map((r) => new Option(r.name || r.id, r.id)));
      if ([...agentSelect.options].some((o) => o.value === current)) agentSelect.value = current;
      agentSelect.hidden = !list(agents).length;
    };
    refreshAgents(opts.agents);
    if (opts.agent && [...agentSelect.options].some((o) => o.value === opts.agent)) agentSelect.value = opts.agent;
    const modelSelect = element('select', undefined, 'chat-model');
    modelSelect.setAttribute('aria-label', 'Model');
    const modelKey = `oschat-model:${opts.app || 'os'}`;
    const setModels = () => {
      const models = modelOptions();
      const signature = models.map((m) => `${m.ref}:${m.configured}`).join(',');
      if (modelSelect.dataset.signature === signature) return;
      let wanted = modelSelect.value;
      if (!wanted) { try { wanted = localStorage.getItem(modelKey) || ''; } catch (e) { wanted = ''; } }
      modelSelect.replaceChildren(...models.map((m) => {
        const option = new Option(`${m.label || m.ref}${m.configured ? '' : ' (needs key)'}`, m.ref);
        option.disabled = !m.configured;
        return option;
      }));
      const usable = models.filter((m) => m.configured).map((m) => m.ref);
      modelSelect.value = usable.includes(wanted) ? wanted : defaultModelRef();
      modelSelect.hidden = !models.length;
      modelSelect.dataset.signature = signature;
    };
    modelSelect.addEventListener('change', () => { try { localStorage.setItem(modelKey, modelSelect.value); } catch (e) { /* optional */ } });
    setModels();
    const working = element('span', '', 'chat-working');
    working.hidden = true;
    const send = element('button', 'Send', 'button primary');
    send.type = 'submit';
    const extras = element('span', undefined, 'chat-extras');
    row.append(attach, agentSelect, modelSelect, working, extras, send);
    const errorNode = element('p', '', 'error-text chat-error');
    errorNode.hidden = true;
    drop.append(previews, textarea);
    composer.append(drop, row, errorNode, picker);
    main.append(header, log, composer);
    if (!opts.compact) container.append(side);
    container.append(main);

    function setError(message) { errorNode.textContent = message || ''; errorNode.hidden = !message; }
    function setBusy(flag, label) { state.busy = flag; send.disabled = flag; working.hidden = !flag; working.textContent = flag ? (label || 'Working…') : ''; }

    function renderPreviews() {
      previews.replaceChildren(...state.pending.map((item, index) => {
        const chip = element('span', undefined, 'chat-preview');
        const img = element('img'); img.src = item.dataUrl; img.alt = item.name;
        const remove = button('×', () => { state.pending.splice(index, 1); renderPreviews(); }, 'icon-button subtle');
        remove.setAttribute('aria-label', `Remove ${item.name}`);
        chip.append(img, element('small', item.name), remove);
        return chip;
      }));
      previews.hidden = !state.pending.length;
    }
    async function addFiles(files) {
      for (const file of files) {
        if (!['image/jpeg', 'image/png'].includes(file.type)) { setError(`${file.name}: only JPEG or PNG.`); continue; }
        if (file.size > 5 * 1024 * 1024) { setError(`${file.name} is over 5 MB. Export a smaller JPEG (2048 px is plenty for a grade).`); continue; }
        const data = await fileToBase64(file);
        state.pending.push({ name: file.name, mime: file.type, data, dataUrl: `data:${file.type};base64,${data}` });
      }
      renderPreviews();
    }
    picker.addEventListener('change', () => { addFiles([...picker.files]); picker.value = ''; });
    ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.add('dragging'); }));
    ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (event) => { event.preventDefault(); drop.classList.remove('dragging'); }));
    drop.addEventListener('drop', (event) => { if (event.dataTransfer && event.dataTransfer.files) addFiles([...event.dataTransfer.files]); });
    textarea.addEventListener('paste', (event) => {
      const files = [...(event.clipboardData && event.clipboardData.files ? event.clipboardData.files : [])];
      if (files.length) { event.preventDefault(); addFiles(files); }
    });

    function turnCard(turn, index) {
      const card = element('article', undefined, `chat-turn chat-${turn.role === 'user' ? 'user' : 'assistant'}`);
      const head = element('div', undefined, 'chat-turn-head');
      head.append(element('strong', turn.who || (turn.role === 'user' ? 'You' : 'Agent')), element('small', chatTimeLabel(turn.time), 'muted'));
      card.append(head);
      const images = list(turn.images);
      if (images.length) {
        const strip = element('div', undefined, 'chat-images');
        images.forEach((image, n) => {
          const key = `${state.thread}:${index}:${n}`;
          const dataUrl = sentImages.get(key) || (typeof image === 'object' && image && image.dataUrl) || null;
          if (dataUrl) { const img = element('img'); img.src = dataUrl; img.alt = typeof image === 'string' ? image : (image.name || 'photo'); strip.append(img); }
          else { const name = typeof image === 'string' ? image.split('/').pop() : (image && image.name) || 'photo'; strip.append(element('span', `photo · ${name}`, 'chip')); }
        });
        card.append(strip);
      }
      if (turn.text) card.append(turn.role === 'user' ? element('p', turn.text, 'chat-user-text') : chatMarkdown(turn.text));
      const calls = list(turn.tool_calls);
      const usage = turn.usage || {};
      if (turn.role !== 'user' && (calls.length || usage.input || usage.output)) {
        const details = element('details', undefined, 'chat-trace');
        details.append(element('summary', `${calls.length} tool call(s)${calls.length ? ': ' + calls.map((c) => c.name).join(', ') : ''} · ${usage.input || 0} in / ${usage.output || 0} out`));
        for (const call of calls) {
          const block = element('div', undefined, 'chat-tool-call');
          block.append(element('strong', call.name), element('pre', JSON.stringify(call.args || {}, null, 1)), element('pre', String(call.result || '').slice(0, 2000)));
          details.append(block);
        }
        card.append(details);
      }
      if (turn.error) card.append(element('p', turn.error, 'error-text'));
      return card;
    }
    function renderLog() {
      log.replaceChildren(...state.turns.map(turnCard));
      if (!state.turns.length) log.append(element('p', opts.app ? 'Ask this app anything about what it found, or tell it what to look for.' : 'A new thread. Ask, or drop a photo in.', 'empty'));
      log.scrollTop = log.scrollHeight;
      titleNode.textContent = state.title || opts.title || (state.thread ? 'Thread' : 'New thread');
    }
    function renderThreads() {
      if (opts.compact) return;
      threadList.replaceChildren(...state.threads.map((thread) => {
        const item = element('button', undefined, 'chat-thread' + (thread.id === state.thread ? ' current' : ''));
        item.type = 'button';
        item.append(element('span', thread.title || thread.id), element('small', `${thread.agent ? thread.agent + ' · ' : ''}${chatTimeLabel(thread.updated)}`, 'muted'));
        item.addEventListener('click', () => openThread(thread.id));
        return item;
      }));
      if (!state.threads.length) threadList.append(element('p', 'No threads yet.', 'empty'));
    }
    async function loadThreads() {
      try {
        const query = opts.app ? `?app=${encodeURIComponent(opts.app)}` : '';
        const data = await chatRequest(`/api/chats${query}`);
        state.threads = list(data && data.ok === true ? data.result : (Array.isArray(data) ? data : (data && data.chats) || []));
        renderThreads();
        return state.threads;
      } catch (error) { state.threads = []; renderThreads(); return []; }
    }
    async function openThread(id) {
      state.thread = id; state.turns = []; state.title = '';
      setError('');
      try { if (id) localStorage.setItem(storageKey, id); else localStorage.removeItem(storageKey); } catch (e) { /* storage is optional */ }
      if (id) {
        try {
          const data = await chatRequest(`/api/chats/${encodeURIComponent(id)}`);
          const thread = data && data.ok === true ? data.result : data;
          state.turns = list(thread && thread.turns).map((turn) => ({ ...turn }));
          state.title = (thread && thread.title) || '';
          if (thread && thread.agent && [...agentSelect.options].some((o) => o.value === thread.agent)) agentSelect.value = thread.agent;
        } catch (error) { setError(error.message || String(error)); }
      }
      renderThreads(); renderLog();
    }
    async function submit() {
      const text = textarea.value.trim();
      if ((!text && !state.pending.length) || state.busy) return;
      setError('');
      const agent = agentSelect.hidden || agentSelect.value === 'auto' ? null : agentSelect.value;
      const body = { thread: state.thread || null, text, agent, app: opts.app || null };
      if (!modelSelect.hidden && modelSelect.value) body.model = modelSelect.value;
      if (state.pending.length) body.images = state.pending.map((item) => ({ name: item.name, mime: item.mime, data_base64: item.data }));
      const userIndex = state.turns.length;
      const userTurn = { role: 'user', who: 'You', time: new Date().toISOString(), text, images: state.pending.map((item) => ({ name: item.name, dataUrl: item.dataUrl })) };
      state.turns.push(userTurn);
      const pendingImages = state.pending.slice();
      state.pending = []; renderPreviews(); textarea.value = '';
      renderLog();
      setBusy(true, agent ? `Asking ${agent}…` : (opts.app ? 'Working…' : 'Asking the Operator…'));
      try {
        const result = await chatRequest('/api/chat', body);
        const payload = result && result.ok === true ? result.result : result;
        if (payload && payload.thread && payload.thread !== state.thread) {
          state.thread = payload.thread;
          try { localStorage.setItem(storageKey, state.thread); } catch (e) { /* optional */ }
        }
        pendingImages.forEach((item, n) => sentImages.set(`${state.thread}:${userIndex}:${n}`, item.dataUrl));
        const message = (payload && payload.message) || {};
        state.turns.push({ role: 'assistant', who: message.who || agent || (opts.app ? opts.app : 'Curator'), time: message.time || new Date().toISOString(), text: message.text || message.answer || 'No answer.', tool_calls: message.tool_calls, usage: message.usage });
        if (payload && payload.title) state.title = payload.title;
        if (!state.title) state.title = text.slice(0, 60);
        renderLog();
        loadThreads();
        if (typeof opts.onReply === 'function') opts.onReply(payload);
        refresh();
      } catch (error) {
        state.turns.push({ role: 'assistant', who: 'OS', time: new Date().toISOString(), text: '', error: error.message || String(error) });
        renderLog();
      } finally { setBusy(false); }
    }
    composer.addEventListener('submit', (event) => { event.preventDefault(); submit(); });
    textarea.addEventListener('keydown', (event) => { if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') { event.preventDefault(); submit(); } });

    const api = {
      container,
      focus: () => textarea.focus(),
      setText: (value) => { textarea.value = value || ''; textarea.focus(); },
      getText: () => textarea.value,
      setAgent: (id) => { if ([...agentSelect.options].some((o) => o.value === id)) agentSelect.value = id; },
      setAgents: refreshAgents,
      setModels,
      model: () => modelSelect.value,
      addExtra: (node) => extras.append(node),
      addHeaderAction: (node) => headerRight.append(node),
      open: openThread,
      send: (text, agent) => { textarea.value = text; if (agent) api.setAgent(agent); return submit(); },
      reload: async () => { await loadThreads(); if (state.thread) await openThread(state.thread); },
      threadId: () => state.thread,
      destroy: () => { chatInstances.delete(api); container.replaceChildren(); }
    };
    chatInstances.add(api);
    (async () => {
      const threads = await loadThreads();
      let remembered = null;
      try { remembered = localStorage.getItem(storageKey); } catch (e) { /* optional */ }
      const fromHash = /[?&]t=([a-zA-Z0-9_-]{1,80})/.exec(location.hash || '');
      const wanted = (fromHash && fromHash[1]) || remembered;
      const exists = wanted && threads.some((thread) => thread.id === wanted);
      if (exists) await openThread(wanted);
      else if (opts.compact && threads.length) await openThread(threads[0].id);
      else renderLog();
    })();
    return api;
  }
};
window.OSChat = OSChat;

// ---------- connectors, models and activity (system/contracts/CONNECTORS.md) ----------
// The backend fills snapshot.connectors, snapshot.models and snapshot.activity. Until it does,
// window.__fakeOS (set only by tests) stands in; in production it is undefined and unused.
const osFakes = () => (window.__fakeOS && typeof window.__fakeOS === 'object') ? window.__fakeOS : null;

function withFakes(state) {
  const fake = osFakes();
  if (!fake || !state) return state;
  for (const key of ['connectors', 'models', 'activity']) {
    if (!Array.isArray(state[key]) && Array.isArray(fake[key])) state[key] = fake[key];
  }
  return state;
}

async function osPost(path, body) {
  const fake = osFakes();
  if (fake && typeof fake.post === 'function') return fake.post(path, body);
  if (!csrfToken) await loadToken();
  try {
    const response = await request(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-HQ-Token': csrfToken }, body: JSON.stringify(body || {}) });
    return response && response.ok === true && 'result' in response ? response.result : response;
  } catch (error) {
    if (error.status === 403) csrfToken = '';
    throw error;
  }
}

const saveSecret = (name, value) => osPost('/api/secrets', { name, value });
const connectorOp = (body) => osPost('/api/connectors', body);

const CONNECTOR_HINTS = {
  gmail: { lines: ['Needs your Gmail address (set in the server config) and an App password: Google account, Security, 2-Step Verification on, then App passwords.'], link: ['Make an app password', 'https://myaccount.google.com/apppasswords'] },
  telegram: { lines: ['1. On your phone, message @BotFather, send /newbot, and copy the token it gives you.', '2. Paste it below and Save.', '3. Open your new bot in Telegram and send /start. The OS answers with your user id the first time; add it to the allow list and you are connected.'] },
  calendar: { lines: ['Nothing to set up on the Mac. The first time a bot adds an event, macOS asks whether the OS may control Calendar: say OK. Off the Mac, events are saved as .ics files instead.'] },
  instagram: { lines: ['Drafts only for now: posts land on the Posts board and on your phone. Publish from Instagram yourself.'] },
  site: { lines: ['Drafts only: updates land in hq/os/drafts/site/ for the site\'s own pipeline.'] },
};

function connectorTone(c) {
  if (c.enabled === false) return 'off';
  const status = c.status && typeof c.status === 'object' ? c.status : {};
  if (status.ok) return 'ok';
  return list(status.needs).length ? 'warn' : 'bad';
}

// Settings that are not secrets (an email address, a chat id) are saved into the connector's
// own settings in hq/os/connectors.json; everything else goes to the Keychain.
const PLAIN_SETTINGS = { address: 'you@gmail.com', chat_id: 'filled in by your first /start', calendar: 'calendar name' };

function settingRow(connectorId, name) {
  const row = element('form', undefined, 'secret-row');
  const input = element('input');
  input.type = name === 'address' ? 'email' : 'text'; input.placeholder = PLAIN_SETTINGS[name] || name; input.setAttribute('aria-label', name);
  const save = element('button', 'Save', 'button secondary'); save.type = 'submit';
  const state = element('small', '', 'muted');
  row.append(input, save, state);
  row.addEventListener('submit', (event) => {
    event.preventDefault();
    const value = input.value.trim();
    if (!value) { input.focus(); return; }
    run(save, async () => {
      await connectorOp({ op: 'update', id: connectorId, patch: { [name]: value } });
      state.textContent = 'saved';
      announce(`${name} saved.`);
      await refresh();
    });
  });
  return row;
}

function secretRow(name, label) {
  const row = element('form', undefined, 'secret-row');
  const input = element('input');
  input.type = 'password'; input.autocomplete = 'off'; input.placeholder = label || name; input.setAttribute('aria-label', label || name);
  const save = element('button', 'Save', 'button secondary'); save.type = 'submit';
  const state = element('small', '', 'muted');
  row.append(input, save, state);
  row.addEventListener('submit', (event) => {
    event.preventDefault();
    const value = input.value;
    if (!value) { input.focus(); return; }
    run(save, async () => {
      const result = await saveSecret(name, value);
      input.value = '';
      state.textContent = result && result.present === false ? 'not saved' : 'saved';
      announce(`${label || name} saved to the Keychain.`);
      await refresh();
    });
  });
  return row;
}

function connectorCard(c) {
  const tone = connectorTone(c);
  const card = element('article', undefined, `app-card connector-card connector-${tone}`);
  const top = element('div', undefined, 'card-top');
  top.append(element('span', (c.name || c.id).slice(0, 1).toUpperCase(), 'app-icon'), badge(c.kind === 'mcp' ? 'mcp' : 'built in'));
  card.append(top, element('h3', c.name || c.id));
  if (c.description) card.append(element('p', c.description, 'card-description'));
  const status = c.status && typeof c.status === 'object' ? c.status : {};
  const line = element('p', undefined, `connector-status status-${tone}`);
  line.textContent = c.enabled === false ? 'Off' : (status.ok ? `Connected${status.detail ? ': ' + status.detail : ''}` : (status.detail || (list(status.needs).length ? 'Needs setup' : 'Not working')));
  card.append(line);
  const hint = CONNECTOR_HINTS[c.id];
  if (hint && !status.ok) {
    const box = element('div', undefined, 'connector-hint');
    hint.lines.forEach((text) => box.append(element('p', text)));
    if (hint.link) { const a = element('a', hint.link[0] + ' ↗'); a.href = hint.link[1]; a.target = '_blank'; a.rel = 'noopener noreferrer'; box.append(a); }
    card.append(box);
  }
  for (const name of list(status.needs)) {
    card.append(element('small', name.replace(/-/g, ' '), 'field-label'), name in PLAIN_SETTINGS ? settingRow(c.id, name) : secretRow(name, name));
  }
  const tools = list(c.tools);
  if (tools.length) {
    const details = element('details', undefined, 'connector-tools');
    details.dataset.key = `tools-${c.id}`;
    details.append(element('summary', `Tools (${tools.length})`));
    const ul = element('ul');
    tools.forEach((tool) => {
      const li = element('li');
      li.append(element('code', `${c.id}.${tool.name}`));
      if (tool.action) li.append(' ', badge('acts'));
      if (tool.description) li.append(element('small', ` ${tool.description}`, 'muted'));
      ul.append(li);
    });
    details.append(ul);
    card.append(details);
  }
  const result = element('p', '', 'connector-result muted');
  result.hidden = true;
  const actions = element('div', undefined, 'card-actions');
  const toggle = element('label', undefined, 'checkbox-row connector-toggle');
  const box = element('input'); box.type = 'checkbox'; box.checked = c.enabled !== false;
  box.addEventListener('change', () => run(null, async () => {
    await connectorOp({ op: 'update', id: c.id, patch: { enabled: box.checked } });
    announce(`${c.name || c.id} ${box.checked ? 'on' : 'off'}.`);
    await refresh();
  }));
  toggle.append(box, element('span', 'Enabled'));
  actions.append(toggle, button('Test', (control) => run(control, async () => {
    result.hidden = false; result.textContent = 'Testing…';
    const out = await connectorOp({ op: 'test', id: c.id });
    result.textContent = `${out && out.ok ? 'Works' : 'Failed'}${out && out.detail ? ': ' + out.detail : ''}${out && Array.isArray(out.tools) ? ` · ${out.tools.length} tools` : ''}`;
    result.dataset.tone = out && out.ok ? 'positive' : 'negative';
    await refresh();
  }), 'secondary'));
  if (c.kind === 'mcp') actions.append(button('Remove', (control) => {
    if (!window.confirm(`Remove ${c.name || c.id}? Its entry leaves hq/os/connectors.json; saved keys stay in the Keychain.`)) return;
    run(control, async () => { await connectorOp({ op: 'remove_mcp', id: c.id }); announce(`${c.name || c.id} removed.`); await refresh(); });
  }, 'subtle'));
  card.append(actions, result);
  return card;
}

const PROVIDERS = [
  { id: 'anthropic', label: 'Anthropic (Claude)', key: true, hint: 'console.anthropic.com, API keys' },
  { id: 'openai', label: 'OpenAI (GPT)', key: true, hint: 'platform.openai.com, API keys' },
  { id: 'gemini', label: 'Google Gemini', key: true, hint: 'aistudio.google.com, Get API key' },
  { id: 'openrouter', label: 'OpenRouter (any model)', key: true, hint: 'openrouter.ai, Keys' },
  { id: 'ollama', label: 'Ollama (local)', key: false, hint: 'Runs on this Mac at 127.0.0.1:11434; no key' },
];

function providerCard(p) {
  const models = list(snapshot.models).filter((m) => m.provider === p.id);
  const configured = models.some((m) => m.configured);
  const card = element('article', undefined, 'entry provider-row');
  const head = element('div', undefined, 'entry-heading');
  head.append(element('h4', p.label), badge(configured ? 'ok' : (p.key ? 'needs key' : 'not running')));
  card.append(head, element('small', p.hint, 'muted'));
  if (p.key) card.append(secretRow(p.id, `${p.label} API key`));
  else card.append(element('p', configured ? `Answered: ${models.filter((m) => m.configured).length} local model(s).` : 'Not answering. Start Ollama and pull a model (ollama pull qwen3:8b).', 'field-hint'));
  return card;
}

function renderConnectors() {
  const connectors = list(snapshot.connectors);
  renderList('connector-list', connectors, connectorCard, snapshot.connectors === undefined ? 'The connectors service has not reported yet.' : 'No connectors yet. Add an MCP server below.');
  const needing = connectors.filter((c) => c.enabled !== false && !(c.status && c.status.ok)).length;
  byId('nav-connectors').hidden = !needing;
  byId('nav-connectors').textContent = needing;
  renderList('provider-list', PROVIDERS.map((p) => ({ ...p, sig: list(snapshot.models).filter((m) => m.provider === p.id).map((m) => `${m.ref}:${m.configured}`).join(',') })), providerCard, '');
  renderList('model-list', list(snapshot.models), (m) => {
    const chip = element('span', `${m.label || m.ref}${m.configured ? '' : ' (needs key)'}`, `chip${m.configured ? '' : ' chip-muted'}`);
    chip.title = m.ref;
    return chip;
  }, 'No models reported yet.');
}

function activitySentence(item) {
  return item.sentence || `${item.who || 'An agent'} ${item.status === 'queued' ? 'queued' : 'used'} ${item.connector ? item.connector + '.' : ''}${item.tool || 'a tool'}${item.result ? ': ' + item.result : ''}`;
}

function whoLabel(who) {
  const value = String(who || '');
  if (value.startsWith('app:')) {
    const entry = moduleEntry(value.slice(4));
    return entry ? entry.manifest.name || value.slice(4) : value.slice(4);
  }
  return value === 'randy' || value === 'owner' ? 'You' : (value || 'OS');
}

function activityCard(item) {
  const card = element('article', undefined, `entry activity-entry activity-${item.status || 'done'}`);
  const head = element('div', undefined, 'entry-heading');
  head.append(element('p', activitySentence(item)), badge(item.status === 'done' ? 'completed' : (item.status || 'completed')));
  card.append(head, element('small', `${chatTimeLabel(item.time)} · ${whoLabel(item.who)}${item.connector ? ' · ' + item.connector + (item.tool ? '.' + item.tool : '') : ''}`, 'muted'));
  return card;
}

function renderActivity() {
  const items = list(snapshot.activity);
  const filter = byId('activity-filter');
  const whos = [...new Set(items.map((item) => item.who).filter(Boolean))];
  const signature = whos.join(',');
  if (filter.dataset.signature !== signature) {
    const current = filter.value;
    filter.replaceChildren(new Option('Everyone', ''), ...whos.map((who) => new Option(whoLabel(who), who)));
    filter.value = whos.includes(current) ? current : '';
    filter.dataset.signature = signature;
  }
  const shown = filter.value ? items.filter((item) => item.who === filter.value) : items;
  renderList('bot-activity-list', shown, activityCard, snapshot.activity === undefined ? 'Activity has not been reported yet.' : 'Nothing done out in the world yet. When a bot sends, schedules or drafts something, it shows up here.');
  renderList('overview-bots', items.slice(0, 10), (item) => {
    const row = element('p', undefined, 'bot-line');
    row.append(element('span', activitySentence(item)), element('small', ` ${ago(item.time)}`, 'muted'));
    return row;
  }, 'No bot has done anything out in the world yet.');
}

// Model picker shared by every mounted chat.
const chatInstances = new Set();
function modelOptions() {
  const models = list(snapshot && snapshot.models);
  return models.slice().sort((a, b) => Number(!!b.configured) - Number(!!a.configured));
}
function defaultModelRef() {
  const models = list(snapshot && snapshot.models);
  const anthropic = models.find((m) => m.configured && m.provider === 'anthropic');
  return (anthropic || models.find((m) => m.configured) || models[0] || {}).ref || '';
}
function refreshChatModels() { chatInstances.forEach((api) => { try { api.setModels(); } catch (error) { /* a chat that unmounted */ } }); }

function route() {
  const target = parseAppHash(location.hash);
  if (target) showModule(target.id, target.view);
  else if (activeModule) hideModules();
  renderModuleNav();
}

function render() {
  const tasks = list(snapshot.tasks);
  const approvals = list(snapshot.approvals);
  const pendingApprovals = approvals.filter((item) => ['pending', 'awaiting_approval', 'requested'].includes(item.status));
  const modulesNow = moduleEntries();
  const curatorSnap = moduleEntry('curator')?.snapshot || {};
  const darkroomSnap = moduleEntry('darkroom')?.snapshot || {};
  const curatorApprovals = Number(curatorSnap.counts?.approvals) || 0;
  const darkroomApprovals = Number(darkroomSnap.counts?.approvals) || 0;
  const decisions = pendingApprovals.length + curatorApprovals + darkroomApprovals;
  byId('stat-apps').textContent = modulesNow.length;
  byId('stat-enabled').textContent = modulesNow.length ? modulesNow.map(([id, entry]) => entry.manifest.name || id).join(' · ') : 'No apps mounted';
  byId('stat-tasks').textContent = tasks.filter((task) => ['queued', 'ready', 'pending', 'awaiting_approval', 'approved'].includes(task.status)).length;
  byId('stat-approvals').textContent = decisions;
  byId('stat-approvals-note').textContent = `OS ${pendingApprovals.length} · Curator ${curatorApprovals} · Darkroom ${darkroomApprovals}`;
  byId('nav-approvals').textContent = pendingApprovals.length;
  byId('stat-memory').textContent = list(snapshot.memories).length;
  renderOverview(curatorSnap, darkroomSnap);
  byId('version').textContent = snapshot.version ? `v${snapshot.version}` : '';
  const visiblePanels = Array.isArray(snapshot.preferences?.panels) ? snapshot.preferences.panels : Object.keys(panelNames);
  Object.keys(panelNames).forEach((id) => {
    byId(id).hidden = !visiblePanels.includes(id);
    document.querySelector(`[data-panel-link="${id}"]`).hidden = !visiblePanels.includes(id);
  });
  applyTheme();
  renderList('app-list', modulesNow.map(([id, entry]) => ({ id, entry })),
    (item) => moduleAppCard(item.id, item.entry), 'No apps yet. Curator and Darkroom are code under apps/<id>; agent apps are made by asking (+ New app).');
  if (!activeModule && parseAppHash(location.hash)) route();
  renderModuleNav();
  renderModuleAskLinks();
  renderActiveModule();
  renderList('approval-list', approvals.map((approval) => ({ ...approval, task: tasks.find((task) => task.id === approval.task_id) })), approvalCard, 'All clear. No approval requests have been recorded.', false);
  renderList('task-list', tasks, taskCard, 'Nothing in the queue. Create a digest, save a memory, or prepare an agent task.');
  renderList('schedule-list', list(snapshot.schedules), scheduleCard, 'No schedules yet. Choose a rhythm that works for you.');
  renderList('memory-list', list(snapshot.memories), memoryCard, 'A fresh page. Save useful context for your workspace.');
  renderConnectors();
  renderActivity();
  refreshChatModels();
  renderList('activity-list', list(snapshot.events), eventCard, 'No events recorded yet. Your real workspace activity will appear here.');
  renderList('adapter-list', list(snapshot.adapters), capabilityCard, 'No adapters reported.');
  renderList('capability-list', list(snapshot.capabilities), capabilityCard, 'No capabilities reported.');
  const views = snapshot.views || {};
  const view = (name) => views[name] || { cards: [], kpis: [] };
  renderKpis('today-kpis', view('today').kpis);
  renderKpis('routines-kpis', view('routines').kpis);
  renderKpis('usage-kpis', view('usage').kpis);
  renderList('today-list', view('today').cards, viewCard, 'hq/HQ.md has nothing listed.');
  renderList('reco-list', view('recos').cards, viewCard, 'No suggestions right now.');
  renderList('routine-list', view('routines').cards, viewCard, 'No routines found.');
  renderList('usage-list', view('usage').cards, viewCard, 'No Claude Code sessions in the last 30 days.');
  renderList('memory-files', view('memory').cards, viewCard, 'No CLAUDE.md or MEMORY.md files found.');
  const cart = view('recos').cards.find((card) => card.id === 'cart');
  byId('nav-cart').textContent = cart ? (cart.name.match(/\((\d+)\)/) || [0, 0])[1] : 0;
  renderList('agent-list', Array.isArray(snapshot.agents) ? snapshot.agents : [], agentCard, snapshot.agents?.error || 'No agents defined in agents/.');
  renderList('proposal-list', Array.isArray(snapshot.agent_proposals) ? snapshot.agent_proposals : [], proposalCard, 'No proposals waiting.');
  renderList('skill-list', Array.isArray(snapshot.skills) ? snapshot.skills : [], skillCard, snapshot.skills?.error || 'No skills found.');
  renderList('command-list', tasks.filter((task) => task.operation === 'agent.prompt').slice(0, 6).map((task) => ({ ...task, approval: approvals.find((item) => item.task_id === task.id) })), commandCard, 'Nothing sent yet. Give an agent work above.', false);
  renderList('git-status', [snapshot.git || {}], gitCard, 'Git status not reported.');
  updateCommandAgents();
  updateWorkflowOptions();
  renderBrain();
  updateAdapterOptions();
}

function approvalCard(approval) {
  const card = element('article', undefined, 'entry approval-entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', approval.title || approval.id), badge(approval.status));
  const task = approval.task;
  const hasFullTask = task?.operation === 'agent.prompt' && typeof task.payload?.prompt === 'string' && Boolean(task.payload.prompt.trim());
  card.append(heading, element('p', `Adapter: ${approval.adapter || 'Not reported'} · Task: ${approval.task_id}`, 'field-hint'));
  const evidence = element('details');
  evidence.dataset.key = approval.id;
  evidence.append(element('summary', 'Review full prompt & request'), element('p', `Prompt hash: ${approval.prompt_hash || 'Not reported'}`, 'mono wrap'));
  if (hasFullTask) evidence.append(element('h4', 'Exact agent instructions'), element('pre', task.payload.prompt, 'code-block'), element('pre', displayText({ operation: task.operation, adapter: task.payload.adapter, model: task.payload.model }), 'code-block'));
  else evidence.append(element('p', 'The complete linked task is missing from this snapshot. Approval is disabled until its full prompt is available.', 'field-hint'));
  card.append(evidence);
  if (['pending', 'awaiting_approval', 'requested'].includes(approval.status)) {
    const actions = element('div', undefined, 'card-actions');
    for (const [label, decision] of [['Approve', 'approved'], ['Reject', 'rejected']]) {
      const control = button(label, (target) => {
        if (decision === 'approved' && !hasFullTask) return;
        if (!window.confirm(`${label} “${approval.title || approval.id}”? ${decision === 'approved' ? 'This authorizes the recorded task. External execution still requires the local CLI worker with --allow-external.' : 'This records a rejection.'}`)) return;
        perform(target, 'approval.decide', { id: approval.id, decision }, `Approval ${decision}.`);
      }, decision === 'approved' ? 'primary' : 'secondary');
      if (decision === 'approved' && !hasFullTask) {
        control.disabled = true;
        control.title = 'The complete linked task and prompt must be available before approval.';
      }
      actions.append(control);
    }
    card.append(actions);
  }
  return card;
}

function taskCard(task) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', task.title || task.id), badge(task.status));
  card.append(heading, element('small', `${task.operation} · ${dateLabel(task.updated || task.created)}`, 'muted'));
  const details = element('details');
  details.dataset.key = task.id;
  details.append(element('summary', 'Task details'), element('pre', displayText({ id: task.id, payload: task.payload, result: task.result, error: task.error }), 'code-block'));
  card.append(details);
  if (task.error) card.append(element('p', displayText(task.error), 'error-text'));
  if (['queued', 'ready', 'pending', 'awaiting_approval', 'approved'].includes(task.status)) card.append(button('Cancel task', (control) => perform(control, 'task.cancel', { id: task.id }, 'Task canceled.'), 'subtle'));
  return card;
}

function intervalLabel(seconds) {
  for (const [unit, amount] of [['day', 86400], ['hour', 3600], ['minute', 60]]) {
    if (seconds >= amount && seconds % amount === 0) return `Every ${seconds / amount} ${unit}${seconds / amount === 1 ? '' : 's'}`;
  }
  return `Every ${seconds} seconds`;
}

function scheduleCard(schedule) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', schedule.name || schedule.id), badge(schedule.enabled ? 'enabled' : 'paused'));
  card.append(heading, element('p', `${intervalLabel(schedule.interval_seconds)} · ${schedule.workflow}`, 'field-hint'), element('small', `Next run: ${dateLabel(schedule.next_run)}`, 'muted'));
  const actions = element('div', undefined, 'card-actions');
  if (schedule.enabled) actions.append(button('Run now', (control) => perform(control, 'schedule.run', { id: schedule.id }, 'Due now. The worker runs it within a few seconds.'), 'secondary'));
  actions.append(button(schedule.enabled ? 'Pause schedule' : 'Enable schedule', (control) => perform(control, 'schedule.toggle', { id: schedule.id, enabled: !schedule.enabled }, 'Schedule updated.'), 'subtle'));
  card.append(actions);
  if (list(schedule.history).length) {
    const history = element('details');
    history.dataset.key = 'history-' + schedule.id;
    history.append(element('summary', `Last ${schedule.history.length} runs`));
    schedule.history.forEach((run) => history.append(element('p', `${dateLabel(run.at)} · ${run.status}${run.detail ? ' · ' + run.detail : ''}`, 'field-hint')));
    card.append(history);
  }
  return card;
}

function memoryCard(memory) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', memory.key), button('Edit', () => {
    byId('memory-key').value = memory.key;
    byId('memory-content').value = displayText(memory.content);
    byId('memory-editor').open = true;
    byId('memory-content').focus();
  }, 'subtle'));
  card.append(heading, element('p', memory.content, 'memory-content'), element('small', `Updated ${dateLabel(memory.updated)}`, 'muted'));
  return card;
}

function renderKpis(id, kpis) {
  const target = byId(id);
  if (!target) return;
  const signature = JSON.stringify(kpis);
  if (viewSignatures.get(id) === signature) return;
  target.replaceChildren(...list(kpis).map(([label, value]) => {
    const cell = element('div', undefined, 'kpi');
    cell.append(element('small', label, 'stat-label'), element('strong', value));
    return cell;
  }));
  viewSignatures.set(id, signature);
}

const viewActions = { copy: 'Copy prompt', open: 'Open', 'cart-add': 'Add to cart', 'cart-remove': 'Remove', 'cart-clear': 'Clear cart', 'rebuild-hq': 'Rebuild HQ now' };

function viewCard(card) {
  const entry = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', card.name), badge(card.status));
  entry.append(heading);
  if (card.summary) entry.append(element('p', card.summary, 'field-hint'));
  if (list(card.fields).length) {
    const details = element('details');
    details.dataset.key = card.id;
    const rows = element('dl', undefined, 'detail-list');
    list(card.fields).forEach(([label, value]) => rows.append(detail(label, value)));
    details.append(element('summary', 'Details'), rows);
    entry.append(details);
  }
  const actions = element('div', undefined, 'card-actions');
  list(card.actions).forEach((action) => actions.append(button(viewActions[action] || action, (control) => viewAction(control, action, card), action === 'cart-add' ? 'primary' : 'subtle')));
  if (actions.childElementCount) entry.append(actions);
  return entry;
}

async function viewAction(control, action, card) {
  if (action === 'copy') {
    try { await navigator.clipboard.writeText(card.copy || ''); announce('Copied. Paste it into Claude Code.'); }
    catch { announce('The browser blocked the clipboard. Open Details and copy the prompt by hand.', true); }
    return;
  }
  if (action === 'open') { window.open(card.link, card.link.startsWith('/') ? '_self' : '_blank', 'noopener'); return; }
  const messages = { 'cart-add': 'Added to your cart.', 'cart-remove': 'Removed from your cart.', 'cart-clear': 'Cart cleared.', 'rebuild-hq': 'hq/HQ.md rebuilt. Commit hq/ when you are happy with it.' };
  await perform(control, 'arms.' + action, { id: card.id }, messages[action] || 'Done.');
}

function agentCard(agent) {
  const card = element('article', undefined, 'app-card');
  const top = element('div', undefined, 'card-top');
  top.append(element('span', agent.id.slice(0, 1).toUpperCase(), 'app-icon'), badge(agent.state));
  card.append(top, element('h3', agent.id), element('p', agent.role, 'card-description'));
  const rows = element('dl', undefined, 'detail-list');
  rows.append(detail('Runs on', `${agent.adapter}${agent.edits ? ' (can edit on a branch)' : ' (read and answer)'}`),
    detail('On this Mac', `${agent.availability} · checked ${dateLabel(agent.checked)}`),
    detail('Last run', agent.last_run ? `${agent.last_run.status} · ${dateLabel(agent.last_run.at)} · ${agent.last_run.title}` : 'Never'),
    detail('Last error', agent.last_error ? `${dateLabel(agent.last_error.at)} · ${agent.last_error.message}` : 'None'),
    detail('Skills', list(agent.skills).join(', ') || 'None'));
  card.append(rows);
  const actions = element('div', undefined, 'card-actions');
  actions.append(button('Give work ↗', () => commandWith(agent.id, ''), 'secondary'));
  card.append(actions);
  return card;
}

function proposalCard(item) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', `${item.id} · ${item.adapter}`), badge('proposed'));
  card.append(heading, element('p', item.role, 'field-hint'), element('small', item.file, 'muted'));
  card.append(button('Accept into agents/', (control) => {
    if (!window.confirm(`Move ${item.file} into agents/? You still review and commit it.`)) return;
    perform(control, 'agent.accept', { id: item.id }, `${item.id} added to agents/. Commit it when you are happy.`);
  }, 'secondary'));
  return card;
}

function skillCard(skill) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', skill.name), badge(skill.model ? `model: ${skill.model}` : 'not pinned'));
  card.append(heading, element('p', skill.description || 'No description.', 'field-hint'),
    element('small', `${skill.source} · ~${skill.tokens.toLocaleString()} tokens · used ${skill.uses}x in 30 days · ${skill.notes} parked note${skill.notes === 1 ? '' : 's'}${skill.small ? ' · small: could pin to haiku' : ''}`, 'muted'));
  const actions = element('div', undefined, 'card-actions');
  actions.append(button('Run ↗', () => commandWith('', `Read ${skill.file} and follow it exactly.`), 'secondary'),
    button('Park a note', (control) => {
      const text = window.prompt(`Feedback for ${skill.name}. It waits in Recos until you apply it.`);
      if (text && text.trim()) perform(control, 'arms.note', { skill: skill.name, text: text.trim() }, `Parked for ${skill.name}.`);
    }, 'subtle'));
  card.append(actions, element('small', skill.file, 'mono muted'));
  return card;
}

function commandCard(task) {
  const card = element('article', undefined, 'entry');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', task.title || task.id), badge(task.status));
  card.append(heading, element('small', `${task.payload?.agent || task.payload?.adapter} · ${task.payload?.mode === 'edit' ? 'edit on a branch' : 'read and answer'} · ${dateLabel(task.updated || task.created)}`, 'muted'));
  const prompt = element('details');
  prompt.dataset.key = 'prompt-' + task.id;
  prompt.append(element('summary', 'Instructions'), element('pre', task.payload?.prompt || '', 'code-block'));
  card.append(prompt);
  if (task.result) card.append(element('pre', displayText(task.result), 'code-block'));
  if (task.error) card.append(element('p', displayText(task.error), 'error-text'));
  const approval = task.approval;
  if (approval && approval.status === 'pending' && task.status === 'awaiting_approval') {
    const actions = element('div', undefined, 'card-actions');
    actions.append(button('Approve and run', (control) => perform(control, 'approval.decide', { id: approval.id, decision: 'approved' }, 'Approved. The worker starts it within a few seconds.'), 'primary'),
      button('Reject', (control) => perform(control, 'approval.decide', { id: approval.id, decision: 'rejected' }, 'Rejected.'), 'subtle'));
    card.append(actions);
  }
  return card;
}

function gitCard(git) {
  const card = element('article', undefined, 'entry');
  if (!git.available) { card.append(element('p', git.error || 'Git is not available here.', 'field-hint')); return card; }
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', `Branch ${git.branch}`), badge(git.changed ? `${git.changed} changed` : 'clean'));
  card.append(heading, element('small', `${git.ahead} ahead · ${git.behind} behind origin · agent branches: ${list(git.agent_branches).join(', ') || 'none'}`, 'muted'));
  return card;
}

function updateCommandAgents() {
  const select = byId('command-agent');
  if (select.contains(document.activeElement)) return;
  const current = select.value;
  const options = [...(Array.isArray(snapshot.agents) ? snapshot.agents : []).map((agent) => [agent.id, `${agent.id} (${agent.adapter}, ${agent.availability})`])];
  const signature = JSON.stringify(options);
  if (select.dataset.signature === signature) return;
  select.replaceChildren(...options.map(([value, label]) => { const option = element('option', label); option.value = value; return option; }));
  select.dataset.signature = signature;
  if (current && options.some(([value]) => value === current)) select.value = current;
}

function updateWorkflowOptions() {
  const select = byId('schedule-workflow');
  const signature = JSON.stringify(snapshot.workflows);
  if (select.dataset.signature === signature || select.contains(document.activeElement)) return;
  select.replaceChildren(...list(snapshot.workflows).map((workflow) => { const option = element('option', workflow.name); option.value = workflow.id; return option; }));
  select.dataset.signature = signature;
}

function commandWith(agentId, prompt) {
  if (agentId) byId('command-agent').value = agentId;
  if (prompt) byId('command-prompt').value = prompt;
  byId('command').scrollIntoView({ block: 'start' });
  byId('command-prompt').focus();
}

function eventCard(event) {
  const card = element('article', undefined, 'event');
  card.append(element('small', `${dateLabel(event.time)} · ${event.event || 'Event'}`, 'muted'), element('p', event.message));
  return card;
}

function capabilityCard(item) {
  const card = element('div', undefined, 'capability');
  const heading = element('div', undefined, 'entry-heading');
  heading.append(element('h4', item.name || item.id), badge(item.status));
  card.append(heading, element('p', item.detail || 'No additional detail reported.', 'field-hint'));
  return card;
}

function renderBrain() {
  const brain = snapshot.brain || {};
  renderList('brain-status', [brain], () => {
    const block = element('div');
    const dirty = brain.dirty === true ? 'Local changes' : brain.dirty === false ? 'Clean working tree' : `Working tree: ${displayText(brain.dirty) || 'Not reported'}`;
    block.append(element('p', dirty, 'brain-title'));
    const definitions = element('dl');
    definitions.append(detail('Repository', brain.root), detail('HEAD', brain.head), detail('Reconciled HEAD', brain.reconciled_head), detail('Drift', brain.drift === false ? 'None reported' : brain.drift === true ? 'Detected: review and reconcile' : displayText(brain.drift)));
    block.append(definitions);
    return block;
  }, 'Repository state is unavailable.');
}

function updateAdapterOptions() {
  const select = byId('task-adapter');
  const adapters = list(snapshot.adapters);
  const signature = JSON.stringify(adapters);
  if (viewSignatures.get('adapter-options') === signature || document.activeElement === select) return;
  const previous = select.value;
  select.replaceChildren(element('option', adapters.length ? 'Choose an adapter' : 'No adapters reported'));
  select.options[0].value = '';
  adapters.forEach((adapter) => {
    const option = element('option', `${adapter.id} · ${adapter.status}`);
    option.value = adapter.id;
    select.append(option);
  });
  if (adapters.some((adapter) => adapter.id === previous)) select.value = previous;
  viewSignatures.set('adapter-options', signature);
  updateAgentModel();
}

function updateAgentModel() {
  const visible = byId('task-operation').value === 'agent.prompt' && byId('task-adapter').value === 'ollama';
  byId('task-model-fields').hidden = !visible;
  byId('task-model').disabled = !visible;
}

function openPreferences() {
  if (!snapshot) { announce('Connect to the workspace before changing preferences.', true); return; }
  const select = byId('theme-choice');
  select.replaceChildren(...list(snapshot.themes).map((theme) => {
    const option = element('option', theme.name || theme.id);
    option.value = theme.id;
    return option;
  }));
  select.value = snapshot.preferences?.theme || 'forest';
  select.required = true;
  byId('panel-choices').replaceChildren(...Object.entries(panelNames).map(([id, name]) => {
    const label = element('label', undefined, 'checkbox-label');
    const input = element('input');
    input.type = 'checkbox';
    input.name = 'panels';
    input.value = id;
    input.checked = (snapshot.preferences?.panels || Object.keys(panelNames)).includes(id);
    label.append(input, element('span', name));
    return label;
  }));
  showError('preferences-error', '');
  byId('preferences-dialog').showModal();
}

document.querySelectorAll('[data-close]').forEach((control) => control.addEventListener('click', () => {
  if (!mutationPending) byId(control.dataset.close).close();
}));
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('cancel', (event) => {
  if (mutationPending) event.preventDefault();
}));
byId('customize').addEventListener('click', openPreferences);
byId('refresh').addEventListener('click', () => run(byId('refresh'), async () => { await loadToken(); await refresh(); }));
byId('worker-tick').addEventListener('click', () => perform(byId('worker-tick'), 'worker.tick', {}, 'Built-in worker pass completed. See task results and activity for details.'));
byId('reconcile').addEventListener('click', () => perform(byId('reconcile'), 'reconcile', {}, 'Repository reconciliation recorded.'));
document.querySelectorAll('#navigation a').forEach((link) => link.addEventListener('click', () => {
  document.querySelectorAll('#navigation a').forEach((item) => item.removeAttribute('aria-current'));
  link.setAttribute('aria-current', 'location');
  if (activeModule) { hideModules(); renderModuleNav(); }
}));
window.addEventListener('hashchange', route);
document.addEventListener('keydown', (event) => {
  if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
  const tag = (event.target && event.target.tagName) || '';
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || (event.target && event.target.isContentEditable)) return;
  if (!activeModule || !['ask', 'chat'].includes(activeModule.view)) return;
  event.preventDefault();
  byId(`module-${activeModule.id}`).dispatchEvent(new CustomEvent('module:focus', { detail: { view: activeModule.view } }));
});

byId('task-operation').addEventListener('change', () => {
  const operation = byId('task-operation').value;
  byId('task-memory-fields').hidden = operation !== 'memory.put';
  byId('task-agent-fields').hidden = operation !== 'agent.prompt';
  for (const id of ['task-key', 'task-content']) { byId(id).required = operation === 'memory.put'; byId(id).disabled = operation !== 'memory.put'; }
  for (const id of ['task-adapter', 'task-prompt']) { byId(id).required = operation === 'agent.prompt'; byId(id).disabled = operation !== 'agent.prompt'; }
  updateAgentModel();
});
byId('task-adapter').addEventListener('change', updateAgentModel);
byId('task-operation').dispatchEvent(new Event('change'));

byId('task-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const operation = values.get('operation');
  const payload = operation === 'memory.put' ? { key: values.get('key').trim(), content: values.get('content').trim() } : operation === 'agent.prompt' ? { adapter: values.get('adapter'), prompt: values.get('prompt').trim() } : {};
  if (operation === 'agent.prompt' && payload.adapter === 'ollama' && values.get('model')?.trim()) payload.model = values.get('model').trim();
  run(form.querySelector('[type="submit"]'), async () => {
    await mutate('task.create', { title: values.get('title').trim(), operation, payload });
    form.reset();
    byId('task-operation').dispatchEvent(new Event('change'));
    announce(operation === 'agent.prompt' ? 'Agent task created. Review its approval before running the external CLI worker.' : 'Task added to the queue. Run built-in tasks when ready.');
    await refresh();
  });
});

byId('schedule-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const seconds = Number(values.get('interval')) * Number(values.get('unit'));
  if (!Number.isInteger(seconds) || seconds < 60 || seconds > 31536000) { announce('Choose an interval between 60 seconds and 365 days.', true); return; }
  run(form.querySelector('[type="submit"]'), async () => {
    await mutate('schedule.create', { name: values.get('name').trim(), workflow: values.get('workflow') || 'daily-digest', interval_seconds: seconds });
    form.reset();
    announce('Schedule saved. A running local worker processes due schedules.');
    await refresh();
  });
});

byId('memory-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  run(form.querySelector('[type="submit"]'), async () => {
    await mutate('memory.put', { key: values.get('key').trim(), content: values.get('content').trim() });
    form.reset();
    announce('Memory saved.');
    await refresh();
  });
});

byId('preferences-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  run(form.querySelector('[type="submit"]'), async () => {
    await mutate('preferences.set', { theme: values.get('theme'), panels: values.getAll('panels') });
    byId('preferences-dialog').close();
    announce('Your workspace preferences are saved.');
    await refresh();
  }, 'preferences-error');
});

byId('command-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const agent = (Array.isArray(snapshot?.agents) ? snapshot.agents : []).find((item) => item.id === values.get('agent'));
  if (!agent) { announce('Choose an agent first.', true); return; }
  const mode = values.get('mode');
  if (mode === 'edit' && !agent.edits) { announce(`${agent.id} runs on ${agent.adapter}, which can only read and answer. Pick a Claude or Codex agent to edit.`, true); return; }
  const prompt = values.get('prompt').trim();
  run(form.querySelector('[type="submit"]'), async () => {
    await mutate('task.create', { title: `${agent.id}: ${prompt.slice(0, 80)}`, operation: 'agent.prompt', payload: { adapter: agent.adapter, agent: agent.id, prompt, ...(mode === 'edit' ? { mode } : {}) } });
    byId('command-prompt').value = '';
    announce('Sent. Approve it below and the worker runs it.');
    await refresh();
  });
});

byId('agent-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  run(form.querySelector('[type="submit"]'), async () => {
    const result = await mutate('agent.propose', { id: values.get('id').trim(), role: values.get('role').trim(), adapter: values.get('adapter'), skills: values.get('skills').split(',').map((item) => item.trim()).filter(Boolean) });
    form.reset();
    announce(result.message || 'Agent proposed.');
    await refresh();
  });
});

byId('skill-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  run(form.querySelector('[type="submit"]'), async () => {
    const result = await mutate('skill.create', { name: values.get('name').trim(), description: values.get('description').trim(), model: values.get('model'), home: values.get('home'), body: values.get('body') });
    form.reset();
    announce(result.message || 'Skill added.');
    await refresh();
  });
});

function cadenceSentence(kind, when) {
  if (kind === 'hours') { const n = Math.max(1, Math.min(168, parseInt(when, 10) || 24)); return `every ${n} hours`; }
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(when || '').trim());
  return `daily at ${match ? `${match[1].padStart(2, '0')}:${match[2]}` : '06:45'}`;
}
byId('apps-new').addEventListener('click', () => { byId('newapp-reply').hidden = true; byId('newapp-reply').replaceChildren(); byId('newapp-dialog').showModal(); byId('newapp-name').focus(); });
byId('newapp-cadence').addEventListener('change', () => { const hours = byId('newapp-cadence').value === 'hours'; byId('newapp-when').value = hours ? '24' : '06:45'; byId('newapp-when').placeholder = hours ? 'hours' : 'HH:MM'; });
byId('newapp-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const name = String(values.get('name') || '').trim();
  const what = String(values.get('what') || '').trim();
  const cadence = cadenceSentence(values.get('cadence'), values.get('when'));
  const notify = !!values.get('notify');
  const text = `Create an app named ${name} that ${what.replace(/\.$/, '')}. Run it ${cadence}. ${notify ? 'Put new finds in my Inbox.' : 'Do not notify me; I will check its board.'}`;
  run(byId('newapp-send'), async () => {
    const result = await chatRequest('/api/chat', { thread: null, text, agent: 'operator', app: null });
    const payload = result && result.ok === true ? result.result : result;
    const reply = byId('newapp-reply');
    reply.replaceChildren(chatMarkdown((payload && payload.message && (payload.message.text || payload.message.answer)) || 'The Operator did not answer.'));
    reply.hidden = false;
    announce(`Asked the Operator to build ${name}.`);
    await refresh();
    const created = moduleEntries().find(([id, entry]) => entry.manifest.kind === 'agent' && (entry.manifest.name || '').toLowerCase() === name.toLowerCase());
    if (created) { byId('newapp-dialog').close(); form.reset(); location.hash = `#app/${created[0]}/board`; }
  }, 'newapp-error');
});

document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden && !mutationPending) refresh(); }, 5000);
refresh().then(() => route());
try {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
} catch (error) { /* installing as an app is optional */ }

// ---------- Add MCP server form ----------
function envRow() {
  const row = element('div', undefined, 'env-row');
  const key = element('input'); key.placeholder = 'KEY'; key.maxLength = 80; key.setAttribute('aria-label', 'Variable name');
  const value = element('input'); value.placeholder = 'value'; value.maxLength = 4000; value.setAttribute('aria-label', 'Value');
  const secret = element('label', undefined, 'checkbox-row');
  const box = element('input'); box.type = 'checkbox';
  box.addEventListener('change', () => { value.type = box.checked ? 'password' : 'text'; value.autocomplete = 'off'; });
  secret.append(box, element('span', 'secret'));
  const remove = button('×', () => row.remove(), 'icon-button subtle');
  remove.setAttribute('aria-label', 'Remove variable');
  row.append(key, element('span', '=', 'muted'), value, secret, remove);
  row.read = () => ({ key: key.value.trim(), value: value.value, secret: box.checked });
  return row;
}
byId('mcp-env-add').addEventListener('click', () => byId('mcp-env').append(envRow()));
byId('mcp-form').addEventListener('change', (event) => {
  if (event.target.name !== 'transport') return;
  const http = byId('mcp-form').querySelector('input[name="transport"]:checked').value === 'http';
  byId('mcp-stdio').hidden = http; byId('mcp-http').hidden = !http;
});
byId('mcp-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const id = String(values.get('id') || '').trim();
  const transport = values.get('transport') === 'http' ? 'http' : 'stdio';
  const rows = [...byId('mcp-env').children].map((row) => row.read()).filter((row) => row.key);
  const bad = rows.find((row) => !/^[A-Za-z_][A-Za-z0-9_-]{0,79}$/.test(row.key));
  if (bad) { showError('mcp-error', `${bad.key} is not a valid variable or header name.`); return; }
  run(form.querySelector('[type="submit"]'), async () => {
    const vars = {};
    for (const row of rows) {
      if (row.secret) {
        const name = `mcp-${id}-${row.key}`;
        if (row.value) await saveSecret(name, row.value);
        vars[row.key] = `secret:${name}`;
      } else vars[row.key] = row.value;
    }
    const spec = { id, name: String(values.get('name') || '').trim() || id, transport };
    if (transport === 'stdio') {
      spec.command = String(values.get('command') || '').trim();
      spec.args = String(values.get('args') || '').split('\n').map((a) => a.trim()).filter(Boolean);
      spec.env = vars;
      if (!spec.command) throw new Error('A stdio server needs a command (for example npx).');
    } else {
      spec.url = String(values.get('url') || '').trim();
      spec.headers = vars;
      if (!/^https?:\/\//.test(spec.url)) throw new Error('An HTTP server needs its URL.');
    }
    await connectorOp({ op: 'add_mcp', spec });
    const test = await connectorOp({ op: 'test', id });
    form.reset(); byId('mcp-env').replaceChildren(); byId('mcp-stdio').hidden = false; byId('mcp-http').hidden = true;
    byId('mcp-add').open = false;
    announce(`${spec.name} added. ${test && test.ok ? `Works${Array.isArray(test.tools) ? `: ${test.tools.length} tools` : ''}.` : `Test failed${test && test.detail ? ': ' + test.detail : ''}.`}`, !(test && test.ok));
    await refresh();
  }, 'mcp-error');
});
byId('activity-filter').addEventListener('change', () => { viewSignatures.delete('bot-activity-list'); renderActivity(); });
