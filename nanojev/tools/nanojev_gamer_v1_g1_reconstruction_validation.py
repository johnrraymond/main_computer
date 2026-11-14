"""G1-0031: frozen 1.25 survivor-DP retention and residual component audit.

All experiments operate on development-only prompts. The 1.25 arm is frozen
from G1-0030; two additional shadow bonuses are exploratory, not selected by
looking at the remaining gold loss. Gold is accessed only after decoding.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import random

import torch

from tools import nanojev_gamer_v1_g1_component_reconstruction as x
from tools import nanojev_gamer_v1_g1_component_diagnosis as d
from tools import nanojev_gamer_v1_g1_competition_rescue as c
from tools import nanojev_gamer_v1_g1_filter_retention as r
from tools import nanojev_gamer_v1_g1_layout_filter as f
from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, _file_sha,
    WINNER_HEAD, CONTROL, GUARD,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _jsonl_bytes
from tools.nanojev_gamer_v1_g1_structural_ood import _pieces, SECTION_NAMES
from tools.nanojev_gamer_v1_graph_contract import validate_graph, execute_graph
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA='nanojev.gamer.graph.g1.reconstruction-independent-development.v1'
REPORT='reconstruction_validation_report.json'
CASES='reconstruction_validation_per_case.jsonl'
LOSSES='reconstruction_validation_residual_audit.jsonl'
DATASET='reconstruction_validation_new_rows.jsonl'
BASE=x.STRICT
SHADOW={'survivor_dp_guard_140_0031':0.40,
        'survivor_dp_guard_160_0031':0.60}
ARMS=(f.CHALLENGER, BASE, *SHADOW)
SEED=310031
NEW_ORIGINAL=320
NEW_STRUCTURAL=320
LAYOUTS=('margin_ledger','nested_statement')


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def load_0030(path,info,hash29,hash28,hash27,hash26,hash22):
    """Fail closed unless the full immutable 0030 report and rows agree."""
    path=Path(path)
    paths={name:path/name for name in (x.REPORT,x.CASES,x.LOSSES,x.FRESH_ROWS)}
    hashes={name:_file_sha(p) for name,p in paths.items()}
    report=json.loads(paths[x.REPORT].read_text(encoding='utf-8'))
    cases=c._jsonl(paths[x.CASES]);losses=c._jsonl(paths[x.LOSSES]);fresh=c._jsonl(paths[x.FRESH_ROWS])
    expected={'schema':x.SCHEMA,
        'status':'INFERENCE_ONLY_COMPONENT_RECONSTRUCTION_NO_PROMOTION',
        'source_0029_file_sha256':hash29,'source_0028_file_sha256':hash28,
        'source_0027_file_sha256':hash27,'source_0026_file_sha256':hash26,
        'source_0022_file_sha256':hash22,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'prior_losses_replayed':x.EXPECTED_LOSSES,
        'prior_losses_recovered':{x.NEUTRAL:5,x.PRIMARY:7,x.STRICT:12},
        'newly_lost_gold_vs_filter_by_arm':{arm:0 for arm in x.BONUSES},
        'primary_candidate_meets_all_development_gates':False,
        'fresh_cases':x.NEW_CASES,'fresh_seed':x.NEW_SEED,
        'fresh_layouts':list(x.NEW_LAYOUTS),
        'configurations_predeclared':[CONTROL,GUARD,f.CHALLENGER,*c.RATIOS,*x.BONUSES],
    }
    for k,v in expected.items():
        if type(report.get(k)) is not type(v) or report[k]!=v:
            raise ValueError('GAMER_G1_0031_0030_REFERENCE_DRIFT:'+k)
    for k in ('no_training','heads_frozen','gold_used_after_inference_only','original_retention_pass','fresh_validation_pass'):
        if report.get(k) is not True:
            raise ValueError('GAMER_G1_0031_0030_EXPECTED_PASS:'+k)
    for k in ('production_promotion','certification_authorized','sealed_test_used',
              'challenge_or_test_split_used','g2_unlocked'):
        if report.get(k) is not False:
            raise ValueError('GAMER_G1_0031_0030_FORBIDDEN_FLAG:'+k)
    if (len(cases)!=sum(x.GROUP_SIZES.values()) or len(losses)!=x.EXPECTED_LOSSES
        or len(fresh)!=x.NEW_CASES or len({(z['group'],z['index']) for z in cases})!=len(cases)
        or len({(z['group'],z['index'],z['kind'],tuple(z['gold_span'])) for z in losses})!=13
        or hashlib.sha256(_jsonl_bytes(fresh)).hexdigest()!=report.get('fresh_dataset_sha256')):
        raise ValueError('GAMER_G1_0031_0030_CASE_REFERENCE_DRIFT')
    if sum(bool(item['recovered'][BASE]) for item in losses)!=12:
        raise ValueError('GAMER_G1_0031_0030_RESIDUAL_COUNT_DRIFT')
    for name,expected_count in x.GROUP_SIZES.items():
        if sum(z['group']==name for z in cases)!=expected_count or name not in report['groups']:
            raise ValueError('GAMER_G1_0031_0030_GROUP_DRIFT:'+name)
    return report,{(z['group'],z['index']):z for z in cases},losses,fresh,paths,hashes


def structural_row(source,layout):
    """Retain every gold surface exactly while restructuring independent text."""
    if layout not in LAYOUTS or source.get('split')!='dev':
        raise ValueError('GAMER_G1_0031_BAD_SOURCE')
    spans=_pieces(source['prompt'],len(SECTION_NAMES[source['family']]))
    order=(tuple(range(len(spans))) if layout=='margin_ledger'
           else tuple(range(len(spans)-1,-1,-1)))
    initial=('MARGIN LEDGER : REVIEWED RECORDS\n\n' if layout=='margin_ledger'
             else 'NESTED STATEMENT : SOURCE ENTRIES\n\n')
    chunks=[initial];position=len(initial);mapped={}
    for idx in order:
        start,_,part=spans[idx]
        name=SECTION_NAMES[source['family']][idx]
        label=(f'== Entry {idx+1} / {name} ==\n' if layout=='margin_ledger'
               else f'{{statement {idx+1}: {name}}}\n')
        chunks.append(label);position+=len(label)
        prefix=('| ' if layout=='margin_ledger' else '    > ')
        for j,ch in enumerate(part):
            if j==0 or part[j-1]=='\n':
                chunks.append(prefix);position+=len(prefix)
            mapped[start+j]=position
            chunks.append(ch);position+=1
        mapped[start+len(part)]=position
        chunks.append('\n\n');position+=2
    prompt=''.join(chunks).rstrip()+'\n'
    result=deepcopy(source)
    result['prompt']=prompt;result['split']='ood_dev'
    result['surface_shift']=layout;result['template']='reconstruction-validation-'+layout
    result['source_prompt_sha256']=_sha(source['prompt'])
    for node in result['graph']['nodes']:
        if 'span' not in node:continue
        a,b=node['span']
        if a not in mapped or b not in mapped or prompt[mapped[a]:mapped[b]]!=source['prompt'][a:b]:
            raise ValueError('GAMER_G1_0031_GOLD_REMAP_NONCONTIGUOUS')
        node['span']=[mapped[a],mapped[b]]
    result['object_spans']=[{'kind':n['kind'],'start':n['span'][0],
        'end':n['span'][1]} for n in result['graph']['nodes'] if 'span' in n]
    validate_graph(result['graph'],complete=True)
    if execute_graph(result['graph'])['decisions']!=source['expected_decisions']:
        raise ValueError('GAMER_G1_0031_SEMANTIC_DRIFT')
    return result


def new_rows(info,ood_rows,novel26,independent28,fresh30):
    """640 new dev prompts with disjoint semantic sources and pinned seed."""
    from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
    forbidden={z['source_prompt_sha256'] for rows in
        (ood_rows,novel26,independent28,fresh30) for z in rows}
    forbidden.update(_sha(z['prompt']) for z in info['fresh_rows']+info['validation_rows'])
    forbidden.update(_sha(z) for z in c._original_prompts(info['dataset']))
    rng=random.Random(SEED);seen=set();rows=[]
    for i in range(NEW_ORIGINAL+NEW_STRUCTURAL):
        maker=encounter if i%2==0 else allocation
        for attempt in range(600):
            source=maker(rng,'dev',i)
            digest=_sha(source['prompt'])
            if digest in forbidden or digest in seen:continue
            if i>=NEW_ORIGINAL:
                try:row=structural_row(source,LAYOUTS[(i-NEW_ORIGINAL)//2%2])
                except ValueError as err:
                    if str(err)=='GAMER_G1_0031_GOLD_REMAP_NONCONTIGUOUS':continue
                    raise
            else:row=source
            break
        else:raise ValueError('GAMER_G1_0031_SOURCE_EXHAUSTED')
        seen.add(digest);rows.append(row)
    if (len(rows)!=640 or len(seen)!=640 or len({z['prompt'] for z in rows})!=640
        or len({z['source_prompt_sha256'] for z in rows[320:]})!=320
        or any(z['split']!='dev' for z in rows[:320])
        or any(z['split']!='ood_dev' for z in rows[320:])):
        raise ValueError('GAMER_G1_0031_NEW_SET_DRIFT')
    return rows


def decode(pred,prepared):
    """Use 0030's frozen 1.25 decoder, plus two predeclared shadow arms."""
    result,rejected,counts,stages=x.decode_reconstruction(pred,prepared)
    selected=stages['selected'];catalog=stages['filtered_catalog']
    for name,bonus in SHADOW.items():
        spans,signals=x.reconstruct_components(
            selected['guard'],selected['filtered_base'],selected['filtered_wide'],
            selected['filtered'],catalog,guard_bonus=bonus,
            experimental_bonuses=tuple(SHADOW.values()))
        result[name]=x._char_set(spans,prepared)
        counts[name]=signals
    return result,rejected,counts,stages


