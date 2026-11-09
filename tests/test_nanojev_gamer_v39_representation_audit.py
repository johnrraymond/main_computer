"""V39 is a DEV-only information audit, not a new promoted classifier."""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import json
import random

import numpy as np
import pytest
import torch

from tools import nanojev_gamer_v1_v38_interacting_role_grounding as v38
from tools import nanojev_gamer_v1_v39_representation_audit as v39


def test_v39_quartiles_are_exact_frozen_feature_slices_and_refuse_wrong_shape():
    source=np.arange(2*4*12,dtype=np.float32).reshape(2,4,12)
    got=v39._quartiles(source)
    assert list(got)==list(v39.SEGMENTS)
    for k,name in enumerate(v39.SEGMENTS):
        np.testing.assert_array_equal(got[name],source[:,:,k*3:(k+1)*3])
        got[name][:]=0
        assert np.any(source[:,:,k*3:(k+1)*3])  # copies, not a mutation
    with pytest.raises(ValueError,match='GAMER_V38_V39_INVALID_FEATURE_LAYOUT'):
        v39._quartiles(np.zeros((3,4,13),dtype=np.float32))


def test_v39_error_accounting_does_not_hide_belief_or_role_errors():
    _,dev=v38.fit_partition(v38.make_deck('fit',35))
    correct=[tuple(x['roles']) for x in dev]
    sides=[x['q_side'] for x in dev]
    stat=v39._summary(dev,correct,sides)
    assert stat['structural_exact']==120
    assert stat['role_exact']==120 and stat['role_slots']==480
    assert stat['belief_exact']==120
    bad=correct.copy()
    bad[0]=tuple(reversed(bad[0]))
    wrong=sides.copy()
    wrong[1]=1-wrong[1]
    errors=v39.diagnostic_by_row(dev,bad,wrong)
    assert len(errors)==2
    assert all(e['prompt']==dev[i]['prompt'] for i,e in enumerate(errors))
    got=v39._summary(dev,bad,wrong)
    assert got['structural_exact']==118 and got['belief_exact']==119
    assert sum(s['total'] for s in got['by_style'].values())==120
    assert sum(s['total'] for s in got['by_intervention'].values())==120


def test_v39_linear_probe_train_only_fits_known_semantics_without_dev_labels(monkeypatch):
    fit,dev=v38.fit_partition(v38.make_deck('fit',35))
    # All role assignments deliberately encoded in synthetic hidden vectors;
    # the probe must learn roles using TRAIN alone and generalize to DEV.
    rng=np.random.default_rng(57)
    x=np.zeros((480,4,10),dtype=np.float32)
    dx=np.zeros((120,4,10),dtype=np.float32)
    for inp,rows in ((x,fit),(dx,dev)):
        for i,r in enumerate(rows):
            for j,role in enumerate(r['roles']):
                inp[i,j,role]=1.
        inp[:]+=rng.normal(0,.005,size=inp.shape).astype(np.float32)
    correct=v39._linear_role_probe(x,fit,dx,epochs=40)
    assert sum(tuple(r)==tuple(row['roles']) for row,r in zip(dev,correct))>=115
    shuffled=v39._linear_role_probe(x,fit,dx,epochs=40,shuffle_labels=True)
    assert sum(tuple(r)==tuple(row['roles']) for row,r in zip(dev,shuffled))<80


def test_v39_audit_refuses_passed_model_before_any_capture(monkeypatch,tmp_path):
    monkeypatch.setattr(v38,'load_head',lambda *_args,**_kw:
        ({'dev_pass':True,'train_variants':list(v38.TRAIN_VARIANTS),
          'sealed_variants':list(v38.TEST_VARIANTS)},object()))
    monkeypatch.setattr(v39.v33,'model_context',lambda *_args,**_kw:
        (_ for _ in ()).throw(AssertionError('MUST_NOT_CAPTURE')))
    with pytest.raises(ValueError,match='GAMER_V38_V39_V38_FAILED_DEV_REQUIRED'):
        v39.audit(SimpleNamespace(v38_run=tmp_path/'v38',output=tmp_path/'audit',
                   root='.',device='cpu',batch_size=4,probe_seed=3901,probe_epochs=40))
    assert not (tmp_path/'audit').exists()


def test_v39_source_never_constructs_sealed_deck_and_only_reads_fit_deck():
    source=Path(v39.__file__).read_text()
    assert "v38.make_deck('fit',manifest['seed'])" in source
    assert "make_deck('holdout'" not in source
    assert "v38.fit_partition(v38.make_deck('fit',manifest['seed']))" in source
    assert "v38.load_head(args.v38_run,args.root,audit_required=False)" in source


def test_v39_end_to_end_mock_refuses_any_holdout_capture_and_writes_diagnostics(monkeypatch,tmp_path):
    fit,dev=v38.fit_partition(v38.make_deck('fit',35))
    original_make=v38.make_deck
    seen=[]
    def only_fit(split,seed):
        seen.append(split)
        assert split=='fit'
        return original_make(split,seed)
    monkeypatch.setattr(v38,'make_deck',only_fit)
    manifest={'dev_pass':False,'train_variants':list(v38.TRAIN_VARIANTS),
              'sealed_variants':list(v38.TEST_VARIANTS),'seed':35,
              'feature_width':8,'fit_hash':v38.digest(fit),
              'dev_hash':v38.digest(dev),'model_id':'local','lineage':{'id':'fake'}}
    monkeypatch.setattr(v38,'load_head',lambda *_args,**_kw:(manifest,object()))
    monkeypatch.setattr(v38,'read',lambda *_args,**_kw:
        {'status':'FAIL_DEV_HOLDOUT_STILL_SEALED','holdout_evaluated':False,
         'dev':{'structural_exact':120}})
    monkeypatch.setattr(v38,'sha',lambda *_args,**_kw:'mock-checkpoint-sha256')
    monkeypatch.setattr(v39.v33,'model_context',lambda *_args,**_kw:(torch,object(),object()))
    monkeypatch.setattr(v39.v33,'verify_activation',lambda *_args,**_kw:None)
    def fake_capture(_model,_tok,_torch,prompts,*,device,batch_size,tag):
        assert len(prompts) in (480,120)
        return (np.ones((len(prompts),4,8),dtype=np.float32),
                np.ones((len(prompts),8),dtype=np.float32))
    monkeypatch.setattr(v38,'capture',fake_capture)
    monkeypatch.setattr(v38,'predict_slots',lambda _net,x,q:
        ([tuple(r['roles']) for r in (fit if len(x)==480 else dev)],
         [r['q_side'] for r in (fit if len(x)==480 else dev)]))
    # Linear probe returns a well-formed prediction but remains diagnostic.
    monkeypatch.setattr(v39,'_linear_role_probe',lambda *_args,**_kw:
        [tuple(r['roles']) for r in dev])
    out=tmp_path/'new-audit'
    result=v39.audit(SimpleNamespace(v38_run=tmp_path/'failed-v38',
        output=out,root='.',device='cpu',batch_size=4,
        probe_seed=3901,probe_epochs=10))
    assert result['status']=='DEV_ONLY_REPRESENTATION_DIAGNOSTIC_NO_PROMOTION'
    assert result['holdout_accessed'] is False and result['production_modified'] is False
    assert result['fresh_capture_reproduced_original'] is True
    assert result['original_v38_dev']['structural_exact']==120
    assert len(result['linear_probe_by_input'])==5
    assert result['dev_errors']==[] and seen==['fit']
    assert json.loads((out/'representation_audit.json').read_text())==result
