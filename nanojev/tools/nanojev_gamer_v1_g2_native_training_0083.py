"""G2-0083: certified native predicted-G1-span training and blind G2 acceptance.

Command: python -m tools.nanojev_gamer_v1_g2_native_training_0083 run --device cuda

This is an *opt-in orchestration module*. It never patches the hash-locked G1-0082
consumer or the legacy G2 curriculum. Gold is used to construct TRAIN labels
and score AFTER blind inference; no gold G1 spans enter the G2 forward call.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

import torch

from tools import nanojev_gamer_v1_backbone_first as backbone
from tools import nanojev_gamer_v1_graph_curriculum as cur
from tools import nanojev_gamer_v1_graph_corpus as corpus
from tools import nanojev_gamer_v1_g1_native_certification_0082 as g1cert
from tools import nanojev_gamer_v1_g1_native_composite_0082 as g1native
from tools.nanojev_gamer_v1_graph_contract import BINDINGS
from tools.nanojev_gamer_v1_graph_head import decode_graph

SCHEMA = 'nanojev.gamer.graph.g2.native-predicted-training-0083.v1'
SEED = 830083
DEV = 96
CHALLENGE = 64
STEPS = 400
ACCUM = 4
LR = 1e-4


def require(condition, code):
    if not condition:
        raise ValueError('G2_0083_' + code)


def sha(path):
    return backbone.sha256(Path(path))


def js(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, obj):
    Path(path).write_text(cur.json_text(obj)+'\n',encoding='utf-8')


def load_jsonl(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]


def write_jsonl(path, rows):
    with Path(path).open('x',encoding='utf-8') as out:
        for row in rows:
            out.write(json.dumps(row,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n')


def defaults(root):
    r = Path(root)
    return {
        'dataset': r/'graph-span-curriculum-0005.jsonl',
        'adapter': r/'transition-005'/'lora'/'best_adapter',
        'g1': r/'oracle-fabric-cutover-0001'/'stages'/'G1',
        'composite': r/'oracle-fabric-g1-certification-completion-0081'/'checkpoint',
        'certificate': r/'oracle-fabric-g1-native-certification-0082'/'scored'/'certificate.json',
        'seal0082': r/'oracle-fabric-g1-native-certification-0082'/'seal'/'prompts.jsonl',
        'seal0077': r/'oracle-fabric-g1-sealed-0077',
        'seal0079': r/'oracle-fabric-g1-quantity-preserving-0079-seal',
    }


def preflight(paths):
    # This verifier reopens the native seal, checks parity and scores, and
    # verifies the reviewed source-file hashes. Merely reading a PASS flag
    # would be insufficient.
    _, parent = cur._load_checkpoint(paths['g1'],device='cpu')
    require(parent.get('stage') == 'G1' and parent.get('train_limit') in (0,None),
            'UNCERTIFIABLE_PARENT')
    fabric = parent.get('deterministic_fabric')
    require(isinstance(fabric,dict) and fabric.get('new_trainable_random_init') is False
            and fabric.get('cutover_id') == 'oracle-fabric-0001', 'DETERMINISTIC_FABRIC_DRIFT')
    proof = g1cert.verify_native_certificate(paths['certificate'], paths['composite'],
                                              expected_head_sha256=parent['head_sha256'])
    require(proof['g2_training_unlocked'] is True, 'G1_NOT_CERTIFIED')
    require(sha(paths['dataset']) == parent['dataset_sha256'], 'DATASET_LINEAGE_DRIFT')
    require(backbone.adapter_fingerprint(paths['adapter']) == parent['source_adapter_sha256'],
            'SOURCE_LINEAGE_DRIFT')
    require(parent['head']['object_extractor']=='joint-span-v1', 'WRONG_G1_EXTRACTOR')
    return parent, proof


def previously_seen(paths):
    # Reuse the *already certified* exclusion registry, then also exclude
    # the G1-0082 prospective cases. No previous acceptance labels are used.
    excluded = g1cert._exclude(None, paths['dataset'], paths['seal0077'], paths['seal0079'])
    audit_rows = load_jsonl(paths['seal0082'])
    require(len(audit_rows)==g1cert.DEV+g1cert.CHALLENGE, 'PREVIOUS_SEAL_MISSING')
    for r in audit_rows:
        require(hashlib.sha256(r['prompt'].encode()).hexdigest()==r['prompt_sha256'],
                'PREVIOUS_SEAL_PROMPT_DRIFT')
        excluded.add(r['prompt_sha256'])
    return excluded


def prospective_rows(paths, *, seed=SEED, dev=DEV, challenge=CHALLENGE):
    require(seed==SEED and (dev,challenge)==(DEV,CHALLENGE), 'PROSPECTIVE_DESIGN_LOCKED')
    exclude=previously_seen(paths)
    rng=random.Random(seed)
    rows=[]; serial=83_000_000
    for split,n in [('dev',dev),('challenge',challenge)]:
        for index in range(n):
            factory=corpus.encounter if index%2==0 else corpus.allocation
            for _ in range(4096):
                candidate=factory(rng,'test',serial)
                serial+=1
                digest=hashlib.sha256(candidate['prompt'].encode()).hexdigest()
                if digest not in exclude:break
            else: raise ValueError('G2_0083_UNIQUE_PROMPT_EXHAUSTED')
            exclude.add(digest)
            require(candidate['annotation_provenance']=='AUTHOR_MARKED_IN_SOURCE_GENERATOR'
                    and candidate['graph']['edges'], 'INVALID_AUTHOR_ORACLE')
            rows.append({'case_id':f'g2-0083-{split}-{index+1:04}',
                         'split':split,'family':candidate['family'],
                         'prompt':candidate['prompt'],'prompt_sha256':digest,
                         'graph':candidate['graph'],
                         'gold_objects':candidate['object_spans']})
    require(len(rows)==DEV+CHALLENGE, 'BAD_PROSPECTIVE_COHORT')
    return rows


def commit_seal(paths, output, parent):
    seal=output/'seal'
    rows=prospective_rows(paths)
    seal.mkdir(parents=True,exist_ok=False)
    write_jsonl(seal/'prompts.jsonl',[
        {k:r[k] for k in ('case_id','split','family','prompt','prompt_sha256')} for r in rows])
    write_jsonl(seal/'answers.SECRET.jsonl',[
        {k:r[k] for k in ('case_id','split','family','prompt_sha256','graph','gold_objects')}
        for r in rows])
    record={'schema':SCHEMA,'status':'COMMITTED_BEFORE_G2_TRAINING',
            'seed':SEED,'dev':DEV,'challenge':CHALLENGE,
            'prompts_sha256':sha(seal/'prompts.jsonl'),
            'answers_sha256':sha(seal/'answers.SECRET.jsonl'),
            'dataset_sha256':sha(paths['dataset']),
            'parent_head_sha256':parent['head_sha256'],
            'native_certificate_sha256':sha(paths['certificate']),
            'composite_interface_sha256':sha(paths['composite']/'composite-interface.json'),
            'production_promotion':False}
    write_json(seal/'commit.json',record)
    return record


def check_seal(paths, root, expected_head=None):
    seal=root/'seal'; record=js(seal/'commit.json')
    require(record.get('schema')==SCHEMA and record.get('status')=='COMMITTED_BEFORE_G2_TRAINING'
            and (record.get('dev'),record.get('challenge'),record.get('seed')) ==
                (DEV,CHALLENGE,SEED),'SEALED_DESIGN_DRIFT')
    require(sha(seal/'prompts.jsonl')==record['prompts_sha256'] and
            sha(seal/'answers.SECRET.jsonl')==record['answers_sha256'] and
            sha(paths['dataset'])==record['dataset_sha256'] and
            sha(paths['certificate'])==record['native_certificate_sha256'] and
            sha(paths['composite']/'composite-interface.json')==record['composite_interface_sha256'],
            'SEALED_EVIDENCE_DRIFT')
    if expected_head is not None:
        require(expected_head==record['parent_head_sha256'],'SEALED_PARENT_DRIFT')
    return record


def compare_frozen_stage(parent_path, trained_path):
    """Proof all parameters outside G2's declared training authority are frozen."""
    from safetensors import safe_open
    a=Path(parent_path)/cur.HEAD_FILE
    b=Path(trained_path)/cur.HEAD_FILE
    changed=[];unchanged=0
    with safe_open(str(a),framework='pt',device='cpu') as lhs,\
         safe_open(str(b),framework='pt',device='cpu') as rhs:
        left=set(lhs.keys());right=set(rhs.keys())
        require(left==right,'PARAMETER_SET_DRIFT')
        for key in sorted(left):
            if torch.equal(lhs.get_tensor(key),rhs.get_tensor(key)):
                unchanged+=1
            else:
                require(any(key.startswith(p) for p in cur.STAGE_MODULES['G2']),
                        'FROZEN_G1_OR_FUTURE_STAGE_MUTATED_'+key)
                changed.append(key)
    require(changed,'G2_PARAMETERS_UNCHANGED_NO_LEARNING')
    return {'changed_g2_parameters':changed, 'unchanged_parameters':unchanged,
            'parent_sha256':sha(a),'trained_sha256':sha(b),
            'non_g2_weights_mutated':False}


