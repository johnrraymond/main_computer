#!/usr/bin/env python3
"""Read-only GAMER prompt/register fidelity experiment.

The *08 prompt plus the SAVED model-selected game/formula are the only
register-extraction inputs. The generator's source parameters, math_prompt,
final-answer label and oracle tables are evaluation evidence ONLY.

This is a TEMPLATE-BOUNDED EXACT EXTRACTION control, not evidence that a
language model can extract registers from arbitrary natural-language prompts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from string import Formatter

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_catalog_extensions as ext
from tools import nanojev_gamer_v1_transition_games as transition
from tools import nanojev_game_transformation_library as native
from tools.nanojev_gamer_v1_flat_registers import _eval_plan
from tools.nanojev_gamer_v1_register_oracle import build_oracle
from tools.nanojev_gamer_v1_register_attribution import _source as _flat_source

SCHEMA='nanojev.gamer.v1.prompt-fidelity.v1'
NUMBER=r'[+\-\u2212]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?(?:/\d+)?'
NUM_RE=re.compile(NUMBER)
ZONES=('zero','small_negative','small_positive','outer_negative','outer_positive')
# Explicitly typed *discrete* counts in the original scalar models.
DISCRETE={
    'captain_crew_risk':frozenset(('crew_count',)),
    'population_signal':frozenset(('n',)),
    'quorum_margin':frozenset(('honest_votes','quorum')),
}


def _hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def _parse_num(token):
    if not NUM_RE.fullmatch(str(token)):
        raise ValueError('GAMER_PROMPT_INVALID_NUMERIC_LITERAL: '+str(token))
    return Fraction(str(token).replace('\u2212','-'))


def _template_values(prompt,template,register_ids):
    """Match literal ORIGINAL question template, capturing numeric slots by role.

    No substitution from source.parameters. The authoring template itself is a
    public, static input grammar; its braces specify only variable positions.
    """
    chunks=['\\A'];order=[]
    for literal,name,format_spec,conversion in Formatter().parse(template):
        chunks.append(re.escape(literal))
        if name is not None:
            if name not in register_ids or format_spec or conversion:
                raise ValueError('GAMER_PROMPT_TEMPLATE_SCHEMA_MISMATCH')
            idx=len(order);order.append(name)
            chunks.append(f'(?P<v{idx}>{NUMBER})')
    chunks.append('\\Z')
    m=re.match(''.join(chunks),prompt,flags=re.DOTALL)
    if not m:
        raise ValueError('GAMER_PROMPT_TEMPLATE_DOES_NOT_MATCH')
    values={}
    for i,key in enumerate(order):
        value=_parse_num(m[f'v{i}'])
        if key in values and values[key]!=value:
            raise ValueError('GAMER_PROMPT_REPEATED_REGISTER_CONFLICT: '+key)
        values[key]=value
    return {'parameter:'+key:v for key,v in values.items()}


def extract_registers(prompt,game_id,plan):
    """Read only prompt string, learned game id, and TRAIN-derived formula plan.

    Fail closed on missing/duplicate/ambiguous observations. In particular,
    values appearing elsewhere in the text are never used to fill a slot.
    """
    if not isinstance(prompt,str) or not isinstance(game_id,str) or not isinstance(plan,dict):
        raise ValueError('GAMER_PROMPT_BAD_EXTRACTOR_INPUT')
    required=set(plan['required_registers'])
    if plan['kind']=='matrix':
        if game_id in native.MATRICES:
            title=native.MATRICES[game_id][0]
            rows=native.MATRICES[game_id][2]
            cols=native.MATRICES[game_id][3]
            if not prompt.startswith(f'Consider a two-player {title} game'):
                raise ValueError('GAMER_PROMPT_MATRIX_TITLE_MISMATCH')
            rule=(r'If you choose (?P<a>.+?) and your opponent chooses (?P<b>.+?), '
                  rf'you receive (?P<v>{NUMBER}) points and your opponent receives (?P<w>{NUMBER}) points\.')
            choice=r'Your opponent chooses (.+?)\.'
        elif game_id in ext.EXTRA_MATRICES:
            spec=ext.EXTRA_MATRICES[game_id]
            rows=spec['row_actions'];cols=spec['column_actions']
            if not prompt.startswith(f'Consider this specified example of the named game {spec["title"]}.'):
                raise ValueError('GAMER_PROMPT_MATRIX_TITLE_MISMATCH')
            rule=(r'If you choose (?P<a>.+?) and the opponent chooses (?P<b>.+?), '
                  rf'you earn (?P<v>{NUMBER}) and the opponent earns (?P<w>{NUMBER})\.')
            choice=r'The opponent chooses (.+?)\.'
        else:
            raise ValueError('GAMER_PROMPT_MATRIX_TEMPLATE_UNSUPPORTED')
        entries=list(re.finditer(rule,prompt))
        if len(entries)!=len(rows)*len(cols):
            raise ValueError('GAMER_PROMPT_MATRIX_CELL_COVERAGE_MISMATCH')
        results={}
        for m in entries:
            if m['a'] not in rows or m['b'] not in cols:
                raise ValueError('GAMER_PROMPT_UNKNOWN_MATRIX_ACTION')
            i,j=rows.index(m['a']),cols.index(m['b'])
            for side,label in (('v','self'),('w','opponent')):
                name=f'payoff:r{i}:c{j}:{label}'
                if name in results:raise ValueError('GAMER_PROMPT_DUPLICATE_MATRIX_CELL')
                results[name]=_parse_num(m[side])
        choice_matches=re.findall(choice,prompt)
        if len(choice_matches)!=1 or choice_matches[0] not in cols:
            raise ValueError('GAMER_PROMPT_AMBIGUOUS_OPPONENT_CHOICE')
        results['opponent_action_index']=Fraction(cols.index(choice_matches[0]))
    elif plan['kind']=='formula':
        if game_id in pairs.SCENARIOS:
            results=_template_values(prompt,pairs.SCENARIOS[game_id][0],
                                     {r.removeprefix('parameter:') for r in required})
        elif game_id in ext.EXTRA_SCALARS:
            results=_template_values(prompt,ext.EXTRA_SCALARS[game_id]['prompt'],
                                     {r.removeprefix('parameter:') for r in required})
        elif game_id in transition.SPECS:
            name=transition.SPECS[game_id]['title']
            if not prompt.startswith(f'Consider a bounded, finite one-step instance of {name}.'):
                raise ValueError('GAMER_PROMPT_TRANSITION_TITLE_MISMATCH')
            pattern=rf'The specified (?P<role>[a-z]+(?: [a-z]+)*) is (?P<value>{NUMBER})\.'
            results={}
            for m in re.finditer(pattern,prompt):
                key='parameter:'+m['role'].replace(' ','_')
                if key in results:raise ValueError('GAMER_PROMPT_DUPLICATE_TRANSITION_REGISTER')
                if key not in required:raise ValueError('GAMER_PROMPT_UNEXPECTED_TRANSITION_REGISTER')
                results[key]=_parse_num(m['value'])
        else:
            raise ValueError('GAMER_PROMPT_FORMULA_TEMPLATE_UNSUPPORTED')
    else:
        raise ValueError('GAMER_PROMPT_UNKNOWN_PLAN_KIND')
    if set(results)!=required:
        raise ValueError('GAMER_PROMPT_REGISTER_COVERAGE_MISMATCH: '+
                         ','.join(sorted(required-set(results))))
    return results


def execute_exact(plan,registers):
    """Exact-rational interpreter of TRAIN-compiled deterministic formula.

    It is intentionally separate from the existing float executor so decimal
    cancellation is not inadvertently obscured by binary floating-point.
    """
    if set(registers)!=set(plan['required_registers']):
        raise ValueError('GAMER_PROMPT_EXACT_REGISTER_SET_MISMATCH')
    vals={k:Fraction(str(v)) for k,v in registers.items()}
    if plan['kind']=='matrix':
        j=int(vals['opponent_action_index'])
        if not 0<=j<plan['columns'] or vals['opponent_action_index']!=j:
            raise ValueError('GAMER_PROMPT_MATRIX_INVALID_SELECTOR')
        choices=[vals[f'payoff:r{i}:c{j}:self'] for i in range(plan['rows'])]
        return Fraction(int(choices[1]>choices[0])) if plan['operations']=='choose_second' else max(choices)
    memory={}
    for step in plan['steps']:
        args=[]
        for operand in step['operands']:
            kind=operand['source']
            if kind=='public_register':a=vals[operand['ref']]
            elif kind=='step':a=memory[operand['ref']]
            elif kind=='formula_constant':a=Fraction(str(operand['value']))
            else:raise ValueError('GAMER_PROMPT_UNKNOWN_FORMULA_OPERAND')
            args.append(a)
        a,b=args;op=step['operation']
        if op=='add':v=a+b
        elif op=='subtract':v=a-b
        elif op=='multiply by':v=a*b
        elif op=='divide by':v=a/b
        elif op=='larger of':v=max(a,b)
        elif op=='choose second action if larger':v=Fraction(int(b>a))
        else:raise ValueError('GAMER_PROMPT_UNKNOWN_FORMULA_OPERATION')
        memory[step['id']]=v
    return memory[plan['final_step']]


def _audit_generated_row(row):
    """Corpus-wide prompt anomalies, strictly as diagnostics—not inputs."""
    alerts=[];game=row['game_id'];case=row['case_sha256']
    source=row['source']
    for field in DISCRETE.get(game,()):
        if 'parameters' in source and field in source['parameters']:
            f=Fraction(str(source['parameters'][field]))
            if f.denominator!=1:
                alerts.append({'case_sha256':case,'game_id':game,
                    'kind':'FRACTIONAL_DISCRETE_QUANTITY','register_id':'parameter:'+field,
                    'source':str(f),'extracted':''})
    for match in re.finditer(r'(?<![\d.])[+\-]?\d+\.\d{12,}(?!\d)',row['prompt_08']):
        token=match.group(0)
        # Long binary-arithmetic artifacts are suspicious, not proof of a
        # faulty true value. Never silently rewrite them in an old dataset.
        alerts.append({'case_sha256':case,'game_id':game,
            'kind':'LONG_DECIMAL_PROMPT_LITERAL','register_id':'',
            'source':token,'extracted':''})
    y=float(row['answer'])
    if 0<abs(y)<1e-10:
        alerts.append({'case_sha256':case,'game_id':game,
            'kind':'NEAR_ZERO_NONZERO_LABEL','register_id':'',
            'source':repr(y),'extracted':''})
    return alerts


def _read_certified(flat_report,case_keys):
    """Recover dev prompts from source only after verifying dataset fingerprint.

    Source labels are retained for validation and reporting, NEVER passed to
    extract_registers or execute_exact.
    """
    source=Path(flat_report['source_run']).resolve()
    path=source/'certified_pairs.jsonl'
    if not path.is_file():
        raise FileNotFoundError('GAMER_PROMPT_ORIGINAL_CERTIFIED_PAIRS_REQUIRED: '+str(path))
    digest=_hash(path)
    if digest!=flat_report['source']['dataset_sha256']:
        raise ValueError('GAMER_PROMPT_DATASET_SHA256_MISMATCH')
    selected={};count=0;corpus_anomalies=[]
    with path.open('r',encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():continue
            row=json.loads(line);count+=1
            corpus_anomalies.extend(_audit_generated_row(row))
            key=(row.get('case_sha256'),row.get('game_id'))
            if key in case_keys:
                if key in selected:raise ValueError('GAMER_PROMPT_DUPLICATE_SOURCE_CASE')
                if row['split']!='dev' or not pairs.certify(row):
                    raise ValueError('GAMER_PROMPT_UNCERTIFIED_SOURCE_DEV_CASE')
                selected[key]=row
    if set(selected)!=case_keys:
        raise ValueError('GAMER_PROMPT_MISSING_CERTIFIED_DEV_CASE')
    return path,digest,selected,count,corpus_anomalies


def _evaluate(records,registers,source_rows,plans):
    cases=[];regrows=[];counts=Counter();anomalies=[]
    for record in records:
        key=(record['case_sha256'],record['game_id'])
        row=source_rows[key];game=record['game_id'];route=record['predicted_game_id']
        # CSV register targets are FLOAT projections, so a mathematically exact
        # literal such as 1/3 must be checked against the certified SOURCE
        # rational instead of Fraction(str(0.3333333333333333)). These labels
        # enter diagnostics only, never extraction or execution.
        reference=build_oracle(row)
        truth={r['id']:Fraction(r['value']['exact']) for r in reference['registers']}
        if set(truth)!=set(registers[key]):
            raise ValueError('GAMER_PROMPT_CSV_REGISTER_SCHEMA_MISMATCH')
        for rid,entry in registers[key].items():
            if not math.isclose(float(truth[rid]),entry['truth'],rel_tol=1e-10,abs_tol=1e-9):
                raise ValueError('GAMER_PROMPT_CSV_REGISTER_TARGET_MISMATCH: '+rid)
        ridge={rid:r['predicted'] for rid,r in registers[key].items()}
        target=float(record['target'])
        if route not in plans:raise ValueError('GAMER_PROMPT_UNKNOWN_LEARNED_ROUTE')
        plan=plans[route]['plan']
        extracted={};failure=None;result=None;float_result=None
        # Deployable fall-through path: retain saved ridge inference whenever
        # exact prompt extraction is unavailable or the route is incorrect.
        hybrid=record['predicted_answer']
        if route!=game:
            failure='LEARNED_GAME_ROUTE_MISMATCH'
        else:
            try:
                extracted=extract_registers(row['prompt_08'],route,plan)
                result=execute_exact(plan,extracted)
                float_result=_eval_plan(plan,{r:float(v) for r,v in extracted.items()})[0]
                combination={rid:float(extracted.get(rid,ridge[rid])) for rid in plan['required_registers']}
                hybrid=_eval_plan(plan,combination)[0]
            except (ValueError,ZeroDivisionError,KeyError,OverflowError) as exc:
                failure=type(exc).__name__+': '+str(exc)
        if result is not None and not math.isclose(float(result),target,rel_tol=1e-9,abs_tol=1e-8):
            # Never silently redefine ground truth to match extraction.
            failure='EXACT_EXECUTION_DISAGREES_WITH_CERTIFIED_LABEL'
        complete=bool(result is not None and failure is None)
        if not complete:hybrid=record['predicted_answer']
        case={'case_sha256':key[0],'game_id':game,'predicted_game_id':route,
              'source_kind':row['source']['kind'],'target_zone':record['target_zone'],
              'target':target,'ridge_answer':record['predicted_answer'],
              'ridge_absolute_error':(abs(float(record['predicted_answer'])-target)
                                      if record['predicted_answer'] is not None else None),
              'exact_answer':float(result) if result is not None else None,
              'exact_answer_rational':str(result) if result is not None else None,
              'exact_float_executor_answer':float_result,
              'hybrid_answer':hybrid,'hybrid_source':('exact' if complete else 'ridge_fallback'),
              'extraction_complete':complete,
              'failure':failure,'register_count':len(truth),
              'exact_registers':len(extracted),
              'arithmetic_zero':bool(result==0) if result is not None and plan['kind']=='formula' else None,
              'discrete_action_zero':bool(result==0) if result is not None and plan['kind']=='matrix' else None}
        cases.append(case)
        for rid,expected in truth.items():
            value=extracted.get(rid)
            if value is not None and value!=expected:
                anomalies.append({'case_sha256':key[0],'game_id':game,'kind':'REGISTER_LITERAL_MISMATCH',
                                  'register_id':rid,'extracted':str(value),'source':str(expected)})
            regrows.append({'case_sha256':key[0],'game_id':game,'register_id':rid,
                'target_zone':record['target_zone'],'oracle_value':float(expected),
                'ridge_value':ridge[rid],'exact_value':float(value) if value is not None else None,
                'exact_rational':str(value) if value is not None else None,
                'exact_match':(value==expected) if value is not None else None,
                'ridge_absolute_error':abs(ridge[rid]-float(expected)),
                'exact_absolute_error':abs(float(value)-float(expected)) if value is not None else None})
        # Audit labels from certified generator; not used in extraction.
        for name in DISCRETE.get(game,()):
            value=Fraction(str(row['source']['parameters'][name]))
            if value.denominator!=1:
                anomalies.append({'case_sha256':key[0],'game_id':game,'kind':'FRACTIONAL_DISCRETE_QUANTITY',
                                  'register_id':'parameter:'+name,'source':str(value),
                                  'extracted':str(extracted.get('parameter:'+name,''))})
        if (0<abs(target)<1e-10 and plan['kind']=='formula'):
            anomalies.append({'case_sha256':key[0],'game_id':game,'kind':'NEAR_ZERO_NONZERO_LABEL',
                              'register_id':'','source':repr(target),
                              'extracted':str(result) if result is not None else ''})
        if complete:
            counts['successful_cases']+=1
        else:counts['failed_cases']+=1
    return cases,regrows,anomalies


def _metrics(cases,field):
    valid=[c for c in cases if c[field] is not None]
    n=len(cases)
    errors=[abs(c[field]-c['target']) for c in valid]
    return {'total_cases':n,'valid_cases':len(valid),'coverage':len(valid)/n if n else 0.,
            'mae_on_valid':sum(errors)/len(errors) if errors else None,
            'mae_with_failures':sum(errors)/n if len(valid)==n and n else None,
            'sign_accuracy_nonzero':(sum(c[field]*c['target']>0 for c in valid if c['target']!=0)
                                     /sum(c['target']!=0 for c in valid)
                                     if any(c['target']!=0 for c in valid) else None),
            'exact_zero_within_1e_6':(sum(abs(c[field])<=1e-6 for c in valid if c['target']==0)
                                      /sum(c['target']==0 for c in valid)
                                      if any(c['target']==0 for c in valid) else None)}


def _zone_metrics(cases,field):
    result={}
    for zone in ZONES:
        grp=[c for c in cases if c['target_zone']==zone]
        result[zone]=_metrics(grp,field)
    result['arithmetic_zero'] = _metrics([c for c in cases if c['target']==0 and c['source_kind'] not in ('matrix','bounded_named_matrix')],field)
    result['matrix_action_zero'] = _metrics([c for c in cases if c['target']==0 and c['source_kind'] in ('matrix','bounded_named_matrix')],field)
    return result


def _csv(path,rows,fields):
    with Path(path).open('w',encoding='utf-8',newline='') as f:
        out=csv.DictWriter(f,fields,extrasaction='ignore');out.writeheader();out.writerows(rows)


def run_prompt_fidelity(flat_run,*,output=None):
    """Evaluate exact prompt extraction against completed frozen-feature ridge run."""
    from tools.nanojev_gamer_v1_diagnostic_train import _output_path
    from tools.nanojev_gamer_v1_handoff import atomic_json
    base,report,plans,records,regs,inputs,hashes=_flat_source(flat_run)
    source_path,source_hash,source_rows,source_count,corpus_audit=_read_certified(report,
        {(c['case_sha256'],c['game_id']) for c in records})
    dest=_output_path(base,output,suffix='prompt-fidelity')
    dest.mkdir(parents=True,exist_ok=False)
    stage='evaluation'
    try:
        print('GAMER FLAT PROMPT-FIDELITY:',dest,flush=True)
        print('  source frozen LoRA and flat run READ ONLY; no model training',flush=True)
        case_rows,reg_rows,anomalies=_evaluate(records,regs,source_rows,plans)
        _csv(dest/'case_comparison.csv',case_rows,
             ['case_sha256','game_id','predicted_game_id','source_kind','target_zone','target',
              'ridge_answer','ridge_absolute_error','exact_answer','exact_answer_rational',
              'exact_float_executor_answer','hybrid_answer','hybrid_source','extraction_complete','failure',
              'register_count','exact_registers','arithmetic_zero','discrete_action_zero'])
        _csv(dest/'register_comparison.csv',reg_rows,
             ['case_sha256','game_id','register_id','target_zone','oracle_value','ridge_value',
              'exact_value','exact_rational','exact_match','ridge_absolute_error','exact_absolute_error'])
        _csv(dest/'prompt_anomalies.csv',anomalies,
             ['case_sha256','game_id','kind','register_id','source','extracted'])
        _csv(dest/'corpus_prompt_anomalies.csv',corpus_audit,
             ['case_sha256','game_id','kind','register_id','source','extracted'])
        if not all(_hash(path)==value for name,path in inputs.items() for value in [hashes[name]]):
            raise ValueError('GAMER_PROMPT_FLAT_ARTIFACT_CHANGED_DURING_ANALYSIS')
        if _hash(source_path)!=source_hash:
            raise ValueError('GAMER_PROMPT_SOURCE_CORPUS_CHANGED_DURING_ANALYSIS')
        exact_cases=[c for c in case_rows if c['extraction_complete']]
        r_ok=[r for r in reg_rows if r['exact_match'] is True]
        outcomes={name:_metrics(case_rows,name) for name in ('ridge_answer','exact_answer','hybrid_answer')}
        if not math.isclose(outcomes['ridge_answer']['mae_on_valid'],
                            report['dev']['learned_route']['metrics_on_valid_predictions']['raw_mae'],
                            rel_tol=1e-9,abs_tol=1e-8):
            raise ValueError('GAMER_PROMPT_RIDGE_REPLAY_METRIC_MISMATCH')
        result={'schema':SCHEMA,'status':'COMPLETE_DIAGNOSTIC_NOT_PROMOTED',
            'source_flat_run':str(base),'source_corpus':str(source_path),
            'source_dataset_sha256':source_hash,'verified_source_cases':len(source_rows),
            'source_dataset_cases':source_count,'dev_cases':len(case_rows),
            'extracted_cases':len(exact_cases),'extracted_registers':len(r_ok),
            'dev_registers':len(reg_rows),'exact_register_fraction':len(r_ok)/len(reg_rows),
            'outcomes':outcomes,
            'by_target_zone':{name:_zone_metrics(case_rows,name)
                              for name in ('ridge_answer','exact_answer','hybrid_answer')},
            'anomalies':dict(Counter(x['kind'] for x in anomalies)),
            'corpus_anomalies':dict(Counter(x['kind'] for x in corpus_audit)),
            'hybrid_exact_cases':len(exact_cases),
            'hybrid_ridge_fallback_cases':len(case_rows)-len(exact_cases),
            'extraction_failures':dict(Counter(c['failure'] for c in case_rows if c['failure'])),
            'controls':{'game_selection':'SAVED_PREDICTED_GAME_ID_NOT_ORACLE',
                'register_source':'ONLY_ORIGINAL_PROMPT_08_AND_STATIC_PUBLIC_TEMPLATES',
                'formula_source':'SAVED_TRAIN_DERIVED_PLAN',
                'ridge':'ORIGINAL_SAVED_LEARNED_ROUTE_AND_REGISTER_CSV',
                'labels':'DEV_EVALUATION_ONLY',
                'exact_execution':'FRACTIONAL_RATIONAL_ARITHMETIC',
                'generalization':'CURRENT_GENERATOR_TEMPLATES_ONLY_NOT_ARBITRARY_PROMPTS',
                'model_mutation':'NONE'},
            'artifacts':['final_report.json','case_comparison.csv',
                          'register_comparison.csv','prompt_anomalies.csv',
                          'corpus_prompt_anomalies.csv']}
        atomic_json(dest/'final_report.json',result)
        print('GAMER FLAT PROMPT-FIDELITY COMPLETE:',dest/'final_report.json',flush=True)
        print('  exact extracted cases:',len(exact_cases),'/',len(case_rows),
              ' register matches:',len(r_ok),'/',len(reg_rows),flush=True)
        for name,m in outcomes.items():
            print(' ',name,'MAE:',m['mae_on_valid'],'coverage:',m['coverage'],flush=True)
        print('  arithmetic-zero cases:',
              result['by_target_zone']['exact_answer']['arithmetic_zero']['total_cases'],
              'matrix action-zero cases:',
              result['by_target_zone']['exact_answer']['matrix_action_zero']['total_cases'],flush=True)
        print('  dev anomalies:',result['anomalies'],flush=True)
        print('  whole-corpus anomalies:',result['corpus_anomalies'],flush=True)
        print('  Source and saved model READ ONLY; diagnostic not promoted.',flush=True)
        return result
    except Exception as exc:
        atomic_json(dest/'failure_report.json',{'schema':SCHEMA,'status':'INCOMPLETE',
            'failed_stage':stage,'exception_type':type(exc).__name__,
            'exception':str(exc),'source_unchanged':_hash(source_path)==source_hash})
        raise


if __name__=='__main__':
    raise SystemExit('Use: python -m tools.nanojev_gamer_v1_staircase flat-prompt --run FLAT_RUN')
