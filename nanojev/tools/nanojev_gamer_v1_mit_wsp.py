"""MIT Weight-Space Programming protocol for NanoJEV Gamer G2.

This differs from the MIT cutover's gradient-trained sparse repair. There is no
backpropagation, optimizer, autograd, or training in this module. An external
reasoning LLM, optionally in a human-reviewed conversation, must *design* the
weight edit from the exported training evidence and parameter inspection.

Usage, from main_computer/nanojev (after original MIT cutover is installed):
  python -m tools.nanojev_gamer_v1_mit_wsp run --device cuda

Run resumes an already-prepared sealed cohort, or prepares it if absent, then
applies the explicitly authored source-code weight intervention, predicts and
scores. No user-supplied JSON proposal is needed. The experiment internally
saves an audit record for reproducibility. Manual subcommands remain available:
prepare, inspect, apply, predict, score and verify.

The model editor sees only the TRAIN split. A fresh development/challenge
cohort is sealed before inspection; its answers are read only by score, after
prediction hashes have been committed. G1, G3+ and parent checkpoints are never
mutated. Neither experiments nor results imply certification.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random

import torch
from safetensors.torch import load_file, save_file

from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g2_structural_learning_0089 as structural
from tools import nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 as legacy
from tools import nanojev_gamer_v1_mit as mit
from tools import nanojev_gamer_v1_mit_decoder as projector

SCHEMA = 'nanojev.gamer.graph.g2.mit-weight-space-programming.v1'
ROOT_DEFAULT = '../runtime/reports/gamer-v1'
CAMPAIGN = 'oracle-fabric-g2-mit-wsp-0001'
SEED = 261121
DEV = 48
CHALLENGE = 32
MAX_EDIT_FRACTION = .035
MAX_ABS_DELTA = 2.0
ALLOWED = mit.EDITABLE

# Explicitly authored MIT-WSP experiment, committed in Python source.
# Only the exact checkpoint inspected on 2026-11-20 is accepted for this edit.
SOURCE_HEAD_SHA256 = '85760048cbde208fc2d131c79d0b318bbe246f13a554e7f34a3cb7fa9549f2ef'
ORIGINAL_WSP_MODULE_SHA256 = '7316d8a8ec7ea7716ae528400d1429924f454a229d422195913a8cd1ef2abfbb'
# Prepared campaigns may have been sealed with the previous source-authored patch.
SOURCE_AUTHORED_WSP_MODULE_SHA256 = '5ca108c142bd0b13fc828d457201c2bdad6dd3c2905abcb1751332cf608c1362'
APPLIES_TO_CLASS_INDEX = 4
APPLIES_TO_BIAS_ORIGINAL = -0.000556803192012012
APPLIES_TO_BIAS_EDITED = -0.750556803192

WSP_DIAGNOSIS = (
    'The inspected TRAIN cohort exhibits excess graph-edge admission, '
    'particularly applies_to: encounter has 118 false positives versus 32 true '
    'positives; allocation has 39 false positives versus 36 true positives. '
    'The class-4 binding bias contributes additively to the applies_to logit, '
    'so reducing it shifts its edge-admission margin without changing pair '
    'embeddings or gradients. This is an admission-calibration causal probe; '
    'it will not fix mistaken endpoints and could reduce recall. Sealed blind '
    'results, not the training observations, determine whether it helps.'
)
WSP_OPERATION_REASON = (
    'For applies_to only, subtract 0.75 from the additive output bias. '
    'This single-coefficient intervention directly reduces edge admission '
    'scores without an optimizer, gradients or changes to other model weights.'
)


def require(condition, code):
    if not condition:
        raise ValueError('MIT_WSP_' + code)


def location(root, output):
    return (Path(output) if output else Path(root)/CAMPAIGN).resolve()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, record):
    mit.write(path, record)


def sha(path):
    return mit.sha(path)


def rows(path):
    return list(mit.rows(path))


def base_paths(root):
    mit_dest=mit.campaign(root)
    paths,parent,cfg,commit=mit.verify(mit_dest,root,gold=False)
    mit._trained(mit_dest,root,paths,commit)
    base_file=mit_dest/'checkpoint'/cur.HEAD_FILE
    require(base_file.is_file(),'BASE_MIT_CHECKPOINT_MISSING')
    return mit_dest,base_file,paths,parent,cfg,commit


def seal_fresh(paths,root,mit_dest):
    rng=random.Random(SEED)
    seen=mit.known_hashes(paths,root)
    # Also exclude the immediately preceding MIT cohort, already evaluated.
    for r in mit.rows(mit_dest/'seal'/'prompts.jsonl'):
        seen.add(r['prompt_sha256'])
    result=[]
    serial=261_121_000
    for split,count in (('dev',DEV),('challenge',CHALLENGE)):
        for i in range(count):
            factory=corpus.encounter if i%2==0 else corpus.allocation
            for _ in range(4096):
                entry=factory(rng,'test',serial)
                serial+=1
                digest=hashlib.sha256(entry['prompt'].encode('utf-8')).hexdigest()
                if digest not in seen:break
            else:raise ValueError('MIT_WSP_EXHAUSTED_FRESH_COHORT')
            require(entry.get('annotation_provenance')=='AUTHOR_MARKED_IN_SOURCE_GENERATOR',
                    'UNTRUSTED_ANNOTATION')
            seen.add(digest)
            result.append({'case_id':f'mit-wsp-{split}-{i+1:04}',
                'split':split,'family':entry['family'],'prompt':entry['prompt'],
                'prompt_sha256':digest,'graph':entry['graph']})
    return result


def readable_weights(state):
    """Small exact tensors and dimensions, no held-out labels."""
    named={}
    for k in ALLOWED:
        t=state[k].detach().float().cpu()
        named[k]={'shape':list(t.shape),'min':float(t.min()),
                  'max':float(t.max()),'mean':float(t.mean()),
                  'std':float(t.std(unbiased=False)),
                  'l2':float(torch.linalg.vector_norm(t))}
        # The final classifier is small enough for direct LLM inspection.
        if k.startswith('binding.'):
            named[k]['values']=t.tolist()
        # Summaries for the large projection reveal where signal is concentrated.
        if k=='pair_projection.weight':
            width=t.shape[0]
            named[k]['input_group_norms']={name:float(torch.linalg.vector_norm(t[:,p*width:(p+1)*width]))
                for p,name in enumerate(('left','right','product','difference'))}
            named[k]['top_12_input_columns_by_norm']=torch.topk(
                torch.linalg.vector_norm(t,dim=0),min(12,t.shape[1])).indices.tolist()
    return named


def training_evidence(items,head,device,spec):
    result=[]
    with torch.no_grad():
        for item in items:
            surface=structural._surface(head,item,device)
            actual=structural._gold_keys(surface,item['targets'])
            predicted=projector.decode(surface,spec)
            logits=structural._forward(head,item,device)
            targets=item['targets'].cpu()
            margins=(logits[...,1:]-logits[...,0:1]).detach().float().cpu()
            n=targets.shape[0]
            errors=[]
            for i in range(n):
                for j in range(n):
                    if i==j:continue
                    gold=int(targets[i,j])
                    best=int(torch.argmax(margins[i,j]))+1
                    best_score=float(margins[i,j,best-1])
                    if gold!=best or (gold==0 and best_score>0):
                        errors.append({'source':i,'destination':j,
                            'gold_label':gold,'best_model_label':best,
                            'best_margin':round(best_score,5),
                            'margins':[round(float(x),5) for x in margins[i,j].tolist()]})
            errors.sort(key=lambda e:-abs(e['best_margin']))
            result.append({'prompt':item['row']['prompt'],
                'prompt_sha256':hashlib.sha256(item['row']['prompt'].encode()).hexdigest(),
                'family':item['row']['family'],'g1_objects':surface['g1_objects'],
                'gold_edges':[list((a,list(sa),b,list(sb),t)) for a,sa,b,sb,t in sorted(actual)],
                'predicted_edges':[list((a,list(sa),b,list(sb),t)) for a,sa,b,sb,t in sorted(predicted)],
                'exact':actual==predicted,
                'false_positives':len(predicted-actual),
                'false_negatives':len(actual-predicted),
                'highest_magnitude_pair_errors':errors[:20]})
    return result


def prepare(*,root=ROOT_DEFAULT,output=None,device='cuda',per_family=8):
    dest=location(root,output)
    mit_dest,base_file,paths,parent,cfg,commit=base_paths(root)
    require(not dest.exists() and dest.parent.is_dir(),'CAMPAIGN_EXISTS_OR_PARENT_MISSING')
    cases=seal_fresh(paths,root,mit_dest)
    network,tok,composite,head,_=structural._load_runtime(paths,device)
    # Compare the current MIT model, not the pre-MIT G2 parent.
    head,_=cur._load_checkpoint(mit_dest/'checkpoint',device=device)
    head.eval().requires_grad_(False)
    items,rejections=mit.train_items(network,tok,composite,head,paths,device,per_family=per_family)
    state=load_file(str(base_file),device='cpu')
    ins={'schema':SCHEMA,'editor_instructions':(
        'Diagnose G2 graph-ranking failures from TRAIN examples and model weights. '
        'Explain the causal mechanism and propose a minimal direct weight edit. '
        'Never use gradients, optimization, loss fitting, or dev/challenge answers. '
        'This is an LLM-authored edit, not retraining. Operations: set, zero_columns, '
        'scale_columns, remove_output_direction. Provide a reason for each operation.'),
        'source_head_sha256':sha(base_file),
        'allowed_parameters':list(ALLOWED),
        'budget':{'max_fraction_of_allowed_parameters':MAX_EDIT_FRACTION,
                  'max_abs_coefficient_delta':MAX_ABS_DELTA},
        'parameter_information':readable_weights(state),
        'train_only_failure_traces':training_evidence(items,head,device,cfg['winner']),
        'training_rejections':rejections,
        'evaluation_answers_exposed':False,
        'proposal_template':{'schema':SCHEMA,'source_head_sha256':sha(base_file),
          'diagnosis':'REPLACE with a mechanism-level causal explanation',
          'editor':'human-reviewed-frontier-LLM',
          'operations':[{'op':'zero_columns','tensor':'pair_projection.weight',
                         'columns':[0],'reason':'REPLACE with causal rationale'}]}}
    dest.mkdir(exist_ok=False)
    (dest/'seal').mkdir(exist_ok=False)
    mit.write_jsonl(dest/'seal'/'prompts.jsonl',
        [{k:r[k] for k in ('case_id','split','family','prompt','prompt_sha256')} for r in cases])
    mit.write_jsonl(dest/'seal'/'answers.SECRET.jsonl',
        [{k:r[k] for k in ('case_id','split','family','prompt_sha256','graph')} for r in cases])
    write(dest/'inspection.json',ins)
    seal={'schema':SCHEMA,'state':'SEALED_BEFORE_REASONED_WEIGHT_EDIT',
          'source_head_sha256':sha(base_file),
          'source_mit_campaign_sha256':sha(mit_dest/'seal'/'commit.json'),
          'prompts_sha256':sha(dest/'seal'/'prompts.jsonl'),
          'answers_sha256':sha(dest/'seal'/'answers.SECRET.jsonl'),
          'inspection_sha256':sha(dest/'inspection.json'),
          'module_sha256':sha(__file__),
          'train_only_export':True,'independent_distribution':False,
          'g3_unlocked':False,'production_promotion':False}
    write(dest/'seal'/'commit.json',seal)
    print('MIT_WSP_PREPARED '+json.dumps({'fresh_cases':len(cases),
        'inspection':str(dest/'inspection.json'),
        'source_head_sha256':seal['source_head_sha256'],
        'status':'AWAITING_REASONING_DRIVEN_WEIGHT_PROPOSAL'},sort_keys=True),flush=True)
    return ins


def verify(dest,root,*,gold=False):
    dest=Path(dest)
    commit=read(dest/'seal'/'commit.json')
    mit_dest,base_file,paths,parent,cfg,mit_commit=base_paths(root)
    valid=(commit['schema']==SCHEMA and
        commit['state']=='SEALED_BEFORE_REASONED_WEIGHT_EDIT' and
        commit['source_head_sha256']==sha(base_file) and
        commit['source_mit_campaign_sha256']==sha(mit_dest/'seal'/'commit.json') and
        commit['prompts_sha256']==sha(dest/'seal'/'prompts.jsonl') and
        commit['inspection_sha256']==sha(dest/'inspection.json') and
        (commit['module_sha256']==sha(__file__) or
         (commit['module_sha256'] in (ORIGINAL_WSP_MODULE_SHA256,
            SOURCE_AUTHORED_WSP_MODULE_SHA256) and
          commit['source_head_sha256']==SOURCE_HEAD_SHA256)) and
        commit['train_only_export'] is True and
        commit['g3_unlocked'] is False and
        commit['production_promotion'] is False)
    if gold:valid=valid and commit['answers_sha256']==sha(dest/'seal'/'answers.SECRET.jsonl')
    require(valid,'PROVENANCE_DRIFT')
    return commit,mit_dest,base_file,paths,parent,cfg


def inspected_state(root,output):
    dest=location(root,output)
    commit,mit_dest,base_file,paths,parent,cfg=verify(dest,root)
    return dest,commit,load_file(str(base_file),device='cpu')


def inspect(*,root=ROOT_DEFAULT,output=None,tensor='binding.weight',row=None,
            start=0,count=192):
    dest,commit,state=inspected_state(root,output)
    require(tensor in ALLOWED,'INSPECT_FORBIDDEN_TENSOR')
    t=state[tensor].detach().float().cpu()
    require(t.ndim in (1,2),'INSPECT_RANK')
    if t.ndim==2:
        require(row is not None and 0<=row<t.shape[0],'INSPECT_ROW_REQUIRED')
        t=t[row]
    else:
        require(row is None,'INSPECT_VECTOR_NO_ROW')
    require(0<=start<len(t) and 1<=count<=512,'INSPECT_SLICE')
    reply={'tensor':tensor,'row':row,'offset':start,
           'source_head_sha256':commit['source_head_sha256'],
           'values':t[start:start+count].tolist()}
    print('MIT_WSP_WEIGHTS '+json.dumps(reply,sort_keys=True),flush=True)
    return reply


def authored_proposal(commit, source):
    """Emit the one inspected and human-reviewable source-authored experiment.

    The source head and original coefficient must match the evidence used to
    choose this edit. This function NEVER searches or fits a weight value.
    """
    require(commit['source_head_sha256']==SOURCE_HEAD_SHA256,
            'SOURCE_AUTHORED_EDIT_REQUIRES_INSPECTED_CHECKPOINT')
    t=source['binding.bias']
    require(t.ndim==1 and len(t)==5 and
            abs(float(t[APPLIES_TO_CLASS_INDEX])-APPLIES_TO_BIAS_ORIGINAL)<1e-6,
            'INSPECTED_CLASS_BIAS_MISMATCH')
    return {'schema':SCHEMA,
            'source_head_sha256':SOURCE_HEAD_SHA256,
            'editor':'human-reviewed-frontier-LLM-source-authored',
            'diagnosis':WSP_DIAGNOSIS,
            'operations':[{'op':'set','tensor':'binding.bias',
                'changes':[{'index':[APPLIES_TO_CLASS_INDEX],
                            'value':APPLIES_TO_BIAS_EDITED}],
                'reason':WSP_OPERATION_REASON}]}


def execute_operations(source,proposal,*,max_fraction=MAX_EDIT_FRACTION,
                       max_delta=MAX_ABS_DELTA):
    """Apply only explicit LLM-authored edits; no optimizers, losses or gradients."""
    require(isinstance(proposal,dict) and proposal.get('schema')==SCHEMA,
            'INVALID_PROPOSAL_SCHEMA')
    require(isinstance(proposal.get('diagnosis'),str) and
            len(proposal['diagnosis'].strip())>=30,'MECHANISM_DIAGNOSIS_REQUIRED')
    operations=proposal.get('operations')
    require(isinstance(operations,list) and 1<=len(operations)<=100,
            'OPERATION_COUNT')
    require(all(k in source for k in ALLOWED),'EDITABLE_SURFACE_MISSING')
    before={k:v.detach().cpu().clone() for k,v in source.items()}
    after={k:v.clone() for k,v in before.items()}
    for operation in operations:
        require(isinstance(operation,dict) and
                isinstance(operation.get('reason'),str) and
                len(operation['reason'].strip())>=12,'OPERATION_EXPLANATION_REQUIRED')
        name=operation.get('tensor')
        require(name in ALLOWED,'FORBIDDEN_TENSOR_EDIT')
        t=after[name]
        kind=operation.get('op')
        if kind=='set':
            changes=operation.get('changes')
            require(isinstance(changes,list) and 1<=len(changes)<=1024,
                    'SET_CHANGES_REQUIRED')
            for entry in changes:
                require(isinstance(entry,dict) and
                        isinstance(entry.get('index'),list) and
                        len(entry['index'])==t.ndim and
                        all(type(i) is int and 0<=i<size for i,size in zip(entry['index'],t.shape)) and
                        isinstance(entry.get('value'),(int,float)) and
                        math.isfinite(float(entry['value'])),'SET_VALUE_INVALID')
                t[tuple(entry['index'])]=float(entry['value'])
        elif kind in ('zero_columns','scale_columns','remove_output_direction'):
            require(t.ndim==2,'COLUMN_EDIT_NOT_MATRIX')
            columns=operation.get('columns')
            require(isinstance(columns,list) and 1<=len(columns)<=128 and
                    len(set(columns))==len(columns) and
                    all(type(c) is int and 0<=c<t.shape[1] for c in columns),
                    'COLUMN_INDICES_INVALID')
            if kind=='zero_columns':
                t[:,columns]=0
            elif kind=='scale_columns':
                factor=operation.get('factor')
                require(isinstance(factor,(float,int)) and math.isfinite(float(factor))
                        and -2<=factor<=2,'SCALE_FACTOR_INVALID')
                t[:,columns]*=float(factor)
            else:
                direction=operation.get('direction')
                alpha=operation.get('alpha',1.0)
                require(isinstance(direction,list) and len(direction)==t.shape[0] and
                        all(isinstance(v,(float,int)) and math.isfinite(float(v)) for v in direction) and
                        isinstance(alpha,(float,int)) and math.isfinite(float(alpha))
                        and 0<float(alpha)<=2,'DIRECTION_INVALID')
                d=torch.tensor(direction,dtype=torch.float64)
                norm=torch.linalg.vector_norm(d)
                require(bool(norm>1e-12),'ZERO_DIRECTION')
                d=(d/norm).to(dtype=t.dtype)
                original=t[:,columns].clone()
                # W'[:,J] = W[:,J] - alpha * u (u^T W[:,J]).
                t[:,columns]=original-float(alpha)*d[:,None]*(d@original)[None,:]
        else:
            raise ValueError('MIT_WSP_UNKNOWN_OPERATION')
        require(bool(torch.isfinite(t).all()),'NONFINITE_EDIT')
    changes={k:int((before[k]!=after[k]).sum()) for k in before}
    require(all(changes[k]==0 for k in before if k not in ALLOWED),
            'FROZEN_PARAMETER_MUTATION')
    selected=sum(changes[k] for k in ALLOWED)
    total=sum(before[k].numel() for k in ALLOWED)
    max_change=max(float((after[k].float()-before[k].float()).abs().max()) for k in ALLOWED)
    require(1<=selected<=math.ceil(total*max_fraction),'SPARSE_EDIT_BUDGET_EXCEEDED')
    require(max_change<=max_delta+1e-6,'COEFFICIENT_DELTA_EXCEEDED')
    require(all(torch.equal(before[k],after[k]) for k in before if k not in ALLOWED),
            'FROZEN_PARAMETER_CHANGED')
    summary={'total_editable_coefficients':total,
             'changed_coefficients':selected,
             'fraction_of_editable_surface':selected/total,
             'max_abs_delta':max_change,
             'per_tensor_changed':{k:changes[k] for k in ALLOWED},
             'method':'LLM_DESIGNED_DIRECT_WEIGHT_EDIT_NO_GRADIENTS'}
    return after,summary


def apply(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output)
    commit,mit_dest,base_file,paths,parent,cfg=verify(dest,root)
    require(not (dest/'checkpoint').exists(),'ALREADY_EDITED')
    source=load_file(str(base_file),device='cpu')
    p=authored_proposal(commit,source)
    modified,edit=execute_operations(source,p)
    # The proposal is authored in this module. Save a record in the campaign
    # solely for audit/hash provenance, never for manual user input.
    write(dest/'proposal.json',p)
    print('MIT_WSP_SOURCE_AUTHORED_EDIT '+json.dumps({
        'tensor':'binding.bias','index':APPLIES_TO_CLASS_INDEX,
        'original':APPLIES_TO_BIAS_ORIGINAL,'edited':APPLIES_TO_BIAS_EDITED,
        'source_head_sha256':SOURCE_HEAD_SHA256},sort_keys=True),flush=True)
    ckpt=dest/'checkpoint'
    ckpt.mkdir(exist_ok=False)
    save_file({k:v.contiguous() for k,v in modified.items()},str(ckpt/cur.HEAD_FILE))
    raw_manifest=read(mit_dest/'checkpoint'/'manifest.json')
    # The standard graph loader requires this exact status. The experiment
    # status is recorded separately in mit_wsp so its provenance remains clear.
    raw_manifest.update({'status':'TRAINED_NOT_CERTIFIED',
         'head_sha256':sha(ckpt/cur.HEAD_FILE),
         'g3_training_unlocked':False,'production_promotion':False,
         'mit_wsp':{'schema':SCHEMA,'version':'MIT-WSP',
            'status':'MIT_WSP_DIRECT_EDIT_NOT_CERTIFIED',
            'source_head_sha256':commit['source_head_sha256'],
            'proposal_sha256':sha(dest/'proposal.json'),'method':edit['method'],
            'changed_coefficients':edit['changed_coefficients'],
            'frozen_g1':True,'frozen_g3_plus':True,
            'no_gradients':True,'no_optimizers':True,
            'certified':False}})
    write(ckpt/'manifest.json',raw_manifest)
    # No changes to the G1/G3+ or old checkpoint can be silently admitted.
    comparison=structural.native.compare_frozen_stage(mit_dest/'checkpoint',ckpt)
    require(comparison['non_g2_weights_mutated'] is False,'FROZEN_STAGE_CHANGED')
    saved=load_file(str(ckpt/cur.HEAD_FILE),device='cpu')
    require(all(torch.equal(modified[k],saved[k]) for k in source),
            'PERSISTED_HEAD_DRIFT')
    report={'schema':SCHEMA,'status':'MIT_WSP_DIRECT_EDIT_APPLIED_NOT_EVALUATED',
         'source_head_sha256':commit['source_head_sha256'],
         'edited_head_sha256':sha(ckpt/cur.HEAD_FILE),
         'proposal_sha256':sha(dest/'proposal.json'),
         'checkpoint_manifest_sha256':sha(ckpt/'manifest.json'),
         'edit':edit,'frozen_stage_comparison':comparison,
         'heldout_answers_accessed':False,'g3_unlocked':False}
    write(dest/'edit-report.json',report)
    print('MIT_WSP_EDIT_APPLIED '+json.dumps({'status':report['status'],
        'edited_head_sha256':report['edited_head_sha256'],
        'changed_coefficients':edit['changed_coefficients']},sort_keys=True),flush=True)
    return report


def check_edit(dest,root):
    commit,mit_dest,base_file,paths,parent,cfg=verify(dest,root)
    rep=read(dest/'edit-report.json')
    head=dest/'checkpoint'/cur.HEAD_FILE
    require(rep['status']=='MIT_WSP_DIRECT_EDIT_APPLIED_NOT_EVALUATED' and
        rep['source_head_sha256']==commit['source_head_sha256'] and
        rep['edited_head_sha256']==sha(head) and
        rep['proposal_sha256']==sha(dest/'proposal.json') and
        rep['checkpoint_manifest_sha256']==sha(dest/'checkpoint'/'manifest.json') and
        read(dest/'checkpoint'/'manifest.json')['mit_wsp']['no_gradients'] is True,
        'EDIT_DRIFT')
    return commit,mit_dest,base_file,paths,parent,cfg,rep


def load_edited_head(base_checkpoint, edited_checkpoint, report, *, device):
    """Load an audited direct edit using the verified MIT parent architecture.

    Old MIT-WSP checkpoint manifests used an experimental status rejected by
    Gamer's generic checkpoint loader. Never modify a prior manifest to work
    around that mismatch: load the accepted base architecture and strictly
    install the audited edited tensor file. Both historical and new manifests
    are supported; an edited head hash or tensor-set mismatch is fatal.
    """
    edited_checkpoint=Path(edited_checkpoint)
    manifest=read(edited_checkpoint/'manifest.json')
    weights=edited_checkpoint/cur.HEAD_FILE
    require(manifest.get('schema')==cur.SCHEMA and
        manifest.get('status') in ('TRAINED_NOT_CERTIFIED',
                                   'MIT_WSP_DIRECT_EDIT_NOT_CERTIFIED') and
        manifest.get('head_sha256')==report['edited_head_sha256']==sha(weights) and
        manifest.get('mit_wsp',{}).get('source_head_sha256')==
            report['source_head_sha256'] and
        manifest.get('mit_wsp',{}).get('no_gradients') is True,
        'EDITED_CHECKPOINT_METADATA_DRIFT')
    edited,_=cur._load_checkpoint(base_checkpoint,device=device)
    edited.load_state_dict(load_file(str(weights),device='cpu'),strict=True)
    return edited.eval().requires_grad_(False)


def predict(*,root=ROOT_DEFAULT,output=None,device='cuda'):
    dest=location(root,output)
    commit,mit_dest,base_file,paths,parent,cfg,rep=check_edit(dest,root)
    require(not (dest/'blind').exists(),'BLIND_ALREADY_RUN')
    network,tok,composite,base,_=structural._load_runtime(paths,device)
    base,_=cur._load_checkpoint(mit_dest/'checkpoint',device=device)
    edited=load_edited_head(mit_dest/'checkpoint',dest/'checkpoint',rep,
                            device=device)
    for h in (base,edited):h.eval().requires_grad_(False)
    prompts=rows(dest/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE,'WRONG_PROMPT_COUNT')
    result=[]
    with torch.no_grad():
        for k,r in enumerate(prompts,1):
            require(hashlib.sha256(r['prompt'].encode()).hexdigest()==r['prompt_sha256'],
                    'PROMPT_HASH_DRIFT')
            inf=mit.infer_row(r,network,tok,composite,{'base':base,'mit':edited},cfg['winner'],device)
            result.append({**{key:r[key] for key in ('case_id','split','family','prompt_sha256')},
                'arms':{'mit_base_exact_projection':inf['arms']['mit_projection_only'],
                    'mit_wsp_direct_edit':inf['arms']['mit_full']},
                'solver':inf['mit_solver']})
            if k==1 or k%10==0 or k==len(prompts):
                print(f'MIT_WSP_BLIND {k}/{len(prompts)}',flush=True)
    blind=dest/'blind'
    blind.mkdir(exist_ok=False)
    mit.write_jsonl(blind/'predictions.jsonl',result)
    lock={'schema':SCHEMA,'state':'BLIND_PREDICTIONS_LOCKED',
        'predictions_sha256':sha(blind/'predictions.jsonl'),
        'prompt_sha256':commit['prompts_sha256'],
        'edited_head_sha256':rep['edited_head_sha256'],
        'source_head_sha256':rep['source_head_sha256'],
        'answers_accessed':False,'g3_unlocked':False,'production_promotion':False}
    write(blind/'lock.json',lock)
    print('MIT_WSP_BLIND_LOCKED '+json.dumps({'cases':len(result),
        'predictions_sha256':lock['predictions_sha256']},sort_keys=True),flush=True)
    return lock


def score(*,root=ROOT_DEFAULT,output=None):
    dest=location(root,output)
    commit,mit_dest,base_file,paths,parent,cfg,rep=check_edit(dest,root)
    require(not (dest/'scored').exists(),'ALREADY_SCORED')
    lock=read(dest/'blind'/'lock.json')
    require(lock['schema']==SCHEMA and lock['state']=='BLIND_PREDICTIONS_LOCKED' and
        lock['predictions_sha256']==sha(dest/'blind'/'predictions.jsonl') and
        lock['prompt_sha256']==commit['prompts_sha256'] and
        lock['source_head_sha256']==commit['source_head_sha256'] and
        lock['edited_head_sha256']==rep['edited_head_sha256'] and
        lock['answers_accessed'] is False,'BLIND_LOCK_DRIFT')
    # Gold file is first opened here, after blind predictions are hash-locked.
    require(sha(dest/'seal'/'answers.SECRET.jsonl')==commit['answers_sha256'],
            'GOLD_DRIFT')
    gold={r['case_id']:r for r in rows(dest/'seal'/'answers.SECRET.jsonl')}
    predicted=rows(dest/'blind'/'predictions.jsonl')
    questions={r['case_id']:r for r in rows(dest/'seal'/'prompts.jsonl')}
    require(len(predicted)==len(gold)==len(questions)==DEV+CHALLENGE,
            'CASE_COUNT_INVALID')
    splits={split:{arm:Counter() for arm in ('mit_base_exact_projection','mit_wsp_direct_edit')}
            for split in ('dev','challenge')}
    cases=[]
    for r in predicted:
        g=gold.get(r['case_id']);q=questions.get(r['case_id'])
        require(g is not None and q is not None and
          all(r.get(k)==g.get(k)==q.get(k) for k in ('case_id','split','family','prompt_sha256')),
          'CASE_ALIGNMENT_INVALID')
        truth=legacy.truth(g['graph'])
        record={'case_id':r['case_id'],'split':r['split'],'family':r['family'],'arms':{}}
        for name,edgelist in r['arms'].items():
            edges={(a,tuple(sa),b,tuple(sb),t) for a,sa,b,sb,t in edgelist}
            tp=len(edges&truth);fp=len(edges-truth);fn=len(truth-edges)
            metric={'exact':int(edges==truth),'tp':tp,'fp':fp,'fn':fn}
            record['arms'][name]=metric
            splits[r['split']][name].update({'cases':1,'exact':metric['exact'],
                                             'tp':tp,'fp':fp,'fn':fn})
        cases.append(record)
    def summary(c):
        return {'cases':c['cases'],'exact':c['exact'],
                'exact_rate':c['exact']/max(1,c['cases']),
                'edge_f1':2*c['tp']/max(1,2*c['tp']+c['fp']+c['fn']),
                'tp':c['tp'],'fp':c['fp'],'fn':c['fn']}
    summaries={split:{name:summary(c) for name,c in arms.items()} for split,arms in splits.items()}
    delta={split:{'exact_delta':arms['mit_wsp_direct_edit']['exact']-
                            arms['mit_base_exact_projection']['exact'],
                  'edge_f1_delta':arms['mit_wsp_direct_edit']['edge_f1']-
                            arms['mit_base_exact_projection']['edge_f1']}
           for split,arms in summaries.items()}
    result={'schema':SCHEMA,'status':'MIT_WSP_BLIND_GENERALIZATION_MEASURED_NOT_CERTIFIED',
            'splits':summaries,'deltas_vs_existing_mit':delta,
            'source_head_sha256':commit['source_head_sha256'],
            'edited_head_sha256':rep['edited_head_sha256'],
            'blind_sha256':lock['predictions_sha256'],
            'answers_sha256':commit['answers_sha256'],
            'no_gradients':True,'g3_unlocked':False,'production_promotion':False,
            'limitations':'Same synthetic generator; no external independent distribution.'}
    scored=dest/'scored'
    scored.mkdir(exist_ok=False)
    write(scored/'report.json',result)
    mit.write_jsonl(scored/'cases.jsonl',cases)
    print('MIT_WSP_SCORED '+json.dumps({'deltas':delta,'status':result['status']},sort_keys=True),flush=True)
    return result


def run(*,root=ROOT_DEFAULT,output=None,device='cuda',per_family=8):
    """Continue the existing sealed 80-case cohort, then predict and score.

    Each existing stage is verified by the downstream stage; partial failures
    are not silently overwritten. No caller-provided proposal file exists.
    """
    dest=location(root,output)
    if not (dest/'seal'/'commit.json').is_file():
        require(not dest.exists(),'INCOMPLETE_UNSEALED_CAMPAIGN')
        prepare(root=root,output=output,device=device,per_family=per_family)
    if not (dest/'edit-report.json').is_file():
        apply(root=root,output=output)
    if not (dest/'blind'/'lock.json').is_file():
        predict(root=root,output=output,device=device)
    if not (dest/'scored'/'report.json').is_file():
        return score(root=root,output=output)
    # Returning to an already-scored experiment never reopens held-out gold.
    report=read(dest/'scored'/'report.json')
    print('MIT_WSP_ALREADY_SCORED '+json.dumps({
        'status':report['status'],
        'deltas':report['deltas_vs_existing_mit']},sort_keys=True),flush=True)
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    for command in ('run','prepare','inspect','apply','predict','score','verify'):
        p=sub.add_parser(command)
        p.add_argument('--root',default=ROOT_DEFAULT)
        p.add_argument('--output')
        if command in ('run','prepare','predict'):p.add_argument('--device',default='cuda')
        if command in ('run','prepare'):p.add_argument('--per-family',type=int,default=8)
        if command=='inspect':
            p.add_argument('--tensor',default='binding.weight')
            p.add_argument('--row',type=int)
            p.add_argument('--start',type=int,default=0)
            p.add_argument('--count',type=int,default=192)
        if command=='verify':p.add_argument('--gold',action='store_true')
    a=ap.parse_args(argv)
    kwargs={'root':a.root,'output':a.output}
    if a.command in ('run','prepare','predict'):kwargs['device']=a.device
    if a.command in ('run','prepare'):kwargs['per_family']=a.per_family
    if a.command=='inspect':
        kwargs.update(tensor=a.tensor,row=a.row,start=a.start,count=a.count)
    if a.command=='verify':
        record,*_=verify(location(a.root,a.output),a.root,gold=a.gold)
        print('MIT_WSP_VERIFIED '+json.dumps({'base_head_sha256':record['source_head_sha256']},
              sort_keys=True),flush=True)
        return record
    return globals()[a.command](**kwargs)


if __name__=='__main__':main()
