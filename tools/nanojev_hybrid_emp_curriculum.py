#!/usr/bin/env python3
"""Shared EMP/zero-line arithmetic cards; NO inference arithmetic executor.

The original trainers own sampling/cancellation laws. This provider translates
existing cards to a neutral schema. Gold delta is accessible to loss/evaluation
only and never enters the head's input feature construction.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import random

from tools import nanojev_zero_line_signed_radius16 as r16
from tools import nanojev_zero_line_outward_return as outward
from tools import nanojev_zero_line_sampled_radius_ladder as sampled
from tools.nanojev_zero_line_zero_only import BANDS

SCHEMA = 'nanojev.hybrid-emp-curriculum.v1'


@dataclass(frozen=True)
class Card:
    a: int
    b: int
    delta: int
    band: int
    magnitude: int
    origin: str
    split: str

    def __post_init__(self):
        if self.a + self.b != self.delta or max(abs(self.a), abs(self.b)) != self.magnitude:
            raise ValueError('HYBRID_CARD_ARITHMETIC_INVARIANT')
        if self.split not in ('train', 'dev', 'test'):
            raise ValueError('HYBRID_CARD_SPLIT')

    def render(self, style=0):
        # Exact signed literals are present, but NEVER a precomputed delta.
        styles=(f'Compute {self.a} + ({self.b})',
                f'Compute ({self.a})+({self.b})',
                f'Input: {self.a} + ({self.b})',
                f'Calculate {self.a} + ({self.b})',
                f'Résumé: ({self.a}) + ({self.b})')
        return styles[style % len(styles)]


def split_for_pair(a, b, seed):
    digest=hashlib.sha256(f'hybrid-emp-pair:{seed}:{a}:{b}'.encode()).digest()
    n=int.from_bytes(digest[:8],'big')%100
    return 'train' if n < 70 else 'dev' if n < 85 else 'test'


def local_population(radius=16, seed=20261026):
    """Exact finite radius entry deck, borrowing the original signed R16 pairs.

    Full operand grid makes sure delta spans [-2r,+2r]. Existing pair_for_max
    adds cancellation curriculum cases, all deduplicated by ordered operands.
    Split is assigned once by ordered pair, not rendered grammar/step.
    """
    if not 1 <= radius <= 16:raise ValueError('HYBRID_LOCAL_RADIUS_UNCERTIFIED')
    found={}
    for a in range(-radius,radius+1):
        for b in range(-radius,radius+1):
            found[(a,b)]='full-domain'
    for m in range(1,radius+1):
        for d in range(-radius,radius+1):
            for swap in (False,True):
                for direction in (-1,1):
                    pair=r16.pair_for_max(m,d,swap,direction)
                    if pair and all(abs(x)<=radius for x in pair):
                        found[pair]='signed-radius16'
    result={'train':[],'dev':[],'test':[]}
    for (a,b),origin in sorted(found.items()):
        split=split_for_pair(a,b,seed)
        m=max(abs(a),abs(b))
        band=r16.band_of(max(m,16))
        result[split].append(Card(a,b,a+b,band,m,origin,split))
    if not all(result.values()):raise RuntimeError('HYBRID_EMPTY_LOCAL_SPLIT')
    return result


class LocalCurriculum:
    def __init__(self, radius=16, seed=20261026):
        self.radius=int(radius);self.seed=int(seed)
        self.decks=local_population(radius,seed)
        self.rng=random.Random(seed)
        self.cursor=0
        self.difficulties={d:1.0 for d in range(-2*radius,2*radius+1)}
        self.by_delta={d:[x for x in self.decks['train'] if x.delta==d]
                       for d in range(-2*radius,2*radius+1)}
        self.eligible=[d for d,cards in self.by_delta.items() if cards]
        if len(self.eligible)<radius*2+1:raise RuntimeError('HYBRID_LOCAL_MISSING_RESIDUALS')

    def next(self):
        # EMP-style difficulty feedback is derived ONLY from TRAIN cards.
        if self.cursor % 3 == 0:
            delta=self.eligible[self.cursor//3 % len(self.eligible)]
        else:
            delta=self.rng.choices(self.eligible,[self.difficulties[d] for d in self.eligible])[0]
        card=self.rng.choice(self.by_delta[delta]);self.cursor+=1
        return card

    def emphasize(self, delta, error):
        if delta not in self.difficulties:raise ValueError('HYBRID_BAD_RESIDUAL_WEIGHT')
        # Bounded exponential smoothing, never DEV/TEST-driven.
        w=self.difficulties[delta]
        self.difficulties[delta]=max(1.,min(8.,0.95*w+0.05*(1.+7.*float(bool(error)))))

    def state(self):
        return {'cursor':self.cursor,'rng':self.rng.getstate(),'difficulties':self.difficulties.copy()}

    def load(self,state):
        if set(state['difficulties'])!=set(self.difficulties):raise ValueError('HYBRID_CURRICULUM_DRIFT')
        self.cursor=state['cursor'];self.rng.setstate(state['rng']);self.difficulties=state['difficulties'].copy()


def outward_cards(*,radius,seed,size=32,cursor=0,weights=None,rng=None,split='train'):
    """Wrap original EMP/zero-line outward machinery, without reimplementing it.

    This is available for the later outward stages. Current Head 1's certified
    lexical radius is 16; the consumer must refuse these cards until Head 1
    is recertified for their source literals.
    """
    if radius<64:raise ValueError('HYBRID_OUTWARD_MIN_RADIUS_64')
    weights=[1.]*len(BANDS) if weights is None else weights
    rng=random.Random(seed) if rng is None else rng
    if split=='train':
        rows,next_cursor=outward.generate_batch(rng,radius,seed,size,weights,cursor,'outward',radius,radius)
    elif split in ('dev','test'):
        rows=[row for group in sampled.evaluation_cards(radius,split,seed,radius,1,1,2) for row in group]
        next_cursor=cursor
    else:raise ValueError('HYBRID_BAD_SPLIT')
    cards=[Card(int(a),int(b),int(d),int(band),int(m),'emp-zero-line-outward',split)
           for a,b,d,band,m in rows]
    return cards,next_cursor


def signature(cards):
    h=hashlib.sha256()
    for card in cards:
        h.update(f'{card.a},{card.b},{card.delta},{card.band},{card.split}\n'.encode())
    return h.hexdigest()
