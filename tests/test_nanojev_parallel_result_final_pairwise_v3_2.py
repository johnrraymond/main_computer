"""End-to-end and mathematical tests for v3.2 parallel arbitration."""
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
import nanojev_parallel_result_final_pairwise_v3_2 as candidate


def test_all_wrong_comparison_supervision_and_gradient():
    torch.manual_seed(3)
    p=np.asarray([[100.,200.,300.,400.,500.,600.,700.,800.,900.,1000.,1100.],
                  [500.,700.,1000.,300.,200.,100.,140.,180.,900.,100.,200.]])
    truth=np.asarray([770.,170.])
    features=torch.randn(2,base.INPUT_WIDTH)
    op=torch.tensor([base.OP_IDS['written_product']]*2)
    design=candidate.feature_design(features,p,op)
    assert design['numeric_only'].shape==(2,candidate.NUMERIC_WIDTH)
    assert design['backbone_only'].shape==(2,base.INPUT_WIDTH+len(base.OP_CLASSES))
    assert design['combined'].shape[1]==base.INPUT_WIDTH+candidate.NUMERIC_WIDTH+len(base.OP_CLASSES)
    nets={k:candidate.BinaryComparator(design[k].shape[1],16) for k in candidate.ABLATIONS}
    pair=candidate.PairwiseRanker(design['combined'].shape[1],16)
    loss,parts=candidate.objectives(nets,pair,design,p,truth,[False,False])
    assert parts['minority_nearest']>0
    loss.backward()
    assert all(torch.isfinite(q.grad).all() for m in list(nets.values())+[pair] for q in m.parameters() if q.grad is not None)
    assert features.grad is None
    assert len(candidate.PAIRS)==55
    labels=candidate.closest_targets(p,truth)[0].argmin(axis=1)
    assert labels[0]==7
    assert labels[1]==7


def test_feature_ablation_and_boolean_policy():
    p=np.tile(np.arange(1,candidate.N+1,dtype=float),(2,1))
    f=torch.randn(2,base.INPUT_WIDTH)
    ops=torch.tensor([base.OP_IDS['written_product']]*2)
    d=candidate.feature_design(f,p,ops)
    f2=f+1000
    d2=candidate.feature_design(f2,p,ops)
    assert torch.equal(d['numeric_only'],d2['numeric_only'])
    assert not torch.equal(d['backbone_only'],d2['backbone_only'])
    assert candidate.choose_ids(np.tile(np.arange(candidate.N),(2,1)),[False,True]).tolist()==[candidate.N-1,0]
    # A false prediction of 'Boolean' must not get truth labels during inference.
    loss,parts=candidate.objectives({k:candidate.BinaryComparator(d[k].shape[1],8) for k in candidate.ABLATIONS},
        candidate.PairwiseRanker(d['combined'].shape[1],8),d,p,[5.,6.],[True,True])
    assert loss.item()==0
    assert parts['minority_nearest']==0


def test_nearest_regret_rescues_and_binary_confusion():
    p=np.asarray([[100,120,150,160,170,180,190,110,140,130,125],
                  [100,120,150,160,170,180,190,110,140,130,125],
                  [100,120,150,160,170,180,190,110,140,130,125]],dtype=float)
    truth=np.asarray([120.,100.,106.])
    choose=np.asarray([1,0,candidate.RESIDUAL_INDEX])
    metr=candidate.measure(p,truth,[False,False,False],choose)
    assert metr['rescued_5pct']==2
    assert metr['damaged_5pct']==0
    assert metr['improved']>=1
    assert metr['mean_log_regret']>=0
    logit=np.array([1.,-1.,1.]);diag=candidate.binary_diagnostics(p,truth,[False]*3,logit,choose)
    assert set(diag['comparator'])>=set(['tp','tn','fp','fn','balanced_accuracy'])
    front=candidate.threshold_frontier(p,truth,[False]*3,choose,logit)
    assert len(front)==5 and front[0]['damaged']==0


def test_pair_votes_and_ties():
    z=torch.zeros(2,len(candidate.PAIRS))
    s=candidate.pair_scores(z)
    assert s.shape==(2,candidate.N)
    assert s.argmax(dim=1).tolist()==[0,0]
    idx=candidate.PAIRS.index((0,1))
    z[:,idx]=8
    s=candidate.pair_scores(z)
    assert s[:,0].gt(s[:,1]).all()
    assert torch.allclose(s.sum(dim=1),torch.zeros(2))


def test_v3_2_preflight_fork_resume_eval(tmp_path,monkeypatch):
    torch.manual_seed(12)
    benchmark=tmp_path/'benchmark';benchmark.mkdir()
    v1=tmp_path/'v1';v2=tmp_path/'v2';v3=tmp_path/'v3';v31=tmp_path/'v31';v32=tmp_path/'v32'
    identity={'model':'qwen','model_info':{'source':'Qwen/Qwen3-0.6B',
        'resolved_commit':'local-test','dtype':'torch.float32'}}
    (benchmark/'model_identity.json').write_text(json.dumps(identity))
    source=tmp_path/'source_models'/'source.pt'
    source.parent.mkdir()
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
    assert closest.execute(closest.parser().parse_args(common+['--median-width','8','--final-width','8',
                '--closest-width','8','--results-root',str(v31),
                '--fork-v3-root',str(v3),'--additional-steps','2']))==0
    parent_sha=hashlib.sha256((v31/'latest.pt').read_bytes()).hexdigest()
    command=common+['--median-width','8','--final-width','8',
                    '--closest-width','8','--comparison-width','8',
                    '--results-root',str(v32)]
    with pytest.raises(RuntimeError,match='PAIRWISE_TARGET_NOT_ABOVE_CHECKPOINT'):
        candidate.execute(candidate.parser().parse_args(command+['--fork-v3-1-root',str(v31),'--steps','2']))
    assert not v32.exists()
    assert candidate.execute(candidate.parser().parse_args(command+[
              '--fork-v3-1-root',str(v31),'--additional-steps','2']))==0
    ck=torch.load(v32/'latest.pt',weights_only=True)
    assert ck['step']==9 and ck['schema']==candidate.SCHEMA
    assert (v32/'pairwise_11'/'best.pt').exists()
    report=json.loads((v32/'validation-step-000009.json').read_text())
    assert len(report['models'])==4
    assert len(report['frontiers_validation_only']['pairwise_11'])==5
    assert report['models']['combined']['numeric_rows']==2
    assert report['reference']['source']['source_nearest_accuracy']>=0
    assert candidate.execute(candidate.parser().parse_args(command+['--additional-steps','1','--resume']))==0
    assert candidate.execute(candidate.parser().parse_args(command+['--eval-only']))==0
    assert torch.load(v32/'latest.pt',weights_only=True)['step']==10
    assert hashlib.sha256((v31/'latest.pt').read_bytes()).hexdigest()==parent_sha
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha
