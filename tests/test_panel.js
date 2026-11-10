const assert = require('node:assert/strict');
const vm = require('node:vm');
const { version, loadScript } = require('./scripts');
const { test } = require('node:test');

function fixture(preferences = {}) {
  const context = {
    window: { addEventListener() {}, removeEventListener() {} },
    document: { body: {}, head: { appendChild() {} }, querySelector: () => null,
      querySelectorAll: () => [], getElementById: () => null,
      createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
      addEventListener() {}, removeEventListener() {} },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: () => 1, clearInterval() {}, Date, Math, Promise
  };
  vm.runInNewContext(loadScript('settings.js'), context);
  context.window.__WUT_SETTINGS__.update({ preferences, ack: [] });
  return { settings: context.window.__WUT_SETTINGS__, context };
}

test('settings use the product version and replace different script versions only once', () => {
  const f = fixture();
  const old = f.settings;
  assert.equal(old.version, version);
  vm.runInNewContext(loadScript('settings.js'), f.context);
  assert.equal(f.context.window.__WUT_SETTINGS__, old);
  old.version = '0.0.0';
  let uninstalled = 0;
  const uninstall = old.uninstall;
  old.uninstall = () => { uninstalled++; uninstall(); };
  vm.runInNewContext(loadScript('settings.js'), f.context);
  assert.equal(uninstalled, 1);
  assert.equal(f.context.window.__WUT_SETTINGS__.version, version);
  assert.notEqual(f.context.window.__WUT_SETTINGS__, old);
});

test('atlas preview maps an icon file to the real appearance entry', () => {
  const f = fixture();
  const atlas = f.context.window.__WUT_ATLAS__;
  assert.ok(atlas);
  const payload = {
    character_skin_atlas: { 1123: { url: 'character_atlas/a.png?v=1', height_light_id: 8 } },
    character_skin: { 8: { game_role_id: 1107,
      body1: { url: 'character/b1.png' }, body2: { url: 'character/b2.png' },
      body3: { url: 'character/b3.png' }, head1: { url: 'character/h1.png' },
      head2: { url: 'character/h2.png' } } },
    accessory_atlas: { 1390: { url: 'accessory_atlas/x.png', height_light_id: 1345 } },
    accessory: { 1345: { url: 'accessory/x.png' } },
    emote_atlas: { 1774: { url: 'emote_atlas/e.png', height_light_id: 1615 } },
    emote: { 1615: { url: 'emote/e.png' } }
  };
  const found = atlas._test.findAtlas(payload, 'http://host/character_atlas/a.png?t=2');
  assert.equal(found.actualType, 'character_skin');
  assert.equal(found.height, 8);
  assert.equal(atlas._test.actualOf(payload, found).game_role_id, 1107);
  const acc = atlas._test.findAtlas(payload, 'http://host/accessory_atlas/x.png');
  assert.equal(atlas._test.actualOf(payload, acc).url, 'accessory/x.png');
  const emote = atlas._test.findAtlas(payload, 'http://host/emote_atlas/e.png');
  assert.equal(atlas._test.actualOf(payload, emote).url, 'emote/e.png');
  const current = atlas._test.currentCharacter(payload, { role_id: '1107', character: { name: 'h1.png' } });
  assert.equal(current.game_role_id, 1107);
  assert.equal(atlas._test.offsetOf({ my_offset_x: 2, my_offset_y: 3 }).join(','), '2,3');
  assert.equal(atlas._test.offsetOf({ offset_x: -2, offset_y: 16 }).join(','), '-2,16');
});

test('daily report renders zero, missing history and unavailable storage distinctly', () => {
  for (const [count, available, expected] of [[0, true, '0'], [null, true, '—'], [7, false, '—']]) {
    const f = fixture();
    const fields = {
      '.wut-report-title': {}, '.wut-report-count': {}, '.wut-report-note': {},
      '.wut-report-chart': { appendChild() {} }
    };
    const status = { dataset: {} };
    const settingsHost = { querySelectorAll: () => [], querySelector: () => status };
    const reportHost = { querySelector: selector => fields[selector] };
    f.context.document.getElementById = id => id === 'wut-plus-settings' ? settingsHost :
      id === 'wut-daily-report' ? reportHost : null;
    f.settings.update({ preferences: {}, ack: [], report: {
      available, yesterday_date: '2026-10-26', yesterday_count: count, days: []
    }});
    assert.equal(fields['.wut-report-count'].textContent, expected);
    assert.match(fields['.wut-report-title'].textContent, /2026-10-26/);
    if (!available) assert.match(fields['.wut-report-note'].textContent, /暂不可用/);
  }
});

