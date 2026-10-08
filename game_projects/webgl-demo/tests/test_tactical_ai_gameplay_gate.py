from __future__ import annotations

from pathlib import Path


GAME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GAME_ROOT.parents[1]


def test_game_defaults_tactical_ai_to_five_second_slice_and_three_pass_gate():
    html = (GAME_ROOT / "web" / "apps" / "webgl.html").read_text(encoding="utf-8")
    service = (REPO_ROOT / "main_computer" / "tactical_ai_service.py").read_text(encoding="utf-8")
    smoke = (GAME_ROOT / "tools" / "tactical_ai_frontend_integration_smoke.py").read_text(encoding="utf-8")
    assert '<option value="5" selected>5 seconds</option>' in html
    assert "self._warmup_target_seconds = 5.0" in service
    assert "self._tactical_time_step_seconds = 5" in service
    assert "time_step = self._positive_int(raw_time_step, 5, 1, 5)" in service
    assert 'choices=range(1, 6), default=5' in smoke


def test_start_new_game_auto_prepares_tactical_ai_and_passes_gate_to_renderer():
    desktop = (GAME_ROOT / "web" / "scripts" / "webgl-desktop.js").read_text(encoding="utf-8")
    assert 'WEBGL_TACTICAL_AI_DEFAULT_TIME_STEP_SECONDS = 5' in desktop
    assert 'WEBGL_TACTICAL_AI_REQUIRED_CONSECUTIVE_PASSES = 3' in desktop
    assert 'void webglPrepareTacticalAIForGame({force: true});' in desktop
    assert 'time_step_seconds: WEBGL_TACTICAL_AI_DEFAULT_TIME_STEP_SECONDS' in desktop
    assert 'consecutive_passes: WEBGL_TACTICAL_AI_REQUIRED_CONSECUTIVE_PASSES' in desktop
    assert 'tacticalAIReadiness: webglTacticalAIReadinessSnapshot' in desktop
    assert 'ensureTacticalAIReadiness: webglEnsureTacticalAIForShuttleExit' in desktop


def test_shuttle_exit_is_blocked_until_tactical_ai_reaches_three_of_three():
    scene = (GAME_ROOT / "web" / "scripts" / "scene-viewer.js").read_text(encoding="utf-8")
    assert 'const requiredPasses = 3;' in scene
    assert 'phase = "tactical-ai-check"' in scene
    assert 'waitingForTacticalAI' in scene
    assert 'if (!tacticalAI.ready)' in scene
    assert 'this.requestTacticalAIExitReadiness();' in scene
    assert 'Remain inside shuttle until Tactical AI reaches' in scene
    assert 'Tactical AI gate ${pilot.tacticalAIExitConsecutivePasses}/${pilot.tacticalAIExitRequiredPasses}' in scene
