"""Shared discovery and isolated connection lifecycles, without real WebViews."""

import json
import unittest
from unittest.mock import Mock, patch

from wuwa_tappo_plus import __version__, cdp
from wuwa_tappo_plus.pages import PageBridge, TargetDiscovery, connection_key, is_panel, is_toast
from wuwa_tappo_plus.rooms import RoomBridge
from wuwa_tappo_plus.settings import SettingsBridge
from wuwa_tappo_plus.toasts import ToastBridge
from wuwa_tappo_plus.ws import WSError


PANEL = {'id': 'panel', 'url': 'http://tauri.localhost/index.html#/panel',
         'webSocketDebuggerUrl': 'ws://local/panel'}
TOAST = {'id': 'toast', 'url': 'http://tauri.localhost/index.html#/toast',
         'webSocketDebuggerUrl': 'ws://local/toast'}
PET = {'id': 'pet', 'url': 'http://tauri.localhost/#/',
       'webSocketDebuggerUrl': 'ws://local/pet'}


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class FakeSession:
    def __init__(self, *args, **kwargs):
        self.target = None
        self.generation = 0
        self.document_epoch = 0
        self.sent = []
        self.closed = False

    def connect_target(self, target):
        self.target = dict(target)

    def js(self, expression):
        self.sent.append(expression)
        return json.dumps({'version': __version__, 'requests': [], 'entries': []})

    def invalidate_document(self):
        self.document_epoch += 1

    def close(self):
        self.closed = True
        self.generation += 1


class ProbeSocket:
    def __init__(self, url, **kwargs):
        self.url = url
        self.command = None

    def send(self, raw):
        self.command = json.loads(raw)

    def recv(self):
        result = {}
        if self.command['method'] == 'Runtime.evaluate':
            result = {'result': {'value': 'ok' if self.url == PET['webSocketDebuggerUrl'] else 'no-store'}}
        return json.dumps({'id': self.command['id'], 'result': result})

    def close(self):
        pass


class PageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.discovery = TargetDiscovery(9222, clock=self.clock)
        self.scan = patch.object(cdp, 'list_targets', return_value=[PANEL, TOAST, PET])
        self.list_targets = self.scan.start()
        self.addCleanup(self.scan.stop)
        self.sessions = patch.object(cdp, 'Session', side_effect=FakeSession)
        self.sessions.start()
        self.addCleanup(self.sessions.stop)

    def bridge(self):
        return PageBridge(9222, '127.0.0.1', Mock(), select=is_panel,
                          uninstall='stop', discovery=self.discovery)

    def test_features_share_one_scan_but_keep_independent_connections(self):
        bridges = [SettingsBridge(9222, '127.0.0.1', Mock(), discovery=self.discovery),
                   RoomBridge(9222, '127.0.0.1', Mock(), discovery=self.discovery),
                   ToastBridge(9222, '127.0.0.1', Mock(), discovery=self.discovery)]
        sessions = [bridge.ensure_connected() for bridge in bridges]
        self.assertEqual(self.list_targets.call_count, 1)
        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(sessions[0].target, PANEL)
        self.assertEqual(sessions[1].target, PANEL)
        self.assertEqual(sessions[2].target, TOAST)
        for second in (1, 2, 3):
            self.clock.now = second
            for bridge in bridges:
                bridge.ensure_connected()
        self.assertEqual(self.list_targets.call_count, 2)

    def test_scan_failures_are_shared_until_the_existing_retry_deadline(self):
        self.list_targets.side_effect = [cdp.CDPError('offline'), [PANEL]]
        for second in (0, 1, 2):
            self.clock.now = second
            with self.assertRaises(cdp.CDPError):
                self.discovery.targets()
        self.assertEqual(self.list_targets.call_count, 1)
        self.clock.now = 3
        self.assertEqual(self.discovery.targets(), [PANEL])
        self.assertEqual(self.list_targets.call_count, 2)

    def test_cache_is_owned_by_endpoint_and_cannot_be_mutated_by_consumers(self):
        first = self.discovery.targets()
        first[0]['url'] = 'changed'
        self.assertEqual(self.discovery.targets()[0]['url'], PANEL['url'])
        other = TargetDiscovery(9333, clock=self.clock)
        other.targets()
        self.assertEqual(self.list_targets.call_count, 2)

    def test_same_id_with_a_replaced_websocket_endpoint_is_reconnected(self):
        bridge = self.bridge()
        old = bridge.ensure_connected()
        replacement = {**PANEL, 'webSocketDebuggerUrl': 'ws://local/replacement'}
        self.list_targets.return_value = [replacement]
        self.clock.now = 3
        current = bridge.ensure_connected()
        self.assertIsNot(current, old)
        self.assertTrue(old.closed)
        self.assertEqual(old.sent, ['stop'])
        self.assertEqual(current.target, replacement)

    def test_removed_panel_closes_only_that_feature_connection(self):
        bridge = self.bridge()
        old = bridge.ensure_connected()
        self.list_targets.return_value = [TOAST]
        self.clock.now = 3
        self.assertIsNone(bridge.ensure_connected())
        self.assertTrue(old.closed)

    def test_disconnect_retries_on_the_next_scan_without_resetting_other_features(self):
        bridge = self.bridge()
        old = bridge.ensure_connected()
        bridge.close()
        self.clock.now = 1
        self.assertIsNone(bridge.ensure_connected())
        self.clock.now = 3
        self.assertIsNot(bridge.ensure_connected(), old)
        self.assertTrue(old.closed)

    def test_failed_connect_releases_the_partially_opened_session(self):
        session = FakeSession()
        with patch.object(cdp, 'Session', return_value=session), \
                patch.object(session, 'connect_target', side_effect=cdp.CDPError('closed')):
            with self.assertRaises(cdp.CDPError):
                self.bridge().ensure_connected()
        self.assertTrue(session.closed)

    def test_page_reload_reinjects_and_invalidates_the_previous_lease(self):
        bridge = self.bridge()
        session = bridge.ensure_connected()
        old = connection_key(session)
        replies = iter([None, 'installed', json.dumps({'version': __version__})])
        session.js = lambda expression: next(replies)
        self.assertEqual(bridge.read_injected('read', 'inject', __version__), {'version': __version__})
        self.assertNotEqual(connection_key(session), old)
        with self.assertRaises(cdp.StaleResponse):
            bridge.require_current(old)

    def test_replaced_connection_cannot_return_its_old_state(self):
        bridge = self.bridge()
        old = bridge.ensure_connected()
        replacement = FakeSession()
        replacement.connect_target(PANEL)

        def reply(_):
            bridge.session = replacement
            return json.dumps({'version': __version__, 'requests': [{'id': 'old'}]})

        old.js = reply
        with self.assertRaises(cdp.StaleResponse):
            bridge.read_injected('read', 'inject', __version__)
        self.assertFalse(replacement.closed)

    def test_only_exact_local_panel_and_toast_routes_are_selected(self):
        self.assertTrue(is_panel(PANEL['url']))
        self.assertTrue(is_toast(TOAST['url']))
        for url in ('http://example.com/index.html#/panel', 'http://tauri.localhost/#/',
                    'http://tauri.localhost/index.html#/panel-other'):
            self.assertFalse(is_panel(url))


