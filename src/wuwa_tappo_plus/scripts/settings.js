(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_SETTINGS__ && window.__WUT_SETTINGS__.version === VERSION) return 'exists';
  if (window.__WUT_SETTINGS__) window.__WUT_SETTINGS__.uninstall();

  var S = window.__WUT_SETTINGS__ = {
    version: VERSION, requests: [], beat: 0, report: null,
    exportPath: null, exportError: false, accessories: {},
    _test: { emitLoginSuccess: emitLoginSuccess },
    preferences: { auto_open: true, balance_reminder: true, bubble: true, animation: true,
                   remember_screen: true, auto_emote: false, pause_clicks: false,
                   game_mode_plus: true, game_mode_opacity: 30, auto_emote_rate: 20 }
  };
  var HOST = 'wut-plus-settings';
  var STYLE = 'wut-plus-settings-style';
  var serial = 0;
  var instance = Date.now().toString(36) + Math.random().toString(36).slice(2);
  var error = '';
  var saved = false;
  var reportKey = '';

  var definitions = [
    ['auto_open', '自动开箱', '宝箱到期且余额充足时自动开启，关闭后仍显示倒计时。'],
    ['auto_emote', '自动发表情', '桌宠会跟随敲击自动发送一个已装备的表情；频率可在下方调整。'],
    ['game_mode_plus', '全屏进入游戏模式·Plus', '开启后由 Plus 接管全屏自动进入游戏模式：窗口化/无边框全屏也生效，官方全屏开关会暂时关闭；关闭后恢复官方设置；手动退出后，本次全屏期间不再自动进入。'],
    ['balance_reminder', '余额提醒', '余额不足时提醒，并显示宝箱倒计时；不影响其他气泡。'],
    ['bubble', '气泡显示', '显示 Plus 气泡，包括余额提醒和小黑屋模式文字。'],
    ['animation', '气泡动画', '让气泡轻轻跳动；关闭后保留静态气泡。'],
    ['remember_screen', '屏幕记忆', '记住桌宠所在的显示器，重启后自动回到该屏幕；显示器不可用时保持原样。'],
    ['hello_boss', '王总好模式', '让房间里的其他桌宠都面朝你，仅在本机生效'],
    ['pause_clicks', '小黑屋模式', '暂停累积和上报新的点击，但仍保留桌宠敲击动画']
  ];

  function cookieValue(name) {
    try {
      var parts = document.cookie ? document.cookie.split(';') : [];
      for (var i = 0; i < parts.length; i++) {
        var part = parts[i].trim();
        if (part.indexOf(name + '=') === 0) {
          return decodeURIComponent(part.slice(name.length + 1));
        }
      }
    } catch (e) {}
    return '';
  }

  function readInfoToken() {
    return cookieValue('kuro_infoToken');
  }

  function readTokenExpiry() {
    var raw = cookieValue('kuro_lastLoginTime');
    if (!raw) return null;
    var text = String(raw).trim();
    if (!text) return null;
    var value = Number(text);
    if (Number.isFinite(value) && value > 0) return value < 1e12 ? value * 1000 : value;
    var parsed = Date.parse(text);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function formatTokenExpiry(expiry) {
    if (!expiry) return '有效期未知';
    var ms = expiry - Date.now();
    if (ms <= 0) return '已过期，请重新登录或续期';
    var days = Math.floor(ms / 86400000);
    var hours = Math.floor((ms % 86400000) / 3600000);
    var minutes = Math.floor((ms % 3600000) / 60000);
    return '剩余' + (days > 0 ? days + '天' : '') +
      (hours > 0 ? hours + '小时' : '') + minutes + '分钟';
  }

  function updateTokenExpiry() {
    var node = document.getElementById('wut-token-expiry');
    if (!node) return;
    var expiry = readTokenExpiry();
    node.textContent = '当前Token有效期：' + formatTokenExpiry(expiry);
    var soon = expiry === null || expiry - Date.now() < 2 * 86400000;
    node.dataset.soon = soon ? 'true' : 'false';
    var renew = document.getElementById('wut-token-renew');
    if (renew) {
      renew.hidden = false;
      renew.style.display = '';
    }
  }

  function isLoggedIn() {
    try {
      var root = document.querySelector('#app');
      var app = root && root.__vue_app__;
      var pinia = app && app.config.globalProperties.$pinia;
      var store = pinia && pinia._s.get('GlobalState');
      return store ? store.isLoggedIn === true : null;
    } catch (e) { return null; }
  }

  // Token login is a recovery path for a fresh or logged-out client. When the
  // app already has a session, pasting another token only risks clobbering it.
  function syncTokenLoginVisibility() {
    var row = document.getElementById('wut-token-login-row');
    if (!row) return;
    var hidden = isLoggedIn() === true;
    row.hidden = hidden;
    row.style.display = hidden ? 'none' : '';
  }

  function showAppToast(message, delay) {
    var script = document.querySelector('script[type="module"][src]');
    if (!script) {
      try { window.alert(message); } catch (e) {}
      return;
    }
    import(script.src).then(function (module) {
      var values = Object.values(module);
      var toast = values.find(function (value) {
        return typeof value === 'function' && value.name === 'showToast';
      });
      if (!toast) throw new Error('toast API unavailable');
      toast(message, delay || 2500);
    }).catch(function () {
      try { window.alert(message); } catch (e) {}
    });
  }

  function setCookieValue(name, value, days) {
    var expires = new Date(Date.now() + days * 86400000).toUTCString();
    document.cookie = name + '=' + encodeURIComponent(value) + '; expires=' + expires + '; path=/';
  }

  function renewToken() {
    var script = document.querySelector('script[type="module"][src]');
    if (!script) return Promise.reject(new Error('native module missing'));
    return import(script.src).then(function (module) {
      var values = Object.values(module);
      var renew = values.find(function (value) {
        return typeof value === 'function' && value.name === 'renewTokenIfNeeded';
      });
      if (!renew) throw new Error('renew API unavailable');
      var original = cookieValue('kuro_lastLoginTime');
      var temporary = String(Math.floor((Date.now() + 60 * 1000) / 1000));
      setCookieValue('kuro_lastLoginTime', temporary, 7);
      function restore() {
        if (original) {
          setCookieValue('kuro_lastLoginTime', original, 7);
        } else {
          document.cookie = 'kuro_lastLoginTime=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';
        }
      }
      return renew().then(function (renewed) {
        if (!renewed) {
          restore();
          throw new Error('renew rejected');
        }
        return true;
      }, function (error) {
        restore();
        throw error;
      });
    });
  }

  function extractToken() {
    var token = readInfoToken();
    if (!token) {
      showAppToast('未找到 Token，请先登录 Wuwa Tappo。');
      return;
    }
    copyToken(token).then(function () {
      showAppToast('Token已提取至剪贴板。Token是你的账号在Wuwa Tappo的登录凭证，'
        + '请勿分享给他人，也不要粘贴到不可信的应用或工具。'
        + '如果泄露Token，请立刻在Wuwa Tappo退出账号', 8000);
    }).catch(function () {
      showAppToast('复制失败，请检查剪贴板权限后重试。');
    });
  }

  function applyAuthToken(token) {
    var script = document.querySelector('script[type="module"][src]');
    if (!script) return Promise.reject(new Error('native module missing'));
    return import(script.src).then(function (module) {
      var values = Object.values(module);
      var apply = values.find(function (value) {
        return typeof value === 'function' && value.name === 'applyAuthTokens';
      });
      if (!apply) throw new Error('applyAuthTokens unavailable');
      apply({ token: token, expire_time: String(Date.now() + 7 * 86400000) });
    });
  }

  // The official SDK login broadcasts this event so the pet window hydrates the
  // new session, reconnects the socket and restores its room. Token login must
  // do the same; reloading only the settings window leaves the pet logged out.
  function emitLoginSuccess() {
    try {
      if (window.__TAURI_INTERNALS__ && window.__TAURI_INTERNALS__.invoke) {
        return Promise.resolve(window.__TAURI_INTERNALS__.invoke('plugin:event|emit', {
          event: 'cat-auth-login-success', payload: {}
        })).catch(function () {});
      }
    } catch (e) {}
    return Promise.resolve();
  }

  function loginWithToken(token) {
    if (!token) {
      showAppToast('请先粘贴 Token。');
      return Promise.resolve(false);
    }
    return applyAuthToken(token).then(emitLoginSuccess).then(function () {
      showAppToast('Token 已写入，正在重新登录…', 1500);
      setTimeout(function () { location.reload(); }, 400);
      return true;
    }).catch(function () {
      showAppToast('Token 登录失败，请检查 Token 是否有效。');
      return false;
    });
  }

  function copyToken(text) {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      try {
        var area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        area.style.left = '-9999px';
        document.body.appendChild(area);
        area.focus();
        area.select();
        var ok = document.execCommand('copy');
        document.body.removeChild(area);
        if (ok) resolve(); else reject(new Error('copy failed'));
      } catch (e) {
        reject(e);
      }
    });
  }

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
        S.requests.push({ id: instance + '-' + (++serial), key: definition[0], value: input.checked });
        error = ''; saved = false;
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
      if (definition[0] === 'game_mode_plus') appendGameModeOpacityRow();
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
      S.requests.push({ id: instance + '-' + (++serial), key: 'auto_emote_rate',
                        value: Number(emoteRange.value) });
      error = ''; saved = false;
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
      S.requests.push({ id: instance + '-' + (++serial), key: 'game_mode_opacity',
                        value: Number(gameRange.value) });
      error = ''; saved = false;
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
      S.requests.push({ id: instance + '-' + (++serial), action: 'export_logs' });
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
      error ? error :
      S.requests.some(function (r) { return !r.action; }) ? '正在保存…' : saved ? '已保存 · 即时生效' : '即时生效 · 自动保存';
    if (status.textContent !== text) status.textContent = text;
    status.dataset.error = error ? 'true' : 'false';
    renderReport();
    updateTokenExpiry();
    var exportButton = document.getElementById('wut-export-button');
    var exportStatus = document.getElementById('wut-export-status');
    if (exportButton && exportStatus) {
      var exporting = S.requests.some(function (r) { return r.action === 'export_logs'; });
      exportButton.disabled = !online || exporting;
      exportStatus.textContent = exporting ? '正在导出…' : S.exportError ? '导出失败，请检查桌面写入权限后重试。' :
        S.exportPath ? '已导出：' + S.exportPath : '将 Plus 和 Wuwa Tappo 日志打包到桌面。';
    }
  }

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

  S.update = function (state) {
    S.beat = Date.now();
    S.preferences = state.preferences;
    S.accessories = state.accessories || S.accessories || {};
    S.petAccessory = state.pet_accessory || null;
    S.report = state.report || null;
    (state.ack || []).forEach(function (ack) {
      S.requests = S.requests.filter(function (r) { return r.id !== ack.id; });
      if (ack.action === 'export_logs') {
        S.exportError = !!ack.error;
        S.exportPath = ack.path || null;
        return;
      }
      if (ack.error) { error = '保存失败，已恢复原设置'; saved = false; }
      else if (!error) saved = true;
    });
    var live = document.getElementById('wut-atlas-modal');
    if (live && live.__wutAccessory && S.petAccessory &&
        S.petAccessory.file === live.__wutAccessory.key) {
      live.__wutAccessory.setBase(S.petAccessory.x, S.petAccessory.y, S.petAccessory.size);
    }
    mount(); render();
  };
  function createAtlasView() {
    var styleId = 'wut-atlas-style';
    var catalog = null;
    var loading = null;
    var modal = null;
    var keyHandler = null;
    var blinkTimer = null;
    var blinkClose = null;
    var controlTimer = null;
    var entryTimer = null;
    var tapTimers = [];
    var ATLAS_TYPES = { character_skin_atlas: 'character_skin',
                        accessory_atlas: 'accessory',
                        emote_atlas: 'emote' };

    function fileOf(url) {
      var text = String(url || '').split('?')[0];
      var parts = text.split('/');
      return (parts.length ? parts[parts.length - 1] : text).toLowerCase();
    }

    function offsetOf(layer) {
      if (!layer) return [0, 0];
      var x = Number(layer.my_offset_x || 0);
      var y = Number(layer.my_offset_y || 0);
      if (!x && !y) { x = Number(layer.offset_x || 0); y = Number(layer.offset_y || 0); }
      return [x, y];
    }

    function place(image, layer, scale) {
      var offset = offsetOf(layer);
      image.style.transform = 'translate(-50%, -50%) translate(' +
        (offset[0] * scale) + 'px, ' + (offset[1] * scale) + 'px)';
    }

    function assetUrl(rel) {
      var text = String(rel || '').trim();
      if (!text) return '';
      if (/^https?:\/\//i.test(text)) return text;
      var host = String(catalog && catalog.host || '').replace(/\/+$/, '');
      return host ? host + '/' + text.replace(/^\/+/, '') : text;
    }

    function loadCatalog() {
      if (catalog) return Promise.resolve(catalog);
      if (loading) return loading;
      loading = new Promise(function (resolve, reject) {
        var request;
        try { request = indexedDB.open('wuwatappo_appearance_asset', 1); }
        catch (e) { reject(e); return; }
        request.onerror = function () { reject(request.error || new Error('indexedDB error')); };
        request.onsuccess = function () {
          var db = request.result;
          try {
            var tx = db.transaction('catalog', 'readonly');
            var get = tx.objectStore('catalog').get('appearance_asset_catalog_v1');
            get.onsuccess = function () {
              var record = get.result;
              if (!record || !record.payload) { reject(new Error('外观数据未缓存')); return; }
              catalog = { payload: record.payload, host: record.assets_host || '' };
              resolve(catalog);
            };
            get.onerror = function () { reject(get.error || new Error('外观数据读取失败')); };
          } catch (e) { reject(e); }
        };
      });
      return loading;
    }

    function findAtlas(payload, iconUrl) {
      var file = fileOf(iconUrl);
      var found = null;
      Object.keys(ATLAS_TYPES).forEach(function (type) {
        var map = payload[type] || {};
        Object.keys(map).forEach(function (id) {
          var entry = map[id];
          if (!found && entry && entry.url && fileOf(entry.url) === file) {
            found = { atlasType: type, actualType: ATLAS_TYPES[type], atlasId: id,
                      height: entry.height_light_id, entry: entry };
          }
        });
      });
      return found;
    }

    function actualOf(payload, atlas) {
      if (!atlas || !atlas.height) return null;
      return (payload[atlas.actualType] || {})[String(atlas.height)] || null;
    }

    function currentCharacter(payload, user) {
      if (!payload || !payload.character_skin) return null;
      var name = user && user.character && user.character.name ? fileOf(user.character.name) : '';
      var role = user && user.role_id ? String(user.role_id) : '';
      var byName = null;
      var byRole = null;
      Object.keys(payload.character_skin).forEach(function (id) {
        var entry = payload.character_skin[id];
        if (!entry) return;
        if (!byRole && role && String(entry.game_role_id) === role) byRole = entry;
        if (name && (fileOf(entry.url) === name ||
            (entry.body1 && fileOf(entry.body1.url) === name) ||
            (entry.head1 && fileOf(entry.head1.url) === name))) byName = entry;
      });
      return byName || byRole;
    }

    function currentUser() {
      try {
        var root = document.querySelector('#app');
        var app = root && root.__vue_app__;
        var pinia = app && app.config.globalProperties.$pinia;
        var state = pinia && pinia._s.get('GlobalState');
        return state && state.$state ? state.$state.userInfo : null;
      } catch (e) { return null; }
    }

    function ensureStyle() {
      if (document.getElementById(styleId)) return;
      var style = document.createElement('style');
      style.id = styleId;
      style.textContent = '#wut-atlas-modal{position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);z-index:2147483000;background:none;user-select:none;-webkit-user-select:none;}'
        + '#wut-atlas-modal .wut-atlas-card{width:340px;max-width:calc(100vw - 32px);background:#fffaf3;border:1px solid rgba(142,94,67,.25);border-radius:16px;box-shadow:0 20px 50px rgba(60,35,20,.28);padding:16px 18px 14px;color:#5b4433;}'
        + '#wut-atlas-modal .wut-atlas-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}'
        + '#wut-atlas-modal .wut-atlas-head strong{font-size:17px;color:#5b4433;}'
        + '#wut-atlas-modal .wut-atlas-close{border:0;background:transparent;color:#8e7b6a;font-size:22px;line-height:1;cursor:pointer;padding:0 2px;}'
        + '#wut-atlas-modal .wut-atlas-close:hover{color:#b4543c;}'
        + '#wut-atlas-modal .wut-atlas-stage{position:relative;width:280px;height:280px;margin:12px auto 6px;border-radius:14px;background:transparent;overflow:hidden;}'
        + '#wut-atlas-modal .wut-atlas-layer{position:absolute;left:50%;top:50%;width:280px;height:280px;transform:translate(-50%,-50%);pointer-events:none;user-select:none;}'
        + '#wut-atlas-modal .wut-atlas-stage--character{cursor:pointer;}'
        + '#wut-atlas-modal .wut-atlas-emote{display:block;max-width:250px;max-height:250px;margin:0 auto;}'
        + '#wut-atlas-modal .wut-atlas-note{margin-top:6px;font-size:12px;line-height:1.5;color:#8e7b6a;text-align:center;}'
        + '#wut-atlas-modal .wut-atlas-controls{margin-top:8px;border-top:1px solid rgba(142,94,67,.16);padding-top:8px;}'
        + '#wut-atlas-modal .wut-atlas-control{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px;color:#8e7b6a;}'
        + '#wut-atlas-modal .wut-atlas-control label{flex:none;width:30px;}'
        + '#wut-atlas-modal .wut-atlas-control input[type=range]{flex:1;min-width:0;accent-color:#b98a5e;}'
        + '#wut-atlas-modal .wut-atlas-value{flex:none;width:44px;text-align:right;font-variant-numeric:tabular-nums;}'
        + '#wut-atlas-modal .wut-atlas-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:8px;}'
        + '#wut-atlas-modal .wut-atlas-actions button{border:1px solid rgba(142,94,67,.3);background:#fff6e9;color:#6a4f3a;border-radius:8px;padding:4px 12px;font-size:12px;cursor:pointer;}'
        + '#wut-atlas-modal .wut-atlas-actions button:disabled{opacity:.5;cursor:wait;}'
        + '#wut-atlas-modal .wut-atlas-hint{margin-top:6px;font-size:11px;color:#8e7b6a;text-align:center;}'
        + '.wut-accessory-position{position:absolute;right:0;top:50%;transform:translateY(-50%);height:30px;padding:0 16px;border:2px solid #ebce9b;border-radius:18px;background:#fef6dd;color:#8e7b6a;font-size:18px;line-height:26px;font-family:inherit;cursor:pointer;}'
        + '.wut-accessory-position:hover{background:#fff;color:#b4543c;}'
        + '#wut-atlas-modal .wut-atlas-loading{display:flex;align-items:center;justify-content:center;height:280px;color:#8e7b6a;font-size:13px;}';
      document.head.appendChild(style);
    }

    function clearTimers() {
      if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null; }
      if (blinkClose) { clearTimeout(blinkClose); blinkClose = null; }
      if (controlTimer) { clearInterval(controlTimer); controlTimer = null; }
      tapTimers.forEach(function (id) { clearTimeout(id); });
      tapTimers = [];
    }

    function appFont() {
      try {
        var element = document.querySelector('.atlas-module') || document.querySelector('.panel-content') || document.body;
        var font = getComputedStyle(element).fontFamily;
        if (font) return font;
      } catch (e) {}
      return '"975MaruSC","Microsoft YaHei","Helvetica Neue",Helvetica,simSun,sans-serif';
    }

    function close() {
      clearTimers();
      if (keyHandler) { document.removeEventListener('keydown', keyHandler); keyHandler = null; }
      if (modal && modal.parentNode) modal.parentNode.removeChild(modal);
      modal = null;
    }

    function build(kind, name) {
      close();
      ensureStyle();
      modal = document.createElement('div');
      modal.id = 'wut-atlas-modal';
      modal.setAttribute('role', 'dialog');
      modal.setAttribute('aria-modal', 'true');
      modal.style.fontFamily = appFont();
      modal.innerHTML = '<div class="wut-atlas-card">'
        + '<div class="wut-atlas-head"><div><strong></strong></div>'
        + '<button type="button" class="wut-atlas-close" aria-label="关闭">×</button></div>'
        + '<div class="wut-atlas-stage"><div class="wut-atlas-loading">正在加载预览…</div></div>'
        + '<div class="wut-atlas-note"></div></div>';
      modal.querySelector('.wut-atlas-head strong').textContent = name || '未命名';
      modal.querySelector('.wut-atlas-close').addEventListener('click', close);
      keyHandler = function (event) { if (event.key === 'Escape') close(); };
      document.addEventListener('keydown', keyHandler);
      var host = document.querySelector('.module-block.atlas-module .panel-content')
        || document.querySelector('.panel-content') || document.body;
      host.appendChild(modal);
      return modal;
    }

    function stageOf() { return modal && modal.querySelector('.wut-atlas-stage'); }
    function noteOf() { return modal && modal.querySelector('.wut-atlas-note'); }

    function fallback(iconSrc, text) {
      var stage = stageOf();
      if (!stage) return;
      stage.innerHTML = '';
      stage.classList.remove('wut-atlas-stage--character');
      var image = document.createElement('img');
      image.className = 'wut-atlas-emote';
      image.src = iconSrc;
      image.alt = '';
      image.draggable = false;
      stage.appendChild(image);
      noteOf().textContent = text;
    }

    function addLayer(className, src, layer) {
      var image = document.createElement('img');
      image.className = 'wut-atlas-layer ' + className;
      image.src = src;
      image.alt = '';
      image.draggable = false;
      // The app draws the head/body frames without their own transform.
      image.style.transform = 'translate(-50%, -50%)';
      return image;
    }

    var tapHand = 'left';

    function tapStage(stage, body, head, frames) {
      if (blinkClose) { clearTimeout(blinkClose); blinkClose = null; }
      if (head) head.src = frames.head1;
      var hand = tapHand;
      tapHand = hand === 'left' ? 'right' : 'left';
      tapTimers.forEach(function (id) { clearTimeout(id); });
      tapTimers = [];
      body.src = hand === 'left' ? frames.body2 : frames.body3;
      tapTimers.push(setTimeout(function () { body.src = frames.body1; }, 220));
    }

    function characterMarkup(entry) {
      var stage = stageOf();
      stage.innerHTML = '';
      stage.classList.add('wut-atlas-stage--character');
      var head = addLayer('wut-atlas-head-layer', assetUrl(entry.head1 && entry.head1.url), entry.head1);
      var body = addLayer('wut-atlas-body-layer', assetUrl(entry.body1 && entry.body1.url), entry.body1);
      stage.appendChild(head);
      stage.appendChild(body);
      var frames = {
        body1: assetUrl(entry.body1 && entry.body1.url),
        body2: assetUrl((entry.body2 || entry.body1) && (entry.body2 || entry.body1).url),
        body3: assetUrl((entry.body3 || entry.body2 || entry.body1) &&
                        (entry.body3 || entry.body2 || entry.body1).url),
        head1: assetUrl(entry.head1 && entry.head1.url),
        head2: assetUrl((entry.head2 || entry.head1) && (entry.head2 || entry.head1).url)
      };
      stage.addEventListener('click', function () { tapStage(stage, body, head, frames); });
      if (frames.head2 && frames.head2 !== frames.head1) {
        blinkTimer = setInterval(function () {
          head.src = frames.head2;
          blinkClose = setTimeout(function () { head.src = frames.head1; blinkClose = null; }, 240);
        }, 5000);
      }
      noteOf().textContent = '点击预览敲击动画';
    }

    function accessoryMarkup(entry, current) {
      var stage = stageOf();
      stage.innerHTML = '';
      stage.classList.remove('wut-atlas-stage--character');
      var key = fileOf(entry.url);
      var petBase = S.petAccessory && S.petAccessory.file === key ? S.petAccessory : null;
      var size = petBase && Number(petBase.size) > 0 ? Number(petBase.size) : 200;
      var head = null;
      var body = null;
      // Draw at the pet's own CSS size so the accessory sits at the same place
      // as on the real cat instead of only looking proportional.
      stage.style.width = size + 'px';
      stage.style.height = size + 'px';
      if (current) {
        stage.classList.add('wut-atlas-stage--character');
        head = addLayer('wut-atlas-head-layer', assetUrl(current.head1 && current.head1.url), current.head1);
        body = addLayer('wut-atlas-body-layer', assetUrl(current.body1 && current.body1.url), current.body1);
        [head, body].forEach(function (image) {
          image.style.width = size + 'px';
          image.style.height = size + 'px';
        });
        stage.appendChild(head);
        stage.appendChild(body);
        var frames = {
          body1: assetUrl(current.body1 && current.body1.url),
          body2: assetUrl((current.body2 || current.body1) && (current.body2 || current.body1).url),
          body3: assetUrl((current.body3 || current.body2 || current.body1) &&
                          (current.body3 || current.body2 || current.body1).url),
          head1: assetUrl(current.head1 && current.head1.url)
        };
        stage.addEventListener('click', function () { tapStage(stage, body, head, frames); });
      }
      var baseX = petBase ? Number(petBase.x)
        : (current ? Number(current.offset_x || 0) : 0) + Number(entry.offset_x || 0);
      var baseY = petBase ? Number(petBase.y)
        : (current ? Number(current.offset_y || 0) : 0) + Number(entry.offset_y || 0);
      var saved = (S.accessories && S.accessories[key]) || null;
      var ov = { x: saved ? Number(saved.x) : 0, y: saved ? Number(saved.y) : 0,
                 scale: saved ? Number(saved.scale) : 1,
                 rotation: saved ? Number(saved.rotation) : 0 };
      var accessory = document.createElement('img');
      accessory.className = 'wut-atlas-layer wut-atlas-accessory-layer';
      accessory.src = assetUrl(entry.url);
      accessory.alt = '';
      accessory.draggable = false;
      accessory.style.width = size + 'px';
      accessory.style.height = size + 'px';
      function apply() {
        accessory.style.transform = 'translate(-50%,-50%) translate(' +
          (baseX + ov.x) + 'px, ' + (baseY + ov.y) + 'px)' +
          ' scale(' + ov.scale + ') rotate(' + ov.rotation + 'deg)';
      }
      apply();
      stage.appendChild(accessory);
      noteOf().textContent = '';
      accessoryControls(key, ov, apply);
      if (modal) {
        modal.__wutAccessory = { key: key, setBase: function (x, y, nextSize) {
          baseX = Number(x); baseY = Number(y);
          var want = Number(nextSize);
          if (want > 0 && want !== size) {
            size = want;
            stage.style.width = size + 'px';
            stage.style.height = size + 'px';
            [head, body, accessory].forEach(function (image) {
              if (!image) return;
              image.style.width = size + 'px';
              image.style.height = size + 'px';
            });
          }
          apply();
        } };
      }
    }

    function accessoryControls(key, ov, apply) {
      var card = modal && modal.querySelector('.wut-atlas-card');
      if (!card) return;
      var box = document.createElement('div');
      box.className = 'wut-atlas-controls';
      var fields = [['x', 'X', -200, 200, 1], ['y', 'Y', -200, 200, 1],
                    ['scale', '缩放', 0.5, 2, 0.01], ['rotation', '旋转', -180, 180, 1]];
      var inputs = {};
      fields.forEach(function (field) {
        var row = document.createElement('div');
        row.className = 'wut-atlas-control';
        var label = document.createElement('label');
        label.textContent = field[1];
        var input = document.createElement('input');
        input.type = 'range';
        input.min = field[2];
        input.max = field[3];
        input.step = field[4];
        input.value = String(ov[field[0]]);
        var value = document.createElement('span');
        value.className = 'wut-atlas-value';
        value.textContent = formatAccessoryValue(field[0], ov[field[0]]);
        input.addEventListener('input', function () {
          ov[field[0]] = Number(input.value);
          value.textContent = formatAccessoryValue(field[0], ov[field[0]]);
          apply();
        });
        inputs[field[0]] = { input: input, value: value };
        row.appendChild(label);
        row.appendChild(input);
        row.appendChild(value);
        box.appendChild(row);
      });
      var hint = document.createElement('div');
      hint.className = 'wut-atlas-hint';
      hint.textContent = '仅影响本机显示；保存后桌宠上的同一个配件也会应用。';
      box.appendChild(hint);
      var actions = document.createElement('div');
      actions.className = 'wut-atlas-actions';
      var reset = document.createElement('button');
      reset.type = 'button';
      reset.textContent = '恢复默认';
      var save = document.createElement('button');
      save.type = 'button';
      save.textContent = '保存';
      reset.addEventListener('click', function () {
        ov.x = 0; ov.y = 0; ov.scale = 1; ov.rotation = 0;
        Object.keys(inputs).forEach(function (name) {
          inputs[name].input.value = String(ov[name]);
          inputs[name].value.textContent = formatAccessoryValue(name, ov[name]);
        });
        apply();
        pushAccessorySave(key, ov);
      });
      save.addEventListener('click', function () { pushAccessorySave(key, ov); });
      actions.appendChild(reset);
      actions.appendChild(save);
      box.appendChild(actions);
      card.appendChild(box);
      if (controlTimer) { clearInterval(controlTimer); controlTimer = null; }
      controlTimer = setInterval(function () {
        var pending = S.requests.some(function (request) {
          return request.action === 'save_accessory' && request.file === key;
        });
        save.disabled = pending;
        save.textContent = pending ? '保存中…' : '保存';
      }, 300);
    }

    function formatAccessoryValue(name, value) {
      if (name === 'scale') return Number(value).toFixed(2);
      if (name === 'rotation') return String(Math.round(value)) + '°';
      return String(Math.round(value));
    }

    function pushAccessorySave(key, ov) {
      S.requests.push({ id: instance + '-' + (++serial), action: 'save_accessory',
                        file: key, x: ov.x, y: ov.y, scale: ov.scale, rotation: ov.rotation });
      error = '';
      saved = false;
    }

    function emoteMarkup(entry) {
      var stage = stageOf();
      stage.innerHTML = '';
      stage.classList.remove('wut-atlas-stage--character');
      var image = document.createElement('img');
      image.className = 'wut-atlas-emote';
      image.src = assetUrl(entry.url);
      image.alt = '';
      image.draggable = false;
      stage.appendChild(image);
      noteOf().textContent = '';
    }

    function openPreview(kind, name, iconSrc) {
      build(kind, name);
      loadCatalog().then(function (data) {
        var atlas = findAtlas(data.payload, iconSrc);
        var entry = actualOf(data.payload, atlas);
        if (kind === 'character') {
          if (entry && entry.body1 && entry.head1) characterMarkup(entry);
          else fallback(iconSrc, '暂无全身素材，显示图鉴图标。');
        } else if (kind === 'accessory') {
          if (entry && entry.url) accessoryMarkup(entry, currentCharacter(data.payload, currentUser()));
          else fallback(iconSrc, '暂无配件素材，显示图鉴图标。');
        } else {
          if (entry && entry.url) emoteMarkup(entry);
          else fallback(iconSrc, '暂无表情素材，显示图鉴图标。');
        }
      }).catch(function () {
        fallback(iconSrc, '外观数据暂不可用，显示图鉴图标。');
      });
    }

    function openItemPreview(item) {
      var classes = item.className;
      var kind = /character_skin/.test(classes) ? 'character'
        : /accessory/.test(classes) ? 'accessory' : 'emote';
      var nameNode = item.querySelector('.atlas-name');
      var image = item.querySelector('.atlas-thumb img') || item.querySelector('img');
      if (!image || !image.src) return;
      openPreview(kind, nameNode ? nameNode.textContent.trim() : '', image.src);
    }

    function openEquippedAccessory() {
      var file = S.petAccessory && S.petAccessory.file;
      if (!file) return;
      loadCatalog().then(function (data) {
        var payload = data.payload;
        var entry = null;
        Object.keys(payload.accessory || {}).forEach(function (id) {
          var item = payload.accessory[id];
          if (!entry && item.url && fileOf(item.url) === file) entry = item;
        });
        if (!entry || !entry.url) return;
        build('accessory', '配件预览');
        accessoryMarkup(entry, currentCharacter(payload, currentUser()));
      }).catch(function () {});
    }

    // Put the accessory entry on the rarity-filter row of the accessories tab,
    // at its far right, instead of covering the preview or every tile.
    function mountAccessoryEntry() {
      ensureStyle();
      var button = document.querySelector('.wut-accessory-position');
      var active = Array.from(document.querySelectorAll('.atlas-tab-item')).find(function (tab) {
        return tab.classList.contains('atlas-tab-item--active') &&
          tab.textContent.trim() === '配件';
      });
      var rarity = document.querySelector('.atlas-select .select-item--rarity');
      var row = rarity ? rarity.closest('.atlas-select') : null;
      var file = S.petAccessory && S.petAccessory.file;
      if (!active || !row || !file) {
        if (button) button.remove();
        return;
      }
      if (button && button.parentElement === row) return;
      if (button) button.remove();
      if (getComputedStyle(row).position === 'static') row.style.position = 'relative';
      button = document.createElement('button');
      button.type = 'button';
      button.className = 'wut-accessory-position';
      button.textContent = '配件位置调整';
      button.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        openEquippedAccessory();
      });
      row.appendChild(button);
    }

    function onClick(event) {
      var target = event.target;
      var item = target && target.closest ? target.closest('.atlas-item--unowned') : null;
      if (!item || !item.closest('.atlas-module')) return;
      if (event.button && event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      openItemPreview(item);
    }

    document.addEventListener('click', onClick, true);
    document.querySelectorAll('.wut-accessory-entry, .wut-atlas-preview-btn')
      .forEach(function (button) { button.remove(); });
    mountAccessoryEntry();
    entryTimer = setInterval(mountAccessoryEntry, 700);
    window.__WUT_ATLAS__ = {
      version: VERSION,
      _test: { fileOf: fileOf, findAtlas: findAtlas, actualOf: actualOf,
               currentCharacter: currentCharacter, offsetOf: offsetOf,
               openPreview: openPreview, openItemPreview: openItemPreview,
               openEquippedAccessory: openEquippedAccessory },
      dispose: function () {
        document.removeEventListener('click', onClick, true);
        if (entryTimer) { clearInterval(entryTimer); entryTimer = null; }
        document.querySelectorAll('.wut-accessory-entry, .wut-accessory-position, .wut-atlas-preview-btn')
          .forEach(function (button) { button.remove(); });
        close();
        var style = document.getElementById(styleId);
        if (style) style.remove();
        delete window.__WUT_ATLAS__;
      }
    };
    return window.__WUT_ATLAS__;
  }
  var atlasView = createAtlasView();
  var observer = new MutationObserver(mount);
  observer.observe(document.body, { childList: true, subtree: true });
  var timer = setInterval(function () { mount(); render(); }, 1000);
  S.uninstall = function () {
    atlasView.dispose();
    observer.disconnect(); clearInterval(timer);
    [HOST, STYLE].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.remove();
    });
    delete window.__WUT_SETTINGS__;
  };
  mount();
  return 'installed';
})()
