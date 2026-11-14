"""Intel-only HDB rescore contracts (no GPU required for these tests)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import pytest

from tools import nanojev_qwen_intel_dml as intel
from tools import nanojev_qwen_lm_radius_ladder as ladder
from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_hdb_balance as hdb
from tools import nanojev_qwen_competency_diagnostic as comp


def test_dml_refuses_non_rescore_stages_and_training(monkeypatch):
    for spec in (
        ['--stage','run','--frontier','--frontier-hdb'],
        ['--stage','hdb-rescore','--frontier','--frontier-hdb'],
        ['--stage','hdb-rescore','--rescore-live','--frontier'],
    ):
        with pytest.raises(ValueError,match='INTEL_DML_READONLY_LIVE_HDB_RESCORE_ONLY'):
            ladder.main([*spec,'--device','dml'])


def test_dml_cli_dispatches_to_rescore_without_cuda(monkeypatch):
    seen=[]
    monkeypatch.setattr(joint,'rescore_hdb',lambda args:seen.append((args.device,args.dml_index,args.intel_smoke_per_tier)) or {'ok':True})
    out=ladder.main(['--stage','hdb-rescore','--rescore-live','--frontier','--frontier-hdb',
                     '--device','dml','--dml-index','1','--intel-smoke-per-tier','2'])
    assert out=={'ok':True}
    assert seen==[('dml',1,2)]


def test_adapter_selection_will_never_select_nvidia(monkeypatch):
    class FakeDml:
        def device_count(self):return 2
        def device_name(self,i):return ('NVIDIA GeForce RTX 3060 Laptop GPU', 'Intel(R) Iris(R) Xe Graphics')[i]
        def device(self,i):return SimpleNamespace(type='privateuseone',index=i)
    monkeypatch.setitem(sys.modules,'torch_directml',FakeDml())
    with pytest.raises(RuntimeError,match='INTEL_DML_REFUSES_NON_INTEL_ADAPTER'):
        intel.intel_device(0)
    # Probe is not run for the rejected NVIDIA selection.


def test_immutable_intel_smoke_deck_and_read_only_checkpoint(tmp_path,monkeypatch):
    import torch
    out=tmp_path/'live'
    out.mkdir()
    monkeypatch.setattr(joint,'_out',lambda args:out)
    rows=[hdb._row(i,0,(9,1,3),label='heldout') for i in range(9)]
    # Two examples per tier, proving first-per-tier selection.
    rows += [hdb._row(i,1,(9,1,3),label='heldout') for i in range(9)]
    ladder._atomic_json(out/'hdb_eval_deck.json',rows)
    before_deck=(out/'hdb_eval_deck.json').read_bytes()
    state={'step':100,'last_checked_step':100,'policy':{'hdb':{'max_tokens':1024,
           'max_new_tokens':48,'max_offset':64},'max_seq':768},
           'parent_sha256':'digest','h_previous':{comp._digest(r['source']):{'correct':False} for r in rows}}
    ladder._atomic_torch(out/'working.pt',{'schema':joint.SCHEMA,'state':state,
                         'lora':{'w':torch.tensor(5.)},'optimizer':{'untouched':True}})
    original_working=(out/'working.pt').read_bytes()
    monkeypatch.setattr(hdb,'_checkpoint',lambda args,policy:({'state':{}},'digest'))
    class Zero:
        def restore_lora(self,model,lora):
            assert float(lora['w'])==5
    class Model:
        def eval(self):pass
    class Tokenizer:
        def __call__(self,s,**kw):return {'input_ids':list(range(1,1+len(s)))}
    monkeypatch.setattr(hdb,'_model',lambda args,parent:(Model(),Tokenizer(),Zero(),[]))
    from tools import nanojev_qwen_intel_dml as dml
    fake_device=SimpleNamespace(type='privateuseone')
    monkeypatch.setattr(dml,'intel_device',lambda idx:(fake_device,'Intel(R) Iris(R) Xe Graphics'))
    monkeypatch.setattr(dml,'prepare_model_for_intel',lambda bb,device:bb)
    seen=[]
    def score(bb,tok,selected,args,policy):
        seen.append((len(selected),args.device,args.eval_batch,all(x in rows for x in selected)))
        return {comp._digest(r['source']):{'correct':True,'emitted':r['target'],
               'finish_reason':'newline','generation_truncated':False,
               'premature_eos':False,'exact_prefix':False} for r in selected}
    monkeypatch.setattr(hdb,'_score_hdb',score)
    args=SimpleNamespace(output='unused',device='dml',rescore_live=True,rescore_output=None,
                         dml_index=1,intel_smoke_per_tier=1,eval_batch=8)
    result=joint._rescore_hdb_live(args)
    report=json.loads(Path(result['report']).read_text())
    assert seen==[(9,fake_device,1,True)]
    assert report['smoke_only'] is True
    assert report['complete_frozen_deck'] is False
    assert report['evaluated_cases']==9 and report['full_eval_deck_cases']==18
    assert report['adapter']=='Intel(R) Iris(R) Xe Graphics'
    assert report['cuda_used'] is False
    assert (out/'hdb_eval_deck.json').read_bytes()==before_deck
    assert (out/'working.pt').read_bytes()==original_working
    assert not list(Path(result['report']).parent.glob('checkpoint_snapshot.pt'))
    assert not (out/'hdb_rescore_report.json').exists()
