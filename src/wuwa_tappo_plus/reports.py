"""Daily keyboard/mouse totals from Wuwa Tappo's cumulative input counter."""

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .config import ROOT


class DailyReport:
    def __init__(self, log, path=None, now=None):
        self.log = log
        self.now = now or datetime.now
        # Keep the old opening-click database separate; never reinterpret it.
        self.path = Path(path) if path is not None else ROOT / "reports" / "input.sqlite3"
        self.connection = None
        self.error = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=1)
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS input_days (
                    day TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS input_baseline (
                    id INTEGER PRIMARY KEY CHECK (id=1), account TEXT NOT NULL,
                    total INTEGER NOT NULL, day TEXT NOT NULL, seen REAL NOT NULL
                );
            """)
        except (OSError, sqlite3.Error) as exc:
            self._fail(exc)

    def _fail(self, exc):
        self.error = str(exc)
        self.log.warn("daily input statistics unavailable: " + self.error[:160])

    def observe(self, snapshot):
        if self.error or not self.connection or not snapshot:
            return
        total = snapshot.get("input_total")
        account = snapshot.get("input_account")
        if type(total) is not int or not 0 <= total <= 9007199254740991 or not account:
            return
        # Persist no keystrokes, button names, or raw account identifiers.
        account = hashlib.sha256(str(account).encode("utf-8")).hexdigest()
        now = self.now()
        day = now.date().isoformat()
        try:
            with self.connection:
                baseline = self.connection.execute(
                    "SELECT account, total, day, seen FROM input_baseline WHERE id=1"
                ).fetchone()
                delta = 0
                if baseline and baseline[0] == account and total >= baseline[1]:
                    # A continuous midnight sample is attributed to the new day.
                    # A gap across dates cannot be allocated reliably.
                    if baseline[2] == day or 0 <= now.timestamp() - baseline[3] <= 5:
                        delta = total - baseline[1]
                self.connection.execute("INSERT OR IGNORE INTO input_days VALUES (?, 0)", (day,))
                if delta:
                    self.connection.execute("UPDATE input_days SET count=count+? WHERE day=?",
                                            (delta, day))
                self.connection.execute(
                    "INSERT OR REPLACE INTO input_baseline VALUES (1, ?, ?, ?, ?)",
                    (account, total, day, now.timestamp())
                )
        except (OSError, ValueError, OverflowError, sqlite3.Error) as exc:
            self._fail(exc)

    def yesterday(self):
        today = self.now().date()
        yesterday = (today - timedelta(days=1)).isoformat()
        report = {"available": not bool(self.error) and self.connection is not None,
                  "yesterday_date": yesterday, "yesterday_count": None, "days": []}
        if not report["available"]:
            return report
        try:
            counts = dict(self.connection.execute(
                "SELECT day, count FROM input_days WHERE day >= ?",
                ((today - timedelta(days=6)).isoformat(),)
            ))
            report["days"] = [{"date": (today - timedelta(days=i)).isoformat(),
                               "count": counts.get((today - timedelta(days=i)).isoformat())}
                              for i in range(7)]
            report["yesterday_count"] = counts.get(yesterday)
            return report
        except sqlite3.Error as exc:
            self._fail(exc)
            report["available"] = False
            return report

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
