"""0103 checkpoint/diagnostic patches must resume safely and preserve gold sealing."""

import pytest

from tools import nanojev_gamer_v1_g2_admission_stability_0103 as m
from tests.test_nanojev_gamer_v1_g2_admission_stability_0103 import _setup


def test_early_durable_checkpoints_and_diagnostics_without_any_gold(tmp_path,monkeypatch):
    root,_,original=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    dest=m.location(root)
    audit_secret=dest/'seal'/'audit_answers.SECRET.jsonl'
    holdout_secret=dest/'seal'/'holdout_answers.SECRET.jsonl'
    audit_hidden=audit_secret.with_suffix('.not-available')
    holdout_hidden=holdout_secret.with_suffix('.not-available')
    audit_secret.rename(audit_hidden)
    holdout_secret.rename(holdout_hidden)
    m.capture(root=root,device='cpu',checkpoint_every=3,diagnostic_every=4)
    p=dest/'audit'/'progress'
    assert (p/'checkpoint-00003.json').is_file()
    assert (p/'checkpoint-00048.json').is_file()
    assert (p/'diagnostic-00004.json').is_file()
    d=m.read(p/'diagnostic-00004.json')
    assert d['completed']==4 and d['gold_accessed'] is False
    assert d['accuracy_available'] is False
    assert d['statistics']['cases']==4
    assert d['source_sha256']==original
    assert 'gold' not in d['statistics']
    assert 'f1' not in d['statistics']
    s=m.status(root=root)
    assert s['splits']['audit']['saved_shards']==48
    assert s['splits']['audit']['locked'] is True
    assert s['splits']['holdout']['saved_shards']==0
    audit_hidden.rename(audit_secret)
    m.audit(root=root)
    assert (dest/'audit'/'folds'/'protection_1.json').is_file()
    m.predict(root=root,device='cpu',checkpoint_every=3,diagnostic_every=4)
    assert (dest/'holdout'/'progress'/'checkpoint-00003.json').is_file()
    assert (dest/'holdout'/'progress'/'diagnostic-00004.json').is_file()
    assert (dest/'holdout'/'lock.json').is_file()
    # The holdout gold has still never been available in any prediction phase.
    holdout_hidden.rename(holdout_secret)
    result=m.score(root=root)
    assert result['weight_edits']==0
    assert (dest/'scored'/'streams'/'stream-00.json').is_file()
    assert (dest/'scored'/'streams'/'stream-09.json').is_file()
    s=m.status(root=root)
    assert s['status']=='COMPLETE'
    assert s['splits']['holdout']['saved_shards']==40


def test_restart_reuses_per_case_shards_and_earlier_checkpoint(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    prior_infer=m._infer
    count={'calls':0}

    def interrupt_after_twelve(*args,**kwargs):
        count['calls']+=1
        if count['calls']==13:raise KeyboardInterrupt('simulated Ctrl+C')
        return prior_infer(*args,**kwargs)

    monkeypatch.setattr(m,'_infer',interrupt_after_twelve)
    with pytest.raises(KeyboardInterrupt):
        m.capture(root=root,device='cpu',checkpoint_every=5,diagnostic_every=5)
    dest=m.location(root)
    assert len(list((dest/'audit'/'shards').glob('*.json.gz')))==12
    assert m.read(dest/'audit'/'progress'/'checkpoint-00010.json')['completed']==10
    assert m.status(root=root)['splits']['audit']['saved_shards']==12
    monkeypatch.setattr(m,'_infer',prior_infer)
    m.capture(root=root,device='cpu',checkpoint_every=5,diagnostic_every=5)
    assert count['calls']==13
    assert len(list((dest/'audit'/'shards').glob('*.json.gz')))==48
    assert m.read(dest/'audit'/'progress'/'checkpoint-latest.json')['completed']==48
    assert m.read(dest/'audit'/'lock.json')['status']=='AUDIT_PREDICTIONS_LOCKED'


def test_published_0103_commit_resumes_and_bad_hash_still_refused(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    dest=m.location(root)
    saved=dest/'seal'/'prepared.json'
    commit=m.read(saved)
    commit['runner_sha256']=m.LEGACY_RUNNER_SHA256
    m.write(saved,commit)
    m.capture(root=root,device='cpu',checkpoint_every=10,diagnostic_every=20)
    assert (dest/'audit'/'lock.json').is_file()
    commit['runner_sha256']='0'*64
    m.write(saved,commit)
    with pytest.raises(ValueError,match='PREDECLARATION_DRIFT'):
        m.verify(root,dest)


def test_invalid_intervals_fail_before_inference(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,audit_cases=48,cases=40)
    with pytest.raises(ValueError,match='CHECKPOINT_INTERVAL_INVALID'):
        m.capture(root=root,device='cpu',checkpoint_every=0)
    with pytest.raises(ValueError,match='DIAGNOSTIC_INTERVAL_INVALID'):
        m.capture(root=root,device='cpu',diagnostic_every=0)
    assert not (m.location(root)/'audit'/'lock.json').exists()


def test_status_unprepared(tmp_path):
    result=m.status(root=tmp_path)
    assert result['status']=='NOT_PREPARED'
