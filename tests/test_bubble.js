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
  const timers = [];
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
      querySelectorAll: () => [],
      querySelector: selector => selector.includes('.pet-dock') ?
        { getBoundingClientRect: () => pet } : null
    },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: () => 1, clearInterval() {},
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() {},
    requestAnimationFrame: fn => { frames.push(fn); return frames.length; },
    cancelAnimationFrame() {}
  };
  const cfg = { version, poll_ms: 1000, alert_low: 2000, alert_critical: 1000 };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/scripts/inject.js'), 'utf8')
    .replace('__CFG__', JSON.stringify(cfg)), context);
  const a = window.__WUT__;
  a.updateAlert(500, { now: 1, left: 30000, right: 60000 });
  return { a, window, calls, posts, frames, elements, pet, original, originalFetch, originalPost, context, timers };
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
  f.context.document.querySelector = selector => /^\.reward-anchor--(?:left|right) \.reward-btn$/.test(selector) ? {
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

test('a chest that has just come due waits out the server grace', () => {
  const f = fixture();
  let clicks = 0;
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => /^\.reward-anchor--(?:left|right) \.reward-btn$/.test(selector) ? {
    disabled: false, click() { clicks++; },
    getBoundingClientRect: () => ({ width: 20, height: 20, x: 1, y: 1 })
  } : query(selector);
  f.a.configure({ enabled: true, bubble: false, animation: false });
  f.a.snapshot = () => ({ now: Date.now(), left: Date.now() - 800, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(clicks, 0);
  // A short wait is expected behaviour, not a stalled gate.
  assert.equal(f.a.block, null);
  f.a.snapshot = () => ({ now: Date.now(), left: Date.now() - 3000, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(clicks, 1);
});

test('missing reward button is reported as a block instead of silence', () => {
  const f = fixture();
  f.a.configure({ enabled: true, bubble: false, animation: false });
  f.a.snapshot = () => ({ now: Date.now(), left: Date.now() - 4000, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(f.a.block.reason, 'no-button');
  assert.equal(f.a.block.slot, 'left');
  assert.equal(f.a.block.balance, 3000);
});

test('the button gets a few seconds to be drawn before it counts as missing', () => {
  const f = fixture();
  f.a.configure({ enabled: true, bubble: false, animation: false });
  let left = Date.now() - 800;
  f.a.snapshot = () => ({ now: Date.now(), left: left, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(f.a.block, null);
  left = Date.now() - 4000;
  f.a.tick();
  assert.equal(f.a.block.reason, 'no-button');
});

test('a new chest timestamp replaces the previous block even with the same reason', () => {
  const f = fixture();
  f.a.configure({ enabled: true, bubble: false, animation: false });
  let left = Date.now() - 4000;
  f.a.snapshot = () => ({ now: Date.now(), left: left, right: 0, bal: 500 });
  f.a.tick();
  const first = f.a.block;
  assert.equal(first.reason, 'no-button');
  left = Date.now() - 5000;
  f.a.tick();
  assert.notEqual(f.a.block, first);
  assert.equal(f.a.block.ts, left);
});

test('a chest that refuses every click is parked and announced', () => {
  const f = fixture();
  let clicks = 0;
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => /^\.reward-anchor--(?:left|right) \.reward-btn$/.test(selector) ? {
    disabled: false, click() { clicks++; },
    getBoundingClientRect: () => ({ width: 20, height: 20, x: 1, y: 1 })
  } : query(selector);
  f.a.configure({ enabled: true, bubble: true, animation: false });
  const due = Date.now() - 6000;
  f.a.snapshot = () => ({ now: Date.now(), left: due, right: 0, bal: 3000 });

  for (let attempt = 0; attempt < 5; attempt++) {
    f.a.slots.left.lastClickAt = 0;   // pretend the 10 s lock window elapsed
    f.a.tick();
    const verify = f.timers.find(timer => timer.ms === undefined);
    assert.ok(verify, 'expected a verify timer after the click');
    f.timers.length = 0;
    verify.fn();
  }

  assert.equal(clicks, 5);
  assert.equal(f.a.slots.left.attempts, 5);
  assert.equal(f.a.slots.left.gaveUp, true);
  assert.equal(f.a.notice.text, '自动开箱失败');

  f.a.tick();
  assert.equal(clicks, 5, 'no sixth click');
  assert.equal(f.a.block.reason, 'give-up');
  assert.equal(f.a.block.slot, 'left');
});

test('giving up on one chest does not stop the other', () => {
  const f = fixture();
  let clicks = 0;
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => /^\.reward-anchor--(?:left|right) \.reward-btn$/.test(selector) ? {
    disabled: false, click() { clicks++; },
    getBoundingClientRect: () => ({ width: 20, height: 20, x: 1, y: 1 })
  } : query(selector);
  f.a.configure({ enabled: true, bubble: false, animation: false });
  const leftDue = Date.now() - 6000;
  let rightTs = 0;
  f.a.snapshot = () => ({ now: Date.now(), left: leftDue, right: rightTs, bal: 3000 });

  for (let attempt = 0; attempt < 5; attempt++) {
    f.a.slots.left.lastClickAt = 0;   // pretend the lock window elapsed
    f.a.tick();
    const verify = f.timers.find(timer => timer.ms === undefined);
    f.timers.length = 0;
    verify.fn();
  }
  assert.equal(f.a.slots.left.gaveUp, true);

  rightTs = Date.now() - 3000;
  f.a.slots.right.lastClickAt = 0;
  f.a.tick();
  assert.equal(clicks, 6, 'the second chest is still clicked');
  assert.equal(f.a.clicks.at(-1).slot, 'right');
  assert.equal(f.a.block, null);
});

test('paused auto open reports its own reason', () => {
  const f = fixture();
  f.a.configure({ enabled: false, bubble: false, animation: false });
  f.a.snapshot = () => ({ now: Date.now(), left: Date.now() - 3000, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(f.a.block.reason, 'paused');
  // Nothing due: there is no stall to report.
  f.a.snapshot = () => ({ now: Date.now(), left: Date.now() + 60000, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(f.a.block, null);
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

// Official 1.0.2 leaves userInfo chest timestamps stale after an open.
function nativeChests() {
  const f = fixture();
  const nodes = new Map();
  const store = { isLoggedIn: true, cumulativeClickCount: 9000,
    $state: { userInfo: { left_treasure_chest: 1, right_treasure_chest: 2 } } };
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => {
    if (selector === '#app') return { __vue_app__: { config: {
      globalProperties: { $pinia: { _s: new Map([['GlobalState', store]]) } }
    } } };
    if (nodes.has(selector)) return nodes.get(selector);
    return query(selector);
  };
  f.a.configure({ enabled: true, bubble: false, animation: false });
  const button = click => ({ disabled: false, textContent: '', click,
    getBoundingClientRect: () => ({ width: 20, height: 20, x: 1, y: 1 }) });
  return { ...f, nodes, store, button };
}

test('native hidden chest timers ignore stale userInfo timestamps', () => {
  const f = nativeChests();
  const s = f.a.snapshot();
  assert.equal(s.left, 0);
  assert.equal(s.right, 0);
  f.a.tick();
  assert.equal(f.a.block, null);
  assert.equal(f.a.clicks.length, 0);
});

test('native countdown is read from its own slot and malformed text is unknown', () => {
  const f = nativeChests();
  f.nodes.set('.reward-anchor--right .reward-countdown__text', { textContent: '12:34' });
  const s = f.a.snapshot();
  assert.equal(s.left, 0);
  assert.ok(Math.abs(s.right - s.now - 754000) < 5);
  for (const textContent of ['00:00', '12:99', '', 'loading']) {
    f.nodes.set('.reward-anchor--right .reward-countdown__text', { textContent });
    assert.equal(f.a.snapshot().right, 0);
  }
});

test('right-only native button never clicks a left button or uses stale slot order', () => {
  const f = nativeChests();
  let clicks = 0;
  const key = '.reward-anchor--right .reward-btn';
  f.nodes.set(key, f.button(() => { clicks++; f.nodes.delete(key); }));
  f.a.snapshot = () => ({ now: Date.now(), left: 0, right: 10,
    native_chests: true, bal: 3000 });
  f.a.tick();
  assert.equal(clicks, 1);
  assert.equal(f.a.clicks[0].slot, 'right');
  f.timers.find(t => t.ms === undefined).fn();
  assert.equal(f.a.retries, 0, 'disappeared native button is not a failed open');
});

test('native button gets a stable ready identity and clears when cooldown starts', () => {
  const f = nativeChests();
  const key = '.reward-anchor--left .reward-btn';
  f.nodes.set(key, f.button(() => {}));
  const first = f.a.snapshot().left;
  assert.ok(first > 0);
  assert.equal(f.a.snapshot().left, first);
  f.nodes.delete(key);
  f.nodes.set('.reward-anchor--left .reward-countdown__text', { textContent: '29:58' });
  const after = f.a.snapshot();
  assert.ok(after.left > after.now);
  assert.equal(f.store.$state.userInfo.left_treasure_chest, 1);
  f.store.isLoggedIn = false;
  assert.equal(f.a.snapshot().left, 0);
});

test('failed native left chest never clicks the ready right button', () => {
  const f = nativeChests();
  let clicks = 0;
  f.nodes.set('.reward-anchor--right .reward-btn', f.button(() => { clicks++; }));
  f.a.snapshot = () => ({ now: Date.now(), left: 10, right: 0, bal: 3000 });
  f.a.tick();
  assert.equal(clicks, 0);
  assert.equal(f.a.block.reason, 'no-button');
});

test('verification from an uninstalled script cannot report a failure', () => {
  const f = nativeChests();
  f.nodes.set('.reward-anchor--left .reward-btn', f.button(() => {}));
  f.a.snapshot = () => ({ now: Date.now(), left: 10, right: 0, bal: 3000 });
  f.a.tick();
  const verify = f.timers.find(t => t.ms === undefined);
  f.a.uninstall();
  verify.fn();
  assert.equal(f.a.retries, 0);
});


test('native open changing only local UI is not retried against stale userInfo', () => {
  const f = nativeChests();
  let now = 100000, clicks = 0;
  class Clock extends Date { static now() { return now; } }
  f.context.Date = Clock;
  const key = '.reward-anchor--left .reward-btn';
  f.nodes.set(key, f.button(() => {
    clicks++;
    f.nodes.delete(key);
    f.nodes.set('.reward-anchor--left .reward-countdown__text', { textContent: '30:00' });
  }));
  f.a.readBal = () => 3000;
  f.nodes.set('.text.web-bold-font', { textContent: '3000', isConnected: true });
  f.context.document.querySelectorAll = selector => selector === '.text.web-bold-font'
    ? [f.nodes.get(selector)] : [];
  f.a.tick();
  assert.equal(clicks, 0, 'wait for native readiness grace');
  now += 2100;
  f.a.tick();
  assert.equal(clicks, 1);
  const verify = f.timers.find(t => t.ms === undefined);
  verify.fn();
  f.a.tick();
  assert.equal(f.a.retries, 0);
  assert.equal(f.a.block, null);
  assert.equal(f.a.errors.length, 0);
  assert.equal(f.store.$state.userInfo.left_treasure_chest, 1);
});


function cachedChests() {
  const f = nativeChests();
  f.store.$state.userInfo.cuid = 'account-a';
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  f.context.Date = Clock;
  const listeners = new Map();
  f.context.document.addEventListener = (type, fn) => listeners.set(type, fn);
  f.context.document.removeEventListener = (type, fn) => {
    if (listeners.get(type) === fn) listeners.delete(type);
  };
  let action, stopped = false;
  f.store.$onAction = fn => { action = fn; return () => { stopped = true; }; };
  f.a.uninstall();
  const source = fs.readFileSync(path.join(__dirname, '../src/wuwa_tappo_plus/scripts/inject.js'), 'utf8');
  function install(seed) {
    vm.runInNewContext(source.replace('__CFG__', JSON.stringify({ ...f.a.cfg,
      chest_cache: seed })), f.context);
    f.a = f.window.__WUT__;
    f.a.snapshot();
  }
  install();
  function capture(slot) {
    const button = { closest: selector => selector === '.reward-anchor--' + slot ? {} : null };
    listeners.get('click')({ target: { closest: () => button } });
  }
  function consume() {
    let after;
    action({ name: 'applyChestClickConsume', args: [1000], after: fn => { after = fn; } });
    if (after) after();
  }
  return { f, capture, consume, install, listeners, stopped: () => stopped,
    advance: ms => { now += ms; } };
}

test('only confirmed native consumption caches a clicked slot for hidden countdown', () => {
  const c = cachedChests(), f = c.f;
  c.capture('right');
  assert.equal(f.a.snapshot().right, 0, 'a click alone is not success');
  c.consume();
  const s = f.a.snapshot();
  assert.equal(s.right - s.now, 1800000);
  assert.equal(s.left, 0);
  assert.equal(f.a.chestCache.right.opened_at, s.now);
  c.advance(5000);
  assert.equal(f.a.snapshot().right - f.a.snapshot().now, 1795000);
  f.a.tick();
  assert.equal(f.a.clicks.length, 0, 'cached timers never click absent buttons');
});

test('confirmed cache survives reinjection but not an account switch or expiry', () => {
  const c = cachedChests(), f = c.f;
  c.capture('left'); c.consume();
  const seed = JSON.parse(JSON.stringify(f.a.chestCache));
  f.a.uninstall(); c.install(seed);
  assert.ok(f.a.snapshot().left > 0);
  c.advance(1800001);
  assert.equal(f.a.snapshot().left, 0);
  c.install(seed);
  f.store.$state.userInfo.cuid = 'account-b';
  assert.equal(f.a.snapshot().left, 0);
  assert.equal(f.a.chestCache.account, 'account-b');
});

test('native button and visible countdown override estimates; logout hides estimates', () => {
  const c = cachedChests(), f = c.f;
  c.capture('left'); c.consume();
  f.nodes.set('.reward-anchor--left .reward-countdown__text', { textContent: '12:00' });
  assert.equal(f.a.snapshot().left - f.a.snapshot().now, 720000);
  f.nodes.delete('.reward-anchor--left .reward-countdown__text');
  assert.equal(f.a.snapshot().left, 0, 'contradicted cache must not reappear');
  f.nodes.set('.reward-anchor--left .reward-btn', f.button(() => {}));
  assert.equal(f.a.snapshot().left, f.a.snapshot().now);
  f.store.isLoggedIn = false;
  assert.equal(f.a.snapshot().left, 0);
});

test('unmatched consumption, late confirmation and uninstalled listeners cannot cache success', () => {
  const c = cachedChests(), f = c.f;
  c.consume();
  assert.equal(f.a.chestCache.left, undefined);
  c.capture('left'); c.advance(60001); c.consume();
  assert.equal(f.a.chestCache.left, undefined);
  f.a.uninstall();
  assert.equal(c.listeners.size, 0);
  assert.equal(c.stopped(), true);
});


test('wallet reads seven-digit, eight-digit and larger safe integer balances', () => {
  const f = fixture();
  const wallet = { textContent: '', isConnected: true };
  f.context.document.querySelectorAll = selector => selector === '.text.web-bold-font' ? [wallet] : [];
  for (const value of ['0', '9999999', '10000000', '10000001', '123456789', '9007199254740991']) {
    wallet.textContent = ' ' + value + ' ';
    assert.equal(f.a.readBal(), Number(value));
  }
  for (const text of ['', '-1', '1.5', '1000W', '1e7', '9007199254740992']) {
    wallet.textContent = text;
    assert.equal(f.a.readBal(), null);
  }
});

test('initial wallet discovery accepts eight digits and ignores other text', () => {
  const f = fixture();
  f.context.document.querySelectorAll = () => [
    { textContent: 'loading', isConnected: true },
    { textContent: '10000000', isConnected: true }
  ];
  assert.equal(f.a.readBal(), 10000000);
});

test('crossing ten million does not block an otherwise ready native chest', () => {
  const f = nativeChests();
  let now = 100000, clicks = 0;
  class Clock extends Date { static now() { return now; } }
  f.context.Date = Clock;
  const wallet = { textContent: '9999999', isConnected: true };
  f.context.document.querySelectorAll = selector => selector === '.text.web-bold-font' ? [wallet] : [];
  f.nodes.set('.reward-anchor--left .reward-btn', f.button(() => { clicks++; }));
  f.a.tick();
  wallet.textContent = '10000000';
  now += 2100;
  f.a.tick();
  assert.equal(clicks, 1);
  assert.equal(f.a.clicks[0].balance, 10000000);
  assert.equal(f.a.block, null);
});


test('native wallet getter wins over DOM including zero and eight-digit balances', () => {
  const f = nativeChests();
  f.context.document.querySelectorAll = () => [{ textContent: '4000', isConnected: true }];
  for (const value of [0, 9999999, 10000000, 123456789, Number.MAX_SAFE_INTEGER]) {
    f.store.totalClickCount = value;
    assert.equal(f.a.readBal(), value);
    assert.equal(f.a.snapshot().bal, value);
  }
  f.context.document.querySelectorAll = () => { throw new Error('DOM should not be read'); };
  assert.equal(f.a.readBal(), Number.MAX_SAFE_INTEGER);
});

test('missing invalid or throwing native wallet uses DOM and can recover', () => {
  const f = nativeChests();
  f.context.document.querySelectorAll = () => [{ textContent: '10000001', isConnected: true }];
  for (const value of [undefined, null, NaN, Infinity, -1, 1.5, '10000000', Number.MAX_SAFE_INTEGER + 1]) {
    f.store.totalClickCount = value;
    assert.equal(f.a.readBal(), 10000001);
  }
  Object.defineProperty(f.store, 'totalClickCount', { configurable: true,
    get() { throw new Error('store updating'); } });
  assert.equal(f.a.readBal(), 10000001);
  Object.defineProperty(f.store, 'totalClickCount', { configurable: true, value: 20000000 });
  assert.equal(f.a.readBal(), 20000000);
});

test('unavailable native wallet and DOM remain unknown', () => {
  const f = nativeChests();
  f.store.totalClickCount = NaN;
  assert.equal(f.a.readBal(), null);
});

test('native zero balance prevents clicking despite a stale sufficient DOM balance', () => {
  const f = nativeChests();
  f.a.cfg.min_balance = 1000;
  let now = 100000, clicks = 0;
  class Clock extends Date { static now() { return now; } }
  f.context.Date = Clock;
  f.store.totalClickCount = 0;
  f.context.document.querySelectorAll = () => [{ textContent: '10000000', isConnected: true }];
  f.nodes.set('.reward-anchor--left .reward-btn', f.button(() => { clicks++; }));
  f.a.tick(); now += 2100; f.a.tick();
  assert.equal(clicks, 0);
  assert.equal(f.a.block.reason, 'low-balance');
});
