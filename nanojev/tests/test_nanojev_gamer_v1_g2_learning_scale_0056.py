"""G2-0056 controlled scale, holdout boundaries and actual optimizer tests."""
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_learning_scale_0056 as m
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_candidate_construction_repair as p51
from tools import nanojev_gamer_v1_g2_counterfactual_learning as p50
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_g2_frozen_generalization_0055 as f55
from tools import nanojev_gamer_v1_g2_memorization_diagnosis_0055 as mem55
from tools import nanojev_gamer_v1_g2_architectural_bakeoff_0054 as p54
from tools import nanojev_gamer_v1_g1_g2_endpoint_repair as p52
from tools import nanojev_gamer_v1_g2_occurrence_evidence_repair as d48

@pytest.fixture(autouse=True)
def cpu_threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def cohorts():
    path=Path(__file__).with_name('test_nanojev_gamer_v1_g2_literature_bakeoff_0055.py')
    spec=importlib.util.spec_from_file_location('g2_0056_parent_fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    samples,pairs=fixture.cohorts()
    final=[];final_pairs=[]
    for j in range(16):
        i=j
        cell_family=('allocation','encounter')[i%2]
        cell_mode=('role_swap','renaming')[(i//2)%2]
        names=[]
        for side in (0,1):
            row=fixture.sample(m.FINAL_GROUP,str(i),side,cell_family,cell_mode)
            anchors=[(k*3,k*3+2,'actor') for k in range(7)]
            edges=[(anchors[int(a)],anchors[int(b)],g53.RELATIONS[int(c)]) for a,b,c in zip(
                row['src'],row['dst'],row['relation'])]
            row['edges']=edges;row['labels']={edges[0],edges[4]}
            final.append(row);names.append(row['prompt_sha256'])
        final_pairs.append({'id':m.FINAL_GROUP+'-'+str(i),'sha256':names,
            'family':cell_family,'mode':cell_mode})
    samples.extend(final)
    for row in samples:
        row.pop('positive',None);row.pop('truth_count',None)
    pairs[m.FINAL_GROUP]=final_pairs
    stages,validation,valid_pairs,stage_pairs,_=m.arrange_cohorts(
        copy.deepcopy(samples),copy.deepcopy(pairs),gate_for(samples,pairs))
    return samples,pairs,stages,validation,valid_pairs,stage_pairs


def gate_for(samples,pairs):
    grouped={}
    for pair in pairs['counterfactual_train']:
        grouped.setdefault((pair['family'],pair['mode']),[]).append(pair)
    return m._ids([grouped[cell][2] for cell in m.CELLS])


def test_balance_pair_independence_and_new_holdout_never_marked():
    samples,pairs,_,_,_,_=cohorts()
    stages,validation,valid_pairs,groups,holdouts=m.arrange_cohorts(
        copy.deepcopy(samples),copy.deepcopy(pairs),gate_for(samples,pairs))
    assert [len(stages[x]) for x in m.SIZES]==[8,16,32,80]
    assert len(valid_pairs)==8 and len(validation)==16
    assert {x['prompt_sha256'] for x in stages[8]}==gate_for(samples,pairs)
    assert all('positive' in x and 'truth_count' in x for x in stages[80])
    assert all('labels' in x for x in holdouts[m.FINAL_GROUP])
    assert all('labels' in x for x in holdouts['counterfactual_probe'])
    assert all('positive' not in x for x in holdouts[m.FINAL_GROUP])
    assert len({s['prompt_sha256'] for s in holdouts[m.FINAL_GROUP]})==32
    for size in m.SIZES:
        assert {s['prompt_sha256'] for s in stages[size]}.isdisjoint(
            {s['prompt_sha256'] for s in validation})
    assert m._ids(groups[8])<m._ids(groups[16])<m._ids(groups[32])<m._ids(groups[80])


def test_validation_test_leakage_and_changed_parent_gate_refused():
    samples,pairs,_,_,_,_=cohorts()
    with pytest.raises(ValueError,match='PARENT_8_COHORT_DRIFT'):
        m.arrange_cohorts(samples,pairs,{'bogus'})
    samples,pairs,_,_,_,_=cohorts()
    pairs[m.FINAL_GROUP][0]['sha256'][0]=pairs['counterfactual_probe'][0]['sha256'][0]
    with pytest.raises(ValueError,match='TRAIN_VALIDATION_TEST_OVERLAP|CAPTURE_IDENTITIES'):
        m.arrange_cohorts(samples,pairs,gate_for(samples,pairs))


def test_newly_generated_probe_prefix_unchanged_and_final_32_unique():
    known=p50.author_pairs(48,32)
    extended=p50.author_pairs(48,48)
    assert [p['sha256'] for p in known['counterfactual_probe']]==[
        p['sha256'] for p in extended['counterfactual_probe'][:32]]
    final=extended['counterfactual_probe'][32:]
    old=m._ids(known['counterfactual_train'])|m._ids(known['counterfactual_probe'])
    assert len(final)==16 and len(m._ids(final))==32
    assert m._ids(final).isdisjoint(old)
    assert {(p['family'],p['mode']) for p in final}==set(m.CELLS)


def test_set_model_real_backward_optimizer_gpu_mask_and_output():
    p55.torch.manual_seed(22)
    model=p55.model_for('set_prediction')
    baseline=m._copy_state(model)
    m.training_preflight(baseline,'cpu')
    assert all(torch.equal(v,model.state_dict()[k]) for k,v in baseline.items())
    assert len(baseline)>0


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA not available here')
def test_cuda_actual_training_and_inference_preflight():
    m.training_preflight(m._copy_state(p55.model_for('set_prediction')),'cuda')


def test_real_stage_train_and_val_checkpoint_selection_without_probe():
    samples,pairs,stages,val,val_pairs,groups=cohorts()
    weights=m._copy_state(p55.model_for('set_prediction'))
    result,state,rows=m.train_stage(weights,stages[8],groups[8],val,val_pairs,
        size=8,device='cpu',steps=4,interval=2,batch=2)
    assert result['steps']==4 and result['size']==8
    assert [x['step'] for x in result['checkpoints']]==[0,2,4]
    assert len(rows)==24 and set(x['section'] for x in rows)=={'train_8','validation_8'}
    assert set(state)==set(weights)
    assert all(torch.isfinite(x).all() for x in state.values())


def test_all_four_arms_train_from_identical_source_and_selection_uses_only_validation(monkeypatch):
    samples,pairs,stages,val,val_pairs,groups=cohorts()
    base=m._copy_state(p55.model_for('set_prediction'))
    checked=[]
    original=m.train_stage
    def audit(base_arg,train,train_pairs,validation,val_pairs,**k):
        assert all(torch.equal(base_arg[n],base[n]) for n in base)
        assert {x['section'] for x in train}=={'counterfactual_train'}
        assert {x['section'] for x in validation}=={'counterfactual_train'}
        checked.append(k['size'])
        return original(base_arg,train,train_pairs,validation,val_pairs,**k)
    monkeypatch.setattr(m,'train_stage',audit)
    results,chosen,weights,ledger,selected=m.learn_and_select(base,stages,val,val_pairs,
        groups,device='cpu',steps=2,interval=1,batch=2)
    assert checked==[8,16,32,80]
    assert chosen in m.SIZES and len(results)==4
    assert len(ledger)==sum(size+16 for size in m.SIZES)
    assert len(weights)==4*len(base)
    assert all(not p.requires_grad and p.grad is None for p in selected.parameters())


def test_finalize_real_training_and_test_after_selection(monkeypatch):
    samples,pairs,_,_,_,_=cohorts()
    base=m._copy_state(p55.model_for('set_prediction'))
    order=[]
    original=m.learn_and_select
    def select(*args,**kwargs):
        assert all('positive' not in x for x in samples if x['section']==m.FINAL_GROUP)
        order.append('selected')
        return original(*args,**kwargs)
    monkeypatch.setattr(m,'learn_and_select',select)
    outcome,weights,policy=m.finalize(samples,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',base=base,source_hashes={'g2_0055_frozen_source_step':320},
        gate_ids=gate_for(samples,pairs),encoder_sha='f'*64,
        steps=2,interval=1,batch=2)
    assert order==['selected']
    assert outcome['training']['optimizer_steps_total']==8
    assert len(outcome['final_evaluation'])==3
    assert outcome['final_evaluation'][m.FINAL_GROUP]['cases']==32
    assert len(outcome['_auxiliary_ledger']['rows'])==sum(m.SIZES)+16*4+96
    assert set(outcome['cohorts']['new_final_test_ids'])==m._ids(pairs[m.FINAL_GROUP])
    assert outcome['gates']['production_promotion'] is False
    assert policy['enabled'] is False and len(weights)==4*len(base)


def test_no_gold_reaches_inference_for_any_split(monkeypatch):
    samples,pairs,_,_,_,_=cohorts()
    base=m._copy_state(p55.model_for('set_prediction'))
    old=p55.infer_edges
    seen=[]
    def check(model,sample,arm):
        assert not set(sample).intersection({'positive','truth_count','labels','edges'})
        seen.append(sample['section'])
        return old(model,sample,arm)
    monkeypatch.setattr(p55,'infer_edges',check)
    m.finalize(samples,pairs,{'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',base=base,source_hashes={},gate_ids=gate_for(samples,pairs),
        encoder_sha='e'*64,steps=1,interval=1,batch=2)
    assert 'counterfactual_probe' in seen and m.FINAL_GROUP in seen


def test_exact_public_cli_dispatch_and_all_parent_paths(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    called={}
    def stub(**kwargs):
        called.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
            'selected_stage':32,'scale_results':{},'final_evaluation':{},
            'training':{'optimizer_steps_total':8},'gates':{'production_promotion':False}}}
    monkeypatch.setattr(m,'run',stub)
    with redirect_stdout(io.StringIO()) as stream:
        main(['frob-g2-0056-learning-scale','--root',str(tmp_path),'--device','cpu',
              '--steps','2','--checkpoint-interval','1'])
    assert called['candidate55_run'].endswith('graph-g2-literature-bakeoff-0055')
    assert called['diagnosis55_run'].endswith('graph-g2-0055-memorization-diagnosis')
    assert called['output'].endswith('graph-g2-0056-learning-scale')
    assert called['scale_steps']==2 and called['checkpoint_interval']==1
    assert called['train_pairs']==48 and called['probe_pairs']==16
    assert '"selected_stage": 32' in stream.getvalue()


def test_real_cuda_checkpoint_load_and_integrity(tmp_path):
    parent=Path('/mnt/data')
    files=[(mem55.REPORT,'g2_0055_memorization_diagnosis_report.json'),
           (mem55.POLICY,'g2_0055_memorization_diagnosis_policy.json'),
           (mem55.WEIGHTS,'g2_0055_memorization_checkpoints.safetensors'),
           (mem55.PREDICTIONS,'g2_0055_memorization_prediction_diagnostics.jsonl.gz'),
           (mem55.LEDGER,'g2_0055_memorization_candidate_per_case.jsonl.gz')]
    if not all((parent/b).exists() for a,b in files):
        pytest.skip('CUDA parent files not in test runtime')
    diag=tmp_path/'diagnosis';diag.mkdir()
    literature=tmp_path/'literature';literature.mkdir()
    bakeoff=tmp_path/'bakeoff';bakeoff.mkdir()
    for a,b in files:(diag/a).symlink_to(parent/b)
    for a,b in [(p55.REPORT,'g2_literature_bakeoff_report.json'),
               (p55.LEDGER,'g2_literature_bakeoff_per_case.jsonl.gz'),
               (p55.WEIGHTS,'g2_0055_development_models.safetensors'),
               (p55.POLICY,'g2_0055_frozen_policy.json')]:
        (literature/a).symlink_to(parent/b)
    (bakeoff/p54.REPORT).symlink_to(parent/'g2_architectural_bakeoff_report.json')
    original,receipt,gate=f55.verify_frozen_checkpoint(diag,literature,bakeoff)
    assert len(gate)==8
    assert all(not param.requires_grad for param in original.parameters())
    assert receipt['g2_0055_frozen_source_step']==320
    m.training_preflight(m._copy_state(original),'cpu')


def test_real_shared_candidate_capture_extension_and_report_writer_with_new_final_test(tmp_path,monkeypatch):
    """Exercise real G2-0051 callback, new-final capture, and atomic output writer.

    Mock only the Qwen network and prior report attestations. Unlike tests
    patching p52.run itself, this traverses the actual shared capture closure.
    """
    from types import SimpleNamespace as NS
    from tools.nanojev_gamer_v1_g2_candidate_construction_repair import SOURCE
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as root
    from tools import nanojev_gamer_v1_g2_counterfactual_learning as generator
    groups=generator.author_pairs(4,4)
    base=NS(d49=NS(g41=NS(verify_parent40=lambda *a,**k:({},{})),
            g42=NS(verify_parent41=lambda *a,**k:({},{},{})),
            b43=NS(verify_parent42=lambda *a,**k:({},{})),
            verify_parent48=lambda *a,**k:({'cohorts':{'train_learn_prompts':[]}}, {}, [])),
         d48=NS(d45=NS(d44=NS(verify_parent43=lambda *a,**k:({},{})),
                 verify_parent44=lambda *a,**k:({},{})),
                 verify_parent47=lambda *a,**k:({},{})),
         d46=NS(verify_parent45=lambda *a,**k:({},{})),
         d47=NS(verify_parent46=lambda *a,**k:({'cohorts':{'train_learn_prompts':[]}},{})),
         verify_parent49=lambda *a,**k:({},{}),author_pairs=generator.author_pairs)
    monkeypatch.setattr(root,'p50',base)
    monkeypatch.setattr(root,'verify_parent50',lambda *a,**k:({
        'cohorts':{'train_learn_prompts':[]},'training':{
        'author_pairs_requested':4,'probe_pairs_requested':4},'schema':'parent50.synthetic'},{}))
    monkeypatch.setattr(root,'parent50_probe_ledger',lambda *a,**k:{})
    monkeypatch.setattr(root,'replay_diagnostic',lambda *a,**k:{
        'exact_parent50_legacy_replay':True})
    monkeypatch.setattr(root,'summaries',lambda rows,pairs:{
        'counterfactual_probe':{'reachable_graphs':{'recovered_typed':8},
        'both_reachable_pairs':{'recovered_typed':4}}})
    monkeypatch.setattr(root,'audit_candidate_case',lambda rec,proposal,section,mode,pair:{
        'section':section,'prompt_sha256':SOURCE.base._sha(rec['row']['prompt']),
        'mode':mode,'pair':pair,'family':rec['row']['family'],
        'interface_repair':{'reachable':True,'missing_gold_edges':0}})
    monkeypatch.setattr(root,'candidate_decision',lambda *a,**k:{'scorer_retraining_performed':False})
    monkeypatch.setattr(SOURCE.base,'gold_binding_edges',lambda row:{('x','y','owns')})
    captures=[]
    def capture(material,tag,*,include_raw_g1):
        assert include_raw_g1
        captures.append((tag,len(material)))
        return [{'row':row,'candidate_repair':{},'g1_raw_interface':{}} for row in material]
    def source_run(*,diagnostic_callback,**k):
        return diagnostic_callback(capture_candidate_repair_rows=capture,
            assert_all_frozen=lambda:None,training=[],fitting=[],auditing=[])
    monkeypatch.setattr(SOURCE,'run',source_run)
    seen=[]
    def finish(samples,pairs,replay,report):
        assert len(samples)==32
        assert len(pairs['counterfactual_train'])==4
        assert len(pairs['counterfactual_probe'])==4
        assert len(pairs['fresh_counterfactual_probe'])==4
        assert len(pairs[m.FINAL_GROUP])==4
        assert [sum(s['section']==section for s in samples) for section in (
            'counterfactual_train','counterfactual_probe',
            'fresh_counterfactual_probe',m.FINAL_GROUP)]==[8,8,8,8]
        seen.extend(samples)
        assert all(s['labels']=={('x','y','owns')} for s in samples)
        return ({'training':{'optimizer_steps_total':4},
            '_auxiliary_ledger':{'filename':m.PREDICTIONS,'rows':[
                {'section':s['section'],'sha':s['prompt_sha256']} for s in samples]}},
            {'unit.weight':torch.ones(1)}, {'enabled':False})
    out=tmp_path/'candidate-new-test'
    report=root.run(bakeoff40_run='p40',oracle41_run='p41',rank42_run='p42',
        absolute43_run='p43',memorization44_run='p44',separability45_run='p45',
        multiprompt46_run='p46',crossover47_run='p47',occurrence48_run='p48',
        integrated49_run='p49',counterfactual50_run='p50',output=str(out),
        train_pairs=4,probe_pairs=4,fresh_probe_pairs=4,final_test_pairs=4,
        interface_extension=lambda raw,parent:{},
        interface_audit=lambda rec,proposal,extended,audit:{
            'reachable':True,'missing_gold_edges':0},
        interface_summary=lambda rows,pairs,replay:{'decision':{'coverage_targets_met':True}},
        interface_case_features=lambda raw,extended,section:{'section':section},
        interface_finalize=finish,alternate_schema=m.SCHEMA,alternate_status=m.STATUS,
        alternate_report_filename=m.REPORT,alternate_ledger_filename=m.LEDGER,
        alternate_weights_filename=m.WEIGHTS,alternate_policy_filename=m.POLICY,
        device='cpu')
    assert len(seen)==32 and len(captures)==4
    assert sum(q[0]=='g2_0056_final_holdout' for q in captures)==1
    assert report['schema']==m.SCHEMA
    assert report['new_optimizer_steps']==4
    assert (out/m.REPORT).exists() and (out/m.PREDICTIONS).exists()
    assert (out/m.WEIGHTS).exists() and (out/m.POLICY).exists()
    assert report['scoring_experiment']['auxiliary_prediction_ledger']['rows']==32
    assert all(s['section']==m.FINAL_GROUP for s in seen[-8:])


def test_run_actual_scoring_callbacks_and_final_section_compatibility(tmp_path,monkeypatch):
    """Run m.run -> shared p52 entry -> 0056 scorer callbacks with real training."""
    from safetensors.torch import save_file
    base=m._copy_state(p55.model_for('set_prediction'))
    cases,pairs,_,_,_,_=cohorts()
    gate=gate_for(cases,pairs)
    prior48=tmp_path/'parent48';prior48.mkdir()
    encoder=d48.OccurrenceEvidenceScorer(8)
    save_file(encoder.state_dict(),str(prior48/d48.WEIGHTS))
    digest=d48.backbone.sha256(prior48/d48.WEIGHTS)
    (prior48/d48.REPORT).write_text(json.dumps({'schema':d48.SCHEMA,'weights_sha256':digest}))
    monkeypatch.setattr(g53,'verify_parent52',lambda *a,**k:({},{}))
    monkeypatch.setattr(p54,'verify_parent53',lambda *a,**k:({},{},{}))
    monkeypatch.setattr(p55,'verify_parent54',lambda *a,**k:({},{},{}))
    monkeypatch.setattr(f55,'verify_frozen_checkpoint',lambda *a,**k:(
        m._load_state(base).eval().requires_grad_(False),{'g2_0055_frozen_source_step':320},gate))
    feature_sections=[]
    old_features=g53.frozen_features
    def frozen_features(raw,expanded,encoder,section):
        feature_sections.append(section)
        return {'section':section}
    monkeypatch.setattr(g53,'frozen_features',frozen_features)
    captured=[]
    def call(**kwargs):
        captured.append(kwargs)
        assert kwargs['final_test_pairs']==16
        assert kwargs['fresh_probe_pairs']==16
        assert kwargs['scoring_schema']==m.SCHEMA
        feature=kwargs['scoring_case_features']
        assert feature({}, {},m.FINAL_GROUP)['section']==m.FINAL_GROUP
        assert feature({}, {},'counterfactual_probe')['section']=='counterfactual_probe'
        # Reconstruct all labels at this test boundary exactly as shared p51 does.
        outcome,weights,policy=kwargs['scoring_finalize'](
            cases,pairs,{'exact_parent50_legacy_replay':True},
            {'interface_repair':{'decision':{'coverage_targets_met':True}}})
        assert outcome['selected_stage'] in m.SIZES
        assert outcome['training']['optimizer_steps_total']==4
        assert outcome['final_evaluation'][m.FINAL_GROUP]['cases']==32
        assert len(weights)==4*len(base)
        assert policy['enabled'] is False
        return {'status':m.STATUS,'scoring_experiment':outcome}
    monkeypatch.setattr(p52,'run',call)
    actual=m.run(diagnosis55_run='mem55',candidate55_run='lit55',
        candidate54_run='bake54',candidate53_run='p53',candidate52_run='p52',
        candidate51_run='p51',occurrence48_run=prior48,counterfactual50_run='p50',
        output=tmp_path/'output',device='cpu',scale_steps=1,checkpoint_interval=1,
        batch_prompts=2)
    assert actual['status']==m.STATUS
    assert feature_sections==['fresh_counterfactual_probe','counterfactual_probe']
    assert len(captured)==1
