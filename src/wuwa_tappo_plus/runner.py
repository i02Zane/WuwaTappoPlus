"""The daemon: keep the injection alive, log every click, publish state."""

import json
import os
import signal
import time
import traceback
from collections import deque
from datetime import datetime

from . import __version__, accessories, cdp, win
from .config import INJECT_PATH, LOG_DIR, LOCK_PATH, STATE_PATH, load
from .logs import Log
from .settings import SettingsBridge
from .reports import DailyReport
from .rooms import RoomBridge, RoomHistory
from .screens import ScreenKeeper
from .toasts import ToastBridge
from .chests import ChestCache

POLL_JS = r"""
(function(){
  var A = window.__WUT__;
  if (!A) return JSON.stringify({inject: 0});
  if (A.configure) A.configure(__LIVE_CFG__);
  A.beat = Date.now();
  var s = null;
  try { s = A.snapshot ? A.snapshot() : null; } catch (e) {}
  return JSON.stringify({
    inject: A.v,
    lowBalance: A.lowBalance,
    alert: A.alert || 0,
    retries: A.retries || 0,
    disabledHits: A.disabledHits || 0,
    block: A.block ? { slot: A.block.slot, reason: A.block.reason,
                       ts: A.block.ts, since: A.block.since,
                       balance: A.block.balance, attempts: A.block.attempts } : null,
    errors: (A.errors || []).slice(-5),
    clicks: (A.clicks || []).slice(-12),
    chestCache: A.chestCache || null,
    state: s
  });
})()
"""

STOP_FILE = LOG_DIR.parent / ".stop"


def _local_stamp(iso):
    """The page reports UTC; logs should read in local time."""
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S.%f")[:-3]
    except (ValueError, TypeError):
        return str(iso)


def _stamp_ms(milliseconds):
    """Epoch milliseconds to a local clock reading, to line up with app logs."""
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000.0).strftime(
            "%H:%M:%S.%f")[:-3]
    except (ValueError, TypeError, OSError, OverflowError):
        return "-"


def _remaining(snapshot, name, now):
    """A hidden native countdown is unknown, not an epoch-sized overdue value."""
    timestamp = snapshot.get(name)
    return round((timestamp - now) / 1000.0, 1) if timestamp else None


