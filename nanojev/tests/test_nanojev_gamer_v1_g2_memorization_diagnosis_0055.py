"""Actual set-predictor continuation, prediction-ledger and CLI integration."""
import copy
import gzip
import importlib.util
import json
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_memorization_diagnosis_0055 as m
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as b54
from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52


@pytest.fixture(autouse=True)
def threads():
    prev=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(prev)


def ref():
    source=Path(__file__).with_name('test_nanojev_gamer_v1_g2_literature_bakeoff_0055.py')
    spec=importlib.util.spec_from_file_location('g2_0055_diagnosis_test_reference',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def small_samples():
    source=ref()
    records,pairs=source.cohorts()
    # Keep original labels on non-training split so accidental leakage is observable.
    before={x['prompt_sha256']:copy.deepcopy(x['labels']) for x in records}
    chosen,picks=m.select_same_eight_training_graphs(records,pairs)
    return records,pairs,chosen,picks,before


def test_exact_0055_memorization_cohort_and_no_other_labels_read():
    records,pairs,chosen,picks,before=small_samples()
    assert len(chosen)==8 and len(picks)==4
    assert {x['section'] for x in chosen}=={'counterfactual_train'}
    for row in records:
        if row in chosen:continue
        assert row['labels']==before[row['prompt_sha256']]
        assert row['positive'].tolist()==[0,4]  # unchanged fixture-owned label
    # Independently reconstruct the original 0055 selection and compare hashes.
    original,pair_list=ref().cohorts()
    split,groups=b54.split_cohorts(original,pair_list)
    expected,_=p55.memorization_subset(split,groups)
    assert {x['prompt_sha256'] for x in chosen}=={x['prompt_sha256'] for x in expected}


def test_real_set_prediction_error_decomposition_contains_reproducible_edges():
    _,_,small,_,_=small_samples()
    torch.manual_seed(13)
    model=p55.TripleSetPredictor()
    row=m.diagnose_case(model,small[0],step=0)
    assert len(row['gold_matched_slot_analysis'])==2
    assert row['false_negative']+row['true_positive']==2
    assert row['false_positive']+row['true_positive']==row['predicted_count']
    assert row['gold_count']==2 and row['candidate_edges']==small[0]['edge_count']
    assert all('gold_relation_log_margin_vs_null' in x for x in row['gold_matched_slot_analysis'])
    stripped={k:v for k,v in small[0].items() if k not in ('positive','truth_count')}
    assert p55.infer_edges(model,stripped,'set_prediction')==p55.infer_edges(model,small[0],'set_prediction')
    assert json.loads(json.dumps(row))==row


def test_continuation_from_real_set_model_trains_checkpoints_and_saves_best_last():
    _,_,small,_,_=small_samples()
    torch.manual_seed(17)
    model=p55.TripleSetPredictor()
    initial={k:v.detach().clone() for k,v in model.state_dict().items()}
    result,rows,weights=m.continue_memorization(model,small,device='cpu',steps=3,interval=1,batch=2)
    assert result['optimizer_state_resumed'] is False
    assert 1<=result['optimizer_steps_total']<=3
    assert result['checkpoints'][0]['step']==0
    assert result['checkpoints'][-1]['step']==result['last_step']
    assert len(rows)==8*len(result['checkpoints'])
    assert len({x['prompt_sha256'] for x in rows})==8
    assert all(x['section']=='memorization' for x in rows)
    assert {'best.queries','last.queries'}<=set(weights)
    assert any(not torch.equal(weights['last.'+key],v) for key,v in initial.items())
    assert all(torch.isfinite(t).all() for t in weights.values())
    assert sum(x['false_negative'] for x in rows[-8:])==result['checkpoints'][-1]['false_negative']
    assert sum(x['false_positive'] for x in rows[-8:])==result['checkpoints'][-1]['false_positive']


def test_finalize_no_probe_scoring_and_auxiliary_predictions():
    records,pairs,_,_,_=small_samples()
    # select_same_eight mutates selected input; regenerate pristine records.
    records,pairs=ref().cohorts()
    report,weights,policy=m.finalize(records,pairs,
       {'exact_parent50_legacy_replay':True},
       {'interface_repair':{'decision':{'coverage_targets_met':True}}},
       device='cpu',steps=2,interval=1,batch=2,
       parent55_model=p55.TripleSetPredictor(),parent55_hashes={},parent48_sha='a'*64)
    assert report['gates']['generalization_evaluated'] is False
    assert report['production_promotion'] is False
    assert len(report['_auxiliary_ledger']['rows'])>=16
    assert all(x['section']=='memorization' for x in report['_auxiliary_ledger']['rows'])
    assert len(set(report['cohort']['prompt_sha256']))==8
    assert weights and policy['enabled'] is False


def test_parent55_contract_validates_actual_uploaded_weights(tmp_path):
    src=Path('/mnt/data')
    # Verify against the actual completed CUDA receipt and saved weights.
    if not (src/'g2_0055_development_models.safetensors').exists():
        pytest.skip('Uploaded G2-0055 CUDA artifacts not present')
    parent=tmp_path/'p55';parent.mkdir()
    p54=tmp_path/'p54';p54.mkdir()
    for incoming,member in [
      ('g2_literature_bakeoff_report.json',p55.REPORT),
      ('g2_literature_bakeoff_per_case.jsonl.gz',p55.LEDGER),
      ('g2_0055_development_models.safetensors',p55.WEIGHTS),
      ('g2_0055_frozen_policy.json',p55.POLICY)]:
        (parent/member).symlink_to(src/incoming)
    (p54/b54.REPORT).symlink_to(src/'g2_architectural_bakeoff_report.json')
    model,sha=m.verify_parent55(parent,p54)
    assert isinstance(model,p55.TripleSetPredictor)
    assert len(sha)==4 and all(len(v)==64 for v in sha.values())
    # Tampering with the policy must block continuation.
    (parent/p55.POLICY).unlink()
    (parent/p55.POLICY).write_text(json.dumps({'enabled':True}),encoding='utf-8')
    with pytest.raises(ValueError,match='PARENT55_INTEGRITY'):
        m.verify_parent55(parent,p54)


def test_public_cli_uses_named_0055_parent_and_new_immutable_output(monkeypatch,tmp_path):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    import io
    from contextlib import redirect_stdout
    seen={}
    def runner(**kwargs):
        seen.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
            'diagnosis':{'outcome':'IMPROVING_NOT_YET_EXACT'},
            'auxiliary_prediction_ledger':{'filename':m.PREDICTIONS,'rows':16},
            'gates':{'production_promotion':False}}}
    monkeypatch.setattr(m,'run',runner)
    with redirect_stdout(io.StringIO()) as f:
        main(['frob-g2-0055-memorization-diagnosis','--root',str(tmp_path),
              '--device','cpu','--steps','2','--checkpoint-interval','1'])
    assert seen['candidate55_run'].endswith('graph-g2-literature-bakeoff-0055')
    assert seen['output'].endswith('graph-g2-0055-memorization-diagnosis')
    assert seen['continuation_steps']==2 and seen['checkpoint_interval']==1
    assert 'IMPROVING_NOT_YET_EXACT' in f.getvalue()


