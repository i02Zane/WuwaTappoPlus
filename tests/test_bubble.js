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

function clickPauseFixture() {
  const f = fixture();
  const sent = [];
  const received = [];
  class Socket {
    constructor(url = 'wss://example.invalid/socket.io/?transport=websocket') {
      this.url = url;
      this.readyState = 1;
      this.acks = new Map();
    }
    send(data) { sent.push(data); }
    dispatchEvent(event) {
      received.push(event.data);
      const match = /^43([0-9]+)(.*)$/.exec(event.data);
      if (match && this.acks.has(match[1])) {
        const callback = this.acks.get(match[1]);
        this.acks.delete(match[1]);
        callback(...JSON.parse(match[2]));
      }
    }
  }
  function store() {
    return {
      localClickCount: 10,
      incrementLocalClickCount() { this.localClickCount++; },
      addLocalClickCount(n) { this.localClickCount += n; },
      setLocalClickCount(n) { this.localClickCount = n; },
      spendClickCount(n) { this.localClickCount -= n; }
    };
  }
  let current = store();
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => selector === '#app' ? {
    __vue_app__: { config: { globalProperties: { $pinia: { _s: new Map([['GlobalState', current]]) } } } }
  } : query(selector);
  f.window.WebSocket = Socket;
  f.window.MessageEvent = class { constructor(type, init) { this.type = type; Object.assign(this, init); } };
  return { ...f, Socket, sent, received, store, get current() { return current; },
    replaceStore() { current = store(); },
    flushAcks() { f.timers.filter(t => t.ms === 0).splice(0).forEach(t => t.fn()); } };
}

test('pause stops new native clicks while preserving actions, hydration and spending', () => {
  const f = clickPauseFixture();
  const increment = f.current.incrementLocalClickCount;
  const add = f.current.addLocalClickCount;
  f.a.configure({ pause_clicks: true });
  let animations = 0;
  function nativeKey() { f.current.incrementLocalClickCount(); animations++; }
  nativeKey();
  f.current.addLocalClickCount(100);
  assert.equal(f.current.localClickCount, 10);
  assert.equal(animations, 1);
  f.current.setLocalClickCount(200);
  f.current.spendClickCount(50);
  assert.equal(f.current.localClickCount, 150);
  assert.equal(f.a.clickPause.counting, true);
  f.a.configure({ pause_clicks: false });
  assert.equal(f.current.incrementLocalClickCount, increment);
  assert.equal(f.current.addLocalClickCount, add);
  nativeKey();
  assert.equal(f.current.localClickCount, 151);
});

test('pause blocks only dedicated click reports and resolves ack without report success', () => {
  const f = clickPauseFixture();
  const socket = new f.Socket();
  f.a.configure({ pause_clicks: true });
  let success = 0, completed = 0;
  socket.acks.set('0', response => { completed++; if (response?.c === 0) success++; });
  socket.send('420["cr",{"num":100000001}]');
  assert.equal(f.sent.length, 0);
  f.flushAcks();
  assert.equal(completed, 1);
  assert.equal(success, 0);
  assert.equal(socket.acks.size, 0);
  assert.equal(f.current.localClickCount, 10);
  assert.deepEqual(f.received, ['430[null]']);
  for (const frame of ['2', '423["rj",{"r":"room"}]', '424["rl",{}]',
    '42["ru",{}]', '42/other,1["cr",{"num":3}]', '42bad',
    '425["cr",{"num":"invalid"}]']) socket.send(frame);
  assert.equal(f.sent.length, 7);
  assert.equal(f.a.clickPause.blockedReports, 1);
  socket.send('42["cr",{"num":8}]');
  assert.equal(f.sent.length, 7);
  const other = new f.Socket('wss://example.invalid/chat');
  other.send('421["cr",{"num":8}]');
  assert.equal(f.sent.length, 8);
  socket.readyState = 0;
  socket.send('421["cr",{"num":8}]');
  assert.equal(f.sent.length, 9);
});

test('pause applies to store replacement and restores transport after toggles and uninstall', () => {
  const f = clickPauseFixture();
  const send = f.Socket.prototype.send;
  f.a.configure({ pause_clicks: true });
  const previous = f.current;
  f.replaceStore();
  f.a.tick();
  previous.incrementLocalClickCount();
  f.current.incrementLocalClickCount();
  assert.equal(previous.localClickCount, 11);
  assert.equal(f.current.localClickCount, 10);
  f.a.configure({ pause_clicks: false });
  assert.equal(f.Socket.prototype.send, send);
  const socket = new f.Socket();
  socket.send('421["cr",{"num":8}]');
  assert.equal(f.sent.length, 1);
  f.a.configure({ pause_clicks: true });
  f.a.uninstall();
  assert.equal(f.Socket.prototype.send, send);
  f.current.incrementLocalClickCount();
  assert.equal(f.current.localClickCount, 11);
  socket.send('422["cr",{"num":9}]');
  assert.equal(f.sent.length, 2);
});

