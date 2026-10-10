#!/usr/bin/env python3
"""Prepare NanoJev through the game service, then verify one *real* bridge-captain inference.

This smoke talks to both existing services:
  - Main Computer (default :8765): /api/applications/game/tactical-ai/{status,prepare,captain/decide}
  - NanoJev manager (from game status, default :9765):
    /control/status, /api/health, /api/evaluate-batch

The game's /prepare endpoint owns model wakeup, checkpoint validation, adapter
startup and its performance gate. This smoke only orchestrates and observes it;
it does not start a separate tactical battle, reset the model, or issue helm orders.
Use --no-prepare to require the model to have been prepared already.
Default: test a *real* NanoJev batch directly through port 9765, without
requiring the Main Computer captain route. Use --game-route to additionally
exercise the game's captain/decide endpoint on port 8765 instead. This is a
separate integration check; direct-model success does not prove game control.
Use --probe-route-only to validate the game route without warming the model.
"""
from __future__ import annotations

import argparse
import itertools
import math
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


GAME_API = "/api/applications/game/tactical-ai"
PREPARING = {"starting-ai", "loading-model", "warming-ai", "restarting-ai"}
BUSY = {"running", "priming-battle", "stopping-battle"}


class EndpointError(RuntimeError):
    pass


def read_json(url: str, data: dict | None = None, timeout: float = 15.0) -> dict:
    body = json.dumps(data, allow_nan=False).encode("utf-8") if data is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = str(parsed.get("error") or parsed.get("lastError") or detail)
        except (ValueError, AttributeError):
            pass
        raise EndpointError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EndpointError(f"Cannot reach {url}: {type(exc).__name__}: {exc}") from exc
    except (ValueError, UnicodeError) as exc:
        raise EndpointError(f"Invalid JSON from {url}: {exc}") from exc
    if not isinstance(result, dict):
        raise EndpointError(f"Expected JSON object from {url}")
    return result


