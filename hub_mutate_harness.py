#!/usr/bin/env python3
"""Operator harness for Hub inspection, membership and code redeployment.

This file is the normal Hub operator surface. Operators state only lifecycle
intent and logical Hub identity; the harness owns run directories, operation
IDs, internal Hub Control stages, resume state, verification, and finalization.

Normal mutation lifecycle:

    read-only topology gate -> accepted/observed pre-inspect -> prep
    -> mutation gate -> do
    -> operation-scoped proof -> finalize -> accepted/observed post-inspect

The lower-level ``tools.hub_control`` package is an internal implementation
boundary. Operators should not invoke its stage commands directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
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
ADD_HUB_REPREP_DO_ERROR_CODES = frozenset(
    {
        "HUB_BRIDGE_ESCROW_NOT_LIVE",
        "HUB_BRIDGE_ESCROW_INTERFACE_INVALID",
        "HUB_BRIDGE_CONTROLLER_NOT_AUTHORIZED",
        "HUB_BRIDGE_SIGNER_ESCROW_MISMATCH",
        "HUB_BRIDGE_SIGNER_CHAIN_MISMATCH",
    }
)


class HarnessError(RuntimeError):
    pass


def quote_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(subprocess.list2cmdline([arg]) for arg in argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def _run_with_live_stderr(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture JSON stdout while teeing child diagnostics to the operator live."""

    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        env=env,
    )
    if proc.stdout is None or proc.stderr is None:
        raise HarnessError("could not open Hub Control child pipes")

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def drain_stdout() -> None:
        stdout_parts.append(proc.stdout.read())

    def drain_stderr() -> None:
        for line in proc.stderr:
            stderr_parts.append(line)
            print(line, end="", flush=True)

    stdout_thread = threading.Thread(target=drain_stdout, name="hub-control-stdout", daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, name="hub-control-stderr", daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    returncode = proc.wait()
    stdout_thread.join()
    stderr_thread.join()

    return subprocess.CompletedProcess(
        args=argv,
        returncode=returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Main Computer Hub operator harness")
    parser.add_argument("operation", choices=("inspect", "add-hub", "remove-hub", "redeploy-hub", "redeploy-hubs"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--hub")
    parser.add_argument("--allow-full-deletion", action="store_true")
    parser.add_argument(
        "--require-hub-route",
        choices=("/api/hub/v1/credits/bridge-deposit",),
        default="",
        help="Fail closed unless the expected feature exists in pushed Hub source and the deployed public route",
    )
    parser.add_argument(
        "--force-git",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "for add-hub, block when relevant Hub runtime files have uncommitted local changes; "
            "--no-force-git warns but does not block"
        ),
    )
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
        self.force_git = bool(getattr(args, "force_git", True))
        if getattr(args, "require_hub_route", "") and self.operation not in {"redeploy-hub", "redeploy-hubs"}:
            raise HarnessError("--require-hub-route applies only to redeploy-hub(s)")
        self._prepared_boundary_printed = False
        self._resumed_existing_run = False

        if self.operation == "inspect":
            if self.hub:
                raise HarnessError("inspect does not take --hub")
            if self.allow_full_deletion:
                raise HarnessError("--allow-full-deletion applies only to remove-hub")
            if not self.force_git:
                raise HarnessError("--no-force-git applies only to add-hub")
            self.run_dir: Path | None = None
            self.state: dict[str, Any] = {}
            return

        if self.operation == "redeploy-hub" and not self.hub:
            raise HarnessError("redeploy-hub requires --hub")
        if self.operation == "redeploy-hubs" and self.hub:
            raise HarnessError("redeploy-hubs does not accept --hub; use redeploy-hub")
        if not self.hub and self.operation not in {"redeploy-hubs"}:
            raise HarnessError(f"{self.operation} requires --hub")
        if self.allow_full_deletion and self.operation != "remove-hub":
            raise HarnessError("--allow-full-deletion applies only to remove-hub")
        if not self.force_git and self.operation != "add-hub":
            raise HarnessError("--no-force-git applies only to add-hub")

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
            "force_git": self.force_git,
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
            "public_url": None,
            "network_ingress_url": None,
            "serve_network_ingress": False,
            "fdb_contract": None,
            "chain_contract": None,
            "dependency_rectification_required": [],
            "preexisting_dependency_drift": [],
            "last_completed_step": None,
            "fdb_bootstrap": None,
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
            if self.operation == "add-hub" and bool(payload.get("force_git", True)) != self.force_git:
                continue
            if payload.get("last_completed_step") == "final-inspect":
                # Older add-hub runs could report completion while their own
                # final inspection recorded unresolved dependency drift.
                # Resume only those runs; all verified runs remain completed.
                if self.operation != "add-hub" or not payload.get("dependency_rectification_required"):
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
        argv = self.control_cmd("inspect", self.network)
        if self.operation == "remove-hub":
            argv.append("--membership-only")
        return argv

    def topology_check_cmd(self) -> list[str]:
        """Read-only topology gate used on entry to the Hub prep boundary."""
        argv = [sys.executable, str(self.repo_root / "tools" / "hub_topology_check.py")]
        if self.operation == "remove-hub":
            # Removal verifies membership and Coolify placement, not live FDB.
            argv.append("--membership-only")
        return [*argv, "--network", self.network, "--repo-root", str(self.repo_root)]

    def fdb_inspect_cmd(self) -> list[str]:
        """Read the independently verified FDB dependency before first-Hub prep."""
        return [
            sys.executable, "-m", "tools.fdb_control", "--repo-root",
            str(self.repo_root), "--json", "inspect", self.network,
        ]

    def fdb_bootstrap_cmd(self, operation: str, service: str) -> list[str]:
        command = [
            sys.executable, str(self.repo_root / "fdb_mutate_harness.py"),
            operation, "--network", self.network, "--service", service,
            "--repo-root", str(self.repo_root),
        ]
        if self._mutation_authorized():
            command.extend(("--execute-mutations", "--yes-i-know-this-mutates-fdb"))
        return command

    def _first_fdb_service(self) -> str:
        """Place initial FDB with the first Hub, using network-scoped naming."""
        pattern = re.fullmatch(re.escape(self.network) + r"([a-z])-hub([1-9][0-9]*)", self.hub)
        if pattern is None:
            raise HarnessError(
                f"cannot infer first FDB placement from Hub {self.hub!r}; "
                "expected <network><placement>-hub<N>"
            )
        return f"{self.network}{pattern.group(1)}-fdb1"

    def _inspect_fdb_dependency(self) -> dict[str, Any]:
        """No accepted state or unverified state is ever treated as ready."""
        if self.run_dir is None:
            raise HarnessError("FDB dependency inspection has no Hub harness run directory")
        argv = self.fdb_inspect_cmd()
        prefix = self.run_dir / "00-fdb-dependency-inspect"
        prefix.with_suffix(".command.txt").write_text(quote_command(argv) + "\n", encoding="utf-8")
        proc = subprocess.run(argv, cwd=self.repo_root, text=True, capture_output=True, check=False)
        prefix.with_suffix(".stdout.txt").write_text(proc.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
        try:
            payload = json.loads(proc.stdout)
        except (ValueError, TypeError) as exc:
            raise HarnessError(
                f"FDB inspection returned invalid JSON (exit={proc.returncode}); see {prefix}.stdout.txt"
            ) from exc
        if proc.returncode != 0 or not isinstance(payload, dict) or payload.get("ok") is not True:
            raise HarnessError(f"FDB dependency inspection failed: {payload.get('error') if isinstance(payload, dict) else payload}")
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("network") != self.network:
            raise HarnessError("FDB dependency inspection did not verify the requested network")
        prefix.with_suffix(".json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    @staticmethod
    def _fdb_is_verified(result: dict[str, Any]) -> bool:
        if result.get("status") != "accepted":
            return False
        services = result.get("services")
        coordinators = result.get("coordinators")
        if not isinstance(services, list) or not services or not isinstance(coordinators, list) or not coordinators:
            return False
        for name in ("cluster_verification", "birth_verification"):
            check = result.get(name)
            if isinstance(check, dict) and check.get("verified") is True:
                return True
        return False

    def _run_fdb_bootstrap(self, operation: str, service: str) -> None:
        """Delegate the entire birth/rebirth transaction to the FDB operator harness.

        That harness owns its frozen cluster identity, retries, evidence, and
        accepted-state commit. The Hub harness never fabricates FDB authority.
        """
        if self.run_dir is None:
            raise HarnessError("FDB bootstrap has no Hub harness run directory")
        argv = self.fdb_bootstrap_cmd(operation, service)
        prefix = self.run_dir / "00-fdb-dependency-bootstrap"
        prefix.with_suffix(".command.txt").write_text(quote_command(argv) + "\n", encoding="utf-8")
        print(f"\n=== FDB dependency: {operation} {service} ===", flush=True)
        # Stream sub-harness output: FoundationDB proof can require a bounded wait.
        with prefix.with_suffix(".output.txt").open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                argv, cwd=self.repo_root, text=True, errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
            )
            if proc.stdout is None:
                raise HarnessError("could not read FDB bootstrap output")
            for line in proc.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            code = proc.wait()
        if code != 0:
            raise HarnessError(
                f"FDB {operation} failed (exit={code}); see {prefix}.output.txt. "
                "The FDB birth state has been preserved for safe resumption; "
                "Hub deployment has not started."
            )

    def _assert_first_hub_fdb_bootstrap(self) -> None:
        """Never recreate FDB automatically underneath an established Hub fleet."""
        from collections.abc import Mapping
        from tools.hub_control.common.models import HubContext
        from tools.hub_control.common.state import read_accepted
        from tools.hub_control.common.privates import load_private, network_doc

        ctx = HubContext.from_repo(self.repo_root)
        try:
            accepted = read_accepted(ctx, self.network)
            private = network_doc(load_private(ctx), self.network)
        except Exception as exc:
            raise HarnessError(f"cannot verify first-Hub authority before FDB birth: {exc}") from exc
        if accepted is not None and accepted.get("hubs"):
            raise HarnessError(
                "automatic FDB birth refused: accepted Hub topology already contains Hubs; "
                "restore the previously accepted FDB dependency"
            )
        desired = private.get("hubs", {})
        if not isinstance(desired, Mapping):
            raise HarnessError("Mother Hub membership is not a mapping; refusing automatic FDB birth")
        active = [name for name, item in desired.items()
                  if isinstance(item, Mapping) and item.get("status") == "active"]
        if active:
            raise HarnessError(
                f"automatic FDB birth refused: Mother already has active Hub members {sorted(active)}; "
                "restore the previously accepted FDB dependency"
            )

    def _ensure_fdb_dependency(self) -> bool:
        """Create FDB automatically only for an initial Hub and proven empty FDB.

        Return False for a nonmutating preview: do not pretend Hub prep passed
        while its mandatory FDB dependency is still unborn.
        """
        if self.operation != "add-hub":
            return True
        print("\n=== initial Hub dependencies: FoundationDB ===", flush=True)
        result = self._inspect_fdb_dependency()
        status = result.get("status")
        print(f"FDB status: {status}")
        if self._fdb_is_verified(result):
            print(f"FDB accepted generation: {result.get('accepted_generation')} (verified)")
            return True
        if status not in {"unborn", "accepted-empty"}:
            raise HarnessError(
                "Hub bootstrap requires independently verified live FoundationDB; "
                f"FDB status={status!r} and proof not verified. Existing accepted FDB "
                "must be repaired without altering its membership."
            )
        if status == "unborn" and result.get("accepted") is not None:
            raise HarnessError("FDB inspect reported contradictory unborn accepted state")
        if status == "accepted-empty":
            proof = result.get("empty_topology_verification")
            if not isinstance(proof, dict) or proof.get("verified") is not True:
                raise HarnessError("FDB accepted-empty topology lacks independent verification")
        self._assert_first_hub_fdb_bootstrap()
        service = self._first_fdb_service()
        operation = "create-cluster" if status == "unborn" else "add-service"
        self.state["fdb_bootstrap"] = {"operation": operation, "service": service, "status": "required"}
        self._write_state()
        print(f"FDB bootstrap plan: {operation} {service}")
        if not self._mutation_authorized():
            print("\nStopping before live mutation; the first Hub also requires FDB bootstrap.")
            print("Rerun the same Hub command with mutation authorization:")
            print(
                f"python .\\hub_mutate_harness.py add-hub --network {self.network} "
                f"--hub {self.hub} --execute-mutations --yes-i-know-this-mutates-hub"
            )
            return False
        self._run_fdb_bootstrap(operation, service)
        after = self._inspect_fdb_dependency()
        if not self._fdb_is_verified(after):
            raise HarnessError(
                "FDB bootstrap returned success but the accepted FDB cluster is not independently verified; "
                "Hub deployment refused"
            )
        self.state["fdb_bootstrap"] = {
            "operation": operation, "service": service, "status": "verified",
            "generation": after.get("accepted_generation"),
        }
        self._write_state()
        print(f"FDB bootstrap accepted and verified (generation {after.get('accepted_generation')}).")
        return True

    def _check_topology_before_prep(self, *, _fdb_rechecked: bool = False) -> bool | None:
        """Diagnose drift before accepted-state pre-inspect can obscure its cause.

        This is intentionally read-only and not a persisted lifecycle step:
        a retry after an explicit operator seal must observe topology again.
        In-progress do/finalize resumes do not revisit this read-only boundary.
        """
        if self.run_dir is None:
            raise HarnessError("topology preflight has no harness run directory")
        argv = self.topology_check_cmd()
        prefix = self.run_dir / "00-topology-check"
        prefix.with_suffix(".command.txt").write_text(quote_command(argv) + "\n", encoding="utf-8")
        print("\n=== prep: Hub topology check ===", flush=True)
        try:
            proc = subprocess.run(
                argv, cwd=self.repo_root, text=True, capture_output=True, check=False,
            )
        except OSError as exc:
            raise HarnessError(f"prep blocked: Hub topology check could not start: {exc}") from exc
        prefix.with_suffix(".stdout.txt").write_text(proc.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
        try:
            report = json.loads(proc.stdout)
        except (ValueError, TypeError) as exc:
            raise HarnessError(
                f"prep blocked: Hub topology check returned non-JSON output "
                f"(exit={proc.returncode}); see {prefix.with_suffix('.stdout.txt')}"
            ) from exc
        prefix.with_suffix(".json").write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        if not isinstance(report, dict) or report.get("network") != self.network:
            raise HarnessError("prep blocked: Hub topology check returned invalid network/report")
        status = report.get("status")
        if status not in {"PASS", "DRIFT", "UNKNOWN"} or proc.returncode != {
            "PASS": 0, "DRIFT": 1, "UNKNOWN": 2,
        }[status]:
            raise HarnessError(
                f"prep blocked: Hub topology check status/exit disagreement "
                f"(status={status!r}, exit={proc.returncode}); see {prefix.with_suffix('.json')}"
            )
        print(f"Hub topology: {status}")
        if status == "PASS":
            return
        # First-Hub bootstrap is the only recoverable UNKNOWN here: the
        # Hub topology checker cannot load an FDB contract before FDB birth.
        # Other UNKNOWN causes (e.g. Coolify inventory failure) stay blocked.
        if status == "UNKNOWN" and self.operation == "add-hub":
            errors = report.get("errors")
            if isinstance(errors, list) and any(
                isinstance(item, dict)
                and item.get("code") == "HUB_MOTHER_STATE_UNVERIFIED"
                and any(marker in str(item.get("message") or "") for marker in (
                    "HUB_FDB_UNBORN", "no accepted FDB state exists",
                    "HUB_FDB_UNAVAILABLE", "accepted FDB topology is empty",
                ))
                for item in errors
            ):
                if _fdb_rechecked:
                    raise HarnessError("Hub topology still reports unavailable FDB after completed bootstrap; refusing retry loop")
                if not self._ensure_fdb_dependency():
                    return False
                # Re-evaluate Hub state and Coolify inventory after FDB birth;
                # no prior UNKNOWN projection is accepted or auto-sealed.
                return self._check_topology_before_prep(_fdb_rechecked=True)
        if status == "DRIFT" and report.get("errors") == []:
            projection = report.get("accepted_projection")
            projection = projection if isinstance(projection, dict) else {}
            details = []
            for label, values in (
                ("stale accepted Hubs", projection.get("stale_hubs")),
                ("unprojected Hubs", projection.get("unprojected_hubs")),
                ("missing live Hubs", report.get("missing_hubs")),
                ("unexpected live Hubs", report.get("extra_hubs")),
            ):
                if isinstance(values, list) and values:
                    details.append(f"{label}: {', '.join(str(v) for v in values)}")
            reason = "\n".join(details)
            command = report.get("seal_command")
            if isinstance(command, str) and command.strip():
                raise HarnessError(
                    "prep blocked: independently verified Hub topology DRIFT."
                    + (f"\n{reason}" if reason else "")
                    + "\nRun the reseal explicitly, then retry the same Hub command:\n"
                    + command
                )
            raise HarnessError(
                "prep blocked: verified Hub topology DRIFT but no reseal command was provided; "
                "review the topology-check evidence before proceeding"
            )
        errors = report.get("errors")
        reasons = "; ".join(
            f"{item.get('code')}: {item.get('message')}"
            for item in errors if isinstance(item, dict)
        ) if isinstance(errors, list) else ""
        raise HarnessError(
            "prep blocked: Hub topology cannot be independently verified"
            + (f" ({reasons})" if reasons else "")
            + "; no reseal command is offered for UNKNOWN/unverified state"
        )

    def prep_cmd(self) -> list[str]:
        command = self.control_cmd(self.operation, "prep", self.network, "--hub", self.hub)
        if self.operation == "remove-hub" and self.allow_full_deletion:
            command.append("--allow-full-deletion")
        return command

    def do_cmd(self) -> list[str]:
        command = self.control_cmd(
            self.operation,
            "do",
            self.network,
            "--operation-id",
            self._required_state("operation_id"),
        )
        if self.operation == "add-hub" and not self.force_git:
            command.append("--no-force-git")
        return command

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

        if self.operation in {"redeploy-hub", "redeploy-hubs"}:
            return self._run_redeploy()

        if self.operation == "inspect":
            if self._mutation_authorized():
                raise HarnessError("inspect is read-only and does not accept mutation authorization flags")
            return self._run_inspect_only()

        if self._resumed_existing_run:
            print(f"Resuming unfinished {self.operation} for {self.hub}.")

        completed = self.state.get("last_completed_step")
        # A completed pre-inspect is only an observation, not a frozen mutation
        # boundary. If prep previously failed and the harness is resumed later,
        # accepted Hub authority may have advanced in the meantime. Refresh the
        # observation before prep so the human-visible starting state matches the
        # authority that prep is about to freeze.
        if self._resumed_existing_run and completed == "pre-inspect":
            print("Refreshing pre-inspect before prep; accepted Hub authority may have changed since the prior attempt.")
            start_index = STEPS.index("pre-inspect")
        else:
            start_index = 0 if completed not in STEPS else STEPS.index(str(completed)) + 1
        for step in STEPS[start_index:]:
            # Entering prep requires the live topology check *before* the
            # accepted-projection pre-inspect, whose stale runtime checks would
            # otherwise abort before we can show the operator the seal command.
            if step == "pre-inspect":
                if self._check_topology_before_prep() is False:
                    return 0
            if step in MUTATING_STEPS and not self._mutation_authorized():
                if not self._prepared_boundary_printed:
                    self._print_prepared_boundary()
                return 0
            result = self._run_step(step, self._command_for(step))
            self._validate_step(step, result)
            self._print_step_result(step, result)
            self.state["last_completed_step"] = step
            self._write_state()

        if self.operation == "add-hub" and not self._finish_add_hub_dependencies():
            return 0
        self._print_final_verification()
        print("HUB_MUTATE_HARNESS_COMPLETE")
        return 0

    def _reconciliation_inspect(self, phase: str) -> dict[str, Any]:
        """Read current accepted authority without rewriting the frozen membership proof."""
        if self.run_dir is None:
            raise HarnessError("dependency reconciliation has no harness run directory")
        argv = self.inspect_cmd()
        prefix = self.run_dir / f"06-dependency-{phase}"
        prefix.with_suffix(".command.txt").write_text(quote_command(argv) + "\n", encoding="utf-8")
        proc = _run_with_live_stderr(argv, cwd=self.repo_root)
        prefix.with_suffix(".stdout.txt").write_text(proc.stdout, encoding="utf-8")
        prefix.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
        payload = _parse_payload(proc, label=f"dependency-{phase}")
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("network") != self.network:
            raise HarnessError("dependency inspection returned a different network or invalid result")
        prefix.with_suffix(".json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    def _verify_reconciled_add_hub(self, result: dict[str, Any]) -> int:
        """Verify current references after membership proof; never replay frozen checks."""
        self._require_accepted_verified(result, label="post-rectification")
        generation = result.get("accepted_generation")
        if (not isinstance(generation, int) or isinstance(generation, bool)
                or generation < int(self._required_state("target_generation"))):
            raise HarnessError("post-rectification accepted generation is missing or regressed")
        hubs = result.get("hubs")
        if not isinstance(hubs, list):
            raise HarnessError("post-rectification inspection omitted accepted Hubs")
        if self.state.get("target_hub_count") is not None and len(hubs) != self.state["target_hub_count"]:
            raise HarnessError("post-rectification Hub membership count changed")
        frozen_ids = self.state.get("membership_hub_ids")
        if frozen_ids is not None and sorted(str(h.get("hub_id") or "") for h in hubs if isinstance(h, dict)) != frozen_ids:
            raise HarnessError("post-rectification changed accepted Hub membership identities")
        matches = [h for h in hubs if isinstance(h, dict) and h.get("hub_id") == self.hub]
        if len(matches) != 1:
            raise HarnessError("post-rectification did not preserve the added Hub identity")
        member = matches[0]
        for key, frozen in (("host_id", self.state.get("resolved_host")),
                            ("controller_id", self.state.get("resolved_controller"))):
            if frozen and member.get(key) != frozen:
                raise HarnessError(f"post-rectification changed the added Hub {key}")
        for dependency in ("chain", "fdb"):
            current = _mapping(result.get(f"{dependency}_contract"), f"current {dependency} contract")
            for hub in hubs:
                accepted = _mapping(hub.get(f"{dependency}_contract"), f"accepted {dependency} contract")
                for key in ("generation", "sha256"):
                    valid = (isinstance(current.get(key), int) and not isinstance(current.get(key), bool)
                             and current[key] >= 0) if key == "generation" else bool(current.get(key))
                    if not valid or accepted.get(key) != current.get(key):
                        raise HarnessError(f"post-rectification {dependency} {key} is not current")
        return generation

    def _finish_add_hub_dependencies(self) -> bool:
        """Finish drift-only adoption inside the authorized, resumable add-hub run."""
        print("\n=== dependency reconciliation ===", flush=True)
        current = self._reconciliation_inspect("before")
        # A previous rectifier may have advanced accepted state just before the
        # process died. Verify that commit first; never redeploy it a second time.
        if (current.get("status") == "accepted" and
                isinstance(current.get("topology_verification"), dict) and
                current["topology_verification"].get("verified") is True):
            generation = self._verify_reconciled_add_hub(current)
        else:
            drift = self._dependency_drift_only(current)
            if not drift:
                raise HarnessError("Hub is not fully verified and the failure is not dependency-only drift")
            # Do not use a past add-hub receipt to authorize rectification
            # after some other operation has advanced Hub membership authority.
            if current.get("accepted_generation") != int(self._required_state("target_generation")):
                raise HarnessError("accepted Hub generation changed since add-hub finalization; refusing rectification")
            hubs = current.get("hubs")
            if not isinstance(hubs, list) or (self.state.get("target_hub_count") is not None
                    and len(hubs) != self.state["target_hub_count"]):
                raise HarnessError("accepted Hub membership changed before dependency rectification")
            frozen_ids = self.state.get("membership_hub_ids")
            if frozen_ids is not None and sorted(str(h.get("hub_id") or "") for h in hubs if isinstance(h, dict)) != frozen_ids:
                raise HarnessError("accepted Hub identities changed before dependency rectification")
            matches = [h for h in hubs if isinstance(h, dict) and h.get("hub_id") == self.hub]
            if len(matches) != 1:
                raise HarnessError("accepted added Hub is missing before dependency rectification")
            for key, frozen in (("host_id", self.state.get("resolved_host")),
                                ("controller_id", self.state.get("resolved_controller"))):
                if frozen and matches[0].get(key) != frozen:
                    raise HarnessError(f"accepted added Hub {key} changed before dependency rectification")
            if not self._mutation_authorized():
                print("Dependency rectification requires the existing add-hub mutation authorization; "
                      "rerun the same add-hub command with --execute-mutations "
                      "--yes-i-know-this-mutates-hub")
                return False
            self.state["dependency_rectification_required"] = drift
            self.state["dependency_reconciliation"] = {"status": "in-progress", "from_generation":
                current.get("accepted_generation")}
            self._write_state()
            from tools.hub_control.common.errors import HubControlError
            from tools.hub_control.common.models import HubContext
            from tools.hub_control.rectify_dependencies import rectify_network
            print("Rectifying accepted Hub dependencies through the existing Hub Control rectifier", flush=True)
            try:
                outcome = rectify_network(HubContext.from_repo(self.repo_root), self.network,
                                          execute=True, confirmed=True, force_git=self.force_git)
            except HubControlError as exc:
                raise HarnessError(f"{exc.code}: {exc.message}") from exc
            if outcome.get("status") not in {"rectified", "already-current"} or outcome.get("network") != self.network:
                raise HarnessError(f"dependency rectifier did not finish: {outcome.get('status')!r}")
            current = self._reconciliation_inspect("after")
            generation = self._verify_reconciled_add_hub(current)
        self.state.setdefault("membership_target_generation", self.state.get("target_generation"))
        self.state["target_generation"] = generation
        self.state["dependency_rectification_required"] = []
        self.state["dependency_reconciliation"] = {"status": "verified", "accepted_generation": generation}
        self._write_state()
        print(f"Dependencies verified at accepted Hub generation {generation}")
        return True

    def _run_redeploy(self) -> int:
        """In-place rollout: authorize assigned bridge controllers, then rebuild.

        A queued deployment UUID is saved before waiting. Reinvoking the same
        command resumes the exact queued build, not another build request.
        """
        from tools.hub_control.common.canonical import sha256_json
        from tools.hub_control.common.models import HubContext
        from tools.hub_control.redeploy import (
            BuildSubmissionRejected, RedeployError, accepted_targets, deployment_history, force_rebuild, git_revision,
            reconcile_build_submission, resolve_existing_hub, verify_runtime, wait_for_build,
            synchronize_bridge_signer, failed_previous_build,
        )
        try:
            # Check Git before opening Mother state or contacting Coolify. The
            # helper emits the existing add/commit/push instructions when Hub
            # image inputs are dirty, and rejects HEADs not pushed to origin.
            revision = git_revision(self.repo_root, self.network)
            required_route = str(getattr(self.args, "require_hub_route", "") or "")
            if required_route:
                from tools.hub_route_provenance import inspect_local_and_git
                provenance = inspect_local_and_git(self.repo_root, route=required_route)
                gates = ("workingHubRoutePresent", "committedHubRoutePresent",
                         "localAuditCallsExactLookup", "headMatchesOriginMain",
                         "hubSourceClean")
                absent = [key for key in gates if not provenance.get(key)]
                if absent:
                    raise HarnessError("Hub required route is not ready in local audit and pushed Git source: "
                                       + ", ".join(absent) + "; no build requested")
                print(f"Hub feature source preflight: {required_route} present in local audit, "
                      "Hub source and pushed HEAD", flush=True)
            print(
                f"Hub Git preflight: relevant source clean; HEAD {revision[:12]} "
                "pushed to origin/main; no commit required for redeployment "
                "(does not prove a requested feature unless --require-hub-route is used)",
                flush=True,
            )
            ctx = HubContext.from_repo(self.repo_root)
            accepted, members = accepted_targets(
                ctx, self.network, self.hub if self.operation == "redeploy-hub" else "",
            )
            accepted_sha = sha256_json(accepted)
            # Resolve every existing application before triggering any build.
            targets = [resolve_existing_hub(ctx, self.network, member) for member in members]
            identities = [
                {"hub_id": target["hub_id"], "application_uuid": target["application_uuid"],
                 "controller_id": target["controller_id"]}
                for target in targets
            ]
            previous = self.state.get("redeploy")
            if previous is None:
                self.state["redeploy"] = {
                    "accepted_sha256": accepted_sha,
                    "revision": revision,
                    "targets": [{**entry, "status": "pending", "deployment_uuid": None, "commit": None}
                                for entry in identities],
                }
                self._write_state()
            else:
                if (previous.get("accepted_sha256") != accepted_sha
                    or [{key: row[key] for key in ("hub_id", "application_uuid", "controller_id")}
                        for row in previous.get("targets", [])] != identities):
                    raise HarnessError(
                        "redeploy resume authority changed (topology or application identity); "
                        "refusing to continue the old rollout"
                    )
            rollout = self.state["redeploy"]
            # Reconcile any interrupted POST before changing the desired Git
            # revision or sending another request. In particular, the original
            # HTTP 405 rollout left a legacy 'triggering' receipt without a UUID.
            for target, item in zip(targets, rollout["targets"]):
                if item["status"] != "triggering":
                    continue
                legacy_start = str(item.get("trigger_started_at") or "")
                if not legacy_start:
                    try:
                        legacy_start = datetime.strptime(
                            self.run_dir.name[:16], "%Y%m%dT%H%M%SZ"
                        ).replace(tzinfo=timezone.utc).isoformat()
                    except (ValueError, AttributeError) as exc:
                        raise HarnessError("cannot reconstruct previous rollout start; no build requested") from exc
                found_uuid = reconcile_build_submission(
                    target,
                    baseline_uuids=item.get("baseline_uuids"),
                    started_after=legacy_start,
                    revision=str(rollout["revision"]),
                )
                if found_uuid:
                    item["deployment_uuid"] = found_uuid
                    item["status"] = "queued"
                    print(f"Recovered existing Coolify build {found_uuid} for {target['hub_id']}")
                else:
                    item["status"] = "pending"
                    item["deployment_uuid"] = None
                    print(f"Coolify confirms no previous build for {target['hub_id']}; retry enabled")
                self._write_state()
            # A failed deployment is terminal: Coolify rolled it back and no
            # build is in flight. Retire only that exact UUID, on a later
            # invocation, before deciding whether a revised source can roll out.
            for target, item in zip(targets, rollout["targets"]):
                if item["status"] == "queued" and item.get("deployment_uuid"):
                    if failed_previous_build(target, str(item["deployment_uuid"])):
                        print(f"Hub {target['hub_id']}: previous build "
                              f"{item['deployment_uuid']} failed; safely enabling a new attempt", flush=True)
                        item["status"] = "pending"
                        item["last_failed_deployment_uuid"] = item["deployment_uuid"]
                        item["deployment_uuid"] = None
                        self._write_state()
            # A new Git revision can supersede a partially deployed revision,
            # but only after every old build has a known terminal outcome. Never
            # submit a new build while a prior build may still be running.
            if rollout["revision"] != revision:
                old_revision = str(rollout["revision"])
                if any(item["status"] != "pending" for item in rollout["targets"]):
                    if not self._mutation_authorized():
                        print(f"Rollout revision changed {old_revision} -> {revision}; "
                              "execution will settle old builds before starting a fresh rollout")
                        return 0
                    print(f"Hub rollout: revision changed {old_revision} -> {revision}; "
                          "reconcile old builds before restarting all Hubs", flush=True)
                    for target, item in zip(targets, rollout["targets"]):
                        status = item["status"]
                        if status not in {"pending", "queued", "built", "verified", "failed"}:
                            raise HarnessError(
                                f"cannot supersede {target['hub_id']} with unresolved status {status!r}; "
                                "no additional build requested"
                            )
                        if status != "queued":
                            continue
                        uuid = str(item.get("deployment_uuid") or "")
                        if not uuid:
                            raise HarnessError(
                                f"queued Hub {target['hub_id']} has no deployment UUID; "
                                "no additional build requested"
                            )
                        print(f"Hub {target['hub_id']}: settle previous build {uuid} "
                              f"at revision {old_revision}", flush=True)
                        if failed_previous_build(target, uuid):
                            item["status"] = "failed"
                        else:
                            try:
                                build = wait_for_build(target, uuid)
                            except RedeployError:
                                # A build can fail while being polled. A confirmed
                                # terminal failure is safe to supersede; an unknown
                                # outcome must continue to block new builds.
                                if not failed_previous_build(target, uuid):
                                    raise
                                item["status"] = "failed"
                            else:
                                actual = str(build["commit"]).strip().lower()
                                if actual != old_revision.lower():
                                    raise HarnessError(
                                        f"Hub {target['hub_id']} built revision {actual}, "
                                        f"not queued revision {old_revision}; no additional build requested"
                                    )
                                item["commit"] = actual
                                item["status"] = "built"
                        self._write_state()
                        print(f"Hub {target['hub_id']}: previous build {item['status']}", flush=True)
                    current, _ = accepted_targets(ctx, self.network)
                    if sha256_json(current) != accepted_sha:
                        raise HarnessError("accepted Hub topology changed while settling old builds")
                    # Keep the old revision's receipt intact inside this same
                    # run. The new revision gets new pending targets and UUIDs.
                    self.state.setdefault("redeploy_history", []).append(
                        json.loads(json.dumps(rollout))
                    )
                    self.state["redeploy"] = {
                        "accepted_sha256": accepted_sha,
                        "revision": revision,
                        "targets": [{**entry, "status": "pending", "deployment_uuid": None, "commit": None}
                                    for entry in identities],
                    }
                    rollout = self.state["redeploy"]
                    self._write_state()
                    print(f"Hub rollout: starting new revision {revision} for all selected Hubs", flush=True)
                else:
                    print(f"Restarting unsubmitted rollout at new Git revision {revision}")
                    rollout["revision"] = revision
                    self._write_state()
            print(f"\n=== {self.operation} preflight ===")
            print(f"network:              {self.network}")
            print(f"accepted generation:  {accepted['generation']} (unchanged)")
            print(f"Git revision:         {revision}")
            print("bridge authorization: verify each assigned admin; deployer signs only if needed")
            for target in targets:
                print(f"  {target['hub_id']}  controller={target['controller_id']}  "
                      f"application={target['application_uuid']}")
            if not self._mutation_authorized():
                print("\nStopping before live mutation. Rerun with "
                      "--execute-mutations --yes-i-know-this-mutates-hub")
                return 0
            def verify_required_live_route(target: dict[str, Any]) -> None:
                if not required_route:
                    return
                from tools.hub_route_provenance import probe_hub_route
                config_file = self.repo_root / "main_computer/config/hub_networks.json"
                config = json.loads(config_file.read_text(encoding="utf-8"))
                public_alias = str(config["networks"][self.network]["hub_public_url"]).rstrip("/")
                direct_url = str(target["public_url"]).rstrip("/")
                for url in dict.fromkeys([direct_url, public_alias]):
                    proof = probe_hub_route(url, route=required_route)
                    if proof.get("routeVerified") is not True:
                        raise HarnessError(f"Hub route not verified at {url}: {proof.get('result')}; "
                                           "build might be healthy but requested feature is not live")
                    print(f"Hub feature runtime verified: {required_route} at {url}", flush=True)

            for target, item in zip(targets, rollout["targets"]):
                if item["status"] == "verified":
                    verify_required_live_route(target)
                    print(f"Skipping verified Hub {target['hub_id']}")
                    continue
                print(f"\n=== redeploy: {target['hub_id']} ===", flush=True)
                if item["status"] == "pending":
                    # Persist the exact existing deployments before attempting
                    # POST. On timeout/crash, the next run determines whether
                    # Coolify actually queued a new build; no blind retries.
                    print(f"Hub {target['hub_id']}: inspect existing Coolify deployments", flush=True)
                    history = deployment_history(target)
                    print(f"Hub {target['hub_id']}: synchronize verified Mother bridge signer", flush=True)
                    synchronize_bridge_signer(ctx, target)
                    item["baseline_uuids"] = [str(row["deployment_uuid"]) for row in history]
                    item["trigger_started_at"] = datetime.now(timezone.utc).isoformat()
                    item["status"] = "triggering"
                    self._write_state()
                    print(f"Hub {target['hub_id']}: submit forced Git rebuild", flush=True)
                    try:
                        item["deployment_uuid"] = force_rebuild(target)
                    except BuildSubmissionRejected:
                        # Explicit 4xx responses were rejected before queuing a
                        # build. There is no ambiguous in-flight request to
                        # preserve, so a corrected invocation can retry.
                        item["status"] = "pending"
                        self._write_state()
                        raise
                    item["status"] = "queued"
                    self._write_state()
                    print(f"Hub {target['hub_id']}: Coolify build queued "
                          f"uuid={item['deployment_uuid']}", flush=True)
                if item["status"] == "queued":
                    print(f"Hub {target['hub_id']}: waiting for Coolify build "
                          f"{item['deployment_uuid']}", flush=True)
                    build = wait_for_build(target, str(item["deployment_uuid"]))
                    actual_revision = str(build["commit"]).strip().lower()
                    if actual_revision != revision:
                        raise HarnessError(
                            f"Hub {target['hub_id']} built revision {actual_revision}, "
                            f"not requested revision {revision}; stopping rollout"
                        )
                    item["commit"] = actual_revision
                    item["status"] = "built"
                    self._write_state()
                if item["status"] == "built":
                    print(f"Hub {target['hub_id']}: verify Hub health and bridge signer", flush=True)
                    verify_runtime(target)
                    verify_required_live_route(target)
                    current, _ = accepted_targets(ctx, self.network)
                    if sha256_json(current) != accepted_sha:
                        raise HarnessError("accepted Hub topology changed during code rollout")
                    item["status"] = "verified"
                    self._write_state()
                    print(f"Hub {target['hub_id']}: build and runtime verified")
            self.state["last_completed_step"] = "final-inspect"
            self._write_state()
            print("HUB_REDEPLOY_HARNESS_COMPLETE")
            return 0
        except RedeployError as exc:
            raise HarnessError(str(exc)) from exc
        except Exception as exc:
            from tools.hub_control.common.errors import HubControlError
            if isinstance(exc, HubControlError):
                raise HarnessError(f"{exc.code}: {exc.message}") from exc
            raise

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
        child_env = os.environ.copy()
        child_env["MAIN_COMPUTER_HUB_PROGRESS"] = "1"
        child_env["PYTHONUNBUFFERED"] = "1"
        proc = _run_with_live_stderr(
            argv,
            cwd=self.repo_root,
            env=child_env,
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
            error = payload.get("error")
            error_code = str(error.get("code") or "") if isinstance(error, dict) else ""
            if (
                step == "do"
                and self.operation == "add-hub"
                and error_code in ADD_HUB_REPREP_DO_ERROR_CODES
            ):
                self._invalidate_prepared_add_hub_after_dependency_failure(error_code)
            raise HarnessError(
                f"{step} failed (exit={proc.returncode}): {payload.get('error')}; "
                f"stdout/stderr saved under {prefix}.*"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise HarnessError(f"{step} did not return a result object")
        return result

    def _invalidate_prepared_add_hub_after_dependency_failure(self, error_code: str) -> None:
        """Force the next retry back through pre-inspect/prep after Chain repair.

        Contract redeployment changes the Chain consumer contract and therefore
        invalidates the dependency projection frozen by the old add-hub prep.
        Keeping that old operation id would only turn the next retry into a
        signer/escrow mismatch.  The harness keeps the run directory/evidence but
        deliberately reopens the read-only observation boundary.
        """

        self.state["last_completed_step"] = "pre-inspect"
        for key in (
            "operation_id",
            "target_generation",
            "target_hub_count",
            "resolved_controller",
            "resolved_host",
            "public_url",
            "network_ingress_url",
            "fdb_contract",
            "chain_contract",
            "chain_preflight",
        ):
            self.state[key] = None
        self.state["serve_network_ingress"] = False
        self.state["rebirth"] = False
        self._write_state()
        print(
            "Hub dependency preflight invalidated the frozen add-hub prep "
            f"({error_code}); after repairing/deploying the Chain contracts, rerun the same add-hub command."
        )

    def _validate_step(self, step: str, result: dict[str, Any]) -> None:
        if step == "pre-inspect":
            status = result.get("status")
            self.state["preexisting_dependency_drift"] = []
            if self.operation == "add-hub" and status in {"unborn", "accepted-empty"}:
                key = "unborn_topology_verification" if status == "unborn" else "empty_topology_verification"
                verification = result.get(key)
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    raise HarnessError(f"pre-inspect could not verify {status} Hub state: {verification}")
            elif status != "accepted":
                raise HarnessError(
                    f"pre-inspect requires an accepted Hub topology; observed status={status!r}"
                )
            else:
                verification = result.get("topology_verification")
                if not isinstance(verification, dict) or verification.get("verified") is not True:
                    drift = self._dependency_drift_only(result)
                    if not drift:
                        raise HarnessError(
                            f"pre-inspect could not independently verify accepted Hub state: {verification}"
                        )
                    self.state["preexisting_dependency_drift"] = drift
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
            target_generation = details.get("target_generation")
            if not isinstance(target_generation, int) or isinstance(target_generation, bool):
                raise HarnessError("prep did not report an integer target_generation")
            self.state["target_generation"] = target_generation
            self.state["target_hub_count"] = details.get("target_hub_count")

            # Prep is the authoritative freeze point. Newer Hub Control versions
            # report the accepted prestate they actually froze; use it to reconcile
            # any earlier pre-inspect snapshot that became stale before prep.
            prep_generation = details.get("accepted_generation")
            if prep_generation is not None:
                if not isinstance(prep_generation, int) or isinstance(prep_generation, bool):
                    raise HarnessError("prep accepted_generation is not an integer")
                if target_generation != prep_generation + 1:
                    raise HarnessError(
                        f"prep generation boundary is inconsistent: accepted={prep_generation}, target={target_generation}"
                    )
                self.state["starting_generation"] = prep_generation
            prep_hub_count = details.get("accepted_hub_count")
            if prep_hub_count is not None:
                if not isinstance(prep_hub_count, int) or isinstance(prep_hub_count, bool) or prep_hub_count < 0:
                    raise HarnessError("prep accepted_hub_count is not a non-negative integer")
                self.state["starting_hub_count"] = prep_hub_count
            prep_status = details.get("accepted_status")
            if prep_status not in (None, ""):
                self.state["starting_status"] = str(prep_status)
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
            if details.get("public_url") not in (None, ""):
                self.state["public_url"] = _text(details.get("public_url"), "prep public_url")
            if details.get("network_ingress_url") not in (None, ""):
                self.state["network_ingress_url"] = _text(details.get("network_ingress_url"), "prep network_ingress_url")
            self.state["serve_network_ingress"] = bool(details.get("serve_network_ingress"))
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
            expected = int(self._required_state("target_generation"))
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
                self.state["dependency_rectification_required"] = []
            elif result.get("status") != "accepted":
                raise HarnessError(
                    f"final-inspect requires accepted Hub authority; observed status={result.get('status')!r}"
                )

            expected = int(self._required_state("target_generation"))
            if result.get("accepted_generation") != expected:
                raise HarnessError(
                    f"final inspect generation mismatch: expected {expected}, got {result.get('accepted_generation')}"
                )
            hubs = result.get("hubs")
            if not isinstance(hubs, list):
                raise HarnessError("final inspect did not expose accepted hubs")
            if self.operation == "add-hub":
                self.state["membership_hub_ids"] = sorted(str(h.get("hub_id") or "") for h in hubs if isinstance(h, dict))
            matches = [item for item in hubs if isinstance(item, dict) and item.get("hub_id") == self.hub]
            if self.operation == "add-hub":
                if len(matches) != 1:
                    raise HarnessError(f"final inspect did not contain exactly one added Hub {self.hub!r}")
                accepted_hub = matches[0]
                if self.state.get("resolved_host") and accepted_hub.get("host_id") != self.state.get("resolved_host"):
                    raise HarnessError("final accepted Hub host differs from prepared host")
                self._require_frozen_contract_reference(
                    accepted_hub.get("fdb_contract"),
                    self.state.get("fdb_contract"),
                    label="FDB",
                )
                self._require_frozen_contract_reference(
                    accepted_hub.get("chain_contract"),
                    self.state.get("chain_contract"),
                    label="Chain",
                )
            elif matches:
                raise HarnessError(f"removed Hub {self.hub!r} is still accepted")
            if self.state.get("target_hub_count") is not None and len(hubs) != self.state.get("target_hub_count"):
                raise HarnessError(
                    f"final Hub count differs from frozen target: expected {self.state.get('target_hub_count')}, got {len(hubs)}"
                )

            if not full_deletion:
                verification = result.get("topology_verification")
                if isinstance(verification, dict) and verification.get("verified") is True:
                    self.state["dependency_rectification_required"] = []
                else:
                    drift = self._dependency_drift_only(result)
                    if not drift:
                        raise HarnessError(
                            f"final-inspect could not independently verify accepted Hub state: {verification}"
                        )
                    self.state["dependency_rectification_required"] = drift
            return


    def _require_frozen_contract_reference(
        self,
        accepted_value: object,
        frozen_value: object,
        *,
        label: str,
    ) -> None:
        accepted = _mapping(accepted_value, f"final accepted Hub {label} contract")
        frozen = _mapping(frozen_value, f"frozen {label} contract")
        for key in ("generation", "sha256"):
            if accepted.get(key) != frozen.get(key):
                raise HarnessError(
                    f"final accepted Hub {label} contract differs from frozen prep target: "
                    f"accepted {key}={accepted.get(key)!r}, frozen {key}={frozen.get(key)!r}"
                )

    def _dependency_drift_only(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        """Return dependency drift evidence iff every final-inspect failure is stale-contract drift.

        Hub membership operations freeze dependency contracts at prep. If FDB or Chain
        authority advances after that freeze, the accepted Hub may immediately become
        stale without invalidating the already-proven membership mutation. The add-hub
        harness must reconcile this exact drift before reporting completion.
        """
        hubs = result.get("hubs")
        if not isinstance(hubs, list):
            return []
        current_contracts = {
            "fdb": result.get("fdb_contract"),
            "chain": result.get("chain_contract"),
        }
        drift: list[dict[str, Any]] = []
        saw_unverified = False
        for item in hubs:
            if not isinstance(item, dict):
                return []
            verification = item.get("verification")
            if not isinstance(verification, dict):
                return []
            if verification.get("verified") is True:
                continue
            saw_unverified = True
            if verification.get("reason") != "hub-dependency-contract-stale":
                return []
            hub_id = str(item.get("hub_id") or "")
            item_drift = False
            for dependency, adoption_key in (
                ("fdb", "fdb_adoption_verified"),
                ("chain", "chain_adoption_verified"),
            ):
                if verification.get(adoption_key) is True:
                    continue
                adopted = item.get(f"{dependency}_contract")
                current = current_contracts[dependency]
                if not isinstance(adopted, dict) or not isinstance(current, dict):
                    return []
                adopted_sha = str(adopted.get("sha256") or "")
                current_sha = str(current.get("sha256") or "")
                if not adopted_sha or not current_sha or adopted_sha == current_sha:
                    return []
                drift.append(
                    {
                        "hub_id": hub_id,
                        "dependency": dependency,
                        "adopted_generation": adopted.get("generation"),
                        "adopted_sha256": adopted_sha,
                        "current_generation": current.get("generation"),
                        "current_sha256": current_sha,
                    }
                )
                item_drift = True
            if not item_drift:
                return []
        return drift if saw_unverified and drift else []

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
            self._print_inspect_stage_result(result, step=step)
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
            if details.get("deployment_uuid"):
                print(f"deployment UUID:      {details.get('deployment_uuid')}")
            if details.get("deployment_status"):
                print(f"deployment status:    {details.get('deployment_status')}")
            if details.get("deployment_commit"):
                print(f"deployment commit:    {details.get('deployment_commit')}")
            source_check = details.get("git_source_check")
            if isinstance(source_check, dict) and source_check.get("checked") is True:
                if source_check.get("dirty"):
                    mode = "warning/override" if not source_check.get("force_git") else "blocked"
                    print(f"Git source check:     dirty ({mode})")
                else:
                    print("Git source check:     clean")
            if "hub_running" in details:
                print(f"Hub running:          {'verified' if details.get('hub_running') else 'not verified'}")
            if "fdb_adoption_verified" in details:
                print(f"FDB adoption:         {'verified' if details.get('fdb_adoption_verified') else 'not verified'}")
            if "chain_adoption_verified" in details:
                print(f"Chain adoption:       {'verified' if details.get('chain_adoption_verified') else 'not verified'}")
            if "bridge_signer_verified" in details:
                print(f"Bridge signing:       {'verified' if details.get('bridge_signer_verified') else 'not verified'}")
            signer = details.get("bridge_signer")
            if isinstance(signer, dict) and signer.get("configured"):
                if signer.get("bridge_controller_address"):
                    print(f"Bridge controller:    {signer.get('bridge_controller_address')}")
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

    def _print_inspect_stage_result(self, result: dict[str, Any], *, step: str) -> None:
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
        if step == "pre-inspect" and self.state.get("preexisting_dependency_drift"):
            print("membership authority: usable; dependency drift only")
            for item in self.state["preexisting_dependency_drift"]:
                dependency = str(item.get("dependency") or "dependency").upper()
                print(
                    f"pre-existing drift:   {item.get('hub_id')} {dependency} "
                    f"generation {item.get('adopted_generation')} -> {item.get('current_generation')}"
                )
        if step == "final-inspect" and self.state.get("dependency_rectification_required"):
            print("membership result:    verified against frozen mutation target")
            for item in self.state["dependency_rectification_required"]:
                dependency = str(item.get("dependency") or "dependency").upper()
                print(
                    f"rectification:        {item.get('hub_id')} {dependency} "
                    f"generation {item.get('adopted_generation')} -> {item.get('current_generation')} required"
                )

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
        if self.state.get("public_url"):
            print(f"direct Hub URL:       {self.state['public_url']}")
        if self.state.get("network_ingress_url"):
            print(f"network ingress:      {self.state['network_ingress_url']}")
            print(f"ingress alias here:   {'yes' if self.state.get('serve_network_ingress') else 'no'}")
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
        if self.state.get("dependency_rectification_required"):
            print("dependency adoption:  rectification required")

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
