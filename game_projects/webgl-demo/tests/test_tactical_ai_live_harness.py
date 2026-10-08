from __future__ import annotations

import json
from pathlib import Path


GAME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = GAME_ROOT.parents[1]


def test_tactical_ai_harness_is_bundled_and_visible_below_strategic_ai():
    game = json.loads((GAME_ROOT / "game.json").read_text(encoding="utf-8"))
    bundles = game["web"]["bundles"]
    assert "web/styles/tactical-ai-debug.css" in bundles["styles"]
    assert "web/scripts/tactical-ai-debug-panel.js" in bundles["runtime-before-routing"]

    html = (GAME_ROOT / "web" / "apps" / "webgl.html").read_text(encoding="utf-8")
    assert 'class="ai-debug-launchers"' in html
    assert html.index('id="strategic-ai-debug-toggle"') < html.index('id="tactical-ai-debug-toggle"')
    assert 'id="tactical-ai-debug-panel"' in html
    assert 'id="tactical-ai-debug-prepare"' in html
    assert 'id="tactical-ai-debug-time-step"' in html
    for seconds in range(1, 6):
        assert f'<option value="{seconds}"' in html
    assert '<option value="5" selected>5 seconds</option>' in html
    assert 'id="tactical-ai-debug-max-latency"' not in html
    assert 'id="tactical-ai-debug-interval"' not in html
    assert 'id="tactical-ai-debug-start"' in html
    assert 'id="tactical-ai-debug-stop"' in html


def test_tactical_panel_uses_game_facing_api_and_performance_gate():
    script = (GAME_ROOT / "web" / "scripts" / "tactical-ai-debug-panel.js").read_text(encoding="utf-8")
    assert '"/api/applications/game/tactical-ai"' in script
    assert 'post("/prepare"' in script
    assert 'post("/battle/start"' in script
    assert 'post("/battle/stop"' in script
    assert 'performance.ready' in script
    assert 'time_step_seconds' in script
    assert 'timeStepMatches' in script
    assert '"restarting-ai"' in script
    assert 'perform(() => post("/prepare", prepareConfig(ui)))' in script
    assert 'RESTART' in script
    assert 'ui.start.disabled' in script


def test_viewport_routes_tactical_api_to_shared_combat_runtime():
    service = (REPO_ROOT / "main_computer" / "tactical_ai_service.py").read_text(encoding="utf-8")
    routes = (REPO_ROOT / "main_computer" / "viewport_routes_game.py").read_text(encoding="utf-8")
    dispatch = (REPO_ROOT / "main_computer" / "viewport_route_dispatch.py").read_text(encoding="utf-8")
    assert "space_captain_fleshed_combat_smoke.py" in service
    assert "runtime.LiveActionDriver" in service
    assert "runtime.CombatSmoke" in service
    assert '"/api/applications/game/tactical-ai/prepare"' in routes
    assert '"/api/applications/game/tactical-ai/battle/start"' in routes
    assert '"/api/applications/game/tactical-ai/status"' in dispatch


def test_battle_is_primed_before_t_zero_is_released():
    service = (REPO_ROOT / "main_computer" / "tactical_ai_service.py").read_text(encoding="utf-8")
    prime = service.index("# Prime both captains before simulation time starts")
    release = service.index('self._phase = "running"', prime)
    run = service.index("result = smoke.run()", release)
    assert prime < release < run
    assert 'self._battle_config["simulationReleasedAfterPrime"] = True' in service
