"""Bridge the settings WebView to whitelisted local preferences."""

import json
import time
import tomllib
from urllib.parse import urlparse

from . import __version__, accessories, cdp
from .config import CONFIG_PATH, Config, save_preferences, with_preferences
from .diagnostics import export_logs
from .paths import SCRIPTS_DIR

READ_JS = """JSON.stringify(window.__WUT_SETTINGS__ ? {
  version: window.__WUT_SETTINGS__.version,
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
        self.export_results = {}
        self.pet_accessory = None

    @property
    def accessories_path(self):
        return self.config_path.parent / accessories.FILE_NAME

    def sync(self, cfg, report=None):
        try:
            overrides = accessories.load(self.accessories_path)
        except OSError:
            overrides = {}
        try:
            # utf-8-sig: a BOM written by a Windows editor must not look like a
            # broken config, or the switches silently stop applying.
            disk = Config(tomllib.loads(self.config_path.read_text(encoding="utf-8-sig")))
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
            ack = []
            for request in (state or {}).get("requests", [])[:10]:
                if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                    continue
                result = {"id": request["id"]}
                if request.get("action") == "export_logs":
                    result = self.export_results.get(request["id"])
                    if result is None:
                        result = {"id": request["id"], "action": "export_logs"}
                        try:
                            result["path"] = str(export_logs(cfg, data_dir=self.config_path.parent))
                        except (OSError, ValueError) as exc:
                            result["error"] = True
                            self._warn("log export: %s" % exc)
                        self.export_results[request["id"]] = result
                        if len(self.export_results) > 32:
                            self.export_results.pop(next(iter(self.export_results)))
                    ack.append(result)
                    continue
                if request.get("action") == "save_accessory":
                    try:
                        overrides = accessories.set_override(
                            self.accessories_path, request.get("file"),
                            request.get("x", 0), request.get("y", 0),
                            request.get("scale", 1), request.get("rotation", 0))
                        result["action"] = "save_accessory"
                    except (OSError, TypeError, ValueError) as exc:
                        result["error"] = True
                        self._warn("accessory override: %s" % exc)
                    ack.append(result)
                    continue
                if "action" in request:
                    ack.append({"id": request["id"], "error": True})
                    continue
                try:
                    saved = save_preferences({request.get("key"): request.get("value")},
                                             path=self.config_path)
                    cfg = with_preferences(cfg, saved.preferences)
                except (OSError, ValueError, TypeError) as exc:
                    result["error"] = True
                    self._warn(exc)
                ack.append(result)
            payload = json.dumps({"preferences": cfg.preferences, "accessories": overrides,
                                  "pet_accessory": self.pet_accessory,
                                  "ack": ack, "report": report})
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
