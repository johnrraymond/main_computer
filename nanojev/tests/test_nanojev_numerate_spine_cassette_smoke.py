"""Offline tests for the Numerate Spine cassette; no HF model or network access."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from argparse import Namespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'tools'))
import nanojev_numerate_spine_cassette_smoke as cassette


def args(**overrides):
    kwargs = dict(models=['qwen'],totals=[5],encodings=['pipe'],widths=[1],
                  noises=['clean'],directions=['forward'],orders=['rule-first'],
                  seed=4,max_cases=300,max_prompt_tokens=2048,report_every=100,resume=False)
    kwargs.update(overrides)
    return Namespace(**kwargs)


def test_conserved_unit_swap_and_negative_control():
    cases = cassette.generate(args())
    assert len(cases) == 12
    plus = [x for x in cases if x['operator']=='+']
    minus = [x for x in cases if x['operator']=='-']
    assert len(plus)==len(minus)==6
    assert [(x['left'],x['right']) for x in plus] == [(i,5-i) for i in range(6)]
    assert {x['true_result'] for x in plus} == {5}
    assert [x['true_result'] for x in minus] == [-5,-3,-1,1,3,5]
    assert all(x['answer_candidates']==[4,5,6] for x in cases)
    assert len({x['id'] for x in cases})==12


def test_base_15_carry_boundary_is_correctly_represented():
    cases = cassette.generate(args(totals=[15]))
    assert len(cases)==32
    row = next(r for r in cases if r['operator']=='+' and r['left']==14)
    assert '||||||||||||||+|' in row['prompt']
    assert row['true_result']==15
    assert row['answer_candidates']==[14,15,16]


def test_zero_is_explicit_and_encoding_width_semantics():
    assert cassette.tally(0,'zero',1)=='EMPTY'
    assert cassette.tally(3,'zero',4)=='0'*12
    assert cassette.tally(2,'alternating',3)=='010101010101'
    assert cassette.tally(1,'pipe',1)=='|'
    with pytest.raises(ValueError,match='NEGATIVE'):
        cassette.tally(-1,'pipe',1)


def test_variations_preserve_gold_and_are_unique():
    a=args(totals=[3,5],encodings=['pipe','one'],widths=[1,16],
           noises=['clean','human','code'],directions=['forward','backward'],
           orders=['rule-first','expression-first'],max_cases=2000)
    cases=cassette.generate(a)
    assert len(cases)==(2*(3+1)+2*(5+1))*2*2*3*2*2
    assert len({x['prompt_sha256'] for x in cases})==len(cases)
    assert all(x['true_result']==x['total'] for x in cases if x['operator']=='+')
    with pytest.raises(ValueError,match='CAP_EXCEEDED'):
        cassette.generate(args(totals=[15],max_cases=5))


def test_forward_backward_are_different_prompts_same_semantics():
    cases=cassette.generate(args(directions=['forward','backward'],totals=[3]))
    f=next(c for c in cases if c['direction']=='forward' and c['left']==2 and c['operator']=='+')
    b=next(c for c in cases if c['direction']=='backward' and c['left']==2 and c['operator']=='+')
    assert f['prompt']!=b['prompt']
    assert f['true_result']==b['true_result']==3


def test_sealed_bank_guards_drift(tmp_path):
    a=args(totals=[3])
    rows=cassette.generate(a)
    identity=cassette.write_cases(tmp_path,rows,a)
    assert len(json.loads((tmp_path/'cases.jsonl').read_text().splitlines()[0])['prompt'])>10
    with pytest.raises(RuntimeError,match='OUTPUT_EXISTS'):
        cassette.write_cases(tmp_path,rows,a)
    a.resume=True
    assert cassette.write_cases(tmp_path,rows,a)==identity
    (tmp_path/'cases.jsonl').write_text('bad',encoding='utf-8')
    with pytest.raises(RuntimeError,match='IDENTITY_DRIFT'):
        cassette.write_cases(tmp_path,rows,a)


def test_operator_char_alignment_for_matched_expression():
    class FakeTokenizer:
        def __call__(self,text,add_special_tokens=True,return_offsets_mapping=True):
            return {'input_ids':list(range(len(text))),
                    'offset_mapping':[(i,i+1) for i in range(len(text))]}
    row=cassette.generate(args())[2]
    token_ids,idx,pos=cassette.locate_operator(FakeTokenizer(),row['prompt'])
    assert token_ids[idx]==idx==pos
    assert row['prompt'][pos] in '+-'
    assert row['prompt'].count('EXPR: ')==1


def test_cli_defaults():
    a=cassette.parser().parse_args([])
    assert a.models==['qwen'] and a.totals==[5]
    assert a.encodings==['pipe'] and a.noises==['clean']
    assert a.max_cases==5000


def test_real_pipeline_toy_model_end_to_end():
    # The built-in self-test exercises the same capture, on-disk resume, and plotting code.
    cassette.self_test()


def test_windows_crlf_bank_resume_is_allowed_without_loosening_identity(tmp_path):
    a = args(totals=[3])
    cases = cassette.generate(a)
    identity = cassette.write_cases(tmp_path, cases, a)
    bank_path = tmp_path / 'cases.jsonl'
    original = bank_path.read_bytes()
    assert b'\r\n' not in original
    bank_path.write_bytes(original.replace(b'\n', b'\r\n'))
    a.resume = True
    assert cassette.write_cases(tmp_path, cases, a) == identity
    # Verify both the legacy CRLF file and new manifest remain untouched.
    assert bank_path.read_bytes() == original.replace(b'\n', b'\r\n')
    assert identity['bank_sha256'] == cassette.digest(original)
    corrupted = bank_path.read_bytes().replace(b'\r\n', b'\n', 1)
    bank_path.write_bytes(corrupted)
    with pytest.raises(RuntimeError,match='CASSETTE_BANK_IDENTITY_DRIFT'):
        cassette.write_cases(tmp_path, cases, a)


def test_analyze_only_needs_existing_bank_and_never_creates_it(tmp_path):
    with pytest.raises(RuntimeError,match='CASSETTE_NO_SAVED_BANK_FOR_ANALYSIS'):
        cassette.main(['--analyze-only','--output-root',str(tmp_path)])
    assert not list(tmp_path.iterdir())


def test_analyze_saved_fails_closed_when_files_missing(tmp_path):
    a=args(totals=[3])
    cases=cassette.generate(a)
    bank=cassette.write_cases(tmp_path,cases,a)
    model_dir=tmp_path/'qwen'
    model_dir.mkdir()
    info={'source':cassette.SOURCE['qwen'][0], 'requested_revision':cassette.SOURCE['qwen'][1]}
    cassette.write_json(model_dir/'model_manifest.json',{
        'schema':cassette.SCHEMA, 'bank_sha256':bank['bank_sha256'],
        'model_info':info, 'max_prompt_tokens':a.max_prompt_tokens,
    })
    with pytest.raises(RuntimeError,match='CASSETTE_MISSING_SAVED_RECORD:000000.npz'):
        cassette.analyze_saved('qwen',cases,bank,tmp_path)
