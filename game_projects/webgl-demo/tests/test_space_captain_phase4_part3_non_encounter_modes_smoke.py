from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "game_projects" / "webgl-demo" / "tools" / "space_captain_phase4_part3_non_encounter_modes_smoke.py"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("space_captain_phase4_part3_non_encounter_modes_smoke", SMOKE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase4_part3_source_contracts() -> None:
    module = _load_smoke_module()
    checks = module._source_contract_checks()
    assert checks
    assert all(checks.values()), checks
