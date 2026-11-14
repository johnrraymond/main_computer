"""G2-0008 train-only learned graph context and strict lineage smoke."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_learned_graph_context as m
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from test_nanojev_gamer_v1_g2_contextual_graph import _fixture as parent_fixture


def test_cli_registered():
    got=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                        'frob-g2-learned-graph-context','--help'],
                       cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=30)
    assert got.returncode==0,got.stderr
    assert '--context-run' in got.stdout and '--steps' in got.stdout


def test_fresh_cohort_excludes_every_predecessor():
    train=[encounter(random.Random(10),'train',1),allocation(random.Random(11),'train',2)]
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=rel.new_validation_rows(train,first,second,third,limit=4)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=4)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=4)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=4)
    fresh=m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=8)
    assert Counter(x['family'] for x in fresh)=={'encounter':4,'allocation':4}
    assert set(base._sha(x['prompt']) for x in fresh).isdisjoint(
        set(base._sha(x['prompt']) for x in train+first+second+third+fourth+fifth+sixth+seventh))
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=3)


def test_candidate_never_evidences_itself_but_multiple_edges_survive():
    a=(0,2,'actor');b=(3,5,'action');c=(6,8,'action')
    anchors=[a,b,c]
    vectors=torch.arange(12,dtype=torch.float32).view(3,4)/8
    parent={(a,b,'owns'),(a,c,'owns')}
    feats=[(a,b,'owns',.9,2.197224577,2,0.),(a,c,'owns',.8,1.38629436,2,.811)]
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    matrix,odds,edges=m.graph_features(anchors,vectors,feats,parent,rule)
    assert matrix.shape==(2,6*4+m.EXTRA) and len(edges)==2
    # src-out context for a->b has only neighbor c; for a->c only b.
    assert torch.allclose(matrix[0,8:12],vectors[2])
    assert torch.allclose(matrix[1,8:12],vectors[1])
    absent,_,_=m.graph_features(anchors,vectors,feats,parent,rule,with_context=False)
    assert torch.count_nonzero(absent[:,8:8+4*4])==0
    assert torch.allclose(matrix[:,:8],absent[:,:8])
    assert torch.allclose(odds,torch.tensor([2.197224577,1.38629436]))
    with pytest.raises(ValueError,match='BAD_NODES'):
        m.graph_features([a,a,c],vectors,feats,parent,rule)


def test_train_context_only_updates_context_parameters():
    actor=(1,3,'actor');left=(4,7,'action');right=(8,10,'action')
    vectors=torch.randn(3,8,generator=torch.Generator().manual_seed(44))
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    feats=[(actor,left,'owns',.9,2.1,2,0.),(actor,right,'owns',.7,.85,2,1.25)]
    context,base_odds,edges=m.graph_features([actor,left,right],vectors,feats,set(),rule)
    case={'features':context,'base_scores':base_odds,'edges':edges,'gold':{edges[1]}}
    torch.manual_seed(1);model=m.GraphContextScorer(8)
    before=model.network[-1].weight.detach().clone()
    info=m.train_context(model,[case],steps=8,batch=2,device='cpu')
    assert info['samples']['positive']==1 and info['samples']['negative']==1
    assert not torch.equal(before,model.network[-1].weight)
    assert all(not p.requires_grad for p in model.parameters())
    with pytest.raises(ValueError,match='BAD_TRAIN_STEPS'):
        m.train_context(model,[case],steps=0)


def test_calibration_rule_no_validation_peek():
    calibration={m.PARENT:{'recall':.64,'f1':.42,'false_positive':1250}}
    for t in m.THRESHOLDS:
        calibration[f'learned_p{round(t*100):02d}']={'recall':.62,'f1':.41,'false_positive':1200}
    calibration['learned_p50']={'recall':.63,'f1':.45,'false_positive':1100}
    chosen,info=m.choose_rule(calibration)
    assert chosen=='learned_p50' and info['eligible_count']==1
    calibration['learned_p50']['recall']=.59
    assert m.choose_rule(calibration)[0] is None


def _fixture(monkeypatch,tmp_path):
    args,seen=parent_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(context7,'VALIDATION_LIMIT',4)
    context_dir=tmp_path/'context-run'
    context7.run(**{**args,'output':context_dir})
    return {**{key:args[key] for key in ('root','g1_validation_report','g2_run',
        'selection_run','discrimination_run','relational_run','density_run','consistency_run')},
        'context_run':context_dir,'output':tmp_path/'learned-run',
        'device':'cpu','validation_limit':4,'steps':4},seen


def test_mocked_full_runner_lineage_frozen_and_immutable(monkeypatch,tmp_path):
    args,seen=_fixture(monkeypatch,tmp_path)
    result=m.run(**args)
    assert result['g1_frozen'] and result['g2_0007_replay_pass']
    assert result['g2_0004_rank_weights_frozen'] and result['g2_0005_rule_frozen']
    assert result['train_info']['steps']==4
    assert result['g2_context_trainable_parameters']>0
    assert result['independent_cases']==4 and result['calibration_cases']==4
    assert not result['production_promotion'] and not result['graph_execution_authorized']
    assert set(result['independent_metrics'])=={m.PARENT,
        *(f'learned_p{round(t*100):02d}' for t in m.THRESHOLDS),
        *(f'no_neighborhood_p{round(t*100):02d}' for t in m.THRESHOLDS)}
    assert result['control_train_info']['feature_name']=='without_context'
    assert len((args['output']/m.CASES).read_text().splitlines())==8
    assert (args['output']/m.WEIGHTS).is_file()
    assert seen[-3:]==['train','g2_0007_replay','new_independent']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)


def test_tampered_parent_metrics_rejected_before_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['context_run']/context7.REPORT
    report=json.loads(path.read_text());report['independent_metrics'][m.PARENT]['correct']+=1
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0007_METRICS'):
        m.run(**args)
    assert not args['output'].exists()


def test_parent_promoted_in_violation_of_contract_rejected(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['context_run']/context7.REPORT
    report=json.loads(path.read_text());report['selected_rule']='motif_bonus_025'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0007_CONTRACT:selected_rule'):
        m.run(**args)
    assert not args['output'].exists()
