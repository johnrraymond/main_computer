#!/usr/bin/env python3
"""Source-certified GAMER register DAGs and read-only frozen-feature probes.

The existing *08 prompt is the ONLY input to the LoRA features. Source values,
math tables, and factor results are used as oracle LABELS, never as probe inputs.
All diagnostics are written outside the active run, and do not update Qwen,
LoRA, C0 Wide or its optimizer. This is a decomposition/decodability diagnostic,
not a deployable register resolver or proof of LoRA insufficiency.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import ast
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from datetime import datetime, timezone

import numpy as np

from tools import nanojev_gamer_v1_pairs as pairs

SCHEMA = 'nanojev.gamer.v1.register-oracle.v1'
PROBE_SCHEMA = 'nanojev.gamer.v1.register-recovery.v1'
OPS = frozenset(('add', 'subtract', 'multiply by', 'divide by',
                 'larger of', 'choose second action if larger'))


def _fingerprint(raw):
    return hashlib.sha256(raw).hexdigest()


def _sha(obj):
    return _fingerprint(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                                   ensure_ascii=False, allow_nan=False).encode('utf-8'))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fraction(x):
    if isinstance(x, bool):
        raise ValueError('GAMER_REGISTER_BOOLEAN_NOT_NUMERIC')
    return x if isinstance(x, Fraction) else Fraction(str(x))


def _literal_visible(prompt, value):
    """A lexical witness, NOT a claim that a value's semantic role was resolved."""
    f = _fraction(value)
    candidates = {pairs.number(value), str(value), str(f)}
    for literal in candidates:
        if not literal:
            continue
        pattern = r'(?<![0-9.])' + re.escape(literal) + r'(?![0-9]|\.[0-9])'
        if re.search(pattern, prompt):
            return literal
    return None


def _value(f):
    f = _fraction(f)
    return {'exact': str(f), 'float': float(f)}


def _reg(reg_id, kind, value, *, label, scope='prompt_input', witness=None):
    f = _fraction(value)
    if not math.isfinite(float(f)):
        raise ValueError('GAMER_REGISTER_NONFINITE')
    return {'id': reg_id, 'kind': kind, 'label': label, 'value': _value(f),
            'scope': scope, 'literal_witness': witness,
            'visibility': ('PROMPT_LITERAL_VERIFIED' if witness is not None
                           else 'NOT_VERIFIED_FROM_PROMPT')}


def _table_family(row):
    return sorted(set(row.get('table_families') or []))


def _symbolic_bindings(row):
    """Bind each arithmetic operand to its ACTUAL source register or constant.

    Values alone cannot distinguish, for example, a prior of 1 from a payout
    of 1. The certified generator's operation tree disambiguates the roles.
    """
    from tools import nanojev_game_native_objectives_v6 as native
    from tools import nanojev_gamer_v1_catalog_extensions as ext
    from tools import nanojev_gamer_v1_transition_games as trans
    src=row['source'];game=row['game_id'];kind=src['kind']
    if kind in ('matrix','bounded_named_matrix'):
        j=src['rival_index'];n=len(src['row_actions'])
        def payoff(i):
            return {'source':'public_register','ref':f'payoff:r{i}:c{j}:self',
                    'value':_value(src['U'][i][j][0])}
        if n==2:
            operations=[('choose second action if larger',payoff(0),payoff(1))]
        elif n==3 and kind=='bounded_named_matrix':
            operations=[('larger of',payoff(0),payoff(1)),
                        ('larger of',{'source':'step','ref':'step:1'},payoff(2))]
        else:
            raise ValueError('GAMER_REGISTER_UNSUPPORTED_MATRIX_ARITY')
    else:
        if kind=='scalar':
            tree=native._expr(game)
            memoize=True
        elif kind=='bounded_scalar':
            tree=pairs._extension_scalar_tree(game)
            memoize=True
        elif kind=='bounded_state_transition':
            tree=ast.parse(trans.SPECS[game]['formula'],mode='eval').body
            memoize=False
        else:
            raise ValueError('GAMER_REGISTER_UNSUPPORTED_SOURCE_KIND')
        operations=[];memo={}
        def walk(node):
            if isinstance(node,ast.Name):
                if node.id not in src['parameters']:
                    raise ValueError('GAMER_REGISTER_HIDDEN_OPERAND_NAME')
                return {'source':'public_register','ref':'parameter:'+node.id,
                        'value':_value(src['parameters'][node.id])}
            if isinstance(node,ast.Constant) and type(node.value) in (int,float):
                return {'source':'formula_constant','value':_value(node.value)}
            key=ast.dump(node,include_attributes=False)
            if memoize and key in memo:
                return {'source':'step','ref':memo[key]}
            if isinstance(node,ast.BinOp) and type(node.op) in pairs.OPS:
                op=pairs.OPS[type(node.op)][0]
                left,right=walk(node.left),walk(node.right)
            elif (isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
                  and node.func.id=='max' and len(node.args)==2 and not node.keywords):
                op='larger of'
                left,right=walk(node.args[0]),walk(node.args[1])
            else:
                raise ValueError('GAMER_REGISTER_UNSUPPORTED_FORMULA_AST')
            n=len(operations)+1
            operations.append((op,left,right))
            if memoize:memo[key]=f'step:{n}'
            return {'source':'step','ref':f'step:{n}'}
        walk(tree)
    if len(operations)!=len(row['factor_steps']):
        raise ValueError('GAMER_REGISTER_SYMBOLIC_STEP_COUNT_MISMATCH')
    return operations


