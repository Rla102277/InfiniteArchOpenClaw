'use strict';

const byId = (id) => document.getElementById(id);
const panelNames = { apps: 'Applications', tasks: 'Tasks & approvals', schedules: 'Schedules', memory: 'Memory', activity: 'Activity', system: 'System' };
const interviewDefaults = [
  { id: 'problem', label: 'What problem should this app help you solve?', hint: 'Describe what feels difficult or scattered today.' },
  { id: 'users', label: 'Who will use this space?', hint: 'Think about the people and their everyday needs.' },
  { id: 'outcome', label: 'What would a useful outcome look like?', hint: 'Describe what you want to be able to track or accomplish.' },
  { id: 'acceptance', label: 'How will you know it is working?', hint: 'Name something you can observe or check on your record board.' }
];
let snapshot = null;
let csrfToken = '';
let refreshPromise = null;
let refreshAgain = false;
let mutationPending = false;
let hasConnected = false;
let noticeTimer;
let activeAppId = null;
let builder = null;
let themeRuleIndex = null;
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
      const state = data.ok === true ? data.result : data;
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

function render() {
  const apps = list(snapshot.apps);
  const tasks = list(snapshot.tasks);
  const approvals = list(snapshot.approvals);
  const pendingApprovals = approvals.filter((item) => ['pending', 'awaiting_approval', 'requested'].includes(item.status));
  byId('stat-apps').textContent = apps.filter((app) => ['installed', 'enabled', 'paused'].includes(app.status)).length;
  byId('stat-enabled').textContent = `${apps.filter((app) => app.status === 'enabled').length} enabled · ${apps.length} in catalog`;
  byId('stat-tasks').textContent = tasks.filter((task) => ['queued', 'ready', 'pending', 'awaiting_approval', 'approved'].includes(task.status)).length;
  byId('stat-approvals').textContent = pendingApprovals.length;
  byId('nav-approvals').textContent = pendingApprovals.length;
  byId('stat-memory').textContent = list(snapshot.memories).length;
  byId('version').textContent = snapshot.version ? `v${snapshot.version}` : '';
  const visiblePanels = Array.isArray(snapshot.preferences?.panels) ? snapshot.preferences.panels : Object.keys(panelNames);
  Object.keys(panelNames).forEach((id) => {
    byId(id).hidden = !visiblePanels.includes(id);
    document.querySelector(`[data-panel-link="${id}"]`).hidden = !visiblePanels.includes(id);
  });
  applyTheme();
  renderList('app-list', apps.map((app) => ({ ...app, recordCount: list(snapshot.records?.[app.id]).length })), appCard, 'Your toolkit starts here. Build your first app from an idea or a template.');
  renderList('template-list', list(snapshot.templates), templateCard, 'No templates are reported by the backend yet.');
  renderList('approval-list', approvals.map((approval) => ({ ...approval, task: tasks.find((task) => task.id === approval.task_id) })), approvalCard, 'All clear. No approval requests have been recorded.', false);
  renderList('task-list', tasks, taskCard, 'Nothing in the queue. Create a digest, save a memory, or prepare an agent task.');
  renderList('schedule-list', list(snapshot.schedules), scheduleCard, 'No schedules yet. Choose a rhythm that works for you.');
  renderList('memory-list', list(snapshot.memories), memoryCard, 'A fresh page. Save useful context for your workspace.');
  renderList('activity-list', list(snapshot.events), eventCard, 'No events recorded yet. Your real workspace activity will appear here.');
  renderList('adapter-list', list(snapshot.adapters), capabilityCard, 'No adapters reported.');
  renderList('capability-list', list(snapshot.capabilities), capabilityCard, 'No capabilities reported.');
  renderBrain();
  updateAdapterOptions();
  if (byId('record-dialog').open) renderRecords();
}

function appCard(app) {
  const card = element('article', undefined, 'app-card');
  const top = element('div', undefined, 'card-top');
  top.append(element('span', (app.name || app.id || 'A').slice(0, 1).toUpperCase(), 'app-icon'), badge(app.status));
  card.append(top, element('h3', app.name || app.id), element('p', app.description || 'A declarative space for your records.', 'card-description'));
  const chips = element('div', undefined, 'chips');
  list(app.fields).forEach((field) => chips.append(element('span', field, 'chip')));
  card.append(chips, element('small', `${app.recordCount} records · ${displayText(app.template) || 'Custom board'}`, 'muted'));
  const actions = element('div', undefined, 'card-actions');
  actions.append(button('Open board ↗', () => openRecords(app.id), 'subtle'));
  const transitions = { available: ['install'], uninstalled: ['install'], installed: ['enable', 'uninstall'], enabled: ['pause', 'uninstall'], paused: ['enable', 'uninstall'] };
  for (const transition of transitions[app.status] || []) {
    const label = transition.charAt(0).toUpperCase() + transition.slice(1);
    actions.append(button(label, (control) => {
      if (transition === 'uninstall' && !window.confirm(`Uninstall “${app.name || app.id}”? This changes its lifecycle status. Review any retained records before continuing.`)) return;
      perform(control, 'app.lifecycle', { id: app.id, transition }, `${label} recorded for ${app.name || app.id}.`);
    }, transition === 'uninstall' ? 'subtle' : 'secondary'));
  }
  card.append(actions);
  return card;
}

