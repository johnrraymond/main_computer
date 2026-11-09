"""V41: prospectively disjoint surface families and sealed-audit protections."""
from __future__ import annotations

import inspect
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v41_surface_generalization as v41
from tools import nanojev_gamer_v1_v40_actor_grounding as v40
from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34


def test_fit_and_dev_are_surface_disjoint_not_just_example_disjoint():
    fit=v41.make_deck('fit')
    dev=v41.make_deck('dev')
    assert len(fit)==480 and len(dev)==120
    assert {r['table_style'] for r in fit}==set(range(8))
    assert {r['table_style'] for r in dev}=={8,9,10}
    assert {r['belief_style'] for r in fit}==set(range(8))
    assert {r['belief_style'] for r in dev}=={8,9,10}
    assert {r['prompt'] for r in fit}.isdisjoint({r['prompt'] for r in dev})
    assert len({r['prompt'] for r in fit+dev})==600
    assert {r['pair'] for r in fit}.isdisjoint({r['pair'] for r in dev})
    assert set(v41.SPLITS['test']['tables']).isdisjoint({*range(11)})
    assert set(v41.SPLITS['test']['beliefs']).isdisjoint({*range(11)})


def test_training_family_contains_originally_failed_v40_formats_as_consumed_history():
    assert v41.SPLITS['fit']['tables']==tuple(range(8))
    assert v41.SPLITS['fit']['beliefs']==tuple(range(8))
    # Previously unopened new prospective families cannot be part of fit or DEV.
    assert set(v41.SPLITS['test']['tables']).isdisjoint(
        set(v41.SPLITS['fit']['tables'])|set(v41.SPLITS['dev']['tables']))
    assert set(v41.SPLITS['test']['beliefs']).isdisjoint(
        set(v41.SPLITS['fit']['beliefs'])|set(v41.SPLITS['dev']['beliefs']))


def test_counterfactuals_preserve_identical_numbers_with_varying_semantics():
    for split in ('fit','dev'):
        rows=v41.make_deck(split)
        for a,b in zip(rows[::2],rows[1::2]):
            assert a['pair']==b['pair']
            p,q=v40.transcribe(a['prompt'])
            p2,q2=v40.transcribe(b['prompt'])
            assert [x['value'] for x in p]==[x['value'] for x in p2]
            assert q['value']==q2['value']
            if a['intervention']=='belief':
                assert a['roles']==b['roles'] and a['q_side']!=b['q_side']
            else:
                assert a['roles']!=b['roles'] and a['q_side']==b['q_side']


def test_decks_balanced_and_labels_not_lexical_shortcuts():
    for split in ('fit','dev'):
        rows=v41.make_deck(split)
        assert abs(sum(r['label'] for r in rows)-len(rows)/2)<=12
        interventions=Counter(r['intervention'] for r in rows)
        assert all(interventions.get(k,0)>=20 for k in ('belief','ours','theirs','actor_exchange'))
        assert all(v41.DEFINITION in r['prompt'] for r in rows)
        assert all('3/4' in r['prompt'] for r in rows)
        assert all(len(v40.transcribe(r['prompt'])[0])==4 for r in rows)


def test_dev_is_held_out_on_sentence_layout_including_number_first():
    dev=v41.make_deck('dev')
    assert any('A result of ' in r['prompt'] for r in dev)
    assert any('If their move is ' in r['prompt'] for r in dev)
    assert any('For our ' in r['prompt'] for r in dev)
    assert not any(r['table_style']<8 for r in dev)
    assert v41.gate({'structural_exact':108,'decision_exact':108,'belief_exact':108,
                     'pairs_exact':54,'numeric_only_accuracy_ceiling':100},v41.DEV)
    assert not v41.gate({'structural_exact':107,'decision_exact':120,'belief_exact':120,
                         'pairs_exact':60,'numeric_only_accuracy_ceiling':50},v41.DEV)
    assert not v41.gate({'structural_exact':120,'decision_exact':120,'belief_exact':107,
                         'pairs_exact':60,'numeric_only_accuracy_ceiling':50},v41.DEV)


def test_prediction_does_not_call_any_label_oracle(monkeypatch):
    prompt=v41.make_deck('fit')[0]['prompt']
    def forbidden(*args,**kwargs):
        raise AssertionError('label oracle or surface parser used in prediction')
    monkeypatch.setattr(v41,'_table',forbidden)
    monkeypatch.setattr(v41,'_belief',forbidden)
    monkeypatch.setattr(v41,'make_deck',forbidden)
    monkeypatch.setattr(v33,'exact_teacher',forbidden)
    monkeypatch.setattr(v34,'ground',forbidden)
    net=v40.head(torch,16).eval()
    x=np.random.default_rng(41).normal(size=(1,4,16)).astype('float32')
    q=np.random.default_rng(42).normal(size=(1,16)).astype('float32')
    role,side=v40.predict_slots(net,x,q)
    assert sorted(role[0])==[0,1,2,3]
    assert v40.numeric_input(prompt,role[0],side[0]).shape==(5,)


