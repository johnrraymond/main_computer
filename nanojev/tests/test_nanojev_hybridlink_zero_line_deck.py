"""Deterministic synthetic bounded saddle coverage, offline and without model weights."""
from collections import Counter
from types import SimpleNamespace

import pytest

from tools import nanojev_hybridlink_zero_line_deck as deck
from tools import nanojev_hybridlink_readiness as ready
from tools import nanojev_qwen_problem_answer_stage1 as stage1


def sample(per_cell=1, seed=42257):
    return deck.build(max_rung=8, radius=8, operand_limit=1024,
                      per_cell=per_cell, seed=seed, oracle=ready.verify_oracle)


def test_full_signed_zero_rectangle_and_provenance():
    by_rung, available, selected = sample()
    assert len(selected) == 9*17*3 == 459
    assert all(len(by_rung[t])==51 for t in range(9))
    assert len({(c['rung'],c['answer'],c['band']) for c in selected})==459
    assert all(c['full_chain'] and c['origin']=='synthetic_diagnostic_only' for c in selected)
    assert all(c['answer'] in range(-8,9) and c['steps']==c['rung']+1 for c in selected)
    assert all(c['max_literal']<=1024 for c in selected)
    for rung in range(9):
        zero=[c for c in by_rung[rung] if c['answer']==0]
        assert len(zero)==3
        assert {c['band'] for c in zero}=={'0-8','9-64','65-1024'}
    rows, summary=ready.coverage_matrix(available,selected,8,8,1,1024,origin='generated')
    assert len(rows)==459
    assert {r['coverage'] for r in rows}=={'generated_sampled'}
    assert all(r['available_recorded']==0 and r['available_generated']==1 for r in rows)
    assert all(x['adequately_sampled_cells']==51 and x['recorded_cells']==0
               and x['suffix_only_cases']==0 for x in summary.values())


def test_exact_intermediate_states_and_cancellation_across_rungs():
    _,_,cases=sample()
    for c in cases:
        evidence=ready.verify_oracle(c['chain'],stage1)
        assert evidence['answer']==c['answer']
        leaves=ready._leaves(__import__('ast').parse(c['chain'][0][9:],mode='eval').body)
        subtotal=0
        for n in leaves:
            assert abs(n)<=1024
            subtotal+=n
            assert abs(subtotal)<=1024
        assert subtotal==c['answer']
        assert len(c['chain'])==c['rung']+2
        if c['band']=='65-1024':
            assert max(abs(n) for n in leaves)>=65
            assert abs(leaves[0]+leaves[1])<=8


def test_generated_multi_case_independent_reproducibility():
    _,_,a=sample(per_cell=2)
    _,_,b=sample(per_cell=2)
    assert len(a)==918 and deck.fingerprint(a)==deck.fingerprint(b)
    assert len({tuple(c['chain']) for c in a})==918
    assert len({tuple(c['chain']) for c in a if c['answer']==0})==54
    _,_,c=sample(per_cell=2,seed=456)
    assert deck.fingerprint(a)!=deck.fingerprint(c)


def test_magnitude_band_extrema_and_partial_cap():
    _,_,cases=deck.build(max_rung=1,radius=2,operand_limit=127,
                         per_cell=2,seed=42,oracle=ready.verify_oracle)
    assert len(cases)==2*5*3*2
    assert {c['band'] for c in cases}=={'0-8','9-64','65-127'}
    assert all(c['max_literal']<=127 for c in cases)


def test_not_train_samples_or_faux_coverage_certifications():
    _,available,selected=sample(per_cell=1)
    rows,summary=ready.coverage_matrix(available,selected,8,8,2,1024,origin='generated')
    assert {r['coverage'] for r in rows}=={'under_sampled'}
    assert summary['8']['adequately_sampled_cells']==0
    assert all(not c.get('trained') for c in selected)


def test_generated_preview_no_gpu_models(tmp_path,monkeypatch):
    from tools import nanojev_qwen_numeric_dual_cutover as parent
    monkeypatch.setattr(parent,'load_parent',lambda a: ({'seed':1},{}))
    monkeypatch.setattr(parent,'_rows',lambda s,cfg,split:stage1._rows_for([9,-9],'dev',0))
    args=SimpleNamespace(stage='readiness-preview',split='dev',stage1_checkpoint=str(tmp_path/'stage1'/'best.pt'),
        text_checkpoint=str(tmp_path/'text'/'best.pt'),detector_checkpoint=None,checkpoint=None,
        cache_dir=None,output=str(tmp_path/'report'),radius=8,max_rung=8,max_cases_per_rung=24,
        min_per_cell=1,seed=1,max_tokens=192,min_gold_cosine=.6,answer_threshold=.9,
        qwen_text_probe_per_rung=0,qwen_text_max_new_tokens=48,
        operand_limit=1024,generate_zero_line=True,generated_per_cell=1)
    result=ready.run(args)
    assert result['selected_cases']==459 and result['full_chain_test'] is True
    assert result['coverage']['8']['adequately_sampled_cells']==51
    assert result['training_overlap_verified'] is False
    assert (tmp_path/'report'/'zero_line_deck.jsonl').exists()
    assert (tmp_path/'report'/'coverage.csv').exists()
    assert (tmp_path/'report'/'readiness.json').exists()
    assert 'generated_sampled' in (tmp_path/'report'/'coverage.csv').read_text()
    assert 'Generated read-only full-chain evaluation' in (tmp_path/'report'/'readiness.md').read_text()


def test_cli_exposes_synthetic_option_and_invalid_count():
    import inspect
    from tools import nanojev_hybridlink_latent_diagnostics as diag
    assert '--generate-zero-line' in inspect.getsource(diag.main)
    args=SimpleNamespace(max_rung=8,radius=8,operand_limit=1024,
        max_cases_per_rung=24,min_per_cell=1,max_tokens=192,min_gold_cosine=.6,
        answer_threshold=.9,qwen_text_probe_per_rung=0,qwen_text_max_new_tokens=48,
        generated_per_cell=9)
    with pytest.raises(ValueError,match='READINESS_INVALID_SAMPLING'):
        ready._validate(args)
