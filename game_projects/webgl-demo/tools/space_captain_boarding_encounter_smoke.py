#!/usr/bin/env python3
"""Smoke the first bridge encounter before browser integration.

The scenario is deliberately asymmetric:

* ship.alpha is the player vessel.  The player is emulated by scripted human actions.
* ship.beta is the hostile boarding vessel.  Its captain is emulated as a stateful
  deterministic captain so this smoke isolates encounter/physics/viewscreen contracts
  without requiring NanoJev wall-clock timing.
* before hostile fire, the hostile captain believes the player vessel is inactive and
  advances a boarding plan on the fixed tactical grid;
* the player's first weapon discharge changes awareness immediately without resetting
  physical space;
* the hostile captain first publishes a combat plan on the next tactical boundary;
* Battle 2 remains authoritative for motion and Impact(...) velocity discontinuities;
* a 60 Hz soft-lock viewscreen tracks the hostile vessel without hard-centering it;
* ordinary weapon impact contributes only a small center-of-mass velocity change;
* the hostile captain's first combat maneuver, not the hit impulse, opens stand-off range;
* the soft-lock viewscreen is stressed by that powered breakaway while retaining the target.

This is the pre-browser vertical-slice contract.  Once it passes, the browser should draw
these states rather than inventing a second simulation.
"""
from __future__ import annotations

import argparse
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
PLAYER_SHIP_ID = "ship.alpha"
HOSTILE_SHIP_ID = "ship.beta"
PLAYER_CAPTAIN_ID = "captain.alpha"
HOSTILE_CAPTAIN_ID = "captain.beta"
MAX_OPENING_IMPACT_DELTA_V_MPS = 1.0


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(float(a["xM"]) - float(b["xM"]), float(a["yM"]) - float(b["yM"]))


def _velocity_distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(float(a["vxMps"]) - float(b["vxMps"]), float(a["vyMps"]) - float(b["vyMps"]))


def _battle_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        backend_url="",
        checkpoint_id="boarding-encounter-emulator",
        checkpoint_sha256="boarding-encounter-emulator",
        evidence_execution_mode="auto",
        questions_per_thought=20,
        duration_seconds=float(args.time_step_seconds) * int(args.slices),
        control_interval_seconds=float(args.time_step_seconds),
        viewport_hz=float(args.viewport_hz),
        initial_separation_m=float(args.initial_separation_m),
        thrust_accel_mps2=float(args.thrust_accel_mps2),
        projectile_speed_mps=float(args.projectile_speed_mps),
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
        simulation_id="boarding-encounter",
        simulation_seed=0,
        request_timeout_seconds=10.0,
    )


def _snapshot(battle: battle2.Battle) -> dict[str, dict[str, float]]:
    return battle.snapshot_ships()


def _range_m(battle: battle2.Battle) -> float:
    a = battle.ships[PLAYER_SHIP_ID]
    b = battle.ships[HOSTILE_SHIP_ID]
    return math.hypot(float(b.x_m - a.x_m), float(b.y_m - a.y_m))


def _anchor(battle: battle2.Battle, kind: str) -> dict[str, Any]:
    return {
        "atSeconds": float(battle.sim_time),
        "kind": str(kind),
        "ships": _snapshot(battle),
        "accelerationsMps2": {
            ship_id: [float(ax), float(ay)]
            for ship_id, (ax, ay) in battle.accelerations().items()
        },
    }


def _predict_from_anchor(anchor: dict[str, Any], at_seconds: float) -> dict[str, dict[str, float]]:
    dt = max(0.0, float(at_seconds) - float(anchor["atSeconds"]))
    result: dict[str, dict[str, float]] = {}
    for ship_id, state in anchor["ships"].items():
        ax, ay = anchor["accelerationsMps2"][ship_id]
        vx = float(state["vxMps"])
        vy = float(state["vyMps"])
        result[ship_id] = {
            "xM": float(state["xM"]) + vx * dt + 0.5 * float(ax) * dt * dt,
            "yM": float(state["yM"]) + vy * dt + 0.5 * float(ay) * dt * dt,
            "vxMps": vx + float(ax) * dt,
            "vyMps": vy + float(ay) * dt,
            "damageFraction": float(state.get("damageFraction", 0.0)),
        }
    return result


def _unit(x: float, y: float) -> tuple[float, float]:
    mag = math.hypot(float(x), float(y))
    if mag <= 1e-12:
        return 1.0, 0.0
    return float(x) / mag, float(y) / mag


def _set_action(
    battle: battle2.Battle,
    captain_id: str,
    *,
    control_id: str,
    accel_x: float,
    accel_y: float,
    fire: bool,
    published_at: float,
    slice_index: int,
) -> dict[str, Any]:
    captain = battle.captains[captain_id]
    captain.action = battle2.Action(
        maneuver=str(control_id),
        fire=bool(fire),
        accel_x_mps2=float(accel_x),
        accel_y_mps2=float(accel_y),
        horizon_seconds=float(battle.args.control_interval_seconds),
        published_at=float(published_at),
        decision_id=f"{captain_id}:encounter:{slice_index:03d}",
    )
    return {
        "captainId": captain_id,
        "controlId": str(control_id),
        "accelerationMps2": [float(accel_x), float(accel_y)],
        "fire": bool(fire),
        "publishedAtSeconds": float(published_at),
        "decisionId": captain.action.decision_id,
    }


