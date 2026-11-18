"""G2-0055 frozen step-320 checkpoint, exactly 80 evaluation graphs, zero training."""
import copy
import gzip
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_frozen_generalization_0055 as m
from tools import nanojev_gamer_v1_g2_memorization_diagnosis_0055 as md
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48


@pytest.fixture(autouse=True)
def threads():
    previous=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def cohort_fixture():
    location=Path(__file__).with_name('test_nanojev_gamer_v1_g2_literature_bakeoff_0055.py')
    spec=importlib.util.spec_from_file_location('g2_0055_frozen_cohort_fixture',location)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.cohorts()


def real_parent_folders(tmp_path):
    files=Path('/mnt/data')
    required=['g2_0055_memorization_diagnosis_report.json',
      'g2_0055_memorization_diagnosis_policy.json',
      'g2_0055_memorization_prediction_diagnostics.jsonl.gz',
      'g2_0055_memorization_candidate_per_case.jsonl.gz',
      'g2_0055_memorization_checkpoints.safetensors',
      'g2_literature_bakeoff_report.json','g2_literature_bakeoff_per_case.jsonl.gz',
      'g2_0055_development_models.safetensors','g2_0055_frozen_policy.json',
      'g2_architectural_bakeoff_report.json']
    if any(not (files/name).exists() for name in required):
        pytest.skip('Real CUDA parent artifacts unavailable in test runtime')
    diag=tmp_path/'diag';diag.mkdir()
    lit=tmp_path/'literature';lit.mkdir()
    bake=tmp_path/'bakeoff';bake.mkdir()
    for name,out in [
        ('g2_0055_memorization_diagnosis_report.json',md.REPORT),
        ('g2_0055_memorization_diagnosis_policy.json',md.POLICY),
        ('g2_0055_memorization_prediction_diagnostics.jsonl.gz',md.PREDICTIONS),
        ('g2_0055_memorization_candidate_per_case.jsonl.gz',md.LEDGER),
        ('g2_0055_memorization_checkpoints.safetensors',md.WEIGHTS)]:
        (diag/out).symlink_to(files/name)
    for name,out in [
        ('g2_literature_bakeoff_report.json',p55.REPORT),
        ('g2_literature_bakeoff_per_case.jsonl.gz',p55.LEDGER),
        ('g2_0055_development_models.safetensors',p55.WEIGHTS),
        ('g2_0055_frozen_policy.json',p55.POLICY)]:
        (lit/out).symlink_to(files/name)
    (bake/p54.REPORT).symlink_to(files/'g2_architectural_bakeoff_report.json')
    return diag,lit,bake


def test_read_actual_cuda_best_checkpoint_verify_integrity_and_infer(tmp_path):
    diag,lit,bake=real_parent_folders(tmp_path)
    model,hashes,gate=m.verify_frozen_checkpoint(diag,lit,bake)
    assert len(gate)==8 and hashes['g2_0055_frozen_source_step']==320
    assert hashes['g2_0055_diagnosis_weights_sha256']==d48.backbone.sha256(diag/md.WEIGHTS)
    assert isinstance(model,p55.TripleSetPredictor)
    assert all(not p.requires_grad and p.grad is None for p in model.parameters())
    m.device_preflight(model,'cpu')
    # Verify the real exact step-320 checkpoint is frozen in evaluation.
    copied={k:v.clone() for k,v in model.state_dict().items()}
    records,pairs=cohort_fixture()
    split,_=p54.split_cohorts(records,pairs)
    small,_=p55.memorization_subset(split,p54.split_cohorts(cohort_fixture()[0],cohort_fixture()[1])[1])
    assert gate==set(json.loads((diag/md.REPORT).read_text())['scoring_experiment']['cohort']['prompt_sha256'])
    assert len(small)==8
    assert all(torch.equal(copied[k],v) for k,v in model.state_dict().items())


