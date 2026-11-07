import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from wuwa_tappo_plus.plaza import PlazaBridge
from wuwa_tappo_plus.plaza_auth import CredentialStore, crypt
from wuwa_tappo_plus.plaza_client import PlazaClient, PlazaError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = PlazaClient(directory=Path(self.temp.name))
        self.client.credentials = Mock()
        self.client.credentials.load.return_value = 'a' * 43
        self.client.opener = Mock()

    def response(self, payload, status=200, headers=None):
        response = Mock(status=status, headers=headers or {})
        response.read.return_value = json.dumps(payload).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        self.client.opener.open.return_value = response
        return response

    def test_page_cannot_supply_a_url_headers_credentials_or_unbounded_page(self):
        for item in ({'op': 'sql'}, {'op': 'list', 'url': 'https://evil.example'},
                     {'op': 'list', 'args': {'headers': {'Authorization': 'secret'}}},
                     {'op': 'list', 'args': {'page': True}}, {'op': 'list', 'args': {'page': 50}}):
            with self.subTest(item=item), self.assertRaises(PlazaError):
                self.client.send(item)
        self.client.opener.open.assert_not_called()

    def test_fixed_request_and_sanitized_response_do_not_expose_token(self):
        stamp = '2026-11-08T00:00:00Z'
        row = dict(id=str(uuid.uuid4()), room_code='Room1234', published_at=stamp, updated_at=stamp, expires_at=stamp, token='private', member_count=20)
        self.response(dict(items=[row], page=1, has_more=False, server_time=stamp, next_refresh_at=stamp, token='private'))
        result = self.client.send({'op': 'list', 'args': {'page': 1, 'refresh': False}})
        self.assertNotIn('token', json.dumps(result))
        self.assertNotIn('member_count', result['items'][0])
        req = self.client.opener.open.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/rooms?page=1&refresh=0'))
        self.assertEqual(req.headers['Authorization'], 'Bearer ' + 'a' * 43)

    def test_first_identity_is_saved_before_publication_and_never_returned(self):
        self.client.credentials.load.return_value = None
        self.client.http = Mock(side_effect=[{'token': 'b' * 43}, {'item': None, 'server_time': '2026-11-08T00:00:00Z'}])
        result = self.client.send({'op': 'own'})
        self.client.credentials.save.assert_called_once_with('b' * 43)
        self.assertNotIn('token', result)

    def test_invalid_body_and_old_member_counts_cannot_be_sent(self):
        for args in ({'room_code': 'bad!', 'key': str(uuid.uuid4())},
                     {'room_code': 'Room1234', 'key': str(uuid.uuid4()), 'member_count': 20},
                     {'room_code': 'Room1234', 'key': 'invalid'}):
            with self.assertRaises(PlazaError):
                self.client.send({'op': 'publish', 'args': args})
        self.client.opener.open.assert_not_called()

    def test_401_does_not_recreate_identity_and_errors_never_echo_body(self):
        self.response({'token': 'private'}, status=401)
        for _ in range(2):
            with self.assertRaises(PlazaError) as error:
                self.client.send({'op': 'own'})
            self.assertEqual(error.exception.code, 'UNAUTHORIZED')
        self.client.credentials.save.assert_not_called()
        self.assertEqual(self.client.credentials.load.call_count, 1)

    def test_invalid_success_and_oversized_responses_fail_closed(self):
        response = self.response({'item': {'token': 'private'}, 'server_time': 'bad'})
        with self.assertRaises(PlazaError):
            self.client.send({'op': 'own'})
        response.read.return_value = b'x' * (256 * 1024 + 1)
        with self.assertRaises(PlazaError):
            self.client.send({'op': 'own'})

    def test_retry_after_prevents_a_second_http_attempt(self):
        self.response({'error': {'code': 'RATE_LIMITED'}}, 429, {'Retry-After': '120'})
        with self.assertRaises(PlazaError) as error:
            self.client.send({'op': 'own'})
        self.assertEqual(error.exception.retry, 120)
        with self.assertRaises(PlazaError):
            self.client.send({'op': 'own'})
        self.assertEqual(self.client.opener.open.call_count, 1)

    def test_refresh_cooldown_does_not_block_withdrawal(self):
        self.response({'error': {'code': 'LIST_COOLDOWN'}}, 429, {'Retry-After': '30'})
        with self.assertRaises(PlazaError):
            self.client.send({'op': 'list'})
        self.response({'item': None, 'server_time': '2026-11-08T00:00:00Z'})
        result = self.client.send({'op': 'withdraw', 'args': {'generation': str(uuid.uuid4()), 'key': str(uuid.uuid4())}})
        self.assertIsNone(result['item'])
        self.assertEqual(self.client.opener.open.call_args.args[0].method, 'DELETE')

    def test_storage_failure_cannot_start_networking(self):
        self.client.credentials.load.side_effect = OSError('private')
        with self.assertRaises(PlazaError) as error:
            self.client.send({'op': 'own'})
        self.assertEqual(str(error.exception), 'IDENTITY_STORAGE_ERROR')
        self.client.opener.open.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_dpapi_round_trip_and_atomic_credential_storage(self):
        value = b'synthetic-test-credential'
        protected = crypt(value)
        self.assertNotIn(value, protected)
        self.assertEqual(crypt(protected, True), value)
        store = CredentialStore(Path(self.temp.name), self.client.base)
        store.save('test-credential')
        self.assertEqual(store.load(), 'test-credential')
        self.assertNotIn('test-credential', store.path.read_text())
        self.assertFalse(store.path.with_name('plaza_auth.tmp').exists())


