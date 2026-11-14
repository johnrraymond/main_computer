#!/usr/bin/env python3
"""Direct neural player/opponent span tagging and coupled decision scoring.

Qwen/LoRA are frozen. This head operates on their contextual token activations.
No token generation, game-specific inference parser, oracle action set or payoff lookup.
Training labels only supervise role-bearing token spans and the winning decision.
"""
from __future__ import annotations
import math
import torch
from torch import nn
from torch.nn import functional as F

OUTSIDE, PLAYER_BEGIN, PLAYER_INSIDE, OPP_BEGIN, OPP_INSIDE = range(5)
ROLE_TAGS = 5


class GamerDirectHead(nn.Module):
    def __init__(self, hidden_size: int, width: int = 192, layers: int = 1):
        super().__init__()
        if hidden_size <= 0 or width < 16 or width % 4 or not (1 <= layers <= 4):
            raise ValueError('GAMER_INVALID_DIRECT_HEAD_DIMENSIONS')
        self.hidden_size = int(hidden_size)
        self.width = int(width)
        self.layers = int(layers)
        self.project = nn.Sequential(nn.LayerNorm(hidden_size), nn.Linear(hidden_size, width))
        self.global_mix = nn.Linear(width, width, bias=False)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(d_model=width, nhead=4,
                dim_feedforward=width*2, dropout=0.0,
                activation='gelu', batch_first=True, norm_first=False),
            num_layers=layers, enable_nested_tensor=False)
        self.span_tags = nn.Linear(width, ROLE_TAGS)
        self.preference = nn.Sequential(nn.LayerNorm(width * 2),
                                        nn.Linear(width * 2, width),
                                        nn.GELU(), nn.Linear(width, 1))

    def forward(self, hidden):
        """[seq,qwen_dim] -> [seq,5] neural role tags, [seq] action scores."""
        if hidden.ndim != 2 or hidden.shape[1] != self.hidden_size:
            raise ValueError('GAMER_DIRECT_HIDDEN_SHAPE_MISMATCH')
        x = self.project(hidden.float())
        global_context = self.global_mix(x[-1]).unsqueeze(0)
        x = self.encoder((x + global_context).unsqueeze(0)).squeeze(0)
        global_decision = x[-1].expand_as(x)
        return self.span_tags(x), self.preference(torch.cat((x,global_decision),-1)).squeeze(-1)


def coupled_start_logits(tag_logits, preferences):
    """A position can win only insofar as the model predicts PLAYER_BEGIN there.

    The same logits drive training and inference. No oracle candidate locations are
    injected into the model's choice distribution.
    """
    if tag_logits.ndim != 2 or tag_logits.shape[-1] != ROLE_TAGS or \
            preferences.shape != tag_logits.shape[:1]:
        raise ValueError('GAMER_DIRECT_ROLE_SCORE_SHAPE_MISMATCH')
    return preferences + F.log_softmax(tag_logits, dim=-1)[:, PLAYER_BEGIN]


def direct_loss(tag_logits, preferences, tags, eligible, action_starts, winners):
    """Train against ALL eligible positions, not teacher-forced gold candidates.

    Labels indicate positive winning action starts in the numerator; every token
    (including opponent mentions and ordinary words) competes in the denominator.
    Predicted player-begin probabilities gate the same preferences used at query.
    """
    if not bool(eligible.any()) or not action_starts:
        raise ValueError('GAMER_DIRECT_NO_SUPERVISED_SPANS')
    if not winners or not set(winners) <= set(action_starts):
        raise ValueError('GAMER_DIRECT_MISSING_WINNER_SUPERVISION')
    if tag_logits.shape[:1] != tags.shape or eligible.shape != tags.shape:
        raise ValueError('GAMER_DIRECT_LABEL_SHAPE_MISMATCH')
    tag_weights = torch.tensor((1.0, 7.0, 5.0, 5.0, 3.0),
                               device=tag_logits.device, dtype=tag_logits.dtype)
    tag_loss = F.cross_entropy(tag_logits[eligible], tags[eligible], weight=tag_weights)
    winning_starts = torch.zeros_like(eligible)
    for name in winners:
        winning_starts[action_starts[name]] = True
    winning_starts &= eligible
    if not bool(winning_starts.any()):
        raise ValueError('GAMER_DIRECT_NO_WINNER_TOKEN_SPANS')
    scores = coupled_start_logits(tag_logits, preferences)
    # Multi-positive objective: alternative mentions of the winning action are
    # all correct. Opponent and unrelated positions are real competitors.
    choice_loss = torch.logsumexp(scores[eligible],dim=0) - \
                  torch.logsumexp(scores[winning_starts],dim=0)
    return tag_loss + choice_loss, {
        'tag_loss':float(tag_loss.detach()),
        'choice_loss':float(choice_loss.detach()),
        'ranking_loss':0.0}


def decode_action_spans(tags, preferences, eligible, offsets, prompt_start, prompt):
    """Decode neural PLAYER_B/PLAYER_I tags; opponent tags are never candidates.

    This slices character offsets only. It does not apply a vocabulary, aliases,
    word regex, input-game schema, or semantic role parser.
    """
    if len(tags)!=len(offsets) or len(tags)!=len(eligible) or len(preferences)!=len(tags):
        raise ValueError('GAMER_DIRECT_TOKEN_ALIGNMENT_MISMATCH')
    spans=[];current=[]
    def close():
        if not current:return
        a=offsets[current[0]][0]-prompt_start
        b=offsets[current[-1]][1]-prompt_start
        if 0 <= a < b <= len(prompt):
            raw=prompt[a:b].strip()
            if raw and len(raw)<=160:
                spans.append({'name':raw,'start':a,'end':b,
                              'preference':float(preferences[current[0]])})
        current.clear()
    for i,tag in enumerate(tags):
        if not eligible[i]:
            close();continue
        if tag==PLAYER_BEGIN:
            close();current.append(i)
        elif tag==PLAYER_INSIDE and current:
            current.append(i)
        else:
            close()
    close()
    by_name={}
    for item in spans:
        by_name.setdefault(item['name'],[]).append(item)
    choices=[]
    for name,occurrences in by_name.items():
        # Log-mean-exp over mentions, matching the probabilistic training logits
        # while avoiding a raw repetition-count reward.
        values=[o['preference'] for o in occurrences]
        best=max(values)
        value=best + math.log(sum(math.exp(v-best) for v in values)/len(values))
        choices.append({'name':name,'neural_preference':value,
                        'mentions':[[o['start'],o['end']] for o in occurrences]})
    choices.sort(key=lambda x:x['neural_preference'],reverse=True)
    return choices
