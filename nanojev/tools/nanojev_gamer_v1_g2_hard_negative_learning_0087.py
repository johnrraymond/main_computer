"""G2-0087: frozen-G1, observed-node, within-kind hard-negative G2 continuation.

One-command run: python -m tools.nanojev_gamer_v1_g2_hard_negative_learning_0087 run --device cuda

Does not modify certified G1-0082 code or alter the legacy native decoder.
Preparation commits fresh answers; the blind predictor never opens those answers.
A separate score phase opens answers only after prediction files are hashed and locked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_native_training_0083 as previous
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES, decode_graph

SCHEMA='nanojev.gamer.graph.g2.within-kind-hard-negatives-0087.v1'
MODULE='tools.nanojev_gamer_v1_g2_hard_negative_learning_0087'
PARENT_SHA='8ba554eb5679faf68ba065fc410a61f28862a36f6b2a0bc3912ff1278a86a85e'
SEED=870087
DEV=96
CHALLENGE=64
STEPS=400
ACCUM=4
LR=4e-5
# Predeclared training coefficients; never tune on sealed prospective answers.
LOSS_WEIGHTS={'positive_ce':1.0,'negative_ce':2.0,'positive_margin':0.5,
              'same_kind_rank':1.5,'hard_false_admission':1.0}


def require(ok,code):
    if not ok:raise ValueError('G2_0087_'+code)


def sha(path):return backbone.sha256(Path(path))
def js(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write_json(path,data):Path(path).write_text(cur.json_text(data)+'\n',encoding='utf-8')
def read_jsonl(path):return previous.load_jsonl(path)
def write_jsonl(path,rows):return previous.write_jsonl(path,rows)


def paths_for(root):
    root=Path(root)
    paths=previous.defaults(root)
    paths['prior']=root/'oracle-fabric-g2-native-training-0083'
    paths['prior_fuzz']=root/'oracle-fabric-g2-causal-activation-fuzz-0085'
    return paths


def preflight(paths):
    parent,proof=previous.preflight(paths)
    saved=previous._verify_saved_training(paths,paths['prior'],parent)
    require(saved['head_sha256']==PARENT_SHA,'PINNED_PARENT_CHECKPOINT_DRIFT')
    require(saved.get('native_composite_g1',{}).get('mode')==
            'predicted_G1_spans_native_G2_binding','PARENT_PREDICTED_G1_REQUIRED')
    require(saved.get('train_limit') in (0,None) and saved['stage']=='G2',
            'UNBOUNDED_G2_PARENT_REQUIRED')
    previous.compare_frozen_stage(paths['g1'],paths['prior']/'training')
    # No G2 certificate may be inferred from the failed 0083 acceptance run.
    report=js(paths['prior']/'report.json')
    require(report.get('status')=='G2_PREDICTED_SPAN_TRAINED_BUT_GATE_BLOCKED'
            and report.get('g3_training_unlocked') is False
            and report.get('g2_checkpoint_sha256')==PARENT_SHA,
            'PARENT_FAILURE_RECORD_MISSING')
    return parent,saved


def excluded_prompts(paths):
    excluded=previous.previously_seen(paths)
    prior_prompts=read_jsonl(paths['prior']/'seal'/'prompts.jsonl')
    require(len(prior_prompts)==previous.DEV+previous.CHALLENGE,
            'PREVIOUS_PROSPECTIVE_MISSING')
    for record in prior_prompts:
        digest=hashlib.sha256(record['prompt'].encode()).hexdigest()
        require(record['prompt_sha256']==digest,'PREVIOUS_PROSPECTIVE_DRIFT')
        excluded.add(digest)
    # Explicitly exclude previously exposed 0085/0086 counterfactual prompts.
    fuzz=paths['prior_fuzz']/'cases.jsonl'
    require(fuzz.exists(),'G2_0085_DIAGNOSTIC_EXCLUSION_REQUIRED')
    fuzz_rows=read_jsonl(fuzz)
    require(len(fuzz_rows)==16,'G2_0085_DIAGNOSTIC_EXCLUSION_INCOMPLETE')
    for r in fuzz_rows:
        digest=hashlib.sha256(r['prompt'].encode()).hexdigest()
        require(digest==r['prompt_sha256'],'G2_0085_EXCLUSION_DRIFT')
        excluded.add(digest)
    return excluded


def prospective_rows(paths):
    excluded=excluded_prompts(paths)
    rng=random.Random(SEED)
    rows=[];serial=87_000_000
    for split,size in (('dev',DEV),('challenge',CHALLENGE)):
        for index in range(size):
            factory=corpus.encounter if index%2==0 else corpus.allocation
            for _ in range(4096):
                candidate=factory(rng,'test',serial)
                serial+=1
                digest=hashlib.sha256(candidate['prompt'].encode()).hexdigest()
                if digest not in excluded:break
            else:raise ValueError('G2_0087_FRESH_PROMPT_EXHAUSTED')
            excluded.add(digest)
            require(candidate['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
                    and candidate['graph']['edges'],'FRESH_GOLD_INVALID')
            rows.append({'case_id':f'g2-0087-{split}-{index+1:04}',
                         'split':split,'family':candidate['family'],
                         'prompt':candidate['prompt'],'prompt_sha256':digest,
                         'gold_objects':candidate['object_spans'],'graph':candidate['graph']})
    require(len(rows)==DEV+CHALLENGE and len(excluded)>=DEV+CHALLENGE,
            'COHORT_SIZE')
    return rows


def commit_seal(paths,output,parent):
    rows=prospective_rows(paths)
    seal=output/'seal';seal.mkdir(parents=False,exist_ok=False)
    write_jsonl(seal/'prompts.jsonl',[
        {k:r[k] for k in ('case_id','split','family','prompt','prompt_sha256')} for r in rows])
    write_jsonl(seal/'answers.SECRET.jsonl',[
        {k:r[k] for k in ('case_id','split','family','prompt_sha256','gold_objects','graph')}
        for r in rows])
    record={'schema':SCHEMA,'status':'SEALED_BEFORE_TRAINING',
        'runner_sha256':sha(Path(__file__)),
        'seed':SEED,'dev':DEV,'challenge':CHALLENGE,
        'prompts_sha256':sha(seal/'prompts.jsonl'),
        'answers_sha256':sha(seal/'answers.SECRET.jsonl'),
        'dataset_sha256':sha(paths['dataset']),
        'parent_g2_head_sha256':parent['head_sha256'],
        'g1_certificate_sha256':sha(paths['certificate']),
        'g1_composite_sha256':sha(paths['composite']/'composite-interface.json'),
        'loss_weights':LOSS_WEIGHTS,'training_steps':STEPS,
        'production_promotion':False,'g3_training_unlocked':False}
    write_json(seal/'commit.json',record)
    return record


def check_seal(paths,root):
    seal=Path(root)/'seal'
    commit=js(seal/'commit.json')
    require(commit.get('schema')==SCHEMA and
            commit.get('status')=='SEALED_BEFORE_TRAINING' and
            (commit.get('seed'),commit.get('dev'),commit.get('challenge'))==
            (SEED,DEV,CHALLENGE) and
            commit.get('loss_weights')==LOSS_WEIGHTS and
            commit.get('parent_g2_head_sha256')==PARENT_SHA and
            commit.get('production_promotion') is False and
            commit.get('g3_training_unlocked') is False,
            'SEAL_DESIGN_DRIFT')
    for rel,key in [('prompts.jsonl','prompts_sha256'),
                    ('answers.SECRET.jsonl','answers_sha256')]:
        require(sha(seal/rel)==commit[key],'SEAL_'+key+'_DRIFT')
    require(commit.get('runner_sha256')==sha(Path(__file__)),
            'AUDIT_RUNNER_CODE_DRIFT')
    require(sha(paths['dataset'])==commit['dataset_sha256'] and
            sha(paths['certificate'])==commit['g1_certificate_sha256'] and
            sha(paths['composite']/'composite-interface.json')==
               commit['g1_composite_sha256'], 'SEAL_LINEAGE_DRIFT')
    return commit


def training_targets(spans,ids,edges,*,device):
    """Gold is used only here, after observed G1 objects are fixed.

    Index by *predicted* G1 node indices. Every directed candidate is labelled,
    including late pairs ignored by G2-0083's truncated-negative sampler.
    """
    require(isinstance(spans,(list,tuple)) and isinstance(ids,dict) and
            isinstance(edges,list),'TRAINING_TARGET_INPUT')
    n=len(spans)
    require(n<=128 and all(isinstance(s,dict) and s.get('kind') in cur.SPAN_KINDS
                          for s in spans),'OBSERVED_G1_SPAN_KINDS')
    require(len(set(ids.values()))==len(ids) and
            all(type(i) is int and 0<=i<n for i in ids.values()),'OBSERVED_ID_MAP')
    target=torch.zeros((n,n),dtype=torch.long,device=device)
    for edge in edges:
        typ=edge.get('type')
        if typ not in BIND_CLASSES[1:] or edge['src'] not in ids or edge['dst'] not in ids:
            continue
        i=ids[edge['src']];j=ids[edge['dst']]
        require(i!=j,'GOLD_SELF_EDGE')
        label=BIND_CLASSES.index(typ)
        require(target[i,j].item() in (0,label),'MULTILABEL_PAIR_UNSUPPORTED')
        target[i,j]=label
    return target


def loss_components(logits,targets,spans):
    """Dense admission + hardest within-kind/source/binding-type ranking.

    Candidate selection is deterministic and detached, while the selected
    logits carry gradients. Negative pairs are labelled from training gold;
    no gold candidate choice enters inference.
    """
    require(logits.ndim==3 and logits.shape[0]==logits.shape[1] and
            logits.shape[-1]==len(BIND_CLASSES) and
            targets.shape==logits.shape[:2] and
            len(spans)==logits.shape[0], 'PAIR_LOGIT_SHAPE')
    n=len(spans)
    require(n>=2,'INSUFFICIENT_G1_NODES')
    mask=~torch.eye(n,dtype=torch.bool,device=logits.device)
    require(targets.dtype==torch.long and
            not bool((targets.diag()!=0).any()) and
            bool(((targets>=0)&(targets<len(BIND_CLASSES))).all()),
            'TARGET_CONTRACT')
    pos=(targets>0)&mask
    neg=(targets==0)&mask
    zero=logits.sum()*0
    pos_ce=F.cross_entropy(logits[pos],targets[pos]) if bool(pos.any()) else zero
    neg_ce=F.cross_entropy(logits[neg],targets[neg]) if bool(neg.any()) else zero
    own_margin=(logits[...,1:]-logits[...,0:1])
    if bool(pos.any()):
        i,j=torch.where(pos)
        score=own_margin[i,j,targets[i,j]-1]
        positive_margin=F.softplus(1.0-score).mean()
    else:positive_margin=zero
    rank_losses=[];rank_groups=0;rank_inverted=0
    # One hardest competitor per gold edge, same source, same destination kind,
    # same binding type. Other types are NOT silently treated as negatives for
    # their own type; only the target edge class is contrasted.
    with torch.no_grad():
        for i,j in pos.nonzero(as_tuple=False).tolist():
            t=int(targets[i,j]);kind=spans[j]['kind']
            rivals=[k for k in range(n) if k!=i and k!=j and
                    spans[k]['kind']==kind and int(targets[i,k])!=t]
            if not rivals:continue
            margins=own_margin.detach()[i,rivals,t-1]
            k=rivals[int(margins.argmax())]
            rank_groups+=1
            rank_inverted+=int(float(own_margin.detach()[i,k,t-1])>=
                               float(own_margin.detach()[i,j,t-1]))
            rank_losses.append((i,j,k,t))
    if rank_losses:
        a,b,c,t=zip(*rank_losses)
        a=torch.tensor(a,device=logits.device);b=torch.tensor(b,device=logits.device)
        c=torch.tensor(c,device=logits.device)
        t=torch.tensor(t,device=logits.device)-1
        ranked=F.softplus(0.75+own_margin[a,c,t]-own_margin[a,b,t]).mean()
    else:ranked=zero
    if bool(neg.any()):
        false_margin=own_margin.max(-1).values[neg]
        k=min(len(false_margin),max(32,4*int(pos.sum().item())))
        hard_false=F.softplus(0.75+false_margin.topk(k).values).mean()
    else:hard_false=zero
    terms={'positive_ce':pos_ce,'negative_ce':neg_ce,
           'positive_margin':positive_margin,'same_kind_rank':ranked,
           'hard_false_admission':hard_false}
    loss=sum(LOSS_WEIGHTS[name]*term for name,term in terms.items())
    stats={'gold_reachable_edges':int(pos.sum().item()),
           'negative_directed_pairs':int(neg.sum().item()),
           'hard_rank_pairs':rank_groups,'hard_rank_inverted':rank_inverted}
    return loss,terms,stats


def _verify_trained(paths,root):
    manifest_path=Path(root)/'training'
    _,record=cur._load_checkpoint(manifest_path,device='cpu')
    require(record.get('stage')=='G2' and
            record.get('head_sha256')==sha(manifest_path/cur.HEAD_FILE) and
            record.get('g2_0087',{}).get('parent_sha256')==PARENT_SHA and
            record.get('native_composite_g1',{}).get('mode')==
            'predicted_G1_spans_native_G2_binding' and
            record.get('dataset_sha256')==sha(paths['dataset']) and
            record.get('source_adapter_sha256')==
                backbone.adapter_fingerprint(paths['adapter']),
            'TRAINED_CHECKPOINT_DRIFT')
    diff=previous.compare_frozen_stage(paths['prior']/'training',manifest_path)
    require(diff['non_g2_weights_mutated'] is False,'FROZEN_STAGES_MUTATED')
    return record,diff


def fit(*,root,output,device='cuda',steps=STEPS,accum=ACCUM,lr=LR,max_length=2048):
    paths=paths_for(root);dest=Path(output).resolve()
    require(steps==STEPS and accum==ACCUM and lr==LR and max_length==2048,
            'PREDECLARED_TRAINING_DESIGN_CHANGED')
    check_seal(paths,dest)
    require(not (dest/'training').exists() and not (dest/'blind').exists(),
            'IMMUTABLE_TRAINING_OUTPUT')
    _,parent_record=preflight(paths)
    model,tok=backbone.model_components(parent_record['model_id'],paths['adapter'],device)
    require(not any(p.requires_grad for p in model.parameters()),'BACKBONE_UNFROZEN')
    composite=g1native.load_composite(paths['composite'],device=device)
    head,record=cur._load_checkpoint(paths['prior']/'training',device=device)
    require(record['head_sha256']==PARENT_SHA,'TRAINING_WRONG_PARENT')
    cur.trainable_stage(head,'G2')
    require(all((p.requires_grad == any(name.startswith(pref)
                for pref in cur.STAGE_MODULES['G2']))
                for name,p in head.named_parameters()), 'STAGE_GRADIENT_AUTHORITY')
    rows=[r for r in cur.load_rows(paths['dataset']) if r['split']=='train']
    require(rows and all(r['family'] in ('encounter','allocation') for r in rows),
            'TRAINING_FAMILY_CONTRACT')
    cache=[];count_reachable=0;count_rank=0
    for index,row in enumerate(rows,1):
        item=cur.capture(model,tok,row,device,max_length)
        h=item['hidden'].to(device=device,dtype=torch.float32)
        g1=composite.predict(h,item['prepared'],family=row['family'],native_stage='G1')
        spans=g1['token_spans']
        ids=cur.composite_binding_labels(item['prepared'],row,spans)
        targets=training_targets(spans,ids,row['graph']['edges'],device='cpu')
        # Hidden and targets held CPU-side; only the G2 head is trainable.
        item={'hidden':item['hidden'],'eligible':item['prepared']['eligible'],
              'spans':spans,'targets':targets,'family':row['family']}
        count_reachable+=int((targets>0).sum())
        count_rank+=sum(1 for v in targets.flatten() if v>0)
        cache.append(item)
        if index==1 or index%50==0 or index==len(rows):
            print(f'G2_0087_CAPTURE {index}/{len(rows)} frozen_G1_predictions',flush=True)
    require(count_reachable>0,'NO_REACHABLE_TRAINING_EDGES')
    sampler={fam:[i for i,r in enumerate(cache) if r['family']==fam]
             for fam in ('encounter','allocation')}
    require(all(sampler.values()),'MISSING_TRAIN_FAMILY')
    # Deterministic alternating families; no answer-driven sample ordering.
    torch.manual_seed(SEED)
    optimizer=torch.optim.AdamW([p for p in head.parameters() if p.requires_grad],lr=lr)
    rng=random.Random(SEED)
    head.train()
    log=[];global_stats=Counter()
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True)
        losses=[]
        for micro in range(accum):
            family=('encounter','allocation')[((step-1)*accum+micro)%2]
            item=cache[rng.choice(sampler[family])]
            h=item['hidden'].to(device=device,dtype=torch.float32)
            output_head=head(h,eligible=item['eligible'].to(device),
                             inference_spans=item['spans'],active_stage='G2')
            n=len(item['spans'])
            if n<2:
                global_stats['no_pair_examples']+=1
                continue
            loss,_,stats=loss_components(output_head['binding_logits'][:n,:n],
                    item['targets'].to(device),item['spans'])
            require(bool(torch.isfinite(loss).item()),'NONFINITE_TRAINING_LOSS')
            # Preserve deterministic-fabric correction regularizer.
            corrections=[p.square().mean() for name,p in head.named_parameters()
                if name.startswith('interface_couplers.') and '.delta.' in name and p.requires_grad]
            if corrections:loss=loss+0.001*torch.stack(corrections).sum()
            (loss/accum).backward()
            losses.append(float(loss.detach()))
            global_stats.update(stats)
        torch.nn.utils.clip_grad_norm_([p for p in head.parameters() if p.requires_grad],1.0)
        optimizer.step()
        require(losses,'NO_VALID_TRAINING_MICROBATCH')
        value=sum(losses)/len(losses)
        log.append(value)
        if step==1 or step%50==0 or step==steps:
            print('G2_0087_TRAIN '+json.dumps({'step':step,'of':steps,
                     'loss':round(value,6),'hard_rank_inverted':global_stats['hard_rank_inverted'],
                     'hard_rank_total':global_stats['hard_rank_pairs']},sort_keys=True),flush=True)
    checkpoint=dest/'training';checkpoint.mkdir(exist_ok=False)
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
              str(checkpoint/cur.HEAD_FILE))
    out=dict(record)
    out.update({'status':'TRAINED_NOT_CERTIFIED','stage':'G2',
      'head_sha256':sha(checkpoint/cur.HEAD_FILE),'steps':steps,'accum':accum,
      'lr':lr,'seed':SEED,'last_loss':log[-1],
      'parent_checkpoint':str(paths['prior'].resolve()/'training'),
      'parent_cert':str(paths['certificate'].resolve()),
      'next_training_gate':'LOCKED_PENDING_CERTIFICATION',
      'train_examples':len(rows),'train_limit':0,
      'production_promotion':False,'test_split_used':False,
      'g2_0087':{'schema':SCHEMA,'parent_sha256':PARENT_SHA,
          'loss_weights':LOSS_WEIGHTS,'hard_negative_policy':
          'same_source_same_destination_kind_same_binding_class',
          'admission_policy':'all_directed_pairs_plus_topk_false_margins',
          'oracle_use':'training_loss_only_after_predicted_G1_spans',
          'frozen_g1':True,'training_samples':len(rows),
          'reachable_gold_training_edges':count_reachable,
          'training_statistics':dict(global_stats)}})
    write_json(checkpoint/'manifest.json',out)
    proof,diff=_verify_trained(paths,dest)
    write_json(dest/'training-integrity.json',
               {'schema':SCHEMA,'trained_head_sha256':proof['head_sha256'],
                'parent_g2_head_sha256':PARENT_SHA,'frozen_parameters':diff,
                'train_examples':len(rows),'training_statistics':dict(global_stats),
                'certified':False})
    return proof


def _raw_pair_margins(output,spans):
    """Gold-blind score surface for independent same-kind rank audit."""
    n=len(spans)
    raw=output['binding_logits'][:n,:n].detach().float().cpu()
    require(raw.shape==(n,n,len(BIND_CLASSES)),'RAW_BINDING_MATRIX_SHAPE')
    margins=raw[...,1:]-raw[...,0:1]
    return [[i,j,[round(float(v),7) for v in margins[i,j].tolist()]]
            for i in range(n) for j in range(n) if i!=j]


def history(*,root,output,device='cuda'):
    """Original exposed development/challenge gates, with current-weight G1 retention.

    This is historical development evidence ONLY; sealed prospective scores are
    computed separately after blind inference. Old G1 ablations are not rerun
    through the G2-only head because G1-0082 already certified them.
    """
    paths=paths_for(root);dest=Path(output).resolve()
    check_seal(paths,dest)
    manifest,_=_verify_trained(paths,dest)
    for stage in ('G2','G1'):
        for split in ('dev','challenge'):
            out=dest/'evaluation'/(stage+'_'+split)
            if out.exists():
                previous._verified_existing_evaluation(out,manifest,
                      stage=stage,split=split)
                continue
            cur.evaluate(dataset=paths['dataset'],checkpoint=dest/'training',
                output=out,split=split,device=device,target_stage=stage,
                ablation_limit=32 if stage=='G2' else 0)
    gate=dest/'historical-development-gate'
    if gate.exists():
        require((gate/'certification.json').exists(),'HISTORY_GATE_INCOMPLETE')
        report=js(gate/'certification.json')
        require(report.get('head_sha256')==manifest['head_sha256'],
                'HISTORY_GATE_WRONG_HEAD')
    else:
        report=cur.certify(checkpoint=dest/'training',
            dev_report=dest/'evaluation'/'G2_dev'/'report.json',
            challenge_report=dest/'evaluation'/'G2_challenge'/'report.json',
            retention_dev={'G1':dest/'evaluation'/'G1_dev'/'report.json'},
            retention_challenge={'G1':dest/'evaluation'/'G1_challenge'/'report.json'},
            output=gate)
    print('G2_0087_HISTORICAL '+json.dumps({'status':report['status'],
        'failures':report['failures']},sort_keys=True),flush=True)
    return report


def predict(*,root,output,device='cuda',max_length=2048):
    paths=paths_for(root);dest=Path(output).resolve();commit=check_seal(paths,dest)
    trained,_=_verify_trained(paths,dest)
    require(not (dest/'blind').exists() and max_length==2048,
            'BLIND_ALREADY_USED_OR_MAX_LENGTH_CHANGED')
    composite=g1native.load_composite(paths['composite'],device=device)
    model,tok=backbone.model_components(trained['model_id'],paths['adapter'],device)
    require(not any(p.requires_grad for p in model.parameters()),'BACKBONE_NOT_FROZEN')
    candidate,_=cur._load_checkpoint(dest/'training',device=device)
    baseline,baseline_manifest=cur._load_checkpoint(paths['prior']/'training',device=device)
    untrained,_=cur._load_checkpoint(paths['g1'],device=device)
    require(baseline_manifest['head_sha256']==PARENT_SHA,'CONTROL_PARENT_CHECKPOINT_DRIFT')
    for network in (candidate,baseline,untrained):network.eval().requires_grad_(False)
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE and
            len({p['case_id'] for p in prompts})==len(prompts),
            'BLIND_PROMPT_BIJECTION')
    predicted=[]
    for index,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==
                row['prompt_sha256'],'PROMPT_HASH_DRIFT')
        prep,h=previous._capture_inference(model,tok,row['prompt'],device,max_length)
        eligible=prep['eligible'].to(device)
        g1=composite.predict(h,prep,family=row['family'],native_stage='G1')
        spans=g1['token_spans']
        def evaluate(net,hidden,spans,*,disable=False,keep_margins=False):
            with torch.inference_mode():
                output_head=net(hidden,eligible=eligible,active_stage='G2',
                                inference_spans=spans)
                if disable:
                    output_head=dict(output_head,
                                     binding_logits=torch.zeros_like(output_head['binding_logits']))
                graph,surfaces=decode_graph(output_head,prep,unlocked='G2')
            require(len(surfaces)==len(spans),'NATIVE_SPAN_LOSS')
            objects=sorted([list((n['span'][0],n['span'][1],n['kind']))
                            for n in graph['nodes'] if 'span' in n])
            result={'g1_objects':objects,
                    'binding_edges':previous._safe_edges(previous._edge_sets(graph))}
            if keep_margins:
                result['pair_margins']=_raw_pair_margins(output_head,spans)
            return result
        modes={'trained':evaluate(candidate,h,spans,keep_margins=True),
               'control':evaluate(baseline,h,spans,keep_margins=True),
               'untrained':evaluate(untrained,h,spans),
               'no_binding':evaluate(candidate,h,spans,disable=True)}
        zero=composite.predict(h,prep,family=row['family'],mode='zero_hidden',native_stage='G1')
        modes['zero_hidden']=evaluate(candidate,torch.zeros_like(h),zero['token_spans'])
        require(modes['trained']['g1_objects']==modes['control']['g1_objects']==
                modes['untrained']['g1_objects']==modes['no_binding']['g1_objects'],
                'G1_ARM_SPANS_DIFFER')
        pred={'case_id':row['case_id'],'split':row['split'],
              'family':row['family'],'prompt_sha256':row['prompt_sha256'],
              'surface_count':len(spans),'modes':modes}
        predicted.append(pred)
        if index==1 or index%10==0 or index==len(prompts):
            print(f'G2_0087_BLIND {index}/{len(prompts)}',flush=True)
    blind=dest/'blind';blind.mkdir(exist_ok=False)
    write_jsonl(blind/'predictions.jsonl',predicted)
    lock={'schema':SCHEMA,'status':'BLIND_PREDICTIONS_LOCKED_UNSCORED',
          'cases':len(predicted),'predictions_sha256':sha(blind/'predictions.jsonl'),
          'prompts_sha256':commit['prompts_sha256'],
          'trained_head_sha256':trained['head_sha256'],
          'parent_head_sha256':PARENT_SHA,
          'gold_read_during_inference':False,
          'teacher_forced_g1':False,'production_promotion':False}
    write_json(blind/'lock.json',lock)
    print('G2_0087_BLIND_LOCKED '+json.dumps({'cases':len(predicted),
          'predictions_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def _paired_rank_statistics(record,answer,metric):
    """Post-lock oracle labels evaluate same-source, same-dst-kind ranking."""
    nodes={(n['span'][0],n['span'][1],n['kind']):n['id'] for n in answer['graph']['nodes']
           if 'span' in n}
    observed=[(x[0],x[1],x[2]) for x in record['g1_objects']]
    lookup={tuple(x):i for i,x in enumerate(observed)}
    gold_edges=previous._gold_edges(answer['graph'])
    # All comparison keys are author-space, so synthetic object ID does not leak.
    types=[]
    for src_kind,src_span,dst_kind,dst_span,typ in gold_edges:
        src=(src_span[0],src_span[1],src_kind)
        dst=(dst_span[0],dst_span[1],dst_kind)
        if src in lookup and dst in lookup:
            types.append((lookup[src],lookup[dst],BIND_CLASSES.index(typ)))
    idx_to_margin={}
    require(len(metric['pair_margins'])==len(observed)*max(len(observed)-1,0),
            'PAIR_MARGINS_INCOMPLETE')
    for a,b,m in metric['pair_margins']:
        require(type(a) is int and type(b) is int and 0<=a<len(observed) and
                0<=b<len(observed) and a!=b and len(m)==len(BIND_CLASSES)-1 and
                all(isinstance(v,(float,int)) and abs(v)<1e7 for v in m) and
                (a,b) not in idx_to_margin, 'PAIR_SCORE_FORMAT')
        idx_to_margin[(a,b)]=m
    require(len(idx_to_margin)==len(metric['pair_margins']),'DUPLICATE_PAIR_MARGIN')
    by_group=defaultdict(set)
    for i,j,t in types:by_group[(i,observed[j][2],t)].add(j)
    groups=0;success=0;inverted=0
    by_kind=Counter();wins=Counter()
    for (i,kind,t),positives in by_group.items():
        negatives=[j for j in range(len(observed)) if j!=i and
                   observed[j][2]==kind and j not in positives]
        if not negatives:continue
        groups+=1
        good=min(idx_to_margin[(i,j)][t-1] for j in positives)
        bad=max(idx_to_margin[(i,j)][t-1] for j in negatives)
        win=good>bad
        success+=int(win);inverted+=int(not win)
        name=f'{observed[i][2]}->{kind}'
        by_kind[name]+=1;wins[name]+=int(win)
    return {'groups':groups,'all_true_above_false':success,'inverted':inverted,
            'by_kind':{k:{'groups':v,'success':wins[k]} for k,v in by_kind.items()}}



def verify_history(paths,dest,trained):
    """Revalidate the original G2 development and G1 retention gate sources."""
    cert_path=dest/'historical-development-gate'/'certification.json'
    require(cert_path.is_file(),'HISTORICAL_EVALUATION_MISSING')
    cert=js(cert_path)
    require(cert.get('schema')==cur.SCHEMA and cert.get('certified_stage')=='G2'
            and cert.get('head_sha256')==trained['head_sha256']
            and cert.get('dataset_sha256')==trained['dataset_sha256']
            and cert.get('production_promotion') is False
            and cert.get('requirements')==cur.GATE_REQUIREMENTS['G2'],
            'HISTORICAL_GATE_LINEAGE')
    requirements=cur.GATE_REQUIREMENTS['G2']
    independent=[]
    for split in ('dev','challenge'):
        for stage in ('G2','G1'):
            file=dest/'evaluation'/(stage+'_'+split)/'report.json'
            key=(split if stage=='G2' else split+':G1')
            hashes=(cert.get('report_hashes',{}) if stage=='G2'
                    else cert.get('retention_hashes',{}))
            sources=(cert.get('source_reports',{}) if stage=='G2'
                     else cert.get('retention_sources',{}))
            require(file.is_file() and sha(file)==hashes.get(key) and
                    str(file.resolve())==sources.get(key),
                    'HISTORICAL_SOURCE_HASH_'+key)
            report=previous._verified_existing_evaluation(file.parent,trained,
                        stage=stage,split=split)
            metric=cur.GATE_REQUIREMENTS[stage]['metric']
            threshold=cur.GATE_REQUIREMENTS[stage][split]
            if report['metrics'][metric]<threshold:
                independent.append(split+':'+stage+':accuracy')
            if stage=='G2':
                for name in ('zero_hidden_ablation','stage_component_ablation'):
                    abl=report['metrics'][name]
                    if abl['cases']<8 or abl['net_gain']<requirements['neural_gain']:
                        independent.append(split+':'+name)
                if len(report['metrics']['by_family'])<2:
                    independent.append(split+':family_coverage')
    cert_pass=cert.get('status')=='DEVELOPMENT_GATE_PASSED'
    require(cert.get('status') in ('DEVELOPMENT_GATE_PASSED','DEVELOPMENT_GATE_BLOCKED') and
            isinstance(cert.get('failures'),list) and
            cert_pass==(len(cert['failures'])==0), 'HISTORICAL_GATE_STATUS_DRIFT')
    if cert_pass and independent:
        raise ValueError('G2_0087_HISTORICAL_GATE_FALSE_PASS')
    return {'status':cert['status'], 'failures':cert['failures'],
            'independent_failures':independent,
            'historical_exact':{split:js(dest/'evaluation'/('G2_'+split)/'report.json')
               ['metrics']['binding_exact_rate'] for split in ('dev','challenge')},
            'g1_retention':{split:js(dest/'evaluation'/('G1_'+split)/'report.json')
               ['metrics']['object_exact_rate'] for split in ('dev','challenge')}}


def score(*,root,output):
    paths=paths_for(root);dest=Path(output).resolve();commit=check_seal(paths,dest)
    trained,_=_verify_trained(paths,dest)
    require(not (dest/'scored').exists(),'ALREADY_SCORED')
    historical=verify_history(paths,dest,trained)
    blind=dest/'blind';lock=js(blind/'lock.json')
    require(lock.get('schema')==SCHEMA and
            lock.get('status')=='BLIND_PREDICTIONS_LOCKED_UNSCORED' and
            lock.get('cases')==DEV+CHALLENGE and
            lock.get('prompts_sha256')==commit['prompts_sha256'] and
            lock.get('predictions_sha256')==sha(blind/'predictions.jsonl') and
            lock.get('trained_head_sha256')==trained['head_sha256'] and
            lock.get('parent_head_sha256')==PARENT_SHA and
            lock.get('gold_read_during_inference') is False and
            lock.get('teacher_forced_g1') is False,
            'BLIND_INTEGRITY_DRIFT')
    prompts=read_jsonl(dest/'seal'/'prompts.jsonl')
    answers=read_jsonl(dest/'seal'/'answers.SECRET.jsonl')
    predicted=read_jsonl(blind/'predictions.jsonl')
    require(len(prompts)==len(answers)==len(predicted)==DEV+CHALLENGE,
            'CASE_COUNTS')
    pmap={r['case_id']:r for r in prompts}
    amap={r['case_id']:r for r in answers}
    require(len(pmap)==len(amap)==DEV+CHALLENGE and
            len({r['case_id'] for r in predicted})==DEV+CHALLENGE and
            set(pmap)==set(amap)=={r['case_id'] for r in predicted},
            'CASE_BIJECTION')
    for ident,row in amap.items():
        require(all(row[k]==pmap[ident][k] for k in ('split','family','prompt_sha256')) and
                hashlib.sha256(pmap[ident]['prompt'].encode()).hexdigest()==
                row['prompt_sha256'],'GOLD_PROMPT_MISMATCH')
    out={};failures=[];case_records=[]
    for split,size in (('dev',DEV),('challenge',CHALLENGE)):
        subset=[r for r in predicted if r['split']==split]
        require(len(subset)==size,'SPLIT_COUNT')
        modes=('trained','control','untrained','no_binding','zero_hidden')
        totals={m:Counter() for m in modes}
        ranking={m:Counter() for m in ('trained','control')}
        rank_by_kind={m:defaultdict(Counter) for m in ('trained','control')}
        families=Counter();paired_gain=paired_loss=0
        reachable_edges=0;total_edges=0
        for r in subset:
            answer=amap[r['case_id']]
            require(all(r[k]==answer[k] for k in ('split','family','prompt_sha256')),
                    'PREDICTION_GOLD_BINDING')
            gs=previous._gold_edges(answer['graph'])
            go={(x['start'],x['end'],x['kind']) for x in answer['gold_objects']}
            exact={}
            require(set(r['modes'])==set(modes),'BLIND_ARM_SET')
            for mode in modes:
                data=r['modes'][mode]
                predicted_edges=previous._keyed_edges(data['binding_edges'])
                objects={tuple(x) for x in data['g1_objects']}
                require(len(objects)==len(data['g1_objects']) and
                        all((*a[1],a[0]) in objects and (*b[1],b[0]) in objects
                            for a,b in [( (ak,at),(bk,bt)) for ak,at,bk,bt,_ in predicted_edges]),
                        'PREDICTED_EDGE_NOT_ANCHORED')
                if mode=='trained':
                    require(len(objects)==r['surface_count'],'G1_OBJECT_COUNT_DRIFT')
                counts=totals[mode]
                tp=len(predicted_edges&gs)
                counts.update({'exact':int(predicted_edges==gs), 'tp':tp,
                               'pred':len(predicted_edges),'gold':len(gs),
                               'g1_exact':int(objects==go)})
                exact[mode]=predicted_edges==gs
                if mode in ranking:
                    s=_paired_rank_statistics(data,answer,data)
                    ranking[mode].update({'groups':s['groups'],
                        'success':s['all_true_above_false'], 'inverted':s['inverted']})
                    for typ,v in s['by_kind'].items():rank_by_kind[mode][typ].update(v)
            require(r['modes']['trained']['g1_objects']==r['modes']['control']['g1_objects']==
                    r['modes']['untrained']['g1_objects']==
                    r['modes']['no_binding']['g1_objects'], 'G1_DIFFERENCE_ACROSS_ARMS')
            observed={tuple(o) for o in r['modes']['trained']['g1_objects']}
            reachable_edges+=sum((*at,ak) in observed and (*bt,bk) in observed
                  for ak,at,bk,bt,_ in gs)
            total_edges+=len(gs)
            paired_gain+=int(exact['trained'] and not exact['control'])
            paired_loss+=int(exact['control'] and not exact['trained'])
            families[r['family']]+=1
            case_records.append({'case_id':r['case_id'],'split':split,'family':r['family'],
                'candidate_exact':exact['trained'],'control_exact':exact['control'],
                'g1_exact':set(map(tuple,r['modes']['trained']['g1_objects']))==go,
                'gold_edges':len(gs),'candidate_edges':len(r['modes']['trained']['binding_edges'])})
        metrics={}
        for m,c in totals.items():
            precision=c['tp']/max(c['pred'],1)
            recall=c['tp']/max(c['gold'],1)
            metrics[m]={'exact':c['exact'],'exact_rate':c['exact']/size,
                'edge_precision':precision,'edge_recall':recall,
                'edge_f1':2*precision*recall/max(precision+recall,1e-12),
                'predicted_edges':c['pred'],'true_positive_edges':c['tp'],
                'false_positive_edges':c['pred']-c['tp'],'g1_exact':c['g1_exact']}
        ranking_result={m:{'groups':v['groups'],'success':v['success'],
            'rate':v['success']/max(v['groups'],1),
            'by_kind':{k:dict(d) for k,d in rank_by_kind[m].items()}}
            for m,v in ranking.items()}
        gate=cur.GATE_REQUIREMENTS['G2']
        split_failures=[]
        if metrics['trained']['exact_rate']<gate[split]:
            split_failures.append('binding_exact_rate_below_original_gate')
        for kind in ('zero_hidden','no_binding'):
            if (metrics['trained']['exact']-metrics[kind]['exact'])/size <gate['neural_gain']:
                split_failures.append(kind+'_causality_below_original_gate')
        if metrics['trained']['exact']<=metrics['untrained']['exact']:
            split_failures.append('no_exact_gain_vs_untrained')
        if metrics['trained']['exact']<=metrics['control']['exact']:
            split_failures.append('no_exact_gain_vs_frozen_G2_0083')
        if ranking_result['trained']['rate']<=ranking_result['control']['rate']:
            split_failures.append('no_within_kind_ranking_gain_vs_G2_0083')
        if len(families)<2:split_failures.append('family_coverage')
        failures.extend(split+':'+x for x in split_failures)
        out[split]={'cases':size,'metrics':metrics,
            'ranking':ranking_result,'paired_gain':paired_gain,'paired_loss':paired_loss,
            'gold_edges_reachable_with_predicted_G1':reachable_edges,
            'gold_edges_unreachable_from_predicted_G1':total_edges-reachable_edges,
            'by_family':dict(families),'failures':split_failures}
    combined=historical['failures']+historical['independent_failures']+failures
    result={'schema':SCHEMA,
         'status':'G2_0087_NUMERIC_GATES_PASSED_NOT_CERTIFIED' if not combined
             else 'G2_0087_GATE_BLOCKED',
         'failures':combined,'prospective_failures':failures,'splits':out,
         'historical':historical,'prospective_passed':not failures,
         'g2_certified':False,'g3_training_unlocked':False,
         'promotion':'NONE_AUTOMATIC','previous_G1_certification_preserved':True,
         'evidence_scope':'new author-generator prospective audit; not out-of-distribution validation'}
    scored=dest/'scored';scored.mkdir(exist_ok=False)
    write_json(scored/'report.json',result)
    write_jsonl(scored/'cases.jsonl',case_records)
    print('G2_0087_RESULT '+json.dumps({'status':result['status'],
        'dev_exact':out['dev']['metrics']['trained']['exact'],
        'challenge_exact':out['challenge']['metrics']['trained']['exact'],
        'edge_f1':{s:out[s]['metrics']['trained']['edge_f1'] for s in out},
        'ranking':{s:{k:{'success':v['success'],'groups':v['groups']}
                      for k,v in out[s]['ranking'].items()} for s in out},
        'failures':combined,'g3_unlocked':False},sort_keys=True),flush=True)
    return result


def run(*,root='../runtime/reports/gamer-v1',output=None,device='cuda'):
    paths=paths_for(root)
    dest=Path(output).resolve() if output else (Path(root)/'oracle-fabric-g2-hard-negative-learning-0087').resolve()
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_IMMUTABLE')
    _,parent=preflight(paths)
    dest.mkdir(exist_ok=False)
    commit_seal(paths,dest,parent)
    fit(root=root,output=dest,device=device)
    history(root=root,output=dest,device=device)
    # Predict and score in separate processes, after the training is complete.
    for command in ('predict','score'):
        cmd=[sys.executable,'-u','-m',MODULE,command,
             '--root',str(Path(root).resolve()),'--output',str(dest)]
        if command=='predict':cmd+=['--device',device]
        subprocess.run(cmd,check=True)
    return js(dest/'scored'/'report.json')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    for action in ('run','fit','history','predict','score'):
        p=subs.add_parser(action)
        p.add_argument('--root',default='../runtime/reports/gamer-v1')
        p.add_argument('--output')
        if action in ('run','fit','history','predict'):
            p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    args=vars(parser.parse_args(argv));command=args.pop('command')
    if command!='run' and not args['output']:
        args['output']=str(Path(args['root'])/'oracle-fabric-g2-hard-negative-learning-0087')
    return {'run':run,'fit':fit,'history':history,'predict':predict,'score':score}[command](**args)


if __name__=='__main__':main()
