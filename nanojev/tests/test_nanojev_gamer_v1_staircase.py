"""Reusable alternating LoRA / C0 Wide staircase tests (no HF download)."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from safetensors.torch import save_file,load_file

from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_staircase as stairs
from tools.nanojev_gamer_v1_pairs import write_dataset


@pytest.fixture
def source(tmp_path):
    run=tmp_path/'transition-005'
    run.mkdir()
    dataset=run/'certified_pairs.jsonl'
    write_dataset(dataset,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=gamer.ensure_manifest(run,dataset,'Qwen/Qwen3-0.6B',False,
                                     include_transition_games=True)
    folder=run/'lora'/'best_adapter'
    folder.mkdir(parents=True)
    (folder/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([0.5,1.0])},str(folder/'adapter_model.safetensors'))
    hp={'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,
        'max_new_tokens':2048,'lora_rank':16,'lora_batch':1,
        'lora_accum':8,'lora_lr':0.0001,'lora_eval_every':200,
        'init_lora_from':str((tmp_path/'catalog-004').resolve()),
        'init_lora_checkpoint':'latest','lora_probe_cases':17}
    state={'schema':gamer.SCHEMA,'phase':'lora','step':795,'pending_eval':False,
           'dataset_sha256':manifest['dataset_sha256'],
           'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
           'hyperparameters':hp,'adapter':{'foo.weight':torch.tensor([3.,4.])},
           'best_step':700}
    torch.save(state,run/'lora'/'latest.pt')
    return run,manifest


def _checkpoint(source,step,weights=None,pending=False):
    state=torch.load(source/'lora'/'latest.pt',weights_only=True)
    state['step']=step
    state['pending_eval']=pending
    if weights is not None:
        state['adapter']={'foo.weight':torch.tensor(weights,dtype=torch.float32)}
    torch.save(state,source/'lora'/'latest.pt')


def test_exact_latest_snapshot_not_older_best_and_source_untouched(source):
    run,manifest=source
    destination=run/'staircase'/'head_cycle_0001'
    old_bytes=(run/'lora'/'latest.pt').read_bytes()
    old_tree=gamer.file_tree_sha256(run/'lora'/'best_adapter')
    report=stairs.snapshot_latest(run,destination,expect_step=795)
    latest_weights=load_file(str(destination/'lora'/'best_adapter'/'adapter_model.safetensors'))
    assert torch.equal(latest_weights['foo.weight'],torch.tensor([3.,4.]))
    assert report['snapshot_source']['checkpoint_step']==795
    assert report['best_step']==795
    assert report['retention_certification']=='NOT_AUDITED'
    assert (run/'lora'/'latest.pt').read_bytes()==old_bytes
    assert gamer.file_tree_sha256(run/'lora'/'best_adapter')==old_tree
    assert not (run/'handoff.json').exists()
    assert not (run/'to_numerical').exists()
    _,observed=gamer.ensure_manifest(destination,destination/'certified_pairs.jsonl',
                                      'Qwen/Qwen3-0.6B',False,include_transition_games=True)
    assert observed==manifest
    args=SimpleNamespace(output=str(destination),model='Qwen/Qwen3-0.6B',allow_experimental=True)
    folder,loaded=gamer.load_frozen_adapter(args,manifest)
    assert folder==destination/'lora'/'best_adapter'
    assert gamer.switch_to_numerical(args,manifest)['adapter_sha256']==loaded['best_adapter_sha256']


def test_staircase_snapshot_allows_pending_evaluation_without_changing_source(source):
    run,_=source
    target=run/'staircase'/'head_cycle_0001'
    _checkpoint(run,1600,pending=True,weights=[8.,9.])
    original=(run/'lora'/'latest.pt').read_bytes()
    # An interrupted evaluation may have already saved prompt-by-prompt work.
    progress=run/'lora'/'eval_step_0001600.json'
    progress.write_text(json.dumps({'matches':[True,False], 'schema':'test-eval-progress'}))
    original_progress=progress.read_bytes()
    report=stairs.snapshot_latest(run,target,expect_step=1600)
    frozen=load_file(str(target/'lora'/'best_adapter'/'adapter_model.safetensors'))
    assert torch.equal(frozen['foo.weight'],torch.tensor([8.,9.]))
    assert report['best_step']==1600
    assert report['snapshot_source']['source_pending_eval'] is True
    assert report['evaluation_scope']=='EXACT_LATEST_PENDING_EVALUATION'
    assert (run/'lora'/'latest.pt').read_bytes()==original
    assert progress.read_bytes()==original_progress
    assert not (run/'handoff.json').exists()
    assert not (run/'lora'/'report.json').exists()


def test_staircase_snapshot_refuses_corrupt_keys_even_during_pending_eval(source):
    run,_=source
    target=run/'staircase'/'head_cycle_0001'
    _checkpoint(run,1600,pending=True)
    state=torch.load(run/'lora'/'latest.pt',weights_only=True)
    state['adapter']={'unexpected':torch.tensor([1.])}
    torch.save(state,run/'lora'/'latest.pt')
    with pytest.raises(ValueError,match='TENSOR_KEYS'):
        stairs.snapshot_latest(run,target)
    assert not target.exists()


def test_pending_evaluation_can_be_resumed_at_same_lora_step(source,monkeypatch):
    run,_=source
    _checkpoint(run,1600,pending=True)
    dispatched=[]
    monkeypatch.setattr(stairs.gamer,'main',lambda cmd: dispatched.append(cmd))
    stairs.lora(run,1600,'cuda')
    assert dispatched[0][0]=='lora'
    assert dispatched[0][dispatched[0].index('--lora-steps')+1]=='1600'
    assert '--resume' in dispatched[0]
    stairs.lora(run,1800,'cuda')
    assert dispatched[1][dispatched[1].index('--lora-steps')+1]=='1800'
    _checkpoint(run,1600,pending=False)
    with pytest.raises(ValueError,match='TARGET_MUST_EXCEED'):
        stairs.lora(run,1600,'cuda')


def test_head_pending_snapshot_then_same_weights_completed_eval_reuses_cycle(source,monkeypatch):
    run,_=source
    _checkpoint(run,1600,pending=True,weights=[8.,9.])
    source_bytes=(run/'lora'/'latest.pt').read_bytes()
    class Frozen:
        def eval(self): return self
    monkeypatch.setattr(gamer,'model_components',lambda model,device,adapter_dir=None:
                        (torch,object(),Frozen()))
    captured=[]
    def feats(_torch,frozen,tok,rows,device,*,batch_size,max_length):
        captured.append(len(rows))
        return np.asarray([[(len(r['prompt_08'])%17)/17.,1.,.5,0.,-.5,0.,.1,.2]
                           for r in rows],dtype=np.float32)
    monkeypatch.setattr(gamer,'capture_original_features',feats)
    first=stairs.head(run,steps=1,device='cpu',head_eval_every=1)
    assert first['latest_step']==1
    assert (run/'lora'/'latest.pt').read_bytes()==source_bytes
    cycle=run/'staircase'/'head_cycle_0001'
    record=json.loads((cycle/'staircase_cycle.json').read_text())
    assert record['source_pending_eval'] is True
    assert record['source_step']==1600
    assert record['source_weights_sha256']
    head_before=torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)
    assert head_before['step']==1
    # The evaluation can finish without an optimizer update, changing the
    # checkpoint bytes and metadata but not its LoRA tensor fingerprint.
    _checkpoint(run,1600,pending=False)
    assert (run/'lora'/'latest.pt').read_bytes()!=source_bytes
    second=stairs.head(run,steps=2,device='cpu',head_eval_every=1)
    assert second['latest_step']==2
    assert len(list((run/'staircase').glob('head_cycle_*')))==1
    assert torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)['step']==2
    assert (cycle/'to_numerical'/'completed_reports'/'report_step_0000001.json').exists()



def test_lora_resume_command_rebuilds_original_hyperparameters(source,monkeypatch):
    run,_=source
    seen=[]
    monkeypatch.setattr(stairs.gamer,'main',lambda cmd: seen.append(cmd))
    stairs.lora(run,1250,'cuda')
    cmd=seen[0]
    assert cmd[0]=='lora'
    assert '--resume' in cmd
    assert cmd[cmd.index('--lora-steps')+1]=='1250'
    assert cmd[cmd.index('--lora-eval-every')+1]=='200'
    assert cmd[cmd.index('--max-new-tokens')+1]=='2048'
    assert cmd[cmd.index('--init-lora-checkpoint')+1]=='latest'
    assert '--include-transition-games' in cmd
    with pytest.raises(ValueError,match='TARGET_MUST_EXCEED'):
        stairs.lora(run,795,'cuda')


def test_head_can_stair_step_twice_without_restarting_weights(source,monkeypatch):
    run,_=source
    class Frozen:
        def eval(self): return self
    captured=[]
    def model_components(model,device,adapter_dir=None):
        adapter_file=Path(adapter_dir)/'adapter_model.safetensors'
        weight=float(load_file(str(adapter_file))['foo.weight'][0])
        captured.append(weight)
        return torch,object(),Frozen()
    def features(_torch,_frozen,_tok,rows,_device,*,batch_size,max_length):
        # Simulated frozen Qwen features depend on exact adapter checkpoint.
        val=captured[-1]
        return np.asarray([[(len(r['prompt_08'])%97)/97.,val/10.,1.,0.,.5,0.,-.5,0.]
                           for r in rows],dtype=np.float32)
    monkeypatch.setattr(gamer,'model_components',model_components)
    monkeypatch.setattr(gamer,'capture_original_features',features)
    first=stairs.head(run,steps=2,device='cpu',head_eval_every=1,capture_batch=16)
    one=run/'staircase'/'head_cycle_0001'
    first_state=torch.load(one/'to_numerical'/'latest.pt',weights_only=True)
    assert first['latest_step']==2
    assert first_state['step']==2
    assert captured[0]==3.
    assert (one/'to_numerical'/'report.json').exists()
    # A head-cycle extension with unchanged LoRA must reuse SAME optimizer.
    first_extended=stairs.head(run,steps=3,device='cpu',head_eval_every=1,capture_batch=16)
    assert first_extended['latest_step']==3
    assert (one/'to_numerical'/'completed_reports'/'report_step_0000002.json').exists()
    one_state=torch.load(one/'to_numerical'/'latest.pt',weights_only=True)
    # Simulate LoRA training having continued from 795 to 900.
    _checkpoint(run,900,weights=[7.,8.])
    second=stairs.head(run,steps=5,device='cpu',head_eval_every=1,capture_batch=16)
    two=run/'staircase'/'head_cycle_0002'
    second_state=torch.load(two/'to_numerical'/'latest.pt',weights_only=True)
    assert second['latest_step']==5
    assert second_state['step']==5
    assert second_state['bootstrap_head']['source_head_step']==3
    assert second_state['bootstrap_head']['optimizer_policy']=='FRESH_ON_CHANGED_FEATURE_DISTRIBUTION'
    assert second['head_transfer']['source_head_step']==3
    assert captured[-1]==7.
    assert torch.equal(torch.load(one/'to_numerical'/'latest.pt',weights_only=True)['model']['net.0.weight'],
                       one_state['model']['net.0.weight']) if 'net.0.weight' in one_state['model'] else True
    assert not (run/'handoff.json').exists()
    assert not (run/'to_numerical').exists()
    assert not (run/'lora'/'report.json').exists()
    assert gamer.file_tree_sha256(one/'lora'/'best_adapter') != gamer.file_tree_sha256(two/'lora'/'best_adapter')


def test_head_transfer_rejects_wrong_dataset_and_short_target(source,tmp_path):
    run,manifest=source
    former=run/'staircase'/'head_cycle_0001'
    stairs.snapshot_latest(run,former)
    state={'schema':gamer.SCHEMA,'phase':'to_numerical','step':30,
           'dataset_sha256':manifest['dataset_sha256'],
           'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
           'hyperparameters':{'model':'Qwen/Qwen3-0.6B'},'model':{'w':torch.zeros(2)},
           'adapter_sha256':'old'}
    (former/'to_numerical').mkdir(exist_ok=True)
    torch.save(state,former/'to_numerical'/'latest.pt')
    args=SimpleNamespace(init_head_from=str(former),output=str(run/'staircase'/'head_cycle_0002'),
                         model='Qwen/Qwen3-0.6B',head_steps=30)
    with pytest.raises(ValueError,match='TARGET_MUST_EXCEED'):
        gamer.head_transfer_identity(args,manifest,torch)
    args.head_steps=40
    wrong=dict(manifest,dataset_sha256='0'*64)
    with pytest.raises(ValueError,match='DATASET_OR_BACKBONE_CHANGED'):
        gamer.head_transfer_identity(args,wrong,torch)


def test_resume_after_head_interruption_and_diagnostic_history(source,monkeypatch):
    """An interruption between checkpoints resumes the same head optimizer; later LoRA changes create a new cycle."""
    from tools import nanojev_gamer_v1_diagnostic as diagnostic
    run,_=source
    class Frozen:
        def eval(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda model,device,adapter_dir=None:
                        (torch,object(),Frozen()))
    def feats(_torch,frozen,tok,rows,device,*,batch_size,max_length):
        return np.asarray([[(len(r['prompt_08'])%17)/17.,1.,.5,0.,-.5,0.,.1,.2]
                           for r in rows],dtype=np.float32)
    monkeypatch.setattr(gamer,'capture_original_features',feats)
    real_save=gamer.save_training_state
    raised={'value':False}
    def stop_after_first_checkpoint(torch_module,path,data):
        if (data.get('phase')=='to_numerical' and data.get('step')==2 and not raised['value']):
            raised['value']=True
            raise KeyboardInterrupt('simulated Ctrl+C before writing step 2')
        return real_save(torch_module,path,data)
    monkeypatch.setattr(gamer,'save_training_state',stop_after_first_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        stairs.head(run,steps=3,device='cpu',head_eval_every=1)
    first=run/'staircase'/'head_cycle_0001'
    saved=torch.load(first/'to_numerical'/'latest.pt',weights_only=True)
    assert saved['step']==1
    assert not (first/'to_numerical'/'report.json').exists()
    monkeypatch.setattr(gamer,'save_training_state',real_save)
    final=stairs.head(run,steps=3,device='cpu',head_eval_every=1)
    assert final['latest_step']==3
    assert len(list((run/'staircase').glob('head_cycle_*')))==1
    diagnosed=diagnostic.diagnose(run)
    assert diagnosed['staircase_cycles'][0]['source_lora_step']==795
    assert diagnosed['staircase_cycles'][0]['head_step']==3
    assert diagnosed['staircase_cycles'][0]['finished'] is True
    assert 'Numerical-head staircase' in diagnostic.format_report(diagnosed)


def test_retry_feature_capture_after_ctrl_c_before_checkpoint(source,monkeypatch):
    run,_=source
    class Frozen:
        def eval(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda model,device,adapter_dir=None:
                        (torch,object(),Frozen()))
    seen={'once':False}
    def feats(_torch,frozen,tok,rows,device,*,batch_size,max_length):
        if not seen['once']:
            seen['once']=True
            raise KeyboardInterrupt('simulated feature-capture interrupt')
        return np.asarray([[(len(r['prompt_08'])%17)/17.,1.,.5,0.,-.5,0.,.1,.2]
                           for r in rows],dtype=np.float32)
    monkeypatch.setattr(gamer,'capture_original_features',feats)
    with pytest.raises(KeyboardInterrupt):
        stairs.head(run,steps=1,device='cpu',head_eval_every=1)
    cycle=run/'staircase'/'head_cycle_0001'
    assert cycle.is_dir()
    assert not (cycle/'to_numerical'/'latest.pt').exists()
    result=stairs.head(run,steps=1,device='cpu',head_eval_every=1)
    assert result['latest_step']==1
    assert len(list((run/'staircase').glob('head_cycle_*')))==1


def test_cli_zero_line_flags_are_only_for_head(source):
    args=stairs.parser().parse_args(['head','--run',str(source[0]),'--steps','610',
                                    '--zero-line-focus','.75','--zero-line-radius','1'])
    assert args.zero_line_focus==.75 and args.zero_line_radius==1
    args=stairs.parser().parse_args(['status','--run',str(source[0]),'--zero-line-focus','.75'])
    with pytest.raises(ValueError,match='ONLY_HEAD'):
        stairs.main(['status','--run',str(source[0]),'--zero-line-focus','.75'])
    args=gamer.parser().parse_args(['to-numerical','--output',str(source[0]),
                                    '--zero-line-focus','.75','--zero-line-radius','1'])
    gamer.validate_flags(args)
    with pytest.raises(ValueError,match='ONLY_TO_NUMERICAL'):
        gamer.validate_flags(gamer.parser().parse_args([
            'lora','--output',str(source[0]),'--zero-line-focus','.75']))


def test_head_sampling_policy_switch_at_existing_step_and_implicit_resume(source,monkeypatch):
    """No LoRA changes, optimizer restart, new head cycle, or feature recapture."""
    run,_=source
    class Frozen:
        def eval(self): return self
    monkeypatch.setattr(gamer,'model_components',lambda model,device,adapter_dir=None:
                        (torch,object(),Frozen()))
    captured=[]
    def feats(_torch,_frozen,_tok,rows,_device,*,batch_size,max_length):
        captured.append(len(rows))
        return np.asarray([[(len(r['prompt_08'])%17)/17.,1.,.5,0.,-.5,0.,.1,.2]
                           for r in rows],dtype=np.float32)
    monkeypatch.setattr(gamer,'capture_original_features',feats)
    first=stairs.head(run,steps=2,device='cpu',head_eval_every=1)
    cycle=run/'staircase'/'head_cycle_0001'
    old=torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)
    assert old['step']==2 and old['sampling_policy']=={'focus':0.,'radius':1.}
    assert old['sampling_counts']['uniform']==2*32
    assert old['sampling_counts']['zero']==0
    assert len(captured)==3

    from tools.nanojev_gamer_v1_zero_line import ZeroLineSampler
    original_draw=ZeroLineSampler.draw
    def interrupt_before_first_focused_step(self,rng,batch_size):
        raise KeyboardInterrupt('simulated Ctrl+C after policy commit, before optimizer step')
    monkeypatch.setattr(ZeroLineSampler,'draw',interrupt_before_first_focused_step)
    with pytest.raises(KeyboardInterrupt,match='after policy commit'):
        stairs.head(run,steps=3,device='cpu',head_eval_every=1,
                    zero_line_focus=.75,zero_line_radius=1.)
    mid=torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)
    assert mid['step']==2
    assert mid['sampling_policy']=={'focus':.75,'radius':1.}
    assert mid['sampling_history'][-1]['start_step']==3
    monkeypatch.setattr(ZeroLineSampler,'draw',original_draw)

    # Recover after Ctrl+C without repeating flags: policy and optimizer persist.
    second=stairs.head(run,steps=3,device='cpu',head_eval_every=1)
    assert second['latest_step']==3
    state=torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)
    assert state['step']==3
    assert state['sampling_policy']=={'focus':.75,'radius':1.}
    assert state['sampling_history']==[{'start_step':1,'focus':0.,'radius':1.},
                                       {'start_step':3,'focus':.75,'radius':1.}]
    assert sum(state['sampling_counts'].values())==3*32
    assert sum(state['sampling_counts'][k] for k in ('zero','negative','positive'))>0
    assert sum(state['sampled_game_counts'].values())==3*32
    assert second['checks'][-1]['target_zero_line']['by_class']['zero']['cases']>0
    assert second['sampling_coverage']['train_games']==146
    assert second['sampling_coverage']['eligible_games_any']>0
    assert len(captured)==3
    assert len(list((run/'staircase').glob('head_cycle_*')))==1

    # Flags omitted on subsequent calls mean KEEP the committed active focus.
    third=stairs.head(run,steps=4,device='cpu',head_eval_every=1)
    state=torch.load(cycle/'to_numerical'/'latest.pt',weights_only=True)
    assert third['latest_step']==4
    assert state['sampling_policy']=={'focus':.75,'radius':1.}
    assert len(state['sampling_history'])==2
    assert sum(state['sampling_counts'].values())==4*32
    assert len(captured)==3
