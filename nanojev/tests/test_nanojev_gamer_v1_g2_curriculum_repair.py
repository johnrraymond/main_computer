"""G2-0037 constrained TRAIN curriculum, safeguards, and execution regression."""
from pathlib import Path
import inspect
import json
import random
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_curriculum_repair as m


def _row(name, split='train', family='encounter'):
    return {'prompt':name,'split':split,'family':family,'structural_topology':'2x2',
            'graph':{'nodes':[{'id':'a','kind':'actor','span':[0,1]},
                              {'id':'b','kind':'action','span':[2,3]}],
                     'edges':[{'src':'a','dst':'b','type':next(iter(m.base.BINDINGS))}]}}


def _evidence(positive=False,useful=False):
    a=(0,1,'actor');b=(2,3,'action');junk=(7,8,'actor')
    initial=[a] if useful else [junk]
    return {'baseline_anchors':initial,'catalog_anchors':set(initial)|{b},
            'anchors':[b] if positive else [junk],
            'features':torch.zeros(1,m.x36.FEATURE_DIM),
            'spans':[{'token_first':2,'token_last':2,'kind':'action'}],
            'baseline_spans':[{'token_first':0,'token_last':0,'kind':'actor'}]}


def test_protocol_and_label_boundary():
    assert m.TRAIN==300 and m.CAL_FIT==120 and m.CAL_AUDIT==60
    assert m.POOL_LIMITS[0]>=m.TRAIN and m.POOL_LIMITS[1]>=m.CAL_FIT
    assert m.POOL_LIMITS[2]>=m.CAL_AUDIT
    assert m.RICH_QUOTAS[0]>m.x36.TRAIN_STEPS//5
    assert set(m.REPLAY)==set(m.x36.FRESH)
    assert not set(m.FRESH)&set(m.REPLAY)
    for fn in (m.novel_train_pool,m.fresh_cohorts):
        code=inspect.getsource(fn)
        assert "'challenge'" not in code and "'test'" not in code
    assert 'x36.analyze_case' in inspect.getsource(m.run)
    assert 'verify_parent36(' in inspect.getsource(m.run)
    assert 'parent35=(parent36[split]' in inspect.getsource(m.run)


def test_novel_train_pool_disjoint_and_reproducible():
    prior=[_row('prior training'),_row('old dev',split='dev')]
    pool=m.novel_train_pool(prior,limits=(8,8,8),seed=7)
    assert [len(group) for group in pool]==[8,8,8]
    rows=[r for group in pool for r in group]
    assert all(r['split']=='train' for r in rows)
    assert len({r['prompt'] for r in rows})==24
    assert set(r['prompt'] for r in rows).isdisjoint(r['prompt'] for r in prior)
    assert all(r['structural_topology'] not in {'2x4','4x2','4x4'} for r in rows)
    assert [m.base._sha(r['prompt']) for r in rows]==[
        m.base._sha(r['prompt']) for group in m.novel_train_pool(prior,limits=(8,8,8),seed=7)
        for r in group]
    with pytest.raises(ValueError,match='POOL_PLAN'):
        m.novel_train_pool(prior,limits=(2,2))


def test_useful_candidate_only_when_counterpart_exists():
    row=_row('synthetic')
    stats=m.useful_new_endpoints(_evidence(True,True),row)
    assert stats['positive']==1 and stats['useful']==1
    assert m.useful_new_endpoints(_evidence(True,False),row)['useful']==0
    assert m.useful_new_endpoints(_evidence(False,True),row)['positive']==0


