"""Self-contained tests for the independent gate; no V3 kernel is imported."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools import nanojev_contract_v3_independent_behavior as gate

IDS = [
    "single_odd_wei_silence",
    "two_stage_double_extension",
    "three_stage_mixed_timeout_awards",
    "four_stage_partial_withdraw_refund",
    "five_stage_progressive_silence_policies",
    "large_odd_wei_conditional_disposition",
]


def sample_report():
    valid = [
        {"id": name, "expect": "accept", "result": "PASS_FORGE", "passed_forge_tests": 34,
         "expected_forge_tests": 34, "spec_sha256": str(i + 1) * 64,
         "solidity_sha256": str(i + 2) * 64, "workspace": f"{name}.workspace"}
        for i, name in enumerate(IDS)
    ]
    invalid = [
        {"id": f"invalid_{i}", "expect": "reject", "result": "PASS_REFUSED"}
        for i in range(9)
    ]
    return {"schema": gate.REPORT_SCHEMA, "status": "PASS_FORGE_V3_GOVERNANCE_HELDOUT_NOT_CERTIFIED",
            "forge": "PASS_LOCAL_DOCKER_FORGE", "kernel_frozen": True, "blockers": [],
            "gold_independence": "NOT_INDEPENDENT_EXPERT_GOLD", "accepted_contracts": 6,
            "rejected_counterexamples": 9, "passed_forge_tests": 203, "total_forge_tests": 203,
            "kernel_sha256": {"frozen_kernel.py": "z" * 64},
            "corpus_manifest_sha256": "a" * 64, "cases": valid + invalid}


def send(actor, method, expect, changes=None, value="0"):
    result = {"type": "send", "actor": actor, "signature": method + "()", "args": [],
              "expect": expect, "value_wei": value}
    if changes is not None:
        result["changes_wei"] = changes
    return result


def sample_gold(report):
    result = gate.gold_template(report)
    result["authorship"] = {"reviewer": "Independent reviewer sample", "reference_model_used": False,
                            "method": "Hand-labeled synthetic transfers with no import of the compiler reference model"}
    for case in result["cases"]:
        case["sequences"] = [
            {"id": "manual_boundary_and_recovery", "terminal": True,
             "steps": [
                 send("payer", "fund", "success", {"payer": "-10", "contract": "10"}, "10"),
                 send("worker", "withdraw", "revert", {"worker": "0", "contract": "0"}),
                 {"type": "time", "advance_seconds": 11},
                 send("outsider", "steal", "revert"),
                 send("worker", "withdraw", "success", {"worker": "10", "contract": "-10"}),
                 {"type": "assert", "contract_balance_wei": "0"},
             ],
             "final": {"contract_balance_wei": "0", "balances_delta_wei": {"payer": "-10", "worker": "10"}}},
            {"id": "ordering_recovery", "terminal": True,
             "steps": [
                 send("payer", "fund", "success", {"payer": "-10", "contract": "10"}, "10"),
                 {"type": "time", "advance_seconds": 11},
                 {"type": "permutation", "steps": [
                     send("outsider", "steal", "revert"),
                     send("worker", "withdraw", "success", {"worker": "10", "contract": "-10"})
                 ]},
             ], "final": {"contract_balance_wei": "0", "balances_delta_wei": {"payer": "-10", "worker": "10"}}},
        ]
    return result


class FakeRPC:
    def __init__(self):
        self.addresses = ["0x" + str(i + 1).zfill(40) for i in range(10)]
        self.balances = {a: 1000 for a in self.addresses}
        self.contract = "0x" + "f" * 40
        self.balances[self.contract] = 0
        self.created = False
        self.now = 0
        self.txno = 0
        self.receipts = {}
        self.snapshots = {}
        self.snapno = 0
        self.funded = False
        self.withdrew = False

    def wait_receipt(self, tx_hash):
        return self.receipts[tx_hash]

    def call(self, method, args=None):
        args = args or []
        if method == "eth_accounts":
            return self.addresses
        if method == "eth_getBalance":
            return hex(self.balances[args[0]])
        if method == "evm_increaseTime":
            self.now += args[0]
            return self.now
        if method == "evm_mine":
            return "0x0"
        if method == "evm_snapshot":
            self.snapno += 1
            self.snapshots[self.snapno] = (self.balances.copy(), self.now, self.funded, self.withdrew)
            return hex(self.snapno)
        if method == "evm_revert":
            key = int(args[0], 16)
            self.balances, self.now, self.funded, self.withdrew = self.snapshots.pop(key)
            return True
        if method == "eth_getTransactionReceipt":
            return self.receipts[args[0]]
        if method == "eth_sendTransaction":
            tx = args[0]
            self.txno += 1
            tx_hash = hex(self.txno)
            success = True
            contract_addr = None
            if "to" not in tx:
                contract_addr = self.contract
                self.created = True
            else:
                assert tx["to"] == self.contract
                sender = tx["from"]
                if tx["data"] == "0x11111111" and sender == self.addresses[0] and not self.funded:
                    amount = int(tx["value"], 16)
                    self.balances[sender] -= amount
                    self.balances[self.contract] += amount
                    self.funded = True
                elif tx["data"] == "0x22222222" and sender == self.addresses[1] and self.now >= 11 and not self.withdrew:
                    self.balances[self.contract] -= 10
                    self.balances[sender] += 10
                    self.withdrew = True
                else:
                    success = False
            self.receipts[tx_hash] = {"status": "0x1" if success else "0x0", "contractAddress": contract_addr}
            return tx_hash
        raise AssertionError("unrecognized fake RPC call " + method)


@pytest.fixture
def golden():
    report = sample_report()
    return report, sample_gold(report)


def test_accepts_complete_independent_vectors(golden):
    report, gold = golden
    pairs = gate.validate_gold(report, gold)
    assert len(pairs) == 6
    assert len(gate.expanded_sequences(gold["cases"][0]["sequences"][1]["steps"])) == 2


def test_missing_or_fake_independence_fails_closed(golden):
    report, gold = golden
    gold["authorship"]["reference_model_used"] = True
    with pytest.raises(gate.CertificationFailure, match="must not use"):
        gate.validate_gold(report, gold)


def test_missing_cases_rejected(golden):
    report, gold = golden
    gold["cases"].pop()
    with pytest.raises(gate.CertificationFailure, match="cover all"):
        gate.validate_gold(report, gold)


def test_wrong_kernel_rejected(golden):
    report, gold = golden
    report["kernel_sha256"]["frozen_kernel.py"] = "modified"
    with pytest.raises(gate.CertificationFailure, match="different kernel"):
        gate.validate_gold(report, gold)


def test_missing_terminal_rejected(golden):
    report, gold = golden
    for seq in gold["cases"][0]["sequences"]:
        seq["terminal"] = False
    with pytest.raises(gate.CertificationFailure, match="missing terminal"):
        gate.validate_gold(report, gold)


def test_wrong_wei_rejected(golden):
    report, gold = golden
    gold["cases"][0]["sequences"][0]["steps"][0]["changes_wei"]["contract"] = "11"
    assert len(gate.validate_gold(report, gold)) == 6  # malformed *semantic* gold is caught at EVM execution


def test_large_order_group_requires_frozen_random_seed():
    group = {"type": "permutation", "steps": [{"type": "send", "index": i} for i in range(5)]}
    with pytest.raises(gate.CertificationFailure, match="deterministic nonnegative seed"):
        gate.expanded_sequences([group])
    group["seed"] = 42
    result = gate.expanded_sequences([group])
    assert len(result) == 24 and len({tuple(st["index"] for st in r) for r in result}) == 24
    assert gate.expanded_sequences([group]) == result


def test_absent_all_order_tests_rejected(golden):
    report, gold = golden
    for case in gold["cases"]:
        case["sequences"][1]["steps"][2] = send("outsider", "steal", "revert")
    with pytest.raises(gate.CertificationFailure, match="permutation"):
        gate.validate_gold(report, gold)


def test_economic_oracle_detects_missing_funds():
    with pytest.raises(gate.CertificationFailure, match="tracked funds not conserved"):
        gate.verify_conservation({"payer": 100, "contract": 0}, {"payer": 90, "contract": 9}, "safety")


def test_economic_oracle_detects_wrong_beneficiary():
    with pytest.raises(gate.CertificationFailure, match="expected delta"):
        gate.verify_deltas({"payer": 100, "worker": 0}, {"payer": 90, "worker": 10}, {"worker": "9"}, "gold")


def test_rpc_guard_rejects_external_rpc():
    with pytest.raises(gate.CertificationFailure, match="local Anvil"):
        gate.RPC("https://mainnet.example/rpc")


def test_zero_source_kernel_imports():
    p = Path(gate.__file__).read_text(encoding="utf-8")
    assert "from tools.nanojev_contract_v3_reference import" not in p
    assert "from tools.nanojev_contract_v3_governance_model import" not in p


def test_evm_execution_independent_of_compiler(monkeypatch, golden):
    report, gold = golden
    artifact = {"abi": [
        {"name": "fund", "type": "function", "inputs": []},
        {"name": "withdraw", "type": "function", "inputs": []},
        {"name": "steal", "type": "function", "inputs": []},
    ], "bytecode": {"object": "0x1234"}}
    table = {"fund()": "0x11111111", "withdraw()": "0x22222222", "steal()": "0x33333333"}
    monkeypatch.setattr(gate, "cast_encode", lambda sig, args, **kwargs: table[sig])
    case = gold["cases"][0]
    for sequence in case["sequences"]:
        count = gate.execute_sequence(FakeRPC(), case, sequence, artifact, "cast")
        assert count == (2 if sequence["id"] == "ordering_recovery" else 1)


def test_false_gold_is_rejected_by_evm(monkeypatch, golden):
    _, gold = golden
    artifact = {"abi": [
        {"name": "fund", "type": "function", "inputs": []},
        {"name": "withdraw", "type": "function", "inputs": []},
        {"name": "steal", "type": "function", "inputs": []},
    ], "bytecode": {"object": "0x1234"}}
    table = {"fund()": "0x11111111", "withdraw()": "0x22222222", "steal()": "0x33333333"}
    monkeypatch.setattr(gate, "cast_encode", lambda sig, args, **kwargs: table[sig])
    bad = copy.deepcopy(gold["cases"][0]["sequences"][0])
    bad["steps"][0]["changes_wei"]["contract"] = "11"
    with pytest.raises(gate.CertificationFailure, match="expected delta"):
        gate.execute_sequence(FakeRPC(), gold["cases"][0], bad, artifact, "cast")


def test_frozen_kernel_disk_changed_is_denied(tmp_path, golden):
    report, _ = golden
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "frozen_kernel.py").write_text("not the frozen kernel")
    with pytest.raises(gate.CertificationFailure, match="kernel changed"):
        gate.verify_kernel_on_disk(report, tmp_path)


def test_missing_source_kernel_is_denied(tmp_path, golden):
    report, _ = golden
    with pytest.raises(gate.CertificationFailure, match="kernel missing"):
        gate.verify_kernel_on_disk(report, tmp_path)


def test_scaffold_is_incomplete(golden):
    report, _ = golden
    template = gate.gold_template(report)
    with pytest.raises(gate.CertificationFailure, match="reviewer not identified"):
        gate.validate_gold(report, template)


def test_six_case_certificate_control_flow(monkeypatch, tmp_path, golden):
    report, gold = golden
    source = tmp_path / "solidity.sol"
    source.write_text("// isolated disposable fake code")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "frozen_kernel.py").write_text("const")
    report["kernel_sha256"]["frozen_kernel.py"] = gate.digest(b"const")
    gold["kernel_sha256"]["frozen_kernel.py"] = gate.digest(b"const")
    artifact = {"abi": [
        {"name": method, "type": "function", "inputs": []}
        for method in ["fund", "withdraw", "steal"]
    ], "bytecode": {"object": "0x1234"}}
    table = {"fund()": "0x11111111", "withdraw()": "0x22222222", "steal()": "0x33333333"}
    monkeypatch.setattr(gate, "cast_encode", lambda sig, args, **kwargs: table[sig])
    monkeypatch.setattr(gate, "source_and_artifact", lambda *args, **kwargs: (artifact, source))
    monkeypatch.setattr(gate.shutil, "which", lambda _: "/fake/local/foundry")

    class FakeAnvilContext:
        def __init__(self, name):
            assert name == "anvil"

        def __enter__(self):
            return FakeRPC()

        def __exit__(self, *_):
            pass

    result = gate.run_certificate(report, gold, tmp_path, repo_root=tmp_path, anvil_factory=FakeAnvilContext)
    assert result["cases_passed"] == 6
    assert result["expert_gold_independence_verified"] is False
    assert result["authorship_attested_not_verified"] is True
    assert sum(item["execution_variants"] for item in result["cases"]) == 18


def test_unauthorized_success_is_not_misreported(monkeypatch, golden):
    report, gold = golden
    artifact = {"abi": [
        {"name": method, "type": "function", "inputs": []}
        for method in ["fund", "withdraw", "steal"]
    ], "bytecode": {"object": "0x1234"}}
    table = {"fund()": "0x11111111", "withdraw()": "0x22222222", "steal()": "0x33333333"}
    monkeypatch.setattr(gate, "cast_encode", lambda sig, args, **kwargs: table[sig])
    bad = copy.deepcopy(gold["cases"][0]["sequences"][0])
    bad["steps"][3]["expect"] = "success"
    with pytest.raises(gate.CertificationFailure, match="expected success"):
        gate.execute_sequence(FakeRPC(), gold["cases"][0], bad, artifact, "cast")
