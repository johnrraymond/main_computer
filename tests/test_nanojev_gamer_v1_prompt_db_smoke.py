"""End-to-end integrity checks for the complete GAMER prompt SQLite corpus."""
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_prompt_db_smoke as smoke
from tools import nanojev_gamer_v1_handoff as handoff
from tools import nanojev_gamer_v1_pairs as pairs


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    root=tmp_path_factory.mktemp('gamer_all_prompt_smoke')
    db=root/'all_games.sqlite3'
    result=smoke.build(db,cases_per_game=6,seed=47,catalog_prompts_per_game=2)
    return db,result


def test_complete_source_registry_229_and_23_families():
    registry=smoke.source_registry()
    assert len(registry)==229
    assert len(set(e[1] for e in registry.values()))==23
    assert smoke._verify_counts(registry)==Counter(native_oracle=33,
        bounded_catalog_oracle=58,bounded_transition_oracle=55,catalog_only=83)
    assert {'hawk_dove','bayes_posterior','captain_ship_defense',
            'centipede','rock_paper_scissors','chess','go','hypergame',
            'byzantine_generals','bertrand_oligopoly'} <= registry.keys()


def test_numerical_examples_have_independently_recomputed_oracles(built):
    db,_=built
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM certified_training_prompts').fetchone()[0]==876
        for (raw,) in conn.execute('SELECT source_case_json FROM certified_training_prompts ORDER BY game_id LIMIT 30'):
            assert pairs.certify(json.loads(raw)) is True


def test_catalog_games_have_real_prompts_but_no_fabricated_targets(built):
    db,_=built
    with sqlite3.connect(db) as conn:
        rows=conn.execute('''SELECT g.game_id,p.prompt,p.numeric_answer,p.math_target,p.source_case_json
            FROM games g JOIN prompts p USING(game_id)
            WHERE g.scope='catalog_only' ORDER BY g.game_id,p.task''').fetchall()
        assert len(rows)==83*2
        assert all(prompt and num is None and math is None and raw is None
                   for _,prompt,num,math,raw in rows)
        assert all('Do not' in prompt for _,prompt,_,_,_ in rows)
        assert conn.execute('SELECT count(DISTINCT game_id) FROM catalog_research_prompts').fetchone()[0]==83
        assert conn.execute('SELECT count(*) FROM certified_training_prompts WHERE game_id=?',('chess',)).fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM catalog_research_prompts WHERE game_id=?',('chess',)).fetchone()[0]==2


def test_coverage_verified_with_all_split_names(built):
    db,report=built
    assert report['status']=='PASS_COMPLETE_GAME_PROMPT_SQLITE_SMOKE'
    assert report['games']==229
    assert report['certified_numerical_prompts']==876
    assert report['unlabelled_catalog_prompts']==166
    assert report['prompts']==1042
    assert report['all_game_ids_covered'] and report['catalog_only_excluded_from_training']
    assert smoke.verify(db,verify_each_oracle=True)['recomputed_each_numerical_oracle']
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT count(*) FROM game_coverage WHERE prompt_count=0').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM game_coverage WHERE certified_count=6').fetchone()[0]==146


def test_export_round_trips_to_original_handoff_loader(built,tmp_path):
    db,_=built
    exported=tmp_path/'certified_pairs.jsonl'
    report=smoke.export_verified(db,exported)
    assert report['status']=='PASS_COMPLETE_GAME_PROMPT_SQLITE_SMOKE'
    cases,manifest=handoff.load_cases(exported,include_transition_games=True)
    assert len(cases)==876
    assert manifest['number_of_verified_games']==146
    assert manifest['provenance']=='source_recomputed'
    assert not any(c['game_id'] in {'chess','go','hypergame'} for c in cases)
    assert set(manifest['by_split'])=={'train','dev','test'}


def test_atomic_no_clobber_and_requires_6_multiple(built):
    db,_=built
    before=db.read_bytes()
    with pytest.raises(FileExistsError,match='EXISTS'):
        smoke.build(db,cases_per_game=6)
    assert db.read_bytes()==before
    with pytest.raises(ValueError,match='MULTIPLE_OF_SIX'):
        smoke.build(db,cases_per_game=5,replace=True)
    assert db.read_bytes()==before
    with pytest.raises(ValueError,match='MUST_BE_1_OR_2'):
        smoke.build(db,cases_per_game=6,catalog_prompts_per_game=3,replace=True)
    assert db.read_bytes()==before


