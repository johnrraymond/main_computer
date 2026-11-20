"""G1-0081: immutable composite G1 checkpoint + current-interface developmental gate.

Single-command, fail-closed completion of the evidence missing from G1-0080.
Replays the EXACT selected G1-0079 cutover interface (no fitting) on the
original declared dev/challenge splits, with matched zero-hidden and G1-span-
component causal ablations, and exports a hash-bound composite checkpoint.

A passed composite developmental gate is NOT a native G1 head certificate:
G2's native curriculum verifier and graph interface have not been altered to
consume the learned decoder. Do not unlock G2 or promote production here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from tools import nanojev_gamer_v1_g1_certification_audit_0080 as evidence

SCHEMA = 'nanojev.gamer.graph.g1.composite-certification-completion-0081.v1'
SELECTED_CUTOVER = 'pinned_historical_continued_64'
MODELS = ('cutover', 'legacy')
SPLITS = ('dev', 'challenge')
KINDS = ('actor', 'action', 'quantity')


def require(value, code):
    if not value:
        raise ValueError('G1_0081_' + code)


def digest(path):
    return evidence.sha(path)


def json_write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, allow_nan=False,
                               sort_keys=True, indent=2)+'\n',encoding='utf8')


def relative_defaults(root):
    root = Path(root)
    return dict(
        seal=root/'oracle-fabric-g1-quantity-preserving-0079-seal',
        predictions=root/'oracle-fabric-g1-quantity-preserving-0079-blind',
        scored=root/'oracle-fabric-g1-quantity-preserving-0079-scored',
        seal_0077=root/'oracle-fabric-g1-sealed-0077',
        reliability_reference=root/'oracle-fabric-g1-reconstruction-reliability-bakeoff-0001',
        discrimination_reference=root/'oracle-fabric-g1-discrimination-trainability-bakeoff-0002',
        dataset=root/'graph-span-curriculum-0005.jsonl',
        legacy=root/'graph-g1-retention-retained-0007',
        cutover=root/'oracle-fabric-cutover-0001'/'stages'/'G1',
        cutover_certificate=root/'oracle-fabric-cutover-0001'/'certificates'/'G1'/'certification.json',
        audit_0080=root/'oracle-fabric-g1-certification-audit-0080',
        structural_reference=root/'oracle-fabric-g1-structural-learning-bakeoff-0001',
        learned_reference=root/'oracle-fabric-g1-learned-interface-bakeoff-0001',
        coverage_reference=root/'oracle-fabric-g1-candidate-coverage-bakeoff-0001',
        output=root/'oracle-fabric-g1-certification-completion-0081',
    )


def validate_audit(ledger, scored, gate):
    require(ledger.get('schema')==evidence.SCHEMA and
            ledger.get('status')=='READ_ONLY_EVIDENCE_VERIFIED_NOT_CERTIFIED' and
            ledger.get('integrity')=='PASS' and ledger.get('quantity_invariance')=='PASS',
            'UPSTREAM_AUDIT_INCOMPLETE')
    require(ledger.get('source_gate_requirements')==gate and
            ledger.get('g1_certified') is False and ledger.get('g2_unlocked') is False and
            ledger.get('audit_used_to_train_or_select') is False,
            'UPSTREAM_AUDIT_SCOPE')
    for model in MODELS:
        require(ledger['models'][model]['selected']['exact_prompts']==
                scored['metrics'][model]['selected']['exact_prompts'] and
                abs(ledger['models'][model]['selected']['f1']-
                    scored['metrics'][model]['selected']['f1'])<1e-12,
                'UPSTREAM_AUDIT_METRIC_DRIFT_'+model)
    require(ledger['models']['cutover']['selected']['exact_prompts']==107 and
            ledger['models']['cutover']['selected']['cases']==128,
            'EXPECTED_PROSPECTIVE_COHORT_DRIFT')



def independently_verify_prospective(paths, audit):
    """Recount the 128 selected objects, not merely trust an 0080 status field."""
    seal=paths['seal'];bl=paths['predictions']
    prompts=evidence.read_jsonl(seal/'audit.prompts.jsonl')
    answers=evidence.read_jsonl(seal/'audit.answers.SECRET.jsonl')
    preds=evidence.read_jsonl(bl/'audit.predictions.jsonl')
    require(len(prompts)==len(answers)==128 and len(preds)==256,
            'PROSPECTIVE_COUNTS')
    prompt_by={r['case_id']:r for r in prompts}
    gold={}
    for item in answers:
        ident=item['case_id']
        require(ident in prompt_by and ident not in gold and
                prompt_by[ident]['prompt_sha256']==item['prompt_sha256'] and
                prompt_by[ident]['family']==item['family'],
                'PROSPECTIVE_ANSWER_BINDING')
        gold[ident]=evidence.checked_objects(item['gold'],
                        len(prompt_by[ident]['prompt']),'GOLD')
    for name in MODELS:
        records=[r for r in preds if r['model']==name]
        require(len(records)==128 and len({r['case_id'] for r in records})==128,
                'PROSPECTIVE_MODEL_COUNTS_'+name)
        pairs=[]
        for item in records:
            ident=item['case_id']; require(ident in gold,'PROSPECTIVE_KEY')
            p=prompt_by[ident]
            require(item['prompt_sha256']==p['prompt_sha256'] and
                    item['family']==p['family'],'PROSPECTIVE_PREDICTION_BINDING')
            selected=evidence.checked_objects(item['selected'],len(p['prompt']),
                                              'SELECTED')
            pairs.append((selected,gold[ident],p['family']))
        calculated=evidence.metrics(pairs)
        earlier=audit['models'][name]['selected']
        for key in ('cases','exact_prompts','correct','expected','predicted'):
            require(calculated[key]==earlier[key],
                    'PROSPECTIVE_INDEPENDENT_METRIC_'+name+'_'+key)
        for key in ('f1','object_macro_f1','object_exact_rate'):
            require(abs(calculated[key]-earlier[key])<1e-12,
                    'PROSPECTIVE_INDEPENDENT_METRIC_'+name+'_'+key)
    return True


def gate_reasons(reports, gate):
    """Apply the source G1 curriculum's unmodified developmental thresholds."""
    failures=[]
    for split in SPLITS:
        r=reports[split]
        m=r['metrics']
        if r['cases'] < (20 if split=='dev' else 12):
            failures.append(f'{split}:not_enough_cases')
        if m['object_exact_rate'] < gate[split]:
            failures.append(f'{split}:object_exact_rate_below_threshold')
        if m['object_macro_f1'] < gate['macro_f1']:
            failures.append(f'{split}:object_f1')
        if len(m['by_family'])<2:
            failures.append(f'{split}:family_diversity')
        for kind in KINDS:
            if m['by_kind'].get(kind,{}).get('recall',0) < gate['kind_recall']:
                failures.append(f'{split}:missing_or_inaccurate_{kind}')
        for key,fail in (('zero_hidden_ablation','neural_causality_not_demonstrated'),
                         ('stage_component_ablation','stage_component_causality_not_demonstrated')):
            a=m.get(key,{})
            if a.get('cases',0)<8:
                failures.append(f'{split}:missing_causal_ablation:{key}')
            if a.get('net_gain',0)<gate['neural_gain']:
                failures.append(f'{split}:{fail}')
    return failures