def test_real_writer_persists_hash_verified_auxiliary_prediction_ledger(tmp_path,monkeypatch):
    # The actual G2-0051 save callback, including its real capture pipeline,
    # is invoked through the existing tested integration fixture.
    original=p51.run
    def intercepted(*args,**kwargs):
        if 'interface_finalize' in kwargs:
            finish=kwargs['interface_finalize']
            def inject(*a,**k):
                experiment,weights,policy=finish(*a,**k)
                experiment['_auxiliary_ledger']={'filename':m.PREDICTIONS,'rows':[
                    {'section':'memorization','prompt_sha256':'test-sha',
                     'predicted_edges':[{'edge_index':1,'relation':'owns'}]}]}
                return experiment,weights,policy
            kwargs['interface_finalize']=inject
        return original(*args,**kwargs)
    monkeypatch.setattr(p51,'run',intercepted)
    source=Path(__file__).with_name('test_nanojev_gamer_v1_g2_repaired_graph_learning.py')
    spec=importlib.util.spec_from_file_location('_g2_0055_diagnosis_writer_test',source)
    library=importlib.util.module_from_spec(spec);spec.loader.exec_module(library)
    library.test_real_0051_0052_0053_capture_callbacks_persist_scoring_artifacts(tmp_path,monkeypatch)
    folder=tmp_path/'score'
    with gzip.open(folder/m.PREDICTIONS,'rt',encoding='utf-8') as file:
        rows=[json.loads(line) for line in file]
    saved=json.loads((folder/g53.REPORT).read_text(encoding='utf-8'))
    detail=saved['scoring_experiment']['auxiliary_prediction_ledger']
    assert detail['rows']==1 and rows[0]['prompt_sha256']=='test-sha'
    assert detail['sha256']==p51.SOURCE.backbone.sha256(folder/m.PREDICTIONS)


