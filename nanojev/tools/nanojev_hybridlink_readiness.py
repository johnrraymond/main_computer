#!/usr/bin/env python3
"""Read-only HybridLink depth x signed-residual x operand-magnitude readiness diagnostic.

The Qwen+Stage1 probe is candidate-retrieval (NOT autonomous arithmetic).  Qwen
numeric readout is Qwen+sidecar, not Qwen alone.  Never recommend Qwen fine
 tuning solely from either proxy.  Gold states NEVER enter generated rollouts.
"""
from __future__ import annotations

import ast
import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from tools import nanojev_hybridlink_frontier_repair as frontier
from tools import nanojev_hybridlink_latent_loop_cutover as loop
from tools import nanojev_hybridlink_latent_diagnostics as diag
from tools import nanojev_hybridlink_t00_curriculum_repair as t00
from tools import nanojev_hybridlink_frozen_detector_cutover as frozen
from tools import nanojev_qwen_numeric_dual_cutover as parent

SCHEMA = 'nanojev.hybridlink.component-readiness.v1'
BANDS = ((8,'0-8'),(64,'9-64'),(1024,'65-1024'),(32768,'1025-32768'),
         (1000000,'32769-1000000'),(math.inf,'over-1000000'))


def _emit(label,value):
    print(label+'='+json.dumps(value,sort_keys=True,default=str),flush=True)


def _leaves(node):
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
        return _leaves(node.left)+_leaves(node.right)
    if isinstance(node,ast.Constant) and type(node.value) is int:return [node.value]
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)):
        child=_leaves(node.operand)
        if len(child)!=1:raise ValueError('UNSUPPORTED_SIGNED_EXPRESSION')
        return [(-child[0] if isinstance(node.op,ast.USub) else child[0])]
    raise ValueError('UNSUPPORTED_EXPRESSION')


def verify_oracle(chain,stage1):
    """Verify exact recorded ONE-STEP reductions, not just final sums."""
    if len(chain)<2 or not chain[0].startswith('PROBLEM: '):
        raise ValueError('INVALID_REDUCTION_CHAIN')
    leaves=_leaves(ast.parse(chain[0][9:],mode='eval').body)
    if len(leaves)!=len(chain):raise ValueError('CHAIN_STEP_COUNT_MISMATCH')
    expected=stage1._rows_for(leaves,'readiness',0)
    observed=[(a,b) for a,b in zip(chain,chain[1:])]
    oracle=[(r['source'],r['target']) for r in expected]
    if observed!=oracle:raise ValueError('CHAIN_NOT_EXACT_REDUCTION')
    if not chain[-1].startswith('ANSWER: '):raise ValueError('CHAIN_MISSING_ANSWER')
    if int(chain[-1][8:].strip())!=sum(leaves):raise ValueError('CHAIN_WRONG_SUM')
    return {'answer':sum(leaves),'max_literal':max(abs(x) for x in leaves),
            'leaves':len(leaves),'steps':len(chain)-1}


def target_bands(operand_limit):
    """Return only magnitude bands inside the declared operand ceiling."""
    if operand_limit is None:
        return BANDS
    if operand_limit < 0:
        raise ValueError('READINESS_INVALID_OPERAND_LIMIT')
    result=[]
    previous=-1
    for ceiling,label in BANDS:
        if operand_limit <= previous:
            break
        if ceiling <= operand_limit:
            result.append((ceiling,label))
        else:
            bottom=previous+1
            if bottom <= operand_limit:
                result.append((operand_limit,f'{bottom}-{operand_limit}'))
            break
        previous=ceiling
    return tuple(result)


def band(value,operand_limit=None):
    return next(label for threshold,label in target_bands(operand_limit) if value<=threshold)


def cell_key(rung,features,operand_limit=None):
    return (rung,features['answer'],band(features['max_literal'],operand_limit))


def catalog(chains,stage1,max_rung,radius,seed,max_cases_per_rung,operand_limit=None):
    """Select actual recorded suffixes, stratified across signed residual and magnitude.

    Full-chain and suffix origins are explicitly distinguished; no fabricated
    T05..T08 targets. No two arbitrary equations are assumed equivalent.
    """
    by_rung={};available={};selected=[]
    rng=random.Random(seed)
    for rung in range(max_rung+1):
        pool={}
        for original in chains:
            if len(original)-1<rung+1:continue
            ch=tuple(original[-(rung+2):]); meta=verify_oracle(ch,stage1)
            # Every literal in EVERY ORIGINAL intermediate state must meet
            # the operand ceiling, not just the last suffix or final answer.
            if operand_limit is not None:
                states=(x for x in original if x.startswith('PROBLEM: '))
                if any(any(abs(v)>operand_limit for v in _leaves(
                    ast.parse(x[9:],mode='eval').body)) for x in states):
                    continue
            if abs(meta['answer'])>radius:continue
            if ch not in pool:
                pool[ch]={'rung':rung,'chain':list(ch),'full_chain':len(original)==len(ch),**meta}
            elif len(original)==len(ch):pool[ch]['full_chain']=True
        grouped=defaultdict(list)
        for case in pool.values():grouped[cell_key(rung,case,operand_limit)].append(case)
        for key,group in grouped.items():
            group.sort(key=lambda x:(not x['full_chain'],x['max_literal'],x['chain'][0]))
        available[rung]={key:len(group) for key,group in grouped.items()}
        keys=sorted(grouped,key=lambda k:(k[2],abs(k[1]),k[1]));rng.shuffle(keys)
        chosen=[]
        while keys and len(chosen)<max_cases_per_rung:
            left=[]
            for key in keys:
                if len(chosen)==max_cases_per_rung:break
                if grouped[key]:chosen.append(grouped[key].pop(0))
                if grouped[key]:left.append(key)
            keys=left
        by_rung[rung]=chosen
        selected.extend(chosen)
    return by_rung,available,selected


