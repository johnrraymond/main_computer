"""0101: source-frozen, class-specific calibration and sealed-holdout acceptance."""
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g2_relationship_admission_0101 as m
from tools import nanojev_gamer_v1_g2_admission_constraints_0100 as prior
from tools import nanojev_gamer_v1_g2_mit_mechanism_0099 as previous
from tests.test_nanojev_gamer_v1_g2_admission_constraints_0100 import (
    _stage, surface, edges, SPEC)


def _case(gold, i, *, a=.3, b=.8, spec=None):
    spec = spec or SPEC
    s = surface(a=a, b=b)
    return {'surface':s, 'family':'allocation' if i%2==0 else 'encounter',
            'gold':[m.source.json_edge(e) for e in gold],
            'baseline':[m.source.json_edge(e) for e in sorted(m.bakeoff.decode(s,spec))]}


def test_class_threshold_affects_only_named_label_and_does_not_mutate_input():
    spec = dict(SPEC)
    spec['out_caps']={'quantity|action|applies_to':2}
    original = json.dumps(spec,sort_keys=True)
    s = surface(a=.3,b=.8)
    zeros = {lab:0.0 for lab in m.LABELS}
    assert m.decode_labels(s,spec,zeros) == {edges()[1]}
    offsets = dict(zeros); offsets['applies_to'] = -0.25
    assert m.decode_labels(s,spec,offsets) == set(edges())
    unrelated=dict(zeros); unrelated['owns']=-1.0
    assert m.decode_labels(s,spec,unrelated) == {edges()[1]}
    assert json.dumps(spec,sort_keys=True) == original
    assert m.decoder(s,spec,'global_0100',zeros) == set()


def test_separate_discovery_selection_and_replication_protection_veto():
    spec=dict(SPEC);spec['out_caps']={'quantity|action|applies_to':2}
    records=[_case(set(edges()),i,spec=spec) for i in range(120)]
    r=m.calibrate(records,spec)
    assert r['frozen_offsets']['applies_to'] in (-1.0,-0.5,-0.25)
    assert r['joint_safe'] is True
    assert all(v==0 for k,v in r['frozen_offsets'].items() if k!='applies_to')
    # Keep discovery unchanged while inverting the audit data: model must NOT
    # retune to a second runner-up after a protected holdout veto.
    for i in range(120):
        if previous.group_of(i) != 'discovery':
            records[i]['gold']=[m.source.json_edge(edges()[1])]
    v=m.calibrate(records,spec)
    assert v['class_decisions']['applies_to']['discovery_selected'] == r['class_decisions']['applies_to']['discovery_selected']
    assert v['class_decisions']['applies_to']['accepted'] == 0.0
    assert v['frozen_offsets']['applies_to'] == 0.0
    assert v['joint_safe'] is False


def test_fresh_case_generation_excludes_0100_without_scanning_tree(tmp_path, monkeypatch):
    old_hashes=set()
    # 0100 campaign is explicitly checked even though new seed differs.
    repo=tmp_path/'reports';repo.mkdir()
    original=repo/prior.NAME/'seal'/'prompts.jsonl'
    original.parent.mkdir(parents=True)
    original.write_text(json.dumps({'prompt_sha256':m.digest('old-prompt')})+'\n')
    # Make fake dataset independent of 0100, preserve real corpus generator.
    monkeypatch.setattr(m.previous.cur,'load_rows',lambda path:[])
    prompts,answers,evidence=m._fresh_cohort(repo,tmp_path/'fake.jsonl',40)
    assert len(prompts)==len(answers)==40
    assert len({row['prompt_sha256'] for row in prompts})==40
    assert m.digest('old-prompt') not in {row['prompt_sha256'] for row in prompts}
    assert evidence[prior.NAME] == m.sha(original)
    assert {row['family'] for row in prompts} == {'allocation','encounter'}


def _stage_0101(tmp_path,monkeypatch):
    root, source_dir, head_sha = _stage(tmp_path,monkeypatch)
    monkeypatch.setattr(m,'_used_prompt_hashes',lambda root,dataset: (set(),{}))
    def fake_cases(root,dataset,count):
        public=[{'case_id':f'g2-0101-{i:04d}',
                 'family':'allocation' if i%2==0 else 'encounter',
                 'prompt':f'0101-unique-{i}',
                 'prompt_sha256':m.digest(f'0101-unique-{i}')}
                for i in range(count)]
        secret=[{'case_id':r['case_id'],'family':r['family'],
                 'prompt_sha256':r['prompt_sha256'],'graph':{}} for r in public]
        return public,secret,{}
    monkeypatch.setattr(m,'_fresh_cohort',fake_cases)
    return root, source_dir, head_sha


