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
import nanojev_parallel_result_median_v2 as lab


def test_raw_median_is_literal_even_eight_and_robust_to_outliers():
    candidate=np.asarray([[100,100,101,102,103,104,105,999999999999.],
                          [-1000,-4,-3,-2,-1,0,1,1e9],
                          [1,0,1,1,0,0,1,0]],dtype=np.float64)
    raw,signed=lab.median_controls(candidate,np.asarray([False,False,True]))
    assert raw.tolist()==[102.5,-1.5,1.]
    assert np.isfinite(signed).all()
    assert 100 < signed[0] < 110
    assert signed[2]==1.
    with pytest.raises(ValueError,match='EIGHT'):
        lab.median_controls(candidate[:,:7],np.zeros(3,dtype=bool))
    broken=candidate.copy();broken[0,1]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE'):
        lab.median_controls(broken,np.zeros(3,dtype=bool))


def test_outlier_median_not_oracle_and_source_can_win():
    vec=np.array([[4050000,600000,700000,800000,900000,950000,1000000,1100000],
                  [600000,200000,300000,400000,500000,1000000,1500000,2000000]],dtype=np.float64)
    med,_=lab.median_controls(vec,np.zeros(2,dtype=bool))
    assert med[0]==925000.
    assert med[1]==550000.
    truth=np.array([4050000.,600000.])
    # Median is not guaranteed to preserve an accurate R0.
    assert base.exact_or_relative_hits(vec[:,0],truth,np.zeros(2,dtype=bool)).all()
    assert not base.exact_or_relative_hits(med,truth,np.zeros(2,dtype=bool)).any()


def test_learned_median_gradients_and_zero_init():
    torch.manual_seed(22)
    candidates=np.asarray([[600000.,1e5,4e6,3e6,4e6,5e6,6e6,1e7],
                           [-200.,-100.,-150.,-175.,-220.,-300.,-250.,-110.]],dtype=np.float64)
    flags=np.array([False,False]);raw,_=lab.median_controls(candidates,flags)
    feats=torch.randn(2,base.INPUT_WIDTH,requires_grad=False)
    ops=torch.tensor([base.OP_IDS['written_product'],base.OP_IDS['sym_sub']])
    ref=torch.as_tensor(candidates[:,0])
    x=lab.median_design(feats,ops,ref,candidates,raw)
    h=lab.MedianResidual(16)
    init=h(x)
    assert torch.count_nonzero(init)==0
    loss=h.objective(init,raw,np.asarray([4050000.,-150.]),flags)
    opt=torch.optim.AdamW(h.parameters(),lr=.01)
    opt.zero_grad();loss.backward()
    assert any(p.grad is not None and torch.isfinite(p.grad).all() for p in h.parameters())
    opt.step()
    assert not torch.equal(init.detach(),h(x).detach())
    assert feats.grad is None
    assert torch.isfinite(h(x)).all()


def test_boolean_passthrough_and_numeric_out_of_range_safety():
    predictions=np.array([[0.,500.,1000.,2000.,3000.,4000.,5000.,6000.]],dtype=np.float64)
    raw,signed=lab.median_controls(predictions,np.array([True]))
    head=lab.MedianResidual(8)
    value=head.decoded(np.asarray([.9]),raw,np.array([True]),predictions[:,0])
    assert value.tolist()==[0.]
    assert raw.tolist()==[0.]
    assert signed.tolist()==[0.]
    assert np.isfinite(head.decoded(np.asarray([1e9]),np.asarray([1e15]),np.array([False]),np.array([5.]))).all()


def test_fork_v1_preserves_all_old_weights_optimizer_and_rng(tmp_path):
    torch.manual_seed(1)
    networks={k:base.ResultHead(k,8) for k in base.NAMES}
    opts={k:torch.optim.AdamW(m.parameters(),lr=.001) for k,m in networks.items()}
    op=base.SharedOperationProbe();op_opt=torch.optim.AdamW(op.parameters(),lr=.001)
    stream=base.previous.FreshPairs(17,{(1,2)})
    replay=base.Replay(7,42)
    rows=stream.batch(3)
    replay.add(torch.randn(3,base.INPUT_WIDTH),rows,[[10,12],[13,16],[9,11]])
    base.train_step(networks,opts,op,op_opt,replay.sample(4),'cpu')
    cfg={'fake':'v1-source-config'}
    best={k:{'step':1,'numeric_5pct':0.} for k in base.NAMES}
    v1=tmp_path/'v1.pt'
    base.atomic_checkpoint(v1,base.pack_state(1,cfg,networks,opts,op,op_opt,stream,replay,best))
    digest=hashlib.sha256(v1.read_bytes()).hexdigest()
    new_net={k:base.ResultHead(k,8) for k in base.NAMES}
    new_opts={k:torch.optim.AdamW(m.parameters(),lr=.001) for k,m in new_net.items()}
    new_op=base.SharedOperationProbe();new_op_opt=torch.optim.AdamW(new_op.parameters(),lr=.001)
    new_stream=base.previous.FreshPairs(17,{(1,2)})
    new_replay=base.Replay(7,42)
    new_median=lab.MedianResidual(8)
    new_median_opt=torch.optim.AdamW(new_median.parameters(),lr=.001)
    c={'v1_config':cfg,'median_width':8}
    step,new_best=lab.fork_v1(v1,c,new_net,new_opts,new_op,new_op_opt,new_stream,new_replay)
    assert step==1 and new_best==best
    for k in base.NAMES:
        assert all(torch.equal(p,new_net[k].state_dict()[n]) for n,p in networks[k].state_dict().items())
        assert len(new_opts[k].state_dict()['state'])>0
    state=lab.pack_state(step,c,new_net,new_opts,new_op,new_op_opt,new_stream,new_replay,
                         new_best,new_median,new_median_opt,{'step':0},{'step':0})
    state_path=tmp_path/'v2.pt';base.atomic_checkpoint(state_path,state)
    assert state['schema']==lab.SCHEMA
    restored=lab.restore_v2(state_path,c,new_net,new_opts,new_op,new_op_opt,new_stream,new_replay,new_median,new_median_opt)
    assert restored[0]==1 and restored[1]==best
    assert hashlib.sha256(v1.read_bytes()).hexdigest()==digest
    with pytest.raises(RuntimeError,match='FORK_CONFIG_OR_SOURCE_DRIFT'):
        lab.fork_v1(v1,{'v1_config':{'bad':1}},new_net,new_opts,new_op,new_op_opt,new_stream,new_replay)


