#!/usr/bin/env python3
"""Isolated, supervised, exact-response Hawk–Dove fixed-game GAMER head.

This is a *game-specific* acquired linear payoff head, not a neural natural-
language game constructor and not a modification of Qwen, LoRA, C0 or Wide.
Training observes numeric encounter counts and oracle cumulative X/Y as labels;
inference consumes only the saved learned payoff coefficients and prompt text.

The existing T08..T00 source grammar is reused unchanged. T08 English counts
are the head's input; T07..T00 use the source Lisp interface (including its
partially expanded branches). No hidden payoff oracle or fixture is read at
query time. Explicit numeric input parsing and exact learned arithmetic are
reported, not disguised as end-to-end neural reasoning.
"""
from __future__ import annotations

from fractions import Fraction
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import re

from tools import nanojev_hawk_dove_million as curriculum

SCHEMA = 'nanojev.gamer.v1.hawk-dove-t08-t00-acquired-linear-head.v1'
LEVELS = tuple(reversed(curriculum.TIERS))
VALUES = curriculum.TAGS
NUMBER = re.compile(r'[+-]?\d+(?:\.\d+)?\Z')
EXACT_GAME = re.compile(
    r'\AYou realize you are playing Hawk–Dove\.\s*'
    r'Your opponent plays Hawk (?P<a>\d+) out of a million times\.\s*'
    r'Your opponent plays Dove (?P<b>\d+) out of the same million times\.\s*'
    r'Which pure strategy gives you the greater total payoff across those encounters\?\Z',
    re.DOTALL,
)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1<<20), b''):
            h.update(block)
    return h.hexdigest()


def _teacher(fixture):
    """Training-only teacher, never imported or called by query()."""
    from tools.nanojev_gamer_v1_hawk_dove_teacher import DOCUMENTED_DEMO_BINDINGS
    p = {k:Decimal(v) for k,v in zip(VALUES, DOCUMENTED_DEMO_BINDINGS)}
    path=Path(fixture)
    if path.is_file():
        actual, meta=curriculum.read_fixture(path)
        if actual!=p:
            raise ValueError('HAWK_DOVE_TEACHER_DIFFERS_FROM_DOCUMENTED_FIXED_GAME')
        return p, {'kind':'EXISTING_IMMUTABLE_FIXTURE_VERIFIED', 'fixture_sha256':meta['sha256'],
                   'fixture_path':str(path)}
    # The exported repository snapshot omitted the original demo fixture.
    # This is an explicit documented fixed-game teacher, not a pretend restored file.
    if path!=curriculum.DEFAULT_FIXTURE:
        raise FileNotFoundError('HAWK_DOVE_EXPLICIT_TEACHER_FIXTURE_MISSING:'+str(path))
    return p, {'kind':'DOCUMENTED_DEMO_FIXED_BINDINGS_NO_FIXTURE_PRESENT',
               'binding_version':'documented-200000-800000-T08-example'}


def _signature(node):
    if isinstance(node,str):
        return '<NUMBER>' if NUMBER.fullmatch(node) else node
    return tuple(_signature(n) for n in node)


def _signatures():
    result={}
    for tier in curriculum.TIERS[:-2]:
        template=curriculum.PLACEHOLDER.sub('100',curriculum.TEMPLATES[tier])
        expr=template[template.index('(ARGMAX'):]
        sig=_signature(curriculum.parse_lisp(expr))
        if sig in result:
            raise AssertionError('HAWK_DOVE_AMBIGUOUS_TIER_SIGNATURE')
        result[sig]=tier
    return result


LISP_SIGNATURES = _signatures()


def _strip_numeric_annotations(prompt):
    """Demand source-certified LogBar/scientific forms; never discard invalid signs."""
    representation=(curriculum.REP_SCIENCE if re.search(r'\d(?:\.\d+)?e[+-]?\d+',prompt,re.I)
                    else curriculum.REP_LEGACY)
    return curriculum.strip_and_validate_bars(prompt, representation)


