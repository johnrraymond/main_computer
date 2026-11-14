"""G1-0021: fixed boundary-guard replication on 320 unseen generator-dev prompts.

G1-0020 deliberately left its candidate field null: it did no automatic
promotion from previously inspected development data. This runner explicitly
nominates the existing boundary guard for *development validation only* after
checking that entire recorded experiment and its lineage. No tuning, training,
challenge/test split, production decoder change, certification, or G2 unlock.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import random

import torch

from tools.nanojev_gamer_v1_g1_action_width_validation import (
    CONTROL, CHALLENGER, SHIFT, _load_prior_inputs,
    _load_action_reference, _load_ranking_reference, _verify_no_drift,
    fresh_rows as prior_width_rows,
)
from tools.nanojev_gamer_v1_g1_action_conflict_diagnosis import (
    _load_width_reference, WIDTH_FILES, _read_jsonl,
)
from tools.nanojev_gamer_v1_g1_selective_conflict import (
    SCHEMA as SELECTIVE_SCHEMA, CONFLICT_FILES, RULES, ARMS,
    _read_conflict_reference, selective_decode,
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

SCHEMA = 'nanojev.gamer.graph.g1.frozen-boundary-guard-fresh-validation.v1'
SEED = 682021
GUARD = 'selective_boundary_guard'
MIN_SCORE_RETENTION = 0.85
GUARD_ENABLED = True
# These reference counts are pinned from G1-0020 before the new dataset is generated.
OLD_REFERENCE = {
    CONTROL: {'exact_prompts':122, 'correct':8540, 'expected':8820,
              'missing_objects':280, 'false_positive_objects':225},
    GUARD: {'exact_prompts':128, 'correct':8551, 'expected':8820,
            'missing_objects':269, 'false_positive_objects':214},
}
OLD_SIGNALS = {'beneficial_long_actions_accepted':11,
               'beneficial_long_actions_rejected':2,
               'harmful_long_actions_accepted':0,
               'harmful_long_actions_rejected':21}
FILES = ('selective_conflict_report.json', 'selective_conflict_cases.jsonl',
         'selective_conflict_decisions.jsonl', 'selective_conflict_signal_examples.jsonl')


def fresh_validation_rows(dataset, first_fresh_rows, prior_validation_rows,
                          width_rows, *, seed=SEED, cases=CASES):
    """Generate a precommitted balanced set excluding *all* earlier G1 prompts."""
    from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
    if type(seed) is not int or seed != SEED or type(cases) is not int or cases != CASES:
        raise ValueError('GAMER_G1_0021_GENERATION_SETTINGS_LOCKED')
    used = _original_prompts(dataset)
    for label, source, count in (('0011',first_fresh_rows,80),
                                  ('0014',prior_validation_rows,CASES),
                                  ('0018',width_rows,CASES)):
        if len(source) != count:
            raise ValueError('GAMER_G1_0021_PRIOR_COUNT:'+label)
        local=set()
        for row in source:
            if (not isinstance(row,dict) or row.get('split') != 'dev'
                    or row.get('family') not in FAMILIES
                    or not isinstance(row.get('prompt'),str)
                    or not row['prompt'] or row['prompt'] in local
                    or row['prompt'] in used):
                raise ValueError('GAMER_G1_0021_PRIOR_PROMPT_OVERLAP:'+label)
            local.add(row['prompt'])
        used.update(local)
    rng=random.Random(seed)
    result=[]
    for index in range(cases):
        generator=encounter if index%2==0 else allocation
        for _ in range(250):
            row=generator(rng,'dev',index)
            if (row.get('split')=='dev' and row.get('family')==FAMILIES[index%2]
                    and isinstance(row.get('prompt'),str)
                    and row['prompt'] and row['prompt'] not in used):
                break
        else:
            raise ValueError('GAMER_G1_0021_FRESH_PROMPTS_EXHAUSTED')
        result.append(row)
        used.add(row['prompt'])
    if (len(result)!=CASES or len({r['prompt'] for r in result})!=CASES or
        any(sum(r['family']==family for r in result)!=CASES//2 for family in FAMILIES)):
        raise ValueError('GAMER_G1_0021_FRESH_INVARIANT')
    return result


def _load_selective_reference(path, info, width_report, width_hashes,
                              conflict_hashes, old_rows):
    """Explicit nomination, not automatic selection from the inspected dev set."""
    path=Path(path)
    paths={name:path/name for name in FILES}
    hashes={name:_file_sha(p) for name,p in paths.items()}
    report=json.loads(paths[FILES[0]].read_text(encoding='utf-8'))
    if 'candidate_for_new_independent_validation' not in report:
        raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:candidate_field_missing')
    cases=_read_jsonl(paths[FILES[1]])
    decisions=_read_jsonl(paths[FILES[2]])
    signals=_read_jsonl(paths[FILES[3]])
    expected_rules=[{'name':name,'min_score_retention':ret,'boundary_guard':boundary}
                    for name,ret,boundary in RULES]
    check={
        'schema':(report.get('schema'),SELECTIVE_SCHEMA),
        'status':(report.get('status'),'SELECTIVE_CONFLICT_DEV_ONLY_NOT_CERTIFICATION'),
        'cases':(report.get('cases'),CASES),
        'families':(report.get('families'),{'encounter':160,'allocation':160}),
        'configuration_predeclared':(report.get('configuration_predeclared'),list(ARMS)),
        'rules_predeclared':(report.get('rules_predeclared'),expected_rules),
        'candidate_for_new_independent_validation':(report.get('candidate_for_new_independent_validation'),None),
        'source_width_report_sha256':(report.get('source_width_report_sha256'),width_hashes[WIDTH_FILES[0]]),
        'source_width_dataset_sha256':(report.get('source_width_dataset_sha256'),width_report['validation_dataset_sha256']),
        'source_conflict_report_sha256':(report.get('source_conflict_report_sha256'),conflict_hashes[CONFLICT_FILES[0]]),
        'source_adapter_sha256':(report.get('source_adapter_sha256'),info['adapter_sha']),
        'checkpoint_file_sha256':(report.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
        'control_metrics_replayed':(report.get('metrics',{}).get(CONTROL),width_report['metrics'][CONTROL]),
        'width_metrics_replayed':(report.get('metrics',{}).get(CHALLENGER),width_report['metrics'][CHALLENGER]),
        'candidate_signals':(report.get('diagnostic_signal_summary',{}).get(
            'gold_tagged_separability_after_inference_only',{}).get(GUARD),OLD_SIGNALS),
    }
    for field,(actual,expected) in check.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:'+field)
    for key in ('heads_frozen','candidate_eligibility_unchanged','production_decoder_unchanged',
                'one_feature_capture_per_prompt','no_training','gold_used_after_inference_only'):
        if report.get(key) is not True:
            raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:'+key)
    for key in ('challenge_or_test_split_used','certification_authorized',
                'production_promotion','g2_unlocked'):
        if report.get(key) is not False:
            raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:'+key)
    if set(report.get('metrics',{}))!=set(ARMS):
        raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:arms')
    for arm, counts in OLD_REFERENCE.items():
        metric=report['metrics'][arm]
        for name,expected in counts.items():
            if type(metric.get(name)) is not int or metric[name]!=expected:
                raise ValueError('GAMER_G1_0021_INVALID_SELECTIVE_REFERENCE:'+arm+'.'+name)
    old=report['metrics'][CONTROL]
    candidate=report['metrics'][GUARD]
    if not (candidate['exact_prompts']>old['exact_prompts'] and
            candidate['f1']>old['f1'] and
            candidate['missing_objects']<old['missing_objects'] and
            candidate['false_positive_objects']<old['false_positive_objects']):
        raise ValueError('GAMER_G1_0021_CANDIDATE_NOMINATION_GATE_FAILED')
    if len(cases)!=CASES or len(decisions)==0 or len(signals)!=34:
        raise ValueError('GAMER_G1_0021_SELECTIVE_ROWS_INVALID')
    totals={name:Counter() for name in ARMS}
    for index,(row, case) in enumerate(zip(old_rows,cases),1):
        sha=hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest()
        if (type(case.get('index')) is not int or case['index']!=index or
                case.get('family')!=row['family'] or case.get('prompt_sha256')!=sha or
                set(case.get('configurations',{}))!=set(ARMS)):
            raise ValueError('GAMER_G1_0021_SELECTIVE_CASE_MISMATCH:'+str(index))
        gold_count=len(row['object_spans'])
        for arm in ARMS:
            c=case['configurations'][arm]
            if (any(type(c.get(k)) is not int or c[k]<0 for k in
                    ('correct','missing','false_positives')) or
                    type(c.get('exact')) is not bool or
                    c['correct']+c['missing']!=gold_count or
                    c['exact']!=(c['missing']==0 and c['false_positives']==0)):
                raise ValueError('GAMER_G1_0021_SELECTIVE_CASE_ACCOUNTING:'+arm+':'+str(index))
            totals[arm].update(c)
            totals[arm]['exact_prompts']+=int(c['exact'])
    for arm, total in totals.items():
        for field,metric in (('correct','correct'),('missing','missing_objects'),
                             ('false_positives','false_positive_objects'),
                             ('exact_prompts','exact_prompts')):
            if total[field]!=report['metrics'][arm][metric]:
                raise ValueError('GAMER_G1_0021_SELECTIVE_TOTALS:'+arm+':'+metric)
    paired=report.get('paired_vs_control',{}).get(GUARD,{})
    if not isinstance(paired,dict):
        raise ValueError('GAMER_G1_0021_SELECTIVE_PAIRED_MISSING')
    only_base=only_guard=0
    for case in cases:
        a=case['configurations'][CONTROL]['exact']
        b=case['configurations'][GUARD]['exact']
        only_base+=int(a and not b)
        only_guard+=int(b and not a)
    checks={
        'a_only_exact':only_base, 'b_only_exact':only_guard,
        'exact_discordant_pairs':only_base+only_guard,
    }
    for field,expected in checks.items():
        if type(paired.get(field)) is not int or paired[field]!=expected:
            raise ValueError('GAMER_G1_0021_SELECTIVE_PAIRED:'+field)
    for gained,lost,delta in (
        ('b_newly_recovered_gold','b_previously_correct_gold_lost',
         totals[GUARD]['correct']-totals[CONTROL]['correct']),
        ('b_new_false_positives','b_old_false_positives_removed',
         totals[GUARD]['false_positives']-totals[CONTROL]['false_positives']),
    ):
        if (type(paired.get(gained)) is not int or type(paired.get(lost)) is not int
                or paired[gained]<0 or paired[lost]<0 or
                paired[gained]-paired[lost]!=delta):
            raise ValueError('GAMER_G1_0021_SELECTIVE_PAIRED:'+gained)
    by_rule=Counter()
    for d in decisions:
        if (type(d.get('index')) is not int or not 1<=d['index']<=CASES or
                d.get('rule') not in dict((n,None) for n,_,_ in RULES) or
                type(d.get('take_challenger')) is not bool):
            raise ValueError('GAMER_G1_0021_BAD_DECISION_ROW')
        by_rule[d['rule']]+=1
    if any(by_rule[name]==0 for name,_,_ in RULES):
        raise ValueError('GAMER_G1_0021_MISSING_RULE_DECISIONS')
    return report, paths, hashes


def _check_files_unchanged(paths,hashes,label):
    for name,path in paths.items():
        if _file_sha(path)!=hashes[name]:
            raise ValueError('GAMER_G1_0021_'+label+'_INPUT_CHANGED_DURING_RUN:'+name)


def run(*,root,transfer_report,fresh_run,null_run,validation_run,action_run,
        ranking_run,width_run,conflict_run,selective_run,output,
        device='cuda',max_length=2048):
    """One-shot, no-training fresh two-way validation, not sealed certification."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0021_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():
        raise FileExistsError('GAMER_G1_0021_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,action_paths,action_hashes=_load_action_reference(action_run,info)
    _,ranking_paths,ranking_hashes=_load_ranking_reference(ranking_run,info,action_hashes)
    old_rows=prior_width_rows(info['dataset'],info['fresh_rows'],info['validation_rows'])
    width_report,_,width_paths,width_hashes=_load_width_reference(
        width_run,old_rows,info,ranking_hashes,action_hashes)
    _,_,conflict_paths,conflict_hashes=_read_conflict_reference(
        conflict_run,width_report,width_hashes,old_rows)
    selective_report,selective_paths,selective_hashes=_load_selective_reference(
        selective_run,info,width_report,width_hashes,conflict_hashes,old_rows)
    rows=fresh_validation_rows(info['dataset'],info['fresh_rows'],
                               info['validation_rows'],old_rows)
    dataset_bytes=_jsonl_bytes(rows)
    dataset_sha=hashlib.sha256(dataset_bytes).hexdigest()
    entry=info['entries'][WINNER_HEAD]
    manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0021_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0021_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    records={CONTROL:[],GUARD:[]}
    per_case=[]
    decisions=[]
    with torch.inference_mode():
        for index,row in enumerate(rows,1):
            captured=cur.capture(model,tok,row,device,max_length)
            prepared=captured['prepared']
            prediction=head(captured['hidden'].to(device=device,dtype=torch.float32),
                            eligible=prepared['eligible'].to(device),active_stage='G1')
            logits=prediction['span_logits']; valid=prediction['span_valid']
            shifted=logits.detach().float().clone();shifted[...,0]-=SHIFT
            proposals=_candidate_scores(shifted,valid,confidence=.50)
            control=decode_action_ranked(proposals,action_power=1.0,action_penalty=0.0)
            if control!=_decode_shift(logits,valid,SHIFT):
                raise ValueError('GAMER_G1_0021_CONTROL_DECODER_DIVERGED')
            wide=decode_action_ranked(proposals,action_power=1.25,action_penalty=0.0)
            guarded,events=selective_decode(control,wide,proposals,
                          min_score_retention=MIN_SCORE_RETENTION,
                          boundary_guard=GUARD_ENABLED)
            # The decoder has already chosen. Gold labels enter *only* here.
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            predictions={CONTROL:_char_set(control,prepared),
                         GUARD:_char_set(guarded,prepared)}
            for name,guess in predictions.items():
                records[name].append((guess,gold))
            per_case.append({'index':index,'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest(),
                'gold_count':len(gold),'configurations':{name:{
                    'correct':len(guess&gold),'missing':len(gold-guess),
                    'false_positives':len(guess-gold),'exact':guess==gold}
                    for name,guess in predictions.items()}})
            for event in events:
                decisions.append({'index':index,**event})
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_BOUNDARY_GUARD_VALIDATION {index}/{CASES}',flush=True)
    metrics={name:_summarize(records[name],rows) for name in records}
    paired=_paired(records[CONTROL],records[GUARD])
    # Confirm that no reference, model, checkpoint, or adapter changed while capturing.
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],
                     action_paths,action_hashes,ranking_paths,ranking_hashes)
    for paths,hashes,label in ((width_paths,width_hashes,'WIDTH'),
                               (conflict_paths,conflict_hashes,'CONFLICT'),
                               (selective_paths,selective_hashes,'SELECTIVE')):
        _check_files_unchanged(paths,hashes,label)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0021_CHECKPOINT_CHANGED_DURING_RUN')
    baseline=metrics[CONTROL];challenger=metrics[GUARD]
    fresh_advantage={
        'more_exact_prompts':challenger['exact_prompts']>baseline['exact_prompts'],
        'higher_object_f1':challenger['f1']>baseline['f1'],
        'no_more_missing_objects':challenger['missing_objects']<=baseline['missing_objects'],
        'no_more_false_positives':challenger['false_positive_objects']<=baseline['false_positive_objects'],
    }
    report={
        'schema':SCHEMA,'status':'FRESH_WITHIN_GENERATOR_DEV_VALIDATION_NOT_CERTIFICATION',
        'seed':SEED,'cases':CASES,'families':{'encounter':160,'allocation':160},
        'split':'new_dev','configurations_predeclared':[
            {'key':CONTROL,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
             'action_width_power':1.0,'action_per_span_penalty':0.0},
            {'key':GUARD,'checkpoint':WINNER_HEAD,'null_logit_shift':SHIFT,
             'action_width_power_for_proposal':1.25,
             'min_score_retention':MIN_SCORE_RETENTION,
             'boundary_guard':GUARD_ENABLED}],
        'metrics':metrics,'paired_comparison':paired,
        'fresh_advantage_checks':fresh_advantage,
        'passes_predeclared_four_metric_guard':all(fresh_advantage.values()),
        'prior_candidate_was_null_by_design':True,
        'candidate_frozen_for_validation_only':GUARD,
        'old_dev_reference_metrics':{name:selective_report['metrics'][name]
                                     for name in (CONTROL,GUARD)},
        'new_dev_dataset_sha256':dataset_sha,
        'excluded_0018_dataset_sha256':width_report['validation_dataset_sha256'],
        'excluded_0014_dataset_sha256':info['validation_report']['validation_dataset_sha256'],
        'excluded_0011_dataset_sha256':info['fresh_dataset_sha'],
        'source_selective_report_sha256':selective_hashes[FILES[0]],
        'source_conflict_report_sha256':conflict_hashes[CONFLICT_FILES[0]],
        'source_width_report_sha256':width_hashes[WIDTH_FILES[0]],
        'source_adapter_sha256':info['adapter_sha'],
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'one_feature_capture_per_prompt':True,'heads_frozen':True,
        'candidate_eligibility_unchanged':True,'no_training':True,
        'production_decoder_unchanged':True,
        'challenge_or_test_split_used':False,'certification_authorized':False,
        'production_promotion':False,'g2_unlocked':False,
        'interpretation':('G1-0020 selected no candidate automatically by design. A fixed '
            'boundary-guard challenger was explicitly nominated after the prior dev analysis, '
            'then compared with its frozen baseline on new, prompt-disjoint within-generator '
            'development examples. This is not out-of-distribution or sealed test validation, '
            'certification, production promotion, or G2 authorization.'),
    }
    output.mkdir(parents=True)
    (output/'boundary_guard_validation_dev.jsonl').write_bytes(dataset_bytes)
    (output/'boundary_guard_validation_per_case.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'boundary_guard_validation_decisions.jsonl').write_bytes(_jsonl_bytes(decisions))
    (output/'boundary_guard_validation_report.json').write_bytes(
        (json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8'))
    return report
