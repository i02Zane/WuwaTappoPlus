const assert = require('node:assert/strict');
const vm = require('node:vm');
const { version, loadScript } = require('./scripts');
const { test } = require('node:test');

function input() {
  const node = { disabled: false, events: [], get value() { return this._value; },
    dispatchEvent(event) { this.events.push(event.type); } };
  return node;
}

function button() {
  const node = { disabled: false, clicks: 0, click() { this.clicks++; } };
  return node;
}

function load(nodes = {}, extras = {}) {
  const context = {
    window: extras.window || {},
    document: {
      body: {},
      head: { appendChild() {} },
      documentElement: { appendChild() {} },
      querySelector: selector => nodes[selector] || null,
      getElementById: id => (extras.hosts || {})[id] || null,
      createElement: extras.createElement || (() => ({ style: {}, setAttribute() {}, appendChild() {} }))
    },
    MutationObserver: class { observe() {} disconnect() {} },
    setInterval: () => 1,
    clearInterval() {},
    Event: class { constructor(type) { this.type = type; } },
    Date, Math, Promise
  };
  // Vue reads the input event; the injected script must go through the native
  // value setter so v-model actually updates.
  function HTMLInputElement() {}
  Object.defineProperty(HTMLInputElement.prototype, 'value', {
    set(value) { this._value = value; },
    get() { return this._value; }
  });
  context.window.HTMLInputElement = HTMLInputElement;
  context.window.Event = context.Event;
  vm.runInNewContext(loadScript('rooms.js'), context);
  return context.window.__WUT_ROOMS__;
}

test('room number is read from the label the panel renders', () => {
  const rooms = load();
  assert.equal(rooms.parseRoomNumber('房间号： 32RH4VrDT'), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber('房间号:32RH4VrDT'), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber(' 32RH4VrDT '), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber('房间号： 32RH4VrDT 复制'), '32RH4VrDT');
});

test('room history shares the product version and replaces different script versions only once', () => {
  let uninstalled = 0;
  const window = { __WUT_ROOMS__: { version: '0.0.0', uninstall() { uninstalled++; } } };
  const rooms = load({}, { window });
  assert.equal(rooms.version, version);
  assert.equal(uninstalled, 1);
  assert.equal(load({}, { window }), rooms);
  assert.equal(uninstalled, 1);
});

test('anything that is not a room number is rejected', () => {
  const rooms = load();
  assert.equal(rooms.parseRoomNumber(''), '');
  assert.equal(rooms.parseRoomNumber('房间号：'), '');
  assert.equal(rooms.parseRoomNumber('abc'), '');
  assert.equal(rooms.parseRoomNumber(null), '');
});

test('joining from the room list fills the input and clicks the real button', () => {
  const field = input();
  const join = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });

  assert.equal(rooms.join('32RH4VrDT'), 'waiting');
  assert.equal(field._value, '32RH4VrDT');
  assert.deepEqual(field.events, ['input']);
  assert.equal(join.clicks, 0);
  rooms.tick();
  assert.equal(join.clicks, 1);
  rooms.tick();
  assert.equal(join.clicks, 1);
  assert.equal(rooms.error, null);
});

test('joining while in a room leaves first, then fills once the input appears', () => {
  const leave = button();
  const nodes = { '.leave-room-btn': leave };
  const rooms = load(nodes);

  assert.equal(rooms.join('32RH4VrDT'), 'leaving');
  assert.equal(leave.clicks, 1);
  assert.equal(rooms.pending, '32RH4VrDT');

  const field = input();
  const join = button();
  nodes['.join-rooms-input'] = field;
  nodes['.join-rooms-btn'] = join;
  delete nodes['.leave-room-btn'];
  rooms.tick();

  assert.equal(field._value, '32RH4VrDT');
  assert.equal(join.clicks, 0);
  rooms.tick();
  assert.equal(join.clicks, 1);
  assert.equal(rooms.pending, null);
});

test('a malformed room number never reaches the input', () => {
  const field = input();
  const join = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });

  assert.equal(rooms.join('<img src=x>'), 'invalid');
  assert.equal(field._value, undefined);
  assert.equal(join.clicks, 0);
  assert.match(rooms.error, /格式/);
});

test('a missing input is reported instead of clicking something else', () => {
  const rooms = load({ '.join-rooms-btn': button() });
  assert.equal(rooms.join('32RH4VrDT'), 'missing');
  assert.match(rooms.error, /输入框/);
});

