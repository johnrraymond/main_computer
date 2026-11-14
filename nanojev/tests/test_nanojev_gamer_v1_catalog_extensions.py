"""No catalog-only label is promoted without a bounded public oracle contract."""
import hashlib
import math
import json
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_handoff as handoff
from tools import nanojev_gamer_v1_catalog_extensions as catalog
from tools import nanojev_game_transformation_library as source


def generate(tmp_path, n=6):
    target=tmp_path/'certified_pairs.jsonl'
    report=pairs.write_dataset(target,cases_per_game=n,seed=47,
                               include_catalog_extensions=True)
    cases=[json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]
    return target,report,cases


def test_catalog_contains_exactly_58_named_bounded_entries_and_no_random_matrix():
    assert len(catalog.EXTRA_IDS)==58
    assert len(catalog.EXTRA_MATRICES)==12
    assert len(catalog.EXTRA_SCALARS)==46
    assert catalog.EXTRA_IDS.isdisjoint(source.MATRICES)
    assert catalog.EXTRA_IDS.isdisjoint(source.SCALAR_MODELS)
    assert 'random_payoff_matrix' not in catalog.EXTRA_IDS
    assert set(catalog.EXTRA_IDS) <= {source.family_game_id(name)
        for texts in source.CATALOG.values() for name in texts.split('|')}
    assert all(x['objective']=='bounded_mathematical_subproblem'
               for x in catalog.EXTRA_SCALARS.values())


def test_full_91_game_dataset_covers_every_split_and_table_is_oracle_consistent(tmp_path):
    target,report,cases=generate(tmp_path,n=18)
    assert report['status']=='PASS_91_GAME_BOUNDED_OBJECTIVE_GENERATION'
    assert report['games']==91 and report['bounded_catalog_entries']==58
    assert report['blind_matrix_cases']==0
    assert len(cases)==1638
    assert report['by_split']=={'train':1092,'dev':273,'test':273}
    assert len({r['prompt_08'] for r in cases})==len(cases)
    assert report['source_registry_sha256']==pairs.extension_signature()
    assert {r['game_id'] for r in cases}==handoff.registry_ids(include_catalog_extensions=True)
    for row in cases:
        assert pairs.certify(row)
        assert math.isclose(float(pairs.evaluate_factors(row['factor_steps'],row['factor_final_step'])),float(row['answer']),rel_tol=1e-12,abs_tol=1e-10)
        assert '| – |' in row['math_prompt']
        assert not row.get('blind_matrix')
        if row['game_id'] in catalog.EXTRA_IDS:
            assert row['bounded_catalog_objective'] is True
            assert row['oracle_scope']=='EXPLICIT_ONE_STEP_OBJECTIVE_NOT_FULL_GAME'
            assert row['generator_schema']==pairs.EXTENSION_SCHEMA
        else:
            assert row['generator_schema']==pairs.SCHEMA
            assert 'bounded_catalog_objective' not in row
    loaded,manifest=handoff.load_cases(target,include_catalog_extensions=True)
    assert len(loaded)==len(cases)
    assert manifest['number_of_verified_games']==91
    assert manifest['bounded_catalog_extension_count']==58
    assert manifest['oracle_scope']=='EXPLICIT_ONE_STEP_OBJECTIVES_NOT_FULL_GAME'


def test_same_seed_exact_bytes_and_legacy_output_unmodified(tmp_path):
    a,b=tmp_path/'a',tmp_path/'b';a.mkdir();b.mkdir()
    pa,_,_=generate(a)
    pb,_,_=generate(b)
    assert pa.read_bytes()==pb.read_bytes()
    legacy=tmp_path/'legacy.jsonl'
    report=pairs.write_dataset(legacy,cases_per_game=6,seed=47)
    assert report['games']==33 and report['bounded_catalog_entries']==0
    rows=[json.loads(line) for line in legacy.read_text(encoding='utf-8').splitlines()]
    assert all(x['generator_schema']==pairs.SCHEMA for x in rows)
    assert report['source_registry_sha256']==pairs.source_signature()


def test_catalog_case_tampering_and_unopted_dataset_refused(tmp_path):
    target,_,cases=generate(tmp_path)
    with pytest.raises(ValueError,match='UNVERIFIED_GAME'):
        handoff.load_cases(target)
    row=next(c for c in cases if c['game_id'] in catalog.EXTRA_IDS)
    for key,value in [('answer','-999'),('math_prompt','fabricated'),
                      ('prompt_08','not the source story'),('source',{}),
                      ('factor_steps',[]),('oracle_verified',False),
                      ('source_registry_sha256','a'*64),('oracle_scope','FULL_GAME')]:
        modified=dict(row);modified[key]=value
        with pytest.raises(ValueError):pairs.certify(modified)


def test_matrix_game_identity_and_rps_three_actions():
    for game,spec in catalog.EXTRA_MATRICES.items():
        row=pairs.generate_case(game,0,47)
        assert row['table_families']==['strategic_payoff','calculation']
        assert pairs.certify(row)
        if game=='rock_paper_scissors':
            assert len(row['source']['U'])==3
            assert len(row['factor_steps'])==2
            assert float(row['answer'])==max(row['source']['U'][i][0][0] for i in range(3))
        else:
            assert len(row['source']['U'])==2
            assert row['answer'] in ('0.0','1.0')
    U=catalog.EXTRA_MATRICES['constant_sum_game']['U']
    assert len({sum(x) for row in U for x in row})==1
    U=catalog.EXTRA_MATRICES['zero_sum_game']['U']
    assert {sum(x) for row in U for x in row}=={0}


def test_named_game_definition_oracles_are_independently_reconstructed():
    for game in catalog.EXTRA_SCALARS:
        for index in range(18):
            row=pairs.generate_case(game,index,47)
            p=row['source']['parameters']
            assert catalog.domain_ok(game,p)
            assert math.isclose(float(row['answer']),float(pairs.extension_oracle(game,p)),rel_tol=1e-12,abs_tol=1e-10)
            assert pairs.certify(row)


def test_cli_prepare_validate_opt_in_and_old_default(tmp_path):
    out=tmp_path/'v12'
    result=handoff.main(['prepare','--output',str(out),'--cases-per-game','6',
                        '--include-catalog-extensions','--device','cpu'])
    assert result['manifest']['number_of_verified_games']==91
    with pytest.raises(ValueError,match='UNVERIFIED_GAME'):
        handoff.main(['validate','--output',str(out),'--device','cpu'])
    val=handoff.main(['validate','--output',str(out),
                      '--include-catalog-extensions','--device','cpu'])
    assert val['status']=='PASS_SOURCE_RECOMPUTED_CERTIFICATION'
    assert val['manifest']['bounded_catalog_extension_count']==58
