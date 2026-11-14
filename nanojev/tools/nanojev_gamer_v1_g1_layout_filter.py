"""G1-0026: frozen, gold-blind layout-boundary candidate filter experiment.

A fixed pre-decoding candidate mask handles generic presentation scaffolding,
never graph meaning, semantic labels or the two 0022 template names. Compare
against *unchanged* control and G1-0021 development leader on existing G1-0022
OOD and new prompt-disjoint layouts. All assessment uses gold after inference.
No training, production patch, certification, sealed tests or G2 unlock.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import random
import re

import torch

from tools.nanojev_gamer_v1_g1_structural_diagnosis import (
    _load_prior_inputs, _load_0022, _verify_no_drift, restore_source,
    verify_author_gold,
    OOD_FILES, WINNER_HEAD, CONTROL, GUARD, SHIFT,
    MIN_SCORE_RETENTION, GUARD_ENABLED, _file_sha,
)
from tools.nanojev_gamer_v1_g1_action_ranking import _candidate_scores, decode_action_ranked
from tools.nanojev_gamer_v1_g1_selective_conflict import selective_decode
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift
from tools.nanojev_gamer_v1_g1_calibration_validation import _summarize, _jsonl_bytes
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_g1_structural_ood import (
    SECTION_NAMES, _pieces,
)
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS, validate_graph, execute_graph
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA = 'nanojev.gamer.graph.g1.layout-candidate-filter-development.v1'
CHALLENGER = 'layout_boundary_filter_0026'
NOVEL_SEED = 260026
NOVEL_CASES = 160
NOVEL_LAYOUTS = ('markdown_note','key_value_index')
EXPECTED_DIAGNOSIS = {'cases':320,'original_gold_reconstruction':
    'EXACT_ROUNDTRIP_AND_INDEPENDENT_AUTHOR_REPLAY_ALL_320',
    'source_control_exact':130,'source_control_correct':8854,
    'ood_control_exact':0,'ood_guard_exact':0}

# This grammar is deliberately general and predeclared; NO template names,
# graph field values, gold positions or test statistics enter the filter.
_HEADING_PATTERNS = (
    re.compile(r'^\s*\[[^\]\n]{1,80}\]\s*$'),
    re.compile(r'^\s*#{1,6}\s+\S[^\n]{0,79}\s*$'),
    re.compile(r'^\s*[A-Za-z][\w -]{0,78}\s*::\s*$'),
    re.compile(r'^\s*[A-Za-z][\w -]{0,78}:\s*$'),
    re.compile(r'^\s*[A-Z][A-Z _/-]{2,79}\s*$'),
)
_INLINE_SEPARATORS = (' | ', ' / ', ' ~ ', ' ↦ ')
_BULLET = re.compile(r'^\s*(?:[-*•>])\s+')


def _heading_ranges(prompt):
    """All character ranges occupied by standalone layout/scaffold lines."""
    ranges=[]
    pos=0
    for line in prompt.splitlines(keepends=True):
        surface=line.rstrip('\r\n')
        if any(pattern.fullmatch(surface) for pattern in _HEADING_PATTERNS):
            ranges.append((pos,pos+len(surface)))
        pos+=len(line)
    return ranges


def exclusion_reason(prompt,bounds,heading_ranges=None):
    """Pure inference-time rule operating on prompt text and token offsets."""
    if bounds is None:
        return 'unmapped_span'
    a,b=bounds
    if not (0<=a<b<=len(prompt)):
        raise ValueError('GAMER_G1_0026_BAD_OFFSET')
    surface=prompt[a:b]
    if '\n' in surface or '\r' in surface:
        return 'crosses_line'
    ranges=_heading_ranges(prompt) if heading_ranges is None else heading_ranges
    if any(a<end and b>start for start,end in ranges):
        return 'scaffold_heading'
    if any(separator in surface for separator in _INLINE_SEPARATORS):
        return 'inline_separator'
    if _BULLET.match(surface):
        return 'bullet_prefix'
    if not any(char.isalnum() for char in surface):
        return 'syntax_only'
    return None


def filter_by_end(by_end,prepared):
    """Leave neural logits/margins unchanged; exclude only presentation spans."""
    prompt=prepared['prompt']
    headings=_heading_ranges(prompt)
    clean=[]; rejected=[];kept=0
    for end,entries in enumerate(by_end):
        survivors=[]
        for start,offset,klass,margin in entries:
            if not (0<=start and offset>=0 and start+offset+1==end
                    and 1<=klass<=len(SPAN_KINDS)):
                raise ValueError('GAMER_G1_0026_BAD_PROPOSAL')
            kind=SPAN_KINDS[klass-1]
            span={'kind':kind,'token_first':start,'token_last':start+offset}
            bounds=token_span_char_bounds(span,prepared)
            reason=exclusion_reason(prompt,bounds,headings)
            if reason is not None:
                rejected.append({'kind':kind,'reason':reason,'bounds':bounds,
                                 'token_first':start,'token_last':start+offset})
            else:
                survivors.append((start,offset,klass,margin));kept+=1
        clean.append(survivors)
    return clean,rejected,kept


def frozen_decode(pred,prepared):
    """One neural forward pass, three frozen decodings, gold never an input."""
    logits=pred['span_logits'];valid=pred['span_valid']
    shifted=logits.detach().float().clone();shifted[...,0]-=SHIFT
    by_end=_candidate_scores(shifted,valid,confidence=.50)
    baseline=decode_action_ranked(by_end,action_power=1.,action_penalty=0.)
    if baseline!=_decode_shift(logits,valid,SHIFT):
        raise ValueError('GAMER_G1_0026_BASELINE_DECODER_DRIFT')
    wide=decode_action_ranked(by_end,action_power=1.25,action_penalty=0.)
    guard,_=selective_decode(baseline,wide,by_end,
        min_score_retention=MIN_SCORE_RETENTION,boundary_guard=GUARD_ENABLED)
    filtered,rejected,kept=filter_by_end(by_end,prepared)
    filter_base=decode_action_ranked(filtered,action_power=1.,action_penalty=0.)
    filter_wide=decode_action_ranked(filtered,action_power=1.25,action_penalty=0.)
    filt,_=selective_decode(filter_base,filter_wide,filtered,
        min_score_retention=MIN_SCORE_RETENTION,boundary_guard=GUARD_ENABLED)
    return {CONTROL:_char_set(baseline,prepared),
            GUARD:_char_set(guard,prepared),CHALLENGER:_char_set(filt,prepared)}, rejected, kept


def _reshape_new(text,layout):
    """Rewrite only outside mentions; return old->new byte-free char offsets."""
    mapped={0:0};pieces=[];i=0;pos=0
    while i<len(text):
        if text.startswith('; ',i):
            replacement=('\n* ' if layout=='markdown_note' else '\n> ')
            i+=2
        elif text.startswith(', ',i):
            replacement=(' ~ ' if layout=='markdown_note' else ' ↦ ')
            i+=2
        else:
            replacement=text[i];i+=1
        pieces.append(replacement);pos+=len(replacement);mapped[i]=pos
    return ''.join(pieces),mapped


def novel_layout_row(source,layout):
    """Two new predeclared formats, distinct from both prior 0022 layouts."""
    if layout not in NOVEL_LAYOUTS or source.get('split')!='dev':
        raise ValueError('GAMER_G1_0026_BAD_FRESH_SOURCE')
    family=source['family']
    pieces=_pieces(source['prompt'],len(SECTION_NAMES[family]))
    order=tuple(reversed(range(len(pieces))))
    head=('# Field Notes\n\n' if layout=='markdown_note' else 'Record Index:\n')
    chunks=[head];pos=len(head);mapped={}
    for idx in order:
        start,end,section=pieces[idx]
        label=SECTION_NAMES[family][idx].title()
        prefix=(f'## {label}\n' if layout=='markdown_note' else f'{label}:\n')
        chunks.append(prefix);pos+=len(prefix)
        formatted,local=_reshape_new(section,layout)
        for old,new in local.items():mapped[start+old]=pos+new
        chunks.append(formatted);pos+=len(formatted)
        chunks.append('\n\n');pos+=2
    text=''.join(chunks).rstrip()+'\n'
    updated=deepcopy(source)
    updated['prompt']=text;updated['split']='ood_dev'
    updated['template']='unseen-layout-'+layout
    updated['surface_shift']=layout
    updated['source_prompt_sha256']=hashlib.sha256(source['prompt'].encode()).hexdigest()
    for node in updated['graph']['nodes']:
        if 'span' not in node:continue
        a,b=node['span']
        if a not in mapped or b not in mapped or text[mapped[a]:mapped[b]]!=source['prompt'][a:b]:
            raise ValueError('GAMER_G1_0026_GOLD_TRANSFORM_DRIFT')
        node['span']=[mapped[a],mapped[b]]
    updated['object_spans']=[{'kind':n['kind'],'start':n['span'][0],
                              'end':n['span'][1]} for n in updated['graph']['nodes'] if 'span' in n]
    validate_graph(updated['graph'],complete=True)
    if execute_graph(updated['graph'])['decisions']!=source['expected_decisions']:
        raise ValueError('GAMER_G1_0026_SEMANTIC_DRIFT')
    return updated


def fresh_layout_rows(info,ood_rows,*,cases=NOVEL_CASES,seed=NOVEL_SEED):
    """New independent development sources and layouts, fixed before inference."""
    from tools.nanojev_gamer_v1_graph_corpus import encounter,allocation
    from tools.nanojev_gamer_v1_g1_fresh_dev import _original_prompts
    if type(cases) is not int or cases!=NOVEL_CASES or type(seed) is not int or seed!=NOVEL_SEED:
        raise ValueError('GAMER_G1_0026_FRESH_SETTINGS_LOCKED')
    prior={r['source_prompt_sha256'] for r in ood_rows}
    prior.update(hashlib.sha256(r['prompt'].encode()).hexdigest()
                 for r in info['fresh_rows']+info['validation_rows'])
    prior.update(hashlib.sha256(prompt.encode()).hexdigest()
                 for prompt in _original_prompts(info['dataset']))
    rng=random.Random(seed);records=[];seen=set()
    for idx in range(cases):
        generator=encounter if idx%2==0 else allocation
        layout=NOVEL_LAYOUTS[(idx//2)%2]
        for _ in range(250):
            source=generator(rng,'dev',idx)
            digest=hashlib.sha256(source['prompt'].encode()).hexdigest()
            if digest not in prior and digest not in seen:break
        else:
            raise ValueError('GAMER_G1_0026_FRESH_PROMPTS_EXHAUSTED')
        seen.add(digest)
        records.append(novel_layout_row(source,layout))
    if len({r['prompt'] for r in records})!=cases:
        raise ValueError('GAMER_G1_0026_DUPLICATE_FRESH_PROMPT')
    return records


def load_diagnosis(path,ood_report,ood_files,ood_hashes,info):
    """Tie forensic findings and frozen OOD references to actual physical files."""
    name=Path(path)/'structural_failure_report.json'
    report=json.loads(name.read_text(encoding='utf-8'))
    checks={
       'schema':(report.get('schema'),'nanojev.gamer.graph.g1.structural-failure-causal-diagnosis.v1'),
       'status':(report.get('status'),'READ_ONLY_STRUCTURAL_FAILURE_DIAGNOSIS_NOT_CERTIFICATION'),
       'cases':(report.get('cases'),EXPECTED_DIAGNOSIS['cases']),
       'roundtrip':(report.get('original_gold_reconstruction'),EXPECTED_DIAGNOSIS['original_gold_reconstruction']),
       'source_exact':(report.get('source_control_metrics',{}).get('exact_prompts'),EXPECTED_DIAGNOSIS['source_control_exact']),
       'source_correct':(report.get('source_control_metrics',{}).get('correct'),EXPECTED_DIAGNOSIS['source_control_correct']),
       'ood_control':(report.get('ood_control_metrics'),ood_report['metrics'][CONTROL]),
       'ood_guard':(report.get('ood_guard_metrics'),ood_report['metrics'][GUARD]),
       'ood_report_sha':(report.get('source_0022_report_file_sha256'),ood_hashes[OOD_FILES[0]]),
       'ood_dataset_sha':(report.get('source_0022_dataset_file_sha256'),ood_hashes[OOD_FILES[1]]),
       'adapter':(report.get('source_adapter_sha256'),info['adapter_sha']),
       'head':(report.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
    }
    for key,(actual,expected) in checks.items():
        if type(actual) is not type(expected) or actual!=expected:
            raise ValueError('GAMER_G1_0026_DIAGNOSIS_REFERENCE_DRIFT:'+key)
    for flag in ('no_training','heads_frozen','decoder_frozen','gold_used_after_inference_only'):
        if report.get(flag) is not True:
            raise ValueError('GAMER_G1_0026_DIAGNOSIS_REFERENCE_DRIFT:'+flag)
    for flag in ('sealed_test_used','production_promotion','certification_authorized','g2_unlocked'):
        if report.get(flag) is not False:
            raise ValueError('GAMER_G1_0026_DIAGNOSIS_REFERENCE_DRIFT:'+flag)
    return report,name,_file_sha(name)


def _paired_any(a,b):
    """Exact matched-pair inference for arbitrary predeclared layout group sizes."""
    if not a or len(a)!=len(b):
        raise ValueError('GAMER_G1_0026_UNPAIRED')
    result=Counter()
    for (pred_a,gold_a),(pred_b,gold_b) in zip(a,b):
        if gold_a!=gold_b:
            raise ValueError('GAMER_G1_0026_GOLD_DRIFT')
        result['b_only_exact']+=int(pred_b==gold_b and pred_a!=gold_a)
        result['a_only_exact']+=int(pred_a==gold_a and pred_b!=gold_b)
        result['b_newly_recovered_gold']+=len((pred_b&gold_b)-pred_a)
        result['b_previously_correct_gold_lost']+=len((pred_a&gold_a)-pred_b)
        result['b_new_false_positives']+=len((pred_b-gold_b)-pred_a)
        result['b_old_false_positives_removed']+=len((pred_a-gold_a)-pred_b)
    discordant=result['b_only_exact']+result['a_only_exact']
    tail=min(result['b_only_exact'],result['a_only_exact'])
    p=(min(1.,2.*math.fsum(math.comb(discordant,i)*(.5**discordant)
                         for i in range(tail+1))) if discordant else 1.)
    return {**{k:int(result[k]) for k in (
        'b_only_exact','a_only_exact','b_newly_recovered_gold',
        'b_previously_correct_gold_lost','b_new_false_positives',
        'b_old_false_positives_removed')},
        'exact_discordant_pairs':discordant,'mcnemar_exact_two_sided_p':p}


def _assess(records,rows,stats):
    metrics={name:_summarize([(p[name],g) for p,g in records],rows)
             for name in (CONTROL,GUARD,CHALLENGER)}
    base=[(p[GUARD],g) for p,g in records]
    filtered=[(p[CHALLENGER],g) for p,g in records]
    return {'metrics':metrics,'paired_vs_frozen_guard':_paired_any(base,filtered),
            'filter_diagnostics':dict(sorted(stats.items()))}


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        diagnosis_run,output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0026_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0026_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    ood_report,ood_rows,old_cases,ood_files,ood_hashes=_load_0022(ood_run,info)
    diagnosis,diagnosis_file,diagnosis_hash=load_diagnosis(
        diagnosis_run,ood_report,ood_files,ood_hashes,info)
    original=[restore_source(row)[0] for row in ood_rows]
    verify_author_gold(ood_rows,original)
    if any(_heading_ranges(row['prompt']) for row in original):
        raise ValueError('GAMER_G1_0026_SOURCE_LAYOUT_UNEXPECTED')
    novel=fresh_layout_rows(info,ood_rows)
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0026_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0026_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    groups={'known_layout':ood_rows,'new_layout':novel}
    evidence={};per_case=[];rule_stats=Counter()
    with torch.inference_mode():
        for group,rows in groups.items():
            records=[];group_stats=Counter()
            layout_stats={layout:Counter() for layout in {r['surface_shift'] for r in rows}}
            for index,row in enumerate(rows,1):
                # Strictly prompt-only capture. No gold labels supplied to model.
                prepared=backbone.prepare_prompt(tok,row['prompt'],max_input_tokens=max_length)
                hidden=backbone.frozen_context(model,prepared,device=device).detach().to(
                    device='cpu',dtype=torch.float16).contiguous()
                prediction=head(hidden.to(device=device,dtype=torch.float32),
                    eligible=prepared['eligible'].to(device),active_stage='G1')
                guesses,rejected,kept=frozen_decode(prediction,prepared)
                gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
                if len(gold)!=len(row['object_spans']):
                    raise ValueError('GAMER_G1_0026_DUPLICATE_GOLD')
                if group=='known_layout':
                    expected=old_cases[index-1]['configurations']
                    for name in (CONTROL,GUARD):
                        actual=guesses[name];saved=expected[name]
                        if (len(actual&gold),len(gold-actual),len(actual-gold),actual==gold)!=(
                            saved['correct'],saved['missing'],saved['false_positives'],saved['exact']):
                            raise ValueError('GAMER_G1_0026_OOD_REPLAY_DRIFT:'+str(index)+'.'+name)
                discarded_gold=sum((*item['bounds'],item['kind']) in gold
                                   for item in rejected if item['bounds'] is not None)
                counters=Counter({'proposals_rejected':len(rejected),
                                  'proposals_kept':kept,
                                  'gold_proposals_rejected':discarded_gold,
                                  'gold_objects_lost_vs_guard':len((guesses[GUARD]&gold)-guesses[CHALLENGER]),
                                  'gold_objects_recovered_vs_guard':len((guesses[CHALLENGER]&gold)-guesses[GUARD]),
                                  'false_positives_removed_vs_guard':len((guesses[GUARD]-gold)-guesses[CHALLENGER]),
                                  'false_positives_added_vs_guard':len((guesses[CHALLENGER]-gold)-guesses[GUARD])})
                for item in rejected:
                    counters['rejected.'+item['reason']]+=1
                    counters['rejected_kind.'+item['kind']]+=1
                    counters['rejected_reason_kind.'+item['reason']+'.'+item['kind']]+=1
                group_stats.update(counters)
                layout_stats[row['surface_shift']].update(counters)
                records.append((guesses,gold))
                per_case.append({'group':group,'index':index,'layout':row['surface_shift'],
                    'family':row['family'],'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                    'gold_count':len(gold),'rejections':len(rejected),
                    'rejected_gold_proposals':discarded_gold,
                    'configurations':{name:{'correct':len(guess&gold),'missing':len(gold-guess),
                        'false_positives':len(guess-gold),'exact':guess==gold}
                        for name,guess in guesses.items()}})
                if index==1 or index%20==0 or index==len(rows):
                    print(f'GAMER_G1_LAYOUT_FILTER {group} {index}/{len(rows)}',flush=True)
            evidence[group]=_assess(records,rows,group_stats)
            rule_stats.update(group_stats)
            evidence[group]['by_layout']={layout:_assess(
                [records[i] for i,r in enumerate(rows) if r['surface_shift']==layout],
                [r for r in rows if r['surface_shift']==layout],layout_stats[layout])
                for layout in sorted({r['surface_shift'] for r in rows})}
    if evidence['known_layout']['metrics'][CONTROL]!=ood_report['metrics'][CONTROL] or \
       evidence['known_layout']['metrics'][GUARD]!=ood_report['metrics'][GUARD]:
        raise ValueError('GAMER_G1_0026_FROZEN_METRIC_DRIFT')
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{},{},{},{})
    for name,p in ood_files.items():
        if _file_sha(p)!=ood_hashes[name]:
            raise ValueError('GAMER_G1_0026_OOD_FILE_CHANGED:'+name)
    if _file_sha(diagnosis_file)!=diagnosis_hash:
        raise ValueError('GAMER_G1_0026_DIAGNOSIS_FILE_CHANGED')
    if _file_sha(Path(entry['checkpoint'])/'manifest.json')!=info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or \
       _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']:
        raise ValueError('GAMER_G1_0026_CHECKPOINT_CHANGED')
    meets_fixed_checks=(rule_stats['gold_proposals_rejected']==0 and
        all(block['metrics'][CHALLENGER]['false_positive_objects']<block['metrics'][GUARD]['false_positive_objects']
            and block['metrics'][CHALLENGER]['missing_objects']<=block['metrics'][GUARD]['missing_objects']
            for block in (evidence['known_layout'],evidence['new_layout'])))
    report={'schema':SCHEMA,'status':'INFERENCE_ONLY_DEVELOPMENT_FILTER_NOT_CERTIFICATION',
       'known_layout_cases':len(ood_rows),'new_layout_cases':len(novel),
       'new_layout_seed':NOVEL_SEED,'new_layouts':list(NOVEL_LAYOUTS),
       'new_layout_dataset_sha256':hashlib.sha256(_jsonl_bytes(novel)).hexdigest(),
       'diagnosis_file_sha256':diagnosis_hash,
       'ood_report_file_sha256':ood_hashes[OOD_FILES[0]],
       'ood_dataset_file_sha256':ood_hashes[OOD_FILES[1]],
       'source_adapter_sha256':info['adapter_sha'],
       'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
       'source_control_reference':diagnosis['source_control_metrics'],
       'configurations_predeclared':[CONTROL,GUARD,CHALLENGER],
       'candidate_rule_predeclared':{
           'line_crossings':True,'standalone_scaffold_headings':True,
           'inline_structural_separators':list(_INLINE_SEPARATORS),
           'bullet_prefixes':True,'syntax_only':True,
           'no_graph_values_or_templates':True},
       'groups':evidence,'rule_counts_all':dict(sorted(rule_stats.items())),
       'experimental_checks_met':meets_fixed_checks,
       'promotion_authorized':False,'no_training':True,'heads_frozen':True,
       'decoder_guard_unchanged':True,'gold_used_after_inference_only':True,
       'challenge_or_test_split_used':False,'sealed_test_used':False,
       'certification_authorized':False,'g2_unlocked':False,
       'interpretation':('Frozen neural proposals filtered by generic text-layout boundaries '
           'before the same existing weighted-interval decoder and selective guard. '
           'Original author source and frozen 0022 outputs preserved; two new unseen '
           'development layouts included. No training, certification or promotion.')}
    output.mkdir(parents=True)
    (output/'layout_filter_report.json').write_text(
        json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',
        encoding='utf-8',newline='\n')
    (output/'layout_filter_per_case.jsonl').write_bytes(_jsonl_bytes(per_case))
    (output/'layout_filter_new_layouts.jsonl').write_bytes(_jsonl_bytes(novel))
    return report
