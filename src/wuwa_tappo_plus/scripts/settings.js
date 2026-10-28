(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_SETTINGS__ && window.__WUT_SETTINGS__.version === VERSION) return 'exists';
  if (window.__WUT_SETTINGS__) window.__WUT_SETTINGS__.uninstall();

  var S = window.__WUT_SETTINGS__ = {
    version: VERSION, requests: [], beat: 0, panelError: null, report: null,
    preferences: { auto_open: true, balance_reminder: true, bubble: true, animation: true, keep_panel_open: true,
                   remember_screen: true, pause_clicks: false }
  };
  var HOST = 'wut-plus-settings';
  var STYLE = 'wut-plus-settings-style';
  var serial = 0;
  var instance = Date.now().toString(36) + Math.random().toString(36).slice(2);
  var error = '';
  var saved = false;
  var reportKey = '';
  var nativePinned = null;
  var pinPending = false;
  var disposed = false;

  function keepPanelOpen() {
    var desired = S.preferences.keep_panel_open !== false;
    if (disposed || nativePinned === desired || pinPending) return;
    pinPending = true;
    var request;
    try {
      request = window.__TAURI_INTERNALS__.invoke(
        'set_skip_panel_outside_click_close', { skip: desired }
      );
    } catch (e) { request = Promise.reject(e); }
    Promise.resolve(request).then(function () {
      nativePinned = desired;
      S.panelError = null;
    }, function (e) { S.panelError = String(e); }).then(function () {
      pinPending = false;
    });
  }
  var definitions = [
    ['auto_open', '自动开箱', '宝箱到期且余额充足时自动开启，关闭后仍显示倒计时。'],
    ['balance_reminder', '余额提醒', '余额不足时提醒，并显示宝箱倒计时；不影响其他气泡。'],
    ['bubble', '气泡显示', '显示 Plus 气泡，包括余额提醒和小黑屋模式文字。'],
    ['animation', '气泡动画', '让气泡轻轻跳动；关闭后保留静态气泡。'],
    ['keep_panel_open', '主窗口保持打开', '点击其他窗口时不自动收起，仍可使用关闭按钮手动关闭。'],
    ['remember_screen', '屏幕记忆', '记住桌宠所在的显示器，重启后自动回到该屏幕；显示器不可用时保持原样。'],
    ['pause_clicks', '小黑屋模式', '暂停累积和上报新的点击，但仍保留桌宠敲击动画']
  ];

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
    });
    var report = document.createElement('div');
    report.id = 'wut-daily-report';
    report.innerHTML = '<div class="wut-report-title">昨日点击数</div>'
      + '<div><span class="wut-report-count">—</span>次</div>'
      + '<div class="wut-report-note"></div>'
      + '<details open><summary>最近 7 天 · 点击数</summary>'
      + '<div class="wut-report-chart" role="list" aria-label="最近七天点击数"></div></details>';
    host.appendChild(report);
    reportKey = '';
    var anchor = container.querySelector('.btn-check-update');
    container.insertBefore(host, anchor ? anchor.closest('.setting-item') :
      container.querySelector('.setting-btns'));
    render();
  }

  function render() {
    var host = document.getElementById(HOST);
    if (!host) return;
    var online = Date.now() - S.beat <= 5000;
    host.querySelectorAll('input[data-preference]').forEach(function (input) {
      var key = input.dataset.preference;
      var pending = S.requests.find(function (r) { return r.key === key; });
      input.checked = pending ? pending.value : S.preferences[key];
      input.disabled = !online || !!pending;
    });
    var status = host.querySelector('.wut-status');
    var text = !online ? 'Plus 后台未连接，请安装或修复 Plus' :
      error ? error : S.panelError ? '主窗口保持打开功能暂不可用' :
      S.requests.length ? '正在保存…' : saved ? '已保存 · 即时生效' : '即时生效 · 自动保存';
    if (status.textContent !== text) status.textContent = text;
    status.dataset.error = error || S.panelError ? 'true' : 'false';
    renderReport();
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
    S.report = state.report || null;
    keepPanelOpen();
    (state.ack || []).forEach(function (ack) {
      S.requests = S.requests.filter(function (r) { return r.id !== ack.id; });
      if (ack.error) { error = '保存失败，已恢复原设置'; saved = false; }
      else if (!error) saved = true;
    });
    mount(); render();
  };
  var observer = new MutationObserver(mount);
  observer.observe(document.body, { childList: true, subtree: true });
  var timer = setInterval(function () { mount(); render(); }, 1000);
  function refreshPanelBehavior() {
    nativePinned = null;
    if (S.beat) keepPanelOpen();
  }
  window.addEventListener('focus', refreshPanelBehavior);
  S.uninstall = function () {
    disposed = true;
    observer.disconnect(); clearInterval(timer);
    window.removeEventListener('focus', refreshPanelBehavior);
    // Restore the app's original behavior when Plus removes its injection.
    try {
      Promise.resolve(window.__TAURI_INTERNALS__.invoke(
        'set_skip_panel_outside_click_close', { skip: false }
      )).catch(function () {});
    } catch (e) {}
    [HOST, STYLE].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.remove();
    });
    delete window.__WUT_SETTINGS__;
  };
  mount();
  return 'installed';
})()
