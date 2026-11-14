"""G2-0024 immutable frozen audit, intervention semantics and lineage guards."""
from __future__ import annotations

from copy import deepcopy
import gzip
import json

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_context_utilization_audit as m
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def _models():
    guard=source13.SourceConditionedScorer(4)
    net=parent23.RelationalContextScorer.from_guard(guard).eval()
    with torch.no_grad():
        net.rel_readout.weight.fill_(.15)
        net.rel_readout.bias.fill_(.02)
    return guard,net


def _features(n=6):
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(941)
        x=torch.randn(n,4*4+source13.EXTRAS)
    b=torch.tensor([-.5,.3,1.,-.4,.1,.9][:n])
    g=([0,0,0,1,1,1])[:n]
    return x,b,g


def _inventory():
    x,b,g=_features()
    edges=[((0,1,'actor'),(2+i*2,3+i*2,'action'),'owns') for i in range(len(g))]
    return dict(features=x,base=b,groups=g,edges=edges)


def test_locked_nonpromotable_protocol():
    assert m.SPLITS==parent23.NEW
    assert m.THRESHOLD==.75 and m.TOP_K==24
    assert m.VARIANTS[0]=='full_context'
    assert not any(m._no_promote().values())
    assert 'training_performed' in m._no_promote()


def test_full_intervention_is_exact_existing_forward_and_base_untouched():
    guard,net=_models()
    x,b,g=_features()
    with torch.no_grad():
        actual=net(x,b,g,contextual=True)
        replay=m._audit_forward(net,x,b,g,'full_context')
        assert torch.allclose(actual,replay,rtol=1e-6,atol=1e-6)
        assert torch.allclose(m._audit_forward(net,x,b,g,'residual_disabled'),
                              m.baseline_residual(net,x,b,g),rtol=0,atol=0)
        assert torch.allclose(m._audit_forward(net,x,b,g,'self_only_counterfactual'),
                              net(x,b,g,contextual=False))
        assert not torch.allclose(replay,m._audit_forward(net,x,b,g,'peer_suppressed'))
        assert not torch.allclose(replay,m._audit_forward(net,x,b,g,'misaligned_peer_values'))
    with pytest.raises(ValueError,match='UNKNOWN_VARIANT'):
        m._audit_forward(net,x,b,g,'other')


def test_uniform_attention_and_peer_misalignment_are_group_local():
    _,net=_models()
    x,b,g=_features()
    original={a:m._audit_forward(net,x,b,g,a) for a in m.VARIANTS}
    shifted=x.clone();shifted[5]+=2
    shifted_results={a:m._audit_forward(net,shifted,b,g,a) for a in m.VARIANTS}
    for variant in m.VARIANTS:
        if variant!='residual_disabled':
            # Changing the other group cannot change residuals on this group.
            base=m.baseline_residual(net,x,b,g)
            other_base=m.baseline_residual(net,shifted,b,g)
            assert torch.allclose((original[variant]-base)[:3],
                (shifted_results[variant]-other_base)[:3],atol=1e-6)
    x1,b1,_=_features(1)
    for variant in m.VARIANTS:
        assert torch.allclose(m._audit_forward(net,x1,b1,[0],variant),
                              m.baseline_residual(net,x1,b1,[0]))
    with pytest.raises(ValueError,match='GROUP_EXCEEDS_TOP24'):
        with torch.no_grad():
            net.rel_readout.weight.fill_(.1)
        many=torch.randn(25,4*4+source13.EXTRAS)
        m._audit_forward(net,many,torch.zeros(25),[0]*25,'full_context')


def test_audit_inventory_reports_attributable_decision_flips_and_parent_replay():
    _,net=_models();inv=_inventory();gold={inv['edges'][0],inv['edges'][2],inv['edges'][4]}
    audited=m.audit_inventory(net,inv,gold)
    assert audited['peer_candidates']==len(inv['edges'])
    assert set(audited['metrics'])==set(m.VARIANTS)
    assert set(audited['shifts'])==set(m.VARIANTS[1:])
    for info in audited['shifts'].values():
        assert info['scores_shifted_over_1e_4']>=0
        assert info['decision_flips']==sum(info[k] for k in (
            'full_only_gold','counterfactual_only_gold',
            'full_only_false_positive','counterfactual_only_false_positive'))
    parent={'candidate_count':len(inv['edges']),'reachable_gold':len(gold),
            'configurations':{'relational_context_0023':audited['metrics']['full_context']},
            'competition':{'relational_context_0023':audited['competition']['full_context']}}
    m.audit_inventory(net,inv,gold,parent_record=parent)
    parent['configurations']['relational_context_0023']['correct']+=1
    with pytest.raises(ValueError,match='LIVE_REPLAY_DRIFT'):
        m.audit_inventory(net,inv,gold,parent_record=parent)


