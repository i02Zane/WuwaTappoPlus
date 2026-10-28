"""Configuration: defaults, TOML loading, and project paths."""

import copy
import os
import re
import tempfile
import tomllib
from pathlib import Path

from . import __version__
from .paths import DATA_DIR, SCRIPTS_DIR

# Page timings describe the app, not the user. They used to be config keys;
# a leftover value in config.toml is ignored on purpose so an old file cannot
# silently change how the page behaves.
POLL_MS = 1000
VERIFY_MS = 2000
STALE_MS = 30000

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
# Do not attempt a click below this character balance. The balance reminder
# tiers are derived from it: low = min_balance + 1000, critical = min_balance.
min_balance = 1000

[clicks]
# Pause new native clicks and the dedicated Socket.IO click report.
pause = false
# Send one random equipped emote after each native tap.
auto_emote = false

[alerts]
# Enable low-balance reminders independently of bubble visibility.
balance = true
# The reminder tiers follow [inject] min_balance: low is min_balance + 1000 and
# critical is min_balance itself, so "not enough to open" is the critical tier.
# Each tier fires once when the balance drops below it and re-arms after you
# type your way back above.
# Show Plus bubbles, including balance reminders and black room mode.
bubble = true
# Animate the reminder bubble. False keeps a static reminder.
animation = true

[panel]
# Keep the main panel open when clicking outside; manual close still works.
keep_open = true
# Remember which monitor the pet is on and put it back after the app restarts.
remember_screen = true

[rooms]
# How many visited rooms the multiplayer tab remembers. Pinned rooms are kept
# on top and do not count against this limit.
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
    "inject": {"enabled": True, "min_balance": 1000},
    "clicks": {"pause": False, "auto_emote": False},
    "alerts": {"balance": True, "bubble": True, "animation": True},
    "panel": {"keep_open": True, "remember_screen": True},
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
        return {
            "version": __version__,
            "enabled": self.auto_open,
            "pause_clicks": self.pause_clicks,
            "auto_emote": self.auto_emote,
            "balance_reminder": self.balance_reminder,
            "bubble": self.alert_bubble,
            "animation": self.alert_animation,
            # Timings are properties of the app, not preferences: they are
            # constants now, and a leftover key in config.toml is ignored.
            "poll_ms": POLL_MS,
            "verify_ms": VERIFY_MS,
            "stale_ms": STALE_MS,
            "min_balance": self.min_balance,
            # The page draws the reminder bubble itself, so it needs the same
            # thresholds the daemon logs against.
            "alert_low": self.alert_low,
            "alert_critical": self.alert_critical,
        }

    @property
    def min_balance(self):
        """The one click-threshold left configurable; the reminder tiers follow it."""
        return int(self.get("inject", "min_balance", 1000))

    @property
    def alert_low(self):
        return self.min_balance + 1000

    @property
    def alert_critical(self):
        return self.min_balance

    @property
    def balance_reminder(self):
        return bool(self.get("alerts", "balance", True))

    @property
    def alert_bubble(self):
        return bool(self.get("alerts", "bubble", True))

    @property
    def auto_open(self):
        return bool(self.get("inject", "enabled", True))

    @property
    def pause_clicks(self):
        return bool(self.get("clicks", "pause", False))

    @property
    def auto_emote(self):
        return bool(self.get("clicks", "auto_emote", False))

    @property
    def alert_animation(self):
        return bool(self.get("alerts", "animation", True))

    @property
    def panel_keep_open(self):
        return bool(self.get("panel", "keep_open", True))

    @property
    def panel_remember_screen(self):
        return bool(self.get("panel", "remember_screen", True))

    @property
    def room_history(self):
        return max(1, int(self.get("rooms", "history", 20)))

    @property
    def preferences(self):
        return {"auto_open": self.auto_open, "balance_reminder": self.balance_reminder,
                "bubble": self.alert_bubble,
                "animation": self.alert_animation, "keep_panel_open": self.panel_keep_open,
                "remember_screen": self.panel_remember_screen,
                "pause_clicks": self.pause_clicks, "auto_emote": self.auto_emote}

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


# ------------------------------------------------------------- reconcile

_SECTION_HEADER = re.compile(r"^[ \t]*\[([A-Za-z0-9_]+)\][ \t]*(?:#[^\n]*)?$")
_KEY_LINE = re.compile(r"^[ \t]*([A-Za-z0-9_]+)[ \t]*=")


