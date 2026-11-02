const assert = require('node:assert/strict');
const vm = require('node:vm');
const { test } = require('node:test');
const { loadScript, version } = require('./scripts');

const KEY = 'wut-plus-game-mode-owner';
const LEGACY = 'wut-plus-auto-fullscreen-backup';
function ownership(extra = {}) {
  return { original_auto: true, owned: false, active: false,
    suppressed: false, failed: false, pending: null, ...extra };
}
function fixture(initial = {}) {
  let settings = { gameMode: false, autoGameModeOnFullscreen: true, windowOpacity: 85, ...initial };
  const storage = new Map();
  const events = new Map();
  const emitted = [];
  const patches = [];
  let emitError = false;
  function loadDesktopCatSettings() { return { ...settings }; }
  function persistDesktopCatSettings(patch) {
    patches.push(patch);
    settings = { ...settings, ...patch };
    return loadDesktopCatSettings();
  }
  async function emitSettingsGameMode(value) {
    if (emitError) throw new Error('IPC unavailable');
    emitted.push(value);
  }
  const module = { renamedA: loadDesktopCatSettings,
    renamedB: persistDesktopCatSettings, renamedC: emitSettingsGameMode };
  const context = {
    URL, JSON, Promise, Object, Number,
    location: { href: 'http://tauri.localhost/#/', origin: 'http://tauri.localhost' },
    document: { querySelector: () => ({ src: 'http://tauri.localhost/assets/index-current.js' }) },
    localStorage: { getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    window: {
      loadNative: async () => module,
      addEventListener: (key, fn) => events.set(key, fn),
      removeEventListener: key => events.delete(key)
    }
  };
  const script = loadScript('game_mode.js').replace('import(url.href)', 'window.loadNative(url.href)');
  vm.runInNewContext(script, context);
  return { api: context.window.__WUT_GAME_MODE__, context, storage, events,
    emitted, patches, settings: () => settings, failEmit: value => { emitError = value; },
    reload: () => vm.runInNewContext(script, context) };
}

test('native API discovery handles renamed exports and preserves unrelated settings', async () => {
  const f = fixture();
  assert.equal(f.api.version, version);
  assert.equal((await f.api.read()).auto, true);
  await f.api.save(ownership());
  assert.equal(await f.api.setAuto(false), false);
  assert.equal(f.settings().windowOpacity, 85);
  assert.equal(JSON.parse(f.storage.get(KEY)).original_auto, true);
});

test('native mode uses explicit values, confirms successful emits and can be repeated', async () => {
  const f = fixture();
  await f.api.save(ownership({ owned: true,
    pending: { target: true, at: 1, attempts: 1, confirmed: false } }));
  await f.api.setMode(true);
  await f.api.setMode(true);
  assert.deepEqual(f.emitted, [true, true]);
  assert.equal((await f.api.read()).ledger.pending.confirmed, true);
  assert.equal(f.settings().gameMode, true);
});

test('failed IPC keeps pending intent unconfirmed for a later retry', async () => {
  const f = fixture();
  await f.api.save(ownership({ owned: true,
    pending: { target: true, at: 1, attempts: 1, confirmed: false } }));
  f.failEmit(true);
  await assert.rejects(f.api.setMode(true), /IPC unavailable/);
  assert.equal((await f.api.read()).ledger.pending.confirmed, false);
  f.failEmit(false);
  await f.api.setMode(true);
  assert.equal((await f.api.read()).ledger.pending.confirmed, true);
});

test('release restores native auto settings, exits only owned mode and is repeatable', async () => {
  const f = fixture({ gameMode: true, autoGameModeOnFullscreen: false });
  await f.api.save(ownership({ owned: true }));
  await f.api.release();
  await f.api.release();
  assert.equal(f.settings().gameMode, false);
  assert.equal(f.settings().autoGameModeOnFullscreen, true);
  assert.equal(f.storage.has(KEY), false);
  assert.deepEqual(f.emitted, [false]);
});

test('release leaves manually enabled mode and an originally disabled auto setting alone', async () => {
  const f = fixture({ gameMode: true, autoGameModeOnFullscreen: false });
  await f.api.save(ownership({ original_auto: false }));
  await f.api.release();
  assert.equal(f.settings().gameMode, true);
  assert.equal(f.settings().autoGameModeOnFullscreen, false);
  assert.deepEqual(f.emitted, []);
});

test('failed release retains recovery information until native IPC succeeds', async () => {
  const f = fixture({ gameMode: true, autoGameModeOnFullscreen: false });
  await f.api.save(ownership({ owned: true }));
  f.failEmit(true);
  await assert.rejects(f.api.release(), /IPC unavailable/);
  assert.equal(f.storage.has(KEY), true);
  assert.equal(f.settings().autoGameModeOnFullscreen, false);
  f.failEmit(false);
  await f.api.release();
  assert.equal(f.storage.has(KEY), false);
});

test('legacy backup is restored even without a running daemon or settings panel', async () => {
  const f = fixture({ autoGameModeOnFullscreen: false });
  f.storage.set(LEGACY, '1');
  await f.api.release();
  assert.equal(f.settings().autoGameModeOnFullscreen, true);
  assert.equal(f.storage.has(LEGACY), false);
});

test('page close restores persisted settings synchronously without waiting for IPC', async () => {
  const f = fixture({ gameMode: true, autoGameModeOnFullscreen: false });
  await f.api.read();
  await f.api.save(ownership({ owned: true }));
  f.events.get('pagehide')();
  assert.equal(f.settings().gameMode, false);
  assert.equal(f.settings().autoGameModeOnFullscreen, true);
  assert.equal(f.storage.has(KEY), false);
});

test('unavailable native APIs do not change settings or erase backups', async () => {
  const f = fixture();
  f.storage.set(LEGACY, '1');
  f.context.window.loadNative = async () => ({});
  await assert.rejects(f.api.release(), /native settings API unavailable/);
  assert.equal(f.storage.get(LEGACY), '1');
  assert.deepEqual(f.patches, []);
});

test('repeated injection retains one native bridge and its recovery information', async () => {
  const f = fixture();
  await f.api.save(ownership({ owned: true }));
  f.reload();
  assert.equal(f.context.window.__WUT_GAME_MODE__, f.api);
  assert.equal(f.events.size, 1);
  assert.equal((await f.api.read()).ledger.owned, true);
});

test('corrupt ownership data is discarded instead of blocking reads', async () => {
  const f = fixture();
  f.storage.set(KEY, '{broken');
  const state = await f.api.read();
  assert.equal(state.ledger, null);
  assert.equal(f.storage.has(KEY), false);
});
