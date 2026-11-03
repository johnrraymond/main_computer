#!/usr/bin/env python3
"""Diagnostic Shotgun for matched HDB08 failure-neighborhood variants.

Shotgun is a fourth joint optimizer population, not a restatement of HDB07/08.
Equivalent underlying payoff balances have multiple surface representations.
The complete frozen evaluation deck never enters the optimizer. Generated
train cases are independent of step-budget progression and explicitly vary
scale, cancellation, offset, coefficients, order, and language.
"""
from __future__ import annotations
import hashlib
import json
import random

VERSION = 'nanojev.qwen.shotgun.v1'
MODES = ('products', 'contributions', 'structured', 'prose',
         'reordered_prose', 'table', 'intermediate', 'word_problem')
SCALES = (1, 10, 100, 1000, 100000, 10000000)
# All errors are exact signed integers; none are interpreted as a class label.


def _random(seed, index, mode):
    payload=json.dumps((VERSION,int(seed),int(index)))
    return random.Random(int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], 'big'))


def case(mode, index, *, seed=42257, evaluation=False):
    if mode not in MODES or index<0:
        raise ValueError('SHOTGUN_INVALID_CASE')
    rng=_random(seed,index,mode)
    p=rng.randint(2,21);q=rng.randint(2,21)
    scale=SCALES[index%len(SCALES)]
    # Offset is NOT a function of the optimizer's 24M-step ceiling.
    # Balance is exactly -(p+q)*offset. Zero, negative and positive appear.
    offset_choices=(0,1,-1,2,-2,8,-8,64,-64,1024,-1024)
    offset=offset_choices[index%len(offset_choices)]
    multiplier=max(scale, abs(offset)+1)
    hawks=q*multiplier+offset
    doves=p*multiplier-offset
    hawk_co=-p; dove_co=q
    hc=hawk_co*hawks;dc=dove_co*doves
    total=hc+dc
    if mode=='products':
        prompt=f'({hawk_co}*{hawks})+({dove_co}*{doves})'
    elif mode=='contributions':
        prompt=f'Combine numerical contributions ({hc})+({dc})'
    elif mode=='structured':
        prompt=f'Balance: Hawk encounters={hawks}; Hawk contribution/encounter={hawk_co}; Dove encounters={doves}; Dove contribution/encounter={dove_co}. Compute the signed total.'
    elif mode=='prose':
        prompt=f'Each of {hawks} Hawk encounters contributes {hawk_co} to the balance. Each of {doves} Dove encounters contributes +{dove_co}. What is the final signed balance?'
    elif mode=='reordered_prose':
        prompt=f'Find the signed aggregate. A Dove encounter adds {dove_co}; there are {doves} Dove encounters. There are also {hawks} Hawk encounters, each subtracting {p}.'
    elif mode=='table':
        prompt=f'Encounter counts and coefficients: [Dove: {doves}, {dove_co}] [Hawk: {hawks}, {hawk_co}]. Return the sum of count times coefficient.'
    elif mode=='intermediate':
        prompt=f'Hawk subtotal={hc}; Dove subtotal={dc}. Add both subtotals and return the signed balance.'
    else:
        prompt=f'A balance starts at zero. It loses {p} points on each of {hawks} Hawk turns and gains {q} points on each of {doves} Dove turns. Where does the balance finish?'
    src='PROBLEM: '+prompt
    return {'id':f'SHOTGUN:{mode}:{index}', 'family':'shotgun','task':'shotgun',
            'mode':mode,'scale':scale,'zero_side':'zero' if total==0 else ('positive' if total>0 else 'negative'),
            'source':src,'target':'PROBLEM: '+str(total),'terminal_kind':'int',
            'metadata':{'hawk_count':hawks,'dove_count':doves,'hawk_coefficient':hawk_co,
                        'dove_coefficient':dove_co,'offset':offset,'expected_balance':total,
                        'is_evaluation':bool(evaluation)}}


def evaluation(seed, per_mode=6):
    """Full frozen mode coverage. Indices never overlap training's >=10k band."""
    if not 1<=per_mode<=32: raise ValueError('SHOTGUN_EVAL_CASES_OUT_OF_RANGE')
    return [case(mode,i,seed=seed,evaluation=True)
            for mode in MODES for i in range(per_mode)]


def train_case(seed,step,mode,forbidden):
    """Fresh deterministic sample with exact source-disjointness enforcement."""
    for attempt in range(128):
        row=case(mode,10000+step*131+attempt,seed=seed)
        if row['source'] not in forbidden:
            return row
    raise RuntimeError('SHOTGUN_TRAIN_EVAL_COLLISION')


def exposures():
    return {mode:{'examples':0,'steps':0} for mode in MODES}


def metrics(rows,before,after,key):
    def fresh(): return {'tested':0,'correct':0,'gained':0,'lost':0,'net':0}
    total=fresh();by_mode={};by_scale={};by_side={}
    for row in rows:
        k=key(row)
        a=bool(before[k]['correct']);b=bool(after[k]['correct'])
        buckets=(total,by_mode.setdefault(row['mode'],fresh()),
                 by_scale.setdefault(str(row['scale']),fresh()),
                 by_side.setdefault(row['zero_side'],fresh()))
        for d in buckets:
            d['tested']+=1;d['correct']+=int(b)
            d['gained']+=int(b and not a);d['lost']+=int(a and not b)
            d['net']=d['gained']-d['lost']
    for d in (total,*by_mode.values(),*by_scale.values(),*by_side.values()):
        d['accuracy']=d['correct']/d['tested'] if d['tested'] else None
    return {'total':total,'by_mode':by_mode,'by_scale':by_scale,'by_side':by_side}


