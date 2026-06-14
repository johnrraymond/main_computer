from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from main_computer.credit_units import (
    CREDIT_WEI_PER_CREDIT,
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
    require_credit_wei,
)
from main_computer.hub_credit_bridge_completion import normalize_bytes32, normalize_evm_address
from main_computer.hub_credit_ledger import HubCreditLedger
from main_computer.hub_credit_models import clean_account_id, clean_worker_id
from main_computer.hub_credit_withdrawal import compute_bridge_withdrawal_reconciliation


SUPPORTED_PROTECTED_NETWORKS = ("dev", "test", "testnet", "mainnet")
DEFAULT_PROTECTED_NETWORK = "dev"
DEFAULT_PROTECTED_REPORT_PATH = Path("runtime") / "temporal_lab" / "protected_pretest_report.json"
DEFAULT_PROTECTED_LEDGER_ROOT = Path("runtime") / "temporal_lab" / "protected_pretest_hub_credit_ledger"


class ProtectedModePretestError(ValueError):
    """Raised when protected-mode profile or ledger invariants fail."""


@dataclass(frozen=True)
class ProtectedNetworkProfile:
    network: str
    deployment_path: Path
    chain_id: int
    rpc_url: str
    hub_credit_bridge_escrow_address: str
    bridge_controller_address: str
    hub_admin_address: str
    smoke_client_address: str
    office_addresses: tuple[str, ...]
    payment_asset: str = "native"
    live_chain_enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "deployment_path": str(self.deployment_path),
            "chain_id": self.chain_id,
            "rpc_url": self.rpc_url,
            "hub_credit_bridge_escrow_address": self.hub_credit_bridge_escrow_address,
            "bridge_controller_address": self.bridge_controller_address,
            "hub_admin_address": self.hub_admin_address,
            "smoke_client_address": self.smoke_client_address,
            "office_addresses": list(self.office_addresses),
            "payment_asset": self.payment_asset,
            "live_chain_enabled": self.live_chain_enabled,
        }


@dataclass(frozen=True)
class ProtectedAmountProbe:
    input_text: str
    credit_wei: str
    display_credits: str
    json_round_trip_exact: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProtectedPretestConfig:
    repo_root: Path
    network: str = DEFAULT_PROTECTED_NETWORK
    deployment_path: Path | None = None
    ledger_root: Path | None = None
    report_path: Path | None = DEFAULT_PROTECTED_REPORT_PATH
    reset_ledger: bool = True
    live_chain: bool = False
    deposit_credits: str = "100"
    hold_credits: str = "10"
    charge_credits: str = "6"
    release_hold_credits: str = "4"
    worker_id: str = "protected-mode-worker-01"

    def resolved_deployment_path(self) -> Path:
        if self.deployment_path is not None:
            return self.deployment_path if self.deployment_path.is_absolute() else self.repo_root / self.deployment_path
        return self.repo_root / "runtime" / "deployments" / self.network / "latest.json"

    def resolved_ledger_root(self) -> Path | None:
        if self.ledger_root is None:
            return None
        return self.ledger_root if self.ledger_root.is_absolute() else self.repo_root / self.ledger_root

    def resolved_report_path(self) -> Path | None:
        if self.report_path is None:
            return None
        return self.report_path if self.report_path.is_absolute() else self.repo_root / self.report_path


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "docker-compose.dev.yml").exists()
            or (candidate / ".git").exists()
        ):
            return candidate
    return current


