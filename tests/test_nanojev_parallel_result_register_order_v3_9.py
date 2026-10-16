"""Corrected prompt-order experiment: no shared-replay contamination and paired labels."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_parallel_result_register_order_v3_9 as fix
from nanojev_register_stability_v3_generate import FIRST,SECOND,OPERATIONS,render


def replay_item(a,b,op='sym_sub'):
    return (torch.zeros(6),a,b,op,77,-42)


def paired_baseline(original=.75,reverse=10_000_000_000.):
    return {'rows':1024,
            'original':{'x_mse':.02,'y_mse':original-.02,'xy_mse':original,
                        'x_5pct':.34,'y_5pct':.22},
            'reversed_label_aligned':{'x_mse':reverse*.9,'y_mse':reverse*.1,
                                      'xy_mse':reverse,'x_5pct':.006,'y_5pct':.004},
            'frozen_operation_probe_accuracy_original':1.0,
            'frozen_operation_probe_accuracy_reversed':1.0}

@pytest.mark.parametrize('op',OPERATIONS)
def test_prompt_only_prefix_reversal_all_op_families(op):
    orig={'a':17,'b':-3,'operation':op[0], 'prompt':render(17,-3,op)}
    reverse=fix.reorder_registration(orig,True)
    assert reverse['prompt'].splitlines()[:2]==orig['prompt'].splitlines()[1::-1]
    assert reverse['prompt'].splitlines()[2:]==orig['prompt'].splitlines()[2:]
    assert {k:v for k,v in reverse.items() if k!='prompt'}=={k:v for k,v in orig.items() if k!='prompt'}
    assert fix.reorder_registration(orig,False) is orig


def test_replay_unchanged_and_operand_labels_fixed():
    rows=[replay_item(7,2),replay_item(4,9),replay_item(1,8),replay_item(21,3)]
    original=[tuple(r) for r in rows]
    seen=[]
    def capture(prompts):
        seen.extend(prompts)
        return torch.ones((len(prompts),6))
    mixed=fix.replace_intermediate_features(rows,capture)
    assert len(seen)==2
    assert all(p.startswith(SECOND+' = ') for p in seen)
    assert mixed[0] is rows[0] and mixed[2] is rows[2]
    assert mixed[1][1:] == rows[1][1:] and mixed[3][1:] == rows[3][1:]
    assert torch.all(mixed[1][0]==1) and torch.all(mixed[3][0]==1)
    assert all(torch.equal(r[0],torch.zeros(6)) for r in rows)
    assert original==rows


def test_odd_number_samples_alternates_and_invalid_operation_fails_closed():
    rows=[replay_item(2,3)]*5
    assert sum(not torch.equal(x[0],torch.zeros(6)) for x in fix.replace_intermediate_features(
         rows,lambda p:torch.ones((len(p),6)),reverse_first=True))==3
    with pytest.raises(RuntimeError,match='ALTREG_UNKNOWN_OPERATION'):
        fix.swapped_prompts_for_replay([replay_item(4,5,'not_an_op')],[True])


def mock_checkpoint(path,step=23800):
    path.parent.mkdir(parents=True,exist_ok=True)
    torch.save({'schema':fix.v37.SCHEMA,'config':{'base':1},'step':step,
                'bottom_operand_decoder':{'weight':torch.arange(4.)},
                'bottom_operand_optimizer':{'state':{'m':torch.arange(3.)}},
                'bottom_phase_tick':19,
                'bottom_phase_counts':{'intermediate':12,'result':5,'final':2},
                'stream':{'drawn':42},'replay':{'rows':[1,2,3]}},path)


def test_safe_fork_isolated_from_damaged_v38(tmp_path):
    original=tmp_path/'v37';target=tmp_path/'v39'
    mock_checkpoint(original/'latest.pt')
    hash_old=hashlib.sha256((original/'latest.pt').read_bytes()).hexdigest()
    marker=fix.create_fork(original,target)
    assert marker['schema']==fix.CUTOVER_SCHEMA
    ck=torch.load(target/'latest.pt',weights_only=True)
    assert ck['config']['register_prompt_order']==fix.ORDER_VALUE
    assert ck['stream']=={'drawn':42} and ck['replay']=={'rows':[1,2,3]}
    assert torch.equal(ck['bottom_operand_optimizer']['state']['m'],torch.arange(3.))
    assert hashlib.sha256((original/'latest.pt').read_bytes()).hexdigest()==hash_old
    with pytest.raises(RuntimeError,match='ALTREG_TARGET_ROOT_NOT_EMPTY'):
        fix.create_fork(original,target)
    with pytest.raises(RuntimeError,match='ALTREG_UNSAFE_FORK_ROOT'):
        fix.create_fork(original,original/'child')
    with pytest.raises(RuntimeError,match='ALTREG_REQUIRES_V3_7_CHECKPOINT'):
        bad=tmp_path/'bad';bad.mkdir()
        torch.save({'schema':'nanojev.register-prompt-order-alternation.v3.8'},bad/'latest.pt')
        fix.create_fork(bad,tmp_path/'badout')


def test_train_requires_real_paired_preflight(tmp_path,monkeypatch):
    original=tmp_path/'v37';target=tmp_path/'v39'
    mock_checkpoint(original/'latest.pt')
    args=fix.parser().parse_args(['--fork-v3-7-root',str(original),
         '--results-root',str(target),'--additional-steps','1'])
    with pytest.raises(RuntimeError,match='ALTREG_PAIRED_PREFLIGHT_REQUIRED'):
        fix.execute(args)
    assert (target/fix.MARKER).exists()
    assert not (target/fix.PREFLIGHT).exists()


def test_integration_v37_hooks_do_not_touch_shared_replay_or_result(tmp_path,monkeypatch):
    original=tmp_path/'v37';target=tmp_path/'v39'
    mock_checkpoint(original/'latest.pt')
    fix.create_fork(original,target)
    sha=hashlib.sha256((target/'latest.pt').read_bytes()).hexdigest()
    (target/fix.PREFLIGHT).write_text(json.dumps({
        'schema':fix.CUTOVER_SCHEMA,'checkpoint_sha256':sha,
        'baseline':paired_baseline(),'safety_gate':{'eligible':False,'reasons':['ALTREG_GATE_REVERSED_NUMERIC_FAILURE']}}))
    state={'called':0}
    old_batch=fix.v37.base.previous.FreshPairs.batch
    old_update=fix.v37.update_intermediaries
    old_metrics=fix.v37.operand_metrics
    old_extract=fix.v37.base.previous.extract_batched
    old_config=fix.v37.config_for
    def fake_extract(torchmod,frozen,tokenizer,prompts,device,max_tokens):
        state.setdefault('captured',[]).extend(prompts)
        return torch.ones((len(prompts),6))
    monkeypatch.setattr(fix.v37.base.previous,'extract_batched',fake_extract)
    def original_update(_,__,___,____,rows,device,weight):
        assert rows[0][0].sum()==0
        assert rows[1][0].sum()==6
        assert rows[2][0].sum()==0
        assert rows[3][0].sum()==6
        assert rows[1][1:]==replay_item(7,2)[1:]
        return (0.,{})
    monkeypatch.setattr(fix.v37,'update_intermediaries',original_update)
    monkeypatch.setattr(fix.v37,'config_for',lambda *a,**k:{'base':1})
    def fake_execute(args):
        # Shared FreshPairs.batch is unchanged! RESULT/FINAL use original replay.
        assert fix.v37.base.previous.FreshPairs.batch is old_batch
        conf=fix.v37.config_for(None,None,None,None)
        assert conf['register_prompt_order']==fix.ORDER_VALUE
        f=fix.v37.base.previous.extract_batched(torch,'model','tok',
            ['original synthetic prompt'],'cpu',256)
        assert f.shape==(1,6)
        rows=[replay_item(7,2)]*4
        before=[r[0].clone() for r in rows]
        fix.v37.update_intermediaries(None,None,None,None,rows,'cpu',1.)
        assert all(torch.equal(r[0],z) for r,z in zip(rows,before))
        state['called']+=1
        return 0
    monkeypatch.setattr(fix.v37,'execute',fake_execute)
    args=fix.parser().parse_args(['--resume','--results-root',str(target),
                                   '--additional-steps','1'])
    assert fix.execute(args)==0
    assert state['called']==1
    assert len(state['captured'])==3
    assert all(p.startswith(SECOND+' =') for p in state['captured'][1:])
    assert fix.v37.update_intermediaries is original_update
    assert fix.v37.operand_metrics is old_metrics
    assert fix.v37.base.previous.extract_batched is fake_extract
    assert fix.v37.config_for(None,None,None,None)=={'base':1}


def test_self_test_is_offline():
    assert fix.self_test()==0


def test_paired_metrics_match_labels_not_positions(monkeypatch):
    """Known X/Y values verify correct-vs-swapped targets and numeric averaging."""
    correct=np.array([[10,20],[30,40]],dtype=np.int64)
    # Synthetic 'capture': original features store [X,Y]; reversed store a
    # position-dominated head interpretation [Y,X] for the purpose of this test.
    original=np.array([[10.,20.],[30.,40.]],dtype=np.float32)
    reversed_=np.array([[20.,10.],[40.,30.]],dtype=np.float32)
    rows=(original,correct,['sym_add','sym_sub'],['symbolic']*2)
    monkeypatch.setattr(fix.v37,'current_xy',lambda model,f:f.detach().cpu().numpy().astype(np.int64))
    monkeypatch.setattr(fix.v37.base,'source_decode',lambda model,f:f.detach().cpu().numpy().astype(np.int64))
    # Identity logits choose operation sym_add/sym_sub respectively
    num_ops=len(fix.v37.base.OP_CLASSES)
    class OpProbe:
        def __call__(self,features):
            z=torch.full((len(features),num_ops),-999.,device=features.device)
            for i in range(len(features)):
                op='sym_add' if int(features[i,0]) in (10,20) else 'sym_sub'
                z[i,fix.v37.base.OP_IDS[op]]=5.
            return z
    def base_metrics(*args,**kwargs):
        return {'hierarchy_refined':{'x_asinh_mse':0.,'y_asinh_mse':0.}}
    res=fix.operand_pair_report(None,None,None,OpProbe(),rows,reversed_,'cpu',base_metrics)
    assert res['original']['xy_mse']==0
    assert res['reversed_position_aligned']['xy_mse']==0
    assert res['reversed_label_aligned']['xy_mse']>0
    assert res['arithmetic_average_label_aligned']['xy_mse']>0
    assert res['frozen_operation_probe_accuracy_original']==1.
    assert res['frozen_operation_probe_accuracy_reversed']==1.


def test_preflight_only_cannot_train_and_resume_check_is_guarded(tmp_path,monkeypatch):
    source=tmp_path/'v37';target=tmp_path/'v39';mock_checkpoint(source/'latest.pt')
    calls=[]
    def mocked_preflight(args,root):
        calls.append('paired_preflight')
        state={'schema':fix.CUTOVER_SCHEMA,'baseline':paired_baseline(),
               'safety_gate':{'eligible':False},
               'checkpoint_sha256':hashlib.sha256((root/'latest.pt').read_bytes()).hexdigest()}
        (root/fix.PREFLIGHT).write_text(json.dumps(state))
    monkeypatch.setattr(fix,'preflight',mocked_preflight)
    monkeypatch.setattr(fix.v37,'execute',lambda *a:pytest.fail('training on preflight'))
    args=fix.parser().parse_args(['--fork-v3-7-root',str(source),
           '--results-root',str(target),'--preflight-only'])
    assert fix.execute(args)==0
    assert calls==['paired_preflight']
    assert torch.load(target/'latest.pt',weights_only=True)['step']==23800
    # If the parent checkpoint content changes before training, hard stop.
    state=torch.load(target/'latest.pt',weights_only=True)
    state['step']=23801
    torch.save(state,target/'latest.pt')
    # Do not allow changing the fork baseline before any actual training.
    with pytest.raises(RuntimeError,match='ALTREG_PREFLIGHT_CHECKPOINT_CHANGED'):
        # restore fork step but change checkpoint bytes
        state['step']=23800
        state['another_key']=1
        torch.save(state,target/'latest.pt')
        fix.execute(fix.parser().parse_args(['--resume','--results-root',str(target),
                                              '--additional-steps','2']))


def test_real_collapse_is_learnable_and_warned_not_rejected():
    # Baseline numbers measured on the user's real CUDA preflight, step 23800.
    baseline=paired_baseline(.7789842007843437,10098745870.63373)
    gate=fix.evaluate_preflight_gate(baseline)
    assert gate['eligible'] is True
    assert gate['reasons']==[]
    assert 'ALTREG_WARN_REVERSED_NUMERIC_FAILURE_EXPECTED_DURING_LEARNING' in gate['warnings']
    assert 'ALTREG_WARN_REVERSED_X_5PCT_COLLAPSE_EXPECTED_DURING_LEARNING' in gate['warnings']
    assert 'ALTREG_WARN_REVERSED_Y_5PCT_COLLAPSE_EXPECTED_DURING_LEARNING' in gate['warnings']
    assert gate['policy']==fix.LEARNING_POLICY


def test_good_accuracy_has_no_warnings():
    baseline=paired_baseline(.8,1.)
    baseline['reversed_label_aligned'].update(x_5pct=.22,y_5pct=.13)
    gate=fix.evaluate_preflight_gate(baseline)
    assert gate['eligible'] and gate['reasons']==[] and gate['warnings']==[]


def test_nonfinite_or_missing_preflight_fails_closed():
    assert not fix.evaluate_preflight_gate({})['eligible']
    baseline=paired_baseline()
    baseline['reversed_label_aligned']['xy_mse']=float('inf')
    assert not fix.evaluate_preflight_gate(baseline)['eligible']
    baseline=paired_baseline()
    del baseline['reversed_label_aligned']['y_mse']
    assert not fix.evaluate_preflight_gate(baseline)['eligible']
    baseline=paired_baseline()
    baseline['original']['x_5pct']=2.
    assert not fix.evaluate_preflight_gate(baseline)['eligible']


def test_existing_failed_preflight_reassessed_and_training_runs(tmp_path,monkeypatch):
    source=tmp_path/'v37';target=tmp_path/'v39'
    mock_checkpoint(source/'latest.pt');fix.create_fork(source,target)
    old_preflight={'schema':fix.CUTOVER_SCHEMA,
        'checkpoint_sha256':hashlib.sha256((target/'latest.pt').read_bytes()).hexdigest(),
        'checkpoint_step':23800, 'baseline':paired_baseline(),
        'safety_gate':{'eligible':False,'reasons':['ALTREG_GATE_REVERSED_NUMERIC_FAILURE']}}
    (target/fix.PREFLIGHT).write_text(json.dumps(old_preflight))
    seen=[]
    def fake_execute(args):
        seen.append('training_started')
        return 0
    monkeypatch.setattr(fix.v37,'execute',fake_execute)
    assert fix.execute(fix.parser().parse_args(['--resume','--results-root',str(target),
                                                '--additional-steps','10']))==0
    assert seen==['training_started']
    report=json.loads((target/fix.PREFLIGHT).read_text())
    assert report['safety_gate']['eligible'] is True
    assert report['safety_gate']['warnings']
    assert report['learning_policy']==fix.LEARNING_POLICY


def test_old_order_degradation_no_longer_aborts_training(tmp_path,monkeypatch):
    source=tmp_path/'v37';target=tmp_path/'v39'
    mock_checkpoint(source/'latest.pt');fix.create_fork(source,target)
    (target/fix.PREFLIGHT).write_text(json.dumps({
        'schema':fix.CUTOVER_SCHEMA,
        'checkpoint_sha256':hashlib.sha256((target/'latest.pt').read_bytes()).hexdigest(),
        'checkpoint_step':23800,'baseline':paired_baseline(),
        'safety_gate':{'eligible':True}}))
    monkeypatch.setattr(fix.v37.base.previous,'extract_batched',
                        lambda torchmod,frozen,tok,prompts,device,max_tokens:torch.ones((len(prompts),6)))
    monkeypatch.setattr(fix.v37,'operand_metrics',lambda *a,**kw:{'hierarchy_refined':{}})
    monkeypatch.setattr(fix,'operand_pair_report',lambda *a,**kw:{
        'original':{'xy_mse':20.},'reversed_label_aligned':{'xy_mse':20_000.},
        'original_operands':{},'reversed_operands':{}})
    def fake_execute(args):
        # Seed capture context before paired validation.
        fix.v37.base.previous.extract_batched(torch,'frozen','tok',['original'], 'cpu',256)
        rows=(np.zeros((2,6),dtype=np.float32),np.array([[2,3],[4,5]],dtype=np.int64),
              ['sym_add','sym_sub'],['scalar','scalar'])
        observed=fix.v37.operand_metrics(None,None,None,None,rows,'cpu')
        assert observed['paired_order']['original']['xy_mse']==20.
        return 0
    monkeypatch.setattr(fix.v37,'execute',fake_execute)
    assert fix.execute(fix.parser().parse_args(['--resume','--results-root',str(target),
                                                '--additional-steps','5']))==0
