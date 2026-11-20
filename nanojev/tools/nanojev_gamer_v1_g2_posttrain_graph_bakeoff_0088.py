"""G2-0088: frozen G2-0087 error attribution, decoder bakeoff, fuzz and sealed audit.

Run from nanojev: python -m tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 run --device cuda

Prior 0087 development is calibration and prior challenge is model selection;
neither is new certification evidence. The selected algorithm is frozen *before*
fresh gold-blind inference and is never refit after answers are opened.
No optimizer, head mutation, threshold change in certified native sources, or G3 unlock.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import torch

from tools import nanojev_gamer_v1_g2_hard_negative_learning_0087 as parent
from tools import nanojev_gamer_v1_g2_native_training_0083 as previous
from tools import nanojev_gamer_v1_g2_causal_activation_fuzz_0085 as fuzz
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_backbone_first as backbone
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES,decode_graph

SCHEMA='nanojev.gamer.graph.g2.posttrain-graph-bakeoff-0088.v1'
MODULE='tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088'
SEED=880088
DEV=96
CHALLENGE=64
FUZZ_PAIRS=8
GRID=(-1.,0.,.5,1.,2.,3.,4.,6.,8.,12.)
METHODS=('native','threshold','source_topk','type_aware','joint')
LIMITS={'max_nodes':128,'max_pairs':128*127,'max_candidates':128*127}


def require(test,code):
    if not test:raise ValueError('G2_0088_'+code)


def sha(path):return parent.sha(path)
def js(path):return parent.js(path)
def write_json(path,data):return parent.write_json(path,data)
def read_jsonl(path):return parent.read_jsonl(path)
def write_jsonl(path,rows):return parent.write_jsonl(path,rows)


def paths_for(root):
    paths=parent.paths_for(root)
    paths['parent_0087']=Path(root)/'oracle-fabric-g2-hard-negative-learning-0087'
    return paths


def ensure_parent(paths):
    """Strict lineage and evaluated-evidence checks. No new training."""
    source=paths['parent_0087']
    manifest,_=parent._verify_trained(paths,source)
    seal=parent.check_seal(paths,source)
    lock=js(source/'blind'/'lock.json')
    require(lock.get('schema')==parent.SCHEMA and
            lock.get('status')=='BLIND_PREDICTIONS_LOCKED_UNSCORED' and
            lock.get('predictions_sha256')==sha(source/'blind'/'predictions.jsonl') and
            lock.get('trained_head_sha256')==manifest['head_sha256'] and
            lock.get('prompts_sha256')==seal['prompts_sha256'],
            'PARENT_BLIND_CHECKSUM_DRIFT')
    report=js(source/'scored'/'report.json')
    require(report.get('schema')==parent.SCHEMA and
            report.get('status')=='G2_0087_GATE_BLOCKED' and
            report.get('g3_training_unlocked') is False and
            report.get('splits',{}).get('dev',{}).get('cases')==parent.DEV and
            report.get('splits',{}).get('challenge',{}).get('cases')==parent.CHALLENGE,
            'PARENT_SCORE_MISSING_OR_CHANGED')
    rows=read_jsonl(source/'blind'/'predictions.jsonl')
    answers=read_jsonl(source/'seal'/'answers.SECRET.jsonl')
    prompts=read_jsonl(source/'seal'/'prompts.jsonl')
    require(len(rows)==len(answers)==len(prompts)==parent.DEV+parent.CHALLENGE,
            'PARENT_CASE_COUNT')
    indexed={r['case_id']:r for r in answers}
    qindex={r['case_id']:r for r in prompts}
    require(len(indexed)==len(qindex)==len(rows),'PARENT_DUPLICATE_CASES')
    for r in rows:
        aid=r['case_id']
        require(aid in indexed and aid in qindex and
                all(r[k]==indexed[aid][k]==qindex[aid][k]
                    for k in ('split','family','prompt_sha256')) and
                hashlib.sha256(qindex[aid]['prompt'].encode()).hexdigest()==r['prompt_sha256'],
                'PARENT_CASE_BINDING')
        require(set(r['modes'])=={'trained','control','untrained','no_binding','zero_hidden'},
                'PARENT_ARM_SET')
        validate_surface(r['modes']['trained'])
        validate_surface(r['modes']['control'])
    # Recompute the exposed control metrics; don't blindly trust saved report.
    for split in ('dev','challenge'):
        subset=[(r,indexed[r['case_id']]) for r in rows if r['split']==split]
        require(len(subset)==(parent.DEV if split=='dev' else parent.CHALLENGE),
                'PARENT_SPLIT_COUNT')
        for mode in ('trained','control'):
            got=score_entries([(r['modes'][mode],a['graph']) for r,a in subset],
                              use_stored=True)
            expected=report['splits'][split]['metrics'][mode]
            require(got['exact']==expected['exact'] and
                    abs(got['edge_f1']-expected['edge_f1'])<1e-9,
                    'PARENT_METRIC_MISMATCH_'+split+'_'+mode)
    return manifest,rows,indexed


def validate_surface(record):
    require(isinstance(record,dict) and
            {'g1_objects','pair_margins','binding_edges'}<=set(record),
            'MISSING_PAIR_SURFACE')
    objects=record['g1_objects']; n=len(objects)
    require(n<=LIMITS['max_nodes'] and len(objects)==len({tuple(x) for x in objects}) and
            all(isinstance(x,list) and len(x)==3 and
                type(x[0]) is int and type(x[1]) is int and x[0]<x[1] and
                isinstance(x[2],str) for x in objects), 'G1_ANCHOR_SHAPE')
    pairs={}
    require(len(record['pair_margins'])==n*(n-1),'PAIR_COUNT')
    for item in record['pair_margins']:
        require(isinstance(item,list) and len(item)==3,'PAIR_FORMAT')
        i,j,m=item
        require(type(i) is int and type(j) is int and i!=j and
                0<=i<n and 0<=j<n and (i,j) not in pairs and
                isinstance(m,list) and len(m)==len(BIND_CLASSES)-1 and
                all(type(z) in (int,float) and math.isfinite(z) and abs(z)<1e7
                    for z in m),'PAIR_SCORE_CORRUPT')
        pairs[(i,j)]=m
    require(len(pairs)==n*(n-1),'PAIR_MATRIX_INCOMPLETE')
    return objects,pairs


def format_edge(objs,i,j,label):
    return (objs[i][2],(objs[i][0],objs[i][1]),
            objs[j][2],(objs[j][0],objs[j][1]),BIND_CLASSES[label])


def decode(record,spec):
    """Gold-blind edge assembly. One type per ordered pair, no self edges.

    Joint method is *greedy capacity-constrained matching*, not an exact
    optimizer. Its capacities are trained only from exposed calibration gold.
    """
    require(spec.get('method') in METHODS,'UNKNOWN_DECODER')
    objs,pairs=validate_surface(record)
    if spec['method']=='native':
        return previous._keyed_edges(record['binding_edges'])
    threshold=float(spec.get('threshold',0.0))
    require(math.isfinite(threshold) and -50<=threshold<=50,'BAD_THRESHOLD')
    candidates=[]
    kind_threshold=spec.get('kind_thresholds',{})
    for (i,j),margins in pairs.items():
        t=max(range(len(margins)),key=lambda k:(margins[k],-k))+1
        typ=BIND_CLASSES[t]
        src_kind=objs[i][2];dst_kind=objs[j][2]
        key='|'.join((src_kind,dst_kind,typ))
        cut=float(kind_threshold.get(key,threshold))
        strength=float(margins[t-1])
        if strength <= cut:continue
        # Full scores are input from the frozen G2 head; no oracle-based rank.
        candidates.append((strength-cut,i,j,t,key))
    candidates.sort(key=lambda x:(-x[0],x[1],x[2],x[3]))
    chosen=[];used_pair=set();out_count=Counter();in_count=Counter()
    for gain,i,j,t,key in candidates:
        kind=objs[j][2]
        src_key=(i,kind,t)
        dst_key=(j,objs[i][2],t)
        if (i,j) in used_pair:continue
        if spec['method'] in ('source_topk','joint'):
            maximum=int(spec.get('k',2)) if spec['method']=='source_topk' else int(
                spec.get('out_caps',{}).get(key,2))
            if out_count[src_key]>=maximum:continue
        if spec['method']=='joint':
            incoming='|'.join((objs[j][2],objs[i][2],BIND_CLASSES[t]))
            cap=int(spec.get('in_caps',{}).get(incoming,2))
            if in_count[dst_key]>=cap:continue
        chosen.append(format_edge(objs,i,j,t))
        used_pair.add((i,j));out_count[src_key]+=1;in_count[dst_key]+=1
    return set(chosen)


def truth(graph):return previous._gold_edges(graph)


def score_entries(entries,*,use_stored=False,spec=None):
    count=Counter()
    for r,gold_graph in entries:
        predicted=previous._keyed_edges(r['binding_edges']) if use_stored else decode(r,spec)
        expected=truth(gold_graph)
        count['cases']+=1;count['exact']+=int(predicted==expected)
        count['tp']+=len(predicted&expected);count['pred']+=len(predicted)
        count['gold']+=len(expected)
    p=count['tp']/count['pred'] if count['pred'] else 0.
    r=count['tp']/count['gold'] if count['gold'] else 0.
    return {'cases':count['cases'],'exact':count['exact'],
            'exact_rate':count['exact']/count['cases'] if count['cases'] else 0,
            'tp':count['tp'],'pred':count['pred'],'gold':count['gold'],
            'fp':count['pred']-count['tp'],'fn':count['gold']-count['tp'],
            'precision':p,'recall':r,'edge_f1':2*p*r/(p+r) if p+r else 0.}


def diagnose(entries):
    """Measure reachable gold, wrong-label, missing/extra and top-k ceilings."""
    counts=Counter();coverage=defaultdict(Counter);by_type=defaultdict(Counter)
    for record,graph in entries:
        objs,pairs=validate_surface(record)
        expected=truth(graph)
        native=previous._keyed_edges(record['binding_edges'])
        lookup={(obj[2],(obj[0],obj[1])):i for i,obj in enumerate(objs)}
        reachable=set()
        grouped_gold=defaultdict(set)
        observed_gold_pairs={}
        for edge in expected:
            a=(edge[0],edge[1]);b=(edge[2],edge[3]);name=edge[4]
            if a in lookup and b in lookup:
                reachable.add(edge)
                i,j=lookup[a],lookup[b]
                grouped_gold[(i,objs[j][2],name)].add(j)
                observed_gold_pairs[(i,j)]=name
        counts['cases']+=1
        counts['g1_exact']+=int({(x[2],(x[0],x[1])) for x in objs}==
            {(n['kind'],tuple(n['span'])) for n in graph['nodes'] if 'span' in n})
        counts['gold_edges']+=len(expected)
        counts['unreachable']+=len(expected-reachable)
        counts['missed_reachable']+=len(reachable-native)
        counts['false_edges']+=len(native-expected)
        counts['wrong_label']+=sum((x[0],x[1],x[2],x[3]) in
            {(e[0],e[1],e[2],e[3]) for e in expected} for x in native-expected)
        counts['oracle_all_gold_reachable']+=int(len(expected)==len(reachable))
        for typ in set(e[4] for e in expected|native):
            gold={e for e in expected if e[4]==typ}
            pred={e for e in native if e[4]==typ}
            t=by_type[typ]
            t['gold']+=len(gold);t['tp']+=len(gold&pred);t['fp']+=len(pred-gold)
        for k in (1,2,3,5,10,999):
            possible=set()
            for (i,kind,name),gold_dest in grouped_gold.items():
                t=BIND_CLASSES.index(name)-1
                rivals=[j for j in range(len(objs)) if i!=j and objs[j][2]==kind]
                # Candidate universe uses the inference-time winning label,
                # never an oracle-selected type on a gold pair.
                rivals=[j for j in rivals if max(range(len(BIND_CLASSES)-1),
                    key=lambda x:(pairs[(i,j)][x],-x))==t]
                ordered=sorted(rivals,key=lambda j:(-pairs[(i,j)][t],j))[:k]
                possible.update(format_edge(objs,i,j,t+1) for j in ordered)
            matched=reachable&possible
            coverage[k]['tp']+=len(matched)
            coverage[k]['reachable']+=len(reachable)
            coverage[k]['oracle_exact']+=int(possible>=expected)
        # Missing gold classes are excluded from the observed candidate universe.
    return {'counts':dict(counts),'by_type':{k:dict(v) for k,v in by_type.items()},
        'top_k_oracle_ceiling':{str(k):dict(v,coverage_rate=v['tp']/max(v['reachable'],1),
            exact_upper_bound=v['oracle_exact']/max(counts['cases'],1)) for k,v in coverage.items()},
        'interpretation':'Top-k uses gold only for post hoc oracle coverage; not a deployable decoder.'}


def fit_caps(entries):
    """Conservative structural capacity priors learned from calibration only."""
    out=Counter();incoming=Counter();margin_by_kind=defaultdict(list)
    for record,graph in entries:
        objs,pairs=validate_surface(record)
        lookup={(x[2],(x[0],x[1])):i for i,x in enumerate(objs)}
        gold=truth(graph)
        src_group=Counter();dst_group=Counter()
        gold_index=set()
        for a,sp,b,dp,typ in gold:
            if (a,sp) not in lookup or (b,dp) not in lookup:continue
            i=lookup[(a,sp)];j=lookup[(b,dp)]
            gold_index.add((i,j,typ))
            src_group[(i,'|'.join((a,b,typ)))]+=1
            dst_group[(j,'|'.join((b,a,typ)))]+=1
        for (_,key),n in src_group.items():out[key]=max(out[key],n)
        for (_,key),n in dst_group.items():incoming[key]=max(incoming[key],n)
        for (i,j),scores in pairs.items():
            t=max(range(len(scores)),key=lambda k:(scores[k],-k))+1
            key='|'.join((objs[i][2],objs[j][2],BIND_CLASSES[t]))
            margin_by_kind[key].append((scores[t-1],int((i,j,BIND_CLASSES[t]) in gold_index)))
    # +1 tolerance avoids asserting every object has a single relation.
    caps={'out_caps':{k:min(128,v+1) for k,v in out.items()},
          'in_caps':{k:min(128,v+1) for k,v in incoming.items()}}
    return caps,margin_by_kind


def _kind_thresholds(by_kind,base):
    chosen={}
    for key,rows in by_kind.items():
        if sum(y for _,y in rows)<8 or len(rows)<24:continue
        # Independently tune same-kind threshold on exposed calibration only.
        best=None
        for t in GRID:
            tp=sum(y and x>t for x,y in rows)
            pred=sum(x>t for x,y in rows)
            gold=sum(y for _,y in rows)
            f1=2*tp/(gold+pred) if gold+pred else 0
            order=(f1,-abs(t-base),-abs(t))
            if best is None or order>best[0]:best=(order,t)
        chosen[key]=best[1]
    return chosen


def _metric_key(m):
    # Original exact-graph metric is primary. F1 only breaks exact ties.
    return (m['exact'],round(m['edge_f1'],10),round(m['precision'],10))


def bakeoff(cal,selection):
    require(len(cal)==parent.DEV and len(selection)==parent.CHALLENGE,
            'EXPOSED_COHORTS_NOT_PARTITIONED')
    caps,kind_examples=fit_caps(cal)
    specs=[{'method':'native'}]
    for threshold in GRID:
        specs.append({'method':'threshold','threshold':threshold})
        for k in (1,2,3):
            specs.append({'method':'source_topk','threshold':threshold,'k':k})
        specs.append({'method':'joint','threshold':threshold,**deepcopy(caps)})
        specs.append({'method':'type_aware','threshold':threshold,
                      'kind_thresholds':_kind_thresholds(kind_examples,threshold)})
    # Keep only best calibration spec per family, then select family on held-out
    # exposed challenge. This is tuning/selection, not prospective validation.
    best={};table=[]
    for index,spec in enumerate(specs):
        m=score_entries(cal,spec=spec)
        method=spec['method'];candidate=(_metric_key(m),-index)
        if method not in best or candidate>best[method][0]:
            best[method]=(candidate,spec,m)
    for method in METHODS:
        _,spec,calmetric=best[method]
        selectmetric=score_entries(selection,spec=spec)
        table.append({'method':method,'spec':spec,'calibration':calmetric,
                      'selection':selectmetric})
    order={m:i for i,m in enumerate(METHODS)}
    winner=max(table,key=lambda x:(_metric_key(x['selection']),
                                -order[x['method']]))
    return {'winner':deepcopy(winner['spec']),'winner_method':winner['method'],
            'arms':table,'calibration_cases':len(cal),
            'selection_cases':len(selection),'candidate_count':len(specs),
            'selection_policy':'max(exact_graphs,edge_f1,precision); tie=predeclared_order',
            'interpretation':'Previously scored G2-0087 examples only; not independent acceptance.'}


def fresh_rows(paths):
    """Generate test split once and exclude all already exposed prompts."""
    exclude=parent.excluded_prompts(paths)
    old=read_jsonl(paths['parent_0087']/'seal'/'prompts.jsonl')
    require(len(old)==parent.DEV+parent.CHALLENGE,'PREVIOUS_0087_EXCLUSION')
    exclude.update(r['prompt_sha256'] for r in old)
    rng=random.Random(SEED); serial=88_000_000;rows=[]
    for split,size in (('dev',DEV),('challenge',CHALLENGE)):
        for i in range(size):
            factory=corpus.encounter if i%2==0 else corpus.allocation
            for _ in range(4096):
                item=factory(rng,'test',serial);serial+=1
                digest=hashlib.sha256(item['prompt'].encode()).hexdigest()
                if digest not in exclude:break
            else:raise ValueError('G2_0088_FRESH_EXHAUSTED')
            exclude.add(digest)
            rows.append({'case_id':f'g2-0088-{split}-{i+1:04}',
                         'split':split,'family':item['family'],
                         'prompt':item['prompt'],'prompt_sha256':digest,
                         'graph':item['graph'],'gold_objects':item['object_spans']})
    return rows,exclude


def make_seal(paths,output,manifest,bakeoff_result):
    rows,seen=fresh_rows(paths)
    # Historical development and challenge must be evaluated too, but never
    # represented as new independent evidence.
    historical=[r for r in cur.load_rows(paths['dataset']) if r['split'] in ('dev','challenge')]
    require(len([r for r in historical if r['split']=='dev'])==80 and
            len([r for r in historical if r['split']=='challenge'])==32,
            'HISTORICAL_COUNTS')
    for i,r in enumerate(historical):
        digest=hashlib.sha256(r['prompt'].encode()).hexdigest()
        rows.append({'case_id':f'g2-0088-historical-{i:04}',
                     'split':'historical_'+r['split'],'family':r['family'],
                     'prompt':r['prompt'],'prompt_sha256':digest,
                     'graph':r['graph'],'gold_objects':r['object_spans']})
    fuzz_rows=fuzz.make_pairs(seed=SEED,pairs=FUZZ_PAIRS)
    for pair in fuzz_rows:
        for side in ('base','fuzz'):
            item=pair[side]
            digest=hashlib.sha256(item['prompt'].encode()).hexdigest()
            require(digest not in seen,'FUZZ_OVERLAP_WITH_PREVIOUS_EXPERIMENT')
            seen.add(digest)
            rows.append({'case_id':f'g2-0088-fuzz-{pair["id"]}-{side}',
                         'split':'fuzz','family':item['family'],
                         'prompt':item['prompt'],'prompt_sha256':digest,
                         'graph':item['graph'],'gold_objects':item['object_spans'],
                         'fuzz_pair':pair['id'],'fuzz_mode':pair['mode'],
                         'fuzz_side':side})
    require(len(rows)==DEV+CHALLENGE+112+FUZZ_PAIRS*2,'TOTAL_CASE_COUNT')
    seal=output/'seal';seal.mkdir(exist_ok=False)
    prompt_fields=('case_id','split','family','prompt','prompt_sha256',
                   'fuzz_pair','fuzz_mode','fuzz_side')
    answer_fields=('case_id','split','family','prompt_sha256','graph','gold_objects',
                   'fuzz_pair','fuzz_mode','fuzz_side')
    write_jsonl(seal/'prompts.jsonl',[{k:r[k] for k in prompt_fields if k in r} for r in rows])
    write_jsonl(seal/'answers.SECRET.jsonl',[{k:r[k] for k in answer_fields if k in r} for r in rows])
    commitment={'schema':SCHEMA,'status':'FROZEN_SELECTION_AND_GOLD_COMMITTED_NOT_PREDICTED',
         'seed':SEED,'fresh_dev':DEV,'fresh_challenge':CHALLENGE,
         'historical':112,'fuzz':FUZZ_PAIRS*2,
         'prompts_sha256':sha(seal/'prompts.jsonl'),
         'answers_sha256':sha(seal/'answers.SECRET.jsonl'),
         'selection_sha256':sha(output/'selection.json'),
         'parent_head_sha256':manifest['head_sha256'],
         'parent_blind_sha256':sha(paths['parent_0087']/'blind'/'predictions.jsonl'),
         'parent_scored_sha256':sha(paths['parent_0087']/'scored'/'report.json'),
         'runner_sha256':sha(__file__),'gold_opened_during_predict':False,
         'G3_training_unlocked':False,'promotion':False}
    write_json(seal/'commit.json',commitment)
    return commitment


def check_seal(paths,dest,*,verify_answers=True):
    record=js(dest/'seal'/'commit.json')
    require(record.get('schema')==SCHEMA and
            record.get('status')=='FROZEN_SELECTION_AND_GOLD_COMMITTED_NOT_PREDICTED' and
            (record.get('fresh_dev'),record.get('fresh_challenge'),
             record.get('historical'),record.get('fuzz'))==
            (DEV,CHALLENGE,112,FUZZ_PAIRS*2) and
            record.get('G3_training_unlocked') is False and
            record.get('promotion') is False and
            record.get('runner_sha256')==sha(__file__), 'SEAL_DESIGN_DRIFT')
    files=[('seal/prompts.jsonl','prompts_sha256'),
           ('selection.json','selection_sha256')]
    if verify_answers:files.append(('seal/answers.SECRET.jsonl','answers_sha256'))
    for file,key in files:
        require(sha(dest/file)==record[key],'SEALED_FILE_DRIFT_'+key)
    require(sha(paths['parent_0087']/'blind'/'predictions.jsonl')==
            record['parent_blind_sha256'] and
            sha(paths['parent_0087']/'scored'/'report.json')==
            record['parent_scored_sha256'] and
            sha(paths['parent_0087']/'training'/cur.HEAD_FILE)==
            record['parent_head_sha256'],'PARENT_LINEAGE_CHANGED')
    return record


def prepare(*,root,output):
    paths=paths_for(root);dest=Path(output).resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
    manifest,rows,indexed=ensure_parent(paths)
    c=[(r['modes']['trained'],indexed[r['case_id']]['graph']) for r in rows if r['split']=='dev']
    s=[(r['modes']['trained'],indexed[r['case_id']]['graph']) for r in rows if r['split']=='challenge']
    diag={'calibration':diagnose(c),'selection':diagnose(s)}
    selected=bakeoff(c,s)
    dest.mkdir(exist_ok=False)
    write_json(dest/'selection.json',{'schema':SCHEMA,'status':'DECODER_FROZEN_BEFORE_NEW_INFERENCE',
         'parent_sha256':manifest['head_sha256'],**selected})
    write_json(dest/'diagnostics.json',diag)
    record=make_seal(paths,dest,manifest,selected)
    print('G2_0088_PREPARED '+json.dumps({'calibration':len(c),'selection':len(s),
          'winner':selected['winner_method'],'candidate_decoders':selected['candidate_count'],
          'fresh':DEV+CHALLENGE,'historical':112,'fuzz':16,
          'commit':record['answers_sha256']},sort_keys=True),flush=True)
    return record


def _infer(net,hidden,eligible,spans,prepared,spec,*,with_margins=True):
    with torch.inference_mode():
        out=net(hidden,eligible=eligible,inference_spans=spans,active_stage='G2')
        graph,surfaces=decode_graph(out,prepared,unlocked='G2')
    require(len(surfaces)==len(spans),'NATIVE_G1_SPANS_CHANGED')
    rec={'g1_objects':sorted([[n['span'][0],n['span'][1],n['kind']]
           for n in graph['nodes'] if 'span' in n]),
         'binding_edges':previous._safe_edges(previous._edge_sets(graph))}
    # The ordering must match the model's original span ordering.
    if with_margins:
        rec['pair_margins']=parent._raw_pair_margins(out,spans)
        anchors=[[x['span'][0],x['span'][1],x['kind']]
                 for x in graph['nodes'] if 'span' in x]
        require(anchors==rec['g1_objects'],'MODEL_NODE_ORDER_NOT_SORTED')
        validate_surface(rec)
    return rec


def predict(*,root,output,device='cuda'):
    paths=paths_for(root);dest=Path(output).resolve()
    commit=check_seal(paths,dest,verify_answers=False)
    require(not (dest/'blind').exists(),'BLIND_ALREADY_EXISTS')
    manifest,_=parent._verify_trained(paths,paths['parent_0087'])
    require(manifest['head_sha256']==commit['parent_head_sha256'],'CHECKPOINT_CHANGE')
    selection=js(dest/'selection.json');spec=selection['winner']
    require(selection['status']=='DECODER_FROZEN_BEFORE_NEW_INFERENCE',
            'SELECTION_NOT_FROZEN')
    # NO access to answers, graph gold, old evaluation labels in this process.
    network,tok=backbone.model_components(manifest['model_id'],paths['adapter'],device)
    require(not any(p.requires_grad for p in network.parameters()),'BACKBONE_UNFROZEN')
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(paths['parent_0087']/'training',device=device)
    baseline,_=cur._load_checkpoint(paths['prior']/'training',device=device)
    untrained,_=cur._load_checkpoint(paths['g1'],device=device)
    for net in (head,baseline,untrained):net.eval().requires_grad_(False)
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE+112+FUZZ_PAIRS*2 and
            len({r['case_id'] for r in prompts})==len(prompts),'PROMPT_BIJECTION')
    results=[]
    for idx,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==
                row['prompt_sha256'],'PROMPT_CHANGED')
        prepared,h=previous._capture_inference(network,tok,row['prompt'],device,2048)
        eligible=prepared['eligible'].to(device)
        g1=composite.predict(h,prepared,family=row['family'],native_stage='G1')
        spans=g1['token_spans']
        trained=_infer(head,h,eligible,spans,prepared,spec)
        original=_infer(baseline,h,eligible,spans,prepared,spec,with_margins=False)
        never=_infer(untrained,h,eligible,spans,prepared,spec,with_margins=False)
        zero_g1=composite.predict(h,prepared,family=row['family'],
                                  mode='zero_hidden',native_stage='G1')
        zero=_infer(head,torch.zeros_like(h),eligible,zero_g1['token_spans'],prepared,spec)
        # No-binding ablation uses the same predicted G1 nodes and zero edges.
        selected=previous._safe_edges({((e[0],e[1]),(e[2],e[3]),e[4])
                 for e in decode(trained,spec)})
        results.append({k:v for k,v in row.items() if k!='prompt'}|{
              'modes':{'selected':{'g1_objects':trained['g1_objects'],'binding_edges':selected},
                       'native':{'g1_objects':trained['g1_objects'],
                                 'binding_edges':trained['binding_edges']},
                       'control':original,'untrained':never,
                       'no_binding':{'g1_objects':trained['g1_objects'],'binding_edges':[]},
                       'zero_hidden':{'g1_objects':zero['g1_objects'],
                                      'binding_edges':previous._safe_edges({
                                          ((e[0],e[1]),(e[2],e[3]),e[4])
                                          for e in decode(zero,spec)})}},
              'raw_surface':trained})
        if idx==1 or idx%20==0 or idx==len(prompts):
            print(f'G2_0088_BLIND {idx}/{len(prompts)} frozen_G2_0087',flush=True)
    blind=dest/'blind';blind.mkdir(exist_ok=False)
    write_jsonl(blind/'predictions.jsonl',results)
    lock={'schema':SCHEMA,'status':'BLIND_LOCKED_NOT_SCORED',
          'cases':len(results),'predictions_sha256':sha(blind/'predictions.jsonl'),
          'prompts_sha256':commit['prompts_sha256'],
          'selection_sha256':commit['selection_sha256'],
          'checkpoint_sha256':manifest['head_sha256'],
          'gold_read_during_prediction':False,'no_training':True,
          'g3_training_unlocked':False}
    write_json(blind/'lock.json',lock)
    print('G2_0088_BLIND_LOCKED '+json.dumps({'cases':len(results),
          'prediction_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def _edge_modes(row,answer):
    result={}
    observed=tuple(map(tuple,row['modes']['selected']['g1_objects']))
    for mode,record in row['modes'].items():
        if mode!='zero_hidden':require(tuple(map(tuple,record['g1_objects']))==observed,
                                      'G1_DIFFERS_ACROSS_HEADS')
        edges=previous._keyed_edges(record['binding_edges'])
        anchors={(x[2],(x[0],x[1])) for x in record['g1_objects']}
        require(all((e[0],e[1]) in anchors and (e[2],e[3]) in anchors
                    for e in edges),'UNANCHORED_PREDICTED_EDGE')
        result[mode]=edges
    return result


def _fuzz_score(rows,answers):
    pairs=defaultdict(dict)
    for r in rows:
        if r['split']=='fuzz':pairs[r['fuzz_pair']][r['fuzz_side']]=r
    require(len(pairs)==FUZZ_PAIRS and all(set(v)=={'base','fuzz'} for v in pairs.values()),
            'FUZZ_PAIR_COMPLETENESS')
    output=Counter();details=[]
    for ident,sides in sorted(pairs.items()):
        base=sides['base'];changed=sides['fuzz']
        mode=base['fuzz_mode']
        require(changed['fuzz_mode']==mode,'FUZZ_MODE_CHANGED')
        pred=[];g1=[]
        for row in (base,changed):
            gold=answers[row['case_id']]['graph']
            nodes={n['id']:(n['kind'],tuple(n['span'])) for n in gold['nodes'] if 'span' in n}
            inverse={v:k for k,v in nodes.items()}
            observed={ (x[2],(x[0],x[1])) for x in row['modes']['selected']['g1_objects']}
            g1.append(observed==set(inverse))
            actual=previous._keyed_edges(row['modes']['selected']['binding_edges'])
            normalized=set()
            for a,sa,b,sb,typ in actual:
                if (a,sa) not in inverse or (b,sb) not in inverse:
                    normalized.add(('UNMATCHED',a,str(sa),b,str(sb),typ))
                else:normalized.add((inverse[a,sa],inverse[b,sb],typ))
            pred.append(normalized)
        invariant=(mode!='action_removal')
        output['pairs']+=1
        output['both_g1_exact']+=int(all(g1))
        if invariant:
            output['expected_invariant']+=1
            output['predicted_invariant']+=int(pred[0]==pred[1])
            output['invariant_with_exact_g1']+=int(all(g1))
            output['preserved_with_exact_g1']+=int(all(g1) and pred[0]==pred[1])
        details.append({'pair_id':ident,'mode':mode,'base_g1_exact':g1[0],
              'fuzz_g1_exact':g1[1],'expected_invariant':invariant,
              'predicted_invariant':pred[0]==pred[1],
              'symmetric_difference':len(pred[0]^pred[1])})
    return {'totals':dict(output),'pairs':details}


def score(*,root,output):
    paths=paths_for(root);dest=Path(output).resolve();commit=check_seal(paths,dest)
    require(not (dest/'scored').exists(),'ALREADY_SCORED')
    lock=js(dest/'blind'/'lock.json')
    require(lock.get('schema')==SCHEMA and lock.get('status')=='BLIND_LOCKED_NOT_SCORED' and
            lock.get('cases')==DEV+CHALLENGE+112+FUZZ_PAIRS*2 and
            lock.get('predictions_sha256')==sha(dest/'blind'/'predictions.jsonl') and
            lock.get('prompts_sha256')==commit['prompts_sha256'] and
            lock.get('selection_sha256')==commit['selection_sha256'] and
            lock.get('checkpoint_sha256')==commit['parent_head_sha256'] and
            lock.get('gold_read_during_prediction') is False,'BLIND_INTEGRITY_FAILED')
    predicted=read_jsonl(dest/'blind'/'predictions.jsonl')
    answer_rows=read_jsonl(dest/'seal'/'answers.SECRET.jsonl')
    prompt_rows=read_jsonl(dest/'seal'/'prompts.jsonl')
    answers={r['case_id']:r for r in answer_rows}
    prompts={r['case_id']:r for r in prompt_rows}
    require(len(predicted)==len(answers)==len(prompts)==DEV+CHALLENGE+112+FUZZ_PAIRS*2 and
            len({r['case_id'] for r in predicted})==len(predicted) and
            len(answers)==len(answer_rows) and len(prompts)==len(prompt_rows) and
            {r['case_id'] for r in predicted}==set(answers)==set(prompts),
            'BLIND_GOLD_CASE_BIJECTION')
    metrics={};failures=[];case_log=[]
    modes=('selected','native','control','untrained','zero_hidden','no_binding')
    for split,size in (('historical_dev',80),('historical_challenge',32),
                       ('dev',DEV),('challenge',CHALLENGE)):
        subset=[r for r in predicted if r['split']==split]
        require(len(subset)==size,'SPLIT_SIZE_'+split)
        sums={mode:Counter() for mode in modes}
        reachable=Counter();family=Counter();g1=0
        for row in subset:
            ident=row['case_id'];gold=answers[ident]
            require(all(row[k]==gold[k]==prompts[ident][k]
                        for k in ('split','family','prompt_sha256')) and
                    hashlib.sha256(prompts[ident]['prompt'].encode()).hexdigest()==
                       gold['prompt_sha256'],'SCORE_PROMPT_BINDING')
            pred=_edge_modes(row,gold)
            # Independently replay the frozen spec against blind raw scores.
            spec=js(dest/'selection.json')['winner']
            require(pred['selected']==decode(row['raw_surface'],spec) and
                    pred['native']==previous._keyed_edges(row['raw_surface']['binding_edges']),
                    'BLIND_DECODE_REPLAY_DRIFT')
            truthset=truth(gold['graph']);family[row['family']]+=1
            anchor={(x[2],(x[0],x[1])) for x in row['modes']['selected']['g1_objects']}
            expected_obj={(n['kind'],tuple(n['span'])) for n in gold['graph']['nodes'] if 'span' in n}
            g1+=int(anchor==expected_obj)
            recoverable={e for e in truthset if (e[0],e[1]) in anchor and (e[2],e[3]) in anchor}
            reachable.update({'gold':len(truthset),'reachable':len(recoverable),
                              'g1_exact':int(anchor==expected_obj),
                              'oracle_exact':int(len(recoverable)==len(truthset))})
            case_line={'case_id':ident,'split':split,'family':row['family'],
                       'g1_exact':anchor==expected_obj,'gold':len(truthset),
                       'unreachable':len(truthset)-len(recoverable)}
            for mode in modes:
                edges=pred[mode];t=sums[mode]
                t.update({'exact':int(edges==truthset),'tp':len(edges&truthset),
                          'pred':len(edges),'gold':len(truthset)})
                case_line[mode+'_exact']=edges==truthset
                case_line[mode+'_false']=len(edges-truthset)
                case_line[mode+'_missed']=len(truthset-edges)
            case_log.append(case_line)
        result={}
        for mode,c in sums.items():
            p=c['tp']/max(c['pred'],1);r=c['tp']/max(c['gold'],1)
            result[mode]={'exact':c['exact'],'exact_rate':c['exact']/size,
                'tp':c['tp'],'fp':c['pred']-c['tp'],'fn':c['gold']-c['tp'],
                'precision':p,'recall':r,'f1':2*p*r/max(p+r,1e-12)}
        gate=cur.GATE_REQUIREMENTS['G2'];key='dev' if split.endswith('dev') else 'challenge'
        failed=[]
        if result['selected']['exact_rate']<gate[key]:failed.append('binding_exact_rate')
        for baseline in ('zero_hidden','no_binding'):
            if (result['selected']['exact']-result[baseline]['exact'])/size<gate['neural_gain']:
                failed.append(baseline+'_neural_causality')
        if result['selected']['exact']<=result['control']['exact']:
            failed.append('no_exact_gain_over_G2_0083')
        if result['selected']['exact']<=result['untrained']['exact']:
            failed.append('no_exact_gain_over_untrained')
        if len(family)<2:failed.append('missing_family')
        metrics[split]={'cases':size,'arms':result,'g1_exact':g1,
            'oracle_upper_bound_exact':reachable['oracle_exact'],
            'unreachable_gold_edges':reachable['gold']-reachable['reachable'],
            'family':dict(family),'failures':failed}
        failures.extend(split+':'+f for f in failed)
    diagnostic={split:diagnose([(r['raw_surface'],answers[r['case_id']]['graph'])
                     for r in predicted if r['split']==split])
                for split in ('dev','challenge')}
    fuzz_score=_fuzz_score(predicted,answers)
    # G1 retention was verified in the 0087 historical run; all 0088
    # inference reuses exactly that head and G1 composite checkpoint.
    selection=js(dest/'selection.json')
    fail_free=not failures
    output_obj={'schema':SCHEMA,'status':('G2_0088_NUMERIC_GATES_PASSED_NOT_CERTIFIED'
            if fail_free else 'G2_0088_GATE_BLOCKED'),
        'selection':selection['winner_method'],
        'evidence_scope':'Fresh within same author generator; not independent distribution.',
        'historical_and_prospective':metrics,'prospective_error_attribution':diagnostic,
        'fuzz':fuzz_score,
        'original_gate':cur.GATE_REQUIREMENTS['G2'],
        'G1_retention_preserved_by_immutable_checkpoint':True,
        'failures':failures,'promotion':False,'g2_certified':False,'g3_training_unlocked':False,
        'integrity':{'parent_head_sha256':commit['parent_head_sha256'],
         'seal_gold_sha256':commit['answers_sha256'],
         'locked_prediction_sha256':lock['predictions_sha256'],
         'selection_sha256':lock['selection_sha256'],
         'gold_opened_only_post_blind_lock':True,'new_training':False}}
    scored=dest/'scored';scored.mkdir(exist_ok=False)
    write_json(scored/'report.json',output_obj)
    write_jsonl(scored/'cases.jsonl',case_log)
    print('G2_0088_RESULT '+json.dumps({'status':output_obj['status'],
        'chosen_decoder':selection['winner_method'],
        'fresh_dev_exact':metrics['dev']['arms']['selected']['exact'],
        'fresh_challenge_exact':metrics['challenge']['arms']['selected']['exact'],
        'fresh_edge_f1':{k:metrics[k]['arms']['selected']['f1'] for k in ('dev','challenge')},
        'historical_dev_exact':metrics['historical_dev']['arms']['selected']['exact'],
        'historical_challenge_exact':metrics['historical_challenge']['arms']['selected']['exact'],
        'failures':failures,'g3_unlocked':False},sort_keys=True),flush=True)
    return output_obj


def run(*,root='../runtime/reports/gamer-v1',output=None,device='cuda'):
    dest=(Path(output) if output else
          Path(root)/'oracle-fabric-g2-posttrain-graph-bakeoff-0088').resolve()
    prepare(root=root,output=dest)
    for command in ('predict','score'):
        args=[sys.executable,'-u','-m',MODULE,command,'--root',str(Path(root).resolve()),
              '--output',str(dest)]
        if command=='predict':args+=['--device',device]
        subprocess.run(args,check=True)
    return js(dest/'scored'/'report.json')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('run','prepare','predict','score'):
        p=sub.add_parser(command)
        p.add_argument('--root',default='../runtime/reports/gamer-v1')
        p.add_argument('--output')
        if command in ('run','predict'):p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    args=vars(parser.parse_args(argv));command=args.pop('command')
    if not args['output'] and command!='run':
        args['output']=str(Path(args['root'])/'oracle-fabric-g2-posttrain-graph-bakeoff-0088')
    return {'run':run,'prepare':prepare,'predict':predict,'score':score}[command](**args)

if __name__=='__main__':main()
