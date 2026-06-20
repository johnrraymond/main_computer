from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_smoke_module():
    repo = Path(__file__).resolve().parents[1]
    script = repo / "scripts" / "smoke_foundationdb_credit_ledger_primitives.py"
    spec = importlib.util.spec_from_file_location("fdb_smoke_bootstrap", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_foundationdb_status_accepts_healthy_configured_cluster_without_database_available_phrase() -> None:
    module = _load_smoke_module()
    status_output = """
Using cluster file `/var/fdb/fdb.cluster'.

Configuration:
  Redundancy mode        - single
  Storage engine         - memory
  Log engine             - ssd-2
  Encryption at-rest     - disabled
  Coordinators           - 1
  Usable Regions         - 1

Cluster:
  FoundationDB processes - 1
  Zones                  - 1
  Machines               - 1

Data:
  Replication health     - Healthy
  Moving data            - 0.000 GB

Workload:
  Read rate              - 21 Hz
  Write rate             - 0 Hz
"""

    assert module._foundationdb_status_is_configured(status_output) is True


def test_foundationdb_status_rejects_unavailable_cluster() -> None:
    module = _load_smoke_module()

    assert module._foundationdb_status_is_configured("The database is unavailable.") is False
    assert module._foundationdb_status_is_configured("Unable to locate any services.") is False