def _input(prompt):
    if not isinstance(prompt,str) or not prompt.strip():
        raise ValueError('HAWK_DOVE_EMPTY_PROMPT')
    if len(prompt)>8192:
        raise ValueError('HAWK_DOVE_PROMPT_TOO_LONG')
    plain=_strip_numeric_annotations(prompt).strip()
    if '(ARGMAX' not in plain:
        m=EXACT_GAME.fullmatch(plain)
        if m is None:
            raise ValueError('HAWK_DOVE_UNSUPPORTED_T08_LANGUAGE_FORM')
        a,b=int(m['a']),int(m['b'])
        _counts(a,b)
        return {'tier':'T08','a':a,'b':b,'tree':None}
    index=plain.index('(ARGMAX')
    english=plain[:index].strip()
    expression=plain[index:].strip()
    tree=curriculum.parse_lisp(expression)
    tier=LISP_SIGNATURES.get(_signature(tree))
    if tier is None:
        raise ValueError('HAWK_DOVE_UNSUPPORTED_LISP_FORM')
    if english:
        if tier!='T03':
            raise ValueError('HAWK_DOVE_ENGLISH_LISP_STRUCTURE_MISMATCH')
        m=EXACT_GAME.fullmatch(english)
        if m is None:
            raise ValueError('HAWK_DOVE_UNSUPPORTED_T07_ENGLISH_FORM')
        a,b=int(m['a']),int(m['b'])
        _counts(a,b)
        # The four count occurrences in the appended Lisp must match the
        # English observations, in payoff-symbol order.
        def operands(node, out):
            if isinstance(node,tuple) and len(node)==3 and node[0]=='MUL':
                for item in node[1:]:
                    if isinstance(item,str) and NUMBER.fullmatch(item):
                        out.append(int(Decimal(item)))
            if isinstance(node,tuple):
                for item in node[1:]:operands(item,out)
        seen=[]
        operands(tree,seen)
        if seen != [a,b,a,b]:
            raise ValueError('HAWK_DOVE_T07_COUNTS_CONTRADICT_ENGLISH')
        return {'tier':'T07','a':a,'b':b,'tree':tree}
    return {'tier':tier,'a':None,'b':None,'tree':tree}


def _counts(a,b):
    if a<0 or b<0 or a+b!=1_000_000:
        raise ValueError('HAWK_DOVE_COUNTS_MUST_SUM_TO_ONE_MILLION')



def _observed_counts(parsed):
    """Read A/B from the *prompt*, not from a teacher/case-id lookup."""
    if parsed['tier']=='T00':
        return None
    if parsed['tier']=='T08':
        return parsed['a'],parsed['b']
    observed={}
    def visit(node):
        if not isinstance(node,tuple):return
        if len(node)==3 and node[0]=='MUL':
            factors=node[1:]
            tags=[v for v in factors if isinstance(v,str) and v in VALUES]
            numerics=[v for v in factors if isinstance(v,str) and NUMBER.fullmatch(v)]
            if len(tags)!=1 or len(numerics)!=1:
                raise ValueError('HAWK_DOVE_MUL_BINDING_AMBIGUOUS')
            value=Decimal(numerics[0])
            if value!=value.to_integral_value() or value<0:
                raise ValueError('HAWK_DOVE_COUNT_MUST_BE_NONNEGATIVE_INTEGER')
            if tags[0] in observed:
                raise ValueError('HAWK_DOVE_PAYOFF_TAG_DUPLICATED')
            observed[tags[0]]=int(value)
            return
        for part in node[1:]:visit(part)
    visit(parsed['tree'])
    expected={
        'T01':{VALUES[2],VALUES[3]},
        'T02':{VALUES[0],VALUES[1]},
    }.get(parsed['tier'],set(VALUES))
    if set(observed)!=expected:
        raise ValueError('HAWK_DOVE_PAYOFF_TAGS_NOT_MATCHING_TIER')
    hawks={v for k,v in observed.items() if k in (VALUES[0],VALUES[2])}
    doves={v for k,v in observed.items() if k in (VALUES[1],VALUES[3])}
    if len(hawks)!=1 or len(doves)!=1:
        raise ValueError('HAWK_DOVE_REPEATED_LISP_COUNTS_DISAGREE')
    a,b=next(iter(hawks)),next(iter(doves))
    _counts(a,b)
    if parsed['tier']=='T07' and (a,b)!=(parsed['a'],parsed['b']):
        raise ValueError('HAWK_DOVE_T07_COUNTS_CONTRADICT_ENGLISH')
    return a,b

def _to_fraction(x):
    if not NUMBER.fullmatch(str(x)):
        raise ValueError('HAWK_DOVE_NONCANONICAL_LITERAL')
    return Fraction(str(x))


def _fmt(x):
    x=Fraction(x)
    return str(x.numerator) if x.denominator==1 else f'{x.numerator}/{x.denominator}'


def _eval(node, weights):
    if isinstance(node,str):
        if node in weights:return weights[node]
        return _to_fraction(node)
    op, *args=node
    if op not in ('ARGMAX','ADD','MUL') or len(args)!=2:
        raise ValueError('HAWK_DOVE_UNAUTHORIZED_OPERATOR')
    l,r=_eval(args[0],weights),_eval(args[1],weights)
    return (l,r) if op=='ARGMAX' else l+r if op=='ADD' else l*r


