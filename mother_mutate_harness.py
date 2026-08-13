#!/usr/bin/env python3
"""Mother add/remove-node mutation harness.

The harness reduces node mutation babysitting without bypassing Mother authority:

* preflights the supplied topology evidence against live Coolify service reality;
* halts on stale Mother topology and prints the explicit rectification command;
* never performs rectification automatically;
* runs prep/release/verify/execute steps in order;
* routes by Mother evidence ``next_phase`` instead of assuming a fixed topology.

Supported add-node routes:

* empty topology -> single-node bootstrap -> read-only proof/finalize;
* existing topology -> replica sync -> validator admission.

Supported remove-node routes:

* one-node single-node topology -> delete sole service -> verify empty topology;
* multi-node topology -> survivor vote guardians -> delete target -> finalize survivors.

Mutation steps require both:
  --execute-mutations
  --yes-i-know-this-mutates-target-host
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


COMMON_STEPS = [
    "detect-topology",
    "prep",
    "verify-prep",
    "release-do",
    "verify-do-release",
    "execute-do",
    "verify-do-evidence",
    "release-identity",
    "verify-identity-release",
    "execute-identity",
    "verify-identity-evidence",
]

SINGLE_NODE_STEPS = [
    "release-bootstrap",
    "verify-bootstrap-release",
    "execute-bootstrap",
    "verify-bootstrap-evidence",
    "finalize-single-node-proof",
    "verify-single-node-proof",
]

REPLICA_ADMISSION_STEPS = [
    "release-replica-sync",
    "verify-replica-sync-release",
    "execute-replica-sync",
    "verify-replica-sync-evidence",
    "release-validator-admission",
    "verify-validator-admission-release",
    "execute-validator-admission",
    "verify-validator-admission-evidence",
    "finalize-post-admission-topology",
]

REMOVE_STEPS = [
    "detect-topology",
    "remove-prep",
    "verify-remove-prep",
    "release-remove-do",
    "verify-remove-do-release",
    "execute-remove-do",
    "verify-remove-do-evidence",
    "finalize-remove",
    "verify-remove-finalize",
]

STEP_ORDER = COMMON_STEPS + SINGLE_NODE_STEPS + REPLICA_ADMISSION_STEPS + [
    step for step in REMOVE_STEPS if step not in COMMON_STEPS
]

MUTATION_STEPS = {
    "execute-do",
    "execute-identity",
    "execute-bootstrap",
    "execute-replica-sync",
    "execute-validator-admission",
    "execute-remove-do",
}


def first_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("command output did not contain a JSON object")
    obj, _end = decoder.raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("command output JSON was not an object")
    return obj


def pick(obj: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = obj
        ok = True
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok:
            return cur
    return None


def require(name: str, value: Any) -> Any:
    if value in (None, ""):
        raise SystemExit(f"missing required value: {name}")
    return value


def quote_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(subprocess.list2cmdline([arg]) for arg in argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def is_clean_or_pass(step: str, obj: dict[str, Any]) -> bool:
    if step in {"prep", "remove-prep"}:
        sha_key = "node_remove_prep_transaction_sha256" if step == "remove-prep" else "node_add_prep_transaction_sha256"
        return (
            pick(obj, "summary.transaction_valid") is True
            and pick(obj, "summary.live_mutation_performed") is False
            and bool(pick(obj, "transaction_artifact.path"))
            and bool(pick(obj, "transaction_artifact.sha256", sha_key))
        )
    if obj.get("clean") is True:
        return True
    if pick(obj, "summary.clean") is True:
        return True
    if obj.get("status") == "pass":
        return True
    if step.startswith("release-") and pick(obj, "summary.clean") is True:
        return True
    if step == "detect-topology" and obj.get("status") in {"pass", "stale", "manual-review-required"}:
        return True
    return False


def step_performed_live_mutation(step: str, obj: dict[str, Any] | None) -> bool:
    if step in MUTATION_STEPS:
        return True
    if obj is None:
        return False
    return any(
        value is True
        for value in (
            obj.get("live_mutation_performed"),
            pick(obj, "summary.live_mutation_performed"),
            pick(obj, "policy.live_mutation_performed"),
        )
    )


def print_step_stop_guidance(step: str, obj: dict[str, Any] | None) -> None:
    if step_performed_live_mutation(step, obj):
        print("Do not retry the mutation blindly. Diagnose/rollback first.")
    else:
        print("No live mutation was performed by this step. Fix the input/tooling issue, then rerun from the last valid artifact.")


def short_summary(step: str, obj: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "clean",
        "status",
        "failure",
        "next_phase",
        "release_already_claimed",
        "created_service_uuid",
        "target_node",
        "target_host",
        "coolify_c_required",
        "replica_sync_required",
        "validator_admission_required",
        "single_node_bootstrap_required",
        "single_node_decommission",
        "service_deletion_performed",
        "service_already_absent",
        "target_service_absent",
        "service_deletion_is_first",
        "validator_removal_vote_required",
        "validator_removal_vote_performed",
        "single_node_bootstrap_authorized",
        "single_node_bootstrap_proven",
        "serves_chain",
        "serves_hub",
        "topology_current",
        "topology_stale",
        "rectification_required",
        "manual_review_required",
        "live_mutation_performed",
        "network_access_performed",
    ]

    summary: dict[str, Any] = {}
    for key in keys:
        value = obj.get(key)
        if value is None:
            value = pick(obj, f"summary.{key}")
        if value is None:
            value = pick(obj, f"target.{key}")
        if value is not None:
            summary[key] = value

    artifact_path = pick(
        obj,
        "transaction_artifact.path",
        "release_artifact.path",
        "evidence.path",
        "transaction_path",
        "release_path",
        "evidence_path",
    )
    artifact_sha = pick(
        obj,
        "transaction_artifact.sha256",
        "release_artifact.sha256",
        "evidence.sha256",
        "node_add_prep_transaction_sha256",
        "node_add_do_release_sha256",
        "node_add_identity_release_sha256",
        "node_add_single_node_bootstrap_release_sha256",
        "node_add_replica_sync_release_sha256",
        "node_add_validator_admission_release_sha256",
        "node_remove_prep_transaction_sha256",
        "node_remove_do_release_sha256",
        "evidence_sha256",
    )
    if artifact_path:
        summary["artifact_path"] = artifact_path
    if artifact_sha:
        summary["artifact_sha256"] = artifact_sha
    return summary



def capped_remove_do_max_wait_seconds(value: float) -> float:
    """Mother remove-node do currently accepts at most 300 seconds."""
    return min(float(value), 300.0)


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(__file__).resolve().parent
        self.run_dir = Path(args.run_dir) if args.run_dir else Path(args.runtime_state_root) / "mother" / "harness-runs" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, Any] = {
            "baseline_evidence": args.baseline_evidence,
            "baseline_evidence_sha256": args.baseline_evidence_sha256,
            "prep_transaction": args.prep_transaction,
            "prep_transaction_sha256": args.prep_transaction_sha256,
            "do_release": args.do_release,
            "do_release_sha256": args.do_release_sha256,
            "do_evidence": args.do_evidence,
            "do_evidence_sha256": args.do_evidence_sha256,
            "identity_release": args.identity_release,
            "identity_release_sha256": args.identity_release_sha256,
            "identity_evidence": args.identity_evidence,
            "identity_evidence_sha256": args.identity_evidence_sha256,
            "bootstrap_release": args.bootstrap_release,
            "bootstrap_release_sha256": args.bootstrap_release_sha256,
            "bootstrap_evidence": args.bootstrap_evidence,
            "bootstrap_evidence_sha256": args.bootstrap_evidence_sha256,
            "single_node_proof_evidence": args.single_node_proof_evidence,
            "single_node_proof_evidence_sha256": args.single_node_proof_evidence_sha256,
            "replica_sync_release": args.replica_sync_release,
            "replica_sync_release_sha256": args.replica_sync_release_sha256,
            "replica_sync_evidence": args.replica_sync_evidence,
            "replica_sync_evidence_sha256": args.replica_sync_evidence_sha256,
            "validator_admission_release": args.validator_admission_release,
            "validator_admission_release_sha256": args.validator_admission_release_sha256,
            "validator_admission_evidence": args.validator_admission_evidence,
            "validator_admission_evidence_sha256": args.validator_admission_evidence_sha256,
            "post_admission_topology_evidence": args.post_admission_topology_evidence,
            "post_admission_topology_evidence_sha256": args.post_admission_topology_evidence_sha256,
            "remove_prep_transaction": args.remove_prep_transaction,
            "remove_prep_transaction_sha256": args.remove_prep_transaction_sha256,
            "remove_do_release": args.remove_do_release,
            "remove_do_release_sha256": args.remove_do_release_sha256,
            "remove_do_evidence": args.remove_do_evidence,
            "remove_do_evidence_sha256": args.remove_do_evidence_sha256,
            "remove_finalize_evidence": args.remove_finalize_evidence,
            "remove_finalize_evidence_sha256": args.remove_finalize_evidence_sha256,
        }

    def cmd(self, *parts: Any) -> list[str]:
        return [sys.executable, str(self.repo_root / "tools" / "mother_deploy.py"), *[str(part) for part in parts]]

    def mutations_allowed(self) -> bool:
        return bool(self.args.execute_mutations and (self.args.yes_i_know_this_mutates_target_host or self.args.yes_i_know_this_mutates_coolify_a))

    def run(self, step: str, argv: list[str], *, allow_failure: bool = False) -> dict[str, Any]:
        if step in MUTATION_STEPS and not self.mutations_allowed():
            print(f"\n=== stopping before mutation boundary: {step} ===")
            print(quote_command(argv))
            print("\nRerun with --execute-mutations --yes-i-know-this-mutates-target-host to execute mutation steps.")
            raise SystemExit(3)

        print(f"\n=== {step} ===")
        print(quote_command(argv))
        proc = subprocess.run(argv, cwd=self.repo_root, text=True, capture_output=True)
        index = STEP_ORDER.index(step) if step in STEP_ORDER else 98
        prefix = f"{index:02d}-{step}"
        (self.run_dir / f"{prefix}.stdout.txt").write_text(proc.stdout, encoding="utf-8")
        (self.run_dir / f"{prefix}.stderr.txt").write_text(proc.stderr, encoding="utf-8")
        if proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print(proc.stderr.rstrip(), file=sys.stderr)

        obj: dict[str, Any] | None = None
        try:
            obj = first_json_object(proc.stdout)
            (self.run_dir / f"{prefix}.json").write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
            print(f"--- summary {step} ---")
            print(json.dumps(short_summary(step, obj), indent=2, sort_keys=True))
        except Exception as exc:
            if proc.returncode == 0:
                raise SystemExit(f"{step}: could not parse JSON output: {exc}") from exc

        if proc.returncode != 0:
            if allow_failure and obj is not None:
                return obj
            print(f"\n{step} failed with exit code {proc.returncode}.")
            if obj is not None:
                evidence_path = pick(obj, "evidence.path", "evidence_path")
                evidence_sha = pick(obj, "evidence.sha256", "evidence_sha256")
                if evidence_path:
                    print(f"evidence_path={evidence_path}")
                if evidence_sha:
                    print(f"evidence_sha256={evidence_sha}")
            print(f"logs={self.run_dir}")
            print_step_stop_guidance(step, obj)
            raise SystemExit(proc.returncode)

        if obj is None:
            raise SystemExit(f"{step}: command succeeded but produced no JSON object")
        if not is_clean_or_pass(step, obj):
            if allow_failure:
                return obj
            print(f"\n{step} did not produce a clean/pass result.")
            evidence_path = pick(obj, "evidence.path", "evidence_path")
            evidence_sha = pick(obj, "evidence.sha256", "evidence_sha256")
            if evidence_path:
                print(f"evidence_path={evidence_path}")
            if evidence_sha:
                print(f"evidence_sha256={evidence_sha}")
            print(f"logs={self.run_dir}")
            print_step_stop_guidance(step, obj)
            raise SystemExit(2)
        return obj

    def step_detect_topology(self) -> None:
        if self.args.skip_staleness_detection:
            return
        require("--baseline-evidence", self.state["baseline_evidence"])
        require("--baseline-evidence-sha256", self.state["baseline_evidence_sha256"])
        obj = self.run("detect-topology", self.cmd(
            "detect-mother-topology-staleness",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--topology-evidence", self.state["baseline_evidence"],
            "--acknowledge-topology-evidence-sha256", self.state["baseline_evidence_sha256"],
            "--max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
        ))
        if pick(obj, "summary.rectification_required") is True:
            print("\nMOTHER_MUTATE_HARNESS_STALE_TOPOLOGY: Mother topology evidence is out of date against live Coolify.")
            print("Run this read-only rectification command, then rerun the harness using the new evidence path/SHA:")
            rect_cmd = self.cmd(
                "adopt-empty-current-topology",
                "--network", self.args.network,
                "--runtime-state-root", self.args.runtime_state_root,
                "--topology-evidence", self.state["baseline_evidence"],
                "--acknowledge-topology-evidence-sha256", self.state["baseline_evidence_sha256"],
                "--max-age-seconds", str(self.args.baseline_max_age_seconds),
                "--timeout", str(self.args.timeout),
                "--max-response-bytes", str(self.args.max_response_bytes),
                "--write-evidence",
            )
            print(quote_command(rect_cmd))
            raise SystemExit(3)
        manual_review_required = (
            pick(obj, "summary.manual_review_required") is True
            or obj.get("status") == "manual-review-required"
        )
        topology_current = pick(obj, "summary.topology_current") is True
        topology_stale = (
            pick(obj, "summary.topology_stale") is True
            or pick(obj, "summary.rectification_required") is True
        )
        unexpected_live_nodes = obj.get("unexpected_live_nodes") or pick(obj, "summary.unexpected_live_nodes") or []
        if unexpected_live_nodes:
            print("\nMOTHER_MUTATE_HARNESS_TOPOLOGY_SPLIT_LIVE: live Coolify inventory contains nodes outside the supplied Mother topology evidence.")
            print(json.dumps(short_summary("detect-topology", obj), indent=2, sort_keys=True))
            raise SystemExit(3)
        if manual_review_required and (not topology_current or topology_stale):
            print("\nMOTHER_MUTATE_HARNESS_TOPOLOGY_MANUAL_REVIEW_REQUIRED: partial/non-empty live rectification is not implemented.")
            print(json.dumps(short_summary("detect-topology", obj), indent=2, sort_keys=True))
            raise SystemExit(3)

    def step_prep(self) -> None:
        require("--baseline-evidence", self.state["baseline_evidence"])
        require("--baseline-evidence-sha256", self.state["baseline_evidence_sha256"])
        obj = self.run("prep", self.cmd(
            "add-node", "prep", self.args.network,
            "--node", self.args.node,
            "--host", self.args.host,
            "--mode", self.args.mode,
            "--runtime-state-root", self.args.runtime_state_root,
            "--baseline-evidence", self.state["baseline_evidence"],
            "--baseline-evidence-sha256", self.state["baseline_evidence_sha256"],
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--write-transaction",
        ))
        self.state["prep_transaction"] = require("prep transaction path", pick(obj, "transaction_artifact.path"))
        self.state["prep_transaction_sha256"] = require("prep transaction sha", pick(obj, "transaction_artifact.sha256", "node_add_prep_transaction_sha256"))

    def step_verify_prep(self) -> None:
        self.run("verify-prep", self.cmd(
            "verify-add-node-prep-transaction",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--transaction", require("prep_transaction", self.state["prep_transaction"]),
            "--max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_release_do(self) -> None:
        obj = self.run("release-do", self.cmd(
            "release-add-node-do",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--transaction", require("prep_transaction", self.state["prep_transaction"]),
            "--acknowledge-node-add-prep-transaction-sha256", require("prep_transaction_sha256", self.state["prep_transaction_sha256"]),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["do_release"] = require("do release path", pick(obj, "release_artifact.path"))
        self.state["do_release_sha256"] = require("do release sha", pick(obj, "release_artifact.sha256", "node_add_do_release_sha256"))

    def step_verify_do_release(self) -> None:
        self.run("verify-do-release", self.cmd(
            "verify-add-node-do-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("do_release", self.state["do_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_do(self) -> None:
        obj = self.run("execute-do", self.cmd(
            "add-node", "do", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("do_release", self.state["do_release"]),
            "--acknowledge-release-sha256", require("do_release_sha256", self.state["do_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--execute",
        ))
        self.state["do_evidence"] = require("do evidence path", pick(obj, "evidence.path"))
        self.state["do_evidence_sha256"] = require("do evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_do_evidence(self) -> None:
        self.run("verify-do-evidence", self.cmd(
            "verify-add-node-do-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("do_evidence", self.state["do_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_release_identity(self) -> None:
        obj = self.run("release-identity", self.cmd(
            "release-add-node-identity",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--add-do-evidence", require("do_evidence", self.state["do_evidence"]),
            "--acknowledge-add-node-do-evidence-sha256", require("do_evidence_sha256", self.state["do_evidence_sha256"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["identity_release"] = require("identity release path", pick(obj, "release_artifact.path"))
        self.state["identity_release_sha256"] = require("identity release sha", pick(obj, "release_artifact.sha256", "node_add_identity_release_sha256"))

    def step_verify_identity_release(self) -> None:
        self.run("verify-identity-release", self.cmd(
            "verify-add-node-identity-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("identity_release", self.state["identity_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_identity(self) -> None:
        obj = self.run("execute-identity", self.cmd(
            "add-node", "identity", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("identity_release", self.state["identity_release"]),
            "--acknowledge-release-sha256", require("identity_release_sha256", self.state["identity_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--execute",
        ))
        self.state["identity_evidence"] = require("identity evidence path", pick(obj, "evidence.path"))
        self.state["identity_evidence_sha256"] = require("identity evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_identity_evidence(self) -> dict[str, Any]:
        obj = self.run("verify-identity-evidence", self.cmd(
            "verify-add-node-identity-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("identity_evidence", self.state["identity_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))
        self.state["route_next_phase"] = pick(obj, "next_phase")
        self.state["single_node_bootstrap_required"] = bool(pick(obj, "single_node_bootstrap_required", "summary.single_node_bootstrap_required"))
        self.state["replica_sync_required"] = bool(pick(obj, "replica_sync_required", "summary.replica_sync_required"))
        self.state["validator_admission_required"] = bool(pick(obj, "validator_admission_required", "summary.validator_admission_required"))
        return obj

    def step_release_bootstrap(self) -> None:
        obj = self.run("release-bootstrap", self.cmd(
            "release-add-node-single-node-bootstrap",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--identity-evidence", require("identity_evidence", self.state["identity_evidence"]),
            "--acknowledge-add-node-identity-evidence-sha256", require("identity_evidence_sha256", self.state["identity_evidence_sha256"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["bootstrap_release"] = require("bootstrap release path", pick(obj, "release_artifact.path"))
        self.state["bootstrap_release_sha256"] = require("bootstrap release sha", pick(obj, "release_artifact.sha256", "node_add_single_node_bootstrap_release_sha256"))

    def step_verify_bootstrap_release(self) -> None:
        self.run("verify-bootstrap-release", self.cmd(
            "verify-add-node-single-node-bootstrap-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("bootstrap_release", self.state["bootstrap_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_bootstrap(self) -> None:
        obj = self.run("execute-bootstrap", self.cmd(
            "add-node", "single-node-bootstrap", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("bootstrap_release", self.state["bootstrap_release"]),
            "--acknowledge-release-sha256", require("bootstrap_release_sha256", self.state["bootstrap_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--max-wait-seconds", str(self.args.max_wait_seconds),
            "--poll-interval-seconds", str(self.args.poll_interval_seconds),
            "--execute",
        ))
        self.state["bootstrap_evidence"] = require("bootstrap evidence path", pick(obj, "evidence.path"))
        self.state["bootstrap_evidence_sha256"] = require("bootstrap evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_bootstrap_evidence(self) -> None:
        obj = self.run("verify-bootstrap-evidence", self.cmd(
            "verify-add-node-single-node-bootstrap-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("bootstrap_evidence", self.state["bootstrap_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ), allow_failure=True)
        if is_clean_or_pass("verify-bootstrap-evidence", obj):
            return
        print("\nBootstrap evidence was not clean. Trying read-only live proof adoption once.")
        adopted = self.run("adopt-bootstrap-proof", self.cmd(
            "adopt-add-node-single-node-bootstrap-live-proof",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("bootstrap_evidence", self.state["bootstrap_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
        ))
        self.state["bootstrap_evidence"] = require("adopted bootstrap evidence path", pick(adopted, "evidence.path"))
        self.state["bootstrap_evidence_sha256"] = require("adopted bootstrap evidence sha", pick(adopted, "evidence.sha256"))
        self.run("verify-bootstrap-evidence", self.cmd(
            "verify-add-node-single-node-bootstrap-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", self.state["bootstrap_evidence"],
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_finalize_single_node_proof(self) -> None:
        obj = self.run("finalize-single-node-proof", self.cmd(
            "add-node", "single-node-chain-and-hub-proof", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--bootstrap-evidence", require("bootstrap_evidence", self.state["bootstrap_evidence"]),
            "--acknowledge-bootstrap-evidence-sha256", require("bootstrap_evidence_sha256", self.state["bootstrap_evidence_sha256"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--write-evidence",
        ))
        self.state["single_node_proof_evidence"] = require("single-node proof evidence path", pick(obj, "evidence.path"))
        self.state["single_node_proof_evidence_sha256"] = require("single-node proof evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_single_node_proof(self) -> None:
        self.run("verify-single-node-proof", self.cmd(
            "verify-add-node-single-node-chain-and-hub-proof-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("single_node_proof_evidence", self.state["single_node_proof_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--bootstrap-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_release_replica_sync(self) -> None:
        obj = self.run("release-replica-sync", self.cmd(
            "release-add-node-replica-sync",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--identity-evidence", require("identity_evidence", self.state["identity_evidence"]),
            "--acknowledge-add-node-identity-evidence-sha256", require("identity_evidence_sha256", self.state["identity_evidence_sha256"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["replica_sync_release"] = require("replica sync release path", pick(obj, "release_artifact.path"))
        self.state["replica_sync_release_sha256"] = require("replica sync release sha", pick(obj, "release_artifact.sha256", "node_add_replica_sync_release_sha256"))

    def step_verify_replica_sync_release(self) -> None:
        self.run("verify-replica-sync-release", self.cmd(
            "verify-add-node-replica-sync-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("replica_sync_release", self.state["replica_sync_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_replica_sync(self) -> None:
        obj = self.run("execute-replica-sync", self.cmd(
            "add-node", "replica-sync", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("replica_sync_release", self.state["replica_sync_release"]),
            "--acknowledge-release-sha256", require("replica_sync_release_sha256", self.state["replica_sync_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--max-wait-seconds", str(self.args.max_wait_seconds),
            "--poll-interval-seconds", str(self.args.poll_interval_seconds),
            "--execute",
        ))
        self.state["replica_sync_evidence"] = require("replica sync evidence path", pick(obj, "evidence.path"))
        self.state["replica_sync_evidence_sha256"] = require("replica sync evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_replica_sync_evidence(self) -> None:
        self.run("verify-replica-sync-evidence", self.cmd(
            "verify-add-node-replica-sync-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("replica_sync_evidence", self.state["replica_sync_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_release_validator_admission(self) -> None:
        obj = self.run("release-validator-admission", self.cmd(
            "release-add-node-validator-admission",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--replica-sync-evidence", require("replica_sync_evidence", self.state["replica_sync_evidence"]),
            "--acknowledge-add-node-replica-sync-evidence-sha256", require("replica_sync_evidence_sha256", self.state["replica_sync_evidence_sha256"]),
            "--replica-sync-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--replica-sync-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["validator_admission_release"] = require("validator admission release path", pick(obj, "release_artifact.path"))
        self.state["validator_admission_release_sha256"] = require("validator admission release sha", pick(obj, "release_artifact.sha256", "node_add_validator_admission_release_sha256"))

    def step_verify_validator_admission_release(self) -> None:
        self.run("verify-validator-admission-release", self.cmd(
            "verify-add-node-validator-admission-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("validator_admission_release", self.state["validator_admission_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--replica-sync-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--replica-sync-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_validator_admission(self) -> None:
        obj = self.run("execute-validator-admission", self.cmd(
            "add-node", "validator-admission", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("validator_admission_release", self.state["validator_admission_release"]),
            "--acknowledge-release-sha256", require("validator_admission_release_sha256", self.state["validator_admission_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--replica-sync-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--replica-sync-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--max-wait-seconds", str(self.args.max_wait_seconds),
            "--poll-interval-seconds", str(self.args.poll_interval_seconds),
            "--execute",
        ))
        self.state["validator_admission_evidence"] = require("validator admission evidence path", pick(obj, "evidence.path"))
        self.state["validator_admission_evidence_sha256"] = require("validator admission evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_validator_admission_evidence(self) -> None:
        self.run("verify-validator-admission-evidence", self.cmd(
            "verify-add-node-validator-admission-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("validator_admission_evidence", self.state["validator_admission_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--replica-sync-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--replica-sync-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--identity-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--identity-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--add-do-max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--add-do-release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))


    def step_finalize_post_admission_topology(self) -> None:
        obj = self.run("finalize-post-admission-topology", self.cmd(
            "add-node", "post-admission-observe", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--validator-admission-evidence", require("validator_admission_evidence", self.state["validator_admission_evidence"]),
            "--acknowledge-validator-admission-evidence-sha256", require("validator_admission_evidence_sha256", self.state["validator_admission_evidence_sha256"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--write-evidence",
        ))
        self.state["post_admission_topology_evidence"] = require("post-admission topology evidence path", pick(obj, "evidence.path"))
        self.state["post_admission_topology_evidence_sha256"] = require("post-admission topology evidence sha", pick(obj, "evidence.sha256"))


    def step_remove_prep(self) -> None:
        require("--baseline-evidence", self.state["baseline_evidence"])
        require("--baseline-evidence-sha256", self.state["baseline_evidence_sha256"])
        obj = self.run("remove-prep", self.cmd(
            "remove-node", "prep", self.args.network,
            "--node", self.args.node,
            "--mode", self.args.remove_mode,
            "--runtime-state-root", self.args.runtime_state_root,
            "--baseline-evidence", self.state["baseline_evidence"],
            "--baseline-evidence-sha256", self.state["baseline_evidence_sha256"],
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--write-transaction",
        ))
        self.state["remove_prep_transaction"] = require("remove prep transaction path", pick(obj, "transaction_artifact.path"))
        self.state["remove_prep_transaction_sha256"] = require("remove prep transaction sha", pick(obj, "transaction_artifact.sha256", "node_remove_prep_transaction_sha256"))

    def step_verify_remove_prep(self) -> None:
        self.run("verify-remove-prep", self.cmd(
            "verify-remove-node-prep-transaction",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--transaction", require("remove_prep_transaction", self.state["remove_prep_transaction"]),
            "--max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_release_remove_do(self) -> None:
        obj = self.run("release-remove-do", self.cmd(
            "release-remove-node-do",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--transaction", require("remove_prep_transaction", self.state["remove_prep_transaction"]),
            "--acknowledge-node-remove-prep-transaction-sha256", require("remove_prep_transaction_sha256", self.state["remove_prep_transaction_sha256"]),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--expires-in-seconds", str(self.args.release_expires_in_seconds),
            "--write-release",
        ))
        self.state["remove_do_release"] = require("remove do release path", pick(obj, "release_artifact.path", "release_path"))
        self.state["remove_do_release_sha256"] = require("remove do release sha", pick(obj, "release_artifact.sha256", "node_remove_do_release_sha256"))

    def step_verify_remove_do_release(self) -> None:
        self.run("verify-remove-do-release", self.cmd(
            "verify-remove-node-do-release",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("remove_do_release", self.state["remove_do_release"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_execute_remove_do(self) -> None:
        obj = self.run("execute-remove-do", self.cmd(
            "remove-node", "do", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--release", require("remove_do_release", self.state["remove_do_release"]),
            "--acknowledge-release-sha256", require("remove_do_release_sha256", self.state["remove_do_release_sha256"]),
            "--max-age-seconds", str(self.args.release_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-wait-seconds", str(capped_remove_do_max_wait_seconds(self.args.max_wait_seconds)),
            "--poll-interval-seconds", str(self.args.poll_interval_seconds),
            *(["--allow-missing-service"] if self.args.allow_missing_service else []),
            "--execute",
        ))
        self.state["remove_do_evidence"] = require("remove do evidence path", pick(obj, "evidence.path"))
        self.state["remove_do_evidence_sha256"] = require("remove do evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_remove_do_evidence(self) -> None:
        self.run("verify-remove-do-evidence", self.cmd(
            "verify-remove-node-do-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("remove_do_evidence", self.state["remove_do_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--release-max-age-seconds", str(self.args.release_lineage_max_age_seconds),
            "--transaction-max-age-seconds", str(self.args.transaction_max_age_seconds),
            "--baseline-max-age-seconds", str(self.args.baseline_max_age_seconds),
        ))

    def step_finalize_remove(self) -> None:
        obj = self.run("finalize-remove", self.cmd(
            "remove-node", "finalize", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--do-evidence", require("remove_do_evidence", self.state["remove_do_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--timeout", str(self.args.timeout),
            "--max-response-bytes", str(self.args.max_response_bytes),
            "--write-evidence",
        ))
        self.state["remove_finalize_evidence"] = require("remove finalize evidence path", pick(obj, "evidence.path"))
        self.state["remove_finalize_evidence_sha256"] = require("remove finalize evidence sha", pick(obj, "evidence.sha256"))

    def step_verify_remove_finalize(self) -> None:
        self.run("verify-remove-finalize", self.cmd(
            "verify-remove-node-finalize-evidence",
            "--network", self.args.network,
            "--runtime-state-root", self.args.runtime_state_root,
            "--evidence", require("remove_finalize_evidence", self.state["remove_finalize_evidence"]),
            "--max-age-seconds", str(self.args.evidence_max_age_seconds),
            "--do-max-age-seconds", str(self.args.evidence_max_age_seconds),
        ))

    def methods(self) -> dict[str, Any]:
        return {
            "detect-topology": self.step_detect_topology,
            "prep": self.step_prep,
            "verify-prep": self.step_verify_prep,
            "release-do": self.step_release_do,
            "verify-do-release": self.step_verify_do_release,
            "execute-do": self.step_execute_do,
            "verify-do-evidence": self.step_verify_do_evidence,
            "release-identity": self.step_release_identity,
            "verify-identity-release": self.step_verify_identity_release,
            "execute-identity": self.step_execute_identity,
            "verify-identity-evidence": self.step_verify_identity_evidence,
            "release-bootstrap": self.step_release_bootstrap,
            "verify-bootstrap-release": self.step_verify_bootstrap_release,
            "execute-bootstrap": self.step_execute_bootstrap,
            "verify-bootstrap-evidence": self.step_verify_bootstrap_evidence,
            "finalize-single-node-proof": self.step_finalize_single_node_proof,
            "verify-single-node-proof": self.step_verify_single_node_proof,
            "release-replica-sync": self.step_release_replica_sync,
            "verify-replica-sync-release": self.step_verify_replica_sync_release,
            "execute-replica-sync": self.step_execute_replica_sync,
            "verify-replica-sync-evidence": self.step_verify_replica_sync_evidence,
            "release-validator-admission": self.step_release_validator_admission,
            "verify-validator-admission-release": self.step_verify_validator_admission_release,
            "execute-validator-admission": self.step_execute_validator_admission,
            "verify-validator-admission-evidence": self.step_verify_validator_admission_evidence,
            "finalize-post-admission-topology": self.step_finalize_post_admission_topology,
            "remove-prep": self.step_remove_prep,
            "verify-remove-prep": self.step_verify_remove_prep,
            "release-remove-do": self.step_release_remove_do,
            "verify-remove-do-release": self.step_verify_remove_do_release,
            "execute-remove-do": self.step_execute_remove_do,
            "verify-remove-do-evidence": self.step_verify_remove_do_evidence,
            "finalize-remove": self.step_finalize_remove,
            "verify-remove-finalize": self.step_verify_remove_finalize,
        }

    def run_steps(self, steps: list[str], start_at: str) -> None:
        methods = self.methods()
        start_index = steps.index(start_at) if start_at in steps else 0
        for step in steps[start_index:]:
            methods[step]()

    def route_after_identity(self) -> str:
        next_phase = str(self.state.get("route_next_phase") or "")
        if self.state.get("single_node_bootstrap_required") or next_phase.startswith("add-node-single-node-bootstrap-"):
            return "single-node"
        if self.state.get("replica_sync_required") or self.state.get("validator_admission_required") or next_phase.startswith("add-node-replica-sync-"):
            return "replica-admission"
        raise SystemExit(f"unsupported add-node next_phase after identity: {next_phase!r}")

    def run_all(self) -> None:
        if self.args.operation == "remove-node":
            if self.args.start_at not in REMOVE_STEPS:
                raise SystemExit(f"start step {self.args.start_at!r} is not valid for remove-node")
            self.run_steps(REMOVE_STEPS, self.args.start_at)
        elif self.args.start_at in COMMON_STEPS:
            self.run_steps(COMMON_STEPS, self.args.start_at)
            route = self.route_after_identity()
            if route == "single-node":
                self.run_steps(SINGLE_NODE_STEPS, SINGLE_NODE_STEPS[0])
            else:
                self.run_steps(REPLICA_ADMISSION_STEPS, REPLICA_ADMISSION_STEPS[0])
        elif self.args.start_at in SINGLE_NODE_STEPS:
            self.run_steps(SINGLE_NODE_STEPS, self.args.start_at)
        elif self.args.start_at in REPLICA_ADMISSION_STEPS:
            self.run_steps(REPLICA_ADMISSION_STEPS, self.args.start_at)
        else:
            raise SystemExit(f"unknown start step: {self.args.start_at}")

        print("\n=== harness complete ===")
        print(json.dumps({k: v for k, v in self.state.items() if v not in (None, "")}, indent=2, sort_keys=True))
        print(f"logs={self.run_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", nargs="?", default="add-node", choices=["add-node", "remove-node"])
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--node", default="mainneta-super1")
    parser.add_argument("--host", default="coolify-a")
    parser.add_argument("--mode", default="reactivate")
    parser.add_argument("--remove-mode", default="soft", choices=["soft"])

    parser.add_argument("--baseline-evidence")
    parser.add_argument("--baseline-evidence-sha256")

    parser.add_argument("--start-at", choices=STEP_ORDER, default="detect-topology")
    parser.add_argument("--skip-staleness-detection", action="store_true")
    parser.add_argument("--execute-mutations", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-target-host", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-coolify-a", action="store_true", help="legacy alias for the target-host mutation acknowledgement")
    parser.add_argument("--run-dir")

    parser.add_argument("--transaction-max-age-seconds", type=int, default=86400)
    parser.add_argument("--baseline-max-age-seconds", type=int, default=86400)
    parser.add_argument("--evidence-max-age-seconds", type=int, default=86400)
    parser.add_argument("--release-lineage-max-age-seconds", type=int, default=86400)
    parser.add_argument("--release-max-age-seconds", type=int, default=900)
    parser.add_argument("--release-expires-in-seconds", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=900.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)

    parser.add_argument("--prep-transaction")
    parser.add_argument("--prep-transaction-sha256")
    parser.add_argument("--do-release")
    parser.add_argument("--do-release-sha256")
    parser.add_argument("--do-evidence")
    parser.add_argument("--do-evidence-sha256")
    parser.add_argument("--identity-release")
    parser.add_argument("--identity-release-sha256")
    parser.add_argument("--identity-evidence")
    parser.add_argument("--identity-evidence-sha256")
    parser.add_argument("--bootstrap-release")
    parser.add_argument("--bootstrap-release-sha256")
    parser.add_argument("--bootstrap-evidence")
    parser.add_argument("--bootstrap-evidence-sha256")
    parser.add_argument("--single-node-proof-evidence")
    parser.add_argument("--single-node-proof-evidence-sha256")
    parser.add_argument("--replica-sync-release")
    parser.add_argument("--replica-sync-release-sha256")
    parser.add_argument("--replica-sync-evidence")
    parser.add_argument("--replica-sync-evidence-sha256")
    parser.add_argument("--validator-admission-release")
    parser.add_argument("--validator-admission-release-sha256")
    parser.add_argument("--validator-admission-evidence")
    parser.add_argument("--validator-admission-evidence-sha256")
    parser.add_argument("--post-admission-topology-evidence")
    parser.add_argument("--post-admission-topology-evidence-sha256")
    parser.add_argument("--remove-prep-transaction")
    parser.add_argument("--remove-prep-transaction-sha256")
    parser.add_argument("--remove-do-release")
    parser.add_argument("--remove-do-release-sha256")
    parser.add_argument("--remove-do-evidence")
    parser.add_argument("--remove-do-evidence-sha256")
    parser.add_argument("--remove-finalize-evidence")
    parser.add_argument("--remove-finalize-evidence-sha256")
    parser.add_argument("--allow-missing-service", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.execute_mutations and not (args.yes_i_know_this_mutates_target_host or args.yes_i_know_this_mutates_coolify_a):
        raise SystemExit("mutation execution requires --yes-i-know-this-mutates-target-host")

    Harness(args).run_all()


if __name__ == "__main__":
    main()