def coverage_matrix(available,selected,max_rung,radius,min_per_cell,operand_limit=None,*,origin="recorded"):
    observed=defaultdict(int)
    for case in selected:observed[cell_key(case['rung'],case,operand_limit)]+=1
    rows=[];summary={}
    for rung in range(max_rung+1):
        rung_rows=[]
        for residual in range(-radius,radius+1):
            for _,magnitude in target_bands(operand_limit):
                key=(rung,residual,magnitude)
                avail=available[rung].get(key,0);tested=observed[key]
                rec={'rung':rung,'residual':residual,'operand_band':magnitude,
                     'available_recorded':avail if origin=='recorded' else 0,
                     'available_generated':avail if origin=='generated' else 0,
                     'selected':tested,'target_cases':min_per_cell,
                     'coverage':('missing_from_deck' if origin=='recorded' else 'missing_generated') if avail==0 else
                         ('under_sampled' if tested<min_per_cell else
                          ('generated_sampled' if origin=='generated' else 'sampled'))}
                rows.append(rec);rung_rows.append(rec)
        summary[str(rung)]={'target_cells':len(rung_rows),
                'available_cells':sum((r['available_recorded']+r['available_generated'])>0 for r in rung_rows),
                'recorded_cells':sum(r['available_recorded']>0 for r in rung_rows),
                'generated_cells':sum(r['available_generated']>0 for r in rung_rows),
                'selected_cells':sum(r['selected']>0 for r in rung_rows),
                'adequately_sampled_cells':sum(r['selected']>=min_per_cell for r in rung_rows),
                'selected_cases':len([c for c in selected if c['rung']==rung]),
                'full_chain_cases':sum(c['full_chain'] for c in selected if c['rung']==rung),
                'suffix_only_cases':sum(not c['full_chain'] for c in selected if c['rung']==rung)}
    return rows,summary


def grade_components(qwen_numeric,detector_gold,generated,step_similarities,quality_floor,
                     *,qwen_transition=None,oracle_valid=True):
    """No causal blame for Qwen from a readout or retrieval proxy."""
    issues=[];next_actions=[]
    if not oracle_valid:
        return {'status':'blocked','issues':['ORACLE_INVALID'],'next_actions':['repair_dataset_oracle'],
                'qwen_finetuning_justified':False}
    if qwen_numeric is None:
        issues.append('QWEN_REDUCTION_UNVERIFIED')
        next_actions.append('measure_independent_qwen_reduction')
    elif qwen_numeric is False:
        issues.append('QWEN_NUMERIC_SIDECAR_DISAGREEMENT')
        next_actions.append('isolate_qwen_representation_from_numeric_head_before_finetuning')
    if qwen_transition is False:
        issues.append('STAGE1_TRANSITION_RETRIEVAL_FAILED')
        next_actions.append('inspect_qwen_plus_stage1_probe_not_autonomous_reduction')
    if detector_gold is False:
        issues.append('DETECTOR_MISCLASSIFIES_GOLD_STATES')
        next_actions.append('retrain_or_calibrate_detector_on_gold_states')
    if generated is False:
        issues.append('GENERATED_STOPPING_FAILURE')
        next_actions.append('inspect_detector_on_generated_vs_gold_states')
    if step_similarities is False:
        issues.append('GENERATED_LATENT_FIDELITY_FAILURE')
        next_actions.append('repair_hybridlink_transition_generator')
    if not issues:
        return {'status':'sampled_pathway_pass_not_arithmetic_certification',
                'issues':['AUTONOMOUS_QWEN_REDUCTION_NOT_TESTED'],
                'next_actions':['verify_autonomous_qwen_reduction_before_qwen_finetuning_decision'],
                'qwen_finetuning_justified':False}
    return {'status':'investigate','issues':issues,'next_actions':list(dict.fromkeys(next_actions)),
            'qwen_finetuning_justified':False}


def _text_reduction_prompt(source):
    """Declared raw text continuation control, NOT the Stage1 training protocol."""
    return ("Reduce exactly the leftmost addition once. Preserve all remaining "+
        "signed integer terms. Output exactly one PROBLEM: or ANSWER: line, no explanation.\n"+
        "Input: PROBLEM: 2+3\nOutput: ANSWER: 5\n"+
        "Input: PROBLEM: (2+3)+4\nOutput: PROBLEM: 5+4\n"+
        "Input: "+source+"\nOutput: ")


def _greedy_qwen_reduction(bb,tok,source,expected,device,max_tokens,max_new_tokens):
    """Untrained prompt-control probe; do NOT treat failure as proof Qwen can't reduce.

    No gold target in the prompt. No use of the HybridLink generator/detector.
    Full-prefix greedy decoding favors compatibility over speed; opt-in only.
    """
    import torch
    head=bb.get_output_embeddings()
    if head is None:return {'status':'unavailable_no_lm_head','correct':None,'completion':None}
    ids=tok(_text_reduction_prompt(source),add_special_tokens=False)['input_ids']
    if len(ids)>=max_tokens:return {'status':'unavailable_context','correct':None,'completion':None}
    context=list(ids);new=[]
    with torch.no_grad():
        for _ in range(max_new_tokens):
            token_ids=torch.tensor([context],device=device,dtype=torch.long)
            dec=bb.get_decoder()(input_ids=token_ids,
                        attention_mask=torch.ones_like(token_ids),return_dict=True,use_cache=False)
            logits=head(dec.last_hidden_state[:,-1,:].to(dtype=head.weight.dtype))
            nxt=int(logits.argmax(-1).item())
            if tok.eos_token_id is not None and nxt==tok.eos_token_id:break
            context.append(nxt);new.append(nxt)
            completion=tok.decode(new,skip_special_tokens=True)
            if '\n' in completion or len(context)>=max_tokens:break
    completion=tok.decode(new,skip_special_tokens=True).split('\n')[0].strip()
    return {'status':'observed_unvalidated_prompt_protocol','correct':completion==expected,
            'completion':completion,'target':expected}


