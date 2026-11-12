"""Daemon orchestration: scheduling, reconnect backoff and orderly shutdown."""

import os
import signal
import threading
import time
import traceback

from . import __version__, cdp
from .config import LOG_DIR, load
from .logs import Log
from .runtime_services import RuntimeServices, attempt, sample_game_mode
from .runtime_state import InstanceLock, StatePublisher

STOP_FILE = LOG_DIR.parent / ".stop"


class Runner:
    def __init__(self, cfg=None, log=None, *, services_factory=RuntimeServices,
                 lock=None, publisher=None, stop_file=STOP_FILE, clock=None, wait=None):
        self.cfg = cfg or load()
        self.log = log or Log(self.cfg.log_max_bytes, self.cfg.log_keep)
        self.services_factory = services_factory
        self.lock = lock if lock is not None else InstanceLock()
        self.publisher = publisher if publisher is not None else StatePublisher()
        self.stop_file = stop_file
        self.clock = clock or time.monotonic
        self._stop = threading.Event()
        self.wait = wait or self._stop.wait

    def request_stop(self, *args):
        self._stop.set()

    def _wait(self, seconds):
        # Keep the existing retry budget, but notice stop files during backoff
        # before the installer's normal graceful-exit budget runs out.
        deadline = self.clock() + seconds
        while not self._stop.is_set() and not self.stop_file.exists():
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            self.wait(min(remaining, 1.0))

    def _sync_settings(self, services):
        report = attempt(self.log, "yesterday report", services.reports.yesterday)
        self.cfg = attempt(self.log, "settings",
                           lambda: services.settings.sync(self.cfg, report), self.cfg)

    def _features(self, services, payload):
        """Preserve normal feature order while giving each its own failure boundary."""
        cfg = self.cfg
        s = services
        observations = s.observations
        snapshot = payload.get("state")
        jobs = (
            ("chest cache", lambda: s.chests.observe(payload.get("chestCache"))),
            ("reports", lambda: s.reports.observe(snapshot)),
            ("accessories", lambda: s.pet.apply_accessories(s.settings.accessories_path)),
            ("accessory base", lambda: setattr(s.settings, "pet_accessory", s.pet.accessory_base())),
            ("fullscreen", lambda: s.game_mode.tick(s.session, cfg.game_mode_plus)),
            ("clicks", lambda: observations.drain_clicks(payload.get("clicks"))),
            ("click gate", lambda: observations.note_block(payload.get("block"), snapshot)),
            ("balance", lambda: observations.alert_balance(cfg, snapshot, payload.get("alert"))),
            ("rooms", lambda: s.rooms.sync(s.history, plaza=s.plaza)),
            ("plaza", lambda: s.plaza.sync(s.session)),
            ("screen", lambda: s.screens.tick(s.session, cfg.panel_remember_screen)),
            ("toasts", lambda: self._toasts(s)),
            ("page errors", lambda: observations.drain_page_errors(payload.get("errors"))),
            ("heartbeat", lambda: observations.heartbeat(cfg, snapshot)),
        )
        for label, action in jobs:
            attempt(self.log, label, action)
        merged = dict(snapshot or {}, inject=payload.get("inject") or 0)
        self.publisher.publish(cfg, s.session, observations, merged, {
            "low_balance": payload.get("lowBalance"),
            "retries": payload.get("retries") or 0,
            "disabled_hits": payload.get("disabledHits") or 0,
            "block": observations.block_reason,
        })

    def _toasts(self, services):
        observations = services.observations
        for when, text in services.toasts.poll(observations.toast_floor_ms):
            observations.log_toast(when, text)

    def run(self):
        if not self.lock.acquire():
            self.log.error("another instance is already running")
            print("already running (lock held by another instance)", flush=True)
            return 2

        services = None
        handlers = {}
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    handlers[sig] = signal.signal(sig, self.request_stop)
                except ValueError:  # Embedding/tests may run outside the main thread.
                    pass
            self.stop_file.unlink(missing_ok=True)
            # No databases or feature resources are opened by a duplicate daemon.
            services = self.services_factory(self.cfg, self.log, lambda: sample_game_mode(self.cfg))
            self.log.info(
                "daemon start version=%s pid=%d port=%d poll=%dms min_balance=%d"
                % (__version__, os.getpid(), self.cfg.port,
                   self.cfg.inject_cfg["poll_ms"], self.cfg.inject_cfg["min_balance"]))
            started = self.clock()
            backoff = 2.0
            failures = 0
            while not self._stop.is_set():
                if self.stop_file.exists():
                    self.log.info("stop requested via stop file")
                    break
                if self.cfg.max_runtime and self.clock() - started >= self.cfg.max_runtime:
                    self.log.info("max_runtime reached, exiting")
                    break
                self._sync_settings(services)
                try:
                    if services.session.ws is None:
                        services.session.connect()
                        self.log.info("cdp connected -> %s" % services.session.target.get("url"))
                    payload = services.pet.poll(self.cfg, services.chests.data)
                except (cdp.CDPError, OSError) as exc:
                    failures += 1
                    attempt(self.log, "disconnect", services.session.close)
                    if failures in (1, 5) or failures % 60 == 0:
                        self.log.warn("cdp error #%d: %s (retry in %.0fs)" % (failures, exc, backoff))
                    self.publisher.publish(self.cfg, services.session, services.observations,
                                           extra={"last_error": str(exc)[:200]})
                    self._wait(backoff)
                    backoff = min(backoff * 1.5, 10.0)
                    continue
                self._features(services, payload)
                backoff = 2.0
                failures = 0
                self._wait(1.0)
        except Exception:
            self.log.error("FATAL " + traceback.format_exc().replace("\n", " | "))
            return 1
        finally:
            try:
                if services is not None:
                    attempt(self.log, "close services", services.close)
                    attempt(self.log, "stopped state", self.publisher.stopped)
                    self.log.info("daemon stop, %d click(s) logged this session"
                                  % services.observations.click_count)
            finally:
                self.lock.release()
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
        return 0


def main():
    return Runner().run()
