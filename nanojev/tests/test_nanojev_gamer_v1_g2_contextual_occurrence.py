"""G2-0038 discriminative signal, nonleakage, calibration, and immutable run."""
from __future__ import annotations

import inspect
import json
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_contextual_occurrence as m


def _evidence():
    kind=m.x36.SPAN_KINDS[0]
    original={'token_first':1,'token_last':1,'kind':kind}
    gold={'token_first':4,'token_last':4,'kind':kind}
    negative={'token_first':5,'token_last':5,'kind':kind}
    return {'baseline_spans':[original],
        'baseline_anchors':[(1,2,kind)],
        'catalog_anchors':{(1,2,kind),(4,5,kind),(5,6,kind)},
        'spans':[gold,negative],
        'anchors':[(4,5,kind),(5,6,kind)],
        'features':torch.zeros(2,m.x36.FEATURE_DIM)}


def _prepared():
    prompt='A B C D E F G H'
    return {'prompt':prompt,'prompt_start':0,
            'offsets':[(i,i+1) for i in range(len(prompt))],
            'eligible':torch.ones(len(prompt),dtype=torch.bool)}


def _row(name='sample',split='train'):
    kind=m.x36.SPAN_KINDS[0]
    return {'prompt':name,'split':split,'family':'encounter',
        'structural_topology':'2x2','graph':{'nodes':[], 'edges':[]},
        'object_spans':[]}


def test_context_features_use_real_frozen_window_and_competitors(monkeypatch):
    evidence=_evidence();prep=_prepared()
    # Char-level offsets give a completely real occurrence_windows invocation.
    hidden=torch.randn(len(prep['prompt']),32,generator=torch.Generator().manual_seed(44))
    monkeypatch.setattr(m.base,'predicted_anchors',lambda spans,pr:[
        (s['token_first'],s['token_last']+1,s['kind']) for s in spans])
    ctx=m.contextual_features(evidence,prep,hidden)
    assert ctx.shape==(2,m.CONTEXT_DIM)
    assert torch.isfinite(ctx).all()
    # Equivalent call is deterministic; prompt state affects it, and the
    # original handcrafted features remain unchanged.
    assert torch.equal(ctx,m.contextual_features(evidence,prep,hidden))
    altered=hidden.clone();altered[4]=hidden[4]*-1
    assert not torch.equal(ctx,m.contextual_features(evidence,prep,altered))
    assert evidence['features'].shape==(2,m.x36.FEATURE_DIM)
    assert not ctx.requires_grad


def test_context_missing_and_duplicates_fail_closed(monkeypatch):
    e=_evidence()
    with pytest.raises(ValueError,match='CONTEXT_MISSING'):
        m.scoring_features(e,'contextual')
    e['spans']=[e['spans'][0],e['spans'][0]]
    monkeypatch.setattr(m.base,'predicted_anchors',lambda spans,prep:[
        (s['token_first'],s['token_last']+1,s['kind']) for s in spans])
    with pytest.raises(ValueError,match='DUPLICATE_CONTEXT_SPAN'):
        m.contextual_features(e,_prepared(),torch.ones(15,16))


def test_calibration_is_label_blind_up_to_admission(monkeypatch):
    e=_evidence();e['context_features']=torch.randn(2,m.CONTEXT_DIM)
    case=_row()
    k=e['spans'][0]['kind']
    monkeypatch.setattr(m.base,'gold_binding_edges',lambda r:
        {((1,2,k),(4,5,k),'binding')})
    class Score(torch.nn.Module):
        def forward(self,features):return torch.tensor([9.,-9.])
    monkeypatch.setattr(m.x36,'choose_spans',lambda e,n,p:
        [*e['baseline_spans'],e['spans'][0]] if p['enabled'] else e['baseline_spans'])
    stats=m.calibration_metrics([(e,case)],Score(),
        {'enabled':True,'threshold':.75,'max_extra':1},'contextual')
    assert stats['true_new_endpoints']==1
    assert stats['false_new_endpoints']==0
    assert stats['newly_reachable_gold_edges']==1
    assert stats['admission_precision']==1.


def test_model_shape_and_hard_negative_training():
    torch.set_num_threads(1)
    cases=[]
    for j in range(6):
        evidence=_evidence()
        original=torch.randn(2,m.x36.FEATURE_DIM,
                             generator=torch.Generator().manual_seed(22+j))
        contextual=torch.randn(2,m.CONTEXT_DIM,
                               generator=torch.Generator().manual_seed(99+j))
        original[0,0]+=3
        contextual[0,0]+=4
        evidence['features']=original;evidence['context_features']=contextual
        cases.append((evidence,_row(f'sample{j}')))
    old=m.x36.label_train_case
    try:
        m.x36.label_train_case=lambda e,r:torch.tensor([1.,0.])
        models,audit=m.train_models(cases,steps=4)
    finally:
        m.x36.label_train_case=old
    assert set(models)==set(m.VIEWS)
    for s in m.SEEDS:
        assert audit['contextual'][str(s)]['hard_negative_pairs']==6
        net=models['contextual'][s]
        assert net(m.scoring_features(cases[0][0],'contextual')).shape==(2,)
        assert not any(p.requires_grad for p in net.parameters())
    with pytest.raises(ValueError,match='CONTEXT_INPUT_SHAPE'):
        net(torch.ones(2,m.MODEL_INPUT-1))


