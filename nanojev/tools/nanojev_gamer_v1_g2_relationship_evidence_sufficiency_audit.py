"""G2-0027: frozen evidence sufficiency audit, no training or model selection.

Distinguishes upstream node/candidate coverage from within-source evidence
confusability. Author labels are used only AFTER candidate capture and scoring.
Similarity is an ambiguity proxy, not a proof of semantic non-identifiability.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import gzip
import json
import math

import torch
from torch.nn import functional as F
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g2_binding_bootstrap as base
from tools import nanojev_gamer_v1_g2_binding_selection as sel
from tools import nanojev_gamer_v1_g2_relational_training as rel
from tools import nanojev_gamer_v1_g2_density_discrimination as density
from tools import nanojev_gamer_v1_g2_source_conditioned_discrimination as source13
from tools import nanojev_gamer_v1_g2_general_relational_discrimination as general16
from tools import nanojev_gamer_v1_g2_architectural_ceiling_diagnosis as ceiling17
from tools import nanojev_gamer_v1_g2_competitor_context_intervention as context18
from tools import nanojev_gamer_v1_g2_inference_realistic_training as train19
from tools import nanojev_gamer_v1_g2_structural_ceiling_validation as ceiling20
from tools import nanojev_gamer_v1_g2_relationship_discrimination_ceiling as ceiling21
from tools import nanojev_gamer_v1_g2_competition_aware_learning as parent22
from tools import nanojev_gamer_v1_g2_contextual_relationship_representation as parent23
from tools import nanojev_gamer_v1_g2_context_utilization_audit as audit24
from tools import nanojev_gamer_v1_g2_context_utilization_intervention as parent25
from tools import nanojev_gamer_v1_g2_competitor_identity_discrimination as parent26

SCHEMA='nanojev.gamer.graph.g2.relationship-evidence-sufficiency-audit.v1'
REPORT='g2_relationship_evidence_sufficiency_audit_report.json'
CASES='g2_relationship_evidence_sufficiency_audit_per_case.jsonl.gz'
FRONTIER='g2_relationship_evidence_sufficiency_frontier.jsonl.gz'
SEED=460034
REPLAY=parent26.NEW
NEW=('new_independent_dev_0027','new_topology_dev_0027')
SPLITS=(*REPLAY,*NEW)
ARMS=parent26.ARMS
TOP_K=parent26.TOP_K
THRESHOLD=parent26.THRESHOLD
COSINE_THRESHOLDS=(.95,.99,.999)
NEAREST_K=5
FRONTIER_PER_CASE=5


def verify_parent26(directory, rows, expected_hashes, train_hash):
    """Full saved G2-0026 ledger, dataset, checkpoint and lineage integrity.

    G2-0026 cohort identifiers are NOT the G2-0025 or G2-0024 identifiers.
    """
    directory=Path(directory)
    rp,cp,wp=(directory/parent26.REPORT,directory/parent26.CASES,
              directory/parent26.WEIGHTS)
    report=json.loads(rp.read_text(encoding='utf-8'))
    required={
        'schema':parent26.SCHEMA,
        'status':'G2_COMPETITOR_IDENTITY_TRAIN_ONLY_NOT_CERTIFIED',
        'parent_hashes':expected_hashes,
        'train_dataset_sha256':train_hash,
        'datasets_sha256':{k:density.sha_rows(rows[k]) for k in parent26.SPLITS},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in rows[k]] for k in parent26.NEW},
        'predeclared':{
            'train_seed':parent26.SEED,'new_dev_seeds':[parent26.EVAL_SEED,parent26.EVAL_SEED+1],
            'steps':parent26.STEPS,'lr':parent26.LR,'batch_seed':parent26.BATCH_SEED,
            'top_k':TOP_K,'groups_per_case':parent26.GROUPS_PER_CASE,
            'fixed_threshold':THRESHOLD,'arms':list(parent26.ARMS),
            'ablations':list(parent26.ABLATIONS),
            'base_checkpoint':'G2-0023 relational_context_0023',
            'initial_residual_readout':'zero_both_arms',
            'loss':'G2-0019 precision_guard + within-group identity contrast',
            'pair_weight':parent26.PAIR_WEIGHT,'pair_margin':parent26.PAIR_MARGIN,
            'hard_negatives_per_group':parent26.HARD_NEGATIVES,
            'training':'frozen_parent_scorer_residual_only',
            'self_only_control':'same_trainable_parameters_and_batches'},
        'candidate_selection_uses_gold':False,'dev_labels_used_for_training':False,
        'train_labels_used_only_for_loss':True,'multi_positive_supervision':True,
        'same_train_batches':True,'same_initial_checkpoint':True,'same_loss':True,
        'same_trainable_parameter_count':True,'frozen_existing_scorer':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_0023_checkpoints':True,'frozen_0024_audit':True,
        'frozen_0025_checkpoints':True,'training_on_train_only':True,
        'new_cohorts_are_dev_not_sealed':True,'model_selection':False,
        'checkpoint_selection':False,'production_threshold_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False}
    for name,expected in required.items():
        if type(report.get(name)) is not type(expected) or report[name]!=expected:
            raise ValueError('GAMER_G2_0027_PARENT_0026_CONTRACT:'+name)
    if (report.get('cases_sha256')!=backbone.sha256(cp) or
        report.get('new_weights_sha256')!=backbone.sha256(wp)):
        raise ValueError('GAMER_G2_0027_PARENT_0026_FILE_HASH')
    train=report.get('train',{})
    for name,expected in {'steps':parent26.STEPS,'lr':parent26.LR,
                          'seed':parent26.SEED,'batch_seed':parent26.BATCH_SEED,
                          'train_cases':640,'same_initialized_weights':True,
                          'zero_reset_readouts':True,'residual_parameters_only':True,
                          'identical_train_batches':True,'identical_loss':True,
                          'hard_identity_weight':parent26.PAIR_WEIGHT,
                          'hard_identity_margin':parent26.PAIR_MARGIN,
                          'hard_identity_negatives':parent26.HARD_NEGATIVES,
                          'identity_pairs_within_source_only':True,
                          'frozen_trained_g2_0023_base':True,
                          'candidate_inventory_label_blind':True,
                          'multi_positive_labels':True}.items():
        if type(train.get(name)) is not type(expected) or train[name]!=expected:
            raise ValueError('GAMER_G2_0027_PARENT_0026_TRAIN:'+name)
    if (set(train.get('last_train_loss',{}))!=set(parent26.TRAINED) or
        train.get('counts',{}).get('mixed_identity_groups',0)<=0 or
        train.get('counts',{}).get('hard_identity_pairs',0)<=0):
        raise ValueError('GAMER_G2_0027_PARENT_0026_TRAIN_SIGNAL')
    grouped={k:[] for k in parent26.SPLITS}
    with gzip.open(cp,'rt',encoding='utf-8') as f:
        for line in f:
            record=json.loads(line)
            split=record.get('group')
            if split not in grouped:raise ValueError('GAMER_G2_0027_PARENT_0026_EXTRA_COHORT')
            i=len(grouped[split])
            if i>=len(rows[split]):raise ValueError('GAMER_G2_0027_PARENT_0026_EXCESS_CASES')
            row=rows[split][i]
            if (record.get('index')!=i+1 or
                record.get('prompt_sha256')!=base._sha(row['prompt']) or
                record.get('family')!=row['family'] or
                record.get('structural_topology')!=row.get('structural_topology','native') or
                record.get('gold_total')!=len(base.gold_binding_edges(row)) or
                set(record.get('configurations',{}))!=set(ARMS) or
                set(record.get('competition',{}))!=set(ARMS) or
                set(record.get('context_ablations',{}))!=set(parent26.ABLATIONS) or
                type(record.get('candidate_count')) is not int or record['candidate_count']<0):
                raise ValueError('GAMER_G2_0027_PARENT_0026_LEDGER_IDENTITY')
            grouped[split].append(record)
    if any(len(grouped[s])!=len(rows[s]) for s in parent26.SPLITS):
        raise ValueError('GAMER_G2_0027_PARENT_0026_LEDGER_COUNT')
    cohorts={'replayed_0025_dev':[r for k in parent26.REPLAY for r in grouped[k]],
             'new_0026_dev':[r for k in parent26.NEW for r in grouped[k]]}
    expected={
        'by_split':{s:parent26.aggregate(records) for s,records in grouped.items()},
        'evaluation':{s:parent26.aggregate(records) for s,records in cohorts.items()},
        'by_family':{s:{f:parent26.aggregate([r for r in records if r['family']==f])
                         for f in sorted({r['family'] for r in records})}
                     for s,records in cohorts.items()},
        'by_density':{s:{d:parent26.aggregate([r for r in records if r['density_bucket']==d])
                          for d in sorted({r['density_bucket'] for r in records})}
                      for s,records in cohorts.items()},
        'by_topology':{s:{t:parent26.aggregate([r for r in records if r['structural_topology']==t])
                           for t in sorted({r['structural_topology'] for r in records})}
                       for s,records in cohorts.items()},
        'diagnostic_gates':{s:parent26.gates(parent26.aggregate(records))
                            for s,records in cohorts.items()}}
    for name,value in expected.items():
        if report.get(name)!=value:
            raise ValueError('GAMER_G2_0027_PARENT_0026_AGGREGATE:'+name)
    tensors=load_file(str(wp),device='cpu')
    shapes={key[len(parent26.TRAINED[0])+1:]:tuple(v.shape) for key,v in tensors.items()
            if key.startswith(parent26.TRAINED[0]+'.')}
    if (not shapes or set(tensors)!={arm+'.'+key for arm in parent26.TRAINED for key in shapes}
        or any(not torch.isfinite(v).all() for v in tensors.values()) or
        any(tuple(tensors[arm+'.'+key].shape)!=shape for arm in parent26.TRAINED
            for key,shape in shapes.items())):
        raise ValueError('GAMER_G2_0027_PARENT_0026_CHECKPOINT_INVENTORY')
    return report,grouped,{
        'g2_0026_report_sha256':backbone.sha256(rp),
        'g2_0026_cases_sha256':backbone.sha256(cp),
        'g2_0026_weights_sha256':backbone.sha256(wp)}


def load_parent26_models(directory,template,weight_hash):
    path=Path(directory)/parent26.WEIGHTS
    if backbone.sha256(path)!=weight_hash:
        raise ValueError('GAMER_G2_0027_PARENT_0026_WEIGHT_DRIFT')
    tensor=load_file(str(path),device='cpu')
    expected={arm+'.'+k for arm in parent26.TRAINED for k in template.state_dict()}
    if set(tensor)!=expected:raise ValueError('GAMER_G2_0027_PARENT_0026_WEIGHT_KEYS')
    result={}
    from copy import deepcopy
    for arm in parent26.TRAINED:
        model=deepcopy(template).eval().requires_grad_(False)
        model.load_state_dict({k:tensor[arm+'.'+k] for k in template.state_dict()},strict=True)
        result[arm]=model
    return result


def fresh_cohorts(history,train_rows):
    independent=general16.independent_rows((history,),limit=320,seed=SEED)
    topology=general16.heldout_topology_rows((history,independent),limit=128,seed=SEED+1)
    combined=[*history,*independent,*topology]
    if (len(independent)!=320 or len(topology)!=128 or
        len({base._sha(r['prompt']) for r in combined})!=len(combined) or
        {r.get('structural_topology') for r in topology}!={'2x4','4x2','4x4'} or
        any(r.get('split')!='dev' for r in (*independent,*topology)) or
        any(r.get('split')!='train' for r in train_rows)):
        raise ValueError('GAMER_G2_0027_COHORT_DISJOINTNESS')
    return dict(zip(NEW,(independent,topology)))


def _text(prompt,anchor):
    s,e,kind=anchor
    if (type(s) is not int or type(e) is not int or s<0 or e<=s or e>len(prompt)
        or not isinstance(kind,str) or not prompt[s:e].strip()):
        raise ValueError('GAMER_G2_0027_INVALID_PREDICTED_ANCHOR')
    return prompt[s:e].strip().casefold()


def _competitor_evidence(case,row,inv,scores):
    """Post-hoc geometric and lexical audit; no label enters score computation.

    Within SAME source/relation groups compare author-gold destinations with
    incorrect candidates. The normalized frozen pair slice and destination
    vectors are *proxies* for separability, not a proof of information absence.
    """
    edges,groups=inv['edges'],inv['groups']
    if (len(edges)!=len(set(edges)) or len(groups)!=len(edges) or
        inv['features'].shape[0]!=len(edges) or
        len(scores['identity_peer_0026'])!=len(edges)):
        raise ValueError('GAMER_G2_0027_BAD_EVIDENCE_INVENTORY')
    anchors=set(case['anchors']);gold=set(case['gold'])
    both=sum(src in anchors and dst in anchors for src,dst,_ in gold)
    compatible=sum(src in anchors and dst in anchors and
                   (relation,src[2],dst[2]) in row['allowed']
                   for src,dst,relation in gold)
    present=sum(edge in gold for edge in edges)
    if present>compatible or compatible>both or both>len(gold):
        raise ValueError('GAMER_G2_0027_EVIDENCE_COVERAGE')
    data=Counter({key:0 for key in (
        'mixed_source_relation_groups','gold_competing','negative_competing',
        'exact_opposite_label_feature_and_logit_collisions',
        'same_destination_surface_competitors',
        'identity_peer_ranking_failures','identity_self_ranking_failures',
        *(prefix+str(t).replace('.','p') for prefix in
          ('dst_cosine_ge_','pair_cosine_ge_','peer_failure_and_pair_ge_')
          for t in COSINE_THRESHOLDS))})
    data.update(gold_total=len(gold),g1_endpoints_present=both,
                eligible_gold=compatible,candidate_gold=present,
                missing_g1=len(gold)-both,missing_compatibility=both-compatible,
                missing_inventory=compatible-present,candidates=len(edges),
                predicted_nodes=len(anchors),gold_nodes=len(row['object_spans']))
    if len(edges)==0:return dict(data),[],[]
    width=case['vectors'].shape[1]
    x=inv['features'].detach().float().cpu()
    if x.shape[1]!=4*width+source13.EXTRAS:
        raise ValueError('GAMER_G2_0027_FEATURE_SCHEMA_DRIFT')
    dest=F.normalize(x[:,width:2*width],dim=-1,eps=1e-12)
    pair=F.normalize(x[:,:4*width],dim=-1,eps=1e-12)
    gold_flags=[edge in gold for edge in edges]
    for edge in edges:
        _text(row['prompt'],edge[0]);_text(row['prompt'],edge[1])
    by_group=defaultdict(list)
    for i,g in enumerate(groups):by_group[g].append(i)
    frontier=[];observations=[]
    peer=scores['identity_peer_0026'];self_scores=scores['identity_self_0026']
    for indices in by_group.values():
        good=[i for i in indices if gold_flags[i]]
        bad=[i for i in indices if not gold_flags[i]]
        if not good or not bad:continue
        data['mixed_source_relation_groups']+=1
        data['gold_competing']+=len(good)
        data['negative_competing']+=len(bad)
        for i in good:
            # Evidence nearest neighbor is *label-aware analysis only*; it is
            # never a candidate selector or decoder.
            similarities_dst=(dest[bad]@dest[i]).tolist()
            similarities_pair=(pair[bad]@pair[i]).tolist()
            nearest_dst=max(similarities_dst)
            nearest_pair=max(similarities_pair)
            # Exact representational conflict is much stronger than proximity.
            exact_any=any(torch.equal(x[i],x[j]) and
                          bool(inv['base'][i]==inv['base'][j]) for j in bad)
            same_text=any(_text(row['prompt'],edges[i][1])==
                          _text(row['prompt'],edges[j][1]) for j in bad)
            hard=max(bad,key=lambda j:(float(peer[j]),-j))
            peer_failed=float(peer[i])<=float(peer[hard])
            self_failed=float(self_scores[i])<=max(float(self_scores[j]) for j in bad)
            if exact_any:data['exact_opposite_label_feature_and_logit_collisions']+=1
            if same_text:data['same_destination_surface_competitors']+=1
            if peer_failed:data['identity_peer_ranking_failures']+=1
            if self_failed:data['identity_self_ranking_failures']+=1
            for threshold in COSINE_THRESHOLDS:
                key=str(threshold).replace('.','p')
                if nearest_dst>=threshold:data['dst_cosine_ge_'+key]+=1
                if nearest_pair>=threshold:data['pair_cosine_ge_'+key]+=1
                if nearest_pair>=threshold and peer_failed:
                    data['peer_failure_and_pair_ge_'+key]+=1
            observations.append((nearest_dst,nearest_pair))
            rival=max(bad,key=lambda j:(float(pair[j]@pair[i]),-j))
            frontier.append({'relation':edges[i][2],
                'source_anchor':list(edges[i][0]),'gold_destination_anchor':list(edges[i][1]),
                'nearest_negative_anchor':list(edges[rival][1]),
                'source_text':_text(row['prompt'],edges[i][0])[:80],
                'gold_destination_text':_text(row['prompt'],edges[i][1])[:80],
                'negative_destination_text':_text(row['prompt'],edges[rival][1])[:80],
                'nearest_pair_cosine':nearest_pair,'nearest_destination_cosine':nearest_dst,
                'exact_feature_collision':exact_any,'same_destination_surface':same_text,
                'identity_peer_ranking_failed':peer_failed,
                'identity_self_ranking_failed':self_failed,
                'identity_peer_gold_score':float(peer[i]),
                'identity_peer_hardest_negative_score':float(peer[hard]),
                'frozen_pair_gold_margin':float(inv['base'][i]),
                'frozen_pair_hardest_negative_margin':float(inv['base'][hard])})
    frontier.sort(key=lambda r:(not r['identity_peer_ranking_failed'],
                                 -r['nearest_pair_cosine'],r['gold_destination_anchor']))
    return dict(data),frontier[:FRONTIER_PER_CASE],observations


def analyze_case(case,row,head,nets,allowed,*,split,index,device,prior=None):
    if case['prompt_sha256']!=base._sha(row['prompt']) or case['family']!=row['family']:
        raise ValueError('GAMER_G2_0027_CASE_IDENTITY')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=TOP_K,train_gold=False)
        # THE MODEL NEVER SEES THE AUTHOR GRAPH, OR THE GOLD LABELS.
        x=inv['features'].to(device);b=inv['base'].to(device);g=inv['groups']
        scores={arm:nets[arm](x,b,g,with_competition=True,
                             contextual=arm not in ('self_only_0025','identity_self_0026'))
                .detach().cpu() for arm in ARMS}
        metrics={arm:sel.score_set(source13.decode_scores(inv,scores[arm],THRESHOLD),case['gold'])
                 for arm in ARMS}
        comp={arm:parent22._competition_failures(inv['edges'],g,scores[arm],case['gold'])
              for arm in ARMS}
    if prior is not None:
        if (prior.get('group')!=split or prior.get('index')!=index or
            prior.get('prompt_sha256')!=case['prompt_sha256'] or
            prior.get('family')!=row['family'] or
            prior.get('structural_topology')!=row.get('structural_topology','native') or
            prior.get('density_bucket')!=density._bucket(len(case['anchors'])) or
            prior.get('candidate_count')!=len(inv['edges']) or
            prior.get('reachable_gold')!=sum(e in case['gold'] for e in inv['edges']) or
            any(prior['configurations'][arm]!=metrics[arm] or
                prior['competition'][arm]!=comp[arm] for arm in ARMS)):
            raise ValueError('GAMER_G2_0027_PARENT_0026_LIVE_REPLAY_DRIFT')
    enriched={**row,'allowed':allowed}
    evidence,frontier,observations=_competitor_evidence(case,enriched,inv,scores)
    if evidence['candidate_gold']!=sum(e in case['gold'] for e in inv['edges']):
        raise ValueError('GAMER_G2_0027_UNREACHABLE_GOLD_DRIFT')
    record={'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
        'family':row['family'],'density_bucket':density._bucket(len(case['anchors'])),
        'structural_topology':row.get('structural_topology','native'),
        'coverage':evidence,'metrics':metrics,'competition':comp,
        'sampled_nearest_dst_cosine_sum':sum(a for a,b in observations),
        'sampled_nearest_pair_cosine_sum':sum(b for a,b in observations)}
    sampled=[{'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],**r}
             for r in frontier]
    return record,sampled


def aggregate(rows):
    if not rows:raise ValueError('GAMER_G2_0027_EMPTY_AGGREGATE')
    coverage=Counter()
    for row in rows:coverage.update(row['coverage'])
    total_gold=coverage['gold_total'];present=coverage['candidate_gold']
    ideal_correct=sum(min(r['coverage']['candidate_gold'],source13.MAX_EDGES) for r in rows)
    metrics={arm:base.metrics([{**r['metrics'][arm],'family':r['family']} for r in rows])
             for arm in ARMS}
    competition={arm:{k:sum(r['competition'][arm][k] for r in rows)
                      for k in ('gold_with_competing_negatives',
                                'gold_not_ahead_of_hardest_negative')} for arm in ARMS}
    competitors=coverage['gold_competing']
    return {'cases':len(rows),'coverage':dict(coverage),
        'g1_endpoint_recall':coverage['g1_endpoints_present']/total_gold if total_gold else 0.,
        'eligible_gold_recall':coverage['eligible_gold']/total_gold if total_gold else 0.,
        'candidate_gold_recall':present/total_gold if total_gold else 0.,
        'oracle_candidate_cap_f1':2*ideal_correct/(ideal_correct+total_gold)
             if ideal_correct+total_gold else 0.,
        'mean_nearest_destination_cosine':sum(r['sampled_nearest_dst_cosine_sum'] for r in rows)/competitors
             if competitors else None,
        'mean_nearest_pair_cosine':sum(r['sampled_nearest_pair_cosine_sum'] for r in rows)/competitors
             if competitors else None,
        'metrics':metrics,'competition':competition}


def _save_json_gz(path,records):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for row in records:
                z.write((json.dumps(row,sort_keys=True,separators=(',',':'),allow_nan=False,
                                    ensure_ascii=False)+'\n').encode('utf-8'))


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,audit24_run,intervention25_run,identity26_run,output,device='cuda',max_length=2048):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0027_IMMUTABLE_OUTPUT')
    if max_length!=2048:
        raise ValueError('GAMER_G2_0027_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0027_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0027_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0027_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,
        context_run=context_run,learned_run=learned_run,recall_run=recall_run,
        crowded_run=crowded_run,robustness_run=robustness_run,
        diagnosis_run=diagnosis_run,source_run=source_run,
        specialization_run=specialization_run)
    training=original+augmented
    report16,rec16,h16=ceiling17.verify_frozen_0016(general_run,source_run,
        routing_robustness_run,augmented,training,calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256']!=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256']!=parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0027_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,h17=context18.verify_parent_0017(ceiling_run,exposed,h16)
    h18=train19.verify_parent_0018(context18_run,exposed,h16,h17,rec17)
    _,h19=ceiling20.verify_parent_0019(train19_run,{'calibration':calibration,**exposed},rec16,
        {**h16,**h17,**h18},expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    _,h20=ceiling21.verify_parent_0020(ceiling20_run,{**exposed,**fresh20},
                                       {**h16,**h17,**h18,**h19})
    fresh21=ceiling21._cohort_rows(prior,fresh20)
    _,_,h21=parent22._read_parent21(ceiling21_run,{**fresh20,**fresh21},
                                   {**h16,**h17,**h18,**h19,**h20})
    fresh22=parent22.fresh_cohorts(prior,fresh20,fresh21)
    _,_,h22=parent23._verify_parent22(competition22_run,{**fresh21,**fresh22},
        {**h16,**h17,**h18,**h19,**h20,**h21},density.sha_rows(training))
    fresh23=parent23.fresh_cohorts(prior,fresh20,fresh21,fresh22)
    previous={**fresh22,**fresh23}
    if len(training)!=640:raise ValueError('GAMER_G2_0027_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0027_PARENT_DEV_OVERLAP')
    parent23_report,old23,h23=audit24.verify_parent23(context23_run,previous,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22},density.sha_rows(training))
    parent24_report,old24,h24=parent26.verify_parent24(audit24_run,fresh23,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23})
    history25=[r for batch in all_previous for r in batch]
    fresh25=parent25.fresh_cohorts(history25,training)
    old25_parent_rows={**fresh23,**fresh25}
    parent25_report,old25,h25=parent26.verify_parent25(intervention25_run,old25_parent_rows,
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24},
        density.sha_rows(training))
    history26=[*history25,*(r for batch in fresh25.values() for r in batch)]
    fresh26=parent26.fresh_cohorts(history26,training)
    all_new=[*history26,*fresh26[parent26.NEW[0]],*fresh26[parent26.NEW[1]]]
    if len({base._sha(r['prompt']) for r in all_new})!=len(all_new):
        raise ValueError('GAMER_G2_0027_NEW_DEV_OVERLAP')
    parent26_report,old26,h26=verify_parent26(identity26_run,{**fresh25,**fresh26},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25},
        density.sha_rows(training))
    history27=[*history26,*(r for batch in fresh26.values() for r in batch)]
    fresh27=fresh_cohorts(history27,training)
    if len({base._sha(r['prompt']) for r in (*history27,*fresh27[NEW[0]],*fresh27[NEW[1]])})!=len(history27)+448:
        raise ValueError('GAMER_G2_0027_FRESH_PROMPT_OVERLAP')

    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
        parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    backbone_model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0027_BACKBONE_UNFROZEN')
    to_evaluate={**fresh26,**fresh27}
    captured={}
    for split,rows in to_evaluate.items():
        captured[split]=rel.capture(head,backbone_model,tokenizer,rows,
                                    device=device,max_length=max_length,group=split)
        if len(captured[split])!=len(rows):
            raise ValueError('GAMER_G2_0027_CAPTURE_COUNT')
    width=captured[parent26.NEW[0]][0]['vectors'].shape[1]
    if any(case['vectors'].shape[1]!=width for group in captured.values() for case in group):
        raise ValueError('GAMER_G2_0027_WIDTH_DRIFT')
    parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                parent19['new_weights_sha256'])
    guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
    controls=audit24.load_parent23_models(context23_run,guard,
                                          parent23_report['new_weights_sha256'])
    frozen23=controls['relational_context_0023']
    frozen23_before={k:v.detach().cpu().clone() for k,v in frozen23.state_dict().items()}
    earlier25=parent26.load_parent25_models(intervention25_run,frozen23,
                                             parent25_report['new_weights_sha256'])
    models26=load_parent26_models(identity26_run,frozen23,
                                  parent26_report['new_weights_sha256'])
    nets={'frozen_context_0023':frozen23,**earlier25,**models26}
    if set(nets)!=set(ARMS):raise ValueError('GAMER_G2_0027_ARM_INVENTORY_DRIFT')
    snapshots={arm:{k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
               for arm,net in nets.items()}
    for arm,net in nets.items():nets[arm]=net.to(device).eval().requires_grad_(False)
    allowed=sel.compatibility_from_train(original)
    records={};frontier=[]
    for split,rows in to_evaluate.items():
        entries=[]
        for i,(case,row) in enumerate(zip(captured[split],rows),1):
            prior=old26[split][i-1] if split in REPLAY else None
            record,near=analyze_case(case,row,head,nets,allowed,split=split,index=i,
                                     device=device,prior=prior)
            entries.append(record);frontier.extend(near)
            if i==1 or i%40==0 or i==len(rows):
                print(f'GAMER_G2_0027_AUDIT {split} {i}/{len(rows)}',flush=True)
        records[split]=entries
    # Immutable all the way through: input checkpoints, G1, and backbone are
    # never updated, not even by an optimizer object.
    rel.validate_original_state(head,head_before)
    if (any(not torch.equal(guard.state_dict()[k].detach().cpu(),v)
            for k,v in guard_before.items()) or
        any(not torch.equal(frozen23.state_dict()[k].detach().cpu(),v)
            for k,v in frozen23_before.items())):
        raise ValueError('GAMER_G2_0027_PARENT_MUTATED')
    for arm,model in nets.items():
        if any(not torch.equal(model.state_dict()[k].detach().cpu(),v)
               for k,v in snapshots[arm].items()):
            raise ValueError('GAMER_G2_0027_SCORER_MUTATED:'+arm)
    if any(p.requires_grad for p in backbone_model.parameters()):
        raise ValueError('GAMER_G2_0027_BACKBONE_TRAINED')

    cohorts={'replayed_0026_dev':[r for split in REPLAY for r in records[split]],
             'new_0027_dev':[r for split in NEW for r in records[split]]}
    by_cohort={name:aggregate(rows) for name,rows in cohorts.items()}
    result={'schema':SCHEMA,'status':'G2_EVIDENCE_SUFFICIENCY_FROZEN_DEV_AUDIT_NOT_CERTIFIED',
        'parent_hashes':{**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26},
        'train_dataset_sha256':density.sha_rows(training),
        'datasets_sha256':{k:density.sha_rows(v) for k,v in to_evaluate.items()},
        'new_prompt_sha256':{k:[base._sha(r['prompt']) for r in fresh27[k]] for k in NEW},
        'predeclared':{'new_dev_seeds':[SEED,SEED+1],
                       'replay_cohorts':list(REPLAY),'new_cohorts':list(NEW),
                       'frozen_arms':list(ARMS),'candidate_top_k':TOP_K,
                       'fixed_threshold':THRESHOLD,
                       'cosine_thresholds':list(COSINE_THRESHOLDS),
                       'frontier_per_case':FRONTIER_PER_CASE,
                       'scope':'posthoc_exact_G1_coverage_pair_and_dst_cosine_proxies',
                       'author_labels_enter_only_offline_diagnostics':True,
                       'no_text_reintroduced_into_scorer':True},
        'evaluation':by_cohort,
        'by_split':{s:aggregate(v) for s,v in records.items()},
        'by_family':{s:{f:aggregate([r for r in v if r['family']==f])
                        for f in sorted({r['family'] for r in v})} for s,v in cohorts.items()},
        'by_density':{s:{d:aggregate([r for r in v if r['density_bucket']==d])
                         for d in sorted({r['density_bucket'] for r in v})}
                      for s,v in cohorts.items()},
        'by_topology':{s:{t:aggregate([r for r in v if r['structural_topology']==t])
                          for t in sorted({r['structural_topology'] for r in v})}
                       for s,v in cohorts.items()},
        'interpretation_limits':{
            'candidate_oracle_f1_uses_dev_gold_and_is_not_deployable':True,
            'gold_vs_negative_cosine_proximity_is_not_proof_of_semantic_indistinguishability':True,
            'absence_of_exact_feature_collision_is_not_proof_of_evidence_sufficiency':True,
            'frozen_feature_distances_are_diagnostic_proxies_not_trained_classifiers':True,
            'prompts_contain_more_information_than_candidate_feature_interface':True,
            'author_graph_and_gold_are_never_scorer_inputs':True,
            'no_oracle_text_parser_or_gold_inference_path':True},
        'new_training':False,'new_trainable_parameters':0,
        'no_optimizer_created':True,'no_weight_writes':True,
        'candidate_selection_uses_gold':False,'gold_used_offline_only':True,
        'frozen_g1':True,'frozen_rank_head':True,'frozen_backbone':True,
        'frozen_parent_weights':True,'replayed_0026_live_scores_verified':True,
        'new_cohorts_are_dev_not_sealed':True,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'model_selection':False,
        'threshold_selection':False,'certification_authorized':False,
        'g2_certified':False,'g2_production_unlocked':False,
        'production_promotion':False,'graph_execution_authorized':False,
        'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    _save_json_gz(output/CASES,[r for s in SPLITS for r in records[s]])
    _save_json_gz(output/FRONTIER,frontier)
    result['cases_sha256']=backbone.sha256(output/CASES)
    result['frontier_sha256']=backbone.sha256(output/FRONTIER)
    (output/REPORT).write_text(json.dumps(result,indent=2,sort_keys=True,allow_nan=False,
                                         ensure_ascii=False)+'\n',encoding='utf-8')
    return result
