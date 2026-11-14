"""G1-0016: read-only action-span ranking ablation on the frozen G1-0014 dev set.

This is a *development* experiment: G1-0014 labels have been inspected already.
Alternatives are predeclared in source and use only frozen G1-0015 winner logits.
Gold and the G1-0015 explanations enter scoring AFTER inference; neither changes
candidate eligibility, scores, inference, checkpoints, or production decoding.
No certification, new-data generalization claim, training, or G2 access.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_action_errors import (
    SCHEMA as ACTION_SCHEMA, WINNER_HEAD, WINNER_KEY, WINNER_SHIFT,
    _load_inputs as _load_prior_inputs, _verify_unchanged as _verify_prior_unchanged,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import CASES, FAMILIES, _jsonl_bytes, _summarize
from tools.nanojev_gamer_v1_g1_decoder_frob import _candidate_scores, _char_set
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift, _verify_metrics
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.frozen-action-span-ranking-dev.v1'
# name, exponent on action token width, action-only fixed per-span penalty.
# All other object kinds retain the original margin * width score.
# Exact baseline is a mandatory control; alternatives cannot tune eligibility.
VARIANTS = (
    ('winner_control', 1.0, 0.0),
    ('action_width_075', 0.75, 0.0),
    ('action_width_125', 1.25, 0.0),
    ('action_width_150', 1.50, 0.0),
    ('action_count_penalty_1', 1.0, 1.0),
    ('action_count_penalty_2', 1.0, 2.0),
)
REFERENCE_COUNTS = {
    'gold_actions': 4166, 'correct_actions': 3950,
    'missing_actions': 216, 'false_positive_actions': 121,
    'miss_reasons': {'below_threshold':15, 'linear_decoder_excluded':100,
                     'null_wins':68, 'wrong_type':33},
}
ACTION_CLASS = SPAN_KINDS.index('action')+1


def _file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _span_score(margin, width, klass, action_power, action_penalty):
    if klass == ACTION_CLASS:
        return margin * width ** action_power - action_penalty
    return margin * width


def decode_action_ranked(by_end, *, action_power, action_penalty):
    """Weighted interval DP, from neural proposals only (no text or gold)."""
    if (not isinstance(action_power,(int,float)) or isinstance(action_power,bool)
            or not math.isfinite(action_power) or not .5 <= action_power <= 1.5
            or not isinstance(action_penalty,(int,float)) or isinstance(action_penalty,bool)
            or not math.isfinite(action_penalty) or not 0 <= action_penalty <= 2):
        raise ValueError('GAMER_G1_0016_UNSAFE_RANKING')
    size = len(by_end)-1
    if size < 0:
        raise ValueError('GAMER_G1_0016_BAD_CANDIDATES')
    dp = [0.]*(size+1)
    back = [None]*(size+1)
    for end in range(1,size+1):
        best = dp[end-1]
        selected = (end-1,None)
        for first, offset, klass, margin in by_end[end]:
            if not (0 <= first < end and offset == end-first-1
                    and 1 <= klass <= len(SPAN_KINDS) and math.isfinite(margin)):
                raise ValueError('GAMER_G1_0016_BAD_CANDIDATES')
            score = _span_score(margin,offset+1,klass,action_power,action_penalty)
            candidate = dp[first]+score
            if score > 0 and candidate > best+1e-8:
                best = candidate
                selected = (first,(first,offset,klass))
        dp[end] = best
        back[end] = selected
    spans = []
    end = size
    while end > 0:
        first, chosen = back[end]
        if chosen is not None:
            first, offset, klass = chosen
            spans.append({'kind':SPAN_KINDS[klass-1],
                          'token_first':first, 'token_last':first+offset})
        end = first
    if len(spans) > 128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    return sorted(spans, key=lambda s:s['token_first'])


def _load_action_reference(path, info):
    """Tie G1-0015's diagnostic records to the exact G1-0014 frozen winner."""
    path = Path(path)
    files = {name:path/name for name in (
        'frozen_development_candidate.json', 'action_error_report.json',
        'action_error_cases.jsonl')}
    hashes = {name:_file_sha(p) for name,p in files.items()}
    selection = json.loads(files['frozen_development_candidate.json'].read_text(encoding='utf-8'))
    report = json.loads(files['action_error_report.json'].read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in files['action_error_cases.jsonl'].read_text(
        encoding='utf-8').splitlines() if line.strip()]
    metric = info['validation_report']['metrics'][WINNER_KEY]
    errors = report.get('action_errors',{})
    # A flattened boolean chain previously hid which frozen contract failed.
    # Keep every equality and identity gate, but identify mismatched paths so a
    # genuine lineage problem is not mistaken for a decoder/model regression.
    def first_difference(actual, expected, prefix):
        if isinstance(actual, dict) and isinstance(expected, dict):
            for key in sorted(actual.keys() | expected.keys(), key=str):
                location = f'{prefix}.{key}'
                if key not in actual or key not in expected:
                    return location
                issue = first_difference(actual[key], expected[key], location)
                if issue:
                    return issue
            return None
        if isinstance(actual, list) and isinstance(expected, list):
            if len(actual) != len(expected):
                return f'{prefix}.length'
            for i, (left, right) in enumerate(zip(actual, expected)):
                issue = first_difference(left, right, f'{prefix}[{i}]')
                if issue:
                    return issue
            return None
        return prefix if type(actual) is not type(expected) or actual != expected else None

    failures = []
    for field, actual, expected in (
        ('report.schema', report.get('schema'), ACTION_SCHEMA),
        ('report.status', report.get('status'), 'DIAGNOSTIC_ONLY_NOT_CERTIFICATION'),
        ('report.cases', report.get('cases'), CASES),
        ('report.families', report.get('families'), {'encounter':160,'allocation':160}),
        ('report.winner', report.get('winner'), selection),
        ('report.baseline_metrics', report.get('baseline_metrics'), metric),
        ('selection.key', selection.get('key'), WINNER_KEY),
        ('selection.checkpoint', selection.get('checkpoint'), WINNER_HEAD),
        ('selection.null_logit_shift', selection.get('null_logit_shift'), WINNER_SHIFT),
        ('selection.decoder', selection.get('decoder'), 'length_linear'),
        ('selection.head_sha256', selection.get('head_sha256'),
            info['entries'][WINNER_HEAD]['manifest']['head_sha256']),
        ('selection.validation_dataset_sha256', selection.get('validation_dataset_sha256'),
            info['validation_report']['validation_dataset_sha256']),
        ('selection.validation_report_file_sha256', selection.get('validation_report_file_sha256'),
            info['validation_file_sha256']['validation_report.json']),
        ('selection.source_adapter_sha256', selection.get('source_adapter_sha256'), info['adapter_sha']),
        ('selection.checkpoint_file_sha256', selection.get('checkpoint_file_sha256'),
            info['checkpoint_files'][WINNER_HEAD]),
        ('selection.status', selection.get('status'), 'FROZEN_DEVELOPMENT_REFERENCE_NOT_PRODUCTION'),
        ('report.action_errors.gold_actions', errors.get('gold_actions'), REFERENCE_COUNTS['gold_actions']),
        ('report.action_errors.correct_actions', errors.get('correct_actions'), REFERENCE_COUNTS['correct_actions']),
        ('report.action_errors.missing_actions', errors.get('missing_actions'), REFERENCE_COUNTS['missing_actions']),
        ('report.action_errors.false_positive_actions', errors.get('false_positive_actions'),
            REFERENCE_COUNTS['false_positive_actions']),
        ('report.action_errors.miss_reasons', errors.get('miss_reasons'),
            REFERENCE_COUNTS['miss_reasons']),
        ('report.diagnostic_example_count', report.get('diagnostic_example_count'), 337),
        ('report.errored_prompts_with_action_errors',
            report.get('errored_prompts_with_action_errors'), 158),
        ('action_error_cases.jsonl.row_count', len(rows), 158),
    ):
        issue = first_difference(actual, expected, field)
        if issue:
            failures.append(issue)
    for key in ('training_authorized','production_promotion','certification_authorized','g2_unlocked'):
        if selection.get(key) is not False:
            failures.append(f'selection.{key}')
    for key in ('challenge_or_test_split_used','production_promotion',
                'certification_authorized','g2_unlocked'):
        if report.get(key) is not False:
            failures.append(f'report.{key}')
    for key in ('no_training','heads_frozen','decoder_frozen'):
        if report.get(key) is not True:
            failures.append(f'report.{key}')
    if failures:
        raise ValueError('GAMER_G1_0016_INVALID_ACTION_REFERENCE:' + ','.join(failures))
    result = {}
    miss_total = Counter()
    fp_total = 0
    for case in rows:
        index = case.get('index')
        if (type(index) is not int or index in result or not 1 <= index <= CASES
                or case.get('family') != info['validation_rows'][index-1]['family']
                or case.get('prompt') != info['validation_rows'][index-1]['prompt']
                or case.get('prompt_sha256') != hashlib.sha256(case['prompt'].encode('utf-8')).hexdigest()):
            raise ValueError('GAMER_G1_0016_INVALID_ACTION_CASE')
        misses = case['missing_actions']
        fps = case['false_positive_actions']
        if not misses and not fps:
            raise ValueError('GAMER_G1_0016_EMPTY_ACTION_CASE')
        for entry in misses:
            if (entry.get('kind') != 'action' or entry.get('reason') not in REFERENCE_COUNTS['miss_reasons']):
                raise ValueError('GAMER_G1_0016_BAD_ACTION_MISS')
            miss_total[entry['reason']] += 1
        for entry in fps:
            if entry.get('kind') != 'action':
                raise ValueError('GAMER_G1_0016_BAD_ACTION_FP')
        fp_total += len(fps)
        result[index] = case
    if dict(miss_total) != REFERENCE_COUNTS['miss_reasons'] or fp_total != 121:
        raise ValueError('GAMER_G1_0016_ACTION_REFERENCE_COUNTS')
    return result, files, hashes


