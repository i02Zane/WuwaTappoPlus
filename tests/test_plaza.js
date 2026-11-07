const { test } = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const moduleLoad = import('../web/plaza/controller.mjs');
async function fixture(saved = null) {
  const { PlazaController } = await moduleLoad;
  let now = 1000000, own = null;
  const data = new Map(saved ? [['wut-plaza-v2', JSON.stringify({ identityEpoch: 'a'.repeat(64), ...saved })]] : []), calls = [];
  const storage = { getItem: k => data.get(k) || null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  const clock = () => now;
  const response = () => ({ identity_epoch: 'a'.repeat(64), item: own, server_time: new Date(now).toISOString() });
  const request = async (op, args) => {
    calls.push({ op, args });
    if (op === 'publish') own = { id: crypto.randomUUID(), generation: crypto.randomUUID(), room_code: args.room_code,
      updated_at: new Date(now).toISOString(), expires_at: new Date(now + 900000).toISOString() };
    if (op === 'withdraw') own = null;
    if (op === 'list') return { items: [], page: args.page, has_more: false, server_time: new Date(now).toISOString(), next_refresh_at: new Date(now + (args.refresh ? 30000 : 0)).toISOString() };
    return response();
  };
  const controller = new PlazaController({ request, storage, now: clock, makeId: () => crypto.randomUUID() });
  const native = { logged: true, known: true, connected: true, account: '123', room: 'Room1234', owner: true };
  controller.update(native);
  return { controller, native, data, calls, storage, request, clock, response,
    advance(ms) { now += ms; }, saved: () => JSON.parse(data.get('wut-plaza-v2') || 'null') };
}

test('publication requires explicit consent and renews without collecting members', async () => {
  const f = await fixture(); await f.controller.tick(); assert.equal(f.calls.length, 0);
  await f.controller.publish(); assert.equal(f.controller.publicState().published, true);
  assert.deepEqual(f.saved().intent, { account: '123', room: 'Room1234' });
  assert.deepEqual(f.calls.map(c => c.op), ['own', 'publish']);
  f.advance(300000); await f.controller.tick();
  assert.deepEqual(f.calls.map(c => c.op), ['own', 'publish', 'own', 'publish']);
  assert.deepEqual(Object.keys(f.calls.at(-1).args).sort(), ['generation', 'key', 'room_code']);
});

test('restart restores a confirmed live publication without an unnecessary renewal', async () => {
  const f = await fixture(); await f.controller.publish();
  const { PlazaController } = await moduleLoad;
  const reopened = new PlazaController({ request: f.request, storage: f.storage, now: f.clock });
  reopened.update(f.native); await reopened.tick();
  assert.equal(f.calls.at(-1).op, 'own'); assert.equal(reopened.publicState().published, true);
});

test('old consent never substitutes for a fresh native room and connection', async () => {
  const { deriveNativeState } = await moduleLoad;
  const store = { isLoggedIn: true, userInfo: { id: 123, room_no: 'Room1234' } };
  assert.equal(deriveNativeState(store, null, true).known, false);
  assert.equal(deriveNativeState({ isLoggedIn: false }, null).known, false);
  assert.equal(deriveNativeState(store, { currentRoomId: 'Room1234', account: '456', roomMode: 'owner' }, true).known, false);
  const f = await fixture({ intent: { account: '123', room: 'Room1234' } });
  for (const state of [{ known: false }, { connected: false }]) {
    f.controller.update({ ...f.native, ...state }); await f.controller.tick(); assert.equal(f.calls.length, 0);
  }
  f.controller.update(f.native); await f.controller.tick(); assert.equal(f.calls.at(-1).op, 'publish');
});

test('non-owners and logged-out users cannot publish', async () => {
  const f = await fixture();
  for (const state of [{ owner: false }, { logged: false }, { connected: false }]) {
    f.controller.update({ ...f.native, ...state }); await f.controller.publish();
  }
  assert.equal(f.calls.length, 0);
});

test('failed publish persists idempotency key across a controller restart', async () => {
  const f = await fixture(); let sent;
  f.controller.request = async (op, args) => { if (op === 'publish') { sent = args; throw { code: 'CONNECTION_FAILED' }; } return f.request(op, args); };
  await f.controller.publish();
  const { PlazaController } = await moduleLoad;
  const restarted = new PlazaController({ request: f.request, storage: f.storage, now: f.clock, makeId: () => crypto.randomUUID() });
  restarted.update(f.native); await restarted.tick();
  assert.deepEqual(f.calls.at(-1).args, sent);
});

test('leaving during a pending publication immediately compensates the successful response', async () => {
  const f = await fixture(); let finish;
  f.controller.request = async (op, args) => op === 'publish' ? new Promise(resolve => { finish = async () => resolve(await f.request(op, args)); }) : f.request(op, args);
  const pending = f.controller.publish(); await new Promise(resolve => setImmediate(resolve));
  f.controller.update({ ...f.native, room: '', known: true });
  await finish(); await pending;
  assert.deepEqual(f.calls.map(c => c.op), ['own', 'publish', 'withdraw']);
  assert.equal(f.saved().intent, null); assert.equal(f.saved().withdrawing, false);
});

test('manual withdrawal during publication cannot be overwritten by the late success', async () => {
  const f = await fixture(); let finish;
  f.controller.request = async (op, args) => op === 'publish' ? new Promise(resolve => { finish = async () => resolve(await f.request(op, args)); }) : f.request(op, args);
  const pending = f.controller.publish(); await new Promise(resolve => setImmediate(resolve));
  await f.controller.withdraw(); assert.equal(f.saved().intent, null);
  await finish(); await pending;
  assert.equal(f.calls.at(-1).op, 'withdraw'); assert.equal(f.controller.publicState().published, false);
});

test('pending withdrawal survives restart, retains its key and never renews', async () => {
  const f = await fixture(); await f.controller.publish();
  f.controller.request = async (op, args) => { if (op === 'withdraw') throw { code: 'CONNECTION_FAILED' }; return f.request(op, args); };
  await f.controller.withdraw();
  const record = f.saved(); assert.equal(record.intent, null); assert.equal(record.withdrawing, true);
  const { PlazaController } = await moduleLoad;
  const restarted = new PlazaController({ request: f.request, storage: f.storage, now: f.clock, makeId: () => crypto.randomUUID() });
  restarted.update({ ...f.native, known: false }); await restarted.tick();
  assert.equal(f.calls.at(-1).op, 'withdraw'); assert.equal(f.calls.at(-1).args.key, record.pendingWrite.key);
  assert.equal(f.saved().withdrawing, false);
});

test('confirmed leave, account switch, logout or owner loss withdraws', async () => {
  for (const change of [{ room: '' }, { account: '456', known: false }, { logged: false }, { owner: false }]) {
    const f = await fixture(); await f.controller.publish();
    f.controller.update({ ...f.native, ...change }); await f.controller.tick();
    assert.equal(f.calls.at(-1).op, 'withdraw'); assert.equal(f.saved().intent, null);
  }
});

test('pagination is allowed during cooldown and refresh cannot reset it', async () => {
  const f = await fixture(); await f.controller.list(0); await f.controller.list(0);
  assert.equal(f.calls.length, 1);
  await f.controller.list(1, false); assert.equal(f.calls.length, 2); assert.equal(f.calls.at(-1).args.refresh, false);
  await f.controller.list(0, false); assert.equal(f.calls.length, 2);
  const { PlazaController } = await moduleLoad;
  const restarted = new PlazaController({ request: f.request, storage: f.storage, now: f.clock });
  restarted.update(f.native); await restarted.list(0); assert.equal(f.calls.length, 2);
  f.advance(30000); await restarted.list(0); assert.equal(f.calls.length, 3);
});

test('failed list retains the previous page and respects Retry-After', async () => {
  const f = await fixture(); await f.controller.list(0);
  f.controller.state.items = [{ room_code: 'Room1234', expires_at: new Date(9999999).toISOString() }];
  f.controller.request = async () => { throw { code: 'RATE_LIMITED', retry: 120 }; };
  f.advance(30000); await f.controller.list(1, false);
  assert.equal(f.controller.state.page, 0); assert.equal(f.controller.state.items.length, 1);
  assert.equal(f.controller.nextListAt, f.clock() + 120000);
});

test('authorization failure stops automatic requests until explicit identity reset', async () => {
  const f = await fixture(); let calls = 0;
  f.controller.request = async () => { calls++; throw { code: 'UNAUTHORIZED' }; };
  await f.controller.publish(); f.advance(300000); await f.controller.tick(); await f.controller.list();
  assert.equal(calls, 1); assert.equal(f.controller.state.authExpired, true);
  f.controller.request = f.request; await f.controller.reset();
  assert.equal(f.controller.state.authExpired, false); assert.equal(f.saved().intent, null);
});

test('dispose and missing durable storage cannot cause a new publication', async () => {
  const f = await fixture(); f.controller.storage.setItem = () => { throw new Error('disk'); };
  await f.controller.publish(); assert.equal(f.calls.length, 0);
  f.controller.dispose(); await f.controller.publish(); assert.equal(f.calls.length, 0);
});

test('socket observer preserves all native sends and restores only its own wrapper', async () => {
  global.PLAZA_CONFIG = { base: 'https://api.example/wuwa-tappo-plus/v1' };
  const { observeRoomSocket } = await import('../web/plaza/native.mjs');
  const sent = [], changes = [], listeners = new Map();
  function Socket() {}
  Socket.prototype.send = function (value) { sent.push(value); return 42; };
  const original = Socket.prototype.send;
  const socket = Object.assign(new Socket(), { url: 'wss://api-mc-wuwatappo.kurogames-global.com/socket.io/?opaque=secret', readyState: 1,
    addEventListener: (name, cb) => listeners.set(name, cb), removeEventListener: name => listeners.delete(name) });
  const observer = observeRoomSocket({ WebSocket: Socket }, value => changes.push(value));
  assert.equal(socket.send('3'), 42); socket.send('42["cr",{}]');
  assert.deepEqual(sent, ['3', '42["cr",{}]']); assert.equal(observer.connected(), true);
  listeners.get('close')(); assert.equal(observer.connected(), false);
  observer.dispose(); assert.equal(Socket.prototype.send, original); assert.equal(listeners.size, 0);
  assert.deepEqual(changes, ['connected', 'disconnected']);
});


test('purging or changing the anonymous credential cannot restore old publication consent', async () => {
  const f = await fixture({ intent: { account: '123', room: 'Room1234' } });
  f.controller.request = async (op, args) => ({ ...(await f.request(op, args)), identity_epoch: 'b'.repeat(64) });
  await f.controller.tick();
  assert.deepEqual(f.calls.map(c => c.op), ['own']);
  assert.equal(f.saved().intent, null); assert.equal(f.controller.publicState().published, false);
});


test('disconnect keeps withdrawal available and cancellation stops renewal before networking finishes', async () => {
  const f = await fixture(); await f.controller.publish();
  f.controller.update({ ...f.native, known: false, connected: false, owner: false });
  assert.equal(f.controller.publicState().published, false);
  assert.equal(f.controller.publicState().canWithdraw, true);
  assert.equal(f.controller.publicState().withdrawRoom, 'Room1234');
  f.controller.request = async () => { throw { code: 'CONNECTION_FAILED' }; };
  await f.controller.withdraw();
  assert.equal(f.saved().intent, null); assert.equal(f.saved().withdrawing, true);
  assert.equal(f.controller.publicState().canWithdraw, true);
});
