"""Regression coverage for isolated MCEL semantic Frob browser suites."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tools import mcel_semantic_other_apps_frob as frob


def test_suite_runner_isolates_each_pytest_process_and_deadline(monkeypatch, capsys):
    monkeypatch.setattr(frob, 'TESTS', ['tests/first.py', 'tests/second.py'])
    seen = []

    def fake_run(command, **kwargs):
        seen.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='===== 4 passed in 2.3s =====', stderr='')

    monkeypatch.setattr(frob.subprocess, 'run', fake_run)
    results, issues = frob.run_browser_regressions(67)
    assert results == {'browserSuite.first': True, 'browserSuite.second': True}
    assert issues == []
    assert len(seen) == 2
    assert [v[0][-1] for v in seen] == frob.TESTS
    assert all(v[0][0:3] == [frob.sys.executable, '-m', 'pytest'] for v in seen)
    assert all(v[1]['timeout'] == 67 and v[1]['cwd'] == frob.ROOT and v[1]['capture_output'] for v in seen)
    assert capsys.readouterr().out.count('  PASS browserSuite.') == 2


def test_suite_runner_preserves_failure_and_keeps_running(monkeypatch):
    monkeypatch.setattr(frob, 'TESTS', ['tests/failing.py', 'tests/passing.py'])
    runs = iter([
        SimpleNamespace(returncode=1, stdout='test_does_something FAILED\n===== 1 failed =====', stderr='AssertionError: wrong'),
        SimpleNamespace(returncode=0, stdout='===== 1 passed =====', stderr=''),
    ])
    monkeypatch.setattr(frob.subprocess, 'run', lambda *args, **kwargs: next(runs))
    results, issues = frob.run_browser_regressions(30)
    assert results == {'browserSuite.failing': False, 'browserSuite.passing': True}
    assert 'test_does_something FAILED' in issues[0]
    assert 'AssertionError: wrong' in issues[0]


def test_suite_timeout_preserves_last_test_even_with_bytes_and_continues(monkeypatch, capsys):
    monkeypatch.setattr(frob, 'TESTS', ['tests/slow.py', 'tests/good.py'])
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, 2, output=b'tests/slow.py::test_long_running ', stderr=b'')
        return SimpleNamespace(returncode=0, stdout='1 passed in 0.01s', stderr='')

    monkeypatch.setattr(frob.subprocess, 'run', fake_run)
    results, issues = frob.run_browser_regressions(2)
    assert results == {'browserSuite.slow': False, 'browserSuite.good': True}
    assert 'test_long_running' in issues[0]
    assert 'TIMEOUT browserSuite.slow' in capsys.readouterr().out


def test_suite_cannot_pass_empty_collection_or_nonzero_exit(monkeypatch):
    monkeypatch.setattr(frob, 'TESTS', ['tests/empty.py', 'tests/nonzero.py'])
    runs = iter([
        SimpleNamespace(returncode=0, stdout='no tests ran in 0.01s', stderr=''),
        SimpleNamespace(returncode=1, stdout='1 passed, 1 failed', stderr=''),
    ])
    monkeypatch.setattr(frob.subprocess, 'run', lambda *args, **kwargs: next(runs))
    results, issues = frob.run_browser_regressions(30)
    assert results == {'browserSuite.empty': False, 'browserSuite.nonzero': False}
    assert len(issues) == 2


def test_nonpositive_suite_timeout_is_rejected():
    with pytest.raises(ValueError, match='positive'):
        frob.run_browser_regressions(0)


def test_calculator_ai_tool_proof_suites_are_mandatory():
    required = {
        'tests/test_mcel_calculator_ai_tool.py',
        'tests/test_mcel_calculator_live_ollama_smoke.py',
        'tests/test_mcel_calculator_completion_gate.py',
    }
    assert set(frob.CALCULATOR_AI_TOOL_TESTS) == required
    assert required.issubset(frob.TESTS)
    assert len(frob.TESTS) == len(set(frob.TESTS))


def test_ai_tool_suite_with_skipped_core_does_not_pass(monkeypatch):
    monkeypatch.setattr(frob, 'TESTS', ['tests/test_mcel_calculator_ai_tool.py'])
    monkeypatch.setattr(frob.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(returncode=0, stdout='7 passed, 4 skipped in 0.40s', stderr=''))
    results, issues = frob.run_browser_regressions(20)
    assert results == {'browserSuite.test_mcel_calculator_ai_tool': False}
    assert len(issues) == 1


def test_ai_tool_suite_requires_successful_real_test_collection(monkeypatch):
    monkeypatch.setattr(frob, 'TESTS', ['tests/test_mcel_calculator_live_ollama_smoke.py'])
    monkeypatch.setattr(frob.subprocess, 'run', lambda *_args, **_kwargs:
        SimpleNamespace(returncode=0, stdout='6 passed in 1.3s', stderr=''))
    results, issues = frob.run_browser_regressions(20)
    assert results['browserSuite.test_mcel_calculator_live_ollama_smoke']
    assert not issues
