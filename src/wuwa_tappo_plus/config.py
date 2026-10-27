"""Configuration: defaults, TOML loading, and project paths."""

import copy
import os
import re
import tempfile
import tomllib
from pathlib import Path

from . import __version__
from .paths import DATA_DIR, SCRIPTS_DIR

ROOT = DATA_DIR
CONFIG_PATH = ROOT / "config.toml"
LOG_DIR = ROOT / "logs"
STATE_PATH = ROOT / ".state.json"
LOCK_PATH = ROOT / ".daemon.lock"
# Written when the user stops on purpose. The watchdog respects it, otherwise a
# stopped daemon would be resurrected a minute later and could not be turned off.
DISABLED_PATH = ROOT / ".disabled"
INJECT_PATH = SCRIPTS_DIR / "inject.js"

DEFAULT_TOML = """\
# Wuwa Tappo Plus - configuration
# Feature switches update live; other changes take effect after install/repair.

[app]
# Absolute path to the app. Leave empty to auto-detect from the registry.
exe = ""
# Debug port written into the WebView2 policy. Change only if 9222 is taken.
port = 9222
host = "127.0.0.1"

[inject]
# Pause automatic clicking without stopping the daemon.
enabled = true
# How often the injected script checks the countdown (ms).
poll_ms = 100
# After a click, ignore the same slot for this long (ms).
lock_ms = 7000
# How long to wait before deciding a click had no effect (ms).
verify_ms = 2000
# How long without a daemon check-in before the page flags itself as orphaned.
# Clicking continues either way; this is a status bit, not a stop switch.
stale_ms = 30000
# Do not attempt a click below this character balance.
min_balance = 1000

[alerts]
# Balance reminders. Each tier fires once when the balance drops below it, and
# re-arms after you type your way back above. Set a tier to 0 to disable it.
low = 2000
critical = 1000
# Show a bouncing speech bubble over the pet while the balance is below a tier.
bubble = true
# Animate the reminder bubble. False keeps a static reminder.
animation = true

[panel]
# Keep the main panel open when clicking outside; manual close still works.
keep_open = true

[rooms]
# How many visited rooms the multiplayer tab remembers.
history = 20

[daemon]
# Seconds between heartbeat lines in the log.
heartbeat = 300
# Stop after this many seconds. 0 means run until stopped.
max_runtime = 0
# Watchdog: a scheduled task fires this often (minutes) and restarts the daemon
# if it is gone. 0 turns the watchdog off.
watchdog_min = 1

[log]
max_bytes = 1048576
keep = 3
"""


def _merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


BUILTIN = {
    "app": {"exe": "", "port": 9222, "host": "127.0.0.1"},
    "inject": {
        "enabled": True,
        "poll_ms": 100,
        "lock_ms": 7000,
        "verify_ms": 2000,
        "stale_ms": 30000,
        "min_balance": 1000,
    },
    "alerts": {"low": 2000, "critical": 1000, "bubble": True, "animation": True},
    "panel": {"keep_open": True},
    "rooms": {"history": 20},
    "daemon": {"heartbeat": 300, "max_runtime": 0, "watchdog_min": 1},
    "log": {"max_bytes": 1048576, "keep": 3},
}