def _correct_number(output,target):
    return output is not None and math.isfinite(output) and abs(output-target)<.5


def _summarize(records,minimum_cosine):
    n=len(records)
    def rate(key):return (sum(bool(x[key]) for x in records)/n) if n else None
    return {'cases':n,'oracle_valid':rate('oracle_valid'),
            'qwen_numeric_direct_exact':rate('qwen_direct'),
            'qwen_numeric_complex_exact':rate('qwen_complex'),
            'qwen_transition_retrieval_correct':rate('qwen_transition'),
            'gold_detector_correct':rate('detector_gold'),
            'hybridlink_correct_stop':rate('correct_stop'),
            'hybridlink_final_cosine_pass':rate('latent_quality'),
            'joint_pathway_pass':rate('joint_pass'),
            'autonomous_qwen_arithmetic_verified':False,
            'metric_scope':'selected_recorded_or_synthetic_cases_not_exhaustive'}


def advice(coverage,measurements):
    """Per-rung actionable evidence, without unsupported causal attribution."""
    if not measurements['cases']:
        return ['NO_RECORDED_CASES_IN_TARGET_RADIUS','GENERATE_AND_VERIFY_EXACT_TRAINING_CHAINS']
    output=[]
    if coverage['adequately_sampled_cells']<coverage['target_cells']:
        output.append('FILL_SIGNED_RESIDUAL_AND_OPERAND_BAND_COVERAGE')
    if measurements['qwen_transition_retrieval_correct'] is not None and measurements['qwen_transition_retrieval_correct']<1.:
        output.append('STAGE1_QWEN_PLUS_PROBE_TRANSITION_RETRIEVAL_WEAK')
    if (measurements['qwen_numeric_direct_exact'] is not None and
            (measurements['qwen_numeric_direct_exact']<1. or measurements['qwen_numeric_complex_exact']<1.)):
        output.append('DIAGNOSE_FROZEN_QWEN_VS_NUMERIC_SIDECAR_BEFORE_TUNING')
    if measurements['gold_detector_correct'] is not None and measurements['gold_detector_correct']<1.:
        output.append('TRAIN_OR_CALIBRATE_GOLD_STATE_DETECTOR')
    if measurements['hybridlink_correct_stop'] is not None and measurements['hybridlink_correct_stop']<1.:
        output.append('REPAIR_GENERATED_STOPPING_PATHWAY')
    if measurements['hybridlink_final_cosine_pass'] is not None and measurements['hybridlink_final_cosine_pass']<1.:
        output.append('REPAIR_GENERATED_LATENT_TRANSITION_FIDELITY')
    output.append('INDEPENDENT_QWEN_ARITHMETIC_VALIDATION_STILL_REQUIRED')
    return output



# An observed failure of a probe is not proof that an upstream frozen
# representation lacks the required information. Stage assignments below
# identify the *next component to inspect*, not permission to fine-tune it.
STAGES=(
    ('dataset','Dataset / reduction oracle'),
    ('qwen_backbone','Frozen Qwen independent reduction'),
    ('stage1_probe','Stage 1 transition projection'),
    ('numeric_direct','Direct numeric readout'),
    ('numeric_complex','Complex numeric readout'),
    ('detector_gold','Detector on gold states'),
    ('hybridlink_latent','HybridLink transition generator'),
    ('detector_generated','Detector on generated states'),
    ('final_output','Generated-state numeric output proxy'),
    ('emission','End-user emitted decimal answer'),
)

def _failure(rung,case_index,step,component,code,expected=None,observed=None,**details):
    return {'rung':rung,'case_index':case_index,'step':step,'component':component,
            'error_type':code,'expected':expected,'observed':observed,**details}


