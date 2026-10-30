"""CPU-only protocol probe tests. No Qwen weights or CUDA required."""
import json
from pathlib import Path
from types import SimpleNamespace

from tools import nanojev_qwen_scale_transfer_answer_prefix_probe as p
from tools import nanojev_qwen_scale_transfer_smoke as transfer


def test_prefix_is_constant_across_targets_and_has_no_extra_newline():
    a=transfer._case('a','1','Calculate 2 + 3',5)
    b=transfer._case('a','2','Calculate 2 + 3',99999)
    assert p._input_prefix(a['source'])==p._input_prefix(b['source'])
    assert p._input_prefix(a['source'])=='PROBLEM: Calculate 2 + 3\nANSWER:'
    assert p._input_prefix(a['source'],space=True).endswith('ANSWER: ')


def test_score_prefill_keeps_strict_and_equivalence_separate():
    row=transfer._case('x','y','Q?',1000,kind='integer')
    assert p._score_prefill(row,' 1000')['strict']
    result=p._score_prefill(row,'1000')
    assert not result['strict']
    assert result['normalized']
    assert result['format_only_missing_space']
    assert not p._score_prefill(row,' 1001')['normalized']
    assert not p._score_prefill(row,' PROBLEM: new question')['normalized']


def test_greedy_prefill_injects_only_answer_label_not_gold():
    import torch
    strings=[]
    class Tokenizer:
        pad_token_id=0
        eos_token_id=99
        def __call__(self, s,add_special_tokens=False):
            strings.append(s)
            assert s.endswith('\nANSWER:')
            assert '1000' not in s
            return {'input_ids':[4,12]}
        def decode(self,ids,skip_special_tokens=True):
            return ''.join({1:' 1000',2:'\n'}.get(v,'?') for v in ids)
    class Decoder:
        def __call__(self,input_ids,attention_mask,use_cache,return_dict):
            assert not use_cache and return_dict
            return SimpleNamespace(last_hidden_state=input_ids.float().unsqueeze(-1))
    class Head:
        weight=torch.ones(3,1)
        def __call__(self,h):
            # after ANSWER: token 12 -> 1; after 1000 token 1 -> newline token 2
            output=torch.zeros((len(h),3),device=h.device)
            output[:,1]=torch.where(h[:,0]==12,10.,0.)
            output[:,2]=torch.where(h[:,0]==1,10.,0.)
            return output
    class Model:
        def get_decoder(self):return Decoder()
        def get_output_embeddings(self):return Head()
    result=p._generate_prefilled(Model(),Tokenizer(),['PROBLEM: calculate'],
                                 'cpu',128,16)
    assert result==[' 1000\n']
    assert strings==['PROBLEM: calculate\nANSWER:']


def test_scoring_aggregate_reports_repair_not_just_syntax():
    row=transfer._case('x','y','Q?',1000,kind='integer')
    rows=[{**row,'grade':p._score_prefill(row,'1000')}]
    assert p._scores(rows)=={'tested':1,'strict':0,'normalized':1,
             'by_group':{'x':{'cases':1,'strict':0,'normalized':1,'failures':[]}},
             'missing_space_only':1}


def test_output_is_separate_from_backbone_and_raw_reports(tmp_path):
    import pytest
    a=tmp_path/'glass';a.mkdir()
    b=tmp_path/'raw';b.mkdir()
    args=SimpleNamespace(output=str(a),raw_probe_output=str(b),
                         probe_output=str(b),stage='deck')
    with pytest.raises(RuntimeError,match='RAW_RESULTS'):
        p.run(args)
    args.probe_output=str(a/'nested')
    with pytest.raises(RuntimeError,match='BACKBONE'):
        p.run(args)
    args.probe_output=str(tmp_path/'prefill')
    p.run(args)
    assert len((tmp_path/'prefill'/'transfer_cases.jsonl').read_text().splitlines())==112
    assert not (b/'report.json').exists()


def test_with_prefix_space_exact_output_has_correct_format():
    row=transfer._case('x','a','Question',1000,kind='integer')
    result=p._score_prefill(row,'1000',prefix_space=True)
    assert result['strict'] and result['normalized']


def test_raw_report_is_not_overwritten_by_deck_stage(tmp_path):
    import json
    base=tmp_path/'baseline';base.mkdir()
    raw=tmp_path/'raw';raw.mkdir()
    original=raw/'report.json'
    original.write_text(json.dumps({'baseline':{'normalized':9}}))
    output=tmp_path/'new'
    p.run(SimpleNamespace(output=str(base),probe_output=str(output),
                raw_probe_output=str(raw),stage='deck'))
    assert json.loads(original.read_text())['baseline']['normalized']==9