def infer(prompt, weights):
    """No teacher values or fixture; source grammar + acquired coefficients."""
    parsed=_input(prompt)
    if set(weights)!=set(VALUES):
        raise ValueError('HAWK_DOVE_HEAD_INCOMPLETE_BINDINGS')
    coeff={k:Fraction(v) for k,v in weights.items()}
    counts=_observed_counts(parsed)
    if parsed['tier']=='T00':
        x,y=_eval(parsed['tree'],coeff)
    else:
        a,b=counts
        expected_x=a*coeff[VALUES[0]]+b*coeff[VALUES[1]]
        expected_y=a*coeff[VALUES[2]]+b*coeff[VALUES[3]]
        if parsed['tier']=='T08':
            x,y=expected_x,expected_y
        else:
            x,y=_eval(parsed['tree'],coeff)
            if (x,y)!=(expected_x,expected_y):
                raise ValueError('HAWK_DOVE_LISP_AND_LEARNED_GAME_CONTRADICTION')
    decision='HAWK' if x>y else 'DOVE' if x<y else 'TIE'
    return {'tier':parsed['tier'],
            'opponent_hawk_count':counts[0] if counts is not None else None,
            'opponent_dove_count':counts[1] if counts is not None else None,
            'hawk_cumulative_payoff':_fmt(x),
            'dove_cumulative_payoff':_fmt(y), 'hawk_minus_dove':_fmt(x-y),
            'decision':decision, 'exact_rational_arithmetic':True,
            'learned_coefficients_used':parsed['tier']!='T00',
            'oracle_used_for_inference':False,
            'construction_mode':'STRICT_SOURCE_GRAMMAR_NUMERIC_ADAPTER_NOT_NEURAL_LANGUAGE_CONSTRUCTION'}


def _train_head(samples, epochs):
    """Adam fit on observed counts/cumulative outcomes; exact rational projection.

    No teacher coefficient is provided to this optimizer. It sees only A,B,X,Y
    and learns the four weights. Projection checks the fitted coefficients and
    every training outcome; it does not substitute an oracle parameter.
    """
    import numpy as np
    if not 1<=epochs<=10000:
        raise ValueError('HAWK_DOVE_INVALID_EPOCHS')
    input_rows=np.asarray([[a/1_000_000,b/1_000_000] for a,b,_,_ in samples],dtype=np.float64)
    targets=np.asarray([[float(x)/1_000_000,float(y)/1_000_000] for _,_,x,y in samples],dtype=np.float64)
    w=np.zeros((2,2),dtype=np.float64)
    m=np.zeros_like(w);v=np.zeros_like(w);lr=0.1
    for i in range(1,epochs+1):
        diff=input_rows@w.T-targets
        grad=(2/len(samples))*diff.T@input_rows
        m=.9*m+.1*grad
        v=.999*v+.001*(grad**2)
        w-=lr*(m/(1-.9**i))/(np.sqrt(v/(1-.999**i))+1e-8)
    # A fixed, small rational family is the intended hypothesis class for this
    # source's exact half-point game. Projection cannot introduce a new answer:
    # every candidate must reproduce all independently computed training X/Y.
    projected=[Fraction(float(value)).limit_denominator(32) for value in w.flat]
    if max(abs(float(estimate)-float(value)) for estimate,value in zip(projected,w.flat))>0.002:
        raise ValueError('HAWK_DOVE_TRAINING_NOT_CONVERGED_REFUSE_PROMOTION')
    result={VALUES[0]:projected[0],VALUES[1]:projected[1],
            VALUES[2]:projected[2],VALUES[3]:projected[3]}
    for a,b,x,y in samples:
        if (a*result[VALUES[0]]+b*result[VALUES[1]],
            a*result[VALUES[2]]+b*result[VALUES[3]])!=(Fraction(x),Fraction(y)):
            raise ValueError('HAWK_DOVE_LEARNED_HEAD_TRAINING_TARGET_MISMATCH')
    return {k:_fmt(value) for k,value in result.items()},float(np.mean((input_rows@w.T-targets)**2))


def _make_t08(a,b):
    return curriculum.TEMPLATES['T08'].replace('{{a0}}',str(a)).replace('{{b0}}',str(b))


