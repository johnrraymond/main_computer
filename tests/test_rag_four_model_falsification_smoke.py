from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "rag_four_model_falsification_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "rag_four_model_falsification_smoke_under_test",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


def test_compact_schema_rejects_long_and_extra_values() -> None:
    valid = {
        "obligations": ["one atomic obligation"],
        "risks": [],
        "tests": [],
        "unknowns": [],
    }
    smoke.validate_schema_value(valid, smoke.SCOUT_SCHEMA)

    too_long = dict(valid)
    too_long["obligations"] = ["x" * 121]
    with pytest.raises(ValueError, match="maximum is 120"):
        smoke.validate_schema_value(too_long, smoke.SCOUT_SCHEMA)

    extra = dict(valid)
    extra["unexpected"] = []
    with pytest.raises(ValueError, match="unexpected keys"):
        smoke.validate_schema_value(extra, smoke.SCOUT_SCHEMA)


def test_solver_protocol_is_raw_code_with_thinking_disabled() -> None:
    solver = smoke.MODEL_BY_KEY["solver"]
    assert solver.think is False
    assert smoke.MODEL_BY_KEY["integrator"].think is False

    baseline_system, _ = smoke.solver_prompt(
        solver,
        smoke.EVIDENCE_PACKER_CASE,
        None,
        baseline=True,
    )
    cascade_system, _ = smoke.solver_prompt(
        solver,
        smoke.EVIDENCE_PACKER_CASE,
        {"requirements": [], "algorithm": [], "tests": [], "unresolved": []},
        baseline=False,
    )

    assert "Return raw Python source only" in baseline_system
    assert "Return raw Python source only" in cascade_system
    assert "Return one compact JSON object" not in baseline_system
    assert "Return one compact JSON object" not in cascade_system


def test_raw_code_call_omits_json_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_http_json(*, method, url, payload, timeout):
        captured.update(payload)
        return {
            "message": {
                "content": "```python\n" + smoke.GOLD_CODE.strip() + "\n```",
                "thinking": "",
            },
            "total_duration": 100,
            "load_duration": 10,
            "prompt_eval_count": 2,
            "prompt_eval_duration": 20,
            "eval_count": 3,
            "eval_duration": 70,
        }

    monkeypatch.setattr(smoke, "http_json", fake_http_json)
    solver = smoke.MODEL_BY_KEY["solver"]
    system, user = smoke.solver_prompt(
        solver,
        smoke.EVIDENCE_PACKER_CASE,
        None,
        baseline=True,
    )
    result = smoke.call_stage(
        base_url="http://ollama.invalid",
        spec=solver,
        stage="baseline_solver",
        system=system,
        user=user,
        schema=None,
        timeout=1,
        keep_alive="0",
        temperature=0.2,
        response_kind="code",
    )

    assert result.ok
    assert result.parsed == {"code": smoke.GOLD_CODE.strip()}
    assert "format" not in captured
    assert captured["think"] is False
    assert captured["options"]["num_predict"] == solver.num_predict