def error_distribution(rows, scores, key):
    """Diagnostic only: normalized residuals, robust spread and sign accuracy.

    Report by presentation mode as well as by scale. Never treat an approximate
    answer as exactly correct and never use these heldouts for supervision.
    """
    from statistics import median
    from math import log1p, copysign
    from tools.nanojev_qwen_lm_radius_ladder import numeric_terminal

    def quantile(values,fraction):
        if not values:return None
        v=sorted(values);a=(len(v)-1)*fraction;lo=int(a);hi=min(lo+1,len(v)-1)
        return v[lo]+(v[hi]-v[lo])*(a-lo)

    def summary(samples):
        residuals=[];correct_sign=0;good=0
        for row,record in samples:
            val=numeric_terminal(record.get('emitted',''))
            if val is None:continue
            answer=row['metadata']['expected_balance']
            unit=abs(row['metadata']['hawk_coefficient'])+row['metadata']['dove_coefficient']
            normalized=(val-answer)/unit
            residuals.append(normalized)
            good+=int(val==answer)
            correct_sign+=int((val>0)-(val<0)==(answer>0)-(answer<0))
        transformed=[copysign(log1p(abs(x)),x) for x in residuals]
        q25=quantile(transformed,.25);q75=quantile(transformed,.75)
        var=((q75-q25)/1.349)**2 if q25 is not None else None
        return {'tested':len(samples),'numeric_outputs':len(residuals),
                'exact_correct_numeric':good,
                'correct_sign_rate_of_numeric':correct_sign/len(residuals) if residuals else None,
                'median_absolute_error_offset_units':median(map(abs,residuals)) if residuals else None,
                'p90_absolute_error_offset_units':quantile([abs(x) for x in residuals],.9),
                'robust_variance_signed_log_offset_error':var}
    grouped={'by_mode':{},'by_scale':{},'by_side':{},'by_offset':{},'by_matrix':{}}
    for row in rows:
        labels={'by_mode':row['mode'],'by_scale':row['scale'],'by_side':row['zero_side']}
        if 'offset' in row.get('metadata',{}):labels['by_offset']=row['metadata']['offset']
        if 'matrix_key' in row:labels['by_matrix']=row['matrix_key']
        for field,label in labels.items():
            grouped[field].setdefault(str(label),[]).append((row,scores[key(row)]))
    return {'schema':'nanojev.shotgun.normalized-error.v1','total':summary([(r,scores[key(r)]) for r in rows]),
            **{field:{group:summary(samples) for group,samples in groups.items()}
               for field,groups in grouped.items()}}


def compare_distributions(before, after):
    """Matched fixed-deck, same-scoring-change diagnostics; not training gains."""
    if not before or before.get('schema')!=after.get('schema'):
        return None
    names=('correct_sign_rate_of_numeric','median_absolute_error_offset_units',
           'p90_absolute_error_offset_units','robust_variance_signed_log_offset_error')

    def compare(a,b):
        if a is None or b is None:return None
        compatible=(a['tested']==b['tested'] and
                    a['numeric_outputs']==a['tested'] and
                    b['numeric_outputs']==b['tested'])
        return {'fully_comparable_numeric_residuals':compatible,
                'previous_numeric_outputs':a['numeric_outputs'],
                'current_numeric_outputs':b['numeric_outputs'],
                'deltas':{field:(b[field]-a[field] if compatible and
                        a[field] is not None and b[field] is not None else None)
                          for field in names}}
    return {'total':compare(before['total'],after['total']),
            **{group:{label:compare(before.get(group,{}).get(label),item)
                       for label,item in after[group].items()}
               for group in ('by_mode','by_scale','by_side','by_offset','by_matrix')
                if group in after}}

# V2 diagnostic: matched treatments of *the same HDB08 mathematical case*.
# V1's six generic calculations remain available as historical controls, but
# are NOT the training source or primary evaluation for the joint trainer.
DIAGNOSTIC_VERSION = 'nanojev.qwen.shotgun.ledger-context-neighborhood.v6'
# These additional variants are evaluation-only. They are never selected by
# _batch, so adding diagnostic information does not change optimizer sampling.
OPERATION_MODES = ('products_with_matrix', 'point_differences',
                   'point_differences_with_matrix', 'points_expression',
                   'payoff_subtractions')
# Additional prompts preserve both the mathematical inputs and target.
# They remain evaluation-only until a same-checkpoint comparison identifies
# which formulations are actually better on nonzero HDB08 failures.
REPHRASE_MODES = (
    'direct_instruction', 'spreadsheet', 'json_packet', 'math_notation',
    'accounting_ledger', 'pseudocode', 'verbal_word_problem',
    'arithmetic_first', 'io_contract', 'separated_context',
    'ledger_reconciliation', 'debit_credit', 'numbered_operations',
    'authoritative_calculation',
)
DIAGNOSTIC_MODES = MODES + OPERATION_MODES + REPHRASE_MODES
# Directed matched contrasts; describe differences, not asserted causality.
OPERATION_BOUNDARIES = (
    ('contributions', 'products', 'multiply-and-cancel'),
    ('products', 'products_with_matrix', 'add-matrix-context-to-exact-expression'),
    ('point_differences', 'point_differences_with_matrix', 'add-matrix-context-to-point-differences'),
    ('products', 'point_differences', 'remove-expression-and-require-point-unit-conversion'),
    ('products', 'points_expression', 'multiply-in-point-units-then-double'),
    ('products', 'payoff_subtractions', 'derive-half-point-coefficients-inside-expression'),
    ('products_with_matrix', 'prose', 'remove-supplied-expression-keeping-matrix'),
    ('point_differences_with_matrix', 'prose', 'remove-supplied-payoff-differences'),
)

DIAGNOSTIC_OFFSETS = (-8, 8, -1, 1, 0, 4, -4, 2, -2)




