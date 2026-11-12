(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_SETTINGS__ && window.__WUT_SETTINGS__.version === VERSION) return 'exists';
  if (window.__WUT_SETTINGS__) window.__WUT_SETTINGS__.uninstall();

  /* @modules */

  var S = window.__WUT_SETTINGS__ = {
    version: VERSION, requests: [], beat: 0, report: null,
    exportPath: null, exportError: false, accessories: {},
    _test: {},
    preferences: { auto_open: true, balance_reminder: true, bubble: true, animation: true,
                   remember_screen: true, auto_emote: false, pause_clicks: false,
                   game_mode_plus: true, game_mode_opacity: 30, auto_emote_rate: 20,
                   game_mode_apps: [] }
  };
  var serial = 0;
  var instance = Date.now().toString(36) + Math.random().toString(36).slice(2);
  var feedback = { error: '', saved: false };
  function nextId() { return instance + '-' + (++serial); }
  var token = createTokenTools();
  S._test.emitLoginSuccess = token.emitLoginSuccess;
  function queueRequest(request) {
    request.id = nextId();
    S.requests.push(request);
    if (request.key || request.action === 'whitelist_add' || request.action === 'save_accessory') {
      feedback.error = ''; feedback.saved = false;
    }
  }
  var apps = createAppsDialog(function () {
    return Array.isArray(S.preferences.game_mode_apps) ? S.preferences.game_mode_apps : [];
  }, function () { return Date.now() - S.beat <= 5000; }, queueRequest, render);
  Object.defineProperties(S, {
    appsOpen: { enumerable: true, get: function () { return apps.isOpen; } },
    appList: { enumerable: true, get: function () { return apps.list; } },
    appsLoading: { enumerable: true, get: function () { return apps.loading; } }
  });
  var reportKey = '';
  function renderReport() {
    var host = document.getElementById('wut-daily-report');
    if (!host) return;
    var key = JSON.stringify(S.report);
    if (key === reportKey) return;
    reportKey = key;
    var report = S.report;
    host.querySelector('.wut-report-title').textContent =
      '昨日点击数' + (report ? ' · ' + report.yesterday_date : '');
    host.querySelector('.wut-report-count').textContent =
      report && report.available && report.yesterday_count !== null ? String(report.yesterday_count) : '—';
    host.querySelector('.wut-report-note').textContent = !report ? '正在读取…' :
      !report.available ? '统计暂不可用，自动开箱不受影响。' :
      '按桌宠「累计点击数」的每日增量统计，键盘与鼠标合计；仅包含启用统计后的记录。— 表示暂无数据。';
    var chart = host.querySelector('.wut-report-chart');
    chart.textContent = '';
    var days = report && report.available ? report.days.slice().reverse() : [];
    var max = Math.max.apply(null, [1].concat(days.map(function (day) { return day.count || 0; })));
    days.forEach(function (day, index) {
      var missing = day.count === null;
      var today = index === days.length - 1;
      var column = document.createElement('div');
      column.className = 'wut-chart-column' + (today ? ' wut-chart-today' : '');
      column.setAttribute('role', 'listitem');
      column.title = day.date + '：' + (missing ? '暂无数据' : day.count + ' 次');
      column.setAttribute('aria-label', column.title);
      var value = document.createElement('span');
      value.className = 'wut-chart-value';
      value.textContent = missing ? '—' : day.count >= 10000 ?
        (day.count / 10000).toFixed(1) + '万' : String(day.count);
      var track = document.createElement('div');
      track.className = 'wut-chart-track' + (missing ? ' wut-chart-missing' :
        day.count === 0 ? ' wut-chart-zero' : '');
      track.setAttribute('aria-hidden', 'true');
      var bar = document.createElement('div');
      bar.className = 'wut-chart-bar';
      bar.style.height = missing ? '0' : day.count === 0 ? '2px' : Math.max(2, day.count / max * 100) + '%';
      track.appendChild(bar);
      var date = document.createElement('span');
      date.className = 'wut-chart-date';
      date.textContent = today ? '今天' : day.date.slice(5).replace('-', '/');
      column.appendChild(value); column.appendChild(track); column.appendChild(date);
      chart.appendChild(column);
    });
  }


  var HOST = 'wut-plus-settings';
  var STYLE = 'wut-plus-settings-style';
  var updateTokenExpiry = token.updateExpiry;
  var syncTokenLoginVisibility = token.syncLoginVisibility;
  var renewToken = token.renew;
  var extractToken = token.extract;
  var showAppToast = token.showToast;
  var loginWithToken = token.login;
  var openAppsDialog = apps.open;
  var renderAppsRow = apps.renderRow;
  var renderAppsDialog = apps.render;
  var definitions = [
    ['auto_open', '自动开箱', '宝箱到期且余额充足时自动开启，关闭后仍显示倒计时。'],
    ['auto_emote', '自动发表情', '桌宠会跟随敲击自动发送一个已装备的表情；频率可在下方调整。'],
    ['game_mode_plus', '自动游戏模式·Plus', '开启后由 Plus 接管自动进入游戏模式：全屏游戏、窗口化/无边框全屏，以及下面白名单里的程序都会生效；官方全屏开关会暂时关闭，关闭本项后恢复官方设置；手动退出后，本次期间不再自动进入。'],
    ['balance_reminder', '余额提醒', '余额不足时提醒，并显示宝箱倒计时；不影响其他气泡。'],
    ['bubble', '气泡显示', '显示 Plus 气泡，包括余额提醒和小黑屋模式文字。'],
    ['animation', '气泡动画', '让气泡轻轻跳动；关闭后保留静态气泡。'],
    ['remember_screen', '屏幕记忆', '记住桌宠所在的显示器，重启后自动回到该屏幕；显示器不可用时保持原样。'],
    ['hello_boss', '王总好模式', '让房间里的其他桌宠都面朝你，仅在本机生效'],
    ['pause_clicks', '小黑屋模式', '暂停累积和上报新的点击，但仍保留桌宠敲击动画']
  ];

  function emitGameOpacityPreview(value, done) {
    try {
      var internals = window.__TAURI_INTERNALS__;
      if (!internals || !internals.invoke) return;
      internals.invoke('plugin:event|emit_to', {
        event: 'wut-game-opacity-preview',
        target: { kind: 'AnyLabel', label: 'main' },
        payload: { value: Number(value), done: !!done }
      }).catch(function () {});
    } catch (e) {}
  }

  function mount() {
    var container = document.querySelector('.settings-module .panel-content');
    if (!container || document.getElementById(HOST)) return;
    if (!document.getElementById(STYLE)) {
      var style = document.createElement('style');
      style.id = STYLE;
      style.textContent = '#wut-plus-settings{border-top:1px solid rgba(142,94,67,.2);'
        + 'border-bottom:1px solid rgba(142,94,67,.2);margin:16px 0;padding:14px 0 4px;}'
        // Both colours are taken from the panel itself: the section title uses
        // the same #8e5e43 as the "最近房间" heading, the status line the same
        // #8e7b6a as a .setting-value such as the account nickname.
        + '#wut-plus-settings .wut-heading{display:flex;flex-wrap:wrap;align-items:center;'
        + 'justify-content:space-between;gap:8px;margin-bottom:8px;color:#8e5e43;}'
        + '#wut-plus-settings .wut-heading strong{font-size:18px;}'
        + '#wut-plus-settings .wut-status{font-size:13px;color:#8e7b6a;}'
        + '#wut-plus-settings .wut-status[data-error="true"]{color:#b4543c;}'
        + '#wut-plus-settings input:disabled+.setting-slider{opacity:.45;cursor:wait;}'
        + '#wut-plus-settings .setting-sub{max-width:420px;}'
        + '#wut-plus-settings .setting-value-rate{flex:0 0 84px;width:84px;'
        + 'text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;}'
        + '#wut-plus-settings .setting-value-opacity{flex:0 0 64px;width:64px;'
        + 'text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;}'
        + '#wut-plus-settings .wut-token-actions{display:flex;flex-wrap:wrap;gap:8px;}'
        + '#wut-plus-settings .wut-token-expiry{font-size:13px;color:#8e7b6a;margin-top:2px;}'
        + '#wut-plus-settings .wut-token-expiry[data-soon="true"]{color:#b4543c;font-weight:600;}'
        + '#wut-plus-settings .wut-token-login-input{flex:1 1 220px;min-width:0;padding:6px 8px;'
        + 'border:1px solid #e0cfc0;border-radius:6px;background:#fff;font-size:13px;color:#8e5e43;}'
        + '#wut-plus-settings .wut-export{gap:18px;}'
        + '#wut-plus-settings .wut-export .setting-label{min-width:0;}'
        + '#wut-plus-settings .wut-export .setting-content{flex-shrink:0;}'
        + '#wut-plus-settings .wut-export button{white-space:nowrap;}'
        + '#wut-plus-settings .wut-export button:disabled{cursor:wait;opacity:.5;}'
        + '#wut-plus-settings .wut-game-row .setting-content{flex-shrink:0;}'
        + '#wut-apps-modal{position:fixed;inset:0;z-index:2147483600;display:flex;align-items:center;'
        + 'justify-content:center;padding:24px;background:rgba(60,35,20,.28);}'
        + '#wut-apps-modal .wut-apps-panel{display:flex;flex-direction:column;width:min(420px,100%);'
        + 'max-height:min(560px,86vh);box-sizing:border-box;padding:16px 18px 18px;'
        + 'border:2px solid rgb(235,206,155);border-radius:18px;background:rgb(251,237,213);'
        + 'color:rgb(142,94,67);font-size:16px;box-shadow:0 18px 48px rgba(60,35,20,.3);}'
        + '#wut-apps-modal .wut-apps-head{display:flex;align-items:center;justify-content:space-between;'
        + 'gap:12px;}'
        + '#wut-apps-modal .wut-apps-head strong{font-size:18px;font-weight:400;color:rgb(142,94,67);}'
        + '#wut-apps-modal .wut-apps-close{flex:none;width:22px;height:22px;padding:0;border:0;'
        + 'border-radius:50%;background:transparent;color:rgb(142,123,106);font-size:20px;line-height:1;'
        + 'cursor:pointer;}'
        + '#wut-apps-modal .wut-apps-close:hover{background:rgb(235,206,155);color:rgb(254,246,221);}'
        + '#wut-apps-modal .wut-apps-title{margin:12px 0 6px;font-size:14px;color:rgb(142,123,106);}'
        + '#wut-apps-modal .wut-game-apps{display:flex;flex-wrap:wrap;gap:6px;}'
        + '#wut-apps-modal .wut-game-app{display:inline-flex;align-items:center;gap:6px;max-width:100%;'
        + 'height:28px;box-sizing:border-box;padding:0 6px 0 12px;border-radius:14px;'
        + 'border:1px solid rgb(231,183,124);background:rgb(254,210,145);color:rgb(142,94,67);'
        + 'font-size:16px;filter:drop-shadow(rgb(231,183,124) 0 2px 0);}'
        + '#wut-apps-modal .wut-game-app>span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}'
        + '#wut-apps-modal .wut-game-app .setting-shortcut-clear{position:static;flex:none;width:16px;'
        + 'height:16px;padding:0;border:0;border-radius:50%;background:rgb(235,206,155);'
        + 'color:rgb(254,246,221);font-size:12px;line-height:1;cursor:pointer;}'
        + '#wut-apps-modal .wut-game-apps-empty{font-size:14px;color:rgb(142,123,106);}'
        + '#wut-apps-modal .wut-apps-open{display:flex;flex-direction:column;gap:6px;overflow-y:auto;'
        + 'flex:1 1 auto;min-height:96px;padding:2px;scrollbar-width:thin;'
        + 'scrollbar-color:rgb(231,183,124) transparent;}'
        + '#wut-apps-modal .wut-apps-open::-webkit-scrollbar{width:8px;}'
        + '#wut-apps-modal .wut-apps-open::-webkit-scrollbar-thumb{background:rgb(231,183,124);'
        + 'border-radius:4px;}'
        + '#wut-apps-modal .wut-apps-item{display:flex;align-items:center;gap:10px;width:100%;'
        + 'box-sizing:border-box;min-height:40px;padding:4px 12px;border:1px solid rgb(235,206,155);'
        + 'border-radius:10px;background:rgb(254,246,221);color:rgb(142,94,67);font-size:18px;'
        + 'font-family:inherit;text-align:left;cursor:pointer;}'
        + '#wut-apps-modal .wut-apps-item:hover{border-color:rgb(153,94,63);}'
        + '#wut-apps-modal .wut-apps-item.is-added{opacity:.55;cursor:default;}'
        + '#wut-apps-modal .wut-apps-item-name{flex:0 1 auto;max-width:66%;overflow:hidden;'
        + 'text-overflow:ellipsis;white-space:nowrap;}'
        + '#wut-apps-modal .wut-apps-item-title{flex:1 1 0;min-width:0;overflow:hidden;'
        + 'text-overflow:ellipsis;white-space:nowrap;font-size:14px;color:rgb(142,123,106);}'
        + '#wut-apps-modal .wut-apps-item-state{flex:none;font-size:14px;color:rgb(142,123,106);}'
        + '#wut-apps-modal .wut-apps-foot{display:flex;align-items:center;gap:8px;margin-top:12px;}'
        + '#wut-apps-modal .wut-apps-foot .setting-input{flex:1 1 auto;min-width:0;max-width:none;'
        + 'box-sizing:border-box;height:30px;padding:0 14px;border:2px solid rgb(235,206,155);'
        + 'border-radius:18px;background:rgb(254,246,221);color:rgb(142,94,67);font-size:16px;'
        + 'font-family:inherit;}'
        + '#wut-apps-modal .wut-apps-foot .setting-input::placeholder{color:rgb(235,206,155);}'
        + '#wut-apps-modal .wut-apps-foot .setting-btn{flex:none;min-width:64px;height:30px;'
        + 'padding:0 16px;border:1px solid rgb(231,183,124);border-radius:18px;'
        + 'background:rgb(254,210,145);color:rgb(142,94,67);font-size:16px;line-height:30px;'
        + 'font-family:inherit;text-align:center;cursor:pointer;'
        + 'filter:drop-shadow(rgb(231,183,124) 0 2px 0);}'
        + '#wut-apps-modal .wut-apps-foot .setting-btn:disabled{opacity:.5;cursor:wait;}'
        + '#wut-apps-modal .wut-apps-note{padding:8px 2px;font-size:14px;color:rgb(142,123,106);}'
        + '#wut-export-status{overflow-wrap:anywhere;}'
        + '#wut-daily-report{padding:12px 18px;margin:12px 0;border-radius:8px;'
        + 'background:rgba(213,169,108,.09);color:#8e5e43;}'
        + '#wut-daily-report .wut-report-title{font-size:14px;}'
        + '#wut-daily-report .wut-report-count{font-size:28px;font-weight:bold;margin-right:5px;}'
        + '#wut-daily-report .wut-report-note{font-size:12px;opacity:.75;margin:6px 0;}'
        + '#wut-daily-report summary{cursor:pointer;font-size:13px;}'
        + '#wut-daily-report .wut-report-chart{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));'
        + 'gap:8px;margin-top:12px;font-variant-numeric:tabular-nums;}'
        + '#wut-daily-report .wut-chart-column{display:flex;flex-direction:column;align-items:center;gap:5px;min-width:0;}'
        + '#wut-daily-report .wut-chart-value{font-size:12px;line-height:18px;white-space:nowrap;}'
        + '#wut-daily-report .wut-chart-track{height:104px;width:100%;max-width:38px;display:flex;'
        + 'align-items:flex-end;border-bottom:1px solid rgba(142,94,67,.25);border-radius:5px 5px 0 0;}'
        + '#wut-daily-report .wut-chart-bar{width:100%;border-radius:5px 5px 0 0;'
        + 'background:linear-gradient(#f4cc8c,#b78056);}'
        + '#wut-daily-report .wut-chart-today .wut-chart-bar{background:linear-gradient(#d3a56e,#8e5e43);}'
        + '#wut-daily-report .wut-chart-missing{background:repeating-linear-gradient(135deg,'
        + 'transparent,transparent 5px,rgba(142,94,67,.05) 5px,rgba(142,94,67,.05) 7px);}'
        + '#wut-daily-report .wut-chart-zero .wut-chart-bar{background:rgba(142,94,67,.3);}'
        + '#wut-daily-report .wut-chart-date{font-size:12px;opacity:.8;white-space:nowrap;}';
      (document.head || document.documentElement).appendChild(style);
    }
    var host = document.createElement('section');
    host.id = HOST;
    host.setAttribute('aria-label', 'Wuwa Tappo Plus');
    var heading = document.createElement('div');
    heading.className = 'wut-heading';
    var title = document.createElement('strong');
    title.textContent = 'Wuwa Tappo Plus v' + VERSION;
    var status = document.createElement('span');
    status.className = 'wut-status';
    status.setAttribute('role', 'status');
    heading.appendChild(title);
    heading.appendChild(status);
    host.appendChild(heading);

    definitions.forEach(function (definition) {
      var row = document.createElement('div');
      row.className = 'setting-item';
      var label = document.createElement('div');
      label.className = 'setting-label';
      var name = document.createElement('div');
      name.className = 'setting-title';
      name.id = 'wut-setting-' + definition[0];
      name.textContent = definition[1];
      var description = document.createElement('div');
      description.className = 'setting-sub';
      description.textContent = definition[2];
      label.appendChild(name);
      label.appendChild(description);
      var content = document.createElement('div');
      content.className = 'setting-content';
      var toggle = document.createElement('label');
      toggle.className = 'setting-switch';
      var input = document.createElement('input');
      input.type = 'checkbox';
      input.dataset.preference = definition[0];
      input.setAttribute('aria-labelledby', name.id);
      input.addEventListener('change', function () {
        if (Date.now() - S.beat > 5000) { render(); return; }
        queueRequest({ key: definition[0], value: input.checked });
        render();
      });
      var slider = document.createElement('span');
      slider.className = 'setting-slider';
      toggle.appendChild(input);
      toggle.appendChild(slider);
      content.appendChild(toggle);
      row.appendChild(label);
      row.appendChild(content);
      host.appendChild(row);
      if (definition[0] === 'auto_emote') appendEmoteRateRow();
      if (definition[0] === 'game_mode_plus') {
        appendGameModeOpacityRow();
        appendGameModeAppsRow();
      }
    });
    function appendEmoteRateRow() {
      var emoteRow = document.createElement('div');
      emoteRow.className = 'setting-item';
      var emoteLabel = document.createElement('div');
      emoteLabel.className = 'setting-label';
      var emoteName = document.createElement('div');
      emoteName.className = 'setting-title';
      emoteName.id = 'wut-setting-auto-emote-rate';
      emoteName.textContent = '自动发表情频率';
      var emoteDesc = document.createElement('div');
      emoteDesc.className = 'setting-sub';
      emoteDesc.textContent = '自动发表情功能每秒发送表情个数的上限，范围1-20。';
      emoteLabel.appendChild(emoteName);
      emoteLabel.appendChild(emoteDesc);
      var emoteContent = document.createElement('div');
      emoteContent.className = 'setting-content';
      var emoteRange = document.createElement('input');
      emoteRange.type = 'range';
      emoteRange.min = '1';
      emoteRange.max = '20';
      emoteRange.step = '1';
      emoteRange.className = 'setting-range';
      emoteRange.dataset.preference = 'auto_emote_rate';
      emoteRange.setAttribute('aria-labelledby', emoteName.id);
      var initialRate = Number(S.preferences.auto_emote_rate);
      if (!isFinite(initialRate)) initialRate = 20;
      initialRate = Math.max(1, Math.min(20, Math.round(initialRate)));
      emoteRange.value = String(initialRate);
      emoteRange.style.setProperty('--range-progress', ((initialRate - 1) / 19 * 100) + '%');
      var emoteValue = document.createElement('span');
      emoteValue.className = 'setting-value setting-value-rate';
      emoteValue.textContent = initialRate + ' 次/秒';
      function showEmoteRate() {
        var value = Number(emoteRange.value);
        emoteRange.style.setProperty('--range-progress', ((value - 1) / 19 * 100) + '%');
        emoteValue.textContent = value + ' 次/秒';
      }
      emoteRange.addEventListener('pointerdown', function () {
        emoteRange.dataset.initialized = '1';
        emoteRange.dataset.editing = 'true';
      });
      emoteRange.addEventListener('input', function () {
        emoteRange.dataset.initialized = '1';
        emoteRange.dataset.editing = 'true';
        showEmoteRate();
      });
      emoteRange.addEventListener('change', function () {
        emoteRange.dataset.editing = 'false';
        if (Date.now() - S.beat > 5000) { render(); return; }
        queueRequest({ key: 'auto_emote_rate',
                          value: Number(emoteRange.value) });
        render();
      });
      emoteRange.addEventListener('blur', function () {
        emoteRange.dataset.editing = 'false';
        render();
      });
      emoteContent.appendChild(emoteRange);
      emoteContent.appendChild(emoteValue);
      emoteRow.appendChild(emoteLabel);
      emoteRow.appendChild(emoteContent);
      host.appendChild(emoteRow);
    }
    function appendGameModeOpacityRow() {
      var gameRow = document.createElement('div');
      gameRow.className = 'setting-item';
      var gameLabel = document.createElement('div');
      gameLabel.className = 'setting-label';
      var gameName = document.createElement('div');
      gameName.className = 'setting-title';
      gameName.id = 'wut-setting-game-mode-opacity';
      gameName.textContent = '游戏模式透明度';
      var gameDesc = document.createElement('div');
      gameDesc.className = 'setting-sub';
      gameDesc.textContent = '进入游戏模式时的桌宠透明度；退出游戏模式后恢复官方选项的透明度';
      gameLabel.appendChild(gameName);
      gameLabel.appendChild(gameDesc);
      var gameContent = document.createElement('div');
      gameContent.className = 'setting-content';
      var gameRange = document.createElement('input');
      gameRange.type = 'range';
      gameRange.min = '0';
      gameRange.max = '100';
      gameRange.step = '1';
      gameRange.className = 'setting-range';
      gameRange.dataset.preference = 'game_mode_opacity';
      gameRange.setAttribute('aria-labelledby', gameName.id);
      var initialOpacity = Number(S.preferences.game_mode_opacity);
      if (!isFinite(initialOpacity)) initialOpacity = 30;
      gameRange.value = String(initialOpacity);
      gameRange.style.setProperty('--range-progress', initialOpacity + '%');
      var gameValue = document.createElement('span');
      gameValue.className = 'setting-value setting-value-opacity';
      gameValue.textContent = initialOpacity + '%';
      gameRange.addEventListener('pointerdown', function () {
        gameRange.dataset.initialized = '1';
        gameRange.dataset.editing = 'true';
      });
      gameRange.addEventListener('input', function () {
        gameRange.dataset.initialized = '1';
        gameRange.dataset.editing = 'true';
        gameRange.style.setProperty('--range-progress', gameRange.value + '%');
        gameValue.textContent = gameRange.value + '%';
        emitGameOpacityPreview(gameRange.value, false);
      });
      gameRange.addEventListener('change', function () {
        gameRange.dataset.editing = 'false';
        emitGameOpacityPreview(gameRange.value, true);
        if (Date.now() - S.beat > 5000) { render(); return; }
        queueRequest({ key: 'game_mode_opacity',
                          value: Number(gameRange.value) });
        render();
      });
      gameRange.addEventListener('blur', function () {
        gameRange.dataset.editing = 'false';
        emitGameOpacityPreview(gameRange.value, true);
        render();
      });
      gameContent.appendChild(gameRange);
      gameContent.appendChild(gameValue);
      gameRow.appendChild(gameLabel);
      gameRow.appendChild(gameContent);
      host.appendChild(gameRow);
    }
    function appendGameModeAppsRow() {
      var row = document.createElement('div');
      row.className = 'setting-item wut-game-row';
      row.id = 'wut-setting-game-mode-apps-row';
      var label = document.createElement('div');
      label.className = 'setting-label';
      var name = document.createElement('div');
      name.className = 'setting-title';
      name.id = 'wut-setting-game-mode-apps';
      name.textContent = '自动游戏模式白名单';
      var desc = document.createElement('div');
      desc.className = 'setting-sub';
      desc.textContent = '这些程序打开时即使不是全屏也进入游戏模式；全屏窗口无需添加。'
        + '在对话框里选择当前打开的应用，或手动输入程序名。';
      label.appendChild(name);
      label.appendChild(desc);
      var content = document.createElement('div');
      content.className = 'setting-content';
      var button = document.createElement('button');
      button.id = 'wut-game-apps-manage';
      button.type = 'button';
      button.className = 'setting-btn';
      button.textContent = '选择应用…';
      button.setAttribute('aria-haspopup', 'dialog');
      button.addEventListener('click', openAppsDialog);
      content.appendChild(button);
      row.appendChild(label);
      row.appendChild(content);
      host.appendChild(row);
      renderAppsRow();
    }

    var report = document.createElement('div');
    report.id = 'wut-daily-report';    report.innerHTML = '<div class="wut-report-title">昨日点击数</div>'
      + '<div><span class="wut-report-count">—</span>次</div>'
      + '<div class="wut-report-note"></div>'
      + '<details open><summary>最近 7 天 · 点击数</summary>'
      + '<div class="wut-report-chart" role="list" aria-label="最近七天点击数"></div></details>';
    host.appendChild(report);
    appendTokenRow();
    var exports = document.createElement('div');
    exports.className = 'setting-item wut-export';
    var exportLabel = document.createElement('div');
    exportLabel.className = 'setting-label';
    var exportTitle = document.createElement('div');
    exportTitle.className = 'setting-title';
    exportTitle.textContent = '诊断日志';
    exportLabel.appendChild(exportTitle);
    var exportContent = document.createElement('div');
    exportContent.className = 'setting-content';
    var exportButton = document.createElement('button');
    exportButton.id = 'wut-export-button';
    exportButton.type = 'button';
    exportButton.className = 'setting-btn';
    exportButton.textContent = '导出 Plus 日志';
    exportButton.addEventListener('click', function () {
      if (Date.now() - S.beat > 5000 || S.requests.some(function (r) { return r.action === 'export_logs'; })) return;
      S.exportPath = null; S.exportError = false;
      queueRequest({ action: 'export_logs' });
      render();
    });
    var exportStatus = document.createElement('span');
    exportStatus.id = 'wut-export-status';
    exportStatus.className = 'setting-sub';
    exportStatus.setAttribute('role', 'status');
    exportLabel.appendChild(exportStatus);
    exportContent.appendChild(exportButton);
    exports.appendChild(exportLabel);
    exports.appendChild(exportContent);
    host.appendChild(exports);
    function appendTokenRow() {
      var row = document.createElement('div');
      row.className = 'setting-item wut-token';
      var label = document.createElement('div');
      label.className = 'setting-label';
      var title = document.createElement('div');
      title.className = 'setting-title';
      title.textContent = 'Token';
      var desc = document.createElement('div');
      desc.id = 'wut-token-expiry';
      desc.className = 'setting-sub wut-token-expiry';
      desc.textContent = '当前Token有效期：读取中…';
      label.appendChild(title);
      label.appendChild(desc);
      var content = document.createElement('div');
      content.className = 'setting-content';
      var actions = document.createElement('div');
      actions.className = 'wut-token-actions';
      var renewButton = document.createElement('button');
      renewButton.id = 'wut-token-renew';
      renewButton.type = 'button';
      renewButton.className = 'setting-btn';
      renewButton.textContent = '手动续期';
      var extractButton = document.createElement('button');
      extractButton.id = 'wut-token-extract';
      extractButton.type = 'button';
      extractButton.className = 'setting-btn';
      extractButton.textContent = '提取Token';
      renewButton.addEventListener('click', function () {
        renewButton.disabled = true;
        renewToken().then(function () {
          updateTokenExpiry();
        }).catch(function () {
          showAppToast('续期失败，请稍后重试或重新登录。');
        }).finally(function () {
          renewButton.disabled = false;
        });
      });
      extractButton.addEventListener('click', extractToken);
      actions.appendChild(renewButton);
      actions.appendChild(extractButton);
      content.appendChild(actions);
      row.appendChild(label);
      row.appendChild(content);
      host.appendChild(row);
    }
    appendTokenLoginRow();
    function appendTokenLoginRow() {
      var row = document.createElement('div');
      row.id = 'wut-token-login-row';
      row.className = 'setting-item wut-token-login';
      var label = document.createElement('div');
      label.className = 'setting-label';
      var title = document.createElement('div');
      title.className = 'setting-title';
      title.textContent = 'Token 登录';
      var desc = document.createElement('div');
      desc.className = 'setting-sub';
      desc.textContent = '用手机端或其他设备提取的 Token 登录当前电脑；Token 只在本机使用，请勿外泄。';
      label.appendChild(title);
      label.appendChild(desc);
      var content = document.createElement('div');
      content.className = 'setting-content';
      var actions = document.createElement('div');
      actions.className = 'wut-token-actions';
      var input = document.createElement('input');
      input.id = 'wut-token-login-input';
      input.className = 'wut-token-login-input';
      input.type = 'password';
      input.placeholder = '粘贴 Token';
      input.autocomplete = 'off';
      input.spellcheck = false;
      var button = document.createElement('button');
      button.id = 'wut-token-login-button';
      button.type = 'button';
      button.className = 'setting-btn';
      button.textContent = '用 Token 登录';
      button.addEventListener('click', function () {
        loginWithToken(input.value.trim());
      });
      actions.appendChild(input);
      actions.appendChild(button);
      content.appendChild(actions);
      row.appendChild(label);
      row.appendChild(content);
      host.appendChild(row);
    }
    reportKey = '';
    var anchor = container.querySelector('.btn-check-update');
    container.insertBefore(host, anchor ? anchor.closest('.setting-item') :
      container.querySelector('.setting-btns'));
    updateTokenExpiry();
    render();
  }

  // Never click the app's own controls. Its settings component renders the
  // stored values only after mount; a click before that saves the defaults it
  // was initialized with and wipes taskbar icon, opacity, pet size and autostart.
  function render() {
    var host = document.getElementById(HOST);
    if (!host) return;
    syncTokenLoginVisibility();
    var online = Date.now() - S.beat <= 5000;
    host.querySelectorAll('input[data-preference]').forEach(function (input) {
      var key = input.dataset.preference;
      var pending = S.requests.find(function (r) { return r.key === key; });
      if (input.type === 'range') {
        if (!input.dataset.initialized && S.beat > 0) {
          var initial = Number(S.preferences[key]);
          var minimum = Number(input.min);
          var maximum = Number(input.max);
          if (!isFinite(minimum)) minimum = 0;
          if (!isFinite(maximum)) maximum = 100;
          if (!isFinite(initial)) initial = minimum;
          initial = Math.max(minimum, Math.min(maximum, Math.round(initial)));
          input.value = String(initial);
          var progress = maximum > minimum ? (initial - minimum) / (maximum - minimum) * 100 : 0;
          input.style.setProperty('--range-progress', progress + '%');
          var valueNode = input.parentElement && input.parentElement.querySelector('.setting-value');
          if (valueNode) {
            valueNode.textContent = key === 'auto_emote_rate' ? initial + ' 次/秒' : initial + '%';
          }
          input.dataset.initialized = '1';
        }
        input.disabled = !online;
        return;
      }
      var current = pending ? pending.value : S.preferences[key];
      input.checked = current;
      input.disabled = !online || !!pending;
    });
    var status = host.querySelector('.wut-status');
    var text = !online ? 'Plus 后台未连接，请安装或修复 Plus' :
      feedback.error ? feedback.error :
      S.requests.some(function (r) { return !r.action; }) ? '正在保存…' : feedback.saved ? '已保存 · 即时生效' : '即时生效 · 自动保存';
    if (status.textContent !== text) status.textContent = text;
    status.dataset.error = feedback.error ? 'true' : 'false';
    renderReport();
    updateTokenExpiry();
    renderAppsRow();
    renderAppsDialog();
    var exportButton = document.getElementById('wut-export-button');
    var exportStatus = document.getElementById('wut-export-status');
    if (exportButton && exportStatus) {
      var exporting = S.requests.some(function (r) { return r.action === 'export_logs'; });
      exportButton.disabled = !online || exporting;
      exportStatus.textContent = exporting ? '正在导出…' : S.exportError ? '导出失败，请检查桌面写入权限后重试。' :
        S.exportPath ? '已导出：' + S.exportPath : '将 Plus 和 Wuwa Tappo 日志打包到桌面。';
    }
  }

  function disposePanel() {
    [HOST, STYLE].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.remove();
    });
  }

  S.update = function (state) {
    S.beat = Date.now();
    S.preferences = state.preferences;
    S.accessories = state.accessories || S.accessories || {};
    S.petAccessory = state.pet_accessory || null;
    S.report = state.report || null;
    (state.ack || []).forEach(function (ack) {
      S.requests = S.requests.filter(function (r) { return r.id !== ack.id; });
      if (ack.action === 'list_apps') {
        apps.receive(ack.apps);
        return;
      }
      if (ack.action === 'export_logs') {
        S.exportError = !!ack.error;
        S.exportPath = ack.path || null;
        return;
      }
      if (ack.error) { feedback.error = '保存失败，已恢复原设置'; feedback.saved = false; }
      else if (!feedback.error) feedback.saved = true;
    });
    atlasView.sync();
    mount(); render();
  };
  var atlasView = createAtlasView(VERSION, {
    pet: function () { return S.petAccessory; },
    overrides: function () { return S.accessories; },
    pending: function (file) {
      return S.requests.some(function (request) {
        return request.action === 'save_accessory' && request.file === file;
      });
    },
    save: queueRequest
  });
  var observer = new MutationObserver(mount);
  observer.observe(document.body, { childList: true, subtree: true });
  var timer = setInterval(function () { mount(); render(); }, 1000);
  S.uninstall = function () {
    atlasView.dispose();
    apps.close();
    observer.disconnect(); clearInterval(timer);
    disposePanel();
    delete window.__WUT_SETTINGS__;
  };
  mount();
  return 'installed';
})()
