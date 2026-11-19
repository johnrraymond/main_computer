"""Offline and fault-injection tests for the real-LLM/Forge smoke gate.

These checks intentionally DO NOT claim that Ollama or the Forge Docker daemon
has been exercised. Only a live CLI invocation can earn PASS_SMOKE_NOT_CERTIFIED.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import nanojev_contract_solidity_smoke as smoke


@pytest.fixture
def cases():
    return smoke.fixture()["cases"]


def reply(case):
    if case["family"] == "needs_clarification":
        return json.dumps({"status": "needs_clarification", "questions": [
            "What exact amount of ETH should be held?",
            "What is the delivery confirmation deadline?",
            "Who can authorize release to the seller?",
            "Under what condition can the buyer request a refund?",
        ]})
    return json.dumps({"status": "contract", "contract": smoke.expected_spec(case["expected_amount_wei"])})


def fake_head_for(cases, *, alter=None):
    values = {c["request"]: reply(c) for c in cases}
    if alter:
        values[alter[0]] = alter[1]
    return lambda prompt: values[prompt]


def fake_docker(command, timeout):
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--entrypoint" in command and command[command.index("--entrypoint") + 1] == "forge"
    assert command[-1] == "build" or command[-2:] == ["test", "-vv"]
    assert any(x.endswith(":/work") for x in command)
    assert "--cap-drop" in command and "ALL" in command
    assert "--network" in command
    return {"exit_code": 0, "stdout_tail": "18 tests passed, 0 failed (fake runner; no Forge called)", "stderr_tail": ""}


def test_fixture_cases_are_independent_and_have_different_values(cases):
    assert len(cases) == 4
    assert len({c["request"] for c in cases}) == 4
    assert [c["family"] for c in cases] == ["positive", "positive", "positive", "needs_clarification"]
    assert cases[0]["expected_amount_wei"] == cases[1]["expected_amount_wei"]
    assert cases[2]["expected_amount_wei"] == 2 * cases[0]["expected_amount_wei"]
    assert "expected_amount_wei" not in smoke.SYSTEM_PROMPT
    assert "escrow_baseline" not in smoke.SYSTEM_PROMPT


def test_parse_rejects_non_json_and_json_array():
    for value in ("```json\n{}\n```", "hello", "[]", "null", "{}"):
        with pytest.raises(smoke.SmokeFailure):
            smoke.parse_head_output(value)


def test_contract_exact_match(cases):
    for case in cases:
        data = smoke.parse_head_output(reply(case))
        spec = smoke.validate_response(case, data)
        if case["family"] == "positive":
            assert spec == smoke.expected_spec(case["expected_amount_wei"])
        else:
            assert spec is None


def test_unexpected_amount_detected_by_oracle(cases):
    c = cases[2]
    data = {"status": "contract", "contract": smoke.expected_spec(10**18)}
    with pytest.raises(smoke.SmokeFailure, match="Intent-to-contract mismatch"):
        smoke.validate_response(c, data)


@pytest.mark.parametrize("field,wrong", [
    ("release_authorizer", "seller"),
    ("release_trigger", "seller_confirms_delivery"),
    ("release_window", "at_or_before_deadline"),
    ("refund_trigger", "buyer_requests_anytime"),
    ("refund_authorizer", "seller"),
    ("settlement", "both_possible"),
    ("timeout_seconds", 86400),
    ("asset", "token"),
    ("funding_authorizer", "seller"),
])
def test_unsafe_schema_values_rejected(cases, field, wrong):
    spec = smoke.expected_spec(10**18)
    spec[field] = wrong
    with pytest.raises(smoke.SmokeFailure, match="Unsupported or inconsistent field"):
        smoke.validate_response(cases[0], {"status": "contract", "contract": spec})


def test_extra_field_rejected(cases):
    spec = smoke.expected_spec(10**18)
    spec["arbitrary_solidity"] = "selfdestruct(...);"
    with pytest.raises(smoke.SmokeFailure, match="Contract field set unsupported"):
        smoke.validate_response(cases[0], {"status": "contract", "contract": spec})


def test_bool_not_accepted_as_integer(cases):
    spec = smoke.expected_spec(10**18)
    spec["amount_wei"] = True
    with pytest.raises(smoke.SmokeFailure, match="Numeric contract fields invalid"):
        smoke.validate_response(cases[0], {"status": "contract", "contract": spec})


def test_ambiguous_prompt_cannot_emit_code(cases):
    with pytest.raises(smoke.SmokeFailure, match="not explicitly refused"):
        smoke.validate_response(cases[-1], {"status": "contract", "contract": smoke.expected_spec(10**18)})


def test_clarification_must_have_valid_questions(cases):
    for questions in ([], [5], [""], ["a" * 401]):
        with pytest.raises(smoke.SmokeFailure, match="questions"):
            smoke.validate_response(cases[-1], {"status": "needs_clarification", "questions": questions})


def test_vague_clarification_fails(cases):
    with pytest.raises(smoke.SmokeFailure, match="every material ambiguity"):
        smoke.validate_response(cases[-1], {"status": "needs_clarification", "questions": ["Can you clarify?"]})


def test_endpoint_only_allows_loopback():
    for target in ("http://evil.example", "https://127.0.0.1:11434", "http://127.0.0.1.evil.test", "http://user:pass@localhost", "http://localhost/x", "file:///etc/passwd"):
        with pytest.raises(smoke.SmokeFailure):
            smoke.local_url(target)
    assert smoke.local_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"


def test_generated_source_is_code_without_untrusted_user_input(tmp_path):
    spec = smoke.expected_spec(10**18)
    source = smoke.source_for(spec, "NanoJevEscrowBaseline")
    assert "uint256 public constant AMOUNT_WEI = 1000000000000000000;" in source
    assert "State.Released" in source and "State.Refunded" in source
    assert "block.timestamp < fundedAt + TIMEOUT_SECONDS" in source
    assert "block.timestamp >= fundedAt + TIMEOUT_SECONDS" in source
    assert "state = State.Released;" in source
    assert "state = State.Refunded;" in source
    assert "seller.call{value: AMOUNT_WEI}" in source
    assert "payable(buyer).call{value: AMOUNT_WEI}" in source
    with pytest.raises(smoke.SmokeFailure):
        smoke.source_for(spec, "Bad; injected")
    generated = smoke.write_foundry_workspace(tmp_path, spec, smoke.expected_spec(2 * 10**18))
    assert len(generated) == 2
    assert len(list((tmp_path / "src").glob("*.sol"))) == 2
    assert len(list((tmp_path / "test").glob("*.t.sol"))) == 2
    assert "function testFuzzWrongDeposit" in (tmp_path / "test" / "NanoJevEscrowBaseline.t.sol").read_text()


def test_docker_command_matches_repository_convention_without_privileged_bind(tmp_path):
    cmd = smoke.docker_args(tmp_path, smoke.DEFAULT_IMAGE, "bridge", "build")
    assert cmd[:4] == ["docker", "run", "--rm", "--network"]
    assert smoke.DEFAULT_IMAGE in cmd
    assert "--privileged" not in cmd
    assert "--network" in cmd
    assert "--cap-drop" in cmd
    assert cmd[-1] == "build"
    assert "--entrypoint" in cmd and cmd[cmd.index("--entrypoint") + 1] == "forge"
    assert not any("docker.sock" in x for x in cmd)
    with pytest.raises(smoke.SmokeFailure):
        smoke.docker_args(tmp_path, smoke.DEFAULT_IMAGE, "host", "build")


def test_injected_full_workflow_is_not_live_certification(tmp_path, cases):
    path = tmp_path / "smoke.json"
    report = smoke.execute(smoke.Options(output=path), head=fake_head_for(cases),
                           docker=fake_docker, docker_available=True)
    assert report["status"] == "PASS_INJECTED_OFFLINE_TEST_ONLY"
    assert report["stages"]["semantic_equivalence_and_contrast"] == "PASS"
    assert report["stages"]["forge_build"]["exit_code"] == 0
    assert report["stages"]["forge_test"]["exit_code"] == 0
    assert report["stages"]["forge_tests_passed"] == 18
    assert len(report["cases"]) == 4
    assert report["cases"][-1]["result"] == "CORRECT_ABSTENTION"
    assert path.exists()
    assert (tmp_path / "smoke.workspace" / "src" / "NanoJevEscrowBaseline.sol").exists()


def test_semantic_failure_must_stop_before_docker(tmp_path, cases):
    wrong = json.dumps({"status": "contract", "contract": smoke.expected_spec(10**18)})
    called = []
    path = tmp_path / "wrong.json"
    report = smoke.execute(smoke.Options(output=path),
                           head=fake_head_for(cases, alter=(cases[2]["request"], wrong)),
                           docker=lambda *x: called.append(x), docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert report["blockers"][0]["stage"] == "semantic_oracle"
    assert called == []
    assert not (tmp_path / "wrong.workspace" / "src").exists()
    assert path.exists()


def test_llm_false_accept_on_ambiguous_text_fails_closed(tmp_path, cases):
    wrong = json.dumps({"status": "contract", "contract": smoke.expected_spec(10**18)})
    report = smoke.execute(smoke.Options(output=tmp_path / "ambig.json"),
                           head=fake_head_for(cases, alter=(cases[-1]["request"], wrong)),
                           docker=fake_docker, docker_available=True)
    assert report["blockers"][0]["stage"] == "semantic_oracle"
    assert "forge_build" not in report["stages"]


def test_docker_failure_stops_at_compile_and_reports(tmp_path, cases):
    report = smoke.execute(smoke.Options(output=tmp_path / "buildfail.json"),
                           head=fake_head_for(cases),
                           docker=lambda cmd, timeout: {"exit_code": 55,
                                                        "stdout_tail": "solc error", "stderr_tail": ""},
                           docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert report["blockers"][0]["stage"] == "forge_build"
    assert "forge_test" not in report["stages"]


def test_forge_zero_tests_does_not_pass(tmp_path, cases):
    def runner(command, timeout):
        return {"exit_code": 0, "stdout_tail": "0 tests passed, 0 failed", "stderr_tail": ""}
    report = smoke.execute(smoke.Options(output=tmp_path / "zerotests.json"),
                           head=fake_head_for(cases), docker=runner, docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert report["blockers"][0]["stage"] == "forge_test"


def test_docker_unavailable_is_blocked_without_llm(tmp_path):
    called = []
    report = smoke.execute(smoke.Options(output=tmp_path / "no-docker.json"),
                           head=lambda prompt: called.append(prompt), docker_available=False)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert "Docker CLI missing" in report["blockers"][0]["reason"]
    assert not called


def test_existing_output_is_not_overwritten(tmp_path, cases):
    p = tmp_path / "old.json"
    p.write_text("previous", encoding="utf-8")
    report = smoke.execute(smoke.Options(output=p), head=fake_head_for(cases), docker_available=True)
    assert report["status"] == "BLOCKED_NOT_VERIFIED"
    assert p.read_text(encoding="utf-8") == "previous"


def test_dry_run_never_claims_pass_or_writes_file(tmp_path):
    path = tmp_path / "plan.json"
    report = smoke.execute(smoke.Options(output=path, dry_run=True), docker_available=False)
    assert report["status"] == "PLAN_ONLY_NOT_EXECUTED"
    assert not path.exists()


def test_source_hash_stable_and_paraphrase_semantics():
    x = smoke.expected_spec(10**18)
    y = smoke.expected_spec(10**18)
    assert smoke.digest(smoke.canonical(x)) == smoke.digest(smoke.canonical(y))
    z = smoke.expected_spec(2 * 10**18)
    assert smoke.digest(smoke.canonical(x)) != smoke.digest(smoke.canonical(z))


def test_final_status_summary_handles_mixed_stage_shapes():
    """Reproduce the Windows traceback after successful Forge test execution."""
    stages = {
        "preflight": "PASS",
        "llm_head": "PASS_LIVE",
        "contract_language_validation": "PASS",
        "forge_build": {"exit_code": 0, "stdout_tail": "compiled"},
        "forge_test": {"exit_code": 0, "stdout_tail": "18 tests passed"},
        "forge_tests_passed": 18,
        "compiled_artifact": "PASS",
    }
    assert smoke.summarize_stages(stages) == {
        "preflight": "PASS", "llm_head": "PASS_LIVE",
        "contract_language_validation": "PASS", "forge_build": 0,
        "forge_test": 0, "forge_tests_passed": 18,
        "compiled_artifact": "PASS",
    }


def test_main_prints_successful_mixed_stage_report_without_rerunning(tmp_path, monkeypatch, capsys):
    """The original smoke completed but its final print raised AttributeError."""
    report = {"status": "PASS_SMOKE_NOT_CERTIFIED", "blockers": [],
              "stages": {"preflight": "PASS", "forge_build": {"exit_code": 0},
                         "forge_test": {"exit_code": 0}, "forge_tests_passed": 18}}
    calls = []
    def canned_run(options):
        calls.append(options)
        return report
    monkeypatch.setattr(smoke, "execute", canned_run)
    output = tmp_path / "new-report.json"
    rc = smoke.main(["--output", str(output)])
    emitted = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert len(calls) == 1
    assert emitted == {"status": "PASS_SMOKE_NOT_CERTIFIED", "blockers": [],
                       "stages": {"preflight": "PASS", "forge_build": 0,
                                  "forge_test": 0, "forge_tests_passed": 18},
                       "report": str(output)}


def test_main_blocked_report_remains_visible(tmp_path, monkeypatch, capsys):
    report = {"status": "BLOCKED_NOT_VERIFIED",
              "blockers": [{"stage": "forge_test", "reason": "failed tests"}],
              "stages": {"preflight": "PASS", "forge_build": {"exit_code": 0},
                         "forge_test": {"exit_code": 1}, "forge_tests_passed": 0}}
    monkeypatch.setattr(smoke, "execute", lambda options: report)
    rc = smoke.main(["--output", str(tmp_path / "blocked-report.json")])
    emitted = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert emitted["stages"]["forge_tests_passed"] == 0
    assert emitted["stages"]["forge_test"] == 1
    assert emitted["blockers"][0]["reason"] == "failed tests"
