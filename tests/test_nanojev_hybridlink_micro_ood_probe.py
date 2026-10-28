"""Read-only OOD deck and evaluator regression checks; no CUDA weights needed."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import nanojev_hybridlink_micro_reduction as micro
from tools import nanojev_hybridlink_micro_ood_probe as ood


def config():
    params={'band':[0,4], 'scales':[8,16,32,64,128,256],
        'seed':71,'train_count':36,'dev_count':12,'test_count':12}
    deck=micro.deck_for(band=(0,4),scales=tuple(params['scales']),seed=71,
         train_count=36,dev_count=12,test_count=12)
    return {**params,'schema':micro.SCHEMA,'deck_hash':micro.deck_hash(deck),
        'sources':{'fake':'hash'},'frozen_backbone':'hash'}


def test_ood_deck_diverse_and_deterministic():
    cfg=config()
    cases=ood.generate_ood_cases(cfg)
    assert [(c['id'],micro.render(c['state']),c['answer']) for c in cases]==[(c['id'],micro.render(c['state']),c['answer']) for c in ood.generate_ood_cases(cfg)]
    assert len(cases)==46
    assert {s:sum(x['suite']==s for x in cases) for s in ood.SUITES}=={
        'control':12,'structure':8,'depth':8,'negative':6,'magnitude':6,'direction':6}
    assert {x['operations'] for x in cases if x['suite'] in ('structure','depth')}=={2,6,10,20}
    assert set(x['answer'] for x in cases)>={-4,-2,-1,0,1,2,3,4}
    assert all(x['max_literal']<=ood.CAP for x in cases)
    assert any(x['max_literal']==ood.CAP for x in cases)
    assert all(x['negative_intermediate'] for x in cases if x['suite']=='negative')
    assert {x['scale'] for x in cases if x['suite']=='magnitude'}=={8,256,4096,65536,1000000,4000000}
    for x in cases:
        assert x['operations']==micro.steps(x['state'])
        assert x['answer']==micro.result_value(x['state'])
        assert not any(word in micro.prompt(x['state']).lower() for word in ('hawk','dove','payoff','game','encounter'))


def test_disjoint_from_training_states_and_structures():
    cfg=config()
    train=micro.deck_for(band=(0,4),scales=tuple(cfg['scales']),seed=71,
          train_count=36,dev_count=12,test_count=12)['train']
    train_markups={micro.render(row['state']) for row in micro.training_rows(train)}
    train_structures={ood._signature(c['state']) for c in train}
    for case in ood.generate_ood_cases(cfg):
        if case['suite']=='control':continue
        assert micro.render(case['state']) not in train_markups
        assert case['signature'] not in train_structures


def test_every_ood_ladder_passes_value_and_one_op_invariant():
    for c in ood.generate_ood_cases(config()):
        state=c['state'];prior=c['answer']
        for _ in range(c['operations']):
            after=micro.reduce_one(state)
            assert after is not None
            scored=micro.evaluate_transition(state,micro.render(after)+'\nEND_RUNG\n')
            assert scored['valid'],(c['id'],scored)
            assert micro.result_value(scored['state'])==prior
            state=scored['state']
        assert micro.steps(state)==0
        assert micro.result_value(state)==prior


def test_negative_literal_ast_roundtrip_and_reject_cheating():
    state=micro._state(('A','B','R'),'2 - 9','-7 + 1',False)
    assert micro.steps(state)==2
    reduced=micro.reduce_one(state)
    assert micro.evaluate_transition(state,micro.render(reduced)+'\nEND_RUNG\n')['valid']
    wrong=micro.render(reduced).replace('A = -7','A = -8')+'\nEND_RUNG\n'
    assert not micro.evaluate_transition(state,wrong)['valid']
    assert not micro.evaluate_transition(state,'<REDUCE>\nA = -7\nB = -6\nR = A - B\n</REDUCE>\nEND_RUNG')['valid']
    with pytest.raises(ValueError,match='MICRO_UNSAFE'):
        micro._node('-(3 * 4)')


def test_reverse_direction_paired_exact_state_and_opposite_sign():
    d=[c for c in ood.generate_ood_cases(config()) if c['suite']=='direction']
    for i in range(0,len(d),2):
        f,r=d[i:i+2]
        assert micro._same(f['state']['a'],r['state']['a'])
        assert micro._same(f['state']['b'],r['state']['b'])
        assert not f['reverse'] and r['reverse']
        assert f['answer']==-r['answer']


def test_preview_model_free_and_suites(capsys):
    cfg=config()
    args=SimpleNamespace(suites=list(ood.SUITES),limit_per_suite=1,
        seed=20261027,controls=['text_only'],preview_cases=3)
    payload=ood.preview(args,cfg)
    assert payload['model_loaded'] is False
    assert payload['test_evaluated'] is False
    assert payload['total_cases']==6
    assert payload['max_generation_calls']==2*sum(c['operations'] for c in
        ood.select_cases(cfg,args.suites,1,args.seed)[0])
    assert 'MICRO_OOD_PREVIEW=' in capsys.readouterr().out


def fake_args(tmp_path,*,output='diagnostic',suites=None,limit=1,controls=None):
    args=SimpleNamespace(checkpoint=str(tmp_path/'trained'/'best.pt'),
       output=str(tmp_path/output), device='cpu',suites=suites or list(ood.SUITES),
       controls=controls or ['text_only'],limit_per_suite=limit,seed=20261027,
       max_new_tokens=512,progress_every=5)
    ck=Path(args.checkpoint);ck.parent.mkdir(parents=True,exist_ok=True)
    ck.write_bytes(b'read-only checkpoint')
    (ck.parent/'run_config.json').write_text(json.dumps(config()),encoding='utf8')
    return args


def test_mock_inference_success_and_artifacts(tmp_path,monkeypatch):
    pytest.importorskip('torch')
    args=fake_args(tmp_path)
    class Eval:
        def eval(self):pass
    mock_model=SimpleNamespace(backbone=Eval(),head=Eval())
    monkeypatch.setattr(micro,'_load',lambda *a,**k:(mock_model,None,{'fake':'hash'},'hash',()))
    def predict(model,tok,prompt,control,budget,*,stop_at_end_rung):
        assert stop_at_end_rung and control=='text_only'
        current=prompt.split('Current:\n',1)[1].split('\nNext:\n',1)[0]
        state=micro.parse(current)
        return micro.target(state)
    monkeypatch.setattr(ood.base,'_decode',predict)
    result=ood.probe(args,config())
    assert result['selected_cases']==6
    assert result['test_evaluated'] is False
    assert result['optimizer_steps']==0
    assert all(item['one_step']['accuracy']==1.0 and item['chains']['accuracy']==1.0
           for item in result['score_by_suite']['text_only'].values())
    assert result['generation_calls']==result['records']*2
    assert (tmp_path/'diagnostic'/'free_chains.json').exists()
    assert (tmp_path/'diagnostic'/'one_steps.json').exists()
    assert (tmp_path/'diagnostic'/'cases.json').exists()
    assert Path(args.checkpoint).read_bytes()==b'read-only checkpoint'
    assert not (Path(args.checkpoint).parent/'test_consumed.json').exists()


def test_mock_free_chain_failure_is_not_corrected(tmp_path,monkeypatch):
    pytest.importorskip('torch')
    args=fake_args(tmp_path,suites=['negative'])
    class Eval:
        def eval(self):pass
    monkeypatch.setattr(micro,'_load',lambda *a,**k:(SimpleNamespace(backbone=Eval(),head=Eval()),None,{'fake':'hash'},'hash',()))
    count={'calls':0}
    def wrong(model,tok,prompt,control,budget,*,stop_at_end_rung):
        count['calls']+=1
        state=micro.parse(prompt.split('Current:\n',1)[1].split('\nNext:\n',1)[0])
        if count['calls']==state_ops+1:
            return micro.render(state)+'\nEND_RUNG\n' # no-op, must be rejected
        return micro.target(state)
    case=ood.select_cases(config(),['negative'],1)[0][0]
    state_ops=case['operations']
    monkeypatch.setattr(ood.base,'_decode',wrong)
    result=ood.probe(args,config())
    assert result['score_by_suite']['text_only']['negative']['one_step']['accuracy']==1.0
    assert result['score_by_suite']['text_only']['negative']['chains']['accuracy']==0.0
    chain=json.loads((tmp_path/'diagnostic'/'free_chains.json').read_text())['records'][0]
    assert chain['steps_done']==1 and not chain['success']


def test_checkpoint_is_mandatory_read_only_before_preview(tmp_path):
    with pytest.raises(SystemExit):
        ood.main(['--stage','preview','--checkpoint',str(tmp_path/'absent.pt')])
