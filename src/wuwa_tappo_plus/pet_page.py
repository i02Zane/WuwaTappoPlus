"""Main pet page injection, snapshots and accessory display synchronization."""

import json

from . import accessories, cdp
from .pages import connection_key
from .script_source import read_script

POLL_JS = r"""
(function(){
  var A = window.__WUT__;
  if (!A) return JSON.stringify({inject: 0});
  if (A.configure) A.configure(__LIVE_CFG__);
  A.beat = Date.now();
  var s = null;
  try { s = A.snapshot ? A.snapshot() : null; } catch (e) {}
  return JSON.stringify({
    inject: A.v,
    lowBalance: A.lowBalance,
    alert: A.alert || 0,
    retries: A.retries || 0,
    disabledHits: A.disabledHits || 0,
    block: A.block ? { slot: A.block.slot, reason: A.block.reason,
                       ts: A.block.ts, since: A.block.since,
                       balance: A.block.balance, attempts: A.block.attempts } : null,
    errors: (A.errors || []).slice(-5),
    clicks: (A.clicks || []).slice(-12),
    chestCache: A.chestCache || null,
    state: s
  });
})()
"""


def inject_source(cfg, chest_cache=None):
    values = dict(cfg.inject_cfg)
    if chest_cache is not None:
        values["chest_cache"] = chest_cache
    return read_script("inject.js").replace("__CFG__", json.dumps(values))


class PetPage:
    def __init__(self, session, log):
        self.session = session
        self.log = log
        self._accessory_key = None
        self._accessory_session = None

    def poll(self, cfg, chest_cache=None):
        poll = POLL_JS.replace("__LIVE_CFG__", json.dumps(cfg.inject_cfg))
        payload = json.loads(self.session.js(poll))
        want = cfg.inject_cfg["version"]
        if payload.get("inject") == want:
            return payload
        self.session.invalidate_document()
        # A new page can reuse the same CDP Session object. Its globals need
        # restoring even when the saved accessory file has not changed.
        self._accessory_session = None
        have = payload.get("inject")
        if have:
            self.log.info("replacing injection v%s with v%s" % (have, want))
            self.session.js("(function(){var A=window.__WUT__; if(A&&A.uninstall)A.uninstall(); return 1;})()")
        verdict = self.session.js(inject_source(cfg, chest_cache))
        self.log.info("inject -> %s" % verdict)
        payload = json.loads(self.session.js(poll))
        if payload.get("inject") != want:
            raise cdp.CDPError("injection did not take effect")
        return payload

    def apply_accessories(self, path):
        """Push saved accessory transforms to the pet page when they change."""
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            key = None
        session = self.session
        sid = connection_key(session)
        if key == self._accessory_key and sid == self._accessory_session:
            return
        overrides = accessories.load(path) if key is not None else {}
        session.js("window.__WUT_ACCESSORY_OVERRIDES__ = %s; 1"
                   % json.dumps(overrides, ensure_ascii=False))
        self._accessory_key = key
        self._accessory_session = sid

    def accessory_base(self):
        """Publish the pet's live accessory anchor so the preview matches it."""
        raw = self.session.js(
            "JSON.stringify((function(){var l=document.querySelector('.cat-stack__layer');"
            "var c=document.querySelector('.cat-mainbox');"
            "return {base:window.__WUT_ACCESSORY_BASE__||null,"
            "size:c?c.getBoundingClientRect().width:null,"
            "layout:c?c.offsetWidth:null};})())")
        data = json.loads(raw) if raw else {}
        base = data.get("base")
        if base and data.get("size"):
            base = dict(base, size=data["size"])
            # The pet draws at a fraction of its layout size; the preview has to
            # use the same factor or the offsets drift at any pet size but 100%.
            if data.get("layout"):
                base["layout"] = data["layout"]
        return base
