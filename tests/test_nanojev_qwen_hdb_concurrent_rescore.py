"""Concurrency contracts for a read-only HDB rescore while the trainer runs."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import pytest

from tools import nanojev_qwen_joint_frontier as joint
from tools import nanojev_qwen_lm_radius_ladder as ladder


def _fixture(tmp_path, monkeypatch):
    out=tmp_path/'live-training'
    out.mkdir()
    monkeypatch.setattr(joint,'_out',lambda args:out)
    ladder._atomic_json(out/'hdb_eval_deck.json',[{'immutable':True}])
    ladder._atomic_json(out/'progress.json',{'step':100})
    ladder._atomic_torch(out/'working.pt',{'step':100,'complete':True})
    (out/'joint_frontier_report.json').write_text('ORIGINAL')
    return out,SimpleNamespace(output=str(tmp_path/'base'),rescore_live=True,rescore_output=None)


def test_live_rescore_uses_pinned_checkpoint_and_separate_reports(tmp_path,monkeypatch):
    out,args=_fixture(tmp_path,monkeypatch)
    original_deck=(out/'hdb_eval_deck.json').read_bytes()
    def verify(a,*,save=True,snapshot_path=None,report_dir=None):
        assert save
        snapshot=Path(snapshot_path)
        reports=Path(report_dir)
        assert snapshot.exists() and snapshot.parent==reports
        assert snapshot.stat().st_ino==(out/'working.pt').stat().st_ino
        import torch
        assert torch.load(snapshot,weights_only=True)['step']==100
        # Simulate a concurrent trainer committing the next checkpoint. Its
        # atomic replacement must not alter the pinned diagnostic snapshot.
        ladder._atomic_torch(out/'working.pt',{'step':200,'complete':True})
        assert torch.load(snapshot,weights_only=True)['step']==100
        assert torch.load(out/'working.pt',weights_only=True)['step']==200
        ladder._atomic_json(reports/'hdb_rescore_report.json',{'checkpoint_step':100})
        (reports/'hdb_rescore_failures.jsonl').write_text('')
        return {'checkpoint_step':100,'optimizer_steps_performed':0,
                'report':str(reports/'hdb_rescore_report.json')}
    monkeypatch.setattr(joint,'_rescore_hdb_checkpoint',verify)
    result=joint._rescore_hdb_live(args)
    assert result['checkpoint_step']==100
    report=Path(result['report'])
    assert report.exists() and report.parent!=out
    assert (report.parent/'checkpoint_snapshot.pt').exists() is False
    assert json.loads(report.read_text())['checkpoint_step']==100
    assert (out/'progress.json').read_text().find('100')>=0
    assert (out/'joint_frontier_report.json').read_text()=='ORIGINAL'
    assert (out/'hdb_eval_deck.json').read_bytes()==original_deck
    assert not (out/'hdb_rescore_report.json').exists()


def test_live_rescore_refuses_unsafe_checkpoint_copy(tmp_path,monkeypatch):
    out,args=_fixture(tmp_path,monkeypatch)
    def no_link(source,dest):
        raise OSError('unsupported volume')
    monkeypatch.setattr(joint.os,'link',no_link)
    with pytest.raises(RuntimeError,match='HDB_LIVE_RESCORE_REQUIRES_HARDLINK'):
        joint._rescore_hdb_live(args)
    assert (out/'working.pt').exists()
    assert not list((out/'concurrent_hdb_rescores').rglob('checkpoint_snapshot.pt'))


def test_live_rescore_rejects_training_root_as_report_dir(tmp_path,monkeypatch):
    out,args=_fixture(tmp_path,monkeypatch)
    args.rescore_output=str(out)
    with pytest.raises(ValueError,match='HDB_LIVE_RESCORE_OUTPUT_MUST_BE_SEPARATE'):
        joint._rescore_hdb_live(args)
    assert json.loads((out/'progress.json').read_text())['step']==100


def test_offline_rescore_keeps_prior_semantics(monkeypatch):
    seen=[]
    monkeypatch.setattr(joint,'_rescore_hdb_checkpoint',lambda args,**kw:seen.append(kw) or {'ok':True})
    monkeypatch.setattr(joint.frontier,'_emit',lambda *a,**kw:None)
    assert joint.rescore_hdb(SimpleNamespace(rescore_live=False))=={'ok':True}
    assert seen==[{}]


def test_live_rescore_cli_dispatch_with_no_trainer_mutation(monkeypatch,tmp_path):
    from tools import nanojev_qwen_lm_radius_ladder as runner
    calls=[]
    monkeypatch.setattr(joint,'rescore_hdb',lambda args:calls.append((args.rescore_live,args.device)) or {'ok':True})
    res=runner.main(['--stage','hdb-rescore','--frontier','--frontier-hdb',
                    '--rescore-live','--device','cpu','--output',str(tmp_path/'run')])
    assert res=={'ok':True}
    assert calls==[(True,'cpu')]
    with pytest.raises(ValueError,match='HDB_LIVE_RESCORE_REQUIRES_HDB_RESCORE_STAGE'):
        runner.main(['--stage','run','--frontier','--frontier-hdb','--rescore-live'])


def test_live_rescore_actual_report_tracks_pinned_step(tmp_path,monkeypatch):
    import torch
    from tools import nanojev_qwen_hdb_balance as hdb
    from tools import nanojev_qwen_competency_diagnostic as competency
    out,args=_fixture(tmp_path,monkeypatch)
    rows=[hdb._row(i,0,(9,1,3),label='heldout') for i in range(9)]
    ladder._atomic_json(out/'hdb_eval_deck.json',rows)
    state={'step':100,'last_checked_step':100,'policy':{'hdb':{'max_tokens':1024,
           'max_new_tokens':48,'max_offset':64},'max_seq':768},
           'parent_sha256':'digest','h_previous':{competency._digest(r['source']):{'correct':False} for r in rows}}
    ladder._atomic_torch(out/'working.pt',{'schema':joint.SCHEMA,'state':state,
                         'lora':{'w':torch.tensor(5.)},'optimizer':{'untouched':True}})
    monkeypatch.setattr(hdb,'_checkpoint',lambda args,policy:({'state':{}},'digest'))
    class Zero:
        def restore_lora(self,model,lora):
            assert float(lora['w'])==5
    class Model:
        def eval(self): pass
    monkeypatch.setattr(hdb,'_model',lambda args,parent:(Model(),
                        __import__('test_nanojev_qwen_hdb_length_safe_rescore').CharacterTokenizer(),Zero(),[]))
    def stub(model,tok,rows,args,policy):
        # Advance the live trainer midway through diagnostic work.
        ladder._atomic_torch(out/'working.pt',{'schema':joint.SCHEMA,
                            'state':{'step':200},'lora':{'w':torch.tensor(9.)}})
        return {competency._digest(r['source']):{'correct':True,'emitted':r['target'],
                'finish_reason':'newline','generation_truncated':False,
                'premature_eos':False,'exact_prefix':False} for r in rows}
    monkeypatch.setattr(hdb,'_score_hdb',stub)
    args.device='cpu'
    args.eval_batch=4
    result=joint._rescore_hdb_live(args)
    assert result['checkpoint_step']==100
    report=json.loads(Path(result['report']).read_text())
    assert report['checkpoint_step']==100
    assert report['checkpoint_source']=='immutable_hardlink_snapshot'
    assert report['optimizer_steps_performed']==0
    assert report['model_weights_changed'] is False
    assert not (out/'hdb_rescore_report.json').exists()
    assert torch.load(out/'working.pt',weights_only=True)['state']['step']==200
