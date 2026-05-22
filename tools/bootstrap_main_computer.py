from __future__ import annotations

import sys
from pathlib import Path

# When this script is launched as tools\bootstrap_main_computer.py, Python puts
# tools\ on sys.path. Add the repository root so main_computer.bootstrap imports
# work before the project package has been installed into the target venv.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from main_computer.bootstrap.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
