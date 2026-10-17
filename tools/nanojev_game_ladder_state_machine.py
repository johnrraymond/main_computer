"""V4 state-passing game ladder: each edge executes exactly its own work.

The *downward* chain starts from a public T08 prompt, never from case gold or a
sealed witness. T08..T05 manipulate facts / rules / symbolic expressions, T04
and T03 evaluate only their respective operands, and T00 executes the final
operator. The independently computed game oracle is checked ONLY after T00.

This proves the declared CLOSED prompt grammar and frozen synthetic game rules,
not arbitrary-language understanding, equilibria, or empirical opponent models.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import sqlite3
from typing import Any

from . import nanojev_game_ladder_proof as ladder
from . import nanojev_game_ladder_edge_programs as v3
from . import nanojev_game_math_registry as maths

PROGRAMS = (
    (8, 7, 'EXTRACT_FACTS', 'facts := PARSE_PUBLIC_STORY(prompt); emit structured game rules (no arithmetic)'),
    (7, 6, 'RESOLVE_RULES', 'rules := SELECT_GAME_RULES(facts, rival_action); emit named rules (no arithmetic)'),
    (6, 5, 'BUILD_SYMBOLIC_EXPRESSIONS', 'X_expr,Y_expr,OP := COMPOSE_SYMBOLIC_OPERATORS(facts,rules) (do not evaluate)'),
    (5, 4, 'BIND_OPERANDS', 'bind operand names to X_expr and Y_expr; emit inline expressions'),
    (4, 3, 'EVALUATE_Y', 'Y := EVALUATE_TAGGED_EXPRESSION(Y_expr), recording every substep'),
    (3, 2, 'EVALUATE_X', 'X := EVALUATE_TAGGED_EXPRESSION(X_expr), recording every substep'),
    (2, 1, 'INTRODUCE_REGISTERS', 'bind literal X,Y to registered operands'),
    (1, 0, 'EXPLICIT_OPERATION', 'resolve named operator from preceding comparison wording'),
)


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha(obj):
    return hashlib.sha256((obj if isinstance(obj, str) else canon(obj)).encode('utf-8')).hexdigest()


def _parse_story(game, prompt):
    """Only extract public facts; deliberately do not call calculate/compile_prompt."""
    n = ladder.NUM
    if game == 'hawk_dove':
        v,c,r = ladder._match(
            r'You are competing with another captain over a resource worth '+n+
            r' credits\. Fighting another confrontational captain has a total cost of '+n+
            r' credits, shared equally\. Two peaceful captains split the resource evenly\. A confrontational captain facing a peaceful one takes the entire resource\. Your rival has chosen (HAWK|DOVE)\. Do you play HAWK\?', prompt)
        facts = dict(game=game, value=v, cost=c, rival=r)
        if maths.frac(v) <= 0 or maths.frac(c) <= maths.frac(v):
            raise ValueError('HAWK_DOVE_DOMAIN')
    elif game == 'betrayal_loyalty_synthetic':
        a,b,r = ladder._match(
            r'You are a captain in an alliance\. Your baseline reputation for betrayal is '+n+
            r' credits, while loyalty has a baseline reputation of '+n+
            r' credits\. A trusting rival gives betrayal a HIGH payoff and loyalty a LOW payoff; an investigating rival gives betrayal a LOW payoff and loyalty a MEDIUM payoff\. Your rival will (TRUST|INVESTIGATE)\. Do you betray\?', prompt)
        facts = dict(game=game, betray=a, loyal=b, rival=r)
    elif game == 'first_price_captain_uncertainty':
        v,r,i,need = ladder._match(
            r'You are bidding for an exclusive trading license in a first-price sealed-bid auction\. You are willing to pay at most '+n+
            r' credits\. Intelligence suggests the rival has '+n+
            r' credits available, but its certainty is (LOW|MEDIUM|HIGH)\. Your own need to win is MEDIUM and the rival\'s need to win is (LOW|MEDIUM|HIGH)\. Neither captain can see the other bid\. The winner pays their own bid\. What should you bid\?', prompt)
        if maths.frac(v)<=0 or maths.frac(r)<=0:raise ValueError('AUCTION_POSITIVE_DOMAIN')
        facts = dict(game=game, maximum=v, resources=r, intelligence=i, need=need)
    else:raise ValueError('UNKNOWN_GAME')
    return facts


def _question(game):
    return v3._phrases(game)[2]


def _to_plain(facts):
    # Used only for rendering. Never calculate Z or inspect a case witness.
    return dict(facts)


def _render_7(f):
    g=f['game'];q=_question(g)
    if g=='hawk_dove':
        return (f'HAWK-DOVE PAYOFF TABLE\nValue: {f["value"]}\nCost: {f["cost"]}\n'
                'Against DOVE: HAWK=HAWK_VS_DOVE, DOVE=DOVE_VS_DOVE\n'
                'Against HAWK: HAWK=HAWK_VS_HAWK, DOVE=DOVE_VS_HAWK\n'
                f'Rival action: {f["rival"]}\n\n{q}')
    if g=='betrayal_loyalty_synthetic':
        return (f'BETRAYAL-LOYALTY PAYOFF TABLE\nBETRAY base: {f["betray"]}\nLOYAL base: {f["loyal"]}\n'
                'Against TRUST: BETRAY=HIGH_PAYOFF, LOYAL=LOW_PAYOFF\n'
                'Against INVESTIGATE: BETRAY=LOW_PAYOFF, LOYAL=MEDIUM_PAYOFF\n'
                f'Rival action: {f["rival"]}\n\n{q}')
    return (f'FIRST-PRICE SEALED-BID RULES\nOwn valuation: {f["maximum"]}\n'
            f'Rival resources estimate: {f["resources"]}\nIntelligence certainty: {f["intelligence"]}\n'
            f'Rival need: {f["need"]}\nOwn need: MEDIUM\n'
            'Winning bidder pays own bid; model uses uniform rival bid beliefs.\n\nWhat should you bid?')


def _resolve_rules(f):
    g=f['game']
    if g=='hawk_dove':
        return dict(left='HAWK_VS_DOVE' if f['rival']=='DOVE' else 'HAWK_VS_HAWK',
                    right='DOVE_VS_DOVE' if f['rival']=='DOVE' else 'DOVE_VS_HAWK',
                    op='GREATER_THAN')
    if g=='betrayal_loyalty_synthetic':
        return dict(left='HIGH_PAYOFF' if f['rival']=='TRUST' else 'LOW_PAYOFF',
                    right='LOW_PAYOFF' if f['rival']=='TRUST' else 'MEDIUM_PAYOFF',
                    op='GREATER_THAN')
    return dict(left='OWN_MEDIUM_NEED',
                intelligence=f['intelligence']+'_INTELLIGENCE',
                rival_need=f['need']+'_NEED',right='LOWER_BOUND',
                solver='OPTIMAL_BID_UNIFORM',
                op='MIDPOINT',reduction_proof='verify_uniform_interior_at_final_oracle')


def _render_6(f,r):
    g=f['game'];q=_question(g)
    if g=='hawk_dove':
        return (f'Resource value: {f["value"]}\nFighting cost: {f["cost"]}\n'
                f'Rival action: {f["rival"]}\nHAWK rule: {r["left"]}\nDOVE rule: {r["right"]}\n\n{q}')
    if g=='betrayal_loyalty_synthetic':
        return (f'BETRAY base: {f["betray"]}\nLOYAL base: {f["loyal"]}\n'
                f'Rival action: {f["rival"]}\nBETRAY modifier: {r["left"]}\nLOYAL modifier: {r["right"]}\n\n{q}')
    return (f'Maximum valuation: {f["maximum"]}\nRival resources estimate: {f["resources"]}\n'
            f'Intelligence certainty: {f["intelligence"]}\nRival need: {f["need"]}\nOwn need: MEDIUM\n'
            'Rule: OPTIMAL_BID_UNIFORM from modeled rival interval\n\nWhat should you bid?')


def _expressions(f,r):
    g=f['game']
    if g=='hawk_dove':
        val=f['value']
        x=f'({val}, {f["cost"]}, HAWK_VS_HAWK)' if f['rival']=='HAWK' else f'({val}, HAWK_VS_DOVE)'
        return x,f'({val}, {r["right"]})'
    if g=='betrayal_loyalty_synthetic':
        return f'({f["betray"]}, {r["left"]})',f'({f["loyal"]}, {r["right"]})'
    return (f'({f["maximum"]}, OWN_MEDIUM_NEED)',
            f'({f["resources"]}, {r["intelligence"]}, {r["rival_need"]}, LOWER_BOUND)')


def _render_5(f, expr):
    _,_,q,a,b,verb,op=v3._phrases(f['game'])
    return f'{a}: {expr["X"]}\n{b}: {expr["Y"]}\n\n{verb} using the extracted values.\n\n{q}'


def _render_inline(f, expr, tier, x=None, y=None):
    prefix,word,q,*_=v3._phrases(f['game'])
    l=expr['X'] if tier in (4,3) else x
    r=expr['Y'] if tier==4 else y
    if l is None or r is None:raise AssertionError('MISSING_STAGED_OPERAND')
    return f'{prefix} {l}{word}{r}.\n\n{q}'


def _render_1(f,x,y):
    prefix,word,q,*_=v3._phrases(f['game'])
    return f'XXXXXX: {x}\nYYYYYYY: {y}\n\n{prefix} XXXXXX{word}YYYYYYY.\n\n{q}'


def _render_0(f,x,y,op):
    return f'XXXXXX: {x}\nYYYYYYY: {y}\n\nOP: {op}\n\n{_question(f["game"])}'


def begin(game,prompt):
    # Do not parse or compute until T08 -> T07 actually executes.
    if game not in ladder.GAMES:raise KeyError(game)
    return {'game':game,'tier':8,'prompt':prompt,'facts':None,'rules':None,
            'expressions':None,'X':None,'Y':None,'OP':None,'Z':None}


def step(source):
    """One DOWN transition: consumes the state returned by preceding step.

    No case ID, scenario witness, canonical result or gold output is accepted.
    No full-game oracle is called. Each state's input and output are preserved.
    """
    before=deepcopy(source)
    current=deepcopy(source)
    tier=current['tier'];g=current['game'];prompt=current['prompt']
    if not 1<=tier<=8:raise ValueError('STATE_EDGE_INVALID_TIER')
    if not isinstance(prompt,str):raise ValueError('STATE_PROMPT_MISSING')
    trace=[]
    if tier==8:
        if any(current[k] is not None for k in ('facts','rules','expressions','X','Y','OP','Z')):
            raise ValueError('EARLY_STATE_INFORMATION_LEAK')
        f=_parse_story(g,prompt)
        current['facts']=f
        new=_render_7(f)
    elif tier==7:
        f=current['facts']
        if f is None or current['rules'] is not None or prompt!=_render_7(f):
            raise ValueError('STATE_T07_NOT_DERIVED_FROM_PRIOR')
        r=_resolve_rules(f)
        current['rules']=r
        new=_render_6(f,r)
    elif tier==6:
        f=current['facts'];r=current['rules']
        if r is None or r!=_resolve_rules(f) or current['expressions'] is not None or prompt!=_render_6(f,r):
            raise ValueError('STATE_T06_NOT_DERIVED_FROM_PRIOR')
        xe,ye=_expressions(f,r)
        current['expressions']={'X':xe,'Y':ye}
        current['OP']=r['op']
        new=_render_5(f,current['expressions'])
    elif tier==5:
        f=current['facts'];expr=current['expressions']
        if expr is None or expr!=dict(zip(('X','Y'),_expressions(f,current['rules']))) or prompt!=_render_5(f,expr):
            raise ValueError('STATE_T05_NOT_DERIVED_FROM_PRIOR')
        new=_render_inline(f,expr,4)
        trace.append({'opcode':'BIND_OPERAND_NAMES','inputs':[_json_names(g)],'output':expr,'role':'symbolic_binding'})
    elif tier==4:
        f=current['facts'];expr=current['expressions']
        if expr!=dict(zip(('X','Y'),_expressions(f,current['rules']))) or prompt!=_render_inline(f,expr,4):
            raise ValueError('STATE_T04_NOT_DERIVED_FROM_PRIOR')
        y=v3._evaluate_tag(expr['Y'],trace)
        current['Y']=ladder.number(y)
        new=_render_inline(f,expr,3,y=current['Y'])
    elif tier==3:
        f=current['facts'];expr=current['expressions']
        if current['Y'] is None or prompt!=_render_inline(f,expr,3,y=current['Y']):
            raise ValueError('STATE_T03_NOT_DERIVED_FROM_PRIOR')
        x=v3._evaluate_tag(expr['X'],trace)
        current['X']=ladder.number(x)
        new=_render_inline(f,expr,2,x=current['X'],y=current['Y'])
    elif tier==2:
        f=current['facts'];expr=current['expressions']
        if current['X'] is None or current['Y'] is None or prompt!=_render_inline(f,expr,2,x=current['X'],y=current['Y']):
            raise ValueError('STATE_T02_NOT_DERIVED_FROM_PRIOR')
        new=_render_1(f,current['X'],current['Y'])
        trace.append({'opcode':'BIND_REGISTERS','inputs':[current['X'],current['Y']],
                      'output':['XXXXXX','YYYYYYY'],'role':'register_binding'})
    else:  # T01 -> T00
        f=current['facts'];r=current['rules']
        if prompt!=_render_1(f,current['X'],current['Y']):raise ValueError('STATE_T01_NOT_DERIVED_FROM_PRIOR')
        if current['OP']!=r['op']:raise AssertionError('STATE_OPERATOR_CONFLICT')
        new=_render_0(f,current['X'],current['Y'],current['OP'])
        trace.append({'opcode':'RESOLVE_OPERATION','inputs':[current['OP']],
                      'output':current['OP'],'role':'operator_surface'})
    current['prompt']=new;current['tier']=tier-1
    rec={'source_tier':tier,'target_tier':tier-1,'program_id':f'T{tier:02d}_TO_T{tier-1:02d}',
         'opcode':PROGRAMS[8-tier][2], 'input_state':before, 'output_state':deepcopy(current),
         'math_steps':trace,'witness_used':False,'oracle_used':False,
         'status':'PASS'}
    return current,rec


def _json_names(g):
    _,_,_,a,b,*_=v3._phrases(g)
    return [a,b]


def finish(state):
    """Only final operation and independent oracle comparison use arithmetic Z."""
    if state['tier']!=0:raise ValueError('FINAL_BEFORE_T00')
    x=maths.frac(state['X']);y=maths.frac(state['Y']);op=state['OP']
    if state['prompt']!=_render_0(state['facts'],state['X'],state['Y'],op):
        raise ValueError('FINAL_PROMPT_MISMATCH')
    observed=maths.execute(op,x,y)
    # Deliberately independent of the state-machine transitions.
    oracle,nodes=ladder.calculate(state['facts'])
    got={'X':maths.out(x),'Y':maths.out(y),'OP':op,'Z':maths.out(observed),
         'result_semantics':oracle['result_semantics']}
    if got!=oracle:raise AssertionError('STATE_MACHINE_FINAL_ORACLE_MISMATCH')
    # The domain precondition for the shortcut MIDPOINT is checked in the
    # independent auction oracle, which executes OPTIMAL_BID_UNIFORM as well.
    return {'canonical':got,'independent_oracle':oracle,'oracle_trace':nodes,
            'oracle_only_at_end':True,'status':'PASS'}


def walk(game,prompt):
    current=begin(game,prompt);edges=[]
    for tier in range(8,0,-1):
        if current['tier']!=tier:raise AssertionError('STATE_TIER_SKIPPED')
        current,record=step(current);edges.append(record)
    end=finish(current)
    return {'edges':edges,'end':end,'status':'PASS'}


DDL = '''
CREATE TABLE ladder_v4_state_cases(
 case_id TEXT PRIMARY KEY REFERENCES ladder_proof_cases(case_id),
 game_id TEXT NOT NULL, start_prompt TEXT NOT NULL, end_state_json TEXT NOT NULL,
 oracle_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status='PASS'));
CREATE TABLE ladder_v4_state_edges(
 case_id TEXT NOT NULL REFERENCES ladder_v4_state_cases(case_id),source_tier INTEGER NOT NULL,
 target_tier INTEGER NOT NULL, program_id TEXT NOT NULL,
 input_state_json TEXT NOT NULL, output_state_json TEXT NOT NULL,
 local_steps_json TEXT NOT NULL, record_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status='PASS'),
 PRIMARY KEY(case_id,source_tier), CHECK(target_tier=source_tier-1));
CREATE TABLE ladder_v4_programs(
 program_id TEXT PRIMARY KEY,source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 opcode TEXT NOT NULL,pseudocode TEXT NOT NULL,definition_sha256 TEXT NOT NULL);
'''


def specs():
    return [dict(program_id=f'T{t:02d}_TO_T{l:02d}',source_tier=t,target_tier=l,
                 opcode=op,pseudocode=pseudo,implementation_sha256=sha(step.__code__.co_code.hex()))
            for t,l,op,pseudo in PROGRAMS]


def build_and_verify(db):
    db.executescript(DDL)
    for s in specs():
        db.execute('INSERT INTO ladder_v4_programs VALUES (?,?,?,?,?,?)',
                   (s['program_id'],s['source_tier'],s['target_tier'],s['opcode'],s['pseudocode'],sha(s)))
    cases=db.execute('SELECT case_id,game_id FROM ladder_proof_cases ORDER BY case_id').fetchall()
    for case_id,g in cases:
        row=db.execute('SELECT prompt FROM ladder_proof_tiers WHERE case_id=? AND tier=8',(case_id,)).fetchone()
        if row is None:raise AssertionError('STATE_MISSING_T08')
        initial=row[0]
        proof=walk(g,initial)
        canonical=json.loads(db.execute('SELECT canonical_json FROM ladder_proof_cases WHERE case_id=?',(case_id,)).fetchone()[0])
        if proof['end']['canonical']!=canonical:raise AssertionError('STATE_GOLD_TARGET_MISMATCH')
        db.execute('INSERT INTO ladder_v4_state_cases VALUES (?,?,?,?,?,?)',
                   (case_id,g,initial,canon(proof['edges'][-1]['output_state']),
                    canon(proof['end']),'PASS'))
        for edge in proof['edges']:
            stored_prompt=db.execute('SELECT prompt FROM ladder_proof_tiers WHERE case_id=? AND tier=?',
                                     (case_id,edge['target_tier'])).fetchone()[0]
            if edge['output_state']['prompt']!=stored_prompt:raise AssertionError('STATE_TRANSITION_OUTPUT_DRIFT')
            db.execute('INSERT INTO ladder_v4_state_edges VALUES (?,?,?,?,?,?,?,?,?)',
                (case_id,edge['source_tier'],edge['target_tier'],edge['program_id'],
                 canon(edge['input_state']),canon(edge['output_state']),
                 canon(edge['math_steps']),sha(edge),'PASS'))
    return verify(db)


def verify(db):
    registered=db.execute('SELECT program_id,source_tier,target_tier,opcode,pseudocode,definition_sha256 FROM ladder_v4_programs ORDER BY source_tier DESC').fetchall()
    expected=[(s['program_id'],s['source_tier'],s['target_tier'],s['opcode'],s['pseudocode'],sha(s)) for s in specs()]
    if registered!=expected:raise AssertionError('STATE_PROGRAM_DEFINITION_DRIFT')
    persisted=db.execute('SELECT case_id,game_id,start_prompt,end_state_json,oracle_json FROM ladder_v4_state_cases ORDER BY case_id').fetchall()
    if len(persisted)!=72:raise AssertionError('STATE_CASE_COVERAGE')
    count=0;local_nodes=0
    for case_id,g,start,last,oracle in persisted:
        proof=walk(g,start)
        if canon(proof['edges'][-1]['output_state'])!=last or canon(proof['end'])!=oracle:
            raise AssertionError('STATE_FINAL_RESULT_DRIFT '+case_id)
        stored=db.execute('SELECT source_tier,input_state_json,output_state_json,local_steps_json,record_sha256 FROM ladder_v4_state_edges WHERE case_id=? ORDER BY source_tier DESC',(case_id,)).fetchall()
        if len(stored)!=8:raise AssertionError('STATE_EDGE_COVERAGE '+case_id)
        for record,row in zip(proof['edges'],stored):
            tier,sin,sout,steps,hashvalue=row
            if tier!=record['source_tier'] or sin!=canon(record['input_state']) or sout!=canon(record['output_state']) or steps!=canon(record['math_steps']) or hashvalue!=sha(record):
                raise AssertionError('STATE_EDGE_DRIFT '+case_id+f' T{tier}')
            # Explicitly require actual state threading and prevent early math.
            if tier>=5 and any(s['opcode'] in maths.OPERATORS for s in record['math_steps']):
                raise AssertionError('EARLY_MATH_EXECUTION '+case_id)
            count+=1;local_nodes+=len(record['math_steps'])
    return {'status':'PASS','proof_scope':'closed_grammar_stateful_local_edge_execution',
            'cases':len(persisted),'stateful_edges':count,
            'local_step_count':local_nodes,'oracle_invocations_per_walk':1,
            'witness_used_in_descent':False,'model_trained':False}


def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(description='Show v4 incremental state and local edge computations')
    ap.add_argument('--database',default='runtime/game-math-source-smoke-v4/game_transformations.sqlite3')
    ap.add_argument('--case',default='first_price_captain_uncertainty:case-00')
    ap.add_argument('--json',action='store_true')
    args=ap.parse_args(argv)
    with sqlite3.connect(args.database) as db:
        row=db.execute('SELECT game_id,start_prompt FROM ladder_v4_state_cases WHERE case_id=?',(args.case,)).fetchone()
    if row is None:ap.error('No v4 state proof for case')
    proof=walk(*row)
    if args.json:
        print(json.dumps(proof,indent=2,sort_keys=True));return 0
    for e in proof['edges']:
        print(f'T{e["source_tier"]:02d} -> T{e["target_tier"]:02d} {e["opcode"]}')
        print('INPUT KNOWN:',', '.join(k for k in ('facts','rules','expressions','X','Y','OP','Z') if e['input_state'].get(k) is not None))
        print('OUTPUT KNOWN:',', '.join(k for k in ('facts','rules','expressions','X','Y','OP','Z') if e['output_state'].get(k) is not None))
        for item in e['math_steps']:
            print('  ',item['opcode'],canon(item.get('input',item.get('inputs'))),'->',canon(item['output']))
        print('PROMPT:',e['output_state']['prompt'].replace('\n',' | '))
    print('FINAL OPERATOR AND INDEPENDENT ORACLE:',canon(proof['end']['canonical']))
    return 0

if __name__=='__main__':raise SystemExit(main())
