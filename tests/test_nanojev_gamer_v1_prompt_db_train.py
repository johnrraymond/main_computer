"""Actual trainer, full catalog admission, no leakage or active-checkpoint writes."""
from __future__ import annotations
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
import pytest
from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_prompt_db_train as trainer

@pytest.fixture(scope='module')
def smoke_db(tmp_path_factory):
    path=tmp_path_factory.mktemp('prompt_db_training')/'all.sqlite3'
    smoke.build(path,cases_per_game=6,catalog_prompts_per_game=2,seed=47)
    return path

@pytest.fixture(scope='module')
def trained(smoke_db,tmp_path_factory):
    path=tmp_path_factory.mktemp('isolated_gamer_training')/'router'
    report=trainer.train(smoke_db,path,epochs=3,seed=47)
    return path,report


def test_train_reads_all_229_games_and_reserves_splits(smoke_db):
    rows,verified=trainer.load_training_rows(smoke_db)
    assert len(rows)==1042 and verified['games']==229
    assert len({r['game'] for r in rows})==229
    assert len({r['family'] for r in rows})==23
    assert sum(r['catalog_only'] and r['split']=='train' for r in rows)==83
    assert sum(r['catalog_only'] and r['split']=='dev' for r in rows)==83
    assert sum(r['catalog_only'] and r['split']=='test' for r in rows)==0
    assert sum(not r['catalog_only'] and r['split']=='train' for r in rows)==584
    assert sum(not r['catalog_only'] and r['split']=='dev' for r in rows)==146
    assert sum(not r['catalog_only'] and r['split']=='test' for r in rows)==146
    assert not any(r['catalog_only'] and r['program'] is not None for r in rows)
    assert 29==len({r['program'] for r in rows if r['program']})
    assert all('answer' not in r and 'math_target' not in r for r in rows)


def test_real_optimizer_creates_isolated_weights_and_metrics(smoke_db,trained):
    dest,report=trained
    assert report['status']=='COMPLETE_ISOLATED_GAME_PLANNING_SMOKE_TRAIN'
    assert report['no_lora_training'] and report['no_c0_training'] and report['no_wide_training']
    assert report['active_exact_numerical_authority_unchanged']
    assert report['splits']['catalog_train']==83
    assert report['splits']['catalog_dev']==83
    assert report['label_classes']['program']==29
    assert report['training_rows']==667
    assert report['metrics']['program']['test']['cases']==146
    assert 0<=report['metrics']['program']['test']['accuracy']<=1
    assert (dest/'final_report.json').is_file() and (dest/'strategy_router.npz').is_file()
    assert trainer._digest(dest/'strategy_router.npz')==report['checkpoint_sha256']
    assert trainer._digest(smoke_db)==report['database_sha256']
    assert sorted(p.name for p in dest.iterdir())==['final_report.json','strategy_router.npz']


def test_checkpoint_replays_predictions_without_oracle_or_prompt_labels(trained):
    folder,_=trained
    predicted=trainer.score_checkpoint(folder/'strategy_router.npz',
        'Consider a bounded instance of Adversarial Bandits. Best reward 10, chosen reward 2, prior regret 10.',
        report=folder/'final_report.json')
    assert predicted['status']=='ADVISORY_UNCERTIFIED_PROMPT_ROUTING'
    assert predicted['numerical_answer'] is None
    assert predicted['production_authority_changed'] is False
    assert predicted['operator_pattern'] is None or isinstance(predicted['operator_pattern'],str)
    assert predicted['checkpoint_sha256']==trainer._digest(folder/'strategy_router.npz')


def test_checkpoint_tampering_is_refused(trained,tmp_path):
    folder,_=trained
    broken=tmp_path/'modified.npz'
    shutil.copyfile(folder/'strategy_router.npz',broken)
    with broken.open('ab') as stream:stream.write(b'MODIFIED')
    with pytest.raises(ValueError,match='FINGERPRINT_MISMATCH'):
        trainer.score_checkpoint(broken,'Game: Chess',report=folder/'final_report.json')
    with pytest.raises(ValueError,match='EMPTY_INFERENCE_PROMPT'):
        trainer.score_checkpoint(folder/'strategy_router.npz','   ')


def test_trainer_refuses_overwrite_and_does_not_touch_db(smoke_db,trained):
    before=trainer._digest(smoke_db)
    with pytest.raises(FileExistsError,match='ALREADY_EXISTS'):
        trainer.train(smoke_db,trained[0],epochs=2)
    with pytest.raises(ValueError,match='EPOCHS_OUT_OF_RANGE'):
        trainer.train(smoke_db,smoke_db.parent/'does-not-matter',epochs=0)
    assert trainer._digest(smoke_db)==before