def classify_path(case,case_index,readouts,gold_states,teacher_transitions,
                  raw,grade,quality_floor,answer_threshold,scale,numeric,
                  *,qwen_text=None):
    """Locate failures on the same recorded chain without changing inference.

    Generated states are produced autonomously first and only graded here.
    Gold Qwen vectors are detector/readout controls, not fed into HybridLink.
    """
    events=[]
    rung=case['rung']; chain=case['chain']; expected_depth=len(chain)-1
    for hop,(source,target) in enumerate(zip(chain,chain[1:]),1):
        rd=readouts[hop-1]; gd=gold_states[hop-1]
        if not teacher_transitions[hop-1]:
            events.append(_failure(rung,case_index,hop,'stage1_probe','STAGE1_RETRIEVAL_MISMATCH',
                                   expected=target,observed='candidate_retrieval_failed',
                                   note='Candidate retrieval proxy; not Qwen autonomous reduction'))
        for field,component in [('direct','numeric_direct'),('complex','numeric_complex')]:
            if not rd[field+'_exact']:
                events.append(_failure(rung,case_index,hop,component,'GOLD_STATE_NUMERIC_READOUT_ERROR',
                    expected=rd['target'],observed=rd[field],source=source,
                    note='Qwen state plus sidecar. Isolate representation from readout before tuning Qwen'))
        if not gd['correct']:
            events.append(_failure(rung,case_index,hop,'detector_gold',
                'GOLD_DETECTOR_FALSE_NEGATIVE' if gd['expected_answer'] else 'GOLD_DETECTOR_FALSE_POSITIVE',
                expected=('ANSWER' if gd['expected_answer'] else 'CONTINUE'),
                observed=gd['p_answer'],gold_target=target))
    if qwen_text is not None and qwen_text.get('correct') is False:
        events.append(_failure(rung,case_index,1,'qwen_backbone','QWEN_TEXT_PROMPT_CONTROL_MISMATCH',
                   expected=chain[1],observed=qwen_text.get('completion'),
                   note='Prompt-dependent test, not proof that backbone training is necessary'))

    generated=raw['generated']
    for index in range(max(expected_depth,len(generated))):
        step=index+1
        if index>=len(generated):
            events.append(_failure(rung,case_index,step,'hybridlink_latent','MISSING_LATENT_TRANSITION',
                  expected=chain[step] if step<len(chain) else None,observed=raw['status']))
            continue
        hop=generated[index]
        hist=grade['history'][index]
        expected_answer=(step==expected_depth)
        is_answer=hop['p_answer']>=answer_threshold
        if is_answer != expected_answer and step<=expected_depth:
            events.append(_failure(rung,case_index,step,'detector_generated',
                'GENERATED_DETECTOR_FALSE_NEGATIVE' if expected_answer else 'GENERATED_DETECTOR_FALSE_POSITIVE',
                expected='ANSWER' if expected_answer else 'CONTINUE',observed=hop['p_answer'],
                note='Generated-state failure; root cause may be generator drift OR detector generalization'))
        if step<=expected_depth:
            cos=hist.get('target_vector_cosine')
            if cos is None or cos<quality_floor:
                events.append(_failure(rung,case_index,step,'hybridlink_latent',
                   'LATENT_FIDELITY_BELOW_FLOOR',expected=quality_floor,observed=cos,
                   gold_target=chain[step]))
            actual=hop.get('_vector')
            if actual is not None:
                value=parent.numeric_label(chain[step-1])
                # Already captured during autonomous rollout: do not repeat GPU
                # numeric passes in this diagnostic-only grading function.
                decoded=hop.get('readout') or {}
                for field,component in [('direct','final_output'),('complex','final_output')]:
                    if not _correct_number(decoded.get(field+'_number'),value):
                        events.append(_failure(rung,case_index,step,component,
                            'GENERATED_STATE_NUMERIC_ERROR',expected=value,
                            observed=decoded.get(field+'_number'),head=field,
                            note='Cannot blame generator until gold-state head is separately validated'))
    if not grade['correct']:
        cause={'ANSWER':'EARLY_OR_LATE_STOP', 'STALLED':'GENERATED_STALLED',
               'BUDGET_EXHAUSTED':'GENERATED_BUDGET_EXHAUSTED',
               'CONTEXT_EXHAUSTED':'GENERATED_CONTEXT_EXHAUSTED'}.get(raw['status'],'GENERATED_EXIT_FAILURE')
        if grade.get('early_false_stop'):cause='GENERATED_EARLY_STOP'
        if grade.get('late_exit'):cause='GENERATED_LATE_STOP'
        events.append(_failure(rung,case_index,grade['steps'],'detector_generated',cause,
            expected=expected_depth,observed=grade['steps'],status=raw['status']))
    return events


def component_matrix(observations,coverage,max_rung,operand_limit):
    """Emit every stage/rung even when untested; do not assign unproven blame."""
    rows=[]
    for rung in range(max_rung+1):
        cases=[c for c in observations if c['rung']==rung]
        n=len(cases)
        events=[e for c in cases for e in c.get('failures',[])]
        by_stage=defaultdict(set)
        for e in events:by_stage[e['component']].add(e['case_index'])
        counts={
          'dataset':(n,n),
          'qwen_backbone':(sum(c['qwen_text_reduction']['correct'] is not None for c in cases),
                           sum(c['qwen_text_reduction']['correct'] is True for c in cases)),
          'stage1_probe':(n,sum(c['qwen_transition'] for c in cases)),
          'numeric_direct':(n,sum(c['qwen_direct'] for c in cases)),
          'numeric_complex':(n,sum(c['qwen_complex'] for c in cases)),
          'detector_gold':(n,sum(c['detector_gold'] for c in cases)),
          'hybridlink_latent':(n,sum(c['latent_quality'] for c in cases)),
          'detector_generated':(n,sum(c['correct_stop'] for c in cases)),
          'final_output':(n,sum(c['final_number_exact'] for c in cases)),
          'emission':(0,0),
        }
        for component,label in STAGES:
            tested,passes=counts[component]
            if not tested:
                verdict='UNMEASURED'
            elif passes==tested:
                verdict='SAMPLED_PASS'
            else:
                verdict='OBSERVED_FAILURE'
            if component=='qwen_backbone':
                verdict='PROMPT_CONTROL_'+verdict if tested else 'UNMEASURED_NOT_QWEN_CERTIFIED'
            if component=='final_output':
                # Numeric-head agreement in latent space is not proof that the
                # end-user output protocol emits the exact decimal integer.
                verdict='PROXY_'+verdict if tested else 'UNMEASURED'
            rows.append({'rung':rung,'component':component,'stage':label,
                 'observed_cases':tested,'passed_cases':passes,
                 'case_failures':len(by_stage[component]),
                 'status':verdict,'selected_cases':n,
                 'covered_cells':coverage[str(rung)]['adequately_sampled_cells'],
                 'target_cells':coverage[str(rung)]['target_cells'],
                 'operand_limit':operand_limit})
    return rows


