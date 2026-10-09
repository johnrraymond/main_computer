#!/usr/bin/env python3
"""Operator harness for bounded QBFT native-mint control.

Normal operators may invoke this file directly, but ``mother_mutate_harness.py
native-mint*`` delegates here so Mother remains the single operator surface.

Mutation lifecycle:

    pre-inspect -> prep -> mutation gate -> do -> operation-inspect
    -> finalize -> final-inspect

``native-mint`` is a one-block reward pulse. ``native-mint-open`` creates a
finite reward window with an automatic OFF transition, and ``native-mint-close``
closes/cancels the latest recorded window early.  No operation creates an
indefinite mint authority.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
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
MUTATION_OPERATIONS = frozenset({"native-mint", "native-mint-open", "native-mint-close"})
SCHEMA = "main-computer.native-mint-mutate-harness.v1"


class HarnessError(RuntimeError):
    pass


def quote_command(argv: list[str]) -> str:
    if os.name == "nt":
        return " ".join(subprocess.list2cmdline([arg]) for arg in argv)
    return " ".join(shlex.quote(arg) for arg in argv)


def _run_with_live_stderr(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    if proc.stdout is None or proc.stderr is None:
        raise HarnessError("could not open native-mint control child pipes")
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    def drain_stdout() -> None:
        stdout_parts.append(proc.stdout.read())

    def drain_stderr() -> None:
        for line in proc.stderr:
            stderr_parts.append(line)
            print(line, end="", flush=True)

    out_thread = threading.Thread(target=drain_stdout, daemon=True)
    err_thread = threading.Thread(target=drain_stderr, daemon=True)
    out_thread.start()
    err_thread.start()
    returncode = proc.wait()
    out_thread.join()
    err_thread.join()
    return subprocess.CompletedProcess(
        args=argv,
        returncode=returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Main Computer Mother native-mint operator harness")
    parser.add_argument(
        "operation",
        choices=("native-mint-status", "native-mint", "native-mint-open", "native-mint-close"),
    )
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--to", help="wallet role (for example deployer) or explicit 0x address")
    parser.add_argument("--amount-wei")
    parser.add_argument("--amount-native")
    parser.add_argument("--wei-per-block")
    parser.add_argument("--native-per-block")
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--activation-lead-blocks", type=int, default=180)
    parser.add_argument("--close-lead-blocks", type=int, default=30)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--execute-mutations", action="store_true")
    parser.add_argument("--yes-i-know-this-mutates-target-host", action="store_true")
    return parser


class Harness:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo_root = Path(args.repo_root).resolve()
        self.runtime_state_root = self.repo_root / "runtime" / "state"
        self.operation = str(args.operation)
        self.network = str(args.network)
        self.to = str(args.to or "").strip()
        self._resumed_existing_run = False
        self._prepared_boundary_printed = False

        self._validate_surface()
        if self.operation == "native-mint-status":
            self.run_dir: Path | None = None
            self.state: dict[str, Any] = {}
            return

        existing = self._find_unfinished_run()
        if existing is not None:
            self.run_dir = existing
            self.state = self._read_state()
            self._resumed_existing_run = True
            return

        self.run_dir = (
            self.runtime_state_root
            / "mother"
            / "native-mint"
            / "harness-runs"
            / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}-{time.time_ns() % 1_000_000_000:09d}"
        )
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state = {
            "schema": SCHEMA,
            "operation": self.operation,
            "network": self.network,
            "request": self._request_signature(),
            "operation_id": None,
            "recipient": None,
            "recipient_source": None,
            "wei_per_block": None,
            "blocks": None,
            "max_mint_wei": None,
            "activation_block": None,
            "expiration_block": None,
            "manual_close_block": None,
            "old_genesis_sha256": None,
            "new_genesis_sha256": None,
            "target_count": None,
            "last_completed_step": None,
        }
        self._write_state()

    def _validate_surface(self) -> None:
        if self.operation == "native-mint-status":
            if any((self.to, self.args.amount_wei, self.args.amount_native, self.args.wei_per_block, self.args.native_per_block)):
                raise HarnessError("native-mint-status does not take recipient or amount arguments")
            return
        if self.operation == "native-mint":
            if not self.to:
                raise HarnessError("native-mint requires --to")
            if self.args.wei_per_block is not None or self.args.native_per_block is not None:
                raise HarnessError("native-mint uses --amount-wei/--amount-native, not per-block amount flags")
            if self.args.amount_wei is None and self.args.amount_native is None:
                raise HarnessError("native-mint requires --amount-wei or --amount-native")
            return
        if self.operation == "native-mint-open":
            if not self.to:
                raise HarnessError("native-mint-open requires --to")
            if self.args.amount_wei is not None or self.args.amount_native is not None:
                raise HarnessError("native-mint-open uses --wei-per-block/--native-per-block")
            if self.args.wei_per_block is None and self.args.native_per_block is None:
                raise HarnessError("native-mint-open requires --wei-per-block or --native-per-block")
            if int(self.args.blocks) < 2:
                raise HarnessError("native-mint-open requires --blocks >= 2; use native-mint for a one-block pulse")
            return
        if self.operation == "native-mint-close":
            if any((self.to, self.args.amount_wei, self.args.amount_native, self.args.wei_per_block, self.args.native_per_block)):
                raise HarnessError("native-mint-close does not take recipient or amount arguments")
            return
        raise HarnessError(f"unsupported operation {self.operation}")

    def _request_signature(self) -> dict[str, Any]:
        return {
            "to": self.to or None,
            "amount_wei": self.args.amount_wei,
            "amount_native": self.args.amount_native,
            "wei_per_block": self.args.wei_per_block,
            "native_per_block": self.args.native_per_block,
            "blocks": int(self.args.blocks),
            "activation_lead_blocks": int(self.args.activation_lead_blocks),
            "close_lead_blocks": int(self.args.close_lead_blocks),
        }

    def _find_unfinished_run(self) -> Path | None:
        root = self.runtime_state_root / "mother" / "native-mint" / "harness-runs"
        if not root.is_dir():
            return None
        wanted = self._request_signature()
        candidates: list[tuple[int, str, Path]] = []
        for state_path in root.glob("*/harness-state.json"):
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
                continue
            if payload.get("operation") != self.operation or payload.get("network") != self.network:
                continue
            if payload.get("request") != wanted or payload.get("last_completed_step") == "final-inspect":
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

    def _state_path(self) -> Path:
        if self.run_dir is None:
            raise HarnessError("read-only status has no harness state")
        return self.run_dir / "harness-state.json"

    def _write_state(self) -> None:
        path = self._state_path()
        path.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _read_state(self) -> dict[str, Any]:
        payload = json.loads(self._state_path().read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise HarnessError("native-mint harness state is malformed")
        return payload

    def _required_state(self, key: str) -> str:
        value = self.state.get(key)
        if not isinstance(value, str) or not value:
            raise HarnessError(f"harness state is missing {key}")
        return value

    def control_cmd(self, *parts: str) -> list[str]:
        return [
            sys.executable,
            "-m",
            "tools.native_mint_control",
            "--runtime-state-root",
            str(self.runtime_state_root),
            "--json",
            *parts,
        ]

    def inspect_cmd(self) -> list[str]:
        return self.control_cmd("inspect", self.network)

    def prep_cmd(self) -> list[str]:
        mode = {
            "native-mint": "mint",
            "native-mint-open": "open",
            "native-mint-close": "close",
        }[self.operation]
        cmd = self.control_cmd("prep", mode, self.network)
        if self.operation in {"native-mint", "native-mint-open"}:
            cmd.extend(["--to", self.to])
        if self.operation == "native-mint":
            if self.args.amount_wei is not None:
                cmd.extend(["--amount-wei", str(self.args.amount_wei)])
            else:
                cmd.extend(["--amount-native", str(self.args.amount_native)])
            cmd.extend(["--activation-lead-blocks", str(self.args.activation_lead_blocks)])
        elif self.operation == "native-mint-open":
            if self.args.wei_per_block is not None:
                cmd.extend(["--wei-per-block", str(self.args.wei_per_block)])
            else:
                cmd.extend(["--native-per-block", str(self.args.native_per_block)])
            cmd.extend(["--blocks", str(self.args.blocks)])
            cmd.extend(["--activation-lead-blocks", str(self.args.activation_lead_blocks)])
        else:
            cmd.extend(["--close-lead-blocks", str(self.args.close_lead_blocks)])
        return cmd

    def do_cmd(self) -> list[str]:
        return self.control_cmd("do", "--operation-id", self._required_state("operation_id"))

    def operation_inspect_cmd(self) -> list[str]:
        return self.control_cmd("operation-inspect", "--operation-id", self._required_state("operation_id"))

    def finalize_cmd(self) -> list[str]:
        return self.control_cmd("finalize", "--operation-id", self._required_state("operation_id"))

    def _mutation_authorized(self) -> bool:
        return bool(self.args.execute_mutations and self.args.yes_i_know_this_mutates_target_host)

    def run(self) -> int:
        if bool(self.args.execute_mutations) != bool(self.args.yes_i_know_this_mutates_target_host):
            raise HarnessError(
                "live native-mint mutation requires both --execute-mutations and --yes-i-know-this-mutates-target-host"
            )
        if self.operation == "native-mint-status":
            if self._mutation_authorized():
                raise HarnessError("native-mint-status is read-only and does not accept mutation authorization flags")
            result = self._invoke("status", self.inspect_cmd(), live_stderr=False)
            self._print_inspection("native-mint-status", result)
            return 0

        if self._resumed_existing_run:
            print(f"Resuming unfinished {self.operation} on {self.network}.")

        completed = self.state.get("last_completed_step")
        start = 0 if completed not in STEPS else STEPS.index(str(completed)) + 1
        for step in STEPS[start:]:
            if step in MUTATING_STEPS and not self._mutation_authorized():
                self._print_prepared_boundary()
                return 0
            command = {
                "pre-inspect": self.inspect_cmd,
                "prep": self.prep_cmd,
                "do": self.do_cmd,
                "operation-inspect": self.operation_inspect_cmd,
                "finalize": self.finalize_cmd,
                "final-inspect": self.inspect_cmd,
            }[step]()
            result = self._invoke(step, command, live_stderr=(step == "do"))
            self._validate(step, result)
            self._print_step(step, result)
            self.state["last_completed_step"] = step
            self._write_state()
        self._print_final_verification()
        print("NATIVE_MINT_MUTATE_HARNESS_COMPLETE")
        return 0

    def _invoke(self, step: str, argv: list[str], *, live_stderr: bool) -> dict[str, Any]:
        print(f"\n=== {step} ===")
        if self.run_dir is not None:
            index = STEPS.index(step) if step in STEPS else 0
            prefix = self.run_dir / f"{index:02d}-{step}"
            (prefix.with_suffix(".command.txt")).write_text(quote_command(argv) + "\n", encoding="utf-8")
        proc = _run_with_live_stderr(argv, cwd=self.repo_root) if live_stderr else subprocess.run(
            argv, cwd=self.repo_root, text=True, capture_output=True, check=False
        )
        if self.run_dir is not None:
            prefix = self.run_dir / f"{STEPS.index(step):02d}-{step}"
            prefix.with_suffix(".stdout.txt").write_text(proc.stdout, encoding="utf-8")
            prefix.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
        try:
            payload = json.loads(proc.stdout.strip())
        except json.JSONDecodeError as exc:
            stderr_lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
            diagnostic = " | ".join(stderr_lines[-8:])
            if diagnostic:
                raise HarnessError(
                    f"{step} returned non-JSON output (exit={proc.returncode}); child stderr: {diagnostic}"
                ) from exc
            raise HarnessError(f"{step} returned non-JSON output (exit={proc.returncode})") from exc
        if not isinstance(payload, dict):
            raise HarnessError(f"{step} returned a non-object JSON payload")
        if proc.returncode != 0 or payload.get("ok") is not True:
            code = payload.get("code")
            message = payload.get("message") or payload
            raise HarnessError(f"{step} failed (exit={proc.returncode}){f' [{code}]' if code else ''}: {message}")
        return payload

    def _validate(self, step: str, result: dict[str, Any]) -> None:
        if step == "prep":
            for key in (
                "operation_id", "recipient", "recipient_source", "wei_per_block", "blocks", "max_mint_wei",
                "activation_block", "expiration_block", "manual_close_block", "old_genesis_sha256",
                "new_genesis_sha256", "target_count",
            ):
                if key in result:
                    self.state[key] = result.get(key)
            if not self.state.get("operation_id"):
                raise HarnessError("prep did not return operation_id")
        elif step == "do":
            if result.get("status") not in {"applied", "finalized"}:
                raise HarnessError(f"do returned unexpected status {result.get('status')!r}")
        elif step == "operation-inspect":
            if result.get("status") != "verified":
                raise HarnessError("operation-inspect did not verify the native-mint operation")
        elif step == "finalize":
            if result.get("status") != "finalized":
                raise HarnessError("finalize did not finalize the native-mint operation")
        elif step in {"pre-inspect", "final-inspect"}:
            if result.get("status") != "observed":
                raise HarnessError(f"{step} did not return observed network state")

    def _print_inspection(self, label: str, result: dict[str, Any]) -> None:
        print(f"network:              {result.get('network', self.network)}")
        print(f"chain ID:             {result.get('chain_id')}")
        print(f"block:                {result.get('block_number')}")
        validators = result.get("validator_set")
        if isinstance(validators, list):
            print(f"validators:           {len(validators)}")
        print(f"genesis SHA256:       {result.get('genesis_sha256')}")
        active = result.get("active_window")
        if isinstance(active, dict):
            print(f"mint window:          {active.get('state')}")
            print(f"window operation:     {active.get('operation_id')}")
            print(f"recipient:            {active.get('recipient')}")
            print(f"wei/block:            {active.get('wei_per_block')}")
            print(f"activation block:     {active.get('activation_block')}")
            print(f"expiration block:     {active.get('effective_expiration_block')}")
        else:
            print("mint window:          none recorded")

    def _print_step(self, step: str, result: dict[str, Any]) -> None:
        if step in {"pre-inspect", "final-inspect"}:
            self._print_inspection(step, result)
            return
        if step == "prep":
            print(f"operation ID:         {result.get('operation_id')}")
            print(f"mode:                 {result.get('mode')}")
            if result.get("recipient"):
                print(f"recipient:            {result.get('recipient')} ({result.get('recipient_source')})")
            if result.get("wei_per_block"):
                print(f"wei/block:            {result.get('wei_per_block')}")
            if result.get("blocks"):
                print(f"reward blocks:        {result.get('blocks')}")
                print(f"max mint wei:         {result.get('max_mint_wei')}")
            if result.get("activation_block"):
                print(f"activation block:     {result.get('activation_block')}")
            if result.get("expiration_block"):
                print(f"automatic off block:  {result.get('expiration_block')}")
            if result.get("manual_close_block"):
                print(f"manual close block:   {result.get('manual_close_block')}")
            print(f"validator targets:    {result.get('target_count')}")
            print(f"old genesis SHA256:   {result.get('old_genesis_sha256')}")
            print(f"new genesis SHA256:   {result.get('new_genesis_sha256')}")
            return
        if step == "do":
            print(f"status:               {result.get('status')}")
            proof = result.get("proof")
            if isinstance(proof, dict):
                print(f"config rollout:       {'verified' if proof.get('config_rollout_verified') else 'not verified'}")
                if proof.get("mint_verified"):
                    print(f"native mint:          verified at block {proof.get('mint_block')}")
                    print(f"minted wei:           {proof.get('minted_wei')}")
                    print(f"automatic off:        block {proof.get('automatic_off_block')} reached")
                elif proof.get("window_staged"):
                    print(f"mint window:          staged; automatic off block {proof.get('expiration_block')}")
                elif proof.get("close_verified"):
                    print(f"mint window close:    verified at block {proof.get('close_block')}")
            return
        if step == "operation-inspect":
            print(f"mutation proof:       {result.get('status')}")
            return
        if step == "finalize":
            print(f"status:               {result.get('status')}")
            print(f"evidence:             {result.get('evidence_path')}")
            return

    def _print_prepared_boundary(self) -> None:
        if self._prepared_boundary_printed:
            return
        self._prepared_boundary_printed = True
        print("\nNative-mint operation is prepared but has not mutated validators.")
        print("Rerun the same command with:")
        command = ["python", ".\\mother_mutate_harness.py", self.operation, "--network", self.network]
        if self.operation in {"native-mint", "native-mint-open"}:
            command.extend(["--to", self.to])
        if self.operation == "native-mint":
            if self.args.amount_wei is not None:
                command.extend(["--amount-wei", str(self.args.amount_wei)])
            else:
                command.extend(["--amount-native", str(self.args.amount_native)])
            if int(self.args.activation_lead_blocks) != 180:
                command.extend(["--activation-lead-blocks", str(self.args.activation_lead_blocks)])
        elif self.operation == "native-mint-open":
            if self.args.wei_per_block is not None:
                command.extend(["--wei-per-block", str(self.args.wei_per_block)])
            else:
                command.extend(["--native-per-block", str(self.args.native_per_block)])
            command.extend(["--blocks", str(self.args.blocks)])
            if int(self.args.activation_lead_blocks) != 180:
                command.extend(["--activation-lead-blocks", str(self.args.activation_lead_blocks)])
        elif int(self.args.close_lead_blocks) != 30:
            command.extend(["--close-lead-blocks", str(self.args.close_lead_blocks)])
        command.extend(["--execute-mutations", "--yes-i-know-this-mutates-target-host"])
        print(" ".join(command))

    def _print_final_verification(self) -> None:
        print("\n=== final native-mint verification ===")
        print(f"operation:            {self.operation}")
        print(f"operation ID:         {self.state.get('operation_id')}")
        if self.state.get("recipient"):
            print(f"recipient:            {self.state.get('recipient')}")
        if self.state.get("max_mint_wei"):
            print(f"maximum minted wei:   {self.state.get('max_mint_wei')}")
        if self.state.get("expiration_block"):
            print(f"automatic off block:  {self.state.get('expiration_block')}")
        print("accepted state:       verified")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return Harness(args).run()
    except HarnessError as exc:
        print(f"NATIVE_MINT_MUTATE_HARNESS_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
