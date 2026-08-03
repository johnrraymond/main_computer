"""Read-only steady-state soak verification for the Mother mainnet.

The soak starts from canonical successful A/C steady-state continuation evidence.
It performs only Coolify GET observations.  Each observation binds the exact
steady-state Compose and required healthy Besu/guardian components on A and C.
Observation windows are separated by at least the guardian freshness horizon so
success across windows proves refreshed guardian cycles rather than one cached
health result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import math
import re
from pathlib import Path
from typing import Any

from . import atomic_files
from .canonical import canonical_json
from .coolify_state import _DEFAULT_MAX_RESPONSE_BYTES, _DEFAULT_OPENER, resolve_coolify_controller
from .deployment_post_admission_steady_state import (
    _GUARDIAN_HEALTH_FRESHNESS_SECONDS,
    _TARGETS,
    _binding,
    _canonical_under,
    _contains_sensitive,
    _ensure_root,
    _mapping,
    _parse_utc,
    _relative,
    _resolve,
    _runtime_target_observation,
    _safe_message,
    _selection,
    _timestamp,
)
from .deployment_post_admission_steady_state_continuation import (
    _EVIDENCE_DIRECTORY as _CONTINUATION_EVIDENCE_DIRECTORY,
    _RELEASE_DIRECTORY as _CONTINUATION_RELEASE_DIRECTORY,
    verify_post_admission_steady_state_continuation_evidence,
)
from .models import OperationIdentity, PrivateStatePaths
from .private_state import PrivateStateReadResult, _secure_private_path


_EVIDENCE_KIND = "main_computer.mother.deployment_mainnet_steady_state_soak_evidence.v1"
_EVIDENCE_DIRECTORY = ("evidence", "deployment-mainnet-steady-state-soaks")
_MIN_WINDOW_SECONDS = _GUARDIAN_HEALTH_FRESHNESS_SECONDS + 5
_A = "mainneta-super1"
_C = "mainnetc-super1"
_ORDER = (_C, _A)


class MotherDeploymentMainnetSoakError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _error(code: str, message: str) -> MotherDeploymentMainnetSoakError:
    return MotherDeploymentMainnetSoakError(code, message)


def _load_baseline(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str],
    baseline_max_age_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any], Path, str]:
    _selection(selected_nodes)
    verified = verify_post_admission_steady_state_continuation_evidence(
        paths,
        private_state,
        Path(evidence_path),
        selected_nodes=selected_nodes,
        max_age_seconds=baseline_max_age_seconds,
        transaction_max_age_seconds=baseline_max_age_seconds,
    )
    if not (
        verified.get("clean") is True
        and verified.get("A_steady_state_installed") is True
        and verified.get("C_steady_state_preserved") is True
        and verified.get("component_scoped_steady_state_verified") is True
        and verified.get("validator_vote_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_BASELINE_INVALID",
            "baseline is not canonical completed A/C steady-state evidence",
        )
    canonical_evidence_path = Path(evidence_path).resolve(strict=False)
    evidence, _, evidence_file_sha = _canonical_under(
        paths,
        canonical_evidence_path,
        _CONTINUATION_EVIDENCE_DIRECTORY,
        "steady-state continuation evidence",
    )
    release_ref = _mapping(evidence.get("release"), "baseline.release")
    release_path = _resolve(
        paths,
        release_ref.get("locator"),
        _CONTINUATION_RELEASE_DIRECTORY,
        "steady-state continuation release",
    )
    release, _, _ = _canonical_under(
        paths,
        release_path,
        _CONTINUATION_RELEASE_DIRECTORY,
        "steady-state continuation release",
    )
    targets = _mapping(release.get("targets"), "baseline release targets")
    for node in _ORDER:
        target = _mapping(targets.get(node), f"baseline target {node}")
        steady = _mapping(target.get("steady_state_compose"), f"{node}.steady_state_compose")
        if not (
            type(steady.get("canonical_text")) is str
            and type(steady.get("semantic_sha256")) is str
            and target.get("controller_id") == _TARGETS[node]["controller_id"]
            and type(target.get("service_uuid")) is str
            and bool(target.get("service_uuid"))
        ):
            raise _error(
                "MOTHER_DEPLOY_MAINNET_SOAK_BASELINE_INVALID",
                f"baseline target {node} is incomplete",
            )
    return evidence, release, canonical_evidence_path, evidence_file_sha


def _observe_target(
    *,
    controller: Any,
    target: Mapping[str, Any],
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    steady = _mapping(target.get("steady_state_compose"), "target.steady_state_compose")
    observation = _runtime_target_observation(
        controller=controller,
        target=target,
        expected_compose=steady["canonical_text"],
        expected_mode="steady-state",
        require_aggregate_healthy=True,
        require_obsolete_absent=True,
        allow_exited_obsolete_records=True,
        allow_degraded_aggregate_for_exited_obsolete_records=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    return observation


def _write_evidence(
    paths: PrivateStatePaths,
    evidence: Mapping[str, Any],
    operation: OperationIdentity,
) -> tuple[Path, str]:
    document = dict(evidence)
    if document.get("kind") != _EVIDENCE_KIND or _contains_sensitive(document):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "mainnet soak evidence is malformed",
        )
    payload = canonical_json(document)
    digest = hashlib.sha256(payload).hexdigest()
    destination = _ensure_root(paths, _EVIDENCE_DIRECTORY, operation) / (
        f"{re.sub(r'[^0-9A-Za-z]+', '', document['completed_at'])[:32]}-"
        f"{digest[:16]}.json"
    )
    atomic_files.durable_create(destination, payload, operation=operation)
    _secure_private_path(destination, is_directory=False, operation=operation)
    return destination, digest


def run_mainnet_steady_state_soak(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    baseline_evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    baseline_max_age_seconds: int = 604800,
    duration_seconds: int = 1800,
    observation_interval_seconds: int = 60,
    timeout: float = 30.0,
    max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    opener: Any = _DEFAULT_OPENER,
    operation: OperationIdentity,
) -> dict[str, Any]:
    if not (
        type(duration_seconds) is int
        and type(observation_interval_seconds) is int
        and duration_seconds >= observation_interval_seconds
        and observation_interval_seconds >= _MIN_WINDOW_SECONDS
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_INVALID",
            f"duration must cover at least one interval and interval must be at least {_MIN_WINDOW_SECONDS} seconds",
        )
    baseline, release, baseline_path, baseline_file_sha = _load_baseline(
        paths,
        private_state,
        Path(baseline_evidence_path),
        selected_nodes=selected_nodes,
        baseline_max_age_seconds=baseline_max_age_seconds,
    )
    targets = _mapping(release.get("targets"), "baseline release targets")
    controllers = {
        "coolify-a": resolve_coolify_controller(private_state, release["network"], "coolify-a"),
        "coolify-c": resolve_coolify_controller(private_state, release["network"], "coolify-c"),
    }

    steady_module = __import__(
        "tools.mother.common.deployment_post_admission_steady_state",
        fromlist=["_MONOTONIC", "_SLEEP"],
    )
    started_at = _timestamp()
    started_mono = steady_module._MONOTONIC()
    required_gaps = int(math.ceil(duration_seconds / observation_interval_seconds))
    windows: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None

    for ordinal in range(required_gaps + 1):
        if ordinal:
            steady_module._SLEEP(observation_interval_seconds)
        elapsed = int(round(steady_module._MONOTONIC() - started_mono))
        node_observations: list[dict[str, Any]] = []
        for node in _ORDER:
            target = _mapping(targets.get(node), f"target {node}")
            try:
                observation = _observe_target(
                    controller=controllers[target["controller_id"]],
                    target=target,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                    opener=opener,
                )
            except Exception as exc:
                failure = {
                    "code": getattr(exc, "code", "MOTHER_DEPLOY_MAINNET_SOAK_OBSERVATION_FAILED"),
                    "message": _safe_message(exc),
                    "node": node,
                    "window_ordinal": ordinal,
                }
                break
            observation["node"] = node
            node_observations.append(observation)
            if observation.get("verified") is not True:
                failure = {
                    "code": "MOTHER_DEPLOY_MAINNET_SOAK_NOT_HEALTHY",
                    "message": f"{node} failed exact component-scoped steady-state observation",
                    "node": node,
                    "window_ordinal": ordinal,
                }
                break
        window_verified = (
            failure is None
            and len(node_observations) == 2
            and all(item.get("verified") is True for item in node_observations)
        )
        windows.append(
            {
                "ordinal": ordinal,
                "elapsed_seconds": elapsed,
                "observed_at": _timestamp(),
                "guardian_cycle_proof": {
                    "freshness": (
                        "both retained guardian health markers are fresher than "
                        f"{_GUARDIAN_HEALTH_FRESHNESS_SECONDS} seconds"
                    ),
                    _A: (
                        "the exact retained A guardian internally proves chain identity, "
                        "validator membership, peer presence, a block-height increase, "
                        "and a fresh latest block"
                    ),
                    _C: (
                        "the exact retained C guardian internally proves chain identity, "
                        "validator membership, peer presence, and synchronized state"
                    ),
                },
                "nodes": node_observations,
                "verified": window_verified,
            }
        )
        if failure is not None:
            break

    completed_at = _timestamp()
    complete = (
        failure is None
        and len(windows) == required_gaps + 1
        and all(window["verified"] is True for window in windows)
        and all(
            windows[index]["elapsed_seconds"] - windows[index - 1]["elapsed_seconds"]
            >= _MIN_WINDOW_SECONDS
            for index in range(1, len(windows))
        )
    )
    stale_records = sorted(
        {
            name
            for window in windows
            for observation in window["nodes"]
            for name in observation.get("recognized_obsolete_components_present", [])
        }
    )
    evidence = {
        "kind": _EVIDENCE_KIND,
        "schema_version": 1,
        "status": "pass" if complete else "manual-review-required",
        "network": release["network"],
        "nodes": list(_ORDER),
        "mother_binding": _binding(private_state),
        "chain_id": release["chain"]["chain_id"],
        "genesis_sha256": release["chain"]["genesis_sha256"],
        "validator_set": list(release["chain"]["validator_set"]),
        "started_at": started_at,
        "completed_at": completed_at,
        "baseline": {
            "locator": _relative(paths, baseline_path, "steady-state continuation evidence"),
            "file_sha256": baseline_file_sha,
        },
        "policy": {
            "read_only": True,
            "allowed_http_methods": ["GET"],
            "network_access_performed": True,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "log_endpoints_queried": False,
            "direct_coolify_database_mutation_authorized": False,
            "service_record_deletion_authorized": False,
            "aggregate_badges_authoritative": False,
        },
        "timing": {
            "requested_duration_seconds": duration_seconds,
            "observation_interval_seconds": observation_interval_seconds,
            "guardian_health_freshness_seconds": _GUARDIAN_HEALTH_FRESHNESS_SECONDS,
            "minimum_refresh_gap_seconds": _MIN_WINDOW_SECONDS,
            "required_gap_count": required_gaps,
            "observation_window_count": len(windows),
            "observed_duration_seconds": windows[-1]["elapsed_seconds"] if windows else 0,
        },
        "proof_basis": {
            "compose": "every window binds the exact released A and C steady-state Compose semantics",
            "components": "both Besu and retained guardian components are running:healthy in every window",
            "blocks": (
                "the exact retained A guardian internally proves a block-height increase and "
                "a fresh latest block on each health cycle; A and C guardian observations are "
                "separated beyond the guardian health freshness horizon"
            ),
        },
        "windows": windows,
        "failure": failure,
        "summary": {
            "clean": complete,
            "complete": complete,
            "exact_compose_unchanged": complete,
            "required_components_healthy": complete,
            "guardian_cycles_refreshed": complete,
            "block_height_advancement_verified_across_windows": complete,
            "blocks_advancing": complete,
            "latest_block_fresh": complete,
            "validator_set_verified": complete,
            "platform_stale_component_records_present": bool(stale_records),
            "platform_stale_component_records": stale_records,
            "live_mutation_performed": False,
            "validator_vote_performed": False,
            "manual_ssh_required": False,
            "public_endpoint_created": False,
            "next_phase": (
                "mainnet-steady-state-soak-complete"
                if complete
                else "manual-review-required"
            ),
        },
    }
    path, digest = _write_evidence(paths, evidence, operation)
    return {
        **evidence,
        "evidence": {"path": str(path), "sha256": digest},
    }


def verify_mainnet_steady_state_soak_evidence(
    paths: PrivateStatePaths,
    private_state: PrivateStateReadResult,
    evidence_path: Path,
    *,
    selected_nodes: Iterable[str] = (),
    max_age_seconds: int = 300,
    baseline_max_age_seconds: int = 604800,
) -> dict[str, Any]:
    _selection(selected_nodes)
    canonical_path = Path(evidence_path).resolve(strict=False)
    document, _, file_sha = _canonical_under(
        paths,
        canonical_path,
        _EVIDENCE_DIRECTORY,
        "mainnet steady-state soak evidence",
    )
    if not (
        document.get("kind") == _EVIDENCE_KIND
        and document.get("schema_version") == 1
        and document.get("status") == "pass"
        and document.get("mother_binding") == _binding(private_state)
        and not _contains_sensitive(document)
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "soak evidence is not a passing canonical document",
        )
    completed = _parse_utc(document.get("completed_at"), "soak.completed_at")
    age = int((datetime.now(timezone.utc) - completed).total_seconds())
    if age < -60 or age > max_age_seconds:
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_STALE",
            "soak evidence is outside its verification age window",
        )

    baseline_ref = _mapping(document.get("baseline"), "soak.baseline")
    baseline_path = _resolve(
        paths,
        baseline_ref.get("locator"),
        _CONTINUATION_EVIDENCE_DIRECTORY,
        "steady-state continuation evidence",
    )
    baseline_document, baseline_release, _, baseline_file_sha = _load_baseline(
        paths,
        private_state,
        baseline_path,
        selected_nodes=selected_nodes,
        baseline_max_age_seconds=baseline_max_age_seconds,
    )
    if baseline_ref.get("file_sha256") != baseline_file_sha:
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "soak baseline file binding is invalid",
        )
    if not (
        document.get("network") == baseline_release.get("network")
        and document.get("nodes") == list(_ORDER)
        and document.get("mother_binding") == baseline_document.get("mother_binding")
        and document.get("chain_id") == baseline_release.get("chain", {}).get("chain_id")
        and document.get("genesis_sha256")
        == baseline_release.get("chain", {}).get("genesis_sha256")
        and document.get("validator_set")
        == baseline_release.get("chain", {}).get("validator_set")
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "soak chain or baseline identity binding is invalid",
        )

    policy = _mapping(document.get("policy"), "soak.policy")
    timing = _mapping(document.get("timing"), "soak.timing")
    summary = _mapping(document.get("summary"), "soak.summary")
    windows = document.get("windows")
    requested_duration = timing.get("requested_duration_seconds")
    interval = timing.get("observation_interval_seconds")
    minimum_gap = timing.get("minimum_refresh_gap_seconds")
    if not (
        policy.get("read_only") is True
        and policy.get("allowed_http_methods") == ["GET"]
        and policy.get("live_mutation_performed") is False
        and policy.get("validator_vote_performed") is False
        and policy.get("manual_ssh_required") is False
        and policy.get("public_endpoint_created") is False
        and policy.get("log_endpoints_queried") is False
        and type(windows) is list
        and len(windows) >= 2
        and type(requested_duration) is int
        and type(interval) is int
        and type(minimum_gap) is int
        and requested_duration >= interval
        and minimum_gap >= _MIN_WINDOW_SECONDS
        and interval >= minimum_gap
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "soak read-only policy or timing is invalid",
        )

    prior_elapsed: float | None = None
    for expected_ordinal, window in enumerate(windows):
        if not isinstance(window, Mapping):
            raise _error("MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID", "soak window is malformed")
        elapsed = window.get("elapsed_seconds")
        observations = window.get("nodes")
        if not (
            window.get("ordinal") == expected_ordinal
            and window.get("verified") is True
            and type(elapsed) is int
            and type(observations) is list
            and [item.get("node") for item in observations] == list(_ORDER)
        ):
            raise _error(
                "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
                "soak window ordering or node coverage is invalid",
            )
        if prior_elapsed is not None and elapsed - prior_elapsed < interval:
            raise _error(
                "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
                "soak windows do not cross the guardian refresh horizon",
            )
        prior_elapsed = elapsed
        for observation in observations:
            if not (
                observation.get("verified") is True
                and observation.get("compose_matches") is True
                and observation.get("required_components_healthy") is True
                and observation.get("obsolete_compose_services_absent") is True
                and (
                    observation.get("recognized_obsolete_components_present") == []
                    or observation.get("recognized_obsolete_component_records_all_exited") is True
                )
                and observation.get("unexpected_component_records_present") == []
            ):
                raise _error(
                    "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
                    "soak observation does not prove exact component-scoped steady state",
                )

    if not (
        windows[0]["elapsed_seconds"] == 0
        and timing.get("observation_window_count") == len(windows)
        and timing.get("observed_duration_seconds") == windows[-1]["elapsed_seconds"]
        and timing.get("observed_duration_seconds") >= requested_duration
        and timing.get("required_gap_count")
        == int(math.ceil(requested_duration / interval))
        and timing.get("required_gap_count") == len(windows) - 1
        and document.get("failure") is None
        and summary.get("clean") is True
        and summary.get("complete") is True
        and summary.get("exact_compose_unchanged") is True
        and summary.get("required_components_healthy") is True
        and summary.get("guardian_cycles_refreshed") is True
        and summary.get("block_height_advancement_verified_across_windows") is True
        and summary.get("blocks_advancing") is True
        and summary.get("latest_block_fresh") is True
        and summary.get("validator_set_verified") is True
        and summary.get("live_mutation_performed") is False
        and summary.get("validator_vote_performed") is False
    ):
        raise _error(
            "MOTHER_DEPLOY_MAINNET_SOAK_EVIDENCE_INVALID",
            "soak summary is not derived from complete observations",
        )

    return {
        "clean": True,
        "network": document["network"],
        "nodes": list(document["nodes"]),
        "mother_binding": document["mother_binding"],
        "chain_id": document["chain_id"],
        "genesis_sha256": document["genesis_sha256"],
        "validator_set": list(document["validator_set"]),
        "age_seconds": age,
        "observation_window_count": len(windows),
        "observed_duration_seconds": timing["observed_duration_seconds"],
        "guardian_cycles_refreshed": True,
        "block_height_advancement_verified_across_windows": True,
        "blocks_advancing": True,
        "latest_block_fresh": True,
        "live_mutation_performed": False,
        "validator_vote_performed": False,
        "manual_ssh_required": False,
        "public_endpoint_created": False,
        "evidence_path": str(canonical_path),
        "evidence_sha256": file_sha,
        "next_phase": "mainnet-steady-state-soak-complete",
    }
