"""Shared target discovery and per-feature page connection lifecycles."""

import json
import threading
import time
from urllib.parse import urlparse

from . import cdp


def target_key(target):
    target = target or {}
    return tuple(target.get(name) for name in ("id", "webSocketDebuggerUrl", "url"))


def connection_key(session):
    if session is None:
        return None
    return (id(session), target_key(session.target),
            getattr(session, "generation", 0), getattr(session, "document_epoch", 0))


def _route(url, fragment):
    try:
        parsed = urlparse(url)
        return (parsed.scheme == "http" and parsed.hostname == "tauri.localhost"
                and parsed.path == "/index.html" and parsed.fragment == fragment)
    except (TypeError, ValueError):
        return False


def is_panel(url):
    return _route(url, "/panel")


def is_toast(url):
    return _route(url, "/toast")


class TargetDiscovery:
    """One three-second snapshot per daemon/endpoint, including failed scans."""

    interval = 3.0

    def __init__(self, port, host="127.0.0.1", *, clock=None):
        self.port = port
        self.host = host
        self.clock = clock or time.monotonic
        self.scanned_at = float("-inf")
        self._targets = ()
        self._error = None
        self._lock = threading.Lock()

    def targets(self, *, at=None):
        with self._lock:
            now = self.clock() if at is None else at
            if now - self.scanned_at >= self.interval:
                self.scanned_at = now
                self._targets = ()
                self._error = None
                try:
                    self._targets = tuple(dict(target) for target in
                                          cdp.list_targets(self.port, self.host, timeout=2))
                except (cdp.CDPError, OSError, ValueError) as exc:
                    self._error = exc
            if self._error is not None:
                raise self._error
            # Consumers may attach their own metadata without changing the cache.
            return [dict(target) for target in self._targets]


class PageBridge:
    """Share discovery/mounting policy, while each feature owns its connection."""

    def __init__(self, port, host, log, *, select, uninstall, discovery=None):
        self.port = port
        self.host = host
        self.log = log
        self.discovery = discovery if discovery is not None else TargetDiscovery(port, host)
        self.select = select
        self.uninstall = uninstall
        self.session = None
        self.last_scan = float("-inf")

    def ensure_connected(self, clock=None):
        now = (clock or self.discovery.clock)()
        if now - self.last_scan < self.discovery.interval:
            return self.session
        try:
            targets = self.discovery.targets(at=now)
        finally:
            self.last_scan = self.discovery.scanned_at
        target = next((t for t in targets
                       if self.select(t.get("url", ""))), None)
        if self.session is not None and target is not None:
            if target_key(self.session.target) == target_key(target):
                return self.session
        self.close()
        if target is not None:
            session = cdp.Session(self.port, self.host, timeout=2)
            try:
                session.connect_target(target)
            except Exception:
                session.close()
                raise
            self.session = session
        return self.session

    def require_current(self, identity):
        if connection_key(self.session) != identity:
            raise cdp.StaleResponse("page connection was replaced")

    def read_injected(self, expression, source, version, *, label=None):
        session = self.session
        identity = connection_key(session)
        raw = session.js(expression)
        self.require_current(identity)
        state = json.loads(raw) if raw else None
        if state and state.get("version") == version:
            return state
        session.invalidate_document()
        identity = connection_key(session)
        if state is not None:
            session.js(self.uninstall)
        message = session.js(source)
        if label:
            self.log.info("%s -> %s" % (label, message))
        raw = session.js(expression)
        self.require_current(identity)
        return json.loads(raw) if raw else None

    def close(self):
        session, self.session = self.session, None
        if session is not None:
            try:
                session.js(self.uninstall)
            except (cdp.CDPError, OSError):
                pass
            finally:
                session.close()
