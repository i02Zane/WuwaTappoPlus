"""Own automatic game mode, with durable recovery and confirmed native writes."""

import json
import time

from . import __version__, cdp, fullscreen
from .paths import SCRIPTS_DIR


def source():
    return (SCRIPTS_DIR / "game_mode.js").read_text(encoding="utf-8").replace(
        "__VERSION__", json.dumps(__version__))


class GameModeController:
    STABLE_SECONDS = 2.0
    ACK_SECONDS = 3.0
    MAX_ATTEMPTS = 3

    def __init__(self, log=None, clock=time.monotonic, wall_clock=time.time,
                 detector=fullscreen.foreground_client_covers_monitor):
        self.log = log
        self.clock = clock
        self.wall_clock = wall_clock
        self.detector = detector
        self.candidate = None
        self.candidate_since = 0.0
        self.script = source()

    def _call(self, session, action, value=None):
        expression = "(async function(){%s;return JSON.stringify(await " \
                     "window.__WUT_GAME_MODE__.%s(%s));})()" % (
                         self.script, action, "" if value is None else json.dumps(value))
        return json.loads(session.js(expression))

    def _save(self, session, ledger):
        self._call(session, "save", ledger)

    def _issue(self, session, ledger, target):
        previous = ledger.get("pending") or {}
        ledger["pending"] = {"target": target, "at": self.wall_clock(),
                             "attempts": previous.get("attempts", 0) + 1, "confirmed": False}
        # Save intent before emitting: reconnect can safely repeat a target value.
        if target:
            ledger["owned"] = True
        self._save(session, ledger)
        self._call(session, "setMode", target)

    def tick(self, session, enabled):
        if not enabled:
            self.release(session)
            return
        state = self._call(session, "read")
        ledger = state["ledger"]
        if ledger is None:
            original = state.get("legacy_auto")
            ledger = {"original_auto": state["auto"] if original is None else original,
                      "owned": False, "active": False, "suppressed": False,
                      "failed": False, "pending": None}
            self._save(session, ledger)
        if state["auto"]:
            if self._call(session, "setAuto", False) is not False:
                raise cdp.CDPError("official fullscreen setting was not disabled")
            # Do not enter until the competing native auto logic is confirmed off.
            state = self._call(session, "read")
            if state["auto"]:
                return
        pending = ledger.get("pending")
        if pending:
            if state["mode"] == pending["target"] and pending.get("confirmed"):
                ledger["owned"] = pending["target"]
                ledger["pending"] = None
                self._save(session, ledger)
            elif self.wall_clock() - pending["at"] < self.ACK_SECONDS:
                return
            elif pending["attempts"] < self.MAX_ATTEMPTS:
                self._issue(session, ledger, pending["target"])
                return
            else:
                ledger["pending"] = None
                ledger["failed"] = True
                self._save(session, ledger)
                if self.log:
                    self.log.warn("game mode: native state not confirmed; paused until fullscreen changes")
        if ledger["owned"] and not state["mode"]:
            # The user manually exited. Respect that choice for this fullscreen.
            ledger["owned"] = False
            ledger["suppressed"] = True
            self._save(session, ledger)
        sample = self.detector()
        if sample is None:
            self.candidate = None  # An unknown sample breaks the stability window.
            return
        now = self.clock()
        if sample != self.candidate:
            self.candidate = sample
            self.candidate_since = now
            return
        if now - self.candidate_since < self.STABLE_SECONDS:
            return
        if ledger["active"] != sample:
            ledger.update(active=sample, suppressed=False, failed=False)
            self._save(session, ledger)
        if ledger["failed"] or ledger["suppressed"]:
            return
        if sample and not state["mode"]:
            self._issue(session, ledger, True)
        elif not sample and ledger["owned"] and state["mode"]:
            self._issue(session, ledger, False)

    def release(self, session):
        """Release only our game mode and restore the native automatic setting."""
        self._call(session, "release")
        self.candidate = None
