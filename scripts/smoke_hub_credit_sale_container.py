#!/usr/bin/env python3
"""
Pre-R2B smoke harness for HubCreditSale.

This script is intentionally narrower than RPC sync. It verifies that the
contract-side funding receipt is still shaped the way the R2A importer expects,
then delegates to tools/build_contracts.py so the repo's existing containerized
Foundry flow runs the Solidity build/tests.

Typical use from the repository root:

    python scripts/smoke_hub_credit_sale_container.py

Useful options:

    python scripts/smoke_hub_credit_sale_container.py --clean
    python scripts/smoke_hub_credit_sale_container.py --skip-forge
    python scripts/smoke_hub_credit_sale_container.py --no-docker
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


EXPECTED_EVENT = (
    ("bytes32", "purchaseId", True),
    ("address", "account", True),
    ("address", "payer", True),
    ("uint256", "creditsGranted", False),
    ("uint256", "amountPaidWei", False),
    ("string", "memo", False),
)
EXPECTED_EVENT_SIGNATURE = (
    "CreditPurchased(bytes32,address,address,uint256,uint256,string)"
)


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


def parse_event_fields(solidity_source: str) -> list[tuple[str, str, bool]]:
    match = re.search(
        r"event\s+CreditPurchased\s*\((?P<body>.*?)\)\s*;",
        solidity_source,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError("CreditPurchased event declaration was not found.")

    fields: list[tuple[str, str, bool]] = []
    for raw_field in match.group("body").split(","):
        raw_field = normalize_space(raw_field)
        if not raw_field:
            continue

        tokens = raw_field.split(" ")
        if len(tokens) not in {2, 3}:
            raise ValueError(f"Could not parse event field: {raw_field!r}")

        indexed = "indexed" in tokens
        cleaned = [token for token in tokens if token != "indexed"]

        if len(cleaned) != 2:
            raise ValueError(f"Could not parse event field: {raw_field!r}")

        field_type, field_name = cleaned
        fields.append((field_type, field_name, indexed))

    return fields


def check_event_shape(repo: Path) -> CheckResult:
    source_path = repo / "contracts" / "src" / "HubCreditSale.sol"
    if not source_path.exists():
        return CheckResult(
            "event_shape",
            False,
            f"Missing contract source: {source_path.relative_to(repo).as_posix()}",
        )

    source = source_path.read_text(encoding="utf-8")
    fields = tuple(parse_event_fields(source))

    if fields != EXPECTED_EVENT:
        return CheckResult(
            "event_shape",
            False,
            "CreditPurchased event shape changed. "
            f"Expected {EXPECTED_EVENT!r}; found {fields!r}.",
        )

    return CheckResult(
        "event_shape",
        True,
        f"CreditPurchased matches {EXPECTED_EVENT_SIGNATURE}",
    )


def check_contract_tests_present(repo: Path) -> CheckResult:
    test_path = repo / "contracts" / "test" / "HubCreditSale.t.sol"
    if not test_path.exists():
        return CheckResult(
            "contract_tests_present",
            False,
            f"Missing contract test: {test_path.relative_to(repo).as_posix()}",
        )

    test_source = test_path.read_text(encoding="utf-8")
    expected_tests = (
        "testQuoteUsesConfiguredPrice",
        "testPurchaseForwardsPaymentAndRecordsNonce",
        "testPurchaseRefundsOverpayment",
        "testPurchaseRejectsUnderpayment",
        "testOwnerCanPausePurchases",
    )
    missing = [name for name in expected_tests if name not in test_source]
    if missing:
        return CheckResult(
            "contract_tests_present",
            False,
            "Missing expected HubCreditSale tests: " + ", ".join(missing),
        )

    return CheckResult(
        "contract_tests_present",
        True,
        "HubCreditSale unit tests are present.",
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
        "contract": "contracts/src/HubCreditSale.sol",
        "event_signature": EXPECTED_EVENT_SIGNATURE,
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
            "Pre-R2B HubCreditSale smoke check using the repo's "
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
        default=Path("runtime/contract_smoke/hub_credit_sale_smoke.json"),
        help="Smoke report path, relative to repo root unless absolute.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = find_repo_root(args.repo_root or Path(__file__))

    report = args.report if args.report.is_absolute() else repo / args.report
    contract_report = repo / "runtime" / "contract_smoke" / "hub_credit_sale_build_report.json"

    print("HubCreditSale pre-R2B smoke")
    print(f"Repository root: {repo}")
    print(f"Expected event signature: {EXPECTED_EVENT_SIGNATURE}")

    static_checks: list[CheckResult] = []

    for check in (check_event_shape, check_contract_tests_present):
        try:
            result = check(repo)
        except Exception as exc:  # defensive: turn parser errors into check failures
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
        print("HubCreditSale pre-R2B smoke passed.")
        return 0

    print("HubCreditSale pre-R2B smoke failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
