#!/usr/bin/env python3
"""
Container-aware smoke harness for HubCreditBridgeEscrow.

This validates the replacement escrow/rectification contract shape before the
hub relies on it for bridge-side Compute Credit accounting.

Typical use from the repository root:

    python scripts/smoke_hub_credit_bridge_escrow_container.py

Useful options:

    python scripts/smoke_hub_credit_bridge_escrow_container.py --clean
    python scripts/smoke_hub_credit_bridge_escrow_container.py --skip-forge
    python scripts/smoke_hub_credit_bridge_escrow_container.py --no-docker
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


EXPECTED_EVENTS = {
    "CreditDeposited": (
        ("bytes32", "depositId", True),
        ("address", "account", True),
        ("address", "payer", True),
        ("uint256", "amountUnits", False),
        ("string", "memo", False),
    ),
    "SpendRectified": (
        ("bytes32", "rectificationId", True),
        ("address", "account", True),
        ("uint256", "amountUnits", False),
        ("uint256", "cumulativeRectifiedUnits", False),
        ("string", "memo", False),
    ),
    "WithdrawalReleased": (
        ("bytes32", "withdrawalId", True),
        ("address", "account", True),
        ("address", "recipient", True),
        ("uint256", "amountUnits", False),
        ("string", "memo", False),
    ),
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


@dataclass
class CommandResult:
    command: list[str]
    cwd: str
    ok: bool
    returncode: int


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            and (candidate / "contracts" / "foundry.toml").exists()
            and (candidate / "tools" / "build_contracts.py").exists()
        ):
            return candidate

    raise RuntimeError(
        "Could not find repository root containing new_patch.py, "
        "contracts/foundry.toml, and tools/build_contracts.py."
    )


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_event_fields(solidity_source: str, event_name: str) -> tuple[tuple[str, str, bool], ...]:
    match = re.search(
        rf"event\s+{re.escape(event_name)}\s*\((?P<body>.*?)\)\s*;",
        solidity_source,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"{event_name} event declaration was not found.")

    fields: list[tuple[str, str, bool]] = []
    for raw_field in match.group("body").split(","):
        raw_field = normalize_space(raw_field)
        if not raw_field:
            continue

        tokens = raw_field.split(" ")
        if len(tokens) not in {2, 3}:
            raise ValueError(f"Could not parse {event_name} event field: {raw_field!r}")

        indexed = "indexed" in tokens
        cleaned = [token for token in tokens if token != "indexed"]

        if len(cleaned) != 2:
            raise ValueError(f"Could not parse {event_name} event field: {raw_field!r}")

        field_type, field_name = cleaned
        fields.append((field_type, field_name, indexed))

    return tuple(fields)


def check_event_shapes(repo: Path) -> CheckResult:
    source_path = repo / "contracts" / "src" / "HubCreditBridgeEscrow.sol"
    if not source_path.exists():
        return CheckResult(
            "event_shapes",
            False,
            f"Missing contract source: {source_path.relative_to(repo).as_posix()}",
        )

    source = source_path.read_text(encoding="utf-8")
    mismatches: list[str] = []

    for event_name, expected in EXPECTED_EVENTS.items():
        found = parse_event_fields(source, event_name)
        if found != expected:
            mismatches.append(f"{event_name}: expected {expected!r}; found {found!r}")

    if mismatches:
        return CheckResult("event_shapes", False, " ; ".join(mismatches))

    return CheckResult(
        "event_shapes",
        True,
        "CreditDeposited, SpendRectified, and WithdrawalReleased event shapes match bridge expectations.",
    )


def check_contract_tests_present(repo: Path) -> CheckResult:
    test_path = repo / "contracts" / "test" / "HubCreditBridgeEscrow.t.sol"
    if not test_path.exists():
        return CheckResult(
            "contract_tests_present",
            False,
            f"Missing contract test: {test_path.relative_to(repo).as_posix()}",
        )

    test_source = test_path.read_text(encoding="utf-8")
    expected_tests = (
        "testDepositRectifyAndReleaseWithdrawal",
        "testDuplicateRectificationIdDoesNotDoubleCount",
        "testDuplicateWithdrawalIdDoesNotDoublePay",
        "testNonBridgeCannotRectifyOrReleaseWithdrawal",
        "testConflictingIdempotencyIdsAreRejected",
    )
    missing = [name for name in expected_tests if name not in test_source]
    if missing:
        return CheckResult(
            "contract_tests_present",
            False,
            "Missing expected HubCreditBridgeEscrow tests: " + ", ".join(missing),
        )

    return CheckResult(
        "contract_tests_present",
        True,
        "HubCreditBridgeEscrow unit tests are present.",
    )


def run_contract_tests(
    repo: Path,
    *,
    clean: bool,
    no_docker: bool,
    report: Path,
) -> CommandResult:
    command = [
        sys.executable,
        "tools/build_contracts.py",
        "--repo-root",
        str(repo),
        "--project",
        "contracts",
        "--test",
        "--report",
        str(report),
    ]

    if clean:
        command.append("--clean")
    if no_docker:
        command.append("--no-docker")

    print()
    print("$ " + " ".join(command))
    print(f"  cwd: {repo}")
    completed = subprocess.run(
        command,
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return CommandResult(
        command=command,
        cwd=str(repo),
        ok=completed.returncode == 0,
        returncode=completed.returncode,
    )


def write_smoke_report(
    report_path: Path,
    *,
    repo: Path,
    static_checks: list[CheckResult],
    forge_result: CommandResult | None,
    ok: bool,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "repo_root": str(repo),
        "contract": "contracts/src/HubCreditBridgeEscrow.sol",
        "event_signatures": {
            "CreditDeposited": "CreditDeposited(bytes32,address,address,uint256,string)",
            "SpendRectified": "SpendRectified(bytes32,address,uint256,uint256,string)",
            "WithdrawalReleased": "WithdrawalReleased(bytes32,address,address,uint256,string)",
        },
        "static_checks": [asdict(item) for item in static_checks],
        "forge": asdict(forge_result) if forge_result else None,
    }
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "HubCreditBridgeEscrow smoke check using the repo's "
            "container-aware Foundry wrapper."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root. Defaults to auto-detection from this script.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Run forge clean before forge build/test.",
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Disable Docker fallback in tools/build_contracts.py.",
    )
    parser.add_argument(
        "--skip-forge",
        action="store_true",
        help="Only run static contract/event-shape checks.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("runtime/contract_smoke/hub_credit_bridge_escrow_smoke.json"),
        help="Smoke report path, relative to repo root unless absolute.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = find_repo_root(args.repo_root or Path(__file__))

    report = args.report if args.report.is_absolute() else repo / args.report
    contract_report = repo / "runtime" / "contract_smoke" / "hub_credit_bridge_escrow_build_report.json"

    print("HubCreditBridgeEscrow smoke")
    print(f"Repository root: {repo}")

    static_checks: list[CheckResult] = []

    for check in (check_event_shapes, check_contract_tests_present):
        try:
            result = check(repo)
        except Exception as exc:
            result = CheckResult(check.__name__, False, str(exc))
        static_checks.append(result)
        status = "ok" if result.ok else "failed"
        print(f"  [{status}] {result.name}: {result.detail}")

    forge_result: CommandResult | None = None
    ok = all(result.ok for result in static_checks)

    if ok and not args.skip_forge:
        forge_result = run_contract_tests(
            repo,
            clean=args.clean,
            no_docker=args.no_docker,
            report=contract_report,
        )
        ok = forge_result.ok
    elif args.skip_forge:
        print()
        print("Skipping Foundry build/test because --skip-forge was provided.")

    write_smoke_report(
        report,
        repo=repo,
        static_checks=static_checks,
        forge_result=forge_result,
        ok=ok,
    )

    print()
    print(f"Wrote smoke report: {report}")

    if ok:
        print("HubCreditBridgeEscrow smoke passed.")
        return 0

    print("HubCreditBridgeEscrow smoke failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
