#!/usr/bin/env python3
"""QW5 phase 1: five independent EMP backbones + Qwen-conditioned readouts.

Four ordinary checkpointed models and ONE already trained Hard EMP specialist.
Neither Qwen nor the source EMP weights are modified.  QW5 receives literal
operands AND selected intermediate/final Qwen hidden states.  Its own learned
corrections and five-way fusion emit the numerical answer, not the LM head.

The same QW5 state is exposed for a future HybridLink augmenting adapter.
Stage-2 feedback and stage-3 runtime replacement are explicitly fail-closed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import torch
from torch import nn
from torch.nn import functional as F

from tools.nanojev_addition_curriculum import target_digits
from tools.nanojev_zero_line_zero_only import input_tensor
from tools.nanojev_zero_line_ladder import prediction_values
from tools import nanojev_emp_dual_bank as dual
from tools import nanojev_emp_dual_bank_ladder as ladder
from tools import nanojev_emp_hard_join as hard
from tools.nanojev_zero_line_radius255_probe import load_certified_model

SCHEMA = 'nanojev.qw5.conditioned-bank.v1'
HEAD_NAMES = (*dual.BANKS, 'hard.left')
QWEN_TAPS = (7, 14, 21, 28)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def source_paths(campaign: Path, stage=None, *, hard_state_dir='hard_emp', allow_missing=False):
    """Find committed sources without modifying the live EMP campaign.

    Match the Hard EMP sidecar directory selected by nanojev_emp_hard_join.
    """
    state_dir = Path(hard_state_dir)
    if (state_dir.is_absolute() or len(state_dir.parts) != 1
            or state_dir.name in ('', '.', '..') or state_dir.name.startswith('.')):
        raise ValueError('QW5_INVALID_HARD_STATE_DIR')
    campaign = Path(campaign).expanduser().resolve()
    if stage is None:
        stages = [(int(p.parent.name.removeprefix('outward_')), p)
                  for p in (campaign / 'stages').glob('outward_*/latest.pt')
                  if p.parent.name.removeprefix('outward_').isdigit()]
        if not stages:
            raise FileNotFoundError('QW5_NO_COMMITTED_EMP_STAGE:' + str(campaign / 'stages'))
        stage_path = max(stages)[1]
    else:
        stage_path = campaign / 'stages' / f'outward_{int(stage)}' / 'latest.pt'
    hard_path = campaign / state_dir / 'latest.pt'
    files = [stage_path, hard_path, campaign / 'source_left.pt', campaign / 'source_right.pt']
    if not allow_missing and any(not p.is_file() for p in files):
        raise FileNotFoundError('QW5_MISSING_EMP_SOURCE:' + ','.join(str(p) for p in files if not p.is_file()))
    return {'stage': stage_path, 'hard': hard_path, 'left': files[2], 'right': files[3]}


def load_five(campaign, ladder_output, *, stage=None, device='cpu', hard_state_dir='hard_emp', source_files=None):
    """Recover exactly 4 committed bank states and the independently trained specialist.

    Hard EMP's own qualification/clone-count rules are not changed. When its
    checkpoint contains two specialists we refuse, rather than silently pick
    an oracle-selected fifth head or alter the live specialist population.
    """
    paths = (source_paths(campaign, stage, hard_state_dir=hard_state_dir)
             if source_files is None else {k: Path(v) for k, v in source_files.items()})
    if set(paths) != {'stage', 'hard', 'left', 'right'} or not all(p.is_file() for p in paths.values()):
        raise RuntimeError('QW5_SOURCE_FILES_INCOMPLETE')
    saved = torch.load(paths['stage'], map_location='cpu', weights_only=True)
    if saved.get('schema') != ladder.SCHEMA + '.checkpoint':
        raise RuntimeError('QW5_ORDINARY_STAGE_SCHEMA')
    hcp = torch.load(paths['hard'], map_location='cpu', weights_only=True)
    if hcp.get('schema') != hard.SCHEMA + '.checkpoint' or hcp.get('specialists') is None:
        raise RuntimeError('QW5_HARD_SPECIALIST_NOT_TRAINED')
    selected = hcp.get('selected')
    if not isinstance(selected, list) or len(selected) != 1 or selected[0] not in dual.BANKS:
        raise RuntimeError('QW5_REQUIRES_EXACTLY_ONE_HARD_SPECIALIST')
    # The normal sidecar is only compatible with the SAME currently committed
    # source probe; guard against combining time-inconsistent checkpoints.
    serial = f"{saved['radius']}-{saved['step']}"
    record = hcp.get('processed', {}).get(serial)
    if record is None or record.get('source_sha256') != sha(paths['stage']):
        raise RuntimeError('QW5_HARD_ORDINARY_STAGE_MISMATCH')
    parent, _ = load_certified_model(str(ladder_output), 'cpu')
    left = dual.checked_source(paths['left'])
    right = dual.checked_source(paths['right'])
    banks = ladder.from_source(parent, left, right, saved['banks'], 'cpu')
    source = selected[0]
    # Hard module state keys are named hard__left.*, not 'hard.left.*'.
    holder = nn.ModuleDict({'hard__left': copy.deepcopy(banks[source])})
    holder.load_state_dict(hcp['specialists'], strict=True)
    models = {name: banks[name] for name in dual.BANKS}
    models['hard.left'] = holder['hard__left']
    provenance = {'campaign': str(Path(campaign).resolve()),
                  'ladder_output': str(Path(ladder_output).expanduser().resolve()),
                  'hard_state_dir': str(hard_state_dir),
                  'stage_radius': saved['radius'], 'stage_step': saved['step'],
                  'specialist_source': source,
                  'sha256': {key: sha(path) for key, path in paths.items()}}
    return {key: m.to(device).eval().requires_grad_(False) for key, m in models.items()}, provenance


def captured_state(bank: nn.Module, x: torch.Tensor):
    """Capture the source model's own final norm, without reimplementing its encoding."""
    latents = []
    hook = bank.norm.register_forward_hook(lambda _m, _a, out: latents.append(out))
    try:
        with torch.no_grad():
            bank(x)
    finally:
        hook.remove()
    if not latents or latents[-1].ndim != 2 or latents[-1].shape[0] != x.shape[0]:
        raise RuntimeError('QW5_BANK_STATE_NOT_CAPTURED')
    return latents[-1].detach()


