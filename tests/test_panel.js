const assert = require('node:assert/strict');
const vm = require('node:vm');
const { version, loadScript } = require('./scripts');
const { test } = require('node:test');

function fixture(preferences = {}) {
  const context = {
    window: { addEventListener() {}, removeEventListener() {} },
    document: { body: {}, querySelector: () => null, getElementById: () => null },
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
    return { dataset: {}, style: {}, children: [], textContent: '',
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
