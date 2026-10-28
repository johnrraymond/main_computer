#!/usr/bin/env python3
"""Read-only, headless diagnosis of Patch 3 Qwen/HybridLink latent recurrence.

T00 = final PROBLEM -> ANSWER hop of each *complete* DEV chain.  The generator
sees only the source; targets and reference states are used *after* generation.
Neither backbone, link, detector nor numeric weights are trained or modified.
Reports do not certify answers or count marker-conditioned oracle as success.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from collections import defaultdict

from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_qwen_numeric_dual_cutover as parent

SCHEMA = 'nanojev.hybridlink.latent-diagnostics.v1'


def _mean(xs):
    finite = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return round(sum(finite)/len(finite), 8) if finite else None


def _cos(a, b):
    import torch.nn.functional as F
    return float(F.cosine_similarity(a.detach().reshape(1,-1).float(), b.detach().to(device=a.device).reshape(1,-1).float(), dim=-1)[0])


def _prob(det, h):
    import torch
    return float(torch.sigmoid(det(h.float()).reshape(-1))[0].detach())


def _safe(x):
    x = float(x)
    return x if math.isfinite(x) else None


def _readouts(numeric, h, scale, target=None):
    """Observational readout only. Never passed to HybridLink or Qwen feedback."""
    import torch
    with torch.no_grad():
        out=numeric(h.reshape(1,-1).float(),scale)
    keys=('direct_number','complex_number','a','b','direct_rho','complex_rho','delta_rho','phase')
    result={k:_safe(out[k].reshape(-1)[0]) for k in keys if k in out}
    a=result.get('a');b=result.get('b')
    result['complex_radius']=math.hypot(a,b) if a is not None and b is not None else None
    result['complex_finite']=all(result.get(k) is not None for k in keys if k in out)
    if target is not None:
        correct=math.asinh((float(target)-1)/scale)
        result['target_value']=target
        result['target_rho']=correct
        result['direct_code_abs_error']=abs(result['direct_rho']-correct) if result.get('direct_rho') is not None else None
        result['complex_code_abs_error']=abs(result['complex_rho']-correct) if result.get('complex_rho') is not None else None
        result['expected_complex_radius']=_safe(math.exp(correct))
        result['unit_circle_error']=abs(result['complex_radius']-1) if target==1 and result['complex_radius'] is not None else None
    return result


def generate_one(bb,tok,link,det,numeric,source,device,max_tokens,scale,*,control='intact'):
    """Produce state without receiving gold text, gold vectors, labels or oracle values."""
    import torch
    with torch.no_grad():
        prefix=loop.token_embeddings(bb,tok,source,device,max_tokens)
        mask=torch.ones(prefix.shape[:2],device=device,dtype=torch.long)
        before=bb.get_decoder()(inputs_embeds=prefix,attention_mask=mask,return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
        _,generated=loop.feedback_step(bb,link,prefix,before,device,zero_latent=(control=='zero_latent'))
        score=_prob(det,generated)
        readout=_readouts(numeric,generated,scale)
    return {'before':before,'generated':generated,'probability':score,'readout':readout,'control':control,
            'relative_drift':float(loop.fc.vector_drift(before,generated).reshape(-1)[0])}


def grade_one(generated, gold, expected, scale, threshold, *, zero=None, gold_prob=None, problem_prob=None,
              naked_prob=None, numeric=None, source=None, expected_depth=None):
    """Posthoc grading; no gold argument flows back to generate_one()."""
    primary=generated
    out=dict(primary['readout'])
    if numeric is not None:
        out=_readouts(numeric,primary['generated'],scale,expected)
    rec={'source':source,'target':f'ANSWER: {expected}','origin_depth':expected_depth,
         'generated_answer_probability':primary['probability'],
         'generated_would_exit':primary['probability']>=threshold,
         'gold_answer_probability':gold_prob,
         'gold_detected':gold_prob is not None and gold_prob>=threshold,
         'counterfactual_problem_probability':problem_prob,
         'counterfactual_bare_probability':naked_prob,
         'generated_to_gold_cosine':_cos(primary['generated'],gold),
         'initial_to_gold_cosine':_cos(primary['before'],gold),
         'generated_drift':primary['relative_drift'],
         'readout':out,'control':primary['control'],
         'geometry_feedback_causal':False}
    rec['gain_over_source_cosine']=rec['generated_to_gold_cosine']-rec['initial_to_gold_cosine']
    if zero is not None:
        rec['zero_to_gold_cosine']=_cos(zero['generated'],gold)
        rec['intact_minus_zero_cosine']=rec['generated_to_gold_cosine']-rec['zero_to_gold_cosine']
        rec['zero_answer_probability']=zero['probability']
        rec['zero_readout']=_readouts(numeric,zero['generated'],scale,expected) if numeric is not None else zero['readout']
    if gold_prob is not None:
        rec['gold_positive_generated_negative']=(gold_prob>=threshold and primary['probability']<threshold)
        rec['marker_contrast']=gold_prob-problem_prob if problem_prob is not None else None
    return rec


def summarize_t00(rows,threshold):
    n=len(rows)
    summary={'cases':n,'generated_answer_exits':sum(r['generated_would_exit'] for r in rows),
             'gold_reference_answer_exits':sum(r['gold_detected'] for r in rows),
             'gold_positive_generated_negative':sum(r.get('gold_positive_generated_negative',False) for r in rows),
             'mean_generated_to_gold_cosine':_mean(r['generated_to_gold_cosine'] for r in rows),
             'mean_initial_to_gold_cosine':_mean(r['initial_to_gold_cosine'] for r in rows),
             'mean_gain_over_source_cosine':_mean(r['gain_over_source_cosine'] for r in rows),
             'mean_intact_minus_zero_cosine':_mean(r.get('intact_minus_zero_cosine') for r in rows),
             'mean_generated_answer_probability':_mean(r['generated_answer_probability'] for r in rows),
             'mean_gold_answer_probability':_mean(r['gold_answer_probability'] for r in rows),
             'mean_counterfactual_problem_probability':_mean(r['counterfactual_problem_probability'] for r in rows),
             'mean_marker_contrast':_mean(r.get('marker_contrast') for r in rows),
             'mean_direct_code_abs_error':_mean(r['readout'].get('direct_code_abs_error') for r in rows),
             'mean_complex_code_abs_error':_mean(r['readout'].get('complex_code_abs_error') for r in rows),
             'mean_zero_direct_code_abs_error':_mean(r.get('zero_readout',{}).get('direct_code_abs_error') for r in rows),
             'mean_zero_complex_code_abs_error':_mean(r.get('zero_readout',{}).get('complex_code_abs_error') for r in rows),
             'mean_direct_vs_complex_code_disagreement':_mean(abs(r['readout']['delta_rho']) if r['readout'].get('delta_rho') is not None else None for r in rows),
             'unity_cases':sum(r['readout'].get('target_value')==1 for r in rows),
             'mean_unity_radius_error':_mean(r['readout'].get('unit_circle_error') for r in rows),
             'answer_threshold':threshold,'geometry_feedback_causal':False}
    if n==0:raise ValueError('DIAGNOSTIC_NO_T00_CASES')
    summary['triage']=triage(summary)
    return summary


def triage(summary):
    """Non-exclusive, evidenced failure tags; never certify success from one metric."""
    tags=[]
    if summary['generated_answer_exits']==0:tags.append('NO_GENERATED_ANSWER_EXITS')
    if summary['gold_reference_answer_exits']==0:tags.append('DETECTOR_REJECTS_GOLD_ANSWER_STATES')
    if summary['gold_positive_generated_negative']>0:tags.append('GENERATOR_TO_DETECTOR_DISTRIBUTION_GAP')
    if summary['mean_intact_minus_zero_cosine'] is not None and summary['mean_intact_minus_zero_cosine']<=0:
        tags.append('NO_POSITIVE_GAIN_VS_ZERO_LATENT')
    if summary['mean_gain_over_source_cosine'] is not None and summary['mean_gain_over_source_cosine']<=0:
        tags.append('NO_FORWARD_PROGRESS_TO_REFERENCE')
    if summary['mean_marker_contrast'] is not None and summary['mean_marker_contrast']>.5:
        tags.append('POSSIBLE_PREFIX_SHORTCUT')
    if not summary['geometry_feedback_causal']:tags.append('COMPLEX_HEAD_IS_OBSERVER_ONLY')
    return tags


def build_trace(bb,tok,link,det,numeric,chain,device,max_tokens,scale,threshold,drift,patience,max_depth):
    """Collect actual autonomous path. Gold chain is consulted ONLY after it stops."""
    import torch
    source=chain[0]
    if not source.startswith('PROBLEM: '):raise ValueError('DIAGNOSTIC_EXPECTS_PROBLEM')
    generated=[]
    with torch.no_grad():
        prefix=loop.token_embeddings(bb,tok,source,device,max_tokens)
        mask=torch.ones(prefix.shape[:2],device=device,dtype=torch.long)
        h=bb.get_decoder()(inputs_embeds=prefix,attention_mask=mask,return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
        fixed=0;status='BUDGET_EXHAUSTED'
        for step in range(1,max_depth+1):
            if prefix.shape[1]+1>max_tokens:
                status='CONTEXT_EXHAUSTED';break
            prefix,hnew=loop.feedback_step(bb,link,prefix,h,device)
            prob=_prob(det,hnew)
            action,fixed,measurement=loop.stopping_action(h,hnew,prob,threshold,drift,fixed,patience)
            generated.append({'step':step,'p_answer':prob,'action':action,
                              'relative_drift':measurement['relative_drift'],
                              'readout':_readouts(numeric,hnew,scale),
                              '_vector':hnew.detach().cpu()})
            h=hnew
            if action in ('ANSWER','STALLED'):
                status=action;break
    return {'root':source,'expected_depth':len(chain)-1,'status':status,
            'steps':len(generated),'generated':generated}


def grade_trace(trace,chain,reference,numeric,scale,threshold):
    """Reference vectors and reference numerical targets used only after rollout."""
    actual=trace['generated'];gold_targets=chain[1:]
    rows=[]
    for rec in actual:
        t=rec['step'];gold_text=gold_targets[t-1] if t<=len(gold_targets) else None
        row={k:v for k,v in rec.items() if k!='_vector'}
        row['gold_target']=gold_text
        row['expected_answer_at_step']=gold_text is not None and gold_text.startswith('ANSWER: ')
        if gold_text is not None:
            from tools import nanojev_qwen_numeric_dual_cutover as prev
            # The output of this hop is the reduction of the *preceding*
            # problem, NOT the next operation inside the target PROBLEM.
            # E.g. (2-1)+5 -> 1+5 produces 1, not 6.
            value=prev.numeric_label(chain[t-1])
            row['readout']=_readouts(numeric,rec['_vector'],scale,value)
            if gold_text in reference:
                row['target_vector_cosine']=_cos(rec['_vector'],reference[gold_text])
        rows.append(row)
    state={k:v for k,v in trace.items() if k!='generated'}
    state['history']=rows
    state['correct_exit']=(state['status']=='ANSWER' and state['steps']==state['expected_depth'])
    state['early_false_stop']=(state['status']=='ANSWER' and state['steps']<state['expected_depth'])
    state['late_exit']=(state['status']=='ANSWER' and state['steps']>state['expected_depth'])
    return state


def summarize_traces(traces):
    return {'cases':len(traces),'answer_exits':sum(x['status']=='ANSWER' for x in traces),
            'correct_exits':sum(x['correct_exit'] for x in traces),
            'early_false_stops':sum(x['early_false_stop'] for x in traces),
            'stalled':sum(x['status']=='STALLED' for x in traces),
            'budget_exhausted':sum(x['status']=='BUDGET_EXHAUSTED' for x in traces),
            'context_exhausted':sum(x['status']=='CONTEXT_EXHAUSTED' for x in traces),
            'mean_step_target_cosine':_mean(row.get('target_vector_cosine') for t in traces for row in t['history']),
            'mean_step_direct_code_abs_error':_mean(row['readout'].get('direct_code_abs_error') for t in traces for row in t['history']),
            'mean_step_complex_code_abs_error':_mean(row['readout'].get('complex_code_abs_error') for t in traces for row in t['history'])}


def select(chains,count):
    if count<1:raise ValueError('DIAGNOSTIC_CASES_MUST_BE_POSITIVE')
    # Rotate through all available origin depths, so a 6-case T00 test includes
    # deep and shallow contexts, rather than the first six shortest examples.
    return loop._select_dev_chains(chains,count)


def verify_paths(output,inputs):
    output=Path(output).resolve()
    for raw in inputs:
        p=Path(raw).resolve()
        if output==p or output in p.parents or p in output.parents:
            raise ValueError('DIAGNOSTIC_OUTPUT_OVERLAPS_INPUT:'+str(p))
    if output.suffix.lower() in ('.pt','.pth'):
        raise ValueError('DIAGNOSTIC_OUTPUT_MUST_BE_DIRECTORY')


def run(args):
    import torch
    if args.stage=='preview':
        pmeta,_=parent.load_parent(args)
        s=parent.stage1()
        ch=loop.chains_from_rows(parent._rows(s,pmeta,args.split),allow_partial=True)
        result={'schema':SCHEMA,'stage':'preview','complete_chains':len(ch),
                'available_depths':sorted(set(len(c)-1 for c in ch)),
                't00':len(ch),'trained_weights_loaded':False}
        print('LATENT_DIAGNOSTIC_PREVIEW='+json.dumps(result,sort_keys=True),flush=True)
        return result
    if not args.detector_checkpoint or not args.checkpoint:
        raise ValueError('DIAGNOSTIC_REQUIRES_DETECTOR_AND_LOOP_CHECKPOINT')
    if args.t00_cases<1 or args.trace_cases<1 or args.max_depth<1:
        raise ValueError('DIAGNOSTIC_BAD_CASE_COUNT_OR_DEPTH')
    inputs=[args.stage1_checkpoint,args.text_checkpoint,args.detector_checkpoint,args.checkpoint]
    verify_paths(args.output,[Path(x).parent for x in inputs])
    hashes={Path(x).name+'@'+str(i):parent.file_hash(x) for i,x in enumerate(inputs)}
    loop_cfg,ck=loop._load_runtime_checkpoint(args)
    loop_ckpt=args.checkpoint
    args._det_sha=parent.file_hash(args.detector_checkpoint)
    args.checkpoint=args.detector_checkpoint
    s,model,tok,num,det,dcfg,pmeta=loop._runtime(args)
    args.checkpoint=loop_ckpt
    bb=model.backbone; bb.eval();num.eval();det.eval()
    link=loop.make_link(bb.config.hidden_size,bb.get_input_embeddings().embedding_dim).to(args.device)
    link.load_state_dict(ck['link'],strict=True)
    det.load_state_dict(ck['detector'],strict=True)
    link.eval();det.eval()
    scale=float(dcfg['scale'])
    max_tokens=loop_cfg['max_tokens']
    threshold=args.answer_threshold if args.answer_threshold is not None else loop_cfg['answer_threshold']
    ch=loop.chains_from_rows(parent._rows(s,pmeta,args.split),allow_partial=True)
    t00=select(ch,args.t00_cases)
    chosen=select(ch,args.trace_cases) if args.stage=='all' else []
    # First perform genuine generated feedback without supplying gold states.
    # Even checkpoint metadata and readout target values cannot influence it.
    generated=[]
    for i,chain in enumerate(t00):
        source=chain[-2]
        primary=generate_one(bb,tok,link,det,num,source,args.device,max_tokens,scale)
        zero=generate_one(bb,tok,link,det,num,source,args.device,max_tokens,scale,control='zero_latent')
        generated.append((chain,primary,zero))
        print('LATENT_DIAGNOSTIC_T00_PROGRESS='+json.dumps({'done':i+1,'total':len(t00),
              'generated_answer_probability':round(primary['probability'],6),
              'drift':round(primary['relative_drift'],6),'root_depth':len(chain)-1},sort_keys=True),flush=True)
    raw_traces=[]
    for j,chain in enumerate(chosen):
        r=build_trace(bb,tok,link,det,num,chain,args.device,max_tokens,scale,threshold,
                      args.drift_threshold,args.stall_patience,args.max_depth)
        raw_traces.append((chain,r))
        print('LATENT_DIAGNOSTIC_TRACE_PROGRESS='+json.dumps({'done':j+1,'total':len(chosen),
              'steps':r['steps'],'status':r['status'],'expected_depth':len(chain)-1},sort_keys=True),flush=True)
    # Encode reference states only *after* every generated transition has finished.
    reference_texts=sorted(set([chain[-1] for chain,_,_ in generated]+
                               [state for chain,_ in raw_traces for state in chain[1:]]+
                               [f'PROBLEM: {chain[-1][8:].strip()}' for chain,_,_ in generated]+
                               [chain[-1][8:].strip() for chain,_,_ in generated]))
    # Token lengths for counterfactual probes are small; error loudly on overflow.
    print('LATENT_DIAGNOSTIC_REFERENCE_ENCODING='+json.dumps({'states':len(reference_texts),'batch':args.encoder_batch},sort_keys=True),flush=True)
    with torch.no_grad():
        refs=s._encode(bb,tok,reference_texts,args.device,args.encoder_batch,max_tokens).detach().cpu()
    reference=dict(zip(reference_texts,refs))
    rows=[]
    for chain,primary,zero in generated:
        target=chain[-1];expected=int(target[len('ANSWER: '):].strip())
        prob_gold=_prob(det,reference[target].to(args.device).reshape(1,-1))
        cp=f'PROBLEM: {expected}';p_problem=_prob(det,reference[cp].to(args.device).reshape(1,-1))
        naked=str(expected);p_naked=_prob(det,reference[naked].to(args.device).reshape(1,-1))
        row=grade_one(primary,reference[target].to(args.device),expected,scale,threshold,zero=zero,
                gold_prob=prob_gold,problem_prob=p_problem,naked_prob=p_naked,numeric=num,
                source=chain[-2],expected_depth=len(chain)-1)
        rows.append(row)
    diag=summarize_t00(rows,threshold)
    traces=[grade_trace(trace,chain,reference,num,scale,threshold) for chain,trace in raw_traces]
    trsum=summarize_traces(traces) if traces else None
    # A zero-latent improvement is informative; numeric disable/enable cannot
    # change a generated state because numeric is never used in feedback_step.
    evidence={'schema':SCHEMA,'stage':args.stage,'split':args.split,
              'no_training':True,'no_model_weights_written':True,'lm_head_called':False,
              'teacher_in_inference':False,'reference_encoded_after_generation':True,
              'numerical_geometry_in_feedback':False,
              'numerical_geometry_causal_ablation_possible_now':False,
              'reason_geometry_not_causal':'feedback_step receives bb,link,prefix,h,device only; numerical readouts are downstream observers',
              'loop_checkpoint':loop_ckpt,'loop_checkpoint_step':ck['step'],
              'loop_checkpoint_degenerate_score':ck.get('dev',{}).get('correct_stop_depth',None)==0,
              't00':diag,'t00_cases':rows,'trace':trsum,'trace_cases':traces,
              'source_sha256':hashes}
    for i,x in enumerate(inputs):
        if parent.file_hash(x)!=hashes[Path(x).name+'@'+str(i)]:
            raise RuntimeError('DIAGNOSTIC_INPUT_CHECKPOINT_MUTATED')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    report=out/'diagnostic.json'
    tmp=out/'diagnostic.json.partial'
    try:
        tmp.write_text(json.dumps(evidence,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf8')
        tmp.replace(report)
    finally:tmp.unlink(missing_ok=True)
    banner={'schema':SCHEMA,'stage':args.stage,'t00':diag,'trace':trsum,
            'report':str(report),'no_model_weights_written':True,
            'geometry_observer_only':True}
    print('LATENT_DIAGNOSTIC_RESULT='+json.dumps(banner,sort_keys=True),flush=True)
    return evidence


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','t00','all'),default='t00')
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--split',choices=('dev','ood','test'),default='dev')
    p.add_argument('--stage1-checkpoint',required=True)
    p.add_argument('--text-checkpoint',required=True)
    p.add_argument('--detector-checkpoint')
    p.add_argument('--checkpoint')
    p.add_argument('--t00-cases',type=int,default=12)
    p.add_argument('--trace-cases',type=int,default=6)
    p.add_argument('--max-depth',type=int,default=12)
    p.add_argument('--encoder-batch',type=int,default=12)
    p.add_argument('--answer-threshold',type=float)
    p.add_argument('--drift-threshold',type=float,default=1e-5)
    p.add_argument('--stall-patience',type=int,default=2)
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'hybridlink_latent_diagnostics_v1'))
    # Compatibility with parent model loading helpers.
    p.add_argument('--baseline');p.add_argument('--backbone');p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    if args.answer_threshold is not None and not 0<args.answer_threshold<=1:
        p.error('DIAGNOSTIC_INVALID_ANSWER_THRESHOLD')
    if args.drift_threshold<0 or args.stall_patience<1:p.error('DIAGNOSTIC_INVALID_DRIFT_SETTING')
    return run(args)


if __name__=='__main__':main()
