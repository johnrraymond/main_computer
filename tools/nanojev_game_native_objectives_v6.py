"""V6 source-derived game-native objectives, independent of the v5 delta bridges.

Every descent starts with public facts only. Source-defined mathematical operators
construct T00 and final game-native Z. All 9 tiers, 8 edges, and postcondition
checks are reproducible. Catalog-only games never become supervised targets.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import re
import sqlite3
from typing import Any

from . import nanojev_game_transformation_library as legacy
from . import nanojev_game_math_registry as mathlib

VERSION='nanojev.game-native-ladders.v6'

# These are mathematical equations for the REAL quantities returned by source
# scalar oracles. The source oracle is evaluated independently at the end.
SCALAR_FORMULAS={
 'hawk_dove_mixed':'V/C',
 'nash_bargain_split':'d1+(V-d1-d2)/2',
 'bayes_posterior':'(prior*l_true)/(prior*l_true+(1-prior)*l_false)',
 'discounted_repeat':'r/(1-gamma)',
 'markov_one_step':'r+gamma*(p*v_good+(1-p)*v_bad)',
 'lanchester_square':'A*A-B*B',
 'blotto_remaining':'B-(x1+x2)',
 'inspection_loss':'attack_probability*((1-detection_probability)*damage)',
 'pursuit_gap':'(gap+evader_speed)-pursuer_speed',
 'routing_delay':'base+congestion_coefficient*flow',
 'second_price_utility':'value-second_price',
 'coalition_surplus':'(v_merged-v_one)-v_two',
 'quorum_margin':'honest_votes-quorum',
 'population_signal':'benefit-cost_per_agent*n',
 'cournot_quantity':'(a-c)/(3*b)',
 'robust_minimum':'max(loss_case_1,loss_case_2)',
 'captain_fuel_reserve':'(initial-reserve_required)-fuel_spent',
 'captain_crew_risk':'crew_count*(1-casualty_probability)',
 'captain_signed_offset':'ship_offset-beacon_offset',
}
assert set(SCALAR_FORMULAS)==set(legacy.SCALAR_MODELS)

OP_AST={ast.Add:'ADD',ast.Sub:'SUBTRACT',ast.Mult:'MULTIPLY',ast.Div:'DIVIDE'}

def canon(v:Any)->str:
 return json.dumps(mathlib.out(v),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)

def sha(v:Any)->str:return hashlib.sha256(canon(v).encode()).hexdigest()

def num(v:Any)->Fraction:return mathlib.frac(v)

def fmt(v:Any)->str:
 if isinstance(v,Fraction):return str(v.numerator) if v.denominator==1 else str(float(v))
 return str(v)

def _expr(gid:str)->ast.AST:
 tree=ast.parse(SCALAR_FORMULAS[gid],mode='eval').body
 def validate(n):
  if isinstance(n,ast.BinOp) and type(n.op) in OP_AST:validate(n.left);validate(n.right)
  elif isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='max' and len(n.args)==2 and not n.keywords:
   for x in n.args:validate(x)
  elif isinstance(n,ast.Name):
   if n.id not in legacy.SCALAR_MODELS[gid][3]:raise ValueError('UNKNOWN_SCALAR_PARAMETER')
  elif isinstance(n,ast.Constant) and type(n.value) in (int,float):pass
  else:raise ValueError('UNSUPPORTED_NATIVE_FORMULA')
 validate(tree)
 if not isinstance(tree,(ast.BinOp,ast.Call)):raise ValueError('ROOT_NOT_BINARY')
 return tree

def _children(node:ast.AST):
 if isinstance(node,ast.BinOp):return node.left,node.right,OP_AST[type(node.op)]
 if isinstance(node,ast.Call):return node.args[0],node.args[1],'MAX'
 raise ValueError('ROOT_NOT_BINARY')

def _evaluate(node:ast.AST,p:dict,trace:list)->Fraction:
 if isinstance(node,ast.Name):return num(p[node.id])
 if isinstance(node,ast.Constant):return num(node.value)
 left,right,op=_children(node)
 a,b=_evaluate(left,p,trace),_evaluate(right,p,trace)
 z=mathlib.execute(op,a,b)
 trace.append({'operator':op,'args':mathlib.out([a,b]),'result':mathlib.out(z)})
 return z

def _mixed(m):
 # p(row0): makes column player indifferent; q(column0): makes row player indifferent.
 u00,u01,u10,u11=[num(m[r][c][0]) for r,c in ((0,0),(0,1),(1,0),(1,1))]
 v00,v01,v10,v11=[num(m[r][c][1]) for r,c in ((0,0),(0,1),(1,0),(1,1))]
 dp=v00-v10-v01+v11;np=v11-v10
 dq=u00-u01-u10+u11;nq=u11-u01
 if not dp or not dq:return None
 p,q=np/dp,nq/dq
 if not (0<p<1 and 0<q<1):return None
 return (np,dp,nq,dq)

def _facts(kind,gid,title,source,mode,selector=None):
 if kind=='matrix':
  f={'kind':'matrix','game_id':gid,'game_name':title,'row_actions':source['row_actions'],
     'column_actions':source['column_actions'],'U':source['U'],'mode':mode,'selector':selector}
 else:
  f={'kind':'scalar','game_id':gid,'game_name':title,'parameters':source['parameters'],
     'mode':'scalar_value','formula_id':gid}
 return f

def render08(f):
 title=json.dumps(f['game_name'],ensure_ascii=False)
 if f['kind']=='scalar':
  lines=[f'{k} = {fmt(num(v))}' for k,v in sorted(f['parameters'].items())]
  return ('You are evaluating the game '+title+'.\nAvailable observations:\n'+ '\n'.join(lines)+
    '\nAccording to the established mathematical rule of this game, what value does the model produce?')
 lines=['You are a captain in '+title+'.',
        'Your actions: '+', '.join(json.dumps(a) for a in f['row_actions'])+'.',
        'Rival actions: '+', '.join(json.dumps(a) for a in f['column_actions'])+'.',
        'Outcomes (your utility, rival utility):']
 for r,ra in enumerate(f['row_actions']):
  for c,ca in enumerate(f['column_actions']):
   u,v=f['U'][r][c]
   lines.append(f'- {json.dumps(ra)} against {json.dumps(ca)}: ({fmt(num(u))}, {fmt(num(v))})')
 if f['mode']=='best_response':
  lines.append('Your rival chooses '+json.dumps(f['column_actions'][f['selector']])+'. Which of your actions maximizes your payoff? Ties favor the first listed action.')
 elif f['mode']=='mixed_row':
  lines.append('Assuming a fully mixed Nash equilibrium, what probability should YOU assign to your FIRST action?')
 else:
  lines.append('Assuming a fully mixed Nash equilibrium, what probability should your RIVAL assign to their FIRST action?')
 return '\n'.join(lines)

def parse08(s:str, *, verify_domain:bool=True):
 # Parse full public prompt independently of frozen case records.
 head,sep,tail=s.partition('\n')
 if not sep:raise ValueError('NO_GAME_HEADER')
 if head.startswith('You are evaluating the game '):
  name=json.loads(head[len('You are evaluating the game '):].removesuffix('.'))
  ids=[gid for gid,(title,*_) in legacy.SCALAR_MODELS.items() if title==name]
  if len(ids)!=1:raise ValueError('UNKNOWN_SCALAR_GAME')
  gid=ids[0];lines=tail.splitlines()
  if not lines or lines[0]!='Available observations:':raise ValueError('BAD_OBSERVATIONS')
  p={}
  for line in lines[1:-1]:
   k,sep,v=line.partition(' = ')
   if not sep or k in p:p.clear();break
   p[k]=float(v)
  f=_facts('scalar',gid,name,{'parameters':p},'scalar_value')
 else:
  if not head.startswith('You are a captain in '):raise ValueError('UNKNOWN_NATIVE_STORY')
  name=json.loads(head[len('You are a captain in '):].removesuffix('.'))
  ids=[gid for gid,(title,*_) in legacy.MATRICES.items() if title==name]
  if len(ids)!=1:raise ValueError('UNKNOWN_MATRIX_GAME')
  gid=ids[0];lines=tail.splitlines()
  if len(lines)<6:raise ValueError('TRUNCATED_MATRIX_STORY')
  def actions(line,prefix):
   if not line.startswith(prefix) or not line.endswith('.'):raise ValueError('ACTION_LINE')
   return json.loads('['+line[len(prefix):-1]+']')
  ra=actions(lines[0],'Your actions: ');ca=actions(lines[1],'Rival actions: ')
  if lines[2]!='Outcomes (your utility, rival utility):':raise ValueError('PAYOFF_HEADER')
  if len(ra)!=2 or len(ca)!=2 or len(lines)!=8:raise ValueError('MATRIX_SHAPE')
  U=[]
  for r in range(2):
   row=[]
   for c in range(2):
    prefix=f'- {json.dumps(ra[r])} against {json.dumps(ca[c])}: ('
    line=lines[3+r*2+c]
    if not line.startswith(prefix) or not line.endswith(')'):raise ValueError('PAYOFF_LINE')
    a,b=line[len(prefix):-1].split(', ')
    row.append([float(a),float(b)])
   U.append(row)
  question=lines[-1]
  if question.startswith('Your rival chooses '):
   match=re.fullmatch(r'Your rival chooses (".*")\. Which of your actions maximizes your payoff\? Ties favor the first listed action\.',question)
   if not match:raise ValueError('ACTION_QUESTION')
   chosen=json.loads(match[1]);mode='best_response';selector=ca.index(chosen)
  elif question=='Assuming a fully mixed Nash equilibrium, what probability should YOU assign to your FIRST action?':mode='mixed_row';selector=None
  elif question=='Assuming a fully mixed Nash equilibrium, what probability should your RIVAL assign to their FIRST action?':mode='mixed_col';selector=None
  else:raise ValueError('QUESTION_NOT_SUPPORTED')
  f=_facts('matrix',gid,name,{'U':U,'row_actions':ra,'column_actions':ca},mode,selector)
 _validate_facts(f, check_oracle=verify_domain)
 if render08(f)!=s:raise ValueError('T08_NOT_CANONICAL')
 return f

def _validate_facts(f, *, check_oracle:bool=True):
 if f['kind']=='scalar':
  if f['game_id'] not in SCALAR_FORMULAS or set(f['parameters'])!=set(legacy.SCALAR_MODELS[f['game_id']][3]):raise ValueError('SCALAR_DOMAIN')
  _expr(f['game_id'])
  if check_oracle:legacy.scalar_oracle(f['game_id'],f['parameters'])
 else:
  if f['game_id'] not in legacy.MATRICES or len(f['U'])!=2 or any(len(r)!=2 for r in f['U']):raise ValueError('MATRIX_DOMAIN')
  if f['mode']=='best_response' and f['selector'] not in (0,1):raise ValueError('RIVAL_ACTION_MISSING')
  if check_oracle and f['mode'].startswith('mixed') and _mixed(f['U']) is None:raise ValueError('NO_INTERIOR_MIXED_NASH')

def _top(f):
 if f['kind']=='scalar':return _children(_expr(f['game_id']))
 if f['mode']=='best_response':return 'YOUR_FIRST_ACTION','YOUR_SECOND_ACTION','ARGMAX'
 return ('INDIFFERENCE_NUMERATOR','INDIFFERENCE_DENOMINATOR','DIVIDE')

def _one_operand(f,side):
 steps=[]
 if f['kind']=='scalar':
  a,b,op=_top(f);value=_evaluate(a if side=='X' else b,f['parameters'],steps)
 elif f['mode']=='best_response':
  idx=0 if side=='X' else 1
  value=num(f['U'][idx][f['selector']][0])
  steps.append({'operator':'LOOKUP_ACTION_PAYOFF','row':idx,'column':f['selector'],'result':mathlib.out(value)})
 else:
  np,dp,nq,dq=_mixed(f['U'])
  value=(np if side=='X' else dp) if f['mode']=='mixed_row' else (nq if side=='X' else dq)
  steps.append({'operator':'SOLVE_INDIFFERENCE_COMPONENT','side':side,'result':mathlib.out(value)})
 return value,steps

def _compute(f):
 x,stx=_one_operand(f,'X');y,sty=_one_operand(f,'Y');op=_top(f)[2]
 if op=='ARGMAX':z=mathlib.execute(op,[x,y]);meaning=f['row_actions'][z]
 else:z=mathlib.execute(op,x,y);meaning='scalar_value' if f['kind']=='scalar' else 'mixed_strategy_probability'
 return x,y,op,z,meaning,stx,sty

def _prompt(st,t):
 f=st['facts'];body=render08(f)
 if t==8:return body
 if t==7:return 'GAME_FACTS: '+canon(f)+'\nOBJECTIVE: '+f['mode']
 if t==6:return 'RULE: '+st['rule']+'\nGAME_FACTS: '+canon(f)
 if t==5:return 'OPERANDS: '+canon(st['expressions'])+'\nRULE: '+st['rule']+'\nGAME_FACTS: '+canon(f)
 if t==4:return 'COMPUTE '+st['OP']+' WITH '+canon(st['expressions'])+'\nGAME_FACTS: '+canon(f)
 if t==3:return 'COMPUTE '+st['OP']+' WITH '+canon(st['expressions']['X'])+' AND '+fmt(st['Y'])+'\nGAME_FACTS: '+canon(f)
 if t==2:return 'Compute '+st['OP']+' using '+fmt(st['X'])+' and '+fmt(st['Y'])+'.'
 if t==1:return 'XXXXXX: '+fmt(st['X'])+'\nYYYYYYY: '+fmt(st['Y'])+'\n\nCompute '+st['OP']+' on XXXXXX and YYYYYYY.'
 if t==0:return 'XXXXXX: '+fmt(st['X'])+'\nYYYYYYY: '+fmt(st['Y'])+'\n\nOP: '+st['OP']+'\n\nReturn the game-native result.'
 raise ValueError('TIER_RANGE')

def begin(prompt):
 f=parse08(prompt)
 return {'tier':8,'prompt':prompt,'facts':f,'rule':None,'expressions':None,'X':None,'Y':None,'OP':None,'Z':None}

def step(st):
 i=st['tier'];assert 1<=i<=8
 if st['prompt']!=_prompt(st,i):raise ValueError('INPUT_PROMPT_TAMPERED')
 nxt=deepcopy(st);f=nxt['facts'];trace=[]
 if i==8:
  if parse08(st['prompt'])!=f:raise ValueError('FACTS_NOT_PARSED')
  trace.append({'operator':'PARSE_PUBLIC_GAME','output':f['game_id']})
 elif i==7:
  _validate_facts(f);nxt['rule']=('SCALAR:'+f['formula_id'] if f['kind']=='scalar' else 'MATRIX:'+f['mode'])
  trace.append({'operator':'SELECT_NATIVE_RULE','output':nxt['rule']})
 elif i==6:
  a,b,op=_top(f)
  nxt['expressions']={'X':ast.unparse(a) if isinstance(a,ast.AST) else a,
                      'Y':ast.unparse(b) if isinstance(b,ast.AST) else b,'OP':op}
  nxt['OP']=op;trace.append({'operator':'CONSTRUCT_NATIVE_EXPRESSIONS','output':nxt['expressions']})
 elif i==5:
  trace.append({'operator':'BIND_NATIVE_OPERANDS','output':['X','Y']})
 elif i==4:
  y,t=_one_operand(f,'Y');nxt['Y']=y;trace.extend(t)
 elif i==3:
  x,t=_one_operand(f,'X');nxt['X']=x;trace.extend(t)
 elif i==2:
  if nxt['X'] is None or nxt['Y'] is None:raise ValueError('OPERANDS_MISSING')
  trace.append({'operator':'BIND_REGISTERS','output':['XXXXXX','YYYYYYY']})
 elif i==1:
  trace.append({'operator':'EXPOSE_NATIVE_OPERATION','output':nxt['OP']})
 nxt['tier']=i-1;nxt['prompt']=_prompt(nxt,i-1)
 return nxt,{'from':i,'to':i-1,'before_hash':sha(st),'after_hash':sha(nxt),'steps':trace}

def finish(st):
 if st['tier']!=0 or st['prompt']!=_prompt(st,0):raise ValueError('INVALID_T00')
 f=st['facts'];x,y,op,z,sem,_,_=_compute(f)
 if (x,y,op)!=(st['X'],st['Y'],st['OP']):raise ValueError('LOCAL_OPERANDS_WRONG')
 if f['kind']=='scalar':
  independent=legacy.scalar_oracle(f['game_id'],f['parameters'])
  if abs(float(z)-independent)>1e-8*max(1,abs(independent)):raise ValueError('SCALAR_NATIVE_ORACLE_MISMATCH')
 elif f['mode']=='best_response':
  pay=[row[f['selector']][0] for row in f['U']]
  if z!=pay.index(max(pay)):raise ValueError('ACTION_ORACLE_MISMATCH')
 else:
  p=float(z);m=f['U']
  if f['mode']=='mixed_row':
   v0=p*m[0][0][1]+(1-p)*m[1][0][1];v1=p*m[0][1][1]+(1-p)*m[1][1][1]
  else:
   v0=p*m[0][0][0]+(1-p)*m[0][1][0];v1=p*m[1][0][0]+(1-p)*m[1][1][0]
  if abs(v0-v1)>1e-8*max(1,abs(v0),abs(v1)):raise ValueError('MIXED_EQUILIBRIUM_INDIFFERENCE_FAIL')
 return {'X':mathlib.out(x),'Y':mathlib.out(y),'OP':op,'Z':mathlib.out(z),
         'Z_semantics':sem,'objective':f['mode'],'game_id':f['game_id']}

def walk(prompt):
 st=begin(prompt);tiers={8:prompt};edges=[]
 while st['tier']:
  st,e=step(st);tiers[st['tier']]=st['prompt'];edges.append(e)
 return {'answer':finish(st),'tiers':tiers,'edges':edges}

# The payoff matrix permutation is *paired* with action-label reindexing.
# The legacy matrix_transform returns a positional tensor only; it remains
# unchanged so all 2096 original regression cases continue to replay.

def labeled_matrix_transform(rows,columns,U,op):
 r=list(rows);c=list(columns);original=deepcopy(U)
 transformed=legacy.matrix_transform(U,op)
 if op=='permute_row':r.reverse()
 elif op=='permute_column':c.reverse()
 elif op=='swap_players':r,c=c,r
 return {'row_actions':r,'column_actions':c,'U':transformed,
         'representation_only':op in ('permute_row','permute_column','swap_players','compose_transformations'),
         'strategically_equivalent':op in ('permute_row','permute_column','swap_players','compose_transformations','positive_affine','shared_payoff_shift')}

def _semantic_payoffs(f):
 return {(r,c):(num(f['U'][i][j][0]),num(f['U'][i][j][1]))
         for i,r in enumerate(f['row_actions']) for j,c in enumerate(f['column_actions'])}

def audit_permutation(source,op):
 before=_semantic_payoffs(source)
 after=labeled_matrix_transform(source['row_actions'],source['column_actions'],source['U'],op)
 if op in ('permute_row','permute_column','compose_transformations'):
  if _semantic_payoffs(after)!=before:raise ValueError('ACTION_PAYOFF_RELABEL_DRIFT')
  prev=legacy.pure_nash(source['U']);new=legacy.pure_nash(after['U'])
  old_profiles={(source['row_actions'][r],source['column_actions'][c]) for r,c in prev}
  new_profiles={(after['row_actions'][r],after['column_actions'][c]) for r,c in new}
  if old_profiles!=new_profiles:raise ValueError('EQUILIBRIUM_LABEL_DRIFT')
  for cname in source['column_actions']:
   j=source['column_actions'].index(cname);nj=after['column_actions'].index(cname)
   old_winners={source['row_actions'][i] for i in range(2) if source['U'][i][j][0]==max(source['U'][k][j][0] for k in range(2))}
   new_winners={after['row_actions'][i] for i in range(2) if after['U'][i][nj][0]==max(after['U'][k][nj][0] for k in range(2))}
   if old_winners!=new_winners:raise ValueError('BEST_RESPONSE_LABEL_DRIFT')
 return after

DDL='''
CREATE TABLE native_v6_cases(
 case_id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(game_id),
 instance_id TEXT NOT NULL REFERENCES instances(instance_id),split TEXT NOT NULL,
 kind TEXT NOT NULL,objective TEXT NOT NULL,answer_json TEXT NOT NULL,
 t08_sha256 TEXT NOT NULL,status TEXT NOT NULL CHECK(status='PASS'));
CREATE TABLE native_v6_tiers(case_id TEXT NOT NULL REFERENCES native_v6_cases(case_id),
 tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8),prompt TEXT NOT NULL,
 PRIMARY KEY(case_id,tier));
CREATE TABLE native_v6_edges(case_id TEXT NOT NULL REFERENCES native_v6_cases(case_id),
 source_tier INTEGER NOT NULL,target_tier INTEGER NOT NULL,
 before_hash TEXT NOT NULL,after_hash TEXT NOT NULL,steps_json TEXT NOT NULL,
 PRIMARY KEY(case_id,source_tier),CHECK(target_tier=source_tier-1));
CREATE TABLE native_v6_game_coverage(game_id TEXT PRIMARY KEY REFERENCES games(game_id),
 objectives_json TEXT NOT NULL,verified_cases INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('native_verified','catalog_only_no_oracle','separate_pilot')));
CREATE TABLE native_v6_matrix_invariants(
 case_id TEXT PRIMARY KEY REFERENCES transformation_cases(case_id),
 operation TEXT NOT NULL,result_json TEXT NOT NULL);
'''

def _expected_facts(conn):
 for iid,gid,name,kind,src,split in conn.execute('''SELECT i.instance_id,i.game_id,g.name,i.kind,i.source_json,i.split
 FROM instances i JOIN games g ON g.game_id=i.game_id ORDER BY i.instance_id'''):
  source=json.loads(src)
  if kind=='scalar':yield iid,split,_facts('scalar',gid,name.removesuffix(' (scalar)'),source,'scalar_value')
  else:
   for col in range(2):yield iid,split,_facts('matrix',gid,name,source,'best_response',col)
   if _mixed(source['U']):
    for mode in ('mixed_row','mixed_col'):yield iid,split,_facts('matrix',gid,name,source,mode)

def build_and_verify(conn):
 conn.executescript(DDL)
 per={}
 for iid,split,f in _expected_facts(conn):
  first=render08(f);proof=walk(first)
  case_id=sha([iid,f['mode'],f.get('selector')]);gid=f['game_id'];mode=f['mode']
  conn.execute('INSERT INTO native_v6_cases VALUES (?,?,?,?,?,?,?,?,?)',
     (case_id,gid,iid,split,f['kind'],mode,canon(proof['answer']),sha(first),'PASS'))
  conn.executemany('INSERT INTO native_v6_tiers VALUES (?,?,?)',[(case_id,t,proof['tiers'][t]) for t in range(9)])
  conn.executemany('INSERT INTO native_v6_edges VALUES (?,?,?,?,?,?)',
      [(case_id,e['from'],e['to'],e['before_hash'],e['after_hash'],canon(e['steps'])) for e in proof['edges']])
  per.setdefault(gid,{}).setdefault(mode,0);per[gid][mode]+=1
 for cid,op,src in conn.execute('''SELECT c.case_id,c.op_id,c.input_json FROM transformation_cases c
 JOIN instances i ON i.instance_id=c.instance_id WHERE i.kind='matrix' ORDER BY c.case_id'''):
  s=json.loads(src);audit=audit_permutation(s,op)
  conn.execute('INSERT INTO native_v6_matrix_invariants VALUES (?,?,?)',
    (cid,op,canon({'row_actions':audit['row_actions'],'column_actions':audit['column_actions'],
                  'representation_only':audit['representation_only'],
                  'strategically_equivalent':audit['strategically_equivalent']})))
 for gid,status in conn.execute('SELECT game_id,status FROM games ORDER BY game_id'):
  values=per.get(gid,{})
  cat='native_verified' if values else 'catalog_only_no_oracle' if status=='catalog_only' else 'separate_pilot'
  conn.execute('INSERT INTO native_v6_game_coverage VALUES (?,?,?,?)',(gid,canon(values),sum(values.values()),cat))
 return verify(conn)

def verify(conn):
 total=conn.execute('SELECT COUNT(*) FROM native_v6_cases').fetchone()[0]
 if not total:raise AssertionError('NATIVE_CASES_EMPTY')
 for cid,ans_json,sha08 in conn.execute('SELECT case_id,answer_json,t08_sha256 FROM native_v6_cases ORDER BY case_id'):
  stored=conn.execute('SELECT tier,prompt FROM native_v6_tiers WHERE case_id=? ORDER BY tier DESC',(cid,)).fetchall()
  if len(stored)!=9 or sha(stored[0][1])!=sha08:raise AssertionError('NATIVE_TIER_DRIFT')
  p=walk(stored[0][1]);
  if canon(p['answer'])!=ans_json or stored!=[(t,p['tiers'][t]) for t in range(8,-1,-1)]:raise AssertionError('NATIVE_ORACLE_TAMPER '+cid)
  edges=conn.execute('SELECT source_tier,target_tier,before_hash,after_hash,steps_json FROM native_v6_edges WHERE case_id=? ORDER BY source_tier DESC',(cid,)).fetchall()
  exp=[(e['from'],e['to'],e['before_hash'],e['after_hash'],canon(e['steps'])) for e in p['edges']]
  if edges!=exp:raise AssertionError('NATIVE_EDGE_TAMPER '+cid)
  for e in p['edges']:
   if e['from']>=5 and any(x['operator'] in OP_AST.values() or x['operator']=='LOOKUP_ACTION_PAYOFF' for x in e['steps']):
    raise AssertionError('PREMATURE_NUMERICAL_EXECUTION')
 if conn.execute('PRAGMA foreign_key_check').fetchall():raise AssertionError('NATIVE_FOREIGN_KEYS')
 invariant_count=0
 for cid,op,src,text in conn.execute('''SELECT a.case_id,a.operation,c.input_json,a.result_json
 FROM native_v6_matrix_invariants a JOIN transformation_cases c USING(case_id) ORDER BY a.case_id'''):
  s=json.loads(src);a=audit_permutation(s,op)
  if canon({'row_actions':a['row_actions'],'column_actions':a['column_actions'],
            'representation_only':a['representation_only'],
            'strategically_equivalent':a['strategically_equivalent']})!=text:raise AssertionError('MATRIX_INVARIANT_TAMPER')
  invariant_count+=1
 if conn.execute('SELECT COUNT(*) FROM native_v6_game_coverage').fetchone()[0]!=conn.execute('SELECT COUNT(*) FROM games').fetchone()[0]:raise AssertionError('NATIVE_COVERAGE_MISSING')
 n_games=conn.execute("SELECT COUNT(*) FROM native_v6_game_coverage WHERE status='native_verified'").fetchone()[0]
 if n_games!=len(legacy.MATRICES)+len(legacy.SCALAR_MODELS):raise AssertionError('NATIVE_GAME_COUNT')
 return {'status':'PASS','games_with_native_objectives':n_games,'native_cases':total,
         'native_tiers':9*total,'native_edges':8*total,
         'matrix_invariants':invariant_count,'catalog_only_blocked':conn.execute("SELECT COUNT(*) FROM native_v6_game_coverage WHERE status='catalog_only_no_oracle'").fetchone()[0],
         'witness_used_in_descent':False,'source_generated':True,'training_performed':False}

def main(argv=None):
 import argparse
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True);p.add_argument('--case');p.add_argument('--limit',type=int,default=1)
 a=p.parse_args(argv)
 with sqlite3.connect(a.database) as db:
  ids=[a.case] if a.case else [r[0] for r in db.execute('SELECT case_id FROM native_v6_cases ORDER BY case_id LIMIT ?',(a.limit,))]
  for cid in ids:
   row=db.execute('SELECT prompt FROM native_v6_tiers WHERE case_id=? AND tier=8',(cid,)).fetchone()
   if row is None:raise ValueError('CASE_NOT_FOUND')
   result=walk(row[0]);print('CASE',cid)
   for t in range(8,-1,-1):print(f'--- T{t:02d} ---\n{result["tiers"][t]}')
   print('NATIVE_ANSWER',canon(result['answer']))
   for e in result['edges']:print(f'T{e["from"]:02d}->T{e["to"]:02d}',canon(e['steps']))
 return 0

if __name__=='__main__':raise SystemExit(main())
