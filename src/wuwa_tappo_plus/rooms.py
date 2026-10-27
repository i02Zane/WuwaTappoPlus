"""Remember the rooms visited in the multiplayer tab so one click can return."""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import __version__, cdp
from .config import ROOT
from .paths import SCRIPTS_DIR

# Room numbers are short alphanumeric tokens (seen: 8 characters, mixed case).
# Anything else in the DOM is not an id and must not reach the database.
ROOM_ID = re.compile(r"^[0-9A-Za-z]{4,16}$")
OWNER_MAX = 32
# A room that stays on screen only refreshes last_seen this often; writing the
# row on every poll would turn a 1 Hz loop into a 1 Hz disk write.
TOUCH_SECONDS = 60


def clean_room_id(value):
    text = str(value or "").strip()
    return text if ROOM_ID.match(text) else ""


def clean_owner(value):
    return " ".join(str(value or "").split())[:OWNER_MAX]


class RoomHistory:
    """Room number, host name, visit count and the last time it was seen."""

    def __init__(self, log, path=None, now=None, limit=20):
        self.log = log
        self.now = now or datetime.now
        self.limit = max(1, int(limit))
        self.path = Path(path) if path is not None else ROOT / "reports" / "rooms.sqlite3"
        self.connection = None
        self.error = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=1)
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL DEFAULT '',
                    visits INTEGER NOT NULL DEFAULT 0,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
            """)
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)

    def _fail(self, exc):
        self.error = str(exc)
        self.log.warn("room history unavailable: " + self.error[:160])

    def _stamp(self):
        return self.now().isoformat(timespec="seconds")

    def note(self, room_id, owner=""):
        """Record a stay in a room. Returns the id when it was stored."""
        return self._store(room_id, owner, again=True)

    def resume(self, room_id, owner=""):
        """Adopt a room that was already open before we started watching.

        A daemon restart, a panel reload or a reconnect is not another visit;
        only a transition from "no room" to "in a room" is.
        """
        return self._store(room_id, owner, again=False)

    def _store(self, room_id, owner, again):
        room_id = clean_room_id(room_id)
        if not self.connection or self.error or not room_id:
            return ""
        owner = clean_owner(owner)
        stamp = self._stamp()
        try:
            with self.connection:
                row = self.connection.execute(
                    "SELECT owner FROM rooms WHERE room_id=?", (room_id,)
                ).fetchone()
                if row is None:
                    # Unknown room: it was still a stay, just an unwatched one.
                    self.connection.execute(
                        "INSERT INTO rooms VALUES (?, ?, 1, ?, ?)",
                        (room_id, owner, stamp, stamp),
                    )
                else:
                    # The host can rename or the room can change hands; trust the
                    # name on screen now, but keep the old one if it is empty.
                    self.connection.execute(
                        "UPDATE rooms SET visits=visits+?, last_seen=?, owner=? WHERE room_id=?",
                        (1 if again else 0, stamp, owner or row[0], room_id),
                    )
                self.connection.execute(
                    "DELETE FROM rooms WHERE room_id NOT IN "
                    "(SELECT room_id FROM rooms ORDER BY last_seen DESC, rowid DESC LIMIT ?)",
                    (self.limit,),
                )
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)
            return ""
        return room_id

    def set_owner(self, room_id, owner):
        """Fill in a host name that was not on screen yet. No new visit."""
        room_id = clean_room_id(room_id)
        owner = clean_owner(owner)
        if not self.connection or self.error or not room_id or not owner:
            return False
        try:
            with self.connection:
                self.connection.execute(
                    "UPDATE rooms SET owner=? WHERE room_id=?", (owner, room_id))
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)
            return False
        return True

    def touch(self, room_id):
        """Refresh last_seen without counting another visit."""
        room_id = clean_room_id(room_id)
        if not self.connection or self.error or not room_id:
            return False
        try:
            with self.connection:
                self.connection.execute(
                    "UPDATE rooms SET last_seen=? WHERE room_id=?", (self._stamp(), room_id)
                )
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)
            return False
        return True

    def forget(self, room_id):
        room_id = clean_room_id(room_id)
        if not self.connection or self.error or not room_id:
            return False
        try:
            with self.connection:
                self.connection.execute("DELETE FROM rooms WHERE room_id=?", (room_id,))
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)
            return False
        return True

    def recent(self):
        if not self.connection or self.error:
            return []
        try:
            rows = self.connection.execute(
                "SELECT room_id, owner, visits, last_seen FROM rooms "
                "ORDER BY last_seen DESC, rowid DESC LIMIT ?",
                (self.limit,),
            ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)
            return []
        return [{"room_id": row[0], "owner": row[1], "visits": row[2], "last_seen": row[3]}
                for row in rows]

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None


READ_JS = """JSON.stringify(window.__WUT_ROOMS__ ? {
  version: window.__WUT_ROOMS__.version,
  current: window.__WUT_ROOMS__.current,
  requests: window.__WUT_ROOMS__.requests,
  error: window.__WUT_ROOMS__.error
} : null)"""


class RoomBridge:
    """Keep the room panel's history list in step with the local database.

    The panel decides everything it can see on its own: it parses the room
    number, renders the list and performs the join by filling the real input
    and clicking the real button. This side only stores what the panel reports
    and hands the list back, so a stale daemon cannot block a join.
    """

    def __init__(self, port, host, log):
        self.port = port
        self.host = host
        self.log = log
        self.session = None
        self.last_scan = -10.0
        self.source = (SCRIPTS_DIR / "rooms.js").read_text(encoding="utf-8").replace(
            "__VERSION__", json.dumps(__version__))
        self.current = None
        self.owner = ""
        self.touched = 0.0
        self.seen = False
        self.last_error = None

    def sync(self, history, clock=None):
        clock = clock or (lambda: datetime.now().timestamp())
        try:
            if clock() - self.last_scan >= 3:
                self.last_scan = clock()
                target = self._find_panel()
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
                return
            state = self._inject_if_needed()
            if not state:
                return
            ack = self._apply_requests(history, state.get("requests") or [])
            self._observe(history, state.get("current"), clock)
            payload = json.dumps({"rooms": history.recent(), "ack": ack}, ensure_ascii=False)
            self.session.js("window.__WUT_ROOMS__.update(%s); 1" % payload)
        except (cdp.CDPError, OSError, ValueError, TypeError) as exc:
            self._warn(exc)
            self.close()

    def _find_panel(self):
        for target in cdp.list_targets(self.port, self.host, timeout=2):
            url = target.get("url", "")
            if url.startswith("http://tauri.localhost/index.html") and url.endswith("#/panel"):
                return target
        return None

    def _inject_if_needed(self):
        raw = self.session.js(READ_JS)
        state = json.loads(raw) if raw else None
        if state and state.get("version") == __version__:
            return state
        if state is not None:
            self.session.js("if(window.__WUT_ROOMS__&&window.__WUT_ROOMS__.uninstall)"
                            "window.__WUT_ROOMS__.uninstall(); 1")
        self.log.info("rooms -> %s" % self.session.js(self.source))
        return json.loads(self.session.js(READ_JS) or "null")

    @staticmethod
    def _apply_requests(history, requests):
        ack = []
        for request in requests[:10]:
            if not isinstance(request, dict) or not isinstance(request.get("id"), str):
                continue
            result = {"id": request["id"]}
            if request.get("action") != "forget" or not history.forget(request.get("room_id")):
                result["error"] = True
            ack.append(result)
        return ack

    def _observe(self, history, current, clock):
        current = current if isinstance(current, dict) else {}
        first = not self.seen
        self.seen = True
        room_id = clean_room_id(current.get("room_id"))
        if not room_id:
            self.current = None
            self.owner = ""
            return
        owner = clean_owner(current.get("owner"))
        stamp = clock()
        if self.current != room_id:
            self.current = room_id
            self.owner = owner
            self.touched = stamp
            # Whatever is on screen in the first reading was already open before
            # we started, so it refreshes the row instead of inventing a visit.
            stored = history.resume(room_id, owner) if first else history.note(room_id, owner)
            if stored:
                self.log.info("room %s host=%s %s"
                              % (room_id, owner or "-", "resumed" if first else "recorded"))
            return
        if owner and owner != self.owner:
            # The member list renders a beat after the room number, so the first
            # reading often has no host name to record.
            self.owner = owner
            history.set_owner(room_id, owner)
            return
        if stamp - self.touched >= TOUCH_SECONDS:
            self.touched = stamp
            history.touch(room_id)

    def _warn(self, exc):
        message = str(exc)
        if message != self.last_error:
            self.log.warn("rooms: " + message[:200])
            self.last_error = message

    def close(self):
        if self.session:
            try:
                self.session.js(
                    "if(window.__WUT_ROOMS__&&window.__WUT_ROOMS__.uninstall)"
                    "window.__WUT_ROOMS__.uninstall(); 1")
            except (cdp.CDPError, OSError):
                pass
            self.session.close()
            self.session = None
        # current/owner survive a reconnect on purpose: coming back to the same
        # room must not look like a new stay.
        self.owner = ""
