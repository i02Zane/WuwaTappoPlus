(function () {
  var VERSION = __VERSION__;
  if (window.__WUT_SETTINGS__ && window.__WUT_SETTINGS__.version === VERSION) return 'exists';
  if (window.__WUT_SETTINGS__) window.__WUT_SETTINGS__.uninstall();

  var S = window.__WUT_SETTINGS__ = {
    version: VERSION, requests: [], beat: 0, report: null,
    exportPath: null, exportError: false,
    preferences: { auto_open: true, balance_reminder: true, bubble: true, animation: true,
                   remember_screen: true, auto_emote: false, pause_clicks: false }
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
    ['auto_emote', '自动发表情', '桌宠会跟随敲击自动发送一个已装备的表情，每秒最多发送20次'],
    ['balance_reminder', '余额提醒', '余额不足时提醒，并显示宝箱倒计时；不影响其他气泡。'],
    ['bubble', '气泡显示', '显示 Plus 气泡，包括余额提醒和小黑屋模式文字。'],
    ['animation', '气泡动画', '让气泡轻轻跳动；关闭后保留静态气泡。'],
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
    });
    var report = document.createElement('div');
    report.id = 'wut-daily-report';
    report.innerHTML = '<div class="wut-report-title">昨日点击数</div>'
      + '<div><span class="wut-report-count">—</span>次</div>'
      + '<div class="wut-report-note"></div>'
      + '<details open><summary>最近 7 天 · 点击数</summary>'
      + '<div class="wut-report-chart" role="list" aria-label="最近七天点击数"></div></details>';
    host.appendChild(report);
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
      error ? error :
      S.requests.some(function (r) { return !r.action; }) ? '正在保存…' : saved ? '已保存 · 即时生效' : '即时生效 · 自动保存';
    if (status.textContent !== text) status.textContent = text;
    status.dataset.error = error ? 'true' : 'false';
    renderReport();
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
    mount(); render();
  };
  function createAtlasView() {
    var styleId = 'wut-atlas-style';
    var catalog = null;
    var loading = null;
    var modal = null;
    var keyHandler = null;
    var blinkTimer = null;
    var tapTimers = [];
    var ATLAS_TYPES = { character_skin_atlas: 'character_skin',
                        accessory_atlas: 'accessory',
                        emote_atlas: 'emote' };
    var KIND = { character: '角色', accessory: '配件', emote: '表情' };

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
      style.textContent = '#wut-atlas-modal{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;background:rgba(46,29,19,.38);}'
        + '#wut-atlas-modal .wut-atlas-card{width:340px;max-width:calc(100vw - 32px);background:#fffaf3;border:1px solid rgba(142,94,67,.25);border-radius:16px;box-shadow:0 20px 50px rgba(60,35,20,.28);padding:16px 18px 14px;color:#5b4433;}'
        + '#wut-atlas-modal .wut-atlas-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;}'
        + '#wut-atlas-modal .wut-atlas-head strong{font-size:17px;color:#5b4433;}'
        + '#wut-atlas-modal .wut-atlas-kind{display:block;margin-top:2px;font-size:12px;color:#8e7b6a;}'
        + '#wut-atlas-modal .wut-atlas-close{border:0;background:transparent;color:#8e7b6a;font-size:22px;line-height:1;cursor:pointer;padding:0 2px;}'
        + '#wut-atlas-modal .wut-atlas-close:hover{color:#b4543c;}'
        + '#wut-atlas-modal .wut-atlas-stage{position:relative;width:280px;height:280px;margin:12px auto 6px;border-radius:14px;background:radial-gradient(circle at 50% 42%,#fff 0%,#fdf3e6 55%,#f6e4cf 100%);overflow:hidden;}'
        + '#wut-atlas-modal .wut-atlas-layer{position:absolute;left:50%;top:50%;width:280px;height:280px;transform:translate(-50%,-50%);pointer-events:none;user-select:none;}'
        + '#wut-atlas-modal .wut-atlas-stage--character{cursor:pointer;}'
        + '#wut-atlas-modal .wut-atlas-stage--character:active{transform:scale(.985);}'
        + '#wut-atlas-modal .wut-atlas-tap{animation:wut-atlas-tap .34s ease;}'
        + '@keyframes wut-atlas-tap{0%{transform:translateY(0) scale(1,1);}30%{transform:translateY(-9px) scale(.98,1.04);}55%{transform:translateY(0) scale(1.03,.97);}100%{transform:translateY(0) scale(1,1);}}'
        + '#wut-atlas-modal .wut-atlas-emote{display:block;max-width:250px;max-height:250px;margin:0 auto;}'
        + '#wut-atlas-modal .wut-atlas-note{margin-top:6px;font-size:12px;line-height:1.5;color:#8e7b6a;text-align:center;}'
        + '#wut-atlas-modal .wut-atlas-loading{display:flex;align-items:center;justify-content:center;height:280px;color:#8e7b6a;font-size:13px;}';
      document.head.appendChild(style);
    }

    function clearTimers() {
      if (blinkTimer) { clearInterval(blinkTimer); blinkTimer = null; }
      tapTimers.forEach(function (id) { clearTimeout(id); });
      tapTimers = [];
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
      modal.innerHTML = '<div class="wut-atlas-card">'
        + '<div class="wut-atlas-head"><div><strong></strong><span class="wut-atlas-kind">'
        + KIND[kind] + '预览 · 未获得</span></div>'
        + '<button type="button" class="wut-atlas-close" aria-label="关闭">×</button></div>'
        + '<div class="wut-atlas-stage"><div class="wut-atlas-loading">正在加载预览…</div></div>'
        + '<div class="wut-atlas-note"></div></div>';
      modal.querySelector('.wut-atlas-head strong').textContent = name || '未命名';
      modal.querySelector('.wut-atlas-close').addEventListener('click', close);
      modal.addEventListener('click', function (event) { if (event.target === modal) close(); });
      keyHandler = function (event) { if (event.key === 'Escape') close(); };
      document.addEventListener('keydown', keyHandler);
      document.body.appendChild(modal);
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
      place(image, layer, 0.5);
      return image;
    }

    function tapStage(stage, body, frames) {
      tapTimers.forEach(function (id) { clearTimeout(id); });
      tapTimers = [];
      stage.classList.remove('wut-atlas-tap');
      void stage.offsetWidth;
      stage.classList.add('wut-atlas-tap');
      body.src = frames.body2;
      tapTimers.push(setTimeout(function () { body.src = frames.body3; }, 110));
      tapTimers.push(setTimeout(function () { body.src = frames.body1; }, 220));
      tapTimers.push(setTimeout(function () { stage.classList.remove('wut-atlas-tap'); }, 360));
    }

    function characterMarkup(entry) {
      var stage = stageOf();
      stage.innerHTML = '';
      stage.classList.add('wut-atlas-stage--character');
      var body = addLayer('wut-atlas-body-layer', assetUrl(entry.body1 && entry.body1.url), entry.body1);
      var head = addLayer('wut-atlas-head-layer', assetUrl(entry.head1 && entry.head1.url), entry.head1);
      stage.appendChild(body);
      stage.appendChild(head);
      var frames = {
        body1: assetUrl(entry.body1 && entry.body1.url),
        body2: assetUrl((entry.body2 || entry.body1) && (entry.body2 || entry.body1).url),
        body3: assetUrl((entry.body3 || entry.body2 || entry.body1) &&
                        (entry.body3 || entry.body2 || entry.body1).url),
        head1: assetUrl(entry.head1 && entry.head1.url),
        head2: assetUrl((entry.head2 || entry.head1) && (entry.head2 || entry.head1).url)
      };
      stage.addEventListener('click', function () { tapStage(stage, body, frames); });
      if (frames.head2 && frames.head2 !== frames.head1) {
        blinkTimer = setInterval(function () {
          head.src = frames.head2;
          tapTimers.push(setTimeout(function () { head.src = frames.head1; }, 160));
        }, 3200);
      }
      noteOf().textContent = '点一下角色试试敲击，眨眼会自动播放。';
    }

    function accessoryMarkup(entry, current) {
      var stage = stageOf();
      stage.innerHTML = '';
      if (current) {
        stage.classList.add('wut-atlas-stage--character');
        var body = addLayer('wut-atlas-body-layer', assetUrl(current.body1 && current.body1.url), current.body1);
        var head = addLayer('wut-atlas-head-layer', assetUrl(current.head1 && current.head1.url), current.head1);
        stage.appendChild(body);
        stage.appendChild(head);
        var frames = {
          body1: assetUrl(current.body1 && current.body1.url),
          body2: assetUrl((current.body2 || current.body1) && (current.body2 || current.body1).url),
          body3: assetUrl((current.body3 || current.body2 || current.body1) &&
                          (current.body3 || current.body2 || current.body1).url)
        };
        stage.addEventListener('click', function () { tapStage(stage, body, frames); });
      }
      var accessory = addLayer('wut-atlas-accessory-layer', assetUrl(entry.url), entry);
      stage.appendChild(accessory);
      noteOf().textContent = current ? '已叠加到当前角色。' : '未找到当前角色，仅显示配件。';
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
      noteOf().textContent = '表情原样展示。';
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

    function onClick(event) {
      var target = event.target;
      var item = target && target.closest ? target.closest('.atlas-item--unowned') : null;
      if (!item || !item.closest('.atlas-module')) return;
      if (event.button && event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      var classes = item.className;
      var kind = /character_skin/.test(classes) ? 'character'
        : /accessory/.test(classes) ? 'accessory' : 'emote';
      var nameNode = item.querySelector('.atlas-name');
      var image = item.querySelector('.atlas-thumb img') || item.querySelector('img');
      if (!image || !image.src) return;
      openPreview(kind, nameNode ? nameNode.textContent.trim() : '', image.src);
    }

    document.addEventListener('click', onClick, true);
    window.__WUT_ATLAS__ = {
      version: VERSION,
      _test: { fileOf: fileOf, findAtlas: findAtlas, actualOf: actualOf,
               currentCharacter: currentCharacter, offsetOf: offsetOf,
               openPreview: openPreview },
      dispose: function () {
        document.removeEventListener('click', onClick, true);
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