def test_train_miner_separate_balanced_cohorts_and_replaces_unhelpful_positive():
    # The first positive cases are not actionable; later ones are. Miner must
    # retain a previously unselected useful positive instead of freezing the
    # first positive quota and declaring a false shortage.
    pools=[[ _row(f'case{i}_{j}',family=('encounter' if j%2==0 else 'allocation'))
             for j in range(14)] for i in range(3)]
    seen=set()
    def freeze(row,*args,**kwargs):
        cohort,j=(int(x) for x in row['prompt'].replace('case','').split('_'))
        seen.add(row['prompt'])
        return _evidence(j<8,useful=j>=3),None
    selected,audit=m.mine_train_curriculum(pools,None,None,None,
        device='cpu',max_length=m.MAX_LENGTH,sizes=(7,7,7),
        rich_quotas=(3,3,3),min_useful=(2,2,2),freeze=freeze)
    for rows,entry in zip(selected,audit.values()):
        assert len(rows)==7
        assert entry['selected_rich_cases']==3
        assert entry['selected_useful_cases']>=2
        assert entry['selected_negative_control_cases']==4
        assert entry['selected_positive_proposals']>=3
        assert entry['training_examples_only']
    all_rows=[row['prompt'] for group in selected for evidence,row in group]
    assert len(set(all_rows))==len(all_rows)
    assert len(seen)>=21
    with pytest.raises(ValueError,match='INSUFFICIENT_TRAIN_POSITIVES'):
        m.mine_train_curriculum([[ _row(f'empty{x}_{i}') for i in range(5)] for x in range(3)],
            None,None,None,device='cpu',max_length=m.MAX_LENGTH,
            sizes=(4,4,4),rich_quotas=(3,3,3),min_useful=(1,1,1),
            freeze=lambda row,*a,**k:(_evidence(False),None))


def test_miner_does_not_read_development_labels_or_hidden_gold():
    src=inspect.getsource(m.mine_train_curriculum)
    assert "row['split']='dev'" not in src
    assert 'dev_rows' not in src
    assert 'useful_new_endpoints(evidence,row)' in src
    assert 'freeze(row,head,model,tokenizer' in src


def test_policy_grid_discloses_rejected_candidates_and_abstains(monkeypatch):
    def stats(cases,net,cfg):
        if cfg['threshold']==.75 and cfg['max_extra']==1:
            return {'true_new_endpoints':4,'false_new_endpoints':0,
                    'newly_reachable_gold_edges':4,'admission_precision':1.}
        return {'true_new_endpoints':0,'false_new_endpoints':6,
                'newly_reachable_gold_edges':0,'admission_precision':0.}
    monkeypatch.setattr(m.x36,'calibration_metrics',stats)
    # Selector must pass independent TRAIN audit (function receives distinct
    # objects; this test also verifies grid evidence is retained).
    nets={seed:object() for seed in m.SEEDS}
    policies,audit=m.fit_policies([('fit',0)],[('audit',0)],nets)
    for seed in m.SEEDS:
        entry=audit[str(seed)]
        assert len(entry['fit_grid'])==len(m.x36.THRESHOLDS)*len(m.x36.BUDGETS)
        assert entry['eligible_fit_policies']==1
        assert entry['audit_evaluated']
        assert policies[f'acceptor_s{seed}']['enabled'] is True
    monkeypatch.setattr(m.x36,'calibration_metrics',lambda *a,**k:{
        'true_new_endpoints':0,'false_new_endpoints':0,
        'newly_reachable_gold_edges':0,'admission_precision':0.})
    disabled,records=m.fit_policies([('fit',0)],[('audit',0)],nets)
    assert all(not p['enabled'] for p in disabled.values())
    assert all(r['fit'] is None and r['audit'] is None and not r['audit_evaluated']
               for r in records.values())


def test_train_acceptor_sufficient_positives_and_frozen_weights():
    torch.set_num_threads(1)
    rng=torch.Generator().manual_seed(123)
    positives=torch.randn(70,m.x36.FEATURE_DIM,generator=rng)
    negatives=torch.randn(70,m.x36.FEATURE_DIM,generator=rng)
    positives[:,0]+=4
    features=torch.cat((positives,negatives))
    labels=torch.cat((torch.ones(70),torch.zeros(70)))
    networks,log=m.x36.train_acceptors([({'features':features},labels)],steps=6)
    assert all(log[str(s)]['train_positive']==70 for s in m.SEEDS)
    assert all(not any(p.requires_grad for p in net.parameters()) for net in networks.values())


def test_parent36_refuses_missing_schema(tmp_path):
    (tmp_path/m.x36.REPORT).write_text(json.dumps({'schema':'wrong'}))
    with pytest.raises(ValueError,match='PARENT_0036_CONTRACT:schema'):
        m.verify_parent36(tmp_path,ancestors={},train_sha='',train36=[],fit36=[],
            audit36=[],old_dev={},fresh36={})


