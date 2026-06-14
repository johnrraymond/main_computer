from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.protected_mode_pretest import (
    ProtectedModePretestError,
    ProtectedPretestConfig,
    load_protected_network_profile,
    protected_amount_probe,
    run_protected_mode_pretest,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_protected_mode_smoke_script_bootstraps_repo_root_for_path_invocation() -> None:
    source = (REPO_ROOT / "scripts" / "smoke_protected_mode.py").read_text(encoding="utf-8")

    assert "REPO_ROOT = Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0, str(REPO_ROOT))" in source
    assert source.index("sys.path.insert(0, str(REPO_ROOT))") < source.index("from main_computer.protected_mode_pretest")


def test_all_protected_network_profiles_are_well_shaped() -> None:
    for network in ("dev", "test", "testnet", "mainnet"):
        profile = load_protected_network_profile(repo_root=REPO_ROOT, network=network)

        assert profile.network == network
        assert profile.chain_id > 0
        assert profile.hub_credit_bridge_escrow_address.startswith("0x")
        assert len(profile.hub_credit_bridge_escrow_address) == 42
        assert profile.bridge_controller_address.startswith("0x")
        assert len(profile.bridge_controller_address) == 42
        assert profile.hub_admin_address.startswith("0x")
        assert profile.smoke_client_address.startswith("0x")
        assert len(profile.office_addresses) >= 1


def test_protected_amount_probe_requires_plain_decimal_string_and_round_trips_bigint() -> None:
    probe = protected_amount_probe("1.234567890123456789")

    assert probe.credit_wei == "1234567890123456789"
    assert probe.display_credits == "1.234567890123456789"
    assert probe.json_round_trip_exact is True

    with pytest.raises(ProtectedModePretestError):
        protected_amount_probe("1e18")

    with pytest.raises(ProtectedModePretestError):
        protected_amount_probe(1)  # type: ignore[arg-type]


def test_bare_protected_mode_pretest_uses_bridge_credit_ledger_and_preserves_invariants(tmp_path) -> None:
    report_path = tmp_path / "protected_report.json"
    report = run_protected_mode_pretest(
        ProtectedPretestConfig(
            repo_root=REPO_ROOT,
            network="dev",
            ledger_root=tmp_path / "ledger",
            report_path=report_path,
            deposit_credits="100",
            hold_credits="10",
            charge_credits="6",
            release_hold_credits="4",
        )
    )

    assert report["ok"] is True
    assert report["network_profile"]["network"] == "dev"
    assert report["live_chain"] is False
    assert Path(report["ledger_root"]).exists()
    assert report_path.exists()

    invariants = report["invariants"]
    assert invariants["bigint_decimal_strings_round_trip"] is True
    assert invariants["bridge_deposit_completion_idempotent"] is True
    assert invariants["hold_idempotent"] is True
    assert invariants["charge_idempotent"] is True
    assert invariants["release_idempotent"] is True
    assert invariants["overdraft_rejected"] is True
    assert invariants["active_hold_blocks_withdrawal"] is True
    assert invariants["withdrawal_reconciliation_conserved"] is True
    assert invariants["final_available_plus_spent_equals_deposit"] is True
    assert invariants["final_held_zero"] is True

    final_totals = report["steps"]["final_status"]["totals"]
    assert final_totals["available_credit_wei"] == "94000000000000000000"
    assert final_totals["spent_credit_wei"] == "6000000000000000000"
    assert final_totals["held_credit_wei"] == "0"

    persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted_report["ok"] is True
    assert persisted_report["account_id"] == "protected-dev-smoke-client"


def test_cli_bare_mode_writes_report_and_returns_success(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    ledger_root = tmp_path / "ledger"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_protected_mode.py",
            "--report",
            str(report_path),
            "--ledger-root",
            str(ledger_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert result.returncode == 0
    assert "PASS: protected-mode bridge-credit pretest succeeded" in result.stdout
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "protected-mode-bridge-credit-pretest-v1"
    assert report["network_profile"]["network"] == "dev"


def test_invalid_deployment_profile_fails_closed(tmp_path) -> None:
    bad_profile = tmp_path / "latest.json"
    bad_profile.write_text(
        json.dumps(
            {
                "chain": {"chain_id": 42424242, "rpc_url": "http://127.0.0.1:18545"},
                "contracts": {
                    "hub_credit_bridge_escrow": {
                        "address": "not-an-address",
                        "bridge_controller_address": "0x" + "1" * 40,
                    }
                },
                "hub_admin": {"address": "0x" + "2" * 40},
                "smoke_client": {"address": "0x" + "3" * 40},
                "offices": [{"address": "0x" + "4" * 40}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_protected_network_profile(repo_root=REPO_ROOT, network="dev", deployment_path=bad_profile)