test('chart orders dates, scales bars, distinguishes missing/zero and keeps precise tooltips', () => {
  const f = fixture();
  function element() {
    const result = { children: [], style: {}, attributes: {},
      appendChild(child) { this.children.push(child); },
      setAttribute(name, value) { this.attributes[name] = value; }
    };
    Object.defineProperty(result, 'textContent', { set(value) {
      this.text = value; if (value === '') this.children = [];
    }, get() { return this.text; } });
    return result;
  }
  const chart = element();
  const fields = { '.wut-report-title': {}, '.wut-report-count': {},
    '.wut-report-note': {}, '.wut-report-chart': chart };
  const status = { dataset: {} };
  f.context.document.createElement = element;
  f.context.document.getElementById = id => id === 'wut-plus-settings' ?
    { querySelectorAll: () => [], querySelector: () => status } :
    id === 'wut-daily-report' ? { querySelector: selector => fields[selector] } : null;
  const days = [20001, 0, null, 5, 2, 1, 4].map((count, i) => ({
    date: '2026-10-' + (27 - i), count
  }));
  f.settings.update({ preferences: {}, ack: [], report: {
    available: true, yesterday_date: '2026-10-26', yesterday_count: 0, days
  }});
  assert.equal(chart.children.length, 7);
  assert.equal(chart.children[0].children[2].textContent, '10/21');
  assert.equal(chart.children[6].children[2].textContent, '今天');
  assert.match(chart.children[6].title, /20001 次/);
  assert.equal(chart.children[6].children[1].children[0].style.height, '100%');
  assert.equal(chart.children[6].children[0].textContent, '2.0万');
  assert.match(chart.children[4].children[1].className, /wut-chart-missing/);
  assert.equal(chart.children[4].children[0].textContent, '—');
  assert.match(chart.children[5].children[1].className, /wut-chart-zero/);
  assert.equal(chart.children[5].children[0].textContent, '0');
  assert.equal(days[0].date, '2026-10-27'); // rendering must not reorder the payload
});

test('settings export queues one action, shows result, and permits retry after failure', () => {
  const f = fixture();
  const nodes = new Map();
  function element() {
    return { dataset: {}, style: { setProperty() {} }, children: [], textContent: '',
      setAttribute() {}, addEventListener(name, handler) { this[name] = handler; },
      appendChild(child) { this.children.push(child); if (child.id) nodes.set(child.id, child); },
      querySelectorAll() { return []; },
      querySelector(selector) {
        if (selector === '.wut-status') return this.children[0].children[1];
        return null;
      }
    };
  }
  const container = element();
  container.querySelector = () => null;
  container.insertBefore = child => container.appendChild(child);
  f.context.document.createElement = element;
  f.context.document.head = element();
  f.context.document.querySelector = () => container;
  // Report internals are unrelated to the export control.
  f.context.document.getElementById = id => id === 'wut-daily-report' ? null : nodes.get(id);
  f.settings.update({ preferences: {}, ack: [] });
  const button = nodes.get('wut-export-button');
  const status = nodes.get('wut-export-status');
  assert.equal(button.disabled, false);
  button.click(); button.click();
  assert.equal(f.settings.requests.length, 1);
  assert.equal(f.settings.requests[0].action, 'export_logs');
  assert.equal(button.disabled, true);
  assert.match(status.textContent, /正在导出/);
  let id = f.settings.requests[0].id;
  f.settings.update({ preferences: {}, ack: [{ id, action: 'export_logs', error: true }] });
  assert.match(status.textContent, /导出失败/);
  assert.equal(button.disabled, false);
  button.click();
  id = f.settings.requests[0].id;
  f.settings.update({ preferences: {}, ack: [{ id, action: 'export_logs', path: 'C:/Desktop/logs.zip' }] });
  assert.match(status.textContent, /已导出：C:\/Desktop\/logs.zip/);
  assert.equal(f.settings.requests.length, 0);
  f.settings.beat = 0;
  button.click();
  assert.equal(f.settings.requests.length, 0);
});


