"""Top-level launcher.

Use this to run the app in development:
    python main.py                # GUI
    python main.py <world> [mode] [--raw]   # CLI
"""
import os
import sys

# Ensure the repo root is on sys.path so `import src...` works when the
# script is launched directly (double-click, `python main.py`, PyInstaller).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.main import main

if __name__ == "__main__":
    raise SystemExit(main())
