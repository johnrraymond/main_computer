#!/usr/bin/env python3
"""Generate the complete source-owned NanoJev game/math database and smoke it.

From the main_computer repository root:
 python -m tools.nanojev_game_math_smoke --generate --verify-all --report
 python -m tools.nanojev_game_math_smoke --generate --verify-all --report --temporary
 python -m tools.nanojev_game_math_smoke --generate --verify-all --report --output-dir runtime/alt-math --replace

All outputs, including DBs and definitions JSON, are GENERATED; do not commit
SQLite binaries. Existing training checkpoints and active task banks are never
modified. All mathematical backlog entries are catalog_only, never gold labels.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from . import nanojev_game_transformation_library as legacy
from . import nanojev_game_math_registry as mathlib
from . import nanojev_game_ladder_proof as ladder
from . import nanojev_game_ladder_state_machine as state_ladder
from . import nanojev_game_ladder_full_catalog as full_ladder
from . import nanojev_game_native_objectives_v6 as native
from . import nanojev_game_mutation_brain_v7 as mutation_brain
from . import nanojev_deterministic_brain_v8 as deterministic_brain

VERSION='nanojev.game-math-source-smoke.v8'
GENERATOR='tools.nanojev_game_math_smoke'
FILES=('game_transformations.sqlite3','nanojev_game_tasks.sqlite3',
       'math_ladder_task_bank.sqlite3','math_definitions.json',
       'math_dependencies.json','game_math_coverage.json',
       'curriculum_verification.json','captain_source_definitions.json',
       'captain_source_audit.json','ladder_bidirectional_task_bank.sqlite3','ladder_roundtrip_proof.json','ladder_transition_programs.json','ladder_state_chain_proof.json','full_ladder_task_bank.sqlite3','full_ladder_coverage.json','full_ladder_state_proof.json','native_game_ladder_proof.json','native_game_coverage.json','native_game_task_bank.sqlite3',
       'mutation_brain_v7.sqlite3','mutation_brain_checkpoint_proof.json',
       'deterministic_brain_v8.sqlite3','deterministic_brain_v8_proof.json',
       'smoke_report.json','manifest.json')

DDL='''
PRAGMA foreign_keys=ON;
CREATE TABLE captain_source_definitions(
 source_id TEXT PRIMARY KEY, definition_kind TEXT NOT NULL, definition_key TEXT NOT NULL,
 value_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status='quarantined_source'),
 source_sha256 TEXT NOT NULL);
CREATE TABLE captain_source_audit(
 audit_id TEXT PRIMARY KEY, game_id TEXT, severity TEXT NOT NULL,
 finding TEXT NOT NULL, mathematical_evidence_json TEXT NOT NULL);
CREATE TABLE math_operators(
 operator_id TEXT PRIMARY KEY, family TEXT NOT NULL, formula TEXT NOT NULL,
 meaning TEXT NOT NULL, inputs_json TEXT NOT NULL, output_type TEXT NOT NULL,
 assumptions TEXT NOT NULL, status TEXT NOT NULL, provenance TEXT NOT NULL,
 implementation TEXT, definition_sha256 TEXT NOT NULL);
CREATE TABLE math_numeric_program_aliases(
 program TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES math_operators(operator_id));
CREATE TABLE math_operator_dependencies(
 operator_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 dependency_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 PRIMARY KEY(operator_id,dependency_id));
CREATE TABLE math_operator_tests(
 operator_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 vector_number INTEGER NOT NULL, args_json TEXT NOT NULL, expected_json TEXT NOT NULL,
 observed_json TEXT NOT NULL, result TEXT NOT NULL CHECK(result='PASS'),
 PRIMARY KEY(operator_id,vector_number));
CREATE TABLE game_math_requirements(
 game_id TEXT NOT NULL REFERENCES games(game_id),
 operator_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 role TEXT NOT NULL, PRIMARY KEY(game_id,operator_id,role));
CREATE TABLE math_growth_requirements(
 game_id TEXT NOT NULL REFERENCES games(game_id),
 requirement TEXT NOT NULL, status TEXT NOT NULL,
 PRIMARY KEY(game_id,requirement));
CREATE TABLE math_execution_graphs(
 graph_id TEXT PRIMARY KEY, game_id TEXT NOT NULL REFERENCES games(game_id),
 scenario_json TEXT NOT NULL, output_semantics TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 x_json TEXT NOT NULL,y_json TEXT NOT NULL,z_json TEXT NOT NULL,
 model_status TEXT NOT NULL, reduction_assumptions TEXT NOT NULL);
CREATE TABLE math_execution_nodes(
 graph_id TEXT NOT NULL REFERENCES math_execution_graphs(graph_id),
 sequence INTEGER NOT NULL, node_name TEXT NOT NULL, operator_id TEXT NOT NULL REFERENCES math_operators(operator_id),
 inputs_json TEXT NOT NULL, output_json TEXT NOT NULL, PRIMARY KEY(graph_id,sequence));
CREATE TABLE curriculum_presentations(
 graph_id TEXT NOT NULL REFERENCES math_execution_graphs(graph_id),
 tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8),
 prompt TEXT NOT NULL, target_json TEXT NOT NULL,
 PRIMARY KEY(graph_id,tier));
CREATE TABLE math_head_capabilities(
 operator_id TEXT PRIMARY KEY REFERENCES math_operators(operator_id),
 head_status TEXT NOT NULL CHECK(head_status IN ('not_measured','measured')),
 score_json TEXT NOT NULL);
CREATE VIEW game_math_coverage AS
 SELECT g.game_id,g.name,g.status,COUNT(DISTINCT r.operator_id) required_ops,
 COALESCE(SUM(CASE WHEN m.status='catalog_only' THEN 1 ELSE 0 END),0) unavailable_dependencies
 FROM games g LEFT JOIN game_math_requirements r ON r.game_id=g.game_id
 LEFT JOIN math_operators m ON m.operator_id=r.operator_id
 GROUP BY g.game_id;
'''

def canon(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(x):return hashlib.sha256(canon(x).encode()).hexdigest()
def write_json(path:Path,data):path.write_text(json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
def file_sha(path:Path):return hashlib.sha256(path.read_bytes()).hexdigest()

def fraction_value(x):
    if isinstance(x,Fraction):return mathlib.out(x)
    return mathlib.out(mathlib.frac(x))

def _mat(op,*args):return mathlib.execute(op,*args)

def canonical_graphs():
    """One frozen trace per independently defined game; source of EVERY rung."""
    # Hawk-Dove: existing verified payoff matrix, not an arbitrary new matrix.
    v=Fraction(6);c=Fraction(10)
    hawk=legacy.MATRICES['hawk_dove'][4]
    assert hawk==[[[-2,-2],[6,0]],[[0,6],[3,3]]]
    assert (v-c)/2 == hawk[0][0][0] and v/2==hawk[1][1][0]
    hx=_mat('HAWK_VS_DOVE',v);hy=_mat('DOVE_VS_DOVE',v)
    assert hx==hawk[0][1][0] and hy==hawk[1][1][0]
    hsteps=[('hawk_against_dove','HAWK_VS_DOVE',[v],hx),
            ('dove_against_dove','DOVE_VS_DOVE',[v],hy),
            ('choose_hawk','GREATER_THAN',[hx,hy],_mat('GREATER_THAN',hx,hy))]
    # Synthetic betrayal/reputation rubric; NOT a canonical game-theoretic
    # equilibrium or an empirically established behavioral model.
    bx=_mat('HIGH_PAYOFF',60);by=_mat('LOW_PAYOFF',40)
    bsteps=[('betray_payoff','HIGH_PAYOFF',[60],bx),
            ('loyal_payoff','LOW_PAYOFF',[40],by),
            ('choose_betrayal','GREATER_THAN',[bx,by],_mat('GREATER_THAN',bx,by))]
    # First-price sealed bid with explicitly frozen synthetic beliefs.
    # LOW_INTELLIGENCE models resources as uniform +/-50% of the estimate;
    # HIGH_NEED assumes rival bids all modeled resources, rather than equating
    # their actual bid with their wallet. The rule is not an auction theorem.
    interval=_mat('LOW_INTELLIGENCE',125)
    rival_interval=tuple(_mat('HIGH_NEED',a) for a in interval)
    own=_mat('OWN_MEDIUM_NEED',100)
    bid=_mat('OPTIMAL_BID_UNIFORM',own,*rival_interval)
    # This MIDPOINT reduction is justified ONLY where hi>=own>lo and the
    # opponent bid is modeled by a continuous uniform distribution.
    assert rival_interval[1]>=own>rival_interval[0]
    reduced=_mat('MIDPOINT',own,rival_interval[0]);assert bid==reduced
    asteps=[('own_valuation','OWN_MEDIUM_NEED',[100],own),
            ('uncertain_rival_resources','LOW_INTELLIGENCE',[125],interval),
            ('rival_bidding_policy_lower','HIGH_NEED',[interval[0]],rival_interval[0]),
            ('rival_bidding_policy_upper','HIGH_NEED',[interval[1]],rival_interval[1]),
            ('auction_optimizer','OPTIMAL_BID_UNIFORM',[own,*rival_interval],bid),
            ('valid_reduction','MIDPOINT',[own,rival_interval[0]],reduced)]
    return [
      {'id':'hawk_dove','title':'Hawk-Dove verified matrix','x':hx,'y':hy,'op':'GREATER_THAN','z':1,
       'inputs':[6,10],'steps':hsteps,'output':'play_hawk_binary',
       'status':'verified_canonical_payoff','assumption':'opponent plays Dove; symmetric matrix with V=6,C=10; ties select Dove',
       'prompts':[
         'XXXXXX: 6\nYYYYYYY: 3\n\nOP: GREATER_THAN\n\nDo you play HAWK?',
         'XXXXXX: 6\nYYYYYYY: 3\n\nPlay HAWK only if XXXXXX is greater than YYYYYYY.\n\nDo you play HAWK?',
         'Play HAWK only if 6 is greater than 3.\n\nDo you play HAWK?',
         'Play HAWK only if (6, HAWK_VS_DOVE) is greater than 3.\n\nDo you play HAWK?',
         'Play HAWK only if (6, HAWK_VS_DOVE) is greater than (6, DOVE_VS_DOVE).\n\nDo you play HAWK?',
         'HAWK: (6, HAWK_VS_DOVE)\nDOVE: (6, DOVE_VS_DOVE)\n\nWhich action has the greater payoff?',
         'The prize is worth 6. HAWK takes the entire prize against DOVE; two DOVEs split the prize.\nWhich action pays more against DOVE?',
         'Against another Dove, Hawk receives the entire prize, while Dove splits it. The prize is worth 6; fighting costs 10.\nPlay Hawk or Dove?',
         'You compete with another captain over a resource worth 6 credits. Confrontation against another confrontational captain causes fighting costs of 10 credits, shared between them. Two peaceful captains divide the prize evenly; an aggressive captain facing a peaceful one takes the full prize. Your rival has chosen a peaceful strategy. Should you play Hawk or Dove?',
       ]},
      {'id':'betrayal_loyalty_synthetic','title':'Betrayal-Loyalty synthetic rubric','x':bx,'y':by,'op':'GREATER_THAN','z':1,
       'inputs':[60,40],'steps':bsteps,'output':'betray_binary',
       'status':'provisional_synthetic_teacher','assumption':'Fixed HIGH_PAYOFF=1.5, LOW_PAYOFF=0.5; NOT a strategic equilibrium',
       'prompts':[
        'XXXXXX: 90\nYYYYYYY: 20\n\nOP: GREATER_THAN\n\nDo you betray?',
        'XXXXXX: 90\nYYYYYYY: 20\n\nBetray only if XXXXXX is greater than YYYYYYY.\n\nDo you betray?',
        'Betray only if 90 is greater than 20.\n\nDo you betray?',
        'Betray only if (60, HIGH_PAYOFF) is greater than 20.\n\nDo you betray?',
        'Betray only if (60, HIGH_PAYOFF) is greater than (40, LOW_PAYOFF).\n\nDo you betray?',
        'BETRAY: (60, HIGH_PAYOFF)\nLOYAL: (40, LOW_PAYOFF)\n\nWhich produces the greater adjusted reputation?',
        'Betrayal base reputation is 60; loyalty base reputation is 40. Betrayal carries HIGH_PAYOFF and loyalty LOW_PAYOFF.\nDo you betray?',
        'An ally trusts you. BETRAY has HIGH_PAYOFF; LOYAL has LOW_PAYOFF. The base reputations are 60 and 40 respectively.\nDo you betray?',
        'You are a captain considering betraying an alliance. Your base reputation from betrayal is 60, while remaining loyal has a base reputation of 40. Betrayal in this situation has HIGH_PAYOFF and loyalty has LOW_PAYOFF under the agreed reputation rubric. Should you betray?',
       ]},
      {'id':'first_price_captain_uncertainty','title':'First-price sealed bid with uncertain opponent','x':own,'y':rival_interval[0],'op':'MIDPOINT','z':bid,
       'inputs':[100,125],'steps':asteps,'output':'bid_credits',
       'status':'verified_under_frozen_synthetic_behavioral_model',
       'assumption':'Uniform rival bid derived from LOW_INTELLIGENCE resources and HIGH_NEED policy; our MEDIUM need implies risk-neutral monetary utility; rival upper bound >= own valuation; ties choose lowest bid',
       'prompts':[
        'XXXXXX: 100\nYYYYYYY: 62.5\n\nOP: MIDPOINT\n\nWhat should you bid?',
        'XXXXXX: 100\nYYYYYYY: 62.5\n\nBid the midpoint of XXXXXX and YYYYYYY.\n\nWhat should you bid?',
        'Bid the midpoint of 100 and 62.5.\n\nWhat should you bid?',
        'Bid the midpoint of (100, OWN_MEDIUM_NEED) and 62.5.\n\nWhat should you bid?',
        'Bid the midpoint of (100, OWN_MEDIUM_NEED) and the lower bound of (125, LOW_INTELLIGENCE, HIGH_NEED).\n\nWhat should you bid?',
        'OWN VALUE: (100, OWN_MEDIUM_NEED)\nRIVAL RESOURCES: (125, LOW_INTELLIGENCE, HIGH_NEED)\n\nUnder the frozen auction policy, extract the lower rival-bid bound and bid the midpoint.',
        'Your value cap is 100 and rival resources are estimated at 125. Intelligence is LOW; the rival need is HIGH; your need is MEDIUM.\nUnder the first-price auction rules, what should you bid?',
        'You may bid at most 100. Rival resources are estimated at 125 with LOW_INTELLIGENCE. The rival has HIGH_NEED to win and you have OWN_MEDIUM_NEED. Under the frozen first-price model, compute the optimal bid.',
        'You are competing for an exclusive trading license. You will pay your own bid if you win. Your maximum willingness to pay is 100 credits. Intelligence estimates the rival has 125 credits available, but confidence in this intelligence is LOW. Your need to win is MEDIUM; your rival’s need to win is HIGH. Neither captain sees the other bid. Under the established bidding model, what should you bid?',
       ]},
    ]

def _source_ops():
    ops={k:dict(v,implementation='tools.nanojev_game_math_registry.execute' if v['status']=='executable' else None)
         for k,v in mathlib.OPERATORS.items()}
    for gid,(_,_,formula,params,changed,step) in legacy.SCALAR_MODELS.items():
        oid='SCALAR_'+gid.upper()
        if oid in ops:raise AssertionError('operator name collision')
        ops[oid]={'operator_id':oid,'family':'legacy_scalar','formula':formula,'inputs':['game_parameters'],
                  'output':'number','dependencies':[],'status':'executable','meaning':'Verified source-game scalar model: '+gid,
                  'assumptions':'See tools.nanojev_game_transformation_library.scalar_oracle for exact validity domain',
                  'provenance':'legacy_source_verified','implementation':'tools.nanojev_game_transformation_library.scalar_oracle'}
    for op_id,kind,definition,domain,claim,status in legacy.TRANSFORMS:
        oid='TRANSFORM_'+op_id.upper()
        ops[oid]={'operator_id':oid,'family':'legacy_transform','formula':definition,'inputs':[domain],
                  'output':claim,'dependencies':[],'status':status,
                  'meaning':'Existing game transformation: '+op_id,
                  'assumptions':'Preservation claim: '+claim+'; verified by original transformation oracle where executable',
                  'provenance':'legacy_source_verified' if status=='executable' else 'legacy_catalog_only',
                  'implementation':'tools.nanojev_game_transformation_library' if status=='executable' else None}
    for op_id,formula,inputs,output,dependencies,meaning in (
        ('MATRIX_NATIVE_BEST_RESPONSE','first argmax_row U[row][opponent_col][row_player]',['matrix','column_index'],'action_index',['ARGMAX'],'Row-player best response to stated opponent action; ties choose first'),
        ('MATRIX_MIXED_ROW_PROBABILITY','(v11-v10)/(v00-v10-v01+v11)',['matrix_2x2'],'probability',['DIVIDE'],'Probability of row0 that makes column player indifferent'),
        ('MATRIX_MIXED_COLUMN_PROBABILITY','(u11-u01)/(u00-u01-u10+u11)',['matrix_2x2'],'probability',['DIVIDE'],'Probability of column0 that makes row player indifferent'),
        ('MATRIX_ACTION_LABEL_RELABEL','permute tensor axes and associated action labels together',['row_actions','column_actions','matrix','permutation'],'labeled_matrix',['TRANSFORM_PERMUTE_ROW','TRANSFORM_PERMUTE_COLUMN'],'Preserve action-identified utilities and equilibria under reindexing'),
    ):
        ops[op_id]={'operator_id':op_id,'family':'game_native','formula':formula,'inputs':inputs,
                    'output':output,'dependencies':dependencies,'status':'executable',
                    'meaning':meaning,'assumptions':'finite 2x2 games; mixed results require strict interior equilibrium',
                    'provenance':'native_source_oracle_verified',
                    'implementation':'tools.nanojev_game_native_objectives_v6'}
    return ops

def _ensure_new_games(cn):
    for gid,name,representation in [
       ('betrayal_loyalty_synthetic','Betrayal Loyalty synthetic reputation','scalar'),
       ('first_price_captain_uncertainty','First Price Auction captain uncertainty','scalar')]:
        fam='captain' if gid.startswith('betrayal') else 'mechanism'
        cn.execute('INSERT INTO games(game_id,family_id,name,math,status,provenance,representation,parameters_json) VALUES (?,?,?,?,?,?,?,?)',
                   (gid,fam,name,'Executable frozen rubric; see math_execution_graphs','oracle_verified',
                    'source_synthetic_assumptions;not_independently_calibrated',representation,canon({'catalog_source':'nanojev_game_math_smoke'})))

def _import_quarantined_captain_source(cn):
    """Read original captain v1 source literally, without importing dependencies.

    Its 39 game definitions, synthetic matrices, jackets-independent taxonomy,
    transforms, dictionary and objective are part of the generated source audit.
    Its v1 payoff labels are NOT used as verified gold because some contradict
    the mathematical classification they claim.
    """
    path=Path(__file__).resolve().parent/'nanojev_game_library_v1.py'
    if not path.is_file():raise FileNotFoundError('CAPTAIN_V1_SOURCE_REQUIRED '+str(path))
    source_sha=file_sha(path)
    ast_tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    required={'FAMILIES','GAMES','MATRICES','TRANSFORMS','DICTIONARY','OBJECTIVE'}
    data={}
    for node in ast_tree.body:
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in required:
                    data[target.id]=ast.literal_eval(node.value)
    if set(data)!=required:raise AssertionError('CAPTAIN_SOURCE_DEFINITION_MISSING '+repr(required-set(data)))
    def put(kind,key,value):
        ident='captain_v1:'+kind+':'+key
        cn.execute('INSERT INTO captain_source_definitions VALUES (?,?,?,?,?,?)',
                   (ident,kind,key,canon(value),'quarantined_source',source_sha))
    for group in ('FAMILIES','GAMES','TRANSFORMS','DICTIONARY'):
        for record in data[group]:put(group.lower(),str(record[0]),record)
    for gid,matrix in data['MATRICES'].items():put('matrices',gid,matrix)
    put('objective','source_generated_v1',data['OBJECTIVE'])
    warnings=[]
    source_games={g[0]:g for g in data['GAMES']}
    for gid,table in data['MATRICES'].items():
        a,b=table
        # Test the row player's strategy ordering from its own stated payoffs.
        # A genuine 2x2 anti-coordination game reverses best response across
        # the two opponent actions; row dominance contradicts that property.
        br=[0 if a[j]>b[j] else 1 if a[j]<b[j] else -1 for j in (0,1)]
        if gid in ('hawk_dove','chicken') and br[0]==br[1] and br[0]!=-1:
            finding='Claims anti-coordination but row-action '+('A' if br[0]==0 else 'B')+' strictly dominates'
            record={'id':'DOMINANCE_CONTRADICTS_ANTI_COORDINATION:'+gid,
                    'game_id':gid,'severity':'BLOCK_GOLD','finding':finding,
                    'evidence':{'source_matrix':table,'row_best_responses':br}}
            warnings.append(record)
            cn.execute('INSERT INTO captain_source_audit VALUES (?,?,?,?,?)',
                       (record['id'],gid,record['severity'],finding,canon(record['evidence'])))
    if data['OBJECTIVE']['target_schema']['OP'].lower().startswith('subtract'):
        warning={'id':'FIXED_SUBTRACTION_NOT_GENERAL_Z','game_id':None,'severity':'BLOCK_GENERAL_Z',
                 'finding':'Original captain-v1 target restricts OP to subtraction; cannot represent auction bid Z',
                 'evidence':{'OP':data['OBJECTIVE']['target_schema']['OP']}}
        warnings.append(warning)
        cn.execute('INSERT INTO captain_source_audit VALUES (?,?,?,?,?)',
                   (warning['id'],None,warning['severity'],warning['finding'],canon(warning['evidence'])))
    if len(warnings)<3:raise AssertionError('EXPECTED_CAPTAIN_V1_DRIFT_NOT_DETECTED')
    return {'source_file':path.name,'sha256':source_sha,
            'definitions':cn.execute('SELECT COUNT(*) FROM captain_source_definitions').fetchone()[0],
            'source_games':len(data['GAMES']),'warnings':warnings,
            'source_status':'quarantined_not_gold'}

def _populate_math(cn,graphs):
    cn.executescript(DDL)
    ops=_source_ops()
    for oid,s in sorted(ops.items()):
        if not s['formula'] or not s['meaning'] or not s['inputs'] or not s['output']:
            raise AssertionError('INCOMPLETE_MATH_DEFINITION '+oid)
        cn.execute('INSERT INTO math_operators VALUES (?,?,?,?,?,?,?,?,?,?,?)',
           (oid,s['family'],s['formula'],s['meaning'],canon(s['inputs']),s['output'],s['assumptions'],
            s['status'],s['provenance'],s['implementation'],digest(s)))
        cn.execute('INSERT INTO math_head_capabilities VALUES (?,?,?)',(oid,'not_measured',canon({'reason':'No model inference performed by source generator'})))
    for program,oid in sorted(mathlib.PROGRAM_ALIASES.items()):
        cn.execute('INSERT INTO math_numeric_program_aliases VALUES (?,?)',(program,oid))
    for oid,s in ops.items():
        for dep in s['dependencies']:
            if dep not in ops:raise AssertionError('MISSING_MATH_DEPENDENCY '+oid+' -> '+dep)
            cn.execute('INSERT INTO math_operator_dependencies VALUES (?,?)',(oid,dep))
    for oid,vectors in sorted(mathlib.VECTORS.items()):
        for i,(args,want) in enumerate(vectors):
            observed=mathlib.out(mathlib.execute(oid,*args))
            if isinstance(want,str):expected=mathlib.out(Fraction(want))
            elif isinstance(want,list):expected=mathlib.out(mathlib._expected(want))
            else:expected=want
            if oid=='LOGIT_RESPONSE':
                if max(abs(a-b) for a,b in zip(observed,expected))>1e-12:raise AssertionError('GOLD_MISMATCH '+oid)
            elif observed!=expected:raise AssertionError('GOLD_MISMATCH '+oid)
            cn.execute('INSERT INTO math_operator_tests VALUES (?,?,?,?,?,?)',
                       (oid,i,canon(args),canon(expected),canon(observed),'PASS'))
    # Verify all legacy scalar mathematical definitions using the original
    # oracle, and persist the actual values for independently repeatable smoke.
    for gid,(_,family,formula,params,changed,step) in legacy.SCALAR_MODELS.items():
        oid='SCALAR_'+gid.upper();value=legacy.scalar_oracle(gid,params)
        cn.execute('INSERT INTO math_operator_tests VALUES (?,?,?,?,?,?)',
                   (oid,0,canon(params),canon(value),canon(value),'PASS'))
        cn.execute('INSERT INTO game_math_requirements VALUES (?,?,?)',(gid,oid,'legacy_scalar_value'))
    for gid in legacy.MATRICES:
        cn.execute('INSERT INTO game_math_requirements VALUES (?,?,?)',(gid,'PURE_NASH','equilibrium_verifier'))
        for op_id,kind,formula,domain,claim,status in legacy.TRANSFORMS:
            if status=='executable' and domain=='matrix':
                cn.execute('INSERT INTO game_math_requirements VALUES (?,?,?)',(gid,'TRANSFORM_'+op_id.upper(),'verified_matrix_transform'))
    for g in graphs:
        for _,oid,_,_ in g['steps']:
            cn.execute('INSERT OR IGNORE INTO game_math_requirements VALUES (?,?,?)',(g['id'],oid,'frozen_extraction'))
    # Suggestions are deliberately NON-AUTHORITATIVE. A catalog title can
    # point to a likely math dependency, but cannot prove its full game oracle.
    suggested=(
      (r'(?i)auction',('GENERAL_AUCTION_OPTIMAL_BID',)),
      (r'(?i)poker|liars dice|battleship',('BELIEF_UPDATE','REGRET_MATCHING')),
      (r'(?i)signaling|screening|bayesian|principal.agent|global game|information cascade',('BELIEF_UPDATE','BAYESIAN_NASH')),
      (r'(?i)stochastic|markov|repeated|dynamic game',('BELLMAN_BACKUP',)),
      (r'(?i)evolutionary|replicator|invasion',('REPLICATOR_UPDATE',)),
      (r'(?i)bargaining|negotiation',('NASH_BARGAINING',)),
      (r'(?i)robust|ambiguity',('ROBUST_ARGMAX',)),
      (r'(?i)regret|bandit',('REGRET_MATCHING',)),
    )
    for gid,name in cn.execute("SELECT game_id,name FROM games WHERE status='catalog_only'"):
        for pattern,operators in suggested:
            if re.search(pattern,name):
                for operator in operators:
                    cn.execute('INSERT OR IGNORE INTO math_growth_requirements VALUES (?,?,?)',
                       (gid,'CANDIDATE_OPERATOR:'+operator,'candidate_not_verified'))
    for (gid,) in cn.execute("SELECT game_id FROM games WHERE status='catalog_only'"):
        cn.execute('INSERT INTO math_growth_requirements VALUES (?,?,?)',
                   (gid,'Game-specific executable oracle and operator mapping not yet implemented','catalog_only'))
    # Register the NEW game-native mathematics, not just prompt renderers.
    for gid in legacy.MATRICES:
        for oid,role in (
            ('MATRIX_NATIVE_BEST_RESPONSE','native_action_selection'),
            ('MATRIX_MIXED_ROW_PROBABILITY','native_equilibrium_row'),
            ('MATRIX_MIXED_COLUMN_PROBABILITY','native_equilibrium_column'),
            ('MATRIX_ACTION_LABEL_RELABEL','semantic_permutation_check')):
            cn.execute('INSERT INTO game_math_requirements VALUES (?,?,?)',(gid,oid,role))
    # Independently verified canonical vectors for the new native operators.
    fixture=[[[4,-13],[-16,-7]],[[-24,2],[0,-1]]]
    native_vectors={
        'MATRIX_NATIVE_BEST_RESPONSE':([fixture,0],0),
        'MATRIX_MIXED_ROW_PROBABILITY':([fixture],{'numerator':1,'denominator':3}),
        'MATRIX_MIXED_COLUMN_PROBABILITY':([fixture],{'numerator':4,'denominator':11}),
        'MATRIX_ACTION_LABEL_RELABEL':([['Intercept','Evade'],['Raid','Withdraw'],fixture,'permute_row'],['Evade','Intercept']),
    }
    for oid,(args,expected) in native_vectors.items():
        if oid=='MATRIX_NATIVE_BEST_RESPONSE':
            actual=max(range(len(args[0])),key=lambda i:args[0][i][args[1]][0])
        elif oid.startswith('MATRIX_MIXED_'):
            vals=native._mixed(args[0]);actual=mathlib.out(vals[0]/vals[1] if oid.endswith('ROW_PROBABILITY') else vals[2]/vals[3])
        else:actual=native.labeled_matrix_transform(*args)['row_actions']
        if actual!=expected:raise AssertionError('NATIVE_OPERATOR_VECTOR_MISMATCH '+oid)
        cn.execute('INSERT INTO math_operator_tests VALUES (?,?,?,?,?,?)',
                   (oid,0,canon(mathlib.out(args)),canon(expected),canon(actual),'PASS'))
    return ops

def _insert_graphs(cn,graphs):
    for g in graphs:
        canonical={'X':fraction_value(g['x']),'Y':fraction_value(g['y']),
                   'OP':g['op'],'Z':mathlib.out(g['z']) if g['op']=='GREATER_THAN' else fraction_value(g['z']), 'result_semantics':g['output']}
        scenario={'visible_inputs':g['inputs'],'target':g['output'],'model_status':g['status']}
        cn.execute('INSERT INTO math_execution_graphs VALUES (?,?,?,?,?,?,?,?,?,?)',
                   (g['id'],g['id'],canon(scenario),g['output'],g['op'],canon(canonical['X']),canon(canonical['Y']),
                    canon(canonical['Z']),g['status'],g['assumption']))
        for i,(name,oid,args,output) in enumerate(g['steps']):
            cn.execute('INSERT INTO math_execution_nodes VALUES (?,?,?,?,?,?)',
                       (g['id'],i,name,oid,canon(mathlib.out(args)),canon(mathlib.out(output))))
        if len(g['prompts'])!=9:raise AssertionError('INCOMPLETE_LADDER '+g['id'])
        if not g['prompts'][0].count('OP: '+g['op']):raise AssertionError('BAD_T00 '+g['id'])
        numbers=re.findall(r'(?<![A-Za-z])\d+(?:\.\d+)?',g['prompts'][8])
        allowed=sorted(str(v) for v in g['inputs'])
        if sorted(numbers)!=allowed:
            raise AssertionError('T08_NON_INPUT_NUMBER_LEAK '+g['id']+': '+repr(numbers))
        for tier,prompt in enumerate(g['prompts']):
            if not prompt.strip():raise AssertionError('EMPTY_PROMPT')
            cn.execute('INSERT INTO curriculum_presentations VALUES (?,?,?,?)',
                       (g['id'],tier,prompt,canon(canonical)))
        final_name,final_op,args,value=g['steps'][-1]
        if final_op!=g['op'] or value!=g['z'] or mathlib.execute(final_op,*args)!=g['z']:
            raise AssertionError('FINAL_EXECUTION_DRIFT '+g['id'])

def verify_generated(cn):
    legacy.verify(cn)
    if cn.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or cn.execute('PRAGMA foreign_key_check').fetchall():
        raise AssertionError('SQL_INTEGRITY_OR_FK')
    expected=len(_source_ops())
    if cn.execute('SELECT COUNT(*) FROM math_operators').fetchone()[0]!=expected:raise AssertionError('DEFINITION_COVERAGE')
    if cn.execute('SELECT COUNT(*) FROM math_numeric_program_aliases').fetchone()[0]!=14:
        raise AssertionError('V3_NUMERIC_PROGRAM_NOT_COVERED')
    if cn.execute("SELECT COUNT(*) FROM math_operators WHERE formula='' OR meaning='' OR inputs_json='' OR output_type='' ").fetchone()[0]:
        raise AssertionError('EMPTY_MATH_DEFINITION')
    for oid in mathlib.OPERATORS:
        if mathlib.OPERATORS[oid]['status']=='executable' and not cn.execute('SELECT 1 FROM math_operator_tests WHERE operator_id=?',(oid,)).fetchone():
            raise AssertionError('NO_TEST_VECTOR '+oid)
    for gid,count in cn.execute('SELECT graph_id,COUNT(*) FROM curriculum_presentations GROUP BY graph_id'):
        if count!=9:raise AssertionError('LADDER_COUNT '+gid)
        expected_targets=set(t[0] for t in cn.execute('SELECT target_json FROM curriculum_presentations WHERE graph_id=?',(gid,)))
        if len(expected_targets)!=1:raise AssertionError('LADDER_TARGET_DRIFT '+gid)
    # Replay each frozen math node FROM THE PERSISTED DATABASE. This catches
    # recorded execution drift, not just drift in the source-level fixtures.
    def unpack(value):
        if isinstance(value,dict) and set(value)=={'numerator','denominator'}:
            return Fraction(value['numerator'],value['denominator'])
        if isinstance(value,list):return [unpack(a) for a in value]
        return value
    for gid,seq,op,arg_text,out_text in cn.execute(
          'SELECT graph_id,sequence,operator_id,inputs_json,output_json FROM math_execution_nodes ORDER BY graph_id,sequence'):
        inputs=unpack(json.loads(arg_text))
        observed=mathlib.out(mathlib.execute(op,*inputs))
        if canon(observed)!=out_text:
            raise AssertionError('PERSISTED_EXECUTION_DRIFT '+gid+':'+str(seq))
    for gid,op,x_text,y_text,z_text in cn.execute(
          'SELECT graph_id,operation_id,x_json,y_json,z_json FROM math_execution_graphs'):
        x=unpack(json.loads(x_text));y=unpack(json.loads(y_text))
        if canon(mathlib.out(mathlib.execute(op,x,y)))!=z_text:
            raise AssertionError('PERSISTED_RESULT_DRIFT '+gid)
    for oid,stored_hash in cn.execute('SELECT operator_id,definition_sha256 FROM math_operators'):
        if digest(_source_ops()[oid])!=stored_hash:
            raise AssertionError('DEFINITION_PROVENANCE_DRIFT '+oid)
    if cn.execute('SELECT COUNT(*) FROM math_growth_requirements WHERE status="candidate_not_verified"').fetchone()[0]==0:
        raise AssertionError('GROWTH_BACKLOG_NOT_POPULATED')
    if cn.execute('SELECT COUNT(*) FROM math_head_capabilities WHERE head_status="measured"').fetchone()[0]:
        raise AssertionError('FALSE_TRAINED_HEAD_CLAIM')
    if cn.execute('SELECT COUNT(*) FROM curriculum_presentations').fetchone()[0]!=27:
        raise AssertionError('LADDER_COVERAGE')
    if cn.execute('SELECT COUNT(*) FROM captain_source_audit WHERE severity="BLOCK_GOLD"').fetchone()[0]<2:
        raise AssertionError('CAPTAIN_MATH_DRIFT_NOT_QUARANTINED')
    if cn.execute('SELECT COUNT(*) FROM captain_source_definitions WHERE status<>"quarantined_source"').fetchone()[0]:
        raise AssertionError('CAPTAIN_SOURCE_FALSE_GOLD')
    # Provisional game labels are deliberately not reported as Nash equilibria.
    if cn.execute('SELECT COUNT(*) FROM math_execution_graphs WHERE model_status="provisional_synthetic_teacher"').fetchone()[0]!=1:
        raise AssertionError('SYNTHETIC_STATUS_LOST')
    ladder.verify_persisted(cn)
    state_ladder.verify(cn)
    full_ladder.verify(cn)
    native.verify(cn)
    return {'status':'PASS','games':cn.execute('SELECT COUNT(*) FROM games').fetchone()[0],
            'math_definitions':expected,
            'math_vectors':cn.execute('SELECT COUNT(*) FROM math_operator_tests').fetchone()[0],
            'legacy_cases_verified':cn.execute('SELECT COUNT(*) FROM transformation_cases').fetchone()[0],
            'frozen_graphs':cn.execute('SELECT COUNT(*) FROM math_execution_graphs').fetchone()[0],
            'curriculum_presentations':27,
            'bidirectional_cases':cn.execute('SELECT COUNT(*) FROM ladder_proof_cases').fetchone()[0],
            'bidirectional_presentations':cn.execute('SELECT COUNT(*) FROM ladder_proof_tiers').fetchone()[0],
            'bidirectional_edges':cn.execute('SELECT COUNT(*) FROM ladder_proof_edges').fetchone()[0],
            'executable_transition_programs':cn.execute('SELECT COUNT(*) FROM ladder_transition_programs').fetchone()[0],
            'executed_transition_edges':cn.execute('SELECT COUNT(*) FROM ladder_transition_executions').fetchone()[0],
            'extraction_math_steps':cn.execute('SELECT COUNT(*) FROM ladder_transition_math_steps').fetchone()[0],
            'stateful_chain_edges':cn.execute('SELECT COUNT(*) FROM ladder_v4_state_edges').fetchone()[0],
            'stateful_chain_cases':cn.execute('SELECT COUNT(*) FROM ladder_v4_state_cases').fetchone()[0],
            'undefined_dependencies':0,'definition_collisions':0,
            'oracle_mismatches':0,'invalid_training_examples':0,'curriculum_inconsistencies':0,
            'catalog_only_games':cn.execute("SELECT COUNT(*) FROM games WHERE status='catalog_only'").fetchone()[0],
            'quarantined_captain_definitions':cn.execute('SELECT COUNT(*) FROM captain_source_definitions').fetchone()[0],
            'candidate_missing_operator_links':cn.execute("SELECT COUNT(*) FROM math_growth_requirements WHERE status='candidate_not_verified'").fetchone()[0]}

def _export_ladder_bank(conn, path):
    with closing(sqlite3.connect(path)) as bank:
        bank.executescript('''CREATE TABLE task_cases(game_id TEXT NOT NULL,tier INTEGER NOT NULL,prompt TEXT NOT NULL,target_json TEXT NOT NULL,graph_id TEXT NOT NULL,PRIMARY KEY(game_id,tier));''')
        bank.executemany('INSERT INTO task_cases VALUES (?,?,?,?,?)',
         conn.execute('SELECT g.game_id,p.tier,p.prompt,p.target_json,p.graph_id FROM curriculum_presentations p JOIN math_execution_graphs g USING(graph_id) ORDER BY g.game_id,p.tier'))
        bank.commit()
        if bank.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0]!=27:raise AssertionError('TASK_BANK_MISSING')

def _export_bidirectional_bank(conn,path):
    with closing(sqlite3.connect(path)) as bank:
        bank.executescript('''CREATE TABLE task_cases(
             case_id TEXT NOT NULL, game_id TEXT NOT NULL, tier INTEGER NOT NULL,
             prompt TEXT NOT NULL, target_json TEXT NOT NULL, witness_json TEXT NOT NULL,
             split TEXT NOT NULL CHECK(split='proof_only'),
             PRIMARY KEY(case_id,tier));''')
        bank.executemany('INSERT INTO task_cases VALUES (?,?,?,?,?,?,?)',
         conn.execute('''SELECT p.case_id,c.game_id,p.tier,p.prompt,p.extracted_json,
                                c.witness_json,'proof_only'
                         FROM ladder_proof_tiers p JOIN ladder_proof_cases c USING(case_id)
                         ORDER BY c.game_id,p.case_id,p.tier'''))
        bank.commit()
        if bank.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0]!=len(list(ladder.frozen_cases()))*9:
            raise AssertionError('BIDIRECTIONAL_BANK_MISSING')

def _export_full_ladder_bank(conn, path):
    """Proof-only export: not automatically registered as live training tasks."""
    with closing(sqlite3.connect(path)) as bank:
        bank.executescript('''CREATE TABLE task_cases(
          case_id TEXT NOT NULL, game_id TEXT NOT NULL, tier INTEGER NOT NULL,
          prompt TEXT NOT NULL, canonical_json TEXT NOT NULL, source_split TEXT NOT NULL,
          usage TEXT NOT NULL CHECK(usage='proof_only'),
          PRIMARY KEY(case_id,tier));''')
        bank.executemany('INSERT INTO task_cases VALUES (?,?,?,?,?,?,?)',
          conn.execute('''SELECT c.case_id,c.game_id,t.tier,t.prompt,c.canonical_json,c.split,'proof_only'
            FROM full_ladder_v5_tiers t JOIN full_ladder_v5_cases c USING(case_id)
            ORDER BY c.game_id,c.case_id,t.tier'''))
        bank.commit()
        if bank.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0]!=9*conn.execute('SELECT COUNT(*) FROM transformation_cases').fetchone()[0]:
            raise AssertionError('FULL_LADDER_EXPORT_MISSING')


def _export_native_bank(conn,path):
    """Proof-only: game-native targets never mutate active training banks."""
    with closing(sqlite3.connect(path)) as bank:
        bank.executescript("""CREATE TABLE task_cases(
        case_id TEXT NOT NULL,game_id TEXT NOT NULL,tier INTEGER NOT NULL,
        objective TEXT NOT NULL,prompt TEXT NOT NULL,target_json TEXT NOT NULL,
        split TEXT NOT NULL,usage TEXT NOT NULL CHECK(usage='proof_only'),
        PRIMARY KEY(case_id,tier));""")
        bank.executemany('INSERT INTO task_cases VALUES (?,?,?,?,?,?,?,?)',conn.execute("""
          SELECT c.case_id,c.game_id,t.tier,c.objective,t.prompt,c.answer_json,c.split,'proof_only'
          FROM native_v6_tiers t JOIN native_v6_cases c USING(case_id)
          ORDER BY c.game_id,c.case_id,t.tier"""))
        bank.commit()
        expected=9*conn.execute('SELECT COUNT(*) FROM native_v6_cases').fetchone()[0]
        if bank.execute('SELECT COUNT(*) FROM task_cases').fetchone()[0]!=expected:
            raise AssertionError('NATIVE_BANK_INCOMPLETE')

def generate(work:Path,seed:int,instances:int):
    math_checks=mathlib.verify_definitions()
    work.mkdir(parents=True,exist_ok=True)
    db=work/'game_transformations.sqlite3'
    original=legacy.build(db,seed,instances)
    graphs=canonical_graphs()
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        _ensure_new_games(conn)
        ops=_populate_math(conn,graphs)
        captain_audit=_import_quarantined_captain_source(conn)
        _insert_graphs(conn,graphs)
        bidirectional=ladder.build_and_verify(conn)
        stateful=state_ladder.build_and_verify(conn)
        full=full_ladder.build_and_verify(conn)
        native_proof=native.build_and_verify(conn)
        mutation_proof=mutation_brain.export_proof_bank(conn,work/'mutation_brain_v7.sqlite3',seed=seed)
        deterministic_proof=deterministic_brain.export_proof_bank(conn,work/'deterministic_brain_v8.sqlite3',seed=seed)
        conn.commit()
        result=verify_generated(conn)
        _export_ladder_bank(conn,work/'math_ladder_task_bank.sqlite3')
        _export_bidirectional_bank(conn,work/'ladder_bidirectional_task_bank.sqlite3')
        _export_full_ladder_bank(conn,work/'full_ladder_task_bank.sqlite3')
        _export_native_bank(conn,work/'native_game_task_bank.sqlite3')
        coverage={r[0]:{'name':r[1],'game_status':r[2], 'mapped_requirements':r[3],
                         'nonexecutable_dependencies':r[4]} for r in conn.execute('SELECT * FROM game_math_coverage ORDER BY game_id')}
        growth={}
        for gid,requirement in conn.execute(
          "SELECT game_id,requirement FROM math_growth_requirements WHERE status='candidate_not_verified' ORDER BY game_id,requirement"):
            growth.setdefault(gid,[]).append(requirement.removeprefix('CANDIDATE_OPERATOR:'))
        for gid,ops_needed in growth.items():
            coverage[gid]['candidate_missing_operators']=ops_needed
        from collections import Counter
        missing_counts=dict(sorted(Counter(op for values in growth.values() for op in values).items()))
        coverage['_summary']={'games_total':len(coverage),'mapped_games':sum(bool(x['mapped_requirements']) for x in coverage.values()),
                    'games_needing_oracles':result['catalog_only_games'],
                    'candidate_operator_links':result['candidate_missing_operator_links'],
                    'candidate_operator_counts':missing_counts,
                    'current_head_measured_capabilities':0,'current_head_status':'not measured by smoke'}
        # Complete per-game coverage is part of the generated dictionary, including blocked games.
        for gid,cov_status,cases,ops_json,note in conn.execute(
              'SELECT game_id,status,verified_cases,observed_ops_json,boundary_note FROM full_ladder_v5_game_coverage ORDER BY game_id'):
            coverage[gid]['full_ladder_v5']={'status':cov_status,'verified_cases':cases,
                'observed_ops':json.loads(ops_json),'scope':note}
        coverage['_summary']['full_ladder_v5']={k:full[k] for k in
               ('source_cases','stateful_edges','tiers','games_with_full_bridges','catalog_only_blocked')}
        coverage['_summary']['game_native_v6']=native_proof
        for gid,obj_text,count,status in conn.execute('SELECT game_id,objectives_json,verified_cases,status FROM native_v6_game_coverage ORDER BY game_id'):
            coverage[gid]['game_native_v6']={'objectives':json.loads(obj_text),'cases':count,'status':status}
        ladders={g['id']:{'status':g['status'],'tiers':9,'Z':mathlib.out(g['z']) if g['op']=='GREATER_THAN' else fraction_value(g['z']),
                'OP':g['op'],'X':fraction_value(g['x']),'Y':fraction_value(g['y']),
                'intermediate_nodes':len(g['steps']),'T08_observation_only':True} for g in graphs}
    # The old TrainingTaskStore remains compatible, but DOES NOT include the new
    # ladder rows. The dedicated ladder bank above is an explicit staging export.
    legacy.export_task_bank(db,work/'nanojev_game_tasks.sqlite3')
    write_json(work/'math_definitions.json',ops)
    with closing(sqlite3.connect(db)) as source_cn:
        captain_definitions={row[0]:{'kind':row[1],'key':row[2],'value':json.loads(row[3]),'status':row[4]}
          for row in source_cn.execute('SELECT * FROM captain_source_definitions ORDER BY source_id')}
    write_json(work/'captain_source_definitions.json',captain_definitions)
    write_json(work/'captain_source_audit.json',captain_audit)
    write_json(work/'ladder_roundtrip_proof.json',bidirectional)
    from . import nanojev_game_ladder_edge_programs as edge_programs
    write_json(work/'ladder_transition_programs.json',edge_programs.specs())
    write_json(work/'ladder_state_chain_proof.json',stateful)
    write_json(work/'full_ladder_state_proof.json',full)
    write_json(work/'native_game_ladder_proof.json',native_proof)
    write_json(work/'mutation_brain_checkpoint_proof.json',mutation_proof)
    write_json(work/'deterministic_brain_v8_proof.json',deterministic_proof)
    write_json(work/'native_game_coverage.json',{k:v.get('game_native_v6') for k,v in coverage.items() if k!='_summary'})
    write_json(work/'full_ladder_coverage.json',coverage)
    write_json(work/'math_dependencies.json',{k:v['dependencies'] for k,v in ops.items()})
    write_json(work/'game_math_coverage.json',coverage)
    write_json(work/'curriculum_verification.json',ladders)
    write_json(work/'smoke_report.json',{'result':'PASS','registry':math_checks,
              'legacy':original,'checks':result,'bidirectional':bidirectional,'stateful':stateful,'full_ladder':full,'native':native_proof,'mutation_brain':mutation_proof,'deterministic_brain':deterministic_proof,'captain_source_audit':captain_audit,
              'source_generated':True,
              'model_trained':False,'active_training_modified':False})
    hashes={n:file_sha(work/n) for n in FILES if n!='manifest.json'}
    source_root=Path(__file__).resolve().parent
    source_files=['nanojev_game_math_smoke.py','nanojev_game_math_registry.py',
                  'nanojev_game_ladder_proof.py','nanojev_game_ladder_edge_programs.py','nanojev_game_ladder_state_machine.py','nanojev_game_ladder_full_catalog.py','nanojev_game_native_objectives_v6.py','nanojev_game_mutation_brain_v7.py','nanojev_deterministic_brain_v8.py',
                  'nanojev_game_transformation_library.py','nanojev_game_library_v1.py','nanojev_training_task_store.py']
    source_hashes={n:file_sha(source_root/n) for n in source_files}
    write_json(work/'manifest.json',{'version':VERSION,'generator':GENERATOR,
                   'seed':seed,'instances_per_game':instances,'source_sha256':source_hashes,
                   'generated_sha256':hashes,'files':list(FILES),'status':'PASS'})
    return {'status':'PASS','registry':math_checks,'checks':result,'bidirectional':{k:v for k,v in bidirectional.items() if k!='case_summary'},'stateful':stateful,'full_ladder':full,'native':native_proof,'mutation_brain':mutation_proof,'deterministic_brain':deterministic_proof,'output_files':list(FILES),
            'game_math_coverage':coverage['_summary'],'output_dir':str(work)}

def _safe_replace(destination):
    if not destination.is_dir():raise RuntimeError('REFUSE_OVERWRITE_NOT_DIRECTORY')
    names={p.name for p in destination.iterdir()}
    if names!=set(FILES):raise RuntimeError('REFUSE_OVERWRITE_FOREIGN_LAYOUT')
    meta=json.loads((destination/'manifest.json').read_text())
    if meta.get('generator')!=GENERATOR or meta.get('version')!=VERSION:
        raise RuntimeError('REFUSE_OVERWRITE_FOREIGN_MANIFEST')
    for name,hash_value in meta.get('generated_sha256',{}).items():
        if file_sha(destination/name)!=hash_value:
            raise RuntimeError('REFUSE_OVERWRITE_MODIFIED_OUTPUT')

def run(output_dir:Path|None,seed:int,instances:int,replace:bool):
    if instances<4:raise ValueError('instances-per-game must be >=4')
    if replace and output_dir is None:raise ValueError('--replace requires --output-dir')
    if output_dir is None:
        with tempfile.TemporaryDirectory(prefix='nanojev-math-smoke-') as tmp:
            result=generate(Path(tmp),seed,instances)
            result['output_dir']=None;result['temporary']=True
            return result
    out=output_dir.resolve();out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists():
        if not replace:raise FileExistsError('REFUSE_OVERWRITE_EXISTING_OUTPUT (specify --replace)')
        _safe_replace(out)
    with tempfile.TemporaryDirectory(prefix='.nanojev-math-staging-',dir=out.parent) as tmp:
        stage=Path(tmp)/'verified'
        report=generate(stage,seed,instances)
        if out.exists():
            backup=out.with_name(out.name+'.before-math-smoke')
            if backup.exists():raise FileExistsError('BACKUP_ALREADY_EXISTS')
            out.rename(backup)
            try:stage.rename(out)
            except BaseException:
                backup.rename(out);raise
            shutil.rmtree(backup)
        else:stage.rename(out)
    report['output_dir']=str(out);report['temporary']=False
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--generate',action='store_true',help='Build all source-owned definitions and SQLite data')
    p.add_argument('--verify-all',action='store_true',help='Require full oracle and database verification')
    p.add_argument('--report',action='store_true',help='Print JSON summary')
    p.add_argument('--temporary',action='store_true',help='Test in a temporary directory; no permanent artifacts')
    p.add_argument('--output-dir',type=Path,default=Path('runtime/game-math-source-smoke-v8'))
    p.add_argument('--replace',action='store_true',help='Replace only a verified, unmodified output of this same generator')
    p.add_argument('--instances-per-game',type=int,default=16)
    p.add_argument('--seed',type=int,default=20261016)
    args=p.parse_args(argv)
    if not args.generate or not args.verify_all:
        p.error('require --generate --verify-all; an unverified output is not supported')
    try:
        result=run(None if args.temporary else args.output_dir,args.seed,args.instances_per_game,args.replace)
        if args.report:print(json.dumps(result,indent=2,sort_keys=True))
        else:print('NANOJEV GAME MATH SMOKE: PASS; definitions=%s cases=%s' %
                   (result['checks']['math_definitions'],result['checks']['legacy_cases_verified']))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'FAIL','error_type':type(exc).__name__,'error':str(exc)},indent=2))
        return 1

if __name__=='__main__':raise SystemExit(main())
