"""CPU and pure-Python assurance for the anonymous micro-rung experiment."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from tools import nanojev_hybridlink_micro_reduction as m


def initial(reverse=False,names=('A','B','R')):
    return m._state(names,'(8 * 2) + (4 * 1)','(8 * 1) + (4 * 2)',reverse)


def test_exact_ladder_six_steps_and_answer():
    s=initial()
    assert m.steps(s)==6
    assert m.result_value(s)==4
    track=[m.render(s)]
    for n in range(6):
        next_s=m.reduce_one(s)
        verdict=m.evaluate_transition(s,m.render(next_s)+'\nEND_RUNG\n')
        assert verdict['valid'], verdict
        assert m.result_value(next_s)==4
        assert m.steps(next_s)==5-n
        s=next_s;track.append(m.render(s))
    assert m.steps(s)==0
    assert m.render(s)=='<REDUCE>\nA = 20\nB = 16\nR = A - B\n</REDUCE>'
    assert m.reduce_one(s) is None
    assert len(track)==7


def test_reverse_sign_preserved_and_register_names():
    for names in m.REGISTERS:
        for reverse in (False,True):
            s=initial(reverse,names)
            expected=-4 if reverse else 4
            while m.steps(s):
                nxt=m.reduce_one(s)
                verdict=m.evaluate_transition(s,m.render(nxt)+'\nEND_RUNG\n')
                assert verdict['valid']
                assert m.result_value(nxt)==expected
                s=nxt
            assert m.result_value(s)==expected


def test_any_one_operation_allowed_not_only_leftmost():
    before=initial()
    legal=m.successors(before)
    assert len(legal)==4
    assert all(m.evaluate_transition(before,m.render(s)+'\nEND_RUNG\n')['valid'] for s in legal)
    # Also permit reducing B before A.
    assert m._same(legal[-1]['a'],before['a'])


@pytest.mark.parametrize('bad,reason',[
    ('<REDUCE>\nA = 20\nB = 16\nR = A - B\n</REDUCE>\nEND_RUNG\n','not_exactly_one'),
    ('<REDUCE>\nA = 17 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = A - B\n</REDUCE>\nEND_RUNG\n','not_exactly_one'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = B - A\n</REDUCE>\nEND_RUNG\n','register_or_direction'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = A - B\n</REDUCE>\nNOT_DONE','MICRO_MISSING'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = A - B\n</REDUCE>\nEND_RUNG\n<REDUCE>\n','MICRO_MISSING'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = A - B\n</REDUCE>\nEND_RUNG\nMORE\n','MICRO_MISSING'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = H - D\n</REDUCE>\nEND_RUNG','MICRO_DIRECTION'),
    ('<REDUCE>\nA = 16 + (4 * 1)\nB = (8 * 1) + (4 * 2)\nR = A - B\n</REDUCE>\nEND_RUNG\n\nAnswer: 4','MICRO_MISSING'),
])
def test_reject_bad_transitions(bad,reason):
    verdict=m.evaluate_transition(initial(),bad)
    assert not verdict['valid']
    assert reason in verdict['reason']


def test_invalid_ast_does_not_execute():
    for expression in ('__import__("os").system("echo x")', '8 / 2', '2 ** 10', 'X', '8 < 10'):
        with pytest.raises(ValueError,match='MICRO_UNSAFE'):
            m._node(expression)


def test_no_domain_terms_in_full_train_deck():
    deck=m.deck_for(band=(0,4),scales=(8,16,32,64,128,256),seed=71,
           train_count=36,dev_count=12,test_count=12)
    assert {k:len(v) for k,v in deck.items()}=={'train':36,'dev':12,'test':12}
    assert {k:len(m.training_rows(v)) for k,v in deck.items()}=={'train':216,'dev':72,'test':72}
    keys=[{case['key'] for case in deck[s]} for s in ('train','dev','test')]
    assert all(not keys[i].intersection(keys[j]) for i in range(3) for j in range(i+1,3))
    for cases in deck.values():
        assert {0,1,-1}=={0 if c['answer']==0 else 1 if c['answer']>0 else -1 for c in cases}
        for c in cases:
            assert m.result_value(c['state'])==c['answer']
        for row in m.training_rows(cases):
            assert not any(word in (row['prompt']+row['target']).lower()
                 for word in ('hawk','dove','payoff','game','encounter'))
            assert m.evaluate_transition(row['state'],row['target'])['valid']


def test_reproducible_deck_and_balanced_eval():
    args=dict(band=(0,4),scales=(8,16,32,64,128,256),seed=71,
           train_count=36,dev_count=12,test_count=12)
    deck=m.deck_for(**args)
    assert m.deck_hash(deck)==m.deck_hash(m.deck_for(**args))
    dev=m._balanced_dev(m.training_rows(deck['dev']),24)
    assert len(dev)==24
    assert {row['position'] for row in dev}==set(range(6))
    assert {row['expected']>0 for row in dev}=={True,False}


def test_arbitrary_tree_size_one_operation_per_step():
    s=m._state(('X','Y','Z'),'((8 * 2) + (4 * 1)) * ((3 + 1) * 2)',
         '(7 * 3) - (2 * (5 + 1))',True)
    n=m.steps(s)
    assert n==10
    answer=m.result_value(s)
    for _ in range(n):
        nxt=m.reduce_one(s)
        assert m.evaluate_transition(s,m.render(nxt)+'\nEND_RUNG\n')['valid']
        assert m.result_value(nxt)==answer
        s=nxt
    assert m.steps(s)==0


def test_preview_model_free(capsys):
    m.main(['--stage','preview','--device','cpu'])
    raw=capsys.readouterr().out
    res=json.loads(raw.split('=',1)[1])
    assert res['model_loaded'] is False
    assert len(res['example_ladder'])==7
    assert res['micro_rows']=={'train':216,'dev':72,'test':72}


def test_teacher_input_and_free_chain_mocked_probe(tmp_path,monkeypatch,capsys):
    pytest.importorskip('torch')
    from tools import nanojev_qwen_hybridagent_activation as activation
    args=SimpleNamespace(checkpoint=str(tmp_path/'trained'/'best.pt'),output=str(tmp_path/'eval'),
         split='dev',case_limit=1,controls=['text_only'],max_new_tokens=128)
    ck=Path(args.checkpoint);ck.parent.mkdir()
    ck.write_bytes(b'check')
    params=dict(band=(0,4),scales=(8,16,32),seed=71,train_count=12,dev_count=6,test_count=6)
    deck=m.deck_for(**params)
    (ck.parent/'run_config.json').write_text(json.dumps({'schema':m.SCHEMA,
         'sources':{'x':'h'},'frozen_backbone':'f','deck_hash':m.deck_hash(deck),
         **{k:list(v) if isinstance(v,tuple) else v for k,v in params.items()}}))
    class Eval:
        def eval(self):pass
    fake=SimpleNamespace(backbone=Eval(),head=Eval())
    monkeypatch.setattr(m,'_load',lambda *args,**kwargs:(fake,None,{'x':'h'},'f',()))
    def decoder(model,tok,prompt,control,budget,*,stop_at_end_rung):
        assert stop_at_end_rung
        state=m.parse(prompt.split('Current:\n',1)[1].split('\nNext:\n',1)[0])
        return m.target(state)
    monkeypatch.setattr(m.base,'_decode',decoder)
    m.probe(args)
    saved=json.loads((tmp_path/'eval'/'result.json').read_text())
    assert saved['controls']['text_only']=={'one_step_correct':6,'one_step_total':6,
                                      'one_step_accuracy':1.0,'chains_correct':1,'chains_total':1}
    assert saved['generation_calls']==12
    assert not (ck.parent/'test_consumed.json').exists()


def test_probe_stops_at_first_failure(tmp_path,monkeypatch):
    pytest.importorskip('torch')
    args=SimpleNamespace(checkpoint=str(tmp_path/'trained'/'best.pt'),output=str(tmp_path/'eval'),
         split='dev',case_limit=1,controls=['text_only'],max_new_tokens=128)
    ck=Path(args.checkpoint);ck.parent.mkdir();ck.write_bytes(b'check')
    params=dict(band=(0,4),scales=(8,16,32),seed=71,train_count=12,dev_count=6,test_count=6)
    deck=m.deck_for(**params)
    (ck.parent/'run_config.json').write_text(json.dumps({'schema':m.SCHEMA,'sources':{},
        'frozen_backbone':'f','deck_hash':m.deck_hash(deck),
        **{k:list(v) if isinstance(v,tuple) else v for k,v in params.items()}}))
    class Eval:
        def eval(self):pass
    monkeypatch.setattr(m,'_load',lambda *a,**k:(SimpleNamespace(backbone=Eval(),head=Eval()),None,{},'f',()))
    monkeypatch.setattr(m.base,'_decode',lambda *a,**k:'<REDUCE>\nA = 0\nB = 0\nR = A - B\n</REDUCE>\nEND_RUNG\n')
    m.probe(args)
    saved=json.loads((tmp_path/'eval'/'result.json').read_text())
    assert saved['generation_calls']==7
    assert saved['controls']['text_only']['chains_correct']==0


def test_test_split_not_partially_consumed(tmp_path):
    args=SimpleNamespace(checkpoint=str(tmp_path/'trained'/'best.pt'),output=str(tmp_path/'out'),
         split='test',case_limit=1,controls=['text_only'],max_new_tokens=128)
    ck=Path(args.checkpoint);ck.parent.mkdir();ck.write_bytes(b'check')
    params=dict(band=(0,4),scales=(8,16,32),seed=71,train_count=12,dev_count=6,test_count=6)
    deck=m.deck_for(**params)
    (ck.parent/'run_config.json').write_text(json.dumps({'schema':m.SCHEMA,'sources':{},
        'frozen_backbone':'f','deck_hash':m.deck_hash(deck),
        **{k:list(v) if isinstance(v,tuple) else v for k,v in params.items()}}))
    with pytest.raises(RuntimeError,match='COMPLETE_SPLIT'):
        m.probe(args)
