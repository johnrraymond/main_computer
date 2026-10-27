#!/usr/bin/env python3
"""QW5 / HybridQwen shared-object staging interface.

This does NOT activate stages 2 or 3. Its zero-cycle path is useful for
probing QW5 on the exact final/intermediate Qwen states from the runtime.
When a separate certified stage-2 checkpoint exists, the same QW5 object can
contribute to HybridLink's existing input at each cycle. The existing
HybridLink parameters and recurrent operation are untouched.
"""
from __future__ import annotations

import torch
from torch import nn

from tools.nanojev_qw5 import QW5, HybridLinkAugmentation, numerical_output, QWEN_TAPS
from tools.nanojev_qwen_hybridagent import HybridQwen


class QW5HybridRuntime(nn.Module):
    """One shared QW5 instance is used for both feedback and final prediction."""

    def __init__(self, hybrid: HybridQwen, qw5: QW5, *,
                 stage2_certified=False, stage3_certified=False):
        super().__init__()
        if not isinstance(hybrid, HybridQwen) or not isinstance(qw5, QW5):
            raise TypeError('QW5_RUNTIME_REQUIRES_ACTUAL_HYBRID_AND_QW5')
        self.hybrid = hybrid
        self.qw5 = qw5
        self.augmentation = HybridLinkAugmentation(qw5.feedback_projection[-1].out_features,
                                                     qw5.qwen_width)
        # Gates are runtime metadata, not checkpoint inference from training loss.
        self.stage2_certified = bool(stage2_certified)
        self.stage3_certified = bool(stage3_certified)

    def _step(self, *, input_ids=None, inputs_embeds=None, cache=None, mask=None):
        if (input_ids is None) == (inputs_embeds is None):
            raise ValueError('QW5_RUNTIME_ONE_INPUT_KIND_REQUIRED')
        if inputs_embeds is not None:
            dtype = self.hybrid.backbone.get_input_embeddings().weight.dtype
            inputs_embeds = inputs_embeds.to(dtype=dtype)
        out = self.hybrid.backbone(input_ids=input_ids, inputs_embeds=inputs_embeds,
                                   attention_mask=mask, past_key_values=cache,
                                   output_hidden_states=True, use_cache=True, return_dict=True)
        if out.past_key_values is None or not out.hidden_states or len(out.hidden_states) <= max(QWEN_TAPS):
            raise RuntimeError('QW5_RUNTIME_QWEN_STATES_UNAVAILABLE')
        states = torch.stack([out.hidden_states[i][:, -1, :].float() for i in QWEN_TAPS], dim=1)
        final = out.hidden_states[-1][:, -1, :]
        return states, final, out.past_key_values

    def reason(self, prompt_ids, operands, *, latent_steps=0):
        """Return an *experimental* QW5 result, not production-authorized output.

        The zero-step path is active. Recurrent QW5 feedback requires a future
        separately authorized stage-2 checkpoint, never phase-1 training alone.
        """
        if prompt_ids.ndim != 2 or prompt_ids.size(0) != 1 or not prompt_ids.size(1):
            raise ValueError('QW5_RUNTIME_ONE_NONEMPTY_PROMPT')
        if operands.shape != (1, 2):
            raise ValueError('QW5_RUNTIME_ONE_OPERAND_PAIR')
        if not 0 <= latent_steps <= 256:
            raise ValueError('QW5_RUNTIME_BAD_LATENT_STEPS')
        if latent_steps and not self.stage2_certified:
            raise RuntimeError('QW5_STAGE2_HYBRIDLINK_AUGMENTATION_DISABLED')
        if latent_steps:
            marker = torch.full((1, 1), self.hybrid.start_id,
                                dtype=prompt_ids.dtype, device=prompt_ids.device)
            prompt_ids = torch.cat((prompt_ids, marker), dim=1)
        mask = torch.ones_like(prompt_ids)
        states, hidden, cache = self._step(input_ids=prompt_ids, mask=mask)
        for _ in range(latent_steps):
            result = self.qw5(operands, states)
            # Augment the *existing* HybridLink input h, not its mechanism.
            combined = self.augmentation(hidden.float(), result['hybrid_features'],
                                         authorized=self.stage2_certified)
            dtype = next(self.hybrid.head.parameters()).dtype
            next_embedding = self.hybrid.head(combined.to(dtype))
            if not bool(torch.isfinite(next_embedding).all()):
                raise RuntimeError('QW5_RUNTIME_NONFINITE_LINK_FEEDBACK')
            mask = torch.cat((mask, mask.new_ones((1, 1))), dim=1)
            states, hidden, cache = self._step(inputs_embeds=next_embedding[:, None, :],
                                                cache=cache, mask=mask)
        # SAME QW5 instance/readout is used after the final recurrent pass.
        return self.qw5(operands, states)

    def answer(self, result):
        """Final numerical authority requires a separately certified stage 3."""
        if not self.stage3_certified:
            raise RuntimeError('QW5_STAGE3_NUMERICAL_OUTPUT_DISABLED')
        return numerical_output(result, authorized=True)