test('panel never clicks official game switches and cannot overwrite stored settings', () => {
  const f = fixture();
  let clicks = 0;
  let reads = 0;
  const auto = { checked: true, click() { clicks += 1; this.checked = !this.checked; } };
  const mode = { checked: false, click() { clicks += 1; this.checked = !this.checked; } };
  const row = (title, input) => ({ querySelector: selector =>
    selector === '.setting-title' ? { textContent: title } : input });
  f.context.localStorage = { getItem: () => { reads += 1; return JSON.stringify({
    gameMode: false, autoGameModeOnFullscreen: false
  }); } };
  f.context.document.querySelector = selector =>
    selector === '.settings-module .panel-content' ? {
      querySelectorAll: () => [row('全屏进入游戏模式', auto), row('游戏模式', mode)]
    } : null;
  f.settings.uninstall();
  assert.equal(clicks, 0);
  assert.equal(auto.checked, true);
  assert.equal(mode.checked, false);
  // The panel does not own the app's settings, so it does not even read them.
  assert.equal(reads, 0);
  // A click before the app's component loads its stored values would save the
  // defaults it was initialized with and wipe taskbar icon, opacity and size.
  assert.doesNotMatch(loadScript('settings.js'), /\.click\s*\(/);
});


test('token login notifies the pet window to hydrate the new session', async () => {
  const f = fixture();
  const calls = [];
  f.context.window.__TAURI_INTERNALS__ = {
    invoke: (command, args) => {
      calls.push({ command, args });
      return Promise.resolve();
    }
  };
  await f.settings._test.emitLoginSuccess();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, 'plugin:event|emit');
  assert.equal(calls[0].args.event, 'cat-auth-login-success');
  // Login must emit the official success event before reloading the panel.
  assert.match(loadScript('settings.js'), /applyAuthToken\(token\)\.then\(emitLoginSuccess\)/);
});


test('token row shows expiry and uses the official toast for extract/renew', async () => {
  const f = fixture();
  const nodes = new Map();
  const alerts = [];
  let copied = null;
  function element() {
    return { dataset: {}, style: { setProperty() {} }, children: [], textContent: '',
      setAttribute() {}, addEventListener(name, handler) { this[name] = handler; },
      appendChild(child) { this.children.push(child); if (child.id) nodes.set(child.id, child); },
      querySelectorAll() { return []; },
      querySelector(selector) {
        if (selector === '.wut-status') return this.children[0].children[1];
        return null;
      }
    };
  }
  const container = element();
  container.querySelector = () => null;
  container.insertBefore = child => container.appendChild(child);
  f.context.document.createElement = element;
  f.context.document.head = element();
  f.context.document.querySelector = () => container;
  f.context.document.getElementById = id =>
    id === 'wut-daily-report' ? null : nodes.get(id);
  f.context.document.cookie = 'kuro_infoToken=info-token-123; kuro_lastLoginTime=' +
    Math.floor((Date.now() + 3.5 * 86400000) / 1000);
  f.context.localStorage = {
    length: 1,
    key: () => 'credentials_zeus-free-test',
    getItem: key => key === 'credentials_zeus-free-test'
      ? JSON.stringify({ access_token: 'access-token-456' }) : null
  };
  f.context.navigator = {
    clipboard: { writeText: text => { copied = text; return Promise.resolve(); } }
  };
  f.context.window.alert = message => { alerts.push(message); };
  f.settings.update({ preferences: {}, ack: [] });
  const expiry = nodes.get('wut-token-expiry');
  const extract = nodes.get('wut-token-extract');
  const renew = nodes.get('wut-token-renew');
  const loginRow = nodes.get('wut-token-login-row');
  assert.match(expiry.textContent, /当前Token有效期：剩余3天/);
  assert.equal(expiry.dataset.soon, 'false');
  assert.equal(renew.hidden, false);
  assert.equal(renew.style.display, '');
  // No app store in this fixture means the state is unknown, so the recovery
  // row stays available rather than disappearing for a logged-out client.
  assert.equal(loginRow.hidden, false);
  assert.equal(loginRow.style.display, '');
  extract.click();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(copied, 'info-token-123');
  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /Token已提取至剪贴板/);
  assert.match(alerts[0], /请勿分享给他人/);
  f.context.document.cookie = 'kuro_infoToken=info-token-123; kuro_lastLoginTime=' +
    Math.floor((Date.now() + 86400000) / 1000);
  f.settings.update({ preferences: {}, ack: [] });
  assert.equal(renew.hidden, false);
  assert.equal(renew.style.display, '');
  renew.click();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(alerts[alerts.length - 1], /续期失败/);
  assert.equal(renew.disabled, false);
  const loginInput = nodes.get('wut-token-login-input');
  const loginButton = nodes.get('wut-token-login-button');
  loginInput.value = 'phone-token-123';
  loginButton.click();
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.match(alerts[alerts.length - 1], /Token 登录失败/);

  // Once the app reports a session, keep the recovery row out of the way.
  const store = { isLoggedIn: true };
  const app = { __vue_app__: { config: { globalProperties: {
    $pinia: { _s: new Map([['GlobalState', store]]) }
  } } } };
  const baseQuery = f.context.document.querySelector;
  f.context.document.querySelector = selector => selector === '#app' ? app : baseQuery(selector);
  f.settings.update({ preferences: {}, ack: [] });
  assert.equal(loginRow.hidden, true);
  assert.equal(loginRow.style.display, 'none');
  store.isLoggedIn = false;
  f.settings.update({ preferences: {}, ack: [] });
  assert.equal(loginRow.hidden, false);
  assert.equal(loginRow.style.display, '');
});