class MainDiscoveryTests(unittest.TestCase):
    def test_main_probe_reuses_discovery_but_still_rejects_non_pet_pages(self):
        discovery = TargetDiscovery(9222, clock=lambda: 0)
        session = cdp.Session(9222, discovery=discovery)
        with patch.object(cdp, 'list_targets', return_value=[PANEL, PET]) as scan, \
                patch.object(cdp, 'WebSocket', ProbeSocket):
            discovery.targets()
            self.assertTrue(session.connect())
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(session.target, PET)
        session.close()


class SynchronousTransportTests(unittest.TestCase):
    def test_closed_auxiliary_session_never_discovers_the_main_window(self):
        session = cdp.Session(9222)
        with patch.object(cdp, 'WebSocket', ProbeSocket), patch.object(cdp, 'list_targets') as scan:
            session.connect_target(PANEL)
            session.close()
            with self.assertRaises(cdp.CDPError):
                session.js('old callback')
        scan.assert_not_called()

    def test_response_from_replaced_socket_is_discarded(self):
        session = cdp.Session(9222)
        old, replacement = Mock(), Mock()
        session.ws = old

        def reply():
            session.ws = replacement
            session.generation += 1
            return json.dumps({'id': 1, 'result': {'result': {'value': 'stale'}}})

        old.recv.side_effect = reply
        with self.assertRaises(cdp.StaleResponse):
            session.js('old')
        replacement.send.assert_not_called()

    def test_failure_from_replaced_socket_is_also_stale(self):
        session = cdp.Session(9222)
        old, replacement = Mock(), Mock()
        session.ws = old

        def fail():
            session.ws = replacement
            session.generation += 1
            raise WSError('closed')

        old.recv.side_effect = fail
        with self.assertRaises(cdp.StaleResponse):
            session.js('old')

    def test_context_reset_discards_old_response_and_next_call_can_recover(self):
        session = cdp.Session(9222)
        session.ws = Mock()
        session.ws.recv.side_effect = [
            json.dumps({'method': 'Runtime.executionContextsCleared'}),
            json.dumps({'id': 1, 'result': {'result': {'value': 'old'}}}),
            json.dumps({'id': 2, 'result': {'result': {'value': 'new'}}}),
        ]
        with self.assertRaises(cdp.StaleResponse):
            session.js('old')
        self.assertEqual(session.js('new'), 'new')
        self.assertEqual(session.document_epoch, 1)

    def test_failed_initial_runtime_subscription_releases_the_socket(self):
        socket = Mock()
        socket.recv.side_effect = WSError('closed')
        session = cdp.Session(9222)
        with patch.object(cdp, 'list_targets', return_value=[PET]), \
                patch.object(cdp, 'WebSocket', return_value=socket):
            with self.assertRaises(cdp.CDPError):
                session.connect()
        socket.close.assert_called_once()
        self.assertIsNone(session.ws)


if __name__ == '__main__':
    unittest.main()
