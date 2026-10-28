#!/usr/bin/env python3
"""Non-destructive T00-first repair of the Qwen/HybridLink generated latent loop.

Only the HybridLink embedding generator is trained. The previously learned Qwen,
Answer Detector, Stage 1 probe, and numeric heads are frozen. A complete answer
is always in the supervised T00 window. Higher rungs are gated by autonomous
DEV rollouts, not scheduled to advance after an arbitrary number of steps.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import random

from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_latent_diagnostics as diag
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_qwen_numeric_dual_cutover as parent
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA = 'nanojev.hybridlink.t00-gated-curriculum-repair.v1'


def t00_pairs(chains):
    """Final hop of EVERY complete chain, including chains with deep roots."""
    return [(ch[-2],ch[-1]) for ch in chains]


def suffix_window(chain,rung,max_unroll,offset=0):
    """Window begins up to rung+1 hops before answer, never exceeds BPTT budget.

    offset=0 is answer-anchored. Larger offsets train earlier transitions.
    Unlike Patch 3, a multi-hop stage cannot accidentally omit its terminal
    answer *at every training step*.
    """
    hops=rung+1
    if len(chain)-1<hops:raise ValueError('REPAIR_CHAIN_TOO_SHALLOW')
    width=min(hops,max_unroll)
    if not 0<=offset<=hops-width:raise ValueError('REPAIR_INVALID_WINDOW_OFFSET')
    start=len(chain)-1-width-offset
    return chain[start:start+width+1]


def windows_for_rung(chain,rung,max_unroll):
    width=min(rung+1,max_unroll)
    return [suffix_window(chain,rung,max_unroll,k) for k in range(rung+2-width)]


def _mean(values):
    vals=[float(x) for x in values if x is not None and math.isfinite(float(x))]
    return sum(vals)/len(vals) if vals else None


def _selected_stage_chains(chains,rung,n):
    eligible=[ch for ch in chains if len(ch)-1>=rung+1]
    if not eligible:raise RuntimeError('REPAIR_NO_DEV_CHAINS_FOR_RUNG')
    # Preserve range of root lengths while scoring a *known starting suffix*.
    chosen=loop._select_dev_chains(eligible,min(n,len(eligible)))
    return [ch[-(rung+2):] for ch in chosen]


def t00_eval(bb,tok,link,det,numeric,chains,reference,args,scale):
    """Paired generated/gold/zero evaluations. Gold is posthoc scoring only."""
    import torch
    from torch.nn import functional as F
    cases=_selected_stage_chains(chains,0,args.eval_cases)
    rows=[]
    link.eval();det.eval();numeric.eval()
    for ch in cases:
        source,target=ch[-2],ch[-1]
        intact=diag.generate_one(bb,tok,link,det,numeric,source,args.device,args.max_tokens,scale)
        zero=diag.generate_one(bb,tok,link,det,numeric,source,args.device,args.max_tokens,scale,control='zero_latent')
        gold=reference[target].to(args.device).reshape(1,-1)
        with torch.no_grad():
            prob_gold=float(torch.sigmoid(det(gold.float()).reshape(-1))[0])
            gold_cos=float(F.cosine_similarity(intact['generated'],gold,dim=-1)[0])
            initial_cos=float(F.cosine_similarity(intact['before'],gold,dim=-1)[0])
            zero_cos=float(F.cosine_similarity(zero['generated'],gold,dim=-1)[0])
        expected=int(target[len('ANSWER: '):].strip())
        numeric_diagnostic=diag._readouts(numeric,intact['generated'],scale,expected)
        rows.append({'source':source,'target':target,'p_generated':intact['probability'],
            'p_gold':prob_gold,'p_zero':zero['probability'],
            'generated_exit':intact['probability']>=args.answer_threshold,
            'gold_exit':prob_gold>=args.answer_threshold,
            'generated_to_gold_cosine':gold_cos,'initial_to_gold_cosine':initial_cos,
            'zero_to_gold_cosine':zero_cos,'gain_over_source':gold_cos-initial_cos,
            'intact_minus_zero':gold_cos-zero_cos,
            'direct_number':intact['readout'].get('direct_number'),
            'complex_number':intact['readout'].get('complex_number'),
            'numeric':numeric_diagnostic})
    n=len(rows)
    return {'cases':n,'answer_exits':sum(x['generated_exit'] for x in rows),
        'gold_answer_exits':sum(x['gold_exit'] for x in rows),
        'zero_answer_exits':sum(x['p_zero']>=args.answer_threshold for x in rows),
        'correct_stop_depth':sum(x['generated_exit'] for x in rows),
        'early_false_stops':0,'generated_exit_rate':sum(x['generated_exit'] for x in rows)/n,
        'gold_exit_rate':sum(x['gold_exit'] for x in rows)/n,
        'mean_generated_answer_probability':_mean(x['p_generated'] for x in rows),
        'mean_generated_to_gold_cosine':_mean(x['generated_to_gold_cosine'] for x in rows),
        'mean_gain_over_source_cosine':_mean(x['gain_over_source'] for x in rows),
        'mean_intact_minus_zero_cosine':_mean(x['intact_minus_zero'] for x in rows),
        'mean_direct_code_abs_error':_mean(x['numeric'].get('direct_code_abs_error') for x in rows),
        'mean_complex_code_abs_error':_mean(x['numeric'].get('complex_code_abs_error') for x in rows),
        'unity_cases':sum(x['numeric'].get('target_value')==1 for x in rows),
        'mean_unity_radius_error':_mean(x['numeric'].get('unit_circle_error') for x in rows),
        'geometry_feedback_causal':False,'generated_feedback':True,
        'reference_used_only_after_generation':True},rows


def stage_eval(bb,tok,link,det,numeric,chains,reference,args,scale,rung):
    if rung==0:return t00_eval(bb,tok,link,det,numeric,chains,reference,args,scale)
    chosen=_selected_stage_chains(chains,rung,args.eval_cases)
    records=[]
    for ch in chosen:
        out=loop.rollout(bb,tok,link,det,numeric,ch[0],args.device,args.max_tokens,scale,
            args.answer_threshold,args.drift_threshold,args.stall_patience,
            rung+1,control='intact',return_vector=True)
        vec=out.pop('_final_vector')
        import torch.nn.functional as F
        target=reference[ch[-1]].reshape(1,-1)
        score=float(F.cosine_similarity(vec.float(),target.float(),dim=-1)[0])
        records.append({'source':ch[0], 'status':out['status'],'steps':out['steps'],
            'expected_steps':rung+1,'final_to_gold_cosine':score,
            'correct_stop':out['status']=='ANSWER' and out['steps']==rung+1,
            'false_stop':out['status']=='ANSWER' and out['steps']<rung+1})
    n=len(records);good=sum(r['correct_stop'] for r in records)
    return {'cases':n,'answer_exits':sum(x['status']=='ANSWER' for x in records),
        'correct_stop_depth':good,'early_false_stops':sum(r['false_stop'] for r in records),
        'generated_exit_rate':good/n,
        'mean_generated_to_gold_cosine':_mean(r['final_to_gold_cosine'] for r in records),
        'mean_gain_over_source_cosine':None,'mean_intact_minus_zero_cosine':None,
        'generated_feedback':True,'reference_used_only_after_generation':True,
        'geometry_feedback_causal':False},records


def gate(metrics,t00,args,rung,steps_at_rung):
    reasons=[]
    if steps_at_rung<args.min_steps_per_rung:reasons.append('MIN_STAGE_STEPS_NOT_MET')
    if metrics['cases']<args.min_gate_cases:reasons.append('INSUFFICIENT_GATE_CASES')
    if metrics['correct_stop_depth']<math.ceil(metrics['cases']*args.min_exit_rate-1e-12):
        reasons.append('INSUFFICIENT_CORRECT_EXITS')
    if metrics['early_false_stops']>args.max_early_stops:reasons.append('EARLY_FALSE_STOPS')
    if t00['generated_exit_rate']<args.min_exit_rate:reasons.append('T00_RETENTION_FAILED')
    if t00['gold_exit_rate']<args.min_gold_exit_rate:reasons.append('GOLD_DETECTOR_BASELINE_FAILED')
    if metrics.get('mean_generated_to_gold_cosine') is None or metrics['mean_generated_to_gold_cosine']<args.min_gold_cosine:
        reasons.append('GENERATED_STATE_TOO_FAR_FROM_GOLD')
    if t00['mean_gain_over_source_cosine'] is None or t00['mean_gain_over_source_cosine']<args.min_source_gain:
        reasons.append('NO_GOLD_DIRECTIONAL_GAIN')
    if t00['mean_intact_minus_zero_cosine'] is None or t00['mean_intact_minus_zero_cosine']<args.min_zero_gain:
        reasons.append('NO_GAIN_OVER_ZERO_CONTROL')
    return len(reasons)==0,reasons


def _store(path,cfg,link,det,step,dev,rung,accepted,gate_reasons):
    frozen._atomic_tensor(path,{'schema':loop.SCHEMA,'config_hash':base.digest(cfg),
        'step':step,'link':{k:v.detach().cpu().clone() for k,v in link.state_dict().items()},
        'detector':{k:v.detach().cpu().clone() for k,v in det.state_dict().items()},
        'dev':dev,'training_schema':SCHEMA,'trained_generator_only':True,
        'accepted_rung':rung if accepted else None,'gate_reasons':gate_reasons,
        'status':'GATE_ACCEPTED_NOT_LATENT_CERTIFIED' if accepted else 'PROVISIONAL_NOT_PROMOTED'})


def _parents(args):
    return [Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent,
            Path(args.detector_checkpoint).parent]+([Path(args.cache_dir)] if args.cache_dir else [])


def _prepare(args):
    import torch
    if not args.detector_checkpoint:raise ValueError('REPAIR_DETECTOR_REQUIRED')
    args.checkpoint=args.detector_checkpoint
    args._det_sha=parent.file_hash(args.detector_checkpoint)
    prepared=frozen._prepare(args,need_teacher=False)
    numeric,det,dcfg,_=frozen._load_sidecars(args,prepared)
    bb=prepared['bb'];bb.eval();bb.requires_grad_(False)
    numeric.eval();numeric.requires_grad_(False)
    det.eval();det.requires_grad_(False)
    return prepared,numeric,det,dcfg


def _reference(prepared,args,dev_chains):
    """Exact DEV target vectors, cached with frozen-Qwen provenance."""
    states=sorted({c[-1] for c in dev_chains})
    emb,hits=frozen._load_or_encode(prepared['cache_dir'],'t00-repair-dev-answers',states,
        prepared['bb'],prepared['tok'],prepared['s'],args,prepared['identity'])
    return dict(zip(states,emb)),hits


def _verified_link_checkpoint(path,args,prepared):
    import torch
    path=Path(path)
    cfg_file=path.parent/'run_config.json'
    if not cfg_file.exists():raise RuntimeError('REPAIR_WARM_START_MISSING_CONFIG')
    cfg=json.loads(cfg_file.read_text(encoding='utf8'))
    item=torch.load(path,map_location='cpu',weights_only=True)
    if cfg.get('schema')!=loop.SCHEMA or item.get('schema')!=loop.SCHEMA or item.get('config_hash')!=base.digest(cfg):
        raise RuntimeError('REPAIR_WARM_START_CONFIG_DRIFT')
    if (cfg.get('stage1_sha256')!=prepared['parent_hash'] or
        cfg.get('detector_sha256')!=args._det_sha or
        cfg.get('text_sha256')!=parent.file_hash(args.text_checkpoint) or
        cfg.get('frozen_backbone_sha256')!=prepared['frozen_hash']):
        raise RuntimeError('REPAIR_WARM_START_PARENT_DRIFT')
    if 'link' not in item or 'detector' not in item:
        raise RuntimeError('REPAIR_WARM_START_MISSING_WEIGHTS')
    return item


def train(args):
    import torch
    if args.steps<1 or args.eval_every<1 or args.max_unroll<1 or args.max_rung<0 or args.max_rung>8:
        raise ValueError('REPAIR_BAD_BUDGET')
    if args.min_steps_per_rung<1 or args.eval_cases<1 or args.min_gate_cases<1:
        raise ValueError('REPAIR_INVALID_GATE_SIZE')
    if not 0<args.min_exit_rate<=1 or not 0<args.min_gold_exit_rate<=1:
        raise ValueError('REPAIR_INVALID_GATE_RATE')
    parent._output_safe(args.output,_parents(args))
    prepared,numeric,det,dcfg=_prepare(args)
    chains={k:loop.chains_from_rows(prepared['deck'][k],allow_partial=(k=='dev')) for k in ('train','dev')}
    bb=prepared['bb'];tok=prepared['tok'];emb=bb.get_input_embeddings()
    torch.manual_seed(args.seed)
    link=loop.make_link(int(bb.config.hidden_size),int(emb.embedding_dim)).to(args.device)
    if args.warm_start_loop:
        # Reuse the generator only after verifying the exact immutable lineage.
        old=_verified_link_checkpoint(args.warm_start_loop,args,prepared)
        link.load_state_dict(old['link'],strict=True)
    optimizer=torch.optim.AdamW(link.parameters(),lr=args.lr,weight_decay=0)
    train_vec={x['text']:v for x,v in zip(prepared['states']['train'],prepared['embeddings']['train'])}
    devref,dev_hit=_reference(prepared,args,chains['dev'])
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    cfg={'schema':loop.SCHEMA,'repair_schema':SCHEMA,'stage1_sha256':prepared['parent_hash'],
        'detector_sha256':args._det_sha,'text_sha256':parent.file_hash(args.text_checkpoint),
        'source_sha256':prepared['source_hash'],'frozen_backbone_sha256':prepared['frozen_hash'],
        'deck_sha256':prepared['parent_cfg']['deck_sha256'], 'seed':args.seed,
        'max_tokens':args.max_tokens,'max_depth':args.max_depth,'max_unroll':args.max_unroll,
        'max_rung':args.max_rung,'steps':args.steps,'lr':args.lr,'eval_every':args.eval_every,
        'answer_threshold':args.answer_threshold,'drift_threshold':args.drift_threshold,
        'stall_patience':args.stall_patience,'min_exit_rate':args.min_exit_rate,
        'min_gold_exit_rate':args.min_gold_exit_rate,'min_gold_cosine':args.min_gold_cosine,
        'min_source_gain':args.min_source_gain,
        'min_zero_gain':args.min_zero_gain,'min_steps_per_rung':args.min_steps_per_rung,
        'eval_cases':args.eval_cases,'min_gate_cases':args.min_gate_cases,
        'max_early_stops':args.max_early_stops,'completion_weight':args.completion_weight,
        'warm_start_loop':str(args.warm_start_loop) if args.warm_start_loop else None,
        'frozen_qwen':True,'frozen_detector':True,'frozen_numeric':True,
        'teacher_targets_at_inference':False,'lm_head_used':False}
    base._atomic_json(output/'run_config.json',cfg)
    rng=random.Random(args.seed)
    rung=0;in_rung=0;accepted=-1;best_candidate=-math.inf;best_step=None
    history=[];t00_train=t00_pairs(chains['train'])
    if not t00_train:raise RuntimeError('REPAIR_NO_T00_TRAINING_PAIRS')
    for step in range(1,args.steps+1):
        eligible=[ch for ch in chains['train'] if len(ch)-1>=rung+1]
        ch=rng.choice(eligible)
        windows=windows_for_rung(ch,rung,args.max_unroll)
        # Rotate through all local windows at later rungs, but ensure at least
        # half the updates reach the answer (unlike previous truncated prefix).
        offset=0 if rung==0 or in_rung%2==0 else 1+((in_rung//2)%(len(windows)-1)) if len(windows)>1 else 0
        suffix=windows[offset]
        if rung==0 and (len(suffix)!=2 or not suffix[-1].startswith('ANSWER: ')):
            raise RuntimeError('REPAIR_T00_WINDOW_MISSING_ANSWER')
        h=train_vec[suffix[0]].to(args.device).reshape(1,-1)
        loss,parts=loop.train_suffix(bb,tok,link,det,suffix,train_vec,h,args.device,
                                     args.max_tokens,args.completion_weight)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(link.parameters(),1.)
        if any(p.grad is not None for p in det.parameters()):
            raise RuntimeError('REPAIR_FROZEN_DETECTOR_RECEIVED_GRADIENT')
        optimizer.step()
        in_rung+=1
        if step%args.eval_every and step!=args.steps:continue
        t00,t00_rows=t00_eval(bb,tok,link,det,numeric,chains['dev'],devref,args,dcfg['scale'])
        if rung==0:metrics=t00;rows=t00_rows
        else:metrics,rows=stage_eval(bb,tok,link,det,numeric,chains['dev'],devref,args,dcfg['scale'],rung)
        passed,reasons=gate(metrics,t00,args,rung,in_rung)
        candidate_score=(metrics['correct_stop_depth']/metrics['cases']
                         +.1*(t00['mean_intact_minus_zero_cosine'] or 0)
                         +.1*(t00['mean_gain_over_source_cosine'] or 0)
                         -metrics['early_false_stops']/metrics['cases'])
        if candidate_score>best_candidate:
            best_candidate=candidate_score;best_step=step
            _store(output/'candidate.pt',cfg,link,det,step,metrics,rung,False,reasons)
        record={'step':step,'rung':f'T{rung:02d}','steps_in_rung':in_rung,
            'training_window':suffix,'window_contains_answer':suffix[-1].startswith('ANSWER: '),
            'loss':float(loss.detach()),'train':parts,'dev':metrics,'t00_retention':t00,
            'gate_pass':passed,'gate_reasons':reasons,'candidate_score':candidate_score,
            'provisional_best_step':best_step,'detector_updated':False,'qwen_updated':False,
            'geometry_feedback_causal':False}
        history.append(record)
        base._atomic_json(output/'progress.json',record)
        print('T00_REPAIR_PROGRESS='+json.dumps({k:record[k] for k in
            ('step','rung','steps_in_rung','window_contains_answer','dev','t00_retention','gate_pass','gate_reasons','detector_updated','qwen_updated','geometry_feedback_causal')},sort_keys=True),flush=True)
        if passed:
            _store(output/(f'accepted_T{rung:02d}.pt'),cfg,link,det,step,metrics,rung,True,[])
            _store(output/'best.pt',cfg,link,det,step,metrics,rung,True,[])
            accepted=rung
            print('T00_REPAIR_GATE='+json.dumps({'rung':f'T{rung:02d}','step':step,
                'accepted_checkpoint':str(output/'best.pt'),'t00_correct_exits':t00['correct_stop_depth']},sort_keys=True),flush=True)
            if rung<args.max_rung:
                rung+=1;in_rung=0
                best_candidate=-math.inf;best_step=None
        # Gate stops re-training an accepted stage unless outward requested.
        if accepted==args.max_rung:break
    if any(p.grad is not None for p in bb.parameters()):raise RuntimeError('REPAIR_QWEN_UPDATED')
    if parent.file_hash(args.stage1_checkpoint)!=prepared['parent_hash'] or parent.file_hash(args.detector_checkpoint)!=args._det_sha:
        raise RuntimeError('REPAIR_PARENT_MUTATED')
    base._atomic_json(output/'history.json',history)
    result={'schema':SCHEMA,'status':('T00_GATE_PASSED_NOT_FULL_LATENT_CERTIFIED' if accepted>=0 else 'T00_NOT_PASSED'),
        'accepted_rung':f'T{accepted:02d}' if accepted>=0 else None,
        'latest_stage':f'T{rung:02d}', 'candidate':str(output/'candidate.pt') if best_step else None,
        'checkpoint':str(output/'best.pt') if accepted>=0 else None,
        'trained_steps':step,'qwen_unchanged':True,'detector_unchanged':True,
        'numeric_unchanged':True,'uses_generated_feedback':True,'geometry_observer_only':True,
        'cache_hits':prepared['cache_hits'],'dev_reference_cache_hit':dev_hit,
        'last_gate_reasons':history[-1]['gate_reasons'] if history else [],
        'last_dev':history[-1]['dev'] if history else None}
    base._atomic_json(output/'result.json',result)
    print('T00_REPAIR_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def reachability(args):
    """Per-example free embedding optimization: can a ONE-token Qwen input reach gold?"""
    import torch
    import torch.nn.functional as F
    if args.reachability_steps<1 or args.reachability_cases<1:raise ValueError('REPAIR_INVALID_REACHABILITY_BUDGET')
    parent._output_safe(args.output,_parents(args)+([Path(args.warm_start_loop).parent] if args.warm_start_loop else []))
    prepared,num,det,dcfg=_prepare(args)
    bb=prepared['bb'];tok=prepared['tok']
    chains=loop.chains_from_rows(prepared['deck']['dev'],allow_partial=True)
    ref,_=_reference(prepared,args,chains)
    if not args.warm_start_loop:raise ValueError('REPAIR_REACHABILITY_REQUIRES_WARM_START')
    ck=_verified_link_checkpoint(args.warm_start_loop,args,prepared)
    link=loop.make_link(bb.config.hidden_size,bb.get_input_embeddings().embedding_dim).to(args.device)
    link.load_state_dict(ck['link']);link.eval();link.requires_grad_(False)
    rows=[]
    for ch in loop._select_dev_chains(chains,args.reachability_cases):
        source,target=ch[-2],ch[-1]
        prefix=loop.token_embeddings(bb,tok,source,args.device,args.max_tokens)
        with torch.no_grad():
            base_h=bb.get_decoder()(inputs_embeds=prefix,attention_mask=torch.ones(prefix.shape[:2],device=args.device,dtype=torch.long),return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
            starting_latent=link(base_h.float()).detach().float().reshape(1,-1)
            _,starting_h=loop.feedback_step(bb,link,prefix,base_h,args.device)
            gold=ref[target].to(args.device).reshape(1,-1)
            start_cos=float(F.cosine_similarity(starting_h.float(),gold.float(),dim=-1)[0])
            start_p=float(torch.sigmoid(det(starting_h.float()).reshape(-1))[0])
        latent=torch.nn.Parameter(starting_latent.clone())
        optim=torch.optim.Adam([latent],lr=args.reachability_lr)
        best=(start_cos,start_p)
        for _ in range(args.reachability_steps):
            seq=torch.cat((prefix,latent.to(prefix.dtype).unsqueeze(1)),dim=1)
            h=bb.get_decoder()(inputs_embeds=seq,attention_mask=torch.ones(seq.shape[:2],device=args.device,dtype=torch.long),return_dict=True,use_cache=False).last_hidden_state[:,-1,:].float()
            cosine=F.cosine_similarity(h,gold.float(),dim=-1).mean()
            loss=1-cosine+.25*F.smooth_l1_loss(h,gold.float())
            optim.zero_grad(set_to_none=True);loss.backward();optim.step()
            with torch.no_grad():
                prob=float(torch.sigmoid(det(h).reshape(-1))[0]);cs=float(cosine)
                if cs>best[0]:best=(cs,prob)
        rows.append({'source':source,'target':target,'starting_cosine':start_cos,
            'optimized_cosine':best[0],'starting_detector_probability':start_p,
            'optimized_detector_probability':best[1],
            'optimized_reaches_threshold':best[1]>=args.answer_threshold})
        print('T00_REPAIR_REACHABILITY_PROGRESS='+json.dumps({'done':len(rows),'total':min(args.reachability_cases,len(chains)),
            'starting_cosine':start_cos,'optimized_cosine':best[0]},sort_keys=True),flush=True)
    report={'schema':SCHEMA,'stage':'reachability','cases':len(rows),
        'mean_start_cosine':_mean(x['starting_cosine'] for x in rows),
        'mean_optimized_cosine':_mean(x['optimized_cosine'] for x in rows),
        'optimized_detector_exits':sum(x['optimized_reaches_threshold'] for x in rows),
        'only_latent_embedding_optimized':True,'all_model_weights_unchanged':True,
        'detector_training':False,'geometry_feedback_causal':False,'rows':rows}
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    base._atomic_json(out/'reachability.json',report)
    print('T00_REPAIR_REACHABILITY='+json.dumps({k:v for k,v in report.items() if k!='rows'},sort_keys=True),flush=True)
    return report


def preview(args):
    cfg,_=parent.load_parent(args)
    s=parent.stage1()
    decks={key:loop.chains_from_rows(parent._rows(s,cfg,key),allow_partial=(key=='dev')) for key in ('train','dev')}
    report={'schema':SCHEMA,'stage':'preview','train_t00_pairs':len(t00_pairs(decks['train'])),
        'dev_t00_pairs':len(t00_pairs(decks['dev'])),'max_rung':args.max_rung,
        'curriculum':'T00_FIRST_GATED','qwen_loaded':False,'detector_trainable':False}
    print('T00_REPAIR_PREVIEW='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','reachability','probe','inspect'),default='preview')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint',required=True);p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--detector-checkpoint');p.add_argument('--warm-start-loop');p.add_argument('--checkpoint')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_t00_repair_v1'))
    p.add_argument('--cache-dir');p.add_argument('--steps',type=int,default=800)
    p.add_argument('--eval-every',type=int,default=50);p.add_argument('--eval-cases',type=int,default=12)
    p.add_argument('--encoder-batch',type=int,default=12);p.add_argument('--max-tokens',type=int,default=192)
    p.add_argument('--max-unroll',type=int,default=2);p.add_argument('--max-depth',type=int,default=14)
    p.add_argument('--max-rung',type=int,default=0);p.add_argument('--min-steps-per-rung',type=int,default=100)
    p.add_argument('--min-gate-cases',type=int,default=6);p.add_argument('--min-exit-rate',type=float,default=.5)
    p.add_argument('--min-gold-exit-rate',type=float,default=.8)
    p.add_argument('--min-gold-cosine',type=float,default=.7)
    p.add_argument('--min-source-gain',type=float,default=.02);p.add_argument('--min-zero-gain',type=float,default=.01)
    p.add_argument('--max-early-stops',type=int,default=0)
    p.add_argument('--lr',type=float,default=.0001);p.add_argument('--completion-weight',type=float,default=.5)
    p.add_argument('--answer-threshold',type=float,default=.9)
    p.add_argument('--drift-threshold',type=float,default=1e-5);p.add_argument('--stall-patience',type=int,default=2)
    p.add_argument('--reachability-cases',type=int,default=4);p.add_argument('--reachability-steps',type=int,default=60)
    p.add_argument('--reachability-lr',type=float,default=.05)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--state');p.add_argument('--control',choices=('intact','zero_latent'),default='intact')
    p.add_argument('--split',choices=('dev','ood','test','train'),default='dev')
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.max_rung>8 or args.max_rung<0 or not 0<args.answer_threshold<=1:
        p.error('REPAIR_BAD_RUNG_OR_THRESHOLD')
    if args.stage in ('probe','inspect'):
        if not args.checkpoint:raise ValueError('REPAIR_CHECKPOINT_REQUIRED')
        return loop.evaluate(args)
    return {'preview':preview,'train':train,'reachability':reachability}[args.stage](args)

if __name__=='__main__':main()
