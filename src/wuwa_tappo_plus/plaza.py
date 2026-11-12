"""Event-driven page bindings and bounded HTTPS work for the plaza."""

import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .cdp_events import EventSession
from .paths import SCRIPTS_DIR
from .plaza_client import PlazaClient, PlazaError
from .pages import connection_key

REQUEST_BINDING = "__wutPlazaRequest"
STATE_BINDING = "__wutPlazaState"
ACTION_BINDING = "__wutPlazaAction"


class PlazaBridge:
    def __init__(self, log):
        self.log = log
        self.commands = deque(maxlen=20)
        self.state = {"error": "广场正在连接…", "logged": None, "owner": False,
                      "published": False, "items": [], "page": 0, "hasMore": False}
        path = SCRIPTS_DIR / "plaza-client.bundle.js"
        self.source = path.read_text(encoding="utf-8") if path.exists() else ""
        self.client = PlazaClient()
        try:
            config = json.loads((SCRIPTS_DIR / "plaza-config.json").read_text(encoding="utf-8"))
            self.client = PlazaClient(config["base"])
        except (OSError, ValueError, KeyError, TypeError):
            self.source = ""
        self.native = None
        self.panel = None
        self.instance = None
        self._native_source_key = None
        self._panel_source_key = None
        self._native_document_epoch = None
        self._panel_document_epoch = None
        self.last_error = None
        self.closed = False
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(128)
        self.events = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plaza-events")
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plaza-http")
        self.futures = {}
        self.sent_times = deque()

    def _warn(self, error):
        name = type(error).__name__
        if name != self.last_error:
            self.log.warn("plaza: " + name)
            self.last_error = name

    def _enqueue(self, function, *args):
        if self.closed or not self.slots.acquire(blocking=False):
            return
        def run():
            try:
                with self.lock:
                    if not self.closed:
                        function(*args)
            except Exception as error:
                self._warn(error)
            finally:
                self.slots.release()
        try:
            self.events.submit(run)
        except RuntimeError:
            self.slots.release()

    def _event(self, channel, params):
        payload = params.get("payload")
        if params.get("name") not in (REQUEST_BINDING, STATE_BINDING, ACTION_BINDING):
            return
        if not isinstance(payload, str) or len(payload) > 32768:
            return
        epoch = params.get("_document_epoch", channel.document_epoch)
        self._enqueue(self._binding, channel, params["name"], payload, epoch)

    def _binding(self, channel, name, raw, epoch):
        if not channel.alive() or channel.document_epoch != epoch:
            return
        data = json.loads(raw)
        if not isinstance(data, dict):
            return
        if channel is self.panel and name == ACTION_BINDING:
            self.queue(data)
        elif channel is self.native and data.get("instance") == self.instance:
            if name == STATE_BINDING and isinstance(data.get("state"), dict):
                self.state = data["state"]
                self._push()
            elif name == REQUEST_BINDING:
                self._request(channel, data)

    def queue(self, request):
        op, page = request.get("op"), request.get("page", 0)
        refresh = request.get("refresh", True)
        if (op not in ("list", "publish", "withdraw", "reset") or len(self.commands) >= 20
                or not isinstance(request.get("id"), str) or len(request["id"]) > 128
                or type(page) is not int or not 0 <= page <= 49 or type(refresh) is not bool):
            return False
        with self.lock:
            if op == "list" and refresh and self.state.get("nextRefreshAt", 0) > time.time() * 1000:
                return True
            command = {"id": request["id"], "op": op, "page": page, "refresh": refresh}
            self.state = {**self.state, "loading" if op == "list" else "busy": True}
            if self.native is not None and self.native.alive():
                self.native.js("window.__WUT_PLAZA__.command(%s);1" % json.dumps(command))
            else:
                self.commands.append(command)
            return True

    def _perform(self, item):
        try:
            return {"id": item["id"], "data": self.client.send(item)}
        except PlazaError as error:
            return {"id": item["id"], "error": {"code": error.code, "retry": error.retry}}
        except Exception:
            return {"id": item["id"], "error": {"code": "CONNECTION_FAILED", "retry": 30}}

    def _request(self, channel, data):
        key = data.get("id")
        if not isinstance(key, str) or len(key) > 128:
            return
        now = time.monotonic()
        while self.sent_times and now - self.sent_times[0] >= 60:
            self.sent_times.popleft()
        identity = (channel, channel.document_epoch, self.instance, key)
        if identity in self.futures:
            return
        if len(self.futures) >= 4 or len(self.sent_times) >= 30:
            self._reply(channel, self.instance, {"id": key, "error": {"code": "RATE_LIMITED", "retry": 30}})
            return
        self.sent_times.append(now)
        item = {"id": key, "op": data.get("op"), "args": data.get("args")}
        future = self.pool.submit(self._perform, item)
        self.futures[identity] = future
        future.add_done_callback(lambda result: self._enqueue(self._finish, identity, result))

    def _finish(self, identity, future):
        self.futures.pop(identity, None)
        channel, epoch, instance, _ = identity
        self._reply(channel, instance, future.result(), epoch=epoch)

    def _reply(self, channel, instance, result, *, epoch=None):
        # A result from a disposed page cannot reach its replacement.
        if (channel is self.native and instance == self.instance and channel.alive()
                and (epoch is None or epoch == channel.document_epoch)):
            channel.js("if(window.__WUT_PLAZA__&&window.__WUT_PLAZA__.instance===%s)"
                       "window.__WUT_PLAZA__.complete([%s]);1" % (json.dumps(instance), json.dumps(result)))

    def _push(self):
        if self.panel is not None and self.panel.alive():
            self.panel.js("if(window.__WUT_ROOMS__)window.__WUT_ROOMS__.update(%s);1"
                          % json.dumps({"plaza": self.state}, ensure_ascii=False))

    def bind_panel(self, session):
        target = session.target
        if not target or not target.get("webSocketDebuggerUrl"):
            return
        identity = connection_key(session)
        with self.lock:
            if (self.panel is not None and self._panel_source_key == identity
                    and self.panel.alive()
                    and self.panel.document_epoch == self._panel_document_epoch):
                return
            if self.panel is not None:
                self.panel.close()
            self.panel = EventSession(target, self._event)
            try:
                self.panel.call("Runtime.enable")
                self.panel.bind(ACTION_BINDING)
                self._panel_source_key = identity
                self._panel_document_epoch = self.panel.document_epoch
                self._push()
            except Exception:
                self.panel.close()
                self.panel = None
                raise

    def sync(self, session):
        # This normal daemon tick discovers rebuilt pages only. User actions,
        # HTTP completions and state delivery run through binding callbacks.
        if not self.source:
            self.state["error"] = "广场资源未构建，请运行 npm run build:plaza。"
            return
        target = session.target
        if not target or not target.get("webSocketDebuggerUrl"):
            return
        identity = connection_key(session)
        try:
            with self.lock:
                if (self.native is None or self._native_source_key != identity
                        or not self.native.alive()
                        or self.native.document_epoch != self._native_document_epoch):
                    if self.native is not None:
                        self.native.close()
                    self.native = EventSession(target, self._event)
                    self.native.call("Runtime.enable")
                    self.native.bind(REQUEST_BINDING)
                    self.native.bind(STATE_BINDING)
                    self.instance = None
                    self._native_source_key = identity
                    self._native_document_epoch = self.native.document_epoch
                current = self.native.js("window.__WUT_PLAZA__&&window.__WUT_PLAZA__.protocol===3"
                                         "&&window.__WUT_PLAZA__.version===%s?window.__WUT_PLAZA__.instance:null" % json.dumps(__version__))
                if current is None or self.instance is None:
                    self.instance = self.native.js(self.source + ";__WUT_PLAZA_CLIENT__.start(%s);"
                                                   "window.__WUT_PLAZA__.resend();window.__WUT_PLAZA__.instance" % json.dumps(__version__))
                elif current != self.instance:
                    self.instance = current
                    self.native.js("window.__WUT_PLAZA__.resend();1")
                if self.commands:
                    commands = list(self.commands)
                    self.native.js("%s.forEach(function(c){window.__WUT_PLAZA__.command(c)});1" % json.dumps(commands))
                    self.commands.clear()
        except Exception as error:
            with self.lock:
                if self.native is not None:
                    self.native.close()
                    self.native = None
            self.state = {**self.state, "error": "广场暂时无法连接，请稍后重试。"}
            self._warn(error)

    def close(self, session=None):
        with self.lock:
            self.closed = True
            if self.native is not None:
                try:
                    self.native.js("if(window.__WUT_PLAZA__)window.__WUT_PLAZA__.uninstall();1")
                except Exception:
                    pass
                self.native.close()
            if self.panel is not None:
                self.panel.close()
            self.native = self.panel = None
        self.events.shutdown(wait=False, cancel_futures=True)
        self.pool.shutdown(wait=False, cancel_futures=True)
