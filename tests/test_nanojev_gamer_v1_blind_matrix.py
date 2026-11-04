"""Blind, anonymous payoff-matrix curriculum: no game label or source payoff shortcuts."""
import json
from collections import Counter
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_handoff as handoff
from tools import nanojev_game_transformation_library as source


def test_blind_matrices_are_game_anonymous_and_oracle_exact():
    for game in sorted(source.MATRICES):
        title=source.MATRICES[game][0]
        for index in range(18,36):
            row=pairs.generate_case(game,index,47,blind_matrix=True)
            assert pairs.certify(row)
            assert row['blind_matrix'] is True
            assert title not in row['prompt_08']
            assert title not in row['math_prompt']
            assert game not in row['prompt_08']
            assert game not in row['math_prompt']
            assert 'Action comparison' in row['math_prompt']
            assert '| – |' in row['math_prompt']
            assert row['source']['scenario_variant']=='blind_independent_payoff_matrix'
            f=row['source'];j=f['rival_index']
            a=f['U'][0][j][0];b=f['U'][1][j][0]
            assert int(float(row['answer'])) == int(b>a)
            assert float(row['zero_line_residual']) == a-b
            assert pairs.evaluate_factors(row['factor_steps'],row['factor_final_step']) == int(b>a)


def test_blind_full_dataset_determinism_split_and_regimes(tmp_path):
    a=tmp_path/'a'/'certified_pairs.jsonl'
    b=tmp_path/'b'/'certified_pairs.jsonl'
    stats=pairs.write_dataset(a,cases_per_game=18,blind_cases_per_matrix=18,seed=47)
    same=pairs.write_dataset(b,cases_per_game=18,blind_cases_per_matrix=18,seed=47)
    assert a.read_bytes()==b.read_bytes()
    assert stats['dataset_sha256']==same['dataset_sha256']
    assert stats['games']==33 and stats['cases']==594+14*18
    assert stats['blind_matrix_cases']==14*18
    assert stats['by_split']=={'train':396+14*12,'dev':99+14*3,'test':99+14*3}
    assert set(stats['blind_matrix_regimes'])==set(pairs.BLIND_MATRIX_REGIMES)
    rows=[json.loads(line) for line in a.read_text(encoding='utf-8').splitlines()]
    assert len(set(row['prompt_08'] for row in rows))==len(rows)
    blind=[row for row in rows if row.get('blind_matrix')]
    assert set(row['game_id'] for row in blind)==set(source.MATRICES)
    assert {row['split'] for row in blind}=={'train','dev','test'}
    assert {row['source']['sample_magnitude'] for row in blind if row['split']=='test'} <= {10000,100000,1000000}
    assert {row['source']['sample_magnitude'] for row in blind if row['split']=='train'} <= {1,10,100,1000}
    assert set(row['blind_matrix_regime'] for row in blind if row['split']=='test')==set(pairs.BLIND_MATRIX_REGIMES)
    assert set(row['blind_matrix_regime'] for row in blind if row['split']=='train')==set(pairs.BLIND_MATRIX_REGIMES)
    assert pairs.certify(blind[-1])


def test_exact_ties_and_signed_neighbors_present():
    cases=[pairs.generate_case(g,18+i,47,blind_matrix=True)
           for g in sorted(source.MATRICES) for i in range(36)]
    tied=[r for r in cases if r['blind_matrix_regime']=='exact_tie']
    assert tied and all(float(r['zero_line_residual'])==0 and float(r['answer'])==0 for r in tied)
    near=[r for r in cases if r['blind_matrix_regime'] in ('signed_neighbor','large_cancellation')]
    assert near and {-1.,0.,1.} <= set(float(r['zero_line_residual']) for r in near)


def test_tampered_regime_or_hidden_game_prompt_rejected():
    row=pairs.generate_case('hawk_dove',18,47,blind_matrix=True)
    assert pairs.certify(row)
    modified=dict(row);modified['blind_matrix_regime']='independent'
    with pytest.raises(ValueError,match='GENERATED_CASE_MISMATCH'):
        pairs.certify(modified)
    modified=dict(row);modified['math_prompt']='Hawk–Dove named game'
    with pytest.raises(ValueError,match='GENERATED_CASE_MISMATCH'):
        pairs.certify(modified)
    modified=dict(row);modified.pop('blind_matrix')
    with pytest.raises(ValueError,match='GENERATED_CASE_MISMATCH'):
        pairs.certify(modified)


def test_blind_cases_require_new_run_and_correct_options(tmp_path):
    args=handoff.parser().parse_args(['prepare','--output',str(tmp_path/'new'),
                                       '--blind-matrix-per-game','6'])
    assert args.blind_matrix_per_game==6
    out=tmp_path/'new'
    result=handoff.main(['prepare','--output',str(out),'--device','cpu',
                         '--cases-per-game','6','--blind-matrix-per-game','6'])
    assert result['generation']['blind_matrix_cases']==14*6
    loaded,report=handoff.load_cases(out/'certified_pairs.jsonl')
    assert len(loaded)==33*6+14*6
    assert report['number_of_verified_games']==33
    validated=handoff.main(['validate','--output',str(out),'--device','cpu'])
    assert validated['status']=='PASS_SOURCE_RECOMPUTED_CERTIFICATION'
    with pytest.raises(FileExistsError,match='DATASET_EXISTS'):
        handoff.main(['prepare','--output',str(out),'--device','cpu',
                      '--cases-per-game','6','--blind-matrix-per-game','6'])
    with pytest.raises(ValueError,match='BLIND_MATRIX_CASES'):
        handoff.main(['prepare','--output',str(tmp_path/'bad'),
                      '--blind-matrix-per-game','5'])


def test_nonblind_cases_remain_unchanged():
    row=pairs.generate_case('hawk_dove',0,47)
    assert pairs.certify(row)
    assert 'blind_matrix' not in row
    assert 'Hawk–Dove' in row['prompt_08']
    assert row['source']['scenario_variant']=='local_payoff_variant'
