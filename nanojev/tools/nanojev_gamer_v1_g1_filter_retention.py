"""G1-0027: original-format retention and thirteen-loss causal audit.

Replays frozen 0026 OOD predictions, audits the exact lost-gold spans, and
compares unchanged G1-0021 guard with G1-0026 filter on 640 original-format
examples (320 paired sources + 320 independently generated 0014 dev examples).
This is inference-only development evidence, not promotion or G2 approval.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import torch

from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, restore_source,
    verify_author_gold, WINNER_HEAD, CONTROL, GUARD, OOD_FILES, _file_sha,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _jsonl_bytes
from tools.nanojev_gamer_v1_g1_action_ranking import _candidate_scores
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g1.layout-filter-retention-and-loss-audit.v1'
REPORT_NAME='filter_retention_report.json'
CASES_NAME='filter_retention_per_case.jsonl'
LOSSES_NAME='filter_retention_losses.jsonl'
EXPECTED_KNOWN=320
EXPECTED_NEW=160
EXPECTED_LOSSES=13  # locked from completed 0026 experiment, not a decision threshold


def _sha_string(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines()
            if line.strip()]


def _gold(row):
    gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
    if len(gold)!=len(row['object_spans']):
        raise ValueError('GAMER_G1_0027_DUPLICATE_GOLD')
    return gold


def load_0026(layout_run, info, ood_hashes, ood_rows):
    """Bind to the immutable 0026 result and independently reauthor its new rows."""
    path=Path(layout_run)
    paths={name:path/name for name in (
        'layout_filter_report.json','layout_filter_per_case.jsonl',
        'layout_filter_new_layouts.jsonl')}
    hashes={name:_file_sha(file) for name,file in paths.items()}
    report=json.loads(paths['layout_filter_report.json'].read_text(encoding='utf-8'))
    cases=_read_jsonl(paths['layout_filter_per_case.jsonl'])
    new=_read_jsonl(paths['layout_filter_new_layouts.jsonl'])
    checks={
        'schema':(report.get('schema'),f.SCHEMA),
        'status':(report.get('status'),'INFERENCE_ONLY_DEVELOPMENT_FILTER_NOT_CERTIFICATION'),
        'known':(report.get('known_layout_cases'),EXPECTED_KNOWN),
        'new':(report.get('new_layout_cases'),EXPECTED_NEW),
        'layouts':(report.get('new_layouts'),list(f.NOVEL_LAYOUTS)),
        'new_seed':(report.get('new_layout_seed'),f.NOVEL_SEED),
        'ood_report_sha':(report.get('ood_report_file_sha256'),ood_hashes[OOD_FILES[0]]),
        'ood_dataset_sha':(report.get('ood_dataset_file_sha256'),ood_hashes[OOD_FILES[1]]),
        'adapter_sha':(report.get('source_adapter_sha256'),info['adapter_sha']),
        'checkpoint_sha':(report.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
        'new_dataset_sha':(report.get('new_layout_dataset_sha256'),
                           hashlib.sha256(_jsonl_bytes(new)).hexdigest()),
        'configurations':(report.get('configurations_predeclared'),[CONTROL,GUARD,f.CHALLENGER]),
        'gold_rejected':(report.get('rule_counts_all',{}).get('gold_proposals_rejected'),0),
        'previously_correct_lost':(
             report.get('rule_counts_all',{}).get('gold_objects_lost_vs_guard'),EXPECTED_LOSSES),
        'cases':(len(cases),EXPECTED_KNOWN+EXPECTED_NEW),
    }
    for label,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0027_0026_REFERENCE_DRIFT:'+label)
    for flag in ('no_training','heads_frozen','decoder_guard_unchanged',
                 'gold_used_after_inference_only','experimental_checks_met'):
        if report.get(flag) is not True:
            raise ValueError('GAMER_G1_0027_0026_REFERENCE_DRIFT:'+flag)
    for flag in ('promotion_authorized','certification_authorized','g2_unlocked',
                 'sealed_test_used','challenge_or_test_split_used'):
        if report.get(flag) is not False:
            raise ValueError('GAMER_G1_0027_0026_REFERENCE_DRIFT:'+flag)
    reauthored=f.fresh_layout_rows(info,ood_rows)
    if _jsonl_bytes(reauthored)!=_jsonl_bytes(new):
        raise ValueError('GAMER_G1_0027_0026_NEW_LAYOUT_LINEAGE_DRIFT')
    return report, cases, new, paths, hashes


def _case_summary(guesses,gold):
    return {name:{'correct':len(pred&gold),'missing':len(gold-pred),
                  'false_positives':len(pred-gold),'exact':pred==gold}
            for name,pred in guesses.items()}


def _counters(guesses,gold,rejected,kept):
    result=Counter({'proposals_rejected':len(rejected),'proposals_kept':kept,
       'gold_proposals_rejected':sum((*r['bounds'],r['kind']) in gold
                     for r in rejected if r['bounds'] is not None),
       'gold_objects_lost_vs_guard':len((guesses[GUARD]&gold)-guesses[f.CHALLENGER]),
       'gold_objects_recovered_vs_guard':len((guesses[f.CHALLENGER]&gold)-guesses[GUARD]),
       'false_positives_removed_vs_guard':len((guesses[GUARD]-gold)-guesses[f.CHALLENGER]),
       'false_positives_added_vs_guard':len((guesses[f.CHALLENGER]-gold)-guesses[GUARD])})
    for r in rejected:
        result['rejected.'+r['reason']]+=1
        result['rejected_kind.'+r['kind']]+=1
        result['rejected_reason_kind.'+r['reason']+'.'+r['kind']]+=1
    return result


def _overlap(a,b):
    return a[0]<b[1] and b[0]<a[1]


def diagnose_losses(row, group, index, gold, guesses, rejected, prepared, prediction):
    """Gold enters only after the frozen inference and decoder selections."""
    lost=sorted((guesses[GUARD]&gold)-guesses[f.CHALLENGER])
    if not lost:return []
    logits=prediction['span_logits']; valid=prediction['span_valid']
    shifted=logits.detach().float().clone();shifted[...,0]-=f.SHIFT
    scored=_candidate_scores(shifted,valid,confidence=.50)
    surviving,_,_=f.filter_by_end(scored,prepared)
    proposal_by_key={}
    for by_end in surviving:
        for first,offset,klass,margin in by_end:
            kind=SPAN_KINDS[klass-1]
            bounds=token_span_char_bounds({'kind':kind,'token_first':first,
                'token_last':first+offset},prepared)
            if bounds is not None:
                proposal_by_key[(*bounds,kind)]=float(margin)
    records=[]
    for g in lost:
        selected_new=sorted(p for p in guesses[f.CHALLENGER] if _overlap(p,g))
        rejected_overlap=[r for r in rejected if r['bounds'] is not None
                          and _overlap(r['bounds'],g)]
        if g not in proposal_by_key:
            raise ValueError('GAMER_G1_0027_PREVIOUSLY_SELECTED_GOLD_CANDIDATE_REMOVED')
        records.append({'group':group,'index':index,'family':row['family'],
          'layout':row.get('surface_shift','original'),
          'prompt_sha256':_sha_string(row['prompt']),
          'gold_span':list(g[:2]),'kind':g[2],
          'gold_surface':row['prompt'][g[0]:g[1]],
          'surviving_gold_margin':round(proposal_by_key[g],6),
          'newly_selected_overlapping_spans':[
              {'span':list(p[:2]),'kind':p[2],
               'surface':row['prompt'][p[0]:p[1]],'is_gold':p in gold,
               'margin':(round(proposal_by_key[p],6) if p in proposal_by_key else None)}
              for p in selected_new],
          'rejected_overlapping_count':len(rejected_overlap),
          'rejected_overlapping_reasons':dict(sorted(Counter(
              r['reason'] for r in rejected_overlap).items())),
          'cause_class':('new_selected_span_competes_with_gold' if selected_new
                         else 'decoder_selection_changed_without_direct_overlap'),
          'not_directly_rejected':True})
    return records


def _capture(head,model,tok,backbone,row,device,max_length):
    prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
    hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
        device='cpu',dtype=torch.float16).contiguous()
    pred=head(hidden.to(device=device,dtype=torch.float32),
        eligible=prepared['eligible'].to(device),active_stage='G1')
    guesses,rejected,kept=f.frozen_decode(pred,prepared)
    return guesses,rejected,kept,prepared,pred


def _evaluate_group(name,rows,head,model,tok,backbone,device,max_length,
                    *,expected_cases=None,expected_group=None,case_index_offset=0):
    records=[];per_case=[];losses=[];stats=Counter();layout_stats={};layout_records={}
    for index,row in enumerate(rows,1):
        if row['split'] not in ('dev','ood_dev') or row['family'] not in ('encounter','allocation'):
            raise ValueError('GAMER_G1_0027_INVALID_DEV_ROW')
        guesses,rejected,kept,prepared,pred=_capture(
            head,model,tok,backbone,row,device,max_length)
        gold=_gold(row)
        summary=_case_summary(guesses,gold)
        counters=_counters(guesses,gold,rejected,kept)
        layout=row.get('surface_shift','original')
        if expected_cases is not None:
            saved=expected_cases[case_index_offset+index-1]
            invariant={
                'group':('known_layout' if name=='known_layout_replay' else 'new_layout'),
                'index':index,'layout':layout,
                'family':row['family'],'prompt_sha256':_sha_string(row['prompt']),
                'gold_count':len(gold),'rejections':len(rejected),
                'rejected_gold_proposals':counters['gold_proposals_rejected'],
                'configurations':summary}
            if saved!=invariant:
                raise ValueError('GAMER_G1_0027_0026_CASE_REPLAY_DRIFT:'+name+':'+str(index))
        records.append((guesses,gold));stats.update(counters)
        layout_stats.setdefault(layout,Counter()).update(counters)
        layout_records.setdefault(layout,([],[]))
        layout_records[layout][0].append((guesses,gold))
        layout_records[layout][1].append(row)
        losses.extend(diagnose_losses(row,name,index,gold,guesses,rejected,prepared,pred))
        per_case.append({'group':name,'index':index,'layout':layout,
             'family':row['family'],'prompt_sha256':_sha_string(row['prompt']),
             'gold_count':len(gold),'configurations':summary,
             'gold_proposals_rejected':counters['gold_proposals_rejected'],
             'gold_objects_lost_vs_guard':counters['gold_objects_lost_vs_guard'],
             'gold_objects_recovered_vs_guard':counters['gold_objects_recovered_vs_guard']})
        if index==1 or index%40==0 or index==len(rows):
            print(f'GAMER_G1_FILTER_RETENTION {name} {index}/{len(rows)}',flush=True)
    result=f._assess(records,rows,stats)
    result['by_layout']={layout:f._assess(*layout_records[layout],layout_stats[layout])
                         for layout in sorted(layout_records)}
    if expected_group is not None:
        for key in ('metrics','paired_vs_frozen_guard','filter_diagnostics'):
            if result[key]!=expected_group[key]:
                raise ValueError('GAMER_G1_0027_0026_GROUP_REPLAY_DRIFT:'+name+':'+key)
        if result['by_layout']!=expected_group['by_layout']:
            raise ValueError('GAMER_G1_0027_0026_LAYOUT_REPLAY_DRIFT:'+name)
    return result,per_case,losses


def _retention_assessment(result):
    guard=result['metrics'][GUARD];filtered=result['metrics'][f.CHALLENGER]
    paired=result['paired_vs_frozen_guard']
    return {'no_f1_regression':filtered['f1']>=guard['f1'],
        'no_exact_prompt_regression':filtered['exact_prompts']>=guard['exact_prompts'],
        'no_missing_object_regression':filtered['missing_objects']<=guard['missing_objects'],
        'no_false_positive_regression':filtered['false_positive_objects']<=guard['false_positive_objects'],
        'no_previously_correct_gold_lost':paired['b_previously_correct_gold_lost']==0}


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        layout_run,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0027_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0027_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    ood_report,ood_rows,_,ood_files,ood_hashes=_load_0022(ood_run,info)
    report_26,cases_26,new,files_26,hashes_26=load_0026(
        layout_run,info,ood_hashes,ood_rows)
    original=[restore_source(row)[0] for row in ood_rows]
    verify_author_gold(ood_rows,original)
    if len(original)!=EXPECTED_KNOWN or len(info['validation_rows'])!=EXPECTED_KNOWN:
        raise ValueError('GAMER_G1_0027_WRONG_ORIGINAL_COUNTS')
    if any(row['split']!='dev' for row in original+info['validation_rows']):
        raise ValueError('GAMER_G1_0027_NON_DEV_SOURCE')
    if len({row['prompt'] for row in original+info['validation_rows']})!=640:
        raise ValueError('GAMER_G1_0027_ORIGINAL_PROMPT_OVERLAP')
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0027_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0027_HEAD_CHANGED')
    head.eval();model,tok=backbone.model_components(
        manifest['model_id'],manifest['source_adapter'],device)
    groups={'original_paired_sources':original,
            'original_independent_0014':info['validation_rows'],
            'known_layout_replay':ood_rows,
            'new_layout_replay':new}
    results={};per_case=[];losses=[]
    with torch.inference_mode():
        for name,rows in groups.items():
            replay=name.endswith('_replay')
            result,case_rows,loss_rows=_evaluate_group(
                name,rows,head,model,tok,backbone,device,max_length,
                expected_cases=(cases_26 if replay else None),
                expected_group=(report_26['groups']['known_layout' if name=='known_layout_replay'
                    else 'new_layout'] if replay else None),
                case_index_offset=(EXPECTED_KNOWN if name=='new_layout_replay' else 0))
            results[name]=result
            per_case.extend(case_rows);losses.extend(loss_rows)
    if results['original_paired_sources']['metrics'][CONTROL]!=report_26['source_control_reference']:
        raise ValueError('GAMER_G1_0027_ORIGINAL_SOURCE_BASELINE_DRIFT')
    if results['original_independent_0014']['metrics'][CONTROL]!=info['validation_report']['metrics']['null_focused_4']:
        raise ValueError('GAMER_G1_0027_0014_BASELINE_DRIFT')
    replay_losses=[r for r in losses if r['group'].endswith('_replay')]
    if len(replay_losses)!=EXPECTED_LOSSES:
        raise ValueError('GAMER_G1_0027_PREVIOUS_LOSSES_NOT_REPRODUCED')
    checks={name:_retention_assessment(results[name]) for name in (
        'original_paired_sources','original_independent_0014')}
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for name,path in ood_files.items():
        if _file_sha(path)!=ood_hashes[name]:
            raise ValueError('GAMER_G1_0027_OOD_FILE_CHANGED:'+name)
    for name,path in files_26.items():
        if _file_sha(path)!=hashes_26[name]:
            raise ValueError('GAMER_G1_0027_0026_FILE_CHANGED:'+name)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0027_CHECKPOINT_CHANGED')
    report={'schema':SCHEMA,'status':'READ_ONLY_G1_0027_RETENTION_AND_LOSS_AUDIT',
       'groups':results,'original_retention_checks':checks,
       'original_retention_pass':all(all(check.values()) for check in checks.values()),
       'losses_replayed':len(replay_losses),
       'lost_span_causes':dict(sorted(Counter(r['cause_class'] for r in replay_losses).items())),
       'losses_by_layout':dict(sorted(Counter(r['layout'] for r in replay_losses).items())),
       'filter_0026_sha256':hashes_26['layout_filter_report.json'],
       'filter_0026_input_file_sha256':hashes_26,
       'ood_input_file_sha256':ood_hashes,
       'original_validation_report_sha256':info['validation_file_sha256']['validation_report.json'],
       'source_adapter_sha256':info['adapter_sha'],
       'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
       'configurations_predeclared':[CONTROL,GUARD,f.CHALLENGER],
       'frozen_0026_replay_exact':True,'gold_used_after_inference_only':True,
       'heads_frozen':True,'no_training':True,'production_promotion':False,
       'certification_authorized':False,'challenge_or_test_split_used':False,
       'sealed_test_used':False,'g2_unlocked':False,
       'interpretation':('Original-prompt retention and exact replay of the known 0026 '
         'structural candidates, including every previously correct gold object lost. '
         'The filter, model, and decoder settings are unchanged. This experiment '
         'only diagnoses and does not authorize promotion.')}
    output.mkdir(parents=True)
    (output/REPORT_NAME).write_text(json.dumps(report,sort_keys=True,indent=2,
        allow_nan=False)+'\n',encoding='utf-8',newline='\n')
    (output/CASES_NAME).write_bytes(_jsonl_bytes(per_case))
    (output/LOSSES_NAME).write_bytes(_jsonl_bytes(losses))
    return report
