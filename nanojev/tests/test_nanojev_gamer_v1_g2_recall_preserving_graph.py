"""G2-0009 recall-weighted graph retention, parent replay, and leakage gates."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_recall_preserving_graph as m
from tools import nanojev_gamer_v1_g2_learned_graph_context as learned8
from tools import nanojev_gamer_v1_g2_contextual_graph as context7
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_binding_discrimination as disc
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_graph_consistency as graph6
from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
from test_nanojev_gamer_v1_g2_learned_graph_context import _fixture as prior_fixture


def test_cli_registered():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
                           'frob-g2-recall-preserving-graph','--help'],
                          cwd=Path(__file__).resolve().parents[1],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert '--learned-run' in result.stdout and '--steps' in result.stdout


def test_ninth_cohort_is_prompt_disjoint():
    train=[encounter(random.Random(10),'train',1),allocation(random.Random(11),'train',2)]
    first=base.novel_dev_rows(train,limit=4,seed=base.SEED)
    second=sel.new_validation_rows(train,first,limit=4)
    third=disc.new_validation_rows(train,first,second,limit=4)
    fourth=rel.new_validation_rows(train,first,second,third,limit=4)
    fifth=density.new_validation_rows(train,first,second,third,fourth,limit=4)
    sixth=graph6.new_validation_rows(train,first,second,third,fourth,fifth,limit=4)
    seventh=context7.new_validation_rows(train,first,second,third,fourth,fifth,sixth,limit=4)
    eighth=learned8.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,limit=4)
    ninth=m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=8)
    assert Counter(x['family'] for x in ninth)=={'encounter':4,'allocation':4}
    earlier=train+first+second+third+fourth+fifth+sixth+seventh+eighth
    assert set(base._sha(row['prompt']) for row in ninth).isdisjoint(
        set(base._sha(row['prompt']) for row in earlier))
    with pytest.raises(ValueError,match='BAD_COHORT'):
        m.new_validation_rows(train,first,second,third,fourth,fifth,sixth,seventh,eighth,limit=3)


def _toy():
    a=(0,2,'actor');b=(3,5,'action');c=(6,8,'action')
    x=torch.tensor([[-1.,0.,0.,1.],[0.,1.,0.,1.],[1.,0.,1.,0.]])
    feats=[(a,b,'owns',.9,2.19722,2,0.),(a,c,'owns',.8,1.38629,2,.81)]
    rule={'minimum_confidence':.5,'density_penalty':0.,'competition_penalty':0.}
    mat,odds,edges=learned8.graph_features([a,b,c],x,feats,set(),rule)
    absent,_,_=learned8.graph_features([a,b,c],x,feats,set(),rule,with_context=False)
    return {'index':1,'prompt_sha256':'abc','family':'allocation','anchors':[a,b,c],
            'gold':{edges[1]},'parent':set(edges),'features':mat,
            'without_context':absent,'base_scores':odds,
            'prior':odds-.2,'control_prior':odds-.1,'retention_edges':edges}


def test_scorer_starts_from_parent_and_training_preserves_input():
    case=_toy()
    torch.manual_seed(31)
    model=m.RetentionScorer(4)
    original=case['features'].clone()
    logits=model(case['features'],case['base_scores'],case['prior'])
    assert torch.allclose(logits,case['base_scores'])
    info=m.train_retention(model,[case],positive_weight=8.,steps=8,batch=2,device='cpu')
    assert info['samples']=={'positive':1,'negative':1}
    assert torch.equal(original,case['features'])
    assert all(not parameter.requires_grad for parameter in model.parameters())
    with pytest.raises(ValueError,match='BAD_TRAIN_CONFIG'):
        m.train_retention(model,[case],positive_weight=1.,steps=4)
    with pytest.raises(ValueError,match='BAD_MODEL_INPUT'):
        model(case['features'][:1],case['base_scores'],case['prior'])


def test_filter_only_no_new_edges_and_high_cut_reduces_recall():
    case=_toy();torch.manual_seed(12)
    model=m.RetentionScorer(4)
    _,rows=m.evaluate([case],model,model,model,model,device='cpu',group='test')
    expected=rows[0]['configurations']
    assert expected[m.PARENT]['correct']==1
    assert expected['retain_w04_p10']==expected[m.PARENT]
    # Every selected subset is a subset of parent; missing never decreases.
    for name,metrics in expected.items():
        assert metrics['correct']<=expected[m.PARENT]['correct']
        assert metrics['false_positive']<=expected[m.PARENT]['false_positive']
        assert metrics['missing']>=expected[m.PARENT]['missing']


def test_recall_loss_is_strict_and_selected_from_calibration_only():
    cal={m.PARENT:{'recall':.63,'false_positive':100,'f1':.42}}
    for key in m._rule_names():
        cal[key]={'recall':.62,'false_positive':90,'f1':.40}
    cal['retain_w08_p20']={'recall':.622,'false_positive':80,'f1':.44}
    assert m.choose_rule(cal)[0]=='retain_w08_p20'
    cal['retain_w08_p20']['recall']=.619
    assert m.choose_rule(cal)[0] is None
    # A seemingly better F1 never makes a rule eligible if recall collapsed.
    cal['retain_w04_p10']={'recall':.59,'false_positive':1,'f1':.90}
    assert m.choose_rule(cal)[0] is None


def _fixture(monkeypatch,tmp_path):
    args,seen=prior_fixture(monkeypatch,tmp_path)
    monkeypatch.setattr(learned8,'VALIDATION_LIMIT',4)
    # The tiny mocked G1/G2 fixture can select zero parent edges. Provide two
    # synthetic TRAIN edges for exercising the trainer; DEV is never modified.
    original_encode=m.encode_retention
    def encode_with_train_example(cases,context,control,*,device,group):
        encoded=original_encode(cases,context,control,device=device,group=group)
        if group=='train' and not any(e in row['gold'] for row in encoded
                                     for e in row['retention_edges']):
            width=encoded[0]['features'].shape[1]
            sample={'retention_edges':[('a','b','owns'),('a','c','owns')],
                    'gold':{('a','b','owns')},'features':torch.ones(2,width),
                    'without_context':torch.zeros(2,width),
                    'base_scores':torch.tensor([.1,.3]),
                    'prior':torch.tensor([.1,.3]),
                    'control_prior':torch.tensor([.1,.3])}
            encoded.append(sample)
        return encoded
    monkeypatch.setattr(m,'encode_retention',encode_with_train_example)
    parent=tmp_path/'learned-run'
    learned8.run(**{**args,'output':parent})
    run_args={key:args[key] for key in ('root','g1_validation_report','g2_run','selection_run',
                                       'discrimination_run','relational_run','density_run',
                                       'consistency_run','context_run')}
    run_args.update(learned_run=parent,output=tmp_path/'recall-run',device='cpu',
                    validation_limit=4,steps=4)
    return run_args,seen


def test_parent_verification_and_mocked_complete_runner(monkeypatch,tmp_path):
    args,seen=_fixture(monkeypatch,tmp_path)
    result=m.run(**args)
    assert result['g1_frozen'] and result['g2_0008_replay_pass']
    assert result['g2_0004_rank_weights_frozen'] and result['g2_0005_rule_frozen']
    assert result['trainable_parameters_per_arm']>0
    assert result['training_arm_w04']['positive_retention_weight']==4.
    assert result['training_arm_w08']['positive_retention_weight']==8.
    assert result['training_control_no_context_w04']['feature_name']=='without_context'
    assert result['training_control_no_context_w08']['feature_name']=='without_context'
    assert result['independent_cases']==4 and result['calibration_cases']==4
    assert not result['production_promotion'] and not result['graph_execution_authorized']
    assert not result['g2_production_unlocked'] and result['no_new_edges_allowed']
    assert set(result['independent_metrics'])=={m.PARENT,*m._rule_names(),
        *(f'no_context_w{w:02d}_p{round(t*100):02d}' for w in (4,8) for t in m.THRESHOLDS)}
    assert len((args['output']/m.CASES).read_text().splitlines())==8
    assert (args['output']/m.WEIGHTS).is_file()
    assert seen[-3:]==['train','g2_0008_replay','new_independent']
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)


def test_corrupted_parent_metrics_rejected_before_output(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['learned_run']/learned8.REPORT
    report=json.loads(path.read_text())
    report['independent_metrics'][m.PARENT]['correct']+=1
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0008_METRICS'):
        m.run(**args)
    assert not args['output'].exists()


def test_illicit_parent_promotion_rejected(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    path=args['learned_run']/learned8.REPORT
    report=json.loads(path.read_text())
    report['selected_rule']='learned_p40'
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='G2_0008_CONTRACT:selected_rule'):
        m.run(**args)
    assert not args['output'].exists()


def test_checkpoint_tampering_rejected(monkeypatch,tmp_path):
    args,_=_fixture(monkeypatch,tmp_path)
    weights=args['learned_run']/learned8.WEIGHTS
    with weights.open('ab') as stream:stream.write(b'corruption')
    with pytest.raises(ValueError,match='G2_0008_WEIGHTS_DRIFT'):
        m.run(**args)
    assert not args['output'].exists()
