"""Daemon orchestration with fake clocks, connections and feature resources."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from wuwa_tappo_plus import cdp
from wuwa_tappo_plus.config import Config
from wuwa_tappo_plus.runner import Runner


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


class RunnerTests(unittest.TestCase):
    def setUp(self):
        signal_patch = patch("wuwa_tappo_plus.runner.signal.signal", return_value=None)
        signal_patch.start()
        self.addCleanup(signal_patch.stop)
        self.make_runner()

    def make_runner(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.stop_file = Path(directory.name) / ".stop"
        self.clock = Clock()
        self.events = []
        self.services = SimpleNamespace(**{
            name: Mock() for name in ("session", "pet", "settings", "reports", "rooms",
                                     "plaza", "history", "toasts", "screens", "chests",
                                     "observations", "game_mode", "close")})
        self.services.session.ws = object()
        self.services.session.target = {"url": "pet"}
        self.services.chests.data = {}
        self.services.observations.click_count = 0
        self.services.observations.block_reason = None
        self.services.observations.toast_floor_ms = 10
        self.cfg = Config({"daemon": {"max_runtime": 2}})
        self.payload = {"inject": "current", "state": {"now": 1000, "bal": 0}}
        self.operations = (
            ("reports.yesterday", {}), ("settings.sync", self.cfg),
            ("pet.poll", self.payload), ("chests.observe", None),
            ("reports.observe", None), ("pet.apply_accessories", None),
            ("pet.accessory_base", {"file": "hat"}), ("game_mode.tick", None),
            ("observations.drain_clicks", None), ("observations.note_block", None),
            ("observations.alert_balance", None), ("rooms.sync", None),
            ("plaza.sync", None), ("screens.tick", None), ("toasts.poll", [(10, "hi")]),
            ("observations.log_toast", None), ("observations.drain_page_errors", None),
            ("observations.heartbeat", None),
        )
        for name, result in self.operations:
            self.method(name).side_effect = self.record(name, result)
        self.factory = Mock(return_value=self.services)
        self.lock = Mock()
        self.publisher = Mock()
        self.runner = Runner(self.cfg, Mock(), services_factory=self.factory, lock=self.lock,
                             publisher=self.publisher, stop_file=self.stop_file,
                             clock=self.clock, wait=self.clock.wait)

    def method(self, name):
        resource, method = name.split(".")
        return getattr(getattr(self.services, resource), method)

    def record(self, name, result):
        def call(*args, **kwargs):
            self.events.append(name)
            return result
        return call

    def test_normal_order_cadence_and_state_are_preserved(self):
        self.assertEqual(self.runner.run(), 0)
        # Check causal dependencies, not an exact copy of the job table.
        first_round = self.events[:len(self.operations)]
        for before, after in (("settings.sync", "pet.poll"),
                              ("pet.poll", "reports.observe"),
                              ("observations.drain_clicks", "observations.log_toast")):
            self.assertLess(first_round.index(before), first_round.index(after))
        self.assertEqual(self.clock.waits, [1.0, 1.0])
        self.services.pet.poll.assert_called_with(self.cfg, {})
        self.services.observations.alert_balance.assert_called_with(self.cfg, self.payload["state"], None)
        self.services.rooms.sync.assert_called_with(self.services.history, plaza=self.services.plaza)
        self.services.toasts.poll.assert_called_with(10)
        self.assertEqual(self.services.settings.pet_accessory, {"file": "hat"})
        state = self.publisher.publish.call_args.args[3]
        self.assertEqual(state, {"inject": "current", "now": 1000, "bal": 0})
        self.publisher.stopped.assert_called_once()
        self.services.close.assert_called_once()
        self.lock.release.assert_called_once()

    def test_every_auxiliary_failure_leaves_later_features_and_next_round_running(self):
        for name, result in self.operations:
            if name == "pet.poll":
                continue
            with self.subTest(feature=name):
                self.make_runner()
                # Cover each isolation boundary once with a representative failure,
                # then let it recover on the next iteration.
                error = (OSError("disk unavailable") if name.startswith(("reports.", "chests."))
                         else ValueError("bad record") if name.startswith("observations.")
                         else cdp.CDPError("page unavailable"))
                calls = []

                def recover(*args, **kwargs):
                    calls.append(1)
                    if len(calls) == 1:
                        raise error
                    return result

                self.method(name).side_effect = recover
                self.assertEqual(self.runner.run(), 0)
                self.assertEqual(len(calls), 2)
                self.assertEqual(self.publisher.publish.call_count, 2)
                self.services.session.close.assert_not_called()
                self.assertEqual(self.clock.now, 2)
                self.assertEqual(self.services.pet.poll.call_count, 2)
                self.runner.log.warn.assert_called_once()

    def test_primary_errors_back_off_and_a_success_resets_retry_budget(self):
        self.runner.cfg = Config({"daemon": {"max_runtime": 9}})
        self.services.settings.sync.side_effect = lambda cfg, report: cfg
        times = []
        replies = iter([cdp.CDPError("one"), OSError("two"), self.payload,
                        cdp.CDPError("after success"), self.payload])

        def poll(*args):
            times.append(self.clock.now)
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.services.pet.poll.side_effect = poll
        self.assertEqual(self.runner.run(), 0)
        self.assertEqual(times, [0, 2, 5, 6, 8])
        self.assertEqual(self.services.session.close.call_count, 3)
        self.assertEqual(self.services.reports.observe.call_count, 2)
        self.assertEqual(self.publisher.publish.call_count, 5)

    def test_primary_retry_delay_stays_capped_at_ten_seconds(self):
        self.runner.cfg = Config({"daemon": {"max_runtime": 50}})
        self.services.settings.sync.side_effect = lambda cfg, report: cfg
        times = []

        def fail(*args):
            times.append(self.clock.now)
            raise cdp.CDPError("offline")

        self.services.pet.poll.side_effect = fail
        self.assertEqual(self.runner.run(), 0)
        self.assertEqual(times, [0, 2, 5, 9.5, 16.25, 26.25, 36.25, 46.25])
        self.services.reports.observe.assert_not_called()

    def test_both_stop_mechanisms_interrupt_wait_before_another_retry(self):
        for mechanism in ("file", "signal"):
            with self.subTest(mechanism=mechanism):
                self.make_runner()
                self.services.pet.poll.side_effect = cdp.CDPError("offline")

                def stop(seconds):
                    self.clock.wait(seconds)
                    if mechanism == "file":
                        self.stop_file.touch()
                    else:
                        self.runner.request_stop()

                self.runner.wait = stop
                self.assertEqual(self.runner.run(), 0)
                self.assertEqual(self.clock.now, 1)
                self.services.pet.poll.assert_called_once()
                self.services.close.assert_called_once()
                self.lock.release.assert_called_once()

    def test_duplicate_opens_no_resources_and_does_not_remove_stop_marker(self):
        self.lock.acquire.return_value = False
        self.stop_file.touch()
        self.assertEqual(self.runner.run(), 2)
        self.factory.assert_not_called()
        self.lock.release.assert_not_called()
        self.assertTrue(self.stop_file.exists())
        self.publisher.stopped.assert_not_called()

    def test_startup_failure_releases_lock_without_marking_another_status_stopped(self):
        self.factory.side_effect = OSError("cannot open database")
        self.assertEqual(self.runner.run(), 1)
        self.lock.release.assert_called_once()
        self.publisher.stopped.assert_not_called()

    def test_shutdown_failures_cannot_strand_the_instance_lock(self):
        for failing in ("services", "status"):
            with self.subTest(failing=failing):
                self.make_runner()
                action = self.services.close if failing == "services" else self.publisher.stopped
                action.side_effect = OSError("unavailable")
                self.assertEqual(self.runner.run(), 0)
                self.services.close.assert_called_once()
                self.publisher.stopped.assert_called_once()
                self.lock.release.assert_called_once()

    def test_updated_config_reaches_all_features_and_the_foreground_detector(self):
        cfg = Config({"appearance": {"game_mode_plus": False}})
        self.services.settings.sync.side_effect = lambda *args: cfg
        self.runner.wait = lambda seconds: self.runner.request_stop()
        self.assertEqual(self.runner.run(), 0)
        self.services.pet.poll.assert_called_once_with(cfg, {})
        self.services.game_mode.tick.assert_called_once_with(self.services.session, False)
        with patch("wuwa_tappo_plus.runner.sample_game_mode", return_value=None) as sample:
            self.assertIsNone(self.factory.call_args.args[2]())
            sample.assert_called_once_with(cfg)
