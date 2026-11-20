"""Independent, adversarial G1-0080 evidence and scoring checks; CPU/stdlib."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_g1_certification_audit_0080 as sut


def write_json(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf8')


def write_rows(p,value):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in value),encoding='utf8')


def old_metric(pairs):
    # The fake upstream metric uses the previous experiment's output schema.
    m=sut.metrics(pairs)
    return {key:m[key] for key in ('cases','exact_prompts','object_exact_rate',
              'precision','recall','f1','correct','predicted','expected',
              'incorrect_objects','false_positive_objects','by_kind')}


def build_fake_run(tmp_path):
    p=tmp_path
    for name in ('seal','predictions','scored','oldseal','reliability','discrimination',
                 'cutover','legacy'):
        (p/name).mkdir()
    (p/'dataset.jsonl').write_text('{"prompt":"pretend"}\n',encoding='utf8')
    dsha=sut.sha(p/'dataset.jsonl')
    source=Path(sut.__file__).with_name('nanojev_gamer_v1_g1_quantity_preserving_bakeoff_0079.py')
    generator=Path(sut.__file__).with_name('nanojev_gamer_v1_graph_corpus.py')
    older_runner=Path(sut.__file__).with_name('nanojev_gamer_v1_g1_sealed_generalization_0077.py')
    previous_prompt='previous-sealed-example'
    write_rows(p/'oldseal'/'prompts.jsonl',[{'case_id':'old1',
        'prompt':previous_prompt,'prompt_sha256':sut.hash_text(previous_prompt)}])
    head_hash={}
    for name in ('legacy','cutover'):
        (p/name/'gamer_graph_head.safetensors').write_bytes(name.encode('utf8'))
        head_hash[name]=sut.sha(p/name/'gamer_graph_head.safetensors')
        write_json(p/name/'manifest.json',{'stage':'G1','status':'TRAINED_NOT_CERTIFIED',
            'head_sha256':head_hash[name],'dataset_sha256':dsha,
            'source_adapter_sha256':'a'*64})
    write_json(p/'oldseal'/'manifest.json',{
        'frozen_report_sha256':'', 'checkpoint_sha256':head_hash,
        'original_dataset_sha256':dsha, 'source_adapter_sha256':'a'*64,
        'generator_sha256':sut.sha(generator),
        'sealed_runner_sha256':sut.sha(older_runner),
        'prompts_sha256':sut.sha(p/'oldseal'/'prompts.jsonl')})
    write_json(p/'discrimination'/'report.json',{
        'status':'EXPLORATORY_TRAINABLE_NOT_CERTIFIED',
        'lineage':{'checkpoint_sha256':head_hash}})
    disc_hash=sut.sha(p/'discrimination'/'report.json')
    oldseal=sut.read_json(p/'oldseal'/'manifest.json')
    oldseal['frozen_report_sha256']=disc_hash
    write_json(p/'oldseal'/'manifest.json',oldseal)
    write_json(p/'reliability'/'report.json',{'status':'RESEARCH_COMPARISON_NOT_CERTIFIED'})
    write_rows(p/'reliability'/'cases.jsonl',[{'case_id':0,
        'prompt_sha256':sut.hash_text('previous-reliability-example')}])
    write_json(p/'certificate.json',{'status':'DEVELOPMENT_GATE_BLOCKED',
        'certified_stage':'G1','next_stage':None,'head_sha256':head_hash['cutover']})
    cohorts={};audits=[];prompts=[];answers=[]
    for phase,n in [('train',96),('selection',32),('audit',128)]:
        cohort=[]
        for i in range(n):
            prompt=f'{phase}-prompt-{i:04d}-actor-action-quantity'
            family='encounter' if i%2==0 else 'allocation'
            value={'prompt':prompt,'family':family,'prompt_sha256':sut.hash_text(prompt)}
            if phase=='audit':
                value['case_id']=f'prospective-{i:05d}'
                prompts.append(value)
                answers.append({'case_id':value['case_id'],'family':family,
                                'prompt_sha256':value['prompt_sha256'],
                                'gold':[[0,5,'actor'],[6,12,'action'],[13,21,'quantity']]})
            else:
                value['object_spans']=[]
            cohort.append(value)
        cohorts[phase]=cohort
    write_rows(p/'seal'/'train.jsonl',cohorts['train'])
    write_rows(p/'seal'/'selection.jsonl',cohorts['selection'])
    write_rows(p/'seal'/'audit.prompts.jsonl',prompts)
    write_rows(p/'seal'/'audit.answers.SECRET.jsonl',answers)
    manifest={'schema':sut.SOURCE_SCHEMA,'status':'AUDIT_COMMITTED_NOT_PREDICTED',
        'audit_answers_used_in_fit_or_selection':False,'evaluation_is_certification':False,
        'cohorts':{'train':96,'selection':32,'audit':128},
        'runner_sha256':sut.sha(source),'generator_sha256':sut.sha(generator),
        'original_dataset_sha256':dsha,
        'seal_0077_manifest_sha256':sut.sha(p/'oldseal'/'manifest.json'),
        'reliability_report_sha256':sut.sha(p/'reliability'/'report.json'),
        'reliability_cases_sha256':sut.sha(p/'reliability'/'cases.jsonl'),
        'discrimination_report_sha256':disc_hash,
        'train_sha256':sut.sha(p/'seal'/'train.jsonl'),
        'selection_sha256':sut.sha(p/'seal'/'selection.jsonl'),
        'audit_prompts_sha256':sut.sha(p/'seal'/'audit.prompts.jsonl'),
        'committed_audit_answers_sha256':sut.sha(p/'seal'/'audit.answers.SECRET.jsonl'),
        'old_0077_0078_original_prompt_overlap':0,
        'prompt_hashes_sha256':sut.hash_text(''.join(
            r['prompt_sha256'] for phase in ('train','selection','audit')
            for r in cohorts[phase]))}
    write_json(p/'seal'/'manifest.json',manifest)
    predictions=[];scored_rows=[];model_pairs={n:[] for n in sut.EXPECTED_MODELS}
    for model in sut.EXPECTED_MODELS:
        for i,prompt in enumerate(prompts):
            expected=set(tuple(s) for s in answers[i]['gold'])
            ctrl=expected if i<100 else {tuple(answers[i]['gold'][0]),tuple(answers[i]['gold'][2])}
            # Exact control=100/128; selected=106/128; quantity always identical.
            selected=expected if i<106 else ctrl
            if i in (121,122,123):
                selected={tuple(answers[i]['gold'][0]),tuple(answers[i]['gold'][2])}
            predictions.append({'model':model,'case_id':prompt['case_id'],
                'prompt_sha256':prompt['prompt_sha256'],'family':prompt['family'],
                'control':[list(x) for x in sorted(ctrl)],
                'selected':[list(x) for x in sorted(selected)]})
            model_pairs[model].append((ctrl,selected,expected,prompt['family']))
            scored_rows.append({'model':model,'case_id':prompt['case_id'],
                'control_exact':ctrl==expected,'selected_exact':selected==expected,
                'recovered_correct':[list(x) for x in sorted((selected&expected)-(ctrl&expected))],
                'lost_correct':[list(x) for x in sorted((ctrl&expected)-(selected&expected))],
                'new_false':[list(x) for x in sorted((selected-expected)-(ctrl-expected))],
                'removed_false':[list(x) for x in sorted((ctrl-expected)-(selected-expected))]})
    write_rows(p/'predictions'/'audit.predictions.jsonl',predictions)
    write_rows(p/'scored'/'cases.jsonl',scored_rows)
    dim, hidden = sut._canonical_shape()
    coef={'hidden':[[0.0]*hidden for _ in range(dim)],'proj':[0.0]*hidden,
          'type_offset':[0.0]*len(sut.ALL_KINDS),'new_offset':[0.0]}
    bundle={'parameters':coef,'sha256':sut.object_hash(coef)}
    trained={n:{arm:copy.deepcopy(bundle) for arm in (
             'pinned_structured_1x','pinned_quantity_margin_2x',
             'pinned_quantity_margin_4x')} for n in sut.EXPECTED_MODELS}
    selected={'cutover':'pinned_historical_continued_64',
              'legacy':'pinned_quantity_margin_4x'}
    selection_metrics={}
    for name,arm in selected.items():
        base={'exact_prompts':20,'f1':.95,'precision':.97,
              'by_kind':{k:{'recall':1.} for k in sut.SELECTION_KINDS}}
        other={**base,'exact_prompts':21,'f1':.96,'precision':.98}
        selection_metrics[name]={a:copy.deepcopy(other if a==arm else base)
                                 for a in sut.ARMS}
    blind={'schema':sut.SOURCE_SCHEMA,'status':'AUDIT_BLIND_PREDICTIONS_LOCKED_NOT_SCORED',
           'audit_answers_opened_during_fit':False,'audit_gold_used_for_selection_or_inference':False,
           'head_weights_modified':False,'g1_certified':False,'g2_unlocked':False,
           'production_promotion':False,'new_audit_is_not_certification':True,
           'committed_audit_answers_sha256':manifest['committed_audit_answers_sha256'],
           'audit_prompts_sha256':manifest['audit_prompts_sha256'],
           'seal_manifest_sha256':sut.sha(p/'seal'/'manifest.json'),
           'prediction_sha256':sut.sha(p/'predictions'/'audit.predictions.jsonl'),
           'selected':selected,'selection_metrics':selection_metrics,
           'trained_coefficients':trained,
           'quantity_guard_guarantee':'retains frozen quantity spans',
           'historical_unprotected_continued_is_diagnostic_only':True}
    write_json(p/'predictions'/'report.json',blind)
    scored={'schema':sut.SOURCE_SCHEMA,'status':'PROSPECTIVE_AUDIT_SCORED_ONCE_NO_PROMOTION',
            'audit_cases':128,'selected':selected,'metrics':{},
            'precommitted_answers_sha256':manifest['committed_audit_answers_sha256'],
            'selection_report_sha256':sut.sha(p/'predictions'/'report.json'),
            'prediction_sha256':blind['prediction_sha256'],
            'audit_used_for_fit_or_model_selection':False,
            'audit_same_generator_distribution':True,'certification_authorized':False,
            'g1_certified':False,'g2_unlocked':False,'production_promotion':False}
    for name,pairs in model_pairs.items():
        scored['metrics'][name]={
            'control':old_metric([(a,g,f) for a,b,g,f in pairs]),
            'selected':old_metric([(b,g,f) for a,b,g,f in pairs])}
    write_json(p/'scored'/'report.json',scored)
    write_json(p/'seal'/'UNSEALED.json',{'status':'SCORED_ONCE',
        'scored_report_sha256':sut.sha(p/'scored'/'report.json'),
        'prediction_sha256':blind['prediction_sha256']})
    args={'seal':p/'seal','predictions':p/'predictions','scored':p/'scored',
          'seal_0077':p/'oldseal','reliability_reference':p/'reliability',
          'discrimination_reference':p/'discrimination','dataset':p/'dataset.jsonl',
          'legacy':p/'legacy','cutover':p/'cutover',
          'cutover_certificate':p/'certificate.json','output':p/'audit'}
    return args


def test_independent_scoring_and_formal_gate_separation(tmp_path):
    args=build_fake_run(tmp_path)
    result=sut.audit(**args)
    assert result['status']=='READ_ONLY_EVIDENCE_VERIFIED_NOT_CERTIFIED'
    assert result['models']['cutover']['selected']['exact_prompts']==106
    assert result['models']['cutover']['control']['exact_prompts']==100
    assert result['models']['cutover']['selected']['by_kind']['quantity']['recall']==1
    assert result['prospective_gate_observations']['cutover']['exact_rate_at_least_dev_threshold']
    assert result['formal_g1_advancement_gate']=='NOT_SATISFIED_BY_THIS_EVIDENCE'
    assert result['g1_certified'] is False and result['g2_unlocked'] is False
    assert result['paired_nonregression']['cutover']['exact_cases_gained']==6
    assert (args['output']/'report.json').exists()
    with pytest.raises(ValueError,match='IMMUTABLE_OUTPUT_EXISTS'):
        sut.audit(**args)


def test_changed_prediction_bytes_rejected_without_output(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['predictions']/'audit.predictions.jsonl'
    f.write_text(f.read_text()+'\n{}\n',encoding='utf8')
    with pytest.raises(ValueError,match='PREDICTIONS_DRIFT'):
        sut.audit(**args)
    assert not args['output'].exists()


def test_changed_source_coefficient_hash_rejected(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['predictions']/'report.json'
    p=sut.read_json(f)
    p['trained_coefficients']['cutover']['pinned_structured_1x']['parameters']['proj'][0]=3.
    write_json(f,p)
    # Even when linked scored hash and marker are updated, the bundle hash catches it.
    sr=args['scored']/'report.json'
    report=sut.read_json(sr);report['selection_report_sha256']=sut.sha(f)
    write_json(sr,report)
    mark=args['seal']/'UNSEALED.json';m=sut.read_json(mark)
    m['scored_report_sha256']=sut.sha(sr);write_json(mark,m)
    with pytest.raises(ValueError,match='pinned_structured_1x_HASH'):
        sut.audit(**args)


def test_quantity_swap_rejected_even_if_commitments_rehashed(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['predictions']/'audit.predictions.jsonl';rows=sut.read_jsonl(f)
    rows[0]['selected']=[x for x in rows[0]['selected'] if x[2]!='quantity']
    write_rows(f,rows)
    blind=args['predictions']/'report.json';br=sut.read_json(blind)
    br['prediction_sha256']=sut.sha(f);write_json(blind,br)
    sr=args['scored']/'report.json';sc=sut.read_json(sr)
    sc['prediction_sha256']=br['prediction_sha256']
    sc['selection_report_sha256']=sut.sha(blind);write_json(sr,sc)
    mark=args['seal']/'UNSEALED.json';m=sut.read_json(mark)
    m['prediction_sha256']=br['prediction_sha256']
    m['scored_report_sha256']=sut.sha(sr);write_json(mark,m)
    with pytest.raises(ValueError,match='QUANTITY_NOT_PRESERVED'):
        sut.audit(**args)


def test_gold_source_corruption_rejected(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['seal']/'audit.answers.SECRET.jsonl'
    rows=sut.read_jsonl(f);rows[0]['gold']=[[0,5,'wrong-type']]
    write_rows(f,rows)
    with pytest.raises(ValueError,match='ANSWERS_DRIFT'):
        sut.audit(**args)


def test_score_claim_not_trusted_if_metrics_wrong(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['scored']/'report.json'
    r=sut.read_json(f);r['metrics']['cutover']['selected']['f1']=1.
    write_json(f,r)
    marker=args['seal']/'UNSEALED.json';m=sut.read_json(marker)
    m['scored_report_sha256']=sut.sha(f);write_json(marker,m)
    with pytest.raises(ValueError,match='METRIC_F1'):
        sut.audit(**args)


def test_duplicate_prediction_identity_detected(tmp_path):
    args=build_fake_run(tmp_path)
    f=args['predictions']/'audit.predictions.jsonl';rows=sut.read_jsonl(f)
    rows[1]['case_id']=rows[0]['case_id']
    rows[1]['prompt_sha256']=rows[0]['prompt_sha256']
    rows[1]['family']=rows[0]['family']
    write_rows(f,rows)
    blind=args['predictions']/'report.json';b=sut.read_json(blind)
    b['prediction_sha256']=sut.sha(f);write_json(blind,b)
    sf=args['scored']/'report.json';s=sut.read_json(sf)
    s['prediction_sha256']=b['prediction_sha256']
    s['selection_report_sha256']=sut.sha(blind);write_json(sf,s)
    mark=args['seal']/'UNSEALED.json';m=sut.read_json(mark)
    m['prediction_sha256']=b['prediction_sha256'];m['scored_report_sha256']=sut.sha(sf)
    write_json(mark,m)
    with pytest.raises(ValueError,match='PREDICTION_IDENTITY'):
        sut.audit(**args)


def test_no_output_inside_a_checkpoint_or_a_seal(tmp_path):
    args=build_fake_run(tmp_path)
    for parent in (args['cutover'],args['seal'],args['scored']):
        with pytest.raises(ValueError,match='OUTPUT_INSIDE_PROTECTED_EVIDENCE'):
            sut.audit(**{**args,'output':parent/'intrusive-audit'})


def test_f1_and_macro_f1_independently_derived():
    gold={(0,1,'actor'),(1,2,'action'),(2,3,'quantity')}
    pred={(0,1,'actor'),(1,2,'action'),(2,3,'actor')}
    m=sut.metrics([(pred,gold,'encounter')])
    assert m['correct']==2 and m['false_positive_objects']==1
    assert m['f1']==pytest.approx(2/3)
    assert m['object_macro_f1']==pytest.approx(((2/3)+1+0)/3)


def test_original_gate_is_loaded_from_source():
    p=Path(sut.__file__).with_name('nanojev_gamer_v1_graph_curriculum.py')
    gate=sut.frozen_gate_requirements(p)
    assert gate=={'metric':'object_exact_rate','dev':.80,'challenge':.70,
                  'macro_f1':.90,'kind_recall':.82,'neural_gain':.08}


def test_discriminator_shape_matches_real_g1_0076_export():
    """G1-0076/0079 serialize a 58x40 hidden matrix, not 37x40.

    G1-0075 concatenates 18 proposal + 32 structured + 1 admission +
    7 semantic-kind features. Never substitute G1-0073's 11 features.
    """
    assert sut._canonical_shape() == (58, 40)
    params = {'hidden': [[0.0] * 40 for _ in range(58)],
              'proj': [0.0] * 40,
              'type_offset': [0.0] * 7,
              'new_offset': [0.0]}
    sut.bundle_integrity({'parameters': params,
                          'sha256': sut.object_hash(params)}, 'REAL_SHAPE')
    too_short = copy.deepcopy(params)
    too_short['hidden'].pop()
    with pytest.raises(ValueError, match='REAL_SHAPE_SHAPE'):
        sut.bundle_integrity({'parameters': too_short,
                              'sha256': sut.object_hash(too_short)}, 'REAL_SHAPE')


def test_recomputed_summary_matches_original_0079_summarizer():
    """Cross-check scores against predecessor logic without using it in auditor."""
    from tools.nanojev_gamer_v1_g1_oracle_decoder_cutover_frob import _summarize
    examples=[
        ({(0,2,'actor'),(2,3,'action')},
         {(0,2,'actor'),(2,3,'action'),(3,4,'quantity')},'encounter'),
        ({(0,2,'actor'),(3,4,'quantity')},
         {(0,2,'actor'),(3,4,'quantity')},'allocation'),
        ({(0,1,'outcome')},
         {(0,1,'outcome'),(2,4,'condition')},'encounter'),
    ]
    ours=sut.metrics(examples)
    predecessor=_summarize([(p,g) for p,g,_ in examples])
    sut.compare_score(ours,predecessor,'PARITY')
    assert ours['object_macro_f1']<=1


def test_quantity_nonregression_selection_uses_all_mandated_kinds():
    metrics={}
    baseline={'exact_prompts':20,'f1':.95,'precision':.95,
              'by_kind':{k:{'recall':1.} for k in sut.SELECTION_KINDS}}
    for arm in sut.ARMS:metrics[arm]=copy.deepcopy(baseline)
    candidate='pinned_quantity_margin_4x'
    metrics[candidate]['exact_prompts']=25
    metrics[candidate]['by_kind']['quantity']['recall']=.99
    assert sut.selection_replay(metrics)==sut.CONTROL_ARM
    metrics[candidate]['by_kind']['quantity']['recall']=1.
    assert sut.selection_replay(metrics)==candidate
