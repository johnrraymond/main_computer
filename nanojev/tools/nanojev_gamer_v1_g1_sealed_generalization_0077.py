"""G1-0077: immutable, three-stage, prospective frozen-interface evaluation.

Protocol (no further fitting or model selection):
  prepare: generate new TEST-vocabulary cases; commit a hash of answers and
           expose separately a prompts-only file for inference.
  predict: verify frozen G1-0076 report, predecessor hashes, models and source;
           read ONLY prompts.jsonl, not the answer file; write immutable outputs.
  score:   verify committed answers and locked predictions, score once, and
           mark the seal consumed. Never use this cohort for selecting a decoder.

Freshness means disjoint *prompts* drawn prospectively from the same graph
corpus generator (in-distribution, not a cross-generator or real-world test).
No certification, production promotion, or G2 advancement is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import random
from collections import Counter

import torch

from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as previous
from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
from tools import nanojev_gamer_v1_g1_structural_learning_bakeoff_0074 as structural
from tools import nanojev_gamer_v1_g1_learned_interface_bakeoff_0073 as learned
from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import sha256, _checked_preflight, _summarize
from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS

SCHEMA='nanojev.gamer.graph.g1.sealed-generalization-0077.v1'
PROTOCOL='PROMPTS_ONLY_PREDICT_THEN_ONE_TIME_ANSWER_UNSEAL'
REQUIRED_MODEL_NAMES=('cutover','legacy')
MIN_EVALUATION_CASES=80
DEFAULT_SEED=760077
DEFAULT_CASES=160
CONTROL=previous.CONTROL
SELECTED='staged_joint_64'


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,
                       separators=(',',':'))+'\n').encode('utf8')


def _dict_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False,
                             separators=(',',':')).encode('utf8')).hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def _rows(path):
    content=Path(path).read_text(encoding='utf8')
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def _write_rows(path, rows):
    with Path(path).open('xb') as f:
        for row in rows: f.write(_json_bytes(row))


def _validate_report(record):
    if (record.get('schema')!=previous.SCHEMA or
            record.get('status')!='EXPLORATORY_TRAINABLE_NOT_CERTIFIED' or
            record.get('selected')!={name:SELECTED for name in REQUIRED_MODEL_NAMES} or
            any(record.get(key) is not False for key in (
                'g1_head_weights_modified','production_promotion','g2_unlocked',
                'sealed_test_used','certification_authorized')) or
            record.get('interface_training_performed') is not True):
        raise ValueError('G1_0077_FROZEN_REPORT_NOT_ELIGIBLE')
    source=record.get('lineage',{})
    if (set(source.get('checkpoint_sha256',{}))!=set(REQUIRED_MODEL_NAMES) or
            any(not isinstance(source.get(k),str) or len(source[k])!=64 for k in (
                'dataset_sha256','coverage_reference_sha256','learned_reference_sha256',
                'structural_reference_sha256','source_adapter_sha256')) or
            set(source.get('model_specific_0071_source',{}))!=set(REQUIRED_MODEL_NAMES)):
        raise ValueError('G1_0077_MISSING_LINEAGE')
    for name in REQUIRED_MODEL_NAMES:
        part=record.get('learned_interface_parameters',{}).get(name,{}).get('staged_joint')
        if (not isinstance(part,dict) or not isinstance(part.get('parameters'),dict)
                or _dict_hash(part['parameters'])!=part.get('sha256')):
            raise ValueError('G1_0077_COEFFICIENT_SHA_DRIFT:'+name)
        _load_coefficients(record,name)  # validate dimensions and finite numeric values
    return source


def _load_coefficients(report,name):
    bundle=report['learned_interface_parameters'][name]['staged_joint']
    parameters=bundle['parameters']
    model=previous.Discriminator()
    expected=model.state_dict()
    if set(parameters)!=set(expected):
        raise ValueError('G1_0077_COEFFICIENT_KEYS:'+name)
    state={}
    for key,baseline in expected.items():
        values=torch.tensor(parameters[key],dtype=baseline.dtype)
        if values.shape!=baseline.shape or not bool(torch.isfinite(values).all()):
            raise ValueError('G1_0077_COEFFICIENT_SHAPE_OR_FINITE:'+name+':'+key)
        if bool(torch.any(torch.abs(values)>8.000001)):
            raise ValueError('G1_0077_COEFFICIENT_OUT_OF_BOUNDS:'+name+':'+key)
        state[key]=values
    model.load_state_dict(state,strict=True)
    return model.eval().requires_grad_(False)


def _validate_prompt_rows(records, manifest):
    if len(records)!=manifest['cases'] or len(records)<MIN_EVALUATION_CASES:
        raise ValueError('G1_0077_CASE_COUNT')
    ids=set();hashes=set();families=set()
    for index,r in enumerate(records):
        if set(r)!={'case_id','family','prompt','prompt_sha256'}:
            raise ValueError('G1_0077_PROMPTS_ONLY_CONTRACT')
        if (r['case_id']!=f'sealed-{index:05d}' or
                r['family'] not in ('encounter','allocation') or
                not isinstance(r['prompt'],str) or not r['prompt'].strip() or
                hashlib.sha256(r['prompt'].encode('utf8')).hexdigest()!=r['prompt_sha256']):
            raise ValueError('G1_0077_INVALID_PROMPT_RECORD')
        if r['case_id'] in ids or r['prompt_sha256'] in hashes:
            raise ValueError('G1_0077_DUPLICATE_PROMPT')
        ids.add(r['case_id']);hashes.add(r['prompt_sha256']);families.add(r['family'])
    if families!={'encounter','allocation'}:
        raise ValueError('G1_0077_MISSING_FAMILY')
    return hashes


def _load_seal(seal):
    seal=Path(seal).resolve()
    record=_read_json(seal/'manifest.json')
    if (record.get('schema')!=SCHEMA or record.get('protocol')!=PROTOCOL or
            record.get('state')!='FROZEN_PRECOMMITTED' or
            record.get('generator_split')!='test' or
            record.get('evaluation_is_certification') is not False):
        raise ValueError('G1_0077_INVALID_SEAL')
    if record.get('sealed_runner_sha256')!=sha256(Path(__file__)):
        raise ValueError('G1_0077_RUNNER_CODE_DRIFT')
    if sha256(seal/'prompts.jsonl')!=record['prompts_sha256']:
        raise ValueError('G1_0077_PROMPT_HASH_DRIFT')
    prompts=_rows(seal/'prompts.jsonl')
    _validate_prompt_rows(prompts,record)
    return seal,record,prompts


def prepare(*,dataset,discrimination_reference,output,seed=DEFAULT_SEED,cases=DEFAULT_CASES):
    """Create prospective sealed data; answer file is never used by predict."""
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    target=Path(output).resolve();reference=Path(discrimination_reference).resolve()
    if target.exists(): raise FileExistsError('G1_0077_IMMUTABLE_SEAL')
    if (target==reference or reference in target.parents or
            cases<MIN_EVALUATION_CASES or cases%2 or seed<0):
        raise ValueError('G1_0077_BAD_PREPARE_CONFIG')
    run_report=reference/'report.json'
    frozen=_read_json(run_report)
    lineage=_validate_report(frozen)
    if sha256(dataset)!=lineage['dataset_sha256']:
        raise ValueError('G1_0077_TRAINING_DATASET_DRIFT')
    old=cur.load_rows(dataset)
    old_prompts={hashlib.sha256(r['prompt'].encode('utf8')).hexdigest() for r in old}
    rng=random.Random(seed)
    prompts=[];answers=[];seen=set()
    for index in range(cases):
        factory=corpus.encounter if index%2==0 else corpus.allocation
        for attempt in range(150):
            candidate=factory(rng,'test',index)
            digest=hashlib.sha256(candidate['prompt'].encode('utf8')).hexdigest()
            if digest not in seen and digest not in old_prompts:
                break
        else:
            raise ValueError('G1_0077_CANNOT_FIND_FRESH_PROMPT')
        seen.add(digest)
        ident=f'sealed-{index:05d}'
        prompts.append({'case_id':ident,'family':candidate['family'],
                        'prompt_sha256':digest,'prompt':candidate['prompt']})
        spans=candidate['object_spans']
        answers.append({'case_id':ident,'prompt_sha256':digest,'family':candidate['family'],
                        'gold':sorted([[s['start'],s['end'],s['kind']] for s in spans])})
    target.mkdir(parents=True,exist_ok=False)
    _write_rows(target/'prompts.jsonl',prompts)
    _write_rows(target/'answers.SECRET.jsonl',answers)
    manifest={'schema':SCHEMA,'protocol':PROTOCOL,'state':'FROZEN_PRECOMMITTED',
        'seed':seed,'cases':cases,'generator_split':'test',
        'generator_sha256':sha256(Path(corpus.__file__)),
        'sealed_runner_sha256':sha256(Path(__file__)),
        'original_dataset_sha256':lineage['dataset_sha256'],
        'frozen_report_sha256':sha256(run_report),
        'selected':frozen['selected'],
        'checkpoint_sha256':lineage['checkpoint_sha256'],
        'source_adapter_sha256':lineage['source_adapter_sha256'],
        'prompts_sha256':sha256(target/'prompts.jsonl'),
        'committed_answer_sha256':sha256(target/'answers.SECRET.jsonl'),
        'old_prompt_overlap':0,'oracle_provenance':'AUTHOR_MARKED_IN_SOURCE_GENERATOR',
        'evaluation_is_certification':False,
        'research_limitation':'same known author generator; independent prompts, not independent distribution',
        'no_post_selection_fitting':True,
        'score_once':True}
    (target/'manifest.json').write_bytes(_json_bytes(manifest))
    print('G1_SEALED_PREPARED '+json.dumps({'cases':cases,
        'prompts_sha256':manifest['prompts_sha256'],
        'committed_answer_sha256':manifest['committed_answer_sha256'],
        'frozen_report_sha256':manifest['frozen_report_sha256'],
        'status':'COMMITTED_NOT_SCORED'},sort_keys=True),flush=True)
    return manifest


def _verify_frozen_lineage(*,record,seal,dataset,legacy,cutover,
                           structural_reference,learned_reference,coverage_reference,
                           discrimination_reference,output):
    from tools import nanojev_gamer_v1_backbone_first as backbone
    original_report=Path(discrimination_reference).resolve()/'report.json'
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    if sha256(Path(corpus.__file__))!=seal['generator_sha256']:
        raise ValueError('G1_0077_GENERATOR_CODE_DRIFT')
    if sha256(original_report)!=seal['frozen_report_sha256']:
        raise ValueError('G1_0077_FROZEN_REPORT_CHANGED')
    lineage=_validate_report(record)
    if record['selected']!=seal['selected']:
        raise ValueError('G1_0077_SELECTION_CHANGED')
    if (sha256(dataset)!=seal['original_dataset_sha256'] or
            sha256(dataset)!=lineage['dataset_sha256']):
        raise ValueError('G1_0077_ORIGINAL_DATASET_DRIFT')
    for location,ref_field in ((structural_reference,'structural_reference_sha256'),
                               (learned_reference,'learned_reference_sha256'),
                               (coverage_reference,'coverage_reference_sha256')):
        if sha256(Path(location)/'report.json')!=lineage[ref_field]:
            raise ValueError('G1_0077_PREDECESSOR_DRIFT:'+ref_field)
    entries, ds_hash, cohorts=_checked_preflight(dataset,legacy,cutover,output)
    if {n:entries[n]['manifest']['head_sha256'] for n in entries}!=seal['checkpoint_sha256']:
        raise ValueError('G1_0077_HEAD_CHANGED')
    manifest=entries['legacy']['manifest']
    if (manifest['source_adapter_sha256']!=seal['source_adapter_sha256'] or
            backbone.adapter_fingerprint(manifest['source_adapter'])!=seal['source_adapter_sha256']):
        raise ValueError('G1_0077_SOURCE_CHANGED')
    partitions=learned._partition(cohorts['dev'])
    previous_structural,_,weights=coverage._require_previous(
        Path(structural_reference).resolve(),entries,ds_hash,partitions,len(cohorts['challenge']))
    priors,_=coverage._require_lattice_config(learned_reference,previous_structural)
    if priors!=lineage['model_specific_0071_source']:
        raise ValueError('G1_0077_PRIOR_CHANGED')
    return entries,weights,priors


def predict(*,seal,dataset,legacy,cutover,structural_reference,learned_reference,
            coverage_reference,discrimination_reference,output,device='cuda',
            max_length=2048):
    """Strictly prompts-only. No answer-file path is opened in this function."""
    from tools import nanojev_gamer_v1_backbone_first as backbone
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    target=Path(output).resolve()
    seal_dir,manifest,prompts=_load_seal(seal)
    if (target.exists() or target==seal_dir or seal_dir in target.parents or
            (seal_dir/'UNSEALED.json').exists() or max_length<=0):
        raise ValueError('G1_0077_PREDICTION_ALREADY_RUN_OR_UNSEALED')
    report=_read_json(Path(discrimination_reference)/'report.json')
    entries,weights,priors=_verify_frozen_lineage(
        record=report,seal=manifest,dataset=dataset,legacy=legacy,cutover=cutover,
        structural_reference=structural_reference,learned_reference=learned_reference,
        coverage_reference=coverage_reference,discrimination_reference=discrimination_reference,
        output=target)
    models={name:_load_coefficients(report,name) for name in REQUIRED_MODEL_NAMES}
    # Deliberately construct rows exclusively from the prompts file. No answer
    # file, graph, gold span, expected decision, or annotation ever enters capture.
    blind_rows=[{'prompt':p['prompt'],'family':p['family'],'object_spans':[]}
                for p in prompts]
    heads={name:cur._load_checkpoint(entries[name]['path'],device=device)[0].eval()
           for name in REQUIRED_MODEL_NAMES}
    base=entries['legacy']['manifest']
    qwen,tok=backbone.model_components(base['model_id'],base['source_adapter'],device)
    captured=coverage._capture(blind_rows,heads,qwen,tok,device=device,
                  max_length=max_length,prior_selected=priors,
                  reference_weights=weights,split='sealed',budget=coverage.MAX_OPTIONS)
    records=[]
    with torch.inference_mode():
        for name in REQUIRED_MODEL_NAMES:
            for index,data in enumerate(captured[name]):
                restricted=previous._restrict(data,64,'frozen')
                ctl=previous._predict(data,CONTROL,weights[name])
                sel=previous._predict(restricted,SELECTED,weights[name],models[name])
                records.append({'case_id':prompts[index]['case_id'],
                    'prompt_sha256':prompts[index]['prompt_sha256'],
                    'family':prompts[index]['family'],'model':name,
                    'control':[list(v) for v in sorted(ctl)],
                    'selected':[list(v) for v in sorted(sel)]})
    if (len(records)!=2*len(prompts) or
            {r['model'] for r in records}!=set(REQUIRED_MODEL_NAMES)):
        raise ValueError('G1_0077_PREDICTION_COUNT')
    # Recheck source assets after inference before emitting immutable predictions.
    if sha256(Path(discrimination_reference)/'report.json')!=manifest['frozen_report_sha256']:
        raise ValueError('G1_0077_REFERENCE_CHANGED_DURING_INFERENCE')
    if sha256(seal_dir/'prompts.jsonl')!=manifest['prompts_sha256']:
        raise ValueError('G1_0077_PROMPTS_CHANGED_DURING_INFERENCE')
    for name in REQUIRED_MODEL_NAMES:
        if sha256(Path(entries[name]['path'])/cur.HEAD_FILE)!=manifest['checkpoint_sha256'][name]:
            raise ValueError('G1_0077_HEAD_CHANGED_DURING_INFERENCE')
    target.mkdir(parents=True,exist_ok=False)
    _write_rows(target/'predictions.jsonl',records)
    pred_report={'schema':SCHEMA,'phase':'BLIND_PREDICTIONS_LOCKED',
        'protocol':PROTOCOL,'cases':len(prompts),
        'prompts_sha256':manifest['prompts_sha256'],
        'frozen_report_sha256':manifest['frozen_report_sha256'],
        'selected_by_model':{n:SELECTED for n in REQUIRED_MODEL_NAMES},
        'checkpoints_sha256':manifest['checkpoint_sha256'],
        'prediction_sha256':sha256(target/'predictions.jsonl'),
        'answers_opened_during_prediction':False,
        'gold_used_for_candidate_selection_or_inference':False,
        'training_performed':False,'promotion':False,'certification_authorized':False}
    (target/'manifest.json').write_bytes(_json_bytes(pred_report))
    print('G1_SEALED_PREDICTED '+json.dumps({'cases':len(prompts),
        'prediction_sha256':pred_report['prediction_sha256'],
        'status':'BLIND_PREDICTIONS_LOCKED'},sort_keys=True),flush=True)
    return pred_report


def _validate_gold(answers,prompts):
    if len(answers)!=len(prompts):raise ValueError('G1_0077_ANSWER_COUNT')
    output={}
    for answer,prompt in zip(answers,prompts):
        if set(answer)!={'case_id','prompt_sha256','family','gold'}:
            raise ValueError('G1_0077_BAD_ANSWER_SCHEMA')
        if any(answer[k]!=prompt[k] for k in ('case_id','prompt_sha256','family')):
            raise ValueError('G1_0077_ANSWER_BINDING')
        gold=set()
        for v in answer['gold']:
            if (not isinstance(v,list) or len(v)!=3 or
                    not isinstance(v[0],int) or not isinstance(v[1],int) or
                    not 0<=v[0]<v[1]<=len(prompt['prompt']) or
                    v[2] not in SPAN_KINDS):
                raise ValueError('G1_0077_BAD_GOLD_SPAN')
            gold.add(tuple(v))
        if len(gold)!=len(answer['gold']):raise ValueError('G1_0077_GOLD_DUPLICATE')
        output[answer['case_id']]=gold
    return output


def _wilson(k,n,z=1.959963984540054):
    if n<1:raise ValueError('G1_0077_EMPTY_COHORT')
    p=k/n;zz=z*z;den=1+zz/n
    center=(p+zz/(2*n))/den
    radius=z*math.sqrt(p*(1-p)/n+zz/(4*n*n))/den
    return {'lower':max(0,center-radius),'upper':min(1,center+radius)}


def _summarize_predictions(predictions,answer_map,model,column):
    paired=[];per_case=[]
    for r in predictions:
        if r['model']!=model:continue
        pred={tuple(x) for x in r[column]}
        gold=answer_map[r['case_id']]
        paired.append((pred,gold))
        per_case.append({'case_id':r['case_id'],'exact':pred==gold,
            'correct':len(pred&gold),'false':len(pred-gold),'missing':len(gold-pred)})
    m=_summarize(paired)
    correct=Counter(); predicted=Counter(); expected=Counter()
    for p,g in paired:
        correct.update(k for _,_,k in (p&g))
        predicted.update(k for _,_,k in p)
        expected.update(k for _,_,k in g)
    scores=[]
    for kind in SPAN_KINDS:
        if not expected[kind]: continue
        scores.append(2*correct[kind]/max(1,predicted[kind]+expected[kind]))
    m['object_macro_f1']=sum(scores)/max(len(scores),1)
    m['exact_rate_wilson_95pct']=_wilson(m['exact_prompts'],m['cases'])
    return m,per_case


def _contract_check(m):
    from tools.nanojev_gamer_v1_graph_curriculum import GATE_REQUIREMENTS
    req=GATE_REQUIREMENTS['G1']
    by_kind=m['by_kind']
    missing={kind:by_kind.get(kind,{}).get('recall',0.) for kind in
             ('actor','action','quantity')
             if by_kind.get(kind,{}).get('recall',0.) < req['kind_recall']}
    return {'exact_at_least_80pct':m['exact_prompts']/m['cases']>=req['dev'],
        'object_macro_f1_at_least_90pct':m['object_macro_f1']>=req['macro_f1'],
        'required_kind_recall_at_least_82pct':not missing,
        'kind_recall_deficits':missing,
        'research_only_not_stage_certification':True}


def score(*,seal,predictions,output):
    """Unseal once after frozen predictions exist; no fit and no selection."""
    target=Path(output).resolve()
    seal_dir,manifest,prompts=_load_seal(seal)
    pred_dir=Path(predictions).resolve()
    if (target.exists() or target==seal_dir or seal_dir in target.parents or
            target==pred_dir or pred_dir in target.parents or
            (seal_dir/'UNSEALED.json').exists()):
        raise ValueError('G1_0077_SCORE_IMMUTABLE_OR_ALREADY_UNSEALED')
    pre=_read_json(pred_dir/'manifest.json')
    if (pre.get('schema')!=SCHEMA or pre.get('phase')!='BLIND_PREDICTIONS_LOCKED' or
            pre.get('prediction_sha256')!=sha256(pred_dir/'predictions.jsonl') or
            pre.get('prompts_sha256')!=manifest['prompts_sha256'] or
            pre.get('frozen_report_sha256')!=manifest['frozen_report_sha256'] or
            pre.get('checkpoints_sha256')!=manifest['checkpoint_sha256'] or
            pre.get('answers_opened_during_prediction') is not False or
            pre.get('gold_used_for_candidate_selection_or_inference') is not False or
            pre.get('training_performed') is not False or
            pre.get('promotion') is not False or
            pre.get('certification_authorized') is not False or
            pre.get('selected_by_model')!={name:SELECTED for name in REQUIRED_MODEL_NAMES}):
        raise ValueError('G1_0077_INVALID_PREDICTION_COMMITMENT')
    predictions_rows=_rows(pred_dir/'predictions.jsonl')
    expected={(p['case_id'],name) for p in prompts for name in REQUIRED_MODEL_NAMES}
    found=set()
    hash_by_id={p['case_id']:p for p in prompts}
    for r in predictions_rows:
        if set(r)!={'case_id','prompt_sha256','family','model','control','selected'}:
            raise ValueError('G1_0077_BAD_PREDICTION_RECORD')
        key=(r['case_id'],r['model'])
        if key in found or key not in expected:raise ValueError('G1_0077_PREDICTION_DUPLICATE')
        p=hash_by_id[r['case_id']]
        if r['prompt_sha256']!=p['prompt_sha256'] or r['family']!=p['family']:
            raise ValueError('G1_0077_PREDICTION_PROMPT_MISMATCH')
        for col in ('control','selected'):
            v=r[col]
            if not isinstance(v,list) or any(not isinstance(x,list) or len(x)!=3 or
                        type(x[0]) is not int or type(x[1]) is not int or
                        type(x[2]) is not str or x[2] not in SPAN_KINDS or
                        not 0<=x[0]<x[1]<=len(p['prompt']) for x in v):
                raise ValueError('G1_0077_INVALID_PREDICTION_SPAN')
            if len({tuple(x) for x in v})!=len(v):
                raise ValueError('G1_0077_DUPLICATE_PREDICTION')
        found.add(key)
    if found!=expected:raise ValueError('G1_0077_PREDICTIONS_INCOMPLETE')
    # Only NOW is the committed answer file opened, after validating predictions.
    if sha256(seal_dir/'answers.SECRET.jsonl')!=manifest['committed_answer_sha256']:
        raise ValueError('G1_0077_ANSWER_COMMITMENT_BROKEN')
    answers=_validate_gold(_rows(seal_dir/'answers.SECRET.jsonl'),prompts)
    metrics={};cases=[]
    for name in REQUIRED_MODEL_NAMES:
        ctl,ctl_cases=_summarize_predictions(predictions_rows,answers,name,'control')
        sel,sel_cases=_summarize_predictions(predictions_rows,answers,name,'selected')
        metrics[name]={'control':ctl,'selected':sel,
            'exact_gain':sel['exact_prompts']-ctl['exact_prompts'],
            'f1_gain':sel['f1']-ctl['f1'],
            'reconstruction_contract_research_check':_contract_check(sel)}
        for a,b in zip(ctl_cases,sel_cases):
            if a['case_id']!=b['case_id']:raise ValueError('G1_0077_CASE_ORDER_DRIFT')
            cases.append({'model':name,'case_id':a['case_id'],
                'control':a,'selected':b})
    target.mkdir(parents=True,exist_ok=False)
    result={'schema':SCHEMA,'status':'SEALED_COHORT_SCORED_NO_PROMOTION',
        'protocol':PROTOCOL,'cases':len(prompts),
        'source_generator_scope':manifest['research_limitation'],
        'precommitted_answer_sha256':manifest['committed_answer_sha256'],
        'frozen_report_sha256':manifest['frozen_report_sha256'],
        'blind_prediction_sha256':pre['prediction_sha256'],
        'metrics':metrics,'trained_or_tuned_on_sealed_cohort':False,
        'oracle_used_during_inference':False,
        'stage_g1_certified':False,'g2_unlocked':False,'production_promotion':False,
        'warning':'Fresh prompts, same known generator; one prospective cohort is not full G1-G7 certification.'}
    (target/'report.json').write_bytes(_json_bytes(result))
    _write_rows(target/'cases.jsonl',cases)
    # Seal is consumed only after all reports have been committed successfully.
    with (seal_dir/'UNSEALED.json').open('xb') as f:
        f.write(_json_bytes({'phase':'SCORED_ONCE','report_sha256':sha256(target/'report.json'),
            'prediction_sha256':pre['prediction_sha256'],
            'answer_sha256':manifest['committed_answer_sha256']}))
    print('G1_SEALED_RESULT '+json.dumps({'status':result['status'],
        'cases':len(prompts),'metrics':{name:{
            'exact':v['selected']['exact_prompts'],
            'control_exact':v['control']['exact_prompts'],
            'f1':round(v['selected']['f1'],5)} for name,v in metrics.items()}},
            sort_keys=True),flush=True)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    pr=sub.add_parser('prepare',help='Make immutable prompts, gold commitment and seal')
    pr.add_argument('--dataset',required=True)
    pr.add_argument('--discrimination-reference',required=True)
    pr.add_argument('--output',required=True)
    pr.add_argument('--seed',type=int,default=DEFAULT_SEED)
    pr.add_argument('--cases',type=int,default=DEFAULT_CASES)
    pp=sub.add_parser('predict',help='Blind frozen-model inference from prompts only')
    for key in ('seal','dataset','legacy','cutover','structural-reference',
                'learned-reference','coverage-reference','discrimination-reference','output'):
        pp.add_argument('--'+key,required=True)
    pp.add_argument('--device',default='cuda')
    pp.add_argument('--max-length',type=int,default=2048)
    ps=sub.add_parser('score',help='Unseal only after predictions are locked')
    for key in ('seal','predictions','output'):
        ps.add_argument('--'+key,required=True)
    values=vars(parser.parse_args(argv))
    command=values.pop('command')
    return {'prepare':prepare,'predict':predict,'score':score}[command](**values)

if __name__=='__main__':main()
