#!/usr/bin/env python3
"""Characterize bridge-viewscreen motion across tactical slices before browser integration.

This smoke deliberately uses the existing Battle 2 authoritative physics.  It does not
render a browser.  Instead it proves the contract the browser renderer will consume:

* tactical intent changes only at a configurable 1..5 second slice boundary;
* authoritative physics advances in smaller sub-slice steps;
* display samples may be generated at a faster viewport cadence from an authoritative
  position/velocity/acceleration anchor;
* an ordinary tactical boundary changes acceleration without snapping position/velocity;
* Impact is a physical discontinuity: position remains continuous, velocity changes, and
  the display prediction anchor is restarted at the exact impact time.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import sys
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

import space_captain_battle2_smoke as battle2


EPS = 1e-9


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(float(a["xM"]) - float(b["xM"]), float(a["yM"]) - float(b["yM"]))


def _velocity_distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(float(a["vxMps"]) - float(b["vxMps"]), float(a["vyMps"]) - float(b["vyMps"]))


def _battle_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        backend_url="",
        checkpoint_id="viewscreen-motion-smoke",
        checkpoint_sha256="viewscreen-motion-smoke",
        evidence_execution_mode="auto",
        questions_per_thought=20,
        duration_seconds=float(args.time_step_seconds) * int(args.slices),
        control_interval_seconds=float(args.time_step_seconds),
        viewport_hz=float(args.viewport_hz),
        initial_separation_m=float(args.initial_separation_m),
        thrust_accel_mps2=float(args.thrust_accel_mps2),
        projectile_speed_mps=6000.0,
        projectile_hit_radius_m=8.0,
        fire_cooldown_seconds=0.4,
        impact_delta_v_mps=float(args.impact_delta_v_mps),
        impact_damage_fraction=float(args.impact_damage_fraction),
        overload_impact_count=3,
        overload_window_seconds=1.0,
        overload_lock_seconds=0.75,
        generate_samples=False,
        generation_drain_seconds=0.0,
        generation_output_dir="",
        simulation_id="viewscreen-tactical-slice",
        simulation_seed=int(args.seed),
        request_timeout_seconds=10.0,
    )


def _state_snapshot(battle: battle2.Battle) -> dict[str, dict[str, float]]:
    return battle.snapshot_ships()


def _anchor(battle: battle2.Battle, kind: str) -> dict[str, Any]:
    return {
        "atSeconds": float(battle.sim_time),
        "kind": str(kind),
        "ships": _state_snapshot(battle),
        "accelerationsMps2": {
            ship_id: [float(ax), float(ay)]
            for ship_id, (ax, ay) in battle.accelerations().items()
        },
    }


def _predict_from_anchor(anchor: dict[str, Any], at_seconds: float) -> dict[str, dict[str, float]]:
    dt = max(0.0, float(at_seconds) - float(anchor["atSeconds"]))
    predicted: dict[str, dict[str, float]] = {}
    for ship_id, state in anchor["ships"].items():
        ax, ay = anchor["accelerationsMps2"][ship_id]
        vx = float(state["vxMps"])
        vy = float(state["vyMps"])
        predicted[ship_id] = {
            "xM": float(state["xM"]) + vx * dt + 0.5 * float(ax) * dt * dt,
            "yM": float(state["yM"]) + vy * dt + 0.5 * float(ay) * dt * dt,
            "vxMps": vx + float(ax) * dt,
            "vyMps": vy + float(ay) * dt,
        }
    return predicted


def _apply_scripted_slice_action(
    battle: battle2.Battle,
    *,
    slice_index: int,
    published_at: float,
) -> dict[str, Any]:
    # Use the real Battle 2 tactical-control packages.  Alternate lateral direction at
    # each slice so an ordinary tactical boundary changes acceleration while preserving
    # the already-integrated position and velocity.
    choices = (
        ("cross-port-fire", "cross-starboard-fire"),
        ("cross-starboard-fire", "cross-port-fire"),
    )
    alpha_id, beta_id = choices[slice_index % len(choices)]
    rows: dict[str, Any] = {}
    for captain_id, option_id in (("captain.alpha", alpha_id), ("captain.beta", beta_id)):
        captain = battle.captains[captain_id]
        candidate = battle.tactical_candidate(captain, option_id)
        if candidate is None:
            raise RuntimeError(f"missing Battle 2 tactical candidate {option_id}")
        captain.action = battle2.Action(
            maneuver=option_id,
            fire=bool(candidate["fire"]),
            accel_x_mps2=float(candidate["accelXMps2"]),
            accel_y_mps2=float(candidate["accelYMps2"]),
            horizon_seconds=float(battle.args.control_interval_seconds),
            published_at=float(published_at),
            decision_id=f"{captain_id}:slice:{slice_index:03d}",
        )
        rows[captain_id] = {
            "controlId": option_id,
            "accelerationMps2": [float(candidate["accelXMps2"]), float(candidate["accelYMps2"])],
            "publishedAtSeconds": float(published_at),
            "decisionId": captain.action.decision_id,
        }
    return rows


def run(args: argparse.Namespace) -> dict[str, Any]:
    time_step = float(args.time_step_seconds)
    physics_step = float(args.physics_step_seconds)
    viewport_hz = float(args.viewport_hz)
    slices = int(args.slices)
    duration = time_step * slices
    impact_at = float(args.impact_at_seconds) if args.impact_at_seconds is not None else time_step * 0.46
    if not (0.0 < impact_at < time_step):
        raise ValueError("impact must occur strictly inside the first tactical slice")

    battle = battle2.Battle(_battle_args(args))
    physics_samples: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    tactical_publications: list[dict[str, Any]] = []
    tactical_boundary_rows: list[dict[str, Any]] = []
    impact_row: dict[str, Any] | None = None
    try:
        tactical_publications.append({
            "sliceIndex": 0,
            "atSeconds": 0.0,
            "actions": _apply_scripted_slice_action(battle, slice_index=0, published_at=0.0),
        })
        battle.reset_viewport_anchor()
        anchors.append(_anchor(battle, "initial-tactical-slice"))

        impact_done = False
        next_tactical_index = 1
        next_tactical = time_step
        substep_count_by_slice = [0 for _ in range(slices)]

        while battle.sim_time < duration - EPS:
            current = float(battle.sim_time)
            next_physics = min(duration, current + physics_step)
            target = next_physics
            if not impact_done and current + EPS < impact_at < target - EPS:
                target = impact_at
            elif not impact_done and abs(impact_at - target) <= EPS:
                target = impact_at
            if next_tactical_index < slices and current + EPS < next_tactical < target - EPS:
                target = next_tactical
            elif next_tactical_index < slices and abs(next_tactical - target) <= EPS:
                target = next_tactical

            battle.integrate_to(target, "viewscreen-physics-substep")
            slice_index = min(slices - 1, int(max(0.0, target - EPS) // time_step))
            substep_count_by_slice[slice_index] += 1
            physics_samples.append({
                "simulationSeconds": float(battle.sim_time),
                "ships": _state_snapshot(battle),
            })

            if not impact_done and abs(battle.sim_time - impact_at) <= EPS:
                old_anchor = anchors[-1]
                predicted_pre = _predict_from_anchor(old_anchor, battle.sim_time)
                pre = _state_snapshot(battle)
                action_ids_before = {
                    captain_id: captain.action.decision_id
                    for captain_id, captain in battle.captains.items()
                }
                battle.impact({
                    "id": "viewscreen-scripted-impact",
                    "sourceShipId": "ship.beta",
                    "targetShipId": "ship.alpha",
                    "directionX": -0.6,
                    "directionY": 0.8,
                    "actualMissDistanceM": 0.0,
                })
                post = _state_snapshot(battle)
                action_ids_after = {
                    captain_id: captain.action.decision_id
                    for captain_id, captain in battle.captains.items()
                }
                anchors.append(_anchor(battle, "Impact"))
                impact_row = {
                    "atSeconds": float(battle.sim_time),
                    "positionDiscontinuityM": max(_distance(pre[s], post[s]) for s in pre),
                    "velocityDiscontinuityMps": max(_velocity_distance(pre[s], post[s]) for s in pre),
                    "preImpactPredictionErrorM": max(_distance(predicted_pre[s], pre[s]) for s in pre),
                    "actionDecisionIdsUnchanged": action_ids_before == action_ids_after,
                }
                impact_done = True

            if next_tactical_index < slices and abs(battle.sim_time - next_tactical) <= EPS:
                old_anchor = anchors[-1]
                predicted = _predict_from_anchor(old_anchor, battle.sim_time)
                before = _state_snapshot(battle)
                before_velocity = {ship_id: dict(row) for ship_id, row in before.items()}
                actions = _apply_scripted_slice_action(
                    battle,
                    slice_index=next_tactical_index,
                    published_at=battle.sim_time,
                )
                after = _state_snapshot(battle)
                battle.reset_viewport_anchor()
                anchors.append(_anchor(battle, "tactical-boundary"))
                tactical_publications.append({
                    "sliceIndex": next_tactical_index,
                    "atSeconds": float(battle.sim_time),
                    "actions": actions,
                })
                tactical_boundary_rows.append({
                    "atSeconds": float(battle.sim_time),
                    "positionDiscontinuityM": max(_distance(before[s], after[s]) for s in before),
                    "velocityDiscontinuityMps": max(_velocity_distance(before_velocity[s], after[s]) for s in before),
                    "incomingPredictionErrorM": max(_distance(predicted[s], before[s]) for s in before),
                })
                next_tactical_index += 1
                next_tactical = next_tactical_index * time_step

        # Generate the display stream after authoritative simulation.  Each frame uses
        # the newest trajectory anchor at or before the frame time.  Normal tactical
        # boundaries replace acceleration; Impact replaces the anchor state itself.
        frame_period = 1.0 / viewport_hz
        frames: list[dict[str, Any]] = []
        anchor_index = 0
        frame_count = int(math.floor(duration * viewport_hz + EPS)) + 1
        for frame_index in range(frame_count):
            at = min(duration, frame_index * frame_period)
            while anchor_index + 1 < len(anchors) and float(anchors[anchor_index + 1]["atSeconds"]) <= at + EPS:
                anchor_index += 1
            anchor = anchors[anchor_index]
            frames.append({
                "simulationSeconds": at,
                "anchorKind": anchor["kind"],
                "anchorAtSeconds": anchor["atSeconds"],
                "ships": _predict_from_anchor(anchor, at),
            })

        # Compare every authoritative sub-step sample against the exact same trajectory
        # model the viewport uses.  This is the browser-independent proof that higher-rate
        # drawing is interpolation/prediction of authoritative motion rather than a second
        # simulation.
        max_render_physics_error = 0.0
        for sample in physics_samples:
            at = float(sample["simulationSeconds"])
            selected = anchors[0]
            for candidate in anchors:
                if float(candidate["atSeconds"]) <= at + EPS:
                    selected = candidate
                else:
                    break
            predicted = _predict_from_anchor(selected, at)
            max_render_physics_error = max(
                max_render_physics_error,
                max(_distance(predicted[s], sample["ships"][s]) for s in sample["ships"]),
            )

        tactical_boundary_position_jump = max(
            [float(row["positionDiscontinuityM"]) for row in tactical_boundary_rows] or [0.0]
        )
        tactical_boundary_velocity_jump = max(
            [float(row["velocityDiscontinuityMps"]) for row in tactical_boundary_rows] or [0.0]
        )
        tactical_boundary_prediction_error = max(
            [float(row["incomingPredictionErrorM"]) for row in tactical_boundary_rows] or [0.0]
        )
        impact_position_jump = float((impact_row or {}).get("positionDiscontinuityM", math.inf))
        impact_velocity_jump = float((impact_row or {}).get("velocityDiscontinuityMps", 0.0))
        impact_prediction_error = float((impact_row or {}).get("preImpactPredictionErrorM", math.inf))
        action_times = [float(row["atSeconds"]) for row in tactical_publications]
        expected_action_times = [round(index * time_step, 10) for index in range(slices)]
        minimum_substeps = int(math.floor(time_step / physics_step - 1e-9))

        checks = {
            "tacticalSliceIsVariableOneToFiveSeconds": 1.0 <= time_step <= 5.0,
            "tacticalIntentPublishesOnlyAtSliceBoundaries": all(
                abs(actual - expected) <= 1e-8
                for actual, expected in zip(action_times, expected_action_times)
            ) and len(action_times) == len(expected_action_times),
            "authoritativePhysicsUsesSubSliceUpdates": all(count >= minimum_substeps for count in substep_count_by_slice),
            "viewportSamplesFasterThanPhysicsSubsteps": len(frames) > len(physics_samples),
            "viewportPredictionMatchesAuthoritativeSubSlicePhysics": max_render_physics_error < 1e-7,
            "ordinaryTacticalBoundaryDoesNotSnapPosition": tactical_boundary_position_jump < 1e-9,
            "ordinaryTacticalBoundaryDoesNotSnapVelocity": tactical_boundary_velocity_jump < 1e-9,
            "ordinaryTacticalBoundaryArrivesOnPredictedTrajectory": tactical_boundary_prediction_error < 1e-7,
            "ImpactFracturesTrajectoryAtExactTimestamp": bool(impact_row) and any(
                anchor["kind"] == "Impact" and abs(float(anchor["atSeconds"]) - impact_at) <= 1e-8
                for anchor in anchors
            ),
            "ImpactDoesNotTeleportPosition": impact_position_jump < 1e-9,
            "ImpactCreatesVelocityDiscontinuity": impact_velocity_jump > 0.0,
            "ImpactArrivesOnPreImpactPredictedTrajectory": impact_prediction_error < 1e-7,
            "ImpactDoesNotInventNewTacticalDecision": bool(impact_row) and impact_row["actionDecisionIdsUnchanged"] is True,
            "ImpactIsOnlyPhysicsVelocityDiscontinuity": battle.velocity_discontinuities == 1 and battle.non_impact_velocity_discontinuities == 0,
            "viewportAnchorResetsAtImpact": battle.viewport_resets_on_impact == 1,
        }
        failed = [name for name, value in checks.items() if value is not True]
        return {
            "ok": not failed,
            "schema": "game.spaceCaptainViewscreenTacticalSliceSmoke.v1",
            "contract": {
                "tacticalClock": "captain intent may change only at the configured tactical-slice boundary",
                "physicsClock": "authoritative motion is integrated in smaller fixed sub-slice updates",
                "viewClock": "display positions are generated at viewport cadence from the current authoritative trajectory anchor",
                "ordinaryBoundaryRule": "a tactical boundary may change acceleration but never snaps position or velocity",
                "impactRule": "Impact preserves position, changes velocity, and starts a new display/physics trajectory segment immediately",
            },
            "checks": checks,
            "failedChecks": failed,
            "metrics": {
                "timeStepSeconds": time_step,
                "physicsStepSeconds": physics_step,
                "viewportHz": viewport_hz,
                "sliceCount": slices,
                "durationSeconds": duration,
                "physicsSubstepCount": len(physics_samples),
                "physicsSubstepsBySlice": substep_count_by_slice,
                "viewportFrameCount": len(frames),
                "viewportFramesPerTacticalSlice": viewport_hz * time_step,
                "maximumViewportVsAuthoritativePhysicsErrorM": max_render_physics_error,
                "maximumTacticalBoundaryPositionDiscontinuityM": tactical_boundary_position_jump,
                "maximumTacticalBoundaryVelocityDiscontinuityMps": tactical_boundary_velocity_jump,
                "maximumTacticalBoundaryPredictionErrorM": tactical_boundary_prediction_error,
                "impactAtSeconds": impact_at,
                "impactPositionDiscontinuityM": impact_position_jump,
                "impactVelocityDiscontinuityMps": impact_velocity_jump,
                "impactPreTrajectoryPredictionErrorM": impact_prediction_error,
                "physicsVelocityDiscontinuityCount": battle.velocity_discontinuities,
                "nonImpactVelocityDiscontinuityCount": battle.non_impact_velocity_discontinuities,
            },
            "tacticalPublications": tactical_publications,
            "trajectoryAnchors": anchors,
            "impact": impact_row,
            "tacticalBoundaries": tactical_boundary_rows,
            "physicsSamples": physics_samples if args.include_samples else [],
            "viewportFrames": frames if args.include_samples else [],
        }
    finally:
        battle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the pre-browser Space Captain viewscreen tactical-slice motion contract.")
    parser.add_argument("--time-step-seconds", type=float, default=5.0, help="Tactical intent slice, constrained to 1..5 seconds (default: 5).")
    parser.add_argument("--physics-step-seconds", type=float, default=0.1, help="Authoritative sub-slice physics update (default: 0.1).")
    parser.add_argument("--viewport-hz", type=float, default=60.0, help="Display sampling cadence (default: 60).")
    parser.add_argument("--slices", type=int, default=2, help="Number of tactical slices to simulate (default: 2).")
    parser.add_argument("--impact-at-seconds", type=float, default=None, help="Impact time inside the first tactical slice; defaults to 46%% of the slice.")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--initial-separation-m", type=float, default=4000.0)
    parser.add_argument("--thrust-accel-mps2", type=float, default=25.0)
    parser.add_argument("--impact-delta-v-mps", type=float, default=0.75)
    parser.add_argument("--impact-damage-fraction", type=float, default=0.02)
    parser.add_argument("--include-samples", action="store_true", help="Include full 0.1s physics samples and viewport-frame rows in JSON output.")
    args = parser.parse_args()

    if not (1.0 <= float(args.time_step_seconds) <= 5.0):
        raise SystemExit("--time-step-seconds must be between 1 and 5")
    if float(args.physics_step_seconds) <= 0.0:
        raise SystemExit("--physics-step-seconds must be positive")
    if float(args.physics_step_seconds) >= float(args.time_step_seconds):
        raise SystemExit("--physics-step-seconds must be smaller than --time-step-seconds")
    if float(args.viewport_hz) <= 0.0:
        raise SystemExit("--viewport-hz must be positive")
    if int(args.slices) < 2:
        raise SystemExit("--slices must be at least 2")

    try:
        result = run(args)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