def checkpoint_record(blind, reliability, discrimination, selected, checkpoint, audit_report,
                      head_sha, source_sha, output, *, candidate_prior,
                      structured_reference_weights, upstream_hashes):
    """Serialize the actual composite, not an invented new set of head weights."""
    from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as disc
    from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as quantity
    base,cont=quantity._models(reliability,discrimination,'cutover')
    bundles={'quantity_control_interface':disc._export_model(base),
             'historical_continued_interface':disc._export_model(cont)}
    if selected.startswith('pinned_quantity_margin_') or selected=='pinned_structured_1x':
        bundles['selected_trained_interface']=blind['trained_coefficients']['cutover'][selected]
    for key,value in bundles.items():
        evidence.bundle_integrity(value,'0081_'+key)
    input_hashes={n:digest(Path(__file__).with_name(n+'.py')) for n in (
        'nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079',
        'nanojev_gamer_v1_g1_reconstruction_reliability_0078',
        'nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076',
        'nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075',
        'nanojev_gamer_v1_g1_structural_learning_bakeoff_0074',
        'nanojev_gamer_v1_g1_learned_interface_bakeoff_0073',
        'nanojev_gamer_v1_graph_curriculum',
        'nanojev_gamer_v1_g1_certification_audit_0080')}
    input_hashes['G1_0081_runner']=digest(__file__)
    record={'schema':SCHEMA,'checkpoint_kind':'COMPOSITE_G1_HEAD_PLUS_TRAINED_INFERENCE_INTERFACE',
            'status':'FROZEN_RESEARCH_CHECKPOINT_NOT_NATIVE_G1_CERTIFICATE',
            'selected_arm':selected,'head_sha256':head_sha,
            'source_adapter_sha256':source_sha,
            'candidate_prior':candidate_prior,
            'structured_reference_weights':structured_reference_weights,
            'extra_candidate_budget':64,
            'upstream_report_sha256':upstream_hashes,
            'frozen_head_file':'gamer_graph_head.safetensors',
            'frozen_head_manifest':'head-manifest.json',
            'coefficient_bundles':bundles,
            'source_code_sha256':input_hashes,
            'source_prospective_audit_sha256':digest(audit_report),
            'selected_not_retrained':True,
            'model_weights_mutated':False,
            'production_promotion':False,'g2_unlocked':False}
    output.mkdir(parents=True,exist_ok=False)
    json_write(output/'composite-interface.json',record)
    shutil.copyfile(Path(checkpoint)/'gamer_graph_head.safetensors',output/'gamer_graph_head.safetensors')
    shutil.copyfile(Path(checkpoint)/'manifest.json',output/'head-manifest.json')
    require(digest(output/'gamer_graph_head.safetensors')==head_sha,'EXPORTED_HEAD_DRIFT')
    require(evidence.read_json(output/'head-manifest.json').get('head_sha256')==head_sha,
            'EXPORTED_HEAD_MANIFEST')
    return record