def _parse_config(text):
    """Split TOML text into a preamble and sections that keep their raw lines."""
    preamble = []
    sections = []
    current = None
    for line in text.splitlines():
        header = _SECTION_HEADER.match(line)
        if header:
            comments = []
            holder = current["lines"] if current is not None else preamble
            while holder and holder[-1].lstrip().startswith("#"):
                comments.insert(0, holder.pop())
            current = {"name": header.group(1), "comments": comments,
                       "header": line, "lines": []}
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current["lines"].append(line)
    return preamble, sections


def _groups(lines):
    """Attach the comment block above each key to that key."""
    groups = []
    pending = []
    for line in lines:
        key = _KEY_LINE.match(line)
        if key:
            groups.append((key.group(1), pending + [line]))
            pending = []
        else:
            pending.append(line)
    if pending:
        groups.append((None, pending))
    return groups


def _clean_section(lines, allowed, template_groups):
    """Keep live keys, drop retired ones with their comments, append new ones."""
    kept = []
    present = set()
    tail = []
    for key, block in _groups(lines):
        if key is None:
            tail = block
        elif key in allowed:
            kept.extend(block)
            present.add(key)
    for key, block in template_groups:
        if key is not None and key not in present:
            kept.extend(block)
    return kept + tail


def _reconcile(text):
    template = tomllib.loads(DEFAULT_TOML)
    current = tomllib.loads(text)
    preamble, sections = _parse_config(text)
    _, template_sections = _parse_config(DEFAULT_TOML)

    # What the file must contain afterwards: template shape, current values.
    expected = {}
    for section in template_sections:
        name = section["name"]
        expected[name] = {key: current.get(name, {}).get(key, value)
                          for key, value in template[name].items()}

    output = list(preamble)
    seen = set()
    for section in sections:
        name = section["name"]
        output.extend(section["comments"])
        output.append(section["header"])
        if name not in template:
            # A section this program does not know: leave it exactly as it is.
            output.extend(section["lines"])
            continue
        seen.add(name)
        groups = _groups(next(item["lines"] for item in template_sections
                              if item["name"] == name))
        output.extend(_clean_section(section["lines"], template[name], groups))

    for section in template_sections:
        if section["name"] in seen:
            continue
        if output and output[-1].strip():
            output.append("")
        output.extend(section["comments"])
        output.append(section["header"])
        output.extend(section["lines"])

    new_text = "\n".join(output).rstrip("\n") + "\n"
    parsed = tomllib.loads(new_text)
    for name, values in expected.items():
        if parsed.get(name) != values:
            raise ValueError("configuration layout is not supported; not rewritten")
    return new_text


def sync_config(path=None):
    """Bring config.toml in line with DEFAULT_TOML, keeping what is still used.

    Values and comments of live keys are left untouched, retired keys are
    removed with their comment block, and missing keys are appended with the
    template's comment and default. Unknown sections are preserved. Returns
    True when the file was rewritten.
    """
    path = Path(path) if path is not None else CONFIG_PATH
    text = path.read_text(encoding="utf-8-sig")
    updated = _reconcile(text)
    if updated == text:
        return False
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".plus-config-", suffix=".tmp",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(updated)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return True


def load():
    ensure_config()
    # utf-8-sig, not utf-8: Windows editors and PowerShell's Set-Content happily
    # add a BOM, and tomllib rejects it as "Invalid statement (at line 1)".
    try:
        data = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit("cannot read %s: %s" % (CONFIG_PATH, exc))
    cfg = Config(data)
    # Best effort: a file this program cannot safely rewrite is left alone.
    try:
        sync_config()
    except (OSError, ValueError):
        pass
    return cfg


PREFERENCE_KEYS = {"auto_open": ("inject", "enabled"),
                   "balance_reminder": ("alerts", "balance"),
                   "bubble": ("alerts", "bubble"),
                   "animation": ("alerts", "animation"),
                   "keep_panel_open": ("panel", "keep_open"),
                   "remember_screen": ("panel", "remember_screen"),
                   "pause_clicks": ("clicks", "pause"),
                   "auto_emote": ("clicks", "auto_emote")}


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
    # Reading with utf-8-sig drops a BOM; writing below always produces none.
    text = path.read_text(encoding="utf-8-sig")
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
