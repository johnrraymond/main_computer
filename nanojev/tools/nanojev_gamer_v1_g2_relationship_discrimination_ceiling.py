"""G2-0021: frozen relationship-score separation, threshold and ranking ceilings.

G2-0020's two newly exposed DEV cohorts serve only as exploratory calibration;
new prompt-disjoint DEV cohorts measure transfer. No training, deployment,
checkpoint choice or certified/test evaluation is performed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path
import gzip
import json
import math

import torch

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
from tools.nanojev_gamer_v1_graph_contract import MAX_EDGES

SCHEMA = 'nanojev.gamer.graph.g2.relationship-discrimination-ceiling.v1'
REPORT = 'g2_relationship_discrimination_ceiling_report.json'
CASES = 'g2_relationship_discrimination_ceiling_per_case.jsonl.gz'
FRONTIER = 'g2_relationship_discrimination_ceiling_frontier.jsonl.gz'
SEED = 460022
SELECT = ('fresh_independent_dev', 'fresh_topology_dev')
ASSESS = ('new_independent_dev_0021', 'new_topology_dev_0021')
SPLITS = (*SELECT, *ASSESS)
THRESHOLD = ceiling20.THRESHOLD
FIXED_LOGIT = math.log(THRESHOLD/(1.-THRESHOLD))
MIN_STRATUM_CASES = 20
POLICIES = ('fixed_075', 'global', 'family', 'density', 'family_density', 'relation')


def _cohort_rows(prior, from20):
    """Freeze counts/seeds BEFORE evaluating any new 0021 scores."""
    seen = [*prior, *from20[SELECT[0]], *from20[SELECT[1]]]
    fresh = general16.independent_rows((seen,), limit=ceiling20.FRESH_INDEPENDENT,
                                       seed=SEED)
    if len(fresh)!=ceiling20.FRESH_INDEPENDENT or len({base._sha(r['prompt']) for r in (*seen,*fresh)}) != len(seen)+len(fresh):
        raise ValueError('GAMER_G2_0021_PROMPT_OVERLAP')
    topology = general16.heldout_topology_rows((seen, fresh),
                               limit=ceiling20.FRESH_TOPOLOGY, seed=SEED+1)
    hashes = [base._sha(r['prompt']) for r in (*seen, *fresh, *topology)]
    if len(hashes) != len(set(hashes)):
        raise ValueError('GAMER_G2_0021_PROMPT_OVERLAP')
    if (len(fresh) != ceiling20.FRESH_INDEPENDENT or
        len(topology) != ceiling20.FRESH_TOPOLOGY or
        {x['structural_topology'] for x in topology} != {'4x4','4x2','2x4'} or
        any(row.get('split') != 'dev' for row in (*fresh, *topology))):
        raise ValueError('GAMER_G2_0021_NEW_COHORT_CONTRACT')
    return dict(zip(ASSESS, (fresh,topology)))


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip()]


def verify_parent_0020(directory, by_split, upstream_hashes):
    """Verify every parent identity, aggregate, SHA and nonpromotion invariant.

    Only the two G2-0020 fresh DEV cohorts are reused as exploratory selection.
    Exact top-24 predictions will be replay-checked during live capture.
    """
    directory = Path(directory)
    report_path, case_path = directory/ceiling20.REPORT, directory/ceiling20.CASES
    report = json.loads(report_path.read_text(encoding='utf-8'))
    if report.get('schema')!=ceiling20.SCHEMA:
        raise ValueError('GAMER_G2_0021_PARENT_0020_CONTRACT:schema')
    if set(by_split)!=set(ceiling20.SPLITS):
        raise ValueError('GAMER_G2_0021_PARENT_0020_SPLIT_CONTRACT')
    required = {
        'schema':ceiling20.SCHEMA,
        'status':'G2_STRUCTURAL_CEILING_DEV_NOT_CERTIFIED',
        'reference':ceiling20.REFERENCE,
        'reference_prelocked_before_fresh_evaluation':True,
        'fixed_threshold':THRESHOLD, 'top_k_inventory':[24,48],
        'seed':ceiling20.SEED,
        'fresh_counts':{k:len(by_split[k]) for k in SELECT},
        'parent_hashes':upstream_hashes,
        'datasets_sha256':{s:density.sha_rows(rows) for s,rows in by_split.items()},
        'fresh_prompt_sha256':{s:[base._sha(r['prompt']) for r in by_split[s]] for s in SELECT},
        'frozen_g1':True, 'frozen_rank_head':True,
        'frozen_baseline_0016':True, 'frozen_precision_guard_0019':True,
        'new_training':False, 'new_trainable_parameters':0,
        'model_selection':False, 'threshold_selection':False,
        'candidate_selection_uses_gold':False, 'gold_used_offline_only':True,
        'fresh_splits_are_dev_not_sealed':True,
        'fresh_prompts_disjoint_from_reconstructed_history':True,
        'fresh_results_not_used_for_selection':True,
        'sealed_test_used':False, 'challenge_or_test_split_used':False,
        'g2_certified':False, 'g2_production_unlocked':False,
        'production_promotion':False, 'graph_execution_authorized':False,
        'memory_writes':False,
    }
    for name,expected in required.items():
        if type(report.get(name)) is not type(expected) or report[name] != expected:
            raise ValueError('GAMER_G2_0021_PARENT_0020_CONTRACT:'+name)
    if report.get('cases_sha256') != backbone.sha256(case_path):
        raise ValueError('GAMER_G2_0021_PARENT_0020_CASE_SHA')
    records = _read_jsonl(case_path)
    if len(records) != sum(len(by_split[s]) for s in ceiling20.SPLITS):
        raise ValueError('GAMER_G2_0021_PARENT_0020_COUNT')
    grouped={}; offset=0
    for split in ceiling20.SPLITS:
        rows=by_split[split]
        part=records[offset:offset+len(rows)]
        offset+=len(rows)
        for index,(rec,row) in enumerate(zip(part,rows),1):
            if (rec.get('group') != split or rec.get('index') != index or
                rec.get('prompt_sha256') != base._sha(row['prompt']) or
                rec.get('family') != row['family'] or
                rec.get('structural_topology') != row.get('structural_topology','native') or
                set(rec.get('configurations',{})) != set(ceiling20.CONFIGS)):
                raise ValueError('GAMER_G2_0021_PARENT_0020_IDENTITY_DRIFT')
        if report.get('evaluation',{}).get(split) != ceiling20.aggregate(part):
            raise ValueError('GAMER_G2_0021_PARENT_0020_METRIC_DRIFT')
        families = {family:ceiling20.aggregate([r for r in part if r['family']==family])
                    for family in ('encounter','allocation') if any(r['family']==family for r in part)}
        buckets = {bucket:ceiling20.aggregate([r for r in part if r['density_bucket']==bucket])
                   for bucket in sorted({r['density_bucket'] for r in part})}
        if (report.get('by_family',{}).get(split) != families or
            report.get('by_density',{}).get(split) != buckets):
            raise ValueError('GAMER_G2_0021_PARENT_0020_SUBGROUP_DRIFT')
        grouped[split]=part
    for split in (ceiling20.EXPOSED[1],SELECT[1]):
        by_topology={top:ceiling20.aggregate([r for r in grouped[split]
                                               if r['structural_topology']==top])
                     for top in ('2x4','4x2','4x4')}
        if report.get('by_topology',{}).get(split) != by_topology:
            raise ValueError('GAMER_G2_0021_PARENT_0020_TOPOLOGY_DRIFT')
    return {s:grouped[s] for s in SELECT}, {
        'g2_0020_report_sha256':backbone.sha256(report_path),
        'g2_0020_cases_sha256':backbone.sha256(case_path)}


def competition_bucket(size):
    return 'solo_1' if size==1 else 'few_2_4' if size<=4 else 'some_5_12' if size<=12 else 'many_13_24'


def relative_bucket(gap):
    return 'solo' if gap is None else 'leader' if gap>=0 else 'near_1_logit' if gap>=-1 else 'behind_1_plus'


def analyze_case(case,row,head,guard,allowed,*,split,index,device,previous=None):
    """Extract every frozen top-24 candidate including negatives and score ties."""
    if case['prompt_sha256'] != base._sha(row['prompt']) or case['family']!=row['family']:
        raise ValueError('GAMER_G2_0021_CASE_IDENTITY_DRIFT')
    with torch.inference_mode():
        rank=rel.full_logits(head,case['vectors'].to(device)).detach().cpu()
        inv=source13.make_inventory(case,rank,allowed,top_k=24,train_gold=False)
        logits=guard(inv['features'].to(device),inv['base'].to(device),
                     inv['groups'],with_competition=True).detach().cpu()
        predicted=source13.decode_scores(inv,logits,THRESHOLD)
    if (len(set(inv['edges'])) != len(inv['edges']) or
        len(inv['groups'])!=len(inv['edges']) or logits.shape!=(len(inv['edges']),)
        or not torch.isfinite(logits).all()):
        raise ValueError('GAMER_G2_0021_BAD_CANDIDATES')
    scored=sel.score_set(predicted,case['gold'])
    if previous is not None:
        if (previous.get('group')!=split or previous.get('index')!=index or
            previous.get('prompt_sha256')!=case['prompt_sha256'] or
            previous.get('density_bucket')!=density._bucket(len(case['anchors'])) or
            previous.get('candidate_inventory',{}).get('top24')!=len(inv['edges']) or
            previous.get('configurations',{}).get('precision_guard_top24')!=scored or
            previous.get('stage_losses',{}).get('top24_reachable_gold')!=
                sum(e in case['gold'] for e in inv['edges'])):
            raise ValueError('GAMER_G2_0021_PARENT_0020_REPLAY_DRIFT')
    grouped=defaultdict(list)
    for i,g in enumerate(inv['groups']):grouped[g].append(i)
    competitor_info={}
    for indices in grouped.values():
        # The largest alternative destination; group size is label blind.
        rank_order=sorted(indices,key=lambda i:(-float(logits[i]),inv['edges'][i]))
        for i in indices:
            other=rank_order[1] if rank_order[0]==i and len(indices)>1 else rank_order[0]
            competitor_info[i]=(len(indices),None if len(indices)==1
                                else float(logits[i])-float(logits[other]))
    candidates=[]
    for i,(edge,score) in enumerate(zip(inv['edges'],logits)):
        size,gap=competitor_info[i]
        candidates.append({'edge':edge,'relation':edge[2],
                           'score':float(score),'gold':edge in case['gold'],
                           'competition':competition_bucket(size),
                           'relative':relative_bucket(gap)})
    result={'group':split,'index':index,'prompt_sha256':case['prompt_sha256'],
            'family':case['family'], 'density_bucket':density._bucket(len(case['anchors'])),
            'structural_topology':row.get('structural_topology','native'),
            'gold_total':len(case['gold']), 'reachable_gold':sum(c['gold'] for c in candidates),
            'fixed_score':scored, 'candidates':candidates}
    predicted_copy=decode_case(result,FIXED_LOGIT)
    if predicted_copy != scored:
        raise ValueError('GAMER_G2_0021_FIXED_DECODER_MISMATCH')
    return result


def decode_case(record,cutoff):
    """Exact score>=cutoff, top-MAX_EDGES, lexical-tie graph decoder."""
    if not math.isfinite(cutoff):raise ValueError('GAMER_G2_0021_BAD_THRESHOLD')
    winning=sorted((c for c in record['candidates'] if c['score']>=cutoff),
                   key=lambda c:(-c['score'],c['edge']))[:MAX_EDGES]
    tp=sum(c['gold'] for c in winning)
    fp=len(winning)-tp
    return {'correct':tp,'missing':record['gold_total']-tp,
            'false_positive':fp,'exact':not (fp or record['gold_total']-tp)}


def decode_relation_policy(record,cutoffs):
    """Exploratory relationship-specific thresholds, same global edge cap."""
    winners=sorted((c for c in record['candidates']
                    if c['score']>=cutoffs.get('relation:'+c['relation'],cutoffs['global'])),
                   key=lambda c:(-c['score'],c['edge']))[:MAX_EDGES]
    tp=sum(c['gold'] for c in winners)
    fp=len(winners)-tp
    return {'correct':tp,'missing':record['gold_total']-tp,
            'false_positive':fp,'exact':not (fp or record['gold_total']-tp)}


def _metrics(tp,fp,gold,reachable=None):
    precision=tp/(tp+fp) if tp+fp else 0.
    recall=tp/gold if gold else 0.
    return {'correct':tp,'false_positive':fp,'missing':gold-tp,
            'precision':precision,'recall':recall,
            'f1':2*tp/(2*tp+fp+gold-tp) if (tp+fp+gold) else 0.,
            **({'candidate_recall':tp/reachable if reachable else 0.} if reachable is not None else {})}


def evaluate_policy(cases,cutoffs,mode='global'):
    total=Counter();exact=0
    for case in cases:
        key=policy_key(case,mode)
        result=(decode_relation_policy(case,cutoffs) if mode=='relation' else
                decode_case(case,cutoffs.get(key,cutoffs['global'])))
        total['correct']+=result['correct'];total['false_positive']+=result['false_positive']
        total['gold']+=case['gold_total'];total['reachable']+=case['reachable_gold']
        exact+=bool(result['exact'])
    return {'cases':len(cases),'exact_cases':exact,
            **_metrics(total['correct'],total['false_positive'],total['gold'],total['reachable'])}


def _rank_metrics(candidates):
    """AUROC and average precision account for equal-score ties exactly."""
    arr=sorted(((c['score'],bool(c['gold'])) for c in candidates),reverse=False)
    p=sum(y for _,y in arr);n=len(arr)-p
    negatives_seen=0;wins=0.;ap=0.;tp=0;fp=0
    for _,items in groupby(arr,key=lambda z:z[0]):
        group=list(items);pos=sum(y for _,y in group);neg=len(group)-pos
        wins+=pos*(negatives_seen+neg/2)
        negatives_seen+=neg
    # Descending score blocks, not an arbitrary ordering within ties.
    for _,items in groupby(reversed(arr),key=lambda z:z[0]):
        group=list(items);pos=sum(y for _,y in group);neg=len(group)-pos
        tp+=pos;fp+=neg
        if p:ap+=(pos/p)*(tp/(tp+fp))
    return {'candidate_gold':p,'candidate_negative':n,
            'roc_auc':wins/(p*n) if p and n else None,
            'average_precision':ap if p else None,
            'random_precision_reference':p/(p+n) if p+n else None,
            'inverted_pair_fraction':(1-wins/(p*n)) if p and n else None}


def _score_quantiles(values):
    if not values:return None
    values=sorted(values);n=len(values)
    return {str(p):values[int((n-1)*p)] for p in (0.,.1,.25,.5,.75,.9,1.)}


def threshold_ceiling(cases,*,attribute=None):
    """Exact all-score bounded micro-F1 optimum, retrospective, never deploy.

    For an attribute stratum this is a *candidate-only* diagnosis; edges outside
    the stratum and its full graph cap are excluded from the hypothetical pool.
    """
    if not cases:raise ValueError('GAMER_G2_0021_EMPTY_CASES')
    entries=[];gold=0;reachable=0
    for i,case in enumerate(cases):
        if attribute is None:
            chosen=case['candidates'];gold+=case['gold_total']
        else:
            field,value=attribute
            chosen=[c for c in case['candidates'] if c[field]==value]
            gold+=sum(c['gold'] for c in chosen)
        reachable+=sum(c['gold'] for c in chosen)
        entries.extend((c['score'],i,c['edge'],int(c['gold'])) for c in chosen)
    if not entries:return None
    entries.sort(key=lambda x:(-x[0],x[1],x[2]))
    used=Counter();tp=fp=0
    maximum=entries[0][0]
    best={'logit_cutoff':maximum+1.,**_metrics(0,0,gold,reachable)}
    checkpoints=[]
    for score,block in groupby(entries,key=lambda z:z[0]):
        for _,i,edge,y in block:
            if used[i]<MAX_EDGES:
                used[i]+=1;tp+=y;fp+=1-y
        metric=_metrics(tp,fp,gold,reachable)
        # The optimizer sees labels only in offline diagnosis.
        if metric['f1']>best['f1']+1.e-14:
            best={'logit_cutoff':score,**metric}
        # Keep only the best precision attainable at/above recall milestones.
        if reachable and tp:
            checkpoints.append((metric['recall'],metric['precision'],score))
    curve={}
    for j in range(1,11):
        target=j/10
        eligible=[(prec,cutoff) for rec,prec,cutoff in checkpoints if rec+1.e-12>=target]
        curve[str(round(target,1))] = ({'max_precision':max(p for p,_ in eligible)}
                                         if eligible else None)
    all_candidates=[c for case in cases for c in case['candidates']
                    if attribute is None or c[attribute[0]]==attribute[1]]
    return {'best_bounded_threshold':best, 'precision_at_recall':curve,
            'ranking':_rank_metrics(all_candidates),
            'score_quantiles':{
                'gold':_score_quantiles([c['score'] for c in all_candidates if c['gold']]),
                'nongold':_score_quantiles([c['score'] for c in all_candidates if not c['gold']])},
            'candidate_coverage':reachable/gold if gold else 0.,
            'oracle_reachable_gold':reachable,
            'oracle_perfect_candidate_ranking_f1':_metrics(
                sum(min(sum(c['gold'] for c in case['candidates']),MAX_EDGES) for case in cases)
                if attribute is None else reachable,0,gold)['f1'],
            'rank_vs_oracle_f1_gap':(_metrics(
                sum(min(sum(c['gold'] for c in case['candidates']),MAX_EDGES) for case in cases)
                if attribute is None else reachable,0,gold)['f1']-best['f1'])}


def _subgroup(cases,key):
    vals=sorted({case[key] for case in cases})
    return {v:{'cases':len([r for r in cases if r[key]==v]),
               'ceiling':threshold_ceiling([r for r in cases if r[key]==v])}
            for v in vals}


def _candidate_stratum(cases,key):
    vals=sorted({c[key] for r in cases for c in r['candidates']})
    return {v:threshold_ceiling(cases,attribute=(key,v)) for v in vals}


def policy_key(case,mode):
    if mode=='global' or mode=='relation':return 'global'
    if mode=='family':return case['family']
    if mode=='density':return case['density_bucket']
    if mode=='family_density':return case['family']+'/'+case['density_bucket']
    raise ValueError('GAMER_G2_0021_BAD_POLICY')


def calibrate_policies(cases):
    """Hypothetical thresholds from G2-0020 exposed DEV only."""
    global_cutoff=threshold_ceiling(cases)['best_bounded_threshold']['logit_cutoff']
    result={'fixed_075':{'global':FIXED_LOGIT}}
    for mode in POLICIES[1:]:
        cuts={'global':global_cutoff}
        if mode=='relation':
            by_relation=defaultdict(list)
            for row in cases:
                for relation in {c['relation'] for c in row['candidates']}:
                    by_relation[relation].append(row)
            for relation,subset in by_relation.items():
                positives=sum(c['gold'] for r in subset for c in r['candidates']
                              if c['relation']==relation)
                negatives=sum(not c['gold'] for r in subset for c in r['candidates']
                              if c['relation']==relation)
                if len(subset)>=MIN_STRATUM_CASES and positives>=20 and negatives>=20:
                    cuts['relation:'+relation]=threshold_ceiling(
                        subset,attribute=('relation',relation))['best_bounded_threshold']['logit_cutoff']
        elif mode!='global':
            buckets=defaultdict(list)
            for row in cases:buckets[policy_key(row,mode)].append(row)
            for key,subset in buckets.items():
                if len(subset)>=MIN_STRATUM_CASES:
                    cuts[key]=threshold_ceiling(subset)['best_bounded_threshold']['logit_cutoff']
        result[mode]=cuts
    return result


def diagnosis(cases):
    return {'cases':len(cases),
            'fixed_075':evaluate_policy(cases,{'global':FIXED_LOGIT}),
            'overall':threshold_ceiling(cases),
            'by_family':_subgroup(cases,'family'),
            'by_density':_subgroup(cases,'density_bucket'),
            'by_topology':_subgroup(cases,'structural_topology'),
            'by_relationship_type':_candidate_stratum(cases,'relation'),
            'by_competition_size':_candidate_stratum(cases,'competition'),
            'by_competitor_position':_candidate_stratum(cases,'relative')}



def exact_frontier(cases):
    """Every reachable TP/recall step of the exact graph-capped threshold path.

    The full candidate rank is frozen. Tied scores enter atomically; per-graph
    cap uses the production decoder's lexical tie-breaking. FP-only steps are
    dominated at unchanged recall and are excluded from this diagnostic curve.
    """
    gold=sum(r['gold_total'] for r in cases)
    entries=[]
    for i,case in enumerate(cases):
        entries.extend((c['score'],i,c['edge'],int(c['gold'])) for c in case['candidates'])
    entries.sort(key=lambda x:(-x[0],x[1],x[2]))
    used=Counter();tp=fp=0
    points=[{'logit_cutoff':entries[0][0]+1.,
             'correct':0,'false_positive':0,'precision':1.,'recall':0.}] if entries else []
    for score,block in groupby(entries,key=lambda x:x[0]):
        before=tp
        for _,i,edge,y in block:
            if used[i]<MAX_EDGES:
                used[i]+=1;tp+=y;fp+=1-y
        if tp>before:
            points.append({'logit_cutoff':score,'correct':tp,'false_positive':fp,
                           'precision':tp/(tp+fp),'recall':tp/gold if gold else 0.})
    return points


def _save_frontier(records,path):
    groups={'selection_0020_fresh_dev':[r for split in SELECT for r in records[split]],
            'assessment_0021_new_dev':[r for split in ASSESS for r in records[split]],
            **{split:records[split] for split in SPLITS}}
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for name,rows in groups.items():
                for item in exact_frontier(rows):
                    z.write((json.dumps({'cohort':name,**item},sort_keys=True,
                                        separators=(',',':'),allow_nan=False)+'\n').encode())


def _save_gzip(records,path):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as z:
            for split in SPLITS:
                for row in records[split]:
                    z.write((json.dumps(row,sort_keys=True,separators=(',',':'),
                                        allow_nan=False)+'\n').encode('utf-8'))


def run(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,output,device='cuda',max_length=2048):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0021_IMMUTABLE_OUTPUT')
    # Fully reconstruct/hard-verify G2-0016..0019 ancestry; no shortcuts around
    # calibration, withheld DEV, original datasets or weights.
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(
        root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original)) != bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0021_PARENT_TRAIN_DRIFT')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1]) != bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0021_PARENT_BOOTSTRAP_DRIFT')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256'] != hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0021_PARENT_G1_LINEAGE')
    general16.verify_frozen_ancestry15(parent15['parent_hashes'],
        g1_validation_report=g1_validation_report,g2_run=g2_run,
        discrimination_run=discrimination_run,relational_run=relational_run,
        density_run=density_run,consistency_run=consistency_run,context_run=context_run,
        learned_run=learned_run,recall_run=recall_run,crowded_run=crowded_run,
        robustness_run=robustness_run,diagnosis_run=diagnosis_run,
        source_run=source_run,specialization_run=specialization_run)
    training=original+augmented
    report16,records16,hashes16=ceiling17.verify_frozen_0016(
        general_run,source_run,routing_robustness_run,augmented,training,
        calibration,independent,withheld)
    if (report16['parent_hashes']['g2_0014_report_sha256'] !=
        parent15['parent_hashes']['g2_0014_report_sha256'] or
        report16['frozen_source13_sha256'] != parent13['new_weights_sha256']):
        raise ValueError('GAMER_G2_0021_PARENT_0016_LINEAGE')
    exposed=dict(zip(ceiling20.EXPOSED,(independent,withheld)))
    rec17,hash17=context18.verify_parent_0017(ceiling_run,exposed,hashes16)
    hash18=train19.verify_parent_0018(context18_run,exposed,hashes16,hash17,rec17)
    rec19,hash19=ceiling20.verify_parent_0019(
        train19_run,{'calibration':calibration,**exposed},records16,
        {**hashes16,**hash17,**hash18},
        expected_train_sha=density.sha_rows(training),
        expected_datasets={s:density.sha_rows(rows) for s,rows in
                           (('calibration',calibration),*exposed.items())})
    prior=[r for batch in (*history,augmented,calibration,independent,withheld) for r in batch]
    fresh20=ceiling20.fresh_cohorts(prior)
    parent_splits={**exposed,**fresh20}
    rec20,hash20=verify_parent_0020(
        ceiling20_run,parent_splits,{**hashes16,**hash17,**hash18,**hash19})
    new=_cohort_rows(prior,fresh20)
    splits={**fresh20,**new}
    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
                      parent15['parent_hashes']['g2_0004_weights_sha256'])
    head_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    model,tokenizer=backbone.model_components(manifest['model_id'],
                                              manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0021_BACKBONE_UNFROZEN')
    guard=None;allowed=sel.compatibility_from_train(original);grouped={}
    for split in SPLITS:
        rows=splits[split]
        captured=rel.capture(head,model,tokenizer,rows,device=device,
                             max_length=max_length,group=split)
        if len(captured)!=len(rows):raise ValueError('GAMER_G2_0021_CAPTURE_COUNT')
        if guard is None:
            width=captured[0]['vectors'].shape[1]
            parent19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
            guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                         parent19['new_weights_sha256']).to(device)
            guard_before={k:v.detach().cpu().clone() for k,v in guard.state_dict().items()}
        saved=[]
        for index,(case,row) in enumerate(zip(captured,rows),1):
            if case['vectors'].shape[1]!=width:
                raise ValueError('GAMER_G2_0021_WIDTH_DRIFT')
            previous=rec20[split][index-1] if split in SELECT else None
            saved.append(analyze_case(case,row,head,guard,allowed,split=split,
                                      index=index,device=device,previous=previous))
            if index==1 or index%40==0 or index==len(rows):
                print(f'GAMER_G2_0021_DIAGNOSE {split} {index}/{len(rows)}',flush=True)
        grouped[split]=saved
    rel.validate_original_state(head,head_before)
    if any(not torch.equal(guard.state_dict()[k].detach().cpu(),value)
           for k,value in guard_before.items()):
        raise ValueError('GAMER_G2_0021_FROZEN_WEIGHT_CHANGED')
    selection=[r for s in SELECT for r in grouped[s]]
    assessment=[r for s in ASSESS for r in grouped[s]]
    cutoffs=calibrate_policies(selection)  # no access to assessment labels
    report={
        'schema':SCHEMA,'status':'G2_DISCRIMINATION_CEILING_DEV_NOT_CERTIFIED',
        'frozen_reference':'G2-0019 precision_guard',
        'parent_hashes':{**hashes16,**hash17,**hash18,**hash19,**hash20},
        'datasets_sha256':{s:density.sha_rows(splits[s]) for s in SPLITS},
        'new_prompt_sha256':{s:[base._sha(r['prompt']) for r in new[s]] for s in ASSESS},
        'predeclared':{'top_k':24,'fixed_threshold':THRESHOLD,
                       'new_seed_independent':SEED,'new_seed_topology':SEED+1,
                       'selection_cohorts':list(SELECT),'assessment_cohorts':list(ASSESS),
                       'min_stratum_cases':MIN_STRATUM_CASES,
                       'policies':list(POLICIES),'max_graph_edges':MAX_EDGES},
        'diagnosis':{
            'selection_0020_fresh_dev':diagnosis(selection),
            'assessment_0021_new_dev':diagnosis(assessment),
            'by_split':{s:diagnosis(grouped[s]) for s in SPLITS}},
        'hypothetical_calibration_from_0020_dev_only':cutoffs,
        'threshold_transfer':{
            cohort:{mode:evaluate_policy(cases,cutoff,mode if mode!='fixed_075' else 'global')
                    for mode,cutoff in cutoffs.items()}
            for cohort,cases in (('selection_0020_fresh_dev',selection),
                                  ('assessment_0021_new_dev',assessment))},
        'new_training':False,'new_trainable_parameters':0,
        'weights_mutated':False,'label_blind_candidate_inventory':True,
        'gold_used_only_for_retrospective_diagnosis_and_exploratory_dev_calibration':True,
        'model_selection':False,'checkpoint_selection':False,
        'production_threshold_selection':False,
        'candidate_level_metrics_conditional_on_reachability':True,
        'new_cohorts_are_dev_not_sealed':True,
        'cross_domain_generality_not_claimed':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'certification_authorized':False,'g2_certified':False,
        'g2_production_unlocked':False,'production_promotion':False,
        'graph_execution_authorized':False,'memory_writes':False}
    output.mkdir(parents=True,exist_ok=False)
    _save_gzip(grouped,output/CASES)
    _save_frontier(grouped,output/FRONTIER)
    report['cases_sha256']=backbone.sha256(output/CASES)
    report['frontier_sha256']=backbone.sha256(output/FRONTIER)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                               encoding='utf-8')
    return report
