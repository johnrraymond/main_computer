#!/usr/bin/env python3
"""Gamer v1: deterministic, source-certified 33-game *08 -> sparse math table pairs.

Only the 33 oracle-executable source games are eligible. Table arithmetic is
reconstructed with a shared, generic arithmetic DAG; no game-specific numeric
calculation is embedded in its evaluator. Source oracles are checked separately.
The output table is TEXT SUPERVISION only, never an input to to_numerical.
"""
from __future__ import annotations

import ast
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
import os

from tools import nanojev_game_transformation_library as src
from tools import nanojev_game_native_objectives_v6 as native

SCHEMA = 'nanojev.gamer.v1.certified-sparse-table-pairs.v1'
PAIR_SCHEMA = 'nanojev.gamer.v1.oracle-table-pair.v1'
OPS = {ast.Add: ('add',lambda a,b:a+b), ast.Sub: ('subtract',lambda a,b:a-b),
       ast.Mult: ('multiply by',lambda a,b:a*b), ast.Div: ('divide by',lambda a,b:a/b)}
ZERO_GAMES = frozenset({'quorum_margin','captain_signed_offset','coalition_surplus',
                        'lanchester_square','population_signal','pursuit_gap',
                        'second_price_utility','captain_fuel_reserve'})

# Source-anchored English prose. The prompt provides the relevant game rules and
# all numeric observations, without precomputing any steps or teaching a new DSL.
SCENARIOS = {
'hawk_dove_mixed':('A population alternates between Hawk and Dove behavior. A contested resource is worth {V} points, and the cost of an all-Hawk conflict is {C} points. In the standard interior Hawk–Dove population model, what is the stable proportion of Hawks?', 'the stable Hawk fraction'),
'nash_bargain_split':('Two parties divide a total worth {V} units. The first party can obtain {d1} units without an agreement, and the second can obtain {d2} units without an agreement. Under the symmetric Nash bargaining split, each receives half the surplus above those disagreement positions. How much does the first party receive?', 'the first party’s total allocation'),
'bayes_posterior':('A hidden condition is present with prior probability {prior}. A signal is observed with probability {l_true} when that condition is present, and probability {l_false} when it is absent. Given that the signal occurred, what is the posterior probability that the condition is present?', 'the posterior probability given the signal'),
'discounted_repeat':('A cooperation agreement pays {r} points each period forever. Every successive period is weighted by a discount factor of {gamma} relative to the previous one. What is the total present value of the continuing reward stream?', 'the discounted infinite-horizon reward'),
'markov_one_step':('A decision pays an immediate reward of {r}. The next state is good with probability {p} and value {v_good}, or bad otherwise with value {v_bad}. Future value is discounted by {gamma}. What is the current reward plus the discounted expected next-state value?', 'the one-step expected state-action value'),
'lanchester_square':('Two opposing forces have effective strengths {A} and {B}. Under the square-law attrition comparison, the advantage is determined by the difference between the squares of their strengths. What is the signed square-law advantage of the first force?', 'the signed square-law advantage'),
'blotto_remaining':('A commander starts with an allocation budget of {B} resource units and commits {x1} to one front and {x2} to another. How many units remain uncommitted? The allocation must not exceed the budget.', 'the remaining budget'),
'inspection_loss':('An attack occurs with probability {attack_probability}. Conditional on an attack, a defender detects it with probability {detection_probability}; an undetected attack causes {damage} points of damage. What is the expected damage from undetected attacks?', 'the expected undetected-attack damage'),
'pursuit_gap':('An evader starts {gap} distance units ahead. Over the next equal time interval the evader moves {evader_speed} units forward, while the pursuer moves {pursuer_speed} units forward. What is their signed separation after the interval?', 'the signed next separation'),
'routing_delay':('A network route has a baseline delay of {base} time units. Every unit of traffic adds {congestion_coefficient} time units of delay, and {flow} units of traffic use the route. What is the total route delay?', 'the total congestion-adjusted delay'),
'second_price_utility':('An auction winner values an item at {value} points and must pay the second-highest bid of {second_price} points. What is the winner’s net utility?', 'the winning bidder’s net utility'),
'coalition_surplus':('A merged coalition produces total value {v_merged}. Its two constituent groups could independently produce {v_one} and {v_two}. What additional surplus is created by their merger compared with remaining separate?', 'the additional coalition surplus'),
'quorum_margin':('A council has {honest_votes} supporting votes. A decision requires a quorum of {quorum} votes. How many votes above or below the threshold does the council stand?', 'the signed margin above quorum'),
'population_signal':('A population interaction produces benefit {benefit} points but incurs {cost_per_agent} points of cost for each of {n} participating agents. What is the net benefit after all participation costs?', 'the net population benefit'),
'cournot_quantity':('Two identical firms choose output simultaneously. The market inverse-demand intercept is {a}, the inverse-demand slope is {b}, and each firm’s unit marginal cost is {c}. For the symmetric interior Cournot equilibrium with linear inverse demand, what is the output of one firm?', 'one firm’s symmetric interior Cournot quantity'),
'robust_minimum':('A defender faces losses of {loss_case_1} points in the first scenario and {loss_case_2} points in the second. For a conservative worst-case assessment, what is the larger possible loss?', 'the worst-case loss'),
'captain_fuel_reserve':('A spacecraft begins with {initial} fuel units, must protect a reserve of {reserve_required} units, and expects to spend {fuel_spent} units on a maneuver. What fuel will remain beyond the protected reserve?', 'the signed fuel available after reserve and spending'),
'captain_crew_risk':('A captain is responsible for {crew_count} people. Each faces a {casualty_probability} probability of casualty in the planned operation. What is the expected number of people who survive?', 'the expected number of survivors'),
'captain_signed_offset':('A spacecraft is at signed coordinate {ship_offset}, while a navigation beacon is at signed coordinate {beacon_offset} on the same axis. What is the spacecraft’s signed position relative to the beacon?', 'the signed relative coordinate'),
}
assert set(SCENARIOS)==set(src.SCALAR_MODELS)==set(native.SCALAR_FORMULAS)


