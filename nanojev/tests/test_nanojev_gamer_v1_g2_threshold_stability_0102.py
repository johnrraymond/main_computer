"""G2-0102: independent fold audit, read-only sealed challenge, source integrity."""
import json
import pytest

from tools import nanojev_gamer_v1_g2_threshold_stability_0102 as m
from tools import nanojev_gamer_v1_g2_admission_constraints_0100 as old
from tests.test_nanojev_gamer_v1_g2_admission_constraints_0100 import _stage, surface, edges


def _setup(tmp_path,monkeypatch):
    root,source_dir,source_sha=_stage(tmp_path,monkeypatch)
    def cohorts(root,dataset,train_cases,holdout_cases):
        data={}
        for split,count in [('train',train_cases),('holdout',holdout_cases)]:
            pub,ans=[],[]
            for i in range(count):
                label=f'unique-{split}-{i}'
                cid=f'g2-0102-{split}-{i:04d}'
                family='allocation' if i%2==0 else 'encounter'
                pub.append({'case_id':cid,'family':family,'prompt':label,
                            'prompt_sha256':m.digest(label),
                            'fold':m.FOLD_NAMES[(i//2)%6] if split=='train' else None})
                ans.append({'case_id':cid,'family':family,'prompt_sha256':m.digest(label),
                            'graph':{}})
            data[split]=(pub,ans)
        return data, {}
    monkeypatch.setattr(m,'_cohort',cohorts)
    monkeypatch.setattr(m,'_prompt_hashes',lambda root,dataset:(set(),{}))
    return root,source_dir,source_sha


def test_grid_and_fold_balancing():
    assert [m.key(x) for x in m.LEVELS] == [
        '-0.50','-0.25','0.00','0.25','0.50','0.75','1.00','1.25','1.50','2.00']
    assert [m.FOLD_NAMES[(i//2)%6] for i in range(12)] == list(sum(([x,x] for x in m.FOLD_NAMES), []))
    with pytest.raises(ValueError,match='LEVEL_OUT_OF_GRID'):
        m.key(5)


def test_discovery_only_and_audit_not_reselection(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,train_cases=48,cases=40)
    m.capture(root=root,device='cpu')
    original=m.calibrate(root=root)
    best=original['discovery_selected_offset']
    audit=m.read(m.location(root)/'train_evidence.json')['audits']['1.00']
    assert audit['audit_folds']==4
    assert audit['unanimous_fold_rule_would_veto'] in (True,False)
    assert original['predeclared_primary_offset']==1.0
    # Gate is diagnostic only; source weights are not copied or altered.
    assert best in m.LEVELS
    assert original['weight_edits']==0


def test_complete_holdout_gold_unavailable_until_scoring(tmp_path,monkeypatch):
    root,src,source_hash=_setup(tmp_path,monkeypatch)
    prepared=m.prepare(root=root,train_cases=48,cases=40)
    m.capture(root=root,device='cpu')
    frozen=m.calibrate(root=root)
    secret=m.location(root)/'seal'/'holdout_answers.SECRET.jsonl'
    hidden=secret.with_suffix('.hidden')
    secret.rename(hidden)
    locked=m.predict(root=root,device='cpu')
    assert locked['gold_accessed'] is False
    assert locked['factors_sha256']==m.sha(m.location(root)/'seal'/'factors.json')
    hidden.rename(secret)
    report=m.score(root=root)
    assert report['cases']==40 and report['train_cases']==48
    assert report['source_unchanged'] is True
    assert report['production_promotion'] is False
    assert report['weight_edits']==0 and report['gradient_steps']==0
    assert set(report['blind_threshold_curve'])=={m.key(v) for v in m.LEVELS}
    assert m.sha(src/m.previous.previous.cur.HEAD_FILE)==source_hash
    assert m.run(root=root,device='cpu')['source_unchanged'] is True


def test_freeze_and_shard_tamper_fail_closed(tmp_path,monkeypatch):
    root,_,_=_setup(tmp_path,monkeypatch)
    m.prepare(root=root,train_cases=48,cases=40)
    m.capture(root=root,device='cpu')
    m.calibrate(root=root)
    m.predict(root=root,device='cpu')
    dest=m.location(root)
    bad=dest/'holdout'/'shards'/'g2-0102-holdout-0000.json.gz'
    obj=old._gzip_read(bad)
    obj['surface']['pair_margins'][0][2][3]=777
    old._gzip_write(bad,obj)
    with pytest.raises(ValueError,match='SHARD_HASH_DRIFT'):
        m.score(root=root)
    freeze=dest/'seal'/'factors.json'
    obj=m.read(freeze); obj['discovery_selected_offset']=2.0
    m.write(freeze,obj)
    with pytest.raises(ValueError,match='SELECTED_FACTOR_DRIFT|FROZEN_FACTOR_DRIFT'):
        m.score(root=root)


def test_actual_generator_excludes_prior_prompts(tmp_path,monkeypatch):
    root=tmp_path/'gamer-v1';root.mkdir()
    campaign=root/old.NAME/'seal';campaign.mkdir(parents=True)
    m.write_rows(campaign/'prompts.jsonl',[{'prompt':'prior','prompt_sha256':m.digest('prior')}])
    monkeypatch.setattr(m.previous.previous.cur,'load_rows',lambda path:[])
    cohorts,evidence=m._cohort(root,tmp_path/'dummy.jsonl',48,40)
    train=cohorts['train'][0];holdout=cohorts['holdout'][0]
    keys=[r['prompt_sha256'] for r in train+holdout]
    assert len(keys)==len(set(keys))==88
    assert m.digest('prior') not in keys
    assert set(r['family'] for r in train)=={'allocation','encounter'}
    assert old.NAME in evidence


def test_stability_classifies_clear_training_regression():
    records=[]
    for i in range(48):
        gold=set(edges())
        b={edges()[1]}
        # +1 suppresses the only correct admitted edge.
        p={m.key(v): b if v==0 else set() for v in m.LEVELS}
        records.append({'fold':m.FOLD_NAMES[(i//2)%6],
                        'family':'allocation' if i%2==0 else 'encounter',
                        'gold':gold,'sets':p})
    result=m._stability(records,1.)
    assert result['status']=='TRAIN_SIGNAL_UNSTABLE'
    assert result['unanimous_fold_rule_would_veto']
    assert result['pooled_support_rule_passes'] is False
