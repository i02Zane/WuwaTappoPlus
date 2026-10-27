(function () {
  var CFG = __CFG__;
  var V = CFG.version;

  // The chest timestamp reaches the page roughly one to two seconds before the
  // server accepts the open. Clicking the moment it turns due is answered with
  // "宝箱冷却中" (the app logs it as POST api/frontend/chests/open code=70006),
  // which is exactly the popup users see next to an automatic open. Wait a
  // fixed moment past due instead. Deliberately a constant, not a preference:
  // it describes the app, not the user.
  var READY_GRACE_MS = 2000;
  // The page needs a moment to draw the reward button after the countdown turns
  // due. Only call the button missing once that beat has clearly passed.
  var DRAW_GRACE_MS = 3000;
  // After a click, wait this long before touching the same chest again. Not a
  // setting: it is a property of how the app answers, and five attempts at this
  // spacing is about a minute of trying before the chest is parked.
  var LOCK_MS = 10000;
  // A chest that keeps refusing clicks is not going to open by retrying. Stop
  // and tell the user instead of clicking forever.
  var MAX_ATTEMPTS = 5;
  // How long the failure bubble stays up before normal reminders return.
  var NOTICE_MS = 15000;

  function newSlot() {
    return { ts: 0, attempts: 0, gaveUp: false, lastClickAt: 0 };
  }

  var old = window.__WUT__;
  if (old && old.v === V) return 'exists v' + V;

  // A previous version left its interval and observer running. Installing on
  // top of it would mean two loops fighting over the same button.
  if (old) {
    try {
      if (old.uninstall) old.uninstall();
      else { clearInterval(old.timer); if (old.obs) old.obs.disconnect(); }
    } catch (e) {}
    delete window.__WUT__;
  }

  var A = window.__WUT__ = {
    v: V,
    clicks: [],
    errors: [],
    btn: null,
    retries: 0,
    disabledHits: 0,
    lowBalance: null,
    // The daemon only records and logs; it does not gate the clicking. These
    // three describe the current state so the bubble and the payload can carry
    // it without the daemon recomputing anything.
    bal: null,
    alert: 0,
    orphan: false,
    // One independent record per chest. Opening one must never touch the
    // other's attempt count, lock or reported stall.
    slots: { left: newSlot(), right: newSlot() },
    // Why a due chest is not being clicked right now, or null: the blocking
    // state of the earliest due chest, shaped for the daemon's log line.
    block: null,
    // A failure the user must see: shown as a bubble for NOTICE_MS.
    notice: null,
    beat: Date.now(),
    installedAt: Date.now(),
    cfg: CFG
  };

  function note(msg) {
    A.errors.push({ t: Date.now(), m: String(msg) });
    if (A.errors.length > 20) A.errors.shift();
  }

  // The number under the pet is the real wallet. `total_click_count -
  // total_click_consume` only counts what the auto clicker did and ignores
  // everything else, so it drifts from the displayed value and lags behind it.
  // Read the rendered number instead: the bubble then always agrees with what
  // the user sees on screen. The node is cached because this runs every poll.
  var balNode = null;

  function barBalance() {
    if (!balNode || !balNode.isConnected) {
      balNode = null;
      var nodes = document.querySelectorAll('.text.web-bold-font');
      for (var i = 0; i < nodes.length; i++) {
        if (/^[0-9]{1,7}$/.test((nodes[i].textContent || '').trim())) {
          balNode = nodes[i];
          break;
        }
      }
    }
    if (!balNode) return null;
    var v = (balNode.textContent || '').trim();
    return /^[0-9]{1,7}$/.test(v) ? parseInt(v, 10) : null;
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
      return {
        now: Date.now(),
        left: u.left_treasure_chest || 0,
        right: u.right_treasure_chest || 0,
        click: u.total_click_count || 0,
        spend: u.total_click_consume || 0,
        input_total: store.isLoggedIn && Number.isSafeInteger(store.cumulativeClickCount)
          ? store.cumulativeClickCount : null,
        input_account: store.isLoggedIn && u.cuid ? String(u.cuid) : null,
        bal: barBalance()
      };
    } catch (e) {
      note(e);
      return null;
    }
  }

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
    A.alert = 0;
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

  // The page hides the chest timer completely once the balance cannot pay for
  // it: at 740 there is no `.reward-btn` in the DOM at all, and between 1000
  // and 2000 only one of the two slots is drawn. The two timestamps in the
  // store keep ticking regardless, so the bubble can still tell you when the
  // next chest lands - which is exactly the moment you would want to top up.
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
  // A slot keeps its past timestamp until it is actually opened (the left one
  // sat 267s overdue during testing), so a non-positive value means "the chest
  // is sitting there waiting for money", not "waiting for time".
  function slotWait(s, later) {
    var list = [];
    if (s.left > 0) list.push(s.left - s.now);
    if (s.right > 0) list.push(s.right - s.now);
    if (!list.length) return null;          // no slot at all
    list.sort(function (a, b) { return a - b; });
    return later ? list[list.length - 1] : list[0];
  }

  // The only balance the project uses. `bal` is the number rendered under the
  // pet; every consumer (bubble, click gate, payload, CLI) reads this one value
  // instead of recomputing it from the click counters, which track only what the
  // auto clicker did and therefore drift from the real wallet.
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
    if (balance !== null) {
      if (crit && balance < crit) want = 2;
      else if (low && balance < low) want = 1;
    }

    if (A.notice && Date.now() >= A.notice.until) A.notice = null;

    if (CFG.bubble === false) {
      if (bubble || level) dropBubble();
      A.alert = want;
      return;
    }
    // A failure notice outranks the balance tiers until it expires; the switch
    // above still wins, so turning bubbles off also turns these off.
    if (A.notice) {
      showNotice(A.notice);
      A.alert = 0;
      return;
    }
    if (!want) {
      if (bubble || level) dropBubble();
      A.alert = 0;
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
      A.alert = want;
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
        var wait = slotWait(snap, want === 1);
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

  // Record why a chest that is already due is not being clicked. The identity
  // is the slot plus its timestamp, so a stall is one line per chest and the
  // other chest's events cannot rewrite it.
  function setBlock(slot, reason, ts, balance, attempts) {
    var b = A.block;
    if (b && b.slot === slot && b.reason === reason && b.ts === ts) {
      b.balance = balance;
      return;
    }
    A.block = { slot: slot, reason: reason, ts: ts, since: Date.now(),
                balance: balance, attempts: attempts || 0 };
  }

  function clearBlock() {
    if (A.block) A.block = null;
  }

  function step() {
    // The daemon stamps A.beat on every poll. Losing it does not stop the
    // clicking: every decision is made in this page, the daemon only records
    // and logs. The flag stays as a diagnostic bit; nothing renders it.
    A.orphan = CFG.stale_ms > 0 && Date.now() - A.beat > CFG.stale_ms;

    var s = (A.snapshot || snapshot)();
    if (!s) return;

    var balance = (s.bal === null || s.bal === undefined) ? null : s.bal;
    A.bal = balance;
    // Unconditional: the tier is a property of the balance, not of whether a
    // chest happens to exist. Gating this on a slot used to freeze the bubble
    // on its last text when both slots were empty.
    updateAlert(balance, s);

    // Each chest is its own record: its timestamp moving means the app opened
    // it, which resets that slot and nothing else.
    var now = Date.now();
    var pairs = [{ name: 'left', ts: s.left > 0 ? s.left : 0 },
                 { name: 'right', ts: s.right > 0 ? s.right : 0 }];
    for (var i = 0; i < pairs.length; i++) {
      var record = A.slots[pairs[i].name];
      if (record.ts === pairs[i].ts) continue;
      record.ts = pairs[i].ts;
      record.attempts = 0;
      record.gaveUp = false;
      record.lastClickAt = 0;
    }

    var due = [];
    for (var i = 0; i < pairs.length; i++) {
      if (pairs[i].ts > 0 && pairs[i].ts <= now) due.push(pairs[i]);
    }
    due.sort(function (a, b) { return a.ts - b.ts; });

    if (CFG.enabled === false) {
      A.lowBalance = null;
      if (due.length) setBlock(due[0].name, 'paused', due[0].ts, balance);
      else clearBlock();
      return;
    }

    var btn = document.querySelector('.reward-btn');

    if (btn && !A.btn) A.btn = { t: now };
    if (!btn && A.btn) A.btn = null;

    if (!due.length) { clearBlock(); return; }

    // The earliest due chest that has not burned its attempts. The other chest
    // keeps its own state; it is neither blocked nor retried by this one.
    var target = null;
    for (var i = 0; i < due.length; i++) {
      if (!A.slots[due[i].name].gaveUp) { target = due[i]; break; }
    }
    if (!target) {
      setBlock(due[0].name, 'give-up', due[0].ts, balance,
               A.slots[due[0].name].attempts);
      return;
    }

    var slot = A.slots[target.name];
    var overdue = now - target.ts;

    if (!btn) {
      // The page draws the button a beat after the countdown turns due; only
      // call it missing once that beat has clearly passed.
      if (overdue < DRAW_GRACE_MS) { clearBlock(); return; }
      if (balance !== null && balance < CFG.min_balance) {
        A.lowBalance = balance;
        setBlock(target.name, 'low-balance', target.ts, balance);
      } else {
        A.lowBalance = 0;
        setBlock(target.name, 'no-button', target.ts, balance);
      }
      return;
    }

    // No wallet on screen yet: the pet dock has not painted its number, so do
    // not guess from the click counters. Wait for the real value.
    if (balance === null) {
      A.lowBalance = 0;
      setBlock(target.name, 'no-wallet', target.ts, balance);
      return;
    }

    if (balance < CFG.min_balance) {
      A.lowBalance = balance;
      setBlock(target.name, 'low-balance', target.ts, balance);
      return;
    }
    A.lowBalance = 0;

    if (btn.disabled) {
      A.disabledHits++;
      setBlock(target.name, 'button-disabled', target.ts, balance);
      return;
    }

    var rect = btn.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) {
      setBlock(target.name, 'button-hidden', target.ts, balance);
      return;
    }

    // Expected wait, not a stall: the countdown is a little ahead of the
    // server, so the click is held back instead of being refused.
    if (overdue < READY_GRACE_MS) { clearBlock(); return; }
    // One click per chest per lock window; a refused click waits for the lock
    // instead of firing again on the next verify interval.
    if (now - slot.lastClickAt < LOCK_MS) { clearBlock(); return; }

    clearBlock();

    var rec = {
      t: s.now,
      iso: new Date(s.now).toISOString(),
      slot: target.name,
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      readyL: target.name === 'left',
      readyR: target.name === 'right',
      balance: balance,
      overdueMs: overdue,
      renderLagMs: A.btn ? now - A.btn.t : -1,
      disabled: !!btn.disabled,
      // How many reward buttons the page drew, and the page's own words on the
      // one about to be clicked: a count above one means the other chest was on
      // screen too, a label such as "冷却中" means the app still refuses it.
      buttons: document.querySelectorAll('.reward-btn').length,
      label: String(btn.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 24),
      attempt: slot.attempts + 1
    };

    slot.lastClickAt = now;
    slot.attempts += 1;
    try {
      btn.click();
      rec.ok = true;
    } catch (e) {
      rec.ok = false;
      rec.err = String(e);
      note(e);
    }

    A.clicks.push(rec);
    if (A.clicks.length > 50) A.clicks.shift();

    // Only this chest's timestamp decides the outcome. A click that changes
    // nothing is retried after the lock, up to MAX_ATTEMPTS; then the chest is
    // parked until the app opens it or the page reloads.
    setTimeout(function () {
      var after = (A.snapshot || snapshot)();
      if (!after) return;
      var moved = target.name === 'left' ? after.left : after.right;
      if (moved !== target.ts) return;
      A.retries++;
      if (slot.attempts >= MAX_ATTEMPTS) {
        slot.gaveUp = true;
        A.notice = {
          text: '自动开箱失败',
          sub: '连续 ' + slot.attempts + ' 次点击没生效，已暂停这个宝箱',
          until: Date.now() + NOTICE_MS
        };
        note('gave up on ' + target.name + ' after ' + slot.attempts + ' attempts');
        return;
      }
      note('click on ' + target.name + ' produced no state change ('
           + slot.attempts + '/' + MAX_ATTEMPTS + ')');
    }, CFG.verify_ms);
  }

  // Always re-arm the grace wake-up, whichever path the step took.
  function tick() {
    step();
    planGrace();
  }

  // Wake exactly when the earliest due chest clears its grace window, so the
  // base poll can stay slow without delaying the click.
  var graceTimer = 0;
  function planGrace() {
    var now = Date.now();
    var next = Infinity;
    for (var name in A.slots) {
      var record = A.slots[name];
      if (!record.ts || record.gaveUp) continue;
      var readyAt = record.ts + READY_GRACE_MS;
      if (readyAt > now && readyAt < next) next = readyAt;
    }
    if (graceTimer) { clearTimeout(graceTimer); graceTimer = 0; }
    if (next !== Infinity) {
      graceTimer = setTimeout(tick, Math.max(0, next - now));
    }
  }

  var lastRun = 0;
  function throttled() {
    var n = Date.now();
    if (n - lastRun < 40) return;
    lastRun = n;
    tick();
  }

  // 1 Hz keeps countdowns and the balance fresh; the observer wakes the loop the
  // moment the chest UI changes, and planGrace() wakes it exactly at the grace
  // boundary. CFG.poll_ms can only raise the floor for slower machines.
  A.timer = setInterval(tick, Math.max(1000, CFG.poll_ms || 1000));
  A.obs = new MutationObserver(throttled);
  A.obs.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['disabled']
  });

  A.configure = function (next) {
    CFG.enabled = next.enabled !== false;
    CFG.bubble = next.bubble !== false;
    CFG.animation = next.animation !== false;
    if (!CFG.enabled) A.lowBalance = null;
    if (!CFG.bubble && bubble) dropBubble();
    if (bubble) bubble.box.style.animation = CFG.animation ? '' : 'none';
  };
  A.tick = tick;
  A.snapshot = snapshot;
  A.readBal = barBalance;
  A.updateAlert = updateAlert;
  A.dropBubble = dropBubble;
  A.uninstall = function () {
    clearInterval(A.timer);
    if (graceTimer) clearTimeout(graceTimer);
    A.obs.disconnect();
    try { dropBubble(); } catch (e) {}
    delete window.__WUT__;
  };

  return 'installed v' + V;
})()