test('retained transport wrapper keeps working after another wrapper and a toggle', () => {
  const f = clickPauseFixture();
  f.a.configure({ pause_clicks: true });
  const first = f.Socket.prototype.send;
  const outer = function (data) { return first.call(this, data); };
  f.Socket.prototype.send = outer;
  f.a.configure({ pause_clicks: false });
  assert.equal(f.Socket.prototype.send, outer);
  f.a.configure({ pause_clicks: true });
  new f.Socket().send('2');
  assert.deepEqual(f.sent, ['2']);
  f.a.uninstall();
  assert.equal(f.Socket.prototype.send, outer);
});


test('black room bubble persists above balance and failure notices with balance reminders off', () => {
  const f = fixture();
  f.a.configure({ pause_clicks: true, bubble: true, balance_reminder: false, animation: false });
  const host = f.elements.get('wut-alert-host');
  assert.ok(host);
  const text = '库爹我错了，我再也不刷点击数了T T';
  assert.equal(host.spans['.wut-main'].textContent, text);
  assert.equal(host.spans['.wut-cd'].textContent, '');
  assert.equal(host.firstChild.style.animation, 'none');
  f.a.notice = { text: '自动开箱失败', until: Infinity };
  for (const balance of [500, 5000, null, 100000001]) {
    f.a.updateAlert(balance, { now: Date.now(), left: 0, right: 0 });
    assert.equal(f.elements.get('wut-alert-host'), host);
    assert.equal(host.spans['.wut-main'].textContent, text);
    assert.equal(host.style.display, 'block');
  }
  f.a.configure({ pause_clicks: true, bubble: true, balance_reminder: false, animation: true });
  assert.equal(host.firstChild.style.animation, '');
  f.a.configure({ pause_clicks: false, bubble: false });
  assert.equal(f.elements.has('wut-alert-host'), false);
});

test('disabling black room mode restores ordinary reminders and uninstall removes its bubble', () => {
  const f = fixture();
  f.a.configure({ pause_clicks: true });
  f.a.configure({ pause_clicks: false, bubble: true });
  f.a.updateAlert(500, null);
  assert.equal(f.elements.get('wut-alert-host').spans['.wut-main'].textContent, '不够开宝箱了');
  f.a.updateAlert(5000, null);
  assert.equal(f.elements.has('wut-alert-host'), false);
  f.a.configure({ pause_clicks: true });
  assert.ok(f.elements.get('wut-alert-host'));
  f.a.uninstall();
  assert.equal(f.elements.has('wut-alert-host'), false);
});


test('global bubble switch hides black room text without resuming clicks', () => {
  const f = clickPauseFixture();
  f.a.configure({ pause_clicks: true, bubble: false });
  f.a.updateAlert(500, null);
  assert.equal(f.elements.has('wut-alert-host'), false);
  f.current.incrementLocalClickCount();
  assert.equal(f.current.localClickCount, 10);
  assert.equal(f.a.alert, 2);
  f.a.configure({ pause_clicks: true, bubble: true });
  assert.equal(f.elements.get('wut-alert-host').spans['.wut-main'].textContent,
    '库爹我错了，我再也不刷点击数了T T');
});

test('balance reminder toggle hides only balance content and restores it', () => {
  const f = fixture();
  f.a.configure({ balance_reminder: false, bubble: true });
  f.a.updateAlert(500, null);
  assert.equal(f.a.alert, 0);
  assert.equal(f.elements.has('wut-alert-host'), false);
  f.a.notice = { text: '自动开箱失败', until: Infinity };
  f.a.updateAlert(500, null);
  assert.equal(f.elements.get('wut-alert-host').spans['.wut-main'].textContent, '自动开箱失败');
  f.a.notice = null;
  f.a.configure({ balance_reminder: true, bubble: true });
  f.a.updateAlert(500, null);
  assert.equal(f.a.alert, 2);
  assert.equal(f.elements.get('wut-alert-host').spans['.wut-main'].textContent, '不够开宝箱了');
});

function emoteFixture() {
  const f = clickPauseFixture();
  const selected = [];
  const menu = { type: { __name: 'EmotionMenu' }, props: {
    emoteItems: Array.from({ length: 5 }, (_, i) => ({ id: 100 + i, name: `emote${i}.png` })),
    assetUrl: name => `https://example.invalid/${name}`
  }, emit(event, item) { selected.push({ event, ...item }); } };
  const query = f.context.document.querySelector;
  f.context.document.querySelector = selector => {
    const found = query(selector);
    return selector === '#app' ? { ...found, _vnode: { component: {
      type: { __name: 'App' }, subTree: { children: [{ component: menu }] }
    } } } : found;
  };
  return { ...f, selected, menu };
}

