"""Regression and anti-shortcut checks for the V2 15-train/5-holdout repair."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import numpy as np
import pytest
from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2 as v2
from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2_corpus as corpus

PAYOFF={'HAWK_HAWK_PAYOFF':'-1','HAWK_DOVE_PAYOFF':'3',
        'DOVE_HAWK_PAYOFF':'0','DOVE_DOVE_PAYOFF':'3/2'}


def _game_stub(monkeypatch):
    monkeypatch.setattr(v2,'_load_game_head',lambda _: (PAYOFF,'test-payoff-checkpoint-sha'))
    # In tests we only replace the unrelated exact payoff executor: all
    # counts, roles and decisions are still computed using real V2 features.
    def decision(prompt,first_role,weights):
        nums=[int(s[3].replace(',','')) for s in v2.generic_number_spans(prompt)]
        a,b=nums if first_role else nums[::-1]
        return {'opponent_hawk_count':a,'opponent_dove_count':b,
                'decision':v2._independent_decision(a,b)}
    monkeypatch.setattr(v2,'_decision',decision)


def test_15_train_5_holdout_role_balanced_no_count_overlap():
    training,holdout=corpus.curriculum(seed=47,samples_per_template=16)
    assert len(training)==240 and len(holdout)==80
    assert {r['template_id'] for r in training}==set(range(15))
    assert {r['template_id'] for r in holdout}==set(range(5))
    assert not {r['hawk_count'] for r in training}&{r['hawk_count'] for r in holdout}
    for i in range(15):
        examples=[r for r in training if r['template_id']==i]
        assert len(examples)==16
        assert [r['variant'] for r in examples].count('reordered')==8
        assert sum(r['first_is_hawk'] for r in examples)==8
        for row in examples:
            assert [sp[3] for sp in row['spans']]==[sp[3] for sp in v2.generic_number_spans(row['prompt'])]
    for row in holdout:
        assert 'variant' not in row  # inherited original V1 holdouts unchanged


def test_v2_features_are_generic_no_deterministic_role_naming():
    a='Red unit was logged 940000 times; blue unit was logged 60000 times.'
    b='Hawk unit was logged 940000 times; Dove unit was logged 60000 times.'
    x=v2._context_features(a)
    y=v2._context_features(b)
    assert len(x)==len(y)==2
    assert any(k=='word=red' for k in x[0])
    assert any(k=='word=hawk' for k in y[0])
    # The feature extractor yields tokens; it never assigns target classes.
    assert not any(key.startswith('role=') for f in x+y for key in f)


def test_local_15_5_text_repair_and_checkpoint_causal_intervention(tmp_path,monkeypatch):
    _game_stub(monkeypatch)
    output=tmp_path/'v2'
    fail=tmp_path/'failed-v1'
    fail.mkdir()
    original=(fail/'language_head.json')
    original.write_text('{"old":"failed-head"}\n')
    r={'status':'FAIL_T08_LANGUAGE_HEAD_DID_NOT_BEAT_CONTROLS',
       'holdout':{'exact_roles':96,'cases':240},
       'checkpoint_sha256':v2._sha(original)}
    (fail/'final_report.json').write_text(json.dumps(r))
    prior_checksum=v2._sha(original)
    report=v2.train(output=output,game_head=tmp_path,prior=fail,run=tmp_path,
                    model_id='none',device='cpu',layer=9,max_length=256,
                    encoder='text',epochs=200,seed=47,samples_per_template=16)
    assert report['status']=='FAIL_T08_V2_NEURAL_ROLE_UNPROVEN'
    with pytest.raises(ValueError,match='NEURAL_ROLE_UNPROVEN'):
        v2.query(output=output,game_head=tmp_path,
                 prompt='Hawk 940000; Dove 60000.',device='cpu')
    assert report['diagnostic_role_gate_passed']
    assert not report['neural_role_transfer_certified']
    assert report['holdout']['role_accuracy']>=.95
    assert report['holdout']['boundary_exact']==report['holdout']['boundary_cases']
    assert report['train']['role_accuracy']==1.0
    assert report['train_templates']==15 and report['holdout_templates']==5
    assert report['prior_failed_checkpoint']['prior_holdout_exact_roles']==96
    assert report['position_only_control']['exact_roles']<report['holdout']['exact_roles']
    assert report['neural_superiority_over_deterministic_baseline_demonstrated'] is False
    assert prior_checksum==v2._sha(original)  # failed checkpoint unchanged
    text='Dove actions observed in the rival log: 60000. Hawk actions observed: 940000. Which action gives the greater cumulative payout?'
    q=v2.query(output=output,game_head=tmp_path,prompt=text,device='cpu',diagnostic=True)
    assert q['status']=='UNCERTIFIED_T08_V2_ROLE_DECISION'
    assert q['neural_role_transfer_certified'] is False
    assert q['first_numeric_role']=='DOVE'
    assert q['result']['decision']=='DOVE'
    assert q['result']['opponent_hawk_count']==940000
    with pytest.raises(FileExistsError):
        v2.train(output=output,game_head=tmp_path,prior=fail,run=tmp_path,
                 model_id='none',device='cpu',layer=9,max_length=256,
                 encoder='text',epochs=200,seed=47,samples_per_template=16)
    state=json.loads((output/'role_head.json').read_text())
    state['coef']=[-v for v in state['coef']]
    state['intercept']=-state['intercept']
    (output/'role_head.json').write_text(v2._canonical(state))
    with pytest.raises(ValueError,match='FINGERPRINT'):
        v2.query(output=output,game_head=tmp_path,prompt=text,device='cpu')
    meta=json.loads((output/'final_report.json').read_text())
    meta['checkpoint_sha256']=v2._sha(output/'role_head.json')
    (output/'final_report.json').write_text(json.dumps(meta))
    swap=v2.query(output=output,game_head=tmp_path,prompt=text,device='cpu',diagnostic=True)
    assert swap['first_numeric_role']!=q['first_numeric_role']
    assert swap['result']['decision']!=q['result']['decision']


def test_query_module_does_not_import_training_corpus():
    c=('import sys; '
       'from tools import nanojev_gamer_v1_hawk_dove_t08_role_v2 as m; '
       "assert 'tools.nanojev_gamer_v1_hawk_dove_t08_role_v2_corpus' not in sys.modules; "
       "assert 'tools.nanojev_gamer_v1_hawk_dove_t08_corpus' not in sys.modules")
    res=subprocess.run([sys.executable,'-c',c],capture_output=True,text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert res.returncode==0,(res.stdout,res.stderr)


def test_qwen_feature_hand_off_and_checkpoint_sha_lineage(tmp_path,monkeypatch):
    _game_stub(monkeypatch)
    def fake_capture(rows,*,run,model_id,device,layer,max_length):
        # Synthetic frozen features, derived only from visible prompt bytes.
        arr=np.asarray([[((len(r['prompt'])%7)-3)/10.0,((len(r['prompt'])%11)-5)/10.0] for r in rows],dtype='float32')
        return arr,'frozen-adapter-sha-for-test'
    monkeypatch.setattr(v2,'_frozen_features',fake_capture)
    output=tmp_path/'qwen-sim'
    report=v2.train(output=output,game_head=tmp_path,prior=None,run=tmp_path,
                    model_id='mock',device='cpu',layer=9,max_length=256,
                    encoder='frozen-qwen',epochs=200,seed=47,samples_per_template=16)
    assert report['status']=='FAIL_T08_V2_NEURAL_ROLE_UNPROVEN'
    with pytest.raises(ValueError,match='NEURAL_ROLE_UNPROVEN'):
        v2.query(output=output,game_head=tmp_path,
                 prompt='Hawk 940000; Dove 60000.',device='cpu')
    assert report['diagnostic_role_gate_passed']
    assert not report['neural_role_transfer_certified']
    assert report['frozen_qwen_only_control'] is not None
    state=json.loads((output/'role_head.json').read_text())
    assert state['qwen_feature_count']==2
    assert state['adapter_sha256']=='frozen-adapter-sha-for-test'
    t='Hawk actions observed: 940000. Dove actions observed: 60000.'
    q=v2.query(output=output,game_head=tmp_path,prompt=t,device='cpu',diagnostic=True)
    assert not q['language_holdout_gate_passed']
    assert not report['neural_role_transfer_certified']
    monkeypatch.setattr(v2,'_frozen_features',lambda *a,**kw: (np.ones((1,2),dtype='float32'),'WRONG-ADAPTER'))
    with pytest.raises(ValueError,match='LINEAGE'):
        v2.query(output=output,game_head=tmp_path,prompt=t,device='cpu',diagnostic=True)


def test_previous_failure_cannot_be_overwritten_or_misrepresented(tmp_path):
    prior=tmp_path/'prior';prior.mkdir()
    c=prior/'language_head.json';c.write_text('wrong')
    (prior/'final_report.json').write_text(json.dumps({'status':'PASS_FAKE','checkpoint_sha256':v2._sha(c)}))
    with pytest.raises(ValueError,match='PRIOR_MUST_BE_FAILED'):
        v2._freeze_previous(prior)


def test_historical_pass_label_cannot_certify_lexical_bypass(tmp_path,monkeypatch):
    """Old V2 reports that passed without neural superiority must fail closed."""
    _game_stub(monkeypatch)
    out=tmp_path/'legacy';out.mkdir()
    state={'schema':v2.SCHEMA,'encoder':'text','vocabulary':{},'coef':[],
           'intercept':0.0,'feature_count':0,'game_checkpoint_sha256':'test-payoff-checkpoint-sha',
           'acceptance_status':'PASS_T08_V2_ROLE_TRANSFER'}
    ck=out/'role_head.json';ck.write_text(v2._canonical(state)+'\n')
    old={'status':'PASS_T08_V2_ROLE_TRANSFER','checkpoint_sha256':v2._sha(ck),
         'neural_superiority_over_deterministic_baseline_demonstrated':False,
         'reused_holdouts_known_from_v1_failure':True}
    (out/'final_report.json').write_text(json.dumps(old))
    with pytest.raises(ValueError,match='NEURAL_ROLE_UNPROVEN'):
        v2.query(output=out,game_head=tmp_path,
                 prompt='Hawk 940000; Dove 60000.',device='cpu')
    q=v2.query(output=out,game_head=tmp_path,
               prompt='Hawk 940000; Dove 60000.',device='cpu',diagnostic=True)
    assert q['status']=='UNCERTIFIED_T08_V2_ROLE_DECISION'
    assert q['neural_role_transfer_certified'] is False
    assert q['language_holdout_gate_passed'] is False
