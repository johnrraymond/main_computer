from __future__ import annotations

import sys

from tools.scheduler_lab.run_lab import main


if __name__ == "__main__":
    raise SystemExit(main(["--role", "workers", *sys.argv[1:]]))