def _surface(row, item):
    start,end,kind = item
    return {'span':[start,end], 'kind':kind, 'surface':row['prompt'][start:end]}


def _compare(base, candidate, gold, row, reasons):
    recovered = (gold-base)&candidate
    lost = (gold&base)-candidate
    newfp = (candidate-gold)-(base-gold)
    removed = (base-gold)-(candidate-gold)
    return {
        'new_exact':int(candidate==gold and base!=gold),
        'lost_exact':int(base==gold and candidate!=gold),
        'recovered_gold':len(recovered),
        'lost_gold':len(lost),
        'new_false_positives':len(newfp),
        'removed_false_positives':len(removed),
        'recovered_actions':sum(k[2]=='action' for k in recovered),
        'lost_actions':sum(k[2]=='action' for k in lost),
        'new_false_positive_actions':sum(k[2]=='action' for k in newfp),
        'removed_false_positive_actions':sum(k[2]=='action' for k in removed),
        'recovered_excluded_actions':sum(k[2]=='action' and reasons.get(k)=='linear_decoder_excluded' for k in recovered),
        'recovered_actions_by_prior_reason':dict(sorted(Counter(
            reasons[k] for k in recovered if k[2]=='action' and k in reasons).items())),
        'recovered_action_spans':[_surface(row,k) for k in sorted(recovered) if k[2]=='action'],
        'lost_action_spans':[_surface(row,k) for k in sorted(lost) if k[2]=='action'],
        'new_false_positive_action_spans':[_surface(row,k) for k in sorted(newfp) if k[2]=='action'],
        'removed_false_positive_action_spans':[_surface(row,k) for k in sorted(removed) if k[2]=='action'],
    }


