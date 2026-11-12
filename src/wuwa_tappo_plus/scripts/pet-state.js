  function createPetState(initialCache, isCurrent, note) {
    // Use the native getter that drives both the displayed wallet and chest
    // affordability. Raw userInfo counters omit local clicks/spending. The DOM
    // reader is a fallback for missing, invalid or inaccessible store values.
    var balNode = null;

    function walletNumber(text) {
      var value = String(text || '').trim();
      if (!/^[0-9]+$/.test(value)) return null;
      var number = Number(value);
      return Number.isSafeInteger(number) ? number : null;
    }

    function barBalance() {
      try {
        var app = document.querySelector('#app');
        var vue = app && app.__vue_app__;
        var pinia = vue && vue.config && vue.config.globalProperties.$pinia;
        var store = pinia && pinia._s && pinia._s.get('GlobalState');
        var balance = store && store.totalClickCount;
        if (Number.isSafeInteger(balance) && balance >= 0) return balance;
      } catch (e) { /* A changing page/store must not disable the DOM fallback. */ }
      if (!balNode || !balNode.isConnected) {
        balNode = null;
        var nodes = document.querySelectorAll('.text.web-bold-font');
        for (var i = 0; i < nodes.length; i++) {
          if (walletNumber(nodes[i].textContent) !== null) {
            balNode = nodes[i];
            break;
          }
        }
      }
      if (!balNode) return null;
      return walletNumber(balNode.textContent);
    }

    // Since official 1.0.2, opening a chest starts a component-local monotonic
    // timer. userInfo's timestamps can remain unchanged after a successful open.
    // The native slot UI is authoritative; hidden timers are unknown, not due.
    var chestUi = { left: { readyAt: 0 }, right: { readyAt: 0 } };

    // Confirm opens through the native Pinia action, which runs only after the
    // successful response starts its local cooldown. Never infer success from
    // a disappearing button or a wallet delta.
    var COOLDOWN_MS = 30 * 60 * 1000;
    var chestCache = initialCache || { account: null };
    var chestStore = null;
    var chestAccount = null;
    var stopChestAction = null;
    var pendingChest = null;

    function watchChests(store, account) {
      chestAccount = account;
      if (account && chestCache.account !== account) {
        chestCache = { account: account };
        pendingChest = null;
      }
      if (chestStore === store) return;
      if (stopChestAction) stopChestAction();
      chestStore = store;
      pendingChest = null;
      if (typeof store.$onAction !== 'function') return;
      stopChestAction = store.$onAction(function (action) {
        if (action.name !== 'applyChestClickConsume' || !pendingChest ||
            !(Number(action.args[0]) > 0)) return;
        var pending = pendingChest;
        action.after(function () {
          if (!isCurrent() || pendingChest !== pending ||
              !store.isLoggedIn || !pending.account ||
              String((store.$state.userInfo || {}).cuid || '') !== pending.account ||
              chestCache.account !== pending.account ||
              Date.now() - pending.at > 60000) return;
          var now = Date.now();
          chestCache[pending.slot] = { opened_at: now, ready_at: now + COOLDOWN_MS };
          pendingChest = null;
        });
      }, true);
    }

    function captureChest(event) {
      var target = event.target;
      var button = target && target.closest && target.closest('.reward-btn');
      if (!button || button.disabled || !chestStore || !chestStore.isLoggedIn || !chestAccount) return;
      var slot = button.closest('.reward-anchor--left') ? 'left'
        : button.closest('.reward-anchor--right') ? 'right' : null;
      if (slot) pendingChest = { slot: slot, at: Date.now(), account: chestCache.account };
    }
    if (document.addEventListener) document.addEventListener('click', captureChest, true);

    function chestButton(name) {
      return document.querySelector('.reward-anchor--' + name + ' .reward-btn');
    }

    // Both the confirmed-open cache and the profile carry a "ready at" time for a
    // slot. The later one reflects the most recent open we know about, so use it:
    // the other source may predate that open.
    function chestReadyAt(name, now, userInfo) {
      var times = [];
      var cached = chestCache && chestCache[name];
      if (chestAccount && cached && Number.isFinite(cached.ready_at) &&
          cached.ready_at > 0 && cached.ready_at <= now + COOLDOWN_MS) {
        times.push(cached.ready_at);
      }
      var field = userInfo && Number(userInfo[name + '_treasure_chest']);
      if (Number.isFinite(field) && field > 0) times.push(field);
      return times.length ? Math.max.apply(null, times) : 0;
    }

    function chestTime(name, now, loggedIn, userInfo) {
      var record = chestUi[name];
      if (loggedIn && chestButton(name)) {
        delete chestCache[name];
        if (!record.readyAt) record.readyAt = now;
        return record.readyAt;
      }
      record.readyAt = 0;
      if (!loggedIn) return 0;
      var text = document.querySelector('.reward-anchor--' + name
        + ' .reward-countdown__text');
      var match = text && /^(\d{1,3}):([0-5]\d)$/.exec((text.textContent || '').trim());
      if (!match) {
        // A past time is not a clickable chest: the native button is the only
        // thing that authorises a click.
        var ready = chestReadyAt(name, now, userInfo);
        return ready > now ? ready : 0;
      }
      var seconds = Number(match[1]) * 60 + Number(match[2]);
      var known = chestCache[name];
      if (known && Math.abs(known.ready_at - now - seconds * 1000) > 2500) {
        delete chestCache[name];
      }
      // 00:00 alone does not authorize a click; wait for the native button.
      return seconds > 0 ? now + seconds * 1000 : 0;
    }

    // A low wallet makes the page stop drawing the chest slots entirely, so both
    // timers read 0 and the bubble loses its second line exactly when the user
    // needs to know a chest is ready again. This hint only feeds the bubble; it
    // never authorises a click, which still needs the native button.
    function chestHint(name, now, userInfo) {
      if (chestButton(name)) return 0;
      if (document.querySelector('.reward-anchor--' + name + ' .reward-countdown__text')) return 0;
      var ready = chestReadyAt(name, now, userInfo);
      // The page only draws the slot again once the wallet recovers, and that
      // countdown then corrects this record. A day is just an upper bound for a
      // hint left over from an old session.
      return ready >= now - 86400000 ? ready : 0;
    }

    function snapshot() {
      try {
        var el = document.querySelector('#app');
        var app = el && el.__vue_app__;
        if (!app) return null;
        var p = app.config.globalProperties.$pinia;
        var store = p && p._s && p._s.get('GlobalState');
        if (!store) return null;
        var u = store.$state.userInfo || {};
        var account = store.isLoggedIn && u.cuid ? String(u.cuid) : null;
        watchChests(store, account);
        var now = Date.now();
        return {
          now: now,
          left: chestTime('left', now, store.isLoggedIn, u),
          right: chestTime('right', now, store.isLoggedIn, u),
          left_hint: chestHint('left', now, u),
          right_hint: chestHint('right', now, u),
          native_chests: true,
          click: u.total_click_count || 0,
          spend: u.total_click_consume || 0,
          // The pet's own 累计点击数: cumulativeClickCount on the store. It is the
          // number the settings page shows, and it includes clicks not synced yet
          // (total_click_count lags behind it).
          click_total: store.isLoggedIn && Number.isSafeInteger(store.cumulativeClickCount)
            ? store.cumulativeClickCount : null,
          click_account: store.isLoggedIn && u.cuid ? String(u.cuid) : null,
          bal: barBalance()
        };
      } catch (e) {
        note(e);
        return null;
      }
    }

    return { get cache() { return chestCache; }, set cache(value) { chestCache = value; },
      snapshot: snapshot, balance: barBalance, button: chestButton,
      dispose: function () {
        if (stopChestAction) stopChestAction();
        stopChestAction = null;
        if (document.removeEventListener) document.removeEventListener('click', captureChest, true);
        pendingChest = null;
      }
    };
  }
