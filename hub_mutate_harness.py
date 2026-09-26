#!/usr/bin/env python3
"""Operator harness for Hub inspection and membership mutation.

This file is the normal Hub operator surface. Operators state only lifecycle
intent and logical Hub identity; the harness owns run directories, operation
IDs, internal Hub Control stages, resume state, verification, and finalization.

Normal mutation lifecycle:

    accepted/observed pre-inspect -> prep -> mutation gate -> do
    -> operation-scoped proof -> finalize -> accepted/observed post-inspect

The lower-level ``tools.hub_control`` package is an internal implementation
boundary. Operators should not invoke its stage commands directly.
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


class HarnessError(RuntimeError):
    pass


def quote_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(subprocess.list2cmdline([arg]) for arg in argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Main Computer Hub operator harness")
    parser.add_argument("operation", choices=("inspect", "add-hub", "remove-hub"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--hub")
    parser.add_argument("--allow-full-deletion", action="store_true")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--execute-mutations", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-hub", action="store_true")
    return parser


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(args.repo_root).resolve()
        self.operation = str(args.operation)
        self.network = str(args.network)
        self.hub = str(args.hub or "").strip()
        self.allow_full_deletion = bool(args.allow_full_deletion)
        self._prepared_boundary_printed = False
        self._resumed_existing_run = False

        if self.operation == "inspect":
            if self.hub:
                raise HarnessError("inspect does not take --hub")
            if self.allow_full_deletion:
                raise HarnessError("--allow-full-deletion applies only to remove-hub")
            self.run_dir: Path | None = None
            self.state: dict[str, Any] = {}
            return

        if not self.hub:
            raise HarnessError(f"{self.operation} requires --hub")
        if self.allow_full_deletion and self.operation != "remove-hub":
            raise HarnessError("--allow-full-deletion applies only to remove-hub")

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
            / "hub"
            / "harness-runs"
            / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{time.time_ns() % 1_000_000_000:09d}"
        )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state = {
            "schema": "main-computer.hub-mutate-harness.v1",
            "operation": self.operation,
            "network": self.network,
            "hub": self.hub,
            "allow_full_deletion": self.allow_full_deletion,
            "full_deletion": False,
            "rebirth": False,
            "operation_id": None,
            "starting_generation": None,
            "starting_status": None,
            "target_generation": None,
            "starting_hub_count": None,
            "target_hub_count": None,
            "resolved_controller": None,
            "resolved_host": None,
            "fdb_contract": None,
            "chain_contract": None,
            "last_completed_step": None,
        }
        self._write_state()

    def _find_unfinished_run(self) -> Path | None:
        root = self.repo_root / "runtime" / "state" / "hub" / "harness-runs"
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
            if payload.get("schema") != "main-computer.hub-mutate-harness.v1":
                continue
            if payload.get("operation") != self.operation:
                continue
            if payload.get("network") != self.network:
                continue
            if payload.get("hub") != self.hub:
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
            "tools.hub_control",
            "--repo-root",
            str(self.repo_root),
            "--json",
            *parts,
        ]

    def inspect_cmd(self) -> list[str]:
        return self.control_cmd("inspect", self.network)

    def prep_cmd(self) -> list[str]:
        command = self.control_cmd(self.operation, "prep", self.network, "--hub", self.hub)
        if self.operation == "remove-hub" and self.allow_full_deletion:
            command.append("--allow-full-deletion")
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

    def run(self) -> int:
        if bool(self.args.execute_mutations) != bool(self.args.yes_i_know_this_mutates_hub):
            raise HarnessError(
                "live mutation requires both --execute-mutations and --yes-i-know-this-mutates-hub"
            )

        if self.operation == "inspect":
            if self._mutation_authorized():
                raise HarnessError("inspect is read-only and does not accept mutation authorization flags")
            return self._run_inspect_only()

        if self._resumed_existing_run:
            print(f"Resuming unfinished {self.operation} for {self.hub}.")

        completed = self.state.get("last_completed_step")
        start_index = 0 if completed not in STEPS else STEPS.index(str(completed)) + 1
        for step in STEPS[start_index:]:
            if step in MUTATING_STEPS and not self._mutation_authorized():
                if not self._prepared_boundary_printed:
                    self._print_prepared_boundary()
                return 0
            result = self._run_step(step, self._command_for(step))
            self._validate_step(step, result)
            self._print_step_result(step, result)
            self.state["last_completed_step"] = step
            self._write_state()

        self._print_final_verification()
        print("HUB_MUTATE_HARNESS_COMPLETE")
        return 0

    def _run_inspect_only(self) -> int:
        proc = subprocess.run(
            self.inspect_cmd(),
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = _parse_payload(proc, label="inspect")
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HarnessError("inspect did not return a result object")
        self._print_inspection(result)
        return 0

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
        if self.run_dir is None:
            raise HarnessError("mutation step has no harness run directory")
        index = STEPS.index(step)
        prefix = self.run_dir / f"{index:02d}-{step}"
        prefix.with_suffix(".command.txt").write_text(quote_command(argv) + "\n", encoding="utf-8")
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
            status = result.get("status")
            if self.operation == "add-hub" and status in {"unborn", "accepted-empty"}:
                key = "unborn_topology_verification" if status == "unborn" else "empty_topology_verification"
                verification = result.get(key)
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    raise HarnessError(f"pre-inspect could not verify {status} Hub state: {verification}")
            else:
                self._require_accepted_verified(result, label="pre-inspect")
            generation = result.get("accepted_generation")
            if not isinstance(generation, int) or isinstance(generation, bool):
                raise HarnessError("pre-inspect did not report an accepted generation")
            hubs = result.get("hubs")
            if not isinstance(hubs, list):
                raise HarnessError("pre-inspect did not expose accepted hubs")
            self.state["starting_generation"] = generation
            self.state["starting_status"] = status
            self.state["starting_hub_count"] = len(hubs)
            return

        if step == "prep":
            if result.get("status") != "prepared":
                raise HarnessError(f"prep did not reach prepared state: {result}")
            details = _mapping(result.get("details"), "prep.details")
            self.state["operation_id"] = _text(details.get("operation_id"), "prep operation_id")
            self.state["target_generation"] = details.get("target_generation")
            self.state["target_hub_count"] = details.get("target_hub_count")
            self.state["full_deletion"] = bool(details.get("full_deletion"))
            self.state["rebirth"] = bool(details.get("rebirth"))
            if self.state["full_deletion"] and not self.allow_full_deletion:
                raise HarnessError("prep opened full Hub deletion without operator acknowledgement")
            if details.get("hub_id") != self.hub:
                raise HarnessError("prep resolved a different Hub identity")
            controller = details.get("controller_id")
            host = details.get("host_id")
            if controller not in (None, ""):
                self.state["resolved_controller"] = _text(controller, "prep controller_id")
            if host not in (None, ""):
                self.state["resolved_host"] = _text(host, "prep host_id")
            self.state["fdb_contract"] = details.get("fdb_contract")
            self.state["chain_contract"] = details.get("chain_contract")
            self.state["chain_preflight"] = details.get("chain_preflight")
            self._print_prepared_boundary()
            self._prepared_boundary_printed = True
            return

        if step == "do":
            wanted = "deployed" if self.operation == "add-hub" else "removed"
            if result.get("status") not in {wanted, "already-finalized", "finalized"}:
                raise HarnessError(f"do did not reach {wanted!r}: {result}")
            return

        if step == "operation-inspect":
            key = "hub_add_verification" if self.operation == "add-hub" else "hub_remove_verification"
            verification = _mapping(result.get(key), key)
            if verification.get("verified") is not True:
                raise HarnessError(f"{key} did not verify the requested mutation: {verification}")
            return

        if step == "finalize":
            if result.get("status") != "finalized":
                raise HarnessError(f"finalize did not reach finalized state: {result}")
            details = _mapping(result.get("details"), "finalize.details")
            if details.get("verified") is not True:
                raise HarnessError("finalize did not report verified=true")
            expected = int(self._required_state("starting_generation")) + 1
            if details.get("accepted_generation") != expected:
                raise HarnessError(
                    f"finalize generation mismatch: expected {expected}, got {details.get('accepted_generation')}"
                )
            return

        if step == "final-inspect":
            full_deletion = bool(self.state.get("full_deletion"))
            if full_deletion:
                if result.get("status") != "accepted-empty":
                    raise HarnessError(
                        f"final-inspect expected accepted-empty after full deletion; observed {result.get('status')!r}"
                    )
                verification = result.get("empty_topology_verification")
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    raise HarnessError(f"final-inspect did not verify accepted-empty Hub state: {verification}")
            else:
                self._require_accepted_verified(result, label="final-inspect")
            expected = int(self._required_state("starting_generation")) + 1
            if result.get("accepted_generation") != expected:
                raise HarnessError(
                    f"final inspect generation mismatch: expected {expected}, got {result.get('accepted_generation')}"
                )
            hubs = result.get("hubs")
            if not isinstance(hubs, list):
                raise HarnessError("final inspect did not expose accepted hubs")
            matches = [item for item in hubs if isinstance(item, dict) and item.get("hub_id") == self.hub]
            if self.operation == "add-hub":
                if len(matches) != 1:
                    raise HarnessError(f"final inspect did not contain exactly one added Hub {self.hub!r}")
                if self.state.get("resolved_host") and matches[0].get("host_id") != self.state.get("resolved_host"):
                    raise HarnessError("final accepted Hub host differs from prepared host")
            elif matches:
                raise HarnessError(f"removed Hub {self.hub!r} is still accepted")
            if self.state.get("target_hub_count") is not None and len(hubs) != self.state.get("target_hub_count"):
                raise HarnessError(
                    f"final Hub count differs from frozen target: expected {self.state.get('target_hub_count')}, got {len(hubs)}"
                )
            return

    def _require_accepted_verified(self, result: dict[str, Any], *, label: str) -> None:
        if result.get("status") != "accepted":
            raise HarnessError(f"{label} requires an accepted Hub topology; observed status={result.get('status')!r}")
        verification = result.get("topology_verification")
        if not isinstance(verification, dict) or verification.get("verified") is not True:
            raise HarnessError(f"{label} could not independently verify accepted Hub state: {verification}")

    def _mutation_authorized(self) -> bool:
        return bool(self.args.execute_mutations and self.args.yes_i_know_this_mutates_hub)

    def _print_step_result(self, step: str, result: dict[str, Any]) -> None:
        if step in {"pre-inspect", "final-inspect"}:
            self._print_inspect_stage_result(result)
            return
        if step == "prep":
            return
        if step == "do":
            details = result.get("details") if isinstance(result.get("details"), dict) else {}
            print(f"status:               {result.get('status')}")
            if details.get("rebirth"):
                print("first-Hub rebirth:    yes")
            if details.get("deployment_action"):
                print(f"deployment action:    {details.get('deployment_action')}")
            if "hub_running" in details:
                print(f"Hub running:          {'verified' if details.get('hub_running') else 'not verified'}")
            if "fdb_adoption_verified" in details:
                print(f"FDB adoption:         {'verified' if details.get('fdb_adoption_verified') else 'not verified'}")
            if "chain_adoption_verified" in details:
                print(f"Chain adoption:       {'verified' if details.get('chain_adoption_verified') else 'not verified'}")
            if "deployment_deleted" in details:
                print(f"deployment deletion:  {'complete' if details.get('deployment_deleted') else 'not complete'}")
            return
        if step == "operation-inspect":
            key = "hub_add_verification" if self.operation == "add-hub" else "hub_remove_verification"
            verification = result.get(key) if isinstance(result.get(key), dict) else {}
            print(f"mutation proof:       {'verified' if verification.get('verified') is True else 'not verified'}")
            if verification.get("reason") not in (None, ""):
                print(f"proof reason:         {verification.get('reason')}")
            return
        if step == "finalize":
            details = result.get("details") if isinstance(result.get("details"), dict) else {}
            print(f"status:               {result.get('status')}")
            if details.get("accepted_generation") is not None:
                print(f"accepted generation:  {details.get('accepted_generation')}")
            print(f"accepted authority:   {'advanced' if details.get('verified') is True else 'not advanced'}")

    def _print_inspect_stage_result(self, result: dict[str, Any]) -> None:
        print(f"status:               {result.get('status')}")
        if result.get("accepted_generation") is not None:
            print(f"accepted generation:  {result.get('accepted_generation')}")
        hubs = result.get("hubs")
        if isinstance(hubs, list):
            print(f"hubs:                 {len(hubs)}")
        verification = (
            result.get("topology_verification")
            or result.get("empty_topology_verification")
            or result.get("unborn_topology_verification")
        )
        if isinstance(verification, dict):
            print(f"verification:         {'verified' if verification.get('verified') is True else 'not verified'}")

    def _print_inspection(self, result: dict[str, Any]) -> None:
        print("=== Hub inspection ===")
        print(f"network:              {self.network}")
        print(f"status:               {result.get('status')}")
        if result.get("accepted_generation") is not None:
            print(f"accepted generation:  {result.get('accepted_generation')}")
        hubs = result.get("hubs")
        if isinstance(hubs, list):
            print("hubs:")
            if not hubs:
                print("  none")
            for item in hubs:
                if not isinstance(item, dict):
                    continue
                hub_id = str(item.get("hub_id") or "?")
                host_id = str(item.get("host_id") or "?")
                status = str(item.get("status") or item.get("deployment_status") or "unknown")
                print(f"  {hub_id}  {host_id}  {status}")
        self._print_contract_line("FDB contract", result.get("fdb_contract"))
        self._print_contract_line("Chain contract", result.get("chain_contract"))
        verification = (
            result.get("topology_verification")
            or result.get("empty_topology_verification")
            or result.get("unborn_topology_verification")
        )
        if isinstance(verification, dict):
            print(f"verification:         {'verified' if verification.get('verified') is True else 'not verified'}")

    def _print_contract_line(self, label: str, contract: object) -> None:
        if not isinstance(contract, dict):
            return
        generation = contract.get("generation")
        status = contract.get("status")
        parts = []
        if generation is not None:
            parts.append(f"generation {generation}")
        if status not in (None, ""):
            parts.append(str(status))
        if parts:
            print(f"{label + ':':21}{' / '.join(parts)}")

    def _print_prepared_boundary(self) -> None:
        print("\n=== resolved Hub mutation ===")
        print(f"operation:            {self.operation}")
        print(f"network:              {self.network}")
        print(f"hub:                  {self.hub}")
        if self.state.get("resolved_controller"):
            print(f"logical controller:   {self.state['resolved_controller']}")
        if self.state.get("resolved_host"):
            print(f"resolved host:        {self.state['resolved_host']}")
        if self.state.get("starting_generation") is not None:
            print(f"accepted generation:  {self.state['starting_generation']}")
        if self.state.get("target_hub_count") is not None:
            print(f"target Hub count:     {self.state['target_hub_count']}")
        self._print_contract_line("FDB contract", self.state.get("fdb_contract"))
        self._print_contract_line("Chain contract", self.state.get("chain_contract"))
        chain_preflight = self.state.get("chain_preflight")
        if isinstance(chain_preflight, dict):
            print("Chain preflight:")
            print(f"  RPC:                 {'verified' if chain_preflight.get('verified') is True else 'not verified'}")
            if chain_preflight.get("chain_id") is not None:
                print(f"  chain ID:            {chain_preflight.get('chain_id')}")
            print(
                "  core requirements:   "
                + ("verified" if chain_preflight.get("required_contracts_verified") is True else "not verified")
            )
            stale = chain_preflight.get("optional_stale_contracts")
            if isinstance(stale, list):
                print(f"  advertised stale:    {', '.join(str(item) for item in stale) if stale else 'none'}")
        if self.state.get("fdb_contract"):
            print("FDB preflight:         contract verified")
        if self.state.get("full_deletion"):
            print("full deletion:         yes")
        if self.state.get("rebirth"):
            print("first-Hub rebirth:    yes")
        if not self._mutation_authorized():
            print("\nStopping before live mutation.")
            print("Rerun the same operation with mutation authorization:")
            print(
                "python .\\hub_mutate_harness.py "
                f"{self.operation} --network {self.network} --hub {self.hub} "
                f"{'--allow-full-deletion ' if self.allow_full_deletion else ''}"
                "--execute-mutations --yes-i-know-this-mutates-hub"
            )

    def _print_final_verification(self) -> None:
        print("\n=== final Hub verification ===")
        print(f"accepted generation:  {self.state.get('target_generation')}")
        print(f"hub:                  {self.hub}")
        if self.state.get("full_deletion"):
            print("full deletion:         yes")
        if self.state.get("rebirth"):
            print("first-Hub rebirth:    yes")
        print("accepted state:       verified")

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
        if not isinstance(payload, dict) or payload.get("schema") != "main-computer.hub-mutate-harness.v1":
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


def _parse_payload(proc: subprocess.CompletedProcess[str], *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(proc.stdout.strip())
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{label} returned non-JSON output (exit={proc.returncode})") from exc
    if proc.returncode != 0 or payload.get("ok") is not True:
        raise HarnessError(f"{label} failed (exit={proc.returncode}): {payload.get('error')}")
    return payload


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
        print(f"HUB_MUTATE_HARNESS_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
