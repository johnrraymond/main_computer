from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATCHING_ROOT = HERE / "patching"
if str(PATCHING_ROOT) not in sys.path:
    sys.path.insert(0, str(PATCHING_ROOT))

from smart_patch_harness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