def _positive_chain_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProtectedModePretestError("chain_id must be an integer") from exc
    if parsed <= 0:
        raise ProtectedModePretestError("chain_id must be positive")
    return parsed


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtectedModePretestError(f"deployment profile not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProtectedModePretestError(f"deployment profile is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtectedModePretestError(f"deployment profile must be a JSON object: {path}")
    return payload


def normalize_network_name(network: str | None) -> str:
    clean = str(network or DEFAULT_PROTECTED_NETWORK).strip().lower()
    if clean not in SUPPORTED_PROTECTED_NETWORKS:
        allowed = ", ".join(SUPPORTED_PROTECTED_NETWORKS)
        raise ProtectedModePretestError(f"unsupported protected network {network!r}; expected one of: {allowed}")
    return clean


def load_protected_network_profile(
    *,
    repo_root: Path,
    network: str = DEFAULT_PROTECTED_NETWORK,
    deployment_path: Path | None = None,
    live_chain: bool = False,
) -> ProtectedNetworkProfile:
    clean_network = normalize_network_name(network)
    resolved_path = deployment_path or (repo_root / "runtime" / "deployments" / clean_network / "latest.json")
    resolved_path = resolved_path if resolved_path.is_absolute() else repo_root / resolved_path
    payload = _load_json(resolved_path)

    chain = payload.get("chain")
    if not isinstance(chain, Mapping):
        raise ProtectedModePretestError("deployment profile must include a chain object")
    chain_id = _positive_chain_id(chain.get("chain_id"))
    rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or "").strip()

    contracts = payload.get("contracts")
    if not isinstance(contracts, Mapping):
        contracts = payload.get("deployments")
    if not isinstance(contracts, Mapping):
        raise ProtectedModePretestError("deployment profile must include contracts/deployments")

    bridge = contracts.get("hub_credit_bridge_escrow")
    if not isinstance(bridge, Mapping):
        raise ProtectedModePretestError("deployment profile must include hub_credit_bridge_escrow")
    contract_address = normalize_evm_address(bridge.get("address"), field_name="hub_credit_bridge_escrow.address")
    bridge_controller = normalize_evm_address(
        bridge.get("bridge_controller_address") or (bridge.get("constructor_args") or [""])[0],
        field_name="hub_credit_bridge_escrow.bridge_controller_address",
    )
    bridge_chain_id = bridge.get("chain_id")
    if bridge_chain_id not in (None, "") and _positive_chain_id(bridge_chain_id) != chain_id:
        raise ProtectedModePretestError("hub_credit_bridge_escrow.chain_id does not match deployment chain.chain_id")

    hub_admin = payload.get("hub_admin")
    if not isinstance(hub_admin, Mapping):
        raise ProtectedModePretestError("deployment profile must include hub_admin")
    hub_admin_address = normalize_evm_address(hub_admin.get("address"), field_name="hub_admin.address")

    smoke_client = payload.get("smoke_client")
    if not isinstance(smoke_client, Mapping):
        raise ProtectedModePretestError("deployment profile must include smoke_client")
    smoke_client_address = normalize_evm_address(smoke_client.get("address"), field_name="smoke_client.address")

    offices = payload.get("offices")
    if not isinstance(offices, Sequence) or isinstance(offices, (str, bytes)) or not offices:
        raise ProtectedModePretestError("deployment profile must include one or more officer addresses")
    office_addresses: list[str] = []
    for index, office in enumerate(offices):
        if not isinstance(office, Mapping):
            raise ProtectedModePretestError(f"office #{index} must be an object")
        office_addresses.append(normalize_evm_address(office.get("address"), field_name=f"offices[{index}].address"))

    return ProtectedNetworkProfile(
        network=clean_network,
        deployment_path=resolved_path,
        chain_id=chain_id,
        rpc_url=rpc_url,
        hub_credit_bridge_escrow_address=contract_address,
        bridge_controller_address=bridge_controller,
        hub_admin_address=hub_admin_address,
        smoke_client_address=smoke_client_address,
        office_addresses=tuple(office_addresses),
        payment_asset=str(bridge.get("payment_asset") or "native"),
        live_chain_enabled=bool(live_chain),
    )


def protected_amount_probe(value: str) -> ProtectedAmountProbe:
    if not isinstance(value, str):
        raise ProtectedModePretestError("protected amount input must be a decimal string")
    if not value.strip():
        raise ProtectedModePretestError("protected amount input must be non-empty")
    if any(ch in value.lower() for ch in ("e", "x")):
        raise ProtectedModePretestError("protected amount input must be a plain decimal string, not exponent or hex")
    credit_wei = credit_decimal_text_to_wei(value, round_up=False)
    require_credit_wei(str(credit_wei), field_name="credit_wei", allow_zero=False)
    encoded = json.dumps({"amount": str(credit_wei)}, sort_keys=True)
    decoded = json.loads(encoded)
    return ProtectedAmountProbe(
        input_text=value,
        credit_wei=str(credit_wei),
        display_credits=credit_wei_to_decimal_text(credit_wei),
        json_round_trip_exact=decoded["amount"] == str(credit_wei),
    )


def deterministic_bytes32(*parts: object) -> str:
    joined = "|".join(str(part) for part in parts)
    return "0x" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


