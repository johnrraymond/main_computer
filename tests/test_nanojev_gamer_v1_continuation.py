"""v11: exact weight transfer 200->201 and completed-run extension without loss."""
import json
from pathlib import Path
import pytest
import torch

from tools import nanojev_gamer_v1_handoff as gamer
from tests.test_nanojev_gamer_v1_transfer import Adapter, fake_peft, deck, args_for


def finished_source(tmp_path, *, source_step=200, best_step=100):
    source=tmp_path/'blind-002'
    adapter=source/'lora'/'best_adapter'
    Adapter({'generations':0,'gen_limits':[]},0.75).save_pretrained(adapter)
    digest=gamer.file_tree_sha256(adapter)
    manifest={'schema':gamer.SCHEMA,'dataset_sha256':'a'*64,
              'original_prompt_corpus_sha256':'z'*64,
              'model':'fake-qwen','provenance':'source_recomputed'}
    gamer.atomic_json(source/'handoff_manifest.json',manifest)
    gamer.atomic_json(source/'lora'/'report.json',{
       'model':'fake-qwen','dataset_sha256':manifest['dataset_sha256'],
       'best_step':best_step,'best_adapter_sha256':digest})
    torch.save({'schema':gamer.SCHEMA,'phase':'lora','step':source_step,
        'pending_eval':False,'dataset_sha256':'a'*64,
        'original_prompt_corpus_sha256':'z'*64,
        'hyperparameters':{'model':'fake-qwen','lora_rank':16},
        'adapter':{'value':torch.tensor([0.93])},
        'best_step':best_step,'best_score':1.0,'best_eval':{'exact':16}},
        source/'lora'/'latest.pt')
    return source


def named_deck():
    cases,manifest=deck()
    assert not any(row.get('blind_matrix') for row in cases)
    return cases,manifest


def test_latest_transfer_is_exact_and_starts_at_201(tmp_path,monkeypatch):
    source=finished_source(tmp_path)
    tracker={'generations':0,'gen_limits':[]}
    fake_peft(monkeypatch,tracker)
    target=tmp_path/'games-003'
    args=args_for(target,source,steps=202)
    args.init_lora_checkpoint='latest'
    cases,manifest=named_deck()
    identity=gamer.parent_lora_identity(args,torch)
    assert identity['checkpoint_kind']=='latest'
    assert identity['source_checkpoint_step']==200
    assert identity['source_best_step']==100
    assert len(identity['checkpoint_sha256'])==64
    original=gamer.save_training_state
    def stop_after_source_import(torch_mod,path,data):
        original(torch_mod,path,data)
        if data['phase']=='lora' and data['step']==200:
            assert float(data['adapter']['value'][0])==pytest.approx(.93)
            assert data['bootstrap']['source_checkpoint_step']==200
            assert tracker['generations']==0
            raise RuntimeError('CHECKPOINT_200_IMPORTED')
    monkeypatch.setattr(gamer,'save_training_state',stop_after_source_import)
    with pytest.raises(RuntimeError,match='CHECKPOINT_200_IMPORTED'):
        gamer.train_lora(args,cases,manifest)
    state=torch.load(target/'lora'/'latest.pt',weights_only=True)
    assert state['step']==200
    assert float(state['adapter']['value'][0])==pytest.approx(.93)
    assert tracker['loaded_value']==pytest.approx(.75)  # Best adapter is only the loader shell.
    monkeypatch.setattr(gamer,'save_training_state',original)
    args.resume=True
    report=gamer.train_lora(args,cases,manifest)
    state=torch.load(target/'lora'/'latest.pt',weights_only=True)
    assert state['step']==202
    assert state['sampling_counts']['named_replay']==4
    assert 'blind_matrix' not in state['sampling_counts']
    assert report['bootstrap']['checkpoint_kind']=='latest'
    assert report['bootstrap']['source_checkpoint_step']==200
    assert report['best_step']==202
    assert float(torch.load(source/'lora'/'latest.pt',weights_only=True)['adapter']['value'][0])==pytest.approx(.93)


