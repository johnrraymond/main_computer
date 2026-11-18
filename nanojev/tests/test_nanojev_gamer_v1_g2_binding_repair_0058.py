"""G2-0058 binding diagnosis, causal repair and actual CLI/report integration."""
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_binding_repair_0058 as m
from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as p57
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53
from tools import nanojev_gamer_v1_graph_curriculum as cli


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def previous_fixture():
    path=Path(__file__).with_name('test_nanojev_gamer_v1_g2_counterfactual_bakeoff_0057.py')
    spec=importlib.util.spec_from_file_location('binding57fixture',path)
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    return fixture


def two_samples():
    return previous_fixture().samples_pair()


def groups_0058():
    f=previous_fixture()
    samples,pairs,gate=f.synthetic_cohorts()
    fixture=f._fixture_module()
    for j in range(32,48):
        hashes=[]
        family=('allocation','encounter')[j%2]
        mode=('role_swap','renaming')[(j//2)%2]
        for side in (0,1):
            row=fixture.sample(m.p57.FINAL_GROUP,str(j),side,family,mode)
            row['prompt_sha256']+='-0058'
            anchors=[(k*3,k*3+2,'actor') for k in range(7)]
            edges=[(anchors[int(a)],anchors[int(b)],g53.RELATIONS[int(r)])
                   for a,b,r in zip(row['src'],row['dst'],row['relation'])]
            row['edges']=edges;row['labels']={edges[0],edges[4]}
            samples.append(row);hashes.append(row['prompt_sha256'])
        pairs[m.p57.FINAL_GROUP].append({'id':'final-'+str(j),'sha256':hashes,
                    'family':family,'mode':mode})
    for row in samples:
        row.pop('positive',None);row.pop('truth_count',None)
    parent={'scoring_experiment':{
        'source_parent56_hashes':{},
        'cohorts':{'train_sha256':sorted(m.p56._ids(
                       [p for c in m.p56.CELLS for p in
                        [x for x in pairs[m.p57.TRAIN_GROUP]
                         if (x['family'],x['mode'])==c][2:]])),
            'validation_sha256':sorted(m.p56._ids(
                       [p for c in m.p56.CELLS for p in
                        [x for x in pairs[m.p57.TRAIN_GROUP]
                         if (x['family'],x['mode'])==c][:2]])),
            'new_final_sha256':sorted(m.p56._ids(pairs[m.p57.FINAL_GROUP][16:32]))}}}
    return samples,pairs,parent


def test_diagnostic_reference_decoder_and_oracle_ceiling():
    a,b=two_samples()
    torch.manual_seed(460558)
    model=p55.model_for('set_prediction')
    with torch.inference_mode():
        result,records=m.diagnose(model,[a,b],[],details=True)
    assert result['gold_edges']==4 and result['candidate_reachable']==2
    assert result['posthoc_oracle']['complete']=={'exact':2,'missing':0,'extra':0}
    assert len(records)==2
    assert all(len(r['matched_gold'])==2 for r in records)
    assert all('admissible_edge_rank' in item for r in records for item in r['matched_gold'])
    assert all('same_label_wrong_destination_margin' in item for r in records for item in r['matched_gold'])
    assert result['posthoc_assignment_uses_gold'] is True
    assert len([s for r in records for s in r['matched_gold']])==4


def test_same_objective_for_control_but_hard_loss_changes_gradient():
    a,b=two_samples();rows={'one':a,'two':b}
    pairs=[{'sha256':('one','two'),'mode':'role_swap'}]
    model=p55.model_for('set_prediction')
    control=m.batch_loss(model,pairs,rows,'A_continue')
    reference=p57.batch_objective(model,pairs,rows,'D_combined')
    assert torch.allclose(control,reference,atol=1e-6,rtol=1e-6)
    repair=m.batch_loss(model,pairs,rows,'B_hard_binding')
    assert float(repair.detach())>float(control.detach())
    model.zero_grad(set_to_none=True)
    repair.backward()
    assert all(p.grad is None or torch.isfinite(p.grad).all() for p in model.parameters())
    assert any(p.grad is not None and float(p.grad.abs().sum())>0 for p in model.parameters())
    m.preflight(m.p56._copy_state(model),'cpu')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA unavailable')
def test_actual_optimization_preflight_on_cuda():
    m.preflight(m.p56._copy_state(p55.model_for('set_prediction')),'cuda')


def test_0058_cohort_isolation_and_exact_old_0057_prefix():
    rows,pairs,parent=groups_0058()
    training,trainmap,val,valpairs,hold,new,newpairs=m.prepare_cohorts(rows,pairs,parent)
    assert len(training)==40 and len(trainmap)==80 and len(val)==16
    assert len(newpairs)==16 and len(new)==32
    assert set(trainmap).isdisjoint(m.p56._ids(newpairs))
    assert all('positive' not in x and 'training_node_ids' not in x for x in new)
    assert m.p56._ids(pairs[m.p57.FINAL_GROUP][32:])==m.p56._ids(newpairs)
    changed=copy.deepcopy(parent)
    changed['scoring_experiment']['cohorts']['new_final_sha256']=['drift']*32
    with pytest.raises(ValueError,match='PARENT57_FINAL_DRIFT'):
        m.prepare_cohorts(groups_0058()[0],pairs,changed)
    rows,pairs,parent=groups_0058()
    pairs[m.p57.FINAL_GROUP][47]['sha256'][0]=pairs[m.p57.FINAL_GROUP][0]['sha256'][0]
    with pytest.raises(ValueError,match='FINAL_DUPLICATION|NEW_FINAL_CAPTURE_IDENTITY'):
        m.prepare_cohorts(rows,pairs,parent)


def test_train_on_training_pairs_only_and_final_not_used_for_selection(monkeypatch):
    samples,pairs,parent=groups_0058()
    base=m.p56._copy_state(p55.model_for('set_prediction'))
    selected=[];original=m.train_one
    def counted(*a,**k):
        assert all('positive' not in x for x in samples if x['section']!=m.p57.TRAIN_GROUP)
        selected.append(k['arm']);return original(*a,**k)
    monkeypatch.setattr(m,'train_one',counted)
    # Full callback with 2 CPU steps per arm, including real 0055 inference.
    exp,weights,policy=m.finalize(samples,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        base=base,parent_hashes={'checkpoint':'synthetic'},parent_report=parent,
        encoder_sha='1'*64,device='cpu',steps=2,interval=1)
    assert selected==list(m.ARMS)
    assert len(weights)==2*len(base)
    assert exp['training']['optimizer_steps_total']==4
    assert exp['diagnosis']['posthoc_assignment_uses_gold'] is True
    assert exp['gates']['new_final_test_scored_once_per_frozen_arm'] is True
    assert exp['final_evaluation_frozen_both_arms']['new_0058_final']['A_continue']['cases']==32
    assert exp['final_evaluation_frozen_both_arms']['new_0058_final']['B_hard_binding']['cases']==32
    assert policy['enabled'] is False and policy['production_promotion'] is False
    assert all('final_evaluation' not in x for x in exp['arms'].values())
    assert len(exp['_auxiliary_ledger']['rows'])<4096


def test_actual_0057_saved_parent_checkpoint_receipt(tmp_path):
    import os
    actual=Path('/mnt/data')
    if not (actual/m.p57.REPORT).exists():pytest.skip('Parent57 external artifacts unavailable')
    folder=tmp_path/'parent57';folder.mkdir()
    for name in (m.p57.REPORT,m.p57.LEDGER,m.p57.PREDICTIONS,
                 m.p57.WEIGHTS,m.p57.POLICY):
        (folder/name).write_bytes((actual/name).read_bytes())
    state,hashes,report=m.verify_parent57(folder)
    assert len(state)==len(p55.model_for('set_prediction').state_dict())
    assert len(hashes)==5
    assert report['scoring_experiment']['selected_arm']=='D_combined'
    (folder/m.p57.PREDICTIONS).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='PARENT57_PREDICTIONS_HASH'):
        m.verify_parent57(folder)


def test_cli_actual_run_route(monkeypatch,tmp_path):
    passed={}
    def stub(**kwargs):
        passed.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
            'diagnosis':{'endpoint_learning_signal':True},
            'selected_development_arm':'A_continue',
            'arms':{'A_continue':{'validation':{},'training':{},'best_step':0}},
            'final_evaluation_frozen_both_arms':{},
            'training':{'optimizer_steps_total':2},'gates':{'production_promotion':False}}}
    monkeypatch.setattr(m,'run',stub)
    with redirect_stdout(io.StringIO()) as output:
        cli.main(['frob-g2-0058-binding-repair','--root',str(tmp_path),
            '--device','cpu','--steps','2','--checkpoint-interval','1'])
    assert passed['parent57_run'].endswith('graph-g2-0057-counterfactual-bakeoff')
    assert passed['scale56_run'].endswith('graph-g2-0056-learning-scale')
    assert passed['output'].endswith('graph-g2-0058-binding-repair')
    assert passed['steps']==2 and passed['checkpoint_interval']==1
    assert passed['train_pairs']==48 and passed['probe_pairs']==16
    assert 'endpoint_learning_signal' in output.getvalue()


