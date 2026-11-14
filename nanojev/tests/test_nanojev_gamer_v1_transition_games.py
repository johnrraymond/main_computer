"""Fourth-table bounded named-game curriculum: direct source, no fabricated full-game labels."""
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
import pytest

from tools import nanojev_gamer_v1_transition_games as transition
from tools import nanojev_gamer_v1_catalog_extensions as catalog
from tools import nanojev_game_transformation_library as source
from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_handoff as handoff


def rows_of(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]


def test_registry_55_are_named_catalog_sources_with_no_random_game_or_overlap():
    native=set(source.MATRICES)|set(source.SCALAR_MODELS)
    assert len(transition.TRANSITION_IDS)==55
    assert len(transition.MODE_FORMULAS)==12
    assert transition.TRANSITION_IDS.isdisjoint(native|catalog.EXTRA_IDS)
    assert not any('random' in x for x in transition.TRANSITION_IDS)
    assert all(x['rule'] and x['objective'] and x['formula'] for x in transition.SPECS.values())
    assert handoff.registry_ids(include_transition_games=True)==native|catalog.EXTRA_IDS|transition.TRANSITION_IDS
    assert len(handoff.registry_ids(include_transition_games=True))==146


def test_each_transition_oracle_matches_independent_factor_reconstruction_and_closed_chance():
    for game in transition.TRANSITION_IDS:
        for i in range(18):
            row=pairs.generate_case(game,i,47)
            assert pairs.certify(row)
            assert row['generator_schema']==pairs.TRANSITION_SCHEMA
            assert row['bounded_transition_objective'] is True
            assert row['oracle_scope']=='EXPLICIT_FINITE_ONE_STEP_TRANSITION_NOT_FULL_GAME'
            assert row['table_families'][0]=='state_transition'
            assert '| State | Actor | Information available | Action / event | Probability | Next state | Reward | Terminal |' in row['math_prompt']
            assert all('–' in line for line in row['math_prompt'].splitlines() if line.startswith('| ') and 'step ' in line)
            state=row['source']
            assert state['kind']=='bounded_state_transition'
            assert transition.validate_kernel(game,state['parameters'],state['transition_rows'])
            assert pairs.evaluate_factors(row['factor_steps'],row['factor_final_step'])==transition.oracle(game,state['parameters'])
            assert float(row['answer'])==float(transition.oracle(game,state['parameters']))
            if state['mode']=='signal':
                assert 'hidden type is not revealed to the receiver' in row['prompt_08']
                assert not any('realized true' in s.lower() for s in row['math_prompt'].splitlines())
                assert sum(Fraction(v['probability']) for v in state['transition_rows'][:2])==1


def test_probability_drift_rejected_before_training():
    for game in ('markov_game','lewis_signaling','cyber_defense'):
        row=pairs.generate_case(game,0,47)
        state=row['source']; p=dict(state['parameters']); rows=[dict(x) for x in state['transition_rows']]
        rows[-1]['probability']='9/10'
        with pytest.raises(ValueError,match='UNNORMALIZED'):
            transition.validate_kernel(game,p,rows)
        altered=dict(row);altered['math_prompt']='forged'
        with pytest.raises(ValueError):pairs.certify(altered)
        altered=dict(row);altered['answer']='999'
        with pytest.raises(ValueError):pairs.certify(altered)


def test_four_table_full_146_game_prepare_validate_and_opt_in(tmp_path):
    target=tmp_path/'certified_pairs.jsonl'
    report=pairs.write_dataset(target,cases_per_game=18,seed=47,include_transition_games=True)
    rows=rows_of(target)
    assert report['status']=='PASS_146_GAME_FOUR_TABLE_BOUNDED_GENERATION'
    assert report['games']==146 and report['cases']==2628
    assert report['bounded_catalog_entries']==58 and report['bounded_transition_entries']==55
    assert report['by_split']=={'train':1752,'dev':438,'test':438}
    assert report['table_families']['state_transition']==990
    assert report['blind_matrix_cases']==0
    assert len({r['prompt_08'] for r in rows})==len(rows)
    counts=Counter(r['game_id'] for r in rows)
    assert len(counts)==146 and set(counts.values())=={18}
    assert all(pairs.certify(r) for r in rows)
    loaded, manifest=handoff.load_cases(target,include_transition_games=True)
    assert len(loaded)==2628 and manifest['number_of_verified_games']==146
    assert manifest['bounded_catalog_extension_count']==58
    assert manifest['bounded_transition_game_count']==55
    assert manifest['fourth_table_family']=='state_transition'
    with pytest.raises(ValueError,match='UNVERIFIED_GAME'):
        handoff.load_cases(target,include_catalog_extensions=True)
    with pytest.raises(ValueError,match='UNVERIFIED_GAME'):
        handoff.load_cases(target)
    result=handoff.main(['prepare','--output',str(tmp_path/'cli'),'--cases-per-game','6','--include-transition-games','--device','cpu'])
    assert result['manifest']['number_of_verified_games']==146
    with pytest.raises(ValueError,match='UNVERIFIED_GAME'):
        handoff.main(['validate','--output',str(tmp_path/'cli'),'--device','cpu'])
    verified=handoff.main(['validate','--output',str(tmp_path/'cli'),'--include-transition-games','--device','cpu'])
    assert verified['status']=='PASS_SOURCE_RECOMPUTED_CERTIFICATION'


def test_transition_dataset_is_reproducible_and_old_modes_stay_identical(tmp_path):
    pa=tmp_path/'a'/'pairs.jsonl';pb=tmp_path/'b'/'pairs.jsonl'
    pairs.write_dataset(pa,cases_per_game=6,include_transition_games=True)
    pairs.write_dataset(pb,cases_per_game=6,include_transition_games=True)
    assert pa.read_bytes()==pb.read_bytes()
    legacy=tmp_path/'legacy.jsonl'
    v12=tmp_path/'v12.jsonl'
    report=pairs.write_dataset(legacy,cases_per_game=6)
    assert report['games']==33
    report=pairs.write_dataset(v12,cases_per_game=6,include_catalog_extensions=True)
    assert report['games']==91 and report['bounded_transition_entries']==0
    assert all('state_transition' not in x['table_families'] for x in rows_of(v12))
    assert all('bounded_transition_objective' not in x for x in rows_of(v12))


def test_probe_sample_has_fixed_anchors_from_every_provenance_class():
    dev=[pairs.generate_case(game,4,47) for game in sorted(handoff.registry_ids(include_transition_games=True))]
    p0,n0=handoff.fast_probe_deck(dev,budget=17,evaluation_index=0)
    p1,n1=handoff.fast_probe_deck(dev,budget=17,evaluation_index=1)
    assert len(p0)==len(p1)==17 and n0==n1==6
    assert p0[:6]==p1[:6]
    assert {r['game_id'] for r in p0[6:]} != {r['game_id'] for r in p1[6:]}
    def category(row):
        return 'transition' if row.get('bounded_transition_objective') else ('catalog' if row.get('bounded_catalog_objective') else 'native')
    assert Counter(category(r) for r in p0[:6])=={'transition':2,'native':2,'catalog':2}
    assert all(row['split']=='dev' for row in p0)
    assert len({row['game_id'] for row in p0})==17
