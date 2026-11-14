"""G2-0020: frozen, gold-blind coverage and scorer/cap diagnostics."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import math
import subprocess
import sys

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as m
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES


def test_cli_and_prelocked_nonpromotion_protocol():
    p = subprocess.run([sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
            'frob-g2-structural-ceiling-validation','--help'],
            cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=60)
    assert p.returncode == 0,p.stderr
    assert '--train19-run' in p.stdout and '--output' in p.stdout
    assert '--steps' not in p.stdout
    assert m.TOP_KS == (24,48)
    assert m.REFERENCE == 'precision_guard' and m.THRESHOLD == .75
    assert m.FRESH_INDEPENDENT == 320 and m.FRESH_TOPOLOGY == 128


def test_stage_partition_tracks_upstream_candidate_score_and_cap_losses():
    src=(0,2,'action')
    a=(3,4,'action');b=(5,6,'action');c=(7,8,'action')
    d=(10,11,'action')
    e1=(src,a,'owns');e2=(src,b,'owns');e3=(src,c,'owns')
    e4=(src,d,'owns'); missing=((99,100,'action'),b,'owns')
    gold={e1,e2,e3,e4,missing}
    inv24={'edges':[e1,e2,e4]}
    inv48={'edges':[e1,e2,e4,e3]}
    p24=torch.tensor([2.,-2.,2.])
    p48=torch.tensor([2.,2.,-2.,3.])
    cutoff=math.log(.75/.25)
    # Decoder cap deliberately omits e4 despite a score above cutoff.
    stage=m.stage_attribution(gold,{src,a,b,c,d},inv24,inv48,p24,p48,
            {e1},{e1,e2,e3},cutoff=cutoff)
    assert stage['gold_total']==5
    assert stage['g1_missing_endpoint']==1
    assert stage['top24_excluded_but_top48_reachable']==1
    assert stage['top24_threshold_rejected_gold']==1
    assert stage['top24_decoder_cap_rejected_gold']==1
    assert stage['top48_threshold_rejected_gold']==1
    assert stage['top48_decoder_cap_rejected_gold']==0
    assert stage['new_top48_true_positive']==1
    assert stage['top48_true_positive']==3
    with pytest.raises(ValueError,match='NONNESTED|BAD_CANDIDATE'):
        m.stage_attribution(gold,{src,a,b,c,d},inv24,{'edges':[e1,e2,e3]},p24,
            torch.ones(3),{e1},{e1},cutoff=cutoff)


def test_nested_inventories_are_label_blind_and_core_features_identical(monkeypatch):
    torch.manual_seed(2)
    anchors=[(i*5,i*5+2,'action') for i in range(56)]
    k=BIND_CLASSES.index('owns')
    rank=torch.zeros(56,56,len(BIND_CLASSES))
    for i in range(56):
        for j in range(56):rank[i,j,k]=56-j
    allowed={('owns','action','action')}
    gold={(anchors[0],anchors[1],'owns'),(anchors[0],anchors[30],'owns'),
          ((777,778,'action'),anchors[2],'owns')}
    case={'anchors':anchors,'vectors':torch.randn(56,8),'gold':gold,
          'family':'encounter','prompt_sha256':base._sha('G2 0020 heldout')}
    row={'prompt':'G2 0020 heldout','family':'encounter',
         'structural_topology':'4x4'}
    class Scorer(torch.nn.Module):
        def forward(self,features,base,groups,*,with_competition=True):
            assert with_competition
            return base/16
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:rank)
    r=m.analyze_case(case,row,None,Scorer(),Scorer(),allowed,
                     split='fresh_topology_dev',index=1,device='cpu')
    assert r['candidate_inventory']['top48']>r['candidate_inventory']['top24']
    assert r['stage_losses']['top24_excluded_but_top48_reachable']>=1
    assert r['stage_losses']['g1_missing_endpoint']==1
    assert r['configurations']['precision_guard_top24']==r['configurations']['baseline_0016_top24']
    assert r['stage_losses']['top48_reachable_gold']>=r['stage_losses']['top24_reachable_gold']
    case_with_fake_labels=deepcopy(case)
    case_with_fake_labels['gold']=set()
    inv=source13.make_inventory(case,rank,allowed,top_k=48,train_gold=False)
    fake=source13.make_inventory(case_with_fake_labels,rank,allowed,top_k=48,train_gold=False)
    assert inv['edges']==fake['edges'] and torch.equal(inv['features'],fake['features'])


def test_exposed_replay_detects_drift(monkeypatch):
    anchors=[(0,2,'action'),(3,5,'action')]
    rank=torch.zeros(2,2,len(BIND_CLASSES))
    rank[0,1,BIND_CLASSES.index('owns')]=4.
    case={'anchors':anchors,'vectors':torch.randn(2,8),'gold':set(),
          'family':'encounter','prompt_sha256':base._sha('replay contract')}
    row={'prompt':'replay contract','family':'encounter'}
    class Net(torch.nn.Module):
        def forward(self,x,b,g,*,with_competition=True):
            return torch.full_like(b,3.)
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:rank)
    allowed={('owns','action','action')}
    first=m.analyze_case(case,row,None,Net(),Net(),allowed,
                         split='new_independent_validation',index=1,device='cpu')
    parent={'group':'new_independent_validation','index':1,
            'prompt_sha256':case['prompt_sha256'],
            'configurations':{
                'baseline_0016':deepcopy(first['configurations'][m.CONFIGS[0]]),
                'precision_guard':deepcopy(first['configurations'][m.CONFIGS[1]])}}
    result=m.analyze_case(case,row,None,Net(),Net(),allowed,
                          split='new_independent_validation',index=1,device='cpu',previous=parent)
    assert result['configurations']==first['configurations']
    parent['configurations']['precision_guard']['correct']+=1
    with pytest.raises(ValueError,match='REPLAY_DRIFT'):
        m.analyze_case(case,row,None,Net(),Net(),allowed,
                       split='new_independent_validation',index=1,device='cpu',previous=parent)


def test_guard_weights_verify_both_frozen_arms_and_sha(tmp_path):
    torch.manual_seed(17)
    trained=source13.SourceConditionedScorer(8)
    d={arm+'.'+name:(v.detach().cpu()+ (0 if arm=='matched_context' else 1)).contiguous()
       for arm in train19.ARMS[1:] for name,v in trained.state_dict().items()}
    weightpath=tmp_path/'weights.safetensors'
    save_file(d,str(weightpath))
    good=backbone.sha256(weightpath)
    guarded=m.load_guarded(weightpath,8,good)
    assert not any(p.requires_grad for p in guarded.parameters())
    for name,value in guarded.state_dict().items():
        assert torch.equal(value,d['precision_guard.'+name])
    with pytest.raises(ValueError,match='WEIGHT_HASH_DRIFT'):
        m.load_guarded(weightpath,8,'wrong')
    with pytest.raises(ValueError,match='WEIGHT_KEY_DRIFT'):
        save_file({k:v for k,v in d.items() if k!='matched_context.attention.bias'},
                  str(tmp_path/'bad.safetensors'))
        m.load_guarded(tmp_path/'bad.safetensors',8,
                       backbone.sha256(tmp_path/'bad.safetensors'))


def test_new_cohort_exclusions_and_fixed_protocol(monkeypatch):
    one={'prompt':'past','family':'encounter','split':'dev','structural_topology':'3x3'}
    observed=[]
    def fresh(prior,*,limit,seed):
        assert limit==320 and seed==460020
        assert any(r['prompt']=='past' for group in prior for r in group)
        observed.append('independent')
        return [{'prompt':'fresh-'+str(i),'family':'allocation','split':'dev'} for i in range(limit)]
    def topology(prior,*,limit,seed):
        assert limit==128 and seed==460021
        assert any(r['prompt']=='fresh-0' for group in prior for r in group)
        observed.append('topology')
        return [{'prompt':'topology-'+str(i),'family':'encounter','split':'dev',
                 'structural_topology':('4x4','4x2','2x4')[i%3]} for i in range(limit)]
    monkeypatch.setattr(m.general16,'independent_rows',fresh)
    monkeypatch.setattr(m.general16,'heldout_topology_rows',topology)
    result=m.fresh_cohorts([one])
    assert observed==['independent','topology']
    assert len(result['fresh_topology_dev'])==128
    with pytest.raises(ValueError,match='FIXED_FRESH_PROTOCOL'):
        m.fresh_cohorts([one],independent_limit=10)
    monkeypatch.setattr(m.general16,'independent_rows',lambda prior,*,limit,seed:
                        [{'prompt':'past','family':'allocation','split':'dev'}]*320)
    with pytest.raises(ValueError,match='PROMPT_OVERLAP'):
        m.fresh_cohorts([one])


def test_summary_attribution_and_fail_closed_parent_and_output(tmp_path):
    stage={'gold_total':2,'g1_missing_endpoint':1,'top24_true_positive':1}
    record={'group':'fresh_independent_dev','family':'encounter',
            'configurations':{arm:sel.score_set(set(),{1,2}) for arm in m.CONFIGS},
            'stage_losses':stage,'candidate_inventory':{'top24':3,'top48':5,'added':2},
            'context_drift':{'candidates':3,'absolute_score_drift_max':.3}}
    agg=m.aggregate([record,record])
    assert agg['stage_losses']['gold_total']==4
    assert agg['candidate_inventory']['added']==4
    assert agg['paired_score_drift']['absolute_score_drift_max']==.3
    with pytest.raises(ValueError,match='EMPTY_AGGREGATION'):
        m.aggregate([])
    (tmp_path/train19.REPORT).write_text('{"schema":"wrong"}')
    with pytest.raises(ValueError,match='PARENT_0019_CONTRACT'):
        m.verify_parent_0019(tmp_path,{}, {}, {},
                             expected_train_sha='a',expected_datasets={})
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,output=tmp_path,g1_validation_report=None,g2_run=None,
              selection_run=None,discrimination_run=None,relational_run=None,
              density_run=None,consistency_run=None,context_run=None,
              learned_run=None,recall_run=None,crowded_run=None,
              robustness_run=None,diagnosis_run=None,source_run=None,
              specialization_run=None,routing_robustness_run=None,
              general_run=None,ceiling_run=None,context18_run=None,train19_run=None)


def test_complete_0019_parent_replay_happy_path_and_gate_tamper(tmp_path):
    # This synthetic 0019 ledger also exercises the real calibration+DEV gate
    # contract, which cannot be checked by a contract-failure-only test.
    splits=m.EXPOSED
    rows={'calibration':[{'prompt':'cal-'+str(i),'family':'allocation',
                          'structural_topology':'native'} for i in range(160)],
          splits[0]:[{'prompt':'iid-0','family':'allocation','structural_topology':'native'}],
          splits[1]:[{'prompt':'ood-'+str(i),'family':'encounter','structural_topology':top}
                     for i,top in enumerate(('2x4','4x2','4x4'))]}
    def score(correct,miss,extra):
        return {'correct':correct,'missing':miss,'false_positive':extra,
                'exact':not (miss or extra)}
    def make_record(row,split,index):
        return {'group':split,'index':index,'family':row['family'],
                'prompt_sha256':base._sha(row['prompt']),
                'structural_topology':row['structural_topology'],
                'density_bucket':'medium_25_48',
                'configurations':{'baseline_0016':score(1,2,3),
                                  'matched_context':score(2,1,2),
                                  'precision_guard':score(3,0,1)}}
    ledger={name:[make_record(row,name,i) for i,row in enumerate(batch,1)]
            for name,batch in rows.items()}
    prior={name:[{'configurations':{'general_75':r['configurations']['baseline_0016']}}
                 for r in records] for name,records in ledger.items()}
    gates=train19.predeclared_gates(ledger)
    (tmp_path/train19.WEIGHTS).write_bytes(b'synthetic checkpoint for lineage test')
    raw={
        'schema':train19.SCHEMA,
        'status':'G2_INFERENCE_REALISTIC_TRAINING_DEV_NOT_CERTIFIED',
        'parent_0016_selected':m.ceiling17.GENERAL,
        'candidate_top_k':24,'fixed_threshold':.75,
        'train_arms':list(train19.ARMS[1:]),
        'train_dataset_sha256':'train-sha','parent_hashes':{},
        'baseline_0016_frozen':True,'backbone_frozen':True,'g1_frozen':True,
        'rank_head_frozen':True,'original_training_data_only':True,
        'train_inventory_gold_injection_disabled':True,
        'candidate_and_group_selection_label_blind':True,
        'selection_on_exposed_dev':False,'checkpoint_selected':False,
        'no_density_specific_inference_router':True,
        'previously_exposed_development_only':True,
        'withheld_topologies_dev_only':True,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'certification_authorized':False,
        'memory_writes':False,
        'new_weights_sha256':backbone.sha256(tmp_path/train19.WEIGHTS),
        'train':{'candidate_top_k':24,'gold_in_candidate_construction':False,
                 'label_blind_full_inference_groups':True,
                 'same_initial_weights_and_training_batches':True,
                 'multi_positive_supervision':True,'train_cases':640,'steps':train19.STEPS},
        'datasets_sha256':{'calibration':'cal-sha',splits[0]:'ind-sha',splits[1]:'ood-sha'},
        'evaluation':{name:train19.aggregate(part) for name,part in ledger.items()},
        'by_family':{name:{fam:train19.aggregate([r for r in part if r['family']==fam])
                            for fam in ('encounter','allocation')
                            if any(r['family']==fam for r in part)}
                     for name,part in ledger.items()},
        'by_density':{name:{'medium_25_48':train19.aggregate(part)}
                      for name,part in ledger.items()},
        'by_topology':{top:train19.aggregate([r for r in ledger[splits[1]]
                                              if r['structural_topology']==top])
                       for top in ('2x4','4x2','4x4')},
        'diagnostic_gates':gates,
    }
    report=tmp_path/train19.REPORT
    casepath=tmp_path/train19.CASES
    casepath.write_text(''.join(json.dumps(r)+'\n' for name in train19.SPLITS for r in ledger[name]))
    report.write_text(json.dumps(raw))
    records, hashes=m.verify_parent_0019(tmp_path,rows,prior,{},
                            expected_train_sha='train-sha',
                            expected_datasets=raw['datasets_sha256'])
    assert records==ledger
    assert hashes['g2_0019_report_sha256']==backbone.sha256(report)
    raw['diagnostic_gates']['calibration']['precision_guard']['f1_above_baseline']=False
    report.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='GATE_DRIFT'):
        m.verify_parent_0019(tmp_path,rows,prior,{},
                             expected_train_sha='train-sha',
                             expected_datasets=raw['datasets_sha256'])
    raw['diagnostic_gates']=gates
    report.write_text(json.dumps(raw))
    original_ledger=casepath.read_text()
    # Calibration records must be present and in the same order as G2-0019.
    casepath.write_text(''.join(json.dumps(r)+'\n' for name in splits for r in ledger[name]))
    with pytest.raises(ValueError,match='PARENT_0019_COUNT'):
        m.verify_parent_0019(tmp_path,rows,prior,{},
                             expected_train_sha='train-sha',
                             expected_datasets=raw['datasets_sha256'])
    casepath.write_text(original_ledger)
    tampered=deepcopy(ledger)
    tampered['calibration'][0]['prompt_sha256']='tampered-calibration'
    casepath.write_text(''.join(json.dumps(r)+'\n' for name in train19.SPLITS
                                for r in tampered[name]))
    with pytest.raises(ValueError,match='PARENT_0019_CASE_DRIFT'):
        m.verify_parent_0019(tmp_path,rows,prior,{},
                             expected_train_sha='train-sha',
                             expected_datasets=raw['datasets_sha256'])
    casepath.write_text(original_ledger)
    tampered=deepcopy(ledger)
    tampered['calibration'][0]['configurations']['precision_guard']['correct']+=1
    casepath.write_text(''.join(json.dumps(r)+'\n' for name in train19.SPLITS
                                for r in tampered[name]))
    with pytest.raises(ValueError,match='PARENT_0019_METRIC_DRIFT'):
        m.verify_parent_0019(tmp_path,rows,prior,{},
                             expected_train_sha='train-sha',
                             expected_datasets=raw['datasets_sha256'])
