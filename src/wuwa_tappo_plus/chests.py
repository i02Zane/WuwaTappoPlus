"""Persist confirmed native chest opens for hidden-countdown reminders."""

import json
import os
import time
from pathlib import Path

from .config import ROOT


class ChestCache:
    def __init__(self, log, path=None):
        self.log = log
        self.path = Path(path) if path is not None else ROOT / "chests.json"
        self.data = {"account": None}
        try:
            self.data = self.clean(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def clean(value):
        now = time.time() * 1000
        if not isinstance(value, dict):
            return {"account": None}
        account = value.get("account")
        if not isinstance(account, str) or not account or len(account) > 256:
            return {"account": None}
        result = {"account": account}
        for slot in ("left", "right"):
            record = value.get(slot)
            if not isinstance(record, dict):
                continue
            opened, ready = record.get("opened_at"), record.get("ready_at")
            if (type(opened) in (int, float) and type(ready) in (int, float)
                    and 0 < opened <= now and now < ready <= now + 1800000
                    and ready - opened == 1800000):
                result[slot] = {"opened_at": opened, "ready_at": ready}
        return result

    def observe(self, value):
        if not isinstance(value, dict) or not value.get("account"):
            return
        data = self.clean(value)
        if data == self.data:
            return
        temporary = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
            self.data = data
        except OSError as exc:
            self.log.warn("chest cache: %s" % exc)
