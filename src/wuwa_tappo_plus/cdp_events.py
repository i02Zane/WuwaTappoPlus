"""CDP channel with an independent reader for page binding callbacks."""

import json
import queue
import select
import socket
import threading

from .cdp import CDPError
from .ws import WebSocket, WSError


class EventSession:
    def __init__(self, target, on_event, timeout=5):
        self.target = target
        self.timeout = timeout
        self.on_event = on_event
        self.ws = WebSocket(target["webSocketDebuggerUrl"], timeout=timeout)
        self.pending = {}
        self.context_independent = set()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.serial = 0
        self.generation = 0
        self.document_epoch = 0
        self._closed = False
        self.reader = threading.Thread(target=self._read, name="plaza-cdp", daemon=True)
        self.reader.start()

    def alive(self):
        return not self.stopped.is_set()

    def _read(self):
        try:
            while not self.stopped.is_set():
                if not select.select([self.ws.sock], [], [], 1)[0]:
                    continue
                message = json.loads(self.ws.recv())
                if "id" in message:
                    with self.lock:
                        waiter = self.pending.get(message["id"])
                    if waiter is not None:
                        try:
                            waiter.put_nowait(message)
                        except queue.Full:
                            pass  # A context reset/close already cancelled this call.
                elif message.get("method") == "Runtime.executionContextsCleared":
                    with self.lock:
                        self.document_epoch += 1
                        waiters = [waiter for serial, waiter in self.pending.items()
                                   if serial not in self.context_independent]
                    self._wake_pending(waiters)
                elif message.get("method") == "Runtime.bindingCalled":
                    # Callback must enqueue work; it must not make a CDP call
                    # from this reader, which also delivers command replies.
                    params = dict(message.get("params") or {})
                    params["_document_epoch"] = self.document_epoch
                    self.on_event(self, params)
        except Exception:
            pass
        finally:
            self.stopped.set()
            self._wake_pending()

    def _wake_pending(self, waiters=None):
        if waiters is None:
            with self.lock:
                waiters = list(self.pending.values())
        for waiter in waiters:
            try:
                waiter.put_nowait(None)
            except queue.Full:
                pass

    def call(self, method, params=None):
        waiter = queue.Queue(maxsize=1)
        with self.lock:
            if self.stopped.is_set():
                raise CDPError("plaza event channel closed")
            self.serial += 1
            serial = self.serial
            self.pending[serial] = waiter
            if method == "Runtime.enable":
                self.context_independent.add(serial)
            identity = (self.generation, self.document_epoch)
        try:
            self.ws.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
            result = waiter.get(timeout=self.timeout)
            with self.lock:
                if (self.stopped.is_set() or identity[0] != self.generation
                        or (method != "Runtime.enable" and identity[1] != self.document_epoch)):
                    raise CDPError("discarded response from a replaced page")
            if result is None or "error" in result:
                raise CDPError("plaza CDP command failed")
            return result.get("result", {})
        except (queue.Empty, OSError, WSError) as error:
            self.close()
            raise CDPError("plaza CDP response unavailable") from error
        finally:
            with self.lock:
                self.pending.pop(serial, None)
                self.context_independent.discard(serial)

    def js(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise CDPError("plaza page evaluation failed")
        return result.get("result", {}).get("value")

    def bind(self, name):
        self.call("Runtime.addBinding", {"name": name})

    def close(self):
        with self.lock:
            if self._closed:
                return
            self._closed = True
            self.stopped.set()
            self.generation += 1
        self._wake_pending()
        try:
            self.ws.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.ws.close()
        except OSError:
            pass
        if self.reader is not threading.current_thread():
            self.reader.join(timeout=1)
