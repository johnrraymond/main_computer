#!/usr/bin/env python3
"""HybridLink Stage 3: generated Qwen latent feedback and learned answer exit.

Frozen Stage1 Qwen; trainable continuous-embedding HybridLink + generated-state
Answer Detector. Gold transitions supervise *training only*, never runtime.
No Qwen LM/vocabulary head, text generation, or arithmetic executor in rollout.
A stagnant unfinished state is STALLED, not ANSWER. No automatic promotion.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import random

from tools import nanojev_hybridlink_frozen_detector_cutover as fc
from tools import nanojev_qwen_numeric_dual_cutover as prev
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA='nanojev.hybridlink.generated-latent-loop.v1'


def chains_from_rows(rows, *, allow_partial=False):
    """Reconstruct suffix curriculum strictly from recorded training transitions."""
    mapping={}
    for r in rows:
        a,b=r['source'],r['target']
        if not a.startswith('PROBLEM: ') or not (b.startswith('PROBLEM: ') or b.startswith('ANSWER: ')):
            raise ValueError('LATENT_INVALID_TRANSITION')
        if r['action']!=('ANSWER' if b.startswith('ANSWER: ') else 'PROBLEM'):
            raise ValueError('LATENT_ACTION_MISMATCH')
        if a in mapping and mapping[a]!=b:
            raise RuntimeError('LATENT_AMBIGUOUS_TRANSITION')
        mapping[a]=b
    roots=sorted(set(mapping)-{b for b in mapping.values() if b.startswith('PROBLEM: ')})
    chains=[]; visited=set()
    for root in roots:
        chain=[root];seen={root}
        while chain[-1] in mapping:
            nxt=mapping[chain[-1]]
            if nxt in seen:raise RuntimeError('LATENT_CYCLIC_TRANSITIONS')
            chain.append(nxt);seen.add(nxt);visited.add(chain[-2])
        if not chain[-1].startswith('ANSWER: '):
            if not allow_partial:raise RuntimeError('LATENT_INCOMPLETE_CHAIN')
            continue
        chains.append(chain)
    if visited!=set(mapping):
        raise RuntimeError('LATENT_UNREACHABLE_OR_CYCLIC_TRANSITIONS')
    if not chains:raise RuntimeError('LATENT_NO_COMPLETE_CHAINS')
    return chains


def make_link(hidden,embed):
    from torch import nn
    inner=max(64,hidden//2)
    return nn.Sequential(nn.LayerNorm(hidden),nn.Linear(hidden,inner),nn.GELU(),nn.Linear(inner,embed))


def token_embeddings(bb,tok,text,device,max_tokens):
    import torch
    ids=tok(text,add_special_tokens=False)['input_ids']
    if not ids or len(ids)+1>max_tokens:
        raise ValueError('LATENT_SOURCE_EXCEEDS_CONTEXT')
    emb=bb.get_input_embeddings()
    indices=torch.tensor([ids],device=device,dtype=torch.long)
    return emb(indices).detach()


def feedback_step(bb,link,prefix,h,device,*,zero_latent=False):
    """Append exactly one generated latent embedding; no oracle, tokenizer or LM head."""
    import torch
    new=link(h.float()).to(prefix.dtype)[:,None,:]
    if zero_latent:new=torch.zeros_like(new)
    if new.shape[-1]!=prefix.shape[-1]:raise RuntimeError('LATENT_EMBED_DIMENSION_MISMATCH')
    seq=torch.cat((prefix,new),dim=1)
    mask=torch.ones(seq.shape[:2],device=device,dtype=torch.long)
    next_h=bb.get_decoder()(inputs_embeds=seq,attention_mask=mask,
                            return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
    if not torch.isfinite(next_h).all():raise RuntimeError('LATENT_NONFINITE_HIDDEN')
    return seq,next_h


def train_suffix(bb,tok,link,det,chain,lookup,initial_h,device,max_tokens,completion_weight):
    """Teacher hidden targets used only as losses; embeddings are generated online."""
    import torch
    import torch.nn.functional as F
    context=token_embeddings(bb,tok,chain[0],device,max_tokens)
    if context.shape[1]+len(chain)-1>max_tokens:raise ValueError('LATENT_UNROLL_EXCEEDS_CONTEXT')
    h=initial_h
    total=torch.zeros((),device=device)
    align=[];finish=[]
    for i,target in enumerate(chain[1:]):
        context,h=feedback_step(bb,link,context,h,device)
        gold=lookup[target].to(device=device,dtype=torch.float32).view_as(h)
        # Supervise the actual *post-feedback* hidden state, not just a head output.
        similarity=F.cosine_similarity(h.float(),gold,dim=-1)
        lvec=1.-similarity.mean()+.25*F.smooth_l1_loss(h.float(),gold)
        is_answer=float(target.startswith('ANSWER: '))
        done=torch.full((h.shape[0],),is_answer,device=device)
        lhalt=F.binary_cross_entropy_with_logits(det(h.float()).squeeze(-1),done)
        total=total+lvec+completion_weight*lhalt
        align.append(float(similarity.detach().mean()))
        finish.append(float(lhalt.detach()))
    return total/len(chain[1:]),{'alignment':sum(align)/len(align),'completion':sum(finish)/len(finish)}


def stopping_action(before,after,prob,threshold,drift_threshold,stable_count,patience):
    observation=fc.classify_latent_step(before,after,prob,threshold,drift_threshold)
    next_count=stable_count+1 if observation['action']=='STALLED' else 0
    action='ANSWER' if observation['action']=='ANSWER' else ('STALLED' if next_count>=patience else 'CONTINUE')
    return action,next_count,observation


def rollout(bb,tok,link,det,numeric,source,device,max_tokens,scale,threshold,
            drift_threshold,stall_patience,max_depth,encode=None,control='intact',return_vector=False):
    """Real generated feedback. No supervised transitions or gold state in signature."""
    import torch
    if control not in ('intact','zero_latent'):raise ValueError('LATENT_UNKNOWN_CONTROL')
    if source.startswith('ANSWER: '):raise ValueError('LATENT_EXPECTS_UNFINISHED_ROOT')
    if not source.startswith('PROBLEM: '):raise ValueError('LATENT_UNKNOWN_START')
    if not 0<threshold<=1 or max_depth<1 or stall_patience<1 or drift_threshold<0:
        raise ValueError('LATENT_INVALID_RUNTIME_CONTROLS')
    link.eval();det.eval();numeric.eval();bb.eval()
    state={'status':'BUDGET_EXHAUSTED','steps':0,'history':[],'control':control,
           'headless':True,'inference_oracle_used':False,'uses_gold_intermediate_states':False}
    with torch.no_grad():
        prefix=token_embeddings(bb,tok,source,device,max_tokens)
        # Initial state is the actual Qwen representation, not a gold target.
        if encode is None:
            h=bb.get_decoder()(inputs_embeds=prefix,attention_mask=torch.ones(prefix.shape[:2],device=device,dtype=torch.long),
                               return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
        else:
            h=encode(source)
        fixed=0
        for step in range(1,max_depth+1):
            if prefix.shape[1]+1>max_tokens:
                state['status']='CONTEXT_EXHAUSTED'
                break
            prefix,new_h=feedback_step(bb,link,prefix,h,device,zero_latent=(control=='zero_latent'))
            p=float(torch.sigmoid(det(new_h.float()).squeeze(-1))[0])
            action,fixed,measurement=stopping_action(h,new_h,p,threshold,drift_threshold,fixed,stall_patience)
            state['history'].append({'step':step,'p_answer':p,'relative_drift':measurement['relative_drift'],
                                     'action':action})
            state['steps']=step
            h=new_h
            if action in ('ANSWER','STALLED'):
                state['status']=action
                break
        output=numeric(h.float(),scale)
        state['direct_number']=float(output['direct_number'][0])
        state['complex_number']=float(output['complex_number'][0])
        state['complex_a']=float(output['a'][0]);state['complex_b']=float(output['b'][0])
        state['readout_delta_rho']=float(output['delta_rho'][0])
        if return_vector:state['_final_vector']=h.float().detach().cpu()
    return state


def _runtime(args):
    """Check all parent hashes before loading runtime; never encode gold deck."""
    import torch
    s=prev.stage1();parent_cfg,parent=prev.load_parent(args)
    model,tok,probe,sources,frozen,dirs=prev._load_qwen(s,args,parent_cfg,parent)
    bb=model.backbone;bb.requires_grad_(False);bb.eval()
    src={'bb':bb,'parent_hash':prev.file_hash(args.stage1_checkpoint)}
    numeric,parentdet,dcfg,dck=fc._load_sidecars(args,src)
    if sources!=dcfg['source_sha256'] or frozen!=dcfg['frozen_backbone_sha256']:
        raise RuntimeError('LATENT_SOURCE_PROVENANCE_MISMATCH')
    if prev.file_hash(args.detector_checkpoint)!=args._det_sha:
        raise RuntimeError('LATENT_DETECTOR_CHANGED_DURING_LOAD')
    return s,model,tok,numeric,parentdet,dcfg,parent_cfg


def _load_runtime_checkpoint(args):
    import torch
    path=Path(args.checkpoint)
    cfg=json.loads((path.parent/'run_config.json').read_text(encoding='utf8'))
    ck=torch.load(path,map_location='cpu',weights_only=True)
    if cfg.get('schema')!=SCHEMA or ck.get('schema')!=SCHEMA or ck.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('LATENT_CHECKPOINT_CONFIG_DRIFT')
    for path_arg,key in [('stage1_checkpoint','stage1_sha256'),('detector_checkpoint','detector_sha256'),
                         ('text_checkpoint','text_sha256')]:
        if prev.file_hash(getattr(args,path_arg))!=cfg[key]:raise RuntimeError('LATENT_CHECKPOINT_PARENT_DRIFT:'+key)
    return cfg,ck


def _save_weights(out,cfg,link,det,step,metrics):
    fc._atomic_tensor(out/'best.pt',{'schema':SCHEMA,'config_hash':base.digest(cfg),'step':step,
       'link':{k:v.detach().cpu().clone() for k,v in link.state_dict().items()},
       'detector':{k:v.detach().cpu().clone() for k,v in det.state_dict().items()},
       'dev':metrics,'status':'GENERATED_LATENT_UNCERTIFIED'})


def _select_dev_chains(chains,count):
    """Deterministically stratify by depth instead of mostly scoring short hops."""
    buckets={}
    for ch in sorted(chains,key=lambda c:(len(c),c[0])):
        buckets.setdefault(len(ch)-1,[]).append(ch)
    selected=[]
    while len(selected)<min(count,len(chains)):
        progressed=False
        for depth in sorted(buckets,reverse=True):
            if buckets[depth]:
                selected.append(buckets[depth].pop(0));progressed=True
            if len(selected)>=count:break
        if not progressed:break
    return selected


def _eval_rollouts(bb,tok,link,det,num,chains,args,scale,control='intact',references=None):
    cases=_select_dev_chains(chains,args.eval_cases)
    reports=[]
    for ch in cases:
        result=rollout(bb,tok,link,det,num,ch[0],args.device,args.max_tokens,scale,
                       args.answer_threshold,args.drift_threshold,args.stall_patience,args.max_depth,
                       control=control,return_vector=references is not None)
        vector=result.pop('_final_vector',None)
        cos=None
        if vector is not None and ch[-1] in references:
            import torch.nn.functional as F
            cos=float(F.cosine_similarity(vector, references[ch[-1]].reshape(1,-1).float(),dim=-1)[0])
        # Reference answer consulted only after generated rollout, never inside it.
        expected=int(ch[-1][8:].strip())
        direct=result['direct_number'];complex_value=result['complex_number']
        target_code=math.asinh((expected-1)/scale)
        direct_error=abs(math.asinh((direct-1)/scale)-target_code) if math.isfinite(direct) else None
        complex_error=abs(math.asinh((complex_value-1)/scale)-target_code) if math.isfinite(complex_value) else None
        reports.append({'final_hidden_cosine':cos,'direct_code_abs_error':direct_error,
                        'complex_code_abs_error':complex_error,'status':result['status'],'steps':result['steps'],'expected_steps':len(ch)-1,
                        'correct_stop_depth':result['status']=='ANSWER' and result['steps']==len(ch)-1,
                        'early_false_stop':result['status']=='ANSWER' and result['steps']<len(ch)-1,
                        'late_stop':result['status']=='ANSWER' and result['steps']>len(ch)-1,
                        'source':ch[0], 'readout_delta_rho':result['readout_delta_rho']})
    n=len(reports)
    valid_cos=[r['final_hidden_cosine'] for r in reports if r['final_hidden_cosine'] is not None]
    good_direct=[r['direct_code_abs_error'] for r in reports if r['direct_code_abs_error'] is not None]
    good_complex=[r['complex_code_abs_error'] for r in reports if r['complex_code_abs_error'] is not None]
    summary={'cases':n,'mean_final_hidden_cosine':sum(valid_cos)/len(valid_cos) if valid_cos else None,
             'mean_direct_code_abs_error':sum(good_direct)/len(good_direct) if good_direct else None,
             'mean_complex_code_abs_error':sum(good_complex)/len(good_complex) if good_complex else None,
             'correct_stop_depth':sum(r['correct_stop_depth'] for r in reports),
             'early_false_stops':sum(r['early_false_stop'] for r in reports),
             'late_stops':sum(r['late_stop'] for r in reports),
             'stalled':sum(r['status']=='STALLED' for r in reports),
             'budget_exhausted':sum(r['status']=='BUDGET_EXHAUSTED' for r in reports),
             'context_exhausted':sum(r['status']=='CONTEXT_EXHAUSTED' for r in reports),
             'answer_exits':sum(r['status']=='ANSWER' for r in reports),
             'depths_tested':sorted(set(r['expected_steps'] for r in reports)),
             'control':control,'prefix_supervision_only':False,'actual_generated_feedback':True,
             'oracle_at_inference':False}
    return summary,reports

def preview(args):
    meta,_=prev.load_parent(args)
    s=prev.stage1();decks={k:chains_from_rows(prev._rows(s,meta,k),allow_partial=(k=='dev')) for k in ('train','dev')}
    report={'schema':SCHEMA,'stage':'preview','no_model_loaded':True,
            'chains':{k:len(v) for k,v in decks.items()},
            'chain_depths':{k:sorted(set(len(ch)-1 for ch in v)) for k,v in decks.items()},
            'headless':True,'frozen_qwen':True,'requires_trained_detector':True}
    print('LATENT_LOOP_PREVIEW='+json.dumps(report,sort_keys=True),flush=True)
    return report


def train(args):
    import torch
    if not args.detector_checkpoint:raise ValueError('LATENT_DETECTOR_CHECKPOINT_REQUIRED')
    if args.steps<1 or args.eval_every<1 or args.max_unroll<1 or args.max_depth<1 or args.lr<=0:
        raise ValueError('LATENT_INVALID_TRAIN_ARGUMENTS')
    protected=[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent,Path(args.detector_checkpoint).parent]
    if args.cache_dir:protected.extend([Path(args.cache_dir)])
    prev._output_safe(args.output,protected)
    args.checkpoint=args.detector_checkpoint
    args._det_sha=prev.file_hash(args.detector_checkpoint)
    prepared=fc._prepare(args,need_teacher=False)
    bb=prepared['bb'];tok=prepared['tok']
    numeric,det,dcfg,_=fc._load_sidecars(args,prepared)
    for p in numeric.parameters():p.requires_grad_(False)
    chains={split:chains_from_rows(prepared['deck'][split],allow_partial=(split=='dev')) for split in ('train','dev')}
    emb=bb.get_input_embeddings();hidden=int(bb.config.hidden_size);dimension=int(emb.embedding_dim)
    torch.manual_seed(args.seed)
    link=make_link(hidden,dimension).to(args.device)
    if args.reset_detector:
        det=fc.detector_head(hidden,args.device)
    det.train();link.train()
    optimizer=torch.optim.AdamW([{'params':link.parameters(),'lr':args.lr},
                                 {'params':det.parameters(),'lr':args.detector_lr}],weight_decay=0)
    lookup={r['text']:v for r,v in zip(prepared['states']['train'],prepared['embeddings']['train'])}
    sources={r['text']:v for r,v in zip(prepared['states']['train'],prepared['embeddings']['train'])}
    maxr=min(8,max(len(c)-1 for c in chains['train']))
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    cfg={'schema':SCHEMA,'stage':'train-generated-loop',
         'stage1_sha256':prepared['parent_hash'],'detector_sha256':args._det_sha,
         'text_sha256':prev.file_hash(args.text_checkpoint),
         'source_sha256':prepared['source_hash'],'frozen_backbone_sha256':prepared['frozen_hash'],
         'deck_sha256':prepared['parent_cfg']['deck_sha256'],
         'scale':dcfg['scale'],'seed':args.seed,'max_tokens':args.max_tokens,
         'max_unroll':args.max_unroll,'max_depth':args.max_depth,'steps':args.steps,
         'eval_every':args.eval_every,'lr':args.lr,'detector_lr':args.detector_lr,
         'completion_weight':args.completion_weight,'answer_threshold':args.answer_threshold,
         'drift_threshold':args.drift_threshold,'stall_patience':args.stall_patience,
         'eval_cases':args.eval_cases,'frozen_qwen':True,'lm_head_used':False,
         'teacher_targets_at_inference':False,'direct_feedback':True,
         'train_chain_count':len(chains['train']),'dev_chain_count':len(chains['dev'])}
    base._atomic_json(out/'run_config.json',cfg)
    dev_reference={r['text']:v for r,v in zip(prepared['states']['dev'],prepared['embeddings']['dev'])}
    rng=random.Random(args.seed)
    best=-math.inf;beststep=None
    for step in range(1,args.steps+1):
        # T00 -> T08 backward suffix curriculum, with truncated unroll for memory.
        rung=1+((step-1)//max(1,args.steps//maxr))%maxr
        eligible=[ch for ch in chains['train'] if len(ch)-1>=rung]
        if not eligible:raise RuntimeError('LATENT_MISSING_CURRICULUM_RUNG')
        chain=rng.choice(eligible)
        horizon=min(rung,args.max_unroll)
        start=len(chain)-1-rung
        suffix=chain[start:start+horizon+1]
        h=sources[suffix[0]].to(args.device).float().unsqueeze(0)
        # Frozen backbone permits gradients *through* decoder to generated input,
        # but no Qwen parameter receives a gradient or optimizer update.
        loss,part=train_suffix(bb,tok,link,det,suffix,lookup,h,args.device,args.max_tokens,args.completion_weight)
        optimizer.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(list(link.parameters())+list(det.parameters()),1.)
        optimizer.step()
        if step%args.eval_every and step!=args.steps:continue
        with torch.no_grad():
            dev,records=_eval_rollouts(bb,tok,link,det,numeric,chains['dev'],args,dcfg['scale'],references=dev_reference)
        score=dev['correct_stop_depth']-3*dev['early_false_stops']-.2*dev['stalled']
        if score>best:
            best=score;beststep=step;_save_weights(out,cfg,link,det,step,dev)
            base._atomic_json(out/'best_dev_cases.json',records)
        print('LATENT_LOOP_PROGRESS='+json.dumps({'step':step,'rung':rung,'unroll':horizon,
           'train_loss':round(float(loss.detach()),6),'train_parts':part,'dev':dev,
           'best_step':beststep,'best_score':best,'qwen_updated':False},sort_keys=True),flush=True)
    if any(p.grad is not None for p in bb.parameters()):raise RuntimeError('LATENT_FROZEN_QWEN_GRADIENT')
    if prev.file_hash(args.detector_checkpoint)!=args._det_sha or prev.file_hash(args.stage1_checkpoint)!=prepared['parent_hash']:
        raise RuntimeError('LATENT_PARENT_MUTATED')
    result={'schema':SCHEMA,'status':'GENERATED_FEEDBACK_TRAINED_NOT_CERTIFIED',
            'checkpoint':str(out/'best.pt'),'best_step':beststep,
            'qwen_unchanged':True,'parent_detector_unchanged':True,
            'oracle_at_inference':False,'lm_head_used':False,
            'dev':torch.load(out/'best.pt',map_location='cpu',weights_only=True)['dev']}
    base._atomic_json(out/'result.json',result)
    print('LATENT_LOOP_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def evaluate(args):
    import torch
    if not args.detector_checkpoint or not args.checkpoint:raise ValueError('LATENT_REQUIRE_BOTH_CHECKPOINTS')
    cfg,ck=_load_runtime_checkpoint(args)
    args._det_sha=prev.file_hash(args.detector_checkpoint)
    args.checkpoint=args.detector_checkpoint
    s,model,tok,numeric,det,dcfg,pmeta=_runtime(args)
    bb=model.backbone
    link=make_link(bb.config.hidden_size,bb.get_input_embeddings().embedding_dim).to(args.device)
    link.load_state_dict(ck['link'],strict=True);det.load_state_dict(ck['detector'],strict=True)
    args.max_tokens=cfg['max_tokens'];args.answer_threshold=cfg['answer_threshold']
    args.drift_threshold=cfg['drift_threshold'];args.stall_patience=cfg['stall_patience'];args.max_depth=cfg['max_depth']
    if args.stage=='inspect':
        if not args.state:raise ValueError('LATENT_STATE_REQUIRED')
        report=rollout(bb,tok,link,det,numeric,args.state,args.device,args.max_tokens,dcfg['scale'],
                       args.answer_threshold,args.drift_threshold,args.stall_patience,args.max_depth,control=args.control)
        report['schema']=SCHEMA
        print('LATENT_LOOP_INSPECT='+json.dumps(report,sort_keys=True),flush=True)
        return report
    if args.split=='train':raise ValueError('LATENT_EVALUATION_ON_TRAIN_REFUSED')
    chain=chains_from_rows(prev._rows(s,pmeta,args.split),allow_partial=True)
    # GOLD: encode completed target states for post-hoc scoring only. They are
    # never passed to rollout(), the link, or the Answer Detector.
    from torch import no_grad
    target_strings=sorted(set(ch[-1] for ch in chain))
    with no_grad():
        refs=s._encode(bb,tok,target_strings,args.device,args.encoder_batch,cfg['max_tokens']).detach().cpu()
    references=dict(zip(target_strings,refs))
    summary,examples=_eval_rollouts(bb,tok,link,det,numeric,chain,args,dcfg['scale'],control=args.control,references=references)
    alternative='zero_latent' if args.control=='intact' else 'intact'
    control,control_examples=_eval_rollouts(bb,tok,link,det,numeric,chain,args,dcfg['scale'],control=alternative,references=references)
    report={'schema':SCHEMA,'split':args.split,'summary':summary,'cases':examples,
            'ablation':control,'ablation_cases':control_examples,
            'intact_minus_ablation_correct_stops':summary['correct_stop_depth']-control['correct_stop_depth'] if args.control=='intact' else control['correct_stop_depth']-summary['correct_stop_depth'],
            'status':'LATENT_ROLLOUT_EVALUATED_NOT_AUTO_CERTIFIED'}
    print('LATENT_LOOP_PROBE='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe','inspect'),default='preview')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint',required=True);p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--detector-checkpoint');p.add_argument('--checkpoint')
    p.add_argument('--state');p.add_argument('--control',choices=('intact','zero_latent'),default='intact')
    p.add_argument('--split',choices=('dev','ood','test','train'),default='dev')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_generated_latent_loop_v1'))
    p.add_argument('--cache-dir');p.add_argument('--steps',type=int,default=800)
    p.add_argument('--eval-every',type=int,default=100);p.add_argument('--eval-cases',type=int,default=16)
    p.add_argument('--encoder-batch',type=int,default=12);p.add_argument('--max-tokens',type=int,default=192)
    p.add_argument('--max-unroll',type=int,default=2);p.add_argument('--max-depth',type=int,default=14)
    p.add_argument('--lr',type=float,default=.0001);p.add_argument('--detector-lr',type=float,default=.00005)
    p.add_argument('--completion-weight',type=float,default=.5)
    p.add_argument('--answer-threshold',type=float,default=.9)
    p.add_argument('--drift-threshold',type=float,default=1e-5);p.add_argument('--stall-patience',type=int,default=2)
    p.add_argument('--seed',type=int,default=42257);p.add_argument('--reset-detector',action='store_true')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if not(0<args.answer_threshold<=1 and 0<args.eval_cases and 0<=args.drift_threshold and args.stall_patience>=1):
        p.error('LATENT_INVALID_THRESHOLDS')
    return {'preview':preview,'train':train,'probe':evaluate,'inspect':evaluate}[args.stage](args)

if __name__=='__main__':main()
