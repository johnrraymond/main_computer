"""Bidirectional, *witness-backed* proof for nine-stage NanoJev game ladders.

Each tier has a narrow, declared parsing grammar. This proves round trips for
source-generated presentation templates, NOT general natural-language understanding.
T08 -> T00 is executable compilation. T00 -> T08 requires the persisted frozen
witness: X,Y,OP alone are not invertible into a unique game story.
"""
from __future__ import annotations

from fractions import Fraction
import json
import re
import sqlite3
from typing import Any

from . import nanojev_game_math_registry as mathlib

GAMES = ('hawk_dove', 'betrayal_loyalty_synthetic', 'first_price_captain_uncertainty')
NUM = r'(-?\d+(?:\.\d+)?(?:/\d+)?)'


def number(v):
    x = mathlib.frac(v)
    n, d = x.numerator, x.denominator
    # All generated finite rationals are exact decimals; fall back to a/b.
    rem = d
    while rem % 2 == 0 and rem > 1: rem //= 2
    while rem % 5 == 0 and rem > 1: rem //= 5
    if rem == 1:
        from decimal import Decimal, localcontext
        with localcontext() as ctx:
            ctx.prec = 80
            return format(Decimal(n) / Decimal(d), 'f').rstrip('0').rstrip('.') if n % d else str(n // d)
    return f'{n}/{d}'


def frozen_cases():
    """Generated *families* of counterfactuals, not three memorized examples."""
    seeds = [
       *[dict(game='hawk_dove', value=v, cost=c, rival=r) for v,c,r in
         ((6,10,'DOVE'),(8,14,'HAWK'),(20,30,'DOVE'),(15,24,'HAWK'),(12,25,'DOVE'),(5,9,'HAWK'))],
       *[dict(game='betrayal_loyalty_synthetic', betray=a, loyal=b, rival=r) for a,b,r in
         ((60,40,'TRUST'),(24,80,'TRUST'),(100,35,'INVESTIGATE'),(45,60,'INVESTIGATE'),(82,20,'TRUST'),(15,70,'TRUST'))],
       *[dict(game='first_price_captain_uncertainty', maximum=v, resources=r, intelligence=i, need=n) for v,r,i,n in
         ((100,125,'LOW','HIGH'),(120,140,'MEDIUM','HIGH'),(100,125,'MEDIUM','MEDIUM'),
          (80,125,'LOW','LOW'),(130,170,'LOW','MEDIUM'),(90,115,'HIGH','MEDIUM'))],
    ]
    # Counterfactual grid: hold game grammar fixed while varying numerical
    # inputs, opponent choices, intelligence grades, and rival motivation.
    for j in range(18):
        v=7+2*j
        seeds.append(dict(game='hawk_dove',value=v,cost=v+4+(j%7),
                          rival='HAWK' if j%2 else 'DOVE'))
        seeds.append(dict(game='betrayal_loyalty_synthetic',
                          betray=20+j*7,loyal=28+(j*11)%91,
                          rival='INVESTIGATE' if j%3==0 else 'TRUST'))
        intel=('LOW','MEDIUM','HIGH')[j%3]
        need=('HIGH','MEDIUM','LOW')[(j//3)%3]
        resources=110+5*j
        lo,hi=mathlib.execute(intel+'_INTELLIGENCE',resources)
        lo=mathlib.execute(need+'_NEED',lo)
        hi=mathlib.execute(need+'_NEED',hi)
        cap=int((lo+hi)/2)
        seeds.append(dict(game='first_price_captain_uncertainty',maximum=cap,
                          resources=resources,intelligence=intel,need=need))
    for idx, case in enumerate(seeds):
        case['case_id'] = f'{case["game"]}:case-{sum(s["game"] == case["game"] for s in seeds[:idx]):02d}'
        canonical, nodes = calculate(case)
        case['canonical'] = canonical
        case['nodes'] = nodes
        yield case


def tags(case):
    game = case['game']
    if game == 'hawk_dove':
        v = number(case['value']); cost = number(case['cost']); rival = case['rival']
        if rival == 'DOVE': return f'({v}, HAWK_VS_DOVE)', f'({v}, DOVE_VS_DOVE)'
        return f'({v}, {cost}, HAWK_VS_HAWK)', f'({v}, DOVE_VS_HAWK)'
    if game == 'betrayal_loyalty_synthetic':
        x,y = ('HIGH_PAYOFF','LOW_PAYOFF') if case['rival']=='TRUST' else ('LOW_PAYOFF','MEDIUM_PAYOFF')
        return f'({number(case["betray"])}, {x})', f'({number(case["loyal"])}, {y})'
    intel = case['intelligence']+'_INTELLIGENCE'; need=case['need']+'_NEED'
    return f'({number(case["maximum"])}, OWN_MEDIUM_NEED)', f'({number(case["resources"])}, {intel}, {need}, LOWER_BOUND)'


def calculate(case):
    game=case['game']; nodes=[]
    def put(name,op,*args):
        result=mathlib.execute(op,*args)
        nodes.append({'name':name,'operator':op,'inputs':mathlib.out(list(args)),'output':mathlib.out(result)})
        return result
    if game=='hawk_dove':
        v=mathlib.frac(case['value']); c=mathlib.frac(case['cost'])
        if v<=0 or c<=v: raise ValueError('hawk game requires 0 < value < cost')
        if case['rival']=='DOVE':
            x=put('hawk_value','HAWK_VS_DOVE',v);y=put('dove_value','DOVE_VS_DOVE',v)
        elif case['rival']=='HAWK':
            x=put('hawk_value','HAWK_VS_HAWK',v,c);y=put('dove_value','DOVE_VS_HAWK',v)
        else:raise ValueError('unknown hawk rival')
        op='GREATER_THAN';output='play_hawk_binary'
    elif game=='betrayal_loyalty_synthetic':
        if case['rival']=='TRUST': xop,yop='HIGH_PAYOFF','LOW_PAYOFF'
        elif case['rival']=='INVESTIGATE':xop,yop='LOW_PAYOFF','MEDIUM_PAYOFF'
        else:raise ValueError('unknown betrayal rival')
        x=put('betray_value',xop,case['betray']);y=put('loyal_value',yop,case['loyal'])
        op='GREATER_THAN';output='betray_binary'
    elif game=='first_price_captain_uncertainty':
        if case['intelligence'] not in ('LOW','MEDIUM','HIGH') or case['need'] not in ('LOW','MEDIUM','HIGH'):
            raise ValueError('invalid auction modifier')
        x=put('our_value','OWN_MEDIUM_NEED',case['maximum'])
        lo,hi=put('resources_interval',case['intelligence']+'_INTELLIGENCE',case['resources'])
        lowbid=put('rival_lower_bid',case['need']+'_NEED',lo)
        highbid=put('rival_upper_bid',case['need']+'_NEED',hi)
        if not (lowbid < x <= highbid):raise ValueError('MIDPOINT reduction requires lower < own <= upper')
        y=lowbid; bid=put('full_auction_optimum','OPTIMAL_BID_UNIFORM',x,lowbid,highbid)
        if bid != (x+y)/2:raise AssertionError('auction reduction not valid')
        op='MIDPOINT';output='bid_credits'
    else:raise KeyError(game)
    z=put('compose',op,x,y)
    return {'X':mathlib.out(mathlib.frac(x)),'Y':mathlib.out(mathlib.frac(y)),
            'OP':op,'Z':mathlib.out(z),'result_semantics':output},nodes


def decoded_fraction(record):
    return Fraction(record['numerator'],record['denominator'])


def render(case,tier):
    """Nine distinct levels. Templates are versioned and intentionally constrained."""
    game=case['game'];can=case['canonical'];x=number(decoded_fraction(can['X']));y=number(decoded_fraction(can['Y']))
    t1,t2=tags(case)
    if game=='hawk_dove':
        q='Do you play HAWK?';a,b='HAWK','DOVE';verb='Play HAWK'
    elif game=='betrayal_loyalty_synthetic':
        q='Do you betray?';a,b='BETRAY','LOYAL';verb='Betray'
    else:q='What should you bid?';a,b='OWN_VALUE','RIVAL_BID_LOWER';verb='Bid'
    prefix='Bid the midpoint of' if game=='first_price_captain_uncertainty' else ('Play HAWK only if' if game=='hawk_dove' else 'Betray only if')
    cmpword=' and ' if can['OP']=='MIDPOINT' else ' is greater than '
    if tier==0:return f'XXXXXX: {x}\nYYYYYYY: {y}\n\nOP: {can["OP"]}\n\n{q}'
    if tier==1:return f'XXXXXX: {x}\nYYYYYYY: {y}\n\n{prefix} XXXXXX{cmpword}YYYYYYY.\n\n{q}'
    if tier==2:return f'{prefix} {x}{cmpword}{y}.\n\n{q}'
    if tier==3:return f'{prefix} {t1}{cmpword}{y}.\n\n{q}'
    if tier==4:return f'{prefix} {t1}{cmpword}{t2}.\n\n{q}'
    if tier==5:return f'{a}: {t1}\n{b}: {t2}\n\n{verb} using the extracted values.\n\n{q}'
    if game=='hawk_dove':
        v=number(case['value']);cost=number(case['cost']);r=case['rival']
        xa='HAWK_VS_DOVE' if r=='DOVE' else 'HAWK_VS_HAWK'
        ya='DOVE_VS_DOVE' if r=='DOVE' else 'DOVE_VS_HAWK'
        if tier==6:return f'Resource value: {v}\nFighting cost: {cost}\nRival action: {r}\nHAWK rule: {xa}\nDOVE rule: {ya}\n\n{q}'
        if tier==7:return (f'HAWK-DOVE PAYOFF TABLE\nValue: {v}\nCost: {cost}\n'
                            f'Against DOVE: HAWK=HAWK_VS_DOVE, DOVE=DOVE_VS_DOVE\n'
                            f'Against HAWK: HAWK=HAWK_VS_HAWK, DOVE=DOVE_VS_HAWK\n'
                            f'Rival action: {r}\n\n{q}')
        if tier==8:return (f'You are competing with another captain over a resource worth {v} credits. '
                            f'Fighting another confrontational captain has a total cost of {cost} credits, shared equally. '
                            f'Two peaceful captains split the resource evenly. A confrontational captain facing a peaceful '
                            f'one takes the entire resource. Your rival has chosen {r}. Do you play HAWK?')
    if game=='betrayal_loyalty_synthetic':
        av=number(case['betray']); bv=number(case['loyal']);r=case['rival']
        xa='HIGH_PAYOFF' if r=='TRUST' else 'LOW_PAYOFF'
        ya='LOW_PAYOFF' if r=='TRUST' else 'MEDIUM_PAYOFF'
        if tier==6:return (f'BETRAY base: {av}\nLOYAL base: {bv}\nRival action: {r}\n'
                            f'BETRAY modifier: {xa}\nLOYAL modifier: {ya}\n\n{q}')
        if tier==7:return (f'BETRAYAL-LOYALTY PAYOFF TABLE\nBETRAY base: {av}\nLOYAL base: {bv}\n'
                            'Against TRUST: BETRAY=HIGH_PAYOFF, LOYAL=LOW_PAYOFF\n'
                            'Against INVESTIGATE: BETRAY=LOW_PAYOFF, LOYAL=MEDIUM_PAYOFF\n'
                            f'Rival action: {r}\n\n{q}')
        if tier==8:return (f'You are a captain in an alliance. Your baseline reputation for betrayal is {av} credits, '
                            f'while loyalty has a baseline reputation of {bv} credits. A trusting rival gives betrayal '
                            f'a HIGH payoff and loyalty a LOW payoff; an investigating rival gives betrayal a LOW payoff '
                            f'and loyalty a MEDIUM payoff. Your rival will {r}. Do you betray?')
    if game=='first_price_captain_uncertainty':
        v=number(case['maximum']); r=number(case['resources']); i=case['intelligence'];n=case['need']
        if tier==6:return (f'Maximum valuation: {v}\nRival resources estimate: {r}\n'
                            f'Intelligence certainty: {i}\nRival need: {n}\nOwn need: MEDIUM\n'
                            'Rule: OPTIMAL_BID_UNIFORM from modeled rival interval\n\nWhat should you bid?')
        if tier==7:return (f'FIRST-PRICE SEALED-BID RULES\nOwn valuation: {v}\nRival resources estimate: {r}\n'
                            f'Intelligence certainty: {i}\nRival need: {n}\nOwn need: MEDIUM\n'
                            'Winning bidder pays own bid; model uses uniform rival bid beliefs.\n\nWhat should you bid?')
        if tier==8:return (f'You are bidding for an exclusive trading license in a first-price sealed-bid auction. '
                            f'You are willing to pay at most {v} credits. Intelligence suggests the rival has {r} credits '
                            f'available, but its certainty is {i}. Your own need to win is MEDIUM and the rival\'s need '
                            f'to win is {n}. Neither captain can see the other bid. The winner pays their own bid. '
                            'What should you bid?')
    raise ValueError('tier outside 0..8')


def _match(pattern,text):
    m=re.fullmatch(pattern,text,flags=re.DOTALL)
    if m is None:raise ValueError('LADDER_PROMPT_NOT_IN_GRAMMAR')
    return m.groups()


def _tag_value(expr):
    m=re.fullmatch(r'\(('+NUM[1:-1]+r'),\s*(.*?)\)',expr)
    if not m:raise ValueError('BAD_TAGGED_OPERAND '+expr)
    first=mathlib.frac(m.group(1));parts=[p.strip() for p in m.group(2).split(',')]
    if parts[0] and re.fullmatch(NUM,parts[0]):
        if len(parts)!=2 or parts[1]!='HAWK_VS_HAWK':raise ValueError('BAD_BINARY_TAG')
        return mathlib.execute('HAWK_VS_HAWK',first,mathlib.frac(parts[0]))
    if len(parts)==1:return mathlib.execute(parts[0],first)
    if len(parts)==3 and parts[2]=='LOWER_BOUND':
        interval=mathlib.execute(parts[0],first)
        return mathlib.execute(parts[1],interval[0])
    raise ValueError('BAD_TAGGED_OPERAND '+expr)


def _extract_primitive(game,tier,text):
    """Independent closed-grammar extraction. No canonical labels are read here."""
    if game=='hawk_dove':q='Do you play HAWK?';a,b='HAWK','DOVE';verb='Play HAWK'
    elif game=='betrayal_loyalty_synthetic':q='Do you betray?';a,b='BETRAY','LOYAL';verb='Betray'
    elif game=='first_price_captain_uncertainty':q='What should you bid?';a,b='OWN_VALUE','RIVAL_BID_LOWER';verb='Bid'
    else:raise KeyError(game)
    op='MIDPOINT' if game=='first_price_captain_uncertainty' else 'GREATER_THAN'
    prefix='Bid the midpoint of' if game=='first_price_captain_uncertainty' else ('Play HAWK only if' if game=='hawk_dove' else 'Betray only if')
    word=' and ' if op=='MIDPOINT' else ' is greater than '
    if tier==0:
        x,y,got_op=_match(r'XXXXXX: '+NUM+r'\nYYYYYYY: '+NUM+r'\n\nOP: ([A-Z_]+)\n\n'+re.escape(q),text)
        if got_op!=op:raise ValueError('WRONG_OPERATOR')
        return {'X':mathlib.frac(x),'Y':mathlib.frac(y),'OP':op},None
    if tier==1:
        x,y=_match(r'XXXXXX: '+NUM+r'\nYYYYYYY: '+NUM+r'\n\n'+re.escape(prefix)+' XXXXXX'+re.escape(word)+r'YYYYYYY\.\n\n'+re.escape(q),text)
        return {'X':mathlib.frac(x),'Y':mathlib.frac(y),'OP':op},None
    if tier==2:
        x,y=_match(re.escape(prefix)+' '+NUM+re.escape(word)+NUM+r'\.\n\n'+re.escape(q),text)
        return {'X':mathlib.frac(x),'Y':mathlib.frac(y),'OP':op},None
    if tier in (3,4):
        # Tagged operands have a deliberately bounded, non-nested template.
        pieces=re.fullmatch(re.escape(prefix)+r' (\([^\n]+?\))'+re.escape(word)+r'(.+)\.\n\n'+re.escape(q),text)
        if not pieces:raise ValueError('LADDER_PROMPT_NOT_IN_GRAMMAR')
        xe,ye=pieces.groups(); x=_tag_value(xe)
        if tier==3 and not re.fullmatch(NUM,ye):raise ValueError('T03_UNEXPECTED_Y')
        if tier==4 and not ye.startswith('('):raise ValueError('T04_EXPECTED_TAG')
        y=_tag_value(ye) if tier==4 else mathlib.frac(ye)
        return {'X':x,'Y':y,'OP':op},None
    if tier==5:
        xe,ye=_match(re.escape(a)+r': (\([^\n]+\))\n'+re.escape(b)+r': (\([^\n]+\))\n\n'+re.escape(verb)+r' using the extracted values\.\n\n'+re.escape(q),text)
        return {'X':_tag_value(xe),'Y':_tag_value(ye),'OP':op},None
    if game=='hawk_dove':
        if tier==6:
            v,c,r,xa,ya=_match(r'Resource value: '+NUM+r'\nFighting cost: '+NUM+r'\nRival action: (HAWK|DOVE)\nHAWK rule: (HAWK_VS_DOVE|HAWK_VS_HAWK)\nDOVE rule: (DOVE_VS_DOVE|DOVE_VS_HAWK)\n\n'+re.escape(q),text)
            if (xa,ya)!=(('HAWK_VS_DOVE','DOVE_VS_DOVE') if r=='DOVE' else ('HAWK_VS_HAWK','DOVE_VS_HAWK')):raise ValueError('RULE_RIVAL_MISMATCH')
        elif tier==7:
            v,c,r=_match(r'HAWK-DOVE PAYOFF TABLE\nValue: '+NUM+r'\nCost: '+NUM+r'\nAgainst DOVE: HAWK=HAWK_VS_DOVE, DOVE=DOVE_VS_DOVE\nAgainst HAWK: HAWK=HAWK_VS_HAWK, DOVE=DOVE_VS_HAWK\nRival action: (HAWK|DOVE)\n\n'+re.escape(q),text)
        else:
            v,c,r=_match(r'You are competing with another captain over a resource worth '+NUM+r' credits\. Fighting another confrontational captain has a total cost of '+NUM+r' credits, shared equally\. Two peaceful captains split the resource evenly\. A confrontational captain facing a peaceful one takes the entire resource\. Your rival has chosen (HAWK|DOVE)\. Do you play HAWK\?',text)
        state={'game':game,'value':mathlib.frac(v),'cost':mathlib.frac(c),'rival':r}
    elif game=='betrayal_loyalty_synthetic':
        if tier==6:
            a,b,r,xa,ya=_match(r'BETRAY base: '+NUM+r'\nLOYAL base: '+NUM+r'\nRival action: (TRUST|INVESTIGATE)\nBETRAY modifier: (HIGH_PAYOFF|LOW_PAYOFF)\nLOYAL modifier: (LOW_PAYOFF|MEDIUM_PAYOFF)\n\n'+re.escape(q),text)
            if (xa,ya)!=(('HIGH_PAYOFF','LOW_PAYOFF') if r=='TRUST' else ('LOW_PAYOFF','MEDIUM_PAYOFF')):raise ValueError('RULE_RIVAL_MISMATCH')
        elif tier==7:
            a,b,r=_match(r'BETRAYAL-LOYALTY PAYOFF TABLE\nBETRAY base: '+NUM+r'\nLOYAL base: '+NUM+r'\nAgainst TRUST: BETRAY=HIGH_PAYOFF, LOYAL=LOW_PAYOFF\nAgainst INVESTIGATE: BETRAY=LOW_PAYOFF, LOYAL=MEDIUM_PAYOFF\nRival action: (TRUST|INVESTIGATE)\n\n'+re.escape(q),text)
        else:
            a,b,r=_match(r'You are a captain in an alliance\. Your baseline reputation for betrayal is '+NUM+r' credits, while loyalty has a baseline reputation of '+NUM+r' credits\. A trusting rival gives betrayal a HIGH payoff and loyalty a LOW payoff; an investigating rival gives betrayal a LOW payoff and loyalty a MEDIUM payoff\. Your rival will (TRUST|INVESTIGATE)\. Do you betray\?',text)
        state={'game':game,'betray':mathlib.frac(a),'loyal':mathlib.frac(b),'rival':r}
    else:
        if tier==6:
            v,r,i,n=_match(r'Maximum valuation: '+NUM+r'\nRival resources estimate: '+NUM+r'\nIntelligence certainty: (LOW|MEDIUM|HIGH)\nRival need: (LOW|MEDIUM|HIGH)\nOwn need: MEDIUM\nRule: OPTIMAL_BID_UNIFORM from modeled rival interval\n\nWhat should you bid\?',text)
        elif tier==7:
            v,r,i,n=_match(r'FIRST-PRICE SEALED-BID RULES\nOwn valuation: '+NUM+r'\nRival resources estimate: '+NUM+r'\nIntelligence certainty: (LOW|MEDIUM|HIGH)\nRival need: (LOW|MEDIUM|HIGH)\nOwn need: MEDIUM\nWinning bidder pays own bid; model uses uniform rival bid beliefs\.\n\nWhat should you bid\?',text)
        else:
            v,r,i,n=_match(r'You are bidding for an exclusive trading license in a first-price sealed-bid auction\. You are willing to pay at most '+NUM+r' credits\. Intelligence suggests the rival has '+NUM+r' credits available, but its certainty is (LOW|MEDIUM|HIGH)\. Your own need to win is MEDIUM and the rival\'s need to win is (LOW|MEDIUM|HIGH)\. Neither captain can see the other bid\. The winner pays their own bid\. What should you bid\?',text)
        state={'game':game,'maximum':mathlib.frac(v),'resources':mathlib.frac(r),'intelligence':i,'need':n}
    can,_=calculate(state)
    return {'X':decoded_fraction(can['X']),'Y':decoded_fraction(can['Y']),'OP':can['OP']},state


def compile_prompt(game,tier,prompt):
    values,state=_extract_primitive(game,tier,prompt)
    z=mathlib.execute(values['OP'],values['X'],values['Y'])
    return {'X':mathlib.out(values['X']),'Y':mathlib.out(values['Y']),
            'OP':values['OP'],'Z':mathlib.out(z),
            'result_semantics':'bid_credits' if game=='first_price_captain_uncertainty' else ('play_hawk_binary' if game=='hawk_dove' else 'betray_binary')},state


def _witness_case(witness):
    case=dict(witness)
    canonical,_=calculate(case)
    case['canonical']=canonical
    return case


def step_up(game,low_tier,prompt,witness):
    """Reconstruct from sealed scenario and prove by source-only reduction."""
    from . import nanojev_game_ladder_edge_programs as programs
    generated,_=programs.reconstruct_up(game,low_tier,prompt,witness)
    return generated


def step_down(game,high_tier,prompt,witness=None):
    """Execute source-only extraction; witness is optional validation, not input."""
    from . import nanojev_game_ladder_edge_programs as programs
    generated,_=programs.execute_down(game,high_tier,prompt)
    if witness is not None:
        case=_witness_case(witness)
        if case['game']!=game or compile_prompt(game,high_tier,prompt)[0]!=case['canonical']:
            raise ValueError('DOWN_WITNESS_MISMATCH')
    return generated


def walk(game,prompt,witness,direction='both'):
    """Replay the complete ladder, returning every actual intermediate prompt."""
    if direction not in ('up','down','both'):raise ValueError('INVALID_DIRECTION')
    if direction=='both':
        direction1=walk(game,prompt,witness,'up')
        direction2=walk(game,direction1[-1]['prompt'],witness,'down')
        if direction2[-1]['prompt']!=prompt:raise AssertionError('ROUNDTRIP_NOT_IDENTICAL')
        return {'up':direction1,'down':direction2}
    tier=0 if direction=='up' else 8
    out=[{'tier':tier,'prompt':prompt,'extracted':compile_prompt(game,tier,prompt)[0]}]
    while (tier<8 if direction=='up' else tier>0):
        if direction=='up':
            prompt=step_up(game,tier,prompt,witness);tier+=1
        else:
            prompt=step_down(game,tier,prompt,witness);tier-=1
        out.append({'tier':tier,'prompt':prompt,'extracted':compile_prompt(game,tier,prompt)[0]})
    return out


DDL = '''
CREATE TABLE ladder_proof_cases(
 case_id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(game_id),
 witness_json TEXT NOT NULL, canonical_json TEXT NOT NULL, operator_trace_json TEXT NOT NULL,
 proof_scope TEXT NOT NULL, status TEXT NOT NULL CHECK(status='PASS'));
CREATE TABLE ladder_proof_tiers(
 case_id TEXT NOT NULL REFERENCES ladder_proof_cases(case_id), tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8),
 prompt TEXT NOT NULL, extracted_json TEXT NOT NULL, reconstruction_proof_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='PASS'), PRIMARY KEY(case_id,tier));
CREATE TABLE ladder_proof_edges(
 case_id TEXT NOT NULL REFERENCES ladder_proof_cases(case_id), low_tier INTEGER NOT NULL,
 high_tier INTEGER NOT NULL, down_compilation_json TEXT NOT NULL, up_witness_reconstruction_json TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='PASS'), PRIMARY KEY(case_id,low_tier),
 CHECK(high_tier=low_tier+1));
CREATE TABLE ladder_transition_programs(
 program_id TEXT PRIMARY KEY,source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 opcode TEXT NOT NULL,pseudocode TEXT NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL);
CREATE TABLE ladder_transition_executions(
 case_id TEXT NOT NULL REFERENCES ladder_proof_cases(case_id),
 source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 direction TEXT NOT NULL CHECK(direction IN ('down','up')),
 record_json TEXT NOT NULL,record_sha256 TEXT NOT NULL,status TEXT NOT NULL CHECK(status='PASS'),
 PRIMARY KEY(case_id,source_tier,target_tier,direction));
CREATE TABLE ladder_transition_math_steps(
 case_id TEXT NOT NULL REFERENCES ladder_proof_cases(case_id),source_tier INTEGER NOT NULL,
 step_index INTEGER NOT NULL,operator_id TEXT NOT NULL,inputs_json TEXT NOT NULL,
 output_json TEXT NOT NULL,role TEXT NOT NULL,
 PRIMARY KEY(case_id,source_tier,step_index));
'''


def build_and_verify(db):
    """Insert only after complete independent recompilation succeeds."""
    db.executescript(DDL)
    from . import nanojev_game_ladder_edge_programs as programs
    for spec in programs.specs():
        db.execute('INSERT INTO ladder_transition_programs VALUES (?,?,?,?,?,?,?)',
                   (spec['program_id'],spec['source_tier'],spec['target_tier'],
                    spec['opcode'],spec['pseudocode'],json.dumps(spec,sort_keys=True),_sha(json.dumps(spec,sort_keys=True))))
    rows=[];cases=list(frozen_cases())
    has_requirements=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='game_math_requirements'").fetchone())
    for case in cases:
        case_id=case['case_id'];g=case['game'];expected=case['canonical']
        if has_requirements:
            for node in case['nodes']:
                db.execute('INSERT OR IGNORE INTO game_math_requirements VALUES (?,?,?)',
                           (g,node['operator'],'bidirectional_ladder_extraction'))
        witness={k:v for k,v in case.items() if k not in ('case_id','canonical','nodes')}
        canonical_by_tier=[];prompts=[]
        for tier in range(9):
            prompt=render(case,tier)
            compiled,parsed=compile_prompt(g,tier,prompt)
            if compiled!=expected:raise AssertionError(f'COMPILE_MISMATCH {case_id} T{tier}: {compiled} != {expected}')
            if tier>=6:
                # Proof of reconstructibility from only information visible at
                # that tier plus the registered, frozen mathematical rules.
                rebuilt=dict(parsed,case_id=case_id)
                canonical,_=calculate(rebuilt)
                if render(dict(rebuilt,canonical=canonical),tier)!=prompt:
                    raise AssertionError(f'TIER_RECONSTRUCTION_MISMATCH {case_id} T{tier}')
            prompts.append(prompt);canonical_by_tier.append(compiled)
        if len(set(prompts))!=9:raise AssertionError('TIER_REPETITION '+case_id)
        witnessed_walk=walk(g,prompts[0],witness,'both')
        if ([p['prompt'] for p in witnessed_walk['up']]!=prompts or
            [p['prompt'] for p in witnessed_walk['down']]!=list(reversed(prompts))):
            raise AssertionError('ROUNDTRIP_PATH_DRIFT '+case_id)
        db.execute('INSERT INTO ladder_proof_cases VALUES (?,?,?,?,?,?,?)',
                   (case_id,g,json.dumps(mathlib.out(witness),sort_keys=True),json.dumps(expected,sort_keys=True),
                    json.dumps(case['nodes'],sort_keys=True),'constrained_prompt_grammar_with_frozen_witness','PASS'))
        for tier,prompt in enumerate(prompts):
            proof={'compiler':'independent_regex_and_math_executor','tier':tier,
                   'parsed_from_prompt':tier>=6,'witness_required_for_reverse':tier<8,
                   'same_frozen_target':True}
            db.execute('INSERT INTO ladder_proof_tiers VALUES (?,?,?,?,?,?)',
                       (case_id,tier,prompt,json.dumps(canonical_by_tier[tier],sort_keys=True),json.dumps(proof,sort_keys=True),'PASS'))
        for t in range(8):
            # Down: compile harder level to its math, then reconstruct the
            # easier level from the sealed witness and validate compilation.
            down=compile_prompt(g,t+1,prompts[t+1])[0]
            easier=compile_prompt(g,t,prompts[t])[0]
            # Up cannot generally invert the numeric tuple: the generator
            # must carry the witness (original scenario), explicitly recorded.
            upper=render(case,t+1)
            if down!=easier or upper!=prompts[t+1]:raise AssertionError('BIDIRECTIONAL_EDGE_FAILURE '+case_id)
            db.execute('INSERT INTO ladder_proof_edges VALUES (?,?,?,?,?,?)',
                       (case_id,t,t+1,json.dumps(down,sort_keys=True),
                        json.dumps({'reproduced_prompt_sha256':_sha(upper),'witness_case_id':case_id},sort_keys=True),'PASS'))
        # Execute and persist the actual programs for ALL adjacent edges.
        # Descents receive no case witness or gold label; their sole input is
        # the prompt and the game's fixed, registered mathematical grammar.
        for high in range(8,0,-1):
            generated,record=programs.execute_down(g,high,prompts[high])
            if generated!=prompts[high-1]:raise AssertionError('EDGE_PROGRAM_TARGET_DRIFT '+case_id)
            text=json.dumps(record,sort_keys=True)
            db.execute('INSERT INTO ladder_transition_executions VALUES (?,?,?,?,?,?,?)',
                       (case_id,high,high-1,'down',text,_sha(text),'PASS'))
            for n in record['steps']:
                db.execute('INSERT INTO ladder_transition_math_steps VALUES (?,?,?,?,?,?,?)',
                           (case_id,high,n['step'],n['opcode'],json.dumps(n['input'],sort_keys=True),
                            json.dumps(n['output'],sort_keys=True),n['role']))
        for low in range(8):
            generated,record=programs.reconstruct_up(g,low,prompts[low],witness)
            if generated!=prompts[low+1]:raise AssertionError('EDGE_PROGRAM_UP_DRIFT '+case_id)
            text=json.dumps(record,sort_keys=True)
            db.execute('INSERT INTO ladder_transition_executions VALUES (?,?,?,?,?,?,?)',
                       (case_id,low,low+1,'up',text,_sha(text),'PASS'))
        rows.append({'case_id':case_id,'game':g,'Z':expected['Z'],'tiers':9,'edges':8})
    report=verify_persisted(db)
    report['case_summary']=rows
    return report


def _sha(s):
    import hashlib
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def verify_persisted(db):
    rows=db.execute('SELECT case_id,game_id,witness_json,canonical_json,operator_trace_json FROM ladder_proof_cases').fetchall()
    if not rows:raise AssertionError('NO_LADDER_PROOF_CASES')
    decisions=set();auction=set()
    for case_id,g,witness_json,canonical_json,trace_json in rows:
        witness=json.loads(witness_json);expected=json.loads(canonical_json)
        can,trace=calculate(witness)
        if can!=expected or mathlib.out(trace)!=json.loads(trace_json):raise AssertionError('WITNESS_TRACE_DRIFT '+case_id)
        prompts=db.execute('SELECT tier,prompt,extracted_json FROM ladder_proof_tiers WHERE case_id=? ORDER BY tier',(case_id,)).fetchall()
        if [t for t,_,_ in prompts]!=list(range(9)):raise AssertionError('MISSING_TIER '+case_id)
        for tier,prompt,cjson in prompts:
            independently_compiled,_=compile_prompt(g,tier,prompt)
            if independently_compiled!=json.loads(cjson) or independently_compiled!=can:
                raise AssertionError(f'CORRUPT_TIER {case_id} {tier}')
            if render(dict(witness,canonical=can),tier)!=prompt:raise AssertionError('WITNESS_RENDER_MISMATCH '+case_id)
        replay=walk(g,prompts[0][1],witness,'both')
        if ([p['prompt'] for p in replay['up']]!=[p[1] for p in prompts] or
            [p['prompt'] for p in replay['down']]!=[p[1] for p in reversed(prompts)]):
            raise AssertionError('PERSISTED_BIDIRECTIONAL_WALK_DRIFT '+case_id)
        edges=db.execute('SELECT low_tier,high_tier,down_compilation_json,up_witness_reconstruction_json FROM ladder_proof_edges WHERE case_id=? ORDER BY low_tier',(case_id,)).fetchall()
        if len(edges)!=8:raise AssertionError('MISSING_EDGE '+case_id)
        for low,high,down,up in edges:
            if high!=low+1 or json.loads(down)!=can:raise AssertionError('EDGE_DOWN_MISMATCH '+case_id)
            if json.loads(up)['reproduced_prompt_sha256']!=_sha(prompts[high][1]):
                raise AssertionError('EDGE_UP_MISMATCH '+case_id)
        (auction if g=='first_price_captain_uncertainty' else decisions).add(json.dumps(can['Z'],sort_keys=True))
    if len(decisions)<2 or len(auction)<3:raise AssertionError('COUNTERFACTUAL_COVERAGE_TOO_NARROW')
    from . import nanojev_game_ladder_edge_programs as programs
    specifications=programs.specs()
    if len(specifications)!=8:raise AssertionError('EDGE_PROGRAM_COUNT')
    for spec in specifications:
        row=db.execute('SELECT spec_json,spec_sha256 FROM ladder_transition_programs WHERE program_id=?',
                       (spec['program_id'],)).fetchone()
        if not row or json.loads(row[0])!=spec or row[1]!=_sha(row[0]):
            raise AssertionError('EDGE_PROGRAM_SPEC_DRIFT '+spec['program_id'])
    for case_id,g,witness_json,canonical_json,_ in rows:
        witness=json.loads(witness_json)
        prompts={t:p for t,p in db.execute('SELECT tier,prompt FROM ladder_proof_tiers WHERE case_id=?',(case_id,))}
        entries=db.execute('SELECT source_tier,target_tier,direction,record_json,record_sha256 FROM ladder_transition_executions WHERE case_id=?',(case_id,)).fetchall()
        if len(entries)!=16:raise AssertionError('EDGE_PROGRAM_INCOMPLETE '+case_id)
        for source,target,direction,stored,sha in entries:
            if _sha(stored)!=sha:raise AssertionError('EDGE_PROGRAM_RECORD_HASH_DRIFT '+case_id)
            if direction=='down':
                computed,record=programs.execute_down(g,source,prompts[source])
            else:
                computed,record=programs.reconstruct_up(g,source,prompts[source],witness)
            if computed!=prompts[target] or record!=json.loads(stored):
                raise AssertionError('EDGE_PROGRAM_EXECUTION_DRIFT '+case_id)
            if direction=='down':
                recorded=db.execute('SELECT step_index,operator_id,inputs_json,output_json,role FROM ladder_transition_math_steps WHERE case_id=? AND source_tier=? ORDER BY step_index',(case_id,source)).fetchall()
                actual=[(n['step'],n['opcode'],json.dumps(n['input'],sort_keys=True),json.dumps(n['output'],sort_keys=True),n['role']) for n in record['steps']]
                if recorded!=actual:raise AssertionError('EDGE_PROGRAM_MATH_TRACE_DRIFT '+case_id)
    return {'status':'PASS','proof_scope':'constrained_prompt_grammar_with_witness',
            'cases':len(rows),'game_families':len({r[1] for r in rows}),
            'tiers_verified':len(rows)*9,'adjacent_edges_verified_both_directions':len(rows)*8,
            'binary_outputs_observed':len(decisions),'distinct_auction_bids_observed':len(auction),
            'model_trained':False,
            'transition_programs':8,'edge_program_runs':len(rows)*16,
            'intermediate_math_steps':db.execute('SELECT COUNT(*) FROM ladder_transition_math_steps').fetchone()[0]}


def main(argv=None):
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Re-execute and display frozen NanoJev ladder up/down paths')
    parser.add_argument('--database',type=Path,default=Path('runtime/game-math-source-smoke-v3/game_transformations.sqlite3'))
    parser.add_argument('--case',dest='case_id',default='first_price_captain_uncertainty:case-00')
    parser.add_argument('--direction',choices=['up','down','both'],default='both')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    with sqlite3.connect(args.database) as db:
        row=db.execute('SELECT game_id,witness_json,canonical_json FROM ladder_proof_cases WHERE case_id=?',(args.case_id,)).fetchone()
        if row is None:parser.error('Unknown case in generated database: '+args.case_id)
        game,wtext,ctext=row;witness=json.loads(wtext);case=_witness_case(witness)
        if case['canonical']!=json.loads(ctext):raise AssertionError('CASE_ORACLE_CHANGED')
        start=render(case,0 if args.direction!='down' else 8)
        result=walk(game,start,witness,args.direction)
        if args.json:print(json.dumps(result,indent=2,sort_keys=True));return 0
        paths=result if isinstance(result,dict) else {args.direction:result}
        for label,path in paths.items():
            print('=== '+label.upper()+' ===')
            for entry in path:
                print(f'--- T{entry["tier"]} ---')
                print(entry['prompt'])
                print('EXTRACTED:',json.dumps(entry['extracted'],sort_keys=True))
        return 0


if __name__=='__main__':raise SystemExit(main())