def _assessment(records,rows):
    metrics={name:_summarize([(g[name],gold) for g,gold in records],rows)
             for name in ARMS}
    paired={name:f._paired_any([(g[f.CHALLENGER],gold) for g,gold in records],
                                [(g[name],gold) for g,gold in records])
            for name in ARMS[1:]}
    baseline=metrics[f.CHALLENGER]
    checks={name:{
        'no_exact_prompt_regression':metrics[name]['exact_prompts']>=baseline['exact_prompts'],
        'no_f1_regression':metrics[name]['f1']>=baseline['f1'],
        'no_missing_object_regression':metrics[name]['missing_objects']<=baseline['missing_objects'],
        'no_false_positive_regression':metrics[name]['false_positive_objects']<=baseline['false_positive_objects'],
        'no_previously_correct_gold_lost':paired[name]['b_previously_correct_gold_lost']==0,
    } for name in ARMS[1:]}
    return {'metrics':metrics,'paired_vs_filter':paired,'checks':checks}


def _capture(row,head,model,tok,backbone,device,max_length):
    prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
    hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
        device='cpu',dtype=torch.float16).contiguous()
    pred=head(hidden.to(device=device,dtype=torch.float32),
        eligible=prepared['eligible'].to(device),active_stage='G1')
    return decode(pred,prepared),prepared


