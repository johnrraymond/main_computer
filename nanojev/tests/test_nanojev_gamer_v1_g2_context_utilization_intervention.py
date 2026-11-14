"""G2-0025 frozen-base peer-context versus self-only training contract."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import gzip
import json

import pytest
import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_context_utilization_intervention as m
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


def fixture_model():
    torch.manual_seed(941)
    guard=source13.SourceConditionedScorer(4)
    net=parent23.RelationalContextScorer.from_guard(guard)
    with torch.no_grad():
        net.rel_readout.weight.fill_(.12)
        net.rel_readout.bias.fill_(.03)
    return guard,net


def fixture_inventory(n=6):
    torch.manual_seed(942)
    g=[0,0,0,1,1,1][:n]
    edges=[((0,1,'actor'),(2+i*3,3+i*3,'action'),'owns') for i in range(n)]
    return {'features':torch.randn(n,4*4+source13.EXTRAS),
            'base':torch.linspace(-.5,1.1,n),
            'groups':g,'edges':edges,'null_argmax':[0]*n}


def test_predeclared_frozen_training_and_new_development_protocol():
    assert m.THRESHOLD==.75 and m.TOP_K==24
    assert m.REPLAY==parent23.NEW
    assert m.NEW==('new_independent_dev_0025','new_topology_dev_0025')
    assert m.STEPS==480 and m.LR==3e-4
    assert 'peer_suppressed' in m.ABLATIONS
    assert m.TRAINED==('self_only_0025','peer_context_0025')


def test_initial_parameters_match_base_is_frozen_and_readout_reset():
    _,net=fixture_model()
    original={k:v.detach().clone() for k,v in net.state_dict().items()}
    arms=m.initialize_arms(net)
    assert all(torch.equal(original[k],v) for k,v in net.state_dict().items())
    for a in m.TRAINED:
        trained=arms[a]
        assert set(trained.state_dict())==set(net.state_dict())
        assert torch.count_nonzero(trained.rel_readout.weight)==0
        assert torch.count_nonzero(trained.rel_readout.bias)==0
        assert {k for k,p in trained.named_parameters() if p.requires_grad}=={
            k for k,p in trained.named_parameters() if k.startswith('rel_')}
        assert all(torch.equal(trained.state_dict()[k],net.state_dict()[k])
                   for k in m._base_snapshot(net))
    inv=fixture_inventory()
    x,b,g=inv['features'],inv['base'],inv['groups']
    for arm in m.TRAINED:
        result=arms[arm](x,b,g,contextual=arm==m.TRAINED[1])
        assert torch.allclose(result,audit24.baseline_residual(net,x,b,g),atol=1e-6)


def test_train_only_residual_branch_receives_gradients_in_both_arms():
    _,net=fixture_model()
    inventory=fixture_inventory()
    gold={inventory['edges'][0],inventory['edges'][1],inventory['edges'][4]}
    labels=torch.tensor([float(edge in gold) for edge in inventory['edges']])
    batch=[(inventory['features'],inventory['base'],inventory['groups'],
            labels,torch.zeros(6),torch.zeros(6))]
    for arm,model in m.initialize_arms(net).items():
        prior=m._base_snapshot(model)
        loss=m._batch_objective(model,batch,contextual=arm==m.TRAINED[1])
        loss.backward()
        assert model.rel_readout.weight.grad is not None
        assert torch.linalg.vector_norm(model.rel_readout.weight.grad)>0
        assert all(p.grad is None for key,p in model.named_parameters()
                   if not key.startswith('rel_'))
        optim=torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],lr=.01)
        optim.step()
        assert any(torch.count_nonzero(model.state_dict()[key]-net.state_dict()[key])
                   for key in model.state_dict() if key.startswith('rel_'))
        assert all(torch.equal(model.state_dict()[key],value) for key,value in prior.items())


def test_peer_path_is_group_local_and_keeps_multiple_positive_destinations():
    _,net=fixture_model()
    inv=fixture_inventory()
    before={a:net(inv['features'],inv['base'],inv['groups'],
                   contextual=a==m.TRAINED[1]) for a in m.TRAINED}
    altered=inv['features'].clone();altered[4]+=10.
    after={a:net(altered,inv['base'],inv['groups'],
                  contextual=a==m.TRAINED[1]) for a in m.TRAINED}
    # The unchanged existing scorer may use its own group features, so
    # compare incremental residuals after subtracting the original base.
    orig_base=audit24.baseline_residual(net,inv['features'],inv['base'],inv['groups'])
    changed_base=audit24.baseline_residual(net,altered,inv['base'],inv['groups'])
    assert torch.allclose((before[m.TRAINED[1]]-orig_base)[:3],
                          (after[m.TRAINED[1]]-changed_base)[:3],atol=1e-5)
    assert torch.allclose((before[m.TRAINED[0]]-orig_base)[:3],
                          (after[m.TRAINED[0]]-changed_base)[:3],atol=1e-5)
    singleton=fixture_inventory(1)
    with torch.no_grad():
        assert torch.allclose(net(singleton['features'],singleton['base'],[0],contextual=True),
                              audit24.baseline_residual(net,singleton['features'],
                                                        singleton['base'],[0]))
    # Training uses independent Bernoulli targets with two valid destinations.
    y=torch.tensor([1.,1.,0.,0.,0.,0.])
    value=m._batch_objective(net,[(inv['features'],inv['base'],inv['groups'],
                        y,torch.zeros(6),torch.zeros(6))],contextual=True)
    assert torch.isfinite(value)


def test_evaluation_ablation_reports_actual_graph_decision_effects(monkeypatch):
    _,net=fixture_model()
    inv=fixture_inventory()
    gold={inv['edges'][0],inv['edges'][1]}
    case={'prompt_sha256':m.base._sha('prompt'),'family':'encounter',
          'anchors':[1,2,3,4],'gold':gold,'vectors':torch.randn(3,4)}
    row={'prompt':'prompt','family':'encounter'}
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:torch.zeros(3))
    monkeypatch.setattr(m.source13,'make_inventory',lambda *a,**kw:inv)
    nets={a:deepcopy(net).eval() for a in m.ARMS}
    r=m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,device='cpu')
    assert set(r['configurations'])==set(m.ARMS)
    assert set(r['context_ablations'])==set(m.ABLATIONS)
    assert r['gold_total']==2 and r['candidate_count']==6
    agg=m.aggregate([r,deepcopy(r)])
    assert agg['cases']==2 and agg['candidates']==12
    assert isinstance(m.gates(agg),dict)
    assert agg['ablations']['peer_suppressed']['decision_flips']==(
        2*r['context_ablations']['peer_suppressed']['decision_flips'])
    parent={'prompt_sha256':r['prompt_sha256'],'candidate_count':6,'reachable_gold':2,
            'configurations':{'relational_context_0023':r['configurations'][m.ARMS[0]]},
            'competition':{'relational_context_0023':r['competition'][m.ARMS[0]]}}
    m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,
                     device='cpu',parent=parent)
    parent['reachable_gold']-=1
    with pytest.raises(ValueError,match='LIVE_REPLAY_DRIFT'):
        m._evaluate_case(case,row,object(),nets,None,split=m.NEW[0],index=1,
                         device='cpu',parent=parent)


def test_fixed_train_contract_refuses_dev_and_wrong_steps():
    _,net=fixture_model()
    with pytest.raises(ValueError,match='FIXED_TRAIN_CONTRACT'):
        m._train([{'split':'dev','family':'encounter'}],net)
    with pytest.raises(ValueError,match='EMPTY_AGGREGATE'):
        m.aggregate([])


def test_frozen_base_during_actual_optimizer_steps(monkeypatch):
    _,model=fixture_model()
    monkeypatch.setattr(m,'STEPS',2)
    inv=fixture_inventory()
    inv['null_argmax']=[False]*6
    data=[{'split':'train','family':'encounter' if i%2==0 else 'allocation',
           'rank':torch.zeros(6),'allowed':None,'gold':{inv['edges'][0],inv['edges'][1]},
           'structural_topology':'native'} for i in range(640)]
    reference={k:v.detach().clone() for k,v in model.state_dict().items()}
    arms,info=m._train(data,model,device='cpu',steps=2,inventory_fn=lambda *a,**kw:inv)
    assert info['steps']==2 and info['frozen_trained_g2_0023_base']
    assert info['counts']['gold']>0
    for a,net in arms.items():
        assert all(torch.equal(net.state_dict()[k],reference[k])
                   for k in m._base_snapshot(model))
        assert torch.count_nonzero(net.rel_readout.weight)>0
    assert all(torch.equal(model.state_dict()[k],v) for k,v in reference.items())


def test_parent24_bad_contract_and_existing_output_fail_before_capture(tmp_path):
    d=tmp_path/'a24';d.mkdir()
    (d/audit24.REPORT).write_text('{"schema":"wrong"}')
    with pytest.raises(ValueError,match='PARENT_0024_CONTRACT'):
        m.verify_parent24(d,{}, {'g2_0023_weights_sha256':'sha'})
    settings={name:None for name in ('root','g1_validation_report','g2_run','selection_run',
        'discrimination_run','relational_run','density_run','consistency_run','context_run',
        'learned_run','recall_run','crowded_run','robustness_run','diagnosis_run',
        'source_run','specialization_run','routing_robustness_run','general_run',
        'ceiling_run','context18_run','train19_run','ceiling20_run','ceiling21_run',
        'competition22_run','context23_run','audit24_run')}
    settings['root']=tmp_path
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**settings,output=tmp_path)
    with pytest.raises(ValueError,match='FIXED_PROTOCOL'):
        m.run(**settings,output=tmp_path/'new',max_length=1800)


def test_deterministic_gzip_and_no_sealed_inputs(tmp_path):
    record={'family':'encounter','configurations':{'dummy':{'correct':1}},'index':1}
    groups={split:[{'group':split,**record}] for split in m.SPLITS}
    a=tmp_path/'a.gz';b=tmp_path/'b.gz'
    m._save_cases(groups,a);m._save_cases(groups,b)
    assert a.read_bytes()==b.read_bytes()
    with gzip.open(a,'rt',encoding='utf-8') as f:
        assert [json.loads(s)['group'] for s in f]==list(m.SPLITS)


def test_cli_name_registered():
    path=Path(m.__file__).with_name('nanojev_gamer_v1_graph_curriculum.py')
    content=path.read_text()
    assert "frob-g2-context-utilization-intervention" in content
    assert "'audit24_run':'graph-g2-context-utilization-audit-0024'" in content


def test_parent24_valid_ledger_and_tampering_rejected(tmp_path,monkeypatch):
    _,net=fixture_model()
    inv=fixture_inventory()
    audited=audit24.audit_inventory(net,inv,{inv['edges'][0],inv['edges'][1]})
    rows={key:[{'prompt':key+'-prompt','family':'encounter',
                'structural_topology':'native','graph':{'nodes':[]}}] for key in m.REPLAY}
    grouped={}
    for split in m.REPLAY:
        grouped[split]=[{'group':split,'index':1,
            'prompt_sha256':m.base._sha(rows[split][0]['prompt']),
            'family':'encounter','density_bucket':'small_1_24',
            'structural_topology':'native','gold_total':2,**audited}]
    monkeypatch.setattr(m.base,'gold_binding_edges',lambda row:[0,1])
    monkeypatch.setattr(m.density,'sha_rows',lambda rows:'synthetic-hash')
    d=tmp_path/'a24';d.mkdir()
    audit24._save_cases(grouped,d/audit24.CASES)
    hashes={'g2_0023_weights_sha256':'saved-parent-weight'}
    combined=[r for split in m.REPLAY for r in grouped[split]]
    report={'schema':audit24.SCHEMA,
        'status':'G2_CONTEXT_UTILIZATION_DIAGNOSTIC_NOT_CERTIFIED',
        'parent_hashes':hashes,
        'datasets_sha256':{split:'synthetic-hash' for split in m.REPLAY},
        'predeclared':{'cohorts':list(m.REPLAY),'fixed_threshold':m.THRESHOLD,
            'top_k':m.TOP_K,'variants':list(audit24.VARIANTS),
            'gradient_indices':list(audit24.GRADIENT_INDICES),
            'parent_model':'G2-0023 relational_context_0023',
            'counterfactual_only_no_inference_changes':True},
        'frozen_checkpoint_sha256':hashes['g2_0023_weights_sha256'],
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,**audit24._no_promote(),
        'parameters':{k:{} for k in parent23.TRAINED},
        'gradient_probes':{k:[] for k in m.REPLAY},
        'cases_sha256':m.backbone.sha256(d/audit24.CASES),
        'evaluation':audit24.aggregate(combined),
        'by_split':{k:audit24.aggregate(v) for k,v in grouped.items()},
        'by_family':{'encounter':audit24.aggregate(combined)},
        'by_density':{'small_1_24':audit24.aggregate(combined)},
        'by_topology':{'native':audit24.aggregate(combined)},
    }
    rp=d/audit24.REPORT
    rp.write_text(json.dumps(report))
    read,rec,shas=m.verify_parent24(d,rows,hashes)
    assert len(rec)==2 and all(len(v)==1 for v in rec.values())
    assert shas['g2_0024_cases_sha256']==report['cases_sha256']
    report['evaluation']['graph_metrics']['full_context']['correct']+=1
    rp.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT_0024_AGGREGATE_DRIFT'):
        m.verify_parent24(d,rows,hashes)
    report['evaluation']=audit24.aggregate(combined)
    report['sealed_test_used']=True
    rp.write_text(json.dumps(report))
    with pytest.raises(ValueError,match='PARENT_0024_CONTRACT:sealed_test_used'):
        m.verify_parent24(d,rows,hashes)


def test_fresh_development_is_disjoint_and_withheld_topologies():
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    import random
    old=[corpus.encounter(random.Random(7),'train',1),
         corpus.allocation(random.Random(8),'train',2)]
    cohorts=m.fresh_cohorts(old,old)
    assert [len(cohorts[k]) for k in m.NEW]==[320,128]
    assert {r['structural_topology'] for r in cohorts[m.NEW[1]]}=={'4x4','4x2','2x4'}
    hashes=[m.base._sha(r['prompt']) for r in [*old,*cohorts[m.NEW[0]],*cohorts[m.NEW[1]]]]
    assert len(hashes)==len(set(hashes))
