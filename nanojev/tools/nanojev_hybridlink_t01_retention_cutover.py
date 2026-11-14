#!/usr/bin/env python3
"""T01 two-hop, generated-feedback training, gated by immutable T00 retention.

Warm-starts only from an accepted T00 repair checkpoint. The frozen Stage 1
Qwen, Answer Detector and numerical heads are never optimized. Inference sees
only the starting PROBLEM; gold vectors are used strictly after generation.
No LM head, text intermediate generation, or geometry-to-feedback shortcut.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from types import SimpleNamespace

from tools import nanojev_hybridlink_t00_curriculum_repair as t00
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_latent_diagnostics as diag
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_qwen_numeric_dual_cutover as parent
from tools import nanojev_hybridlink_rung_reduction as base

SCHEMA='nanojev.hybridlink.t01-gated-t00-retention.v1'


def t01_windows(chains):
    """Use two ACTUAL successive hops, including intermediate and final answer."""
    return [ch[-3:] for ch in chains if len(ch)>=3 and ch[-1].startswith('ANSWER: ') and ch[-2].startswith('PROBLEM: ')]


def _mean(values):
    return t00._mean(values)


def _selected(chains,n):
    eligible=[ch for ch in chains if len(ch)>=3]
    if not eligible:raise RuntimeError('T01_NO_TWO_HOP_DEV_CHAINS')
    return t00._selected_stage_chains(eligible,1,n)


def _references(prepared,args,dev_chains):
    """Frozen reference states cached with exact Qwen lineage and ordered texts."""
    selected=set()
    for ch in dev_chains:
        if len(ch)>=3:selected.update(ch[-2:])
        selected.add(ch[-1])
    strings=sorted(selected)
    vectors,hit=frozen._load_or_encode(prepared['cache_dir'],'t01-dev-gold-states',strings,
                                      prepared['bb'],prepared['tok'],prepared['s'],args,prepared['identity'])
    return dict(zip(strings,vectors)),hit


def _run_one(bb,tok,link,det,numeric,source,device,max_tokens,scale,answer_threshold,drift,patience):
    """Does NOT receive the target chain or any oracle intermediate state."""
    return diag.build_trace(bb,tok,link,det,numeric,[source],device,max_tokens,
                            scale,answer_threshold,drift,patience,2)


def evaluate_t01(bb,tok,link,det,numeric,chains,references,args,scale):
    import torch
    selected=_selected(chains,args.eval_cases)
    results=[]
    link.eval();det.eval();numeric.eval();bb.eval()
    for chain in selected:
        start,intermediate,answer=chain
        # The entire recurrent computation receives only start. References enter post-hoc.
        raw=_run_one(bb,tok,link,det,numeric,start,args.device,args.max_tokens,scale,
                     args.answer_threshold,args.drift_threshold,args.stall_patience)
        rows=[]
        for i,rec in enumerate(raw['generated']):
            gold=chain[i+1] if i+1<len(chain) else None
            reference_cos=diag._cos(rec['_vector'],references[gold]) if gold in references else None
            numeric_target=parent.numeric_label(chain[i]) if gold is not None else None
            numbers=diag._readouts(numeric,rec['_vector'].to(args.device),scale,numeric_target)
            rows.append({'step':i+1,'action':rec['action'],'answer_probability':rec['p_answer'],
                         'relative_drift':rec['relative_drift'],'reference_cosine':reference_cos,
                         'reference_type':'ANSWER' if gold and gold.startswith('ANSWER: ') else 'PROBLEM',
                         'direct_code_error':numbers.get('direct_code_abs_error'),
                         'complex_code_error':numbers.get('complex_code_abs_error'),
                         'delta_rho':numbers.get('delta_rho')})
        zero=loop.rollout(bb,tok,link,det,numeric,start,args.device,args.max_tokens,scale,
                          args.answer_threshold,args.drift_threshold,args.stall_patience,2,
                          control='zero_latent',return_vector=True)
        zero_final=zero.pop('_final_vector')
        zero_cos=diag._cos(zero_final,references[answer]) if zero['steps']==2 else None
        final_cos=rows[-1]['reference_cosine'] if raw['steps']==2 else None
        correct=raw['status']=='ANSWER' and raw['steps']==2 and len(rows)==2 and rows[0]['action']=='CONTINUE'
        false_stop=raw['status']=='ANSWER' and raw['steps']==1
        results.append({'source':start,'intermediate':intermediate,'answer':answer,
                        'status':raw['status'],'steps':raw['steps'],'correct_stop':correct,
                        'early_false_stop':false_stop,
                        'first_step_continued':bool(rows) and rows[0]['action']=='CONTINUE',
                        'second_step_answer':len(rows)==2 and rows[1]['action']=='ANSWER',
                        'first_step_gold_cosine':rows[0]['reference_cosine'] if rows else None,
                        'final_gold_cosine':final_cos,'zero_final_gold_cosine':zero_cos,
                        'gain_over_zero':final_cos-zero_cos if final_cos is not None and zero_cos is not None else None,
                        'zero_status':zero['status'],'history':rows})
    n=len(results)
    summary={'cases':n,'correct_stop_depth':sum(x['correct_stop'] for x in results),
             'answer_exits':sum(x['status']=='ANSWER' for x in results),
             'early_false_stops':sum(x['early_false_stop'] for x in results),
             'first_step_continued':sum(x['first_step_continued'] for x in results),
             'second_step_answer':sum(x['second_step_answer'] for x in results),
             'stalled':sum(x['status']=='STALLED' for x in results),
             'budget_exhausted':sum(x['status']=='BUDGET_EXHAUSTED' for x in results),
             'correct_exit_rate':sum(x['correct_stop'] for x in results)/n,
             'mean_first_step_gold_cosine':_mean(x['first_step_gold_cosine'] for x in results),
             'mean_final_gold_cosine':_mean(x['final_gold_cosine'] for x in results),
             'mean_intact_minus_zero_cosine':_mean(x['gain_over_zero'] for x in results),
             'mean_first_step_p_answer':_mean(x['history'][0]['answer_probability'] for x in results if x['history']),
             'mean_second_step_p_answer':_mean(x['history'][1]['answer_probability'] for x in results if len(x['history'])==2),
             'mean_first_step_direct_code_error':_mean(x['history'][0]['direct_code_error'] for x in results if x['history']),
             'mean_first_step_complex_code_error':_mean(x['history'][0]['complex_code_error'] for x in results if x['history']),
             'mean_second_step_direct_code_error':_mean(x['history'][1]['direct_code_error'] for x in results if len(x['history'])==2),
             'mean_second_step_complex_code_error':_mean(x['history'][1]['complex_code_error'] for x in results if len(x['history'])==2),
             'reference_used_only_after_generation':True,'geometry_feedback_causal':False,
             'qwen_frozen':True,'detector_frozen':True}
    return summary,results


def t00_regressions(baseline_rows,current_rows,baseline_summary,current_summary,args):
    """Reject a candidate that loses ANY previously accepted baseline T00 case."""
    prior={r['source']:r for r in baseline_rows}
    now={r['source']:r for r in current_rows}
    if prior.keys()!=now.keys():raise RuntimeError('T01_T00_RETENTION_CASE_DRIFT')
    lost=sorted(s for s in prior if prior[s]['generated_exit'] and not now[s]['generated_exit'])
    new_false_reference=sorted(s for s in prior if prior[s]['gold_exit'] and not now[s]['gold_exit'])
    cos_drop=(baseline_summary['mean_generated_to_gold_cosine'] or 0)-(current_summary['mean_generated_to_gold_cosine'] or 0)
    report={'baseline_cases':len(prior),'baseline_correct':sum(r['generated_exit'] for r in prior.values()),
            'current_correct':sum(r['generated_exit'] for r in now.values()),
            'lost_sources':lost,'gold_baseline_lost_sources':new_false_reference,
            'mean_gold_cosine_drop':cos_drop,'max_allowed_cosine_drop':args.max_t00_cosine_drop,
            'retention_pass':not lost and not new_false_reference and cos_drop<=args.max_t00_cosine_drop
                              and current_summary['generated_exit_rate']>=args.min_t00_retention_rate}
    return report


def gate_t01(t01,t00_retention,steps,args):
    reasons=[]
    if steps<args.min_t01_steps:reasons.append('MIN_T01_STEPS_NOT_MET')
    if t01['cases']<args.min_gate_cases:reasons.append('INSUFFICIENT_T01_DEV_CASES')
    if not t00_retention['retention_pass']:reasons.append('T00_RETENTION_REGRESSED')
    if t01['early_false_stops']>0:reasons.append('T01_EARLY_FALSE_STOPS')
    if t01['correct_stop_depth']<math.ceil(t01['cases']*args.min_t01_exit_rate-1e-12):
        reasons.append('INSUFFICIENT_TWO_HOP_CORRECT_EXITS')
    if t01['first_step_continued']<math.ceil(t01['cases']*args.min_t01_exit_rate-1e-12):
        reasons.append('INTERMEDIATE_FALSE_COMPLETION')
    if t01['mean_first_step_gold_cosine'] is None or t01['mean_first_step_gold_cosine']<args.min_intermediate_cosine:
        reasons.append('INTERMEDIATE_REPRESENTATION_MISMATCH')
    if t01['mean_final_gold_cosine'] is None or t01['mean_final_gold_cosine']<args.min_final_cosine:
        reasons.append('FINAL_REPRESENTATION_MISMATCH')
    if t01['mean_intact_minus_zero_cosine'] is None or t01['mean_intact_minus_zero_cosine']<args.min_zero_gain:
        reasons.append('NO_TWO_HOP_ADVANTAGE_OVER_ZERO')
    return not reasons,reasons


def _save(path,cfg,link,det,step,t01,t00_retention,accepted,reasons):
    frozen._atomic_tensor(path,{'schema':loop.SCHEMA,'config_hash':base.digest(cfg),'step':step,
        'link':{k:v.detach().cpu().clone() for k,v in link.state_dict().items()},
        'detector':{k:v.detach().cpu().clone() for k,v in det.state_dict().items()},
        'dev':t01,'training_schema':SCHEMA,'trained_generator_only':True,
        't00_retention':t00_retention,'accepted_rung':1 if accepted else None,
        'gate_reasons':reasons,'status':'T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED' if accepted else 'PROVISIONAL_T01'})


def _prepare(args):
    import torch
    if not args.warm_start_t00:raise ValueError('T01_ACCEPTED_T00_CHECKPOINT_REQUIRED')
    if args.steps<1 or args.eval_every<1 or args.eval_cases<1 or args.t00_cases<1 or args.max_unroll<2:
        raise ValueError('T01_INVALID_BUDGET')
    if args.min_t01_steps<1 or args.min_gate_cases<1 or not 0<args.min_t01_exit_rate<=1:
        raise ValueError('T01_INVALID_GATE')
    if not 0<args.min_t00_retention_rate<=1 or args.max_t00_cosine_drop<0:
        raise ValueError('T01_INVALID_RETENTION_POLICY')
    protected=[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent,
               Path(args.detector_checkpoint).parent,Path(args.warm_start_t00).parent]
    if getattr(args,'stage','train')=='probe' and args.checkpoint:protected.append(Path(args.checkpoint).parent)
    if args.cache_dir:protected.append(Path(args.cache_dir))
    parent._output_safe(args.output,protected)
    if Path(args.output,'best.pt').exists() or Path(args.output,'accepted_T01.pt').exists():
        raise RuntimeError('T01_REFUSES_OVERWRITE_ACCEPTED_CHECKPOINT')
    prepared,numeric,det,dcfg=t00._prepare(args)
    ck=t00._verified_link_checkpoint(args.warm_start_t00,args,prepared)
    t00_cfg=json.loads((Path(args.warm_start_t00).parent/'run_config.json').read_text(encoding='utf8'))
    if (args.max_tokens!=t00_cfg['max_tokens'] or args.answer_threshold!=t00_cfg['answer_threshold'] or
        args.drift_threshold!=t00_cfg['drift_threshold'] or args.stall_patience!=t00_cfg['stall_patience']):
        raise RuntimeError('T01_RUNTIME_CONTROLS_DIFFER_FROM_ACCEPTED_T00')
    if ck.get('accepted_rung')!=0 or ck.get('status')!='GATE_ACCEPTED_NOT_LATENT_CERTIFIED' or ck.get('training_schema')!=t00.SCHEMA:
        raise RuntimeError('T01_WARM_START_MUST_BE_ACCEPTED_T00')
    bb=prepared['bb'];tok=prepared['tok']
    link=loop.make_link(int(bb.config.hidden_size),int(bb.get_input_embeddings().embedding_dim)).to(args.device)
    link.load_state_dict(ck['link'],strict=True)
    if any(not torch.equal(p.detach().cpu(),ck['detector'][k]) for k,p in det.state_dict().items()):
        raise RuntimeError('T01_T00_CHECKPOINT_DETECTOR_DRIFT')
    det.requires_grad_(False);numeric.requires_grad_(False);bb.requires_grad_(False)
    return prepared,numeric,det,dcfg,link


def train(args):
    import torch
    prepared,numeric,det,dcfg,link=_prepare(args)
    train_chains=loop.chains_from_rows(prepared['deck']['train'])
    dev_chains=loop.chains_from_rows(prepared['deck']['dev'],allow_partial=True)
    eligible=t01_windows(train_chains)
    if not eligible:raise RuntimeError('T01_NO_TRAIN_TWO_HOP_EXAMPLES')
    reference,ref_hit=_references(prepared,args,dev_chains)
    bb=prepared['bb'];tok=prepared['tok']
    t00_args=SimpleNamespace(**vars(args));t00_args.eval_cases=args.t00_cases
    with torch.no_grad():
        baseline,baseline_rows=t00.t00_eval(bb,tok,link,det,numeric,dev_chains,reference,t00_args,dcfg['scale'])
    if baseline['generated_exit_rate']<args.min_t00_retention_rate:
        raise RuntimeError('T01_T00_BASELINE_NOT_ACCEPTABLE')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    cfg={'schema':loop.SCHEMA,'t01_schema':SCHEMA,'stage1_sha256':prepared['parent_hash'],
         'detector_sha256':args._det_sha,'text_sha256':parent.file_hash(args.text_checkpoint),
         'source_sha256':prepared['source_hash'],'frozen_backbone_sha256':prepared['frozen_hash'],
         'deck_sha256':prepared['parent_cfg']['deck_sha256'],'t00_sha256':parent.file_hash(args.warm_start_t00),
         'scale':dcfg['scale'],'seed':args.seed,'steps':args.steps,'eval_every':args.eval_every,
         'eval_cases':args.eval_cases,'t00_cases':args.t00_cases,'lr':args.lr,
         't00_replay_fraction':args.t00_replay_fraction,'completion_weight':args.completion_weight,
         'max_tokens':args.max_tokens,'max_depth':args.max_depth,'max_unroll':args.max_unroll,
         'answer_threshold':args.answer_threshold,'drift_threshold':args.drift_threshold,
         'stall_patience':args.stall_patience,'frozen_qwen':True,'frozen_detector':True,
         'frozen_numeric':True,'oracle_at_inference':False,'lm_head_used':False,
         'min_t01_steps':args.min_t01_steps,'min_t01_exit_rate':args.min_t01_exit_rate,
         'min_t00_retention_rate':args.min_t00_retention_rate,
         'max_t00_cosine_drop':args.max_t00_cosine_drop,'min_intermediate_cosine':args.min_intermediate_cosine,
         'min_final_cosine':args.min_final_cosine,'min_zero_gain':args.min_zero_gain}
    base._atomic_json(out/'run_config.json',cfg)
    base._atomic_json(out/'t00_baseline.json',{'metrics':baseline,'cases':baseline_rows})
    print('T01_RETENTION_BASELINE='+json.dumps({'t00_exits':baseline['answer_exits'],
          't00_cases':baseline['cases'],'t00_gold_cosine':baseline['mean_generated_to_gold_cosine'],
          'warm_start_validated':True},sort_keys=True),flush=True)
    train_map={r['text']:v for r,v in zip(prepared['states']['train'],prepared['embeddings']['train'])}
    replay=t00.t00_pairs(train_chains)
    rng=random.Random(args.seed);torch.manual_seed(args.seed)
    opt=torch.optim.AdamW(link.parameters(),lr=args.lr,weight_decay=0)
    history=[];best_candidate=-math.inf;last=None;accepted=False
    for step in range(1,args.steps+1):
        replay_t00=rng.random()<args.t00_replay_fraction
        ch=rng.choice(replay if replay_t00 else eligible)
        h=train_map[ch[0]].to(args.device).reshape(1,-1)
        link.train()
        loss,parts=loop.train_suffix(bb,tok,link,det,ch,train_map,h,args.device,
                                     args.max_tokens,args.completion_weight)
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(link.parameters(),1.)
        if any(p.grad is not None for p in det.parameters()) or any(p.grad is not None for p in bb.parameters()):
            raise RuntimeError('T01_FROZEN_PARENT_RECEIVED_GRADIENT')
        opt.step()
        if step%args.eval_every and step!=args.steps:continue
        with torch.no_grad():
            t01_stats,t01_cases=evaluate_t01(bb,tok,link,det,numeric,dev_chains,reference,args,dcfg['scale'])
            t00_stats,t00_cases=t00.t00_eval(bb,tok,link,det,numeric,dev_chains,reference,t00_args,dcfg['scale'])
        retention=t00_regressions(baseline_rows,t00_cases,baseline,t00_stats,args)
        passed,reasons=gate_t01(t01_stats,retention,step,args)
        score=(t01_stats['correct_stop_depth']/t01_stats['cases']
              +.05*(t01_stats['mean_first_step_gold_cosine'] or 0)
              +.05*(t01_stats['mean_final_gold_cosine'] or 0)
              -.5*t01_stats['early_false_stops']/t01_stats['cases']
              -.5*(not retention['retention_pass']))
        if score>best_candidate:
            best_candidate=score
            _save(out/'candidate.pt',cfg,link,det,step,t01_stats,retention,False,reasons)
        last={'step':step,'train_mode':'T00_REPLAY' if replay_t00 else 'T01_TWO_HOP',
              'training_loss':float(loss.detach()),'training_parts':parts,
              't01':t01_stats,'t00':t00_stats,'retention':retention,
              'gate_pass':passed,'gate_reasons':reasons,'geometry_feedback_causal':False}
        history.append(last);base._atomic_json(out/'progress.json',last)
        base._atomic_json(out/'latest_t01_cases.json',t01_cases)
        print('T01_RETENTION_PROGRESS='+json.dumps(last,sort_keys=True),flush=True)
        if passed:
            _save(out/'accepted_T01.pt',cfg,link,det,step,t01_stats,retention,True,[])
            _save(out/'best.pt',cfg,link,det,step,t01_stats,retention,True,[])
            accepted=True
            print('T01_RETENTION_GATE='+json.dumps({'accepted':True,'step':step,
                't01_correct_exits':t01_stats['correct_stop_depth'],
                't00_retained':retention['current_correct'],
                'checkpoint':str(out/'best.pt')},sort_keys=True),flush=True)
            break
    if parent.file_hash(args.stage1_checkpoint)!=prepared['parent_hash'] or parent.file_hash(args.detector_checkpoint)!=args._det_sha:
        raise RuntimeError('T01_PARENT_MUTATED')
    base._atomic_json(out/'history.json',history)
    result={'schema':SCHEMA,'status':'T01_ACCEPTED_NOT_FULL_LATENT_CERTIFIED' if accepted else 'T01_PROVISIONAL_NOT_PROMOTED',
            'accepted_rung':'T01' if accepted else None,'checkpoint':str(out/'best.pt') if accepted else None,
            'candidate':str(out/'candidate.pt') if (out/'candidate.pt').exists() else None,
            'trained_steps':step,'qwen_unchanged':True,'detector_unchanged':True,
            'numeric_unchanged':True,'t00_warmstart_sha256':cfg['t00_sha256'],
            'dev_reference_cache_hit':ref_hit,'last_gate_reasons':last['gate_reasons'] if last else [],
            'last_t01':last['t01'] if last else None,'last_t00_retention':last['retention'] if last else None,
            'complex_geometry_observer_only':True,'actual_generated_feedback':True}
    base._atomic_json(out/'result.json',result)
    print('T01_RETENTION_RESULT='+json.dumps(result,sort_keys=True),flush=True)
    return result


def probe(args):
    import torch
    target_checkpoint=args.checkpoint
    if not target_checkpoint:raise ValueError('T01_PROBE_REQUIRES_CHECKPOINT')
    prepared,numeric,det,dcfg,link=_prepare(args)
    ck=t00._verified_link_checkpoint(target_checkpoint,args,prepared)
    if ck.get('training_schema')!=SCHEMA:raise RuntimeError('T01_PROBE_UNEXPECTED_CHECKPOINT')
    link.load_state_dict(ck['link'],strict=True)
    dev_chains=loop.chains_from_rows(prepared['deck']['dev'],allow_partial=True)
    refs,hit=_references(prepared,args,dev_chains)
    t00_args=SimpleNamespace(**vars(args));t00_args.eval_cases=args.t00_cases
    with torch.no_grad():
        t01_summary,rows=evaluate_t01(prepared['bb'],prepared['tok'],link,det,numeric,dev_chains,refs,args,dcfg['scale'])
        t00_summary,_=t00.t00_eval(prepared['bb'],prepared['tok'],link,det,numeric,dev_chains,refs,t00_args,dcfg['scale'])
    out={'schema':SCHEMA,'t01':t01_summary,'t00':t00_summary,'cases':rows,'reference_used_only_after_generation':True}
    if args.output:
        Path(args.output).mkdir(parents=True,exist_ok=True)
        base._atomic_json(Path(args.output)/'probe.json',out)
    print('T01_RETENTION_PROBE='+json.dumps({k:v for k,v in out.items() if k!='cases'},sort_keys=True),flush=True)
    return out


def preview(args):
    p,_=parent.load_parent(args);s=parent.stage1()
    chains={key:loop.chains_from_rows(parent._rows(s,p,key),allow_partial=(key=='dev')) for key in ('train','dev')}
    report={'schema':SCHEMA,'stage':'preview','train_t01_windows':len(t01_windows(chains['train'])),
            'dev_t01_windows':len(t01_windows(chains['dev'])),'min_t01_steps':args.min_t01_steps,
            't00_replay_fraction':args.t00_replay_fraction,'backbone_loaded':False}
    print('T01_RETENTION_PREVIEW='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','train','probe'),default='preview')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--stage1-checkpoint',required=True);p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--detector-checkpoint');p.add_argument('--warm-start-t00');p.add_argument('--checkpoint')
    p.add_argument('--cache-dir');p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_t01_retention_v1'))
    p.add_argument('--steps',type=int,default=600);p.add_argument('--eval-every',type=int,default=50)
    p.add_argument('--eval-cases',type=int,default=12);p.add_argument('--t00-cases',type=int,default=12)
    p.add_argument('--encoder-batch',type=int,default=12);p.add_argument('--max-tokens',type=int,default=192)
    p.add_argument('--max-depth',type=int,default=14);p.add_argument('--max-unroll',type=int,default=2)
    p.add_argument('--lr',type=float,default=.00005);p.add_argument('--completion-weight',type=float,default=.5)
    p.add_argument('--t00-replay-fraction',type=float,default=.3)
    p.add_argument('--min-t01-steps',type=int,default=100);p.add_argument('--min-gate-cases',type=int,default=8)
    p.add_argument('--min-t01-exit-rate',type=float,default=.75)
    p.add_argument('--min-t00-retention-rate',type=float,default=1.)
    p.add_argument('--max-t00-cosine-drop',type=float,default=.025)
    p.add_argument('--min-intermediate-cosine',type=float,default=.5)
    p.add_argument('--min-final-cosine',type=float,default=.7)
    p.add_argument('--min-zero-gain',type=float,default=.01)
    p.add_argument('--answer-threshold',type=float,default=.9)
    p.add_argument('--drift-threshold',type=float,default=1e-5);p.add_argument('--stall-patience',type=int,default=2)
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if not 0<=args.t00_replay_fraction<=1:raise ValueError('T01_INVALID_REPLAY_FRACTION')
    if args.stage=='preview':return preview(args)
    if not args.detector_checkpoint or not args.warm_start_t00:
        raise ValueError('T01_REQUIRES_DETECTOR_AND_ACCEPTED_T00_CHECKPOINT')
    return {'train':train,'probe':probe}[args.stage](args)

if __name__=='__main__':main()