def _edge_sets(graph):
    return cur._normalized_edges(graph,BINDINGS)


def _safe_edges(items):
    def serialize(anchor): return [anchor[0],list(anchor[1])]
    return [[serialize(a),serialize(b),label] for a,b,label in sorted(items)]


def _check_prediction_row(record):
    require(isinstance(record,dict) and
            set(record)=={'case_id','split','family','prompt_sha256','modes','surface_count'}
            and record['split'] in ('dev','challenge') and
            record['family'] in ('encounter','allocation') and
            type(record['surface_count']) is int and 0<=record['surface_count']<=128 and
            set(record['modes'])=={'trained','untrained','zero_hidden','no_binding'},
            'PREDICTION_SCHEMA')
    for key,value in record['modes'].items():
        require(isinstance(value,dict) and set(value)=={'binding_edges','g1_objects'} and
                isinstance(value['binding_edges'],list) and isinstance(value['g1_objects'],list),
                'PREDICTION_MODE_'+key)


def _capture_inference(model,tok,prompt,device,max_length):
    prepared=backbone.prepare_prompt(tok,prompt,max_input_tokens=max_length)
    with torch.inference_mode():
        h=backbone.frozen_context(model,prepared,device=device)
    return prepared,h


def predict(*, root,device='cuda',max_length=2048):
    """Gold-blind subprocess: deliberately opens ONLY prompts and frozen models."""
    root=Path(root).resolve()
    paths=defaults(root.parent)
    proof=check_seal(paths,root)
    trained=root/'training'
    _,trained_manifest=cur._load_checkpoint(trained,device='cpu')
    require(trained_manifest['stage']=='G2' and
            trained_manifest.get('native_composite_g1',{}).get('mode')==
            'predicted_G1_spans_native_G2_binding','WRONG_G2_STAGE_MODE')
    require(trained_manifest['head_sha256']==sha(trained/cur.HEAD_FILE),'TRAINED_HEAD_HASH')
    _,g1_manifest=cur._load_checkpoint(paths['g1'],device='cpu')
    check_seal(paths,root,expected_head=g1_manifest['head_sha256'])
    require(trained_manifest['dataset_sha256']==proof['dataset_sha256'] and
            trained_manifest['source_adapter_sha256']==g1_manifest['source_adapter_sha256'],
            'TRAINED_G2_LINEAGE_DRIFT')
    require(sha(paths['certificate'])==trained_manifest['native_composite_g1']['certificate_sha256'],
            'G2_NATIVE_CERT_DRIFT')
    # Verify native G1 source while loading the immutable composite interface.
    composite=g1native.load_composite(paths['composite'],device=device)
    head,_=cur._load_checkpoint(trained,device=device)
    head.eval().requires_grad_(False)
    parent,_=cur._load_checkpoint(paths['g1'],device=device)
    parent.eval().requires_grad_(False)
    model,tok=backbone.model_components(trained_manifest['model_id'],
                                        trained_manifest['source_adapter'],device)
    require(not any(p.requires_grad for p in model.parameters()),'BACKBONE_NOT_FROZEN')
    prompts=load_jsonl(root/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE and
            len({r['case_id'] for r in prompts})==len(prompts), 'PROMPT_COUNT_OR_DUPLICATE')
    predicted=[]
    for i,row in enumerate(prompts,1):
        require(hashlib.sha256(row['prompt'].encode()).hexdigest()==row['prompt_sha256'],
                'PROMPT_COMMITMENT_DRIFT')
        prep,hidden=_capture_inference(model,tok,row['prompt'],device,max_length)
        eligible=prep['eligible'].to(device)
        g1=composite.predict(hidden,prep,family=row['family'],native_stage='G1')
        spans=g1['token_spans']
        def score(h, s, network, no_binding=False):
            with torch.inference_mode():
                out=network(h,eligible=eligible,active_stage='G2',inference_spans=s)
                if no_binding:
                    out=dict(out,binding_logits=torch.zeros_like(out['binding_logits']))
                graph,surfaces=decode_graph(out,prep,unlocked='G2')
            require(len(surfaces)==len(s),'NATIVE_G1_SPANS_NOT_CONSUMED')
            objects=sorted([list(o) for o in {
                (t['span'][0],t['span'][1],t['kind']) for t in graph['nodes'] if 'span' in t}])
            return {'binding_edges':_safe_edges(_edge_sets(graph)),'g1_objects':objects}
        modes={'trained':score(hidden,spans,head),
               'untrained':score(hidden,spans,parent),
               'no_binding':score(hidden,spans,head,True)}
        ablated=composite.predict(hidden,prep,family=row['family'],mode='zero_hidden',
                                  native_stage='G1')
        modes['zero_hidden']=score(torch.zeros_like(hidden),ablated['token_spans'],head)
        require(modes['trained']['g1_objects']==modes['untrained']['g1_objects']==
                modes['no_binding']['g1_objects'], 'G1_PREDICTIONS_DRIFT_BETWEEN_G2_ARMS')
        r={k:row[k] for k in ('case_id','split','family','prompt_sha256')}
        r.update(modes=modes,surface_count=len(spans))
        _check_prediction_row(r)
        predicted.append(r)
        if i==1 or i%10==0 or i==len(prompts):
            print(f'G2_0083_SEALED_PREDICT {i}/{len(prompts)}',flush=True)
    path=root/'blind';path.mkdir(parents=True,exist_ok=False)
    write_jsonl(path/'predictions.jsonl',predicted)
    lock={'schema':SCHEMA,'status':'BLIND_G2_NATIVE_PREDICTIONS_LOCKED',
          'cases':len(predicted),'predictions_sha256':sha(path/'predictions.jsonl'),
          'prompts_sha256':proof['prompts_sha256'],
          'g2_head_sha256':trained_manifest['head_sha256'],
          'g1_parent_sha256':proof['parent_head_sha256'],
          'gold_read_during_inference':False,'teacher_forced_g1_spans':False,
          'production_promotion':False}
    write_json(path/'lock.json',lock)
    return lock


