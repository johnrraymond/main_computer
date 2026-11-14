"""G1-0023: paired source-vs-structural-OOD diagnostic, read-only.

The saved 0022 surface is deterministically reversed into its *same* source
example.  Gold annotation, token alignment, candidate proposals, classifier
eligibility and both frozen decoders are assessed separately.  No labels enter
inference, no checkpoint changes and no G2/certification permission.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import random
from pathlib import Path

import torch

from tools.nanojev_gamer_v1_g1_structural_ood import (
    CONTROL, GUARD, SHIFT, LAYOUTS, SECTION_NAMES, SECTION_ORDERS, SEED,
    _reshape_clause, structural_ood_row,
)
from tools.nanojev_gamer_v1_g1_action_ranking import (
    WINNER_HEAD, _candidate_scores, _file_sha, decode_action_ranked,
)
from tools.nanojev_gamer_v1_g1_action_width_validation import (
    _load_prior_inputs, _verify_no_drift,
)
from tools.nanojev_gamer_v1_g1_calibration_validation import (
    CASES, _jsonl_bytes, _summarize, _paired,
)
from tools.nanojev_gamer_v1_g1_boundary_guard_validation import MIN_SCORE_RETENTION, GUARD_ENABLED
from tools.nanojev_gamer_v1_g1_selective_conflict import selective_decode
from tools.nanojev_gamer_v1_g1_null_boundary import _decode_shift
from tools.nanojev_gamer_v1_g1_decoder_frob import _char_set
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS, validate_graph, execute_graph
from tools.nanojev_gamer_v1_graph_head import token_span_char_bounds

SCHEMA='nanojev.gamer.graph.g1.structural-failure-causal-diagnosis.v1'
OOD_FILES=('structural_ood_report.json','structural_ood_dataset.jsonl',
           'structural_ood_per_case.jsonl','frozen_development_leader.json')
EXPECTED_CONTROL={'correct':8580,'expected':9135,'missing_objects':555,
                  'false_positive_objects':2927,'exact_prompts':0}
EXPECTED_GUARD={'correct':8592,'expected':9135,'missing_objects':543,
                'false_positive_objects':2915,'exact_prompts':0}


def _read_jsonl(path):
    data=Path(path).read_text(encoding='utf-8')
    return [json.loads(x) for x in data.splitlines() if x.strip()]


def _check_jsonl(row, index):
    if row.get('family') not in SECTION_NAMES or row.get('surface_shift') not in LAYOUTS:
        raise ValueError(f'GAMER_G1_0023_BAD_OOD_ROW:{index}')
    if (row.get('split')!='ood_dev' or
        row.get('template')!='structural-ood-'+row['surface_shift'] or
        not isinstance(row.get('prompt'),str) or
        not isinstance(row.get('object_spans'),list)):
        raise ValueError(f'GAMER_G1_0023_BAD_OOD_ROW:{index}')


def restore_source(shifted):
    """Reconstruct *original* text and annotation positions, with exact round trip.

    This is a diagnostic inverse of the deterministic 0022 generator, not
    an inference-time parser.  Every remapped gold span must survive unchanged.
    """
    _check_jsonl(shifted,shifted.get('index'))
    layout=shifted['surface_shift'];family=shifted['family']
    prompt=shifted['prompt'];head=('EVIDENCE DOSSIER\n' if layout=='evidence_first'
                                      else 'DATA LEDGER\n')
    if not prompt.startswith(head) or not prompt.endswith('\n'):
        raise ValueError('GAMER_G1_0023_OOD_HEADER_MALFORMED')
    blocks=prompt[len(head):].rstrip('\n').split('\n\n')
    order=SECTION_ORDERS[family][layout]
    if len(blocks)!=len(order):
        raise ValueError('GAMER_G1_0023_OOD_SECTION_COUNT')
    restored={};region_maps={};surface_regions={}
    for order_pos,block in enumerate(blocks):
        section_index=order[order_pos]
        name=SECTION_NAMES[family][section_index]
        prefix=(f'[{name}]\n' if layout=='evidence_first' else f'{name} ::\n')
        if not block.startswith(prefix):
            raise ValueError('GAMER_G1_0023_OOD_SECTION_ORDER')
        surface=block[len(prefix):]
        original=(surface.replace('\n  - ','; ').replace(' | ', ', ')
                  if layout=='evidence_first' else
                  surface.replace('\n  • ','; ').replace(' / ', ', '))
        canonical,mapping=_reshape_clause(original,layout)
        if canonical!=surface:
            raise ValueError('GAMER_G1_0023_OOD_SEPARATOR_NONREVERSIBLE')
        start_in_shifted=prompt.index(block) + len(prefix)
        region_maps[section_index]=(start_in_shifted,mapping)
        surface_regions[section_index]=(start_in_shifted,start_in_shifted+len(surface))
        restored[section_index]=original
    if len(restored)!=len(SECTION_NAMES[family]):
        raise ValueError('GAMER_G1_0023_OOD_MISSING_SECTION')
    sections=[restored[i] for i in range(len(restored))]
    original_prompt=' '.join(sections)
    if hashlib.sha256(original_prompt.encode('utf-8')).hexdigest()!=shifted.get('source_prompt_sha256'):
        raise ValueError('GAMER_G1_0023_OOD_ORIGINAL_SHA_MISMATCH')
    original_starts={};cursor=0
    for index,section in enumerate(sections):
        original_starts[index]=cursor
        cursor+=len(section)+1
    source=deepcopy(shifted)
    source['prompt']=original_prompt;source['split']='dev'
    source['template']='reconstructed-for-0023-diagnostic'
    source.pop('surface_shift',None);source.pop('source_prompt_sha256',None)
    for node in source['graph']['nodes']:
        if 'span' not in node:continue
        a,b=node['span']
        targets=[]
        for section_index,(surface_start,mapping) in region_maps.items():
            inverse={v:k for k,v in mapping.items()}
            if (a-surface_start in inverse and b-surface_start in inverse):
                begin=original_starts[section_index]+inverse[a-surface_start]
                end=original_starts[section_index]+inverse[b-surface_start]
                if original_prompt[begin:end]==prompt[a:b]:
                    targets.append((begin,end))
        if len(targets)!=1:
            raise ValueError('GAMER_G1_0023_OOD_GOLD_SPAN_REMAP')
        node['span']=list(targets[0])
    source['object_spans']=[{'kind':node['kind'],'start':node['span'][0],
                             'end':node['span'][1]} for node in source['graph']['nodes']
                            if 'span' in node]
    validate_graph(source['graph'],complete=True)
    if execute_graph(source['graph'])['decisions']!=shifted['expected_decisions']:
        raise ValueError('GAMER_G1_0023_OOD_GRAPH_DECISION_DRIFT')
    rebuilt=structural_ood_row(source,layout=layout)
    for field in ('prompt','object_spans','graph','expected_decisions','source_prompt_sha256'):
        if rebuilt[field]!=shifted[field]:
            raise ValueError('GAMER_G1_0023_OOD_ROUNDTRIP:'+field)
    return source, surface_regions



def verify_author_gold(shifted_rows, originals):
    """Replay independent authored sources in the exact 0022 PRNG stream.

    Rejected duplicate prompts consume PRNG calls in 0022.  We advance until
    its stored pre-transform source hash, checking the entire author graph,
    not merely text recovered by inverting the formatter.
    """
    from tools.nanojev_gamer_v1_graph_corpus import encounter, allocation
    rng=random.Random(SEED)
    for index,(shifted,source) in enumerate(zip(shifted_rows,originals)):
        gen=encounter if shifted['family']=='encounter' else allocation
        for attempt in range(250):
            authored=gen(rng,'dev',index)
            if hashlib.sha256(authored['prompt'].encode('utf-8')).hexdigest()==shifted['source_prompt_sha256']:
                break
        else:
            raise ValueError('GAMER_G1_0023_AUTHORED_SOURCE_NOT_FOUND:'+str(index+1))
        for key in ('prompt','graph','object_spans','expected_decisions','family'):
            if authored[key]!=source[key]:
                raise ValueError('GAMER_G1_0023_AUTHORED_GOLD_MISMATCH:'+str(index+1)+'.'+key)
        transformed=structural_ood_row(authored,layout=shifted['surface_shift'])
        if transformed!=shifted:
            raise ValueError('GAMER_G1_0023_AUTHORED_TRANSFORM_MISMATCH:'+str(index+1))
    return len(shifted_rows)

def _load_0022(ood_run,info):
    path=Path(ood_run)
    files={name:path/name for name in OOD_FILES}
    sha={name:_file_sha(p) for name,p in files.items()}
    report=json.loads(files[OOD_FILES[0]].read_text(encoding='utf-8'))
    rows=_read_jsonl(files[OOD_FILES[1]])
    cases=_read_jsonl(files[OOD_FILES[2]])
    freeze=json.loads(files[OOD_FILES[3]].read_text(encoding='utf-8'))
    checks={
       'schema':(report.get('schema'),'nanojev.gamer.graph.g1.structural-ood-fixed-comparison.v1'),
       'status':(report.get('status'),'STRUCTURAL_OOD_STRESS_ONLY_NOT_SEALED_CERTIFICATION'),
       'cases':(report.get('cases'),CASES),
       'frozen_development_leader':(report.get('frozen_development_leader'),GUARD),
       'layouts':(report.get('layouts'),list(LAYOUTS)),
       'ood_dataset_sha256':(report.get('ood_dataset_sha256'),hashlib.sha256(_jsonl_bytes(rows)).hexdigest()),
       'source_adapter_sha256':(report.get('source_adapter_sha256'),info['adapter_sha']),
       'checkpoint_file_sha256':(report.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
       'freeze.key':(freeze.get('key'),GUARD),
       'freeze.checkpoint':(freeze.get('checkpoint'),WINNER_HEAD),
       'freeze.source_adapter_sha256':(freeze.get('source_adapter_sha256'),info['adapter_sha']),
       'freeze.checkpoint_file_sha256':(freeze.get('checkpoint_file_sha256'),info['checkpoint_files'][WINNER_HEAD]),
       'freeze.null_logit_shift':(freeze.get('null_logit_shift'),SHIFT),
       'freeze.min_score_retention':(freeze.get('min_score_retention'),MIN_SCORE_RETENTION),
       'freeze.boundary_guard':(freeze.get('boundary_guard'),True),
       'freeze.status':(freeze.get('status'),'FROZEN_DEVELOPMENT_ONLY_NOT_PRODUCTION'),
    }
    for name,expected in ((CONTROL,EXPECTED_CONTROL),(GUARD,EXPECTED_GUARD)):
        for key,value in expected.items():
            checks[f'{name}.{key}']=(report.get('metrics',{}).get(name,{}).get(key),value)
    for name,(a,b) in checks.items():
        if type(a)!=type(b) or a!=b:
            raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:'+name)
    for flag in ('heads_frozen','no_training','candidate_eligibility_unchanged',
                 'one_feature_capture_per_prompt','production_decoder_unchanged'):
        if report.get(flag) is not True:
            raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:'+flag)
    for flag in ('g2_unlocked','certification_authorized','production_promotion',
                 'challenge_or_test_split_used','sealed_test_used'):
        if report.get(flag) is not False:
            raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:'+flag)
    if (len(rows)!=CASES or len(cases)!=CASES or
        report.get('shift_distribution')!={
            f'{f}.{l}':80 for f in ('encounter','allocation') for l in LAYOUTS}):
        raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:counts')
    totals={key:Counter() for key in (CONTROL,GUARD)}
    for index,(row,case) in enumerate(zip(rows,cases),1):
        _check_jsonl(row,index)
        if (case.get('index')!=index or case.get('family')!=row['family'] or
            case.get('surface_shift')!=row['surface_shift'] or
            case.get('prompt_sha256')!=hashlib.sha256(row['prompt'].encode()).hexdigest() or
            case.get('gold_count')!=len(row['object_spans'])):
            raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:case_'+str(index))
        for name in (CONTROL,GUARD):
            part=case.get('configurations',{}).get(name,{})
            for key in ('correct','missing','false_positives'):
                if type(part.get(key)) is not int or part[key]<0:
                    raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:case_field_'+str(index))
            if (part['correct']+part['missing']!=case['gold_count'] or
                type(part.get('exact')) is not bool or
                part['exact']!=(part['missing']==0 and part['false_positives']==0)):
                raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:case_accounting_'+str(index))
            totals[name]['correct']+=part['correct']
            totals[name]['missing_objects']+=part['missing']
            totals[name]['false_positive_objects']+=part['false_positives']
            totals[name]['exact_prompts']+=part['exact']
    for name in (CONTROL,GUARD):
        for key in ('correct','missing_objects','false_positive_objects','exact_prompts'):
            if totals[name][key]!=report['metrics'][name][key]:
                raise ValueError('GAMER_G1_0023_INVALID_0022_REFERENCE:totals_'+name+'_'+key)
    return report,rows,cases,files,sha


def _region(span,regions):
    a,b,_=span
    if any(start<=a and b<=end for start,end in regions.values()):
        return 'section_content'
    if any(a<end and b>start for start,end in regions.values()):
        return 'crosses_section_content'
    return 'heading_or_layout_separator'


def _overlap_type(span,gold):
    a,b,kind=span
    if span in gold:return 'correct'
    same=any(k==kind and a<e and b>s for s,e,k in gold)
    other=any(k!=kind and a<e and b>s for s,e,k in gold)
    if same:return 'same_kind_boundary_overlap'
    if other:return 'other_kind_overlap'
    return 'unannotated_surface'


def _attribution(proposals,selected,gold,regions):
    counters=Counter()
    for item in proposals:
        if item not in gold:
            counters['proposal_false_positive.'+item[2]]+=1
            counters['proposal_false_positive.'+item[2]+'.'+_overlap_type(item,gold)]+=1
    for item in selected:
        if item in gold: continue
        counters['selected_false_positive.'+item[2]]+=1
        counters['selected_false_positive.'+item[2]+'.'+_overlap_type(item,gold)]+=1
        counters['selected_false_positive.'+item[2]+'.'+_region(item,regions)]+=1
    return counters


def _gold_stage(pred,prepared,row,proposals,selected):
    """Gold-informed stage attribution after inference; never changes proposals."""
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    aligned=cur.aligned_labels(prepared,row)['spans']
    gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
    if len(aligned)!=len(gold):
        raise ValueError('GAMER_G1_0023_GOLD_ALIGNMENT_COUNT')
    logits=pred['span_logits'].detach().float().cpu()
    valid=pred['span_valid'].detach().bool().cpu()
    counters=Counter()
    source_nodes=[node for node in row['graph']['nodes'] if 'span' in node]
    if len(source_nodes)!=len(aligned):
        raise ValueError('GAMER_G1_0023_GOLD_NODE_ALIGNMENT')
    for span,node in zip(aligned,source_nodes):
        bounds=token_span_char_bounds(span,prepared)
        kind=span['kind']
        if kind!=node['kind']:
            raise ValueError('GAMER_G1_0023_GOLD_KIND_ALIGNMENT')
        target=(node['span'][0],node['span'][1],kind)
        counters['gold.'+kind]+=1
        if bounds is None or (*bounds,kind)!=target:
            counters['gold_token_boundary_mismatch.'+kind]+=1
        if target in selected:
            counters['selected_gold.'+kind]+=1
            continue
        if bounds is None or (*bounds,kind)!=target:
            counters['missing.'+kind+'.gold_token_boundary_mismatch']+=1
            continue
        item=target
        first=span['token_first'];offset=span['token_last']-first
        if not (0<=first<valid.shape[0] and 0<=offset<valid.shape[1]) or not valid[first,offset]:
            reason='candidate_invalid'
        elif item in proposals:
            reason='decoder_excluded'
        else:
            scores=logits[first,offset]
            shifted=scores.clone();shifted[0]-=SHIFT
            best=int(shifted.argmax())
            label=SPAN_KINDS.index(kind)+1
            if best==0:reason='null_wins_after_shift'
            elif best!=label:reason='wrong_kind'
            elif float(shifted.softmax(-1)[label])<.5:
                reason='below_threshold'
            else:reason='proposal_mapping_mismatch'
        counters['missing.'+kind+'.'+reason]+=1
    return counters


def _decode_pair(pred,prepared):
    logits=pred['span_logits'];valid=pred['span_valid']
    shifted=logits.detach().float().clone();shifted[...,0]-=SHIFT
    by_end=_candidate_scores(shifted,valid,confidence=.50)
    proposals=[{'kind':SPAN_KINDS[klass-1], 'token_first':start,
                'token_last':start+offset}
               for entries in by_end for start,offset,klass,_ in entries]
    proposed=_char_set(proposals,prepared)
    base=decode_action_ranked(by_end,action_power=1.,action_penalty=0.)
    if base!=_decode_shift(logits,valid,SHIFT):
        raise ValueError('GAMER_G1_0023_BASELINE_DECODER_DRIFT')
    wide=decode_action_ranked(by_end,action_power=1.25,action_penalty=0.)
    guard,_=selective_decode(base,wide,by_end,
                     min_score_retention=MIN_SCORE_RETENTION,boundary_guard=GUARD_ENABLED)
    return proposed,_char_set(base,prepared),_char_set(guard,prepared)


def run(*,root,transfer_report,fresh_run,null_run,validation_run,ood_run,
        output,device='cuda',max_length=2048):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if type(max_length) is not int or max_length<1:
        raise ValueError('GAMER_G1_0023_INVALID_MAX_LENGTH')
    output=Path(output)
    if output.exists():raise FileExistsError('GAMER_G1_0023_IMMUTABLE_OUTPUT')
    info=_load_prior_inputs(root,transfer_report,fresh_run,null_run,validation_run,output)
    ood_report,rows,cases,files,hashes=_load_0022(ood_run,info)
    reconstructed=[restore_source(r) for r in rows]
    sources=[pair[0] for pair in reconstructed]
    verify_author_gold(rows,sources)
    if len({s['prompt'] for s in sources})!=CASES:
        raise ValueError('GAMER_G1_0023_DUPLICATE_SOURCE_PROMPTS')
    entry=info['entries'][WINNER_HEAD];manifest=entry['manifest']
    if backbone.adapter_fingerprint(manifest['source_adapter'])!=info['adapter_sha']:
        raise ValueError('GAMER_G1_0023_ADAPTER_CHANGED')
    head,loaded=cur._load_checkpoint(entry['checkpoint'],device=device)
    if loaded.get('stage')!='G1' or loaded.get('head_sha256')!=manifest['head_sha256']:
        raise ValueError('GAMER_G1_0023_HEAD_CHANGED')
    head.eval()
    model,tok=backbone.model_components(manifest['model_id'],manifest['source_adapter'],device)
    pairs=[]
    counters={key:Counter() for key in ('source','ood')}
    layout_counters={l:{s:Counter() for s in ('source','ood')} for l in LAYOUTS}
    by_layout={l:{s:[] for s in ('source','ood')} for l in LAYOUTS}
    per_case=[]
    with torch.inference_mode():
        for index,(shifted,(source,regions)) in enumerate(zip(rows,reconstructed),1):
            data={}
            for surface,row in (('source',source),('ood',shifted)):
                cap=cur.capture(model,tok,row,device,max_length)
                prepared=cap['prepared']
                pred=head(cap['hidden'].to(device=device,dtype=torch.float32),
                          eligible=prepared['eligible'].to(device),active_stage='G1')
                proposed,control,guard=_decode_pair(pred,prepared)
                gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
                if len(gold)!=len(row['object_spans']):
                    raise ValueError('GAMER_G1_0023_DUPLICATE_GOLD_OBJECT')
                if surface=='ood':
                    expected=cases[index-1]['configurations']
                    for key,guess in ((CONTROL,control),(GUARD,guard)):
                        x=expected[key]
                        if (len(guess&gold),len(gold-guess),len(guess-gold),guess==gold)!=(
                            x['correct'],x['missing'],x['false_positives'],x['exact']):
                            raise ValueError('GAMER_G1_0023_0022_PREDICTION_DRIFT:'+str(index)+'.'+key)
                stage=_gold_stage(pred,prepared,row,proposed,control)
                stage['raw_valid_token_span_count']=int(pred['span_valid'].sum().item())
                stage['eligible_token_count']=int(prepared['eligible'].sum().item())
                stage['prompt_character_count']=len(row['prompt'])
                region_map=regions if surface=='ood' else {0:(0,len(row['prompt']))}
                trace=_attribution(proposed,control,gold,region_map)
                ctr=counters[surface];ctr.update(stage);ctr.update(trace)
                layout_counters[shifted['surface_shift']][surface].update(stage)
                layout_counters[shifted['surface_shift']][surface].update(trace)
                for kind in SPAN_KINDS:
                    ctr[f'proposal_count.{kind}']+=sum(k==kind for _,_,k in proposed)
                    ctr[f'selected_count.{kind}']+=sum(k==kind for _,_,k in control)
                    layout_counters[shifted['surface_shift']][surface][f'proposal_count.{kind}']+=sum(k==kind for _,_,k in proposed)
                    layout_counters[shifted['surface_shift']][surface][f'selected_count.{kind}']+=sum(k==kind for _,_,k in control)
                data[surface]=(control,gold,proposed,guard)
                by_layout[shifted['surface_shift']][surface].append((control,gold))
            source_control,source_gold,source_proposals,_=data['source']
            ood_control,ood_gold,ood_proposals,ood_guard=data['ood']
            paired_row={
                'index':index,'family':shifted['family'],'layout':shifted['surface_shift'],
                'original_sha256':shifted['source_prompt_sha256'],
                'transformed_sha256':hashlib.sha256(shifted['prompt'].encode()).hexdigest(),
                'source':{'gold':len(source_gold),'correct':len(source_control&source_gold),
                          'missing':len(source_gold-source_control),
                          'false_positives':len(source_control-source_gold),
                          'proposal_count':len(source_proposals)},
                'ood':{'gold':len(ood_gold),'correct':len(ood_control&ood_gold),
                       'missing':len(ood_gold-ood_control),
                       'false_positives':len(ood_control-ood_gold),
                       'proposal_count':len(ood_proposals)}}
            per_case.append(paired_row)
            pairs.append((data['source'],data['ood']))
            if index==1 or index%20==0 or index==CASES:
                print(f'GAMER_G1_STRUCTURAL_DIAGNOSIS {index}/{CASES}',flush=True)
    source_records=[(p[0][0],p[0][1]) for p in pairs]
    ood_records=[(p[1][0],p[1][1]) for p in pairs]
    ood_guard_records=[(p[1][3],p[1][1]) for p in pairs]
    source_metric=_summarize(source_records,sources)
    ood_metric=_summarize(ood_records,rows)
    for surface,metric in (('source',source_metric),('ood',ood_metric)):
        for kind,kind_metric in metric['by_kind'].items():
            gold_total=counters[surface]['gold.'+kind]
            selected=counters[surface]['selected_gold.'+kind]
            misses=sum(value for key,value in counters[surface].items()
                       if key.startswith('missing.'+kind+'.'))
            if (gold_total!=kind_metric['total'] or selected!=kind_metric['correct']
                    or misses!=kind_metric['total']-kind_metric['correct']):
                raise ValueError('GAMER_G1_0023_STAGE_ACCOUNTING:'+surface+'.'+kind)
    ood_guard_metric=_summarize(ood_guard_records,rows)
    for key,metric in ((CONTROL,ood_metric),(GUARD,ood_guard_metric)):
        if metric!=ood_report['metrics'][key]:
            raise ValueError('GAMER_G1_0023_0022_FULL_METRIC_DRIFT:'+key)
    _verify_no_drift(info,transfer_report,fresh_run,manifest['source_adapter'],{}, {}, {}, {})
    for name,p in files.items():
        if _file_sha(p)!=hashes[name]:
            raise ValueError('GAMER_G1_0023_0022_FILE_CHANGED_DURING_RUN:'+name)
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    if (_file_sha(Path(entry['checkpoint'])/'manifest.json')!=
            info['checkpoint_files'][WINNER_HEAD]['manifest_sha256'] or
        _file_sha(Path(entry['checkpoint'])/cur.HEAD_FILE)!=
            info['checkpoint_files'][WINNER_HEAD]['head_file_sha256']):
        raise ValueError('GAMER_G1_0023_CHECKPOINT_CHANGED_DURING_RUN')
    report={
      'schema':SCHEMA,'status':'READ_ONLY_STRUCTURAL_FAILURE_DIAGNOSIS_NOT_CERTIFICATION',
      'cases':CASES,'head_sha256':manifest['head_sha256'],
      'checkpoint_file_sha256':info['checkpoint_files'][WINNER_HEAD],
      'source_adapter_sha256':info['adapter_sha'],
      'source_0022_report_file_sha256':hashes[OOD_FILES[0]],
      'source_0022_dataset_file_sha256':hashes[OOD_FILES[1]],
      'original_gold_reconstruction':'EXACT_ROUNDTRIP_AND_INDEPENDENT_AUTHOR_REPLAY_ALL_320',
      'source_control_metrics':source_metric,'ood_control_metrics':ood_metric,
      'ood_guard_metrics':ood_guard_metric,
      'by_layout':{layout:{surface:_summarize(by_layout[layout][surface],
                           [rows[i] if surface=='ood' else sources[i]
                            for i in range(CASES) if rows[i]['surface_shift']==layout])
                      for surface in ('source','ood')}
                   for layout in LAYOUTS},
      'stage_attribution':{surface:dict(sorted(count.items()))
                           for surface,count in counters.items()},
      'stage_attribution_by_layout':{layout:{surface:dict(sorted(count.items()))
                 for surface,count in kinds.items()}
                 for layout,kinds in layout_counters.items()},
      'source_to_ood_deltas':{
          'false_positives_by_kind':{kind:ood_metric['false_positives_by_kind'].get(kind,0)
                 -source_metric['false_positives_by_kind'].get(kind,0)
                 for kind in SPAN_KINDS},
          'missing_by_kind':{kind:ood_metric['missing_by_kind'].get(kind,0)
                 -source_metric['missing_by_kind'].get(kind,0)
                 for kind in SPAN_KINDS},
          'classified_proposals_by_kind':{kind:counters['ood']['proposal_count.'+kind]
                 -counters['source']['proposal_count.'+kind]
                 for kind in SPAN_KINDS},
          'raw_valid_token_spans':counters['ood']['raw_valid_token_span_count']
                 -counters['source']['raw_valid_token_span_count']},
      'no_training':True,'heads_frozen':True,'decoder_frozen':True,
      'gold_used_after_inference_only':True,'challenge_or_test_split_used':False,
      'sealed_test_used':False,'production_promotion':False,
      'certification_authorized':False,'g2_unlocked':False,
      'interpretation':('Paired comparison of the same annotated synthetic graph before and '
        'after deterministic structural rewriting. Source and OOD control use the same '
        'frozen model; stage counts distinguish eligible neural proposals from decoder '
        'selections. Diagnostic gold informs only post-inference analysis. '
        'No intervention, retraining, promotion, or G2 authorization.'),
    }
    output.mkdir(parents=True)
    (output/'structural_failure_report.json').write_text(
        json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+'\n',encoding='utf-8',newline='\n')
    (output/'structural_failure_pairs.jsonl').write_bytes(_jsonl_bytes(per_case))
    return report
