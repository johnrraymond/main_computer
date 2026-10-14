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
import nanojev_parallel_result_final_v3 as final


def test_final_head_gradient_and_no_backbone_gradients():
    torch.manual_seed(19)
    z=torch.randn(4,base.INPUT_WIDTH,requires_grad=False)
    ids=torch.tensor([base.OP_IDS['written_product']]*4)
    candidates=np.asarray([
        [600,100,200,300,400,500,600,700,800,900,1000],
        [4050000,100,200,300,400,500,600,700,4050000,900,1000],
        [1,0,0,0,0,0,0,0,0,0,0],
        [100,0,0,0,0,0,0,0,0,0,0]],dtype=np.float64)
    x=final.final_design(z,ids,torch.tensor(candidates[:,0],dtype=torch.float32),
                         candidates,np.median(candidates[:,:8],axis=1))
    model=final.FinalHead(16)
    out=model(x)
    assert out[0].shape==(4,len(final.NAMES))
    truth=np.asarray([500,4050000,1,100],dtype=np.float64)
    loss,parts=final.final_objective(out,candidates,truth,np.asarray([False,False,True,False]))
    loss.backward()
    assert torch.isfinite(loss)
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    assert z.grad is None


def test_source_retention_and_median_minority_selection():
    candidate=np.zeros((4,len(final.NAMES)),dtype=np.float64)
    candidate[:,0]=[100.,600.,300.,400.]
    candidate[:,1]=[200.,1000.,999.,700.]
    candidate[:,8]=[1000.,1000.,300.,700.]
    truth=np.asarray([100.,1000.,300.,700.])
    proposed=np.asarray([1,1,8,1])
    confidence=np.asarray([.99,.99,.99,.99])
    # Source successes 2/4; switching first loses one and switching the last rescues one.
    best=final.calibrate(candidate,proposed,confidence,truth,np.zeros(4,dtype=bool),1.)
    assert best['damaged']==0
    result,index=final.choose(candidate,proposed,confidence,best['threshold'],np.zeros(4,dtype=bool))
    assert result[0]==100. and result[2]==300.
    # No threshold can rescue second/fourth while preserving first when all signals equal.
    assert best['threshold']>0.99
    # Gate never overrides a Boolean even if it would otherwise switch.
    result,index=final.choose(candidate,proposed,confidence,0.,np.array([True,False,False,False]))
    assert index[0]==0 and result[0]==100.


def test_v2_target_preflight_before_writing_any_artifacts(tmp_path):
    v1=tmp_path/'v1';v1.mkdir()
    v2=tmp_path/'v2'
    cfg={'v1_config':{'source':'test'}}
    base.atomic_checkpoint(v1/'latest.pt',{'schema':base.SCHEMA,'config':cfg['v1_config'],'step':2300})
    args=types.SimpleNamespace(resume=False,eval_only=False,steps=500,additional_steps=None)
    with pytest.raises(RuntimeError,match='MEDIAN_TARGET_STEPS_NOT_ABOVE_CHECKPOINT:2300'):
        median.preflight_checkpoint_target(args,v2,cfg,v1)
    assert not v2.exists()
    args.additional_steps=500
    assert median.preflight_checkpoint_target(args,v2,cfg,v1)==2300
    assert args.steps==2800


def test_v3_preflight_and_mocked_fork_resume_eval(tmp_path,monkeypatch):
    torch.manual_seed(12)
    root=tmp_path/'benchmark';root.mkdir()
    v1_root=tmp_path/'v1';v2_root=tmp_path/'v2';v3_root=tmp_path/'v3'
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
    common=['--v3-root',str(root),'--source-checkpoint',str(source),
            '--device','cpu','--dtype','float32','--width','8','--batch-size','3',
            '--replay-batch','4','--replay-capacity','9','--replay-updates','1',
            '--eval-every','1','--checkpoint-every','1','--report-every','1',
            '--val-examples','2','--threads','2']
    assert base.execute(base.parser().parse_args(common+['--results-root',str(v1_root),'--steps','2']))==0
    assert median.execute(median.parser().parse_args(common+['--results-root',str(v2_root),'--steps','3',
                   '--median-width','8','--fork-v1-root',str(v1_root)]))==0
    v2_file=v2_root/'latest.pt';v2_hash=hashlib.sha256(v2_file.read_bytes()).hexdigest()
    args=final.parser().parse_args(common+['--results-root',str(v3_root),'--steps','2',
                        '--median-width','8','--final-width','8','--fork-v2-root',str(v2_root)])
    with pytest.raises(RuntimeError,match='FINAL_TARGET_NOT_ABOVE_CHECKPOINT:3'):
        final.execute(args)
    assert not v3_root.exists(), 'invalid fork must not create output root or checkpoint'
    args=final.parser().parse_args(common+['--results-root',str(v3_root),
                    '--median-width','8','--final-width','8','--fork-v2-root',str(v2_root),
                    '--additional-steps','2'])
    assert final.execute(args)==0
    ck=torch.load(v3_root/'latest.pt',map_location='cpu',weights_only=True)
    assert ck['schema']==final.SCHEMA and ck['step']==5
    assert (v3_root/'final'/'best.pt').exists()
    report=json.loads((v3_root/'validation-step-000005.json').read_text())
    assert 'oracle_numeric_5pct' in report and 'final' in report and 'calibration' in report
    assert len(report['examples'][0]['candidates'])==len(final.NAMES)
    assert np.load(v3_root/'correctness-step-000005.npy').shape==(2,len(final.NAMES)+1)
    assert final.execute(final.parser().parse_args(common+['--results-root',str(v3_root),
                        '--median-width','8','--final-width','8','--additional-steps','1','--resume']))==0
    assert final.execute(final.parser().parse_args(common+['--results-root',str(v3_root),
                        '--median-width','8','--final-width','8','--eval-only']))==0
    assert torch.load(v3_root/'latest.pt',weights_only=True)['step']==6
    assert hashlib.sha256(v2_file.read_bytes()).hexdigest()==v2_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
