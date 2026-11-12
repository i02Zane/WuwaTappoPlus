  function createRoomPlaza(nativeState, HOST, inRoom, join, nextId, onError) {
    var PLAZA_CONTROLS = 'wut-plaza-controls';
    var activeTab = 'recent';
    var plaza = { items: [], logged: null, owner: false, published: false };
    var plazaPainted = '';
    var plazaNote = '';
    var confirming = false;
    var confirmation = null;
    function plazaRequest(op, page, refresh) {
      refresh = refresh !== false;
      if (Date.now() - nativeState.beat > 5000 || (!plaza.logged && !(op === 'withdraw' && plaza.canWithdraw)) || nativeState.requests.some(function (r) { return r.action === 'plaza'; })) return;
      if (op === 'list' && refresh && Date.now() < (plaza.nextRefreshAt || 0)) { renderPlaza(); return; }
      if (plaza.busy || (op === 'list' && plaza.loading)) return;
      var command = { id: nextId(), op: op,
                      page: Math.max(0, Math.min(49, page || 0)), refresh: refresh };
      try {
        if (typeof window.__wutPlazaAction !== 'function') throw new Error('binding unavailable');
        window.__wutPlazaAction(JSON.stringify(command));
        if (op === 'list') plaza.loading = true; else plaza.busy = true;
      } catch (e) { onError('Plus 广场连接尚未就绪，请稍后重试。'); }
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
      var canPublish = inRoom() && leave && plaza.owner && nativeState.current && plaza.room === nativeState.current.room_id;
      var canWithdraw = plaza.canWithdraw === true;
      var allowed = canPublish || canWithdraw;
      var parent = leave ? leave.parentNode : container;
      if (!allowed) { if (control) control.remove(); if (confirmation) confirmation.cancel(); return; }
      if (!control) {
        control = document.createElement('div'); control.id = PLAZA_CONTROLS;
        control.innerHTML = '<button type="button" class="common-btn"></button>'
          + '<span class="wut-plaza-action-status" role="status"></span>';
        control.querySelector('button').addEventListener('click', async function () {
          if (confirming || plaza.busy || Date.now() - nativeState.beat > 5000) return;
          var room = nativeState.current && nativeState.current.room_id;
          var withdrawal = plaza.withdrawRoom;
          var op = plaza.canWithdraw ? 'withdraw' : 'publish';
          confirming = true; mountPlazaControls(container);
          var confirmed = await confirmPlaza(op);
          confirming = false;
          if (confirmed && ((op === 'withdraw' && plaza.canWithdraw && plaza.withdrawRoom === withdrawal)
              || (op === 'publish' && inRoom() && plaza.owner && !plaza.canWithdraw && nativeState.current && nativeState.current.room_id === room && plaza.room === room))) plazaRequest(op, 0);
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
      var pending = plaza.busy || nativeState.requests.some(function (r) { return r.action === 'plaza' && r.op !== 'list'; });
      var button = control.querySelector('button');
      var buttonText = pending ? '处理中…' : plaza.withdrawing ? '撤下待确认' : canWithdraw ? '从广场撤下' : '发布到广场';
      if (button.textContent !== buttonText) button.textContent = buttonText;
      button.disabled = confirming || pending || !!plaza.withdrawing || (!canWithdraw && (!leave || leave.disabled)) || Date.now() - nativeState.beat > 5000;
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
      if (!plaza.logged || Date.now() - nativeState.beat > 5000 || inRoom()) return 'unavailable';
      if (!plazaItems().some(function (item) { return item.room_code === roomId; })) return 'expired';
      plazaNote = '';
      return join(roomId);
    }

    function randomJoin() {
      if (!plaza.logged || Date.now() - nativeState.beat > 5000 || inRoom()) return 'unavailable';
      if (nativeState.pending) return 'waiting';
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
      var loading = plaza.loading || nativeState.requests.some(function (r) { return r.action === 'plaza' && r.op === 'list'; });
      var note = view.querySelector('.wut-plaza-note');
      var noteText = nativeState.pending ? '正在进入 ' + nativeState.pending + '…' : nativeState.error || plaza.error || (plaza.logged == null ? '正在同步登录状态…' : !plaza.logged ? '登录 Wuwa Tappo 后可浏览广场。' : plazaNote ||
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
      random.disabled = !plaza.logged || !!nativeState.pending || !plazaItems().length;
      var items = plazaItems();
      var key = JSON.stringify([plaza.logged, items, nativeState.pending, Math.floor(Date.now() / 60000)]);
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
        row.dataset.busy = nativeState.pending === item.room_code ? 'true' : 'false';
        row.tabIndex = 0; row.setAttribute('role', 'button'); row.setAttribute('aria-label', '进入房间 ' + item.room_code);
        var code = row.querySelector('.wut-room-id');
        if (!code) { code = document.createElement('span'); code.className = 'wut-room-id'; row.appendChild(code); }
        if (code.textContent !== item.room_code) code.textContent = item.room_code;
        var detail = row.querySelector('.wut-room-detail');
        if (detail) detail.remove();
        var enter = row.querySelector('.wut-plaza-enter');
        if (!enter) { enter = document.createElement('button'); enter.type = 'button'; enter.className = 'wut-plaza-enter wut-rooms-tab'; enter.style.marginLeft = 'auto'; enter.textContent = '进入'; row.appendChild(enter); }
        enter.disabled = !!nativeState.pending;
        if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
      });
      oldRows.forEach(function (row) { row.remove(); });
    }

    function selectTab(tab) {
      activeTab = tab === 'plaza' ? 'plaza' : 'recent';
      if (activeTab === 'plaza') plazaRequest('list', 0);
      renderPlaza();
    }
    return { request: plazaRequest, enter: enterPlaza, randomJoin: randomJoin,
      selectTab: selectTab, render: renderPlaza, mount: mountPlazaControls,
      get page() { return plaza.page || 0; },
      update: function (state) {
        if (state && state.plaza && !(state.plaza.instance && state.plaza.instance === plaza.instance && state.plaza.revision < plaza.revision)) {
          var wasLogged = plaza.logged;
          plaza = state.plaza;
          if (plaza.logged && !wasLogged && activeTab === 'plaza') plazaRequest('list', 0);
        }
      },
      dispose: function () {
        if (confirmation) confirmation.cancel();
        var control = document.getElementById(PLAZA_CONTROLS);
        if (control) control.remove();
      }
    };
  }
