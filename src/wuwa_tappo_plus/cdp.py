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


class StaleResponse(CDPError):
    """A replaced connection/document must not supply data to its successor."""


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

    def __init__(self, port, host="127.0.0.1", timeout=20.0, *, discovery=None):
        self.port = port
        self.host = host
        self.timeout = timeout
        self.ws = None
        self.target = None
        self.seq = 0
        self.discovery = discovery
        self.generation = 0
        self.document_epoch = 0
        self._explicit_target = False

    def connect(self):
        self.close()
        self._explicit_target = False
        candidates = (self.discovery.targets() if self.discovery is not None
                      else list_targets(self.port, self.host))
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
                self._call("Runtime.enable", {})
                verdict = self.js(PROBE_JS)
            except (CDPError, WSError, OSError) as exc:
                last_err = exc
                self.close()
                continue
            if verdict == "ok":
                return True
            last_err = CDPError("probe on %s returned %r" % (t.get("url"), verdict))
            self.close()

        raise CDPError("no usable target: %s" % last_err)

    def js(self, expr, await_promise=True):
        identity = (self.ws, self.generation, self.document_epoch)
        try:
            return self._js(expr, await_promise)
        except (WSError, OSError) as exc:
            if identity[0] is not None and identity != (self.ws, self.generation, self.document_epoch):
                raise StaleResponse("discarded failure from a replaced connection") from exc
            if isinstance(exc, WSError):
                raise CDPError("WebSocket disconnected: %s" % exc) from exc
            raise

    def connect_target(self, target):
        """Connect to an explicitly selected auxiliary window, without probing the pet."""
        self.close()
        self._explicit_target = True
        try:
            self.ws = WebSocket(target["webSocketDebuggerUrl"], timeout=self.timeout)
            self.target = target
            self._call("Runtime.enable", {})
        except (WSError, OSError, CDPError) as exc:
            self.close()
            raise CDPError("cannot connect auxiliary window: %s" % exc) from exc

    def _js(self, expr, await_promise=True):
        result = self._call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": await_promise})
        if "exceptionDetails" in result:
            detail = json.dumps(result["exceptionDetails"], ensure_ascii=False)
            raise CDPError("JS exception: " + detail[:300])
        return result.get("result", {}).get("value")

    def _call(self, method, params):
        if self.ws is None and self._explicit_target:
            raise CDPError("auxiliary page is not connected")
        if self.ws is None and not self.connect():
            raise CDPError("not connected")
        ws, generation, document_epoch = self.ws, self.generation, self.document_epoch
        self.seq += 1
        mid = self.seq
        ws.send(json.dumps({
            "id": mid,
            "method": method,
            "params": params,
        }))
        while True:
            raw = ws.recv()
            if (self.ws is not ws or self.generation != generation
                    or self.document_epoch != document_epoch):
                raise StaleResponse("discarded response from a replaced connection")
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("method") == "Runtime.executionContextsCleared":
                self.invalidate_document()
                if method == "Runtime.enable":
                    document_epoch = self.document_epoch
                    continue
                raise StaleResponse("page context was cleared during evaluation")
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise CDPError("CDP command failed: %s" % method)
            return msg.get("result", {})

    def state(self):
        raw = self.js(STATE_JS)
        if not raw:
            raise CDPError("empty state payload")
        data = json.loads(raw)
        if not data.get("ok"):
            raise CDPError("state read failed: %s" % data.get("err"))
        return data

    def invalidate_document(self):
        self.document_epoch += 1

    def close(self):
        ws, self.ws = self.ws, None
        self.target = None
        self.generation += 1
        self.invalidate_document()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
