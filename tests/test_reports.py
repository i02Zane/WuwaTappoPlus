import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

from wuwa_tappo_plus.reports import DailyReport


class DailyClickTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "input.sqlite3"
        self.now = datetime(2026, 10, 27, 9)

    def tracker(self):
        tracker = DailyReport(Mock(), path=self.path, now=lambda: self.now)
        self.addCleanup(tracker.close)
        return tracker

    def sample(self, total, account="person"):
        return {"click_total": total, "click_account": account}

    def counts(self, tracker):
        return {row["date"]: row["count"] for row in tracker.yesterday()["days"]}

    def test_first_sample_is_baseline_not_historical_daily_count(self):
        tracker = self.tracker()
        tracker.observe(self.sample(32531))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 0)
        self.assertIsNone(tracker.yesterday()["yesterday_count"])

    def test_positive_click_increments_are_counted(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        tracker.observe(self.sample(107))
        tracker.observe(self.sample(112))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 12)

    def test_repeated_sample_does_not_double_count(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        for _ in range(5):
            tracker.observe(self.sample(103))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 3)

    def test_chest_clicks_and_wallet_spending_do_not_count(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        tracker.observe({**self.sample(100), "clicks": [{"t": 100}], "bal": 0, "spend": 1000})
        self.assertEqual(self.counts(tracker)["2026-10-27"], 0)

    def test_restart_restores_baseline(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        tracker.observe(self.sample(103))
        tracker.close()
        restarted = self.tracker()
        restarted.observe(self.sample(105))
        self.assertEqual(self.counts(restarted)["2026-10-27"], 5)

    def test_account_switch_does_not_count_another_accounts_history(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        tracker.observe(self.sample(102))
        tracker.observe(self.sample(30000, "another"))
        tracker.observe(self.sample(30003, "another"))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 5)

    def test_counter_reset_rebases_without_negative_or_fake_jump(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        tracker.observe(self.sample(0))
        tracker.observe(self.sample(2))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 2)

    def test_continuous_midnight_samples_use_new_day(self):
        tracker = self.tracker()
        self.now = datetime(2026, 10, 26, 23, 59, 59)
        tracker.observe(self.sample(100))
        self.now = datetime(2026, 10, 27, 0, 0, 1)
        tracker.observe(self.sample(103))
        self.assertEqual(tracker.yesterday()["yesterday_count"], 0)
        self.assertEqual(self.counts(tracker)["2026-10-27"], 3)

    def test_cross_day_gap_does_not_invent_a_daily_allocation(self):
        tracker = self.tracker()
        self.now = datetime(2026, 10, 26, 21)
        tracker.observe(self.sample(100))
        self.now = datetime(2026, 10, 27, 9)
        tracker.observe(self.sample(500))
        self.assertEqual(self.counts(tracker)["2026-10-27"], 0)

    def test_old_chest_records_are_not_imported(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE clicks (id TEXT, day TEXT)")
            connection.execute("INSERT INTO clicks VALUES ('opening', '2026-10-26')")
        connection.close()
        self.assertIsNone(self.tracker().yesterday()["yesterday_count"])

    def test_missing_data_and_observed_zero_are_distinct(self):
        tracker = self.tracker()
        self.assertIsNone(tracker.yesterday()["yesterday_count"])
        self.now = datetime(2026, 10, 26, 23)
        tracker.observe(self.sample(100))
        self.now = datetime(2026, 10, 27, 9)
        self.assertEqual(tracker.yesterday()["yesterday_count"], 0)

    def test_raw_account_identifier_is_not_stored(self):
        tracker = self.tracker()
        tracker.observe(self.sample(100, "private-account"))
        stored = tracker.connection.execute("SELECT account FROM input_baseline").fetchone()[0]
        self.assertNotIn("private-account", stored)
        self.assertEqual(len(stored), 64)

    def test_invalid_or_logged_out_samples_do_not_create_data(self):
        tracker = self.tracker()
        for sample in (None, {}, self.sample(None), self.sample(-1), self.sample(float("nan")),
                       self.sample(True), self.sample(100, None)):
            tracker.observe(sample)
        self.assertIsNone(self.counts(tracker)["2026-10-27"])

    def test_bad_database_is_preserved_without_breaking_automation(self):
        self.path.write_bytes(b"invalid database")
        tracker = self.tracker()
        tracker.observe(self.sample(100))
        self.assertFalse(tracker.yesterday()["available"])
        self.assertEqual(self.path.read_bytes(), b"invalid database")


if __name__ == "__main__":
    unittest.main()