test('joining waits for the native button to become enabled without filling repeatedly', () => {
  const field = input();
  const join = button();
  join.disabled = true;
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });

  assert.equal(rooms.join('32RH4VrDT'), 'waiting');
  rooms.tick();
  assert.equal(join.clicks, 0);
  assert.equal(rooms.pending, '32RH4VrDT');
  assert.deepEqual(field.events, ['input']);
  join.disabled = false;
  rooms.tick();
  assert.equal(join.clicks, 1);
  assert.equal(rooms.pending, null);
  assert.equal(rooms.error, null);
});

test('waiting for an unavailable native connection times out without clicking', () => {
  const field = input();
  const join = button();
  join.disabled = true;
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });
  rooms.join('32RH4VrDT');
  rooms.pendingAt = Date.now() - 13000;
  rooms.tick();
  assert.equal(rooms.pending, null);
  assert.equal(join.clicks, 0);
  assert.match(rooms.error, /联机连接/);
});

test('editing the room number cancels the queued join and preserves manual input', () => {
  const field = input();
  const join = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });
  rooms.join('32RH4VrDT');
  field._value = 'Other123';
  rooms.tick();
  assert.equal(rooms.pending, null);
  assert.equal(join.clicks, 0);
  assert.equal(field._value, 'Other123');
  assert.match(rooms.error, /已更改/);
});

test('repeated history clicks do not submit a stale room id', () => {
  const field = input();
  const join = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': join });
  rooms.join('32RH4VrDT');
  assert.equal(rooms.join('Other123'), 'waiting');
  assert.equal(join.clicks, 0);
  assert.equal(field._value, '32RH4VrDT');
  rooms.tick();
  assert.equal(join.clicks, 1);
});

test('waiting for the leave to finish is bounded', () => {
  const rooms = load({ '.leave-room-btn': button() });
  rooms.join('32RH4VrDT');
  rooms.pendingAt = Date.now() - 13000;
  rooms.tick();
  assert.equal(rooms.pending, null);
  assert.match(rooms.error, /超时/);
});

test('the list disappears while you are inside a room', () => {
  const removed = [];
  const hosts = {
    'wut-rooms': { remove() { removed.push('list'); delete hosts['wut-rooms']; } }
  };
  const number = { textContent: '房间号： 32RH4VrDT' };
  const module = { querySelector: selector => (selector === '.room-number' ? number : null) };
  const rooms = load(
    { '.room-module': module, '.room-module .panel-content': { appendChild() {}, querySelector: () => null } },
    { hosts }
  );

  rooms.tick();

  assert.deepEqual(removed, ['list']);
  assert.equal(rooms.current.room_id, '32RH4VrDT');
});

test('update replaces the list and drops acknowledged requests', () => {
  const rooms = load();
  rooms.requests.push({ id: 'a-1', action: 'forget', room_id: '32RH4VrDT' });
  rooms.update({ rooms: [{ room_id: '32RH4VrDT', owner: '白柳', visits: 2, last_seen: '2026-10-27T09:00:00' }],
    ack: [{ id: 'a-1' }] });
  assert.equal(rooms.requests.length, 0);
  assert.equal(rooms.rooms.length, 1);
  assert.equal(rooms.rooms[0].owner, '白柳');
});

test('a failed acknowledgement is surfaced', () => {
  const rooms = load();
  rooms.update({ rooms: [], ack: [{ id: 'a-2', error: true }] });
  assert.match(rooms.error, /未能保存/);
});

function historyView() {
  let insertions = 0;
  function element() {
    let content = '';
    return {
      children: [], dataset: {}, style: {}, className: '', parentNode: null,
      get textContent() { return content; },
      set textContent(value) {
        content = value;
        this.children.forEach(child => { child.parentNode = null; });
        this.children = [];
      },
      setAttribute() {},
      appendChild(child) { this.insertBefore(child, null); },
      insertBefore(child, before) {
        insertions++;
        if (child.parentNode) child.remove();
        const index = before ? this.children.indexOf(before) : this.children.length;
        this.children.splice(index, 0, child);
        child.parentNode = this;
      },
      remove() {
        if (this.parentNode) {
          const siblings = this.parentNode.children;
          siblings.splice(siblings.indexOf(this), 1);
          this.parentNode = null;
        }
      },
      querySelector(selector) { return this.children.find(child => child.className === selector.slice(1)) || null; }
    };
  }
  const host = element();
  const badge = element();
  badge.className = 'wut-rooms-note';
  const list = element();
  list.className = 'wut-rooms-list';
  host.appendChild(badge);
  host.appendChild(list);
  const rooms = load({}, { hosts: { 'wut-rooms': host }, createElement: element });
  return { rooms, list, insertions: () => insertions };
}