function templateCard(template) {
  const card = element('article', undefined, 'template-card');
  card.append(element('span', '▧', 'template-icon'), element('h4', template.name || template.id), element('p', template.description || 'A starting point for a record board.'), button('Use template →', () => openBuilder(template.id), 'subtle'));
  return card;
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
  card.append(heading, element('p', `${intervalLabel(schedule.interval_seconds)} · ${schedule.workflow}`, 'field-hint'), element('small', `Next run: ${dateLabel(schedule.next_run)}`, 'muted'), button(schedule.enabled ? 'Pause schedule' : 'Enable schedule', (control) => perform(control, 'schedule.toggle', { id: schedule.id, enabled: !schedule.enabled }, 'Schedule updated.'), 'subtle'));
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
    definitions.append(detail('Repository', brain.root), detail('HEAD', brain.head), detail('Reconciled HEAD', brain.reconciled_head), detail('Drift', brain.drift === false ? 'None reported' : brain.drift === true ? 'Detected — review and reconcile' : displayText(brain.drift)));
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

function openBuilder(templateId) {
  if (!snapshot) { announce('Connect to the workspace before building an app.', true); return; }
  if (!list(snapshot.templates).length) { announce('No app templates are available yet.', true); return; }
  byId('builder-form').reset();
  builder = { step: 0, answers: {}, preview: null, questions: interviewDefaults.map((fallback) => ({ ...fallback, ...list(snapshot.builder_questions).find((question) => question.id === fallback.id), id: fallback.id })) };
  byId('builder-template').replaceChildren(...list(snapshot.templates).map((template) => {
    const option = element('option', template.name || template.id);
    option.value = template.id;
    return option;
  }));
  if (templateId) byId('builder-template').value = templateId;
  byId('builder-id').dataset.manual = 'false';
  renderBuilder();
  byId('builder-dialog').showModal();
}

function builderArguments() {
  return { id: byId('builder-id').value.trim(), name: byId('builder-name').value.trim(), template: byId('builder-template').value, answers: { ...builder.answers } };
}

function saveBuilderAnswer() {
  if (builder.step >= 1 && builder.step <= 4) builder.answers[builder.questions[builder.step - 1].id] = byId('builder-answer').value.trim();
}

function renderBuilder() {
  const step = builder.step;
  byId('builder-step-label').textContent = step === 5 ? 'Step 6 of 6 · Review & approve' : `Step ${step + 1} of 6`;
  byId('builder-progress').value = step + 1;
  byId('builder-identity').hidden = step !== 0;
  for (const id of ['builder-name', 'builder-id', 'builder-template']) byId(id).disabled = step !== 0;
  byId('builder-question').hidden = step === 0 || step === 5;
  byId('builder-answer').disabled = step === 0 || step === 5;
  byId('builder-answer').required = step > 0 && step < 5;
  byId('builder-review').hidden = step !== 5;
  byId('builder-back').hidden = step === 0;
  byId('builder-next').hidden = step === 5;
  byId('builder-next').textContent = step === 4 ? 'Preview app →' : 'Continue →';
  byId('builder-create').hidden = step !== 5;
  byId('builder-create').disabled = !builder.preview;
  showError('builder-error', '');
  if (step > 0 && step < 5) {
    const question = builder.questions[step - 1];
    byId('builder-question-number').textContent = `QUESTION ${step} OF 4`;
    byId('builder-answer-label').textContent = question.label;
    byId('builder-answer-hint').textContent = question.hint;
    byId('builder-answer').value = builder.answers[question.id] || '';
    byId('builder-answer').focus();
  }
}

function renderPreview(preview) {
  const blocks = [['Manifest', preview.manifest], ['Specification', preview.spec], ['Implementation plan', preview.plan], ['Questions', preview.questions]];
  byId('builder-preview').replaceChildren(...blocks.map(([title, content]) => {
    const section = element('section', undefined, 'preview-section');
    section.append(element('h3', title), element('pre', content == null ? 'Not provided by the backend.' : displayText(content), 'code-block'));
    return section;
  }));
}

function openRecords(id) {
  activeAppId = id;
  byId('record-form').reset();
  showError('record-error', '');
  renderRecords();
  byId('record-dialog').showModal();
}

function renderRecords() {
  const app = list(snapshot.apps).find((item) => item.id === activeAppId);
  byId('record-title').textContent = app?.name || activeAppId;
  byId('record-description').textContent = `${app?.description || 'A declarative record board.'} ${app?.status === 'enabled' ? '' : 'Enable this app to add records.'}`;
  byId('record-save').disabled = app?.status !== 'enabled';
  renderList('record-list', list(snapshot.records?.[activeAppId]), (record) => {
    const card = element('article', undefined, 'entry');
    const heading = element('div', undefined, 'entry-heading');
    heading.append(element('h4', record.title || record.id), badge(record.status));
    card.append(heading, element('p', record.note || 'No note', 'memory-content'), element('small', dateLabel(record.created), 'muted'));
    return card;
  }, 'No records yet. Add your first entry below once the app is enabled.');
}

document.querySelectorAll('[data-close]').forEach((control) => control.addEventListener('click', () => {
  if (!mutationPending) byId(control.dataset.close).close();
}));
document.querySelectorAll('dialog').forEach((dialog) => dialog.addEventListener('cancel', (event) => {
  if (mutationPending) event.preventDefault();
}));
['new-app', 'hero-build', 'apps-build'].forEach((id) => byId(id).addEventListener('click', () => openBuilder()));
byId('customize').addEventListener('click', openPreferences);
byId('refresh').addEventListener('click', () => run(byId('refresh'), async () => { await loadToken(); await refresh(); }));
byId('worker-tick').addEventListener('click', () => perform(byId('worker-tick'), 'worker.tick', {}, 'Built-in worker pass completed. See task results and activity for details.'));
byId('reconcile').addEventListener('click', () => perform(byId('reconcile'), 'reconcile', {}, 'Repository reconciliation recorded.'));
document.querySelectorAll('#navigation a').forEach((link) => link.addEventListener('click', () => {
  document.querySelectorAll('#navigation a').forEach((item) => item.removeAttribute('aria-current'));
  link.setAttribute('aria-current', 'location');
}));

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
    await mutate('schedule.create', { name: values.get('name').trim(), workflow: 'daily-digest', interval_seconds: seconds });
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

byId('builder-name').addEventListener('input', () => {
  if (byId('builder-id').dataset.manual !== 'true') {
    const slug = byId('builder-name').value.toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    byId('builder-id').value = (/^[0-9]/.test(slug) ? `app-${slug}` : slug).slice(0, 64).replace(/-$/, '');
  }
});
byId('builder-id').addEventListener('input', () => { byId('builder-id').dataset.manual = 'true'; });
byId('builder-back').addEventListener('click', () => {
  if (mutationPending) return;
  saveBuilderAnswer();
  builder.preview = null;
  builder.step = Math.max(0, builder.step - 1);
  renderBuilder();
});
byId('builder-form').addEventListener('submit', (event) => {
  event.preventDefault();
  if (mutationPending || !builder || builder.step === 5) return;
  saveBuilderAnswer();
  if (builder.step > 0 && !byId('builder-answer').value.trim()) { showError('builder-error', 'Add an answer before continuing.'); return; }
  if (builder.step < 4) {
    builder.step += 1;
    renderBuilder();
    return;
  }
  run(byId('builder-next'), async () => {
    const preview = await mutate('builder.preview', builderArguments());
    if (!preview || !preview.manifest || !preview.spec || !preview.plan) throw new Error('The preview is incomplete. Review your answers and try again.');
    builder.preview = preview;
    builder.step = 5;
    renderPreview(preview);
    renderBuilder();
    byId('builder-review').scrollIntoView({ block: 'nearest' });
    byId('builder-create').focus();
  }, 'builder-error');
});
byId('builder-create').addEventListener('click', () => {
  if (!builder?.preview || builder.step !== 5) return;
  run(byId('builder-create'), async () => {
    const values = builderArguments();
    await mutate('builder.create', { ...values, confirm: true });
    byId('builder-dialog').close();
    builder = null;
    announce(`${values.name} created as a declarative record board. Install and enable it from Applications.`);
    await refresh();
  }, 'builder-error');
});

byId('record-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = new FormData(form);
  const app = list(snapshot?.apps).find((item) => item.id === activeAppId);
  if (app?.status !== 'enabled') { showError('record-error', 'Enable this app before adding a record.'); return; }
  run(byId('record-save'), async () => {
    await mutate('app.record', { id: activeAppId, title: values.get('title').trim(), status: values.get('status').trim(), note: values.get('note').trim() });
    form.reset();
    await refresh();
    byId('record-entry-title').focus();
    announce('Record added to your board.');
  }, 'record-error');
});

document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
setInterval(() => { if (!document.hidden && !mutationPending) refresh(); }, 5000);
refresh();
