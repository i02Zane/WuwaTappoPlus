"""Bridge the settings WebView to whitelisted local preferences."""

import json
import time
import tomllib
from urllib.parse import urlparse

from . import __version__, cdp
from .config import CONFIG_PATH, Config, save_preferences, with_preferences
from .paths import SCRIPTS_DIR

READ_JS = """JSON.stringify(window.__WUT_SETTINGS__ ? {
  version: window.__WUT_SETTINGS__.version,
  panelError: window.__WUT_SETTINGS__.panelError,
  requests: window.__WUT_SETTINGS__.requests
} : null)"""


class SettingsBridge:
    def __init__(self, port, host, log):
        self.port = port
        self.host = host
        self.log = log
        self.session = None
        self.last_scan = -10.0
        self.config_path = CONFIG_PATH
        self.source = (SCRIPTS_DIR / "settings.js").read_text(encoding="utf-8").replace(
            "__VERSION__", json.dumps(__version__))
        self.last_error = None

    def sync(self, cfg, report=None):
        try:
            disk = Config(tomllib.loads(self.config_path.read_text(encoding="utf-8")))
            cfg = with_preferences(cfg, disk.preferences)
        except (OSError, ValueError) as exc:
            self._warn(exc)

        try:
            if time.monotonic() - self.last_scan >= 3:
                self.last_scan = time.monotonic()
                targets = [t for t in cdp.list_targets(self.port, self.host, timeout=2)
                           if self._is_panel(t.get("url", ""))]
                target = targets[0] if targets else None
                current = self.session.target.get("id") if self.session else None
                if target is None or target.get("id") != current:
                    self.close()
                    if target:
                        session = cdp.Session(self.port, self.host, timeout=2)
                        try:
                            session.connect_target(target)
                        except Exception:
                            session.close()
                            raise
                        self.session = session
            if self.session is None:
                return cfg
            state = json.loads(self.session.js(READ_JS))
            if not state or state.get("version") != __version__:
                self.session.js(self.source)
                state = json.loads(self.session.js(READ_JS))
            if state and state.get("panelError"):
                self._warn("keep panel open: %s" % state["panelError"])
            ack = []
            for request in (state or {}).get("requests", [])[:10]:
                if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                    continue
                result = {"id": request["id"]}
                try:
                    saved = save_preferences({request.get("key"): request.get("value")},
                                             path=self.config_path)
                    cfg = with_preferences(cfg, saved.preferences)
                except (OSError, ValueError, TypeError) as exc:
                    result["error"] = True
                    self._warn(exc)
                ack.append(result)
            payload = json.dumps({"preferences": cfg.preferences, "ack": ack, "report": report})
            self.session.js("window.__WUT_SETTINGS__.update(%s); 1" % payload)
        except (cdp.CDPError, OSError, ValueError, TypeError) as exc:
            self._warn(exc)
            self.close()
        return cfg

    @staticmethod
    def _is_panel(url):
        parsed = urlparse(url)
        return (parsed.scheme == "http" and parsed.hostname == "tauri.localhost"
                and parsed.path == "/index.html" and parsed.fragment == "/panel")

    def _warn(self, exc):
        message = str(exc)
        if message != self.last_error:
            self.log.warn("settings: " + message[:200])
            self.last_error = message

    def close(self):
        if self.session:
            try:
                self.session.js("if(window.__WUT_SETTINGS__) window.__WUT_SETTINGS__.uninstall(); 1")
            except (cdp.CDPError, OSError):
                pass
            self.session.close()
            self.session = None
