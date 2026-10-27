"""The daemon: keep the injection alive, log every click, publish state."""

import json
import os
import signal
import time
import traceback
from collections import deque
from datetime import datetime

from . import __version__, cdp, win
from .config import INJECT_PATH, LOG_DIR, LOCK_PATH, STATE_PATH, load
from .logs import Log
from .settings import SettingsBridge
from .reports import DailyReport
from .rooms import RoomBridge, RoomHistory

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
    errors: (A.errors || []).slice(-5),
    clicks: (A.clicks || []).slice(-12),
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


class Runner:
    def __init__(self, cfg=None, log=None):
        self.cfg = cfg or load()
        self.log = log or Log(self.cfg.log_max_bytes, self.cfg.log_keep)
        self.session = cdp.Session(self.cfg.port, self.cfg.host)
        self.settings = SettingsBridge(self.cfg.port, self.cfg.host, self.log)
        self.reports = DailyReport(self.log)
        self.rooms = RoomBridge(self.cfg.port, self.cfg.host, self.log)
        self.history = RoomHistory(self.log, limit=self.cfg.room_history)
        self.pending = deque(maxlen=400)
        self.click_count = 0
        self.started = datetime.now()
        self.last_beat = 0.0
        self.low_balance_warned_at = 0.0
        self.alert_level = None
        self._lock_handle = None
        self._stop = False

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
                "remain_left": round((snapshot.get("left") or 0) / 1000.0 - now / 1000.0, 1),
                "remain_right": round((snapshot.get("right") or 0) / 1000.0 - now / 1000.0, 1),
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
            stamp = _local_stamp(rec.get("iso"))
            if rec.get("ok") is False:
                self.log.error(
                    "CLICK-FAIL at=%s xy=(%s,%s) err=%s"
                    % (stamp, rec.get("x"), rec.get("y"), rec.get("err"))
                )
                continue
            self.log.info(
                "CLICK at=%s xy=(%s,%s) readyL=%s readyR=%s balance=%s "
                "overdue=%sms renderLag=%sms disabled=%s"
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
                )
            )

    def check_balance(self, snapshot, low_balance):
        """Record why the page is refusing to click, at most every ten minutes.

        The gate lives in the page: only it can see whether the chest button
        exists at all and what the wallet renders. This side just notes that the
        refusal happened instead of recomputing the same comparison.

        The page reports the shortfall when it refused and 0 when the balance was
        fine, so a falsey value is not a low balance - reading only `is None`
        logged the nonsense line "balance 0 < 1000".
        """
        if not snapshot or not low_balance:
            return
        now = time.time()
        due = min(snapshot.get("left") or 0, snapshot.get("right") or 0)
        overdue = due and (due / 1000.0) <= (snapshot.get("now") or 0) / 1000.0
        if overdue and now - self.low_balance_warned_at > 600:
            self.low_balance_warned_at = now
            self.log.warn(
                "a chest is ready but balance %s < %s - type on the keyboard to earn "
                "characters, no click attempted"
                % (low_balance, self.cfg.inject_cfg["min_balance"])
            )

    def alert_balance(self, snapshot, alert):
        """Blink and log when the page reports a tier change.

        The tier is decided in the page, right next to the number it comes from,
        so the bubble and the taskbar can never disagree. This side only owns
        the two things the page cannot reach: Win32 and the log file. The very
        first reading just sets the baseline.

        The balance only falls when a chest is opened and rises when you type,
        so a level change is a real event rather than noise.
        """
        if not snapshot:
            return
        # A page that just reloaded reports zeros until the Pinia store fills
        # in, so the chest timestamps (absolute epoch ms, never 0) gate this.
        if not (snapshot.get("left") or 0):
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
            "daemon start version=%s pid=%d port=%d poll=%dms lock=%dms min_balance=%d"
            % (
                __version__,
                os.getpid(),
                self.cfg.port,
                self.cfg.inject_cfg["poll_ms"],
                self.cfg.inject_cfg["lock_ms"],
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
                    snapshot = payload.get("state")
                    self.reports.observe(snapshot)

                    self.drain_clicks(payload.get("clicks"))
                    self.check_balance(snapshot, payload.get("lowBalance"))
                    self.alert_balance(snapshot, payload.get("alert"))
                    self.rooms.sync(self.history)

                    for err in payload.get("errors") or []:
                        self.log.warn("page: %s" % err.get("m"))

                    backoff = 2.0
                    failures = 0

                    if snapshot and time.time() - self.last_beat >= self.cfg.heartbeat:
                        self.last_beat = time.time()
                        now = snapshot.get("now") or 0
                        self.log.info(
                            "state remain L=%.1fs R=%.1fs balance=%s"
                            % (
                                ((snapshot.get("left") or 0) - now) / 1000.0,
                                ((snapshot.get("right") or 0) - now) / 1000.0,
                                snapshot.get("bal"),
                            )
                        )

                    merged = dict(snapshot or {})
                    merged["inject"] = payload.get("inject") or 0
                    self.publish(merged, {
                        "low_balance": payload.get("lowBalance"),
                        "retries": payload.get("retries") or 0,
                        "disabled_hits": payload.get("disabledHits") or 0,
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