def canon(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(x):
    return hashlib.sha256(canon(x).encode('utf-8')).hexdigest()


def number(x):
    if isinstance(x, float):
        return repr(x)
    f = x if isinstance(x,Fraction) else Fraction(str(x))
    return str(f.numerator) if f.denominator == 1 else (str(float(f)) if len(str(f.denominator))>8 else f'{f.numerator}/{f.denominator}')


def frac(x):
    return Fraction(str(x))


def source_signature():
    return sha({'matrices':src.MATRICES,'scalar_models':src.SCALAR_MODELS,
                'source_formulas':native.SCALAR_FORMULAS,'english_prose':SCENARIOS})


def sample_scalar(game, index, seed, attempt=0):
    name,_,_,base,changed,increment = src.SCALAR_MODELS[game]
    base = dict(base)
    # Values remain human-readable and distinguishable. Candidates are checked
    # against the original source oracle, rather than assuming every perturbation
    # satisfies each domain (Hawk interior, Blotto budget, etc.).
    rng=random.Random(int(sha([seed,game,index,attempt])[:16],16))
    for trial in range(200):
        p={}
        for k,v in base.items():
            if k in ('prior','l_true','l_false','gamma','p','attack_probability','detection_probability','casualty_probability'):
                p[k]=round(max(.05,min(.95, v+rng.choice([-3,-2,-1,0,1,2,3])*.035)),3)
            elif k in ('b','C','cost_per_agent','congestion_coefficient'):
                p[k]=round(max(.1,v+rng.choice([-2,-1,0,1,2])*min(abs(v)*.10,2)),4)
            else:
                spread = max(1.0,min(abs(v)*.18,20.0))
                p[k]=round(v+rng.choice([-4,-3,-2,-1,0,1,2,3,4])*spread,4)
        # Explicitly sample the mathematical boundary where the given source
        # objective has a signed, physically meaningful comparison.
        if game in ZERO_GAMES:
            side=(-1,0,1)[(index//6*2+index%6)%3]
            if game=='quorum_margin':p['honest_votes']=max(1,round(p['quorum']))+side
            elif game=='captain_signed_offset':p['ship_offset']=p['beacon_offset']+side
            elif game=='coalition_surplus':p['v_merged']=p['v_one']+p['v_two']+side
            elif game=='lanchester_square':p['B']=max(2.,abs(p['B']));p['A']=p['B']+side
            elif game=='population_signal':p['benefit']=round(p['cost_per_agent']*p['n']+side,4)
            elif game=='pursuit_gap':p['gap']=p['pursuer_speed']-p['evader_speed']+side
            elif game=='second_price_utility':p['value']=p['second_price']+side
            elif game=='captain_fuel_reserve':p['initial']=p['reserve_required']+p['fuel_spent']+side
        try:
            answer=src.scalar_oracle(game,p)
            if not math.isfinite(answer):continue
            native._validate_facts(native._facts('scalar',game,name,{'parameters':p},'scalar_value'))
            return p
        except (ValueError,ZeroDivisionError,OverflowError):
            pass
    raise ValueError(f'GAMER_NO_VALID_SCALAR_PARAMETERS: {game} #{index}')


def sample_matrix(game,index,seed,attempt=0):
    name,family,rows,cols,base = src.MATRICES[game]
    rng=random.Random(int(sha([seed,game,index,attempt])[:16],16))
    factor=rng.choice([1,2,3,4,5])
    addrow=rng.randrange(-4,5)
    addcol=rng.randrange(-4,5)
    U=[[[factor*base[r][c][0]+addrow, factor*base[r][c][1]+addcol]
         for c in range(2)] for r in range(2)]
    rival=index%2
    # Set the signed advantage across -1,0,+1 without changing the game's
    # information structure. These are explicitly labelled payoff variants.
    side=(-1,0,1)[(index//6*2+index%6)%3]
    U[0][rival][0]=U[1][rival][0]+side
    return {'U':U,'row_actions':rows,'column_actions':cols,
            'rival_index':rival,'base_affine_scale':factor,'scenario_variant':'local_payoff_variant'}



# Optional blind-matrix curriculum. These generators DO NOT read game payoff
# tensors. The source game ID remains private provenance, never model input.
# Regime choice is rotated across game IDs and the train/dev/test split so no
# evaluation split is restricted to a single regime.
BLIND_MATRIX_REGIMES = (
    'independent', 'zero_sum', 'symmetric', 'coordination',
    'anti_coordination', 'exact_tie', 'signed_neighbor',
    'large_cancellation', 'positive_affine',
)


def sample_blind_matrix(game, index, seed, attempt=0):
    """Sample a completely novel 2x2 payoff tensor, independent of its game.

    The game ID only provides certified provenance and deterministic sample
    identity. The prompt/target never reveals it. Test magnitudes intentionally
    extend beyond the training magnitudes: this is an OOD probe, not a claim of
    universal game competence.
    """
    if game not in src.MATRICES:
        raise ValueError('GAMER_BLIND_MATRIX_REQUIRES_MATRIX_SOURCE')
    game_ordinal=sorted(src.MATRICES).index(game)
    rng=random.Random(int(sha(['blind-matrix-v1',seed,game,index,attempt])[:16],16))
    split_index=index%6
    regime=BLIND_MATRIX_REGIMES[(game_ordinal*5 + index//6 + split_index*3) % len(BLIND_MATRIX_REGIMES)]
    magnitude=(1,10,100,1000)[rng.randrange(4)] if split_index!=5 else (10000,100000,1000000)[rng.randrange(3)]
    rival=rng.randrange(2)

    def value():
        return rng.randrange(-9,10)*magnitude+rng.randrange(-3,4)

    U=[[[value(),value()] for _ in range(2)] for _ in range(2)]
    if regime=='zero_sum':
        for row in U:
            for cell in row:
                cell[1]=-cell[0]
    elif regime=='symmetric':
        for i in range(2):
            for j in range(2):
                U[i][j][1]=U[j][i][0]
    elif regime in ('coordination','anti_coordination'):
        for i in range(2):
            for j in range(2):
                bonus=magnitude*(2+rng.randrange(1,7))
                sign=(1 if i==j else -1) if regime=='coordination' else (-1 if i==j else 1)
                U[i][j][0]=sign*bonus+rng.randrange(-3,4)
        for i in range(2):
            for j in range(2):
                U[i][j][1]=U[j][i][0]
    elif regime in ('exact_tie','signed_neighbor','large_cancellation'):
        side = 0 if regime=='exact_tie' else (-1,0,1)[(game_ordinal+index//6)%3]
        if regime=='large_cancellation':
            large=rng.choice([-1,1])*(10000*magnitude+rng.randrange(100))
            U[1][rival][0]=large
        U[0][rival][0]=U[1][rival][0]+side
    elif regime=='positive_affine':
        scale=rng.choice((2,3,5,7))
        shift_row=value();shift_column=value()
        for row in U:
            for cell in row:
                cell[0]=scale*cell[0]+shift_row
                cell[1]=scale*cell[1]+shift_column

    row_actions=['Action A','Action B']
    column_actions=['Move X','Move Y']
    # Permute both axes while preserving the meaning of the chosen opponent
    # move and the corresponding row best response.
    if rng.getrandbits(1):
        U.reverse();row_actions.reverse()
    if rng.getrandbits(1):
        U=[list(reversed(row)) for row in U]
        column_actions.reverse();rival=1-rival
    return {'U':U,'row_actions':row_actions,'column_actions':column_actions,
            'rival_index':rival,'blind_matrix_regime':regime,
            'scenario_variant':'blind_independent_payoff_matrix',
            'blind_matrix':True, 'sample_magnitude':magnitude}


def evaluate_factors(factors, final):
    """Generic exact arithmetic reconstruction; never evaluates a hidden game formula."""
    memory={}
    for row in factors:
        step=str(row['step'])
        if step in memory:raise ValueError('GAMER_DUPLICATE_FACTOR_STEP')
        def operand(x):
            if isinstance(x,str) and x.startswith('step '):
                dep=x[5:]
                if dep not in memory:raise ValueError('GAMER_MISSING_FACTOR_DEPENDENCY')
                return memory[dep]
            return frac(x)
        a,b=operand(row['left']),operand(row['right'])
        if row['operation']=='larger of':value=max(a,b)
        elif row['operation']=='choose second action if larger':value=Fraction(int(b>a))
        else:
            candidates=[v for v in OPS.values() if v[0]==row['operation']]
            if len(candidates)!=1:raise ValueError('GAMER_UNKNOWN_FACTOR_OPERATION')
            value=candidates[0][1](a,b)
        memory[step]=value
    if str(final) not in memory:raise ValueError('GAMER_MISSING_FINAL_FACTOR')
    return memory[str(final)]


def factorize_scalar(game,p):
    root=native._expr(game)
    steps=[]
    memo={}
    def build(node):
        if isinstance(node,ast.Name):return number(p[node.id])
        if isinstance(node,ast.Constant):return number(node.value)
        if isinstance(node,ast.Call):
            op='larger of';args=node.args
        elif isinstance(node,ast.BinOp):
            op=OPS[type(node.op)][0];args=[node.left,node.right]
        else:raise ValueError('GAMER_UNSUPPORTED_AST')
        key=ast.dump(node,include_attributes=False)
        if key in memo:return memo[key]
        a=build(args[0]);b=build(args[1]);sid=len(steps)+1
        steps.append({'step':sid,'left':a,'operation':op,'right':b})
        ref='step '+str(sid)
        memo[key]=ref
        return ref
    last=build(root)
    assert last=='step '+str(len(steps))
    return steps,len(steps)


def factorize_matrix(U,rival):
    # Both raw payoffs are present; a shared two-value choice factor yields
    # numerical action index (0 for first, 1 for second) with a fixed tie rule.
    first=U[0][rival][0];second=U[1][rival][0]
    return [{'step':1,'left':first,'operation':'choose second action if larger','right':second}],1


def render_factors(steps):
    """One sparse, human-mathematical factor table shared by every game."""
    columns=['Add','Subtract','Multiply','Divide','Maximum','Action comparison']
    lookup={'add':0,'subtract':1,'multiply by':2,'divide by':3,
            'larger of':4,'choose second action if larger':5}
    symbols=['+','−','×','÷','maximum of','compare']
    lines=['| Step | '+' | '.join(columns)+' | Result |',
           '|---:|' + '---|'*len(columns) + '---|']
    for r in steps:
        cells=['–']*len(columns)
        idx=lookup[r['operation']]
        a,b=r['left'],r['right']
        if idx==4: expr=f'maximum of {a} and {b}'
        elif idx==5:expr=f'{a} versus {b}'
        else:expr=f'{a} {symbols[idx]} {b}'
        cells[idx]=expr
        lines.append(f"| {r['step']} | "+' | '.join(cells)+' | – |')
    return '\n'.join(lines)


def render_scalar(game,p,steps):
    name=src.SCALAR_MODELS[game][0]
    spec=SCENARIOS[game]
    lines=[f'Mathematical calculation for {name}.',
           f'Find {spec[1]}. The observations are:',
           '| Quantity | Numerical value |','|---|---:|']
    for key,value in sorted(p.items()):lines.append(f'| {key.replace("_"," ")} | {number(value)} |')
    if game=='markov_one_step':
        lines.extend(['','Possible next states (use the probability-weighted values):',
                      '| Next state | Probability | Value | Discount |', '|---|---:|---:|---:|',
                      f"| Good | {number(p['p'])} | {number(p['v_good'])} | {number(p['gamma'])} |",
                      f"| Bad | 1 − {number(p['p'])} | {number(p['v_bad'])} | {number(p['gamma'])} |"])
    elif game=='bayes_posterior':
        lines.extend(['','Bayesian evidence:', '| Condition | Prior probability | Signal likelihood | Joint probability |',
                      '|---|---:|---:|---|',
                      f"| Present | {number(p['prior'])} | {number(p['l_true'])} | – |",
                      f"| Absent | 1 − {number(p['prior'])} | {number(p['l_false'])} | – |"])
    elif game=='inspection_loss':
        lines.extend(['','Risk factors:', '| Event | Probability | Damage | Expected contribution |',
                      '|---|---:|---:|---|',
                      f"| Attack, not detected | {number(p['attack_probability'])} × (1 − {number(p['detection_probability'])}) | {number(p['damage'])} | – |"])
    lines.extend(['','Apply these reusable arithmetic factors in order. A reference to a step means its computed result. The dash marks a result still to calculate:',
                  render_factors(steps), '',f'Report the numerical value of step {len(steps)}. Do not fill in the result cells here.'])
    return '\n'.join(lines)


def render_matrix(game,f,steps):
    name=src.MATRICES[game][0]
    rival=f['rival_index'];ra=f['row_actions'];ca=f['column_actions'];U=f['U']
    blind=bool(f.get('blind_matrix'))
    lines=[('Mathematical payoff comparison for an unnamed two-player game.' if blind
            else f'Mathematical payoff comparison for {name}.'),
           ('All four possible numerical outcomes are supplied.' if blind
            else 'This is a hypothetical payoff variant of the named two-player game. All four possible outcomes are supplied.'),
           'The opponent chooses '+ca[rival]+'. Compare your two action payoffs against that choice.',
           '| Your action | Opponent action | Your payoff | Opponent payoff |',
           '|---|---|---:|---:|']
    for i in range(2):
        for j in range(2):lines.append(f'| {ra[i]} | {ca[j]} | {number(U[i][j][0])} | {number(U[i][j][1])} |')
    lines.extend(['','Required numerical calculation:',render_factors(steps),'',
                  f'If the first action has a payoff at least as large as the second, return numerical action index 0 ({ra[0]}). Otherwise return numerical action index 1 ({ra[1]}).',
                  'The result cell remains blank until the numerical problem is solved.'])
    return '\n'.join(lines)


def render_original_scalar(game,p):
    text=SCENARIOS[game][0]
    return text.format(**{k:number(v) for k,v in p.items()})


def render_original_matrix(game,f):
    name=src.MATRICES[game][0];ra=f['row_actions'];ca=f['column_actions'];U=f['U']
    lines=[('Consider an unnamed two-player game with these particular rewards and penalties.'
            if f.get('blind_matrix') else f'Consider a two-player {name} game with these particular rewards and penalties.'),
           f'You can choose {ra[0]} or {ra[1]}. Your opponent can choose {ca[0]} or {ca[1]}.',
           'The possible outcomes are:']
    for i in range(2):
        for j in range(2):
            lines.append(f'If you choose {ra[i]} and your opponent chooses {ca[j]}, you receive {number(U[i][j][0])} points and your opponent receives {number(U[i][j][1])} points.')
    lines.append(f'Your opponent chooses {ca[f["rival_index"]]}. Which of your two actions gives you a greater reward? If they tie, choose the first action listed. Answer by the numerical index: 0 for {ra[0]}, 1 for {ra[1]}.')
    return '\n'.join(lines)


def residual_scalar(game,p,answer):
    if game not in ZERO_GAMES:return None
    if game=='lanchester_square':return number(p['A']-p['B'])
    return number(answer)


def generate_case(game,index,seed,attempt=0,*,blind_matrix=False):
    if blind_matrix and game not in src.MATRICES:
        raise ValueError('GAMER_BLIND_MATRIX_REQUIRES_MATRIX_SOURCE')
    if game in src.SCALAR_MODELS:
        p=sample_scalar(game,index,seed,attempt)
        factors,last=factorize_scalar(game,p)
        computed=evaluate_factors(factors,last)
        oracle=src.scalar_oracle(game,p)
        original=render_original_scalar(game,p)
        math_prompt=render_scalar(game,p,factors)
        state={'kind':'scalar','parameters':p}
        residual=residual_scalar(game,p,float(computed))
        tables=['calculation']+(['probabilistic_outcomes'] if game in ('bayes_posterior','markov_one_step','inspection_loss') else [])
    else:
        f=(sample_blind_matrix(game,index,seed,attempt) if blind_matrix
           else sample_matrix(game,index,seed,attempt))
        factors,last=factorize_matrix(f['U'],f['rival_index'])
        computed=Fraction(0 if f['U'][0][f['rival_index']][0]>=f['U'][1][f['rival_index']][0] else 1)
        # Generic reconstruction returns the requested numerical action index.
        reconstructed=evaluate_factors(factors,last)
        if computed != reconstructed:raise ValueError('GAMER_MATRIX_TABLE_MISMATCH')
        # Independent source-native operator verifies the same choice and tie
        # policy against the payoff tensor, not merely our generic DAG.
        native_facts=native._facts('matrix',game,src.MATRICES[game][0],
                                  {'U':f['U'],'row_actions':f['row_actions'],
                                   'column_actions':f['column_actions']},'best_response',f['rival_index'])
        native._validate_facts(native_facts)
        *_,native_answer,native_meaning,_,_=native._compute(native_facts)
        if int(native_answer)!=int(computed) or native_meaning!=f['row_actions'][int(computed)]:
            raise ValueError('GAMER_MATRIX_SOURCE_OPERATOR_MISMATCH')
        oracle=float(computed)
        original=render_original_matrix(game,f)
        math_prompt=render_matrix(game,f,factors)
        state={'kind':'matrix',**f}
        residual=number(f['U'][0][f['rival_index']][0]-f['U'][1][f['rival_index']][0])
        tables=['strategic_payoff','calculation']
    if game in src.SCALAR_MODELS and not math.isclose(float(computed),float(oracle),rel_tol=1e-10,abs_tol=1e-9):
        raise ValueError(f'GAMER_ORACLE_TABLE_DISAGREEMENT: {game}')
    row={'schema':PAIR_SCHEMA,'generator_schema':SCHEMA,'game_id':game,
         'split':('train','train','train','train','dev','test')[index%6],
         'prompt_08':original,'math_prompt':math_prompt,'answer':repr(float(computed)),
         'source':state,'factor_steps':factors,'factor_final_step':last,
         'table_families':tables,'oracle_verified':True,'table_verified':True,
         'source_registry_sha256':source_signature(),
         'case_index':index,'seed':seed,'attempt':attempt}
    if residual is not None:row['zero_line_residual']=residual
    if blind_matrix:
        row['blind_matrix']=True
        row['blind_matrix_regime']=f['blind_matrix_regime']
    row['oracle_fingerprint']=sha({'source':state,'game':game,'result':repr(float(computed)),'source_registry_sha256':row['source_registry_sha256']})
    row['table_fingerprint']=hashlib.sha256(math_prompt.encode('utf-8')).hexdigest()
    row['case_sha256']=sha({k:v for k,v in row.items() if k!='case_sha256'})
    return row


def certify(row):
    """Reject altered inputs, labels, or provenance; regenerate from the source."""
    if row.get('generator_schema')!=SCHEMA:raise ValueError('GAMER_UNRECOGNIZED_GENERATOR_PROVENANCE')
    if row.get('source_registry_sha256')!=source_signature():raise ValueError('GAMER_SOURCE_REGISTRY_DRIFT')
    if row.get('game_id') not in set(src.MATRICES)|set(src.SCALAR_MODELS):raise ValueError('GAMER_BAD_SOURCE_GAME')
    expected=generate_case(row['game_id'],row['case_index'],row['seed'],row['attempt'],
                           blind_matrix=(row.get('blind_matrix') is True))
    if canon(row)!=canon(expected):raise ValueError('GAMER_GENERATED_CASE_MISMATCH_OR_TAMPER')
    return True


def write_dataset(dataset, *, cases_per_game=18, seed=47, blind_cases_per_matrix=0):
    if cases_per_game<6 or cases_per_game%6:
        raise ValueError('GAMER_CASES_PER_GAME_MUST_BE_A_MULTIPLE_OF_SIX_AT_LEAST_SIX')
    if blind_cases_per_matrix < 0 or (blind_cases_per_matrix and blind_cases_per_matrix%6):
        raise ValueError('GAMER_BLIND_MATRIX_CASES_MUST_BE_MULTIPLE_OF_SIX')
    dataset=Path(dataset)
    if dataset.exists():raise FileExistsError(f'GAMER_DATASET_EXISTS: {dataset}. Use a fresh output directory')
    ids=sorted(set(src.MATRICES)|set(src.SCALAR_MODELS))
    if len(ids)!=33:raise ValueError('GAMER_REGISTRY_NOT_33')
    rows=[];seen=set()
    for game in ids:
        modes=[(i,False) for i in range(cases_per_game)]
        if game in src.MATRICES:
            modes.extend((cases_per_game+i,True) for i in range(blind_cases_per_matrix))
        for index,blind in modes:
            for attempt in range(300):
                row=generate_case(game,index,seed,attempt,blind_matrix=blind)
                if row['prompt_08'] not in seen:break
            else:raise ValueError(f'GAMER_GENERATED_PROMPT_COLLISION: {game} {index}')
            seen.add(row['prompt_08'])
            certify(row)
            rows.append(row)
    splits=Counter(r['split'] for r in rows)
    dataset.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=dataset.parent,delete=False) as stream:
        tmp=Path(stream.name)
        for row in rows:stream.write(canon(row)+'\n')
    try:os.replace(tmp,dataset)
    finally:
        if tmp.exists():tmp.unlink()
    # Human-inspectable summaries are derived from exactly the certified rows.
    import csv
    summary=dataset.with_name('factorization_summary.csv')
    example=dataset.with_name('sample_pairs.md')
    with summary.open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream)
        ops=('add','subtract','multiply by','divide by','larger of','choose second action if larger')
        writer.writerow(['game_id','table_families','cases','steps_per_case_mean',*ops])
        for game in ids:
            g=[r for r in rows if r['game_id']==game]
            count=Counter(step['operation'] for row in g for step in row['factor_steps'])
            writer.writerow([game,','.join(g[0]['table_families']),len(g),
                             round(sum(len(r['factor_steps']) for r in g)/len(g),3),
                             *[count.get(op,0) for op in ops]])
    with example.open('w',encoding='utf-8') as stream:
        stream.write('# Gamer v1 — source-verified examples\n\n')
        for game in ('hawk_dove','bayes_posterior','markov_one_step','captain_signed_offset'):
            row=next(r for r in rows if r['game_id']==game)
            stream.write(f'## {game}\n\n### *08 human prompt\n\n{row["prompt_08"]}\n\n')
            stream.write(f'### Math-table training target\n\n{row["math_prompt"]}\n\n')
            stream.write(f'Oracle numerical target (not in LoRA math table): `{row["answer"]}`\n\n')
    return {'status':'PASS_33_GAME_ORACLE_TABLE_GENERATION',
            'blind_matrix_cases':sum(r.get('blind_matrix') is True for r in rows),
            'blind_matrix_regimes':dict(Counter(r['blind_matrix_regime'] for r in rows if r.get('blind_matrix'))),
            'games':len(ids),'cases':len(rows),'by_split':dict(splits),
            'table_families':dict(Counter(k for r in rows for k in r['table_families'])),
            'dataset':str(dataset),'factorization_summary':str(summary),'sample_pairs':str(example),'dataset_sha256':hashlib.sha256(dataset.read_bytes()).hexdigest(),
            'source_registry_sha256':source_signature(),
            'zero_line_cases':sum('zero_line_residual' in r for r in rows)}
