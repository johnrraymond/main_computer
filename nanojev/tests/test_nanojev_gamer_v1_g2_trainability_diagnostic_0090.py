"""G2-0090 progressive memorization, gradients and fail-closed governance."""
import json
from pathlib import Path

import pytest
import torch

from tools import nanojev_gamer_v1_g2_trainability_diagnostic_0090 as m
from tools.nanojev_gamer_v1_graph_head import GamerGraphHead, BIND_CLASSES


@pytest.fixture
def small():
    torch.manual_seed(90)
    head = GamerGraphHead(hidden_size=16,width=16,layers=1,
                          latent_slots=2,memory_slots=2,oracle_coupling=True)
    spec={'method':'joint','threshold':0.0,'k':2,'out_caps':{},'in_caps':{}}
    items=[]
    for idx in range(2):
        objs=[[0,4,'actor'],[6,10,'action'],[12,16,'action']]
        target=torch.zeros(3,3,dtype=torch.long)
        target[0,1+idx]=BIND_CLASSES.index('owns')
        spans=[{'kind':k,'token_first':i,'token_last':i} for i,(_,_,k) in enumerate(objs)]
        items.append({'row':{'prompt':f'Alice case {idx}','family':'encounter'},
                      'hidden':torch.randn(5,16),
                      'eligible':torch.ones(5,dtype=torch.bool),
                      'spans':spans,'objects':objs,'targets':target})
    return head,items,spec


def test_step_limit_validation():
    assert m.stage_limits(None)==(300,450,600,1000)
    assert m.stage_limits('1,2,3,4')==(1,2,3,4)
    for bad in ('1,2,3','1,0,3,4','3,4,hello,6','1,2,3,5001','1,2,3,4,5'):
        with pytest.raises(ValueError,match='G2_0090_STAGE'):
            m.stage_limits(bad)


def test_case_metrics_and_graph_loss_are_real_decoder(small):
    head,items,spec=small
    metrics=m.snapshot(head,items,spec,'cpu')
    assert metrics['cases']==2
    assert len(metrics['records'])==2
    assert metrics['graph_errors']>=0
    for r in metrics['records']:
        assert isinstance(r['exact'],int)
        assert r['false_positive']>=0 and r['false_negative']>=0
        assert r['within_kind_groups']==1
        assert r['gold_edges']==1


def test_gradient_matrix_is_symmetric_and_non_destructive(small):
    head,items,spec=small
    before={k:v.clone() for k,v in head.state_dict().items()}
    result=m.gradient_diagnosis(head,items,spec,'cpu',include_transfer=True)
    assert result['cases']==2
    assert result['pair_count']==1
    assert result['pair_cosines'][0][1]==pytest.approx(result['pair_cosines'][1][0])
    assert len(result['finite_step_transfer']['probes'])==2
    assert result['finite_step_transfer']['probe_count']==2
    for k,v in head.state_dict().items():assert torch.equal(v,before[k]),k


def test_transfer_restores_parameters_on_raised_error(small,monkeypatch):
    head,items,spec=small
    params=m._trainable(head)
    vectors,losses=m._per_case_gradients(head,items,spec,'cpu',params)
    before={k:v.clone() for k,v in head.state_dict().items()}
    def crash(*args,**kwargs):
        raise RuntimeError('simulated diagnostics crash')
    monkeypatch.setattr(m,'snapshot',crash)
    with pytest.raises(RuntimeError,match='simulated'):
        m.reversible_transfer(head,items,spec,'cpu',params,vectors,losses)
    for k,v in head.state_dict().items():assert torch.equal(v,before[k])


