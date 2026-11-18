"""G2-0054 end-to-end cohort, optimizer, decoder, oracle and CLI validation."""
import copy
import gzip
import hashlib
import io
import json
from contextlib import redirect_stdout

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as b
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52


@pytest.fixture(autouse=True)
def single_thread():
    old=torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def _sample(section,i,side,*,gold=True):
    a=(0,5,'actor');z=(6,10,'action');w=(11,15,'actor')
    edges=[(a,z,'owns'),(w,z,'owns')]+[(a,z,'rel'+str(j)) for j in range(18)]
    n=len(edges)
    node=torch.randn(3,96,generator=torch.Generator().manual_seed(17)).to(torch.float16)
    relation=torch.tensor([0,0]+[j%len(g.RELATIONS) for j in range(18)])
    return {'section':section,'edges':edges,'node':node,
        'src':torch.tensor([0,2]+[0]*18),'dst':torch.tensor([1]*n),
        'relation':relation,'kinds':torch.zeros(3,dtype=torch.long),
        'margin':torch.tensor([.3,.4,.5]),
        'geometry':torch.zeros(n,5),
        'labels':{edges[0]} if gold else {(a,z,'missing_gold')},
        'prompt_sha256':f'{section}-{i}-{side}',
        'pair':f'{section}-p{i}',
        'family':('allocation','encounter')[(i//1)%2],
        'mode':('role_swap','renaming')[(i//2)%2]}


def _cohorts():
    pairs={};samples=[]
    for section,num in (('counterfactual_train',48),('counterfactual_probe',16),
                        ('fresh_counterfactual_probe',16)):
        section_pairs=[]
        for i in range(num):
            shas=[]
            for side in (0,1):
                sample=_sample(section,i,side,gold=not (section=='counterfactual_probe' and i==0 and side==0))
                samples.append(sample);shas.append(sample['prompt_sha256'])
            section_pairs.append({'id':f'{section}-p{i}','sha256':shas,
                'family':('allocation','encounter')[i%2],
                'mode':('role_swap','renaming')[(i//2)%2]})
        pairs[section]=section_pairs
    return samples,pairs


def _parent_state():
    return {k:v.clone() for k,v in g.EdgeRanker().state_dict().items()}


def test_exact_train_fit_split_and_oracle_ceiling():
    items,pairs=_cohorts()
    cohorts,paired=b.split_cohorts(items,pairs)
    assert [len(cohorts[k]) for k in ('train','train_fit','counterfactual_probe','fresh_counterfactual_probe')]==[80,16,32,32]
    assert [len(paired[k]) for k in ('train','train_fit','counterfactual_probe','fresh_counterfactual_probe')]==[40,8,16,16]
    fake=torch.zeros(20)
    scored=[(s,fake,torch.arange(20)) for s in cohorts['counterfactual_probe']]
    results=b.evaluate_rows(scored,arm='Oracle_coverage_ceiling',oracle=True)
    measured=g.aggregate(results,paired['counterfactual_probe'])
    assert measured['exact']==31 and measured['both_exact_pairs']==15
    assert measured['missing']==1 and measured['extra']==0


def test_competitor_negative_mining_retains_all_gold_and_shared_endpoint_rivals():
    s=g.mark_truth(_sample('counterfactual_train',0,0))
    score=torch.arange(s['edge_count']).float()
    ix=b.competing_negative_indices(s,score,torch.Generator().manual_seed(1))
    assert set(s['positive'].tolist())<=set(ix.tolist())
    assert len(ix.unique())==len(ix)
    assert ix.max()<s['edge_count']
    assert len(set(ix.tolist())-set(s['positive'].tolist()))>0
    assert (s['src'][ix]==s['src'][s['positive'][0]]).any()


def test_both_rank_training_paths_start_identically_and_modify_weights():
    s=[g.mark_truth(_sample('counterfactual_train',i,0)) for i in range(4)]
    original=_parent_state()
    models=[]
    for kind in ('control','competition'):
        model,history=b.continue_ranker(original,s,mode=kind,device='cpu',steps=2,batch=2)
        assert history[0]['step']==1 and history[-1]['step']==2
        assert all(torch.isfinite(x).all() for x in model.state_dict().values())
        models.append(model)
    assert any(not torch.equal(models[0].state_dict()[key],models[1].state_dict()[key])
               for key in original)
    assert all(torch.equal(v,original[k]) for k,v in original.items())


def test_calibrator_has_no_probe_labels_and_produces_bounded_decoder():
    cases=[g.mark_truth(_sample('counterfactual_train',i,0)) for i in range(16)]
    model=g.EdgeRanker()
    rows=b.ranked_predictions(model,cases)
    fit=b.calibrate(rows)
    assert isinstance(fit['threshold'],float)
    assert fit['candidate_thresholds_tested']>=2
    output=b.evaluate_rows(rows,arm='B',threshold=fit['threshold'])
    assert all(0<=r['selected_edges']<=b.DECODE_BUDGET for r in output)
    cases[0]['section']='counterfactual_probe'
    with pytest.raises(ValueError,match='CALIBRATION_COHORT'):
        b.calibrate(b.ranked_predictions(model,cases))


def test_full_bakeoff_trains_both_arms_and_returns_report_and_all_weights():
    raw,pairs=_cohorts()
    parent={'scoring_experiment':{'results':{'train':{'cases':80},
             'train_fit':{'cases':16},'counterfactual_probe':{'cases':32}}}}
    report={'interface_repair':{'decision':{'coverage_targets_met':True}}}
    experiment,weights,policy=b.finalize(raw,pairs,
        {'exact_parent50_legacy_replay':True},report,
        device='cpu',steps=2,batch=2,parent53_state=_parent_state(),
        parent53_report=parent,parent53_hashes={'ref':'hash'},parent48_sha='0'*64)
    assert set(experiment['results'])=={'train','train_fit','counterfactual_probe','fresh_counterfactual_probe'}
    assert all(len(experiment['results'][section])==5 for section in experiment['results'])
    assert experiment['results']['counterfactual_probe']['Oracle_coverage_ceiling']['exact']==31
    assert experiment['results']['counterfactual_probe']['Oracle_coverage_ceiling']['both_exact_pairs']==15
    assert experiment['training']['optimizer_steps_total']==4
    assert experiment['development_selection']['chosen_arm'] in experiment['results']['train_fit']
    assert all(k.startswith(('control.','competition.')) for k in weights)
    assert len(weights)==2*len(_parent_state())
    assert policy['enabled'] is False and policy['oracle_is_not_deployable'] is True
    assert experiment['gates']['production_promotion'] is False
    assert experiment['design']['probes_used_for_model_selection'] is False


def test_selection_is_invariant_to_probe_changes():
    base={'train_fit':{}}
    for i,arm in enumerate(('A_control_fixed','B_control_calibrated',
                             'C_competition_fixed','D_competition_calibrated')):
        base['train_fit'][arm]={'exact':i,'missing':20-i,'extra':10,'rank_coverage':i}
    base['counterfactual_probe']={'C_competition_fixed':{'exact':400}}
    initial=b.choose_arm(base)
    base['counterfactual_probe']['C_competition_fixed']['exact']=0
    assert b.choose_arm(base)==initial=='D_competition_calibrated'


def test_parent53_verifies_realistic_0053_schema_weights_and_0052_hash(tmp_path):
    p53=tmp_path/'p53';p52_dir=tmp_path/'p52';p53.mkdir();p52_dir.mkdir()
    (p52_dir/p52.REPORT).write_text(json.dumps({'schema':p52.SCHEMA}))
    save_file(_parent_state(),str(p53/g.WEIGHTS))
    with gzip.open(p53/g.LEDGER,'wt') as f:f.write('{}\n')
    sha=g.p52.p51.SOURCE.backbone.sha256
    parent53={'schema':g.SCHEMA,'status':g.STATUS,
        'production_promotion':False,'sealed_test_used':False,
        'scorer_retraining_performed':True,'new_optimizer_steps':240,
        'scoring_weights_sha256':sha(p53/g.WEIGHTS),
        'ledger_sha256':sha(p53/g.LEDGER),
        'scoring_experiment':{'parent_hashes':{'g2_0052_report_sha256':sha(p52_dir/p52.REPORT)},
            'training':{'frozen_g1_and_frozen_0048_encoder':True},
            'gates':{'production_promotion':False},
            'results':{'train':{'cases':80},'train_fit':{'cases':16},
                       'counterfactual_probe':{'cases':32}}}}
    (p53/g.REPORT).write_text(json.dumps(parent53))
    report,state,hashes=b.verify_parent53(p53,{'schema':p52.SCHEMA},p52_dir)
    assert report['schema']==g.SCHEMA and len(state)==len(_parent_state())
    assert len(hashes['g2_0053_weights_sha256'])==64
    parent53['scoring_weights_sha256']='not-a-hash'
    (p53/g.REPORT).write_text(json.dumps(parent53))
    with pytest.raises(ValueError,match='PARENT53_CONTRACT'):
        b.verify_parent53(p53,{'schema':p52.SCHEMA},p52_dir)


def test_cli_dispatch_enters_0054_runner_with_all_parents(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    seen={}
    def stub(**kwargs):
        seen.update(kwargs)
        return {'status':b.STATUS,'scoring_experiment':{
            'results':{},'calibration':{},'development_selection':{},
            'gates':{'production_promotion':False}}}
    monkeypatch.setattr(b,'run',stub)
    with redirect_stdout(io.StringIO()) as output:
        main(['frob-g2-architectural-bakeoff','--root',str(tmp_path),'--device','cpu'])
    assert seen['candidate53_run'].endswith('graph-g2-repaired-graph-learning-0053')
    assert seen['candidate52_run'].endswith('graph-g2-endpoint-interface-repair-0052')
    assert seen['candidate51_run'].endswith('graph-g2-candidate-construction-repair-0051')
    assert seen['steps']==160 and seen['batch_prompts']==4
    assert 'G2_0054_CONTROLLED_BAKEOFF_AUDITED_NOT_CERTIFIED' in output.getvalue()


def test_0054_passes_custom_weight_names_through_real_p52_runner(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
    import types
    # Check the real 0054 wrapper accepts real parent53 and forwards policy,
    # schema, report and weight names to the existing G2-0052 capture engine.
    p48=tmp_path/'p48';p48.mkdir()
    net=d48.OccurrenceEvidenceScorer(8)
    save_file(net.state_dict(),str(p48/d48.WEIGHTS))
    sha=d48.backbone.sha256(p48/d48.WEIGHTS)
    (p48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':sha}))
    monkeypatch.setattr(g,'verify_parent52',lambda *a,**k:({'mock':'parent52'},{}))
    monkeypatch.setattr(b,'verify_parent53',lambda *a,**k:({
        'scoring_experiment':{'results':{}}},_parent_state(),{'mock':'parent53'}))
    src=tmp_path/'p52';src.mkdir();(src/p52.REPORT).write_text('{}')
    captured={}
    def actual_wrapper_spy(**kwargs):
        captured.update(kwargs)
        assert kwargs['scoring_schema']==b.SCHEMA
        assert kwargs['scoring_report_name']==b.REPORT
        assert kwargs['alternate_weights_filename']==b.WEIGHTS
        assert kwargs['alternate_policy_filename']==b.POLICY
        return {'status':b.STATUS}
    monkeypatch.setattr(p52,'run',actual_wrapper_spy)
    assert b.run(candidate53_run='unused',candidate52_run=src,candidate51_run='unused',
        occurrence48_run=p48,counterfactual50_run='unused',output=tmp_path/'out',
        device='cpu')['status']==b.STATUS
    assert callable(captured['scoring_case_features'])
    assert callable(captured['scoring_finalize'])


def test_actual_source_callback_saves_0054_named_artifacts(tmp_path,monkeypatch):
    """Reuse the 0053 real 0051/0052 capture test with 0054 output filenames.

    Exercises the real capture -> feature -> gold audit -> scorer finalize ->
    gzip ledger -> safetensors -> JSON policy -> immutable publish pipeline.
    """
    import importlib.util
    from pathlib import Path
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
    source=Path(__file__).with_name('test_nanojev_gamer_v1_g2_repaired_graph_learning.py')
    spec=importlib.util.spec_from_file_location('_g2_0053_bakeoff_io_reference',source)
    reference=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    # The existing full callback test expects the artifact constants from its
    # 0053 module. Override only for this test and restore via monkeypatch.
    for name,value in (('WEIGHTS',b.WEIGHTS),('POLICY',b.POLICY),
                       ('REPORT',b.REPORT),('LEDGER',b.LEDGER)):
        monkeypatch.setattr(g,name,value)
    old=p51.run
    def call_with_0054_filenames(*args,**kwargs):
        kwargs['alternate_weights_filename']=b.WEIGHTS
        kwargs['alternate_policy_filename']=b.POLICY
        return old(*args,**kwargs)
    monkeypatch.setattr(p51,'run',call_with_0054_filenames)
    reference.test_real_0051_0052_0053_capture_callbacks_persist_scoring_artifacts(
        tmp_path,monkeypatch)
    assert (tmp_path/'score'/b.WEIGHTS).is_file()
    assert (tmp_path/'score'/b.POLICY).is_file()
    assert (tmp_path/'score'/b.REPORT).is_file()
    assert (tmp_path/'score'/b.LEDGER).is_file()


def test_cpu_endpoint_indexing_and_loss_gradient_in_both_arms():
    # Exactly the CPU sample-index / model-score device split used in CUDA.
    # With CPU scores this exercises real endpoint-rival masks and gradients.
    sample=g.mark_truth(_sample('counterfactual_train',0,0))
    net=g.EdgeRanker()
    idx=b.competing_negative_indices(sample,torch.arange(sample['edge_count']).float(),
                                     torch.Generator().manual_seed(3))
    assert idx.device.type=='cpu'
    for mode in ('control','competition'):
        net.zero_grad(set_to_none=True)
        loss=b._loss(net,sample,idx,mode)
        assert torch.isfinite(loss)
        loss.backward()
        assert any(p.grad is not None and torch.any(p.grad!=0) for p in net.parameters())


def test_preflight_trains_both_arms_before_capture_on_cpu(monkeypatch):
    from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
    out=io.StringIO()
    initial=_parent_state()
    with redirect_stdout(out):
        b.training_device_preflight(initial,'cpu')
    assert 'GAMER_G2_0054_DEVICE_PREFLIGHT' in out.getvalue()
    assert '"status": "PASS"' in out.getvalue()
    assert all(torch.equal(x,initial[k]) for k,x in initial.items())


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Requires actual CUDA')
def test_cuda_preflight_exercises_both_training_arms_and_no_device_mismatch():
    # GPU acceptance: catches the previously undetected CPU ids / CUDA mask
    # mismatch in the competition branch before the expensive source capture.
    b.training_device_preflight(_parent_state(),'cuda')


def test_operator_command_runs_preflight_before_expensive_capture(tmp_path,monkeypatch):
    # Check the real public run wrapper actually invokes the guard before
    # entering the 0052 capture engine rather than relying on tests alone.
    from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48
    import json
    from safetensors.torch import save_file
    p48=tmp_path/'parent48';p48.mkdir()
    net=d48.OccurrenceEvidenceScorer(8)
    save_file(net.state_dict(),str(p48/d48.WEIGHTS))
    sha=d48.backbone.sha256(p48/d48.WEIGHTS)
    (p48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':sha}))
    monkeypatch.setattr(g,'verify_parent52',lambda *args,**kwargs: ({},{}))
    monkeypatch.setattr(b,'verify_parent53',lambda *args,**kwargs: ({},_parent_state(),{}))
    p52_dir=tmp_path/'parent52';p52_dir.mkdir()
    (p52_dir/p52.REPORT).write_text('{}')
    observed=[]
    real_preflight=b.training_device_preflight
    def checked_preflight(weights,device):
        observed.append('preflight')
        return real_preflight(weights,device)
    def capture_spy(**kwargs):
        observed.append('capture')
        return {'status':b.STATUS}
    monkeypatch.setattr(b,'training_device_preflight',checked_preflight)
    monkeypatch.setattr(p52,'run',capture_spy)
    b.run(candidate53_run='unused',candidate52_run=p52_dir,candidate51_run='unused',
          occurrence48_run=p48,counterfactual50_run='unused',
          output=tmp_path/'output',device='cpu')
    assert observed==['preflight','capture']
