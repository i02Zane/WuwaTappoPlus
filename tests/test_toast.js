const assert = require('node:assert/strict');
const vm = require('node:vm');
const { test } = require('node:test');
const { version, loadScript } = require('./scripts');

function load() {
  const root = { innerText: '' };
  const window = {};
  const stopped = { observer: false, timer: false };
  const context = {
    window,
    document: {
      body: {},
      querySelector: selector => (selector === '.toast-overlay-root' ? root : null)
    },
    MutationObserver: class { observe() {} disconnect() { stopped.observer = true; } },
    setInterval: () => 1,
    clearInterval() { stopped.timer = true; },
    Date, Math
  };
  vm.runInNewContext(loadScript('toast.js'), context);
  return { toast: window.__WUT_TOAST__, root, window, stopped };
}

test('toast stream deduplicates visible text but records repeats after dismissal in order', () => {
  const { toast, root } = load();
  for (const text of ['  宝箱冷却中 \n', '宝箱冷却中', '', '宝箱冷却中', '已复制房间号']) {
    root.innerText = text;
    toast.sample();
  }
  assert.deepEqual(Array.from(toast.entries, entry => entry.m),
    ['宝箱冷却中', '宝箱冷却中', '已复制房间号']);
  assert.ok(toast.entries.every(entry => typeof entry.t === 'number'));
});

test('the script version is the product version and uninstall detaches it', () => {
  const { toast, root, window, stopped } = load();
  assert.equal(toast.version, version);
  root.innerText = '已复制';
  toast.sample();
  toast.uninstall();
  assert.equal(window.__WUT_TOAST__, undefined);
  assert.deepEqual(stopped, { observer: true, timer: true });
});