def test_progressive_stage_restores_best_head_and_optimizer(small):
    head,items,spec=small
    params=m._trainable(head)
    optimizer=torch.optim.AdamW(params,lr=1e-4,weight_decay=0.)
    first=m.train_stage(head,optimizer,items[:1],spec,'cpu',limit=2,stage=1,
                        every=1,grad_every=2)
    assert first['summary']['stage']==1
    assert first['summary']['steps_executed']<=2
    assert first['summary']['best_exact']==m.snapshot(head,items[:1],spec,'cpu')['exact']
    assert first['summary']['recovered_best_checkpoint']
    second=m.train_stage(head,optimizer,items,spec,'cpu',limit=2,stage=2,
                         every=1,grad_every=2)
    assert second['initial']['cases']==2
    assert second['summary']['best_exact']==m.snapshot(head,items,spec,'cpu')['exact']


def test_no_production_gate_changes():
    assert m.STAGES==(1,2,4,8)
    assert m.prior.cur.GATE_REQUIREMENTS['G2']=={
        'metric':'binding_exact_rate','dev':0.80,'challenge':0.70,'neural_gain':0.08}
    assert m.prior.cur.STAGE_MODULES['G2']==(
        'binding.','pair_projection.','interface_couplers.G1_G2.delta.')


def test_best_selection_prefers_exact_graphs_over_loss():
    full={'exact':2,'graph_errors':2,'mean_loss':2.5}
    weak={'exact':1,'graph_errors':0,'mean_loss':0.01}
    assert m.quality(full)>m.quality(weak)
    assert m.quality({'exact':2,'graph_errors':1,'mean_loss':3.0})>m.quality(full)


def test_regression_classification():
    before={'records':[{'prompt_sha256':'a','exact':1,'false_positive':0,'false_negative':0},
                       {'prompt_sha256':'b','exact':0,'false_positive':1,'false_negative':2}]}
    after={'records':[{'prompt_sha256':'a','exact':0,'false_positive':1,'false_negative':0},
                      {'prompt_sha256':'b','exact':1,'false_positive':0,'false_negative':0}]}
    result=m._case_regressions(before,after)
    assert result['lost_exact']==['a']
    assert result['gained_exact']==['b']
    assert result['existing_case_error_increase']==['a']


def test_resume_refuses_nonexistent_output(tmp_path):
    with pytest.raises(ValueError,match='RESUME_OUTPUT_MISSING'):
        m.diagnose(root=tmp_path,output=tmp_path/'missing',device='cpu',resume=True)


def test_save_stage_locks_and_detects_tamper(tmp_path,small,monkeypatch):
    head,items,spec=small
    params=m._trainable(head)
    optimizer=torch.optim.AdamW(params,lr=1e-4)
    result=m.train_stage(head,optimizer,items[:1],spec,'cpu',limit=1,stage=1,every=1,
                         grad_every=1)
    dest=tmp_path/'result';dest.mkdir()
    (dest/'provenance.json').write_text(json.dumps({'head_sha256':'frozen'}))
    def fake_save(head,manifest,path,paths,*,arm,detail):
        path.mkdir()
        (path/'head.safetensors').write_bytes(b'frozen test')
        (path/'manifest.json').write_text(json.dumps({'arm':arm}))
    monkeypatch.setattr(m.prior,'_save_head',fake_save)
    monkeypatch.setattr(m.prior.cur,'HEAD_FILE','head.safetensors')
    m._checkpoint_stage({},dest,head,{},optimizer,result,1,{})
    assert (dest/'stage-1'/'lock.json').exists()
    lock=m.read(dest/'stage-1'/'lock.json')
    assert lock['stage']==1
    assert lock['g3_unlocked'] is False
    assert lock['optimizer_sha256']==m.sha(dest/'stage-1'/'optimizer.pt')


