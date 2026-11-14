"""G1-0018: predeclared, new-development-only action-width 1.25 replication.

The G1-0016 ablation nominated exactly one challenger. This runner does not
search hyperparameters, alter eligibility, train a head, or access sealed data.
All decision settings and the random seed are fixed before fresh inference.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random

import torch

from tools.nanojev_gamer_v1_g1_action_errors import (
    WINNER_HEAD, WINNER_KEY, WINNER_SHIFT,
    _load_inputs as _load_prior_inputs,
    _verify_unchanged as _verify_prior_unchanged,
)
from tools.nanojev_gamer_v1_g1_action_ranking import (
    SCHEMA as RANKING_SCHEMA,
    VARIANTS, _candidate_scores, _file_sha, _load_action_reference,
    decode_action_ranked,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, FAMILIES, _jsonl_bytes, _summarize, _paired,
)
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_fresh_dev import _original_prompts
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift, _verify_metrics

SCHEMA = 'nanojev.gamer.graph.g1.frozen-action-width-fresh-validation.v1'
SEED = 281803
CONTROL = 'null_focused_4_control'
CHALLENGER = 'null_focused_4_action_width_125'
CHALLENGER_POWER = 1.25
SHIFT = 4.0
CANDIDATES = (
    (CONTROL, 1.0, 0.0),
    (CHALLENGER, CHALLENGER_POWER, 0.0),
)
PINNED_ABLATION = {
    'exact_prompts': 140,
    'missing_objects': 267,
    'false_positive_objects': 204,
}


def fresh_rows(original_dataset, old_fresh_rows, prior_validation_rows,
               *, seed=SEED, cases=CASES):
    """Create exactly 320 prompt-disjoint, balanced dev examples."""
    from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
    if type(seed) is not int or seed != SEED or type(cases) is not int or cases != CASES:
        raise ValueError('GAMER_G1_0018_GENERATION_SETTINGS_LOCKED')
    used = _original_prompts(original_dataset)
    for source, prior in (('0011', old_fresh_rows), ('0014', prior_validation_rows)):
        if len(prior) != (80 if source == '0011' else CASES):
            raise ValueError('GAMER_G1_0018_INVALID_PRIOR_ROW_COUNT:'+source)
        seen = set()
        for row in prior:
            prompt = row.get('prompt') if isinstance(row, dict) else None
            if (not isinstance(prompt,str) or row.get('split') != 'dev'
                    or row.get('family') not in FAMILIES
                    or prompt in seen or prompt in used):
                raise ValueError('GAMER_G1_0018_INVALID_PRIOR_PROMPTS:'+source)
            seen.add(prompt)
        used.update(seen)
    rng = random.Random(seed)
    rows = []
    for index in range(cases):
        generator = encounter if index % 2 == 0 else allocation
        for attempt in range(250):
            row = generator(rng, 'dev', index)
            if (row.get('split') == 'dev' and row.get('family') == FAMILIES[index % 2]
                    and isinstance(row.get('prompt'),str)
                    and row['prompt'] not in used):
                break
        else:
            raise ValueError('GAMER_G1_0018_PROMPT_EXCLUSION_EXHAUSTED')
        rows.append(row)
        used.add(row['prompt'])
    if (len(rows) != CASES or len({r['prompt'] for r in rows}) != CASES
            or any(sum(r['family'] == f for r in rows) != 160 for f in FAMILIES)):
        raise ValueError('GAMER_G1_0018_FRESH_GENERATION_INVARIANT')
    return rows


def _load_ranking_reference(path, info, action_hashes):
    """Check the actual 0016 report, including frozen baseline and both variants."""
    path = Path(path)
    files = {name: path / name for name in (
        'action_ranking_report.json', 'action_ranking_changes.jsonl',
        'action_ranking_competitors.jsonl',
    )}
    hashes = {name: _file_sha(p) for name,p in files.items()}
    report = json.loads(files['action_ranking_report.json'].read_text(encoding='utf-8'))
    reference = info['validation_report']['metrics'][WINNER_KEY]
    challenger = report.get('alternatives',{}).get('action_width_125',{})
    metric = challenger.get('metrics',{})
    paired = challenger.get('paired_changes',{})
    expected_variants = [
        {'name':name,'action_width_power':power,'action_per_span_penalty':penalty}
        for name,power,penalty in VARIANTS
    ]
    checks = {
        'schema':(report.get('schema'),RANKING_SCHEMA),
        'status':(report.get('status'),'FROZEN_DEV_ACTION_RANKING_ABLATION_ONLY'),
        'cases':(report.get('cases'),CASES),
        'families':(report.get('families'),{'encounter':160,'allocation':160}),
        'baseline':(report.get('baseline'),'winner_control'),
        'baseline_metrics':(report.get('baseline_metrics'),reference),
        'winner_checkpoint':(report.get('winner_checkpoint'),WINNER_HEAD),
        'winner_null_logit_shift':(report.get('winner_null_logit_shift'),WINNER_SHIFT),
        'variants_predeclared':(report.get('variants_predeclared'),expected_variants),
        'source_0015_file_sha256':(report.get('source_0015_file_sha256'),action_hashes),
        'source_0014_validation_report_sha256':(report.get('source_0014_validation_report_sha256'),
                                                info['validation_file_sha256']['validation_report.json']),
        'source_0014_validation_dataset_sha256':(report.get('source_0014_validation_dataset_sha256'),
                                                 info['validation_report']['validation_dataset_sha256']),
        'head_sha256':(report.get('head_sha256'),info['entries'][WINNER_HEAD]['manifest']['head_sha256']),
        'head_files_sha256':(report.get('head_files_sha256'),info['checkpoint_files'][WINNER_HEAD]),
        'source_adapter_sha256':(report.get('source_adapter_sha256'),info['adapter_sha']),
        'baseline_0015_competing_action_spans':(report.get('baseline_0015_competing_action_spans'),100),
        'alternatives.action_width_125.action_width_power':(challenger.get('action_width_power'),CHALLENGER_POWER),
        'alternatives.action_width_125.action_per_span_penalty':(challenger.get('action_per_span_penalty'),0.0),
        'alternatives.action_width_125.dev_improves_exact_and_f1':(
            challenger.get('dev_improves_exact_and_f1'),True),
        'alternatives.action_width_125.metrics.cases':(metric.get('cases'),CASES),
        'alternatives.action_width_125.metrics.expected':(metric.get('expected'),reference['expected']),
        'alternatives.action_width_125.metrics.correct':(
            metric.get('correct'),metric.get('expected',-1)-metric.get('missing_objects',-2)),
        'alternatives.action_width_125.metrics.predicted':(
            metric.get('predicted'),metric.get('correct',-1)+metric.get('false_positive_objects',-2)),
        'alternatives.action_width_125.paired_changes.recovered_actions':(
            paired.get('recovered_actions'),17),
        'alternatives.action_width_125.paired_changes.lost_actions':(
            paired.get('lost_actions'),18),
        'one_feature_capture_per_prompt':(report.get('one_feature_capture_per_prompt'),True),
        'heads_frozen':(report.get('heads_frozen'),True),
        'candidate_eligibility_unchanged':(report.get('candidate_eligibility_unchanged'),True),
        'decoder_production_unchanged':(report.get('decoder_production_unchanged'),True),
        'no_training':(report.get('no_training'),True),
        'challenge_or_test_split_used':(report.get('challenge_or_test_split_used'),False),
        'certification_authorized':(report.get('certification_authorized'),False),
        'production_promotion':(report.get('production_promotion'),False),
        'g2_unlocked':(report.get('g2_unlocked'),False),
    }
    for key,value in PINNED_ABLATION.items():
        checks[f'alternatives.action_width_125.metrics.{key}'] = (metric.get(key),value)
    for name in ('action_width_075','action_width_150',
                 'action_count_penalty_1','action_count_penalty_2'):
        if name not in report.get('alternatives', {}):
            raise ValueError('GAMER_G1_0018_UNTRUSTED_RANKING:alternatives.'+name)
    for key,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError('GAMER_G1_0018_UNTRUSTED_RANKING:'+key)
    if not math.isfinite(metric.get('f1',float('nan'))) or not (.9736 < metric['f1'] < .9738):
        raise ValueError('GAMER_G1_0018_UNTRUSTED_RANKING:challenger.f1')
    if report.get('exploratory_ranking',[None])[0] != 'action_width_125':
        raise ValueError('GAMER_G1_0018_UNTRUSTED_RANKING:exploratory_ranking')
    return report,files,hashes


def _verify_no_drift(info, transfer_report, fresh_run, source_adapter,
                     action_paths, action_hashes, ranking_paths, ranking_hashes):
    _verify_prior_unchanged(info, transfer_report, fresh_run, source_adapter)
    for label, paths, hashes in (('ACTION',action_paths,action_hashes),
                                 ('RANKING',ranking_paths,ranking_hashes)):
        for name,path in paths.items():
            if _file_sha(path) != hashes[name]:
                raise ValueError('GAMER_G1_0018_'+label+'_INPUT_CHANGED_DURING_RUN:'+name)


def run(*, root, transfer_report, fresh_run, null_run, validation_run,
        action_run, ranking_run, output, device='cuda', max_length=2048):
    """One-shot, no-training fresh replication of a fixed two-decoder comparison."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length < 1:
        raise ValueError('GAMER_G1_0018_INVALID_MAX_LENGTH')
    output = Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0018_IMMUTABLE_OUTPUT')
    info = _load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _refs,action_paths,action_hashes = _load_action_reference(action_run,info)
    ranking,ranking_paths,ranking_hashes = _load_ranking_reference(
        ranking_run,info,action_hashes)
    rows = fresh_rows(info['dataset'],info['fresh_rows'],info['validation_rows'])
    dataset_bytes = _jsonl_bytes(rows)
    dataset_sha = hashlib.sha256(dataset_bytes).hexdigest()
    entry = info['entries'][WINNER_HEAD]
    manifest = entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter']) != info['adapter_sha']:
        raise ValueError('GAMER_G1_0018_ADAPTER_CHANGED')
    head,loaded = cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage') != 'G1' or loaded.get('head_sha256') != manifest['head_sha256']:
        raise ValueError('GAMER_G1_0018_HEAD_CHANGED')
    head.eval()
    model,tok = backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records = {name: [] for name,_,_ in CANDIDATES}
    per_case = []
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            item = cur.capture(model,tok,row,device,max_length)
            prepared=item['prepared']
            hidden=item['hidden'].to(device=device,dtype=torch.float32)
            prediction=head(hidden,eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=prediction['span_logits']
            valid=prediction['span_valid']
            shifted=logits.detach().float().clone()
            shifted[...,0] -= SHIFT
            candidates=_candidate_scores(shifted,valid,confidence=.50)
            decoded={name:decode_action_ranked(
                candidates,action_power=power,action_penalty=penalty)
                for name,power,penalty in CANDIDATES}
            if decoded[CONTROL] != _decode_shift(logits,valid,SHIFT):
                raise ValueError('GAMER_G1_0018_BASELINE_DECODER_DIVERGED')
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            guesses={name:_char_set(spans,prepared) for name,spans in decoded.items()}
            for name,guess in guesses.items():
                records[name].append((guess,gold))
            per_case.append({
                'index':index,'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'gold_count':len(gold),
                'configurations':{name:{
                    'correct':len(guess&gold),
                    'missing':len(gold-guess),
                    'false_positives':len(guess-gold),
                    'exact':guess==gold,
                } for name,guess in guesses.items()},
            })
            if index == 1 or index % 20 == 0 or index == CASES:
                print(f'GAMER_G1_ACTION_WIDTH_VALIDATION {index}/{CASES}',flush=True)
    metrics={name:_summarize(records[name],rows) for name,_,_ in CANDIDATES}
    paired=_paired(records[CONTROL],records[CHALLENGER])
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],
                     action_paths,action_hashes,ranking_paths,ranking_hashes)
    report={
        'schema':SCHEMA,'status':'FRESH_WITHIN_GENERATOR_DEVELOPMENT_REPLICATION_ONLY',
        'seed':SEED,'cases':CASES,'families':{'encounter':160,'allocation':160},
        'split':'new_dev','configurations_predeclared':[
            {'key':name,'checkpoint':WINNER_HEAD,'null_logit_shift':WINNER_SHIFT,
             'action_width_power':power,'action_per_span_penalty':penalty}
            for name,power,penalty in CANDIDATES],
        'metrics':metrics,
        'paired_comparison':paired,
        'ranking_rule_predeclared':'exact_prompts_desc,f1_desc,false_positives_asc,key_asc',
        'fresh_ranking_not_certification':sorted(metrics,key=lambda name:(
            -metrics[name]['exact_prompts'],-metrics[name]['f1'],
            metrics[name]['false_positive_objects'],name)),
        'old_validation_dataset_sha256':info['validation_report']['validation_dataset_sha256'],
        'old_fresh_dataset_sha256':info['fresh_dataset_sha'],
        'validation_dataset_sha256':dataset_sha,
        'previous_action_ranking_report_sha256':ranking_hashes['action_ranking_report.json'],
        'previous_action_error_report_sha256':action_hashes['action_error_report.json'],
        'source_adapter_sha256':info['adapter_sha'],
        'head_sha256':manifest['head_sha256'],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'old_ablation_metrics':{
            'control':ranking['baseline_metrics'],
            'challenger':ranking['alternatives']['action_width_125']['metrics']},
        'one_feature_capture_per_prompt':True,'heads_frozen':True,
        'candidate_eligibility_unchanged':True,
        'production_decoder_unchanged':True,'no_training':True,
        'prior_train_and_development_prompt_overlap':0,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'production_promotion':False,'g2_unlocked':False,
        'interpretation':('Only the frozen null-focused +4.0 baseline and predeclared '
            'action-width 1.25 challenger were evaluated on seed-disjoint, '
            'prompt-disjoint, within-generator dev examples. This is not sealed '
            'test or out-of-distribution certification; no automatic promotion or G2 unlock.'),
    }
    output.mkdir(parents=True)
    (output/'action_width_validation_dev.jsonl').write_bytes(dataset_bytes)
    (output/'action_width_validation_per_case.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'action_width_validation_report.json').write_bytes(
        (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    return report
