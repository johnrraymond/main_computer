"""G1-0030: gold-blind, inference-only reconstruction of broken decoder components.

G1-0029 found thirteen components with multiple original guard selections and a
rejected guard member. G1-0028 could not preserve a surviving guard selection
because it only attempted to restore entire intact components. Here each broken
component is reconstructed as an interval schedule over **surviving** candidates
from the frozen guard, filtered base and filtered wide decoders. Other components
remain exactly as G1-0026 selected them. No gold, template or source label can
enter the selection rule. All cohorts are development data, never certification.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random

import torch

from tools import nanojev_gamer_v1_g1_component_diagnosis as d
from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_filter_retention as r
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_g1_selective_conflict import _components, _signature
from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, restore_source,
    verify_author_gold, WINNER_HEAD, CONTROL, GUARD, _file_sha,
)
from tools.nanojev_gamer_v1_g1_structural_ood import SECTION_NAMES, _pieces
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _jsonl_bytes
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_graph_contract import validate_graph, execute_graph

SCHEMA='nanojev.gamer.graph.g1.surviving-component-reconstruction-development.v1'
REPORT='component_reconstruction_report.json'
CASES='component_reconstruction_per_case.jsonl'
LOSSES='component_reconstruction_prior_losses.jsonl'
FRESH_ROWS='component_reconstruction_new_layouts.jsonl'
# Fixed candidate arms. The 1.0 arm is a neutral interval-DP control;
# the other arms slightly prefer guard spans that survived G1-0026.
NEUTRAL='survivor_dp_neutral_0030'
PRIMARY='survivor_dp_guard_110_0030'
STRICT='survivor_dp_guard_125_0030'
BONUSES={NEUTRAL:0.0, PRIMARY:0.10, STRICT:0.25}
NEW_SEED=300030
NEW_CASES=320
NEW_LAYOUTS=('reverse_casebook','field_cards')
EXPECTED_LOSSES=13
GROUP_SIZES={
    'original_paired_sources':320,'original_independent_0014':320,
    'known_layout_replay':320,'new_layout_replay':160,
    'prior_independent_replay':320,'fresh_structural_validation':NEW_CASES,
}


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _key(span):
    return _signature(span)


def _schedule(keys,catalog,guard_keys,bonus):
    """Exact weighted interval scheduling (not the G1-0028 whole-component gate).

    Tokens are inclusive. Scores use the original linear interval objective
    and an explicitly predeclared, gold-blind continuity bonus for surviving
    original guard selections. No source text, graph labels or gold is read.
    """
    if not keys:return []
    ordered=sorted(keys,key=lambda k:(k[1],k[0],k[2]))
    ends=[key[1] for key in ordered]
    previous=[bisect_left(ends,key[0],0,i) for i,key in enumerate(ordered)]
    # Each entry is (score, number-of-filter-picks, tuple of selected keys).
    # Deterministically preserve frozen filter decisions on an exact tie.
    best=[(0.,0,())]
    for i,key in enumerate(ordered):
        margin=catalog[key]
        if not math.isfinite(margin) or margin<=0:
            raise ValueError('GAMER_G1_0030_NONPOSITIVE_OR_BAD_MARGIN')
        value=margin*(key[1]-key[0]+1)*(1.+bonus if key in guard_keys else 1.)
        past=best[previous[i]]
        include=(past[0]+value,past[1],past[2]+(key,))
        omit=best[-1]
        # Score comparison is sufficient; on exactly equal scores prefer the
        # original filtered arrangement by refusing to make a speculative swap.
        if include[0]>omit[0]+1e-9:
            best.append(include)
        else:
            best.append(omit)
    return list(best[-1][2])


def reconstruct_components(guard, filtered_base, filtered_wide, filtered,
                           filtered_catalog, *, guard_bonus, experimental_bonuses=()):
    """Replace only multi-guard components broken by candidate rejection.

    A candidate that was rejected by the G1-0026 filter is never restored.
    All other components, including common spans, retain frozen 0026 results.
    Reconstruction uses only spans independently selected by a frozen decoder
    (not every raw proposal, which would add a large false-positive surface).
    """
    if (type(guard_bonus) is not float or guard_bonus not in
            (*tuple(BONUSES.values()), *experimental_bonuses) or
            any(type(x) is not float or not math.isfinite(x) or x<0. for x in experimental_bonuses)):
        raise ValueError('GAMER_G1_0030_UNDECLARED_BONUS')
    catalog=filtered_catalog
    if len(catalog)!=len(set(catalog)):
        raise ValueError('GAMER_G1_0030_INVALID_CATALOG')
    common,groups=_components(guard,filtered)
    choice=set(common);stats=Counter()
    base_keys={_key(x) for x in filtered_base}
    wide_keys={_key(x) for x in filtered_wide}
    filtered_keys={_key(x) for x in filtered}
    guard_keys={_key(x) for x in guard}
    if any(k not in catalog for k in filtered_keys|base_keys|wide_keys):
        raise ValueError('GAMER_G1_0030_FILTERED_DECODER_SELECTED_REJECTED_PROPOSAL')
    for group in groups:
        old=set(group['control']);new=set(group['challenger'])
        removed=old-catalog.keys()
        if not (len(old)>1 and removed and new):
            choice.update(new)
            stats['frozen_component_unchanged']+=1
            continue
        stats['broken_multi_guard_component']+=1
        low=min(k[0] for k in old|new);high=max(k[1] for k in old|new)
        # Restrict the reconstruction to this bounded overlap component.
        candidates={key for key in (old|new|base_keys|wide_keys)
                    if key in catalog and low<=key[0]<=key[1]<=high}
        if not new<=candidates or len(candidates)>48:
            choice.update(new)
            stats['bounded_fallback_to_filter']+=1
            continue
        survivor=old&catalog.keys()
        if not survivor:
            choice.update(new)
            stats['no_surviving_guard_selection']+=1
            continue
        rebuilt=set(_schedule(candidates,catalog,survivor,guard_bonus))
        # A new candidate must be an actual filtered survivor and stay within
        # the component. No resurrection of rejected guard partners.
        if not rebuilt<=catalog.keys():
            raise ValueError('GAMER_G1_0030_RESTORED_REJECTED_SPAN')
        choice.update(rebuilt)
        stats['reconstructed_component']+=1
        stats['candidate_count']+=len(candidates)
        stats['removed_guard_candidates_excluded']+=len(removed)
        stats['surviving_guard_selected']+=len(rebuilt&survivor)
        stats['new_component_spans']+=len(rebuilt-new)
        stats['old_component_spans_removed']+=len(new-rebuilt)
        if rebuilt!=new:
            stats['component_decision_changed']+=1
    if not choice<=catalog.keys():
        raise ValueError('GAMER_G1_0030_OUTPUT_CONTAINS_REJECTED_SPAN')
    ordered=sorted(choice)
    if len(ordered)>128 or any(a[1]>=b[0] for a,b in zip(ordered,ordered[1:])):
        raise ValueError('GAMER_G1_0030_NONOVERLAPPING_LIMIT_VIOLATION')
    return ([{'kind':kind,'token_first':a,'token_last':b} for a,b,kind in ordered],stats)


def decode_reconstruction(pred,prepared):
    """Recompute all frozen selections; attach only experimental 0030 arms."""
    stages=d.frozen_stages(pred,prepared)
    selected=stages['selected'];catalog=stages['filtered_catalog']
    results=dict(stages['guesses']);counts={}
    for name,bonus in BONUSES.items():
        spans,signals=reconstruct_components(
            selected['guard'],selected['filtered_base'],selected['filtered_wide'],
            selected['filtered'],catalog,guard_bonus=bonus)
        results[name]=_char_set(spans,prepared)
        counts[name]=signals
    return results,stages['rejected'],counts,stages


def load_0029(component_run,info,hash28,hash27,hash26,hash22,losses27):
    """Require the real 0029 13-case diagnostic, including immutable ancestry."""
    base=Path(component_run)
    paths={name:base/name for name in (d.REPORT,d.LOSSES)}
    hashes={name:_file_sha(path) for name,path in paths.items()}
    report=json.loads(paths[d.REPORT].read_text(encoding='utf-8'))
    losses=c._jsonl(paths[d.LOSSES])
    expected={'schema':d.SCHEMA,
        'status':'READ_ONLY_COMPONENT_DIAGNOSIS_NOT_INTERVENTION',
        'prior_losses_replayed':EXPECTED_LOSSES,
        'source_0028_files_sha256':hash28,
        'source_0027_files_sha256':hash27,
        'source_0026_files_sha256':hash26,
        'source_0022_files_sha256':hash22,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'production_promotion':False,'certification_authorized':False,
        'g2_unlocked':False,'sealed_test_used':False,
        'challenge_or_test_split_used':False,'heads_frozen':True,
        'no_training':True,'gold_used_after_inference_only':True,
        'decoder_unchanged':True,
    }
    for key,val in expected.items():
        if type(report.get(key)) is not type(val) or report[key]!=val:
            raise ValueError('GAMER_G1_0030_0029_DRIFT:'+key)
    summary=report['summary']
    if summary.get('count')!=EXPECTED_LOSSES or summary.get('cause_classes')!={
            'complex_component_excluded_by_0028':EXPECTED_LOSSES} or len(losses)!=EXPECTED_LOSSES:
        raise ValueError('GAMER_G1_0030_0029_LOSS_SUMMARY_DRIFT')
    prior={(x['group'],x['index'],x['kind'],tuple(x['gold_span'])) for x in losses27}
    actual={(x['group'],x['index'],x['kind'],tuple(x['gold_span'])) for x in losses}
    if actual!=prior or len(actual)!=EXPECTED_LOSSES:
        raise ValueError('GAMER_G1_0030_0029_CASE_DRIFT')
    if any(x['cause_class']!='complex_component_excluded_by_0028' or
           not x['gold_survived_candidate_filter'] or
           x['component']['guard_count']<=1 or
           x['component']['removed_guard_member_count']<1 for x in losses):
        raise ValueError('GAMER_G1_0030_0029_CAUSAL_DRIFT')
    return report,losses,paths,hashes


def new_layout_row(source,layout):
    """Unseen development rendering, with exact gold-character remapping."""
    if source.get('split')!='dev' or layout not in NEW_LAYOUTS:
        raise ValueError('GAMER_G1_0030_BAD_SOURCE_OR_LAYOUT')
    sections=_pieces(source['prompt'],len(SECTION_NAMES[source['family']]))
    order=(tuple(range(len(sections)-1,-1,-1)) if layout=='reverse_casebook'
           else tuple(range(1,len(sections),2))+tuple(range(0,len(sections),2)))
    header=('CASEBOOK OF EVENTS\n\n' if layout=='reverse_casebook'
            else 'FIELD CARDS\n\n')
    chunks=[header];position=len(header);mapped={}
    for idx in order:
        begin,_,part=sections[idx];label=SECTION_NAMES[source['family']][idx].title()
        prefix=(f'## Card {idx+1}: {label}\n' if layout=='reverse_casebook'
                else f'[{label} - {idx+1}]\n')
        chunks.append(prefix);position+=len(prefix)
        transformed,local=f._reshape_new(part,
            'markdown_note' if layout=='reverse_casebook' else 'key_value_index')
        for old,new in local.items():mapped[begin+old]=position+new
        chunks.append(transformed);position+=len(transformed)
        chunks.append('\n\n');position+=2
    prompt=''.join(chunks).rstrip()+'\n'
    result=deepcopy(source);result['prompt']=prompt;result['split']='ood_dev'
    result['template']='component-reconstruction-'+layout
    result['surface_shift']=layout
    result['source_prompt_sha256']=_digest(source['prompt'])
    for node in result['graph']['nodes']:
        if 'span' not in node:continue
        a,b=node['span']
        if a not in mapped or b not in mapped or prompt[mapped[a]:mapped[b]]!=source['prompt'][a:b]:
            raise ValueError('GAMER_G1_0030_NEW_LAYOUT_GOLD_MAP_DRIFT')
        node['span']=[mapped[a],mapped[b]]
    result['object_spans']=[{'kind':n['kind'],'start':n['span'][0],
                            'end':n['span'][1]} for n in result['graph']['nodes'] if 'span' in n]
    validate_graph(result['graph'],complete=True)
    if execute_graph(result['graph'])['decisions']!=source['expected_decisions']:
        raise ValueError('GAMER_G1_0030_NEW_LAYOUT_SEMANTIC_DRIFT')
    return result


def new_rows(info,ood_rows,known_new,old_independent):
    from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
    excluded={x['source_prompt_sha256'] for x in ood_rows+known_new+old_independent}
    excluded.update(_digest(x['prompt']) for x in info['fresh_rows']+info['validation_rows'])
    excluded.update(_digest(x) for x in c._original_prompts(info['dataset']))
    rng=random.Random(NEW_SEED);rows=[];seen=set()
    for i in range(NEW_CASES):
        generator=encounter if i%2==0 else allocation
        layout=NEW_LAYOUTS[(i//2)%2]
        for _ in range(250):
            original=generator(rng,'dev',i);digest=_digest(original['prompt'])
            if digest not in excluded and digest not in seen:break
        else:
            raise ValueError('GAMER_G1_0030_PROMPT_EXHAUSTED')
        seen.add(digest)
        rows.append(new_layout_row(original,layout))
    if len({x['prompt'] for x in rows})!=NEW_CASES:
        raise ValueError('GAMER_G1_0030_NEW_PROMPT_DUPLICATE')
    return rows


def _metrics(records,rows):
    names=(CONTROL,GUARD,f.CHALLENGER,*c.RATIOS,*BONUSES)
    return {name:_summarize([(g[name],gold) for g,gold in records],rows) for name in names}


def _pair(records,name):
    return f._paired_any([(g[f.CHALLENGER],gold) for g,gold in records],
                         [(g[name],gold) for g,gold in records])


def _group_stats(records,rows,signals):
    return {'metrics':_metrics(records,rows),
        'paired_vs_filter':{arm:_pair(records,arm) for arm in BONUSES},
        'component_counts':{arm:dict(sorted(signals[arm].items())) for arm in BONUSES}}


def evaluate_group(name,rows,head,model,tok,backbone,device,max_length,
                   *,prior_cases=None,prior_losses=None):
    records=[];individual=[];restored=[]
    signals={arm:Counter() for arm in BONUSES}
    layout_records={};layout_rows={}
    for index,row in enumerate(rows,1):
        if row['split'] not in ('dev','ood_dev') or row['family'] not in ('encounter','allocation'):
            raise ValueError('GAMER_G1_0030_NON_DEVELOPMENT_ROW')
        prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
        hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
            device='cpu',dtype=torch.float16).contiguous()
        pred=head(hidden.to(device=device,dtype=torch.float32),
                  eligible=prepared['eligible'].to(device),active_stage='G1')
        guesses,rejected,local,stages=decode_reconstruction(pred,prepared)
        gold=r._gold(row)
        summary=r._case_summary(guesses,gold)
        if prior_cases is not None:
            expected=prior_cases.get((name,index))
            prior_names=(CONTROL,GUARD,f.CHALLENGER,*c.RATIOS)
            # G1-0028's per-case JSONL does not include gold_count (unlike
            # G1-0027's). Recover that invariant from its persisted per-arm
            # accounting; also require exact frozen metrics and case identity.
            # Do not silently substitute a missing field or weaken replay.
            saved=expected.get('configurations') if isinstance(expected,dict) else None
            expected_gold=len(gold)
            if (not isinstance(expected,dict)
                or expected.get('group')!=(name if name!='prior_independent_replay'
                    else 'independent_structural_validation')
                or expected.get('index')!=index
                or expected.get('family')!=row['family']
                or expected.get('layout')!=row.get('surface_shift','original')
                or expected.get('prompt_sha256')!=_digest(row['prompt'])
                or not isinstance(saved,dict)
                or set(saved)!=set(prior_names)
                or any(not isinstance(saved.get(k),dict)
                    or type(saved[k].get('correct')) is not int
                    or type(saved[k].get('missing')) is not int
                    or saved[k]['correct']+saved[k]['missing']!=expected_gold
                    for k in prior_names)
                or saved!={k:summary[k] for k in prior_names}):
                raise ValueError('GAMER_G1_0030_FROZEN_0028_CASE_DRIFT:'+name+':'+str(index))
        for arm in BONUSES:signals[arm].update(local[arm])
        records.append((guesses,gold))
        layout=row.get('surface_shift','original')
        layout_records.setdefault(layout,[]).append((guesses,gold))
        layout_rows.setdefault(layout,[]).append(row)
        individual.append({'group':name,'index':index,'layout':layout,'family':row['family'],
            'prompt_sha256':_digest(row['prompt']),
            'configurations':summary,'rejected_proposal_count':len(rejected),
            'gold_proposals_rejected':sum((*x['bounds'],x['kind']) in gold for x in rejected
                                      if x['bounds'] is not None),
            'component_counts':{arm:dict(sorted(local[arm].items())) for arm in BONUSES}})
        if prior_losses:
            for old in prior_losses.get((name,index),[]):
                key=(*old['gold_span'],old['kind'])
                if key not in guesses[GUARD]&gold or key in guesses[f.CHALLENGER]:
                    raise ValueError('GAMER_G1_0030_0029_LOSS_NOT_REPRODUCED')
                restored.append({'group':name,'index':index,'layout':layout,
                    'gold_span':old['gold_span'],'kind':old['kind'],
                    'recovered':{arm:key in guesses[arm] for arm in BONUSES},
                    'frozen_guard_correct':True,'frozen_filter_lost':True})
        if index==1 or index%40==0 or index==len(rows):
            print(f'GAMER_G1_COMPONENT_RECONSTRUCTION {name} {index}/{len(rows)}',flush=True)
    result=_group_stats(records,rows,signals)
    result['by_layout']={layout:{'metrics':_metrics(layout_records[layout],layout_rows[layout]),
        'paired_vs_filter':{arm:_pair(layout_records[layout],arm) for arm in BONUSES}}
        for layout in sorted(layout_records)}
    return result,individual,restored


def _checks(group):
    baseline=group['metrics'][f.CHALLENGER]
    chosen=group['metrics'][PRIMARY]
    paired=group['paired_vs_filter'][PRIMARY]
    return {'no_exact_prompt_regression':chosen['exact_prompts']>=baseline['exact_prompts'],
        'no_f1_regression':chosen['f1']>=baseline['f1'],
        'no_missing_object_regression':chosen['missing_objects']<=baseline['missing_objects'],
        'no_false_positive_regression':chosen['false_positive_objects']<=baseline['false_positive_objects'],
        'no_previously_correct_gold_lost':paired['b_previously_correct_gold_lost']==0}


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        layout_run,retention_run,competition_run,component_run,output,
        device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0030_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0030_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,ood_rows,_,ood_paths,ood_hashes=_load_0022(ood_run,info)
    report26,cases26,novel26,paths26,hashes26=r.load_0026(layout_run,info,ood_hashes,ood_rows)
    report27,cases27,losses27,paths27,hashes27=c.load_retention(
        retention_run,info,hashes26['layout_filter_report.json'])
    report28,cases28,paths28,hashes28=d.load_0028(
        competition_run,info,hashes26,hashes27,ood_hashes,losses27)
    report29,losses29,paths29,hashes29=load_0029(
        component_run,info,hashes28,hashes27,hashes26,ood_hashes,losses27)
    old_independent=c._jsonl(paths28[c.FRESH_ROWS])
    original=[restore_source(x)[0] for x in ood_rows]
    verify_author_gold(ood_rows,original)
    if any(x['split']!='dev' for x in original+info['validation_rows']):
        raise ValueError('GAMER_G1_0030_NON_DEV_SOURCE')
    fresh=new_rows(info,ood_rows,novel26,old_independent)
    groups={'original_paired_sources':original,
        'original_independent_0014':info['validation_rows'],
        'known_layout_replay':ood_rows,'new_layout_replay':novel26,
        'prior_independent_replay':old_independent,
        'fresh_structural_validation':fresh}
    if (len({row['prompt'] for rows in groups.values() for row in rows})!=sum(GROUP_SIZES.values())
        or any(len(rows)!=GROUP_SIZES[k] for k,rows in groups.items())):
        raise ValueError('GAMER_G1_0030_DUPLICATE_OR_WRONG_GROUP_SIZE')
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0030_ADAPTER_DRIFT')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0030_HEAD_DRIFT')
    head.eval();model,tok=backbone.model_components(
        manifest['model_id'],manifest['source_adapter'],device)
    losses_by_case={}
    for loss in losses29:
        losses_by_case.setdefault((loss['group'],loss['index']),[]).append(loss)
    results={};cases=[];restored=[]
    with torch.inference_mode():
        for name,rows in groups.items():
            cases_before={((name if name!='prior_independent_replay' else 'independent_structural_validation'),i):x
                for (group,i),x in cases28.items() if group==(name if name!='prior_independent_replay' else 'independent_structural_validation')}
            # G1-0028 names its old independent cohort differently.
            if name=='prior_independent_replay':
                old_cases={('prior_independent_replay',i):x for (_,i),x in cases_before.items()}
            elif name=='fresh_structural_validation':
                old_cases=None
            else:
                old_cases=cases_before
            group,details,recovered=evaluate_group(name,rows,head,model,tok,backbone,
                device,max_length,prior_cases=old_cases,prior_losses=losses_by_case)
            results[name]=group;cases.extend(details);restored.extend(recovered)
            if name in report28['groups']:
                for arm in (CONTROL,GUARD,f.CHALLENGER,*c.RATIOS):
                    if group['metrics'][arm]!=report28['groups'][name]['metrics'][arm]:
                        raise ValueError('GAMER_G1_0030_FROZEN_0028_METRIC_DRIFT:'+name+':'+arm)
            elif name=='prior_independent_replay':
                for arm in (CONTROL,GUARD,f.CHALLENGER,*c.RATIOS):
                    if group['metrics'][arm]!=report28['groups']['independent_structural_validation']['metrics'][arm]:
                        raise ValueError('GAMER_G1_0030_PRIOR_INDEPENDENT_METRIC_DRIFT:'+arm)
    if len(restored)!=EXPECTED_LOSSES:
        raise ValueError('GAMER_G1_0030_LOSS_COUNT_DRIFT')
    recovery={arm:sum(x['recovered'][arm] for x in restored) for arm in BONUSES}
    checks={name:_checks(group) for name,group in results.items()}
    lost_gold={arm:sum(results[name]['paired_vs_filter'][arm]['b_previously_correct_gold_lost']
            for name in results) for arm in BONUSES}
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for paths,hashes in ((ood_paths,ood_hashes),(paths26,hashes26),(paths27,hashes27),
                         (paths28,hashes28),(paths29,hashes29)):
        for key,path in paths.items():
            if _file_sha(path)!=hashes[key]:
                raise ValueError('GAMER_G1_0030_INPUT_CHANGED:'+key)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=info['checkpoint_files'][WINNER_HEAD]['manifest_sha256']
        or _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0030_CHECKPOINT_CHANGED')
    report={'schema':SCHEMA,'status':'INFERENCE_ONLY_COMPONENT_RECONSTRUCTION_NO_PROMOTION',
        'groups':results,'prior_losses_replayed':len(restored),
        'prior_losses_recovered':recovery,
        'newly_lost_gold_vs_filter_by_arm':lost_gold,
        'original_retention_pass':all(all(checks[n].values()) for n in (
            'original_paired_sources','original_independent_0014')),
        'fresh_validation_pass':all(checks['fresh_structural_validation'].values()),
        'checks':checks,'primary_candidate_meets_all_development_gates':(
            recovery[PRIMARY]==EXPECTED_LOSSES and lost_gold[PRIMARY]==0 and
            all(all(x.values()) for x in checks.values())),
        'component_rule_predeclared':{'candidate_sources':['surviving_original_guard',
             'filtered_base','filtered_wide','filtered_guard'],
             'only_broken_multi_guard_components':True,
             'maximum_candidates_per_component':48,
             'positive_neural_margin_linear_weighted_interval_dp':True,
             'gold_blind':True,'bonuses':BONUSES,'primary':PRIMARY},
        'fresh_cases':NEW_CASES,'fresh_seed':NEW_SEED,'fresh_layouts':list(NEW_LAYOUTS),
        'fresh_dataset_sha256':hashlib.sha256(_jsonl_bytes(fresh)).hexdigest(),
        'source_0029_file_sha256':hashes29,'source_0028_file_sha256':hashes28,
        'source_0027_file_sha256':hashes27,'source_0026_file_sha256':hashes26,
        'source_0022_file_sha256':ood_hashes,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'configurations_predeclared':[CONTROL,GUARD,f.CHALLENGER,*c.RATIOS,*BONUSES],
        'no_training':True,'heads_frozen':True,'gold_used_after_inference_only':True,
        'production_promotion':False,'certification_authorized':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,'g2_unlocked':False,
        'interpretation':('Development-only reconstruction of broken multi-selection decoder '
            'components using surviving filtered candidates. The fixed prior losses and all '
            'regressions are measured, not assumed. No training or production promotion.')}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
        encoding='utf-8',newline='\n')
    (output/CASES).write_bytes(_jsonl_bytes(cases))
    (output/LOSSES).write_bytes(_jsonl_bytes(restored))
    (output/FRESH_ROWS).write_bytes(_jsonl_bytes(fresh))
    return report