def _verify_replay(row,name,index,saved,guesses):
    expected=saved.get('configurations') if isinstance(saved,dict) else None
    names=(CONTROL,GUARD,f.CHALLENGER,*c.RATIOS,*x.BONUSES)
    summary=r._case_summary(guesses,r._gold(row))
    if (saved.get('group')!=name or saved.get('index')!=index
        or saved.get('layout')!=row.get('surface_shift','original')
        or saved.get('family')!=row['family']
        or saved.get('prompt_sha256')!=_sha(row['prompt'])
        or not isinstance(expected,dict) or set(expected)!=set(names)
        or {k:summary[k] for k in names}!=expected):
        raise ValueError('GAMER_G1_0031_0030_CASE_REPLAY_DRIFT:'+name+':'+str(index))


def _residual_audit(old,row,prepared,stages,guesses,prior_0030_recovery):
    """Post-inference component report; no gold or label is used for selection."""
    token_key=tuple(old['guard_gold_token_selection'])
    gold_key=(*old['gold_span'],old['kind'])
    if (token_span_char_bounds({'kind':token_key[2],
        'token_first':token_key[0],'token_last':token_key[1]},prepared)
        !=tuple(old['gold_span'])):
        raise ValueError('GAMER_G1_0031_0029_GOLD_TOKEN_DRIFT')
    group,removed,cause=d.locate_loss_component(token_key,
        stages['selected']['guard'],stages['selected']['filtered'],stages['filtered_catalog'])
    if cause!='complex_component_excluded_by_0028':
        raise ValueError('GAMER_G1_0031_0029_CAUSE_DRIFT')
    selected=stages['selected'];clean=stages['filtered_catalog'];uncut=stages['all_catalog']
    lo=min(k[0] for k in group['control']+group['challenger'])
    hi=max(k[1] for k in group['control']+group['challenger'])
    survivors=set(k for k in (set(group['control'])|set(group['challenger'])|
        {x._key(s) for s in selected['filtered_base']+selected['filtered_wide']})
        if k in clean and lo<=k[0]<=k[1]<=hi)
    rejections={(z['token_first'],z['token_last'],z['kind']):z['reason']
                for z in stages['rejected']}
    details=[d.describe_span(key,prepared,uncut,clean,rejections,r._gold(row))
             for key in sorted(survivors|set(group['control'])|set(group['challenger']))]
    guard_survivors=set(group['control'])&clean.keys()
    score_paths={}
    for arm,bonus in ((BASE,.25),*SHADOW.items()):
        chosen=x._schedule(survivors,clean,guard_survivors,bonus)
        score_paths[arm]={
            'selected_token_spans':[list(k) for k in chosen],
            'selected_score':round(sum(clean[k]*(k[1]-k[0]+1)*
                (1.+bonus if k in guard_survivors else 1.) for k in chosen),6),
            'gold_surviving_token_span_selected':token_key in chosen,
            'guard_bonus':bonus,
        }
    return {'group':old['group'],'index':old['index'],'kind':old['kind'],
        'layout':old['layout'],'gold_span':old['gold_span'],
        'old_125_recovered':bool(prior_0030_recovery[BASE]),
        # Replay all three frozen 0030 arms, not just the 1.25 primary and
        # the exploratory 0031 shadows. 0030's saved loss records include
        # neutral/1.10/1.25, while ARMS intentionally omits the first two.
        'candidate_recovered':{arm:gold_key in guesses[arm]
                               for arm in (*x.BONUSES,*SHADOW)},
        'frozen_0026_selected':gold_key in guesses[f.CHALLENGER],
        'guard_component_count':len(group['control']),
        'rejected_guard_member_count':len(removed),
        'candidate_pool_size':len(survivors),
        'component_weighted_interval_paths':score_paths,
        'candidate_spans':details,
        'status':'POST_INFERENCE_GOLD_DIAGNOSTIC_NOT_SELECTION'}