def next_training_stage(matrix):
    """Ordered recommendations; separate test-needed from train-needed."""
    decisions={}
    for row in matrix:
        rung=row['rung']; decisions.setdefault(str(rung),[])
        component=row['component']; verdict=row['status']
        if row['selected_cases']==0:
            continue
        if row['case_failures'] or 'FAILURE' in verdict:
            actions={
              'stage1_probe':'inspect Stage 1 transition projection/LoRA; retrieval is only a proxy',
              'numeric_direct':'verify direct numeric target/scale/sidecar; train direct head only if isolated',
              'numeric_complex':'verify complex numeric target/scale/sidecar; train complex head only if isolated',
              'detector_gold':'calibrate/retrain detector on gold intermediate vs final states',
              'hybridlink_latent':'repair HybridLink generator on failing transitions with acquired-case retention',
              'detector_generated':'compare generated vs gold detector behavior; distinguish drift from detector generalization',
              'final_output':'validate emitted decimal integer; isolate numeric head vs generator before training',
              'qwen_backbone':'investigate autonomous text-control reduction; do not tune Qwen from this alone',
            }
            if component in actions:
                decisions[str(rung)].append({'component':component,'action':actions[component],
                                              'evidence':verdict})
        if component=='emission':
            decisions[str(rung)].append({'component':'emission',
                'action':'no end-user answer emission is exercised by this diagnostic; add a dedicated exact text-output check',
                'evidence':'not_measured'})
        if component=='qwen_backbone' and row['observed_cases']==0:
            decisions[str(rung)].append({'component':'qwen_backbone',
                'action':'run independent Qwen reduction control before deciding whether backbone LoRA needs training',
                'evidence':'not_measured'})
    for rung in range(len(decisions)):
        if not any(row['rung']==rung and row['selected_cases'] for row in matrix):
            decisions[str(rung)].append({'component':'dataset','action':
                'generate exact full reduction chains within signed radius and operand ceiling',
                'evidence':'no_eligible_cases'})
    return decisions


def _validate(args):
    if getattr(args,'generate_zero_line',False) and args.radius>8:
        raise ValueError('READINESS_GENERATED_ZERO_LINE_RADIUS_MAX_8')
    if not 0<=args.max_rung<=11 or args.radius<0 or args.radius>1024 or getattr(args,'operand_limit',1024)<max(8,args.radius):
        raise ValueError('READINESS_INVALID_FRONTIER_TARGET')
    if args.max_cases_per_rung<1 or args.min_per_cell<1 or args.max_tokens<16 or (
         getattr(args,'generated_per_cell',1)<1 or getattr(args,'generated_per_cell',1)>8) or (
         args.qwen_text_probe_per_rung<0 or args.qwen_text_max_new_tokens<1):
        raise ValueError('READINESS_INVALID_SAMPLING')
    if not 0<args.min_gold_cosine<=1 or not 0<args.answer_threshold<=1:
        raise ValueError('READINESS_INVALID_THRESHOLDS')


