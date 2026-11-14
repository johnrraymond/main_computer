"""G1-0020: frozen, inference-only selective action-conflict experiment.

All candidates and rules are predeclared. The 0018 examples and 0019 gold
annotations are already inspected development data, not an independent test.
Decisions use only two frozen decoder outputs and calibrated neural margins;
no prompt text, labels, failure causes, training, production change, or G2.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import median

import torch

from tools.nanojev_gamer_v1_g1_action_conflict_diagnosis import (
    SCHEMA as CONFLICT_SCHEMA, WIDTH_FILES, PINNED, PINNED_PAIRED,
    _read_jsonl, _load_width_reference, _candidate_catalog,
    _selected_catalog, _changed_objects,
)
from tools.nanojev_gamer_v1_g1_action_width_validation import (
    CONTROL, CHALLENGER, SHIFT, _load_prior_inputs,
    _load_action_reference, _load_ranking_reference, _verify_no_drift, fresh_rows,
)
from tools.nanojev_gamer_v1_g1_action_errors import WINNER_HEAD
from tools.nanojev_gamer_v1_g1_action_ranking import (
    _candidate_scores, _file_sha, decode_action_ranked,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, _jsonl_bytes, _summarize, _paired,
)
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA = 'nanojev.gamer.graph.g1.selective-action-conflict-dev.v1'
CONFLICT_FILES = ('action_conflict_report.json', 'action_conflict_objects.jsonl')
# Fixed inference-only rules. A longer action must overlap only action spans.
# Its ORIGINAL (linear) weighted score must retain at least 85/90/95% of
# the displaced action scores. Boundary guard additionally requires a
# single-end extension of no more than two tokens.
RULES = (
    ('selective_score_retention_095', 0.95, False),
    ('selective_score_retention_090', 0.90, False),
    ('selective_score_retention_085', 0.85, False),
    ('selective_boundary_guard', 0.85, True),
)
ARMS = (CONTROL, CHALLENGER) + tuple(name for name,_,_ in RULES)


def _signature(span):
    return (span['token_first'], span['token_last'], span['kind'])


def _proposal_map(by_end):
    lookup = {}
    for end, group in enumerate(by_end):
        for first, offset, klass, margin in group:
            key = (first, end-1, SPAN_KINDS[klass-1])
            if key in lookup or not math.isfinite(margin):
                raise ValueError('GAMER_G1_0020_DUPLICATE_OR_BAD_PROPOSAL')
            lookup[key] = float(margin)
    return lookup


def _components(control, challenger):
    """Token-interval overlap components; never join merely adjacent spans."""
    base = {_signature(span) for span in control}
    wide = {_signature(span) for span in challenger}
    common = base & wide
    changed = [(key, 'control') for key in base-common] + [
        (key, 'challenger') for key in wide-common]
    changed.sort(key=lambda pair: (pair[0][0], pair[0][1], pair[1], pair[0][2]))
    groups = []
    for key, side in changed:
        if not groups or key[0] > groups[-1]['last']:
            groups.append({'last':key[1], 'control':[], 'challenger':[]})
        group = groups[-1]
        group['last'] = max(group['last'], key[1])
        group[side].append(key)
    return common, groups


def _gate_component(group, margins, *, min_score_retention, boundary_guard):
    """Decide without labels: long action must outrank displaced shorter actions.

    When the interval comparison is ambiguous, the original decoder wins.
    Baseline and challenger outputs are valid non-overlapping schedules; each
    overlap component is disjoint from all other components.
    """
    longer = group['challenger']
    shorter = group['control']
    if len(longer) != 1 or not shorter:
        return False, {'reason':'ambiguous_component'}
    chosen = longer[0]
    if chosen[2] != 'action' or any(s[2] != 'action' for s in shorter):
        return False, {'reason':'mixed_kind'}
    width = chosen[1]-chosen[0]+1
    if any(width <= s[1]-s[0]+1 or
           not (chosen[0] <= s[1] and s[0] <= chosen[1]) for s in shorter):
        return False, {'reason':'not_longer_overlap'}
    if chosen not in margins or any(s not in margins for s in shorter):
        raise ValueError('GAMER_G1_0020_SELECTED_PROPOSAL_MISSING')
    strongest = max(margins[s] for s in shorter)
    delta = margins[chosen]-strongest
    raw_margin_ratio = margins[chosen]/strongest
    long_baseline_score = margins[chosen]*width
    displaced_baseline_score = sum(margins[s]*(s[1]-s[0]+1) for s in shorter)
    score_retention = long_baseline_score/displaced_baseline_score
    if boundary_guard:
        if len(shorter) != 1:
            return False, {'reason':'multiple_shorter', 'margin_delta':round(delta,6)}
        short = shorter[0]
        left = short[0]-chosen[0]
        right = chosen[1]-short[1]
        if not ((left == 0 and 1 <= right <= 2) or
                (right == 0 and 1 <= left <= 2)):
            return False, {'reason':'boundary_not_one_sided', 'margin_delta':round(delta,6)}
    accepted = score_retention >= min_score_retention - 1e-8
    return accepted, {'reason':'accepted' if accepted else 'insufficient_margin',
                      'margin_delta':round(delta,6),
                      'raw_margin_ratio':round(raw_margin_ratio,6),
                      'score_retention':round(score_retention,6),
                      'long_baseline_score':round(long_baseline_score,6),
                      'displaced_baseline_score':round(displaced_baseline_score,6),
                      'long_margin':round(margins[chosen],6),
                      'max_short_margin':round(strongest,6),
                      'long_width':width,
                      'short_widths':[s[1]-s[0]+1 for s in shorter]}


def selective_decode(control, challenger, by_end, *, min_score_retention, boundary_guard=False):
    """Splice disjoint token-conflict components, using inference data only."""
    if (type(min_score_retention) not in (int,float) or not math.isfinite(min_score_retention) or
            min_score_retention not in (0.85,0.90,0.95) or type(boundary_guard) is not bool):
        raise ValueError('GAMER_G1_0020_INVALID_FIXED_RULE')
    margins = _proposal_map(by_end)
    common, components = _components(control, challenger)
    chosen = set(common)
    decisions=[]
    for group in components:
        take_wide, signals = _gate_component(group,margins,
                      min_score_retention=min_score_retention,boundary_guard=boundary_guard)
        chosen.update(group['challenger'] if take_wide else group['control'])
        decisions.append({'take_challenger':take_wide,
                          'control':[list(s) for s in group['control']],
                          'challenger':[list(s) for s in group['challenger']],
                          **signals})
    ordered = sorted(chosen)
    for left,right in zip(ordered,ordered[1:]):
        if left[1] >= right[0]:
            raise ValueError('GAMER_G1_0020_HYBRID_OVERLAPPING_SPANS')
    if len(chosen) > 128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return ([{'kind':kind,'token_first':first,'token_last':last}
             for first,last,kind in ordered],decisions)


def _read_conflict_reference(path, width_report, width_hashes, rows):
    """Validate frozen 0019 evidence and bind it to this exact 0018 run."""
    path=Path(path)
    paths={name:path/name for name in CONFLICT_FILES}
    hashes={name:_file_sha(p) for name,p in paths.items()}
    report=json.loads(paths[CONFLICT_FILES[0]].read_text(encoding='utf-8'))
    objects=_read_jsonl(paths[CONFLICT_FILES[1]])
    checks={
        'schema':(report.get('schema'),CONFLICT_SCHEMA),
        'status':(report.get('status'),'FROZEN_DEV_DIAGNOSIS_ONLY_NOT_CERTIFICATION'),
        'cases':(report.get('cases'),CASES),
        'families':(report.get('families'),{'encounter':160,'allocation':160}),
        'frozen_reference':(report.get('frozen_reference'),CONTROL),
        'experimental_challenger':(report.get('experimental_challenger'),CHALLENGER),
        'source_width_report_sha256':(report.get('source_width_report_sha256'),
                                      width_hashes[WIDTH_FILES[0]]),
        'source_width_dataset_sha256':(report.get('source_width_dataset_sha256'),
                                       width_report['validation_dataset_sha256']),
        'metrics_replayed':(report.get('metrics_replayed'),width_report['metrics']),
        'paired_replayed':(report.get('paired_replayed'),width_report['paired_comparison']),
        'changed_object_rows':(report.get('changed_object_rows'),66),
        'changed_case_count':(report.get('changed_case_count'),26),
    }
    for name,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0020_INVALID_CONFLICT_REFERENCE:'+name)
    for key in ('heads_frozen','decoder_production_unchanged',
                'candidate_eligibility_unchanged','no_training','gold_used_after_inference_only'):
        if report.get(key) is not True:
            raise ValueError('GAMER_G1_0020_INVALID_CONFLICT_REFERENCE:'+key)
    for key in ('challenge_or_test_split_used','certification_authorized','production_promotion','g2_unlocked'):
        if report.get(key) is not False:
            raise ValueError('GAMER_G1_0020_INVALID_CONFLICT_REFERENCE:'+key)
    target={'recovered_gold':13,'lost_gold':21,'new_false_positive':14,'removed_false_positive':18}
    counters=Counter()
    case_numbers=set()
    for obj in objects:
        direction=obj.get('direction')
        index=obj.get('index')
        if (direction not in target or type(index) is not int or not 1<=index<=CASES or
            obj.get('family')!=rows[index-1]['family'] or
            obj.get('prompt_sha256')!=hashlib.sha256(rows[index-1]['prompt'].encode('utf-8')).hexdigest()):
            raise ValueError('GAMER_G1_0020_BAD_CONFLICT_OBJECT')
        span=obj.get('span')
        if (type(span) is not list or len(span)!=2 or
            any(type(v) is not int for v in span) or not 0<=span[0]<span[1]<=len(rows[index-1]['prompt']) or
            obj.get('surface')!=rows[index-1]['prompt'][span[0]:span[1]] or
            obj.get('kind')!='action' and direction in ('recovered_gold','lost_gold')):
            raise ValueError('GAMER_G1_0020_BAD_CONFLICT_SPAN')
        if not isinstance(obj.get('candidate'),dict) or not isinstance(obj.get('overlapping_other_selected'),list):
            raise ValueError('GAMER_G1_0020_BAD_CONFLICT_CANDIDATE')
        counters[direction]+=1
        case_numbers.add(index)
    if dict(counters)!=target or len(case_numbers)!=26:
        raise ValueError('GAMER_G1_0020_BAD_CONFLICT_TOTALS')
    for key,count in target.items():
        if report.get('change_summary',{}).get(key,{}).get('count')!=count:
            raise ValueError('GAMER_G1_0020_BAD_CONFLICT_SUMMARY:'+key)
    return report,objects,paths,hashes


def _signal_diagnosis(objects):
    """Use gold-tagged 0019 explanations only AFTER all decodes finish."""
    grouped={}
    for direction in ('recovered_gold','lost_gold'):
        samples=[]
        for obj in objects:
            if obj['direction']!=direction:
                continue
            candidates=obj['overlapping_other_selected']
            if len(candidates)!=1 or candidates[0].get('kind')!='action':
                raise ValueError('GAMER_G1_0020_NOT_SINGLE_ACTION_COMPETITOR')
            own=obj['candidate']
            other=candidates[0]
            long,short=(own,other) if direction=='recovered_gold' else (other,own)
            long_width=long['token_width'];short_width=short['token_width']
            if long_width<=short_width:
                raise ValueError('GAMER_G1_0020_UNEXPECTED_WIDTH_DIRECTION')
            delta=float(long['margin'])-float(short['margin'])
            left=short['token_first']-long['token_first']
            right=long['token_last']-short['token_last']
            samples.append({'index':obj['index'],'margin_delta':round(delta,6),
                            'raw_margin_ratio':round(float(long['margin'])/float(short['margin']),6),
                            'score_retention':round(float(long['control_score'])/float(short['control_score']),6),
                            'long_margin':long['margin'],'short_margin':short['margin'],
                            'width_ratio':round(long_width/short_width,6),
                            'added_left_tokens':left,'added_right_tokens':right,
                            'one_sided_short_extension':(
                                (left==0 and 1<=right<=2) or (right==0 and 1<=left<=2))})
        grouped[direction]=samples
    report={}
    for direction,samples in grouped.items():
        margins=[s['margin_delta'] for s in samples]
        widths=[s['width_ratio'] for s in samples]
        ratios=[s['raw_margin_ratio'] for s in samples]
        retentions=[s['score_retention'] for s in samples]
        report[direction]={
            'count':len(samples),
            'median_margin_delta':round(median(margins),6),
            'min_margin_delta':round(min(margins),6),
            'max_margin_delta':round(max(margins),6),
            'median_width_ratio':round(median(widths),6),
            'median_raw_margin_ratio':round(median(ratios),6),
            'median_score_retention':round(median(retentions),6),
            'one_sided_short_extension_count':sum(s['one_sided_short_extension'] for s in samples),
            'score_retention_ge_095':sum(s['score_retention']>=0.95 for s in samples),
            'score_retention_ge_090':sum(s['score_retention']>=0.90 for s in samples),
            'score_retention_ge_085':sum(s['score_retention']>=0.85 for s in samples),
        }
    separation={}
    for name,min_retention,boundary_guard in RULES:
        accepted={direction:sum(item['score_retention']>=min_retention and
             (not boundary_guard or item['one_sided_short_extension'])
             for item in grouped[direction])
             for direction in ('recovered_gold','lost_gold')}
        separation[name]={
            'beneficial_long_actions_accepted':accepted['recovered_gold'],
            'harmful_long_actions_accepted':accepted['lost_gold'],
            'beneficial_long_actions_rejected':len(grouped['recovered_gold'])-accepted['recovered_gold'],
            'harmful_long_actions_rejected':len(grouped['lost_gold'])-accepted['lost_gold'],
        }
    report['gold_tagged_separability_after_inference_only']=separation
    return report, grouped


def run(*,root,transfer_report,fresh_run,null_run,validation_run,action_run,
        ranking_run,width_run,conflict_run,output,device='cuda',max_length=2048):
    """Exploratory replay on already-inspected 0018 dev, not certification."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0020_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0020_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,action_paths,action_hashes=_load_action_reference(action_run,info)
    _,ranking_paths,ranking_hashes=_load_ranking_reference(ranking_run,info,action_hashes)
    rows=fresh_rows(info['dataset'],info['fresh_rows'],info['validation_rows'])
    width_report,width_cases,width_paths,width_hashes=_load_width_reference(
        width_run,rows,info,ranking_hashes,action_hashes)
    conflict_report,conflicts,conflict_paths,conflict_hashes=_read_conflict_reference(
        conflict_run,width_report,width_hashes,rows)
    entry=info['entries'][WINNER_HEAD]
    manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0020_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0020_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records={name:[] for name in ARMS}
    decisions=[]
    replayed_conflicts=[]
    per_case=[]
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            captured=cur.capture(model,tok,row,device,max_length)
            prepared=captured['prepared']
            prediction=head(captured['hidden'].to(device=device,dtype=torch.float32),
                            eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=prediction['span_logits'];valid=prediction['span_valid']
            shifted=logits.detach().float().clone();shifted[...,0]-=SHIFT
            candidates=_candidate_scores(shifted,valid,confidence=.50)
            decoded={CONTROL:decode_action_ranked(candidates,action_power=1.,action_penalty=0.),
                     CHALLENGER:decode_action_ranked(candidates,action_power=1.25,action_penalty=0.)}
            if decoded[CONTROL]!=_decode_shift(logits,valid,SHIFT):
                raise ValueError('GAMER_G1_0020_CONTROL_DECODER_DIVERGED')
            for name,min_score_retention,boundary in RULES:
                spans,details=selective_decode(decoded[CONTROL],decoded[CHALLENGER],candidates,
                                                min_score_retention=min_score_retention,boundary_guard=boundary)
                decoded[name]=spans
                for detail in details:
                    decisions.append({'index':index,'rule':name,**detail})
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            guesses={name:_char_set(spans,prepared) for name,spans in decoded.items()}
            for name in ARMS:
                records[name].append((guesses[name],gold))
            if guesses[CONTROL]!=guesses[CHALLENGER]:
                catalog=_candidate_catalog(candidates,prepared)
                selected=_selected_catalog({name:decoded[name] for name in (CONTROL,CHALLENGER)},
                                           prepared,catalog)
                if any(set(selected[name])!=guesses[name]
                       for name in (CONTROL,CHALLENGER)):
                    raise ValueError('GAMER_G1_0020_SELECTED_SPAN_DIVERGED')
                replayed_conflicts.extend(_changed_objects(index,row,gold,
                    guesses[CONTROL],guesses[CHALLENGER],catalog,selected))
            expected=width_cases[index-1]['configurations']
            for name in (CONTROL,CHALLENGER):
                guess=guesses[name]
                actual={'correct':len(guess&gold),'missing':len(gold-guess),
                        'false_positives':len(guess-gold),'exact':guess==gold}
                if actual!=expected[name]:
                    raise ValueError('GAMER_G1_0020_WIDTH_CASE_DIVERGED:'+str(index)+':'+name)
            per_case.append({'index':index,'family':row['family'],
                  'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                  'configurations':{name:{
                      'correct':len(guess&gold),'missing':len(gold-guess),
                      'false_positives':len(guess-gold),'exact':guess==gold,
                  } for name,guess in guesses.items()}})
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_SELECTIVE_CONFLICT {index}/{CASES}',flush=True)
    metrics={name:_summarize(records[name],rows) for name in ARMS}
    for name in (CONTROL,CHALLENGER):
        if metrics[name]!=width_report['metrics'][name]:
            raise ValueError('GAMER_G1_0020_WIDTH_METRICS_DIVERGED:'+name)
    if _paired(records[CONTROL],records[CHALLENGER]) != width_report['paired_comparison']:
        raise ValueError('GAMER_G1_0020_PAIRED_REPLAY_DIVERGED')
    if replayed_conflicts != conflicts:
        raise ValueError('GAMER_G1_0020_CONFLICT_REPLAY_DIVERGED')
    signal_report,signals=_signal_diagnosis(conflicts)
    for paths,hashes,label in ((width_paths,width_hashes,'WIDTH'),
                               (conflict_paths,conflict_hashes,'CONFLICT')):
        for name,path in paths.items():
            if _file_sha(path)!=hashes[name]:
                raise ValueError('GAMER_G1_0020_'+label+'_INPUT_CHANGED_DURING_RUN:'+name)
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],
                     action_paths,action_hashes,ranking_paths,ranking_hashes)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json') !=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE) !=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0020_CHECKPOINT_CHANGED_DURING_RUN')
    report={
        'schema':SCHEMA,'status':'SELECTIVE_CONFLICT_DEV_ONLY_NOT_CERTIFICATION',
        'cases':CASES,'families':{'encounter':160,'allocation':160},
        'configuration_predeclared':list(ARMS),
        'rules_predeclared':[{'name':name,'min_score_retention':min_score_retention,
                              'boundary_guard':boundary} for name,min_score_retention,boundary in RULES],
        'metrics':metrics,
        'paired_vs_control':{name:_paired(records[CONTROL],records[name])
                             for name in ARMS if name!=CONTROL},
        'selection_order_exploratory_only':sorted(ARMS,key=lambda name:(
              -metrics[name]['exact_prompts'],-metrics[name]['f1'],
              metrics[name]['false_positive_objects'],name)),
        'candidate_for_new_independent_validation':None,
        'diagnostic_signal_summary':signal_report,
        'source_conflict_report_sha256':conflict_hashes[CONFLICT_FILES[0]],
        'source_width_report_sha256':width_hashes[WIDTH_FILES[0]],
        'source_width_dataset_sha256':width_report['validation_dataset_sha256'],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'one_feature_capture_per_prompt':True,'heads_frozen':True,
        'candidate_eligibility_unchanged':True,'production_decoder_unchanged':True,
        'no_training':True,'gold_used_after_inference_only':True,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'production_promotion':False,'g2_unlocked':False,
        'interpretation':('Fixed inference-only selective rules evaluated on the already-examined '
           'G1-0018 within-generator development examples. Diagnostic labels may describe '
           'rules after inference but never choose spans. Selection here is exploratory; '
           'no proof of generalization, promotion, G2, or new independent validation.'),
    }
    output.mkdir(parents=True)
    (output/'selective_conflict_report.json').write_bytes(
        (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    (output/'selective_conflict_cases.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'selective_conflict_decisions.jsonl').write_bytes(_jsonl_bytes(decisions))
    (output/'selective_conflict_signal_examples.jsonl').write_bytes(_jsonl_bytes([
        {'direction':direction,**item} for direction,items in signals.items() for item in items]))
    return report
