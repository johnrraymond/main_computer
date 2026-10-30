"""Protocol tests: numeric-terminal ladder without answer-prefix supervision."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import nanojev_qwen_lm_radius_ladder as m


def test_complete_packet_numerical_detection_is_target_free():
    for text,value in [('PROBLEM: 0',0),('PROBLEM: -12\n',-12),('PROBLEM: 1024',1024)]:
        assert m.numeric_terminal(text)==value
        assert m.numerical_completion(text)=={'numerical':True,'complete':True,'value':value}
    for text in ['PROBLEM: 0+0','PROBLEM: 10+(-10)','PROBLEM: 10 trillion',
                 'PROBLEM: 1.0','PROBLEM: 01','PROBLEM: -0','PROBLEM: +1',
                 'PROBLEM: 12\nANSWER: 12','ANSWER: 0','PROBLEM: x=1',
                 'PROBLEM: 0\n\n', 'PROBLEM: 3 4', '0', '']:
        assert m.numeric_terminal(text) is None, text
        assert m.numerical_completion(text)['complete'] is False


def test_numeric_deck_reaches_terminal_without_any_answer_targets():
    for rung in range(0,9):
        row=m._case(rung,(2,-2)+((0,)*rung),f'rung{rung}',terminal_mode='numeric')
        assert row['source'].startswith('PROBLEM: ')
        assert row['complete_chain'][-1]=='PROBLEM: 0'
        assert len(row['complete_chain'])==rung+2
        assert all('ANSWER:' not in text for text in row['complete_chain'])
        if rung==0:
            assert row['target']=='PROBLEM: 0'
            assert m.numeric_terminal(row['target'])==0
            assert m.correctness(row['source'],row['target'],'PROBLEM: 0\n')[0]
            assert not m.correctness(row['source'],row['target'],'ANSWER: 0\n')[0]
        else:
            assert m.numeric_terminal(row['target']) is None
            assert not m.correctness(row['source'],row['target'],'PROBLEM: 0')[0]
    cases,scope=m.deck(0,2,8,terminal_mode='numeric')
    assert all(c['target']==f'PROBLEM: {c["residual"]}' for c in cases)
    assert scope['kind']=='exhaustive_ordered_t00'


def test_numeric_config_changes_checkpoint_contract():
    from test_nanojev_qwen_lm_radius_ladder import _zero_fork_fixture
    # Original fixture remains unchanged and uses the exact legacy contract.
    args=SimpleNamespace(radii='0,1',operand_limit=8,max_rung=2,per_cell=1,seed=1,
        t00_mode='exhaustive',steps_per_rung=10,repair_steps=2,batch_size=2,
        replay_per_batch=0,lr=.0001,max_seq=128,max_tokens=256,max_new_tokens=64,
        stage1_checkpoint='a',text_checkpoint='b')
    legacy=m._config(args)
    args.terminal_mode='numeric'
    modern=m._config(args)
    assert modern['terminal_mode']=='numeric'
    assert 'terminal_mode' not in legacy
    assert m._hash(legacy)!=m._hash(modern)
    assert m._case_bank(modern,0,0)[0][0]['target'].startswith('PROBLEM: ')


def test_numeric_resume_rejects_existing_old_output(tmp_path):
    torch=pytest.importorskip('torch')
    class Zero:
        @staticmethod
        def restore_lora(bb,x):pass
    cfg={'schema':m.SCHEMA,'radii':[0], 'max_rung':0,'steps_per_rung':10}
    state=m._new_state(cfg,'s','f')
    torch.save({'schema':m.SCHEMA,'state':state,'config':cfg,'lora':{}},tmp_path/'working.pt')
    with pytest.raises(RuntimeError,match='TERMINAL_CONTRACT_CHANGED_USE_NEW_OUTPUT'):
        m._load_resume(tmp_path,cfg|{'terminal_mode':'numeric'},None,Zero,'s','f')


def test_numeric_certified_bootstrap_imports_only_seed_not_certification(tmp_path):
    torch=pytest.importorskip('torch')
    from test_nanojev_qwen_lm_radius_ladder import _zero_fork_fixture
    parent,legacy_cfg,cert,args=_zero_fork_fixture(tmp_path)
    cfg=legacy_cfg|{'terminal_mode':'numeric','numerical_completion':'strict_integer_literal_PROBLEM_packet',
                    'grading':'greedy exact one-hop PROBLEM packet; numeral terminal detected independently'}
    class Zero:
        @staticmethod
        def restore_lora(bb,weights):bb.value=weights['lora_a'].item()
    bb=SimpleNamespace(value=None)
    state=m._numeric_bootstrap_from_certified_zero(parent,m._new_state(cfg,'source','frozen'),cfg,bb,Zero)
    assert bb.value==2
    assert state['certificates']==[]
    assert state['steps_by_rung']['0']==0
    assert state['rung']==0
    assert state['numeric_bootstrap']['old_certificate_reused'] is False
    out=tmp_path/'numeric';out.mkdir()
    m._ensure_numeric_bootstrap_snapshot(out,state,parent)
    assert (out/'numeric_bootstrap_legacy_seed.pt').is_file()
    # The copied original remains unchanged; its legacy certificate is not transferred.
    assert torch.load(parent/'certified_T00_R0000.pt',weights_only=True)['certificate']==cert
    with pytest.raises(RuntimeError,match='RESUME_SOURCE_MISMATCH'):
        m._ensure_numeric_bootstrap_snapshot(out,state,tmp_path/'other')


def test_numeric_audit_distinguishes_terminal_completion_from_early_stop(monkeypatch):
    responses={'PROBLEM: 1+(-1)':'PROBLEM: 0\n',
               'PROBLEM: (1+(-1))+3':'PROBLEM: 0\n'}
    monkeypatch.setattr(m,'_greedy',lambda bb,tok,sources,*args:[responses[x] for x in sources])
    rows=[m._case(0,(1,-1),'zero','numeric'),m._case(1,(1,-1,3),'unfinished','numeric')]
    report=m._audit(None,None,rows,'cpu',256,64,2)
    assert report['passed']==1
    assert report['numeric_completion']=={'detected':2,'correct':1,'premature':1}


def test_detect_stage_runs_without_model_load(capsys):
    result=m.main(['--stage','detect','--packet','PROBLEM: 0'])
    assert result['complete'] is True and result['value']==0
    assert 'QWEN_LADDER_NUMERICAL_DETECTION=' in capsys.readouterr().out
    assert m.main(['--stage','detect','--packet','PROBLEM: 10+(-10)'])['complete'] is False