def build_oracle(row):
    """Only regenerate/certify source cases, never infer labels from `math_prompt`.

    Parameter slots and matrix payoff cells are public INPUT registers.
    Arithmetic factor nodes are supervised INTERMEDIATES, forbidden as input.
    Hidden affine-scale/variant metadata and transition hidden states are
    intentionally excluded. Any unverified public value is excluded from probes.
    """
    if not pairs.certify(row):
        raise ValueError('GAMER_REGISTER_SOURCE_NOT_CERTIFIED')
    game = row['game_id']
    source = row['source']
    prompt = row['prompt_08']
    kind = source['kind']
    registers = []
    if kind in ('scalar', 'bounded_scalar', 'bounded_state_transition'):
        for name, value in sorted(source['parameters'].items()):
            registers.append(_reg('parameter:'+name, 'parameter', value, label=name,
                                  witness=_literal_visible(prompt, value)))
    elif kind in ('matrix', 'bounded_named_matrix'):
        payoffs = source['U']
        for i, rr in enumerate(payoffs):
            for j, cell in enumerate(rr):
                for k, player in enumerate(('self', 'opponent')):
                    name = f'payoff:r{i}:c{j}:{player}'
                    registers.append(_reg(name, 'payoff', cell[k], label=name,
                                          witness=_literal_visible(prompt, cell[k])))
        j = source['rival_index']
        name = source['column_actions'][j]
        # The index is implicit in the ordered, model-visible action list;
        # its numeric value need not appear as an isolated literal.
        selector_public = name in prompt and all(a in prompt for a in source['column_actions'])
        registers.append(_reg('opponent_action_index', 'action_selector', j,
                              label='opponent_action_index',
                              witness=name if selector_public else None))
    else:
        raise ValueError('GAMER_REGISTER_UNKNOWN_SOURCE_KIND: ' + str(kind))
    if any(r['visibility'] != 'PROMPT_LITERAL_VERIFIED' for r in registers):
        bad = [r['id'] for r in registers if r['visibility'] != 'PROMPT_LITERAL_VERIFIED']
        # Fail closed rather than train against a possibly hidden generator value.
        raise ValueError('GAMER_REGISTER_PROMPT_VISIBILITY_NOT_PROVEN: '+game+':'+','.join(bad))
    steps = []
    memory = {}
    bindings=_symbolic_bindings(row)
    for step,(expected_op,symbolic_left,symbolic_right) in zip(row['factor_steps'],bindings):
        n = int(step['step'])
        if n != len(steps) + 1:
            raise ValueError('GAMER_REGISTER_NON_SEQUENTIAL_DAG')
        if step['operation']!=expected_op:
            raise ValueError('GAMER_REGISTER_SYMBOLIC_OPERATION_MISMATCH')
        operands=[]
        for side,symbolic in (('left',symbolic_left),('right',symbolic_right)):
            literal=step[side]
            operand=dict(symbolic)
            if symbolic['source']=='step':
                dep=int(symbolic['ref'].split(':')[1])
                if dep not in memory or literal!=f'step {dep}':
                    raise ValueError('GAMER_REGISTER_SYMBOLIC_DEPENDENCY_MISMATCH')
                operand['value']=_value(memory[dep])
            else:
                if isinstance(literal,str) and literal.startswith('step '):
                    raise ValueError('GAMER_REGISTER_EXPECTED_LITERAL')
                if _fraction(literal)!=_fraction(symbolic['value']['exact']):
                    raise ValueError('GAMER_REGISTER_SYMBOLIC_VALUE_MISMATCH')
                operand['prompt_witness']=_literal_visible(prompt,literal)
            operands.append(operand)
        a,b=(_fraction(op['value']['exact']) for op in operands)
        op=step['operation']
        if op=='add':v=a+b
        elif op=='subtract':v=a-b
        elif op=='multiply by':v=a*b
        elif op=='divide by':v=a/b
        elif op=='larger of':v=max(a,b)
        elif op=='choose second action if larger':v=Fraction(int(b>a))
        else:raise ValueError('GAMER_REGISTER_UNSUPPORTED_OPERATION: '+op)
        memory[n]=v
        steps.append({'id':f'step:{n}','operation':op,'operands':operands,
                      'result':_value(v),'scope':'SUPERVISION_ONLY_NOT_MODEL_INPUT'})
    end = int(row['factor_final_step'])
    if end not in memory or memory[end] != pairs.evaluate_factors(row['factor_steps'], end):
        raise ValueError('GAMER_REGISTER_DAG_ORACLE_MISMATCH')
    if not math.isclose(float(memory[end]), float(row['answer']), rel_tol=1e-10, abs_tol=1e-9):
        raise ValueError('GAMER_REGISTER_FINAL_ANSWER_MISMATCH')
    return {'schema':SCHEMA, 'case_sha256':row['case_sha256'], 'game_id':game,
            'split':row['split'], 'case_index':row['case_index'],
            'prompt_sha256':_fingerprint(prompt.encode('utf-8')),
            'source_kind':kind, 'table_families':_table_family(row),
            'target_zone':_target_zone(float(row['answer'])),
            'registers':registers, 'steps':steps, 'final_step':f'step:{end}',
            'final_answer':_value(memory[end]),
            'visibility_contract':'CERTIFIED_PROMPT_REGENERATION_AND_LITERAL_WITNESSES',
            'forbidden_feature_inputs':['source', 'math_prompt', 'factor_steps',
                                        'oracle_registers', 'oracle_intermediates',
                                        'answer', 'table_families', 'game_id']}