class BridgeTests(unittest.TestCase):
    def test_http_completion_is_delivered_by_callback_without_another_sync(self):
        bridge = PlazaBridge(Mock()); self.addCleanup(bridge.close)
        native = Mock()
        native.alive.return_value = True
        bridge.native = native
        bridge.instance = 'instance'
        completed = threading.Event()
        native.js.side_effect = lambda _: completed.set()
        bridge.client.send = Mock(return_value={'item': None})
        with bridge.lock:
            bridge._request(native, {'id': 'request', 'op': 'own', 'instance': 'instance'})
        self.assertTrue(completed.wait(2))
        self.assertIn('.complete(', native.js.call_args.args[0])

    def test_old_page_response_is_not_delivered_to_its_replacement(self):
        bridge = PlazaBridge(Mock()); self.addCleanup(bridge.close)
        old = Mock()
        bridge.native = Mock()
        bridge.instance = 'new'
        bridge._reply(old, 'old', {'id': 'request', 'data': {}})
        old.js.assert_not_called()
        bridge.native.js.assert_not_called()

    def test_stale_generated_configuration_disables_only_plaza(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'plaza-client.bundle.js').write_text('old artifact')
            (root / 'plaza-config.json').write_text('{"env":"retired"}')
            with patch('wuwa_tappo_plus.plaza.SCRIPTS_DIR', root):
                bridge = PlazaBridge(Mock()); self.addCleanup(bridge.close)
            session = Mock()
            bridge.sync(session)
            session.js.assert_not_called()
            self.assertIn('build:plaza', bridge.state['error'])

    def test_commands_are_bounded_and_pagination_bypasses_only_refresh_cooldown(self):
        import time
        bridge = PlazaBridge(Mock()); self.addCleanup(bridge.close)
        self.assertFalse(bridge.queue({'id': 'a', 'op': 'sql'}))
        self.assertFalse(bridge.queue({'id': 'a', 'op': 'list', 'page': True}))
        bridge.state = {'nextRefreshAt': time.time() * 1000 + 30000, 'loading': False}
        self.assertTrue(bridge.queue({'id': 'a', 'op': 'list'})); self.assertEqual(len(bridge.commands), 0)
        self.assertTrue(bridge.queue({'id': 'b', 'op': 'list', 'page': 1, 'refresh': False}))
        self.assertEqual(len(bridge.commands), 1); self.assertTrue(bridge.state['loading'])

    def test_background_errors_are_structured_without_exception_text(self):
        bridge = PlazaBridge(Mock()); self.addCleanup(bridge.close)
        bridge.client.send = Mock(side_effect=RuntimeError('private token'))
        result = bridge._perform({'id': 'a', 'op': 'own'})
        self.assertNotIn('private', json.dumps(result)); self.assertEqual(result['error']['code'], 'CONNECTION_FAILED')