def _head_variant(head, mode):
    """Match the native curriculum's two G1 ablations without altering weights."""
    import torch
    class Variant(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.head=head
        def forward(self,hidden,**kwargs):
            h=torch.zeros_like(hidden) if mode=='zero_hidden' else hidden
            result=self.head(h,**kwargs)
            if mode=='stage_component':
                result=dict(result)
                result['span_logits']=torch.zeros_like(result['span_logits'])
                if 'spans' in result:result['spans']=[]
            return result
    require(mode in ('zero_hidden','stage_component'),'UNKNOWN_ABLATION')
    return Variant().eval()


def evaluate_current_interface(*,rows_by_split,head,model,tok,device,weights,priors,
                               initial,continued,trained,arm,limit=0,max_length=2048):
    """Use prompt-only capture. Gold labels are read only after all predictions."""
    from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
    from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as quantity
    reports={}
    for split,rows in rows_by_split.items():
        blind=[{'prompt':r['prompt'],'family':r['family'],'object_spans':[]} for r in rows]
        captures={}
        for mode in ('full','zero_hidden','stage_component'):
            component=head if mode=='full' else _head_variant(head,mode)
            captured=coverage._capture(blind,{'cutover':component},model,tok,
                device=device,max_length=max_length,
                prior_selected=priors,reference_weights=weights,
                split=split+'_'+mode,budget=coverage.MAX_OPTIONS)
            require(len(captured['cutover'])==len(rows),'CAPTURE_COUNT_'+mode)
            captures[mode]=captured['cutover']
        cases=[];paired=[]
        for index,row in enumerate(rows):
            predictions={}
            for mode in captures:
                data=captures[mode][index]
                require(not data.added.case.gold and not data.base.case.gold,
                        'GOLD_IN_INFERENCE_'+mode)
                predictions[mode]=quantity._predict(data,arm,weights['cutover'],initial,
                                                   continued,trained)
            gold={(s['start'],s['end'],s['kind']) for s in row['object_spans']}
            cases.append((predictions['full'],gold,row['family']))
            paired.append({'index':index+1,'family':row['family'],
                'prompt_sha256':hashlib.sha256(row['prompt'].encode('utf8')).hexdigest(),
                'gold':[list(x) for x in sorted(gold)],
                'selected':[list(x) for x in sorted(predictions['full'])],
                'zero_hidden_exact':predictions['zero_hidden']==gold,
                'stage_component_exact':predictions['stage_component']==gold})
        m=evidence.metrics(cases)
        head_correct=[v['selected']==v['gold'] for v in paired]
        n=len(paired) if not limit else min(limit,len(paired))
        require(n>=8,'ABLATION_CASE_COUNT')
        for key,field in [('zero_hidden_ablation','zero_hidden_exact'),
                          ('stage_component_ablation','stage_component_exact')]:
            head_n=sum(head_correct[:n]);other_n=sum(v[field] for v in paired[:n])
            m[key]={'cases':n,'correct':other_n,'head_first_n_correct':head_n,
                    'net_gain':(head_n-other_n)/n}
        m['object_micro_f1']=m['f1']
        r={'schema':SCHEMA,'stage':'G1','split':split,'cases':len(rows),
            'limited':False,'test_split_used':False,'generated_tokens':0,
            'checkpoint_stage':'G1','metrics':m,
            'interface_predictions_from_gold':False,
            'evaluation_split_previously_examined':True,
            'ablation_matched_indices':True,
            'ablation_scope':'all rows' if not limit else 'first '+str(n)}
        reports[split]=(r,paired)
        print('G1_0081_EVALUATED '+json.dumps({'split':split,
            'exact':m['exact_prompts'],'cases':len(rows),
            'zero_hidden_gain':round(m['zero_hidden_ablation']['net_gain'],6),
            'stage_component_gain':round(m['stage_component_ablation']['net_gain'],6)},
            sort_keys=True),flush=True)
    return reports


def run(*,root='../runtime/reports/gamer-v1',output=None,device='cuda',
        max_length=2048,ablation_limit=0):
    """One-command replay+causal ablation+composite export+fail-closed gate."""
    from tools import nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079 as quantity
    from tools import nanojev_gamer_v1_g1_sealed_generalization_0077 as sealed
    from tools import nanojev_gamer_v1_g1_candidate_coverage_bakeoff_0075 as coverage
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_backbone_first as backbone
    paths=relative_defaults(root)
    if output is not None:paths['output']=Path(output)
    target=paths['output'].resolve()
    require(not target.exists(),'IMMUTABLE_OUTPUT_EXISTS')
    protected=[v.resolve() for k,v in paths.items() if k not in ('output',)]
    require(all(target!=p and target not in p.parents and p not in target.parents
                for p in protected),'OUTPUT_OVERLAPS_EVIDENCE')
    require(device in ('cpu','cuda') and max_length>0 and ablation_limit>=0,'CONFIG')
    refs={k:paths[k] for k in (
        'seal','predictions','scored','seal_0077','reliability_reference',
        'discrimination_reference','dataset','legacy','cutover','cutover_certificate')}
    manifest,blind,scored,gate,before=evidence._read_frozen_artifacts(**refs)
    audit_path=paths['audit_0080']/'report.json'
    require(audit_path.is_file(),'MISSING_0080_AUDIT')
    audit=evidence.read_json(audit_path)
    validate_audit(audit,scored,gate)
    independently_verify_prospective(paths,audit)
    require(blind['selected']['cutover']==SELECTED_CUTOVER,'UNEXPECTED_SELECTED_INTERFACE')
    # Verify the complete checkpoint, Qwen/LoRA source, candidate-lattice priors,
    # and exact G1-0076/0078 initialization before any model execution.
    oldseal,oldmanifest,_=sealed._load_seal(paths['seal_0077'])
    discr=evidence.read_json(paths['discrimination_reference']/'report.json')
    entries,weights,priors=sealed._verify_frozen_lineage(
        record=discr,seal=oldmanifest,dataset=paths['dataset'],
        legacy=paths['legacy'],cutover=paths['cutover'],
        structural_reference=paths['structural_reference'],
        learned_reference=paths['learned_reference'],
        coverage_reference=paths['coverage_reference'],
        discrimination_reference=paths['discrimination_reference'],output=target)
    reliability=evidence.read_json(paths['reliability_reference']/'report.json')
    initial,continued=quantity._models(reliability,discr,'cutover')
    trained={k:quantity._load_model(v) for k,v in
             blind['trained_coefficients']['cutover'].items()}
    rows=cur.load_rows(paths['dataset'])
    phase={k:[r for r in rows if r['split']==k] for k in SPLITS}
    require(all(phase.values()) and len(phase['dev'])>=20 and
            len(phase['challenge'])>=12,'MISSING_DECLARED_SPLITS')
    head=cur._load_checkpoint(paths['cutover'],device=device)[0].eval()
    m=entries['cutover']['manifest']
    require(not m.get('train_limit'),'SUBSET_SMOKE_HEAD_NOT_CERTIFIABLE')
    model,tok=backbone.model_components(m['model_id'],m['source_adapter'],device)
    trials=evaluate_current_interface(rows_by_split=phase,head=head,model=model,tok=tok,
        device=device,weights=weights,priors=priors,
        initial=initial,continued=continued,trained=trained,
        arm=SELECTED_CUTOVER,limit=ablation_limit,max_length=max_length)
    failures=gate_reasons({s:trials[s][0] for s in SPLITS},gate)
    # The numeric developmental gate and native G2-ready certification are
    # separate facts. No data-dependent shortcut can create the latter.
    numeric_passed=not failures
    technical_blockers=[] if numeric_passed else failures[:]
    technical_blockers.append('composite_inference_not_integrated_into_native_G1_G2_graph_runtime')
    technical_blockers.append('original_dev_and_challenge_cohorts_previously_exposed_to_research')
    # Every source must be unchanged during scoring, before writing anything.
    for src,d in before.items():require(digest(src)==d,'INPUT_MUTATION')
    # Write a complete immutable result or nothing. No half-written certificate
    # is allowed to occupy the final output after a crash.
    require(target.parent.is_dir(),'OUTPUT_PARENT_MISSING')
    stage=Path(tempfile.mkdtemp(prefix='.g1_0081_staging_',dir=target.parent))
    try:
        for split in SPLITS:
            dest=stage/'evaluations'/split
            dest.mkdir(parents=True,exist_ok=False)
            json_write(dest/'report.json',trials[split][0])
            with (dest/'cases.jsonl').open('x',encoding='utf8') as f:
                for item in trials[split][1]:
                    f.write(json.dumps(item,sort_keys=True)+'\n')
        bundle_dir=stage/'checkpoint'
        checkpoint_record(blind,reliability,discr,SELECTED_CUTOVER,
            paths['cutover'],audit_path,m['head_sha256'],m['source_adapter_sha256'],
            bundle_dir,candidate_prior=priors['cutover'],
            structured_reference_weights=weights['cutover'],
            upstream_hashes={'G1_0076':digest(paths['discrimination_reference']/'report.json'),
                             'G1_0078':digest(paths['reliability_reference']/'report.json'),
                             'G1_0079':digest(paths['predictions']/'report.json')})
        cert={'schema':SCHEMA,'status':('COMPOSITE_DEVELOPMENT_GATE_PASSED_NOT_NATIVE'
                  if numeric_passed else 'COMPOSITE_DEVELOPMENT_GATE_BLOCKED'),
              'selected_arm':SELECTED_CUTOVER,'stage':'G1',
              'numeric_development_gate_passed':numeric_passed,
              'native_G1_checkpoint_certified':False,'next_stage':None,
              'g1_certified':False,'g2_unlocked':False,'production_promotion':False,
              'scope':'Dev/challenge replay of frozen learned composite; not native G2-consuming certificate',
              'dev_and_challenge_are_blind':False,
              'source_gate_requirements':gate,'failures':technical_blockers,
              'numeric_gate_failures':failures,
              'head_sha256':m['head_sha256'],'dataset_sha256':digest(paths['dataset']),
              'source_adapter_sha256':m['source_adapter_sha256'],
              'coefficient_checkpoint_sha256':digest(bundle_dir/'composite-interface.json'),
              'audit_0080_report_sha256':digest(audit_path),
              'precommitted_prospective_exact':audit['models']['cutover']['selected']['exact_prompts'],
              'reports_sha256':{s:digest(stage/'evaluations'/s/'report.json') for s in SPLITS}}
        json_write(stage/'certificate.json',cert)
        json_write(stage/'input-integrity.json',{'status':'PASS','sha256':before})
        for src,d in before.items():require(digest(src)==d,'INPUT_MUTATION_AFTER_EXPORT')
        require(not target.exists(),'OUTPUT_BECAME_OCCUPIED')
        stage.rename(target)
    except BaseException:
        if stage.exists():shutil.rmtree(stage,ignore_errors=True)
        raise
    print('G1_0081_RESULT '+json.dumps({'status':cert['status'],
          'numeric_development_gate_passed':numeric_passed,
          'dev_exact':trials['dev'][0]['metrics']['exact_prompts'],
          'dev_cases':trials['dev'][0]['cases'],
          'challenge_exact':trials['challenge'][0]['metrics']['exact_prompts'],
          'challenge_cases':trials['challenge'][0]['cases'],
          'failures':technical_blockers,'g2_unlocked':False},sort_keys=True),flush=True)
    return cert


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='../runtime/reports/gamer-v1')
    parser.add_argument('--output',default=None)
    parser.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    parser.add_argument('--max-length',type=int,default=2048)
    parser.add_argument('--ablation-limit',type=int,default=0,
                        help='0 evaluates every case under both ablations; >=8 for diagnostics')
    return run(**vars(parser.parse_args(argv)))

if __name__=='__main__':main()