def _selected_action_conflicts(prediction, prepared, row, baseline_spans, ref_case, index):
    """Gold-informed *post-inference* decomposition of baseline action rivals."""
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if not ref_case:
        return []
    aligned = cur.aligned_labels(prepared,row)['spans']
    mapping = {(*token_span_char_bounds(s,prepared),s['kind']):s for s in aligned}
    logits = prediction['span_logits'].detach().float().cpu()
    proposals = []
    for miss in ref_case['missing_actions']:
        if miss['reason'] != 'linear_decoder_excluded':
            continue
        key = (*miss['span'],'action')
        aligned_span = mapping.get(key)
        if aligned_span is None:
            raise ValueError('GAMER_G1_0016_ACTION_ALIGNMENT')
        first,last = aligned_span['token_first'],aligned_span['token_last']
        scores = logits[first,last-first]
        gold_margin = float(scores[ACTION_CLASS]-scores[0])+WINNER_SHIFT
        competitors = []
        for span in baseline_spans:
            if span['token_first'] <= last and span['token_last'] >= first:
                bounds = token_span_char_bounds(span,prepared)
                klass = SPAN_KINDS.index(span['kind'])+1
                s_first,s_last = span['token_first'],span['token_last']
                entry = logits[s_first,s_last-s_first]
                margin = float(entry[klass]-entry[0])+WINNER_SHIFT
                competitors.append({'span':list(bounds), 'kind':span['kind'],
                    'token_width':s_last-s_first+1, 'margin':round(margin,6),
                    'baseline_weight':round(margin*(s_last-s_first+1),6),
                    'surface':row['prompt'][bounds[0]:bounds[1]]})
        if not competitors:
            raise ValueError('GAMER_G1_0016_MISSING_RIVAL')
        proposals.append({'index':index, 'family':row['family'],
            'gold_action_span':list(key[:2]),
            'gold_action_surface':row['prompt'][key[0]:key[1]],
            'gold_token_width':last-first+1,
            'gold_calibrated_margin':round(gold_margin,6),
            'gold_baseline_weight':round(gold_margin*(last-first+1),6),
            'selected_conflicts':competitors})
    return proposals


