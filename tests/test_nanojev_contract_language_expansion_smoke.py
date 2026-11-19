"""Offline validation of expanded multi-family LLM -> typed DSL -> Solidity smoke.

All Ollama/Forge outputs are explicitly injected; tests do NOT certify real Docker
compilation or any human-independent gold standard.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from tools import nanojev_contract_solidity_smoke as v1
from tools import nanojev_contract_language_expansion_smoke as exp


@pytest.fixture
def fixture_cases():
    return exp.load_fixture()["cases"]


def mock_response(case):
    if case["family"] == "needs_clarification":
        return json.dumps({"status": "needs_clarification", "questions": [
            "What is the exact amount of ETH?",
            "How long is the time lock, in days?",
            "Who is the constructor-provided beneficiary or recipient?",
            "Can the depositor take back the gift, or is refund prohibited?",
            "What are the split percentage shares and how should a contradiction be resolved?",
        ]})
    return json.dumps({"status": "contract", "contract": exp.expected(case)})


def fake_head_factory(cases, alter=None):
    v1_cases = v1.fixture()["cases"]
    responses = {c["request"]: json.dumps({"status": "contract", "contract": v1.expected_spec(c["expected_amount_wei"])})
                 for c in v1_cases if c["family"] == "positive"}
    responses[v1_cases[-1]["request"]] = json.dumps({"status": "needs_clarification", "questions": [
        "What amount of ETH?", "What deadline?", "Who can authorize release?", "When is a refund allowed?",
    ]})
    responses.update({c["request"]: mock_response(c) for c in cases})
    if alter:
        responses[alter[0]] = alter[1]
    return lambda _mode, request: responses[request]


def fake_docker(args, _timeout):
    assert args[:3] == ["docker", "run", "--rm"]
    assert "--cap-drop" in args and "ALL" in args
    assert "--privileged" not in args
    assert "--entrypoint" in args and args[args.index("--entrypoint") + 1] == "forge"
    assert args[-1] == "build" or args[-2:] == ["test", "-vv"]
    return {"exit_code": 0, "stdout_tail": "56 tests passed, 0 failed (INJECTED; no actual Docker)", "stderr_tail": ""}


def test_holdout_pinned_with_two_new_independent_spec_families(fixture_cases):
    assert len(fixture_cases) == 10
    assert len({x["request"] for x in fixture_cases}) == 10
    assert len({x["id"] for x in fixture_cases}) == 10
    assert set(x["type"] for x in fixture_cases) == {"timelocked_gift", "split_payout"}
    assert {x["family"] for x in fixture_cases} == {"positive", "needs_clarification"}
    assert sum(x["family"] == "positive" for x in fixture_cases) == 6
    assert sum(x["family"] == "needs_clarification" for x in fixture_cases) == 4
    assert "gift_baseline" not in exp.PROMPT
    assert "split_baseline" not in exp.PROMPT
    assert "expected" not in exp.PROMPT


def test_injected_head_validates_every_new_case(fixture_cases):
    for case in fixture_cases:
        predicted = v1.parse_head_output(mock_response(case))
        actual = exp.validate(case, predicted)
        if case["family"] == "positive":
            assert actual == exp.expected(case)
        else:
            assert actual is None


def test_semantic_contrasts_distinct_and_same(fixture_cases):
    cs = {c["id"]: c for c in fixture_cases if c["family"] == "positive"}
    specs = {name: exp.expected(case) for name, case in cs.items()}
    exp.check_relations(specs)
    assert specs["gift_baseline"] == specs["gift_paraphrase"]
    assert specs["split_baseline"] == specs["split_paraphrase"]
    assert specs["gift_baseline"]["lock_seconds"] == 604800
    assert specs["gift_duration_change"]["lock_seconds"] == 1209600
    assert specs["split_baseline"]["primary_bps"] == 7500
    assert specs["split_ratio_change"]["primary_bps"] == 6000


@pytest.mark.parametrize("key, value", [
    ("claim_authorizer", "depositor"),
    ("claim_window", "strictly_before_maturity"),
    ("refund", "depositor_anytime"),
    ("beneficiary", "depositor"),
    ("settlement", "multiple_claims"),
    ("asset", "erc20"),
    ("amount_wei", True),
    ("lock_seconds", 0),
    ("lock_seconds", 366*86400),
])
def test_unsafe_gift_semantics_rejected(fixture_cases, key, value):
    case = fixture_cases[0]
    spec = exp.expected(case)
    spec[key] = value
    with pytest.raises(v1.SmokeFailure):
        exp.validate(case, {"status": "contract", "contract": spec})


@pytest.mark.parametrize("key, value", [
    ("distribution_authorizer", "primary"),
    ("funding_authorizer", "secondary"),
    ("primary", "payer"),
    ("refund", "anytime"),
    ("settlement", "two_distributions"),
    ("primary_bps", 0),
    ("primary_bps", 10000),
    ("primary_bps", 7501),
    ("secondary_bps", True),
    ("amount_wei", -1),
])
def test_unsafe_split_semantics_rejected(fixture_cases, key, value):
    case = fixture_cases[5]
    spec = exp.expected(case)
    spec[key] = value
    with pytest.raises(v1.SmokeFailure):
        exp.validate(case, {"status": "contract", "contract": spec})


def test_no_extra_fields_or_untrusted_solidity_in_compiled_code(fixture_cases):
    case = fixture_cases[0]
    spec = exp.expected(case)
    spec["arbitrary_solidity"] = "selfdestruct(msg.sender)"
    with pytest.raises(v1.SmokeFailure):
        exp.validate(case, {"status": "contract", "contract": spec})
    spec.pop("arbitrary_solidity")
    with pytest.raises(v1.SmokeFailure):
        exp.gift_source(spec, "Bad;injection")
    src = exp.gift_source(spec, "NanoJevGiftBaseline")
    assert "selfdestruct" not in src
    assert "require(msg.sender == beneficiary" in src
    assert "block.timestamp >= fundedAt + LOCK_SECONDS" in src
    assert "state = State.Claimed;" in src
    assert "payable" in src


def test_split_state_transition_and_partial_payout_atomicity(fixture_cases):
    spec = exp.expected(fixture_cases[5])
    src = exp.split_source(spec, "NanoJevSplitBaseline")
    assert "state = State.Distributed;" in src
    assert "primary.call{value: primaryAmount}" in src
    assert "secondary.call{value: secondaryAmount}" in src
    assert "AMOUNT_WEI - primaryAmount" in src
    assert "require(primary_ != secondary_" in src
    test = exp.split_test(spec, "NanoJevSplitBaseline")
    assert "testRejectingRecipientRollsBackEntireDistribution" in test
    assert not re.search(r"\bfunction\s+testFail\w*\s*\(", test)
    assert 'require(!ok, "partial transfer succeeded")' in test
    assert 'require(uint256(c.state()) == 1, "state not rolled back")' in test
    assert 'require(address(c).balance == AMOUNT, "funds not rolled back")' in test
    assert "testConstructorRejectsIdenticalRecipients" in test
    assert "testNoRefundFunction" in test
    assert "testNoDoubleDistribution" in test


def test_wrong_positive_values_are_not_accepted(fixture_cases):
    case = fixture_cases[2]
    wrong = exp.gift_spec(case["expected"]["amount_wei"], 7*86400)
    with pytest.raises(v1.SmokeFailure, match="Intent-to-spec mismatch"):
        exp.validate(case, {"status": "contract", "contract": wrong})
    case = fixture_cases[7]
    wrong = exp.split_spec(4*10**18, 7500)
    with pytest.raises(v1.SmokeFailure, match="Intent-to-spec mismatch"):
        exp.validate(case, {"status": "contract", "contract": wrong})


def test_llm_false_accept_of_contradictions_rejected(fixture_cases):
    for case in [fixture_cases[4], fixture_cases[9]]:
        spec = exp.gift_spec(3*10**18, 7*86400) if case["type"] == "timelocked_gift" else exp.split_spec(4*10**18,7500)
        with pytest.raises(v1.SmokeFailure, match="ambiguous/contradictory"):
            exp.validate(case, {"status": "contract", "contract": spec})


def test_ambiguous_clarification_requires_material_questions(fixture_cases):
    for case in fixture_cases:
        if case["family"] == "needs_clarification":
            with pytest.raises(v1.SmokeFailure, match="Clarification omitted"):
                exp.validate(case, {"status": "needs_clarification", "questions": ["Can you explain?"]})


def test_full_synthetic_pipeline_does_not_claim_cuda_or_forge_pass(tmp_path, fixture_cases):
    output = tmp_path / "expanded.json"
    report = exp.run(exp.Options(output=output), head=fake_head_factory(fixture_cases),
                     docker=fake_docker, docker_available=True)
    assert report["status"] == "PASS_INJECTED_OFFLINE_TEST_ONLY", report["blockers"]
    assert report["stages"]["contract_language_validation"] == "PASS"
    assert report["stages"]["forge_build"]["exit_code"] == 0
    assert report["stages"]["forge_tests_passed"] >= 50
    assert len(report["cases"]) == 14
    assert sum(c["result"] == "CORRECT_CLARIFICATION" for c in report["cases"]) == 5
    assert len(report["generated"]) == 6
    assert output.exists()
    workspace = output.with_name(output.stem + ".workspace")
    assert len(list((workspace / "src").glob("*.sol"))) == 6
    assert len(list((workspace / "test").glob("*.t.sol"))) == 6
    count = sum(len(re.findall(r"\bfunction\s+test\w*\s*\(", p.read_text(encoding="utf-8")))
                for p in (workspace / "test").glob("*.t.sol"))
    assert count == report["expected_foundry_tests"]
    assert count == 56
    assert report["stages"]["compiled_artifacts"] == "INJECTED_TEST_RUNNER"


def test_legacy_foundry_testfail_name_blocks_before_docker(tmp_path, fixture_cases, monkeypatch):
    original = exp.split_test

    def legacy_split_test(spec, name):
        return original(spec, name).replace(
            "testRejectingRecipientRollsBackEntireDistribution",
            "testFailedRecipientRevertsEntireDistribution")

    monkeypatch.setattr(exp, "split_test", legacy_split_test)
    docker_calls = []
    report = exp.run(
        exp.Options(output=tmp_path / "legacy-name.json"),
        head=fake_head_factory(fixture_cases),
        docker=lambda *a: docker_calls.append(a),
        docker_available=True,
    )
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert report["blockers"][0]["stage"] == "solidity_emit"
    assert "testFail" in report["blockers"][0]["reason"]
    assert docker_calls == []


def test_generation_failure_prevents_forge(tmp_path, fixture_cases):
    wrong = json.dumps({"status": "contract", "contract": exp.gift_spec(3*10**18, 7*86400)})
    called=[]
    report=exp.run(exp.Options(output=tmp_path/"fail.json"),
                   head=fake_head_factory(fixture_cases, alter=(fixture_cases[2]["request"], wrong)),
                   docker=lambda *a: called.append(a), docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert report["blockers"][0]["stage"] == "semantic_oracle"
    assert called == []
    assert not (tmp_path / "fail.workspace" / "src").exists()


def test_forge_failing_and_zero_tests_block(tmp_path, fixture_cases):
    for i, run_docker in enumerate((
        lambda *_: {"exit_code": 42, "stdout_tail":"solc failure", "stderr_tail":""},
        lambda *_: {"exit_code": 0, "stdout_tail":"0 tests passed, 0 failed", "stderr_tail":""},
    )):
        report=exp.run(exp.Options(output=tmp_path/f"fail-{i}.json"),
                       head=fake_head_factory(fixture_cases), docker=run_docker, docker_available=True)
        assert report["status"] == "BLOCKED_NOT_VERIFIED"
        assert report["blockers"][0]["stage"] == ("forge_build" if i == 0 else "forge_test")


def test_no_docker_blocks_without_llm(tmp_path):
    called=[]
    report=exp.run(exp.Options(output=tmp_path/"no-docker.json"),
                   head=lambda *_: called.append(True), docker_available=False)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert "Docker CLI" in report["blockers"][0]["reason"]
    assert called == []


def test_output_no_overwrite_and_dry_run(tmp_path, fixture_cases):
    out=tmp_path/"existing.json"
    out.write_text("untouched")
    report=exp.run(exp.Options(output=out), head=fake_head_factory(fixture_cases), docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert out.read_text() == "untouched"
    plan=tmp_path/"plan.json"
    report=exp.run(exp.Options(output=plan, dry_run=True), docker_available=False)
    assert report["status"] == "PLAN_ONLY_NOT_EXECUTED"
    assert len(report["planned_cases"]) == 14
    assert not plan.exists()


def test_v1_escrow_fingerprint_is_locked(monkeypatch):
    monkeypatch.setattr(exp, "ESCROW_RUNNER_SHA256", "0"*64)
    with pytest.raises(v1.SmokeFailure, match="Frozen v1 escrow"):
        exp.load_fixture()


def test_v2_holdout_fingerprint_is_locked(monkeypatch):
    monkeypatch.setattr(exp, "FIXTURE_SHA256", "0"*64)
    with pytest.raises(v1.SmokeFailure, match="holdout fixture fingerprint"):
        exp.load_fixture()


def test_status_summary_accepts_numeric_counts_and_dicts():
    result=v1.summarize_stages({"n":56, "forge_build":{"exit_code":0}, "pass":"PASS"})
    assert result == {"n":56, "forge_build":0, "pass":"PASS"}


def extra_gold_cases():
    return [
        {"id":"external_gift_001", "type":"timelocked_gift", "family":"positive",
         "request":"Create an ETH gift. The deployer alone deposits precisely 5 ether once. A distinct beneficiary address is supplied at construction; only that beneficiary may claim all funds at or after 3 days elapsed since funding. The creator may never refund or cancel. Only one final withdrawal may occur.",
         "expected":{"amount_wei":5*10**18,"lock_seconds":3*86400}},
        {"id":"external_gift_ambiguity", "type":"timelocked_gift", "family":"needs_clarification",
         "request":"I wish to lock some ether for someone. The beneficiary may receive the amount at some future time, but I have not decided the precise value, recipient, or delay.",
         "missing":["amount", "lock_duration", "beneficiary"]},
    ]


def write_external_gold(path, cases):
    path.write_text(json.dumps({"schema":"nanojev.contract-language-expansion.external.v1",
                                "provenance":{"authorship":"unverified example, not an independent expert"},
                                "cases": cases}, indent=2), encoding="utf-8")


def test_optional_external_gold_runs_through_llm_and_forge(tmp_path, fixture_cases):
    extra = extra_gold_cases()
    path=tmp_path / "external.json"
    write_external_gold(path, extra)
    loaded, digest = exp.load_independent_cases(path)
    assert loaded == extra
    assert digest == v1.digest(path.read_bytes())
    head=fake_head_factory(fixture_cases + extra)

    def fake_docker_more(args, timeout):
        base=fake_docker(args, timeout)
        base["stdout_tail"]="65 tests passed, 0 failed (INJECTED)"
        return base

    result=exp.run(exp.Options(output=tmp_path/"with-extra.json", independent_cases=path),
                   head=head, docker=fake_docker_more, docker_available=True)
    assert result["status"] == "PASS_INJECTED_OFFLINE_TEST_ONLY", result["blockers"]
    assert result["independent_gold"] == "SUPPLIED_AUTHORSHIP_AND_ACCURACY_UNVERIFIED"
    assert result["independent_gold_sha256"] == digest
    assert result["expected_foundry_tests"] == 65
    assert len(result["cases"]) == 16
    assert len(result["generated"]) == 7
    assert "NanoJevGiftExternal000" in result["generated"]
    assert result["generated"]["NanoJevGiftExternal000"]["origin"] == "supplied_external_gold_unverified"
    assert (tmp_path / "with-extra.workspace" / "test" / "NanoJevGiftExternal000.t.sol").exists()


def test_external_gold_rejects_malformed_and_collision(tmp_path):
    cases=extra_gold_cases()
    path=tmp_path/"input.json"
    cases[0]["id"]="gift_baseline"
    write_external_gold(path, cases)
    with pytest.raises(v1.SmokeFailure, match="nonreserved"):
        exp.load_independent_cases(path)
    cases[0]["id"]="new_gift"
    cases[0]["expected"]["lock_seconds"]="three days"
    write_external_gold(path, cases)
    with pytest.raises(v1.SmokeFailure, match="expected fields invalid"):
        exp.load_independent_cases(path)
    with pytest.raises(v1.SmokeFailure, match="cannot be read"):
        exp.load_independent_cases(tmp_path/"missing.json")


def test_external_gold_cannot_hide_wrong_acceptance(tmp_path, fixture_cases):
    cases=extra_gold_cases()
    path=tmp_path/"input.json"
    write_external_gold(path, cases)
    wrong=json.dumps({"status":"contract","contract":exp.gift_spec(5*10**18, 7*86400)})
    calls=[]
    result=exp.run(exp.Options(output=tmp_path/"wrong.json", independent_cases=path),
                   head=fake_head_factory(fixture_cases+cases, alter=(cases[0]["request"],wrong)),
                   docker=lambda *args:calls.append(args), docker_available=True)
    assert result["status"] == "BLOCKED_NOT_VERIFIED"
    assert result["blockers"][0]["stage"] == "semantic_oracle"
    assert calls == []


def test_emitters_block_unvalidated_numeric_values():
    bad_gift=exp.gift_spec(3*10**18, 0)
    with pytest.raises(v1.SmokeFailure, match="Unvalidated gift"):
        exp.gift_source(bad_gift,"NanoJevGiftBad")
    bad_split=exp.split_spec(4*10**18, 12000)
    with pytest.raises(v1.SmokeFailure, match="Unvalidated split"):
        exp.split_source(bad_split,"NanoJevSplitBad")
