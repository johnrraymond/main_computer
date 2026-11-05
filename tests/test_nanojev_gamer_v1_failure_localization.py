"""Targeted loss-of-sign and inverse-gain diagnostics, without HF/GPU or source mutation."""
from pathlib import Path
from types import SimpleNamespace
import csv
import json

import numpy as np
import pytest

from tools import nanojev_gamer_v1_failure_localization as loc
from tools import nanojev_gamer_v1_diagnostic_train as core
from tools import nanojev_gamer_v1_staircase as stair


def _rows():
    vals=[('captain_crew_risk',0.),('captain_crew_risk',25.),
          ('captain_crew_risk',31.),('bertrand_oligopoly',-0.5),
          ('bertrand_oligopoly',.5),('bertrand_oligopoly',-22.),
          ('other',0.),('other',4.),('other',-4.)]
    return [{'game_id':g,'answer':v,'split':'train',
             'table_families':['calculation','state_transition'],
             'prompt_08':f'{g} #{i}'} for i,(g,v) in enumerate(vals)]


def _hp():
    return {'head_batch':3,'eval_batch':9,'head_lr':.0002,'weight_decay':.0001,
            'aux_weight':.25,'huber_beta':.1,'grad_clip':1.}


def _source(tmp_path,torch):
    rows=_rows();x=np.arange(36,dtype=np.float32).reshape(9,4)/50.
    torch.manual_seed(47);net=core._arch(torch,4,'wide32')
    optimizer=torch.optim.AdamW(net.parameters(),lr=_hp()['head_lr'],weight_decay=.0001)
    loss=core._loss(torch,net,torch.from_numpy(x),
                    torch.from_numpy(core._truth(rows).astype('float32')),'signed_log_huber',_hp())
    loss.backward();optimizer.step()
    frozen={k:v.detach().clone() for k,v in net.state_dict().items()}
    return {'head_hp':_hp(), 'head_state':{'model':frozen,
             'optimizer':optimizer.state_dict(),'step':2000,'adapter_sha256':'adapter'},
            'head_sha256':'head','lora_state':{'step':1800},
            'manifest':{'dataset_sha256':'dataset'},'lora_sha256':'lora',
            'selected':{'train':{'rows':rows,'features':x},
                        'dev':{'rows':[dict(row,split='dev') for row in rows],
                               'features':x.copy()}}}


def test_trace_matches_real_forward_both_heads_and_inverse():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    torch.manual_seed(3);net=core._arch(torch,4,'wide32')
    x=np.arange(32,dtype=np.float32).reshape(8,4)/50
    t=loc._trace(torch,net,x,'cpu',batch=3)
    with torch.inference_mode():
        main,aux=net(torch.from_numpy(x))
    assert np.allclose(t['corrected_logit'],main.numpy(),rtol=1e-6)
    assert np.allclose(t['auxiliary_logit'],aux.numpy(),rtol=1e-6)
    assert np.allclose(t['corrected_raw'],loc._inverse(main.numpy()))
    assert t['normalized'].shape==(8,4)
    assert t['encoder_gelu_1'].shape==(8,1024)
    assert t['encoder_gelu_3'].shape==(8,128)
    assert t['bottleneck_gelu'].shape==(8,32)
    assert len(loc.TRACE_KEYS)==len(t)
    # Inverse transform is monotone/sign preserving even at high magnitudes.
    a=np.array([-10.,-0.2,0.,.2,10.]);b=loc._inverse(a)
    assert (np.sign(a)==np.sign(b)).all()
    assert abs(b[-1])>abs(a[-1])*100