class Runner:
    def __init__(self, cfg=None, log=None):
        self.cfg = cfg or load()
        self.log = log or Log(self.cfg.log_max_bytes, self.cfg.log_keep)
        self.session = cdp.Session(self.cfg.port, self.cfg.host)
        self.settings = SettingsBridge(self.cfg.port, self.cfg.host, self.log)
        self.reports = DailyReport(self.log)
        self.rooms = RoomBridge(self.cfg.port, self.cfg.host, self.log)
        self.history = RoomHistory(self.log, limit=self.cfg.room_history)
        self.toasts = ToastBridge(self.cfg.port, self.cfg.host, self.log)
        self.screens = ScreenKeeper(self.log)
        self.chests = ChestCache(self.log)
        self.pending = deque(maxlen=400)
        # The page keeps its last 20 notes and hands back the last five on every
        # poll, so without this ledger one error would be written to the log once
        # per second for as long as it stays in that window.
        self.page_errors = deque(maxlen=400)
        self.block_reason = None
        self.block_key = (None, None, None)
        self.block_logged_at = 0.0
        self.click_count = 0
        self.started = datetime.now()
        # Toasts buffered in the page before this daemon started are history,
        # not news; the small slack covers the start-up window.
        self.toast_floor_ms = int(self.started.timestamp() * 1000) - 2000
        self.last_click_ms = 0
        self.last_beat = 0.0
        self.alert_level = None
        self._lock_handle = None
        self._stop = False
        self._accessory_key = None
        self._accessory_session = None

    # ------------------------------------------------------------- plumbing

    def acquire_lock(self):
        if not LOCK_PATH.parent.exists():
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        # On Windows an already-locked file fails at open() time with a sharing
        # violation, so the open itself has to be inside the guard.
        try:
            handle = open(LOCK_PATH, "a+")
        except OSError:
            return False
        try:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        self._lock_handle = handle
        return True

    def release_lock(self):
        if self._lock_handle is None:
            return
        try:
            import msvcrt

            self._lock_handle.seek(0)
            msvcrt.locking(self._lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        try:
            self._lock_handle.close()
        except OSError:
            pass
        self._lock_handle = None

    def publish(self, snapshot=None, extra=None):
        payload = {
            "plus_version": __version__,
            "resource_dir": str(INJECT_PATH.parent),
            "pid": os.getpid(),
            "started": self.started.isoformat(timespec="seconds"),
            "updated": datetime.now().isoformat(timespec="seconds"),
            "port": self.cfg.port,
            "target": (self.session.target or {}).get("url"),
            "clicks_logged": self.click_count,
            "alert": self.alert_level or 0,
            "auto_open": self.cfg.auto_open,
        }
        if snapshot:
            now = snapshot.get("now") or int(time.time() * 1000)
            payload.update({
                "now": now,
                "left": snapshot.get("left"),
                "right": snapshot.get("right"),
                "remain_left": _remaining(snapshot, "left", now),
                "remain_right": _remaining(snapshot, "right", now),
                "balance": snapshot.get("bal"),
                "inject": snapshot.get("inject", 0),
            })
        if extra:
            payload.update(extra)
        tmp = STATE_PATH.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, STATE_PATH)
        except OSError:
            pass

    # ------------------------------------------------------------ injection

    def inject_source(self):
        cfg = dict(self.cfg.inject_cfg)
        if hasattr(self, "chests"):
            cfg["chest_cache"] = self.chests.data
        src = INJECT_PATH.read_text(encoding="utf-8")
        return src.replace("__CFG__", json.dumps(cfg))

    def ensure_injected(self):
        poll = POLL_JS.replace("__LIVE_CFG__", json.dumps(self.cfg.inject_cfg))
        payload = json.loads(self.session.js(poll))
        want = self.cfg.inject_cfg["version"]
        if payload.get("inject") == want:
            return payload
        have = payload.get("inject")
        if have:
            self.log.info("replacing injection v%s with v%s" % (have, want))
            self.session.js("(function(){var A=window.__WUT__; if(A&&A.uninstall)A.uninstall(); return 1;})()")
        verdict = self.session.js(self.inject_source())
        self.log.info("inject -> %s" % verdict)
        payload = json.loads(self.session.js(poll))
        if payload.get("inject") != want:
            raise cdp.CDPError("injection did not take effect")
        return payload

    def apply_accessories(self):
        """Push saved accessory transforms to the pet page when they change."""
        path = self.settings.accessories_path
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        session = self.session
        sid = id(session) if session is not None else None
        if key == self._accessory_key and sid == self._accessory_session:
            return
        overrides = accessories.load(path) if key is not None else {}
        session.js("window.__WUT_ACCESSORY_OVERRIDES__ = %s; 1"
                   % json.dumps(overrides, ensure_ascii=False))
        self._accessory_key = key
        self._accessory_session = sid

    def sync_accessory_base(self):
        """Publish the pet's live accessory anchor so the preview matches it."""
        raw = self.session.js(
            "JSON.stringify((function(){var l=document.querySelector('.cat-stack__layer');"
            "var c=document.querySelector('.cat-mainbox');"
            "return {base:window.__WUT_ACCESSORY_BASE__||null,"
            "size:c?c.getBoundingClientRect().width:null};})())")
        data = json.loads(raw) if raw else {}
        base = data.get("base")
        if base and data.get("size"):
            base = dict(base, size=data["size"])
        if base != self.settings.pet_accessory:
            self.settings.pet_accessory = base

    # ---------------------------------------------------------------- clicks

    def drain_clicks(self, clicks):
        # The page keeps its own click buffer across daemon restarts, so ignore
        # anything that happened before this process came up.
        floor_ms = int(self.started.timestamp() * 1000) - 2000
        for rec in clicks or []:
            key = (rec.get("t"), rec.get("x"), rec.get("y"))
            if key in self.pending:
                continue
            self.pending.append(key)
            if (rec.get("t") or 0) < floor_ms:
                continue
            self.click_count += 1
            if (rec.get("t") or 0) > self.last_click_ms:
                self.last_click_ms = rec.get("t") or 0
            stamp = _local_stamp(rec.get("iso"))
            if rec.get("ok") is False:
                self.log.error(
                    "CLICK-FAIL at=%s xy=(%s,%s) err=%s"
                    % (stamp, rec.get("x"), rec.get("y"), rec.get("err"))
                )
                continue
            self.log.info(
                "CLICK at=%s xy=(%s,%s) readyL=%s readyR=%s balance=%s "
                "overdue=%sms renderLag=%sms disabled=%s btns=%s label=%s"
                % (
                    stamp,
                    rec.get("x"),
                    rec.get("y"),
                    rec.get("readyL"),
                    rec.get("readyR"),
                    rec.get("balance"),
                    rec.get("overdueMs"),
                    rec.get("renderLagMs"),
                    rec.get("disabled"),
                    rec.get("buttons"),
                    rec.get("label") or "-",
                )
            )

    def drain_page_errors(self, errors):
        """Write each page note once, not once per poll.

        The page reports a rolling window, so the same dict comes back on every
        heartbeat until twenty newer notes push it out. The tuple of timestamp
        and text is stable, which makes it a usable identity.
        """
        for err in errors or []:
            if not isinstance(err, dict):
                continue
            key = (err.get("t"), err.get("m"))
            if key in self.page_errors:
                continue
            self.page_errors.append(key)
            self.log.warn("page: %s" % err.get("m"))

    def log_toast(self, when_ms, text):
        """Write one app toast, pointing at our last click when it follows one.

        A toast that arrives seconds after a click is usually the app refusing
        it ("宝箱冷却中"), so those are warnings; everything else is ordinary
        feedback and stays at info level.
        """
        after = ""
        if self.last_click_ms and 0 <= when_ms - self.last_click_ms <= 10000:
            after = " (%.1fs after our click)" % ((when_ms - self.last_click_ms) / 1000.0)
        line = 'TOAST at=%s text="%s"%s' % (_stamp_ms(when_ms), text, after)
        if after:
            self.log.warn(line)
        else:
            self.log.info(line)

    # Why a due chest is not being clicked, in the words the log will use.
    BLOCK_TEXT = {
        "paused": "auto open is paused",
        "no-button": "the page draws no reward button",
        "no-wallet": "the balance is not rendered yet",
        "button-disabled": "the reward button is disabled",
        "button-hidden": "the reward button has no size",
        "low-balance": "balance below min_balance",
        "give-up": "clicked repeatedly, the chest never opened",
    }

    def note_block(self, block, snapshot):
        """Log why a due chest is not being clicked, once per chest and reason.

        A chest that is visibly ready but never opens is the question every
        report starts with. The page names the gate; this side writes one line
        when the answer changes, plus a reminder every ten minutes while the
        same chest stays blocked.

        The identity includes the pair of chest timestamps, not just the reason:
        two chests land a few seconds apart, and keying on the reason alone let
        the first one's warning swallow the second's.
        """
        reason = block.get("reason") if isinstance(block, dict) else None
        slot = block.get("slot") if isinstance(block, dict) else None
        ts = block.get("ts") if isinstance(block, dict) else None
        # The UI is in flux for a moment after our own click: the button is
        # disabled while the open is in flight, then the next chest redraws.
        # That is not a stall, so give the click five seconds to settle.
        if self.last_click_ms and time.time() * 1000 - self.last_click_ms < 5000:
            return
        now = time.time()
        key = (slot, reason, ts)
        if key == self.block_key:
            if reason and now - self.block_logged_at >= 600:
                self.block_logged_at = now
                self._log_block("still waiting: ", reason, block, snapshot)
            return
        previous = self.block_key[1]
        self.block_key = key
        self.block_reason = reason
        self.block_logged_at = now
        if reason:
            self._log_block("", reason, block, snapshot)
        elif previous:
            self.log.info("click gate open again (was %s)" % previous)

    def _log_block(self, prefix, reason, block, snapshot):
        detail = []
        if block.get("slot"):
            detail.append("slot=%s" % block["slot"])
        balance = block.get("balance")
        detail.append("balance=%s" % ("-" if balance is None else balance))
        left = (snapshot or {}).get("left") or 0
        now = (snapshot or {}).get("now") or 0
        if left and now:
            seconds = int((now - left) / 1000.0)
            if seconds >= 0:
                detail.append("chest %ds overdue" % seconds)
        if reason == "give-up":
            detail.append("after %s attempts" % block.get("attempts"))
        if reason == "low-balance":
            detail.append("typing earns characters")
        message = "not clicking: %s (%s)" % (
            reason, ", ".join(detail))
        writer = self.log.info if reason == "paused" else self.log.warn
        writer(prefix + message)

    def alert_balance(self, snapshot, alert):
        """Log when the page reports a tier change.

        The tier is decided in the page, right next to the number it comes from,
        so the bubble and the log can never disagree. The page owns everything
        the user sees; this side only formats the line and tracks the
        transition. The very first reading just sets the baseline.

        The balance only falls when a chest is opened and rises when you type,
        so a level change is a real event rather than noise.
        """
        if not self.cfg.balance_reminder:
            self.alert_level = None
            return
        if not snapshot:
            return
        # Hidden native chests have no timestamp. Only an unreadable wallet
        # means the page is not ready; a visible zero balance is valid.
        if snapshot.get("bal") is None:
            return

        level = alert or 0
        balance = snapshot.get("bal")
        shown = "?" if balance is None else str(balance)

        previous = self.alert_level
        self.alert_level = level
        if previous is None or level == previous:
            return

        if level > previous:
            if level == 2:
                self.log.warn(
                    "balance %s below %s - clicking stops under %s, type to earn"
                    % (shown, self.cfg.alert_critical, self.cfg.inject_cfg["min_balance"])
                )
            else:
                left = (balance // 1000) if balance is not None else 0
                self.log.warn(
                    "balance %s below %s - roughly %d more chest%s before it stops"
                    % (shown, self.cfg.alert_low, left, "" if left == 1 else "s")
                )
        else:
            back = self.cfg.alert_critical if previous == 2 else self.cfg.alert_low
            self.log.info("balance %s back above %s" % (shown, back))

    # ------------------------------------------------------------------ loop

    def request_stop(self, *args):
        self._stop = True

    def run(self):
        if not self.acquire_lock():
            self.log.error("another instance is already running")
            print("already running (lock held by another instance)", flush=True)
            return 2

        signal.signal(signal.SIGINT, self.request_stop)
        try:
            signal.signal(signal.SIGTERM, self.request_stop)
        except (AttributeError, ValueError):
            pass

        if STOP_FILE.exists():
            STOP_FILE.unlink()

        self.log.info(
            "daemon start version=%s pid=%d port=%d poll=%dms min_balance=%d"
            % (
                __version__,
                os.getpid(),
                self.cfg.port,
                self.cfg.inject_cfg["poll_ms"],
                self.cfg.inject_cfg["min_balance"],
            )
        )

        started = time.time()
        backoff = 2.0
        failures = 0

        try:
            while not self._stop:
                if STOP_FILE.exists():
                    self.log.info("stop requested via stop file")
                    break
                if self.cfg.max_runtime and time.time() - started >= self.cfg.max_runtime:
                    self.log.info("max_runtime reached, exiting")
                    break

                try:
                    self.cfg = self.settings.sync(self.cfg, self.reports.yesterday())
                    if self.session.ws is None:
                        self.session.connect()
                        self.log.info("cdp connected -> %s" % self.session.target.get("url"))

                    payload = self.ensure_injected()
                    self.chests.observe(payload.get("chestCache"))
                    snapshot = payload.get("state")
                    self.reports.observe(snapshot)

                    try:
                        self.apply_accessories()
                    except Exception as exc:
                        self.log.warn("accessories: %s" % str(exc)[:160])

                    try:
                        self.sync_accessory_base()
                    except Exception as exc:
                        self.log.warn("accessory base: %s" % str(exc)[:160])

                    self.drain_clicks(payload.get("clicks"))
                    self.note_block(payload.get("block"), snapshot)
                    self.alert_balance(snapshot, payload.get("alert"))
                    self.rooms.sync(self.history)

                    try:
                        self.screens.tick(self.session, self.cfg.panel_remember_screen)
                    except Exception as exc:  # never let window work stop the clicker
                        self.log.warn("screen: %s" % str(exc)[:160])

                    for when, text in self.toasts.poll(self.toast_floor_ms):
                        self.log_toast(when, text)

                    self.drain_page_errors(payload.get("errors"))

                    backoff = 2.0
                    failures = 0

                    if snapshot and time.time() - self.last_beat >= self.cfg.heartbeat:
                        self.last_beat = time.time()
                        now = snapshot.get("now") or 0
                        self.log.info(
                            "state remain L=%s R=%s balance=%s"
                            % (
                                ("%.1fs" % _remaining(snapshot, "left", now)) if snapshot.get("left") else "unknown",
                                ("%.1fs" % _remaining(snapshot, "right", now)) if snapshot.get("right") else "unknown",
                                snapshot.get("bal"),
                            )
                        )

                    merged = dict(snapshot or {})
                    merged["inject"] = payload.get("inject") or 0
                    self.publish(merged, {
                        "low_balance": payload.get("lowBalance"),
                        "retries": payload.get("retries") or 0,
                        "disabled_hits": payload.get("disabledHits") or 0,
                        "block": self.block_reason,
                    })

                except (cdp.CDPError, OSError) as exc:
                    failures += 1
                    self.session.close()
                    if failures in (1, 5) or failures % 60 == 0:
                        self.log.warn(
                            "cdp error #%d: %s (retry in %.0fs)" % (failures, exc, backoff)
                        )
                    self.publish(extra={"last_error": str(exc)[:200]})
                    time.sleep(backoff)
                    # Capped low: a restarting app should be picked up quickly,
                    # a ready chest must not wait half a minute for a retry.
                    backoff = min(backoff * 1.5, 10.0)
                    continue

                time.sleep(1.0)

        except Exception:
            self.log.error("FATAL " + traceback.format_exc().replace("\n", " | "))
            return 1
        finally:
            self.settings.close()
            self.reports.close()
            self.rooms.close()
            self.history.close()
            self.toasts.close()
            self.session.close()
            self.release_lock()
            try:
                if STATE_PATH.exists():
                    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                    data["pid"] = None
                    data["stopped"] = datetime.now().isoformat(timespec="seconds")
                    STATE_PATH.write_text(
                        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
                    )
            except Exception:
                pass
            self.log.info("daemon stop, %d click(s) logged this session" % self.click_count)

        return 0


def main():
    return Runner().run()
