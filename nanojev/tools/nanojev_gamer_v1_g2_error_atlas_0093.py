"""G2-0093: frozen 1,000-base / 5,000-input error atlas with held-out causal confirmation.

    python -m tools.nanojev_gamer_v1_g2_error_atlas_0093 run --device cuda

Phases run in separate processes: prepare -> predict -> discover -> confirm -> probe.
No training, threshold fitting, checkpoint mutation, deployment, certification or G3 unlock.
The *discovery* labels select error classes; confirmation rules are frozen before
confirmation gold is read. All inference reads only prompts and locked checkpoints.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys

import torch

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_g2_frozen_generalization_0091 as frozen
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as activation
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as hard
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA='nanojev.gamer.graph.g2.large-scale-failure-atlas-0093.v1'
MODULE='tools.nanojev_gamer_v1_g2_error_atlas_0093'
DEFAULT_ROOT='../runtime/reports/gamer-v1'
OUTPUT_NAME='oracle-fabric-g2-large-scale-error-atlas-0093'
SEED=930093
DEFAULT_BASES=1000
MODES=('base','renaming','role_swap','added_distractor','action_removal')
FAMILIES=('allocation','encounter')
ARMS=('g2_0087','g2_0092_B')
MIN_CLASS_EVENTS=30
MIN_CLASS_CASES=20
TOP_CLASSES=8


def require(value, code):
    if not value:raise ValueError('G2_0093_'+code)


def sha(path):return hard.sha(path)
def read(path):return hard.js(path)
def write(path,obj):return hard.write_json(path,obj)
def digest(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()

def jsonl(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)

def jsonl_write(path,records):
    with Path(path).open('x',encoding='utf-8') as f:
        for r in records:f.write(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n')


def _atomic_gzip(path,value):
    """Each case is a resumable, atomic, compressed, individually hashed unit."""
    path=Path(path);tmp=path.with_suffix('.tmp')
    require(not tmp.exists(),'STALE_INCOMPLETE_SHARD_'+path.stem)
    try:
        with gzip.open(tmp,'wt',encoding='utf-8',compresslevel=6) as out:
            json.dump(value,out,sort_keys=True,separators=(',',':'),allow_nan=False)
        os.replace(tmp,path)
    finally:
        if tmp.exists():tmp.unlink()


def _shard_read(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:return json.load(f)


def _roots(root):
    p=frozen._roots(root)
    p['parent_0092']=Path(root)/'oracle-fabric-g2-mixed-family-fuzz-0092'
    return p


def parents(paths):
    """Read-only lineage verification of the actual failed 0092 Arm B state."""
    info=frozen.verify_parent(paths)
    old=paths['parent_0092'];report=read(old/'report.json')
    stage=old/'B_from_0090'/'stage-4'
    lock=read(stage/'lock.json');summary=read(stage/'report.json')
    require(report.get('status')=='MIXED_FAMILY_TRAINABILITY_BLOCKED' and
            report.get('g3_unlocked') is False and
            report['arms']['B_from_0090']['eight_pass'] is False,
            'PARENT_0092_NOT_EXPERIMENTAL_FAILURE')
    require(summary['arm']=='B_from_0090' and summary['best_exact']==2 and
            summary['checkpoint_sha256']==lock['checkpoint_sha256'] and
            lock['checkpoint_sha256']==sha(stage/'checkpoint'/cur.HEAD_FILE),
            'ARM_B_HEAD_DRIFT')
    manifest=read(stage/'checkpoint'/'manifest.json')
    require(manifest['head_sha256']==lock['checkpoint_sha256'] and
            manifest['g3_training_unlocked'] is False and
            manifest.get('g2_0089',{}).get('frozen_g1') is True,
            'ARM_B_MANIFEST_DRIFT')
    require(lock['g3_unlocked'] is False and
            summary['g3_unlocked'] is False and report['g2_certified'] is False,
            'FAILED_PARENT_CLAIMS_CERTIFICATION')
    for diagnostic in lock.get('diagnostics',[]):
        require(sha(stage/diagnostic['file'])==diagnostic['sha256'],
                'ARM_B_CAUSAL_EVIDENCE_DRIFT')
    for key,relative in [('history_sha256','history.json'),
                         ('journal_sha256','monitor.jsonl'),
                         ('report_sha256','report.json')]:
        if key in lock:require(sha(stage/relative)==lock[key],'ARM_B_FILE_DRIFT_'+key)
    # This comparison checks that every G1 and future-stage coefficient is unchanged.
    frozen_comparison=frozen.structural.native.compare_frozen_stage(
        paths['parent_0087']/'training',stage/'checkpoint')
    require(frozen_comparison['non_g2_weights_mutated'] is False,
            'G1_OR_FUTURE_STAGE_CHANGED')
    require(info['parent_sha256']==sha(paths['parent_0087']/'training'/cur.HEAD_FILE),
            'BASE_HEAD_CHANGED')
    return {'head_0087_sha256':info['parent_sha256'],
            'head_0092_B_sha256':lock['checkpoint_sha256'],
            'g1_certificate_sha256':info['g1_certificate_sha256'],
            'g1_composite_sha256':info['g1_composite_sha256'],
            'dataset_sha256':info['dataset_sha256'],
            'decoder':info['decoder'],
            'source_0092_report_sha256':sha(old/'report.json'),
            'source_0092_lock_sha256':sha(stage/'lock.json'),
            'source_0090_lock_sha256':info['stage_lock_sha256']}


def _known_prompts(paths,root):
    """Exclude all named preceding training/diagnostic/sealed prompts we can verify."""
    seen=frozen._excluded(paths,Path(root))
    for r in cur.load_rows(paths['dataset']):seen.add(digest(r['prompt']))
    for experiment in ('oracle-fabric-g2-frozen-generalization-0091',
                       'oracle-fabric-g2-mixed-family-fuzz-0092'):
        p=Path(root)/experiment/'seal'/'prompts.jsonl'
        if p.exists():
            for r in jsonl(p):seen.add(digest(r['prompt']))
    # 0092 uses author-generated diagnostic and training counterfactual prompts.
    prov=paths['parent_0092']/'provenance.json'
    if prov.exists():
        for row in read(prov).get('diagnostic_fuzz',[]):
            seen.update(x for x in (row.get('base_sha256'),row.get('fuzz_sha256')) if x)
    return seen


def _new_name(excluded,options):
    return next(name for name in options if name not in excluded)


def _controlled_suite(family,seed,serial):
    """Four transformations of THE SAME base, without redrawing numeric content.

    Using identical RNG seeds with explicit role/name overrides keeps all
    unaffected numerical text and gold relations constant. For removal, graph
    changes are author-annotated and evaluated against its own gold graph.
    """
    chooser=random.Random(seed)
    actors=tuple(corpus.ACTORS_TEST)
    names=chooser.sample(corpus.NAME_POOLS['test'],7)
    actor,rival=chooser.sample(actors,2)
    if family=='allocation':
        used=names[:3]
        def make(which,*,actor_name=actor,action_names=None):
            return corpus.allocation(random.Random(seed), 'test',serial,
                actor_override=actor_name,
                names_override=list(used if action_names is None else action_names))
        base=make('base')
        changed_actor=_new_name({actor},actors)
        rename=make('renaming',actor_name=changed_actor)
        swapped=[used[1],used[0],used[2]]
        role=make('role_swap',action_names=swapped)
        removal=make('action_removal',action_names=used[:-1])
    else:
        player=names[:3];opponent=names[3:5]
        def make(which,*,actor_name=actor,rival_name=rival,
                 player_names=None,opponent_names=None):
            return corpus.encounter(random.Random(seed),'test',serial,
                actor_override=actor_name,rival_override=rival_name,
                player_override=list(player if player_names is None else player_names),
                opponent_override=list(opponent if opponent_names is None else opponent_names))
        base=make('base')
        third=_new_name({actor,rival},actors)
        rename=make('renaming',actor_name=third)
        role=make('role_swap',actor_name=rival,rival_name=actor)
        removal=make('action_removal',player_names=player[:2])
    distractor=deepcopy(base)
    distractor['prompt']+=' Independent note: '+names[5]+' is not involved in the choice.'
    result=dict(zip(MODES,(base,rename,role,distractor,removal)))
    orig={(e['src'],e['dst'],e['type']) for e in base['graph']['edges']}
    for mode,row in result.items():
        require(row['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR',
                'SOURCE_ANNOTATIONS_MISSING')
        if mode in ('renaming','role_swap','added_distractor'):
            require({(e['src'],e['dst'],e['type']) for e in row['graph']['edges']}==orig,
                    'INVARIANT_GOLD_DRIFT_'+mode)
        elif mode=='action_removal':
            require({(e['src'],e['dst'],e['type']) for e in row['graph']['edges']}!=orig,
                    'ACTION_REMOVAL_NO_GOLD_DELTA')
        for n in row['graph']['nodes']:
            if 'span' not in n:continue
            start,stop=n['span']
            require(0<=start<stop<=len(row['prompt']),
                    'AUTHOR_SPAN_OUTSIDE_PROMPT')
    require(len({digest(r['prompt']) for r in result.values()})==len(MODES),
            'FUZZ_NOT_DISTINCT')
    return result


def build_cohort(paths,root,*,bases=DEFAULT_BASES,seed=SEED):
    require(type(bases) is int and bases>=4 and bases%10==0,
            'BASE_COUNT_MUST_BE_MULTIPLE_OF_TEN')
    known=_known_prompts(paths,root);rng=random.Random(seed)
    public=[];secret=[];balance=Counter()
    per_family=bases//2
    for base_index in range(bases):
        family=FAMILIES[base_index%2]
        family_index=base_index//2
        split=('discovery' if family_index<7*per_family//10 else 'confirmation')
        for attempt in range(100):
            serial=93_000_000+base_index*101+attempt
            suite=_controlled_suite(family,rng.randrange(2**31),serial)
            hashes=[digest(v['prompt']) for v in suite.values()]
            if all(x not in known for x in hashes):break
        else:raise ValueError('G2_0093_UNIQUE_FUZZ_SUITE_EXHAUSTED')
        known.update(hashes)
        base_id=f'g2-0093-{base_index+1:05}'
        for mode,row in suite.items():
            cid=base_id+'-'+mode
            public.append({'case_id':cid,'base_id':base_id,'mode':mode,
                           'family':family,'split':split,'prompt':row['prompt'],
                           'prompt_sha256':digest(row['prompt'])})
            secret.append({'case_id':cid,'base_id':base_id,'mode':mode,
                           'family':family,'split':split,
                           'prompt_sha256':digest(row['prompt']),
                           'graph':row['graph'],'gold_objects':row['object_spans']})
            balance[(split,family,mode)]+=1
    require(len(public)==len(secret)==bases*5 and
            len({r['prompt_sha256'] for r in public})==len(public),
            'CORPUS_DUPLICATES')
    require(all(balance[('discovery',f,m)]==7*per_family//10 and
                balance[('confirmation',f,m)]==per_family-7*per_family//10
                for f in FAMILIES for m in MODES),'COHORT_BALANCE')
    return public,secret


def prepare(*,root=DEFAULT_ROOT,output=None,bases=DEFAULT_BASES,seed=SEED):
    paths=_roots(root);dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
    lineage=parents(paths)
    public,secret=build_cohort(paths,root,bases=bases,seed=seed)
    dest.mkdir(exist_ok=False);seal=dest/'seal';seal.mkdir(exist_ok=False)
    jsonl_write(seal/'prompts.jsonl',public)
    jsonl_write(seal/'answers.discovery.SECRET.jsonl',
                (r for r in secret if r['split']=='discovery'))
    jsonl_write(seal/'answers.confirmation.SECRET.jsonl',
                (r for r in secret if r['split']=='confirmation'))
    record={'schema':SCHEMA,'state':'GOLD_COMMITTED_BEFORE_INFERENCE',
       'bases':bases,'inputs':5*bases,'seed':seed,
       'discovery_bases':2*(7*(bases//2)//10),
       'confirmation_bases':bases-2*(7*(bases//2)//10),
       'prompt_sha256':sha(seal/'prompts.jsonl'),
       'answers_sha256':{split:sha(seal/f'answers.{split}.SECRET.jsonl')
                         for split in ('discovery','confirmation')},
       'runner_sha256':sha(__file__),'lineage':lineage,
       'gold_not_used_in_inference':True,'frozen_checkpoints':True,
       'generator_independent_distribution':False,'g3_unlocked':False}
    write(seal/'commit.json',record)
    print('G2_0093_PREPARED '+json.dumps({'bases':bases,'inputs':5*bases,
       'discovery':record['discovery_bases']*5,
       'confirmation':record['confirmation_bases']*5,
       'head_0092_B':lineage['head_0092_B_sha256']},sort_keys=True),flush=True)
    return record


def _check(paths,dest,*,gold=False):
    record=read(dest/'seal'/'commit.json')
    require(record['schema']==SCHEMA and record['g3_unlocked'] is False and
            record['state']=='GOLD_COMMITTED_BEFORE_INFERENCE' and
            record['runner_sha256']==sha(__file__) and
            record['prompt_sha256']==sha(dest/'seal'/'prompts.jsonl') and
            record['lineage']==parents(paths), 'SEAL_OR_SOURCE_DRIFT')
    if gold:
        for split in (('discovery','confirmation') if gold is True else (gold,)):
            require(split in ('discovery','confirmation') and
                    record['answers_sha256'][split]==sha(
                        dest/'seal'/f'answers.{split}.SECRET.jsonl'),
                    'GOLD_COMMIT_BROKEN_'+str(split))
    return record


def _blind_case(row,network,tok,composite,heads,device):
    """Strictly gold-blind; only uses prompt, family, and frozen neural weights."""
    prepared,h=bakeoff.previous._capture_inference(network,tok,row['prompt'],device,2048)
    spans=composite.predict(h,prepared,family=row['family'],native_stage='G1')['token_spans']
    surfaces={}
    with torch.inference_mode():
        for arm,head in heads.items():
            surfaces[arm]=frozen._infer(head,h,prepared['eligible'].to(device),spans,prepared)
    require(surfaces[ARMS[0]]['g1_objects']==surfaces[ARMS[1]]['g1_objects'],
            'FROZEN_G1_DISAGREEMENT')
    return {'case_id':row['case_id'],'prompt_sha256':row['prompt_sha256'],
            'family':row['family'],'split':row['split'],'mode':row['mode'],
            'base_id':row['base_id'],'g1_objects':surfaces[ARMS[0]]['g1_objects'],
            'raw_heads':surfaces}


def _shard_index(dest,rows,*,must_complete):
    directory=dest/'blind'/'shards';indexed=[]
    for row in rows:
        path=directory/(row['case_id']+'.json.gz')
        if not path.exists():
            require(not must_complete,'BLIND_SHARD_MISSING_'+row['case_id'])
            continue
        record=_shard_read(path)
        require(record['case_id']==row['case_id'] and
                record['prompt_sha256']==row['prompt_sha256'] and
                record['split']==row['split'] and record['mode']==row['mode'] and
                record['family']==row['family'] and record['base_id']==row['base_id'] and
                set(record['raw_heads'])==set(ARMS),'BLIND_SHARD_IDENTITY_DRIFT')
        for arm in ARMS:
            bakeoff.validate_surface(record['raw_heads'][arm])
            require(record['g1_objects']==record['raw_heads'][arm]['g1_objects'],
                    'BLIND_G1_MISMATCH')
        indexed.append((row['case_id'],sha(path)))
    return indexed


def predict(*,root=DEFAULT_ROOT,output=None,device='cuda'):
    paths=_roots(root);dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    commit=_check(paths,dest,gold=False)
    require(not (dest/'blind'/'lock.json').exists(),'BLIND_ALREADY_LOCKED')
    prompts=list(jsonl(dest/'seal'/'prompts.jsonl'))
    require(len(prompts)==commit['inputs'],'PROMPT_COUNT')
    blind=dest/'blind';blind.mkdir(exist_ok=True)
    shard_dir=blind/'shards';shard_dir.mkdir(exist_ok=True)
    # Warm startup and model load only if at least one case remains unfinished.
    pending=[r for r in prompts if not (shard_dir/(r['case_id']+'.json.gz')).exists()]
    if pending:
        record,_=hard._verify_trained(paths,paths['parent_0087'])
        network,tok=backbone.model_components(record['model_id'],paths['adapter'],device)
        require(all(not p.requires_grad for p in network.parameters()),'BACKBONE_NOT_FROZEN')
        composite=g1native.load_composite(paths['composite'],device=device)
        a,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
        b,_=cur._load_checkpoint(paths['parent_0092']/'B_from_0090'/'stage-4'/'checkpoint',device=device)
        heads=dict(zip(ARMS,(a,b)))
        for head in heads.values():head.eval().requires_grad_(False)
        for count,row in enumerate(prompts,1):
            target=shard_dir/(row['case_id']+'.json.gz')
            if not target.exists():
                require(digest(row['prompt'])==row['prompt_sha256'],'PROMPT_HASH_CHANGED')
                _atomic_gzip(target,_blind_case(row,network,tok,composite,heads,device))
            if count==1 or count%50==0 or count==len(prompts):
                print(f'G2_0093_BLIND {count}/{len(prompts)} frozen_heads',flush=True)
    index=_shard_index(dest,prompts,must_complete=True)
    require(len(index)==commit['inputs'],'INCOMPLETE_BLIND_INDEX')
    manifest={'schema':SCHEMA,'cases':len(index),'entries':index,
              'prompts_sha256':commit['prompt_sha256'],
              'head_sha256':{arm:commit['lineage'][key] for arm,key in
                            [('g2_0087','head_0087_sha256'),
                             ('g2_0092_B','head_0092_B_sha256')]},
              'gold_used':False,'g3_unlocked':False}
    write(blind/'index.json',manifest)
    lock={'schema':SCHEMA,'state':'BLIND_PREDICTIONS_HASH_LOCKED',
          'cases':len(index),'index_sha256':sha(blind/'index.json'),
          'prompts_sha256':commit['prompt_sha256'],'gold_used':False,
          'g3_unlocked':False}
    write(blind/'lock.json',lock)
    print('G2_0093_BLIND_LOCKED '+json.dumps({'cases':len(index),
             'index_sha256':lock['index_sha256']},sort_keys=True),flush=True)
    return lock


def _verify_blind(dest,commit):
    lock=read(dest/'blind'/'lock.json');index=read(dest/'blind'/'index.json')
    require(lock['schema']==index['schema']==SCHEMA and
            lock['state']=='BLIND_PREDICTIONS_HASH_LOCKED' and
            lock['gold_used'] is False and index['gold_used'] is False and
            lock['g3_unlocked'] is False and index['g3_unlocked'] is False and
            lock['index_sha256']==sha(dest/'blind'/'index.json') and
            index['prompts_sha256']==commit['prompt_sha256'] and
            index['cases']==commit['inputs']==len(index['entries']),
            'BLIND_LOCK_INVALID')
    prompts={r['case_id']:r for r in jsonl(dest/'seal'/'prompts.jsonl')}
    require(len(prompts)==commit['inputs'],'PROMPT_IDS_DUPLICATE')
    for cid,hashvalue in index['entries']:
        require(cid in prompts and
                sha(dest/'blind'/'shards'/(cid+'.json.gz'))==hashvalue,
                'BLIND_SHARD_DRIFT')
    require(len(set(cid for cid,_ in index['entries']))==commit['inputs'] and
            set(cid for cid,_ in index['entries'])==set(prompts),
            'BLIND_CASE_UNIVERSE_CHANGED')
    return prompts


def _gold_edges(graph):return bakeoff.truth(graph)

def _reachable(gold,objects):
    anchors={(o[2],(o[0],o[1])) for o in objects}
    return {e for e in gold if (e[0],tuple(e[1])) in anchors and
                              (e[2],tuple(e[3])) in anchors}


def _error_signature(edge,kind):
    return '|'.join((kind,edge[0],edge[2],edge[4]))


def _edge_rows(pred,gold,objects):
    """Enumerate ALL graph errors; distinguish upstream omissions and wrong types."""
    reachable=_reachable(gold,objects)
    by_pair={(e[0],tuple(e[1]),e[2],tuple(e[3])):e for e in pred}
    goldpairs={(e[0],tuple(e[1]),e[2],tuple(e[3])):e for e in gold}
    events=[]
    for e in sorted(gold-pred):
        pair=(e[0],tuple(e[1]),e[2],tuple(e[3]))
        category=('unreachable_g1' if e not in reachable else
                  'wrong_type' if pair in by_pair else 'missed')
        events.append({'kind':category,'edge':_json_edge(e),
                       'signature':_error_signature(e,category)})
    for e in sorted(pred-gold):
        pair=(e[0],tuple(e[1]),e[2],tuple(e[3]))
        category='wrong_type_prediction' if pair in goldpairs else 'extra'
        events.append({'kind':category,'edge':_json_edge(e),
                       'signature':_error_signature(e,category)})
    return events,reachable


def _json_edge(e):return [e[0],list(e[1]),e[2],list(e[3]),e[4]]


def _pair_rank(surface, gold):
    """Same-source, same-destination-kind, same-binding-type gold rankings."""
    objects,pairs=bakeoff.validate_surface(surface)
    anchor={(o[2],(o[0],o[1])):i for i,o in enumerate(objects)}
    grouped=defaultdict(list)
    for edge in gold:
        src=anchor.get((edge[0],tuple(edge[1])))
        dst=anchor.get((edge[2],tuple(edge[3])))
        if src is None or dst is None:continue
        label=BIND_CLASSES.index(edge[4])-1
        if label<0:continue
        grouped[(src,edge[2],label)].append(dst)
    total=0;success=0;inverted=0
    for (src,dst_kind,label),true_dsts in grouped.items():
        false=[j for j,o in enumerate(objects) if j!=src and o[2]==dst_kind and
               j not in true_dsts]
        if not false:continue
        if any((src,j) not in pairs for j in true_dsts+false):continue
        total+=1
        strong_false=max(pairs[(src,j)][label] for j in false)
        if min(pairs[(src,j)][label] for j in true_dsts)>strong_false:success+=1
        else:inverted+=1
    return {'groups':total,'success':success,'inverted':inverted}


def _score_case(record,answer,spec):
    gold=_gold_edges(answer['graph'])
    objects=record['g1_objects'];out={}
    for arm in ARMS:
        surface=record['raw_heads'][arm]
        pred=bakeoff.decode(surface,spec)
        events,reachable=_edge_rows(pred,gold,objects)
        ranked=_pair_rank(surface,gold)
        tp=len(pred&gold)
        out[arm]={'exact':int(pred==gold),'tp':tp,'fp':len(pred-gold),
                  'fn':len(gold-pred),'gold_edges':len(gold),
                  'predicted_edges':len(pred), 'unreachable':len(gold-reachable),
                  'g1_reachable':int(len(gold)==len(reachable)),
                  'ranking':ranked,'errors':events,'edges':[_json_edge(e) for e in sorted(pred)]}
    return out


def _id_edge_set(edges,graph,*,semantic=False):
    surface={(r[0],tuple(r[1]),r[2],tuple(r[3]),r[4]) for r in edges}
    if not semantic:return frozen._normalized_edges(surface,graph)
    # Removal changes author node numbering. Compare unchanged surface text
    # occurrences instead, so renumbering is not counted as a relationship edit.
    require('_g2_0093_prompt' in graph,'REMOVAL_PROMPT_NOT_AVAILABLE')
    prompt=graph['_g2_0093_prompt'];counts=Counter();anchors={}
    for node in graph['nodes']:
        if 'span' not in node:continue
        start,stop=node['span'];piece=prompt[start:stop]
        require(piece and 0<=start<stop<=len(prompt),'BAD_SEMANTIC_ANCHOR')
        k=(node['kind'],piece)
        counts[k]+=1
        anchors[(node['kind'],tuple(node['span']))]=(*k,counts[k])
    return {(anchors.get((s,si),('UNKNOWN',s,str(si))),
             anchors.get((d,di),('UNKNOWN',d,str(di))),typ)
            for s,si,d,di,typ in surface}


def _score_suite(rows):
    """One base and four semantic transforms, using surface binding gold ONLY."""
    require({r['mode'] for r in rows}==set(MODES),'FUZZ_SUITE_MISSING')
    by={r['mode']:r for r in rows};base=by['base'];result={}
    for mode in MODES[1:]:
        alt=by[mode];semantic=(mode=='action_removal')
        gold_base=_id_edge_set([_json_edge(e) for e in
               bakeoff.truth(base['gold_graph'])],base['gold_graph'],semantic=semantic)
        gold_alt=_id_edge_set([_json_edge(e) for e in
               bakeoff.truth(alt['gold_graph'])],alt['gold_graph'],semantic=semantic)
        require((gold_base==gold_alt)==(not semantic),
                'COUNTERFACTUAL_GOLD_EXPECTATION_'+mode)
        results={}
        for arm in ARMS:
            a=_id_edge_set(base['arms'][arm]['edges'],base['gold_graph'],semantic=semantic)
            b=_id_edge_set(alt['arms'][arm]['edges'],alt['gold_graph'],semantic=semantic)
            results[arm]={'g1_exact_both':bool(base['g1_exact'] and alt['g1_exact']),
                'correct_both':bool(a==gold_base and b==gold_alt),
                'delta_correct':bool((a^b)==(gold_base^gold_alt)),
                'predicted_invariant':bool(a==b),
                'gold_invariant':bool(gold_base==gold_alt),
                'gold_delta':len(gold_base^gold_alt),
                'predicted_delta':len(a^b),
                'alignment':'surface-occurrence' if semantic else 'stable-author-id'}
        result[mode]=results
    return result


def wilson_interval(successes,total,z=1.96):
    """Unadjusted 95% Wilson interval, for independent base-graph draws.

    Fuzz variants are intentionally excluded from the effective sample size.
    This describes the same author-generator distribution only.
    """
    if total<=0:return [None,None]
    p=successes/total;z2=z*z
    mid=(p+z2/(2*total))/(1+z2/total)
    half=z*math.sqrt((p*(1-p)+z2/(4*total))/total)/(1+z2/total)
    return [max(0.0,mid-half),min(1.0,mid+half)]


def _evaluate_split(dest,split,commit):
    # Gold opens only in this scorer; 'discover' never reads confirmation records.
    answers={r['case_id']:r for r in jsonl(
        dest/'seal'/f'answers.{split}.SECRET.jsonl')}
    prompts=[r for r in jsonl(dest/'seal'/'prompts.jsonl') if r['split']==split]
    require(len(prompts)==len(answers) and len(prompts)%len(MODES)==0,
            'SCORING_SPLIT_MISMATCH')
    grouped=defaultdict(list);summary=defaultdict(Counter)
    event_count=Counter();case_count=Counter();base_count=defaultdict(set)
    per_arm=defaultdict(lambda:defaultdict(Counter))
    rows=[];fuzz_summary=defaultdict(Counter)
    for p in prompts:
        answer=answers[p['case_id']]
        require(p['prompt_sha256']==answer['prompt_sha256'] and
                p['base_id']==answer['base_id'] and p['mode']==answer['mode'],
                'ANSWER_PROMPT_MISMATCH')
        blind=_shard_read(dest/'blind'/'shards'/(p['case_id']+'.json.gz'))
        require(blind['prompt_sha256']==p['prompt_sha256'] and
                blind['family']==p['family'] and blind['split']==split,
                'BLIND_ANSWER_ID_DRIFT')
        outcomes=_score_case(blind,answer,commit['lineage']['decoder'])
        gold_objects={(n['kind'],tuple(n['span'])) for n in answer['graph']['nodes']
                      if 'span' in n}
        g1_exact={(x[2],(x[0],x[1])) for x in blind['g1_objects']}==gold_objects
        item={'case_id':p['case_id'],'base_id':p['base_id'],'split':split,
              'family':p['family'],'mode':p['mode'],'g1_exact':bool(g1_exact),
              'gold_graph':dict(answer['graph'],_g2_0093_prompt=p['prompt']),
              'arms':outcomes}
        grouped[p['base_id']].append(item)
        for arm,metric in outcomes.items():
            c=per_arm[arm][p['family']]
            for k in ('tp','fp','fn','exact','unreachable','g1_reachable'):
                c[k]+=metric[k]
            c['cases']+=1;c['ranking_groups']+=metric['ranking']['groups']
            c['ranking_success']+=metric['ranking']['success']
            for sig in {e['signature'] for e in metric['errors']}:
                case_count[(arm,p['family'],sig)]+=1
                base_count[(arm,p['family'],sig)].add(p['base_id'])
            for event in metric['errors']:
                event_count[(arm,p['family'],event['signature'])]+=1
        rows.append(item)
    require(len(grouped)==len(prompts)//len(MODES) and
            all(len(v)==len(MODES) for v in grouped.values()),'FUZZ_GROUP_COUNT')
    pairs=[]
    for bid,items in sorted(grouped.items()):
        report=_score_suite(items)
        pairs.append({'base_id':bid,'split':split,'family':items[0]['family'],
                      'transformations':report})
        for mode,per in report.items():
            for arm,m in per.items():
                c=fuzz_summary[(arm,mode,items[0]['family'])]
                for k in ('correct_both','delta_correct','g1_exact_both',
                          'predicted_invariant'):
                    c[k]+=int(m[k])
                c['pairs']+=1
    return (rows,pairs,per_arm,event_count,case_count,fuzz_summary,
            {k:len(v) for k,v in base_count.items()})


def _compact_case(row):
    return {k:v for k,v in row.items() if k!='gold_graph'}


def _summary(per_arm):
    result={}
    for arm,by in per_arm.items():
        result[arm]={}
        for family,count in by.items():
            c=dict(count);c['edge_f1']=2*c['tp']/max(1,2*c['tp']+c['fp']+c['fn'])
            c['exact_rate']=c['exact']/max(1,c['cases'])
            c['reachable_ceiling']=c['g1_reachable']/max(1,c['cases'])
            result[arm][family]=c
    return result


def _signature_ledger(event_count,case_count,base_count,arm,bases_per_family):
    signatures=[]
    for (found,family,sig),num in event_count.items():
        if found!=arm:continue
        cases=case_count[(found,family,sig)]
        affected=base_count.get((found,family,sig),0)
        category,src,dst,binding=sig.split('|',3)
        signatures.append({'family':family,'category':category,'src_kind':src,
                'dst_kind':dst,'binding':binding,'signature':sig,
                'events':num,'affected_cases':cases,
                'affected_base_graphs':affected,'base_graphs':bases_per_family,
                'base_prevalence':affected/max(1,bases_per_family),
                'base_wilson_95':wilson_interval(affected,bases_per_family)})
    signatures.sort(key=lambda r:(-r['affected_base_graphs'],-r['events'],
                                  r['family'],r['signature']))
    return signatures


def discover(*,root=DEFAULT_ROOT,output=None):
    paths=_roots(root);dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    commit=_check(paths,dest,gold='discovery')
    _verify_blind(dest,commit)
    require(not (dest/'discovery').exists(),'DISCOVERY_ALREADY_FROZEN')
    # Validate entire secret gold seal, but use only discovery rows to fit hypotheses.
    rows,pairs,stats,events,cases,fuzz,base_cases=_evaluate_split(
        dest,'discovery',commit)
    signatures=_signature_ledger(events,cases,base_cases,'g2_0092_B',
        commit['discovery_bases']//2)
    chosen=[r for r in signatures if r['category'] in ('extra','missed','wrong_type',
             'wrong_type_prediction') and r['events']>=MIN_CLASS_EVENTS and
             r['affected_cases']>=MIN_CLASS_CASES and
             r['affected_base_graphs']>=10][:TOP_CLASSES]
    hypotheses={'schema':SCHEMA,'phase':'FROZEN_DISCOVERY_HYPOTHESES',
                'signature_selection':'largest affected discovery case count, then edge count',
                'rules':chosen,'min_events':MIN_CLASS_EVENTS,
                'min_cases':MIN_CLASS_CASES,'max_classes':TOP_CLASSES,
                'intervention_selection':'blind highest matching predicted edge margin; no gold selection',
                'probes_per_rule_per_split':4,
                'no_confirmation_gold_used':True,
                'g3_unlocked':False}
    area=dest/'discovery';area.mkdir(exist_ok=False)
    write(area/'hypotheses.json',hypotheses)
    write(area/'summary.json',{'schema':SCHEMA,'split':'discovery',
        'cases':len(rows),'base_graphs':len(pairs),'arms':_summary(stats),
        'ranked_failure_classes':signatures,
        'fuzz':{'|'.join(k):dict(v) for k,v in fuzz.items()},
        'g1_not_edited':True,'g3_unlocked':False})
    with gzip.open(area/'cases.jsonl.gz','wt',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(_compact_case(r),sort_keys=True)+'\n')
    jsonl_write(area/'counterfactuals.jsonl',pairs)
    write(area/'lock.json',{'schema':SCHEMA,
         'hypotheses_sha256':sha(area/'hypotheses.json'),
         'summary_sha256':sha(area/'summary.json'),
         'cases_sha256':sha(area/'cases.jsonl.gz'),
         'counterfactual_sha256':sha(area/'counterfactuals.jsonl'),
         'blind_index_sha256':sha(dest/'blind'/'index.json'),
         'g3_unlocked':False})
    print('G2_0093_DISCOVERY '+json.dumps({'cases':len(rows),
           'hypotheses':len(chosen),'locked':sha(area/'lock.json')},sort_keys=True),flush=True)
    return hypotheses


def _discovery_lock(dest):
    lock=read(dest/'discovery'/'lock.json')
    area=dest/'discovery'
    for field,filename in [('hypotheses_sha256','hypotheses.json'),
              ('summary_sha256','summary.json'),('cases_sha256','cases.jsonl.gz'),
              ('counterfactual_sha256','counterfactuals.jsonl')]:
        require(sha(area/filename)==lock[field], 'DISCOVERY_LOCK_DRIFT_'+field)
    require(lock['blind_index_sha256']==sha(dest/'blind'/'index.json')
            and lock['g3_unlocked'] is False,'DISCOVERY_BLIND_CHANGED')
    hypothesis=read(area/'hypotheses.json')
    require(hypothesis['phase']=='FROZEN_DISCOVERY_HYPOTHESES' and
            hypothesis['no_confirmation_gold_used'] is True and
            len(hypothesis['rules'])<=TOP_CLASSES,'HYPOTHESIS_INVALID')
    return hypothesis


def confirm(*,root=DEFAULT_ROOT,output=None):
    paths=_roots(root);dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    commit=_check(paths,dest,gold='confirmation');_verify_blind(dest,commit)
    hypothesis=_discovery_lock(dest)
    require(not (dest/'confirmation').exists(),'CONFIRMATION_ALREADY_SCORED')
    rows,pairs,stats,events,cases,fuzz,base_cases=_evaluate_split(
        dest,'confirmation',commit)
    signatures=_signature_ledger(events,cases,base_cases,'g2_0092_B',
        commit['confirmation_bases']//2)
    signature_idx={(r['family'],r['signature']):r for r in signatures}
    # No new confirmation-derived hypotheses may be selected.
    replicated=[]
    for rule in hypothesis['rules']:
        seen=signature_idx.get((rule['family'],rule['signature']),{})
        family_cases=sum(r['family']==rule['family'] for r in rows)
        family_bases=commit['confirmation_bases']//2
        current=seen.get('affected_base_graphs',0)
        rate=current/max(1,family_bases)
        prior=rule['base_prevalence']
        # Descriptive replication, NOT a causal or external-distribution claim.
        replicated.append(dict(rule,confirmation_events=seen.get('events',0),
              confirmation_affected_cases=seen.get('affected_cases',0),
              confirmation_cases=family_cases,
              confirmation_affected_base_graphs=current,
              confirmation_base_graphs=family_bases,
              confirmation_rate=rate,
              confirmation_wilson_95=wilson_interval(current,family_bases),
              replication_criterion='>=5_affected_bases_and_within_factor_two',
              replicated=(current>=5 and .5*prior<=rate<=2*prior)))
    area=dest/'confirmation';area.mkdir(exist_ok=False)
    write(area/'summary.json',{'schema':SCHEMA,'split':'confirmation',
         'cases':len(rows),'base_graphs':len(pairs), 'arms':_summary(stats),
         'predeclared_hypothesis_sha256':sha(dest/'discovery'/'hypotheses.json'),
         'replication':replicated,'fuzz':{'|'.join(k):dict(v) for k,v in fuzz.items()},
         'g3_unlocked':False,'independent_distribution':False})
    with gzip.open(area/'cases.jsonl.gz','wt',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(_compact_case(r),sort_keys=True)+'\n')
    jsonl_write(area/'counterfactuals.jsonl',pairs)
    write(area/'lock.json',{'schema':SCHEMA,
         'summary_sha256':sha(area/'summary.json'),
         'cases_sha256':sha(area/'cases.jsonl.gz'),
         'counterfactual_sha256':sha(area/'counterfactuals.jsonl'),
         'hypotheses_sha256':sha(dest/'discovery'/'hypotheses.json'),
         'g3_unlocked':False})
    print('G2_0093_CONFIRMATION '+json.dumps({'cases':len(rows),
         'replicated_classes':sum(r['replicated'] for r in replicated),
         'of':len(replicated),'locked':sha(area/'lock.json')},sort_keys=True),flush=True)
    return replicated


def _confirm_lock(dest):
    lock=read(dest/'confirmation'/'lock.json');area=dest/'confirmation'
    for k,name in [('summary_sha256','summary.json'),
                   ('cases_sha256','cases.jsonl.gz'),
                   ('counterfactual_sha256','counterfactuals.jsonl')]:
        require(sha(area/name)==lock[k],'CONFIRMATION_LOCK_DRIFT')
    require(lock['hypotheses_sha256']==sha(dest/'discovery'/'hypotheses.json') and
            lock['g3_unlocked'] is False,'CONFIRMATION_HYPOTHESIS_DRIFT')


def _blind_target(surface,rule,*,side='high'):
    """Selection is allowed to see model scores + frozen rule, never gold."""
    require(side in ('high','low'),'BAD_PROBE_SCORE_SIDE')
    objs,pairs=bakeoff.validate_surface(surface)
    if rule['category'] in ('extra','wrong_type_prediction'):
        direction='predicted'
    else:direction='all'
    best=None
    label=BIND_CLASSES.index(rule['binding'])-1
    if label<0:return None
    for (i,j),margins in pairs.items():
        if objs[i][2]!=rule['src_kind'] or objs[j][2]!=rule['dst_kind']:continue
        edge_max=max(range(len(margins)),key=lambda k:(margins[k],-k))
        if direction=='predicted' and (edge_max!=label or margins[label]<=0):continue
        value=float(margins[label]);candidate=((value if side=='high' else -value),-i,-j,i,j)
        if best is None or candidate>best:best=candidate
    return (best[-2],best[-1]) if best is not None else None


def _pick_probes(dest,hypothesis,split):
    prompts=[r for r in jsonl(dest/'seal'/'prompts.jsonl') if r['split']==split]
    chosen=[];used=set()
    for rule in hypothesis['rules']:
        count=0
        for p in prompts:
            if p['family']!=rule['family'] or p['case_id'] in used:continue
            record=_shard_read(dest/'blind'/'shards'/(p['case_id']+'.json.gz'))
            side=('high' if count%2==0 else 'low')
            target=_blind_target(record['raw_heads']['g2_0092_B'],rule,side=side)
            if target is None:continue
            used.add(p['case_id']);count+=1
            chosen.append({'case_id':p['case_id'],'rule':rule['signature'],
                           'family':p['family'],'indices':target,'split':split,
                           'blind_score_stratum':side})
            if count==hypothesis['probes_per_rule_per_split']:break
    return chosen


def _probe_case(head,net,tok,composite,public,gold,target,device):
    """Limited causal evidence: interventions + sham and unrelated-object controls."""
    prepared,h=bakeoff.previous._capture_inference(net,tok,public['prompt'],device,2048)
    spans=composite.predict(h,prepared,family=public['family'],native_stage='G1')['token_spans']
    with torch.inference_mode():
        output,captures=activation.trace_head(head,h,prepared['eligible'].to(device),spans)
    i,j=target['indices']
    require(0<=i<len(spans) and 0<=j<len(spans) and i!=j,'INVALID_CAUSAL_PAIR')
    baseline=output['binding_logits'][i,j].detach().float()
    gold_map=activation._pairs_by_gold(gold['graph'])
    anchors=[]
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
    for span in spans:
        bounds=token_span_char_bounds(span,prepared)
        require(bounds is not None,'UNMAPPABLE_PROBED_OBJECT')
        anchors.append((*bounds,span['kind']))
    label=gold_map.get((anchors[i],anchors[j]))
    gold_index=BIND_CLASSES.index(label) if label else 0
    original=activation._decision(baseline)
    outcomes=[]
    # Precisely one edge-representation intervention, one source-token
    # intervention, and an unrelated-source control; sham is an equality check.
    def result(name,logits):
        changed=logits[i,j].detach().float()
        predicted=activation._decision(changed)
        return {'intervention':name,'baseline_pred':original,
                'intervened_pred':predicted,'gold_label':gold_index,
                'correct_before':original==gold_index,
                'correct_after':predicted==gold_index,
                'decision_changed':predicted!=original,
                'typed_margin_delta':float((changed[1:]-changed[0]).max()-
                                           (baseline[1:]-baseline[0]).max())}
    outcomes.append(result('sham_unmodified',output['binding_logits']))
    for name,stage,src,pair in [
        ('pair_projection_zero','pair_projection',None,(i,j)),
        ('encoder_source_zero','encoder',i,None),
        ('coupler_source_zero','g1_g2_coupler',i,None)]:
        if stage not in activation.stage_modules(head):continue
        patched=activation._patch_head(head,h,prepared['eligible'].to(device),spans,
                stage=stage,src_index=src,pair=pair)
        outcomes.append(result(name,patched))
    others=[k for k in range(len(spans)) if k not in (i,j)]
    if others:
        patched=activation._patch_head(head,h,prepared['eligible'].to(device),spans,
                 stage='encoder',src_index=others[0])
        outcomes.append(result('unrelated_source_zero_control',patched))
    require(outcomes[0]['decision_changed'] is False and
            outcomes[0]['typed_margin_delta']==0.,'SHAM_IS_NOT_IDENTITY')
    diagnostic_pairs=activation._pair_stats(output['binding_logits'],spans,
                                             prepared,gold['graph'])
    return {'outcomes':outcomes,'g1_object_count':len(spans),
            'gold_edge_present':bool(gold_index),
            'binding_score_separability':activation.summarize_pairs(diagnostic_pairs),
            'stage_gold_conditioned_geometry':activation._representation_probe(
                 captures,spans,diagnostic_pairs),
            'stage_norms':{name:float(torch.linalg.vector_norm(captures[name]))
                       for name in ('project','encoder','g1_g2_coupler',
                                    'pair_projection','binding_input') if name in captures},
            'nonselective_if_true_positive_lost':True}


def probe(*,root=DEFAULT_ROOT,output=None,device='cuda'):
    paths=_roots(root);dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    commit=_check(paths,dest,gold=True);_verify_blind(dest,commit)
    hypothesis=_discovery_lock(dest);_confirm_lock(dest)
    require(not (dest/'causal').exists(),'CAUSAL_ALREADY_COMPLETED')
    chosen=[*_pick_probes(dest,hypothesis,'discovery'),
            *_pick_probes(dest,hypothesis,'confirmation')]
    require(all(x['rule'] in {r['signature'] for r in hypothesis['rules']}
                for x in chosen),'UNPREDECLARED_PROBE')
    selection=dest/'probe-selection.json'
    write(selection,{'schema':SCHEMA,'state':'FROZEN_BLIND_RULE_SELECTED',
          'cases':chosen,'hypotheses_sha256':sha(dest/'discovery'/'hypotheses.json'),
          'no_gold_target_selection':True,'g3_unlocked':False})
    answers={r['case_id']:r for split in ('discovery','confirmation')
             for r in jsonl(dest/'seal'/f'answers.{split}.SECRET.jsonl')}
    prompts={r['case_id']:r for r in jsonl(dest/'seal'/'prompts.jsonl')}
    detail=[]
    if chosen:
        original,_=hard._verify_trained(paths,paths['parent_0087'])
        network,tok=backbone.model_components(original['model_id'],paths['adapter'],device)
        require(all(not p.requires_grad for p in network.parameters()),'PROBE_BACKBONE_TRAINABLE')
        composite=g1native.load_composite(paths['composite'],device=device)
        head,_=cur._load_checkpoint(paths['parent_0092']/'B_from_0090'/'stage-4'/'checkpoint',device=device)
        head.eval().requires_grad_(False)
        for idx,item in enumerate(chosen,1):
            result=_probe_case(head,network,tok,composite,prompts[item['case_id']],
                               answers[item['case_id']],item,device)
            detail.append(item|result)
            if idx%10==0 or idx==len(chosen):
                print(f'G2_0093_CAUSAL {idx}/{len(chosen)} frozen_head',flush=True)
    area=dest/'causal';area.mkdir(exist_ok=False)
    jsonl_write(area/'probes.jsonl',detail)
    by=defaultdict(Counter)
    for r in detail:
        for entry in r['outcomes']:
            k=(r['split'],r['rule'],entry['intervention'])
            by[k]['tests']+=1
            by[k]['decision_changed']+=int(entry['decision_changed'])
            by[k]['correct_before']+=int(entry['correct_before'])
            by[k]['correct_after']+=int(entry['correct_after'])
            by[k]['true_positive_damaged']+=int(entry['correct_before'] and
                  entry['gold_label'] and not entry['correct_after'])
            by[k]['false_positive_corrected']+=int(not entry['correct_before'] and
                  not entry['gold_label'] and entry['correct_after'])
    report={'schema':SCHEMA,'status':'OBSERVATIONAL_AND_INTERVENTIONAL_DIAGNOSIS_ONLY',
        'probe_cases':len(detail),'selection_sha256':sha(selection),
        'counts':{'|'.join(k):dict(v) for k,v in by.items()},
        'repaired_mechanism_proven':False,
        'reason':'Zeroing activations is destructive and cannot identify a selective repair by itself',
        'g3_unlocked':False}
    write(area/'summary.json',report)
    write(area/'lock.json',{'schema':SCHEMA,'summary_sha256':sha(area/'summary.json'),
        'probes_sha256':sha(area/'probes.jsonl'),
        'selection_sha256':sha(selection),'g3_unlocked':False})
    final={'schema':SCHEMA,'status':'G2_0093_FROZEN_LARGE_SCALE_DIAGNOSIS_ONLY',
        'base_graphs':commit['bases'],'evaluated_inputs':commit['inputs'],
        'discovery':read(dest/'discovery'/'summary.json'),
        'confirmation':read(dest/'confirmation'/'summary.json'),
        'causal':report,'g1_frozen':True,'g2_frozen':True,
        'generator_independent_distribution':False,
        'g2_certified':False,'g3_unlocked':False,
        'integrity':{'seal_commit_sha256':sha(dest/'seal'/'commit.json'),
           'blind_index_sha256':sha(dest/'blind'/'index.json'),
           'discovery_lock_sha256':sha(dest/'discovery'/'lock.json'),
           'confirmation_lock_sha256':sha(dest/'confirmation'/'lock.json'),
           'causal_lock_sha256':sha(area/'lock.json')}}
    write(dest/'report.json',final)
    print('G2_0093_RESULT '+json.dumps({'status':final['status'],
            'bases':commit['bases'],'inputs':commit['inputs'],
            'selected_error_classes':len(hypothesis['rules']),
            'probes':len(detail),'g3_unlocked':False},sort_keys=True),flush=True)
    return final


def run(*,root=DEFAULT_ROOT,output=None,device='cuda',bases=DEFAULT_BASES,seed=SEED):
    dest=(Path(output) if output else Path(root)/OUTPUT_NAME).resolve()
    prepare(root=root,output=dest,bases=bases,seed=seed)
    for phase in ('predict','discover','confirm','probe'):
        args=[sys.executable,'-u','-m',MODULE,phase,
              '--root',str(Path(root).resolve()),'--output',str(dest)]
        if phase in ('predict','probe'):args+=['--device',device]
        subprocess.run(args,check=True)
    return read(dest/'report.json')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for cmd in ('run','prepare','predict','discover','confirm','probe'):
        q=sub.add_parser(cmd);q.add_argument('--root',default=DEFAULT_ROOT)
        q.add_argument('--output',default=None)
        if cmd in ('run','prepare'):
            q.add_argument('--bases',type=int,default=DEFAULT_BASES)
            q.add_argument('--seed',type=int,default=SEED)
        if cmd in ('run','predict','probe'):q.add_argument('--device',default='cuda')
    args=vars(p.parse_args(argv));cmd=args.pop('command')
    return {'run':run,'prepare':prepare,'predict':predict,
            'discover':discover,'confirm':confirm,'probe':probe}[cmd](**args)

if __name__=='__main__':main()
