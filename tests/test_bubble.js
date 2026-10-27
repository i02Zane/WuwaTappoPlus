const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { test } = require('node:test');
const path = require('node:path');
const { version } = require('./scripts');

function fixture() {
  const elements = new Map();
  const calls = [];
  const posts = [];
  const frames = [];
  const pet = { left: 400, top: 300, width: 150, height: 150, bottom: 450 };
  const original = {
    regions: [{ left: 400, top: 300, width: 150, height: 160 }],
    inputRegions: [{ left: 410, top: 310, width: 100, height: 100 }],
    updateNativeInput: true, windowPassthrough: false, nativeInput: true
  };
  function node() {
    const el = {
      style: {}, className: '', offsetWidth: 160, offsetHeight: 50,
      appendChild(child) { elements.set(child.id, child); child.parentNode = this; },
      removeChild(child) { elements.delete(child.id); },
      getBoundingClientRect() {
        const left = Number.parseFloat(this.style.left || '80') - 80;
        const top = 800 - Number.parseFloat(this.style.bottom || '0') - 50;
        return { left, top, width: 160, height: 50, right: left + 160, bottom: top + 50 };
      }
    };
    Object.defineProperty(el, 'innerHTML', { set() {
      el.firstChild = node();
      el.spans = { '.wut-main': {}, '.wut-cd': {} };
      el.querySelector = name => el.spans[name];
    }});
    return el;
  }
  const originalFetch = (url, options) => {
    calls.push({ url, options });
    return Promise.resolve({});
  };
  const originalPost = data => posts.push(JSON.parse(data));
  const window = {
    innerWidth: 1000, innerHeight: 800, fetch: originalFetch,
    ipc: { postMessage: originalPost },
    dispatchEvent() {
      window.fetch('http://ipc.localhost/set_main_hit_region', {
        method: 'POST', body: JSON.stringify({ payload: original })
      });
    },
    __TAURI_INTERNALS__: {
      invoke(cmd, args) {
        return window.fetch('http://ipc.localhost/' + cmd, {
          method: 'POST', body: JSON.stringify(args),
          headers: { get: () => '7' }
        });
      }
    }
  };
  const context = {
    window, URL, Event, Promise, Date,
    document: {
      head: node(), body: node(),
      createElement: node,
      getElementById: id => elements.get(id),
      querySelector: selector => selector.includes('.pet-dock') ?
        { getBoundingClientRect: () => pet } : null
    },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: () => 1, clearInterval() {},
    setTimeout: () => 1,
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    cancelAnimationFrame() {}
  };
  const cfg = { version, poll_ms: 100, alert_low: 2000, alert_critical: 1000 };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/scripts/inject.js'), 'utf8')
    .replace('__CFG__', JSON.stringify(cfg)), context);
  const a = window.__WUT__;
  a.updateAlert(500, { now: 1, left: 30000, right: 60000 });
  return { a, window, calls, posts, frames, elements, pet, original, originalFetch, originalPost, context };
}

test('bubble stays above the pet with no gap', () => {
  const f = fixture();
  const rect = f.elements.get('wut-alert-host').getBoundingClientRect();
  assert.equal(f.pet.top - rect.bottom, 0);
});

test('main injection uses the product version and upgrades a different script version once', () => {
  const f = fixture();
  const source = fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/scripts/inject.js'), 'utf8')
    .replace('__CFG__', JSON.stringify(f.a.cfg));
  assert.equal(f.a.v, version);
  vm.runInNewContext(source, f.context);
  assert.equal(f.window.__WUT__, f.a);
  f.a.v = '0.0.0';
  let uninstalled = 0;
  const uninstall = f.a.uninstall;
  f.a.uninstall = () => { uninstalled++; uninstall(); };
  vm.runInNewContext(source, f.context);
  assert.equal(uninstalled, 1);
  assert.equal(f.window.__WUT__.v, version);
  assert.notEqual(f.window.__WUT__, f.a);
});

test('native visual region expands without changing app input regions', () => {
  const f = fixture();
  const body = JSON.parse(f.calls.at(-1).options.body).payload;
  assert.equal(body.regions.length, 2);
  assert.deepEqual(body.regions[0], f.original.regions[0]);
  assert.equal(body.updateNativeInput, false);
  assert.deepEqual(body.inputRegions, []);
  const appOptions = { body: JSON.stringify({ payload: f.original }) };
  f.window.fetch('http://ipc.localhost/set_main_hit_region', appOptions);
  const appBody = JSON.parse(f.calls.at(-1).options.body).payload;
  assert.deepEqual(appBody.inputRegions, f.original.inputRegions);
  assert.equal(appBody.updateNativeInput, true);
  assert.equal(appBody.windowPassthrough, false);
});

test('postMessage fallback extends only the matching native command', () => {
  const f = fixture();
  f.window.ipc.postMessage(JSON.stringify({ cmd: 'set_main_hit_region',
    payload: { payload: f.original }, callback: 8 }));
  assert.equal(f.posts[0].payload.payload.regions.length, 2);
  assert.deepEqual(f.posts[0].payload.payload.inputRegions, f.original.inputRegions);
  assert.equal(f.posts[0].callback, 8);
  const unrelated = { cmd: 'other', payload: { value: 42 } };
  f.window.ipc.postMessage(JSON.stringify(unrelated));
  assert.deepEqual(f.posts[1], unrelated);
});

