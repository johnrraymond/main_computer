"""G2-0098: Mechanism-targeted direct weight edit on selected 0096 head.

In 0097 a global none bias removed 190 false positives but lost 73 true
positives and one exact graph. Here we identify WRONG-ENDPOINT competitions
using TRAIN-only annotated games, inspect the actual 192-dimensional binding
pair activations, and add a sparse analytic direction to *one* binding.weight
output row. No gradients, optimizer, development/challenge answer access at
inference, or promotion. Decoder and original checkpoint remain unchanged.

Usage from main_computer/nanojev:
  python -u -m tools.nanojev_gamer_v1_g2_mit_directional_0098 run --device cuda
  python -m tools.nanojev_gamer_v1_g2_mit_directional_0098 find

Phases: prepare, diagnose, edit, predict, score. Run resumes completed phases.
If training examples do not exhibit a stable contrast, the runner refuses to
edit; no unlocalized edit is manufactured.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import torch
import torch.nn.functional as F
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_g2_mit_weight_intervention_0097 as earlier
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as bakeoff
from tools import nanojev_gamer_v1_g2_frozen_generalization_0091 as frozen
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES

SCHEMA = 'nanojev.gamer.graph.g2.mit-directional-0098.v1'
MODULE = 'tools.nanojev_gamer_v1_g2_mit_directional_0098'
ROOT_DEFAULT = earlier.ROOT_DEFAULT
NAME = 'oracle-fabric-g2-mit-directional-0098'
SEED = 980098
TRAIN_CASES = 80
CHALLENGE_CASES = 240
TOP_FEATURES = 12
MAX_ABS_CHANGE = 0.12
MAX_L2_CHANGE = 0.45
DESIRED_PAIR_MARGIN_GAIN = 0.35
MIN_TRAIN_PAIRS_PER_HALF = 8
MIN_VALIDATED_GAIN = 0.07

def require(ok, code):
    if not ok:
        raise ValueError('G2_0098_' + code)
sha, load, write, rows = earlier.sha, earlier.load, earlier.write, earlier.rows
digest, write_rows = earlier.digest, earlier.write_rows


def location(root, output):
    return (Path(output) if output else Path(root) / NAME).resolve()


def _train_selection(paths, n):
    """Training rows only; deterministic balanced and shuffled by prompt hash."""
    require(n >= 40 and n <= 400 and n % 2 == 0, 'BAD_TRAIN_CASE_COUNT')
    corpus = cur.load_rows(paths['dataset'])
    families = {}
    for fam in ('allocation', 'encounter'):
        members = [r for r in corpus if r['family'] == fam and r['split'] == 'train']
        members.sort(key=lambda r: hashlib.sha256((str(SEED)+r['prompt']).encode()).hexdigest())
        require(len(members) >= n//2, 'INSUFFICIENT_TRAIN_'+fam)
        families[fam] = members[:n//2]
    return [families[('allocation','encounter')[k%2]][k//2] for k in range(n)]


def _training_hash(rows_):
    keys=[{'family':r['family'], 'prompt_sha256':digest(r['prompt'])} for r in rows_]
    return hashlib.sha256(json.dumps(keys, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def _source(commit):
    src=earlier._candidate_info(commit['source']['checkpoint'])
    require(src==commit['source'], 'PARENT_CHECKPOINT_DRIFT')
    return src


def prepare(*,root=ROOT_DEFAULT,output=None,checkpoint=None,arm=earlier.PREFERRED_ARM,
            cases=CHALLENGE_CASES,train_cases=TRAIN_CASES):
    dest=location(root,output)
    require(not dest.exists() and dest.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    source=earlier.select_checkpoint(root,checkpoint,arm)
    paths=structural.paths_for(root)
    _,spec=structural.preflight(paths)
    train=_train_selection(paths,train_cases)
    previous_seed=earlier.SEED
    try:
        earlier.SEED=SEED
        prompts,answers=earlier.new_cohort(root,Path(paths['dataset']),cases)
    finally:
        earlier.SEED=previous_seed
    dest.mkdir()
    (dest/'seal').mkdir()
    write_rows(dest/'seal'/'prompts.jsonl',prompts)
    write_rows(dest/'seal'/'answers.SECRET.jsonl',answers)
    commit={'schema':SCHEMA,'state':'SEALED_BEFORE_DIAGNOSIS_OR_EDIT',
            'runner_sha256':sha(__file__),'source':source,'decoder_spec':spec,
            'cases':cases,'train_cases':train_cases,
            'training_dataset_sha256':sha(paths['dataset']),
            'training_selection_sha256':_training_hash(train),
            'prompts_sha256':sha(dest/'seal'/'prompts.jsonl'),
            'answers_sha256':sha(dest/'seal'/'answers.SECRET.jsonl'),
            'edit_class':'single_binding_readout_row',
            'top_features':TOP_FEATURES,'max_abs_change':MAX_ABS_CHANGE,
            'max_l2_change':MAX_L2_CHANGE,
            'desired_pair_gain':DESIRED_PAIR_MARGIN_GAIN,
            'training':False,'g3_unlocked':False,'production_promotion':False}
    write(dest/'seal'/'commit.json',commit)
    print('G2_0098_PREPARED '+json.dumps({'cases':cases,'train_cases':train_cases,
          'checkpoint':source['checkpoint'], 'parent_sha256':source['head_sha256'],
          'status':commit['state']},sort_keys=True),flush=True)
    return commit


def check(dest, *, gold=False):
    commit=load(dest/'seal'/'commit.json')
    require(commit.get('schema')==SCHEMA and
            commit.get('state')=='SEALED_BEFORE_DIAGNOSIS_OR_EDIT' and
            commit.get('runner_sha256')==sha(__file__) and
            commit.get('g3_unlocked') is False and
            commit.get('production_promotion') is False and
            commit.get('edit_class')=='single_binding_readout_row' and
            commit.get('top_features')==TOP_FEATURES and
            commit.get('max_abs_change')==MAX_ABS_CHANGE and
            commit.get('max_l2_change')==MAX_L2_CHANGE and
            commit.get('desired_pair_gain')==DESIRED_PAIR_MARGIN_GAIN and
            commit['prompts_sha256']==sha(dest/'seal'/'prompts.jsonl'),
            'SEAL_DRIFT')
    _source(commit)
    if gold:
        require(commit['answers_sha256']==sha(dest/'seal'/'answers.SECRET.jsonl'),
                'CHALLENGE_GOLD_DRIFT')
    return commit


def _pair_index(objects):
    lookup={}
    for k,obj in enumerate(objects):
        key=(obj[2], (int(obj[0]),int(obj[1])))
        require(key not in lookup,'DUPLICATE_G1_OBJECT')
        lookup[key]=k
    return lookup


def _key_parts(edge):
    return ((edge[0],tuple(edge[1])),(edge[2],tuple(edge[3])))


def competing_pairs(selected, gold, objects):
    """Training-only WRONG endpoint vs the SAME label's true endpoint.

    Same source comparison cancels source identity; same destination comparison
    cancels target identity. Pairs are required to exist in predicted G1, so
    neither head nor decoder is teacher forced to invent missing nodes.
    """
    idx=_pair_index(objects)
    result=[]
    for wrong in sorted(selected - gold):
        (source,target)=_key_parts(wrong)
        label=wrong[4]
        if source not in idx or target not in idx: continue
        alternatives=[]
        for correct in sorted(gold):
            if correct[4]!=label:continue
            (src2,dst2)=_key_parts(correct)
            if src2 not in idx or dst2 not in idx:continue
            if source==src2 and target[0]==dst2[0] and target!=dst2:
                alternatives.append(((idx[src2],idx[dst2]),(idx[source],idx[target]),'target'))
            elif target==dst2 and source[0]==src2[0] and source!=src2:
                alternatives.append(((idx[src2],idx[dst2]),(idx[source],idx[target]),'source'))
        if alternatives:
            best=sorted(alternatives,key=lambda a:(a[2],a[0],a[1]))[0]
            result.append((label,*best))
    return result


def _hook_features(head, call):
    """Observe 192-D post-GELU pair activations, never backpropagate."""
    seen=[]
    def hook(_module,_inputs,output):
        seen.append(F.gelu(output.detach().float()).cpu())
    h=head.pair_projection.register_forward_hook(hook)
    try:
        surface=call()
    finally:
        h.remove()
    require(len(seen)==1,'PAIR_FEATURE_HOOK_COUNT')
    return surface,seen[0]


def direction_from_pairs(by_label):
    """Analytic sparse weight direction. No training loss / gradients / search.

    0: discovery half of training examples, 1: independent training check.
    Select one class by the number of wrong-endpoint competitions. Select
    feature coordinates with consistent signed gold-minus-wrong contrast.
    Strength is fixed before any challenge inference, not tuned on F1.
    """
    diagnostics={}
    candidates=[]
    for label in BIND_CLASSES[1:]:
        disc=by_label.get(label,{}).get(0,[])
        verify=by_label.get(label,{}).get(1,[])
        diagnostics[label]={'discovery_competitions':len(disc),
                            'validation_competitions':len(verify)}
        if len(disc)<MIN_TRAIN_PAIRS_PER_HALF or len(verify)<MIN_TRAIN_PAIRS_PER_HALF:
            continue
        x=torch.stack([torch.as_tensor(t,dtype=torch.float64) for t in disc])
        y=torch.stack([torch.as_tensor(t,dtype=torch.float64) for t in verify])
        require(x.ndim==2 and y.ndim==2 and x.shape[1]==y.shape[1] and
                x.shape[1]>=TOP_FEATURES and torch.isfinite(x).all().item() and
                torch.isfinite(y).all().item(),'BAD_PAIR_FEATURES')
        dm=x.mean(0); vm=y.mean(0)
        spread=x.std(0,unbiased=False).clamp(min=.05)
        # Stable signal is required across independent TRAIN-only halves.
        valid=(dm*vm>0)&(vm.abs()>=.25*dm.abs())&(dm.abs()>=.04)
        ranking=[i for i in range(x.shape[1]) if bool(valid[i])]
        ranking.sort(key=lambda k:(-float((dm[k].abs()/spread[k])),k))
        columns=ranking[:TOP_FEATURES]
        if len(columns)<4:continue
        raw=torch.zeros_like(dm)
        for k in columns:
            raw[k]=dm[k]/spread[k]
        raw_norm=float(raw.norm())
        if raw_norm<1e-10:continue
        raw/=raw_norm
        validation_gain=float(vm @ raw)
        if validation_gain<=0:continue
        discovery_gain=float(dm @ raw)
        # Single precommitted margin target; cap each coefficient and row L2.
        coeff=min(DESIRED_PAIR_MARGIN_GAIN/max(discovery_gain,1e-9),
                  MAX_ABS_CHANGE/max(float(raw.abs().max()),1e-9),
                  MAX_L2_CHANGE)
        delta=raw*coeff
        validated=float(vm @ delta)
        if validated<MIN_VALIDATED_GAIN:continue
        score=len(disc)+len(verify)
        item={'label':label,'index':BIND_CLASSES.index(label),'columns':columns,
              'deltas':[float(delta[i]) for i in columns],
              'discovery_competitions':len(disc),
              'validation_competitions':len(verify),
              'estimated_discovery_margin_gain':float(dm@delta),
              'estimated_validation_margin_gain':validated,
              'delta_l2':float(delta.norm()),
              'delta_max_abs':float(delta.abs().max()),
              'mechanism':'same-label wrong-endpoint competition',
              'gradient_steps':0}
        diagnostics[label].update({'stable_dimensions':len(columns),
                                   'validation_margin_gain':validated})
        candidates.append((score,validated,item))
    require(candidates,'NO_STABLE_TRAIN_ONLY_ENDPOINT_DIRECTION')
    candidates.sort(key=lambda v:(-v[0],-v[1],v[2]['index']))
    return candidates[0][2],diagnostics


def _row_training_pairs(row, network,tokenizer,composite,head,spec,device):
    prepared,hidden=bakeoff.previous._capture_inference(
        network,tokenizer,row['prompt'],device,2048)
    g1=composite.predict(hidden,prepared,family=row['family'],native_stage='G1')
    spans=g1['token_spans']
    surf,features=_hook_features(head,lambda:bakeoff._infer(
        head,hidden,prepared['eligible'].to(device),spans,prepared,spec,
        with_margins=True))
    objs=surf['g1_objects']
    require(len(objs)<=features.shape[0] and
            features.shape[-1]==head.binding.weight.shape[1],
            'PAIR_FEATURE_GEOMETRY')
    selected=bakeoff.decode(surf,spec)
    gold=bakeoff.truth(row['graph'])
    contrasts=competing_pairs(selected,gold,objs)
    per_label={}
    for label,correct,wrong,mode in contrasts:
        i,j=correct; a,b=wrong
        delta=features[i,j]-features[a,b]
        per_label.setdefault(label,[]).append(delta.tolist())
    return {'family':row['family'],'prompt_sha256':digest(row['prompt']),
            'selected':len(selected),'gold':len(gold),
            'wrong_endpoint_competitions':len(contrasts),
            'pair_differences':per_label}


def diagnose(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=location(root,output);commit=check(dest)
    d=dest/'diagnosis';d.mkdir(exist_ok=True)
    require(not (d/'lock.json').exists(),'DIAGNOSIS_ALREADY_LOCKED')
    shards=d/'shards';shards.mkdir(exist_ok=True)
    paths=structural.paths_for(root)
    require(sha(paths['dataset'])==commit['training_dataset_sha256'],
            'TRAIN_DATA_CHANGED')
    training=_train_selection(paths,commit['train_cases'])
    require(_training_hash(training)==commit['training_selection_sha256'],
            'TRAIN_SELECTION_CHANGED')
    pending=[(i,row) for i,row in enumerate(training)
             if not (shards/f'{i:04d}.json').is_file()]
    if pending:
        network,tok,composite,reference,_manifest=structural._load_runtime(paths,device)
        head=earlier._load_scientific_head(commit['source']['checkpoint'],reference,device=device)
        spec=commit['decoder_spec']
        with torch.inference_mode():
            for progress,(idx,row) in enumerate(pending,1):
                item=_row_training_pairs(row,network,tok,composite,head,spec,device)
                target=shards/f'{idx:04d}.json'
                temp=target.with_suffix('.tmp')
                write(temp,item);os.replace(temp,target)
                if progress==1 or progress%20==0 or progress==len(pending):
                    print('G2_0098_TRAIN_DIAGNOSIS '
                        +f'{len(training)-len(pending)+progress}/{len(training)}',flush=True)
    by_label=defaultdict(lambda:{0:[],1:[]})
    totals=Counter()
    for i,row in enumerate(training):
        item=load(shards/f'{i:04d}.json')
        require(item['prompt_sha256']==digest(row['prompt']) and
                item['family']==row['family'],'TRAIN_SHARD_DRIFT')
        totals['training_cases']+=1
        totals['competitions']+=item['wrong_endpoint_competitions']
        for label,vectors in item['pair_differences'].items():
            require(label in BIND_CLASSES[1:],'UNKNOWN_RELATION_LABEL')
            by_label[label][(i//2)%2].extend(vectors)
    try:
        edit_spec,per_label=direction_from_pairs(by_label)
    except ValueError as exc:
        summary={'training_cases':totals['training_cases'],
                 'competitions':totals['competitions'],
                 'per_label':{k:{'discovery':len(v[0]),'validation':len(v[1])}
                              for k,v in sorted(by_label.items())},
                 'reason':str(exc),'edit_performed':False}
        write(d/'diagnosis-failure.json',summary)
        print('G2_0098_NO_STABLE_DIRECTION '+json.dumps(summary,sort_keys=True),
              flush=True)
        raise
    statement={'schema':SCHEMA,'mechanism':'incorrect endpoint outranks correct endpoint',
               'training_only':True,'dataset_sha256':commit['training_dataset_sha256'],
               'source_head_sha256':commit['source']['head_sha256'],
               'totals':dict(totals),'by_label':per_label,'edit':edit_spec,
               'gold_inference_access':False,'gradient_steps':0,
               'challenge_used_for_selection':False,'g3_unlocked':False,
               'production_promotion':False}
    write(d/'diagnosis.json',statement)
    write(d/'lock.json',{'schema':SCHEMA,'state':'TRAIN_ONLY_DIRECTION_LOCKED',
          'diagnosis_sha256':sha(d/'diagnosis.json'),
          'edit':edit_spec,'source_head_sha256':commit['source']['head_sha256']})
    print('G2_0098_DIRECTION_LOCKED '+json.dumps({'edit':edit_spec,
          'training_competitions':totals['competitions']},sort_keys=True),flush=True)
    return statement


def _direction_lock(dest,commit):
    d=dest/'diagnosis'
    lock=load(d/'lock.json')
    require(lock.get('schema')==SCHEMA and
            lock.get('state')=='TRAIN_ONLY_DIRECTION_LOCKED' and
            lock.get('diagnosis_sha256')==sha(d/'diagnosis.json') and
            lock.get('source_head_sha256')==commit['source']['head_sha256'] and
            lock.get('edit')==load(d/'diagnosis.json')['edit'],
            'DIRECTION_LOCK_DRIFT')
    return lock['edit']


def edit_state(state,plan):
    require('binding.weight' in state and 'binding.bias' in state,
            'BINDING_READOUT_MISSING')
    weight=state['binding.weight']
    require(weight.ndim==2 and weight.shape[0]==len(BIND_CLASSES),
            'BINDING_WEIGHT_GEOMETRY')
    label=plan['label'];idx=BIND_CLASSES.index(label)
    require(idx==plan['index'] and 0<idx<len(BIND_CLASSES),
            'LABEL_AUTHORITY_DRIFT')
    cols=plan['columns'];deltas=plan['deltas']
    require(4<=len(cols)==len(deltas)<=TOP_FEATURES and
            all(type(i) is int and 0<=i<weight.shape[1] for i in cols) and
            len(set(cols))==len(cols),'DIRECTION_COORDINATE_INVALID')
    change=torch.zeros(weight.shape[1],dtype=torch.float64)
    for i,d in zip(cols,deltas):change[i]=float(d)
    require(torch.isfinite(change).all().item() and
            float(change.abs().max())<=MAX_ABS_CHANGE+1e-7 and
            float(change.norm())<=MAX_L2_CHANGE+1e-7,
            'DIRECTION_COEFFICIENT_LIMIT')
    out={key:value.detach().cpu().clone().contiguous()
         for key,value in state.items()}
    before=out['binding.weight'][idx].clone()
    out['binding.weight'][idx]+=change.to(out['binding.weight'].dtype)
    change_saved=(out['binding.weight'][idx]-before).double()
    require(len(change_saved.nonzero())==len(cols) and
            torch.allclose(change_saved,change,atol=3e-7,rtol=1e-5),
            'DIRECTION_ROUNDTRIP_DRIFT')
    for name in state:
        if name!='binding.weight':
            require(torch.equal(out[name],state[name].cpu()),
                    'OTHER_PARAMETER_MUTATED')
        else:
            before_whole=out[name].clone();before_whole[idx]=before
            require(torch.equal(before_whole,state[name].cpu()),
                    'OTHER_READOUT_ROW_MUTATED')
    return out,{'label':label,'index':idx,'tensor':'binding.weight',
                'changed_coefficients':len(cols),'modified_output_rows':1,
                'columns':cols,'deltas':deltas,'max_abs_delta':float(change.abs().max()),
                'delta_l2':float(change.norm()),'gradient_steps':0,
                'training_only_diagnostic':True}


def edit(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output);commit=check(dest)
    plan=_direction_lock(dest,commit)
    target=dest/'edit'
    require(not target.exists(),'EDIT_ALREADY_EXISTS')
    source=Path(commit['source']['checkpoint'])
    raw=load_file(str(source/cur.HEAD_FILE),device='cpu')
    out,receipt=edit_state(raw,plan)
    target.mkdir();d=target/'checkpoint';d.mkdir()
    save_file(out,str(d/cur.HEAD_FILE))
    manifest=load(source/cur.MANIFEST)
    manifest.update({'head_sha256':sha(d/cur.HEAD_FILE),
           'status':'G2_0098_TRAIN_DIRECTION_EDIT_EXPERIMENTAL',
           'g3_training_unlocked':False,'g3_unlocked':False,
           'production_promotion':False,
           'g2_0098_mit':{'schema':SCHEMA,'parent_head_sha256':
              commit['source']['head_sha256'],'diagnosis_sha256':
              sha(dest/'diagnosis'/'diagnosis.json'),'receipt':receipt}})
    write(d/cur.MANIFEST,manifest)
    saved=load_file(str(d/cur.HEAD_FILE),device='cpu')
    require(set(saved)==set(out) and all(torch.equal(saved[k],out[k]) for k in out),
            'EDITED_CHECKPOINT_DRIFT')
    receipt.update({'parent_head_sha256':commit['source']['head_sha256'],
            'edited_head_sha256':sha(d/cur.HEAD_FILE),
            'manifest_sha256':sha(d/cur.MANIFEST),
            'source_unchanged':_source(commit)==commit['source'],
            'production_promotion':False,'g3_unlocked':False})
    write(target/'receipt.json',receipt)
    print('G2_0098_EDITED '+json.dumps(receipt,sort_keys=True),flush=True)


def verify_edit(dest):
    commit=check(dest);plan=_direction_lock(dest,commit)
    receipt=load(dest/'edit'/'receipt.json')
    ck=dest/'edit'/'checkpoint'
    require(receipt['parent_head_sha256']==commit['source']['head_sha256'] and
            receipt['edited_head_sha256']==sha(ck/cur.HEAD_FILE) and
            receipt['manifest_sha256']==sha(ck/cur.MANIFEST) and
            receipt['source_unchanged'] is True and
            receipt['label']==plan['label'] and
            receipt['columns']==plan['columns'] and
            receipt['deltas']==plan['deltas'] and
            receipt['changed_coefficients']==len(plan['columns']) and
            receipt['gradient_steps']==0 and
            receipt['production_promotion'] is False and
            receipt['g3_unlocked'] is False,'EDIT_RECEIPT_DRIFT')
    return receipt


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=location(root,output);commit=check(dest)
    receipt=verify_edit(dest)
    blind=dest/'blind'
    require(not (blind/'lock.json').exists(),'BLIND_ALREADY_LOCKED')
    blind.mkdir(exist_ok=True)
    shards=blind/'shards';shards.mkdir(exist_ok=True)
    public=list(rows(dest/'seal'/'prompts.jsonl'))
    require(len(public)==commit['cases'] and
            len({r['case_id'] for r in public})==len(public),
            'CHALLENGE_COUNT_DRIFT')
    pending=[r for r in public if not (shards/(r['case_id']+'.json')).is_file()]
    if pending:
        paths=structural.paths_for(root)
        net,tok,composite,reference,_manifest=structural._load_runtime(paths,device)
        h0=earlier._load_scientific_head(commit['source']['checkpoint'],reference,
                                        device=device)
        h1=earlier._load_scientific_head(dest/'edit'/'checkpoint',reference,
                                        device=device)
        spec=commit['decoder_spec']
        with torch.inference_mode():
            for index,row in enumerate(pending,1):
                require(row['prompt_sha256']==digest(row['prompt']),'PROMPT_DRIFT')
                prepared,hidden=bakeoff.previous._capture_inference(
                    net,tok,row['prompt'],device,2048)
                g1=composite.predict(hidden,prepared,family=row['family'],
                                     native_stage='G1')
                spans=g1['token_spans'];eligible=prepared['eligible'].to(device)
                b0=frozen._infer(h0,hidden,eligible,spans,prepared)
                b1=frozen._infer(h1,hidden,eligible,spans,prepared)
                require(b0['g1_objects']==b1['g1_objects'],'G1_CHANGED')
                before=bakeoff.decode(b0,spec)
                after=bakeoff.decode(b1,spec)
                record={'case_id':row['case_id'],'family':row['family'],
                        'prompt_sha256':row['prompt_sha256'],
                        'g1_objects':b0['g1_objects'],
                        'baseline':[earlier.json_edge(e) for e in sorted(before)],
                        'edited':[earlier.json_edge(e) for e in sorted(after)]}
                target=shards/(row['case_id']+'.json')
                temp=target.with_suffix('.tmp');write(temp,record);os.replace(temp,target)
                if index==1 or index%25==0 or index==len(pending):
                    print(f'G2_0098_BLIND {len(public)-len(pending)+index}/{len(public)}',
                          flush=True)
    output_path=blind/'predictions.jsonl';tmp=blind/'predictions.jsonl.tmp'
    with tmp.open('w',encoding='utf-8') as f:
        for row in public:
            v=earlier._validated_shard(shards/(row['case_id']+'.json'),row)
            f.write(json.dumps(v,sort_keys=True)+'\n')
    os.replace(tmp,output_path)
    lock={'schema':SCHEMA,'state':'BLIND_PREDICTIONS_LOCKED',
          'predictions_sha256':sha(output_path),
          'prompts_sha256':commit['prompts_sha256'],
          'parent_head_sha256':commit['source']['head_sha256'],
          'edited_head_sha256':receipt['edited_head_sha256'],
          'gold_accessed':False,'g3_unlocked':False,'production_promotion':False}
    write(blind/'lock.json',lock)
    print('G2_0098_BLIND_LOCKED '+json.dumps({'cases':len(public),
          'predictions_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)


def score(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output);commit=check(dest,gold=True)
    receipt=verify_edit(dest)
    lock=load(dest/'blind'/'lock.json')
    require(lock.get('schema')==SCHEMA and
            lock.get('state')=='BLIND_PREDICTIONS_LOCKED' and
            lock.get('predictions_sha256')==sha(dest/'blind'/'predictions.jsonl') and
            lock.get('prompts_sha256')==commit['prompts_sha256'] and
            lock.get('parent_head_sha256')==commit['source']['head_sha256'] and
            lock.get('edited_head_sha256')==receipt['edited_head_sha256'] and
            lock.get('gold_accessed') is False,'BLIND_LOCK_DRIFT')
    require(not (dest/'scored').exists(),'ALREADY_SCORED')
    answers={v['case_id']:v for v in rows(dest/'seal'/'answers.SECRET.jsonl')}
    public={v['case_id']:v for v in rows(dest/'seal'/'prompts.jsonl')}
    prediction=list(rows(dest/'blind'/'predictions.jsonl'))
    require(len(prediction)==len(public)==len(answers)==commit['cases'] and
            len({v['case_id'] for v in prediction})==len(prediction),
            'SCORE_CASE_COUNT')
    totals={f:{a:Counter() for a in ('baseline','edited')}
            for f in ('allocation','encounter','overall')}
    paired=Counter()
    for row in prediction:
        case=row['case_id']
        require(case in answers and case in public and
                row['family']==answers[case]['family']==public[case]['family'] and
                row['prompt_sha256']==answers[case]['prompt_sha256']==
                public[case]['prompt_sha256'],'SCORE_CASE_DRIFT')
        gold=bakeoff.truth(answers[case]['graph'])
        before=earlier.as_set(row['baseline']);after=earlier.as_set(row['edited'])
        for name,edges in (('baseline',before),('edited',after)):
            c={'cases':1,'exact':int(edges==gold),'tp':len(edges&gold),
               'fp':len(edges-gold),'fn':len(gold-edges)}
            totals[row['family']][name].update(c)
            totals['overall'][name].update(c)
        paired.update({'rescued_tp':len((after&gold)-(before&gold)),
           'lost_tp':len((before&gold)-(after&gold)),
           'removed_fp':len((before-gold)-(after-gold)),
           'introduced_fp':len((after-gold)-(before-gold)),
           'exact_gained':int(after==gold and before!=gold),
           'exact_lost':int(before==gold and after!=gold)})
    metrics={fam:{arm:earlier.summarize(c) for arm,c in v.items()}
             for fam,v in totals.items()}
    deltas={fam:{'f1':val['edited']['edge_f1']-val['baseline']['edge_f1'],
                 'exact':val['edited']['exact']-val['baseline']['exact'],
                 'fp':val['edited']['fp']-val['baseline']['fp'],
                 'fn':val['edited']['fn']-val['baseline']['fn']}
            for fam,val in metrics.items()}
    passed=(deltas['overall']['f1']>.005 and
            deltas['overall']['exact']>=0 and
            all(deltas[f]['f1']>=-.01 for f in ('allocation','encounter')))
    report={'schema':SCHEMA,
            'status':('MIT_DIRECTION_FRESH_IMPROVEMENT_NOT_CERTIFIED' if passed else
                      'MIT_DIRECTION_NO_SAFE_GAIN_ON_FRESH_CHALLENGE'),
            'metrics':metrics,'deltas':deltas,'paired':dict(paired),
            'edit':receipt,'precommitted_gate_passed':bool(passed),
            'parent_unchanged':_source(commit)==commit['source'],
            'training':False,'g3_unlocked':False,'production_promotion':False,
            'limitations':('Train-only analytic edit of sparse binding readout '
                            'weights; not a full causal circuit proof. '
                            'Synthetic balanced held-out cohort. No reward '
                            'for prior 0097 blind answers and no promotion.')}
    (dest/'scored').mkdir()
    write(dest/'scored'/'report.json',report)
    print('G2_0098_SCORED '+json.dumps({'status':report['status'],
            'deltas':deltas,'paired':dict(paired),
            'parent_unchanged':report['parent_unchanged']},sort_keys=True),flush=True)
    return report


def run(*,root=ROOT_DEFAULT,output=None,device='cuda',checkpoint=None,
        arm=earlier.PREFERRED_ARM,cases=CHALLENGE_CASES,train_cases=TRAIN_CASES):
    dest=location(root,output)
    phases=[('prepare',['--arm',arm,'--cases',str(cases),
                        '--train-cases',str(train_cases)] +
               (['--checkpoint',checkpoint] if checkpoint else [])),
            ('diagnose',['--device',device]),('edit',[]),
            ('predict',['--device',device]),('score',[])]
    for phase,args in phases:
        marker={'prepare':dest/'seal'/'commit.json',
                'diagnose':dest/'diagnosis'/'lock.json',
                'edit':dest/'edit'/'receipt.json',
                'predict':dest/'blind'/'lock.json',
                'score':dest/'scored'/'report.json'}[phase]
        if marker.is_file():
            check(dest)
            if phase in ('diagnose','edit','predict','score'):_direction_lock(dest,check(dest))
            if phase in ('edit','predict','score'):verify_edit(dest)
            continue
        argv=[sys.executable,'-u','-m',MODULE,phase,'--root',str(root)]
        if output:argv.extend(['--output',str(output)])
        argv.extend(args)
        subprocess.run(argv,check=True)
    report=load(dest/'scored'/'report.json')
    print('G2_0098_RESULT '+json.dumps({'status':report['status'],
          'gate':report['precommitted_gate_passed'],
          'deltas':report['deltas']},sort_keys=True),flush=True)
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    subs=ap.add_subparsers(dest='command',required=True)
    for name in ('find','run','prepare','diagnose','edit','predict','score'):
        p=subs.add_parser(name)
        p.add_argument('--root',default=ROOT_DEFAULT)
        if name!='find':p.add_argument('--output')
        if name in ('run','prepare','find'):p.add_argument('--arm',default=earlier.PREFERRED_ARM)
        if name in ('run','prepare'):p.add_argument('--checkpoint')
        if name in ('run','prepare'):p.add_argument('--cases',type=int,default=CHALLENGE_CASES)
        if name in ('run','prepare'):p.add_argument('--train-cases',type=int,default=TRAIN_CASES)
        if name in ('run','diagnose','predict'):p.add_argument('--device',default='cuda')
    args=ap.parse_args(argv)
    if args.command=='find':
        print('G2_0098_SOURCE_CANDIDATES '+json.dumps(
            earlier.discover(args.root,args.arm),sort_keys=True),flush=True)
        return
    params={'root':args.root,'output':args.output}
    if args.command in ('run','prepare'):
        params.update(checkpoint=args.checkpoint,arm=args.arm,cases=args.cases,
                      train_cases=args.train_cases)
    if args.command in ('run','diagnose','predict'):
        params['device']=args.device
    globals()[args.command](**params)


if __name__=='__main__':
    main()