def test_policy_grid_preserves_independent_train_gate(monkeypatch):
    monkeypatch.setattr(m,'ranking_audit',lambda *a,**k:{'top1_precision':0.5})
    def stats(cases,net,cfg,view):
        passed=cfg['threshold']==.85 and cfg['max_extra']==1
        audit=cases is heldout
        return {'true_new_endpoints':(1 if audit else 4) if passed else 0,
                'false_new_endpoints':0,'newly_reachable_gold_edges':3 if passed else 0,
                'admission_precision':1. if passed else 0.}
    heldout=object()
    monkeypatch.setattr(m,'calibration_metrics',stats)
    models={view:{s:object() for s in m.SEEDS} for view in m.VIEWS}
    policies,details=m.fit_policies(object(),heldout,models)
    assert len(details)==6
    assert all(not p['enabled'] and p['disabled_reason']=='failed_independent_train_audit'
               for p in policies.values())
    assert all(len(d['fit_grid'])==18 for d in details.values())
    assert all(d['audit']['true_new_endpoints']==1 for d in details.values())


def test_parent37_rejects_bad_schema(tmp_path):
    (tmp_path/m.x37.REPORT).write_text(json.dumps({'schema':'bad'}))
    with pytest.raises(ValueError,match='PARENT_0037_CONTRACT:schema'):
        m.verify_parent37(tmp_path,ancestors={},train_sha='',fresh37={},pool37=[])


def test_new_model_uses_no_author_input_during_context_extraction():
    src=inspect.getsource(m.contextual_features)
    assert 'base.gold_binding_edges(' not in src and 'row[' not in src
    assert 'occurrence_windows' in src
    assert 'competitors' in src
    assert 'selected' in src


def test_cli_wires_parent_0037():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-contextual-occurrence','--help'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--curriculum37-run' in result.stdout


def test_empty_early_exit_artifacts_are_immutable(tmp_path):
    networks={view:{seed:(m.ContextualAcceptor() if view=='contextual' else
              m.x36.OccurrenceAcceptor()).eval().requires_grad_(False)
              for seed in m.SEEDS} for view in m.VIEWS}
    path=tmp_path/'run'
    report=m.persist(path,{'schema':m.SCHEMA,'development_evaluation_skipped':True},
                     networks,{f'{view}_s{seed}':{'enabled':False}
                         for view in m.VIEWS for seed in m.SEEDS},[])
    assert path.joinpath(m.LEDGER).is_file()
    assert path.joinpath(m.WEIGHTS).is_file()
    assert path.joinpath(m.POLICIES).is_file()
    assert path.joinpath(m.REPORT).is_file()
    assert report['ledger_sha256']==m.backbone.sha256(path/m.LEDGER)
    with pytest.raises(FileExistsError):
        m.persist(path,report,networks,{},[])


