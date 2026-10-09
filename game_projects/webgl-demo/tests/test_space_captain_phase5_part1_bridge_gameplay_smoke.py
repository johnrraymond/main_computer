from __future__ import annotations

import importlib.util
from pathlib import Path

GAME_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = GAME_ROOT / "tools" / "space_captain_phase5_part1_bridge_gameplay_smoke.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("space_captain_phase5_part1_bridge_gameplay_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gameplay_contract_no_legacy_target_acquisition_and_power_output_only():
    checks = load_tool().source_contract_checks()
    assert all(checks.values()), checks


def test_production_scene_methods_extracted_for_execution():
    tool = load_tool()
    methods = tool.extract_scene_methods()
    assert len(methods) == len(tool.METHODS)
    for method_name in tool.METHODS:
        assert any(text.startswith(method_name + "(") for text in methods), method_name


def test_chromium_probe_exercises_authority_and_power_independently():
    source = load_tool().PROBE_JS.read_text(encoding="utf-8")
    for gate in (
        "encounterAlreadySelectedOutsideBridge",
        "offscreenEncounterAndCameraContinue",
        "powerToggleSucceedsEvenWhenSwitchingOff",
        "bridgeEntryImmediatelyShowsCombatObjective",
        "fireCommandAcceptedByRealAuthority",
        "fireDoesNotDirectlyDamageTarget",
        "destructionUpdatesObjectiveFromImpact",
        "destroyedEnemyObjectivePersistsAfterExplosion",
        "planetModeSkipsManualCentering",
    ):
        assert gate in source