def _score_case(a,b, teacher, weights, *, tier, annotation_mode='raw'):
    true_x,true_y,true_z=curriculum.oracle(a,b,teacher)
    if tier=='T08' and (a%2):
        # Unseen exact-zero neighbors can have fractional hidden totals. The
        # existing renderer restricts all tiers to integral LogBar values.
        prompt=_make_t08(a,b)
    else:
        if annotation_mode=='raw':
            prompt=curriculum.render(tier,a,b,teacher)
        else:
            slots=curriculum.SLOTS[tier]
            prompt=curriculum.render(tier,a,b,teacher,full_slots=slots,
                                     representation=(curriculum.REP_SCIENCE if annotation_mode=='science'
                                                     else curriculum.REP_LEGACY))
    prediction=infer(prompt,weights)
    return prediction['hawk_cumulative_payoff']==_fmt(Fraction(true_x)) and \
           prediction['dove_cumulative_payoff']==_fmt(Fraction(true_y)) and \
           prediction['decision']==true_z


def train(output, *, epochs=200, seed=47, fixture=curriculum.DEFAULT_FIXTURE, step=1000):
    output=Path(output)
    if output.exists():raise FileExistsError('HAWK_DOVE_T08_HEAD_ALREADY_EXISTS:'+str(output))
    if step<2 or step%2 or 1_000_000%step or step>100_000:
        raise ValueError('HAWK_DOVE_STEP_MUST_BE_EVEN_DIVISOR_OF_MILLION_IN_2_TO_100000')
    teacher, provenance=_teacher(fixture)
    train_a=list(range(0,1_000_001,step))
    samples=[]
    acquired_cases=[]
    observed_views={tier:0 for tier in LEVELS}
    for a in train_a:
        b=1_000_000-a
        x,y,_=curriculum.oracle(a,b,teacher)
        # Every tier's real source text is admitted to the curriculum; no
        # example is supplied a magic game ID or the teacher payoff weights.
        for tier in LEVELS:
            prompt=curriculum.render(tier,a,b,teacher)
            parsed=_input(prompt)
            observed_views[tier]+=1
            # Durable, regeneration-safe acquisition evidence. The head sees
            # numeric count features only; answer labels are not query features.
            acquired_cases.append({'tier':tier,'hawk_count':a,'dove_count':b,
                                   'prompt':prompt,'target_hawk':curriculum.canonical_num(x),
                                   'target_dove':curriculum.canonical_num(y),
                                   'target_choice':curriculum.oracle(a,b,teacher)[2]})
            if tier=='T00':
                # T00 already exposes X/Y; it is evaluated but cannot be a
                # game-payoff training feature without leaking the answer.
                assert _eval(parsed['tree'],{})==(Fraction(x),Fraction(y))
                continue
            counts=_observed_counts(parsed)
            if counts!=(a,b):
                raise ValueError('HAWK_DOVE_TIER_COUNT_INPUT_MISMATCH')
            samples.append((counts[0],counts[1],x,y))
    print(f'GAMER HAWK-DOVE: fitting four fixed-game payoff weights from {len(samples)} certified count pairs; no Qwen/LoRA changes',flush=True)
    weights,optimizer_mse=_train_head(samples,epochs)
    # Re-check entire acquired set with a genuine T08 text query and independently
    # generated teacher X/Y. No teacher or gold label enters infer().
    train_correct=sum(_score_case(a,1_000_000-a,teacher,weights,tier='T08') for a in train_a)
    # No permanent holdout: fresh mid-grid counts are measured before acquisition.
    # A future training cycle can incorporate these, while the accepted head
    # retains the entire previously acquired training grid.
    train_set=set(train_a)
    probe_a=[a for a in range(step//2,1_000_000,step) if a not in train_set] if step>=2 else []
    probe_correct=sum(_score_case(a,1_000_000-a,teacher,weights,tier='T08') for a in probe_a)
    boundary=[599999,600000,600001]
    boundary_ok=sum(_score_case(a,1_000_000-a,teacher,weights,tier='T08') for a in boundary)
    # Full raw T08→T00 ladder and source LogBar/scientific mutation views.
    audited=[]
    source_test_a=sorted(set([0,200000,400000,600000,800000,940000,1000000]+probe_a[::max(1,len(probe_a)//24)]))
    for tier in LEVELS:
        correct=0;cases=0
        for a in source_test_a:
            b=1_000_000-a
            for mode in ('raw','logbar','science'):
                cases+=1
                correct+=int(_score_case(a,b,teacher,weights,tier=tier,annotation_mode=mode))
        audited.append({'tier':tier,'correct':correct,'cases':cases})
    if train_correct!=len(train_a) or probe_correct!=len(probe_a) or boundary_ok!=3 or any(x['correct']!=x['cases'] for x in audited):
        raise ValueError('HAWK_DOVE_FAILED_FULL_RECALL_OR_LADDER_REFUSE_CHECKPOINT')
    result={
        'status':'PASS_ISOLATED_HAWK_DOVE_T08_T00_ACQUISITION', 'schema':SCHEMA,
        'game_id':'fixed_hawk_dove_million_counts',
        'training':{'epochs':epochs,'optimizer':'float64_adam_then_exact_rational_projection_checked_against_all_training_XY',
                    'training_count_pairs':len(train_a),'count_bearing_tier_training_presentations':len(samples),
                    'all_tier_presentations_ingested':observed_views,'training_step':step,'adam_normalized_mse':optimizer_mse},
        'teacher':provenance,'teacher_bindings_never_used_as_model_features':True,
        'learned_payoff_coefficients':weights,
        'acquired_t08_recall':{'correct':train_correct,'cases':len(train_a)},
        'fresh_t08_probe':{'correct':probe_correct,'cases':len(probe_a),
                           'scope':'UNSEEN_INTERSTITIAL_COUNTS_SAME_GAME'},
        'exact_zero_boundary':{'correct':boundary_ok,'cases':3,
                               'hawk_counts':[599999,600000,600001]},
        'full_ladder':audited,
        'source_code_sha256':sha_file(Path(curriculum.__file__)),
        'no_qwen_lora_c0_wide_training':True,
        'no_production_authority_change':True,
        'model_checkpoint_used_for_decision':True,
        'language_generalization_certified':False,
        'decision_rule':'LEARNED_PAYOFF_COEFFICIENTS_EXACT_SOURCE_GRAMMAR_EXECUTION',
        'limitations':'One fixed Hawk–Dove game; the numeric input adapter and source grammar are deterministic. '
                      'No learned free-form language parser, no general multi-game strategy, and no Qwen weight update.',
    }
    # Only create destination after every gate passes. No replacement.
    output.mkdir(parents=True,exist_ok=False)
    ledger=output/'acquired_cases.jsonl'
    with ledger.open('w',encoding='utf-8',newline='\n') as f:
        for row in acquired_cases:
            f.write(canonical(row)+'\n')
    result['acquisition_ledger']={'path':str(ledger.resolve()),
                                   'sha256':sha_file(ledger),
                                   'cases':len(acquired_cases),
                                   'contains_oracle_targets_for_training_only':True,
                                   'not_read_by_query':True}
    checkpoint=output/'hawk_dove_head.json'
    checkpoint.write_text(canonical({'schema':SCHEMA,'weights':weights,'source_code_sha256':result['source_code_sha256']})+'\n',encoding='utf-8')
    result.update(checkpoint=str(checkpoint.resolve()),checkpoint_sha256=sha_file(checkpoint))
    (output/'final_report.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    return result


def query(output, prompt):
    """Never call _teacher, oracle, read_fixture, renderer, or train()."""
    checkpoint=Path(output)/'hawk_dove_head.json'
    report_path=Path(output)/'final_report.json'
    if not checkpoint.is_file() or not report_path.is_file():
        raise FileNotFoundError('HAWK_DOVE_T08_CHECKPOINT_OR_REPORT_MISSING')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if sha_file(checkpoint)!=report['checkpoint_sha256']:
        raise ValueError('HAWK_DOVE_CHECKPOINT_FINGERPRINT_MISMATCH')
    state=json.loads(checkpoint.read_text(encoding='utf-8'))
    if state.get('schema')!=SCHEMA or set(state.get('weights',{}))!=set(VALUES):
        raise ValueError('HAWK_DOVE_CHECKPOINT_SCHEMA_OR_WEIGHT_MISMATCH')
    if state['source_code_sha256']!=sha_file(Path(curriculum.__file__)):
        raise ValueError('HAWK_DOVE_GRAMMAR_SOURCE_CHANGED_SINCE_TRAINING')
    result=infer(prompt,state['weights'])
    return {'status':'ACQUIRED_FIXED_GAME_DECISION',
            'schema':SCHEMA,'game_id':'fixed_hawk_dove_million_counts',
            'checkpoint_sha256':report['checkpoint_sha256'],
            'learned_fixed_game_head':True,'decision_source':'LEARNED_GAME_PAYOFF_HEAD_PLUS_EXACT_ARITHMETIC',
            'no_teacher_fixture_or_sqlite_access_during_query':True,
            'no_frozen_qwen_or_lora_inference':True,
            'production_numerical_authority_changed':False,
            'result':result}
