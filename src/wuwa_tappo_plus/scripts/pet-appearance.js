  function createPetAppearance(CFG) {
    // Local accessory transforms, game opacity and room-pet mirroring.
    var accessoryState = { file: '', base: null, applied: '' };
    var accessoryObserver = null;
    var accessoryLayer = null;

    function observeAccessory(layer) {
      if (accessoryLayer === layer && accessoryObserver) return;
      if (accessoryObserver) accessoryObserver.disconnect();
      accessoryLayer = layer;
      accessoryObserver = new MutationObserver(function () { applyAccessoryOverride(); });
      accessoryObserver.observe(layer, { attributes: true, attributeFilter: ['style', 'src'] });
    }

    function accessoryFile(url) {
      var parts = String(url || '').split('?')[0].split('/');
      return (parts[parts.length - 1] || '').toLowerCase();
    }

    function translateOf(style) {
      var match = /translate\(\s*(-?[\d.]+)px\s*,\s*(-?[\d.]+)px\s*\)/.exec(String(style || ''));
      return match ? { x: parseFloat(match[1]), y: parseFloat(match[2]) } : { x: 0, y: 0 };
    }

    // The app positions the accessory with the character's own offset; these local
    // overrides add to it and only change what this computer draws.
    function applyAccessoryOverride() {
      var layer = document.querySelector('.cat-stack__layer');
      if (!layer) {
        if (accessoryObserver) { accessoryObserver.disconnect(); accessoryObserver = null; }
        accessoryLayer = null;
        window.__WUT_ACCESSORY_BASE__ = null;
        accessoryState = { file: '', base: null, applied: '' };
        return;
      }
      // The app rewrites the layer's inline transform several times a second, so
      // watching it is the only way the custom transform survives.
      observeAccessory(layer);
      var overrides = window.__WUT_ACCESSORY_OVERRIDES__ || {};
      var file = accessoryFile(layer.src);
      var override = overrides[file];
      var current = String(layer.style.transform || '');
      if (!override) {
        if (accessoryState.applied && current === accessoryState.applied) {
          layer.style.transform = accessoryState.base || '';
        }
        var plain = translateOf(current);
        window.__WUT_ACCESSORY_BASE__ = { file: file, x: plain.x, y: plain.y };
        accessoryState = { file: '', base: null, applied: '' };
        return;
      }
      var base;
      if (accessoryState.file === file && accessoryState.applied &&
          current === accessoryState.applied) {
        base = accessoryState.base || '';
      } else {
        // The app rewrote the layer (character/accessory change): adopt its base.
        base = current;
        accessoryState.file = file;
        accessoryState.base = base;
      }
      var translate = translateOf(base);
      window.__WUT_ACCESSORY_BASE__ = { file: file, x: translate.x, y: translate.y };
      var x = translate.x + Number(override.x || 0);
      var y = translate.y + Number(override.y || 0);
      var scale = Number(override.scale || 1);
      var rotation = Number(override.rotation || 0);
      var next = 'translate(' + x + 'px, ' + y + 'px)';
      if (scale !== 1) next += ' scale(' + scale + ')';
      if (rotation) next += ' rotate(' + rotation + 'deg)';
      if (current !== next) layer.style.transform = next;
      accessoryState.applied = next;
    }

    function gameModeEnabled() {
      try {
        var settings = JSON.parse(localStorage.getItem('wuwatappo-settings') || '{}');
        return !!settings && settings.gameMode === true;
      } catch (e) {
        return false;
      }
    }

    function gameModeOpacityFrom(value) {
      var number = Number(value);
      if (!isFinite(number)) number = 30;
      return Math.max(0, Math.min(100, Math.round(number)));
    }

    function gameModeOpacity() {
      return gameModeOpacityFrom(CFG.game_mode_opacity);
    }

    // The pet is drawn inside .pet-dock; its normal opacity comes from the app's
    // --window-opacity CSS variable and hover rule. While game mode is active an
    // inline opacity overrides both; removing it restores the app's own setting.
    var previewGameOpacity = null;
    var previewUntil = 0;
    var gamePreviewEventId = null;
    var bossTimer = 0;

    function applyGameOpacity(target, value) {
      if (!target || !target.dataset || !target.style) return;
      if (value !== null) {
        var desired = String(value / 100);
        if (target.dataset.wutGameOpacity !== desired) {
          if (!target.dataset.wutGameOpacity) {
            target.dataset.wutBaseOpacity = target.style.opacity || '';
            target.dataset.wutBaseTransition = target.style.transition || '';
          }
          if (target.classList && target.classList.contains('room-cat-shell') &&
              !target.style.transition) {
            target.style.transition = 'opacity .35s ease';
          }
          target.style.opacity = desired;
          target.dataset.wutGameOpacity = desired;
        }
      } else if (target.dataset.wutGameOpacity) {
        target.style.opacity = target.dataset.wutBaseOpacity || '';
        target.style.transition = target.dataset.wutBaseTransition || '';
        delete target.dataset.wutGameOpacity;
        delete target.dataset.wutBaseOpacity;
        delete target.dataset.wutBaseTransition;
      }
    }

    function syncGameModeOpacity() {
      if (previewGameOpacity !== null && Date.now() > previewUntil) previewGameOpacity = null;
      var mode = gameModeEnabled();
      var value = previewGameOpacity !== null ? previewGameOpacity :
        (mode ? gameModeOpacity() : null);
      // The room container itself must not be dimmed: every .room-cat-shell
      // inside it already carries the app's own opacity, so targeting the
      // container multiplied the two and made friends' pets dimmer than ours.
      var room = document.querySelector('.room-cats-shell');
      if (room && room.dataset && room.style && room.dataset.wutGameOpacity) {
        room.style.opacity = room.dataset.wutBaseOpacity || '';
        if (room.style.transition === 'opacity .35s ease') room.style.transition = '';
        delete room.dataset.wutGameOpacity;
        delete room.dataset.wutBaseOpacity;
      }
      applyGameOpacity(document.querySelector('.pet-dock'), value);
      var cats = document.querySelectorAll('.room-cat-shell');
      for (var i = 0; i < cats.length; i += 1) applyGameOpacity(cats[i], value);
    }

    function mirrorBossPet(visual, mirrored) {
      if (!visual || !visual.dataset || !visual.style) return;
      if (mirrored) {
        if (visual.dataset.wutBossScale === undefined) {
          visual.dataset.wutBossScale = visual.style.scale || '';
        }
        if (visual.dataset.wutBossZ === undefined) {
          visual.dataset.wutBossZ = visual.style.zIndex || '';
        }
        if (visual.style.scale !== '-1 1') visual.style.scale = '-1 1';
        // Lift the mirrored pet above the ID pill without moving it: z-index
        // keeps both the pet and the name visible.
        if (visual.style.zIndex !== '1') visual.style.zIndex = '1';
      } else {
        if (visual.dataset.wutBossScale !== undefined) {
          visual.style.scale = visual.dataset.wutBossScale;
          delete visual.dataset.wutBossScale;
        }
        if (visual.dataset.wutBossZ !== undefined) {
          visual.style.zIndex = visual.dataset.wutBossZ;
          delete visual.dataset.wutBossZ;
        }
      }
    }

    function myPetCenter() {
      var pet = document.querySelector('.pet-dock .cat-wrap') ||
        document.querySelector('.pet-dock');
      if (!pet) return null;
      var rect = pet.getBoundingClientRect();
      if (!rect || (rect.width === 0 && rect.height === 0)) return null;
      return rect.left + rect.width / 2;
    }

    // "王总好": every pet faces left. Mirror the ones left of the user's pet so
    // they face back, while their nickname and emotion layer stay unmirrored.
    function syncBossMode() {
      var cats = document.querySelectorAll('.room-cat-shell');
      var center = CFG.hello_boss === true ? myPetCenter() : null;
      for (var i = 0; i < cats.length; i += 1) {
        var visual = cats[i].querySelector ? cats[i].querySelector('.cat-friend') : null;
        if (!visual) continue;
        var mirrored = false;
        if (center !== null) {
          var rect = visual.getBoundingClientRect ? visual.getBoundingClientRect() : null;
          if (rect && rect.width) mirrored = rect.left + rect.width / 2 < center;
        }
        mirrorBossPet(visual, mirrored);
      }
      ensureBossTimer(cats.length > 0);
    }

    function ensureBossTimer(hasCats) {
      var active = CFG.hello_boss === true && hasCats;
      if (active && !bossTimer) {
        bossTimer = setInterval(syncBossMode, 50);
      } else if (!active && bossTimer) {
        clearInterval(bossTimer);
        bossTimer = 0;
      }
    }

    // The settings page sends this while the slider is dragged so the pet previews
    // the value without waiting for the config round trip. Payload: {value, done}.
    function installGameOpacityPreview() {
      if (!window.__TAURI_INTERNALS__ || !window.__TAURI_INTERNALS__.invoke ||
          !window.__TAURI_INTERNALS__.transformCallback) return;
      try {
        var handler = window.__TAURI_INTERNALS__.transformCallback(function (event) {
          var payload = event && event.payload || {};
          if (payload.done === true) {
            previewGameOpacity = null;
          } else {
            var value = Number(payload.value);
            if (!isFinite(value)) return;
            previewGameOpacity = Math.max(0, Math.min(100, Math.round(value)));
            previewUntil = Date.now() + 8000;
          }
          syncGameModeOpacity();
        });
        Promise.resolve(window.__TAURI_INTERNALS__.invoke('plugin:event|listen', {
          event: 'wut-game-opacity-preview',
          target: { kind: 'AnyLabel', label: 'main' },
          handler: handler
        })).then(function (id) {
          gamePreviewEventId = id;
        }).catch(function () {});
      } catch (e) {}
    }

    return { sync: function () {
      applyAccessoryOverride(); syncGameModeOpacity(); syncBossMode();
    }, syncMode: function () {
      syncGameModeOpacity(); syncBossMode();
    }, normalizeOpacity: gameModeOpacityFrom,
    installPreview: installGameOpacityPreview,
    dispose: function () {
      if (accessoryObserver) { accessoryObserver.disconnect(); accessoryObserver = null; }
      accessoryLayer = null;
      if (gamePreviewEventId != null && window.__TAURI_INTERNALS__) {
        try {
          Promise.resolve(window.__TAURI_INTERNALS__.invoke('plugin:event|unlisten', {
            event: 'wut-game-opacity-preview',
            eventId: gamePreviewEventId
          })).catch(function () {});
        } catch (e) {}
      }
      gamePreviewEventId = null;
      CFG.hello_boss = false;
      syncBossMode();
      if (bossTimer) { clearInterval(bossTimer); bossTimer = 0; }
    } };
  }