def _mock_pipeline(monkeypatch,enable):
    row=_row('fresh example',split='dev')
    train=_row('TRAIN example')
    state={'ancestors':{},'training':[],'history29':[],'fresh29':{},
           'head':object(),'head_before':object(),
           'model':torch.nn.Identity(),'reference':torch.nn.Identity(),
           'reference_before':{},'tokenizer':object()}
    monkeypatch.setattr(m.p30,'_prepare',lambda **k:state)
    monkeypatch.setattr(m.p30,'calibration_and_dev',lambda *a:([],{}))
    monkeypatch.setattr(m.x31,'verify_parent30',lambda *a,**k:(None,
        {'calibrated_reference':{},m.x31.CHAMPION:{}},None,{}))
    monkeypatch.setattr(m.x31,'fresh_cohorts',lambda *a:([],{}))
    monkeypatch.setattr(m.x32,'verify_parent31',lambda *a,**k:(None,
        {'calibrated_reference':{},**{name:{} for name in m.x32.SOURCE_NAMES}},None,{},{}))
    monkeypatch.setattr(m.x32,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x33,'verify_parent32',lambda *a,**k:(None,{},None,{},{}))
    monkeypatch.setattr(m.x33,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x34,'verify_parent33',lambda *a,**k:(None,{},None,{},{}))
    monkeypatch.setattr(m.x34,'fresh_cohorts',lambda *a:{})
    monkeypatch.setattr(m.x35,'verify_parent34',lambda *a,**k:(None,{}))
    monkeypatch.setattr(m.x35,'fresh_cohorts',lambda *a:{})
    monkeypatch.setattr(m.x36,'verify_parent35',lambda *a,**k:({},{}))
    monkeypatch.setattr(m.x36,'fresh_cohorts',lambda *a:(([],[],[]),{}))
    monkeypatch.setattr(m.x37,'verify_parent36',lambda *a,**k:({},{}))
    monkeypatch.setattr(m.x37,'fresh_cohorts',lambda *a:([[],[],[]],
        {split:[row] for split in m.REPLAY}))
    monkeypatch.setattr(m,'verify_parent37',lambda *a,**k:(
        {split:[{}] for split in m.REPLAY},{}))
    monkeypatch.setattr(m.x37,'_previous_rows',lambda *a:[train])
    monkeypatch.setattr(m,'fresh_cohorts',lambda *a:(
        [[train],[train],[train]],{split:[row] for split in m.FRESH}))
    monkeypatch.setattr(m.x32,'load_parent_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x33,'load_parent32_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x34,'load_parent33_models',lambda *a,**k:{})
    e=_evidence();e['context_features']=torch.ones(2,m.CONTEXT_DIM)
    monkeypatch.setattr(m.x37,'mine_train_curriculum',lambda *a,**k:(
        [[(e,train)] for _ in range(3)],
        {'train_0038':{},'train_fit_0038':{},'train_audit_0038':{}}))
    class Tiny(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.ones(1))
        def forward(self,x):return x[:,0]*self.weight
    monkeypatch.setattr(m,'train_models',lambda *a:({
        view:{s:Tiny().eval().requires_grad_(False) for s in m.SEEDS}
        for view in m.VIEWS},{view:{} for view in m.VIEWS}))
    policy={f'{view}_s{s}':{'enabled':bool(enable and view=='contextual' and s==m.SEEDS[0]),
        'threshold':.75,'max_extra':1 if view=='contextual' else 0}
        for view in m.VIEWS for s in m.SEEDS}
    monkeypatch.setattr(m,'fit_policies',lambda *a:(policy,
        {f'{view}_s{s}':{'fit_rank':{'top1_precision':.5},
                        'audit_rank':{'top1_precision':.5}}
         for view in m.VIEWS for s in m.SEEDS}))
    monkeypatch.setattr(m.x31.rel,'validate_original_state',lambda *a:None)
    if enable:
        monkeypatch.setattr(m,'freeze_context_case',lambda *a,**k:(e,
            ({},torch.zeros(2,8),None)))
        def fake_analyze(row,*a,**k):
            metrics={f'{view}__{mode}_s{s}':{'correct':0,'false_positive':0,
                'missing':1,'exact_cases':0,'competing_gold':0,'ranking_failures':0}
                for view in ('baseline',*m.VIEWS) for mode in ('group','structured')
                for s in m.SEEDS}
            cov={'gold_endpoint_count':2,'catalog_gold_endpoint_count':2,
                'baseline_gold_endpoint_count':1,
                'actionable_catalog_gold_endpoint_count':2,
                'lost_gold_endpoints_to_catalog_cap':0,
                'unrecoverable_gold_endpoints':0,
                'catalog_candidate_count':2}
            for arm in ('baseline',*(f'{view}:{s}' for view in m.VIEWS for s in m.SEEDS)):
                cov[arm]={'selected_anchors':1,'new_gold_anchors':0,
                    'new_false_anchors':0,'gold_edges_reachable_by_endpoints':0,
                    'represented_gold_edges':0,'fully_representable':False}
            return {'group':k['split'],'index':k['index'],
                'prompt_sha256':m.base._sha(row['prompt']),
                'family':row['family'],'gold_total':1,'candidate_gold':0,
                'metrics':metrics,'coverage':cov}
        monkeypatch.setattr(m,'analyze_case',fake_analyze)
    return dict(precision30_run='p30',representation31_run='p31',
        completeness32_run='p32',structured33_run='p33',
        diagnosis34_run='p34',coverage35_run='p35',selective36_run='p36',
        curriculum37_run='p37',device='cpu')


@pytest.mark.parametrize('enable',[False,True])
def test_mocked_run_gate_controls_development_execution(monkeypatch,tmp_path,enable):
    args=_mock_pipeline(monkeypatch,enable)
    report=m.run(**args,output=str(tmp_path/'experiment'))
    assert report['development_evaluation_skipped'] is (not enable)
    assert report['sealed_test_used'] is False
    assert report['production_promotion'] is False
    assert len(report['train_policy_audit'])==6
    if enable:
        assert report['status']=='CONTEXTUAL_OCCURRENCE_DEV_NOT_CERTIFIED'
        assert len(report['by_split'])==4
        assert not report['development_gates']['contextual_improved_and_safe']
    else:
        assert report['status']=='TRAIN_CALIBRATION_UNSAFE_NO_DEV_EVALUATION'
        assert report['by_split']=={}
        assert report['development_gates']['contextual_improved_and_safe'] is False
    path=tmp_path/'experiment'
    assert m.backbone.sha256(path/m.LEDGER)==report['ledger_sha256']
    with pytest.raises(FileExistsError):
        m.run(**args,output=str(path))