test('auto game mode switch owns a whitelist of programs', () => {
  const f = fixture();
  const nodes = new Map();
  function element() {
    return { dataset: {}, style: { setProperty() {} }, children: [], textContent: '',
      setAttribute() {}, addEventListener(name, handler) { this[name] = handler; },
      appendChild(child) { this.children.push(child); if (child.id) nodes.set(child.id, child); },
      querySelectorAll() { return []; },
      querySelector(selector) {
        if (selector === '.wut-status') return { dataset: {}, textContent: '' };
        return null;
      }
    };
  }
  const container = element();
  container.insertBefore = child => container.appendChild(child);
  f.context.document.createElement = element;
  f.context.document.head = element();
  f.context.document.querySelector = () => container;
  f.context.document.getElementById = id =>
    id === 'wut-daily-report' ? null : nodes.get(id);
  f.settings.update({ preferences: { game_mode_apps: ['game.exe'] }, ack: [] });
  assert.equal(nodes.get('wut-game-apps-add').textContent, '添加最近使用的程序');
  assert.equal(nodes.get('wut-game-apps-add').disabled, true);

  f.settings.update({ preferences: { game_mode_apps: ['game.exe'] },
                      foreground_app: 'game.exe', ack: [] });
  assert.equal(nodes.get('wut-game-apps-add').textContent, '添加 game.exe');
  assert.equal(nodes.get('wut-game-apps-add').disabled, false);

  assert.equal(nodes.get('wut-setting-game-mode-apps').textContent, '自动游戏模式白名单');
  const list = nodes.get('wut-game-apps');
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].children[0].textContent, 'game.exe');
  assert.equal(nodes.get('wut-game-apps-empty').style.display, 'none');

  list.children[0].children[1].click();
  assert.equal(f.settings.requests.length, 1);
  assert.equal(f.settings.requests[0].key, 'game_mode_apps');
  assert.deepEqual(f.settings.requests[0].value, []);
  f.settings.update({ preferences: { game_mode_apps: [] }, ack: [] });
  assert.equal(nodes.get('wut-game-apps-empty').style.display, '');

  nodes.get('wut-game-apps-add').click();
  const last = f.settings.requests[f.settings.requests.length - 1];
  assert.equal(last.action, 'whitelist_add_foreground');
  nodes.get('wut-game-apps-add').click();
  assert.equal(f.settings.requests.filter(r => r.action === 'whitelist_add_foreground').length, 1,
               'one add request at a time');

  // Typing a name works too: the panel can never be the candidate itself.
  const input = nodes.get('wut-game-apps-input');
  input.value = '  Other.EXE  ';
  nodes.get('wut-game-apps-manual').click();
  const manual = f.settings.requests[f.settings.requests.length - 1];
  assert.equal(manual.action, 'whitelist_add');
  assert.equal(manual.name, 'Other.EXE');
  assert.equal(input.value, '');
  // The old wording promised fullscreen-only behaviour.
  assert.doesNotMatch(loadScript('settings.js'), /全屏进入游戏模式·Plus/);
});