def test_finished_run_can_extend_exact_optimizer_and_archive_report(tmp_path,monkeypatch):
    source=finished_source(tmp_path)
    tracker={'generations':0,'gen_limits':[]}
    fake_peft(monkeypatch,tracker)
    dest=tmp_path/'games-003'
    args=args_for(dest,source,steps=202)
    args.init_lora_checkpoint='latest'
    cases,manifest=named_deck()
    report=gamer.train_lora(args,cases,manifest)
    assert torch.load(dest/'lora'/'latest.pt',weights_only=True)['step']==202
    assert (dest/'lora'/'report.json').is_file()
    args.resume=True
    with pytest.raises(ValueError,match='LARGER_LORA_STEPS'):
        gamer.train_lora(args,cases,manifest)
    assert (dest/'lora'/'report.json').is_file()  # refusal preserves finished report
    args.lora_steps=204
    extended=gamer.train_lora(args,cases,manifest)
    assert extended['best_step'] in (202,204)
    assert torch.load(dest/'lora'/'latest.pt',weights_only=True)['step']==204
    assert (dest/'lora'/'completed_reports'/'report_step_0000202.json').exists()
    assert (dest/'lora'/'report.json').is_file()
    assert extended['history'][0]['step']==202 and extended['history'][-1]['step']==204
    assert extended['sampling_counts']['named_replay']==8
    assert tracker['generations']==4


def test_latest_parent_lineage_refuses_corruption_and_short_total(tmp_path,monkeypatch):
    source=finished_source(tmp_path)
    args=args_for(tmp_path/'games-003',source,steps=199)
    args.init_lora_checkpoint='latest'
    tracker={'generations':0,'gen_limits':[]}
    fake_peft(monkeypatch,tracker)
    cases,manifest=named_deck()
    with pytest.raises(ValueError,match='MUST_EXCEED_PARENT'):
        gamer.train_lora(args,cases,manifest)
    args.lora_steps=300
    state=torch.load(source/'lora'/'latest.pt',weights_only=True)
    state['dataset_sha256']='wrong'
    torch.save(state,source/'lora'/'latest.pt')
    with pytest.raises(ValueError,match='LATEST_CHECKPOINT_LINEAGE_INVALID'):
        gamer.parent_lora_identity(args,torch)


def test_resume_with_changed_step_target_is_same_optimizer_lineage(tmp_path):
    args=args_for(tmp_path/'new',tmp_path/'source',steps=200)
    args.init_lora_checkpoint='latest'
    old=gamer.frozen_hyperparameters(args,'lora')
    args.lora_steps=9000
    assert old==gamer.frozen_hyperparameters(args,'lora')
    assert old['init_lora_checkpoint']=='latest'


def test_new_cli_supports_named_continuation_beyond_200(tmp_path):
    args=gamer.parser().parse_args(['lora','--output',str(tmp_path/'games-003'),
        '--init-lora-from',str(tmp_path/'blind-002'),'--init-lora-checkpoint','latest',
        '--lora-steps','1000'])
    gamer.validate_flags(args)
    assert args.lora_steps==1000 and args.init_lora_checkpoint=='latest'
    with pytest.raises(ValueError,match='INIT_LATEST_REQUIRES'):
        gamer.validate_flags(gamer.parser().parse_args([
          'lora','--output',str(tmp_path/'new'),'--init-lora-checkpoint','latest']))


def test_cannot_extend_after_frozen_wide_handoff(tmp_path,monkeypatch):
    source=finished_source(tmp_path)
    fake_peft(monkeypatch,{'generations':0,'gen_limits':[]})
    target=tmp_path/'games-003'
    args=args_for(target,source,steps=202)
    args.init_lora_checkpoint='latest'
    cases,manifest=named_deck()
    gamer.train_lora(args,cases,manifest)
    args.resume=True;args.lora_steps=204
    (target/'handoff.json').write_text('{}')
    with pytest.raises(ValueError,match='FROZEN_HANDOFF_EXISTS'):
        gamer.train_lora(args,cases,manifest)
    assert (target/'lora'/'report.json').is_file()
    assert not (target/'lora'/'completed_reports').exists()