def test_generating_0058_final_preserves_0057_identity_prefix():
    gen=m.p56.p52.p51.p50
    old=gen.author_pairs(48,64)
    grown=gen.author_pairs(48,80)
    a=old['counterfactual_probe'];b=grown['counterfactual_probe']
    assert [(p['id'],p['sha256']) for p in a]==[(p['id'],p['sha256']) for p in b[:64]]
    assert len(b[64:])==16 and not m.p56._ids(a)&m.p56._ids(b[64:])


def test_shared_capture_and_actual_0058_report_writer(monkeypatch,tmp_path):
    """Exercise the real common capture callback and atomic artifact writer."""
    from types import SimpleNamespace as NS
    from tools.nanojev_gamer_v1_g2_candidate_construction_repair import SOURCE
    from tools import nanojev_gamer_v1_g2_candidate_construction_repair as core
    from tools import nanojev_gamer_v1_g2_counterfactual_learning as generator
    monkeypatch.setattr(core,'p50',NS(d49=NS(g41=NS(verify_parent40=lambda *a,**k:({},{})),
        g42=NS(verify_parent41=lambda *a,**k:({},{},{})),
        b43=NS(verify_parent42=lambda *a,**k:({},{})),
        verify_parent48=lambda *a,**k:({'cohorts':{'train_learn_prompts':[]}}, {}, [])),
        d48=NS(d45=NS(d44=NS(verify_parent43=lambda *a,**k:({},{})),
            verify_parent44=lambda *a,**k:({},{})),
        verify_parent47=lambda *a,**k:({},{})),
        d46=NS(verify_parent45=lambda *a,**k:({},{})),
        d47=NS(verify_parent46=lambda *a,**k:({'cohorts':{'train_learn_prompts':[]}},{})),
        verify_parent49=lambda *a,**k:({},{}),author_pairs=generator.author_pairs))
    monkeypatch.setattr(core,'verify_parent50',lambda *a,**k:({
        'cohorts':{'train_learn_prompts':[]},'training':{
            'author_pairs_requested':4,'probe_pairs_requested':4},
        'schema':'parent50.test'},{}))
    monkeypatch.setattr(core,'parent50_probe_ledger',lambda *a,**k:{})
    monkeypatch.setattr(core,'replay_diagnostic',lambda *a,**k:{
        'exact_parent50_legacy_replay':True})
    monkeypatch.setattr(core,'summaries',lambda rows,pairs:{
        'counterfactual_probe':{'reachable_graphs':{'recovered_typed':8},
                                'both_reachable_pairs':{'recovered_typed':4}}})
    monkeypatch.setattr(core,'audit_candidate_case',lambda rec,proposal,section,mode,pair:{
        'section':section,'prompt_sha256':SOURCE.base._sha(rec['row']['prompt']),
        'mode':mode,'pair':pair,'family':rec['row']['family'],
        'interface_repair':{'reachable':True,'missing_gold_edges':0}})
    monkeypatch.setattr(core,'candidate_decision',lambda *a,**k:{'scorer_retraining_performed':False})
    monkeypatch.setattr(SOURCE.base,'gold_binding_edges',lambda row:{('x','y','owns')})
    def capture(material,tag,*,include_raw_g1):
        return [{'row':row,'candidate_repair':{},'g1_raw_interface':{}} for row in material]
    monkeypatch.setattr(SOURCE,'run',lambda *,diagnostic_callback,**k:diagnostic_callback(
        capture_candidate_repair_rows=capture,assert_all_frozen=lambda:None,
        training=[],fitting=[],auditing=[]))
    def finish(samples,pairs,replay,report):
        assert len(samples)==48 # 4 train + 4 historical + 4 fresh + 12 final pairs
        assert len(pairs[m.p57.FINAL_GROUP])==12
        assert sum('training_node_ids' in s for s in samples)==8
        assert all('training_node_ids' not in s for s in samples if s['section']!=m.p57.TRAIN_GROUP)
        assert sum(s['section']==m.p57.FINAL_GROUP for s in samples)==24
        return ({'training':{'optimizer_steps_total':2},
                 '_auxiliary_ledger':{'filename':m.PREDICTIONS,
                    'rows':[{'sha':s['prompt_sha256']} for s in samples]}},
                 {'A_continue.dummy':torch.ones(1)},
                 {'enabled':False,'production_promotion':False})
    root=tmp_path/'output'
    report=core.run(bakeoff40_run='a',oracle41_run='b',rank42_run='c',absolute43_run='d',
        memorization44_run='e',separability45_run='f',multiprompt46_run='g',
        crossover47_run='h',occurrence48_run='i',integrated49_run='j',
        counterfactual50_run='k',output=str(root),train_pairs=4,probe_pairs=4,
        fresh_probe_pairs=4,final_test_pairs=12,
        interface_extension=lambda raw,proposal:{'anchors':[(0,1,'actor')]},
        interface_audit=lambda rec,p,e,a:{'reachable':True,'missing_gold_edges':0},
        interface_summary=lambda rows,pairs,replay:{'decision':{'coverage_targets_met':True}},
        interface_case_features=lambda raw,extra,section:{'node':torch.zeros(1,96),
                                                            'section':section},
        interface_training_alignment=lambda row,extra,case:{'training_node_ids':('train-id',)},
        interface_finalize=finish,
        alternate_schema=m.SCHEMA,alternate_status=m.STATUS,
        alternate_report_filename=m.REPORT,alternate_ledger_filename=m.LEDGER,
        alternate_weights_filename=m.WEIGHTS,alternate_policy_filename=m.POLICY,
        device='cpu')
    assert report['scoring_experiment']['auxiliary_prediction_ledger']['rows']==48
    assert report['new_optimizer_steps']==2
    assert (root/m.REPORT).is_file() and (root/m.PREDICTIONS).is_file()
    assert (root/m.WEIGHTS).is_file() and (root/m.POLICY).is_file()
    assert report['scoring_weights_sha256']==m._sha(root/m.WEIGHTS)
    assert report['scoring_policy_sha256']==m._sha(root/m.POLICY)