def test_cli_command_and_dependencies():
    result=subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
        'frob-g2-occurrence-curriculum','--help'],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--selective36-run' in result.stdout
    assert '--device' in result.stdout


def test_mocked_end_to_end_creates_immutable_result(monkeypatch,tmp_path):
    # Full G2-0037 orchestration with frozen-decision paths replaced only at
    # expensive external interfaces. Includes checkpoint, ledger and policies.
    row=_row('fresh sample',split='dev')
    train=_row('train sample')
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
    monkeypatch.setattr(m.x36,'fresh_cohorts',lambda *a:(([],[],[]),
        {split:[row] for split in m.REPLAY}))
    monkeypatch.setattr(m,'verify_parent36',lambda *a,**k:({split:[{}] for split in m.REPLAY},{}))
    monkeypatch.setattr(m,'fresh_cohorts',lambda *a:(([train],[train],[train]),
        {split:[row] for split in m.FRESH}))
    monkeypatch.setattr(m.x32,'load_parent_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x33,'load_parent32_models',lambda *a,**k:{})
    monkeypatch.setattr(m.x34,'load_parent33_models',lambda *a,**k:{})
    evidence=_evidence(True,True)
    monkeypatch.setattr(m,'mine_train_curriculum',lambda *a,**k:(
        [[(evidence,train)] for _ in range(3)],{'train_0037':{},'train_fit_0037':{},'train_audit_0037':{}}))
    class Tiny(torch.nn.Module):
        def __init__(self):super().__init__();self.weight=torch.nn.Parameter(torch.ones(1))
        def forward(self,x):return x[:,0]*self.weight
    monkeypatch.setattr(m.x36,'train_acceptors',lambda *a:({s:Tiny().eval().requires_grad_(False)
       for s in m.SEEDS},{str(s):{} for s in m.SEEDS}))
    monkeypatch.setattr(m,'fit_policies',lambda *a:({f'acceptor_s{s}':{
        'enabled':False,'threshold':.97,'max_extra':0} for s in m.SEEDS},{}))
    monkeypatch.setattr(m.x36,'freeze_case',lambda *a,**k:(evidence,({},torch.zeros(2,8),None)))
    def analyze(row,*args,**kwargs):
        metrics={f'{arm}__{mode}_s{seed}':{'correct':0,'false_positive':0,
            'missing':1,'exact_cases':0,'competing_gold':0,'ranking_failures':0}
            for arm in ('baseline','selective') for mode in ('group','structured')
            for seed in m.SEEDS}
        cov={'gold_endpoint_count':2,'catalog_gold_endpoint_count':2,
            'baseline_gold_endpoint_count':1,'unrecoverable_gold_endpoints':0,
            'actionable_catalog_gold_endpoint_count':2,'lost_gold_endpoints_to_catalog_cap':0,
            'catalog_candidate_count':1}
        for arm in ('baseline',*[str(s) for s in m.SEEDS]):
            cov[arm]={'selected_anchors':1,'new_gold_anchors':0,'new_false_anchors':0,
                'gold_edges_reachable_by_endpoints':0,'represented_gold_edges':0,
                'fully_representable':False}
        return {'group':kwargs['split'],'index':kwargs['index'],
            'prompt_sha256':m.base._sha(row['prompt']),'family':row['family'],
            'gold_total':1,'candidate_gold':0,'metrics':metrics,'coverage':cov}
    monkeypatch.setattr(m.x36,'analyze_case',analyze)
    monkeypatch.setattr(m.x31.rel,'validate_original_state',lambda *a:None)
    path=tmp_path/'experiment'
    args=dict(precision30_run='p30',representation31_run='p31',
        completeness32_run='p32',structured33_run='p33',
        diagnosis34_run='p34',coverage35_run='p35',selective36_run='p36',
        output=str(path),device='cpu')
    report=m.run(**args)
    assert report['evaluation']['new_0037_dev']['cases']==2
    assert report['train_cases']==1 and report['train_fit_cases']==1
    assert report['dev_labels_used_for_training'] is False
    assert report['production_promotion'] is False
    for name in (m.REPORT,m.LEDGER,m.POLICIES,m.WEIGHTS):
        assert (path/name).is_file()
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(**args)
