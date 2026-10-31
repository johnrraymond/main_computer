"""CPU-only contract tests for ranked, cumulative first-layer glass training."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import torch

from tools import nanojev_qwen_lm_radius_ladder_frontier as fr
from tools import nanojev_qwen_lm_frontier_rank1_glasses as rank1
from tools import nanojev_qwen_lm_frontier_tasks as tasks


def _args(tmp_path, **overrides):
    argv=['--phase','glasses','--stage','status','--device','cpu',
          '--output',str(tmp_path/'ordered')]
    for k,v in overrides.items():
        if isinstance(v,bool):
            if v:argv.append('--'+k.replace('_','-'))
        else:argv.extend(('--'+k.replace('_','-'),str(v)))
    # reuse production argument parsing without invoking the run
    import argparse
    import inspect
    # our own small parameter record suffices to exercise helper functions
    defaults=dict(samples_output=str(tmp_path/'ordered_mixed_v1'),
        glasses_output=str(tmp_path/'glasses'),glass_scheme='easy',
        glass_seed=48157,glass_train_per_cell=12,glass_validation_per_cell=4,
        glass_easy_threshold=.75,glass_retention_floor=.95,
        glass_feature_layers=4,glass_width=192,glass_lr=.0003,
        glass_preserve_weight=.1,glass_delta_l2=.001,glass_gate_loss=.002,
        glass_steps_per_round=12,glass_rounds=6,glass_plateau_patience=3,
        glass_force_hard=False,max_tokens=256,max_new_tokens=64,
        stage1_checkpoint=None,text_checkpoint=None,baseline=None,
        backbone=None,activation=None,format=None,device='cpu',output=str(tmp_path/'ordered'),
        allow_model_download=False,stage='status')
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_case_decks_reproducible_nonoverlap_and_cover_full_frontier(tmp_path):
    a=_args(tmp_path)
    policy=rank1._policy(a,'deadbeef')
    state={'policy':{'source_levels':2,'operand_boundary':1024,'start_rung_override':-1},
           'seed_state':{'rung':1}}
    train=rank1._case_deck(state,policy,'train')
    val=rank1._case_deck(state,policy,'validation')
    fresh=rank1._case_deck(state,policy,'fresh')
    assert len(train)==216 and len(val)==72 and len(fresh)==72
    assert len({r['id'] for r in train+val+fresh})==360
    assert len({(r['task'],r['level']) for r in train})==18
    assert train==rank1._case_deck(state,policy,'train')
    assert set(r['id'] for r in train).isdisjoint(r['id'] for r in val)


def test_rank_uses_actual_baseline_correctness_not_training_loss():
    rows=[];baseline={}
    for name,pattern in (('logic_graphs',[True]*9+[False]),
                         ('algebra',[True]*2+[False]*8)):
        for i,correct in enumerate(pattern):
            id=f'{name}:L0:{i}'
            rows.append({'id':id,'task':name,'level':0})
            baseline[id]={'correct':correct}
    easy,hard,report=rank1._classify(rows,baseline,.75)
    assert easy=={'logic_graphs:L0'}
    assert hard=={'algebra:L0'}
    assert report['algebra:L0']['accuracy']==.2


def test_comparisons_include_all_regressions_once():
    rows=[{'id':str(i),'task':'algebra' if i>=3 else 'logic_graphs','level':0}
          for i in range(6)]
    baseline={str(i):{'correct':v} for i,v in enumerate((True,True,False,False,True,False))}
    results=[{'id':str(i),'correct':v} for i,v in enumerate((False,True,True,True,True,False))]
    m=rank1._metric(rows,baseline,results,{'logic_graphs:L0'})
    assert m['all']=={'tested':6,'correct':4,'baseline_correct':3,'fixed':2,'regressed':1}
    assert m['easy']['regressed']==1 and m['easy']['fixed']==1
    assert m['hard']['fixed']==1


def test_mixed_success_requires_hard_gain_and_easy_retention():
    base={'easy':{'correct':20},'hard':{'correct':3}}
    assert rank1._mixed_success(base,{'easy':{'correct':19},'hard':{'correct':4,'tested':12}},retention_floor=.95)['success']
    assert not rank1._mixed_success(base,{'easy':{'correct':18},'hard':{'correct':6,'tested':12}},retention_floor=.95)['success']
    assert not rank1._mixed_success(base,{'easy':{'correct':20},'hard':{'correct':3,'tested':12}},retention_floor=.95)['success']


def test_stage_requires_previous_checkpoints_and_skips_hard_if_mixed_works(tmp_path):
    root=tmp_path/'glasses'
    with pytest.raises(RuntimeError,match='COMPLETED_EASY'):
        rank1._check_stage_order(root,'mixed')
    (root/'easy').mkdir(parents=True)
    (root/'easy'/'result.json').write_text(json.dumps({'status':'COMPLETE'}))
    assert rank1._check_stage_order(root,'mixed')
    with pytest.raises(RuntimeError,match='COMPLETED_MIXED'):
        rank1._check_stage_order(root,'hard')
    (root/'mixed').mkdir()
    (root/'mixed'/'result.json').write_text(json.dumps({'status':'COMPLETE',
        'mixed_success':{'A':{'success':False},'B':{'success':True}}}))
    assert not rank1._check_stage_order(root,'hard')
    assert rank1._check_stage_order(root,'hard',True)
    (root/'mixed'/'result.json').write_text(json.dumps({'status':'COMPLETE',
        'mixed_success':{'A':{'success':False},'B':{'success':False}}}))
    assert rank1._check_stage_order(root,'hard')
    assert rank1._predecessor(root,'mixed')=='easy'
    assert rank1._predecessor(root,'hard')=='easy'  # independent fallback


def test_phase_three_status_no_Qwen_load_and_no_mixed_mutation(tmp_path):
    out=tmp_path/'ordered_mixed_v1';out.mkdir()
    (out/'glass_ready.pt').write_bytes(b'no_model_read_during_status')
    (out/'glass_readiness.json').write_text('{}')
    status=fr.main(['--phase','glasses','--stage','status','--output',str(tmp_path/'ordered')])
    assert status['samples_ready']
    assert status['easy'] is None and status['mixed'] is None
    assert (out/'glass_ready.pt').read_bytes()==b'no_model_read_during_status'
    assert not (tmp_path/'ordered_mixed_glasses_v1').exists()


def test_independent_glass_best_save_load_and_optimizer_resume(tmp_path):
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    args=_args(tmp_path,glass_width=6,glass_feature_layers=2)
    root=tmp_path/'glasses'
    policy=rank1._policy(args,'sha')
    glasses,opts,parents=rank1._load_networks(args,8,root,'easy',policy,'cpu')
    assert parents=={}
    for name in ('A','B'):
        assert glasses[name].net.delta.weight.count_nonzero()==0
        with torch.no_grad():glasses[name].net.delta.bias[0]=3.1 if name=='A' else 7.2
    stage=root/'easy';stage.mkdir(parents=True)
    metric={'easy':{'correct':5},'hard':{'correct':2},'all':{'correct':7,'regressed':0}}
    for name in ('A','B'):rank1._save_best(stage,name,glasses[name],opts[name],policy,metric,2)
    resumed,optimizers,hashes=rank1._load_networks(args,8,root,'mixed',policy,'cpu')
    assert hashes.keys()=={'A','B'}
    assert resumed['A'].net.delta.bias[0].item()==pytest.approx(3.1)
    assert resumed['B'].net.delta.bias[0].item()==pytest.approx(7.2)
    assert resumed['A'].gated is False and resumed['B'].gated is True
    state={'policy':policy,'parents':hashes,'step_in_round':3,'round':1}
    (root/'mixed').mkdir(exist_ok=True)
    rank1._working_state(root/'mixed',resumed,optimizers,state)
    with torch.no_grad():resumed['A'].net.delta.bias[0]=9.9
    result=rank1._resume(root/'mixed',resumed,optimizers,policy,'cpu')
    assert result['step_in_round']==3
    assert resumed['A'].net.delta.bias[0].item()==pytest.approx(3.1)
    with pytest.raises(RuntimeError,match='POLICY_DRIFT'):
        rank1._resume(root/'mixed',resumed,optimizers,{**policy,'lr':.7},'cpu')


def test_phase_flags_retain_original_default_and_glass_dispatch(tmp_path,monkeypatch):
    from tools import nanojev_qwen_lm_frontier_rank1_glasses as glass
    calls=[]
    monkeypatch.setattr(glass,'main',lambda a:calls.append(a) or {'called':True})
    result=fr.main(['--phase','glasses','--glass-scheme','mixed','--stage','preview',
                    '--output',str(tmp_path/'ordered'),
                    '--glass-steps-per-round','7'])
    assert result['called']
    a=calls[0]
    assert a.glass_scheme=='mixed'
    assert a.glass_steps_per_round==7
    assert a.samples_output==str((tmp_path/'ordered_mixed_v1').resolve())
    assert a.glasses_output==str((tmp_path/'ordered_mixed_glasses_v1').resolve())


def test_mixed_checkpoint_success_rank_priority():
    # If Mixed acquired new hard competence without sacrificing easy, that
    # checkpoint outranks a later one with higher raw correctness but forgetting.
    easy={'easy':{'correct':20},'hard':{'correct':2}}
    good={'easy':{'correct':20},'hard':{'correct':5,'tested':10},
          'all':{'correct':25,'regressed':0}}
    bad={'easy':{'correct':15},'hard':{'correct':11,'tested':10},
         'all':{'correct':26,'regressed':1}}
    def scored(x):
        success=rank1._mixed_success(easy,x,retention_floor=.95)
        return [int(success['success']),x['all']['correct'],-x['all']['regressed']]
    assert scored(good)>scored(bad)


def test_previously_fixed_errors_receive_supervised_retention_not_zero_delta(tmp_path,monkeypatch):
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    glass=ab.ContinuousGlass(8,2,6,False)
    opt=torch.optim.AdamW(glass.net.parameters(),lr=.2)
    class BB:
        head=torch.nn.Linear(8,2,bias=False)
        def get_output_embeddings(self):return self.head
    bb=BB()
    with torch.no_grad():
        bb.head.weight[0].fill_(-.4)
        bb.head.weight[1].fill_(.4)
    row={'id':'once_wrong','source':'PROBLEM: 1+1','target':'PROBLEM: 2'}
    replay=None
    monkeypatch.setattr(ab,'_rollout',lambda *a,**k:{'correct':True,'wrong_state':None})
    monkeypatch.setattr(ab,'_token_ids',lambda tok,string:[0,1])
    monkeypatch.setattr(ab,'_capture_gold_position',
        lambda bb,tok,wrong,args:(torch.ones(1,8),torch.ones(1,16)))
    args=SimpleNamespace(device='cpu',max_new_tokens=4,
        preserve_weight=.1,delta_l2=0,gate_loss=0)
    before=glass.net.delta.weight.detach().clone()
    rank1._train_rank1_step(glass,opt,bb,None,row,replay,
                           {row['id']:{'correct':False}},args,__import__('random').Random(5))
    assert not torch.equal(glass.net.delta.weight,before), 'acquired corrections must stay supervised'
    glass=ab.ContinuousGlass(8,2,6,False)
    opt=torch.optim.AdamW(glass.net.parameters(),lr=.2)
    before=glass.net.delta.weight.detach().clone()
    rank1._train_rank1_step(glass,opt,bb,None,row,replay,
                           {row['id']:{'correct':True}},args,__import__('random').Random(5))
    assert torch.equal(glass.net.delta.weight,before), 'existing Qwen successes favor zero intervention'


def test_mock_full_easy_mixed_hard_handoff_keeps_backbone_immutable(tmp_path,monkeypatch):
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    parent=tmp_path/'ordered_mixed_v1';parent.mkdir()
    data={'schema':rank1.samples.SCHEMA,'state':{
        'step':100,'best_step':100,'seed_state':{'rung':1},
        'policy':{'source_levels':2,'operand_boundary':1024,'start_rung_override':-1}},
        'lora':{'mock.weight':torch.ones(2,2)}}
    torch.save(data,parent/'glass_ready.pt')
    (parent/'glass_readiness.json').write_text(json.dumps({
        'status':'MIXED_LOSS_PLATEAU','glass_ready_checkpoint':'glass_ready.pt'}))
    frozen_before=(parent/'glass_ready.pt').read_bytes()
    class FakeBB:
        def __init__(self):self.head=torch.nn.Linear(8,2)
        def get_output_embeddings(self):return self.head
    monkeypatch.setattr(rank1,'_load_backbone',lambda *a:(FakeBB(),None,None))
    hard_tasks={'algebra','arithmetic_operators','fractions_units'}
    def mock_rollout(bb,tok,row,args,glass=None,**kwargs):
        idx=int(row['id'].split(':')[-1]); hard=row['task'] in hard_tasks
        plain=(idx%5==0) if hard else (idx%5!=0)
        if glass is None:return {'id':row['id'],'correct':plain,'output':str(int(plain))}
        correction=glass.net.delta.bias.detach()[1 if hard else 0].item()>0.0
        return {'id':row['id'],'correct':plain or correction,'output':str(int(plain or correction))}
    monkeypatch.setattr(ab,'_rollout',mock_rollout)
    def fake_step(glass,opt,bb,tok,row,replay,baseline,args,rng):
        with torch.no_grad():glass.net.delta.bias[1 if row['task'] in hard_tasks else 0]=.1
        return {'loss':.01}
    monkeypatch.setattr(rank1,'_train_rank1_step',fake_step)
    args=_args(tmp_path,stage='run',glass_width=6,glass_feature_layers=2,
               glass_rounds=2,glass_steps_per_round=4,glass_plateau_patience=3,
               stage1_checkpoint='fake1',text_checkpoint='fake2')
    easy=rank1.run(args)
    assert easy['status']=='COMPLETE' and easy['scheme']=='easy'
    assert (tmp_path/'glasses'/'easy'/'best_A.pt').exists()
    assert (tmp_path/'glasses'/'easy'/'best_B.pt').exists()
    assert (tmp_path/'glasses'/'easy'/'working.pt').exists()
    # Resumption returns the same completed result, no additional training.
    assert rank1.run(args)==easy
    args.glass_scheme='mixed'
    mixed=rank1.run(args)
    assert mixed['status']=='COMPLETE' and mixed['skip_hard']
    assert mixed['mixed_success']['A']['success']
    assert mixed['mixed_success']['B']['success']
    assert mixed['validation']['A']['easy']['correct']>=easy['validation']['A']['easy']['correct']
    assert (tmp_path/'glasses'/'mixed'/'best_B.pt').exists()
    args.glass_scheme='hard'
    hard=rank1.run(args)
    assert hard['status']=='SKIPPED'
    assert (parent/'glass_ready.pt').read_bytes()==frozen_before
    assert not (tmp_path/'glasses'/'hard').exists()

@pytest.mark.parametrize('shape,expected_easy,expected_hard', [
    ('all_easy',18,0),('all_hard',0,18),('perfect',18,0)])
def test_resume_cached_baseline_accepts_one_sided_classification(tmp_path,monkeypatch,shape,expected_easy,expected_hard):
    """Previously failing 288-case census resumes without a new model scan."""
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    args=_args(tmp_path)
    policy=rank1._policy(args,'frozen-sha')
    state={'policy':{'source_levels':2,'operand_boundary':1024,'start_rung_override':-1},
           'seed_state':{'rung':1}}
    root=tmp_path/'glasses';root.mkdir()
    train=rank1._case_deck(state,policy,'train')
    validation=rank1._case_deck(state,policy,'validation')
    known={}
    for offset,row in enumerate(train+validation):
        if shape=='all_easy':correct=(offset%12)!=0
        elif shape=='all_hard':correct=(offset%12)<2
        else:correct=True
        known[row['id']]={'id':row['id'],'correct':correct,'output':str(correct)}
    with (root/'baseline.jsonl').open('w',encoding='utf8') as f:
        for row in train+validation:
            f.write(json.dumps({'id':row['id'],'backbone_sha256':policy['backbone_sha256'],
                                'result':known[row['id']]})+'\n')
    easy,hard,stats=rank1._classify(train,known,policy['easy_threshold'])
    original={'schema':rank1.SCHEMA,'policy':policy,'train_cases':len(train),
              'validation_cases':len(validation),'cells':stats,'easy':sorted(easy),
              'hard':sorted(hard),'baseline_correct':sum(v['correct'] for v in known.values())}
    (root/'classification.json').write_text(json.dumps(original))
    event=[]
    monkeypatch.setattr(fr,'_event',lambda name,payload:event.append((name,payload)))
    monkeypatch.setattr(ab,'_rollout',lambda *a,**k:pytest.fail('baseline must not rescan'))
    contents=(root/'baseline.jsonl').read_bytes()
    _,_,recovered,now_easy,now_hard=rank1._setup_deck(
        args,root,None,None,None,policy,state)
    assert recovered==known
    assert len(now_easy)==expected_easy and len(now_hard)==expected_hard
    assert (root/'baseline.jsonl').read_bytes()==contents
    assert json.loads((root/'classification.json').read_text())==original
    report=next(v for name,v in event if name=='GLASSES_BASELINE_CLASSIFICATION')
    assert report['easy_cells']==expected_easy and report['hard_cells']==expected_hard
    assert report['train_total']==216 and report['validation_total']==72
    assert report['no_invented_hard_cases'] is True
    assert report['reused_baseline_records']==288
    assert report['new_baseline_records']==0


def test_hard_skip_when_no_hard_cells_even_if_forced(tmp_path):
    root=tmp_path/'glasses'
    (root/'easy').mkdir(parents=True)
    (root/'easy'/'result.json').write_text(json.dumps({'status':'COMPLETE'}))
    (root/'mixed').mkdir()
    (root/'mixed'/'result.json').write_text(json.dumps({'status':'COMPLETE',
        'mixed_success':{'A':{'success':False},'B':{'success':False}}}))
    (root/'classification.json').write_text(json.dumps({'easy':['algebra:L0'],'hard':[]}))
    assert not rank1._check_stage_order(root,'hard')
    assert not rank1._check_stage_order(root,'hard',True)
    (root/'classification.json').write_text(json.dumps({'easy':[],'hard':['algebra:L0']}))
    assert rank1._check_stage_order(root,'hard')


@pytest.mark.parametrize('case_type', ['all_easy','all_hard'])
def test_one_sided_census_full_easy_mixed_handoff(tmp_path,monkeypatch,case_type):
    from tools import nanojev_qwen_lm_deformation_smoke as ab
    parent=tmp_path/'ordered_mixed_v1';parent.mkdir()
    data={'schema':rank1.samples.SCHEMA,'state':{'step':100,'best_step':100,
        'seed_state':{'rung':1},'policy':{'source_levels':2,'operand_boundary':1024,
        'start_rung_override':-1}},'lora':{'mock.weight':torch.ones(2,2)}}
    torch.save(data,parent/'glass_ready.pt')
    (parent/'glass_readiness.json').write_text(json.dumps({
        'status':'MIXED_LOSS_PLATEAU','glass_ready_checkpoint':'glass_ready.pt'}))
    frozen_sha=fr._sha256(parent/'glass_ready.pt')
    class FakeBB:
        def __init__(self):self.head=torch.nn.Linear(8,2)
        def get_output_embeddings(self):return self.head
    monkeypatch.setattr(rank1,'_load_backbone',lambda *a:(FakeBB(),None,None))
    def mock_rollout(bb,tok,row,args,glass=None,**kwargs):
        idx=int(row['id'].split(':')[-1])
        correct=(idx%12!=0) if case_type=='all_easy' else (idx%12<2)
        fix=(glass is not None and glass.net.delta.bias.detach()[0].item()>.0)
        return {'id':row['id'],'correct':bool(correct or fix),'output':'mock'}
    monkeypatch.setattr(ab,'_rollout',mock_rollout)
    def fake_step(glass,opt,bb,tok,row,replay,baseline,args,rng):
        with torch.no_grad():glass.net.delta.bias[0]=.1
        return {'loss':.01}
    monkeypatch.setattr(rank1,'_train_rank1_step',fake_step)
    args=_args(tmp_path,stage='run',glass_width=6,glass_feature_layers=2,
        glass_rounds=2,glass_steps_per_round=3,glass_plateau_patience=3,
        stage1_checkpoint='fake1',text_checkpoint='fake2')
    easy=rank1.run(args)
    assert easy['status']=='COMPLETE'
    classification=json.loads((tmp_path/'glasses'/'classification.json').read_text())
    assert len(classification['easy'])==(18 if case_type=='all_easy' else 0)
    assert len(classification['hard'])==(0 if case_type=='all_easy' else 18)
    assert rank1.run(args)==easy  # completed stage is idempotent
    args.glass_scheme='mixed'
    mixed=rank1.run(args)
    assert mixed['status']=='COMPLETE'
    assert mixed['skip_hard']
    args.glass_scheme='hard'
    hard=rank1.run(args)
    assert hard['status']=='SKIPPED'
    assert hard['reason']==('NO_HARD_CELLS_IN_BASELINE' if case_type=='all_easy'
                             else 'MIXED_ALREADY_SUCCEEDED')
    assert fr._sha256(parent/'glass_ready.pt')==frozen_sha
