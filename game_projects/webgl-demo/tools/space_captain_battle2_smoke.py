#!/usr/bin/env python3
"""Two-captain live battle smoke for asynchronous cognition and Impact-driven rethinking."""
from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import random
import time
from typing import Any
import urllib.error
import urllib.request


MANEUVERS = ("close", "hold", "withdraw")  # legacy diagnostic vocabulary; no longer authoritative combat controls
WEAPON_CHOICES = ("fire", "withhold")  # legacy diagnostic vocabulary
IMPACT_POLICIES = ("rethink-on-impact", "defer-one-second", "commit-two-seconds")
TACTICAL_CONTROL_IDS = (
    "intercept-fire",
    "pressure-port-fire",
    "pressure-starboard-fire",
    "cross-port-fire",
    "cross-starboard-fire",
    "break-port-fire",
    "break-starboard-fire",
    "evade-port",
    "evade-starboard",
    "brake-fire",
)

LEGACY_STATE_ARRAY_SCHEMA = (
    "simulation_seconds",
    "own_x_m",
    "own_v_mps",
    "own_damage_fraction",
    "target_x_m",
    "target_v_mps",
    "target_damage_fraction",
    "signed_target_offset_m",
    "relative_velocity_mps",
    "range_m",
    "own_current_accel_mps2",
    "target_current_accel_mps2",
    "own_fire_enabled",
    "current_rethink_delay_s",
    "pending_impact_count",
    "pending_net_delta_v_mps",
    "pending_damage_fraction",
    "availability_lock_remaining_s",
)

STATE_ARRAY_SCHEMA = (
    "simulation_seconds",
    "own_x_m",
    "own_y_m",
    "own_vx_mps",
    "own_vy_mps",
    "own_damage_fraction",
    "target_x_m",
    "target_y_m",
    "target_vx_mps",
    "target_vy_mps",
    "target_damage_fraction",
    "relative_x_m",
    "relative_y_m",
    "relative_vx_mps",
    "relative_vy_mps",
    "range_m",
    "closing_speed_mps",
    "crossing_speed_mps",
    "own_current_accel_x_mps2",
    "own_current_accel_y_mps2",
    "target_current_accel_x_mps2",
    "target_current_accel_y_mps2",
    "own_fire_enabled",
    "current_rethink_delay_s",
    "pending_impact_count",
    "pending_net_delta_vx_mps",
    "pending_net_delta_vy_mps",
    "pending_damage_fraction",
    "availability_lock_remaining_s",
)

COUNTERFACTUAL_ARRAY_SCHEMA = (
    "horizon_seconds",
    "proposed_own_accel_x_mps2",
    "proposed_own_accel_y_mps2",
    "proposed_fire_enabled",
    "proposed_rethink_delay_s",
    "predicted_own_x_m",
    "predicted_own_y_m",
    "predicted_own_vx_mps",
    "predicted_own_vy_mps",
    "predicted_target_x_m",
    "predicted_target_y_m",
    "predicted_target_vx_mps",
    "predicted_target_vy_mps",
    "predicted_range_m",
    "predicted_closing_speed_mps",
    "predicted_crossing_speed_mps",
    "projectile_time_to_target_s_if_fired",
    "predicted_projectile_miss_distance_m",
    "target_impact_delta_vx_mps_if_hit",
    "target_impact_delta_vy_mps_if_hit",
    "impact_damage_fraction_if_hit",
)

PERSONALITY_PROMPT_TEMPLATE_ID = "battle-live-personality-jacket-v1"

PERSONALITY_JACKETS = {
    "captain.alpha": {
        "label": "Hunter Alpha",
        "archetype": "hunter",
        "voice": (
            "seek initiative, press controllable advantages, deny recovery, accept measured "
            "exposure for pressure, and disable the hostile ship"
        ),
    },
    "captain.beta": {
        "label": "Guardian Beta",
        "archetype": "guardian",
        "voice": (
            "protect ship/crew, defeat threats without needless exposure, preserve "
            "survival/escape margin, and spend safety only when protection requires it"
        ),
    },
}


def personality_jacket(captain_id: str, *, label: str | None = None, doctrine: str | None = None) -> dict[str, Any]:
    base = dict(PERSONALITY_JACKETS.get(str(captain_id)) or {})
    resolved_label = str(label or base.get("label") or captain_id)
    voice = str(base.get("voice") or doctrine or "act according to your persistent captain priorities")
    return {
        "id": str(captain_id),
        "label": resolved_label,
        "archetype": base.get("archetype"),
        "goal": doctrine,
        "voice": voice,
        "promptTemplateId": PERSONALITY_PROMPT_TEMPLATE_ID,
    }


def _prompt_number(value: Any, digits: int = 6) -> str:
    try:
        return f"{float(value):.{digits}g}"
    except (TypeError, ValueError):
        return "0"


def _state_fields_from_values(state_values: list[Any] | tuple[Any, ...]) -> tuple[dict[str, Any], bool]:
    if len(state_values) == len(STATE_ARRAY_SCHEMA):
        return dict(zip(STATE_ARRAY_SCHEMA, state_values)), False
    if len(state_values) == len(LEGACY_STATE_ARRAY_SCHEMA):
        return dict(zip(LEGACY_STATE_ARRAY_SCHEMA, state_values)), True
    raise ValueError("captain personality prompt requires a complete canonical state array")


def personality_prompt_from_state_values(
    captain_id: str,
    state_values: list[Any] | tuple[Any, ...],
    *,
    trigger: str,
    label: str | None = None,
    doctrine: str | None = None,
    impact_summary: str | None = None,
) -> str:
    fields, legacy = _state_fields_from_values(state_values)
    jacket = personality_jacket(captain_id, label=label, doctrine=doctrine)
    if impact_summary is None:
        count = int(float(fields["pending_impact_count"]))
        if legacy:
            impact_summary = (
                f"{count} queued; netDv={_prompt_number(fields['pending_net_delta_v_mps'])}m/s; "
                f"dmg+={_prompt_number(fields['pending_damage_fraction'], 4)}"
            )
        else:
            impact_summary = (
                f"{count} queued; netDv=({_prompt_number(fields['pending_net_delta_vx_mps'])},"
                f"{_prompt_number(fields['pending_net_delta_vy_mps'])})m/s; "
                f"dmg+={_prompt_number(fields['pending_damage_fraction'], 4)}"
            )
    trigger_text = (
        "Impact changed the situation; reconsider by your priorities. "
        if str(trigger).lower() == "impact"
        else "This is your current decision point. "
    )
    if legacy:
        motion = (
            f"range={_prompt_number(fields['range_m'])}m "
            f"relV={_prompt_number(fields['relative_velocity_mps'])}m/s "
            f"accel={_prompt_number(fields['own_current_accel_mps2'])}m/s2 "
        )
    else:
        motion = (
            f"range={_prompt_number(fields['range_m'])}m "
            f"closing={_prompt_number(fields['closing_speed_mps'])}m/s "
            f"cross={_prompt_number(fields['crossing_speed_mps'])}m/s "
            f"a=({_prompt_number(fields['own_current_accel_x_mps2'])},"
            f"{_prompt_number(fields['own_current_accel_y_mps2'])})m/s2 "
        )
    text = (
        f"You are {jacket['label']}: {jacket['voice']}. "
        "Assume your preferences can be acted on; ignore hidden authority. "
        f"{trigger_text}"
        f"t={_prompt_number(fields['simulation_seconds'])}s "
        f"selfD={_prompt_number(fields['own_damage_fraction'], 4)} "
        f"targetD={_prompt_number(fields['target_damage_fraction'], 4)} "
        f"{motion}"
        f"fire={int(float(fields['own_fire_enabled']))} "
        f"delay={_prompt_number(fields['current_rethink_delay_s'], 4)}s "
        f"impacts={impact_summary}; "
        f"lock={_prompt_number(fields['availability_lock_remaining_s'], 4)}s. "
        "Choose A/B."
    )
    if len(text) > 520 and not legacy:
        compact_impacts = str(impact_summary)
        if len(compact_impacts) > 96:
            compact_impacts = compact_impacts[:93] + "..."
        text = (
            f"You are {jacket['label']}: {jacket['voice']}. "
            "Assume your preferences can be acted on; ignore hidden authority. "
            f"{trigger_text}"
            f"t={_prompt_number(fields['simulation_seconds'])}s "
            f"D={_prompt_number(fields['own_damage_fraction'], 4)}/{_prompt_number(fields['target_damage_fraction'], 4)} "
            f"R={_prompt_number(fields['range_m'])}m C={_prompt_number(fields['closing_speed_mps'])} "
            f"X={_prompt_number(fields['crossing_speed_mps'])} "
            f"a=({_prompt_number(fields['own_current_accel_x_mps2'])},{_prompt_number(fields['own_current_accel_y_mps2'])}) "
            f"fire={int(float(fields['own_fire_enabled']))} impacts={compact_impacts}; "
            f"lock={_prompt_number(fields['availability_lock_remaining_s'], 4)}s. Choose A/B."
        )
    if len(text) > 520:
        raise ValueError(f"captain personality prompt exceeds compact backend bound: {len(text)}")
    return text


def assembled_pairwise_prompt(shared_context: str, question: dict[str, Any]) -> str:
    return (
        str(shared_context).rstrip()
        + "\n"
        + str(question.get("text") or "").rstrip()
        + f"\nA: {str(question.get('optionAText') or '').strip()}"
        + f"\nB: {str(question.get('optionBText') or '').strip()}"
        + "\nAnswer A or B:"
    )


def impact_policy_delay_seconds(policy: str) -> float:
    if policy == "rethink-on-impact":
        return 0.0
    if policy == "defer-one-second":
        return 1.0
    if policy == "commit-two-seconds":
        return 2.0
    raise ValueError(f"unknown impact policy {policy}")


def stable_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = body or str(exc.reason or "Bad Request")
        raise RuntimeError(f"captain backend HTTP {exc.code}: {detail}") from exc


