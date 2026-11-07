(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_ROOMS__ && window.__WUT_ROOMS__.version === VERSION) return 'exists';
  if (window.__WUT_ROOMS__) window.__WUT_ROOMS__.uninstall();

  var HOST = 'wut-rooms';
  var PLAZA_CONTROLS = 'wut-plaza-controls';
  var activeTab = 'recent';
  var plaza = { items: [], logged: null, owner: false, published: false };
  var plazaPainted = '';
  var plazaNote = '';
  var confirming = false;
  var confirmation = null;
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
    '#wut-room-visibility{display:flex;flex-shrink:0;align-items:center;height:36px;box-sizing:border-box;margin:28px 0 0;padding:0 26px;border-radius:5px;background:#fbedd5;color:#8e5e43;}'
    + '#wut-room-visibility .wut-members-title{flex:0 0 240px;font-size:18px;font-weight:400;}'
    + '#wut-room-visibility+.room-members{margin-top:10px;}'
    + '#wut-room-visibility .wut-room-eye{flex:0 0 36px;width:36px;height:36px;border:0;padding:0;cursor:pointer;background-color:transparent;background-position:center;background-repeat:no-repeat;background-size:24px auto;display:inline-flex;align-items:center;justify-content:center;border-radius:6px;}'
    + '#wut-room-visibility .wut-room-eye:hover:not(:disabled){background-color:rgba(185,123,81,.1);}'
    + '#wut-room-visibility .wut-room-eye svg{width:24px;height:24px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;}'
    + '#wut-room-visibility .wut-room-eye:disabled{opacity:.4;cursor:default;}'
    + '#wut-room-visibility .wut-room-eye:focus-visible{outline:2px solid #b97b51;outline-offset:2px;border-radius:6px;}'
  ].join('');

  // The panel shows "房间号： 32RH4VrDT" next to a copy button; only the token
  // matters. Anything that is not a short alphanumeric id is not a room.
  var PLAZA_CSS = '#wut-rooms [hidden]{display:none!important;}'
    + '#wut-rooms .wut-rooms-tabs{display:flex;gap:8px;margin-bottom:12px;position:relative;}'
    + '#wut-rooms .wut-rooms-tab{font:inherit;font-size:14px;color:#8e5e43;background:#fed291;'
    + 'border:1px solid #e7b77c;border-radius:20px;padding:3px 14px;cursor:pointer;white-space:nowrap;}'
    + '#wut-rooms .wut-rooms-tab[aria-selected="false"]{background:transparent;}'
    + '#wut-rooms .wut-rooms-tab:disabled{opacity:.45;cursor:default;}'
    + '.room-module .panel-content.wut-room-browser{display:flex;flex-direction:column;overflow:hidden;min-height:0;}'
    // The native create-room button is inline-block; as a flex item it would
    // stretch to the panel width. Keep it content-sized.
    + '.room-module .panel-content.wut-room-browser>.create-room-btn{align-self:flex-start;}'
    + '.wut-room-browser> :not(#wut-rooms){flex-shrink:0;}'
    + '.wut-room-browser>#wut-rooms{display:flex;flex-direction:column;flex:1;min-height:0;}'
    + '#wut-rooms .wut-rooms-tabs,#wut-rooms .wut-rooms-head{flex-shrink:0;}'
    + '#wut-rooms .wut-rooms-list{min-height:0;overflow-y:auto;}'
    + '#wut-rooms .wut-plaza-view{display:flex;flex-direction:column;flex:1;min-height:0;}'
    + '#wut-rooms .wut-plaza-list{flex:1;min-height:0;overflow-y:auto;list-style:none;padding:0;margin:10px 0;}'
    + '#wut-rooms .wut-plaza-row{padding-right:24px;}'
    + '#wut-rooms .wut-plaza-help{display:inline-flex;align-items:center;justify-content:center;'
    + 'width:15px;height:15px;margin-left:5px;border:1px solid currentColor;border-radius:50%;'
    + 'font-size:11px;line-height:1;cursor:help;opacity:.8;}'
    + '#wut-rooms .wut-plaza-tooltip{position:absolute;top:calc(100% + 6px);left:0;z-index:20;'
    + 'max-width:300px;padding:8px 10px;border:1px solid #e7b77c;border-radius:10px;'
    + 'background:#fff3dd;color:#8e5e43;font-size:12px;line-height:1.6;'
    + 'box-shadow:0 6px 18px rgba(70,40,20,.18);}'
    + '#wut-rooms .wut-plaza-note{flex-shrink:0;font-size:13px;color:#8e7b6a;}'
    + '#wut-rooms .wut-plaza-pages{display:flex;flex-shrink:0;gap:10px;align-items:center;'
    + 'margin-top:auto;padding:12px 0 0;font-size:13px;}'
    + '#wut-rooms [data-plaza="random"]{margin-left:auto;}'
    + '#wut-plaza-controls{position:relative;display:inline-flex;flex-direction:column;align-items:flex-start;max-width:100%;gap:6px;margin:10px 10px 0 0;vertical-align:top;}'
    + '.room-info>#wut-plaza-controls{position:absolute;bottom:0;margin:0;}'
    + '.room-info>#wut-plaza-controls .wut-plaza-action-status{position:absolute;top:calc(100% + 7px);right:0;width:300px;}'
    + '#wut-plaza-controls .common-btn{white-space:nowrap;}'
    + '#wut-plaza-controls .wut-plaza-action-status{display:block;max-width:300px;'
    + 'font-size:13px;color:#b4543c;overflow-wrap:anywhere;}'
    + '#wut-plaza-controls .wut-plaza-action-status:empty{display:none;}'
    + '#wut-plaza-confirm{max-width:480px;width:calc(100% - 64px);box-sizing:border-box;'
    + 'padding:26px;border:2px solid #e7b77c;border-radius:18px;background:#fff3dd;color:#8e5e43;'
    + 'font:inherit;box-shadow:0 12px 40px rgba(70,40,20,.2);}'
    + '#wut-plaza-confirm::backdrop{background:rgba(60,35,20,.35);}'
    + '#wut-plaza-confirm p{font-size:18px;line-height:1.6;margin:0 0 24px;}'
    + '#wut-plaza-confirm .wut-confirm-buttons{display:flex;justify-content:flex-end;gap:12px;}'
    + '#wut-plaza-confirm .common-btn{min-width:90px;}';

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
      style.textContent = CSS + PLAZA_CSS;
      (document.head || document.documentElement).appendChild(style);
    }
  }

  function build() {
    ensureStyle();
    var host = document.createElement('div');
    host.id = HOST;
    host.innerHTML = '<div class="wut-rooms-tabs" role="tablist" aria-label="房间列表">'
      + '<button type="button" class="wut-rooms-tab" role="tab" data-tab="recent">最近房间</button>'
      + '<button type="button" class="wut-rooms-tab" role="tab" data-tab="plaza">联机广场（测试）'
      + '<span class="wut-plaza-help" aria-hidden="true">?</span></button>'
      + '<div class="wut-plaza-tooltip" role="tooltip" hidden>'
      + '实验性功能，随时可能调整或关闭<br>'
      + '主动发布时会把房间码上传到广场服务器<br>'
      + '房间由玩家主动公开，是否可加入由原生软件判断。'
      + '</div></div>'
      + '<div class="wut-rooms-head">'
      + '<strong class="wut-rooms-title">最近房间</strong>'
      + '<span class="wut-rooms-note" role="status"></span></div>'
      + '<ul class="wut-rooms-list" role="tabpanel"></ul>'
      + '<div class="wut-plaza-view" role="tabpanel" hidden>'
      + '<div class="wut-plaza-note" role="status"></div>'
      + '<ul class="wut-plaza-list"></ul><div class="wut-plaza-pages">'
      + '<button type="button" class="wut-rooms-tab" data-plaza="previous">上一页</button>'
      + '<span class="wut-plaza-page"></span><button type="button" class="wut-rooms-tab" data-plaza="next">下一页</button>'
      + '<button type="button" class="wut-rooms-tab" data-plaza="refresh">刷新</button>'
      + '<button type="button" class="wut-rooms-tab" data-plaza="random">随机进入</button>'
      + '<button type="button" class="wut-rooms-tab" data-plaza="reset" hidden>重建广场身份</button></div></div>';
    var help = host.querySelector('.wut-plaza-help');
    var tooltip = host.querySelector('.wut-plaza-tooltip');
    if (help && tooltip) {
      var showTip = function () { tooltip.hidden = false; };
      var hideTip = function () { tooltip.hidden = true; };
      help.addEventListener('mouseenter', showTip);
      help.addEventListener('mouseleave', hideTip);
      // Keep the icon from switching tabs when clicked; it only explains them.
      help.addEventListener('click', function (event) {
        event.preventDefault(); event.stopPropagation(); showTip();
      });
    }
    host.addEventListener('click', function (event) {
      var tab = event.target.closest('[data-tab]');
      var action = event.target.closest('[data-plaza]');
      if (tab || action) {
        event.preventDefault(); event.stopPropagation();
        if (tab) {
          activeTab = tab.dataset.tab === 'plaza' ? 'plaza' : 'recent';
          if (activeTab === 'plaza') plazaRequest('list', 0);
        } else if (action.dataset.plaza === 'reset') {
          plazaRequest('reset', 0);
        } else if (action.dataset.plaza === 'random') {
          randomJoin();
        } else {
          var page = plaza.page || 0;
          plazaRequest('list', action.dataset.plaza === 'next' ? page + 1 : action.dataset.plaza === 'previous' ? page - 1 : page, action.dataset.plaza === 'refresh');
        }
        renderPlaza(); return;
      }
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
      if (row.dataset.source === 'plaza') enterPlaza(row.dataset.room);
      else join(row.dataset.room);
    });
    host.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      var row = event.target.closest('.wut-room');
      // The row itself joins; its two buttons keep their native key handling.
      if (!row || event.target.closest('.wut-room-forget')
          || event.target.closest('.wut-room-pin')) return;
      event.preventDefault();
      if (row.dataset.source === 'plaza') enterPlaza(row.dataset.room);
      else join(row.dataset.room);
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

  function nativeVisibilityIcons() {
    var icons = {};
    Array.from(document.styleSheets || []).forEach(function (sheet) {
      try {
        Array.from(sheet.cssRules || []).forEach(function (rule) {
          var selector = rule.selectorText || '';
          if (selector.indexOf('.room-visible-btn') < 0 || selector.indexOf(':') >= 0 || !rule.style || !rule.style.backgroundImage) return;
          icons[selector.indexOf('.room-invisible') >= 0 ? 'hide' : 'show'] = rule.style.backgroundImage;
        });
      } catch (e) { /* A changed/cross-origin stylesheet uses the inline fallback. */ }
    });
    return icons;
  }

  function mountVisibility(container) {
    var leave = container.querySelector('.leave-room-btn');
    var members = container.querySelector('.room-members');
    var control = document.getElementById(VISIBILITY);
    if (!inRoom() || !leave || !members || !members.parentNode) {
      if (control) control.remove();
      visibilityUntil = 0;
      return;
    }
    ensureStyle();
    if (!control) {
      control = document.createElement('div'); control.id = VISIBILITY;
      var title = document.createElement('span'); title.className = 'wut-members-title'; title.textContent = '房间成员'; control.appendChild(title);
      var icons = nativeVisibilityIcons();
      [[true, '显示他人桌宠', 'show'], [false, '隐藏他人桌宠', 'hide']].forEach(function (definition) {
        var button = document.createElement('button');
        button.id = VISIBILITY + '-' + definition[2]; button.type = 'button';
        button.className = 'room-visible-btn wut-room-eye' + (definition[0] ? '' : ' room-invisible');
        button.title = definition[1]; button.setAttribute('aria-label', definition[1]);
        if (icons[definition[2]]) button.style.backgroundImage = icons[definition[2]];
        else button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'
          + (definition[0] ? '' : '<path d="m3 3 18 18"/>') + '</svg>';
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
      members.parentNode.insertBefore(control, members);
    } else if (control.parentNode !== members.parentNode || control.nextElementSibling !== members) {
      members.parentNode.insertBefore(control, members);
    }
    var buttons = memberButtons(container);
    var visible = buttons.filter(function (button) { return !button.classList.contains('room-invisible'); }).length;
    if (visibilityUntil && (visibilityTarget ? visible === buttons.length : visible === 0)) visibilityUntil = 0;
    var pending = visibilityUntil > Date.now();
    var disabled = pending || !buttons.length || leave.disabled;
    control.querySelector('#' + VISIBILITY + '-show').disabled = disabled || visible === buttons.length;
    control.querySelector('#' + VISIBILITY + '-hide').disabled = disabled || visible === 0;
    control.setAttribute('aria-busy', pending ? 'true' : 'false');
    control.title = buttons.length ? '仅切换当前在线的其他桌宠，不影响自己的桌宠' : '当前没有可切换的其他桌宠';
  }

  function mount() {
    var container = document.querySelector('.room-module .panel-content');
    if (!container) return;
    mountVisibility(container);
    ensureStyle();
    mountPlazaControls(container);
    if (container.classList) container.classList.toggle('wut-room-browser', !inRoom());
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

  function plazaRequest(op, page, refresh) {
    refresh = refresh !== false;
    if (Date.now() - R.beat > 5000 || (!plaza.logged && !(op === 'withdraw' && plaza.canWithdraw)) || R.requests.some(function (r) { return r.action === 'plaza'; })) return;
    if (op === 'list' && refresh && Date.now() < (plaza.nextRefreshAt || 0)) { renderPlaza(); return; }
    if (plaza.busy || (op === 'list' && plaza.loading)) return;
    var command = { id: instance + '-' + (++serial), op: op,
                    page: Math.max(0, Math.min(49, page || 0)), refresh: refresh };
    try {
      if (typeof window.__wutPlazaAction !== 'function') throw new Error('binding unavailable');
      window.__wutPlazaAction(JSON.stringify(command));
      if (op === 'list') plaza.loading = true; else plaza.busy = true;
    } catch (e) { R.error = 'Plus 广场连接尚未就绪，请稍后重试。'; }
    plazaNote = '';
    var container = document.querySelector('.room-module .panel-content');
    if (container) mountPlazaControls(container);
    renderPlaza();
  }

  function confirmPlaza(op) {
    return new Promise(function (resolve) {
      var dialog = document.createElement('dialog');
      dialog.id = 'wut-plaza-confirm';
      // A top-layer dialog is appended to body, outside the panel's font scope.
      var reference = document.querySelector('.room-module .leave-room-btn');
      if (reference && window.getComputedStyle) {
        var font = window.getComputedStyle(reference);
        dialog.style.fontFamily = font.fontFamily;
        dialog.style.fontWeight = font.fontWeight;
      }
      dialog.setAttribute('aria-labelledby', 'wut-plaza-confirm-message');
      dialog.innerHTML = '<p id="wut-plaza-confirm-message"></p><div class="wut-confirm-buttons">'
        + '<button type="button" class="common-btn wut-confirm-cancel" autofocus>取消</button>'
        + '<button type="button" class="common-btn wut-confirm-ok">确认</button></div>';
      dialog.querySelector('p').textContent = op === 'publish'
        ? '发布到广场的房间将可以被任何人访问，是否要发布？' : '是否要将房间从广场撤下？';
      var done = false;
      function finish(confirmed) {
        if (done) return; done = true;
        dialog.remove(); confirmation = null; resolve(confirmed);
      }
      confirmation = { cancel: function () { finish(false); } };
      dialog.querySelector('.wut-confirm-cancel').addEventListener('click', function () { finish(false); });
      dialog.querySelector('.wut-confirm-ok').addEventListener('click', function () { finish(true); });
      dialog.addEventListener('cancel', function (event) { event.preventDefault(); finish(false); });
      document.body.appendChild(dialog);
      try { dialog.showModal(); }
      catch (error) { plazaNote = '无法打开确认窗口，请重新打开联机页后重试。'; finish(false); }
    });
  }

  function mountPlazaControls(container) {
    var control = document.getElementById(PLAZA_CONTROLS);
    var leave = document.querySelector('.room-module .leave-room-btn');
    var canPublish = inRoom() && leave && plaza.owner && R.current && plaza.room === R.current.room_id;
    var canWithdraw = plaza.canWithdraw === true;
    var allowed = canPublish || canWithdraw;
    var parent = leave ? leave.parentNode : container;
    if (!allowed) { if (control) control.remove(); if (confirmation) confirmation.cancel(); return; }
    if (!control) {
      control = document.createElement('div'); control.id = PLAZA_CONTROLS;
      control.innerHTML = '<button type="button" class="common-btn"></button>'
        + '<span class="wut-plaza-action-status" role="status"></span>';
      control.querySelector('button').addEventListener('click', async function () {
        if (confirming || plaza.busy || Date.now() - R.beat > 5000) return;
        var room = R.current && R.current.room_id;
        var withdrawal = plaza.withdrawRoom;
        var op = plaza.canWithdraw ? 'withdraw' : 'publish';
        confirming = true; mountPlazaControls(container);
        var confirmed = await confirmPlaza(op);
        confirming = false;
        if (confirmed && ((op === 'withdraw' && plaza.canWithdraw && plaza.withdrawRoom === withdrawal)
            || (op === 'publish' && inRoom() && plaza.owner && !plaza.canWithdraw && R.current && R.current.room_id === room && plaza.room === room))) plazaRequest(op, 0);
        mountPlazaControls(container);
      });
      if (leave) parent.insertBefore(control, leave);
      else if (typeof parent.insertBefore === 'function') parent.insertBefore(control, parent.firstChild);
      else parent.appendChild(control);
    } else if (control.parentNode !== parent) {
      if (leave) parent.insertBefore(control, leave);
      else if (typeof parent.insertBefore === 'function') parent.insertBefore(control, parent.firstChild);
      else parent.appendChild(control);
    }
    control.style.right = leave ? ((leave.offsetWidth || 135) + 10) + 'px' : '';
    var pending = plaza.busy || R.requests.some(function (r) { return r.action === 'plaza' && r.op !== 'list'; });
    var button = control.querySelector('button');
    var buttonText = pending ? '处理中…' : plaza.withdrawing ? '撤下待确认' : canWithdraw ? '从广场撤下' : '发布到广场';
    if (button.textContent !== buttonText) button.textContent = buttonText;
    button.disabled = confirming || pending || !!plaza.withdrawing || (!canWithdraw && (!leave || leave.disabled)) || Date.now() - R.beat > 5000;
    var status = control.querySelector('.wut-plaza-action-status');
    var text = plaza.error || '';
    if (status.textContent !== text) status.textContent = text;
  }

  function plazaItems() {
    return plaza.logged ? (plaza.items || []).filter(function (item) {
      return item && /^[0-9A-Za-z]{4,16}$/.test(item.room_code || '') && Date.parse(item.expires_at) > Date.now() + (plaza.serverOffset || 0);
    }) : [];
  }

  function enterPlaza(roomId) {
    if (!plaza.logged || Date.now() - R.beat > 5000 || inRoom()) return 'unavailable';
    if (!plazaItems().some(function (item) { return item.room_code === roomId; })) return 'expired';
    plazaNote = '';
    return join(roomId);
  }

  function randomJoin() {
    if (!plaza.logged || Date.now() - R.beat > 5000 || inRoom()) return 'unavailable';
    if (R.pending) return 'waiting';
    var items = plazaItems();
    if (!items.length) {
      plazaNote = '当前列表没有可随机进入的房间，请稍后刷新。';
      renderPlaza();
      return 'empty';
    }
    return enterPlaza(items[Math.floor(Math.random() * items.length)].room_code);
  }

  function renderPlaza() {
    var host = document.getElementById(HOST);
    if (!host) return;
    var view = host.querySelector('.wut-plaza-view');
    if (!view) return; // Older test fixtures only model the original history list.
    var show = activeTab === 'plaza';
    view.hidden = !show;
    host.querySelector('.wut-rooms-list').hidden = show;
    host.querySelector('.wut-rooms-head').hidden = show;
    host.querySelectorAll('[data-tab]').forEach(function (tab) {
      var selected = tab.dataset.tab === activeTab;
      tab.setAttribute('aria-selected', selected ? 'true' : 'false'); tab.tabIndex = selected ? 0 : -1;
    });
    var loading = plaza.loading || R.requests.some(function (r) { return r.action === 'plaza' && r.op === 'list'; });
    var note = view.querySelector('.wut-plaza-note');
    var noteText = R.pending ? '正在进入 ' + R.pending + '…' : R.error || plaza.error || (plaza.logged == null ? '正在同步登录状态…' : !plaza.logged ? '登录 Wuwa Tappo 后可浏览广场。' : plazaNote ||
      (loading ? '正在读取…' : ''));
    if (note.textContent !== noteText) note.textContent = noteText;
    var cooldown = Math.max(0, Math.ceil(((plaza.nextRefreshAt || 0) - Date.now()) / 1000));
    var page = plaza.page || 0;
    view.querySelector('.wut-plaza-page').textContent = '第 ' + (page + 1) + ' 页';
    view.querySelector('[data-plaza="previous"]').disabled = loading || page === 0 || !plaza.logged;
    view.querySelector('[data-plaza="next"]').disabled = loading || !plaza.hasMore || !plaza.logged;
    var refresh = view.querySelector('[data-plaza="refresh"]');
    refresh.disabled = loading || cooldown > 0 || !plaza.logged;
    var refreshText = cooldown > 0 ? '刷新（' + cooldown + '秒）' : '刷新';
    if (refresh.textContent !== refreshText) refresh.textContent = refreshText;
    var reset = view.querySelector('[data-plaza="reset"]');
    if (reset) { reset.hidden = !plaza.authExpired; reset.disabled = !!plaza.busy; }
    var random = view.querySelector('[data-plaza="random"]');
    random.disabled = !plaza.logged || !!R.pending || !plazaItems().length;
    var items = plazaItems();
    var key = JSON.stringify([plaza.logged, items, R.pending, Math.floor(Date.now() / 60000)]);
    var list = view.querySelector('.wut-plaza-list');
    if (key === plazaPainted && list.children.length) return;
    plazaPainted = key;
    if (!items.length) {
      var emptyText = plaza.logged == null ? '正在同步登录状态…' : plaza.logged ? '暂时没有公开的房间。' : '请先在桌宠中登录。';
      if (list.children.length === 1 && list.children[0].className === 'wut-rooms-empty') {
        if (list.children[0].textContent !== emptyText) list.children[0].textContent = emptyText;
        return;
      }
      list.textContent = '';
      var empty = document.createElement('li'); empty.className = 'wut-rooms-empty';
      empty.textContent = emptyText; list.appendChild(empty);
      return;
    }
    var oldRows = new Map();
    Array.from(list.children).forEach(function (child) {
      if (child.dataset.room) oldRows.set(child.dataset.room, child);
      else child.remove();
    });
    items.forEach(function (item, index) {
      var row = oldRows.get(item.room_code) || document.createElement('li');
      oldRows.delete(item.room_code);
      row.className = 'wut-room wut-plaza-row'; row.dataset.room = item.room_code; row.dataset.source = 'plaza';
      row.dataset.busy = R.pending === item.room_code ? 'true' : 'false';
      row.tabIndex = 0; row.setAttribute('role', 'button'); row.setAttribute('aria-label', '进入房间 ' + item.room_code);
      var code = row.querySelector('.wut-room-id');
      if (!code) { code = document.createElement('span'); code.className = 'wut-room-id'; row.appendChild(code); }
      if (code.textContent !== item.room_code) code.textContent = item.room_code;
      var detail = row.querySelector('.wut-room-detail');
      if (detail) detail.remove();
      var enter = row.querySelector('.wut-plaza-enter');
      if (!enter) { enter = document.createElement('button'); enter.type = 'button'; enter.className = 'wut-plaza-enter wut-rooms-tab'; enter.style.marginLeft = 'auto'; enter.textContent = '进入'; row.appendChild(enter); }
      enter.disabled = !!R.pending;
      if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
    });
    oldRows.forEach(function (row) { row.remove(); });
  }

  function note() {
    if (R.pending) return '正在进入 ' + R.pending + '…';
    if (R.error) return R.error;
    if (Date.now() - R.beat > 5000) return 'Plus 后台未连接，记录不会保存';
    return R.rooms.length ? '点一下回到该房间，点 ☆ 收藏并置顶' : '';
  }

  function render() {
    renderPlaza();
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
    if (state && Object.prototype.hasOwnProperty.call(state, 'rooms')) R.rooms = state.rooms || [];
    if (state && state.plaza && !(state.plaza.instance && state.plaza.instance === plaza.instance && state.plaza.revision < plaza.revision)) {
      var wasLogged = plaza.logged;
      plaza = state.plaza;
      if (plaza.logged && !wasLogged && activeTab === 'plaza') plazaRequest('list', 0);
    }
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
  R.selectTab = function (tab) { activeTab = tab === "plaza" ? "plaza" : "recent"; if (activeTab === "plaza") plazaRequest("list", 0); renderPlaza(); };
  R.plazaRequest = plazaRequest;
  R.join = join;
  R.enterPlaza = enterPlaza;
  R.randomJoin = randomJoin;
  R.togglePin = togglePin;
  R.parseRoomNumber = parseRoomNumber;
  R.tick = tick;

  var observer = new MutationObserver(function () { mount(); });
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  timeout = setInterval(tick, 1000);

  R.uninstall = function () {
    observer.disconnect();
    var container = document.querySelector('.room-module .panel-content');
    if (container && container.classList) container.classList.remove('wut-room-browser');
    if (confirmation) confirmation.cancel();
    clearInterval(timeout);
    [HOST, STYLE, VISIBILITY, PLAZA_CONTROLS].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.remove();
    });
    delete window.__WUT_ROOMS__;
  };

  tick();
  return 'installed v' + R.version;
})()
