import json
import queue
import socket
import threading
import unittest
from unittest.mock import patch

from wuwa_tappo_plus.cdp_events import EventSession


class FakeSocket:
    def __init__(self, *args, **kwargs):
        self.sock, self.peer = socket.socketpair()
        self.messages = queue.Queue()
        self.sent = []
        self.drop = False

    def emit(self, message):
        self.messages.put(json.dumps(message))
        self.peer.sendall(b'x')

    def send(self, raw):
        command = json.loads(raw)
        self.sent.append(command)
        if not self.drop:
            self.emit({'id': command['id'], 'result': {'result': {'value': command['params'].get('expression')}}})

    def recv(self):
        self.sock.recv(1)
        return self.messages.get_nowait()

    def close(self):
        self.sock.close()
        self.peer.close()


class EventChannelTests(unittest.TestCase):
    def connect(self, callback):
        with patch('wuwa_tappo_plus.cdp_events.WebSocket', FakeSocket):
            channel = EventSession({'webSocketDebuggerUrl': 'ws://test', 'id': 'target'}, callback)
        self.addCleanup(channel.close)
        return channel

    def test_binding_callback_arrives_without_evaluating_or_polling_the_page(self):
        received = threading.Event()
        channel = self.connect(lambda *_: received.set())
        channel.ws.emit({'method': 'Runtime.bindingCalled', 'params': {'name': '__wutPlazaAction', 'payload': '{}'}})
        self.assertTrue(received.wait(2))
        self.assertEqual(channel.ws.sent, [])

    def test_command_response_and_binding_event_share_one_reader(self):
        received = threading.Event()
        channel = self.connect(lambda *_: received.set())
        channel.ws.emit({'method': 'Runtime.bindingCalled', 'params': {}})
        self.assertEqual(channel.js('a response'), 'a response')
        self.assertTrue(received.wait(2))

    def test_close_wakes_pending_command_and_reader(self):
        received = threading.Event()
        channel = self.connect(lambda *_: None)
        channel.ws.drop = True
        errors = []
        def call():
            try:
                channel.js('wait')
            except Exception as error:
                errors.append(type(error).__name__)
            finally:
                received.set()
        thread = threading.Thread(target=call)
        thread.start()
        channel.close()
        self.assertTrue(received.wait(2))
        thread.join(timeout=2)
        self.assertEqual(errors, ['CDPError'])

    def test_context_reset_wakes_pending_evaluation_and_discards_late_reply(self):
        channel = self.connect(lambda *_: None)
        channel.ws.drop = True
        sent, finished = threading.Event(), threading.Event()
        original_send = channel.ws.send

        def send(raw):
            original_send(raw)
            sent.set()

        channel.ws.send = send
        errors = []

        def evaluate():
            try:
                channel.js('old document')
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                finished.set()

        worker = threading.Thread(target=evaluate)
        worker.start()
        self.assertTrue(sent.wait(2))
        channel.ws.emit({'method': 'Runtime.executionContextsCleared'})
        self.assertTrue(finished.wait(2))
        worker.join(timeout=2)
        self.assertEqual(errors, ['CDPError'])
        self.assertEqual(channel.document_epoch, 1)
        channel.ws.emit({'id': 1, 'result': {'result': {'value': 'late old reply'}}})
        channel.ws.drop = False
        self.assertEqual(channel.js('new document'), 'new document')

    def test_binding_events_capture_document_epoch_before_entering_work_queue(self):
        channel = self.connect(lambda *_: None)
        got = threading.Event()
        epochs = []
        channel.on_event = lambda _, params: (epochs.append(params['_document_epoch']), got.set())
        channel.ws.emit({'method': 'Runtime.executionContextsCleared'})
        channel.ws.emit({'method': 'Runtime.bindingCalled', 'params': {'name': 'test'}})
        self.assertTrue(got.wait(2))
        self.assertEqual(epochs, [1])

    def test_initial_context_reset_does_not_cancel_runtime_subscription(self):
        channel = self.connect(lambda *_: None)
        original_send = channel.ws.send

        def send(raw):
            channel.ws.emit({'method': 'Runtime.executionContextsCleared'})
            original_send(raw)

        channel.ws.send = send
        channel.call('Runtime.enable')
        self.assertEqual(channel.document_epoch, 1)
        self.assertTrue(channel.alive())

    def test_close_is_idempotent_and_new_calls_fail_immediately(self):
        channel = self.connect(lambda *_: None)
        channel.close()
        channel.close()
        with self.assertRaisesRegex(Exception, 'closed'):
            channel.js('after close')
