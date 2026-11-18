"""Behavioral and command-path tests for G2-0055.

The tests run actual model losses and inference, not source-text assertions.
They deliberately exercise both failed and passing memorization gates.
"""
import copy
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as m
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as b54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def sample(section='counterfactual_train',name='a',side=0,role='allocation',mode='role_swap'):
    n=7
    src=torch.tensor([0,0,1,1,2,2,3,3,4,4,5,5],dtype=torch.long)
    dst=torch.tensor([1,2,2,3,3,4,4,0,0,1,1,6],dtype=torch.long)
    rel=torch.tensor([0,1,0,1,0,1,0,1,0,1,0,1],dtype=torch.long)
    return {'section':section,'prompt_sha256':f'{section}-{name}-{side}',
       'pair':f'{section}-{name}','family':role,'mode':mode,
       'node':torch.randn(n,96,generator=torch.Generator().manual_seed(14+side)).half(),
       'kinds':torch.zeros(n,dtype=torch.long),'margin':torch.zeros(n),
       'src':src,'dst':dst,'relation':rel,'geometry':torch.zeros(len(src),5),
       'positive':torch.tensor([0,4],dtype=torch.long),'truth_count':2,
       'edge_count':len(src)}


def cohorts():
    rows=[];pairs={}
    for section,n in (('counterfactual_train',48),('counterfactual_probe',16),
                      ('fresh_counterfactual_probe',16)):
        pairs[section]=[]
        for i in range(n):
            family=['allocation','encounter'][i%2]
            mode=['role_swap','renaming'][(i//2)%2]
            cells=[]
            for side in (0,1):
                s=sample(section,str(i),side,family,mode)
                anchors=[(j*3,j*3+2,'actor') for j in range(7)]
                edges=[(anchors[int(u)],anchors[int(v)],g53.RELATIONS[int(r)])
                       for u,v,r in zip(s['src'],s['dst'],s['relation'])]
                s['edges']=edges;s['labels']={edges[0],edges[4]}
                rows.append(s);cells.append(s['prompt_sha256'])
            pairs[section].append({'id':f'{section}-{i}','sha256':cells,
                'family':family,'mode':mode})
    return rows,pairs


def synthetic_parent():
    return {'scoring_experiment':{'results':{
        'counterfactual_probe':{'D_competition_calibrated':{'exact':0,'cases':32}},
        'fresh_counterfactual_probe':{'D_competition_calibrated':{'exact':0,'cases':32}}}}}


@pytest.mark.parametrize('arm',m.ARMS)
def test_all_architectures_train_backward_and_infer_without_gold_at_inference(arm):
    torch.manual_seed(17)
    net=m.model_for(arm)
    case=sample()
    opt=torch.optim.AdamW(net.parameters(),lr=.001)
    opt.zero_grad(set_to_none=True)
    loss=m.objective(net,case,arm)
    assert bool(torch.isfinite(loss))
    loss.backward()
    assert all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in net.parameters())
    opt.step()
    first=m.infer_edges(net.eval(),case,arm)
    edited={k:v for k,v in case.items() if k not in ('positive','truth_count')}
    second=m.infer_edges(net.eval(),edited,arm)
    assert first==second
    assert all(0<=i<case['edge_count'] for i in first)


def test_cuda_device_contract_on_cpu_with_real_optimizer():
    m.training_device_preflight('cpu')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA device not present')
def test_every_architecture_cuda_optimizer_and_inference():
    m.training_device_preflight('cuda')


def test_memorization_stratification_no_probe_leakage():
    cases,pairs=cohorts()
    slices,groups=b54.split_cohorts(cases,pairs)
    small,small_pairs=m.memorization_subset(slices,groups)
    assert len(small)==8 and len(small_pairs)==4
    assert {x['section'] for x in small}=={'counterfactual_train'}
    assert {(p['family'],p['mode']) for p in small_pairs}=={
        ('allocation','role_swap'),('allocation','renaming'),
        ('encounter','role_swap'),('encounter','renaming')}
    assert not {x['prompt_sha256'] for x in small}&{
        x['prompt_sha256'] for x in slices['train_fit']}


def test_gate_failure_prevents_evaluation_of_probes(monkeypatch):
    cases,pairs=cohorts()
    called=[]
    original=m.audit_arm
    def audit(model,samples,arm,pair_list,*,section):
        called.append(section)
        assert section=='memorization'
        return original(model,samples,arm,pair_list,section=section)
    monkeypatch.setattr(m,'audit_arm',audit)
    outcome,weights,policy=m.finalize(cases,pairs,
       {'exact_parent50_legacy_replay':True},
       {'interface_repair':{'decision':{'coverage_targets_met':True}}},
       device='cpu',gate_steps=1,full_steps=1,batch=2,
       parent54_report=synthetic_parent(),parent54_hashes={},parent48_sha='f'*64)
    assert outcome['results']=={}
    assert len(called)==3 and set(called)=={'memorization'}
    assert outcome['gates']['any_architecture_memorized'] is False
    assert outcome['training']['optimizer_steps_total']==3
    assert policy['enabled'] is False and policy['selected_development_arm'] is None
    assert any(k.startswith('set_prediction.') for k in weights)


def test_passing_gate_advances_only_passing_architecture(monkeypatch):
    cases,pairs=cohorts()
    original=m.train_model
    def fake_train(model,samples,arm,*,steps,device,batch,gate_subset=None):
        if gate_subset is not None:
            return model,[],1,len(gate_subset) if arm=='biaffine' else 0
        assert arm=='biaffine'
        return model,[],1,None
    # Force the audit metric to agree with the memorization gate so the
    # comparison proceeds. This is a logic test, not a claim of learned accuracy.
    original_audit=m.audit_arm
    def fake_audit(model,samples,arm,pair_list,*,section):
        rows,counts=original_audit(model,samples,arm,pair_list,section=section)
        if section in ('memorization','memorization_retention') and arm=='biaffine':
            counts['exact']=8
        return rows,counts
    monkeypatch.setattr(m,'train_model',fake_train)
    monkeypatch.setattr(m,'audit_arm',fake_audit)
    report,weights,policy=m.finalize(cases,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',gate_steps=1,full_steps=1,batch=2,
        parent54_report=synthetic_parent(),parent54_hashes={},parent48_sha='f'*64)
    assert report['gates']['architectures_allowed_to_generalize']==['biaffine']
    assert set(report['results'])=={'biaffine'}
    assert set(report['results']['biaffine'])=={
        'train','train_fit','counterfactual_probe','fresh_counterfactual_probe'}
    assert report['training']['optimizer_steps_total']==4
    assert policy['selected_development_arm']=='biaffine'


def test_missing_candidate_truth_never_misreported_as_exact():
    net=m.model_for('biaffine')
    s=sample();s['truth_count']=3
    rows,stats=m.audit_arm(net,[s],'biaffine',[],section='train')
    assert rows[0]['exact'] is False
    assert rows[0]['missing']>=1


def test_rejects_invalid_training_budgets_and_architectures():
    with pytest.raises(ValueError,match='UNKNOWN_ARCHITECTURE'):
        m.model_for('anything')
    with pytest.raises(ValueError,match='TRAIN_BUDGET'):
        m.train_model(m.BiaffineParser(),[sample()],'biaffine',steps=0,device='cpu',batch=1)


def test_public_cli_dispatches_new_runner_and_immutable_parent_names(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    from contextlib import redirect_stdout
    import io
    observed={}
    def fake_run(**kwargs):
        observed.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
           'memorization_gate':{},'results':{},'train_fit_selection':None,
           'gates':{'production_promotion':False}}}
    monkeypatch.setattr(m,'run',fake_run)
    with redirect_stdout(io.StringIO()) as stdout:
        main(['frob-g2-literature-bakeoff','--root',str(tmp_path),'--device','cpu'])
    assert observed['candidate52_run'].endswith('graph-g2-endpoint-interface-repair-0052')
    assert observed['candidate53_run'].endswith('graph-g2-repaired-graph-learning-0053')
    assert observed['candidate54_run'].endswith('graph-g2-architectural-bakeoff-0054')
    assert observed['output'].endswith('graph-g2-literature-bakeoff-0055')
    assert observed['memorization_steps']==240 and observed['full_steps']==320
    assert observed['batch_prompts']==2 and observed['device']=='cpu'
    assert m.STATUS in stdout.getvalue()