def _keyed_edges(edges):
    require(isinstance(edges,list),'EDGE_FORMAT')
    result=set()
    for r in edges:
        require(isinstance(r,list) and len(r)==3 and r[2] in BINDINGS and
                all(isinstance(x,list) and len(x)==2 and x[0] in cur.SPAN_KINDS and
                    isinstance(x[1],list) and len(x[1])==2 and
                    all(type(v) is int for v in x[1]) for x in r[:2]),
                'EDGE_INVALID')
        result.add((r[0][0],tuple(r[0][1]),r[1][0],tuple(r[1][1]),r[2]))
    require(len(result)==len(edges),'DUPLICATE_PREDICTED_BINDING')
    return result


def _gold_edges(graph):
    return {(a[0],a[1],b[0],b[1],name) for a,b,name in _edge_sets(graph)}


def score(*,root):
    root=Path(root).resolve();paths=defaults(root.parent)
    proof=check_seal(paths,root)
    blind=root/'blind'
    lock=js(blind/'lock.json')
    require(lock.get('schema')==SCHEMA and lock.get('status')=='BLIND_G2_NATIVE_PREDICTIONS_LOCKED'
            and lock.get('cases')==DEV+CHALLENGE and
            lock.get('prompts_sha256')==proof['prompts_sha256'] and
            lock.get('gold_read_during_inference') is False and
            lock.get('teacher_forced_g1_spans') is False and
            sha(blind/'predictions.jsonl')==lock['predictions_sha256'] and
            sha(root/'training'/cur.HEAD_FILE)==lock['g2_head_sha256'],
            'BLIND_PREDICTION_COMMITMENT_DRIFT')
    _,trained_manifest=cur._load_checkpoint(root/'training',device='cpu')
    require(trained_manifest.get('stage')=='G2' and
            trained_manifest.get('native_composite_g1',{}).get('mode')==
            'predicted_G1_spans_native_G2_binding' and
            trained_manifest.get('head_sha256')==lock['g2_head_sha256'] and
            trained_manifest.get('dataset_sha256')==proof['dataset_sha256'],
            'SCORED_G2_CHECKPOINT_LINEAGE_DRIFT')
    gold=load_jsonl(root/'seal'/'answers.SECRET.jsonl')
    pred=load_jsonl(blind/'predictions.jsonl')
    require(len(pred)==len(gold)==DEV+CHALLENGE and
            len({r['case_id'] for r in pred})==len(pred) and
            {r['case_id'] for r in pred}=={r['case_id'] for r in gold},
            'GOLD_PREDICTION_BIJECTION')
    expectations={r['case_id']:r for r in gold}
    prompts=load_jsonl(root/'seal'/'prompts.jsonl')
    require(len(prompts)==DEV+CHALLENGE and
            len({r['case_id'] for r in prompts})==len(prompts),
            'SEALED_PROMPT_BIJECTION')
    prompt_by={r['case_id']:r for r in prompts}
    for item in gold:
        require(item['case_id'] in prompt_by and
                all(item[k]==prompt_by[item['case_id']][k]
                    for k in ('split','family','prompt_sha256')) and
                hashlib.sha256(prompt_by[item['case_id']]['prompt'].encode()).hexdigest()==
                    item['prompt_sha256'], 'SECRET_ANSWER_PROMPT_MISMATCH')
    outcome={};status=[];cases=[]
    for split in ('dev','challenge'):
        relevant=[p for p in pred if p['split']==split]
        n_expected=DEV if split=='dev' else CHALLENGE
        require(len(relevant)==n_expected,'SPLIT_SIZE')
        counters={mode:{'exact':0,'tp':0,'pred':0,'gold':0,'g1_exact':0}
                  for mode in ('trained','untrained','zero_hidden','no_binding')}
        by_family=Counter();fam_correct=Counter();paired_gains=paired_losses=0
        reachable_gold_edges=0; all_gold_edges=0; conditioned_exact=Counter();conditioned_total=Counter()
        for r in relevant:
            _check_prediction_row(r)
            answer=expectations[r['case_id']]
            require(answer['split']==split and answer['family']==r['family'] and
                    answer['prompt_sha256']==r['prompt_sha256'],'GOLD_CASE_BINDING')
            gs=_gold_edges(answer['graph'])
            go={(x['start'],x['end'],x['kind']) for x in answer['gold_objects']}
            targets={}
            for name,record in r['modes'].items():
                ps=_keyed_edges(record['binding_edges'])
                require(all((ak,at)!=(bk,bt) for ak,at,bk,bt,_ in ps), 'SELF_BINDING_PREDICTION')
                objects={tuple(item) for item in record['g1_objects']}
                require(len(objects)==len(record['g1_objects']),'DUPLICATE_G1_OBJECT')
                require(all((*at,ak) in objects and (*bt,bk) in objects for ak,at,bk,bt,_ in ps), 'BINDING_ANCHOR_NOT_G1_PREDICTION')
                if name=='trained': require(len(objects)==r['surface_count'],'MISSING_G1_SURFACE')
                info=counters[name]
                info['exact']+=int(ps==gs);info['tp']+=len(ps&gs)
                info['pred']+=len(ps);info['gold']+=len(gs)
                info['g1_exact']+=int(objects==go)
                targets[name]=ps==gs
            gold_reachable_objects={tuple(item) for item in r['modes']['trained']['g1_objects']}
            reachable_gold_edges+=sum((*at,ak) in gold_reachable_objects and
                                      (*bt,bk) in gold_reachable_objects
                                      for ak,at,bk,bt,_ in gs)
            all_gold_edges+=len(gs)
            group='g1_exact' if gold_reachable_objects==go else 'g1_inexact'
            conditioned_total[group]+=1
            conditioned_exact[group]+=int(targets['trained'])
            fam=r['family'];by_family[fam]+=1;fam_correct[fam]+=int(targets['trained'])
            paired_gains+=int(targets['trained'] and not targets['untrained'])
            paired_losses+=int(targets['untrained'] and not targets['trained'])
            cases.append({'case_id':r['case_id'],'split':split,'family':fam,
                          'exact':targets['trained'],'baseline_exact':targets['untrained'],
                          'g1_exact':(set(map(tuple,r['modes']['trained']['g1_objects']))==go),
                          'gold_binding_count':len(gs),
                          'predicted_binding_count':len(_keyed_edges(r['modes']['trained']['binding_edges']))})
        metrics={}
        for name,counts in counters.items():
            p=counts['tp']/max(counts['pred'],1)
            recall=counts['tp']/max(counts['gold'],1)
            metrics[name]={'exact':counts['exact'],'exact_rate':counts['exact']/n_expected,
                           'edge_precision':p,'edge_recall':recall,
                           'edge_f1':2*p*recall/max(p+recall,1e-12),
                           'g1_exact':counts['g1_exact']}
        gate=cur.GATE_REQUIREMENTS['G2']
        failures=[]
        if metrics['trained']['exact_rate']<gate[split]:failures.append('binding_exact_rate')
        for ablation in ('zero_hidden','no_binding'):
            gain=(metrics['trained']['exact']-metrics[ablation]['exact'])/n_expected
            if gain<gate['neural_gain']:failures.append('insufficient_'+ablation+'_causality')
        if len(by_family)<2:failures.append('family_diversity')
        if not all(r['gold_binding_count']>0 for r in cases if r['split']==split):
            failures.append('binding_free_examples')
        # Improvement over untrained native G2 is evidence of G2 *learning*,
        # not just G1 quality or a lucky default decoder.
        if metrics['trained']['exact']<=metrics['untrained']['exact']:
            failures.append('no_positive_net_gain_over_untrained_G2')
        outcome[split]={'cases':n_expected,'metrics':metrics,
                        'gold_edges_reachable_with_predicted_g1':reachable_gold_edges,
                        'gold_edges_unreachable_from_predicted_g1':all_gold_edges-reachable_gold_edges,
                        'binding_exact_conditioned_on_g1':{
                            k:{'cases':conditioned_total[k], 'correct':conditioned_exact[k]}
                            for k in ('g1_exact','g1_inexact')},
                        'by_family':{k:{'cases':v,'correct':fam_correct[k]} for k,v in by_family.items()},
                        'paired_gain':paired_gains,'paired_loss':paired_losses,
                        'failures':failures}
        status.extend(split+':'+x for x in failures)
    result={'schema':SCHEMA,'status':'PROSPECTIVE_G2_BINDING_GATE_PASSED' if not status else
             'PROSPECTIVE_G2_BINDING_GATE_BLOCKED',
            'splits':outcome,'failures':status,'g2_prospective_pass':not status,
            'g3_training_unlocked':False,'production_promotion':False,
            'claims':'Within-generator prospective G2 relationship generalization only; not G3 certification.'}
    out=root/'prospective';out.mkdir(parents=True,exist_ok=False)
    write_json(out/'report.json',result)
    write_jsonl(out/'cases.jsonl',cases)
    return result