def test_diagnose_full_progression_hashes_and_resume_fail_closed(tmp_path,small,monkeypatch):
    from safetensors.torch import save_file,load_file
    head,items,spec=small
    import copy
    data=[copy.deepcopy(items[i%2]) for i in range(8)]
    for i,it in enumerate(data):
        it['row']['prompt']=f'Unique training example {i}'
        it['reachable']=1
    old=tmp_path/'0089';old.mkdir()
    old_selection={'cases':[{'prompt_sha256':m.case_key(c),
                             'family':c['row']['family'],'reachable_edges':1} for c in data]}
    (old/'trainability-selection.json').write_text(json.dumps(old_selection))
    (old/'trainability.json').write_text(json.dumps({'results':[{'exact':1},{'exact':1}],
                           'one_pass':True,'eight_pass':False,'status':'EXACT_GRAPH_TRAINABILITY_BLOCKED',
                           'g3_unlocked':False}))
    root=tmp_path/'roots';(root/'composite').mkdir(parents=True)
    (root/'composite'/'composite-interface.json').write_text('{}')
    (root/'certificate').write_text('{}')
    (root/'data.jsonl').write_text('[]')
    (root/'parent'/'training').mkdir(parents=True)
    (root/'parent'/'training'/m.prior.cur.HEAD_FILE).write_bytes(b'parent')
    paths={'dataset':root/'data.jsonl', 'certificate':root/'certificate',
           'composite':root/'composite', 'parent_0087':root/'parent'}
    monkeypatch.setattr(m.prior,'paths_for',lambda root:paths)
    monkeypatch.setattr(m,'_upstream',lambda paths,previous:(spec,{'results':[{'exact':1},{'exact':1}]}))
    monkeypatch.setattr(m.prior,'_load_runtime',lambda paths,device:(None,None,None,head,{}))
    monkeypatch.setattr(m.prior,'_candidate_training_cases',lambda *a,**kw:(data,{}))
    def save_h(head,manifest,path,paths,*,arm,detail):
        path.mkdir()
        save_file({k:v.detach().cpu().contiguous() for k,v in head.state_dict().items()},
                  str(path/m.prior.cur.HEAD_FILE))
        (path/'manifest.json').write_text(json.dumps({'g2_0089':{
          'frozen_g1':True,'parent_head_sha256':m.sha(paths['parent_0087']/'training'/m.prior.cur.HEAD_FILE),
          'arm':arm}}))
    monkeypatch.setattr(m.prior,'_save_head',save_h)
    def fake_load(checkpoint,*,device):
        fresh=GamerGraphHead(hidden_size=16,width=16,layers=1,latent_slots=2,
                              memory_slots=2,oracle_coupling=True)
        fresh.load_state_dict(load_file(str(checkpoint/m.prior.cur.HEAD_FILE),device=device))
        return fresh,m.read(checkpoint/'manifest.json')
    monkeypatch.setattr(m.prior.cur,'_load_checkpoint',fake_load)
    monkeypatch.setattr(m.prior.native,'compare_frozen_stage',lambda *args:None)
    out=tmp_path/'diagnosis'
    report=m.diagnose(root=root,output=out,previous=old,device='cpu',step_limits='1,1,1,1')
    assert report['g2_certified'] is False
    assert report['g3_unlocked'] is False
    assert len(report['stages'])==4
    assert [r['stage'] for r in report['stages']]==[1,2,4,8]
    assert (out/'stage-8'/'checkpoint'/m.prior.cur.HEAD_FILE).exists()
    assert (out/'stage-8'/'gradients.json').exists()
    assert report['checkpoint_mutation'] is False
    assert report['comparison']['cold_start_0089_eight_exact']==1
    with pytest.raises(ValueError,match='ALREADY_COMPLETE'):
        m.diagnose(root=root,output=out,previous=old,device='cpu',step_limits='1,1,1,1',resume=True)
    # A completed stage can be replayed without another optimizer step.
    (out/'report.json').unlink()
    resumed=m.diagnose(root=root,output=out,previous=old,device='cpu',step_limits='1,1,1,1',resume=True)
    assert resumed['eight_exact']==report['eight_exact']
    assert resumed['g3_unlocked'] is False
    # A stage hash alteration is never silently accepted during resume.
    (out/'report.json').unlink()
    (out/'stage-4'/'optimizer.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='STAGE_FILE_CHANGED'):
        m.diagnose(root=root,output=out,previous=old,device='cpu',step_limits='1,1,1,1',resume=True)
