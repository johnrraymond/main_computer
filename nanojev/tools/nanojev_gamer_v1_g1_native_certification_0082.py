"""G1-0082: native composite G1->G2 execution and prospective sealed certification.

One invocation stages immutable commitments, checks exact per-case parity against
G1-0081, executes a label-blind prediction subprocess, then scores against the
committed oracle answers.  No fit, promotion or G2 training takes place.

A native G1 certificate is NOT automatically a license to run the original G2
curriculum: legacy G2 scripts still need an explicit composite-consumption mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools import nanojev_gamer_v1_g1_certification_audit_0080 as audit
from tools import nanojev_gamer_v1_g1_certification_completion_0081 as completion
from tools import nanojev_gamer_v1_g1_native_composite_0082 as native

SCHEMA='nanojev.gamer.graph.g1.native-certification-0082.v1'
SEED=820082
DEV=96
CHALLENGE=64


def require(value,code):
    if not value:raise ValueError('G1_0082_'+code)


def write_json(path,data):
    with Path(path).open('x',encoding='utf8') as f:
        f.write(json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+'\n')


def write_jsonl(path,items):
    with Path(path).open('x',encoding='utf8') as f:
        for item in items:
            f.write(json.dumps(item,sort_keys=True,allow_nan=False)+'\n')


def rows(path):
    return audit.read_jsonl(path)


def sha_text(value):
    return hashlib.sha256(value.encode('utf8')).hexdigest()


def _defaults(root):
    root=Path(root)
    return {
        'checkpoint':root/'oracle-fabric-g1-certification-completion-0081'/'checkpoint',
        'completion':root/'oracle-fabric-g1-certification-completion-0081',
        'dataset':root/'graph-span-curriculum-0005.jsonl',
        'seal_0077':root/'oracle-fabric-g1-sealed-0077',
        'seal_0079':root/'oracle-fabric-g1-quantity-preserving-0079-seal',
        'output':root/'oracle-fabric-g1-native-certification-0082',
    }


def _verify_0081(path,checkpoint):
    report=audit.read_json(Path(path)/'certificate.json')
    require(report.get('schema')==completion.SCHEMA and
            report.get('status')=='COMPOSITE_DEVELOPMENT_GATE_PASSED_NOT_NATIVE' and
            report.get('numeric_development_gate_passed') is True and
            report.get('numeric_gate_failures')==[] and
            report.get('dev_and_challenge_are_blind') is False and
            report.get('g2_unlocked') is False and report.get('production_promotion') is False,
            'UPSTREAM_0081_NOT_ELIGIBLE')
    require(report.get('coefficient_checkpoint_sha256')==
            audit.sha(Path(checkpoint)/'composite-interface.json'),
            'UPSTREAM_0081_CHECKPOINT_DRIFT')
    for split,expected in report.get('reports_sha256',{}).items():
        require(split in ('dev','challenge') and audit.sha(Path(path)/'evaluations'/split/'report.json')==expected,
                'UPSTREAM_0081_REPORT_TAMPERED')
    hashes=report.get('reports_sha256',{})
    require(set(hashes)=={'dev','challenge'},'MISSING_0081_REPORTS')
    return report


def _historical_cases(checkpoint,completion_dir,original_dataset):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    original=cur.load_rows(original_dataset)
    for split in ('dev','challenge'):
        expected=rows(Path(completion_dir)/'evaluations'/split/'cases.jsonl')
        source=[x for x in original if x['split']==split]
        require(len(expected)==len(source),'UPSTREAM_0081_CASE_COUNT_'+split)
        for row,exp in zip(source,expected):
            require(exp['prompt_sha256']==sha_text(row['prompt']) and
                    exp['family']==row['family'],'UPSTREAM_0081_CASE_BINDING')
            yield row,exp


def _verify_parity(predicted,expected):
    require(predicted=={tuple(x) for x in expected['selected']},
            'NATIVE_0081_EXACT_OBJECT_PARITY_FAILED')


def _exclude(root,dataset,seal_0077,seal_0079):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    excluded={sha_text(x['prompt']) for x in cur.load_rows(dataset)}
    for p in (Path(seal_0077)/'prompts.jsonl',
              Path(seal_0079)/'train.jsonl',
              Path(seal_0079)/'selection.jsonl',
              Path(seal_0079)/'audit.prompts.jsonl'):
        require(p.is_file(),'MISSING_PREVIOUS_PROMPT_COHORT')
        for item in rows(p):
            require(item['prompt_sha256']==sha_text(item['prompt']),
                    'INVALID_PREVIOUS_PROMPT_HASH')
            excluded.add(item['prompt_sha256'])
    # G1-0078's 192 newly generated research cases were also exposed. Their
    # case ledger records hashes for BOTH checkpoints; exclude all phases.
    older=Path(seal_0079).parent/'oracle-fabric-g1-reconstruction-reliability-bakeoff-0001'/'cases.jsonl'
    require(older.is_file(),'MISSING_0078_PREVIOUS_RESEARCH_COHORT')
    previous=rows(older)
    require(len(previous)>=192 and all(isinstance(x.get('prompt_sha256'),str) and
            len(x['prompt_sha256'])==64 for x in previous), 'INVALID_0078_PROMPT_HASHES')
    excluded.update(x['prompt_sha256'] for x in previous)
    return excluded


def _fresh(dataset,seal_0077,seal_0079,seed=SEED,dev=DEV,challenge=CHALLENGE):
    from tools import nanojev_gamer_v1_graph_corpus as corpus
    require(seed!=790079 and seed!=760077 and dev>=20 and challenge>=12 and
            dev%2==challenge%2==0,'INVALID_NEW_COHORT')
    exclude=_exclude(None,dataset,seal_0077,seal_0079)
    rng=random.Random(seed)
    all_rows=[];serial=82_000_000
    for split,n in (('dev',dev),('challenge',challenge)):
        for i in range(n):
            factory=corpus.encounter if i%2==0 else corpus.allocation
            for _ in range(1000):
                item=factory(rng,'test',serial)
                serial+=1
                key=sha_text(item['prompt'])
                if key not in exclude:break
            else:raise ValueError('G1_0082_NEW_PROMPT_EXHAUSTED')
            exclude.add(key)
            require(item['family'] in ('encounter','allocation') and
                    item['object_spans'],'EMPTY_GENERATED_ORACLE')
            all_rows.append({'case_id':f'g1-0082-{split}-{i+1:04}',
                'split':split,'family':item['family'],'prompt':item['prompt'],
                'prompt_sha256':key,'gold':sorted([list((s['start'],s['end'],s['kind']))
                                                 for s in item['object_spans']])})
    return all_rows


def _commit(directory,rows_new,reference,composite,source):
    directory.mkdir(parents=True,exist_ok=False)
    prompts=[{k:r[k] for k in ('case_id','split','family','prompt','prompt_sha256')}
             for r in rows_new]
    answers=[{k:r[k] for k in ('case_id','split','family','prompt_sha256','gold')}
             for r in rows_new]
    write_jsonl(directory/'prompts.jsonl',prompts)
    write_jsonl(directory/'answers.SECRET.jsonl',answers)
    commit={'schema':SCHEMA,'status':'ANSWERS_COMMITTED_NOT_PREDICTED',
        'cases':len(prompts),'dev_cases':sum(x['split']=='dev' for x in prompts),
        'challenge_cases':sum(x['split']=='challenge' for x in prompts),
        'prompts_sha256':audit.sha(directory/'prompts.jsonl'),
        'answers_sha256':audit.sha(directory/'answers.SECRET.jsonl'),
        'composite_checkpoint_sha256':audit.sha(composite/'composite-interface.json'),
        'upstream_0081_certificate_sha256':audit.sha(reference/'certificate.json'),
        'dataset_sha256':audit.sha(source),
        'seed':SEED,'production_promotion':False,'g2_training_unlocked':False}
    write_json(directory/'commit.json',commit)
    return commit


def _check_commit(seal,checkpoint,completion_dir,dataset):
    seal=Path(seal);record=audit.read_json(seal/'commit.json')
    require(record.get('schema')==SCHEMA and
            record.get('status')=='ANSWERS_COMMITTED_NOT_PREDICTED' and
            record.get('cases')==record.get('dev_cases',0)+record.get('challenge_cases',0) and
            record.get('dev_cases',0)>=20 and record.get('challenge_cases',0)>=12,
            'BAD_SEAL')
    for key,p in [('prompts_sha256','prompts.jsonl'),
                  ('answers_sha256','answers.SECRET.jsonl')]:
        require(audit.sha(seal/p)==record.get(key),'SEALED_'+key+'_DRIFT')
    require(audit.sha(Path(checkpoint)/'composite-interface.json')==
            record['composite_checkpoint_sha256'] and
            audit.sha(Path(completion_dir)/'certificate.json')==
            record['upstream_0081_certificate_sha256'] and
            audit.sha(dataset)==record['dataset_sha256'],'INPUT_COMMIT_DRIFT')
    return record


def _capture(model,tok,prompt,device,max_length):
    from tools import nanojev_gamer_v1_backbone_first as bb
    import torch
    prep=bb.prepare_prompt(tok,prompt,max_input_tokens=max_length)
    with torch.inference_mode():
        hidden=bb.frozen_context(model,prep,device=device)
    return prep,hidden


def predict(*,checkpoint,completion_dir,seal,dataset,output,device='cuda',max_length=2048,
            verify_historical=True):
    """This function NEVER opens or hashes a seal answer file, even on errors."""
    from tools import nanojev_gamer_v1_backbone_first as bb
    from tools.nanojev_gamer_v1_graph_head import decode_graph
    seal=Path(seal);destination=Path(output)
    require(not destination.exists() and max_length>0,'PREDICTION_IMMUTABLE')
    _verify_0081(completion_dir,checkpoint)
    manifest=audit.read_json(seal/'commit.json')
    require(manifest.get('status')=='ANSWERS_COMMITTED_NOT_PREDICTED' and
            audit.sha(seal/'prompts.jsonl')==manifest['prompts_sha256'] and
            audit.sha(checkpoint+'/composite-interface.json' if isinstance(checkpoint,str)
                      else Path(checkpoint)/'composite-interface.json')==
                      manifest['composite_checkpoint_sha256'], 'PREDICTION_INPUT_DRIFT')
    runtime=native.load_composite(checkpoint,device=device)
    m=runtime.manifest
    require(bb.adapter_fingerprint(m['source_adapter'])==m['source_adapter_sha256'],
            'FROZEN_SOURCE_ADAPTER_DRIFT')
    model,tok=bb.model_components(m['model_id'],m['source_adapter'],device)
    parity=[]
    if verify_historical:
        for index,(row,old) in enumerate(_historical_cases(checkpoint,completion_dir,dataset),1):
            prep,hidden=_capture(model,tok,row['prompt'],device,max_length)
            result=runtime.predict(hidden,prep,family=row['family'],native_stage='G2')
            _verify_parity(result['objects'],old)
            parity.append({'prompt_sha256':old['prompt_sha256'],
                           'selected':[list(x) for x in sorted(result['objects'])],
                           'native_g2_surface_count':result['output']['surface_count']})
            if index==1 or index%20==0 or index==112:
                print(f'G1_0082_NATIVE_PARITY {index}/112',flush=True)
        require(len(parity)==112,'UPSTREAM_PARITY_CASES')
    output_rows=[]
    prompts=rows(seal/'prompts.jsonl')
    require(len(prompts)==manifest['cases'] and
            len({x['case_id'] for x in prompts})==len(prompts),'PREDICTION_CASES')
    for index,item in enumerate(prompts,1):
        require(sha_text(item['prompt'])==item['prompt_sha256'],
                'PREDICTION_PROMPT_DRIFT')
        prep,hidden=_capture(model,tok,item['prompt'],device,max_length)
        modes={}
        for mode in ('full','zero_hidden','stage_component'):
            result=runtime.predict(hidden,prep,family=item['family'],mode=mode,native_stage='G2')
            surfaces=decode_graph(result['output'],prep,unlocked='G2')[1]
            require(len(surfaces)==len(result['objects']), 'NATIVE_GRAPH_DECODE_LOST_SURFACES')
            modes[mode]=[list(x) for x in sorted(result['objects'])]
        output_rows.append({k:item[k] for k in ('case_id','split','family','prompt_sha256')} |
                           {'predictions':modes,'native_g2_surface_consumed':True})
        if index==1 or index%10==0 or index==len(prompts):
            print(f'G1_0082_SEALED_NATIVE {index}/{len(prompts)}',flush=True)
    destination.mkdir(parents=True,exist_ok=False)
    write_jsonl(destination/'predictions.jsonl',output_rows)
    write_jsonl(destination/'historical-parity.jsonl',parity)
    write_json(destination/'lock.json',{
        'schema':SCHEMA,'status':'BLIND_NATIVE_PREDICTIONS_LOCKED',
        'cases':len(prompts), 'predictions_sha256':audit.sha(destination/'predictions.jsonl'),
        'prompts_sha256':manifest['prompts_sha256'],
        'composite_checkpoint_sha256':manifest['composite_checkpoint_sha256'],
        'historical_parity_count':len(parity),
        'historical_parity_sha256':audit.sha(destination/'historical-parity.jsonl'),
        'native_G2_head_consumed_composite_spans':True,
        'oracle_labels_read_during_inference':False,
        'production_promotion':False,'g2_training_unlocked':False})
    return len(output_rows)


def _score_metrics(rows_pred,gold_rows,gate):
    from tools import nanojev_gamer_v1_g1_certification_audit_0080 as evidence
    expected={x['case_id']:x for x in gold_rows}
    require(len(expected)==len(gold_rows)==len(rows_pred) and
            {x['case_id'] for x in rows_pred}==set(expected),
            'SCORING_COUNT_OR_DUPLICATE_ID')
    output={}
    for split in ('dev','challenge'):
        selections=[];causal={k:0 for k in ('zero_hidden','stage_component')}
        present=0
        for item in rows_pred:
            if item['split']!=split:continue
            secret=expected[item['case_id']]
            require(item['family']==secret['family'] and
                    item['prompt_sha256']==secret['prompt_sha256'] and
                    secret['split']==split,'SCORING_CASE_BINDING')
            n=secret['prompt_length']
            gold=evidence.checked_objects(secret['gold'],n,'GOLD')
            m=item['predictions']
            require(set(m)=={'full','zero_hidden','stage_component'},'ABLATION_SET')
            evaluated={k:evidence.checked_objects(v,n,k) for k,v in m.items()}
            selections.append((evaluated['full'],gold,item['family']))
            for k in causal:causal[k]+=int(evaluated[k]==gold)
            present+=1
        require(present>= (20 if split=='dev' else 12),'SCORING_SPLIT_MISSING')
        metrics=evidence.metrics(selections)
        failures=[]
        for name in ('actor','action','quantity'):
            if metrics['by_kind'].get(name,{}).get('recall',0)<gate['kind_recall']:
                failures.append(name+'_recall_below_gate')
        if metrics['object_exact_rate']<gate[split]:failures.append('object_exact_rate_below_gate')
        if metrics['object_macro_f1']<gate['macro_f1']:failures.append('macro_f1_below_gate')
        if len(metrics['by_family'])<2:failures.append('family_diversity_missing')
        for ablation,correct in causal.items():
            gain=(metrics['exact_prompts']-correct)/present
            metrics[ablation+'_ablation']={'cases':present,'correct':correct,'net_gain':gain}
            if gain<gate['neural_gain']:failures.append(ablation+'_causality_missing')
        output[split]={'cases':present,'metrics':metrics,'failures':failures}
    return output


def score(*,checkpoint,completion_dir,seal,predictions,dataset,output):
    seal=Path(seal);predictions=Path(predictions);target=Path(output)
    require(not target.exists(),'SCORING_IMMUTABLE')
    commit=_check_commit(seal,checkpoint,completion_dir,dataset)
    lock=audit.read_json(predictions/'lock.json')
    require(lock.get('schema')==SCHEMA and
            lock.get('status')=='BLIND_NATIVE_PREDICTIONS_LOCKED' and
            lock.get('historical_parity_count')==112 and
            lock.get('native_G2_head_consumed_composite_spans') is True and
            lock.get('oracle_labels_read_during_inference') is False and
            lock.get('prompts_sha256')==commit['prompts_sha256'] and
            lock.get('composite_checkpoint_sha256')==commit['composite_checkpoint_sha256'] and
            audit.sha(predictions/'predictions.jsonl')==lock['predictions_sha256'] and
            audit.sha(predictions/'historical-parity.jsonl')==lock['historical_parity_sha256'],
            'LOCKED_NATIVE_PREDICTION_DRIFT')
    # Recheck that the same native G2 inputs reproduced every *individual*
    # G1-0081 result, rather than trusting a single summary parity count.
    historical=rows(predictions/'historical-parity.jsonl')
    source_cases=list(_historical_cases(checkpoint,completion_dir,dataset))
    require(len(historical)==len(source_cases)==112,'NATIVE_PARITY_COUNT')
    for observed,(_,expected) in zip(historical,source_cases):
        require(observed['prompt_sha256']==expected['prompt_sha256'] and
                observed['selected']==expected['selected'] and
                observed['native_g2_surface_count']==len(expected['selected']),
                'NATIVE_PARITY_EVIDENCE_DRIFT')
    all_rows=rows(predictions/'predictions.jsonl')
    answer_rows=rows(seal/'answers.SECRET.jsonl')
    prompts=rows(seal/'prompts.jsonl')
    prompt_by={x['case_id']:x for x in prompts}
    for x in answer_rows:
        require(x['case_id'] in prompt_by,'ANSWER_UNBOUND')
        x['prompt_length']=len(prompt_by[x['case_id']]['prompt'])
    gate=completion.evidence.frozen_gate_requirements(
        Path(__file__).with_name('nanojev_gamer_v1_graph_curriculum.py'))
    reports=_score_metrics(all_rows,answer_rows,gate)
    old=_verify_0081(completion_dir,checkpoint)
    failures=[split+':'+f for split in ('dev','challenge') for f in reports[split]['failures']]
    status='NATIVE_COMPOSITE_G1_PROSPECTIVE_GATE_PASSED' if not failures else 'NATIVE_COMPOSITE_G1_PROSPECTIVE_GATE_BLOCKED'
    cert={'schema':SCHEMA,'status':status,
        'native_G1_G2_parity_verified':True,
        'native_G2_head_consumed_composite_G1_objects':True,
        'prospective_gate_passed':not failures,
        'g1_native_composite_certified':not failures,
        'g2_training_unlocked':not failures, # EXPLICIT composite-aware native G2 mode only
        'production_promotion':False,
        'original_gate_requirements':gate,
        'failures':failures,
        'composite_checkpoint_sha256':commit['composite_checkpoint_sha256'],
        'upstream_0081_certificate_sha256':commit['upstream_0081_certificate_sha256'],
        'sealed_prompts_sha256':commit['prompts_sha256'],
        'sealed_answers_sha256':commit['answers_sha256'],
        'blind_predictions_sha256':lock['predictions_sha256'],
        'historical_parity_count':lock['historical_parity_count'],
        'historical_parity_sha256':lock['historical_parity_sha256'],
        'source_code_sha256':{p.stem:audit.sha(p) for p in (
            Path(__file__),Path(native.__file__),
            Path(__file__).with_name('nanojev_gamer_v1_graph_head.py'))},
        'splits':reports,
        'scope':'Prospective native G1->G2 consumption; authorizes ONLY explicitly composite-aware G2 training if passed.'}
    target.mkdir(parents=True,exist_ok=False)
    write_json(target/'certificate.json',cert)
    print('G1_0082_RESULT '+json.dumps({
        'status':status,'dev_exact':reports['dev']['metrics']['exact_prompts'],
        'dev_cases':reports['dev']['cases'],
        'challenge_exact':reports['challenge']['metrics']['exact_prompts'],
        'challenge_cases':reports['challenge']['cases'],
        'native_parity':lock['historical_parity_count'],
        'failures':failures,'g2_training_unlocked':not failures},sort_keys=True),flush=True)
    return cert


def verify_native_certificate(path,checkpoint,*,expected_head_sha256=None):
    """Independent proof before native G2 training is authorized.

    Reopen the seal, locked blind predictions and gold; do not accept a mere
    ``status`` string. Fail closed on any changed evidence or incomplete gate.
    """
    certificate=Path(path).resolve()
    destination=certificate.parent.parent
    root=Path(checkpoint).resolve()
    require(certificate.name=='certificate.json' and certificate.parent.name=='scored',
            'NATIVE_CERT_LOCATION')
    proof=audit.read_json(certificate)
    require(proof.get('schema')==SCHEMA and
            proof.get('status')=='NATIVE_COMPOSITE_G1_PROSPECTIVE_GATE_PASSED' and
            proof.get('g1_native_composite_certified') is True and
            proof.get('g2_training_unlocked') is True and
            proof.get('production_promotion') is False and
            proof.get('native_G1_G2_parity_verified') is True and
            proof.get('native_G2_head_consumed_composite_G1_objects') is True and
            proof.get('failures')==[], 'NATIVE_CERT_NOT_AUTHORIZED')
    # Verify all three immutable evidence files without resolving a source path
    # from untrusted certificate strings.
    seal=destination/'seal';blind=destination/'blind'
    data=audit.read_json(seal/'commit.json')
    lock=audit.read_json(blind/'lock.json')
    require(data.get('schema')==SCHEMA and lock.get('schema')==SCHEMA and
            lock.get('status')=='BLIND_NATIVE_PREDICTIONS_LOCKED' and
            lock.get('historical_parity_count')==112 and
            audit.sha(seal/'prompts.jsonl')==data.get('prompts_sha256') and
            audit.sha(seal/'answers.SECRET.jsonl')==data.get('answers_sha256') and
            audit.sha(blind/'predictions.jsonl')==lock.get('predictions_sha256') and
            audit.sha(blind/'historical-parity.jsonl')==lock.get('historical_parity_sha256') and
            proof.get('historical_parity_sha256')==lock['historical_parity_sha256'] and
            audit.sha(root/'composite-interface.json')==data.get('composite_checkpoint_sha256') and
            proof.get('composite_checkpoint_sha256')==data['composite_checkpoint_sha256'] and
            proof.get('blind_predictions_sha256')==lock['predictions_sha256'] and
            proof.get('sealed_answers_sha256')==data['answers_sha256'],
            'NATIVE_CERT_EVIDENCE_TAMPERED')
    # The historical reference must be present and exact. The source run is
    # already locked by its own per-split SHA-256 report commitments.
    original_reference=destination.parent/'oracle-fabric-g1-certification-completion-0081'
    require((original_reference/'certificate.json').is_file(),'NATIVE_CERT_SOURCE_0081_MISSING')
    old=_verify_0081(original_reference,root)
    require(audit.sha(original_reference/'certificate.json')==
            data['upstream_0081_certificate_sha256'], 'NATIVE_CERT_SOURCE_0081_DRIFT')
    observed=rows(blind/'historical-parity.jsonl')
    source_cases=list(_historical_cases(root,original_reference,
        destination.parent/'graph-span-curriculum-0005.jsonl'))
    require(len(observed)==len(source_cases)==112,'NATIVE_CERT_PARITY_COUNT')
    for r,(_,expected) in zip(observed,source_cases):
        require(r['prompt_sha256']==expected['prompt_sha256'] and
                r['selected']==expected['selected'] and
                r['native_g2_surface_count']==len(expected['selected']),
                'NATIVE_CERT_PARITY_DRIFT')
    for stem,sha in proof.get('source_code_sha256',{}).items():
        require(audit.sha(Path(__file__).with_name(stem+'.py'))==sha,
                'NATIVE_CERT_CODE_DRIFT_'+stem)
    original=audit.read_json(root/'head-manifest.json')
    require(expected_head_sha256 is None or
            original['head_sha256']==expected_head_sha256,'NATIVE_CERT_PARENT_HEAD_DRIFT')
    guard=completion.evidence.frozen_gate_requirements(
        Path(__file__).with_name('nanojev_gamer_v1_graph_curriculum.py'))
    require(proof['original_gate_requirements']==guard,'NATIVE_CERT_GATE_CHANGED')
    answers=rows(seal/'answers.SECRET.jsonl')
    prompts={x['case_id']:x for x in rows(seal/'prompts.jsonl')}
    for item in answers:
        require(item['case_id'] in prompts and
                item['prompt_sha256']==prompts[item['case_id']]['prompt_sha256'],
                'NATIVE_CERT_ANSWER_BINDING')
        item['prompt_length']=len(prompts[item['case_id']]['prompt'])
    recomputed=_score_metrics(rows(blind/'predictions.jsonl'),answers,guard)
    require(not recomputed['dev']['failures'] and not recomputed['challenge']['failures'] and
            recomputed==proof['splits'], 'NATIVE_CERT_RESCORING_DRIFT')
    return proof


def run(*,root='../runtime/reports/gamer-v1',output=None,device='cuda',
        max_length=2048,seed=SEED,dev=DEV,challenge=CHALLENGE):
    paths=_defaults(root)
    if output:paths['output']=Path(output)
    dest=paths['output'].resolve()
    require(not dest.exists() and dest.parent.is_dir(),'IMMUTABLE_OUTPUT')
    require((seed,dev,challenge)==(SEED,DEV,CHALLENGE), 'PREDECLARED_SEALED_DESIGN_LOCKED')
    # Interrupted sealed runs cannot be silently reused or superseded after
    # an operator could have seen their answers.
    require(not list(dest.parent.glob('.g1_0082_sealed_*')),
            'INTERRUPTED_SEAL_REQUIRES_INDEPENDENT_NEW_COHORT')
    _verify_0081(paths['completion'],paths['checkpoint'])
    # Before committing a new test, recheck that the composite code+weights are
    # compatible with the installed native consumer. No oracle answer opened.
    native.load_composite(paths['checkpoint'],device='cpu')
    test_rows=_fresh(paths['dataset'],paths['seal_0077'],paths['seal_0079'],
                     seed=seed,dev=dev,challenge=challenge)
    scratch=Path(tempfile.mkdtemp(prefix='.g1_0082_sealed_',dir=dest.parent))
    try:
        _commit(scratch/'seal',test_rows,paths['completion'],paths['checkpoint'],paths['dataset'])
        cmd=[sys.executable,'-u','-m','tools.nanojev_gamer_v1_g1_native_certification_0082',
             'predict','--checkpoint',str(paths['checkpoint']),'--completion-dir',str(paths['completion']),
             '--seal',str(scratch/'seal'),'--dataset',str(paths['dataset']),
             '--output',str(scratch/'blind'),'--device',device,'--max-length',str(max_length)]
        subprocess.run(cmd,check=True)
        score(checkpoint=paths['checkpoint'],completion_dir=paths['completion'],
              seal=scratch/'seal',predictions=scratch/'blind',dataset=paths['dataset'],
              output=scratch/'scored')
        require(not dest.exists(),'OUTPUT_COLLISION')
        scratch.rename(dest)
        return audit.read_json(dest/'scored'/'certificate.json')
    except BaseException:
        # A prepared but interrupted seal cannot be retried as fresh. Preserve
        # evidence in the staging directory; never change or silently reuse it.
        print('G1_0082_INTERRUPTED '+str(scratch),flush=True)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    run_args=sub.add_parser('run')
    run_args.add_argument('--root',default='../runtime/reports/gamer-v1')
    run_args.add_argument('--output')
    run_args.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    run_args.add_argument('--max-length',type=int,default=2048)
    run_args.add_argument('--seed',type=int,default=SEED)
    run_args.add_argument('--dev',type=int,default=DEV)
    run_args.add_argument('--challenge',type=int,default=CHALLENGE)
    p=sub.add_parser('predict')
    for name in ('checkpoint','completion-dir','seal','dataset','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--max-length',type=int,default=2048)
    s=sub.add_parser('score')
    for name in ('checkpoint','completion-dir','seal','dataset','predictions','output'):
        s.add_argument('--'+name,required=True)
    a=vars(parser.parse_args(argv));cmd=a.pop('command')
    if cmd=='run':return run(**a)
    if cmd=='predict':return predict(**a)
    return score(**a)

if __name__=='__main__':main()
