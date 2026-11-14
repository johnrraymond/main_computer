"""G2-0026 competitor identity training and immutable parent contracts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import gzip
import json

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_competitor_identity_discrimination as m
from tools import nanojev_gamer_v1_g2_context_utilization_intervention as parent25
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def fixture_model():
    torch.manual_seed(283)
    guard=source13.SourceConditionedScorer(4)
    model=parent23.RelationalContextScorer.from_guard(guard)
    with torch.no_grad():
        model.rel_readout.weight.fill_(.07)
        model.rel_readout.bias.fill_(.03)
    return model


def fixture_inventory():
    torch.manual_seed(284)
    n=6
    edges=[((0,1,'actor'),(2+i*3,3+i*3,'action'),'owns') for i in range(n)]
    return {'features':torch.randn(n,4*4+source13.EXTRAS),
            'base':torch.linspace(-.7,.7,n),'groups':[0,0,0,1,1,1],
            'edges':edges,'null_argmax':[False]*n}


def test_fixed_experiment_protocol_and_status():
    assert m.REPLAY==parent25.NEW
    assert m.NEW==('new_independent_dev_0026','new_topology_dev_0026')
    assert m.TRAINED==('identity_self_0026','identity_peer_0026')
    assert m.STEPS==480 and m.LR==parent25.LR
    assert m.PAIR_MARGIN==1 and m.PAIR_WEIGHT==.75 and m.HARD_NEGATIVES==3
    assert m.TOP_K==24 and m.THRESHOLD==.75
    assert set(parent25.ARMS).issubset(m.ARMS)


def test_parent24_verification_keeps_its_original_0023_cohorts(tmp_path, monkeypatch):
    """Regression for startup KeyError: new_independent_dev_0025.

    The G2-0024 audit belongs to G2-0023 DEV. G2-0026's REPLAY belongs
    to G2-0025 DEV and must never determine the audit's row inventory.
    """
    from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24

    assert audit24.SPLITS == parent25.REPLAY
    assert set(audit24.SPLITS).isdisjoint(m.REPLAY)
    model=fixture_model()
    inv=fixture_inventory()
    audited=audit24.audit_inventory(model, inv, {inv['edges'][0], inv['edges'][1]})
    rows={split:[{'prompt':split+'-prompt', 'family':'encounter',
                  'structural_topology':'native'}] for split in audit24.SPLITS}
    monkeypatch.setattr(m.base, 'gold_binding_edges', lambda row:[0,1])
    monkeypatch.setattr(m.density, 'sha_rows', lambda records:'synthetic-hash')
    entries={split:[{'group':split, 'index':1,
                     'prompt_sha256':m.base._sha(rows[split][0]['prompt']),
                     'family':'encounter', 'density_bucket':'small_1_24',
                     'structural_topology':'native', 'gold_total':2, **audited}]
             for split in audit24.SPLITS}
    root=tmp_path/'audit24';root.mkdir()
    audit24._save_cases(entries, root/audit24.CASES)
    hashes={'g2_0023_weights_sha256':'saved-parent-weight'}
    combined=[record for split in audit24.SPLITS for record in entries[split]]
    report={'schema':audit24.SCHEMA,
        'status':'G2_CONTEXT_UTILIZATION_DIAGNOSTIC_NOT_CERTIFIED',
        'parent_hashes':hashes,
        'datasets_sha256':{split:'synthetic-hash' for split in audit24.SPLITS},
        'predeclared':{'cohorts':list(audit24.SPLITS),'fixed_threshold':m.THRESHOLD,
            'top_k':m.TOP_K,'variants':list(audit24.VARIANTS),
            'gradient_indices':list(audit24.GRADIENT_INDICES),
            'parent_model':'G2-0023 relational_context_0023',
            'counterfactual_only_no_inference_changes':True},
        'frozen_checkpoint_sha256':hashes['g2_0023_weights_sha256'],
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,**audit24._no_promote(),
        'parameters':{name:{} for name in parent23.TRAINED},
        'gradient_probes':{split:[] for split in audit24.SPLITS},
        'cases_sha256':m.backbone.sha256(root/audit24.CASES),
        'evaluation':audit24.aggregate(combined),
        'by_split':{split:audit24.aggregate(group) for split,group in entries.items()},
        'by_family':{'encounter':audit24.aggregate(combined)},
        'by_density':{'small_1_24':audit24.aggregate(combined)},
        'by_topology':{'native':audit24.aggregate(combined)}}
    (root/audit24.REPORT).write_text(json.dumps(report))
    verified, records, signatures=m.verify_parent24(root, rows, hashes)
    assert verified==report
    assert set(records)==set(audit24.SPLITS)
    assert sum(map(len,records.values()))==2
    assert signatures['g2_0024_cases_sha256']==report['cases_sha256']
    report['evaluation']['cases']+=1
    (root/audit24.REPORT).write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT_0024_AGGREGATE_DRIFT'):
        m.verify_parent24(root, rows, hashes)


def test_hard_identity_loss_is_multi_positive_and_source_local():
    labels=torch.tensor([1.,1.,0.,1.,0.,0.])
    groups=[0,0,0,1,1,1]
    logits=torch.tensor([1.,1.,3.,2.,0.,1.],requires_grad=True)
    base=m.hard_identity_loss(logits,labels,groups)
    changed=logits.detach().clone()
    changed[0]+=2.;changed[1]+=2.
    assert m.hard_identity_loss(changed,labels,groups)<base
    changed=logits.detach().clone();changed[2]-=2.
    assert m.hard_identity_loss(changed,labels,groups)<base
    base.backward()
    assert logits.grad[0]<0 and logits.grad[1]<0
    assert logits.grad[2]>0
    assert logits.grad[3]<0 and logits.grad[4]>0
    # Neither positive is ever treated as a negative of the other.
    assert torch.isfinite(base)


def test_hard_negative_is_top_k_and_no_cross_source_gradients():
    logits=torch.tensor([1.,3.,2.,-5.,4.,1.],requires_grad=True)
    loss=m.hard_identity_loss(logits,torch.tensor([1.,0.,0.,0.,1.,1.]),
                              [0,0,0,0,1,1],hard_negatives=2)
    loss.backward()
    assert logits.grad[1]>0 and logits.grad[2]>0
    assert logits.grad[3]==0
    assert logits.grad[4]==0 and logits.grad[5]==0 # only positives in group 1
    assert logits.grad[0]<0


def test_hard_identity_zero_connected_for_no_valid_competition():
    scores=torch.tensor([1.,2.],requires_grad=True)
    z=m.hard_identity_loss(scores,torch.ones(2),[0,0])
    assert z.item()==0
    z.backward()
    assert scores.grad is not None and not scores.grad.abs().sum()
    with pytest.raises(ValueError,match='IDENTITY_SHAPES'):
        m.hard_identity_loss(scores,torch.ones(3),[0,0])
    with pytest.raises(ValueError,match='GROUP_ORDER'):
        m.hard_identity_loss(scores,torch.ones(2),[1,0])


def test_hard_identity_loss_depends_on_real_candidate_identity():
    # High-scoring incorrect destination becomes a hard negative only if its
    # actual TRAIN identity is incorrect; never a positional one-hot label.
    scores=torch.tensor([3.,2.,1.])
    actual=m.hard_identity_loss(scores,torch.tensor([1.,0.,0.]),[0,0,0])
    inverted=m.hard_identity_loss(scores,torch.tensor([0.,1.,0.]),[0,0,0])
    assert actual<inverted


def test_matched_arms_initialize_identically_and_freeze_original_scorer():
    model=fixture_model();before=deepcopy(model.state_dict())
    arms=m.initialize_arms(model)
    assert set(arms)==set(m.TRAINED)
    for net in arms.values():
        assert torch.count_nonzero(net.rel_readout.weight)==0
        assert {k for k,p in net.named_parameters() if p.requires_grad}=={
            k for k,p in net.named_parameters() if k.startswith('rel_')}
        assert all(torch.equal(net.state_dict()[k],v)
                   for k,v in m._base_snapshot(model).items())
    assert all(torch.equal(before[k],model.state_dict()[k]) for k in before)
    assert all(torch.equal(arms[m.TRAINED[0]].state_dict()[k],arms[m.TRAINED[1]].state_dict()[k])
               for k in before)


def test_train_objective_updates_branch_in_both_arms_and_keeps_multi_positive():
    model=fixture_model();inv=fixture_inventory()
    labels=torch.tensor([1.,1.,0.,0.,1.,0.])
    batch=[(inv['features'],inv['base'],inv['groups'],labels,torch.zeros(6),torch.zeros(6))]
    for arm,net in m.initialize_arms(model).items():
        original=m._base_snapshot(net)
        loss=m._batch_objective(net,batch,contextual=arm==m.TRAINED[1])
        loss.backward()
        assert torch.isfinite(loss)
        assert net.rel_readout.weight.grad is not None
        assert torch.linalg.vector_norm(net.rel_readout.weight.grad)>0
        assert all(p.grad is None for k,p in net.named_parameters() if not k.startswith('rel_'))
        torch.optim.AdamW((p for p in net.parameters() if p.requires_grad),lr=.01).step()
        assert all(torch.equal(net.state_dict()[k],v) for k,v in original.items())


def test_automatic_pair_loss_is_not_unchanged_g2_0025_loss():
    model=fixture_model();inv=fixture_inventory()
    y=torch.tensor([1.,1.,0.,0.,1.,0.]);g=inv['groups']
    net=m.initialize_arms(model)[m.TRAINED[1]]
    logits=net(inv['features'],inv['base'],g,contextual=True)
    baseline=m.train19.training_objective(logits,y,torch.zeros(6),torch.zeros(6),g,
                                           arm='precision_guard')
    result=m._batch_objective(net,[(inv['features'],inv['base'],g,y,torch.zeros(6),
                                     torch.zeros(6))],contextual=True)
    assert torch.allclose(result,baseline+m.PAIR_WEIGHT*m.hard_identity_loss(logits,y,g))
    assert result>baseline


def test_two_step_training_freezes_parent_and_compares_identical_data(monkeypatch):
    monkeypatch.setattr(m,'STEPS',2)
    model=fixture_model();original=deepcopy(model.state_dict())
    inv=fixture_inventory();gold={inv['edges'][0],inv['edges'][1],inv['edges'][4]}
    cases=[{'split':'train','family':'encounter' if i%2==0 else 'allocation',
            'rank':torch.zeros(6),'allowed':None,'gold':gold,
            'structural_topology':'native'} for i in range(640)]
    arms,info=m._train(cases,model,device='cpu',steps=2,inventory_fn=lambda *a,**kw:inv)
    assert info['steps']==2 and info['counts']['gold']>0
    assert info['hard_identity_weight']==m.PAIR_WEIGHT
    assert info['counts']['mixed_identity_groups']>0
    assert info['counts']['hard_identity_pairs']>0
    assert info['identity_pairs_within_source_only']
    assert all(torch.equal(original[k],model.state_dict()[k]) for k in original)
    for net in arms.values():
        assert torch.count_nonzero(net.rel_readout.weight)>0
        assert all(torch.equal(net.state_dict()[k],original[k])
                   for k in original if not k.startswith('rel_'))


def test_parent25_live_replay_checks_all_three_parent_models(monkeypatch):
    model=fixture_model();inv=fixture_inventory()
    gold={inv['edges'][0],inv['edges'][1]}
    case={'prompt_sha256':m.base._sha('prompt'),'family':'encounter',
          'anchors':[1,2,3,4],'gold':gold,'vectors':torch.randn(3,4)}
    row={'prompt':'prompt','family':'encounter'}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:torch.zeros(3))
    monkeypatch.setattr(m.source13,'make_inventory',lambda *a,**kw:inv)
    nets={a:deepcopy(model).eval() for a in m.ARMS}
    for arm in m.TRAINED:
        nets[arm]=m.initialize_arms(model)[arm].eval()
    r=m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,device='cpu')
    assert set(r['configurations'])==set(m.ARMS)
    prior={'prompt_sha256':r['prompt_sha256'],'candidate_count':r['candidate_count'],
           'reachable_gold':r['reachable_gold'],
           'configurations':{a:r['configurations'][a] for a in parent25.ARMS},
           'competition':{a:r['competition'][a] for a in parent25.ARMS}}
    m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,
                     device='cpu',parent=prior)
    prior['configurations'][parent25.ARMS[1]]['false_positive']+=1
    with pytest.raises(ValueError,match='PARENT_0025_LIVE_REPLAY_DRIFT'):
        m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,
                         device='cpu',parent=prior)
    agg=m.aggregate([r,r]);assert agg['cases']==2
    assert all(k in m.gates(agg) for k in ('identity_peer_f1_above_identity_self_by_005',
              'competitor_identity_values_help_graph_f1'))


def synthetic_parent25(tmp_path,monkeypatch):
    model=fixture_model();inv=fixture_inventory()
    gold={inv['edges'][0],inv['edges'][1]}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:torch.zeros(3))
    monkeypatch.setattr(m.source13,'make_inventory',lambda *a,**kw:inv)
    monkeypatch.setattr(m.base,'gold_binding_edges',lambda row:[0,1])
    monkeypatch.setattr(m.density,'sha_rows',lambda rows:'synthetic-hash')
    nets={arm:deepcopy(model).eval() for arm in parent25.ARMS}
    rows={k:[{'prompt':k,'family':'encounter','structural_topology':'native'}]
          for k in parent25.SPLITS}
    groups={}
    for split in parent25.SPLITS:
        case={'prompt_sha256':m.base._sha(split),'family':'encounter',
              'anchors':[1,2,3,4],'gold':gold,'vectors':torch.randn(3,4)}
        groups[split]=[parent25._evaluate_case(case,rows[split][0],object(),nets,None,
                  split=split,index=1,device='cpu')]
    dirname=tmp_path/'p25';dirname.mkdir()
    parent25._save_cases(groups,dirname/parent25.CASES)
    tensors={arm+'.'+k:v.detach().clone().contiguous()
             for arm in parent25.TRAINED for k,v in model.state_dict().items()}
    save_file(tensors,str(dirname/parent25.WEIGHTS))
    hashes={'g2_0024_report_sha256':'fake'}
    report={'schema':parent25.SCHEMA,'status':'G2_CONTEXT_BRANCH_TRAIN_ONLY_NOT_CERTIFIED',
       'parent_hashes':hashes,'train_dataset_sha256':'synthetic-hash',
       'datasets_sha256':{k:'synthetic-hash' for k in parent25.SPLITS},
       'new_prompt_sha256':{k:[m.base._sha(rows[k][0]['prompt'])] for k in parent25.NEW},
       'predeclared':{'train_seed':parent25.SEED,'new_dev_seeds':[parent25.EVAL_SEED,
           parent25.EVAL_SEED+1],'steps':parent25.STEPS,'lr':parent25.LR,
           'batch_seed':parent25.BATCH_SEED,'top_k':m.TOP_K,
           'groups_per_case':m.GROUPS_PER_CASE,'fixed_threshold':m.THRESHOLD,
           'arms':list(parent25.ARMS),'ablations':list(parent25.ABLATIONS),
           'base_checkpoint':'G2-0023 relational_context_0023',
           'initial_residual_readout':'zero_both_arms',
           'loss':'G2-0019 precision_guard unchanged',
           'training':'frozen_parent_scorer_residual_only',
           'self_only_control':'same_trainable_parameters_and_batches'},
       'train':{'steps':parent25.STEPS,'lr':parent25.LR,'seed':parent25.SEED,
           'batch_seed':parent25.BATCH_SEED,'train_cases':640,
           'same_initialized_weights':True,'zero_reset_readouts':True,
           'frozen_trained_g2_0023_base':True,'residual_parameters_only':True,
           'identical_train_batches':True,'identical_loss':True,
           'candidate_inventory_label_blind':True,'multi_positive_labels':True,
           'last_train_loss':{a:.4 for a in parent25.TRAINED}},
       'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
       'train_labels_used_only_for_loss':True,'multi_positive_supervision':True,
       'same_train_batches':True,'same_initial_checkpoint':True,'same_loss':True,
       'same_trainable_parameter_count':True,'frozen_existing_scorer':True,
       'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
       'frozen_0023_checkpoints':True,'frozen_0024_audit':True,
       'training_on_train_only':True,'new_cohorts_are_dev_not_sealed':True,
       'model_selection':False,'checkpoint_selection':False,
       'production_threshold_selection':False,'sealed_test_used':False,
       'challenge_or_test_split_used':False,'certification_authorized':False,
       'g2_certified':False,'g2_production_unlocked':False,
       'production_promotion':False,'graph_execution_authorized':False,
       'memory_writes':False,
       'cases_sha256':m.backbone.sha256(dirname/parent25.CASES),
       'new_weights_sha256':m.backbone.sha256(dirname/parent25.WEIGHTS)}
    cohorts={'replayed_0023_dev':[r for s in parent25.REPLAY for r in groups[s]],
             'new_0025_dev':[r for s in parent25.NEW for r in groups[s]]}
    report['by_split']={k:parent25.aggregate(v) for k,v in groups.items()}
    report['evaluation']={k:parent25.aggregate(v) for k,v in cohorts.items()}
    report['by_family']={k:{'encounter':parent25.aggregate(v)} for k,v in cohorts.items()}
    report['by_density']={k:{'small_1_24':parent25.aggregate(v)} for k,v in cohorts.items()}
    report['by_topology']={k:{'native':parent25.aggregate(v)} for k,v in cohorts.items()}
    report['diagnostic_gates']={k:parent25.gates(parent25.aggregate(v)) for k,v in cohorts.items()}
    (dirname/parent25.REPORT).write_text(json.dumps(report))
    return dirname,rows,hashes,model


def test_parent25_ledger_weights_verified_and_detect_tampering(tmp_path,monkeypatch):
    dirname,rows,hashes,model=synthetic_parent25(tmp_path,monkeypatch)
    report,records,digests=m.verify_parent25(dirname,rows,hashes,'synthetic-hash')
    assert sum(len(v) for v in records.values())==4
    assert 'g2_0025_weights_sha256' in digests
    models=m.load_parent25_models(dirname,model,report['new_weights_sha256'])
    assert set(models)==set(parent25.TRAINED)
    assert all(torch.equal(models[a].state_dict()[k],model.state_dict()[k])
               for a in models for k in model.state_dict())
    broken=deepcopy(report);broken['evaluation']['new_0025_dev']['cases']+=1
    (dirname/parent25.REPORT).write_text(json.dumps(broken))
    with pytest.raises(ValueError,match='AGGREGATE_DRIFT'):
        m.verify_parent25(dirname,rows,hashes,'synthetic-hash')
    (dirname/parent25.REPORT).write_text(json.dumps(report))
    with open(dirname/parent25.CASES,'ab') as f:f.write(b'corruption')
    with pytest.raises(ValueError,match='HASH_DRIFT'):
        m.verify_parent25(dirname,rows,hashes,'synthetic-hash')


def test_wrong_parent_schema_and_existing_output_fail_closed(tmp_path):
    d=tmp_path/'parent';d.mkdir();(d/parent25.REPORT).write_text('{"schema":"incorrect"}')
    with pytest.raises(ValueError,match='PARENT_0025_CONTRACT'):
        m.verify_parent25(d,{}, {},'hash')
    required={name:tmp_path for name in ('g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run','context_run',
        'learned_run','recall_run','crowded_run','robustness_run','diagnosis_run',
        'source_run','specialization_run','routing_robustness_run','general_run',
        'ceiling_run','context18_run','train19_run','ceiling20_run','ceiling21_run',
        'competition22_run','context23_run','audit24_run','intervention25_run')}
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,output=tmp_path,**required)
    with pytest.raises(ValueError,match='FIXED_PROTOCOL'):
        m.run(root=tmp_path,output=tmp_path/'out',max_length=100,**required)
    with pytest.raises(ValueError,match='FIXED_TRAIN_CONTRACT'):
        m._train([{'split':'dev','family':'encounter'}],fixture_model())


def test_deterministic_ledgers_and_cli_registration(tmp_path):
    entries={split:[{'group':split,'index':1}] for split in m.SPLITS}
    paths=[tmp_path/'a.gz',tmp_path/'b.gz']
    for path in paths:m._save_cases(entries,path)
    assert paths[0].read_bytes()==paths[1].read_bytes()
    with gzip.open(paths[0],'rt') as f:
        assert [json.loads(line)['group'] for line in f]==list(m.SPLITS)
    text=Path(m.__file__).with_name('nanojev_gamer_v1_graph_curriculum.py').read_text()
    assert text.count('frob-g2-competitor-identity-discrimination')==2
    assert "'intervention25_run':'graph-g2-context-utilization-intervention-0025'" in text
