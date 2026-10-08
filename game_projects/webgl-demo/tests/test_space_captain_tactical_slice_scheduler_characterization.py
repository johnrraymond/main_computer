from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GAME_ROOT = ROOT / "game_projects" / "webgl-demo"
TOOL = GAME_ROOT / "tools" / "space_captain_fleshed_combat_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("space_captain_fleshed_slice_characterization", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fleshed_combat_game_defaults_now_match_five_second_tactical_slice() -> None:
    source = TOOL.read_text(encoding="utf-8")
    assert "CONTROL_DT = 0.5" in source
    assert "PHYSICS_DT = 0.1" in source
    assert '"--live-action-interval-seconds", type=float, default=5.0' in source


class _DelayedFakeDriver:
    def __init__(self, latency_seconds: float = 2.0) -> None:
        self.latency_seconds = float(latency_seconds)
        self.in_flight: dict[str, float] = {}
        self.launches: list[tuple[str, float, str]] = []
        self.publications: list[tuple[str, float, float]] = []

    def harvest_completed(self, smoke, ship):
        ready_at = self.in_flight.get(ship.id)
        if ready_at is None or smoke.time + 1e-9 < ready_at:
            return None
        self.in_flight.pop(ship.id, None)
        self.publications.append((ship.id, float(smoke.time), float(ready_at)))
        return {"maneuver": "intercept", "weapon": "hold", "defense": "none", "warp": "none"}

    def thought_in_flight(self, ship_id: str) -> bool:
        return ship_id in self.in_flight

    def launch_thought(self, smoke, ship, trigger: str) -> bool:
        if ship.id in self.in_flight:
            return False
        self.launches.append((ship.id, float(smoke.time), str(trigger)))
        self.in_flight[ship.id] = float(smoke.time) + self.latency_seconds
        return True


def _drive_control_clock(smoke, through_seconds: float) -> None:
    ticks = int(round(through_seconds / 0.5))
    for tick in range(0, ticks + 1):
        smoke.time = tick * 0.5
        smoke.control_tick()


def test_live_captain_launches_and_publishes_on_fixed_five_second_grid() -> None:
    module = load_module()
    driver = _DelayedFakeDriver(latency_seconds=2.0)
    smoke = module.CombatSmoke(
        7,
        20.0,
        live_action_driver=driver,
        live_action_interval_seconds=5.0,
    )

    # The t=0 request finishes at t=2, but its action must not become visible
    # in the middle of the 0..5 tactical slice.
    _drive_control_clock(smoke, 4.5)
    assert "alpha" not in smoke.live_current_actions

    smoke.time = 5.0
    smoke.control_tick()

    alpha_launches = [round(at, 3) for ship_id, at, _ in driver.launches if ship_id == "alpha"]
    alpha_publications = [round(at, 3) for ship_id, at, _ in driver.publications if ship_id == "alpha"]
    assert alpha_launches[:2] == [0.0, 5.0]
    assert alpha_publications == [5.0]

    for tick in range(11, 21):
        smoke.time = tick * 0.5
        smoke.control_tick()
    alpha_launches = [round(at, 3) for ship_id, at, _ in driver.launches if ship_id == "alpha"]
    alpha_publications = [round(at, 3) for ship_id, at, _ in driver.publications if ship_id == "alpha"]
    assert alpha_launches[:3] == [0.0, 5.0, 10.0]
    assert alpha_publications[:2] == [5.0, 10.0]


def test_late_live_captain_result_skips_slots_without_drifting_grid() -> None:
    module = load_module()
    driver = _DelayedFakeDriver(latency_seconds=6.0)
    smoke = module.CombatSmoke(
        7,
        25.0,
        live_action_driver=driver,
        live_action_interval_seconds=5.0,
    )

    _drive_control_clock(smoke, 20.0)

    alpha_launches = [round(at, 3) for ship_id, at, _ in driver.launches if ship_id == "alpha"]
    alpha_publications = [round(at, 3) for ship_id, at, _ in driver.publications if ship_id == "alpha"]

    # The first request misses the t=5 slot.  It may publish at t=10, but that
    # does not create a t=16 or t=21 clock.  Future work remains grid-anchored.
    assert alpha_launches[:3] == [0.0, 10.0, 20.0]
    assert alpha_publications[:2] == [10.0, 20.0]
    assert all(abs((at / 5.0) - round(at / 5.0)) < 1e-9 for at in alpha_launches)
    assert all(abs((at / 5.0) - round(at / 5.0)) < 1e-9 for at in alpha_publications)