def test_production_runner_preflights_before_any_expensive_capture(monkeypatch,tmp_path):
    from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
    from safetensors.torch import save_file
    parent48=tmp_path/'parent48';parent48.mkdir()
    state=d48.OccurrenceEvidenceScorer(8).state_dict()
    save_file(state,str(parent48/d48.WEIGHTS))
    sha=d48.backbone.sha256(parent48/d48.WEIGHTS)
    (parent48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':sha}))
    parent52=tmp_path/'parent52';parent52.mkdir()
    seen=[]
    monkeypatch.setattr(g53,'verify_parent52',lambda folder:({},{}))
    monkeypatch.setattr(b54,'verify_parent53',lambda *args:({}, {}, {}))
    monkeypatch.setattr(m,'verify_parent54',lambda *args:(synthetic_parent(), {}))
    real=m.training_device_preflight
    def preflight(device):
        seen.append('device_preflight')
        real(device)
    def capture(**kwargs):
        seen.append('capture')
        assert kwargs['scoring_schema']==m.SCHEMA
        assert kwargs['scoring_report_name']==m.REPORT
        assert kwargs['scoring_ledger_name']==m.LEDGER
        assert kwargs['alternate_weights_filename']==m.WEIGHTS
        assert kwargs['alternate_policy_filename']==m.POLICY
        assert callable(kwargs['scoring_case_features'])
        assert callable(kwargs['scoring_finalize'])
        cases,pairs=cohorts()
        experiment,weights,policy=kwargs['scoring_finalize'](
            cases,pairs,{'exact_parent50_legacy_replay':True},
            {'interface_repair':{'decision':{'coverage_targets_met':True}}})
        assert experiment['training']['optimizer_steps_total']>0
        assert set(experiment['memorization_gate'])==set(m.ARMS)
        assert policy['enabled'] is False and bool(weights)
        return {'status':m.STATUS}
    monkeypatch.setattr(m,'training_device_preflight',preflight)
    monkeypatch.setattr(p52,'run',capture)
    output=m.run(candidate54_run='unused',candidate53_run='unused',candidate52_run=parent52,
        candidate51_run='unused',occurrence48_run=parent48,counterfactual50_run='unused',
        output=tmp_path/'report',device='cpu')
    assert output['status']==m.STATUS and seen==['device_preflight','capture']


def test_shared_capture_source_saves_0055_named_files(tmp_path,monkeypatch):
    """The existing real callback persists a 0055-named report/ledger/weights/policy."""
    import importlib.util
    import gzip
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
    source=Path(__file__).with_name('test_nanojev_gamer_v1_g2_repaired_graph_learning.py')
    spec=importlib.util.spec_from_file_location('_g2_0055_capture_reference',source)
    ref=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ref)
    for name,value in (('WEIGHTS',m.WEIGHTS),('POLICY',m.POLICY),
                       ('REPORT',m.REPORT),('LEDGER',m.LEDGER)):
        monkeypatch.setattr(g53,name,value)
    orig=p51.run
    def wrap(*args,**kwargs):
        kwargs['alternate_weights_filename']=m.WEIGHTS
        kwargs['alternate_policy_filename']=m.POLICY
        return orig(*args,**kwargs)
    monkeypatch.setattr(p51,'run',wrap)
    ref.test_real_0051_0052_0053_capture_callbacks_persist_scoring_artifacts(
        tmp_path,monkeypatch)
    root=tmp_path/'score'
    assert all((root/name).exists() for name in (m.REPORT,m.LEDGER,m.WEIGHTS,m.POLICY))
    assert bool(load_file(str(root/m.WEIGHTS)))
    with gzip.open(root/m.LEDGER,'rt') as input_file:
        assert len(list(input_file))==12