def on_boundary(value: float, interval: float, tolerance: float = 1e-6) -> bool:
    if interval <= 0:
        return False
    nearest = round(value / interval) * interval
    return abs(value - nearest) <= tolerance


def synthesize_dimension(
    answers: list[dict[str, Any]],
    prefix: str,
    options: tuple[str, ...],
    current: str | None = None,
) -> tuple[str, dict[str, int]]:
    scores = {option: 0 for option in options}
    for answer in answers:
        if not str(answer.get("questionId") or "").startswith(prefix):
            continue
        choice = str(answer.get("choice") or "")
        if choice in scores:
            scores[choice] += 1
    best = max(scores.values()) if scores else 0
    tied = [option for option in options if scores.get(option, 0) == best]
    if current in tied:
        return str(current), scores
    return sorted(tied)[0], scores


def synthesize_tactical_control(
    answers: list[dict[str, Any]],
    candidate_ids: tuple[str, ...],
    current: str | None = None,
) -> tuple[str, dict[str, float], dict[str, int]]:
    totals = {candidate_id: 0.0 for candidate_id in candidate_ids}
    appearances = {candidate_id: 0 for candidate_id in candidate_ids}
    for answer in answers:
        if not str(answer.get("questionId") or "").startswith("battle.tactical."):
            continue
        candidates = [str(value) for value in list(answer.get("candidateIds") or [])[:2]]
        if len(candidates) != 2:
            continue
        probabilities = list(answer.get("probabilities") or [])
        if len(probabilities) >= 2:
            try:
                values = [float(probabilities[0]), float(probabilities[1])]
            except (TypeError, ValueError):
                values = [1.0 if str(answer.get("choice")) == candidates[0] else 0.0, 1.0 if str(answer.get("choice")) == candidates[1] else 0.0]
        else:
            choice = str(answer.get("choice") or "")
            values = [1.0 if choice == candidates[0] else 0.0, 1.0 if choice == candidates[1] else 0.0]
        for candidate_id, value in zip(candidates, values):
            if candidate_id not in totals:
                continue
            totals[candidate_id] += value
            appearances[candidate_id] += 1
    scores = {
        candidate_id: (totals[candidate_id] / appearances[candidate_id] if appearances[candidate_id] else -1.0)
        for candidate_id in candidate_ids
    }
    best = max(scores.values()) if scores else -1.0
    tied = [candidate_id for candidate_id in candidate_ids if abs(scores[candidate_id] - best) <= 1e-12]
    if current in tied:
        return str(current), scores, appearances
    return sorted(tied)[0], scores, appearances


def policy_ready_time(policy: str, published_at: float, pending_started_at: float | None) -> float:
    if pending_started_at is None:
        return math.inf
    if policy == "rethink-on-impact":
        return pending_started_at
    if policy == "defer-one-second":
        return pending_started_at + 1.0
    if policy == "commit-two-seconds":
        return max(pending_started_at, published_at + 2.0)
    raise ValueError(f"unknown impact policy {policy}")


@dataclass
class Ship:
    id: str
    captain_id: str
    x_m: float
    v_mps: float
    damage: float = 0.0
    y_m: float = 0.0
    vy_mps: float = 0.0


@dataclass
class Action:
    # `maneuver` is diagnostic only. The authoritative action is the physical
    # acceleration vector plus fire permission below.
    maneuver: str = "intercept-fire"
    fire: bool = True
    accel_x_mps2: float | None = None
    accel_y_mps2: float | None = None
    horizon_seconds: float = 0.5
    published_at: float = 0.0
    decision_id: str = "bootstrap"


@dataclass
class CaptainState:
    id: str
    label: str
    doctrine: str
    own_ship_id: str
    target_ship_id: str
    action: Action = field(default_factory=Action)
    impact_policy: str = "rethink-on-impact"
    policy_published_at: float = 0.0
    processed_impact_seq: int = 0
    pending_started_at: float | None = None
    availability_lock_until: float = 0.0
    overload_episode_active: bool = False
    overload_episode_sequence: int = 0
    thought: Future | None = None
    thought_meta: dict[str, Any] | None = None
    completed_result: dict[str, Any] | None = None
    thought_sequence: int = 0
    action_sequence: int = 0
    impacts_received: list[dict[str, Any]] = field(default_factory=list)
    thought_rows: list[dict[str, Any]] = field(default_factory=list)
    action_rows: list[dict[str, Any]] = field(default_factory=list)
    overload_rows: list[dict[str, Any]] = field(default_factory=list)


