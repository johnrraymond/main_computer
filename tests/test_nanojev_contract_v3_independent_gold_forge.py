"""The independent V3 gold corpus, source hash guard and Docker Forge suite compiler tests.

No V3 code-generator or reference implementation is imported. Test execution is
static/offline; only the explicit Docker command gives actual EVM evidence.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import pytest
from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_independent_forge_gold as forge_gold

ROOT=Path(__file__).resolve().parents[1]
GOLD=ROOT/"runtime/reports/nanojev-contract-v3-independent-gold.v1.json"
REPORT=ROOT/"runtime/reports/nanojev-contract-v3-governance-heldout-forge-0001.json"


def test_gold_is_complete_and_real_report_matched():
    gold=gate.read_json(GOLD)
    assert len(gold["cases"])==6
    assert sum(len(c["sequences"]) for c in gold["cases"])==47
    assert any(s.get("type")=="permutation" for c in gold["cases"]
               for seq in c["sequences"] for s in seq["steps"])
    if REPORT.exists():
        assert len(gate.validate_gold(gate.read_json(REPORT),gold))==6


def test_constructed_forge_suites_cover_all_vectors():
    gold=gate.read_json(GOLD)
    total=0
    for c in gold["cases"]:
        fund=next(s for s in c["sequences"][0]["steps"] if s.get("signature")=="fund()" and s.get("expect")=="success")
        amount=int(fund["value_wei"])
        solidity,count=forge_gold.generated_suite(c,amount)
        assert count>=len(c["sequences"])
        assert solidity.count("function testIndependentGoldTrace")==count
        assert solidity.count("contract NanoJevIndependentGoldTrace")==count
        assert 'vm.prank(OUTSIDER);' in solidity
        assert "source_oracle" not in solidity
        assert "vm.warp(" in solidity
        assert "conservation failure" in solidity
        total+=count
    assert total==52


def test_preflight_accepts_provided_workspace_and_only_newline_conversion():
    if not REPORT.exists():pytest.skip("uploaded Forge workspaces/report not installed here")
    original=gate.read_json(REPORT)
    gold=gate.read_json(GOLD)
    name=Path("runtime/reports")
    first=Path(str(original["cases"][0]["workspace"]).replace("\\", "/")).name
    candidate=ROOT/name/first/"src/NanoJevGoverned01.sol"
    if not candidate.exists():pytest.skip("generated Forge workspace not installed here")
    result=gate.preflight_gold_runtime(original,gold,ROOT/name,repo_root=ROOT)
    assert result["status"]=="PASS_GOLD_AND_PINNED_ARTIFACT_PREFLIGHT_NOT_EVM_CERTIFIED"
    assert result["cases_preflighted"]==6
    assert result["traces"]==47
    assert result["execution_variants"]==52
    assert result["local_anvil_execution"] is False
    assert result["forged_execution"] is False


def test_reversible_crlf_only_hash_policy(tmp_path):
    src=tmp_path/"test.sol"
    src.write_bytes(b"a\nb\n")
    expected=gate.digest(src.read_bytes())
    assert gate.canonical_solidity_sha256(src,expected)==expected
    src.write_bytes(b"a\r\nb\r\n")
    assert gate.canonical_solidity_sha256(src,expected)==expected
    src.write_bytes(b"a  \r\nb\r\n")
    with pytest.raises(gate.CertificationFailure,match="hash mismatch"):
        gate.canonical_solidity_sha256(src,expected)


def test_docker_gold_requires_actual_docker(monkeypatch,tmp_path):
    monkeypatch.setattr(forge_gold.shutil,"which",lambda _:None)
    with pytest.raises(gate.CertificationFailure,match="Docker executable required"):
        forge_gold.onchain_docker({}, {}, report_root=tmp_path,repo_root=tmp_path,output=tmp_path/"out.json")


def test_gold_cannot_be_substituted_without_hash_mismatch():
    if not REPORT.exists():pytest.skip("real report not installed here")
    r=gate.read_json(REPORT)
    gold=gate.read_json(GOLD)
    bad=copy.deepcopy(gold)
    bad["cases"][0]["spec_sha256"]="0"*64
    with pytest.raises(gate.CertificationFailure,match="specification hash mismatch"):
        gate.validate_gold(r,bad)