def run(*, root, transfer_report, fresh_run, null_run, validation_run,
        action_run, output, device='cuda', max_length=2048):
    """Run the fixed ablation on G1-0014 dev, NEVER a new test/challenge split."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0016_INVALID_MAX_LENGTH')
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0016_IMMUTABLE_OUTPUT')
    info = _load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    refs, ref_files, ref_hash = _load_action_reference(action_run,info)
    entry = info['entries'][WINNER_HEAD]
    manifest = entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter']) != info['adapter_sha']:
        raise ValueError('GAMER_G1_0016_ADAPTER_CHANGED')
    head,loaded = cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0016_HEAD_CHANGED')
    head.eval()
    model,tok = backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records = {name:[] for name,_,_ in VARIANTS}
    counters = {name:Counter() for name,_,_ in VARIANTS[1:]}
    recovered_reasons = {name:Counter() for name,_,_ in VARIANTS[1:]}
    changed_rows = []
    competitors = []
    with torch.inference_mode():
        for index,row in enumerate(info['validation_rows'],1):
            item=cur.capture(model,tok,row,device,max_length)
            prepared=item['prepared']
            prediction=head(item['hidden'].to(device=device,dtype=torch.float32),
                eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=prediction['span_logits']
            valid=prediction['span_valid']
            biased=logits.detach().float().clone()
            biased[...,0] -= WINNER_SHIFT
            by_end=_candidate_scores(biased,valid,confidence=.50)
            baseline=_decode_shift(logits,valid,WINNER_SHIFT)
            decoded={}
            for name,power,penalty in VARIANTS:
                decoded[name]=decode_action_ranked(by_end,action_power=power,
                                                    action_penalty=penalty)
            if decoded[VARIANTS[0][0]] != baseline:
                raise ValueError('GAMER_G1_0016_BASELINE_DECODER_DIVERGED')
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            base=_char_set(baseline,prepared)
            expected=info['validation_cases'][index-1]['configurations'][WINNER_KEY]
            actual={'correct':len(base&gold),'missing':len(gold-base),
                    'false_positives':len(base-gold),'exact':base==gold}
            if actual != expected:
                raise ValueError('GAMER_G1_0016_BASELINE_CASE_REPLAY_DIVERGED')
            ref=refs.get(index)
            action_misses=sorted(k for k in gold-base if k[2]=='action')
            action_fp=sorted(k for k in base-gold if k[2]=='action')
            if (ref is None and (action_misses or action_fp)) or (ref is not None and (
                action_misses != sorted((*m['span'],'action') for m in ref['missing_actions']) or
                action_fp != sorted((*m['span'],'action') for m in ref['false_positive_actions']))):
                raise ValueError('GAMER_G1_0016_ACTION_REPLAY_DIVERGED')
            reason_map={(*m['span'],'action'):m['reason'] for m in ref['missing_actions']} if ref else {}
            competitors.extend(_selected_action_conflicts(prediction,prepared,row,baseline,ref,index))
            changes={}
            for name,_,_ in VARIANTS:
                guess=_char_set(decoded[name],prepared)
                records[name].append((guess,gold))
                if name==VARIANTS[0][0]:
                    continue
                difference=_compare(base,guess,gold,row,reason_map)
                if any(difference[k] for k in (
                    'new_exact','lost_exact','recovered_gold','lost_gold',
                    'new_false_positives','removed_false_positives')):
                    changes[name]=difference
                for k,v in difference.items():
                    if isinstance(v,int):counters[name][k]+=v
                recovered_reasons[name].update(difference['recovered_actions_by_prior_reason'])
            if changes:
                changed_rows.append({'index':index,'family':row['family'],
                    'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                    'changes':changes})
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_ACTION_RANKING {index}/{CASES}',flush=True)
    metric={name:_summarize(recs,info['validation_rows']) for name,recs in records.items()}
    base_metric=metric['winner_control']
    _verify_metrics(base_metric,info['validation_report']['metrics'][WINNER_KEY],
                    '0016_BASELINE_REPLAY')
    if base_metric != info['validation_report']['metrics'][WINNER_KEY]:
        raise ValueError('GAMER_G1_0016_METRICS_REPLAY_DIVERGED')
    if len(competitors)!=100:
        raise ValueError('GAMER_G1_0016_COMPETITION_COUNT_DIVERGED')
    _verify_prior_unchanged(info,transfer_report,fresh_run,manifest['source_adapter'])
    if any(_file_sha(path)!=ref_hash[name] for name,path in ref_files.items()):
        raise ValueError('GAMER_G1_0016_ACTION_INPUT_CHANGED_DURING_RUN')
    proposals={}
    for name,power,penalty in VARIANTS[1:]:
        m=metric[name]
        c=counters[name]
        proposals[name]={
            'action_width_power':power,'action_per_span_penalty':penalty,
            'metrics':m,
            'paired_changes':dict(sorted(c.items())),
            'recovered_action_misses_by_0015_reason':dict(sorted(recovered_reasons[name].items())),
            'dev_improves_exact_and_f1':m['exact_prompts']>base_metric['exact_prompts'] and m['f1']>base_metric['f1'],
        }
    exploratory_rank=sorted(metric,key=lambda n:(-metric[n]['exact_prompts'],
        -metric[n]['f1'],metric[n]['false_positive_objects'],n))
    report={
        'schema':SCHEMA,'status':'FROZEN_DEV_ACTION_RANKING_ABLATION_ONLY',
        'cases':CASES,'families':{'encounter':160,'allocation':160},
        'baseline':'winner_control','winner_checkpoint':'null_focused',
        'winner_null_logit_shift':WINNER_SHIFT,
        'baseline_metrics':base_metric,'variants_predeclared':[
            {'name':name,'action_width_power':power,'action_per_span_penalty':penalty}
            for name,power,penalty in VARIANTS],
        'alternatives':proposals,'exploratory_ranking':exploratory_rank,
        'ranking_rule':'exact_prompts_desc,f1_desc,false_positives_asc,name_asc',
        'baseline_0015_competing_action_spans':len(competitors),
        'changed_prompt_count':len(changed_rows),
        'source_0015_file_sha256':ref_hash,
        'source_0014_validation_report_sha256':info['validation_file_sha256']['validation_report.json'],
        'source_0014_validation_dataset_sha256':info['validation_report']['validation_dataset_sha256'],
        'head_sha256':manifest['head_sha256'],
        'head_files_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'one_feature_capture_per_prompt':True,'heads_frozen':True,
        'candidate_eligibility_unchanged':True,'decoder_production_unchanged':True,
        'no_training':True,'challenge_or_test_split_used':False,
        'certification_authorized':False,'production_promotion':False,'g2_unlocked':False,
        'interpretation':('The 320 G1-0014 dev examples were previously inspected; '
            'this is an exploratory ranking ablation, NOT independent validation. '
            'Gold-informed conflict explanations are created only after inference. '
            'A selected alternative requires disjoint fresh development testing.'),
    }
    output.mkdir(parents=True)
    (output/'action_ranking_report.json').write_bytes(
        (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    (output/'action_ranking_changes.jsonl').write_bytes(_jsonl_bytes(changed_rows))
    (output/'action_ranking_competitors.jsonl').write_bytes(_jsonl_bytes(competitors))
    return report
