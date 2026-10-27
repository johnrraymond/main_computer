"""Frozen QW5 bank provenance: exact-match recovery, immutable snapshots."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import nanojev_qw5 as qw
from tools import nanojev_qw5_join as join
from tools import nanojev_qw5_source_archive as recovery


def setup(tmp_path):
    campaign = tmp_path / 'campaign'
    stage = campaign / 'stages' / 'outward_64'
    hard = campaign / 'hard_emp_frontier_v2'
    stage.mkdir(parents=True);hard.mkdir()
    content = {'stage':(stage/'latest.pt',b'original ordinary'),
               'hard':(hard/'latest.pt',b'original specialist'),
               'left':(campaign/'source_left.pt',b'left bank source'),
               'right':(campaign/'source_right.pt',b'right bank source')}
    for file,data in content.values():
        file.write_bytes(data)
    source = {'campaign':str(campaign.resolve()),'stage_radius':64,'stage_step':300,
              'hard_state_dir':'hard_emp_frontier_v2',
              'specialist_source':'left.left','ladder_output':str((tmp_path/'ladder').resolve()),
              'sha256':{k:qw.sha(path) for k,(path,_) in content.items()}}
    run = tmp_path / 'qw5';run.mkdir()
    cfg={'schema':join.SCHEMA,'source':source}
    (run/'config.json').write_text(json.dumps(cfg))
    return run, cfg, content


def test_recovery_survives_live_hard_checkpoint_mutation_and_removal(tmp_path):
    run,cfg,source=setup(tmp_path)
    pinned=join.archive_sources(run,cfg)
    assert all(p.is_file() for p in pinned.values())
    source['hard'][0].write_bytes(b'NEW specialist trained by EMP')
    source['stage'][0].unlink()
    resolved=join.verify_sources(cfg,run)
    assert all(resolved[k]==pinned[k] for k in recovery.KEYS)
    audit=recovery.inspect(run)
    assert not audit['hard']['live_match'] and audit['hard']['archive_match']
    assert not audit['stage']['live_match'] and audit['stage']['archive_match']
    assert join.archive_sources(run,cfg)==resolved  # idempotent; no mutation
    assert cfg==json.loads((run/'config.json').read_text())


def test_recover_exact_historical_hard_checkpoint(monkeypatch,tmp_path):
    run,cfg,source=setup(tmp_path)
    original_hard=source['hard'][0]
    backup=tmp_path/'snapshots'/'hard-v1.pt'
    backup.parent.mkdir()
    backup.write_bytes(original_hard.read_bytes())
    original_hard.write_bytes(b'later specialist')
    with pytest.raises(RuntimeError,match='QW5_SOURCE_SHA_DRIFT:hard'):
        join.verify_sources(cfg,run)
    monkeypatch.setattr(qw,'load_five',lambda *_a,**_kw:({},cfg['source']))
    got,paths=recovery.resolve(run,hard_checkpoint=backup)
    assert paths['hard']==backup
    recovered=join.archive_sources(run,got,paths)
    assert recovered['hard'].read_bytes()==b'original specialist'
    assert join.verify_sources(cfg,run)['hard']==recovered['hard']
    assert original_hard.read_bytes()==b'later specialist'


def test_recovery_fails_closed_without_matching_original(monkeypatch,tmp_path):
    run,cfg,source=setup(tmp_path)
    source['hard'][0].write_bytes(b'later specialist')
    with pytest.raises(RuntimeError,match='QW5_ORIGINAL_SOURCE_UNAVAILABLE:hard'):
        recovery.resolve(run)
    fake=tmp_path/'fake-hard.pt';fake.write_bytes(b'incompatible historical candidate')
    with pytest.raises(RuntimeError,match='QW5_ORIGINAL_SOURCE_UNAVAILABLE:hard'):
        recovery.resolve(run,hard_checkpoint=fake)
    assert not (run/'source_archive').exists()


def test_recovery_search_finds_exact_backup(monkeypatch,tmp_path):
    run,cfg,source=setup(tmp_path)
    backup=tmp_path/'snapshots'/'nested'/'specialist-old.pt'
    backup.parent.mkdir(parents=True)
    backup.write_bytes(source['hard'][0].read_bytes())
    source['hard'][0].write_bytes(b'new weights')
    monkeypatch.setattr(qw,'load_five',lambda *_a,**_kw:({},cfg['source']))
    got,paths=recovery.resolve(run,search_roots=[tmp_path/'snapshots'])
    assert paths['hard']==backup
    assert got==cfg


def test_archive_tampering_is_rejected_even_if_live_matches(tmp_path):
    run,cfg,_=setup(tmp_path)
    pinned=join.archive_sources(run,cfg)
    pinned['hard'].write_bytes(b'corrupt')
    with pytest.raises(RuntimeError,match='QW5_SOURCE_ARCHIVE_CORRUPT:hard'):
        join.verify_sources(cfg,run)
