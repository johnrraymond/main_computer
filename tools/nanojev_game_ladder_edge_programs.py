"""Executable, source-authored T00..T08 transition programs.

Each DOWN program accepts its *source prompt* plus public game grammar and
mathematical registry. It NEVER accepts the stored canonical answer or case
witness. UP reconstruction requires an explicit sealed witness and must pass
the corresponding DOWN program. These are closed-grammar proofs, not claims
about free-form language understanding.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from typing import Any

from . import nanojev_game_ladder_proof as base
from . import nanojev_game_math_registry as maths


# Human-readable specifications and executable opcode, one per edge. The engine
# below dispatches ONLY these opcodes; no source-string eval/exec is permitted.
PROGRAMS = (
    (8, 7, 'EXTRACT_OBSERVATIONS', 'facts := PARSE_GAME_STORY(prompt); VERIFY_GAME_RULES(facts); EMIT_PAYOFF_OR_AUCTION_RULES(facts)'),
    (7, 6, 'RESOLVE_RULE_SELECTION', 'facts := PARSE_GAME_RULE_TABLE(prompt); SELECT_RULES(facts, opponent_action); EMIT_NAMED_RULES(facts)'),
    (6, 5, 'COMPILE_OPERAND_TAGS', 'facts := PARSE_NAMED_RULES(prompt); RUN_FROZEN_GAME_ORACLE(facts); EMIT_NAMED_TAGGED_OPERANDS(facts)'),
    (5, 4, 'BIND_TAGGED_OPERANDS', 'left,right := PARSE_TAGS(prompt); OP := RESOLVE_GAME_OPERATION(game); EMIT_INLINE_COMPARISON(left,right,OP)'),
    (4, 3, 'EVALUATE_RIGHT_EXPRESSION', 'left,right := PARSE_INLINE_EXPRESSION(prompt); y := EXECUTE_TAG_TREE(right); EMIT_COMPARISON(left,y)'),
    (3, 2, 'EVALUATE_LEFT_EXPRESSION', 'left,y := PARSE_INLINE_EXPRESSION(prompt); x := EXECUTE_TAG_TREE(left); EMIT_COMPARISON(x,y)'),
    (2, 1, 'INTRODUCE_REGISTERS', 'x,y := PARSE_LITERAL_EXPRESSION(prompt); BIND(XXXXXX,x); BIND(YYYYYYY,y); EMIT_REGISTER_EXPRESSION(x,y)'),
    (1, 0, 'EXPLICIT_OPERATION', 'x,y,op := PARSE_REGISTER_EXPRESSION(prompt); EMIT_EXPLICIT_OP(x,y,op)'),
)


def specs():
    return [dict(program_id=f'T{high:02d}_TO_T{low:02d}',source_tier=high,target_tier=low,
                 opcode=op,pseudocode=pseudo,requires_witness=False,
                 rules='game_operator_registry_and_frozen_prompt_grammar',
                 executable=True,implementation_sha256=hashlib.sha256(inspect.getsource(execute_down).encode()).hexdigest())
            for high,low,op,pseudo in PROGRAMS]


def _phrases(game):
    if game=='hawk_dove':return ('Play HAWK only if',' is greater than ','Do you play HAWK?','HAWK','DOVE','Play HAWK','GREATER_THAN')
    if game=='betrayal_loyalty_synthetic':return ('Betray only if',' is greater than ','Do you betray?','BETRAY','LOYAL','Betray','GREATER_THAN')
    if game=='first_price_captain_uncertainty':return ('Bid the midpoint of',' and ','What should you bid?','OWN_VALUE','RIVAL_BID_LOWER','Bid','MIDPOINT')
    raise KeyError(game)


def _node(trace, operation, args, result, *, role):
    trace.append(dict(step=len(trace),opcode=operation,input=maths.out(list(args)),output=maths.out(result),role=role))
    return result


def _evaluate_tag(expression: str, trace):
    """Parse a single unambiguous tag and execute all math substeps."""
    m=re.fullmatch(r'\((' + base.NUM[1:-1] + r'),\s*(.*?)\)',expression)
    if not m:raise ValueError('EDGE_MALFORMED_TAG')
    x=maths.frac(m.group(1));ops=[z.strip() for z in m.group(2).split(',')]
    if len(ops)==1:
        result=maths.execute(ops[0],x)
        return _node(trace,ops[0],[x],result,role='tagged_scalar')
    if len(ops)==2 and re.fullmatch(base.NUM,ops[0]) and ops[1]=='HAWK_VS_HAWK':
        cost=maths.frac(ops[0]);result=maths.execute('HAWK_VS_HAWK',x,cost)
        return _node(trace,'HAWK_VS_HAWK',[x,cost],result,role='tagged_payoff')
    if len(ops)==3 and ops[2]=='LOWER_BOUND':
        interval=_node(trace,ops[0],[x],maths.execute(ops[0],x),role='intelligence_to_interval')
        # Need changes the *distribution support*, not just its lower bound.
        # This explicit two-endpoint transformation prevents hiding missing math.
        bounds=tuple(_node(trace,ops[1],[endpoint],maths.execute(ops[1],endpoint),role=f'behavior_support_{i}')
                     for i,endpoint in enumerate(interval))
        if not bounds[0]<bounds[1]:raise ValueError('DEGENERATE_OPPONENT_DISTRIBUTION')
        return _node(trace,'LOWER_BOUND',[bounds],maths.execute('LOWER_BOUND',bounds),role='extract_distribution_bound')
    raise ValueError('EDGE_UNKNOWN_TAG_SHAPE')


def _inline(game,prompt,tier):
    prefix,word,q,*_= _phrases(game)
    if tier==4:
        m=re.fullmatch(re.escape(prefix)+r' (\([^\n]+?\))'+re.escape(word)+r'(\([^\n]+\))\.\n\n'+re.escape(q),prompt)
    elif tier==3:
        m=re.fullmatch(re.escape(prefix)+r' (\([^\n]+?\))'+re.escape(word)+base.NUM+r'\.\n\n'+re.escape(q),prompt)
    elif tier==2:
        m=re.fullmatch(re.escape(prefix)+r' '+base.NUM+re.escape(word)+base.NUM+r'\.\n\n'+re.escape(q),prompt)
    else:raise ValueError('INVALID_INLINE_TIER')
    if m is None:raise ValueError('EDGE_INLINE_GRAMMAR')
    return m.groups()


def execute_down(game: str, tier: int, prompt: str):
    """Run one executable edge; return (next_prompt, execution_record).

    No witness, case ID, original target, or stored scenario enters this call.
    """
    if not 1<=tier<=8:raise ValueError('EDGE_TIER_OUT_OF_RANGE')
    canonical, parsed = base.compile_prompt(game,tier,prompt)
    prefix,word,q,a,b,verb,op=_phrases(game)
    steps=[]
    opcode=PROGRAMS[8-tier][2]
    if tier in (8,7,6):
        if parsed is None:raise ValueError('PUBLIC_SCENARIO_NOT_EXTRACTED')
        case=dict(parsed)
        expected,nodes=base.calculate(case)
        if expected!=canonical:raise AssertionError('PUBLIC_SCENARIO_MATH_DRIFT')
        case['canonical']=expected
        for n in nodes:
            steps.append(dict(step=len(steps),opcode=n['operator'],input=n['inputs'],output=n['output'],role=n['name']))
        generated=base.render(case,tier-1)
    elif tier==5:
        xe,ye=base._match(re.escape(a)+r': (\([^\n]+\))\n'+re.escape(b)+r': (\([^\n]+\))\n\n'+re.escape(verb)+r' using the extracted values\.\n\n'+re.escape(q),prompt)
        _node(steps,'BIND_OPERAND_NAMES',[a,b],[xe,ye],role='surface_expression_binding')
        generated=f'{prefix} {xe}{word}{ye}.\n\n{q}'
    elif tier==4:
        xe,ye=_inline(game,prompt,4)
        y=_evaluate_tag(ye,steps)
        generated=f'{prefix} {xe}{word}{base.number(y)}.\n\n{q}'
    elif tier==3:
        xe,y=_inline(game,prompt,3)
        x=_evaluate_tag(xe,steps)
        generated=f'{prefix} {base.number(x)}{word}{y}.\n\n{q}'
    elif tier==2:
        x,y=_inline(game,prompt,2)
        _node(steps,'BIND_REGISTERS',[x,y],['XXXXXX','YYYYYYY'],role='explicit_operands_to_registers')
        generated=f'XXXXXX: {x}\nYYYYYYY: {y}\n\n{prefix} XXXXXX{word}YYYYYYY.\n\n{q}'
    else:  # T01 -> T00
        x,y=base._match(r'XXXXXX: '+base.NUM+r'\nYYYYYYY: '+base.NUM+r'\n\n'+re.escape(prefix)+' XXXXXX'+re.escape(word)+r'YYYYYYY\.\n\n'+re.escape(q),prompt)
        _node(steps,'RESOLVE_OPERATION',[op],[op],role='natural_language_to_named_operator')
        generated=f'XXXXXX: {x}\nYYYYYYY: {y}\n\nOP: {op}\n\n{q}'
    after, _ = base.compile_prompt(game,tier-1,generated)
    if canonical!=after:raise AssertionError('EDGE_CHANGES_CANONICAL_MATH')
    record=dict(program_id=f'T{tier:02d}_TO_T{tier-1:02d}',opcode=opcode,
                game=game,source_tier=tier,target_tier=tier-1,
                source_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                target_prompt_sha256=hashlib.sha256(generated.encode()).hexdigest(),
                steps=steps,canonical=canonical,witness_used=False,status='PASS')
    return generated,record


def reconstruct_up(game: str, low_tier: int, prompt: str, witness: dict):
    """Witness-backed ascent, independently checked by the DOWN program."""
    if not 0<=low_tier<8:raise ValueError('UP_TIER_OUT_OF_RANGE')
    case=base._witness_case(witness)
    if case['game']!=game:raise ValueError('UP_WITNESS_GAME_MISMATCH')
    current,_=base.compile_prompt(game,low_tier,prompt)
    if current!=case['canonical'] or base.render(case,low_tier)!=prompt:
        raise ValueError('UP_PRECONDITION_FAILED')
    generated=base.render(case,low_tier+1)
    reduced,down_record=execute_down(game,low_tier+1,generated)
    if reduced!=prompt:raise AssertionError('UP_RECONSTRUCTION_NOT_PROVED_BY_DOWN_PROGRAM')
    return generated,dict(program_id=f'T{low_tier:02d}_TO_T{low_tier+1:02d}',
                          direction='up_with_sealed_witness',verified_by=down_record['program_id'],
                          verification=down_record,status='PASS')


def walk_programs(game: str, first: str, witness: dict):
    tier=0;prompt=first;up=[]
    while tier<8:
        nxt,record=reconstruct_up(game,tier,prompt,witness)
        up.append(record);prompt=nxt;tier+=1
    down=[]
    while tier>0:
        prompt,record=execute_down(game,tier,prompt)
        down.append(record);tier-=1
    if prompt!=first:raise AssertionError('PROGRAM_WALK_NOT_REVERSIBLE_WITH_WITNESS')
    return dict(up=up,down=down,canonical=base.compile_prompt(game,0,prompt)[0],status='PASS')


def main(argv=None):
    import argparse
    import sqlite3
    parser=argparse.ArgumentParser(description='Show executable T08..T00 extraction programs and intermediate math')
    parser.add_argument('--database',default='runtime/game-math-source-smoke-v3/game_transformations.sqlite3')
    parser.add_argument('--case',default='first_price_captain_uncertainty:case-00')
    parser.add_argument('--json',action='store_true')
    args=parser.parse_args(argv)
    with sqlite3.connect(args.database) as db:
        rows=db.execute('''SELECT source_tier,record_json FROM ladder_transition_executions
                           WHERE case_id=? AND direction='down' ORDER BY source_tier DESC''',(args.case,)).fetchall()
    if len(rows)!=8:parser.error('No complete executable transition proof for case: '+args.case)
    records=[json.loads(r[1]) for r in rows]
    if args.json:print(json.dumps(records,sort_keys=True,indent=2));return 0
    for record in records:
        print(f"T{record['source_tier']:02d} -> T{record['target_tier']:02d}  {record['opcode']}")
        print('PSEUDOCODE:',next(s['pseudocode'] for s in specs() if s['program_id']==record['program_id']))
        for step in record['steps']:
            print('   ',step['opcode'],json.dumps(step['input'],sort_keys=True),'->',json.dumps(step['output'],sort_keys=True))
    return 0

if __name__=='__main__':raise SystemExit(main())
