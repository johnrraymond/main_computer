"""v11: anonymous random-matrix generator is retired from new Gamer training."""
import json
import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_handoff as handoff
from tools import nanojev_game_transformation_library as source


def test_verified_game_examples_preserve_source_oracle_and_identity():
    for game in sorted(source.MATRICES):
        row=pairs.generate_case(game,0,47)
        assert pairs.certify(row)
        assert not row.get('blind_matrix')
        assert source.MATRICES[game][0] in row['math_prompt']
        assert row['source']['scenario_variant']=='local_payoff_variant'


def test_anonymous_generator_is_not_available():
    with pytest.raises(ValueError,match='BLIND_MATRIX_RETIRED'):
        pairs.generate_case('hawk_dove',18,47,blind_matrix=True)
    with pytest.raises(ValueError,match='BLIND_MATRIX_RETIRED'):
        pairs.write_dataset('unused.jsonl',blind_cases_per_matrix=18)
    with pytest.raises(ValueError,match='BLIND_MATRIX_RETIRED'):
        handoff.main(['prepare','--output','unused','--blind-matrix-per-game','18'])


def test_new_dataset_has_no_anonymous_games_and_is_deterministic(tmp_path):
    a=tmp_path/'a'/'certified_pairs.jsonl'
    b=tmp_path/'b'/'certified_pairs.jsonl'
    first=pairs.write_dataset(a,cases_per_game=6)
    second=pairs.write_dataset(b,cases_per_game=6)
    assert a.read_bytes()==b.read_bytes()
    assert first['dataset_sha256']==second['dataset_sha256']
    assert first['games']==33 and first['cases']==198
    assert first['blind_matrix_cases']==0 and first['blind_matrix_regimes']=={}
    rows=[json.loads(row) for row in a.read_text(encoding='utf-8').splitlines()]
    assert len(set(row['prompt_08'] for row in rows))==len(rows)
    assert all('blind_matrix' not in row for row in rows)
    loaded,manifest=handoff.load_cases(a)
    assert len(loaded)==198 and manifest['number_of_verified_games']==33


def test_archived_blind_dataset_cannot_enter_new_training(tmp_path):
    row=pairs.generate_case('hawk_dove',0,47)
    row['blind_matrix']=True
    file=tmp_path/'legacy.jsonl'
    file.write_text(json.dumps(row)+'\n')
    with pytest.raises(ValueError,match='BLIND_MATRIX_RETIRED'):
        handoff.load_cases(file,allow_partial=True)