def _verified_existing_evaluation(path, manifest, *, stage, split):
    """Fail closed when resuming from a completed, immutable evaluation.

    These reports are not a sealed substitute for prospective evaluation.
    Their data lineage and internally recomputed exact-case rates must match.
    """
    report=js(path/'report.json')
    expected_cases={'dev':80,'challenge':32}[split]
    metric=cur.GATE_REQUIREMENTS[stage]['metric']
    details=report.get('cases_detail')
    require(report.get('schema')==cur.SCHEMA and report.get('stage')==stage and
            report.get('checkpoint_stage')=='G2' and report.get('split')==split and
            report.get('cases')==expected_cases and report.get('limited') is False and
            report.get('test_split_used') is False and report.get('generated_tokens')==0 and
            report.get('teacher_graph_at_inference') is False and
            report.get('checkpoint_head_sha256')==manifest['head_sha256'] and
            report.get('dataset_sha256')==manifest['dataset_sha256'] and
            report.get('source_adapter_sha256')==manifest['source_adapter_sha256'],
            'EXISTING_EVALUATION_LINEAGE_'+stage+'_'+split)
    require(isinstance(details,list) and len(details)==expected_cases and
            all(r.get('index')==i and type(r.get('passed')) is bool
                for i,r in enumerate(details,1)),
            'EXISTING_EVALUATION_CASES_'+stage+'_'+split)
    computed=sum(r['passed'] for r in details)/expected_cases
    require(abs(report.get('metrics',{}).get(metric,-1)-computed)<1e-12,
            'EXISTING_EVALUATION_METRICS_'+stage+'_'+split)
    return report


