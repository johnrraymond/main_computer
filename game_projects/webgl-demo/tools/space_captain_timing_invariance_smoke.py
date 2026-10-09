#!/usr/bin/env python3
"""Phase 0: prove the Space Captain multirate model stays coherent as timing parameters vary.

This is not an optimizer.  It deliberately varies the tactical decision interval and
physics integration step and asks whether the *same architecture* still yields a
causally coherent encounter:

* tactical publications remain on the configured immutable grid;
* authoritative physics remains continuous between exact event anchors;
* impacts never teleport position and are the only allowed velocity discontinuity;
* browser rendering is independent of the authority cadence;
* soft target lock remains usable;
* changing render cadence does not change authoritative encounter state.

The default sweep covers tactical slices 1..5 seconds, physics steps 0.05/0.10/0.20
seconds, and synthetic Chromium render schedules 30/60/120/165 Hz.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
ROOT_TOOLS = REPO_ROOT / "tools"
for path in (HERE, ROOT_TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import space_captain_boarding_encounter_smoke as boarding
import playwright_chromium_code_smoke as browser_code_smoke

SCHEMA = "game.spaceCaptainTimingInvarianceSmoke.v1"
CHROMIUM_PROBE = HERE / "space_captain_timing_invariance_chromium_probe.js"
CONTRACT_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "space-captain-multirate-contract.js"
AUTHORITY_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-encounter-runtime.js"
PROJECTION_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-viewscreen-projection.js"
RUNTIME_JS = REPO_ROOT / "game_projects" / "webgl-demo" / "web" / "scripts" / "bridge-viewscreen-encounter-runtime.js"
DEFAULT_TACTICAL_SLICES = (1.0, 2.0, 3.0, 4.0, 5.0)
DEFAULT_PHYSICS_STEPS = (0.05, 0.10, 0.20)
DEFAULT_RENDER_HZ = (30.0, 60.0, 120.0, 165.0)
EPS = 1e-9


def _csv_positive_floats(text: str) -> tuple[float, ...]:
    try:
        values = tuple(float(item.strip()) for item in str(text).split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated numbers") from exc
    if not values or any((not math.isfinite(value) or value <= 0.0) for value in values):
        raise argparse.ArgumentTypeError("all values must be finite and > 0")
    return values


def _boarding_args(tactical_slice_seconds: float, physics_step_seconds: float) -> argparse.Namespace:
    return argparse.Namespace(
        time_step_seconds=float(tactical_slice_seconds),
        physics_step_seconds=float(physics_step_seconds),
        viewport_hz=60.0,
        slices=5,
        player_fire_at_seconds=None,
        opening_shot="hit",
        initial_separation_m=2600.0,
        thrust_accel_mps2=25.0,
        projectile_speed_mps=6000.0,
        impact_delta_v_mps=0.75,
        impact_damage_fraction=0.02,
        miss_distance_m=45.0,
        designed_combat_range_floor_m=900.0,
        view_half_width_m=3500.0,
        view_half_height_m=2000.0,
        soft_lock_zone=0.16,
        hard_lock_envelope=0.45,
        camera_response_seconds=0.35,
        include_samples=False,
    )


def _matrix(tactical_slices: Iterable[float], physics_steps: Iterable[float]) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for tactical in tactical_slices:
        if not 1.0 <= float(tactical) <= 5.0:
            raise ValueError(f"tactical slice {tactical} is outside the supported 1..5 second envelope")
        for physics in physics_steps:
            if float(physics) >= float(tactical):
                raise ValueError(f"physics step {physics} must be smaller than tactical slice {tactical}")
            result.append({
                "tacticalSliceSeconds": float(tactical),
                "physicsStepSeconds": float(physics),
            })
    return result


def _run_python_matrix(matrix: list[dict[str, float]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for config in matrix:
        tactical = config["tacticalSliceSeconds"]
        physics = config["physicsStepSeconds"]
        result = boarding.run(_boarding_args(tactical, physics))
        metrics = result.get("metrics") or {}
        view = result.get("viewScreen") or {}
        expected_fire = tactical * 2.34
        expected_reaction = tactical * 3.0
        coherence_checks = {
            "boardingEncounterPasses": result.get("ok") is True,
            "playerFireScalesWithTacticalSlice": abs(float(metrics.get("playerFireAtSeconds", math.inf)) - expected_fire) <= 1e-8,
            "hostileReactionUsesNextFixedBoundary": abs(float(metrics.get("hostileCombatReactionAtSeconds", math.inf)) - expected_reaction) <= 1e-8,
            "viewportPredictionRemainsAccurate": float(metrics.get("maximumViewportVsAuthoritativePhysicsErrorM", math.inf)) < 1e-7,
            "tacticalBoundaryPositionIsContinuous": float(metrics.get("maximumTacticalBoundaryPositionDiscontinuityM", math.inf)) < 1e-9,
            "tacticalBoundaryVelocityIsContinuous": float(metrics.get("maximumTacticalBoundaryVelocityDiscontinuityMps", math.inf)) < 1e-9,
            "hostilityTransitionIsContinuous": (
                float(metrics.get("hostilityTransitionPositionDiscontinuityM", math.inf)) < 1e-9
                and float(metrics.get("hostilityTransitionVelocityDiscontinuityMps", math.inf)) < 1e-9
            ),
            "impactIsSmallAndNonTeleporting": (
                float(metrics.get("openingImpactDeltaVMps", math.inf)) <= float(metrics.get("maximumOpeningImpactDeltaVMps", 0.0)) + EPS
                and bool((result.get("checks") or {}).get("ImpactDoesNotTeleportPosition"))
            ),
            "softLockRetainsEnemy": float(view.get("hardLockRetainedFraction", 0.0)) >= 0.995,
            "bothShipsRemainVisible": float(view.get("bothShipsVisibleFraction", 0.0)) >= 0.90,
            "onlyImpactCreatesVelocityDiscontinuity": int(metrics.get("nonImpactVelocityDiscontinuityCount", -1)) == 0,
        }
        failed = [name for name, passed in coherence_checks.items() if not passed]
        rows.append({
            "ok": not failed,
            **config,
            "checks": coherence_checks,
            "failedChecks": failed,
            "metrics": {
                "playerFireAtSeconds": metrics.get("playerFireAtSeconds"),
                "openingShotImpactAtSeconds": metrics.get("openingShotImpactAtSeconds"),
                "hostileCombatReactionAtSeconds": metrics.get("hostileCombatReactionAtSeconds"),
                "finalRangeM": metrics.get("finalRangeM"),
                "minimumRangeAfterHostileCombatReactionM": metrics.get("minimumRangeAfterHostileCombatReactionM"),
                "openingImpactDeltaVMps": metrics.get("openingImpactDeltaVMps"),
                "maximumViewportVsAuthoritativePhysicsErrorM": metrics.get("maximumViewportVsAuthoritativePhysicsErrorM"),
                "hardLockRetainedFraction": view.get("hardLockRetainedFraction"),
                "bothShipsVisibleFraction": view.get("bothShipsVisibleFraction"),
            },
        })

    convergence: list[dict[str, Any]] = []
    tactical_values = sorted({row["tacticalSliceSeconds"] for row in rows})
    for tactical in tactical_values:
        matches = [row for row in rows if row["tacticalSliceSeconds"] == tactical]
        impact_times = [float(row["metrics"]["openingShotImpactAtSeconds"]) for row in matches]
        final_ranges = [float(row["metrics"]["finalRangeM"]) for row in matches]
        reaction_times = [float(row["metrics"]["hostileCombatReactionAtSeconds"]) for row in matches]
        impact_dv = [float(row["metrics"]["openingImpactDeltaVMps"]) for row in matches]
        item = {
            "tacticalSliceSeconds": tactical,
            "physicsSteps": [row["physicsStepSeconds"] for row in matches],
            "maxImpactTimeSpreadSeconds": max(impact_times) - min(impact_times),
            "maxFinalRangeSpreadM": max(final_ranges) - min(final_ranges),
            "maxReactionTimeSpreadSeconds": max(reaction_times) - min(reaction_times),
            "maxImpactDeltaVSpreadMps": max(impact_dv) - min(impact_dv),
        }
        item["ok"] = (
            item["maxImpactTimeSpreadSeconds"] <= 1e-8
            and item["maxFinalRangeSpreadM"] <= 1e-6
            and item["maxReactionTimeSpreadSeconds"] <= 1e-8
            and item["maxImpactDeltaVSpreadMps"] <= 1e-9
        )
        convergence.append(item)
    return rows, convergence


def _run_chromium_matrix(
    matrix: list[dict[str, float]],
    render_hz_values: tuple[float, ...],
    *,
    headed: bool,
    timeout_seconds: float,
) -> dict[str, Any]:
    source = CONTRACT_JS.read_text(encoding="utf-8") + "\n" + AUTHORITY_JS.read_text(encoding="utf-8") + "\n" + PROJECTION_JS.read_text(encoding="utf-8") + "\n" + RUNTIME_JS.read_text(encoding="utf-8") + "\n" + CHROMIUM_PROBE.read_text(encoding="utf-8")
    return browser_code_smoke.run_smoke(
        source=source,
        user_args={"matrix": matrix, "renderHzValues": list(render_hz_values)},
        headed=headed,
        viewport=(1280, 800),
        timeout_seconds=timeout_seconds,
        fail_on_console_error=True,
    )


def run(
    *,
    tactical_slices: tuple[float, ...] = DEFAULT_TACTICAL_SLICES,
    physics_steps: tuple[float, ...] = DEFAULT_PHYSICS_STEPS,
    render_hz_values: tuple[float, ...] = DEFAULT_RENDER_HZ,
    headed: bool = False,
    timeout_seconds: float = 30.0,
    skip_chromium: bool = False,
) -> dict[str, Any]:
    matrix = _matrix(tactical_slices, physics_steps)
    python_rows, physics_convergence = _run_python_matrix(matrix)

    chromium: dict[str, Any] | None = None
    chromium_result: dict[str, Any] = {}
    if not skip_chromium:
        chromium = _run_chromium_matrix(
            matrix,
            render_hz_values,
            headed=headed,
            timeout_seconds=timeout_seconds,
        )
        raw = ((chromium.get("execution") or {}).get("result"))
        chromium_result = raw if isinstance(raw, dict) else {}

    checks = {
        "everyTimingPairProducesCoherentEncounter": all(row["ok"] for row in python_rows),
        "physicsResolutionChangesConvergeOnSameSolution": all(row["ok"] for row in physics_convergence),
        "tacticalCadenceChangesRemainSemanticallyCoherent": all(
            abs(float(row["metrics"]["hostileCombatReactionAtSeconds"]) - row["tacticalSliceSeconds"] * 3.0) <= 1e-8
            for row in python_rows
        ),
        "chromiumTimingMatrixPasses": skip_chromium or (chromium is not None and chromium.get("ok") is True),
        "renderCadenceDoesNotChangeAuthoritativeSolution": skip_chromium or bool(
            (chromium_result.get("checks") or {}).get("renderCadenceDoesNotChangeAuthoritativeSolution")
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]

    return {
        "ok": not failed,
        "schema": SCHEMA,
        "purpose": "prove tactical and physics timing values are parameters of one coherent multirate model rather than baked-in constants",
        "contract": {
            "tactical": "cadence may vary, but captain publications remain on the configured immutable tactical grid",
            "physics": "step may vary, but continuous trajectories converge and exact semantic events re-anchor at their true timestamps",
            "render": "render cadence is independent of authority cadence and cannot change authoritative outcome",
            "continuity": "ordinary tactical changes never teleport or snap velocity; impact may change velocity but never position",
            "camera": "soft target lock remains stable without hard-centering the hostile vessel",
        },
        "configuredEnvelope": {
            "tacticalSliceSeconds": list(tactical_slices),
            "physicsStepSeconds": list(physics_steps),
            "renderHz": list(render_hz_values),
            "timingPairCount": len(matrix),
            "browserCaseCount": 0 if skip_chromium else len(matrix) * len(render_hz_values),
        },
        "checks": checks,
        "failedChecks": failed,
        "pythonMatrix": python_rows,
        "physicsConvergenceByTacticalSlice": physics_convergence,
        "chromiumMatrix": chromium,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tactical-slices", type=_csv_positive_floats, default=DEFAULT_TACTICAL_SLICES, help="Comma-separated tactical slice seconds (default: 1,2,3,4,5).")
    parser.add_argument("--physics-steps", type=_csv_positive_floats, default=DEFAULT_PHYSICS_STEPS, help="Comma-separated physics step seconds (default: 0.05,0.1,0.2).")
    parser.add_argument("--render-hz", type=_csv_positive_floats, default=DEFAULT_RENDER_HZ, help="Comma-separated synthetic Chromium render cadences (default: 30,60,120,165).")
    parser.add_argument("--headed", action="store_true", help="Launch Chromium headed.")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--skip-chromium", action="store_true", help="Run only deterministic Python timing/physics characterization.")
    parser.add_argument("--output", type=Path)
    ns = parser.parse_args(argv)
    if ns.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    try:
        report = run(
            tactical_slices=tuple(ns.tactical_slices),
            physics_steps=tuple(ns.physics_steps),
            render_hz_values=tuple(ns.render_hz),
            headed=ns.headed,
            timeout_seconds=float(ns.timeout_seconds),
            skip_chromium=bool(ns.skip_chromium),
        )
    except (RuntimeError, ValueError) as exc:
        report = {"ok": False, "schema": SCHEMA, "error": f"{type(exc).__name__}: {exc}"}
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if ns.output:
        ns.output.parent.mkdir(parents=True, exist_ok=True)
        ns.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
