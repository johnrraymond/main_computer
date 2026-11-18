"""Standalone acceptance for the G2-0060 actor candidate bake-off.

Uses small frozen-capture simulations to exercise actual immutable on-disk
stage transitions without importing/loading Qwen or requiring a GPU.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest
import torch

PATH = Path(__file__).resolve().parents[1]/'tools'/'nanojev_gamer_v1_g2_actor_bakeoff_0060.py'
spec=importlib.util.spec_from_file_location('g2_0060_test_target',PATH)
a60=importlib.util.module_from_spec(spec)
spec.loader.exec_module(a60)


@pytest.mark.parametrize('prompt,actor',[
    ('A Alori Yor chooses from Z, Q.', 'Alori Yor'),
    ('Consider the choices available to the Retreat: A, B.', 'Retreat'),
    ('For the Choice L, available moves are X, Y.', 'Choice L'),
    ('Available choices for the Golgolgol 638 are X, Y.', 'Golgolgol 638'),
    ('The Alquinpal compares plans: X, Y.', 'Alquinpal'),
    ('For a Elngol-Pal, the available projects are A, B.', 'Elngol-Pal'),
    ('A Silver has alternatives X, Y.', 'Silver'),
    ('Project selection for the Nelyorberdra 893: X, Y.', 'Nelyorberdra 893'),
])
def test_all_existing_author_prompt_shapes_are_cue_accessible(prompt,actor):
    prepared={'prompt':prompt,'prompt_start':0,
              'offsets':[(i,i+1) for i in range(len(prompt))],
              'eligible':[True]*len(prompt)}
    found=a60.cue_actors(prompt,prepared)
    assert found==[(prompt.index(actor),prompt.index(actor)+len(actor),'actor')]


def test_cues_do_not_guess_actor_from_arbitrary_text_or_gold_data():
    prompt='This text has a capitalized actor-like phrase, but no participant cue.'
    prepared={'prompt':prompt,'prompt_start':0,'offsets':[(i,i+1) for i in range(len(prompt))]}
    assert a60.cue_actors(prompt,prepared)==[]
    bad={'prompt':'A Sam chooses from X, Y.', 'prompt_start':0,'offsets':[]}
    assert a60.cue_actors(bad['prompt'],bad)==[]


def test_actor_policy_preserves_frozen_parents_and_numeric_evidence(monkeypatch):
    prompt='A Choice L chooses from Move A, Move B.'
    cue=(2,10,'actor')
    parent={'expanded_anchors':[(0,1,'actor')], 'recovered_typed':set()}
    # Exactly six supplemental slots; every actor proposal competes for them.
    new=[(12+i,13+i,'quantity') for i in range(6)]
    provenance={new[i]:{'source':'inference_text_numeric' if i<2 else 'frozen_g1_raw_spatial'}
                for i in range(6)}
    evidence={'anchors':parent['expanded_anchors']+new,'new_anchors':new,
              'typed_edges':set(),'new_endpoint_provenance':provenance}
    p52=types.SimpleNamespace(MAX_ANCHORS=7,
       _validate=lambda raw,par:(torch.zeros(1,1,3),torch.ones(1,1,dtype=torch.bool),
          {'prompt':prompt,'prompt_start':0,
           'offsets':[(i,i+1) for i in range(len(prompt))]}),
       inventory=lambda anchors,allowed:{('owns',tuple(anchors))})
    g=types.SimpleNamespace(p52=p52,_construct_evidence=lambda raw,par:evidence)
    tools=types.ModuleType('tools');contract=types.ModuleType('tools.nanojev_gamer_v1_graph_contract')
    contract.SPAN_KINDS=('actor','quantity')
    graph_head=types.ModuleType('tools.nanojev_gamer_v1_graph_head')
    graph_head.token_span_char_bounds=lambda raw,prepared:(2,10)
    monkeypatch.setitem(sys.modules,'tools',tools)
    monkeypatch.setitem(sys.modules,contract.__name__,contract)
    monkeypatch.setitem(sys.modules,graph_head.__name__,graph_head)
    monkeypatch.setattr(a60,'raw_actor_candidates',lambda raw,par,g:[(cue,3.0)])
    raw={'allowed': [('owns','actor','quantity')]}
    base=a60._actor_reservation(raw,parent,g,arm='evidence')
    assert base is evidence
    cue_arm=a60._actor_reservation(raw,parent,g,arm='actor_cues')
    assert len(cue_arm['anchors'])==7
    assert cue in cue_arm['anchors']
    assert all(a in cue_arm['anchors'] for a in new[:2])
    assert parent['expanded_anchors'][0] in cue_arm['anchors']
    assert len(set(cue_arm['anchors']))==7
    both=a60._actor_reservation(raw,parent,g,arm='hybrid')
    assert cue in both['anchors'] and len(both['anchors'])<=7


def _fake_results(start,stop,*,cue_holdout_loss=False):
    entries=[]
    for idx in range(start,stop):
        for side in range(2):
            scores={}
            for arm in a60.ARMS:
                missing = (1 if arm=='evidence' and idx%8==0 else 0)
                if arm=='actor_quota' and idx%16==0:missing=1
                if arm=='actor_cues' and cue_holdout_loss and idx==start:missing=1
                # Hybrid is a post-hoc alternative; it must not replace locked cue arm.
                scores[arm]={'reachable':missing==0,'missing_edges':missing,
                    'missing_by_relation':{'owns':missing} if missing else {},
                    'regressed_edges':int(arm!='evidence' and missing and idx%8!=0),
                    'recovered_edges':int(arm!='evidence' and idx%8==0 and not missing),
                    'actor_anchor_count':20,'typed_candidate_edges':200 if arm=='hybrid' else 150,
                    'misses':[],'regressed':[],'anchor_count':192}
            entries.append({'index':idx,'pair_id':'p'+str(idx),'side':side,
                'family':('allocation' if idx%4 in (0,1) else 'encounter'),
                'mode':('renaming' if idx%2==0 else 'role_swap'),
                'distractor_count':idx%5,'topology_hash':'t'+str(idx),
                'arms':scores})
    return entries


def _gate_g():
    return types.SimpleNamespace(ROLLOUT_MIN_REACHABILITY=.98,
          ROLLOUT_MIN_CELL_REACHABILITY=.95,ROLLOUT_MIN_CELL_PAIRS=2,
          ROLLOUT_MIN_TOPOLOGIES=4,SHARD_PAIRS=16,
          CELLS=(('allocation','renaming'),('allocation','role_swap'),
                 ('encounter','renaming'),('encounter','role_swap')))


def test_development_scores_include_regressions_and_distractor_strata(monkeypatch):
    monkeypatch.setattr(a60,'DEVELOPMENT',(16,32))
    report=a60._cohort(_gate_g(),_fake_results(16,32),'development')
    assert report['evidence']['fully_reachable_pairs']==14
    assert report['evidence']['acceptance_passed'] is False
    assert report['actor_cues']['fully_reachable_pairs']==16
    assert report['actor_cues']['regressed_gold_edges']==0
    assert report['actor_cues']['acceptance_passed'] is True
    assert a60._select_development(_gate_g(),report)=='actor_cues'
    assert set(report['actor_cues']['cells'])=={'allocation:renaming',
        'allocation:role_swap','encounter:renaming','encounter:role_swap'}


def test_regressions_block_even_when_reachability_passes(monkeypatch):
    monkeypatch.setattr(a60,'DEVELOPMENT',(16,32))
    rows=_fake_results(16,32)
    rows[0]['arms']['actor_cues']['regressed_edges']=1
    report=a60._cohort(_gate_g(),rows,'development')
    assert 'INTRODUCED_MISSING_GOLD_EDGES' in report['actor_cues']['failed_checks']
    assert not report['actor_cues']['acceptance_passed']


def test_saved_shard_hash_cannot_silently_change(tmp_path):
    path=tmp_path/'development-0000016.json'
    data={'schema':a60.SCHEMA,'phase':'development','start':16,
          'entries':[{'index':16,'pair_id':'a','prompt_sha256':'p','side':0}]}
    path.write_text(json.dumps(data))
    digest=path.with_suffix('.sha256.json')
    digest.write_text(json.dumps({'sha256':a60._sha(path)}))
    a60._verify_saved_shard(path,digest,phase='development',index=16,
                            entries=[(16,'a','p',0)])
    path.write_text(json.dumps({**data,'start':17}))
    with pytest.raises(ValueError,match='HASH_MISMATCH'):
        a60._verify_saved_shard(path,digest,phase='development',index=16,
                                entries=[(16,'a','p',0)])


def test_g2_0059_cli_has_new_actor_bakeoff_command():
    core=PATH.with_name('nanojev_gamer_v1_g2_scaling_0059.py').read_text()
    assert "sub.add_parser('actor-bakeoff'" in core
    assert "elif a.phase=='actor-bakeoff':" in core
    assert 'a60.run(' in core


def test_end_to_end_capture_restart_development_lock_and_holdout_no_reselect(monkeypatch,tmp_path):
    """Exercise actual shard files and staged control using a frozen fake hook."""
    monkeypatch.setattr(a60,'DEVELOPMENT',(16,48))
    monkeypatch.setattr(a60,'HOLDOUT',(48,80))
    monkeypatch.setattr(a60,'_source_snapshot',lambda g:{'frozen':'deadbeef'})
    plan=tmp_path/'plan';plan.mkdir()
    (plan/'plan.json').write_text(json.dumps({'seed':17}))
    baseline_dir=tmp_path/'baseline';baseline_dir.mkdir()
    (baseline_dir/'capture.json').write_text('{"candidate_policy":"evidence"}')
    root=tmp_path/'root';root.mkdir()
    out=tmp_path/'bakeoff'
    snapshot={'seed':17,'splits':{'train':{'pairs':80}}}
    calls=[]

    def pair_for(idx):
        mode='renaming' if idx%2==0 else 'role_swap'
        family='allocation' if idx%4 in (0,1) else 'encounter'
        return {'index':idx,'id':'id:'+str(idx),'sha256':[f'hash:{idx}:0',f'hash:{idx}:1'],
                'family':family,'mode':mode,'topology_hash':str(idx),
                'distractor_count':idx%5,
                'rows':({'prompt':f'prompt:{idx}:0'},{'prompt':f'prompt:{idx}:1'})}

    def fake_iter(plan,split,seed,shard_pairs):
        assert split=='train' and shard_pairs==16
        for i in range(0,80,16):
            pairs=[pair_for(idx) for idx in range(i,i+16)]
            yield types.SimpleNamespace(index=i,pairs=pairs,rows=[row for p in pairs for row in p['rows']])

    class Baseline:
        config={'candidate_policy':'evidence'}
        def __init__(self,plan,folder,split,require_complete=False):
            self.pairs=[]
            for start in (16,32):
                for offset in range(16):
                    pair=pair_for(start+offset)
                    rows=[{'side':side,'pair_id':pair['id'],
                           'prompt_sha256':pair['sha256'][side]} for side in (0,1)]
                    self.pairs.append((start,offset,*rows))

    def fake_atomic(path,datum):
        path=Path(path)
        if path.exists():raise AssertionError('output was overwritten')
        path.write_text(json.dumps(datum,sort_keys=True))

    def fake_p40(**kwargs):
        calls.append(kwargs['diagnostic_callback'])
        assert not Path(kwargs['output']).exists()
        def capture_rows(rows,tag,include_raw_g1=False):
            assert include_raw_g1
            return [{'row':dict(row),'candidate_repair':{},'g1_raw_interface':{}} for row in rows]
        kwargs['diagnostic_callback'](capture_candidate_repair_rows=capture_rows,
             assert_all_frozen=lambda:None)

    def fake_entry(g,row,pair,side,raw,parent,baseline=None):
        if pair['index']<48:
            assert baseline is not None and baseline['side']==side
        else:assert baseline is None
        entry=_fake_results(pair['index'],pair['index']+1,
                 cue_holdout_loss=pair['index']==48)[side]
        return {**entry,'pair_id':pair['id'],
                'prompt_sha256':pair['sha256'][side],
                'family':pair['family'],'mode':pair['mode'],
                'distractor_count':pair['distractor_count'],
                'topology_hash':pair['topology_hash']}

    monkeypatch.setattr(a60,'_entry',fake_entry)
    g=types.SimpleNamespace(**vars(_gate_g()),
          __file__=str(PATH.with_name('nanojev_gamer_v1_g2_scaling_0059.py')),
          _require_plan=lambda path:snapshot, FeatureCorpus=Baseline,
          _iter_shards=fake_iter,_capture_parent_preflight=lambda root:{},
          atomic_json=fake_atomic,
          p40=types.SimpleNamespace(run=fake_p40,MAX_LENGTH=512))
    first=a60.run(g,plan,baseline_dir,out,root=root,phase='development',
                  device='cpu',max_shards=1)
    assert first['status']=='IN_PROGRESS' and first['pairs_captured']==16
    assert len(calls)==1
    locked=a60.run(g,plan,baseline_dir,out,root=root,phase='development',device='cpu')
    assert locked['status']=='DEVELOPMENT_LOCKED' and locked['selected_arm']=='actor_cues'
    assert len(calls)==2
    again=a60.run(g,plan,baseline_dir,out,root=root,phase='development',device='cpu')
    assert again==locked and len(calls)==2   # no recalibration/capture on resume
    halfway=a60.run(g,plan,baseline_dir,out,root=root,phase='holdout',
                    device='cpu',max_shards=1)
    assert halfway['status']=='IN_PROGRESS'
    holdout=a60.run(g,plan,baseline_dir,out,root=root,phase='holdout',device='cpu')
    assert holdout['selected_arm']=='actor_cues'
    assert holdout['status']=='HOLDOUT_GATE_FAILED'
    assert holdout['arms']['hybrid']['acceptance_passed']
    assert not holdout['arms']['actor_cues']['acceptance_passed']
    assert json.loads((out/'development-selection.json').read_text())['selected_arm']=='actor_cues'
    assert len(calls)==4


def test_holdout_requires_completed_development(monkeypatch,tmp_path):
    monkeypatch.setattr(a60,'DEVELOPMENT',(16,32))
    monkeypatch.setattr(a60,'HOLDOUT',(32,48))
    monkeypatch.setattr(a60,'_source_snapshot',lambda g:{'frozen':'deadbeef'})
    plan=tmp_path/'plan';plan.mkdir();(plan/'plan.json').write_text('{}')
    baseline_dir=tmp_path/'baseline';baseline_dir.mkdir()
    (baseline_dir/'capture.json').write_text('{}')
    class Baseline:
        config={'candidate_policy':'evidence'}
        pairs=[]
        def __init__(self,*a,**kw):pass
    g=types.SimpleNamespace(_require_plan=lambda path:{'seed':1,'splits':{'train':{'pairs':48}}},
                            FeatureCorpus=Baseline)
    with pytest.raises(ValueError,match='DEVELOPMENT_NOT_FROZEN'):
        a60.run(g,plan,baseline_dir,tmp_path/'out',root=tmp_path,phase='holdout')
