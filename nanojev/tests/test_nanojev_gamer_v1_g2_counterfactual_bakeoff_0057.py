"""G2-0057 controlled four-arm paired-consistency behavioral and CLI tests."""
import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest
import torch
from safetensors.torch import save_file

from tools import nanojev_gamer_v1_g2_counterfactual_bakeoff_0057 as m
from tools import nanojev_gamer_v1_g2_learning_scale_0056 as p56
from tools import nanojev_gamer_v1_g2_literature_bakeoff_0055 as p55
from tools import nanojev_gamer_v1_g2_repaired_graph_learning as g53


@pytest.fixture(autouse=True)
def threads():
    old=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def _fixture_module():
    path=Path(__file__).with_name('test_nanojev_gamer_v1_g2_literature_bakeoff_0055.py')
    spec=importlib.util.spec_from_file_location('g2_0057_parent_fixture',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def synthetic_cohorts():
    fixture=_fixture_module()
    rows,pairs=fixture.cohorts()
    final=[]
    for j in range(32):
        family=('allocation','encounter')[j%2]
        mode=('role_swap','renaming')[(j//2)%2]
        hashes=[]
        for side in (0,1):
            row=fixture.sample(m.FINAL_GROUP,str(j),side,family,mode)
            if j>=16:row['prompt_sha256']+='-0057'
            anchors=[(k*3,k*3+2,'actor') for k in range(7)]
            edges=[(anchors[int(a)],anchors[int(b)],g53.RELATIONS[int(r)])
                   for a,b,r in zip(row['src'],row['dst'],row['relation'])]
            row['edges']=edges;row['labels']={edges[0],edges[4]}
            rows.append(row);hashes.append(row['prompt_sha256'])
        final.append({'id':'final-'+str(j),'sha256':hashes,
                      'family':family,'mode':mode})
    pairs[m.FINAL_GROUP]=final
    for row in rows:
        if row['section']==m.TRAIN_GROUP:
            row['training_node_ids']=tuple('n'+str(j) for j in range(len(row['node'])))
        row.pop('positive',None)
        row.pop('truth_count',None)
    chosen={}
    for pair in pairs[m.TRAIN_GROUP]:
        chosen.setdefault((pair['family'],pair['mode']),[]).append(pair)
    ids=m.p56._ids([chosen[cell][2] for cell in m.CELLS])
    return rows,pairs,ids


def samples_pair():
    fixture=_fixture_module()
    a=fixture.sample('counterfactual_train','one',0,'allocation','role_swap')
    a['training_node_ids']=tuple('n'+str(i) for i in range(7))
    b=copy.deepcopy(a)
    a['prompt_sha256']='one'
    b['prompt_sha256']='two'
    # Permute the positional object-occurrence vocabulary. Same authored nodes;
    # the identity correspondence is NOT a shared raw tensor position.
    perm=[2,0,1,3,6,4,5]
    inv={old:new for new,old in enumerate(perm)}
    b['node']=a['node'][perm].clone()
    b['kinds']=a['kinds'][perm].clone()
    b['margin']=a['margin'][perm].clone()
    b['src']=torch.tensor([inv[int(i)] for i in a['src']])
    b['dst']=torch.tensor([inv[int(i)] for i in a['dst']])
    b['training_node_ids']=tuple(a['training_node_ids'][i] for i in perm)
    return a,b


def test_same_supervised_loss_as_parent_0055():
    a,_=samples_pair()
    model=p55.model_for('set_prediction')
    forward=model(a)
    objective,_=m._matched_set_loss(forward,a)
    reference=p55.set_objective(model,a)
    assert torch.allclose(objective,reference,atol=1e-6,rtol=1e-6)


def test_role_permutation_consistency_compares_logical_not_surface_nodes():
    a,b=samples_pair()
    assert a['training_node_ids']!=b['training_node_ids']
    assert set(m.canonical_edges(a))==set(m.canonical_edges(b))
    model=p55.model_for('set_prediction')
    fa,fb=model(a),model(b)
    _,aa=m._matched_set_loss(fa,a);_,ab=m._matched_set_loss(fb,b)
    regularizer=m.paired_consistency(a,b,fa,fb,aa,ab)
    assert float(regularizer)>=0 and bool(torch.isfinite(regularizer))
    bad=copy.deepcopy(b)
    bad['training_node_ids']=tuple('swapped_'+x for x in b['training_node_ids'])
    with pytest.raises(ValueError,match='AUTHOR_PAIRED_SEMANTICS_CHANGED'):
        m.paired_consistency(a,bad,fa,fb,aa,ab)


def test_selection_of_objectives_by_mode():
    assert m.arm_uses_pair('A_control','renaming') is False
    assert m.arm_uses_pair('A_control','role_swap') is False
    assert m.arm_uses_pair('B_renaming','renaming') is True
    assert m.arm_uses_pair('B_renaming','role_swap') is False
    assert m.arm_uses_pair('C_role_swap','role_swap') is True
    assert m.arm_uses_pair('C_role_swap','renaming') is False
    assert all(m.arm_uses_pair('D_combined',mode) for mode in ('renaming','role_swap'))


def test_real_pair_gradient_for_every_arm():
    a,b=samples_pair()
    rows={'a':a,'b':b}
    model=p55.model_for('set_prediction')
    with torch.no_grad():
        control=m.batch_objective(model,[{'mode':'renaming','sha256':('a','b')}],rows,'A_control')
        rename=m.batch_objective(model,[{'mode':'renaming','sha256':('a','b')}],rows,'B_renaming')
        swap=m.batch_objective(model,[{'mode':'renaming','sha256':('a','b')}],rows,'C_role_swap')
    assert float(rename)>=float(control)-1e-6
    assert torch.allclose(control,swap,atol=1e-6)
    m.preflight(m.p56._copy_state(model),'cpu')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA unavailable')
def test_real_paired_optimizer_and_alignment_on_cuda():
    m.preflight(m.p56._copy_state(p55.model_for('set_prediction')),'cuda')


def test_balanced_cohort_pair_boundaries_and_new_holdout_opacity():
    cases,pairs,gate=synthetic_cohorts()
    (training,train_map,validation,val_pairs,holdouts,previous,
     fresh)=m.prepare_cohorts(cases,pairs,gate)
    assert len(training)==40 and len(train_map)==80
    assert len(validation)==16 and len(val_pairs)==8
    assert len(previous)==len(fresh)==16
    assert len(m.p56._ids(fresh))==32
    parent={'training_ids':list(m.p56._ids(training)),
            'validation_ids':list(m.p56._ids(val_pairs)),
            'new_final_test_ids':list(m.p56._ids(previous)),
            'memorization_gate_ids':list(gate)}
    m.prepare_cohorts(synthetic_cohorts()[0],pairs,gate,parent)
    parent['new_final_test_ids']=['wrong']*32
    with pytest.raises(ValueError,match='PARENT56_COHORT_DRIFT'):
        m.prepare_cohorts(synthetic_cohorts()[0],pairs,gate,parent)
    assert all(len([p for p in training if p['mode']==mode])==20
               for mode in ('renaming','role_swap'))
    assert set().union(*(m.p56._ids(xs) for xs in [previous,fresh,training,val_pairs])) == (
        m.p56._ids(previous)|m.p56._ids(fresh)|m.p56._ids(training)|m.p56._ids(val_pairs))
    assert all('positive' not in row for rows in holdouts.values() for row in rows)
    assert all('training_node_ids' not in row for rows in holdouts.values() for row in rows)
    assert all(row['truth_count']==len(row['positive']) for row in train_map.values())
    assert set(train_map).isdisjoint(m.p56._ids(val_pairs)|m.p56._ids(fresh))


def test_parent_0055_gate_mismatch_and_holdout_overlap_refused():
    cases,pairs,gate=synthetic_cohorts()
    with pytest.raises(ValueError,match='ORIGINAL_0055_GATE_DRIFT'):
        m.prepare_cohorts(cases,pairs,{'wrong'})
    cases,pairs,gate=synthetic_cohorts()
    pairs[m.FINAL_GROUP][-1]['sha256'][0]=pairs['counterfactual_probe'][0]['sha256'][0]
    with pytest.raises(ValueError,match='PROMPT_SPLIT_LEAK|CAPTURE_IDENTITY'):
        m.prepare_cohorts(cases,pairs,gate)


def test_candidate_annotations_never_create_test_alignment():
    row={'graph':{'nodes':[{'id':'actor-1','kind':'actor','span':[0,3]},
                          {'id':'action-1','kind':'action','span':[5,9]}]}}
    raw={'anchors':[(0,3,'actor'),(5,9,'action'),(8,10,'actor')]}
    result=m.training_node_alignment(row,raw,{'node':torch.zeros(3,96)})
    assert result['training_node_ids']==('actor-1','action-1',None)
    a,b=samples_pair()
    assert m._safe_evaluate(p55.model_for('set_prediction'),[a,b],
        [{'sha256':('one','two')}],'test')[0]['cases']==2


def test_matched_batch_order_and_equal_supervision():
    rows,pairs,gate=synthetic_cohorts()
    train,*_=m.prepare_cohorts(rows,pairs,gate)
    batch1=[[p['id'] for p in x] for x in m.pair_batches(train,40,123)]
    batch2=[[p['id'] for p in x] for x in m.pair_batches(train,40,123)]
    assert batch1==batch2 and len(batch1)==40
    flat=[name for group in batch1[:20] for name in group]
    assert len(flat)==len(set(flat))==40


def test_real_four_arm_optimizer_and_final_evaluation_after_selection(monkeypatch):
    samples,pairs,gate=synthetic_cohorts()
    base=m.p56._copy_state(p55.model_for('set_prediction'))
    original=m.train_and_select
    observe=[]
    def instrument(*args,**kwargs):
        assert all('positive' not in x for x in samples if x['section']!=m.TRAIN_GROUP)
        observe.append('selected')
        return original(*args,**kwargs)
    monkeypatch.setattr(m,'train_and_select',instrument)
    exp,weights,policy=m.finalize(samples,pairs,
        {'exact_parent50_legacy_replay':True},
        {'interface_repair':{'decision':{'coverage_targets_met':True}}},
        device='cpu',base=base,hashes={'report':'synthetic'},gate_ids=gate,
        encoder_sha='a'*64,steps=2,interval=1)
    assert observe==['selected']
    assert len(exp['arms'])==4 and exp['selected_arm'] in m.ARMS
    assert exp['training']['optimizer_steps_total']==8
    assert exp['final_evaluation_selected_arm_only']['new_0057_final']['cases']==32
    assert exp['gates']['new_final_test_evaluated_once'] is True
    assert len(weights)==4*len(base)
    assert 'mean_abs_cardinality_error' in exp['arms']['A_control']['validation']
    assert len(exp['_auxiliary_ledger']['rows'])==4*(80+16)+128
    assert policy['enabled'] is False and exp['production_promotion'] is False


def test_cli_routes_real_run_path(tmp_path,monkeypatch):
    from tools.nanojev_gamer_v1_graph_curriculum import main
    called={}
    def stub(**kwargs):
        called.update(kwargs)
        return {'status':m.STATUS,'scoring_experiment':{
            'selected_arm':'D_combined','arms':{},'final_evaluation_selected_arm_only':{},
            'training':{'optimizer_steps_total':8},
            'gates':{'production_promotion':False}}}
    monkeypatch.setattr(m,'run',stub)
    with redirect_stdout(io.StringIO()) as stream:
        main(['frob-g2-0057-counterfactual-bakeoff','--root',str(tmp_path),
              '--device','cpu','--steps','2','--checkpoint-interval','1'])
    assert called['scale56_run'].endswith('graph-g2-0056-learning-scale')
    assert called['output'].endswith('graph-g2-0057-counterfactual-bakeoff')
    assert called['steps']==2 and called['checkpoint_interval']==1
    assert called['train_pairs']==48 and called['probe_pairs']==16
    assert 'D_combined' in stream.getvalue()


def test_actual_parent56_checkpoint_receipts(tmp_path):
    stage=tmp_path/'parent';stage.mkdir()
    model=p55.model_for('set_prediction')
    state=m.p56._copy_state(model)
    save_file({'stage_80.'+name:value for name,value in state.items()},
              str(stage/p56.WEIGHTS))
    for f in (p56.POLICY,p56.LEDGER,p56.PREDICTIONS):
        (stage/f).write_bytes(b'123')
    doc={'schema':p56.SCHEMA,'status':p56.STATUS,
         'production_promotion':False,'sealed_test_used':False,
         'scoring_weights_sha256':m._sha(stage/p56.WEIGHTS),
         'scoring_policy_sha256':m._sha(stage/p56.POLICY),
         'ledger_sha256':m._sha(stage/p56.LEDGER),
         'scoring_experiment':{
            'selected_stage':80,'gates':{'final_new_cohort_evaluated_once':True,
                                        'production_promotion':False},
            'training':{'steps_per_stage':960,'optimizer_steps_total':3840},
            'scale_results':{'80':{'best_step':720}},
            'cohorts':{'validation_graphs':16,
                       'training_ids':[str(i) for i in range(80)],
                       'validation_ids':[str(i) for i in range(16)],
                       'memorization_gate_ids':[str(i) for i in range(8)],
                       'new_final_test_ids':[str(i) for i in range(32)]},
            'auxiliary_prediction_ledger':{'filename':p56.PREDICTIONS,
                                            'sha256':m._sha(stage/p56.PREDICTIONS)}}}
    (stage/p56.REPORT).write_text(json.dumps(doc))
    got,hashes,report=m.verify_parent56(stage)
    assert set(got)==set(state) and len(hashes)==5
    assert report['scoring_experiment']['selected_stage']==80
    (stage/p56.POLICY).write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='PARENT56_HASH'):
        m.verify_parent56(stage)


def test_new_final_cohort_is_prefix_stable_against_0056():
    gen=m.p56.p52.p51.p50
    original=gen.author_pairs(48,48)
    grown=gen.author_pairs(48,64)
    before=original['counterfactual_probe']
    after=grown['counterfactual_probe']
    assert [(p['id'],p['sha256']) for p in before]==[
        (p['id'],p['sha256']) for p in after[:48]]
    new=after[48:]
    assert len(new)==16 and len(m.p56._ids(new))==32
    assert not m.p56._ids(new)&m.p56._ids(before)


def test_core_shared_scoring_capture_records_train_alignment_and_never_test(monkeypatch,tmp_path):
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
        'schema':'parent50.synthetic'},{}))
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
    def expanded(raw,parent):return {'anchors':[(0,1,'actor')]}
    def aligned(row,extra,case):
        return {'training_node_ids':('train-id',)}
    def finish(samples,pairs,replay,report):
        assert len(samples)==40 # train 8, probe 8, fresh 8, final 16
        assert sum('training_node_ids' in row for row in samples)==8
        assert all('training_node_ids' not in row for row in samples[8:])
        return ({'training':{'optimizer_steps_total':1},
             '_auxiliary_ledger':{'filename':m.PREDICTIONS,
                 'rows':[{'sha':row['prompt_sha256']} for row in samples]}},
             {'a':torch.ones(1)}, {'enabled':False})
    report=core.run(bakeoff40_run='a',oracle41_run='b',rank42_run='c',absolute43_run='d',
        memorization44_run='e',separability45_run='f',multiprompt46_run='g',
        crossover47_run='h',occurrence48_run='i',integrated49_run='j',
        counterfactual50_run='k',output=str(tmp_path/'output'),
        train_pairs=4,probe_pairs=4,fresh_probe_pairs=4,final_test_pairs=8,
        interface_extension=expanded,interface_audit=lambda rec,p,e,a:{
            'reachable':True,'missing_gold_edges':0},
        interface_summary=lambda rows,pairs,replay:{'decision':{'coverage_targets_met':True}},
        interface_case_features=lambda raw,extra,section:{'node':torch.zeros(1,96),
                                                          'section':section},
        interface_training_alignment=aligned,interface_finalize=finish,
        alternate_schema=m.SCHEMA,alternate_status=m.STATUS,
        alternate_report_filename=m.REPORT,alternate_ledger_filename=m.LEDGER,
        alternate_weights_filename=m.WEIGHTS,alternate_policy_filename=m.POLICY,
        device='cpu')
    assert report['scoring_experiment']['auxiliary_prediction_ledger']['rows']==40
    assert (tmp_path/'output'/m.REPORT).exists()
    assert (tmp_path/'output'/m.PREDICTIONS).exists()
    assert (tmp_path/'output'/m.WEIGHTS).exists()
