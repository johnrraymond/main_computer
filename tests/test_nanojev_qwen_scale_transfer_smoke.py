"""No-model CPU checks for exact scale-transfer suite and grading."""
import json
from pathlib import Path

from tools import nanojev_qwen_scale_transfer_smoke as m


def test_deck_is_deterministic_and_no_duplicate_ids():
    first, second = m.make_deck(), m.make_deck()
    assert first == second
    assert len(first) >= 90
    assert len(first) == len({r['id'] for r in first})
    groups = {r['group'] for r in first}
    assert {'word_to_integer','word_to_power','inverse_power','signed_cancel',
            'godel_coordinates','graham_symbolic','definition_boundary',
            'mixed_scale','small_coefficient','symbolic_power','unit_conversion'} <= groups


def test_exact_scales_preserve_coefficient_magnitude_and_units():
    rows={r['id']:r for r in m.make_deck()}
    assert rows['word_to_integer:trillion']['target']=='ANSWER: 10000000000000'
    assert rows['word_to_power:centillion']['target']=='ANSWER: 10 * 10^303 dollars'
    assert rows['small_coefficient:trillion']['target']=='ANSWER: 7000000000000'
    assert rows['mixed_scale:trillion_billion_small']['target']=='ANSWER: 12345'
    assert rows['signed_cancel:quadrillion_near_zero']['target']=='ANSWER: 1'
    assert rows['symbolic_power:1_e1000']['target']=='ANSWER: 1 * 10^1000'


def test_godel_examples_are_real_integer_encodings_not_undefined_spaces():
    rows={r['id']:r for r in m.make_deck()}
    assert rows['godel_coordinates:pair_0_0']['target']=='ANSWER: 0'
    assert rows['godel_coordinates:pair_1_-1']['target']==f'ANSWER: {m.cantor(2,1)}'
    assert rows['godel_coordinates:triple_-1_2_0']['target']==f'ANSWER: {m.cantor(m.cantor(1,4),0)}'
    assert rows['definition_boundary:undefined_universe']['target']=='ANSWER: NO'
    assert rows['worded_coordinates:trillion_signed']['target']==f'ANSWER: {m.cantor(m.cantor(m.zigzag(10),m.zigzag(0)),m.zigzag(-10))}'
    assert rows['graham_symbolic:identity_cancellation']['target']=='ANSWER: 0'


def test_custom_name_is_defined_only_in_its_question():
    rows={r['id']:r for r in m.make_deck()}
    assert rows['definition_boundary:undefined_scale']['target']=='ANSWER: NO'
    d=rows['definition_boundary:defined_scale']
    assert 'THIS QUESTION ONLY' in d['source']
    assert 'invented stipulation' in d['comment']
    assert d['target']=='ANSWER: 2 * 10^45'


def test_strict_and_normalized_answers_are_reported_separately():
    row=m._case('t','1','Q?',1000000,kind='integer')
    assert m.grade(row,'ANSWER: 1000000')['strict']
    g=m.grade(row,'ANSWER: 1,000,000')
    assert not g['strict'] and g['normalized']
    assert not m.grade(row,'ANSWER: 1000001')['normalized']
    assert not m.grade(row,'ANSWER: 1000000\nOTHER')['normalized']
    row=m._case('t','2','Q?','10 * 10^12 dollars',kind='power')
    assert m.grade(row,'ANSWER: 10*10^{12} dollars')['normalized']
    assert not m.grade(row,'ANSWER: 10 * 10^9 dollars')['normalized']
    assert m.grade(row,'ANSWER: 1 * 10^13 dollars')['normalized']
    assert m.grade(row,'ANSWER: 10000000000000 dollars')['normalized']


def test_group_report_counts_actual_errors():
    a=m._case('x','a','Q1','YES');b=m._case('x','b','Q2','NO')
    scored=[{**a,'grade':m.grade(a,'ANSWER: YES')},
            {**b,'grade':m.grade(b,'ANSWER: YES')}]
    result=m._aggregate(scored)['x']
    assert result=={'cases':2,'strict':1,'normalized':1,'failures':['x:b']}


