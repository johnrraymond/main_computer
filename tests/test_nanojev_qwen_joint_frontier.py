"""No GPU tests for the continuous joint T and Balanced Hawks/Doves frontier."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_hdb_frontier as legacy_hdb
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_lm_frontier_stress_repair as frontier
from tools import nanojev_qwen_lm_frontier_tasks as math_tasks
from tools import nanojev_qwen_shotgun as shotgun


def args(tmp_path, **changes):
    a=dict(output=str(tmp_path/'numeric'),frontier_output=None,joint_output=None,
        stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
        text_checkpoint=str(tmp_path/'text'/'best.pt'),
        hdb_steps=900,hdb_check_every=100,hdb_max_offset=64,
        hdb_t_replay_per_batch=1,hdb_max_seq=768,hdb_max_tokens=1024,
        hdb_max_new_tokens=12,competency_hdb_cases=5,
        competency_legacy_decode=False,competency_progress_every=100,
        terminal_mode='numeric',max_rung=8,batch_size=4,eval_batch=8,
        device='cpu',seed=435,lr=0.00005,radii='0,1,2,3,4',
        operand_limit=1024,frontier_max_radius=64,frontier_steps=1200,
        frontier_check_every=100,frontier_eval_cases=192,frontier_train_cases=128,
        max_seq=192,max_tokens=256,max_new_tokens=64,
        frontier_neighbor_probe_cap=30,frontier_repair_center_cap=30,
        frontier_harvest_cases=8,frontier_repair_steps=10,local_radius=4,
        joint_steps=100,joint_check_every=25,joint_t_max_radius=256,
        joint_t_refresh_every=5,joint_t_eval_per_shell=45,
        joint_t_train_cases_per_shell=81,joint_hdb_min_share=.4,
        joint_hdb_max_share=.7,joint_math_levels=2,joint_math_eval_per_level=3)
    a.update(changes)
    return SimpleNamespace(**a)


def test_joint_policy_maintains_both_frontiers(tmp_path):
    a=args(tmp_path);p=joint._policy(a)
    assert (p['t_seed_radius'],p['t_max_radius'])==(64,256)
    assert p['hdb']['steps']==p['steps']==100
    assert joint._out(a).name.endswith('_joint_t_hdb_numeric_frontier_v2')
    assert p['hdb']['train_slopes']==hdb.TRAIN_SLOPES
    with pytest.raises(ValueError,match='JOINT_FRONTIER_INVALID_POLICY'):
        joint._policy(args(tmp_path,joint_t_max_radius=64))
    with pytest.raises(ValueError,match='JOINT_FRONTIER_INVALID_POLICY'):
        joint._policy(args(tmp_path,joint_hdb_min_share=.9))


def test_frozen_joint_eval_decks_and_chain_slices(tmp_path):
    a=args(tmp_path);p=joint._policy(a)
    ts,hs,ch=joint._eval(a,p)
    assert {r['radius'] for r in ts}=={64,256}
    assert {r['rung'] for r in ts}==set(range(9))
    assert {r['tier'] for r in hs}=={f'HDB{x:02d}' for x in range(9)}
    assert all(r['rung']==8 for r in ch)
    assert len({r['source'] for r in [*ts,*hs]})==len(ts)+len(hs)
    forbidden={r['source'] for r in [*ts,*hs]}
    pool=joint._t_pool(a,p,0,forbidden)
    assert pool
    assert all(r['source'] not in forbidden for r in pool)
    assert pool != joint._t_pool(a,p,10,forbidden)


def test_every_joint_step_trains_all_mathematical_frontiers(tmp_path):
    a=args(tmp_path);p=joint._policy(a)
    trows,hrows,ch=joint._eval(a,p)
    mathrows=joint._math_eval(a,p,trows,hrows)
    assert {r['task'] for r in mathrows}==set(math_tasks.TASKS)
    assert {r['level'] for r in mathrows}=={0,1}
    assert len({r['source'] for r in [*trows,*hrows,*mathrows]})==len(trows)+len(hrows)+len(mathrows)
    shotgunrows=joint._shotgun_eval(a,p,trows,hrows,mathrows)
    forbidden={r['source'] for r in [*trows,*hrows,*mathrows,*shotgunrows]}
    pool=joint._t_pool(a,p,0,forbidden)
    texp,hexp,mexp=joint._exposures(p)
    state={'t_exposures':texp,'h_exposures':hexp,'math_exposures':mexp,
           't_current':{'by_rung':{}},'h_current':{'by_tier':{}},
           'math_current':{'by_task_level':{}},'shotgun_exposures':shotgun.exposures(),
           'modes':{'hdb-major':0,'t-major':0,'math-major':0,'shotgun-major':0}}
    modes=set()
    trained_math=set();hdb_sides=set()
    for step in range(160):
        rows,mode,share=joint._batch(a,p,step,state,pool,forbidden)
        assert len(rows)==4
        assert all(r['source'] not in forbidden for r in rows)
        kinds={'shotgun' if r.get('family')=='shotgun' else
               'hdb' if r.get('family')=='hdb' else
               'mathematics' if r.get('task') in math_tasks.TASKS else 't' for r in rows}
        assert kinds=={'shotgun','hdb','mathematics','t'}
        hdb_sides.update(r['zero_side'] for r in rows if r.get('family')=='hdb')
        trained_math.update(r['task'] for r in rows if r.get('task') in math_tasks.TASKS)
        assert all(r['target'].startswith('PROBLEM: ') for r in rows)
        assert .2<=share<=.7
        joint._add_exposures(state,rows)
        modes.add(mode)
    assert modes=={'hdb-major','t-major','math-major','shotgun-major'}
    assert hdb_sides=={'negative','positive','zero'}
    assert trained_math==set(math_tasks.TASKS)
    assert all(v['examples']>0 for v in texp.values())
    assert all(v['examples']>0 for v in hexp.values())
    assert all(v['examples']>0 for v in mexp.values())


def test_math_transition_metrics_are_not_mislabelled_as_t_rungs(tmp_path):
    a=args(tmp_path);p=joint._policy(a)
    t,h,_=joint._eval(a,p)
    rows=joint._math_eval(a,p,t,h)
    baseline={frontier._key(r):{'correct':False} for r in rows}
    current={frontier._key(r):{'correct':r['task']=='algebra'} for r in rows}
    scores=joint._math_metrics(rows,baseline,current)
    assert scores['total']['tested']==len(rows)
    assert scores['by_task']['algebra']['accuracy']==1
    assert scores['by_task']['logic_graphs']['accuracy']==0
    assert scores['total']['gained']==scores['by_task']['algebra']['tested']
    assert set(scores['by_task'])==set(math_tasks.TASKS)


def test_learning_state_reports_real_regression_and_provisional_stall():
    state={}
    first=joint._learning_state(state,'T07',3,10,0,1,100,family='T')
    assert first['classification']=='regressing'
    assert first['nonpositive_net_checks']==1
    second=joint._learning_state(state,'T07',3,10,0,0,200,family='T')
    assert second['classification']=='not_improving_yet'
    third=joint._learning_state(state,'T07',3,10,0,0,300,family='T')
    assert third['classification']=='provisionally_stalled'
    fourth=joint._learning_state(state,'T07',4,10,1,0,400,family='T')
    assert fourth['classification']=='acquiring'
    assert fourth['nonpositive_net_checks']==0


def test_cli_joint_dispatch_is_not_sequential_hdb(monkeypatch,tmp_path):
    seen=[]
    monkeypatch.setattr(joint,'run',lambda a:seen.append('joint') or {'status':'ok'})
    monkeypatch.setattr(legacy_hdb,'run',lambda *a,**k:seen.append('separate'))
    from tools import nanojev_qwen_lm_radius_ladder as module
    a=args(tmp_path)
    command=['--stage','run','--frontier','--frontier-hdb',
             '--output',a.output,'--stage1-checkpoint',a.stage1_checkpoint,
             '--text-checkpoint',a.text_checkpoint]
    assert module.main(command)=={'status':'ok'}
    assert seen==['joint']
    preview=module.main(['--stage','preview','--frontier','--frontier-hdb',
                          '--radii','0,1,2,3,4','--frontier-max-radius','64'])
    assert preview['optimizer_populations']==('T','HDB','mathematics','shotgun')
    assert set(preview['mathematical_families'])==set(math_tasks.TASKS)
    with pytest.raises(ValueError,match='HDB_SEPARATE_STAGE_DEPRECATED'):
        module.main(['--stage','hdb-frontier'])



def test_focus_parser_is_explicit_validated_and_resolves_the_mathematical_levels(tmp_path):
    policy=joint._policy(args(tmp_path))
    parse=lambda x:joint._parse_focus(x,policy)
    assert parse(None) is None  # Not supplying the switch means keep prior focus.
    assert parse('none')=={}
    assert parse('T07,HDB08,algebra:L1')=={
        'T07':4.0,'HDB08':4.0,'algebra:L1':4.0}
    assert parse('[T07=8,HDB08=5,math:algebra:L1=6]')=={
        'T07':8.0,'HDB08':5.0,'algebra:L1':6.0}
    assert parse('mathematics=2,algebra=3,algebra:L1=8')=={
        'math':2.,'algebra':3.,'algebra:L1':8.}
    assert parse('T07=1')=={}  # One is the neutral, unfocused weight.
    assert joint._focus_weight(parse('math=2,algebra=3,algebra:L1=8'),
                               'algebra:L1')==8
    assert joint._focus_weight(parse('math=2,algebra=3,algebra:L1=8'),
                               'algebra:L0')==3
    assert joint._focus_weight(parse('math=2,algebra=3,algebra:L1=8'),
                               'probability:L0')==2
    assert joint._focus_weight(parse('math=2'), 'HDB08')==1
    for bad,code in [('', 'EMPTY'),('[T07=5','BAD_BRACKETS'),('T09=5','UNKNOWN_TARGET'),
            ('HDB09','UNKNOWN_TARGET'),('T07=0','WEIGHT_OUT_OF_RANGE'),
            ('T07=21','WEIGHT_OUT_OF_RANGE'),('T07=NaN','WEIGHT_OUT_OF_RANGE'),
            ('T07=inf','WEIGHT_OUT_OF_RANGE'),('T07=x','BAD_WEIGHT'),
            ('T07=2,T07=4','DUPLICATE'),('algebra:L2','LEVEL_OUT_OF_RANGE'),
            ('algebra:Lx','BAD_LEVEL'),('T07,,algebra','EMPTY_ELEMENT'),
            ('bogus','UNKNOWN_TARGET')]:
        with pytest.raises(ValueError,match='JOINT_FOCUS_'+code):
            parse(bad)


def test_focused_training_increases_direct_exposure_but_leaves_every_population_active(tmp_path):
    a=args(tmp_path);policy=joint._policy(a)
    trows,hrows,_=joint._eval(a,policy)
    mathrows=joint._math_eval(a,policy,trows,hrows)
    forbidden={r['source'] for r in [*trows,*hrows,*mathrows]}
    pool=joint._t_pool(a,policy,0,forbidden)

    def trial(focus):
        t,h,m=joint._exposures(policy)
        state={'t_exposures':t,'h_exposures':h,'math_exposures':m,
               'shotgun_exposures':shotgun.exposures(),
               't_current':{'by_rung':{}},'h_current':{'by_tier':{}},
               'math_current':{'by_task_level':{}},'focus':focus}
        modes=set()
        for step in range(280):
            rows,mode,_=joint._batch(a,policy,step,state,pool,forbidden)
            assert len(rows)==a.batch_size
            assert {('shotgun' if r.get('family')=='shotgun' else
                     'HDB' if r.get('family')=='hdb' else
                     'math' if r.get('task') in math_tasks.TASKS else 'T')
                    for r in rows}=={'T','HDB','math','shotgun'}
            assert all(r['source'] not in forbidden for r in rows)
            modes.add(mode)
            joint._add_exposures(state,rows)
        return t,h,m,modes

    base_t,base_h,base_m,_=trial({})
    t,h,m,modes=trial({'T07':12,'algebra:L1':10})
    assert t['T07']['examples']>base_t['T07']['examples']*1.7
    assert m['algebra:L1']['examples']>base_m['algebra:L1']['examples']*1.6
    assert all(v['examples']>0 for v in t.values())
    assert all(v['examples']>0 for v in h.values())
    assert all(v['examples']>0 for v in m.values())
    assert set(modes)=={'t-major','hdb-major','math-major','shotgun-major'}
    weights=joint._focus_groups(policy,{'T07':12,'algebra:L1':10})
    assert weights['t']>weights['hdb'] and weights['mathematics']>weights['hdb']



def test_hdb_focus_and_larger_minibatches_do_not_drop_other_math(tmp_path):
    a=args(tmp_path,batch_size=8)
    p=joint._policy(a)
    ts,hs,_=joint._eval(a,p)
    ms=joint._math_eval(a,p,ts,hs)
    forbidden={r['source'] for r in [*ts,*hs,*ms]}
    pool=joint._t_pool(a,p,0,forbidden)
    tx,hx,mx=joint._exposures(p)
    state={'t_exposures':tx,'h_exposures':hx,'math_exposures':mx,
           'shotgun_exposures':shotgun.exposures(),
           't_current':{'by_rung':{}},'h_current':{'by_tier':{}},
           'math_current':{'by_task_level':{}},'focus':{'HDB08':20.}}
    for step in range(70):
        rows,_,_=joint._batch(a,p,step,state,pool,forbidden)
        assert len(rows)==8
        assert any(r.get('task') in math_tasks.TASKS for r in rows)
        assert any(r.get('family')=='hdb' for r in rows)
        assert any('rung' in r and r.get('task') not in math_tasks.TASKS for r in rows)
        joint._add_exposures(state,rows)
    assert hx['HDB08']['examples']>4*min(v['examples'] for k,v in hx.items() if k!='HDB08')
    assert sum(v['examples'] for v in tx.values())>=70
    assert sum(v['examples'] for v in mx.values())>=70


def test_legacy_status_before_migration_is_readable(tmp_path):
    a=args(tmp_path)
    path=joint._out(a)/'progress.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    policy=joint._policy(a)
    for key in ('math_tasks','math_levels','math_eval_per_level','math_tasks_version'):
        policy.pop(key)
    path.write_text(json.dumps({'status':'COMPLETE','step':100,
        'training_seconds':5.,'t_exposures':{},'h_exposures':{},
        'policy':policy}),encoding='utf8')
    result=joint._status(a)
    assert result['step']==100
    assert result['focus_feedback']['diagnostic_state']=='legacy_frontier_not_yet_migrated'

def test_focus_cli_preview_and_rejects_irrelevant_modes(tmp_path):
    a=args(tmp_path)
    result=ladder.main(['--stage','preview','--frontier','--frontier-hdb',
        '--radii','0,1,2,3,4','--frontier-max-radius','64',
        '--focus','[HDB08=10,algebra:L1=8]'])
    assert result['focus_schedule']=={'HDB08':10.,'algebra:L1':8.}
    assert result['focus_effective_targets']['HDB08']==10
    assert result['focus_effective_targets']['algebra:L1']==8
    with pytest.raises(ValueError,match='JOINT_FOCUS_REQUIRES_JOINT_FRONTIER_RUN_OR_PREVIEW'):
        ladder.main(['--stage','competency','--frontier','--frontier-hdb',
                     '--focus','T07=4'])

def test_t_rung_priority_uses_correct_now_not_missing_correct():
    stats={f'T{x:02d}':{'tested':20,'correct_now':20} for x in range(9)}
    stats['T07']['correct_now']=0
    chosen=joint._priority(stats,[f'T{x:02d}' for x in range(9)],
                           {f'T{x:02d}':{'examples':20} for x in range(9)},1)
    assert chosen=='T07'


def test_real_joint_training_flow_checkpoint_resume_and_reports(monkeypatch,tmp_path):
    torch=pytest.importorskip('torch')
    a=args(tmp_path,joint_steps=2,joint_check_every=1,joint_t_eval_per_shell=36,
           joint_t_train_cases_per_shell=81)
    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__();self.weight=torch.nn.Parameter(torch.tensor(0.))
    class Zero:
        @staticmethod
        def lora_state(bb):return {'weight':bb.weight.detach().clone()}
        @staticmethod
        def restore_lora(bb,state):
            with torch.no_grad():bb.weight.copy_(state['weight'])
    parent={'state':{'phase_step':1200},'optimizer':None}
    monkeypatch.setattr(hdb,'_checkpoint',lambda args,policy:(parent,'frozen-parent-sha'))
    monkeypatch.setattr(hdb,'_model',lambda args,p:(Model(),None,Zero,[Model().weight]))
    # Ensure the optimizer uses the same Model instance passed to training.
    def load_model(*_):
        bb=Model();return bb,None,Zero,[bb.weight]
    monkeypatch.setattr(hdb,'_model',load_model)
    def step(bb,tok,opt,rows,args):
        with torch.no_grad():bb.weight.add_(1.)
        return 0.125
    monkeypatch.setattr(ladder,'_train_step',step)
    def fake_t(bb,tok,rows,args):
        good=float(bb.weight)>0
        return {frontier._key(r):{'correct':good,'emitted':r['target'] if good else 'BAD'} for r in rows}
    monkeypatch.setattr(joint,'_score_t',fake_t)
    monkeypatch.setattr(joint,'_score_shotgun',fake_t)
    def fake_h(bb,tok,rows,args,policy):
        good=float(bb.weight)>0
        return {joint.competency._digest(r['source']):{
             'correct':good,'emitted':r['target'] if good else 'INVALID'} for r in rows}
    monkeypatch.setattr(hdb,'_score_hdb',fake_h)
    def fake_hc(bb,tok,rows,args,policy):
        good=float(bb.weight)>0
        return {joint.competency._digest(r['source']):{'correct':good,
                 'terminal_correct':good,'first_wrong_rung':None if good else r['tier']}
                for r in rows}
    monkeypatch.setattr(hdb,'_score_balance_chains',fake_hc)
    def fake_c(bb,tok,rows,args,*,greedy=None,progress=None):
        good=float(bb.weight)>0
        return {frontier._key(r):{'correct':good,'terminal_correct':good,
                'first_wrong_rung':None if good else 'T07',
                'failure_reason':None if good else 'wrong_terminal_or_intermediate'}
                for r in rows}
    monkeypatch.setattr(frontier,'_chain_score',fake_c)
    state=joint.run(a)
    assert state['status']=='COMPLETE'
    assert state['step']==2
    assert sum(state['modes'].values())==2
    assert sum(v['examples'] for v in state['t_exposures'].values())>0
    assert sum(v['examples'] for v in state['h_exposures'].values())>0
    assert sum(v['examples'] for v in state['math_exposures'].values())>0
    assert sum(v['examples'] for v in state['shotgun_exposures'].values())>0
    assert (joint._out(a)/'shotgun_eval_deck.json').exists()
    report=json.loads((joint._out(a)/'joint_frontier_report.json').read_text())
    assert report['chains']['from_start']['strict_correct']==len(joint._eval(a,joint._policy(a))[2])
    assert 'hdb_numeric_balance_chains' in report
    assert 'shotgun' in report
    assert report['shotgun']['from_baseline']['total']['tested']==len(json.loads((joint._out(a)/'shotgun_eval_deck.json').read_text()))
    assert report['shotgun']['matched_hdb08_comparison']['matched_scenarios']*len(shotgun.DIAGNOSTIC_MODES)==report['shotgun']['from_baseline']['total']['tested']
    assert report['hdb_numeric_balance_chains']['from_start']['correct']==27
    assert 'T07' in report['per_tier_learning_state']
    assert 'HDB07' in report['per_tier_learning_state']
    assert 'algebra:L0' in report['per_tier_learning_state']
    assert set(report['mathematics']['from_baseline']['by_task'])==set(math_tasks.TASKS)
    assert (joint._out(a)/'hdb_difficulty_report.json').exists()
    assert joint.run(a)['step']==2
    # The continuing budget can be extended; arbitrary mathematical
    # curriculum changes cannot be silently applied to trained weights.
    with pytest.raises(RuntimeError,match='JOINT_FRONTIER_RESUME_SOURCE_POLICY_OR_DECK_DRIFT'):
        joint.run(args(tmp_path,joint_steps=3,joint_check_every=1,
                       joint_t_eval_per_shell=36,joint_math_levels=3))
    extended=joint.run(args(tmp_path,joint_steps=3,joint_check_every=1,
                        joint_t_eval_per_shell=36))
    assert extended['step']==3
    assert extended['math_exposures']
    # Simulate migration from the ACTUAL v2 matched HDB08 diagnostic,
    # with a consistent v2 deck SHA and untouched optimizer/LoRA.
    checkpoint_before_v2=torch.load(joint._out(a)/'working.pt',map_location='cpu',weights_only=True)
    v2_model=checkpoint_before_v2['lora']['weight'].clone()
    v2_optimizer=checkpoint_before_v2['optimizer']
    deck_path=joint._out(a)/'shotgun_eval_deck.json'
    full_deck=json.loads(deck_path.read_text())
    v2_deck=[r for r in full_deck if r['mode'] in shotgun.MODES]
    assert len(v2_deck)*len(shotgun.DIAGNOSTIC_MODES)==len(full_deck)*len(shotgun.MODES)
    ladder._atomic_json(deck_path,v2_deck)
    v2_checkpoint=checkpoint_before_v2
    v2_checkpoint['state']['policy']['shotgun_version']='nanojev.qwen.shotgun.failure-neighborhood.v2'
    v2_checkpoint['state']['shotgun_diagnostic_version']='nanojev.qwen.shotgun.failure-neighborhood.v2'
    v2_checkpoint['state']['shotgun_eval_sha256']=ladder.deck_fingerprint(v2_deck)
    ladder._atomic_torch(joint._out(a)/'working.pt',v2_checkpoint)
    v2_report_path=joint._out(a)/'joint_frontier_report.json'
    v2_report=json.loads(v2_report_path.read_text())
    v2_report['shotgun']['diagnostic_version']='nanojev.qwen.shotgun.failure-neighborhood.v2'
    ladder._atomic_json(v2_report_path,v2_report)
    migrated_v2=joint.run(args(tmp_path,joint_steps=3,joint_check_every=1,
                               joint_t_eval_per_shell=36))
    assert migrated_v2['step']==3
    assert migrated_v2['shotgun_diagnostic_version']==shotgun.DIAGNOSTIC_VERSION
    assert migrated_v2['shotgun_matched_current']['operation_boundaries']
    assert (joint._out(a)/'shotgun_v2_eval_deck.json').is_file()
    assert json.loads((joint._out(a)/'shotgun_v2_eval_deck.json').read_text())==v2_deck
    assert (joint._out(a)/'shotgun_v2_archived_diagnostic.json').is_file()
    snapshot=torch.load(joint._out(a)/'pre_shotgun_operation_isolation_migration.pt',
                        map_location='cpu',weights_only=True)
    assert torch.equal(snapshot['lora']['weight'],v2_model)
    assert snapshot['optimizer']==v2_optimizer
    observed=json.loads((joint._out(a)/'shotgun_operation_cases.json').read_text())
    assert observed['step']==3 and len(observed['scenarios'])>0
    assert not joint.latest_competency(a)['shotgun_report_is_current']
    # Simulate the actual v1 -> matched HDB08 Shotgun upgrade, with a complete
    # optimizer state. It must rebase ONLY Shotgun's diagnostic baseline.
    old_checkpoint=torch.load(joint._out(a)/'working.pt',map_location='cpu',weights_only=True)
    preserved_weight=old_checkpoint['lora']['weight'].clone()
    old_checkpoint['state']['policy']['shotgun_version']=joint.shotgun.VERSION
    old_checkpoint['state'].pop('shotgun_diagnostic_version')
    old_checkpoint['state']['shotgun_eval_sha256']='old-v1-48-case-deck'
    prior_report_path=joint._out(a)/'joint_frontier_report.json'
    prior_report=json.loads(prior_report_path.read_text())
    prior_report['shotgun'].pop('diagnostic_version',None)
    ladder._atomic_json(prior_report_path,prior_report)
    ladder._atomic_torch(joint._out(a)/'working.pt',old_checkpoint)
    migrated_shotgun=joint.run(args(tmp_path,joint_steps=3,joint_check_every=1,
                                    joint_t_eval_per_shell=36))
    assert migrated_shotgun['step']==3
    assert migrated_shotgun['shotgun_diagnostic_version']==joint.shotgun.DIAGNOSTIC_VERSION
    assert migrated_shotgun['shotgun_baseline_step']==3
    assert migrated_shotgun['shotgun_matched_current']['matched_scenarios']>0
    archive=joint._out(a)/'shotgun_v1_archived_diagnostic.json'
    assert archive.is_file()
    assert json.loads(archive.read_text())['original_eval_sha256']=='old-v1-48-case-deck'
    snap=torch.load(joint._out(a)/'pre_shotgun_failure_neighborhood_migration.pt',
                    map_location='cpu',weights_only=True)
    assert torch.equal(snap['lora']['weight'],preserved_weight)
    assert snap['optimizer']==old_checkpoint['optimizer']
    latest=joint.latest_competency(a)
    assert not latest['shotgun_report_is_current']
    assert latest['shotgun']['diagnostic_state']=='operation_isolation_migration_baseline_only'
    # Simulate an authentic v2 T/HDB-only checkpoint, retaining every learned
    # LoRA tensor and the optimizer. Migration must not reinitialize either.
    workspace=joint._out(a)
    original=torch.load(workspace/'working.pt',map_location='cpu',weights_only=True)
    previous_lora=original['lora']['weight'].clone()
    original['schema']=joint.LEGACY_SCHEMA
    st=original['state'];st['schema']=joint.LEGACY_SCHEMA
    st['policy']['schema']=joint.LEGACY_SCHEMA
    for key in ('math_tasks_version','math_tasks','math_levels','math_eval_per_level'):
        st['policy'].pop(key)
    st['eval_sha256']=':'.join(st['eval_sha256'].split(':')[:2])
    for key in ('math_initial','math_previous','math_current',
                'math_exposures','math_baseline_step','last_math_exposures'):
        st.pop(key,None)
    st['modes'].pop('math-major',None)
    ladder._atomic_torch(workspace/'working.pt',original)
    # When the old campaign is complete, the first migration performs only
    # baseline inference; do not pretend a mathematical optimizer step ran.
    migrated_idle=joint.run(args(tmp_path,joint_steps=3,joint_check_every=1,
                                 joint_t_eval_per_shell=36))
    assert migrated_idle['step']==3
    assert migrated_idle['status']=='COMPLETE'
    snapshot=joint.latest_competency(a)
    assert snapshot['diagnostic_state']=='migration_baseline_only_no_math_training_yet'
    assert snapshot['mathematics']['baseline_at_optimizer_step']==3
    migrated=joint.run(args(tmp_path,joint_steps=4,joint_check_every=1,
                            joint_t_eval_per_shell=36))
    assert migrated['step']==4
    assert migrated['migration_from']==joint.LEGACY_SCHEMA
    assert migrated['math_baseline_step']==3
    assert migrated['math_exposures']
    backup=torch.load(workspace/'pre_math_frontier_migration.pt',map_location='cpu',weights_only=True)
    assert backup['schema']==joint.LEGACY_SCHEMA
    assert torch.equal(backup['lora']['weight'],previous_lora)
    # A focus change on this existing v3 run must preserve adapter and AdamW
    # state, create a pre-transition snapshot, and change *only* scheduling.
    before=torch.load(workspace/'working.pt',map_location='cpu',weights_only=True)
    focused_args=args(tmp_path,joint_steps=6,joint_check_every=1,
        joint_t_eval_per_shell=36,focus='[T07=12,algebra:L1=8]')
    focused=joint.run(focused_args)
    assert focused['step']==6
    assert focused['focus']=={'T07':12.,'algebra:L1':8.}
    assert focused['focus_since_step']==4
    snaps=list((workspace/'focus_snapshots').glob('*.pt'))
    assert len(snaps)==1
    snapshot=torch.load(snaps[0],map_location='cpu',weights_only=True)
    assert torch.equal(snapshot['lora']['weight'],before['lora']['weight'])
    assert snapshot['optimizer']==before['optimizer']
    summary=joint._status(focused_args)
    assert summary['focus_schedule']==focused['focus']
    assert summary['focus_feedback']['targets']['T07']['weight']==12
    assert summary['focus_feedback']['targets']['algebra:L1']['weight']==8
    report=joint.latest_competency(focused_args)
    assert report['focus_report_is_current']
    assert report['focus_schedule']==focused['focus']
    assert report['active_focus_schedule']==focused['focus']
    assert report['focus_feedback']['optimizer_steps_since_focus']==2
    assert report['focus_feedback']['targets']['T07']['direct_examples_since_focus']>0
    # Omitted argument leaves focus intact on later continuation.
    unchanged=joint.run(args(tmp_path,joint_steps=7,joint_check_every=1,
        joint_t_eval_per_shell=36))
    assert unchanged['focus']==focused['focus']
    # A later explicit change does not fork/reset the optimizer.
    retarget=joint.run(args(tmp_path,joint_steps=8,joint_check_every=1,
        joint_t_eval_per_shell=36,focus='HDB08=10'))
    assert retarget['focus']=={'HDB08':10.}
    assert len(list((workspace/'focus_snapshots').glob('*.pt')))==2
    cleared=joint.run(args(tmp_path,joint_steps=9,joint_check_every=1,
        joint_t_eval_per_shell=36,focus='none'))
    assert cleared['focus']=={}
    assert len(cleared['focus_history'])==3
    assert len(list((workspace/'focus_snapshots').glob('*.pt')))==3
    # Rescheduling monitoring on a live checkpoint must preserve LoRA + AdamW.
    actual_save=joint._save
    unscored_checkpoints=[]
    def record_save(out,state,bb,zero,opt):
        if state['step']==10 and state['last_checked_step']==9:
            unscored_checkpoints.append(state['step'])
        return actual_save(out,state,bb,zero,opt)
    monkeypatch.setattr(joint,'_save',record_save)
    resumed=joint.run(args(tmp_path,joint_steps=11,joint_check_every=1000,
        joint_t_eval_per_shell=36,joint_heartbeat_every=1,
        joint_probe_every=1,joint_save_every=1))
    assert resumed['step']==11
    assert unscored_checkpoints==[10]
    assert resumed['policy']['check_every']==1000
    assert resumed['last_checked_step']==11
    probe=json.loads((workspace/'joint_probe_report.json').read_text())
    assert probe['step']==10
    assert probe['last_full_audit_step']==9
    heartbeat=json.loads((workspace/'joint_heartbeat.json').read_text())
    assert heartbeat['step']==11
    status=joint._status(args(tmp_path))
    assert status['checkpoint_step']==11
    assert status['full_audit_step']==11
    assert status['latest_probe'] is None


def test_no_training_exposure_does_not_create_false_stall():
    s={}
    assert joint._learning_state(s,'HDB08',0,9,0,0,0,
             family='HDB',interval_exposure=0)['nonpositive_net_checks']==0
    assert joint._learning_state(s,'HDB08',0,9,0,0,10,
             family='HDB',interval_exposure=10)['nonpositive_net_checks']==1
    assert joint._learning_state(s,'HDB08',0,9,0,0,10,
             family='HDB',interval_exposure=0)['nonpositive_net_checks']==1


def test_monitor_uses_a_small_frozen_subset_but_keeps_broad_coverage(tmp_path):
    a=args(tmp_path);p=joint._policy(a)
    ts,hs,_=joint._eval(a,p)
    ms=joint._math_eval(a,p,ts,hs)
    t,h,m=joint._probe_deck(ts,hs,ms)
    assert (len(t),len(h),len(m))==(18,27,18)
    assert {(r['rung'],r['radius']) for r in t}=={
        (rung,radius) for rung in range(9) for radius in (64,256)}
    assert {(r['tier'],r['zero_side']) for r in h}=={
        (f'HDB{rung:02d}',side) for rung in range(9)
        for side in ('negative','zero','positive')}
    assert {(r['task'],r['level']) for r in m}=={
        (task,lev) for task in math_tasks.TASKS for lev in (0,1)}
    assert all(id(r) in {id(x) for x in ts} for r in t)
    assert all(id(r) in {id(x) for x in hs} for r in h)
    assert all(id(r) in {id(x) for x in ms} for r in m)


def test_probe_observational_and_heartbeat_does_not_save_model(monkeypatch,tmp_path):
    a=args(tmp_path)
    p=joint._policy(a)
    t,h,_=joint._eval(a,p);m=joint._math_eval(a,p,t,h)
    deck=joint._probe_deck(t,h,m)
    state={'policy':p,'step':150,'last_checked_step':100,'training_seconds':3.5,
           't_exposures':{'T00':{'examples':10}},
           'h_exposures':{'HDB00':{'examples':10}},
           'math_exposures':{'algebra:L0':{'examples':10}},
           'focus':{'T07':12},'t_previous':{},'h_previous':{},'math_previous':{}}
    for rr in t:state['t_previous'][frontier._key(rr)]={'correct':True}
    for rr in h:state['h_previous'][joint.competency._digest(rr['source'])]={'correct':True}
    for rr in m:state['math_previous'][frontier._key(rr)]={'correct':True}
    monkeypatch.setattr(joint,'_score_t',lambda _,__,rows,___:
        {frontier._key(r):{'correct':False} for r in rows})
    monkeypatch.setattr(joint,'_score_math',lambda _,__,rows,___:
        {frontier._key(r):{'correct':True} for r in rows})
    monkeypatch.setattr(hdb,'_score_hdb',lambda _,__,rows,___,____:
        {joint.competency._digest(r['source']):{'correct':True} for r in rows})
    out=joint._out(a);out.mkdir(parents=True,exist_ok=True)
    report=joint._run_probe(out,state,None,None,a,deck)
    assert report['kind']=='non_authoritative_fixed_deck_probe'
    assert report['T']['lost_since_full_audit']==18
    assert report['HDB']['lost_since_full_audit']==0
    assert report['mathematics']['correct']==18
    assert state['t_previous'][frontier._key(t[0])]['correct'] is True
    assert state['last_checked_step']==100
    assert not (out/'working.pt').exists()
    heartbeat=joint._write_heartbeat(out,state,0.125)
    assert heartbeat['step']==150
    assert heartbeat['kind']=='uncommitted_live_heartbeat'
    assert not (out/'working.pt').exists()
    assert joint._monitor_cadence(args(tmp_path,joint_heartbeat_every=25,
                                       joint_probe_every=250,joint_save_every=100))==(25,250,100)
    with pytest.raises(ValueError,match='JOINT_MONITOR_INTERVAL_INVALID'):
        joint._monitor_cadence(args(tmp_path,joint_probe_every=-2))


def test_full_audit_cadence_is_mutable_but_core_geometry_is_not(tmp_path):
    p1=joint._policy(args(tmp_path,joint_check_every=100))
    p2=joint._policy(args(tmp_path,joint_check_every=1000))
    assert joint._compatible_policy(p1,p2)
    p3=joint._policy(args(tmp_path,joint_check_every=1000,joint_math_levels=3))
    assert not joint._compatible_policy(p1,p3)
    p4=joint._policy(args(tmp_path,joint_check_every=1000,joint_steps=200))
    assert joint._compatible_policy(p1,p4)


def test_joint_monitor_cli_and_read_only_status(tmp_path,monkeypatch):
    from tools import nanojev_qwen_lm_radius_ladder as module
    monkeypatch.setattr(joint,'run',lambda a:('run',a.joint_heartbeat_every,a.joint_probe_every,
                                               a.joint_save_every,a.joint_check_every))
    a=args(tmp_path)
    command=['--stage','run','--frontier','--frontier-hdb',
             '--output',a.output,'--stage1-checkpoint',a.stage1_checkpoint,
             '--text-checkpoint',a.text_checkpoint]
    assert module.main(command+['--joint-heartbeat-every','25','--joint-probe-every','250',
                       '--joint-save-every','100','--joint-check-every','1000'])==('run',25,250,100,1000)
