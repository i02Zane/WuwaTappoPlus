import json
import tempfile
import unittest
from pathlib import Path

from wuwa_tappo_plus import accessories


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / accessories.FILE_NAME

    def test_a_missing_file_means_no_overrides(self):
        self.assertEqual(accessories.load(self.path), {})

    def test_changes_round_trip_atomically(self):
        overrides = accessories.set_override(self.path, "84e5d107.png", 4, -2, 1.2, 15)
        self.assertEqual(overrides, {"84e5d107.png": {"x": 4.0, "y": -2.0,
                                                      "scale": 1.2, "rotation": 15.0}})
        self.assertEqual(accessories.load(self.path), overrides)
        parsed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(parsed["84e5d107.png"]["scale"], 1.2)
        overrides = accessories.set_override(self.path, "84e5d107.png", 0, 0, 1, 0)
        self.assertEqual(overrides, {})
        self.assertEqual(accessories.load(self.path), {})

    def test_invalid_values_are_rejected(self):
        for values in ((1, 2, 0.1, 0), (0, 0, 1, 999), (0, 0, 3.1, 0)):
            with self.assertRaises(ValueError):
                accessories.normalize("84e5d107.png", *values)
        with self.assertRaises(ValueError):
            accessories.normalize("../evil.png", 0, 0, 1, 0)
        with self.assertRaises(ValueError):
            accessories.normalize("84e5d107.png", True, 0, 1, 0)

    def test_load_ignores_corrupt_entries(self):
        self.path.write_text(json.dumps({
            "good.png": {"x": 1, "y": 2, "scale": 1, "rotation": 0},
            "bad.png": {"x": "nope", "y": 0, "scale": 1, "rotation": 0},
            "scalar.png": 3,
        }), encoding="utf-8")
        self.assertEqual(accessories.load(self.path),
                         {"good.png": {"x": 1.0, "y": 2.0, "scale": 1.0, "rotation": 0.0}})


if __name__ == "__main__":
    unittest.main()
