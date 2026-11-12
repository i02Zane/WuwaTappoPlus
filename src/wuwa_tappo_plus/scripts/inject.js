(function () {
  /* @modules */

  var CFG = __CFG__;
  var V = CFG.version;

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
    // A failure the user must see, with expiry set by the chest module.
    notice: null,
    beat: Date.now(),
    installedAt: Date.now(),
    cfg: CFG
  };

  function note(msg) {
    A.errors.push({ t: Date.now(), m: String(msg) });
    if (A.errors.length > 20) A.errors.shift();
  }

  function isCurrent() { return window.__WUT__ === A; }
  var actions = createPetActions(CFG, isCurrent);
  A.clickPause = actions.clickPause;
  A.autoEmote = actions.autoEmote;
  var reader = createPetState(CFG.chest_cache, isCurrent, note);
  Object.defineProperty(A, 'chestCache', {
    enumerable: true,
    get: function () { return reader.cache; },
    set: function (value) { reader.cache = value; }
  });
  var reminders = createPetReminders(CFG, {
    notice: function () { return A.notice; },
    clearNotice: function () { A.notice = null; },
    setAlert: function (level) { A.alert = level; }
  }, note);
  var appearance = createPetAppearance(CFG);

  var snapshot = reader.snapshot;
  var chestButton = reader.button;
  var updateAlert = reminders.update;
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

    // Each visible ready chest has a stable identity until its native button
    // is replaced by a cooldown or hidden. Keep the two retry budgets separate.
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

    if (!due.length) { A.btn = null; clearBlock(); return; }

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
    // Never use the first global reward button: it may belong to the other slot.
    var btn = chestButton(target.name);
    if (btn && (!A.btn || A.btn.slot !== target.name)) A.btn = { t: now, slot: target.name };
    if (!btn) A.btn = null;

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

    // Neither native wallet nor DOM fallback is available. Wait instead of
    // guessing from raw account counters.
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

    // Verify the same native slot, not the stale userInfo timestamp. A missing
    // button may also mean the dock was hidden: do not claim failure or retry
    // against another slot. The next real button starts a new chest record.
    var attempt = slot.attempts;
    var clickAt = slot.lastClickAt;
    setTimeout(function () {
      if (window.__WUT__ !== A) return;
      var after = (A.snapshot || snapshot)();
      if (!after) return;
      if (slot.ts !== target.ts || slot.lastClickAt !== clickAt) return;
      var moved = target.name === 'left' ? after.left : after.right;
      if (moved !== target.ts || (after.native_chests && !chestButton(target.name))) return;
      A.retries++;
      if (attempt >= MAX_ATTEMPTS) {
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


  function tick() {
    actions.sync();
    step();
    planGrace();
    appearance.sync();
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
    var wasPaused = CFG.pause_clicks === true;
    var wasReminder = CFG.balance_reminder !== false;
    CFG.pause_clicks = next.pause_clicks === true;
    CFG.auto_emote = next.auto_emote === true;
    CFG.auto_emote_rate = next.auto_emote_rate;
    actions.sync();
    CFG.enabled = next.enabled !== false;
    CFG.balance_reminder = next.balance_reminder !== false;
    CFG.bubble = next.bubble !== false;
    CFG.animation = next.animation !== false;
    CFG.game_mode_opacity = appearance.normalizeOpacity(next.game_mode_opacity);
    CFG.hello_boss = next.hello_boss === true;
    appearance.syncMode();
    if (!CFG.enabled) A.lowBalance = null;
    reminders.configure(wasPaused, wasReminder, A.bal);
  };
  A.tick = tick;
  A.snapshot = reader.snapshot;
  A.readBal = reader.balance;
  A.updateAlert = reminders.update;
  A.dropBubble = reminders.drop;
  A.uninstall = function () {
    actions.dispose();
    clearInterval(A.timer);
    appearance.dispose();
    if (graceTimer) clearTimeout(graceTimer);
    A.obs.disconnect();
    reader.dispose();
    try { reminders.drop(); } catch (e) {}
    delete window.__WUT__;
  };

  actions.sync();
  appearance.installPreview();
  if (CFG.pause_clicks) reminders.update(null, null);
  return 'installed v' + V;
})()
