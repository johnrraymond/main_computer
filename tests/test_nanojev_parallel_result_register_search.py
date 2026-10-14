"""CPU-only tests; never fetches Qwen or reads user checkpoints."""
import sys
from pathlib import Path
import numpy as np
import pytest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import nanojev_parallel_result_register_search as lab


def test_operation_contract_and_numeric_limits():
    assert len(lab.OP_CLASSES)==20
    expected={
        'sym_add':12, 'sym_sub':2, 'sym_reverse_sub':-2,
        'sym_multiply':35, 'sym_absdiff':2, 'sym_max':7,
        'written_add':12,'written_sub':2,'written_product':35,
        'written_difference':-2,'written_greater':1,
        'algebra_left':2,'algebra_right':-2,'algebra_difference':12,
        'conditional_gt':1,'conditional_min':5,'conditional_positive':5,
        'multistep_double':9,'multistep_combine':24,'multistep_difference':9,
    }
    for operation,answer in expected.items():
        assert lab.result_value(7,5,operation)==answer,operation
    assert lab.result_value(2_000_000,2_000_000,'multistep_square')==16_000_000_000_000
    assert lab.result_value(3,2,'written_average')==2.5
    with pytest.raises(ValueError,match='UNSUPPORTED_OPERATION'):
        lab.result_value(2,3,'unknown')


def test_exact_large_decimal_and_bit_targets():
    a=np.asarray([16_000_000_000_000.,4_050_000.,135000.,-135000.,0.],dtype=np.float64)
    left,place,length=lab.decimal_targets(a)
    assert length.tolist()==[13,6,5,5,0]
    for idx,v in enumerate(a):
        s=str(abs(int(v)))
        assert ''.join(str(x) for x in left[idx] if x!=10)==s
        reconstructed=sum(int(d)*10**p for p,d in enumerate(place[idx]))
        assert reconstructed==abs(int(v))
    assert (lab.bit_targets([16_000_000_000_000.])[0]*2.**np.arange(lab.N_BITS)).sum()==16_000_000_000_000
    exponent,mantissa=lab.scientific_targets([135000.,4_050_000.])
    assert exponent.tolist()==[5,6]
    assert np.allclose(mantissa,[np.log10(1.35),np.log10(4.05)],atol=1e-6)
    flags,_=lab.ordinal_targets([135000.])
    assert flags[0].sum()==6


def test_no_oracle_operation_in_design_and_separate_backprop():
    torch.manual_seed(19)
    models={k:lab.ResultHead(k,16) for k in lab.NAMES}
    optimizers={k:torch.optim.AdamW(v.parameters(),lr=.001) for k,v in models.items()}
    op=lab.SharedOperationProbe()
    op_opt=torch.optim.AdamW(op.parameters(),lr=.001)
    torch.manual_seed(24)
    rows=[(torch.randn(lab.INPUT_WIDTH,dtype=torch.float16),135000,30,'written_product',
           110000,25),
          (torch.randn(lab.INPUT_WIDTH,dtype=torch.float16),0,0,'sym_add',0,0),
          (torch.randn(lab.INPUT_WIDTH,dtype=torch.float16),1,2,'conditional_gt',1,2),
          (torch.randn(lab.INPUT_WIDTH,dtype=torch.float16),2000000,2000000,'sym_multiply',
           1500000,1800000)]
    before={k:next(iter(v.parameters())).detach().clone() for k,v in models.items()}
    loss,stats=lab.train_step(models,optimizers,op,op_opt,rows,torch.device('cpu'))
    assert np.isfinite(loss)
    assert set(stats)==set(lab.NAMES)
    assert all(np.isfinite(v) for v in stats.values())
    assert all(not torch.equal(before[k],next(iter(models[k].parameters()))) for k in lab.NAMES)
    assert not rows[0][0].requires_grad


