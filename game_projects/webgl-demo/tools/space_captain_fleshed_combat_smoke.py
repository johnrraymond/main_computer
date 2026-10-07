from __future__ import annotations

import argparse
import json
import math
import random
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any


EPS = 1e-9
CONTROL_DT = 0.5
PHYSICS_DT = 0.1


GAME_ROOT = Path(__file__).resolve().parents[1]
TOOL_ROOT = GAME_ROOT / "tools"
ROOT = GAME_ROOT.parents[1]
LIVE_BACKEND = TOOL_ROOT / "space_captain_clef_backend.py"
DEFAULT_LIVE_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_structured_supervision_train_v1"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, timeout: float = 1.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    *,
    request_id: str = "",
    client_send_unix_ns: int | None = None,
) -> dict[str, Any]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"content-type": "application/json"}
    if request_id:
        headers["x-main-computer-request-id"] = request_id
    if client_send_unix_ns is not None:
        headers["x-main-computer-client-send-unix-ns"] = str(int(client_send_unix_ns))
    request = urllib.request.Request(url, data=raw, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_nanojev_python() -> Path:
    home = Path.home()
    windows = home / "NanoJev" / ".venv" / "Scripts" / "python.exe"
    if windows.is_file():
        return windows
    posix = home / "NanoJev" / ".venv" / "bin" / "python"
    if posix.is_file():
        return posix
    raise RuntimeError("could not find NanoJev venv Python; pass --nanojev-python")


class LiveActionDriver:
    """Run independent asynchronous CLEF thoughts for each combat captain."""

    def __init__(
        self,
        evaluate_url: str,
        health: dict[str, Any],
        *,
        request_timeout_seconds: float = 180.0,
        include_call_snapshots: bool = False,
    ) -> None:
        self.evaluate_url = str(evaluate_url)
        self.health = dict(health)
        self.request_timeout_seconds = float(request_timeout_seconds)
        self.include_call_snapshots = bool(include_call_snapshots)
        self.call_snapshots: list[dict[str, Any]] = []
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fleshed-captain")
        self.thoughts: dict[str, Future[dict[str, Any]]] = {}
        self.thought_meta: dict[str, dict[str, Any]] = {}
        self.launch_count = 0
        self.completion_count = 0
        self.call_count = 0
        self.judgment_count = 0
        self.max_concurrent_thoughts = 0
        self.model_latency_ms: list[float] = []
        self.wall_latency_ms: list[float] = []
        self.last_response_diagnostics: dict[str, Any] = {}
        self.last_response_diagnostics_by_captain: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _candidate_text(smoke: "CombatSmoke", ship: "Ship", action: dict[str, str]) -> str:
        target = smoke.opponent(ship)
        r = smoke.range_between(ship, target)
        weapon = action["weapon"]
        if weapon in {"pulse", "heavy"}:
            spec = smoke.weapon_specs(weapon)
            envelope = smoke.envelope_factor(weapon, r)
            heat_after = ship.weapon_heat + spec["heat"] * (1.0 + 0.55 * (1.0 - ship.subsystems.weapons))
            weapon_note = (
                f"{weapon} fire, envelope={envelope:.2f}, track floor={spec['track_floor']:.2f}, "
                f"projected heat={heat_after:.2f}"
            )
        else:
            weapon_note = "hold fire and cool weapons"
        if action["warp"] == "initiate":
            warp_note = f"initiate warp; current full-spool estimate={smoke.warp_spool_seconds(ship):.1f}s"
        elif action["warp"] == "continue":
            warp_note = f"continue warp spool from {ship.warp_progress:.0%}"
        elif action["warp"] == "cancel":
            warp_note = f"cancel warp spool at {ship.warp_progress:.0%} and remain in combat"
        else:
            warp_note = "no warp transition"
        return (
            f"Maneuver={action['maneuver']}; weapon={weapon_note}; defense={action['defense']}; "
            f"warp={warp_note}. Max thrust={smoke.max_thrust(ship):.1f} m/s^2."
        )

    @staticmethod
    def _context(smoke: "CombatSmoke", ship: "Ship") -> str:
        target = smoke.opponent(ship)
        r = smoke.range_between(ship, target)
        own = ship.subsystems
        enemy = target.subsystems
        text = (
            f"Acting captain={ship.id}; live combat t={smoke.time:.1f}s range={r:.0f}m. "
            f"Own hull={ship.hull:.2f}, propulsion/weapons/sensors/defense/warp="
            f"{own.propulsion:.2f}/{own.weapons:.2f}/{own.sensors:.2f}/{own.defense:.2f}/{own.warp:.2f}, "
            f"track={ship.track_quality:.2f}, heat={ship.weapon_heat:.2f}, shield={ship.defense_charge:.2f}, "
            f"warp={ship.warp_state} {ship.warp_progress:.0%}. "
            f"Enemy hull={target.hull:.2f}, propulsion/weapons/sensors/defense/warp="
            f"{enemy.propulsion:.2f}/{enemy.weapons:.2f}/{enemy.sensors:.2f}/{enemy.defense:.2f}/{enemy.warp:.2f}, "
            f"track={target.track_quality:.2f}, heat={target.weapon_heat:.2f}, warp={target.warp_state} {target.warp_progress:.0%}. "
            "Choose the reachable action package that best survives, gains tactical advantage, and completes or prevents escape."
        )
        if len(text) > 760:
            raise RuntimeError(f"live-action captain context exceeds backend bound: {len(text)} chars")
        return text

    def request_for(self, smoke: "CombatSmoke", ship: "Ship", trigger: str) -> tuple[dict[str, Any], dict[str, Any]]:
        candidates = smoke.live_action_candidates(ship)
        candidate_lookup: dict[str, dict[str, str]] = {}
        candidate_ids: list[str] = []
        for index, action in enumerate(candidates):
            candidate_id = f"{ship.id}-c{index}"
            candidate_ids.append(candidate_id)
            candidate_lookup[candidate_id] = dict(action)
        questions: list[dict[str, Any]] = []
        for pair_index, (left_index, right_index) in enumerate(combinations(range(len(candidates)), 2)):
            left_id = candidate_ids[left_index]
            right_id = candidate_ids[right_index]
            questions.append({
                "id": f"live-{ship.id}-{int(round(smoke.time * 10)):05d}-{pair_index:02d}",
                "optionA": left_id,
                "optionB": right_id,
                "optionAText": self._candidate_text(smoke, ship, candidates[left_index]),
                "optionBText": self._candidate_text(smoke, ship, candidates[right_index]),
                "semanticMode": "machine-grounded-tactical-control-v1",
                "text": "From your own priorities, which complete reachable combat action produces the better future?",
            })
        payload = {
            "schema": "game.captainDecisionRequest.v6",
            "checkpoint": {
                "family": "tinystories-clef",
                "checkpointId": str(self.health["checkpointId"]),
                "sha256": str(self.health["checkpointSha256"]),
            },
            "semanticContext": {"mode": "compact-shared-context-v2", "text": self._context(smoke, ship)},
            "execution": {"evidenceMode": "auto"},
            "battle": {
                "mode": "battle-2",
                "trigger": trigger,
                "captainId": ship.id,
                "observation": smoke.ship_snapshot(ship),
            },
            "tacticalControls": [
                {"id": candidate_ids[index], **dict(action)} for index, action in enumerate(candidates)
            ],
            "questions": questions,
        }
        strategic_pairs: list[dict[str, str]] = []
        selection_mode = "flat-pairwise-v1"
        if ship.warp_state == "spooling":
            selection_mode = "matched-warp-branch-v1"
            by_tactical_shell: dict[tuple[str, str, str], dict[str, str]] = {}
            for candidate_id in candidate_ids:
                action = candidate_lookup[candidate_id]
                shell = (action["maneuver"], action["weapon"], action["defense"])
                by_tactical_shell.setdefault(shell, {})[action["warp"]] = candidate_id
            for shell_index, (_, branch_ids) in enumerate(by_tactical_shell.items()):
                if {"continue", "cancel"}.issubset(branch_ids):
                    strategic_pairs.append({
                        "shellId": f"shell-{shell_index}",
                        "continueCandidateId": branch_ids["continue"],
                        "cancelCandidateId": branch_ids["cancel"],
                    })
            if len(strategic_pairs) != 3:
                raise RuntimeError(
                    f"spooling live-action surface must contain exactly 3 matched warp pairs, got {strategic_pairs}"
                )
        client_request_id = f"{ship.id}-{int(round(smoke.time * 1000)):08d}-{self.launch_count + 1:06d}"
        payload["diagnostics"] = {"clientRequestId": client_request_id}
        meta = {
            "captainId": ship.id,
            "trigger": trigger,
            "launchSimulationSeconds": smoke.time,
            "clientRequestId": client_request_id,
            "questionCount": len(questions),
            "candidateLookup": candidate_lookup,
            "candidateIds": candidate_ids,
            "selectionMode": selection_mode,
            "strategicPairs": strategic_pairs,
        }
        if self.include_call_snapshots:
            meta["callSnapshotRequest"] = payload
        return payload, meta

    def _provider_call(self, payload: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        client_send_unix_ns = time.time_ns()
        request_id = str(meta.get("clientRequestId") or "")
        response = _post_json(
            self.evaluate_url,
            payload,
            timeout=self.request_timeout_seconds,
            request_id=request_id,
            client_send_unix_ns=client_send_unix_ns,
        )
        completed = time.perf_counter()
        return {
            "response": response,
            "wallLatencyMs": (completed - started) * 1000.0,
            "clientSendUnixNs": client_send_unix_ns,
            "completedWallMonotonic": completed,
            **meta,
        }

    def launch_thought(self, smoke: "CombatSmoke", ship: "Ship", trigger: str) -> bool:
        existing = self.thoughts.get(ship.id)
        if existing is not None:
            return False
        payload, meta = self.request_for(smoke, ship, trigger)
        self.thought_meta[ship.id] = meta
        self.thoughts[ship.id] = self.executor.submit(self._provider_call, payload, meta)
        self.launch_count += 1
        self.max_concurrent_thoughts = max(self.max_concurrent_thoughts, len(self.thoughts))
        smoke.event(
            "live-thought-launch",
            ship=ship.id,
            trigger=trigger,
            questionCount=meta["questionCount"],
        )
        return True

    def thought_in_flight(self, ship_id: str) -> bool:
        return ship_id in self.thoughts

    def harvest_completed(self, smoke: "CombatSmoke", ship: "Ship") -> dict[str, str] | None:
        future = self.thoughts.get(ship.id)
        if future is None or not future.done():
            return None
        try:
            result = future.result()
        finally:
            self.thoughts.pop(ship.id, None)
            self.thought_meta.pop(ship.id, None)
        response = dict(result["response"])
        answers = list(response.get("answers") or [])
        expected = int(result["questionCount"])
        if len(answers) != expected:
            raise RuntimeError(
                f"live-action backend returned {len(answers)} answers for {expected} questions for {ship.id}"
            )
        candidate_lookup = dict(result["candidateLookup"])
        candidate_ids = list(result["candidateIds"])
        scores = {candidate_id: 0.0 for candidate_id in candidate_ids}
        for answer in answers:
            choice = str(answer.get("choice") or "")
            if choice not in scores:
                raise RuntimeError(f"live-action backend returned invalid answer for {ship.id}: {answer}")
            scores[choice] += 1.0 + float(answer.get("margin") or 0.0)

        selection_mode = str(result.get("selectionMode") or "flat-pairwise-v1")
        strategic_pairs = list(result.get("strategicPairs") or [])
        strategic_branch_scores: dict[str, float] = {}
        strategic_comparisons: list[dict[str, Any]] = []
        winning_strategic_branch: str | None = None
        conditional_scores: dict[str, float] = {}
        conditional_ranked: list[tuple[str, float]] = []

        if selection_mode == "matched-warp-branch-v1":
            answer_by_pair: dict[frozenset[str], dict[str, Any]] = {}
            for answer in answers:
                ids = [str(value) for value in (answer.get("candidateIds") or [])]
                if len(ids) == 2:
                    answer_by_pair[frozenset(ids)] = answer
            branch_scores = {"continue": 0.0, "cancel": 0.0}
            for pair in strategic_pairs:
                continue_id = str(pair["continueCandidateId"])
                cancel_id = str(pair["cancelCandidateId"])
                answer = answer_by_pair.get(frozenset((continue_id, cancel_id)))
                if answer is None:
                    raise RuntimeError(
                        f"missing matched warp comparison for {ship.id}: {continue_id} vs {cancel_id}"
                    )
                ids = [str(value) for value in (answer.get("candidateIds") or [])]
                probabilities = [float(value) for value in (answer.get("probabilities") or [])]
                if len(ids) != 2 or len(probabilities) != 2:
                    raise RuntimeError(f"invalid matched warp answer for {ship.id}: {answer}")
                probability_by_id = dict(zip(ids, probabilities))
                continue_probability = probability_by_id[continue_id]
                cancel_probability = probability_by_id[cancel_id]
                branch_scores["continue"] += continue_probability
                branch_scores["cancel"] += cancel_probability
                strategic_comparisons.append({
                    "shellId": pair["shellId"],
                    "continueCandidateId": continue_id,
                    "cancelCandidateId": cancel_id,
                    "continueProbability": continue_probability,
                    "cancelProbability": cancel_probability,
                    "choice": str(answer.get("choice") or ""),
                    "margin": float(answer.get("margin") or 0.0),
                })
            strategic_branch_scores = branch_scores
            winning_strategic_branch = (
                "continue" if branch_scores["continue"] >= branch_scores["cancel"] else "cancel"
            )
            eligible_ids = [
                candidate_id
                for candidate_id in candidate_ids
                if candidate_lookup[candidate_id]["warp"] == winning_strategic_branch
            ]
            conditional_scores = {candidate_id: 0.0 for candidate_id in eligible_ids}
            eligible = set(eligible_ids)
            for answer in answers:
                ids = [str(value) for value in (answer.get("candidateIds") or [])]
                if len(ids) != 2 or not set(ids).issubset(eligible):
                    continue
                choice = str(answer.get("choice") or "")
                conditional_scores[choice] += 1.0 + float(answer.get("margin") or 0.0)
            conditional_ranked = sorted(
                conditional_scores.items(),
                key=lambda item: (-item[1], candidate_ids.index(item[0])),
            )
            if not conditional_ranked:
                raise RuntimeError(f"no conditional tactical ranking for {ship.id} branch={winning_strategic_branch}")
            winner_id = conditional_ranked[0][0]
            ranked = conditional_ranked
        else:
            ranked = sorted(scores.items(), key=lambda item: (-item[1], candidate_ids.index(item[0])))
            winner_id = ranked[0][0]

        chosen = dict(candidate_lookup[winner_id])
        chosen_at_decision = dict(chosen)
        wall_ms = float(result["wallLatencyMs"])
        model_ms = float(response.get("modelLatencyMs") or 0.0)
        server_diagnostics = dict(response.get("serverDiagnostics") or {})
        diagnostics = {
            "captainId": ship.id,
            "trigger": result["trigger"],
            "clientRequestId": result.get("clientRequestId"),
            "clientSendUnixNs": result.get("clientSendUnixNs"),
            "launchSimulationSeconds": result["launchSimulationSeconds"],
            "publishSimulationSeconds": smoke.time,
            "wallLatencyMs": wall_ms,
            "modelLatencyMs": model_ms,
            "amortizedQuestionLatencyMs": float(response.get("amortizedQuestionLatencyMs") or 0.0),
            "evidenceExecutionModeActual": response.get("evidenceExecutionModeActual"),
            "sharedPrefixCacheUsed": response.get("sharedPrefixCacheUsed"),
            "backboneForwardBatchCount": response.get("backboneForwardBatchCount"),
            "questionCount": len(answers),
            "selectionMode": selection_mode,
            "winningStrategicBranch": winning_strategic_branch,
            "chosenCandidateId": winner_id,
            "serverQueueAdmissionMs": server_diagnostics.get("queueAdmissionMs"),
            "serverHttpBodyReadMs": server_diagnostics.get("httpBodyReadMs"),
            "serverEvaluateTotalMs": server_diagnostics.get("evaluateTotalMs"),
            "serverStageTimingsMs": dict(server_diagnostics.get("stageTimingsMs") or {}),
        }
        self.completion_count += 1
        self.call_count += 1
        self.judgment_count += len(answers)
        self.wall_latency_ms.append(wall_ms)
        self.model_latency_ms.append(model_ms)
        self.last_response_diagnostics = diagnostics
        self.last_response_diagnostics_by_captain[ship.id] = diagnostics
        if self.include_call_snapshots:
            request_snapshot = result.get("callSnapshotRequest") or {}
            snapshot = {
                "captainId": ship.id,
                "trigger": result["trigger"],
                "launchSimulationSeconds": result["launchSimulationSeconds"],
                "publishSimulationSeconds": smoke.time,
                "request": request_snapshot,
                "observationAtLaunch": dict((request_snapshot.get("battle") or {}).get("observation") or {}),
                "candidates": [dict(candidate) for candidate in (request_snapshot.get("tacticalControls") or [])],
                "questions": [dict(question) for question in (request_snapshot.get("questions") or [])],
                "answers": [dict(answer) for answer in answers],
                "selectionMode": selection_mode,
                "candidateScores": {candidate_id: scores[candidate_id] for candidate_id in candidate_ids},
                "ranking": [
                    {"candidateId": candidate_id, "score": score}
                    for candidate_id, score in ranked
                ],
                "chosenCandidateId": winner_id,
                "chosenAction": chosen_at_decision,
                "responseDiagnostics": dict(diagnostics),
            }
            if selection_mode == "matched-warp-branch-v1":
                snapshot.update({
                    "strategicPairs": [dict(pair) for pair in strategic_pairs],
                    "strategicComparisons": strategic_comparisons,
                    "strategicBranchScores": dict(strategic_branch_scores),
                    "winningStrategicBranch": winning_strategic_branch,
                    "conditionalCandidateScores": dict(conditional_scores),
                    "conditionalRanking": [
                        {"candidateId": candidate_id, "score": score}
                        for candidate_id, score in conditional_ranked
                    ],
                })
            self.call_snapshots.append(snapshot)
        smoke.event("live-thought-publish", **diagnostics, action=dict(chosen_at_decision))
        return chosen

    def close(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "provider": self.health.get("provider"),
            "runDir": self.health.get("runDir"),
            "checkpointPath": self.health.get("checkpointPath"),
            "checkpointId": self.health.get("checkpointId"),
            "checkpointSha256": self.health.get("checkpointSha256"),
            "cycle": self.health.get("cycle"),
            "reuseEpoch": self.health.get("reuseEpoch"),
            "thoughtLaunchCount": self.launch_count,
            "thoughtCompletionCount": self.completion_count,
            "thoughtsInFlightAtEnd": len(self.thoughts),
            "maxConcurrentThoughts": self.max_concurrent_thoughts,
            "callCount": self.call_count,
            "independentJudgmentCount": self.judgment_count,
            "meanModelLatencyMs": (sum(self.model_latency_ms) / len(self.model_latency_ms)) if self.model_latency_ms else 0.0,
            "meanWallLatencyMs": (sum(self.wall_latency_ms) / len(self.wall_latency_ms)) if self.wall_latency_ms else 0.0,
            "lastResponseDiagnostics": dict(self.last_response_diagnostics),
            "lastResponseDiagnosticsByCaptain": dict(self.last_response_diagnostics_by_captain),
            "callSnapshotsIncluded": self.include_call_snapshots,
            **({"callSnapshots": self.call_snapshots} if self.include_call_snapshots else {}),
        }


@dataclass
class Subsystems:
    propulsion: float = 1.0
    weapons: float = 1.0
    sensors: float = 1.0
    defense: float = 1.0
    warp: float = 1.0

    def clamp(self) -> None:
        for name in ("propulsion", "weapons", "sensors", "defense", "warp"):
            setattr(self, name, max(0.0, min(1.0, float(getattr(self, name)))))


@dataclass
class Ship:
    id: str
    label: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    hull: float = 1.0
    subsystems: Subsystems = field(default_factory=Subsystems)
    weapon_heat: float = 0.0
    overheated: bool = False
    defense_charge: float = 1.0
    defense_mode: str = "none"
    track_quality: float = 0.20
    current_ax: float = 0.0
    current_ay: float = 0.0
    maneuver: str = "hold"
    weapon_mode: str = "hold"
    cooldown_until: float = 0.0
    next_think_at: float = 0.0
    alive: bool = True
    mission_killed: bool = False
    escaped: bool = False
    warp_state: str = "idle"
    warp_progress: float = 0.0
    warp_initiations: int = 0
    warp_cancellations: int = 0


@dataclass
class Shot:
    seq: int
    fired_at: float
    arrives_at: float
    attacker_id: str
    target_id: str
    weapon_mode: str
    hit_probability: float
    damage: float
    subsystem_damage: float
    impulse_mps: float
    attacker_track_quality: float
    range_at_fire: float


class CombatSmoke:
    def __init__(
        self,
        seed: int,
        duration: float,
        *,
        live_action_driver: LiveActionDriver | None = None,
        live_action_interval_seconds: float = 2.0,
    ) -> None:
        self.seed = seed
        self.duration = duration
        self.rng = random.Random(seed)
        self.time = 0.0
        self.shot_seq = 0
        self.events: list[dict[str, Any]] = []
        self.shots: list[Shot] = []
        self.unique_actions: set[str] = set()
        self.subsystem_hits = 0
        self.shield_absorptions = 0
        self.ecm_ticks = 0
        self.heat_blocks = 0
        self.track_gated_shots = 0
        self.weapon_modes_fired: set[str] = set()
        self.warp_initiations = 0
        self.warp_cancellations = 0
        self.warp_completions = 0
        self.warp_subsystem_hits = 0
        self.terminal_reason: str | None = None
        self.terminal_time: float | None = None
        self.live_action_driver = live_action_driver
        self.live_action_interval_seconds = float(live_action_interval_seconds)
        self.live_next_thought_at: dict[str, float] = {"alpha": 0.0, "beta": 0.0}
        self.live_current_actions: dict[str, dict[str, str]] = {}
        self.live_start_wall: float | None = None
        self.ships = {
            "alpha": Ship("alpha", "Hunter Alpha", -1600.0, -120.0, vx=35.0, vy=6.0, track_quality=0.28),
            "beta": Ship("beta", "Guardian Beta", 1600.0, 120.0, vx=-20.0, vy=-3.0, track_quality=0.34),
        }

    @staticmethod
    def clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    @staticmethod
    def unit(x: float, y: float) -> tuple[float, float]:
        mag = math.hypot(x, y)
        if mag <= EPS:
            return (0.0, 0.0)
        return (x / mag, y / mag)

    def opponent(self, ship: Ship) -> Ship:
        return self.ships["beta" if ship.id == "alpha" else "alpha"]

    def range_between(self, a: Ship, b: Ship) -> float:
        return math.hypot(b.x - a.x, b.y - a.y)

    def relative_geometry(self, own: Ship, target: Ship) -> dict[str, float]:
        rx = target.x - own.x
        ry = target.y - own.y
        ux, uy = self.unit(rx, ry)
        lx, ly = -uy, ux
        rvx = target.vx - own.vx
        rvy = target.vy - own.vy
        closing = -(rvx * ux + rvy * uy)
        crossing = rvx * lx + rvy * ly
        return {
            "range": math.hypot(rx, ry),
            "ux": ux,
            "uy": uy,
            "lx": lx,
            "ly": ly,
            "closing": closing,
            "crossing": crossing,
        }

    @staticmethod
    def max_thrust(ship: Ship) -> float:
        # Propulsion degradation changes the physically reachable acceleration set.
        return 30.0 * (0.25 + 0.75 * ship.subsystems.propulsion)

    @staticmethod
    def sensor_track_gain(ship: Ship) -> float:
        return 0.22 * (0.15 + 0.85 * ship.subsystems.sensors)

    @staticmethod
    def shield_efficiency(ship: Ship) -> float:
        return 0.20 + 0.65 * ship.subsystems.defense


    @staticmethod
    def warp_spool_rate(ship: Ship) -> float:
        # Healthy drive translates after ~8 s of uninterrupted spool. Damage stretches
        # that interval continuously; a nearly destroyed drive cannot make progress.
        if ship.subsystems.warp <= 0.08:
            return 0.0
        return (0.18 + 0.82 * ship.subsystems.warp) / 8.0

    @classmethod
    def warp_spool_seconds(cls, ship: Ship) -> float:
        rate = cls.warp_spool_rate(ship)
        return math.inf if rate <= EPS else 1.0 / rate

    @staticmethod
    def available_warp_choices(ship: Ship) -> list[str]:
        if ship.warp_state == "spooling":
            return ["continue", "cancel"]
        if ship.warp_state == "idle" and ship.subsystems.warp > 0.08:
            return ["initiate"]
        return []

    @staticmethod
    def weapon_specs(mode: str) -> dict[str, float]:
        if mode == "pulse":
            return {
                "min_range": 250.0,
                "optimal_range": 1350.0,
                "max_range": 2600.0,
                "speed": 5200.0,
                "damage": 0.085,
                "subsystem_damage": 0.055,
                "impulse": 6.0,
                "heat": 0.16,
                "cooldown": 0.55,
                "track_floor": 0.28,
            }
        if mode == "heavy":
            return {
                "min_range": 850.0,
                "optimal_range": 2450.0,
                "max_range": 4100.0,
                "speed": 3800.0,
                "damage": 0.17,
                "subsystem_damage": 0.12,
                "impulse": 11.0,
                "heat": 0.42,
                "cooldown": 1.25,
                "track_floor": 0.54,
            }
        raise KeyError(mode)

    @classmethod
    def envelope_factor(cls, mode: str, range_m: float) -> float:
        spec = cls.weapon_specs(mode)
        lo = spec["min_range"]
        opt = spec["optimal_range"]
        hi = spec["max_range"]
        if range_m < lo or range_m > hi:
            return 0.0
        if range_m <= opt:
            return 0.45 + 0.55 * (range_m - lo) / max(EPS, opt - lo)
        return 1.0 - 0.72 * (range_m - opt) / max(EPS, hi - opt)

    def event(self, kind: str, **payload: Any) -> None:
        self.events.append({"t": round(self.time, 3), "kind": kind, **payload})

    def update_passive_state(self, dt: float) -> None:
        for ship in self.ships.values():
            if not ship.alive:
                continue
            cooling = 0.18 * dt * (0.55 + 0.45 * ship.subsystems.weapons)
            ship.weapon_heat = max(0.0, ship.weapon_heat - cooling)
            if ship.overheated and ship.weapon_heat <= 0.48:
                ship.overheated = False
                self.event("weapon-recovered", ship=ship.id, heat=round(ship.weapon_heat, 3))
            if ship.defense_mode == "shield":
                ship.defense_charge = max(0.0, ship.defense_charge - 0.12 * dt)
            elif ship.defense_mode == "ecm":
                ship.defense_charge = max(0.0, ship.defense_charge - 0.15 * dt)
            else:
                ship.defense_charge = min(1.0, ship.defense_charge + 0.10 * dt * (0.35 + 0.65 * ship.subsystems.defense))
            if ship.warp_state == "spooling":
                before = ship.warp_progress
                ship.warp_progress = min(1.0, ship.warp_progress + self.warp_spool_rate(ship) * dt)
                if self.warp_spool_rate(ship) <= EPS:
                    self.event("warp-stalled", ship=ship.id, progress=round(ship.warp_progress, 3), warpHealth=round(ship.subsystems.warp, 3))
                elif int(before * 10) != int(ship.warp_progress * 10):
                    self.event("warp-progress", ship=ship.id, progress=round(ship.warp_progress, 3), warpHealth=round(ship.subsystems.warp, 3))

    def update_tracking(self, dt: float) -> None:
        for ship in self.ships.values():
            if not ship.alive:
                continue
            target = self.opponent(ship)
            geom = self.relative_geometry(ship, target)
            target_accel = math.hypot(target.current_ax, target.current_ay)
            maneuver_penalty = min(0.18, target_accel / 30.0 * 0.12)
            crossing_penalty = min(0.10, abs(geom["crossing"]) / 500.0 * 0.10)
            ecm_penalty = 0.0
            if target.defense_mode == "ecm" and target.defense_charge > 0.0:
                ecm_penalty = 0.18 * (0.30 + 0.70 * target.subsystems.defense)
                self.ecm_ticks += 1
            gain = self.sensor_track_gain(ship)
            delta = (gain - maneuver_penalty - crossing_penalty - ecm_penalty) * dt
            ship.track_quality = self.clamp01(ship.track_quality + delta)

    def integrate(self, dt: float) -> None:
        for ship in self.ships.values():
            if not ship.alive:
                continue
            ship.vx += ship.current_ax * dt
            ship.vy += ship.current_ay * dt
            ship.x += ship.vx * dt
            ship.y += ship.vy * dt

    def choose_action(self, ship: Ship) -> dict[str, str]:
        target = self.opponent(ship)
        geom = self.relative_geometry(ship, target)
        r = geom["range"]

        # Active defense is chosen independently of weapon mode.
        if ship.hull < 0.46 and ship.defense_charge > 0.24 and ship.subsystems.defense > 0.18:
            defense = "shield"
        elif target.track_quality > 0.68 and ship.defense_charge > 0.34 and ship.subsystems.defense > 0.25:
            defense = "ecm"
        else:
            defense = "none"

        # Damage changes maneuver preference as well as max available thrust.
        if ship.subsystems.propulsion < 0.35:
            maneuver = "break"
        elif ship.hull < 0.38:
            maneuver = "evade-starboard" if ship.id == "alpha" else "evade-port"
        elif r > 2200.0:
            maneuver = "intercept"
        elif target.subsystems.propulsion < 0.35:
            maneuver = "press"
        elif int(self.time / 2.0) % 2 == 0:
            maneuver = "cross-port" if ship.id == "alpha" else "cross-starboard"
        else:
            maneuver = "cross-starboard" if ship.id == "alpha" else "cross-port"

        # Weapon envelopes + heat + track quality jointly determine what is actually useful.
        weapon = "hold"
        heavy = self.weapon_specs("heavy")
        pulse = self.weapon_specs("pulse")
        heavy_ok = (
            self.envelope_factor("heavy", r) > 0.0
            and ship.track_quality >= heavy["track_floor"]
            and ship.weapon_heat + heavy["heat"] <= 1.0
            and not ship.overheated
            and ship.subsystems.weapons > 0.18
        )
        pulse_ok = (
            self.envelope_factor("pulse", r) > 0.0
            and ship.track_quality >= pulse["track_floor"]
            and ship.weapon_heat + pulse["heat"] <= 1.0
            and not ship.overheated
            and ship.subsystems.weapons > 0.10
        )
        if heavy_ok and (r > 1700.0 or target.subsystems.propulsion < 0.45):
            weapon = "heavy"
        elif pulse_ok:
            weapon = "pulse"
        elif self.envelope_factor("heavy", r) > 0.0 and ship.track_quality < heavy["track_floor"]:
            self.track_gated_shots += 1

        warp = "none"
        if ship.warp_state == "spooling":
            # Once spool exists, cancel is a real captain choice until translation.
            # Alpha deliberately demonstrates one tactical abort if the opponent becomes
            # vulnerable enough while Alpha still has fighting margin.
            if ship.warp_cancellations == 0 and ship.warp_progress >= 0.35:
                warp = "cancel"
            else:
                warp = "continue"
        elif ship.subsystems.warp > 0.08 and (ship.hull < 0.42 or ship.subsystems.propulsion < 0.42):
            warp = "initiate"
        return {"maneuver": maneuver, "weapon": weapon, "defense": defense, "warp": warp}

    def live_action_candidates(self, ship: Ship) -> list[dict[str, str]]:
        """Return a compact reachable action surface for one CLEF thought.

        While warp is spooling, strategic continue/cancel is evaluated with three
        matched tactical counterfactual shells (six candidates total). Outside
        spool, retain the five-candidate tactical surface.
        """
        base = dict(self.choose_action(ship))
        target = self.opponent(ship)
        r = self.range_between(ship, target)

        def weapon_legal(mode: str) -> bool:
            if mode == "hold":
                return True
            spec = self.weapon_specs(mode)
            return (
                self.envelope_factor(mode, r) > 0.0
                and ship.track_quality >= spec["track_floor"]
                and ship.weapon_heat
                + spec["heat"] * (1.0 + 0.55 * (1.0 - ship.subsystems.weapons))
                <= 1.0 + EPS
                and not ship.overheated
                and ship.subsystems.weapons > (0.10 if mode == "pulse" else 0.18)
            )

        weapon_options = [mode for mode in ("hold", "pulse", "heavy") if weapon_legal(mode)]
        defense_options = ["none"]
        if ship.defense_charge > 0.03 and ship.subsystems.defense > 0.18:
            defense_options.append("shield")
        if ship.defense_charge > 0.03 and ship.subsystems.defense > 0.25:
            defense_options.append("ecm")
        available_warp = self.available_warp_choices(ship)

        if base["weapon"] not in weapon_options:
            base["weapon"] = "hold"
        if base["defense"] not in defense_options:
            base["defense"] = "none"

        maneuver_cycle = [
            "intercept",
            "cross-port",
            "cross-starboard",
            "evade-port" if ship.id == "beta" else "evade-starboard",
            "break",
        ]

        if ship.warp_state == "spooling":
            if not {"continue", "cancel"}.issubset(set(available_warp)):
                raise RuntimeError(f"spooling ship lacks continue/cancel warp choices: {available_warp}")

            tactical_shells: list[dict[str, str]] = []
            tactical_seen: set[tuple[str, str, str]] = set()

            def add_shell(maneuver: str, weapon: str, defense: str) -> None:
                key = (maneuver, weapon, defense)
                if key in tactical_seen:
                    return
                tactical_seen.add(key)
                tactical_shells.append({
                    "maneuver": maneuver,
                    "weapon": weapon,
                    "defense": defense,
                })

            add_shell(base["maneuver"], base["weapon"], base["defense"])
            for maneuver in maneuver_cycle:
                if maneuver != base["maneuver"]:
                    add_shell(maneuver, base["weapon"], base["defense"])
                    break
            for weapon in weapon_options:
                if weapon != base["weapon"]:
                    add_shell(base["maneuver"], weapon, base["defense"])
                    break
            for defense in defense_options:
                if defense != base["defense"]:
                    add_shell(base["maneuver"], base["weapon"], defense)
                    break
            for maneuver in maneuver_cycle:
                for weapon in weapon_options:
                    for defense in defense_options:
                        add_shell(maneuver, weapon, defense)
                        if len(tactical_shells) >= 3:
                            break
                    if len(tactical_shells) >= 3:
                        break
                if len(tactical_shells) >= 3:
                    break
            if len(tactical_shells) < 3:
                raise RuntimeError(f"could not construct at least 3 distinct tactical shells for {ship.id}: {tactical_shells}")
            tactical_shells = tactical_shells[:3]

            candidates: list[dict[str, str]] = []
            for shell in tactical_shells:
                candidates.append({**shell, "warp": "continue"})
                candidates.append({**shell, "warp": "cancel"})
            return candidates

        warp_options = ["none"]
        if "initiate" in available_warp:
            warp_options.append("initiate")
        if base["warp"] not in warp_options:
            base["warp"] = warp_options[0]

        candidates: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()

        def add(action: dict[str, str]) -> None:
            key = (action["maneuver"], action["weapon"], action["defense"], action["warp"])
            if key not in seen:
                seen.add(key)
                candidates.append(dict(action))

        add(base)
        for maneuver in maneuver_cycle:
            if maneuver != base["maneuver"]:
                add({**base, "maneuver": maneuver})
                break
        for weapon in weapon_options:
            if weapon != base["weapon"]:
                add({**base, "weapon": weapon})
                break
        for defense in defense_options:
            if defense != base["defense"]:
                add({**base, "defense": defense})
                break
        for warp in warp_options:
            if warp != base["warp"]:
                add({**base, "warp": warp})
                break
        for maneuver in maneuver_cycle:
            add({**base, "maneuver": maneuver})
            if len(candidates) >= 5:
                break
        for weapon in weapon_options:
            add({**base, "weapon": weapon})
            if len(candidates) >= 5:
                break
        if len(candidates) < 2:
            add({"maneuver": "hold", "weapon": "hold", "defense": "none", "warp": warp_options[0]})
        return candidates[:5]

    def apply_action(self, ship: Ship, action: dict[str, str]) -> None:
        target = self.opponent(ship)
        geom = self.relative_geometry(ship, target)
        thrust = self.max_thrust(ship)
        ux, uy, lx, ly = geom["ux"], geom["uy"], geom["lx"], geom["ly"]
        maneuver = action["maneuver"]
        if maneuver == "intercept":
            dx, dy = ux, uy
        elif maneuver == "press":
            dx, dy = 0.92 * ux + 0.40 * lx, 0.92 * uy + 0.40 * ly
        elif maneuver == "cross-port":
            dx, dy = 0.18 * ux + lx, 0.18 * uy + ly
        elif maneuver == "cross-starboard":
            dx, dy = 0.18 * ux - lx, 0.18 * uy - ly
        elif maneuver == "evade-port":
            dx, dy = -0.18 * ux + lx, -0.18 * uy + ly
        elif maneuver == "evade-starboard":
            dx, dy = -0.18 * ux - lx, -0.18 * uy - ly
        elif maneuver == "break":
            dx, dy = -ux, -uy
        else:
            dx, dy = 0.0, 0.0
        nx, ny = self.unit(dx, dy)
        ship.current_ax = nx * thrust
        ship.current_ay = ny * thrust
        ship.maneuver = maneuver
        ship.weapon_mode = action["weapon"]
        ship.defense_mode = action["defense"] if ship.defense_charge > 0.03 else "none"
        warp_action = action.get("warp", "none")
        consumed_warp_action: str | None = None
        if warp_action == "initiate" and ship.warp_state == "idle" and ship.subsystems.warp > 0.08:
            ship.warp_state = "spooling"
            ship.warp_progress = 0.0
            ship.warp_initiations += 1
            self.warp_initiations += 1
            consumed_warp_action = "continue"
            self.event(
                "warp-initiated",
                ship=ship.id,
                estimatedSpoolSeconds=round(self.warp_spool_seconds(ship), 3),
                warpHealth=round(ship.subsystems.warp, 3),
            )
        elif warp_action == "cancel" and ship.warp_state == "spooling":
            cancelled_at = ship.warp_progress
            ship.warp_state = "idle"
            ship.warp_progress = 0.0
            ship.warp_cancellations += 1
            self.warp_cancellations += 1
            consumed_warp_action = "none"
            self.event("warp-cancelled", ship=ship.id, progress=round(cancelled_at, 3))
        signature = f"{ship.id}:{ship.maneuver}:{ship.weapon_mode}:{ship.defense_mode}:{warp_action}"
        self.unique_actions.add(signature)
        self.event(
            "decision",
            ship=ship.id,
            maneuver=ship.maneuver,
            weapon=ship.weapon_mode,
            defense=ship.defense_mode,
            warpAction=warp_action,
            warpState=ship.warp_state,
            warpProgress=round(ship.warp_progress, 3),
            warpChoices=self.available_warp_choices(ship),
            track=round(ship.track_quality, 3),
            heat=round(ship.weapon_heat, 3),
            propulsion=round(ship.subsystems.propulsion, 3),
            weapons=round(ship.subsystems.weapons, 3),
            sensors=round(ship.subsystems.sensors, 3),
            defenseHealth=round(ship.subsystems.defense, 3),
            warpHealth=round(ship.subsystems.warp, 3),
        )
        # Initiate/cancel are one-shot transition commands. If this action dict is
        # the live captain's persistent published action, consume the edge after
        # the transition succeeds while preserving maneuver/weapon/defense intent.
        if consumed_warp_action is not None:
            action["warp"] = consumed_warp_action

    def attempt_fire(self, ship: Ship) -> None:
        mode = ship.weapon_mode
        if mode not in ("pulse", "heavy") or self.time + EPS < ship.cooldown_until or not ship.alive:
            return
        target = self.opponent(ship)
        spec = self.weapon_specs(mode)
        r = self.range_between(ship, target)
        envelope = self.envelope_factor(mode, r)
        if envelope <= 0.0 or ship.track_quality < spec["track_floor"]:
            return
        effective_heat = spec["heat"] * (1.0 + 0.55 * (1.0 - ship.subsystems.weapons))
        if ship.weapon_heat + effective_heat > 1.0:
            ship.overheated = True
            self.heat_blocks += 1
            self.event("weapon-overheat", ship=ship.id, requested=mode, heat=round(ship.weapon_heat, 3))
            return

        target_speed = math.hypot(target.vx, target.vy)
        target_accel = math.hypot(target.current_ax, target.current_ay)
        evasion = min(0.36, target_accel / 30.0 * 0.20 + target_speed / 700.0 * 0.16)
        sensor_factor = 0.55 + 0.45 * ship.subsystems.sensors
        weapon_factor = 0.60 + 0.40 * ship.subsystems.weapons
        hit_probability = self.clamp01(
            0.08 + 0.80 * ship.track_quality * envelope * sensor_factor * weapon_factor - evasion
        )
        if target.defense_mode == "ecm" and target.defense_charge > 0.0:
            hit_probability *= 0.68
        travel = r / spec["speed"]
        self.shot_seq += 1
        self.shots.append(
            Shot(
                seq=self.shot_seq,
                fired_at=self.time,
                arrives_at=self.time + travel,
                attacker_id=ship.id,
                target_id=target.id,
                weapon_mode=mode,
                hit_probability=hit_probability,
                damage=spec["damage"] * (0.55 + 0.45 * ship.subsystems.weapons),
                subsystem_damage=spec["subsystem_damage"] * (0.55 + 0.45 * ship.subsystems.weapons),
                impulse_mps=spec["impulse"],
                attacker_track_quality=ship.track_quality,
                range_at_fire=r,
            )
        )
        ship.weapon_heat += effective_heat
        ship.cooldown_until = self.time + spec["cooldown"] * (1.0 + 0.65 * (1.0 - ship.subsystems.weapons))
        self.weapon_modes_fired.add(mode)
        self.event(
            "fire",
            ship=ship.id,
            weapon=mode,
            target=target.id,
            range=round(r, 1),
            track=round(ship.track_quality, 3),
            hitProbability=round(hit_probability, 3),
            heat=round(ship.weapon_heat, 3),
        )

    def choose_subsystem(self, shot: Shot, target: Ship) -> str:
        # Heavy fire is deliberately more likely to disable propulsion/weapons so that
        # subsystem damage can create a mission kill instead of being decorative.
        if shot.weapon_mode == "heavy":
            pool = ["propulsion", "weapons", "warp", "propulsion", "weapons", "warp", "sensors", "defense"]
        else:
            pool = ["propulsion", "weapons", "sensors", "defense", "warp"]
        return pool[self.rng.randrange(len(pool))]

    def resolve_shot(self, shot: Shot) -> None:
        attacker = self.ships[shot.attacker_id]
        target = self.ships[shot.target_id]
        if not target.alive:
            return
        roll = self.rng.random()
        if roll > shot.hit_probability:
            self.event(
                "miss",
                attacker=attacker.id,
                target=target.id,
                weapon=shot.weapon_mode,
                probability=round(shot.hit_probability, 3),
            )
            return

        hull_damage = shot.damage
        subsystem_damage = shot.subsystem_damage
        impulse = shot.impulse_mps
        absorbed = 0.0
        if target.defense_mode == "shield" and target.defense_charge > 0.0 and target.subsystems.defense > 0.05:
            efficiency = self.shield_efficiency(target)
            available_absorb = target.defense_charge * 0.55
            requested_absorb = hull_damage * efficiency
            absorbed = min(requested_absorb, available_absorb)
            hull_damage -= absorbed
            subsystem_damage *= (1.0 - 0.65 * efficiency)
            impulse *= (1.0 - 0.55 * efficiency)
            target.defense_charge = max(0.0, target.defense_charge - absorbed / 0.55)
            if absorbed > 0.0:
                self.shield_absorptions += 1

        subsystem = self.choose_subsystem(shot, target)
        old_health = getattr(target.subsystems, subsystem)
        setattr(target.subsystems, subsystem, old_health - subsystem_damage)
        target.subsystems.clamp()
        self.subsystem_hits += 1
        if subsystem == "warp":
            self.warp_subsystem_hits += 1
            self.event(
                "warp-damaged",
                ship=target.id,
                before=round(old_health, 3),
                after=round(target.subsystems.warp, 3),
                estimatedSpoolSeconds=round(self.warp_spool_seconds(target), 3) if self.warp_spool_rate(target) > EPS else None,
                spoolProgress=round(target.warp_progress, 3),
            )
        target.hull = max(0.0, target.hull - hull_damage)

        geom = self.relative_geometry(attacker, target)
        target.vx += geom["ux"] * impulse
        target.vy += geom["uy"] * impulse
        # Being hit degrades the victim's local firing solution until sensors reacquire.
        target.track_quality = max(0.0, target.track_quality - (0.08 if shot.weapon_mode == "pulse" else 0.15))
        self.event(
            "hit",
            attacker=attacker.id,
            target=target.id,
            weapon=shot.weapon_mode,
            hullDamage=round(hull_damage, 3),
            shieldAbsorbed=round(absorbed, 3),
            subsystem=subsystem,
            subsystemBefore=round(old_health, 3),
            subsystemAfter=round(getattr(target.subsystems, subsystem), 3),
            hullRemaining=round(target.hull, 3),
        )
        self.evaluate_terminal(target)

    def process_arrivals(self) -> None:
        due = [shot for shot in self.shots if shot.arrives_at <= self.time + EPS]
        self.shots = [shot for shot in self.shots if shot.arrives_at > self.time + EPS]
        for shot in sorted(due, key=lambda s: (s.arrives_at, s.seq)):
            self.resolve_shot(shot)
            if self.terminal_reason:
                break

    def evaluate_terminal(self, ship: Ship) -> None:
        if ship.hull <= 0.0 and ship.alive:
            ship.alive = False
            self.terminal_reason = f"{ship.id}-destroyed"
        elif (
            ship.subsystems.propulsion <= 0.16
            and ship.subsystems.weapons <= 0.16
            and ship.alive
        ):
            ship.mission_killed = True
            ship.alive = False
            self.terminal_reason = f"{ship.id}-mission-killed"
        if self.terminal_reason and self.terminal_time is None:
            self.terminal_time = self.time
            self.event("terminal", reason=self.terminal_reason)

    def check_warp_completion(self) -> None:
        if self.terminal_reason:
            return
        for ship in self.ships.values():
            if ship.alive and ship.warp_state == "spooling" and ship.warp_progress >= 1.0 - EPS:
                ship.warp_state = "translated"
                ship.escaped = True
                ship.alive = False
                self.warp_completions += 1
                self.terminal_reason = f"{ship.id}-warped-out"
                self.terminal_time = self.time
                self.event("warp-complete", ship=ship.id, warpHealth=round(ship.subsystems.warp, 3))
                self.event("terminal", reason=self.terminal_reason)
                return

    def control_tick(self) -> None:
        if self.live_action_driver is not None:
            for ship in self.ships.values():
                if not ship.alive:
                    continue
                completed = self.live_action_driver.harvest_completed(self, ship)
                if completed is not None:
                    self.live_current_actions[ship.id] = completed
                    self.live_next_thought_at[ship.id] = self.time + self.live_action_interval_seconds
                if (
                    not self.live_action_driver.thought_in_flight(ship.id)
                    and self.time + EPS >= self.live_next_thought_at[ship.id]
                ):
                    trigger = "initial" if ship.id not in self.live_current_actions else "periodic"
                    self.live_action_driver.launch_thought(self, ship, trigger)
        for ship in self.ships.values():
            if ship.alive:
                if self.live_action_driver is None:
                    action = self.choose_action(ship)
                else:
                    # Until the first live thought publishes, preserve a physically valid fallback action.
                    action = self.live_current_actions.get(ship.id) or self.choose_action(ship)
                self.apply_action(ship, action)
        for ship in self.ships.values():
            if ship.alive:
                self.attempt_fire(ship)

    def _advance_one_physics_step(self, dt: float) -> None:
        self.update_passive_state(dt)
        self.update_tracking(dt)
        self.integrate(dt)
        self.time = round(self.time + dt, 10)
        self.process_arrivals()
        self.check_warp_completion()

    def _run_accelerated(self) -> None:
        next_control = 0.0
        while self.time <= self.duration + EPS and not self.terminal_reason:
            if self.time + EPS >= next_control:
                self.control_tick()
                next_control += CONTROL_DT
            self._advance_one_physics_step(PHYSICS_DT)

    def _run_live_realtime(self) -> None:
        # Match Battle 2's proven architecture: wall time is authoritative in live mode,
        # cognition runs on Futures, and physics never waits for inference.
        self.live_start_wall = time.perf_counter()
        next_control = 0.0
        while not self.terminal_reason:
            elapsed = min(self.duration, time.perf_counter() - self.live_start_wall)
            while self.time + PHYSICS_DT <= elapsed + EPS and not self.terminal_reason:
                if self.time + EPS >= next_control:
                    self.control_tick()
                    next_control += CONTROL_DT
                self._advance_one_physics_step(PHYSICS_DT)
            if self.terminal_reason or elapsed >= self.duration - EPS:
                break
            time.sleep(0.005)
        # Publish any result that completed exactly at the terminal/duration boundary for diagnostics only.
        if not self.terminal_reason:
            while self.time + PHYSICS_DT <= self.duration + EPS:
                if self.time + EPS >= next_control:
                    self.control_tick()
                    next_control += CONTROL_DT
                self._advance_one_physics_step(PHYSICS_DT)

    def run(self) -> dict[str, Any]:
        try:
            if self.live_action_driver is None:
                self._run_accelerated()
            else:
                self._run_live_realtime()
        finally:
            if self.live_action_driver is not None:
                # Harvest already-completed thoughts without blocking; never wait for cognition after battle end.
                for ship in self.ships.values():
                    if self.live_action_driver.thought_in_flight(ship.id):
                        future = self.live_action_driver.thoughts.get(ship.id)
                        if future is not None and future.done():
                            self.live_action_driver.harvest_completed(self, ship)

        checks = self.build_checks()
        ok = all(checks.values())
        return {
            "ok": ok,
            "schema": "game.spaceCaptainFleshedCombatSmoke.v1",
            "seed": self.seed,
            "durationBudgetSeconds": self.duration,
            "finishedAtSeconds": round(self.terminal_time if self.terminal_time is not None else min(self.time, self.duration), 3),
            "terminalReason": self.terminal_reason or "duration-expired",
            "checks": checks,
            "metrics": {
                "eventCount": len(self.events),
                "shotCount": self.shot_seq,
                "subsystemHits": self.subsystem_hits,
                "shieldAbsorptions": self.shield_absorptions,
                "ecmTicks": self.ecm_ticks,
                "heatBlocks": self.heat_blocks,
                "trackGatedShotOpportunities": self.track_gated_shots,
                "weaponModesFired": sorted(self.weapon_modes_fired),
                "uniqueActionPackages": len(self.unique_actions),
                "warpInitiations": self.warp_initiations,
                "warpCancellations": self.warp_cancellations,
                "warpCompletions": self.warp_completions,
                "warpSubsystemHits": self.warp_subsystem_hits,
            },
            "finalShips": {ship_id: self.ship_snapshot(ship) for ship_id, ship in self.ships.items()},
            "events": self.events,
            "liveAction": (self.live_action_driver.summary() if self.live_action_driver is not None else {"enabled": False}),
            "featureProbes": self.feature_probes(),
        }

    def feature_probes(self) -> dict[str, Any]:
        healthy = Ship("probe", "Probe", 0.0, 0.0)
        damaged_propulsion = Ship("probe", "Probe", 0.0, 0.0, subsystems=Subsystems(propulsion=0.25))
        damaged_sensors = Ship("probe", "Probe", 0.0, 0.0, subsystems=Subsystems(sensors=0.25))
        pristine_shield = Ship("probe", "Probe", 0.0, 0.0)
        damaged_shield = Ship("probe", "Probe", 0.0, 0.0, subsystems=Subsystems(defense=0.25))
        healthy_warp = Ship("probe", "Probe", 0.0, 0.0)
        damaged_warp = Ship("probe", "Probe", 0.0, 0.0, subsystems=Subsystems(warp=0.25))
        disabled_warp = Ship("probe", "Probe", 0.0, 0.0, subsystems=Subsystems(warp=0.05))
        spooling_warp = Ship("probe", "Probe", 0.0, 0.0, warp_state="spooling", warp_progress=0.42)

        heavy = self.weapon_specs("heavy")
        pulse = self.weapon_specs("pulse")
        heat = 0.0
        heavy_fires_before_block = 0
        while heat + heavy["heat"] <= 1.0 + EPS:
            heat += heavy["heat"]
            heavy_fires_before_block += 1
        heavy_heat_blocked = heat + heavy["heat"] > 1.0

        # Controlled hit-quality probe: identical geometry, only track quality differs.
        def nominal_hit_probability(track: float, mode: str, range_m: float) -> float:
            spec = self.weapon_specs(mode)
            envelope = self.envelope_factor(mode, range_m)
            return self.clamp01(0.08 + 0.80 * track * envelope - 0.08)

        mission_kill_probe = Ship(
            "probe",
            "Probe",
            0.0,
            0.0,
            subsystems=Subsystems(propulsion=0.15, weapons=0.15, sensors=0.9, defense=0.9),
        )
        destroyed_probe = Ship("probe", "Probe", 0.0, 0.0, hull=0.0)
        escape_probe = Ship("probe", "Probe", 0.0, 0.0, hull=0.25)

        warp_probe = Ship("warp-probe", "Warp Probe", 0.0, 0.0)
        warp_probe.warp_state = "spooling"
        warp_probe.warp_progress = 0.40
        warp_probe_choices = self.available_warp_choices(warp_probe)
        warp_probe_cancelled = "cancel" in warp_probe_choices
        if warp_probe_cancelled:
            warp_probe.warp_state = "idle"
            warp_probe.warp_progress = 0.0
        warp_probe.warp_state = "spooling"
        warp_probe.warp_progress = 0.0
        healthy_probe_rate = self.warp_spool_rate(warp_probe)
        damaged_probe = Ship("warp-damaged-probe", "Warp Damaged Probe", 0.0, 0.0, subsystems=Subsystems(warp=0.25))
        damaged_probe.warp_state = "spooling"
        damaged_probe_rate = self.warp_spool_rate(damaged_probe)
        warp_probe_completed = healthy_probe_rate > 0.0 and (healthy_probe_rate * self.warp_spool_seconds(warp_probe)) >= 1.0 - EPS

        return {
            "healthyMaxThrust": round(self.max_thrust(healthy), 3),
            "damagedPropulsionMaxThrust": round(self.max_thrust(damaged_propulsion), 3),
            "healthyTrackGainPerSecond": round(self.sensor_track_gain(healthy), 3),
            "damagedSensorTrackGainPerSecond": round(self.sensor_track_gain(damaged_sensors), 3),
            "healthyShieldEfficiency": round(self.shield_efficiency(pristine_shield), 3),
            "damagedShieldEfficiency": round(self.shield_efficiency(damaged_shield), 3),
            "pulseEnvelopeAt1200m": round(self.envelope_factor("pulse", 1200.0), 3),
            "pulseEnvelopeAt3500m": round(self.envelope_factor("pulse", 3500.0), 3),
            "heavyEnvelopeAt1200m": round(self.envelope_factor("heavy", 1200.0), 3),
            "heavyEnvelopeAt3000m": round(self.envelope_factor("heavy", 3000.0), 3),
            "lowTrackHeavyHitProbability": round(nominal_hit_probability(0.25, "heavy", 2450.0), 3),
            "highTrackHeavyHitProbability": round(nominal_hit_probability(0.85, "heavy", 2450.0), 3),
            "heavyFiresBeforeHeatBlockWithoutCooling": heavy_fires_before_block,
            "heavyHeatBlockOccurs": heavy_heat_blocked,
            "missionKillPredicate": mission_kill_probe.subsystems.propulsion <= 0.16 and mission_kill_probe.subsystems.weapons <= 0.16,
            "destroyedPredicate": destroyed_probe.hull <= 0.0,
            "escapePredicate": escape_probe.hull < 0.30 and 7000.0 > 6500.0,
            "healthyWarpSpoolSeconds": round(self.warp_spool_seconds(healthy_warp), 3),
            "damagedWarpSpoolSeconds": round(self.warp_spool_seconds(damaged_warp), 3),
            "disabledWarpSpoolRate": round(self.warp_spool_rate(disabled_warp), 6),
            "idleWarpChoices": self.available_warp_choices(healthy_warp),
            "spoolingWarpChoices": self.available_warp_choices(spooling_warp),
            "warpCancelPreservesPreTranslationState": spooling_warp.warp_state == "spooling" and spooling_warp.warp_progress < 1.0,
            "warpProbeCanCancel": warp_probe_cancelled and warp_probe.warp_state == "spooling",
            "warpProbeDamageReducesRate": damaged_probe_rate < healthy_probe_rate,
            "warpProbeCanComplete": warp_probe_completed,
            "pulseHeatPerShot": pulse["heat"],
            "heavyHeatPerShot": heavy["heat"],
        }

    def build_checks(self) -> dict[str, bool]:
        probes = self.feature_probes()
        final = list(self.ships.values())
        return {
            "propulsionDamageShrinksReachableAcceleration": probes["damagedPropulsionMaxThrust"] < probes["healthyMaxThrust"],
            "sensorDamageSlowsTrackAcquisition": probes["damagedSensorTrackGainPerSecond"] < probes["healthyTrackGainPerSecond"],
            "defenseDamageWeakensShield": probes["damagedShieldEfficiency"] < probes["healthyShieldEfficiency"],
            "weaponModesHaveDifferentUsefulEnvelopes": (
                probes["pulseEnvelopeAt1200m"] > 0.0
                and probes["pulseEnvelopeAt3500m"] == 0.0
                and probes["heavyEnvelopeAt3000m"] > 0.0
            ),
            "weaponHeatCanBlockRepeatedHeavyFire": (
                probes["heavyFiresBeforeHeatBlockWithoutCooling"] >= 2
                and probes["heavyHeatBlockOccurs"] is True
                and probes["heavyHeatPerShot"] > probes["pulseHeatPerShot"]
            ),
            "trackQualityChangesHitProbability": probes["highTrackHeavyHitProbability"] > probes["lowTrackHeavyHitProbability"],
            "terminalStatePredicatesExist": (
                probes["missionKillPredicate"]
                and probes["destroyedPredicate"]
                and probes["escapePredicate"]
            ),
            "bothWeaponModesActuallyFire": self.weapon_modes_fired == {"pulse", "heavy"},
            "subsystemDamageActuallyOccurs": self.subsystem_hits >= 1,
            "activeDefenseActuallyOperates": (self.shield_absorptions + self.ecm_ticks) >= 1,
            "battleProducesDeniedFireOpportunities": (self.heat_blocks + self.track_gated_shots) >= 1,
            "captainsUseMultipleActionPackages": len(self.unique_actions) >= 6,
            "warpDamageSlowsSpool": probes["damagedWarpSpoolSeconds"] > probes["healthyWarpSpoolSeconds"],
            "severeWarpDamageCanStopSpool": probes["disabledWarpSpoolRate"] == 0.0,
            "warpChoiceAppearsAndCanBeCancelled": (
                "initiate" in probes["idleWarpChoices"]
                and "cancel" in probes["spoolingWarpChoices"]
                and "continue" in probes["spoolingWarpChoices"]
            ),
            "warpStateMachineProbePasses": (
                probes["warpProbeCanCancel"]
                and probes["warpProbeDamageReducesRate"]
                and probes["warpProbeCanComplete"]
            ),
            "warpAppearsInIntegratedCombat": self.warp_initiations >= 1,
            "damageChangesCombatState": any(
                min(
                    ship.subsystems.propulsion,
                    ship.subsystems.weapons,
                    ship.subsystems.sensors,
                    ship.subsystems.defense,
                    ship.subsystems.warp,
                ) < 0.95
                for ship in final
            ),
        }

    @staticmethod
    def ship_snapshot(ship: Ship) -> dict[str, Any]:
        return {
            "hull": round(ship.hull, 3),
            "positionM": [round(ship.x, 1), round(ship.y, 1)],
            "velocityMps": [round(ship.vx, 1), round(ship.vy, 1)],
            "speedMps": round(math.hypot(ship.vx, ship.vy), 1),
            "subsystems": {k: round(v, 3) for k, v in asdict(ship.subsystems).items()},
            "weaponHeat": round(ship.weapon_heat, 3),
            "defenseCharge": round(ship.defense_charge, 3),
            "defenseMode": ship.defense_mode,
            "trackQuality": round(ship.track_quality, 3),
            "warpState": ship.warp_state,
            "warpProgress": round(ship.warp_progress, 3),
            "warpEstimatedSpoolSeconds": round(CombatSmoke.warp_spool_seconds(ship), 3) if CombatSmoke.warp_spool_rate(ship) > EPS else None,
            "alive": ship.alive,
            "missionKilled": ship.mission_killed,
            "escaped": ship.escaped,
        }


def narrate(result: dict[str, Any]) -> str:
    lines = [
        f"Fleshed combat smoke — seed {result['seed']}.",
        f"The engagement ended at {result['finishedAtSeconds']} seconds: {result['terminalReason']}.",
        "",
    ]
    interesting = {
        "decision",
        "fire",
        "hit",
        "weapon-overheat",
        "weapon-recovered",
        "warp-initiated",
        "warp-cancelled",
        "warp-damaged",
        "warp-complete",
        "terminal",
    }
    for event in result["events"]:
        if event["kind"] not in interesting:
            continue
        t = event["t"]
        kind = event["kind"]
        if kind == "decision":
            lines.append(
                f"At {t:g}s, {event['ship']} chose {event['maneuver']}, "
                f"weapon={event['weapon']}, defense={event['defense']}, warp={event['warpAction']} "
                f"(track {event['track']:.2f}, heat {event['heat']:.2f}, warp {event['warpProgress']:.2f})."
            )
        elif kind == "fire":
            lines.append(
                f"At {t:g}s, {event['ship']} fired {event['weapon']} at {event['target']} "
                f"from {event['range']:.0f}m with p(hit)={event['hitProbability']:.2f}."
            )
        elif kind == "hit":
            shield = f", shield absorbed {event['shieldAbsorbed']:.3f}" if event["shieldAbsorbed"] > 0 else ""
            lines.append(
                f"At {t:g}s, {event['attacker']}'s {event['weapon']} hit {event['target']}: "
                f"hull -{event['hullDamage']:.3f}{shield}; {event['subsystem']} "
                f"{event['subsystemBefore']:.2f}->{event['subsystemAfter']:.2f}."
            )
        elif kind == "weapon-overheat":
            lines.append(f"At {t:g}s, {event['ship']}'s weapons overheated and blocked {event['requested']} fire.")
        elif kind == "weapon-recovered":
            lines.append(f"At {t:g}s, {event['ship']}'s weapons cooled enough to fire again.")
        elif kind == "warp-initiated":
            lines.append(f"At {t:g}s, {event['ship']} initiated warp spool; estimated translation in {event['estimatedSpoolSeconds']:.1f}s at current drive health.")
        elif kind == "warp-cancelled":
            lines.append(f"At {t:g}s, {event['ship']} cancelled warp at {event['progress']:.0%} spool.")
        elif kind == "warp-damaged":
            suffix = "offline" if event['estimatedSpoolSeconds'] is None else f"new full-spool estimate {event['estimatedSpoolSeconds']:.1f}s"
            lines.append(f"At {t:g}s, {event['ship']}'s warp drive was damaged {event['before']:.2f}->{event['after']:.2f}; {suffix}.")
        elif kind == "warp-complete":
            lines.append(f"At {t:g}s, {event['ship']} completed warp translation and left the engagement.")
        elif kind == "terminal":
            lines.append(f"At {t:g}s, combat became terminal: {event['reason']}.")
    lines.append("")
    for ship_id, ship in result["finalShips"].items():
        subs = ship["subsystems"]
        lines.append(
            f"{ship_id}: hull={ship['hull']:.3f}; propulsion={subs['propulsion']:.3f}; "
            f"weapons={subs['weapons']:.3f}; sensors={subs['sensors']:.3f}; "
            f"defense={subs['defense']:.3f}; warp={subs['warp']:.3f}; track={ship['trackQuality']:.3f}; "
            f"warpState={ship['warpState']} progress={ship['warpProgress']:.3f}."
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone richer-space-combat smoke: subsystem damage, terminal states, "
            "weapon envelopes/heat, active shield+ECM defense, sensor track quality, interruptible warp escape, "
            "and optional live CLEF captain action selection."
        )
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--duration-seconds", type=float, default=45.0)
    parser.add_argument("--narrate", action="store_true")
    parser.add_argument("--events", action="store_true", help="Keep the full event stream in JSON output.")
    parser.add_argument(
        "--include-call-snapshots",
        action="store_true",
        help=(
            "Persist each completed live CLEF thought's launch observation, candidates, "
            "pairwise questions/answers, scores, winner, and timing under liveAction.callSnapshots."
        ),
    )
    parser.add_argument("--live-action", action="store_true", help="Use the managed NanoJev CLEF service to choose combat actions.")
    parser.add_argument("--live-action-interval-seconds", type=float, default=2.0, help="Minimum simulated seconds after a published CLEF action before that captain may launch its next asynchronous thought.")
    parser.add_argument("--live-action-backend-url", default="", help="Reuse an already-running Space Captain protocol adapter instead of launching one.")
    parser.add_argument("--nanojev-service-url", default="http://127.0.0.1:9765", help="Managed NanoJev lifecycle-service URL used by the Space Captain protocol adapter.")
    parser.add_argument("--run-dir", default=str(DEFAULT_LIVE_RUN))
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--nanojev-python", default="")
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    if args.duration_seconds <= 0:
        raise SystemExit("--duration-seconds must be positive")
    if args.live_action_interval_seconds <= 0:
        raise SystemExit("--live-action-interval-seconds must be positive")

    backend = None
    log_path: Path | None = None
    try:
        live_driver = None
        if args.live_action:
            if args.live_action_backend_url:
                evaluate_url = str(args.live_action_backend_url).rstrip("/")
                if evaluate_url.endswith("/captain/evaluate"):
                    health_url = evaluate_url[: -len("/captain/evaluate")] + "/health"
                else:
                    health_url = evaluate_url + "/health"
                    evaluate_url = evaluate_url + "/captain/evaluate"
                health = _get_json(health_url, timeout=5.0)
            else:
                nanojev_python = (
                    Path(args.nanojev_python).expanduser().resolve(strict=True)
                    if args.nanojev_python
                    else _default_nanojev_python().resolve(strict=True)
                )
                runtime_dir = ROOT / "runtime" / "captain_live_clef"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                port = _free_port()
                log_path = runtime_dir / f"fleshed-live-action-{int(time.time())}-{port}.log"
                command = [
                    str(nanojev_python), str(LIVE_BACKEND),
                    "--service-url", str(args.nanojev_service_url),
                    "--port", str(port),
                ]
                log_handle = log_path.open("w", encoding="utf-8")
                backend = subprocess.Popen(
                    command, cwd=ROOT, stdout=log_handle, stderr=subprocess.STDOUT, text=True
                )
                log_handle.close()
                health_url = f"http://127.0.0.1:{port}/health"
                evaluate_url = f"http://127.0.0.1:{port}/captain/evaluate"
                health = None
                started = time.monotonic()
                while time.monotonic() - started < float(args.startup_timeout_seconds):
                    code = backend.poll()
                    if code is not None:
                        tail = log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
                        raise RuntimeError(f"live CLEF backend exited during startup with code {code}: {tail}")
                    try:
                        candidate = _get_json(health_url)
                        if candidate.get("ok") is True:
                            health = candidate
                            break
                    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                        pass
                    time.sleep(0.25)
                if not health or health.get("ok") is not True:
                    raise RuntimeError(f"live CLEF backend did not become healthy; log={log_path}")
            if health.get("ok") is not True:
                raise RuntimeError(f"live CLEF backend health failed: {health}")
            live_driver = LiveActionDriver(
                evaluate_url,
                health,
                request_timeout_seconds=float(args.request_timeout_seconds),
                include_call_snapshots=bool(args.include_call_snapshots),
            )

        result = CombatSmoke(
            args.seed,
            args.duration_seconds,
            live_action_driver=live_driver,
            live_action_interval_seconds=float(args.live_action_interval_seconds),
        ).run()
        if log_path is not None:
            result["liveAction"]["backendLog"] = str(log_path)
        if args.narrate:
            header = ""
            if result.get("liveAction", {}).get("enabled"):
                live = result["liveAction"]
                header = (
                    f"Live action: {live.get('provider')} checkpoint={live.get('checkpointId')} "
                    f"calls={live.get('callCount')} judgments={live.get('independentJudgmentCount')}.\n"
                )
            print(header + narrate(result))
        else:
            if not args.events:
                result = dict(result)
                result.pop("events", None)
            print(json.dumps(result, indent=2))
        return 0 if result.get("ok") is True else 1
    finally:
        if 'live_driver' in locals() and live_driver is not None:
            live_driver.close()
        if backend is not None:
            backend.terminate()
            try:
                backend.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait(timeout=5.0)


if __name__ == "__main__":
    raise SystemExit(main())
