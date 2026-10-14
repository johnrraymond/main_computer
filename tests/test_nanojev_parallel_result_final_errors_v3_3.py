"""Unit and mocked integration checks for per-register FINAL error estimators."""
import hashlib
import json
from pathlib import Path
import sys
import types

import numpy as np
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_parallel_result_register_search as base
import nanojev_parallel_result_median_v2 as median
import nanojev_parallel_result_final_v3 as legacy
import nanojev_parallel_result_final_closest_v3_1 as closest
import nanojev_parallel_result_final_pairwise_v3_2 as pair
import nanojev_parallel_result_final_errors_v3_3 as candidate


def test_all_wrong_training_and_each_head_gradients():
    torch.manual_seed(4)
    proposals=np.tile(np.arange(11,dtype=float)*100+100,(3,1))
    truth=np.array([10000.,6000.,-6000.])
    x=candidate.design(torch.randn(3,base.INPUT_WIDTH),proposals,
                       torch.tensor([base.OP_IDS['written_product']]*3))
    nets={name:candidate.ErrorEstimator(8) for name in candidate.NAMES}
    loss,part=candidate.errors_objective(nets,x,proposals,truth,[False,False,False])
    assert part['all_wrong_rows']==3
    loss.backward()
    for name,m in nets.items():
        assert m.net[-1].weight.grad is not None
        assert torch.isfinite(m.net[-1].weight.grad).all()
    assert np.isfinite(loss.item())


def test_independent_estimator_and_truth_free_feature_path():
    torch.manual_seed(5)
    c=np.tile(np.arange(1,12,dtype=float),(2,1))
    f=torch.randn(2,base.INPUT_WIDTH)
    ops=torch.tensor([base.OP_IDS['written_product']]*2)
    x=candidate.design(f,c,ops)
    nets={name:candidate.ErrorEstimator(8) for name in candidate.NAMES}
    predicted,_=candidate.predict_all(nets,x)
    before=predicted.detach().clone()
    # Changing one register's weights cannot alter any other register estimate.
    with torch.no_grad():nets['bits'].net[-1].bias[0]+=2.
    after,_=candidate.predict_all(nets,x)
    assert torch.equal(before[:,0],after[:,0])
    assert torch.equal(before[:,candidate.NAMES.index('source')],after[:,candidate.NAMES.index('source')])
    assert not torch.equal(before[:,candidate.NAMES.index('bits')],after[:,candidate.NAMES.index('bits')])
    # True RESULT must not be an input to design.
    assert torch.equal(x,candidate.design(f,c,ops))
    assert candidate.select_min_error(np.tile(np.arange(11),(2,1)),[False,True])[0].tolist()==[0,0]


def test_calibration_uses_only_specified_indices():
    predicted=np.full((6,11),.5)
    actual=np.full((6,11),.7)
    actual[1::2]=18.
    offsets=candidate.calibrate_offsets(predicted,actual,[False]*6,[0,2,4])
    assert np.allclose(offsets,.2)
    # Changing excluded rows does not change the fitted offsets.
    actual[1::2]=1e10
    assert np.array_equal(offsets,candidate.calibrate_offsets(predicted,actual,[False]*6,[0,2,4]))
    bool_flags=[True,False,True,False,True,False]
    assert np.allclose(candidate.calibrate_offsets(predicted,actual,bool_flags,[0,2,4]),np.zeros(11))


def test_nearest_target_even_with_all_wrong():
    c=np.array([[500,90,100,110,120,130,140,150,160,170,180]],dtype=float)
    truth=np.array([10.])
    numeric,rel,log,within=candidate.error_targets(c,truth,[False])
    assert not within.any()
    assert rel.argmin(axis=1).tolist()==[1]
    pred=np.array([[10,0,2,3,4,5,6,7,8,9,10]],dtype=float)
    ids,_=candidate.select_min_error(pred,[False])
    assert ids.tolist()==[1]


