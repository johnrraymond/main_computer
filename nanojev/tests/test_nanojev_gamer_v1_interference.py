"""Opt-in causal interference diagnostic regression tests."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools.nanojev_gamer_v1_interference import (
    source_groups,table_signature,target_zone,causal_probe,run_interference,
    optimizer_comparison_probe,run_optimizer_comparison)
from tools.nanojev_gamer_v1_diagnostic import main


def _tiny_rows():
    table = [
        ('alpha',-0.5,['calculation','strategic_payoff']),
        ('alpha',0.0,['calculation','strategic_payoff']),
        ('alpha',0.5,['calculation','strategic_payoff']),
        ('beta',-5.0,['calculation','state_transition']),
        ('beta',0.0,['calculation','state_transition']),
        ('beta',5.0,['calculation','state_transition']),
    ]
    return [{'game_id':g,'answer':y,'table_families':f,'split':'train','prompt_08':f'{g}{i}'}
            for i,(g,y,f) in enumerate(table)]


def test_overlapping_table_cohorts_and_stratified_games():
    rows=_tiny_rows()
    groups=source_groups(rows,1.0)
    assert len(groups['game:alpha'])==3
    assert len(groups['family:calculation'])==6
    assert len(groups['family:state_transition'])==3
    assert len(groups['family_zone:strategic_payoff|small_negative'])==1
    assert 'signature:calculation+strategic_payoff' in groups
    assert 'signature:calculation+state_transition' in groups
    assert target_zone(-.5,1)=='small_negative'
    assert target_zone(0,1)=='zero'
    assert table_signature(rows[0])=='calculation+strategic_payoff'
    with pytest.raises(ValueError,match='UNKNOWN_TABLE_FAMILY'):
        table_signature({'table_families':['fake']})


def test_identical_start_and_replicated_causal_deltas():
    torch=pytest.importorskip('torch')
    from tools.nanojev_qwen_c0_compressive_train import make_network
    rows=_tiny_rows()
    dev=[dict(r,split='dev') for r in rows]
    x=np.arange(24,dtype=np.float32).reshape(6,4)/20
    xd=(x+.02).copy()
    torch.manual_seed(19)
    net=make_network(torch,4,(16,8,4,2))
    opt=torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=0.)
    saved_net=copy.deepcopy(net.state_dict())
    saved_opt=copy.deepcopy(opt.state_dict())
    hp={'head_batch':4,'head_lr':.001,'weight_decay':0.,'aux_weight':.1,
        'huber_beta':1.,'grad_clip':1.,'eval_batch':4}
    before={k:v.clone() for k,v in saved_net.items()}
    a=causal_probe(torch,rows,dev,x,xd,weights=saved_net,
                    optimizer_state=saved_opt,hyperparameters=hp,
                    hidden=(16,8,4,2),repeats=2,seed=10)
    b=causal_probe(torch,rows,dev,x,xd,weights=saved_net,
                    optimizer_state=saved_opt,hyperparameters=hp,
                    hidden=(16,8,4,2),repeats=2,seed=10)
    assert a==b
    assert a['source_count']>6
    assert len(a['source_batches']['game:alpha'])==2
    assert all(0<=i<6 for entry in a['source_batches']['game:alpha']
               for i in entry['train_indices'])
    assert a['repeats']==2 and a['updates_per_trial']==1
    assert {'source':'game:alpha','target':'game:beta'}.items() <= next(
        e for e in a['effects'] if e['source']=='game:alpha' and e['target']=='game:beta').items()
    assert any(g['source_a'].startswith('family:') for g in a['gradient_cosines'])
    for key in before: assert torch.equal(saved_net[key],before[key])
    assert saved_opt["state"]=={}
    assert opt.state_dict()==saved_opt



def test_three_optimizer_modes_use_the_same_batch_gradients_and_matched_norm():
    torch=pytest.importorskip('torch')
    from tools.nanojev_qwen_c0_compressive_train import make_network, signed_log, neural_loss
    rows=_tiny_rows()
    dev=[dict(r,split='dev') for r in rows]
    x=np.arange(24,dtype=np.float32).reshape(6,4)/20
    xd=(x+.03).copy()
    hp={'head_batch':4,'head_lr':.004,'weight_decay':.01,'aux_weight':.1,
        'huber_beta':1.,'grad_clip':1.,'eval_batch':4}
    torch.manual_seed(37)
    net=make_network(torch,4,(16,8,4,2))
    opt=torch.optim.AdamW(net.parameters(),lr=hp['head_lr'],weight_decay=hp['weight_decay'])
    xx=torch.as_tensor(x)
    yy=torch.as_tensor(signed_log(np.asarray([r['answer'] for r in rows])),dtype=torch.float32)
    # Populate nontrivial AdamW moment history, including distinct signs.
    for _ in range(4):
        a,b=net(xx)
        loss=neural_loss(torch,a,b,yy,beta=1.,aux_weight=.1)
        opt.zero_grad(set_to_none=True);loss.backward()
        torch.nn.utils.clip_grad_norm_(net.parameters(),1.)
        opt.step()
    weights=copy.deepcopy(net.state_dict())
    moments=copy.deepcopy(opt.state_dict())
    assert moments['state']
    first=optimizer_comparison_probe(
        torch,rows,dev,x,xd,weights=weights,optimizer_state=moments,
        hyperparameters=hp,hidden=(16,8,4,2),seed=9)
    second=optimizer_comparison_probe(
        torch,rows,dev,x,xd,weights=weights,optimizer_state=moments,
        hyperparameters=hp,hidden=(16,8,4,2),seed=9)
    assert first==second
    assert first['optimizer_history_steps_min']==4
    assert first['optimizer_history_steps_max']==4
    assert first['updates_per_trial']==1
    assert len(first['trials'])==6*2*3
    assert len(first['predictions'])==6*2*3*len(dev)
    for source in first['source_groups']:
        for repeat in (0,1):
            trials=[t for t in first['trials'] if t['source']==source and t['repeat']==repeat]
            assert len(trials)==3
            assert len({tuple(t['train_indices']) for t in trials})==1
            assert len({t['training_loss_before_update'] for t in trials})==1
            assert len({t['unclipped_gradient_norm'] for t in trials})==1
            assert all(t['norm_matched'] for t in trials)
            norms=[t['matched_update_norm'] for t in trials]
            assert max(norms)-min(norms)<max(norms)*0.00001
    assert any(t['update_cosine_to_saved_adamw']<0.999
               for t in first['trials'] if t['mode']=='fresh_adamw')
    for k in weights:
        assert torch.equal(net.state_dict()[k],weights[k])
    assert opt.state_dict()['state'].keys()==moments['state'].keys()
    for ident in moments['state']:
        for k,v in moments['state'][ident].items():
            assert torch.equal(v,opt.state_dict()['state'][ident][k])


def test_optimizer_comparison_requires_saved_optimizer_history():
    torch=pytest.importorskip('torch')
    rows=_tiny_rows()
    x=np.zeros((6,4),dtype=np.float32)
    with pytest.raises(ValueError,match='OPTIMIZER_HISTORY_UNAVAILABLE'):
        optimizer_comparison_probe(torch,rows,rows,x,x,weights={},
                                   optimizer_state={'state':{},'param_groups':[]},
                                   hyperparameters={},hidden=(16,8,4,2))


def test_cli_is_explicit_and_rejects_watch(tmp_path,monkeypatch,capsys):
    from test_nanojev_gamer_v1_diagnostic import _make_run
    run,_,_=_make_run(tmp_path)
    def fake(*a,**kw):
        return {'cycle':'head_cycle_0002','head_step':2000,'source_groups':5,'repeats':2,
                'baseline_dev_mae':5.4,'reproduced_game_damage_pairs':0,
                'top_game_damage':[],'report':str(tmp_path/'matrix.json'),
                'matrix_csv':str(tmp_path/'matrix.csv')}
    monkeypatch.setattr('tools.nanojev_gamer_v1_interference.run_interference',fake)
    monkeypatch.setattr('tools.nanojev_gamer_v1_interference.run_optimizer_comparison',
        lambda *a,**kw: {'cycle':'head_cycle_0002','head_step':2000,'repeats':2,
          'sources':['zone:small_positive'],'optimizer_history_steps_min':2000,
          'optimizer_history_steps_max':2000,'summary':[],
          'report':str(tmp_path/'optim.json'),
          'predictions_csv':str(tmp_path/'optim.csv')})
    assert main(['--run',str(run),'--json'])==0
    normal=json.loads(capsys.readouterr().out)
    assert 'interference' not in normal
    assert main(['--run',str(run),'--json','--interference'])==0
    focused=json.loads(capsys.readouterr().out)
    assert focused['interference']['head_step']==2000
    assert main(['--run',str(run),'--json','--interference-optimizer'])==0
    comparison=json.loads(capsys.readouterr().out)
    assert 'interference' not in comparison
    assert comparison['interference_optimizer']['head_step']==2000
    with pytest.raises(SystemExit):
        main(['--run',str(run),'--interference','--watch','30'])
    with pytest.raises(SystemExit):
        main(['--run',str(run),'--interference-optimizer','--watch','30'])
    with pytest.raises(SystemExit):
        main(['--run',str(run),'--interference-repeats','1','--interference'])
    with pytest.raises(SystemExit):
        main(['--run',str(run),'--interference-output',str(tmp_path)])


def test_full_checkpoint_and_feature_lineage_and_external_report(tmp_path):
    torch=pytest.importorskip('torch')
    from tools.nanojev_qwen_c0_compressive_train import make_network, VARIANTS
    from tools.nanojev_gamer_v1_handoff import canonical,sha_bytes,original_prompt
    run=tmp_path/'run'
    cycle=run/'staircase'/'head_cycle_0001'
    (cycle/'to_numerical'/'feature_cache').mkdir(parents=True)
    (cycle/'lora').mkdir(parents=True)
    (cycle/'staircase_cycle.json').write_text('{}')
    rows=_tiny_rows()+[dict(r,split='dev',prompt_08='dev'+str(i)) for i,r in enumerate(_tiny_rows())]
    dataset=''.join(json.dumps(row,sort_keys=True)+'\n' for row in rows)
    (run/'certified_pairs.jsonl').write_text(dataset)
    manifest={'dataset_sha256':hashlib.sha256(dataset.encode()).hexdigest(),
              'original_prompt_corpus_sha256':'corpus', 'model':'Qwen/Qwen3-0.6B'}
    (run/'handoff_manifest.json').write_text(json.dumps(manifest))
    sha='a'*64
    (cycle/'lora'/'report.json').write_text(json.dumps({'best_adapter_sha256':sha}))
    hp={'model':'Qwen/Qwen3-0.6B','max_length':512,'head_batch':3,'eval_batch':6,
        'head_lr':.001,'weight_decay':0.,'huber_beta':1.,'aux_weight':.1,'grad_clip':1.}
    torch.manual_seed(2)
    head=make_network(torch,4,VARIANTS['wide'])
    opt=torch.optim.AdamW(head.parameters(),lr=.001,weight_decay=0.)
    # Real AdamW moment history for the additional optimizer-isolation test.
    for par in head.parameters(): par.grad=torch.full_like(par,.01)
    opt.step()
    opt.zero_grad(set_to_none=True)
    state={'schema':'nanojev.gamer.v1.original-prompt-shared-handoff.v1',
           'phase':'to_numerical','step':2000,'dataset_sha256':manifest['dataset_sha256'],
           'original_prompt_corpus_sha256':'corpus','adapter_sha256':sha,
           'model':head.state_dict(),'optimizer':opt.state_dict(),'hyperparameters':hp,
           'sampling_policy':{'focus':.75,'radius':1.0}}
    torch.save(state,cycle/'to_numerical'/'latest.pt')
    for split in ('train','dev'):
        sub=[r for r in rows if r['split']==split]
        identity={'dataset_sha256':manifest['dataset_sha256'],
                  'original_prompt_corpus_sha256':'corpus','adapter_sha256':sha,
                  'model':hp['model'],'max_length':hp['max_length'],
                  'prompts_sha256':sha_bytes(canonical([original_prompt(r) for r in sub]).encode())}
        x=np.linspace(-1,1,len(sub)*4,dtype=np.float32).reshape(len(sub),4)
        np.savez_compressed(cycle/'to_numerical'/'feature_cache'/f'{split}.npz',
                            identity=canonical(identity),features=x)
    with pytest.raises(ValueError,match='OUTSIDE_ACTIVE_RUN'):
        run_interference(run,output=run/'bad')
    outcome=run_interference(run,repeats=2,output=tmp_path/'results')
    assert outcome['head_step']==2000
    assert Path(outcome['matrix_csv']).is_file()
    report=json.loads(Path(outcome['report']).read_text())
    assert report['tables_overlap'] is True
    assert report['experiment']['source_count']==len(report['experiment']['source_counts'])
    assert report['experiment']['gradient_cosines']
    assert report['reproduced_game_damage_top_30']==report['reproduced_game_damage_top_30']
    assert not (run/'bad').exists()
    with pytest.raises(ValueError,match='OUTSIDE_ACTIVE_RUN'):
        run_optimizer_comparison(run,output=run/'optimizer_bad')
    optimizer_result=run_optimizer_comparison(run,repeats=2,output=tmp_path/'results')
    assert optimizer_result['head_step']==2000
    optimizer_report=json.loads(Path(optimizer_result['report']).read_text())
    assert optimizer_report['experiment']['optimizer_history_steps_min']==1
    assert optimizer_report['experiment']['modes']==[
        'saved_adamw','fresh_adamw','plain_gradient_descent']
    assert optimizer_report['experiment']['source_groups']
    assert Path(optimizer_result['predictions_csv']).is_file()
    assert sum(1 for _ in open(optimizer_result['predictions_csv'],encoding='utf-8'))==(
        1+len(optimizer_report['experiment']['trials'])*len([r for r in rows if r['split']=='dev']))
    assert not (run/'optimizer_bad').exists()
    # No trained state or dataset changes, and reports exist only outside run.
    fresh=torch.load(cycle/'to_numerical'/'latest.pt',map_location='cpu',weights_only=True)
    for name in head.state_dict():
        assert torch.equal(fresh['model'][name],state['model'][name])
    corrupted=np.load(cycle/'to_numerical'/'feature_cache'/'train.npz',allow_pickle=False)
    corrupted.close()
    wrong=json.loads((run/'handoff_manifest.json').read_text());wrong['dataset_sha256']='f'*64
    (run/'handoff_manifest.json').write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match='DATASET_SHA_MISMATCH'):
        run_interference(run,output=tmp_path/'results')
