"""G2-0030: TRAIN-only precision/graph-F1 selection from frozen G2-0029 candidates.

No DEV selection, no graph oracle at inference, no checkpoint updates or promotion.
The independent TRAIN calibration set was not used to fit G2-0029 weights.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import gzip
import json
import math
import random
import hashlib

import numpy as np
import torch
from safetensors.torch import load_file

from tools import nanojev_gamer_v1_g2_architecture_bakeoff as m

# All lineage functions are the exact G2-0029 implementations. This module
# does not reproduce or alter upstream learned scoring / graph construction.
base=m.base
backbone=m.backbone
sel=m.sel
rel=m.rel
density=m.density
source13=m.source13
general16=m.general16
ceiling17=m.ceiling17
context18=m.context18
train19=m.train19
ceiling20=m.ceiling20
ceiling21=m.ceiling21
parent22=m.parent22
parent23=m.parent23
audit24=m.audit24
parent25=m.parent25
parent26=m.parent26
parent27=m.parent27
parent28=m.parent28
verify_parent28=m.verify_parent28
fresh_cohorts=m.fresh_cohorts
REFERENCE=m.REFERENCE
SEEDS=m.SEEDS
STEPS=m.STEPS

SCHEMA='nanojev.gamer.graph.g2.precision-constrained-selection.v1'
REPORT='g2_graph_precision_selection_report.json'
LEDGER='g2_graph_precision_selection_per_case.jsonl.gz'
POLICIES='g2_graph_precision_selection_frozen_policies.json'
SEED=460130
CAL_FIT=120
CAL_AUDIT=60
NEW=('new_independent_dev_0030','new_topology_dev_0030')
REPLAY=m.NEW
ARMS=m.ARMS
# Fixed before reading any G2-0030 development labels.
ALPHAS=(0.25,0.50,1.00)
THRESHOLDS=(.55,.65,.75,.82,.88,.93)
MARGINS=(None,2.0,4.0)
REFERENCE_GATES=(None,.60)
REQUIRED_GAIN=.02
CONFIDENCE_DRAWS=300


def _hash_rows(rows):
    return density.sha_rows(rows)


def verify_parent29(directory, rows, parent_hashes, train_sha):
    """Check frozen parent weights, lineages, all 896 case rows and aggregates."""
    p=Path(directory)
    rp,cp,wp=(p/m.REPORT,p/m.LEDGER,p/m.WEIGHTS)
    report=json.loads(rp.read_text(encoding='utf-8'))
    if report.get('schema')!=m.SCHEMA:
        raise ValueError('GAMER_G2_0030_PARENT_0029_CONTRACT:schema')
    if set(rows)!=set(m.SPLITS):
        raise ValueError('GAMER_G2_0030_PARENT_0029_CONTRACT:cohorts')
    required={'schema':m.SCHEMA,
        'status':'ARCHITECTURE_BAKEOFF_DEVELOPMENT_NOT_CERTIFIED',
        'parent_hashes':parent_hashes,
        'train_dataset_sha256':train_sha,
        'datasets_sha256':{k:_hash_rows(rows[k]) for k in m.SPLITS},
        'frozen_backbone':True,'frozen_g1':True,'frozen_g2_rank_head':True,
        'frozen_parent_weights':True,'candidate_selection_uses_gold':False,
        'dev_labels_used_for_training':False,'dev_checkpoint_selection':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'production_promotion':False,'g2_certified':False,
        'graph_execution_authorized':False,'certification_authorized':False}
    for key,value in required.items():
        if report.get(key)!=value:
            raise ValueError('GAMER_G2_0030_PARENT_0029_CONTRACT:'+key)
    pre=report.get('predeclared',{})
    if (pre.get('training_seeds')!=list(m.SEEDS) or pre.get('steps_per_seed')!=m.STEPS or
            pre.get('arms')!=list(m.ARMS) or pre.get('reference')!=m.REFERENCE or
            pre.get('fresh')!=list(m.NEW) or pre.get('replay')!=list(m.REPLAY)):
        raise ValueError('GAMER_G2_0030_PARENT_0029_PROTOCOL')
    if (report.get('cases_sha256')!=backbone.sha256(cp) or
            report.get('development_weights_sha256')!=backbone.sha256(wp)):
        raise ValueError('GAMER_G2_0030_PARENT_0029_HASH_DRIFT')
    expected_names={REFERENCE,*[f'{arm}_s{seed}' for arm in ARMS for seed in SEEDS]}
    found={k:[] for k in m.SPLITS}
    with gzip.open(cp,'rt',encoding='utf-8') as file:
        for line in file:
            entry=json.loads(line);group=entry.get('group')
            if group not in found or len(found[group])>=len(rows[group]):
                raise ValueError('GAMER_G2_0030_PARENT_0029_UNEXPECTED_ROW')
            i=len(found[group]);r=rows[group][i]
            if (entry.get('index')!=i+1 or
                    entry.get('prompt_sha256')!=base._sha(r['prompt']) or
                    entry.get('family')!=r['family'] or
                    entry.get('gold_total')!=len(base.gold_binding_edges(r)) or
                    set(entry.get('metrics',{}))!=expected_names):
                raise ValueError('GAMER_G2_0030_PARENT_0029_CASE_DRIFT')
            found[group].append(entry)
    if any(len(found[k])!=len(rows[k]) for k in m.SPLITS):
        raise ValueError('GAMER_G2_0030_PARENT_0029_ROW_COUNT')
    for split,records in found.items():
        if report['by_split'][split]!=m.aggregate(records):
            raise ValueError('GAMER_G2_0030_PARENT_0029_METRIC_DRIFT:'+split)
    for name,splits in (('replayed_0028_dev',m.REPLAY),('new_0029_dev',m.NEW)):
        all_records=[r for split in splits for r in found[split]]
        if report['evaluation'][name]!=m.aggregate(all_records):
            raise ValueError('GAMER_G2_0030_PARENT_0029_EVALUATION_DRIFT:'+name)
    if report.get('gates')!=m.promotion_gates(report['evaluation']['new_0029_dev'],
                                                 report['by_split'][m.NEW[1]]):
        raise ValueError('GAMER_G2_0030_PARENT_0029_GATE_DRIFT')
    tensors=load_file(str(wp),device='cpu')
    if (not tensors or any(not torch.isfinite(v).all() for v in tensors.values()) or
            {k.split('.',1)[0] for k in tensors}!={f'{a}_s{s}' for a in ARMS for s in SEEDS}):
        raise ValueError('GAMER_G2_0030_PARENT_0029_WEIGHT_KEYS')
    return report,found,tensors,{
        'g2_0029_report_sha256':backbone.sha256(rp),
        'g2_0029_cases_sha256':backbone.sha256(cp),
        'g2_0029_weights_sha256':backbone.sha256(wp)}


def load_frozen_challengers(tensors,width,dim,device='cpu'):
    """Every learned parameter must have an exact saved counterpart."""
    classes={'predicate_argument':m.PredicateArgument,
             'dynamic_span_graph':m.DynamicSpanGraph,
             'joint_graph_decoder':m.JointGraphDecoder}
    models={};found=set()
    for seed in SEEDS:
        nets={}
        for name,cls in classes.items():
            prefix=f'{name}_s{seed}.'
            state={k[len(prefix):]:v for k,v in tensors.items() if k.startswith(prefix)}
            found.update(prefix+k for k in state)
            model=cls(width,dim)
            if (set(state)!=set(model.state_dict()) or
                    any(state[k].shape!=v.shape for k,v in model.state_dict().items())):
                raise ValueError('GAMER_G2_0030_PARENT_0029_WEIGHT_SHAPE:'+prefix)
            model.load_state_dict(state,strict=True)
            model=model.to(device).eval().requires_grad_(False)
            nets[name]=model
        models[seed]=nets
    if found!=set(tensors):raise ValueError('GAMER_G2_0030_PARENT_0029_UNUSED_WEIGHTS')
    return models


def calibration_and_dev(history, fresh29):
    """Novel, explicitly TRAIN synthetic calibration; separate untouched DEV."""
    prior=[*history,*(r for group in m.NEW for r in fresh29[group])]
    calib=general16.curriculum_rows((prior,),limit=CAL_FIT+CAL_AUDIT,seed=SEED)
    random.Random(SEED+3).shuffle(calib)  # balance topology across fit/audit
    if len(calib)!=180 or any(r.get('split')!='train' for r in calib):
        raise ValueError('GAMER_G2_0030_TRAIN_CALIBRATION_CONTRACT')
    exclusion=[*prior,*calib]
    independent=general16.independent_rows((exclusion,),limit=320,seed=SEED+100)
    topology=general16.heldout_topology_rows((exclusion,independent),limit=128,seed=SEED+101)
    all_rows=[*exclusion,*independent,*topology]
    if (len({base._sha(r['prompt']) for r in all_rows})!=len(all_rows) or
            any(r.get('split')!='dev' for r in (*independent,*topology)) or
            {r['structural_topology'] for r in topology}!={'2x4','4x2','4x4'} or
            any(r['structural_topology'] in {'2x4','4x2','4x4'} for r in calib)):
        raise ValueError('GAMER_G2_0030_DATA_LEAKAGE')
    return calib,dict(zip(NEW,(independent,topology)))


def collect_scores(ex,nets):
    """Remove gold, text and high-dimensional input from the decoder interface."""
    if len(ex['edges'])!=ex['all_inventory_edges']:
        raise ValueError('GAMER_G2_0030_INVENTORY_PRUNED')
    raw={REFERENCE:ex['reference'].detach().cpu().float().numpy().copy()}
    with torch.inference_mode():
        for seed,models in nets.items():
            for arm,model in models.items():
                item={k:v.to(next(model.parameters()).device) for k,v in ex.items()
                      if k in ('features','reference','nodes','src','dst','group')}
                s=model(item).detach().cpu().float().numpy().copy()
                raw[f'{arm}_s{seed}']=s
    if not all(np.isfinite(v).all() and len(v)==len(ex['edges']) for v in raw.values()):
        raise ValueError('GAMER_G2_0030_NONFINITE_SCORE')
    indices=list(ex['group'].tolist())
    _,inverse=np.unique(np.asarray(indices,dtype=np.int64),return_inverse=True)
    return {'raw':raw,'groups':inverse.astype(np.int32),
            'edges':ex['edges'],'gold':ex['gold'],
            'gold_total':len(ex['gold']),
            'candidate_gold':ex['all_candidate_gold'],
            'candidate_count':ex['all_inventory_edges']}


def select_graph(item,*,source,alpha,threshold,margin=None,reference_gate=None):
    """Label-blind calibrated multi-positive selection and existing graph cap."""
    if source!=REFERENCE and source not in item['raw']:
        raise ValueError('GAMER_G2_0030_INVALID_SCORE_SOURCE')
    if not len(item['edges']):return set()
    ref=item['raw'][REFERENCE]
    if source==REFERENCE:
        if alpha!=0:raise ValueError('GAMER_G2_0030_BASELINE_ALPHA')
        scores=ref
    else:
        if alpha not in ALPHAS:raise ValueError('GAMER_G2_0030_BAD_ALPHA')
        scores=ref+alpha*(item['raw'][source]-ref)
    cutoff=math.log(threshold/(1-threshold))
    keep=scores>=cutoff
    if reference_gate is not None:
        cutoff_ref=math.log(reference_gate/(1-reference_gate))
        keep &= ref>=cutoff_ref
    if margin is not None:
        maximum=np.full(int(item['groups'].max())+1,-np.inf,dtype=np.float64)
        np.maximum.at(maximum,item['groups'],scores)
        keep &= scores>=maximum[item['groups']]-margin
    indices=np.flatnonzero(keep)
    if len(indices)>source13.MAX_EDGES:
        indices=sorted(indices,key=lambda i:(-float(scores[i]),item['edges'][i]))[:source13.MAX_EDGES]
    return {item['edges'][int(i)] for i in indices}


def measure(selected,item):
    gold=item['gold']
    return {'correct':len(selected&gold),
            'false_positive':len(selected-gold),
            'missing':len(gold-selected),
            'exact_cases':int(selected==gold)}


def _totals_f1(totals):
    return 2*totals['correct']/max(1,2*totals['correct']+totals['false_positive']+totals['missing'])


def select_calibration_policies(cases,*,seeds=SEEDS,arms=ARMS):
    """Maximize *graph F1* on TRAIN calibration-fit only; freeze before DEV.

    A held-out TRAIN calibration-audit cohort is reported but never used to
    revise the selected policies. Reference gets the same threshold search.
    """
    if not cases or not all(item['gold_total']>=0 for item in cases):
        raise ValueError('GAMER_G2_0030_EMPTY_CALIBRATION')
    def score(policy):
        total=Counter()
        for case in cases:total.update(measure(select_graph(case,**policy),case))
        return _totals_f1(total),total
    policies={};scores={}
    baseline_best=None
    for threshold in THRESHOLDS:
        for margin in MARGINS:
            policy=dict(source=REFERENCE,alpha=0.,threshold=threshold,
                        margin=margin,reference_gate=None)
            f1,totals=score(policy)
            # On ties use conservative simpler rule (no rank margin and closest
            # to fixed parent threshold), rather than a lucky complex decoder.
            quality=(f1,-int(margin is not None),-abs(threshold-m.THRESHOLD))
            if baseline_best is None or quality>baseline_best[0]:
                baseline_best=(quality,policy,totals)
    policies['calibrated_reference']=baseline_best[1]
    scores['calibrated_reference']=_totals_f1(baseline_best[2])
    print('GAMER_G2_0030_CALIBRATE reference F1='+f"{scores['calibrated_reference']:.5f}",flush=True)
    for arm in arms:
        for seed in seeds:
            name=f'{arm}_s{seed}'
            best=None
            for alpha in ALPHAS:
                for threshold in THRESHOLDS:
                    for margin in MARGINS:
                        for gate in REFERENCE_GATES:
                            policy=dict(source=name,alpha=alpha,threshold=threshold,
                                        margin=margin,reference_gate=gate)
                            f1,totals=score(policy)
                            quality=(f1,-int(gate is not None),-int(margin is not None),
                                     -alpha,-abs(threshold-m.THRESHOLD))
                            if best is None or quality>best[0]:best=(quality,policy,totals)
            policies[name]=best[1];scores[name]=_totals_f1(best[2])
            print(f'GAMER_G2_0030_CALIBRATE {name} F1={scores[name]:.5f}',flush=True)
    return policies,scores


def ranking_counts(item,score):
    """Rank gold against hardest negative within each source/relation group."""
    failures=0;competing=0
    buckets=defaultdict(list)
    for i,g in enumerate(item['groups']):buckets[int(g)].append(i)
    gold=item['gold']
    for ids in buckets.values():
        pos=[i for i in ids if item['edges'][i] in gold]
        neg=[i for i in ids if item['edges'][i] not in gold]
        if not pos or not neg:continue
        maximum=float(max(score[i] for i in neg))
        competing+=len(pos)
        failures+=sum(float(score[i])<=maximum for i in pos)
    return competing,failures


def evaluate_case(item,policies,*,split,index,row,n_anchors,prior=None):
    raw=item['raw']
    baseline={ 'source':REFERENCE,'alpha':0.,'threshold':m.THRESHOLD,
               'margin':None,'reference_gate':None }
    configurations={'identity_peer_0026':baseline,**policies}
    counts={}
    for name,policy in configurations.items():
        graph=select_graph(item,**policy)
        result=measure(graph,item)
        src=policy['source'];alpha=policy['alpha'];ref=raw[REFERENCE]
        ranked=ref if src==REFERENCE else ref+alpha*(raw[src]-ref)
        competing,failures=ranking_counts(item,ranked)
        result.update(competing_gold=competing,ranking_failures=failures)
        counts[name]=result
    if prior is not None:
        expected=prior['metrics'][REFERENCE]
        for k in ('correct','false_positive','missing','competing_gold','ranking_failures'):
            if counts['identity_peer_0026'][k]!=expected[k]:
                raise ValueError('GAMER_G2_0030_PARENT_0029_REPLAY_DRIFT:'+k)
    return {'group':split,'index':index,'prompt_sha256':base._sha(row['prompt']),
        'family':row['family'],'structural_topology':row.get('structural_topology','native'),
        'density_bucket':density._bucket(n_anchors),
        'gold_total':item['gold_total'],'candidate_gold':item['candidate_gold'],
        'candidate_count':item['candidate_count'],'metrics':counts}


def aggregate(rows):
    if not rows:raise ValueError('GAMER_G2_0030_EMPTY_METRICS')
    names=set(rows[0]['metrics'])
    totals={name:Counter() for name in names}
    for row in rows:
        if set(row['metrics'])!=names:raise ValueError('GAMER_G2_0030_INCONSISTENT_MODELS')
        for name in names:totals[name].update(row['metrics'][name])
    summary={}
    for name,values in totals.items():
        tp,fp,fn=values['correct'],values['false_positive'],values['missing']
        summary[name]={'correct':tp,'false_positive':fp,'missing':fn,
            'f1':_totals_f1(values),'precision':tp/max(1,tp+fp),
            'recall':tp/max(1,tp+fn),
            'exact_graph_rate':values['exact_cases']/len(rows),
            'exact_cases':values['exact_cases'],
            'competing_gold':values['competing_gold'],
            'ranking_failures':values['ranking_failures'],
            'ranking_failure_rate':values['ranking_failures']/max(1,values['competing_gold'])}
    cg=sum(r['candidate_gold'] for r in rows)
    gt=sum(r['gold_total'] for r in rows)
    return {'cases':len(rows),'gold_total':gt,'candidate_gold':cg,
        'offline_deterministic_candidate_oracle_ceiling_f1':2*cg/max(1,cg+gt),
        'models':summary}


def paired_interval(rows,name,baseline='identity_peer_0026',draws=CONFIDENCE_DRAWS,seed=SEED):
    rng=random.Random(seed);deltas=[]
    for _ in range(draws):
        selection=[rows[rng.randrange(len(rows))] for _ in rows]
        results=aggregate(selection)['models']
        deltas.append(results[name]['f1']-results[baseline]['f1'])
    deltas.sort()
    return {'f1_delta_CI95':[deltas[int(.025*(draws-1))],deltas[int(.975*(draws-1))]],
            'draws':draws,'method':'paired_case_bootstrap'}


def development_gates(fresh,ood,*,seeds=SEEDS,arms=ARMS):
    ref=fresh['models']['identity_peer_0026'];cal=fresh['models']['calibrated_reference']
    ood_ref=ood['models']['identity_peer_0026'];ood_cal=ood['models']['calibrated_reference']
    gates={}
    for arm in arms:
        tests=[]
        for seed in seeds:
            name=f'{arm}_s{seed}';v=fresh['models'][name];o=ood['models'][name]
            passes=(v['f1']-ref['f1']>=REQUIRED_GAIN and
                    v['f1']-cal['f1']>=REQUIRED_GAIN and
                    v['ranking_failures']<=ref['ranking_failures'] and
                    o['f1']>=max(ood_ref['f1'],ood_cal['f1']) and
                    o['ranking_failures']<=ood_ref['ranking_failures'])
            tests.append({'seed':seed,'f1_delta_vs_frozen':v['f1']-ref['f1'],
                'f1_delta_vs_calibrated':v['f1']-cal['f1'],
                'ood_f1_delta_vs_calibrated':o['f1']-ood_cal['f1'],
                'ranking_failure_delta':v['ranking_failures']-ref['ranking_failures'],
                'pass':bool(passes)})
        gates[arm]={'all_seeds_pass':all(v['pass'] for v in tests),'seeds':tests}
    return {'required_f1_gain_vs_both_controls':REQUIRED_GAIN,
            'all_three_seeds_required':True,'no_ranking_regression':True,
            'withheld_topology_no_f1_regression':True,
            'arms':gates,'development_success':any(v['all_seeds_pass'] for v in gates.values()),
            'production_promotion':False,'certification_authorized':False}


def _write_ledger(path,records):
    with open(path,'wb') as f:
        with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as zipped:
            for r in records:
                zipped.write((json.dumps(r,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n').encode())


def _prepare(*,root,g1_validation_report,g2_run,selection_run,discrimination_run,
        relational_run,density_run,consistency_run,context_run,learned_run,
        recall_run,crowded_run,robustness_run,diagnosis_run,source_run,
        specialization_run,routing_robustness_run,general_run,ceiling_run,
        context18_run,train19_run,ceiling20_run,ceiling21_run,competition22_run,
        context23_run,audit24_run,intervention25_run,identity26_run,evidence27_run,
        probe28_run,bakeoff29_run,output,device='cuda',max_length=2048,steps=STEPS):
    root,output=Path(root),Path(output)
    if output.exists():raise FileExistsError('GAMER_G2_0030_IMMUTABLE_OUTPUT')
    if max_length!=2048 or steps!=STEPS:
        raise ValueError('GAMER_G2_0030_FIXED_PROTOCOL')
    head,manifest,dataset,bootstrap,_,_,hashes=sel.load_parents(root,g1_validation_report,g2_run)
    original=base.original_training_rows(dataset,source13.TRAIN_LIMIT)
    if base._sha('\n'.join(base._sha(r['prompt']) for r in original))!=bootstrap.get('train_prompt_hashes_sha256'):
        raise ValueError('GAMER_G2_0028_TRAIN_LINEAGE')
    history,prior15,augmented,calibration,independent,withheld=ceiling17.regeneration(original)
    if density.sha_rows(history[1])!=bootstrap.get('dev_dataset_sha256'):
        raise ValueError('GAMER_G2_0028_BOOTSTRAP_LINEAGE')
    parent15,_,parent13=general16.verify_robust15_parent(
        routing_robustness_run,prior15,source_run,specialization_run)
    if parent15['parent_hashes']['g1_validation_report_sha256']!=hashes['g1_validation_report_sha256']:
        raise ValueError('GAMER_G2_0028_G1_LINEAGE')
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
        raise ValueError('GAMER_G2_0028_PARENT_0016_LINEAGE')
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
    if len(training)!=640:raise ValueError('GAMER_G2_0028_TRAIN_COUNT')
    all_previous=[training,calibration,independent,withheld,*fresh20.values(),
                  *fresh21.values(),*fresh22.values(),*fresh23.values()]
    if len({base._sha(r['prompt']) for batch in all_previous for r in batch})!=sum(map(len,all_previous)):
        raise ValueError('GAMER_G2_0028_PARENT_DEV_OVERLAP')
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
        raise ValueError('GAMER_G2_0028_NEW_DEV_OVERLAP')
    parent26_report,old26,h26=parent27.verify_parent26(identity26_run,{**fresh25,**fresh26},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25},
        density.sha_rows(training))
    history27=[*history26,*(r for batch in fresh26.values() for r in batch)]
    fresh27=parent27.fresh_cohorts(history27,training)
    if len({base._sha(r['prompt']) for r in (*history27,*fresh27[parent27.NEW[0]],*fresh27[parent27.NEW[1]])})!=len(history27)+448:
        raise ValueError('GAMER_G2_0028_FRESH_PROMPT_OVERLAP')

    report27,old27,h27=parent28.verify_parent27(evidence27_run,{**fresh26,**fresh27},
        {**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26},
        density.sha_rows(training))
    history28=[*history27,*(r for batch in fresh27.values() for r in batch)]
    fresh28=parent28.fresh_cohorts(history28,training)
    evaluate={**fresh27,**fresh28}



    # Parent G2-0028 must match all reconstructed historical cohorts and
    # its saved tensor and per-case ledgers before a new optimizer is created.
    ancestors={**h16,**h17,**h18,**h19,**h20,**h21,**h22,**h23,**h24,**h25,**h26,**h27}
    parent28_report,old28,h28=verify_parent28(probe28_run,{**fresh27,**fresh28},
        ancestors,density.sha_rows(training))
    history29=[*history28,*(row for batch in fresh28.values() for row in batch)]
    fresh29=fresh_cohorts(history29,training)
    evaluation={**fresh28,**fresh29}
    assert len(evaluation)==4

    parent29_report,old29,weights29,h29=verify_parent29(
        bakeoff29_run,evaluation,{**ancestors,**h28},density.sha_rows(training))

    head=general16.load_frozen_rank4(head,Path(relational_run)/rel.WEIGHTS,
         parent15['parent_hashes']['g2_0004_weights_sha256'])
    rank_before={k:v.detach().cpu().clone() for k,v in head.state_dict().items()}
    head=head.to(device).eval().requires_grad_(False)
    model,tokenizer=backbone.model_components(manifest['model_id'],
        manifest['source_adapter'],device)
    if any(p.requires_grad for p in model.parameters()):
        raise ValueError('GAMER_G2_0030_BACKBONE_UNFROZEN')
    old19=json.loads((Path(train19_run)/train19.REPORT).read_text(encoding='utf-8'))
    allowed=sel.compatibility_from_train(original)
    width=parent28.rel.capture(head,model,tokenizer,training[:1],device=device,
        max_length=max_length,group='train_0029_width')[0]['vectors'].shape[-1]
    guard=ceiling20.load_guarded(Path(train19_run)/train19.WEIGHTS,width,
                                 old19['new_weights_sha256'])
    frozen23=audit24.load_parent23_models(context23_run,guard,
               parent23_report['new_weights_sha256'])['relational_context_0023']
    reference=parent27.load_parent26_models(identity26_run,frozen23,
               parent26_report['new_weights_sha256'])[REFERENCE]
    original_reference={k:v.detach().cpu().clone() for k,v in reference.state_dict().items()}
    reference=reference.to(device).eval().requires_grad_(False)

    models29=load_frozen_challengers(weights29,width,
        4*width+source13.EXTRAS+1+20,device=device)
    return dict(root=root,output=output,training=training,original=original,
        history29=history29,fresh29=fresh29,head=head,model=model,tokenizer=tokenizer,
        reference=reference,allowed=allowed,width=width,models29=models29,
        ancestors={**ancestors,**h28,**h29},parent29_report=parent29_report,
        old29=old29,hashes=hashes,head_before=rank_before,
        reference_before=original_reference)



def run(*,bakeoff29_run,output,device='cuda',max_length=2048,**kwargs):
    """A single predeclared calibration followed by untouched DEV evaluation."""
    state=_prepare(bakeoff29_run=bakeoff29_run,output=output,device=device,
                   max_length=max_length,**kwargs)
    calibration,fresh=calibration_and_dev(state['history29'],state['fresh29'])
    # All calibration prompts are new TRAIN data. No parent G2-0029 training
    # examples, no previous DEV cohort and no new DEV examples are eligible.
    parent_shas={digest for k in m.NEW
                    for digest in state['parent29_report']['new_prompt_sha256'][k]}
    assert not (parent_shas&{base._sha(r['prompt']) for r in calibration})
    if len(fresh[NEW[0]])!=320 or len(fresh[NEW[1]])!=128:
        raise ValueError('GAMER_G2_0030_FRESH_CASE_COUNT')

    def evaluate_raw(rows,split):
        entries=[]
        for start in range(0,len(rows),40):
            batch=rows[start:start+40]
            captures=rel.capture(state['head'],state['model'],state['tokenizer'],
                  batch,device=device,max_length=max_length,group=split)
            for index,(row,case) in enumerate(zip(batch,captures),start+1):
                ex=m.case_inventory(case,row,state['head'],state['reference'],
                         state['allowed'],device=device,train=False)
                entries.append((collect_scores(ex,state['models29']),row,
                                len(case['anchors']),index))
            print(f'GAMER_G2_0030_CAPTURE {split} {min(start+40,len(rows))}/{len(rows)}',flush=True)
        return entries

    cal_raw=evaluate_raw(calibration,'train_calibration_0030')
    policies,fit_f1=select_calibration_policies([v[0] for v in cal_raw[:CAL_FIT]])
    assert set(policies)=={'calibrated_reference',*[f'{arm}_s{seed}'
                                for arm in ARMS for seed in SEEDS]}
    audit=[]
    for item,row,n,index in cal_raw[CAL_FIT:]:
        audit.append(evaluate_case(item,policies,split='train_calibration_audit_0030',
                                   index=index-CAL_FIT,row=row,n_anchors=n))
    fit=[]
    for item,row,n,index in cal_raw[:CAL_FIT]:
        fit.append(evaluate_case(item,policies,split='train_calibration_fit_0030',
                                 index=index,row=row,n_anchors=n))
    del cal_raw

    evaluated={}
    # Previous DEV is replayed only to catch frozen-reference drift. It is not
    # available to search, rank or modify the calibration policy.
    for split,rows in {**state['fresh29'],**fresh}.items():
        raw=evaluate_raw(rows,split)
        results=[]
        for item,row,n,index in raw:
            old=state['old29'][split][index-1] if split in m.NEW else None
            results.append(evaluate_case(item,policies,split=split,index=index,
                    row=row,n_anchors=n,prior=old))
        evaluated[split]=results
        del raw

    rel.validate_original_state(state['head'],state['head_before'])
    if (any(not torch.equal(v,state['reference'].state_dict()[k].detach().cpu())
            for k,v in state['reference_before'].items()) or
            any(p.requires_grad for p in state['model'].parameters())):
        raise ValueError('GAMER_G2_0030_FROZEN_PARENT_MUTATION')
    if any(p.requires_grad for models in state['models29'].values() for net in models.values()
           for p in net.parameters()):
        raise ValueError('GAMER_G2_0030_CHALLENGER_TRAINED')

    fresh_cases=[row for name in NEW for row in evaluated[name]]
    replay_cases=[row for name in REPLAY for row in evaluated[name]]
    fresh_summary=aggregate(fresh_cases)
    ood_summary=aggregate(evaluated[NEW[1]])
    gates=development_gates(fresh_summary,ood_summary)
    report={
        'schema':SCHEMA,'status':'TRAIN_CALIBRATED_PRECISION_SELECTION_DEV_NOT_CERTIFIED',
        'parent_hashes':state['ancestors'],
        'g2_0029_weight_retraining':False,
        'calibration_train_only':True,
        'calibration_excluded_from_0029_training':True,
        'calibration_fit_not_reused_for_development':True,
        'calibration_audit_not_used_for_policy_selection':True,
        'dev_labels_used_for_policy_selection':False,
        'dev_checkpoint_selection':False,
        'candidate_selection_uses_gold':False,
        'frozen_backbone':True,'frozen_g1':True,'frozen_rank_head':True,
        'frozen_g2_0026':True,'frozen_g2_0029_challengers':True,
        'sealed_test_used':False,'challenge_or_test_split_used':False,
        'production_promotion':False,'g2_certified':False,
        'graph_execution_authorized':False,'certification_authorized':False,
        'predeclared':{
          'calibration_seed':SEED,'calibration_split':'train',
          'calibration_fit_count':CAL_FIT,'calibration_audit_count':CAL_AUDIT,
          'fresh_independent_seed':SEED+100,'fresh_topology_seed':SEED+101,
          'candidate_top_k':m.TOP_K,'architecture_seeds':list(SEEDS),
          'alphas':list(ALPHAS),'thresholds':list(THRESHOLDS),
          'group_best_margins':list(MARGINS),'reference_gates':list(REFERENCE_GATES),
          'no_exclusive_destination_assumption':True,
          'max_edges':source13.MAX_EDGES,'f1_success_delta':REQUIRED_GAIN,
          'ranking_and_ood_nonregression_required':True,
          'confidence_draws':CONFIDENCE_DRAWS,
          'no_DEV_policy_or_seed_selection':True},
        'train_dataset_sha256':_hash_rows(state['training']),
        'calibration_fit_sha256':_hash_rows(calibration[:CAL_FIT]),
        'calibration_audit_sha256':_hash_rows(calibration[CAL_FIT:]),
        'datasets_sha256':{name:_hash_rows(rows) for name,rows in
                              {**state['fresh29'],**fresh}.items()},
        'policy_fit_graph_f1':fit_f1,
        'policies':policies,
        'calibration_fit':aggregate(fit),
        'calibration_audit':aggregate(audit),
        'evaluation':{'replayed_0029_dev':aggregate(replay_cases),
                      'new_0030_dev':fresh_summary},
        'by_split':{name:aggregate(records) for name,records in evaluated.items()},
        'by_density':{name:{bucket:aggregate([r for r in records if r['density_bucket']==bucket])
                            for bucket in sorted({r['density_bucket'] for r in records})}
                       for name,records in {'replayed_0029_dev':replay_cases,
                                            'new_0030_dev':fresh_cases}.items()},
        'by_family':{name:{family:aggregate([r for r in records if r['family']==family])
                           for family in sorted({r['family'] for r in records})}
                     for name,records in {'replayed_0029_dev':replay_cases,
                                          'new_0030_dev':fresh_cases}.items()},
        'by_topology':{name:{topology:aggregate([r for r in records if r['structural_topology']==topology])
                             for topology in sorted({r['structural_topology'] for r in records})}
                       for name,records in {'replayed_0029_dev':replay_cases,
                                            'new_0030_dev':fresh_cases}.items()},
        'gates':gates,
        'paired_intervals':{name:paired_interval(fresh_cases,name,seed=SEED+index)
                            for index,name in enumerate(sorted(policies))},
        'limitations':{
          'no_new_representation_learning':True,
          'calibration_is_synthetic_train_distribution_not_real_world':True,
          'global_graph_cap_retained':True,
          'no_use_of_original_deterministic_executable':True,
          'gold_oracle_metrics_are_offline_label_informed_and_not_deployable':True,
          'DEV_is_not_sealed_test_and_does_not_authorize_promotion':True}}
    directory=Path(output)
    directory.mkdir(parents=True,exist_ok=False)
    ordered=[*fit,*audit,*(r for split in (*REPLAY,*NEW) for r in evaluated[split])]
    _write_ledger(directory/LEDGER,ordered)
    (directory/POLICIES).write_text(json.dumps(policies,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    report['ledger_sha256']=backbone.sha256(directory/LEDGER)
    report['policies_sha256']=backbone.sha256(directory/POLICIES)
    (directory/REPORT).write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
    return report
