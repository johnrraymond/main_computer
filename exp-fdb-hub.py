from __future__ import annotations

# EXPERIMENTAL-ONLY: exp-fdb-hub.py is not the Hub intended for actual
# network deployment. Production Hub Control must deploy Stable Hub
# (main_computer.stable_hub via run-stable-hub.py), whose worker-home Hub
# handoff is authoritative. Keep this module for isolated experiments/tests.

from main_computer.exp_fdb_hub import main


if __name__ == "__main__":
    raise SystemExit(main())
