"""Tests for closest-estimator training and source-preserving v3 fork."""
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


def proposals(rows):
    assert all(len(row)==closest.N for row in rows)
    return np.asarray(rows,dtype=np.float64)


def test_nearest_including_all_wrong_and_ties():
    # All outcomes fail 5%, yet one alternative is closest.
    values = proposals([[100,300,400,600,900,700,800,1500,400,1200,950],
                        [100,200,300,400,500,600,700,800,900,1000,1100],
                        [100,100,200,300,400,500,600,700,800,900,1000]])
    truth = np.array([235.,810.,100.])
    winners=closest.nearest_target(values,truth)
    assert winners.tolist()==[1,7,0]  # exact tie between R0 and R1 favors R0
    relative,transformed=closest.numerical_errors(values,truth)
    assert relative.shape==(3,closest.N) and transformed.shape==relative.shape
    assert relative[0,winners[0]]>.05
    assert np.all(transformed>=0.)


def test_rank_gradient_and_all_wrong_supervision():
    torch.manual_seed(22)
    values=proposals([[100,200,300,400,500,600,700,800,900,1000,1100],
                      [500,600,700,800,900,1000,1100,1200,1300,1400,1500],
                      [1,2,3,4,5,6,7,8,9,10,11]])
    truth=np.asarray([750.,1100.,8.])
    feat=torch.randn(3,base.INPUT_WIDTH)
    op=torch.tensor([base.OP_IDS['written_product']]*3)
    design=legacy.final_design(feat,op,torch.tensor(values[:,0],dtype=torch.float32),
                                values,np.median(values[:,:8],axis=1))
    model=closest.ClosestFinal(16)
    assert torch.allclose(model.net[-1].bias,torch.zeros(closest.N))
    output=model(design)
    loss,parts=closest.closest_objective(output,values,truth,np.zeros(3,dtype=bool))
    loss.backward()
    assert torch.isfinite(loss)
    assert parts['minority_examples']==3
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
    assert feat.grad is None
    chosen,estimates=closest.closest_predictions(output,np.asarray([False,True,False]))
    assert chosen[1]==0 and estimates.shape==(3,closest.N)
    with pytest.raises(ValueError,match='CLOSEST_INVALID_NUMERICAL_TARGETS'):
        closest.numerical_errors(np.ones((3,8)),truth)


def test_unconditional_selection_and_frontier_not_gate():
    values=np.zeros((4,closest.N),dtype=np.float64)
    values[:,0]=[100,200,300,400]
    values[:,1]=[120,220,350,401]
    truth=np.asarray([120,220,300,401],dtype=float)
    estimates=np.full_like(values,10.)
    estimates[:,0]=.2
    estimates[:,1]=.1
    winner=np.array([1,1,1,1]);chosen=closest.select(values,winner)
    assert np.array_equal(chosen,values[:,1]),'ungated FINAL always makes a selection'
    frontier=closest.retention_frontier(values,winner,estimates,truth,np.zeros(4,dtype=bool))
    assert {round(x['required_retention'],2) for x in frontier}=={1.,.99,.98,.95,0.}
    assert frontier[0]['damaged']==0
    assert frontier[-1]['numeric_5pct']>=frontier[0]['numeric_5pct']


def test_v3_1_preflight_fork_resume_eval(tmp_path,monkeypatch):
    torch.manual_seed(12)
    benchmark=tmp_path/'benchmark';benchmark.mkdir()
    v1=tmp_path/'v1';v2=tmp_path/'v2';v3=tmp_path/'v3';v31=tmp_path/'v31'
    identity={'model':'qwen','model_info':{'source':'Qwen/Qwen3-0.6B',
        'resolved_commit':'local-test','dtype':'torch.float32'}}
    (benchmark/'model_identity.json').write_text(json.dumps(identity))
    source=tmp_path/'source.pt'
    frozen=base.constrained.model_factory('continuity',8,.25)
    torch.save({'schema':base.buffered.SCHEMA,'model':frozen.state_dict(),
                'backbone_identity':identity,
                'config':{'constraint_stage':'continuity','rank':8,'residual_scale':.25}},source)
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    tasks=[{'a':7,'b':2,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':135000,'b':30,'split':'val','operation':'written_product','family':'written'},
           {'a':-11,'b':4,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':1,'b':0,'split':'control','operation':'conditional_gt','family':'conditional'}]
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
            '--val-examples','2','--threads','2']
    assert base.execute(base.parser().parse_args(common+['--results-root',str(v1),'--steps','2']))==0
    assert median.execute(median.parser().parse_args(common+['--results-root',str(v2),
                             '--median-width','8','--steps','3','--fork-v1-root',str(v1)]))==0
    assert legacy.execute(legacy.parser().parse_args(common+['--results-root',str(v3),
                             '--median-width','8','--final-width','8',
                             '--fork-v2-root',str(v2),'--additional-steps','2']))==0
    v3_hash=hashlib.sha256((v3/'latest.pt').read_bytes()).hexdigest()
    common31=common+['--median-width','8','--final-width','8','--closest-width','8',
                     '--results-root',str(v31)]
    args=closest.parser().parse_args(common31+['--fork-v3-root',str(v3),'--steps','2'])
    with pytest.raises(RuntimeError,match='CLOSEST_TARGET_NOT_ABOVE_CHECKPOINT:5'):
        closest.execute(args)
    assert not v31.exists(),'invalid target must not touch output directory'
    args=closest.parser().parse_args(common31+['--fork-v3-root',str(v3),'--additional-steps','2'])
    assert closest.execute(args)==0
    ck=torch.load(v31/'latest.pt',map_location='cpu',weights_only=True)
    assert ck['schema']==closest.SCHEMA and ck['step']==7
    assert (v31/'closest'/'best.pt').exists()
    report=json.loads((v31/'validation-step-000007.json').read_text())
    assert report['nearest']['numeric_rows']==2
    assert len(report['nearest']['selection_by_name_numeric'])==closest.N
    assert 'nearest_candidate_accuracy_all_wrong' in report['nearest']
    assert (v31/'correctness-step-000007.npy').exists()
    assert closest.execute(closest.parser().parse_args(common31+['--additional-steps','1','--resume']))==0
    assert closest.execute(closest.parser().parse_args(common31+['--eval-only']))==0
    assert closest.nearest_target(np.tile(np.arange(closest.N, dtype=float), (1,1)), np.array([3.4])).tolist()==[3]
    assert torch.load(v31/'latest.pt',weights_only=True)['step']==8
    assert hashlib.sha256((v3/'latest.pt').read_bytes()).hexdigest()==v3_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