def test_parameter_initialization_and_gradient_audit_never_updates_weights():
    guard=source13.SourceConditionedScorer(4)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(parent23.SEED)
        model=parent23.RelationalContextScorer.from_guard(guard).eval()
    initial=m.parameter_utilization(model,guard)
    assert all(v['update_l2']==0 for v in initial.values())
    assert initial['rel_readout.weight']['initial_l2']==0.
    with torch.no_grad():model.rel_readout.weight.fill_(.2)
    updated=m.parameter_utilization(model,guard)
    assert updated['rel_readout.weight']['update_l2']>0
    x,b,g=_features()
    before={k:v.detach().clone() for k,v in model.state_dict().items()}
    flags={k:p.requires_grad for k,p in model.named_parameters()}
    probe=m.gradient_probe(model,x,b,g)
    assert probe is not None
    assert probe['rel_readout.weight']['l2']>0
    assert probe['rel_query.weight']['l2']>0
    assert all(torch.equal(model.state_dict()[k],v) for k,v in before.items())
    assert {k:p.requires_grad for k,p in model.named_parameters()}==flags
    assert m.gradient_probe(model,x[:1],b[:1],[0]) is None


def test_aggregate_has_graph_metrics_not_just_logit_shifts():
    _,net=_models()
    inv=_inventory();gold={inv['edges'][0],inv['edges'][2]}
    data=m.audit_inventory(net,inv,gold)
    record={'family':'encounter','density_bucket':'medium_25_48','structural_topology':'native',**data}
    aggregate=m.aggregate([record,deepcopy(record)])
    assert aggregate['cases']==2
    assert aggregate['candidates']==2*len(inv['edges'])
    assert aggregate['graph_metrics']['full_context']['cases']==2
    assert aggregate['interventions']['peer_suppressed']['decision_flips']==(
        2*data['shifts']['peer_suppressed']['decision_flips'])
    with pytest.raises(ValueError,match='EMPTY_AGGREGATE'):
        m.aggregate([])


def test_load_exact_parent_checkpoint_and_reject_keys_hashes(tmp_path):
    guard=source13.SourceConditionedScorer(4)
    prototype=parent23.RelationalContextScorer.from_guard(guard)
    directory=tmp_path/'p23';directory.mkdir()
    tensors={arm+'.'+k:v.detach().cpu().contiguous().clone()
             for arm in parent23.TRAINED for k,v in prototype.state_dict().items()}
    target=directory/parent23.WEIGHTS
    save_file(tensors,str(target))
    sha=m.backbone.sha256(target)
    models=m.load_parent23_models(directory,guard,sha)
    assert set(models)==set(parent23.TRAINED)
    assert all(not p.requires_grad for net in models.values() for p in net.parameters())
    with pytest.raises(ValueError,match='PARENT_WEIGHT_HASH'):
        m.load_parent23_models(directory,guard,'0'*64)
    tensors.pop(next(iter(tensors)))
    save_file(tensors,str(target))
    with pytest.raises(ValueError,match='PARENT_WEIGHT_KEYS'):
        m.load_parent23_models(directory,guard,m.backbone.sha256(target))


def test_parent_report_rejects_bad_schema_before_attempting_ledger(tmp_path):
    directory=tmp_path/'p23';directory.mkdir()
    (directory/parent23.REPORT).write_text('{"schema":"wrong"}')
    with pytest.raises(ValueError,match='PARENT_0023_CONTRACT'):
        m.verify_parent23(directory,{}, {},'x')


def test_output_safety_and_deterministic_gzip(tmp_path):
    inv=_inventory();_,net=_models()
    d=m.audit_inventory(net,inv,{inv['edges'][0]})
    groups={k:[{'group':k,'index':1,'family':'encounter',**d}] for k in m.SPLITS}
    a=tmp_path/'a.gz';b=tmp_path/'b.gz'
    m._save_cases(groups,a);m._save_cases(groups,b)
    assert a.read_bytes()==b.read_bytes()
    with gzip.open(a,'rt',encoding='utf-8') as f:
        assert len([json.loads(line) for line in f])==2
    parameters={name:None for name in (
        'root','g1_validation_report','g2_run','selection_run','discrimination_run',
        'relational_run','density_run','consistency_run','context_run','learned_run',
        'recall_run','crowded_run','robustness_run','diagnosis_run','source_run',
        'specialization_run','routing_robustness_run','general_run','ceiling_run',
        'context18_run','train19_run','ceiling20_run','ceiling21_run',
        'competition22_run','context23_run')}
    parameters['root']=tmp_path
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**parameters,output=tmp_path)
    with pytest.raises(ValueError,match='FIXED_PROTOCOL'):
        m.run(**parameters,output=tmp_path/'x',max_length=1000)


def test_parent23_ledger_contract_recomputes_aggregates_and_rejects_tampering(tmp_path,monkeypatch):
    directory=tmp_path/'p23';directory.mkdir()
    rows={split:[{'prompt':f'prompt-{split}','family':'encounter','split':'dev',
                  'structural_topology':'native'}] for split in parent23.SPLITS}
    monkeypatch.setattr(m.base,'gold_binding_edges',lambda row:{'edge'})
    monkeypatch.setattr(m.density,'sha_rows',lambda value: str(len(value))+'-rows')
    records={}
    for split in parent23.SPLITS:
        r=rows[split][0]
        records[split]=[{'group':split,'index':1,'prompt_sha256':base._sha(r['prompt']),
            'family':'encounter','structural_topology':'native','density_bucket':'small_1_24',
            'gold_total':1,'reachable_gold':1,'candidate_count':2,
            'configurations':{arm:{'correct':1,'missing':0,'false_positive':1,'exact':False}
                              for arm in parent23.ARMS},
            'competition':{arm:{'gold_with_competing_negatives':1,
                                'gold_not_ahead_of_hardest_negative':1}
                           for arm in parent23.ARMS}}]
    ledger=directory/parent23.CASES
    parent23._save_cases(records,ledger)
    (directory/parent23.WEIGHTS).write_bytes(b'synthetic checkpoint marker')
    combined={'replayed_0022_dev':[r for k in parent23.REPLAY for r in records[k]],
              'new_0023_dev':[r for k in parent23.NEW for r in records[k]]}
    report={'schema':parent23.SCHEMA,
        'status':'G2_CONTEXTUAL_RELATIONSHIP_DEV_NOT_CERTIFIED',
        'parent_hashes':{'parent':'hash'},'train_dataset_sha256':'TRAIN',
        'datasets_sha256':{k:'1-rows' for k in records},
        'new_prompt_sha256':{k:[base._sha(rows[k][0]['prompt'])] for k in parent23.NEW},
        'predeclared':{'train_seed':parent23.SEED,
            'prior_0022_training_batch_seed':m.parent22.SEED,
            'new_dev_seeds':[parent23.EVAL_SEED,parent23.EVAL_SEED+1],
            'steps':parent23.STEPS,'lr':parent23.LR,
            'top_k':m.TOP_K,'groups_per_case':parent23.GROUPS_PER_CASE,
            'fixed_threshold':m.THRESHOLD,'arms':list(parent23.ARMS),
            'architecture':'candidate_specific_cross_attention_residual',
            'capacity_control':'same_parameters_candidate_self_only',
            'loss':'g2_0019_precision_guard_unmodified',
            'single_valid_destination_assumption':False},
        'train':{'steps':parent23.STEPS,'lr':parent23.LR,'seed':parent23.SEED,
            'batch_seed':m.parent22.SEED,'train_cases':640,
            'same_initialization':True,'same_training_batches':True,
            'same_precision_guard_loss':True,'no_new_training_gold_injection':True,
            'capacity_matched_trainable_parameters':True},
        'by_split':{k:parent23.aggregate(v) for k,v in records.items()},
        'evaluation':{k:parent23.aggregate(v) for k,v in combined.items()},
        'by_family':{k:{'encounter':parent23.aggregate(v)} for k,v in combined.items()},
        'by_density':{k:{'small_1_24':parent23.aggregate(v)} for k,v in combined.items()},
        'by_topology':{k:{} for k in combined},
        'diagnostic_gates':{k:parent23.gates(parent23.aggregate(v))
                            for k,v in combined.items()},
        'cases_sha256':m.backbone.sha256(ledger),
        'new_weights_sha256':m.backbone.sha256(directory/parent23.WEIGHTS)}
    report.update({'candidate_selection_uses_gold':False,'gold_used_for_train_loss_only':True,
        'multi_positive_supervision':True,'same_train_batches':True,
        'same_initial_checkpoint':True,'same_loss_for_trained_arms':True,
        'same_trainable_parameter_count':True,'frozen_g1':True,
        'frozen_rank_head':True,'frozen_backbone':True,'frozen_parent_guard':True,
        'frozen_0022_controls':True,'training_on_train_only':True,
        'new_cohorts_are_dev_not_sealed':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False})
    (directory/parent23.REPORT).write_text(json.dumps(report))
    loaded,cases,hashes=m.verify_parent23(directory,rows,{'parent':'hash'},'TRAIN')
    assert loaded==report
    assert len(cases)==4 and set(hashes)=={
        'g2_0023_report_sha256','g2_0023_cases_sha256','g2_0023_weights_sha256'}
    altered=deepcopy(report);altered['evaluation']['new_0023_dev']['reachable_gold']=0
    (directory/parent23.REPORT).write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='AGGREGATE_DRIFT'):
        m.verify_parent23(directory,rows,{'parent':'hash'},'TRAIN')
    (directory/parent23.REPORT).write_text(json.dumps(report))
    with gzip.open(ledger,'rt',encoding='utf-8') as z:
        lines=[json.loads(line) for line in z]
    lines[0]['prompt_sha256']='invalid'
    with gzip.open(ledger,'wt',encoding='utf-8') as z:
        for row in lines:z.write(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='HASH_DRIFT'):
        m.verify_parent23(directory,rows,{'parent':'hash'},'TRAIN')