test('unrelated fetch calls keep the original options object', () => {
  const f = fixture();
  const options = { body: 'do not parse this' };
  f.window.fetch('https://example.invalid/set_main_hit_region', options);
  assert.equal(f.calls.at(-1).options, options);
});

test('asynchronous IPC fallback does not add a second bubble region', () => {
  const f = fixture();
  const args = JSON.parse(f.calls.at(-1).options.body);
  f.window.ipc.postMessage(JSON.stringify({
    cmd: 'set_main_hit_region', payload: args, callback: 7
  }));
  assert.equal(f.posts[0].payload.payload.regions.length, 2);
  f.a.dropBubble();
  assert.deepEqual(JSON.parse(f.calls.at(-1).options.body).payload.regions,
    f.original.regions);
});

test('near screen top bubble moves below pet and flips tail', () => {
  const f = fixture();
  f.pet.top = 5; f.pet.bottom = 155;
  f.a.updateAlert(500, { now: 1, left: 30000, right: 60000 });
  const host = f.elements.get('wut-alert-host');
  assert.equal(host.className, 'wut-below');
  assert.equal(host.getBoundingClientRect().top - f.pet.bottom, 0);
});

test('bubble stays within horizontal screen edges', () => {
  const f = fixture();
  for (const left of [-60, 950]) {
    f.pet.left = left;
    f.a.updateAlert(500, { now: 1, left: 30000, right: 60000 });
    const rect = f.elements.get('wut-alert-host').getBoundingClientRect();
    assert.ok(rect.left >= 8);
    assert.ok(rect.right <= 992);
  }
});

test('balance recovery restores transports and removes extra region', () => {
  const f = fixture();
  f.a.updateAlert(3000, null);
  assert.equal(f.window.fetch, f.originalFetch);
  assert.equal(f.window.ipc.postMessage, f.originalPost);
  assert.equal(f.elements.has('wut-alert-host'), false);
  const restored = JSON.parse(f.calls.at(-1).options.body).payload;
  assert.deepEqual(restored.regions, f.original.regions);
  assert.equal(restored.updateNativeInput, false);
});

test('uninstall restores the original region and transports', () => {
  const f = fixture();
  f.a.uninstall();
  assert.equal(f.window.fetch, f.originalFetch);
  assert.equal(f.window.ipc.postMessage, f.originalPost);
  assert.equal(f.window.__WUT__, undefined);
});

test('empty app regions are respected while pet is hidden', () => {
  const f = fixture();
  f.window.fetch('http://ipc.localhost/set_main_hit_region', {
    body: JSON.stringify({ payload: { ...f.original, regions: [] } })
  });
  assert.deepEqual(JSON.parse(f.calls.at(-1).options.body).payload.regions, []);
});

test('live pause blocks clicks while keeping state and reminders available', () => {
  const f = fixture();
  let clicks = 0;
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => selector === '.reward-btn' ? {
    disabled: false, click() { clicks++; },
    getBoundingClientRect: () => ({ width: 20, height: 20, x: 1, y: 1 })
  } : query(selector);
  f.a.snapshot = () => ({ now: Date.now(), left: 1, right: Date.now() + 10000, bal: 3000 });
  f.a.configure({ enabled: false, bubble: true, animation: true });
  f.a.tick();
  assert.equal(clicks, 0);
  assert.equal(f.a.bal, 3000);
  f.a.configure({ enabled: true, bubble: true, animation: true });
  f.a.tick();
  assert.equal(clicks, 1);
});

test('animation switch retains the bubble and toggles animation in place', () => {
  const f = fixture();
  const host = f.elements.get('wut-alert-host');
  f.a.configure({ enabled: true, bubble: true, animation: false });
  assert.equal(host.firstChild.style.animation, 'none');
  assert.equal(f.elements.get('wut-alert-host'), host);
  f.a.configure({ enabled: true, bubble: true, animation: true });
  assert.equal(host.firstChild.style.animation, '');
});

test('bubble switch hides reminder and restores it without changing alert thresholds', () => {
  const f = fixture();
  f.a.configure({ enabled: true, bubble: false, animation: true });
  f.a.updateAlert(500, null);
  assert.equal(f.elements.has('wut-alert-host'), false);
  assert.equal(f.a.alert, 2);
  f.a.configure({ enabled: true, bubble: true, animation: true });
  f.a.updateAlert(500, null);
  assert.equal(f.elements.has('wut-alert-host'), true);
});

test('re-enabling a hidden bubble after balance recovery clears alert status', () => {
  const f = fixture();
  f.a.configure({ enabled: true, bubble: false, animation: true });
  f.a.updateAlert(500, null);
  assert.equal(f.a.alert, 2);
  f.a.configure({ enabled: true, bubble: true, animation: true });
  f.a.updateAlert(3000, null);
  assert.equal(f.a.alert, 0);
});