def _finish_trained_run(paths,dest,trained,unchanged,*,device,max_length):
    """Complete saved G2 work; never invoke the optimizer or change G1-0082 code.

    G1-0082 already independently certified its causal ablations.  Here G1 is
    evaluated for RETENTION only.  The signed curriculum's G1 branch forbids
    feeding G2 inference_spans to the G1-only head; so use ablation_limit=0.
    G2's own causal ablations remain enabled and are NOT bypassed.
    """
    require(not (dest/'report.json').exists() and not (dest/'prospective').exists(),
            'COMPLETION_ALREADY_RECORDED')
    reports={}
    for stage in ('G2','G1'):
        for split in ('dev','challenge'):
            key=stage+'_'+split
            evaluation=dest/'evaluation'/key
            if evaluation.exists():
                reports[key]=_verified_existing_evaluation(evaluation,trained,stage=stage,split=split)
            else:
                # G2: retain original 32-case causal tests. G1: retention only;
                # its separately certified G1-0082 ablations remain authoritative.
                reports[key]=cur.evaluate(dataset=paths['dataset'],checkpoint=dest/'training',
                    output=evaluation,split=split,device=device,
                    max_length=max_length,ablation_limit=32 if stage=='G2' else 0,
                    target_stage=stage)
    certificate=cur.certify(checkpoint=dest/'training',
        dev_report=dest/'evaluation'/'G2_dev'/'report.json',
        challenge_report=dest/'evaluation'/'G2_challenge'/'report.json',
        retention_dev={'G1':dest/'evaluation'/'G1_dev'/'report.json'},
        retention_challenge={'G1':dest/'evaluation'/'G1_challenge'/'report.json'},
        output=dest/'development-gate')
    command=[sys.executable,'-u','-m','tools.nanojev_gamer_v1_g2_native_training_0083',
             'predict','--root',str(dest.resolve()),'--device',device,
             '--max-length',str(max_length)]
    subprocess.run(command,check=True)
    prospect=score(root=dest)
    reasons=list(certificate['failures'])+['prospective:'+x for x in prospect['failures']]
    summary={'schema':SCHEMA,'status':('G2_PREDICTED_SPAN_TRAINING_AND_GATES_PASSED' if
                not reasons else 'G2_PREDICTED_SPAN_TRAINED_BUT_GATE_BLOCKED'),
             'g1_native_gate_verified':True,'g2_trained_on_predicted_g1':True,
             'g2_checkpoint_sha256':trained['head_sha256'],
             'frozen_parameters':unchanged,
             'dev_binding_exact':reports['G2_dev']['metrics']['binding_exact_rate'],
             'challenge_binding_exact':reports['G2_challenge']['metrics']['binding_exact_rate'],
             'g1_retention':{k:reports[k]['metrics']['object_exact_rate']
                             for k in ('G1_dev','G1_challenge')},
             'g1_retention_ablation_scope':'retention_only; original G1-0082 causal certification preserved',
             'formal_development_gate':certificate['status'],
             'prospective_binding_gate':prospect['status'],'failures':reasons,
             'g2_development_and_prospective_gates_passed':not reasons,
             'g2_certified':False,'g3_training_unlocked':False,'production_promotion':False}
    write_json(dest/'report.json',summary)
    print('G2_0083_RESULT '+json.dumps({
        'status':summary['status'],
        'dev_binding_exact':summary['dev_binding_exact'],
        'challenge_binding_exact':summary['challenge_binding_exact'],
        'prospective_dev_exact':prospect['splits']['dev']['metrics']['trained']['exact'],
        'prospective_challenge_exact':prospect['splits']['challenge']['metrics']['trained']['exact'],
        'prospective_edge_f1':{split:prospect['splits'][split]['metrics']['trained']['edge_f1']
                            for split in ('dev','challenge')},
        'unreachable_gold_edges':{split:prospect['splits'][split]['gold_edges_unreachable_from_predicted_g1']
                            for split in ('dev','challenge')},
        'failures':reasons,'g3_training_unlocked':False},sort_keys=True),flush=True)
    return summary


