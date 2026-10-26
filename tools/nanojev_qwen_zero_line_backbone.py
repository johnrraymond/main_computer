#!/usr/bin/env python3
"""Bounded signed-zero-line backbone SFT; no arithmetic executor or HybridLink training.

Directly updates trainable low-rank Qwen attention weights, keeping pretrained
weights and the verified v2 HybridLink fixed. Teacher thoughts are SFT labels,
never presented to the backbone in the blind evaluation. An independent
oracle-vs-corrupt *non-answer-bearing* thought probe tests causal usefulness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import sys

import torch
from torch import nn
from torch.nn import functional as F

from tools import nanojev_qwen_hybridagent as hybrid
from tools import nanojev_qwen_hybridagent_activation as activation

SCHEMA = 'nanojev.qwen3-06b.zero-line-backbone-sft-lora.v1'
BASELINE = hybrid.DEFAULT_OUTPUT
DEFAULT_OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_zero_line_backbone_sft_v1'
ORACLE_THOUGHT = 'The equal-and-opposite terms cancel. Keep the signed remainder.'
CORRUPT_THOUGHT = 'The equal-and-opposite terms reinforce. Keep the large magnitude.'


class LoRALinear(nn.Module):
    """Trainable fp32 low-rank delta around a completely frozen projection."""
    def __init__(self, original: nn.Linear, rank: int, alpha: float):
        super().__init__()
        assert isinstance(original, nn.Linear) and rank > 0 and alpha > 0, 'ZERO_LORA_BAD_PROJECTION'
        self.base = original
        self.base.requires_grad_(False)
        self.lora_a = nn.Parameter(torch.empty(rank, original.in_features, device=original.weight.device,
                                               dtype=torch.float32))
        self.lora_b = nn.Parameter(torch.zeros(original.out_features, rank, device=original.weight.device,
                                               dtype=torch.float32))
        self.scale = float(alpha) / rank
        nn.init.kaiming_uniform_(self.lora_a, a=math.sqrt(5))

    def forward(self, x):
        base = self.base(x)
        delta = F.linear(F.linear(x.float(), self.lora_a), self.lora_b) * self.scale
        return base + delta.to(base.dtype)


def install_lora(backbone, rank: int, alpha: float, *, required_layers: int | None = None):
    assert all(not p.requires_grad for p in backbone.parameters()), 'ZERO_BACKBONE_MUST_BE_FROZEN_FIRST'
    projections = [(name, layer) for name, layer in backbone.named_modules()
                   if name.endswith(('.q_proj', '.v_proj')) and isinstance(layer, nn.Linear)]
    if required_layers is not None:
        assert len(projections) == 2 * required_layers, (
            f'ZERO_LORA_PROJECTION_COUNT_MISMATCH:{len(projections)} expected={2 * required_layers}')
    assert projections, 'ZERO_LORA_NO_QWEN_ATTENTION_PROJECTIONS'
    names = []
    for name, layer in projections:
        parent_name, attribute = name.rsplit('.', 1)
        parent = backbone.get_submodule(parent_name)
        setattr(parent, attribute, LoRALinear(layer, rank, alpha))
        names.append(name)
    return tuple(names)


def lora_named_parameters(backbone):
    return [(n, p) for n, p in backbone.named_parameters()
            if n.endswith(('.lora_a', '.lora_b'))]


def lora_state(backbone):
    return {n: p.detach().cpu().clone() for n, p in lora_named_parameters(backbone)}


def restore_lora(backbone, state):
    actual = dict(lora_named_parameters(backbone))
    assert set(actual) == set(state), 'ZERO_LORA_CHECKPOINT_KEY_DRIFT'
    with torch.no_grad():
        for name, tensor in state.items():
            assert tensor.shape == actual[name].shape, 'ZERO_LORA_CHECKPOINT_SHAPE_DRIFT:' + name
            actual[name].copy_(tensor.to(actual[name].device, dtype=actual[name].dtype))


def frozen_backbone_sha256(backbone):
    """SHA256 of *every* frozen tensor, streamed one parameter at a time."""
    digest = hashlib.sha256()
    with torch.no_grad():
        for name, param in backbone.named_parameters():
            if name.endswith(('.lora_a', '.lora_b')): continue
            assert not param.requires_grad, 'ZERO_NONLORA_PARAMETER_TRAINABLE:' + name
            digest.update(name.encode('utf8'))
            digest.update(str(tuple(param.shape)).encode())
            digest.update(str(param.dtype).encode())
            # NumPy cannot materialize BF16 arrays. Reinterpret the raw tensor
            # bytes instead of converting numerical values to float32 (which
            # would change the digest and could conceal representation drift).
            # This produces exactly the same bytes as legacy .numpy().tobytes()
            # for NumPy-supported dtypes, preserving existing saved hashes.
            raw = param.detach().contiguous().cpu().reshape(-1).view(torch.uint8)
            digest.update(raw.numpy().tobytes())
    return digest.hexdigest()


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def make_cases(seed: int = 71, train_count: int = 96, dev_count: int = 18, test_count: int = 18):
    """Disjoint signed/zero and composed cancellation; disjoint magnitude ranges."""
    rng = random.Random(seed)
    cases = {}
    for split, n, lo, hi in [('train', train_count, 101, 65000),
                             ('dev', dev_count, 100003, 800000),
                             ('test', test_count, 1000003, 2000000)]:
        assert n >= 6 and n % 6 == 0, 'ZERO_CASE_COUNT_MUST_BE_MULTIPLE_OF_SIX'
        taken, rows = set(), []
        for i in range(n):
            a = rng.randint(lo, hi)
            while a in taken:
                a = rng.randint(lo, hi)
            taken.add(a)
            if i % 3 == 0:
                delta = 0
            elif i % 3 == 1:
                delta = rng.randint(1, 15)
            else:
                delta = -rng.randint(1, 15)
            composed = i % 2 == 1
            a_signed = a if (i // 3) % 2 == 0 else -a
            if composed:
                u = rng.choice([j for j in range(-15, 16) if j not in (0, delta)])
                v = delta - u
                expression = f'({a_signed}) + (-({a_signed}) + ({u})) + ({v})'
            else:
                u, v = delta, 0
                expression = f'({a_signed}) + (-({a_signed}) + ({delta}))'
            rows.append({'id': split + '_' + str(i).zfill(3), 'split': split,
                         'a': a_signed, 'u': u, 'v': v, 'answer': delta,
                         'family': 'composed' if composed else 'direct',
                         'sign': 'zero' if delta == 0 else 'positive' if delta > 0 else 'negative',
                         'expression': expression})
        # Interleave all six (sign, family) classes. Every 6 consecutive
        # samples expose all combinations, including in short smoke runs.
        groups = {(sign, family): [] for sign in ('negative','zero','positive')
                  for family in ('direct','composed')}
        for row in rows: groups[(row['sign'],row['family'])].append(row)
        for group in groups.values(): rng.shuffle(group)
        ordered=[]
        for j in range(n//6):
            for key in groups:
                ordered.append(groups[key][j])
        cases[split] = ordered
    all_mag = [[abs(row['a']) for row in cases[s]] for s in ('train', 'dev', 'test')]
    assert max(all_mag[0]) < min(all_mag[1]) and max(all_mag[1]) < min(all_mag[2]), (
        'ZERO_MAGNITUDE_SPLIT_OVERLAP')
    for split, rows in cases.items():
        assert len({r['a'] for r in rows}) == len(rows), 'ZERO_REPEAT_MAGNITUDE:' + split
        assert all(sum(r['sign'] == s for r in rows) == len(rows)//3 for s in ('zero','positive','negative')), (
            'ZERO_SIGN_IMBALANCE:' + split)
        assert all((r['u'] + r['v']) == r['answer'] for r in rows), 'ZERO_ARITHMETIC_INCONSISTENCY'
    return cases


def text_record(row):
    prompt = 'Question: Compute ' + row['expression'] + '.\nReasoning: '
    if row['family'] == 'direct':
        thought = 'The equal-and-opposite terms cancel to zero. The signed remainder is ' + str(row['answer']) + '.'
    else:
        thought = ('The equal-and-opposite terms cancel to zero. Combine the two signed '
                   'remainders to obtain ' + str(row['answer']) + '.')
    target = thought + '\nAnswer: ' + str(row['answer']) + '\n'
    return prompt, target


def encoded_sft(tokenizer, row, max_seq: int):
    prompt, completion = text_record(row)
    p = tokenizer(prompt, add_special_tokens=False)['input_ids']
    t = tokenizer(completion, add_special_tokens=False)['input_ids']
    assert p and t and len(p) + len(t) <= max_seq, 'ZERO_SFT_SEQUENCE_TOO_LONG'
    ids = p + t
    labels = [-100] * len(p) + t
    assert labels[len(p)] == t[0] and all(v == -100 for v in labels[:len(p)]), (
        'ZERO_SFT_TARGET_MASK_LEAK')
    return ids, labels


def answer_logprob(backbone, tokenizer, row, thought: str, device: str):
    """Gold answer used only by scorer; no gold numerals in the thought itself."""
    assert not re.search(r'\d', thought), 'ZERO_ORACLE_THOUGHT_CONTAINS_NUMERICAL_ANSWER'
    context = 'Question: Compute ' + row['expression'] + '.\nReasoning: ' + thought + '\nAnswer: '
    start = tokenizer(context, add_special_tokens=False)['input_ids']
    target = tokenizer(str(row['answer']), add_special_tokens=False)['input_ids']
    assert start and target, 'ZERO_EMPTY_PROBE'
    ids = torch.tensor([start + target], dtype=torch.long, device=device)
    with torch.no_grad():
        scores = backbone(input_ids=ids, attention_mask=torch.ones_like(ids), use_cache=False,
                          return_dict=True).logits[0].float()
        chosen = torch.tensor(target, dtype=torch.long, device=device)
        positions = torch.arange(len(start)-1, len(start)-1+len(target), device=device)
        return float(F.log_softmax(scores.index_select(0, positions), -1)
                     .gather(-1, chosen[:,None]).sum().item())


def thought_utility(backbone, tokenizer, rows, device):
    if not rows: raise AssertionError('ZERO_UTILITY_EMPTY_DATASET')
    was_training = backbone.training
    backbone.eval()
    try:
        details = []
        for row in rows:
            good = answer_logprob(backbone, tokenizer, row, ORACLE_THOUGHT, device)
            bad = answer_logprob(backbone, tokenizer, row, CORRUPT_THOUGHT, device)
            details.append({'id': row['id'], 'gold_logprob_correct': good,
                            'gold_logprob_corrupted': bad, 'difference': good-bad})
        return {'mean_advantage': sum(d['difference'] for d in details)/len(details),
                'positive_advantage_cases': sum(d['difference'] > 0 for d in details),
                'cases': len(details), 'records': details}
    finally:
        backbone.train(was_training)


def blind_completion(backbone, tokenizer, row, device, *, tokens=56):
    """Greedy text-only answer with no teacher thought and no gold in input."""
    prefix = 'Question: Compute ' + row['expression'] + '.\nReasoning: '
    prompt_ids = tokenizer(prefix, add_special_tokens=False)['input_ids']
    assert prompt_ids, 'ZERO_BLIND_EMPTY_PROMPT'
    ids = torch.tensor([prompt_ids], device=device, dtype=torch.long)
    continuation = []
    backbone.eval()
    with torch.no_grad():
        out = backbone(input_ids=ids, attention_mask=torch.ones_like(ids),
                       use_cache=True, return_dict=True)
        cache = out.past_key_values
        assert cache is not None, 'ZERO_BLIND_CACHE_REQUIRED'
        logits = out.logits[:, -1, :]
        for _ in range(tokens):
            token = logits.argmax(-1,keepdim=True)
            index = int(token.item())
            if tokenizer.eos_token_id is not None and index == tokenizer.eos_token_id: break
            continuation.append(index)
            ids = token
            out = backbone(input_ids=ids, past_key_values=cache, use_cache=True, return_dict=True)
            cache = out.past_key_values
            logits = out.logits[:, -1, :]
    text = tokenizer.decode(continuation, skip_special_tokens=True)
    found = re.search(r'(?:^|\n)\s*Answer:\s*([+-]?\d+)(?!\d)', text)
    predicted = int(found.group(1)) if found else None
    return {'id':row['id'],'expected':row['answer'],'predicted':predicted,
            'correct':predicted==row['answer'], 'completion':text[:280]}


def blind_probe(backbone, tokenizer, rows, device, *, tokens=56):
    was_training = backbone.training
    try:
        records = [blind_completion(backbone, tokenizer, r, device, tokens=tokens) for r in rows]
        return {'cases':len(rows), 'correct':sum(x['correct'] for x in records),
                'accuracy':sum(x['correct'] for x in records)/len(records), 'records':records}
    finally:
        backbone.train(was_training)


def gradient_report(backbone):
    """Return named gradient diagnostics; never collapse missing and overflow."""
    trainable = lora_named_parameters(backbone)
    assert trainable, 'ZERO_SFT_NO_LORA_PARAMETERS'
    missing, nonfinite, present, nonzero = [], [], [], []
    for name, param in trainable:
        g = param.grad
        if g is None:
            missing.append(name)
        else:
            present.append(name)
            if not bool(torch.isfinite(g).all()):
                nonfinite.append(name)
            elif bool(g.abs().max() > 0):
                nonzero.append(name)
    return {'present':present, 'missing':missing, 'nonfinite':nonfinite, 'nonzero':nonzero}


def resolve_amp_dtype(device, choice='auto'):
    """BF16's exponent range avoids FP16 backprop overflow on supported GPUs."""
    assert choice in ('auto', 'bfloat16', 'float16'), 'ZERO_SFT_UNSUPPORTED_AMP_DTYPE'
    if not str(device).startswith('cuda'):
        return 'cpu'
    bf16 = bool(torch.cuda.is_bf16_supported())
    if choice == 'bfloat16' and not bf16:
        raise RuntimeError('ZERO_SFT_BF16_NOT_SUPPORTED_ON_THIS_GPU')
    return 'bfloat16' if choice == 'bfloat16' or (choice == 'auto' and bf16) else 'float16'


def train_one(backbone, tokenizer, sample, optimizer, device, *, max_seq=192, scaler=None,
              amp_dtype='auto', max_overflow_retries=5):
    ids, labels = encoded_sft(tokenizer, sample, max_seq)
    x = torch.tensor([ids], dtype=torch.long, device=device)
    y = torch.tensor([labels], dtype=torch.long, device=device)
    backbone.train()
    cuda = str(device).startswith('cuda')
    effective = resolve_amp_dtype(device, amp_dtype) if amp_dtype != 'cpu' else 'cpu'
    # A single attempt has exactly one forward/backward. No optimizer step may
    # occur with missing, zero or nonfinite adapter gradients.
    for attempt in range(max_overflow_retries + 1):
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type='cuda' if cuda else 'cpu', enabled=cuda,
                            dtype=torch.bfloat16 if effective == 'bfloat16' else torch.float16):
            result = backbone(input_ids=x, attention_mask=torch.ones_like(x), labels=y,
                              use_cache=False, return_dict=True)
            loss = result.loss
        assert loss is not None and bool(torch.isfinite(loss)), (
            f'ZERO_SFT_NONFINITE_LOSS:amp={effective},attempt={attempt + 1}')
        if scaler is None:
            loss.backward()
        else:
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
        report = gradient_report(backbone)
        if report['nonfinite']:
            # Only a genuinely active FP16 loss scaler may safely recover by
            # backoff and full recomputation.  In BF16, a nonfinite gradient
            # is not just a loss-scale problem and must fail visibly.
            active_scaler = (scaler is not None and getattr(scaler, 'is_enabled', lambda: False)())
            if active_scaler and effective == 'float16' and attempt < max_overflow_retries:
                old_scale = float(scaler.get_scale())
                new_scale = max(1., old_scale / 2.)
                if old_scale > new_scale:
                    scaler.update(new_scale=new_scale)
                    print('ZERO_BACKBONE_GRADIENT_RETRY=' + json.dumps({
                        'attempt':attempt + 1, 'amp_dtype':effective,
                        'old_scale':old_scale,'new_scale':new_scale,
                        'nonfinite_count':len(report['nonfinite']),
                        'first_nonfinite':report['nonfinite'][0]}, sort_keys=True), flush=True)
                    continue
            raise AssertionError('ZERO_SFT_GRADIENT_NONFINITE:' + json.dumps({
                'amp_dtype':effective, 'attempt':attempt + 1,
                'loss_scale':float(scaler.get_scale()) if active_scaler else None,
                'names':report['nonfinite'][:8], 'count':len(report['nonfinite'])}, sort_keys=True))
        # LoRA's A-matrix legitimately has a ZERO gradient on the first
        # update, because B starts at zero. Its gradient must still exist.
        # Any missing gradient signals a disconnected adapter projection.
        assert not report['missing'], 'ZERO_SFT_GRADIENT_MISSING:' + repr(report['missing'][:8])
        assert report['present'], 'ZERO_SFT_ALL_GRADIENTS_MISSING'
        assert report['nonzero'], 'ZERO_SFT_ALL_GRADIENTS_ZERO'
        torch.nn.utils.clip_grad_norm_([p for _,p in lora_named_parameters(backbone)], 1.0)
        if scaler is None:
            optimizer.step()
        else:
            scaler.step(optimizer)
            scaler.update()
        assert all(p.grad is None for n,p in backbone.named_parameters()
                   if not n.endswith(('.lora_a','.lora_b'))), 'ZERO_FROZEN_PARAMETER_HAS_GRADIENT'
        return float(loss.detach().item())
    raise RuntimeError('ZERO_SFT_GRADIENT_RETRY_EXHAUSTED')