def test_failed_dev_cannot_open_prospective_sealed_deck(tmp_path,monkeypatch):
    lineage={'model_id':'fake-qwen','source_files':{},'base_activation':'frozen-base'}
    monkeypatch.setattr(v33,'activation',lambda project:lineage)
    monkeypatch.setattr(v33,'verify_activation',lambda project,lineage:lineage)
    monkeypatch.setattr(v33,'model_context',lambda *args:(torch,object(),object()))
    monkeypatch.setattr(v40,'_load_v34',lambda *args:({},v34.head(torch)))
    monkeypatch.setattr(v40,'capture',lambda m,tok,t,prompts,**kw:(
        np.ones((len(prompts),4,8),dtype='float32'),
        np.ones((len(prompts),8),dtype='float32')))
    monkeypatch.setattr(v40,'fit_head',lambda *args,**kwargs:(
        v40.head(torch,8).state_dict(),[],(0,0,0),
        ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    base=tmp_path/'v34';base.mkdir()
    (base/'grounded_head.pt').write_bytes(b'protected-v34')
    (base/'train_manifest.json').write_text('{}')
    root=tmp_path/'v41'
    result=v41.train(root,v34_root=base,device='cpu',project=tmp_path)
    assert result['status']=='FAIL_OOD_DEV_TEST_STILL_SEALED'
    assert result['test_evaluated'] is False
    assert not (root/'holdout_consumed.json').exists()
    assert not (root/'holdout_deck.json').exists()
    assert not (root/'holdout_acceptance.json').exists()
    original=v41.make_deck
    def forbid_holdout(split,*args,**kwargs):
        if split=='test':
            raise AssertionError('sealed deck generated')
        return original(split,*args,**kwargs)
    monkeypatch.setattr(v41,'make_deck',forbid_holdout)
    with pytest.raises(ValueError,match='DEV_GATE_FAILED_TEST_STILL_SEALED'):
        v41.audit(root,project=tmp_path,device='cpu')
    assert not (root/'holdout_consumed.json').exists()


def test_lineage_and_test_artifact_tamper_rejected(tmp_path,monkeypatch):
    # Reuse a compact fabricated but structurally valid checkpoint to exercise
    # tamper gates. This is NOT a synthetic claim of model accuracy.
    lineage={'model_id':'fake-qwen','source_files':{},'base_activation':'frozen-base'}
    monkeypatch.setattr(v33,'activation',lambda project:lineage)
    monkeypatch.setattr(v33,'verify_activation',lambda project,lineage:lineage)
    monkeypatch.setattr(v33,'model_context',lambda *args:(torch,object(),object()))
    monkeypatch.setattr(v40,'_load_v34',lambda *args:({},v34.head(torch)))
    monkeypatch.setattr(v40,'capture',lambda m,tok,t,prompts,**kw:(
        np.ones((len(prompts),4,8),dtype='float32'),np.ones((len(prompts),8),dtype='float32')))
    monkeypatch.setattr(v40,'fit_head',lambda *args,**kwargs:(
        v40.head(torch,8).state_dict(),[],(0,0,0),
        ([(0,1,2,3)]*120,[0]*120,[0]*120)))
    base=tmp_path/'v34';base.mkdir()
    (base/'grounded_head.pt').write_bytes(b'protected-v34')
    (base/'train_manifest.json').write_text('{}')
    root=tmp_path/'v41'
    v41.train(root,v34_root=base,device='cpu',project=tmp_path)
    v41.load(root,project=tmp_path)
    (root/'train_dev_provenance.json').write_text('{}')
    with pytest.raises(ValueError,match='LINEAGE_CHANGED'):
        v41.load(root,project=tmp_path)


def test_prospective_sealed_generator_is_not_invoked_during_training():
    s=inspect.getsource(v41.train)
    assert "make_deck('test'" not in s
    assert 'v40_failed_holdout_read' in s
    assert "make_deck('test'" in inspect.getsource(v41.audit)
    assert "holdout_consumed.json" in inspect.getsource(v41.audit)
    for fn in (v41._scores,v40.predict_slots,v40.numeric_input,v40.run_decisions):
        source=inspect.getsource(fn)
        assert 'exact_teacher(' not in source
        assert 'ground(' not in source


def test_default_parser_requires_only_py_not_deployment():
    import argparse
    assert v41.DEV['structural_exact']==108
    assert v41.TEST['structural_exact']==180
    assert all(v41.SPLITS[s]['tables'] for s in v41.SPLITS)
