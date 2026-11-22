"""Gamer G2-0103: global admission stability, distribution shift and gate-causality audit.

Predeclared comparison: frozen 0096 decoder (offset 0) vs SAME decoder with
strictly global admission offset +1.0.  No fit, alternate-arm selection, weight
edit, or automatic promotion is possible. Six new TRAIN audit seeds and ten
independent random streams (one 200-case block per stream by default) form a
new 2,000-example sealed confirmation cohort. Predict with holdout gold absent.

Run from main_computer/nanojev:
    python -u -m tools.nanojev_gamer_v1_g2_admission_stability_0103 run --device cuda

The seed streams use the same synthetic author-annotated generator; they are
NOT independent external distributions. CIs are descriptive bootstrap estimates.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import random
import statistics

import torch
from tools import nanojev_gamer_v1_g2_threshold_stability_0102 as prior
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.admission-stability-0103.v1'
NAME = 'oracle-fabric-g2-admission-stability-0103'
ROOT_DEFAULT = prior.ROOT_DEFAULT
FAMILIES = ('allocation', 'encounter', 'overall')
FOLDS = prior.FOLD_NAMES
AUDIT_SEEDS = tuple(4_103_000 + i*12011 for i in range(6))
HOLDOUT_SEEDS = tuple(8_103_000 + i*14243 for i in range(10))
BOOT_SEED = 5_103_456
AUDIT_CASES = 480
HOLDOUT_CASES = 2000
BASELINE, STRICT = 'baseline', 'strict_1'
ARMS = (BASELINE, STRICT)
BOOTSTRAPS = 600
# Exact SHA-256 of the original published 0103 runner. Allow an in-flight run
# to resume after installing this operational-only checkpoint/telemetry fix.
# No arbitrary runner revisions or changed sealed factors are trusted.
LEGACY_RUNNER_SHA256 = 'eef6582d636e9a426eaab771e19364303184d9511d4d46c6ab77b8182516e927'
CHECKPOINT_EVERY = 10
DIAGNOSTIC_EVERY = 20
sha, digest, read, write, rows, write_rows = (prior.sha, prior.digest,
    prior.read, prior.write, prior.rows, prior.write_rows)
source, structural, bakeoff = prior.source, prior.structural, prior.bakeoff
corpus, contract = prior.corpus, prior.contract

# All decision thresholds are predeclared, NOT inferred from challenge results.
POLICY = {
    'fixed_offset': 1.0, 'minimum_holdout_f1_gain': 0.005,
    'minimum_positive_seed_groups': 8, 'maximum_family_f1_regression': 0.0,
    'maximum_exacts_lost_case_rate_upper95': 0.015,
    'maximum_conditional_exact_loss_upper95': 0.15,
    'minimum_net_exact_graphs': 0,
    'bootstrap_iterations': BOOTSTRAPS,
    'classification': 'DIAGNOSTIC_ONLY_NO_PRODUCTION_PERMISSION',
}


def require(ok, code):
    if not ok:
        raise ValueError('G2_0103_' + code)


def location(root, output=None):
    return (Path(output) if output else Path(root)/NAME).resolve()


def _prior_prompt_hashes(root, dataset):
    """Only known campaign locations; never recursively walk the runtime tree."""
    used, provenance = prior._prompt_hashes(root, dataset)
    names = (prior.NAME, prior.last.NAME, prior.previous.NAME,
             prior.source.OUTPUT_NAME, prior.previous.previous.NAME,
             prior.previous.previous.earlier.NAME)
    for campaign in dict.fromkeys(names):
        for filename in ('prompts.jsonl', 'train_prompts.jsonl',
                         'holdout_prompts.jsonl'):
            path = Path(root)/campaign/'seal'/filename
            if path.is_file():
                provenance[str(campaign)+'/'+filename] = sha(path)
                for record in rows(path):
                    h = record.get('prompt_sha256')
                    require(isinstance(h,str) and len(h)==64, 'PRIOR_PROMPT_HASH_INVALID')
                    used.add(h)
    return used, provenance


def _make_cohort(root, dataset, audit_cases, holdout_cases):
    require(48<=audit_cases<=2400 and audit_cases%12==0, 'AUDIT_CASES_MULTIPLE_OF_12')
    require(40<=holdout_cases<=5000 and holdout_cases%20==0, 'HOLDOUT_CASES_MULTIPLE_OF_20')
    used,excluded=_prior_prompt_hashes(root,dataset)
    cohorts={}
    for split,total,seeds in (('audit',audit_cases,AUDIT_SEEDS),
                              ('holdout',holdout_cases,HOLDOUT_SEEDS)):
        count=total//len(seeds)
        require(count%2==0,'SEED_BLOCK_NOT_FAMILY_BALANCED')
        public,answers=[],[]
        for group,seed in enumerate(seeds):
            rng=random.Random(seed)
            for local in range(count):
                i=group*count+local
                family='allocation' if local%2==0 else 'encounter'
                generator=corpus.allocation if family=='allocation' else corpus.encounter
                for attempt in range(4096):
                    # No reused prompt, even if one stream produces a duplicate.
                    record=generator(rng, 'train' if split=='audit' else 'test',
                                     303_000_000+group*500000+local*4096+attempt)
                    h=digest(record['prompt'])
                    if h not in used:break
                else:require(False,'UNIQUE_PROMPT_GENERATION_EXHAUSTED')
                require(record.get('annotation_provenance')==
                        'AUTHOR_MARKED_IN_SOURCE_GENERATOR','AUTHOR_GOLD_REQUIRED')
                contract.validate_graph(record['graph'],complete=True)
                used.add(h)
                cid=f'g2-0103-{split}-{i:05d}'
                attributes={'case_id':cid,'family':family,'prompt_sha256':h,
                            'stream':group,'fold':FOLDS[group] if split=='audit' else None}
                public.append({**attributes,'prompt':record['prompt']})
                answers.append({**attributes,'graph':record['graph']})
        cohorts[split]=(public,answers)
    return cohorts,excluded


def _source(root):
    identity,spec=prior._source(root)
    require(spec.get('method')=='joint','JOINT_DECODER_REQUIRED')
    require(source._candidate_info(identity['checkpoint'])==identity,
            'SOURCE_CHECKPOINT_DRIFT')
    return identity,spec


def prepare(*,root=ROOT_DEFAULT,output=None,audit_cases=AUDIT_CASES,
            cases=HOLDOUT_CASES):
    dest=location(root,output)
    require(dest.parent.is_dir() and not dest.exists(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    identity,spec=_source(root)
    dataset=structural.paths_for(root)['dataset']
    cohorts,excluded=_make_cohort(root,Path(dataset),audit_cases,cases)
    (dest/'seal').mkdir(parents=True)
    for split in ('audit','holdout'):
        public,secret=cohorts[split]
        write_rows(dest/'seal'/f'{split}_prompts.jsonl',public)
        write_rows(dest/'seal'/f'{split}_answers.SECRET.jsonl',secret)
    inputs={name:sha(dest/'seal'/name) for name in (
        'audit_prompts.jsonl','audit_answers.SECRET.jsonl',
        'holdout_prompts.jsonl','holdout_answers.SECRET.jsonl')}
    commit={'schema':SCHEMA,'status':'FIXED_SETTINGS_SEALED_BEFORE_ANY_INFERENCE',
            'runner_sha256':sha(__file__), 'source':identity, 'decoder_spec':spec,
            'factors':{BASELINE:0.0,STRICT:1.0},'policy':POLICY,
            'audit_seeds':list(AUDIT_SEEDS),'holdout_seeds':list(HOLDOUT_SEEDS),
            'audit_cases':audit_cases,'cases':cases,'inputs':inputs,
            'excluded_prior_prompt_shas':excluded,
            'weight_edits':0,'gradient_steps':0,
            'g3_unlocked':False,'production_promotion':False}
    write(dest/'seal'/'prepared.json',commit)
    print('G2_0103_PREPARED '+json.dumps({
        'source_sha256':identity['head_sha256'],'audit_cases':audit_cases,
        'audit_seeds':list(AUDIT_SEEDS),'sealed_holdout_cases':cases,
        'holdout_seed_streams':len(HOLDOUT_SEEDS),
        'factors':commit['factors'],'status':commit['status']},sort_keys=True),flush=True)
    return commit


def verify(root,dest,*,read_gold=None):
    """Default never stat, hash, or read either SECRET file."""
    c=read(dest/'seal'/'prepared.json')
    require(c.get('schema')==SCHEMA and
            c.get('runner_sha256') in (sha(__file__),LEGACY_RUNNER_SHA256) and
            c.get('status')=='FIXED_SETTINGS_SEALED_BEFORE_ANY_INFERENCE' and
            c.get('factors')=={BASELINE:0.0,STRICT:1.0} and
            c.get('policy')==POLICY and
            c.get('audit_seeds')==list(AUDIT_SEEDS) and
            c.get('holdout_seeds')==list(HOLDOUT_SEEDS) and
            c.get('gradient_steps')==c.get('weight_edits')==0 and
            c.get('g3_unlocked') is False and
            c.get('production_promotion') is False,'PREDECLARATION_DRIFT')
    identity,spec=_source(root)
    require(c['source']==identity and c['decoder_spec']==spec,'SOURCE_DRIFT')
    require(c['audit_cases']%12==0 and c['cases']%20==0,
            'COHORT_SIZE_DRIFT')
    for split in ('audit','holdout'):
        name=f'{split}_prompts.jsonl'
        require(sha(dest/'seal'/name)==c['inputs'][name], 'PROMPT_SHA_DRIFT_'+split)
        if read_gold==split:
            name=f'{split}_answers.SECRET.jsonl'
            require(sha(dest/'seal'/name)==c['inputs'][name], 'ANSWER_SHA_DRIFT_'+split)
    dataset=structural.paths_for(root)['dataset']
    _,excluded=_prior_prompt_hashes(root,Path(dataset))
    require(c['excluded_prior_prompt_shas']==excluded,'PRIOR_PROMPTS_DRIFT')
    return c


def _prompts(dest,split,c):
    data=list(rows(dest/'seal'/f'{split}_prompts.jsonl'))
    count=c['audit_cases'] if split=='audit' else c['cases']
    require(len(data)==count and len({r['prompt_sha256'] for r in data})==count,
            'PROMPT_IDENTITY_DRIFT')
    group_size=count//(len(AUDIT_SEEDS) if split=='audit' else len(HOLDOUT_SEEDS))
    for i,row in enumerate(data):
        group=i//group_size
        require(row['case_id']==f'g2-0103-{split}-{i:05d}' and
                row['stream']==group and
                row['fold']==(FOLDS[group] if split=='audit' else None) and
                row['family']==('allocation' if (i%group_size)%2==0 else 'encounter') and
                row['prompt_sha256']==digest(row['prompt']), 'PROMPT_ROW_DRIFT')
    return data


def _predictions(surface,spec):
    bakeoff.validate_surface(surface)
    return {arm:[source.json_edge(e) for e in sorted(prior.previous.decode_variant(
                surface,spec,offset=offset,capacity=0))]
            for arm,offset in ((BASELINE,0.0),(STRICT,1.0))}


def _infer(row,network,tok,composite,head,spec,device):
    prepared,hidden=bakeoff.previous._capture_inference(
        network,tok,row['prompt'],device,2048)
    g1=composite.predict(hidden,prepared,family=row['family'],native_stage='G1')
    surface=bakeoff._infer(head,hidden,prepared['eligible'].to(device),
                          g1['token_spans'],prepared,spec,with_margins=True)
    return {'case_id':row['case_id'],'family':row['family'],
            'prompt_sha256':row['prompt_sha256'],'fold':row['fold'],
            'stream':row['stream'],'surface':surface,
            'predictions':_predictions(surface,spec)}


def _validate_shard(path,row,c):
    obj=prior.previous._gzip_read(path)
    require(all(obj[k]==row[k] for k in (
        'case_id','family','prompt_sha256','fold','stream')),
        'SHARD_CASE_BINDING_DRIFT')
    require(obj['predictions']==_predictions(obj['surface'],c['decoder_spec']),
            'SHARD_DECODER_REPLAY_DRIFT')
    return obj


def _atomic_write(path, value):
    """Publish JSON progress state atomically so Ctrl+C cannot tear a snapshot."""
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    write(temp,value)
    os.replace(temp,path)


def _telemetry_init():
    return {'cases':0,'families':Counter(),'g1_objects':0,
            'candidate_pairs':0,'baseline_edges':0,'strict_edges':0,
            'baseline_only_edges':0,'strict_only_edges':0}


def _telemetry_add(stats,obj):
    """Read only predictions and G1 metadata; never consult sealed answers."""
    p=source.as_set(obj['predictions'][BASELINE])
    q=source.as_set(obj['predictions'][STRICT])
    stats['cases']+=1
    stats['families'][obj['family']]+=1
    stats['g1_objects']+=len(obj['surface']['g1_objects'])
    stats['candidate_pairs']+=len(obj['surface']['pair_margins'])
    stats['baseline_edges']+=len(p)
    stats['strict_edges']+=len(q)
    stats['baseline_only_edges']+=len(p-q)
    stats['strict_only_edges']+=len(q-p)


def _telemetry_public(stats):
    return {k:(dict(sorted(v.items())) if isinstance(v,Counter) else v)
            for k,v in stats.items()}


def _snapshot_progress(dest,c,split,stats,row,*,checkpoint_every,
                       diagnostic_every,force=False):
    """Durable checkpoints and fast, gold-free diagnostics every small batch."""
    total=c['audit_cases'] if split=='audit' else c['cases']
    n=stats['cases']
    if not n:return
    progress=dest/split/'progress'
    shard=dest/split/'shards'/(row['case_id']+'.json.gz')
    common={'schema':SCHEMA,'split':split,'completed':n,'total':total,
            'prepared_sha256':sha(dest/'seal'/'prepared.json'),
            'source_sha256':c['source']['head_sha256'],
            'last_case_id':row['case_id'],'last_shard_sha256':sha(shard),
            'gold_accessed':False,'weight_edits':0,'production_promotion':False}
    if force or n%checkpoint_every==0 or n==total:
        checkpoint={**common,'status':'INFERENCE_PARTIAL_CHECKPOINT',
                    'remaining':total-n}
        _atomic_write(progress/f'checkpoint-{n:05d}.json',checkpoint)
        _atomic_write(progress/'checkpoint-latest.json',checkpoint)
        print('G2_0103_CHECKPOINT '+json.dumps({
            'split':split,'completed':n,'total':total,'remaining':total-n,
            'last_case_id':row['case_id'],'status':checkpoint['status']},
            sort_keys=True),flush=True)
    if force or n%diagnostic_every==0 or n==total:
        diagnostic={**common,'status':'PARTIAL_UNLABELED_DIAGNOSTIC',
                    'statistics':_telemetry_public(stats),
                    'accuracy_available':False,
                    'accuracy_note':('Gold is sealed during inference. '
                        'Accuracy and exact-graph effects are computed only '
                        'after prediction lock and scoring.')}
        _atomic_write(progress/f'diagnostic-{n:05d}.json',diagnostic)
        _atomic_write(progress/'diagnostic-latest.json',diagnostic)
        metrics=diagnostic['statistics']
        print('G2_0103_DIAGNOSTIC '+json.dumps({
            'split':split,'completed':n,'total':total,
            'baseline_edges':metrics['baseline_edges'],
            'strict_edges':metrics['strict_edges'],
            'suppressed_edges':metrics['baseline_only_edges'],
            'new_edges':metrics['strict_only_edges'],
            'accuracy_available':False},sort_keys=True),flush=True)


def _capture_split(*,root,dest,c,split,device,
                   checkpoint_every=CHECKPOINT_EVERY,
                   diagnostic_every=DIAGNOSTIC_EVERY):
    require(isinstance(checkpoint_every,int) and 1<=checkpoint_every<=1000,
            'CHECKPOINT_INTERVAL_INVALID')
    require(isinstance(diagnostic_every,int) and 1<=diagnostic_every<=1000,
            'DIAGNOSTIC_INTERVAL_INVALID')
    prompts=_prompts(dest,split,c)
    area=dest/split
    shards=area/'shards'
    shards.mkdir(parents=True,exist_ok=True)
    require(not (area/'lock.json').exists(),'ALREADY_LOCKED_'+split)
    existing=[];pending=[]
    stats=_telemetry_init()
    for row in prompts:
        path=shards/(row['case_id']+'.json.gz')
        if path.is_file():
            # Check every previous result before trusting it, including results
            # written by the old 0103 runner. A valid-but-changed result fails.
            obj=_validate_shard(path,row,c)
            existing.append(row)
            _telemetry_add(stats,obj)
        else:
            pending.append(row)
    if existing:
        print('G2_0103_RESUME '+json.dumps({
            'split':split,'previously_saved':len(existing),
            'remaining':len(pending),'gold_accessed':False},sort_keys=True),
            flush=True)
        _snapshot_progress(dest,c,split,stats,existing[-1],
                           checkpoint_every=checkpoint_every,
                           diagnostic_every=diagnostic_every,force=True)
    if pending:
        paths=structural.paths_for(root)
        network,tok,composite,reference,_=structural._load_runtime(paths,device)
        head=source._load_scientific_head(c['source']['checkpoint'],reference,device=device)
        head.eval().requires_grad_(False)
        with torch.inference_mode():
            for row in pending:
                obj=_infer(row,network,tok,composite,head,c['decoder_spec'],device)
                path=shards/(row['case_id']+'.json.gz')
                # 0100's writer uses a temporary gzip and os.replace, so a
                # crash leaves either a complete shard or no shard at all.
                prior.previous._gzip_write(path,obj)
                _telemetry_add(stats,obj)
                n=stats['cases']
                _snapshot_progress(dest,c,split,stats,row,
                                   checkpoint_every=checkpoint_every,
                                   diagnostic_every=diagnostic_every)
    hashes={}
    for row in prompts:
        path=shards/(row['case_id']+'.json.gz')
        _validate_shard(path,row,c)
        hashes[path.name]=sha(path)
    lock={'schema':SCHEMA,'status':f'{split.upper()}_PREDICTIONS_LOCKED',
          'prepared_sha256':sha(dest/'seal'/'prepared.json'),
          'source_sha256':c['source']['head_sha256'],
          'shards':hashes,'gold_accessed':False,
          'weight_edits':0,'production_promotion':False,'g3_unlocked':False}
    if split=='holdout':
        require((dest/'audit'/'audit_report.json').is_file(),'AUDIT_NOT_FINALIZED')
        lock['audit_report_sha256']=sha(dest/'audit'/'audit_report.json')
    _atomic_write(area/'lock.json',lock)
    print('G2_0103_'+split.upper()+'_LOCKED '+json.dumps({
        'cases':len(prompts),'gold_accessed':False,
        'status':lock['status']},sort_keys=True),flush=True)
    return lock


def capture(*,root=ROOT_DEFAULT,output=None,device='cuda',
            checkpoint_every=CHECKPOINT_EVERY,diagnostic_every=DIAGNOSTIC_EVERY):
    dest=location(root,output)
    c=verify(root,dest)
    return _capture_split(root=root,dest=dest,c=c,split='audit',device=device,
                          checkpoint_every=checkpoint_every,
                          diagnostic_every=diagnostic_every)


def _load_locked(dest,c,split):
    lock=read(dest/split/'lock.json')
    require(lock.get('schema')==SCHEMA and
            lock.get('status')==f'{split.upper()}_PREDICTIONS_LOCKED' and
            lock.get('prepared_sha256')==sha(dest/'seal'/'prepared.json') and
            lock.get('source_sha256')==c['source']['head_sha256'] and
            lock.get('gold_accessed') is False and lock.get('weight_edits')==0 and
            lock.get('production_promotion') is False and
            lock.get('g3_unlocked') is False, 'PREDICTION_LOCK_DRIFT')
    if split=='holdout':
        require(lock.get('audit_report_sha256')==sha(dest/'audit'/'audit_report.json'),
                'AUDIT_REPORT_DRIFT')
    prompts=_prompts(dest,split,c)
    require(set(lock['shards'])=={row['case_id']+'.json.gz' for row in prompts},
            'SHARD_COUNT_DRIFT')
    out=[]
    for row in prompts:
        path=dest/split/'shards'/(row['case_id']+'.json.gz')
        require(lock['shards'][path.name]==sha(path),'SHARD_SHA_DRIFT')
        out.append(_validate_shard(path,row,c))
    return out


def _attach_gold(dest,split,records):
    answers={r['case_id']:r for r in rows(dest/'seal'/f'{split}_answers.SECRET.jsonl')}
    require(len(answers)==len(records),'ANSWERS_COUNT_DRIFT')
    for rec in records:
        a=answers.get(rec['case_id'])
        require(a is not None and all(a[k]==rec[k] for k in (
            'case_id','family','prompt_sha256','fold','stream')),
            'GOLD_CASE_BINDING_DRIFT')
        rec['gold']=bakeoff.truth(a['graph'])
        rec['sets']={arm:source.as_set(v) for arm,v in rec['predictions'].items()}
    return records


def _count(pred,gold):
    return Counter(cases=1,exact=int(pred==gold),tp=len(pred&gold),
                   fp=len(pred-gold),fn=len(gold-pred))


def _metrics(records):
    before=Counter();after=Counter();paired=Counter()
    by_label=defaultdict(Counter)
    for r in records:
        gold=r['gold'];p=r['sets'][BASELINE];q=r['sets'][STRICT]
        before.update(_count(p,gold));after.update(_count(q,gold))
        paired.update(exact_gained=int(q==gold and p!=gold),
            exact_lost=int(p==gold and q!=gold),
            removed_fp=len((p-gold)-(q-gold)),
            introduced_fp=len((q-gold)-(p-gold)),
            lost_tp=len((p&gold)-(q&gold)),
            rescued_tp=len((q&gold)-(p&gold)))
        for label,edges in (
            ('removed_false_positive',(p-gold)-(q-gold)),
            ('introduced_false_positive',(q-gold)-(p-gold)),
            ('lost_correct',(p&gold)-(q&gold)),
            ('rescued_correct',(q&gold)-(p&gold))):
            for edge in edges:by_label[edge[4]][label]+=1
    b=prior.previous._score(before);a=prior.previous._score(after)
    return {'baseline':b,'strict':a,'delta':{'f1':a['edge_f1']-b['edge_f1'],
            'exact_net':a['exact']-b['exact'],'fp':a['fp']-b['fp'],
            'fn':a['fn']-b['fn'],'tp':a['tp']-b['tp']},
            'paired':dict(paired),'changes_by_relation_label':dict(by_label)}


def _strata(records,selector):
    groups=defaultdict(list)
    for rec in records:groups[str(selector(rec))].append(rec)
    return {k:_metrics(v) for k,v in sorted(groups.items())}


def _features(rec,spec):
    surf=rec['surface'];objs=surf['g1_objects'];pairs=surf['pair_margins']
    count=0
    for pair in pairs:
        i,j,vals=pair
        if not vals:continue
        k=max(range(len(vals)), key=lambda x:(vals[x],-x))
        label=BIND_CLASSES[k+1]
        key='|'.join((objs[i][2],objs[j][2],label))
        threshold=spec.get('kind_thresholds',{}).get(key,spec.get('threshold',0.0))
        if float(threshold)<vals[k]<=float(threshold)+1.0:
            count+=1
    return {'g1_objects':len(objs),'candidate_pairs':len(pairs),
            'near_admission_boundary_pairs':count,'gold_edges':len(rec['gold']),
            'baseline_edges':len(rec['sets'][BASELINE])}


def _distribution(records,spec):
    if not records:return {}
    features=[_features(r,spec) for r in records]
    result={'cases':len(records)}
    for field in features[0]:
        arr=sorted(x[field] for x in features)
        result[field]={'mean':sum(arr)/len(arr),'median':statistics.median(arr),
                       'p90':arr[min(len(arr)-1,int(.90*(len(arr)-1)))],
                       'max':arr[-1]}
    return result


def _wilson_upper(successes,n,confidence_z=1.959963984540054):
    if not n:return 1.0
    p=successes/n
    z2=confidence_z**2
    return ((p+z2/(2*n)) + confidence_z*math.sqrt(
        p*(1-p)/n+z2/(4*n*n)))/(1+z2/n)


def _f1(counts):
    tp,fp,fn=counts
    return 2*tp/max(1,2*tp+fp+fn)


def _pack(records):
    result=[]
    for r in records:
        gold=r['gold'];p=r['sets'][BASELINE];q=r['sets'][STRICT]
        before=(len(p&gold),len(p-gold),len(gold-p))
        after=(len(q&gold),len(q-gold),len(gold-q))
        result.append((before,after))
    return result


def _gain(sample):
    a=[0,0,0];b=[0,0,0]
    for x,y in sample:
        for k in range(3):a[k]+=x[k];b[k]+=y[k]
    return _f1(b)-_f1(a)


def _ci(values):
    values=sorted(values)
    return [values[int(.025*(len(values)-1))],
            values[int(.975*(len(values)-1))]]


def _bootstrap(records,*,seed,iterations=BOOTSTRAPS,block=False):
    """Paired case bootstrap stratified by family/stream; or stream block bootstrap."""
    if not records:return {'ci95':[0,0],'samples':[]}
    rng=random.Random(seed)
    by_stream=defaultdict(list)
    for r in records:
        by_stream[r['stream']].append(r)
    if block:
        chunks=[_pack(g) for _,g in sorted(by_stream.items())]
        draws=[_gain([x for _ in range(len(chunks))
                      for x in chunks[rng.randrange(len(chunks))]])
               for _ in range(iterations)]
    else:
        strata=defaultdict(list)
        for r in records:strata[(r['stream'],r['family'])].append(r)
        packed=[_pack(v) for _,v in sorted(strata.items())]
        draws=[]
        for _ in range(iterations):
            first=[0,0,0];second=[0,0,0]
            for pool in packed:
                for _ in range(len(pool)):
                    x,y=pool[rng.randrange(len(pool))]
                    for k in range(3):first[k]+=x[k];second[k]+=y[k]
            draws.append(_f1(second)-_f1(first))
    return {'ci95':_ci(draws),'samples':draws}


def _audit_fold(rec):return rec['fold']


def _case_graph_bin(rec):
    n=len(rec['gold'])
    return '00-10' if n<=10 else '11-20' if n<=20 else '21+'


def _g1_bin(rec):
    n=len(rec['surface']['g1_objects'])
    return '00-12' if n<=12 else '13-24' if n<=24 else '25+'


def _near_bin(rec,spec):
    n=_features(rec,spec)['near_admission_boundary_pairs']
    return 'none' if n==0 else '01-03' if n<=3 else '04+'


def _diagnose(records,spec,*,bootstrap_seed,full=False):
    overall=_metrics(records)
    by_family=_strata(records,lambda r:r['family'])
    by_stream=_strata(records,lambda r:r['stream'])
    by_graph=_strata(records,_case_graph_bin)
    by_g1=_strata(records,_g1_bin)
    by_margin=_strata(records,lambda r:_near_bin(r,spec))
    ci=_bootstrap(records,seed=bootstrap_seed)
    metrics={'overall':overall,'families':by_family,'seed_streams':by_stream,
             'graph_size_buckets':by_graph,'g1_object_buckets':by_g1,
             'margin_boundary_buckets':by_margin,
             'distribution':_distribution(records,spec),
             'paired_case_bootstrap_ci95':ci['ci95'],
             'exact_loss_upper95_case_rate':_wilson_upper(
                 overall['paired'].get('exact_lost',0),len(records)),
             'exact_loss_upper95_conditional_on_baseline_exact':_wilson_upper(
                 overall['paired'].get('exact_lost',0),
                 overall['baseline']['exact'])}
    if full:
        block=_bootstrap(records,seed=bootstrap_seed+1,block=True)
        metrics['seed_block_bootstrap_ci95']=block['ci95']
    return metrics


def audit(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output)
    c=verify(root,dest,read_gold='audit')
    outfile=dest/'audit'/'audit_report.json'
    require(not outfile.exists(),'AUDIT_ALREADY_SCORED')
    records=_attach_gold(dest,'audit',_load_locked(dest,c,'audit'))
    # Now audit gold is legitimately open; publish useful causal diagnostics
    # fold-by-fold before expensive pooled bootstrapping begins.
    for fold in FOLDS:
        batch=[r for r in records if r['fold']==fold]
        evidence={'schema':SCHEMA,'fold':fold,'cases':len(batch),
                  'audit_lock_sha256':sha(dest/'audit'/'lock.json'),
                  'metrics':_metrics(batch),'status':'AUDIT_FOLD_SCORED'}
        _atomic_write(dest/'audit'/'folds'/f'{fold}.json',evidence)
        d=evidence['metrics']['delta']
        print('G2_0103_AUDIT_FOLD '+json.dumps({
            'fold':fold,'cases':len(batch),'f1_delta':d['f1'],
            'exact_net':d['exact_net'],'fp_delta':d['fp'],
            'fn_delta':d['fn']},sort_keys=True),flush=True)
    details=_diagnose(records,c['decoder_spec'],bootstrap_seed=BOOT_SEED)
    folds=_strata(records,_audit_fold)
    # 0102's legacy veto covers replication/protection, not discovery.
    losses=[n for n,row in folds.items() if n in FOLDS[2:] and
            (row['delta']['f1']<0 or row['delta']['exact_net']<0)]
    ci=details['paired_case_bootstrap_ci95']
    result={'schema':SCHEMA,'status':'TRAIN_AUDIT_COMPLETE_FIXED_1_UNCHANGED',
            'source_sha256':c['source']['head_sha256'],'split':'audit',
            'fixed_offset':1.0,'summary':details,'folds':folds,
            'legacy_unanimous_fold_veto':bool(losses),'vetoed_folds':losses,
            'audit_f1_signal_ci95':ci,'weight_edits':0,
            'production_promotion':False}
    write(outfile,result)
    print('G2_0103_TRAIN_AUDIT '+json.dumps({
        'f1':details['overall']['delta']['f1'],'ci95':ci,
        'net_exact':details['overall']['delta']['exact_net'],
        'legacy_veto':bool(losses),'vetoed_folds':losses,
        'status':result['status']},sort_keys=True),flush=True)
    return result


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda',
            checkpoint_every=CHECKPOINT_EVERY,diagnostic_every=DIAGNOSTIC_EVERY):
    dest=location(root,output)
    c=verify(root,dest)
    require((dest/'audit'/'audit_report.json').is_file(),'AUDIT_NOT_COMPLETE')
    return _capture_split(root=root,dest=dest,c=c,split='holdout',device=device,
                          checkpoint_every=checkpoint_every,
                          diagnostic_every=diagnostic_every)


def _historical(root,identity):
    """Read already-observed campaigns AFTER current blind lock, never for selection."""
    entries={}
    campaigns=((prior.previous.NAME,'admission_only'),
               (prior.last.NAME,'global_0100'))
    for campaign,arm in campaigns:
        path=Path(root)/campaign/'scored'/'report.json'
        if not path.is_file():
            entries[campaign]={'available':False}
            continue
        obj=read(path)
        require(obj['source']['head_sha256']==identity['head_sha256'],
                'PRIOR_CAMPAIGN_CHECKPOINT_DRIFT')
        entries[campaign]={'available':True,'sha256':sha(path),
                           'fixed_1_effect':obj['effects']['overall'][arm]}
    base=Path(root)/prior.NAME
    report=base/'scored'/'report.json'
    training=base/'train_evidence.json'
    if not report.is_file() or not training.is_file():
        entries[prior.NAME]={'available':False}
    else:
        past=read(report);train=read(training)
        require(past['source']['head_sha256']==identity['head_sha256'],
                'PRIOR_0102_CHECKPOINT_DRIFT')
        old=train['audits'][prior.key(1.)]
        entries[prior.NAME]={'available':True,'scored_sha256':sha(report),
            'train_evidence_sha256':sha(training),
            'train_audit_effect':old['pooled_audit']['effects']['overall'],
            'train_audit_ci95':old['pooled_audit']['f1_paired_bootstrap_95'],
            'holdout_effect':past['predeclared_fixed_1']['effects']['overall'],
            'holdout_ci95':past['predeclared_fixed_1']['f1_paired_bootstrap_95']}
    return entries


def _distribution_shift(audit_records,holdout_records,spec):
    """Standardized differences are descriptive, not distribution-equality tests."""
    a=[_features(r,spec) for r in audit_records]
    b=[_features(r,spec) for r in holdout_records]
    result={}
    for column in a[0]:
        x=[v[column] for v in a];y=[v[column] for v in b]
        mx,my=statistics.mean(x),statistics.mean(y)
        va=statistics.pvariance(x);vb=statistics.pvariance(y)
        denom=math.sqrt((va+vb)/2)
        smd=(my-mx)/denom if denom else 0.0
        result[column]={'audit_mean':mx,'holdout_mean':my,
                        'mean_difference':my-mx,
                        'standardized_mean_difference':smd,
                        'descriptive_shift_flag_abs_smd_gt_0_2':abs(smd)>.2}
    return {'measured_features':result,
            'any_descriptive_shift_flag':any(
                x['descriptive_shift_flag_abs_smd_gt_0_2'] for x in result.values()),
            'caution':'Standardized differences detect descriptive imbalance; '
               'they do not prove that a distribution shift caused an accuracy gap.'}


def _case_ledger(records,spec):
    """One factual paired-error record per example, no raw prompts or gold graph."""
    out=[]
    for r in records:
        gold=r['gold'];p=r['sets'][BASELINE];q=r['sets'][STRICT]
        obj={'case_id':r['case_id'],'prompt_sha256':r['prompt_sha256'],
             'family':r['family'],'seed_stream':r['stream'],
             'gold_edges':len(gold),'g1_objects':len(r['surface']['g1_objects']),
             'candidate_pairs':len(r['surface']['pair_margins']),
             'near_boundary_candidate_pairs':_features(r,spec)[
                 'near_admission_boundary_pairs'],
             'baseline_exact':p==gold,'strict_exact':q==gold,
             'baseline':dict(_count(p,gold)),'strict':dict(_count(q,gold)),
             'removed_false_positives':len((p-gold)-(q-gold)),
             'lost_correct_edges':len((p&gold)-(q&gold)),
             'introduced_false_positives':len((q-gold)-(p-gold)),
             'recovered_correct_edges':len((q&gold)-(p&gold))}
        out.append(obj)
    return out


def _classify(audit_details,holdout):
    audit_total=audit_details['summary']['overall'];overall=holdout['overall']
    f1=overall['delta']['f1'];ci=holdout['paired_case_bootstrap_ci95']
    family=holdout['families'];seeds=holdout['seed_streams']
    positive=sum(x['delta']['f1']>0 for x in seeds.values())
    severe_seeds=sum(x['delta']['f1'] < -0.005 for x in seeds.values())
    family_bad=any(x['delta']['f1']<POLICY['maximum_family_f1_regression']
                   for x in family.values())
    risk_case=holdout['exact_loss_upper95_case_rate']
    risk_cond=holdout['exact_loss_upper95_conditional_on_baseline_exact']
    stable=(f1>=POLICY['minimum_holdout_f1_gain'] and ci[0]>0 and
            holdout['seed_block_bootstrap_ci95'][0]>0 and
            positive>=POLICY['minimum_positive_seed_groups'] and
            not family_bad and overall['delta']['exact_net']>=0 and
            risk_case<=POLICY['maximum_exacts_lost_case_rate_upper95'] and
            risk_cond<=POLICY['maximum_conditional_exact_loss_upper95'])
    unstable=(ci[1]<=0 or family_bad and severe_seeds>=3 or
              overall['delta']['exact_net']<0 and severe_seeds>=3)
    if stable:
        status=('STABLE_EVIDENCE_OLD_GATE_POTENTIALLY_OVERRIGID'
                if audit_details['legacy_unanimous_fold_veto']
                else 'STABLE_EVIDENCE_CONSISTENT_WITH_GATE')
    elif unstable:
        status='REPLICATED_INSTABILITY_OR_MATERIAL_REGRESSION'
    else:
        status='EVIDENCE_INCONCLUSIVE'
    # Conservative old gate vetoes a single negative audit fold / exact loss.
    legacy=(audit_details['legacy_unanimous_fold_veto'] or
            overall['paired'].get('exact_lost',0)>0)
    return {'status':status,'diagnostic_support':bool(stable),
            'legacy_gate_would_veto':bool(legacy),
            'positive_seed_streams':positive,
            'severe_negative_seed_streams':severe_seeds,
            'family_regression':bool(family_bad),
            'exact_loss_conditional_upper95':risk_cond,
            'exact_loss_case_upper95':risk_case,
            'no_automatic_promotion':True}


def score(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output)
    c=verify(root,dest,read_gold='holdout')
    require(not (dest/'scored'/'report.json').exists(),'ALREADY_SCORED')
    audit_path=dest/'audit'/'audit_report.json'
    audit_result=read(audit_path)
    require(audit_result.get('schema')==SCHEMA and
            audit_result.get('source_sha256')==c['source']['head_sha256'] and
            audit_result.get('fixed_offset')==1.0,'AUDIT_CONTRACT_DRIFT')
    records=_attach_gold(dest,'holdout',_load_locked(dest,c,'holdout'))
    # Prediction lock exists before any gold-informed holdout diagnostics.
    for stream in range(len(HOLDOUT_SEEDS)):
        batch=[r for r in records if r['stream']==stream]
        evidence={'schema':SCHEMA,'stream':stream,'cases':len(batch),
                  'holdout_lock_sha256':sha(dest/'holdout'/'lock.json'),
                  'metrics':_metrics(batch),'status':'HOLDOUT_STREAM_SCORED'}
        _atomic_write(dest/'scored'/'streams'/f'stream-{stream:02d}.json',evidence)
        d=evidence['metrics']['delta']
        print('G2_0103_HOLDOUT_STREAM '+json.dumps({
            'stream':stream,'cases':len(batch),'f1_delta':d['f1'],
            'exact_net':d['exact_net'],'fp_delta':d['fp'],
            'fn_delta':d['fn']},sort_keys=True),flush=True)
    details=_diagnose(records,c['decoder_spec'],bootstrap_seed=BOOT_SEED+100,
                      full=True)
    classification=_classify(audit_result,details)
    # Compare independent audit vs holdout changes (not a threshold retune).
    old_records=_attach_gold(dest,'audit',_load_locked(dest,c,'audit'))
    train_draws=_bootstrap(old_records,seed=BOOT_SEED+101)['samples']
    holdout_draws=_bootstrap(records,seed=BOOT_SEED+102)['samples']
    gap_ci=_ci([b-a for a,b in zip(train_draws,holdout_draws)])
    gap=details['overall']['delta']['f1']-audit_result['summary']['overall']['delta']['f1']
    comparison={
        'distribution_shift_diagnostics':_distribution_shift(
            old_records,records,c['decoder_spec']),
        'holdout_minus_audit_f1':gap,
        'holdout_minus_audit_ci95_approx':gap_ci,
        'sampling_variation_compatible':gap_ci[0]<=0<=gap_ci[1],
        'audit_distribution':audit_result['summary']['distribution'],
        'holdout_distribution':details['distribution'],
        'limitations':('Audit uses generator train mode and holdout uses generator test '
                        'mode, with disjoint prompt sets. Synthetic generator shift '
                        'can contribute to the gap; bootstrap does not remove it.')}
    original_ok=source._candidate_info(c['source']['checkpoint'])==c['source']
    require(original_ok,'ORIGINAL_CHECKPOINT_MUTATED')
    report={'schema':SCHEMA,'source':c['source'],'cases':c['cases'],
            'audit_cases':c['audit_cases'],'fixed_offset':1.0,
            'status':classification['status'],
            'predeclared_policy':POLICY,
            'audit':audit_result,'holdout':details,
            'training_vs_holdout':comparison,
            'historical_0102_not_used_for_selection':_historical(root,c['source']),
            'classification':classification,'source_unchanged':True,
            'weight_edits':0,'gradient_steps':0,
            'production_promotion':False,'g3_unlocked':False,
            'limitations':('No independent real-world distribution; ten seeds are ten '
                'synthetic random streams. No threshold search on holdout. '
                'Bootstrap intervals are approximate and do not prove safety.')}
    (dest/'scored').mkdir(exist_ok=True)
    # Only after prediction lock and gold verification; case IDs and counts
    # retain exact causal attribution without exporting raw prompt/gold content.
    ledger=dest/'scored'/'case_ledger.jsonl'
    write_rows(ledger,_case_ledger(records,c['decoder_spec']))
    report['case_ledger']={'path':'scored/case_ledger.jsonl',
                           'sha256':sha(ledger),'cases':len(records)}
    write(dest/'scored'/'report.json',report)
    summary={'f1':details['overall']['delta']['f1'],
             'ci95':details['paired_case_bootstrap_ci95'],
             'seed_block_ci95':details['seed_block_bootstrap_ci95'],
             'fp':details['overall']['delta']['fp'],
             'fn':details['overall']['delta']['fn'],
             'exact_net':details['overall']['delta']['exact_net'],
             'exact_lost':details['overall']['paired'].get('exact_lost',0),
             'positive_seed_streams':classification['positive_seed_streams'],
             'audit_f1':audit_result['summary']['overall']['delta']['f1'],
             'gap_ci95':gap_ci,
             'legacy_veto':classification['legacy_gate_would_veto'],
             'diagnostic_support':classification['diagnostic_support'],
             'status':report['status'],'source_unchanged':True}
    print('G2_0103_SCORED '+json.dumps(summary,sort_keys=True),flush=True)
    return report


def run(*,root=ROOT_DEFAULT,output=None,audit_cases=AUDIT_CASES,
        cases=HOLDOUT_CASES,device='cuda',
        checkpoint_every=CHECKPOINT_EVERY,diagnostic_every=DIAGNOSTIC_EVERY):
    dest=location(root,output)
    if not (dest/'seal'/'prepared.json').exists():
        prepare(root=root,output=output,audit_cases=audit_cases,cases=cases)
    if not (dest/'audit'/'lock.json').exists():
        capture(root=root,output=output,device=device,
                checkpoint_every=checkpoint_every,diagnostic_every=diagnostic_every)
    if not (dest/'audit'/'audit_report.json').exists():
        audit(root=root,output=output)
    if not (dest/'holdout'/'lock.json').exists():
        predict(root=root,output=output,device=device,
                checkpoint_every=checkpoint_every,diagnostic_every=diagnostic_every)
    if not (dest/'scored'/'report.json').exists():
        return score(root=root,output=output)
    c=verify(root,dest)
    require(source._candidate_info(c['source']['checkpoint'])==c['source'],
            'ORIGINAL_CHECKPOINT_MUTATED')
    report=read(dest/'scored'/'report.json')
    require(report.get('schema')==SCHEMA and report.get('source')==c['source']
            and report.get('fixed_offset')==1.0 and
            report.get('cases')==c['cases'] and
            report.get('audit_cases')==c['audit_cases'] and
            report.get('weight_edits')==report.get('gradient_steps')==0 and
            report.get('source_unchanged') is True and
            report.get('production_promotion') is False and
            report.get('g3_unlocked') is False and
            report.get('case_ledger',{}).get('sha256')==sha(
                dest/'scored'/'case_ledger.jsonl'),
            'FINISHED_REPORT_DRIFT')
    return report


def status(*,root=ROOT_DEFAULT,output=None):
    """Fast, read-only look at durable progress. Does not load CUDA or gold."""
    dest=location(root,output)
    prep=dest/'seal'/'prepared.json'
    if not prep.is_file():
        result={'status':'NOT_PREPARED','path':str(dest)}
        print('G2_0103_STATUS '+json.dumps(result,sort_keys=True),flush=True)
        return result
    c=read(prep)
    require(c.get('schema')==SCHEMA and
            c.get('runner_sha256') in (sha(__file__),LEGACY_RUNNER_SHA256),
            'STATUS_PREPARED_CONTRACT_DRIFT')
    outcome={'status':'IN_PROGRESS','path':str(dest),'splits':{}}
    for split,total in (('audit',c['audit_cases']),('holdout',c['cases'])):
        area=dest/split
        checkpoint=area/'progress'/'checkpoint-latest.json'
        diagnostic=area/'progress'/'diagnostic-latest.json'
        on_disk=sum((area/'shards'/(f'g2-0103-{split}-{i:05d}.json.gz')).is_file()
                    for i in range(total))
        row={'saved_shards':on_disk,'total':total,'remaining':total-on_disk,
             'locked':(area/'lock.json').is_file(),
             'last_checkpoint':read(checkpoint) if checkpoint.is_file() else None,
             'last_diagnostic':read(diagnostic) if diagnostic.is_file() else None}
        outcome['splits'][split]=row
    outcome['audit_scored']=(dest/'audit'/'audit_report.json').is_file()
    outcome['holdout_scored']=(dest/'scored'/'report.json').is_file()
    if outcome['holdout_scored']:outcome['status']='COMPLETE'
    print('G2_0103_STATUS '+json.dumps(outcome,sort_keys=True),flush=True)
    return outcome


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('run','prepare','capture','audit','predict','score','status'):
        p=sub.add_parser(command)
        p.add_argument('--root',default=ROOT_DEFAULT)
        p.add_argument('--output')
        if command in ('run','prepare'):
            p.add_argument('--audit-cases',type=int,default=AUDIT_CASES)
            p.add_argument('--cases',type=int,default=HOLDOUT_CASES)
        if command in ('run','capture','predict'):
            p.add_argument('--device',default='cuda')
            p.add_argument('--checkpoint-every',type=int,default=CHECKPOINT_EVERY)
            p.add_argument('--diagnostic-every',type=int,default=DIAGNOSTIC_EVERY)
    args=parser.parse_args(argv)
    kw={'root':args.root,'output':args.output}
    if args.command in ('run','prepare'):
        kw.update(audit_cases=args.audit_cases,cases=args.cases)
    if args.command in ('run','capture','predict'):
        kw['device']=args.device
        kw['checkpoint_every']=args.checkpoint_every
        kw['diagnostic_every']=args.diagnostic_every
    globals()[args.command](**kw)

if __name__=='__main__':main()
