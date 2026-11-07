"""CDP channel with an independent reader for page binding callbacks."""

import json
import queue
import select
import socket
import threading

from .cdp import CDPError
from .ws import WebSocket


class EventSession:
    def __init__(self, target, on_event, timeout=5):
        self.target = target
        self.timeout = timeout
        self.on_event = on_event
        self.ws = WebSocket(target["webSocketDebuggerUrl"], timeout=timeout)
        self.pending = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.serial = 0
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
                        waiter.put_nowait(message)
                elif message.get("method") == "Runtime.bindingCalled":
                    # Callback must enqueue work; it must not make a CDP call
                    # from this reader, which also delivers command replies.
                    self.on_event(self, message.get("params") or {})
        except Exception:
            pass
        finally:
            self.stopped.set()
            with self.lock:
                for waiter in self.pending.values():
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
        try:
            self.ws.send(json.dumps({"id": serial, "method": method, "params": params or {}}))
            result = waiter.get(timeout=self.timeout)
            if result is None or "error" in result:
                raise CDPError("plaza CDP command failed")
            return result.get("result", {})
        except (queue.Empty, OSError) as error:
            self.close()
            raise CDPError("plaza CDP response unavailable") from error
        finally:
            with self.lock:
                self.pending.pop(serial, None)

    def js(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise CDPError("plaza page evaluation failed")
        return result.get("result", {}).get("value")

    def bind(self, name):
        self.call("Runtime.addBinding", {"name": name})

    def close(self):
        self.stopped.set()
        try:
            self.ws.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.ws.close()
        if self.reader is not threading.current_thread():
            self.reader.join(timeout=1)
