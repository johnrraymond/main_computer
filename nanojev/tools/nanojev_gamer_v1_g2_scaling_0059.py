"""G2-0059: restartable, lineage-checked graph data and learning pipeline.

Research only. The one-time frozen Qwen/G1/G2-0052 capture is sharded;
G2 training reads cached tensors, never Qwen or held-out test labels.
Commands: plan, capture, train, evaluate, audit (see --help).
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, OrderedDict
from dataclasses import dataclass
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile

import torch
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_counterfactual_learning as p50
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_learning_scale_0056 as p56
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_binding_repair_0058 as p58
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
from tools import nanojev_gamer_v1_g2_completion_bakeoff as p40

SCHEMA = 'nanojev.gamer.graph.g2.scalable-data-learning-0059.v1'
SEED = 460559
SPLITS = ('train', 'validation', 'test', 'challenge')
CELLS = tuple((f,m) for f in ('allocation','encounter') for m in ('renaming','role_swap'))
FEATURE_FIELDS = ('node','src','dst','relation','kinds','margin','geometry')
MAX_PAIRS = 500000  # Engineering bound, NOT a claim of adequate diversity.
SHARD_PAIRS = 16
SNAPSHOT_NAME = 'selected.safetensors'
# Rollout cohort sizes are PAIRS, not graphs. The first 16 pairs are a
# diagnostic replay only and never count toward an independent acceptance.
ROLLOUT_PILOT_PAIRS = SHARD_PAIRS
ROLLOUT_CANARY_NEW_PAIRS = 128
ROLLOUT_QUALIFICATION_NEW_PAIRS = 512
ROLLOUT_MIN_TOPOLOGIES = 24
ROLLOUT_MIN_CELL_PAIRS = 8
ROLLOUT_MIN_REACHABILITY = .98
ROLLOUT_MIN_CELL_REACHABILITY = .95


def refuse(why):
    raise ValueError('GAMER_G2_0059_'+str(why))


def sha(raw):
    return hashlib.sha256(raw if isinstance(raw,bytes) else raw.encode('utf-8')).hexdigest()


def file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def atomic_json(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():refuse('IMMUTABLE_FILE_'+path.name)
    fd,tmp=tempfile.mkstemp(prefix='.g2-0059-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+'\n')
            f.flush();os.fsync(f.fileno())
        if path.exists():refuse('IMMUTABLE_RACE')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def atomic_tensors(path,tensors):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():refuse('IMMUTABLE_TENSORS_'+path.name)
    fd,tmp=tempfile.mkstemp(prefix='.g2-0059-',suffix='.safetensors',dir=path.parent)
    os.close(fd)
    try:
        save_file({k:v.detach().cpu().contiguous() for k,v in tensors.items()},tmp)
        if path.exists():refuse('IMMUTABLE_TENSOR_RACE')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _seed(split,index,seed):
    return int(sha(f'{SCHEMA}:{seed}:{split}:{index}')[:15],16)


def _lexicon(split):
    names=corpus.NAME_POOLS['train']
    # TRAIN and VALIDATION use nonoverlapping words; TEST uses corpus DEV
    # lexical inventory. Role nouns are also kept split-disjoint.
    if split=='train':return names[:int(len(names)*.7)],corpus.ACTORS_TRAIN
    if split=='validation':return names[int(len(names)*.7):],corpus.ACTORS_DEV
    if split=='test':return corpus.NAME_POOLS['dev'],corpus.ACTORS_CHALLENGE
    return corpus.NAME_POOLS['challenge'],corpus.ACTORS_TEST


def _append_binding_distractors(row,specs):
    """Add author-verified, non-decision evidence; no inference heuristics.

    Each clause introduces a repeated action occurrence and a numeric marker.
    These nodes compete with existing occurrences for source/destination slots.
    The score graph and its executable decision are unchanged.
    """
    from tools.nanojev_gamer_v1_graph_contract import validate_graph,execute_graph
    choices=row['graph']['objective']['candidates']
    by_id={n['id']:n for n in row['graph']['nodes']}
    for choice_index,marker in specs:
        target=by_id[choices[choice_index]]
        surface=row['prompt'][target['span'][0]:target['span'][1]]
        pre=' A separate record references '
        start=len(row['prompt'])+len(pre)
        row['prompt']+=pre+surface+' with marker '
        ref=f'n{len(row["graph"]["nodes"])}'
        row['graph']['nodes'].append({'id':ref,'kind':'action','span':[start,start+len(surface)]})
        row['object_spans'].append({'kind':'action','start':start,'end':start+len(surface)})
        num_start=len(row['prompt']);value=str(marker)
        row['prompt']+=value+'.'
        qty=f'n{len(row["graph"]["nodes"])}'
        row['graph']['nodes'].append({'id':qty,'kind':'quantity',
                                     'span':[num_start,num_start+len(value)],'value':value})
        row['object_spans'].append({'kind':'quantity','start':num_start,
                                    'end':num_start+len(value)})
        row['graph']['edges'].extend(({'src':ref,'dst':choices[choice_index],
                                      'type':'refers_to'},
                                     {'src':ref,'dst':qty,'type':'measures'}))
    validate_graph(row['graph'],complete=True)
    if execute_graph(row['graph'])['decisions']!=row['expected_decisions']:
        refuse('DISTRACTOR_CHANGED_DECISION')
    return row


def make_pair(split,index,seed=SEED):
    """Deterministic independent scenarios, pair complete, variable shapes.

    Endpoints, names, values and layouts are generated by the TRAIN author;
    no model imports a template, annotation, or answer during inference.
    """
    if split not in SPLITS or not 0<=index<MAX_PAIRS:refuse('PAIR_INDEX')
    rng=random.Random(_seed(split,index,seed))
    family,mode=CELLS[index%4]
    names,actors=_lexicon(split)
    if len(names)<12:refuse('LEXICAL_INVENTORY')
    slots=rng.sample(tuple(names),12)
    actor,first,second,third,*extra=slots
    new_actor=first if mode=='role_swap' else extra[0]
    new_first=actor if mode=='role_swap' else extra[1]
    template=index//4%4
    numeric_seed=rng.randrange(2**31)
    if family=='allocation':
        count=(6 if split=='challenge' else rng.choice((2,3,4,5)))
        options=[first,second,third,*extra[2:]][:count]
        if len(options)!=count:refuse('OPTION_COUNT')
        left=corpus.allocation(random.Random(numeric_seed),'train',template,
            actor_override=actor,names_override=options)
        right=corpus.allocation(random.Random(numeric_seed),'train',template,
            actor_override=new_actor,names_override=[new_first,*options[1:]])
        dimensions=(count,0)
    else:
        dimensions=(rng.choice(((2,4),(4,2))) if split=='challenge' else
                    rng.choice(((2,2),(2,3),(3,2),(3,3))))
        n_player,n_opponent=dimensions
        actions=[first,second,third,*extra[2:]][:n_player+n_opponent]
        if len(actions)!=n_player+n_opponent:refuse('ENCOUNTER_COUNT')
        rival=rng.choice(tuple(actors))
        left=corpus.encounter(random.Random(numeric_seed),'train',template,
            actor_override=actor,rival_override=rival,
            player_override=actions[:n_player],opponent_override=actions[n_player:])
        right=corpus.encounter(random.Random(numeric_seed),'train',template,
            actor_override=new_actor,rival_override=rival,
            player_override=[new_first,*actions[1:n_player]],opponent_override=actions[n_player:])
    base_gold=len(p50.g40.base.gold_binding_edges(left))
    max_extra=min(5,(p55.SLOTS-base_gold)//2)
    specs=[(rng.randrange(len(left['graph']['objective']['candidates'])),rng.randrange(10,100))
           for _ in range(rng.randrange(max_extra+1))]
    _append_binding_distractors(left,specs)
    _append_binding_distractors(right,specs)
    p50.verify_pair(left,right,mode,family)
    # SLOTS bound checked using the actual gold-to-inference binding conversion.
    gold=len(p50.g40.base.gold_binding_edges(left))
    if gold>p55.SLOTS or gold!=len(p50.g40.base.gold_binding_edges(right)):
        refuse('SET_CAPACITY_'+str(gold))
    # Canonical scenario hash excludes all surface names/spans but includes
    # numeric outcomes, graph connectivity and operators; prevents a scenario
    # (or its counterfactual mate) from crossing splits.
    graph=left['graph'];node_data=[(n['id'],n['kind'],n.get('value'),n.get('operator'))
                                    for n in graph['nodes']]
    core=sha(canonical_json((family,dimensions,template,node_data,graph['edges'],graph['objective'])))
    topology=sha(canonical_json((family,dimensions,template,
        [(n['kind'],n.get('operator')) for n in graph['nodes']],graph['edges'])))
    prompts=(sha(left['prompt']),sha(right['prompt']))
    if prompts[0]==prompts[1]:refuse('PAIR_TEXT_DUPLICATE')
    pair_id=sha(f'{split}:{index}:{prompts[0]}:{prompts[1]}')
    return {'split':split,'index':index,'id':pair_id,'family':family,'mode':mode,
        'dimensions':list(dimensions),'template':template,'distractor_count':len(specs),
        'gold_edges':gold,
        'scenario_hash':core,'topology_hash':topology,'sha256':list(prompts),
        'rows':(left,right)}


def pair_receipt(pair):
    return {k:v for k,v in pair.items() if k!='rows'}


def _require_plan(folder):
    folder=Path(folder);plan=json.loads((folder/'plan.json').read_text(encoding='utf-8'))
    if plan.get('schema')!=SCHEMA or plan.get('production_promotion') is not False:
        refuse('PLAN_SCHEMA')
    for split in SPLITS:
        pathname=folder/f'{split}.jsonl.gz'
        if file_sha(pathname)!=plan['files'][split]['sha256']:refuse('PLAN_HASH_'+split)
    return plan


def _read_receipts(folder,split):
    with gzip.open(Path(folder)/f'{split}.jsonl.gz','rt',encoding='utf-8') as f:
        for line in f:yield json.loads(line)


def create_plan(output,*,train_graphs=10000,validation_graphs=400,test_graphs=400,
                challenge_graphs=400,seed=SEED,
                minimum_topologies=24):
    counts=dict(zip(SPLITS,(train_graphs,validation_graphs,test_graphs,challenge_graphs)))
    if any(v<8 or v%8 or v//2>MAX_PAIRS for v in counts.values()):
        refuse('SPLIT_COUNTS_REQUIRE_MULTIPLES_OF_8')
    folder=Path(output)
    if folder.exists():refuse('IMMUTABLE_PLAN')
    folder.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='g2-0059-plan-',dir=folder.parent))
    used_prompts=set();used_scenarios=set();stat={};files={}
    try:
        for split,count in counts.items():
            tops=Counter();shapes=Counter();cells=Counter()
            with gzip.open(temp/f'{split}.jsonl.gz','wt',encoding='utf-8',compresslevel=6) as f:
                for ix in range(count//2):
                    pair=make_pair(split,ix,seed)
                    hashes=pair['sha256']
                    if any(h in used_prompts for h in hashes):refuse('DUPLICATE_PROMPT')
                    if pair['scenario_hash'] in used_scenarios:refuse('DUPLICATE_SCENARIO')
                    used_prompts.update(hashes);used_scenarios.add(pair['scenario_hash'])
                    receipt=pair_receipt(pair);f.write(canonical_json(receipt)+'\n')
                    tops[pair['topology_hash']]+=2
                    shapes[f"{pair['family']}:{pair['dimensions']}:{pair['template']}"]+=2
                    cells[pair['family']+':'+pair['mode']]+=2
            if len(tops)<min(minimum_topologies,max(1,count//4)):
                refuse('STRUCTURAL_DIVERSITY_INSUFFICIENT_'+split)
            if max(cells.values())-min(cells.values())>2:refuse('CELL_IMBALANCE')
            stat[split]={'graphs':count,'pairs':count//2,
                'distinct_topologies':len(tops),'topology_distribution':dict(tops),
                'shape_distribution':dict(shapes),'cells':dict(cells),
                'largest_topology_fraction':round(max(tops.values())/count,6)}
            files[split]={'name':f'{split}.jsonl.gz','sha256':file_sha(temp/f'{split}.jsonl.gz')}
        plan={'schema':SCHEMA,'seed':seed,'splits':stat,'files':files,
            'minimum_topologies':minimum_topologies,
            'scenario_disjoint':True,'prompt_disjoint':True,'counterfactual_pair_atomic':True,
            'structural_diversity_is_not_semantic_diversity':True,
            'production_promotion':False,'sealed_test_used':False,
            'next_gate':'FROZEN_FEATURE_CAPTURE_AND_COVERAGE_AUDIT'}
        atomic_json(temp/'plan.json',plan)
        if folder.exists():refuse('PLAN_RACE')
        os.replace(temp,folder)
    finally:
        if temp.exists():
            import shutil
            shutil.rmtree(temp)
    return plan


@dataclass
class Shard:
    split:str
    index:int
    pairs:list
    rows:list


def _iter_shards(plan_dir,split,seed,shard_pairs):
    bucket=[];start=0
    for receipt in _read_receipts(plan_dir,split):
        p=make_pair(split,receipt['index'],seed)
        if pair_receipt(p)!=receipt:refuse('AUTHOR_REPLAY_DRIFT_'+split)
        bucket.append(p)
        if len(bucket)==shard_pairs:
            yield Shard(split,start,bucket,[r for pair in bucket for r in pair['rows']])
            start+=len(bucket);bucket=[]
    if bucket:yield Shard(split,start,bucket,[r for pair in bucket for r in pair['rows']])


def _shard_paths(output,split,index):
    base=f'{split}-{index:07d}'
    return Path(output)/f'{base}.safetensors',Path(output)/f'{base}.json',Path(output)/f'{base}.labels.safetensors'


def _construct_spatial(raw, parent):
    """G2-0059 research alternative: spatially balanced *raw-neural* anchors.

    The author graph, labels, game family and objective are not read.  A
    suffix-only neural candidate can otherwise lose every slot to better
    ranked spans earlier in a longer prompt.  Preserve the admitted 0051
    anchors; compete for the 0052 additional slots by both kind and location.
    This is a research candidate interface, never a promoted G1 revision.
    """
    logits, valid, prepared = p52._validate(raw, parent)
    admitted=list(parent['expanded_anchors'])
    budget=p52.MAX_ANCHORS-len(admitted)
    if budget<0:refuse('SPATIAL_ADMITTED_OVER_BUDGET')
    # Build each spatial bucket directly from the complete frozen G1 token
    # lattice.  The standard G2-0052 global top-384 per kind may exclude an
    # entire suffix before spatial balancing could help it.
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    import heapq
    score=logits[:,:,1:]-logits[:,:,0:1]
    prompt_len=max(1,len(prepared['prompt']))
    active_kinds=tuple(kind for kind in SPAN_KINDS
        if any(kind==src_kind or kind==dst_kind for _,src_kind,dst_kind in raw['allowed']))
    if not active_kinds:refuse('SPATIAL_NO_PERMITTED_TYPES')
    bins={(kind,sector):[] for kind in active_kinds for sector in range(8)}
    # Per-bin top-32 is bounded and chosen without any author annotations.
    # Candidates are scored by G1's *original* type-versus-null margin.
    for t,w in valid.nonzero(as_tuple=False).tolist():
        bounds=token_span_char_bounds({'token_first':t,'token_last':t+w},prepared)
        if bounds is None:continue
        for kind in active_kinds:
            k=SPAN_KINDS.index(kind)
            margin=float(score[t,w,k]);anchor=(bounds[0],bounds[1],kind)
            variants=(anchor,p52._punct_trim(anchor,prepared['prompt']))
            for variant in dict.fromkeys(a for a in variants if a is not None):
                region=min(7,8*variant[0]//prompt_len)
                bucket=bins[kind,region]
                adjusted=margin-(0.001 if variant!=anchor else 0.)
                item=(adjusted,-t,-w,variant,(t,t+w))
                if len(bucket)<32:heapq.heappush(bucket,item)
                elif item>bucket[0]:heapq.heapreplace(bucket,item)
    queues={key:sorted(items,reverse=True) for key,items in bins.items()}
    from collections import deque
    sectors=(0,7,1,6,2,5,3,4)
    slots=deque((active_kinds[(j+turn)%len(active_kinds)],sector)
        for turn in range(len(active_kinds)) for j,sector in enumerate(sectors))
    choices=[];seen=set(admitted);cursor={k:0 for k in queues}
    while slots and len(choices)<budget:
        key=slots.popleft();entries=queues[key];i=cursor[key]
        while i<len(entries) and entries[i][3] in seen:i+=1
        cursor[key]=i
        if i<len(entries):
            item=entries[i];cursor[key]+=1
            seen.add(item[3]);choices.append((item[0],item[3],item[4]))
            slots.append(key)
    # The source-neural selection is deterministic even when margins tie.
    neural={kind:[(m,a,span) for m,a,span in choices if a[2]==kind]
            for kind in SPAN_KINDS}
    anchors=admitted+[item[1] for item in choices]
    if len(anchors)!=len(set(anchors)) or len(anchors)>p52.MAX_ANCHORS:
        refuse('SPATIAL_ANCHOR_INVARIANT')
    provenance={item[1]:{'token_first':item[2][0],
        'token_last':item[2][1],'kind':item[1][2],
        'char_span':[item[1][0],item[1][1]],
        'neural_margin':round(item[0],6),'source':'frozen_g1_raw_spatial'}
        for item in choices}
    typed=p52.inventory(anchors,raw['allowed'])
    if not set(parent['recovered_typed'])<=typed:
        refuse('SPATIAL_PARENT_MONOTONICITY')
    return {'anchors':anchors,'new_anchors':[item[1] for item in choices],
        'typed_edges':typed,'new_endpoint_provenance':provenance,
        'raw_lattice':p52._raw_lattice(logits,valid,prepared),
        'raw_valid':valid,'logits':logits,'prepared':prepared,'selected_neural':neural}



def _construct_evidence(raw, parent):
    """Research-only, label-blind coverage policy for numeric/repeated mentions.

    Lexical evidence is taken exclusively from the inference prompt, never
    graph nodes, object_spans, authored identifiers, labels or the pair ID.
    Existing 0051 anchors are immutable. New anchors compete for the remaining
    bounded 0052 capacity; no weights are changed or scorer is retrained.
    """
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

    logits,valid,prepared=p52._validate(raw,parent)
    prompt=prepared['prompt']
    allowed=set(raw['allowed'])
    base=list(parent['expanded_anchors'])
    remaining=p52.MAX_ANCHORS-len(base)
    if remaining<0:refuse('EVIDENCE_PARENT_BUDGET')
    allowed_kinds={kind for _,src,dst in allowed for kind in (src,dst)}
    lattice=p52._raw_lattice(logits,valid,prepared)
    offsets=prepared['offsets'];start=int(prepared['prompt_start'])
    eligibility=prepared.get('eligible')
    if eligibility is not None:
        eligibility=(eligibility.tolist() if hasattr(eligibility,'tolist')
                     else eligibility)
    valid_offsets=sorted(((left,right) for i,(left,right) in enumerate(offsets)
        if right>left and left>=start and right<=start+len(prompt)
        and (eligibility is None or bool(eligibility[i]))),key=lambda item:item[1])
    valid_ends=[end for _,end in valid_offsets]
    options={}
    def offer(anchor,priority,margin,source,token=None):
        if anchor in base or anchor[2] not in allowed_kinds:
            return
        a,b,kind=anchor
        if not (0<=a<b<=len(prompt)):
            return
        # The occurrence encoder needs at least one frozen contextual token.
        # Lexical proposals may end inside a token, but must overlap that
        # prompt's eligible tokenizer offsets (no invented hidden states).
        # Frozen tokenizer offsets are monotone on the prompt. One binary
        # search avoids scanning every token for each of thousands of raw
        # span alternatives on a large captured corpus.
        index=bisect_right(valid_ends,start+a)
        if index>=len(valid_offsets) or valid_offsets[index][0]>=start+b:
            return
        variant=(float(priority),float(margin),-(b-a),source,token)
        previous=options.get(anchor)
        if previous is None or variant[:2]>previous[:2]:
            options[anchor]=variant

    # An integer/decimal literal is a deterministic text-level hypothesis of
    # a quantity; it is NOT an assertion that the literal is a graph node.
    # This explicitly handles digits that Qwen merges with punctuation.
    if 'quantity' in allowed_kinds:
        for match in re.finditer(r'(?<![\w.])[-+]?\d+(?:\.\d+)?(?!\w)',prompt):
            a,b=match.span();candidate=(a,b,'quantity')
            # Do not let a numeric substring inside a hyphenated identifier
            # steal priority over a standalone numeric measurement.
            if (a>0 and prompt[a-1]=='-') or (b<len(prompt) and prompt[b]=='-'):
                continue
            # Separate marker numbers from optional digits in object names
            # using only the immediate text boundary, not authored templates.
            score=6.0
            offer(candidate,score,0.,'inference_text_numeric')

    # A repeated surface is evidence of an occurrence, even if G1's weak
    # suffix-span score would lose the global 192-anchor contest. Candidate
    # surfaces still originate from frozen neural action/actor hypotheses.
    candidate_surfaces={}
    for anchor in base:
        if anchor[2] in ('action','actor') and anchor[2] in allowed_kinds:
            candidate_surfaces[anchor]=(5.0,0.)
    scores=logits[:,:,1:]-logits[:,:,0:1]
    for t,w in valid.nonzero(as_tuple=False).tolist():
        bounds=token_span_char_bounds({'token_first':t,'token_last':t+w},prepared)
        if bounds is None:continue
        for kind in ('action','actor'):
            if kind not in allowed_kinds:continue
            k=SPAN_KINDS.index(kind)
            neural=float(scores[t,w,k])
            # A low raw neural margin is allowed, but only when the exact
            # surface recurs; this avoids vocabulary-specific rescue rules.
            if neural<-10.:continue
            anchor=p52._punct_trim((*bounds,kind),prompt)
            if anchor is None or anchor[1]-anchor[0]<3:continue
            current=candidate_surfaces.get(anchor)
            if current is None or (3.0,neural)>current:
                candidate_surfaces[anchor]=(3.0,neural)
    seen_surfaces=set()
    for anchor,(priority,margin) in sorted(candidate_surfaces.items(),
                                           key=lambda kv:(-kv[1][0],-kv[1][1],kv[0])):
        a,b,kind=anchor
        surface=prompt[a:b]
        if surface.casefold() in seen_surfaces:continue
        seen_surfaces.add(surface.casefold())
        if not any(c.isalpha() for c in surface):continue
        # Exact text repetition with boundaries prevents substring anchors
        # inside longer names (e.g., 'Zen' inside 'Zenxan').
        positions=[m.span() for m in re.finditer(re.escape(surface),prompt,re.IGNORECASE)
            if (m.start()==0 or not (prompt[m.start()-1].isalnum() or prompt[m.start()-1] in '_-'))
            and (m.end()==len(prompt) or not (prompt[m.end()].isalnum() or prompt[m.end()] in '_-'))]
        if len(positions)<2:continue
        for left,right in positions:
            offer((left,right,kind),priority,min(10.,margin),
                  'recurrent_frozen_g1_surface')

    # Bounded, deterministic allocation: numeric evidence first, recurring
    # action evidence second, then baseline spatial G1 candidates. We record
    # all discarded counts for post-capture diagnosis; never look at gold.
    spatial=_construct_spatial(raw,parent)
    for anchor in spatial['new_anchors']:
        prov=spatial['new_endpoint_provenance'][anchor]
        neural=float(prov['neural_margin'])
        offer(anchor,1.0,neural,'frozen_g1_raw_spatial',
              (prov['token_first'],prov['token_last']))
    # Deterministic tie-breaking, with spatial distribution over repeated
    # surfaces and numeric mentions already represented by their char spans.
    ranked=sorted(options.items(),key=lambda item:(
        -item[1][0],-item[1][1],item[0][0],item[0][1],item[0][2]))
    chosen=ranked[:remaining]
    anchors=base+[anchor for anchor,_ in chosen]
    if len(anchors)!=len(set(anchors)) or len(anchors)>p52.MAX_ANCHORS:
        refuse('EVIDENCE_ANCHOR_INVARIANT')
    typed=p52.inventory(anchors,allowed)
    if not set(parent['recovered_typed'])<=typed:
        refuse('EVIDENCE_PARENT_MONOTONICITY')
    provenance={anchor:{'kind':anchor[2],'char_span':[anchor[0],anchor[1]],
        'neural_margin':round(info[1],6),'source':info[3],
        'token_first':info[4][0] if info[4] is not None else None,
        'token_last':info[4][1] if info[4] is not None else None}
        for anchor,info in chosen}
    return {'anchors':anchors,'new_anchors':[anchor for anchor,_ in chosen],
        'typed_edges':typed,'new_endpoint_provenance':provenance,
        'raw_lattice':lattice,'raw_valid':valid,'logits':logits,'prepared':prepared,
        'selected_neural':spatial['selected_neural'],
        'evidence_summary':{'eligible':len(options),'selected':len(chosen),
            'by_source':dict(Counter(info[3] for _,info in chosen)),
            'budget':remaining,'discarded':max(0,len(options)-remaining)}}


def inspect_missing(plan_dir,capture_dir,*,split='train',shard_index=0):
    """Offline diagnosis from the existing immutable capture sidecars.

    Reports original endpoints and frozen G1 margin/token-boundary evidence.
    Does not open any test labels or write to a committed feature directory.
    """
    if split!='train':refuse('BINDING_DIAGNOSIS_TRAIN_ONLY')
    capture=FeatureCorpus(plan_dir,capture_dir,split,require_complete=False)
    config=capture.config
    if config.get('candidate_policy') not in ('spatial','evidence'):
        refuse('BINDING_DIAGNOSIS_REQUIRES_PILOT')
    by_reason=Counter();by_kind=Counter();by_relation=Counter();records=[]
    matched=[entry for start,_,left,right in capture.pairs if start>=shard_index
             and start<shard_index+config['shard_pairs']
             for entry in (left,right)]
    if len(matched)!=2*config['shard_pairs']:
        refuse('DIAGNOSIS_SHARD_INCOMPLETE')
    for entry in matched:
        dia=entry.get('binding_diagnosis')
        if not isinstance(dia,dict):refuse('DIAGNOSIS_MISSING_SIDECAR')
        for missing in dia['cases']:
            for node in missing['missing_nodes']:
                by_reason[node['reason']]+=1;by_kind[node['kind']]+=1
            by_relation[missing['type']]+=1
            records.append({'pair_id':entry['pair_id'],'side':entry['side'],
                'family':entry['family'],'mode':entry['mode'],
                **missing})
    return {'schema':SCHEMA,'status':'READONLY_ENDPOINT_DIAGNOSIS',
        'capture_policy':config['candidate_policy'],
        'missing_edges':len(records),'by_endpoint_reason':dict(by_reason),
        'by_endpoint_kind':dict(by_kind),'by_relation':dict(by_relation),
        'edges':records,'production_promotion':False,'sealed_test_used':False}

def _diagnose_missing_edges(row,raw,expanded,*,baseline=None):
    """Train-only, after inference construction; never passed to the scorer.

    Classify each missed binding against the precise author spans, the frozen
    token lattice, source-only neural margin, and type-signature inventory.
    Does not modify input candidates or choose a policy from gold labels.
    """
    gold=p50.g40.base.gold_binding_edges(row)
    allowed=set(raw['allowed']);anchors=set(expanded['anchors'])
    missing=gold-set(expanded['typed_edges'])
    lattice=expanded['raw_lattice'];logits=expanded['logits']
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    new_count=int(row.get('_g2_0059_distractor_count',0))
    inserted={n['id'] for n in row['graph']['nodes'][-2*new_count:]} if new_count else set()
    anchor_to_id={(n['span'][0],n['span'][1],n['kind']):n['id']
        for n in row['graph']['nodes'] if 'span' in n}
    cases=[];reasons=Counter();by_origin=Counter();by_relation=Counter()
    for src,dst,relation in sorted(missing):
        signature=(relation,src[2],dst[2]);missing_nodes=[]
        for anchor in (src,dst):
            if anchor in anchors:continue
            eligible=lattice.get(anchor[:2],())
            punct_only=False
            if not eligible:
                # Frozen tokenizer may attach punctuation to a numeric token.
                # Only generic punctuation trimming is used, never author gold
                # in inference; this branch is a post-capture diagnostic.
                eligible=[token for bounds, tokens in lattice.items()
                    if p52._punct_trim((*bounds,anchor[2]),row['prompt'])==anchor
                    for token in tokens]
                punct_only=bool(eligible)
            rank=None
            if not eligible:
                why='no_exact_or_punctuation_trimmed_token_boundary';margin=None
            else:
                kind_index=SPAN_KINDS.index(anchor[2])+1
                margin=max(float(logits[t,w,kind_index]-logits[t,w,0])
                    for t,w in eligible)
                deltas=logits[:,:,kind_index]-logits[:,:,0]
                # This is a post-hoc rank in the frozen G1 lattice, NOT a
                # feature or ranking rule for candidate generation.
                mask=expanded.get('raw_valid')
                if mask is None:
                    mask=torch.zeros_like(deltas,dtype=torch.bool)
                    for token_spans in lattice.values():
                        for t,w in token_spans:mask[t,w]=True
                rank=1+int(((deltas>margin+1e-7)&mask).sum().item())
                why=('punctuation_boundary_not_selected' if punct_only else
                    'raw_g1_favors_null' if margin<=0 else
                    'positive_raw_option_not_selected')
            missing_nodes.append({'role':'source' if anchor==src else 'destination',
                'kind':anchor[2], 'span':[anchor[0],anchor[1]],
                'surface':row['prompt'][anchor[0]:anchor[1]],
                'token_spans':[[int(t),int(t+w)] for t,w in eligible[:8]],
                'raw_kind_rank':rank,
                'reason':why,'raw_margin':round(margin,6) if margin is not None else None})
        if signature not in allowed:why='unsupported_type_signature'
        elif missing_nodes:why='missing_endpoint'
        else:why='inventory_or_alignment_defect'
        origin=('added_distractor' if anchor_to_id.get(src) in inserted
                or anchor_to_id.get(dst) in inserted else 'original_graph')
        reasons[why]+=1;by_origin[origin]+=1;by_relation[relation]+=1
        cases.append({'type':relation,'origin':origin,'reason':why,
            'src_span':list(src[:2]),'dst_span':list(dst[:2]),
            'src_kind':src[2],'dst_kind':dst[2],
            'missing_nodes':missing_nodes})
    return {'missing_edges':len(missing),'by_cause':dict(reasons),
        'by_origin':dict(by_origin),'by_relation':dict(by_relation),
        'cases':cases, 'baseline_missing_edges':(
            len(gold-set(baseline['typed_edges'])) if baseline is not None else None)}


def _payload_feature(sample,rec,split,pair,side):
    tensors={key:sample[key].detach().cpu() for key in FEATURE_FIELDS}
    if any(not bool(torch.isfinite(v).all()) for v in tensors.values() if v.is_floating_point()):
        refuse('NONFINITE_CAPTURE')
    # Gold is attached after all model-facing features are frozen. For TEST,
    # it is written to a separate label-only file; train never opens it.
    gold=set(p50.g40.base.gold_binding_edges(rec['row']))
    score={**sample,'labels':gold,'edges':list(sample['edges'])}
    g53.mark_truth(score)
    if score['truth_count']>p55.SLOTS:refuse('CAPTURE_SLOT_OVERFLOW')
    coverage=(len(score['positive'])==score['truth_count'])
    if split=='train' and coverage:
        aligned=p57.training_node_alignment(rec['row'],rec['expanded_interface'],sample)
        ids=aligned['training_node_ids']
    else:ids=None
    metadata={'prompt_sha256':pair['sha256'][side],'pair_id':pair['id'],
        'family':pair['family'],'mode':pair['mode'],'side':side,
        'truth_count':score['truth_count'],'candidate_reachable':coverage,
        'edge_count':len(sample['edges']),'topology_hash':pair['topology_hash'],
        'missing_candidate_gold_edges':score['truth_count']-len(score['positive']),
        'dimensions':pair['dimensions'],'template':pair['template'],
        'distractor_count':pair['distractor_count'],
        'training_node_ids':list(ids) if ids is not None else None}
    if '_capture_diagnosis' in sample and split=='train':
        metadata['binding_diagnosis']=sample['_capture_diagnosis']
        metadata['binding_diagnosis_before']=sample['_capture_diagnosis_before']
    # Inference tensors contain no gold or semantic node ids.
    return tensors,score['positive'],metadata


def _write_shard(output,shard,samples):
    feature_path,meta_path,label_path=_shard_paths(output,shard.split,shard.index)
    if any(p.exists() for p in (feature_path,meta_path,label_path)):
        refuse('SHARD_PARTIAL_EXISTS')
    if len(samples)!=len(shard.rows):refuse('CAPTURE_ROW_COUNT')
    tensors={};labels={};entries=[]
    for i,(sample,row) in enumerate(zip(samples,shard.rows)):
        pair=shard.pairs[i//2]
        rec={'row':row,'expanded_interface':sample.pop('_expanded_interface')}
        t,pos,entry=_payload_feature(sample,rec,shard.split,pair,i%2)
        for key,val in t.items():tensors[f'{i}.{key}']=val
        # This tensor is never part of the inference feature shard.
        labels[f'{i}.positive']=pos
        entries.append(entry)
    if shard.split=='train' and any(not x['candidate_reachable'] for x in entries):
        # Retain failures for a causal coverage audit, but do not invent gold
        # endpoints or use incomplete graphs as fully supervised examples.
        pass
    atomic_tensors(feature_path,tensors)
    atomic_tensors(label_path,labels)
    meta={'schema':SCHEMA,'split':shard.split,'pair_start':shard.index,
        'pair_count':len(shard.pairs),'entries':entries,'features_sha256':file_sha(feature_path),
        'labels_sha256':file_sha(label_path),'label_file_separate':True}
    atomic_json(meta_path,meta)
    return meta


def verify_shard(folder,split,start,expected_pairs=None):
    features,meta,labels=_shard_paths(folder,split,start)
    if not all(p.exists() for p in (features,meta,labels)):
        refuse('MISSING_OR_PARTIAL_SHARD_'+split+'_'+str(start))
    item=json.loads(meta.read_text(encoding='utf-8'))
    if (item.get('schema')!=SCHEMA or item.get('split')!=split
        or item.get('pair_start')!=start or (expected_pairs is not None
            and item.get('pair_count')!=expected_pairs)
        or item.get('features_sha256')!=file_sha(features)
        or item.get('labels_sha256')!=file_sha(labels)):
        refuse('SHARD_INTEGRITY_'+split+'_'+str(start))
    return item


def _load_parent_encoder(parent48,device):
    parent48=Path(parent48)
    report=json.loads((parent48/d48.REPORT).read_text(encoding='utf-8'))
    checkpoint=parent48/d48.WEIGHTS
    if report.get('schema')!=d48.SCHEMA or report.get('weights_sha256')!=file_sha(checkpoint):
        refuse('FROZEN_ENCODER_PARENT')
    original=load_file(str(checkpoint),device='cpu')
    encoder=d48.OccurrenceEvidenceScorer(original['encoder.norm.weight'].shape[0])
    encoder.load_state_dict(original,strict=True)
    encoder.eval().requires_grad_(False).to(device)
    return encoder,file_sha(checkpoint)


def _parent40_paths(root):
    labels={
        'precision30_run':'graph-precision-selection-0030',
        'representation31_run':'representation-bakeoff-0031',
        'completeness32_run':'graph-completeness-0032',
        'structured33_run':'structured-graph-decoder-0033',
        'diagnosis34_run':'causal-graph-diagnosis-0034',
        'coverage35_run':'candidate-coverage-0035',
        'selective36_run':'selective-occurrence-0036',
        'curriculum37_run':'occurrence-curriculum-0037',
        'context38_run':'contextual-occurrence-0038',
        'relationship39_run':'recovered-relationships-0039',
    }
    return {name:str(Path(root)/('graph-g2-'+suffix)) for name,suffix in labels.items()}


def _capture_parent_paths(root):
    return {**_early_parent_paths(root), **_parent40_paths(root)}


def _capture_parent_preflight(root):
    """Fail before Qwen loading if the frozen legacy parent chain is unavailable."""
    root=Path(root)
    paths=_capture_parent_paths(root)
    missing=[str(Path(value)) for value in paths.values() if not Path(value).is_dir()]
    required={
        'g1_validation_report':root/'graph-g1-reconstruction-validation-0031'/'reconstruction_validation_report.json',
        'precision30_report':Path(paths['precision30_run'])/p40.p30.REPORT,
    }
    missing.extend(str(path) for path in required.values() if not path.is_file())
    if missing:
        refuse('CAPTURE_PARENTS_MISSING:'+json.dumps(sorted(set(missing))))
    return paths


def _early_parent_paths(root):
    # Exact 0001-0029 path names inherited from 0058's live CLI.
    short=('binding-bootstrap-0001','binding-selection-0002','binding-discrimination-0003',
      'relational-training-0004','density-discrimination-0005','graph-consistency-0006',
      'contextual-graph-0007','learned-graph-context-0008','recall-preserving-graph-0009',
      'crowded-recall-calibration-0010','robustness-validation-0011',
      'relationship-error-diagnosis-0012','source-conditioned-discrimination-0013',
      'density-specialization-0014','density-routing-robustness-0015',
      'general-relational-discrimination-0016','architectural-ceiling-diagnosis-0017',
      'competitor-context-intervention-0018','inference-realistic-training-0019',
      'structural-ceiling-validation-0020','relationship-discrimination-ceiling-0021',
      'competition-aware-learning-0022','contextual-relationship-representation-0023',
      'context-utilization-audit-0024','context-utilization-intervention-0025',
      'competitor-identity-discrimination-0026','relationship-evidence-sufficiency-audit-0027',
      'occurrence-identity-evidence-probe-0028','architecture-bakeoff-0029')
    names=('g2_run','selection_run','discrimination_run','relational_run','density_run',
      'consistency_run','context_run','learned_run','recall_run','crowded_run',
      'robustness_run','diagnosis_run','source_run','specialization_run',
      'routing_robustness_run','general_run','ceiling_run','context18_run','train19_run',
      'ceiling20_run','ceiling21_run','competition22_run','context23_run','audit24_run',
      'intervention25_run','identity26_run','evidence27_run','probe28_run','bakeoff29_run')
    return {k:str(Path(root)/('graph-g2-'+v)) for k,v in zip(names,short)}


def capture(plan_dir,output,*,root,device='cuda',shard_pairs=SHARD_PAIRS,max_shards=None,
            candidate_policy='legacy',compare_capture=None,only_shards=None,
            evidence_proof=None):
    """One real frozen capture session. Shards are persisted before returning.

    The 0040 hook loads Qwen and frozen G1/G2 ancestors exactly once. A resumed
    call verifies completed shard hashes and skips them; it cannot silently
    reuse stale or partially written shards.
    """
    if shard_pairs<1 or shard_pairs>64 or max_shards is not None and max_shards<1:
        refuse('CAPTURE_BUDGET')
    if candidate_policy not in ('legacy','spatial','evidence','actor_cues'):
        refuse('CANDIDATE_POLICY')
    if compare_capture is not None and (shard_pairs!=SHARD_PAIRS or max_shards!=1):
        refuse('PILOT_COMPARISON_REQUIRES_ONE_ORIGINAL_SHARD')
    if compare_capture is not None and candidate_policy not in ('spatial','evidence'):
        refuse('PILOT_REQUIRES_SPATIAL_OR_EVIDENCE_POLICY')
    if candidate_policy=='evidence' and compare_capture is None and evidence_proof is None:
        refuse('EVIDENCE_PILOT_REQUIRES_SPATIAL_PARENT')
    if evidence_proof is not None and (candidate_policy!='evidence' or compare_capture is not None
            or only_shards is None or shard_pairs!=SHARD_PAIRS):
        refuse('EVIDENCE_ROLLOUT_MUST_BE_GATED')
    # Resolve caller-supplied paths before entering the legacy capture runner.
    # That runner expects the repository root as its working directory, just
    # like the established graph_curriculum CLI entrypoint. Its relative
    # resource paths must not depend on where the operator launched Python.
    plan_dir=Path(plan_dir).resolve()
    output=Path(output).resolve()
    root=Path(root).resolve()
    plan=_require_plan(plan_dir)
    if not root.is_dir():refuse('PARENT_REPORT_ROOT_MISSING')
    paths=_capture_parent_preflight(root)
    parent57=root/'graph-g2-0057-counterfactual-bakeoff'
    _,parent_hashes,parent_report=p58.verify_parent57(parent57)
    # Confirm that the G2-0052 interface artifact still matches its saved
    # parent lineage; a code-level feature change cannot masquerade as data.
    g53.verify_parent52(root/'graph-g2-endpoint-interface-repair-0052')
    encoder,encoder_hash=_load_parent_encoder(root/'graph-g2-occurrence-evidence-repair-0048',device)
    cfg={'schema':SCHEMA,'plan_sha256':file_sha(Path(plan_dir)/'plan.json'),
        'frozen_encoder_sha256':encoder_hash,
        'parent57_report_sha256':file_sha(parent57/p57.REPORT),
        'parent57_artifact_hashes':parent_hashes,
        'capture_source_hashes':{name:file_sha(Path(module.__file__)) for name,module in
            (('pipeline',__import__(__name__,fromlist=['main'])),
             ('graph_author',corpus),('legacy_frozen_runner',p40),
             ('counterfactual_label_contract',p50),('g2_0052',p52),
             ('frozen_occurrence_encoder',d48),('g2_features',g53),
             ('training_pair_alignment',p57))},
        'shard_pairs':shard_pairs,'candidate_policy':candidate_policy,
        'production_promotion':False}
    if candidate_policy=='actor_cues':
        from tools import nanojev_gamer_v1_g2_actor_bakeoff_0060 as actor_policy
        from tools import nanojev_gamer_v1_g2_learning_0061 as learning_lane
        cfg['capture_source_hashes']['actor_policy_0060']=file_sha(Path(actor_policy.__file__))
        cfg['capture_source_hashes']['experimental_lane_0061']=file_sha(Path(learning_lane.__file__))
    if evidence_proof is not None:
        if evidence_proof['parent57_report_sha256']!=cfg['parent57_report_sha256'] or (
            evidence_proof['frozen_encoder_sha256']!=cfg['frozen_encoder_sha256']):
            refuse('EVIDENCE_PILOT_PARENT_DRIFT')
        cfg['rollout_pilot_proof_sha256']=evidence_proof['proof_sha256']
    output.mkdir(parents=True,exist_ok=True)
    config=output/'capture.json'
    if config.exists():
        prior=json.loads(config.read_text(encoding='utf-8'))
        if prior!=cfg:
            # A previous attempt may have failed before saving its very first
            # shard. Source fixes must be replayable without manual deletion,
            # but never silently reinterpret even one committed feature shard.
            def without_sources(record):
                compatible={k:v for k,v in record.items() if k!='capture_source_hashes'}
                # Empty pre-fix captures did not record candidate_policy.
                # Legacy is the only backward-compatible interpretation.
                if 'candidate_policy' not in compatible:
                    compatible['candidate_policy']='legacy'
                return compatible
            if (without_sources(prior)!=without_sources(cfg) or
                    not isinstance(prior.get('capture_source_hashes'),dict) or
                    any(path.name!='capture.json' for path in output.iterdir())):
                refuse('CAPTURE_CONFIG_DRIFT')
            fd,tmp=tempfile.mkstemp(prefix='.g2-0059-rebase-',suffix='.tmp',dir=output)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    stream.write(json.dumps(cfg,indent=2,sort_keys=True)+'\n')
                    stream.flush();os.fsync(stream.fileno())
                # Identity fields and the empty feature store were verified.
                os.replace(tmp,config)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
            print('GAMER_G2_0059_CAPTURE_EMPTY_REBASE '+json.dumps({
                'reason':'source_changed_before_first_committed_shard',
                'preserved_completed_shards':0},sort_keys=True),flush=True)
    else:atomic_json(config,cfg)
    needed=[];completed=[]
    for split in SPLITS:
        count=plan['splits'][split]['pairs']
        for start in range(0,count,shard_pairs):
            size=min(shard_pairs,count-start)
            f,m,l=_shard_paths(output,split,start)
            if m.exists():
                verify_shard(output,split,start,size)
                completed.append((split,start))
            else:
                # No commit marker: a crashed write is disposable, not a
                # completed/corrupt shard. Never delete a committed shard.
                for orphan in (f,l):
                    if orphan.exists():orphan.unlink()
                needed.append((split,start))
    if only_shards is not None:
        allowed=set(only_shards)
        if not allowed or any(split not in SPLITS or start<0 or start%shard_pairs
                              for split,start in allowed):
            refuse('ROLLOUT_SHARD_SELECTION')
        if any(item not in allowed for item in completed):
            refuse('ROLLOUT_UNAUTHORIZED_PREEXISTING_SHARD')
        needed=[item for item in needed if item in allowed]
    pending=len(needed)
    # A committed shard must map exactly to the immutable plan, not merely
    # have matching internal file checksums. Partial captures are permitted.
    for split in SPLITS:
        FeatureCorpus(plan_dir,output,split,require_complete=False)
    if max_shards is not None:needed=needed[:max_shards]
    if not needed:
        return {'status':'COMPLETE_OR_NO_PENDING_SHARDS',
                'completed_shards':len(completed),'pending':pending,'captured_now':0}
    needed_ids=set(needed)
    def consume(**hooks):
        cap=hooks.get('capture_candidate_repair_rows')
        check=hooks.get('assert_all_frozen')
        if cap is None or check is None:refuse('FROZEN_CAPTURE_HOOK_MISSING')
        recorded=[]
        for split in SPLITS:
          for shard in _iter_shards(plan_dir,split,plan['seed'],shard_pairs):
            if (split,shard.index) not in needed_ids:
                continue
            payload=[]
            for offset in range(0,len(shard.rows),8):
                batch=shard.rows[offset:offset+8]
                records=cap(batch,'g2_0059_'+shard.split,
                            include_raw_g1=True)
                if len(records)!=len(batch):refuse('CAPTURE_CARDINALITY')
                for record,row in zip(records,batch):
                    if record['row']['prompt']!=row['prompt']:
                        refuse('CAPTURE_PROMPT_IDENTITY')
                    proposed=record.pop('candidate_repair')
                    raw=record.pop('g1_raw_interface')
                    if candidate_policy=='spatial':
                        baseline=p52.construct(raw,proposed)
                        expanded=_construct_spatial(raw,proposed)
                    elif candidate_policy=='evidence':
                        baseline=_construct_spatial(raw,proposed)
                        expanded=_construct_evidence(raw,proposed)
                    elif candidate_policy=='actor_cues':
                        from tools import nanojev_gamer_v1_g2_actor_bakeoff_0060 as actor_policy
                        baseline=_construct_evidence(raw,proposed)
                        expanded=actor_policy._actor_reservation(raw,proposed,
                            __import__(__name__,fromlist=['main']),arm='actor_cues',
                            evidence=baseline)
                    else:
                        baseline=None
                        expanded=p52.construct(raw,proposed)
                    if shard.split=='train' and candidate_policy in ('spatial','evidence','actor_cues'):
                        row['_g2_0059_distractor_count']=shard.pairs[
                            len(payload)//2]['distractor_count']
                        # Diagnostic reads gold only after label-blind candidates
                        # are finalized. This sidecar is never a model input.
                        diagnosis=_diagnose_missing_edges(row,raw,expanded,baseline=baseline)
                        diagnosis_before=_diagnose_missing_edges(row,raw,baseline)
                        del row['_g2_0059_distractor_count']
                    model_input=g53.frozen_features(raw,expanded,encoder,'counterfactual_train')
                    model_input['_expanded_interface']=expanded
                    if shard.split=='train' and candidate_policy in ('spatial','evidence','actor_cues'):
                        model_input['_capture_diagnosis']=diagnosis
                        model_input['_capture_diagnosis_before']=diagnosis_before
                    payload.append(model_input)
            metadata=_write_shard(output,shard,payload)
            check()
            recorded.append((shard.split,shard.index))
            print('GAMER_G2_0059_CAPTURE '+json.dumps({'split':shard.split,
                'pair_start':shard.index,'pairs':len(shard.pairs),
                'reachable_pairs':sum(metadata['entries'][i]['candidate_reachable']
                    and metadata['entries'][i+1]['candidate_reachable']
                    for i in range(0,len(metadata['entries']),2)),
                'saved':len(recorded)},sort_keys=True),flush=True)
        return recorded
    # G2-0040 refuses an output path that already exists. A reusable
    # '.capture-working' directory would therefore break every restart.
    # Commit only the SHA-checked shards; keep each transient parent run in
    # a fresh workspace that is removed after its frozen callback completes.
    with tempfile.TemporaryDirectory(prefix='.g2-0059-frozen-',dir=output) as work:
        fresh_parent_output=Path(work)/'frozen-run'  # MUST NOT already exist.
        original_cwd=Path.cwd()
        try:
            os.chdir(Path(__file__).resolve().parents[2])
            p40.run(**paths,output=str(fresh_parent_output),device=device,
                root=str(root),g1_validation_report=str(root/'graph-g1-reconstruction-validation-0031'/'reconstruction_validation_report.json'),
                max_length=p40.MAX_LENGTH,diagnostic_callback=consume)
        finally:
            os.chdir(original_cwd)
    output_status={'status':'CAPTURED','captured_now':len(needed),
        'completed_before':len(completed),'remaining':pending-len(needed),
        'candidate_policy':candidate_policy}
    if compare_capture is not None:
        # Comparison is against immutable, hashed, previously captured graphs.
        # No shard of the original capture is ever edited or overwritten.
        old=FeatureCorpus(plan_dir,compare_capture,'train',require_complete=False)
        new=FeatureCorpus(plan_dir,output,'train',require_complete=False)
        expected_prior='legacy' if candidate_policy=='spatial' else 'spatial'
        if old.config.get('candidate_policy')!=expected_prior:
            refuse('PILOT_COMPARISON_PARENT_POLICY_MISMATCH')
        if not old.pairs or len(new.pairs)!=len(old.pairs) or len(old.pairs)!=SHARD_PAIRS:
            refuse('PILOT_PAIR_COUNT_MISMATCH')
        comparison=[];causes=Counter();origins=Counter();relations=Counter()
        recovered_edges=0;regressed_edges=0;persistent_missing=0
        prior_causes=Counter();prior_origins=Counter();prior_relations=Counter()
        for i,((_,_,oa,ob),(_,_,na,nb)) in enumerate(zip(old.pairs,new.pairs)):
            if (oa['pair_id'],ob['pair_id'])!=(na['pair_id'],nb['pair_id']):
                refuse('PILOT_PAIR_ID_DRIFT')
            for side,(before,after) in enumerate(((oa,na),(ob,nb))):
                dia=after.get('binding_diagnosis')
                if not isinstance(dia,dict):refuse('PILOT_DIAGNOSIS_ABSENT')
                prior=after.get('binding_diagnosis_before')
                if not isinstance(prior,dict):refuse('PILOT_BASELINE_DIAGNOSIS_ABSENT')
                if prior['missing_edges']!=before['missing_candidate_gold_edges']:
                    refuse('PILOT_BASELINE_REPLAY_DIFFERS_FROM_COMMITTED_SHARD')
                def edge_key(case):
                    return (case['type'],tuple(case['src_span']),tuple(case['dst_span']),
                            case['src_kind'],case['dst_kind'])
                before_set={edge_key(case) for case in prior['cases']}
                after_set={edge_key(case) for case in dia['cases']}
                if len(before_set)!=prior['missing_edges'] or len(after_set)!=dia['missing_edges']:
                    refuse('PILOT_DUPLICATE_MISSING_EDGE_DIAGNOSIS')
                old_diagnosis=before.get('binding_diagnosis')
                if expected_prior=='spatial' and not isinstance(old_diagnosis,dict):
                    refuse('PILOT_BASELINE_EDGE_DIAGNOSIS_ABSENT')
                if isinstance(old_diagnosis,dict) and (
                    {edge_key(case) for case in old_diagnosis['cases']}!=before_set):
                    refuse('PILOT_BASELINE_EDGE_ID_DRIFT')
                repaired=before_set-after_set
                introduced=after_set-before_set
                retained=after_set&before_set
                recovered_edges+=len(repaired)
                regressed_edges+=len(introduced)
                persistent_missing+=len(retained)
                prior_causes.update(prior['by_cause']);prior_origins.update(prior['by_origin'])
                prior_relations.update(prior['by_relation'])
                causes.update(dia['by_cause']);origins.update(dia['by_origin'])
                relations.update(dia['by_relation'])
                comparison.append({'pair_index':i,'side':side,'family':after['family'],
                    'distractor_count':after['distractor_count'],
                    'before_missing':before['missing_candidate_gold_edges'],
                    'after_missing':after['missing_candidate_gold_edges'],
                    'recovered_edges':len(repaired),'introduced_errors':len(introduced),
                    'persistent_missing':len(retained),
                    'causes':dia['by_cause'],'origin':dia['by_origin'],
                    'before_causes':prior['by_cause'],'before_origin':prior['by_origin']})
        result={'schema':SCHEMA,'status':'LABEL_BLIND_CANDIDATE_PILOT_AUDITED',
            'policy':candidate_policy,'pairs':len(new.pairs),
            'baseline_reachable_pairs':old.reachable_pairs,
            'candidate_reachable_pairs':new.reachable_pairs,
            'baseline_missing_edges':sum(a['missing_candidate_gold_edges']+b['missing_candidate_gold_edges'] for _,_,a,b in old.pairs),
            'candidate_missing_edges':sum(a['missing_candidate_gold_edges']+b['missing_candidate_gold_edges'] for _,_,a,b in new.pairs),
            'recovered_edges':recovered_edges,'regressed_edges':regressed_edges,
            'persistent_missing_edges':persistent_missing,
            'baseline_missing_by_cause':dict(prior_causes),
            'baseline_missing_by_origin':dict(prior_origins),
            'baseline_missing_by_relation':dict(prior_relations),
            'missing_by_cause':dict(causes),'missing_by_origin':dict(origins),
            'missing_by_relation':dict(relations),'cases':comparison,
            'pilot_reachability_floor_passed':new.reachability>=.98 and regressed_edges==0,
            'training_coverage_gate_passed':False,
            'production_promotion':False,'sealed_test_used':False}
        atomic_json(output/'pilot-comparison.json',result)
        print('GAMER_G2_0059_PILOT_COMPARISON '+json.dumps({k:v for k,v in result.items() if k!='cases'},sort_keys=True),flush=True)
        output_status['pilot_comparison']=result
    return output_status


class FeatureCorpus:
    """Bounded-memory, integrity checked pair streamer; no Qwen model loaded."""
    def __init__(self,plan_dir,capture_dir,split,*,cache_shards=2,require_complete=True):
        if split not in SPLITS or not 1<=cache_shards<=16:refuse('DATASET_CONFIGURATION')
        self.plan=_require_plan(plan_dir)
        self.plan_dir=Path(plan_dir);self.folder=Path(capture_dir)
        config=json.loads((self.folder/'capture.json').read_text(encoding='utf-8'))
        if (config.get('schema')!=SCHEMA or
           config.get('plan_sha256')!=file_sha(self.plan_dir/'plan.json') or
           config.get('production_promotion') is not False):
            refuse('FEATURE_PLAN_OR_LINEAGE_DRIFT')
        self.config=config;self.split=split;self.max_cache=cache_shards
        self.cache=OrderedDict();self.shards=[];self.pairs=[]
        receipt_stream=iter(_read_receipts(plan_dir,split))
        shard_pairs=config['shard_pairs'];total=self.plan['splits'][split]['pairs']
        for start in range(0,total,shard_pairs):
            size=min(shard_pairs,total-start)
            expected_receipts=[]
            for _ in range(size):
                try:expected_receipts.append(next(receipt_stream))
                except StopIteration:refuse('CAPTURE_PLAN_PAIR_COUNT_SHORT')
            f,m,l=_shard_paths(self.folder,split,start)
            if not m.exists():
                if require_complete:refuse('CAPTURE_INCOMPLETE_'+split+'_'+str(start))
                continue
            info=verify_shard(self.folder,split,start,size)
            entries=info['entries']
            if (len(entries)!=info['pair_count']*2 or
                any(entries[2*i]['pair_id']!=entries[2*i+1]['pair_id'] or
                    entries[2*i]['side']!=0 or entries[2*i+1]['side']!=1
                    for i in range(info['pair_count']))):
                refuse('PAIR_SHARD_CORRUPT')
            self.shards.append((start,info))
            for pair_idx in range(info['pair_count']):
                a,b=entries[2*pair_idx:2*pair_idx+2]
                expected=expected_receipts[pair_idx]
                if (a['pair_id']!=expected['id'] or
                    (a['prompt_sha256'],b['prompt_sha256'])!=tuple(expected['sha256']) or
                    a['topology_hash']!=expected['topology_hash'] or
                    a['family']!=expected['family'] or a['mode']!=expected['mode']):
                    refuse('CAPTURE_VS_PLAN_PROMPT_OR_STRUCTURE_DRIFT')
                if (a['family']!=b['family'] or a['mode']!=b['mode'] or
                    a['topology_hash']!=b['topology_hash']):
                    refuse('PAIRED_METADATA_DRIFT')
                self.pairs.append((start,pair_idx,a,b))
        try:
            next(receipt_stream)
        except StopIteration:pass
        else:refuse('CAPTURE_PLAN_PAIR_COUNT_EXCESS')
        if require_complete and len(self.pairs)!=total:refuse('PAIR_CORPUS_INCOMPLETE')
        self.reachable_pairs=sum(a['candidate_reachable'] and b['candidate_reachable']
                                 for _,_,a,b in self.pairs)
        self.reachability=self.reachable_pairs/len(self.pairs) if self.pairs else 0
        self.indices=[i for i,(_,_,a,b) in enumerate(self.pairs)
                      if a['candidate_reachable'] and b['candidate_reachable']]

    def _tensors(self,start):
        if start in self.cache:
            self.cache.move_to_end(start)
            return self.cache[start]
        f,m,l=_shard_paths(self.folder,self.split,start)
        # On every cache miss, verify both files against their committed hashes.
        verify_shard(self.folder,self.split,start)
        features=load_file(str(f),device='cpu')
        labels=load_file(str(l),device='cpu')
        # Inference features are explicitly prevented from storing labels.
        if any('positive' in key or 'truth' in key for key in features):
            refuse('FEATURE_LEAKAGE')
        self.cache[start]=(features,labels)
        if len(self.cache)>self.max_cache:self.cache.popitem(last=False)
        return self.cache[start]

    def pair(self,index,*,with_labels=True):
        if not 0<=index<len(self.pairs):refuse('PAIR_LOOKUP_INDEX')
        start,offset,a,b=self.pairs[index]
        feat,labels=self._tensors(start)
        result=[]
        for side,meta in enumerate((a,b)):
            prefix=f'{offset*2+side}.'
            sample={k:feat[prefix+k] for k in FEATURE_FIELDS}
            sample.update({'section':self.split,'prompt_sha256':meta['prompt_sha256'],
                'pair':meta['pair_id'],'family':meta['family'],'mode':meta['mode'],
                'edge_count':meta['edge_count']})
            if with_labels:
                sample['positive']=labels[prefix+'positive']
                sample['truth_count']=meta['truth_count']
            if self.split=='train' and with_labels:
                if not isinstance(meta['training_node_ids'],list):
                    refuse('TRAIN_CANONICAL_ALIGNMENT_MISSING')
                sample['training_node_ids']=tuple(meta['training_node_ids'])
            elif self.split!='train' and meta['training_node_ids'] is not None:
                # TRAIN features may retain separate supervision metadata,
                # but a feature-only TRAIN read must not return it.  Held-out
                # splits can never contain canonical author IDs at all.
                refuse('HELDOUT_AUTHOR_ID_LEAKAGE')
            result.append(sample)
        return (result[0],result[1]),{'id':a['pair_id'],
            'sha256':(a['prompt_sha256'],b['prompt_sha256']),
            'family':a['family'],'mode':a['mode']}

    def batches(self,epoch,batch_pairs):
        """Shuffle shard order and within-shard pairs; cap active shards in RAM."""
        if not 1<=batch_pairs<=8:refuse('BATCH_SIZE')
        by_shard={}
        for i in self.indices:by_shard.setdefault(self.pairs[i][0],[]).append(i)
        rng=random.Random(int(sha(f'{SCHEMA}:{self.plan["seed"]}:epoch:{epoch}')[:15],16))
        order=list(by_shard);rng.shuffle(order)
        current=[]
        for start in order:
            indices=by_shard[start][:];rng.shuffle(indices)
            for i in indices:
                current.append(i)
                if len(current)==batch_pairs:
                    yield current;current=[]
        if current:yield current


def coverage_audit(plan_dir,capture_dir,*,require_complete=True):
    plan=_require_plan(plan_dir)
    results={}
    parent_sha=None
    for split in SPLITS:
        ds=FeatureCorpus(plan_dir,capture_dir,split,require_complete=require_complete)
        if parent_sha is None:parent_sha=ds.config['parent57_report_sha256']
        if ds.config['parent57_report_sha256']!=parent_sha:
            refuse('PARENT_SPLIT_DRIFT')
        bycell=Counter()
        byshape=Counter()
        shape_quality={}
        missing_edges=0
        for _,_,a,b in ds.pairs:
            key=f"{a['family']}:{a['dimensions']}:{a['distractor_count']}"
            scores=shape_quality.setdefault(key,Counter())
            scores['pairs']+=1
            missing_edges+=a['missing_candidate_gold_edges']+b['missing_candidate_gold_edges']
            if a['candidate_reachable'] and b['candidate_reachable']:
                scores['both_reachable']+=1
                bycell[a['family']+':'+a['mode']]+=1
                byshape[a['topology_hash']]+=1
        results[split]={'pairs_captured':len(ds.pairs),
            'pairs_planned':plan['splits'][split]['pairs'],
            'both_graphs_candidate_reachable':ds.reachable_pairs,
            'pair_reachability':round(ds.reachability,6),
            'reachable_pairs_per_cell':dict(bycell),
            'reachable_topologies':len(byshape),
            'missing_candidate_gold_edges':missing_edges,
            'shape_coverage':{key:dict(value) for key,value in sorted(shape_quality.items())},
            'complete':len(ds.pairs)==plan['splits'][split]['pairs']}
    ready=all(results[s]['complete'] and results[s]['pair_reachability']>=.98
              for s in ('train','validation'))
    return {'schema':SCHEMA,'results':results,
        'training_coverage_gate_passed':ready,
        'pilot_only':not all(results[s]['complete'] for s in SPLITS),
        'parent57_sha256':parent_sha,'sealed_test_used':False,
        'production_promotion':False}



def _verify_evidence_pilot(plan_dir,pilot_dir):
    """Bind authorization to the REAL checksum-verified, complete 16-pair pilot.

    The prior comparison is a report, not a source of model-facing labels.
    We independently verify its feature/label shard and every plan receipt.
    A changed pilot or frozen ancestor invalidates the authorization.
    """
    pilot_dir=Path(pilot_dir).resolve()
    evidence=FeatureCorpus(plan_dir,pilot_dir,'train',require_complete=False)
    if evidence.config.get('candidate_policy')!='evidence':
        refuse('ROLLOUT_PILOT_POLICY')
    if evidence.config['shard_pairs']!=SHARD_PAIRS or len(evidence.pairs)!=ROLLOUT_PILOT_PAIRS:
        refuse('ROLLOUT_PILOT_NOT_EXACTLY_ONE_SHARD')
    if evidence.reachable_pairs!=ROLLOUT_PILOT_PAIRS or any(
        a['missing_candidate_gold_edges'] or b['missing_candidate_gold_edges']
        for _,_,a,b in evidence.pairs):
        refuse('ROLLOUT_PILOT_UNREACHABLE')
    for split in ('validation','test','challenge'):
        if FeatureCorpus(plan_dir,pilot_dir,split,require_complete=False).pairs:
            refuse('ROLLOUT_PILOT_HELDOUT_CAPTURE')
    proof_file=pilot_dir/'pilot-comparison.json'
    proof=json.loads(proof_file.read_text(encoding='utf-8'))
    if (proof.get('schema')!=SCHEMA or proof.get('policy')!='evidence'
            or proof.get('status')!='LABEL_BLIND_CANDIDATE_PILOT_AUDITED'
            or proof.get('pairs')!=ROLLOUT_PILOT_PAIRS
            or proof.get('candidate_reachable_pairs')!=ROLLOUT_PILOT_PAIRS
            or proof.get('candidate_missing_edges')!=0
            or proof.get('regressed_edges')!=0
            or proof.get('persistent_missing_edges')!=0
            or not proof.get('pilot_reachability_floor_passed')
            or proof.get('production_promotion') is not False
            or proof.get('sealed_test_used') is not False):
        refuse('ROLLOUT_PILOT_COMPARISON_REJECTED')
    # The pilot is a valid result only if it includes a nontrivial repair.
    if (proof.get('baseline_missing_edges',0)<=0 or
        proof.get('recovered_edges')!=proof['baseline_missing_edges']):
        refuse('ROLLOUT_PILOT_NO_MEASURED_RECOVERY')
    config=evidence.config
    hashes=config.get('capture_source_hashes',{})
    for name,module in (('graph_author',corpus),('legacy_frozen_runner',p40),
                        ('counterfactual_label_contract',p50),('g2_0052',p52),
                        ('frozen_occurrence_encoder',d48),('g2_features',g53),
                        ('training_pair_alignment',p57)):
        if hashes.get(name)!=file_sha(Path(module.__file__)):
            refuse('ROLLOUT_PILOT_DEPENDENCY_CHANGED_'+name)
    # The current G2-0059 command intentionally has a newer code hash than
    # the already accepted pilot. Frozen-model and input code MAY NOT drift.
    _,metadata,_=_shard_paths(pilot_dir,'train',0)
    digest=sha(canonical_json({'report':file_sha(proof_file),
        'capture_config':file_sha(pilot_dir/'capture.json'),
        'shard_metadata':file_sha(metadata)}))
    return {'proof_sha256':digest,
        'parent57_report_sha256':config['parent57_report_sha256'],
        'frozen_encoder_sha256':config['frozen_encoder_sha256']}


def _rollout_cohort(ds,start,count,*,minimum_topologies=None):
    """Audit a disjoint pair-index interval, never including pilot examples."""
    if minimum_topologies is None:minimum_topologies=ROLLOUT_MIN_TOPOLOGIES
    stop=start+count
    indexed={shard_start+offset:(a,b) for shard_start,offset,a,b in ds.pairs}
    data=[]
    for i in range(start,stop):
        if i not in indexed:
            continue
        a,b=indexed[i]
        if a['side']!=0 or b['side']!=1 or a['pair_id']!=b['pair_id']:
            refuse('ROLLOUT_PAIR_DRIFT')
        data.append((a,b))
    complete=len(data)==count
    families=Counter()
    reachable_cells=Counter()
    types=Counter()
    distractors=Counter()
    reachable_distractors=Counter()
    missing=0
    topology=set()
    for a,b in data:
        cell=a['family']+':'+a['mode']
        families[cell]+=1
        topology.add(a['topology_hash'])
        bucket='with_distractors' if a['distractor_count'] else 'without_distractors'
        distractors[bucket]+=1
        missing+=a['missing_candidate_gold_edges']+b['missing_candidate_gold_edges']
        if a['candidate_reachable'] and b['candidate_reachable']:
            reachable_cells[cell]+=1
            reachable_distractors[bucket]+=1
    reach=sum(reachable_cells.values())
    ratio=reach/count if complete else None
    min_cells=(set(f+':'+m for f,m in CELLS)==set(families)
        and all(families[c]>=ROLLOUT_MIN_CELL_PAIRS for c in families))
    failed=[]
    if not complete:failed.append('INCOMPLETE_CAPTURE')
    if complete and ratio<ROLLOUT_MIN_REACHABILITY:
        failed.append('OVERALL_REACHABILITY_BELOW_98_PERCENT')
    if len(topology)<minimum_topologies:
        failed.append('INSUFFICIENT_DISTINCT_TOPOLOGIES')
    if not min_cells:failed.append('MISSING_OR_UNDERSIZED_FAMILY_MODE_CELL')
    for c in sorted(families):
        if reachable_cells[c]/families[c]<ROLLOUT_MIN_CELL_REACHABILITY:
            failed.append('FAMILY_MODE_REACHABILITY:'+c)
    for c,total in sorted(distractors.items()):
        if (total>=ROLLOUT_MIN_CELL_PAIRS and
                reachable_distractors[c]/total<ROLLOUT_MIN_CELL_REACHABILITY):
            failed.append('DISTRACTOR_REACHABILITY:'+c)
    valid=not failed
    return {'pair_start':start,'pairs_expected':count,'pairs_captured':len(data),
        'complete':complete,'both_graphs_reachable':reach,
        'pair_reachability':round(ratio,6) if ratio is not None else None,
        'missing_candidate_gold_edges':missing,'distinct_topologies':len(topology),
        'cell_counts':dict(families),'reachable_by_cell':dict(reachable_cells),
        'distractor_counts':dict(distractors),'reachable_by_distractor':dict(reachable_distractors),
        'gate_passed':bool(valid),'failed_checks':failed}


def _rollout_gate_record(path,report):
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=report:
            refuse('ROLLOUT_GATE_REPORT_DRIFT')
    else:
        atomic_json(path,report)


def rollout(plan_dir,capture_dir,pilot_dir,*,root,device='cuda',
            stage='canary',max_shards=None):
    """Controlled stage transitions; fails shut on incomplete/weak cohorts.

    No cohort ever uses the held-out test/challenge labels as a gate.
    No previously accepted features or the original pilot are rewritten.
    """
    if stage not in ('canary','qualification','full'):
        refuse('ROLLOUT_STAGE')
    plan_dir=Path(plan_dir).resolve();capture_dir=Path(capture_dir).resolve()
    pilot_dir=Path(pilot_dir).resolve()
    if capture_dir==pilot_dir or capture_dir==plan_dir:
        refuse('ROLLOUT_OUTPUT_MUST_BE_SEPARATE')
    plan=_require_plan(plan_dir)
    proof=_verify_evidence_pilot(plan_dir,pilot_dir)
    min_train=ROLLOUT_PILOT_PAIRS+ROLLOUT_CANARY_NEW_PAIRS+ROLLOUT_QUALIFICATION_NEW_PAIRS
    if (plan['splits']['train']['pairs']<min_train or
        plan['splits']['validation']['pairs']<ROLLOUT_MIN_CELL_PAIRS*4):
        refuse('ROLLOUT_PLAN_TOO_SMALL')
    if (min_train%SHARD_PAIRS or ROLLOUT_CANARY_NEW_PAIRS%SHARD_PAIRS or
            ROLLOUT_QUALIFICATION_NEW_PAIRS%SHARD_PAIRS):
        refuse('ROLLOUT_SIZES_REQUIRE_COMPLETE_SHARDS')
    train_canary_stop=ROLLOUT_PILOT_PAIRS+ROLLOUT_CANARY_NEW_PAIRS
    train_qualification_stop=train_canary_stop+ROLLOUT_QUALIFICATION_NEW_PAIRS
    # Early stages cannot touch test/challenge, even for feature capture.
    if stage=='canary':
        desired={'train':train_canary_stop}
    elif stage=='qualification':
        desired={'train':train_qualification_stop,
                 'validation':plan['splits']['validation']['pairs']}
    else:
        desired={split:plan['splits'][split]['pairs'] for split in SPLITS}
    allowed={(split,start) for split,count in desired.items()
             for start in range(0,count,SHARD_PAIRS)}
    canary=None;qualification=None;validation=None
    if capture_dir.exists() and (capture_dir/'capture.json').exists():
        train_ds=FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
        canary=_rollout_cohort(train_ds,ROLLOUT_PILOT_PAIRS,
                              ROLLOUT_CANARY_NEW_PAIRS)
        if stage!='canary' and not canary['gate_passed']:
            refuse('ROLLOUT_CANARY_GATE_NOT_PASSED')
        if stage=='full':
            qualification=_rollout_cohort(train_ds,train_canary_stop,
                                           ROLLOUT_QUALIFICATION_NEW_PAIRS)
            validation=_rollout_cohort(FeatureCorpus(plan_dir,capture_dir,
                'validation',require_complete=False),0,
                plan['splits']['validation']['pairs'])
            if not qualification['gate_passed'] or not validation['gate_passed']:
                refuse('ROLLOUT_QUALIFICATION_GATE_NOT_PASSED')
    elif stage!='canary':
        refuse('ROLLOUT_CANARY_GATE_NOT_PASSED')
    result=capture(plan_dir,capture_dir,root=root,device=device,
                   shard_pairs=SHARD_PAIRS,max_shards=max_shards,
                   candidate_policy='evidence',only_shards=allowed,
                   evidence_proof=proof)
    train_ds=FeatureCorpus(plan_dir,capture_dir,'train',require_complete=False)
    canary=_rollout_cohort(train_ds,ROLLOUT_PILOT_PAIRS,ROLLOUT_CANARY_NEW_PAIRS)
    if canary['complete']:
        _rollout_gate_record(capture_dir/'rollout-canary-gate.json',canary)
    if stage in ('qualification','full'):
        qualification=_rollout_cohort(train_ds,train_canary_stop,
                                       ROLLOUT_QUALIFICATION_NEW_PAIRS)
        validation=_rollout_cohort(FeatureCorpus(plan_dir,capture_dir,'validation',
            require_complete=False),0,plan['splits']['validation']['pairs'])
        if qualification['complete']:
            _rollout_gate_record(capture_dir/'rollout-qualification-train-gate.json',qualification)
        if validation['complete']:
            _rollout_gate_record(capture_dir/'rollout-qualification-validation-gate.json',validation)
    full_coverage=None
    if stage=='canary':
        complete=canary['complete']
        passed=canary['gate_passed']
    elif stage=='qualification':
        complete=qualification['complete'] and validation['complete']
        passed=(canary['gate_passed'] and qualification['gate_passed']
                and validation['gate_passed'])
    else:
        # Completion of qualification is NOT completion of the full corpus.
        # Only a complete training+validation corpus can satisfy training's
        # existing 98% gate. Test/challenge are checked for completeness only;
        # their labels never influence model or policy selection here.
        full_coverage=coverage_audit(plan_dir,capture_dir,require_complete=False)
        complete=all(full_coverage['results'][split]['complete'] for split in SPLITS)
        passed=(complete and canary['gate_passed'] and qualification['gate_passed']
                and validation['gate_passed'] and
                full_coverage['training_coverage_gate_passed'])
    final_status=('CAPTURE_IN_PROGRESS' if not complete else
                  'GATE_PASSED' if passed else 'GATE_FAILED')
    return {'schema':SCHEMA,'stage':stage,'status':final_status,
        'capture':result,'canary':canary,'qualification_train':qualification,
        'qualification_validation':validation,'full_coverage':full_coverage,
        'pilot_proof_sha256':proof['proof_sha256'],
        'next_stage':('qualification' if stage=='canary' and passed else
                      'full' if stage=='qualification' and passed else None),
        'sealed_test_used':False,'production_promotion':False}

def _model_from_parent(root,*,capture_config=None):
    path=Path(root)/'graph-g2-0057-counterfactual-bakeoff'
    base,hashes,report=p58.verify_parent57(path)
    if capture_config is not None:
        if capture_config['parent57_report_sha256']!=file_sha(path/p57.REPORT):
            refuse('CAPTURE_TO_MODEL_PARENT_DRIFT')
    model=p56._load_state(base)
    if any(not torch.isfinite(v).all() for v in model.state_dict().values()):
        refuse('PARENT_NONFINITE')
    return model,file_sha(path/p57.REPORT)


def eval_stream(model,ds,*,max_pairs=None):
    """Validation/test only, bounded to one cached shard pair at a time."""
    model.eval();total=Counter();families={f:Counter() for f in ('allocation','encounter')}
    modes={m:Counter() for m in ('renaming','role_swap')}
    if max_pairs is not None and max_pairs<1:refuse('EVAL_COUNT')
    count=min(len(ds.pairs),max_pairs) if max_pairs else len(ds.pairs)
    with torch.inference_mode():
        for index in range(count):
            samples,receipt=ds.pair(index,with_labels=True)
            all_exact=True
            for sample in samples:
                # Gold is never passed to the inference function.
                prediction=p56._infer(model,sample)
                gold=set(sample['positive'].tolist())
                tp=len(prediction&gold)
                entry={'exact':int(prediction==gold and len(gold)==sample['truth_count']),
                    'true_positive':tp,'gold_edges':sample['truth_count'],
                    'predicted_edges':len(prediction),
                    'missing':sample['truth_count']-tp,
                    'extra':len(prediction-gold),
                    'count_error_l1':abs(sample['truth_count']-len(prediction)),
                    'candidate_reachable':int(len(gold)==sample['truth_count']),
                    'graphs':1}
                total.update(entry);families[receipt['family']].update(entry)
                modes[receipt['mode']].update(entry)
                all_exact=all_exact and entry['exact']==1
            total['pairs']+=1;total['exact_pairs']+=int(all_exact)
    def fmt(c):
        out=dict(c)
        out['edge_recall']=round(c['true_positive']/c['gold_edges'],6) if c['gold_edges'] else 0.
        out['edge_precision']=round(c['true_positive']/c['predicted_edges'],6) if c['predicted_edges'] else 0.
        return out
    return {**fmt(total),'families':{k:fmt(v) for k,v in families.items()},
        'modes':{k:fmt(v) for k,v in modes.items()}}


def _write_optimizer(path,opt):
    path=Path(path)
    if path.exists():refuse('OPTIMIZER_FILE_EXISTS')
    fd,tmp=tempfile.mkstemp(prefix='.g2-0059-',suffix='.pt',dir=path.parent)
    os.close(fd)
    try:
        torch.save(opt.state_dict(),tmp)
        if path.exists():refuse('OPTIMIZER_FILE_RACE')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _ckpt_files(output,step):
    stem=f'checkpoint-{step:09d}'
    return (Path(output)/f'{stem}.safetensors',
            Path(output)/f'{stem}.optimizer.pt',Path(output)/f'{stem}.json')


def _save_checkpoint(output,step,model,opt,*,epoch,next_batch,metrics):
    weights,optimizer,receipt=_ckpt_files(output,step)
    if receipt.exists():refuse('CHECKPOINT_REWRITE')
    # Uncommitted crashed writes are disposable only if no receipt exists.
    for orphan in (weights,optimizer):
        if orphan.exists():orphan.unlink()
    atomic_tensors(weights,p56._copy_state(model))
    _write_optimizer(optimizer,opt)
    info={'schema':SCHEMA,'step':step,'epoch':epoch,'next_batch':next_batch,
        'validation':metrics,'weights_sha256':file_sha(weights),
        'optimizer_sha256':file_sha(optimizer)}
    atomic_json(receipt,info)
    return info


def _checkpoints(output):
    result=[]
    for receipt in sorted(Path(output).glob('checkpoint-*.json')):
        info=json.loads(receipt.read_text(encoding='utf-8'))
        weights,opt,meta=_ckpt_files(output,info['step'])
        if (meta!=receipt or info['schema']!=SCHEMA or
            file_sha(weights)!=info['weights_sha256'] or
            file_sha(opt)!=info['optimizer_sha256']):
            refuse('CHECKPOINT_HASH_OR_STRUCTURE_DRIFT')
        result.append(info)
    if any(a['step']>=b['step'] for a,b in zip(result,result[1:])):
        refuse('CHECKPOINT_ORDER')
    return result


def _validation_quality(metrics,step):
    return (metrics['exact'],-metrics['missing']-metrics['extra'],
            metrics['exact_pairs'],-metrics['missing'],-step)


def training_plan(plan_dir,capture_dir,*,epochs,batch_pairs,limit_training_graphs=None):
    if not 1<=epochs<=100 or not 1<=batch_pairs<=8:refuse('TRAINING_CONFIGURATION')
    ds=FeatureCorpus(plan_dir,capture_dir,'train')
    vd=FeatureCorpus(plan_dir,capture_dir,'validation')
    total_graphs=ds.plan['splits']['train']['graphs']
    limit=total_graphs if limit_training_graphs is None else limit_training_graphs
    if (type(limit) is not int or limit<16 or limit%8 or limit>total_graphs):
        refuse('TRAINING_SCALE_LIMIT_MUST_BE_MULTIPLE_OF_8')
    eligible=[i for i in ds.indices if i<limit//2]
    selected_reachability=len(eligible)/(limit//2)
    if selected_reachability<.98 or vd.reachability<.98:
        refuse(f'FROZEN_CANDIDATE_COVERAGE_BELOW_98_PERCENT:{selected_reachability:.4f}:{vd.reachability:.4f}')
    if len(eligible)<8 or len(vd.indices)<4:refuse('INSUFFICIENT_REACHABLE_PAIRS')
    # A scale ladder is a strictly nested prefix of the same four-cell,
    # counterfactual-pair-complete dataset; no reused validation/test data.
    cell_counts=Counter()
    for i in eligible:
        _,_,a,b=ds.pairs[i];cell_counts[a['family']+':'+a['mode']]+=1
    if len(cell_counts)!=4 or max(cell_counts.values())-min(cell_counts.values())>max(1,limit//100):
        refuse('TRAIN_CELL_BALANCE_AFTER_COVERAGE_FILTER')
    per_epoch=math.ceil(len(eligible)/batch_pairs)
    return {'graphs_captured':total_graphs,
        'requested_training_graphs':limit,
        'reachable_train_graphs':len(eligible)*2,
        'reachable_pairs_by_cell':dict(cell_counts),
        'validation_graphs':len(vd.pairs)*2,
        'steps_per_epoch':per_epoch,'total_optimizer_steps':per_epoch*epochs,
        'graph_presentations':len(eligible)*2*epochs,
        'epochs':epochs,'batch_pairs':batch_pairs,
        'candidate_coverage_minimum':.98}


def train(plan_dir,capture_dir,output,*,root,device='cuda',epochs=4,
          batch_pairs=2,checkpoint_every=None,max_new_steps=None,
          learning_rate=p55.LR,limit_training_graphs=None):
    """Separate invocation: neither TEST nor CHALLENGE is opened or scored."""
    if device not in ('cpu','cuda') or device=='cuda' and not torch.cuda.is_available():
        refuse('TRAIN_DEVICE_UNAVAILABLE')
    if not 0<learning_rate<=.005 or max_new_steps is not None and max_new_steps<1:
        refuse('TRAIN_BUDGET')
    budget=training_plan(plan_dir,capture_dir,epochs=epochs,batch_pairs=batch_pairs,
                         limit_training_graphs=limit_training_graphs)
    ds=FeatureCorpus(plan_dir,capture_dir,'train')
    ds.indices=[i for i in ds.indices if i<budget['requested_training_graphs']//2]
    vd=FeatureCorpus(plan_dir,capture_dir,'validation')
    config=json.loads((Path(capture_dir)/'capture.json').read_text(encoding='utf-8'))
    model,source_hash=_model_from_parent(root,capture_config=config)
    model=model.to(device)
    opt=torch.optim.AdamW(model.parameters(),lr=learning_rate)
    checkpoint_every=checkpoint_every or budget['steps_per_epoch']
    if checkpoint_every<1:refuse('CHECKPOINT_INTERVAL')
    task={'schema':SCHEMA,'training_budget':budget,
        'plan_sha256':config['plan_sha256'],
        'capture_config_sha256':file_sha(Path(capture_dir)/'capture.json'),
        'parent57_sha256':source_hash,'device':device,'learning_rate':learning_rate,
        'checkpoint_every':checkpoint_every,
        'pinned_training_source':{name:file_sha(Path(module.__file__)) for name,module in
            (('pipeline',__import__(__name__,fromlist=['main'])),('set_predictor',p55),
             ('paired_objective',p57),('scale_eval',p56))},
        'torch_version':str(torch.__version__),
        'objective':'0057_D_combined_paired_supervision_with_new_diverse_data',
        'production_promotion':False,'sealed_test_used':False}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    cfg_path=output/'training.json'
    if cfg_path.exists():
        if json.loads(cfg_path.read_text(encoding='utf-8'))!=task:refuse('TRAINING_CONFIG_DRIFT')
    else:atomic_json(cfg_path,task)
    if (output/'selection.json').exists():refuse('TRAINING_ALREADY_COMPLETE')
    saved=_checkpoints(output)
    if saved:
        last=saved[-1]
        weights,optim,_=_ckpt_files(output,last['step'])
        model.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
        opt.load_state_dict(torch.load(optim,map_location=device,weights_only=True))
        epoch_start=last['epoch'];batch_start=last['next_batch'];global_step=last['step']
    else:
        epoch_start=batch_start=global_step=0
        # Step zero is persisted, so resume always has an unambiguous start.
        initial=eval_stream(model,vd)
        _save_checkpoint(output,0,model,opt,epoch=0,next_batch=0,metrics=initial)
    new_steps=0
    for epoch in range(epoch_start,epochs):
        for batch_index,indices in enumerate(ds.batches(epoch,batch_pairs)):
            if epoch==epoch_start and batch_index<batch_start:continue
            pairs=[];rows={}
            for ix in indices:
                (a,b),receipt=ds.pair(ix)
                pairs.append(receipt)
                rows[a['prompt_sha256']]=a;rows[b['prompt_sha256']]=b
            model.train();opt.zero_grad(set_to_none=True)
            loss=p57.batch_objective(model,pairs,rows,'D_combined')
            if not bool(torch.isfinite(loss)):refuse('NONFINITE_TRAINING_LOSS')
            loss.backward()
            norm=torch.nn.utils.clip_grad_norm_(model.parameters(),5.0)
            if not bool(torch.isfinite(norm)):refuse('NONFINITE_GRADIENT')
            opt.step();global_step+=1;new_steps+=1
            completed_epoch=batch_index+1==budget['steps_per_epoch']
            should_save=global_step%checkpoint_every==0 or completed_epoch
            if should_save or (max_new_steps is not None and new_steps>=max_new_steps):
                model.eval();validation=eval_stream(model,vd)
                next_epoch,next_batch=(epoch+1,0) if completed_epoch else (epoch,batch_index+1)
                _save_checkpoint(output,global_step,model,opt,epoch=next_epoch,
                                 next_batch=next_batch,metrics=validation)
                print('GAMER_G2_0059_TRAIN '+json.dumps({'step':global_step,
                    'epoch':next_epoch,'validation_exact':validation['exact'],
                    'validation_recall':validation['edge_recall'],
                    'validation_missing':validation['missing'],'loss':round(float(loss.detach()),6)},
                    sort_keys=True),flush=True)
            if max_new_steps is not None and new_steps>=max_new_steps:
                return {'status':'PAUSED_RESTARTABLE','global_step':global_step,
                    'remaining_steps':budget['total_optimizer_steps']-global_step}
    if global_step!=budget['total_optimizer_steps']:refuse('TRAINING_STEP_COUNT')
    history=_checkpoints(output)
    selected=max(history,key=lambda row:_validation_quality(row['validation'],row['step']))
    record={'schema':SCHEMA,'status':'RESEARCH_TRAINING_COMPLETE_NOT_CERTIFIED',
        'selected_step':selected['step'],'selected_weights_sha256':selected['weights_sha256'],
        'validation':selected['validation'],
        'optimizer_steps':global_step,
        'cohort_graphs':budget['requested_training_graphs'],
        'selection_uses_test':False,'selection_uses_challenge':False,
        'production_promotion':False,'sealed_test_used':False}
    atomic_json(output/'selection.json',record)
    return record


def evaluate(plan_dir,capture_dir,train_dirs,output,*,root,device='cuda'):
    """Validation-only ladder selection, then ONE test + challenge evaluation.

    Every independent rung starts at the same G2-0057 parent. Test labels
    cannot influence either rung or checkpoint selection because the test
    corpus is opened only after the chosen checkpoint has been identified.
    """
    if device not in ('cpu','cuda') or device=='cuda' and not torch.cuda.is_available():
        refuse('EVALUATION_DEVICE')
    output=Path(output)
    if output.exists():refuse('FINAL_EVALUATION_ALREADY_EXISTS')
    config=json.loads((Path(capture_dir)/'capture.json').read_text(encoding='utf-8'))
    if isinstance(train_dirs,(str,Path)):train_dirs=[Path(train_dirs)]
    train_dirs=[Path(folder) for folder in train_dirs]
    if not train_dirs or len({str(p.resolve()) for p in train_dirs})!=len(train_dirs):
        refuse('LADDER_TRAINING_PATHS')
    candidates=[];rung_sizes=set()
    for train_dir in train_dirs:
        train_conf=json.loads((train_dir/'training.json').read_text(encoding='utf-8'))
        selected=json.loads((train_dir/'selection.json').read_text(encoding='utf-8'))
        history=_checkpoints(train_dir)
        if not history or history[-1]['step']!=train_conf['training_budget']['total_optimizer_steps']:
            refuse('INCOMPLETE_TRAINING')
        match=[x for x in history if x['step']==selected['selected_step']]
        if len(match)!=1 or match[0]['weights_sha256']!=selected['selected_weights_sha256']:
            refuse('SELECTION_HASH')
        if (train_conf['capture_config_sha256']!=file_sha(Path(capture_dir)/'capture.json')
            or train_conf['plan_sha256']!=config['plan_sha256']
            or train_conf['production_promotion'] is not False
            or selected['production_promotion'] is not False):
            refuse('TRAINING_CAPTURE_OR_PROMOTION_DRIFT')
        rung=train_conf['training_budget']['requested_training_graphs']
        if rung in rung_sizes:refuse('DUPLICATE_LADDER_RUNG')
        rung_sizes.add(rung)
        candidates.append({'folder':train_dir,'training_config':train_conf,
            'selected':selected,'rung':rung,
            'selection_hash':file_sha(train_dir/'selection.json')})
    parent_hashes={row['training_config']['parent57_sha256'] for row in candidates}
    if len(parent_hashes)!=1:refuse('LADDER_PARENT_MISMATCH')
    # Larger independent training runs compete on *the same* validation
    # cohort, never on test/challenge. Prefer the smaller rung on full ties.
    winning=max(candidates,key=lambda item:(
        _validation_quality(item['selected']['validation'],item['selected']['selected_step']),
        -item['rung']))
    baseline,source_hash=_model_from_parent(root,capture_config=config)
    if parent_hashes!={source_hash}:refuse('PARENT57_SOURCE_DRIFT')
    baseline=baseline.to(device).eval().requires_grad_(False)
    checkpoint=_ckpt_files(winning['folder'],winning['selected']['selected_step'])[0]
    trained=p56._load_state(load_file(str(checkpoint),device='cpu'))
    trained=trained.to(device).eval().requires_grad_(False)
    frozen_snap=p56._copy_state(trained)
    ladder=[{'training_graphs':item['rung'],
             'selected_step':item['selected']['selected_step'],
             'validation':item['selected']['validation'],
             'training_selection_sha256':item['selection_hash']}
            for item in sorted(candidates,key=lambda item:item['rung'])]
    final={}
    for split in ('test','challenge'):
        ds=FeatureCorpus(plan_dir,capture_dir,split)
        final[split]={'baseline_0057':eval_stream(baseline,ds),
                      'scaled_candidate':eval_stream(trained,ds)}
    if any(not torch.equal(value,trained.state_dict()[key].detach().cpu())
           for key,value in frozen_snap.items()):
        refuse('FINAL_MODEL_MUTATION')
    report={'schema':SCHEMA,'status':'RESEARCH_EVALUATED_NOT_CERTIFIED',
        'selection_basis':'validation_exact_graphs_then_edge_errors_then_pairs_then_earlier_checkpoint; smaller_rung_on_tie',
        'training_ladder':ladder,
        'selected_training_graphs':winning['rung'],
        'selected_step':winning['selected']['selected_step'],
        'weights_sha256':winning['selected']['selected_weights_sha256'],
        'capture_sha256':file_sha(Path(capture_dir)/'capture.json'),
        'training_selection_sha256':winning['selection_hash'],
        'test_and_structural_challenge':final,
        'gates':{'production_promotion':False,'sealed_test_used':False,
            'validation_selected_before_test':True,'structural_challenge_is_ood':True,
            'g2_certified':False}}
    output.mkdir(parents=True,exist_ok=False)
    atomic_json(output/'evaluation.json',report)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=(Path(__file__).resolve().parents[2]/'runtime/reports/gamer-v1'))
    sub=p.add_subparsers(dest='phase',required=True)
    plan=sub.add_parser('plan',help='Create immutable balanced, diversified split manifests')
    plan.add_argument('--output',type=Path)
    plan.add_argument('--train-graphs',type=int,default=10000)
    plan.add_argument('--validation-graphs',type=int,default=400)
    plan.add_argument('--test-graphs',type=int,default=400)
    plan.add_argument('--challenge-graphs',type=int,default=400)
    plan.add_argument('--minimum-topologies',type=int,default=24)
    plan.add_argument('--seed',type=int,default=SEED)
    cap=sub.add_parser('capture',help='Cache frozen Qwen/G1/G2-0052 tensors, resumably')
    cap.add_argument('--plan',type=Path)
    cap.add_argument('--output',type=Path)
    cap.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    cap.add_argument('--shard-pairs',type=int,default=SHARD_PAIRS)
    cap.add_argument('--max-shards',type=int)
    cap.add_argument('--candidate-policy',choices=('legacy','spatial','evidence'),default='legacy')
    cap.add_argument('--compare-capture',type=Path,
                     help='Audit same first 16 pairs against previously committed shard')
    rol=sub.add_parser('rollout',help='Gated evidence-policy capture on new, unseen cohorts')
    rol.add_argument('--plan',type=Path)
    rol.add_argument('--output',type=Path)
    rol.add_argument('--pilot',type=Path,help='The immutable 16-pair evidence-pilot capture')
    rol.add_argument('--stage',choices=('canary','qualification','full'),default='canary')
    rol.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    rol.add_argument('--max-shards',type=int,help='Limit this call; rerun to resume')
    actor=sub.add_parser('actor-bakeoff',help='G2-0060: frozen-G1 four-arm actor-selection bake-off')
    actor.add_argument('--plan',type=Path)
    actor.add_argument('--baseline',type=Path,help='Original immutable failed evidence canary')
    actor.add_argument('--output',type=Path)
    actor.add_argument('--stage',choices=('development','holdout'),default='development')
    actor.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    actor.add_argument('--max-shards',type=int,help='Process at most this many new 16-pair shards')
    expcap=sub.add_parser('learn-capture',help='G2-0061 research-only contextual capture, train/validation prefixes')
    expcap.add_argument('--plan',type=Path)
    expcap.add_argument('--output',type=Path)
    expcap.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    expcap.add_argument('--train-graphs',type=int,default=1000)
    expcap.add_argument('--validation-graphs',type=int,default=400)
    expcap.add_argument('--max-shards',type=int)
    exptra=sub.add_parser('learn-train',help='G2-0061 experimental learning; never formal qualification')
    exptra.add_argument('--plan',type=Path)
    exptra.add_argument('--capture',type=Path)
    exptra.add_argument('--output',type=Path)
    exptra.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    exptra.add_argument('--train-graphs',type=int,default=1000)
    exptra.add_argument('--validation-graphs',type=int,default=400)
    exptra.add_argument('--epochs',type=int,default=4)
    exptra.add_argument('--batch-pairs',type=int,default=2)
    exptra.add_argument('--learning-rate',type=float)
    exptra.add_argument('--checkpoint-every',type=int)
    exptra.add_argument('--max-new-steps',type=int)
    exptra.add_argument('--research-coverage-exception',action='store_true',
                        help='Explicit authorization for learning below the formal 98%% gate')
    ins=sub.add_parser('inspect-binding',help='Read detailed missing endpoint evidence from an existing pilot shard')
    ins.add_argument('--plan',type=Path)
    ins.add_argument('--capture',type=Path,required=True)
    aud=sub.add_parser('audit',help='Check all shard digests and coverage before training')
    aud.add_argument('--plan',type=Path)
    aud.add_argument('--capture',type=Path)
    aud.add_argument('--allow-incomplete',action='store_true',
                     help='Pilot audit on captured shards; never authorizes training')
    tra=sub.add_parser('train',help='Continue set predictor from frozen G2-0057')
    tra.add_argument('--plan',type=Path)
    tra.add_argument('--capture',type=Path)
    tra.add_argument('--output',type=Path)
    tra.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    tra.add_argument('--epochs',type=int,default=4)
    tra.add_argument('--batch-pairs',type=int,default=2)
    tra.add_argument('--checkpoint-every',type=int)
    tra.add_argument('--max-new-steps',type=int)
    tra.add_argument('--learning-rate',type=float,default=p55.LR)
    tra.add_argument('--limit-training-graphs',type=int,
                     help='Nested prefix rung: 1000, 5000, 10000, 25000, ...')
    ev=sub.add_parser('evaluate',help='One-time frozen comparison on new TEST and structural CHALLENGE')
    ev.add_argument('--plan',type=Path)
    ev.add_argument('--capture',type=Path)
    ev.add_argument('--training',type=Path,action='append',
                    help='Repeat for independent scale rungs; selection uses validation only')
    ev.add_argument('--output',type=Path)
    ev.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    a=p.parse_args(argv)
    root=a.root
    if a.phase=='plan':
        result=create_plan(a.output or root/'graph-g2-0059-plan',
            train_graphs=a.train_graphs,validation_graphs=a.validation_graphs,
            test_graphs=a.test_graphs,challenge_graphs=a.challenge_graphs,
            minimum_topologies=a.minimum_topologies,seed=a.seed)
    elif a.phase=='capture':
        result=capture(a.plan or root/'graph-g2-0059-plan',
            a.output or root/'graph-g2-0059-capture',root=root,device=a.device,
            shard_pairs=a.shard_pairs,max_shards=a.max_shards,
            candidate_policy=a.candidate_policy,compare_capture=a.compare_capture)
    elif a.phase=='rollout':
        result=rollout(a.plan or root/'graph-g2-0059-plan',
            a.output or root/'graph-g2-0059-evidence-rollout',
            a.pilot or root/'graph-g2-0059-evidence-pilot',
            root=root,device=a.device,stage=a.stage,max_shards=a.max_shards)
    elif a.phase=='actor-bakeoff':
        from tools import nanojev_gamer_v1_g2_actor_bakeoff_0060 as a60
        result=a60.run(__import__(__name__,fromlist=['main']),
            a.plan or root/'graph-g2-0059-plan',
            a.baseline or root/'graph-g2-0059-evidence-rollout',
            a.output or root/'graph-g2-0060-actor-bakeoff',
            root=root,phase=a.stage,device=a.device,max_shards=a.max_shards)
    elif a.phase=='learn-capture':
        from tools import nanojev_gamer_v1_g2_learning_0061 as next_learning
        result=next_learning.capture_learning(a.plan or root/'graph-g2-0059-plan',
            a.output or root/'graph-g2-0061-context-capture',root=root,device=a.device,
            train_graphs=a.train_graphs,validation_graphs=a.validation_graphs,
            max_shards=a.max_shards)
    elif a.phase=='learn-train':
        if not a.research_coverage_exception:
            refuse('EXPERIMENTAL_TRAINING_REQUIRES_EXPLICIT_COVERAGE_EXCEPTION')
        from tools import nanojev_gamer_v1_g2_learning_0061 as next_learning
        result=next_learning.train_learning(a.plan or root/'graph-g2-0059-plan',
            a.capture or root/'graph-g2-0061-context-capture',
            a.output or root/'graph-g2-0061-training',root=root,device=a.device,
            train_graphs=a.train_graphs,validation_graphs=a.validation_graphs,
            epochs=a.epochs,batch_pairs=a.batch_pairs,
            learning_rate=a.learning_rate,checkpoint_every=a.checkpoint_every,
            max_new_steps=a.max_new_steps)
    elif a.phase=='inspect-binding':
        result=inspect_missing(a.plan or root/'graph-g2-0059-plan',a.capture)
    elif a.phase=='audit':
        result=coverage_audit(a.plan or root/'graph-g2-0059-plan',
            a.capture or root/'graph-g2-0059-capture',
            require_complete=not a.allow_incomplete)
    elif a.phase=='train':
        result=train(a.plan or root/'graph-g2-0059-plan',
            a.capture or root/'graph-g2-0059-capture',
            a.output or root/'graph-g2-0059-training',root=root,device=a.device,
            epochs=a.epochs,batch_pairs=a.batch_pairs,
            checkpoint_every=a.checkpoint_every,max_new_steps=a.max_new_steps,
            learning_rate=a.learning_rate,
            limit_training_graphs=a.limit_training_graphs)
    else:
        result=evaluate(a.plan or root/'graph-g2-0059-plan',
            a.capture or root/'graph-g2-0059-capture',
            a.training or [root/'graph-g2-0059-training'],
            a.output or root/'graph-g2-0059-final-evaluation',root=root,device=a.device)
    print('GAMER_G2_0059_'+a.phase.upper()+' '+json.dumps(result,sort_keys=True),flush=True)
    return result


if __name__=='__main__':main()
