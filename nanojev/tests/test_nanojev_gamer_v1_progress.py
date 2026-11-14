"""Gamer v1 LoRA heartbeat regression: no metrics or generation changes."""
from contextlib import nullcontext
from types import SimpleNamespace
import torch
from tools.nanojev_gamer_v1_handoff import evaluate_lora


class FakeTokenizer:
    pad_token_id = 0
    eos_token_id = 2
    def __call__(self, text, truncation, return_tensors):
        assert truncation is False
        return {'input_ids': torch.tensor([[7,8]]), 'attention_mask':torch.ones((1,2),dtype=torch.long)}
    def decode(self, ids, skip_special_tokens):
        assert skip_special_tokens is True
        return 'math' if int(ids[0])==9 else 'other'


class FakeModel:
    def eval(self): pass
    def generate(self, **kw):
        assert kw['max_new_tokens']==32
        assert kw['do_sample'] is False
        return torch.tensor([[7,8,9]])


def test_dev_evaluation_reports_start_and_finish_for_every_prompt(capsys):
    dev=[{'game_id':'hawk_dove', 'prompt_08':'Hawk', 'math_prompt':'math'},
         {'game_id':'matching_pennies', 'prompt_08':'Heads', 'math_prompt':'other'}]
    report=evaluate_lora(torch,FakeModel(),FakeTokenizer(),dev,
                         device='cpu',max_length=32,max_new_tokens=32)
    assert report['exact']==1 and report['total']==2 and report['strict_exact']==0.5
    assert report['by_game']['hawk_dove']=={'exact':1,'total':1}
    log=capsys.readouterr().out
    assert 'START 1/2' in log and 'DONE 1/2' in log
    assert 'START 2/2' in log and 'DONE 2/2' in log
    assert 'evaluation complete: exact=1/2' in log
