'use strict';
// Generic module for agent apps (os/system/contracts/AGENT_RUNTIME.md): apps defined by data under
// hq/os/apps/<id>/ (a prompt, a schedule, output fields). One script serves every agent app; the
// shell asks OSModules.__agentApp(id) for an instance. Views: board, chat, settings.

(function () {
  const list = (v) => Array.isArray(v) ? v : [];

  function createAgentApp(appId) {
    let host = null;
    let root = null;
    let snapshot = {};
    let currentView = 'board';
    let chat = null;
    const nodes = {};
    const sort = { key: null, dir: 1 };
    let filterText = '';
    let settingsDirty = false;

    const el = (tag, text, className) => host.el(tag, text, className);
    const button = (label, onClick, className) => host.button(label, onClick, className);
    const badge = (status) => host.badge(status);
    const spec = () => (snapshot.spec && typeof snapshot.spec === 'object' ? snapshot.spec : {});
    const fields = () => { const f = list(spec().fields).filter((x) => typeof x === 'string'); return f.length ? f : ['title', 'status', 'note']; };
    const records = () => list(snapshot.records);
    const timeLabel = (value) => {
      if (!value) return 'never';
      const d = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
      return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    };

    function section(id, eyebrow, title, extra) {
      const sec = el('section', null, 'aa-view');
      sec.dataset.view = id;
      sec.hidden = true;
      const header = el('header', null, 'aa-heading');
      const left = el('div');
      const eye = el('p', eyebrow, 'aa-eyebrow');
      nodes[id + '-eyebrow'] = eye;
      const h1 = el('h1', title);
      nodes[id + '-title'] = h1;
      left.append(eye, h1);
      header.append(left);
      if (extra) header.append(extra);
      sec.append(header);
      return sec;
    }

    function build() {
      root.replaceChildren();
      // Board
      const boardActions = el('div', null, 'aa-actions');
      nodes['run-now'] = button('Run now', runNow, 'primary');
      nodes['last-run'] = el('small', '', 'aa-muted');
      boardActions.append(nodes['last-run'], nodes['run-now']);
      const board = section('board', 'AGENT APP', 'Board', boardActions);
      const toolbar = el('div', null, 'aa-toolbar');
      const filter = el('input');
      filter.type = 'search'; filter.placeholder = 'Filter rows'; filter.setAttribute('aria-label', 'Filter rows');
      filter.addEventListener('input', () => { filterText = filter.value.trim().toLowerCase(); renderBoard(); });
      nodes['filter'] = filter;
      nodes['board-kpis'] = el('div', null, 'aa-kpis');
      toolbar.append(filter);
      nodes['board-table'] = el('div', null, 'aa-table-wrap');
      nodes['last-result'] = el('div', null, 'aa-last');
      board.append(nodes['last-result'], nodes['board-kpis'], toolbar, nodes['board-table']);

      // Chat
      const chatView = section('chat', 'TALK TO THIS APP', 'Chat');
      nodes['chat-host'] = el('div', null, 'aa-chat-host');
      chatView.append(nodes['chat-host']);

      // Settings
      const settings = section('settings', 'HOW IT RUNS', 'Settings');
      const form = el('form', null, 'aa-panel aa-form');
      nodes['settings-form'] = form;
      const grid = el('div', null, 'aa-grid');
      const field = (key, label, type, hint) => {
        const wrap = el('label', null, 'aa-field');
        wrap.append(el('span', label));
        let input;
        if (type === 'textarea') { input = el('textarea'); input.rows = key === 'prompt' ? 14 : 3; }
        else if (type === 'checkbox') { input = el('input'); input.type = 'checkbox'; wrap.classList.add('aa-check'); }
        else if (type === 'select') { input = el('select'); input.append(new Option('default', 'default'), new Option('deep', 'deep')); }
        else { input = el('input'); input.type = 'text'; }
        input.name = key;
        input.addEventListener('input', () => { settingsDirty = true; });
        input.addEventListener('change', () => { settingsDirty = true; });
        nodes['set-' + key] = input;
        if (type === 'checkbox') wrap.prepend(input); else wrap.append(input);
        if (hint) wrap.append(el('small', hint, 'aa-muted'));
        return wrap;
      };
      grid.append(
        field('name', 'Name', 'text'),
        field('icon', 'Icon', 'text', 'One character for the sidebar'),
        field('fields', 'Fields', 'text', 'Comma separated columns for the board'),
        field('key', 'Key field', 'text', 'The field that makes a record unique (e.g. url)'),
        field('model', 'Model', 'select', 'Any model you have a key for (Connectors, Models)'),
      );
      const flags = el('div', null, 'aa-flags');
      flags.append(field('notify', 'New finds go to my Inbox', 'checkbox'), field('web', 'May search the web', 'checkbox'));

      // When it runs: triggers
      const triggersBox = el('div', null, 'aa-block');
      triggersBox.append(el('h3', 'When it runs', 'aa-subhead'), el('p', 'Any of these starts a run. A schedule, new mail matching a Gmail search, or a command you send it from Telegram.', 'aa-muted'));
      nodes['triggers'] = el('div', null, 'aa-triggers');
      const addTrigger = el('div', null, 'aa-actions');
      addTrigger.append(
        button('+ Schedule', () => { nodes['triggers'].append(triggerRow({ type: 'schedule', every: '1d' })); settingsDirty = true; }, 'subtle'),
        button('+ Email', () => { nodes['triggers'].append(triggerRow({ type: 'email', query: '', every: '30m' })); settingsDirty = true; }, 'subtle'),
        button('+ Telegram command', () => { nodes['triggers'].append(triggerRow({ type: 'telegram', command: '/' + appId.replace(/-/g, '') })); settingsDirty = true; }, 'subtle'));
      triggersBox.append(nodes['triggers'], addTrigger);

      // What it can reach: connectors, and what it may do without asking: policy
      const reachBox = el('div', null, 'aa-block');
      reachBox.append(el('h3', 'What it can reach', 'aa-subhead'), el('p', 'Tick the connectors this bot may use. Set them up in Connectors first.', 'aa-muted'));
      nodes['connectors'] = el('div', null, 'aa-connectors');
      reachBox.append(nodes['connectors']);
      const policyBox = el('div', null, 'aa-block');
      policyBox.append(el('h3', 'Do it, or ask me first', 'aa-subhead'), el('p', 'For each thing that acts out in the world. Asking sends an approval to your Inbox and your phone. Some actions always ask, whatever this says.', 'aa-muted'));
      nodes['policy'] = el('div', null, 'aa-policy');
      policyBox.append(nodes['policy']);

      form.append(field('description', 'Description', 'textarea'), grid, flags, triggersBox, reachBox, policyBox, field('prompt', 'Standing instructions (prompt)', 'textarea'));
      const actions = el('div', null, 'aa-actions aa-form-actions');
      const save = el('button', 'Save', 'button primary'); save.type = 'submit';
      actions.append(button('Delete app', deleteApp, 'subtle'), save);
      form.append(actions);
      form.addEventListener('submit', (event) => { event.preventDefault(); saveSettings(); });
      settings.append(form);

      root.append(board, chatView, settings);
      root.addEventListener('module:focus', () => { if (currentView === 'chat' && ensureChat()) chat.focus(); });
    }

    const osSnap = () => (host && typeof host.os === 'function' ? host.os() : {}) || {};
    const osConnectors = () => list(osSnap().connectors);
    const osModels = () => list(osSnap().models);
    const specTriggers = (s) => {
      const out = list(s.triggers).filter((x) => x && typeof x === 'object');
      if (out.length) return out;
      if (s.every) return [{ type: 'schedule', every: s.every }];
      if (s.at) return [{ type: 'schedule', at: s.at }];
      return [];
    };
    const minutes = (every) => { const m = /^(\d+)([mhd])$/.exec(String(every || '')); if (!m) return 30; return Number(m[1]) * ({ m: 1, h: 60, d: 1440 })[m[2]]; };

    function triggerRow(trigger) {
      const row = el('div', null, 'aa-trigger');
      const kind = trigger.type === 'email' || trigger.type === 'telegram' ? trigger.type : 'schedule';
      row.dataset.type = kind;
      const mark = () => { settingsDirty = true; };
      const input = (value, placeholder, label, width) => { const i = el('input'); i.type = 'text'; i.value = value || ''; i.placeholder = placeholder; i.setAttribute('aria-label', label); if (width) i.style.maxWidth = width; i.addEventListener('input', mark); return i; };
      if (kind === 'schedule') {
        const mode = el('select'); mode.setAttribute('aria-label', 'Schedule kind');
        mode.append(new Option('Every', 'every'), new Option('Daily at', 'at'));
        mode.value = trigger.at ? 'at' : 'every';
        const amount = el('input'); amount.type = 'number'; amount.min = '1'; amount.max = '720'; amount.setAttribute('aria-label', 'Hours'); amount.style.maxWidth = '90px';
        const m = /^(\d+)([mhd])$/.exec(String(trigger.every || '24h'));
        amount.value = m ? String(m[2] === 'd' ? Number(m[1]) * 24 : m[2] === 'm' ? Math.max(1, Math.round(Number(m[1]) / 60)) : Number(m[1])) : '24';
        const unit = el('span', 'hours', 'aa-muted');
        const time = el('input'); time.type = 'time'; time.value = trigger.at || '07:00'; time.setAttribute('aria-label', 'Time of day');
        const sync = () => { const at = mode.value === 'at'; amount.hidden = at; unit.hidden = at; time.hidden = !at; };
        mode.addEventListener('change', () => { sync(); mark(); });
        amount.addEventListener('input', mark); time.addEventListener('input', mark);
        sync();
        row.append(el('strong', 'Schedule'), mode, amount, unit, time);
        row.read = () => mode.value === 'at'
          ? { type: 'schedule', at: time.value || '07:00' }
          : { type: 'schedule', every: (() => { const h = Math.max(1, parseInt(amount.value, 10) || 24); return h % 24 === 0 ? `${h / 24}d` : `${h}h`; })() };
      } else if (kind === 'email') {
        const query = input(trigger.query, 'Gmail search, e.g. from:(submittable OR callforentry) newer_than:2d', 'Gmail search');
        const every = el('input'); every.type = 'number'; every.min = '5'; every.max = '1440'; every.value = String(minutes(trigger.every)); every.style.maxWidth = '90px'; every.setAttribute('aria-label', 'Check every N minutes');
        every.addEventListener('input', mark);
        row.append(el('strong', 'Email'), query, el('span', 'check every', 'aa-muted'), every, el('span', 'min', 'aa-muted'));
        row.read = () => ({ type: 'email', query: query.value.trim(), every: `${Math.max(5, parseInt(every.value, 10) || 30)}m` });
      } else {
        const command = input(trigger.command, '/scout', 'Telegram command', '200px');
        row.append(el('strong', 'Telegram'), el('span', 'when I send', 'aa-muted'), command);
        row.read = () => { const c = command.value.trim().replace(/\s+/g, ''); return { type: 'telegram', command: c.startsWith('/') ? c : '/' + c }; };
      }
      const remove = button('×', () => { row.remove(); settingsDirty = true; }, 'subtle');
      remove.setAttribute('aria-label', 'Remove trigger');
      row.append(remove);
      return row;
    }

    function chosenConnectors() {
      return [...nodes['connectors'].querySelectorAll('input[type="checkbox"]')].filter((box) => box.checked).map((box) => box.value);
    }

    function renderConnectorChoices(s) {
      const allowed = s.connectors === 'all' ? osConnectors().map((c) => c.id) : list(s.connectors);
      const connectors = osConnectors();
      if (!connectors.length) { nodes['connectors'].replaceChildren(el('p', 'No connectors reported yet. Set them up in Connectors.', 'aa-muted')); return; }
      nodes['connectors'].replaceChildren(...connectors.map((c) => {
        const ready = c.enabled !== false && c.status && c.status.ok;
        const wrap = el('label', null, 'aa-conn' + (c.enabled === false ? ' aa-conn-off' : ''));
        const box = el('input'); box.type = 'checkbox'; box.value = c.id; box.checked = allowed.includes(c.id);
        box.disabled = c.enabled === false;
        box.addEventListener('change', () => { settingsDirty = true; renderPolicy(currentPolicy()); });
        wrap.append(box, el('span', c.name || c.id));
        if (c.enabled === false) { const a = el('a', 'set up in Connectors'); a.href = '#connectors'; wrap.append(a); }
        else if (!ready) wrap.append(el('small', 'needs setup', 'aa-muted'));
        return wrap;
      }));
    }

    function currentPolicy() {
      const out = {};
      nodes['policy'].querySelectorAll('select[data-tool]').forEach((select) => { out[select.dataset.tool] = select.value; });
      return Object.keys(out).length ? out : null;
    }

    function renderPolicy(policy) {
      const p = policy && typeof policy === 'object' ? policy : {};
      const chosen = chosenConnectors();
      const rows = [];
      for (const c of osConnectors().filter((x) => chosen.includes(x.id))) {
        for (const tool of list(c.tools).filter((x) => x && x.action)) rows.push({ name: `${c.id}.${tool.name}`, label: `${c.name || c.id}: ${tool.name.replace(/_/g, ' ')}`, connector: c.id });
      }
      const lookup = (name, connector) => p[name] || p[`${connector}.*`] || p['*'] || 'ask';
      const select = (name, value) => {
        const s = el('select'); s.dataset.tool = name; s.setAttribute('aria-label', name);
        s.append(new Option('Do it', 'auto'), new Option('Ask me first', 'ask'));
        s.value = value === 'auto' ? 'auto' : 'ask';
        s.addEventListener('change', () => { settingsDirty = true; });
        return s;
      };
      const table = el('table', null, 'aa-table aa-policy-table');
      const body = el('tbody');
      for (const r of rows) {
        const tr = el('tr');
        const name = el('td'); name.append(el('span', r.label), el('code', ` ${r.name}`, 'aa-muted'));
        const choice = el('td'); choice.append(select(r.name, lookup(r.name, r.connector)));
        tr.append(name, choice); body.append(tr);
      }
      const dtr = el('tr');
      const dname = el('td'); dname.append(el('span', 'Anything else'), el('code', ' *', 'aa-muted'));
      const dchoice = el('td'); dchoice.append(select('*', p['*'] || 'ask'));
      dtr.append(dname, dchoice); body.append(dtr);
      table.append(body);
      nodes['policy'].replaceChildren(rows.length ? table : el('p', 'The connectors you ticked only read. Nothing to approve.', 'aa-muted'), ...(rows.length ? [] : [table]));
    }

    function renderModelChoices(s) {
      const select = nodes['set-model'];
      const models = osModels();
      const current = s.model || 'default';
      const options = [new Option('OS default', 'default'), new Option('OS deep (for hard judgment)', 'deep'),
        ...models.map((m) => { const o = new Option(`${m.label || m.ref}${m.configured ? '' : ' (needs key)'}`, m.ref); o.disabled = !m.configured && m.ref !== current; return o; })];
      if (!options.some((o) => o.value === current)) options.push(new Option(current, current));
      select.replaceChildren(...options);
      select.value = current;
    }

    function ensureChat() {
      if (chat || !host || !host.chat || !nodes['chat-host']) return chat;
      chat = host.chat.mount(nodes['chat-host'], { app: appId, agent: null, agents: [], compact: false, title: spec().name || appId, placeholder: 'Ask about what it found, or tell it what to look for from now on.' });
      return chat;
    }

    async function runNow() {
      const control = nodes['run-now'];
      control.disabled = true;
      const label = control.textContent;
      control.textContent = 'Running… (web search can take a minute)';
      try {
        const result = await host.action({ action: 'app.run' });
        if (result && result.error) { host.announce(`${spec().name || appId}: run failed: ${result.error}`, true); host.refresh(); return; }
        const summary = result && typeof result === 'object'
          ? (result.message || `${(result.changes && result.changes.new) ?? result.new ?? result.added ?? 0} new, ${(result.changes && result.changes.updated) ?? result.updated ?? 0} updated${result.usage ? ` · ${result.usage.input || 0} in / ${result.usage.output || 0} out` : ''}`)
          : 'Run finished.';
        host.announce(`${spec().name || appId}: ${summary}`);
        host.refresh();
      } catch (e) {
        host.announce(e.message || String(e), true);
      } finally { control.disabled = false; control.textContent = label; }
    }

    async function setRecord(id, patch) {
      try { await host.action({ action: 'record.set', id, fields: patch }); host.refresh(); }
      catch (e) { host.announce(e.message || String(e), true); }
    }
    async function deleteRecord(record) {
      if (!window.confirm(`Delete "${record.title || record.id}" from ${spec().name || appId}? The app may find it again on its next run unless you mark it instead.`)) return;
      try { await host.action({ action: 'record.delete', id: record.id }); host.refresh(); }
      catch (e) { host.announce(e.message || String(e), true); }
    }

    function renderBoard() {
      const s = spec();
      nodes['board-title'].textContent = s.name || appId;
      nodes['board-eyebrow'].textContent = (s.description || 'AGENT APP').toUpperCase().slice(0, 90);
      const lastRun = snapshot.last_run && typeof snapshot.last_run === 'object' ? (snapshot.last_run.finished || snapshot.last_run.started) : snapshot.last_run;
      nodes['last-run'].textContent = `last run ${timeLabel(lastRun)}${s.every ? ` · every ${s.every}` : s.at ? ` · daily at ${s.at}` : ''}`;
      const last = snapshot.last_run && typeof snapshot.last_run === 'object' ? snapshot.last_run : null;
      const result = nodes['last-result'];
      result.replaceChildren();
      result.className = 'aa-last';
      if (!last) {
        result.append(el('p', 'It has not run yet. Press Run now, or wait: a new app runs about a minute after it appears.', 'aa-muted'));
      } else if (last.error) {
        result.classList.add('aa-last-error');
        result.append(el('strong', 'The last run failed.'), el('p', last.error), el('p', 'Check the model with ./hq doctor (API key, model, web search on your account), then Run now.', 'aa-muted'));
      } else {
        const ch = (last.changes && (last.changes.counts || last.changes)) || {};
        result.append(el('strong', `Last run: ${ch.new ?? 0} new, ${ch.updated ?? 0} updated`), el('p', last.answer || 'No summary.'));
      }
      const counts = snapshot.counts && typeof snapshot.counts === 'object' ? snapshot.counts : {};
      nodes['board-kpis'].replaceChildren(...[['Records', counts.records ?? records().length], ['New today', counts.new_today ?? 0]].map(([label, value]) => {
        const box = el('div', null, 'aa-kpi');
        box.append(el('span', label, 'aa-kpi-label'), el('strong', String(value)));
        return box;
      }));
      const cols = fields();
      let rows = records().slice();
      if (filterText) rows = rows.filter((r) => cols.some((c) => String(r[c] ?? '').toLowerCase().includes(filterText)) || String(r.id || '').toLowerCase().includes(filterText));
      if (sort.key) rows.sort((a, b) => String(a[sort.key] ?? '').localeCompare(String(b[sort.key] ?? ''), undefined, { numeric: true }) * sort.dir);
      else rows.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')));
      const table = el('table', null, 'aa-table');
      const thead = el('thead'); const tr = el('tr');
      for (const c of cols) {
        const th = el('th', c + (sort.key === c ? (sort.dir > 0 ? ' ↑' : ' ↓') : ''));
        th.tabIndex = 0;
        th.addEventListener('click', () => { if (sort.key === c) sort.dir = -sort.dir; else { sort.key = c; sort.dir = 1; } renderBoard(); });
        tr.append(th);
      }
      tr.append(el('th', ''));
      thead.append(tr); table.append(thead);
      const tbody = el('tbody');
      const statuses = ['new', 'open', 'considering', 'entered', 'done', 'skip'];
      for (const r of rows) {
        const row = el('tr');
        if (r.status === 'new') row.classList.add('aa-new');
        for (const c of cols) {
          const td = el('td');
          const value = r[c];
          if (c === 'status') {
            const select = el('select');
            const options = statuses.includes(String(value || '')) ? statuses : [String(value || 'new'), ...statuses];
            for (const o of options) select.append(new Option(o, o));
            select.value = String(value || 'new');
            select.addEventListener('change', () => setRecord(r.id, { status: select.value }));
            td.append(select);
          } else if (c === 'url' && typeof value === 'string' && /^https?:\/\//.test(value)) {
            const a = el('a', value.replace(/^https?:\/\//, '').slice(0, 60)); a.href = value; a.target = '_blank'; a.rel = 'noopener noreferrer';
            td.append(a);
          } else {
            td.textContent = value == null ? '' : String(value);
            td.title = td.textContent;
          }
          row.append(td);
        }
        const act = el('td', null, 'aa-row-actions');
        act.append(button('Ask', () => { host.navigate('chat'); if (ensureChat()) chat.send(`About "${r.title || r.id}": tell me more, and whether it is worth my time.`); }, 'subtle'),
                   button('×', () => deleteRecord(r), 'subtle'));
        row.append(act);
        tbody.append(row);
      }
      table.append(tbody);
      nodes['board-table'].replaceChildren(rows.length ? table : el('p', records().length ? 'No rows match the filter.' : 'Nothing yet. Press Run now, or wait for the schedule.', 'aa-muted'));
    }

    function renderSettings() {
      if (settingsDirty) return; // do not stomp on edits in progress
      if (nodes['settings-form'] && nodes['settings-form'].contains(document.activeElement)) return;
      const s = spec();
      nodes['settings-title'].textContent = `Settings · ${s.name || appId}`;
      nodes['set-name'].value = s.name || '';
      nodes['set-icon'].value = s.icon || '';
      nodes['set-description'].value = s.description || '';
      nodes['set-fields'].value = fields().join(', ');
      nodes['set-key'].value = s.key || '';
      renderModelChoices(s);
      nodes['triggers'].replaceChildren(...specTriggers(s).map(triggerRow));
      if (!nodes['triggers'].children.length) nodes['triggers'].append(el('p', 'No trigger: it only runs when you press Run now.', 'aa-muted aa-empty-trigger'));
      renderConnectorChoices(s);
      renderPolicy(s.policy);
      nodes['set-notify'].checked = s.notify !== false;
      nodes['set-web'].checked = s.web !== false;
      nodes['set-prompt'].value = snapshot.prompt || '';
    }
    async function saveSettings() {
      const patch = {
        name: nodes['set-name'].value.trim(),
        icon: nodes['set-icon'].value.trim().slice(0, 2),
        description: nodes['set-description'].value.trim(),
        fields: nodes['set-fields'].value.split(',').map((x) => x.trim()).filter(Boolean),
        key: nodes['set-key'].value.trim(),
        model: nodes['set-model'].value,
        notify: nodes['set-notify'].checked,
        web: nodes['set-web'].checked,
      };
      const triggers = [...nodes['triggers'].querySelectorAll('.aa-trigger')].map((row) => row.read()).filter(Boolean);
      const bad = triggers.find((x) => x.type === 'email' && !x.query);
      if (bad) { host.announce('An email trigger needs a Gmail search.', true); return; }
      const schedule = triggers.find((x) => x.type === 'schedule');
      patch.triggers = triggers;
      patch.every = schedule && schedule.every ? schedule.every : null;
      patch.at = schedule && schedule.at ? schedule.at : null;
      patch.connectors = chosenConnectors();
      patch.policy = currentPolicy() || { '*': 'ask' };
      try {
        await host.action({ action: 'app.update', spec: patch, prompt: nodes['set-prompt'].value });
        settingsDirty = false;
        host.announce(`${patch.name || appId} saved.`);
        host.refresh();
      } catch (e) { host.announce(e.message || String(e), true); }
    }
    async function deleteApp() {
      const name = spec().name || appId;
      const typed = window.prompt(`Type the app's id to delete it: ${appId}\nIts folder moves to hq/os/apps/_deleted/; records stay in the brain.`);
      if (typed !== appId) { if (typed !== null) host.announce('Not deleted: the id did not match.', true); return; }
      try {
        await host.action({ action: 'app.delete' });
        host.announce(`${name} deleted.`);
        location.hash = '#overview';
        host.refresh();
      } catch (e) { host.announce(e.message || String(e), true); }
    }

    function showView(name) {
      currentView = ['board', 'chat', 'settings'].includes(name) ? name : 'board';
      root.querySelectorAll('.aa-view').forEach((v) => { v.hidden = v.dataset.view !== currentView; });
      if (currentView === 'chat' && ensureChat()) chat.focus();
    }

    return {
      mount(container, hostApi) { host = hostApi; root = container; build(); showView(currentView); },
      render(moduleSnapshot, view) {
        snapshot = moduleSnapshot || {};
        if (view) showView(view);
        if (snapshot.error) { host.announce(`${appId}: ${snapshot.error}`, true); return; }
        try { renderBoard(); } catch (e) { console.error('agent app board', appId, e); }
        try { renderSettings(); } catch (e) { console.error('agent app settings', appId, e); }
        if (currentView === 'chat') ensureChat();
      },
      unmount() { if (chat) chat.destroy(); chat = null; if (root) root.replaceChildren(); host = null; root = null; },
    };
  }

  window.OSModules = window.OSModules || {};
  window.OSModules.__agentApp = createAgentApp;
})();