class Config:
    def __init__(self, data):
        self.data = _merge(BUILTIN, data or {})

    def get(self, section, key, default=None):
        return self.data.get(section, {}).get(key, default)

    @property
    def exe(self):
        return self.get("app", "exe", "") or ""

    @property
    def port(self):
        return int(self.get("app", "port", 9222))

    @property
    def host(self):
        return self.get("app", "host", "127.0.0.1")

    @property
    def inject_cfg(self):
        inj = dict(self.data.get("inject", {}))
        return {
            "version": __version__,
            "enabled": self.auto_open,
            "bubble": self.alert_bubble,
            "animation": self.alert_animation,
            "poll_ms": int(inj.get("poll_ms", 100)),
            "lock_ms": int(inj.get("lock_ms", 7000)),
            "verify_ms": int(inj.get("verify_ms", 2000)),
            "stale_ms": int(inj.get("stale_ms", 30000)),
            "min_balance": int(inj.get("min_balance", 1000)),
            # The page draws the reminder bubble itself, so it needs the same
            # thresholds the daemon logs against.
            "alert_low": self.alert_low,
            "alert_critical": self.alert_critical,
        }

    @property
    def alert_low(self):
        return int(self.get("alerts", "low", 2000) or 0)

    @property
    def alert_critical(self):
        return int(self.get("alerts", "critical", 1000) or 0)

    @property
    def alert_bubble(self):
        return bool(self.get("alerts", "bubble", True))

    @property
    def auto_open(self):
        return bool(self.get("inject", "enabled", True))

    @property
    def alert_animation(self):
        return bool(self.get("alerts", "animation", True))

    @property
    def panel_keep_open(self):
        return bool(self.get("panel", "keep_open", True))

    @property
    def room_history(self):
        return max(1, int(self.get("rooms", "history", 20)))

    @property
    def preferences(self):
        return {"auto_open": self.auto_open, "bubble": self.alert_bubble,
                "animation": self.alert_animation, "keep_panel_open": self.panel_keep_open}

    @property
    def heartbeat(self):
        return int(self.get("daemon", "heartbeat", 300))

    @property
    def max_runtime(self):
        return int(self.get("daemon", "max_runtime", 0))

    @property
    def watchdog_min(self):
        return int(self.get("daemon", "watchdog_min", 1) or 0)

    @property
    def log_max_bytes(self):
        return int(self.get("log", "max_bytes", 1048576))

    @property
    def log_keep(self):
        return int(self.get("log", "keep", 3))


def ensure_config():
    """Create config.toml from the template on first run. Returns True if created."""
    ROOT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        return False
    CONFIG_PATH.write_text(DEFAULT_TOML, encoding="utf-8")
    return True


def load():
    ensure_config()
    try:
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit("cannot read %s: %s" % (CONFIG_PATH, exc))
    return Config(data)


PREFERENCE_KEYS = {"auto_open": ("inject", "enabled"),
                   "bubble": ("alerts", "bubble"),
                   "animation": ("alerts", "animation"),
                   "keep_panel_open": ("panel", "keep_open")}


def with_preferences(cfg, preferences):
    """Copy only the live settings; connection and timing changes need restart."""
    data = copy.deepcopy(cfg.data)
    for name, value in preferences.items():
        if name not in PREFERENCE_KEYS or type(value) is not bool:
            raise ValueError("invalid Plus preference")
        section, key = PREFERENCE_KEYS[name]
        data.setdefault(section, {})[key] = value
    return Config(data)


def save_preferences(preferences, path=None):
    """Atomically update whitelisted booleans, preserving other TOML values."""
    path = Path(path) if path is not None else CONFIG_PATH
    with_preferences(Config({}), preferences)  # validate before reading/writing
    text = path.read_text(encoding="utf-8")
    expected = tomllib.loads(text)
    sections = re.compile(r"(?m)^[ \t]*\[([a-zA-Z0-9_]+)\][ \t]*(?:#[^\n]*)?$")
    for name, value in preferences.items():
        section, key = PREFERENCE_KEYS[name]
        expected.setdefault(section, {})[key] = value
        headers = list(sections.finditer(text))
        header = next((m for m in headers if m.group(1) == section), None)
        line = "%s = %s" % (key, "true" if value else "false")
        if header is None:
            text = text.rstrip() + "\n\n[%s]\n%s\n" % (section, line)
            continue
        end = next((m.start() for m in headers if m.start() > header.start()), len(text))
        body = text[header.end():end]
        pattern = r"(?m)^([ \t]*)%s[ \t]*=[^\n]*$" % re.escape(key)
        if re.search(pattern, body):
            body = re.sub(pattern, lambda m: m.group(1) + line, body, count=1)
        else:
            body = body.rstrip() + "\n" + line + "\n\n"
        text = text[:header.end()] + body + text[end:]
    if tomllib.loads(text) != expected:
        raise ValueError("unsupported TOML layout; configuration was not changed")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".plus-config-", suffix=".tmp", delete=False) as f:
            temporary = Path(f.name)
            f.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return Config(expected)
