(function () {
  'use strict';
  var VERSION = __VERSION__;
  if (window.__WUT_GAME_MODE__ && window.__WUT_GAME_MODE__.version === VERSION) return;
  if (window.__WUT_GAME_MODE__) window.__WUT_GAME_MODE__.dispose();
  var KEY = 'wut-plus-game-mode-owner';
  var LEGACY = 'wut-plus-auto-fullscreen-backup';
  var native = null;
  var loading = null;

  async function loadNative() {
    if (native) return native;
    if (!loading) {
      // Import the app's existing entry module, never a hardcoded asset hash.
      var script = document.querySelector('script[type="module"][src]');
      if (!script) throw new Error('native settings module missing');
      var url = new URL(script.src, location.href);
      if (url.origin !== location.origin || !url.pathname.startsWith('/assets/')) {
        throw new Error('unexpected native settings module');
      }
      loading = import(url.href).then(function (module) {
        var values = Object.values(module);
        function named(name) {
          var fn = values.find(function (value) {
            return typeof value === 'function' && value.name === name;
          });
          if (!fn) throw new Error('native settings API unavailable: ' + name);
          return fn;
        }
        var api = { load: named('loadDesktopCatSettings'),
                    persist: named('persistDesktopCatSettings'),
                    emit: named('emitSettingsGameMode') };
        var settings = api.load();
        if (typeof settings.gameMode !== 'boolean' ||
            typeof settings.autoGameModeOnFullscreen !== 'boolean') {
          throw new Error('native game mode settings unavailable');
        }
        native = api;
        return api;
      }).finally(function () { loading = null; });
    }
    return loading;
  }

  function ledger() {
    var raw = localStorage.getItem(KEY);
    if (raw === null) return null;
    var data = JSON.parse(raw);
    if (!data || ['original_auto', 'owned', 'active', 'suppressed', 'failed'].some(function (key) {
      return typeof data[key] !== 'boolean';
    }) || (data.pending !== null && (!data.pending ||
      typeof data.pending.target !== 'boolean' || !Number.isFinite(data.pending.at) ||
      typeof data.pending.confirmed !== 'boolean' || !Number.isInteger(data.pending.attempts) || data.pending.attempts < 1))) {
      throw new Error('invalid Plus game mode recovery data');
    }
    return data;
  }

  function clearEffective() {
    delete window.__WUT_EFFECTIVE_FULLSCREEN__;
  }

  async function setMode(value) {
    var api = await loadNative();
    // Use the same persistence + explicit enabled event as the native settings.
    // Repeating a value is safe even when a previous acknowledgement was lost.
    api.persist({ gameMode: value });
    await api.emit(value);
    var data = ledger();
    if (data && data.pending && data.pending.target === value) {
      data.pending.confirmed = true;
      localStorage.setItem(KEY, JSON.stringify(data));
    }
    return api.load().gameMode;
  }

  function restoreLocal(api, data, legacy) {
    var patch = {};
    if (data) {
      patch.autoGameModeOnFullscreen = data.original_auto;
      if (data.owned) patch.gameMode = false;
    } else if (legacy !== null) {
      patch.autoGameModeOnFullscreen = legacy === '1';
    }
    if (Object.keys(patch).length) {
      api.persist(patch);
      var restored = api.load();
      if (Object.keys(patch).some(function (key) { return restored[key] !== patch[key]; })) {
        throw new Error('native game mode restoration not confirmed');
      }
    }
    localStorage.removeItem(KEY);
    localStorage.removeItem(LEGACY);
    clearEffective();
  }

  var api = window.__WUT_GAME_MODE__ = {
    version: VERSION,
    read: async function () {
      var settings = (await loadNative()).load();
      var legacy = localStorage.getItem(LEGACY);
      clearEffective();
      return { mode: settings.gameMode, auto: settings.autoGameModeOnFullscreen,
               ledger: ledger(), legacy_auto: legacy === null ? null : legacy === '1' };
    },
    save: function (value) {
      localStorage.setItem(KEY, JSON.stringify(value));
      localStorage.removeItem(LEGACY);
      return true;
    },
    setAuto: async function (value) {
      var api = await loadNative();
      api.persist({ autoGameModeOnFullscreen: value });
      return api.load().autoGameModeOnFullscreen;
    },
    setMode: setMode,
    release: async function () {
      var data = ledger();
      var legacy = localStorage.getItem(LEGACY);
      clearEffective();
      if (data === null && legacy === null) return true;
      var api = await loadNative();
      if (data && data.owned) {
        if (await setMode(false) !== false) throw new Error('native game mode exit not confirmed');
      }
      restoreLocal(api, data, legacy);
      return true;
    },
    dispose: function () {
      window.removeEventListener('pagehide', onPageHide);
      delete window.__WUT_GAME_MODE__;
    }
  };
  function onPageHide() {
    // A closing WebView cannot await IPC. Restore persisted settings synchronously;
    // there is no visible pet left whose rendering would need an event.
    try { if (native) restoreLocal(native, ledger(), localStorage.getItem(LEGACY)); } catch (e) {}
  }
  window.addEventListener('pagehide', onPageHide);
})();