def run(args):
    _validate(args)
    meta,ck=parent.load_parent(args)
    s=parent.stage1()
    rows=parent._rows(s,meta,args.split)
    chains=loop.chains_from_rows(rows,allow_partial=False)
    operand_limit=getattr(args,'operand_limit',1024)
    synthetic=bool(getattr(args,'generate_zero_line',False))
    if synthetic:
        from tools import nanojev_hybridlink_zero_line_deck as deck
        by_rung,available,selected=deck.build(max_rung=args.max_rung,radius=args.radius,
            operand_limit=operand_limit,per_cell=args.generated_per_cell,seed=args.seed,oracle=verify_oracle)
        origin='generated'
    else:
        by_rung,available,selected=catalog(chains,s,args.max_rung,args.radius,args.seed,args.max_cases_per_rung,operand_limit)
        origin='recorded'
    coverage,coverage_summary=coverage_matrix(available,selected,args.max_rung,args.radius,args.min_per_cell,operand_limit,origin=origin)
    report={'schema':SCHEMA,'stage':args.stage,'split':args.split,
            'target':{'max_rung':args.max_rung,'signed_residual_radius':args.radius,
                      'operand_bands':[name for _,name in target_bands(operand_limit)],
                      'operand_limit':operand_limit,
                      'min_cases_per_cell':args.min_per_cell},
            'depths_in_deck':sorted({len(c)-1 for c in chains}),
            'selected_cases':len(selected),'coverage':coverage_summary,
            'deck_origin':'synthetic_diagnostic_only' if synthetic else 'recorded_development_chains',
            'full_chain_test':synthetic,
            'generated_cases_per_cell':args.generated_per_cell if synthetic else 0,
            'deck_sha256':deck.fingerprint(selected) if synthetic else None,
            'training_overlap_verified':False if synthetic else None,
            'no_training':True,'gold_reference_never_fed_to_generated_rollout':True,
            'qwen_autonomous_reduction_proven':False,
            'qwen_finetuning_automatically_recommended':False}
    if args.stage=='readiness' and (not args.checkpoint or not args.detector_checkpoint):
        raise ValueError('READINESS_REQUIRES_CHECKPOINT_AND_DETECTOR')
    out=Path(args.output)
    diag.verify_paths(out,[Path(args.stage1_checkpoint).parent,Path(args.text_checkpoint).parent]+(
        [Path(args.detector_checkpoint).parent,Path(args.checkpoint).parent] if args.stage=='readiness' else [])+
        ([Path(args.cache_dir)] if args.cache_dir else []))
    out.mkdir(parents=True,exist_ok=True)
    with (out/'coverage.csv').open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=coverage[0].keys());writer.writeheader();writer.writerows(coverage)
    with (out/'selected_cases.json').open('w',encoding='utf8') as f:
        json.dump(selected,f,indent=2)
    if synthetic:
        with (out/'zero_line_deck.jsonl').open('w',encoding='utf8') as f:
            for case in selected:
                f.write(json.dumps(case,sort_keys=True)+'\n')
    if args.stage=='readiness-preview':
        report['status']='coverage_preview_only'
        _write_report(out,report)
        _emit('HYBRID_READINESS_PREVIEW',{'report':str(out/'readiness.json'),**report})
        return report
    import torch
    from tools import nanojev_qwen_zero_line_backbone as zero
    link_checkpoint=args.checkpoint
    prepared,num,det,dcfg=t00._prepare(args)
    args.checkpoint=link_checkpoint
    # t00._prepare returns the frozen detector separately, while the frontier
    # verifier expects it in its prepared context. Pass the *actual* loaded
    # detector: do not skip its checkpoint weight-by-weight drift check.
    item=frontier.verify_input(link_checkpoint,args,{**prepared,'det':det})
    bb=prepared['bb'];tok=prepared['tok'];bb.eval();num.eval();det.eval()
    link=loop.make_link(int(bb.config.hidden_size),int(bb.get_input_embeddings().embedding_dim)).to(args.device)
    link.load_state_dict(item['link'],strict=True);link.eval()
    scale=float(dcfg['scale'])
    report['checkpoint_training_status']={'training_schema':item.get('training_schema'),
        'status':item.get('status'),'accepted_rung':item.get('accepted_rung'),
        'reported_cycle':item.get('cycle'),
        'note':'Checkpoint claim is historical metadata; fresh measured readiness is separate'}
    names=sorted({text for ch in selected for text in ch['chain']})
    if not names:
        report['status']='no_selected_recorded_cases';_write_report(out,report)
        _emit('HYBRID_READINESS_RESULT',report);return report
    refs_vec,hit=frozen._load_or_encode(prepared['cache_dir'],'readiness-reference-v1',names,
              bb,tok,s,args,prepared['identity'])
    refs=dict(zip(names,refs_vec))
    # Stage1 transition-probe retrieval, using text-parent teacher vectors.
    # Teacher LoRA is restored to Qwen ONLY during teacher vector encoding, then
    # frozen Stage1 Qwen is restored exactly (finally). No generation oracle.
    targets=sorted({b for case in selected for b in case['chain'][1:]})
    text_cfg=json.loads((Path(args.text_checkpoint).parent/'run_config.json').read_text(encoding='utf8'))
    text_ck=torch.load(args.text_checkpoint,map_location='cpu',weights_only=True)
    from tools import nanojev_hybridlink_rung_reduction as base
    if text_ck.get('schema')!=text_cfg.get('schema') or text_ck.get('config_hash')!=base.digest(text_cfg):
        raise RuntimeError('READINESS_TEXT_TEACHER_PROVENANCE_DRIFT')
    original_lora=zero.lora_state(bb)
    zero.restore_lora(bb,text_ck['lora'])
    try:
        teacher_vec,teacher_cache_hit=frozen._load_or_encode(prepared['cache_dir'],
            'readiness-teacher-targets-v1',targets,bb,tok,s,args,
            {'text_sha256':parent.file_hash(args.text_checkpoint),
             'source_sha256':prepared['source_hash'],'deck_sha256':meta['deck_sha256']})
    finally:
        zero.restore_lora(bb,original_lora)
    for name,old in original_lora.items():
        if not torch.equal(zero.lora_state(bb)[name].cpu(),old.cpu()):
            raise RuntimeError('READINESS_QWEN_LORA_RESTORE_FAILED')
    with torch.no_grad():
        ctrl,projections=prepared['probe'](torch.stack([refs[x] for x in names]).to(args.device))
        probed={x:(int(c),v.detach().cpu()) for x,c,v in zip(names,ctrl.argmax(-1).tolist(),projections)}
        import torch.nn.functional as F
        teacher_norm=F.normalize(teacher_vec.float(),dim=-1)
        kind={'ANSWER':[],'PROBLEM':[]}
        for j,t in enumerate(targets):kind[t.split(':',1)[0]].append(j)
        # Candidate pool includes all selected target states, not one rung at a time.
        # Retrieval is artificial candidate matching, NOT Qwen solving arithmetic.
        def transition_correct(source,target):
            index,v=probed[source]
            actual_type='ANSWER' if index==1 else 'PROBLEM'
            expected_type=target.split(':',1)[0]
            if actual_type!=expected_type:return False
            ids=kind[actual_type]
            scores=F.cosine_similarity(v[None,:].float(),teacher_norm[ids],dim=-1)
            return targets[ids[int(scores.argmax())]]==target
        teacher_hash=parent.file_hash(args.text_checkpoint)
    observations=[]
    text_counts=defaultdict(int)
    for index,case in enumerate(selected,1):
        chain=case['chain'];expected=len(chain)-1
        # Precompute actual Qwen+numeric and detector-on-gold components for
        # EVERY one-step reduction, not merely terminal answer states.
        readouts=[];gold_actions=[];teacher_transitions=[]
        for before,after in zip(chain,chain[1:]):
            value=parent.numeric_label(before)
            h=refs[before].to(args.device)
            numeric=diag._readouts(num,h,scale,value)
            readouts.append({'target':value,'direct':numeric.get('direct_number'),
                   'complex':numeric.get('complex_number'),
                   'direct_exact':_correct_number(numeric.get('direct_number'),value),
                   'complex_exact':_correct_number(numeric.get('complex_number'),value)})
            gold_probability=diag._prob(det,refs[after].to(args.device).reshape(1,-1))
            expected_answer=after.startswith('ANSWER: ')
            gold_actions.append({'p_answer':gold_probability,'expected_answer':expected_answer,
                                 'correct':(gold_probability>=args.answer_threshold)==expected_answer})
            teacher_transitions.append(transition_correct(before,after))
        # CRITICAL: reference states are not passed into generation.
        raw=diag.build_trace(bb,tok,link,det,num,[chain[0]],args.device,args.max_tokens,
                 scale,args.answer_threshold,args.drift_threshold,args.stall_patience,
                 expected+args.overrun_steps)
        grade=frontier._record_grade(raw,chain,refs,num,scale,args.answer_threshold)
        cosines=[x['target_vector_cosine'] for x in grade['history'][:expected]]
        fidelity=(len(cosines)==expected and all(c is not None and c>=args.min_gold_cosine for c in cosines))
        final_quality=(grade['final_cosine'] is not None and grade['final_cosine']>=args.min_gold_cosine)
        rec={k:case[k] for k in ('rung','answer','max_literal','full_chain','chain')}
        rec.update({'band':band(case['max_literal'],operand_limit),'oracle_valid':True,
                    'qwen_direct':all(r['direct_exact'] for r in readouts),
                    'qwen_complex':all(r['complex_exact'] for r in readouts),
                    'qwen_transition':all(teacher_transitions),
                    'detector_gold':all(r['correct'] for r in gold_actions),
                    'correct_stop':bool(grade['correct']),
                    'latent_quality':bool(fidelity),
                    'final_quality':bool(final_quality),
                    'joint_pass':bool(grade['correct'] and fidelity),
                    'generated_status':grade['status'],'generated_steps':grade['steps'],
                    'generated_history':grade['history'],
                    'qwen_one_step':readouts,'gold_detector_steps':gold_actions,
                    'stage1_retrieval_steps':teacher_transitions})
        if text_counts[case['rung']] < args.qwen_text_probe_per_rung:
            rec['qwen_text_reduction']=_greedy_qwen_reduction(bb,tok,chain[0],chain[1],
                  args.device,args.max_tokens,args.qwen_text_max_new_tokens)
            text_counts[case['rung']]+=1
        else:
            rec['qwen_text_reduction']={'status':'not_sampled','correct':None}
        rec['diagnosis']=grade_components(rec['qwen_direct'] and rec['qwen_complex'],
                    rec['detector_gold'],rec['correct_stop'],rec['latent_quality'],
                    args.min_gold_cosine,qwen_transition=rec['qwen_transition'])
        rec['failures']=classify_path(case,index,readouts,gold_actions,teacher_transitions,
                   raw,grade,args.min_gold_cosine,args.answer_threshold,scale,num,
                   qwen_text=rec['qwen_text_reduction'])
        # This is a proxy for numerical correctness, not emitted text; the
        # readout is observational and does not control HybridLink stopping.
        generated_final=(raw['generated'][-1] if len(raw['generated'])==expected else None)
        if generated_final is not None and generated_final.get('_vector') is not None:
            final_readout=generated_final.get('readout') or {}
            rec['generated_final_readout']={k:final_readout.get(k) for k in ('direct_number','complex_number')}
            rec['final_number_exact']=bool(grade['correct']) and all(
                 _correct_number(final_readout.get(key),case['answer'])
                 for key in ('direct_number','complex_number'))
        else:
            rec['generated_final_readout']=None
            rec['final_number_exact']=False
        observations.append(rec)
        _emit('HYBRID_READINESS_PROGRESS',{'done':index,'total':len(selected),
              'rung':case['rung'],'band':rec['band'],'residual':case['answer'],
              'qwen_numeric_both':rec['qwen_direct'] and rec['qwen_complex'],
              'gold_detector':rec['detector_gold'], 'stop':rec['correct_stop'],
              'latent':rec['latent_quality']})
    per_rung={str(r):_summarize([x for x in observations if x['rung']==r],args.min_gold_cosine)
              for r in range(args.max_rung+1)}
    actions={str(r):advice(coverage_summary[str(r)],per_rung[str(r)])
             for r in range(args.max_rung+1)}
    per_band={str(r):{name:_summarize([x for x in observations if x['rung']==r and x['band']==name],args.min_gold_cosine)
              for _,name in target_bands(operand_limit)} for r in range(args.max_rung+1)}
    per_residual={str(r):{str(delta):_summarize([x for x in observations if x['rung']==r and x['answer']==delta],args.min_gold_cosine)
              for delta in range(-args.radius,args.radius+1)} for r in range(args.max_rung+1)}
    matrix=component_matrix(observations,coverage_summary,args.max_rung,operand_limit)
    training_actions=next_training_stage(matrix)
    failures=[e for x in observations for e in x['failures']]
    with (out/'training_matrix.csv').open('w',encoding='utf8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(matrix[0]));writer.writeheader();writer.writerows(matrix)
    with (out/'failure_events.csv').open('w',encoding='utf8',newline='') as f:
        cols=('rung','case_index','step','component','error_type','expected','observed','source','gold_target','note')
        writer=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');writer.writeheader()
        for event in failures:writer.writerow(event)
    report.update({'status':'diagnostic_only','reference_cache_hit':hit,'teacher_cache_hit':teacher_cache_hit,
        'qwen_text_probe_per_rung':args.qwen_text_probe_per_rung,
        'qwen_text_observed':sum(x['qwen_text_reduction']['correct'] is not None for x in observations),
        'qwen_text_correct':sum(x['qwen_text_reduction']['correct'] is True for x in observations),
        'text_parent_sha256':teacher_hash,
        'evidence':{'qwen_stage1_transition':'supervised probe retrieval against known teacher-vector candidates, NOT generation',
           'qwen_numerical':'Qwen representation + separately trained numeric head; exact integer within 0.5, NOT Qwen-alone proof',
           'detector':'frozen Answer Detector on separately Qwen-encoded gold states',
           'hybridlink':'autonomous generated latent loop, gold references used for grading ONLY',
           'arithmetic':'exact Python oracle certifies targets, NOT neural arithmetic',
           'qwen_alone_reduction':'Raw text-only Qwen one-hop test is opt-in and prompt-protocol-dependent; a failure cannot establish Qwen lacks latent reduction ability'},
        'per_rung':per_rung,'per_band':per_band,'per_residual':per_residual,
        'component_matrix':matrix,'next_training_stages':training_actions,
        'failure_counts':dict((code,sum(e['error_type']==code for e in failures))
                              for code in sorted({e['error_type'] for e in failures})),
        'recommended_next_experiments':actions,
        'cases':observations})
    _write_report(out,report)
    summary={'schema':SCHEMA,'status':'diagnostic_only','report':str(out/'readiness.json'),
             'per_rung':per_rung,'coverage':coverage_summary,
             'recommended_next_experiments':actions,
             'qwen_finetuning_automatically_recommended':False,
             'autonomous_qwen_reduction_measured':bool(args.qwen_text_probe_per_rung),
             'component_isolation_required_before_qwen_finetuning':True,
             'training_matrix':str(out/'training_matrix.csv'),
             'failure_events':str(out/'failure_events.csv'),
             'next_training_stages':training_actions}
    _emit('HYBRID_READINESS_COMPONENT_MATRIX',{'stages':len(STAGES),'rungs':args.max_rung+1,
          'failure_events':len(failures),'matrix':str(out/'training_matrix.csv'),
          'next_training_stages':training_actions})
    _emit('HYBRID_READINESS_RESULT',summary)
    return report


def _write_report(out,result):
    path=Path(out)/'readiness.json';temporary=path.with_suffix('.json.partial')
    try:temporary.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    # Human-readable action matrix, including the reason for NOT blaming Qwen.
    lines=['# HybridLink component readiness\n',f"Split: {result['split']}; stage: {result['stage']}",
      f"Target: T00–T{result['target']['max_rung']:02d}, signed residual ±{result['target']['signed_residual_radius']}",
      ('Generated read-only full-chain evaluation deck; no checkpoint was trained or promoted.'
       if result.get('full_chain_test') else
       'No checkpoint was trained or promoted. Each result covers selected recorded chains, not a full rectangle.'),
      'IMPORTANT: Stage1 retrieval and Qwen+numeric sidecar do NOT certify autonomous Qwen arithmetic.',
      'A readout failure does NOT by itself prove Qwen fine-tuning is necessary.\n',
      '| Rung | Cases | Covered cells | Target cells | Qwen+numeric direct | Qwen+numeric complex | Stage1 retrieval | Gold detector | Generated stop | Latent per-step |',
      '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for rung,c in result['coverage'].items():
        perf=result.get('per_rung',{}).get(rung,{})
        def rate(k):
            x=perf.get(k);return '?' if x is None else f'{x:.0%}'
        lines.append(f"| T{int(rung):02d} | {c['selected_cases']} | {c['available_cells']} | {c['target_cells']} | "+
            ' | '.join((rate('qwen_numeric_direct_exact'),rate('qwen_numeric_complex_exact'),
                rate('qwen_transition_retrieval_correct'),rate('gold_detector_correct'),
                rate('hybridlink_correct_stop'),rate('hybridlink_final_cosine_pass')))+' |')
    if result.get('recommended_next_experiments'):
        lines.append('\n## Per-rung next experiment')
        for rung,acts in result['recommended_next_experiments'].items():
            lines.append(f"T{int(rung):02d}: "+', '.join(acts))
    if result.get('component_matrix'):
        lines.extend(['','## Stage-by-stage training diagnosis',
            '| Rung | Component | Cases | Pass | Status |', '|---|---|---:|---:|---|'])
        for r in result['component_matrix']:
            lines.append(f"| T{r['rung']:02d} | {r['stage']} | {r['observed_cases']} | {r['passed_cases']} | {r['status']} |")
        lines.extend(['','## Suggested next stage by rung'])
        for rung,items in result['next_training_stages'].items():
            lines.append(f"T{int(rung):02d}: "+'; '.join(item['action'] for item in items))
    lines.extend(['','Read `coverage.csv` for every signed-residual × operand-band cell and `readiness.json` for exact failures.',
      'Per-step failure_events.csv and training_matrix.csv locate what should be inspected at each rung.',
      'For generated runs, zero_line_deck.jsonl contains all oracle-verified full chains (diagnostic only).',
      'Training decisions must be based on independently isolating the failing component; no automatic Qwen fine-tune decision.'])
    (Path(out)/'readiness.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