class QW5(nn.Module):
    """Five Qwen-conditioned readout specialists and a learned shared final readout.

    Source AdditionNets are frozen, but the five conditioning adapters and
    correction readouts train independently.  Their fused hidden states, with
    provenance retained by head position, are the future HybridLink signal.
    """
    def __init__(self, source_banks, qwen_width=1024, taps=QWEN_TAPS, bridge_width=1024):
        super().__init__()
        if tuple(source_banks) != HEAD_NAMES:
            raise ValueError('QW5_EXPECTS_FOUR_PLUS_ONE_IN_ORDER')
        self.banks = nn.ModuleDict({key.replace('.', '__'): m for key, m in source_banks.items()})
        self.names = HEAD_NAMES
        self.taps = tuple(taps)
        self.qwen_width = int(qwen_width)
        dims = [int(self.banks[key.replace('.', '__')].sign_head.in_features) for key in self.names]
        if len(set(dims)) != 1 or dims[0] < 8:
            raise RuntimeError('QW5_INCOMPATIBLE_EMP_HIDDEN_DIMENSIONS')
        self.width = dims[0]
        for model in self.banks.values():
            model.eval().requires_grad_(False)
        # Head-specific Qwen interfaces; zero state is the operand-only control.
        self.conditioners = nn.ModuleList([
            nn.Sequential(nn.LayerNorm(self.qwen_width * len(self.taps)),
                          nn.Linear(self.qwen_width * len(self.taps), self.width),
                          nn.GELU(), nn.Linear(self.width, self.width)) for _ in self.names])
        self.gains = nn.Parameter(torch.full((5,), 0.1))
        self.sign_corrections = nn.ModuleList([nn.Linear(self.width, 3) for _ in self.names])
        self.digit_corrections = nn.ModuleList([nn.Linear(self.width, 70) for _ in self.names])
        for module in (*self.sign_corrections, *self.digit_corrections):
            nn.init.zeros_(module.weight)
            nn.init.zeros_(module.bias)
        self.voter = nn.Sequential(nn.LayerNorm(self.width * 5),
                                   nn.Linear(self.width * 5, 64), nn.GELU(), nn.Linear(64, 5))
        self.sign_fusion = nn.Linear(self.width * 5, 3)
        self.digit_fusion = nn.Linear(self.width * 5, 70)
        nn.init.zeros_(self.sign_fusion.weight); nn.init.zeros_(self.sign_fusion.bias)
        nn.init.zeros_(self.digit_fusion.weight); nn.init.zeros_(self.digit_fusion.bias)
        self.feedback_projection = nn.Sequential(nn.LayerNorm(self.width * 5),
                                                 nn.Linear(self.width * 5, bridge_width))

    def train(self, mode=True):
        super().train(mode)
        for bank in self.banks.values():
            bank.eval()
        return self

    def forward(self, operands, qwen_states):
        if operands.ndim != 2 or operands.shape[-1] != 2:
            raise ValueError('QW5_REQUIRES_TWO_NUMERICAL_OPERANDS')
        required = (operands.shape[0], len(self.taps), self.qwen_width)
        if tuple(qwen_states.shape) != required:
            raise ValueError(f'QW5_QWEN_HIDDEN_SHAPE:{tuple(qwen_states.shape)}!={required}')
        if not bool(torch.isfinite(qwen_states).all()):
            raise RuntimeError('QW5_NONFINITE_QWEN_STATES')
        v = qwen_states.float().reshape(len(operands), -1)
        combined = []; candidates = []
        for i, key in enumerate(self.names):
            bank = self.banks[key.replace('.', '__')]
            base = captured_state(bank, operands)
            delta = self.conditioners[i](v)
            hidden = base.float() + self.gains[i] * delta
            sign = bank.sign_head(hidden.to(bank.sign_head.weight.dtype)).float()
            digits = bank.digit_head(hidden.to(bank.digit_head.weight.dtype)).float().reshape(-1, 7, 10)
            sign = sign + self.sign_corrections[i](hidden)
            digits = digits + self.digit_corrections[i](hidden).reshape(-1, 7, 10)
            candidates.append({'sign': sign, 'digits': digits})
            combined.append(hidden)
        features = torch.cat(combined, dim=-1)
        weights = torch.softmax(self.voter(features), dim=-1)
        sign = torch.stack([p['sign'] for p in candidates], dim=1)
        digits = torch.stack([p['digits'] for p in candidates], dim=1)
        predicted = {'sign': (weights.unsqueeze(-1) * sign).sum(1) + self.sign_fusion(features),
                     'digits': (weights[:, :, None, None] * digits).sum(1)
                               + self.digit_fusion(features).reshape(-1, 7, 10)}
        return {'answer': predicted, 'head_predictions': candidates,
                'weights': weights, 'emp_states': torch.stack(combined, dim=1),
                'hybrid_features': self.feedback_projection(features)}


