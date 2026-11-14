"""G1-0022: frozen boundary guard versus control on predeclared structural OOD.

This is a no-training, no-tuning *out-of-template* stress test. It deliberately
changes sentence order and clause layout while retaining annotated source graphs.
It does NOT consume the existing challenge/test splits, constitute sealed-test
certification, promote a production decoder, or unlock G2.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re

import torch

from tools.nanojev_gamer_v1_g1_boundary_guard_validation import (
    SCHEMA as PRIOR_SCHEMA, CONTROL, GUARD, SEED as PRIOR_SEED,
    MIN_SCORE_RETENTION, GUARD_ENABLED, FILES as SELECTIVE_FILES,
    _load_selective_reference, _check_files_unchanged,
)
from tools.nanojev_gamer_v1_g1_action_width_validation import (
    _load_prior_inputs, _load_action_reference, _load_ranking_reference,
    _verify_no_drift, fresh_rows as prior_width_rows,
)
from tools.nanojev_gamer_v1_g1_action_conflict_diagnosis import (
    _load_width_reference, _read_jsonl, WIDTH_FILES,
)
from tools.nanojev_gamer_v1_g1_selective_conflict import (
    _read_conflict_reference, selective_decode, CONFLICT_FILES,
)
from tools.nanojev_gamer_v1_g1_action_errors import WINNER_HEAD
from tools.nanojev_gamer_v1_g1_action_ranking import (
    _candidate_scores, _file_sha, decode_action_ranked,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, FAMILIES, _jsonl_bytes, _summarize, _paired,
)
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_fresh_dev import _original_prompts
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift

SCHEMA='nanojev.gamer.graph.g1.structural-ood-fixed-comparison.v1'
SEED=220022
SHIFT=4.0
LAYOUTS=('evidence_first', 'ledger_grid')
PRIOR_FILES=('boundary_guard_validation_report.json',
             'boundary_guard_validation_dev.jsonl',
             'boundary_guard_validation_per_case.jsonl',
             'boundary_guard_validation_decisions.jsonl')
PRIOR_CONTROL={'cases':320,'exact_prompts':117,'correct':8689,'expected':8981,
               'missing_objects':292,'false_positive_objects':231}
PRIOR_GUARD={'cases':320,'exact_prompts':125,'correct':8703,'expected':8981,
             'missing_objects':278,'false_positive_objects':217}

# Sections are numbered in their original synthetic generator order. OOD order
# deliberately places payoff/benefit evidence ahead of the choice list.
SECTION_NAMES={
    'encounter':('CHOICE REGISTER','COUNTERPART ACTIONS','OBSERVATIONS',
                 'PAIRING RETURNS','DECISION'),
    'allocation':('CHOICE REGISTER','BENEFIT-COST LEDGER','DECISION'),
}
SECTION_ORDERS={
    'encounter':{'evidence_first':(3,2,1,0,4),
                 'ledger_grid':(2,3,1,0,4)},
    'allocation':{'evidence_first':(1,0,2),
                  'ledger_grid':(1,0,2)},
}


def _pieces(prompt, expected):
    """Extract complete source sentences without changing mention text."""
    cut=[0]
    for match in re.finditer(r'\. (?=[A-Z])',prompt):
        cut.extend((match.start()+1, match.end()))
    cut.append(len(prompt))
    result=[]
    for i in range(0,len(cut)-1,2):
        start=cut[i]
        end=cut[i+1]
        result.append((start,end,prompt[start:end]))
    if (len(result)!=expected or any(not t.endswith('.') or not t
                                     for _,_,t in result)):
        raise ValueError('GAMER_G1_0022_SOURCE_SENTENCES_UNEXPECTED')
    if ''.join(t if index==0 else ' '+t for index,(_,_,t) in enumerate(result))!=prompt:
        raise ValueError('GAMER_G1_0022_SENTENCE_RECONSTRUCTION_FAILED')
    return result


def _reshape_clause(text, layout):
    """Render separators as table entries; retain boundary maps for gold spans.

    Source annotation words contain neither '; ' nor ', '. Replacement only
    happens outside mentions and all source mention character slices survive.
    """
    output=[]
    boundary={0:0}
    source=0
    cursor=0
    while source<len(text):
        if text.startswith('; ',source):
            replacement=('\n  • ' if layout=='ledger_grid' else '\n  - ')
            source+=2
        elif text.startswith(', ',source):
            replacement=(' / ' if layout=='ledger_grid' else ' | ')
            source+=2
        else:
            replacement=text[source]
            source+=1
        output.append(replacement)
        cursor+=len(replacement)
        boundary[source]=cursor
    return ''.join(output),boundary


def structural_ood_row(row, *, layout):
    """Reorder source segments, transform separators, remap all span offsets.

    The graph topology, values, decision objective and teacher answer remain
    *identical* to the author-labeled base row. No gold is consulted at inference.
    """
    from tools.nanojev_gamer_v1_graph_contract import validate_graph, execute_graph
    if layout not in LAYOUTS or row.get('family') not in FAMILIES or row.get('split')!='dev':
        raise ValueError('GAMER_G1_0022_INVALID_SOURCE_OR_LAYOUT')
    original=row['prompt']
    family=row['family']
    pieces=_pieces(original,len(SECTION_NAMES[family]))
    order=SECTION_ORDERS[family][layout]
    output=[('EVIDENCE DOSSIER\n' if layout=='evidence_first' else 'DATA LEDGER\n')]
    cursor=len(output[0]);boundary={}
    for idx in order:
        start,end,section=pieces[idx]
        heading=SECTION_NAMES[family][idx]
        prefix=f'[{heading}]\n' if layout=='evidence_first' else f'{heading} ::\n'
        output.append(prefix);cursor+=len(prefix)
        reshaped,mapping=_reshape_clause(section,layout)
        for old,shifted in mapping.items():
            boundary[start+old]=cursor+shifted
        output.append(reshaped);cursor+=len(reshaped)
        output.append('\n\n');cursor+=2
    text=''.join(output).rstrip()+'\n'
    updated=deepcopy(row)
    updated['prompt']=text
    updated['split']='ood_dev'
    updated['template']='structural-ood-'+layout
    updated['surface_shift']=layout
    updated['source_prompt_sha256']=hashlib.sha256(original.encode('utf-8')).hexdigest()
    for node in updated['graph']['nodes']:
        if 'span' not in node:continue
        start,end=node['span']
        if start not in boundary or end not in boundary:
            raise ValueError('GAMER_G1_0022_MENTION_CROSSES_SECTION')
        node['span']=[boundary[start],boundary[end]]
        if text[boundary[start]:boundary[end]]!=original[start:end]:
            raise ValueError('GAMER_G1_0022_MENTION_CONTENT_CHANGED')
    updated['object_spans']=[{'kind':node['kind'],'start':node['span'][0],
                              'end':node['span'][1]}
                             for node in updated['graph']['nodes'] if 'span' in node]
    # Fail closed if remapped graph ceases to have the same objective/solution.
    validate_graph(updated['graph'],complete=True)
    if execute_graph(updated['graph'])['decisions']!=row['expected_decisions']:
        raise ValueError('GAMER_G1_0022_SEMANTIC_DRIFT')
    original_identity=deepcopy(row['graph'])
    new_identity=deepcopy(updated['graph'])
    for graph in (original_identity,new_identity):
        for node in graph['nodes']:
            node.pop('span',None)
    # Compare graph without the positional annotation metadata.
    if original_identity!=new_identity:
        raise ValueError('GAMER_G1_0022_GRAPH_TOPOLOGY_DRIFT')
    return updated


def ood_rows(dataset, first_fresh_rows, prior_validation_rows,
             width_rows, guard_rows, *, seed=SEED, cases=CASES):
    """Fixed 4-cell shift: family x layout. No challenge/test source opened."""
    from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
    if type(seed) is not int or seed!=SEED or type(cases) is not int or cases!=CASES:
        raise ValueError('GAMER_G1_0022_GENERATION_PARAMETERS_LOCKED')
    seen=_original_prompts(dataset)
    for label,source,count in (('0011',first_fresh_rows,80),
                               ('0014',prior_validation_rows,CASES),
                               ('0018',width_rows,CASES),
                               ('0021',guard_rows,CASES)):
        if len(source)!=count:
            raise ValueError('GAMER_G1_0022_PRIOR_COUNT:'+label)
        for row in source:
            if (row.get('split') not in ('dev','new_dev') or
                row.get('family') not in FAMILIES or
                not isinstance(row.get('prompt'),str) or
                row['prompt'] in seen):
                raise ValueError('GAMER_G1_0022_PRIOR_OVERLAP_OR_STRUCTURE:'+label)
            seen.add(row['prompt'])
    rng=random.Random(seed)
    records=[]; shifts=Counter();sources=set()
    for index in range(cases):
        family=FAMILIES[index%2]
        layout=LAYOUTS[(index//2)%2]
        generator=encounter if family=='encounter' else allocation
        for _ in range(250):
            source=generator(rng,'dev',index)
            if source['prompt'] not in seen and source['prompt'] not in sources:
                transformed=structural_ood_row(source,layout=layout)
                if transformed['prompt'] not in seen:
                    break
        else:
            raise ValueError('GAMER_G1_0022_NEW_PROMPTS_EXHAUSTED')
        sources.add(source['prompt']);seen.add(transformed['prompt'])
        records.append(transformed);shifts[(family,layout)]+=1
    if (len(records)!=CASES or len({r['prompt'] for r in records})!=CASES or
        any(shifts[(family,layout)]!=CASES//4 for family in FAMILIES for layout in LAYOUTS)):
        raise ValueError('GAMER_G1_0022_SHIFT_BALANCE_INVALID')
    return records


def _load_0021_reference(path,info,selective_hashes,old_rows):
    """Freeze validated dev leader, verify physical & content lineage."""
    path=Path(path)
    paths={name:path/name for name in PRIOR_FILES}
    hashes={name:_file_sha(p) for name,p in paths.items()}
    report=json.loads(paths[PRIOR_FILES[0]].read_text(encoding='utf-8'))
    rows=_read_jsonl(paths[PRIOR_FILES[1]])
    cases=_read_jsonl(paths[PRIOR_FILES[2]])
    decisions=_read_jsonl(paths[PRIOR_FILES[3]])
    checks={
       'schema':(report.get('schema'),PRIOR_SCHEMA),
       'status':(report.get('status'),'FRESH_WITHIN_GENERATOR_DEV_VALIDATION_NOT_CERTIFICATION'),
       'seed':(report.get('seed'),PRIOR_SEED),
       'cases':(report.get('cases'),CASES),
       'families':(report.get('families'),{'encounter':160,'allocation':160}),
       'candidate_frozen_for_validation_only':(report.get('candidate_frozen_for_validation_only'),GUARD),
       'source_selective_report_sha256':(report.get('source_selective_report_sha256'),
                                        selective_hashes[SELECTIVE_FILES[0]]),
       'source_adapter_sha256':(report.get('source_adapter_sha256'),info['adapter_sha']),
       'checkpoint_file_sha256':(report.get('checkpoint_file_sha256'),
                                 info['checkpoint_files'][WINNER_HEAD]),
       'excluded_0011_dataset_sha256':(report.get('excluded_0011_dataset_sha256'),info['fresh_dataset_sha']),
       'excluded_0014_dataset_sha256':(report.get('excluded_0014_dataset_sha256'),
                                    info['validation_report']['validation_dataset_sha256']),
       'new_dev_dataset_sha256':(report.get('new_dev_dataset_sha256'),
                                hashlib.sha256(_jsonl_bytes(rows)).hexdigest()),
       'predeclared_candidate_settings':(report.get('configurations_predeclared'),[
            {'key':CONTROL,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
             'action_width_power':1.0,'action_per_span_penalty':0.0},
            {'key':GUARD,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
             'action_width_power_for_proposal':1.25,
             'min_score_retention':MIN_SCORE_RETENTION,'boundary_guard':True}]),
       'passes_predeclared_four_metric_guard':(report.get('passes_predeclared_four_metric_guard'),True),
       'fresh_advantage_checks':(report.get('fresh_advantage_checks'),{
           'higher_object_f1':True,'more_exact_prompts':True,
           'no_more_false_positives':True,'no_more_missing_objects':True}),
    }
    for name,expect in ((CONTROL,PRIOR_CONTROL),(GUARD,PRIOR_GUARD)):
        value=report.get('metrics',{}).get(name,{})
        for field,expected in expect.items():
            checks[name+'.'+field]=(value.get(field),expected)
    paired=report.get('paired_comparison',{})
    for name,value in {'a_only_exact':0,'b_only_exact':8,
                       'exact_discordant_pairs':8,
                       'b_newly_recovered_gold':16,
                       'b_previously_correct_gold_lost':2,
                       'b_old_false_positives_removed':16,
                       'b_new_false_positives':2}.items():
        checks['paired.'+name]=(paired.get(name),value)
    for field,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:'+field)
    for flag in ('heads_frozen','candidate_eligibility_unchanged',
                 'one_feature_capture_per_prompt','no_training',
                 'production_decoder_unchanged'):
        if report.get(flag) is not True:
            raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:'+flag)
    for flag in ('challenge_or_test_split_used','certification_authorized',
                 'production_promotion','g2_unlocked'):
        if report.get(flag) is not False:
            raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:'+flag)
    if len(rows)!=CASES or len(cases)!=CASES:
        raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:row_count')
    if len(decisions)<1 or set(report.get('metrics',{}))!={CONTROL,GUARD}:
        raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:decisions_or_arms')
    # The summary and every saved per-prompt prediction must agree. Report
    # metadata alone is insufficient to freeze a result used for OOD selection.
    totals={key:Counter() for key in (CONTROL,GUARD)}
    for i,(row,case) in enumerate(zip(rows,cases),1):
        if (not isinstance(row.get('object_spans'),list) or
            case.get('gold_count')!=len(row['object_spans']) or
            set(case.get('configurations',{}))!={CONTROL,GUARD}):
            raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:case_accounting_'+str(i))
        for name in (CONTROL,GUARD):
            c=case['configurations'][name]
            if (any(type(c.get(field)) is not int or c[field]<0 for field in
                    ('correct','missing','false_positives')) or
                    type(c.get('exact')) is not bool or
                    c['correct']+c['missing']!=case['gold_count'] or
                    c['exact']!=(c['missing']==0 and c['false_positives']==0)):
                raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:case_accounting_'+str(i))
            totals[name].update(c)
            totals[name]['exact_prompts']+=int(c['exact'])
    for name in (CONTROL,GUARD):
        ref=report['metrics'][name]
        for key,source in (('correct','correct'),('missing_objects','missing'),
                           ('false_positive_objects','false_positives'),
                           ('exact_prompts','exact_prompts')):
            if ref.get(key)!=totals[name][source]:
                raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:totals.'+name+'.'+key)
    if (paired.get('b_newly_recovered_gold',-1)-paired.get('b_previously_correct_gold_lost',-1)
            !=totals[GUARD]['correct']-totals[CONTROL]['correct'] or
        paired.get('b_new_false_positives',-1)-paired.get('b_old_false_positives_removed',-1)
            !=totals[GUARD]['false_positives']-totals[CONTROL]['false_positives']):
        raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:paired_accounting')
    used=set(r['prompt'] for r in old_rows)
    found=set()
    for i,(row,case) in enumerate(zip(rows,cases),1):
        sha=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
        if (row.get('split')!='dev' or row.get('family')!=FAMILIES[(i-1)%2]
                or row['prompt'] in used or row['prompt'] in found
                or case.get('prompt_sha256')!=sha or case.get('index')!=i
                or case.get('family')!=row['family']):
            raise ValueError('GAMER_G1_0022_INVALID_0021_REFERENCE:case_'+str(i))
        used.add(row['prompt']);found.add(row['prompt'])
    return report,rows,paths,hashes


def run(*,root,transfer_report,fresh_run,null_run,validation_run,action_run,
        ranking_run,width_run,conflict_run,selective_run,guard_run,output,
        device='cuda',max_length=2048):
    """Evaluate the same frozen model once per genuinely restructured prompt."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0022_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0022_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,action_paths,action_hashes=_load_action_reference(action_run,info)
    _,ranking_paths,ranking_hashes=_load_ranking_reference(ranking_run,info,action_hashes)
    width_rows=prior_width_rows(info['dataset'],info['fresh_rows'],info['validation_rows'])
    width_report,_,width_paths,width_hashes=_load_width_reference(
        width_run,width_rows,info,ranking_hashes,action_hashes)
    _,_,conflict_paths,conflict_hashes=_read_conflict_reference(
        conflict_run,width_report,width_hashes,width_rows)
    _,selective_paths,selective_hashes=_load_selective_reference(
        selective_run,info,width_report,width_hashes,conflict_hashes,width_rows)
    guard_report,guard_rows,guard_paths,guard_hashes=_load_0021_reference(
        guard_run,info,selective_hashes,width_rows)
    rows=ood_rows(info['dataset'],info['fresh_rows'],info['validation_rows'],
                  width_rows,guard_rows)
    dataset_bytes=_jsonl_bytes(rows)
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0022_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0022_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records={CONTROL:[],GUARD:[]};per_case=[];decisions=[]
    by_shift={k:{CONTROL:[],GUARD:[]} for k in LAYOUTS}
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            captured=cur.capture(model,tok,row,device,max_length)
            prepared=captured['prepared']
            pred=head(captured['hidden'].to(device=device,dtype=torch.float32),
                      eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=pred['span_logits'];valid=pred['span_valid']
            shifted=logits.detach().float().clone();shifted[...,0]-=SHIFT
            proposals=_candidate_scores(shifted,valid,confidence=.50)
            control=decode_action_ranked(proposals,action_power=1.0,action_penalty=0.0)
            if control!=_decode_shift(logits,valid,SHIFT):
                raise ValueError('GAMER_G1_0022_CONTROL_DECODER_DIVERGED')
            wide=decode_action_ranked(proposals,action_power=1.25,action_penalty=0.0)
            guarded,events=selective_decode(control,wide,proposals,
                         min_score_retention=MIN_SCORE_RETENTION,boundary_guard=GUARD_ENABLED)
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            guesses={CONTROL:_char_set(control,prepared),GUARD:_char_set(guarded,prepared)}
            for key,guess in guesses.items():
                item=(guess,gold)
                records[key].append(item)
                by_shift[row['surface_shift']][key].append(item)
            per_case.append({'index':index,'family':row['family'],
                  'surface_shift':row['surface_shift'],
                  'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                  'gold_count':len(gold),'configurations':{key:{
                      'correct':len(guess&gold),'missing':len(gold-guess),
                      'false_positives':len(guess-gold),'exact':guess==gold}
                    for key,guess in guesses.items()}})
            for event in events:
                decisions.append({'index':index,'surface_shift':row['surface_shift'],**event})
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_STRUCTURAL_OOD {index}/{CASES}',flush=True)
    metrics={key:_summarize(records[key],rows) for key in (CONTROL,GUARD)}
    paired=_paired(records[CONTROL],records[GUARD])
    shift_metrics={shift:{key:_summarize(by_shift[shift][key],
                  [row for row in rows if row['surface_shift']==shift])
                  for key in (CONTROL,GUARD)} for shift in LAYOUTS}
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],
                     action_paths,action_hashes,ranking_paths,ranking_hashes)
    for paths,hashes,label in ((width_paths,width_hashes,'WIDTH'),
                                (conflict_paths,conflict_hashes,'CONFLICT'),
                                (selective_paths,selective_hashes,'SELECTIVE'),
                                (guard_paths,guard_hashes,'GUARD')):
        _check_files_unchanged(paths,hashes,label)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
          info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
          info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0022_CHECKPOINT_CHANGED_DURING_RUN')
    base=metrics[CONTROL];candidate=metrics[GUARD]
    gates={
      'exact_prompts_not_worse':candidate['exact_prompts']>=base['exact_prompts'],
      'object_f1_not_worse':candidate['f1']>=base['f1'],
      'missing_objects_not_more':candidate['missing_objects']<=base['missing_objects'],
      'false_positives_not_more':candidate['false_positive_objects']<=base['false_positive_objects'],
    }
    report={
      'schema':SCHEMA,'status':'STRUCTURAL_OOD_STRESS_ONLY_NOT_SEALED_CERTIFICATION',
      'seed':SEED,'cases':CASES,'families':{'encounter':160,'allocation':160},
      'layouts':list(LAYOUTS),'shift_distribution':{
          f'{f}.{s}':sum(r['family']==f and r['surface_shift']==s for r in rows)
          for f in FAMILIES for s in LAYOUTS},
      'shift_definition':('Author-annotated synthetic dev prompts restructured by moving '
         'the payoff/benefit evidence ahead of choices, replacing list separators '
         'with ledger rows, and adding explicit record headers. Original graph '
         'semantics, decision answers, and marked entity substrings are invariant.'),
      'split':'structural_ood_dev','source_split':'dev',
      'metrics':metrics,'metrics_by_surface_shift':shift_metrics,
      'paired_comparison':paired,
      'predeclared_nonregression_checks':gates,'passes_all_nonregression_checks':all(gates.values()),
      'frozen_development_leader':GUARD,
      'configurations_predeclared':[
          {'key':CONTROL,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
           'action_width_power':1.0,'action_per_span_penalty':0.0},
          {'key':GUARD,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
           'action_width_power_for_proposal':1.25,
           'min_score_retention':MIN_SCORE_RETENTION,'boundary_guard':GUARD_ENABLED}],
      'ood_dataset_sha256':hashlib.sha256(dataset_bytes).hexdigest(),
      'source_0021_report_sha256':guard_hashes[PRIOR_FILES[0]],
      'source_0021_dataset_sha256':guard_report['new_dev_dataset_sha256'],
      'source_selective_report_sha256':selective_hashes[SELECTIVE_FILES[0]],
      'source_adapter_sha256':info['adapter_sha'],
      'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
      'heads_frozen':True,'candidate_eligibility_unchanged':True,
      'one_feature_capture_per_prompt':True,'no_training':True,
      'production_decoder_unchanged':True,'production_promotion':False,
      'challenge_or_test_split_used':False,'sealed_test_used':False,
      'certification_authorized':False,'g2_unlocked':False,
      'interpretation':('This evaluates a fixed development leader against a frozen control '
        'under an intentionally altered synthetic surface grammar and layout. '
        'It does not show transfer beyond the same two decision families, '
        'does not use reserved challenge/test splits, and does not certify G1 or unlock G2.'),
    }
    freeze={
       'schema':'nanojev.gamer.graph.g1.frozen-development-leader.v1',
       'status':'FROZEN_DEVELOPMENT_ONLY_NOT_PRODUCTION',
       'key':GUARD,'checkpoint':WINNER_HEAD,
       'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
       'source_adapter_sha256':info['adapter_sha'],
       'null_logit_shift':SHIFT,'action_width_power_for_proposal':1.25,
       'min_score_retention':MIN_SCORE_RETENTION,'boundary_guard':True,
       'source_0021_report_sha256':guard_hashes[PRIOR_FILES[0]],
       'training_authorized':False,'g2_unlocked':False,
       'certification_authorized':False,'production_promotion':False,
    }
    output.mkdir(parents=True)
    (output/'frozen_development_leader.json').write_bytes((
      json.dumps(freeze,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
    (output/'structural_ood_dataset.jsonl').write_bytes(dataset_bytes)
    (output/'structural_ood_per_case.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'structural_ood_conflict_decisions.jsonl').write_bytes(_jsonl_bytes(decisions))
    (output/'structural_ood_report.json').write_bytes((
      json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
    return report
