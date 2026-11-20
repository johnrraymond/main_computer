"""G1-0082 regression: native injection, sealed scoring, and strict lineage."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest
import torch

from tools.nanojev_gamer_v1_graph_head import GamerGraphHead, token_span_char_bounds, decode_graph
from tools import nanojev_gamer_v1_g1_native_composite_0082 as native
from tools import nanojev_gamer_v1_g1_native_certification_0082 as completion
from tools import nanojev_gamer_v1_g1_certification_audit_0080 as evidence


def _prep(prompt='Alice pays Bob.'):
    from tools.nanojev_gamer_v1_graph_contract import SPAN_KINDS
    offsets=[(0,5),(6,10),(11,14),(14,15)]
    return {'prompt':prompt,'offsets':offsets,'prompt_start':0,
            'eligible':torch.ones(4,dtype=torch.bool)}


def _head():
    torch.manual_seed(917)
    return GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,
                          memory_slots=2,max_operands=2,max_span_tokens=4).eval()


def test_native_g2_uses_explicit_composite_objects_not_neural_default():
    head=_head()
    prepared=_prep()
    hidden=torch.linspace(-1.,1.,64).reshape(4,16)
    spans=[{'kind':'actor','token_first':0,'token_last':0},
           {'kind':'action','token_first':1,'token_last':1},
           {'kind':'actor','token_first':2,'token_last':2}]
    with torch.no_grad():
        observed=head(hidden,eligible=prepared['eligible'],active_stage='G2',
                      inference_spans=spans)
    assert observed['spans']==spans
    assert observed['surface_count']==3
    assert observed['binding_logits'].shape[0]>=3
    graph,surfaces=decode_graph(observed,prepared,unlocked='G2')
    assert {(x['span'][0],x['span'][1],x['kind']) for x in graph['nodes']}=={
        (0,5,'actor'),(6,10,'action'),(11,14,'actor')}
    assert len(surfaces)==3


@pytest.mark.parametrize('bad',[
    [{'kind':'actor','token_first':2,'token_last':2},
     {'kind':'action','token_first':1,'token_last':1}],
    [{'kind':'actor','token_first':0,'token_last':2},
     {'kind':'action','token_first':2,'token_last':2}],
    [{'kind':'bogus','token_first':0,'token_last':0}],
    [{'kind':'actor','token_first':-1,'token_last':0}],
    [{'kind':'actor','token_first':0,'token_last':999}],
    [{'kind':'actor','token_first':0,'token_last':0,'gold':1}],
    [{'kind':'actor','token_first':True,'token_last':1}],
])
def test_native_g2_rejects_invalid_or_gold_injection(bad):
    head=_head()
    with pytest.raises(ValueError,match='GAMER_GRAPH_INFERENCE_SPANS_INVALID'):
        head(torch.randn(4,16),eligible=torch.ones(4,dtype=torch.bool),
             active_stage='G2',inference_spans=bad)


def test_native_g1_rejects_external_injection_and_teacher_mix():
    head=_head();span=[{'kind':'actor','token_first':0,'token_last':0}]
    with pytest.raises(ValueError,match='INFERENCE_SPANS_CONFLICT'):
        head(torch.randn(4,16),eligible=torch.ones(4,dtype=torch.bool),
             active_stage='G1',inference_spans=span)
    with pytest.raises(ValueError,match='INFERENCE_SPANS_CONFLICT'):
        head(torch.randn(4,16),eligible=torch.ones(4,dtype=torch.bool),
             active_stage='G2',training_spans=span,inference_spans=span)


def test_native_g2_eligibility_rejected():
    head=_head();mask=torch.ones(4,dtype=torch.bool);mask[1]=False
    with pytest.raises(ValueError,match='INFERENCE_SPANS_INVALID'):
        head(torch.randn(4,16),eligible=mask,active_stage='G2',
             inference_spans=[{'kind':'action','token_first':1,'token_last':1}])


def test_token_choice_round_trips_normalized_author_coordinates():
    data=SimpleNamespace(added=SimpleNamespace(case=SimpleNamespace(options=[
        SimpleNamespace(char_key=(0,5,'actor'),start=0,end=1,kind=1),
        SimpleNamespace(char_key=(6,10,'action'),start=1,end=2,kind=2)])))
    spans=native.NativeCompositeG1._tokens({(0,5,'actor'),(6,10,'action')},data,_prep())
    assert spans==[{'kind':'actor','token_first':0,'token_last':0},
                   {'kind':'action','token_first':1,'token_last':1}]


def test_token_choice_rejects_missing_and_conflicting_token_spans():
    opt=SimpleNamespace(char_key=(0,5,'actor'),start=0,end=1,kind=1)
    data=SimpleNamespace(added=SimpleNamespace(case=SimpleNamespace(options=[opt])))
    with pytest.raises(ValueError,match='UNMAPPABLE_CHAR_OBJECT'):
        native.NativeCompositeG1._tokens({(0,5,'actor'),(6,10,'action')},data,_prep())
    with pytest.raises(ValueError,match='OVERLAPPING_CHAR_OBJECTS'):
        native.NativeCompositeG1._tokens({(0,5,'actor'),(3,7,'action')},data,_prep())


def test_read_only_frozen_manifest_rejects_no_checkpoint(tmp_path):
    with pytest.raises(FileNotFoundError):
        native.load_composite(tmp_path/'missing')


def test_new_seal_rejects_malformed_commit(tmp_path):
    seal=tmp_path/'seal';seal.mkdir()
    completion.write_json(seal/'commit.json',{'schema':completion.SCHEMA,
                  'status':'ANSWERS_COMMITTED_NOT_PREDICTED','cases':160,
                  'dev_cases':96,'challenge_cases':64})
    with pytest.raises((ValueError,FileNotFoundError,KeyError)):
        completion._check_commit(seal,tmp_path,tmp_path,tmp_path/'x')


def test_independent_gate_scoring_uses_original_thresholds_and_ablations():
    case=[];answers=[]
    for split,n in [('dev',20),('challenge',12)]:
        for i in range(n):
            key=f'{split}-{i}'
            selected=[(0,5,'actor'),(6,10,'action'),(11,14,'quantity')]
            entry={'case_id':key,'split':split,'family':('encounter' if i%2 else 'allocation'),
                   'prompt_sha256':'f'*64,'predictions':{
                    'full':[list(x) for x in selected],
                    'zero_hidden':[], 'stage_component':[]}}
            case.append(entry)
            answers.append({'case_id':key,'split':split,'family':entry['family'],
                'prompt_sha256':'f'*64,'prompt_length':20,
                'gold':[list(x) for x in selected]})
    gate={'metric':'object_exact_rate','dev':.8,'challenge':.7,'macro_f1':.9,
          'kind_recall':.82,'neural_gain':.08}
    r=completion._score_metrics(case,answers,gate)
    assert not r['dev']['failures'] and not r['challenge']['failures']
    assert r['dev']['metrics']['exact_prompts']==20
    assert r['dev']['metrics']['zero_hidden_ablation']['net_gain']==1.
    case[0]['predictions']['full']=[]
    r=completion._score_metrics(case,answers,gate)
    assert r['dev']['metrics']['exact_prompts']==19


def test_scoring_detects_changed_case_labels():
    gate={'metric':'object_exact_rate','dev':.8,'challenge':.7,'macro_f1':.9,
          'kind_recall':.82,'neural_gain':.08}
    p=[{'case_id':'a','split':'dev','family':'encounter',
        'prompt_sha256':'a','predictions':{'full':[],'zero_hidden':[],'stage_component':[]}}]
    g=[{'case_id':'a','split':'dev','family':'encounter',
        'prompt_sha256':'b','prompt_length':10,'gold':[]}]
    with pytest.raises(ValueError,match='SCORING_CASE_BINDING'):
        completion._score_metrics(p,g,gate)


def test_immutable_design_gate_rejects_posthoc_seed_or_cohort_sizes(tmp_path):
    with pytest.raises(ValueError,match='PREDECLARED_SEALED_DESIGN_LOCKED'):
        completion.run(output=str(tmp_path/'x'),root=tmp_path,seed=11)


def test_native_g2_binding_supervision_uses_only_predicted_objects():
    from tools.nanojev_gamer_v1_graph_curriculum import composite_binding_labels, _pair_loss
    from tools.nanojev_gamer_v1_graph_head import BIND_CLASSES
    prep=_prep()
    spans=[{'kind':'actor','token_first':0,'token_last':0},
           {'kind':'action','token_first':1,'token_last':1}]
    row={'graph':{'nodes':[
        {'id':'a','kind':'actor','span':[0,5]},
        {'id':'action','kind':'action','span':[6,10]},
        {'id':'b','kind':'actor','span':[11,14]}],
        'edges':[{'src':'a','dst':'action','type':'owns'},
                 {'src':'action','dst':'b','type':'owns'}]}}
    ids=composite_binding_labels(prep,row,spans)
    assert ids=={'a':0,'action':1}
    # The absent 'b' has NO teacher-forced node index; its gold edge cannot be
    # misattributed to a different predicted object.
    logits=torch.randn(4,4,len(BIND_CLASSES),requires_grad=True)
    edges=[e for e in row['graph']['edges'] if e['type'] in BIND_CLASSES]
    loss=_pair_loss(logits,ids,edges,BIND_CLASSES)
    loss.backward()
    assert torch.isfinite(loss) and logits.grad is not None


def test_native_g2_rejects_ambiguous_predicted_node_identity():
    from tools.nanojev_gamer_v1_graph_curriculum import composite_binding_labels
    with pytest.raises(ValueError,match='COMPOSITE_DUPLICATE_OBJECT'):
        composite_binding_labels(_prep(),{'graph':{'nodes':[]}},[
            {'kind':'actor','token_first':0,'token_last':0},
            {'kind':'actor','token_first':0,'token_last':0}])


def test_prospective_native_certificate_is_recomputed_before_g2_training(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_g1_certification_audit_0080 as evidence
    root=tmp_path
    checkpoint=root/'checkpoint';checkpoint.mkdir()
    (checkpoint/'composite-interface.json').write_text('{}')
    completion_dir=root/'upstream';(completion_dir/'evaluations').mkdir(parents=True)
    (completion_dir/'certificate.json').write_text('{}')
    source=root/'source.jsonl';source.write_text('[]\n')
    population=[]
    for split,n in [('dev',96),('challenge',64)]:
        for i in range(n):
            prompt='Alice pays Bob 10'
            population.append({'case_id':f'{split}-{i}', 'split':split,
                'family':'encounter' if i%2 else 'allocation', 'prompt':prompt,
                'prompt_sha256':completion.sha_text(prompt),
                'gold':[[0,5,'actor'],[6,10,'action'],[11,14,'actor'],[15,17,'quantity']]})
    # Test the actual immutable commit->score boundary, without a GPU run.
    seal=root/'seal'
    oldcommit=completion._commit(seal,population,completion_dir,checkpoint,source)
    blind=root/'blind';blind.mkdir()
    predictions=[]
    for x in population:
        predictions.append({k:x[k] for k in ('case_id','split','family','prompt_sha256')} |
           {'predictions':{'full':x['gold'],'zero_hidden':[],
                           'stage_component':[]},'native_g2_surface_consumed':True})
    completion.write_jsonl(blind/'predictions.jsonl',predictions)
    historic=[{'prompt_sha256':str(i),'selected':[[0,5,'actor']],
               'native_g2_surface_count':1} for i in range(112)]
    completion.write_jsonl(blind/'historical-parity.jsonl',historic)
    completion.write_json(blind/'lock.json',{
        'schema':completion.SCHEMA,'status':'BLIND_NATIVE_PREDICTIONS_LOCKED',
        'historical_parity_count':112,
        'historical_parity_sha256':evidence.sha(blind/'historical-parity.jsonl'),
        'native_G2_head_consumed_composite_spans':True,
        'oracle_labels_read_during_inference':False,
        'prompts_sha256':oldcommit['prompts_sha256'],
        'composite_checkpoint_sha256':oldcommit['composite_checkpoint_sha256'],
        'predictions_sha256':evidence.sha(blind/'predictions.jsonl')})
    monkeypatch.setattr(completion,'_verify_0081',lambda *args,**kwargs:{})
    monkeypatch.setattr(completion,'_historical_cases',lambda *args,**kwargs:
                        iter([({}, {'prompt_sha256':r['prompt_sha256'],
                        'selected':r['selected']}) for r in historic]))
    result=completion.score(checkpoint=checkpoint,completion_dir=completion_dir,
        seal=seal,predictions=blind,dataset=source,output=root/'scored')
    assert result['g1_native_composite_certified'] is True
    assert result['g2_training_unlocked'] is True
    assert result['splits']['dev']['metrics']['exact_prompts']==96
    # Tampered after locking: a second scoring attempt must refuse the input.
    with (blind/'predictions.jsonl').open('a') as f:f.write('X')
    with pytest.raises(ValueError,match='LOCKED_NATIVE_PREDICTION_DRIFT'):
        completion.score(checkpoint=checkpoint,completion_dir=completion_dir,
            seal=seal,predictions=blind,dataset=source,output=root/'scored2')


def test_composite_checkpoint_roundtrip_into_native_g2_and_hash_drift(tmp_path):
    import json
    from safetensors.torch import save_file
    from tools import nanojev_deterministic_fabric as fabric
    from tools import nanojev_gamer_v1_g1_discrimination_trainability_bakeoff_0076 as discriminator
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    from tools import nanojev_gamer_v1_g1_certification_completion_0081 as prior
    h={'hidden_size':16,'width':16,'layers':1,'latent_slots':2,
       'memory_slots':2,'max_operands':2,'max_span_tokens':4,
       'oracle_coupling':True,'object_extractor':'joint-span-v1'}
    head=fabric.new_graph_head(**{k:v for k,v in h.items() if k!='object_extractor'})
    pristine=fabric.state_digest(head)
    save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
              str(tmp_path/'gamer_graph_head.safetensors'))
    head_hash=evidence.sha(tmp_path/'gamer_graph_head.safetensors')
    (tmp_path/'head-manifest.json').write_text(json.dumps({
        'schema':cur.SCHEMA,'status':'TRAINED_NOT_CERTIFIED','stage':'G1',
        'head_sha256':head_hash,'source_adapter_sha256':'TEST',
        'head':h,'deterministic_fabric':{'initial_state_sha256':pristine,
                                       'new_trainable_random_init':False}}))
    learned=discriminator._export_model(discriminator.Discriminator())
    record={'schema':prior.SCHEMA,
        'checkpoint_kind':'COMPOSITE_G1_HEAD_PLUS_TRAINED_INFERENCE_INTERFACE',
        'status':'FROZEN_RESEARCH_CHECKPOINT_NOT_NATIVE_G1_CERTIFICATE',
        'selected_arm':prior.SELECTED_CUTOVER,'extra_candidate_budget':64,
        'selected_not_retrained':True,'model_weights_mutated':False,
        'g2_unlocked':False,'head_sha256':head_hash,
        'source_adapter_sha256':'TEST',
        'coefficient_bundles':{
            'quantity_control_interface':learned,
            'historical_continued_interface':learned},
        'candidate_prior':'runnerup_joint_gap0p50_bonus0p50',
        'structured_reference_weights':[[0.]*32]}
    (tmp_path/'composite-interface.json').write_text(json.dumps(record))
    bridge=native.load_composite(tmp_path,validate_code=False)
    prep=_prep()
    for mode in ('full','zero_hidden','stage_component'):
        result=bridge.predict(torch.zeros(4,16),prep,family='encounter',mode=mode)
        assert result['native_objects']==result['objects']
        assert result['output']['surface_count']==len(result['objects'])
    record['head_sha256']='0'*64
    (tmp_path/'composite-interface.json').write_text(json.dumps(record))
    with pytest.raises(ValueError,match='HEAD_LINEAGE_INVALID'):
        native.load_composite(tmp_path,validate_code=False)


def test_g2_training_requires_explicit_certificate_and_composite_pair(tmp_path):
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    with pytest.raises(PermissionError,match='COMPOSITE_G1_G2_REQUIRES_CERTIFIED_PAIR'):
        cur.train(dataset=tmp_path/'missing',output=tmp_path/'out',
                  source_adapter=tmp_path/'adapter',stage='G2',
                  parent_checkpoint=tmp_path/'parent',native_composite_g1=tmp_path/'composite')
    with pytest.raises(PermissionError,match='COMPOSITE_G1_G2_REQUIRES_CERTIFIED_PAIR'):
        cur.train(dataset=tmp_path/'missing',output=tmp_path/'out',
                  source_adapter=tmp_path/'adapter',stage='G1',
                  native_composite_cert=tmp_path/'certificate.json')


def test_legacy_g2_training_without_native_flags_unchanged():
    from tools import nanojev_gamer_v1_graph_curriculum as cur
    import inspect
    param=inspect.signature(cur.train).parameters
    assert param['native_composite_g1'].default is None
    assert param['native_composite_cert'].default is None


def test_review_locked_curriculum_version_is_exact():
    assert native.audit.sha(Path(native.curriculum.__file__))==native.NATIVE_CURRICULUM_SHA256


def test_scoring_refuses_duplicate_predictions_even_if_count_matches():
    gate={'metric':'object_exact_rate','dev':.8,'challenge':.7,'macro_f1':.9,
          'kind_recall':.82,'neural_gain':.08}
    dup={'case_id':'one','split':'dev','family':'encounter',
         'prompt_sha256':'a','predictions':{'full':[],'zero_hidden':[],
                                          'stage_component':[]}}
    gold=[{'case_id':'one','split':'dev','family':'encounter',
           'prompt_sha256':'a','prompt_length':10,'gold':[]},
          {'case_id':'two','split':'dev','family':'allocation',
           'prompt_sha256':'b','prompt_length':10,'gold':[]}]
    with pytest.raises(ValueError,match='SCORING_COUNT_OR_DUPLICATE_ID'):
        completion._score_metrics([dict(dup),dict(dup)],gold,gate)
