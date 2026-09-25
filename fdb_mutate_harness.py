#!/usr/bin/env python3
"""Operator harness for FoundationDB inspection and topology mutation.

This file is the normal FDB operator surface.  Operators name the intent and
logical service; the harness owns run directories, operation IDs, low-level
FDB Control invocations, resume state, verification, and finalization.

Normal mutation lifecycle:

    accepted/live pre-inspect -> prep -> mutation gate -> do
    -> operation-scoped FDB proof -> finalize -> accepted/live post-inspect

A second invocation of the same high-level command automatically resumes the
matching unfinished harness run.  Low-level ``tools.fdb_control`` commands are
retained only as internal implementation/evidence and are not printed during
normal operation.
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
    parser.add_argument("operation", choices=("inspect", "add-service", "remove-service"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--service")
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
        self._prepared_boundary_printed = False
        self._resumed_existing_run = False

        if self.operation == "inspect":
            if self.service:
                raise HarnessError("inspect does not take --service")
            self.run_dir: Path | None = None
            self.state: dict[str, Any] = {}
            return

        if not self.service:
            raise HarnessError(f"{self.operation} requires --service")

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
            "operation_id": None,
            "starting_generation": None,
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
        return self.control_cmd(self.operation, "prep", self.network, "--service", self.service)

    def do_cmd(self) -> list[str]:
        return self.control_cmd(
            self.operation,
            "do",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )

    def operation_inspect_cmd(self) -> list[str]:
        return self.control_cmd(
            "inspect",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )

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

    def run(self) -> int:
        if bool(self.args.execute_mutations) != bool(self.args.yes_i_know_this_mutates_fdb):
            raise HarnessError(
                "live mutation requires both --execute-mutations and --yes-i-know-this-mutates-fdb"
            )

        if self.operation == "inspect":
            if self._mutation_authorized():
                raise HarnessError("inspect is read-only and does not accept mutation authorization flags")
            return self._run_inspect_only()

        if self._resumed_existing_run:
            print(f"Resuming unfinished {self.operation} for {self.service}.")

        completed = self.state.get("last_completed_step")
        start_index = 0 if completed not in STEPS else STEPS.index(str(completed)) + 1

        for step in STEPS[start_index:]:
            if step in MUTATING_STEPS and not self._mutation_authorized():
                if not self._prepared_boundary_printed:
                    self._print_prepared_boundary()
                return 0
            result = self._run_step(step, self._command_for(step))
            self._validate_step(step, result)
            self.state["last_completed_step"] = step
            self._write_state()

        self._print_final_verification()
        print("FDB_MUTATE_HARNESS_COMPLETE")
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

        services = result.get("services")
        if isinstance(services, list):
            print("services:")
            for item in services:
                if not isinstance(item, dict):
                    continue
                service_id = str(item.get("service_id") or "?")
                host_id = str(item.get("host_id") or "?")
                endpoint = str(item.get("endpoint") or "?")
                role = " coordinator" if endpoint in coordinator_set else ""
                print(f"  {service_id}  {host_id}  {endpoint}{role}")

        verification = result.get("cluster_verification") or result.get("birth_verification")
        if isinstance(verification, dict):
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

    def _validate_step(self, step: str, result: dict[str, Any]) -> None:
        if step == "pre-inspect":
            self._require_accepted_verified(result, label="pre-inspect")
            generation = result.get("accepted_generation")
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise HarnessError("pre-inspect did not report an accepted generation")
            self.state["starting_generation"] = generation
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
            target_coordinators = details.get("target_coordinators")
            if not isinstance(target_coordinators, list) or not target_coordinators:
                raise HarnessError("prep did not freeze a non-empty target coordinator set")
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
            wanted = "deployed" if self.operation == "add-service" else "removed"
            if result.get("status") not in {wanted, "already-finalized", "finalized"}:
                raise HarnessError(f"do did not reach {wanted!r}: {result}")
            return

        if step == "operation-inspect":
            key = "add_service_verification" if self.operation == "add-service" else "remove_service_verification"
            verification = _mapping(result.get(key), key)
            if verification.get("verified") is not True:
                raise HarnessError(f"{key} did not verify the requested mutation: {verification}")
            expected_reason = (
                "fdb-add-service-proof-satisfied"
                if self.operation == "add-service"
                else "fdb-remove-service-proof-satisfied"
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
            expected_generation = int(self._required_state("starting_generation")) + 1
            if details.get("accepted_generation") != expected_generation:
                raise HarnessError(
                    f"finalize generation mismatch: expected {expected_generation}, got {details.get('accepted_generation')}"
                )
            return

        if step == "final-inspect":
            self._require_accepted_verified(result, label="final-inspect")
            expected_generation = int(self._required_state("starting_generation")) + 1
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
            if cluster_id_changed != coordinator_changed:
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
            print(f"target coordinators:  {', '.join(self.state['target_coordinators'])}")
            print(f"coordinator change:   {'yes' if self.state.get('coordinators_changed') else 'no'}")
        if not self._mutation_authorized():
            print("\nStopping before live mutation.")
            print("Rerun the same operation with mutation authorization:")
            print(
                "python .\\fdb_mutate_harness.py "
                f"{self.operation} --network {self.network} --service {self.service} "
                "--execute-mutations --yes-i-know-this-mutates-fdb"
            )

    def _print_final_verification(self) -> None:
        print("\n=== final FDB verification ===")
        print(f"accepted generation: {self.state.get('target_generation')}")
        print(f"service:             {self.service}")
        print(f"coordinators:        {', '.join(self.state.get('final_coordinators') or [])}")
        print(f"coordinator change:  {'yes' if self.state.get('coordinators_changed') else 'no'}")
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