def _target_zone(y):
    if y==0:return 'zero'
    if -1<=y<0:return 'small_negative'
    if 0<y<=1:return 'small_positive'
    return 'outer_negative' if y<0 else 'outer_positive'


def _signed_log(x):
    x = np.asarray(x, dtype=np.float64)
    return np.sign(x) * np.log1p(np.abs(x))


def _unsign_log(x):
    x = np.asarray(x, dtype=np.float64)
    return np.sign(x) * np.expm1(np.clip(np.abs(x), 0, 25))


def _dual_probe(train_x, train_y, dev_x, *, alpha=0.1):
    """Frozen-feature, supervised ridge readout; NOT a causal sufficiency test."""
    if not len(train_y):
        raise ValueError('GAMER_REGISTER_PROBE_EMPTY')
    train_x = np.asarray(train_x, dtype=np.float64)
    dev_x = np.asarray(dev_x, dtype=np.float64)
    y = _signed_log(train_y)
    xmean = train_x.mean(axis=0)
    x = train_x-xmean
    d = dev_x-xmean
    # Shared global normalization, never estimated using dev labels or vectors.
    scale = max(float(np.sqrt(np.mean(x*x))), 1e-6)
    x = x / (scale * math.sqrt(x.shape[1]))
    d = d / (scale * math.sqrt(x.shape[1]))
    k = x @ x.T
    lam = max(alpha*float(np.trace(k))/max(1,len(k)), 1e-6)
    weight = np.linalg.solve(k + np.eye(len(k))*lam, y-y.mean())
    pred = d @ x.T @ weight + y.mean()
    return _unsign_log(pred), _unsign_log(np.full(len(dev_x), y.mean()))


