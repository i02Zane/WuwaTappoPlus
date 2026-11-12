  function createPetReminders(CFG, notifications, note) {
    // ---- balance reminder ---------------------------------------------------
    // The page is a transparent full-screen overlay and the pet dock is scaled
    // by 0.75, so the bubble is positioned in screen coordinates from the pet's
    // own rect rather than nested inside the dock: scaling and stacking contexts
    // in there would shrink and clip it.
    var ALERT_ID = 'wut-alert-host';
    var STYLE_ID = 'wut-alert-style';
    var level = 0;
    var shownFor = '';
    var lastPlace = '';
    var bubble = null;
    var PET_GAP = 0;

    var ALERT_CSS = [
      '#wut-alert-host{position:fixed;z-index:2147483000;pointer-events:none;',
      'left:50%;bottom:110px;transform:translateX(-50%);display:none;}',

      '#wut-alert-host .wut-alert{position:relative;padding:5px 13px 6px;',
      'border-radius:16px;background:#fdf9ed;border:2px solid rgba(142,94,67,.22);',
      'box-shadow:0 4px 14px rgba(90,55,30,.22),inset 0 1px 0 #fff;color:#8e5e43;',
      'text-align:center;white-space:nowrap;',
      'font-family:"975MaruSC-Bold","975MaruSC","Microsoft YaHei",sans-serif;',
      'line-height:1.25;',
      'animation:wut-alert-pop .34s cubic-bezier(.2,1.7,.45,1) both,',
      'wut-alert-bob 1.5s ease-in-out .34s infinite;}',

      '#wut-alert-host .wut-alert .wut-main{display:block;font-size:14px;',
      'letter-spacing:.4px;font-weight:700;}',

      // The countdown line. Tabular figures keep the bubble from twitching as
      // the digits change every second, and :empty keeps it out of the layout
      // entirely on the tiers that have nothing to say.
      '#wut-alert-host .wut-alert .wut-cd{display:block;margin-top:1px;',
      'font-size:12px;font-weight:600;letter-spacing:.3px;opacity:.75;',
      'font-variant-numeric:tabular-nums;}',
      '#wut-alert-host .wut-alert .wut-cd:empty{display:none;}',

      '#wut-alert-host .wut-alert:after{content:"";position:absolute;left:50%;',
      'bottom:-9px;width:12px;height:12px;margin-left:-6px;background:#fdf9ed;',
      'border-right:2px solid rgba(142,94,67,.22);',
      'border-bottom:2px solid rgba(142,94,67,.22);',
      'border-radius:0 0 3px 0;transform:rotate(45deg);}',

      '#wut-alert-host .wut-alert--critical{background:#fdf1e4;color:#b4543c;',
      'border-color:rgba(180,84,60,.28);',
      'animation:wut-alert-pop .34s cubic-bezier(.2,1.7,.45,1) both,',
      'wut-alert-bob 1s ease-in-out .34s infinite;}',
      '#wut-alert-host .wut-alert--critical:after{background:#fdf1e4;',
      'border-color:rgba(180,84,60,.28);}',
      // A stalled auto-open, not a balance tier: same shape, different message.
      '#wut-alert-host .wut-alert--warn{background:#fdf3e3;color:#a86524;',
      'border-color:rgba(168,101,36,.3);',
      'animation:wut-alert-pop .34s cubic-bezier(.2,1.7,.45,1) both,',
      'wut-alert-bob 2s ease-in-out .34s infinite;}',
      '#wut-alert-host .wut-alert--warn:after{background:#fdf3e3;',
      'border-color:rgba(168,101,36,.3);}',
      '#wut-alert-host.wut-below .wut-alert:after{bottom:auto;top:-7px;',
      'transform:rotate(225deg);}',

      '@keyframes wut-alert-pop{from{opacity:0;transform:scale(.55) translateY(8px);}',
      'to{opacity:1;transform:scale(1) translateY(0);}}',
      '@keyframes wut-alert-bob{0%,100%{transform:translateY(0) scale(1);}',
      '35%{transform:translateY(-4px) scale(1.03);}',
      '60%{transform:translateY(-1px) scale(.995);}',
      '80%{transform:translateY(-2px) scale(1.01);}}'
    ].join('');

    // Tauri clips the native window to its own visual regions. Extend only that
    // list, leaving inputRegions and passthrough untouched. Both IPC transports
    // are covered; all unrelated requests pass through unchanged.
    var regionBridge = (function () {
      var active = false;
      var base = null;
      var oldFetch = null;
      var fetchHook = null;
      var oldPost = null;
      var postHook = null;
      var sending = false;
      var ownCallback = '';
      var pending = false;
      var lastKey = '';
      var reported = false;

      function visualRegion() {
        if (!bubble || bubble.host.style.display === 'none') return null;
        var r = bubble.host.getBoundingClientRect();
        if (!r.width || !r.height) return null;
        // Leave room for shadow, tail, bounce and the initial pop overshoot.
        var left = Math.max(0, Math.floor(r.left - 24));
        var top = Math.max(0, Math.floor(r.top - 24));
        var right = Math.min(window.innerWidth, Math.ceil(r.right + 24));
        var bottom = Math.min(window.innerHeight, Math.ceil(r.bottom + 24));
        return right > left && bottom > top ?
          { left: left, top: top, width: right - left, height: bottom - top } : null;
      }

      function expand(args) {
        if (!active || sending || !args || !args.payload ||
            !Array.isArray(args.payload.regions)) return args;
        base = Object.assign({}, args.payload, { regions: args.payload.regions.slice() });
        var extra = visualRegion();
        if (!extra || !base.regions.length) return args;
        return Object.assign({}, args, {
          payload: Object.assign({}, base, { regions: base.regions.concat([extra]) })
        });
      }

      function refresh() {
        if (!active || !base || pending) return;
        var extra = visualRegion();
        if (!base.regions.length) extra = null; // respect the app hiding itself
        var key = JSON.stringify(extra);
        if (key === lastKey) return;
        lastKey = key;
        var payload = Object.assign({}, base, {
          regions: extra ? base.regions.concat([extra]) : base.regions.slice(),
          inputRegions: [], updateNativeInput: false
        });
        pending = true;
        sending = true;
        var request;
        try {
          request = window.__TAURI_INTERNALS__.invoke('set_main_hit_region', { payload: payload });
        } catch (e) {
          request = Promise.reject(e);
        }
        sending = false;
        Promise.resolve(request).catch(function (e) {
          if (!reported) { reported = true; note('bubble region: ' + e); }
        }).then(function () { ownCallback = ''; pending = false; if (active) refresh(); });
      }

      function start() {
        if (active || !window.__TAURI_INTERNALS__) return;
        active = true;
        oldFetch = window.fetch;
        fetchHook = function (url, options) {
          var next = options;
          try {
            var u = typeof url === 'string' ? new URL(url) : null;
            if (active && u && (u.hostname === 'ipc.localhost' ||
                (u.protocol === 'ipc:' && u.hostname === 'localhost')) &&
                u.pathname === '/set_main_hit_region' && options &&
                typeof options.body === 'string') {
              if (sending && options.headers && typeof options.headers.get === 'function') {
                ownCallback = String(options.headers.get('Tauri-Callback'));
              }
              var args = JSON.parse(options.body);
              var changed = expand(args);
              if (changed !== args) next = Object.assign({}, options, { body: JSON.stringify(changed) });
            }
          } catch (e) { /* Unknown transport shape: leave the app request intact. */ }
          return oldFetch.call(this, url, next);
        };
        window.fetch = fetchHook;
        if (window.ipc && typeof window.ipc.postMessage === 'function') {
          oldPost = window.ipc.postMessage;
          postHook = function (data) {
            var next = data;
            try {
              var message = JSON.parse(data);
              if (message.cmd === 'set_main_hit_region' && String(message.callback) !== ownCallback) {
                var changed = expand(message.payload);
                if (changed !== message.payload) {
                  next = JSON.stringify(Object.assign({}, message, { payload: changed }));
                }
              }
            } catch (e) { /* Preserve unrelated or non-JSON IPC messages. */ }
            return oldPost.call(this, next);
          };
          try { window.ipc.postMessage = postHook; } catch (e) {}
        }
        // Ask the app to publish its original regions; do not invent replacements
        // for its pet, room members, menus or panels.
        window.dispatchEvent(new Event('resize'));
      }

      function stop() {
        if (!active) return;
        active = false;
        if (window.fetch === fetchHook) window.fetch = oldFetch;
        if (window.ipc && window.ipc.postMessage === postHook) window.ipc.postMessage = oldPost;
        if (base) {
          var payload = Object.assign({}, base, { inputRegions: [], updateNativeInput: false });
          try {
            Promise.resolve(window.__TAURI_INTERNALS__.invoke(
              'set_main_hit_region', { payload: payload }
            )).catch(function () {});
          } catch (e) {}
        }
        base = null;
        lastKey = '';
      }

      return { start: start, refresh: refresh, stop: stop };
    })();

    function buildBubble() {
      if (!document.getElementById(STYLE_ID)) {
        var st = document.createElement('style');
        st.id = STYLE_ID;
        st.textContent = ALERT_CSS;
        (document.head || document.documentElement).appendChild(st);
      }
      var host = document.createElement('div');
      host.id = ALERT_ID;
      host.innerHTML = '<div class="wut-alert">'
        + '<span class="wut-main">0</span>'
        + '<span class="wut-cd"></span></div>';
      document.body.appendChild(host);
      bubble = { host: host, box: host.firstChild,
                 msg: host.querySelector('.wut-main'),
                 cd: host.querySelector('.wut-cd') };
      regionBridge.start();
      return bubble;
    }

    function dropBubble() {
      level = 0;
      notifications.setAlert(0);
      shownFor = '';
      lastPlace = '';
      bubble = null;
      stopFollow();
      var el = document.getElementById(ALERT_ID);
      if (el) el.parentNode.removeChild(el);
      var st = document.getElementById(STYLE_ID);
      if (st) st.parentNode.removeChild(st);
      regionBridge.stop();
    }

    // The pet is moved by a transform the page updates on every pointermove, and
    // the 100ms polling loop is far too coarse for that: the bubble would trail
    // the pet by up to a tenth of a second while dragging. A frame loop keeps it
    // glued to the pet for as long as it is on screen.
    var followRaf = 0;

    function followFrame() {
      followRaf = 0;
      if (!bubble) return;
      if (!placeBubble()) {
        if (bubble.host.style.display !== 'none') bubble.host.style.display = 'none';
        regionBridge.refresh();
        return;          // restarted by the next poll if the pet comes back
      }
      if (bubble.host.style.display !== 'block') bubble.host.style.display = 'block';
      regionBridge.refresh();
      followRaf = requestAnimationFrame(followFrame);
    }

    function startFollow() {
      if (!followRaf && bubble) followRaf = requestAnimationFrame(followFrame);
    }

    function stopFollow() {
      if (followRaf) {
        cancelAnimationFrame(followRaf);
        followRaf = 0;
      }
    }

    function placeBubble() {
      if (!bubble) return false;
      var pet = document.querySelector('.pet-dock .cat-wrap')
        || document.querySelector('.pet-dock');
      if (!pet) return false;
      var r = pet.getBoundingClientRect();
      // Only hide when the pet is really gone. A high threshold here used to
      // hide the bubble as soon as the pet was dragged into the top half of the
      // screen, which looked like the bubble refusing to follow.
      if (r.width < 4 || r.height < 4 || r.bottom < 20) return false;
      var half = (bubble.box.offsetWidth || 150) / 2 + 8;
      var left = Math.round(Math.max(half, Math.min(window.innerWidth - half, r.left + r.width / 2)));
      var h = bubble.box.offsetHeight || 56;
      var below = r.top < h + PET_GAP + 8;
      var top = below ? Math.min(window.innerHeight - h - 8, r.bottom + PET_GAP) : r.top - PET_GAP - h;
      var bottom = Math.round(window.innerHeight - top - h);
      var key = left + 'x' + bottom + 'x' + below;
      if (key !== lastPlace) {
        lastPlace = key;
        bubble.host.style.left = left + 'px';
        bubble.host.style.bottom = bottom + 'px';
        bubble.host.className = below ? 'wut-below' : '';
      }
      return true;
    }

    // Hidden native timers are unknown. In particular, a low wallet can hide
    // both slots; do not invent a countdown from stale account timestamps.
    function fmtCd(ms) {
      if (!(ms > 0)) return '';
      var s = Math.ceil(ms / 1000);
      var m = Math.floor(s / 60);
      var h = Math.floor(m / 60);
      s %= 60;
      m %= 60;
      var p = function (n) { return n < 10 ? '0' + n : '' + n; };
      return h > 0 ? h + ':' + p(m) + ':' + p(s) : m + ':' + p(s);
    }

    // Which slot the bubble quotes depends on the tier, because the bubble is
    // there to say what the page is not already saying:
    //
    //   under 1000  the page draws no chest at all, so quote the EARLIEST slot -
    //               that is the next thing that can happen.
    //   1000..2000   the page is already showing the first slot, so quote the
    //               LATER one and answer "what comes after that".
    //
    // Only visible native countdowns and ready buttons supply times. Hidden
    // slots are unknown and must not reuse the legacy store timestamps.
    function slotWait(s, later) {
      var list = [];
      if (s.left > 0) list.push(s.left - s.now);
      if (s.right > 0) list.push(s.right - s.now);
      if (!list.length) return null;          // no slot at all
      list.sort(function (a, b) { return a - b; });
      return later ? list[list.length - 1] : list[0];
    }

    // The only balance reader: native totalClickCount first, rendered wallet as
    // fallback. Bubble, click gate, payload and CLI all share this value.
    // A failure the user has to hear about: reuses the reminder bubble so the
    // native hit region and the pet tracking keep working unchanged.
    function showNotice(notice) {
      if (!bubble) buildBubble();
      if (level !== -1) {
        bubble.box.className = 'wut-alert wut-alert--warn';
        bubble.box.style.animation = 'none';
        void bubble.box.offsetWidth;
        bubble.box.style.animation = CFG.animation === false ? 'none' : '';
        level = -1;                       // notices sit outside the balance tiers
      }
      var key = 'notice|' + notice.text + '|' + notice.sub;
      if (shownFor !== key) {
        shownFor = key;
        bubble.msg.textContent = notice.text;
        bubble.cd.textContent = notice.sub || '';
      }
      bubble.host.style.display = placeBubble() ? 'block' : 'none';
      regionBridge.refresh();
      startFollow();
    }

    function updateAlert(balance, snap) {
      var low = CFG.alert_low || 0;
      var crit = CFG.alert_critical || 0;
      var want = 0;
      if (CFG.balance_reminder !== false && balance !== null) {
        if (crit && balance < crit) want = 2;
        else if (low && balance < low) want = 1;
      }

      var notice = notifications.notice();
      if (notice && Date.now() >= notice.until) { notifications.clearNotice(); notice = null; }

      // Bubble visibility is global; balance reminders and mode text have
      // separate content switches. Keep reporting the balance tier when hidden.
      if (CFG.bubble === false) {
        if (bubble || level) dropBubble();
        notifications.setAlert(want);
        return;
      }
      if (CFG.pause_clicks) {
        showNotice({ text: '库爹我错了，我再也不刷点击数了T T', sub: '' });
        notifications.setAlert(want);
        return;
      }
      // A failure notice outranks the balance tiers until it expires; the switch
      // above still wins, so turning bubbles off also turns these off.
      if (notice) {
        showNotice(notice);
        notifications.setAlert(0);
        return;
      }
      if (!want) {
        if (bubble || level) dropBubble();
        notifications.setAlert(0);
        return;
      }

      if (level !== want) {
        if (!bubble) buildBubble();
        bubble.box.className = 'wut-alert' + (want === 2 ? ' wut-alert--critical' : '');
        // Restart the hop so a tier change is noticed even if the box was
        // already up from the previous tier.
        bubble.box.style.animation = 'none';
        void bubble.box.offsetWidth;
        bubble.box.style.animation = CFG.animation === false ? 'none' : '';
        level = want;
        notifications.setAlert(want);
      }
      if (bubble) {
        // The bar under the pet already carries the exact number, so the bubble
        // says what it means for the clicker instead of repeating it.
        var text;
        if (want === 2) {
          text = '不够开宝箱了';
        } else {
          var boxes = Math.floor(balance / 1000);
          text = boxes < 1 ? '不够开宝箱了' : '只够开 ' + boxes + ' 个宝箱了';
        }
        // Second line, three states: a refresh countdown, "宝箱已刷新" when the
        // quoted slot is due and only the balance is keeping it shut, and nothing
        // at all when there is no slot to talk about (:empty drops the line,
        // leaving a one-line bubble).
        var cd = '';
        if (snap) {
          // Hidden slots fall back to the local cache so a ready chest is still
          // announced while the balance is too low to draw it.
          var known = {
            now: snap.now,
            left: snap.left > 0 ? snap.left : (snap.left_hint || 0),
            right: snap.right > 0 ? snap.right : (snap.right_hint || 0)
          };
          var wait = slotWait(known, want === 1);
          if (wait !== null) {
            cd = wait > 0 ? '宝箱 ' + fmtCd(wait) + ' 后刷新' : '宝箱已刷新';
          }
        }
        var key = text + '|' + cd;
        if (shownFor !== key) {
          shownFor = key;
          if (bubble.msg.textContent !== text) bubble.msg.textContent = text;
          if (bubble.cd.textContent !== cd) bubble.cd.textContent = cd;
        }
        bubble.host.style.display = placeBubble() ? 'block' : 'none';
        regionBridge.refresh();
        // Paint once right away, then let the frame loop keep it on the pet.
        startFollow();
      }
    }

    return { update: updateAlert, drop: dropBubble, configure: function (wasPaused, wasReminder, balance) {
      if (!CFG.bubble && bubble) dropBubble();
      if (bubble) bubble.box.style.animation = CFG.animation ? '' : 'none';
      if (wasPaused !== CFG.pause_clicks || CFG.pause_clicks ||
          wasReminder !== CFG.balance_reminder) updateAlert(balance, null);
    } };
  }
