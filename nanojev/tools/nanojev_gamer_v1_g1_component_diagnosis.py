"""G1-0029: causal audit of the 13 frozen G1-0027 decoder-competition losses.

Recompute only the thirteen affected development examples, reproduce the three
frozen decoders plus both failed G1-0028 arms, then record actual token
components, proposal membership, margins and stage decisions. Gold is inspected
only after every inference/decoder decision. No decoder intervention is applied.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import torch

from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_filter_retention as r
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_g1_action_ranking import _candidate_scores, decode_action_ranked
from tools.nanojev_gamer_v1_g1_selective_conflict import _components, _proposal_map, selective_decode
from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, WINNER_HEAD,
    CONTROL, GUARD, _file_sha,
)
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
from tools.nanojev_gamer_v1_g1_calibration_validation import _jsonl_bytes

SCHEMA = 'nanojev.gamer.graph.g1.decoder-component-causal-audit.v1'
REPORT = 'component_diagnosis_report.json'
LOSSES = 'component_diagnosis_losses.jsonl'
SOURCE_FILES = (c.REPORT,c.CASES,c.LOSSES,c.FRESH_ROWS)
EXPECTED_LOSSES = 13


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def load_0028(competition_run,info,hash26,hash27,hash22,losses27):
    """Refuse non-matching 0028 evidence, including its unsuccessful rescue."""
    base=Path(competition_run)
    paths={name:base/name for name in SOURCE_FILES}
    hashes={name:_file_sha(path) for name,path in paths.items()}
    report=json.loads(paths[c.REPORT].read_text(encoding='utf-8'))
    cases=c._jsonl(paths[c.CASES]);lost=c._jsonl(paths[c.LOSSES]);novel=c._jsonl(paths[c.FRESH_ROWS])
    expected={
        'schema':c.SCHEMA,'status':'INFERENCE_ONLY_DEVELOPMENT_NO_PROMOTION',
        'prior_losses_replayed':EXPECTED_LOSSES,
        'prior_losses_recovered':{key:0 for key in c.RATIOS},
        'newly_lost_gold_by_arm':{key:0 for key in c.RATIOS},
        'primary_candidate_meets_all_development_gates':False,
        'retention_0027_file_sha256':hash27,
        'layout_0026_file_sha256':hash26,
        'ood_0022_file_sha256':hash22,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'configurations_predeclared':[CONTROL,GUARD,f.CHALLENGER,*c.RATIOS],
    }
    for name,value in expected.items():
        if type(report.get(name)) is not type(value) or report.get(name)!=value:
            raise ValueError('GAMER_G1_0029_0028_REFERENCE_DRIFT:'+name)
    for name in ('heads_frozen','no_training','gold_used_after_inference_only',
                 'original_retention_pass','independent_validation_pass'):
        if report.get(name) is not True:
            raise ValueError('GAMER_G1_0029_0028_REFERENCE_DRIFT:'+name)
    for name in ('production_promotion','certification_authorized','g2_unlocked',
                 'sealed_test_used','challenge_or_test_split_used'):
        if report.get(name) is not False:
            raise ValueError('GAMER_G1_0029_0028_REFERENCE_DRIFT:'+name)
    if len(cases)!=1440 or len(lost)!=EXPECTED_LOSSES or len(novel)!=c.INDEPENDENT_CASES:
        raise ValueError('GAMER_G1_0029_0028_ROW_DRIFT')
    if hashlib.sha256(_jsonl_bytes(novel)).hexdigest()!=report['independent_dataset_sha256']:
        raise ValueError('GAMER_G1_0029_0028_INDEPENDENT_DATASET_DRIFT')
    lookup={(x['group'],x['index'],x['kind'],tuple(x['gold_span'])):x for x in losses27}
    seen=set()
    for loss in lost:
        key=(loss['group'],loss['index'],loss['kind'],tuple(loss['gold_span']))
        if key not in lookup or key in seen or loss['recovered']!={arm:False for arm in c.RATIOS}:
            raise ValueError('GAMER_G1_0029_0028_LOSS_LINEAGE_DRIFT')
        seen.add(key)
    return report,{(x['group'],x['index']):x for x in cases},paths,hashes


def frozen_stages(pred,prepared):
    """Inference only. Return actual selections and selective gate decisions."""
    logits=pred['span_logits'];valid=pred['span_valid']
    shifted=logits.detach().float().clone();shifted[...,0]-=f.SHIFT
    proposal=_candidate_scores(shifted,valid,confidence=.50)
    original_base=decode_action_ranked(proposal,action_power=1.,action_penalty=0.)
    original_wide=decode_action_ranked(proposal,action_power=1.25,action_penalty=0.)
    guard,guard_decisions=selective_decode(original_base,original_wide,proposal,
        min_score_retention=f.MIN_SCORE_RETENTION,boundary_guard=f.GUARD_ENABLED)
    clean,rejected,kept=f.filter_by_end(proposal,prepared)
    filtered_base=decode_action_ranked(clean,action_power=1.,action_penalty=0.)
    filtered_wide=decode_action_ranked(clean,action_power=1.25,action_penalty=0.)
    filtered,filtered_decisions=selective_decode(filtered_base,filtered_wide,clean,
        min_score_retention=f.MIN_SCORE_RETENTION,boundary_guard=f.GUARD_ENABLED)
    baseline,other_rejected,other_kept,arbitration_signals=c.decode_competition_rescue(pred,prepared)
    if rejected!=other_rejected or kept!=other_kept:
        raise ValueError('GAMER_G1_0029_FILTER_REPLAY_DRIFT')
    if (_char_set(original_base,prepared)!=baseline[CONTROL] or
        _char_set(guard,prepared)!=baseline[GUARD] or
        _char_set(filtered,prepared)!=baseline[f.CHALLENGER]):
        raise ValueError('GAMER_G1_0029_SELECTION_REPLAY_DRIFT')
    return {'selected':{
        'original_base':original_base,'original_wide':original_wide,
        'guard':guard,'filtered_base':filtered_base,
        'filtered_wide':filtered_wide,'filtered':filtered},
        'guard_decisions':guard_decisions,'filtered_decisions':filtered_decisions,
        'all_catalog':_proposal_map(proposal),'filtered_catalog':_proposal_map(clean),
        'rejected':rejected,'kept_count':kept,'guesses':baseline}


def locate_loss_component(gold_key,guard,filtered,filtered_catalog):
    """Classify structural reasons G1-0028 could not restore a lost token span."""
    common,groups=_components(guard,filtered)
    if gold_key in common:
        raise ValueError('GAMER_G1_0029_GOLD_NOT_LOST')
    hits=[x for x in groups if gold_key in x['control']]
    if len(hits)!=1:
        raise ValueError('GAMER_G1_0029_MISSING_OR_DUPLICATE_COMPONENT')
    group=hits[0]
    older=group['control'];newer=group['challenger']
    missing=[key for key in older if key not in filtered_catalog]
    if not newer:
        cause='no_filtered_selection_in_component'
    elif len(older)!=1 or len(newer)>2:
        cause='complex_component_excluded_by_0028'
    elif missing:
        cause='removed_guard_partner_blocks_0028'
    else:
        old_margin=min(filtered_catalog[key] for key in older)
        new_margin=max(filtered_catalog[key] for key in newer)
        cause=('0028_confidence_gate_rejected_guard' if
            old_margin+1e-8<c.RATIOS[c.PRIMARY]*new_margin else
            '0028_should_have_restored_guard')
    return group,missing,cause


def describe_span(key,prepared,all_catalog,filtered_catalog,rejection_lookup,gold):
    start,end,kind=key
    bounds=token_span_char_bounds({'kind':kind,'token_first':start,'token_last':end},prepared)
    prompt=prepared['prompt']
    return {'token_first':start,'token_last':end,'token_width':end-start+1,
        'kind':kind,'char_span':list(bounds) if bounds is not None else None,
        'surface':prompt[bounds[0]:bounds[1]] if bounds is not None else None,
        'neural_margin':round(all_catalog[key],6) if key in all_catalog else None,
        'linear_interval_score':round(all_catalog[key]*(end-start+1),6) if key in all_catalog else None,
        'wide_interval_score':round(all_catalog[key]*((end-start+1)**(1.25 if kind=='action' else 1.)),6)
            if key in all_catalog else None,
        'survives_filter':key in filtered_catalog,
        'filter_rejection_reason':rejection_lookup.get(key),
        'gold_match':bool(bounds is not None and (*bounds,kind) in gold)}


def _hit_decision(decision,key):
    return (list(key) in decision.get('control',[]) or
            list(key) in decision.get('challenger',[]))


def examine_loss(old,row,prepared,stages,expected_case):
    """Post-inference gold audit. Never supply gold to any decoder."""
    prompt=row['prompt'];gold=r._gold(row)
    key=(*old['gold_span'],old['kind'])
    if (expected_case['prompt_sha256']!=_digest(prompt) or
        expected_case['layout']!=row['surface_shift'] or
        expected_case['family']!=row['family'] or
        old['prompt_sha256']!=_digest(prompt) or
        key not in gold):
        raise ValueError('GAMER_G1_0029_GOLD_CASE_DRIFT')
    guessed=stages['guesses']
    if key not in guessed[GUARD] or key in guessed[f.CHALLENGER]:
        raise ValueError('GAMER_G1_0029_LOSS_NOT_REPRODUCED')
    if key in guessed[c.PRIMARY] or key in guessed[c.STRICT]:
        raise ValueError('GAMER_G1_0029_0028_FAILURE_NOT_REPRODUCED')
    guard_keys=[(s['token_first'],s['token_last'],s['kind']) for s in stages['selected']['guard']
        if token_span_char_bounds(s,prepared)==tuple(old['gold_span']) and s['kind']==old['kind']]
    if len(guard_keys)!=1:
        raise ValueError('GAMER_G1_0029_GUARD_TOKEN_GOLD_MISMATCH')
    gkey=guard_keys[0]
    group,missing,cause=locate_loss_component(gkey,stages['selected']['guard'],
        stages['selected']['filtered'],stages['filtered_catalog'])
    if gkey not in stages['filtered_catalog']:
        raise ValueError('GAMER_G1_0029_PREVIOUSLY_VALID_GOLD_PROPOSAL_REJECTED')
    rejection={(x['token_first'],x['token_last'],x['kind']):x['reason']
               for x in stages['rejected']}
    all_catalog=stages['all_catalog'];clean_catalog=stages['filtered_catalog']
    sorted_keys=sorted(set(group['control']+group['challenger']))
    def details(keys):
        return [describe_span(x,prepared,all_catalog,clean_catalog,rejection,gold)
                for x in keys]
    def stage(name):
        return gkey in {(s['token_first'],s['token_last'],s['kind'])
                        for s in stages['selected'][name]}
    selected_overlap=sorted(p for p in guessed[f.CHALLENGER]
        if r._overlap(p,key))
    if not selected_overlap:
        raise ValueError('GAMER_G1_0029_EXPECTED_OVERLAPPING_FILTER_SELECTION')
    if sorted(tuple(x) for x in selected_overlap)!=sorted(
        (*obj['span'],obj['kind']) for obj in old['newly_selected_overlapping_spans']):
        raise ValueError('GAMER_G1_0029_0027_OVERLAP_DRIFT')
    def score_total(keys,score):
        return round(sum((all_catalog[k]*((k[1]-k[0]+1)**
            (1.25 if k[2]=='action' else 1.)) if score=='wide' else
            all_catalog[k]*(k[1]-k[0]+1)) for k in keys),6)
    decision_guard=[d for d in stages['guard_decisions'] if _hit_decision(d,gkey)]
    decision_filtered=[d for d in stages['filtered_decisions'] if any(
        _hit_decision(d,k) for k in group['challenger'])]
    return {'group':old['group'],'index':old['index'],'layout':old['layout'],
        'family':row['family'],'prompt_sha256':_digest(prompt),
        'gold_span':old['gold_span'],'kind':old['kind'],
        'gold_surface':prompt[key[0]:key[1]],
        'cause_class':cause,
        'gold_survived_candidate_filter':True,
        'guard_gold_token_selection':list(gkey),
        'stage_selected_gold':{name:stage(name) for name in stages['selected']},
        'guard_selective_gate_decisions':decision_guard,
        'filtered_selective_gate_decisions':decision_filtered,
        'component':{'guard_count':len(group['control']),
            'filtered_count':len(group['challenger']),
            'guard_selected':details(group['control']),
            'filtered_selected':details(group['challenger']),
            'removed_guard_member_count':len(missing),
            'removed_guard_members':details(missing),
            'guard_linear_score_sum':score_total(group['control'],'linear'),
            'filtered_linear_score_sum':score_total(group['challenger'],'linear'),
            'guard_wide_score_sum':score_total(group['control'],'wide'),
            'filtered_wide_score_sum':score_total(group['challenger'],'wide')},
        'direct_filtered_overlaps':[
            {'char_span':list(p[:2]),'kind':p[2],
             'surface':prompt[p[0]:p[1]],'is_gold':p in gold}
            for p in selected_overlap],
        'filtered_competitor_selected_count':len(sorted_keys),
        'evidence_scope':'POST_INFERENCE_GOLD_AUDIT_ONLY_NO_RULE_SELECTION'}


def summarize(records):
    if len(records)!=EXPECTED_LOSSES:
        raise ValueError('GAMER_G1_0029_WRONG_LOSS_COUNT')
    return {'count':len(records),
        'cause_classes':dict(sorted(Counter(x['cause_class'] for x in records).items())),
        'by_layout':dict(sorted(Counter(x['layout'] for x in records).items())),
        'gold_proposals_rejected':sum(not x['gold_survived_candidate_filter'] for x in records),
        'components_with_multiple_guard_spans':sum(x['component']['guard_count']>1 for x in records),
        'components_with_multiple_filter_spans':sum(x['component']['filtered_count']>1 for x in records),
        'components_with_removed_guard_member':sum(x['component']['removed_guard_member_count']>0 for x in records),
        'gold_selected_by_filtered_base':sum(x['stage_selected_gold']['filtered_base'] for x in records),
        'gold_selected_by_filtered_wide':sum(x['stage_selected_gold']['filtered_wide'] for x in records)}


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        layout_run,retention_run,competition_run,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0029_IMMUTABLE_OUTPUT')
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0029_INVALID_MAX_LENGTH')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,ood_rows,_,ood_paths,ood_hashes=_load_0022(ood_run,info)
    report26,cases26,new,paths26,hashes26=r.load_0026(layout_run,info,ood_hashes,ood_rows)
    report27,cases27,losses27,paths27,hashes27=c.load_retention(
        retention_run,info,hashes26['layout_filter_report.json'])
    report28,cases28,paths28,hashes28=load_0028(
        competition_run,info,hashes26,hashes27,ood_hashes,losses27)
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0029_ADAPTER_DRIFT')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0029_HEAD_DRIFT')
    head.eval();model,tok=backbone.model_components(
        manifest['model_id'],manifest['source_adapter'],device)
    groups={'known_layout_replay':ood_rows,'new_layout_replay':new}
    records=[]
    with torch.inference_mode():
        for idx,old in enumerate(sorted(losses27,key=lambda x:(x['group'],x['index'],x['kind'],x['gold_span'])),1):
            row=groups[old['group']][old['index']-1]
            if row['split']!='ood_dev':
                raise ValueError('GAMER_G1_0029_NON_DEV_CASE')
            prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
            hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
                device='cpu',dtype=torch.float16).contiguous()
            pred=head(hidden.to(device=device,dtype=torch.float32),
                      eligible=prepared['eligible'].to(device),active_stage='G1')
            stages=frozen_stages(pred,prepared)
            expected=cases28.get((old['group'],old['index']))
            if expected is None or expected['configurations']!=r._case_summary(
                    stages['guesses'],r._gold(row)):
                raise ValueError('GAMER_G1_0029_0028_FROZEN_CASE_DRIFT')
            record=examine_loss(old,row,prepared,stages,expected)
            records.append(record)
            print(f'GAMER_G1_COMPONENT_DIAGNOSIS {idx}/{EXPECTED_LOSSES}: '
                  f'{old["layout"]} {record["cause_class"]}',flush=True)
    counts=summarize(records)
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for paths,hashes in ((ood_paths,ood_hashes),(paths26,hashes26),
                         (paths27,hashes27),(paths28,hashes28)):
        for name,path in paths.items():
            if _file_sha(path)!=hashes[name]:
                raise ValueError('GAMER_G1_0029_INPUT_MUTATION:'+name)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
        info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
        info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0029_CHECKPOINT_MUTATION')
    report={'schema':SCHEMA,'status':'READ_ONLY_COMPONENT_DIAGNOSIS_NOT_INTERVENTION',
        'prior_losses_replayed':EXPECTED_LOSSES,'summary':counts,
        'source_0028_files_sha256':hashes28,'source_0027_files_sha256':hashes27,
        'source_0026_files_sha256':hashes26,'source_0022_files_sha256':ood_hashes,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'no_training':True,'heads_frozen':True,'gold_used_after_inference_only':True,
        'decoder_unchanged':True,'production_promotion':False,
        'certification_authorized':False,'challenge_or_test_split_used':False,
        'sealed_test_used':False,'g2_unlocked':False,
        'interpretation':('Thirteen-case causal audit of token-level overlap components and '
          'guard/filtered decoder decisions, including surviving and rejected proposals. '
          'Gold only labels already-computed predictions. Results identify possible next '
          'experiments, not an approved inference rule or promotion.')}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
        encoding='utf-8',newline='\n')
    (output/LOSSES).write_bytes(_jsonl_bytes(records))
    return report