def _verify_saved_training(paths,dest,parent):
    """Resume ONLY the exact native G2 checkpoint saved by this campaign."""
    require(dest.is_dir() and (dest/'training').is_dir() and
            (dest/'seal'/'commit.json').is_file(), 'NO_SAVED_G2_CAMPAIGN')
    check_seal(paths,dest,expected_head=parent['head_sha256'])
    _,trained=cur._load_checkpoint(dest/'training',device='cpu')
    bound=trained.get('native_composite_g1') or {}
    require(trained.get('stage')=='G2' and trained.get('train_limit') in (0,None) and
            trained.get('head_sha256')==sha(dest/'training'/cur.HEAD_FILE) and
            trained.get('dataset_sha256')==parent['dataset_sha256'] and
            trained.get('source_adapter_sha256')==parent['source_adapter_sha256'] and
            trained.get('deterministic_fabric')==parent.get('deterministic_fabric') and
            bound.get('mode')=='predicted_G1_spans_native_G2_binding' and
            Path(bound.get('checkpoint','')).resolve()==paths['composite'].resolve() and
            bound.get('checkpoint_manifest_sha256')==sha(paths['composite']/'composite-interface.json') and
            Path(bound.get('certificate','')).resolve()==paths['certificate'].resolve() and
            bound.get('certificate_sha256')==sha(paths['certificate']),
            'SAVED_G2_LINEAGE_INVALID')
    return trained