def probe_captain_route(base: str, timeout: float) -> dict:
    """Prove the *running* viewport recognizes this endpoint without inference.

    The intentionally invalid schema is rejected at the very first service
    validation check. A current viewport replies HTTP 409 with
    BRIDGE_CAPTAIN_OBSERVATION_SCHEMA_REQUIRED; a stale viewport falls through
    its routing table to HTTP 400 "Unknown Tactical AI route.". No model is
    loaded, no game state changes, and no captain order is issued by this probe.
    """
    url = base + "/captain/decide"
    payload = json.dumps({"schema": "game.bridgeCaptainRoutePreflight.v1"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EndpointError(f"Captain route preflight could not reach {url}: {exc}") from exc
    try:
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict):
            raise ValueError("non-object response")
    except (ValueError, UnicodeError) as exc:
        raise EndpointError(f"Captain route preflight returned invalid JSON from {url} (HTTP {status}): {exc}") from exc
    error = str(result.get("error") or "")
    return {"ok": status == 409 and "BRIDGE_CAPTAIN_OBSERVATION_SCHEMA_REQUIRED" in error,
            "httpStatus": status, "error": error,
            "check": "invalid-schema/no-inference", "url": url}


def manager_summary(manager: dict | None) -> dict:
    if not isinstance(manager, dict):
        return {"ok": False, "error": "no manager response"}
    keys = (
        "ok", "runtime_state", "phase", "container_state", "running",
        "backend_ready", "model_loaded", "checkpoint_validated",
        "backend_error", "container_error", "last_error", "error",
    )
    return {key: manager[key] for key in keys if key in manager}


def manager_ready(manager: dict) -> bool:
    return all(manager.get(key) is True for key in (
        "ok", "running", "backend_ready", "model_loaded", "checkpoint_validated"
    ))


def service_ready(status: dict) -> bool:
    return (
        status.get("ok") is True
        and status.get("modelLoaded") is True
        and (status.get("performance") or {}).get("ready") is True
        and (status.get("adapter") or {}).get("running") is True
        and bool((status.get("adapter") or {}).get("checkpointSha256"))
    )


def observation() -> dict:
    # Deterministic input, real provider response. Never a test-injected model answer.
    return {
        "schema": "game.bridgeCaptainObservation.v1",
        "captainId": "captain.beta", "shipId": "ship.beta",
        "simulationSeconds": 0, "rangeM": 2667.8838055657516,
        "radialVelocityMps": -85.0, "targetHullPercent": 100,
        "relativePositionM": [2600, 598], "relativeVelocityMps": [-85, -12],
        "transporterMaxRangeM": 2500,
        "mission": "pursue-main-ship-and-seek-boarding-range",
    }


def direct_nanojev_captain_decision(manager_url: str, source_observation: dict, expected: dict,
                                   timeout: float, *, counterbalance: bool = False) -> dict:
    """Native NanoJev inference: the manager proxies /api/evaluate-batch.

    Mirror the live bridge's state-dependent helm and boarding intents. This verifies the real
    model and checkpoint without relying on the *different* app route. It does
    not issue a helm command or claim gameplay integration was verified.
    """
    health = read_json(manager_url + "/api/health", timeout=timeout)
    if health.get("ready") is not True or health.get("model_family") != "nanojev-clef":
        raise EndpointError("NANOJEV_MODEL_NOT_READY: /api/health did not confirm a ready CLEF model")
    checkpoint_id = str(health.get("release_name") or health.get("checkpoint_revision_resolved") or
                        health.get("checkpoint_selector") or "")
    checkpoint_sha = str(health.get("release_manifest_sha256") or
                         health.get("checkpoint_revision_resolved") or "")
    if not checkpoint_id or not checkpoint_sha or (expected.get("checkpointId") != checkpoint_id or
                                                   expected.get("checkpointSha256") != checkpoint_sha):
        raise EndpointError("NANOJEV_CHECKPOINT_MISMATCH: manager model differs from the prepared captain adapter")

    distance = float(source_observation["rangeM"])
    radial = float(source_observation["radialVelocityMps"])
    max_range = float(source_observation["transporterMaxRangeM"])
    if not all(math.isfinite(v) for v in (distance, radial, max_range)):
        raise EndpointError("Invalid physics values in captain observation")
    stand_off = max(200.0, max_range - 450.0)
    outer = max(200.0, max_range - 100.0)
    boarding = source_observation.get("boarding") or {"phase": "idle", "boarders": "aboard"}
    phase = boarding.get("phase", "idle")
    if phase not in ("idle", "deploying", "deployed", "recalling", "recovered", "abandoning", "abandoned"):
        raise EndpointError("NANOJEV_BOARDING_PHASE_INVALID")
    relative_velocity = source_observation.get("relativeVelocityMps") or [0, 0]
    relative_speed = math.hypot(*relative_velocity)
    choices = [
        ("helm", "approach", None, "Pursue the main ship and close separation for eventual boarding"),
        ("helm", "hold", stand_off, f"Match relative velocity and hold {stand_off:.1f} m"),
        ("helm", "hold", outer, f"Match relative velocity and hold {outer:.1f} m"),
    ]
    if phase in ("idle", "recovered", "abandoned"):
        choices.append(("helm", "withdraw", None, "Withdraw and increase separation from main ship"))
    choices.append(("helm", "coast", None, "Cease commanded thrust, retaining momentum"))
    if phase == "idle" and distance <= max_range and relative_speed <= 5:
        choices.append(("boarding", "initiate", None,
                        "Initiate boarding commitment: deployment takes 30 simulation seconds; hold range until completed"))
    elif phase == "deployed":
        choices.extend((
            ("boarding", "recall", None,
             "Recall deployed boarders before withdrawal; recovery takes 24 seconds and requires maintained range"),
            ("boarding", "abandon", None,
             "Abandon deployed boarders to permit withdrawal; abandonment requires 12 seconds"),
        ))
    pairs = list(itertools.combinations(range(len(choices)), 2))
    # The original one-way comparisons always put low-index options first,
    # creating an index/position confound. For live cognition diagnostics,
    # evaluate each pair in BOTH directions and require order-invariant votes.
    comparison_rows = [
        (f"bridge-beta-{i}-ab", a, b, i, "ab")
        for i, (a, b) in enumerate(pairs)
    ] if counterbalance else [
        (f"bridge-beta-{i}", a, b, i, "ab")
        for i, (a, b) in enumerate(pairs)
    ]
    if counterbalance:
        comparison_rows.extend(
            (f"bridge-beta-{i}-ba", b, a, i, "ba")
            for i, (a, b) in enumerate(pairs)
        )
    request = {
        "schema": "nanojev.pairwise-batch.v1",
        "shared_context": {"text": (
            "Acting captain=beta, enemy raider ship.beta. Mission: pursue the main ship and seek boarding range. "
            f"Real bridge combat t={source_observation['simulationSeconds']:.2f}s; "
            f"observed separation={distance:.2f}m; radial closing velocity={-radial:.2f}m/s; "
            f"raider hull={source_observation['targetHullPercent']:.1f}%; "
            f"relative position={source_observation['relativePositionM']}m; "
            f"relative velocity={source_observation['relativeVelocityMps']}m/s; "
            f"personnel transporter max range={max_range:.1f}m; "
            f"boarding range currently eligible={distance <= max_range and relative_speed <= 5}. "
            f"boarding phase={phase}, boarders={boarding.get('boarders', 'aboard')}; "
            f"completion time={boarding.get('completeAtSeconds')}. "
            "Choose the most appropriate authorized helm or boarding commitment from each pair. "
            "Deployment takes 30s, recall 24s, abandonment 12s; cannot withdraw with boarders committed. "
            "No actual boarding combat or transporter execution is simulated."
        )},
        "execution": {"evidence_mode": "auto"},
        "questions": [
            {"id": question_id,
             "prompt": "Which authorized maneuver better advances the raider captain's mission in this physical situation?",
             "candidates": [
                 {"id": f"beta-c{first}", "text": choices[first][3]},
                 {"id": f"beta-c{second}", "text": choices[second][3]},
             ]}
            for question_id, first, second, _, _ in comparison_rows
        ],
    }
    response = read_json(manager_url + "/api/evaluate-batch", request, timeout=timeout)
    if response.get("schema") != "nanojev.pairwise-batch-result.v1":
        raise EndpointError("NANOJEV_BATCH_SCHEMA_MISMATCH")
    model = response.get("model") or {}
    model_id = str(model.get("release_name") or model.get("revision") or "")
    model_sha = str(model.get("release_manifest_sha256") or model.get("revision") or "")
    if model_id != checkpoint_id or model_sha != checkpoint_sha:
        raise EndpointError("NANOJEV_RESPONSE_CHECKPOINT_MISMATCH: model changed during inference")
    rows = response.get("results")
    if not isinstance(rows, list) or len(rows) != len(comparison_rows):
        raise EndpointError("NANOJEV_BATCH_RESULT_COUNT_MISMATCH")
    scores = [0.0] * len(choices)
    votes: dict[int, dict[str, dict]] = {}
    first_slot_votes = 0
    for row, (qid, first, second, pair_id, orientation) in zip(rows, comparison_rows):
        if not isinstance(row, dict) or row.get("id") != qid:
            raise EndpointError("NANOJEV_BATCH_QUESTION_ID_MISMATCH")
        choice = row.get("choice")
        if choice not in (f"beta-c{first}", f"beta-c{second}"):
            raise EndpointError("NANOJEV_BATCH_INVALID_CHOICE")
        margin = row.get("margin", 0)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or not math.isfinite(float(margin)) or abs(float(margin)) > 1:
            raise EndpointError("NANOJEV_BATCH_INVALID_MARGIN")
        winner_index = int(choice.removeprefix("beta-c"))
        first_slot_votes += int(winner_index == first)
        votes.setdefault(pair_id, {})[orientation] = {
            "winner": winner_index, "margin": float(margin),
            "firstCandidate": first, "secondCandidate": second,
        }
    consistent_pairs = 0
    position_disagreements = 0
    pair_evidence = []
    for i, (a, b) in enumerate(pairs):
        ab = votes[i]["ab"]
        ba = votes[i].get("ba")
        if ba is None:
            scores[ab["winner"]] += 1 + ab["margin"]
            consistent_pairs += 1
        elif ab["winner"] == ba["winner"]:
            scores[ab["winner"]] += 1 + (ab["margin"] + ba["margin"]) / 2
            consistent_pairs += 1
        else:
            position_disagreements += 1
        pair_evidence.append({"pair": [f"beta-c{a}", f"beta-c{b}"],
            "forwardChoice": f"beta-c{ab['winner']}",
            "reverseChoice": f"beta-c{ba['winner']}" if ba else None,
            "orderInvariant": ba is None or ab["winner"] == ba["winner"]})
    ranked = sorted(range(len(choices)), key=lambda i: (-scores[i], i))
    top = ranked[0]
    # Do not turn a position-biased batch into an apparently authoritative
    # model order. Require at least half of comparisons to agree when reversed,
    # and a unique evidence-backed top candidate.
    minimum_consistent = math.ceil(len(pairs) / 2) if counterbalance else 1
    trusted = (consistent_pairs >= minimum_consistent
               and scores[top] > 0
               and (len(ranked) == 1 or scores[top] > scores[ranked[1]] + 1e-9))
    action_type, action, range_m, _ = choices[top] if trusted else (None, None, None, None)
    bias = {
        "counterbalanced": counterbalance,
        "forwardAndReversePairs": len(pairs) if counterbalance else 0,
        "consistentPairs": consistent_pairs,
        "positionDisagreements": position_disagreements,
        "firstCandidateWinRate": first_slot_votes / len(comparison_rows),
        "minimumConsistentPairs": minimum_consistent,
        "pairEvidence": pair_evidence,
    }
    return {
        "ok": True, "schema": "game.bridgeNanoJevDirectInference.v1",
        "source": "nanojev-manager-direct-inference",
        "captainId": "captain.beta", "shipId": "ship.beta",
        "selectionTrusted": trusted,
        "diagnostics": bias,
        "actionType": action_type,
        "maneuver": action if action_type == "helm" else None,
        "boardingAction": action if action_type == "boarding" else None,
        "rangeM": range_m,
        "candidateScores": scores,
        "modelReceipt": {
            "checkpointId": checkpoint_id,
            "checkpointSha256": checkpoint_sha,
            "chosenCandidateId": f"beta-c{top}" if trusted else None,
            "modelLatencyMs": (response.get("metrics") or {}).get("model_latency_ms"),
            "questionCount": len(comparison_rows),
        },
        "gameplayOrderIssued": False,
    }


def run(args: argparse.Namespace) -> dict:
    base = args.base_url.rstrip("/") + GAME_API
    report: dict[str, Any] = {
        "schema": "game.bridgeNanoJevLiveEndpointSmoke.v1", "ok": False,
        "gameServiceUrl": base, "prepareRequested": False,
        "prepared": False, "events": [],
        "inferenceMode": "game-route" if getattr(args, "game_route", False) else "nanojev-manager-direct",
        "gameIntegrationVerified": False,
    }
    stage = "game-status"
    manager_url = args.manager_url
    last_status: dict = {}
    last_manager: dict = {}
    try:
        status = read_json(base + "/status", timeout=args.http_timeout_seconds)
        last_status = status
        reported_manager_url = str(status.get("managerUrl") or "http://127.0.0.1:9765").rstrip("/")
        manager_url = str(manager_url or reported_manager_url).rstrip("/")
        report["managerUrl"] = manager_url
        if args.manager_url and manager_url != reported_manager_url:
            report["managerUrlMismatch"] = (
                f"Game service uses {reported_manager_url}; probe override uses {manager_url}"
            )
        # The Main Computer application route and the NanoJev manager endpoint
        # are *different contracts*. Only test the application route when the
        # caller explicitly asks for it; direct model inference uses port 9765.
        if args.game_route or args.probe_route_only:
            stage = "captain-route-preflight"
            report["captainRoutePreflight"] = probe_captain_route(base, args.http_timeout_seconds)
            if not report["captainRoutePreflight"]["ok"]:
                # Preserve the exact HTTP status/body while explaining the likely
                # source-vs-process mismatch in the normal failure path.
                verify_captain_route_result = report["captainRoutePreflight"]
                error = verify_captain_route_result["error"]
                if "Unknown Tactical AI route" in error or "decide_bridge_captain" in error:
                    raise EndpointError(
                        "MAIN_COMPUTER_VIEWPORT_STALE: The running service on port 8765 "
                        "does not have the captain/decide handler in the supplied code. "
                        "Restart only the Main Computer viewport with "
                        "'.\\dev-control.ps1 restart -Mode local', then retry. "
                        "No NanoJev retraining or reset is required. "
                        f"HTTP {verify_captain_route_result['httpStatus']}: {error}"
                    )
                raise EndpointError(
                    "BRIDGE_CAPTAIN_ROUTE_PREFLIGHT_FAILED: expected HTTP 409 "
                    "BRIDGE_CAPTAIN_OBSERVATION_SCHEMA_REQUIRED; received "
                    f"HTTP {verify_captain_route_result['httpStatus']}: {error or '<no error>'}"
                )
            report["events"].append({"event": "captain-route-preflight", "status": "supported"})
            if getattr(args, "probe_route_only", False):
                report["routeOnly"] = True
                report["ok"] = True
                return report
        stage = "manager-status"
        try:
            last_manager = read_json(manager_url + "/control/status", timeout=args.http_timeout_seconds)
        except EndpointError as exc:
            # Prepare via the game service may still recover; preserve the cause.
            report["managerProbeError"] = str(exc)
        report["managerInitial"] = manager_summary(last_manager)
        stage = "prepare"
        phase = str(status.get("phase") or "unknown")
        if phase in BUSY and not service_ready(status):
            raise RuntimeError(f"Tactical AI is busy ({phase}); refusing to interrupt an active battle")
        if not service_ready(status):
            if args.no_prepare:
                raise RuntimeError(
                    f"Tactical AI not prepared (phase={phase}). Run again without --no-prepare to use the game service's /prepare API."
                )
            if phase not in PREPARING:
                config = {
                    "time_step_seconds": args.time_step_seconds,
                    "consecutive_passes": args.consecutive_passes,
                    "warmup_timeout_seconds": args.warmup_timeout_seconds,
                    "startup_timeout_seconds": args.startup_timeout_seconds,
                    "request_timeout_seconds": args.request_timeout_seconds,
                }
                reply = read_json(base + "/prepare", config, timeout=args.http_timeout_seconds)
                report["prepareRequested"] = True
                if reply.get("ok") is False:
                    raise RuntimeError(str(reply.get("lastError") or reply.get("error") or "Prepare rejected"))
                # The previous status may have phase=error from an earlier run.
                # /prepare clears that failure, so use its fresh response rather
                # than incorrectly failing on the old status before polling.
                status = reply
                last_status = reply
                report["events"].append({"event": "prepare-requested", "phase": reply.get("phase")})
            else:
                report["events"].append({"event": "existing-preparation", "phase": phase})

        stage = "wait-ready"
        deadline = time.monotonic() + args.ready_timeout_seconds
        last_phase = None
        while not service_ready(status):
            phase = str(status.get("phase") or "unknown")
            if phase != last_phase:
                report["events"].append({"event": "phase", "phase": phase})
                print(f"NanoJev: {phase}", file=sys.stderr, flush=True)
                last_phase = phase
            if phase == "error" or status.get("lastError"):
                raise RuntimeError(f"Tactical AI preparation failed: {status.get('lastError') or status.get('error') or phase}")
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Tactical AI did not become performance-ready within {args.ready_timeout_seconds:g}s; "
                    f"last phase={phase}, warmup passes={(status.get('performance') or {}).get('consecutivePasses')}, "
                    f"lastError={status.get('lastError') or ''}"
                )
            time.sleep(min(args.poll_seconds, max(0.0, deadline - time.monotonic())))
            status = read_json(base + "/status", timeout=args.http_timeout_seconds)
            last_status = status
        report["prepared"] = True
        stage = "verify-checkpoint"
        last_manager = read_json(manager_url + "/control/status", timeout=args.http_timeout_seconds)
        if not manager_ready(last_manager):
            raise RuntimeError(f"NanoJev manager does not confirm loaded/verified checkpoint: {manager_summary(last_manager)}")
        report["managerReady"] = manager_summary(last_manager)
        adapter = status.get("adapter") or {}
        report["adapterCheckpointId"] = adapter.get("checkpointId")
        stage = "captain-inference"
        if getattr(args, "game_route", False):
            decision = read_json(base + "/captain/decide", observation(), timeout=args.decision_timeout_seconds)
            receipt = decision.get("modelReceipt") or {}
            if not (
                decision.get("ok") is True
                and decision.get("source") == "nanojev-captain-v6"
                and decision.get("captainId") == "captain.beta"
                and decision.get("shipId") == "ship.beta"
                and bool(receipt.get("checkpointSha256"))
                and receipt.get("checkpointSha256") == adapter.get("checkpointSha256")
                and receipt.get("checkpointId") == adapter.get("checkpointId")
                and decision.get("maneuver") in ("approach", "hold", "withdraw", "coast")
            ):
                raise RuntimeError("Captain inference did not return an authenticated checkpoint-backed NanoJev decision")
            report["gameIntegrationVerified"] = True
        else:
            decision = direct_nanojev_captain_decision(
                manager_url, observation(), adapter, args.decision_timeout_seconds
            )
            report["directModelVerified"] = True
        report["decision"] = decision
        report["ok"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["failedStage"] = stage
    finally:
        if last_status:
            report["gameStatus"] = {
                "phase": last_status.get("phase"), "lastError": last_status.get("lastError"),
                "modelLoaded": last_status.get("modelLoaded"),
                "performance": {k: (last_status.get("performance") or {}).get(k) for k in (
                    "ready", "consecutivePasses", "requiredConsecutivePasses", "softResetCount",
                    "lastSoftResetReason", "timeStepSeconds",
                )},
                "adapter": {k: (last_status.get("adapter") or {}).get(k) for k in (
                    "running", "checkpointId", "checkpointSha256", "logPath"
                )},
            }
        if last_manager:
            report["managerFinal"] = manager_summary(last_manager)
    return report


def positive_float(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="Main Computer viewport service")
    parser.add_argument("--manager-url", default=None, help="NanoJev manager URL (defaults to managerUrl from game status)")
    parser.add_argument("--no-prepare", action="store_true", help="Only check an already-prepared model")
    parser.add_argument("--probe-route-only", action="store_true", help="Check only the Main Computer app route on port 8765")
    parser.add_argument("--game-route", action="store_true", help="Additionally require the app's captain/decide endpoint; default checks NanoJev directly on port 9765")
    parser.add_argument("--time-step-seconds", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--consecutive-passes", type=int, choices=range(1, 21), default=3)
    parser.add_argument("--warmup-timeout-seconds", type=positive_float, default=120.0)
    parser.add_argument("--startup-timeout-seconds", type=positive_float, default=180.0)
    parser.add_argument("--request-timeout-seconds", type=positive_float, default=300.0)
    parser.add_argument("--ready-timeout-seconds", type=positive_float, default=420.0)
    parser.add_argument("--decision-timeout-seconds", type=positive_float, default=30.0)
    parser.add_argument("--http-timeout-seconds", type=positive_float, default=10.0)
    parser.add_argument("--poll-seconds", type=positive_float, default=2.0)
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
