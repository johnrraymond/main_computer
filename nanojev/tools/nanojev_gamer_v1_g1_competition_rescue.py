"""G1-0028: gold-blind arbitration of surviving boundary-filter conflicts.

The previous 0026 filter and 0021 guard remain immutable references. This
experiment never edits checkpoints or production inference. Paired replay
anchors the known 13 lost objects, plus a prompt-disjoint development cohort
under two previously unused structural layouts. No sealed/test examples.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g1_filter_retention as r
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_g1_action_ranking import _candidate_scores, decode_action_ranked
from tools.nanojev_gamer_v1_g1_selective_conflict import selective_decode, _components, _proposal_map
from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, restore_source,
    verify_author_gold, WINNER_HEAD, CONTROL, GUARD, OOD_FILES, _file_sha,
)
from tools.nanojev_gamer_v1_g1_structural_ood import _pieces, SECTION_NAMES
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _jsonl_bytes
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_fresh_dev import _original_prompts
from tools.nanojev_gamer_v1_graph_contract import validate_graph, execute_graph

SCHEMA = 'nanojev.gamer.graph.g1.confidence-conflict-rescue-development.v1'
REPORT = 'competition_rescue_report.json'
CASES = 'competition_rescue_per_case.jsonl'
LOSSES = 'competition_rescue_prior_losses.jsonl'
FRESH_ROWS = 'competition_rescue_independent_layouts.jsonl'
PRIMARY = 'confidence_preserve_095_0028'
STRICT = 'confidence_preserve_110_0028'
RATIOS = {PRIMARY: .95, STRICT: 1.10}
SEED = 280028
INDEPENDENT_CASES = 320
INDEPENDENT_LAYOUTS = ('bracket_register', 'section_table')
EXPECTED_0027 = 13
EXPECTED_GROUP_SIZES = {
    'original_paired_sources':320,'original_independent_0014':320,
    'known_layout_replay':320,'new_layout_replay':160,
    'independent_structural_validation':INDEPENDENT_CASES,
}


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def load_retention(retention_run, info, report_26_hash):
    """Bind experiment to immutable G1-0027 and exact 13 losses."""
    path=Path(retention_run)
    paths={name:path/name for name in (r.REPORT_NAME,r.CASES_NAME,r.LOSSES_NAME)}
    hashes={name:_file_sha(p) for name,p in paths.items()}
    report=json.loads(paths[r.REPORT_NAME].read_text(encoding='utf-8'))
    cases=_jsonl(paths[r.CASES_NAME]);losses=_jsonl(paths[r.LOSSES_NAME])
    checks={
        'schema':(report.get('schema'),r.SCHEMA),
        'status':(report.get('status'),'READ_ONLY_G1_0027_RETENTION_AND_LOSS_AUDIT'),
        'filter_digest':(report.get('filter_0026_sha256'),report_26_hash),
        'adapter_digest':(report.get('source_adapter_sha256'),info['adapter_sha']),
        'checkpoint_digest':(report.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
        'replay_exact':(report.get('frozen_0026_replay_exact'),True),
        'original_retention':(report.get('original_retention_pass'),True),
        'losses':(report.get('losses_replayed'),EXPECTED_0027),
        'loss_rows':(len(losses),EXPECTED_0027),
        'case_rows':(len(cases),1120),
        'cause':(report.get('lost_span_causes'),{'new_selected_span_competes_with_gold':13}),
        'layouts':(report.get('losses_by_layout'),{'key_value_index':10,'ledger_grid':1,'markdown_note':2}),
    }
    for name,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0028_0027_REFERENCE_DRIFT:'+name)
    for key in ('no_training','heads_frozen','gold_used_after_inference_only'):
        if report.get(key) is not True:
            raise ValueError('GAMER_G1_0028_0027_REFERENCE_DRIFT:'+key)
    for key in ('production_promotion','certification_authorized','g2_unlocked',
                'challenge_or_test_split_used','sealed_test_used'):
        if report.get(key) is not False:
            raise ValueError('GAMER_G1_0028_0027_REFERENCE_DRIFT:'+key)
    group_by_index={(c['group'],c['index']):c for c in cases}
    if len(group_by_index)!=len(cases):
        raise ValueError('GAMER_G1_0028_0027_DUPLICATE_CASE')
    seen=set()
    for loss in losses:
        key=(loss['group'],loss['index'],loss['kind'],tuple(loss['gold_span']))
        if key in seen or loss.get('cause_class')!='new_selected_span_competes_with_gold' or not loss.get('not_directly_rejected'):
            raise ValueError('GAMER_G1_0028_0027_LOSS_REFERENCE_DRIFT')
        seen.add(key)
        case=group_by_index.get(key[:2])
        if case is None or case['prompt_sha256']!=loss['prompt_sha256'] or case['layout']!=loss['layout']:
            raise ValueError('GAMER_G1_0028_0027_LOSS_CASE_DRIFT')
    return report,group_by_index,losses,paths,hashes


def protect_surviving_guard(guard, filtered, filtered_proposals, *, margin_ratio):
    """Inference-only arbitration: protect a strong surviving guard choice.

    Never resurrect a proposal excluded by the 0026 grammar. Only replace
    competing spans in a disjoint conflict component if ONE original guard
    selection has >= ratio * the strongest competing neural margin.
    Crucially, this function receives no gold labels, prompt templates, or
    validation statistics; it uses only both decoders' selections and margins.
    """
    if type(margin_ratio) is not float or margin_ratio not in tuple(RATIOS.values()):
        raise ValueError('GAMER_G1_0028_UNDECLARED_RATIO')
    catalog=_proposal_map(filtered_proposals)
    common,groups=_components(guard,filtered)
    chosen=set(common);signals=Counter()
    for group in groups:
        older=group['control'];newer=group['challenger']
        if not older or not newer:
            chosen.update(newer)
            signals['disjoint_or_removed_component']+=1
            continue
        if len(older)!=1 or len(newer)>2:
            chosen.update(newer)
            signals['ambiguous_component']+=1
            continue
        if any(key not in catalog for key in older):
            chosen.update(newer)
            signals['filtered_guard_cannot_be_restored']+=1
            continue
        if any(key not in catalog for key in newer):
            raise ValueError('GAMER_G1_0028_SELECTED_NOT_IN_FILTERED_CATALOG')
        old_margin=min(catalog[k] for k in older)
        new_margin=max(catalog[k] for k in newer)
        if old_margin + 1e-8 >= margin_ratio*new_margin:
            chosen.update(older)
            signals['restored_guard_component']+=1
        else:
            chosen.update(newer)
            signals['retained_filter_component']+=1
    ordered=sorted(chosen)
    if len(ordered)>128:
        raise ValueError('GAMER_GRAPH_TOO_MANY_PREDICTED_OBJECTS')
    if any(left[1]>=right[0] for left,right in zip(ordered,ordered[1:])):
        raise ValueError('GAMER_G1_0028_RESCUE_OVERLAP')
    return ([{'kind':kind,'token_first':start,'token_last':end}
             for start,end,kind in ordered],signals)


def decode_competition_rescue(pred,prepared):
    """Reproduce 0026 exactly; add only a separate, experimental fourth arm."""
    baseline,rejected,kept=f.frozen_decode(pred,prepared)
    logits=pred['span_logits'];valid=pred['span_valid']
    shifted=logits.detach().float().clone();shifted[...,0]-=f.SHIFT
    proposals=_candidate_scores(shifted,valid,confidence=.50)
    unfiltered_base=decode_action_ranked(proposals,action_power=1.,action_penalty=0.)
    unfiltered_wide=decode_action_ranked(proposals,action_power=1.25,action_penalty=0.)
    guard,_=selective_decode(unfiltered_base,unfiltered_wide,proposals,
        min_score_retention=f.MIN_SCORE_RETENTION,boundary_guard=f.GUARD_ENABLED)
    clean,new_rejected,new_kept=f.filter_by_end(proposals,prepared)
    filtered_base=decode_action_ranked(clean,action_power=1.,action_penalty=0.)
    filtered_wide=decode_action_ranked(clean,action_power=1.25,action_penalty=0.)
    filtered,_=selective_decode(filtered_base,filtered_wide,clean,
        min_score_retention=f.MIN_SCORE_RETENTION,boundary_guard=f.GUARD_ENABLED)
    if (new_rejected!=rejected or new_kept!=kept or
        _char_set(guard,prepared)!=baseline[GUARD] or
        _char_set(filtered,prepared)!=baseline[f.CHALLENGER]):
        raise ValueError('GAMER_G1_0028_0026_DECODER_DRIFT')
    new={};diagnostics={}
    for name,ratio in RATIOS.items():
        selection,signals=protect_surviving_guard(guard,filtered,clean,margin_ratio=ratio)
        new[name]=_char_set(selection,prepared)
        diagnostics[name]=signals
    return {**baseline,**new},rejected,kept,diagnostics


def independent_layout_row(source,layout):
    """New, 0026-disjoint structural renderings with independently mapped gold."""
    if source.get('split')!='dev' or layout not in INDEPENDENT_LAYOUTS:
        raise ValueError('GAMER_G1_0028_INVALID_FRESH_SOURCE')
    count=len(SECTION_NAMES[source['family']]);sections=_pieces(source['prompt'],count)
    order=(tuple(range(1,count))+ (0,) if layout=='bracket_register'
           else tuple(range(0,count,2))+tuple(range(1,count,2)))
    head=('INDEX OF EVENTS\n\n' if layout=='bracket_register'
          else '---- REPORT BLOCKS ----\n\n')
    chunks=[head];pos=len(head);mapped={}
    for idx in order:
        start,end,part=sections[idx]
        name=SECTION_NAMES[source['family']][idx].title()
        prefix=(f'[{name}]\n' if layout=='bracket_register' else f'{name} ::\n')
        chunks.append(prefix);pos+=len(prefix)
        transformed,local=f._reshape_new(part,
            'markdown_note' if layout=='bracket_register' else 'key_value_index')
        for old,new in local.items():mapped[start+old]=pos+new
        chunks.append(transformed);pos+=len(transformed)
        chunks.append('\n\n');pos+=2
    text=''.join(chunks).rstrip()+'\n'
    result=deepcopy(source);result['prompt']=text;result['split']='ood_dev'
    result['template']='independent-layout-'+layout
    result['surface_shift']=layout;result['source_prompt_sha256']=_digest(source['prompt'])
    for node in result['graph']['nodes']:
        if 'span' not in node:continue
        a,b=node['span']
        if a not in mapped or b not in mapped or text[mapped[a]:mapped[b]]!=source['prompt'][a:b]:
            raise ValueError('GAMER_G1_0028_GOLD_MAP_DRIFT')
        node['span']=[mapped[a],mapped[b]]
    result['object_spans']=[{'kind':n['kind'],'start':n['span'][0],
         'end':n['span'][1]} for n in result['graph']['nodes'] if 'span' in n]
    validate_graph(result['graph'],complete=True)
    if execute_graph(result['graph'])['decisions']!=source['expected_decisions']:
        raise ValueError('GAMER_G1_0028_SEMANTIC_DRIFT')
    return result


def independent_rows(info,ood_rows,known_new):
    from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
    excluded={r['source_prompt_sha256'] for r in ood_rows+known_new}
    excluded.update(_digest(r['prompt']) for r in info['fresh_rows']+info['validation_rows'])
    excluded.update(_digest(prompt) for prompt in _original_prompts(info['dataset']))
    rng=random.Random(SEED);rows=[];seen=set()
    for idx in range(INDEPENDENT_CASES):
        generator=encounter if idx%2==0 else allocation
        layout=INDEPENDENT_LAYOUTS[(idx//2)%2]
        for _ in range(250):
            source=generator(rng,'dev',idx)
            sha=_digest(source['prompt'])
            if sha not in excluded and sha not in seen:break
        else:
            raise ValueError('GAMER_G1_0028_INDEPENDENT_PROMPTS_EXHAUSTED')
        seen.add(sha)
        rows.append(independent_layout_row(source,layout))
    if len({x['prompt'] for x in rows})!=INDEPENDENT_CASES:
        raise ValueError('GAMER_G1_0028_DUPLICATE_INDEPENDENT_PROMPT')
    return rows


def _pair(a,b,records):
    return f._paired_any([(g[a],gold) for g,gold in records],
                         [(g[b],gold) for g,gold in records])


def _metrics(records,rows):
    names=(CONTROL,GUARD,f.CHALLENGER,*RATIOS)
    return {name:_summarize([(guesses[name],gold) for guesses,gold in records],rows)
            for name in names}


def assess(records,rows,signals):
    return {'metrics':_metrics(records,rows),
       'paired_vs_filter':{name:_pair(f.CHALLENGER,name,records) for name in RATIOS},
       'arbitration_counts':{name:dict(sorted(signals[name].items())) for name in RATIOS}}


def evaluate_group(name,rows,head,model,tok,backbone,device,max_length,
                   *,old_cases=None,prior_losses=None):
    records=[];individual=[];restored=[];signals={name:Counter() for name in RATIOS}
    by_layout={};layout_rows={}
    for index,row in enumerate(rows,1):
        if row['split'] not in ('dev','ood_dev') or row['family'] not in ('encounter','allocation'):
            raise ValueError('GAMER_G1_0028_NON_DEVELOPMENT_ROW')
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
            device='cpu',dtype=torch.float16).contiguous()
        pred=head(hidden.to(device=device,dtype=torch.float32),
            eligible=prepared['eligible'].to(device),active_stage='G1')
        guesses,rejected,kept,local=decode_competition_rescue(pred,prepared)
        gold=r._gold(row)
        summary=r._case_summary(guesses,gold)
        if old_cases is not None:
            expected=old_cases.get((name,index))
            if (expected is None or expected['prompt_sha256']!=_digest(row['prompt']) or
                expected['gold_count']!=len(gold) or expected['configurations']!={
                    key:summary[key] for key in (CONTROL,GUARD,f.CHALLENGER)}):
                raise ValueError('GAMER_G1_0028_0027_REPLAY_DRIFT:'+name+':'+str(index))
        records.append((guesses,gold))
        layout=row.get('surface_shift','original')
        by_layout.setdefault(layout,[]).append((guesses,gold))
        layout_rows.setdefault(layout,[]).append(row)
        for arm in RATIOS:signals[arm].update(local[arm])
        individual.append({'group':name,'index':index,'layout':layout,'family':row['family'],
            'prompt_sha256':_digest(row['prompt']),
            'configurations':summary,'proposals_rejected':len(rejected),
            'gold_proposals_rejected':sum((*item['bounds'],item['kind']) in gold
                                      for item in rejected if item['bounds'] is not None)})
        if prior_losses is not None:
            for old in prior_losses.get((name,index),[]):
                key=(*old['gold_span'],old['kind'])
                if key not in guesses[GUARD]&gold or key in guesses[f.CHALLENGER]:
                    raise ValueError('GAMER_G1_0028_PREVIOUS_LOSS_NOT_REPRODUCED')
                restored.append({'group':name,'index':index,'layout':layout,
                    'kind':old['kind'],'gold_span':old['gold_span'],
                    'recovered':{arm:key in guesses[arm] for arm in RATIOS}})
        if index==1 or index%40==0 or index==len(rows):
            print(f'GAMER_G1_COMPETITION_RESCUE {name} {index}/{len(rows)}',flush=True)
    result=assess(records,rows,signals)
    result['by_layout']={key:{'metrics':_metrics(by_layout[key],layout_rows[key]),
        'paired_vs_filter':{arm:_pair(f.CHALLENGER,arm,by_layout[key]) for arm in RATIOS}}
        for key in sorted(by_layout)}
    return result,individual,restored


def _retention(result):
    baseline=result['metrics'][f.CHALLENGER]
    challenger=result['metrics'][PRIMARY]
    paired=result['paired_vs_filter'][PRIMARY]
    return {'no_exact_prompt_regression':challenger['exact_prompts']>=baseline['exact_prompts'],
        'no_f1_regression':challenger['f1']>=baseline['f1'],
        'no_missing_object_regression':challenger['missing_objects']<=baseline['missing_objects'],
        'no_false_positive_regression':challenger['false_positive_objects']<=baseline['false_positive_objects'],
        'no_previously_correct_gold_lost':paired['b_previously_correct_gold_lost']==0}


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        layout_run,retention_run,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0028_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0028_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,ood_rows,_,ood_paths,ood_hashes=_load_0022(ood_run,info)
    report26,cases26,new,paths26,hashes26=r.load_0026(layout_run,info,ood_hashes,ood_rows)
    report27,cases27,losses27,paths27,hashes27=load_retention(
        retention_run,info,hashes26['layout_filter_report.json'])
    prior_by_group={}
    for row in losses27:prior_by_group.setdefault((row['group'],row['index']),[]).append(row)
    sources=[restore_source(row)[0] for row in ood_rows]
    verify_author_gold(ood_rows,sources)
    if len(sources)!=320 or len(info['validation_rows'])!=320 or len(new)!=160:
        raise ValueError('GAMER_G1_0028_PRIOR_COUNTS_DRIFT')
    novel=independent_rows(info,ood_rows,new)
    if any(x['split']!='dev' for x in sources+info['validation_rows']):
        raise ValueError('GAMER_G1_0028_INVALID_ORIGINAL_SPLIT')
    if len({x['prompt'] for x in sources+info['validation_rows']+ood_rows+new+novel})!=1440:
        raise ValueError('GAMER_G1_0028_PROMPT_REUSE')
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0028_ADAPTER_DRIFT')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0028_HEAD_DRIFT')
    head.eval();model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    groups={'original_paired_sources':sources,
            'original_independent_0014':info['validation_rows'],
            'known_layout_replay':ood_rows,'new_layout_replay':new,
            'independent_structural_validation':novel}
    results={};per_case=[];lost=[]
    with torch.inference_mode():
        for name,rows in groups.items():
            if len(rows)!=EXPECTED_GROUP_SIZES[name]:
                raise ValueError('GAMER_G1_0028_WRONG_GROUP_SIZE:'+name)
            group,individual,restored=evaluate_group(name,rows,head,model,tok,backbone,
                device,max_length,old_cases=cases27 if name!='independent_structural_validation' else None,
                prior_losses=prior_by_group)
            results[name]=group;per_case.extend(individual);lost.extend(restored)
            if name in report27['groups']:
                for arm in (CONTROL,GUARD,f.CHALLENGER):
                    if group['metrics'][arm]!=report27['groups'][name]['metrics'][arm]:
                        raise ValueError('GAMER_G1_0028_FROZEN_REPLAY_METRIC_DRIFT:'+name+':'+arm)
    if len(lost)!=EXPECTED_0027:
        raise ValueError('GAMER_G1_0028_LOSSES_NOT_REPLAYED')
    original_checks={name:_retention(results[name]) for name in (
        'original_paired_sources','original_independent_0014')}
    structural_checks={name:{
        'no_exact_prompt_regression':results[name]['metrics'][PRIMARY]['exact_prompts']>=results[name]['metrics'][f.CHALLENGER]['exact_prompts'],
        'no_f1_regression':results[name]['metrics'][PRIMARY]['f1']>=results[name]['metrics'][f.CHALLENGER]['f1'],
        'no_missing_object_regression':results[name]['metrics'][PRIMARY]['missing_objects']<=results[name]['metrics'][f.CHALLENGER]['missing_objects'],
        'no_false_positive_regression':results[name]['metrics'][PRIMARY]['false_positive_objects']<=results[name]['metrics'][f.CHALLENGER]['false_positive_objects'],
       } for name in ('known_layout_replay','new_layout_replay','independent_structural_validation')}
    recovery={arm:sum(x['recovered'][arm] for x in lost) for arm in RATIOS}
    lost_by_arm={arm:sum(results[name]['paired_vs_filter'][arm]['b_previously_correct_gold_lost']
        for name in ('known_layout_replay','new_layout_replay','independent_structural_validation')) for arm in RATIOS}
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for paths,hashes in ((ood_paths,ood_hashes),(paths26,hashes26),(paths27,hashes27)):
        for name,path in paths.items():
            if _file_sha(path)!=hashes[name]:
                raise ValueError('GAMER_G1_0028_INPUT_MUTATION:'+name)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0028_CHECKPOINT_MUTATION')
    report={'schema':SCHEMA,'status':'INFERENCE_ONLY_DEVELOPMENT_NO_PROMOTION',
        'groups':results,'prior_losses_replayed':len(lost),
        'prior_losses_recovered':recovery,'newly_lost_gold_by_arm':lost_by_arm,
        'original_retention_checks':original_checks,
        'original_retention_pass':all(all(x.values()) for x in original_checks.values()),
        'structural_checks':structural_checks,
        'independent_validation_pass':all(structural_checks['independent_structural_validation'].values()),
        'primary_candidate_meets_all_development_gates':(
            recovery[PRIMARY]==EXPECTED_0027 and lost_by_arm[PRIMARY]==0 and
            all(all(x.values()) for x in original_checks.values()) and
            all(all(x.values()) for x in structural_checks.values())),
        'candidate_rules_predeclared':{'primary':PRIMARY,'strict_control':STRICT,
            'margin_ratios':RATIOS,'single_surviving_guard_span':True,
            'max_competing_spans':2,'gold_blind':True},
        'independent_seed':SEED,'independent_cases':INDEPENDENT_CASES,
        'independent_layouts':list(INDEPENDENT_LAYOUTS),
        'independent_dataset_sha256':hashlib.sha256(_jsonl_bytes(novel)).hexdigest(),
        'retention_0027_file_sha256':hashes27,
        'layout_0026_file_sha256':hashes26,
        'ood_0022_file_sha256':ood_hashes,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'configurations_predeclared':[CONTROL,GUARD,f.CHALLENGER,*RATIOS],
        'heads_frozen':True,'no_training':True,'gold_used_after_inference_only':True,
        'production_promotion':False,'certification_authorized':False,
        'challenge_or_test_split_used':False,'sealed_test_used':False,'g2_unlocked':False,
        'interpretation':('Fixed neural-margin arbitration between the frozen guard and the '
            '0026 layout filter, with a distinct independent development layout cohort. '
            'Whether the thirteen known losses are recovered is reported, not assumed. '
            'No training, selection on sealed data, certification or production promotion.')}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
        encoding='utf-8',newline='\n')
    (output/CASES).write_bytes(_jsonl_bytes(per_case))
    (output/LOSSES).write_bytes(_jsonl_bytes(lost))
    (output/FRESH_ROWS).write_bytes(_jsonl_bytes(novel))
    return report