def test_v3_3_full_fork_resume_and_eval(tmp_path,monkeypatch):
    torch.manual_seed(13)
    benchmark=tmp_path/'benchmark';benchmark.mkdir()
    v1=tmp_path/'v1';v2=tmp_path/'v2';v3=tmp_path/'v3';v31=tmp_path/'v31';v32=tmp_path/'v32';v33=tmp_path/'v33'
    identity={'model':'qwen','model_info':{'source':'Qwen/Qwen3-0.6B',
        'resolved_commit':'local-test','dtype':'torch.float32'}}
    (benchmark/'model_identity.json').write_text(json.dumps(identity))
    source=tmp_path/'source_models'/'source.pt';source.parent.mkdir()
    frozen=base.constrained.model_factory('continuity',8,.25)
    torch.save({'schema':base.buffered.SCHEMA,'model':frozen.state_dict(),
                'backbone_identity':identity,
                'config':{'constraint_stage':'continuity','rank':8,'residual_scale':.25}},source)
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    tasks=[{'a':7,'b':2,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':135000,'b':30,'split':'val','operation':'written_product','family':'written'},
           {'a':13,'b':10,'split':'val','operation':'sym_sub','family':'symbolic'},
           {'a':12,'b':5,'split':'val','operation':'algebra_left','family':'algebra'},
           {'a':-11,'b':4,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':1,'b':0,'split':'control','operation':'conditional_gt','family':'conditional'},
           {'a':4,'b':6,'split':'control','operation':'sym_add','family':'symbolic'}]
    monkeypatch.setattr(base.previous,'read_benchmark',lambda path:({'tasks_sha256':'mock'},tasks))
    def fake_eval(path,manifest,all_tasks,split,count=None):
        rows=[r for r in all_tasks if r['split']==split]
        if count is not None:rows=rows[:count]
        return (np.full((len(rows),base.INPUT_WIDTH),.012,dtype=np.float32),
                np.asarray([[r['a'],r['b']] for r in rows],dtype=np.int64),
                [r['operation'] for r in rows],[r['family'] for r in rows])
    monkeypatch.setattr(base.previous,'load_eval',fake_eval)
    monkeypatch.setattr(base.previous,'extract_batched',lambda torch_,frozen_,tok,prompts,dev,max_tok:
                        torch.zeros((len(prompts),base.INPUT_WIDTH)))
    monkeypatch.setattr(base.previous,'verify_capture_alignment',lambda *a,**k:None)
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda name,args:(torch,torch.nn.Identity(),None,'cpu',
        {'source':'Qwen/Qwen3-0.6B','resolved_commit':'local-test','dtype':'torch.float32'})
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    common=['--v3-root',str(benchmark),'--source-checkpoint',str(source),
            '--device','cpu','--dtype','float32','--width','8','--batch-size','3',
            '--replay-batch','4','--replay-capacity','9','--replay-updates','1',
            '--eval-every','1','--checkpoint-every','1','--report-every','1',
            '--val-examples','4','--threads','2']
    assert base.execute(base.parser().parse_args(common+['--results-root',str(v1),'--steps','2']))==0
    assert median.execute(median.parser().parse_args(common+['--results-root',str(v2),
                             '--median-width','8','--steps','3','--fork-v1-root',str(v1)]))==0
    assert legacy.execute(legacy.parser().parse_args(common+['--results-root',str(v3),
                             '--median-width','8','--final-width','8',
                             '--fork-v2-root',str(v2),'--additional-steps','2']))==0
    assert closest.execute(closest.parser().parse_args(common+['--median-width','8','--final-width','8',
                '--closest-width','8','--results-root',str(v31),
                '--fork-v3-root',str(v3),'--additional-steps','2']))==0
    assert pair.execute(pair.parser().parse_args(common+['--median-width','8','--final-width','8',
                '--closest-width','8','--comparison-width','8',
                '--results-root',str(v32),'--fork-v3-1-root',str(v31),'--additional-steps','2']))==0
    parent_sha=hashlib.sha256((v32/'latest.pt').read_bytes()).hexdigest()
    command=common+['--median-width','8','--final-width','8','--closest-width','8',
                    '--comparison-width','8','--error-width','8','--results-root',str(v33)]
    with pytest.raises(RuntimeError,match='ERRORS_TARGET_NOT_ABOVE_CHECKPOINT'):
        candidate.execute(candidate.parser().parse_args(command+['--fork-v3-2-root',str(v32),'--steps','2']))
    assert not v33.exists()
    assert candidate.execute(candidate.parser().parse_args(command+[
              '--fork-v3-2-root',str(v32),'--additional-steps','2']))==0
    ck=torch.load(v33/'latest.pt',weights_only=True)
    assert ck['step']==11 and ck['schema']==candidate.SCHEMA
    assert len(ck['error_estimators'])==11
    report=json.loads((v33/'validation-step-000011.json').read_text())
    assert set(report['per_candidate'])==set(candidate.NAMES)
    assert set(report['per_candidate_calibrated'])==set(candidate.NAMES)
    assert report['selection_holdout_rows']==2 and report['calibration_rows']==2
    assert 'error_calibrated' in report['calibrated_policies_from_validation']
    assert set(['error_raw','error_calibrated','pairwise_11','combined']).issubset(report['policy'])
    assert (v33/'error_calibrated'/'best.pt').exists()
    assert candidate.execute(candidate.parser().parse_args(command+['--additional-steps','1','--resume']))==0
    assert candidate.execute(candidate.parser().parse_args(command+['--eval-only']))==0
    assert candidate.execute(candidate.parser().parse_args(command+['--eval-only','--eval-best']))==0
    assert (v33/'evaluation-best-test.json').is_file()
    assert torch.load(v33/'latest.pt',weights_only=True)['step']==12
    assert (v33/'evaluation-test.json').exists() and (v33/'evaluation-control.json').exists()
    assert hashlib.sha256((v32/'latest.pt').read_bytes()).hexdigest()==parent_sha
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
