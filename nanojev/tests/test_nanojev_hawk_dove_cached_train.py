"""Exact-capture memoization without CUDA, Qwen downloads, or checkpoint mutation."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import torch
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_altreg_math_v3_decoder_experiment as v3


class FrozenCacheTests(unittest.TestCase):
    def test_exact_single_and_multi_prompt_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            store = cached.FrozenCaptureCache(Path(directory), {'model': 'frozen-test'}, 1)
            calls = []
            def capture(backbone, tokenizer, device, prompts, limit):
                calls.append(tuple(prompts))
                n = len(prompts)
                seq = torch.zeros((n, 3, 4), dtype=torch.float32)
                for i, prompt in enumerate(prompts):
                    seq[i] = torch.arange(12, dtype=torch.float32).reshape(3, 4) + len(prompt)
                return torch.arange(n * 8, dtype=torch.float32).reshape(n, 8), seq, torch.ones(n,3,dtype=torch.bool)
            model = {'backbone': object(), 'tokenizer': object(), 'device': 'cpu',
                     'config': {'max_prompt_tokens': 100}}
            with mock.patch.object(v3, 'full_capture', side_effect=capture):
                old = store.capture(model, ['A'])
                second = store.capture(model, ['A'])
                self.assertEqual(len(calls), 1)
                for a,b in zip(old,second): self.assertTrue(torch.equal(a,b))
                group = store.capture(model, ['A','longer B'])
                self.assertEqual(len(calls), 2)
                group_again = store.capture(model, ['A','longer B'])
                self.assertEqual(len(calls), 2)
                for a,b in zip(group,group_again): self.assertTrue(torch.equal(a,b))
                reloaded = cached.FrozenCaptureCache(Path(directory), {'model':'frozen-test'}, 0)
                from_disk = reloaded.capture(model,['A','longer B'])
                for a,b in zip(group,from_disk): self.assertTrue(torch.equal(a,b))
                self.assertEqual(reloaded.stats()['disk_hits'], 1)
                self.assertEqual(store.stats()['misses'], 2)
                with self.assertRaisesRegex(RuntimeError, 'IDENTITY_DRIFT'):
                    cached.FrozenCaptureCache(Path(directory), {'model':'different'}, 1)

    def test_same_prediction_and_decoder_gradients(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            store = cached.FrozenCaptureCache(Path(directory), {'model':'mock'}, 2)
            class Parent:
                def extract(self, flat): return flat[:, :4]
                def xy_coord(self, h): return h[:, :2]
            class Decoder(torch.nn.Module):
                def __init__(self):
                    super().__init__(); self.weight = torch.nn.Linear(4, 2)
                def forward(self, h, pxy, neutral, seq, mask):
                    return self.weight(h) + pxy + seq[:,0,:2]
            class Head(torch.nn.Module):
                def forward(self, seq, mask): return seq.mean(dim=1)
            class Base:
                OP_IDS = {'written_sub':0}
            calls=[]
            def capture(backbone, tokenizer, device, prompts, limit):
                calls.append(tuple(prompts))
                n = len(prompts)
                flat = torch.ones((n,8),dtype=torch.float32)
                seq = torch.ones((n,5,4),dtype=torch.float32)
                mask = torch.ones((n,5),dtype=torch.bool)
                return flat,seq,mask
            decoder=Decoder()
            model={'torch':torch,'v3':v3,'base':Base,'device':'cpu','backbone':object(),
                   'tokenizer':object(),'parent':Parent(),'decoder':decoder,
                   'gamehead':Head(), 'config':{'max_prompt_tokens':64}}
            with mock.patch.object(v3,'full_capture',side_effect=capture):
                raw,_ = ex.forward(model,['test'])
                raw.sum().backward(); expected=[v.grad.clone() for v in decoder.parameters()]
                decoder.zero_grad(set_to_none=True)
                out,_=cached.cached_forward(model,['test'],store)
                self.assertTrue(torch.equal(raw,out))
                out.sum().backward()
                for v,w in zip(decoder.parameters(),expected):
                    self.assertTrue(torch.equal(v.grad,w))
                _=cached.cached_forward(model,['test'],store)
                self.assertEqual(len(calls),2) # one original + one cache miss
                self.assertEqual(store.stats()['ram_hits'],1)

if __name__ == '__main__': unittest.main()
