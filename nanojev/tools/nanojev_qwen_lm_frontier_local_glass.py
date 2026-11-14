#!/usr/bin/env python3
"""Activation-local Qwen glass adapted to the frontier A/B/C comparison.

Reuses the proven FailureModeGlass exact-signature router and token-margin
repair optimizer.  It never uses source text or gold tokens in routing; gold
labels are consulted by the offline trainer only.  The Qwen backbone and LM
head stay frozen.  Unmatched activations receive exactly zero correction.
"""
from __future__ import annotations
from types import SimpleNamespace

from tools import nanojev_qwen_lm_failure_mode_glass as local
from tools import nanojev_qwen_lm_deformation_smoke as ab


class FrontierLocalGlass:
    def __init__(self, hidden, feature_layers=4, *, threshold=.99999, device='cpu'):
        if feature_layers != 4:
            raise ValueError('LOCAL_GLASS_REQUIRES_FOUR_SIGNATURE_LAYERS')
        self.hidden = int(hidden)
        self.feature_layers = int(feature_layers)
        self.router = local.FailureModeGlass(self.hidden, threshold=threshold, device=device)
        self.targets = {}  # Mode -> gold token. Ambiguous modes must not oscillate.

    def signature(self, features):
        import torch.nn.functional as F
        x = features.detach().float().reshape(self.feature_layers, self.hidden)
        # Equivalent to the existing failure-mode four-layer signature:
        # normalize each activation independently, concatenate and normalize.
        return F.normalize(F.normalize(x, dim=-1).reshape(1, -1), dim=-1)

    def apply(self, h, features):
        import torch
        sig = self.signature(features)
        adjusted, mode = self.router.apply(h, sig)
        delta = adjusted.float() - h.float()
        # Conform to ContinuousGlass.apply without fabricating gate probability.
        return adjusted, delta.to(h.dtype), None

    def add(self, features, gold):
        mode, created = self.router.add(self.signature(features))
        if mode in self.targets and self.targets[mode] != int(gold):
            return mode, created, True
        self.targets[mode] = int(gold)
        return mode, created, False

    @property
    def n_modes(self):
        return self.router.n_modes

    def state(self):
        return {'hidden': self.hidden, 'feature_layers': self.feature_layers,
                'router': self.router.state(), 'targets':self.targets}

    @classmethod
    def restore(cls, state, *, device='cpu'):
        obj = cls(state['hidden'], state['feature_layers'],
                  threshold=state['router']['threshold'], device=device)
        obj.router = local.FailureModeGlass.restore(state['router'], device=device)
        obj.targets = {int(k):int(v) for k,v in state.get('targets',{}).items()}
        return obj


def repair_step(adapter, bb, tok, row, rollout_args, *,
                token_steps=32, lr=.1, margin=1., max_token_fixes=2):
    """Repair successive genuine wrong greedy tokens; charge all work to clock.

    A failed correction is *not* recorded as an acquired competency. Actual
    gains/losses are determined by separate unassisted greedy evaluation.
    """
    head = bb.get_output_embeddings()
    training_args = SimpleNamespace(device=rollout_args.device,
        token_train_steps=int(token_steps), lr=float(lr), logit_margin=float(margin),
        l2=0., grad_clip=10.)
    created = 0
    updated = 0
    optimizer_steps = 0
    collisions = 0
    for attempt in range(max_token_fixes):
        trace = ab._rollout(bb, tok, row, rollout_args, glass=adapter,
                            capture_first_wrong=True)
        if trace['correct']:
            return {'correct_after': True, 'created': created,
                    'updated': updated, 'token_optimizer_steps': optimizer_steps,
                    'ambiguous_mode_collisions':collisions}
        fault = trace.get('wrong_state')
        if fault is None or fault.get('gold') is None:
            break
        h, features = ab._capture_gold_position(bb, tok, fault, rollout_args)
        mode, is_new, ambiguous = adapter.add(features, int(fault['gold']))
        created += int(is_new)
        if ambiguous:
            # Two different target tokens routed to the same activation key
            # cannot safely be disambiguated by this local router.
            collisions += 1
            break
        original = adapter.router.corrections[mode].detach().clone()
        result = local.train_one_token(head, adapter.router, mode,
                                       h, int(fault['gold']), training_args)
        if not result['ok']:
            # An untrainable repair must not destroy a previously useful mode.
            import torch
            with torch.no_grad(): adapter.router.corrections[mode].copy_(original)
        updated += 1
        optimizer_steps += result['steps']
        if not result['ok']:
            break
    return {'correct_after': False, 'created': created,
            'updated': updated, 'token_optimizer_steps': optimizer_steps,
            'ambiguous_mode_collisions':collisions}