@pytest.mark.parametrize('arm',m.ARMS)
def test_large_candidate_inventory_backward_and_no_gold_in_inference(arm):
    """Exercise a realistically wide inventory, not only tiny 12-edge fixtures."""
    torch.manual_seed(23)
    n=96;edges=8192
    gen=torch.Generator().manual_seed(23)
    s={
        'node':torch.randn(n,96,generator=gen).half(),
        'kinds':torch.randint(len(g53.KIND_INDEX),(n,),generator=gen),
        'margin':torch.randn(n,generator=gen),
        'src':torch.randint(n,(edges,),generator=gen),
        'dst':torch.randint(n,(edges,),generator=gen),
        'relation':torch.randint(len(g53.RELATIONS),(edges,),generator=gen),
        'geometry':torch.randn(edges,5,generator=gen),
        'positive':torch.arange(0,26,dtype=torch.long),
        'truth_count':26,'edge_count':edges}
    model=m.model_for(arm)
    loss=m.objective(model,s,arm)
    assert bool(torch.isfinite(loss))
    loss.backward()
    assert any(p.grad is not None and bool(torch.isfinite(p.grad).all()) for p in model.parameters())
    without_labels={k:v for k,v in s.items() if k not in ('positive','truth_count')}
    predicted=m.infer_edges(model.eval(),without_labels,arm)
    assert all(0<=i<edges for i in predicted)


