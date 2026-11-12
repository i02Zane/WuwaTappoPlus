  function createAtlasView(VERSION, access) {
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

    // The app stores the accessory anchor in layout units (the 200px cat
    // canvas); on screen everything is multiplied by the pet size factor.
    function accessoryOffset(baseX, baseY, ov, petScale) {
      var factor = Number(petScale) > 0 ? Number(petScale) : 1;
      return { x: (Number(baseX) + Number(ov.x || 0)) * factor,
               y: (Number(baseY) + Number(ov.y || 0)) * factor };
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
      var petBase = access.pet() && access.pet().file === key ? access.pet() : null;
      var size = petBase && Number(petBase.size) > 0 ? Number(petBase.size) : 200;
      var layout = petBase && Number(petBase.layout) > 0 ? Number(petBase.layout) : 0;
      var petScale = layout > 0 ? size / layout : 1;
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
      var saved = (access.overrides() && access.overrides()[key]) || null;
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
        var offset = accessoryOffset(baseX, baseY, ov, petScale);
        accessory.style.transform = 'translate(-50%,-50%) translate(' +
          offset.x + 'px, ' + offset.y + 'px)' +
          ' scale(' + ov.scale + ') rotate(' + ov.rotation + 'deg)';
      }
      apply();
      stage.appendChild(accessory);
      noteOf().textContent = '';
      accessoryControls(key, ov, apply);
      if (modal) {
        modal.__wutAccessory = { key: key, setBase: function (x, y, nextSize, nextLayout) {
          baseX = Number(x); baseY = Number(y);
          var wantLayout = Number(nextLayout);
          if (wantLayout > 0 && wantLayout !== layout) layout = wantLayout;
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
          petScale = layout > 0 ? size / layout : 1;
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
        var pending = access.pending(key);
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
      access.save({ action: 'save_accessory',
                        file: key, x: ov.x, y: ov.y, scale: ov.scale, rotation: ov.rotation });
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
      var file = access.pet() && access.pet().file;
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
      var file = access.pet() && access.pet().file;
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
               accessoryOffset: accessoryOffset,
               openPreview: openPreview, openItemPreview: openItemPreview,
               openEquippedAccessory: openEquippedAccessory },
      sync: function () {
        var live = document.getElementById('wut-atlas-modal');
        if (live && live.__wutAccessory && access.pet() &&
            access.pet().file === live.__wutAccessory.key) {
          live.__wutAccessory.setBase(access.pet().x, access.pet().y,
                                      access.pet().size, access.pet().layout);
        }
      },
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
