#!/usr/bin/env python3
"""Frozen feature cache + correctness-aware preservation for Glass A/B.

Cache building is lazy and MUST be included in the measured training budget.
Frozen teacher-forced states are consistent across Glass updates, but this is
not an exact substitute for the changing autoregressive path: greedy output
on the full untouched evaluation deck remains the success criterion.
"""
from __future__ import annotations
import hashlib
from tools import nanojev_qwen_lm_deformation_smoke as ab


class FrozenSites:
    def __init__(self, bb, tok, args, baseline):
        self.bb, self.tok, self.args, self.baseline = bb, tok, args, baseline
        self.sites = {}

    def state(self):
        return {k:(h.detach().cpu(),features.detach().cpu(),gold)
                for k,(h,features,gold) in self.sites.items()}

    def restore(self, saved):
        self.sites={k:(h.to(self.args.device),f.to(self.args.device),int(gold))
                    for k,(h,f,gold) in saved.items()}

    def get(self, row, *, replay=False, occurrence=0):
        # On replay protect different gold-token positions over subsequent
        # exposures. Each selected position is cached and reused verbatim.
        expected = ab._token_ids(self.tok, row['target']+'\n')
        if not expected:raise RuntimeError('RATE_EMPTY_GOLD_TARGET')
        digest=int(hashlib.sha256(row['id'].encode()).hexdigest()[:8],16)
        pos=(digest+occurrence)%len(expected) if replay else None
        key=(row['id'],pos)
        if key in self.sites:return self.sites[key]
        if replay:
            prefix=ab._token_ids(self.tok,row['source']+'\n')+expected[:pos]
            gold=expected[pos]
        else:
            baseline=ab._rollout(self.bb,self.tok,row,self.args,
                                  capture_first_wrong=True)
            wrong=baseline.get('wrong_state')
            if wrong is None or wrong.get('gold') is None:
                # A baseline failure can be due to formatting / termination;
                # fall back to the first teacher-forced target position.
                prefix=ab._token_ids(self.tok,row['source']+'\n')
                gold=expected[0]
            else:
                prefix=wrong['prefix']
                gold=int(wrong['gold'])
        h,feat=ab._capture_gold_position(self.bb,self.tok,{'prefix':prefix},self.args)
        self.sites[key]=(h.detach(),feat.detach(),int(gold))
        return self.sites[key]


def train_step(glass, optimizer, bb, hard, replay, sites, args, *, step=0):
    import torch
    import torch.nn.functional as F
    optimizer.zero_grad(set_to_none=True)
    head=bb.get_output_embeddings()
    terms=[]
    for row,is_replay in ((hard,False),(replay,True)):
        h,feat,gold=sites.get(row,replay=is_replay,occurrence=step)
        corrected,delta,prob=glass.apply(h,feat)
        weight=getattr(head,'weight',None)
        logits=head(corrected.to(dtype=weight.dtype if weight is not None else corrected.dtype)).float()[0]
        gold_value=logits[gold]
        if is_replay:
            # Preserve correctness margins, NOT simply delta^2. Correct
            # outputs can be destabilized by very small logit changes.
            rivals=torch.cat((logits[:gold],logits[gold+1:])).max()
            terms.append(args.glass_preserve_weight *
                F.softplus(rivals-gold_value+args.glass_preserve_margin))
        else:
            terms.append(F.cross_entropy(logits.reshape(1,-1),
                                         torch.tensor([gold],device=logits.device)))
        terms.append(args.glass_delta_l2*delta.square().mean())
        if glass.gated:
            target=torch.zeros_like(prob) if is_replay else torch.ones_like(prob)
            terms.append(args.glass_gate_loss*F.binary_cross_entropy(prob,target))
    loss=sum(terms)
    if not bool(torch.isfinite(loss)):
        raise RuntimeError('RATE_GLASS_NONFINITE_TRAIN_LOSS')
    loss.backward()
    torch.nn.utils.clip_grad_norm_(glass.net.parameters(),1.)
    optimizer.step()
    return {'loss':float(loss.detach()),'cached_sites':len(sites.sites)}
