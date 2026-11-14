"""Isolated diagnostic-training controls, deterministic evidence, and provenance."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_diagnostic_train as diag
from tools import nanojev_gamer_v1_staircase as stair


def _rows():
    data=[('alpha',0.,('calculation','strategic_payoff')),
          ('alpha',-.5,('calculation','strategic_payoff')),
          ('alpha',.5,('calculation','strategic_payoff')),
          ('beta',-5.,('calculation','state_transition')),
          ('beta',0.,('calculation','state_transition')),
          ('beta',5.,('calculation','state_transition'))]
    return [{'game_id':g,'answer':y,'split':'train','prompt_08':f'{g}:{i}',
             'table_families':list(f)} for i,(g,y,f) in enumerate(data)]


def test_sampling_matched_and_deterministic():
    rows=_rows()
    for name in ('mixed','zone_alternating','zone_blocked','signature_blocked','only:alpha'):
        a=diag._make_schedule(rows,9,4,1729,name,1.)
        b=diag._make_schedule(rows,9,4,1729,name,1.)
        assert a==b
        assert len(a)==9 and all(len(s)==4 for s in a)
        assert all(0<=index<len(rows) for s in a for index in s)
        if name=='only:alpha':assert all(rows[i]['game_id']=='alpha' for s in a for i in s)


def test_c0_bottleneck_and_true_bypass_shapes():
    torch=pytest.importorskip('torch')
    torch.set_num_threads(1)
    for name in ('wide32','wide128','bypass'):
        net=diag._arch(torch,4,name)
        x=torch.rand(3,4)
        a,b=net(x)
        assert a.shape==(3,) and b.shape==(3,)
        assert diag._activation_stats(torch,net,np.ones((3,4),np.float32),'cpu')['final_embedding']['width']==(
            32 if name=='wide32' else 128)


def test_loss_metrics_are_distinct_and_finite():
    torch=pytest.importorskip('torch')
    torch.set_num_threads(1)
    net=diag._arch(torch,4,'wide32')
    x=torch.tensor(np.arange(24,dtype=np.float32).reshape(6,4)/20)
    y=torch.tensor(diag._truth(_rows()),dtype=torch.float32)
    hp={'huber_beta':.1,'aux_weight':.25}
    for objective in ('signed_log_huber','signed_log_mse','raw_mae'):
        net.zero_grad()
        loss=diag._loss(torch,net,x,y,objective,hp)
        loss.backward()
        assert np.isfinite(loss.item())
        assert any(p.grad is not None for p in net.parameters())
    observed=diag._metrics(_rows(),[.2,-.4,.4,-2,.1,2])
    assert observed['cases']==6 and observed['zero_abs_mae']==pytest.approx(.15)
    assert observed['signed_log_mae']!=observed['raw_mae']


def test_full_training_logs_per_case_forgetting_and_does_not_mutate_source(tmp_path):
    torch=pytest.importorskip('torch')
    torch.set_num_threads(1)
    rows=_rows()
    dev=[dict(r,split='dev') for r in rows]
    x=np.arange(24,dtype=np.float32).reshape(6,4)/50
    log=diag.EvidenceLog(tmp_path/'evidence')
    hp={'head_batch':4,'eval_batch':6,'head_lr':.002,'weight_decay':0.,
        'aux_weight':.25,'huber_beta':.1,'grad_clip':1.}
    try:
        sched=diag._make_schedule(rows,3,4,1729,'mixed',1.)
        result=diag._run_training(log,name='smoke',rows_train=rows,rows_dev=dev,
            x_train=x,x_dev=x+.01,architecture='wide32',objective='signed_log_huber',
            schedule=sched,seed=1729,steps=3,eval_every=1,device='cpu',hp=hp,radius=1.)
        assert result['evaluations']==4
        assert result['final']['cases']==6 and result['final_train']['cases']==6
        assert (log.dir/'checkpoints'/'smoke_seed_1729'/'step_000003.pt').exists()
        assert not (log.dir/'checkpoints'/'smoke_seed_1729'/'step_000001.pt').exists()
    finally:log.close()
    entries=[json.loads(x) for x in (log.dir/'events.jsonl').read_text().splitlines()]
    assert len([x for x in entries if x['event']=='training_step'])==3
    assert len([x for x in entries if x['event']=='evaluation'])==4
    assert all(x['dev']['objective_loss']>=0 and x['train']['objective_loss']>=0
               for x in entries if x['event']=='evaluation')
    assert len((log.dir/'predictions.csv').read_text().splitlines())==1+4*6*2
    assert (log.dir/'forgetting.jsonl').is_file()


def test_output_rejects_active_run_and_existing_directory(tmp_path):
    run=tmp_path/'source';run.mkdir()
    with pytest.raises(ValueError,match='OUTSIDE_ACTIVE_RUN'):
        diag._output_path(run,run/'stuff')
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        diag._output_path(run,tmp_path)
    a=diag._output_path(run,None)
    a.mkdir()
    b=diag._output_path(run,None)
    assert a!=b


def test_parser_exposes_one_opt_in_phase_and_separates_flags():
    args=stair.parser().parse_args(['diagnostic-train','--run','foo','--diag-lora-steps','0'])
    assert args.phase=='diagnostic-train'
    assert args.diag_lora_steps==0
    with pytest.raises(ValueError,match='DIAGNOSTIC_FLAGS_ONLY'):
        stair.main(['status','--run','foo','--diag-lora-steps','0'])
    with pytest.raises(ValueError,match='USES_DIAG_HEAD_STEPS'):
        stair.main(['diagnostic-train','--run','foo','--steps','20'])


def test_diagnosis_remains_explicitly_bounded():
    experiments=[{'experiment':'lora:latest','seed':1,'final':{'raw_mae':2.,'zero_abs_mae':.2,'small_negative_sign':.5},'final_train':{'raw_mae':1.}},
                 {'experiment':'lora:extended','seed':1,'final':{'raw_mae':1.,'zero_abs_mae':.1,'small_negative_sign':.6},'final_train':{'raw_mae':.8}},
                 {'experiment':'lora:latest','seed':2,'final':{'raw_mae':2.,'zero_abs_mae':.2,'small_negative_sign':.5},'final_train':{'raw_mae':1.}},
                 {'experiment':'lora:extended','seed':2,'final':{'raw_mae':1.,'zero_abs_mae':.1,'small_negative_sign':.6},'final_train':{'raw_mae':.8}}]
    result=diag._decision(experiments,12,{'games_selected':2})
    assert result['lora_more_training'].startswith('BOUNDED_EVIDENCE_')
    assert result['overall']=='BOUNDED_CAUSAL_EVIDENCE_NOT_PROMOTED'
    assert diag._decision([],0,{})['lora_more_training'].startswith('INCONCLUSIVE')


def test_full_orchestration_without_qwen_is_isolated_and_reports_failure(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    torch.set_num_threads(1)
    run=tmp_path/'active';run.mkdir()
    (run/'lora'/'best_adapter').mkdir(parents=True)
    (run/'lora'/'best_adapter'/'marker').write_text('unchanged')
    source_bytes=(run/'lora'/'best_adapter'/'marker').read_bytes()
    rows=_rows();dev=[dict(row,split='dev') for row in rows]
    x=np.arange(24,dtype=np.float32).reshape(6,4)/50
    from tools.nanojev_qwen_c0_compressive_train import make_network,VARIANTS
    net=make_network(torch,4,VARIANTS['wide'])
    best_sha='best-sha';latest_sha='latest-sha'
    source={'head_hp':{'head_lr':.002,'weight_decay':0.,'aux_weight':.25,'huber_beta':.1,
                       'grad_clip':1.,'head_batch':3,'eval_batch':6,'max_length':32},
       'head_state':{'step':2000,'model':net.state_dict(),'adapter_sha256':latest_sha},
       'head_sha256':'head-source','lora_state':{'step':1800,'best_step':1000},
       'lora_sha256':'lora-source','manifest':{'dataset_sha256':'data',
          'original_prompt_corpus_sha256':'prompts','model':'mock-model',
          'number_of_verified_games':2},
       'cycle':run/'staircase'/'head_cycle_0002','source_adapter':run/'lora'/'best_adapter',
       'games':['alpha','beta'],'all_count':{'train':6,'dev':6},
       'selected':{'train':{'rows':rows,'features':x,'case_count':6},
                   'dev':{'rows':dev,'features':x+.01,'case_count':6}}}
    monkeypatch.setattr(diag,'_source_inputs',lambda *a,**k:source)
    from tools import nanojev_gamer_v1_handoff as gamer
    monkeypatch.setattr(gamer,'file_tree_sha256',lambda path:best_sha)
    def lora_stub(log,**kw):return run/'lora'/'best_adapter','extended-sha',1812
    monkeypatch.setattr(diag,'_clone_and_extend_lora',lora_stub)
    monkeypatch.setattr(diag,'_capture_adapter',lambda log,**kw:{'train':x*1.01,'dev':(x+.01)*1.01})
    result=diag.run_diagnostic_train(run,device='cpu',head_steps=2,eval_every=1,
       sequence_steps=2,lora_steps=12,seeds=2,game_limit=1)
    assert result['status']=='COMPLETED_NOT_PROMOTED'
    assert (run/'lora'/'best_adapter'/'marker').read_bytes()==source_bytes
    dest=Path(result['output'])
    assert (dest/'final_report.json').is_file()
    assert (dest/'events.jsonl').is_file()
    assert (dest/'predictions.csv').is_file()
    assert (dest/'groups.csv').is_file()
    assert len(result['experiments'])>20


def test_preflight_accepts_real_certified_staircase_lineage(tmp_path,monkeypatch):
    """Exercise real source hashes + cached Qwen feature lineage, no HF download."""
    torch=pytest.importorskip('torch')
    torch.set_num_threads(1)
    from safetensors.torch import save_file
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools.nanojev_gamer_v1_pairs import write_dataset
    run=tmp_path/'source';run.mkdir()
    data=run/'certified_pairs.jsonl'
    write_dataset(data,cases_per_game=6,seed=47,include_transition_games=True)
    _,manifest=gamer.ensure_manifest(run,data,'Qwen/Qwen3-0.6B',False,include_transition_games=True)
    best=run/'lora'/'best_adapter';best.mkdir(parents=True)
    (best/'adapter_config.json').write_text(json.dumps({
        'r':16,'task_type':'CAUSAL_LM','base_model_name_or_path':'Qwen/Qwen3-0.6B'}))
    save_file({'foo.weight':torch.tensor([0.5,1.0])},str(best/'adapter_model.safetensors'))
    hp={'model':'Qwen/Qwen3-0.6B','seed':47,'max_length':1024,'max_new_tokens':512,
        'lora_rank':16,'lora_batch':1,'lora_accum':8,'lora_lr':0.0001,'lora_eval_every':200}
    state={'schema':gamer.SCHEMA,'phase':'lora','step':1800,'pending_eval':False,
           'dataset_sha256':manifest['dataset_sha256'],
           'original_prompt_corpus_sha256':manifest['original_prompt_corpus_sha256'],
           'hyperparameters':hp,'adapter':{'foo.weight':torch.tensor([3.,4.])},
           'best_step':1000}
    torch.save(state,run/'lora'/'latest.pt')
    class FakeModel:
        def eval(self):return self
    monkeypatch.setattr(gamer,'model_components',lambda *a,**k:(torch,object(),FakeModel()))
    monkeypatch.setattr(gamer,'capture_original_features',
        lambda _torch,_model,_tok,rows,_device,*,batch_size,max_length:
            np.array([[float(len(r['prompt_08'])%11)/11.,.1,.2,.3,.4,.5,.6,.7]
                      for r in rows],dtype=np.float32))
    stair.head(run,steps=1,device='cpu',head_eval_every=1)
    loaded=diag._source_inputs(run)
    assert len(loaded['games'])==146
    assert loaded['selected']['train']['case_count']==584
    assert loaded['selected']['dev']['case_count']==146
    assert loaded['head_state']['step']==1
    assert loaded['head_state']['adapter_sha256']==gamer.file_tree_sha256(
        loaded['source_adapter'])
    assert not list(run.parent.glob('*diagnostic-train*'))


def test_lora_continuation_touches_clone_not_active_run(tmp_path,monkeypatch):
    from tools import nanojev_gamer_v1_staircase as staircase
    from tools import nanojev_gamer_v1_handoff as gamer
    run=tmp_path/'active';lora=run/'lora';best=lora/'best_adapter'
    best.mkdir(parents=True)
    (best/'model.bin').write_bytes(b'example adapter template')
    (lora/'latest.pt').write_bytes(b'example checkpoint bytes')
    (lora/'report.json').write_text('safe source report')
    (run/'handoff_manifest.json').write_text('example manifest')
    (run/'certified_pairs.jsonl').write_text('example certified bytes')
    prior={str(f.relative_to(run)):f.read_bytes() for f in run.rglob('*') if f.is_file()}
    source={'lora_sha256':gamer.sha_bytes((lora/'latest.pt').read_bytes()),
            'lora_state':{'step':1800}}
    output=tmp_path/'results';log=diag.EvidenceLog(output)
    calls=[]
    class FakeProcess:
        def __init__(self,cmd,**kw):
            calls.append(cmd)
            self.stdout=iter(['GAMER LoRA optimizer: DONE step 1801/1812\n'])
        def wait(self):return 0
        def terminate(self):raise AssertionError('Should not terminate success')
    monkeypatch.setattr(diag.subprocess,'Popen',FakeProcess)
    def fake_snapshot(clone,dest,*,expect_step):
        assert clone==output/'lora_work'
        assert (clone/'lora'/'latest.pt').read_bytes()==prior['lora/latest.pt']
        dest.mkdir()
        (dest/'lora'/'best_adapter').mkdir(parents=True)
        (dest/'lora'/'best_adapter'/'model.bin').write_bytes(b'new candidate')
    monkeypatch.setattr(staircase,'snapshot_latest',fake_snapshot)
    try:
        adapter,sha,step=diag._clone_and_extend_lora(log,run=run,source=source,
            output=output,device='cuda',lora_steps=12)
        assert adapter==output/'lora_candidate'/'lora'/'best_adapter'
        assert sha==gamer.file_tree_sha256(adapter)
        assert step==1812
        assert str(output/'lora_work') in calls[0]
        assert str(run) not in calls[0]
        assert {str(f.relative_to(run)):f.read_bytes() for f in run.rglob('*') if f.is_file()}==prior
    finally:log.close()