def test_full_runner_uses_uploaded_0055_checkpoint_and_real_finalize(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
    from safetensors.torch import save_file
    src=Path('/mnt/data')
    if not (src/'g2_0055_development_models.safetensors').exists():
        pytest.skip('Uploaded G2-0055 checkpoint unavailable')
    parent=tmp_path/'p55';parent.mkdir()
    p54=tmp_path/'p54';p54.mkdir()
    for incoming,member in [
      ('g2_literature_bakeoff_report.json',p55.REPORT),
      ('g2_literature_bakeoff_per_case.jsonl.gz',p55.LEDGER),
      ('g2_0055_development_models.safetensors',p55.WEIGHTS),
      ('g2_0055_frozen_policy.json',p55.POLICY)]:
        (parent/member).symlink_to(src/incoming)
    (p54/b54.REPORT).symlink_to(src/'g2_architectural_bakeoff_report.json')
    parent48=tmp_path/'p48';parent48.mkdir()
    weights48=d48.OccurrenceEvidenceScorer(8).state_dict()
    save_file(weights48,str(parent48/d48.WEIGHTS))
    h48=d48.backbone.sha256(parent48/d48.WEIGHTS)
    (parent48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,
        'weights_sha256':h48}),encoding='utf-8')
    seen=[]
    monkeypatch.setattr(g53,'verify_parent52',lambda path:({},{}))
    monkeypatch.setattr(b54,'verify_parent53',lambda *args:({}, {}, {}))
    monkeypatch.setattr(p55,'verify_parent54',lambda *args:({}, {}))
    def captured(**kwargs):
        seen.append('capture')
        assert kwargs['scoring_schema']==m.SCHEMA
        assert kwargs['alternate_weights_filename']==m.WEIGHTS
        assert kwargs['alternate_policy_filename']==m.POLICY
        assert callable(kwargs['scoring_case_features'])
        records,pairs=ref().cohorts()
        exp,weights,policy=kwargs['scoring_finalize'](records,pairs,
            {'exact_parent50_legacy_replay':True},
            {'interface_repair':{'decision':{'coverage_targets_met':True}}})
        assert exp['diagnosis']['baseline']['step']==0
        assert exp['parent55_hashes']['g2_0055_weights_sha256']==d48.backbone.sha256(parent/p55.WEIGHTS)
        assert exp['_auxiliary_ledger']['rows'] and 'last.queries' in weights
        assert policy['enabled'] is False
        return {'status':m.STATUS,'scoring_experiment':exp}
    monkeypatch.setattr(p52,'run',captured)
    outcome=m.run(candidate55_run=parent,candidate54_run=p54,
        candidate53_run='unused',candidate52_run='unused',
        candidate51_run='unused',occurrence48_run=parent48,
        counterfactual50_run='unused',output=tmp_path/'out',device='cpu',
        continuation_steps=2,checkpoint_interval=1,batch_prompts=2)
    assert seen==['capture'] and outcome['status']==m.STATUS


def test_reject_invalid_parameters():
    _,_,small,_,_=small_samples()
    with pytest.raises(ValueError,match='CONTINUATION_BUDGET'):
        m.continue_memorization(p55.TripleSetPredictor(),small,device='cpu',steps=0)
    with pytest.raises(ValueError,match='INVALID_BUDGET'):
        m.run(candidate55_run='',candidate54_run='',candidate53_run='',candidate52_run='',
              candidate51_run='',occurrence48_run='',counterfactual50_run='',output='',continuation_steps=0)