def _spoken_integer(value):
    """Deterministic English wording for HDB encounter counts and coefficients."""
    n=int(value)
    if n<0:
        return 'negative '+_spoken_integer(-n)
    under20=('zero','one','two','three','four','five','six','seven','eight',
             'nine','ten','eleven','twelve','thirteen','fourteen','fifteen',
             'sixteen','seventeen','eighteen','nineteen')
    tens=('','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety')
    if n<20:return under20[n]
    if n<100:return tens[n//10]+(('-'+under20[n%10]) if n%10 else '')
    if n<1000:return under20[n//100]+' hundred'+(' '+_spoken_integer(n%100) if n%100 else '')
    for scale,label in ((1_000_000_000,'billion'),(1_000_000,'million'),(1000,'thousand')):
        if n>=scale:
            return _spoken_integer(n//scale)+' '+label+(((' and ' if n%scale<100 else ' ')+_spoken_integer(n%scale)) if n%scale else '')
    raise AssertionError('SHOTGUN_UNREACHABLE_SPOKEN_INTEGER')


def _spoken_points(value):
    """Express exact whole/half game payoffs verbally, without float drift."""
    from decimal import Decimal
    d=Decimal(str(value))
    negative=d<0
    d=abs(d)
    whole=int(d)
    fraction=d-whole
    if fraction not in (Decimal(0), Decimal('0.5')):
        raise ValueError('SHOTGUN_UNSUPPORTED_VERBAL_PAYOFF_PRECISION')
    words=_spoken_integer(whole)
    if fraction:
        words=('one half' if whole==0 else words+' and one half')
    return ('negative ' if negative else '')+words


def diagnostic_case(mode, *, variant, offset, label, evaluation=False, total_encounters=1_000_000):
    """One HDB08 oracle case presented in controlled equivalent ways.

    Underlying counts, payoff matrix and half-point target are identical for
    all diagnostic modes. Training matrices must never match heldout matrices.
    """
    from tools import nanojev_qwen_hdb_balance as hdb
    common,p,q=map(int,variant)
    if mode not in DIAGNOSTIC_MODES:
        raise ValueError('SHOTGUN_UNKNOWN_MODE')
    if evaluation and (common,p,q) not in hdb.EVAL_VARIANTS:
        raise ValueError('SHOTGUN_EVALUATION_MATRIX_NOT_HELDOUT')
    if not evaluation and (common not in hdb.TRAIN_COMMON or (p,q) not in hdb.TRAIN_SLOPES):
        raise ValueError('SHOTGUN_TRAIN_MATRIX_NOT_IN_TRAIN_DOMAIN')
    base=hdb._row(8,int(offset),(common,p,q),label=label)
    hc=base['hawk_coefficient']; dc=base['dove_coefficient']
    total_encounters=int(total_encounters)
    if (total_encounters<2*abs(int(offset)) or
            (total_encounters*q)%(p+q)):
        raise ValueError('SHOTGUN_INVALID_ENCOUNTER_SCALE')
    a=total_encounters*q//(p+q)+int(offset)
    b=total_encounters-a
    neg=hc*a;pos=dc*b
    payoff=base['payoffs']
    matrix=(f'HH={payoff["hawk_hawk"]}; HD={payoff["hawk_dove"]}; '
            f'DH={payoff["dove_hawk"]}; DD={payoff["dove_dove"]}')
    points_h=f'{hc/2:g}'
    points_d=f'{dc/2:g}'
    products_text=f'In half-point units compute exactly: ({hc}*{a})+({dc}*{b}).'
    differences_text=(f'Each Hawk-opponent encounter gives a Hawk-minus-Dove advantage '
                      f'of {points_h} points; each Dove-opponent encounter gives an '
                      f'advantage of {points_d} points. Opponent Hawk count={a}; '
                      f'opponent Dove count={b}. Calculate the total advantage '
                      'as an exact signed integer in half-point units.')
    if mode=='direct_instruction':
        prompt=(f'Game payoffs in points:\nHH = {payoff["hawk_hawk"]}\n'
                f'HD = {payoff["hawk_dove"]}\nDH = {payoff["dove_hawk"]}\n'
                f'DD = {payoff["dove_dove"]}\n'
                f'Calculate exactly this integer expression: ({hc} * {a}) + ({dc} * {b}).\n'
                'The coefficients and answer are in half-point units. Output only the integer packet.')
    elif mode=='spreadsheet':
        prompt=(f'PAYOFF TABLE (points)\n'
                f'                 Opponent H    Opponent D\n'
                f'Player H         {payoff["hawk_hawk"]}             {payoff["hawk_dove"]}\n'
                f'Player D         {payoff["dove_hawk"]}             {payoff["dove_dove"]}\n'
                'CALCULATION TABLE (half-point units)\n'
                f'Coefficient | Quantity\n{hc} | {a}\n{dc} | {b}\n'
                'Multiply each coefficient by its quantity. Add the two products. '
                'Return the exact integer total.')
    elif mode=='json_packet':
        packet={
            'task':'signed_balance',
            'payoffs_in_points':{'HH':float(payoff['hawk_hawk']),
                                 'HD':float(payoff['hawk_dove']),
                                 'DH':float(payoff['dove_hawk']),
                                 'DD':float(payoff['dove_dove'])},
            'calculation':{'operator':'sum_of_products','terms':[
                {'coefficient_half_points':hc,'quantity':a},
                {'coefficient_half_points':dc,'quantity':b}]},
            'result_units':'half-points','required_output':'PROBLEM: <integer>'}
        prompt='Compute the exact result of the following JSON task:\n'+json.dumps(packet,indent=2)
    elif mode=='math_notation':
        prompt=(f'Payoff matrix M in points, rows H,D and columns H,D:\n'
                f'M = [[{payoff["hawk_hawk"]}, {payoff["hawk_dove"]}], '
                f'[{payoff["dove_hawk"]}, {payoff["dove_dove"]}]]\n'
                f'Let A={hc}, B={dc}, x={a}, y={b}.\n'
                'Evaluate exactly S = A*x + B*y. S is an integer measured '
                'in half-point units. Return S.')
    elif mode=='accounting_ledger':
        prompt=(f'Game payoff reference in points: {matrix}.\n'
                'SIGNED HALF-POINT LEDGER\n'
                f'Entry A: {a} transactions, each contributing {hc} half-points.\n'
                f'Entry B: {b} transactions, each contributing +{dc} half-points.\n'
                'What is the exact net balance after combining these two entries? '
                'Return a signed integer.')
    elif mode=='pseudocode':
        prompt=(f'Execute the following pseudocode as exact integer arithmetic; '
                f'the payoff data are recorded in points:\n'
                f'HH = {payoff["hawk_hawk"]}\nHD = {payoff["hawk_dove"]}\n'
                f'DH = {payoff["dove_hawk"]}\nDD = {payoff["dove_dove"]}\n'
                f'a = {hc}  # half-point coefficient\n'
                f'b = {dc}  # half-point coefficient\n'
                f'x = {a}  # Hawk encounters\n'
                f'y = {b}  # Dove encounters\n'
                'result = (a * x) + (b * y)\nReturn result as an integer.')
    elif mode=='verbal_word_problem':
        prompt=(f'In a Hawk and Dove game, the Hawk-versus-Hawk payoff is '
                f'{_spoken_points(payoff["hawk_hawk"])} points, '
                f'Hawk-versus-Dove is {_spoken_points(payoff["hawk_dove"])} points, '
                f'Dove-versus-Hawk is {_spoken_points(payoff["dove_hawk"])} points, '
                f'and Dove-versus-Dove is {_spoken_points(payoff["dove_dove"])} points. '
                f'The balance ledger contains {_spoken_integer(a)} contributions '
                f'of {_spoken_integer(hc)} half-points and {_spoken_integer(b)} '
                f'contributions of {_spoken_integer(dc)} half-points. '
                'What is their exact combined signed balance in half-point units?')
    elif mode=='arithmetic_first':
        prompt=(f'FIRST evaluate exactly: ({hc}*{a})+({dc}*{b}).\n'
                'The result is in half-point units.\n'
                f'Associated game payoffs in points: {matrix}.\n'
                'Return the result of the supplied expression, not a new calculation from the matrix.')
    elif mode=='io_contract':
        prompt=(f'FUNCTION: signed_balance\nOUTPUT_TYPE: integer\n'
                'OUTPUT_UNITS: half-points\n'
                f'CONTEXT: payoff.HH={payoff["hawk_hawk"]}; '
                f'payoff.HD={payoff["hawk_dove"]}; '
                f'payoff.DH={payoff["dove_hawk"]}; '
                f'payoff.DD={payoff["dove_dove"]} (points)\n'
                f'INPUT: term_1=({hc},{a}); term_2=({dc},{b})\n'
                'OPERATION: sum(coefficient * quantity for each term)\n'
                'OUTPUT: exact signed integer')
    elif mode=='separated_context':
        prompt=(f'PART A - GAME CONTEXT (payoffs in points): {matrix}.\n'
                'PART B - THE REQUESTED COMPUTATION (coefficients in half-point units):\n'
                f'({hc}*{a})+({dc}*{b})\n'
                'The coefficients in Part B are already derived. Do not derive '
                'replacement coefficients from Part A. Evaluate Part B exactly '
                'and return the signed integer result.')
    elif mode=='ledger_reconciliation':
        prompt=(f'Game payoff reference in points: {matrix}.\n'
                'RECONCILE TWO SIGNED HALF-POINT ACCOUNTS\n'
                f'Account A has {a} entries of {hc} half-points each.\n'
                f'Account B has {b} entries of {dc:+d} half-points each.\n'
                'Calculate each account subtotal separately, then add their '
                'signed subtotals. Report only the exact integer net balance.')
    elif mode=='debit_credit':
        prompt=(f'Game payoffs in points: {matrix}.\n'
                'SIGNED DEBIT/CREDIT BALANCE (all amounts in half-point units)\n'
                f'DEBITS: {a} entries of {abs(hc)} half-points each.\n'
                f'CREDITS: {b} entries of {dc} half-points each.\n'
                'Net balance = total credits minus total debits. '
                'Return the exact signed integer.')
    elif mode=='numbered_operations':
        prompt=(f'Reference payoff matrix in points: {matrix}.\n'
                'Evaluate the already-specified arithmetic in half-point units, '
                'independently of the matrix presentation.\n'
                f'Step 1: Multiply {hc} by {a}.\n'
                f'Step 2: Multiply {dc:+d} by {b}.\n'
                'Step 3: Add the two signed products.\n'
                'Return only the final exact signed integer.')
    elif mode=='authoritative_calculation':
        prompt=(f'REFERENCE PAYOFF DATA (points): {matrix}.\n'
                f'AUTHORITATIVE CALCULATION (half-point units): '
                f'({hc}*{a})+({dc}*{b}).\n'
                'The calculation already incorporates the relevant payoff '
                'differences. Evaluate that expression exactly, rather than '
                'reinterpreting the payoff data. Return the signed integer.')
    elif mode=='products_with_matrix':
        # Exact *same* arithmetic instruction as 'products'; one new factor:
        # preceding payoff context. A pass->fail flip tests contextual interference.
        prompt=f'Payoff matrix in points: {matrix}.\n{products_text}'
    elif mode=='point_differences':
        # The points-based differences are supplied; the model still must
        # convert to half-point units and perform the count-weighted sum.
        prompt=differences_text
    elif mode=='point_differences_with_matrix':
        # Identical point-differences instruction, with payoff context appended.
        prompt=f'Payoff matrix in points: {matrix}.\n{differences_text}'
    elif mode=='points_expression':
        # The exact multiplication is supplied but still in *point* units.
        prompt=(f'Compute exactly 2*(({points_h}*{a})+({points_d}*{b})) '
                'to get the signed total in half-point units.')
    elif mode=='payoff_subtractions':
        # The extraction/subtractions are spelled out as arithmetic; unlike
        # 'products' they are not pre-computed, but no matrix reading is needed.
        hh=payoff['hawk_hawk'];dh=payoff['dove_hawk']
        hd=payoff['hawk_dove'];dd=payoff['dove_dove']
        prompt=(f'Compute exactly 2*((({hh})-({dh}))*{a}+'
                f'(({hd})-({dd}))*{b}). Return the signed half-point total.')
    elif mode=='products':
        # Equivalent to HDB07's intermediate exact arithmetic (already
        # converted to half-point coefficients).
        prompt=products_text
    elif mode=='contributions':
        prompt=f'In half-point units add the signed totals: ({neg})+({pos}).'
    elif mode=='structured':
        prompt=(f'Hawk/Dove payoff data in points: {matrix}. Opponent Hawk count={a}; '
                f'opponent Dove count={b}. Compute cumulative payoff(Hawk)-payoff(Dove) '
                'as an exact integer in half-point units.')
    elif mode=='prose':
        # Deliberately reproduce HDB08's ORIGINAL prompt verbatim for the
        # heldout 1M-count cases. This is the control that makes comparisons
        # with the existing HDB08 diagnostic genuinely matched.
        prompt=(f'In {total_encounters} encounters of Balanced Hawks and Doves, '
                f'the opponent plays Hawk {a} times and Dove {b} times.\n'
                f'Playing Hawk against Hawk pays {payoff["hawk_hawk"]} points.\n'
                f'Playing Hawk against Dove pays {payoff["hawk_dove"]} points.\n'
                f'Playing Dove against Hawk pays {payoff["dove_hawk"]} points.\n'
                f'Playing Dove against Dove pays {payoff["dove_dove"]} points.\n'
                'Calculate the exact signed difference of the cumulative '
                'payoffs from playing Hawk versus playing Dove, '
                'in half-point units.\n'
                'Return one canonical terminal PROBLEM: integer packet.')
    elif mode=='reordered_prose':
        prompt=(f'Calculate the exact signed advantage of Hawk, in half-point units. '
                f'Dove meets Dove: {payoff["dove_dove"]} points; Dove meets Hawk: '
                f'{payoff["dove_hawk"]} points; Hawk meets Dove: '
                f'{payoff["hawk_dove"]} points; Hawk meets Hawk: '
                f'{payoff["hawk_hawk"]} points. The opponent plays Dove {b} '
                f'times and Hawk {a} times.')
    elif mode=='table':
        prompt=(f'Payoff matrix (points), rows=our choice H,D; columns=opponent H,D: '
                f'[[{payoff["hawk_hawk"]},{payoff["hawk_dove"]}],'
                f'[{payoff["dove_hawk"]},{payoff["dove_dove"]}]]. '
                f'Opponent frequencies [H={a},D={b}]. Return the cumulative '
                'row-H minus row-D payoff in half-point units.')
    elif mode=='intermediate':
        prompt=(f'Payoffs in points: {matrix}. Opponent Hawk={a}; Dove={b}. '
                f'The corresponding Hawk-minus-Dove advantages in half-point '
                f'units are {hc} against Hawk and +{dc} against Dove. '
                'Compute the exact signed aggregate.')
    else:
        prompt=(f'Across {a+b} games, an opponent chose Hawk {a} times and '
                f'Dove {b} times. The outcomes for our Hawk strategy are '
                f'{payoff["hawk_hawk"]} and {payoff["hawk_dove"]} points '
                f'against those choices; for our Dove strategy they are '
                f'{payoff["dove_hawk"]} and {payoff["dove_dove"]}. '
                'How many more half-point units does Hawk earn in total? '
                'Give a signed integer, including a negative sign if it earns less.')
    source=(prompt if mode=='prose' else
            'PROBLEM: '+prompt+'\nReturn one canonical terminal PROBLEM: integer packet.')
    if evaluation and mode=='prose' and source!=base['source']:
        raise RuntimeError('SHOTGUN_HDB08_CANONICAL_PROMPT_MISMATCH')
    total=int(neg+pos);unit=abs(hc)+abs(dc)
    assert total==-common*(p+q)*int(offset)
    return {'id':f'SHOTGUN-V2:{label}:{mode}:{common}-{p}-{q}:{offset}:N{total_encounters}',
            'family':'shotgun','task':'shotgun', 'mode':mode,
            'scale':total_encounters,'offset_magnitude':abs(int(offset)),
            'matrix_key':f'{common}:{p}:{q}',
            'scenario_key':f'{common}:{p}:{q}:{offset}',
            'zero_side':base['zero_side'],
            'source':source,'target':f'PROBLEM: {total}',
            'terminal_kind':'int',
            'metadata':{'hawk_count':a,'dove_count':b,'hawk_coefficient':hc,
                        'dove_coefficient':dc,'offset':int(offset),
                        'expected_balance':total,'expected_hdb08_source':base['source'],
                        'is_evaluation':bool(evaluation),
                        'payoff_matrix':dict(payoff)}}


def diagnostic_evaluation(hdb_rows, *, per_family=6):
    """Same heldout HDB08 scenarios across modes; never optimizer inputs.

    Per_family=6 means six offsets for EACH of the three heldout matrices,
    not six problems total; 486 matched responses at the six-offset setting.
    """
    from tools import nanojev_qwen_hdb_balance as hdb
    if not 1<=per_family<=len(DIAGNOSTIC_OFFSETS):
        raise ValueError('SHOTGUN_DIAGNOSTIC_EVAL_CASES_OUT_OF_RANGE')
    available={(tuple(int(x) for x in (r['id'].split('-P',1)[1].split('-K',1)[0].split('_'))),int(r['offset']))
               for r in hdb_rows if r['tier']=='HDB08'}
    # Follow the actual frozen HDB evaluation policy (which may use 3, 5 or
    # 9 offsets); never create an unmatched question and call it paired.
    available_offsets={k for _,k in available}
    offsets=tuple(k for k in DIAGNOSTIC_OFFSETS if k in available_offsets)[:per_family]
    scenarios={(tuple(matrix),k) for matrix in hdb.EVAL_VARIANTS for k in offsets}
    if not offsets or not scenarios.issubset(available):
        raise RuntimeError('SHOTGUN_DIAGNOSTIC_NOT_MATCHED_TO_HDB08_EVAL')
    rows=[diagnostic_case(mode,variant=matrix,offset=k,
                          label='MATCHED-HDB08',evaluation=True)
          for matrix in hdb.EVAL_VARIANTS for k in offsets for mode in DIAGNOSTIC_MODES]
    if len({r['source'] for r in rows})!=len(rows):
        raise RuntimeError('SHOTGUN_DIAGNOSTIC_DUPLICATE_SOURCES')
    return rows


def failure_profile(hdb_rows, scores, key):
    """Use only failure *categories*; never replay heldout source/coefficients.

    Training uses disjoint train matrices. A weak k becomes an error-neighborhood
    magnitude choice, not an evaluated matrix/wording copied into training.
    """
    counts={}
    by_side={'negative':0,'positive':0,'zero':0}
    for row in hdb_rows:
        if row.get('tier')!='HDB08':continue
        rec=scores.get(key(row),{})
        if rec.get('correct'):continue
        k=int(row['offset'])
        counts[k]=counts.get(k,0)+1
        by_side[row['zero_side']]+=1
    return {'schema':DIAGNOSTIC_VERSION,'offset_failures':{str(k):v for k,v in sorted(counts.items())},
            'side_failures':by_side,'failed_scenarios':sum(counts.values()),
            'training_matrices':'train-only; heldout evaluation matrices excluded'}


def diagnostic_train_case(seed,step,mode,forbidden,profile=None):
    """Fresh matched-style variants concentrated around actual weak offsets."""
    from tools import nanojev_qwen_hdb_balance as hdb
    if mode not in MODES:raise ValueError('SHOTGUN_UNKNOWN_MODE')
    rng=_random(seed,step,mode)
    offsets=tuple(int(k) for k,v in (profile or {}).get('offset_failures',{}).items() if v>0)
    weights=tuple(int(v) for v in (profile or {}).get('offset_failures',{}).values() if v>0)
    default=(-1024,-64,-16,-8,-4,-2,-1,0,1,2,4,8,16,64,1024)
    for attempt in range(128):
        common=rng.choice(hdb.TRAIN_COMMON)
        p,q=rng.choice(hdb.TRAIN_SLOPES)
        if (common,p,q)==(1,1,1):continue
        # 75% actual failure neighborhoods; 25% breadth/zero/outer shells.
        if offsets and rng.random()<.75:
            offset=rng.choices(offsets,weights=weights,k=1)[0]
        else:
            offset=rng.choice(default)
        row=diagnostic_case(mode,variant=(common,p,q),offset=offset,
                            label=f'TRAIN-S{step}-A{attempt}',
                            total_encounters=rng.choice((100000,200000,1000000,2000000,10000000)))
        if row['source'] not in forbidden:
            return row
    raise RuntimeError('SHOTGUN_DIAGNOSTIC_TRAIN_COLLISION')


def matched_comparison(rows,scores,key):
    """Paired-mode answer comparison, by exact same underlying HDB08 case."""
    from tools.nanojev_qwen_lm_radius_ladder import numeric_terminal
    groups={};by_offset={};by_matrix={}
    for row in rows:
        rec=scores[key(row)]
        value=numeric_terminal(rec.get('emitted',''))
        expected=row['metadata']['expected_balance']
        correct=bool(rec.get('correct'))
        groups.setdefault(row['scenario_key'],{})[row['mode']]=correct
        for dest,label in ((by_offset,str(row['metadata']['offset'])),
                           (by_matrix,row['matrix_key'])):
            bucket=dest.setdefault(label,{'correct':0,'tested':0,'non_numeric':0})
            bucket['correct']+=int(correct);bucket['tested']+=1
            bucket['non_numeric']+=int(value is None)
    if any(set(modes)!=set(DIAGNOSTIC_MODES) for modes in groups.values()):
        raise RuntimeError('SHOTGUN_INCOMPLETE_MATCHED_SCENARIOS')
    comparisons={}
    for mode in DIAGNOSTIC_MODES:
        if mode=='prose':continue
        both=sum(int(v[mode] and v['prose']) for v in groups.values())
        rescue=sum(int(v[mode] and not v['prose']) for v in groups.values())
        interference=sum(int(v['prose'] and not v[mode]) for v in groups.values())
        comparisons[mode]={'both_exact':both,
                           'mode_succeeds_hdb_style_fails':rescue,
                           'hdb_style_succeeds_mode_fails':interference}
    boundaries={}
    for left,right,label in OPERATION_BOUNDARIES:
        nonzero=[modes for scenario,modes in groups.items()
                 if int(scenario.rsplit(':',1)[-1])!=0]
        def paired(measured):
            return {'both_correct':sum(int(v[left] and v[right]) for v in measured),
                    'left_correct_right_wrong':sum(int(v[left] and not v[right]) for v in measured),
                    'left_wrong_right_correct':sum(int(not v[left] and v[right]) for v in measured),
                    'both_wrong':sum(int(not v[left] and not v[right]) for v in measured),
                    'tested':len(measured)}
        boundaries[label]={'left':left,'right':right,'all':paired(list(groups.values())),
                           'nonzero':paired(nonzero)}
    return {'schema':DIAGNOSTIC_VERSION,'matched_scenarios':len(groups),
            'operation_boundaries':boundaries,
            'all_modes_exact':sum(all(v.values()) for v in groups.values()),
            'all_modes_wrong':sum(not any(v.values()) for v in groups.values()),
            'by_offset':by_offset,'by_payoff_matrix':by_matrix,
            'against_prose_payoff_matrix':comparisons,
            'note':'Descriptive paired answer tests; same arithmetic across all modes, no hidden-state claims'}


def operation_trace(rows, scores, key, *, step):
    """Preserve matched source and raw response for forensic comparison.

    This sidecar is observational evidence; it is not included in optimizer
    inputs, focus decisions, or the checkpoint's authoritative state.
    """
    from tools.nanojev_qwen_lm_radius_ladder import numeric_terminal
    cases={}
    for row in rows:
        rec=scores[key(row)]
        predicted=numeric_terminal(rec.get('emitted',''))
        expected=int(row['metadata']['expected_balance'])
        group=cases.setdefault(row['scenario_key'],{'expected':expected,
            'matrix_key':row['matrix_key'],'offset':row['metadata']['offset'],
            'modes':{}})
        if group['expected']!=expected or row['mode'] in group['modes']:
            raise RuntimeError('SHOTGUN_OPERATION_TRACE_CASE_MISMATCH')
        group['modes'][row['mode']]={'source':row['source'],
            'emitted':rec.get('emitted',''),'numeric_answer':predicted,
            'exact':bool(rec.get('correct')),
            'signed_error':(predicted-expected if predicted is not None else None)}
    if any(set(x['modes'])!=set(DIAGNOSTIC_MODES) for x in cases.values()):
        raise RuntimeError('SHOTGUN_OPERATION_TRACE_INCOMPLETE')
    return {'schema':DIAGNOSTIC_VERSION,'step':int(step),
            'scope':'fixed heldout diagnostic; never used for supervision',
            'scenarios':cases}


# Read-only, checkpoint-pinned factorial experiment. Deliberately separate from
# DIAGNOSTIC_MODES and the four-population optimizer. No deck migration.
COEFFICIENT_PROBE_VERSION = 'nanojev.qwen.shotgun.coefficient-matrix-factorial.v4'
COEFFICIENT_PROBE_MODES = (
    'products', 'products_with_matrix', 'products_doubled',
    'products_doubled_with_matrix', 'products_with_matrix_hh_shift',
    'products_doubled_with_matrix_hh_shift',
    'products_with_numeric_context', 'products_with_matrix_explicitly_ignored',
)


def coefficient_probe_evaluation(hdb_rows, *, per_family=6):
    """Independent interventions on expression E and payoff matrix M.

    The 2x2 E0/E2 × M0/M1 factorial has 4 cells. E0-alone/E2-alone
    measure arithmetic scaling without a matrix. Numeric-only context and
    an explicitly irrelevant matrix are separately identified controls.
    No generated target ever becomes a training row.
    """
    from decimal import Decimal
    seed_rows = diagnostic_evaluation(hdb_rows, per_family=per_family)
    originals = {}
    for r in seed_rows:
        if r['mode'] in ('products', 'products_with_matrix'):
            originals.setdefault(r['scenario_key'], {})[r['mode']] = r
    rows=[]
    for scenario, pair in originals.items():
        if set(pair) != {'products', 'products_with_matrix'}:
            raise RuntimeError('SHOTGUN_FACTORIAL_MISSING_ORIGINAL_CONTROL')
        a=pair['products'];b=pair['products_with_matrix']
        old=a['metadata']; hc=int(old['hawk_coefficient']);dc=int(old['dove_coefficient'])
        nh=int(old['hawk_count']);nd=int(old['dove_count']); target=int(old['expected_balance'])
        expression=f'({hc}*{nh})+({dc}*{nd})'
        doubled=f'({2*hc}*{nh})+({2*dc}*{nd})'
        assert expression in a['source'] and expression in b['source']
        matrix=old['payoff_matrix']
        fields=('hawk_hawk','hawk_dove','dove_hawk','dove_dove')
        names=('HH','HD','DH','DD')
        matrix_part='; '.join(f'{name}={matrix[f]}' for name,f in zip(names,fields))
        altered={**matrix,'hawk_hawk':format(Decimal(str(matrix['hawk_hawk']))+1,'f')}
        altered_part='; '.join(f'{name}={altered[f]}' for name,f in zip(names,fields))
        shell=lambda m, expr: (f'PROBLEM: Payoff matrix in points: {m}.\n'
                               f'In half-point units compute exactly: {expr}.\n'
                               'Return one canonical terminal PROBLEM: integer packet.')
        assert shell(matrix_part,expression)==b['source'], 'SHOTGUN_FACTORIAL_BASE_PROMPT_DRIFT'
        no_matrix=lambda expr: (f'PROBLEM: In half-point units compute exactly: {expr}.\n'
                              'Return one canonical terminal PROBLEM: integer packet.')
        assert no_matrix(expression)==a['source'], 'SHOTGUN_FACTORIAL_ARITHMETIC_PROMPT_DRIFT'
        def include(mode, source, multiplier, present, shifted):
            if mode=='products':
                row=dict(a)
            elif mode=='products_with_matrix':
                row=dict(b)
            else:
                row={**a,'mode':mode,'source':source,
                     'id':f'SHOTGUN-FACTORIAL:{scenario}:{mode}'}
            row['factor_expression']=multiplier
            row['factor_matrix_present']=present
            row['factor_matrix_hh_shift_points']=shifted
            row['target']=f'PROBLEM: {target*multiplier}'
            row['metadata']={**old,'expected_balance':target*multiplier,
                             'hawk_coefficient':hc*multiplier,
                             'dove_coefficient':dc*multiplier,
                             'is_evaluation':True,'factorial_only':True}
            rows.append(row)
        include('products',a['source'],1,False,0)
        include('products_with_matrix',b['source'],1,True,0)
        include('products_doubled',no_matrix(doubled),2,False,0)
        include('products_doubled_with_matrix',shell(matrix_part,doubled),2,True,0)
        include('products_with_matrix_hh_shift',shell(altered_part,expression),1,True,1)
        include('products_doubled_with_matrix_hh_shift',shell(altered_part,doubled),2,True,1)
        nums=', '.join(str(matrix[f]) for f in fields)
        include('products_with_numeric_context',(
            f'PROBLEM: Irrelevant reference numbers: {nums}.\n'
            f'In half-point units compute exactly: {expression}.\n'
            'Return one canonical terminal PROBLEM: integer packet.'),1,False,0)
        include('products_with_matrix_explicitly_ignored',(
            f'PROBLEM: Payoff matrix in points: {matrix_part}.\n'
            'The matrix is background only; do not recompute coefficients from it.\n'
            f'In half-point units compute exactly: {expression}.\n'
            'Return one canonical terminal PROBLEM: integer packet.'),1,True,0)
    if (not rows or len(rows)!=len(originals)*len(COEFFICIENT_PROBE_MODES) or
            len({r['source'] for r in rows})!=len(rows)):
        raise RuntimeError('SHOTGUN_FACTORIAL_INCOMPLETE_OR_DUPLICATE_DECK')
    return rows


def coefficient_probe_report(rows, scores, key, *, step, checkpoint_sha256):
    """Actual paired raw emissions and signed changes, including interaction.

    Compare numeric answers even if exact grading fails; non-numeric packets
    remain explicitly missing. The experimental axes change one factor at a
    time; doubling the arithmetic deliberately changes its exact target.
    """
    from tools.nanojev_qwen_lm_radius_ladder import numeric_terminal
    from collections import Counter
    scenarios={}
    for r in rows:
        record=scores[key(r)];output=record.get('emitted','')
        value=numeric_terminal(output)
        target=int(r['metadata']['expected_balance'])
        ident=r['scenario_key']; mode=r['mode']
        scene=scenarios.setdefault(ident,{
            'matrix_key':r['matrix_key'],'offset':int(r['metadata']['offset']),
            'original_expected':target//r['factor_expression'],
            'hawk_count':int(r['metadata']['hawk_count']), 'modes':{}})
        if scene['original_expected']*r['factor_expression']!=target or mode in scene['modes']:
            raise RuntimeError('SHOTGUN_FACTORIAL_SCENARIO_OR_TARGET_DRIFT')
        scene['modes'][mode]={'source':r['source'],'emitted':output,
            'parsed_integer':value,'expected':target,
            'exact_packet':bool(record.get('correct')),
            'numeric_correct':value==target if value is not None else False,
            'signed_error':value-target if value is not None else None,
            'negative_zero_packet':bool(__import__('re').fullmatch(r'\s*PROBLEM:\s*-0\s*',output)),
            'expression_multiplier':r['factor_expression'],
            'matrix_present':r['factor_matrix_present'],
            'matrix_hh_shift_points':r['factor_matrix_hh_shift_points']}
    if any(set(s['modes'])!=set(COEFFICIENT_PROBE_MODES) for s in scenarios.values()):
        raise RuntimeError('SHOTGUN_FACTORIAL_INCOMPLETE_SCENARIOS')
    comparisons={}
    pairs=(
        ('products','products_with_matrix','add_original_matrix'),
        ('products','products_doubled','double_expression_without_matrix'),
        ('products_with_matrix','products_doubled_with_matrix','double_expression_same_matrix'),
        ('products_with_matrix','products_with_matrix_hh_shift','change_matrix_same_expression'),
        ('products_doubled_with_matrix','products_doubled_with_matrix_hh_shift','change_matrix_doubled_expression'),
        ('products_with_matrix_hh_shift','products_doubled_with_matrix_hh_shift','double_expression_shifted_matrix'),
        ('products','products_with_numeric_context','add_unrelated_numbers'),
        ('products_with_matrix','products_with_matrix_explicitly_ignored','tell_model_matrix_is_irrelevant'),
    )
    for left,right,name in pairs:
        stats={}
        for scope,subset in (('all',scenarios.values()),('nonzero',(
                s for s in scenarios.values() if s['original_expected']!=0))):
            c=Counter()
            for s in subset:
                a=s['modes'][left];b=s['modes'][right]
                c['tested']+=1
                c['both_correct']+=int(a['numeric_correct'] and b['numeric_correct'])
                c['left_correct_right_wrong']+=int(a['numeric_correct'] and not b['numeric_correct'])
                c['left_wrong_right_correct']+=int(not a['numeric_correct'] and b['numeric_correct'])
                c['both_wrong']+=int(not a['numeric_correct'] and not b['numeric_correct'])
                if a['parsed_integer'] is not None and b['parsed_integer'] is not None:
                    c['numeric_pairs']+=1
                    c['unchanged_numeric_output']+=int(a['parsed_integer']==b['parsed_integer'])
                    c['response_delta_matches_target_delta']+=int(
                        b['parsed_integer']-a['parsed_integer']==b['expected']-a['expected'])
            stats[scope]=dict(c)
        comparisons[name]={'left':left,'right':right,**stats}
    factorial=[]
    for ident,s in scenarios.items():
        v=s['modes'];a=v['products_with_matrix']['parsed_integer']
        b=v['products_doubled_with_matrix']['parsed_integer']
        c=v['products_with_matrix_hh_shift']['parsed_integer']
        d=v['products_doubled_with_matrix_hh_shift']['parsed_integer']
        observed={'scenario':ident,'original_expected':s['original_expected'],
                  'matrix_changed_expression_fixed':c-a if a is not None and c is not None else None,
                  'expression_doubled_matrix_fixed':b-a if a is not None and b is not None else None,
                  'expression_doubled_shifted_matrix':d-c if c is not None and d is not None else None,
                  'interaction':d-c-b+a if all(x is not None for x in (a,b,c,d)) else None,
                  'matrix_derived_hh_shift_if_recomputed':2*s['hawk_count']}
        factorial.append(observed)
    return {'schema':COEFFICIENT_PROBE_VERSION,'checkpoint_step':int(step),
            'checkpoint_sha256':checkpoint_sha256,
            'evaluated_prompts':len(rows),'matched_scenarios':len(scenarios),
            'evaluation_only':True,'optimizer_updates':0,
            'interpretation':'Observed answer dependence; not proof of internal computation. Doubled-expression targets differ from original; original HDB08 training unaffected.',
            'contrasts':comparisons,'factorial_differences':factorial,
            'scenarios':scenarios}
