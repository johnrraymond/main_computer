#!/usr/bin/env python3
"""Read-only word/number magnitude-transfer smoke for frozen Qwen + optional glass.

The suite is not a curriculum or a mathematical oracle at inference.  It tests
unassisted greedy output on exact answer packets, with separately constructed
Python-reference targets.  New/undefined number words are never assigned a
numerical magnitude unless the question explicitly defines one.

Examples deliberately go far beyond the numeric-domain of the training ladder.
A failure is useful evidence of lack of transfer, not a reason to train silently.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import contextlib
import hashlib
import json
import re
from pathlib import Path

SCHEMA = 'nanojev.qwen.worded-order-transfer-smoke.v1'
SCALES = (
    ('hundred', 2), ('thousand', 3), ('million', 6), ('billion', 9),
    ('trillion', 12), ('quadrillion', 15), ('quintillion', 18),
    ('sextillion', 21), ('septillion', 24), ('octillion', 27),
    ('nonillion', 30), ('decillion', 33), ('undecillion', 36),
    ('duodecillion', 39), ('tredecillion', 42), ('quattuordecillion', 45),
    ('quindecillion', 48), ('vigintillion', 63), ('centillion', 303),
)
ANSWER_LINE = re.compile(r'^ANSWER: (.+)$')
INTEGER = re.compile(r'^([+-]?\d[\d,_]*)(?: ([A-Za-z]+))?$')
POWER = re.compile(r'^([+-]?\d+)\s*\*\s*10\^\{?(\d+)\}?(?: ([A-Za-z]+))?$')


def zigzag(n: int) -> int:
    return 2*n if n >= 0 else -2*n-1


def cantor(a: int, b: int) -> int:
    if a < 0 or b < 0:
        raise ValueError('Cantor pairing requires nonnegative inputs')
    return (a+b)*(a+b+1)//2+b


def _case(group, name, question, answer, *, kind='literal', order=None, comment=''):
    return {
        'id': f'{group}:{name}', 'group':group,
        'source':'PROBLEM: '+question,
        'target':'ANSWER: '+str(answer), 'kind':kind,
        'order':order, 'comment':comment,
    }


def make_deck():
    """Deterministic complete deck with exact targets; no model dependency."""
    cases=[]
    def add(*args,**kwargs): cases.append(_case(*args,**kwargs))
    # Ordinary exact decimal -> named order; small coefficients intentionally dominate.
    for word, exponent in SCALES:
        if exponent <= 48:
            add('word_to_integer',word,
                f'Convert 10 {word} dollars to an exact integer number of dollars. '
                'Give only the integer, without commas or the unit.',
                10*(10**exponent),kind='integer',order=exponent)
        add('word_to_power',word,
            f'In short-scale English, express 10 {word} dollars as coefficient * 10^exponent dollars. '
            'Do not expand the digits.',
            f'10 * 10^{exponent} dollars',kind='power',order=exponent)
        add('inverse_power',word,
            f'Express 10^{exponent} in short-scale English as one magnitude word. '
            'Give the word alone.',word,order=exponent)
    # Values explicitly normalized to tiny coefficients and exact common scales.
    for word, exponent in SCALES[:12]:
        add('small_coefficient',word,
            f'What is (10 {word} dollars - 3 {word} dollars) in whole dollars? '
            'Give just the integer.',7*10**exponent,kind='integer',order=exponent)
        if exponent >= 3:
            # For exponent=3, hundred is a 10x step; keep the normalization exact.
            ratio_exp = exponent-(2 if exponent==3 else 3)
            previous = ('hundred' if exponent==3 else next(w for w,e in SCALES if e == exponent-3))
            add('unit_conversion',word,
                f'How many {previous}s equal one {word}? Give only the integer.',
                10**ratio_exp,kind='integer',order=exponent)
    add('mixed_scale','trillion_billion',
        'Convert 10 trillion dollars plus 500 billion dollars into whole dollars. Give only the integer.',
        10*10**12+500*10**9,kind='integer',order=12)
    add('mixed_scale','trillion_billion_small',
        'Convert 12 trillion dollars plus 345 billion dollars into whole billions of dollars. Give only the integer.',
        12*1000+345,kind='integer',order=12)
    add('mixed_scale','million_thousands',
        'Convert 2 million meters plus 300 thousand meters into whole meters. Give only the integer.',
        2_300_000,kind='integer',order=6)
    add('signed_cancel','trillion_minus_trillion',
        'What is 10 trillion dollars minus 10 trillion dollars in whole dollars? Give only the integer.',
        0,kind='integer',order=12)
    add('signed_cancel','quadrillion_near_zero',
        'What is 10 quadrillion dollars minus 9999999999999999 dollars? Give only the integer.',
        1,kind='integer',order=15)
    add('signed_cancel','negative_residual',
        'What is 10 billion minus 10000000003? Give only the integer.',
        -3,kind='integer',order=9)
    # Exact decomposition and recomposition tests; no expanded mega-sized digits.
    for coeff, exp in [(10,2),(10,12),(3,33),(10,63),(2,100),(7,303),(1,1000)]:
        add('symbolic_power',f'{coeff}_e{exp}',
            f'Represent {coeff} times ten to the power {exp} in coefficient * 10^exponent notation. '
            'Do not expand the digits.',
            f'{coeff} * 10^{exp}',kind='power',order=exp)
    for a,b in [(0,0),(1,-1),(-1,1),(2,3),(-3,2)]:
        n=cantor(zigzag(a),zigzag(b))
        add('godel_coordinates',f'pair_{a}_{b}',
            'For this question define zigzag(n)=2*n for nonnegative integers, '
            'zigzag(n)=-2*n-1 for negative integers, and '
            'pair(a,b)=(a+b)*(a+b+1)/2+b for nonnegative a,b. '
            f'What is pair(zigzag({a}),zigzag({b}))? Give only the integer.',
            n,kind='integer')
    for a,b,c in [(0,0,0),(1,0,2),(-1,2,0)]:
        n=cantor(cantor(zigzag(a),zigzag(b)),zigzag(c))
        add('godel_coordinates',f'triple_{a}_{b}_{c}',
            'Define z(n)=2*n for n>=0, and z(n)=-2*n-1 for n<0. '
            'Define p(a,b)=(a+b)*(a+b+1)/2+b for nonnegative inputs. '
            f'Evaluate p(p(z({a}),z({b})),z({c})). Give only the integer.',
            n,kind='integer')
    # Graham's number cannot meaningfully be expanded; test its symbolic identity.
    add('graham_symbolic','definition',
        "In Graham's-number notation g1=3 up-arrow-up-arrow-up-arrow-up-arrow 3, "
        'g(n+1)=3 with g(n) up-arrows to 3, and G=g64. '
        'Which indexed g-term is G? Give only the index notation.',
        'g64')
    add('graham_symbolic','comparison',
        "Is Graham's number greater than 10^100? Give only YES or NO.", 'YES')
    # Algebraic identities near the Graham-number boundary are exact without
    # any attempt to materialize its decimal expansion.
    add('graham_symbolic','identity_additive',
        "Let G denote Graham's number. Simplify G+0 symbolically. Output only G.", 'G')
    add('graham_symbolic','identity_multiplicative',
        "Let G denote Graham's number. Simplify 1*G symbolically. Output only G.", 'G')
    add('graham_symbolic','identity_cancellation',
        "Let G denote Graham's number. Simplify G-G exactly. Output only the integer.",
        0,kind='integer')
    add('graham_symbolic','identity_nonzero',
        "Let G denote Graham's number. Simplify G/G exactly. Output only the integer.",
        1,kind='integer')
    # Coordinate context drives decomposition BEFORE integer encoding.
    add('worded_coordinates','trillion_signed',
        'Represent the three coordinates (10 trillion meters, 0 trillion meters, '
        '-10 trillion meters) in units of one trillion meters. '
        'Define z(n)=2*n for n>=0, z(n)=-2*n-1 for n<0; '
        'define p(a,b)=(a+b)*(a+b+1)/2+b. '
        'What is p(p(z(x),z(y)),z(zcoord)) for the normalized signed coefficients '
        '(x,y,zcoord)? Give only the integer.',
        cantor(cantor(zigzag(10),zigzag(0)),zigzag(-10)),kind='integer',order=12)
    add('worded_coordinates','billion_to_trillion',
        'Take coordinates (500 billion meters, 1 trillion meters, 0 meters). '
        'Normalize each coordinate to units of 500 billion meters. '
        'Define z(n)=2*n for n>=0, z(n)=-2*n-1 for n<0; '
        'define p(a,b)=(a+b)*(a+b+1)/2+b. '
        'What is p(p(z(x),z(y)),z(zcoord)) for the normalized coefficients? '
        'Give only the integer.',
        cantor(cantor(zigzag(1),zigzag(2)),zigzag(0)),kind='integer',order=12)
    add('definition_boundary','unit_mismatch',
        'Can 10 trillion dollars and 10 trillion meters be directly added '
        'as one quantity without defining a conversion? Give only YES or NO.', 'NO')
    # These are *not* standard magnitude/coordinate definitions.  Test explicit
    # restraint, then an in-question stipulated meaning (not a global fact).
    add('definition_boundary','undefined_scale',
        'A text mentions "mega-deca-quadrillions" without defining the term. '
        'Can its exact power-of-ten magnitude be uniquely determined? Give only YES or NO.', 'NO')
    add('definition_boundary','defined_scale',
        'In THIS QUESTION ONLY, define "mega-deca-quadrillion" to mean 10^45. '
        'Express 2 mega-deca-quadrillions in coefficient * 10^exponent notation.',
        '2 * 10^45',kind='power',order=45,
        comment='10^45 is a local, invented stipulation, not a standard scale-name claim')
    add('definition_boundary','undefined_universe',
        'Without an agreed encoding, can the phrase "the Goedel-space coordinates of the known universe" '
        'specify one unique integer coordinate? Give only YES or NO.', 'NO')
    add('definition_boundary','hundreds_ambiguous',
        'Does the expression "hundreds of dollars" by itself specify one exact integer amount? '
        'Give only YES or NO.', 'NO')
    if len({r['id'] for r in cases})!=len(cases):
        raise RuntimeError('TRANSFER_DUPLICATE_IDS')
    return cases


def _numeric_value(body):
    """Exact mantissa/power normalization without expanding enormous powers.

    E.g. 1000 and 10 * 10^2 both become (1,3,None).  There is no
    probability or approximation here; all arithmetic uses Python ints.
    """
    body=body.strip()
    matched=INTEGER.fullmatch(body)
    if matched:
        coefficient=int(matched.group(1).replace(',', '').replace('_',''))
        exponent=0
        unit=matched.group(2)
    else:
        matched=POWER.fullmatch(body)
        if not matched:return None
        coefficient=int(matched.group(1))
        exponent=int(matched.group(2))
        unit=matched.group(3)
    if coefficient==0:return (0,0,unit.lower() if unit else None)
    while coefficient%10==0:
        coefficient//=10
        exponent+=1
    return (coefficient,exponent,unit.lower() if unit else None)


def _normalize_body(body,kind):
    body=body.strip()
    if kind in ('integer','power'):
        return _numeric_value(body)
    return body.casefold() if kind=='literal' else body


def grade(row, generated):
    """Strict one-line packet and a separately reported normalization grade."""
    lines=generated.replace('\r\n','\n').replace('\r','\n').strip().split('\n')
    actual=lines[0].strip() if len(lines)==1 else None
    exact=actual==row['target']
    m=ANSWER_LINE.fullmatch(actual or '')
    t=ANSWER_LINE.fullmatch(row['target'])
    normalized=bool(m and t and _normalize_body(m.group(1),row['kind'])==_normalize_body(t.group(1),row['kind']))
    return {'strict':exact,'normalized':normalized,'actual':generated}


def _aggregate(rows):
    groups=defaultdict(lambda:{'cases':0,'strict':0,'normalized':0,'failures':[]})
    for rec in rows:
        key=rec['group'];v=groups[key];v['cases']+=1
        v['strict']+=int(rec['grade']['strict'])
        v['normalized']+=int(rec['grade']['normalized'])
        if not rec['grade']['normalized']:
            v['failures'].append(rec['id'])
    return {k:dict(v) for k,v in sorted(groups.items())}


def _load_model(args):
    """Restore the immutable post-training LoRA; never update weights."""
    import torch
    from tools import nanojev_qwen_lm_radius_ladder as ladder
    from tools import nanojev_qwen_lm_deformation_smoke as smoke
    baseline_file=Path(args.output)/'baseline.pt'
    if not baseline_file.is_file():raise FileNotFoundError('TRANSFER_BASELINE_NOT_FOUND:'+str(baseline_file))
    baseline=torch.load(baseline_file,map_location='cpu',weights_only=True)
    bb,tok,source,frozen,_dirs,zero=ladder._fresh_parent(args)
    if baseline.get('source_sha256')!=source or baseline.get('frozen_sha256')!=frozen:
        raise RuntimeError('TRANSFER_BASELINE_PARENT_MISMATCH')
    zero.restore_lora(bb,baseline['lora'])
    bb.eval();bb.requires_grad_(False)
    return bb,tok,zero,smoke


def _glass(args,bb,zero,smoke):
    if args.glass_mode=='off':return None
    import torch
    root=Path(args.output)
    saved_path=root/'glass.pt'
    if not saved_path.is_file():raise FileNotFoundError('TRANSFER_GLASS_NOT_FOUND:'+str(saved_path))
    saved=torch.load(saved_path,map_location='cpu',weights_only=True)
    if saved.get('backbone_lora_sha256')!=smoke._lora_digest(bb,zero):
        raise RuntimeError('TRANSFER_GLASS_BASELINE_DRIFT')
    head=bb.get_output_embeddings()
    layer=smoke.RelativeDeformationGlass(None,hidden=int(head.weight.shape[1]),
            keys=saved['keys'],projection_dim=int(saved['projection_dim']),
            threshold=float(saved['threshold']),seed=args.seed)
    layer.net.to(args.device)
    if args.glass_mode=='original':
        state=saved['glass']
    else:
        refined=root/'glass_refinement'/'refined_glass.pt'
        if not refined.is_file():raise FileNotFoundError('TRANSFER_REFINED_NOT_FOUND:'+str(refined))
        data=torch.load(refined,map_location='cpu',weights_only=True)
        state=data['glass']
    layer.net.load_state_dict(state,strict=True)
    layer.net.eval()
    return layer


def _run_variant(args,cases,bb,tok,layer,label):
    from tools import nanojev_qwen_lm_radius_ladder as ladder
    results=[]
    head=bb.get_output_embeddings()
    cm=layer.hook(head) if layer is not None else contextlib.nullcontext()
    # Greedy generation is always free-running; no target supplied.
    with cm:
        for k in range(0,len(cases),args.eval_batch):
            batch=cases[k:k+args.eval_batch]
            outputs=ladder._greedy(bb,tok,[r['source'] for r in batch],
                                   args.device,args.max_tokens,args.max_new_tokens)
            for row,completion in zip(batch,outputs):
                results.append({**row,'grade':grade(row,completion)})
            if args.progress_every and (k//args.eval_batch+1)%args.progress_every==0:
                print('TRANSFER_SMOKE_PROGRESS='+json.dumps({'variant':label,'tested':len(results),
                       'total':len(cases)},sort_keys=True),flush=True)
    return results


def run(args):
    cases=make_deck()
    output=Path(args.probe_output)
    # Intentionally separate from the trained original artifact directory.
    resolved=output.resolve();protected=Path(args.output).resolve()
    if resolved==protected or resolved in protected.parents or protected in resolved.parents:
        raise RuntimeError('TRANSFER_OUTPUT_OVERLAPS_BACKBONE')
    output.mkdir(parents=True,exist_ok=True)
    deck_path=output/'transfer_cases.jsonl'
    deck_path.write_text(''.join(json.dumps(r,sort_keys=True,ensure_ascii=False)+'\n' for r in cases),encoding='utf8')
    sha=hashlib.sha256(deck_path.read_bytes()).hexdigest()
    if args.stage=='deck':
        print('TRANSFER_SMOKE_DECK='+json.dumps({'cases':len(cases),'path':str(deck_path),
             'sha256':sha,'groups':dict(Counter(r['group'] for r in cases))},sort_keys=True),flush=True)
        return {'cases':len(cases),'sha256':sha}
    if not args.stage1_checkpoint or not args.text_checkpoint:
        raise ValueError('TRANSFER_REQUIRES_STAGE1_AND_TEXT_CHECKPOINT')
    from tools import nanojev_qwen_lm_radius_ladder as ladder
    bb,tok,zero,smoke=_load_model(args)
    glass=_glass(args,bb,zero,smoke)
    backbone_digest=smoke._lora_digest(bb,zero)
    base=_run_variant(args,cases,bb,tok,None,'baseline')
    corrected=_run_variant(args,cases,bb,tok,glass,args.glass_mode) if glass is not None else None
    if backbone_digest!=smoke._lora_digest(bb,zero):raise RuntimeError('TRANSFER_MUTATED_QWEN')
    def scores(rows):
        return {'tested':len(rows),'strict':sum(r['grade']['strict'] for r in rows),
                'normalized':sum(r['grade']['normalized'] for r in rows),'by_group':_aggregate(rows)}
    answer={'schema':SCHEMA,'deck_sha256':sha,'baseline_sha256':smoke._file_hash(Path(args.output)/'baseline.pt'),
            'variant':args.glass_mode,'baseline':scores(base),'with_glass':scores(corrected) if corrected is not None else None,
            'note':'Exact worded-number and coordinate transfer; no training, no oracle at inference. Symbolic names require explicit definitions.'}
    if corrected is not None:
        pb={r['id'] for r in base if r['grade']['normalized']}
        pc={r['id'] for r in corrected if r['grade']['normalized']}
        answer['glass_effect']={'fixed':sorted(pc-pb),'regressed':sorted(pb-pc),'net':len(pc)-len(pb)}
    ladder._atomic_json(output/'report.json',answer)
    (output/'predictions.jsonl').write_text(''.join(json.dumps({'variant':tag,**r},sort_keys=True,ensure_ascii=False)+'\n'
                for tag,entries in [('baseline',base),(args.glass_mode,corrected or [])] for r in entries),encoding='utf8')
    print('TRANSFER_SMOKE_RESULT='+json.dumps({'cases':len(cases),'baseline':{k:v for k,v in answer['baseline'].items() if k!='by_group'},
          'glass':({k:v for k,v in answer['with_glass'].items() if k!='by_group'} if corrected is not None else None),
          'glass_effect':answer.get('glass_effect'),
          'report':str(output/'report.json')},sort_keys=True),flush=True)
    return answer


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('deck','run'),default='deck')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--stage1-checkpoint')
    p.add_argument('--text-checkpoint')
    p.add_argument('--ladder-working',help='Accepted for compatibility; the live checkpoint is never read/written')
    p.add_argument('--radii',default='0,1,2,3,4',help='Accepted for compatibility with $glassArgs')
    p.add_argument('--max-rung',type=int,default=8,help='Accepted for compatibility with $glassArgs')
    p.add_argument('--operand-limit',type=int,default=1024,help='Accepted for compatibility with $glassArgs')
    p.add_argument('--output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_deformation_glass_smoke_v1'),
                   help='Existing read-only frozen baseline and glass directory')
    p.add_argument('--probe-output',default=str(Path.home()/'NanoJev'/'runs'/'qwen_worded_order_transfer_smoke_v1'))
    p.add_argument('--glass-mode',choices=('off','original','refined'),default='refined')
    p.add_argument('--seed',type=int,default=42257)
    p.add_argument('--baseline');p.add_argument('--backbone')
    p.add_argument('--activation');p.add_argument('--format')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--eval-batch',type=int,default=4)
    p.add_argument('--max-tokens',type=int,default=512)
    p.add_argument('--max-new-tokens',type=int,default=128)
    p.add_argument('--progress-every',type=int,default=5)
    args=p.parse_args(argv)
    if args.eval_batch<1 or args.max_new_tokens<8 or args.max_tokens<=args.max_new_tokens:
        p.error('Invalid batch or token limits')
    return run(args)


if __name__=='__main__':main()
