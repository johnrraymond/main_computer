"""Anti-template-leakage and genuinely unseen-game smoke query tests."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_prompt_db_train as trainer
from tools import nanojev_gamer_v1_prompt_db_query as probe


@pytest.fixture(scope='module')
def artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp('query_and_audit')
    db = root / 'all.sqlite3'
    smoke.build(db, cases_per_game=6, catalog_prompts_per_game=2, seed=47)
    folder = root / 'train'
    trainer.train(db, folder, epochs=2, seed=47)
    return db, folder


def test_reported_test_is_same_masked_training_templates(artifacts):
    rows, _ = trainer.load_training_rows(artifacts[0])
    overlap = probe.template_overlap(rows)
    assert overlap['certified_test_cases'] == 146
    assert overlap['masked_test_template_matches_training'] == 146
    assert overlap['test_games_also_present_in_training'] == 146
    assert overlap['distinct_certified_test_games'] == 146


def test_query_uses_saved_weights_and_no_oracle_at_inference(artifacts):
    db, folder = artifacts
    with sqlite3.connect(db) as conn:
        raw, = conn.execute("SELECT prompt FROM prompts WHERE split='train' LIMIT 1").fetchone()
    result = probe.query(db, folder, raw)
    source = trainer.score_checkpoint(folder / 'strategy_router.npz', raw,
                                      report=folder / 'final_report.json')
    assert result['status'] == 'UNLABELLED_ADVISORY_QUERY_NOT_STRATEGIC_CERTIFICATION'
    assert result['prediction']['family'] == source['family']
    assert result['prediction']['scope'] == source['scope']
    assert result['prediction']['operator_pattern'] == source['operator_pattern']
    assert result['prediction']['strategic_decision'] is None
    assert result['prediction']['numerical_answer'] is None
    assert result['novelty']['exact_training_prompt_matches'] >= 1
    assert result['novelty']['masked_training_template_matches'] >= 1
    assert result['novelty']['is_new_masked_template'] is False
    assert result['database_used_after_prediction_for_novelty_only']


def test_query_flags_template_reuse_despite_changed_numbers(artifacts):
    db, folder = artifacts
    with sqlite3.connect(db) as conn:
        raw, = conn.execute("SELECT prompt FROM prompts WHERE split='test' LIMIT 1").fetchone()
    result = probe.query(db, folder, raw)
    assert result['novelty']['exact_training_prompt_matches'] == 0
    assert result['novelty']['masked_training_template_matches'] >= 1
    assert result['novelty']['is_new_masked_template'] is False



def test_changed_numbers_do_not_create_new_program_evidence(artifacts):
    db, folder = artifacts
    with sqlite3.connect(db) as conn:
        raw, = conn.execute("SELECT prompt FROM prompts WHERE split='train' AND numerical_training_eligible=1 LIMIT 1").fetchone()
    assert trainer.NUMERIC.search(raw)
    changed = trainer.NUMERIC.sub('999', raw)
    assert changed != raw
    baseline = probe.query(db, folder, raw)
    altered = probe.query(db, folder, changed)
    assert altered['novelty']['exact_training_prompt_matches'] == 0
    assert altered['novelty']['masked_training_template_matches'] > 0
    assert altered['prediction'] == baseline['prediction']
    assert altered['numerical_values_masked_as_in_training']

def test_open_prompt_is_advisory_and_no_invented_decision(artifacts):
    db, folder = artifacts
    raw = 'Two neighboring agents decide whether to disclose information or withhold it while sharing the same uncertain resource.'
    result = probe.query(db, folder, raw)
    assert result['novelty']['masked_training_template_matches'] == 0
    assert result['prediction']['numerical_answer'] is None
    assert result['prediction']['strategic_decision'] is None
    assert result['production_authority_changed'] is False


def test_database_and_checkpoint_lineage_refuses_tampering(artifacts, tmp_path):
    db, folder = artifacts
    altered_db = tmp_path / 'changed.sqlite3'
    shutil.copyfile(db, altered_db)
    with sqlite3.connect(altered_db) as conn:
        conn.execute("UPDATE prompts SET numeric_answer='999' WHERE prompt_id=(SELECT prompt_id FROM prompts WHERE numerical_training_eligible=1 LIMIT 1)")
    with pytest.raises(ValueError, match='FINGERPRINT_MISMATCH'):
        probe.query(altered_db, folder, 'One-shot coordination game')
    checkpoint = folder / 'strategy_router.npz'
    original = checkpoint.read_bytes()
    try:
        with checkpoint.open('ab') as fp:
            fp.write(b'tamper')
        with pytest.raises(ValueError, match='FINGERPRINT_MISMATCH'):
            probe.query(db, folder, 'One-shot coordination game')
    finally:
        checkpoint.write_bytes(original)
    with pytest.raises(ValueError, match='PROMPT_MUST_BE'):
        probe.query(db, folder, '  ')


def test_heldout_game_partition_and_masking(artifacts):
    rows, _ = trainer.load_training_rows(artifacts[0])
    heldout = probe._select_heldout_games(rows, seed=47)
    assert 2 <= len(heldout) <= 46
    training = [r for r in rows if r['split']=='train' and r['game'] not in heldout]
    evaluation = [r for r in rows if r['game'] in heldout and r['split']=='test']
    assert evaluation
    assert not {r['game'] for r in training} & {r['game'] for r in evaluation}
    assert not {r['text'] for r in training} & {r['text'] for r in evaluation}
    sample = smoke.source_registry()['adversarial_bandits']
    s = probe._blind_text('An example of ' + sample[0] + '.', 'adversarial_bandits', smoke.source_registry())
    assert sample[0].lower() not in s.lower()


def test_independent_audit_retrains_without_touching_saved_checkpoint(artifacts, tmp_path):
    db, folder = artifacts
    before = trainer._digest(folder / 'strategy_router.npz')
    db_before = trainer._digest(db)
    out = tmp_path / 'audit.json'
    report = probe.audit(db, folder, epochs=2, seed=47, report_path=out)
    assert out.is_file()
    assert report['original_split']['masked_test_template_match_rate'] == 1.0
    holdout = report['independent_game_holdout_probe']
    assert holdout['no_training_examples_from_evaluation_games']
    assert holdout['exact_masked_template_overlap'] == 0
    assert holdout['heldout_games'] >= 2
    assert holdout['heldout_certified_games'] >= 1
    assert holdout['ordinary_text']['program']['cases'] > 0
    assert holdout['game_and_family_cue_redacted']['program']['cases'] > 0
    assert report['does_not_certify_full_game_decisions']
    assert report['no_original_checkpoint_modification']
    assert trainer._digest(folder / 'strategy_router.npz') == before
    assert trainer._digest(db) == db_before
    persisted = json.loads(out.read_text(encoding='utf-8'))
    assert persisted['original_split'] == report['original_split']
    # Repeating the same command must succeed, without touching its report.
    old_bytes = out.read_bytes()
    same = probe.audit(db, folder, epochs=2, seed=47, report_path=out)
    assert same['report_reused_verified_identical'] is True
    assert out.read_bytes() == old_bytes
    assert same['original_split'] == report['original_split']
    # Changing the audit seed/epochs must not silently bless stale evidence.
    with pytest.raises(FileExistsError, match='DIFFERENT_RESULT_USE_NEW_PATH'):
        probe.audit(db, folder, epochs=1, report_path=out)
    assert out.read_bytes() == old_bytes
    with pytest.raises(ValueError, match='IMMUTABLE_TRAIN_RUN'):
        probe.audit(db, folder, epochs=1, report_path=folder/'new.json')


def test_cli_query_and_audit_commands(artifacts, tmp_path, capsys):
    db, folder = artifacts
    result = smoke.main(['query', '--output',str(db), '--train-output',str(folder),
                        '--prompt','Each player chooses between two actions with uncertain payoffs.'])
    assert result['prediction']['strategic_decision'] is None
    assert 'UNLABELLED_ADVISORY_QUERY' in capsys.readouterr().out
    audited = smoke.main(['audit','--output',str(db),'--train-output',str(folder),
                          '--audit-report',str(tmp_path/'report.json'),'--epochs','2'])
    assert audited['independent_game_holdout_probe']['heldout_games'] > 0
    assert 'COMPLETE_READ_ONLY' in capsys.readouterr().out


def test_audit_existing_corrupt_or_modified_report_fails_closed(artifacts, tmp_path):
    db, folder = artifacts
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('{broken', encoding='utf-8')
    with pytest.raises(ValueError, match='EXISTING_INVALID'):
        probe.audit(db, folder, seed=47, epochs=1, report_path=invalid)
    assert invalid.read_text(encoding='utf-8') == '{broken'
    wrong = tmp_path / 'wrong.json'
    wrong.write_text(json.dumps({'schema': probe.AUDIT_SCHEMA}), encoding='utf-8')
    with pytest.raises(FileExistsError, match='DIFFERENT_RESULT_USE_NEW_PATH'):
        probe.audit(db, folder, seed=47, epochs=1, report_path=wrong)
    assert json.loads(wrong.read_text(encoding='utf-8')) == {'schema': probe.AUDIT_SCHEMA}


def test_audit_reuse_checks_source_lineage_again(artifacts, tmp_path):
    db, folder = artifacts
    report = tmp_path / 'audit.json'
    probe.audit(db, folder, seed=47, epochs=1, report_path=report)
    first = report.read_bytes()
    checkpoint = folder / 'strategy_router.npz'
    original = checkpoint.read_bytes()
    try:
        with checkpoint.open('ab') as fp:
            fp.write(b'tamper')
        with pytest.raises(ValueError, match='FINGERPRINT_MISMATCH'):
            probe.audit(db, folder, seed=47, epochs=1, report_path=report)
    finally:
        checkpoint.write_bytes(original)
    assert report.read_bytes() == first