def test_corrected_readout_causal_restore_and_sufficiency_identifies_sign_collapse():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    rows=_rows();x=np.arange(36,dtype=np.float32).reshape(9,4)/50
    old=core._arch(torch,4,'wide32');new=core._arch(torch,4,'wide32')
    with torch.no_grad():
        old.corrected.weight.zero_();old.corrected.bias.fill_(-.3)
    new.load_state_dict(old.state_dict())
    with torch.no_grad():new.corrected.bias.fill_(+.3)
    a=loc._snapshot(old);b=loc._snapshot(new)
    before=loc._trace(torch,old,x,'cpu',9);after=loc._trace(torch,new,x,'cpu',9)
    assert all(after['corrected_raw']>0)
    assert all(before['corrected_raw']<0)
    results=loc._counterfactuals(torch,new,old,a,b,x,rows,1.,
               before['corrected_raw'],after['corrected_raw'],SimpleNamespace(device='cpu',eval_batch=9))
    restored=next(c for c in results if c['stage']=='corrected_readout' and
                  c['intervention']=='restore_old_in_updated')
    applied=next(c for c in results if c['stage']=='corrected_readout' and
                  c['intervention']=='apply_new_in_previous')
    assert restored['sign_losses_restored']>=2
    assert applied['sign_losses_reproduced']>=2
    evidence=loc._analyze_layer_causality(results,{'sign_lost':3,'newly_damaged':0})
    assert evidence['candidate']=='corrected_bias'
    assert restored['all_raw_mae']==pytest.approx(core._metrics(rows,before['corrected_raw'])['raw_mae'])
    assert applied['all_raw_mae']==pytest.approx(core._metrics(rows,after['corrected_raw'])['raw_mae'])
    assert all(torch.equal(v,b[k]) for k,v in new.state_dict().items())
    assert all(torch.equal(v,a[k]) for k,v in old.state_dict().items())
    other=next(c for c in results if c['stage']=='encoder_linear_1' and
               c['intervention']=='restore_old_in_updated')
    assert other['sign_losses_restored']==0


def test_per_step_loss_and_gradient_components_are_finite():
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    net=core._arch(torch,4,'wide32')
    x=torch.from_numpy(np.arange(36,dtype=np.float32).reshape(9,4)/50.)
    y=torch.tensor(core._truth(_rows()),dtype=torch.float32)
    loss,parts=loc._gradient_partition(torch,net,x,y,_hp())
    assert float(loss.detach().item())>0
    assert set(parts['by_group'])==set(loc.STAGE_PARAMS)
    assert parts['by_group']['corrected_readout']['corrected_gradient_l2']>0
    assert parts['by_group']['corrected_readout']['auxiliary_gradient_l2']==0
    assert parts['by_group']['auxiliary_readout']['corrected_gradient_l2']==0
    assert parts['by_group']['auxiliary_readout']['auxiliary_gradient_l2']>0
    assert all(v['gradient_cosine'] is None or -1.0001<=v['gradient_cosine']<=1.0001
               for v in parts['by_group'].values())


def test_run_trial_streams_every_case_and_layer_and_preserves_initial_source(tmp_path):
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    source=_source(tmp_path,torch)
    source_before={k:v.clone() for k,v in source['head_state']['model'].items()}
    rows=source['selected']['train']['rows'];dev=source['selected']['dev']['rows']
    x=source['selected']['train']['features']
    log=core.EvidenceLog(tmp_path/'evidence')
    files=loc._LocalizationFiles(log,'cpu',9)
    try:
        result=loc._isolated_trial(log,files,source,rows,dev,x,x,
                   game='captain_crew_risk',seed=1729,start='fresh',steps=2,
                   radius=1.,hp=_hp(),device='cpu')
    finally:
        files.close();log.close()
    assert result['steps']==2 and len(result['step_summaries'])==2
    assert len(list(csv.DictReader((log.dir/'localization_cases.csv').open())))==2*(len(dev)+_hp()['head_batch'])
    assert len(list(csv.DictReader((log.dir/'counterfactuals.csv').open())))==2*len(loc.STAGE_PARAMS)*2
    assert (log.dir/'counterfactual_cases.csv').is_file()
    with (log.dir/'layer_traces.jsonl').open() as stream:
        traces=[json.loads(v) for v in stream]
    assert len(traces)==2
    assert all('stage_traces' in line and 'corrected_vs_auxiliary_gradients' in line for line in traces)
    assert all('inverse_clip_cases' in line['case_changes'] for line in traces)
    assert all('rescue_ranked' in line for line in traces)
    assert (log.dir/'checkpoints'/'captain_crew_risk_fresh_1729'/'step_000002.pt').is_file()
    assert all(torch.equal(v,source_before[k]) for k,v in source['head_state']['model'].items())


