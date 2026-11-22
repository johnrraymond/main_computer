"""Gamer G2-0102: frozen G2-0096 global admission stability / safety-gate audit.

Generate a NEW author-annotated TRAIN cohort (six preassigned stratified folds),
collect original G2 neural surfaces once, and audit a PREDECLARED global threshold
grid. Discovery folds alone choose an exploratory candidate; replication and
protection folds are exclusively audits. A disjoint, new 240-case sealed holdout
then evaluates the entire precommitted grid, with gold absent from prediction.

No neural weight update, solver-cap change, production promotion, or G3 unlock.

  python -u -m tools.nanojev_gamer_v1_g2_threshold_stability_0102 run --device cuda
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random

import torch
from tools import nanojev_gamer_v1_g2_relationship_admission_0101 as last
from tools import nanojev_gamer_v1_g2_admission_constraints_0100 as previous

SCHEMA = 'nanojev.gamer.graph.g2.threshold-stability-0102.v1'
NAME = 'oracle-fabric-g2-threshold-stability-0102'
ROOT_DEFAULT = last.ROOT_DEFAULT
LEVELS = (-0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
FAMILIES = ('allocation', 'encounter', 'overall')
FOLD_NAMES = ('discovery_0', 'discovery_1', 'replication_0',
              'replication_1', 'protection_0', 'protection_1')
TRAIN_SEED, HOLDOUT_SEED, BOOT_SEED = 1_000_102, 2_000_102, 3_000_102
TRAIN_CASES, HOLDOUT_CASES = 240, 240
sha, digest, read, write, rows, write_rows = (last.sha, last.digest,
    last.read, last.write, last.rows, last.write_rows)
source, structural, bakeoff = last.source, last.structural, last.bakeoff
corpus, contract = last.corpus, last.contract


def require(condition, name):
    if not condition:
        raise ValueError('G2_0102_' + name)


def key(value):
    value = float(value)
    require(value in LEVELS, 'LEVEL_OUT_OF_GRID')
    return format(value, '.2f')


def location(root, output=None):
    return (Path(output) if output else Path(root) / NAME).resolve()


def _prompt_hashes(root, dataset):
    # Explicit known campaign paths only, never recursively traverse runtime.
    used = {digest(x['prompt']) for x in previous.previous.cur.load_rows(dataset)}
    evidence = {}
    campaigns = (previous.NAME, last.NAME, source.OUTPUT_NAME,
                 previous.previous.earlier.NAME)
    for campaign in campaigns:
        p = Path(root) / campaign / 'seal' / 'prompts.jsonl'
        if p.is_file():
            evidence[campaign] = sha(p)
            used.update(x.get('prompt_sha256') or digest(x['prompt']) for x in rows(p))
    return used, evidence


def _cohort(root, dataset, train_cases, holdout_cases):
    require(48 <= train_cases <= 2400 and train_cases % 12 == 0,
            'TRAIN_CASES_MULTIPLE_OF_12_REQUIRED')
    require(40 <= holdout_cases <= 2000 and holdout_cases % 2 == 0,
            'HOLDOUT_CASES_EVEN_REQUIRED')
    used, evidence = _prompt_hashes(root, dataset)
    result = {}
    for split, count, seed, serial in (
        ('train', train_cases, TRAIN_SEED, 102_000_000),
        ('holdout', holdout_cases, HOLDOUT_SEED, 202_000_000)):
        rng = random.Random(seed)
        public, secret = [], []
        for i in range(count):
            family = 'allocation' if i % 2 == 0 else 'encounter'
            generator = corpus.allocation if family == 'allocation' else corpus.encounter
            for _ in range(4096):
                record = generator(rng, 'train' if split == 'train' else 'test', serial)
                serial += 1
                h = digest(record['prompt'])
                if h not in used:
                    break
            else:
                require(False, 'UNIQUE_PROMPT_GENERATION_EXHAUSTED')
            require(record.get('annotation_provenance') ==
                    'AUTHOR_MARKED_IN_SOURCE_GENERATOR', 'AUTHOR_GOLD_REQUIRED')
            contract.validate_graph(record['graph'], complete=True)
            used.add(h)
            cid = f'g2-0102-{split}-{i:04d}'
            public.append({'case_id': cid, 'family': family,
                           'prompt': record['prompt'], 'prompt_sha256': h,
                           'fold': FOLD_NAMES[(i // 2) % 6] if split == 'train' else None})
            secret.append({'case_id': cid, 'family': family,
                           'prompt_sha256': h, 'graph': record['graph']})
        result[split] = (public, secret)
    return result, evidence


def _source(root):
    prior, _, _ = previous._selected_source(root)
    require(prior['decoder_spec'].get('method') == 'joint', 'JOINT_REQUIRED')
    require(source._candidate_info(prior['source']['checkpoint']) == prior['source'],
            'CHECKPOINT_DRIFT')
    return prior['source'], prior['decoder_spec']


def prepare(*, root=ROOT_DEFAULT, output=None, train_cases=TRAIN_CASES,
            cases=HOLDOUT_CASES):
    dest = location(root, output)
    require(dest.parent.is_dir() and not dest.exists(), 'OUTPUT_EXISTS')
    identity, spec = _source(root)
    paths = structural.paths_for(root)
    cohorts, excluded = _cohort(root, Path(paths['dataset']), train_cases, cases)
    (dest/'seal').mkdir(parents=True)
    for split in ('train', 'holdout'):
        prompts, answers = cohorts[split]
        write_rows(dest/'seal'/f'{split}_prompts.jsonl', prompts)
        write_rows(dest/'seal'/f'{split}_answers.SECRET.jsonl', answers)
    commit = {'schema': SCHEMA, 'status': 'THRESHOLD_GRID_PREDECLARED',
              'runner_sha256': sha(__file__), 'source': identity, 'decoder_spec': spec,
              'levels': list(LEVELS), 'fixed_primary_offset': 1.0,
              'fold_names': list(FOLD_NAMES), 'train_seed': TRAIN_SEED,
              'holdout_seed': HOLDOUT_SEED, 'bootstrap_seed': BOOT_SEED,
              'train_cases': train_cases, 'cases': cases,
              'excluded_prior_prompt_shas': excluded,
              'input_sha256': {name: sha(dest/'seal'/name) for name in (
                  'train_prompts.jsonl', 'train_answers.SECRET.jsonl',
                  'holdout_prompts.jsonl', 'holdout_answers.SECRET.jsonl')},
              'weight_edits': 0, 'gradient_steps': 0,
              'g3_unlocked': False, 'production_promotion': False}
    write(dest/'seal'/'prepared.json', commit)
    print('G2_0102_PREPARED ' + json.dumps({
        'source_sha256':identity['head_sha256'], 'train_cases':train_cases,
        'train_folds':list(FOLD_NAMES), 'sealed_holdout_cases':cases,
        'fixed_primary_offset':1.0, 'levels':LEVELS,
        'status':commit['status']}, sort_keys=True), flush=True)
    return commit


def verify(root, dest, *, phase='capture'):
    c = read(dest/'seal'/'prepared.json')
    require(c.get('schema') == SCHEMA and c.get('status') == 'THRESHOLD_GRID_PREDECLARED'
            and c.get('runner_sha256') == sha(__file__) and
            c.get('levels') == list(LEVELS) and c.get('fixed_primary_offset') == 1.0
            and c.get('fold_names') == list(FOLD_NAMES) and
            c.get('train_seed') == TRAIN_SEED and c.get('holdout_seed') == HOLDOUT_SEED
            and c.get('bootstrap_seed') == BOOT_SEED and
            c.get('gradient_steps') == c.get('weight_edits') == 0 and
            c.get('g3_unlocked') is False and c.get('production_promotion') is False,
            'PREDECLARATION_DRIFT')
    identity, spec = _source(root)
    require(c['source'] == identity and c['decoder_spec'] == spec,
            'SOURCE_DRIFT')
    for name in ('train_prompts.jsonl', 'train_answers.SECRET.jsonl',
                 'holdout_prompts.jsonl', 'holdout_answers.SECRET.jsonl'):
        if name.endswith('answers.SECRET.jsonl') and (
            (phase == 'capture' and name.startswith('train_') is False) or
            (phase == 'predict')):
            # NO reads, hashes, or stat on blind answers during inference.
            continue
        require(sha(dest/'seal'/name) == c['input_sha256'][name],
                'INPUT_SHA_DRIFT_'+name)
    dataset = structural.paths_for(root)['dataset']
    _, excluded = _prompt_hashes(root, Path(dataset))
    require(excluded == c['excluded_prior_prompt_shas'], 'PRIOR_CAMPAIGN_DRIFT')
    return c


def _prompts(dest, split, count):
    data = list(rows(dest/'seal'/f'{split}_prompts.jsonl'))
    require(len(data) == count and len({x['prompt_sha256'] for x in data}) == count,
            'PROMPT_UNIQUENESS_OR_COUNT')
    if split == 'train':
        for i, item in enumerate(data):
            require(item['fold'] == FOLD_NAMES[(i // 2)%6], 'FOLD_BINDING_DRIFT')
    return data


def _predictions(surface, spec):
    return {key(v): [source.json_edge(e) for e in sorted(
                previous.decode_variant(surface, spec, offset=v, capacity=0))]
            for v in LEVELS}


def _infer(row, network, tok, composite, head, spec, device):
    prepared, hidden = bakeoff.previous._capture_inference(
        network, tok, row['prompt'], device, 2048)
    g1 = composite.predict(hidden, prepared,
                           family=row['family'], native_stage='G1')
    surface = bakeoff._infer(head, hidden, prepared['eligible'].to(device),
                            g1['token_spans'], prepared, spec, with_margins=True)
    return {'case_id':row['case_id'],'family':row['family'],
            'prompt_sha256':row['prompt_sha256'], 'fold':row.get('fold'),
            'surface':surface,'predictions':_predictions(surface,spec)}


def _capture_split(*, root, dest, c, split, device):
    count = c['train_cases'] if split == 'train' else c['cases']
    prompts = _prompts(dest, split, count)
    area = dest/split
    shards = area/'shards'; shards.mkdir(parents=True, exist_ok=True)
    lockpath = area/'lock.json'
    require(not lockpath.exists(), 'ALREADY_LOCKED_'+split)
    pending = [(i,r) for i,r in enumerate(prompts)
               if not (shards/(r['case_id']+'.json.gz')).exists()]
    if pending:
        paths = structural.paths_for(root)
        net, tok, comp, reference, _ = structural._load_runtime(paths, device)
        head = source._load_scientific_head(c['source']['checkpoint'],reference,device=device)
        head.eval().requires_grad_(False)
        with torch.inference_mode():
            for step, (i, row) in enumerate(pending, 1):
                require(row['prompt_sha256'] == digest(row['prompt']),
                        'PROMPT_TEXT_DRIFT')
                obj = _infer(row,net,tok,comp,head,c['decoder_spec'],device)
                previous._gzip_write(shards/(row['case_id']+'.json.gz'),obj)
                if step==1 or step%25==0 or step==len(pending):
                    print(f'G2_0102_{split.upper()} {len(prompts)-len(pending)+step}/{len(prompts)}',
                          flush=True)
    hashes = {}
    for row in prompts:
        file = shards/(row['case_id']+'.json.gz')
        obj = previous._gzip_read(file)
        require(all(obj[k] == row[k] for k in ('case_id','family','prompt_sha256','fold'))
                and set(obj['predictions']) == {key(v) for v in LEVELS},
                'SHARD_IDENTITY_DRIFT')
        bakeoff.validate_surface(obj['surface'])
        require(obj['predictions'] == _predictions(obj['surface'],c['decoder_spec']),
                'SHARD_DECODER_REPLAY_DRIFT')
        hashes[file.name] = sha(file)
    lock = {'schema':SCHEMA,'status': f'{split.upper()}_PREDICTIONS_LOCKED',
            'input_precommit_sha256':sha(dest/'seal'/'prepared.json'),
            'source_sha256':c['source']['head_sha256'], 'shard_sha256':hashes,
            'gold_accessed':False,'weight_edits':0,'g3_unlocked':False,
            'production_promotion':False}
    if split=='holdout':
        require((dest/'seal'/'factors.json').is_file(), 'FACTORS_NOT_FROZEN')
        lock['factors_sha256'] = sha(dest/'seal'/'factors.json')
    write(lockpath,lock)
    print('G2_0102_'+split.upper()+'_LOCKED '+json.dumps({
        'cases':len(prompts),'source_sha256':lock['source_sha256'],
        'gold_accessed':False,'status':lock['status']},sort_keys=True),flush=True)
    return lock


def capture(*, root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=location(root,output)
    c=verify(root,dest,phase='capture')
    return _capture_split(root=root,dest=dest,c=c,split='train',device=device)


def _load_locked(root,dest,c,split):
    lock=read(dest/split/'lock.json')
    require(lock['schema']==SCHEMA and
            lock['status']==f'{split.upper()}_PREDICTIONS_LOCKED' and
            lock['input_precommit_sha256']==sha(dest/'seal'/'prepared.json') and
            lock['source_sha256']==c['source']['head_sha256'] and
            lock['gold_accessed'] is False and lock['weight_edits']==0 and
            lock['production_promotion'] is False and lock['g3_unlocked'] is False,
            'PREDICTION_LOCK_DRIFT_'+split)
    if split=='holdout':
        require(lock['factors_sha256']==sha(dest/'seal'/'factors.json'),
                'FROZEN_FACTORS_DRIFT')
    prompts=_prompts(dest,split,c['train_cases'] if split=='train' else c['cases'])
    result=[]
    for row in prompts:
        p=dest/split/'shards'/(row['case_id']+'.json.gz')
        require(lock['shard_sha256'].get(p.name)==sha(p),'SHARD_HASH_DRIFT')
        record=previous._gzip_read(p)
        require(all(record[k] == row[k] for k in ('case_id','family','prompt_sha256','fold'))
                and record['predictions']==_predictions(record['surface'],c['decoder_spec']),
                'SHARD_REPLAY_DRIFT')
        result.append(record)
    return result


def _gold_join(dest,split,records):
    answers = {r['case_id']:r for r in rows(dest/'seal'/f'{split}_answers.SECRET.jsonl')}
    require(len(answers)==len(records),'ANSWERS_COUNT_DRIFT')
    for r in records:
        answer = answers.get(r['case_id'])
        require(answer is not None and answer['prompt_sha256']==r['prompt_sha256'] and
                answer['family']==r['family'],'GOLD_BINDING_DRIFT')
        r['gold']=bakeoff.truth(answer['graph'])
        r['sets']={level:source.as_set(v) for level,v in r['predictions'].items()}
    return records


def _case_counts(pred,gold):
    return Counter(cases=1,exact=int(pred==gold),tp=len(pred & gold),
                   fp=len(pred-gold),fn=len(gold-pred))


def _aggregate(records,offset,family=None):
    ct=Counter()
    for r in records:
        if family and family!='overall' and r['family'] != family:
            continue
        ct.update(_case_counts(r['sets'][key(offset)],r['gold']))
    return previous._score(ct)


def _delta(a,b):
    return {'f1':a['edge_f1']-b['edge_f1'],
            'exact_net':a['exact']-b['exact'],
            'fp':a['fp']-b['fp'], 'fn':a['fn']-b['fn'],
            'tp':a['tp']-b['tp']}


def _paired(records,offset):
    paired=Counter()
    for r in records:
        before=r['sets'][key(0.)];after=r['sets'][key(offset)];gold=r['gold']
        paired.update(exact_gained=int(after==gold and before!=gold),
            exact_lost=int(before==gold and after!=gold),
            lost_tp=len((before & gold)-(after & gold)),
            rescued_tp=len((after & gold)-(before & gold)),
            removed_fp=len((before-gold)-(after-gold)),
            introduced_fp=len((after-gold)-(before-gold)))
    return dict(paired)


def _bootstrap(records,offset,seed,iterations=800):
    """Family-stratified paired bootstrap; approximate, not an exact confidence proof."""
    if not records or offset==0:
        return [0.0,0.0]
    rng=random.Random(seed)
    cohorts=[[r for r in records if r['family']==fam]
             for fam in ('allocation','encounter')]
    values=[]
    for _ in range(iterations):
        sample=[pool[rng.randrange(len(pool))] for pool in cohorts if pool
                for _ in range(len(pool))]
        values.append(_delta(_aggregate(sample,offset),_aggregate(sample,0))['f1'])
    values.sort()
    return [values[int((iterations-1)*.025)],values[int((iterations-1)*.975)]]


def _evaluate(records,offset,*, with_folds=False,seed=BOOT_SEED):
    baseline={f:_aggregate(records,0,f) for f in FAMILIES}
    selected={f:_aggregate(records,offset,f) for f in FAMILIES}
    effect={f:_delta(selected[f],baseline[f]) for f in FAMILIES}
    fold_results={}
    if with_folds:
        for fold in FOLD_NAMES:
            subset=[r for r in records if r['fold']==fold]
            fold_results[fold]={fam:_delta(_aggregate(subset,offset,fam),
                                         _aggregate(subset,0,fam)) for fam in FAMILIES}
    return {'baseline':baseline,'edited':selected,'effects':effect,
            'paired':_paired(records,offset),
            'f1_paired_bootstrap_95':_bootstrap(records,offset,seed),
            'folds':fold_results}


def _stability(records, offset):
    audit=[r for r in records if r['fold'].startswith(('replication','protection'))]
    details=_evaluate(audit,offset,with_folds=True)
    fold_overall=[details['folds'][f]['overall'] for f in FOLD_NAMES[2:]]
    positive=sum(d['f1']>0 for d in fold_overall)
    old_veto=any(d['f1'] < 0 or d['exact_net'] < 0 for d in fold_overall)
    pooled=details['effects']['overall']
    ci=details['f1_paired_bootstrap_95']
    family_ok=all(details['effects'][f]['f1']>=-0.005
                  for f in ('allocation','encounter'))
    strong=(pooled['f1']>=0.002 and pooled['exact_net']>=0 and
            positive>=3 and ci[0]>0 and family_ok)
    unstable=(pooled['f1']<=0 or positive<=1 or
              any(details['effects'][f]['f1'] < -0.01
                  for f in ('allocation','encounter')))
    if strong and old_veto:
        state='CONSERVATIVE_FOLD_VETO_WITH_POOLED_SUPPORT'
    elif strong:
        state='CONSISTENT_POOLED_SUPPORT'
    elif unstable:
        state='TRAIN_SIGNAL_UNSTABLE'
    else:
        state='TRAIN_EVIDENCE_INCONCLUSIVE'
    return {'offset':offset, 'status':state,'unanimous_fold_rule_would_veto':old_veto,
            'positive_audit_folds':positive,'audit_folds':4,
            'pooled_audit':details,'pooled_support_rule_passes':strong,
            'interpretation':('A single negative small fold can be conservative; '
                 'pooled support is not proof of zero exact-graph harm.')}


def calibrate(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output); c=verify(root,dest,phase='capture')
    require(not (dest/'seal'/'factors.json').exists(), 'FACTORS_ALREADY_FROZEN')
    rec=_gold_join(dest,'train',_load_locked(root,dest,c,'train'))
    discovery=[r for r in rec if r['fold'].startswith('discovery')]
    trials={}
    for level in LEVELS:
        trials[key(level)]=_evaluate(discovery,level,seed=BOOT_SEED+1)
    # Discovery-only selection. Audit observations cannot retune this level.
    options=[v for v in LEVELS if v>0 and
             trials[key(v)]['effects']['overall']['f1']>0.001 and
             trials[key(v)]['effects']['overall']['exact_net']>=0]
    chosen=max(options,key=lambda v:(trials[key(v)]['effects']['overall']['f1'],
                                  trials[key(v)]['effects']['overall']['exact_net'],
                                  -abs(v))) if options else 0.0
    stability={key(v):_stability(rec,v) for v in (1.0,chosen)}
    all_folds={key(v):_evaluate(rec,v,with_folds=True,seed=BOOT_SEED+10)
               for v in LEVELS}
    evidence={'schema':SCHEMA, 'train_cases':len(rec),
              'selection':'DISCOVERY_ONLY, AUDITS NEVER RETUNE',
              'discovery_trials':trials,'selected_offset':chosen,
              'predeclared_primary_offset':1.0,
              'audits':stability, 'all_training_folds':all_folds,
              'limitations':('Six folds are independent subsets of one NEW synthetic '
                   'training cohort, not six independently sourced distributions. '
                   'Bootstrap intervals are approximate. Exact graph losses '
                   'are explicitly retained even when pooled F1 improves.')}
    write(dest/'train_evidence.json',evidence)
    frozen={'schema':SCHEMA,'status':'SETTINGS_FROZEN_BEFORE_HOLDOUT',
            'precommit_sha256':sha(dest/'seal'/'prepared.json'),
            'train_lock_sha256':sha(dest/'train'/'lock.json'),
            'train_evidence_sha256':sha(dest/'train_evidence.json'),
            'discovery_selected_offset':chosen,'predeclared_primary_offset':1.0,
            'levels':list(LEVELS),'source_sha256':c['source']['head_sha256'],
            'weight_edits':0,'production_promotion':False,'g3_unlocked':False}
    write(dest/'seal'/'factors.json',frozen)
    print('G2_0102_TRAIN_AUDIT '+json.dumps({
        'fixed_1':stability[key(1.0)]['status'],
        'fixed_1_audit_f1':stability[key(1.0)]['pooled_audit']['effects']['overall']['f1'],
        'fixed_1_audit_95ci':stability[key(1.0)]['pooled_audit']['f1_paired_bootstrap_95'],
        'fixed_1_audit_exact':stability[key(1.0)]['pooled_audit']['effects']['overall']['exact_net'],
        'discovery_selected':chosen, 'selected_audit_status':stability[key(chosen)]['status'],
        'status':frozen['status']},sort_keys=True),flush=True)
    return frozen


def _verify_frozen(dest,c):
    frozen=read(dest/'seal'/'factors.json')
    require(frozen.get('schema')==SCHEMA and
            frozen.get('status')=='SETTINGS_FROZEN_BEFORE_HOLDOUT' and
            frozen['precommit_sha256']==sha(dest/'seal'/'prepared.json') and
            frozen['train_lock_sha256']==sha(dest/'train'/'lock.json') and
            frozen['train_evidence_sha256']==sha(dest/'train_evidence.json') and
            frozen['levels']==list(LEVELS) and
            frozen['source_sha256']==c['source']['head_sha256'] and
            frozen['predeclared_primary_offset']==1.0 and
            frozen.get('weight_edits')==0 and frozen.get('g3_unlocked') is False
            and frozen.get('production_promotion') is False,
            'FROZEN_FACTOR_DRIFT')
    evidence=read(dest/'train_evidence.json')
    require(frozen['discovery_selected_offset']==evidence['selected_offset'] and
            frozen['discovery_selected_offset'] in LEVELS,
            'SELECTED_FACTOR_DRIFT')
    return frozen,evidence


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=location(root,output);c=verify(root,dest,phase='predict')
    _verify_frozen(dest,c)
    return _capture_split(root=root,dest=dest,c=c,split='holdout',device=device)


def _historical_preobserved(root, identity):
    """Previously observed campaigns, descriptive ONLY; never used to fit/veto."""
    available={}
    for name, arm in ((previous.NAME, 'admission_only'),
                      (last.NAME, 'global_0100')):
        p=Path(root)/name/'scored'/'report.json'
        if not p.is_file():
            available[name]={'available':False}
            continue
        obj=read(p)
        require(obj.get('source',{}).get('head_sha256')==identity['head_sha256'],
                'HISTORICAL_CHECKPOINT_MISMATCH')
        effect=obj['effects']['overall'][arm]
        available[name]={'available':True,'report_sha256':sha(p),'arm':arm,
                         'f1_delta':effect['f1'],
                         'exact_net':effect['exact'],
                         'fp_delta':effect['fp'],'fn_delta':effect['fn']}
    return available


def score(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output);c=verify(root,dest,phase='score')
    require(not (dest/'scored'/'report.json').exists(),'ALREADY_SCORED')
    frozen,evidence=_verify_frozen(dest,c)
    rec=_gold_join(dest,'holdout',_load_locked(root,dest,c,'holdout'))
    all_holdout={key(v):_evaluate(rec,v,seed=BOOT_SEED+100+i)
                 for i,v in enumerate(LEVELS)}
    fixed=all_holdout[key(1.0)]
    selected=all_holdout[key(frozen['discovery_selected_offset'])]
    train_primary=evidence['audits'][key(1.0)]
    # This is an EXPERIMENTAL evidence gate, never a production cutover.
    certified=(train_primary['pooled_support_rule_passes'] and
               fixed['effects']['overall']['f1']>=0.005 and
               fixed['effects']['overall']['exact_net']>=0 and
               fixed['paired'].get('exact_lost',0)==0 and
               all(fixed['effects'][fam]['f1']>=-0.002
                   for fam in ('allocation','encounter')) and
               fixed['f1_paired_bootstrap_95'][0]>0)
    source_unchanged=source._candidate_info(c['source']['checkpoint'])==c['source']
    require(source_unchanged,'ORIGINAL_CHECKPOINT_MUTATED')
    report={'schema':SCHEMA,'status':('GLOBAL_THRESHOLD_EVIDENCE_GATE_PASSED_NOT_PROMOTED'
                                      if certified else 'THRESHOLD_STABILITY_EVALUATED_NOT_CERTIFIED'),
            'train_cases':c['train_cases'],'cases':c['cases'], 'source':c['source'],
            'predeclared_fixed_1':fixed,'discovery_selected_offset':frozen['discovery_selected_offset'],
            'discovery_selected':selected,'blind_threshold_curve':all_holdout,
            'training_audits':evidence['audits'],
            'training_fold_curve':evidence['all_training_folds'],
            'previously_observed_holdouts_not_used_for_selection':
                _historical_preobserved(root,c['source']),
            'gate':bool(certified),'source_unchanged':source_unchanged,
            'weight_edits':0,'gradient_steps':0,
            'g3_unlocked':False,'production_promotion':False,
            'decision_rule':('Fixed +1 must have pooled supported training audits, '
                  'blind F1 >= +0.5pp, family F1 >= -0.2pp, no blind exact graph '
                  'losses, nonnegative net exact, and positive paired bootstrap lower bound. '
                  'Other levels are exploratory; never select from blind results.'),
            'limitations':('Synthetic author-marked test distribution; no exact guarantees. '
                    'Bootstrap CIs are descriptive approximations, not proof of safety.')}
    (dest/'scored').mkdir(exist_ok=True)
    write(dest/'scored'/'report.json',report)
    summary={'fixed_1':{'f1':fixed['effects']['overall']['f1'],
                        'exact':fixed['effects']['overall']['exact_net'],
                        'exact_lost':fixed['paired'].get('exact_lost',0),
                        'fp':fixed['effects']['overall']['fp'],
                        'fn':fixed['effects']['overall']['fn'],
                        'ci95':fixed['f1_paired_bootstrap_95']},
             'discovery_selected_offset':frozen['discovery_selected_offset'],
             'discovery_selected_f1':selected['effects']['overall']['f1'],
             'training_primary_status':train_primary['status'],
             'gate':certified,'source_unchanged':True,'status':report['status']}
    print('G2_0102_SCORED '+json.dumps(summary,sort_keys=True),flush=True)
    return report


def run(*,root=ROOT_DEFAULT,output=None,train_cases=TRAIN_CASES,
        cases=HOLDOUT_CASES,device='cuda'):
    dest=location(root,output)
    if not (dest/'seal'/'prepared.json').exists():
        prepare(root=root,output=output,train_cases=train_cases,cases=cases)
    if not (dest/'train'/'lock.json').exists():
        capture(root=root,output=output,device=device)
    if not (dest/'seal'/'factors.json').exists():
        calibrate(root=root,output=output)
    if not (dest/'holdout'/'lock.json').exists():
        predict(root=root,output=output,device=device)
    if not (dest/'scored'/'report.json').exists():
        return score(root=root,output=output)
    c=verify(root,dest,phase='score')
    _verify_frozen(dest,c)
    require(source._candidate_info(c['source']['checkpoint'])==c['source'],
            'ORIGINAL_CHECKPOINT_MUTATED')
    return read(dest/'scored'/'report.json')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest='command',required=True)
    for name in ('run','prepare','capture','calibrate','predict','score'):
        q=commands.add_parser(name)
        q.add_argument('--root',default=ROOT_DEFAULT)
        q.add_argument('--output')
        if name in ('run','prepare'):
            q.add_argument('--train-cases',type=int,default=TRAIN_CASES)
            q.add_argument('--cases',type=int,default=HOLDOUT_CASES)
        if name in ('run','capture','predict'):
            q.add_argument('--device',default='cuda')
    args=p.parse_args(argv)
    kwargs={'root':args.root,'output':args.output}
    if args.command in ('run','prepare'):
        kwargs.update(train_cases=args.train_cases,cases=args.cases)
    if args.command in ('run','capture','predict'):
        kwargs['device']=args.device
    globals()[args.command](**kwargs)

if __name__=='__main__':
    main()