test('auto emotes are opt-in and single native taps select exactly one equipped emote', () => {
  const f = emoteFixture();
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 0);
  f.a.configure({ auto_emote: true });
  for (let i = 0; i < 5; i++) {
    f.context.Math = Object.create(Math); f.context.Math.random = () => (i + .1) / 5;
    f.current.incrementLocalClickCount();
    assert.equal(f.selected[i].id, 100 + i);
    assert.equal(f.selected[i].src, `https://example.invalid/emote/emote${i}.png`);
    assert.equal(f.selected[i].event, 'select');
  }
  assert.equal(f.selected.length, 5); assert.equal(f.current.localClickCount, 16);
  f.current.addLocalClickCount(50); f.current.setLocalClickCount(200); f.current.spendClickCount(5);
  assert.equal(f.selected.length, 5); assert.equal(f.a.clickPause.counting, false);
});

test('auto emotes coexist with pause mode and restore native actions after toggles and uninstall', () => {
  const f = emoteFixture(); const original = f.current.incrementLocalClickCount;
  f.a.configure({ auto_emote: true, pause_clicks: true });
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 1); assert.equal(f.current.localClickCount, 10);
  f.a.configure({ auto_emote: true, pause_clicks: false });
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 2); assert.equal(f.current.localClickCount, 11);
  f.a.configure({ auto_emote: false, pause_clicks: true });
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 2); assert.equal(f.current.localClickCount, 11);
  f.a.configure({ auto_emote: false, pause_clicks: false });
  assert.equal(f.current.incrementLocalClickCount, original);
  f.a.configure({ auto_emote: true }); f.a.uninstall();
  assert.equal(f.current.incrementLocalClickCount, original);
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 2);
});

test('missing, unmounted or changed emote components never interrupt native counting', () => {
  const f = emoteFixture(); f.a.configure({ auto_emote: true });
  f.menu.props.emoteItems = []; f.current.incrementLocalClickCount();
  f.menu.props.emoteItems = [{ id: 12, name: 'new.png' }]; f.menu.isUnmounted = true;
  f.current.incrementLocalClickCount(); f.menu.isUnmounted = false;
  f.menu.props.assetUrl = () => { throw new Error('native component changed'); };
  assert.doesNotThrow(() => f.current.incrementLocalClickCount());
  assert.equal(f.current.localClickCount, 13); assert.equal(f.selected.length, 0);
  f.menu.props.assetUrl = name => name;
  f.current.incrementLocalClickCount(); assert.equal(f.selected[0].id, 12);
});

test('auto emotes cap bursts at 20 per rolling second without suppressing taps or queueing sends', () => {
  const f = emoteFixture(); let now = 0;
  f.context.performance = { now: () => now }; f.a.configure({ auto_emote: true });
  for (let i = 0; i < 100; i++) f.current.incrementLocalClickCount();
  assert.equal(f.selected.length, 20); assert.equal(f.current.localClickCount, 110);
  assert.equal(f.a.autoEmote.limited, 80);
  now = 999; f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 20);
  now = 1000; f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 21);
  assert.equal(f.timers.filter(t => t.ms === 1000).length, 0);
});

test('rolling emote budget expires each timestamp independently across second boundaries', () => {
  const f = emoteFixture(); let now = 0;
  f.context.performance = { now: () => now }; f.a.configure({ auto_emote: true });
  for (let i = 0; i < 10; i++) f.current.incrementLocalClickCount();
  now = 900; for (let i = 0; i < 10; i++) f.current.incrementLocalClickCount();
  now = 1000; for (let i = 0; i < 20; i++) f.current.incrementLocalClickCount();
  assert.equal(f.selected.length, 30);
  now = 1899; f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 30);
  now = 1900; for (let i = 0; i < 20; i++) f.current.incrementLocalClickCount();
  assert.equal(f.selected.length, 40);
});

test('auto emote toggles and pause mode changes do not reset the shared send budget', () => {
  const f = emoteFixture(); f.context.performance = { now: () => 50 };
  f.a.configure({ auto_emote: true });
  for (let i = 0; i < 20; i++) f.current.incrementLocalClickCount();
  f.a.configure({ auto_emote: false }); f.a.configure({ auto_emote: true, pause_clicks: true });
  f.current.incrementLocalClickCount(); assert.equal(f.selected.length, 20);
  assert.equal(f.current.localClickCount, 30); assert.equal(f.a.autoEmote.limited, 1);
});
