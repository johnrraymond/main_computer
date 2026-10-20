#!/usr/bin/env python3
"""Operator harness for FoundationDB inspection and topology mutation.

This file is the normal FDB operator surface.  Operators name the intent and
logical service; the harness owns run directories, operation IDs, low-level
FDB Control invocations, resume state, verification, and finalization.

Normal mutation lifecycle:

    accepted/live pre-inspect -> prep -> mutation gate -> do
    -> operation-scoped FDB proof -> finalize -> accepted/live post-inspect

The initial create-cluster transaction starts from independently inspected
``unborn`` state, infers the first service's controller from its name, and
uses the same proof/finalization machinery.  Cluster identity is frozen in
the harness resume state before any live mutation.

A second invocation of the same high-level command automatically resumes the
matching unfinished harness run.  Low-level ``tools.fdb_control`` commands are
retained only as internal implementation/evidence and are not printed during
normal operation.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


STEPS = (
    "pre-inspect",
    "prep",
    "do",
    "operation-inspect",
    "finalize",
    "final-inspect",
)
MUTATING_STEPS = frozenset({"do", "finalize"})


def quote_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(subprocess.list2cmdline([arg]) for arg in argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Main Computer FDB operator harness")
    parser.add_argument("operation", choices=("inspect", "diagnose-observer", "create-cluster", "add-service", "remove-service"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--service")
    parser.add_argument("--allow-full-deletion", action="store_true")
    parser.add_argument("--project-uuid", default="", help="Explicit Coolify project UUID for remove-service prep")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--execute-mutations", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-fdb", action="store_true")
    return parser


class HarnessError(RuntimeError):
    pass


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(args.repo_root).resolve()
        self.operation = str(args.operation)
        self.network = str(args.network)
        self.service = str(args.service or "").strip()
        self.allow_full_deletion = bool(args.allow_full_deletion)
        self.project_uuid = str(getattr(args, "project_uuid", "") or "").strip()
        self._prepared_boundary_printed = False
        self._resumed_existing_run = False

        if self.operation in {"inspect", "diagnose-observer"}:
            if self.operation == "inspect" and self.service:
                raise HarnessError("inspect does not take --service")
            if self.operation == "diagnose-observer" and not self.service:
                raise HarnessError("diagnose-observer requires --service")
            if self.allow_full_deletion:
                raise HarnessError("--allow-full-deletion applies only to remove-service")
            self.run_dir: Path | None = None
            self.state: dict[str, Any] = {}
            return

        if not self.service:
            raise HarnessError(f"{self.operation} requires --service")
        if self.allow_full_deletion and self.operation != "remove-service":
            raise HarnessError("--allow-full-deletion applies only to remove-service")
        if self.project_uuid and self.operation != "remove-service":
            raise HarnessError("--project-uuid applies only to remove-service")

        existing = self._find_unfinished_run()
        if existing is not None:
            self.run_dir = existing
            self.state = self._read_state()
            self._resumed_existing_run = True
            return

        self.run_dir = (
            self.repo_root
            / "runtime"
            / "state"
            / "fdb"
            / "harness-runs"
            / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{time.time_ns() % 1_000_000_000:09d}"
        )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state = {
            "schema": "main-computer.fdb-mutate-harness.v1",
            "operation": self.operation,
            "network": self.network,
            "service": self.service,
            "allow_full_deletion": self.allow_full_deletion,
            "full_deletion": False,
            "operation_id": None,
            "starting_generation": None,
            "starting_status": None,
            "rebirth": False,
            "cluster_id": secrets.token_hex(8) if self.operation == "create-cluster" else None,
            "target_generation": None,
            "resolved_controller": None,
            "resolved_host": None,
            "resolved_endpoint": None,
            "starting_cluster": None,
            "starting_coordinators": None,
            "target_coordinators": None,
            "coordinators_changed": None,
            "final_cluster": None,
            "final_coordinators": None,
            "last_completed_step": None,
        }
        self._write_state()

    def _find_unfinished_run(self) -> Path | None:
        root = self.repo_root / "runtime" / "state" / "fdb" / "harness-runs"
        if not root.is_dir():
            return None

        candidates: list[tuple[int, str, Path]] = []
        for state_path in root.glob("*/harness-state.json"):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("schema") != "main-computer.fdb-mutate-harness.v1":
                continue
            if payload.get("operation") != self.operation:
                continue
            if payload.get("network") != self.network:
                continue
            if payload.get("service") != self.service:
                continue
            if bool(payload.get("allow_full_deletion", False)) != self.allow_full_deletion:
                continue
            if payload.get("last_completed_step") == "final-inspect":
                continue
            try:
                stamp = state_path.stat().st_mtime_ns
            except OSError:
                stamp = 0
            candidates.append((stamp, state_path.parent.name, state_path.parent))

        if not candidates:
            return None
        candidates.sort(reverse=True)
        return candidates[0][2]

    def control_cmd(self, *parts: str) -> list[str]:
        return [
            sys.executable,
            "-m",
            "tools.fdb_control",
            "--repo-root",
            str(self.repo_root),
            "--json",
            *parts,
        ]

    def prep_cmd(self) -> list[str]:
        command = self.control_cmd(self.operation, "prep", self.network, "--service", self.service)
        if self.operation == "create-cluster":
            command.extend(("--infer-placement", "--cluster-id", self._required_state("cluster_id")))
        if self.operation == "remove-service" and self.allow_full_deletion:
            command.append("--allow-full-deletion")
        if self.operation == "remove-service" and self.project_uuid:
            command.extend(("--project-uuid", self.project_uuid))
        return command

    def do_cmd(self) -> list[str]:
        return self.control_cmd(
            self.operation,
            "do",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )

    def operation_inspect_cmd(self) -> list[str]:
        command = self.control_cmd(
            "inspect",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )
        # A running Coolify service does not imply that its FDB observer has
        # finished bootstrapping and emitted the operation-specific proof.
        # Match the existing finalize verifier's bounded proof wait.  Normal
        # `inspect` remains an immediate, read-only observation.
        if self.operation in {"create-cluster", "add-service"}:
            command.extend(("--wait-timeout-seconds", "300"))
        return command

    def finalize_cmd(self) -> list[str]:
        return self.control_cmd(
            self.operation,
            "finalize",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )

    def inspect_cmd(self) -> list[str]:
        return self.control_cmd("inspect", self.network)

    def _recover_deleted_unaccepted_birth(self) -> None:
        """Resume an unaccepted birth after manual Coolify service deletion.

        Never discard cluster identity, accepted authority, or persistent FDB
        storage. A missing service must be independently observed before the
        frozen *same* transaction is rewound from do to deployment.
        """
        if (
            self.operation != "create-cluster"
            or self.state.get("last_completed_step") != "do"
            or not self._mutation_authorized()
        ):
            return
        from tools.fdb_control.common.models import FdbContext
        from tools.fdb_control.common.state import read_accepted_state, require_operation, update_operation
        from tools.fdb_control.create_cluster import plan_from_operation
        from tools.fdb_control.observer_diagnostics import diagnose_observer

        ctx = FdbContext.from_repo(self.repo_root)
        if read_accepted_state(ctx, self.network) is not None:
            return
        operation_id = self._required_state("operation_id")
        plan, op = plan_from_operation(ctx, self.network, operation_id)
        if op.get("kind") != "create-cluster" or op.get("stage") not in {"deployed", "deploying"}:
            return
        if len(plan.services) != 1 or plan.services[0].service_id != self.service:
            raise HarnessError("unfinished birth operation does not match requested service")
        if plan.cluster.cluster_id != self.state.get("cluster_id"):
            raise HarnessError("unfinished birth cluster identity differs from frozen harness state")
        observation = diagnose_observer(ctx, self.network, self.service)
        if observation.get("status") != "service-not-found":
            return
        # Only local operation metadata is reset. The remote service is already
        # absent; no Coolify deletions and no storage removal are authorized.
        update_operation(
            ctx, self.network, operation_id,
            stage="prepared", deployment_result=None,
            cleanup_result={"status": "externally-deleted", "service_name": f"main-computer-{self.service}"},
        )
        self.state["last_completed_step"] = "prep"
        self.state["birth_service_recovery"] = "missing-service-reuse-frozen-birth"
        self._write_state()
        print("Unaccepted FDB birth service was deleted; reusing the frozen operation and cluster identity.")

    def _prepared_operation(self):
        """Read the frozen control operation, not a possibly stale harness pre-inspect."""
        from tools.fdb_control.common.models import FdbContext
        from tools.fdb_control.common.state import accepted_state_from_wire, require_operation
        from tools.fdb_control.add_service import plan_from_operation as add_plan
        from tools.fdb_control.remove_service import plan_from_operation as remove_plan

        ctx = FdbContext.from_repo(self.repo_root)
        operation_id = self._required_state("operation_id")
        op = require_operation(ctx, self.network, operation_id)
        if op.get("operation_id") != operation_id or op.get("network") != self.network or op.get("kind") != self.operation:
            raise HarnessError("prepared FDB operation identity differs from the harness request")
        if self.operation not in {"add-service", "remove-service"}:
            raise HarnessError("prepared-operation recovery supports add/remove-service only")
        target = _mapping(op.get("target"), "prepared operation target")
        prestate = accepted_state_from_wire(_mapping(target.get("accepted_prestate"), "operation accepted prestate"))
        if prestate.network != self.network:
            raise HarnessError("prepared FDB operation prestate belongs to another network")
        plan, _ = (add_plan if self.operation == "add-service" else remove_plan)(ctx, self.network, operation_id)
        service = plan.added_service if self.operation == "add-service" else plan.removed_service
        if service.service_id != self.service:
            raise HarnessError("prepared FDB operation targets a different service")
        if self.operation == "remove-service" and (not plan.services) != self.allow_full_deletion:
            raise HarnessError("prepared FDB removal scope differs from operator acknowledgement")
        return ctx, op, prestate, plan

    def _bind_prepared_generation(self) -> None:
        """Reconcile an old resume snapshot against its immutable operation prestate.

        The pre-inspect may precede another accepted mutation. Prep is the
        transaction boundary: its frozen accepted prestate is authoritative.
        """
        if self.operation not in {"add-service", "remove-service"}:
            return
        from tools.fdb_control.common.errors import FdbControlError
        from tools.fdb_control.common.state import read_accepted_state
        try:
            ctx, op, prestate, plan = self._prepared_operation()
        except (FdbControlError, KeyError, ValueError, TypeError) as exc:
            raise HarnessError(f"invalid prepared FDB operation: {exc}") from exc
        if op.get("stage") != "finalized" and read_accepted_state(ctx, self.network) != prestate:
            raise HarnessError("accepted FDB topology changed since the prepared operation; refusing to resume mutation")
        target = prestate.generation + 1
        recorded = self.state.get("target_generation")
        if type(recorded) is not int or recorded != target:
            raise HarnessError(f"prepared FDB target generation mismatch: operation={target}, harness={recorded!r}")
        if self.state.get("target_coordinators") != [item.endpoint for item in plan.coordinators]:
            raise HarnessError("prepared FDB coordinator target differs from harness state")
        service = plan.added_service if self.operation == "add-service" else plan.removed_service
        if self.state.get("resolved_host") != service.host_id or self.state.get("resolved_endpoint") != service.endpoint:
            raise HarnessError("prepared FDB placement differs from harness state")
        changed = prestate.coordinators != plan.coordinators
        if self.state.get("coordinators_changed") != changed:
            raise HarnessError("prepared FDB coordinator-change flag differs from operation")
        if self.operation == "add-service" and self.state.get("rebirth") != (not prestate.services):
            raise HarnessError("prepared FDB rebirth flag differs from operation")
        # Correct the *harness cache*, not the frozen operation or accepted state.
        self.state["starting_generation"] = prestate.generation
        self.state["starting_status"] = "accepted-empty" if not prestate.services else "accepted"
        self.state["starting_cluster"] = {
            "description": prestate.cluster.description, "cluster_id": prestate.cluster.cluster_id,
        }
        self.state["starting_coordinators"] = [item.endpoint for item in prestate.coordinators]
        self._write_state()

    def _recover_finalized_operation(self) -> None:
        """Finish bookkeeping for an already-committed operation without redeploying."""
        if self.operation not in {"add-service", "remove-service"}:
            return
        if self.state.get("last_completed_step") not in {"prep", "do", "operation-inspect", "finalize"}:
            return
        from tools.fdb_control.common.models import AcceptedClusterState
        from tools.fdb_control.common.state import read_accepted_state

        ctx, op, prestate, plan = self._prepared_operation()
        if op.get("stage") != "finalized":
            return
        verification = _mapping(op.get("verification"), "finalized operation verification")
        expected_reason = (
            "fdb-add-service-proof-satisfied" if self.operation == "add-service"
            else "fdb-full-deletion-proof-satisfied" if self.state.get("full_deletion")
            else "fdb-remove-service-proof-satisfied"
        )
        if verification.get("verified") is not True or verification.get("reason") != expected_reason:
            raise HarnessError("finalized FDB operation lacks the correct verification proof")
        expected = AcceptedClusterState(
            network=self.network, generation=prestate.generation + 1,
            cluster=plan.cluster, services=plan.services, coordinators=plan.coordinators,
            redundancy_mode=plan.redundancy_mode, storage_engine=plan.storage_engine, retired=False,
        )
        accepted = read_accepted_state(ctx, self.network)
        if accepted != expected or op.get("accepted_generation") != expected.generation:
            raise HarnessError("finalized operation does not match the currently accepted FDB topology")
        # Do not replace the original failed finalize logs. Preserve a separate
        # recovery record demonstrating which committed operation was verified.
        result = {
            "status": "finalized",
            "details": {"operation_id": self._required_state("operation_id"),
                        "verified": True, "accepted_generation": expected.generation},
        }
        self._validate_step("finalize", result)
        (self.run_dir / "04-finalize.recovery.json").write_text(
            json.dumps({"network": self.network, "operation_id": op["operation_id"],
                        "accepted_generation": expected.generation,
                        "source": "verified-finalized-operation"}, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print("Recovered already-finalized FDB operation; no deployment or finalize mutation repeated.")
        self.state["last_completed_step"] = "finalize"
        self._write_state()

    def run(self) -> int:
        if bool(self.args.execute_mutations) != bool(self.args.yes_i_know_this_mutates_fdb):
            raise HarnessError(
                "live mutation requires both --execute-mutations and --yes-i-know-this-mutates-fdb"
            )

        if self.operation in {"inspect", "diagnose-observer"}:
            if self._mutation_authorized():
                raise HarnessError(f"{self.operation} is read-only and does not accept mutation authorization flags")
            return self._run_inspect_only() if self.operation == "inspect" else self._run_observer_diagnosis()

        if self._resumed_existing_run:
            print(f"Resuming unfinished {self.operation} for {self.service}.")
        self._recover_deleted_unaccepted_birth()
        if self._resumed_existing_run and self.state.get("last_completed_step") in {
            "prep", "do", "operation-inspect", "finalize",
        }:
            self._bind_prepared_generation()
            self._recover_finalized_operation()

        completed = self.state.get("last_completed_step")
        start_index = 0 if completed not in STEPS else STEPS.index(str(completed)) + 1

        for step in STEPS[start_index:]:
            if step in MUTATING_STEPS and not self._mutation_authorized():
                if not self._prepared_boundary_printed:
                    self._print_prepared_boundary()
                return 0
            result = self._run_step(step, self._command_for(step))
            self._validate_step(step, result)
            if step == "prep" and self.operation in {"add-service", "remove-service"}:
                self._bind_prepared_generation()
            self._print_step_result(step, result)
            self.state["last_completed_step"] = step
            self._write_state()

        self._print_final_verification()
        print("FDB_MUTATE_HARNESS_COMPLETE")
        return 0

    def _run_observer_diagnosis(self) -> int:
        argv = self.control_cmd("diagnose-observer", self.network, "--service", self.service)
        proc = subprocess.run(argv, cwd=self.repo_root, capture_output=True, text=True, check=False)
        try:
            payload = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            raise HarnessError(f"diagnose-observer returned non-JSON output (exit={proc.returncode})") from exc
        if proc.returncode != 0 or payload.get("ok") is not True:
            raise HarnessError(f"diagnose-observer failed: {payload.get('error')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HarnessError("diagnose-observer did not return a result object")
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    def _run_inspect_only(self) -> int:
        argv = self.control_cmd("inspect", self.network)
        proc = subprocess.run(
            argv,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        try:
            payload = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            raise HarnessError(f"inspect returned non-JSON output (exit={proc.returncode})") from exc
        if proc.returncode != 0 or payload.get("ok") is not True:
            raise HarnessError(f"inspect failed (exit={proc.returncode}): {payload.get('error')}")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HarnessError("inspect did not return a result object")
        self._print_inspection(result)
        return 0

    def _print_inspection(self, result: dict[str, Any]) -> None:
        print("=== FDB inspection ===")
        print(f"network:             {self.network}")
        print(f"status:              {result.get('status')}")
        if result.get("accepted_generation") is not None:
            print(f"accepted generation: {result.get('accepted_generation')}")

        cluster = result.get("cluster")
        if isinstance(cluster, dict):
            description = cluster.get("description")
            cluster_id = cluster.get("cluster_id")
            if description not in (None, ""):
                print(f"cluster:             {description}")
            if cluster_id not in (None, ""):
                print(f"cluster id:          {cluster_id}")

        coordinators = result.get("coordinators")
        coordinator_set = set(coordinators) if isinstance(coordinators, list) else set()
        if coordinator_set:
            print(f"coordinators:        {', '.join(str(item) for item in coordinators)}")
        elif result.get("status") == "accepted-empty":
            print("coordinators:        none")

        services = result.get("services")
        if isinstance(services, list):
            print("services:")
            if not services:
                print("  none")
            for item in services:
                if not isinstance(item, dict):
                    continue
                service_id = str(item.get("service_id") or "?")
                host_id = str(item.get("host_id") or "?")
                endpoint = str(item.get("endpoint") or "?")
                role = " coordinator" if endpoint in coordinator_set else ""
                print(f"  {service_id}  {host_id}  {endpoint}{role}")

        verification = (
            result.get("cluster_verification")
            or result.get("birth_verification")
            or result.get("empty_topology_verification")
        )
        if isinstance(verification, dict):
            if verification.get("reason") == "accepted-empty-topology" and verification.get("verified") is True:
                print("verification:        accepted empty topology")
            else:
                print(f"verification:        {'verified' if verification.get('verified') is True else 'not verified'}")

    def _command_for(self, step: str) -> list[str]:
        if step in {"pre-inspect", "final-inspect"}:
            return self.inspect_cmd()
        if step == "prep":
            return self.prep_cmd()
        if step == "do":
            return self.do_cmd()
        if step == "operation-inspect":
            return self.operation_inspect_cmd()
        if step == "finalize":
            return self.finalize_cmd()
        raise HarnessError(f"unknown step: {step}")

    def _run_step(self, step: str, argv: list[str]) -> dict[str, Any]:
        index = STEPS.index(step)
        prefix = self.run_dir / f"{index:02d}-{step}"
        (prefix.with_suffix(".command.txt")).write_text(quote_command(argv) + "\n", encoding="utf-8")
        print(f"\n=== {step} ===")
        proc = subprocess.run(
            argv,
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        prefix.with_suffix(".stdout.txt").write_text(proc.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
        try:
            payload = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            raise HarnessError(
                f"{step} returned non-JSON output (exit={proc.returncode}); see {prefix.with_suffix('.stdout.txt')}"
            ) from exc
        prefix.with_suffix(".json").write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        if proc.returncode != 0 or payload.get("ok") is not True:
            raise HarnessError(
                f"{step} failed (exit={proc.returncode}): {payload.get('error')}; "
                f"stdout/stderr saved under {prefix}.*"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HarnessError(f"{step} did not return a result object")
        return result

    def _print_step_result(self, step: str, result: dict[str, Any]) -> None:
        """Print the validated control-boundary result without dumping raw JSON."""

        if step in {"pre-inspect", "final-inspect"}:
            self._print_inspect_stage_result(result)
            return

        if step == "prep":
            # _validate_step("prep") already prints the frozen operator boundary.
            return

        if step == "do":
            details = result.get("details")
            details = details if isinstance(details, dict) else {}
            print(f"status:              {result.get('status')}")

            if self.operation in {"add-service", "create-cluster"}:
                if details.get("rebirth"):
                    print("first-service rebirth: yes")
                if details.get("action"):
                    print(f"deployment action:   {details.get('action')}")
                if details.get("waited_for_running") is True:
                    print("service running:     verified")
                if "coordinators_changed" in details:
                    print(
                        "coordinator change:  "
                        f"{'yes' if details.get('coordinators_changed') else 'no'}"
                    )
                coordinators = details.get("target_coordinators")
                if isinstance(coordinators, list):
                    print(
                        "target coordinators: "
                        f"{', '.join(str(item) for item in coordinators) if coordinators else 'none'}"
                    )
                return

            full_deletion = bool(details.get("full_deletion") or self.state.get("full_deletion"))
            if full_deletion:
                print("FDB drain:           skipped (explicit final-copy deletion)")
            elif details.get("safe_withdrawal_verified") is True:
                print("FDB drain:           verified")
            if "coolify_service_deleted" in details:
                print(
                    "Coolify deletion:    "
                    f"{'complete' if details.get('coolify_service_deleted') else 'not complete'}"
                )
            if "endpoint_exclusion_cleared" in details and not full_deletion:
                print(
                    "endpoint exclusion:  "
                    f"{'cleared' if details.get('endpoint_exclusion_cleared') else 'not cleared'}"
                )
            if "coordinators_changed" in details:
                print(
                    "coordinator change:  "
                    f"{'yes' if details.get('coordinators_changed') else 'no'}"
                )
            return

        if step == "operation-inspect":
            key = (
                "birth_verification"
                if self.operation == "create-cluster"
                else ("add_service_verification" if self.operation == "add-service" else "remove_service_verification")
            )
            verification = result.get(key)
            verification = verification if isinstance(verification, dict) else {}
            print(
                "mutation proof:       "
                f"{'verified' if verification.get('verified') is True else 'not verified'}"
            )
            reason = verification.get("reason")
            if reason not in (None, ""):
                print(f"proof reason:         {reason}")
            if self.operation in {"add-service", "create-cluster"}:
                status = verification.get("coolify_status") or verification.get("status")
                if status not in (None, ""):
                    print(f"service status:       {status}")
            else:
                target_status = verification.get("target_status")
                if target_status not in (None, ""):
                    print(f"target status:        {target_status}")
                helper_status = verification.get("helper_status")
                if helper_status not in (None, ""):
                    print(f"helper status:        {helper_status}")
            return

        if step == "finalize":
            details = result.get("details")
            details = details if isinstance(details, dict) else {}
            print(f"status:              {result.get('status')}")
            if details.get("accepted_generation") is not None:
                print(f"accepted generation: {details.get('accepted_generation')}")
            print(
                "accepted authority:  "
                f"{'advanced' if details.get('verified') is True else 'not advanced'}"
            )
            if "consumer_contract_changed" in details:
                print(
                    "consumer contract:   "
                    f"{'changed' if details.get('consumer_contract_changed') else 'unchanged'}"
                )
            if "hub_fdb_rectification_required" in details:
                print(
                    "Hub rectification:   "
                    f"{'required' if details.get('hub_fdb_rectification_required') else 'not required'}"
                )
            return

    def _print_inspect_stage_result(self, result: dict[str, Any]) -> None:
        print(f"status:              {result.get('status')}")
        if result.get("accepted_generation") is not None:
            print(f"accepted generation: {result.get('accepted_generation')}")

        services = result.get("services")
        if isinstance(services, list):
            print(f"services:            {len(services)}")

        coordinators = result.get("coordinators")
        if isinstance(coordinators, list):
            print(
                "coordinators:        "
                f"{', '.join(str(item) for item in coordinators) if coordinators else 'none'}"
            )

        verification = (
            result.get("cluster_verification")
            or result.get("birth_verification")
            or result.get("empty_topology_verification")
        )
        if isinstance(verification, dict):
            print(
                "verification:        "
                f"{'verified' if verification.get('verified') is True else 'not verified'}"
            )

    def _validate_step(self, step: str, result: dict[str, Any]) -> None:
        if step == "pre-inspect":
            status = result.get("status")
            if self.operation == "create-cluster":
                if status != "unborn" or result.get("accepted") is not None:
                    raise HarnessError(f"create-cluster requires an unborn FDB network; observed {status!r}")
                self.state["starting_generation"] = 0
                self.state["starting_status"] = "unborn"
                self.state["starting_cluster"] = None
                self.state["starting_coordinators"] = []
                return
            if self.operation == "add-service" and status == "accepted-empty":
                verification = result.get("empty_topology_verification")
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    raise HarnessError(
                        f"pre-inspect could not verify accepted empty FDB state: {verification}"
                    )
                if result.get("services") != [] or result.get("coordinators") != []:
                    raise HarnessError("accepted-empty pre-inspect exposed non-empty services or coordinators")
            else:
                self._require_accepted_verified(result, label="pre-inspect")
            generation = result.get("accepted_generation")
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise HarnessError("pre-inspect did not report an accepted generation")
            self.state["starting_generation"] = generation
            self.state["starting_status"] = status
            self.state["starting_cluster"] = result.get("cluster")
            self.state["starting_coordinators"] = result.get("coordinators")
            return

        if step == "prep":
            if result.get("status") != "prepared":
                raise HarnessError(f"prep did not reach prepared state: {result}")
            details = _mapping(result.get("details"), "prep.details")
            operation_id = _text(details.get("operation_id"), "prep operation_id")
            self.state["operation_id"] = operation_id
            self.state["target_generation"] = details.get("target_generation")
            if self.operation == "create-cluster":
                target = _mapping(details.get("target"), "prep.details.target")
                request = _mapping(target.get("request"), "prep birth request")
                if request.get("network") != self.network:
                    raise HarnessError("birth prepared for the wrong network")
                cluster = _mapping(request.get("cluster"), "prep birth cluster")
                if cluster.get("description") != f"main_computer_{self.network}" or cluster.get("cluster_id") != self.state.get("cluster_id"):
                    raise HarnessError("birth cluster identity differs from frozen harness intent")
                services = request.get("services")
                if not isinstance(services, list) or len(services) != 1 or not isinstance(services[0], dict):
                    raise HarnessError("birth prep must contain exactly one service")
                service = services[0]
                if service.get("service_id") != self.service:
                    raise HarnessError("birth prep resolved a different service identity")
                host = _text(service.get("host_id"), "birth host")
                endpoint = f"{_text(service.get('address'), 'birth address')}:{service.get('port')}"
                if request.get("coordinator_service_ids") != [self.service] or request.get("redundancy_mode") != "single":
                    raise HarnessError("initial birth must have exactly one coordinator and single redundancy")
                self.state["resolved_host"] = host
                self.state["resolved_controller"] = host
                self.state["resolved_endpoint"] = endpoint
                self.state["target_generation"] = 1
                self.state["target_coordinators"] = [endpoint]
                self.state["coordinators_changed"] = True
                self._print_prepared_boundary()
                self._prepared_boundary_printed = True
                return
            target_coordinators = details.get("target_coordinators")
            full_deletion = bool(details.get("full_deletion"))
            if not isinstance(target_coordinators, list):
                raise HarnessError("prep did not freeze a target coordinator set")
            if not target_coordinators and not (self.operation == "remove-service" and full_deletion):
                raise HarnessError("prep froze an empty coordinator set without full deletion")
            if full_deletion and not self.allow_full_deletion:
                raise HarnessError("prep opened full deletion without operator acknowledgement")
            self.state["full_deletion"] = full_deletion
            self.state["rebirth"] = bool(details.get("rebirth"))
            self.state["target_coordinators"] = target_coordinators
            self.state["coordinators_changed"] = bool(details.get("coordinators_changed"))
            if self.operation == "add-service":
                controller_id = details.get("controller_id")
                if controller_id not in (None, ""):
                    self.state["resolved_controller"] = _text(controller_id, "prep controller_id")
                self.state["resolved_host"] = _text(details.get("host_id"), "prep host_id")
                self.state["resolved_endpoint"] = _text(details.get("endpoint"), "prep endpoint")
                if details.get("service_id") != self.service:
                    raise HarnessError("prep resolved a different service identity")
            else:
                self.state["resolved_host"] = _text(details.get("host_id"), "prep host_id")
                self.state["resolved_endpoint"] = _text(details.get("service_endpoint"), "prep service_endpoint")
                if details.get("service_id") != self.service:
                    raise HarnessError("remove prep resolved a different service identity")
            self._print_prepared_boundary()
            self._prepared_boundary_printed = True
            return

        if step == "do":
            wanted = "deployed" if self.operation in {"add-service", "create-cluster"} else "removed"
            if result.get("status") not in {wanted, "already-finalized", "finalized"}:
                raise HarnessError(f"do did not reach {wanted!r}: {result}")
            return

        if step == "operation-inspect":
            key = ("birth_verification" if self.operation == "create-cluster" else
                   "add_service_verification" if self.operation == "add-service" else "remove_service_verification")
            verification = _mapping(result.get(key), key)
            if verification.get("verified") is not True:
                raise HarnessError(f"{key} did not verify the requested mutation: {verification}")
            if self.operation == "create-cluster":
                if verification.get("reason") != "fdb-observer-proof-satisfied":
                    raise HarnessError("birth proof was not satisfied by the FDB observer")
                if verification.get("service_id") != self.service:
                    raise HarnessError("birth proof verifies a different service")
                return
            expected_reason = (
                "fdb-add-service-proof-satisfied"
                if self.operation == "add-service"
                else (
                    "fdb-full-deletion-proof-satisfied"
                    if self.state.get("full_deletion")
                    else "fdb-remove-service-proof-satisfied"
                )
            )
            if verification.get("reason") != expected_reason:
                raise HarnessError(f"unexpected FDB proof reason: {verification.get('reason')!r}")
            return

        if step == "finalize":
            if result.get("status") != "finalized":
                raise HarnessError(f"finalize did not reach finalized state: {result}")
            details = _mapping(result.get("details"), "finalize.details")
            if details.get("verified") is not True:
                raise HarnessError("finalize did not report verified=true")
            expected_generation = int(self._required_state("target_generation"))
            if self.operation == "create-cluster":
                # Birth finalize publishes generation 1. The control-layer result
                # exposes accepted_state_path, not accepted_generation.
                if not details.get("accepted_state_path"):
                    raise HarnessError("birth finalize did not publish an accepted state")
                return
            if details.get("accepted_generation") != expected_generation:
                raise HarnessError(
                    f"finalize generation mismatch: expected {expected_generation}, got {details.get('accepted_generation')}"
                )
            return

        if step == "final-inspect":
            if self.operation == "create-cluster":
                self._require_accepted_verified(result, label="birth final-inspect")
                if result.get("accepted_generation") != 1:
                    raise HarnessError("newly born FDB cluster must have accepted generation 1")
                cluster = _mapping(result.get("cluster"), "birth accepted cluster")
                if cluster.get("description") != f"main_computer_{self.network}" or cluster.get("cluster_id") != self.state.get("cluster_id"):
                    raise HarnessError("accepted birth identity differs from frozen harness intent")
                if result.get("coordinators") != self.state.get("target_coordinators"):
                    raise HarnessError("accepted birth coordinator differs from prepared coordinator")
                services = result.get("services")
                if not isinstance(services, list) or len(services) != 1 or not isinstance(services[0], dict):
                    raise HarnessError("accepted birth must contain exactly one service")
                service = services[0]
                if service.get("service_id") != self.service or service.get("host_id") != self.state.get("resolved_host") or service.get("endpoint") != self.state.get("resolved_endpoint"):
                    raise HarnessError("accepted birth service differs from prepared placement")
                self.state["final_cluster"] = cluster
                self.state["final_coordinators"] = result["coordinators"]
                return
            full_deletion = bool(self.state.get("full_deletion"))
            if full_deletion:
                if result.get("status") != "accepted-empty":
                    raise HarnessError(
                        f"final-inspect expected accepted-empty after full deletion; observed {result.get('status')!r}"
                    )
                verification = result.get("empty_topology_verification")
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    raise HarnessError(f"final-inspect did not verify accepted empty topology: {verification}")
            else:
                self._require_accepted_verified(result, label="final-inspect")
            expected_generation = int(self._required_state("target_generation"))
            if result.get("accepted_generation") != expected_generation:
                raise HarnessError(
                    f"final inspect generation mismatch: expected {expected_generation}, got {result.get('accepted_generation')}"
                )
            final_cluster = result.get("cluster")
            final_coordinators = result.get("coordinators")
            target_coordinators = self.state.get("target_coordinators")
            if final_coordinators != target_coordinators:
                raise HarnessError(
                    f"final coordinator set differs from frozen target: expected {target_coordinators!r}, got {final_coordinators!r}"
                )
            start_cluster = self.state.get("starting_cluster")
            if not isinstance(start_cluster, dict) or not isinstance(final_cluster, dict):
                raise HarnessError("inspect did not expose cluster identity")
            if final_cluster.get("description") != start_cluster.get("description"):
                raise HarnessError("cluster description changed during service mutation")
            coordinator_changed = bool(self.state.get("coordinators_changed"))
            cluster_id_changed = final_cluster.get("cluster_id") != start_cluster.get("cluster_id")
            if full_deletion:
                if cluster_id_changed:
                    raise HarnessError("full deletion must preserve historical cluster identity")
            elif self.state.get("rebirth"):
                if self.state.get("starting_status") != "accepted-empty":
                    raise HarnessError("rebirth was prepared without an accepted-empty prestate")
                if cluster_id_changed:
                    raise HarnessError("first-service rebirth must preserve historical cluster identity")
            elif cluster_id_changed != coordinator_changed:
                raise HarnessError(
                    "cluster id must change exactly when the frozen coordinator topology changes"
                )
            self.state["final_cluster"] = final_cluster
            self.state["final_coordinators"] = final_coordinators
            services = result.get("services")
            if not isinstance(services, list):
                raise HarnessError("final inspect did not expose accepted services")
            matches = [item for item in services if isinstance(item, dict) and item.get("service_id") == self.service]
            if self.operation == "add-service":
                if len(matches) != 1:
                    raise HarnessError(f"final inspect did not contain exactly one added service {self.service!r}")
                target = matches[0]
                if target.get("host_id") != self.state.get("resolved_host"):
                    raise HarnessError("final accepted host differs from prepared host")
                if target.get("endpoint") != self.state.get("resolved_endpoint"):
                    raise HarnessError("final accepted endpoint differs from prepared endpoint")
            elif matches:
                raise HarnessError(f"removed service {self.service!r} is still accepted")
            return

    def _require_accepted_verified(self, result: dict[str, Any], *, label: str) -> None:
        if result.get("status") != "accepted":
            raise HarnessError(f"{label} requires an accepted FDB cluster; observed status={result.get('status')!r}")
        verification = result.get("cluster_verification") or result.get("birth_verification")
        if not isinstance(verification, dict) or verification.get("verified") is not True:
            raise HarnessError(f"{label} could not independently verify accepted FDB state: {verification}")

    def _mutation_authorized(self) -> bool:
        return bool(self.args.execute_mutations and self.args.yes_i_know_this_mutates_fdb)

    def _print_prepared_boundary(self) -> None:
        print("\n=== resolved FDB mutation ===")
        print(f"operation:           {self.operation}")
        print(f"network:             {self.network}")
        print(f"service:             {self.service}")
        if self.state.get("resolved_controller"):
            print(f"logical controller:  {self.state['resolved_controller']}")
        if self.state.get("resolved_host"):
            print(f"resolved host:       {self.state['resolved_host']}")
        if self.state.get("resolved_endpoint"):
            print(f"resolved endpoint:   {self.state['resolved_endpoint']}")
        if self.state.get("starting_generation") is not None:
            print(f"accepted generation: {self.state['starting_generation']}")
        if self.state.get("target_coordinators") is not None:
            coordinators = self.state["target_coordinators"]
            print(f"target coordinators:  {', '.join(coordinators) if coordinators else 'none'}")
            print(f"coordinator change:   {'yes' if self.state.get('coordinators_changed') else 'no'}")
        if self.state.get("full_deletion"):
            print("full deletion:        yes")
        if self.state.get("rebirth"):
            print("first-service rebirth: yes")
        if not self._mutation_authorized():
            print("\nStopping before live mutation.")
            print("Rerun the same operation with mutation authorization:")
            print(
                "python .\\fdb_mutate_harness.py "
                f"{self.operation} --network {self.network} --service {self.service} "
                f"{'--allow-full-deletion ' if self.allow_full_deletion else ''}"
                "--execute-mutations --yes-i-know-this-mutates-fdb"
            )

    def _print_final_verification(self) -> None:
        print("\n=== final FDB verification ===")
        print(f"accepted generation: {self.state.get('target_generation')}")
        print(f"service:             {self.service}")
        final_coordinators = self.state.get("final_coordinators") or []
        print(f"coordinators:        {', '.join(final_coordinators) if final_coordinators else 'none'}")
        print(f"coordinator change:  {'yes' if self.state.get('coordinators_changed') else 'no'}")
        if self.state.get("full_deletion"):
            print("full deletion:       yes")
        if self.state.get("rebirth"):
            print("first-service rebirth: yes")
        cluster = self.state.get("final_cluster")
        if isinstance(cluster, dict):
            print(f"cluster id:           {cluster.get('cluster_id')}")
        print("accepted state:      verified")

    def _required_state(self, key: str) -> str:
        value = self.state.get(key)
        if value in (None, ""):
            raise HarnessError(f"harness state is missing required value: {key}")
        return str(value)

    def _read_state(self) -> dict[str, Any]:
        if self.run_dir is None:
            raise HarnessError("inspect has no mutation harness state")
        path = self.run_dir / "harness-state.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise HarnessError(f"could not read resume state {path}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "main-computer.fdb-mutate-harness.v1":
            raise HarnessError(f"unsupported harness state: {path}")
        return payload

    def _write_state(self) -> None:
        if self.run_dir is None:
            raise HarnessError("inspect has no mutation harness state")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "harness-state.json"
        temp = path.with_name(path.name + f".tmp-{os.getpid()}")
        temp.write_text(json.dumps(self.state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HarnessError(f"{label} is not an object")
    return value


def _text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HarnessError(f"{label} is missing")
    return text


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return Harness(args).run()
    except HarnessError as exc:
        print(f"FDB_MUTATE_HARNESS_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
