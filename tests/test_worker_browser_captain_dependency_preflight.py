"""The Captain subprocess shares the launcher's Python interpreter and dependencies."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ENTRY = Path(__file__).resolve().parents[1] / 'tools' / 'worker_browser_acceptance.py'
SPEC = importlib.util.spec_from_file_location('tested_captain_dependency_preflight', ENTRY)
assert SPEC and SPEC.loader
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def test_pinned_requirement_is_canonical():
    assert app._captain_signing_requirement() == 'eth-account==0.13.7'
    assert 'eth_account' in app.CAPTAIN_SIGNING_MODULES
    assert 'eth_abi' in app.CAPTAIN_SIGNING_MODULES
    assert 'eth_utils' in app.CAPTAIN_SIGNING_MODULES


def test_import_probe_uses_exact_active_python(monkeypatch):
    calls = []
    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(app.subprocess, 'run', fake_run)
    assert app._captain_signing_imports_ready()
    args, kw = calls[0]
    assert args[:2] == [sys.executable, '-c']
    assert 'from eth_account import Account' in args[2]
    assert kw['timeout'] == 20


def test_ready_dependency_performs_no_pip_mutation(monkeypatch):
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: True)
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: pytest.fail('unexpected pip command'))
    app.ensure_captain_signing_dependencies()


def test_missing_dependency_bootstraps_only_pinned_wheel_inside_venv(monkeypatch):
    checks = iter([False, True])
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: next(checks))
    monkeypatch.setattr(app.importlib.util, 'find_spec', lambda name: None if name == 'eth_account' else object())
    monkeypatch.setattr(app.sys, 'prefix', '/test/venv')
    monkeypatch.setattr(app.sys, 'base_prefix', '/test/base')
    calls = []
    def fake_run(args, **kw):
        calls.append((args,kw))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(app.subprocess, 'run', fake_run)
    app.ensure_captain_signing_dependencies()
    assert len(calls) == 1
    command, kw = calls[0]
    assert command == [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', 'eth-account==0.13.7']
    assert kw['timeout'] == 180
    assert kw['capture_output'] is True


def test_refuses_auto_install_into_system_python(monkeypatch):
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: False)
    monkeypatch.setattr(app.importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setattr(app.sys, 'prefix', '/test/system')
    monkeypatch.setattr(app.sys, 'base_prefix', '/test/system')
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: pytest.fail('cannot install to system'))
    with pytest.raises(app.AcceptanceError, match='outside a virtual environment'):
        app.ensure_captain_signing_dependencies()


def test_broken_existing_install_not_hidden_by_pip(monkeypatch):
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: False)
    monkeypatch.setattr(app.importlib.util, 'find_spec', lambda name: object())
    monkeypatch.setattr(app, '_captain_signing_failure_detail', lambda: 'eth_account: ImportError: broken package')
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: pytest.fail('must not auto-upgrade'))
    with pytest.raises(app.AcceptanceError, match='broken package'):
        app.ensure_captain_signing_dependencies()


def test_fails_closed_when_install_does_not_succeed(monkeypatch):
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: False)
    monkeypatch.setattr(app.importlib.util, 'find_spec', lambda name: None)
    monkeypatch.setattr(app.sys, 'prefix', '/test/venv')
    monkeypatch.setattr(app.sys, 'base_prefix', '/test/base')
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(app.AcceptanceError, match='installation failed'):
        app.ensure_captain_signing_dependencies()


def test_not_run_for_wallet_mode(monkeypatch, tmp_path):
    # The regression that matters: dependency auto-install is not global.
    source = ENTRY.read_text(encoding='utf-8')
    assert "if args.action == 'run' and args.mode == 'work-cycle':\n        ensure_captain_signing_dependencies()" in source
    assert "'MC_WORKER_TEST_PYTHON': sys.executable" in source


def test_import_failure_reports_missing_transitive_module_without_full_traceback(monkeypatch):
    monkeypatch.setattr(app, '_captain_signing_imports_ready', lambda: False)
    monkeypatch.setattr(app.importlib.util, 'find_spec', lambda name: object())
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args[2])
        if 'from eth_account' in args[2]:
            return SimpleNamespace(returncode=1, stderr='Traceback (most recent call last):\n  File "C:/secret", line 1\nModuleNotFoundError: No module named \'hexbytes\'\n')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(app.subprocess, 'run', fake_run)
    with pytest.raises(app.AcceptanceError, match=r"eth_account: ModuleNotFoundError: No module named 'hexbytes'") as exc:
        app.ensure_captain_signing_dependencies()
    assert 'C:/secret' not in str(exc.value)
    assert calls == ['from eth_account import Account']


def test_import_failure_identifies_wrong_symbol(monkeypatch):
    def fake_run(args, **kwargs):
        return SimpleNamespace(returncode=1 if 'from eth_abi' in args[2] else 0,
                               stderr="ImportError: cannot import name 'encode' from 'eth_abi'\n")
    monkeypatch.setattr(app.subprocess, 'run', fake_run)
    assert app._captain_signing_failure_detail() == "eth_abi: ImportError: cannot import name 'encode' from 'eth_abi'"


def test_import_failure_omits_arbitrary_subprocess_output(monkeypatch):
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr='my-private-secret=example\\nException without expected format\\n', stdout='private-key',
    ))
    result = app._captain_signing_failure_detail()
    assert 'private' not in result
    assert result.startswith('eth_account: import failed')


def test_import_probe_checks_all_three_when_no_single_import_fails(monkeypatch):
    calls = []
    def fake_run(args, **kw):
        calls.append(args[2]);return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(app.subprocess, 'run', fake_run)
    assert 'combined Captain import' in app._captain_signing_failure_detail()
    assert len(calls) == 3


def test_failure_detail_is_bounded(monkeypatch):
    monkeypatch.setattr(app.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=1, stderr='ImportError: ' + 'z' * 1000,
    ))
    assert len(app._captain_signing_failure_detail()) < 270
