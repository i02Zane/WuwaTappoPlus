(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_ROOMS__ && window.__WUT_ROOMS__.version === VERSION) return 'exists';
  if (window.__WUT_ROOMS__) window.__WUT_ROOMS__.uninstall();

  var HOST = 'wut-rooms';
  var STYLE = 'wut-rooms-style';
  var VISIBILITY = 'wut-room-visibility';
  var visibilityUntil = 0;
  var visibilityTarget = false;
  var WAIT_JOIN_MS = 12000;

  var R = window.__WUT_ROOMS__ = {
    version: VERSION,
    rooms: [],
    requests: [],
    current: null,
    pending: null,
    pendingAt: 0,
    beat: 0,
    error: null
  };
  var serial = 0;
  var instance = Date.now().toString(36) + Math.random().toString(36).slice(2);
  var painted = '';
  var timeout = 0;
  var filledInput = null;

  // Colours, radii and type sizes are copied from the panel itself: rows use the
  // settings page's .setting-item look (#fbedd5, 5px, 40px tall) and the heading
  // matches the Plus block's .wut-heading, so the list reads as part of the app.
  var CSS = [
    '#wut-rooms{margin:22px 0 4px;padding:14px 0 0;',
    'border-top:1px solid rgba(142,94,67,.2);color:#8e5e43;',
    'font-family:"975MaruSC","Microsoft YaHei","Helvetica Neue",Helvetica,simSun,sans-serif;}',

    '#wut-rooms .wut-rooms-head{display:flex;flex-wrap:wrap;align-items:baseline;',
    'justify-content:space-between;gap:8px;margin:0 0 10px;}',
    '#wut-rooms .wut-rooms-title{font-size:18px;font-weight:bold;line-height:24px;}',
    '#wut-rooms .wut-rooms-note{font-size:13px;line-height:24px;color:#8e7b6a;}',
    '#wut-rooms .wut-rooms-note[data-error="true"]{color:#b4543c;}',

    '#wut-rooms .wut-rooms-list{list-style:none;margin:0;padding:0;}',

    '#wut-rooms .wut-room{position:relative;display:flex;align-items:center;',
    'box-sizing:border-box;gap:12px;min-height:40px;',
    'padding:8px 46px 8px 24px;margin:0 0 10px;border-radius:5px;background:#fbedd5;',
    'cursor:pointer;transition:background .15s ease;}',
    '#wut-rooms .wut-room:last-child{margin-bottom:0;}',
    '#wut-rooms .wut-room:hover{background:#fef6dd;}',
    '#wut-rooms .wut-room:focus-visible{outline:2px solid #e7b77c;outline-offset:1px;}',
    '#wut-rooms .wut-room[data-busy="true"]{opacity:.55;cursor:progress;}',
    // Pinned rows sort first and are exempt from the history limit; the inset
    // accent marks them without fighting the hover colour above.
    '#wut-rooms .wut-room[data-pinned="true"]{box-shadow:inset 3px 0 0 #e7b77c;}',

    // The star sits before the room number, so a favourite reads as a badge.
    // Its slot is reserved even when hidden: hovering must not shift the id.
    '#wut-rooms .wut-room-pin{flex:0 0 18px;width:18px;height:18px;padding:0;',
    'border:0;border-radius:4px;background:none;color:#c08a4a;font-family:inherit;',
    'font-size:15px;line-height:18px;cursor:pointer;opacity:0;visibility:hidden;',
    'transition:opacity .15s ease,visibility .15s ease;}',
    '#wut-rooms .wut-room-pin:hover{color:#a86a24;}',
    '#wut-rooms .wut-room:hover .wut-room-pin,',
    '#wut-rooms .wut-room:focus-within .wut-room-pin,',
    '#wut-rooms .wut-room[data-pinned="true"] .wut-room-pin{opacity:1;visibility:visible;}',

    // The room number keeps its weight; the host sits right behind it as the
    // same secondary line the settings page uses for a value, so the row reads
    // "which room, whose room" left to right.
    '#wut-rooms .wut-room-id{flex:0 0 auto;font-size:18px;line-height:24px;',
    'letter-spacing:.5px;font-variant-numeric:tabular-nums;}',
    '#wut-rooms .wut-room-owner{flex:0 1 auto;min-width:0;font-size:14px;',
    'line-height:24px;color:#8e7b6a;max-width:190px;overflow:hidden;',
    'text-overflow:ellipsis;white-space:nowrap;}',
    '#wut-rooms .wut-room-detail{flex:0 1 auto;min-width:0;margin-left:auto;',
    'font-size:14px;line-height:24px;color:#8e7b6a;max-width:320px;overflow:hidden;',
    'text-overflow:ellipsis;white-space:nowrap;font-variant-numeric:tabular-nums;}',

    '#wut-rooms .wut-room-forget{position:absolute;right:12px;top:50%;',
    'transform:translateY(-50%);width:22px;height:22px;padding:0;',
    'border-radius:50%;border:1px solid rgba(142,94,67,.18);',
    'background:rgba(142,94,67,.07);color:#8e5e43;font-family:inherit;',
    'font-size:14px;line-height:1;display:flex;align-items:center;justify-content:center;',
    'cursor:pointer;visibility:hidden;opacity:0;',
    'transition:opacity .15s ease,visibility .15s ease,background .15s ease;}',
    '#wut-rooms .wut-room:hover .wut-room-forget,',
    '#wut-rooms .wut-room:focus-within .wut-room-forget{visibility:visible;opacity:1;}',
    '#wut-rooms .wut-room-forget:hover{background:#fed291;}',

    '#wut-rooms .wut-rooms-empty{font-size:14px;line-height:24px;color:#8e7b6a;',
    'opacity:.8;padding:0 2px;}',
    '#wut-room-visibility{position:absolute;bottom:0;display:flex;gap:10px;white-space:nowrap;}'
  ].join('');

  // The panel shows "房间号： 32RH4VrDT" next to a copy button; only the token
  // matters. Anything that is not a short alphanumeric id is not a room.
  function parseRoomNumber(text) {
    var flat = String(text == null ? '' : text).replace(/\s+/g, ' ').trim();
    var labelled = flat.match(/房间号\s*[:：]?\s*([0-9A-Za-z]{4,16})/);
    if (labelled) return labelled[1];
    var tokens = flat.match(/[0-9A-Za-z]{4,16}/g);
    return tokens ? tokens[tokens.length - 1] : '';
  }

  function setValue(input, value) {
    // Vue reads the input event; assigning .value alone leaves v-model stale.
    try {
      var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
      if (desc && desc.set) desc.set.call(input, value);
      else input.value = value;
    } catch (e) {
      input.value = value;
    }
    try {
      var Ctor = window.Event || (typeof Event !== 'undefined' ? Event : null);
      if (Ctor) input.dispatchEvent(new Ctor('input', { bubbles: true }));
    } catch (e) { /* an event-less fill still leaves the text in place */ }
  }

  function enterRoom(roomId) {
    var input = document.querySelector('.join-rooms-input');
    var button = document.querySelector('.join-rooms-btn');
    if (!input || !button) {
      R.pending = null;
      R.error = '找不到加入房间的输入框';
      return 'missing';
    }
    if (filledInput !== input) {
      setValue(input, roomId);
      filledInput = input;
      // The panel forwards v-model to the pet window over IPC, then receives
      // an updated snapshot. A synchronous click can submit the previous id.
      return 'waiting';
    }
    if (input.value !== roomId) {
      R.pending = null;
      filledInput = null;
      R.error = '房间号已更改，请手动加入或重新选择';
      return 'cancelled';
    }
    if (button.disabled) {
      return 'waiting';
    }
    R.pending = null;
    filledInput = null;
    R.error = null;
    button.click();
    return 'joining';
  }

  // Joining from inside a room needs two steps: the app only renders the room
  // number input once you have left. The list is hidden while you are in a room,
  // so this is a guard for the moment the panel swaps views rather than the
  // normal path - either way it is the same input fill and real button click a
  // person would do, just in the right order.
  function join(roomId) {
    roomId = String(roomId == null ? '' : roomId).trim();
    if (!/^[0-9A-Za-z]{4,16}$/.test(roomId)) {
      R.error = '房间号格式不正确';
      render();
      return 'invalid';
    }
    if (R.pending) return 'waiting';
    R.pending = roomId;
    R.pendingAt = Date.now();
    R.error = null;
    filledInput = null;
    var leave = document.querySelector('.leave-room-btn');
    if (leave && !leave.disabled) {
      painted = '';
      render();
      leave.click();
      return 'leaving';
    }
    var result = enterRoom(roomId);
    render();
    return result;
  }

  // Pinned rooms first, newest stay first inside each group. The daemon returns
  // the same order, so this only matters while a pin is still queued, and it
  // keeps the optimistic row in place instead of letting it jump back.
  function sortRooms() {
    R.rooms.sort(function (a, b) {
      if (!!a.pinned !== !!b.pinned) return a.pinned ? -1 : 1;
      var left = String(a.last_seen || '');
      var right = String(b.last_seen || '');
      return left === right ? 0 : (left < right ? 1 : -1);
    });
  }

  // Pin or unpin a row. The star flips immediately and the request travels with
  // the next heartbeat; the daemon's list stays authoritative, so the ack is
  // what makes the change durable.
  function togglePin(roomId) {
    roomId = String(roomId == null ? '' : roomId).trim();
    if (!/^[0-9A-Za-z]{4,16}$/.test(roomId)) return 'invalid';
    var target = null;
    for (var i = 0; i < R.rooms.length; i++) {
      if (R.rooms[i].room_id === roomId) { target = R.rooms[i]; break; }
    }
    var want = !(target && target.pinned);
    R.requests.push({ id: instance + '-' + (++serial), action: 'pin',
                      room_id: roomId, pinned: want });
    if (target) target.pinned = want;
    sortRooms();
    painted = '';
    render();
    return want ? 'pinned' : 'unpinned';
  }

  function detect() {
    var module = document.querySelector('.room-module');
    if (!module) return;                    // other tab: keep the last reading
    var number = module.querySelector('.room-number');
    if (!number) {
      R.current = null;                     // the panel is out of a room
      return;
    }
    var roomId = parseRoomNumber(number.textContent);
    if (!roomId) {
      R.current = null;
      return;
    }
    var owner = module.querySelector('li.room-owner .room-member-name');
    var name = owner ? String(owner.textContent || '').trim().slice(0, 32) : '';
    if (!R.current || R.current.room_id !== roomId || R.current.owner !== name) {
      R.current = { room_id: roomId, owner: name };
    }
  }

  function ago(iso) {
    var then = Date.parse(iso || '');
    if (!isFinite(then)) return '';
    var seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时前';
    return Math.floor(seconds / 86400) + ' 天前';
  }

  function ensureStyle() {
    if (!document.getElementById(STYLE)) {
      var style = document.createElement('style');
      style.id = STYLE;
      style.textContent = CSS;
      (document.head || document.documentElement).appendChild(style);
    }
  }

  function build() {
    ensureStyle();
    var host = document.createElement('div');
    host.id = HOST;
    host.innerHTML = '<div class="wut-rooms-head">'
      + '<strong class="wut-rooms-title">最近房间</strong>'
      + '<span class="wut-rooms-note" role="status"></span></div>'
      + '<ul class="wut-rooms-list"></ul>';
    host.addEventListener('click', function (event) {
      var forget = event.target.closest('.wut-room-forget');
      var star = event.target.closest('.wut-room-pin');
      var row = event.target.closest('.wut-room');
      if (!row) return;
      event.preventDefault();
      event.stopPropagation();
      if (star) {
        togglePin(row.dataset.room);
        return;
      }
      if (forget) {
        R.requests.push({ id: instance + '-' + (++serial), action: 'forget',
                          room_id: row.dataset.room });
        painted = '';
        render();
        return;
      }
      join(row.dataset.room);
    });
    host.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      var row = event.target.closest('.wut-room');
      // The row itself joins; its two buttons keep their native key handling.
      if (!row || event.target.closest('.wut-room-forget')
          || event.target.closest('.wut-room-pin')) return;
      event.preventDefault();
      join(row.dataset.room);
    });
    return host;
  }

  // While you are inside a room the panel already shows its number, its host
  // and a leave button; a rejoin list underneath only pushes that block down
  // and invites a pointless second join, so the history belongs to the
  // "not in a room" view alone.
  function inRoom() {
    var module = document.querySelector('.room-module');
    if (!module) return false;
    return !!(module.querySelector('.room-number') || module.querySelector('.leave-room-btn'));
  }

  function memberButtons(container) {
    // The native panel renders an eye only for an online, non-local member.
    return Array.from(container.querySelectorAll('.room-members .room-visible-btn')).filter(function (button) {
      return !button.disabled;
    });
  }

  function mountVisibility(container) {
    var leave = container.querySelector('.leave-room-btn');
    var info = container.querySelector('.room-info');
    var control = document.getElementById(VISIBILITY);
    if (!inRoom() || !leave || !info) {
      if (control) control.remove();
      visibilityUntil = 0;
      return;
    }
    ensureStyle();
    if (!control) {
      control = document.createElement('div'); control.id = VISIBILITY;
      [[true, '显示他人桌宠', 'show'], [false, '隐藏他人桌宠', 'hide']].forEach(function (definition) {
        var button = document.createElement('button');
        button.id = VISIBILITY + '-' + definition[2]; button.type = 'button'; button.className = 'common-btn';
        button.textContent = definition[1];
        button.addEventListener('click', function (event) {
          event.preventDefault(); event.stopPropagation();
          if (visibilityUntil > Date.now() || !inRoom()) return;
          var current = document.querySelector('.room-module .panel-content');
          if (!current || !current.contains(control)) return;
          var show = definition[0];
          // Snapshot once: IPC updates may arrive after all native clicks return.
          var targets = memberButtons(current).filter(function (eye) {
            return eye.classList.contains('room-invisible') === show;
          });
          if (!targets.length) return;
          visibilityTarget = show;
          visibilityUntil = Date.now() + 2000;
          control.querySelectorAll('button').forEach(function (item) { item.disabled = true; });
          targets.forEach(function (eye) { eye.click(); });
          mountVisibility(current);
        });
        control.appendChild(button);
      });
      info.insertBefore(control, leave);
    }
    var buttons = memberButtons(container);
    var visible = buttons.filter(function (button) { return !button.classList.contains('room-invisible'); }).length;
    if (visibilityUntil && (visibilityTarget ? visible === buttons.length : visible === 0)) visibilityUntil = 0;
    var pending = visibilityUntil > Date.now();
    var disabled = pending || !buttons.length || leave.disabled;
    control.querySelector('#' + VISIBILITY + '-show').disabled = disabled || visible === buttons.length;
    control.querySelector('#' + VISIBILITY + '-hide').disabled = disabled || visible === 0;
    control.style.right = ((leave.offsetWidth || 0) + 10) + 'px';
    control.setAttribute('aria-busy', pending ? 'true' : 'false');
    control.title = buttons.length ? '仅切换当前在线的其他桌宠，不影响自己的桌宠' : '当前没有可切换的其他桌宠';
  }

  function mount() {
    var container = document.querySelector('.room-module .panel-content');
    if (!container) return;
    mountVisibility(container);
    if (inRoom()) {
      var stale = document.getElementById(HOST);
      if (stale) stale.remove();
      return;
    }
    if (document.getElementById(HOST)) return;
    var host = build();
    var anchor = container.querySelector('.join-rooms');
    if (anchor && anchor.parentNode === container) container.insertBefore(host, anchor.nextSibling);
    else container.appendChild(host);
    painted = '';
    render();
  }

  function note() {
    if (R.pending) return '正在进入 ' + R.pending + '…';
    if (R.error) return R.error;
    if (Date.now() - R.beat > 5000) return 'Plus 后台未连接，记录不会保存';
    return R.rooms.length ? '点一下回到该房间，点 ☆ 收藏并置顶' : '';
  }

  function render() {
    var host = document.getElementById(HOST);
    if (!host) return;
    var key = JSON.stringify([R.rooms, R.pending, R.error, Date.now() - R.beat > 5000,
                              Math.floor(Date.now() / 60000)]);
    if (key === painted) return;
    painted = key;
    var text = note();
    var badge = host.querySelector('.wut-rooms-note');
    badge.textContent = text;
    badge.dataset.error = R.error ? 'true' : 'false';
    var list = host.querySelector('.wut-rooms-list');
    if (!R.rooms.length) {
      if (list.children.length === 1 && list.children[0].className === 'wut-rooms-empty') return;
      list.textContent = '';
      var empty = document.createElement('li');
      empty.className = 'wut-rooms-empty';
      empty.textContent = '进入过的房间会记录在这里。';
      list.appendChild(empty);
      return;
    }
    // Keep each room's DOM node across heartbeats and time-label updates so
    // hover transitions and keyboard focus survive background synchronization.
    var oldRows = new Map();
    Array.from(list.children).forEach(function (child) {
      if (child.dataset.room) oldRows.set(child.dataset.room, child);
      else child.remove();
    });
    function part(row, className, tag) {
      var child = row.querySelector('.' + className);
      if (!child) {
        child = document.createElement(tag || 'span');
        child.className = className;
        row.appendChild(child);
      }
      return child;
    }
    function setText(node, value) {
      if (node.textContent !== value) node.textContent = value;
    }
    R.rooms.forEach(function (room, index) {
      var row = oldRows.get(room.room_id) || document.createElement('li');
      oldRows.delete(room.room_id);
      row.className = 'wut-room';
      row.dataset.room = room.room_id;
      row.dataset.busy = R.pending === room.room_id ? 'true' : 'false';
      row.dataset.pinned = room.pinned ? 'true' : 'false';
      row.tabIndex = 0;
      row.setAttribute('role', 'button');
      // Created before the number so the star reads as a badge; the reserved
      // slot keeps the id from jumping when an unpinned row is hovered.
      var pin = row.querySelector('.wut-room-pin');
      if (!pin) {
        pin = document.createElement('button');
        pin.type = 'button';
        pin.className = 'wut-room-pin';
        row.insertBefore(pin, row.firstChild || null);
      }
      pin.title = room.pinned ? '取消收藏' : '收藏并置顶';
      pin.setAttribute('aria-label',
        (room.pinned ? '取消收藏 ' : '收藏 ') + room.room_id);
      pin.setAttribute('aria-pressed', room.pinned ? 'true' : 'false');
      setText(pin, room.pinned ? '★' : '☆');
      var id = part(row, 'wut-room-id');
      setText(id, room.room_id);

      // Host first, then the visit count and time pushed to the right edge.
      var owner = part(row, 'wut-room-owner');
      setText(owner, room.owner || '');
      owner.title = room.owner || '';
      owner.hidden = !room.owner;

      var parts = [];
      if (room.visits > 1) parts.push(room.visits + ' 次');
      var when = ago(room.last_seen);
      if (when) parts.push(when);
      var detail = part(row, 'wut-room-detail');
      setText(detail, parts.join(' · '));
      detail.title = detail.textContent;

      var forget = part(row, 'wut-room-forget', 'button');
      forget.type = 'button';
      forget.className = 'wut-room-forget';
      setText(forget, '×');
      forget.setAttribute('aria-label', '删除 ' + room.room_id + ' 的记录');

      row.setAttribute('aria-label', '回到房间 ' + room.room_id
        + (room.pinned ? '，已收藏' : '')
        + (room.owner ? '，房主 ' + room.owner : ''));
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
    oldRows.forEach(function (row) { row.remove(); });
  }

  function tick() {
    detect();
    if (R.pending && R.current && R.current.room_id === R.pending) {
      R.pending = null;
      filledInput = null;
    }
    if (R.pending && Date.now() - R.pendingAt > WAIT_JOIN_MS) {
      R.pending = null;
      filledInput = null;
      R.error = document.querySelector('.leave-room-btn')
        ? '离开房间超时' : '等待加入按钮超时，请检查桌宠联机连接后重试';
      painted = '';
    }
    if (R.pending && document.querySelector('.join-rooms-input')) enterRoom(R.pending);
    mount();
    render();
  }

  R.update = function (state) {
    R.beat = Date.now();
    R.rooms = (state && state.rooms) || [];
    var ack = (state && state.ack) || [];
    var answered = {};
    ack.forEach(function (item) { answered[item.id] = true; });
    // A pin the daemon has not answered yet must not flicker back on the next
    // heartbeat: keep the queued intent on top of the authoritative list. An
    // answered request is finished, including when the acknowledgement failed.
    R.requests.forEach(function (request) {
      if (request.action !== 'pin' || answered[request.id]) return;
      R.rooms.forEach(function (room) {
        if (room.room_id === request.room_id) room.pinned = request.pinned;
      });
    });
    sortRooms();
    if (ack.length) {
      R.requests = R.requests.filter(function (request) {
        return !answered[request.id];
      });
      ack.forEach(function (item) { if (item.error) R.error = '记录未能保存'; });
    }
    mount();
    render();
  };
  R.join = join;
  R.togglePin = togglePin;
  R.parseRoomNumber = parseRoomNumber;
  R.tick = tick;

  var observer = new MutationObserver(function () { mount(); });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  timeout = setInterval(tick, 1000);

  R.uninstall = function () {
    observer.disconnect();
    clearInterval(timeout);
    [HOST, STYLE, VISIBILITY].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.remove();
    });
    delete window.__WUT_ROOMS__;
  };

  tick();
  return 'installed v' + R.version;
})()
