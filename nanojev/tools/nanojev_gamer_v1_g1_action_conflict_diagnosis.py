"""G1-0019: forensic replay of G1-0018's 13 gains and 21 regressions.

Read-only, gold-informed *after* frozen inference. No candidate filtering,
training, score tuning, inference change, promotion or G2 access. The only
prompts scored are the already-inspected 0018 within-generator dev examples.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_action_width_validation import (
    SCHEMA as WIDTH_SCHEMA, CANDIDATES, CONTROL, CHALLENGER, SHIFT,
    _load_prior_inputs, _load_action_reference, _load_ranking_reference,
    _verify_no_drift, fresh_rows,
)
from tools.nanojev_gamer_v1_g1_action_errors import WINNER_HEAD, WINNER_KEY
from tools.nanojev_gamer_v1_g1_action_ranking import (
    _candidate_scores, _file_sha, _span_score, decode_action_ranked,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, FAMILIES, _jsonl_bytes, _summarize, _paired,
)
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.frozen-action-conflict-diagnosis.v1'
WIDTH_FILES = (
    'action_width_validation_report.json',
    'action_width_validation_dev.jsonl',
    'action_width_validation_per_case.jsonl',
)
# Pinned *before* replay to reject a different validation or accidental drift.
PINNED = {
    CONTROL: {'exact_prompts':122,'correct':8540,'expected':8820,
              'missing_objects':280,'false_positive_objects':225,'predicted':8765},
    CHALLENGER: {'exact_prompts':128,'correct':8532,'expected':8820,
                 'missing_objects':288,'false_positive_objects':221,'predicted':8753},
}
PINNED_PAIRED = {
    'a_only_exact':1,'b_only_exact':7,'b_newly_recovered_gold':13,
    'b_previously_correct_gold_lost':21,'b_new_false_positives':14,
    'b_old_false_positives_removed':18,'exact_discordant_pairs':8,
    'mcnemar_exact_two_sided_p':0.0703125,
}
DIRECTIONS = ('recovered_gold','lost_gold','new_false_positive','removed_false_positive')


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def _require_equal(label, actual, expected):
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError('GAMER_G1_0019_INVALID_WIDTH_REFERENCE:'+label)


def _load_width_reference(path, rows, info, ranking_hashes, action_hashes):
    """Validate complete physical 0018 files, not just its printed totals."""
    path = Path(path)
    files = {name:path/name for name in WIDTH_FILES}
    hashes = {name:_file_sha(p) for name,p in files.items()}
    report = json.loads(files[WIDTH_FILES[0]].read_text(encoding='utf-8'))
    actual_rows = files[WIDTH_FILES[1]].read_bytes()
    if actual_rows != _jsonl_bytes(rows):
        raise ValueError('GAMER_G1_0019_WIDTH_DATASET_CHANGED')
    cases = _read_jsonl(files[WIDTH_FILES[2]])
    expected_config = [
        {'key':name,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
         'action_width_power':power,'action_per_span_penalty':penalty}
        for name,power,penalty in CANDIDATES
    ]
    checks = {
        'schema':(report.get('schema'),WIDTH_SCHEMA),
        'status':(report.get('status'),'FRESH_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY'),
        'seed':(report.get('seed'),281803),
        'split':(report.get('split'),'new_dev'),
        'cases':(report.get('cases'),CASES),
        'families':(report.get('families'),{'encounter':160,'allocation':160}),
        'configurations_predeclared':(report.get('configurations_predeclared'),expected_config),
        'validation_dataset_sha256':(report.get('validation_dataset_sha256'),
             hashlib.sha256(actual_rows).hexdigest()),
        'old_validation_dataset_sha256':(report.get('old_validation_dataset_sha256'),
             info['validation_report']['validation_dataset_sha256']),
        'old_fresh_dataset_sha256':(report.get('old_fresh_dataset_sha256'),info['fresh_dataset_sha']),
        'previous_action_ranking_report_sha256':(report.get('previous_action_ranking_report_sha256'),
             ranking_hashes['action_ranking_report.json']),
        'previous_action_error_report_sha256':(report.get('previous_action_error_report_sha256'),
             action_hashes['action_error_report.json']),
        'source_adapter_sha256':(report.get('source_adapter_sha256'),info['adapter_sha']),
        'head_sha256':(report.get('head_sha256'),info['entries'][WINNER_HEAD]['manifest']['head_sha256']),
        'checkpoint_file_sha256':(report.get('checkpoint_file_sha256'),
             info['checkpoint_files'][WINNER_HEAD]),
        'fresh_ranking_not_certification':(report.get('fresh_ranking_not_certification'),
             [CHALLENGER,CONTROL]),
    }
    for key, (actual, expected) in checks.items():
        _require_equal(key,actual,expected)
    for key in ('one_feature_capture_per_prompt','heads_frozen',
                'candidate_eligibility_unchanged','production_decoder_unchanged','no_training'):
        _require_equal(key,report.get(key),True)
    for key in ('challenge_or_test_split_used','certification_authorized',
                'production_promotion','g2_unlocked'):
        _require_equal(key,report.get(key),False)
    if not isinstance(report.get('metrics'),dict) or not isinstance(report.get('paired_comparison'),dict):
        raise ValueError('GAMER_G1_0019_INVALID_WIDTH_REFERENCE:report-shape')
    _require_equal('metrics_keys',set(report['metrics']),set(PINNED))
    for name, expected in PINNED.items():
        actual = report['metrics'][name]
        for key, count in expected.items():
            _require_equal('metrics.'+name+'.'+key,actual.get(key),count)
        _require_equal('metrics.'+name+'.missing_by_kind.total',
                       sum(actual.get('missing_by_kind',{}).values()),expected['missing_objects'])
        _require_equal('metrics.'+name+'.false_positives_by_kind.total',
                       sum(actual.get('false_positives_by_kind',{}).values()),expected['false_positive_objects'])
    for key, count in PINNED_PAIRED.items():
        _require_equal('paired_comparison.'+key,report.get('paired_comparison',{}).get(key),count)
    _require_equal('per_case.count',len(cases),CASES)
    totals={name:Counter() for name in PINNED}
    discordant=Counter()
    for index,(row,case) in enumerate(zip(rows,cases),1):
        expected_sha=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
        for key, actual, expected in (
            ('index',case.get('index'),index),('family',case.get('family'),row['family']),
            ('prompt_sha256',case.get('prompt_sha256'),expected_sha),
            ('gold_count',case.get('gold_count'),len(row['object_spans'])),
            ('configurations',set(case.get('configurations',{})),set(PINNED))):
            _require_equal('per_case.'+key,actual,expected)
        for name in PINNED:
            part=case['configurations'][name]
            for key in ('correct','missing','false_positives'):
                if type(part.get(key)) is not int or part[key] < 0:
                    raise ValueError('GAMER_G1_0019_INVALID_WIDTH_CASE_COUNT:'+name+'.'+key)
            _require_equal('per_case.accounting',part['correct']+part['missing'],case['gold_count'])
            _require_equal('per_case.exact',part.get('exact'),
                           part['missing']==0 and part['false_positives']==0)
            totals[name].update(part)
            totals[name]['exact_prompts']+=int(part['exact'])
        a=case['configurations'][CONTROL]['exact']
        b=case['configurations'][CHALLENGER]['exact']
        discordant['a_only_exact']+=int(a and not b)
        discordant['b_only_exact']+=int(b and not a)
    for name in PINNED:
        for key in ('correct','missing','false_positives','exact_prompts'):
            metric_key = {'missing':'missing_objects','false_positives':'false_positive_objects'}.get(key,key)
            _require_equal('per_case.totals.'+name+'.'+key,totals[name][key],PINNED[name][metric_key])
    for key in ('a_only_exact','b_only_exact'):
        _require_equal('per_case.paired.'+key,discordant[key],PINNED_PAIRED[key])
    return report,cases,files,hashes


def _overlap(left,right):
    return left[0] < right[1] and right[0] < left[1]


def _bucket(width):
    return '1' if width == 1 else '2' if width == 2 else '3-4' if width <= 4 else '5-8' if width <= 8 else '9+'


def _candidate_catalog(by_end, prepared):
    """Immutable neural proposals; scores displayed for both objectives."""
    result = {}
    for end,items in enumerate(by_end):
        for first,offset,klass,margin in items:
            span={'kind':SPAN_KINDS[klass-1],'token_first':first,'token_last':end-1}
            bounds=token_span_char_bounds(span,prepared)
            if bounds is None:
                # Same mapping policy as _char_set: proposals outside the
                # prompt are never counted as observable predictions.
                continue
            key=(bounds[0],bounds[1],span['kind'])
            width=offset+1
            data={
                'kind':span['kind'],'span':[bounds[0],bounds[1]],
                'token_first':first,'token_last':end-1,'token_width':width,
                'margin':round(margin,6),
                'control_score':round(_span_score(margin,width,klass,1.0,0.),6),
                'challenger_score':round(_span_score(margin,width,klass,1.25,0.),6),
            }
            # Distinct token spans may normalize to the same character span.
            # Keep them all, including their different margins and widths.
            result.setdefault(key,[]).append(data)
    return result


def _selected_catalog(decoded, prepared, catalog):
    selected={}
    for name,spans in decoded.items():
        choices={}
        for span in spans:
            bounds=token_span_char_bounds(span,prepared)
            if bounds is None:
                continue
            key=(*bounds,span['kind'])
            matches=[item for item in catalog.get(key,[])
                     if item['token_first']==span['token_first']
                     and item['token_last']==span['token_last']]
            if len(matches)!=1 or key in choices:
                raise ValueError('GAMER_G1_0019_UNRESOLVED_SELECTED_SPAN')
            choices[key]=matches[0]
        selected[name]=choices
    return selected


def _changed_objects(index,row, gold, control, challenger,catalog,selected=None):
    """Gold may annotate comparisons only AFTER both decodes have finished."""
    parts={
        'recovered_gold':(challenger & gold) - control,
        'lost_gold':(control & gold) - challenger,
        'new_false_positive':(challenger-gold)-control,
        'removed_false_positive':(control-gold)-challenger,
    }
    result=[]
    if selected is None:
        # Supports pure unit fixtures using one proposal per character span.
        selected={CONTROL:{key:(items[0] if isinstance(items,list) else items)
                           for key,items in catalog.items() if key in control},
                  CHALLENGER:{key:(items[0] if isinstance(items,list) else items)
                              for key,items in catalog.items() if key in challenger}}
    all_entries={key:(items if isinstance(items,list) else [items])
                 for key,items in catalog.items()}
    for direction in DIRECTIONS:
        for obj in sorted(parts[direction]):
            if obj not in catalog:
                raise ValueError('GAMER_G1_0019_CHANGED_OBJECT_NOT_ELIGIBLE')
            other=control if direction in ('recovered_gold','new_false_positive') else challenger
            other_name=CONTROL if direction in ('recovered_gold','new_false_positive') else CHALLENGER
            own_name=CHALLENGER if other_name==CONTROL else CONTROL
            if obj not in selected[own_name]:
                raise ValueError('GAMER_G1_0019_CHANGED_SELECTED_SPAN_MISSING')
            own=selected[own_name][obj]
            affected=[]
            for competing in sorted(other):
                if _overlap(obj,competing):
                    if competing not in selected[other_name]:
                        raise ValueError('GAMER_G1_0019_COMPETING_OBJECT_NOT_ELIGIBLE')
                    affected.append({**selected[other_name][competing],
                                     'surface':row['prompt'][competing[0]:competing[1]],
                                     'gold_match':competing in gold})
            eligible=[{**data,'surface':row['prompt'][cand[0]:cand[1]],
                       'gold_match':cand in gold,
                       'selected_control':selected[CONTROL].get(cand)==data,
                       'selected_challenger':selected[CHALLENGER].get(cand)==data}
                      for cand,items in sorted(all_entries.items()) if _overlap(obj,cand)
                      for data in items]
            other_widths=[x['token_width'] for x in affected]
            if not other_widths:
                relative='no_overlapping_selected'
            elif own['token_width']>max(other_widths):
                relative='longer_than_every_competitor'
            elif own['token_width']<min(other_widths):
                relative='shorter_than_every_competitor'
            else:
                relative='mixed_or_equal_width'
            result.append({
                'index':index,'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'direction':direction,'kind':obj[2], 'span':[obj[0],obj[1]],
                'surface':row['prompt'][obj[0]:obj[1]],
                'token_width':own['token_width'],
                'token_width_bucket':_bucket(own['token_width']),
                'candidate':own,
                'candidate_score_change':round(own['challenger_score']-own['control_score'],6),
                # Local comparisons, not substitutes for the global interval DP.
                'local_control_score_gap':round(own['control_score']-
                            sum(x['control_score'] for x in affected),6),
                'local_challenger_score_gap':round(own['challenger_score']-
                            sum(x['challenger_score'] for x in affected),6),
                'relative_token_width':relative,
                'overlapping_other_selected':affected,
                'overlapping_eligible_candidates':eligible,
                'other_selected_action_count':sum(x['kind']=='action' for x in affected),
                'other_selected_gold_count':sum(x['gold_match'] for x in affected),
                'other_selected_non_gold_count':sum(not x['gold_match'] for x in affected),
            })
    return result


def _summarize_changes(objects):
    directions=Counter(o['direction'] for o in objects)
    report={}
    for direction in DIRECTIONS:
        subset=[o for o in objects if o['direction']==direction]
        report[direction]={
            'count':len(subset),
            'by_kind':dict(sorted(Counter(x['kind'] for x in subset).items())),
            'by_family':dict(sorted(Counter(x['family'] for x in subset).items())),
            'by_token_width':dict(sorted(Counter(x['token_width_bucket'] for x in subset).items())),
            'by_relative_token_width':dict(sorted(Counter(x['relative_token_width'] for x in subset).items())),
            'by_competitor_kind':dict(sorted(Counter(other['kind'] for x in subset
                for other in x['overlapping_other_selected']).items())),
            'overlaps_other_selected':sum(bool(x['overlapping_other_selected']) for x in subset),
            'overlaps_other_selected_false_positive':sum(x['other_selected_non_gold_count']>0 for x in subset),
            'overlaps_other_selected_correct_gold':sum(x['other_selected_gold_count']>0 for x in subset),
            'other_selected_multiple':sum(len(x['overlapping_other_selected'])>1 for x in subset),
        }
    expected={'recovered_gold':13,'lost_gold':21,
              'new_false_positive':14,'removed_false_positive':18}
    for direction,count in expected.items():
        if directions[direction]!=count:
            raise ValueError('GAMER_G1_0019_REPLAY_CHANGE_DIVERGED:'+direction)
    return report


def run(*,root,transfer_report,fresh_run,null_run,validation_run,action_run,
        ranking_run,width_run,output,device='cuda',max_length=2048):
    """Replay the already-observed 0018 cases, with strict lineage and totals."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0019_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0019_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,action_paths,action_hashes=_load_action_reference(action_run,info)
    _,ranking_paths,ranking_hashes=_load_ranking_reference(ranking_run,info,action_hashes)
    rows=fresh_rows(info['dataset'],info['fresh_rows'],info['validation_rows'])
    width_report,width_cases,width_paths,width_hashes=_load_width_reference(
        width_run,rows,info,ranking_hashes,action_hashes)
    entry=info['entries'][WINNER_HEAD]
    manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter']) != info['adapter_sha']:
        raise ValueError('GAMER_G1_0019_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0019_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records={name:[] for name,_,_ in CANDIDATES}
    changed=[]
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            captured=cur.capture(model,tok,row,device,max_length)
            prepared=captured['prepared']
            prediction=head(captured['hidden'].to(device=device,dtype=torch.float32),
                            eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=prediction['span_logits'];valid=prediction['span_valid']
            shifted=logits.detach().float().clone(); shifted[...,0]-=SHIFT
            candidates=_candidate_scores(shifted,valid,confidence=.50)
            decoded={name:decode_action_ranked(candidates,action_power=power,
                                                 action_penalty=penalty)
                     for name,power,penalty in CANDIDATES}
            if decoded[CONTROL]!=_decode_shift(logits,valid,SHIFT):
                raise ValueError('GAMER_G1_0019_CONTROL_DECODER_DIVERGED')
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            guesses={name:_char_set(spans,prepared) for name,spans in decoded.items()}
            for name,guess in guesses.items():
                records[name].append((guess,gold))
            expected=width_cases[index-1]['configurations']
            for name,guess in guesses.items():
                actual={'correct':len(guess&gold),'missing':len(gold-guess),
                        'false_positives':len(guess-gold),'exact':guess==gold}
                if actual!=expected[name]:
                    raise ValueError('GAMER_G1_0019_WIDTH_CASE_DIVERGED:'+str(index)+':'+name)
            if guesses[CONTROL]!=guesses[CHALLENGER]:
                catalog=_candidate_catalog(candidates,prepared)
                selected=_selected_catalog(decoded,prepared,catalog)
                if any(set(selected[name])!=guesses[name] for name in selected):
                    raise ValueError('GAMER_G1_0019_SELECTED_CHAR_SPAN_DIVERGED')
                changed.extend(_changed_objects(index,row,gold,
                    guesses[CONTROL],guesses[CHALLENGER],catalog,selected))
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_ACTION_CONFLICT_DIAGNOSIS {index}/{CASES}',flush=True)
    metrics={name:_summarize(records[name],rows) for name,_,_ in CANDIDATES}
    for name in PINNED:
        if metrics[name]!=width_report['metrics'][name]:
            raise ValueError('GAMER_G1_0019_WIDTH_METRICS_DIVERGED:'+name)
    paired=_paired(records[CONTROL],records[CHALLENGER])
    if paired!=width_report['paired_comparison']:
        raise ValueError('GAMER_G1_0019_WIDTH_PAIRED_DIVERGED')
    summary=_summarize_changes(changed)
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],
                     action_paths,action_hashes,ranking_paths,ranking_hashes)
    for name,path in width_paths.items():
        if _file_sha(path)!=width_hashes[name]:
            raise ValueError('GAMER_G1_0019_WIDTH_INPUT_CHANGED_DURING_RUN:'+name)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json') !=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE) !=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0019_CHECKPOINT_CHANGED_DURING_RUN')
    report={
        'schema':SCHEMA,'status':'FROZEN_DEV_DIAGNOSIS_ONLY_NOT_CERTIFICATION',
        'cases':CASES,'families':{'encounter':160,'allocation':160},
        'frozen_reference':'null_focused_4_control',
        'experimental_challenger':'null_focused_4_action_width_125',
        'metrics_replayed':metrics,'paired_replayed':paired,
        'change_summary':summary,
        'changed_object_rows':len(changed),
        'changed_case_count':len({x['index'] for x in changed}),
        'source_width_report_sha256':width_hashes[WIDTH_FILES[0]],
        'source_width_dataset_sha256':width_report['validation_dataset_sha256'],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'heads_frozen':True,'decoder_production_unchanged':True,
        'candidate_eligibility_unchanged':True,'no_training':True,
        'gold_used_after_inference_only':True,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'production_promotion':False,'g2_unlocked':False,
        'interpretation':('Gold-informed post-inference analysis of the already-examined '
            '0018 within-generator development set. Overlapping proposals and score '
            'differences are observable competition, not proof of a unique causal '
            'mechanism. No new hyperparameter selection or independent validation.'),
    }
    output.mkdir(parents=True)
    (output/'action_conflict_report.json').write_bytes(
        (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    (output/'action_conflict_objects.jsonl').write_bytes(_jsonl_bytes(changed))
    return report