def test_complete_gold_blind_lifecycle_with_frozen_weights(tmp_path,monkeypatch):
    root, source_dir, head_hash = _stage_0101(tmp_path,monkeypatch)
    c=m.prepare(root=root,cases=40)
    dest=m.location(root)
    assert c['source']['head_sha256']==head_hash
    assert c['gradient_steps']==0 and c['weight_edits']==0
    assert set(c['offsets'])==set(m.LABELS)
    secret=dest/'seal'/'answers.SECRET.jsonl'
    hidden=dest/'seal'/'answers.HIDDEN'
    secret.rename(hidden)
    lock=m.predict(root=root,device='cpu')
    assert lock['gold_accessed'] is False
    hidden.rename(secret)
    report=m.score(root=root)
    assert report['source_unchanged'] is True
    assert report['weight_edits']==0 and report['production_promotion'] is False
    assert report['metrics']['overall']['baseline']['cases']==40
    assert report['metrics']['overall']['class_calibrated']['cases']==40
    assert report['metrics']['overall']['global_0100']['cases']==40
    assert m.run(root=root,device='cpu')['source_unchanged'] is True
    assert m.sha(source_dir/previous.cur.HEAD_FILE)==head_hash
    assert list(root.rglob('gamer_graph_head.safetensors'))==[source_dir/previous.cur.HEAD_FILE]


def test_factors_drift_is_rejected(tmp_path,monkeypatch):
    root,_,_=_stage_0101(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    dest=m.location(root)
    c=m.read(dest/'seal'/'commit.json')
    c['offsets']['applies_to']=1.5
    m.write(dest/'seal'/'commit.json',c)
    with pytest.raises(ValueError,match='FROZEN_CLASS_SETTINGS_DRIFT'):
        m.predict(root=root,device='cpu')


def test_blind_shard_tampering_is_rejected(tmp_path,monkeypatch):
    root,_,_=_stage_0101(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    m.predict(root=root,device='cpu')
    dest=m.location(root)
    shard=dest/'blind'/'shards'/'g2-0101-0000.json.gz'
    data=prior._gzip_read(shard)
    data['surface']['pair_margins'][0][2][3]=500
    prior._gzip_write(shard,data)
    with pytest.raises(ValueError,match='BLIND_SHARD_SHA256_DRIFT'):
        m.score(root=root)


def test_resume_keeps_first_shard_unchanged(tmp_path,monkeypatch):
    root,_,_=_stage_0101(tmp_path,monkeypatch)
    c=m.prepare(root=root,cases=40)
    dest=m.location(root)
    secret=dest/'seal'/'answers.SECRET.jsonl'
    hidden=dest/'seal'/'answers.HIDDEN';secret.rename(hidden)
    shards=dest/'blind'/'shards';shards.mkdir(parents=True)
    s=surface(a=.3,b=.8)
    obj={'case_id':'g2-0101-0000','family':'allocation',
         'prompt_sha256':m.digest('0101-unique-0'),'surface':s,
         'predictions':{a:[m.source.json_edge(e) for e in sorted(m.decoder(
                s,c['decoder_spec'],a,c['offsets']))] for a in m.ARMS}}
    prior._gzip_write(shards/'g2-0101-0000.json.gz',obj)
    original=m.sha(shards/'g2-0101-0000.json.gz')
    m.predict(root=root,device='cpu')
    assert original==m.sha(shards/'g2-0101-0000.json.gz')
    hidden.rename(secret)
    assert m.score(root=root)['metrics']['overall']['baseline']['cases']==40


def test_no_repromotion_after_0100_sample_and_safety_gate(tmp_path,monkeypatch):
    root,_,_=_stage_0101(tmp_path,monkeypatch)
    m.prepare(root=root,cases=40)
    m.predict(root=root,device='cpu')
    report=m.score(root=root)
    assert not report['gate']
    assert report['g3_unlocked'] is False
    assert report['production_promotion'] is False
