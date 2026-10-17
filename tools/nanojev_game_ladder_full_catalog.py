"""V5: executable, state-threaded T08->T00 bridges for ALL legacy oracle cases.

Scope: mathematical transformation *bridges*, not synthetic strategic decisions.
Catalog-only games are recorded as blocked; no game oracle is fabricated. T08
uses a controlled readable problem statement containing the full requisite game
payoffs/parameters. No case-specific sealed witness or gold values are used in
DOWNWARD extraction. Full independent matrix / scalar oracles run at final check.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from fractions import Fraction
from typing import Any

from . import nanojev_game_transformation_library as legacy
from . import nanojev_game_math_registry as mathlib

VERSION = 'nanojev.full-library.stateful-bridges.v5'
STEPS = (
    (8, 7, 'EXTRACT_SOURCE_FACTS', 'Parse stated game, actions, numerical payoffs or parameters, and transform; no oracle'),
    (7, 6, 'RESOLVE_TRANSFORM_RULE', 'Resolve registered matrix or scalar transform and validate its domain; no oracle'),
    (6, 5, 'BUILD_EXPRESSIONS', 'Compose unexecuted original/transformed operand expressions and SUBTRACT'),
    (5, 4, 'BIND_SEMANTIC_OPERANDS', 'Bind original and transformed payoff/value names to expressions'),
    (4, 3, 'EXECUTE_TRANSFORMED_OPERAND', 'Apply requested transformation and extract its transformed numerical payoff/value'),
    (3, 2, 'EXECUTE_ORIGINAL_OPERAND', 'Compute original numerical payoff/value independently'),
    (2, 1, 'BIND_REGISTERS', 'Render X/Y values as registered natural-language comparison'),
    (1, 0, 'EXPOSE_OPERATOR', 'Replace natural-language subtraction with explicit SUBTRACT operator'),
)


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def hash_obj(x: Any) -> str:
    return hashlib.sha256(canon(x).encode('utf-8')).hexdigest()


def fmt(x: Any) -> str:
    return str(x)


def _facts_for_db(case_row: tuple) -> dict:
    case_id, game, name, op, src_json, target_json, split = case_row
    src = json.loads(src_json)
    target = json.loads(target_json)
    if target['kind'] == 'matrix':
        facts = {'kind': 'matrix', 'game_id': game, 'game_name': name, 'transform': op,
                 'row_actions': src['row_actions'], 'column_actions': src['column_actions'], 'U': src['U']}
    elif target['kind'] == 'scalar':
        facts = {'kind': 'scalar', 'game_id': game, 'game_name': name, 'transform': op,
                 'parameters': src['parameters'], 'changed': target['changed'],
                 'increment': src['increment'], 'formula': src['formula']}
    else:
        raise ValueError('UNSUPPORTED_SOURCE_KIND')
    return facts


def _check_facts(f: dict) -> None:
    if f['kind']=='matrix':
        if f['game_id'] not in legacy.MATRICES:raise ValueError('MATRIX_NOT_VERIFIED')
        if f['transform'] not in {r[0] for r in legacy.TRANSFORMS if r[-1]=='executable'}:
            raise ValueError('MATRIX_TRANSFORM_NOT_VERIFIED')
        if f['transform'] not in ('swap_players','permute_row','permute_column','positive_affine',
                                 'shared_payoff_shift','change_one_payoff','reverse_payoff_sign','compose_transformations'):
            raise ValueError('MATRIX_TRANSFORM_NOT_IMPLEMENTED')
        m=f['U']
        if not m or any(not isinstance(row,list) or len(row)!=len(m[0]) for row in m):raise ValueError('INVALID_MATRIX')
        if len(m)!=len(f['row_actions']) or len(m[0])!=len(f['column_actions']):raise ValueError('ACTION_MATRIX_SHAPE')
        if any(not isinstance(cell,list) or len(cell)!=2 or any(not isinstance(x,(int,float)) or isinstance(x,bool) for x in cell) for row in m for cell in row):
            raise ValueError('INVALID_TWO_PLAYER_PAYOFFS')
    elif f['kind']=='scalar':
        if f['game_id'] not in legacy.SCALAR_MODELS or f['transform']!='scalar_parameter_shift':
            raise ValueError('SCALAR_NOT_VERIFIED')
        keys=set(legacy.SCALAR_MODELS[f['game_id']][3])
        if set(f['parameters'])!=keys or f['changed'] not in keys:
            raise ValueError('PARAMETER_MISMATCH')
        if f['formula']!=legacy.SCALAR_MODELS[f['game_id']][2]:raise ValueError('FORMULA_DRIFT')
        if f['changed']!=legacy.SCALAR_MODELS[f['game_id']][4] or f['increment']!=legacy.SCALAR_MODELS[f['game_id']][5]:
            raise ValueError('SHIFT_OPERATOR_DRIFT')
    else:raise ValueError('INVALID_KIND')


def t08(f: dict) -> str:
    if f['kind']=='matrix':
        return ('In the game '+json.dumps(f['game_name'],ensure_ascii=False)+', two players select actions from the row and column lists. '
                'Every payoff cell lists [row-player utility, column-player utility].\n'
                'Game data: '+canon({k:f[k] for k in ('game_id','kind','row_actions','column_actions','U')})+'\n'
                'Apply the game transformation '+f['transform']+'.\n'
                'Calculate the original first-cell row utility MINUS the first-cell row utility after the transformation.')
    return ('In the game '+json.dumps(f['game_name'],ensure_ascii=False)+', an existing scalar mathematical model has these parameters.\n'
            'Game data: '+canon({k:f[k] for k in ('game_id','kind','parameters','formula')})+'\n'
            'Apply the game transformation scalar_parameter_shift: increase '+f['changed']+' by '+fmt(f['increment'])+'.\n'
            'Calculate the original model value MINUS the model value after the parameter change.')


def parse_t08(prompt: str) -> dict:
    matrix = re.fullmatch(r'In the game ("(?:[^"\\]|\\.)*"), two players select actions from the row and column lists\. Every payoff cell lists \[row-player utility, column-player utility\]\.\nGame data: (\{[^\n]+\})\nApply the game transformation ([a-z_]+)\.\nCalculate the original first-cell row utility MINUS the first-cell row utility after the transformation\.',prompt)
    if matrix:
        name,source,op=matrix.groups()
        f=json.loads(source);f.update(game_name=json.loads(name),transform=op)
    else:
        scalar=re.fullmatch(r'In the game ("(?:[^"\\]|\\.)*"), an existing scalar mathematical model has these parameters\.\nGame data: (\{[^\n]+\})\nApply the game transformation scalar_parameter_shift: increase ([a-zA-Z0-9_]+) by (-?[0-9.]+)\.\nCalculate the original model value MINUS the model value after the parameter change\.',prompt)
        if not scalar:raise ValueError('T08_GRAMMAR_REJECTED')
        name,source,changed,increment=scalar.groups()
        f=json.loads(source);f.update(game_name=json.loads(name),transform='scalar_parameter_shift',changed=changed,increment=float(increment))
    _check_facts(f)
    expected_name=legacy.MATRICES[f['game_id']][0] if f['kind']=='matrix' else legacy.SCALAR_MODELS[f['game_id']][0]
    if f['game_name'] not in (expected_name,expected_name+' (scalar)') or t08(f)!=prompt:raise ValueError('T08_NOT_CANONICAL')
    return f


def _expr(f:dict) -> dict:
    if f['kind']=='matrix':
        return {'X':{'operator':'MATRIX_FIRST_ROW_PAYOFF','parameters':'source_matrix'},
                'Y':{'operator':f['transform'],'parameters':'source_matrix','selector':'first_cell_row_payoff'},'OP':'SUBTRACT'}
    return {'X':{'operator':f['game_id'],'parameters':'original'},
            'Y':{'operator':f['game_id'],'parameters':'shifted_parameter_'+f['changed']},'OP':'SUBTRACT'}


def _prompt(state:dict,tier:int) -> str:
    f=state['facts']; kind=f['kind'];op=f['transform']; source_data={k:v for k,v in f.items() if k!='game_name'}
    if tier==8:return t08(f)
    if tier==7:return 'GAME: '+f['game_id']+'\nKIND: '+kind+'\nDATA: '+canon(source_data)+'\nGOAL: ORIGINAL_MINUS_TRANSFORMED'
    if tier==6:return ('GAME: '+f['game_id']+'\nRULE: '+state['rule']+'\nDATA: '+canon(source_data)+'\nGOAL: ORIGINAL_MINUS_TRANSFORMED')
    if tier==5:return ('ORIGINAL_VALUE: '+canon(state['expressions']['X'])+'\nTRANSFORMED_VALUE: '+canon(state['expressions']['Y'])+'\nRULE: '+state['rule']+'\nDATA: '+canon(source_data)+'\nGOAL: ORIGINAL_MINUS_TRANSFORMED')
    if tier==4:return ('Subtract (ORIGINAL '+canon(state['expressions']['X'])+') minus (TRANSFORMED '+canon(state['expressions']['Y'])+').\nDATA: '+canon(source_data))
    if tier==3:return ('Subtract (ORIGINAL '+canon(state['expressions']['X'])+') minus '+fmt(state['Y'])+'.\nDATA: '+canon(source_data))
    if tier==2:return 'Subtract '+fmt(state['X'])+' minus '+fmt(state['Y'])+'.'
    if tier==1:return 'XXXXXX: '+fmt(state['X'])+'\nYYYYYYY: '+fmt(state['Y'])+'\n\nSubtract XXXXXX minus YYYYYYY.'
    if tier==0:return 'XXXXXX: '+fmt(state['X'])+'\nYYYYYYY: '+fmt(state['Y'])+'\n\nOP: SUBTRACT\n\nWhat is the change?'
    raise ValueError(tier)


def begin(public_prompt:str)->dict:
    f=parse_t08(public_prompt)
    state={'tier':8,'prompt':public_prompt,'facts':f,'rule':None,'expressions':None,
           'X':None,'Y':None,'OP':None,'Z':None}
    return state


def _transformed(f:dict):
    if f['kind']=='matrix':
        matrix=legacy.matrix_transform(f['U'],f['transform'])
        return matrix[0][0][0],matrix
    changed=dict(f['parameters']);key=f['changed']
    changed[key]=round(changed[key]+f['increment'],5)
    return legacy.scalar_oracle(f['game_id'],changed),changed


def _original(f:dict):
    if f['kind']=='matrix':return f['U'][0][0][0]
    return legacy.scalar_oracle(f['game_id'],f['parameters'])


def step(state:dict):
    tier=state['tier']
    if tier<1 or tier>8:raise ValueError('TIER_OUT_OF_RANGE')
    if state['prompt']!=_prompt(state,tier):raise ValueError('PROMPT_STATE_NOT_CONSISTENT')
    next_state=deepcopy(state);f=next_state['facts'];trace=[]
    if tier==8:
        if parse_t08(state['prompt'])!=f:raise ValueError('STORY_FACTS_INCONSISTENT')
        trace.append({'opcode':'PARSE_PUBLIC_OBSERVATIONS','output':list(sorted(f))})
    elif tier==7:
        _check_facts(f)
        next_state['rule']='MATRIX_TRANSFORM:'+f['transform'] if f['kind']=='matrix' else 'SCALAR_MODEL:'+f['game_id']+':PARAMETER_SHIFT'
        trace.append({'opcode':'RESOLVE_SOURCE_RULE','output':next_state['rule']})
    elif tier==6:
        next_state['expressions']=_expr(f)
        next_state['OP']='SUBTRACT'
        trace.append({'opcode':'CONSTRUCT_SYMBOLIC_OPERANDS','output':next_state['expressions']})
    elif tier==5:
        if next_state['expressions']!=_expr(f):raise ValueError('EXPRESSION_BINDING_DRIFT')
        trace.append({'opcode':'BIND_ORIGINAL_TRANSFORMED','output':['X','Y']})
    elif tier==4:
        y, full = _transformed(f)
        next_state['Y']=y
        trace.append({'opcode':'APPLY_MATRIX_TRANSFORM' if f['kind']=='matrix' else 'APPLY_SCALAR_PARAMETER_SHIFT',
                      'input':f['transform'],'output':full})
        trace.append({'opcode':'EXTRACT_TRANSFORMED_SCALAR','output':y})
    elif tier==3:
        x=_original(f)
        next_state['X']=x
        trace.append({'opcode':'EXTRACT_ORIGINAL_SCALAR','output':x})
    elif tier==2:
        if next_state['X'] is None or next_state['Y'] is None:raise ValueError('NO_OPERANDS')
        trace.append({'opcode':'BIND_REGISTERS','output':['XXXXXX','YYYYYYY']})
    else:
        next_state['OP']='SUBTRACT'
        trace.append({'opcode':'REVEAL_EXPLICIT_OPERATOR','output':'SUBTRACT'})
    next_state['tier']=tier-1
    next_state['prompt']=_prompt(next_state,tier-1)
    record={'source_tier':tier,'target_tier':tier-1,'program_id':'FULL_T%02d_TO_T%02d'%(tier,tier-1),
            'before_hash':hash_obj(state),'after_hash':hash_obj(next_state),'steps':trace}
    return next_state,record


def finish(state:dict)->dict:
    if state['tier']!=0 or state['prompt']!=_prompt(state,0):raise ValueError('NOT_T00')
    f=state['facts']; x=_original(f);y,full=_transformed(f)
    if not _numeric_eq(x,state['X']) or not _numeric_eq(y,state['Y']):raise AssertionError('FINAL_ORACLE_DISAGREEMENT')
    if f['kind']=='matrix':
        proof={'U_prime':full,'pure_nash':legacy.pure_nash(full),'original_pure_nash':legacy.pure_nash(f['U'])}
    else:proof={'new_parameters':full,'value_before':x,'value_after':y}
    z=mathlib.execute('SUBTRACT',mathlib.frac(state['X']),mathlib.frac(state['Y']))
    return {'X':mathlib.out(mathlib.frac(state['X'])),'Y':mathlib.out(mathlib.frac(state['Y'])),
            'OP':'SUBTRACT','Z':mathlib.out(z),'game_truth':proof,'result_semantics':'legacy_original_minus_transformed_not_action'}


def _numeric_eq(a,b)->bool:
    return abs(float(a)-float(b))<=1e-9*max(1,abs(float(a)),abs(float(b)))


def walk(public_prompt:str)->dict:
    state=begin(public_prompt)
    tiers={8:state['prompt']};edges=[]
    while state['tier']>0:
        state,edge=step(state);edges.append(edge);tiers[state['tier']]=state['prompt']
    return {'end':finish(state),'edges':edges,'tiers':tiers}


DDL='''
CREATE TABLE full_ladder_v5_cases(
 case_id TEXT PRIMARY KEY REFERENCES transformation_cases(case_id),
 game_id TEXT NOT NULL REFERENCES games(game_id), split TEXT NOT NULL,
 kind TEXT NOT NULL, canonical_json TEXT NOT NULL, full_oracle_json TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status='PASS'));
CREATE TABLE full_ladder_v5_tiers(
 case_id TEXT NOT NULL REFERENCES full_ladder_v5_cases(case_id),
 tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8), prompt TEXT NOT NULL,
 PRIMARY KEY(case_id,tier));
CREATE TABLE full_ladder_v5_edges(
 case_id TEXT NOT NULL REFERENCES full_ladder_v5_cases(case_id),
 source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 program_id TEXT NOT NULL, before_hash TEXT NOT NULL,after_hash TEXT NOT NULL,
 local_steps_json TEXT NOT NULL,PRIMARY KEY(case_id,source_tier),
 CHECK(target_tier=source_tier-1));
CREATE TABLE full_ladder_v5_programs(
 program_id TEXT PRIMARY KEY,source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 opcode TEXT NOT NULL,pseudocode TEXT NOT NULL,implementation_sha256 TEXT NOT NULL);
CREATE TABLE full_ladder_v5_game_coverage(
 game_id TEXT PRIMARY KEY REFERENCES games(game_id),
 status TEXT NOT NULL CHECK(status IN ('verified_transform_bridge','catalog_only_no_oracle','verified_pilot_only')),
 verified_cases INTEGER NOT NULL, observed_ops_json TEXT NOT NULL,
 boundary_note TEXT NOT NULL);
'''


def _rows(db):
    return db.execute('''SELECT c.case_id,i.game_id,g.name,c.op_id,c.input_json,c.target_json,c.split
        FROM transformation_cases c JOIN instances i USING(instance_id) JOIN games g ON i.game_id=g.game_id
        ORDER BY c.case_id''').fetchall()


def _assert_legacy_case(f:dict,target:dict,end:dict):
    if not _numeric_eq(end['X']['numerator']/end['X']['denominator'],target['numeric_bridge']['x']):
        raise AssertionError('ORIGINAL_NUMERIC_BRIDGE_MISMATCH')
    if not _numeric_eq(end['Y']['numerator']/end['Y']['denominator'],target['numeric_bridge']['y']):
        raise AssertionError('TRANSFORMED_NUMERIC_BRIDGE_MISMATCH')
    if target['numeric_bridge']['operation']!='subtract':raise AssertionError('WRONG_LEGACY_OPERATION')
    truth=end['game_truth']
    if f['kind']=='matrix':
        for key in ('U_prime','pure_nash','original_pure_nash'):
            if truth[key]!=target[key]:raise AssertionError('MATRIX_FULL_ORACLE_MISMATCH:'+key)
    else:
        if truth['new_parameters']!=target['new_parameters']:raise AssertionError('SHIFTED_PARAMETER_DRIFT')
        if not _numeric_eq(truth['value_after'],target['value_after']):raise AssertionError('SHIFTED_VALUE_DRIFT')
        if not _numeric_eq(truth['value_before'],target['value_before']):raise AssertionError('ORIGINAL_VALUE_DRIFT')


def _program_hash():
    # Source binding is verified at read time with running code's bytecode.
    return hash_obj([begin.__code__.co_code.hex(),step.__code__.co_code.hex(),finish.__code__.co_code.hex(),
                     legacy.matrix_transform.__code__.co_code.hex(),legacy.scalar_oracle.__code__.co_code.hex()])


def build_and_verify(db:sqlite3.Connection)->dict:
    db.executescript(DDL)
    stamp=_program_hash()
    for t,l,op,description in STEPS:
        db.execute('INSERT INTO full_ladder_v5_programs VALUES (?,?,?,?,?,?)',
                   (f'FULL_T{t:02d}_TO_T{l:02d}',t,l,op,description,stamp))
    summary={}
    for row in _rows(db):
        case_id,gid,name,op,source,target_json,split=row
        f=_facts_for_db(row)
        # An executable source definition is never silently replaced by a synthetic policy.
        _check_facts(f)
        first=t08(f)
        proof=walk(first)
        target=json.loads(target_json)
        _assert_legacy_case(f,target,proof['end'])
        db.execute('INSERT INTO full_ladder_v5_cases VALUES (?,?,?,?,?,?,?,?)',
                   (case_id,gid,split,f['kind'],canon({k:proof['end'][k] for k in ('X','Y','OP','Z','result_semantics')}),
                    canon(proof['end']['game_truth']),hash_obj(first),'PASS'))
        db.executemany('INSERT INTO full_ladder_v5_tiers VALUES (?,?,?)',
                       [(case_id,t,proof['tiers'][t]) for t in range(9)])
        db.executemany('INSERT INTO full_ladder_v5_edges VALUES (?,?,?,?,?,?,?)',
                       [(case_id,e['source_tier'],e['target_tier'],e['program_id'],e['before_hash'],e['after_hash'],canon(e['steps'])) for e in proof['edges']])
        info=summary.setdefault(gid,{'count':0,'ops':set()});info['count']+=1;info['ops'].add(op)
    for gid,status in db.execute('SELECT game_id,status FROM games ORDER BY game_id').fetchall():
        data=summary.get(gid,{'count':0,'ops':set()})
        if data['count']:cov='verified_transform_bridge';note='Nine-tier source-derived legacy transformation bridge; final Z is original minus transformed value, NOT strategic action.'
        elif status=='oracle_verified':cov='verified_pilot_only';note='Legacy transformation cases absent; separate synthetic pilot may exist; do not claim full bridge.'
        else:cov='catalog_only_no_oracle';note='Game is catalog-only; no executable source oracle or supervised nine-tier ladder.'
        db.execute('INSERT INTO full_ladder_v5_game_coverage VALUES (?,?,?,?,?)',
                   (gid,cov,data['count'],canon(sorted(data['ops'])),note))
    return verify(db)


def verify(db:sqlite3.Connection)->dict:
    stamp=_program_hash()
    actual=db.execute('SELECT program_id,source_tier,target_tier,opcode,pseudocode,implementation_sha256 FROM full_ladder_v5_programs ORDER BY source_tier DESC').fetchall()
    expect=[(f'FULL_T{t:02d}_TO_T{l:02d}',t,l,op,desc,stamp) for t,l,op,desc in STEPS]
    if actual!=expect:raise AssertionError('FULL_PROGRAM_DEFINITION_DRIFT')
    if db.execute('PRAGMA foreign_key_check').fetchall():raise AssertionError('FULL_FOREIGN_KEY_DRIFT')
    cases=db.execute('SELECT case_id,game_id,canonical_json,full_oracle_json,prompt_sha256 FROM full_ladder_v5_cases ORDER BY case_id').fetchall()
    if len(cases)!=db.execute('SELECT COUNT(*) FROM transformation_cases').fetchone()[0]:
        raise AssertionError('FULL_CASE_NOT_COMPLETE')
    game_names={r[0] for r in db.execute('SELECT game_id FROM games')}
    if len(game_names)!=db.execute('SELECT COUNT(*) FROM full_ladder_v5_game_coverage').fetchone()[0]:
        raise AssertionError('FULL_GAME_COVERAGE_NOT_COMPLETE')
    n_edges=0;n_tiers=0
    for cid,gid,canon_text,oracle_text,prompt_sha in cases:
        stored=db.execute('SELECT tier,prompt FROM full_ladder_v5_tiers WHERE case_id=? ORDER BY tier DESC',(cid,)).fetchall()
        if len(stored)!=9:raise AssertionError('FULL_TIER_COVERAGE')
        first=stored[0][1]
        if hash_obj(first)!=prompt_sha:raise AssertionError('FULL_T08_TAMPER')
        proof=walk(first)
        if canon_text!=canon({k:proof['end'][k] for k in ('X','Y','OP','Z','result_semantics')}) or oracle_text!=canon(proof['end']['game_truth']):
            raise AssertionError('FULL_GOLD_MISMATCH '+cid)
        if stored!=[(t,proof['tiers'][t]) for t in range(8,-1,-1)]:raise AssertionError('FULL_TIER_TAMPER '+cid)
        edges=db.execute('SELECT source_tier,target_tier,program_id,before_hash,after_hash,local_steps_json FROM full_ladder_v5_edges WHERE case_id=? ORDER BY source_tier DESC',(cid,)).fetchall()
        expected=[(e['source_tier'],e['target_tier'],e['program_id'],e['before_hash'],e['after_hash'],canon(e['steps'])) for e in proof['edges']]
        if edges!=expected:raise AssertionError('FULL_EDGE_TAMPER '+cid)
        for e in proof['edges']:
            if e['source_tier']>=5 and any(s['opcode'].startswith('APPLY_') for s in e['steps']):
                raise AssertionError('FULL_PREMATURE_MATH '+cid)
        # check full game truth against original CASE, not just frozen projection
        target_text=db.execute('SELECT target_json FROM transformation_cases WHERE case_id=?',(cid,)).fetchone()[0]
        _assert_legacy_case(parse_t08(first),json.loads(target_text),proof['end'])
        n_edges+=8;n_tiers+=9
    if db.execute("SELECT COUNT(*) FROM full_ladder_v5_game_coverage WHERE status='catalog_only_no_oracle'").fetchone()[0]!=db.execute("SELECT COUNT(*) FROM games WHERE status='catalog_only'").fetchone()[0]:
        raise AssertionError('FALSE_GAME_COVERAGE')
    return {'status':'PASS','source_cases':len(cases),'stateful_edges':n_edges,'tiers':n_tiers,
            'games_with_full_bridges':db.execute("SELECT COUNT(*) FROM full_ladder_v5_game_coverage WHERE status='verified_transform_bridge'").fetchone()[0],
            'catalog_only_blocked':db.execute("SELECT COUNT(*) FROM full_ladder_v5_game_coverage WHERE status='catalog_only_no_oracle'").fetchone()[0],
            'full_legacy_oracle_verified':True,'witness_used_in_descent':False,'model_trained':False,
            'proof_scope':'structured_payoff_and_parameter_transformation_bridges_not_general_decision_making'}


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True)
    p.add_argument('--case',default=None)
    p.add_argument('--limit',type=int,default=1)
    args=p.parse_args(argv)
    with sqlite3.connect(args.database) as db:
        rows=db.execute('SELECT case_id FROM full_ladder_v5_cases ORDER BY case_id LIMIT ?', (args.limit,)).fetchall() if args.case is None else [(args.case,)]
        for (cid,) in rows:
            row=db.execute('SELECT prompt FROM full_ladder_v5_tiers WHERE case_id=? AND tier=8',(cid,)).fetchone()
            if row is None:raise ValueError('CASE_ID_NOT_FOUND '+cid)
            proof=walk(row[0]);print('CASE',cid)
            for t in range(8,-1,-1):print(f'--- T{t:02d} ---\n'+proof['tiers'][t])
            print('CANONICAL',canon({k:proof['end'][k] for k in ('X','Y','OP','Z')}))
            for e in proof['edges']:print(e['program_id'],canon(e['steps']))
    return 0

if __name__=='__main__':raise SystemExit(main())