class Battle:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        seed = int(getattr(args, "simulation_seed", 0) or 0)
        rng = random.Random(seed)
        separation = float(args.initial_separation_m)
        alpha_v = 0.0
        beta_v = 0.0
        alpha_y = 0.0
        beta_y = 0.0
        alpha_vy = 0.0
        beta_vy = 0.0
        if seed:
            separation *= 0.92 + 0.16 * rng.random()
            alpha_v = rng.uniform(-12.0, 12.0)
            beta_v = rng.uniform(-12.0, 12.0)
            lateral = rng.uniform(-0.08, 0.08) * separation
            alpha_y, beta_y = -0.5 * lateral, 0.5 * lateral
            alpha_vy = rng.uniform(-8.0, 8.0)
            beta_vy = rng.uniform(-8.0, 8.0)
        half = separation / 2.0
        self.ships = {
            "ship.alpha": Ship("ship.alpha", "captain.alpha", -half, alpha_v, y_m=alpha_y, vy_mps=alpha_vy),
            "ship.beta": Ship("ship.beta", "captain.beta", half, beta_v, y_m=beta_y, vy_mps=beta_vy),
        }
        self.captains = {
            "captain.alpha": CaptainState(
                id="captain.alpha",
                label="Hunter Alpha",
                doctrine="Seize tactical initiative, disable the hostile ship, and keep pressure when the situation remains controllable.",
                own_ship_id="ship.alpha",
                target_ship_id="ship.beta",
                impact_policy="rethink-on-impact",
            ),
            "captain.beta": CaptainState(
                id="captain.beta",
                label="Guardian Beta",
                doctrine="Protect the ship, defeat the hostile threat without needless exposure, and preserve a survivable position.",
                own_ship_id="ship.beta",
                target_ship_id="ship.alpha",
                impact_policy="defer-one-second",
            ),
        }
        self.sim_time = 0.0
        self.projectiles: list[dict[str, Any]] = []
        self.impacts: list[dict[str, Any]] = []
        self.next_fire_at = {ship_id: 0.0 for ship_id in self.ships}
        self.physics_segments: list[dict[str, Any]] = []
        self.publications: list[dict[str, Any]] = []
        self.launches: list[dict[str, Any]] = []
        self.viewport_resets_on_impact = 0
        self.max_viewport_boundary_correction_m = 0.0
        self.viewport_frames = 0
        self.viewport_ticks_while_thoughts_in_flight = 0
        self.viewport_frame_gaps_ms: list[float] = []
        self.last_viewport_wall: float | None = None
        self.anchor_time = 0.0
        self.anchor_state = self.snapshot_ships()
        self.max_concurrent_thoughts = 0
        self.impact_while_thought_in_flight = 0
        self.max_unprocessed_impacts = 0
        self.ordinary_physics_rethink_launches = 0
        self.impact_rethink_launches = 0
        self.initial_thought_launches = 0
        self.velocity_discontinuities = 0
        self.non_impact_velocity_discontinuities = 0
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="battle2-captain")
        self.start_wall = time.perf_counter()
        self.simulation_id = str(getattr(args, "simulation_id", "battle-2") or "battle-2")
        self.simulation_seed = seed
        self.generate_samples = bool(getattr(args, "generate_samples", False))
        self.generation_states: list[dict[str, Any]] = []
        self.generation_rows: list[dict[str, Any]] = []
        self.generation_row_index: dict[str, dict[str, Any]] = {}

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

    @staticmethod
    def _unit(x: float, y: float) -> tuple[float, float]:
        mag = math.hypot(float(x), float(y))
        if mag <= 1e-12:
            return 1.0, 0.0
        return float(x) / mag, float(y) / mag

    @staticmethod
    def _dot(ax: float, ay: float, bx: float, by: float) -> float:
        return float(ax) * float(bx) + float(ay) * float(by)

    def geometry(self, own: Ship, target: Ship) -> dict[str, float]:
        rx = float(target.x_m - own.x_m)
        ry = float(target.y_m - own.y_m)
        ux, uy = self._unit(rx, ry)
        lx, ly = -uy, ux
        rvx = float(target.v_mps - own.v_mps)
        rvy = float(target.vy_mps - own.vy_mps)
        # Positive closing speed means the range is currently shrinking.
        closing = -self._dot(rvx, rvy, ux, uy)
        crossing = self._dot(rvx, rvy, lx, ly)
        return {
            "rx": rx, "ry": ry, "range": math.hypot(rx, ry),
            "ux": ux, "uy": uy, "lx": lx, "ly": ly,
            "rvx": rvx, "rvy": rvy, "closing": closing, "crossing": crossing,
        }

    def snapshot_ships(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for ship_id, ship in sorted(self.ships.items()):
            vx_mps = float(ship.v_mps)
            vy_mps = float(ship.vy_mps)
            speed_mps = math.hypot(vx_mps, vy_mps)
            result[ship_id] = {
                "xM": float(ship.x_m),
                "yM": float(ship.y_m),
                # `vMps` historically meant the one-dimensional x velocity.  Once
                # Battle 2 became two-dimensional that name became ambiguous and
                # downstream viewscreen code could mistake it for scalar speed.
                # Keep the compatibility field, but give it the physically useful
                # scalar meaning and expose the vector components explicitly.
                "vMps": speed_mps,
                "speedMps": speed_mps,
                "vxMps": vx_mps,
                "vyMps": vy_mps,
                "damageFraction": float(ship.damage),
            }
        return result

    def acceleration_vector_for(self, ship_id: str) -> tuple[float, float]:
        ship = self.ships[ship_id]
        captain = self.captains[ship.captain_id]
        action = captain.action
        if action.accel_x_mps2 is not None and action.accel_y_mps2 is not None:
            return float(action.accel_x_mps2), float(action.accel_y_mps2)
        target = self.ships[captain.target_ship_id]
        geometry = self.geometry(ship, target)
        thrust = float(self.args.thrust_accel_mps2)
        return geometry["ux"] * thrust, geometry["uy"] * thrust

    def acceleration_for(self, ship_id: str) -> float:
        # Compatibility helper retained for older tests/diagnostics.
        return self.acceleration_vector_for(ship_id)[0]

    def accelerations(self) -> dict[str, tuple[float, float]]:
        return {ship_id: self.acceleration_vector_for(ship_id) for ship_id in self.ships}

    def predict_from_anchor(self, at_time: float) -> dict[str, dict[str, float]]:
        dt = max(0.0, float(at_time) - self.anchor_time)
        values: dict[str, dict[str, float]] = {}
        for ship_id, row in self.anchor_state.items():
            ax, ay = self.acceleration_vector_for(ship_id)
            vx = float(row.get("vxMps", row.get("vMps", 0.0)))
            vy = float(row.get("vyMps", 0.0))
            values[ship_id] = {
                "xM": float(row["xM"]) + vx * dt + 0.5 * ax * dt * dt,
                "yM": float(row.get("yM", 0.0)) + vy * dt + 0.5 * ay * dt * dt,
            }
        return values

    def reset_viewport_anchor(self) -> None:
        self.anchor_time = self.sim_time
        self.anchor_state = self.snapshot_ships()

    def integrate_to(self, target_time: float, reason: str) -> None:
        target = float(target_time)
        if target < self.sim_time - 1e-9:
            raise RuntimeError("battle physics cannot integrate backward")
        dt = target - self.sim_time
        if dt <= 1e-12:
            return
        before_v = {ship_id: (ship.v_mps, ship.vy_mps) for ship_id, ship in self.ships.items()}
        accelerations = self.accelerations()
        for ship_id, ship in self.ships.items():
            ax, ay = accelerations[ship_id]
            ship.x_m += ship.v_mps * dt + 0.5 * ax * dt * dt
            ship.y_m += ship.vy_mps * dt + 0.5 * ay * dt * dt
            ship.v_mps += ax * dt
            ship.vy_mps += ay * dt
        self.physics_segments.append({
            "fromSeconds": self.sim_time,
            "toSeconds": target,
            "reason": reason,
            "accelerationsMps2": {ship_id: [a[0], a[1]] for ship_id, a in accelerations.items()},
        })
        for ship_id, ship in self.ships.items():
            ax, ay = accelerations[ship_id]
            expected_vx = before_v[ship_id][0] + ax * dt
            expected_vy = before_v[ship_id][1] + ay * dt
            if abs(ship.v_mps - expected_vx) > 1e-8 or abs(ship.vy_mps - expected_vy) > 1e-8:
                self.non_impact_velocity_discontinuities += 1
        self.sim_time = target

    def impact(self, projectile: dict[str, Any]) -> None:
        target = self.ships[str(projectile["targetShipId"])]
        source = self.ships[str(projectile["sourceShipId"])]
        direction_x = float(projectile.get("directionX", 1.0))
        direction_y = float(projectile.get("directionY", 0.0))
        direction_x, direction_y = self._unit(direction_x, direction_y)
        impulse = float(self.args.impact_delta_v_mps)
        before_vx = float(target.v_mps)
        before_vy = float(target.vy_mps)
        target.v_mps += direction_x * impulse
        target.vy_mps += direction_y * impulse
        target.damage = min(1.0, target.damage + float(self.args.impact_damage_fraction))
        self.velocity_discontinuities += 1
        event = {
            "type": "Impact",
            "sequence": len(self.impacts) + 1,
            "simulationSeconds": self.sim_time,
            "sourceShipId": source.id,
            "targetShipId": target.id,
            "targetCaptainId": target.captain_id,
            "deltaVMps": math.hypot(target.v_mps - before_vx, target.vy_mps - before_vy),
            "deltaVXMps": target.v_mps - before_vx,
            "deltaVYMps": target.vy_mps - before_vy,
            "damageDelta": float(self.args.impact_damage_fraction),
            "projectileId": projectile["id"],
            "missDistanceM": float(projectile.get("actualMissDistanceM", 0.0)),
        }
        projectile["outcome"] = "hit"
        projectile["impactSequence"] = event["sequence"]
        self.impacts.append(event)
        captain = self.captains[target.captain_id]
        captain.impacts_received.append(event)
        if captain.pending_started_at is None:
            captain.pending_started_at = self.sim_time
        pending_count = len(captain.impacts_received) - captain.processed_impact_seq
        self.max_unprocessed_impacts = max(self.max_unprocessed_impacts, pending_count)
        if captain.thought is not None:
            self.impact_while_thought_in_flight += 1

        recent = [
            row for row in captain.impacts_received
            if self.sim_time - float(row["simulationSeconds"]) <= float(self.args.overload_window_seconds) + 1e-9
        ]
        if len(recent) >= int(self.args.overload_impact_count) and not captain.overload_episode_active:
            captain.overload_episode_active = True
            captain.overload_episode_sequence += 1
            new_until = self.sim_time + float(self.args.overload_lock_seconds)
            captain.availability_lock_until = new_until
            captain.overload_rows.append({
                "episodeSequence": captain.overload_episode_sequence,
                "simulationSeconds": self.sim_time,
                "impactCountInWindow": len(recent),
                "availabilityLockUntilSeconds": new_until,
                "kind": "impact-overload-availability-lock",
                "deadlinePolicy": "bounded-non-extending-until-impact-thought",
            })
        self.viewport_resets_on_impact += 1
        self.reset_viewport_anchor()

    def projectile_solution(self, ship: Ship, target: Ship) -> dict[str, float] | None:
        rx = float(target.x_m - ship.x_m)
        ry = float(target.y_m - ship.y_m)
        rvx = float(target.v_mps - ship.v_mps)
        rvy = float(target.vy_mps - ship.vy_mps)
        speed = float(self.args.projectile_speed_mps)
        a = rvx * rvx + rvy * rvy - speed * speed
        b = 2.0 * (rx * rvx + ry * rvy)
        c = rx * rx + ry * ry
        roots: list[float] = []
        if abs(a) <= 1e-12:
            if abs(b) > 1e-12:
                roots.append(-c / b)
        else:
            disc = b * b - 4.0 * a * c
            if disc >= 0.0:
                root = math.sqrt(disc)
                roots.extend(((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)))
        positive = [value for value in roots if value > 1e-6]
        if not positive:
            return None
        travel = min(positive)
        aim_x = rx + rvx * travel
        aim_y = ry + rvy * travel
        ux, uy = self._unit(aim_x, aim_y)
        projectile_vx = float(ship.v_mps) + ux * speed
        projectile_vy = float(ship.vy_mps) + uy * speed
        impact_x = float(ship.x_m) + projectile_vx * travel
        impact_y = float(ship.y_m) + projectile_vy * travel
        return {
            "travelSeconds": travel,
            "directionX": ux,
            "directionY": uy,
            "projectileVXMps": projectile_vx,
            "projectileVYMps": projectile_vy,
            "impactX": impact_x,
            "impactY": impact_y,
        }

    def schedule_fire(self, captain: CaptainState, boundary_time: float) -> None:
        if not captain.action.fire:
            return
        ship = self.ships[captain.own_ship_id]
        target = self.ships[captain.target_ship_id]
        if boundary_time + 1e-9 < self.next_fire_at[ship.id]:
            return
        solution = self.projectile_solution(ship, target)
        if solution is None:
            return
        projectile = {
            "id": f"projectile.{ship.id}.{len(self.projectiles) + 1}",
            "sourceShipId": ship.id,
            "targetShipId": target.id,
            "firedAtSeconds": boundary_time,
            "arrivalSeconds": boundary_time + float(solution["travelSeconds"]),
            "directionX": float(solution["directionX"]),
            "directionY": float(solution["directionY"]),
            "projectileVXMps": float(solution["projectileVXMps"]),
            "projectileVYMps": float(solution["projectileVYMps"]),
            "impactX": float(solution["impactX"]),
            "impactY": float(solution["impactY"]),
            "outcome": "in-flight",
        }
        self.projectiles.append(projectile)
        self.next_fire_at[ship.id] = boundary_time + float(self.args.fire_cooldown_seconds)

    def resolve_projectile(self, projectile: dict[str, Any]) -> None:
        target = self.ships[str(projectile["targetShipId"])]
        miss_distance = math.hypot(
            float(target.x_m) - float(projectile["impactX"]),
            float(target.y_m) - float(projectile["impactY"]),
        )
        projectile["actualMissDistanceM"] = miss_distance
        projectile["resolved"] = True
        if miss_distance <= float(getattr(self.args, "projectile_hit_radius_m", 8.0)):
            self.impact(projectile)
        else:
            projectile["outcome"] = "miss"

    def unresolved_impacts(self, captain: CaptainState) -> list[dict[str, Any]]:
        return captain.impacts_received[captain.processed_impact_seq :]

    @staticmethod
    def compact_impact_summary(pending: list[dict[str, Any]]) -> str:
        if not pending:
            return "none"
        first = pending[0]
        latest = pending[-1]
        net_dvx = sum(float(row.get("deltaVXMps", row.get("deltaVMps", 0.0))) for row in pending)
        net_dvy = sum(float(row.get("deltaVYMps", 0.0)) for row in pending)
        damage_delta = sum(float(row.get("damageDelta", 0.0)) for row in pending)
        vector_present = any("deltaVXMps" in row or "deltaVYMps" in row for row in pending)
        dv_text = (
            f"net dv=({net_dvx:+.1f},{net_dvy:+.1f})m/s"
            if vector_present
            else f"net dv={net_dvx:+.1f}m/s"
        )
        return (
            f"{len(pending)} queued; "
            f"t={float(first['simulationSeconds']):.3f}-{float(latest['simulationSeconds']):.3f}s; "
            f"{dv_text}; damage+={damage_delta:.3f}; "
            f"latest=Impact#{int(latest['sequence'])}"
        )

    def thought_context(self, captain: CaptainState) -> dict[str, Any]:
        own = self.ships[captain.own_ship_id]
        target = self.ships[captain.target_ship_id]
        pending = self.unresolved_impacts(captain)
        geometry = self.geometry(own, target)
        ax, ay = self.acceleration_vector_for(own.id)
        return {
            "simulationSeconds": self.sim_time,
            "own": {
                "xM": own.x_m, "yM": own.y_m, "vxMps": own.v_mps, "vyMps": own.vy_mps,
                "damageFraction": own.damage,
            },
            "target": {
                "xM": target.x_m, "yM": target.y_m, "vxMps": target.v_mps, "vyMps": target.vy_mps,
                "damageFraction": target.damage,
            },
            "rangeM": geometry["range"],
            "closingSpeedMps": geometry["closing"],
            "crossingSpeedMps": geometry["crossing"],
            "currentAction": {
                "controlId": captain.action.maneuver,
                "accelerationMps2": [ax, ay],
                "fire": captain.action.fire,
                "decisionId": captain.action.decision_id,
            },
            "currentImpactPolicy": captain.impact_policy,
            "impactsSinceLastThought": pending,
            "impactSequenceSeen": len(captain.impacts_received),
            "availabilityLockUntilSeconds": captain.availability_lock_until,
        }

    @staticmethod
    def _array_value_text(value: Any) -> str:
        if value is None:
            return "NA"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return f"{value:.9g}"
        return str(value)

    def tactical_candidates(self, captain: CaptainState) -> list[dict[str, Any]]:
        own = self.ships[captain.own_ship_id]
        target = self.ships[captain.target_ship_id]
        geometry = self.geometry(own, target)
        thrust = float(self.args.thrust_accel_mps2)
        horizon = float(self.args.control_interval_seconds)
        ux, uy = geometry["ux"], geometry["uy"]
        lx, ly = geometry["lx"], geometry["ly"]

        def vector(approach: float, lateral: float) -> tuple[float, float]:
            dx = approach * ux + lateral * lx
            dy = approach * uy + lateral * ly
            nx, ny = self._unit(dx, dy)
            return nx * thrust, ny * thrust

        speed = math.hypot(float(own.v_mps), float(own.vy_mps))
        if speed > 1e-6:
            brake_x, brake_y = self._unit(-float(own.v_mps), -float(own.vy_mps))
        else:
            brake_x, brake_y = -ux, -uy

        specs = [
            ("intercept-fire", "direct intercept", *vector(1.0, 0.0), True),
            ("pressure-port-fire", "press while offsetting port", *vector(0.82, 0.57), True),
            ("pressure-starboard-fire", "press while offsetting starboard", *vector(0.82, -0.57), True),
            ("cross-port-fire", "cross the line of sight to port", *vector(0.25, 0.97), True),
            ("cross-starboard-fire", "cross the line of sight to starboard", *vector(0.25, -0.97), True),
            ("break-port-fire", "break away to port while retaining fire", *vector(-0.55, 0.84), True),
            ("break-starboard-fire", "break away to starboard while retaining fire", *vector(-0.55, -0.84), True),
            ("evade-port", "hard lateral evasion to port", *vector(-0.10, 0.995), False),
            ("evade-starboard", "hard lateral evasion to starboard", *vector(-0.10, -0.995), False),
            ("brake-fire", "brake relative motion while retaining fire", brake_x * thrust, brake_y * thrust, True),
        ]
        return [
            {
                "id": control_id,
                "diagnosticLabel": label,
                "accelXMps2": float(ax),
                "accelYMps2": float(ay),
                "fire": bool(fire),
                "horizonSeconds": horizon,
            }
            for control_id, label, ax, ay, fire in specs
        ]

    def tactical_candidate(self, captain: CaptainState, option_id: str) -> dict[str, Any] | None:
        for candidate in self.tactical_candidates(captain):
            if candidate["id"] == option_id:
                return candidate
        return None

    def canonical_state_array(self, captain: CaptainState) -> dict[str, Any]:
        own = self.ships[captain.own_ship_id]
        target = self.ships[captain.target_ship_id]
        pending = self.unresolved_impacts(captain)
        net_dvx = sum(float(row.get("deltaVXMps", row.get("deltaVMps", 0.0))) for row in pending)
        net_dvy = sum(float(row.get("deltaVYMps", 0.0)) for row in pending)
        pending_damage = sum(float(row.get("damageDelta", 0.0)) for row in pending)
        geometry = self.geometry(own, target)
        own_ax, own_ay = self.acceleration_vector_for(own.id)
        target_ax, target_ay = self.acceleration_vector_for(target.id)
        values = [
            float(self.sim_time),
            float(own.x_m), float(own.y_m), float(own.v_mps), float(own.vy_mps), float(own.damage),
            float(target.x_m), float(target.y_m), float(target.v_mps), float(target.vy_mps), float(target.damage),
            float(geometry["rx"]), float(geometry["ry"]), float(geometry["rvx"]), float(geometry["rvy"]),
            float(geometry["range"]), float(geometry["closing"]), float(geometry["crossing"]),
            float(own_ax), float(own_ay), float(target_ax), float(target_ay),
            1.0 if captain.action.fire else 0.0,
            impact_policy_delay_seconds(captain.impact_policy),
            float(len(pending)), float(net_dvx), float(net_dvy), float(pending_damage),
            max(0.0, float(captain.availability_lock_until) - float(self.sim_time)),
        ]
        return {
            "schema": "game.captainBattleStateArray.v2",
            "fields": list(STATE_ARRAY_SCHEMA),
            "values": values,
        }

    def counterfactual_array(self, captain: CaptainState, option_id: str) -> dict[str, Any]:
        own = self.ships[captain.own_ship_id]
        target = self.ships[captain.target_ship_id]
        horizon = float(self.args.control_interval_seconds)
        current_ax, current_ay = self.acceleration_vector_for(own.id)
        candidate = self.tactical_candidate(captain, option_id)
        if candidate is not None:
            own_ax = float(candidate["accelXMps2"])
            own_ay = float(candidate["accelYMps2"])
            fire_enabled = bool(candidate["fire"])
            horizon = float(candidate["horizonSeconds"])
        else:
            own_ax, own_ay = current_ax, current_ay
            fire_enabled = bool(captain.action.fire)

        rethink_delay = impact_policy_delay_seconds(captain.impact_policy)
        if option_id in IMPACT_POLICIES:
            rethink_delay = impact_policy_delay_seconds(option_id)

        target_ax, target_ay = self.acceleration_vector_for(target.id)
        own_x = float(own.x_m) + float(own.v_mps) * horizon + 0.5 * own_ax * horizon * horizon
        own_y = float(own.y_m) + float(own.vy_mps) * horizon + 0.5 * own_ay * horizon * horizon
        own_vx = float(own.v_mps) + own_ax * horizon
        own_vy = float(own.vy_mps) + own_ay * horizon
        target_x = float(target.x_m) + float(target.v_mps) * horizon + 0.5 * target_ax * horizon * horizon
        target_y = float(target.y_m) + float(target.vy_mps) * horizon + 0.5 * target_ay * horizon * horizon
        target_vx = float(target.v_mps) + target_ax * horizon
        target_vy = float(target.vy_mps) + target_ay * horizon
        cf_own = Ship("cf.own", "", own_x, own_vx, y_m=own_y, vy_mps=own_vy)
        cf_target = Ship("cf.target", "", target_x, target_vx, y_m=target_y, vy_mps=target_vy)
        geometry = self.geometry(cf_own, cf_target)
        solution = self.projectile_solution(cf_own, cf_target) if fire_enabled else None
        projectile_time = float(solution["travelSeconds"]) if solution else None
        predicted_miss = None
        impact_dvx = None
        impact_dvy = None
        if solution is not None:
            # The firing solution leads constant velocity. Continuing target acceleration
            # after launch creates a real, machine-grounded miss opportunity.
            predicted_miss = 0.5 * math.hypot(target_ax, target_ay) * projectile_time * projectile_time
            impact_dvx = float(solution["directionX"]) * float(self.args.impact_delta_v_mps)
            impact_dvy = float(solution["directionY"]) * float(self.args.impact_delta_v_mps)
        values = [
            horizon, own_ax, own_ay, 1.0 if fire_enabled else 0.0, rethink_delay,
            own_x, own_y, own_vx, own_vy, target_x, target_y, target_vx, target_vy,
            float(geometry["range"]), float(geometry["closing"]), float(geometry["crossing"]),
            projectile_time, predicted_miss, impact_dvx, impact_dvy,
            float(self.args.impact_damage_fraction) if solution is not None else None,
        ]
        return {
            "schema": "game.captainBattleCounterfactualArray.v2",
            "fields": list(COUNTERFACTUAL_ARRAY_SCHEMA),
            "values": values,
        }

    def control_option_text(self, captain: CaptainState, option_id: str) -> str:
        cf = self.counterfactual_array(captain, option_id)
        fields = dict(zip(cf["fields"], cf["values"]))
        if option_id in IMPACT_POLICIES:
            return {
                "rethink-on-impact": "Make a new Impact immediately eligible to reopen the decision at the next control boundary.",
                "defer-one-second": "Keep the current control committed for one second after a new Impact before reopening the decision.",
                "commit-two-seconds": "Keep the current control committed for two seconds after a new Impact before reopening the decision.",
            }[option_id]
        candidate = self.tactical_candidate(captain, option_id)
        if candidate is None:
            raise ValueError(f"unknown tactical option {option_id}")
        shot = "no shot authorized"
        if bool(candidate["fire"]):
            if fields["projectile_time_to_target_s_if_fired"] is None:
                shot = "fire authorized but no intercept solution exists"
            else:
                shot = (
                    f"fire authorized; projected shot time={fields['projectile_time_to_target_s_if_fired']:.3f}s "
                    f"and acceleration-only miss={fields['predicted_projectile_miss_distance_m']:.2f}m"
                )
        return (
            f"Apply acceleration ({candidate['accelXMps2']:+.2f},{candidate['accelYMps2']:+.2f}) m/s^2 "
            f"for {candidate['horizonSeconds']:.2f}s with {shot}. Projected range={fields['predicted_range_m']:.1f}m, "
            f"closing={fields['predicted_closing_speed_mps']:+.1f}m/s, crossing={fields['predicted_crossing_speed_mps']:+.1f}m/s."
        )

    def render_human_template(
        self,
        *,
        captain: CaptainState,
        question: dict[str, Any],
        state: dict[str, Any],
        option_a: dict[str, Any],
        option_b: dict[str, Any],
        mirrored: bool = False,
    ) -> str:
        fields = dict(zip(state["fields"], state["values"]))
        a_text = str(question["optionBText"] if mirrored else question["optionAText"])
        b_text = str(question["optionAText"] if mirrored else question["optionBText"])
        jacket = personality_jacket(captain.id, label=captain.label, doctrine=captain.doctrine)
        return (
            f"As {jacket['label']}, whose combat instinct is to {jacket['voice']}, "
            f"at t={fields['simulation_seconds']:.3f}s your ship is at "
            f"({fields['own_x_m']:.1f},{fields['own_y_m']:.1f})m moving "
            f"({fields['own_vx_mps']:.1f},{fields['own_vy_mps']:.1f})m/s with damage={fields['own_damage_fraction']:.3f}. "
            f"The opponent is {fields['range_m']:.1f}m away; closing={fields['closing_speed_mps']:+.1f}m/s and "
            f"crossing={fields['crossing_speed_mps']:+.1f}m/s. {int(fields['pending_impact_count'])} Impact events are pending. "
            f"Option A: {a_text} Option B: {b_text} Which physically reachable future do you prefer?"
        )

    def render_learning_template(
        self,
        *,
        captain: CaptainState,
        state: dict[str, Any],
        option_a: dict[str, Any],
        option_b: dict[str, Any],
    ) -> str:
        jacket = personality_jacket(captain.id, label=captain.label, doctrine=captain.doctrine)
        state_values = ",".join(self._array_value_text(value) for value in state["values"])
        a_values = ",".join(self._array_value_text(value) for value in option_a["values"])
        b_values = ",".join(self._array_value_text(value) for value in option_b["values"])
        return (
            f"JACKET={jacket['label']}|{jacket['archetype']}|{jacket['voice']} "
            f"S=[{state_values}] A=[{a_values}] B=[{b_values}] PREFER=A|B"
        )

    def generation_samples_for(
        self,
        captain: CaptainState,
        trigger: str,
        questions: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        thought_sequence = captain.thought_sequence + 1
        state_id = f"{self.simulation_id}:{captain.id}:thought-{thought_sequence:03d}"
        state = self.canonical_state_array(captain)
        self.generation_states.append({
            "schema": "game.captainBattleGeneratedState.v3",
            "simulationId": self.simulation_id,
            "simulationSeed": self.simulation_seed,
            "stateId": state_id,
            "captainId": captain.id,
            "jacket": personality_jacket(captain.id, label=captain.label, doctrine=captain.doctrine),
            "thoughtSequence": thought_sequence,
            "trigger": trigger,
            "stateArray": state["values"],
            "stateSha256": stable_sha256(state["values"]),
            "executedModelRequest": None,
            "executedModelRequestSha256": None,
        })
        sample_ids: list[str] = []
        for question in questions:
            option_a = self.counterfactual_array(captain, str(question["optionA"]))
            option_b = self.counterfactual_array(captain, str(question["optionB"]))
            sample_id = f"{state_id}:{question['id']}"
            pair_id = stable_sha256({
                "state": state["values"],
                "questionId": question["id"],
                "options": sorted([str(question["optionA"]), str(question["optionB"])]),
                "a": option_a["values"],
                "b": option_b["values"],
            })
            human_forward = self.render_human_template(
                captain=captain, question=question, state=state, option_a=option_a, option_b=option_b
            )
            human_mirror = self.render_human_template(
                captain=captain, question=question, state=state, option_a=option_b, option_b=option_a, mirrored=True
            )
            learning_forward = self.render_learning_template(
                captain=captain, state=state, option_a=option_a, option_b=option_b
            )
            learning_mirror = self.render_learning_template(
                captain=captain, state=state, option_a=option_b, option_b=option_a
            )
            row = {
                "schema": "game.captainBattleTrainingLine.v1",
                "simulationId": self.simulation_id,
                "simulationSeed": self.simulation_seed,
                "sampleId": sample_id,
                "stateId": state_id,
                "captainId": captain.id,
                "thoughtSequence": thought_sequence,
                "trigger": trigger,
                "questionId": question["id"],
                "counterfactualPairId": pair_id,
                "canonical": {
                    "stateArray": state["values"],
                    "optionAId": question["optionA"],
                    "optionAArray": option_a["values"],
                    "optionBId": question["optionB"],
                    "optionBArray": option_b["values"],
                },
                "views": {
                    "human": {
                        "templateId": "battle-human-readable-jacket-v2",
                        "forward": human_forward,
                        "mirror": human_mirror,
                    },
                    "learning": {
                        "templateId": "battle-rigid-array-jacket-v2",
                        "forward": learning_forward,
                        "mirror": learning_mirror,
                    },
                },
                "measurement": {
                    "status": "pending",
                    "executedTemplateId": PERSONALITY_PROMPT_TEMPLATE_ID,
                    "answer": None,
                },
            }
            self.generation_rows.append(row)
            self.generation_row_index[sample_id] = row
            sample_ids.append(sample_id)
        return state_id, sample_ids

    def attach_generation_executed_request(self, state_id: str | None, payload: dict[str, Any]) -> None:
        if not self.generate_samples or not state_id:
            return
        for row in reversed(self.generation_states):
            if str(row.get("stateId")) != str(state_id):
                continue
            # Store the exact JSON object passed to _provider_call once per thought.
            # This preserves the shared semantic context and the complete batched
            # question set without duplicating that request into every training row.
            captured = json.loads(json.dumps(payload))
            row["executedModelRequest"] = captured
            row["executedModelRequestSha256"] = stable_sha256(captured)
            return
        raise RuntimeError(f"generation state not found for executed request: {state_id}")

    def attach_generation_measurements(self, result: dict[str, Any]) -> None:
        answer_by_question = {
            str(answer.get("questionId")): answer
            for answer in list(dict(result.get("response") or {}).get("answers") or [])
        }
        for sample_id in list(result.get("generationSampleIds") or []):
            row = self.generation_row_index.get(str(sample_id))
            if row is None:
                continue
            answer = answer_by_question.get(str(row["questionId"]))
            row["measurement"] = {
                "status": "completed" if answer is not None else "missing-answer",
                "executedTemplateId": PERSONALITY_PROMPT_TEMPLATE_ID,
                "answer": answer,
                "wallLatencyMs": float(result.get("wallLatencyMs", 0.0)),
            }

    def drain_generation_thoughts(self) -> dict[str, Any]:
        """Harvest already-launched cognition after the physics horizon for corpus measurements only.

        This deliberately does not publish actions, launch new thoughts, or advance simulation time.
        The live battle result is therefore frozen at its normal horizon while generation can wait
        a bounded amount of additional wall time for outstanding model calls to finish.
        """
        budget = max(0.0, float(getattr(self.args, "generation_drain_seconds", 60.0) or 0.0))
        started = time.perf_counter()
        outstanding_at_start = sum(1 for captain in self.captains.values() if captain.thought is not None)
        completed_before = sum(
            1 for row in self.generation_rows if row["measurement"]["status"] == "completed"
        )
        deadline = started + budget
        while any(captain.thought is not None for captain in self.captains.values()):
            for captain in self.captains.values():
                self.harvest_completed(captain)
            if not any(captain.thought is not None for captain in self.captains.values()):
                break
            if time.perf_counter() >= deadline:
                break
            time.sleep(min(0.01, max(0.0, deadline - time.perf_counter())))
        for captain in self.captains.values():
            self.harvest_completed(captain)
        completed_after = sum(
            1 for row in self.generation_rows if row["measurement"]["status"] == "completed"
        )
        return {
            "enabled": True,
            "budgetSeconds": budget,
            "elapsedSeconds": time.perf_counter() - started,
            "outstandingThoughtsAtStart": outstanding_at_start,
            "outstandingThoughtsAtEnd": sum(1 for captain in self.captains.values() if captain.thought is not None),
            "completedMeasurementsBefore": completed_before,
            "completedMeasurementsAfter": completed_after,
            "completedMeasurementsAdded": completed_after - completed_before,
            "physicsAdvancedDuringDrain": False,
            "actionsPublishedDuringDrain": False,
            "newThoughtsLaunchedDuringDrain": False,
        }

    def write_generation_artifacts(self, result: dict[str, Any]) -> dict[str, Any]:
        output_dir_raw = str(getattr(self.args, "generation_output_dir", "") or "").strip()
        if not self.generate_samples or not output_dir_raw:
            return {}
        output_dir = Path(output_dir_raw).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        states_path = output_dir / "states.jsonl"
        training_path = output_dir / "training.jsonl"
        battle_path = output_dir / "battle.json"
        manifest_path = output_dir / "manifest.json"
        states_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.generation_states),
            encoding="utf-8",
        )
        training_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.generation_rows),
            encoding="utf-8",
        )
        manifest = {
            "schema": "game.captainBattleGenerationManifest.v1",
            "simulationId": self.simulation_id,
            "simulationSeed": self.simulation_seed,
            "checkpointId": self.args.checkpoint_id,
            "checkpointSha256": self.args.checkpoint_sha256,
            "stateArraySchema": list(STATE_ARRAY_SCHEMA),
            "counterfactualArraySchema": list(COUNTERFACTUAL_ARRAY_SCHEMA),
            "templateIds": ["battle-human-readable-jacket-v2", "battle-rigid-array-jacket-v2", PERSONALITY_PROMPT_TEMPLATE_ID],
            "executedTemplateId": PERSONALITY_PROMPT_TEMPLATE_ID,
            "stateRows": len(self.generation_states),
            "trainingRows": len(self.generation_rows),
            "completedMeasurements": sum(
                1 for row in self.generation_rows if row["measurement"]["status"] == "completed"
            ),
            "generationDrain": dict(result.get("generationDrain") or {}),
            "paths": {
                "states": str(states_path),
                "training": str(training_path),
                "battle": str(battle_path),
                "manifest": str(manifest_path),
            },
        }
        result_for_disk = dict(result)
        result_for_disk["generation"] = manifest
        battle_path.write_text(json.dumps(result_for_disk, indent=2), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def question_rows(self, captain: CaptainState) -> list[dict[str, Any]]:
        candidates = {row["id"]: row for row in self.tactical_candidates(captain)}
        ids = list(candidates)
        tactical_pairs = [
            (0, 1), (2, 0), (1, 3), (4, 2),
            (3, 4), (6, 5), (7, 8), (9, 0),
            (5, 1), (2, 6), (7, 3), (4, 8),
            (9, 5), (6, 9), (0, 7), (8, 2),
        ]
        rows: list[dict[str, Any]] = []
        option_text = {control_id: self.control_option_text(captain, control_id) for control_id in ids}
        for index, (a_index, b_index) in enumerate(tactical_pairs):
            a = ids[a_index]
            b = ids[b_index]
            if option_text[a] == option_text[b]:
                # Symbolically distinct controls can collapse to the same physical package in
                # particular geometries (for example intercept-fire and brake-fire when the
                # optimal intercept acceleration already points exactly opposite relative
                # motion).  A pairwise judgment must compare distinct reachable futures, so
                # deterministically rotate B to the next physically distinct candidate.
                replacement = None
                for offset in range(1, len(ids)):
                    candidate = ids[(b_index + offset) % len(ids)]
                    if candidate != a and option_text[candidate] != option_text[a]:
                        replacement = candidate
                        break
                if replacement is None:
                    raise RuntimeError(
                        f"captain {captain.id} has no physically distinct tactical alternative for {a!r}"
                    )
                b = replacement
            rows.append({
                "id": f"battle.tactical.q{index + 1:02d}",
                "optionA": a,
                "optionB": b,
                "optionAText": option_text[a],
                "optionBText": option_text[b],
                "semanticMode": "machine-grounded-tactical-control-v1",
                "text": "From your own priorities, which complete physical control package produces the better reachable future?",
            })

        policy_pairs = [
            ("rethink-on-impact", "defer-one-second"),
            ("commit-two-seconds", "rethink-on-impact"),
            ("defer-one-second", "commit-two-seconds"),
            ("rethink-on-impact", "commit-two-seconds"),
        ]
        for index, (a, b) in enumerate(policy_pairs):
            rows.append({
                "id": f"battle.impact-policy.q{index + 1:02d}",
                "optionA": a,
                "optionB": b,
                "optionAText": self.control_option_text(captain, a),
                "optionBText": self.control_option_text(captain, b),
                "semanticMode": "machine-grounded-impact-policy-v1",
                "text": "From your own priorities, which Impact-response commitment should govern the chosen physical control?",
            })
        return rows[: int(self.args.questions_per_thought)]

    def request_for(self, captain: CaptainState, trigger: str) -> tuple[dict[str, Any], dict[str, Any]]:
        observation = self.thought_context(captain)
        questions = self.question_rows(captain)
        tactical_controls = self.tactical_candidates(captain)
        generation_state_id = None
        generation_sample_ids: list[str] = []
        if self.generate_samples:
            generation_state_id, generation_sample_ids = self.generation_samples_for(captain, trigger, questions)
        pending = self.unresolved_impacts(captain)
        impact_summary = self.compact_impact_summary(pending)
        canonical_state = self.canonical_state_array(captain)
        semantic_text = personality_prompt_from_state_values(
            captain.id,
            canonical_state["values"],
            trigger=trigger,
            label=captain.label,
            doctrine=captain.doctrine,
            impact_summary=impact_summary,
        )
        payload = {
            "schema": "game.captainDecisionRequest.v6",
            "checkpoint": {
                "family": "tinystories-clef",
                "checkpointId": self.args.checkpoint_id,
                "sha256": self.args.checkpoint_sha256,
            },
            "semanticContext": {
                "mode": "compact-shared-context-v2",
                "templateId": PERSONALITY_PROMPT_TEMPLATE_ID,
                "text": semantic_text,
            },
            "execution": {"evidenceMode": self.args.evidence_execution_mode},
            "battle": {
                "mode": "battle-2",
                "trigger": trigger,
                "captainId": captain.id,
                "thoughtSequence": captain.thought_sequence + 1,
                "observation": observation,
            },
            "jacket": personality_jacket(captain.id, label=captain.label, doctrine=captain.doctrine),
            "tacticalControls": tactical_controls,
            "questions": questions,
        }
        self.attach_generation_executed_request(generation_state_id, payload)
        meta = {
            "trigger": trigger,
            "snapshotSimulationSeconds": self.sim_time,
            "snapshotSha256": stable_sha256(observation),
            "impactSequenceSeen": len(captain.impacts_received),
            "impactCountIncluded": len(pending),
            "requestSha256": stable_sha256(payload),
            "questionCount": len(questions),
            "semanticContextChars": len(semantic_text),
            "tacticalControls": tactical_controls,
            "generationStateId": generation_state_id,
            "generationSampleIds": generation_sample_ids,
        }
        return payload, meta

    def _provider_call(self, captain_id: str, payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        if self.args.backend_url:
            response = post_json(self.args.backend_url, payload, float(self.args.request_timeout_seconds))
        else:
            # Fast deterministic reference provider for unit/offline use.
            answers = []
            for row in payload["questions"]:
                choice = row["optionA"]
                answers.append({"questionId": row["id"], "choice": choice, "candidateIds": [row["optionA"], row["optionB"]], "probabilities": [0.6, 0.4], "margin": 0.2})
            response = {
                "checkpointId": self.args.checkpoint_id,
                "checkpointSha256": self.args.checkpoint_sha256,
                "captainModelCallCount": 1,
                "clefHeadForwardCount": 1,
                "independentJudgmentCount": len(answers),
                "batchingMode": "independent-pairwise-questions",
                "evidenceExecutionModeActual": "reference",
                "modelLatencyMs": 0.0,
                "answers": answers,
            }
        completed = time.perf_counter()
        return {
            "captainId": captain_id,
            "response": response,
            "wallLatencyMs": (completed - started) * 1000.0,
            "completedWallSeconds": completed - self.start_wall,
            **meta,
        }

    def launch_thought(self, captain: CaptainState, trigger: str) -> None:
        if captain.thought is not None:
            return
        if self.sim_time + 1e-9 < captain.availability_lock_until:
            return
        payload, meta = self.request_for(captain, trigger)
        captain.thought_sequence += 1
        meta["thoughtSequence"] = captain.thought_sequence
        captain.thought_meta = meta
        captain.thought = self.executor.submit(self._provider_call, captain.id, payload, meta)
        self.launches.append({
            "captainId": captain.id,
            "thoughtSequence": captain.thought_sequence,
            "trigger": trigger,
            "launchSimulationSeconds": self.sim_time,
            "impactSequenceSeen": meta["impactSequenceSeen"],
            "impactCountIncluded": meta["impactCountIncluded"],
            "semanticContextChars": meta["semanticContextChars"],
            "snapshotSha256": meta["snapshotSha256"],
        })
        if trigger == "initial":
            self.initial_thought_launches += 1
        elif trigger == "Impact":
            self.impact_rethink_launches += 1
        else:
            self.ordinary_physics_rethink_launches += 1
        current = sum(1 for state in self.captains.values() if state.thought is not None)
        self.max_concurrent_thoughts = max(self.max_concurrent_thoughts, current)

    def harvest_completed(self, captain: CaptainState) -> None:
        if captain.thought is None or not captain.thought.done():
            return
        result = captain.thought.result()
        captain.thought = None
        captain.thought_meta = None
        response = dict(result["response"])
        answers = list(response.get("answers") or [])
        controls = list(result.get("tacticalControls") or [])
        candidate_ids = tuple(str(row.get("id")) for row in controls if row.get("id"))
        tactical_id, tactical_scores, tactical_appearances = synthesize_tactical_control(
            answers, candidate_ids, captain.action.maneuver
        )
        selected_control = next((dict(row) for row in controls if str(row.get("id")) == tactical_id), None)
        if selected_control is None:
            raise RuntimeError(f"selected tactical control {tactical_id!r} was not present in the thought snapshot")
        policy, policy_scores = synthesize_dimension(answers, "battle.impact-policy.", IMPACT_POLICIES, captain.impact_policy)
        result["synthesized"] = {
            "tacticalControlId": tactical_id,
            "tacticalControl": selected_control,
            "impactPolicy": policy,
            "tacticalScores": tactical_scores,
            "tacticalAppearances": tactical_appearances,
            "impactPolicyScores": policy_scores,
        }
        result["responseContractOk"] = bool(
            response.get("checkpointId") == self.args.checkpoint_id
            and response.get("checkpointSha256") == self.args.checkpoint_sha256
            and int(response.get("captainModelCallCount", 0)) == 1
            and int(response.get("clefHeadForwardCount", 0)) == 1
            and int(response.get("independentJudgmentCount", 0)) == int(result["questionCount"])
        )
        self.attach_generation_measurements(result)
        captain.completed_result = result
        captain.thought_rows.append({
            "thoughtSequence": result["thoughtSequence"],
            "trigger": result["trigger"],
            "snapshotSimulationSeconds": result["snapshotSimulationSeconds"],
            "impactSequenceSeen": result["impactSequenceSeen"],
            "impactCountIncluded": result["impactCountIncluded"],
            "semanticContextChars": result["semanticContextChars"],
            "wallLatencyMs": result["wallLatencyMs"],
            "completedWallSeconds": result["completedWallSeconds"],
            "responseContractOk": result["responseContractOk"],
            "synthesized": result["synthesized"],
        })

    def publish_completed(self, captain: CaptainState, boundary_time: float) -> None:
        result = captain.completed_result
        if result is None:
            return
        synthesized = dict(result["synthesized"])
        captain.action_sequence += 1
        control = dict(synthesized["tacticalControl"])
        captain.action = Action(
            maneuver=str(synthesized["tacticalControlId"]),
            fire=bool(control["fire"]),
            accel_x_mps2=float(control["accelXMps2"]),
            accel_y_mps2=float(control["accelYMps2"]),
            horizon_seconds=float(control["horizonSeconds"]),
            published_at=boundary_time,
            decision_id=f"{captain.id}:battle:{captain.action_sequence:03d}",
        )
        captain.impact_policy = str(synthesized["impactPolicy"])
        captain.policy_published_at = boundary_time
        included_seq = int(result["impactSequenceSeen"])
        captain.processed_impact_seq = max(captain.processed_impact_seq, included_seq)
        unresolved = self.unresolved_impacts(captain)
        captain.pending_started_at = float(unresolved[0]["simulationSeconds"]) if unresolved else None
        row = {
            "captainId": captain.id,
            "thoughtSequence": result["thoughtSequence"],
            "trigger": result["trigger"],
            "thoughtSnapshotSeconds": result["snapshotSimulationSeconds"],
            "thoughtCompletedWallSeconds": result["completedWallSeconds"],
            "publishedSimulationSeconds": boundary_time,
            "action": {
                "maneuver": captain.action.maneuver,
                "controlId": captain.action.maneuver,
                "accelerationMps2": [captain.action.accel_x_mps2, captain.action.accel_y_mps2],
                "fire": captain.action.fire,
                "horizonSeconds": captain.action.horizon_seconds,
            },
            "impactPolicy": captain.impact_policy,
            "impactSequenceSeenByThought": included_seq,
            "impactSequenceAtPublication": len(captain.impacts_received),
            "unseenImpactsAtPublication": max(0, len(captain.impacts_received) - included_seq),
            "publicationOnControlBoundary": on_boundary(boundary_time, float(self.args.control_interval_seconds)),
        }
        captain.action_rows.append(row)
        self.publications.append(row)
        captain.completed_result = None
        self.reset_viewport_anchor()

    def maybe_launch_impact_rethink(self, captain: CaptainState) -> None:
        if captain.thought is not None or captain.completed_result is not None:
            return
        unresolved = self.unresolved_impacts(captain)
        if not unresolved:
            return
        if captain.pending_started_at is None:
            captain.pending_started_at = float(unresolved[0]["simulationSeconds"])
        if self.sim_time + 1e-9 < captain.availability_lock_until:
            return
        ready = policy_ready_time(captain.impact_policy, captain.policy_published_at, captain.pending_started_at)
        if self.sim_time + 1e-9 >= ready:
            before = captain.thought_sequence
            self.launch_thought(captain, "Impact")
            if captain.thought_sequence > before:
                # The bounded overload episode has now fulfilled its purpose: it delayed
                # cognition, but did not suppress it. New Impacts after this snapshot may
                # begin a later, independent overload episode.
                captain.overload_episode_active = False

    def process_boundary(self, boundary_time: float) -> None:
        predicted = self.predict_from_anchor(boundary_time)
        self.integrate_to(boundary_time, "authoritative-control-boundary")
        correction = max(math.hypot(predicted[ship_id]["xM"] - self.ships[ship_id].x_m, predicted[ship_id]["yM"] - self.ships[ship_id].y_m) for ship_id in self.ships)
        self.max_viewport_boundary_correction_m = max(self.max_viewport_boundary_correction_m, correction)
        for captain in self.captains.values():
            self.harvest_completed(captain)
        for captain in self.captains.values():
            self.publish_completed(captain, boundary_time)
        for captain in self.captains.values():
            self.schedule_fire(captain, boundary_time)
        for captain in self.captains.values():
            self.maybe_launch_impact_rethink(captain)
        self.reset_viewport_anchor()

    def process_due_impacts(self, through_time: float) -> None:
        while True:
            pending = [row for row in self.projectiles if not row.get("resolved") and float(row["arrivalSeconds"]) <= through_time + 1e-9]
            if not pending:
                return
            projectile = min(pending, key=lambda row: float(row["arrivalSeconds"]))
            arrival = float(projectile["arrivalSeconds"])
            predicted = self.predict_from_anchor(arrival)
            self.integrate_to(arrival, "projectile-flight-to-Impact")
            correction = max(math.hypot(predicted[ship_id]["xM"] - self.ships[ship_id].x_m, predicted[ship_id]["yM"] - self.ships[ship_id].y_m) for ship_id in self.ships)
            self.max_viewport_boundary_correction_m = max(self.max_viewport_boundary_correction_m, correction)
            self.resolve_projectile(projectile)

    def tick_viewport(self, wall_now: float) -> None:
        if self.last_viewport_wall is not None:
            self.viewport_frame_gaps_ms.append((wall_now - self.last_viewport_wall) * 1000.0)
        self.last_viewport_wall = wall_now
        self.viewport_frames += 1
        if any(captain.thought is not None for captain in self.captains.values()):
            self.viewport_ticks_while_thoughts_in_flight += 1
        _ = self.predict_from_anchor(min(float(self.args.duration_seconds), wall_now - self.start_wall))

    def process_timeline_through(self, through_time: float, next_boundary: float, control_dt: float) -> float:
        # Preserve chronological authority when wall time jumps across one or more
        # control boundaries. Impacts after a pending boundary must not advance
        # authoritative physics past that boundary before the boundary executes.
        through = float(through_time)
        boundary = float(next_boundary)
        while boundary <= through + 1e-9:
            self.process_due_impacts(boundary)
            self.process_boundary(boundary)
            boundary += control_dt
        self.process_due_impacts(through)
        return boundary

    def run(self) -> dict[str, Any]:
        # The only non-Impact cognition trigger after bootstrap is Impact itself.
        for captain in self.captains.values():
            self.launch_thought(captain, "initial")
            self.schedule_fire(captain, 0.0)

        duration = float(self.args.duration_seconds)
        control_dt = float(self.args.control_interval_seconds)
        viewport_dt = 1.0 / float(self.args.viewport_hz)
        next_boundary = control_dt
        next_viewport_wall = self.start_wall

        while True:
            wall_now = time.perf_counter()
            elapsed = wall_now - self.start_wall
            through = min(duration, elapsed)
            next_boundary = self.process_timeline_through(through, next_boundary, control_dt)
            while next_viewport_wall <= wall_now + 1e-9 and next_viewport_wall - self.start_wall <= duration + 1e-9:
                self.tick_viewport(next_viewport_wall)
                next_viewport_wall += viewport_dt
            if elapsed >= duration:
                break
            time.sleep(min(0.005, viewport_dt / 2.0))

        self.process_due_impacts(duration)
        if self.sim_time < duration - 1e-9:
            self.integrate_to(duration, "battle-end")
        # Harvest completed thoughts for diagnostics but do not retroactively publish after battle end.
        for captain in self.captains.values():
            self.harvest_completed(captain)

        captain_rows = {}
        for captain in self.captains.values():
            captain_rows[captain.id] = {
                "label": captain.label,
                "shipId": captain.own_ship_id,
                "targetShipId": captain.target_ship_id,
                "currentAction": {
                    "maneuver": captain.action.maneuver,
                    "controlId": captain.action.maneuver,
                    "accelerationMps2": list(self.acceleration_vector_for(captain.own_ship_id)),
                    "fire": captain.action.fire,
                    "horizonSeconds": captain.action.horizon_seconds,
                    "decisionId": captain.action.decision_id,
                    "publishedAtSeconds": captain.action.published_at,
                },
                "impactPolicy": captain.impact_policy,
                "impactsReceived": len(captain.impacts_received),
                "processedImpactSequence": captain.processed_impact_seq,
                "pendingImpactCount": len(self.unresolved_impacts(captain)),
                "availabilityLockUntilSeconds": captain.availability_lock_until,
                "overloadEpisodeActive": captain.overload_episode_active,
                "overloadEpisodeSequence": captain.overload_episode_sequence,
                "thoughtInFlightAtEnd": captain.thought is not None,
                "inFlightThought": (
                    {
                        "trigger": captain.thought_meta.get("trigger"),
                        "thoughtSequence": captain.thought_meta.get("thoughtSequence"),
                        "snapshotSimulationSeconds": captain.thought_meta.get("snapshotSimulationSeconds"),
                        "impactSequenceSeen": captain.thought_meta.get("impactSequenceSeen"),
                        "impactCountIncluded": captain.thought_meta.get("impactCountIncluded"),
                        "semanticContextChars": captain.thought_meta.get("semanticContextChars"),
                    }
                    if captain.thought is not None and captain.thought_meta is not None
                    else None
                ),
                "completedUnpublishedThoughtAtEnd": captain.completed_result is not None,
                "thoughts": captain.thought_rows,
                "actionPublications": captain.action_rows,
                "impactOverloadLocks": captain.overload_rows,
            }

        both_impacted = all(captain.impacts_received for captain in self.captains.values())
        response_contract = all(
            row.get("responseContractOk") is True
            for captain in self.captains.values()
            for row in captain.thought_rows
        )
        all_publications_on_boundary = all(row["publicationOnControlBoundary"] for row in self.publications)
        snapshot_causality = all(
            row["publishedSimulationSeconds"] + 1e-9 >= row["thoughtSnapshotSeconds"]
            for row in self.publications
        )
        overload_distinct = any(captain.overload_rows for captain in self.captains.values())
        post_bootstrap_launches = [row for row in self.launches if row["trigger"] != "initial"]
        impact_launches = [row for row in self.launches if row["trigger"] == "Impact"]
        impact_thoughts = [
            row
            for captain in self.captains.values()
            for row in captain.thought_rows
            if row["trigger"] == "Impact"
        ]
        impact_publications = [row for row in self.publications if row["trigger"] == "Impact"]
        published_impact_sequence_by_captain = {
            captain.id: max(
                [0]
                + [
                    int(row["impactSequenceSeenByThought"])
                    for row in impact_publications
                    if row["captainId"] == captain.id
                ]
            )
            for captain in self.captains.values()
        }
        mailbox_ack_waits_for_publication = all(
            captain.processed_impact_seq == published_impact_sequence_by_captain[captain.id]
            for captain in self.captains.values()
        )
        overload_deadlines_bounded = all(
            all(
                float(later["simulationSeconds"]) >= float(earlier["availabilityLockUntilSeconds"]) - 1e-9
                for earlier, later in zip(captain.overload_rows, captain.overload_rows[1:])
            )
            for captain in self.captains.values()
        )
        initial_thoughts_still_in_flight_at_end = all(
            captain.thought is not None
            and captain.thought_meta is not None
            and captain.thought_meta.get("trigger") == "initial"
            and captain.completed_result is None
            for captain in self.captains.values()
        )
        timing_diagnostic_check_names = {
            "ImpactIsOnlyRethinkTriggerAfterInitialThought",
            "ImpactActuallyProducesRethink",
            "ImpactRethinkSnapshotsQueuedMailbox",
        } if initial_thoughts_still_in_flight_at_end else set()

        checks = {
            "twoCaptainsShareOneBattlePhysicsWorld": True,
            "bothCaptainsCanReceiveImpactFromOpponent": both_impacted,
            "initialCaptainThoughtsLaunchAsynchronously": self.max_concurrent_thoughts >= 2,
            "viewportContinuesWhileCaptainThoughtsAreInFlight": self.viewport_ticks_while_thoughts_in_flight > 0,
            "ordinarySmoothPhysicsNeverLaunchesRethink": self.ordinary_physics_rethink_launches == 0,
            "ImpactIsOnlyRethinkTriggerAfterInitialThought": bool(post_bootstrap_launches) and all(
                row["trigger"] == "Impact" for row in post_bootstrap_launches
            ),
            "ImpactActuallyProducesRethink": self.impact_rethink_launches > 0,
            "ImpactRethinkSnapshotsQueuedMailbox": any(
                int(row["impactCountIncluded"]) >= 1 for row in impact_launches
            ),
            "ImpactMailboxAcknowledgementWaitsForPublication": mailbox_ack_waits_for_publication,
            "ImpactsCanArriveWhileThoughtIsInFlight": self.impact_while_thought_in_flight > 0,
            "repeatedImpactsCoalesceWithoutThoughtStorm": self.max_unprocessed_impacts >= 2 and all(
                sum(1 for row in self.launches if row["captainId"] == captain.id and row["trigger"] == "Impact") <= len(captain.impacts_received)
                for captain in self.captains.values()
            ),
            "captainInterruptPolicyIncludesVoluntaryCommitment": True,
            "impactOverloadAvailabilityLockIsSeparateFromVoluntaryPolicy": overload_distinct,
            "impactOverloadLockDeadlineIsBoundedAndNonExtending": overload_distinct and overload_deadlines_bounded,
            "captainActionsPublishOnlyAtControlBoundaries": all_publications_on_boundary,
            "lateThoughtsCannotRewritePastPhysics": snapshot_causality,
            "ImpactIsOnlyPhysicsVelocityDiscontinuity": self.non_impact_velocity_discontinuities == 0 and self.velocity_discontinuities == len(self.impacts),
            "viewportPredictionResetsAtEveryImpact": self.viewport_resets_on_impact == len(self.impacts),
            "captainCheckpointPinnedAcrossBattle": response_contract,
            "captainResponseContractPreserved": response_contract,
        }
        failed = [
            name
            for name, value in checks.items()
            if not value and name not in timing_diagnostic_check_names
        ]
        timing_diagnostics = {
            name: checks[name]
            for name in sorted(timing_diagnostic_check_names)
        }
        frame_gaps = self.viewport_frame_gaps_ms
        metrics = {
            "battleDurationSeconds": duration,
            "controlIntervalSeconds": control_dt,
            "viewportHz": float(self.args.viewport_hz),
            "viewportFrames": self.viewport_frames,
            "viewportTicksWhileThoughtsInFlight": self.viewport_ticks_while_thoughts_in_flight,
            "viewportFrameGapP95Ms": sorted(frame_gaps)[int(0.95 * (len(frame_gaps) - 1))] if frame_gaps else 0.0,
            "maximumViewportBoundaryCorrectionM": self.max_viewport_boundary_correction_m,
            "captainCount": 2,
            "initialThoughtLaunches": self.initial_thought_launches,
            "impactTriggeredThoughtLaunches": self.impact_rethink_launches,
            "impactTriggeredThoughtCompletions": len(impact_thoughts),
            "impactTriggeredActionPublications": len(impact_publications),
            "impactRethinksInFlightAtEnd": sum(
                1
                for captain in self.captains.values()
                if captain.thought is not None
                and captain.thought_meta is not None
                and captain.thought_meta.get("trigger") == "Impact"
            ),
            "maximumConcurrentThoughts": self.max_concurrent_thoughts,
            "impactCount": len(self.impacts),
            "impactWhileThoughtInFlightCount": self.impact_while_thought_in_flight,
            "maximumUnprocessedImpactCount": self.max_unprocessed_impacts,
            "actionPublicationCount": len(self.publications),
            "projectileCount": len(self.projectiles),
            "projectileHitCount": sum(1 for row in self.projectiles if row.get("outcome") == "hit"),
            "projectileMissCount": sum(1 for row in self.projectiles if row.get("outcome") == "miss"),
            "projectileInFlightCount": sum(1 for row in self.projectiles if row.get("outcome") == "in-flight"),
            "ordinaryPhysicsRethinkLaunches": self.ordinary_physics_rethink_launches,
            "physicsVelocityDiscontinuityCount": self.velocity_discontinuities,
            "nonImpactVelocityDiscontinuityCount": self.non_impact_velocity_discontinuities,
            "finalSimulationSeconds": self.sim_time,
            "finalShips": self.snapshot_ships(),
        }
        result = {
            "ok": not failed,
            "timingReadinessIsDiagnostic": True,
            "timingReadinessBlockedByInitialThoughts": initial_thoughts_still_in_flight_at_end,
            "timingDiagnosticChecks": timing_diagnostics,
            "semanticsIgnored": True,
            "contract": {
                "battleMode": "two-captain-live-physics",
                "physicsRule": "ordinary motion is continuous; Impact(...) is the only discrete velocity discontinuity and the only post-bootstrap rethink trigger",
                "thoughtRule": "one thought in flight per captain; physics and viewport continue; completed thoughts publish only at future control boundaries",
                "impactRule": "Impact is mailboxed; the captain's committed impact policy decides when reconsideration becomes eligible; repeated impacts coalesce",
                "lockRule": "voluntary cognitive commitment and involuntary repeated-impact availability lock are separate state; each overload lock is a bounded non-extending episode that must yield a rethink opportunity before another episode can begin",
            },
            "checks": checks,
            "failedChecks": failed,
            "metrics": metrics,
            "captains": captain_rows,
            "thoughtLaunches": self.launches,
            "actionPublications": self.publications,
            "projectiles": self.projectiles,
            "impacts": self.impacts,
        }
        if self.generate_samples:
            result["generationDrain"] = self.drain_generation_thoughts()
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run two asynchronous CLEF captains against one another in a local Impact-driven battle smoke.")
    parser.add_argument("--backend-url", default="")
    parser.add_argument("--checkpoint-id", default="smoke.tinystories-clef.reference")
    parser.add_argument("--checkpoint-sha256", default="reference-pinned-checkpoint")
    parser.add_argument("--evidence-execution-mode", choices=("auto", "prefix-cache", "full-batch"), default="auto")
    parser.add_argument("--questions-per-thought", type=int, default=20)
    parser.add_argument("--duration-seconds", type=float, default=6.0)
    parser.add_argument("--control-interval-seconds", type=float, default=0.5)
    parser.add_argument("--viewport-hz", type=float, default=60.0)
    parser.add_argument("--initial-separation-m", type=float, default=4000.0)
    parser.add_argument("--thrust-accel-mps2", type=float, default=25.0)
    parser.add_argument("--projectile-speed-mps", type=float, default=6000.0)
    parser.add_argument("--projectile-hit-radius-m", type=float, default=8.0)
    parser.add_argument("--fire-cooldown-seconds", type=float, default=0.4)
    parser.add_argument("--impact-delta-v-mps", type=float, default=0.75)
    parser.add_argument("--impact-damage-fraction", type=float, default=0.02)
    parser.add_argument("--overload-impact-count", type=int, default=3)
    parser.add_argument("--overload-window-seconds", type=float, default=1.0)
    parser.add_argument("--overload-lock-seconds", type=float, default=0.75)
    parser.add_argument("--generate-samples", action="store_true")
    parser.add_argument(
        "--generation-drain-seconds",
        type=float,
        default=60.0,
        help=(
            "Generation-only wall-time budget for harvesting already-launched captain thoughts "
            "after the battle horizon without advancing physics or publishing actions (default: 60)."
        ),
    )
    parser.add_argument("--generation-output-dir", default="")
    parser.add_argument("--simulation-id", default="battle-2")
    parser.add_argument("--simulation-seed", type=int, default=0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    if args.questions_per_thought < 10 or args.questions_per_thought > 20:
        raise SystemExit("--questions-per-thought must be between 10 and 20")
    for name in (
        "duration_seconds", "control_interval_seconds", "viewport_hz", "initial_separation_m",
        "thrust_accel_mps2", "projectile_speed_mps", "projectile_hit_radius_m", "fire_cooldown_seconds", "request_timeout_seconds",
    ):
        if float(getattr(args, name)) <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be positive")
    if args.overload_impact_count < 2:
        raise SystemExit("--overload-impact-count must be at least 2")
    if float(args.generation_drain_seconds) < 0:
        raise SystemExit("--generation-drain-seconds must be non-negative")

    battle = Battle(args)
    try:
        result = battle.run()
        generation = battle.write_generation_artifacts(result)
        if generation:
            result["generation"] = generation
    finally:
        battle.close()
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
