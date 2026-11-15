"""G2-0044: TRAIN-only controlled memorization, gradient and decoder diagnosis."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_g2_memorization_diagnosis as d


def record(monkeypatch,prompt='case-zero'):
    edges=[('a','b','rel'),('a','c','rel'),('d','e','rel'),('d','f','rel')]
    truth={edges[0],edges[2]}
    features={'edges':np.zeros((4,d.x33.EDGE_DIM),dtype=np.float32),
              'groups':np.zeros((2,d.x33.GROUP_DIM),dtype=np.float32),
              'index':np.asarray([0,0,1,1],dtype=np.int64)}
    count=np.full((2,d.x33.COUNT_CLASSES),-50.)
    count[:,1]=0.
    item={'row':{'prompt':prompt,'gold':truth,'split':'train','family':'test'},
          'admitted':True,'features':features,'shortlisted':{'edges':edges},
          'structured_pred':(np.asarray([-4.,4.,-4.,4.]),count,2.),
          'opportunity':{'shortlist_representable':True,'repairable':True}}
    monkeypatch.setattr(d.base,'gold_binding_edges',lambda row:row['gold'])
    policy={'enabled':True,'source':d.g40.SOURCE,'group_weight':.7,
            'global_weight':0.,'fallback':None}
    return item,truth,policy


def test_oracle_and_direct_score_fit_real_decoder(monkeypatch):
    item,gold,policy=record(monkeypatch)
    assert d.direct_gold_oracle(item,policy)
    outcome=d.direct_score_optimization(item,policy,steps=130)
    assert outcome['exact']
    assert outcome['first_exact_step'] is not None




def test_exact_frozen_feature_label_collision_detected(monkeypatch):
    item,_,_=record(monkeypatch)
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    result=d.frozen_edge_identifiability([item],parent)
    assert result['edges']==4
    assert result['conflicting_gold_label_signatures']>0
    assert result['exact_collision_proves_edge_readout_insufficient']

def test_single_case_trainability_gradient_and_parent_frozen(monkeypatch):
    item,gold,policy=record(monkeypatch)
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    frozen={k:v.clone() for k,v in parent.state_dict().items()}
    for kind in d.KINDS:
        model,stats=d.train_memorization([item],parent,kind,policy,steps=2,
                                           phase='single',checkpoint_every=1)
        assert stats['steps_completed']==2
        assert len(stats['checkpoints'])>=3
        assert stats['gradient_probe']['gradients']
        assert all(g['finite'] and g['exists'] for g in
                   stats['gradient_probe']['gradients'].values())
        assert model.predict(item['features'])[0].shape==(4,)
        assert all(torch.equal(v,parent.state_dict()[k]) for k,v in frozen.items())
        for k,v in model.core.state_dict().items():
            change=(k.startswith('edge_readout.') or kind=='joint_count' and
                    (k.startswith('group_readout.') or k.startswith('graph_readout.')))
            if not change:assert torch.equal(v,frozen[k]),k


def test_choose_mini_deterministic_and_disjoint(monkeypatch):
    item,_,_=record(monkeypatch)
    rows=[]
    for i in range(20):
        ex=copy.deepcopy(item);ex['row']['prompt']=f'distinct-{i:03d}'
        rows.append(ex)
    a,b=d.choose_cases(rows,multi=6,heldout=6)
    c,e=d.choose_cases(list(reversed(rows)),multi=6,heldout=6)
    assert [d._sha(r) for r in a]==[d._sha(r) for r in c]
    assert [d._sha(r) for r in b]==[d._sha(r) for r in e]
    assert not set(map(d._sha,a))&set(map(d._sha,b))
    assert len(a)==len(b)==6


def test_never_train_on_dev_or_unreachable(monkeypatch):
    item,_,policy=record(monkeypatch)
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    bad=copy.deepcopy(item);bad['row']['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_GOLD_FORBIDDEN'):
        d.direct_gold_oracle(bad,policy)
    with pytest.raises(ValueError,match='BAD_TRAIN_SPLIT'):
        d.train_memorization([bad],parent,'absolute',policy,steps=1,phase='single')
    with pytest.raises(ValueError,match='DUPLICATE_TRAIN_PROMPT'):
        d.choose_cases([item]*12,multi=6,heldout=6)
    bad=copy.deepcopy(item);bad['opportunity']['repairable']=False
    with pytest.raises(ValueError,match='BAD_TRAIN_SPLIT'):
        d.train_memorization([bad],parent,'absolute',policy,steps=1,phase='single')


def test_parent43_upload_integrity():
    folder=Path('/mnt/data')
    if not (folder/d.b43.REPORT).exists():pytest.skip('uploaded parent not mounted')
    parent=json.loads((folder/d.b43.REPORT).read_text(encoding='utf-8'))
    hashes=parent['parent_hashes']
    g40={k:v for k,v in hashes.items() if k.startswith('g2_0040_')}
    g41={k:v for k,v in hashes.items() if k.startswith('g2_0041_')}
    g42={k:v for k,v in hashes.items() if k.startswith('g2_0042_')}
    result,digests=d.verify_parent43(folder,g40,g41,g42)
    assert result['train_policy']['enabled'] is False
    assert 'g2_0043_weights_sha256' in digests


def test_no_unsafe_decoder_alignment_suppressed(monkeypatch):
    item,_,policy=record(monkeypatch)
    monkeypatch.setattr(d,'_decode',lambda *args:set())
    with pytest.raises(ValueError,match='GOLD_ORACLE_DECODER_ALIGNMENT'):
        d.direct_gold_oracle(item,policy)


def test_interpret_is_bounded():
    assert 'NOT_DEMONSTRATED' in d.interpret({'absolute:single':{'memorized':False},
          'absolute:multi':{'memorized':False}}, {'exact':True})
    assert 'DIRECT_LOGIT' in d.interpret({}, {'exact':False})
    assert 'INTERFERENCE' in d.interpret({'absolute:single':{'memorized':True},
          'absolute:multi':{'memorized':False}}, {'exact':True})


def test_cli_registration():
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-train-memorization-diagnosis','--help'],capture_output=True,
        text=True,check=True)
    assert '--absolute43-run' in proc.stdout
    assert '--single-steps' in proc.stdout
    assert '--multi-steps' in proc.stdout


def test_diagnostic_never_promotes_or_invokes_dev(monkeypatch,tmp_path):
    item,_,policy=record(monkeypatch)
    training=[]
    for i in range(16):
        ex=copy.deepcopy(item)
        ex['row']['prompt']=f'train-{i}'
        training.append(ex)
    fitting=[]
    auditing=[]
    for tag,items in [('fit',fitting),('audit',auditing)]:
        for i in range(3):
            ex=copy.deepcopy(item);ex['row']['prompt']=f'{tag}-{i}'
            items.append(ex)
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    monkeypatch.setattr(d,'direct_score_optimization',lambda *args,**kwargs:{'exact':True})
    monkeypatch.setattr(d.g40,'baseline_selection',lambda rec,arm:set())
    outcome=d.stage2(training,fitting,auditing,policy,parent,lambda:None,
        tmp_path/'results',{'training_sha256':{},'train_dataset_sha256':'abc'},
        {},single_steps=1,multi_steps=1)
    assert outcome['development_evaluation_skipped']
    assert not outcome['production_promotion']
    assert not outcome['train_policy']['enabled']
    assert len(outcome['arm_results'])==6
    assert (tmp_path/'results'/d.REPORT).exists()
    assert (tmp_path/'results'/d.WEIGHTS).exists()
    with pytest.raises(FileExistsError):
        d.stage2(training,fitting,auditing,policy,parent,lambda:None,
            tmp_path/'results',{'training_sha256':{},'train_dataset_sha256':'abc'},
            {},single_steps=1,multi_steps=1)