def test_reporting_does_not_inflate_boolean_numeric():
    values=np.array([4050000.,0.,1.,16e12]);bools=np.array([False,False,True,False])
    prediction=np.array([4000000.,0.,0.,16e12]);summary=lab.diagnostic(prediction,values,bools)
    assert summary['rows']==4 and summary['numeric_rows']==3
    assert summary['numeric_5pct']==1.0 and summary['boolean_exact']==0.
    assert summary['overall_5pct']==.75
    assert lab.exact_or_relative_hits([0],[0],[False])[0]
    assert not lab.exact_or_relative_hits([1],[0],[False])[0]


def test_evaluate_shared_operation_and_source_with_no_gold_operands():
    torch.manual_seed(7)
    # Fake source satisfies the same raw interface, but never sees truth.
    class ZeroSource(torch.nn.Module):
        def forward(self,x):
            logits=torch.zeros((len(x),38),device=x.device)
            logits[:,0]=3.;logits[:,19]=3.
            return logits
    class FixedOps(torch.nn.Module):
        def forward(self,x):
            result=torch.full((len(x),len(lab.OP_CLASSES)),-100.,device=x.device)
            result[:,lab.OP_IDS['sym_add']]=100.
            return result
    net={name:lab.ResultHead(name,8) for name in lab.NAMES}
    feats=np.zeros((3,lab.INPUT_WIDTH),dtype=np.float32)
    pair=np.array([[4,7],[0,0],[9,-2]],dtype=np.int64)
    ops=['sym_add']*3;families=['symbolic']*3
    report,matrix=lab.evaluate(net,FixedOps(),ZeroSource(),(feats,pair,ops,families),'cpu',batch_size=2)
    assert matrix.shape==(3,8)
    assert report['operation_accuracy']==1.
    assert report['rows']==3
    assert set(report['registers'])=={'source',*lab.NAMES}
    assert all(name in report['overlap_correct_counts'] for name in ('source',)+lab.NAMES)


def test_checkpoint_resume_restores_independent_optimizers_and_replay(tmp_path):
    torch.manual_seed(12)
    models={k:lab.ResultHead(k,8) for k in lab.NAMES}
    opts={k:torch.optim.AdamW(m.parameters(),lr=.001) for k,m in models.items()}
    op=lab.SharedOperationProbe();oop=torch.optim.AdamW(op.parameters(),lr=.001)
    stream=lab.previous.FreshPairs(3,{(1,2)})
    replay=lab.Replay(6,9)
    rows=stream.batch(2)
    sample=torch.randn(2,lab.INPUT_WIDTH)
    replay.add(sample,rows,[[3,4],[5,6]])
    # Advance all optimizers and their independent internal states.
    lab.train_step(models,opts,op,oop,replay.sample(3),'cpu')
    config={'fingerprint':'frozen-source-hash'}
    scores={k:{'step':1,'numeric_5pct':.1} for k in lab.NAMES}
    saved=lab.pack_state(1,config,models,opts,op,oop,stream,replay,scores)
    cp=tmp_path/'latest.pt';lab.atomic_checkpoint(cp,saved)
    expected_pairs=stream.batch(2)
    expected_replay=[(r[1],r[2]) for r in replay.sample(4)]
    fresh_models={k:lab.ResultHead(k,8) for k in lab.NAMES}
    fresh_opts={k:torch.optim.AdamW(m.parameters(),lr=.001) for k,m in fresh_models.items()}
    fresh_op=lab.SharedOperationProbe();fresh_oop=torch.optim.AdamW(fresh_op.parameters(),lr=.001)
    fresh_stream=lab.previous.FreshPairs(3,{(1,2)})
    fresh_replay=lab.Replay(6,9)
    step,best=lab.restore_state(cp,config,fresh_models,fresh_opts,fresh_op,fresh_oop,fresh_stream,fresh_replay)
    assert step==1 and best==scores
    assert fresh_stream.batch(2)==expected_pairs
    assert [(r[1],r[2]) for r in fresh_replay.sample(4)]==expected_replay
    for k in lab.NAMES:
        for param in models[k].state_dict():
            assert torch.equal(models[k].state_dict()[param],fresh_models[k].state_dict()[param])
        assert len(fresh_opts[k].state_dict()['state'])>0
    with pytest.raises(RuntimeError,match='CONFIG_OR_SOURCE_DRIFT'):
        lab.restore_state(cp,{'fingerprint':'different'},fresh_models,fresh_opts,fresh_op,fresh_oop,fresh_stream,fresh_replay)


