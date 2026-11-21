"""Gamer G2 MIT version: sparse weight intervention + exact constrained projection.

From main_computer/nanojev, with existing G2-0087/0088 lineage in runtime:
  python -m tools.nanojev_gamer_v1_mit run --device cuda
  python -m tools.nanojev_gamer_v1_mit query --prompt "..." --device cuda

The MIT designation identifies our experimental adaptation of two MIT research
ideas; it is NOT an MIT-developed model, a reproduction of either paper, or an
assertion that a mathematical proof covers semantic accuracy.

All training labels come from the original training split. New dev/challenge
answers are sealed before training and never opened in the prediction phase.
G1 and G3+ coefficients remain frozen. No certification or G3 unlock is made.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import torch
from torch.nn import functional as F
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools import nanojev_gamer_v1_mit_decoder as projector

SCHEMA = 'nanojev.gamer.graph.g2.mit-cutover.v1'
VERSION = 'MIT'
MODULE = 'tools.nanojev_gamer_v1_mit'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
CAMPAIGN = 'oracle-fabric-g2-mit-0001'
SEED = 261120
DEV = 48
CHALLENGE = 32
EDITABLE = ('pair_projection.weight', 'pair_projection.bias',
            'binding.weight', 'binding.bias')
MAX_EDIT_FRACTION = .002  # 0.2 percent of the G2 edit candidate parameter surface
MAX_ABS_DELTA = .03
LR = .0003
STEPS = 200
PER_FAMILY = 8


def require(ok, why):
    if not ok: raise ValueError('MIT_GAMER_' + why)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, record):
    Path(path).write_text(json.dumps(record, sort_keys=True, indent=2,
                     ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def write_jsonl(path, records):
    with Path(path).open('x', encoding='utf-8') as file:
        for r in records:
            file.write(json.dumps(r, sort_keys=True, ensure_ascii=False,
                                  allow_nan=False)+'\n')


def rows(path):
    with Path(path).open(encoding='utf-8') as file:
        for line in file:
            if line.strip(): yield json.loads(line)


def campaign(root, output=None):
    return (Path(output) if output else Path(root)/CAMPAIGN).resolve()


def sources(root):
    paths = structural.paths_for(root)
    manifest, decoder = structural.preflight(paths)
    require(decoder.get('method') == 'joint','PARENT_NOT_JOINT')
    return paths, manifest, decoder


def known_hashes(paths, root):
    """Exclude previously generated train and named historical test prompts."""
    seen = {hashlib.sha256(r['prompt'].encode()).hexdigest()
            for r in cur.load_rows(paths['dataset'])}
    for name in ('oracle-fabric-g2-hard-negative-learning-0087',
                 'oracle-fabric-g2-posttrain-graph-bakeoff-0088',
                 'oracle-fabric-g2-structural-learning-0089',
                 'oracle-fabric-g2-frozen-generalization-0091',
                 'oracle-fabric-g2-mixed-family-fuzz-0092',
                 'oracle-fabric-g2-large-scale-error-atlas-0093'):
        file = Path(root)/name/'seal'/'prompts.jsonl'
        if file.exists():
            for r in rows(file):
                seen.add(r.get('prompt_sha256') or hashlib.sha256(r['prompt'].encode()).hexdigest())
    return seen


def seal_new_cases(paths, root):
    rng = random.Random(SEED)
    seen = known_hashes(paths, root)
    serial = 26_112_000
    result=[]
    for split, count in (('dev',DEV),('challenge',CHALLENGE)):
        for i in range(count):
            factory = corpus.encounter if i % 2 == 0 else corpus.allocation
            for _ in range(4096):
                entry = factory(rng, 'test', serial);serial += 1
                digest = hashlib.sha256(entry['prompt'].encode()).hexdigest()
                if digest not in seen: break
            else: raise ValueError('MIT_GAMER_COHORT_EXHAUSTED')
            seen.add(digest)
            require(entry.get('annotation_provenance') == 'AUTHOR_MARKED_IN_SOURCE_GENERATOR',
                    'UNTRUSTED_COHORT_ANNOTATIONS')
            result.append({'case_id':f'mit-{split}-{i+1:04}',
                'split':split,'family':entry['family'],'prompt':entry['prompt'],
                'prompt_sha256':digest,'graph':entry['graph']})
    return result


def prepare(*,root=ROOT_DEFAULT,output=None):
    paths, parent, spec = sources(root)
    dest=campaign(root,output)
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_ALREADY_EXISTS')
    cases=seal_new_cases(paths,root)
    dest.mkdir(exist_ok=False)
    (dest/'seal').mkdir(exist_ok=False)
    write_jsonl(dest/'seal'/'prompts.jsonl',
        [{key:r[key] for key in ('case_id','split','family','prompt','prompt_sha256')}
         for r in cases])
    write_jsonl(dest/'seal'/'answers.SECRET.jsonl',
        [{key:r[key] for key in ('case_id','split','family','prompt_sha256','graph')}
         for r in cases])
    write(dest/'decoder.json',{'version':VERSION,'schema':SCHEMA,
          'source_selection_sha256':sha(paths['parent_0088']/'selection.json'),
          'winner':spec,'method':'exact-capacity-constrained-MILP',
          'labels_per_pair':4,'trainable_parameters':list(EDITABLE),
          'max_edit_fraction':MAX_EDIT_FRACTION,'max_abs_delta':MAX_ABS_DELTA,
          'g3_training_unlocked':False,'certified':False})
    record={'schema':SCHEMA,'version':VERSION,
            'state':'GOLD_COMMITTED_BEFORE_TRAINING',
            'source_head_sha256':parent['head_sha256'],
            'source_dataset_sha256':sha(paths['dataset']),
            'source_g1_certificate_sha256':sha(paths['certificate']),
            'source_g1_composite_sha256':sha(paths['composite']/'composite-interface.json'),
            'decoder_sha256':sha(dest/'decoder.json'),
            'source_selection_sha256':sha(paths['parent_0088']/'selection.json'),
            'prompts_sha256':sha(dest/'seal'/'prompts.jsonl'),
            'answers_sha256':sha(dest/'seal'/'answers.SECRET.jsonl'),
            'runner_sha256':sha(__file__),'projector_sha256':sha(projector.__file__),
            'seed':SEED,'dev':DEV,'challenge':CHALLENGE,
            'independent_distribution':False,
            'g3_training_unlocked':False,'production_promotion':False}
    write(dest/'seal'/'commit.json',record)
    print('MIT_GAMER_PREPARED '+json.dumps({'version':'MIT',
            'fresh_dev':DEV,'fresh_challenge':CHALLENGE,
            'sealed_answers_sha256':record['answers_sha256']},sort_keys=True),flush=True)
    return record


def verify(dest,root,*,gold=False):
    dest=Path(dest)
    commit=read(dest/'seal'/'commit.json')
    paths,parent,spec=sources(root)
    checks=(commit.get('schema')==SCHEMA and commit.get('version')==VERSION and
        commit.get('state')=='GOLD_COMMITTED_BEFORE_TRAINING' and
        commit.get('source_head_sha256')==parent['head_sha256'] and
        commit.get('source_dataset_sha256')==sha(paths['dataset']) and
        commit.get('source_g1_certificate_sha256')==sha(paths['certificate']) and
        commit.get('source_g1_composite_sha256')==sha(paths['composite']/'composite-interface.json') and
        commit.get('source_selection_sha256')==sha(paths['parent_0088']/'selection.json') and
        commit.get('decoder_sha256')==sha(dest/'decoder.json') and
        commit.get('prompts_sha256')==sha(dest/'seal'/'prompts.jsonl') and
        commit.get('runner_sha256')==sha(__file__) and
        commit.get('projector_sha256')==sha(projector.__file__) and
        commit.get('g3_training_unlocked') is False and
        commit.get('production_promotion') is False)
    if gold: checks=checks and commit['answers_sha256']==sha(dest/'seal'/'answers.SECRET.jsonl')
    require(checks,'CAMPAIGN_PROVENANCE_DRIFT')
    cfg=read(dest/'decoder.json')
    require(cfg['version']==VERSION and cfg['winner']==spec and cfg['method']==
        'exact-capacity-constrained-MILP' and cfg['g3_training_unlocked'] is False,
        'DECODER_DRIFT')
    return paths, parent, cfg, commit


def train_items(network,tok,composite,head,paths,device,per_family=PER_FAMILY):
    require(1<=per_family<=64,'TRAIN_FAMILY_LIMIT')
    selected={k:[] for k in ('encounter','allocation')};reject=Counter()
    seen=set()
    for row in cur.load_rows(paths['dataset']):
        family=row['family']
        if row['split']!='train' or family not in selected or len(selected[family])>=per_family:
            continue
        digest=hashlib.sha256(row['prompt'].encode()).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        item=structural._item_capture(network,tok,composite,row,device)
        if item['reachable']==0:
            reject['no_reachable_gold']+=1;continue
        author=legacy.truth(row['graph'])
        if item['reachable']!=len(author):
            reject['upstream_unreachable_gold']+=1;continue
        surface=structural._surface(head,item,device)
        item['objects']=surface['g1_objects']
        require(structural._gold_keys(surface,item['targets'])==author,
                'TRAIN_LABEL_ANCHOR_DRIFT')
        selected[family].append(item)
        if all(len(s)==per_family for s in selected.values()):break
    require(all(len(s)==per_family for s in selected.values()),
            'NOT_ENOUGH_BALANCED_REACHABLE_TRAIN_CASES')
    return [*selected['encounter'],*selected['allocation']],dict(reject)


def differentiable_loss(head,item,device):
    """G2 loss with same-kind competing endpoint ranking, train-only gold."""
    logits=structural._forward(head,item,device)
    targets=item['targets'].to(device)
    n=targets.shape[0]
    if n<2:raise ValueError('MIT_GAMER_TRAIN_TOO_FEW_OBJECTS')
    eligible=~torch.eye(n,dtype=torch.bool,device=device)
    ce=F.cross_entropy(logits.reshape(-1,logits.shape[-1]),
           targets.reshape(-1),reduction='none').reshape(n,n)
    weights=torch.where(targets>0,torch.full_like(ce,4.),torch.ones_like(ce))
    loss=(ce*weights*eligible).sum()/(weights*eligible).sum().clamp_min(1)
    margins=logits[...,1:]-logits[...,0:1]
    ranking=[]
    for i,j in (targets>0).nonzero(as_tuple=False).tolist():
        cls=int(targets[i,j])-1
        kind=item['spans'][j]['kind']
        rivals=[k for k in range(n) if k!=i and k!=j and
            item['spans'][k]['kind']==kind and int(targets[i,k])!=cls+1]
        if rivals:
            ranking.append(F.softplus(.75 + margins[i,rivals,cls].max()-margins[i,j,cls]))
    if ranking:loss=loss+torch.stack(ranking).mean()
    return loss


def edit_masks(head,items,device,*,fraction=MAX_EDIT_FRACTION):
    """Pick a fixed, sparse, gradient-sensitive subset of G2 coefficients."""
    require(0<fraction<=.05,'EDIT_BUDGET')
    head.requires_grad_(False)
    named=dict(head.named_parameters())
    require(all(k in named for k in EDITABLE),'EDITABLE_PARAMETERS_MISSING')
    for key in EDITABLE:named[key].requires_grad_(True)
    head.zero_grad(set_to_none=True)
    total=0.0
    for item in items:
        loss=differentiable_loss(head,item,device)/len(items)
        require(bool(torch.isfinite(loss).item()),'SALIENCY_LOSS_NONFINITE')
        loss.backward()
        total+=float(loss.detach())
    saliencies=[named[key].grad.detach().abs().reshape(-1) for key in EDITABLE]
    all_scores=torch.cat(saliencies)
    budget=max(1,math.ceil(len(all_scores)*fraction))
    require(torch.isfinite(all_scores).all().item(),'NONFINITE_SALIENCY')
    require(bool((all_scores>0).any().item()),'NO_SALIENCY')
    top=torch.topk(all_scores,budget,largest=True,sorted=False).indices
    mask=torch.zeros_like(all_scores,dtype=torch.bool)
    mask[top]=True
    masks={};offset=0
    for key in EDITABLE:
        size=named[key].numel()
        masks[key]=mask[offset:offset+size].reshape(named[key].shape).clone()
        offset+=size
    head.zero_grad(set_to_none=True)
    return masks,{'editable_coefficients':len(all_scores),
        'selected_coefficients':budget,'fraction':budget/len(all_scores),
        'mean_initial_loss':total}


def sparse_train(head,items,device,*,steps=STEPS,lr=LR,fraction=MAX_EDIT_FRACTION,
                 max_delta=MAX_ABS_DELTA):
    require(1<=steps<=5000 and 0<lr<=.01 and 0<max_delta<=.2,
            'EDIT_TRAINING_BOUNDS')
    torch.manual_seed(SEED)
    head.eval()
    masks,selection=edit_masks(head,items,device,fraction=fraction)
    named=dict(head.named_parameters())
    start={key:named[key].detach().clone() for key in EDITABLE}
    params=[named[key] for key in EDITABLE]
    optimizer=torch.optim.AdamW(params,lr=lr,weight_decay=0.)
    history=[]
    for step in range(1,steps+1):
        optimizer.zero_grad(set_to_none=True)
        for item in items:
            (differentiable_loss(head,item,device)/len(items)).backward()
        for key in EDITABLE:
            named[key].grad.mul_(masks[key])
        torch.nn.utils.clip_grad_norm_(params,1.)
        optimizer.step()
        with torch.no_grad():
            for key in EDITABLE:
                # Exact preservation, not merely an L2 regularizer.
                delta=(named[key]-start[key]).clamp(-max_delta,max_delta)
                named[key].copy_(start[key]+delta*masks[key])
        if step==1 or step%25==0 or step==steps:
            head.zero_grad(set_to_none=True)
            with torch.no_grad():
                value=sum(float(differentiable_loss(head,it,device)) for it in items)/len(items)
            history.append({'step':step,'loss':value})
            print('MIT_GAMER_WEIGHT_EDIT '+json.dumps(history[-1],sort_keys=True),flush=True)
    with torch.no_grad():
        edited=sum(int((named[k]-start[k]).abs().gt(1e-10).sum().item()) for k in EDITABLE)
        max_observed=max(float((named[k]-start[k]).abs().max().item()) for k in EDITABLE)
        require(edited<=selection['selected_coefficients'] and
                max_observed<=max_delta+1e-6,'EDIT_SCOPE_OVERRUN')
        require(all(torch.equal(named[k][~masks[k]],start[k][~masks[k]])
                    for k in EDITABLE),'UNSELECTED_COEFFICIENT_CHANGED')
    selection.update({'actually_changed':edited,'max_abs_delta_observed':max_observed,
        'max_abs_delta_allowed':max_delta,'steps':steps,'lr':lr,
        'history':history,'training_only':True})
    return masks,selection


def train(*,root=ROOT_DEFAULT,output=None,device='cuda',steps=STEPS,
          per_family=PER_FAMILY,fraction=MAX_EDIT_FRACTION,lr=LR):
    dest=campaign(root,output)
    paths,parent,cfg,commit=verify(dest,root,gold=False)
    require(not (dest/'checkpoint').exists(),'TRAINING_ALREADY_COMMITTED')
    network,tok,composite,head,manifest=structural._load_runtime(paths,device)
    head.eval()
    items,rejections=train_items(network,tok,composite,head,paths,device,per_family=per_family)
    # Seal the selected training IDs before the update, separate from held-out gold.
    selected=[{'family':it['row']['family'],
        'prompt_sha256':hashlib.sha256(it['row']['prompt'].encode()).hexdigest(),
        'gold_reachable_edges':it['reachable']} for it in items]
    write(dest/'train-cases.json',{'rows':selected,'rejected':rejections,
                                 'dataset_sha256':commit['source_dataset_sha256']})
    masks,detail=sparse_train(head,items,device,steps=steps,lr=lr,fraction=fraction)
    ckpt=dest/'checkpoint';ckpt.mkdir(exist_ok=False)
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
              str(ckpt/cur.HEAD_FILE))
    save_file({key:m.cpu().contiguous() for key,m in masks.items()},
              str(ckpt/'mit-edit-mask.safetensors'))
    checkpoint_manifest=dict(manifest)
    checkpoint_manifest.update({'stage':'G2','status':'TRAINED_NOT_CERTIFIED',
        'head_sha256':sha(ckpt/cur.HEAD_FILE),
        'next_training_gate':'LOCKED_PENDING_CERTIFICATION',
        'g3_training_unlocked':False,'production_promotion':False,
        'mit_gamer':{'schema':SCHEMA,'version':VERSION,
           'source_head_sha256':commit['source_head_sha256'],
           'mask_sha256':sha(ckpt/'mit-edit-mask.safetensors'),
           'train_cases_sha256':sha(dest/'train-cases.json'),
           'sparse_edit':detail,'frozen_g1':True,'frozen_g3_plus':True,
           'decoder_sha256':commit['decoder_sha256'],
           'g3_training_unlocked':False,'certified':False}})
    write(ckpt/'manifest.json',checkpoint_manifest)
    compared=structural.native.compare_frozen_stage(paths['parent_0087']/'training',ckpt)
    require(compared['non_g2_weights_mutated'] is False,'FROZEN_STAGES_CHANGED')
    # Independently verify that only the FOUR authorized parameter tensors changed.
    from safetensors.torch import load_file
    base=load_file(str(paths['parent_0087']/'training'/cur.HEAD_FILE),device='cpu')
    modified=load_file(str(ckpt/cur.HEAD_FILE),device='cpu')
    require(base.keys()==modified.keys() and
            all(k in EDITABLE or torch.equal(base[k],modified[k]) for k in base),
            'UNAUTHORIZED_HEAD_WEIGHT_MUTATION')
    changed=sum(int((base[k]!=modified[k]).sum()) for k in EDITABLE)
    require(changed<=detail['selected_coefficients'],'POSTSAVE_EDIT_BUDGET')
    report={'schema':SCHEMA,'version':VERSION,
            'status':'MIT_G2_SPARSE_WEIGHT_EDIT_TRAINED_NOT_CERTIFIED',
            'source_head_sha256':commit['source_head_sha256'],
            'head_sha256':checkpoint_manifest['head_sha256'],
            'manifest_sha256':sha(ckpt/'manifest.json'),
            'mask_sha256':sha(ckpt/'mit-edit-mask.safetensors'),
            'train_cases_sha256':sha(dest/'train-cases.json'),
            'parent_stage_mutation':compared,
            'changed_coefficients':changed,'edit':detail,
            'heldout_evaluated':False,'g3_unlocked':False,'production_promotion':False}
    write(dest/'training-report.json',report)
    print('MIT_GAMER_TRAINED '+json.dumps({'changed_coefficients':changed,
        'head_sha256':report['head_sha256'],'status':report['status']},sort_keys=True),flush=True)
    return report


def _trained(dest,root,paths,commit):
    dest=Path(dest)
    rep=read(dest/'training-report.json')
    man=read(dest/'checkpoint'/'manifest.json')
    require(rep.get('schema')==SCHEMA and man.get('mit_gamer',{}).get('version')=='MIT' and
        man['head_sha256']==rep['head_sha256']==sha(dest/'checkpoint'/cur.HEAD_FILE) and
        rep['manifest_sha256']==sha(dest/'checkpoint'/'manifest.json') and
        rep['mask_sha256']==sha(dest/'checkpoint'/'mit-edit-mask.safetensors') and
        rep['train_cases_sha256']==sha(dest/'train-cases.json') and
        rep['source_head_sha256']==commit['source_head_sha256'] and
        man['mit_gamer']['source_head_sha256']==commit['source_head_sha256'] and
        man['mit_gamer']['decoder_sha256']==commit['decoder_sha256'] and
        man.get('g3_training_unlocked') is False and man.get('production_promotion') is False and
        rep.get('g3_unlocked') is False,'TRAINED_HEAD_DRIFT')
    return man


def infer_row(row,network,tok,composite,heads,spec,device):
    prepared, hidden = legacy.previous._capture_inference(
        network,tok,row['prompt'],device,2048)
    g1=composite.predict(hidden,prepared,family=row.get('family','unlabeled'),native_stage='G1')
    elig=prepared['eligible'].to(device)
    spans=g1['token_spans']
    raw=legacy._infer(heads['base'],hidden,elig,spans,prepared,spec,with_margins=True)
    revised=legacy._infer(heads['mit'],hidden,elig,spans,prepared,spec,with_margins=True)
    require(raw['g1_objects']==revised['g1_objects'],'G1_PREDICTION_DRIFT')
    base_greedy=legacy.decode(raw,spec)
    altered_greedy=legacy.decode(revised,spec)
    control,ctrl_details=projector.decode(raw,spec,return_diagnostics=True)
    mit,mit_details=projector.decode(revised,spec,return_diagnostics=True)
    serialize=lambda x:[list((a,list(sa),b,list(sb),t)) for a,sa,b,sb,t in sorted(x)]
    return {k:row[k] for k in ('case_id','split','family','prompt_sha256') if k in row} | {
        'g1_objects':raw['g1_objects'],
        'arms':{'legacy_greedy':serialize(base_greedy),
                'mit_projection_only':serialize(control),
                'mit_weight_edit_only':serialize(altered_greedy),
                'mit_full':serialize(mit)},
        'mit_solver':mit_details,'control_solver':ctrl_details}


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=campaign(root,output)
    paths,parent,cfg,commit=verify(dest,root,gold=False)
    _trained(dest,root,paths,commit)
    require(not (dest/'blind').exists(),'BLIND_ALREADY_COMMITTED')
    network,tok,composite,base,manifest=structural._load_runtime(paths,device)
    edited,checked=cur._load_checkpoint(dest/'checkpoint',device=device)
    require(checked['head_sha256']==sha(dest/'checkpoint'/cur.HEAD_FILE),'EDITED_HEAD_MISMATCH')
    for h in (base,edited):h.eval().requires_grad_(False)
    prompts=list(rows(dest/'seal'/'prompts.jsonl'))
    require(len(prompts)==DEV+CHALLENGE,'SEALED_PROMPT_COUNT')
    result=[]
    for k,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==row['prompt_sha256'],
                'BLIND_PROMPT_DRIFT')
        result.append(infer_row(row,network,tok,composite,
                {'base':base,'mit':edited},cfg['winner'],device))
        if k==1 or k%10==0 or k==len(prompts):
            print(f'MIT_GAMER_BLIND {k}/{len(prompts)}',flush=True)
    (dest/'blind').mkdir(exist_ok=False)
    write_jsonl(dest/'blind'/'predictions.jsonl',result)
    lock={'schema':SCHEMA,'version':VERSION,'state':'GOLD_BLIND_PREDICTIONS_LOCKED',
        'source_head_sha256':commit['source_head_sha256'],
        'mit_head_sha256':checked['head_sha256'],
        'prompts_sha256':commit['prompts_sha256'],
        'predictions_sha256':sha(dest/'blind'/'predictions.jsonl'),
        'cases':len(result),'gold_accessed_during_inference':False,
        'g3_unlocked':False,'production_promotion':False}
    write(dest/'blind'/'lock.json',lock)
    print('MIT_GAMER_BLIND_LOCKED '+json.dumps({'cases':len(result),
                'predictions_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def score(*,root=ROOT_DEFAULT,output=None):
    dest=campaign(root,output)
    paths,parent,cfg,commit=verify(dest,root,gold=True)
    _trained(dest,root,paths,commit)
    require(not (dest/'scored').exists(),'SCORE_ALREADY_COMMITTED')
    blind=read(dest/'blind'/'lock.json')
    require(blind.get('schema')==SCHEMA and
        blind.get('state')=='GOLD_BLIND_PREDICTIONS_LOCKED' and
        blind['source_head_sha256']==commit['source_head_sha256'] and
        blind['mit_head_sha256']==sha(dest/'checkpoint'/cur.HEAD_FILE) and
        blind['prompts_sha256']==commit['prompts_sha256'] and
        blind['predictions_sha256']==sha(dest/'blind'/'predictions.jsonl') and
        blind['gold_accessed_during_inference'] is False and
        blind['g3_unlocked'] is False,'BLIND_LOCK_DRIFT')
    predicted=list(rows(dest/'blind'/'predictions.jsonl'))
    answers={r['case_id']:r for r in rows(dest/'seal'/'answers.SECRET.jsonl')}
    prompts={r['case_id']:r for r in rows(dest/'seal'/'prompts.jsonl')}
    require(len(predicted)==len(answers)==len(prompts)==DEV+CHALLENGE,
            'BLIND_OR_ANSWER_COUNT')
    scores={split:{arm:Counter() for arm in ('legacy_greedy','mit_projection_only',
            'mit_weight_edit_only','mit_full')} for split in ('dev','challenge')}
    families={split:{family:{arm:Counter() for arm in scores[split]}
              for family in ('encounter','allocation')}
              for split in scores}
    cases=[]
    for r in predicted:
        key=r['case_id'];gold=answers.get(key);question=prompts.get(key)
        require(gold is not None and question is not None and
            all(r.get(k)==gold.get(k)==question.get(k) for k in
                ('case_id','split','family','prompt_sha256')) and
            hashlib.sha256(question['prompt'].encode()).hexdigest()==r['prompt_sha256'],
            'GOLD_PREDICTION_ALIGNMENT')
        truth=legacy.truth(gold['graph'])
        record={'case_id':key,'split':r['split'],'family':r['family'],'arms':{}}
        for arm,edgelist in r['arms'].items():
            edges={(a,tuple(sa),b,tuple(sb),t) for a,sa,b,sb,t in edgelist}
            tp=len(edges&truth);fp=len(edges-truth);fn=len(truth-edges)
            metrics={'exact':int(edges==truth),'tp':tp,'fp':fp,'fn':fn}
            record['arms'][arm]=metrics
            for out in (scores[r['split']][arm],families[r['split']][r['family']][arm]):
                out.update({'cases':1,'exact':metrics['exact'],'tp':tp,'fp':fp,'fn':fn})
        cases.append(record)
    def summary(counter):
        return {'cases':counter['cases'],'exact':counter['exact'],
            'exact_rate':counter['exact']/max(1,counter['cases']),
            'edge_f1':2*counter['tp']/max(1,2*counter['tp']+counter['fp']+counter['fn']),
            'tp':counter['tp'],'fp':counter['fp'],'fn':counter['fn']}
    split_results={split:{arm:summary(count) for arm,count in arms.items()}
                   for split,arms in scores.items()}
    family_results={split:{fam:{arm:summary(v) for arm,v in arms.items()}
                    for fam,arms in x.items()} for split,x in families.items()}
    # Active MIT remains an EXPERIMENT; independent certification is separate.
    comparison={split:{'exact_delta':split_results[split]['mit_full']['exact']-
                    split_results[split]['legacy_greedy']['exact'],
            'edge_f1_delta':split_results[split]['mit_full']['edge_f1']-
                    split_results[split]['legacy_greedy']['edge_f1']}
                    for split in split_results}
    report={'schema':SCHEMA,'version':VERSION,
        'status':'MIT_GAMER_FRESH_GENERALIZATION_MEASURED_NOT_CERTIFIED',
        'splits':split_results,'families':family_results,
        'deltas_vs_legacy':comparison,
        'source_head_sha256':commit['source_head_sha256'],
        'mit_head_sha256':blind['mit_head_sha256'],
        'blind_predictions_sha256':blind['predictions_sha256'],
        'answers_sha256':commit['answers_sha256'],
        'decoder_sha256':commit['decoder_sha256'],
        'g2_certified':False,'g3_unlocked':False,'production_promotion':False,
        'limitations':'Same synthetic generator, not independently distributed; no G3 promotion.'}
    (dest/'scored').mkdir(exist_ok=False)
    write(dest/'scored'/'report.json',report)
    write_jsonl(dest/'scored'/'cases.jsonl',cases)
    print('MIT_GAMER_SCORED '+json.dumps({'status':report['status'],
            'deltas_vs_legacy':comparison},sort_keys=True),flush=True)
    return report


def query(*,prompt,root=ROOT_DEFAULT,output=None,device='cuda'):
    """MIT-first live G2 entry point; no game/family or gold provided by caller."""
    require(isinstance(prompt,str) and prompt.strip(),'EMPTY_PROMPT')
    dest=campaign(root,output)
    # Immediately usable MIT cutover: projection-only on the certified-lineage
    # legacy G2 weights, then automatically use the versioned sparse-edited
    # checkpoint when the separate training campaign has produced one.
    paths,parent,spec=sources(root)
    network,tok,composite,base,_=structural._load_runtime(paths,device)
    if (dest/'checkpoint').is_dir():
        paths,_,cfg,commit=verify(dest,root,gold=False)
        _trained(dest,root,paths,commit)
        edited,manifest=cur._load_checkpoint(dest/'checkpoint',device=device)
        spec=cfg['winner']
        weight_edit_active=True
    else:
        require(not (dest/'training-report.json').exists(),
                'INCOMPLETE_EDITED_CHECKPOINT')
        edited=base
        manifest=parent
        weight_edit_active=False
    for h in (base,edited):h.eval().requires_grad_(False)
    row={'case_id':'live','split':'live','family':'unlabeled',
         'prompt':prompt,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()}
    item=infer_row(row,network,tok,composite,
                   {'base':base,'mit':edited},spec,device)
    return {'version':'MIT','status':'MIT_G2_PARTIAL_GRAPH_NOT_CERTIFIED',
        'model_head_sha256':manifest['head_sha256'],
        'weight_edit_active':weight_edit_active,
        'g1_objects':item['g1_objects'],
        'edges':item['arms']['mit_full'],
        'baseline_edges':item['arms']['legacy_greedy'],
        'solver':item['mit_solver'],
        'labels_used_at_inference':False,'g3_unlocked':False,
        'production_promotion':False}


def run(*,root=ROOT_DEFAULT,output=None,device='cuda',steps=STEPS,
        per_family=PER_FAMILY,fraction=MAX_EDIT_FRACTION,lr=LR):
    dest=campaign(root,output)
    prepare(root=root,output=dest)
    jobs=[('train',{'steps':steps,'per_family':per_family,'fraction':fraction,'lr':lr}),
          ('predict',{}),('score',{})]
    for cmd,extra in jobs:
        args=[sys.executable,'-m',MODULE,cmd,'--root',str(root),'--output',str(dest)]
        if cmd!='score':args.extend(['--device',device])
        for key,value in extra.items():args.extend(['--'+key.replace('_','-'),str(value)])
        subprocess.run(args,check=True)
    return read(dest/'scored'/'report.json')


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='cmd',required=True)
    for command in ('prepare','train','predict','score','query','run','verify'):
        p=sub.add_parser(command)
        p.add_argument('--root',default=ROOT_DEFAULT)
        p.add_argument('--output')
        if command in ('train','predict','query','run'):
            p.add_argument('--device',default='cuda')
        if command in ('train','run'):
            p.add_argument('--steps',type=int,default=STEPS)
            p.add_argument('--per-family',type=int,default=PER_FAMILY)
            p.add_argument('--fraction',type=float,default=MAX_EDIT_FRACTION)
            p.add_argument('--lr',type=float,default=LR)
        if command=='query':p.add_argument('--prompt',required=True)
        if command=='verify':p.add_argument('--gold',action='store_true')
    a=ap.parse_args(argv)
    kw={'root':a.root,'output':a.output}
    if a.cmd in ('train','predict','query','run'):kw['device']=a.device
    if a.cmd in ('train','run'):
        kw.update(steps=a.steps,per_family=a.per_family,
                  fraction=a.fraction,lr=a.lr)
    if a.cmd=='query':kw['prompt']=a.prompt
    if a.cmd=='verify':
        result=verify(campaign(a.root,a.output),a.root,gold=a.gold)
        print('MIT_GAMER_VERSION_VERIFIED '+json.dumps({'version':'MIT',
                 'source_head_sha256':result[3]['source_head_sha256']},sort_keys=True))
        return result
    return globals()[a.cmd](**kw)


if __name__=='__main__':main()