def test_mocked_v1_fork_training_resume_evaluate_without_qwen(tmp_path,monkeypatch):
    root=tmp_path/'benchmark';root.mkdir()
    v1_root=tmp_path/'v1';v2_root=tmp_path/'v2'
    identity={'model':'qwen','model_info':{'source':'Qwen/Qwen3-0.6B',
        'resolved_commit':'local-test','dtype':'torch.float32'}}
    (root/'model_identity.json').write_text(json.dumps(identity))
    source=tmp_path/'source.pt'
    frozen=base.constrained.model_factory('continuity',8,.25)
    torch.save({'schema':base.buffered.SCHEMA,'model':frozen.state_dict(),
                'backbone_identity':identity,
                'config':{'constraint_stage':'continuity','rank':8,'residual_scale':.25}},source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    tasks=[{'a':7,'b':2,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':135000,'b':30,'split':'val','operation':'written_product','family':'written'},
           {'a':-11,'b':4,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':1,'b':0,'split':'control','operation':'conditional_gt','family':'conditional'}]
    monkeypatch.setattr(base.previous,'read_benchmark',lambda path:({'tasks_sha256':'mock'},tasks))
    def fake_eval(path,manifest,all_tasks,split,count=None):
        selected=[r for r in all_tasks if r['split']==split]
        if count is not None:selected=selected[:count]
        return (np.full((len(selected),base.INPUT_WIDTH),.012,dtype=np.float32),
                np.asarray([[r['a'],r['b']] for r in selected],dtype=np.int64),
                [r['operation'] for r in selected],[r['family'] for r in selected])
    monkeypatch.setattr(base.previous,'load_eval',fake_eval)
    monkeypatch.setattr(base.previous,'extract_batched',lambda torch_,frozen_,tok,prompts,dev,max_tok:
                        torch.zeros((len(prompts),base.INPUT_WIDTH)))
    monkeypatch.setattr(base.previous,'verify_capture_alignment',lambda *a,**k:None)
    cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    cassette.load_source=lambda name,args:(torch,torch.nn.Identity(),None,'cpu',
        {'source':'Qwen/Qwen3-0.6B','resolved_commit':'local-test','dtype':'torch.float32'})
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',cassette)
    common=['--v3-root',str(root),'--source-checkpoint',str(source),
            '--device','cpu','--dtype','float32','--width','8','--batch-size','3',
            '--replay-batch','4','--replay-capacity','9','--replay-updates','1',
            '--eval-every','1','--checkpoint-every','1','--report-every','1',
            '--val-examples','2','--threads','2']
    assert base.execute(base.parser().parse_args(common+['--results-root',str(v1_root),'--steps','2']))==0
    v1_file=v1_root/'latest.pt';v1_digest=hashlib.sha256(v1_file.read_bytes()).hexdigest()
    assert lab.execute(lab.parser().parse_args(common+['--results-root',str(v2_root),'--steps','3',
                '--median-width','8','--fork-v1-root',str(v1_root)]))==0
    ck=torch.load(v2_root/'latest.pt',weights_only=True)
    assert ck['step']==3 and ck['schema']==lab.SCHEMA
    assert ck['median_best']['step']>=3
    assert (v2_root/lab.HEAD/'best.pt').is_file()
    assert (v2_root/lab.HEAD/'latest.pt').is_file()
    report=json.loads((v2_root/'validation-step-000003.json').read_text())
    assert set(report['registers'])==set(lab.ALL_NAMES)
    assert len(report['examples'][0]['candidates'])==len(lab.ALL_NAMES)
    assert np.load(v2_root/'correctness-step-000003.npy').shape==(2,len(lab.ALL_NAMES))
    assert report['registers']['median_raw']['boolean_exact'] is None
    assert hashlib.sha256(v1_file.read_bytes()).hexdigest()==v1_digest
    assert lab.execute(lab.parser().parse_args(common+['--results-root',str(v2_root),'--steps','4',
                '--median-width','8','--resume']))==0
    assert lab.execute(lab.parser().parse_args(common+['--results-root',str(v2_root),
                '--median-width','8','--eval-only']))==0
    assert torch.load(v2_root/'latest.pt',weights_only=True)['step']==4
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    with pytest.raises(RuntimeError,match='SOURCE_DRIFT'):
        lab.fork_v1(v1_file,{'v1_config':{'wrong':1}}, {},{}, None,None,None,None)