def test_real_checkpoint_hash_tamper_refused(tmp_path):
    diag,lit,bake=real_parent_folders(tmp_path)
    (diag/md.POLICY).unlink()
    policy=json.loads((Path('/mnt/data')/'g2_0055_memorization_diagnosis_policy.json').read_text())
    policy['enabled']=True
    (diag/md.POLICY).write_text(json.dumps(policy))
    with pytest.raises(ValueError,match='PARENT_DIAGNOSIS_OR_HASH'):
        m.verify_frozen_checkpoint(diag,lit,bake)


def test_real_checkpoint_on_exact_80_cohorts_never_receives_gold(monkeypatch,tmp_path):
    diag,lit,bake=real_parent_folders(tmp_path)
    model,_,_=m.verify_frozen_checkpoint(diag,lit,bake)
    records,pairs=cohort_fixture()
    # Use local fixture's eight actual TRAIN ids, not SHA values of CUDA prompts.
    strat,groups=p54.split_cohorts(copy.deepcopy(records),copy.deepcopy(pairs))
    small,_=p55.memorization_subset(strat,groups)
    training_ids={x['prompt_sha256'] for x in small}
    before={k:v.detach().clone() for k,v in model.state_dict().items()}
    original=p55.infer_edges
    observed=[]
    def inspect(model,sample,arm):
        assert 'positive' not in sample and 'truth_count' not in sample
        assert 'labels' not in sample and 'edges' not in sample
        observed.append(sample['prompt_sha256'])
        return original(model,sample,arm)
    monkeypatch.setattr(p55,'infer_edges',inspect)
    result,rows=m.eval_cohorts(model,records,pairs,training_ids)
    assert len(rows)==80 and len(observed)==80
    assert set(observed)=={x['prompt_sha256'] for x in rows}
    assert all(x['prompt_sha256'] not in training_ids for x in rows)
    assert [result[name]['cases'] for name in m.SECTIONS]==[16,32,32]
    assert [result[name]['pairs'] for name in m.SECTIONS]==[8,16,16]
    assert all(sum(a['cases'] for a in result[name]['families'].values())==result[name]['cases']
               for name in m.SECTIONS)
    assert all(torch.equal(before[k],v) for k,v in model.state_dict().items())
    assert all(not p.requires_grad and p.grad is None for p in model.parameters())


def test_reject_leaked_gate_graph_or_duplicate_evaluation_id(tmp_path):
    records,pairs=cohort_fixture()
    strat,groups=p54.split_cohorts(copy.deepcopy(records),copy.deepcopy(pairs))
    small,_=p55.memorization_subset(strat,groups)
    gate={x['prompt_sha256'] for x in small}
    bad={x['prompt_sha256'] for x in records if x['section']=='counterfactual_probe'}
    model=p55.TripleSetPredictor().eval().requires_grad_(False)
    with pytest.raises(ValueError,match='MEMORIZATION_GATE_COHORT_DRIFT'):
        m.eval_cohorts(model,records,pairs,gate|{next(iter(bad))})


def test_readonly_finalizer_has_zero_optimizer_steps_and_80_prediction_rows(tmp_path):
    records,pairs=cohort_fixture()
    split,groups=p54.split_cohorts(copy.deepcopy(records),copy.deepcopy(pairs))
    small,_=p55.memorization_subset(split,groups)
    model=p55.TripleSetPredictor().eval().requires_grad_(False)
    frozen={k:v.detach().clone() for k,v in model.state_dict().items()}
    exp,weights,policy=m.finalize(records,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        model=model,source_hashes={'g2_0055_frozen_source_step':320},
        gate_ids={x['prompt_sha256'] for x in small})
    assert exp['training']=={'optimizer_steps_total':0,'optimizer_created':False}
    assert exp['evaluation']['graphs_evaluated']==80
    assert exp['evaluation']['memorization_graphs_evaluated']==0
    assert len(exp['_auxiliary_ledger']['rows'])==80
    assert all(torch.equal(v,weights[k]) for k,v in frozen.items())
    assert policy['enabled'] is False and policy['optimizer_steps_total']==0
    assert exp['gates']['model_frozen'] is True