def _probe_metrics(truth, pred, baseline):
    truth = np.asarray(truth, dtype=np.float64)
    err = np.abs(np.asarray(pred) - truth)
    null = np.abs(np.asarray(baseline) - truth)
    return {'cases':int(len(truth)), 'probe_raw_mae':float(err.mean()),
            'baseline_raw_mae':float(null.mean()),
            'probe_signed_log_mae':float(np.abs(_signed_log(pred)-_signed_log(truth)).mean()),
            'baseline_signed_log_mae':float(np.abs(_signed_log(baseline)-_signed_log(truth)).mean()),
            'better_than_mean_baseline_raw':bool(err.mean()<null.mean()),
            'better_than_mean_baseline_signed_log':bool(
                np.abs(_signed_log(pred)-_signed_log(truth)).mean()<
                np.abs(_signed_log(baseline)-_signed_log(truth)).mean())}


def probe_registers(oracle_train, oracle_dev, x_train, x_dev, *, limit=128):
    """Two bounded baselines: per-game/role oracle routing and shared role.

    Per-game routing is DIAGNOSTIC ONLY because inference has no oracle game ID.
    Shared role probes do not use game IDs for routing, but selecting the role
    itself is oracle-conditioned; this does not implement a register selector.
    """
    if limit < 2:
        raise ValueError('GAMER_REGISTER_PROBE_LIMIT_TOO_LOW')
    x_train=np.asarray(x_train,dtype=np.float64)
    x_dev=np.asarray(x_dev,dtype=np.float64)
    if (x_train.ndim!=2 or x_dev.ndim!=2 or x_train.shape[0]!=len(oracle_train)
        or x_dev.shape[0]!=len(oracle_dev) or x_train.shape[1]!=x_dev.shape[1]
        or not np.isfinite(x_train).all() or not np.isfinite(x_dev).all()):
        raise ValueError('GAMER_REGISTER_FEATURE_SHAPE_OR_FINITE_INVALID')
    results=[]; cases=[]; skip=Counter()
    for routing in ('oracle_game_and_role', 'role_only'):
        groups = defaultdict(lambda: {'train':[], 'dev':[]})
        for split, docs in (('train',oracle_train),('dev',oracle_dev)):
            for i, doc in enumerate(docs):
                for register in doc['registers']:
                    # Only public, certified source registers. No intermediate or answer input.
                    if register['visibility']!='PROMPT_LITERAL_VERIFIED':
                        continue
                    key=(doc['game_id']+':'+register['id'] if routing=='oracle_game_and_role'
                         else register['id'])
                    groups[key][split].append((i,register))
        for key,g in sorted(groups.items()):
            if len(g['train'])<2 or not g['dev']:
                skip['insufficient_train_or_dev'] += 1
                continue
            # Preserve deterministic coverage of games in shared-role groups.
            entries = g['train']
            if len(entries)>limit:
                by_game=defaultdict(list)
                for i,r in entries:by_game[oracle_train[i]['game_id']].append((i,r))
                order=[]
                while any(by_game.values()):
                    for game in sorted(by_game):
                        if by_game[game]:order.append(by_game[game].pop(0))
                entries=order[:limit]
            ti=np.asarray([i for i,_ in entries],dtype=int)
            di=np.asarray([i for i,_ in g['dev']],dtype=int)
            yt=np.asarray([float(r['value']['float']) for _,r in entries],dtype=np.float64)
            yd=np.asarray([float(r['value']['float']) for _,r in g['dev']],dtype=np.float64)
            pred,null = _dual_probe(x_train[ti],yt,x_dev[di])
            info=_probe_metrics(yd,pred,null)
            info.update({'routing':routing,'register_role':key,
                         'train_cases':int(len(entries)),
                         'train_games':len({oracle_train[i]['game_id'] for i in ti}),
                         'dev_games':len({oracle_dev[i]['game_id'] for i in di}),
                         'probe_is_deployable':False})
            results.append(info)
            for (i,reg),truth,p,b in zip(g['dev'],yd,pred,null):
                cases.append({'routing':routing,'register_role':key,
                              'game_id':oracle_dev[i]['game_id'],
                              'target_zone':oracle_dev[i]['target_zone'],
                              'case_sha256':oracle_dev[i]['case_sha256'],
                              'register_id':reg['id'], 'target':float(truth),
                              'prediction':float(p), 'mean_baseline':float(b),
                              'absolute_error':float(abs(p-truth)),
                              'baseline_absolute_error':float(abs(b-truth))})
    return results,cases,dict(skip)