def test_complete_orchestration_both_initializations_and_cli_flags(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    run=tmp_path/'source';run.mkdir();(run/'marker').write_text('untouched')
    source=_source(tmp_path,torch)
    monkeypatch.setattr(core,'_source_inputs',lambda *a,**kw:source)
    report=loc.run_localization(run,device='cpu',games=('bertrand_oligopoly',),
               starts=('fresh','checkpoint'),steps=1,seeds=1)
    assert report['status']=='COMPLETED_NOT_PROMOTED'
    assert len(report['trials'])==2
    assert (Path(report['output'])/'final_report.json').is_file()
    assert not (run/'final_report.json').exists()
    assert (run/'marker').read_text()=='untouched'
    args=stair.parser().parse_args(['diagnostic-localize','--run','x',
                                    '--localize-start','fresh','--localize-steps','2'])
    assert args.phase=='diagnostic-localize' and args.localize_start=='fresh'
    with pytest.raises(ValueError,match='LOCALIZE_FLAGS_ONLY'):
        stair.main(['status','--run','x','--localize-steps','2'])
    with pytest.raises(ValueError,match='LOCALIZE_USES'):
        stair.main(['diagnostic-localize','--run','x','--steps','2'])
    with pytest.raises(ValueError,match='DIAGNOSTIC_FLAGS_ONLY'):
        stair.main(['diagnostic-localize','--run','x','--diag-lora-steps','0'])


def test_refuse_missing_checkpoint_optimizer_and_nonexistent_game(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    source=_source(tmp_path,torch);source['head_state']['optimizer']={'state':{}}
    rows=source['selected']['train']['rows'];x=source['selected']['train']['features']
    log=core.EvidenceLog(tmp_path/'evidence')
    files=loc._LocalizationFiles(log,'cpu',9)
    try:
        with pytest.raises(ValueError,match='OPTIMIZER_STATE_REQUIRED'):
            loc._isolated_trial(log,files,source,rows,rows,x,x,game='captain_crew_risk',
                       seed=1729,start='checkpoint',steps=1,radius=1.,hp=_hp(),device='cpu')
    finally:files.close();log.close()
    run=tmp_path/'source';run.mkdir()
    monkeypatch.setattr(core,'_source_inputs',lambda *a,**kw:source)
    with pytest.raises(ValueError,match='GAME_HAS_NO_TRAINING_CASES'):
        loc.run_localization(run,device='cpu',games=('nonexistent',),steps=1,seeds=1)
    assert not list(tmp_path.glob('source-diagnostic-localize*'))


def test_real_staircase_lineage_full_localization_end_to_end(tmp_path,monkeypatch):
    """No monkeypatch of the localization source loader: exercise real hashes."""
    torch=pytest.importorskip('torch');torch.set_num_threads(1)
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools.nanojev_gamer_v1_pairs import write_dataset
    run=tmp_path/'certified';run.mkdir()
    data=run/'certified_pairs.jsonl'
    write_dataset(data,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=gamer.ensure_manifest(run,data,'Qwen/Qwen3-0.6B',False,include_transition_games=True)
    best=run/'lora'/'best_adapter';best.mkdir(parents=True)
    (best/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([.5,1.])},str(best/'adapter_model.safetensors'))
    hp={'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,'max_new_tokens':512,
        'lora_rank':16,'lora_batch':1,'lora_accum':8,'lora_lr':.0001,'lora_eval_every':200}
    torch.save({'schema':gamer.SCHEMA,'phase':'lora','step':1800,'pending_eval':False,
        'dataset_sha256':manifest['dataset_sha256'],
        'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
        'hyperparameters':hp,'adapter':{'foo.weight':torch.tensor([3.,4.])},
        'best_step':1000},run/'lora'/'latest.pt')
    class FakeModel:
        def eval(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(torch,object(),FakeModel()))
    monkeypatch.setattr(gamer,'capture_original_features',
        lambda _torch,_model,_tok,rows,_device,*,batch_size,max_length:
            np.array([[float(len(r['prompt_08'])%11)/11.,.1,.2,.3,.4,.5,.6,.7]
                      for r in rows],dtype=np.float32))
    stair.head(run,steps=1,device='cpu',head_eval_every=1)
    before_bytes=(run/'staircase'/'head_cycle_0001'/'to_numerical'/'latest.pt').read_bytes()
    report=loc.run_localization(run,device='cpu',games=('captain_crew_risk',),
                      starts=('checkpoint',),steps=1,seeds=1)
    assert report['status']=='COMPLETED_NOT_PROMOTED'
    assert report['trials'][0]['start']=='checkpoint'
    assert report['source']['certified_dev_cases']==146
    assert (run/'staircase'/'head_cycle_0001'/'to_numerical'/'latest.pt').read_bytes()==before_bytes
    assert Path(report['output']).parent==run.parent
