"""No CUDA/model downloads: exact oracle, radius extension, budget invariants."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import nanojev_qwen_lm_radius_ladder as m


def test_radii_must_start_zero_grow_and_fit():
    assert m.parse_radii('0,1,2,4,8',1024)==(0,1,2,4,8)
    for text in ('1,2','0,2,1','0,0,1','0,-1','0,2000','0,1,2,'):
        with pytest.raises(ValueError):m.parse_radii(text,1024)


def test_t00_exhaustive_entire_bounded_pairs():
    cases,scope=m.deck(0,8,1024)
    assert scope['kind']=='exhaustive_ordered_t00'
    assert len(cases)==34761
    assert len({c['source'] for c in cases})==34761
    assert len([c for c in cases if c['residual']==0])==2049
    for c in cases:
        assert sum(c['leaves'])==c['residual']
        assert c['target']==f'ANSWER: {c["residual"]}'
        assert all(abs(x)<=1024 for x in c['leaves'])
        assert len(c['complete_chain'])==2


def test_higher_rung_full_51_cells_and_repeated_zero():
    for rung in range(1,9):
        cases,scope=m.deck(rung,8,1024,per_cell=8)
        assert scope['cells']==51 and not scope['unreachable_cells']
        assert len(cases)==408
        assert len([c for c in cases if c['residual']==0])==24
        assert len({c['source'] for c in cases})==len(cases)
        for c in cases:
            assert len(c['complete_chain'])==rung+2
            assert c['source'].startswith('PROBLEM: ')
            assert c['target'].startswith('PROBLEM: ')
            assert all(abs(x)<=1024 for x in c['leaves'])
            acc=0
            for x in c['leaves']:
                acc+=x
                assert abs(acc)<=1024


def test_radius_expansion_greater_than_eight_and_impossible_cells():
    for rung in (0,1,3,8):
        cases,scope=m.deck(rung,16,1024,per_cell=2,t00_mode='sampled')
        assert any(c['residual']==16 for c in cases)
        assert any(c['residual']==-16 for c in cases)
        assert any(c['residual']==0 for c in cases)
        assert all(abs(c['residual'])<=16 for c in cases)
        assert all(abs(x)<=1024 for c in cases for x in c['leaves'])
    cases,scope=m.deck(1,32,1024,per_cell=2,t00_mode='sampled')
    assert scope['unreachable_cells']
    assert all(x['reason']=='arithmetic_unreachable' for x in scope['unreachable_cells'])


def test_determinism_and_prefix_stability():
    old,_=m.deck(3,4,1024,per_cell=2)
    new,_=m.deck(3,8,1024,per_cell=2)
    ids={c['id']:c for c in new}
    assert all(ids[x['id']]==x for x in old)
    assert m.deck_fingerprint(old)==m.deck_fingerprint(m.deck(3,4,1024,per_cell=2)[0])


def test_oracle_packet_is_one_reduction_and_never_final_for_upper_rungs():
    c=m._case(3,(64,-64,1,-1,2),'example')
    assert c['target']=='PROBLEM: ((0+1)+(-1))+2'
    assert c['complete_chain'][-1]=='ANSWER: 2'
    assert m.correctness(c['source'],c['target'],'ANSWER: 2\n')[0] is False
    assert m.correctness(c['source'],c['target'],c['target']+'\n')[0] is True


def test_strict_lm_packet_and_no_shortcuts():
    assert m.parse_packet('ANSWER: 0\n')=='ANSWER: 0'
    assert m.parse_packet('PROBLEM: 1+2\n')=='PROBLEM: 1+2'
    for bad in ('', '0', 'ANSWER: 01','ANSWER: 1.0', 'ANSWER: -0\nANSWER: 0',
                'ANSWER: 0\nPROBLEM: 1+2','ANSWER: 0 plus more','ANSWER: +0',
                'ANSWER: 0\n\n','SOLUTION: 0'):
        assert m.parse_packet(bad) is None, bad


def test_state_budgets_explicit_and_no_auto_extension():
    cfg={'max_rung':8,'steps_per_rung':10}
    s=m._new_state(cfg,'sha','base')
    assert all(v==10 for v in s['budget_by_rung'].values())
    with pytest.raises(RuntimeError):m._budget_rule(s,5)
    s['status']='BUDGET_EXHAUSTED';s['rung']=2
    m._budget_rule(s,5)
    assert s['budget_by_rung']['2']==15
    assert s['budget_by_rung']['1']==10
    assert s['status']=='READY'
    with pytest.raises(ValueError):m._budget_rule(s,-1)


def test_spend_ledger_durable_resume_and_tampering(tmp_path):
    assert m._spent_ledger(tmp_path)=={}
    m._charge_step(tmp_path,0,1)
    m._charge_step(tmp_path,0,2)
    m._charge_step(tmp_path,1,1)
    assert m._spent_ledger(tmp_path)=={'0':2,'1':1}
    m._charge_step(tmp_path,0,4)
    with pytest.raises(RuntimeError,match='NONMONOTONE'):m._spent_ledger(tmp_path)


def test_hash_config_and_resume_rejects_tampering(tmp_path):
    torch=pytest.importorskip('torch')
    class Zero:
        @staticmethod
        def restore_lora(bb,lora):bb.value=lora
    cfg={'x':2};state={'schema':m.SCHEMA,'config':cfg,'config_hash':m._hash(cfg),
                        'source_sha256':'sha','frozen_sha256':'frozen'}
    obj={'schema':m.SCHEMA,'state':state,'lora':{'w':torch.tensor(1.)},'optimizer':None}
    torch.save(obj,tmp_path/'working.pt')
    bb=SimpleNamespace(value=None)
    got,opt=m._load_resume(tmp_path,cfg,bb,Zero,'sha','frozen')
    assert got==state and opt is None and bb.value['w'].item()==1.
    with pytest.raises(RuntimeError,match='CONFIG'):
        m._load_resume(tmp_path,{'x':3},bb,Zero,'sha','frozen')
    with pytest.raises(RuntimeError,match='MODEL_DRIFT'):
        m._load_resume(tmp_path,cfg,bb,Zero,'other','frozen')


def test_certified_expansion_requires_new_prefix_and_same_lineage(tmp_path):
    torch=pytest.importorskip('torch')
    cfg={'schema':m.SCHEMA,'radii':[0,1,2,4,8],'steps_per_rung':10,'operand_limit':1024,
         'max_rung':8,'stage1_checkpoint':'a','text_checkpoint':'b','seed':1,'t00_mode':'exhaustive',
         'model_family':'x','grading':'x','no_hybridlink':True,'no_detector':True,
         'no_numeric_sidecars':True}
    state={'schema':m.SCHEMA,'status':'CERTIFIED_ALL','config':cfg,'config_hash':m._hash(cfg),
           'source_sha256':'sha','frozen_sha256':'frozen','certificates':[{'rung':0,'radius':8}]}
    class Zero:
        @staticmethod
        def restore_lora(bb,lora):bb.value=lora
    path=tmp_path/'old.pt'
    torch.save({'schema':m.SCHEMA,'state':state,'lora':{'x':torch.tensor(2)}},path)
    target=cfg | {'radii':[0,1,2,4,8,16]}
    new=m._new_state(target,'sha','frozen')
    bb=SimpleNamespace(value=None)
    result=m._expand_from(path,new,target,bb,Zero)
    assert result['rung']==0 and result['radius_index']==5
    assert result['new_radius_start']==5 and result['certificates']==state['certificates']
    assert bb.value['x'].item()==2
    with pytest.raises(RuntimeError,match='APPENDED'):
        m._expand_from(path,m._new_state(cfg,'sha','frozen'),cfg,bb,Zero)
    with pytest.raises(RuntimeError,match='LINEAGE'):
        bad=target|{'text_checkpoint':'different'}
        m._expand_from(path,m._new_state(bad,'sha','frozen'),bad,bb,Zero)
    with pytest.raises(RuntimeError,match='BACKBONE_DRIFT'):
        m._expand_from(path,m._new_state(target,'wrong','frozen'),target,bb,Zero)


def test_protected_output_path(tmp_path):
    root=tmp_path/'qwen';root.mkdir()
    with pytest.raises(RuntimeError):m._outputs_safe(root/'child',[root])
    with pytest.raises(RuntimeError):m._outputs_safe(root,[root/'child'])
    other=tmp_path/'else'
    m._outputs_safe(other,[root])


def test_preview_command_writes_scope(tmp_path):
    args=SimpleNamespace(radii='0,1,2',operand_limit=64,max_rung=1,per_cell=2,
         seed=12,t00_mode='exhaustive',steps_per_rung=10,repair_steps=1,batch_size=1,
         replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,max_new_tokens=64,
         stage1_checkpoint=None,text_checkpoint=None,output=str(tmp_path))
    result=m.preview(args)
    assert result['no_model_loaded']
    assert len(result['per_rung'])==6
    assert (tmp_path/'deck_preview.json').is_file()
    assert result['per_rung'][2]['scope']=='exhaustive_ordered_t00'


def test_actual_batched_greedy_lm_head_output_not_teacher_proxy():
    import torch
    from torch import nn
    dim=256
    class Tok:
        pad_token_id=0
        eos_token_id=1
        def __call__(self,text,add_special_tokens=False):
            return {'input_ids':[ord(c)+2 for c in text]}
        def decode(self,ids,skip_special_tokens=True):
            return ''.join(chr(x-2) for x in ids if x>=2)
    class Decoder(nn.Module):
        def forward(self,input_ids=None,attention_mask=None,use_cache=False,return_dict=True):
            return SimpleNamespace(last_hidden_state=torch.nn.functional.one_hot(input_ids,dim).float())
    class BB:
        def __init__(self):
            self.dec=Decoder();self.head=nn.Linear(dim,dim,bias=False)
            with torch.no_grad():
                self.head.weight.zero_()
                sequence='\nANSWER: 0\n'
                for src,dst in zip(sequence,sequence[1:]):
                    self.head.weight[ord(dst)+2,ord(src)+2]=10.
        def get_decoder(self):return self.dec
        def get_output_embeddings(self):return self.head
    bb=BB();tok=Tok()
    responses=m._greedy(bb,tok,['PROBLEM: 8+(-8)','PROBLEM: 64+(-64)'],
                         'cpu',max_tokens=128,max_new_tokens=24)
    assert all(response=='ANSWER: 0\n' for response in responses)
    rows=[{'id':str(i),'source':src,'target':'ANSWER: 0'}
          for i,src in enumerate(['PROBLEM: 8+(-8)','PROBLEM: 64+(-64)'])]
    audit=m._audit(bb,tok,rows,'cpu',128,24,2)
    assert audit['all_pass'] and audit['tested']==2 and audit['passed']==2
    rows[1]['target']='ANSWER: 1'
    audit=m._audit(bb,tok,rows,'cpu',128,24,2)
    assert not audit['all_pass'] and audit['failures'][0]['emitted']=='ANSWER: 0\n'


def test_real_run_budget_halts_resumes_without_refund_and_requires_extension(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('lora_a',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'lora_a':bb.p.detach().cpu().clone()})
    def restore(bb,state):
        with torch.no_grad():bb.p.copy_(state['lora_a'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    def load(args):return FakeBB(),None,'source','frozen',[],zero
    monkeypatch.setattr(m,'_fresh_parent',load)
    def audit(bb,tok,rows,*a,**kw):
        return {'all_pass':False,'tested':len(rows),'passed':0,'total':len(rows),
                'failures':[{'id':rows[0]['id'],'source':rows[0]['source']}],'truncated':False}
    monkeypatch.setattr(m,'_audit',audit)
    monkeypatch.setattr(m,'_audit_retention',lambda *args,**kwargs:{'ok':True,'passed':0,'total':0,'floor':.95,'certificates':[],'failures':[]})
    def training(bb,tok,optimizer,rows,args):
        with torch.no_grad():bb.p.add_(1.)
        return 1.
    monkeypatch.setattr(m,'_train_step',training)
    home=tmp_path/'parents';home.mkdir();(home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    out=tmp_path/'campaign'
    args=SimpleNamespace(radii='0',operand_limit=8,max_rung=0,per_cell=1,
         seed=12,t00_mode='sampled',steps_per_rung=2,repair_steps=1,batch_size=1,
         replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,max_new_tokens=64,
         stage1_checkpoint=str(home/'stage1.pt'),text_checkpoint=str(home/'text.pt'),
         output=str(out),device='cpu',eval_batch=2,fail_limit=10,
         extend_budget=0,warm_start_ladder=None)
    first=m.run(args)
    assert first['status']=='BUDGET_EXHAUSTED' and first['steps_by_rung']['0']==2
    assert m._spent_ledger(out)=={'0':2}
    second=m.run(args)
    assert second['status']=='BUDGET_EXHAUSTED' and second['steps_by_rung']['0']==2
    assert m._spent_ledger(out)=={'0':2}
    args.extend_budget=1
    third=m.run(args)
    assert third['status']=='BUDGET_EXHAUSTED' and third['steps_by_rung']['0']==3
    assert third['budget_by_rung']['0']==3
    assert m._spent_ledger(out)=={'0':3}


def test_fast_probe_stratifies_zero_line_and_radius_without_certification():
    rows,scope=m.deck(0,8,1024)
    sample=m._probe_rows(rows,128)
    assert len(sample)==128
    assert [s['id'] for s in sample]==[s['id'] for s in m._probe_rows(rows,128)]
    assert len(set(s['id'] for s in sample))==128
    assert {s['residual'] for s in sample}==set(range(-8,9))
    for residual in range(-8,9):
        subs=[c for c in sample if c['residual']==residual]
        maximums=[max(abs(v) for v in x['leaves']) for x in subs]
        assert any(mag<=8 for mag in maximums),residual
        assert any(9<=mag<=64 for mag in maximums),residual
        assert any(65<=mag<=1024 for mag in maximums),residual
    assert len(m._probe_rows(rows,512))==512
    assert m._probe_rows(rows,100000)==rows


def test_audit_progress_is_reported_not_implicit_certification(monkeypatch,capsys):
    cases,scope=m.deck(0,0,8)
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*args:["ANSWER: 1\n"]*len(sources))
    grade=m._audit(None,None,cases,'cpu',128,16,4,fail_limit=3,
                   progress_every=5,progress_tag='probe',rung=0,radius=0,
                   record_pass_ids=True)
    assert not grade['all_pass']
    assert grade['tested']==17
    assert grade['passed']==0
    assert len(grade['failures'])==3
    assert grade['passed_ids']==[]
    emitted=capsys.readouterr().out
    assert 'QWEN_LADDER_AUDIT_PROGRESS=' in emitted
    assert '"phase": "probe"' in emitted
    assert '"total": 17' in emitted


def _zero_fork_fixture(tmp_path, *, original_weights=2, working_weights=999):
    """Parent was certified at T00/R0, then trained further at uncertified R1."""
    import torch
    parent_dir=tmp_path/'original';parent_dir.mkdir()
    parent_args=SimpleNamespace(
        radii='0,1,2,4,8',operand_limit=8,max_rung=8,per_cell=1,
        seed=12,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=1,replay_per_batch=0,lr=.001,max_seq=192,
        max_tokens=256,max_new_tokens=64,
        stage1_checkpoint=str(tmp_path/'parents'/'stage1.pt'),
        text_checkpoint=str(tmp_path/'parents'/'text.pt'),
    )
    cfg=m._config(parent_args)
    rows,scope=m._case_bank(cfg,0,0)
    cert={'rung':0,'radius':0,'case_count':len(rows),'deck_sha256':m.deck_fingerprint(rows),
          'scope':scope,'steps_used':50,'deck_config':cfg}
    parent=m._new_state(cfg,'source','frozen')
    parent.update(rung=0,radius_index=1,certificates=[cert])
    parent['steps_by_rung']['0']=100  # 50 further uncertified R1 updates
    torch.save({'schema':m.SCHEMA,'state':parent,'lora':{'lora_a':torch.tensor(float(working_weights))}},parent_dir/'working.pt')
    torch.save({'schema':m.SCHEMA,'status':'CERTIFIED','certificate':cert,
                'lora':{'lora_a':torch.tensor(float(original_weights))},
                'config_hash':m._hash(cfg)},parent_dir/'certified_T00_R0000.pt')
    fork_args=SimpleNamespace(**vars(parent_args))
    fork_args.radii='0'
    return parent_dir,m._config(fork_args),cert,fork_args


def test_zero_fork_imports_certified_weights_not_later_working_weights(tmp_path):
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    class Zero:
        @staticmethod
        def restore_lora(bb,lora):bb.value=lora['lora_a'].item()
    bb=SimpleNamespace(value=None)
    state=m._fork_certified_zero(parent,m._new_state(cfg,'source','frozen'),cfg,bb,Zero)
    assert bb.value==2, 'later uncertified R1 LoRA must never contaminate fork'
    assert state['rung']==1 and state['radius_index']==0
    assert state['certificates']==[cert]
    assert state['steps_by_rung']['0']==50
    assert state['inherited_steps_by_rung']=={'0':50}
    assert state['budget_by_rung']['0']==100
    assert state['steps_by_rung']['1']==0
    assert state['zero_fork']['source'].startswith('certified_T00_R0000.pt')
    assert not (parent/'budget_ledger.jsonl').exists()


def test_zero_fork_rejects_source_mismatch_stale_cert_and_changed_training(tmp_path):
    import torch
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    class Zero:
        @staticmethod
        def restore_lora(bb,lora):bb.value=lora['lora_a'].item()
    bb=SimpleNamespace(value=None)
    def fork(changed_cfg=None,source='source'):
        c=changed_cfg or cfg
        return m._fork_certified_zero(parent,m._new_state(c,source,'frozen'),c,bb,Zero)
    with pytest.raises(RuntimeError,match='MODEL_DRIFT'):fork(source='different')
    with pytest.raises(RuntimeError,match='CONFIG_MISMATCH'):fork(cfg|{'per_cell':2})
    with pytest.raises(RuntimeError,match='REQUIRES_RADIUS_ZERO'):fork(cfg|{'radii':[0,1]})
    with pytest.raises(RuntimeError,match='REQUIRES_RADIUS_ZERO'):fork(cfg|{'max_rung':0})
    saved=torch.load(parent/'certified_T00_R0000.pt',weights_only=True)
    saved['certificate']['deck_sha256']='tampered'
    torch.save(saved,parent/'certified_T00_R0000.pt')
    with pytest.raises(RuntimeError,match='NOT_IN_PARENT_HISTORY'):fork()


def test_zero_fork_rejects_bad_deck_even_if_parent_history_matches(tmp_path):
    import torch
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    snap=torch.load(parent/'certified_T00_R0000.pt',weights_only=True)
    state=torch.load(parent/'working.pt',weights_only=True)
    snap['certificate']['deck_sha256']='tampered'
    state['state']['certificates']=[snap['certificate']]
    torch.save(snap,parent/'certified_T00_R0000.pt')
    torch.save(state,parent/'working.pt')
    with pytest.raises(RuntimeError,match='CERTIFIED_DECK_DRIFT'):
        m._fork_certified_zero(parent,m._new_state(cfg,'source','frozen'),cfg,
            SimpleNamespace(),SimpleNamespace(restore_lora=lambda *args:None))


def test_inherited_budget_ledger_is_monotone_without_refunding(tmp_path):
    assert m._spent_ledger(tmp_path,{'0':50})=={}
    m._charge_step(tmp_path,0,51)
    m._charge_step(tmp_path,1,1)
    m._charge_step(tmp_path,0,52)
    assert m._spent_ledger(tmp_path,{'0':50})=={'0':52,'1':1}
    with pytest.raises(RuntimeError,match='NONMONOTONE'):
        m._spent_ledger(tmp_path)
    with pytest.raises(RuntimeError,match='NONMONOTONE'):
        m._spent_ledger(tmp_path,{'0':49})


def test_fork_run_certifies_only_zero_line_and_restarts(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    parent,cfg,cert,fork_args=_zero_fork_fixture(tmp_path)
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('lora_a',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'lora_a':bb.p.detach().cpu().clone()})
    def restore(bb,state):
        with torch.no_grad():bb.p.copy_(state['lora_a'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    def allpass(bb,tok,rows,*a,**kw):
        return {'all_pass':True,'tested':len(rows),'passed':len(rows),'total':len(rows),
                'failures':[],'truncated':False,'passed_ids':[r['id'] for r in rows]}
    monkeypatch.setattr(m,'_audit',allpass)
    fakeparents=tmp_path/'parents';fakeparents.mkdir()
    (fakeparents/'stage1.pt').write_text('fake');(fakeparents/'text.pt').write_text('fake')
    fork_args.output=str(tmp_path/'fork');fork_args.fork_certified_zero_from=str(parent)
    fork_args.warm_start_ladder=None;fork_args.device='cpu';fork_args.eval_batch=4
    fork_args.fail_limit=64;fork_args.extend_budget=0
    result=m.run(fork_args)
    assert result['status']=='CERTIFIED_ALL'
    assert [(c['rung'],c['radius']) for c in result['certificates']]==[(r,0) for r in range(9)]
    assert result['steps_by_rung']['0']==50
    assert m._spent_ledger(fork_args.output,{'0':50})=={}
    assert len(list((tmp_path/'fork').glob('certified_T*_R0000.pt')))==9
    assert torch.load(tmp_path/'fork'/'certified_T01_R0000.pt',weights_only=True)['lora']['lora_a'].item()==2
    assert m.run(fork_args)['status']=='CERTIFIED_ALL'
    fork_args.fork_certified_zero_from=str(tmp_path/'other')
    with pytest.raises(RuntimeError,match='RESUME_SOURCE_MISMATCH'):
        m.run(fork_args)


def test_retention_progress_has_explicit_phase(monkeypatch,capsys):
    cfg={'operand_limit':8,'per_cell':1,'seed':12,'t00_mode':'exhaustive'}
    cases,scope=m.deck(0,0,8)
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(cases),'deck_config':cfg}
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*args:['ANSWER: 0\n']*len(sources))
    guard=m._audit_retention(None,None,cfg,[cert],SimpleNamespace(device='cpu',
           max_tokens=128,max_new_tokens=16,eval_batch=4,audit_progress_every=5))
    assert guard['ok'] and guard['passed']==17 and guard['total']==17
    text=capsys.readouterr().out
    assert 'QWEN_LADDER_RETENTION_BEGIN=' in text
    assert '"phase": "retention"' in text
    assert '"tested": 17' in text


def test_fork_snapshot_copy_resume_after_interruption(tmp_path):
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    state=m._new_state(cfg,'source','frozen')
    state=m._fork_certified_zero(parent,state,cfg,SimpleNamespace(),
         SimpleNamespace(restore_lora=lambda *args:None))
    out=tmp_path/'branch';out.mkdir()
    m._ensure_local_zero_snapshot(out,state,parent)
    copied=out/'certified_T00_R0000.pt'
    assert copied.read_bytes()==(parent/'certified_T00_R0000.pt').read_bytes()
    copied.unlink()
    with pytest.raises(RuntimeError,match='LOCAL_SNAPSHOT_MISSING'):
        m._ensure_local_zero_snapshot(out,state)
    m._ensure_local_zero_snapshot(out,state,parent)
    copied.write_bytes(b'tampered')
    with pytest.raises(RuntimeError,match='LOCAL_SNAPSHOT_DRIFT'):
        m._ensure_local_zero_snapshot(out,state,parent)


def test_zero_fork_crash_charge_is_not_refunded_on_resume(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    class FakeBB(nn.Module):
        def __init__(self):super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('lora_a',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'lora_a':bb.p.detach().cpu().clone()})
    def restore(bb,lora):
        with torch.no_grad():bb.p.copy_(lora['lora_a'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    calls={'passes':False}
    def auditing(bb,tok,rows,*a,**kw):
        ok=calls['passes']
        return {'all_pass':ok,'tested':len(rows),'passed':len(rows) if ok else 0,
                'total':len(rows),'failures':[] if ok else [{'id':rows[0]['id']}],
                'truncated':False,'passed_ids':[r['id'] for r in rows] if ok else []}
    monkeypatch.setattr(m,'_audit',auditing)
    monkeypatch.setattr(m,'_audit_retention',lambda *a,**kw:{'ok':True,'passed':0,'total':0,'floor':.95,'certificates':[],'failures':[]})
    def interrupt_training(*args):raise KeyboardInterrupt('emulated power loss after charge')
    monkeypatch.setattr(m,'_train_step',interrupt_training)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    args.output=str(tmp_path/'fork');args.fork_certified_zero_from=str(parent)
    args.warm_start_ladder=None;args.device='cpu';args.eval_batch=4;args.fail_limit=64;args.extend_budget=0
    with pytest.raises(KeyboardInterrupt,match='power loss'):
        m.run(args)
    assert m._spent_ledger(args.output,{'0':50})=={'1':1}
    calls['passes']=True
    result=m.run(args)
    assert result['status']=='CERTIFIED_ALL'
    assert result['steps_by_rung']['0']==50
    assert result['steps_by_rung']['1']==1
    assert m._spent_ledger(args.output,{'0':50})=={'1':1}


def test_retention_95_percent_is_ceil_and_per_certificate():
    assert m._required_retained(2049,.95)==1947
    assert m._required_retained(24,.95)==23
    assert m._required_retained(17,.95)==17
    assert m._required_retained(2049,1.)==2049
    assert m._required_retained(24,.90)==22


def test_policy_recorded_on_existing_campaign_and_drift_refused():
    args=SimpleNamespace(retention_floor=.95,local_radius=4,local_repair_steps=10)
    state={'steps_by_rung':{'0':50,'1':850}}
    p=m._record_learning_policy(state,args)
    assert p['strategy']=='commit_then_local_radius_repair_v1'
    assert state['learning_policy_migration']['at_steps_by_rung']=={'0':50,'1':850}
    assert m._record_learning_policy(state,args)==p
    args.retention_floor=.9
    with pytest.raises(RuntimeError,match='POLICY_DRIFT'):
        m._record_learning_policy(state,args)
    for floor in (0,1.001,float('nan')):
        with pytest.raises(ValueError):m._learning_policy(SimpleNamespace(retention_floor=floor))


def test_retention_full_census_95pct_uses_exact_emitted_packets(monkeypatch):
    cases,scope=m.deck(0,0,1024)
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(cases),'deck_config':cfg}
    bad={r['source'] for r in cases[:102]}
    def greedy(bb,tok,sources,*args):
        return ['PROBLEM: 0\n' if src in bad else 'ANSWER: 0\n' for src in sources]
    monkeypatch.setattr(m,'_greedy',greedy)
    args=SimpleNamespace(device='cpu',max_tokens=128,max_new_tokens=16,
        eval_batch=64,fail_limit=64,audit_progress_every=0,
        retention_floor=.95)
    guard=m._audit_retention(None,None,cfg,[cert],args)
    assert guard['ok']
    assert guard['passed']==1947 and guard['total']==2049
    assert guard['certificates'][0]['required']==1947
    assert len(guard['failures'])==64
    assert len(guard['certificates'][0]['failures'])==64
    bad.add(cases[102]['source'])
    guard=m._audit_retention(None,None,cfg,[cert],args)
    assert not guard['ok']
    assert guard['tested']<=2049
    assert guard['certificates'][0]['accuracy_upper_bound']<.95
    assert guard['certificates'][0]['accuracy'] is None or guard['certificates'][0]['accuracy']<.95


def test_local_radius_repair_preserves_residual_and_operand_limit():
    cases,_=m.deck(0,0,1024)
    cases2,_=m.deck(1,0,1024,per_cell=1)
    cfg={'operand_limit':1024,'per_cell':1,'seed':42257,'t00_mode':'exhaustive'}
    cert0={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(cases),'deck_config':cfg}
    cert1={'rung':1,'radius':0,'deck_sha256':m.deck_fingerprint(cases2),'deck_config':cfg}
    boundary=next(row for row in cases if row['leaves']==[-1024,1024])
    example=cases2[0]
    guard={'failures':[{'rung':0,'radius':0,'id':boundary['id']},
                       {'rung':1,'radius':0,'id':example['id']}]}
    local,centers=m._local_radius_rows(cfg,[cert0,cert1],guard,4)
    assert centers==2
    assert len(local)>2
    assert any(x['source']==boundary['source'] for x in local)
    assert any(x['leaves']==[-1023,1023] for x in local)
    assert any(x['rung']==1 for x in local)
    assert len(set((x['source'],x['target']) for x in local))==len(local)
    for row in local:
        assert row['residual']==0
        assert sum(row['leaves'])==0
        assert all(abs(v)<=1024 for v in row['leaves'])
        partial=0
        for v in row['leaves']:
            partial+=v
            assert abs(partial)<=1024


def test_commit_then_local_repair_does_not_reject_retention_regression(tmp_path,monkeypatch,capsys):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    # Make a miniature fork with the same real certificate invariants, but a
    # two-step T01 budget: one acquisition update + one post-commit local repair.
    parent_obj=torch.load(parent/'working.pt',weights_only=True)
    snap=torch.load(parent/'certified_T00_R0000.pt',weights_only=True)
    parent_cfg=dict(parent_obj['state']['config'],steps_per_rung=2)
    parent_obj['state']['config']=parent_cfg
    parent_obj['state']['config_hash']=m._hash(parent_cfg)
    parent_obj['state']['steps_by_rung']['0']=0
    parent_obj['state']['budget_by_rung']={str(i):2 for i in range(9)}
    snap['certificate']['steps_used']=0
    snap['certificate']['deck_config']=parent_cfg
    snap['config_hash']=m._hash(parent_cfg)
    parent_obj['state']['certificates']=[snap['certificate']]
    torch.save(snap,parent/'certified_T00_R0000.pt')
    torch.save(parent_obj,parent/'working.pt')
    args.steps_per_rung=2;args.radii='0';args.repair_steps=1
    args.local_repair_steps=1;args.local_radius=4;args.retention_floor=.95
    args.output=str(tmp_path/'new_fork');args.fork_certified_zero_from=str(parent)
    args.warm_start_ladder=None;args.device='cpu';args.eval_batch=4
    args.fail_limit=64;args.extend_budget=0
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('lora_a',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'lora_a':bb.p.detach().cpu().clone()})
    def restore(bb,state):
        with torch.no_grad():bb.p.copy_(state['lora_a'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    monkeypatch.setattr(m,'_fresh_parent',lambda a:(FakeBB(),None,'source','frozen',[],zero))
    def fake_audit(bb,tok,rows,*a,**kw):
        is_t00=rows[0]['rung']==0
        bad=(2 if is_t00 and bb.p.item()>2 else (len(rows) if not is_t00 else 0))
        passed=len(rows)-bad
        return {'all_pass':passed==len(rows),'tested':len(rows),'passed':passed,
                'total':len(rows),'truncated':False,
                'failures':[{'id':row['id'],'source':row['source'],
                             'expected':row['target'],'emitted':'PROBLEM: 0\n'}
                            for row in rows[:bad]],
                'passed_ids':[row['id'] for row in rows[bad:]]}
    monkeypatch.setattr(m,'_audit',fake_audit)
    training_batches=[]
    def step(bb,tok,optimizer,rows,args):
        training_batches.append([r['rung'] for r in rows])
        with torch.no_grad():bb.p.add_(1.)
        return 1.
    monkeypatch.setattr(m,'_train_step',step)
    result=m.run(args)
    assert result['status']=='BUDGET_EXHAUSTED'
    assert result['steps_by_rung']['1']==2
    assert result['last_retention']['ok'] is False
    assert result['last_retention']['passed']==15
    assert result['last_committed_probe']['passed']==0
    assert m._spent_ledger(args.output,{'0':0})=={'1':2}
    assert len(training_batches)==2
    assert training_batches[0].count(1)>=1
    assert training_batches[1].count(0)>=1
    saved=torch.load(tmp_path/'new_fork'/'working.pt',weights_only=True)
    assert saved['lora']['lora_a'].item()==4.,'neither step may be rolled back'
    output=capsys.readouterr().out
    assert 'QWEN_LADDER_COMMIT=' in output and 'QWEN_LADDER_LOCAL_REPAIR=' in output
    assert 'REJECTED_RETENTION_REGRESSION' not in output
    assert m.run(args)['status']=='BUDGET_EXHAUSTED'


def _schedule_resume_fixture(tmp_path, radii='0', status='READY',
                             rung=1, radius_index=0, certified=None):
    """Standalone persisted campaign; no CUDA and no Qwen downloads."""
    import torch
    args=SimpleNamespace(radii=radii,operand_limit=8,max_rung=8,per_cell=1,
        seed=12,t00_mode='exhaustive',steps_per_rung=1000,repair_steps=25,
        batch_size=4,replay_per_batch=1,lr=.001,max_seq=192,
        max_tokens=256,max_new_tokens=64,
        stage1_checkpoint=str(tmp_path/'stage1.pt'),
        text_checkpoint=str(tmp_path/'text.pt'))
    cfg=m._config(args)
    if certified is None:
        rows,scope=m._case_bank(cfg,0,0)
        certified=[{'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(rows),
                    'case_count':len(rows),'scope':scope,'steps_used':50,
                    'deck_config':cfg}]
    state=m._new_state(cfg,'source','frozen')
    state.update(status=status,rung=rung,radius_index=radius_index,
                 certificates=certified)
    state['steps_by_rung']['0']=50
    state['steps_by_rung']['1']=725
    state['learning_policy']={'strategy':'commit_then_local_radius_repair_v1',
               'retention_floor':.95,'local_radius':4,'local_repair_steps':10}
    opt={'state':{0:{'step':torch.tensor(42.),'exp_avg':torch.tensor([17.])}},
         'param_groups':[{'params':[0],'lr':.001}]}
    obj={'schema':m.SCHEMA,'state':state,'config':cfg,
         'lora':{'p':torch.tensor(19.)},'optimizer':opt}
    out=tmp_path/'campaign';out.mkdir()
    torch.save(obj,out/'working.pt')
    m._atomic_json(out/'progress.json',state)
    class Zero:
        @staticmethod
        def restore_lora(bb,lora):bb.weight=lora['p'].item()
    return out,cfg,state,opt,Zero


def test_inplace_radii_change_keeps_active_t01_and_optimizer_and_certificates(tmp_path,capsys):
    import torch
    out,cfg,old,opt,zero=_schedule_resume_fixture(tmp_path)
    updated=cfg | {'radii':[0,1,4]}
    bb=SimpleNamespace(weight=None)
    state,optimizer=m._load_resume(out,updated,bb,zero,'source','frozen')
    assert bb.weight==19
    assert (state['rung'],state['radius_index'])==(1,0), 'T01/R0 must stay active'
    assert state['steps_by_rung']['1']==725 and state['steps_by_rung']['0']==50
    assert state['budget_by_rung']['1']==1000
    assert state['certificates']==old['certificates']
    assert state['certificates'][0]['deck_config']['radii']==[0]
    assert state['config_hash']==m._hash(updated)
    assert state['radius_schedule_history'][-1]['from']==[0]
    assert state['radius_schedule_history'][-1]['to']==[0,1,4]
    assert optimizer['state'][0]['step'].item()==42.
    stored=torch.load(out/'working.pt',map_location='cpu',weights_only=True)
    assert stored['lora']['p'].item()==19.
    assert stored['optimizer']['state'][0]['exp_avg'].item()==17.
    assert stored['config']==updated
    assert json.loads((out/'progress.json').read_text())['config']==updated
    assert 'QWEN_LADDER_RADIUS_SCHEDULE_CHANGED=' in capsys.readouterr().out
    assert m._first_unfinished_target(updated,state['certificates'])==(0,1)
    # Identical resume does not create an extra migration event.
    again,_=m._load_resume(out,updated,bb,zero,'source','frozen')
    assert len(again['radius_schedule_history'])==1


def test_removing_active_radius_retargets_without_discarding_old_certification(tmp_path):
    out,cfg,state,opt,zero=_schedule_resume_fixture(
        tmp_path,radii='0,1,4',rung=1,radius_index=1)
    cert=state['certificates'][0]
    state['certificates'].append({**cert,'rung':0,'radius':1})
    state['certificates'].append({**cert,'rung':1,'radius':0})
    # This test checks navigation; the certificates are not regraded here.
    import torch
    saved=torch.load(out/'working.pt',weights_only=True)
    saved['state']=state
    torch.save(saved,out/'working.pt')
    new=cfg|{'radii':[0,4]}
    migrated,_=m._load_resume(out,new,SimpleNamespace(),zero,'source','frozen')
    assert (migrated['rung'],new['radii'][migrated['radius_index']])==(0,4)
    assert len(migrated['certificates'])==3, 'removed radii remain historical retention guards'
    assert migrated['steps_by_rung']['1']==725


def test_completed_campaign_reopens_for_added_radius_but_uses_same_budget(tmp_path):
    out,cfg,state,opt,zero=_schedule_resume_fixture(tmp_path,status='CERTIFIED_ALL',
                                                  rung=9,radius_index=0)
    # Simplified complete campaign fixture: every zero-radius rung is certified.
    base=state['certificates'][0]
    state['certificates']=[{**base,'rung':i} for i in range(9)]
    import torch
    saved=torch.load(out/'working.pt',weights_only=True)
    saved['state']=state
    torch.save(saved,out/'working.pt')
    new=cfg|{'radii':[0,1,4]}
    migrated,_=m._load_resume(out,new,SimpleNamespace(),zero,'source','frozen')
    assert migrated['status']=='READY'
    assert (migrated['rung'],new['radii'][migrated['radius_index']])==(0,1)
    assert migrated['steps_by_rung']['0']==50
    assert migrated['budget_by_rung']['0']==1000
    assert len(migrated['certificates'])==9


def test_schedule_targets_previous_missing_radius_after_finishing_active_rung():
    cfg={'radii':[0,1,4],'max_rung':2}
    done=[{'rung':0,'radius':0}]
    assert m._first_unfinished_target(cfg,done,preferred=(1,0))==(1,0)
    done.append({'rung':1,'radius':0})
    assert m._first_unfinished_target(cfg,done)==(0,1)
    done.extend([{'rung':0,'radius':1},{'rung':0,'radius':4}])
    assert m._first_unfinished_target(cfg,done)==(1,1)
    done.extend([{'rung':1,'radius':1},{'rung':1,'radius':4},
                 {'rung':2,'radius':0},{'rung':2,'radius':1},
                 {'rung':2,'radius':4}])
    assert m._first_unfinished_target(cfg,done) is None


def test_radius_schedule_resume_rejects_non_radius_drift_and_bad_lineage(tmp_path):
    out,cfg,old,opt,zero=_schedule_resume_fixture(tmp_path)
    with pytest.raises(RuntimeError,match='RESUME_CONFIG_DRIFT'):
        m._load_resume(out,cfg|{'radii':[0,1],'operand_limit':16},
                       SimpleNamespace(),zero,'source','frozen')
    with pytest.raises(RuntimeError,match='RESUME_MODEL_DRIFT'):
        m._load_resume(out,cfg|{'radii':[0,1]},SimpleNamespace(),zero,'wrong','frozen')
    import torch
    unchanged=torch.load(out/'working.pt',weights_only=True)
    assert unchanged['state']['config']['radii']==[0]
    unchanged['state']['certificates'][0].pop('deck_config')
    torch.save(unchanged,out/'working.pt')
    with pytest.raises(RuntimeError,match='NEEDS_CERTIFICATE_DECK_CONFIG'):
        m._load_resume(out,cfg|{'radii':[0,1]},SimpleNamespace(),zero,'source','frozen')


def test_retention_progress_log_reduced_80_percent_for_same_cli_interval(monkeypatch,capsys):
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    cases,scope=m.deck(0,0,1024)
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(cases),'deck_config':cfg}
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*a:['ANSWER: 0\n']*len(sources))
    args=SimpleNamespace(device='cpu',max_tokens=128,max_new_tokens=16,
                         eval_batch=8,fail_limit=64,audit_progress_every=8,
                         retention_floor=.95)
    result=m._audit_retention(None,None,cfg,[cert],args)
    stdout=capsys.readouterr().out
    lines=[line for line in stdout.splitlines()
           if line.startswith('QWEN_LADDER_AUDIT_PROGRESS=')]
    assert result['ok'] and result['passed']==2049
    assert 49<=len(lines)<=53, f'got {len(lines)} vs 257 prior progress lines'
    assert '"tested": 2049' in lines[-1]
    assert 'QWEN_LADDER_RETENTION=' in stdout


def test_zero_fork_still_reads_original_certificate_after_parent_radius_edit(tmp_path):
    import torch
    parent,cfg,cert,args=_zero_fork_fixture(tmp_path)
    bb=SimpleNamespace(value=None)
    zero=SimpleNamespace(restore_lora=lambda bb,lora:setattr(bb,'value',lora['lora_a'].item()))
    parent_checkpoint=torch.load(parent/'working.pt',weights_only=True)
    old_hash=parent_checkpoint['state']['config_hash']
    new_cfg=parent_checkpoint['state']['config']|{'radii':[0,1,4]}
    parent_checkpoint['state']['config']=new_cfg
    parent_checkpoint['state']['config_hash']=m._hash(new_cfg)
    parent_checkpoint['config']=new_cfg
    torch.save(parent_checkpoint,parent/'working.pt')
    assert old_hash!=m._hash(new_cfg)
    # The original immutable certified_T00_R0000.pt has the original hash.
    assert torch.load(parent/'certified_T00_R0000.pt',weights_only=True)['config_hash']==old_hash
    state=m._fork_certified_zero(parent,m._new_state(cfg,'source','frozen'),cfg,bb,zero)
    assert bb.value==2
    assert state['certificates']==[cert]


def test_sampled_progress_rotates_stratified_disjoint_large_deck():
    rows,_=m.deck(0,1,1024)
    groups=m._sampled_progress_rows(rows,128,2,('r1',0))
    assert [len(g) for g in groups]==[128,128]
    assert not {x['id'] for x in groups[0]} & {x['id'] for x in groups[1]}
    for group in groups:
        assert {r['residual'] for r in group}=={-1,0,1}
        assert all(abs(r['residual'])<=1 for r in group)
    repeat=m._sampled_progress_rows(rows,128,2,('r1',0))
    assert [[r['id'] for r in g] for g in groups]==[[r['id'] for r in g] for g in repeat]
    changed=m._sampled_progress_rows(rows,128,2,('r1',1))
    assert [r['id'] for r in groups[0]] != [r['id'] for r in changed[0]]


def test_sampled_advance_95_percent_is_per_sample_not_pooled(monkeypatch):
    rows,_=m.deck(0,1,1024)
    args=SimpleNamespace(device='cpu',max_tokens=128,max_new_tokens=32,
                         eval_batch=8,fail_limit=64,audit_progress_every=0,
                         probe_cases=128,advance_mode='sampled',
                         advance_accuracy=.95,advance_probes=2)
    counts=[]
    def audit(bb,tok,items,*other,**kw):
        index=len(counts)
        counts.append(len(items))
        # First group succeeds (128/128), second fails 7 (121/128).
        passed=len(items)-(7 if index==1 else 0)
        return {'all_pass':passed==len(items),'tested':len(items),'passed':passed,
                'total':len(items), 'truncated':False,
                'failures':[{'id':r['id']} for r in items[:len(items)-passed]]}
    monkeypatch.setattr(m,'_audit',audit)
    grade=m._sampled_progress_grade(None,None,rows,args,0,1,0,'test')
    assert grade['passed']==249 and grade['total']==256
    assert grade['accuracy']>.95 and not grade['readiness']
    assert counts==[128,128]


def test_advancement_policy_switch_preserves_current_unfinished_target():
    cfg={'radii':[0,1,4],'max_rung':1,'steps_per_rung':10000}
    s=m._new_state(cfg,'source','frozen')
    s['rung']=1;s['radius_index']=0;s['steps_by_rung']['1']=2650
    s['certificates']=[{'rung':0,'radius':0}]
    mode=SimpleNamespace(advance_mode='sampled',advance_accuracy=.95,advance_probes=2)
    result=m._record_advancement_policy(s,mode)
    assert result['mode']=='sampled'
    assert (s['rung'],cfg['radii'][s['radius_index']])==(1,0)
    assert s['steps_by_rung']['1']==2650
    assert len(s['certificates'])==1
    assert s['provisional']==[]
    # When the active target was completed, use radius-first progression.
    s['provisional']=[{'rung':1,'radius':0}]
    assert m._first_unfinished_target(cfg,s['certificates'],
        provisional=s['provisional'],radius_first=True)==(0,1)


def test_sampled_mode_advances_without_exhaustive_current_decks(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    measured=[]
    def audit(bb,tok,items,*other,**kw):
        measured.append((len(items),kw.get('progress_tag')))
        assert len(items)<=8, 'a large full-deck audit was accidentally launched'
        return {'all_pass':True,'passed':len(items),'tested':len(items),
                'total':len(items),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',audit)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    out=tmp_path/'campaign'
    args=SimpleNamespace(radii='0,1',operand_limit=64,max_rung=1,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'stage1.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(out),device='cpu',
        eval_batch=8,fail_limit=10,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,audit_progress_every=0,retention_floor=.95,
        local_radius=4,local_repair_steps=10,retention_sample_cases=8)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert [(p['rung'],p['radius']) for p in state['provisional']]==[
        (0,0),(1,0),(0,1),(1,1)]
    assert all(p['provisional'] for p in state['provisional'])
    assert not state['certificates']
    assert all(x==0 for x in state['steps_by_rung'].values())
    assert max(length for length,_ in measured)==8
    saved=torch.load(out/'working.pt',weights_only=True)
    assert len(saved['state']['provisional'])==4
    again=m.run(args)
    assert again['status']=='PROVISIONAL_ALL'
    # Exact mode explicitly ignores provisional milestones and returns to
    # exhaustive acquisition, instead of silently certifying samples.
    args.advance_mode='exact'
    new=m._record_advancement_policy(again,args)
    assert new['mode']=='exact'
    assert again['status']=='READY'
    assert (again['rung'],again['radius_index'])==(0,0)


def test_retention_sample_proportional_stratified_rotates_and_does_not_repeat_same_slice():
    rows,_=m.deck(0,1,1024)
    fingerprint=m.deck_fingerprint(rows)
    a=m._retention_sample_rows(rows,128,fingerprint,0)
    b=m._retention_sample_rows(rows,128,fingerprint,1)
    assert len(a)==len(b)==128
    assert a==m._retention_sample_rows(rows,128,fingerprint,0)
    assert len({x['id'] for x in a})==128
    assert len({x['id'] for x in b})==128
    assert {x['residual'] for x in a}=={-1,0,1}
    assert {x['residual'] for x in b}=={-1,0,1}
    assert {x['id'] for x in a}!={x['id'] for x in b}
    assert m._retention_sample_rows(rows,9000,fingerprint,2)==rows


def test_retention_samples_keep_95_percent_and_known_fault_replay_unbiased(monkeypatch):
    cfg={'operand_limit':128,'per_cell':2,'seed':42257,'t00_mode':'exhaustive'}
    rows,scope=m.deck(0,0,128)
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(rows),'deck_config':cfg}
    key=f"0:0:{cert['deck_sha256']}"
    state={'retention_known_failures':{}}
    args=SimpleNamespace(retention_floor=.95,retention_sample_cases=32,
        retention_known_failure_cases=8,retention_full_every=10,
        device='cpu',max_tokens=256,max_new_tokens=64,eval_batch=8,
        fail_limit=64,audit_progress_every=0)
    fault_id=rows[-1]['id']
    observed=[]
    def fake_audit(bb,tok,items,*pos,**kw):
        observed.append((kw['progress_tag'],[x['id'] for x in items]))
        failures=[{'id':x['id'],'source':x['source'],'expected':x['target'],
                   'emitted':'ANSWER: 12345','parsed':'ANSWER: 12345'}
                  for x in items if x['id']==fault_id]
        return {'all_pass':not failures,'tested':len(items),
                'passed':len(items)-len(failures),'total':len(items),
                'failures':failures,'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    # Full audit first: an actual failure is discovered and kept by case ID.
    full=m._audit_retention(None,None,cfg,[cert],args,state=state)
    assert full['certificates'][0]['sample_only'] is False
    assert state['retention_known_failures'][key]==[fault_id]
    assert full['certificates'][0]['passed']==256
    # Pick a rotating sample that excludes the known failure. Replay should
    # still check the known fault but NOT count it in the statistical score.
    attempt=next(i for i in range(50) if fault_id not in
        {r['id'] for r in m._retention_sample_rows(rows,32,cert['deck_sha256'],i)})
    observed.clear()
    sampled=m._audit_retention(None,None,cfg,[cert],args,sample_cases=32,
                               attempt=attempt,state=state)
    entry=sampled['certificates'][0]
    assert entry['sample_only'] and entry['passed']==entry['total']==32
    assert entry['accuracy']==1.0 and entry['ok']
    assert entry['known_failure_tested']==1
    assert entry['known_failure_failed']==1
    assert len(sampled['failures'])==1
    assert [len(ids) for phase,ids in observed]==[32,1]
    assert observed[1][0]=='known_failure'
    assert state['retention_known_failures'][key]==[fault_id]
    # Resume can use exactly the stored ID list with no model-state reset.
    recovered={'retention_known_failures':dict(state['retention_known_failures'])}
    repeated=m._audit_retention(None,None,cfg,[cert],args,sample_cases=32,
                                  attempt=attempt,state=recovered)
    assert repeated['certificates'][0]['known_failure_tested']==1


def test_retention_known_failure_bank_bounded_and_detects_tampering(monkeypatch):
    cfg={'operand_limit':64,'per_cell':2,'seed':42257,'t00_mode':'exhaustive'}
    rows,_=m.deck(0,0,64)
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(rows),'deck_config':cfg}
    key=f"0:0:{cert['deck_sha256']}"
    args=SimpleNamespace(retention_floor=.95,retention_known_failure_cases=3,
        device='cpu',max_tokens=128,max_new_tokens=32,eval_batch=8,
        fail_limit=64,audit_progress_every=0)
    state={}
    def fake_audit(bb,tok,items,*a,**kw):
        fails=[{'id':x['id']} for x in items[:5]]
        return {'passed':len(items)-len(fails),'tested':len(items),
                'total':len(items),'failures':fails,'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    m._audit_retention(None,None,cfg,[cert],args,state=state)
    assert len(state['retention_known_failures'][key])==3
    state['retention_known_failures'][key].append('a-stale-or-invented-id')
    with pytest.raises(RuntimeError,match='FAILURE_BANK_DECK_DRIFT'):
        m._audit_retention(None,None,cfg,[cert],args,sample_cases=16,state=state)


def test_periodic_retention_every_tenth_provisional_or_opt_out():
    s={'provisional':[]}
    a=SimpleNamespace(retention_full_every=10)
    assert not m._periodic_retention_due(s,a)
    s['provisional']=[{}]*8
    assert not m._periodic_retention_due(s,a)
    s['provisional']=[{}]*9
    assert m._periodic_retention_due(s,a)
    s['provisional']=[{}]*10
    assert not m._periodic_retention_due(s,a)
    a.retention_full_every=0
    s['provisional']=[{}]*9
    assert not m._periodic_retention_due(s,a)
    for key in ('retention_sample_cases','retention_full_every','retention_known_failure_cases'):
        args=SimpleNamespace(**{key:-1})
        with pytest.raises(ValueError,match='BAD_RETENTION_SAMPLING_POLICY'):
            m._retention_settings(args)


def test_sampled_milestone_uses_limited_history_even_for_certified_case(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    observed=[]
    def audit(bb,tok,items,*a,**kw):
        observed.append((kw.get('progress_tag'),len(items)))
        return {'all_pass':True,'passed':len(items),'tested':len(items),
                'total':len(items),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',audit)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    args=SimpleNamespace(radii='0,1',operand_limit=64,max_rung=0,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'stage1.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(tmp_path/'campaign'),device='cpu',
        eval_batch=8,fail_limit=10,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,retention_sample_cases=8,retention_full_every=0,
        audit_progress_every=0,retention_floor=.95,local_radius=4,
        local_repair_steps=10)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert [x['retention_scope'] for x in state['provisional']]==['sampled','sampled']
    assert max(n for phase,n in observed if phase=='retention')==8
    # Reopen in place: changing the sampling budget is mutable campaign
    # policy and is not mistaken for a model/deck configuration drift.
    args.retention_sample_cases=32
    second=m.run(args)
    assert second['status']=='PROVISIONAL_ALL'
    assert second['steps_by_rung']==state['steps_by_rung']


def test_periodic_full_retention_census_on_second_milestone(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    observed=[]
    def fake_audit(bb,tok,rows,*other,**kw):
        observed.append((kw.get('progress_tag'),len(rows)))
        return {'all_pass':True,'passed':len(rows),'tested':len(rows),
                'total':len(rows),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    args=SimpleNamespace(radii='0,1',operand_limit=64,max_rung=0,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'stage1.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(tmp_path/'campaign'),device='cpu',
        eval_batch=8,fail_limit=10,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,retention_sample_cases=8,retention_full_every=2,
        audit_progress_every=0,retention_floor=.95,local_radius=4,
        local_repair_steps=10)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert [x['retention_scope'] for x in state['provisional']]==['sampled','periodic_full']
    full=[size for phase,size in observed if phase=='retention' and size>8]
    assert full==[129], observed
    # Full historical audit did not turn a provisional milestone into a cert.
    assert state['certificates']==[]
    again=m.run(args)
    assert len(again['provisional'])==2
    assert len([size for phase,size in observed if phase=='retention' and size>8])==1


def test_gate_tripwire_stops_when_max_errors_exceeded_and_projects_full_deck(monkeypatch,capsys):
    # Exactly two errors permitted in 50 at 95%; the third error trips.
    rows=[{'id':str(i),'source':f'PROBLEM: {i}', 'target':'ANSWER: 0'}
          for i in range(50)]
    seen=[]
    def greedy(bb,tok,sources,*others):
        seen.extend(sources)
        return ['ANSWER: 1\n' if int(src.split()[-1]) in (1,8,12,31) else
                'ANSWER: 0\n' for src in sources]
    monkeypatch.setattr(m,'_greedy',greedy)
    grade=m._audit(None,None,rows,'cpu',128,16,4,min_passed=48,
                   fail_limit=2,progress_tag='retention',rung=0,radius=0,
                   population_total=2049)
    assert grade['truncated'] and grade['tripwire_tripped']
    assert grade['allowed_errors']==2 and grade['tested']==13
    assert grade['passed']==10 and len(grade['failures'])==2
    assert grade['error_projection']['observed_errors']==3
    assert grade['error_projection']['population_total']==2049
    assert grade['error_projection']['estimated_total_errors']>3
    assert grade['error_projection']['estimate_is_exact'] is False
    emitted=capsys.readouterr().out
    assert 'QWEN_LADDER_GATE_TRIPWIRE=' in emitted
    assert '"gate_failed": true' in emitted
    assert '"audit_complete": false' in emitted
    # A periodic full gate MUST NOT stop after the same threshold.
    complete=m._audit(None,None,rows,'cpu',128,16,4,fail_limit=2,
                       progress_tag='periodic_gate_full',population_total=50)
    assert complete['tested']==50 and not complete['truncated']
    assert complete['error_projection']['estimated_total_errors']==4
    assert complete['error_projection']['estimate_is_exact']


def test_sampled_acquisition_tripwire_preserves_planned_denominators(monkeypatch):
    rows,_=m.deck(1,0,128,per_cell=8)
    args=SimpleNamespace(device='cpu',max_tokens=128,max_new_tokens=32,
        eval_batch=4,fail_limit=64,audit_progress_every=0,
        probe_cases=128,advance_mode='sampled',advance_accuracy=.95,advance_probes=2)
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*other:
                        ['ANSWER: 777\n']*len(sources))
    grade=m._sampled_progress_grade(None,None,rows,args,1,0,0,'test')
    assert grade['truncated'] and grade['tripwire_tripped']
    assert not grade['readiness']
    # 24-case samples need 23 successes, so each stops after 2 failures.
    assert grade['tested']==4 and grade['total']==48 and grade['passed']==0
    assert len(grade['failures'])==4
    assert all(x['tripwire_tripped'] and x['allowed_errors']==1
               for x in grade['per_sample'])
    assert grade['estimated_current_deck_errors']>4


def test_retention_tripwire_reports_projected_full_deck_and_periodic_forces_full(monkeypatch):
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    rows,_=m.deck(0,0,1024)
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(rows),
          'deck_config':cfg}
    args=SimpleNamespace(retention_floor=.95,retention_known_failure_cases=0,
        device='cpu',max_tokens=128,max_new_tokens=32,eval_batch=8,
        fail_limit=64,audit_progress_every=0)
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*other:
                        ['ANSWER: 777\n']*len(sources))
    sample=m._audit_retention(None,None,cfg,[cert],args,sample_cases=128,attempt=2)
    entry=sample['certificates'][0]
    assert not entry['ok'] and entry['tripwire_tripped']
    assert entry['allowed_errors']==6 and entry['tested']==7
    assert entry['error_projection']['population_total']==2049
    assert entry['error_projection']['estimated_total_errors']>1000
    assert not entry['complete'] and entry['accuracy'] is None
    # Every Nth provisional advancement performs a real full census, even
    # when it is obvious the requested accuracy cannot be met.
    periodic=m._audit_retention(None,None,cfg,[cert],args,
                                sample_cases=None,force_complete=True)
    full=periodic['certificates'][0]
    assert not full['ok'] and not full['tripwire_tripped']
    assert full['tested']==2049 and full['complete']
    assert full['error_projection']['estimated_total_errors']==2049
    assert full['error_projection']['estimate_is_exact']


def test_gate_full_every_option_and_projection_edge_cases():
    assert m._gate_full_interval(SimpleNamespace())==10
    assert m._gate_full_interval(SimpleNamespace(gate_full_every=0))==0
    assert m._gate_full_interval(SimpleNamespace(gate_full_every=3))==3
    with pytest.raises(ValueError,match='BAD_GATE_FULL_INTERVAL'):
        m._gate_full_interval(SimpleNamespace(gate_full_every=-1))
    with pytest.raises(ValueError,match='INVALID_ERROR_PROJECTION'):
        m._project_errors(4,5,9)
    assert m._project_errors(0,0,9)['estimated_total_errors']==4.5
    assert m._project_errors(9,4,9)['estimated_total_errors']==4


def test_periodic_current_gate_checks_full_deck_every_second_readiness_and_persists(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    audited=[]
    def fake_audit(bb,tok,rows,*other,**kw):
        audited.append((kw.get('progress_tag'),len(rows),kw.get('min_passed')))
        return {'all_pass':True,'passed':len(rows),'tested':len(rows),
                'total':len(rows),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    args=SimpleNamespace(radii='0,1',operand_limit=64,max_rung=0,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'stage1.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(tmp_path/'campaign'),device='cpu',
        eval_batch=8,fail_limit=10,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,retention_sample_cases=8,retention_full_every=0,
        gate_full_every=2,audit_progress_every=0,retention_floor=.95,
        local_radius=4,local_repair_steps=10)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert state['readiness_gate_count']==2
    full=[x for x in audited if x[0]=='periodic_gate_full']
    assert len(full)==1 and full[0][1]==385 and full[0][2] is None
    assert state['last_periodic_gate']['tested']==385
    again=m.run(args)
    assert again['readiness_gate_count']==2
    assert len([x for x in audited if x[0]=='periodic_gate_full'])==1


def test_periodic_current_gate_failure_feeds_training_instead_of_empty_failure_bank(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'source','frozen',[],zero))
    def fake_audit(bb,tok,rows,*other,**kw):
        if kw.get('progress_tag')=='periodic_gate_full':
            fails=[{'id':r['id'],'source':r['source']} for r in rows[:30]]
            return {'all_pass':False,'passed':len(rows)-len(fails),
                'tested':len(rows),'total':len(rows),'failures':fails,'truncated':False}
        return {'all_pass':True,'passed':len(rows),'tested':len(rows),
                'total':len(rows),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    monkeypatch.setattr(m,'_train_step',lambda *args,**kw:0.0)
    home=tmp_path/'parents';home.mkdir()
    (home/'stage1.pt').write_text('fake');(home/'text.pt').write_text('fake')
    args=SimpleNamespace(radii='0,1',operand_limit=64,max_rung=0,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=1,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'stage1.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(tmp_path/'campaign'),device='cpu',
        eval_batch=8,fail_limit=64,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,retention_sample_cases=8,retention_full_every=0,
        gate_full_every=2,audit_progress_every=0,retention_floor=.95,
        local_radius=4,local_repair_steps=1)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert state['last_periodic_gate']['ok'] is False
    assert state['last_committed_probe']['kind']=='sampled'
    assert state['steps_by_rung']['0']==1
    assert len(state['provisional'])==2


def _adaptive_args(**kwargs):
    defaults=dict(adaptive_repair=True,adaptive_check_min=5,adaptive_check_max=100,
        adaptive_repair_min=2,adaptive_repair_max=20,adaptive_harvest_cases=128,
        adaptive_failure_target=8,adaptive_full_steps=1000,repair_steps=25,
        local_repair_steps=10,device='cpu',eval_batch=4,fail_limit=64,
        max_tokens=256,max_new_tokens=64,retention_known_failure_cases=32)
    return SimpleNamespace(**(defaults|kwargs))


def test_adaptive_policy_is_opt_in_mutable_and_checkpoint_safe():
    state=m._new_state({'max_rung':1,'radii':[0,1],'steps_per_rung':1000},'s','f')
    assert m._adaptive_control(state,_adaptive_args(adaptive_repair=False)) is None
    control=m._adaptive_control(state,_adaptive_args())
    target=m._adaptive_target(control,control['policy'],1,0,_adaptive_args())
    assert target['check_steps']==25 and target['repair_steps']==10
    target['check_steps']=13
    new=m._adaptive_control(state,_adaptive_args(adaptive_check_max=60))
    assert new['targets']['1:0']['check_steps']==13
    assert len(new['policy_history'])==1
    assert state['config_hash']==m._hash(state['config'])
    import json
    json.dumps(state)
    assert m._adaptive_control(state,_adaptive_args(adaptive_repair=False)) is None
    assert state['adaptive_controller']['targets']['1:0']['check_steps']==13
    with pytest.raises(ValueError,match='ADAPTIVE_CONTROLLER'):
        m._adaptive_policy(_adaptive_args(adaptive_check_min=101))


def test_adaptive_feedback_shortens_interventions_when_retention_fails():
    args=_adaptive_args()
    policy=m._adaptive_policy(args)
    state=m._new_state({'max_rung':1,'radii':[0],'steps_per_rung':1000},'s','f')
    control=m._adaptive_control(state,args)
    target=m._adaptive_target(control,policy,1,0,args)
    decision=m._adaptive_decide(target,policy,.25,.90,{'ok':False},{'ok':False},10)
    assert decision['reason']=='retention_below_floor'
    assert target['check_steps']==13 and target['repair_steps']==15
    decision=m._adaptive_decide(target,policy,.9,1.,{'ok':False},{'ok':True},10)
    assert decision['reason']=='near_acquisition_frontier'
    assert target['check_steps']==7 and target['repair_steps']==14
    decision=m._adaptive_decide(target,policy,.2,.35,{'ok':True},{'ok':True},0)
    assert decision['reason']=='stable_retention_extend_acquisition'
    assert target['check_steps']==10


def test_adaptive_harvest_finishes_small_deck_and_preserves_original_gate_score(monkeypatch):
    rows,_=m.deck(2,0,1024,per_cell=8)
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    cert={'rung':2,'radius':0,'deck_sha256':m.deck_fingerprint(rows),'deck_config':cfg}
    first=rows[0]
    guard={'ok':False,'passed':0,'tested':2,'total':24,
           'failures':[{'rung':2,'radius':0,'id':first['id'],
                        'source':first['source'],'expected':first['target']}],
           'certificates':[{'rung':2,'radius':0,'ok':False}]}
    seen=[]
    def fake_greedy(bb,tok,sources,*args):
        seen.extend(sources)
        result=[]
        by_source={r['source']:r for r in rows}
        for source in sources:
            row=by_source[source]
            result.append('ANSWER: 999' if row in rows[:10] else row['target'])
        return result
    monkeypatch.setattr(m,'_greedy',fake_greedy)
    state={'retention_known_failures':{},'attempts':0}
    args=_adaptive_args(adaptive_harvest_cases=8,adaptive_failure_target=2)
    target={'harvest_cases':8,'last_harvest_yield':None}
    info=m._adaptive_harvest(None,None,cfg,[cert],guard,args,state,0,target=target)
    assert info['cases_tested']==23, 'small 24-case deck must finish after known failed case'
    assert info['new_centers']==9
    assert len({f['id'] for f in guard['failures']})==10
    assert (guard['passed'],guard['tested'],guard['ok'])==(0,2,False)
    assert info['purpose']=='repair_discovery_not_accuracy_estimation'
    assert target['last_harvest_yield']>0
    key=f'2:0:{cert["deck_sha256"]}'
    assert len(state['retention_known_failures'][key])==9


def test_adaptive_harvest_large_deck_limits_and_decays_budget(monkeypatch):
    rows,_=m.deck(0,0,1024)
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    cert={'rung':0,'radius':0,'deck_sha256':m.deck_fingerprint(rows),'deck_config':cfg}
    guard={'ok':False,'failures':[{'rung':0,'radius':0,'id':rows[0]['id']}],
           'certificates':[{'rung':0,'radius':0,'ok':False}]}
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*a:['ANSWER: 0']*len(sources))
    # Every zero-line T00 case is correct; no further failing centers exist.
    state={'retention_known_failures':{},'attempts':1}
    target={'harvest_cases':64}
    info=m._adaptive_harvest(None,None,cfg,[cert],guard,
        _adaptive_args(adaptive_harvest_cases=64),state,1,target=target)
    assert info['cases_tested']<=64 and info['new_centers']==0
    assert info['next_budget']==32


def test_adaptive_center_first_repair_uses_failed_center_before_neighbors(tmp_path,monkeypatch):
    import torch
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    rows,_=m.deck(0,0,1024)
    cert={'rung':0,'radius':0,'deck_config':cfg,'deck_sha256':m.deck_fingerprint(rows)}
    failing=rows[123]
    guard={'failures':[{'rung':0,'radius':0,'id':failing['id']}]}
    batches=[]
    def train(bb,tok,opt,batch,args):
        batches.append([r['source'] for r in batch]);return .1
    monkeypatch.setattr(m,'_train_step',train)
    monkeypatch.setattr(m,'_persist',lambda *a,**kw:None)
    monkeypatch.setattr(m,'_charge_step',lambda *a:None)
    state={'steps_by_rung':{'1':0}}
    args=_adaptive_args(batch_size=4,local_radius=4,local_repair_steps=10)
    result=m._train_local_radius(SimpleNamespace(train=lambda:None,eval=lambda:None),None,None,
        guard,[cert],cfg,[m._case(1,(2,-2,0),'new')],args,
        __import__('random').Random(1),state,tmp_path,None,1,5,
        step_cap=2,center_first=True)
    assert result['steps']==2 and result['centers']==1
    assert failing['source'] in batches[0]
    assert len(batches)==2 and state['steps_by_rung']['1']==2


def test_adaptive_harvest_does_not_erase_audit_failure_on_resumption(monkeypatch,tmp_path):
    # A durable checkpoint state can round-trip adaptive decisions; neither
    # the original tripwire gate score nor optimizer accounting is rewritten.
    args=_adaptive_args()
    state=m._new_state({'max_rung':1,'radii':[0],'steps_per_rung':1000},'source','frozen')
    control=m._adaptive_control(state,args)
    target=m._adaptive_target(control,control['policy'],1,0,args)
    m._adaptive_decide(target,control['policy'],.5,.9,{'ok':False},{'ok':True},4)
    path=tmp_path/'progress.json'
    m._atomic_json(path,state)
    restored=json.loads(path.read_text())
    assert restored['adaptive_controller']['targets']['1:0']['repairs']==1
    assert restored['adaptive_controller']['targets']['1:0']['check_steps']==13
    assert restored['steps_by_rung']==state['steps_by_rung']


def test_adaptive_real_run_resumes_checkpoint_and_spent_budget(tmp_path,monkeypatch):
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class FakeBB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('lora_a',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'lora_a':bb.p.detach().clone()})
    def restore(bb,weights):
        with torch.no_grad():bb.p.copy_(weights['lora_a'])
    monkeypatch.setattr(zero,'restore_lora',restore)
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(FakeBB(),None,'s','f',[],zero))
    def fake_audit(bb,tok,rows,*args,**kwargs):
        return {'all_pass':False,'tested':len(rows),'passed':0,'total':len(rows),
                'failures':[{'id':rows[0]['id'],'source':rows[0]['source'],
                             'expected':rows[0]['target']}],'truncated':False}
    monkeypatch.setattr(m,'_audit',fake_audit)
    monkeypatch.setattr(m,'_audit_retention',lambda *a,**kw:
        {'ok':True,'passed':0,'tested':0,'total':0,'floor':.95,
         'certificates':[],'failures':[]})
    def train(bb,tok,opt,rows,args):
        with torch.no_grad():bb.p.add_(1.)
        return .5
    monkeypatch.setattr(m,'_train_step',train)
    home=tmp_path/'parent';home.mkdir()
    (home/'stage.pt').touch();(home/'text.pt').touch()
    args=SimpleNamespace(radii='0',operand_limit=8,max_rung=0,per_cell=1,
        seed=12,t00_mode='sampled',steps_per_rung=7,repair_steps=1,
        batch_size=1,replay_per_batch=0,lr=.001,max_seq=192,
        max_tokens=256,max_new_tokens=64,stage1_checkpoint=str(home/'stage.pt'),
        text_checkpoint=str(home/'text.pt'),output=str(tmp_path/'campaign'),
        device='cpu',eval_batch=2,fail_limit=10,extend_budget=0,
        warm_start_ladder=None,adaptive_repair=True,
        adaptive_check_min=2,adaptive_check_max=4,
        adaptive_repair_min=2,adaptive_repair_max=4,
        adaptive_harvest_cases=16,adaptive_failure_target=2,
        adaptive_full_steps=0)
    first=m.run(args)
    assert first['status']=='BUDGET_EXHAUSTED'
    assert first['steps_by_rung']['0']==7
    target=first['adaptive_controller']['targets']['0:0']
    assert target['checks']>=2
    assert target['check_steps']<=4
    assert first['adaptive_controller']['decisions']
    original=torch.load(tmp_path/'campaign'/'working.pt',weights_only=True)
    assert original['lora']['lora_a'].item()==7.
    resumed=m.run(args)
    assert resumed['steps_by_rung']['0']==7
    assert resumed['adaptive_controller']==first['adaptive_controller']
    assert torch.load(tmp_path/'campaign'/'working.pt',weights_only=True)['lora']['lora_a'].item()==7.


def test_ready_rung_failed_retention_reuses_gate_without_duplicate_pre_repair_audit(tmp_path, monkeypatch):
    """An already ready rung must not spend a second full history scan before repair."""
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero

    class BB(nn.Module):
        def __init__(self):
            super().__init__()
            self.p=nn.Parameter(torch.tensor(0.))

    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(BB(),None,'s','f',[],zero))
    original_new=m._new_state
    def with_previous(cfg,s,f):
        state=original_new(cfg,s,f)
        rows,scope=m._case_bank(cfg,0,0)
        state['provisional']=[{'rung':0,'radius':0,'case_count':len(rows),
            'deck_sha256':m.deck_fingerprint(rows),'deck_config':cfg,'scope':scope}]
        state['rung']=1
        return state
    monkeypatch.setattr(m,'_new_state',with_previous)
    samples=[]
    def sample(*args):
        samples.append(args[-1])
        return {'readiness':True,'accuracy':1.,'passed':8,'tested':8,
                'total':8,'failures':[],'per_sample':[],'all_pass':True}
    monkeypatch.setattr(m,'_sampled_progress_grade',sample)
    guards=[]
    def retention(*args,**kwargs):
        guards.append(len(guards))
        ok=len(guards)>1
        return {'ok':ok,'passed':8 if ok else 7,'tested':8,'total':8,'floor':.95,
                'certificates':[],'failures':[] if ok else [{'rung':0,'radius':0,'id':'x'}]}
    monkeypatch.setattr(m,'_audit_retention',retention)
    repairs=[]
    def repair(*args,**kwargs):
        repairs.append(1)
        return {'attempted':True,'steps':1}
    monkeypatch.setattr(m,'_train_local_radius',repair)
    p=tmp_path/'source';p.mkdir()
    for x in ('stage.pt','text.pt'):(p/x).touch()
    args=SimpleNamespace(radii='0',operand_limit=64,max_rung=1,per_cell=2,
        seed=12,t00_mode='exhaustive',steps_per_rung=10,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,
        max_tokens=256,max_new_tokens=64,stage1_checkpoint=str(p/'stage.pt'),
        text_checkpoint=str(p/'text.pt'),output=str(tmp_path/'campaign'),
        device='cpu',eval_batch=8,fail_limit=16,extend_budget=0,
        warm_start_ladder=None,advance_mode='sampled',advance_accuracy=.95,
        advance_probes=2,probe_cases=8,retention_sample_cases=8,
        retention_full_every=0,gate_full_every=0,audit_progress_every=0,
        retention_floor=.95,adaptive_repair=False)
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert len(guards)==3  # readiness, post repair, successful advancement
    assert len(samples)==3  # readiness, post repair, next readiness
    assert len(repairs)==1
    assert state['steps_by_rung']['1']==0


def test_adaptive_local_repair_balances_neighbors_across_damage_centers(tmp_path, monkeypatch):
    import random
    cfg={'operand_limit':1024,'per_cell':8,'seed':42257,'t00_mode':'exhaustive'}
    rows,_=m.deck(0,0,1024)
    cert={'rung':0,'radius':0,'deck_config':cfg,'deck_sha256':m.deck_fingerprint(rows)}
    a,b=rows[900],rows[1100]
    guard={'failures':[{'rung':0,'radius':0,'id':x['id']} for x in (a,b)]}
    batches=[]
    monkeypatch.setattr(m,'_train_step',lambda bb,tok,opt,batch,args:
                        batches.append(batch[:2]) or .01)
    monkeypatch.setattr(m,'_persist',lambda *args,**kwargs:None)
    monkeypatch.setattr(m,'_charge_step',lambda *args:None)
    bb=SimpleNamespace(train=lambda:None,eval=lambda:None)
    args=_adaptive_args(batch_size=4,local_radius=2,local_repair_steps=10)
    result=m._train_local_radius(bb,None,None,guard,[cert],cfg,
        [m._case(1,(2,-2,0),'new')],args,random.Random(5),
        {'steps_by_rung':{'1':0}},tmp_path,None,1,5,step_cap=4,
        center_first=True)
    assert result['steps']==4 and result['centers']==2
    assert {x['id'] for x in batches[0]}=={a['id'],b['id']}
    for batch in batches[1:]:
        assert any(x['id'].startswith(a['id']+'-LOCAL-') for x in batch)
        assert any(x['id'].startswith(b['id']+'-LOCAL-') for x in batch)


def test_rung_major_migrates_deep_zero_frontier_without_forging_certificates():
    """T06/R0 readiness is useful but not a historical retention certificate."""
    cfg={'radii':[0,1,2,3,4],'max_rung':8,'steps_per_rung':10000}
    state=m._new_state(cfg,'s','f')
    state['rung']=6
    state['radius_index']=0
    state['steps_by_rung']['6']=55
    state['steps_by_rung']['0']=75
    original_cert={'rung':0,'radius':0,'deck_sha256':'exact'}
    state['certificates']=[original_cert]
    state['provisional']=[{'rung':0,'radius':1}]+[
        {'rung':r,'radius':0} for r in range(1,6)]
    state['advancement_policy']={'mode':'sampled','accuracy':.95,'probes':2}
    state['last_audit']={'rung':6,'radius':0,'passed':48,'total':48,
                         'all_pass':True,'audit_kind':'sampled'}
    old_steps=dict(state['steps_by_rung'])
    selection=m._record_curriculum(state,SimpleNamespace(curriculum='rung-major'))
    assert selection=={'mode':'rung-major'}
    assert (state['rung'],cfg['radii'][state['radius_index']])==(0,2)
    assert state['status']=='READY'
    assert state['steps_by_rung']==old_steps
    assert state['certificates']==[original_cert]
    assert (6,0) not in {(x['rung'],x['radius']) for x in state['provisional']}
    assert state['last_audit']['rung']==6
    assert state['curriculum_history'][-1]['deferred_unfinished_target']==[6,0]
    assert m._first_unfinished_target(cfg,state['certificates'],
        provisional=state['provisional'],radius_first=False)==(0,2)
    for radius in (2,3,4):
        state['provisional'].append({'rung':0,'radius':radius})
    assert m._first_unfinished_target(cfg,state['certificates'],
        provisional=state['provisional'],radius_first=False)==(1,1)


def test_rung_major_keeps_policy_on_resume_without_repeat_flag():
    cfg={'radii':[0,1,2],'max_rung':2,'steps_per_rung':10000}
    state=m._new_state(cfg,'s','f')
    state['rung']=2
    state['provisional']=[{'rung':0,'radius':0},{'rung':1,'radius':0}]
    state['advancement_policy']={'mode':'sampled','accuracy':.95,'probes':2}
    m._record_curriculum(state,SimpleNamespace(curriculum='rung-major'))
    assert state['rung']==0 and state['radius_index']==1
    count=len(state['curriculum_history'])
    assert m._record_curriculum(state,SimpleNamespace(curriculum=None))=={'mode':'rung-major'}
    assert len(state['curriculum_history'])==count
    assert m._schedule_radius_first(state,'sampled') is False
    # Explicit legacy schedule retains the radius-first option.
    m._record_curriculum(state,SimpleNamespace(curriculum='radius-major'))
    assert state['rung']==2 and state['radius_index']==0


def test_curriculum_snapshot_is_immutable_and_contains_pre_migration_state(tmp_path):
    import torch
    cfg={'radii':[0,1,2],'max_rung':6,'steps_per_rung':10000}
    state=m._new_state(cfg,'source','frozen')
    state.update({'rung':6,'radius_index':0,'attempts':14})
    state['steps_by_rung']['6']=55
    before={'state':state,'lora':{'p':torch.tensor([1.,2.])},
            'optimizer':{'state':{1:{'step':torch.tensor(55)}}}}
    home=tmp_path/'run';home.mkdir()
    torch.save(before,home/'working.pt')
    backup=m._snapshot_curriculum_transition(home,state,SimpleNamespace(curriculum='rung-major'))
    assert backup.is_file() and backup.parent.name=='curriculum_snapshots'
    recovered=torch.load(backup,weights_only=True)
    assert recovered['state']['rung']==6
    assert recovered['optimizer']['state'][1]['step'].item()==55
    torch.save({'state':{'rung':0},'optimizer':None},home/'working.pt')
    second=m._snapshot_curriculum_transition(home,state,SimpleNamespace(curriculum='rung-major'))
    assert backup==second
    assert torch.load(backup,weights_only=True)['state']['rung']==6
    assert len(state['curriculum_snapshots'])==1


def test_balanced_repair_centers_share_attention_across_residual_signs_and_depth():
    def row(id,rung,residual):
        return {'id':id,'rung':rung,'residual':residual}
    bank={}
    failures=[]
    for key,value in [((0,0,'a0'),0),((0,0,'a1'),0),((0,0,'a2'),0),
                      ((0,2,'b0'),-1),((0,2,'b1'),-1),
                      ((1,2,'c0'),1),((1,2,'c1'),1)]:
        bank[key]=row(key[2],key[0],value)
        failures.append({'rung':key[0],'radius':key[1],'id':key[2]})
    sorted_centers=m._balanced_repair_centers(failures,bank)
    assert len(sorted_centers)==7 and len(set(sorted_centers))==7
    assert {(x[0],x[1]) for x in sorted_centers[:3]}=={(0,0),(0,2),(1,2)}
    rotated=m._balanced_repair_centers(failures,bank,rotation=1)
    assert rotated[0]!=sorted_centers[0]
    assert set(rotated)==set(sorted_centers)


def test_rung_major_real_run_acquires_full_width_before_depth(tmp_path,monkeypatch):
    torch=pytest.importorskip('torch')
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class BB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(0.))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(BB(),None,'src','frozen',[],zero))
    def audit(bb,tok,items,*others,**kw):
        return {'all_pass':True,'passed':len(items),'tested':len(items),
                'total':len(items),'failures':[],'truncated':False}
    monkeypatch.setattr(m,'_audit',audit)
    parent=tmp_path/'parents';parent.mkdir()
    (parent/'a.pt').touch();(parent/'b.pt').touch()
    args=SimpleNamespace(radii='0,1,2',operand_limit=8,max_rung=2,per_cell=1,
        seed=14,t00_mode='exhaustive',steps_per_rung=100,repair_steps=1,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(parent/'a.pt'),
        text_checkpoint=str(parent/'b.pt'),output=str(tmp_path/'campaign'),
        device='cpu',eval_batch=8,fail_limit=10,extend_budget=0,
        warm_start_ladder=None,advance_mode='sampled',advance_accuracy=.95,
        advance_probes=2,probe_cases=8,audit_progress_every=0,retention_floor=.95,
        local_radius=4,local_repair_steps=10,retention_sample_cases=8,
        retention_full_every=0,gate_full_every=0,curriculum='rung-major')
    state=m.run(args)
    assert state['status']=='PROVISIONAL_ALL'
    assert [(x['rung'],x['radius']) for x in state['provisional']]==[
        (r,d) for r in range(3) for d in range(3)]
    assert state['steps_by_rung']=={'0':0,'1':0,'2':0}
    assert state['curriculum']['mode']=='rung-major'
    args.curriculum=None
    again=m.run(args)
    assert again['status']=='PROVISIONAL_ALL'
    assert again['curriculum']['mode']=='rung-major'


def test_resumed_t06_zero_width_migration_keeps_weights_optimizer_and_archive(tmp_path,monkeypatch):
    """End-to-end restart from deep-zero campaign into rung-major missing cells."""
    import torch
    from torch import nn
    from tools import nanojev_qwen_zero_line_backbone as zero
    class BB(nn.Module):
        def __init__(self):
            super().__init__();self.p=nn.Parameter(torch.tensor(6.25))
    monkeypatch.setattr(zero,'lora_named_parameters',lambda bb:[('p',bb.p)])
    monkeypatch.setattr(zero,'lora_state',lambda bb:{'p':bb.p.detach().clone()})
    monkeypatch.setattr(zero,'restore_lora',lambda bb,state:bb.p.data.copy_(state['p']))
    monkeypatch.setattr(m,'_fresh_parent',lambda args:(BB(),None,'src','freeze',[],zero))
    monkeypatch.setattr(m,'_audit',lambda bb,tok,items,*others,**kw:
        {'all_pass':True,'passed':len(items),'tested':len(items),
         'total':len(items),'failures':[],'truncated':False})
    home=tmp_path/'p';home.mkdir()
    for name in ('a.pt','b.pt'):(home/name).touch()
    out=tmp_path/'campaign'
    args=SimpleNamespace(radii='0,1,2',operand_limit=8,max_rung=6,per_cell=1,
        seed=17,t00_mode='exhaustive',steps_per_rung=1000,repair_steps=5,
        batch_size=2,replay_per_batch=0,lr=.001,max_seq=192,max_tokens=256,
        max_new_tokens=64,stage1_checkpoint=str(home/'a.pt'),
        text_checkpoint=str(home/'b.pt'),output=str(out),device='cpu',
        eval_batch=8,fail_limit=16,extend_budget=0,warm_start_ladder=None,
        advance_mode='sampled',advance_accuracy=.95,advance_probes=2,
        probe_cases=8,audit_progress_every=0,retention_floor=.95,
        local_radius=4,local_repair_steps=10,retention_sample_cases=8,
        retention_full_every=0,gate_full_every=0,curriculum='rung-major')
    cfg=m._config(args)
    state=m._new_state(cfg,'src','freeze')
    state['rung']=6;state['steps_by_rung']['6']=45;state['attempts']=26
    state['advancement_policy']=m._advancement_policy(args)
    for r,d in [(0,0),(0,1)]+[(r,0) for r in range(1,6)]:
        rows,scope=m._case_bank(cfg,r,d)
        entry={'rung':r,'radius':d,'case_count':len(rows),
               'deck_sha256':m.deck_fingerprint(rows),'scope':scope,
               'deck_config':cfg}
        if r==0 and d==0:state['certificates'].append(entry)
        else:state.setdefault('provisional',[]).append(entry)
    from pathlib import Path
    out.mkdir()
    bb=BB()
    opt=torch.optim.AdamW([bb.p],lr=args.lr,weight_decay=0)
    m._persist(out,bb,zero,state,opt)
    resumed=m.run(args)
    assert resumed['status']=='PROVISIONAL_ALL'
    assert resumed['steps_by_rung']['6']==45
    assert [(c['rung'],c['radius']) for c in resumed['provisional']][6:9]==[
        (0,2),(1,1),(1,2)]
    backup=out/resumed['curriculum_snapshots'][0]
    archived=torch.load(backup,weights_only=True)
    assert archived['state']['rung']==6
    assert archived['lora']['p'].item()==6.25
    assert archived['optimizer']['param_groups'][0]['lr']==args.lr
    assert torch.load(out/'working.pt',weights_only=True)['lora']['p'].item()==6.25
