"""Bottom-heavy update-cycle, numerical supervision, parent preservation and lifecycle."""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(ROOT))
import nanojev_parallel_result_bottom_heavy_v3_4 as m


def test_phase_schedule_exact_80_15_5():
    labels=[m.phase_for_tick(i) for i in range(200)]
    assert labels.count('intermediate')==160
    assert labels.count('result')==30
    assert labels.count('final')==10
    assert labels[19]=='final' and labels[20]=='intermediate'
    with pytest.raises(ValueError):m.phase_for_tick(-1)


def test_operand_losses_supply_real_gradients_without_touching_parent():
    torch.manual_seed(20)
    parent=m.base.constrained.model_factory('continuity',8,.25)
    import copy
    live=copy.deepcopy(parent)
    auxiliary=m.OperandAux(8)
    h=torch.randn(5,m.base.INPUT_WIDTH)
    xy=np.array([[1,4],[-34,0],[32000,-129],[1999999,-456],[0,0]],dtype=np.int64)
    original={n:v.detach().clone() for n,v in parent.state_dict().items()}
    hierarchical_loss,stats=m.hierarchy_objective(live,h,xy)
    auxiliary_loss,aux=m.operand_aux_objective(auxiliary(h),xy)
    assert aux['digits']>0 and aux['regime']>0 and aux['coordinate']>=0
    (hierarchical_loss+auxiliary_loss).backward()
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in live.parameters())
    assert any(p.grad is not None and p.grad.abs().sum()>0 for p in auxiliary.parameters())
    assert all(torch.equal(p,original[n]) for n,p in parent.state_dict().items())


def test_valid_supervision_regardless_of_final_answer():
    f=torch.randn(2,m.base.INPUT_WIDTH)
    aux=m.OperandAux(8)
    l,_=m.operand_aux_objective(aux(f),np.array([[123,-93],[-200,0]]))
    assert torch.isfinite(l)
    with pytest.raises(ValueError,match='BOTTOM_OPERAND_RANGE_OR_SHAPE'):
        m.operand_aux_objective(aux(f),np.array([[123,-93],[3000000,0]]))



def test_full_parent_fork_train_resume_eval(tmp_path,monkeypatch):
    import json, types
    from nanojev_parallel_result_final_errors_v3_3 import execute as _unused
    import nanojev_parallel_result_register_search as base
    import nanojev_parallel_result_median_v2 as median
    import nanojev_parallel_result_final_v3 as legacy
    import nanojev_parallel_result_final_closest_v3_1 as closest
    import nanojev_parallel_result_final_pairwise_v3_2 as pair
    import nanojev_parallel_result_final_errors_v3_3 as candidate
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
    root=tmp_path/'v34'
    parent_v33_sha=hashlib.sha256((v33/'latest.pt').read_bytes()).hexdigest()
    command=common+['--median-width','8','--final-width','8','--closest-width','8',
                    '--comparison-width','8','--error-width','8',
                    '--aux-width','8','--results-root',str(root)]
    with pytest.raises(RuntimeError,match='BOTTOM_TARGET_NOT_ABOVE_CHECKPOINT'):
        m.execute(m.parser().parse_args(command+['--fork-v3-3-root',str(v33),'--steps','12']))
    assert not root.exists()
    # 21 iterations exercise a whole 16:3:1 schedule and a repeat.
    efficient=command+['--eval-every','10','--checkpoint-every','10','--report-every','10']
    assert m.execute(m.parser().parse_args(efficient+['--fork-v3-3-root',str(v33),
                                               '--additional-steps','21']))==0
    ck=torch.load(root/'latest.pt',weights_only=True)
    assert ck['schema']==m.SCHEMA and ck['step']==33
    assert ck['bottom_phase_counts']=={'intermediate':17,'result':3,'final':1}
    assert ck['bottom_phase_tick']==21
    assert ck['bottom_operand_decoder'] and ck['bottom_operand_aux']
    before=torch.load(v33/'latest.pt',weights_only=True)
    assert any(not torch.equal(t,before['models']['residual'][n])
               for n,t in ck['models']['residual'].items())
    assert any(not torch.equal(t,before['error_estimators']['source'][n])
               for n,t in ck['error_estimators']['source'].items())
    original=torch.load(source,weights_only=True)['model']
    assert any(not torch.equal(t,original[n]) for n,t in ck['bottom_operand_decoder'].items())
    assert all(torch.equal(t,before['operation_probe'][n]) for n,t in ck['operation_probe'].items())
    record=json.loads((root/'validation-step-000033.json').read_text())
    assert set(record['intermediaries'])=={'original_frozen','hierarchy_refined','aux_continuous'}
    assert (root/'intermediate_primary'/'best.pt').is_file()
    assert m.execute(m.parser().parse_args(efficient+['--additional-steps','1','--resume']))==0
    assert torch.load(root/'latest.pt',weights_only=True)['bottom_phase_tick']==22
    assert m.execute(m.parser().parse_args(command+['--eval-only']))==0
    assert m.execute(m.parser().parse_args(command+['--eval-only','--eval-best']))==0
    assert (root/'evaluation-best-test.json').is_file()
    assert (root/'evaluation-control.json').is_file()
    assert hashlib.sha256((v33/'latest.pt').read_bytes()).hexdigest()==parent_v33_sha
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_sha

    # Validation-best parent is an independent immutable fork option.
    chosen=tmp_path/'v34_best_parent'
    assert m.execute(m.parser().parse_args(command[:-2]+['--results-root',str(chosen),
                   '--fork-v3-3-root',str(v33),'--fork-parent-best',
                   '--additional-steps','1']))==0
    best_parent=torch.load(v33/'error_calibrated'/'best.pt',weights_only=True)
    child=torch.load(chosen/'latest.pt',weights_only=True)
    assert child['step']==best_parent['step']+1