test('unchanged background heartbeats preserve hovered rows and their children', () => {
  const view = historyView();
  const data = [{ room_id: 'Room1234', owner: '房主', visits: 1, last_seen: new Date().toISOString() }];
  view.rooms.update({ rooms: data });
  const row = view.list.children[0];
  const children = row.children.slice();
  const insertions = view.insertions();
  for (let i = 0; i < 5; i++) view.rooms.update({ rooms: JSON.parse(JSON.stringify(data)) });
  assert.equal(view.list.children[0], row);
  assert.deepEqual(row.children, children);
  assert.equal(view.insertions(), insertions);
});

test('room metadata and status changes update existing nodes without reinserting them', () => {
  const view = historyView();
  const data = [{ room_id: 'Room1234', owner: '', visits: 1, last_seen: new Date().toISOString() }];
  view.rooms.update({ rooms: data });
  const row = view.list.children[0];
  const owner = row.querySelector('.wut-room-owner');
  assert.equal(owner.hidden, true);
  const insertions = view.insertions();
  view.rooms.update({ rooms: [{ ...data[0], owner: '房主', visits: 2,
    last_seen: new Date(Date.now() - 120000).toISOString() }], ack: [{ id: 'x', error: true }] });
  assert.equal(view.list.children[0], row);
  assert.equal(row.querySelector('.wut-room-owner'), owner);
  assert.equal(owner.textContent, '房主');
  assert.equal(owner.hidden, false);
  assert.match(row.querySelector('.wut-room-detail').textContent, /2 次/);
  assert.equal(view.insertions(), insertions);
});

test('reordering and deleting history retain the surviving room nodes', () => {
  const view = historyView();
  const a = { room_id: 'Room1234', visits: 1 };
  const b = { room_id: 'Room5678', visits: 1 };
  view.rooms.update({ rooms: [a, b] });
  const [first, second] = view.list.children;
  view.rooms.update({ rooms: [b, a] });
  assert.deepEqual(view.list.children, [second, first]);
  view.rooms.update({ rooms: [a] });
  assert.deepEqual(view.list.children, [first]);
  assert.equal(second.parentNode, null);
  view.rooms.update({ rooms: [] });
  assert.equal(view.list.children.length, 1);
  assert.equal(view.list.children[0].className, 'wut-rooms-empty');
  view.rooms.update({ rooms: [a] });
  assert.equal(view.list.children.length, 1);
  assert.equal(view.list.children[0].dataset.room, a.room_id);
});

test('a pinned room sorts first and the star queues the change', () => {
  const view = historyView();
  const older = { room_id: 'Room1234', owner: '', visits: 1, last_seen: '2026-10-27T09:00:00' };
  const newer = { room_id: 'Room5678', owner: '', visits: 1, last_seen: '2026-10-27T10:00:00' };
  view.rooms.update({ rooms: [newer, older] });
  assert.deepEqual(view.list.children.map(node => node.dataset.room),
    ['Room5678', 'Room1234']);

  assert.equal(view.rooms.togglePin('Room1234'), 'pinned');
  const request = view.rooms.requests.at(-1);
  assert.equal(request.action, 'pin');
  assert.equal(request.room_id, 'Room1234');
  assert.equal(request.pinned, true);
  assert.equal(view.rooms.rooms[0].room_id, 'Room1234');
  assert.equal(view.list.children[0].dataset.pinned, 'true');
  assert.equal(view.list.children[0].querySelector('.wut-room-pin').textContent, '★');
});

test('a pin that has not been stored yet is not reverted by the next heartbeat', () => {
  const view = historyView();
  const room = { room_id: 'Room1234', owner: '', visits: 1, last_seen: '2026-10-27T09:00:00' };
  view.rooms.update({ rooms: [room] });
  view.rooms.togglePin('Room1234');

  // The daemon has not seen the request yet and still reports the old row.
  view.rooms.update({ rooms: [{ ...room, pinned: false }] });
  assert.equal(view.rooms.rooms[0].pinned, true);

  // Its acknowledgement ends the optimistic phase without undoing the star.
  const id = view.rooms.requests[0].id;
  view.rooms.update({ rooms: [{ ...room, pinned: true }], ack: [{ id }] });
  assert.equal(view.rooms.requests.length, 0);
  assert.equal(view.rooms.rooms[0].pinned, true);
});

