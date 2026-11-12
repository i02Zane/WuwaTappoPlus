"""Read the app's toast messages so a refused click leaves a record.

The toast window is a separate WebView (``index.html#/toast``): messages such
as "宝箱冷却中" are rendered there and never reach the pet page. The app's own
log file does record the failed request, but only as
``POST api/frontend/chests/open code=70006`` in UTC; one line per toast with a
local timestamp is enough to line the two up.
"""

import json
from collections import deque

from . import __version__, cdp
from .paths import SCRIPTS_DIR
from .pages import PageBridge, is_toast

READ_JS = """JSON.stringify(window.__WUT_TOAST__ ? {
  version: window.__WUT_TOAST__.version,
  entries: window.__WUT_TOAST__.entries
} : null)"""


class ToastBridge(PageBridge):
    """Keep one session on the toast WebView and hand back new messages.

    The page-side script owns the buffer; this class only notices new entries
    and returns them, the same split the room history bridge uses.
    """

    def __init__(self, port, host, log, *, discovery=None):
        super().__init__(port, host, log, select=is_toast, discovery=discovery,
                         uninstall="if(window.__WUT_TOAST__&&window.__WUT_TOAST__.uninstall)"
                                   "window.__WUT_TOAST__.uninstall(); 1")
        self.seen = deque(maxlen=400)
        self.last_error = None
        self.source = (SCRIPTS_DIR / "toast.js").read_text(encoding="utf-8").replace(
            "__VERSION__", json.dumps(__version__))

    def poll(self, since_ms=None, clock=None):
        """Return ``[(epoch_ms, text)]`` for messages not returned before.

        ``since_ms`` drops entries the page buffered before this daemon started:
        a restart must not replay yesterday's toasts into the log.
        """
        try:
            if self.ensure_connected(clock) is None:
                return []
            state = self.read_injected(READ_JS, self.source, __version__, label="toast")
            return self._fresh(state.get("entries") if state else None, since_ms)
        except cdp.StaleResponse:
            return []
        except (cdp.CDPError, OSError, ValueError, TypeError) as exc:
            self._warn(exc)
            self.close()
            return []

    def _fresh(self, entries, since_ms):
        fresh = []
        for entry in entries or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("m"), str):
                continue
            key = (entry.get("t"), entry["m"])
            if key in self.seen:
                continue
            self.seen.append(key)
            when = entry.get("t")
            if not isinstance(when, (int, float)):
                continue
            if since_ms is not None and when < since_ms:
                continue
            fresh.append((when, entry["m"]))
        return fresh

    def _warn(self, exc):
        message = str(exc)
        if message != self.last_error:
            self.log.warn("toast: " + message[:200])
            self.last_error = message