def _verify_loss_replay(matching,item):
    """Match every frozen 0030 loss decision; fail closed on missing arms."""
    expected=matching.get('recovered')
    actual=item.get('candidate_recovered')
    if (not isinstance(expected,dict) or set(expected)!=set(x.BONUSES)
        or not isinstance(actual,dict) or not set(x.BONUSES)<=set(actual)
        or expected!={arm:actual[arm] for arm in x.BONUSES}):
        raise ValueError('GAMER_G1_0031_0030_LOSS_REPLAY_DRIFT')


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        layout_run,retention_run,competition_run,component_run,reconstruction_run,
        output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0031_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0031_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    _,ood_rows,_,ood_paths,hash22=_load_0022(ood_run,info)
    _,_,novel26,paths26,hash26=r.load_0026(layout_run,info,hash22,ood_rows)
    _,_,losses27,paths27,hash27=c.load_retention(
        retention_run,info,hash26['layout_filter_report.json'])
    _,_,paths28,hash28=d.load_0028(
        competition_run,info,hash26,hash27,hash22,losses27)
    _,losses29,paths29,hash29=x.load_0029(
        component_run,info,hash28,hash27,hash26,hash22,losses27)
    report30,cases30,losses30,fresh30,paths30,hash30=load_0030(
        reconstruction_run,info,hash29,hash28,hash27,hash26,hash22)
    old_independent=c._jsonl(paths28[c.FRESH_ROWS])
    fresh=new_rows(info,ood_rows,novel26,old_independent,fresh30)
    new_groups={'fresh_original_validation':fresh[:NEW_ORIGINAL],
                'fresh_structural_validation':fresh[NEW_ORIGINAL:]}
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0031_ADAPTER_DRIFT')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0031_HEAD_DRIFT')
    head.eval();model,tok=backbone.model_components(
        manifest['model_id'],manifest['source_adapter'],device)
    source_groups={'known_layout_replay':ood_rows,'new_layout_replay':novel26}
    by_loss={(p['group'],p['index'],p['kind'],tuple(p['gold_span'])):p for p in losses30}
    diagnostics=[];result={};per_case=[]
    with torch.inference_mode():
        for i,old in enumerate(sorted(losses29,key=lambda z:(z['group'],z['index'],z['kind'],z['gold_span'])),1):
            row=source_groups[old['group']][old['index']-1]
            (guesses,rejected,signals,stages),prepared=_capture(
                row,head,model,tok,backbone,device,max_length)
            _verify_replay(row,old['group'],old['index'],cases30[(old['group'],old['index'])],guesses)
            matching=by_loss[(old['group'],old['index'],old['kind'],tuple(old['gold_span']))]
            item=_residual_audit(old,row,prepared,stages,guesses,matching['recovered'])
            _verify_loss_replay(matching,item)
            diagnostics.append(item)
            print(f'GAMER_G1_RECONSTRUCTION_VALIDATION prior_loss {i}/13',flush=True)
        if sum(not item['old_125_recovered'] for item in diagnostics)!=1:
            raise ValueError('GAMER_G1_0031_REMAINING_LOSS_DRIFT')
        for name,rows in new_groups.items():
            records=[];cases=[]
            for index,row in enumerate(rows,1):
                (guesses,rejected,signals,stages),prepared=_capture(
                    row,head,model,tok,backbone,device,max_length)
                gold=r._gold(row)
                records.append((guesses,gold))
                cases.append({'group':name,'index':index,'family':row['family'],
                    'layout':row.get('surface_shift','original'),
                    'prompt_sha256':_sha(row['prompt']),
                    'configurations':r._case_summary(guesses,gold),
                    'rejected_gold_proposals':sum((*z['bounds'],z['kind']) in gold
                        for z in rejected if z['bounds'] is not None)})
                if index==1 or index%40==0 or index==len(rows):
                    print(f'GAMER_G1_RECONSTRUCTION_VALIDATION {name} {index}/{len(rows)}',flush=True)
            result[name]=_assessment(records,rows)
            per_case.extend(cases)
    original30={name:report30['groups'][name] for name in x.GROUP_SIZES}
    legacy_checks={name:{arm:{
        'no_exact_prompt_regression':g['metrics'][arm]['exact_prompts']>=g['metrics'][f.CHALLENGER]['exact_prompts'],
        'no_f1_regression':g['metrics'][arm]['f1']>=g['metrics'][f.CHALLENGER]['f1'],
        'no_missing_object_regression':g['metrics'][arm]['missing_objects']<=g['metrics'][f.CHALLENGER]['missing_objects'],
        'no_false_positive_regression':g['metrics'][arm]['false_positive_objects']<=g['metrics'][f.CHALLENGER]['false_positive_objects'],
        'no_previously_correct_gold_lost':g['paired_vs_filter'][arm]['b_previously_correct_gold_lost']==0,
        } for arm in (BASE,)} for name,g in original30.items()}
    primary_pass=all(all(v.values()) for group in result.values() for arm,v in group['checks'].items() if arm==BASE)
    inherited_pass=all(all(v.values()) for group in legacy_checks.values() for v in group.values())
    residual=[p for p in diagnostics if not p['old_125_recovered']]
    analysis={'remaining_prior_loss':residual[0],
        'recovered_prior_losses':{arm:sum(q['candidate_recovered'][arm] for q in diagnostics)
                                  for arm in ARMS[1:]},
        'newly_lost_prior_gold_by_arm':{arm:sum(q['old_125_recovered'] and not q['candidate_recovered'][arm]
                                         for q in diagnostics) for arm in ARMS[1:]}}
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for paths,hashes in ((ood_paths,hash22),(paths26,hash26),(paths27,hash27),
                         (paths28,hash28),(paths29,hash29),(paths30,hash30)):
        for key,p in paths.items():
            if _file_sha(p)!=hashes[key]:
                raise ValueError('GAMER_G1_0031_INPUT_CHANGED:'+key)
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=info['checkpoint_files'][WINNER_HEAD]['manifest_sha256']
        or _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0031_CHECKPOINT_CHANGED')
    report={'schema':SCHEMA,'status':'DEVELOPMENT_VALIDATION_NO_PROMOTION',
        'groups':result,'residual_audit':analysis,'prior_development_checks_125':legacy_checks,
        'frozen_125_independent_validation_pass':bool(primary_pass),
        'frozen_125_all_development_checks_pass':bool(primary_pass and inherited_pass),
        'frozen_125_remaining_known_loss_count':1,
        'exploratory_shadow_arms':SHADOW,
        'shadow_arms_not_promotion_eligible_from_this_experiment':True,
        'fresh_dataset_sha256':hashlib.sha256(_jsonl_bytes(fresh)).hexdigest(),
        'fresh_seed':SEED,'fresh_case_counts':{k:len(v) for k,v in new_groups.items()},
        'fresh_layouts':list(LAYOUTS),'prompt_disjoint_new_original_and_structural':True,
        'source_0030_file_sha256':hash30,'source_0029_file_sha256':hash29,
        'source_0028_file_sha256':hash28,'source_0027_file_sha256':hash27,
        'source_0026_file_sha256':hash26,'source_0022_file_sha256':hash22,
        'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
        'source_adapter_sha256':info['adapter_sha'],
        'configurations_predeclared':list(ARMS),
        'no_training':True,'heads_frozen':True,'gold_used_after_inference_only':True,
        'production_promotion':False,'certification_authorized':False,
        'sealed_test_used':False,'challenge_or_test_split_used':False,'g2_unlocked':False,
        'interpretation':('The frozen G1-0030 1.25 strategy is evaluated on fresh, '
           'development-only original and structural prompts; one remaining historical '
           'loss is audited with exact competing spans. Shadow bonuses are exploratory '
           'and may not be promoted from this experiment.')}
    output.mkdir(parents=True)
    (output/REPORT).write_text(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
                              encoding='utf-8',newline='\n')
    (output/CASES).write_bytes(_jsonl_bytes(per_case))
    (output/LOSSES).write_bytes(_jsonl_bytes(diagnostics))
    (output/DATASET).write_bytes(_jsonl_bytes(fresh))
    return report