test('unpinning drops the star and still records the intent', () => {
  const view = historyView();
  view.rooms.update({ rooms: [{ room_id: 'Room1234', visits: 1,
    last_seen: '2026-10-27T09:00:00', pinned: true }] });
  assert.equal(view.rooms.togglePin('Room1234'), 'unpinned');
  assert.equal(view.rooms.requests.at(-1).pinned, false);
  assert.equal(view.list.children[0].dataset.pinned, 'false');
});

function visibilityView(hidden = [false, true], delayed = false) {
  const hosts = {}, listeners = {};
  let inRoom = true, writes = 0;
  const eyes = hidden.map(value => ({ hidden: value, disabled: false, clicks: 0,
    classList: { contains() { return this.eye.hidden; } },
    click() { this.clicks++; if (!delayed) this.hidden = !this.hidden; } }));
  eyes.forEach(eye => { eye.classList.eye = eye; });
  const leave = { offsetWidth: 120, disabled: false };
  const info = { insertBefore(node, before) { assert.equal(before, leave); hosts[node.id] = node; } };
  const container = { contains: node => hosts[node.id] === node, appendChild() {},
    querySelector: selector => !inRoom ? null : selector === '.leave-room-btn' ? leave : selector === '.room-info' ? info : null,
    querySelectorAll: () => eyes };
  const module = { querySelector: selector => inRoom && selector === '.leave-room-btn' ? leave : null };
  const list = { children: [{ className: 'wut-rooms-empty' }] }, note = { dataset: {} };
  const history = { querySelector: selector => selector === '.wut-rooms-list' ? list : note };
  function createElement() {
    let text = '';
    return { style: {}, children: [], setAttribute() {},
      get textContent() { return text; }, set textContent(value) { if ((this.id || '').startsWith('wut-room-visibility')) writes++; text = value; },
      addEventListener(type, handler) { listeners[this.id + ':' + type] = handler; },
      appendChild(child) { this.children.push(child); },
      querySelector(selector) { return this.children.find(child => '#' + child.id === selector); },
      querySelectorAll() { return this.children; },
      remove() { delete hosts[this.id]; } };
  }
  const rooms = load({ '.room-module': module, '.room-module .panel-content': container }, { hosts, createElement });
  return { rooms, eyes, leave, hosts, get control() { return hosts['wut-room-visibility']; },
    get show() { return hosts['wut-room-visibility']?.querySelector('#wut-room-visibility-show'); },
    get hide() { return hosts['wut-room-visibility']?.querySelector('#wut-room-visibility-hide'); },
    writes: () => writes,
    click(action = 'hide') { listeners['wut-room-visibility-' + action + ':click']({ preventDefault() {}, stopPropagation() {} }); },
    leaveRoom() { inRoom = false; hosts['wut-rooms'] = history; rooms.tick(); } };
}

test('one-click room visibility hides only visible native members and can show everyone again', () => {
  const f = visibilityView([false, true, false]);
  assert.equal(f.show.textContent, '显示他人桌宠'); assert.equal(f.hide.textContent, '隐藏他人桌宠');
  assert.equal(f.show.disabled, false); assert.equal(f.hide.disabled, false);
  assert.equal(f.control.style.right, '130px');
  f.click(); assert.deepEqual(f.eyes.map(eye => eye.clicks), [1, 0, 1]);
  assert.equal(f.show.disabled, false); assert.equal(f.hide.disabled, true);
  f.click('show'); assert.deepEqual(f.eyes.map(eye => eye.clicks), [2, 1, 2]);
  assert.equal(f.show.disabled, true); assert.equal(f.hide.disabled, false);
  const writes = f.writes(); f.rooms.tick(); f.rooms.tick(); assert.equal(f.writes(), writes);
});

test('room visibility waits for native state and ignores repeated clicks during IPC updates', () => {
  const f = visibilityView([false, true], true);
  f.click(); f.click(); assert.deepEqual(f.eyes.map(eye => eye.clicks), [1, 0]);
  assert.equal(f.hide.disabled, true); assert.equal(f.show.disabled, true);
  f.eyes[0].hidden = true; f.rooms.tick();
  assert.equal(f.hide.disabled, true); assert.equal(f.show.disabled, false);
});

test('room visibility has no targets for a solo room and is removed on leave or uninstall', () => {
  const solo = visibilityView([]); assert.equal(solo.hide.disabled, true); assert.equal(solo.show.disabled, true);
  solo.click(); assert.equal(solo.hide.disabled, true); assert.equal(solo.show.disabled, true);
  solo.leaveRoom(); assert.equal(solo.control, undefined);
  const room = visibilityView([true]); room.rooms.uninstall(); assert.equal(room.control, undefined);
});