def _boarding_action(battle: battle2.Battle, slice_index: int, published_at: float) -> dict[str, Any]:
    hostile = battle.ships[HOSTILE_SHIP_ID]
    player = battle.ships[PLAYER_SHIP_ID]
    tx, ty = _unit(player.x_m - hostile.x_m, player.y_m - hostile.y_m)
    speed_x = float(hostile.v_mps - player.v_mps)
    speed_y = float(hostile.vy_mps - player.vy_mps)

    phase = min(int(slice_index), 2)
    if phase == 0:
        # Close assertively on the apparently inactive vessel.
        accel_x, accel_y = tx * 5.0, ty * 5.0
        control_id = "boarding-approach"
        boarding_state = "approach"
    elif phase == 1:
        # Begin matching velocity while retaining closure.
        bx, by = _unit(-speed_x, -speed_y)
        mix_x = 0.40 * tx + 0.60 * bx
        mix_y = 0.40 * ty + 0.60 * by
        ux, uy = _unit(mix_x, mix_y)
        accel_x, accel_y = ux * 8.0, uy * 8.0
        control_id = "boarding-match-velocity"
        boarding_state = "velocity-match"
    else:
        # Final alignment is gentle; the ship remains physically committed to boarding.
        bx, by = _unit(-speed_x, -speed_y)
        mix_x = 0.65 * tx + 0.35 * bx
        mix_y = 0.65 * ty + 0.35 * by
        ux, uy = _unit(mix_x, mix_y)
        accel_x, accel_y = ux * 4.0, uy * 4.0
        control_id = "boarding-align"
        boarding_state = "boarding-prep"

    row = _set_action(
        battle,
        HOSTILE_CAPTAIN_ID,
        control_id=control_id,
        accel_x=accel_x,
        accel_y=accel_y,
        fire=False,
        published_at=published_at,
        slice_index=slice_index,
    )
    row.update({
        "mode": "boarding",
        "boardingState": boarding_state,
        "awareness": "player-believed-inactive",
        "mission": "board-apparently-inactive-player-ship",
    })
    return row


def _combat_action(
    battle: battle2.Battle,
    captain_id: str,
    option_id: str,
    published_at: float,
    slice_index: int,
    *,
    role: str,
) -> dict[str, Any]:
    captain = battle.captains[captain_id]
    candidate = battle.tactical_candidate(captain, option_id)
    if candidate is None:
        raise RuntimeError(f"missing Battle 2 tactical candidate {option_id}")
    row = _set_action(
        battle,
        captain_id,
        control_id=option_id,
        accel_x=float(candidate["accelXMps2"]),
        accel_y=float(candidate["accelYMps2"]),
        fire=bool(candidate["fire"]),
        published_at=published_at,
        slice_index=slice_index,
    )
    row.update({
        "mode": "combat",
        "role": role,
        "mission": (
            "human-player-scripted-combat"
            if captain_id == PLAYER_CAPTAIN_ID
            else "defeat-active-player-and-preserve-boarding-vessel"
        ),
    })
    return row



def _hostile_breakaway_action(
    battle: battle2.Battle,
    published_at: float,
    slice_index: int,
    combat_index: int,
) -> dict[str, Any]:
    """Abort boarding under thrust; weapon impact itself is not the range-control mechanism."""
    hostile = battle.ships[HOSTILE_SHIP_ID]
    player = battle.ships[PLAYER_SHIP_ID]
    away_x, away_y = _unit(hostile.x_m - player.x_m, hostile.y_m - player.y_m)
    tangent_x, tangent_y = -away_y, away_x

    # Keep a strong lateral component so the break is legible on the viewscreen while
    # still opening range.  At the next slice the lateral sign flips, producing a real
    # powered crossing maneuver rather than a weapon-induced bounce.
    if combat_index % 2 == 0:
        mix_x = 0.70 * away_x + 0.71 * tangent_x
        mix_y = 0.70 * away_y + 0.71 * tangent_y
        control_id = "break-away-port-fire"
    else:
        mix_x = 0.70 * away_x - 0.71 * tangent_x
        mix_y = 0.70 * away_y - 0.71 * tangent_y
        control_id = "break-away-starboard-fire"

    ux, uy = _unit(mix_x, mix_y)
    thrust = float(battle.args.thrust_accel_mps2)
    row = _set_action(
        battle,
        HOSTILE_CAPTAIN_ID,
        control_id=control_id,
        accel_x=ux * thrust,
        accel_y=uy * thrust,
        fire=True,
        published_at=published_at,
        slice_index=slice_index,
    )
    row.update({
        "mode": "combat",
        "role": "hostile-captain-emulator",
        "mission": "defeat-active-player-and-preserve-boarding-vessel",
        "awareness": "active-hostile-player",
        "rangeIntent": "abort-boarding-and-open-stand-off-range",
    })
    return row

def _player_hold(battle: battle2.Battle, slice_index: int, published_at: float) -> dict[str, Any]:
    row = _set_action(
        battle,
        PLAYER_CAPTAIN_ID,
        control_id="player-observe-hold",
        accel_x=0.0,
        accel_y=0.0,
        fire=False,
        published_at=published_at,
        slice_index=slice_index,
    )
    row.update({
        "mode": "player-observation",
        "role": "scripted-human-emulator",
        "mission": "observe-and-interrupt-boarding-before-completion",
    })
    return row


