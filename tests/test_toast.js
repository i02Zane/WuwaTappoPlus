const assert = require('node:assert/strict');
const vm = require('node:vm');
const { test } = require('node:test');
const { version, loadScript } = require('./scripts');

function load() {
  const root = { innerText: '' };
  const window = {};
  const context = {
    window,
    document: {
      body: {},
      querySelector: selector => (selector === '.toast-overlay-root' ? root : null)
    },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: () => 1,
    clearInterval() {},
    Date, Math
  };
  vm.runInNewContext(loadScript('toast.js'), context);
  return { toast: window.__WUT_TOAST__, root, window };
}

test('a toast is recorded once while it stays on screen', () => {
  const { toast, root } = load();
  root.innerText = '  宝箱冷却中 \n';
  toast.sample();
  toast.sample();
  assert.equal(toast.entries.length, 1);
  assert.equal(toast.entries[0].m, '宝箱冷却中');
  assert.equal(typeof toast.entries[0].t, 'number');
});

test('a dismissed toast that appears again is a new entry', () => {
  const { toast, root } = load();
  root.innerText = '宝箱冷却中';
  toast.sample();
  root.innerText = '';
  toast.sample();
  root.innerText = '宝箱冷却中';
  toast.sample();
  assert.deepEqual(Array.from(toast.entries, entry => entry.m),
    ['宝箱冷却中', '宝箱冷却中']);
});

test('a different message on screen is recorded in order', () => {
  const { toast, root } = load();
  root.innerText = '已复制房间号';
  toast.sample();
  root.innerText = '宝箱冷却中';
  toast.sample();
  assert.deepEqual(Array.from(toast.entries, entry => entry.m),
    ['已复制房间号', '宝箱冷却中']);
});

test('the script version is the product version and uninstall detaches it', () => {
  const { toast, root, window } = load();
  assert.equal(toast.version, version);
  root.innerText = '已复制';
  toast.sample();
  toast.uninstall();
  assert.equal(window.__WUT_TOAST__, undefined);
});