def resume(*,root='../runtime/reports/gamer-v1',output=None,device='cuda',max_length=2048):
    """Resume a run interrupted AFTER G2 training, without retraining or resealing."""
    paths=defaults(root)
    dest=Path(output) if output else Path(root)/'oracle-fabric-g2-native-training-0083'
    require(1<=max_length<=4096,'MAX_LENGTH')
    parent,_=preflight(paths)
    trained=_verify_saved_training(paths,dest,parent)
    unchanged=compare_frozen_stage(paths['g1'],dest/'training')
    print('G2_0083_RESUME '+json.dumps({'mode':'EVALUATION_ONLY_NO_RETRAIN',
          'checkpoint_sha256':trained['head_sha256'],
          'existing_dev_challenge_reused':True},sort_keys=True),flush=True)
    return _finish_trained_run(paths,dest,trained,unchanged,
                               device=device,max_length=max_length)


def run(*,root='../runtime/reports/gamer-v1',output=None,device='cuda',
        steps=STEPS,accum=ACCUM,lr=LR,max_length=2048):
    paths=defaults(root)
    dest=Path(output) if output else Path(root)/'oracle-fabric-g2-native-training-0083'
    require(not dest.exists() and dest.parent.is_dir(),'OUTPUT_IMMUTABLE')
    require(steps>=1 and accum>=1 and 0<lr<.01 and 1<=max_length<=4096,
            'TRAINING_SETTINGS')
    parent,_=preflight(paths)
    dest.mkdir(parents=False,exist_ok=False)
    commit_seal(paths,dest,parent)
    # Train ONLY G2 from certified G1 predictions; no teacher-forced G1 spans.
    torch.manual_seed(830083)
    trained=cur.train(dataset=paths['dataset'],output=dest/'training',
        source_adapter=paths['adapter'],stage='G2',
        parent_checkpoint=paths['g1'],prerequisite_cert=paths['certificate'],
        native_composite_g1=paths['composite'],native_composite_cert=paths['certificate'],
        cutover_id=parent['deterministic_fabric']['cutover_id'],
        steps=steps,accum=accum,lr=lr,device=device,
        consistency_weight=0,max_length=max_length)
    require(trained['stage']=='G2' and trained['train_limit']==0 and
            trained['native_composite_g1']['mode']=='predicted_G1_spans_native_G2_binding',
            'TRAINING_MODE_INVALID')
    unchanged=compare_frozen_stage(paths['g1'],dest/'training')
    return _finish_trained_run(paths,dest,trained,unchanged,
                               device=device,max_length=max_length)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('run')
    p.add_argument('--root',default='../runtime/reports/gamer-v1')
    p.add_argument('--output')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--steps',type=int,default=STEPS)
    p.add_argument('--accum',type=int,default=ACCUM)
    p.add_argument('--lr',type=float,default=LR)
    p.add_argument('--max-length',type=int,default=2048)
    p=sub.add_parser('resume')
    p.add_argument('--root',default='../runtime/reports/gamer-v1')
    p.add_argument('--output')
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--max-length',type=int,default=2048)
    p=sub.add_parser('predict')
    p.add_argument('--root',required=True)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--max-length',type=int,default=2048)
    p=sub.add_parser('score');p.add_argument('--root',required=True)
    args=vars(parser.parse_args(argv));cmd=args.pop('command')
    return {'run':run,'resume':resume,'predict':predict,'score':score}[cmd](**args)

if __name__=='__main__':main()
