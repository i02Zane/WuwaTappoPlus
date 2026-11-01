import unittest
from unittest.mock import patch

from wuwa_tappo_plus import fullscreen

from wuwa_tappo_plus.fullscreen import client_covers_monitor


class ClientCoverTests(unittest.TestCase):
    def test_exact_client_covering_the_monitor(self):
        self.assertTrue(client_covers_monitor((0, 0, 2560, 1440), (0, 0, 2560, 1440)))

    def test_title_bar_above_the_client_is_not_fullscreen(self):
        self.assertFalse(client_covers_monitor((0, 30, 2560, 1470), (0, 0, 2560, 1440)))

    def test_maximized_work_area_does_not_cover_the_monitor(self):
        self.assertFalse(client_covers_monitor((0, 0, 2560, 1392), (0, 0, 2560, 1440)))

    def test_small_offset_is_tolerated(self):
        self.assertTrue(client_covers_monitor((-2, -1, 2562, 1441), (0, 0, 2560, 1440)))

    def test_overscan_is_tolerated(self):
        self.assertTrue(client_covers_monitor((-8, 0, 2568, 1440), (0, 0, 2560, 1440)))

    def test_a_window_that_does_not_reach_an_edge_is_not_fullscreen(self):
        self.assertFalse(client_covers_monitor((0, 0, 2550, 1440), (0, 0, 2560, 1440)))
        self.assertFalse(client_covers_monitor((0, 0, 2560, 1430), (0, 0, 2560, 1440)))

    def test_smaller_window_is_not_fullscreen(self):
        self.assertFalse(client_covers_monitor((0, 0, 1920, 1080), (0, 0, 2560, 1440)))


class DetectionFailureTests(unittest.TestCase):
    def test_missing_foreground_is_unknown_instead_of_an_exit(self):
        self.assertIsNone(fullscreen.window_client_covers_monitor(None))

    def test_foreground_api_error_is_unknown(self):
        with patch.object(fullscreen.user32, "GetForegroundWindow", side_effect=OSError("transient")):
            self.assertIsNone(fullscreen.foreground_client_covers_monitor())

    def test_client_rectangle_api_failure_is_unknown(self):
        with patch.object(fullscreen.user32, "GetAncestor", return_value=42), \
                patch.object(fullscreen.user32, "IsWindowVisible", return_value=True), \
                patch.object(fullscreen.user32, "IsIconic", return_value=False), \
                patch.object(fullscreen.user32, "GetClassNameW", return_value=0), \
                patch.object(fullscreen.user32, "GetWindowThreadProcessId", return_value=0), \
                patch.object(fullscreen, "_process_name", return_value="game.exe"), \
                patch.object(fullscreen.user32, "GetClientRect", return_value=False):
            self.assertIsNone(fullscreen.window_client_covers_monitor(42))


if __name__ == "__main__":
    unittest.main()
