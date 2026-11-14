"""G2-0027 evidence audit: provenance, coverage, feature ambiguity and replay."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import gzip
import json

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_relationship_evidence_sufficiency_audit as m
from tools import nanojev_gamer_v1_g2_competitor_identity_discrimination as parent26
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13


SRC=(0,1,'actor')
A=(2,3,'action')
B=(4,5,'action')
C=(6,7,'action')
EDGES=[(SRC,A,'owns'),(SRC,B,'owns'),(SRC,C,'owns')]


def fixture():
    torch.manual_seed(27)
    features=torch.randn(3,4*4+source13.EXTRAS)
    features[1]=features[0]  # opposite-label collision, *exact* and observable
    inv={'edges':EDGES,'groups':[0,0,0],'features':features,
         'base':torch.tensor([1.,1.,0.]),'null_argmax':[False]*3}
    row={'prompt':'S A B C','family':'encounter','allowed':{('owns','actor','action')},
         'object_spans':[{'start':0,'end':1,'kind':'actor'},
                         {'start':2,'end':3,'kind':'action'}]}
    case={'anchors':[SRC,A,B,C],'gold':{EDGES[0]},'vectors':torch.randn(4,4),
          'prompt_sha256':m.base._sha('S A B C'),'family':'encounter'}
    scores={a:torch.tensor([0.1,1.0,-0.2]) for a in m.ARMS}
    return case,row,inv,scores


def test_protocol_has_no_training_and_separate_historical_cohorts():
    assert m.REPLAY==parent26.NEW
    assert m.NEW==('new_independent_dev_0027','new_topology_dev_0027')
    assert m.SEED!=parent26.SEED
    assert m.TOP_K==24 and m.THRESHOLD==.75
    assert m.ARMS==parent26.ARMS
    assert m.COSINE_THRESHOLDS==(.95,.99,.999)
    assert not hasattr(m,'_train')


def test_exact_collision_and_same_group_gold_vs_negative_is_detected():
    case,row,inv,scores=fixture()
    counts,frontier,observations=m._competitor_evidence(case,row,inv,scores)
    assert counts['gold_total']==1
    assert counts['g1_endpoints_present']==counts['eligible_gold']==counts['candidate_gold']==1
    assert counts['mixed_source_relation_groups']==1
    assert counts['gold_competing']==1 and counts['negative_competing']==2
    assert counts['exact_opposite_label_feature_and_logit_collisions']==1
    assert counts['identity_peer_ranking_failures']==1
    assert counts['pair_cosine_ge_0p999']==1
    assert counts.get('same_destination_surface_competitors',0)==0
    assert len(observations)==1 and len(frontier)==1
    assert frontier[0]['identity_peer_ranking_failed']
    assert frontier[0]['gold_destination_text']=='a'
    assert frontier[0]['negative_destination_text']=='b'
    assert frontier[0]['source_text']=='s'


def test_coverage_separates_missing_g1_from_missing_top24_and_compatibility():
    case,row,inv,scores=fixture()
    case['gold']={EDGES[0],(SRC,(8,9,'action'),'owns')}
    counts,_,_=m._competitor_evidence(case,row,inv,scores)
    assert counts['gold_total']==2 and counts['missing_g1']==1
    assert counts['missing_inventory']==0 and counts['missing_compatibility']==0
    case,row,inv,scores=fixture()
    inv['edges']=inv['edges'][1:];inv['features']=inv['features'][1:]
    inv['groups']=inv['groups'][1:]
    scores={a:values[1:] for a,values in scores.items()}
    counts,_,_=m._competitor_evidence(case,row,inv,scores)
    assert counts['missing_g1']==0 and counts['missing_inventory']==1
    assert counts['candidate_gold']==0
    case,row,inv,scores=fixture()
    row['allowed']=set()
    inv['edges']=[];inv['features']=inv['features'][:0];inv['groups']=[]
    scores={a:values[:0] for a,values in scores.items()}
    counts,_,_=m._competitor_evidence(case,row,inv,scores)
    assert counts['missing_compatibility']==1 and counts['candidate_gold']==0


def test_author_graph_is_not_needed_to_select_candidates_or_compute_scores(monkeypatch):
    case,row,inv,scores=fixture()
    def fake_full_logits(head,v):return torch.zeros((4,4,5))
    class Dummy(torch.nn.Module):
        def forward(self,x,b,g,**kw):return torch.tensor([0.1,2.,-0.2])
    monkeypatch.setattr(m.rel,'full_logits',fake_full_logits)
    monkeypatch.setattr(m.source13,'make_inventory',lambda *a,**kw:inv)
    nets={a:Dummy().eval() for a in m.ARMS}
    row['allowed']=None  # analyze_case always gets TRAIN compatibility separately
    rec,front=m.analyze_case(case,row,None,nets,{('owns','actor','action')},
                             split=m.NEW[0],index=1,device='cpu')
    assert rec['coverage']['gold_competing']==1
    assert rec['metrics']['identity_peer_0026']['false_positive']>=1
    old={'group':m.NEW[0],'index':1,'prompt_sha256':rec['prompt_sha256'],
         'family':rec['family'],'structural_topology':rec['structural_topology'],
         'density_bucket':rec['density_bucket'],
         'candidate_count':3,'reachable_gold':1,
         'configurations':rec['metrics'],'competition':rec['competition']}
    m.analyze_case(case,row,None,nets,{('owns','actor','action')},split=m.NEW[0],
                   index=1,device='cpu',prior=old)
    old['configurations']['identity_peer_0026']['correct']+=1
    with pytest.raises(ValueError,match='LIVE_REPLAY_DRIFT'):
        m.analyze_case(case,row,None,nets,{('owns','actor','action')},
                       split=m.NEW[0],index=1,device='cpu',prior=old)
    assert len(front)==1


def test_ceiling_is_gold_assisted_and_kept_separate_from_real_model_metrics():
    case,row,inv,scores=fixture()
    counts,front,obs=m._competitor_evidence(case,row,inv,scores)
    metric={'correct':0,'missing':1,'false_positive':1,'exact':False}
    record={'coverage':counts,'metrics':{arm:metric.copy() for arm in m.ARMS},
            'competition':{arm:{'gold_with_competing_negatives':1,
                                'gold_not_ahead_of_hardest_negative':1}
                           for arm in m.ARMS},
            'sampled_nearest_dst_cosine_sum':sum(d for d,p in obs),
            'sampled_nearest_pair_cosine_sum':sum(p for d,p in obs),
            'family':'encounter'}
    summary=m.aggregate([record])
    assert summary['oracle_candidate_cap_f1']==1.
    assert summary['metrics']['identity_peer_0026']['f1']==0.
    assert summary['candidate_gold_recall']==1.
    assert summary['coverage']['exact_opposite_label_feature_and_logit_collisions']==1


def test_no_raw_prompt_or_author_graph_is_exposed_in_summary():
    case,row,inv,scores=fixture()
    result,front,obs=m._competitor_evidence(case,row,inv,scores)
    assert 'prompt' not in result and 'gold' not in result
    assert 'prompt' not in front[0]
    assert not any('graph' in key for key in front[0])
    with pytest.raises(ValueError,match='INVALID_PREDICTED_ANCHOR'):
        m._text('S A B C',(0,100,'action'))


def test_verified_parent26_cohort_contract_does_not_reuse_parent25_names(tmp_path):
    # Exercise the entire immutable parent gate with tiny synthetic source
    # rows, saved ledger, aggregates and checkpoint, no backbone required.
    rows={};grouped={}
    for index,split in enumerate(parent26.SPLITS):
        row={'prompt':f'synthetic prompt {index}','family':'encounter',
             'structural_topology':'native','graph':{'nodes':[],'edges':[]}}
        rows[split]=[row]
        metrics={'correct':0,'missing':0,'false_positive':0,'exact':True}
        competition={'gold_with_competing_negatives':0,
                     'gold_not_ahead_of_hardest_negative':0}
        r={'group':split,'index':1,'prompt_sha256':m.base._sha(row['prompt']),
           'family':row['family'],'structural_topology':'native',
           'density_bucket':'small_1_24','gold_total':0,
           'reachable_gold':0,'candidate_count':1,
           'configurations':{a:metrics for a in parent26.ARMS},
           'competition':{a:competition for a in parent26.ARMS},
           'context_ablations':{a:{'metrics':metrics,'competition':competition,
                                   'mean_absolute_logit_shift':0.,
                                   'max_absolute_logit_shift':0.,'decision_flips':0}
                                for a in parent26.ABLATIONS}}
        grouped[split]=[r]
    cases=tmp_path/parent26.CASES
    m._save_json_gz(cases,[grouped[s][0] for s in parent26.SPLITS])
    weights=tmp_path/parent26.WEIGHTS
    save_file({a+'.w':torch.zeros(1) for a in parent26.TRAINED},str(weights))
    hashes={'g2_test_lineage':'fixed'};train_hash='train-hash'
    cohorts={'replayed_0025_dev':[r for s in parent26.REPLAY for r in grouped[s]],
             'new_0026_dev':[r for s in parent26.NEW for r in grouped[s]]}
    aggregate=parent26.aggregate
    report={'schema':parent26.SCHEMA,
        'status':'G2_COMPETITOR_IDENTITY_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':hashes,'train_dataset_sha256':train_hash,
        'datasets_sha256':{s:m.density.sha_rows(rows[s]) for s in parent26.SPLITS},
        'new_prompt_sha256':{s:[m.base._sha(r['prompt']) for r in rows[s]]
                             for s in parent26.NEW},
        'predeclared':{'train_seed':parent26.SEED,
            'new_dev_seeds':[parent26.EVAL_SEED,parent26.EVAL_SEED+1],
            'steps':parent26.STEPS,'lr':parent26.LR,'batch_seed':parent26.BATCH_SEED,
            'top_k':m.TOP_K,'groups_per_case':parent26.GROUPS_PER_CASE,
            'fixed_threshold':m.THRESHOLD,'arms':list(parent26.ARMS),
            'ablations':list(parent26.ABLATIONS),
            'base_checkpoint':'G2-0023 relational_context_0023',
            'initial_residual_readout':'zero_both_arms',
            'loss':'G2-0019 precision_guard + within-group identity contrast',
            'pair_weight':parent26.PAIR_WEIGHT,'pair_margin':parent26.PAIR_MARGIN,
            'hard_negatives_per_group':parent26.HARD_NEGATIVES,
            'training':'frozen_parent_scorer_residual_only',
            'self_only_control':'same_trainable_parameters_and_batches'},
        'train':{'steps':parent26.STEPS,'lr':parent26.LR,'seed':parent26.SEED,
                 'batch_seed':parent26.BATCH_SEED,'train_cases':640,
                 'same_initialized_weights':True,'zero_reset_readouts':True,
                 'residual_parameters_only':True,'identical_train_batches':True,
                 'identical_loss':True,'hard_identity_weight':parent26.PAIR_WEIGHT,
                 'hard_identity_margin':parent26.PAIR_MARGIN,
                 'hard_identity_negatives':parent26.HARD_NEGATIVES,
                 'identity_pairs_within_source_only':True,
                 'frozen_trained_g2_0023_base':True,
                 'candidate_inventory_label_blind':True,'multi_positive_labels':True,
                 'last_train_loss':{a:0. for a in parent26.TRAINED},
                 'counts':{'mixed_identity_groups':1,'hard_identity_pairs':1}},
        'by_split':{s:aggregate(v) for s,v in grouped.items()},
        'evaluation':{s:aggregate(v) for s,v in cohorts.items()},
        'by_family':{s:{'encounter':aggregate(v)} for s,v in cohorts.items()},
        'by_density':{s:{'small_1_24':aggregate(v)} for s,v in cohorts.items()},
        'by_topology':{s:{'native':aggregate(v)} for s,v in cohorts.items()},
        'diagnostic_gates':{s:parent26.gates(aggregate(v)) for s,v in cohorts.items()},
        'cases_sha256':m.backbone.sha256(cases),
        'new_weights_sha256':m.backbone.sha256(weights)}
    for field in ('frozen_g1','frozen_rank_head','frozen_backbone','frozen_existing_scorer',
                  'frozen_0023_checkpoints','frozen_0024_audit','frozen_0025_checkpoints',
                  'training_on_train_only','new_cohorts_are_dev_not_sealed',
                  'same_train_batches','same_initial_checkpoint','same_loss',
                  'same_trainable_parameter_count','multi_positive_supervision',
                  'train_labels_used_only_for_loss'):
        report[field]=True
    for field in ('candidate_selection_uses_gold','dev_labels_used_for_training',
                  'model_selection','checkpoint_selection','production_threshold_selection',
                  'sealed_test_used','challenge_or_test_split_used','certification_authorized',
                  'g2_certified','g2_production_unlocked','production_promotion',
                  'graph_execution_authorized','memory_writes'):
        report[field]=False
    (tmp_path/parent26.REPORT).write_text(json.dumps(report))
    old,grouped_result,found=m.verify_parent26(tmp_path,rows,hashes,train_hash)
    assert old['schema']==parent26.SCHEMA and set(grouped_result)==set(parent26.SPLITS)
    assert 'g2_0026_weights_sha256' in found
    damaged=deepcopy(report);damaged['by_split'][parent26.NEW[0]]['cases']+=1
    (tmp_path/parent26.REPORT).write_text(json.dumps(damaged))
    with pytest.raises(ValueError,match='AGGREGATE'):
        m.verify_parent26(tmp_path,rows,hashes,train_hash)
    (tmp_path/parent26.REPORT).write_text(json.dumps(report))
    bad=deepcopy(rows)
    bad[parent26.NEW[0]]=[dict(bad[parent26.NEW[0]][0],prompt='wrong')]
    with pytest.raises(ValueError,match='CONTRACT:datasets_sha256'):
        m.verify_parent26(tmp_path,bad,hashes,train_hash)


def test_identical_features_with_distinct_base_logits_are_not_exact_collisions():
    case,row,inv,scores=fixture()
    inv['base'][1]=inv['base'][0]+.1
    counts,frontier,_=m._competitor_evidence(case,row,inv,scores)
    assert counts['pair_cosine_ge_0p999']==1
    assert counts['exact_opposite_label_feature_and_logit_collisions']==0
    assert not frontier[0]['exact_feature_collision']


def test_identical_destination_surface_is_distinct_from_frozen_feature_collision():
    case,row,inv,scores=fixture()
    row['prompt']='S A A C'
    inv['features'][1]=inv['features'][1]+torch.arange(inv['features'].shape[1])*1e-3
    counts,frontier,_=m._competitor_evidence(case,row,inv,scores)
    assert counts['same_destination_surface_competitors']==1
    assert counts['exact_opposite_label_feature_and_logit_collisions']==0
    assert frontier[0]['same_destination_surface']


def test_evidence_inventory_must_have_distinct_candidates_and_matching_score_count():
    case,row,inv,scores=fixture()
    inv['edges'][1]=inv['edges'][0]
    with pytest.raises(ValueError,match='BAD_EVIDENCE_INVENTORY'):
        m._competitor_evidence(case,row,inv,scores)
    case,row,inv,scores=fixture()
    scores['identity_peer_0026']=torch.ones(2)
    with pytest.raises(ValueError,match='BAD_EVIDENCE_INVENTORY'):
        m._competitor_evidence(case,row,inv,scores)


def test_fresh_development_cohort_generator_is_prompt_disjoint():
    import random
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    rng=random.Random(2727)
    train=[corpus.encounter(rng,'train',i) if i%2==0 else corpus.allocation(rng,'train',i)
           for i in range(12)]
    cohorts=m.fresh_cohorts(train,train)
    assert {k:len(v) for k,v in cohorts.items()}=={m.NEW[0]:320,m.NEW[1]:128}
    all_rows=[*train,*cohorts[m.NEW[0]],*cohorts[m.NEW[1]]]
    assert len({m.base._sha(r['prompt']) for r in all_rows})==len(all_rows)
    assert {r.get('structural_topology') for r in cohorts[m.NEW[1]]}=={'2x4','4x2','4x4'}
    assert {r['split'] for r in cohorts[m.NEW[0]]+cohorts[m.NEW[1]]}=={'dev'}