def test_runner_dispatch_from_real_cli_without_training(monkeypatch,tmp_path):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    called={}
    def runner(**kwargs):
        called.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
            'source_checkpoint':{'g2_0055_frozen_source_step':320},
            'evaluation':{'results':{}},'training':{'optimizer_steps_total':0},
            'gates':{'model_frozen':True}}}
    monkeypatch.setattr(m,'run',runner)
    with redirect_stdout(io.StringIO()) as captured:
        main(['frob-g2-0055-frozen-generalization','--root',str(tmp_path),'--device','cpu'])
    assert called['device']=='cpu'
    assert called['candidate55_run'].endswith('graph-g2-literature-bakeoff-0055')
    assert called['diagnosis55_run'].endswith('graph-g2-0055-memorization-diagnosis')
    assert called['output'].endswith('graph-g2-0055-frozen-generalization')
    assert called['train_pairs']==48 and called['probe_pairs']==16
    assert '"optimizer_steps": 0' in captured.getvalue()


def test_runner_with_real_checkpoint_and_finalizer_callback(tmp_path,monkeypatch):
    diag,lit,bake=real_parent_folders(tmp_path)
    parent48=tmp_path/'parent48';parent48.mkdir()
    original=d48.OccurrenceEvidenceScorer(8).state_dict()
    save_file(original,str(parent48/d48.WEIGHTS))
    h48=d48.backbone.sha256(parent48/d48.WEIGHTS)
    (parent48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':h48}))
    monkeypatch.setattr(g53,'verify_parent52',lambda *a,**k:({},{}))
    monkeypatch.setattr(p54,'verify_parent53',lambda *a,**k:({}, {}, {}))
    monkeypatch.setattr(p55,'verify_parent54',lambda *a,**k:({}, {}))
    did=[]
    def captured(**kwargs):
        did.append(True)
        assert kwargs['interface_inference_only'] is True
        assert kwargs['scoring_schema']==m.SCHEMA
        assert kwargs['alternate_weights_filename']==m.WEIGHTS
        assert kwargs['alternate_policy_filename']==m.POLICY
        assert callable(kwargs['scoring_case_features'])
        rows,pairs=cohort_fixture()
        # This path runs the full 80-graph frozen model evaluation callback.
        # Fixture SHA identifiers differ from real CUDA gate cohort; patch the
        # exact cohort-check by passing its locally analogous train IDs.
        chosen,grp=p54.split_cohorts(copy.deepcopy(rows),copy.deepcopy(pairs))
        gate,_=p55.memorization_subset(chosen,grp)
        local={x['prompt_sha256'] for x in gate}
        original_finalize=m.finalize
        def local_finalize(*args,**k):
            k['gate_ids']=local
            return original_finalize(*args,**k)
        monkeypatch.setattr(m,'finalize',local_finalize)
        try:
            experiment,weights,policy=kwargs['scoring_finalize'](rows,pairs,
                {'exact_parent50_legacy_replay':True},
                {'interface_repair':{'decision':{'coverage_targets_met':True}}})
        finally:
            monkeypatch.setattr(m,'finalize',original_finalize)
        assert experiment['training']['optimizer_steps_total']==0
        assert len(experiment['_auxiliary_ledger']['rows'])==80
        assert policy['enabled'] is False and len(weights)>0
        return {'status':m.STATUS,'scoring_experiment':experiment}
    monkeypatch.setattr(p52,'run',captured)
    def optimizer_forbidden(*args,**kwargs):
        raise AssertionError('Frozen generalization must not create an optimizer')
    monkeypatch.setattr(torch.optim,'AdamW',optimizer_forbidden)
    result=m.run(diagnosis55_run=diag,candidate55_run=lit,candidate54_run=bake,
        candidate53_run='none',candidate52_run='none',candidate51_run='none',
        occurrence48_run=parent48,counterfactual50_run='none',
        output=tmp_path/'output',device='cpu')
    assert did==[True] and result['status']==m.STATUS


def test_scoring_extension_refuses_fake_zero_training_without_explicit_readonly():
    # The default shared G2-0051 extension contract remains TRAIN-ONLY:
    # backwards compatibility must not silently accept zero-step "training".
    import inspect
    assert inspect.signature(p51.run).parameters['interface_inference_only'].default is False

# Same real SOURCE.run / G2-0051 -> G2-0052 -> frozen extension writer boundary
# used in G2-0053 integration, with the receipt changed to ZERO optimizer steps.
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as e
from tools.nanojev_gamer_v1_g2_occurrence_evidence_repair import OccurrenceEvidenceScorer
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead

def feature_case():
    torch.manual_seed(553)
    head=GamerGraphHead(hidden_size=8,width=16,layers=1,latent_slots=2,
                        memory_slots=2,max_span_tokens=2).eval().requires_grad_(False)
    hidden=torch.randn(4,8)
    prepared={'prompt':'alpha beta gamma delta','prompt_start':0,
        'offsets':[(0,5),(6,10),(11,16),(17,22)],
        'eligible':torch.ones(4,dtype=torch.bool)}
    g1=head(hidden,eligible=prepared['eligible'],active_stage='G1')
    raw={'span_logits':g1['span_logits'],'span_valid':g1['span_valid'],
         'prepared':prepared,'hidden':hidden,'allowed':{('owns','actor','action')}}
    a=(0,5,'actor');b=(6,10,'action');c=(11,16,'action')
    parent={'expanded_anchors':[a,b], 'recovered_typed':{(a,b,'owns')}}
    extended=e.construct(raw,parent)
    return raw,extended,a,b,c

def test_actual_shared_callback_writes_zero_step_frozen_evaluation(tmp_path,monkeypatch):
    """Run the ACTUAL p51 callback including p52 constructor and final ZIP inputs.

    The parent-report verification and expensive Qwen extraction are replaced
    with precise synthetic boundaries; candidate construction, neural feature
    extraction, gold-after-inference timing, report writing and model saving are real.
    """
    from types import SimpleNamespace as N
    from safetensors.torch import load_file
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
    from tools.nanojev_gamer_v1_g2_candidate_construction_repair import SOURCE
    root=tmp_path/'score';A=(0,5,'actor');B=(6,10,'action')
    prompts=['alpha beta '+word+' delta' for word in
        ('gamma','sigma','theta','omega','kappa','alpha','bravo','fable',
         'globe','house','inlet','joker')]
    rows=[{'prompt':p,'split':'train','family':'allocation',
           'annotation_provenance':'AUTHOR_MARKED_IN_SOURCE_GENERATOR'} for p in prompts]
    def pair(i,section):
        chunk=rows[i:i+2]
        return {'id':section+'-pair','rows':chunk,'mode':'role_swap','family':'allocation',
                'sha256':[SOURCE.base._sha(x['prompt']) for x in chunk]}
    cohorts={'counterfactual_train':[pair(0,'counterfactual_train')],
             'counterfactual_probe':[pair(i,'counterfactual_probe') for i in range(2,12,2)]}
    mk=lambda *args,**kwargs:({}, {})
    fake=N(d49=N(g41=N(verify_parent40=mk),
                  g42=N(verify_parent41=lambda *a,**k:({}, {},{})),
                  b43=N(verify_parent42=mk),verify_parent48=lambda *a,**k:
                      ({'cohorts':{'train_learn_prompts':[]}}, {}, [])),
           d48=N(d45=N(d44=N(verify_parent43=mk),verify_parent44=mk),verify_parent47=mk),
           d46=N(verify_parent45=mk),d47=N(verify_parent46=lambda *a,**k:
               ({'cohorts':{'train_learn_prompts':[]}},{})),
           verify_parent49=mk,author_pairs=lambda t,p,**k:{
               'counterfactual_train':cohorts['counterfactual_train'],
               'counterfactual_probe':cohorts['counterfactual_probe'][:p]})
    monkeypatch.setattr(p51,'p50',fake)
    monkeypatch.setattr(p51,'verify_parent50',lambda *a,**k:({
         'cohorts':{'train_learn_prompts':[]},
         'training':{'author_pairs_requested':1,'probe_pairs_requested':1},
         'schema':'fake.valid.parent50'},{}))
    monkeypatch.setattr(p51,'parent50_probe_ledger',lambda *a,**k:
        {SOURCE.base._sha(x['prompt']):0 for x in rows[2:4]})
    monkeypatch.setattr(SOURCE.base,'gold_binding_edges',lambda row:{(A,B,'owns')})
    parent48=d48.OccurrenceEvidenceScorer(8).eval().requires_grad_(False)
    def feature_callback(raw,extension,section):
        return g.frozen_features(raw,extension,parent48,section)
    calls=[]
    def completion(cases,pairs,replay,report):
        calls.append({'count':len(cases),'replay':replay,'report':report['schema']})
        assert len(cases)==12 and replay['exact_parent50_legacy_replay'] is True
        assert sum(x['section']=='fresh_counterfactual_probe' for x in cases)==8
        assert all(x['labels']=={(A,B,'owns')} for x in cases)
        return {'score':'frozen','training':{'optimizer_steps_total':0},'gates':{'model_frozen':True}},g.EdgeRanker().state_dict(),{'enabled':False}
    def fake_capture(material,tag,*,include_raw_g1):
        assert include_raw_g1
        result=[]
        for row in material:
            raw,ext,_,_,_=feature_case()
            prepared=dict(raw['prepared']);prepared['prompt']=row['prompt']
            raw['prepared']=prepared
            baseline={'row':row,'recovered':{'anchors':[A,B]},
               'shortlisted':{'edges':[(A,B,'owns')]}}
            evidence={'anchors':[A,B],'spans':[{'token_first':0,'token_last':0},
                                            {'token_first':1,'token_last':1}],
                'baseline_anchors':[A]}
            baseline['candidate_repair']=p51.candidates_for_record(baseline,evidence,raw['allowed'])
            baseline['g1_raw_interface']=raw
            result.append(baseline)
        return result
    def fake_source_run(*,diagnostic_callback,**kwargs):
        return diagnostic_callback(capture_candidate_repair_rows=fake_capture,
                                   assert_all_frozen=lambda:None,
                                   training=[],fitting=[],auditing=[])
    monkeypatch.setattr(SOURCE,'run',fake_source_run)
    result=p51.run(bakeoff40_run='p40',oracle41_run='p41',rank42_run='p42',
        absolute43_run='p43',memorization44_run='p44',separability45_run='p45',
        multiprompt46_run='p46',crossover47_run='p47',occurrence48_run='p48',
        integrated49_run='p49',counterfactual50_run='p50',output=str(root),
        train_pairs=1,probe_pairs=1,fresh_probe_pairs=4,device='cpu',
        interface_inference_only=True,
        interface_extension=e.construct,interface_audit=e.audit,
        interface_summary=e.summarize,
        interface_case_features=feature_callback,interface_finalize=completion,
        alternate_schema=g.SCHEMA,alternate_status=g.STATUS,
        alternate_report_filename=g.REPORT,alternate_ledger_filename=g.LEDGER)
    assert calls and result['scoring_experiment']['score']=='frozen'
    assert result['new_optimizer_steps']==0 and result['scorer_retraining_performed'] is False
    report=json.loads((root/g.REPORT).read_text())
    assert report['status']==g.STATUS
    assert report['scorer_retraining_performed'] is False
    assert report['new_optimizer_steps']==0
    assert report['fresh_probe_candidate_audit']['cases']==8
    assert report['fresh_probe_candidate_audit']['pairs']==4
    assert report['scoring_weights_sha256']==SOURCE.backbone.sha256(root/g.WEIGHTS)
    assert report['scoring_policy_sha256']==SOURCE.backbone.sha256(root/g.POLICY)
    assert report['ledger_sha256']==SOURCE.backbone.sha256(root/g.LEDGER)
    assert len(load_file(str(root/g.WEIGHTS)))==len(g.EdgeRanker().state_dict())
    with gzip.open(root/g.LEDGER,'rt') as f:
        assert len([json.loads(line) for line in f])==12
