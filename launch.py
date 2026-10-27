"""Source and PyInstaller entry point; independent of the current directory."""

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wuwa_tappo_plus.entry import main

if __name__ == "__main__":
    raise SystemExit(main())