def qw5_loss(out, answers, aux_weight=0.15):
    """Supervision only from TRAIN labels; no answer-based selector at inference."""
    def ce(pred):
        return (F.cross_entropy(pred['sign'], answers.sign().long() + 1)
                + F.cross_entropy(pred['digits'].transpose(1, 2), target_digits(answers)))
    return ce(out['answer']) + aux_weight * torch.stack([ce(v) for v in out['head_predictions']]).mean()


def result_stats(qw5, operands, hidden, answers):
    qw5.eval()
    with torch.no_grad():
        intact = qw5(operands, hidden)
        no_qwen = qw5(operands, torch.zeros_like(hidden))
        wrong = qw5(operands, hidden.roll(1, 0)) if len(hidden) > 1 else None
        def accuracy(out):
            decoded = prediction_values(out['answer'])[0]
            return float((decoded == answers).float().mean().item())
        result = prediction_values(intact['answer'])[0]
        metrics = {'cards': len(answers), 'exact': accuracy(intact),
                   'qwen_zero_exact': accuracy(no_qwen),
                   'qwen_mismatch_exact': accuracy(wrong) if wrong else None,
                   'individual_exact': {}, 'operand_only_baseline_exact': {},
                   'by_residual_sign': {}, 'all_baselines_wrong': 0,
                   'all_baselines_wrong_qw5_recovered': 0,
                   'baseline_best_exact': None}
        baseline_hits = []
        for i, name in enumerate(qw5.names):
            pred = prediction_values(intact['head_predictions'][i])[0]
            metrics['individual_exact'][name] = float((pred == answers).float().mean().item())
            raw = qw5.banks[name.replace('.', '__')](operands)
            base = prediction_values(raw)[0] == answers
            baseline_hits.append(base)
            metrics['operand_only_baseline_exact'][name] = float(base.float().mean().item())
        metrics['baseline_best_exact'] = max(metrics['operand_only_baseline_exact'].values())
        none_right = ~torch.stack(baseline_hits).any(dim=0)
        metrics['all_baselines_wrong'] = int(none_right.sum().item())
        metrics['all_baselines_wrong_qw5_recovered'] = int(((result == answers) & none_right).sum().item())
        for key, mask in [('negative', answers < 0), ('zero', answers == 0),
                          ('positive', answers > 0),
                          ('near_residual', answers.abs() <= 3)]:
            n = int(mask.sum().item())
            metrics['by_residual_sign'][key] = {'cards': n, 'exact':
                float(((result[mask] == answers[mask]).float().mean()).item()) if n else None}
        return metrics


class HybridLinkAugmentation(nn.Module):
    """Stage-2 interface only; never enabled by ordinary QW5 training."""
    def __init__(self, qw5_feature_size=1024, link_size=1024):
        super().__init__()
        self.adapter = nn.Linear(qw5_feature_size, link_size, bias=False)
        nn.init.zeros_(self.adapter.weight)

    def forward(self, original_input, qw5_features, *, authorized=False):
        if not authorized:
            raise RuntimeError('QW5_STAGE2_HYBRIDLINK_AUGMENTATION_DISABLED')
        if (original_input.ndim != 2 or qw5_features.ndim != 2
                or original_input.size(0) != qw5_features.size(0)
                or original_input.size(1) != self.adapter.out_features
                or qw5_features.size(1) != self.adapter.in_features):
            raise ValueError('QW5_STAGE2_DIMENSION_MISMATCH')
        return original_input + self.adapter(qw5_features)


def numerical_output(qw5_result, *, authorized=False):
    """Stage-3 numerical authority: fail closed until separately certified."""
    if not authorized:
        raise RuntimeError('QW5_STAGE3_NUMERICAL_OUTPUT_DISABLED')
    return prediction_values(qw5_result['answer'])[0]
