#!/usr/bin/env python3
"""Read-only, answer-free teacher controls for Qwen/HybridLink latent consumption.

The one-token teacher control is a *valid native input embedding*, not a gold
answer or a certified sufficient latent thought. A separate full answer-free
teacher-prefix control diagnoses dependence on context length. No teacher
inputs enter the production latent path, and no optimizer is constructed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import torch
from torch.nn import functional as F

from tools import nanojev_qwen_hybrid_alignment_repair as align
from tools import nanojev_qwen_hybrid_interface_repair as interface
from tools import nanojev_qwen_zero_line_backbone as zero

SCHEMA = 'nanojev.qwen3-06b.hybrid-consumption-diagnostic.v1'
OUTPUT = Path.home()/'NanoJev'/'runs'/'qwen3_06b_hybrid_consumption_diagnostic_v1'
CONDITIONS = ('text','marker_only','zero','intact','teacher_one',
              'teacher_full','teacher_wrong_full')


def answer_free_thought(row):
    """Same pre-answer teacher text as format activation, without reading gold."""
    assert row['family'] in ('direct','composed'), 'CONSUMPTION_UNKNOWN_FAMILY'
    sentence = ('Combine the signed remainders.' if row['family']=='composed'
                else 'Keep the signed remainder.')
    thought = 'Equal and opposite terms cancel. '+sentence
    assert re.search(r'\d',thought) is None, 'CONSUMPTION_THOUGHT_CONTAINS_NUMERAL'
    return thought


def teacher_tokens(tok, row):
    thought = answer_free_thought(row)
    tokens=tok(thought,add_special_tokens=False)['input_ids']
    assert tokens and all(isinstance(t,int) and t>=0 for t in tokens), 'CONSUMPTION_TEACHER_TOKEN_INVALID'
    return tokens


def _prefix(model,tok,ids,cond,row):
    """Construct identical prompt and marker boundaries for each intervention.

    The only condition that receives a full trajectory receives *answer-free*
    reasoning text as native input embeddings (not a fitted latent vector).
    """
    assert cond in CONDITIONS,'CONSUMPTION_UNKNOWN_CONDITION'
    assert ids.ndim==2 and ids.shape[0]==1 and ids.shape[1]>0,'CONSUMPTION_BAD_PROMPT'
    assert row['family'] in ('direct','composed'),'CONSUMPTION_BAD_FAMILY'
    if cond=='text':
        mask=torch.ones_like(ids)
        _,logits,cache=model._step(input_ids=ids,mask=mask)
        return logits,cache,mask,0
    start=ids.new_tensor([[model.start_id]])
    end=ids.new_tensor([[model.end_id]])
    if cond=='marker_only':
        full=torch.cat((ids,start,end),dim=1)
        mask=torch.ones_like(full)
        _,logits,cache=model._step(input_ids=full,mask=mask)
        return logits,cache,mask,0
    marked=torch.cat((ids,start),dim=1)
    mask=torch.ones_like(marked)
    h,_,cache=model._step(input_ids=marked,mask=mask)
    table=model.backbone.get_input_embeddings()
    if cond=='zero':
        vector=torch.zeros_like(table(start))
    elif cond=='intact':
        vector=model.head(h.float())[:,None,:]
    else:
        if cond=='teacher_wrong_full':
            other={'family':'composed' if row['family']=='direct' else 'direct'}
            native=teacher_tokens(tok,other)
        else:
            native=teacher_tokens(tok,row)
        if cond=='teacher_one':native=native[:1]
        native_ids=torch.tensor([native],dtype=torch.long,device=ids.device)
        vector=table(native_ids)
    assert vector.ndim==3 and vector.shape[0]==1 and vector.shape[1]>=1
    assert torch.isfinite(vector.float()).all(),'CONSUMPTION_VECTOR_NONFINITE'
    mask=torch.cat((mask,mask.new_ones((1,vector.shape[1]))),dim=1)
    _,_,cache=model._step(inputs_embeds=vector,cache=cache,mask=mask)
    mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
    _,logits,cache=model._step(input_ids=end,cache=cache,mask=mask)
    return logits,cache,mask,int(vector.shape[1])


def _generate_from_prefix(model,tok,logits,cache,mask,budget):
    assert 1<=budget<=256,'CONSUMPTION_BAD_BUDGET'
    tokens=[]
    for _ in range(budget):
        nxt=logits.argmax(dim=-1,keepdim=True)
        token=int(nxt.item())
        if tok.eos_token_id is not None and token==tok.eos_token_id:break
        tokens.append(token)
        mask=torch.cat((mask,mask.new_ones((1,1))),dim=1)
        _,logits,cache=model._step(input_ids=nxt,cache=cache,mask=mask)
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    text=tok.decode(tokens,skip_special_tokens=True)
    result=paired.parse_answer(text)
    return {'answer':result['answer_label'],'leading_integer':result['leading_integer'],
            'parseable':result['answer_label'] is not None,
            'completion':text[:300], 'tokens':len(tokens)}


def _first_answer_logprob(model,tok,ids,cond,row):
    """Teacher labels are used only as NEXT-token scoring targets, never inputs."""
    _,cache,mask,_=_prefix(model,tok,ids,cond,row)
    suffix=tok('\nAnswer: ',add_special_tokens=False)['input_ids']
    assert suffix,'CONSUMPTION_EMPTY_ANSWER_SUFFIX'
    suffix_ids=torch.tensor([suffix],dtype=torch.long,device=ids.device)
    mask=torch.cat((mask,mask.new_ones((1,len(suffix)))),dim=1)
    _,logits,_=model._step(input_ids=suffix_ids,cache=cache,mask=mask)
    target=tok(str(row['answer']),add_special_tokens=False)['input_ids']
    assert target,'CONSUMPTION_EMPTY_ANSWER_LABEL'
    # Only score first answer token: gold labels never enter a model forward.
    return float(F.log_softmax(logits.float(),dim=-1)[0,target[0]]),int(target[0])


def _boundary_geometry(model,tok,ids,row):
    marked=torch.cat((ids,ids.new_tensor([[model.start_id]])),dim=1)
    h,_,_=model._step(input_ids=marked,mask=torch.ones_like(marked))
    first=torch.tensor([[teacher_tokens(tok,row)[0]]],device=ids.device,dtype=torch.long)
    ref=model.backbone.get_input_embeddings()(first).float()[:,0,:]
    candidate=model.head(h.float()).float()
    cosine=float(F.cosine_similarity(candidate,ref,dim=-1))
    return {'head_norm':float(candidate.norm()),'teacher_one_norm':float(ref.norm()),
            'cosine_to_teacher_one':cosine,
            'head_to_teacher_one_norm_ratio':float(candidate.norm()/ref.norm().clamp_min(1e-12)),
            'teacher_first_token_id':int(first.item()),
            'candidate':candidate.cpu().reshape(-1)}


def summarize(records):
    assert records and all(set(r['variants'])==set(CONDITIONS) for r in records), 'CONSUMPTION_MISSING_CONDITIONS'
    scores={}
    for cond in CONDITIONS:
        vals=[r['variants'][cond] for r in records]
        scores[cond]={'correct':sum(x['answer']==r['expected'] for x,r in zip(vals,records)),
            'parseable':sum(bool(x['parseable']) for x in vals),
            'cases':len(vals),
            'mean_first_answer_logprob':sum(x['first_answer_logprob'] for x in vals)/len(vals)}
    head_vs_ref=sum(r['geometry']['cosine_to_teacher_one'] for r in records)/len(records)
    first_ids=set(r['geometry']['teacher_first_token_id'] for r in records)
    # Descriptive evidence, NOT a pass/fail certificate. One teacher token may
    # carry no task-specific information; a full prefix gets more token budget.
    evidence={
        'teacher_one_minus_intact_correct':scores['teacher_one']['correct']-scores['intact']['correct'],
        'teacher_full_minus_intact_correct':scores['teacher_full']['correct']-scores['intact']['correct'],
        'intact_minus_zero_correct':scores['intact']['correct']-scores['zero']['correct'],
        'intact_minus_wrong_teacher_full_correct':scores['intact']['correct']-scores['teacher_wrong_full']['correct'],
        'teacher_first_token_unique_ids':len(first_ids),
        'mean_head_to_teacher_first_cosine':head_vs_ref,
    }
    if scores['teacher_one']['correct']>scores['intact']['correct']:
        interpretation='Native one-token reference outperformed learned head; investigate head generation and downstream conditioning.'
    elif scores['teacher_full']['correct']>scores['teacher_one']['correct']:
        interpretation='Full answer-free text context helped; one token may be insufficient. Does not by itself identify a backbone defect.'
    else:
        interpretation='No advantage from answer-free references demonstrated; this does not establish that the backbone cannot consume useful latent thoughts.'
    return {'score':scores,'evidence':evidence,'interpretation':interpretation}


def _preflight(interface_dir,repair_root):
    interface_dir,repair_root=Path(interface_dir),Path(repair_root)
    # Preexisting provenance checks on BOTH stages; fail closed on stale heads.
    original=align._sources(interface_dir)
    gate=align._json(repair_root/'gate_result.json')
    rec=align._json(repair_root/'recurrence.json')
    geometry=align._json(repair_root/'geometry_gate.json')
    sha=interface.fingerprint(repair_root/'alignment'/'best.pt')
    assert gate['schema']==align.SCHEMA and gate['sources']==original and gate['new_head_checkpoint_sha256']==sha, 'CONSUMPTION_GATE_DRIFT'
    assert gate['status'] in ('ONE_STEP_FAIL','ONE_STEP_PASS'),'CONSUMPTION_GATE_INCOMPLETE'
    assert rec['schema']==align.SCHEMA and rec['pass'] and rec['repair_head_sha256']==sha and rec['source_checkpoints']==original,'CONSUMPTION_RECURRENCE_DRIFT'
    assert geometry['schema']==align.SCHEMA and geometry['pass'],'CONSUMPTION_GEOMETRY_NOT_PASSED'
    assert align._json(repair_root/'alignment'/'result.json')['status']=='TRAINED_UNCERTIFIED','CONSUMPTION_HEAD_INCOMPLETE'
    return {'format':original['format'],'old_head':original['head'],'repaired_head':sha,
        'prior_gate_status':gate['status'],'original_sources':original}


def run(*,device='cuda',base=interface.hybrid.DEFAULT_OUTPUT,numerical=zero.DEFAULT_OUTPUT,
        interface_dir=interface.ROOT,repair_root=align.ROOT,output=OUTPUT,
        cases=18,budget=48,generate=True,loader=None,allow_download=False):
    base,numerical,interface_dir,repair_root,output=map(Path,(base,numerical,interface_dir,repair_root,output))
    assert cases>=6 and cases<=120 and cases%6==0,'CONSUMPTION_BAD_CASE_COUNT'
    assert 1<=budget<=256,'CONSUMPTION_BAD_BUDGET'
    for source in (base,numerical,interface_dir,repair_root):
        assert output.resolve()!=source.resolve() and output.resolve() not in source.resolve().parents and source.resolve() not in output.resolve().parents, 'CONSUMPTION_OUTPUT_SOURCE_OVERLAP'
    assert not output.exists() or not any(output.iterdir()),'CONSUMPTION_OUTPUT_EXISTS'
    prior=_preflight(interface_dir,repair_root)
    model,tok,fc,hashes=align._load(device=device,base=base,numerical=numerical,
            interface_dir=interface_dir,loader=loader,allow_download=allow_download)
    assert hashes==prior['original_sources'],'CONSUMPTION_SOURCES_CHANGED_ON_LOAD'
    head,_=align._restore(model,repair_root,hashes)
    model.head=head
    model.eval().requires_grad_(False)
    rows=align._gate_rows(fc,cases)
    score_records=[]
    with torch.inference_mode():
        for row in rows:
            prompt=f"Question: Compute {row['expression']}.\nReasoning: "
            ids=torch.tensor([tok(prompt,add_special_tokens=False)['input_ids']],device=device,dtype=torch.long)
            assert ids.shape[1]>0,'CONSUMPTION_EMPTY_PROMPT'
            geometry=_boundary_geometry(model,tok,ids,row)
            geometry.pop('candidate')
            variants={}
            for cond in CONDITIONS:
                logprob,tok_id=_first_answer_logprob(model,tok,ids,cond,row)
                if generate:
                    logits,cache,mask,slots=_prefix(model,tok,ids,cond,row)
                    generated=_generate_from_prefix(model,tok,logits,cache,mask,budget)
                else:
                    slots=(len(teacher_tokens(tok,row)) if cond=='teacher_full' else
                           len(teacher_tokens(tok,{'family':'direct' if row['family']=='composed' else 'composed'}))
                           if cond=='teacher_wrong_full' else 0 if cond in ('text','marker_only') else 1)
                    generated={'answer':None,'leading_integer':None,'parseable':False,'completion':'','tokens':0}
                variants[cond]={**generated,'first_answer_logprob':logprob,
                                'first_answer_token_id':tok_id,'injected_slots':slots}
            record={'id':row['id'],'family':row['family'],'sign':row['sign'],
                'evaluation_group':'independent_blind' if row['id'].startswith('independent_') else 'original_regression',
                'expected':row['answer'],'geometry':geometry,'variants':variants}
            score_records.append(record)
            print('HYBRID_CONSUMPTION_CASE='+json.dumps(record,sort_keys=True,ensure_ascii=False),flush=True)
    out=summarize(score_records) if generate else {
        'score':{},'evidence':{'mean_head_to_teacher_first_cosine':sum(r['geometry']['cosine_to_teacher_one'] for r in score_records)/len(score_records)},
        'interpretation':'First-answer-token scoring only; no free-running accuracy certification.'}
    first_answer_scores={cond:sum(r['variants'][cond]['first_answer_logprob'] for r in score_records)/len(score_records)
                         for cond in CONDITIONS}
    # No teacher trajectory is ever given to the ordinary production mode.
    end=_preflight(interface_dir,repair_root)
    assert end==prior,'CONSUMPTION_SOURCE_CHECKPOINT_MUTATED'
    assert all(not x.requires_grad for x in model.parameters()),'CONSUMPTION_MODEL_UNFROZEN'
    result={'schema':SCHEMA,'status':'READ_ONLY_CONSUMPTION_DIAGNOSTIC_UNCERTIFIED',
        'sources':prior,'cases':cases,'dataset_fingerprint':zero.fingerprint(rows),
        'score':out['score'],'mean_first_answer_logprob':first_answer_scores,
        'evidence':out['evidence'],'interpretation':out['interpretation'],
        'teacher_references_answer_free':True,
        'teacher_one_native_token_only':True,'teacher_full_uses_extra_input_tokens':True,
        'oracle_gold_answer_used_as_model_input':False,
        'gold_answer_used_as_scoring_label':True,
        'optimizer_steps':0,'source_mutations':0,'deeper_training_permitted':False,
        'free_generation_evaluated':bool(generate),'records':score_records}
    output.mkdir(parents=True,exist_ok=True)
    zero.atomic_json(output/'result.json',result)
    print('HYBRID_CONSUMPTION_RESULT='+json.dumps({k:v for k,v in result.items() if k!='records'},sort_keys=True),flush=True)
    print('HYBRID_CONSUMPTION_REPORT='+str((output/'result.json').resolve()),flush=True)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path,default=interface.hybrid.DEFAULT_OUTPUT)
    p.add_argument('--numerical',type=Path,default=zero.DEFAULT_OUTPUT)
    p.add_argument('--interface',type=Path,default=interface.ROOT)
    p.add_argument('--repair',type=Path,default=align.ROOT)
    p.add_argument('--output',type=Path,default=OUTPUT)
    p.add_argument('--cases',type=int,default=18)
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--no-generation',action='store_true',help='Score gold first token without sampling or decoding')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        run(device=args.device,base=args.baseline,numerical=args.numerical,
            interface_dir=args.interface,repair_root=args.repair,output=args.output,
            cases=args.cases,budget=args.max_new_tokens,generate=not args.no_generation,
            allow_download=args.allow_model_download)
        return 0
    except (AssertionError,RuntimeError,ValueError,KeyError,FileNotFoundError,OSError) as exc:
        print('HYBRID_CONSUMPTION_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2


if __name__=='__main__':sys.exit(main())
