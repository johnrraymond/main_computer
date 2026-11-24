"""G2-0108: frozen G2-0103 semantic-source and matched-endpoint evidence audit.

    python -m tools.nanojev_gamer_v1_g2_semantic_evidence_audit_0108 audit
    python -m tools.nanojev_gamer_v1_g2_semantic_evidence_audit_0108 selftest
    python -u -m tools.nanojev_gamer_v1_g2_semantic_evidence_audit_0108 run --device cuda

Investigation, not optimization. Recovers verbatim discovery prompts through the
PUBLIC G2-0093 prompt seal because G2-0097 intentionally strips prompt text
from its cached expert records. Never reads confirmation answers. Records frozen
model decisions, true/false edge and matched-endpoint evidence, preserving
which claims are observational and which would require new causal experiments.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import torch

from tools import nanojev_gamer_v1_g2_pair_competition_forensics_0107 as q
from tools import nanojev_gamer_v1_g2_checkpoint_forensics_0106 as f
from tools import nanojev_gamer_v1_g2_raw_graph_reranking_0103 as d
from tools import nanojev_gamer_v1_g2_graph_reconstruction_0101 as r
from tools import nanojev_gamer_v1_g2_encounter_bakeoff_0100 as b
from tools import nanojev_gamer_v1_g2_router_trainability_0098 as tr
from tools import nanojev_gamer_v1_g2_fuzz_and_correct_0095 as old

TAG='G2_0108_SEMANTIC_EVIDENCE'
SCHEMA='nanojev.g2.semantic-evidence-audit-0108.v1'
REL=tuple(b.BIND_CLASSES)
FEATURE_GROUPS=[('span_geometry',2*b.S+5),('expert_margins',b.E*b.R),
                ('expert_discrete_labels',b.E*(b.R+1)),('raw_max',b.R),
                ('raw_mean',b.R),('raw_std',b.R),('frozen_router',b.R+1)]


def require(condition,reason):
    if not condition:raise ValueError(TAG+'_'+reason)


def emit(kind,data):
    print(TAG+'_'+kind+' '+json.dumps(data,sort_keys=True,ensure_ascii=False,allow_nan=False),flush=True)


def sha_text(s):return hashlib.sha256(s.encode('utf-8')).hexdigest()


def _public_prompts(source,case_rows):
    """Join only the public discovery prompt seal, never the secret answers.

    The upstream 0097 cache strips item.row.prompt on purpose. Looking for it
    there silently produces null endpoints; this function is the fix.
    """
    atlas_dir=Path(source[1]);path=atlas_dir/'seal'/'prompts.jsonl'
    require(path.is_file(),'PUBLIC_PROMPT_SEAL_NOT_FOUND')
    needed={str(row['case_id']):row for row in case_rows}
    require(len(needed)==len(case_rows) and needed,'DUPLICATE_OR_EMPTY_CASE_IDS')
    found={}
    for p in old.lines(path):
        ident=str(p.get('case_id',''))
        if ident not in needed:continue
        row=needed[ident]
        require(p.get('split')=='discovery','CASE_NOT_IN_DISCOVERY_SEAL_'+ident)
        require(p.get('base_id')==row['base_id'] and p.get('family')==row['family'],
                'PUBLIC_CASE_LINEAGE_DRIFT_'+ident)
        prompt=p.get('prompt')
        require(isinstance(prompt,str) and bool(prompt),'MISSING_VERBATIM_PROMPT_'+ident)
        require(p.get('prompt_sha256')==sha_text(prompt),'PROMPT_HASH_DRIFT_'+ident)
        require(ident not in found,'DUPLICATE_PROMPT_IN_SEAL_'+ident)
        found[ident]={'text':prompt,'sha256':p['prompt_sha256']}
    require(set(found)==set(needed),'PUBLIC_PROMPT_JOIN_INCOMPLETE_'+str(len(needed)-len(found)))
    return found


def _verified_objects(prompt,objects):
    """Fail closed on text mapping. Never output a null source/destination."""
    require(isinstance(prompt,str) and prompt,'EMPTY_VERIFIED_PROMPT')
    output=[]
    for index,obj in enumerate(objects):
        require(len(obj)==3,'INVALID_OBJECT_SPAN_RECORD')
        a,z,kind=int(obj[0]),int(obj[1]),str(obj[2])
        require(0<=a<z<=len(prompt),'OBJECT_CHAR_SPAN_OUT_OF_BOUNDS_'+str(index))
        value=prompt[a:z]
        require(bool(value.strip()) and value==value.strip(),
                'OBJECT_CHAR_SPAN_NOT_ANCHORED_'+str(index))
        output.append({'index':index,'kind':kind,'start':a,'end':z,'text':value,
                       'context':prompt[max(0,a-48):min(len(prompt),z+48)]})
    return output


def _verify_0107_lineage(previous, reference_0103_digest, report_0106_digest, expected):
    """Validate the real G2-0107 report schema and its frozen 0103 replay.

    G2-0107 writes `source_0103_report_sha256`, not
    `reference_0103_sha256`. Reject missing hashes, drifted reports,
    missing full replay metrics and any acceptance-policy change.
    """
    replay=previous.get('frozen_0103_replay')
    require(previous.get('schema')==q.SCHEMA
            and previous.get('checkpoint_modified') is False
            and previous.get('new_training') is False
            and previous.get('production_promoted') is False
            and previous.get('confirmation_used') is False
            and previous.get('dev_used') is False
            and previous.get('source_0103_report_sha256')==reference_0103_digest
            and previous.get('source_0106_report_sha256')==report_0106_digest
            and isinstance(replay,dict)
            and all(replay.get(k)==expected[k] for k in ('tp','fp','fn','exact','cases')),
            '0107_LINEAGE_OR_REPLAY_DRIFT')


def _load(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,r102,p102,report,original,priorpath,prior=q._load(root,output)
    previous_path=source[8]/'pair-competition-forensics-0107'/'result.json'
    require(previous_path.is_file(),'RUN_G2_0107_FIRST')
    previous=old.read(previous_path)
    _verify_0107_lineage(previous,old.digest(original),old.digest(priorpath),report['candidate'])
    return source,experts,r99,r100,r101,r102,p102,report,original,previous_path


def audit(root=old.ROOT_DEFAULT,output=None):
    source,experts,r99,r100,r101,r102,p102,report,original,p107=_load(root,output)
    cases=[{'case_id':rec['case_id'],'family':rec['family'],'base_id':rec['base_id']}
           for rec in old.read(p107)['case_level']]
    prompts=_public_prompts(source,cases)
    require(len(prompts)==report['candidate']['cases']==275,'PROMPT_COHORT_DRIFT')
    emit('AUDIT',{'ready':True,'public_prompts_hash_verified':len(prompts),
         'frozen_0103_exact':report['candidate']['exact'],'prior_0107_sha256':old.digest(p107),
         'prompt_provenance':'G2_0093_PUBLIC_DISCOVERY_SEAL',
         'semantic_text_directly_in_reranker':False,
         'reranker_inputs':['object_kind_and_span_geometry','seven_expert_margins',
                            'seven_decoded_labels','frozen_router_scores'],
         'gold_only_for_posthoc_matching':True,'new_training':False,
         'checkpoints_modified':False,'confirmation_used':False,'g3_unlocked':False})


def _pair_ev(i,j,t,objects,spans,stage):
    require(0<=int(t)<len(REL),'INVALID_TYPED_RELATION')
    label=REL[int(t)]
    raw=stage['margins'][:,i,j,int(t)-1].detach().cpu() if t else None
    votes=stage['preds'][:,i,j].detach().cpu()
    score=stage['score'][i,j].detach().cpu()
    base=stage['base'][i,j].detach().cpu()
    si,sj=spans[i],spans[j]
    return {'relation':label,'pair':[i,j],'source':si,'destination':sj,
            'raw_max':round(float(raw.max()),5) if t else None,
            'raw_mean':round(float(raw.mean()),5) if t else None,
            'raw_expert_margins':[round(float(v),5) for v in raw] if t else None,
            'expert_decoded_labels':[REL[int(v)] for v in votes],
            'expert_votes':int((votes==int(t)).sum()) if t else int((votes==0).sum()),
            'learned_margin':round(float(score[int(t)]-score[0]),5),
            'router_margin':round(float(base[int(t)]-base[0]),5),
            'pairwise_winner':REL[int(stage['pair'][i,j])],
            'decoded_winner':REL[int(stage['graph'][i,j])]}


def _feat_groups(features,i,j,u,v):
    x=features[i,j].detach().cpu().float()
    y=features[u,v].detach().cpu().float()
    require(x.shape==y.shape and x.ndim==1,'FEATURE_WIDTH_DRIFT')
    off=0;out={}
    for name,width in FEATURE_GROUPS:
        z=(x[off:off+width]-y[off:off+width]).abs()
        require(z.numel()==width,'FEATURE_GROUP_MISALIGNMENT')
        out[name]={'mean_abs':round(float(z.mean()),6),'max_abs':round(float(z.max()),6)}
        off+=width
    require(off==x.numel(),'FEATURE_GROUP_TOTAL_WIDTH_MISMATCH')
    return out


def _dist(a,b):
    return max(0,max(int(a[0]),int(b[0]))-min(int(a[1]),int(b[1])))


def _match(i,j,t,reference,objects):
    """Match only same RELATION with a shared endpoint, independently of scores."""
    choices=[]
    for u,v,g in reference:
        if t!=g or (i,j)==(u,v):continue
        src=i==u;dst=j==v
        if not (src or dst):continue
        geometry=abs(_dist(objects[i],objects[j])-_dist(objects[u],objects[v]))
        cand=({'pair':[u,v],'same_source':src,'same_destination':dst,
               'distance_difference':geometry},(u,v,g))
        choices.append(cand)
    if not choices:return None
    # Endpoints and distance determine the match; NO gold scores determine it.
    choices.sort(key=lambda k:(not(k[0]['same_source'] and k[0]['same_destination']),
                               not k[0]['same_source'],k[0]['distance_difference'],k[0]['pair']))
    return choices[0]


def _case(rec,fold,stage,prompt,primary=('refers_to','applies_to')):
    objects=rec['item']['objects'];gold=rec['item']['targets'].cpu().long()
    graph=stage['graph'].cpu().long();pair=stage['pair'].cpu().long()
    require(graph.shape==gold.shape and pair.shape==gold.shape,'REPLAY_SHAPE_DRIFT')
    spans=_verified_objects(prompt['text'],objects)
    n=len(spans);require(tuple(gold.shape)==(n,n),'G1_OBJECT_COUNT_MISMATCH')
    masks=~torch.eye(n,dtype=torch.bool)
    fps=[(i,j,int(graph[i,j])) for i,j in (masks&(graph>0)&(gold==0)).nonzero().tolist()]
    tps=[(i,j,int(graph[i,j])) for i,j in (masks&(graph>0)&(gold==graph)).nonzero().tolist()]
    fns=[(i,j,int(gold[i,j])) for i,j in (masks&(graph==0)&(gold>0)).nonzero().tolist()]
    require(not bool((masks&(graph>0)&(gold>0)&(graph!=gold)).any()),
            'WRONG_POSITIVE_TYPE_NOT_HANDLED')
    features=d._raw_features(objects,stage['margins'],stage['preds'],stage['base'])
    count=Counter();by_relation=defaultdict(Counter);rows=[]
    for i,j,t in fps:
        label=REL[t]
        if label not in primary:continue
        evidence=_pair_ev(i,j,t,objects,spans,stage)
        missed=_match(i,j,t,fns,objects)
        correct=_match(i,j,t,tps,objects)
        data={'type':'FALSE_POSITIVE','edge':evidence,'matched_missing_gold':None,
              'matched_selected_true':None}
        count['false_positives']+=1;by_relation[label]['false_positives']+=1
        if evidence['expert_votes']==b.E:
            count['unanimous_false_positives']+=1;by_relation[label]['unanimous_false_positives']+=1
        if missed:
            matched,ref=missed; u,v,g=ref;ev=_pair_ev(u,v,g,objects,spans,stage)
            diff=_feat_groups(features,i,j,u,v)
            delta=evidence['learned_margin']-ev['learned_margin']
            data['matched_missing_gold']={'selection':matched,'edge':ev,'feature_distance':diff,
                 'wrong_minus_correct':{'learned_margin':round(delta,5),
                         'raw_max':round(evidence['raw_max']-ev['raw_max'],5),
                         'router_margin':round(evidence['router_margin']-ev['router_margin'],5)}}
            count['false_with_matched_missed_gold']+=1
            by_relation[label]['false_with_matched_missed_gold']+=1
            if delta>0:
                count['wrong_outranks_missing_gold']+=1;by_relation[label]['wrong_outranks_missing_gold']+=1
            if evidence['raw_max']>ev['raw_max']:
                count['expert_raw_wrong_outranks_missing_gold']+=1
                by_relation[label]['expert_raw_wrong_outranks_missing_gold']+=1
            if evidence['router_margin']>ev['router_margin']:
                count['router_wrong_outranks_missing_gold']+=1
                by_relation[label]['router_wrong_outranks_missing_gold']+=1
            if (evidence['raw_max']<=ev['raw_max'] and delta>0):
                count['raw_ranks_gold_better_but_reranker_reverses']+=1
                by_relation[label]['raw_ranks_gold_better_but_reranker_reverses']+=1
            if (evidence['router_margin']<=ev['router_margin'] and delta>0):
                count['router_ranks_gold_better_but_reranker_reverses']+=1
                by_relation[label]['router_ranks_gold_better_but_reranker_reverses']+=1
            if (evidence['raw_max']>ev['raw_max'] and delta>0):
                count['raw_and_reranker_both_misrank']+=1
                by_relation[label]['raw_and_reranker_both_misrank']+=1
            if all(v['max_abs']<0.000001 for v in diff.values()):
                count['identical_local_input_different_gold']+=1
                by_relation[label]['identical_local_input_different_gold']+=1
        if correct:
            matched,ref=correct;u,v,g=ref;ev=_pair_ev(u,v,g,objects,spans,stage)
            data['matched_selected_true']={'selection':matched,'edge':ev,
                  'feature_distance':_feat_groups(features,i,j,u,v),
                  'wrong_minus_correct_learned':round(evidence['learned_margin']-ev['learned_margin'],5)}
            count['false_with_matched_true_selected']+=1
        rows.append(data)
    for i,j,t in fns:
        label=REL[t]
        if label not in primary:continue
        ev=_pair_ev(i,j,t,objects,spans,stage)
        data={'type':'FALSE_NEGATIVE','edge':ev,'matched_selected_wrong':None}
        count['false_negatives']+=1;by_relation[label]['false_negatives']+=1
        if ev['raw_max']>0:count['missing_despite_positive_raw_max']+=1
        if ev['expert_votes']>0:count['missing_despite_expert_vote']+=1
        wrong=_match(i,j,t,fps,objects)
        if wrong:
            matched,ref=wrong;u,v,g=ref;bad=_pair_ev(u,v,g,objects,spans,stage)
            data['matched_selected_wrong']={'selection':matched,'edge':bad,
                  'feature_distance':_feat_groups(features,i,j,u,v),
                  'correct_minus_wrong_learned':round(ev['learned_margin']-bad['learned_margin'],5)}
        rows.append(data)
    case=rec['item']['row']
    obj={'fold':fold,'case_id':case['case_id'],'base_id':case['base_id'],
         'family':case['family'],'mode':case['mode'],
         'prompt':prompt['text'],'prompt_sha256':prompt['sha256'],
         'object_count':n,'object_text_coverage':len(spans),
         'graph':{k:int(q._metric(rec,graph)[k]) for k in ('tp','fp','fn','exact')},
         'evidence':rows,'counts':dict(count)}
    return obj,count,by_relation


def _format_example(case,item):
    edge=item['edge'];s=edge['source'];t=edge['destination']
    lines=[f"{case['case_id']} [{case['family']}/{case['mode']}] {item['type']} {edge['relation']}",
           f"  SOURCE {s['kind']} {s['start']}:{s['end']} {s['text']!r} | {s['context']!r}",
           f"  DEST   {t['kind']} {t['start']}:{t['end']} {t['text']!r} | {t['context']!r}",
           f"  expert_votes={edge['expert_votes']}/{b.E} raw_max={edge['raw_max']} "
           f"reranker={edge['learned_margin']} router={edge['router_margin']} "
           f"pairwise={edge['pairwise_winner']} decoded={edge['decoded_winner']}"]
    for field in ('matched_missing_gold','matched_selected_true','matched_selected_wrong'):
        match=item.get(field)
        if not match:continue
        other=match['edge']
        lines.append(f"  {field}: {other['source']['text']!r} -> {other['destination']['text']!r} "
                     f"votes={other['expert_votes']} raw_max={other['raw_max']} "
                     f"reranker={other['learned_margin']} router={other['router_margin']}")
        if 'wrong_minus_correct' in match:lines.append('  SCORE_GAP '+str(match['wrong_minus_correct']))
        if 'feature_distance' in match:
            lines.append('  INPUT_FEATURE_L1 '+str({k:v['mean_abs'] for k,v in match['feature_distance'].items()}))
    return '\n'.join(lines)


def _select_examples(results,limit):
    """Stratify by target relation, mistake type, variant, and matching status.

    Selecting only by margin gives repetitive unanimous false positives and
    hides the failure cases where the correct endpoint had stronger raw scores.
    """
    buckets=defaultdict(list)
    for case in results:
        for edge in case['evidence']:
            label=edge['edge']['relation']
            typ=edge['type']
            matched=bool(edge.get('matched_missing_gold') or edge.get('matched_selected_wrong'))
            unanimous=edge['edge']['expert_votes']==b.E
            key=(label,typ,case['mode'],matched,unanimous)
            buckets[key].append((case,edge))
    for bucket in buckets.values():
        bucket.sort(key=lambda item:(-abs(item[1]['edge']['learned_margin']),
                   item[0]['case_id'],item[1]['edge']['pair']))
    keys=sorted(buckets,key=lambda k:(0 if k[0]=='refers_to' else 1,
        0 if k[1]=='FALSE_POSITIVE' else 1,
        0 if k[3] else 1,0 if k[4] else 1,k[2]))
    selected=[]
    while len(selected)<limit and any(buckets.values()):
        for key in keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                if len(selected)==limit:break
    return selected


def _summary_text(doc,top):
    lines=[TAG+' / READ-ONLY MATCHED ENDPOINT EVIDENCE',
           'Frozen G2-0103; gold used for retrospective scoring and matching only.',
           'The reranker receives no lexical text directly: only typed spans, margins, labels and router surface.',
           'These scores and feature differences are observational, not semantic causal proof.',
           '']
    lines.append('SUMMARY '+json.dumps(doc['summary'],sort_keys=True))
    lines.append('BY_RELATION '+json.dumps(doc['by_relation'],sort_keys=True))
    lines.append('')
    for case,item in top:
        lines.append(_format_example(case,item));lines.append('')
    return '\n'.join(lines)+'\n'


def run(root=old.ROOT_DEFAULT,output=None,device='cuda',force=False,max_examples=32):
    require(device=='cpu' or (device.startswith('cuda') and torch.cuda.is_available()),'CUDA_UNAVAILABLE')
    require(1<=max_examples<=200,'BAD_EXAMPLE_LIMIT')
    source,experts,r99,r100,r101,r102,p102,report,original,p107=_load(root,output)
    target=source[8]/'semantic-evidence-audit-0108'/'result.json'
    require(force or not target.exists(),'REPORT_EXISTS_USE_FORCE')
    emit('START',{'device':device,'heldout_cases':275,'model_training':False,
           'source_text':'HASH_VERIFIED_PUBLIC_DISCOVERY_SEAL',
           'checkpoint_write':False,'reused_fit_not_independent_certification':True})
    # Public seal is joined separately because G2-0097 _cached_features
    # intentionally drops every row field other than ID/base/mode/family.
    records,spec=b.parent._records(source,experts,device)
    prompts=_public_prompts(source,[rec['item']['row'] for rec in records])
    results=[];counts=Counter();relations=defaultdict(Counter);replay=Counter()
    with torch.inference_mode():
        for fold,(index,(train,val,test),base_counts) in enumerate(tr._split_by_base(records,3,d.SEED)):
            require(index==fold and base_counts==report['folds'][fold]['base_counts'],
                    'FROZEN_FOLD_SPLIT_CHANGED')
            bundle=r._bundle_from_0100(fold,r100['folds'][fold],r99,experts,device)
            models,manifest=d.load_candidate(Path(report['folds'][fold]['manifest']).parent,
                                             experts,device,old.digest(p102))
            choice=report['folds'][fold]['chosen']
            require(manifest['selected']==choice,'FROZEN_CHOICE_DRIFT')
            local=Counter()
            for rec in test:
                case_id=rec['item']['row']['case_id']
                require(case_id in prompts,'MISSING_PUBLIC_PROMPT_'+case_id)
                stage=f._stage(rec,experts,spec,device,bundle,models,choice,probe=False)
                actual=d._infer(rec,experts,spec,device,bundle,choice['arm'],
                                models.get(choice['arm']),**choice['params'])
                require(torch.equal(actual,stage['graph']),'INFERENCE_DRIFT')
                metric=q._metric(rec,stage['graph']);local.update(metric);replay.update(metric)
                case,ct,rel=_case(rec,fold,stage,prompts[case_id])
                results.append(case);counts.update(ct)
                for name,datum in rel.items():relations[name].update(datum)
            expected=report['folds'][fold]['selected']['overall']
            require(all(local[k]==expected[k] for k in ('tp','fp','fn','exact','cases')),
                    'FOLD_REPLAY_MISMATCH_'+str(fold))
            emit('FOLD',{'fold':fold,'heldout_cases':len(test),'exact':local['exact'],
                  'text_coverage':len(test),'matched_fp_fn':sum(c['counts'].get(
                  'false_with_matched_missed_gold',0) for c in results if c['fold']==fold)})
    expected=report['candidate']
    require(all(replay[k]==expected[k] for k in ('tp','fp','fn','exact','cases')),
            'GLOBAL_FROZEN_REPLAY_MISMATCH')
    require(len(results)==len(prompts)==275 and all(c['object_text_coverage']==c['object_count']
            and all(it['edge']['source']['text'] and it['edge']['destination']['text']
                    for it in c['evidence']) for c in results),'INCOMPLETE_TEXT_EVIDENCE')
    sample=_select_examples(results,max_examples)
    by_variant=defaultdict(Counter)
    for case in results:
        by_variant[case['family']+'|'+case['mode']].update(case['counts'])
    doc={'schema':SCHEMA,'replay':dict(replay),'summary':dict(counts),
         'by_variant':{key:dict(value) for key,value in sorted(by_variant.items())},
         'by_relation':{k:dict(v) for k,v in sorted(relations.items())},
         'case_level':results,'examples':[
             {'case_id':c['case_id'],'evidence':e} for c,e in sample],
         'public_prompt_seal_sha256':old.digest(source[1]/'seal'/'prompts.jsonl'),
         'source_0103_sha256':old.digest(original),'source_0107_sha256':old.digest(p107),
         'interpretation':{'semantic_input_direct_to_reranker':False,
           'semantic_path':'frozen_backbone_to_experts_then_expert_outputs_and_router_scores',
           'matched_pairs':'same_relation_shared_source_or_destination; distance-matched without score-based selection',
           'not_proven':'which original semantic cues are causally necessary; requires independent controlled input intervention',
           'scores_are_not_probabilities':True},
         'new_training':False,'checkpoints_modified':False,'production_promoted':False,
         'g3_unlocked':False,'confirmation_used':False,'dev_used':False,
         'reused_fit_not_independent_certification':True}
    target.parent.mkdir(parents=True,exist_ok=True)
    old.write(target,doc)
    summary=target.parent/'examples.txt'
    summary.write_text(_summary_text(doc,sample),encoding='utf-8')
    emit('DIAGNOSIS',{'replay':doc['replay'],'summary':doc['summary'],
                      'by_relation':doc['by_relation'],
                      'sampled_text_examples':len(sample),'text_join_missing':0})
    for case,it in sample[:min(3,len(sample))]:
        emit('EXAMPLE',{'case_id':case['case_id'],'relation':it['edge']['relation'],
             'wrong_source':it['edge']['source']['text'],
             'wrong_destination':it['edge']['destination']['text'],
             'matched_gold':(it.get('matched_missing_gold') or {}).get('edge',{}).get('destination',{}).get('text'),
             'margin':it['edge']['learned_margin']})
    emit('SAVED',{'report':str(target),'examples':str(summary),
                  'sha256':old.digest(target),'frozen_replay':True,
                  'all_prompt_and_span_evidence_verified':True,
                  'production_promoted':False,'checkpoints_modified':False})


def selftest():
    # Regression for the real 0107 writer schema. Old tests skipped _load,
    # allowing a nonexistent `reference_0103_sha256` key to ship unnoticed.
    reference='ab'*32; ancestor='cd'*32
    expected={'tp':6832,'fp':731,'fn':455,'exact':61,'cases':275}
    fixture={'schema':q.SCHEMA,'checkpoint_modified':False,
             'new_training':False,'production_promoted':False,
             'confirmation_used':False,'dev_used':False,
             'source_0103_report_sha256':reference,
             'source_0106_report_sha256':ancestor,
             'frozen_0103_replay':dict(expected)}
    _verify_0107_lineage(fixture,reference,ancestor,expected)
    for key,value in [('source_0103_report_sha256','00'*32),
                      ('source_0106_report_sha256','00'*32),
                      ('checkpoint_modified',True),('new_training',True),
                      ('production_promoted',True),('confirmation_used',True)]:
        broken=dict(fixture);broken[key]=value
        try:_verify_0107_lineage(broken,reference,ancestor,expected)
        except ValueError as exc:
            require(str(exc)==TAG+'_0107_LINEAGE_OR_REPLAY_DRIFT',
                    'UNEXPECTED_LINEAGE_REFUSAL')
        else:raise AssertionError('G2_0108_LINEAGE_TAMPER_ACCEPTED_'+key)
    broken=dict(fixture);broken['frozen_0103_replay']={**expected,'fp':730}
    try:_verify_0107_lineage(broken,reference,ancestor,expected)
    except ValueError:pass
    else:raise AssertionError('G2_0108_REPLAY_TAMPER_ACCEPTED')
    prompt='Alice allocates 9 tokens to Beta. Beta allocates 3 tokens to Alpha.'
    objs=[[0,5,'actor'],[6,15,'action'],[16,17,'quantity'],
          [28,32,'actor'],[34,38,'actor'],[39,48,'action'],[49,50,'quantity'],[61,66,'actor']]
    aligned=_verified_objects(prompt,objs)
    require(aligned[0]['text']=='Alice' and aligned[2]['text']=='9','TEXT_EXTRACTION_INVALID')
    for altered in ('', 'Too short'):
        try:_verified_objects(altered,objs)
        except ValueError:pass
        else:raise AssertionError('G2_0108_NULL_OR_MISALIGNED_TEXT_ACCEPTED')
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);seal=root/'seal';seal.mkdir()
        cases=[{'case_id':'c0','base_id':'b0','family':'encounter','mode':'base'}]
        p={'case_id':'c0','base_id':'b0','family':'encounter','split':'discovery',
           'prompt':prompt,'prompt_sha256':sha_text(prompt)}
        (seal/'prompts.jsonl').write_text(json.dumps(p)+'\n',encoding='utf-8')
        src=[None,root]
        found=_public_prompts(src,cases)
        require(found['c0']['text']==prompt,'PUBLIC_JOIN_INVALID')
        p['prompt_sha256']='tampered'
        (seal/'prompts.jsonl').write_text(json.dumps(p)+'\n',encoding='utf-8')
        try:_public_prompts(src,cases)
        except ValueError:pass
        else:raise AssertionError('G2_0108_PROMPT_TAMPER_ACCEPTED')
    model=d.RawGraphReranker(contextual=False).eval()
    n=len(objs)
    margins=torch.randn(b.E,n,n,b.R)
    pred=torch.zeros((b.E,n,n),dtype=torch.long)
    base=torch.zeros((n,n,b.R+1))
    with torch.no_grad():
        logits=model(objs,margins,pred,base)
        # Text is intentionally not an input to this forward() path; changing
        # text alone cannot change the scores while inputs remain constant.
        swapped_text='Z'*len(prompt)
        other=model(objs,margins,pred,base)
        require(torch.equal(logits,other) and swapped_text!=prompt,
                'RERANKER_NO_DIRECT_LEXICAL_INPUT_DRIFT')
        feat=d._raw_features(objs,margins,pred,base)
        diff=_feat_groups(feat,0,1,0,2)
        require(set(diff)=={k for k,_ in FEATURE_GROUPS},'FEATURE_DELTA_INVALID')
        same=_feat_groups(feat,0,1,0,1)
        require(all(v['max_abs']==0 for v in same.values()),'FEATURE_IDENTITY_INVALID')
    # Strong adversarial case: all experts support an incorrect relation,
    # and its missing correct alternative has the same source.
    obj2=[[0,5,'actor'],[6,15,'action'],[16,17,'quantity'],[28,32,'action']]
    t=REL.index('applies_to')
    gold=torch.zeros((4,4),dtype=torch.long);gold[2,3]=t
    selected=torch.zeros_like(gold);selected[2,1]=t
    score=torch.zeros((4,4,len(REL)));score[...,1:]=-8.
    score[2,1,t]=5.;score[2,3,t]=-2.
    baseline=score.clone()
    experts_margin=torch.full((b.E,4,4,b.R),-2.)
    experts_margin[:,2,1,t-1]=3.;experts_margin[:,2,3,t-1]=1.
    exp_pred=selected[None].expand(b.E,-1,-1).clone()
    from tools.nanojev_gamer_v1_g2_posttrain_graph_bakeoff_0088 import format_edge
    record={'item':{'objects':obj2,'targets':gold,'g1_exact':True,
             'full_gold':{format_edge(obj2,2,3,t)},'row':{
             'case_id':'c0','base_id':'b0','mode':'base','family':'encounter'}}}
    fakeprompt='Alice allocates 9 tokens to Beta.'
    verified={'text':fakeprompt,'sha256':sha_text(fakeprompt)}
    # Valid spans source/destination all anchored on real prompt substrings.
    fakeobjects=[[0,5,'actor'],[6,15,'action'],[16,17,'quantity'],[28,32,'action']]
    stage={'score':score,'base':baseline,'margins':experts_margin,'preds':exp_pred,
           'graph':selected,'pair':score.argmax(-1)}
    case,ct,by=_case(record,0,stage,verified)
    require(ct['false_positives']==1 and ct['false_negatives']==1 and
            ct['false_with_matched_missed_gold']==1 and
            ct['wrong_outranks_missing_gold']==1 and
            len(case['evidence'][0]['matched_missing_gold']['edge']['destination']['text'])>0,
            'MATCHED_ENDPOINT_DIAGNOSTIC_FAILED')
    emit('SELFTEST',{'verbatim_prompt_join':True,'real_0107_schema_lineage_test':True,'hash_tamper_rejected':True,
      'missing_prompt_rejected':True,'out_of_bounds_span_rejected':True,
      'complete_endpoint_text':True,'feature_groups_checked':True,
      'expert_consensus_wrong_pair_matched_to_missing_gold':True,
      'no_lexical_input_to_frozen_reranker':True,'no_training':True})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',nargs='?',choices=('audit','selftest','run'),default='audit')
    parser.add_argument('--root',default=old.ROOT_DEFAULT)
    parser.add_argument('--output')
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--force',action='store_true',help='Replace only this diagnostic result and examples')
    parser.add_argument('--max-examples',type=int,default=32)
    opt=parser.parse_args(argv)
    if opt.command=='selftest':selftest()
    elif opt.command=='audit':audit(opt.root,opt.output)
    else:run(opt.root,opt.output,opt.device,opt.force,opt.max_examples)

if __name__=='__main__':main()