def atomic_json(path: Path, payload):
    partial = path.with_name(path.name + '.partial')
    partial.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n', encoding='utf8')
    os.replace(partial, path)


def atomic_torch(path: Path, payload):
    partial = path.with_name(path.name + '.partial')
    torch.save(payload, partial)
    os.replace(partial, path)


def strict_check_input_paths(output: Path, baseline: Path):
    if baseline.resolve() == output.resolve() or output.resolve() in baseline.resolve().parents \
       or baseline.resolve() in output.resolve().parents:
        raise ValueError('ZERO_OUTPUT_COLLIDES_WITH_BASELINE')
    for p in (baseline/'adapter.pt', baseline/'adapter_config.json'):
        assert p.is_file(), 'ZERO_BASELINE_ARTIFACT_MISSING:' + str(p)


def run(*, output=DEFAULT_OUTPUT, baseline=BASELINE, device='cuda', steps=40,
        train_cases=96, dev_cases=18, test_cases=18, max_seq=192,
        lr=0.0002, rank=8, alpha=16., seed=71, eval_every=10,
        resume=False, stop_after=None, allow_download=False, model_loader=None, blind_cases=6,
        amp_dtype='auto'):
    output, baseline = Path(output), Path(baseline)
    strict_check_input_paths(output, baseline)
    assert steps >= 1 and eval_every >= 1 and max_seq >= 40 and rank >= 1 and lr > 0 and blind_cases >= 0, (
        'ZERO_INVALID_TRAINING_CONFIG')
    split = make_cases(seed, train_cases, dev_cases, test_cases)
    data_hash = fingerprint(split)
    base_hashes = {n: activation.file_hash(baseline/n) for n in ('adapter.pt','adapter_config.json')}
    loader = model_loader or hybrid.load_qwen
    model, tokenizer, details = loader(device=device, allow_download=allow_download)
    hybrid.load_adapter(baseline, model, details)
    assert details['base_model'] == hybrid.MODEL_ID, 'ZERO_REQUIRES_QWEN3_06B_BASE'
    model.backbone.requires_grad_(False)
    model.head.requires_grad_(False)
    torch.manual_seed(seed)
    if str(device).startswith('cuda'): torch.cuda.manual_seed_all(seed)
    names = install_lora(model.backbone, rank, alpha, required_layers=(int(model.backbone.config.num_hidden_layers)
                        if hasattr(model.backbone, 'config') else None))
    optimizer = torch.optim.AdamW([p for _,p in lora_named_parameters(model.backbone)], lr=lr, weight_decay=0.)
    effective_amp_dtype = resolve_amp_dtype(device, amp_dtype)
    scaler = torch.amp.GradScaler('cuda', enabled=(effective_amp_dtype == 'float16'),
                                  init_scale=256., growth_interval=2000)
    config = {'schema':SCHEMA, 'data_hash':data_hash,'baseline':str(baseline.resolve()),
              'base_hashes':base_hashes,'base_model':details['base_model'],'revision':details['revision'],
              'steps':steps,'max_seq':max_seq,'lr':lr,'rank':rank,'alpha':alpha,'seed':seed,
              'eval_every':eval_every,'lora_modules':list(names),
              'phase':'backbone_numerical_thought_sft_not_hybridlink_alignment',
              'train_cases':train_cases,'dev_cases':dev_cases,'test_cases':test_cases,'blind_cases':blind_cases,
              'amp_dtype':effective_amp_dtype}
    if resume:
        assert (output/'run_config.json').is_file() and (output/'last.pt').is_file(), 'ZERO_RESUME_FILES_MISSING'
        assert json.loads((output/'run_config.json').read_text()) == config, 'ZERO_RESUME_CONFIG_OR_BASELINE_DRIFT'
        cp = torch.load(output/'last.pt', map_location='cpu', weights_only=True)
        assert cp['schema'] == SCHEMA, 'ZERO_RESUME_SCHEMA_DRIFT'
        restore_lora(model.backbone, cp['lora'])
        optimizer.load_state_dict(cp['optimizer'])
        scaler.load_state_dict(cp['scaler'])
        torch.set_rng_state(cp['torch_rng'])
        if str(device).startswith('cuda') and cp['cuda_rng'] is not None:
            torch.cuda.set_rng_state_all(cp['cuda_rng'])
        start = int(cp['step']); best = float(cp['best_dev_advantage'])
    else:
        if output.exists() and any(output.iterdir()):
            # A previous attempt may have failed before its first successful
            # optimizer step. Reclaim ONLY that exact inert run_config.json
            # with a matching baseline; never erase checkpoints or data.
            entries = {x.name for x in output.iterdir()}
            if entries != {'run_config.json'}:
                raise FileExistsError('ZERO_REFUSES_EXISTING_OUTPUT:' + str(output))
            prior = json.loads((output/'run_config.json').read_text(encoding='utf8'))
            # A v1 failed pre-step run had no amp_dtype key. It is safe to
            # upgrade that sole inert metadata file only if EVERY original
            # config field matches. Any checkpoint is still a hard refusal.
            old_format = 'amp_dtype' not in prior
            expected = {k:v for k,v in config.items() if k != 'amp_dtype'} if old_format else config
            if prior != expected:
                raise FileExistsError('ZERO_REFUSES_PRESTEP_CONFIG_DRIFT:' + str(output))
            if old_format:
                atomic_json(output/'run_config.json',config)
            print('ZERO_BACKBONE_RECOVERED_PRESTEP=' + str(output), flush=True)
        else:
            output.mkdir(parents=True,exist_ok=True)
            atomic_json(output/'run_config.json',config)
        start, best = 0, -float('inf')
    assert 0 <= start < steps, 'ZERO_RESUME_STEP_OUT_OF_RANGE_OR_ALREADY_COMPLETE'
    if str(device).startswith('cuda'):torch.cuda.reset_peak_memory_stats()
    initial = thought_utility(model.backbone, tokenizer, split['dev'][:6],device)
    if start and 'initial_utility' in cp: initial = cp['initial_utility']
    # Tensor hash includes LoRA matrices; after training only frozen tensors can be checked.
    frozen_sha256_before = frozen_backbone_sha256(model.backbone)
    lora_before = lora_state(model.backbone)
    emitted = []
    for step in range(start+1,steps+1):
        row = split['train'][(step-1) % len(split['train'])]
        loss = train_one(model.backbone,tokenizer,row,optimizer,device,max_seq=max_seq,scaler=scaler,
                         amp_dtype=effective_amp_dtype)
        if step == 1 or step % eval_every == 0 or step == steps or (stop_after is not None and step >= stop_after):
            metrics = thought_utility(model.backbone,tokenizer,split['dev'][:6],device)
            record = {'step':step,'train_loss':loss,'dev_thought_advantage':metrics['mean_advantage'],
                      'best_dev_thought_advantage':max(best,metrics['mean_advantage'])}
            print('ZERO_BACKBONE_STEP='+json.dumps(record,sort_keys=True),flush=True)
            emitted.append(record)
            if metrics['mean_advantage'] > best:
                best = metrics['mean_advantage']
                atomic_torch(output/'best_lora.pt',{'schema':SCHEMA,'step':step,'lora':lora_state(model.backbone),
                                                   'dev_advantage':best})
            cp = {'schema':SCHEMA,'step':step,'lora':lora_state(model.backbone),
                  'optimizer':optimizer.state_dict(),'scaler':scaler.state_dict(),
                  'torch_rng':torch.get_rng_state(),
                  'cuda_rng':torch.cuda.get_rng_state_all() if str(device).startswith('cuda') else None,
                  'best_dev_advantage':best,'initial_utility':initial}
            atomic_torch(output/'last.pt',cp)
        if stop_after is not None and step >= stop_after:break
    finished = step == steps if steps > start else True
    frozen_sha256_after = frozen_backbone_sha256(model.backbone)
    assert frozen_sha256_after == frozen_sha256_before, 'ZERO_BACKBONE_FROZEN_WEIGHT_CHANGED'
    lora_after = lora_state(model.backbone)
    assert any(not torch.equal(t, lora_after[name]) for name,t in lora_before.items()), (
        'ZERO_OPTIMIZER_DID_NOT_UPDATE_LORA')
    assert {n:activation.file_hash(baseline/n) for n in base_hashes} == base_hashes, (
        'ZERO_BASELINE_ARTIFACT_MUTATION')
    final = thought_utility(model.backbone,tokenizer,split['dev'][:6],device)
    blind = (blind_probe(model.backbone, tokenizer, split['test'][:blind_cases],device)
             if finished and blind_cases else None)
    report = {'schema':SCHEMA, 'status':'TRAINING_SMOKE_COMPLETED_UNCERTIFIED' if finished else 'PAUSED_RESUMABLE',
              'step':step,'steps':steps, 'trainable_projection_count':len(names),
              'start_step':start,'optimizer_steps_this_run':max(0,step-start),
              'initial_dev_thought_utility':initial['mean_advantage'],
              'final_dev_thought_utility':final['mean_advantage'],
              'dev_utility_positive_cases':final['positive_advantage_cases'],
              'dev_utility_case_count':final['cases'],
              'teacher_utility_gate': bool(final['mean_advantage'] > 0 and
                                           final['positive_advantage_cases'] >= 4),
              'baseline_mutations':0,'frozen_backbone_sha256':frozen_sha256_after,
              'head_trainable_parameters':sum(p.numel() for p in model.head.parameters() if p.requires_grad),
              'amp_dtype':effective_amp_dtype,
              'cuda_peak_allocated_mb':round(torch.cuda.max_memory_allocated()/1048576,2) if str(device).startswith('cuda') else None,
              'blind_test':blind,
              'blind_accuracy':blind['accuracy'] if blind is not None else 'NOT_TESTED',
              'note':'Thought utility is log-probability discrimination, not blind numerical computation.'}
    atomic_json(output/'result.json',report)
    print('ZERO_BACKBONE_RESULT='+json.dumps(report,sort_keys=True),flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda')
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--baseline',type=Path,default=BASELINE)
    p.add_argument('--steps',type=int,default=40)
    p.add_argument('--max-seq',type=int,default=192)
    p.add_argument('--lr',type=float,default=0.0002)
    p.add_argument('--amp-dtype', choices=('auto','bfloat16','float16'), default='auto',
                   help='Auto uses BF16 if supported; otherwise dynamically scaled FP16')
    p.add_argument('--rank',type=int,default=8)
    p.add_argument('--seed',type=int,default=71)
    p.add_argument('--eval-every',type=int,default=10)
    p.add_argument('--stop-after',type=int,default=None)
    p.add_argument('--blind-cases',type=int,default=6)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--allow-model-download',action='store_true')
    args=p.parse_args(argv)
    try:
        run(output=args.output,baseline=args.baseline,device=args.device,steps=args.steps,
            max_seq=args.max_seq,lr=args.lr,rank=args.rank,seed=args.seed,
            eval_every=args.eval_every,stop_after=args.stop_after,resume=args.resume,
            allow_download=args.allow_model_download,blind_cases=args.blind_cases,
            amp_dtype=args.amp_dtype)
        return 0
    except (AssertionError,RuntimeError,OSError,ValueError,FileExistsError) as exc:
        print('ZERO_BACKBONE_REFUSED='+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