def test_two_graph_real_memorization_learns_exact_without_oracle():
    # End-to-end optimization and exact-set decoding, no mocked arm success.
    examples=[sample(name='overfit',side=0),sample(name='overfit',side=1)]
    for arm in m.ARMS:
        torch.manual_seed(554)
        model,trace,step,exact=m.train_model(m.model_for(arm),examples,arm,
            steps=120,device='cpu',batch=2,gate_subset=examples)
        assert exact==2, (arm,step,trace)
        assert all(m.infer_edges(model,s,arm)==set(s['positive'].tolist()) for s in examples)


def test_parent54_validation_hashes_actual_report_ledger_and_weights(tmp_path):
    from safetensors.torch import save_file
    source=tmp_path/'0054';source.mkdir()
    parent53=tmp_path/'0053';parent53.mkdir()
    parent52=tmp_path/'0052';parent52.mkdir()
    h=g53.p52.p51.SOURCE.backbone.sha256
    (parent53/g53.REPORT).write_text('official parent 0053 test report')
    (parent53/g53.WEIGHTS).write_bytes(b'parent 0053 scorer weights')
    (source/b54.LEDGER).write_bytes(b'fake ledger row\n')
    save_file({'check':torch.ones(2)},str(source/b54.WEIGHTS))
    record={'schema':b54.SCHEMA,'status':b54.STATUS,
        'production_promotion':False,'sealed_test_used':False,
        'scoring_weights_sha256':h(source/b54.WEIGHTS),
        'ledger_sha256':h(source/b54.LEDGER),
        'scoring_experiment':{
            'design':{'oracle_accesses_gold_only_for_ceiling_audit':True},
            'parent53_hashes':{'g2_0053_report_sha256':h(parent53/g53.REPORT),
                               'g2_0053_weights_sha256':h(parent53/g53.WEIGHTS)},
            'results':{'counterfactual_probe':{'Oracle_coverage_ceiling':{'exact':31}},
                       'fresh_counterfactual_probe':{'Oracle_coverage_ceiling':{'exact':32}}}}}
    (source/b54.REPORT).write_text(json.dumps(record))
    doc,hashes=m.verify_parent54(source,parent53,parent52)
    assert len(hashes)==3 and doc['schema']==b54.SCHEMA
    (source/b54.LEDGER).write_bytes(b'CORRUPT')
    with pytest.raises(ValueError,match='PARENT54_CONTRACT_OR_HASH'):
        m.verify_parent54(source,parent53,parent52)


def test_rectangular_hungarian_matches_reference_optimum():
    import numpy as np
    linear_sum_assignment=pytest.importorskip('scipy.optimize').linear_sum_assignment
    rng=np.random.default_rng(251)
    for shape in ((64,39),(39,64),(4,3),(3,4),(1,8),(8,1)):
        costs=rng.random(shape)*20-10
        rows,cols=m.rectangular_assignment(costs)
        rr,cc=linear_sum_assignment(costs)
        assert len(rows)==len(set(rows))==min(shape)
        assert len(cols)==len(set(cols))==min(shape)
        assert np.isclose(costs[rows,cols].sum(),costs[rr,cc].sum())


def test_memorization_forgetting_after_full_training_blocks_generalization(monkeypatch):
    cases,pairs=cohorts()
    real_audit=m.audit_arm
    def fake_train(model,samples,arm,*,steps,device,batch,gate_subset=None):
        return model,[],1,(len(gate_subset) if arm=='biaffine' else 0) if gate_subset is not None else None
    def fake_audit(model,samples,arm,pair_list,*,section):
        assert section in ('memorization','memorization_retention')
        rows,stats=real_audit(model,samples,arm,pair_list,section=section)
        stats['exact']=8 if section=='memorization' and arm=='biaffine' else 0
        return rows,stats
    monkeypatch.setattr(m,'train_model',fake_train)
    monkeypatch.setattr(m,'audit_arm',fake_audit)
    result,weights,policy=m.finalize(cases,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',gate_steps=1,full_steps=1,batch=2,
        parent54_report=synthetic_parent(),parent54_hashes={},parent48_sha='f'*64)
    assert result['memorization_gate']['biaffine']['pass'] is True
    assert result['memorization_gate']['biaffine']['retained_after_full'] is False
    assert result['results']=={}
    assert result['gates']['architectures_allowed_to_generalize']==[]