def test_verification_refuses_sqlite_numeric_label_tampering(smoke_db,tmp_path):
    broken=tmp_path/'bad.sqlite3'
    shutil.copyfile(smoke_db,broken)
    with sqlite3.connect(broken) as con:
        con.execute("UPDATE prompts SET numeric_answer='999' WHERE prompt_id=(SELECT prompt_id FROM prompts WHERE numerical_training_eligible=1 LIMIT 1)")
    with pytest.raises(ValueError,match='CERTIFIED_SOURCE_MISMATCH'):
        trainer.load_training_rows(broken)


def test_one_catalog_prompt_per_game_is_not_misreported_as_holdout(tmp_path):
    db=tmp_path/'one.sqlite3'
    smoke.build(db,cases_per_game=6,catalog_prompts_per_game=1)
    with pytest.raises(ValueError,match='REQUIRES_TWO_CATALOG_PROMPTS'):
        trainer.load_training_rows(db)


def test_cli_train_and_score_smoke(smoke_db,tmp_path,capsys):
    dest=tmp_path/'cli_train'
    report=smoke.main(['train','--output',str(smoke_db),'--train-output',str(dest),'--epochs','2'])
    assert report['training_rows']==667
    result=smoke.main(['score','--output',str(smoke_db),'--train-output',str(dest),
                       '--prompt','A bounded one-step risk assessment with known payoffs.'])
    assert result['numerical_answer'] is None
    assert 'ADVISORY_UNCERTIFIED' in capsys.readouterr().out


def test_numerical_digits_masked_without_changing_game_descriptions():
    assert trainer._sanitize('Regret 10/23 of 32.5 plus -5 in Risk Game')=='Regret NUMVALUE of NUMVALUE plus NUMVALUE in Risk Game'


def test_train_command_autobuilds_complete_default_db_and_trains(tmp_path, capsys):
    """The *unchanged* user-facing train command must bootstrap all 229 games."""
    db = tmp_path / 'missing' / 'prompts.sqlite3'
    dest = tmp_path / 'game-planning-smoke-0001'
    assert not db.exists()
    report = smoke.main([
        'train', '--output', str(db), '--train-output', str(dest), '--epochs', '8',
    ])
    messages = capsys.readouterr().out
    assert 'GAMER TRAIN AUTO-BUILD: missing SQLite corpus' in messages
    assert 'GAMER TRAIN AUTO-BUILD VERIFIED: 229 games, 2794 prompts' in messages
    assert db.is_file()
    corpus = smoke.verify(db, verify_each_oracle=True)
    assert corpus['games'] == 229
    assert corpus['prompts'] == 2794
    assert corpus['certified_numerical_prompts'] == 2628
    assert corpus['unlabelled_catalog_prompts'] == 166
    assert report['source_games'] == 229
    assert report['certified_games'] == 146
    assert report['catalog_only_games'] == 83
    assert report['epochs'] == 8
    assert report['database_sha256'] == trainer._digest(db)
    assert (dest / 'strategy_router.npz').is_file()
    assert (dest / 'final_report.json').is_file()


def test_train_command_reuses_existing_verified_db_without_rebuilding(smoke_db, tmp_path, capsys):
    original_sha = trainer._digest(smoke_db)
    report = smoke.main([
        'train', '--output', str(smoke_db), '--train-output', str(tmp_path / 'reuse'), '--epochs', '2',
    ])
    assert 'reusing existing SQLite corpus' in capsys.readouterr().out
    assert report['database_sha256'] == original_sha
    assert trainer._digest(smoke_db) == original_sha
    assert report['training_rows'] == 667


def test_train_command_does_not_autorepair_existing_tampered_database(smoke_db, tmp_path):
    broken = tmp_path / 'tampered.sqlite3'
    shutil.copyfile(smoke_db, broken)
    with sqlite3.connect(broken) as con:
        con.execute("UPDATE prompts SET numeric_answer='999' "
                    "WHERE prompt_id=(SELECT prompt_id FROM prompts "
                    "WHERE numerical_training_eligible=1 LIMIT 1)")
    original_sha = trainer._digest(broken)
    dest = tmp_path / 'invalid_training'
    with pytest.raises(ValueError, match='CERTIFIED_SOURCE_MISMATCH'):
        smoke.main(['train', '--output', str(broken), '--train-output', str(dest), '--epochs', '2'])
    assert trainer._digest(broken) == original_sha
    assert not dest.exists()


def test_train_command_rejects_invalid_or_existing_destination_before_autobuild(tmp_path):
    db = tmp_path / 'missing.sqlite3'
    exists = tmp_path / 'already_trained'
    exists.mkdir()
    with pytest.raises(FileExistsError, match='ALREADY_EXISTS'):
        smoke.main(['train', '--output', str(db), '--train-output', str(exists)])
    assert not db.exists()
    with pytest.raises(ValueError, match='EPOCHS_OUT_OF_RANGE'):
        smoke.main(['train', '--output', str(db), '--train-output', str(tmp_path / 'future'),
                    '--epochs', '0'])
    assert not db.exists()
    with pytest.raises(ValueError, match='PROTECTED_SOURCE_DIRECTORY'):
        smoke.main(['train', '--output', str(db), '--train-output', str(tmp_path)])
    assert not db.exists()
