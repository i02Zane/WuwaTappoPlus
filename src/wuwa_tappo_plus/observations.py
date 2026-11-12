"""Interpret page observations without owning connections or the daemon loop."""

import time
from collections import deque
from datetime import datetime

from .runtime_state import remaining


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


class PageObservations:
    def __init__(self, log, started=None, wall_clock=None):
        self.log = log
        self.started = started or datetime.now()
        self.wall_clock = wall_clock or time.time
        self.pending = deque(maxlen=400)
        self.page_errors = deque(maxlen=400)
        self.block_reason = None
        self.block_key = (None, None, None)
        self.block_logged_at = 0.0
        self.click_count = 0
        self.toast_floor_ms = int(self.started.timestamp() * 1000) - 2000
        self.last_click_ms = 0
        self.last_beat = 0.0
        self.alert_level = None

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
        if self.last_click_ms and self.wall_clock() * 1000 - self.last_click_ms < 5000:
            return
        now = self.wall_clock()
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

    def alert_balance(self, cfg, snapshot, alert):
        """Log when the page reports a tier change.

        The tier is decided in the page, right next to the number it comes from,
        so the bubble and the log can never disagree. The page owns everything
        the user sees; this side only formats the line and tracks the
        transition. The very first reading just sets the baseline.

        The balance only falls when a chest is opened and rises when you type,
        so a level change is a real event rather than noise.
        """
        if not cfg.balance_reminder:
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
                    % (shown, cfg.alert_critical, cfg.inject_cfg["min_balance"])
                )
            else:
                left = (balance // 1000) if balance is not None else 0
                self.log.warn(
                    "balance %s below %s - roughly %d more chest%s before it stops"
                    % (shown, cfg.alert_low, left, "" if left == 1 else "s")
                )
        else:
            back = cfg.alert_critical if previous == 2 else cfg.alert_low
            self.log.info("balance %s back above %s" % (shown, back))

    def heartbeat(self, cfg, snapshot):
        now = self.wall_clock()
        if not snapshot or now - self.last_beat < cfg.heartbeat:
            return
        self.last_beat = now
        stamp = snapshot.get("now") or 0
        self.log.info("state remain L=%s R=%s balance=%s" % (
            ("%.1fs" % remaining(snapshot, "left", stamp)) if snapshot.get("left") else "unknown",
            ("%.1fs" % remaining(snapshot, "right", stamp)) if snapshot.get("right") else "unknown",
            snapshot.get("bal")))
