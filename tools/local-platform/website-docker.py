from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_REPO_ROOT))

from main_computer.local_platform_lifecycle import main


if __name__ == "__main__":
    raise SystemExit(main())
