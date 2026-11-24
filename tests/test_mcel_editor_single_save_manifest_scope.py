"""Regression for live Code Editor Save stalling in full-tree manifest hashing.

Only the explicit single-file, no-validation Save has target-scoped freshness.
Reviewed project transactions continue to use full-project manifests.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from main_computer import mcel_project_edit_transaction as engine


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo(tmp_path: Path) -> tuple[Path, Path, bytes]:
    repo = tmp_path / 'main_computer'
    repo.mkdir()
    shutil.copy2(Path(__file__).resolve().parents[1] / 'new_patch.py', repo / 'new_patch.py')
    source = repo / 'runtime' / 'reports' / 'mcel-hosted-signal-proof' / 'reference.js'
    source.parent.mkdir(parents=True)
    before = b'export const mcelHostedAcceptance = 1;'
    source.write_bytes(before)
    # Large ignored-as-unrelated runtime tree must never be traversed by the
    # narrow Save path. Target is intentionally inside runtime itself.
    for i in range(500):
        p = repo / 'runtime' / 'unrelated' / f'generated-{i:04d}.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('irrelevant')
    return repo, source, before


def _change(before: bytes):
    return [{'operation': 'modify',
             'path': 'runtime/reports/mcel-hosted-signal-proof/reference.js',
             'expected_before_sha256': _sha(before),
             'replacement_text': 'export const mcelHostedAcceptance = 2;'}]


def _prepare(tmp_path: Path, repo: Path, changes, **kwargs):
    return engine.prepare_project_edit_transaction(
        repo_root=repo, project_root='.', changes=changes,
        output_dir=tmp_path / 'outside-output', validations=[],
        project_manifest_scope=engine.SINGLE_MODIFY_SCOPE, **kwargs)


def test_real_explicit_save_avoids_full_tree_manifest_and_copy(tmp_path, monkeypatch):
    repo, source, before = _repo(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Full repository traversal during single-file Save')
    monkeypatch.setattr(engine, '_project_manifest', forbidden)
    monkeypatch.setattr(engine, '_copy_project', forbidden)
    report = _prepare(tmp_path, repo, _change(before))
    assert report['dry_run']['ok'] is True
    assert report['source_project_manifest_scope'] == engine.SINGLE_MODIFY_SCOPE
    assert len(report['source_project_files']) == 1
    assert report['source_project_files'][0]['sha256'] == _sha(before)
    assert source.read_bytes() == before
    receipt = engine.apply_project_edit_transaction(repo_root=repo, transaction=report, reviewed=True)
    assert source.read_bytes() == b'export const mcelHostedAcceptance = 2;'
    assert receipt['source_project_manifest_scope'] == engine.SINGLE_MODIFY_SCOPE
    assert receipt['unrelated_project_files_inspected'] is False
    assert receipt['strict_project_manifest_required'] is False
    assert receipt['files'][0]['written_sha256'] == _sha(source.read_bytes())


def test_target_changed_after_prepare_blocks_live_write(tmp_path):
    repo, source, before = _repo(tmp_path)
    report = _prepare(tmp_path, repo, _change(before))
    source.write_bytes(b'changed by concurrent writer')
    with pytest.raises(engine.ProjectEditTransactionError, match='Live source hash drifted'):
        engine.apply_project_edit_transaction(repo_root=repo, transaction=report, reviewed=True)
    assert source.read_bytes() == b'changed by concurrent writer'


def test_strict_manifest_refused_for_scoped_report(tmp_path):
    repo, source, before = _repo(tmp_path)
    report = _prepare(tmp_path, repo, _change(before))
    with pytest.raises(engine.ProjectEditTransactionError, match='Target-scoped Save cannot enforce'):
        engine.apply_project_edit_transaction(repo_root=repo, transaction=report,
                                              reviewed=True, require_project_manifest=True)
    assert source.read_bytes() == before


def test_unreviewed_save_never_writes(tmp_path):
    repo, source, before = _repo(tmp_path)
    report = _prepare(tmp_path, repo, _change(before))
    with pytest.raises(engine.ProjectEditTransactionError, match='reviewed=True'):
        engine.apply_project_edit_transaction(repo_root=repo, transaction=report, reviewed=False)
    assert source.read_bytes() == before


def test_target_scope_rejects_multi_file_create_and_validation(tmp_path):
    repo, source, before = _repo(tmp_path)
    for changes, validations in [
        (_change(before) + [{'operation':'modify','path':'new_patch.py',
                             'expected_before_sha256':engine.sha256_file(repo/'new_patch.py'),
                             'replacement_text':'new content'}], []),
        ([{'operation':'create','path':'new.js','replacement_text':'new'}], []),
        (_change(before), [{'argv':['python','--version'],'cwd':'.'}]),
    ]:
        with pytest.raises(engine.ProjectEditTransactionError, match='exactly one modify'):
            engine.prepare_project_edit_transaction(repo_root=repo, project_root='.',
                changes=changes, output_dir=tmp_path/'output2',
                validations=validations, project_manifest_scope=engine.SINGLE_MODIFY_SCOPE)
    assert source.read_bytes() == before


def test_scoped_report_rejects_tampered_source_inventory(tmp_path):
    repo, source, before = _repo(tmp_path)
    report = _prepare(tmp_path, repo, _change(before))
    report['source_project_files'][0]['sha256'] = '0'*64
    with pytest.raises(engine.ProjectEditTransactionError, match='Target-scoped source manifest mismatch'):
        engine.apply_project_edit_transaction(repo_root=repo, transaction=report, reviewed=True)
    assert source.read_bytes() == before


def test_full_project_manifest_remains_default(tmp_path, monkeypatch):
    repo, source, before = _repo(tmp_path)
    actual = engine._project_manifest
    calls=[]
    def counted(path):
        calls.append(path)
        return actual(path)
    monkeypatch.setattr(engine, '_project_manifest', counted)
    report = engine.prepare_project_edit_transaction(
        repo_root=repo, project_root='.', changes=_change(before),
        output_dir=tmp_path/'full', validations=[])
    assert report['source_project_manifest_scope'] == engine.FULL_PROJECT_SCOPE
    assert len(report['source_project_files']) >= 501
    receipt = engine.apply_project_edit_transaction(repo_root=repo, transaction=report,
                                                     reviewed=True, require_project_manifest=True)
    assert receipt['unrelated_project_files_inspected'] is True
    assert len(calls) >= 2


def test_live_http_save_chooses_scoped_mode_and_full_review_stays_full(tmp_path, monkeypatch):
    """Exercise the real loopback route, not just the transaction helper."""
    import json
    import threading
    from urllib.request import Request, urlopen
    from main_computer.config import MainComputerConfig
    # The repository snapshots omit full game_projects payloads. The server
    # loads a global unrelated demo game on import; replace only that embedded
    # game's static HTML with an inert value, leaving editor routes real.
    from main_computer import game_web_loader
    monkeypatch.setattr(game_web_loader, 'read_game_bundle', lambda *args, **kwargs: '')
    from main_computer.viewport import ViewportServer

    repo, source, before = _repo(tmp_path)
    def request(base, route, data):
        req = Request(base+route, data=json.dumps(data).encode('utf-8'),
                      headers={'Content-Type':'application/json'}, method='POST')
        with urlopen(req,timeout=12) as response:
            assert response.status == 200
            return json.loads(response.read())
    # Fail if the standard single-file Save enumerates or copies the tree.
    monkeypatch.setattr(engine, '_project_manifest', lambda *_: (_ for _ in ()).throw(
        AssertionError('Unwanted full-project hash in Save')))
    monkeypatch.setattr(engine, '_copy_project', lambda *_: (_ for _ in ()).throw(
        AssertionError('Unwanted full-project staging in Save')))
    server = ViewportServer(('127.0.0.1',0),MainComputerConfig(workspace=repo))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'
        saved = request(base,'/api/applications/editor/project/file/save',{
            'repo_dir': '.', 'project_root': '.',
            'path': 'runtime/reports/mcel-hosted-signal-proof/reference.js',
            'expected_before_sha256': _sha(before),
            'replacement_text': 'export const mcelHostedAcceptance = 2;',
            'explicit_save': True, 'stale_source_checked': True,
            'write_policy': 'author-owned-source', 'validation_profile':'none',
        })
        assert saved['ok'] is True
        assert saved['state'] == 'applied'
        assert saved['transaction']['source_project_manifest_scope'] == engine.SINGLE_MODIFY_SCOPE
        assert saved['receipt']['unrelated_project_files_inspected'] is False
        assert source.read_bytes() == b'export const mcelHostedAcceptance = 2;'
    finally:
        server.shutdown()
        server_thread.join(timeout=4)
