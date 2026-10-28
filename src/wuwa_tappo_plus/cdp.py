"""Chrome DevTools Protocol client for the Wuwa Tappo WebView2 window.

The app runs seven WebView2 pages: the pet window at `http://tauri.localhost/#/`
plus six sub-pages under `index.html#/...` (about, panel, toast, login...).
Only the pet window carries the reward button, so target selection is ranked
and then confirmed by a probe instead of trusting the first match.
"""

import json
import socket
import urllib.error
import urllib.request

from .ws import WebSocket, WSError

PROBE_JS = r"""
(function(){
  try{
    var el = document.querySelector('#app');
    if (!el || !el.__vue_app__) return 'no-vue';
    var p = el.__vue_app__.config.globalProperties.$pinia;
    if (!p || !p._s || !p._s.get('GlobalState')) return 'no-store';
    return 'ok';
  }catch(e){ return 'err:' + e; }
})()
"""

STATE_JS = r"""
(function(){
  try{
    var app = document.querySelector('#app').__vue_app__;
    var st = app.config.globalProperties.$pinia._s.get('GlobalState').$state;
    var u = st.userInfo || {};
    var A = window.__WUT__ || {};
    var snap = A.snapshot ? A.snapshot() : null;
    return JSON.stringify({
      ok: true,
      now: Date.now(),
      left: snap ? snap.left : 0,
      right: snap ? snap.right : 0,
      click: u.total_click_count || 0,
      spend: u.total_click_consume || 0,
      // The one wallet reader lives in the injected script, so the CLI cannot
      // grow a second opinion about what the balance is. Null when the script
      // is not installed yet; the caller shows "-" rather than guessing.
      bal: (A.readBal ? A.readBal() : null),
      inject: A.v || 0,
      auto_open: A.cfg ? A.cfg.enabled !== false : null,
      block: A.block ? A.block.reason : null,
      nickname: u.nickname || ''
    });
  }catch(e){ return JSON.stringify({ok:false, err:String(e)}); }
})()
"""


class CDPError(Exception):
    pass


def _rank(url):
    """Higher is better. The pet window is the only one without `index.html`."""
    if not url:
        return 0
    if url.rstrip("/").endswith("#") or url.endswith("#/"):
        return 1000
    if "index.html#/panel" in url:
        return 200
    if "index.html" in url:
        return 100
    return 10


# The debug port only ever answers on loopback. An HTTP_PROXY in the
# environment would otherwise send 127.0.0.1 through the proxy and come back
# as a 502 that looks like a broken CDP endpoint.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get_json(url, timeout):
    with _OPENER.open(url, timeout=timeout) as r:
        return json.load(r)


def list_targets(port, host="127.0.0.1", timeout=5.0):
    url = "http://%s:%d/json/list" % (host, port)
    try:
        data = _get_json(url, timeout)
    except urllib.error.URLError as exc:
        raise CDPError("cannot reach CDP on port %d: %s" % (port, exc)) from exc
    pages = [t for t in data if t.get("type") == "page"]
    pages.sort(key=lambda t: _rank(t.get("url") or ""), reverse=True)
    return pages


def probe_port(port, host="127.0.0.1", timeout=3.0):
    url = "http://%s:%d/json/version" % (host, port)
    try:
        return _get_json(url, timeout)
    except Exception:
        return None


class Session:
    """One CDP connection bound to the pet window."""

    def __init__(self, port, host="127.0.0.1", timeout=20.0):
        self.port = port
        self.host = host
        self.timeout = timeout
        self.ws = None
        self.target = None
        self.seq = 0

    def connect(self):
        self.close()
        candidates = list_targets(self.port, self.host)
        if not candidates:
            raise CDPError("no page target found")

        last_err = None
        for t in candidates:
            try:
                ws = WebSocket(t["webSocketDebuggerUrl"], timeout=self.timeout)
            except (WSError, OSError, socket.error) as exc:
                last_err = exc
                continue
            self.ws = ws
            self.target = t
            try:
                verdict = self.js(PROBE_JS)
            except CDPError as exc:
                last_err = exc
                self.close()
                continue
            if verdict == "ok":
                return True
            last_err = CDPError("probe on %s returned %r" % (t.get("url"), verdict))
            self.close()

        raise CDPError("no usable target: %s" % last_err)

    def js(self, expr, await_promise=True):
        try:
            return self._js(expr, await_promise)
        except WSError as exc:
            raise CDPError("WebSocket disconnected: %s" % exc) from exc

    def connect_target(self, target):
        """Connect to an explicitly selected auxiliary window, without probing the pet."""
        self.close()
        try:
            self.ws = WebSocket(target["webSocketDebuggerUrl"], timeout=self.timeout)
        except (WSError, OSError) as exc:
            raise CDPError("cannot connect auxiliary window: %s" % exc) from exc
        self.target = target

    def _js(self, expr, await_promise=True):
        if self.ws is None and not self.connect():
            raise CDPError("not connected")
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({
            "id": mid,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
        }))
        while True:
            raw = self.ws.recv()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") != mid:
                continue
            result = msg.get("result", {})
            if "exceptionDetails" in result:
                detail = json.dumps(result["exceptionDetails"], ensure_ascii=False)
                raise CDPError("JS exception: " + detail[:300])
            return result.get("result", {}).get("value")

    def state(self):
        raw = self.js(STATE_JS)
        if not raw:
            raise CDPError("empty state payload")
        data = json.loads(raw)
        if not data.get("ok"):
            raise CDPError("state read failed: %s" % data.get("err"))
        return data

    def close(self):
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = None
        self.target = None