def _write_json(path,obj):
    from tools.nanojev_gamer_v1_handoff import atomic_json
    atomic_json(path,obj)


def _output_path(run, output):
    from tools.nanojev_gamer_v1_diagnostic_train import _output_path
    return _output_path(run,output,suffix='register-oracle')


def _summary(rows):
    games=defaultdict(lambda: {'cases':0,'registers':set(),'operations':Counter(),
                               'families':set(),'unverified':0})
    for doc in rows:
        item=games[doc['game_id']]
        item['cases']+=1
        item['registers'].update(r['id'] for r in doc['registers'])
        item['operations'].update(s['operation'] for s in doc['steps'])
        item['families'].update(doc['table_families'])
        item['unverified']+=sum(r['visibility']!='PROMPT_LITERAL_VERIFIED'
                                for r in doc['registers'])
    return {g:{'cases':v['cases'],'registers':sorted(v['registers']),
               'operations':dict(sorted(v['operations'].items())),
               'table_families':sorted(v['families']),
               'unverified_public_registers':v['unverified']}
            for g,v in sorted(games.items())}


def run_register_oracle(run, *,output=None, max_games=0,train_per_game=0,
                        dev_per_game=0, probe_limit=128):
    """No GPU, model load, optimizer, LoRA training or production mutation."""
    from tools.nanojev_gamer_v1_diagnostic_train import _source_inputs
    run=Path(run).resolve()
    if not run.is_dir():raise FileNotFoundError('GAMER_REGISTER_RUN_MISSING')
    if min(probe_limit,2)<2 or min(max_games,train_per_game,dev_per_game)<0:
        raise ValueError('GAMER_REGISTER_INVALID_CONTROLS')
    output=_output_path(run,output)
    selected=_source_inputs(run,max_games=max_games,train_per_game=train_per_game,
                            dev_per_game=dev_per_game)
    output.mkdir(parents=True,exist_ok=False)
    stage='build_oracle'
    evidence=output/'events.jsonl'
    def event(kind, **payload):
        with evidence.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'time':_now(),'event':kind,**payload},allow_nan=False)+'\n')
            f.flush()
    try:
        print('GAMER REGISTER-ORACLE:',output,flush=True)
        print('  read-only source:',run,flush=True)
        print('  certified games:',len(selected['games']),flush=True)
        event('start',games=selected['games'],dataset_sha256=selected['manifest']['dataset_sha256'],
              head_sha256=selected['head_sha256'],adapter_sha256=selected['head_state']['adapter_sha256'])
        oracle={}
        for split in ('train','dev'):
            original=selected['selected'][split]['rows']
            path=output/f'{split}_register_oracles.jsonl'
            docs=[]
            with path.open('w',encoding='utf-8') as f:
                for row in original:
                    doc=build_oracle(row)
                    docs.append(doc)
                    f.write(json.dumps(doc,sort_keys=True,allow_nan=False)+'\n')
                f.flush()
            oracle[split]=docs
            event('oracle_split_complete',split=split,cases=len(docs),
                  register_labels=sum(len(d['registers']) for d in docs),
                  arithmetic_steps=sum(len(d['steps']) for d in docs))
            print(f'  {split}: {len(docs)} cases, '
                  f'{sum(len(d["registers"]) for d in docs)} register labels',flush=True)
        stage='probe_frozen_features'
        results,cases,skips=probe_registers(oracle['train'],oracle['dev'],
            selected['selected']['train']['features'],selected['selected']['dev']['features'],
            limit=probe_limit)
        event('probe_complete',group_count=len(results),prediction_count=len(cases),skipped=skips)
        _write_json(output/'probe_groups.json',results)
        with (output/'probe_predictions.csv').open('w',encoding='utf-8',newline='') as f:
            fields=['routing','register_role','game_id','target_zone','case_sha256','register_id',
                    'target','prediction','mean_baseline','absolute_error','baseline_absolute_error']
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(cases)
        byrouting={}
        for routing in ('oracle_game_and_role','role_only'):
            subset=[r for r in cases if r['routing']==routing]
            if subset:
                byrouting[routing]={'evaluated_register_predictions':len(subset),
                    'raw_mae':float(np.mean([r['absolute_error'] for r in subset])),
                    'mean_baseline_raw_mae':float(np.mean([r['baseline_absolute_error'] for r in subset])),
                    'groups':sum(r['routing']==routing for r in results)}
        zone_metrics={}
        for routing in ('oracle_game_and_role','role_only'):
            zone_metrics[routing]={}
            for zone in ('zero','small_negative','small_positive','outer_negative','outer_positive'):
                cohort=[r for r in cases if r['routing']==routing and r['target_zone']==zone]
                if cohort:
                    zone_metrics[routing][zone]={
                        'register_predictions':len(cohort),
                        'probe_raw_mae':float(np.mean([r['absolute_error'] for r in cohort])),
                        'baseline_raw_mae':float(np.mean([r['baseline_absolute_error'] for r in cohort]))}
        summary=_summary(oracle['train']+oracle['dev'])
        _write_json(output/'register_catalog.json',summary)
        report={'schema':PROBE_SCHEMA,'status':'COMPLETE_BOUNDED_DECODABILITY_PROBE_NOT_PROMOTED',
                'source_run':str(run),'output':str(output),
                'dataset_sha256':selected['manifest']['dataset_sha256'],
                'frozen_adapter_sha256':selected['head_state']['adapter_sha256'],
                'head_checkpoint_sha256':selected['head_sha256'],
                'cases':{s:len(oracle[s]) for s in oracle},
                'game_count':len(summary),
                'register_labels':{s:sum(len(d['registers']) for d in oracle[s]) for s in oracle},
                'factor_steps':{s:sum(len(d['steps']) for d in oracle[s]) for s in oracle},
                'probe':byrouting, 'probe_by_final_answer_zone':zone_metrics,
                'skipped_groups':skips,
                'limitations':[
                    'Only original *08 prompt features are used as probe inputs; oracle labels never enter features.',
                    'The oracle is built from certified game-generator parameters and factor DAGs, not a model-produced prediction.',
                    'Per-game probe routing uses oracle game identity and cannot be deployed without a learned game/register router.',
                    'Role-only probes still assume the requested role is known; register selection is not trained in this stage.',
                    'Each game has few independent training and development cases; probe failure is not evidence of absent LoRA information.',
                    'Scalar/embedding probes diagnose decodability under these regressors, not information-theoretic sufficiency.',
                    'Source-generated answer and intermediate step values are supervision-only; never feed them to an inference model.',
                    'Table family memberships overlap; calculation is shared across tasks.',
                ],
                'next_gate':'Test learned register selection and operation routing only after inspecting role-level recovery.',
                'artifacts':['train_register_oracles.jsonl','dev_register_oracles.jsonl',
                             'register_catalog.json','probe_groups.json',
                             'probe_predictions.csv','events.jsonl','final_report.json']}
        _write_json(output/'final_report.json',report)
        event('completed',status=report['status'],probes=byrouting)
        print('GAMER REGISTER-ORACLE COMPLETE:',output/'final_report.json',flush=True)
        for route,metric in byrouting.items():
            print(f'  {route}: MAE={metric["raw_mae"]:.5g} '
                  f'vs mean-baseline={metric["mean_baseline_raw_mae"]:.5g}; '
                  f'{metric["groups"]} groups',flush=True)
        print('  No LoRA or numerical-head weights changed.',flush=True)
        return report
    except Exception as exc:
        _write_json(output/'failure_report.json',{'schema':PROBE_SCHEMA,'status':'INCOMPLETE',
            'failed_stage':stage,'exception_type':type(exc).__name__, 'exception':str(exc),
            'events':str(evidence)})
        event('failure',stage=stage,error=str(exc))
        raise