def test_readonly_verifier_rejects_omitted_game(built,tmp_path):
    db,_=built
    duplicate=tmp_path/'missing_game.sqlite3'
    shutil.copyfile(db,duplicate)
    with sqlite3.connect(duplicate) as conn:
        conn.execute('DELETE FROM prompts WHERE game_id=?',('chess',))
        conn.execute('DELETE FROM games WHERE game_id=?',('chess',))
    with pytest.raises(ValueError,match='GAME_COVERAGE_OR_SCOPE_CHANGED'):
        smoke.verify(duplicate)


def test_readonly_verifier_rejects_unverified_source_mutation(built,tmp_path):
    db,_=built
    duplicate=tmp_path/'tamper.sqlite3'
    shutil.copyfile(db,duplicate)
    with sqlite3.connect(duplicate) as conn:
        conn.execute('''UPDATE prompts SET numeric_answer='9999'
            WHERE prompt_id=(SELECT prompt_id FROM prompts WHERE numerical_training_eligible=1 LIMIT 1)''')
    with pytest.raises(ValueError,match='CERTIFIED_SOURCE_MISMATCH'):
        smoke.verify(duplicate)


def test_verifier_rejects_catalog_prompt_mutation_even_with_rewritten_digest(built,tmp_path):
    db,_=built
    duplicate=tmp_path/'catalog_tamper.sqlite3'
    shutil.copyfile(db,duplicate)
    with sqlite3.connect(duplicate) as conn:
        game,pid,task=conn.execute('SELECT game_id,prompt_id,task FROM prompts WHERE numerical_training_eligible=0 LIMIT 1').fetchone()
        changed='INVENTED GAME RULES AND NUMERIC ANSWERS'
        idx=0 if task=='structural_inventory' else 1
        new_id=smoke.sha_text(smoke.canonical(['catalog',game,idx,changed]))
        conn.execute('UPDATE prompts SET prompt_id=?,prompt=?,prompt_sha256=? WHERE prompt_id=?',
                     (new_id,changed,smoke.sha_text(changed),pid))
    with pytest.raises(ValueError,match='CATALOG_SOURCE_PROMPT_CHANGED'):
        smoke.verify(duplicate)


def test_deterministic_identical_build(tmp_path):
    a=tmp_path/'a.sqlite3'; b=tmp_path/'b.sqlite3'
    smoke.build(a,cases_per_game=6,seed=12,catalog_prompts_per_game=1)
    smoke.build(b,cases_per_game=6,seed=12,catalog_prompts_per_game=1)
    assert a.read_bytes()==b.read_bytes()
    assert smoke.verify(a)['unlabelled_catalog_prompts']==83


def test_build_closes_sqlite_handle_before_atomic_publish_on_windows(tmp_path, monkeypatch):
    """Emulate Windows rename refusal if the builder retains an open connection.

    sqlite3.Connection.__exit__ only ends a transaction; it does not close.
    POSIX allows renaming an open SQLite file, so the assertion is explicit.
    """
    import sqlite3

    db = tmp_path / 'prompts.sqlite3'
    live = []
    original_connect = sqlite3.connect
    original_replace = smoke.os.replace

    def track_connection(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        live.append(connection)
        return connection

    def windows_guarded_replace(src, dst):
        assert live, 'The build should have opened its temporary database'
        for connection in live:
            try:
                connection.execute('SELECT 1')
            except sqlite3.ProgrammingError as exc:
                assert 'closed' in str(exc).lower()
            else:
                raise PermissionError('Simulated WinError 32: SQLite connection still open')
        return original_replace(src, dst)

    monkeypatch.setattr(smoke.sqlite3, 'connect', track_connection)
    monkeypatch.setattr(smoke.os, 'replace', windows_guarded_replace)

    result = smoke.build(db, cases_per_game=6, catalog_prompts_per_game=2, seed=47)
    assert result['games'] == 229
    assert db.is_file()
    assert not list(tmp_path.glob('tmp*.sqlite3'))


def test_build_closes_sqlite_handle_and_deletes_temporary_file_on_failure(tmp_path, monkeypatch):
    """A validation failure must close the Windows file handle before unlink."""
    import sqlite3

    db = tmp_path / 'prompts.sqlite3'
    live = []
    original_connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        live.append(connection)
        return connection

    def break_validation(*args, **kwargs):
        raise ValueError('SIMULATED_ORACLE_VALIDATION_FAILURE')

    monkeypatch.setattr(smoke.sqlite3, 'connect', track_connection)
    monkeypatch.setattr(smoke, 'verify_connection', break_validation)
    with pytest.raises(ValueError, match='SIMULATED_ORACLE_VALIDATION_FAILURE'):
        smoke.build(db, cases_per_game=6, catalog_prompts_per_game=2, seed=47)

    assert not db.exists()
    assert not list(tmp_path.glob('tmp*.sqlite3'))
    assert live
    for connection in live:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')