def test_deck_stage_requires_no_model(tmp_path):
    m.main(['--stage','deck','--probe-output',str(tmp_path)])
    lines=(tmp_path/'transfer_cases.jsonl').read_text(encoding='utf8').splitlines()
    assert len(lines)==len(m.make_deck())
    assert json.loads(lines[0])['source'].startswith('PROBLEM: ')
    assert not (tmp_path/'report.json').exists()


def test_loader_uses_baseline_frozen_and_rejects_mismatch(tmp_path,monkeypatch):
    import types
    import sys
    import torch
    src=tmp_path/'baseline.pt';torch.save({'source_sha256':'sha','frozen_sha256':'fixed','lora':{'w':torch.tensor([1.])}},src)
    class Dummy:
        def eval(self):return self
        def requires_grad_(self,_):return self
    class Zero:
        @staticmethod
        def restore_lora(bb,state):bb.state=state
    model=Dummy()
    dummy_ladder=types.ModuleType('tools.nanojev_qwen_lm_radius_ladder')
    dummy_ladder._fresh_parent=lambda args:(model,object(),'sha','fixed',[],Zero)
    dummy_smoke=types.ModuleType('tools.nanojev_qwen_lm_deformation_smoke')
    from tools import nanojev_qwen_lm_radius_ladder as actual_ladder
    monkeypatch.setattr(actual_ladder,'_fresh_parent',dummy_ladder._fresh_parent)
    args=types.SimpleNamespace(output=str(tmp_path))
    bb,*_=m._load_model(args)
    assert float(bb.state['w'][0])==1.0
    monkeypatch.setattr(actual_ladder,'_fresh_parent',lambda args:(model,object(),'notsha','fixed',[],Zero))
    try:m._load_model(args)
    except RuntimeError as exc:assert 'MISMATCH' in str(exc)
    else:raise AssertionError('missing expected drift protection')


def test_generation_path_gets_sources_only_and_glass_is_a_readonly_hook(monkeypatch):
    import contextlib
    import sys
    import types
    seen=[]
    class Head:pass
    class FakeBB:
        def get_output_embeddings(self):return Head()
    class FakeGlass:
        @contextlib.contextmanager
        def hook(self,head):
            seen.append(('hook_on',type(head).__name__))
            try:yield
            finally:seen.append(('hook_off',type(head).__name__))
    ladder=types.ModuleType('tools.nanojev_qwen_lm_radius_ladder')
    def greedy(bb,tok,sources,device,max_tokens,max_new_tokens):
        assert sources and all(s.startswith('PROBLEM: ') and 'ANSWER:' not in s for s in sources)
        seen.append(('inference',len(sources)))
        return ['ANSWER: NO']*len(sources)
    ladder._greedy=greedy
    from tools import nanojev_qwen_lm_radius_ladder as actual_ladder
    monkeypatch.setattr(actual_ladder,'_greedy',greedy)
    args=types.SimpleNamespace(device='cpu',max_tokens=128,max_new_tokens=16,
                               eval_batch=2,progress_every=0)
    rows=m.make_deck()[:3]
    out=m._run_variant(args,rows,FakeBB(),object(),FakeGlass(),'probe')
    assert len(out)==3
    assert seen[0][0]=='hook_on' and seen[-1][0]=='hook_off'
    assert sum(v[1] for v in seen if v[0]=='inference')==3


def test_probe_output_never_overwrites_model_folder(tmp_path):
    import types
    src=tmp_path/'model';src.mkdir()
    args=types.SimpleNamespace(probe_output=str(src/'nested'),output=str(src),stage='deck')
    try:m.run(args)
    except RuntimeError as exc:assert 'OVERLAPS' in str(exc)
    else:raise AssertionError('probe must reject nested model output')
