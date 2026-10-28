#!/usr/bin/env python3
"""Frozen Qwen numerical-readout + Answer Detector cutover (v1).

Stage 1 source Qwen LoRA and its transition probe are immutable. Train numerical
and answer-completion sidecars on cached *actual Qwen* hidden states. This does
not implement the HybridLink recurrence or assert that label-prefixed training
states generalize to generated latent states.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random

from tools import nanojev_qwen_numeric_dual_cutover as prev
from tools import nanojev_numeric_complex_contract as nc
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA = 'nanojev.hybridlink.frozen-numeric-detector-cutover.v1'
CACHE_SCHEMA = 'nanojev.hybridlink.frozen-vector-cache.v1'
HANDOFF_SCHEMA = 'nanojev.hybridlink.frozen-detector-handoff.v1'


def _torch():
    import torch
    return torch


def detector_head(hidden,device='cpu'):
    from torch import nn
    return nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,max(16,hidden//4)),
                         nn.GELU(),nn.Linear(max(16,hidden//4),1)).to(device)


def is_complete_state(state):
    if state.startswith('ANSWER: '):
        prev.numeric_label(state)  # validate arithmetic syntax and target type
        return True
    if state.startswith('PROBLEM: '):
        prev.numeric_label(state)
        return False
    raise ValueError('FROZEN_DETECTOR_UNKNOWN_STATE')


def states_from_rows(rows):
    """Unique source + target boundaries; all intermediate PROBLEM states negative.

    In particular, a valid intermediate number remains a negative completion
    label even if its value is one.
    """
    states={}
    for r in rows:
        for text in (r['source'],r['target']):
            if text not in states:
                states[text]={'text':text,'done':is_complete_state(text),
                              'value':prev.numeric_label(text)}
    return list(states.values())


def _digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),
                                     default=str).encode()).hexdigest()


def _atomic_tensor(path,obj):
    import torch
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.partial')
    try:
        torch.save(obj,tmp)
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def _load_or_encode(path,description,strings,bb,tok,s,args,identity):
    """Cache only immutable hidden vectors; key exact ordered text and provenance."""
    import torch
    key=_digest({'schema':CACHE_SCHEMA,'description':description,
                 'texts':strings,'identity':identity,'max_tokens':args.max_tokens,
                 'encoder_batch':args.encoder_batch})
    path=Path(path);file=path/(description+'-'+key[:24]+'.pt')
    if file.exists():
        item=torch.load(file,map_location='cpu',weights_only=True)
        if (item.get('schema')!=CACHE_SCHEMA or item.get('key')!=key or
            item.get('identity')!=identity):
            raise RuntimeError('FROZEN_VECTOR_CACHE_PROVENANCE_MISMATCH')
        emb=item.get('vectors')
        if not isinstance(emb,torch.Tensor) or emb.ndim!=2 or emb.shape!=(len(strings),int(bb.config.hidden_size)) or not torch.isfinite(emb).all():
            raise RuntimeError('FROZEN_VECTOR_CACHE_CORRUPT')
        print('FROZEN_CACHE_HIT='+json.dumps({'split':description,'rows':len(strings)},sort_keys=True),flush=True)
        return emb, True
    print('FROZEN_CACHE_ENCODING='+json.dumps({'split':description,'rows':len(strings),'batch_size':args.encoder_batch},sort_keys=True),flush=True)
    parts=[]
    with torch.no_grad():
        for start in range(0,len(strings),args.encoder_batch):
            batch=strings[start:start+args.encoder_batch]
            parts.append(s._encode(bb,tok,batch,args.device,args.encoder_batch,args.max_tokens).detach().to('cpu').float())
            if (start//args.encoder_batch+1)%20==0 or start+args.encoder_batch>=len(strings):
                print('FROZEN_CACHE_PROGRESS='+json.dumps({'split':description,'done':min(len(strings),start+args.encoder_batch),'total':len(strings)},sort_keys=True),flush=True)
    vec=torch.cat(parts,dim=0)
    if vec.ndim!=2 or vec.shape!=(len(strings),int(bb.config.hidden_size)) or not torch.isfinite(vec).all():
        raise RuntimeError('FROZEN_ENCODER_BAD_VECTORS')
    _atomic_tensor(file,{'schema':CACHE_SCHEMA,'key':key,'identity':identity,'vectors':vec})
    print('FROZEN_CACHE_WRITE='+json.dumps({'split':description,'rows':len(strings)},sort_keys=True),flush=True)
    return vec, False


def _prepare(args,need_teacher=True):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    s=prev.stage1();parent_cfg,parent=prev.load_parent(args)
    parent_hash=prev.file_hash(args.stage1_checkpoint)
    model,tok,probe,sources,frozen,dirs=prev._load_qwen(s,args,parent_cfg,parent)
    if args.cache_dir:
        cache_dir=Path(args.cache_dir)
    elif args.stage in ('probe','inspect') and args.checkpoint:
        cache_dir=Path(args.checkpoint).parent/'frozen_cache'
    else:
        cache_dir=Path(args.output)/'frozen_cache'
    for source in (*dirs,Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent):
        if cache_dir.resolve()==Path(source).resolve() or cache_dir.resolve() in Path(source).resolve().parents or Path(source).resolve() in cache_dir.resolve().parents:
            raise RuntimeError('FROZEN_CACHE_OVERLAPS_PROTECTED_SOURCE')
    bb=model.backbone
    bb.requires_grad_(False);bb.eval();probe.requires_grad_(False);probe.eval()
    pre_lora=zero.lora_state(bb)
    prehash={k:hashlib.sha256(v.detach().cpu().contiguous().numpy().tobytes()).hexdigest() for k,v in pre_lora.items()}
    frozenhash=zero.frozen_backbone_sha256(bb)
    if frozenhash!=frozen:raise RuntimeError('FROZEN_SOURCE_WEIGHTS_CHANGED_BEFORE_TRAIN')
    deck={split:prev._rows(s,parent_cfg,split) for split in ('train','dev')}
    raw={split:states_from_rows(rows) for split,rows in deck.items()}
    train_keys={r['text'] for r in raw['train']}
    shared=sum(r['text'] in train_keys for r in raw['dev'])
    raw['dev']=[r for r in raw['dev'] if r['text'] not in train_keys]
    if not any(r['done'] for r in raw['dev']) or not any(not r['done'] for r in raw['dev']):
        raise RuntimeError('FROZEN_NO_DISJOINT_DEV_DETECTOR_CLASSES')
    identity={'qwen_checkpoint_sha256':parent_hash,'backbone_sha256':frozen,
              'encoder_code_sha256':prev.file_hash(s.__file__) if getattr(s,'__file__',None) else 'mock-encoder',
              'source_sha256':sources,'deck_sha256':parent_cfg['deck_sha256']}
    embeddings={};hits={}
    for split in ('train','dev'):
        vec,hit=_load_or_encode(cache_dir,'parent-'+split,[r['text'] for r in raw[split]],
                                bb,tok,s,args,identity)
        embeddings[split]=vec;hits[split]=hit
    teacher=None;teacher_hit=None
    if need_teacher:
        old_cfg=json.loads((Path(args.text_checkpoint).parent/'run_config.json').read_text(encoding='utf8'))
        old=torch.load(args.text_checkpoint,weights_only=True,map_location='cpu')
        if old.get('schema')!=old_cfg.get('schema') or old.get('config_hash')!=base.digest(old_cfg):
            raise RuntimeError('FROZEN_TEXT_TEACHER_DRIFT')
        # Teacher LoRA is different, so cache and restore under separate provenance.
        zero.restore_lora(bb,old['lora'])
        try:
            teacher,teacher_hit=_load_or_encode(cache_dir,'teacher-dev',
                [r['target'] for r in deck['dev']],bb,tok,s,args,
                {'text_sha256':prev.file_hash(args.text_checkpoint),'source_sha256':sources,
                 'deck_sha256':parent_cfg['deck_sha256']})
        finally:
            zero.restore_lora(bb,pre_lora)
    # Restore always, and independently verify both static and LoRA weights.
    for name,p in zero.lora_state(bb).items():
        if hashlib.sha256(p.detach().cpu().contiguous().numpy().tobytes()).hexdigest()!=prehash[name]:
            raise RuntimeError('FROZEN_QWEN_LORA_CHANGED')
    if zero.frozen_backbone_sha256(bb)!=frozenhash:raise RuntimeError('FROZEN_QWEN_BASE_CHANGED')
    return {'model':model,'tok':tok,'probe':probe,'bb':bb,'s':s,'parent_cfg':parent_cfg,
            'parent':parent,'parent_hash':parent_hash,'source_hash':sources,'frozen_hash':frozen,
            'lora_hashes':prehash,'deck':deck,'states':raw,'embeddings':embeddings,
            'cache_hits':hits,'teacher':teacher,'teacher_hit':teacher_hit,'cache_dir':cache_dir,
            'dev_overlap_removed':shared,'identity':identity}


def detector_loss(head,h,done):
    import torch.nn.functional as F
    return F.binary_cross_entropy_with_logits(head(h.float()).squeeze(-1),done.float())


def _evaluate_heads(numeric,detector,h,states,scale,threshold):
    import torch
    h=h.to(next(numeric.parameters()).device)
    numeric.eval();detector.eval()
    outputs=[];p=[]
    with torch.no_grad():
        for offset in range(0,len(states),128):
            part=h[offset:offset+128]
            outputs.append(numeric(part,scale))
            p.extend(torch.sigmoid(detector(part).squeeze(-1)).cpu().tolist())
    values=[r['value'] for r in states]
    pairs={k:torch.cat([o[k].detach().cpu() for o in outputs]) for k in outputs[0]}
    nd=nc.diagnostics(pairs,values,scale)
    done=[r['done'] for r in states]
    pos=[v for v,y in zip(p,done) if y]
    neg=[v for v,y in zip(p,done) if not y]
    tp=sum(v>=threshold for v in pos);fp=sum(v>=threshold for v in neg)
    near=[i for i,r in enumerate(states) if not r['done'] and r['value']==1]
    return {'numeric':nd,'detector':{
      'count':len(done),'positives':len(pos),'negatives':len(neg),
      'threshold':threshold,'true_positive_rate':round(tp/max(1,len(pos)),6),
      'false_positive_rate':round(fp/max(1,len(neg)),6),
      'balanced_accuracy':round(.5*(tp/max(1,len(pos))+(len(neg)-fp)/max(1,len(neg))),6),
      'hard_unity_intermediate_count':len(near),
      'hard_unity_intermediate_false_stops':sum(p[i]>=threshold for i in near),
      'mean_answer_probability':round(sum(pos)/max(1,len(pos)),6),
      'mean_unfinished_probability':round(sum(neg)/max(1,len(neg)),6),
      'prefix_supervision_only':True}}


def vector_drift(before,after,eps=1e-12):
    import torch
    if before.shape!=after.shape or before.ndim not in (1,2):
        raise ValueError('FROZEN_DRIFT_VECTOR_SHAPE')
    a=before.detach().float();b=after.detach().float()
    if not torch.isfinite(a).all() or not torch.isfinite(b).all():
        raise ValueError('FROZEN_DRIFT_NONFINITE')
    return (torch.linalg.vector_norm(b-a,dim=-1)/torch.linalg.vector_norm(a,dim=-1).clamp_min(eps)).cpu()


def classify_latent_step(before,after,answer_probability,answer_threshold=.9,drift_threshold=1e-5):
    """For a *real HybridLink-generated pair*; not an inference executor.

    The caller must compare states in matched Qwen contexts. Stable-but-incomplete
    is STALLED, never ANSWER. A detector-positive state may terminate independently
    of the drift. Only independently measured completion can yield ANSWER.
    """
    import torch
    delta=vector_drift(before,after)
    if delta.numel()!=1:raise ValueError('FROZEN_DRIFT_REQUIRES_SINGLE_PAIR')
    prob=float(answer_probability)
    if not math.isfinite(prob) or not 0<=prob<=1:raise ValueError('FROZEN_INVALID_ANSWER_PROBABILITY')
    if not (0<answer_threshold<=1 and drift_threshold>=0):raise ValueError('FROZEN_INVALID_STABILITY_THRESHOLD')
    return {'action':'ANSWER' if prob>=answer_threshold else ('STALLED' if float(delta.item())<=drift_threshold else 'CONTINUE'),
            'relative_drift':float(delta.item()),'answer_probability':prob,
            'answer_threshold':answer_threshold,'drift_threshold':drift_threshold}


def _load_sidecars(args,prepared):
    import torch
    p=Path(args.checkpoint)
    cfg=json.loads((p.parent/'run_config.json').read_text(encoding='utf8'))
    if cfg.get('schema')!=SCHEMA or cfg.get('parent_sha256')!=prepared['parent_hash']:
        raise RuntimeError('FROZEN_CHECKPOINT_PARENT_OR_SCHEMA_DRIFT')
    ck=torch.load(p,weights_only=True,map_location='cpu')
    if ck.get('schema')!=SCHEMA or ck.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('FROZEN_CHECKPOINT_INVALID')
    hidden=int(prepared['bb'].config.hidden_size)
    num=nc._numeric_head(hidden,args.device);num.load_state_dict(ck['numeric'],strict=True)
    det=detector_head(hidden,args.device);det.load_state_dict(ck['detector'],strict=True)
    return num,det,cfg,ck


def preview(args):
    parentcfg,_=prev.load_parent(args);s=prev.stage1()
    decks={k:states_from_rows(prev._rows(s,parentcfg,k)) for k in ('train','dev')}
    report={'schema':SCHEMA,'stage':'preview','model_loaded':False,
            'parent_sha256':prev.file_hash(args.stage1_checkpoint),
            'counts':{k:{'states':len(v),'answers':sum(x['done'] for x in v),
                          'unfinished':sum(not x['done'] for x in v)} for k,v in decks.items()},
            'qwen_frozen':True,'lm_head_used':False,'latent_loop_active':False}
    print('FROZEN_DETECTOR_PREVIEW='+json.dumps(report,sort_keys=True),flush=True)
    return report


def train(args):
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    if args.steps<1 or args.batch_size<2 or args.encoder_batch<1 or args.max_tokens<1 or args.eval_every<1 or args.numeric_lr<=0 or args.numeric_weight<=0:
        raise ValueError('FROZEN_TRAIN_ARGUMENTS')
    prev._output_safe(args.output,[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent])
    if Path(args.output).exists() and any(Path(args.output).iterdir()) and not args.cache_dir:
        raise RuntimeError('FROZEN_OUTPUT_REQUIRES_SEPARATE_CACHE_DIR_FOR_REUSE')
    prepared=_prepare(args,need_teacher=True)
    bb=prepared['bb'];probe=prepared['probe'];s=prepared['s']
    # Stage1 retention baseline is evaluated once, not every 100 training steps.
    with torch.no_grad():
        parent_dev=s._evaluation(bb,prepared['tok'],probe,prepared['deck']['dev'],
           prepared['teacher'].to(args.device),args.device,args.encoder_batch,args.max_tokens)
    hidden=int(bb.config.hidden_size)
    torch.manual_seed(args.seed)
    num=nc._numeric_head(hidden,args.device)
    det=detector_head(hidden,args.device)
    optimizer=torch.optim.AdamW([{'params':num.parameters(),'lr':args.numeric_lr},
                                 {'params':det.parameters(),'lr':args.numeric_lr}],weight_decay=0.)
    x=prepared['embeddings']['train'].to(args.device)
    states=prepared['states']['train']
    pos=[i for i,r in enumerate(states) if r['done']]
    neg=[i for i,r in enumerate(states) if not r['done']]
    if not pos or not neg:raise RuntimeError('FROZEN_TRAIN_MISSING_CLASS')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    config={'schema':SCHEMA,'stage':'train-frozen-sidecars','parent_sha256':prepared['parent_hash'],
            'text_parent_sha256':prev.file_hash(args.text_checkpoint),'scale':args.scale,
            'source_sha256':prepared['source_hash'],'frozen_backbone_sha256':prepared['frozen_hash'],
            'deck_sha256':prepared['parent_cfg']['deck_sha256'],
            'max_tokens':args.max_tokens,'encoder_batch':args.encoder_batch,
            'train_count':len(states),'dev_count':len(prepared['states']['dev']),
            'seed':args.seed,'steps':args.steps,'eval_every':args.eval_every,
            'numeric_lr':args.numeric_lr,'batch_size':args.batch_size,
            'answer_threshold':args.answer_threshold,'numeric_weight':args.numeric_weight,
            'parent_dev_payload_accuracy':parent_dev['payload_accuracy'],
            'parent_dev_control_accuracy':parent_dev['control_accuracy'],
            'dev_overlap_removed':prepared['dev_overlap_removed'],
            'no_lm_head':True,'qwen_frozen':True,'hybridlink_running':False}
    base._atomic_json(out/'run_config.json',config)
    rng=random.Random(args.seed)
    best=None;best_score=-math.inf
    for step in range(1,args.steps+1):
        num.train();det.train()
        half=max(1,args.batch_size//2)
        selected=rng.choices(pos,k=half)+rng.choices(neg,k=args.batch_size-half)
        rng.shuffle(selected)
        state=[states[i] for i in selected]
        h=x[selected]
        numeric_loss,parts=nc.loss(num(h,args.scale),[r['value'] for r in state],args.scale)
        targets=torch.tensor([r['done'] for r in state],device=args.device,dtype=torch.float32)
        completion_loss=detector_loss(det,h,targets)
        total=args.numeric_weight*numeric_loss+completion_loss
        if not torch.isfinite(total):raise RuntimeError('FROZEN_NONFINITE_TRAIN_LOSS')
        optimizer.zero_grad(set_to_none=True);total.backward()
        torch.nn.utils.clip_grad_norm_([*num.parameters(),*det.parameters()],1.)
        optimizer.step()
        if step%args.eval_every and step!=args.steps:continue
        metrics=_evaluate_heads(num,det,prepared['embeddings']['dev'],
                                prepared['states']['dev'],args.scale,args.answer_threshold)
        nd=metrics['numeric'];dd=metrics['detector']
        # Refuse to chase accuracy by rewarding false stops: use an explicit
        # zero-false-stop criterion first, then balanced recall and geometry.
        score=(-10*dd['false_positive_rate'] + dd['true_positive_rate'] +
               .1*(nd['direct_code_within_0_1']+nd['complex_code_within_0_1']))
        if score>best_score:
            best_score=score;best=step
            _atomic_tensor(out/'best.pt',{'schema':SCHEMA,'config_hash':base.digest(config),
                 'parent_sha256':prepared['parent_hash'],'step':step,
                 'numeric':{k:v.detach().cpu().clone() for k,v in num.state_dict().items()},
                 'detector':{k:v.detach().cpu().clone() for k,v in det.state_dict().items()},
                 'dev_metrics':metrics,'status':'UNVALIDATED_FOR_LATENT_FEEDBACK'})
        print('FROZEN_DETECTOR_PROGRESS='+json.dumps({'step':step,'best_step':best,
              'dev':metrics,'training_numeric_loss':round(float(numeric_loss.detach()),6),
              'training_completion_loss':round(float(completion_loss.detach()),6),
              'stage1_payload_accuracy_preserved':parent_dev['payload_accuracy'],
              'qwen_updated':False,'teacher_cached':prepared['teacher_hit']},sort_keys=True),flush=True)
    # Verify Qwen & its control probe have not been updated in any way.
    for name,t in zero.lora_state(bb).items():
        if hashlib.sha256(t.detach().cpu().contiguous().numpy().tobytes()).hexdigest()!=prepared['lora_hashes'][name]:
            raise RuntimeError('FROZEN_BACKBONE_LORA_DRIFT')
    if zero.frozen_backbone_sha256(bb)!=prepared['frozen_hash']:
        raise RuntimeError('FROZEN_BASE_BACKBONE_DRIFT')
    if any(p.grad is not None for p in bb.parameters()):
        raise RuntimeError('FROZEN_BACKBONE_GRADIENT_DETECTED')
    if any(p.grad is not None for p in probe.parameters()):
        raise RuntimeError('FROZEN_STAGE1_PROBE_GRADIENT_DETECTED')
    report={'schema':SCHEMA,'status':'TRAINED_SIDECARS_NOT_LATENT_CERTIFIED',
            'checkpoint':str(out/'best.pt'),'best_step':best,'best_score':best_score,
            'stage1_dev_payload_accuracy':parent_dev['payload_accuracy'],
            'stage1_dev_control_accuracy':parent_dev['control_accuracy'],
            'qwen_frozen_verified':True,'stage1_probe_frozen_verified':True,
            'parent_unchanged':prev.file_hash(args.stage1_checkpoint)==prepared['parent_hash'],
            'dev_overlap_removed':prepared['dev_overlap_removed'],
            'cache_hits':prepared['cache_hits'],'teacher_cache_hit':prepared['teacher_hit'],
            'latent_feedback_untested':True}
    base._atomic_json(out/'result.json',report)
    print('FROZEN_DETECTOR_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def probe(args):
    if not args.checkpoint:raise ValueError('FROZEN_CHECKPOINT_REQUIRED')
    pre=_prepare(args,need_teacher=False)
    num,det,cfg,ck=_load_sidecars(args,pre)
    metrics=_evaluate_heads(num,det,pre['embeddings']['dev'],pre['states']['dev'],
                            cfg['scale'],cfg['answer_threshold'])
    report={'schema':SCHEMA,'status':'FROZEN_DEV_PROBE','dev':metrics,
            'qwen_frozen':True,'hybridlink_running':False,
            'evaluated_on_latent_feedback':False}
    print('FROZEN_DETECTOR_PROBE='+json.dumps(report,sort_keys=True),flush=True)
    return report


def inspect(args):
    import torch
    if not args.checkpoint or not args.state:raise ValueError('FROZEN_INSPECT_REQUIRES_CHECKPOINT_AND_STATE')
    # Inference is deliberately independent of training deck and gold parser.
    s=prev.stage1();parent_cfg,parent=prev.load_parent(args)
    model,tok,probe,sources,frozen,dirs=prev._load_qwen(s,args,parent_cfg,parent)
    pre={'bb':model.backbone,'parent_hash':prev.file_hash(args.stage1_checkpoint)}
    num,det,cfg,ck=_load_sidecars(args,pre)
    if sources!=cfg['source_sha256'] or frozen!=cfg['frozen_backbone_sha256']:
        raise RuntimeError('FROZEN_INSPECT_BACKBONE_PROVENANCE_DRIFT')
    model.backbone.eval();num.eval();det.eval()
    with torch.no_grad():
        h=s._encode(model.backbone,tok,[args.state],args.device,1,cfg['max_tokens'])
        pred=num(h,cfg['scale'])
        prob=float(torch.sigmoid(det(h).squeeze(-1))[0])
        status='CANDIDATE_ANSWER' if prob>=cfg['answer_threshold'] else 'CONTINUE_OR_STALLED_UNKNOWN'
    report={'schema':SCHEMA,'state':args.state,'detector_probability':prob,
            'detector_class':status,'direct_number':float(pred['direct_number'][0]),
            'complex_number':float(pred['complex_number'][0]),
            'a':float(pred['a'][0]),'b':float(pred['b'][0]),
            'complex_log_radius':float(pred['complex_rho'][0]),
            'readout_disagreement_log_radius':float(pred['delta_rho'][0]),
            'fixed_point_unobserved':True,'lm_head_used':False,'inference_oracle_used':False}
    print('FROZEN_DETECTOR_INSPECT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def handoff(args):
    import torch
    if not args.checkpoint:raise ValueError('FROZEN_HANDOFF_REQUIRES_CHECKPOINT')
    pre=prev.load_parent(args)
    path=Path(args.checkpoint)
    cfg=json.loads((path.parent/'run_config.json').read_text(encoding='utf8'))
    ck=torch.load(path,weights_only=True,map_location='cpu')
    if cfg.get('schema')!=SCHEMA or cfg.get('parent_sha256')!=prev.file_hash(args.stage1_checkpoint) or ck.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('FROZEN_HANDOFF_CHECKPOINT_INVALID')
    report={'schema':HANDOFF_SCHEMA,'status':'PREPARED_NOT_ACTIVATED',
            'checkpoint_path':str(path.resolve()),'checkpoint_sha256':prev.file_hash(path),
            'parent_qwen_checkpoint_sha256':cfg['parent_sha256'],
            'numerical_contract':nc.SCHEMA,'answer_threshold':cfg['answer_threshold'],
            'feature_names':['direct_rho','complex_rho','a','b','delta_rho'],
            'detector_source':'Qwen last-layer state','stalled_is_not_answer':True,
            'needs_matched_hybridlink_qwen_vectors':True,
            'needs_latent_feedback_validation':True,'lm_head_used':False}
    target=Path(args.output)
    if target.exists() and any(target.iterdir()):raise RuntimeError('FROZEN_HANDOFF_NONEMPTY_OUTPUT')
    target.mkdir(parents=True,exist_ok=True)
    base._atomic_json(target/'handoff.json',report)
    print('FROZEN_DETECTOR_HANDOFF='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe','inspect','handoff'),default='preview')
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--stage1-checkpoint',required=True)
    p.add_argument('--checkpoint');p.add_argument('--state')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_frozen_detector_v1'))
    p.add_argument('--cache-dir',help='Optional reusable frozen vector cache outside protected checkpoint dirs')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--steps',type=int,default=1200);p.add_argument('--eval-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=64);p.add_argument('--encoder-batch',type=int,default=12)
    p.add_argument('--max-tokens',type=int,default=192);p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--scale',type=float,default=64.);p.add_argument('--numeric-lr',type=float,default=.0002)
    p.add_argument('--numeric-weight',type=float,default=.5)
    p.add_argument('--answer-threshold',type=float,default=.9)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if not(math.isfinite(args.scale) and args.scale>0 and 0<args.answer_threshold<=1):
        p.error('FROZEN_INVALID_SCALE_OR_THRESHOLD')
    if args.stage in ('probe','inspect','handoff') and not args.checkpoint:
        p.error('FROZEN_CHECKPOINT_REQUIRED')
    return {'preview':preview,'train':train,'probe':probe,'inspect':inspect,'handoff':handoff}[args.stage](args)

if __name__=='__main__':main()
