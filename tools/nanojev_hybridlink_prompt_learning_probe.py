#!/usr/bin/env python3
"""Read-only Qwen HybridLink prompt-vs-learned-answer diagnostic.

A bounded diagnostic on the existing Qwen3 HybridAgent checkpoints. This is
NOT QW5 Stage 2; QW5 feedback remains disabled. TEST is never accessed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path
import sys

SCHEMA = 'nanojev.hybridlink.prompt-vs-learned-answer.v3'
RUNGS = ('Z0','Z1','Z2','R0','R1','R2','B0',*(f'T{i:02d}' for i in range(9)))
DEFAULT_OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'hybridlink_prompt_learning_probe_v3'


def _payoffs(anchor: int, delta: int, variant: int):
    # HH - DH = +1 and HD - DD = -1, so Hawk - Dove = a-b.
    a = anchor
    b = anchor - delta
    assert a > 0 and b > 0
    hh, dh = (2 + variant), (1 + variant)
    hd, dd = (1 + variant), (2 + variant)
    x, y = a*hh + b*hd, a*dh + b*dd
    assert x-y == delta
    return a,b,hh,hd,dh,dd,x,y


def question(rung: str, anchor: int, delta: int, style: str, *, reverse: bool=False, variant: int=0):
    """Return prompt + signed oracle answer; never interpolate oracle answer in text.

    Tier prompts are scaled *diagnostic analogues* of historical T00-T08;
    they are not the historical million-encounter suite or a certification.
    """
    assert rung in RUNGS and anchor >= 2*max(1,abs(delta))
    assert style in ('direct','scaffold')
    a,b=anchor, -anchor+delta
    if rung in ('Z0','Z1','Z2','R0'):
        expr = (f'A position starts at {a} and changes by {b}. What is its signed final position?'
                if rung!='R0' else f'Compute the signed sum {a} + ({b}).')
        rule = f'Use signed addition. The two inputs are {a} and {b}; compute A+B exactly.'
        target=delta
    elif rung == 'R1':
        first, second=anchor+delta,anchor
        expr=f'Compute the signed difference: {first} minus {second}.'
        rule=f'The first value is {first}; the second is {second}. Compute first minus second, including the sign.'
        target=delta
    elif rung == 'R2':
        first, second=anchor+delta,anchor
        target=-delta if reverse else delta
        expr=(f'Compute {second} minus {first}.' if reverse else f'Compute {first} minus {second}.')
        rule=(f'The same two totals are {first} and {second}. Direction matters: '
              + (f'second ({second}) minus first ({first}).' if reverse else f'first ({first}) minus second ({second}).'))
    else:
        a,b,hh,hd,dh,dd,x,y=_payoffs(anchor,delta,variant)
        target=delta
        if rung=='B0':
            expr=f'Compare Hawk total {x} against Dove total {y}. Return signed Hawk-minus-Dove difference.'
        elif rung=='T00':
            expr=f'(ARGMAX {x} {y})\nReturn the signed Hawk-minus-Dove difference, not the winning strategy.'
        elif rung in ('T01','T02'):
            if rung=='T01':
                expr=f'(ARGMAX {x} (ADD (MUL {a} {dh}) (MUL {b} {dd})))'
            else:
                expr=f'(ARGMAX (ADD (MUL {a} {hh}) (MUL {b} {hd})) {y})'
            expr+='\nReturn the signed Hawk-minus-Dove difference, not the winning strategy.'
        elif rung in ('T03','T04','T05','T06'):
            terms=((a,hh),(b,hd),(a,dh),(b,dd))
            if rung=='T04': terms=(terms[1],terms[0],terms[2],terms[3])
            if rung=='T05': terms=(terms[0],terms[1],terms[3],terms[2])
            def mul(z):return f'(MUL {z[0]} {z[1]})' if rung!='T06' else f'(MUL {z[1]} {z[0]})'
            expr=f'(ARGMAX (ADD {mul(terms[0])} {mul(terms[1])}) (ADD {mul(terms[2])} {mul(terms[3])}))'
            expr+='\nReturn the signed Hawk-minus-Dove difference, not the winning strategy.'
        else:
            expr=(f'In Hawk-Dove, the opponent plays Hawk {a} times and Dove {b} times. '
                  f'Your Hawk payoffs are {hh} against Hawk and {hd} against Dove; '
                  f'your Dove payoffs are {dh} against Hawk and {dd} against Dove. '
                  'What is the signed advantage of choosing Hawk over Dove across all encounters?')
            if rung=='T07':
                expr+='\n(Compare the two sums of count-times-payoff products.)'
        if rung not in ('B0','T00','T01','T02','T07','T08'):
            expr=(f'Payoff bindings: HAWK_HAWK_PAYOFF={hh}; HAWK_DOVE_PAYOFF={hd}; '
                  f'DOVE_HAWK_PAYOFF={dh}; DOVE_DOVE_PAYOFF={dd}.\n'+expr)
        rule=(f'Use {a} Hawk encounters and {b} Dove encounters. '
              f'Hawk total = ({a}*{hh})+({b}*{hd}); '
              f'Dove total = ({a}*{dh})+({b}*{dd}). '
              'Subtract the Dove total from the Hawk total, retaining the sign.')
    # Both arms preserve the actual FORMAT/SFT completion contract. The
    # scaffold changes only an answer-free instruction *inside* the question,
    # never the learned 'Reasoning:' generation prefix.
    prompt = (f'Question: {expr}\n'
              + (f'Instruction: {rule}\n' if style == 'scaffold' else '')
              + 'Reasoning: ')
    assert prompt.endswith('\nReasoning: ')
    assert 'Response:' not in prompt and 'Method:' not in prompt
    assert f'Answer: {target}' not in prompt
    return prompt,target


def make_rows(rungs,lo,hi,scales,styles,seed):
    assert rungs and all(r in RUNGS for r in rungs)
    assert 0 <= lo <= hi and hi <= 4_000_000
    assert scales and all(2*max(1,hi)<=s<=4_000_000 for s in scales)
    assert styles and all(s in ('direct','scaffold') for s in styles)
    # A minimum paired diagnostic: exact zero, both small +/- and band endpoints.
    magnitudes = [0]
    if lo==0:
        if hi>0: magnitudes.extend([-1,1])
    else:
        magnitudes.extend([-lo,lo])
    if hi>max(1,lo):magnitudes.extend([-hi,hi])
    ds=tuple(dict.fromkeys(magnitudes))
    rows=[]
    for rung in rungs:
        for scale in scales:
            for delta in ds:
                for reverse in ((False,True) if rung=='R2' and delta else (False,)):
                    for style in styles:
                        prompt,target=question(rung,scale,delta,style,reverse=reverse,variant=(seed+scale)%3)
                        identity=f'{rung}:{scale}:{delta}:{int(reverse)}'
                        rows.append({'id':identity,'rung':rung,'anchor':scale,'delta':delta,
                            'band':f'{lo}-{hi}','style':style,'reverse':reverse,
                            'sign':'positive' if target>0 else 'negative' if target<0 else 'zero',
                            'prompt':prompt,'expected':target})
    return rows


# A generated second question must never supply credit for the first question.
# A correct first answer followed by additional text is counted as correct
# numerically but fails the separate, single-answer termination contract.
_NEW_QUESTION = re.compile(r'(?m)^[ \t]*Question:[ \t]*')
_FORMAL_ANSWER = re.compile(r'(?m)^[ \t]*Answer:[ \t]*([+-]?\d+)\b')
_LEADING_INTEGER = re.compile(r'^\s*([+-]?\d+)[ \t]*(?:\r?\n|$)')


def parse_completion(text, *, budget_exhausted=False):
    """Score only the first question's completion, without an answer oracle.

    `completion` is currently capped to 300 characters by the shared read-only
    decoder. If that window is full, report the limitation rather than claiming
    that later unobserved output obeyed the termination contract.
    """
    generated_question = _NEW_QUESTION.search(text)
    primary = text[:generated_question.start()] if generated_question else text
    formal = list(_FORMAL_ANSWER.finditer(primary))
    leading = _LEADING_INTEGER.match(primary)
    formal_answer = int(formal[0].group(1)) if formal else None
    leading_answer = int(leading.group(1)) if leading else None
    first = formal_answer if formal_answer is not None else leading_answer
    kind = 'answer_label' if formal_answer is not None else ('leading_integer' if leading_answer is not None else 'none')
    first_match = formal[0] if formal else leading
    trailing_primary = bool(primary[first_match.end():].strip()) if first_match else False
    question_after_answer = bool(generated_question and first is not None)
    extra = trailing_primary or question_after_answer
    multiple = len(formal) > 1
    window_limit = len(text) >= 300
    violations = []
    if generated_question: violations.append('generated_new_question')
    if multiple: violations.append('multiple_answer_labels')
    if extra: violations.append('extra_output_after_answer')
    if first is None: violations.append('missing_completed_answer')
    if window_limit: violations.append('completion_window_limit_reached')
    if budget_exhausted: violations.append('generation_token_budget_reached')
    clean = first is not None and not violations
    return {'strict_answer': formal_answer, 'leading_integer': leading_answer,
            'first_completed_answer': first, 'first_answer_kind': kind,
            'single_answer_terminated': clean,
            'generated_new_question': bool(generated_question),
            'extra_output_after_answer': extra,
            'multiple_answer_labels': multiple,
            'completion_window_limit_reached': window_limit,
            'generation_token_budget_reached': bool(budget_exhausted),
            'format_violations': violations}


def summarize(records):
    groups=defaultdict(list)
    for r in records:
        for key in (('total',r['configuration'],r['depth'],r['control'],r['style']),
                    ('rung',r['configuration'],r['rung'],r['depth'],r['control'],r['style']),
                    ('sign',r['configuration'],r['sign'],r['depth'],r['control'],r['style'])):
            groups[key].append(r)
    scores=[]
    for key,rs in sorted(groups.items()):
        good=sum(r['strict_answer']==r['expected'] for r in rs)
        leading=sum(r['leading_integer']==r['expected'] for r in rs)
        first=sum(r['first_completed_answer']==r['expected'] for r in rs)
        terminated=sum(r['single_answer_terminated'] and r['first_completed_answer']==r['expected'] for r in rs)
        scores.append({'slice':list(key),'n':len(rs),'strict_correct':good,
                       'strict_accuracy':round(good/len(rs),5),'leading_correct':leading,
                       'strict_parseable':sum(r['strict_answer'] is not None for r in rs),
                       'first_completed_correct':first,
                       'first_completed_accuracy':round(first/len(rs),5),
                       'terminated_correct':terminated,
                       'terminated_accuracy':round(terminated/len(rs),5),
                       'single_answer_terminated_count':sum(r['single_answer_terminated'] for r in rs),
                       'generated_new_question_count':sum(r['generated_new_question'] for r in rs),
                       'extra_output_after_answer_count':sum(r['extra_output_after_answer'] for r in rs),
                       'multiple_answer_labels_count':sum(r['multiple_answer_labels'] for r in rs),
                       'completion_window_limit_reached_count':sum(r['completion_window_limit_reached'] for r in rs),
                       'generation_token_budget_reached_count':sum(r.get('generation_token_budget_reached',False) for r in rs)})
    return scores


def paired_comparisons(records):
    """Paired tests isolate marker entry, blank positions, and learned feedback.

    Match the exact configuration, problem, prompt style and (for latent-to-
    latent comparisons) depth. No unmatched records contribute to a delta.
    """
    indexed={(r['configuration'],r['id'],r['style'],r['depth'],r['control']):r
             for r in records}
    available=sorted({r['depth'] for r in records if r['depth']>0})
    # spec: (comparison name, condition A, condition B, compare across styles)
    comparisons=[('marker_only_vs_text_only',('direct',0,'marker_only'),('direct',0,'text_only'),False)]
    for depth in available:
        comparisons.extend((
            (f'zero_{depth}_vs_marker_only', ('direct',depth,'zero_latent'),('direct',0,'marker_only'),False),
            (f'zero_{depth}_vs_text_only', ('direct',depth,'zero_latent'),('direct',0,'text_only'),False),
            (f'intact_{depth}_vs_zero_{depth}',('direct',depth,'intact'),('direct',depth,'zero_latent'),False),
            (f'intact_{depth}_vs_marker_only',('direct',depth,'intact'),('direct',0,'marker_only'),False),
            (f'intact_{depth}_vs_text_only',('direct',depth,'intact'),('direct',0,'text_only'),False),
        ))
    comparisons.extend((
        ('scaffold_vs_direct_text_only',('scaffold',0,'text_only'),('direct',0,'text_only'),True),
        ('scaffold_vs_direct_marker_only',('scaffold',0,'marker_only'),('direct',0,'marker_only'),True),
    ))
    for depth in available:
        comparisons.extend((
            (f'scaffold_vs_direct_zero_{depth}',('scaffold',depth,'zero_latent'),('direct',depth,'zero_latent'),True),
            (f'scaffold_vs_direct_intact_{depth}',('scaffold',depth,'intact'),('direct',depth,'intact'),True),
        ))
    result=[]
    for name, a, b, cross_style in comparisons:
        groups=defaultdict(lambda: {'n':0,'a_only':0,'b_only':0,'both_correct':0,'both_wrong':0,
             'first_a_only':0,'first_b_only':0,'first_both_correct':0,'first_both_wrong':0,
             'terminated_a_only':0,'terminated_b_only':0,
             'terminated_both_correct':0,'terminated_both_wrong':0})
        # For matched same-style controls, compare direct and scaffold independently.
        for (config,ident,style,depth,control),x in indexed.items():
            target_a=(a if cross_style else (style,a[1],a[2]))
            target_b=(b if cross_style else (style,b[1],b[2]))
            if (style,depth,control)!=target_a:continue
            y=indexed.get((config,ident,*target_b))
            if y is None:continue
            assert x['expected']==y['expected'],'HYBRID_ABLATION_PAIR_TARGET_MISMATCH'
            key=(config,x['rung'],'paired_styles' if cross_style else style)
            g=groups[key];g['n']+=1
            for prefix,metric in (('', 'strict_answer'),('first_', 'first_completed_answer')):
                ac=x[metric]==x['expected'];bc=y[metric]==y['expected']
                g[prefix+('a_only' if ac and not bc else 'b_only' if bc and not ac else
                           'both_correct' if ac else 'both_wrong')]+=1
            ac=x['single_answer_terminated'] and x['first_completed_answer']==x['expected']
            bc=y['single_answer_terminated'] and y['first_completed_answer']==y['expected']
            g['terminated_'+('a_only' if ac and not bc else 'b_only' if bc and not ac else
                             'both_correct' if ac else 'both_wrong')]+=1
        for key,count in sorted(groups.items()):
            result.append({'comparison':name,'configuration':key[0],
                           'rung':key[1],'style':key[2],**count})
    return result


def inference_conditions(depths):
    """Exactly one text/marker control, plus same-depth zero and learned arms."""
    assert 0 in depths and len(depths)==len(set(depths)),'HYBRID_ABLATION_DEPTHS_REPEAT_OR_MISSING_ZERO'
    assert set(depths)<={0,1,2,4},'HYBRID_ABLATION_UNSUPPORTED_DEPTH'
    return ((0,'text_only'),(0,'marker_only'),
            *((d,ctrl) for d in depths if d>0 for ctrl in ('zero_latent','intact')))


def decode_condition(model,tokenizer,ids,depth,control,budget):
    """Separate marker tokens from embeddings without touching checkpoint state.

    marker_only consumes actual <latent></latent> marker token IDs, *without*
    introducing any latent positions, invoking HybridLink, or injecting gold
    numerical information. Zero/intact enter the exact same rollout path.
    """
    import torch
    from tools import nanojev_qwen_hybridagent_paired_diagnostic as paired
    if control=='text_only':
        assert depth==0
        return paired.decode_once(model,tokenizer,ids,0,budget)
    if control=='marker_only':
        assert depth==0 and model.start_id!=model.end_id,'HYBRID_ABLATION_BAD_MARKERS'
        markers=torch.tensor([[model.start_id,model.end_id]],device=ids.device,dtype=ids.dtype)
        marker_ids=torch.cat((ids,markers),dim=1)
        return paired.decode_once(model,tokenizer,marker_ids,0,budget)
    assert depth in (1,2,4) and control in ('intact','zero_latent'),'HYBRID_ABLATION_UNKNOWN_CONTROL'
    return paired.decode_once(model,tokenizer,ids,depth,budget,corrupt=control=='zero_latent')


def _source_setup(device,baseline,backbone,activation_dir,format_dir,joint_dir,include_joint,allow_download):
    import torch
    from tools import nanojev_qwen_hybridagent as hybrid
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    from tools import nanojev_qwen_hybridagent_format_activation as format_train
    from tools import nanojev_qwen_hybridagent_self_conditioned as joint
    # Match the existing paired diagnostic's source/format provenance contract.
    hashes=format_train.source_hashes(baseline,backbone)
    format_train.assert_provenance(baseline,backbone,hashes)
    cfg=json.loads((format_dir/'run_config.json').read_text(encoding='utf8'))
    result=json.loads((format_dir/'result.json').read_text(encoding='utf8'))
    assert cfg['schema']==format_train.SCHEMA and cfg['sources']==hashes,'HYBRID_PROBE_FORMAT_SOURCE_DRIFT'
    assert result['status']=='FORMAT_ACTIVATION_SMOKE_COMPLETED_UNCERTIFIED','HYBRID_PROBE_FORMAT_NOT_COMPLETE'
    fmt=torch.load(format_dir/'best_format_lora.pt',map_location='cpu',weights_only=True)
    assert fmt['schema']==format_train.SCHEMA and fmt['config_hash']==zero.fingerprint(cfg),'HYBRID_PROBE_FORMAT_HASH_DRIFT'
    hc=json.loads((activation_dir/'run_config.json').read_text(encoding='utf8'))
    hr=json.loads((activation_dir/'result.json').read_text(encoding='utf8'))
    assert hc['schema']==activation.SCHEMA and hr['status']=='PASS_MECHANICS_ONLY','HYBRID_PROBE_HEAD_NOT_COMPLETE'
    assert Path(hc['baseline_dir']).resolve()==baseline.resolve(),'HYBRID_PROBE_HEAD_SOURCE_DRIFT'
    head=torch.load(activation_dir/'best_head.pt',map_location='cpu',weights_only=True)
    assert head['schema']==activation.SCHEMA and head['training_stage']=='activation_only','HYBRID_PROBE_HEAD_HASH_DRIFT'
    lo_cfg=json.loads((backbone/'run_config.json').read_text(encoding='utf8'))
    lo=torch.load(backbone/'best_lora.pt',map_location='cpu',weights_only=True)
    assert lo['schema']==zero.SCHEMA,'HYBRID_PROBE_BACKBONE_SCHEMA_DRIFT'
    configs=[('numerical_pre_format',lo['lora'],head['head']),('format_activated',fmt['lora'],head['head'])]
    source_dirs=[baseline,backbone,activation_dir,format_dir]
    if include_joint:
        jc=json.loads((joint_dir/'run_config.json').read_text(encoding='utf8'))
        jr=json.loads((joint_dir/'result.json').read_text(encoding='utf8'))
        assert jc['schema']==joint.SCHEMA and jr['status']=='SELF_CONDITIONED_SMOKE_COMPLETED_UNCERTIFIED','HYBRID_PROBE_JOINT_INCOMPLETE'
        assert jc['sources']==joint.source_fingerprints(baseline,activation_dir,backbone),'HYBRID_PROBE_JOINT_DRIFT'
        js=torch.load(joint_dir/'best.pt',map_location='cpu',weights_only=True)
        assert js['schema']==joint.SCHEMA and js['config_sha256']==joint.sha_config(jc),'HYBRID_PROBE_JOINT_HASH_DRIFT'
        configs.append(('previous_joint',js['lora'],js['head']))
        source_dirs.append(joint_dir)
    fingerprints={str(p.resolve()):activation.file_hash(p) for d in source_dirs for p in d.iterdir() if p.is_file()}
    model,tokenizer,details=hybrid.load_qwen(device=device,allow_download=allow_download,
      precision='bfloat16' if device=='cuda' and torch.cuda.is_bf16_supported() else 'auto')
    hybrid.load_adapter(baseline,model,details)
    assert details['base_model']==hybrid.MODEL_ID and details['revision']==hybrid.MODEL_REVISION,'HYBRID_PROBE_QWEN_MODEL_DRIFT'
    model.backbone.requires_grad_(False);model.head.requires_grad_(False)
    zero.install_lora(model.backbone,int(lo_cfg['rank']),float(lo_cfg['alpha']),
                      required_layers=int(model.backbone.config.num_hidden_layers))
    model.head.to(device=device,dtype=torch.float32)
    frozen_hash=zero.frozen_backbone_sha256(model.backbone)
    return model,tokenizer,configs,source_dirs,fingerprints,frozen_hash


def run(*,device='cuda',rungs=('Z0','R2','T08'),band=(0,4),scales=(8,),
        styles=('direct','scaffold'),depths=(0,1,2,4),max_new_tokens=48,
        baseline=None,backbone=None,activation_dir=None,format_dir=None,joint_dir=None,
        include_joint=False,output=DEFAULT_OUTPUT,seed=71,allow_download=False,emit_cases=True):
    import torch
    from tools import nanojev_qwen_hybridagent as hybrid
    from tools import nanojev_qwen_zero_line_backbone as zero
    from tools import nanojev_qwen_hybridagent_activation as activation
    from tools import nanojev_qwen_hybridagent_self_conditioned as joint
    from tools import nanojev_qwen_hybridagent_format_activation as format_train
    baseline=Path(baseline or hybrid.DEFAULT_OUTPUT)
    backbone=Path(backbone or zero.DEFAULT_OUTPUT)
    activation_dir=Path(activation_dir or activation.DEFAULT_RUN)
    format_dir=Path(format_dir or format_train.OUTPUT)
    joint_dir=Path(joint_dir or joint.OUTPUT)
    output=Path(output)
    conditions=inference_conditions(depths)
    source_dirs=[baseline,backbone,activation_dir,format_dir]+([joint_dir] if include_joint else [])
    assert all(output.resolve()!=d.resolve() and output.resolve() not in d.resolve().parents
        and d.resolve() not in output.resolve().parents for d in source_dirs),'HYBRID_PROBE_OUTPUT_OVERLAPS_SOURCE'
    assert not output.exists() or not any(output.iterdir()),'HYBRID_PROBE_OUTPUT_EXISTS'
    rows=make_rows(rungs,band[0],band[1],scales,styles,seed)
    assert len(rows)<=128,'HYBRID_PROBE_TOO_MANY_CASES_SELECT_SMALLER_DIAGNOSTIC'
    model,tokenizer,configs,dirs,hashes,frozen=_source_setup(device,baseline,backbone,
       activation_dir,format_dir,joint_dir,include_joint,allow_download)
    data=[]
    for config_name,lora,head in configs:
        zero.restore_lora(model.backbone,lora)
        model.head.load_state_dict(head,strict=True)
        for row in rows:
            ids=tokenizer(row['prompt'],add_special_tokens=False)['input_ids']
            assert len(ids)>0 and len(ids)<=1024,'HYBRID_PROBE_PROMPT_TOO_LONG'
            tensor=torch.tensor([ids],dtype=torch.long,device=device)
            for depth,ctrl in conditions:
                x=decode_condition(model,tokenizer,tensor,depth,ctrl,max_new_tokens)
                record={k:v for k,v in row.items() if k!='prompt'}
                record.update({'configuration':config_name,'depth':depth,'control':ctrl,
                               'marker_tokens_present':ctrl!='text_only',
                               'latent_positions':depth,'completion':x['completion'],
                               'tokens':x['tokens']})
                record.update(parse_completion(x['completion'],
                    budget_exhausted=x['tokens']>=max_new_tokens))
                data.append(record)
                if emit_cases:
                    print('HYBRID_PROMPT_CASE='+json.dumps(record,sort_keys=True),flush=True)
        assert zero.frozen_backbone_sha256(model.backbone)==frozen,'HYBRID_PROBE_BACKBONE_MUTATION'
    now={str(p.resolve()):activation.file_hash(p) for d in dirs for p in d.iterdir() if p.is_file()}
    assert hashes==now,'HYBRID_PROBE_SOURCE_CHANGED'
    summary={'schema':SCHEMA,'status':'READ_ONLY_DIAGNOSTIC_NOT_CERTIFIED',
        'configuration_names':[c[0] for c in configs], 'source_file_sha256':hashes,
        'rungs':list(rungs),'band':list(band),'scales':list(scales),'styles':list(styles),
        'depths':list(depths),'conditions':[{'depth':d,'control':c} for d,c in conditions],
        'questions':len(rows),'inference_records':len(data),
        'test_evaluated':False,'optimizer_steps':0,'qwen_qw5_stage2_active':False,
        'answer_oracle_in_prompt':False,
        'prompt_suffix':'Reasoning: ',
        'format_contract':'trained_prefix_first_answer_and_termination_scored_separately',
        'completion_observation_max_chars':300,
        'marker_control':'literal_start_and_end_tokens_no_latent_embeddings',
        'zero_control':'same_marker_and_latent_count_as_intact_but_zeroed_head_output',
        'deck_sha256':hashlib.sha256(json.dumps(rows,sort_keys=True).encode('utf8')).hexdigest(),
        'scores':summarize(data),
        'paired_comparisons':paired_comparisons(data)}
    output.mkdir(parents=True,exist_ok=True)
    zero.atomic_json(output/'result.json',{'summary':summary,'prompt_deck':rows,'records':data})
    print('HYBRID_PROMPT_RESULT='+json.dumps(summary,sort_keys=True),flush=True)
    return summary


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('preview','run'),default='run')
    p.add_argument('--rungs',default='Z0,R2,T08')
    p.add_argument('--band',default='0-4')
    p.add_argument('--scales',default='8')
    p.add_argument('--styles',default='direct,scaffold')
    p.add_argument('--depths',default='0,1,2,4')
    p.add_argument('--quiet-cases',action='store_true',help='Print only the summary; save all case records to result.json')
    p.add_argument('--max-new-tokens',type=int,default=48)
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--baseline',type=Path)
    p.add_argument('--backbone',type=Path)
    p.add_argument('--activation',type=Path)
    p.add_argument('--format',type=Path)
    p.add_argument('--joint',type=Path)
    p.add_argument('--include-joint',action='store_true')
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        rungs=tuple(x.strip() for x in args.rungs.split(',') if x.strip())
        scales=tuple(int(x) for x in args.scales.split(','))
        styles=tuple(x.strip() for x in args.styles.split(','))
        depths=tuple(int(x) for x in args.depths.split(','))
        lo,hi=map(int,args.band.split('-'))
        rows=make_rows(rungs,lo,hi,scales,styles,args.seed)
        if args.stage=='preview':
            info={'schema':SCHEMA,'rows':len(rows),'rungs':rungs,'band':[lo,hi],
                  'scales':scales,'styles':styles,'preview':rows[:12],
                  'read_only':True,'model_loaded':False,
                'conditions':[{'depth':d,'control':c} for d,c in inference_conditions(depths)]}
            print('HYBRID_PROMPT_PREVIEW='+json.dumps(info,sort_keys=True));return 0
        run(device=args.device,rungs=rungs,band=(lo,hi),scales=scales,styles=styles,
            depths=depths,max_new_tokens=args.max_new_tokens,seed=args.seed,
            baseline=args.baseline,backbone=args.backbone,
            activation_dir=args.activation,format_dir=args.format,joint_dir=args.joint,
            include_joint=args.include_joint,output=args.output,allow_download=args.allow_model_download,
            emit_cases=not args.quiet_cases)
        return 0
    except (OSError,ValueError,AssertionError,RuntimeError,KeyError) as exc:
        print('HYBRID_PROMPT_REFUSED='+type(exc).__name__+':'+str(exc),file=sys.stderr,flush=True)
        return 2

if __name__=='__main__':raise SystemExit(main())
