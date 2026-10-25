#!/usr/bin/env python3
"""Qwen3-0.6B-Base + released-architecture HybridLink, real embedding feedback.

A locally initialised, *untrained* adapter is not an agentic checkpoint.  This
module deliberately does not reuse the numerical Gaussian/HybridLink sidecars.
The Qwen transformer consumes each generated latent vector as its NEXT input
embedding, through the SAME pretrained transformer and attention KV cache.
No arithmetic executor, answer router or teacher oracle participates in this
path.  HF transformers is loaded lazily; --inspect works offline without it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import torch
from torch import nn
from torch.nn import functional as F

MODEL_ID = 'Qwen/Qwen3-0.6B-Base'
# Pinned Base checkpoint; retained instruct pin is available by explicit selection.
MODEL_REVISION = '311c62e88814bff7206909ccd330bab0a784743b'
INSTRUCT_ID = 'Qwen/Qwen3-0.6B'
INSTRUCT_REVISION = 'c1899de289a04d12100db370d81485cdf75e47ca'
SCHEMA = 'nanojev.qwen3-0.6b.hybridagent-ln-res-link.v2'
START = '<latent>'
END = '</latent>'
DEFAULT_OUTPUT = Path.home() / 'NanoJev' / 'runs' / 'qwen3_06b_base_hybridagent_ln_v2'


class HybridLink(nn.Module):
    """Exact named module graph of togethercomputer/HybridAgent hybrid_agent/hybridlink.py.

    Official release: post_ln(h + proj2(GELU(proj1(pre_ln(h))))).
    Unlike our legacy v1 head, this has TWO real executed LayerNorms and
    upstream parameter names. A zero-initialized output projection is NOT used:
    PyTorch's default Linear initialization is part of the fresh baseline.
    """
    def __init__(self, hidden_size: int):
        super().__init__()
        if hidden_size <= 0:
            raise ValueError('HYBRID_BAD_HIDDEN_SIZE')
        self.proj1 = nn.Linear(hidden_size, hidden_size)
        self.act = nn.GELU()
        self.proj2 = nn.Linear(hidden_size, hidden_size)
        self.pre_ln = nn.LayerNorm(hidden_size)
        self.post_ln = nn.LayerNorm(hidden_size)

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        out = self.proj2(self.act(self.proj1(self.pre_ln(hidden))))
        return self.post_ln(hidden + out)


class HybridQwen(nn.Module):
    """Real pretrained causal backbone, one residual link, shared KV history."""
    def __init__(self, backbone: nn.Module, head: HybridLink, start_id: int, end_id: int):
        super().__init__()
        self.backbone = backbone
        self.head = head
        self.start_id = int(start_id)
        self.end_id = int(end_id)
        self.last_input_embeddings = None  # diagnostic ONLY, never persisted

    def _step(self, *, input_ids=None, inputs_embeds=None, cache=None, mask=None):
        if (input_ids is None) == (inputs_embeds is None):
            raise ValueError('HYBRID_EXPECTS_ONE_INPUT_KIND')
        out = self.backbone(input_ids=input_ids, inputs_embeds=inputs_embeds,
                            attention_mask=mask, past_key_values=cache,
                            output_hidden_states=True, use_cache=True,
                            return_dict=True)
        if not out.hidden_states or out.past_key_values is None:
            raise RuntimeError('HYBRID_BACKBONE_MISSING_STATES_OR_CACHE')
        return out.hidden_states[-1][:, -1, :], out.logits[:, -1, :], out.past_key_values

    def rollout(self, prompt_ids: torch.Tensor, latent_steps: int, *, enter_latent=True,
                stop_on_end=False, return_trace=False):
        """Append real latent vectors, with no detached/cached proxy model.

        The transformer initially reads text, then the enter marker (if used).
        Each latent state is mapped by the link and fed via inputs_embeds in a
        fresh forward call using the previous attention KV cache.  Autograd can
        flow through all latent passes when the caller enables gradients.
        """
        if latent_steps < 0 or latent_steps > 256:
            raise ValueError('HYBRID_LATENT_STEPS_OUT_OF_RANGE')
        if prompt_ids.ndim != 2 or prompt_ids.size(0) != 1 or prompt_ids.size(1) < 1:
            raise ValueError('HYBRID_REQUIRES_ONE_NONEMPTY_PROMPT')
        if enter_latent and latent_steps:
            marker = torch.full((1, 1), self.start_id, device=prompt_ids.device,
                                dtype=prompt_ids.dtype)
            prompt_ids = torch.cat((prompt_ids, marker), dim=1)
        mask = torch.ones_like(prompt_ids)
        hidden, logits, cache = self._step(input_ids=prompt_ids, mask=mask)
        trace = []
        for _ in range(latent_steps):
            next_embedding = self.head(hidden)
            self.last_input_embeddings = next_embedding
            if not torch.isfinite(next_embedding).all():
                raise RuntimeError('HYBRID_NONFINITE_LATENT')
            # Decision reads the *generated embedding*, not answer logits.
            if stop_on_end and self.nearest_embedding_is_end(next_embedding):
                break
            mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
            hidden, logits, cache = self._step(inputs_embeds=next_embedding[:, None, :],
                                               cache=cache, mask=mask)
            if return_trace:
                trace.append({'latent_step': len(trace) + 1,
                              'embedding_norm': float(next_embedding.detach().float().norm()),
                              'hidden_norm': float(hidden.detach().float().norm())})
        return hidden, logits, cache, mask, trace

    def nearest_embedding_is_end(self, latent_embedding: torch.Tensor) -> bool:
        """Paper's exit test: nearest INPUT-embedding token, by cosine similarity.

        Chunk the comparison to limit temporary GPU allocation. Only enabled
        explicitly after actual HybridSFT/alignment; do not trust untrained
        randomly initialized end-token embeddings for automatic stopping.
        """
        if latent_embedding.shape[0] != 1:
            raise ValueError('HYBRID_EXIT_BATCH_NOT_ONE')
        table = self.backbone.get_input_embeddings().weight.detach()
        v = F.normalize(latent_embedding.detach().float(), dim=-1)
        best_sim = -float('inf')
        best_id = -1
        for i in range(0, table.shape[0], 4096):
            block = F.normalize(table[i:i + 4096].float(), dim=-1)
            scores = F.linear(v, block)[0]
            score, idx = scores.max(dim=0)
            if float(score) > best_sim:
                best_sim, best_id = float(score), i + int(idx)
        return best_id == self.end_id

    def text_after_latents(self, cache, mask, *, max_tokens: int, eos_token_id: int | None):
        """Explicitly return to ordinary text, preserving hybrid KV context."""
        marker = torch.tensor([[self.end_id]], device=mask.device, dtype=torch.long)
        mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
        _, logits, cache = self._step(input_ids=marker, cache=cache, mask=mask)
        produced = []
        for _ in range(max_tokens):
            tok = logits.argmax(dim=-1, keepdim=True)
            tid = int(tok.item())
            if eos_token_id is not None and tid == eos_token_id:
                break
            produced.append(tid)
            mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
            _, logits, cache = self._step(input_ids=tok, cache=cache, mask=mask)
        return produced


def _dtype_and_device(device: str, precision: str):
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('HYBRID_CUDA_REQUESTED_BUT_UNAVAILABLE')
    if precision == 'auto':
        precision = 'float16' if device == 'cuda' else 'float32'
    if precision == 'float16' and device == 'cpu':
        raise ValueError('HYBRID_FP16_CPU_UNSUPPORTED')
    if precision == 'bfloat16' and device == 'cuda' and not torch.cuda.is_bf16_supported():
        raise ValueError('HYBRID_BF16_CUDA_UNSUPPORTED')
    return device, getattr(torch, precision)


def _register_latent_markers(tokenizer, added_token_class):
    """Register latent control tokens on both Transformers 4 and 5 tokenizers.

    Transformers 5 replaced ``additional_special_tokens`` with
    ``extra_special_tokens``.  Preserve the tokenizer's existing extra tokens
    and their AddedToken metadata instead of replacing or de-specializing them.
    """
    key = ('extra_special_tokens' if hasattr(tokenizer, 'extra_special_tokens')
           else 'additional_special_tokens')
    existing = list(getattr(tokenizer, key, ()) or ())
    names = {str(token) for token in existing}
    additions = [added_token_class(token, normalized=False, special=True)
                 for token in (START, END) if token not in names]
    tokenizer.add_special_tokens({key: [*existing, *additions]})
    return (tokenizer.convert_tokens_to_ids(START),
            tokenizer.convert_tokens_to_ids(END))


def load_qwen(model_id=MODEL_ID, revision=None, *, device='auto',
              precision='auto', allow_download=False):
    """Load actual Qwen3-0.6B: no silent model substitution or remote code."""
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer, AddedToken
    except ImportError as exc:
        raise RuntimeError('HYBRID_REQUIRES_TRANSFORMERS: pip install "transformers>=4.56,<6" safetensors accelerate') from exc
    device, dtype = _dtype_and_device(device, precision)
    path = Path(model_id).expanduser()
    local = path.is_dir()
    source = str(path.resolve()) if local else str(model_id)
    if not local and source not in (MODEL_ID, INSTRUCT_ID):
        raise ValueError('HYBRID_ONLY_QWEN3_06B_BASE_INSTRUCT_OR_LOCAL_PATH')
    options = {'local_files_only': not allow_download, 'trust_remote_code': False}
    if not local:
        if revision is None:
            revision = MODEL_REVISION if source == MODEL_ID else INSTRUCT_REVISION
        options['revision'] = revision
    try:
        tokenizer = AutoTokenizer.from_pretrained(source, **options)
        original_len = len(tokenizer)
        if tokenizer.pad_token_id is None and tokenizer.eos_token_id is not None:
            tokenizer.pad_token = tokenizer.eos_token
        # Special tokens must each occupy exactly one embedding position.
        start_id, end_id = _register_latent_markers(tokenizer, AddedToken)
        if start_id == end_id or min(start_id, end_id) < 0:
            raise RuntimeError('HYBRID_TOKENIZER_MARKER_COLLISION')
        backbone = AutoModelForCausalLM.from_pretrained(
            source, dtype=dtype, attn_implementation='sdpa', **options)
    except (OSError, ValueError) as exc:
        raise RuntimeError('HYBRID_QWEN_NOT_CACHED: use --allow-model-download once, or --model LOCAL_QWEN_DIR; '
                           'original error: ' + str(exc)) from exc
    if getattr(backbone.config, 'model_type', '') != 'qwen3':
        raise RuntimeError('HYBRID_NOT_QWEN3_BACKBONE')
    hidden = int(backbone.config.hidden_size)
    if hidden != 1024 or int(backbone.config.num_hidden_layers) != 28:
        raise RuntimeError('HYBRID_NOT_QWEN3_06B_ARCHITECTURE')
    if backbone.get_input_embeddings().embedding_dim != hidden:
        raise RuntimeError('HYBRID_INPUT_HIDDEN_DIM_MISMATCH')
    # Existing embeddings are unchanged. Added rows are persisted separately
    # so a new invocation reconstructs the identical token interface.
    backbone.resize_token_embeddings(len(tokenizer), mean_resizing=False)
    if (tokenizer(START, add_special_tokens=False)['input_ids'] != [start_id]
            or tokenizer(END, add_special_tokens=False)['input_ids'] != [end_id]):
        raise RuntimeError('HYBRID_MARKERS_NOT_ATOMIC_TOKENS')
    backbone.to(device)
    backbone.eval()
    backbone.requires_grad_(False)
    head = HybridLink(hidden).to(device=device, dtype=dtype)
    model = HybridQwen(backbone, head, start_id, end_id).to(device)
    details = {'schema': SCHEMA, 'base_model': source, 'revision': revision if not local else None,
               'model_type': backbone.config.model_type, 'hidden_size': hidden,
               'original_vocab_size': original_len, 'tokenizer_size': len(tokenizer),
               'start_id': int(start_id), 'end_id': int(end_id),
               'device': device, 'dtype': str(dtype).replace('torch.', ''),
               'architecture': 'HybridLink: post_ln(h + proj2(GELU(proj1(pre_ln(h))))); same Qwen backbone receives link outputs as inputs_embeds',
               'adapter_type': 'ln_res_adapter',
               'frozen_backbone': True, 'training_status': 'untrained'}
    return model, tokenizer, details


def _atomic_json(path, payload):
    tmp = path.with_name(path.name + '.partial')
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _atomic_torch(path, payload):
    tmp = path.with_name(path.name + '.partial')
    torch.save(payload, tmp)
    os.replace(tmp, path)


def _token_rows(model: HybridQwen):
    ids = [model.start_id, model.end_id]
    emb = model.backbone.get_input_embeddings().weight.detach()
    rows = {'input_embeddings': emb[ids].cpu().clone()}
    output = model.backbone.get_output_embeddings()
    if output is not None and output.weight.data_ptr() != emb.data_ptr():
        rows['output_embeddings'] = output.weight.detach()[ids].cpu().clone()
    return rows


def save_adapter(output: Path, model: HybridQwen, details: dict):
    """Only small adapter/token rows; original Qwen weights remain untouched."""
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('HYBRID_REFUSES_EXISTING_OUTPUT:' + str(output))
    output.mkdir(parents=True, exist_ok=True)
    payload = {'schema': SCHEMA, 'head': {k: v.detach().cpu() for k, v in model.head.state_dict().items()},
               'tokens': _token_rows(model)}
    _atomic_torch(output / 'adapter.pt', payload)
    _atomic_json(output / 'adapter_config.json', details)
    return output / 'adapter.pt'


def read_config(output: Path):
    cfg = json.loads((output / 'adapter_config.json').read_text(encoding='utf-8'))
    if cfg.get('schema') != SCHEMA:
        raise RuntimeError('HYBRID_ADAPTER_SCHEMA_MISMATCH: legacy v1 and normalized v2 are NOT interchangeable; '
                           'prepare a separate v2 output and leave the original checkpoint untouched')
    return cfg


def load_adapter(output: Path, model: HybridQwen, details: dict):
    cfg = read_config(output)
    keys = ('base_model', 'revision', 'model_type', 'hidden_size', 'original_vocab_size',
            'tokenizer_size', 'start_id', 'end_id')
    drift = [k for k in keys if cfg.get(k) != details.get(k)]
    if drift:
        raise RuntimeError('HYBRID_ADAPTER_BACKBONE_OR_TOKENIZER_DRIFT:' + ','.join(drift))
    obj = torch.load(output / 'adapter.pt', map_location='cpu', weights_only=True)
    if obj.get('schema') != SCHEMA:
        raise RuntimeError('HYBRID_ADAPTER_WEIGHTS_SCHEMA_MISMATCH')
    if cfg.get('adapter_type') != 'ln_res_adapter':
        raise RuntimeError('HYBRID_ADAPTER_TYPE_MISMATCH')
    model.head.load_state_dict(obj['head'], strict=True)
    ids = [model.start_id, model.end_id]
    tokens = obj['tokens']
    with torch.no_grad():
        dest = model.backbone.get_input_embeddings().weight
        if tokens['input_embeddings'].shape != dest[ids].shape:
            raise RuntimeError('HYBRID_INPUT_TOKEN_SHAPE_DRIFT')
        dest[ids] = tokens['input_embeddings'].to(dest)
        output_emb = model.backbone.get_output_embeddings()
        if output_emb is not None and output_emb.weight.data_ptr() != dest.data_ptr():
            if 'output_embeddings' not in tokens:
                raise RuntimeError('HYBRID_OUTPUT_TOKEN_ROWS_MISSING')
            output_emb.weight[ids] = tokens['output_embeddings'].to(output_emb.weight)
    return cfg


def preflight(model: HybridQwen, tokenizer, *, check_gradient=True):
    """Real Qwen forward, zero-step text parity, actual latent feedback, gradients."""
    device = next(model.backbone.parameters()).device
    ids = tokenizer('A + (-A + delta) equals delta. A = 1000000; delta = -1.\n',
                    return_tensors='pt')['input_ids'].to(device)
    with torch.no_grad():
        baseline = model.backbone(input_ids=ids, attention_mask=torch.ones_like(ids),
                                  use_cache=True, output_hidden_states=True,
                                  return_dict=True).logits[:, -1, :]
        _, logits0, _, _, _ = model.rollout(ids, 0)
        parity = torch.allclose(baseline, logits0, rtol=0, atol=0)
        h2, logits2, _, _, trace = model.rollout(ids, 2, return_trace=True)
        valid = bool(torch.isfinite(h2).all() and torch.isfinite(logits2).all())
    if not parity or not valid:
        raise RuntimeError('HYBRID_REAL_FORWARD_PREFLIGHT_FAILED')
    gradient = 'not_checked'
    if check_gradient:
        model.head.zero_grad(set_to_none=True)
        # Backbone weights frozen; the same backbone must nevertheless carry
        # gradients to the adapter from later attention / hidden computation.
        with torch.enable_grad():
            h, _, _, _, _ = model.rollout(ids, 2)
            loss = h.float().square().mean()
            loss.backward()
        grad = model.head.proj2.weight.grad
        if grad is None or not bool(torch.isfinite(grad).all()) or not bool(grad.abs().sum() > 0):
            raise RuntimeError('HYBRID_FEEDBACK_GRADIENT_DISCONNECTED')
        if any(p.grad is not None for p in model.backbone.parameters()):
            raise RuntimeError('HYBRID_BACKBONE_UNEXPECTED_GRADIENT')
        gradient = 'PASS'
        model.head.zero_grad(set_to_none=True)
    return {'text_zero_step_parity': 'PASS', 'cached_embedding_feedback': 'PASS',
            'latent_steps': len(trace), 'latent_trace': trace,
            'link_backward': gradient, 'arithmetic_accuracy': 'NOT_TESTED',
            'adaptive_mode_switching': 'NOT_TRAINED'}


def run(args):
    if args.command == 'inspect':
        cfg = read_config(args.output)
        print(json.dumps(cfg, sort_keys=True, indent=2))
        return
    if args.command == 'prepare' and args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError('HYBRID_REFUSES_EXISTING_OUTPUT:' + str(args.output))
    model, tokenizer, details = load_qwen(args.model, args.revision, device=args.device,
                                         precision=args.precision,
                                         allow_download=args.allow_model_download)
    if args.command == 'prepare':
        # Fail before publishing any checkpoint if the head is disconnected.
        report = preflight(model, tokenizer, check_gradient=not args.skip_gradient_check)
        save_adapter(args.output, model, details)
        _atomic_json(args.output / 'preflight.json', report)
        print('HYBRID_QWEN_PREPARED=' + json.dumps({'status': 'READY_UNTRAINED',
              'output': str(args.output), 'backbone': details['base_model'],
              'hidden_size': details['hidden_size'], 'report': report}, sort_keys=True))
        return
    cfg = load_adapter(args.output, model, details)
    if args.command == 'smoke':
        report = preflight(model, tokenizer, check_gradient=not args.skip_gradient_check)
        print('HYBRID_QWEN_SMOKE=' + json.dumps(report, sort_keys=True))
        return
    if args.command == 'generate':
        if args.latent_steps < 0 or args.latent_steps > 256 or args.max_new_tokens < 0 or args.max_new_tokens > 2048:
            raise ValueError('HYBRID_BAD_GENERATION_LENGTH')
        if args.adaptive_exit and cfg['training_status'] == 'untrained':
            raise RuntimeError('HYBRID_ADAPTIVE_EXIT_REQUIRES_TRAINED_CHECKPOINT')
        device = next(model.backbone.parameters()).device
        encoded = tokenizer(args.prompt, return_tensors='pt', add_special_tokens=False)
        ids = encoded['input_ids'].to(device)
        with torch.inference_mode():
            hidden, logits, cache, mask, trace = model.rollout(
                ids, args.latent_steps, enter_latent=args.latent_steps > 0,
                stop_on_end=args.adaptive_exit, return_trace=True)
            if args.latent_steps:
                result_ids = model.text_after_latents(cache, mask,
                       max_tokens=args.max_new_tokens, eos_token_id=tokenizer.eos_token_id)
            else:
                result_ids = []
                for _ in range(args.max_new_tokens):
                    tok = logits.argmax(dim=-1, keepdim=True)
                    tid = int(tok.item())
                    if tokenizer.eos_token_id == tid:
                        break
                    result_ids.append(tid)
                    mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
                    _, logits, cache = model._step(input_ids=tok, cache=cache, mask=mask)
        print('HYBRID_QWEN_GENERATION=' + json.dumps({
            'status': cfg['training_status'], 'prompt': args.prompt,
            'latent_steps': len(trace), 'trace': trace,
            'continuation': tokenizer.decode(result_ids, skip_special_tokens=True),
            'warning': 'UNTRAINED: generation is an architecture smoke, NOT a reasoning result'
            if cfg['training_status'] == 'untrained' else None}, ensure_ascii=False))
        return
    raise ValueError('HYBRID_UNKNOWN_COMMAND')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('prepare', 'inspect', 'smoke', 'generate'))
    p.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    p.add_argument('--model', default=MODEL_ID, help='Pinned Qwen3-0.6B-Base (default), opt-in instruct, or existing local Qwen3 directory')
    p.add_argument('--revision', default=None,
                   help='Optional revision override; otherwise each Qwen variant uses its own pinned revision')
    p.add_argument('--allow-model-download', action='store_true', help='Opt in to a one-time Hugging Face download')
    p.add_argument('--device', choices=('auto', 'cuda', 'cpu'), default='auto')
    p.add_argument('--precision', choices=('auto', 'float16', 'float32', 'bfloat16'), default='auto')
    p.add_argument('--skip-gradient-check', action='store_true', help='Disable costly gradient preflight (no training)')
    p.add_argument('--prompt', default='A + (-A + 1) = ', help='Text prompt for generation only')
    p.add_argument('--latent-steps', type=int, default=4)
    p.add_argument('--max-new-tokens', type=int, default=24)
    p.add_argument('--adaptive-exit', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        run(args)
        return 0
    except (RuntimeError, OSError, FileExistsError, ValueError) as exc:
        print('HYBRID_QWEN_REFUSED:' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
