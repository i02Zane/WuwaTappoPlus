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
  const plazaCommands = [];
  const context = {
    window: extras.window || {},
    document: {
      body: extras.body || {},
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
  context.window.__wutPlazaAction = value => plazaCommands.push(JSON.parse(value));
  context.window.HTMLInputElement = HTMLInputElement;
  context.window.Event = context.Event;
  vm.runInNewContext(loadScript('rooms.js'), context);
  context.window.__WUT_ROOMS__.plazaCommands = plazaCommands;
  return context.window.__WUT_ROOMS__;
}

test('room labels accept native formats and reject invalid room numbers', () => {
  const rooms = load();
  assert.equal(rooms.parseRoomNumber('房间号： 32RH4VrDT'), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber('房间号:32RH4VrDT'), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber(' 32RH4VrDT '), '32RH4VrDT');
  assert.equal(rooms.parseRoomNumber('房间号： 32RH4VrDT 复制'), '32RH4VrDT');
  for (const invalid of ['', '房间号：', 'abc', null]) {
    assert.equal(rooms.parseRoomNumber(invalid), '');
  }
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

function historyView(withPlaza = false, fresh = false) {
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
      addEventListener(name, handler) { this[name] = handler; },
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
  let plazaList, plazaControls;
  if (withPlaza) {
    const head = element(); head.className = 'wut-rooms-head'; host.appendChild(head);
    const view = element(); view.className = 'wut-plaza-view'; host.appendChild(view);
    plazaList = element();
    plazaControls = Object.fromEntries(['.wut-plaza-note', '.wut-plaza-page', '[data-plaza="previous"]',
      '[data-plaza="next"]', '[data-plaza="refresh"]', '[data-plaza="reset"]', '[data-plaza="random"]'].map(key => [key, element()]));
    plazaControls['.wut-plaza-list'] = plazaList;
    view.querySelector = selector => plazaControls[selector] || null;
    host.querySelectorAll = () => [];
  }
  const hosts = fresh ? {} : { 'wut-rooms': host };
  const container = { querySelector: () => null,
    appendChild(child) { hosts[child.id] = child; } };
  let created = false;
  const rooms = load(fresh ? { '.room-module .panel-content': container } : {}, {
    hosts, createElement(tag) {
      if (fresh && tag === 'div' && !created) { created = true; return host; }
      return element();
    }
  });
  return { rooms, host, list, plazaList, plazaControls, insertions: () => insertions };
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

test('pinning survives stale heartbeats, acknowledges storage and can be undone', () => {
  const view = historyView();
  const older = { room_id: 'Room1234', owner: '', visits: 1, last_seen: '2026-10-27T09:00:00' };
  const newer = { room_id: 'Room5678', owner: '', visits: 1, last_seen: '2026-10-27T10:00:00' };
  view.rooms.update({ rooms: [newer, older] });
  assert.deepEqual(view.list.children.map(node => node.dataset.room), ['Room5678', 'Room1234']);
  assert.equal(view.rooms.togglePin('Room1234'), 'pinned');
  const request = view.rooms.requests.at(-1);
  assert.equal(request.action, 'pin');
  assert.equal(request.room_id, 'Room1234');
  assert.equal(request.pinned, true);
  assert.equal(view.rooms.rooms[0].room_id, 'Room1234');
  assert.equal(view.list.children[0].dataset.pinned, 'true');
  assert.equal(view.list.children[0].querySelector('.wut-room-pin').textContent, '★');

  // Old background data must not undo an unacknowledged user action.
  view.rooms.update({ rooms: [newer, { ...older, pinned: false }] });
  assert.equal(view.rooms.rooms[0].pinned, true);
  view.rooms.update({ rooms: [{ ...older, pinned: true }, newer], ack: [{ id: request.id }] });
  assert.equal(view.rooms.requests.length, 0);
  assert.equal(view.rooms.rooms[0].pinned, true);

  assert.equal(view.rooms.togglePin('Room1234'), 'unpinned');
  assert.equal(view.rooms.requests.at(-1).pinned, false);
  const unpinned = view.list.children.find(node => node.dataset.room === 'Room1234');
  assert.equal(unpinned.dataset.pinned, 'false');
  assert.deepEqual(view.list.children.map(node => node.dataset.room), ['Room5678', 'Room1234']);
});

function visibilityView(hidden = [false, true], delayed = false) {
  const hosts = {}, listeners = {};
  let inRoom = true, writes = 0;
  const eyes = hidden.map(value => ({ hidden: value, disabled: false, clicks: 0,
    classList: { contains() { return this.eye.hidden; } },
    click() { this.clicks++; if (!delayed) this.hidden = !this.hidden; } }));
  eyes.forEach(eye => { eye.classList.eye = eye; });
  const leave = { offsetWidth: 120, disabled: false };
  let members = {};
  const container = { insertBefore(node, before) { assert.equal(before, members); hosts[node.id] = node; node.parentNode = this; node.nextElementSibling = members; }, contains: node => hosts[node.id] === node, appendChild() {},
    querySelector: selector => !inRoom ? null : selector === '.leave-room-btn' ? leave : selector === '.room-members' ? members : null,
    querySelectorAll: () => eyes };
  members.parentNode = container;
  const module = { querySelector: selector => inRoom && selector === '.leave-room-btn' ? leave : null };
  const list = { children: [{ className: 'wut-rooms-empty' }] }, note = { dataset: {} };
  const history = { querySelector: selector => selector === '.wut-rooms-list' ? list : selector === '.wut-plaza-view' ? null : note };
  function createElement(tag = 'div') {
    let text = '';
    return { tagName: tag.toUpperCase(), style: {}, children: [], attributes: {}, setAttribute(key, value) { this.attributes[key] = value; },
      get textContent() { return text; }, set textContent(value) { if ((this.id || '').startsWith('wut-room-visibility')) writes++; text = value; },
      addEventListener(type, handler) { listeners[this.id + ':' + type] = handler; },
      appendChild(child) { this.children.push(child); },
      querySelector(selector) { return this.children.find(child => '#' + child.id === selector); },
      querySelectorAll(selector) { return this.children.filter(child => selector !== 'button' || child.tagName === 'BUTTON'); },
      remove() { delete hosts[this.id]; } };
  }
  const rooms = load({ '.room-module': module, '.room-module .panel-content': container }, { hosts, createElement });
  return { rooms, eyes, leave, hosts, get members() { return members; }, get control() { return hosts['wut-room-visibility']; },
    get show() { return hosts['wut-room-visibility']?.querySelector('#wut-room-visibility-show'); },
    get hide() { return hosts['wut-room-visibility']?.querySelector('#wut-room-visibility-hide'); },
    writes: () => writes,
    click(action = 'hide') { listeners['wut-room-visibility-' + action + ':click']({ preventDefault() {}, stopPropagation() {} }); },
    rebuildMembers() { members = { parentNode: container }; rooms.tick(); },
    leaveRoom() { inRoom = false; hosts['wut-rooms'] = history; rooms.tick(); } };
}

test('one-click room visibility hides only visible native members and can show everyone again', () => {
  const f = visibilityView([false, true, false]);
  assert.equal(f.control.children[0].textContent, '房间成员');
  assert.equal(f.control.nextElementSibling, f.members);
  assert.equal(f.show.title, '显示他人桌宠'); assert.equal(f.hide.title, '隐藏他人桌宠');
  assert.equal(f.show.attributes['aria-label'], '显示他人桌宠');
  assert.equal(f.hide.attributes['aria-label'], '隐藏他人桌宠');
  assert.equal(f.show.textContent, ''); assert.equal(f.hide.textContent, '');
  assert.match(f.show.className, /room-visible-btn/); assert.match(f.hide.className, /room-invisible/);
  assert.equal(f.show.disabled, false); assert.equal(f.hide.disabled, false);
  assert.equal(f.control.style.right, undefined);
  f.click(); assert.deepEqual(f.eyes.map(eye => eye.clicks), [1, 0, 1]);
  assert.equal(f.show.disabled, false); assert.equal(f.hide.disabled, true);
  f.click('show'); assert.deepEqual(f.eyes.map(eye => eye.clicks), [2, 1, 2]);
  assert.equal(f.show.disabled, true); assert.equal(f.hide.disabled, false);
  const writes = f.writes(); f.rooms.tick(); f.rooms.tick(); assert.equal(f.writes(), writes);
});

test('member header follows a rebuilt native list without duplicating controls', () => {
  const f = visibilityView([false, true]);
  const header = f.control, previous = f.members;
  f.rebuildMembers();
  assert.equal(f.control, header); assert.notEqual(f.members, previous);
  assert.equal(f.control.nextElementSibling, f.members);
  assert.equal(f.control.querySelectorAll('button').length, 2);
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

test('owner actions sit beside leave and require confirmation before publication or withdrawal', async () => {
  const hosts = {};
  let label = '', writes = 0;
  const controlButton = { get textContent() { return label; }, set textContent(value) { label = value; writes++; },
    addEventListener(name, callback) { this[name] = callback; } };
  const status = { textContent: '' };
  const parent = { insertBefore(node, before) { assert.equal(before, leave); hosts[node.id] = node; node.parentNode = parent; } };
  const leave = { offsetWidth: 135, parentNode: parent, disabled: false };
  const control = { style: {}, setAttribute() {}, querySelector: selector => selector === 'button' ? controlButton : status,
    remove() { delete hosts['wut-plaza-controls']; } };
  let dialog;
  function createElement(tag) {
    if (tag !== 'dialog') return control;
    const message = {};
    const cancel = { addEventListener(name, handler) { this[name] = handler; } };
    const ok = { addEventListener(name, handler) { this[name] = handler; } };
    dialog = { message, cancel, ok, setAttribute() {}, addEventListener() {}, showModal() { this.open = true; },
      remove() { this.open = false; delete hosts['wut-plaza-confirm']; },
      querySelector(selector) { return selector === 'p' ? message : selector === '.wut-confirm-cancel' ? cancel : ok; }
    };
    return dialog;
  }
  const module = { querySelector: selector => selector === '.room-number' ? { textContent: 'Room1234' } : null };
  const container = { querySelector: () => null, appendChild(node) { hosts[node.id] = node; } };
  const rooms = load({ '.room-module': module, '.room-module .panel-content': container, '.room-module .leave-room-btn': leave },
    { hosts, createElement, body: { appendChild(node) { hosts[node.id] = node; } } });
  const plaza = { logged: true, owner: false, room: 'Room1234', published: false, canWithdraw: false, items: [] };
  rooms.update({ rooms: [], plaza }); assert.equal(hosts['wut-plaza-controls'], undefined);
  rooms.update({ rooms: [], plaza: { ...plaza, owner: true } });
  assert.equal(control.parentNode, leave.parentNode); assert.equal(control.style.right, '145px');
  assert.equal(controlButton.textContent, '发布到广场');
  Object.defineProperty(hosts, 'wut-room-visibility', { configurable: true,
    get: () => ({ offsetWidth: 280, remove() {} }) });
  rooms.update({ rooms: [], plaza: { ...plaza, owner: true } });
  assert.equal(control.style.right, '145px');
  const priorWrites = writes;
  rooms.update({ rooms: [], plaza: { ...plaza, owner: true } }); assert.equal(writes, priorWrites);
  let pending = controlButton.click();
  assert.equal(dialog.message.textContent, '发布到广场的房间将可以被任何人访问，是否要发布？');
  assert.equal(rooms.plazaCommands.length, 0); dialog.cancel.click(); await pending;
  assert.equal(rooms.plazaCommands.length, 0); assert.equal(controlButton.disabled, false);
  pending = controlButton.click(); dialog.ok.click(); await pending;
  assert.equal(rooms.plazaCommands[0].op, 'publish'); assert.equal(controlButton.textContent, '处理中…');
  let id = rooms.plazaCommands[0].id; rooms.plazaCommands.length = 0; rooms.plazaCommands.length = 0;
  rooms.update({ rooms: [], plaza: { ...plaza, owner: true, published: true, canWithdraw: true }, ack: [{ id }] });
  assert.equal(controlButton.textContent, '从广场撤下');
  leave.disabled = true;
  rooms.update({ rooms: [], plaza: { ...plaza, owner: false, published: false, canWithdraw: true, withdrawRoom: 'Room1234' } });
  assert.equal(controlButton.disabled, false);
  assert.equal(controlButton.textContent, '从广场撤下');
  pending = controlButton.click();
  assert.equal(dialog.message.textContent, '是否要将房间从广场撤下？');
  assert.equal(rooms.plazaCommands.length, 0); dialog.ok.click(); await pending;
  assert.equal(rooms.plazaCommands[0].op, 'withdraw'); id = rooms.plazaCommands[0].id; rooms.plazaCommands.length = 0;
  leave.disabled = false;
  rooms.update({ rooms: [], plaza: { ...plaza, owner: true }, ack: [{ id }] });
  pending = controlButton.click();
  rooms.update({ rooms: [], plaza: { ...plaza, owner: false } }); await pending;
  assert.equal(rooms.plazaCommands.length, 0); assert.equal(hosts['wut-plaza-controls'], undefined);
});

test('an already selected plaza loads once when native login state becomes available', () => {
  const rooms = load(); rooms.selectTab('plaza'); assert.equal(rooms.plazaCommands.length, 0);
  rooms.update({ rooms: [], plaza: { logged: true, owner: false, items: [] } });
  assert.equal(rooms.plazaCommands.length, 1); assert.equal(rooms.plazaCommands[0].op, 'list');
  rooms.update({ rooms: [], plaza: { logged: true, owner: false, items: [] } });
  assert.equal(rooms.plazaCommands.length, 1);
});

test('switching tabs and reopening a panel respects the backend-wide refresh deadline', () => {
  const plaza = { logged: true, owner: false, items: [], nextRefreshAt: Date.now() + 30000 };
  for (let i = 0; i < 2; i++) {
    const view = historyView(true, true);
    const rooms = view.rooms;
    rooms.update({ rooms: [], plaza });
    for (const tab of ['plaza', 'recent', 'plaza']) {
      view.host.click({ target: { closest: selector =>
        selector === '[data-tab]' ? { dataset: { tab } } : null },
        preventDefault() {}, stopPropagation() {} });
      assert.equal(view.host.querySelector('.wut-plaza-view').hidden, tab !== 'plaza');
    }
    rooms.plazaRequest('list', 1);
    assert.equal(rooms.plazaCommands.length, 0);
  }
});

function plazaRoom(room_code, expires_at = new Date(Date.now() + 60000).toISOString()) {
  return { room_code, expires_at };
}

test('plaza row entry uses the native input and waits before clicking join', () => {
  const field = input(), nativeJoin = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': nativeJoin });
  rooms.update({ plaza: { logged: true, items: [plazaRoom('Room1234')] } });
  assert.equal(rooms.enterPlaza('Room1234'), 'waiting');
  assert.equal(field._value, 'Room1234'); assert.equal(nativeJoin.clicks, 0);
  rooms.tick(); assert.equal(nativeJoin.clicks, 1); assert.equal(rooms.requests.length, 0);
});

test('random entry skips only expired and invalid rooms without requesting a list', () => {
  const field = input(), nativeJoin = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': nativeJoin });
  rooms.update({ plaza: { logged: true, items: [plazaRoom('Gone1234', new Date(Date.now() - 1).toISOString()), plazaRoom('bad!'), plazaRoom('Good1234')] } });
  assert.equal(rooms.randomJoin(), 'waiting'); assert.equal(field._value, 'Good1234');
  assert.equal(rooms.randomJoin(), 'waiting'); assert.equal(field.events.length, 1);
  rooms.tick(); assert.equal(nativeJoin.clicks, 1); assert.equal(rooms.requests.length, 0);
});

test('random entry and row entry reject unavailable or expired cached rooms', () => {
  const field = input(), nativeJoin = button();
  const rooms = load({ '.join-rooms-input': field, '.join-rooms-btn': nativeJoin });
  rooms.update({ plaza: { logged: true, items: [plazaRoom('Gone1234', new Date(Date.now() - 1).toISOString())] } });
  assert.equal(rooms.randomJoin(), 'empty'); assert.equal(rooms.enterPlaza('Gone1234'), 'expired');
  rooms.update({ plaza: { logged: false, items: [plazaRoom('Good1234')] } });
  assert.equal(rooms.randomJoin(), 'unavailable'); assert.equal(rooms.enterPlaza('Good1234'), 'unavailable');
  assert.equal(nativeJoin.clicks, 0); assert.equal(field.events.length, 0); assert.equal(rooms.requests.length, 0);
});


test('empty plaza message follows login changes and pagination stays enabled during refresh cooldown', () => {
  const view = historyView(true);
  assert.match(view.plazaList.children[0].textContent, /同步/);
  view.rooms.update({ plaza: { logged: true, items: [], page: 1, hasMore: true, nextRefreshAt: Date.now() + 30000 } });
  assert.match(view.plazaList.children[0].textContent, /暂时没有/);
  assert.equal(view.plazaControls['[data-plaza="previous"]'].disabled, false);
  assert.equal(view.plazaControls['[data-plaza="next"]'].disabled, false);
  assert.equal(view.plazaControls['[data-plaza="refresh"]'].disabled, true);
  view.rooms.plazaRequest('list', 2, false);
  assert.equal(view.rooms.plazaCommands.at(-1).refresh, false);
  view.rooms.update({ plaza: { logged: false, items: [] } });
  assert.match(view.plazaList.children[0].textContent, /请先/);
});
