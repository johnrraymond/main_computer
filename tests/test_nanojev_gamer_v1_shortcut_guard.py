"""Cue-blind GAMER checks: preserve known-case recall without false generalization."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_prompt_db_train as trainer
from tools import nanojev_gamer_v1_prompt_db_query as queries
from tools import nanojev_gamer_v1_prompt_db_deconfound as guard


@pytest.fixture(scope='module')
def source():
    reg=smoke.source_registry()
    return (reg,guard.titles_from_registry(reg),guard.family_names_from_registry(smoke.FAMILY_MODELS))


def test_game_title_swap_is_cue_blind(source):
    _,titles,families=source
    base='Players may choose between two actions. The first payoff is 4 and the second is 7.'
    a=f'Consider a two-player Prisoners Dilemma: {base}'
    b=f'Consider a two-player Anti-coordination: {base}'
    assert guard.blind_text(a,titles,families)==guard.blind_text(b,titles,families)
    assert 'Prisoners Dilemma' not in guard.blind_text(a,titles,families)
    assert 'Anti-coordination' not in guard.blind_text(b,titles,families)


def test_catalog_labels_equations_are_not_model_features(source):
    reg,titles,families=source
    game=next(g for g,(_,_,scope) in reg.items() if scope=='catalog_only')
    title,family,_=reg[game]
    for kind in (0,1):
        raw=smoke.catalog_prompt(title,family,kind)
        clean=guard.blind_text(raw,titles,families)
        assert title not in clean
        assert smoke.FAMILY_MODELS[family][0] not in clean
        assert smoke.FAMILY_MODELS[family][1] not in clean
        assert 'numerical' in clean.lower()


def test_deconfounded_feature_does_not_depend_on_label(source):
    _,titles,families=source
    text='Consider a two-player Prisoners Dilemma: one payoff is -10 and another is 3.'
    assert guard.blind_text(text,titles,families)==guard.blind_text(text,titles,families)
    assert guard.blind_text(text,titles,families)!=text
    assert 'NUMVALUE' in guard.blind_text(text,titles,families)


@pytest.fixture(scope='module')
def trained(tmp_path_factory):
    root=tmp_path_factory.mktemp('cue_blind')
    db=root/'prompts.sqlite3'
    smoke.build(db,cases_per_game=6,catalog_prompts_per_game=2,seed=47)
    run=root/'run'
    report=trainer.train(db,run,epochs=2,seed=47)
    return db,run,report


def test_new_checkpoint_is_cue_blind_and_has_independent_diagnostic(trained):
    db,run,report=trained
    assert report['schema']==trainer.SCHEMA
    assert report['input_feature_contract']==guard.SCHEMA
    assert report['certified_test_masked_feature_template_overlap']==146
    assert not report['certified_test_is_independent_generalization_evidence']
    assert not report['catalog_only_family_recognition_certified']
    assert report['metrics']['family']['test']['cases']==146
    probe=report['game_disjoint_probe']
    assert probe['evaluation_feature_overlap']==0
    assert probe['heldout_games']==46
    assert probe['metrics']['family']['cases']==probe['certified_evaluation_games']
    assert probe['metrics']['program']['cases']==probe['certified_evaluation_games']
    assert probe['separate_diagnostic_model_not_saved_checkpoint']
    assert trainer._digest(run/'strategy_router.npz')==report['checkpoint_sha256']
    with np.load(run/'strategy_router.npz',allow_pickle=False) as weights:
        meta=json.loads(str(weights['meta_json']))
    assert meta['input_feature_contract']==guard.SCHEMA
    assert len(meta['cue_titles'])==len({title for title,_,_ in smoke.source_registry().values()})
    assert trainer._digest(db)==report['database_sha256']


def test_new_model_uses_same_features_for_game_name_swap(trained):
    db,run,report=trained
    p='Consider a two-player Prisoners Dilemma: first payoff 5, second payoff 8.'
    q=p.replace('Prisoners Dilemma','Anti-coordination')
    a=queries.query(db,run,p)
    b=queries.query(db,run,q)
    assert a['prediction']==b['prediction']
    assert a['direct_game_title_and_family_cues_removed']
    assert not a['numeric_payoff_sensitive_decision_produced']
    assert a['prediction']['strategic_decision'] is None
    assert a['lineage']==b['lineage']
    assert a['novelty']['model_feature_training_matches']==b['novelty']['model_feature_training_matches']


def test_new_model_rejects_different_payoff_as_decision_proof(trained):
    db,run,_=trained
    template='Two opponents choose actions. The penalty for defecting is {} points.'
    outputs=[queries.query(db,run,template.format(x)) for x in [1,2,3]]
    assert len({tuple(sorted(x['prediction'].items())) for x in outputs})==1
    assert all(x['prediction']['numerical_answer'] is None for x in outputs)
    assert all(not x['numeric_payoff_sensitive_decision_produced'] for x in outputs)


def test_audit_v2_remains_read_only(trained,tmp_path):
    db,run,report=trained
    original=trainer._digest(run/'strategy_router.npz')
    out=tmp_path/'cue_blind_audit.json'
    audited=queries.audit(db,run,seed=47,epochs=1,report_path=out)
    assert audited['cue_blind_checkpoint']['no_numeric_decision_claim']
    assert audited['cue_blind_checkpoint']['masked_test_template_matches_training']==146
    assert trainer._digest(run/'strategy_router.npz')==original
    assert queries.audit(db,run,seed=47,epochs=1,report_path=out)['report_reused_verified_identical']