def _publish_slice(
    battle: battle2.Battle,
    *,
    slice_index: int,
    at_seconds: float,
    hostile_awareness: str,
) -> dict[str, Any]:
    if hostile_awareness == "player-believed-inactive":
        player = _player_hold(battle, slice_index, at_seconds)
        hostile = _boarding_action(battle, slice_index, at_seconds)
    else:
        combat_index = max(0, slice_index - 3)
        player_options = ("pressure-starboard-fire", "intercept-fire", "cross-port-fire")
        player = _combat_action(
            battle,
            PLAYER_CAPTAIN_ID,
            player_options[combat_index % len(player_options)],
            at_seconds,
            slice_index,
            role="scripted-human-emulator",
        )
        hostile = _hostile_breakaway_action(
            battle,
            at_seconds,
            slice_index,
            combat_index,
        )
    return {
        "sliceIndex": int(slice_index),
        "atSeconds": float(at_seconds),
        "actions": {
            PLAYER_CAPTAIN_ID: player,
            HOSTILE_CAPTAIN_ID: hostile,
        },
    }


def _soft_lock_view(
    frames: list[dict[str, Any]],
    *,
    viewport_hz: float,
    half_width_m: float,
    half_height_m: float,
    soft_zone: float,
    hard_zone: float,
    response_seconds: float,
    impact_at: float | None,
    combat_break_at: float | None,
    combat_break_window_seconds: float,
    include_samples: bool,
) -> dict[str, Any]:
    if not frames:
        raise RuntimeError("soft-lock view requires frames")
    target0 = frames[0]["ships"][HOSTILE_SHIP_ID]
    # Deliberately begin with the target off-center but already acquired. A true hard
    # lock would force offset=0 every frame; the soft lock is allowed to breathe.
    camera_x = float(target0["xM"]) - 0.08 * half_width_m
    camera_y = float(target0["yM"]) + 0.035 * half_height_m
    prior_camera_x = camera_x
    prior_camera_y = camera_y
    prior_at = float(frames[0]["simulationSeconds"])

    rows: list[dict[str, Any]] = []
    maximum_offset = 0.0
    mean_offset_sum = 0.0
    locked_count = 0
    both_visible_count = 0
    camera_snap_count = 0
    max_camera_step = 0.0
    total_camera_travel = 0.0
    impact_offset_before: float | None = None
    impact_offset_after: float | None = None
    combat_break_offset: float | None = None
    combat_break_recovery_target: float | None = None
    combat_break_recovered_at: float | None = None

    for index, frame in enumerate(frames):
        at = float(frame["simulationSeconds"])
        dt = max(0.0, at - prior_at) if index else 0.0
        target = frame["ships"][HOSTILE_SHIP_ID]
        player = frame["ships"][PLAYER_SHIP_ID]

        raw_x = (float(target["xM"]) - camera_x) / half_width_m
        raw_y = (float(target["yM"]) - camera_y) / half_height_m
        desired_x = camera_x
        desired_y = camera_y
        if abs(raw_x) > soft_zone:
            desired_x = float(target["xM"]) - math.copysign(soft_zone * half_width_m, raw_x)
        if abs(raw_y) > soft_zone:
            desired_y = float(target["yM"]) - math.copysign(soft_zone * half_height_m, raw_y)

        if dt > 0.0:
            alpha = 1.0 - math.exp(-dt / response_seconds)
            camera_x += (desired_x - camera_x) * alpha
            camera_y += (desired_y - camera_y) * alpha

        camera_step = math.hypot(camera_x - prior_camera_x, camera_y - prior_camera_y)
        total_camera_travel += camera_step
        max_camera_step = max(max_camera_step, camera_step)
        if index and camera_step > max(25.0, 0.025 * half_width_m):
            camera_snap_count += 1

        target_x = (float(target["xM"]) - camera_x) / half_width_m
        target_y = (float(target["yM"]) - camera_y) / half_height_m
        player_x = (float(player["xM"]) - camera_x) / half_width_m
        player_y = (float(player["yM"]) - camera_y) / half_height_m
        offset = math.hypot(target_x, target_y)
        maximum_offset = max(maximum_offset, offset)
        mean_offset_sum += offset
        locked = abs(target_x) <= hard_zone and abs(target_y) <= hard_zone
        if locked:
            locked_count += 1
        if abs(target_x) <= 1.0 and abs(target_y) <= 1.0 and abs(player_x) <= 1.0 and abs(player_y) <= 1.0:
            both_visible_count += 1

        # With realistic impact momentum, the hit should barely perturb screen-space
        # translation. Record the last frame before and first frame after impact so the
        # smoke can prove that the camera stress comes from powered maneuver instead.
        if impact_at is not None:
            if at < impact_at - EPS:
                impact_offset_before = offset
            elif impact_offset_after is None:
                impact_offset_after = offset

        # The first hostile combat plan is the intentional viewscreen stressor. It
        # changes acceleration continuously; the soft lock must follow without snapping
        # and measurably settle toward its preferred framing within one tactical slice.
        if combat_break_at is not None and at >= combat_break_at - EPS:
            if combat_break_offset is None:
                combat_break_offset = offset
                combat_break_recovery_target = 0.80 * offset
            elif (
                combat_break_recovered_at is None
                and combat_break_recovery_target is not None
                and at <= combat_break_at + combat_break_window_seconds + EPS
                and offset <= combat_break_recovery_target
            ):
                combat_break_recovered_at = at

        if include_samples:
            rows.append({
                "simulationSeconds": at,
                "cameraCenterM": [camera_x, camera_y],
                "targetOffsetNormalized": [target_x, target_y],
                "playerOffsetNormalized": [player_x, player_y],
                "targetInsideHardLock": locked,
                "trajectoryAnchorKind": frame["anchorKind"],
                "trajectoryAnchorAtSeconds": frame["anchorAtSeconds"],
            })

        prior_camera_x = camera_x
        prior_camera_y = camera_y
        prior_at = at

    total = len(frames)
    impact_offset_step = None
    if impact_offset_before is not None and impact_offset_after is not None:
        impact_offset_step = abs(impact_offset_after - impact_offset_before)
    break_recovery_seconds = math.inf
    if combat_break_at is not None and combat_break_recovered_at is not None:
        break_recovery_seconds = max(0.0, combat_break_recovered_at - combat_break_at)

    return {
        "mode": "enemy-soft-target-lock",
        "targetShipId": HOSTILE_SHIP_ID,
        "viewHalfWidthM": half_width_m,
        "viewHalfHeightM": half_height_m,
        "softZoneNormalized": soft_zone,
        "hardLockEnvelopeNormalized": hard_zone,
        "responseSeconds": response_seconds,
        "maximumTargetOffsetNormalized": maximum_offset,
        "meanTargetOffsetNormalized": mean_offset_sum / max(1, total),
        "hardLockRetainedFraction": locked_count / max(1, total),
        "bothShipsVisibleFraction": both_visible_count / max(1, total),
        "cameraSnapCount": camera_snap_count,
        "maximumCameraStepM": max_camera_step,
        "totalCameraTravelM": total_camera_travel,
        "impactTargetOffsetStepNormalized": impact_offset_step,
        "combatBreakAtSeconds": combat_break_at,
        "combatBreakOffsetAtStartNormalized": combat_break_offset,
        "combatBreakRecoveryTargetNormalized": combat_break_recovery_target,
        "combatBreakRecoveredAtSeconds": combat_break_recovered_at,
        "combatBreakRecoverySeconds": break_recovery_seconds,
        "samples": rows,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    time_step = float(args.time_step_seconds)
    physics_step = float(args.physics_step_seconds)
    viewport_hz = float(args.viewport_hz)
    slices = int(args.slices)
    duration = time_step * slices
    fire_at = (
        float(args.player_fire_at_seconds)
        if args.player_fire_at_seconds is not None
        else time_step * 2.34
    )
    boarding_deadline = time_step * 4.0
    if not (0.0 < fire_at < boarding_deadline < duration + EPS):
        raise ValueError("player fire must occur after t=0 and before the boarding deadline, with the deadline inside the simulation")

    battle = battle2.Battle(_battle_args(args))
    # Deterministic first-encounter geometry: player ship is initially inert while the
    # hostile vessel is already closing from a visibly offset boarding approach.
    player = battle.ships[PLAYER_SHIP_ID]
    hostile = battle.ships[HOSTILE_SHIP_ID]
    player.x_m = 0.0
    player.y_m = 0.0
    player.v_mps = 0.0
    player.vy_mps = 0.0
    player.damage = 0.0
    hostile.x_m = float(args.initial_separation_m)
    hostile.y_m = 0.23 * float(args.initial_separation_m)
    hostile.v_mps = -85.0
    hostile.vy_mps = -12.0
    hostile.damage = 0.0

    hostile_awareness = "player-believed-inactive"
    encounter_phase = "boarding-approach"
    boarding_state = "approach"
    events: list[dict[str, Any]] = []
    publications: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    physics_samples: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    impact_row: dict[str, Any] | None = None
    fire_row: dict[str, Any] | None = None
    impact_at: float | None = None
    miss_resolve_at: float | None = None
    projectile: dict[str, Any] | None = None

    try:
        publications.append(_publish_slice(
            battle,
            slice_index=0,
            at_seconds=0.0,
            hostile_awareness=hostile_awareness,
        ))
        battle.reset_viewport_anchor()
        anchors.append(_anchor(battle, "initial-boarding-slice"))
        events.append({
            "type": "EncounterPhase",
            "simulationSeconds": 0.0,
            "phase": encounter_phase,
            "hostileAwareness": hostile_awareness,
            "boardingState": boarding_state,
        })

        next_boundary_index = 1
        next_boundary = time_step
        fired = False
        shot_resolved = False
        min_range_before_fire = _range_m(battle)
        range_at_fire = math.nan
        hostility_transition_position_jump = math.inf
        hostility_transition_velocity_jump = math.inf

        while battle.sim_time < duration - EPS:
            current = float(battle.sim_time)
            target = min(duration, current + physics_step)
            event_targets: list[float] = []
            if not fired and current + EPS < fire_at <= target + EPS:
                event_targets.append(fire_at)
            if impact_at is not None and not shot_resolved and current + EPS < impact_at <= target + EPS:
                event_targets.append(impact_at)
            if miss_resolve_at is not None and not shot_resolved and current + EPS < miss_resolve_at <= target + EPS:
                event_targets.append(miss_resolve_at)
            if next_boundary_index < slices and current + EPS < next_boundary <= target + EPS:
                event_targets.append(next_boundary)
            if event_targets:
                # Prefer the exact semantic event timestamp over an accumulated
                # floating-point physics-step value that is merely epsilon-close.
                target = min(event_targets)

            battle.integrate_to(target, "boarding-encounter-physics-substep")
            physics_samples.append({
                "simulationSeconds": float(battle.sim_time),
                "ships": _snapshot(battle),
                "rangeM": _range_m(battle),
            })
            if not fired:
                min_range_before_fire = min(min_range_before_fire, _range_m(battle))

            if not fired and abs(battle.sim_time - fire_at) <= EPS:
                pre = _snapshot(battle)
                range_at_fire = _range_m(battle)
                fired = True
                hostile_awareness = "active-hostile-player"
                encounter_phase = "hostile-reaction-pending"
                boarding_state = "aborted-by-hostile-fire"
                solution = battle.projectile_solution(player, hostile)
                if solution is None:
                    raise RuntimeError("opening player shot has no projectile solution")
                projectile = {
                    "id": "opening-player-shot",
                    "sourceShipId": PLAYER_SHIP_ID,
                    "targetShipId": HOSTILE_SHIP_ID,
                    "firedAtSeconds": float(battle.sim_time),
                    "arrivalSeconds": float(battle.sim_time) + float(solution["travelSeconds"]),
                    "directionX": float(solution["directionX"]),
                    "directionY": float(solution["directionY"]),
                    "actualMissDistanceM": 0.0 if args.opening_shot == "hit" else float(args.miss_distance_m),
                    "outcome": "in-flight",
                }
                if args.opening_shot == "hit":
                    impact_at = float(projectile["arrivalSeconds"])
                else:
                    miss_resolve_at = float(projectile["arrivalSeconds"])
                post = _snapshot(battle)
                hostility_transition_position_jump = max(_distance(pre[s], post[s]) for s in pre)
                hostility_transition_velocity_jump = max(_velocity_distance(pre[s], post[s]) for s in pre)
                fire_row = {
                    "type": "PlayerWeaponFired",
                    "simulationSeconds": float(battle.sim_time),
                    "rangeM": range_at_fire,
                    "openingShot": args.opening_shot,
                    "projectileArrivalSeconds": float(projectile["arrivalSeconds"]),
                    "hostileAwarenessBefore": "player-believed-inactive",
                    "hostileAwarenessAfter": hostile_awareness,
                    "encounterPhaseAfter": encounter_phase,
                    "positionDiscontinuityM": hostility_transition_position_jump,
                    "velocityDiscontinuityMps": hostility_transition_velocity_jump,
                }
                events.append(fire_row)

            if impact_at is not None and not shot_resolved and abs(battle.sim_time - impact_at) <= EPS:
                if projectile is None:
                    raise RuntimeError("impact scheduled without projectile")
                selected_anchor = anchors[-1]
                predicted = _predict_from_anchor(selected_anchor, battle.sim_time)
                pre = _snapshot(battle)
                battle.impact(projectile)
                post = _snapshot(battle)
                anchors.append(_anchor(battle, "Impact"))
                impact_row = {
                    "type": "Impact",
                    "simulationSeconds": float(battle.sim_time),
                    "positionDiscontinuityM": max(_distance(pre[s], post[s]) for s in pre),
                    "velocityDiscontinuityMps": max(_velocity_distance(pre[s], post[s]) for s in pre),
                    "incomingPredictionErrorM": max(_distance(predicted[s], pre[s]) for s in pre),
                    "targetShipId": HOSTILE_SHIP_ID,
                }
                events.append(dict(impact_row))
                shot_resolved = True

            if miss_resolve_at is not None and not shot_resolved and abs(battle.sim_time - miss_resolve_at) <= EPS:
                if projectile is None:
                    raise RuntimeError("miss scheduled without projectile")
                projectile["outcome"] = "miss"
                events.append({
                    "type": "PlayerOpeningShotMissed",
                    "simulationSeconds": float(battle.sim_time),
                    "missDistanceM": float(args.miss_distance_m),
                })
                shot_resolved = True

            if next_boundary_index < slices and abs(battle.sim_time - next_boundary) <= EPS:
                selected_anchor = anchors[-1]
                predicted = _predict_from_anchor(selected_anchor, battle.sim_time)
                before = _snapshot(battle)
                publication = _publish_slice(
                    battle,
                    slice_index=next_boundary_index,
                    at_seconds=battle.sim_time,
                    hostile_awareness=hostile_awareness,
                )
                if hostile_awareness == "active-hostile-player":
                    encounter_phase = "combat"
                else:
                    hostile_row = publication["actions"][HOSTILE_CAPTAIN_ID]
                    boarding_state = str(hostile_row.get("boardingState") or boarding_state)
                    encounter_phase = f"boarding-{boarding_state}"
                after = _snapshot(battle)
                battle.reset_viewport_anchor()
                anchors.append(_anchor(battle, "tactical-boundary"))
                publications.append(publication)
                boundary_rows.append({
                    "atSeconds": float(battle.sim_time),
                    "sliceIndex": next_boundary_index,
                    "positionDiscontinuityM": max(_distance(before[s], after[s]) for s in before),
                    "velocityDiscontinuityMps": max(_velocity_distance(before[s], after[s]) for s in before),
                    "incomingPredictionErrorM": max(_distance(predicted[s], before[s]) for s in before),
                    "encounterPhase": encounter_phase,
                    "hostileMode": publication["actions"][HOSTILE_CAPTAIN_ID]["mode"],
                })
                events.append({
                    "type": "TacticalBoundary",
                    "simulationSeconds": float(battle.sim_time),
                    "sliceIndex": next_boundary_index,
                    "encounterPhase": encounter_phase,
                    "hostileMode": publication["actions"][HOSTILE_CAPTAIN_ID]["mode"],
                })
                next_boundary_index += 1
                next_boundary = next_boundary_index * time_step

        # Generate an exact display trajectory from the authoritative trajectory anchors.
        frame_period = 1.0 / viewport_hz
        frame_count = int(math.floor(duration * viewport_hz + EPS)) + 1
        frames: list[dict[str, Any]] = []
        anchor_index = 0
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

        max_view_physics_error = 0.0
        for sample in physics_samples:
            at = float(sample["simulationSeconds"])
            selected = anchors[0]
            for candidate in anchors:
                if float(candidate["atSeconds"]) <= at + EPS:
                    selected = candidate
                else:
                    break
            predicted = _predict_from_anchor(selected, at)
            max_view_physics_error = max(
                max_view_physics_error,
                max(_distance(predicted[s], sample["ships"][s]) for s in sample["ships"]),
            )

        view = _soft_lock_view(
            frames,
            viewport_hz=viewport_hz,
            half_width_m=float(args.view_half_width_m),
            half_height_m=float(args.view_half_height_m),
            soft_zone=float(args.soft_lock_zone),
            hard_zone=float(args.hard_lock_envelope),
            response_seconds=float(args.camera_response_seconds),
            impact_at=(impact_row or {}).get("simulationSeconds"),
            combat_break_at=math.ceil((fire_at - EPS) / time_step) * time_step,
            combat_break_window_seconds=time_step,
            include_samples=bool(args.include_samples),
        )

        publication_times = [float(row["atSeconds"]) for row in publications]
        expected_times = [float(index) * time_step for index in range(slices)]
        hostile_pre_fire_combat = [
            row for row in publications
            if float(row["atSeconds"]) < fire_at - EPS
            and row["actions"][HOSTILE_CAPTAIN_ID]["mode"] == "combat"
        ]
        hostile_post_fire_combat = [
            row for row in publications
            if float(row["atSeconds"]) > fire_at + EPS
            and row["actions"][HOSTILE_CAPTAIN_ID]["mode"] == "combat"
        ]
        next_reaction_boundary = math.ceil((fire_at - EPS) / time_step) * time_step
        first_hostile_combat_time = (
            float(hostile_post_fire_combat[0]["atSeconds"])
            if hostile_post_fire_combat else math.inf
        )
        max_boundary_position_jump = max([float(r["positionDiscontinuityM"]) for r in boundary_rows] or [0.0])
        max_boundary_velocity_jump = max([float(r["velocityDiscontinuityMps"]) for r in boundary_rows] or [0.0])
        max_boundary_prediction_error = max([float(r["incomingPredictionErrorM"]) for r in boundary_rows] or [0.0])

        speed_contract_ok = all(
            abs(float(state.get("speedMps", math.nan)) - math.hypot(float(state["vxMps"]), float(state["vyMps"]))) < 1e-9
            and abs(float(state.get("vMps", math.nan)) - float(state.get("speedMps", math.nan))) < 1e-9
            for anchor in anchors
            for state in anchor["ships"].values()
        )
        designed_range_floor_m = float(args.designed_combat_range_floor_m)
        post_combat_ranges = [
            float(sample["rangeM"])
            for sample in physics_samples
            if float(sample["simulationSeconds"]) >= first_hostile_combat_time - EPS
        ]
        minimum_range_after_combat = min(post_combat_ranges or [_range_m(battle)])
        range_floor_ok = minimum_range_after_combat >= designed_range_floor_m - 1e-9
        default_breakaway_stress_required = abs(time_step - 5.0) <= EPS
        default_breakaway_stress_ok = (
            not default_breakaway_stress_required
            or (
                math.isfinite(float(view["combatBreakRecoverySeconds"]))
                and 0.0 < float(view["combatBreakRecoverySeconds"]) <= time_step + EPS
            )
        )

        if args.opening_shot == "hit":
            opening_delta_v = float((impact_row or {}).get("velocityDiscontinuityMps", math.inf))
            shot_physics_check = bool(impact_row) and opening_delta_v > 0.0
            impact_translation_small = opening_delta_v <= MAX_OPENING_IMPACT_DELTA_V_MPS + EPS
            impact_position_ok = bool(impact_row) and float(impact_row["positionDiscontinuityM"]) < 1e-9
            impact_prediction_ok = bool(impact_row) and float(impact_row["incomingPredictionErrorM"]) < 1e-7
            expected_velocity_discontinuities = 1
        else:
            opening_delta_v = 0.0
            shot_physics_check = impact_row is None and battle.velocity_discontinuities == 0
            impact_translation_small = True
            impact_position_ok = True
            impact_prediction_ok = True
            expected_velocity_discontinuities = 0

        checks = {
            "tacticalSliceIsVariableOneToFiveSeconds": 1.0 <= time_step <= 5.0,
            "tacticalPublicationsStayOnFixedGrid": len(publication_times) == len(expected_times) and all(
                abs(actual - expected) <= 1e-8 for actual, expected in zip(publication_times, expected_times)
            ),
            "hostileBeginsBoardingOriented": publications[0]["actions"][HOSTILE_CAPTAIN_ID]["mode"] == "boarding",
            "hostileDoesNotTreatPlayerAsHostileBeforeFire": len(hostile_pre_fire_combat) == 0,
            "playerFireTransitionsEncounterImmediately": bool(fire_row)
            and fire_row["hostileAwarenessAfter"] == "active-hostile-player",
            "playerFiresBeforeBoardingDeadline": fire_at < boarding_deadline,
            "hostileCombatReactionOccursOnNextTacticalBoundary": abs(first_hostile_combat_time - next_reaction_boundary) <= 1e-8,
            "hostilityTransitionDoesNotResetPosition": hostility_transition_position_jump < 1e-9,
            "hostilityTransitionDoesNotResetVelocity": hostility_transition_velocity_jump < 1e-9,
            "openingShotPhysicsMatchesOutcome": shot_physics_check,
            "ImpactDoesNotTeleportPosition": impact_position_ok,
            "ImpactArrivesOnPredictedTrajectory": impact_prediction_ok,
            "openingImpactTranslationIsSmall": impact_translation_small,
            "ordinaryTacticalBoundariesDoNotSnapPosition": max_boundary_position_jump < 1e-9,
            "ordinaryTacticalBoundariesDoNotSnapVelocity": max_boundary_velocity_jump < 1e-9,
            "ordinaryTacticalBoundariesArriveOnPredictedTrajectory": max_boundary_prediction_error < 1e-7,
            "viewportPredictionMatchesAuthoritativeSubSlicePhysics": max_view_physics_error < 1e-7,
            "serializedShipSpeedMatchesVelocityVector": speed_contract_ok,
            "softLockKeepsEnemyInsideHardEnvelope": float(view["hardLockRetainedFraction"]) >= 0.995,
            "softLockIsNotHardCentered": float(view["meanTargetOffsetNormalized"]) > 0.01,
            "softLockCameraHasNoFrameSnaps": int(view["cameraSnapCount"]) == 0,
            "impactDoesNotDriveViewscreenMotion": (
                view["impactTargetOffsetStepNormalized"] is None
                or float(view["impactTargetOffsetStepNormalized"]) < 0.01
            ),
            "softLockSettlesAfterHostileBreakaway": default_breakaway_stress_ok,
            "hostileBreakawayMaintainsDesignedStandOff": range_floor_ok,
            "zoomedOutViewShowsBothShipsMostOfEncounter": float(view["bothShipsVisibleFraction"]) >= 0.90,
            "encounterUsesOneContinuousPhysicalSimulation": battle.non_impact_velocity_discontinuities == 0,
            "onlyImpactMayCreateVelocityDiscontinuity": battle.velocity_discontinuities == expected_velocity_discontinuities,
        }
        failed = [name for name, passed in checks.items() if passed is not True]

        return {
            "ok": not failed,
            "schema": "game.spaceCaptainBoardingEncounterSmoke.v1",
            "emulation": {
                "player": "scripted-human-emulator",
                "hostileCaptain": "stateful-deterministic-captain-emulator",
                "note": "NanoJev is intentionally not required; this smoke isolates encounter, authoritative physics, and viewscreen contracts.",
            },
            "contract": {
                "opening": "hostile captain boards a vessel believed inactive while the viewscreen soft-tracks the hostile ship",
                "playerTrigger": "player weapon discharge makes the encounter hostile immediately without resetting physical space",
                "hostileReaction": "hostile captain publishes its first combat plan on the next fixed tactical boundary",
                "physics": "Battle 2 authoritative sub-slice integration remains continuous; ordinary weapon impact contributes only a small center-of-mass delta-v",
                "viewscreen": "60 Hz soft target lock retains the hostile ship through impact and smoothly follows the powered combat break without camera snaps",
                "rangeDesign": "the hostile captain's powered breakaway, not weapon impulse, arrests the boarding collapse and preserves stand-off range",
            },
            "checks": checks,
            "failedChecks": failed,
            "metrics": {
                "timeStepSeconds": time_step,
                "physicsStepSeconds": physics_step,
                "viewportHz": viewport_hz,
                "sliceCount": slices,
                "durationSeconds": duration,
                "boardingDeadlineSeconds": boarding_deadline,
                "playerFireAtSeconds": fire_at,
                "openingShot": args.opening_shot,
                "openingShotImpactAtSeconds": (impact_row or {}).get("simulationSeconds"),
                "openingImpactDeltaVMps": (
                    float(impact_row["velocityDiscontinuityMps"])
                    if impact_row is not None else None
                ),
                "maximumOpeningImpactDeltaVMps": MAX_OPENING_IMPACT_DELTA_V_MPS,
                "designedCombatRangeFloorM": designed_range_floor_m,
                "minimumRangeAfterHostileCombatReactionM": minimum_range_after_combat,
                "hostileCombatReactionAtSeconds": first_hostile_combat_time,
                "expectedHostileCombatReactionAtSeconds": next_reaction_boundary,
                "initialRangeM": float(args.initial_separation_m) * math.hypot(1.0, 0.23),
                "minimumRangeBeforePlayerFireM": min_range_before_fire,
                "rangeAtPlayerFireM": range_at_fire,
                "finalRangeM": _range_m(battle),
                "physicsSubstepCount": len(physics_samples),
                "viewportFrameCount": len(frames),
                "maximumViewportVsAuthoritativePhysicsErrorM": max_view_physics_error,
                "maximumTacticalBoundaryPositionDiscontinuityM": max_boundary_position_jump,
                "maximumTacticalBoundaryVelocityDiscontinuityMps": max_boundary_velocity_jump,
                "maximumTacticalBoundaryPredictionErrorM": max_boundary_prediction_error,
                "hostilityTransitionPositionDiscontinuityM": hostility_transition_position_jump,
                "hostilityTransitionVelocityDiscontinuityMps": hostility_transition_velocity_jump,
                "physicsVelocityDiscontinuityCount": battle.velocity_discontinuities,
                "nonImpactVelocityDiscontinuityCount": battle.non_impact_velocity_discontinuities,
                "hostilePreFireCombatPublicationCount": len(hostile_pre_fire_combat),
                "hostilePostFireCombatPublicationCount": len(hostile_post_fire_combat),
            },
            "viewScreen": {k: v for k, v in view.items() if k != "samples"},
            "encounterEvents": events,
            "tacticalPublications": publications,
            "trajectoryAnchors": anchors,
            "openingImpact": impact_row,
            "tacticalBoundaries": boundary_rows,
            "physicsSamples": physics_samples if args.include_samples else [],
            "viewportFrames": frames if args.include_samples else [],
            "viewScreenSamples": view["samples"] if args.include_samples else [],
        }
    finally:
        battle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke the first boarding-to-hostile bridge encounter and soft-lock viewscreen contract.")
    parser.add_argument("--time-step-seconds", type=float, default=5.0, help="Tactical slice, constrained to 1..5 seconds (default: 5).")
    parser.add_argument("--physics-step-seconds", type=float, default=0.1, help="Authoritative physics step (default: 0.1).")
    parser.add_argument("--viewport-hz", type=float, default=60.0, help="Synthetic viewscreen frame rate (default: 60).")
    parser.add_argument("--slices", type=int, default=5, help="Number of tactical slices to simulate (default: 5).")
    parser.add_argument("--player-fire-at-seconds", type=float, default=None, help="Opening hostile-fire time; default is 2.34 tactical slices.")
    parser.add_argument("--opening-shot", choices=("hit", "miss"), default="hit", help="Whether the player's opening shot hits (default) or misses.")
    parser.add_argument("--initial-separation-m", type=float, default=2600.0)
    parser.add_argument("--thrust-accel-mps2", type=float, default=25.0)
    parser.add_argument("--projectile-speed-mps", type=float, default=6000.0)
    parser.add_argument("--impact-delta-v-mps", type=float, default=0.75, help="Small center-of-mass delta-v from the opening hit (default: 0.75 m/s; smoke rejects >1 m/s).")
    parser.add_argument("--impact-damage-fraction", type=float, default=0.02)
    parser.add_argument("--miss-distance-m", type=float, default=45.0)
    parser.add_argument("--designed-combat-range-floor-m", type=float, default=900.0, help="Minimum separation the powered hostile breakaway must preserve after combat begins (default: 900 m).")
    parser.add_argument("--view-half-width-m", type=float, default=3500.0, help="Zoomed-out half-width of the tracked tactical view.")
    parser.add_argument("--view-half-height-m", type=float, default=2000.0, help="Zoomed-out half-height of the tracked tactical view.")
    parser.add_argument("--soft-lock-zone", type=float, default=0.16, help="Normalized target dead-zone half-extent before camera follow begins.")
    parser.add_argument("--hard-lock-envelope", type=float, default=0.45, help="Normalized target envelope that must retain acquisition.")
    parser.add_argument("--camera-response-seconds", type=float, default=0.35, help="Soft camera follow time constant.")
    parser.add_argument("--include-samples", action="store_true", help="Include full physics, viewport, and soft-lock sample streams.")
    args = parser.parse_args()

    if not (1.0 <= float(args.time_step_seconds) <= 5.0):
        raise SystemExit("--time-step-seconds must be between 1 and 5")
    for name in (
        "physics_step_seconds", "viewport_hz", "initial_separation_m", "thrust_accel_mps2",
        "projectile_speed_mps", "designed_combat_range_floor_m",
        "view_half_width_m", "view_half_height_m", "camera_response_seconds",
    ):
        if float(getattr(args, name)) <= 0.0:
            raise SystemExit(f"--{name.replace('_', '-')} must be positive")
    if float(args.physics_step_seconds) >= float(args.time_step_seconds):
        raise SystemExit("--physics-step-seconds must be smaller than --time-step-seconds")
    if int(args.slices) < 5:
        raise SystemExit("--slices must be at least 5 so boarding, hostile reaction, and combat all occur")
    if not (0.0 < float(args.soft_lock_zone) < float(args.hard_lock_envelope) < 1.0):
        raise SystemExit("soft lock zone and hard lock envelope must satisfy 0 < soft < hard < 1")

    try:
        result = run(args)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
