"""Executable source-oracle certification for Gamer v1 sparse math-table pairs."""
import json
from collections import Counter
from pathlib import Path

import pytest

from tools import nanojev_gamer_v1_pairs as pairs
from tools import nanojev_gamer_v1_handoff as handoff
from tools import nanojev_game_transformation_library as src


def make_sample(tmp_path,n=6):
    path=tmp_path/'certified_pairs.jsonl'
    stats=pairs.write_dataset(path,cases_per_game=n,seed=47)
    rows=[json.loads(line) for line in path.read_text(encoding='utf8').splitlines()]
    return path,stats,rows


def test_all_source_games_covered_and_all_steps_oracle_certified(tmp_path):
    path,stats,rows=make_sample(tmp_path,n=18)
    assert stats['status']=='PASS_33_GAME_ORACLE_TABLE_GENERATION'
    assert stats['games']==33 and stats['cases']==594
    assert set(r['game_id'] for r in rows)==set(src.MATRICES)|set(src.SCALAR_MODELS)
    assert stats['by_split']=={'train':396,'dev':99,'test':99}
    assert len({r['prompt_08'] for r in rows})==len(rows)
    assert {r['generator_schema'] for r in rows}=={pairs.SCHEMA}
    assert stats['zero_line_cases']>0
    for r in rows:
        assert pairs.certify(r)
        assert 'Result |' in r['math_prompt']
        assert '| – |' in r['math_prompt']
        assert isinstance(float(r['answer']),float)
    assert (tmp_path/'factorization_summary.csv').is_file()
    assert (tmp_path/'sample_pairs.md').is_file()
    assert path.stat().st_size>1_000_000


def test_deterministic_bytes_and_no_implicit_overwrite(tmp_path):
    x=tmp_path/'x';y=tmp_path/'y';x.mkdir();y.mkdir()
    a,sa,_=make_sample(x)
    b,sb,_=make_sample(y)
    assert a.read_bytes()==b.read_bytes()
    assert sa['dataset_sha256']==sb['dataset_sha256']
    with pytest.raises(FileExistsError,match='DATASET_EXISTS'):
        pairs.write_dataset(a)


def test_data_tamper_is_not_accepted_as_verified(tmp_path):
    path,stats,rows=make_sample(tmp_path)
    row=next(r for r in rows if r['game_id']=='bayes_posterior')
    assert pairs.certify(row)
    for field,change in (('math_prompt','Forged table'),('answer','999'),
                         ('prompt_08','Altered question'),('source',{}),
                         ('factor_steps',[]),('source_registry_sha256','0'*64),
                         ('oracle_verified',False),('table_verified',False)):
        manipulated=dict(row);manipulated[field]=change
        with pytest.raises(ValueError):pairs.certify(manipulated)


def test_calculation_is_a_shared_generic_factor_evaluator(tmp_path):
    path,_,rows=make_sample(tmp_path)
    operators={step['operation'] for row in rows for step in row['factor_steps']}
    assert operators=={'add','subtract','multiply by','divide by','larger of',
                       'choose second action if larger'}
    assert len(set(r['table_families'][0] for r in rows))<=2
    for row in rows:
        computed=pairs.evaluate_factors(row['factor_steps'],row['factor_final_step'])
        assert abs(float(computed)-float(row['answer']))<1e-8
    # Duplicate subexpressions are shared, not recomputed as extra factors.
    bayes=next(r for r in rows if r['game_id']=='bayes_posterior')
    assert len(bayes['factor_steps'])==5
    assert 'multiply' in bayes['math_prompt'].lower()
    assert 'G.U=' not in bayes['math_prompt'] and 'OP:' not in bayes['math_prompt']


def test_zero_line_metadata_is_physical_not_invented(tmp_path):
    _,_,rows=make_sample(tmp_path,n=18)
    for game in pairs.ZERO_GAMES:
        subset=[r for r in rows if r['game_id']==game]
        assert set(float(r['zero_line_residual'])>0 for r in subset)=={True,False}
        assert {-1,0,1}<=set((float(r['zero_line_residual'])>0)-(float(r['zero_line_residual'])<0) for r in subset)
        assert {r['split'] for r in subset}=={'train','dev','test'}
    assert all('zero_line_residual' not in r for r in rows if r['game_id']=='bayes_posterior')


def test_prompt_is_plain_english_and_math_has_populated_numeric_factors(tmp_path):
    _,_,rows=make_sample(tmp_path)
    for row in rows:
        assert ' ' in row['prompt_08'] and len(row['prompt_08'])>60
        assert '|' in row['math_prompt'] and '| – |' in row['math_prompt']
        assert not row['prompt_08'].startswith('GAME:')
        assert not row['math_prompt'].startswith('GAME:')
    assert 'posterior probability' in next(r for r in rows if r['game_id']=='bayes_posterior')['prompt_08']
    assert 'Action comparison' in next(r for r in rows if r['game_id']=='hawk_dove')['math_prompt']


def test_cli_prepare_validate_directly_without_external_dataset(tmp_path):
    out=tmp_path/'cutover-001'
    result=handoff.main(['prepare','--output',str(out),'--device','cpu','--cases-per-game','6'])
    assert result['status']=='PASS_PREPARE_AND_CERTIFY'
    assert result['manifest']['number_of_verified_games']==33
    assert result['manifest']['provenance']=='source_recomputed'
    checked=handoff.main(['validate','--output',str(out),'--device','cpu'])
    assert checked['status']=='PASS_SOURCE_RECOMPUTED_CERTIFICATION'
    with pytest.raises(FileExistsError,match='DATASET_EXISTS'):
        handoff.main(['prepare','--output',str(out),'--device','cpu','--cases-per-game','6'])


def test_source_change_detected_when_generated_source_registry_fingerprint_differs(tmp_path,monkeypatch):
    path,_,rows=make_sample(tmp_path)
    assert pairs.certify(rows[0])
    monkeypatch.setattr(pairs,'source_signature',lambda:'8'*64)
    with pytest.raises(ValueError,match='SOURCE_REGISTRY_DRIFT'):
        pairs.certify(rows[0])


def test_generated_dataset_rejects_case_edit_even_with_new_hash(tmp_path):
    path,_,rows=make_sample(tmp_path)
    rows[2]['math_prompt']='Made up mathematics'
    rows[2]['table_fingerprint']=pairs.sha('Made up mathematics')
    path.write_text('\n'.join(pairs.canon(r) for r in rows)+'\n',encoding='utf-8')
    with pytest.raises(ValueError,match='GENERATED_CASE_MISMATCH'):
        handoff.load_cases(path)


def test_generated_dataset_used_for_original_prompt_only_numeric_capture(tmp_path,monkeypatch):
    _,_,rows=make_sample(tmp_path)
    seen=[]
    class FakeTok:
        def encode(self,text,add_special_tokens=True):return list(text)
    from tools import nanojev_qwen_c0_compressive_train as source
    import numpy as np
    def fake_capture(torch,model,tok,observed,args):
        seen.extend(observed)
        return np.zeros((len(observed),8),dtype=np.float32)
    monkeypatch.setattr(source,'capture_features',fake_capture)
    handoff.capture_original_features(None,None,FakeTok(),rows[:3],'cpu',batch_size=3,max_length=5000)
    assert seen==[{'prompt':r['prompt_08']} for r in rows[:3]]
    assert all('math_prompt' not in i and 'factor_steps' not in i for i in seen)
