import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from tools.nanojev_addition_curriculum import AdditionNet
from tools.nanojev_zero_line_head_radius_growth import (TRAIN_BANDS, OOD_BANDS, RADII,
    sampled_cards, fold, select, samples_by_sign_delta_band, certify,
    new_batch, evaluate, run, parser)
from tools.nanojev_zero_line_ladder import SCHEMA as LADDER_SCHEMA


def test_disjoint_signed_family_splits():
    for a,b in [(128,-127), (-127,128),(-128,127),(255,-239), (224,-240)]:
        assert fold(a,b,20261025)==fold(b,a,20261025)
        assert fold(a,b,20261025)==fold(-a,-b,20261025)


def test_strata_balanced_no_duplicate_pairs():
    train=sampled_cards(16,TRAIN_BANDS,20261025,'train',12)
    dev=sampled_cards(16,TRAIN_BANDS,20261025,'dev',6)
    ood=sampled_cards(16,OOD_BANDS,20261025,'ood',6)
    assert len({(a,b) for a,b,*_ in train})==len(train)
    assert {tuple(sorted((abs(a),abs(b)))) for a,b,*_ in train}.isdisjoint(
           {tuple(sorted((abs(a),abs(b)))) for a,b,*_ in dev})
    for cards in (train,dev,ood):
        groups=Counter((d,band,sgn) for a,b,d,band,sgn in cards)
        assert len(groups)==33*4*2
        assert all(-16<=d<=16 and a+b==d for a,b,d,_,_ in cards)
    assert min(Counter((d,band,sgn) for a,b,d,band,sgn in dev).values())>=6
    assert all(max(abs(a),abs(b))>=256 for a,b,*_ in ood)


def test_certified_radius_requires_contiguous_positives_and_negatives_and_zero():
    cells={f'{d}:{band}:{orient}':{'rate':1.,'count':6} for d in range(-16,17)
          for band in range(4) for orient in (-1,1)}
    metrics={'max_delta':16,'cells':cells}
    assert certify(metrics,.8)==16
    cells['-9:2:-1']['rate']=.5
    assert certify(metrics,.8)==8
    cells['0:2:-1']['rate']=.5
    assert certify(metrics,.8)==0
    cells['0:2:-1']['rate']=1.
    cells.pop('4:0:1')
    assert certify(metrics,.8)==3


def test_curriculum_sample_rehearses_small_residuals_and_frontier():
    import random
    cards=sampled_cards(16,TRAIN_BANDS,20261025,'train',3)
    b=new_batch(cards,16,90,random.Random(12))
    assert len(b)==90
    assert sum(abs(d)<=2 for _,_,d,_,_ in b)>=30
    assert sum(abs(d)>8 for _,_,d,_,_ in b)>=30


def synthetic_source(tmp_path):
    root=tmp_path/'source'; (root/'rungs').mkdir(parents=True)
    model=AdditionNet('digit_tokens',16,2,1,1,42)
    torch.save({'schema':LADDER_SCHEMA+'.passed','radius':127,'step':4800,
        'report':{'passed':True,'streak':3,'stability_target':3,
                  'mastery':{'groups':{'all':{'accuracy':1.}}}},
        'model':model.state_dict()}, root/'rungs'/'radius_127_passed.pt')
    (root/'config.json').write_text(json.dumps({'schema':LADDER_SCHEMA,'settings':{
        'width':16,'heads':2,'layers':1,'rounds':1,'seed':42}}))
    return root


def test_cpu_end_to_end_probe_and_no_training(tmp_path):
    src=synthetic_source(tmp_path)
    output=tmp_path/'probe'
    args=parser().parse_args(['--phase','probe','--ladder-output',str(src),
        '--output',str(output),'--device','cpu','--max-residual','16',
        '--train-per-cell','1','--dev-per-cell','1','--ood-per-cell','1',
        '--eval-batch-size','256'])
    before=(src/'rungs/radius_127_passed.pt').read_bytes()
    result=run(args)
    assert result['no_training'] and result['test_untouched']
    assert 'certified_radius' in result['baseline_dev']
    assert (src/'rungs/radius_127_passed.pt').read_bytes()==before
    with pytest.raises(FileExistsError):run(args)


def test_cpu_end_to_end_head_only(tmp_path):
    src=synthetic_source(tmp_path)
    output=tmp_path/'train'
    args=parser().parse_args(['--phase','train','--ladder-output',str(src),
        '--output',str(output),'--device','cpu','--max-residual','16',
        '--train-per-cell','1','--dev-per-cell','1','--ood-per-cell','1',
        '--eval-batch-size','512','--steps-per-stage','1','--probe-every','1',
        '--batch-size','12','--cpu-threads','2'])
    before=torch.load(src/'rungs/radius_127_passed.pt',weights_only=True)
    history=run(args)
    assert len(history)==1
    state=torch.load(output/'latest.pt',weights_only=True)
    assert state['stage']==1 and state['step']==0
    after=torch.load(src/'rungs/radius_127_passed.pt',weights_only=True)
    for key in before['model']:
        assert torch.equal(after['model'][key],before['model'][key])
    for key in before['model']:
        if not key.startswith(('sign_head.', 'digit_head.')):
            assert torch.equal(state['model'][key],before['model'][key])
    assert json.loads((output/'result.json').read_text())['test_untouched'] is True