def test_incomplete_paths_are_not_compared() -> None:
    passed = smoke.CandidateEvaluation(
        label="baseline",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    report = smoke.comparison(
        baseline_results=[smoke._self_test_stage("baseline_solver")],
        baseline_eval=passed,
        cascade_results=[smoke._self_test_stage("scout")],
        cascade_eval=None,
        execute=True,
    )

    assert report["comparable"] is False
    assert report["baseline_tests_passed"] is None
    assert report["cascade_tests_passed"] is None
    assert report["cascade_total_model_compute_ns"] is None


def test_gold_candidate_reaches_deterministic_harness() -> None:
    evaluation = smoke.evaluate_code(
        label="gold",
        code=smoke.GOLD_CODE,
        required_function=smoke.EVIDENCE_PACKER_CASE.function_name,
        timeout=5,
        execute=True,
    )
    assert evaluation.safety_ok
    assert evaluation.tests_ok


def _stage(stage: str, parsed: dict | None, *, ok: bool = True) -> smoke.StageResult:
    content = smoke.canonical_json(parsed) if parsed is not None else ""
    return smoke.StageResult(
        stage=stage,
        model="fake",
        ok=ok,
        parsed=parsed,
        content=content,
        thinking="",
        request_sha256="0" * 64,
        response_sha256="1" * 64,
        wall_ms=1,
        total_duration_ns=10,
        load_duration_ns=0,
        prompt_eval_count=1,
        prompt_eval_duration_ns=5,
        eval_count=1,
        eval_duration_ns=5,
        error=None if ok else "fake failure",
    )


def _args(policy: str = "on-failure") -> SimpleNamespace:
    return SimpleNamespace(
        ollama_url="http://ollama.invalid",
        timeout=1.0,
        keep_alive="0",
        temperature=0.2,
        execution_timeout=2.0,
        execute=True,
        escalation_policy=policy,
    )


def test_deterministic_merge_keeps_original_task_authoritative() -> None:
    scout = {
        "obligations": ["invented optional None behavior"],
        "risks": [],
        "tests": ["check exact budget"],
        "unknowns": [],
    }
    falsifier = {
        "rejected": ["default=2 is wrong"],
        "missing": ["accept None"],
        "counterexamples": ["None should mean unlimited"],
        "corrections": ["replace default 2"],
    }
    packet = smoke.deterministic_merge(
        case=smoke.EVIDENCE_PACKER_CASE,
        scout=scout,
        falsifier=falsifier,
    )

    assert packet["required_outcomes"] == list(
        smoke.EVIDENCE_PACKER_CASE.acceptance_contract
    )
    assert "original task" in packet["authority"].lower()
    assert "accept None" in packet["untrusted_review_flags"]
    assert packet["scout_tests"] == ["check exact budget"]


def test_fast_lane_skips_integrator_when_code_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scout = {
        "obligations": ["validate positive integers"],
        "risks": ["bool is an int subclass"],
        "tests": ["reject True"],
        "unknowns": [],
    }
    falsifier = {
        "rejected": [],
        "missing": [],
        "counterexamples": ["max_chars=True must raise"],
        "corrections": [],
    }
    called: list[str] = []

    def fake_call_stage(**kwargs):
        stage = kwargs["stage"]
        called.append(stage)
        if stage == "scout":
            return _stage(stage, scout)
        if stage == "falsifier":
            return _stage(stage, falsifier)
        if stage == "cascade_solver":
            return _stage(stage, {"code": smoke.GOLD_CODE})
        raise AssertionError(f"unexpected stage {stage}")

    passed = smoke.CandidateEvaluation(
        label="cascade",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    monkeypatch.setattr(smoke, "call_stage", fake_call_stage)
    monkeypatch.setattr(smoke, "evaluate_code", lambda **kwargs: passed)

    run = smoke.run_cascade(args=_args(), case=smoke.EVIDENCE_PACKER_CASE)

    assert called == ["scout", "falsifier", "cascade_solver"]
    assert run.escalated is False
    assert run.evaluation is passed
    assert smoke.cascade_expected_stages(run.results) == (
        "scout",
        "falsifier",
        "cascade_solver",
    )


def test_failed_fast_lane_escalates_to_12b_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scout = {
        "obligations": ["validate positive integers"],
        "risks": [],
        "tests": ["reject True"],
        "unknowns": [],
    }
    falsifier = {
        "rejected": [],
        "missing": [],
        "counterexamples": ["max_chars=True must raise"],
        "corrections": [],
    }
    integrator = {
        "requirements": ["reject bool"],
        "algorithm": ["validate before iterating"],
        "tests": ["call with True"],
        "unresolved": [],
    }
    called: list[str] = []

    def fake_call_stage(**kwargs):
        stage = kwargs["stage"]
        called.append(stage)
        payloads = {
            "scout": scout,
            "falsifier": falsifier,
            "cascade_solver": {"code": "def pack_evidence(*args): return []"},
            "integrator": integrator,
            "cascade_retry_solver": {"code": smoke.GOLD_CODE},
        }
        return _stage(stage, payloads[stage])

    failed = smoke.CandidateEvaluation(
        label="cascade",
        safety_ok=True,
        tests_ok=False,
        returncode=1,
        stdout="",
        stderr="failed",
        duration_ms=1,
        failure_kind="deterministic_test_failure",
        failure_reason="failed",
    )
    passed = smoke.CandidateEvaluation(
        label="cascade_retry",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    evaluations = iter((failed, passed))
    monkeypatch.setattr(smoke, "call_stage", fake_call_stage)
    monkeypatch.setattr(
        smoke,
        "evaluate_code",
        lambda **kwargs: next(evaluations),
    )

    run = smoke.run_cascade(args=_args(), case=smoke.EVIDENCE_PACKER_CASE)

    assert called == [
        "scout",
        "falsifier",
        "cascade_solver",
        "integrator",
        "cascade_retry_solver",
    ]
    assert run.escalated is True
    assert run.escalation_reason == "fast-lane code failed deterministic tests"
    assert run.first_evaluation is failed
    assert run.evaluation is passed
    assert "escalation_review" in run.merged_packet


def test_completed_fast_lane_is_comparable_without_integrator() -> None:
    passed = smoke.CandidateEvaluation(
        label="passed",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    report = smoke.comparison(
        baseline_results=[smoke._self_test_stage("baseline_solver", compute_ns=20)],
        baseline_eval=passed,
        cascade_results=[
            smoke._self_test_stage("scout", compute_ns=3),
            smoke._self_test_stage("falsifier", compute_ns=4),
            smoke._self_test_stage("cascade_solver", compute_ns=15),
        ],
        cascade_eval=passed,
        execute=True,
    )

    assert report["comparable"] is True
    assert report["cascade_escalated"] is False
    assert report["cascade_solver_stage"] == "cascade_solver"
    assert report["cascade_total_model_compute_ns"] == 22


def test_re_import_is_allowed_and_reaches_harness() -> None:
    evaluation = smoke.evaluate_code(
        label="regex",
        code="import re\n" + smoke.GOLD_CODE,
        required_function=smoke.EVIDENCE_PACKER_CASE.function_name,
        timeout=5,
        execute=True,
    )

    assert evaluation.safety_ok is True
    assert evaluation.tests_ok is True
    assert evaluation.failure_kind is None


def test_evaluation_failure_kinds_are_distinct() -> None:
    policy = smoke.evaluate_code(
        label="policy",
        code=(
            "import os\n"
            "def pack_evidence(chunks, max_chars, max_per_source=2):\n"
            "    return []\n"
        ),
        required_function=smoke.EVIDENCE_PACKER_CASE.function_name,
        timeout=5,
        execute=True,
    )
    syntax = smoke.evaluate_code(
        label="syntax",
        code="def pack_evidence(:\n    pass\n",
        required_function=smoke.EVIDENCE_PACKER_CASE.function_name,
        timeout=5,
        execute=True,
    )
    deterministic = smoke.evaluate_code(
        label="tests",
        code=(
            "def pack_evidence(chunks, max_chars, max_per_source=2):\n"
            "    return chunks\n"
        ),
        required_function=smoke.EVIDENCE_PACKER_CASE.function_name,
        timeout=5,
        execute=True,
    )

    assert policy.failure_kind == "safety_policy_failure"
    assert syntax.failure_kind == "syntax_failure"
    assert deterministic.failure_kind == "deterministic_test_failure"


def test_policy_rejection_does_not_escalate_but_syntax_does() -> None:
    solver = _stage("cascade_solver", {"code": smoke.GOLD_CODE})
    policy = smoke.CandidateEvaluation(
        label="policy",
        safety_ok=False,
        tests_ok=False,
        returncode=None,
        stdout="",
        stderr="blocked",
        duration_ms=1,
        failure_kind="safety_policy_failure",
        failure_reason="blocked",
    )
    syntax = smoke.CandidateEvaluation(
        label="syntax",
        safety_ok=False,
        tests_ok=False,
        returncode=None,
        stdout="",
        stderr="syntax",
        duration_ms=1,
        failure_kind="syntax_failure",
        failure_reason="syntax",
    )

    assert smoke.should_escalate(
        policy="on-failure",
        execute=True,
        solver=solver,
        evaluation=policy,
    ) == (False, None)
    assert smoke.should_escalate(
        policy="on-failure",
        execute=True,
        solver=solver,
        evaluation=syntax,
    ) == (True, "fast-lane code had invalid Python syntax")


def test_comparison_separates_fast_lane_and_escalation_compute() -> None:
    passed = smoke.CandidateEvaluation(
        label="passed",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    report = smoke.comparison(
        baseline_results=[smoke._self_test_stage("baseline_solver", compute_ns=20)],
        baseline_eval=passed,
        cascade_results=[
            smoke._self_test_stage("scout", compute_ns=3),
            smoke._self_test_stage("falsifier", compute_ns=4),
            smoke._self_test_stage("cascade_solver", compute_ns=15),
            smoke._self_test_stage("integrator", compute_ns=8),
            smoke._self_test_stage("cascade_retry_solver", compute_ns=17),
        ],
        cascade_eval=passed,
        cascade_first_eval=passed,
        execute=True,
    )

    observed = report["observed_compute"]
    assert report["comparable"] is True
    assert report["fast_lane_tests_passed"] is True
    assert observed["fast_lane_total_model_compute_ns"] == 22
    assert observed["escalation_total_model_compute_ns"] == 25
    assert observed["cascade_total_model_compute_ns"] == 47
    assert report["interpretation"] == (
        "both passed; equal correctness with higher total cascade compute in this run"
    )


def test_policy_rejection_is_not_reported_as_correctness_regression() -> None:
    passed = smoke.CandidateEvaluation(
        label="baseline",
        safety_ok=True,
        tests_ok=True,
        returncode=0,
        stdout="ALL_TESTS_PASSED",
        stderr="",
        duration_ms=1,
    )
    rejected = smoke.CandidateEvaluation(
        label="cascade",
        safety_ok=False,
        tests_ok=False,
        returncode=None,
        stdout="",
        stderr="blocked",
        duration_ms=1,
        failure_kind="safety_policy_failure",
        failure_reason="blocked",
    )
    report = smoke.comparison(
        baseline_results=[smoke._self_test_stage("baseline_solver", compute_ns=20)],
        baseline_eval=passed,
        cascade_results=[
            smoke._self_test_stage("scout", compute_ns=3),
            smoke._self_test_stage("falsifier", compute_ns=4),
            smoke._self_test_stage("cascade_solver", compute_ns=15),
        ],
        cascade_eval=rejected,
        cascade_first_eval=rejected,
        execute=True,
    )

    assert report["comparable"] is False
    assert "not correctness regressions" in report["interpretation"]