def test_full_mocked_training_resume_and_eval_only_no_qwen(monkeypatch,tmp_path):
    """Exercise main orchestration with real saved source model, stubbed captures."""
    import json,hashlib,sys,types
    root=tmp_path/'benchmark';root.mkdir()
    result_root=tmp_path/'results'
    identity={'model':'qwen','model_info':{'source':'Qwen/Qwen3-0.6B',
        'resolved_commit':'local-fixture','dtype':'torch.float32'}}
    (root/'model_identity.json').write_text(json.dumps(identity))
    source=tmp_path/'source.pt'
    frozen=lab.constrained.model_factory('continuity',8,.25)
    torch.save({'schema':lab.buffered.SCHEMA,'model':frozen.state_dict(),
                'backbone_identity':identity,
                'config':{'constraint_stage':'continuity','rank':8,'residual_scale':.25}},source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    tasks=[{'a':7,'b':2,'split':'val','operation':'sym_add','family':'symbolic'},
           {'a':135000,'b':30,'split':'val','operation':'written_product','family':'written'},
           {'a':-11,'b':4,'split':'test','operation':'sym_sub','family':'symbolic'},
           {'a':1,'b':0,'split':'control','operation':'conditional_gt','family':'conditional'}]
    monkeypatch.setattr(lab.previous,'read_benchmark',lambda r:({'tasks_sha256':'fixture'},tasks))
    def fake_eval(r,manifest,all_tasks,split,count=None):
        ts=[t for t in all_tasks if t['split']==split]
        if count is not None:ts=ts[:count]
        return (np.ones((len(ts),lab.INPUT_WIDTH),dtype=np.float32)*.012,
                np.array([[t['a'],t['b']] for t in ts],dtype=np.int64),
                [t['operation'] for t in ts],[t['family'] for t in ts])
    monkeypatch.setattr(lab.previous,'load_eval',fake_eval)
    monkeypatch.setattr(lab.previous,'extract_batched',lambda torch_,frozen_,tokenizer_,prompts,device,max_tokens:
        torch.zeros((len(prompts),lab.INPUT_WIDTH),dtype=torch.float32))
    monkeypatch.setattr(lab.previous,'verify_capture_alignment',lambda *a,**k:None)
    fake_cassette=types.ModuleType('nanojev_numerate_spine_cassette_smoke')
    def fake_load(src,args):
        return (torch,torch.nn.Identity(),None,'cpu',{'resolved_commit':'local-fixture','source':'Qwen/Qwen3-0.6B',
                'dtype':'torch.float32'})
    fake_cassette.load_source=fake_load
    monkeypatch.setitem(sys.modules,'nanojev_numerate_spine_cassette_smoke',fake_cassette)
    common=['--v3-root',str(root),'--source-checkpoint',str(source),
            '--results-root',str(result_root),'--device','cpu','--dtype','float32',
            '--width','8','--batch-size','3','--replay-batch','4','--replay-capacity','9',
            '--replay-updates','1','--eval-every','1','--checkpoint-every','1',
            '--report-every','1','--val-examples','2','--threads','2']
    assert lab.execute(lab.parser().parse_args(common+['--steps','2']))==0
    first=torch.load(result_root/'latest.pt',weights_only=True)
    assert first['step']==2
    assert all((result_root/name/'best.pt').is_file() for name in lab.NAMES)
    assert all((result_root/name/'latest.pt').is_file() for name in lab.NAMES)
    assert len(list(result_root.glob('validation-step-*.json')))==2
    assert lab.execute(lab.parser().parse_args(common+['--steps','3','--resume']))==0
    resumed=torch.load(result_root/'latest.pt',weights_only=True)
    assert resumed['step']==3
    assert lab.execute(lab.parser().parse_args(common+['--eval-only']))==0
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    # Source exact fingerprint is persisted in the global checkpoint.
    assert resumed['config']['source_sha256']==digest
