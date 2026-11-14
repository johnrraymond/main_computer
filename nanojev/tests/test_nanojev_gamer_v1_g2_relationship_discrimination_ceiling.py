"""G2-0021 frozen scorer and threshold-ceiling regression tests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import gzip
import json
import math
import subprocess
import sys

import pytest
import torch

from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as m
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES


def _edge(i):
    return ((0,1,'action'), (i*3+2,i*3+3,'action'), 'owns')


def _case(scores,gold,*,family='encounter',density='small_1_24',index=1):
    candidates=[{'edge':_edge(i), 'relation':'owns', 'score':float(s), 'gold':i in gold,
                 'competition':'few_2_4','relative':'leader' if i==0 else 'behind_1_plus'}
                for i,s in enumerate(scores)]
    return {'group':'synthetic_dev', 'index':index, 'family':family,
            'density_bucket':density, 'structural_topology':'native',
            'gold_total':len(gold)+1,'reachable_gold':len(gold),
            'candidates':candidates}


def test_cli_only_diagnostic_not_training():
    cmd=[sys.executable,'-m','tools.nanojev_gamer_v1_graph_curriculum',
         'frob-g2-relationship-discrimination-ceiling','--help']
    run=subprocess.run(cmd,cwd=Path(__file__).resolve().parents[1],
                       capture_output=True,text=True,timeout=60)
    assert run.returncode==0,run.stderr
    assert '--ceiling20-run' in run.stdout and '--output' in run.stdout
    assert '--steps' not in run.stdout and '--train' not in run.stdout.replace('--train19-run','')
    assert m.SEED==460022 and m.THRESHOLD==.75 and m.MIN_STRATUM_CASES==20


def test_best_f1_and_auc_separate_score_order_from_cutoff():
    good=_case([5.,4.,-1.,-3.],{0,1},index=1)
    summary=m.threshold_ceiling([good])
    assert summary['ranking']['roc_auc']==1.
    assert summary['ranking']['average_precision']==1.
    assert summary['candidate_coverage']==pytest.approx(2/3)
    assert summary['best_bounded_threshold']['logit_cutoff']==4.
    assert summary['best_bounded_threshold']['correct']==2
    assert summary['best_bounded_threshold']['missing']==1
    assert summary['oracle_perfect_candidate_ranking_f1']==pytest.approx(.8)
    assert summary['rank_vs_oracle_f1_gap']==pytest.approx(0.)
    # A reversed score ranking cannot be fixed with any global cutoff.
    wrong=_case([4.,3.,2.,1.],{2,3})
    low=m.threshold_ceiling([wrong])
    assert low['ranking']['roc_auc']==0.
    assert low['ranking']['inverted_pair_fraction']==1.
    assert low['rank_vs_oracle_f1_gap']>0.
    assert low['best_bounded_threshold']['f1']<low['oracle_perfect_candidate_ranking_f1']


def test_equal_score_ties_are_not_artificially_separable():
    row=_case([1.,1.,1.,1.],{0,2})
    result=m.threshold_ceiling([row])
    assert result['ranking']['roc_auc']==.5
    assert result['ranking']['average_precision']==.5
    assert result['best_bounded_threshold']['correct']==2
    assert result['best_bounded_threshold']['false_positive']==2
    assert result['best_bounded_threshold']['logit_cutoff']==1.
    assert m.decode_case(row,1.000001)['correct']==0


def test_oracle_uses_candidate_only_inventory_and_graph_edge_cap():
    # At a tied cutoff, the existing graph cap keeps lexically first edges.
    row=_case([2.]*(MAX_EDGES+2),{MAX_EDGES,MAX_EDGES+1})
    out=m.decode_case(row,2.)
    assert out['correct']==0 and out['false_positive']==MAX_EDGES
    ceiling=m.threshold_ceiling([row])
    assert ceiling['best_bounded_threshold']['f1']==0.
    assert ceiling['oracle_perfect_candidate_ranking_f1']>0.
    assert ceiling['oracle_reachable_gold']==2


def test_score_quantiles_competitor_strata_and_density_diagnostics():
    rows=[_case([2.,-1.,-2.],{0},index=i,
                density='crowded_49_128' if i%2 else 'small_1_24',
                family='allocation' if i%3 else 'encounter') for i in range(1,7)]
    result=m.diagnosis(rows)
    assert result['cases']==6
    assert set(result['by_density'])=={'small_1_24','crowded_49_128'}
    assert set(result['by_competition_size'])=={'few_2_4'}
    assert set(result['by_competitor_position'])=={'leader','behind_1_plus'}
    assert result['overall']['score_quantiles']['gold']['0.5']==2.
    assert result['fixed_075']['correct']==6


def test_calibration_uses_exposed_only_and_fallback_for_rare_strata():
    selection=[_case([2.,1.,-2.],{0},index=i,
                     family=('allocation' if i%2 else 'encounter'),
                     density=('small_1_24' if i%3 else 'medium_25_48'))
               for i in range(24)]
    policies=m.calibrate_policies(selection)
    assert list(policies)==list(m.POLICIES)
    assert policies['fixed_075']=={'global':m.FIXED_LOGIT}
    assert 'encounter' not in policies['family'] # 12 cases < 20
    assert 'allocation' not in policies['family']
    new=[_case([-2.,2.,-1.],{0},index=i) for i in range(4)]
    summary={p:m.evaluate_policy(new,cutoffs,mode=p if p!='fixed_075' else 'global')
             for p,cutoffs in policies.items()}
    assert summary['fixed_075']['correct']==0
    assert summary['global']['correct']==0
    assert summary['family']['correct']==0


def test_new_cohort_excludes_all_previous_0020_and_history(monkeypatch):
    old=[{'prompt':'history','split':'dev'}]
    from20={'fresh_independent_dev':[{'prompt':'old-20-i','split':'dev'}],
            'fresh_topology_dev':[{'prompt':'old-20-t','split':'dev'}]}
    seen=[]
    def independent(prior,*,limit,seed):
        assert seed==460022 and limit==320
        assert {'history','old-20-i','old-20-t'} <= {r['prompt'] for batch in prior for r in batch}
        seen.append('independent')
        return [{'prompt':f'new-i-{i}','split':'dev','family':'allocation'} for i in range(limit)]
    def topology(prior,*,limit,seed):
        assert seed==460023 and limit==128
        assert any(r['prompt']=='new-i-0' for batch in prior for r in batch)
        seen.append('topology')
        return [{'prompt':f'new-t-{i}','split':'dev','family':'encounter',
                 'structural_topology':('4x4','4x2','2x4')[i%3]} for i in range(limit)]
    monkeypatch.setattr(m.general16,'independent_rows',independent)
    monkeypatch.setattr(m.general16,'heldout_topology_rows',topology)
    results=m._cohort_rows(old,from20)
    assert seen==['independent','topology']
    assert len(results[m.ASSESS[0]])==320 and len(results[m.ASSESS[1]])==128
    monkeypatch.setattr(m.general16,'independent_rows',lambda *args,**kw:
                        [{'prompt':'history','split':'dev','family':'allocation'}]*320)
    with pytest.raises(ValueError,match='PROMPT_OVERLAP'):
        m._cohort_rows(old,from20)


def test_live_replay_requires_exact_stored_top24_metrics(monkeypatch):
    anchors=[(0,1,'action'),(3,4,'action'),(6,7,'action')]
    rank=torch.zeros(3,3,len(BIND_CLASSES))
    k=BIND_CLASSES.index('owns')
    rank[0,1,k]=5.;rank[0,2,k]=2.
    row={'prompt':'g2-0021-mock','family':'encounter','split':'dev'}
    case={'prompt_sha256':base._sha(row['prompt']),'anchors':anchors,
          'vectors':torch.randn(3,8),'gold':{(anchors[0],anchors[1],'owns')},
          'family':'encounter'}
    class Net(torch.nn.Module):
        def forward(self,features,base,groups,*,with_competition=True):
            assert with_competition
            return base.clone().float()/2.
    monkeypatch.setattr(m.rel,'full_logits',lambda head,v:rank)
    allowed={('owns','action','action')}
    first=m.analyze_case(case,row,None,Net(),allowed,
                         split=m.SELECT[0],index=1,device='cpu')
    parent={'group':m.SELECT[0],'index':1,'prompt_sha256':first['prompt_sha256'],
            'density_bucket':first['density_bucket'],
            'candidate_inventory':{'top24':len(first['candidates'])},
            'configurations':{'precision_guard_top24':first['fixed_score']},
            'stage_losses':{'top24_reachable_gold':first['reachable_gold']}}
    again=m.analyze_case(case,row,None,Net(),allowed,split=m.SELECT[0],index=1,
                         device='cpu',previous=parent)
    assert first==again
    corrupted=deepcopy(parent)
    corrupted['stage_losses']['top24_reachable_gold']+=1
    with pytest.raises(ValueError,match='REPLAY_DRIFT'):
        m.analyze_case(case,row,None,Net(),allowed,split=m.SELECT[0],index=1,
                       device='cpu',previous=corrupted)
    other=deepcopy(case);other['gold']=set()
    relabeled=m.analyze_case(other,row,None,Net(),allowed,
                              split=m.SELECT[0],index=1,device='cpu')
    assert [c['score'] for c in relabeled['candidates']]==[c['score'] for c in first['candidates']]
    assert [c['edge'] for c in relabeled['candidates']]==[c['edge'] for c in first['candidates']]


def test_verifier_rejects_bad_parent_and_immutable_output(tmp_path):
    parent=tmp_path/'parent';parent.mkdir()
    (parent/ceiling20.REPORT).write_text('{"schema":"wrong"}',encoding='utf-8')
    with pytest.raises(ValueError,match='PARENT_0020_CONTRACT'):
        m.verify_parent_0020(parent,{}, {})
    with pytest.raises(FileExistsError,match='IMMUTABLE_OUTPUT'):
        m.run(root=tmp_path,output=tmp_path,g1_validation_report=None,g2_run=None,
              selection_run=None,discrimination_run=None,relational_run=None,
              density_run=None,consistency_run=None,context_run=None,learned_run=None,
              recall_run=None,crowded_run=None,robustness_run=None,diagnosis_run=None,
              source_run=None,specialization_run=None,routing_robustness_run=None,
              general_run=None,ceiling_run=None,context18_run=None,train19_run=None,
              ceiling20_run=None)


def test_ledger_gzip_reproducible_and_auditable(tmp_path):
    records={k:[_case([1.,-2.],{0},index=i)] for i,k in enumerate(m.SPLITS,1)}
    one=tmp_path/'first.gz';two=tmp_path/'second.gz'
    m._save_gzip(records,one);m._save_gzip(records,two)
    assert one.read_bytes()==two.read_bytes()
    with gzip.open(one,'rt',encoding='utf-8') as f:
        read=[json.loads(line) for line in f]
    assert [r['index'] for r in read]==[1,2,3,4]


def test_exact_recall_frontier_has_monotone_tp_and_atomic_ties():
    one=_case([3.,3.,1.,-2.],{0,2},index=1)
    two=_case([4.,0.,-3.],{0},index=2)
    curve=m.exact_frontier([one,two])
    assert curve[0]['correct']==0
    assert [p['correct'] for p in curve]==[0,1,2,3]
    assert curve[1]['logit_cutoff']==4.
    assert curve[2]['logit_cutoff']==3.
    assert curve[-1]['logit_cutoff']==1.
    optimum=m.threshold_ceiling([one,two])['best_bounded_threshold']
    matching=[p for p in curve if p['logit_cutoff']==optimum['logit_cutoff']]
    assert matching and matching[0]['correct']==optimum['correct']


def test_valid_0020_parent_ledger_replays_and_detects_metric_tampering(tmp_path):
    by_split={}
    for split in ceiling20.SPLITS:
        topologies=('2x4','4x2','4x4') if split.endswith('topology_validation') or split=='fresh_topology_dev' else ('native',)
        by_split[split]=[{'prompt':f'{split}-{top}','family':'allocation','split':'dev',
                          'structural_topology':top} for top in topologies]
    grouped={}
    for split,rows in by_split.items():
        grouped[split]=[{
            'group':split,'index':i,'prompt_sha256':base._sha(row['prompt']),
            'family':row['family'],'structural_topology':row['structural_topology'],
            'density_bucket':'small_1_24',
            'configurations':{cfg:{'correct':0,'missing':1,'false_positive':0,'exact':False}
                              for cfg in ceiling20.CONFIGS},
            'candidate_inventory':{'top24':4,'top48':4,'added':0},
            'stage_losses':{'gold_total':1,'g1_missing_endpoint':0},
            'context_drift':{'candidates':4,'absolute_score_drift_max':0.}}
            for i,row in enumerate(rows,1)]
    report={
        'schema':ceiling20.SCHEMA,'status':'G2_STRUCTURAL_CEILING_DEV_NOT_CERTIFIED',
        'reference':'precision_guard','reference_prelocked_before_fresh_evaluation':True,
        'fixed_threshold':m.THRESHOLD,'top_k_inventory':[24,48],
        'seed':ceiling20.SEED,'fresh_counts':{k:len(by_split[k]) for k in m.SELECT},
        'parent_hashes':{'ancestor':'sha'},
        'datasets_sha256':{k:m.density.sha_rows(v) for k,v in by_split.items()},
        'fresh_prompt_sha256':{k:[base._sha(r['prompt']) for r in by_split[k]] for k in m.SELECT},
        'frozen_g1':True,'frozen_rank_head':True,'frozen_baseline_0016':True,
        'frozen_precision_guard_0019':True,'new_training':False,
        'new_trainable_parameters':0,'model_selection':False,'threshold_selection':False,
        'candidate_selection_uses_gold':False,'gold_used_offline_only':True,
        'fresh_splits_are_dev_not_sealed':True,
        'fresh_prompts_disjoint_from_reconstructed_history':True,
        'fresh_results_not_used_for_selection':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False,
        'evaluation':{s:ceiling20.aggregate(records) for s,records in grouped.items()},
        'by_family':{s:{'allocation':ceiling20.aggregate(rows)} for s,rows in grouped.items()},
        'by_density':{s:{'small_1_24':ceiling20.aggregate(rows)} for s,rows in grouped.items()},
        'by_topology':{s:{top:ceiling20.aggregate([r for r in grouped[s]
                                                if r['structural_topology']==top])
                          for top in ('2x4','4x2','4x4')}
                       for s in (ceiling20.EXPOSED[1],m.SELECT[1])},
    }
    (tmp_path/ceiling20.CASES).write_text(''.join(json.dumps(row)+'\n'
                        for split in ceiling20.SPLITS for row in grouped[split]),encoding='utf-8')
    report['cases_sha256']=backbone.sha256(tmp_path/ceiling20.CASES)
    path=tmp_path/ceiling20.REPORT
    path.write_text(json.dumps(report),encoding='utf-8')
    selected,hashes=m.verify_parent_0020(tmp_path,by_split,{'ancestor':'sha'})
    assert set(selected)==set(m.SELECT)
    assert hashes['g2_0020_cases_sha256']==report['cases_sha256']
    report['evaluation'][m.SELECT[0]]['cases']+=1
    path.write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='METRIC_DRIFT'):
        m.verify_parent_0020(tmp_path,by_split,{'ancestor':'sha'})


def test_relation_specific_transfer_applies_only_by_relation_not_gold():
    row=_case([.5,.6,2.],{0,2})
    row['candidates'][0]['relation']='owns'
    row['candidates'][1]['relation']='attacks'
    row['candidates'][2]['relation']='attacks'
    thresholds={'global':m.FIXED_LOGIT,'relation:owns':0.}
    outcome=m.decode_relation_policy(row,thresholds)
    assert outcome=={'correct':2,'missing':1,'false_positive':0,'exact':False}
    assert m.evaluate_policy([row],thresholds,'relation')['correct']==2
    assert m.evaluate_policy([row],{'global':m.FIXED_LOGIT},'global')['correct']==1
