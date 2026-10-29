#!/usr/bin/env python3
"""Read-only LM-head packet decoding controls for bounded HybridLink zero-line tests.

Four explicitly separate paths:
* qwen_free: frozen Stage1 Qwen continues after ``PROBLEM: ...\\n``.
* qwen_prefilled: same Qwen continues after a declared ``ANSWER: `` prefix.
* hybrid_free: generated HybridLink latent loop stops, then Qwen LM head emits text.
* hybrid_prefilled: generated latent loop stops, then append a declared
  ``\\nANSWER: `` textual prefix before decoding with Qwen's vocabulary head.

The prefilling controls are an *intervention* and must never be described as
normal production emission. The checkpoint's Qwen LoRA is preserved; the frozen
LM head is used with no numerical sidecar, teacher vector, or gold target.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from tools import nanojev_hybridlink_frontier_repair as frontier
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_latent_diagnostics as diag
from tools import nanojev_hybridlink_readiness as readiness
from tools import nanojev_hybridlink_t00_curriculum_repair as t00
from tools import nanojev_qwen_numeric_dual_cutover as parent

SCHEMA='nanojev.hybridlink.lm-packet-probe.v1'
MODES=('qwen_free','qwen_prefilled','hybrid_free','hybrid_prefilled')
ANSWER_LINE=re.compile(r'^ANSWER:\s*([+-]?\d+)\s*$')
NUMBER_LINE=re.compile(r'^([+-]?\d+)\s*$')


def parse_answer(completion,mode):
    """Parse complete emitted reduction packet; no hidden oracle or guesswork.

    Free mode may emit intermediate ``PROBLEM: ...`` lines; the first
    terminal ``ANSWER: integer`` must be the final line. Prefilled mode must
    emit only a signed integer (the ``ANSWER:`` label was already provided).
    """
    lines=[line.strip() for line in completion.splitlines()]
    if mode.endswith('_prefilled'):
        first=lines[0] if lines else ''
        match=NUMBER_LINE.fullmatch(first)
        valid=bool(match and len(lines)==1)
        return {'valid_packet':valid,'number':int(match.group(1)) if valid else None,
                'first_line':first}
    if not lines:return {'valid_packet':False,'number':None,'first_line':''}
    # A full packet may contain multiple explicit reduction lines, but only
    # the final line can be an ANSWER. Do not skip errors or accept later tries.
    valid_prefix=all(line.startswith('PROBLEM: ') for line in lines[:-1])
    match=ANSWER_LINE.fullmatch(lines[-1])
    valid=bool(valid_prefix and match)
    return {'valid_packet':valid,'number':int(match.group(1)) if valid else None,
            'first_line':lines[0]}


def _ids(tok,text):
    tokens=tok(text,add_special_tokens=False)['input_ids']
    if not tokens:raise RuntimeError('LM_PACKET_EMPTY_TOKEN_INPUT')
    return tokens


def completion(bb,tok,device,max_tokens,max_new_tokens,*,text=None,embeddings=None,first_hidden=None,stop_after_first_line=False):
    """Greedy Qwen LM-head decoder with full-prefix replay; no KV-cache drift.

    The first_hidden option makes the first emitted token read *exactly* the
    generated HybridLink terminal hidden state (before any textual prefill).
    """
    import torch
    if (text is None)==(embeddings is None):raise ValueError('LM_PACKET_NEEDS_ONE_PREFIX')
    head=bb.get_output_embeddings()
    if head is None:return {'status':'lm_head_unavailable','completion':'','tokens':0,'stop':'unavailable'}
    if text is not None:
        tensor=torch.tensor([_ids(tok,text)],device=device,dtype=torch.long)
        prefix=bb.get_input_embeddings()(tensor).detach()
    else:
        prefix=embeddings.detach()
    if prefix.shape[1]>=max_tokens:
        return {'status':'context_exhausted','completion':'','tokens':0,'stop':'context'}
    emitted=[];stop='token_budget';decoder=bb.get_decoder()
    eos=tok.eos_token_id
    eos_ids={int(eos)} if isinstance(eos,int) else set(int(x) for x in eos or ())
    with torch.no_grad():
        for step in range(max_new_tokens):
            if prefix.shape[1]>=max_tokens:
                stop='context';break
            if step==0 and first_hidden is not None:
                h=first_hidden.detach()
            else:
                mask=torch.ones(prefix.shape[:2],device=device,dtype=torch.long)
                h=decoder(inputs_embeds=prefix,attention_mask=mask,
                          return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
            weight=getattr(head,'weight',None)
            dtype=weight.dtype if weight is not None else h.dtype
            logits=head(h.to(dtype=dtype))
            token=int(logits.argmax(dim=-1).reshape(-1)[0].item())
            if token in eos_ids:
                stop='eos';break
            emitted.append(token)
            decoded=tok.decode(emitted,skip_special_tokens=True)
            if '\n' in decoded or '\r' in decoded:
                complete=decoded.replace('\r\n','\n').replace('\r','\n').split('\n')[:-1]
                if stop_after_first_line or any(ANSWER_LINE.fullmatch(line.strip()) for line in complete):
                    stop='answer_line' if not stop_after_first_line else 'newline'
                    break
            ids=torch.tensor([[token]],device=device,dtype=torch.long)
            prefix=torch.cat((prefix,bb.get_input_embeddings()(ids).detach()),dim=1)
    text_out=tok.decode(emitted,skip_special_tokens=True)
    packet=text_out.replace('\r\n','\n').replace('\r','\n').rstrip('\n')
    return {'status':'observed','completion':packet,'tokens':len(emitted),'stop':stop}


def generated_latent(bb,tok,link,det,source,args):
    """Actual generated feedback with detector stopping; zero teacher leakage."""
    import torch
    with torch.no_grad():
        prefix=loop.token_embeddings(bb,tok,source,args.device,args.max_tokens)
        mask=torch.ones(prefix.shape[:2],device=args.device,dtype=torch.long)
        h=bb.get_decoder()(inputs_embeds=prefix,attention_mask=mask,
                           return_dict=True,use_cache=False).last_hidden_state[:,-1,:]
        fixed=0;status='BUDGET_EXHAUSTED';hist=[]
        for step in range(1,args.max_rung+2+args.overrun_steps):
            if prefix.shape[1]+1>args.max_tokens:
                status='CONTEXT_EXHAUSTED';break
            prefix,hnext=loop.feedback_step(bb,link,prefix,h,args.device)
            prob=diag._prob(det,hnext)
            action,fixed,measurement=loop.stopping_action(
                h,hnext,prob,args.answer_threshold,args.drift_threshold,fixed,args.stall_patience)
            hist.append({'step':step,'action':action,'p_answer':prob,
                         'relative_drift':measurement['relative_drift']})
            h=hnext
            if action in ('ANSWER','STALLED'):
                status=action;break
    return prefix,h,{'status':status,'steps':len(hist),'history':hist}


def _append_marker(bb,tok,prefix,device,marker='\nANSWER: '):
    import torch
    suffix=torch.tensor([_ids(tok,marker)],device=device,dtype=torch.long)
    return torch.cat((prefix,bb.get_input_embeddings()(suffix).detach()),dim=1)


def _cases(args):
    meta,_=parent.load_parent(args)
    s=parent.stage1()
    if args.generate_zero_line:
        from tools import nanojev_hybridlink_zero_line_deck as deck
        _,_,rows=deck.build(max_rung=args.max_rung,radius=args.radius,
              operand_limit=args.operand_limit,per_cell=args.generated_per_cell,
              seed=args.seed,oracle=readiness.verify_oracle)
        fingerprint=deck.fingerprint(rows)
        origin='synthetic_diagnostic_only'
    else:
        sources=parent._rows(s,meta,args.split)
        chains=loop.chains_from_rows(sources,allow_partial=False)
        _,_,rows=readiness.catalog(chains,s,args.max_rung,args.radius,args.seed,
                                   args.max_cases_per_rung,args.operand_limit)
        fingerprint=None;origin='recorded_development_chains'
    limit=args.lm_packet_cases_per_rung
    if limit:
        chosen=[];counts={}
        for row in rows:
            rung=row['rung']
            if counts.get(rung,0)>=limit:continue
            chosen.append(row);counts[rung]=counts.get(rung,0)+1
        rows=chosen
    return rows,fingerprint,origin


def run(args):
    import torch
    readiness._validate(args)
    if args.lm_packet_max_new_tokens<1 or args.lm_packet_cases_per_rung<0:
        raise ValueError('LM_PACKET_INVALID_LIMITS')
    modes=tuple(dict.fromkeys(x.strip() for x in args.lm_packet_modes.split(',') if x.strip()))
    if not modes or any(m not in MODES for m in modes):
        raise ValueError('LM_PACKET_INVALID_MODES:'+args.lm_packet_modes)
    need_hybrid=any(m.startswith('hybrid_') for m in modes)
    if need_hybrid and (not args.checkpoint or not args.detector_checkpoint):
        raise ValueError('LM_PACKET_HYBRID_REQUIRES_LINK_AND_DETECTOR')
    rows,fingerprint,origin=_cases(args)
    diag.verify_paths(args.output,[Path(args.stage1_checkpoint).parent,
        Path(args.text_checkpoint).parent]+([Path(args.detector_checkpoint).parent,
        Path(args.checkpoint).parent] if need_hybrid else [])+
        ([Path(args.cache_dir)] if args.cache_dir else []))
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if need_hybrid:
        checkpoint=args.checkpoint
        prepared,num,det,dcfg=t00._prepare(args)
        args.checkpoint=checkpoint
        verified=frontier.verify_input(checkpoint,args,{**prepared,'det':det})
        bb=prepared['bb'];tok=prepared['tok']
        link=loop.make_link(int(bb.config.hidden_size),
             int(bb.get_input_embeddings().embedding_dim)).to(args.device)
        link.load_state_dict(verified['link'],strict=True);link.eval();det.eval()
    else:
        cfg,ck=parent.load_parent(args);s=parent.stage1()
        mdl,tok,probe,source,frozen,dirs=parent._load_qwen(s,args,cfg,ck)
        bb=mdl.backbone
    bb.eval();bb.requires_grad_(False)
    expected_by_rung={str(r):{'cases':0,**{m:{'correct':0,'valid_packet':0,'observed':0} for m in modes}}
                      for r in range(args.max_rung+1)}
    results=[]
    for index,row in enumerate(rows,1):
        source_text=row['chain'][0];target=int(row['answer']);rung=row['rung']
        item={'rung':rung,'answer':target,'band':row['band'] if 'band' in row else
              readiness.band(row['max_literal'],args.operand_limit),
              'full_chain':row['full_chain'],'source':source_text,'expected_steps':rung+1,
              'modes':{}}
        latent=None;hidden=None;exit_info=None
        if need_hybrid:
            latent,hidden,exit_info=generated_latent(bb,tok,link,det,source_text,args)
            item['hybrid_exit']=exit_info
        for mode in modes:
            if mode=='qwen_free':
                observation=completion(bb,tok,args.device,args.max_tokens,
                    args.lm_packet_max_new_tokens,text=source_text+'\n')
            elif mode=='qwen_prefilled':
                observation=completion(bb,tok,args.device,args.max_tokens,
                    args.lm_packet_max_new_tokens,text=source_text+'\nANSWER: ',stop_after_first_line=True)
            elif mode=='hybrid_free':
                observation=completion(bb,tok,args.device,args.max_tokens,
                    args.lm_packet_max_new_tokens,embeddings=latent,first_hidden=hidden)
            else:
                marker=_append_marker(bb,tok,latent,args.device)
                observation=completion(bb,tok,args.device,args.max_tokens,
                    args.lm_packet_max_new_tokens,embeddings=marker,stop_after_first_line=True)
            parsed=parse_answer(observation['completion'],mode)
            observed=observation['status']=='observed'
            valid=observed and parsed['valid_packet']
            exact=valid and parsed['number']==target
            # For the HybridLink arms, exact arithmetic is reported separately
            # from whether the detector stopped at the oracle's rung.
            packet={**observation,**parsed,'exact':exact,
                   'full_packet':(source_text+'\n'+('ANSWER: ' if mode.endswith('_prefilled') else '')+observation['completion']),
                   'canonical_reduction_chain_match':(observation['completion'].splitlines()==row['chain'][1:])
                       if mode=='qwen_free' and valid else None,
                   'exit_correct':(exit_info['status']=='ANSWER' and
                        exit_info['steps']==rung+1) if mode.startswith('hybrid_') else None,
                   'prompt_protocol':('ANSWER marker prefilled' if mode.endswith('_prefilled')
                     else 'no ANSWER marker prefilled')}
            item['modes'][mode]=packet
            bucket=expected_by_rung[str(rung)][mode]
            bucket['observed']+=int(observed);bucket['valid_packet']+=int(valid)
            bucket['correct']+=int(exact)
        expected_by_rung[str(rung)]['cases']+=1
        results.append(item)
        print('HYBRID_LM_PACKET_PROGRESS='+json.dumps({'done':index,'total':len(rows),
            'rung':rung,'band':item['band'],'expected':target,
            'results':{m:{'number':v['number'],'valid':v['valid_packet'],'exact':v['exact'],
                          'text':v['completion']} for m,v in item['modes'].items()}},
            sort_keys=True),flush=True)
    report={'schema':SCHEMA,'status':'diagnostic_only','no_training':True,
        'source_checkpoint':str(args.checkpoint) if need_hybrid else None,
        'source_stage1_checkpoint':str(args.stage1_checkpoint),
        'split':args.split,'target':{'max_rung':args.max_rung,'radius':args.radius,
                                   'operand_limit':args.operand_limit},
        'deck_origin':origin,'deck_sha256':fingerprint,'modes':modes,'cases':results,
        'per_rung':expected_by_rung,
        'evidence':{
          'qwen_free':'Stage1 frozen Qwen LoRA + original LM vocabulary head; no answer prefix supplied',
          'qwen_prefilled':'Stage1 frozen Qwen + LM head, explicit ANSWER marker supplied, number not supplied',
          'hybrid_free':'Generated HybridLink stopping state + Qwen LM head; no answer marker supplied',
          'hybrid_prefilled':'Generated HybridLink stopping state + Qwen LM head with explicit ANSWER marker; counterfactual decoding intervention',
          'not_covered':'Text teacher parent uses a different <REDUCE> protocol; this is not a test of autonomous teacher arithmetic',
          'gold_never_fed_at_inference':True,
          'lm_head_not_trained_by_stage1':True,
          'format_failures_not_equal_to_arithmetic_failures':True,
          'generated_state_decoder_not_trained_for_text_emission':True}}
    tmp=out/'lm_packet_report.json.partial'
    tmp.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(out/'lm_packet_report.json')
    with (out/'lm_packet_cases.csv').open('w',encoding='utf-8',newline='') as f:
        columns=['rung','band','answer','source','mode','completion','parsed_number',
                 'valid_packet','exact','status','stop','generated_exit_status',
                 'generated_exit_steps','generated_exit_correct']
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
        for rec in results:
            for mode,obs in rec['modes'].items():
                exit_=rec.get('hybrid_exit') or {}
                writer.writerow({'rung':rec['rung'],'band':rec['band'],'answer':rec['answer'],
                      'source':rec['source'],'mode':mode,'completion':obs['completion'],
                      'parsed_number':obs['number'],'valid_packet':obs['valid_packet'],
                      'exact':obs['exact'],'status':obs['status'],'stop':obs['stop'],
                      'generated_exit_status':exit_.get('status') if mode.startswith('hybrid_') else None,
                      'generated_exit_steps':exit_.get('steps') if mode.startswith('hybrid_') else None,
                      'generated_exit_correct':obs['exit_correct']})
    print('HYBRID_LM_PACKET_RESULT='+json.dumps({'schema':SCHEMA,'cases':len(results),
           'per_rung':expected_by_rung,'modes':modes,'output':str(out/'lm_packet_report.json'),
           'csv':str(out/'lm_packet_cases.csv'),'status':'diagnostic_only'},sort_keys=True),flush=True)
    return report