def assert_account_totals(account: Mapping[str, Any], *, available: int, held: int, spent: int, bridge_completed: int) -> None:
    actual = {
        "available": int(account.get("available_credit_wei") or 0),
        "held": int(account.get("held_credit_wei") or 0),
        "spent": int(account.get("spent_credit_wei") or 0),
        "bridge_completed": int(account.get("bridge_completed_credit_wei") or 0),
    }
    expected = {
        "available": available,
        "held": held,
        "spent": spent,
        "bridge_completed": bridge_completed,
    }
    if actual != expected:
        raise ProtectedModePretestError(f"account total mismatch: actual={actual}, expected={expected}")


def run_protected_mode_pretest(config: ProtectedPretestConfig) -> dict[str, Any]:
    clean_network = normalize_network_name(config.network)
    profile = load_protected_network_profile(
        repo_root=config.repo_root,
        network=clean_network,
        deployment_path=config.resolved_deployment_path(),
        live_chain=config.live_chain,
    )

    amount_probes = {
        "deposit": protected_amount_probe(config.deposit_credits),
        "hold": protected_amount_probe(config.hold_credits),
        "charge": protected_amount_probe(config.charge_credits),
        "release_hold": protected_amount_probe(config.release_hold_credits),
    }
    deposit_wei = int(amount_probes["deposit"].credit_wei)
    hold_wei = int(amount_probes["hold"].credit_wei)
    charge_wei = int(amount_probes["charge"].credit_wei)
    release_hold_wei = int(amount_probes["release_hold"].credit_wei)

    if charge_wei > hold_wei:
        raise ProtectedModePretestError("charge_credits cannot exceed hold_credits")
    if hold_wei + release_hold_wei >= deposit_wei:
        raise ProtectedModePretestError("deposit_credits must exceed hold_credits + release_hold_credits")

    tempdir: tempfile.TemporaryDirectory[str] | None = None
    ledger_root = config.resolved_ledger_root()
    if ledger_root is None:
        tempdir = tempfile.TemporaryDirectory(prefix="protected-mode-pretest-")
        ledger_root = Path(tempdir.name) / "hub_credit_ledger"
    elif config.reset_ledger and ledger_root.exists():
        shutil.rmtree(ledger_root)

    account_id = clean_account_id(f"protected-{clean_network}-smoke-client")
    worker_id = clean_worker_id(config.worker_id)
    deposit_id = normalize_bytes32(
        deterministic_bytes32("protected-mode-deposit", profile.network, profile.chain_id, profile.hub_credit_bridge_escrow_address, account_id)
    )
    completion_tx_hash = deterministic_bytes32("protected-mode-complete", deposit_id)
    settlement_request_id = f"protected-mode-settle-{profile.network}"
    release_request_id = f"protected-mode-release-{profile.network}"

    try:
        ledger = HubCreditLedger(ledger_root)
        initial_status = ledger.status()

        deposit_result = ledger.record_completed_bridge_deposit(
            account_id=account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
            memo="protected-mode dev bridge completion pretest",
            metadata={
                "protected_mode_pretest": True,
                "network": profile.network,
                "bridge_controller_address": profile.bridge_controller_address,
            },
        )
        assert_account_totals(
            deposit_result["account"],
            available=deposit_wei,
            held=0,
            spent=0,
            bridge_completed=deposit_wei,
        )

        duplicate_deposit = ledger.record_completed_bridge_deposit(
            account_id=account_id,
            owner_address=profile.smoke_client_address,
            chain_completed_credit_wei=str(deposit_wei),
            deposit_id=deposit_id,
            completion_tx_hash=completion_tx_hash,
            chain_id=profile.chain_id,
            contract_address=profile.hub_credit_bridge_escrow_address,
            completed_units=deposit_wei,
            deposit_amount_units=deposit_wei,
        )
        if not duplicate_deposit["idempotent"]:
            raise ProtectedModePretestError("duplicate bridge completion did not replay idempotently")

        hold_result = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=settlement_request_id,
            credit_wei=str(hold_wei),
            memo="protected-mode settlement hold",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            hold_result["account"],
            available=deposit_wei - hold_wei,
            held=hold_wei,
            spent=0,
            bridge_completed=deposit_wei,
        )

        duplicate_hold = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=settlement_request_id,
            credit_wei=str(hold_wei),
        )
        if not duplicate_hold["idempotent"]:
            raise ProtectedModePretestError("duplicate hold did not replay idempotently")

        charge_result = ledger.charge_hold_credit_wei(
            hold_id=hold_result["hold"]["hold_id"],
            charged_credit_wei=str(charge_wei),
            worker_node_id=worker_id,
            memo="protected-mode charge settlement",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            charge_result["account"],
            available=deposit_wei - charge_wei,
            held=0,
            spent=charge_wei,
            bridge_completed=deposit_wei,
        )

        duplicate_charge = ledger.charge_hold_credit_wei(
            hold_id=hold_result["hold"]["hold_id"],
            charged_credit_wei=str(charge_wei),
            worker_node_id=worker_id,
        )
        if not duplicate_charge["idempotent"]:
            raise ProtectedModePretestError("duplicate charge did not replay idempotently")

        release_hold = ledger.create_hold_credit_wei(
            account_id=account_id,
            request_id=release_request_id,
            credit_wei=str(release_hold_wei),
            memo="protected-mode failure-path hold",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        release_result = ledger.release_hold(
            hold_id=release_hold["hold"]["hold_id"],
            reason="protected-mode failure release",
            metadata={"protected_mode_pretest": True, "network": profile.network},
        )
        assert_account_totals(
            release_result["account"],
            available=deposit_wei - charge_wei,
            held=0,
            spent=charge_wei,
            bridge_completed=deposit_wei,
        )

        duplicate_release = ledger.release_hold(
            hold_id=release_hold["hold"]["hold_id"],
            reason="protected-mode duplicate release",
        )
        if not duplicate_release["idempotent"]:
            raise ProtectedModePretestError("duplicate release did not replay idempotently")

        insufficient_rejected = False
        try:
            ledger.create_hold_credit_wei(
                account_id=account_id,
                request_id=f"protected-mode-overdraft-{profile.network}",
                credit_wei=str(deposit_wei + CREDIT_WEI_PER_CREDIT),
            )
        except ValueError:
            insufficient_rejected = True
        if not insufficient_rejected:
            raise ProtectedModePretestError("overdraft hold was not rejected")

        active_hold_reconciliation = compute_bridge_withdrawal_reconciliation(
            deposit_units=deposit_wei,
            finalized_spend_units=charge_wei,
            active_hold_units=release_hold_wei,
            already_rectified_units=0,
            already_withdrawn_units=0,
        )
        if active_hold_reconciliation.can_withdraw:
            raise ProtectedModePretestError("withdrawal reconciliation did not block active holds")

        withdrawal_reconciliation = compute_bridge_withdrawal_reconciliation(
            deposit_units=deposit_wei,
            finalized_spend_units=charge_wei,
            active_hold_units=0,
            already_rectified_units=0,
            already_withdrawn_units=0,
        )
        if not withdrawal_reconciliation.can_withdraw:
            raise ProtectedModePretestError(f"withdrawal reconciliation unexpectedly blocked: {withdrawal_reconciliation.block_reason}")
        if withdrawal_reconciliation.unrectified_units != charge_wei:
            raise ProtectedModePretestError("withdrawal reconciliation unrectified units do not equal finalized spend")
        if withdrawal_reconciliation.withdrawable_units != deposit_wei - charge_wei:
            raise ProtectedModePretestError("withdrawal reconciliation withdrawable units are not conserved")

        final_status = ledger.status()
        final_totals = final_status["totals"]
        if int(final_totals["available_credit_wei"]) + int(final_totals["spent_credit_wei"]) != deposit_wei:
            raise ProtectedModePretestError("available + spent does not equal completed bridge funding")
        if int(final_totals["held_credit_wei"]) != 0:
            raise ProtectedModePretestError("held balance did not return to zero")

        report = {
            "ok": True,
            "mode": "protected-mode-bridge-credit-pretest-v1",
            "network_profile": profile.as_dict(),
            "live_chain": bool(config.live_chain),
            "live_chain_note": "bare/default smoke performs no RPC writes; this pretest uses the existing HubCreditLedger and deployment profile",
            "ledger_root": str(ledger_root),
            "account_id": account_id,
            "worker_id": worker_id,
            "amount_probes": {key: probe.as_dict() for key, probe in amount_probes.items()},
            "steps": {
                "initial_status": initial_status,
                "bridge_deposit_completed": deposit_result,
                "bridge_deposit_duplicate": duplicate_deposit,
                "hold_created": hold_result,
                "hold_duplicate": duplicate_hold,
                "hold_charged": charge_result,
                "charge_duplicate": duplicate_charge,
                "failure_hold_created": release_hold,
                "failure_hold_released": release_result,
                "release_duplicate": duplicate_release,
                "overdraft_rejected": insufficient_rejected,
                "withdrawal_reconciliation_with_active_hold": active_hold_reconciliation.as_dict(),
                "withdrawal_reconciliation": withdrawal_reconciliation.as_dict(),
                "final_status": final_status,
            },
            "invariants": {
                "bigint_decimal_strings_round_trip": all(probe.json_round_trip_exact for probe in amount_probes.values()),
                "profile_addresses_validated": True,
                "bridge_deposit_completion_idempotent": bool(duplicate_deposit["idempotent"]),
                "hold_idempotent": bool(duplicate_hold["idempotent"]),
                "charge_idempotent": bool(duplicate_charge["idempotent"]),
                "release_idempotent": bool(duplicate_release["idempotent"]),
                "overdraft_rejected": insufficient_rejected,
                "active_hold_blocks_withdrawal": not active_hold_reconciliation.can_withdraw,
                "withdrawal_reconciliation_conserved": (
                    withdrawal_reconciliation.can_withdraw
                    and withdrawal_reconciliation.unrectified_units == charge_wei
                    and withdrawal_reconciliation.withdrawable_units == deposit_wei - charge_wei
                ),
                "final_available_plus_spent_equals_deposit": (
                    int(final_totals["available_credit_wei"]) + int(final_totals["spent_credit_wei"]) == deposit_wei
                ),
                "final_held_zero": int(final_totals["held_credit_wei"]) == 0,
            },
        }

        report_path = config.resolved_report_path()
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            report["report_path"] = str(report_path)
        return report
    finally:
        if tempdir is not None:
            tempdir.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Protected-mode bridge-credit pretest smoke.")
    parser.add_argument("--network", choices=SUPPORTED_PROTECTED_NETWORKS, default=DEFAULT_PROTECTED_NETWORK)
    parser.add_argument("--deployment", type=Path, default=None)
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_PROTECTED_LEDGER_ROOT)
    parser.add_argument("--keep-ledger", action="store_true", help="Do not delete/recreate the pretest HubCreditLedger root before running.")
    parser.add_argument("--report", type=Path, default=DEFAULT_PROTECTED_REPORT_PATH)
    parser.add_argument("--deposit-credits", default="100")
    parser.add_argument("--hold-credits", default="10")
    parser.add_argument("--charge-credits", default="6")
    parser.add_argument("--release-hold-credits", default="4")
    parser.add_argument("--worker-id", default="protected-mode-worker-01")
    parser.add_argument(
        "--live-chain",
        action="store_true",
        help="Mark the report as live-chain mode. This pretest still performs no RPC writes; future patches can wire this to existing bridge smokes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = find_repo_root(Path.cwd())

    try:
        report = run_protected_mode_pretest(
            ProtectedPretestConfig(
                repo_root=repo_root,
                network=args.network,
                deployment_path=args.deployment,
                ledger_root=args.ledger_root,
                report_path=args.report,
                reset_ledger=not args.keep_ledger,
                live_chain=args.live_chain,
                deposit_credits=args.deposit_credits,
                hold_credits=args.hold_credits,
                charge_credits=args.charge_credits,
                release_hold_credits=args.release_hold_credits,
                worker_id=args.worker_id,
            )
        )
    except ProtectedModePretestError as exc:
        print(f"FAIL: {exc}")
        return 2
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print("PASS: protected-mode bridge-credit pretest succeeded")
    if report.get("report_path"):
        print(f"report: {report['report_path']}")
    print(f"network: {report['network_profile']['network']}")
    print(f"chain_id: {report['network_profile']['chain_id']}")
    print(f"hub_credit_bridge_escrow: {report['network_profile']['hub_credit_bridge_escrow_address']}")
    print(f"account_id: {report['account_id']}")
    print(f"final_available_credit_wei: {report['steps']['final_status']['totals']['available_credit_wei']}")
    print(f"final_spent_credit_wei: {report['steps']['final_status']['totals']['spent_credit_wei']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
