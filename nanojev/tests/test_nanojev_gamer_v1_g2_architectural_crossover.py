"""G2-0047 acceptance: valid architectural discrimination, immutable diagnostics."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_architectural_crossover as d
from tests.test_nanojev_gamer_v1_g2_multiprompt_discrimination import scenario


def fixture(monkeypatch,count=28):
    records,parent,p45,policy=scenario(monkeypatch,count=count)
    fitting=copy.deepcopy(records[:2]);auditing=copy.deepcopy(records[:2])
    for i,r in enumerate(fitting):r['row']['prompt']=f'crossover-fit-{i}'
    for i,r in enumerate(auditing):r['row']['prompt']=f'crossover-audit-{i}'
    cohort=d.d46.select_cohorts(records,fitting,auditing,p45,policy)
    p46={'cohorts':{
        'train_learn_prompts':[d.d46._sha(r) for r in cohort['learn']],
        'transfer_prompts':[d.d46._sha(r) for r in cohort['transfer']],
        'train_fit_prompts':[d.d46._sha(r) for r in cohort['train_fit']],
        'natural_train_audit_prompts':[d.d46._sha(r) for r in cohort['natural_train_audit']],
        'fixed_g2_0045_transfer_prompts':cohort['fixed_transfer_prompts'],
        'excluded_train_prompts':cohort['excluded_train_prompts'],
        'train_source_count':count,
    },
    'results':{'arms':{'nonlinear:bce':{name:{} for name in (
              'learn','transfer','train_fit','natural_train_audit')}}},
    'train_dataset_sha256':'synthetic','training_sha256':{}}
    return records,fitting,auditing,parent,p45,p46,policy,cohort


def test_trainable_representation_starts_at_exact_frozen_parent(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    cache=d.prepare_features(records[:1],parent)
    sample=next(iter(cache.values()))
    prior={name:val.detach().clone() for name,val in parent.state_dict().items()}
    torch.manual_seed(d.SEED+100)
    frozen=d.CrossoverScorer(parent,'frozen')
    torch.manual_seed(d.SEED+100)
    joint=d.CrossoverScorer(parent,'joint')
    with torch.inference_mode():
        score_frozen=frozen(sample)
        score_joint=joint(sample)
    assert torch.allclose(score_frozen,sample['parent_score'],atol=2e-4,rtol=2e-4)
    assert torch.allclose(score_joint,score_frozen,atol=2e-5,rtol=2e-5)
    assert len(frozen.representation_params())==0
    assert len(joint.representation_params())>0
    assert all(torch.equal(parent.state_dict()[name],val) for name,val in prior.items())


def test_gradients_reach_private_relational_representation(monkeypatch):
    records,_,_,parent,*_=fixture(monkeypatch)
    with torch.no_grad():
        parent.edge_readout.weight.fill_(0.1)
    cache=d.prepare_features(records[:1],parent)
    item=next(iter(cache.values()))
    joint=d.CrossoverScorer(parent,'joint')
    score=joint(item)
    # Exact zero-initialized correction guarantees parent-score starting point,
    # but its gradient and the parent-readout path train feature layers.
    loss=score.sum()
    loss.backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all() and p.grad.abs().sum()>0
               for p in joint.representation_params())
    assert all(p.grad is None for p in parent.parameters())
    assert all(p.grad is None for p in joint.readout.base.parameters())


def test_structured_margin_refuses_dev_and_is_differentiable(monkeypatch):
    records,_,_,parent,*rest=fixture(monkeypatch)
    policy=rest[-2]
    rec=records[0]
    gold=d.d46._gold(rec)
    truth=torch.tensor([e in gold for e in rec['shortlisted']['edges']],dtype=torch.float32)
    scores=torch.tensor([-10.,10.,-10.,10.],requires_grad=True)
    loss=d.structured_margin(scores,rec,truth,policy)
    assert torch.isfinite(loss) and float(loss.detach())>0
    loss.backward()
    assert scores.grad is not None and torch.isfinite(scores.grad).all()
    invalid=copy.deepcopy(rec);invalid['row']['split']='dev'
    with pytest.raises(ValueError,match='STRUCTURED_DEV_REFUSED'):
        d.structured_margin(scores,invalid,truth,policy)


def test_predeclared_cohort_cannot_change(monkeypatch):
    records,fit,audit,_,p45,p46,policy,cohort=fixture(monkeypatch)
    d.verify_cohorts(cohort,p46)
    p46['cohorts']['train_learn_prompts'].reverse()
    with pytest.raises(ValueError,match='PARENT_COHORT_DRIFT'):
        d.verify_cohorts(cohort,p46)


def test_all_four_arms_have_finite_train_graphs_and_parent_is_unchanged(monkeypatch):
    records,fit,audit,parent,p45,p46,policy,cohort=fixture(monkeypatch)
    train=cohort['learn'][:5]
    cache=d.prepare_features(train,parent)
    supervision=d.d46.prepare_supervision(train,parent)
    frozen_state={k:v.clone() for k,v in parent.state_dict().items()}
    for representation in d.REPRESENTATIONS:
        for objective in d.OBJECTIVES:
            model,summary=d.train_arm(train,parent,policy,representation,objective,
                cache,supervision,steps=2,batch_size=3,eval_every=1,structured_per_batch=1)
            assert summary['steps_completed']==2
            assert summary['train_final']['all']['cases']==len(train)
            assert all(not parameter.requires_grad for parameter in model.parameters())
            assert all(torch.equal(parent.state_dict()[key],old)
                       for key,old in frozen_state.items())
    with pytest.raises(ValueError,match='BAD_ARM_OR_BUDGET'):
        d.train_arm(train,parent,policy,'frozen','bce',cache,supervision,steps=0)


def test_outputs_are_immutable_and_promotion_is_disabled(monkeypatch,tmp_path):
    records,fit,audit,parent,p45,p46,policy,cohort=fixture(monkeypatch)
    destination=tmp_path/'crossover'
    result=d.stage2(records,fit,audit,policy,parent,lambda:None,
                    destination,p45,p46,{},steps=2,batch_size=4,eval_every=1,
                    structured_per_batch=1)
    assert result['status']=='TRAIN_ARCHITECTURAL_CROSSOVER_COMPLETE'
    assert result['production_promotion'] is False
    assert result['g2_certified'] is False
    assert result['train_policy']['enabled'] is False
    assert result['development_evaluation_skipped'] is True
    assert set(result['results']['arms'])=={'frozen:bce','frozen:structured',
                                          'joint:bce','joint:structured'}
    for name in (d.REPORT,d.LEDGER,d.WEIGHTS,d.POLICIES):
        assert (destination/name).is_file()
    assert json.loads((destination/d.POLICIES).read_text())==result['train_policy']
    assert not list(tmp_path.glob('*.staging-*'))
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        d.stage2(records,fit,audit,policy,parent,lambda:None,destination,
                 p45,p46,{},steps=2,batch_size=4,eval_every=1,structured_per_batch=1)


def test_no_dev_leakage(monkeypatch,tmp_path):
    records,fit,audit,parent,p45,p46,policy,cohort=fixture(monkeypatch)
    bad=copy.deepcopy(records)
    bad[10]['row']['split']='dev'
    with pytest.raises(ValueError,match='NONTRAIN_RECORD'):
        d.stage2(bad,fit,audit,policy,parent,lambda:None,tmp_path/'bad',
                 p45,p46,{},steps=2,batch_size=4,eval_every=1,
                 structured_per_batch=1)
    assert not (tmp_path/'bad').exists()


def test_cli_crossover_command_registered():
    p=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-architectural-crossover','--help'],check=True,capture_output=True,text=True)
    assert '--multiprompt46-run' in p.stdout
    assert '--structured-per-batch' in p.stdout


def test_decision_does_not_claim_proof():
    arm_names=('frozen:bce','frozen:structured','joint:bce','joint:structured')
    def result(values):
        return {'arms':{a:{'transfer':{'candidate_reachable':{'exact':v}}}
                        for a,v in zip(arm_names,values)}}
    no=d.determine_action(result([3,3,4,4]))
    assert no['decision']=='NO_DECISIVE_TRANSFER_GAIN_REEXAMINE_INPUT_EVIDENCE_AND_GRAPH_OBJECTIVE'
    assert not no['causal_proof_of_representation_insufficiency']
    yes=d.determine_action(result([3,4,10,10]))
    assert yes['representation_learning_signal']
    assert yes['decision']=='CONSIDER_TRAINABLE_REPRESENTATIONS'


def test_frozen_bce_exactly_replays_g2_0046_training(monkeypatch):
    records,_,_,parent,p45,p46,policy,cohort=fixture(monkeypatch)
    training=cohort['learn'][:8]
    supervision=d.d46.prepare_supervision(training,parent)
    cache=d.prepare_features(training,parent)
    old_cache={k:(item['h'],item['prior'],item['parent_score']) for k,item in cache.items()}
    model46,stats46=d.d46.fit_shared(training,parent,'nonlinear','bce',policy,
        steps=3,batch_size=4,eval_every=1,supervision=supervision,caches=old_cache)
    model47,stats47=d.train_arm(training,parent,policy,'frozen','bce',cache,supervision,
        steps=3,batch_size=4,eval_every=1,structured_per_batch=1)
    assert stats46['train_final']==stats47['train_final']
    for rec in training:
        key=d.d46._sha(rec)
        with torch.inference_mode():
            left=model46(cache[key]['h'],cache[key]['prior'])
            right=model47(cache[key])
        assert torch.equal(left,right)


def test_reference_budget_fails_closed_if_control_cannot_reproduce(monkeypatch,tmp_path):
    records,fit,audit,parent,p45,p46,policy,cohort=fixture(monkeypatch)
    monkeypatch.setattr(d,'STEPS',2)
    monkeypatch.setattr(d,'BATCH_PROMPTS',4)
    monkeypatch.setattr(d,'EVAL_EVERY',1)
    cache=d.prepare_features(cohort['learn'],parent)
    sup=d.d46.prepare_supervision(cohort['learn'],parent)
    old_cache={key:(item['h'],item['prior'],item['parent_score']) for key,item in cache.items()}
    reference,_=d.d46.fit_shared(cohort['learn'],parent,'nonlinear','bce',policy,
        steps=2,batch_size=4,eval_every=1,supervision=sup,caches=old_cache)
    all_cache=d.prepare_features([*cohort['learn'],*cohort['transfer'],*fit,*audit],parent)
    for group,section in (('learn','learn'),('transfer','transfer'),
                          ('train_fit','train_fit'),('natural_train_audit','natural_train_audit')):
        cohort_rows=cohort[group]
        scored=d.d46.score_records(cohort_rows,reference,policy,group,'nonlinear:bce',2,
            {k:(v['h'],v['prior'],v['parent_score']) for k,v in all_cache.items()})
        p46['results']['arms']['nonlinear:bce'][section]=d.d46.summarize(scored)
    # The original reference is now a real G2-0046 training run on these prompts.
    report=d.stage2(records,fit,audit,policy,parent,lambda:None,tmp_path/'ok',p45,p46,{},
                    steps=2,batch_size=4,eval_every=1,structured_per_batch=1)
    assert report['protocol']['reference_budget_reproduced'] is True
    assert report['results']['arms']['frozen:bce']['transfer']==p46['results']['arms']['nonlinear:bce']['transfer']
    changed=copy.deepcopy(p46)
    changed['results']['arms']['nonlinear:bce']['transfer']['all']['exact']+=1
    with pytest.raises(ValueError,match='BASELINE_REPRODUCTION_FAILED'):
        d.stage2(records,fit,audit,policy,parent,lambda:None,tmp_path/'bad',p45,changed,{},
                 steps=2,batch_size=4,eval_every=1,structured_per_batch=1)
    assert not (tmp_path/'bad').exists()


def test_real_uploaded_0046_report_shape_and_parent_verifier(tmp_path):
    from pathlib import Path
    import gzip
    from safetensors.torch import save_file
    source=Path('/mnt/data/Pasted text(20261115-005445).txt')
    if not source.is_file():
        pytest.skip('G2-0046 report attachment is unavailable')
    report=json.loads(source.read_text(encoding='utf-8'))
    assert report['results']['arms']['nonlinear:bce']['transfer']['all']['cases']==32
    assert len(report['cohorts']['train_learn_prompts'])==111
    assert len(report['cohorts']['transfer_prompts'])==32
    parent=d.x33.GraphSetEnergy().eval().requires_grad_(False)
    weights={}
    for kind in d.d46.KINDS:
        for loss in d.d46.LOSSES:
            for name,value in d.d45.Readout(kind,parent).state_dict().items():
                weights[f'{kind}_{loss}.{name}']=value.detach().clone().contiguous()
    save_file(weights,str(tmp_path/d.d46.WEIGHTS))
    (tmp_path/d.d46.POLICIES).write_text(json.dumps(report['train_policy']))
    # Reproduce G2-0046's real ledger writer: parent plus four arms, all
    # cohort labels unchanged (in particular ``learn``, not ``train_learn``).
    sections={
        'learn':'train_learn_prompts',
        'transfer':'transfer_prompts',
        'train_fit':'train_fit_prompts',
        'natural_train_audit':'natural_train_audit_prompts',
    }
    arms=('frozen_parent',*(f'{kind}:{loss}' for kind in d.d46.KINDS
                             for loss in d.d46.LOSSES))
    rows=[{'section':section,'arm':arm,'prompt_sha256':sha}
          for arm in arms
          for section,field in sections.items()
          for sha in report['cohorts'][field]]
    def rewrite_ledger(items):
        with gzip.open(tmp_path/d.d46.LEDGER,'wt',encoding='utf-8') as f:
            for item in items:
                f.write(json.dumps(item)+'\n')
        report['ledger_sha256']=d.backbone.sha256(tmp_path/d.d46.LEDGER)
        (tmp_path/d.d46.REPORT).write_text(json.dumps(report))
    rewrite_ledger(rows)
    report['ledger_sha256']=d.backbone.sha256(tmp_path/d.d46.LEDGER)
    report['weights_sha256']=d.backbone.sha256(tmp_path/d.d46.WEIGHTS)
    report['policies_sha256']=d.backbone.sha256(tmp_path/d.d46.POLICIES)
    (tmp_path/d.d46.REPORT).write_text(json.dumps(report))
    result,hashes=d.verify_parent46(tmp_path,report['parent_hashes'])
    assert result['cohorts']['train_source_count']==180
    assert len(hashes)==4
    # Misnamed parent cohort was the actual production preflight failure.
    malformed=copy.deepcopy(rows)
    malformed[0]['section']='train_learn'
    rewrite_ledger(malformed)
    with pytest.raises(ValueError,match='PARENT_LEDGER_SECTION'):
        d.verify_parent46(tmp_path,report['parent_hashes'])
    # The gate must still reject duplicated, missing, or cross-cohort records.
    for changed,expected in (
        ([*rows[:-1],rows[0]],'PARENT_LEDGER_DUPLICATE'),
        (rows[:-1],'PARENT_LEDGER_COUNT'),
        ([{**rows[0],'prompt_sha256':'f'*64},*rows[1:]],'PARENT_LEDGER_PROMPT'),
        ([{**rows[0],'arm':'unknown'},*rows[1:]],'PARENT_LEDGER_ARM'),
    ):
        rewrite_ledger(changed)
        with pytest.raises(ValueError,match=expected):
            d.verify_parent46(tmp_path,report['parent_hashes'])
    rewrite_ledger(rows)
    (tmp_path/d.d46.POLICIES).write_text('{"enabled":true}')
    with pytest.raises(ValueError,match='PARENT_POLICY_DRIFT'):
        d.verify_parent46(tmp_path,report['parent_hashes'])
