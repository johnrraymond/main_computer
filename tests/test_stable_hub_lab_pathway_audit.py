from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from tools.stable_hub_lab import pathway_audit


def _create_files(root: Path) -> None:
    for selectors in pathway_audit.PATHWAY_LANES.values():
        for rel in selectors:
            file = root / rel
            file.parent.mkdir(parents=True, exist_ok=True)
            file.touch()


def test_all_lanes_pass_but_known_production_gaps_are_never_certified(tmp_path, monkeypatch):
    _create_files(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='1 passed\n', stderr='')
    monkeypatch.setattr(pathway_audit.subprocess, 'run', run)
    result = pathway_audit.run_pathway_audit(root=tmp_path)
    assert result['ok'] is True
    assert result['full_pathway_certified'] is False
    assert len(calls) == len(pathway_audit.PATHWAY_LANES)
    assert any('owner_hub_process_killed_after_hold' in s
               for s in result['open_assurance_boundaries'])
    assert not any('worker_compute_starts_before_funds' in s
                   for s in result['open_assurance_boundaries'])
    for command, kwargs in calls:
        assert command[:3] == [pathway_audit.sys.executable, '-m', 'pytest']
        assert kwargs['check'] is False
        assert kwargs['timeout'] == 300.0


def test_failure_of_any_lane_fails_closed_but_continues_remaining_lanes(tmp_path, monkeypatch):
    _create_files(tmp_path)
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1 if len(calls) == 2 else 0,
                               stdout='failed' if len(calls) == 2 else 'passed', stderr='')
    monkeypatch.setattr(pathway_audit.subprocess, 'run', run)
    result = pathway_audit.run_pathway_audit(root=tmp_path)
    assert result['ok'] is False
    assert sum(not lane['ok'] for lane in result['lanes']) == 1
    assert len(calls) == len(pathway_audit.PATHWAY_LANES)


def test_missing_file_fails_lane_without_running_its_pytest(tmp_path, monkeypatch):
    monkeypatch.setattr(pathway_audit.subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('must not run')))
    result = pathway_audit.run_pathway_audit(root=tmp_path)
    assert not result['ok']
    assert all(lane['missing'] for lane in result['lanes'])


def test_timeout_and_process_error_are_failures(tmp_path, monkeypatch):
    _create_files(tmp_path)
    calls = 0
    def run(command, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        if calls == 2:
            raise OSError('no executable')
        return SimpleNamespace(returncode=0, stdout='pass', stderr='')
    monkeypatch.setattr(pathway_audit.subprocess, 'run', run)
    result = pathway_audit.run_pathway_audit(root=tmp_path, timeout_per_lane=5)
    assert not result['ok']
    assert result['lanes'][0]['exit_code'] == 124
    assert result['lanes'][1]['ok'] is False
    assert len(result['lanes']) == len(pathway_audit.PATHWAY_LANES)


def test_negative_timeout_is_rejected(tmp_path):
    try:
        pathway_audit.run_pathway_audit(root=tmp_path, timeout_per_lane=0)
    except ValueError:
        pass
    else:
        raise AssertionError('timeout must be positive')


def test_main_lab_exposes_pathway_gate_and_never_conflates_with_stress():
    source = (Path(__file__).resolve().parents[1] / 'tools/stable_hub_lab/run_lab.py').read_text()
    assert '--verify-pathway-local' in source
    assert 'if args.verify_pathway_local:' in source
    assert 'full_pathway_certified' in source or 'FULL PATHWAY CERTIFIED' in source
    assert 'run_pathway_audit(' in source


def test_pathway_progress_reports_start_and_finish_for_each_lane(tmp_path, monkeypatch):
    _create_files(tmp_path)
    monkeypatch.setattr(pathway_audit.subprocess, 'run',
                        lambda *a, **kw: SimpleNamespace(returncode=0, stdout='pass', stderr=''))
    events = []
    result = pathway_audit.run_pathway_audit(
        root=tmp_path, progress=lambda name, phase: events.append((name, phase)),
    )
    assert result['ok'] is True
    assert events == [event for name in pathway_audit.PATHWAY_LANES
                      for event in ((name, 'running'), (name, 'pass'))]
