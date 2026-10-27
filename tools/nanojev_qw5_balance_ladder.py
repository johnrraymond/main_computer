#!/usr/bin/env python3
"""Fail-closed QW5 zero-line -> Hawk-Dove rung-climb experiment.

A separate research run: never mutates stage-1 QW5, EMP, HybridLink, or LM.
The FIRST failed rung stops the climb. Both the zero-line and past rungs
must remain competent; intact/zeroed/mismatched Qwen are compared on DEV.
Tests are split by numerical family, not by presentation or near-boundary row.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import random
import sys

RUNG_ORDER = ('Z0', 'Z1', 'Z2', 'R0a', 'R0b', 'R0c', 'R1', 'R2', 'B0', *(f'T{i:02d}' for i in range(9)))
SCHEMA = 'nanojev.qw5.zero-balance-hawk-dove-ladder.v3'
R0_RUNGS = ('R0a', 'R0b', 'R0c')
TOTAL = 1_000_000
NEAR_RESIDUAL_LIMIT = 16  # Same boundary for sampling and gate measurement.
# Denominator must divide TOTAL: each payoff family has an EXACT balance.
PAYOFF_PAIRS = ((1, 1), (1, 3), (3, 1), (2, 2), (1, 4), (4, 1),
                (2, 3), (3, 2), (1, 7), (7, 1), (3, 5), (5, 3))
SHIFTS = (-1, 0, 1)
OFFSETS = (0, -1, 1, -2, 2, -8, 8, -32, 32, -256, 256,
           -4096, 4096, -25000, 25000, -100000, 100000)


@dataclass(frozen=True)
class Card:
    rung: str
    split: str
    family: str
    operands: tuple[int, int]
    answer: int
    prompt: str
    near: bool
    zero: bool
    source: tuple[int, int]  # actual opponent counts or cancellation operands
    pair_id: str  # exact invariant counterpart, kept in the same split
    contrast_id: str | None = None  # same operands, opposite question/answer in R2


def split_family(name: str, seed: int) -> str:
    digit = hashlib.sha256(f'qw5-ladder:{seed}:{name}'.encode()).digest()[0] % 10
    return 'test' if digit < 2 else 'dev' if digit < 4 else 'train'


def payoffs(p: int, q: int, ch: int, cd: int):
    from tools import nanojev_hawk_dove_million as hd
    return {hd.TAGS[0]: Decimal(ch-q), hd.TAGS[1]: Decimal(cd+p),
            hd.TAGS[2]: Decimal(ch), hd.TAGS[3]: Decimal(cd)}


def payoff_text(p):
    from tools import nanojev_hawk_dove_million as hd
    return 'Payoff bindings: ' + '; '.join(f'{tag}={int(p[tag])}' for tag in hd.TAGS) + '.'


def story_payoffs(p):
    from tools import nanojev_hawk_dove_million as hd
    v = [int(p[tag]) for tag in hd.TAGS]
    return (' If you play Hawk, you earn ' + str(v[0]) + ' against Hawk and ' + str(v[1]) +
            ' against Dove. If you play Dove, you earn ' + str(v[2]) +
            ' against Hawk and ' + str(v[3]) + ' against Dove.')


def game_cards(rung: str, seed: int) -> list[Card]:
    """Inputs contain *only values visible in the relevant tier*.

    B0 and T00: computed totals are visible and used as (X,-Y).
    T01+ : numeric EMP operand input is the encounter-count pair (a,b),
            NEVER hidden X or Y. Qwen prompt contains the tier's true context.
    Generated variable-payoff variants are deliberately distinct from the
    original fixed-binding historical teacher, and include payoff definitions.
    """
    from tools import nanojev_hawk_dove_million as hd
    if rung not in ('B0', *hd.TIERS):
        raise ValueError('QW5_NOT_GAME_RUNG:' + rung)
    rows = []
    for p, q in PAYOFF_PAIRS:
        if TOTAL % (p+q):
            raise AssertionError('NO_EXACT_BALANCE_FOR_FAMILY')
        center = TOTAL * p // (p+q)
        for ch in SHIFTS:
            for cd in SHIFTS:
                pp = payoffs(p, q, ch, cd)
                group = f'game:{p}:{q}:{ch}:{cd}'
                # Hold out entire decision-boundary ratios (p,q), not
                # merely common-offset variations of a seen boundary.
                split = split_family(f'game:{p}:{q}', seed)
                for offset in OFFSETS:
                    a = center + offset
                    b = TOTAL - a
                    if a < 0 or b < 0:
                        continue
                    # Paired invariant: adding one to every payoff adds
                    # TOTAL to X and Y equally, preserving X-Y exactly.
                    for common_shift in (0, 1):
                        actual = {tag: val + common_shift for tag,val in pp.items()}
                        x, y, _ = hd.oracle(a, b, actual)
                        if x != int(x) or y != int(y):
                            raise AssertionError('FRACTIONAL_HAWK_DOVE_TARGET')
                        delta = int(x-y)
                        if abs(delta) > 9_999_999 or max(abs(int(x)), abs(int(y))) > 9_999_999:
                            raise AssertionError('QW5_SEVEN_DIGIT_RANGE_EXCEEDED')
                        if rung == 'B0':
                            prompt = f'Compare the two totals. Hawk total {int(x)}. Dove total {int(y)}. Give the signed Hawk-minus-Dove difference.'
                            operands = (int(x), -int(y))
                        else:
                            prompt = hd.render(rung, a, b, actual, full_slots=())
                            # Certify the historical tier before adding the
                            # variable-payoff context of this new curriculum.
                            hd.certify(prompt, rung, a, b, actual, full_slots=())
                            if rung == 'T08':
                                prompt += story_payoffs(actual)
                            else:
                                prompt = payoff_text(actual) + '\n' + prompt
                            prompt += '\nSigned Hawk-minus-Dove difference:'
                            operands = (int(x), -int(y)) if rung == 'T00' else (a, b)
                        rows.append(Card(rung, split, group, operands, delta,
                                         prompt, abs(offset) <= 2, offset == 0, (a,b),
                                         f'{group}:offset:{offset}'))
    return rows


def cancellation_cards(rung: str, seed: int) -> list[Card]:
    if rung not in ('Z0', 'Z1', 'Z2'):
        raise ValueError('QW5_NOT_ZERO_RUNG:' + rung)
    radius = {'Z0': 512, 'Z1': 65_536, 'Z2': 2_000_000}[rung]
    # Distinct magnitude families hold all +/- displacements together.
    rng = random.Random(f'qw5-zero-ladder:{rung}:{seed}')
    anchors = set()
    while len(anchors) < 180:
        k = rng.randint(1, radius) if rng.randrange(2) else min(radius, round(2 ** rng.uniform(0, radius.bit_length()-1)))
        anchors.add(max(1, k))
    rows = []
    for anchor in sorted(anchors):
        group = f'{rung}:magnitude:{anchor}'
        split = split_family(group, seed)
        for delta in (-128, -8, -2, -1, 0, 1, 2, 8, 128):
            if anchor < abs(delta) + 1:
                continue
            # Either BOTH opposed orientations exist, or neither does.
            # Otherwise the invariance pair would be malformed.
            if any(max(anchor,abs(-anchor*sign+delta))>radius for sign in (-1,1)):
                continue
            for sign in (-1, 1):
                a = anchor * sign
                b = -a + delta
                prompt = (f'A location starts at {a} and changes by {b}. '
                          'What is the signed final position?')
                rows.append(Card(rung, split, group, (a,b), delta,
                                 prompt, abs(delta)<=2, delta==0, (a,b),
                                 f'{group}:delta:{delta}'))
    return rows



# R0a/b/c–R2 are the representation-and-balance transition. Numerical inputs
# DIFFER by stage: R0 signed addition; R1 (A,-B), already signed;
# R2 strictly (A,B) for BOTH orientations, so only Qwen can set the direction.
TRANSITION_RESIDUALS = (0, -1, 1, -2, 2, -8, 8, -16, 16, -32, 32,
                        -128, 128, -512, 512, -4096, 4096, -25000, 25000,
                        -100000, 100000, -500000, 500000, -1000000, 1000000)
TRANSITION_MAX = 2_000_000
R0_MAGNITUDE_RANGES = ((17, 128, '17_128'),
                       (129, 4096, '129_4096'),
                       (4097, 100_000, '4097_100000'),
                       (100_001, 1_000_000, '100001_1000000'))
R0_BANDS = {'R0a': 2, 'R0b': 3, 'R0c': 4}
R0_CONTROL_RESIDUALS = (0, -1, 1, -2, 2, -8, 8, -16, 16)
R0_MAGNITUDES_PER_SPLIT = 12  # 12 unseen magnitudes per scale and sign per split.


@lru_cache(maxsize=128)
def r0_residuals(rung: str, split: str, seed: int) -> tuple[int, ...]:
    """Exactly-held-out signed residual values, not only held-out anchors.

    Zero and <=16 controls intentionally recur across splits. ALL far signed
    values (including their magnitudes) are unique to their assigned split.
    R0a/b/c are separate training stages; heldouts are stage-local.
    """
    if rung not in R0_RUNGS or split not in ('train', 'dev', 'test'):
        raise ValueError('QW5_R0_INVALID_RUNG_OR_SPLIT')
    residuals = list(R0_CONTROL_RESIDUALS)
    for lower, upper, _ in R0_MAGNITUDE_RANGES[:R0_BANDS[rung]]:
        rng = random.Random(f'qw5-r0-holdout-v3:{rung}:{seed}:{lower}:{upper}')
        pool = rng.sample(range(lower, upper+1), 3*R0_MAGNITUDES_PER_SPLIT)
        # Put the exact scale ceiling in TRAIN, while DEV/TEST assess
        # distinct unseen interior magnitudes near that ceiling.
        if upper not in pool:
            pool[0] = upper
        # Partition magnitudes, not only signed target values: neither their
        # negative nor positive appears in another split for the same rung.
        partitions = {'train':pool[:12], 'dev':pool[12:24], 'test':pool[24:36]}
        for magnitude in partitions[split]:
            residuals.extend((-magnitude, magnitude))
    return tuple(residuals)


def is_r0(rung: str) -> bool:
    return rung in R0_RUNGS


def rung_scale_names(rung: str) -> tuple[str, ...]:
    if not is_r0(rung):
        return ()
    return ('zero', 'near_positive', 'near_negative',
            *(f'far_{side}_{scale}' for side in ('positive','negative')
              for _, _, scale in R0_MAGNITUDE_RANGES[:R0_BANDS[rung]]))


def transition_anchors(seed: int) -> tuple[int, ...]:
    rng = random.Random(f'qw5-transition-magnitudes-v1:{seed}')
    anchors = {128, 512, 4096, 65_536, 100_000, 250_000, 500_000,
               1_000_000, 1_500_000, 1_999_999}
    while len(anchors) < 180:
        anchors.add(rng.randint(256, TRANSITION_MAX - 1))
    return tuple(sorted(anchors))


def transition_cards(rung: str, seed: int) -> list[Card]:
    """Paired invariant prompts for R0/R1, plus R2 contrast quadruples.

    For R2, the operand tensors are (A,B) with no sign/direction token. Each
    contrast has four prompts: forward symbolic/semantic and reverse
    symbolic/semantic. Two invariant pairs each have a fixed correct label,
    and the two pairs require opposite labels. TEST holds out entire anchor
    families and the contrast is never divided across a data split or sample.
    """
    if rung not in (*R0_RUNGS,'R1','R2'):
        raise ValueError('QW5_NOT_TRANSITION_RUNG:' + rung)
    rows = []
    for anchor in transition_anchors(seed):
        family = f'transition:anchor:{anchor}'
        split = split_family(family,seed)
        deltas = r0_residuals(rung,split,seed) if is_r0(rung) else TRANSITION_RESIDUALS
        for delta in deltas:
            if is_r0(rung):
                # Paired commutativity of signed addition. Unlike requiring
                # +/-anchor orientations simultaneously, swapping A/B lets
                # *every split* include residuals up to +/-1,000,000 while
                # keeping each individual operand within +/-2,000,000.
                if anchor <= abs(delta):
                    continue
                allowed=[sign for sign in (-1,1)
                         if abs(-sign*anchor+delta)<=TRANSITION_MAX]
                if not allowed:
                    continue
                sign=allowed[int(hashlib.sha256(f'{anchor}:{delta}:{seed}'.encode()).hexdigest(),16)%len(allowed)]
                a=sign*anchor
                b=-a+delta
                pair_id=f'{family}:residual:{delta}'
                variants=((a,b),(b,a))
                for orientation,(first,second) in enumerate(variants):
                    if orientation==0:
                        prompt=f'A location starts at {first} and changes by {second}. Return the signed final position.'
                    else:
                        prompt=f'Compute the signed sum of {first} and {second}. Return the exact result.'
                    rows.append(Card(rung,split,family,(first,second),delta,prompt,
                                     abs(delta)<=16,delta==0,(first,second),pair_id))
            else:
                # Positive untransformed totals, including both possible
                # signed directions and exact equality. Nothing here supplies
                # the final difference as a hidden input.
                b=anchor
                a=b+delta
                if a < 1 or a > TRANSITION_MAX:
                    continue
                contrast=f'{family}:delta:{delta}'
                if rung == 'R1':
                    variants=(
                        f'Calculate ({a}) + (-{b}). Return the signed number.',
                        f'Calculate {a} minus {b}. Return the signed number.',
                        f'Find the signed difference: first value {a}, second value {b}. Return first minus second.',
                        f'The first account contains {a}; the second {b}. What is the first account advantage, allowing a negative answer?',
                    )
                    first=variants[(abs(delta)+anchor)%4]
                    second=variants[(abs(delta)+anchor+1)%4]
                    for prompt in (first,second):
                        rows.append(Card(rung,split,family,(a,-b),delta,prompt,
                                         abs(delta)<=16,delta==0,(a,b),contrast))
                else:
                    # These four share identical numeric inputs (A,B). Two
                    # ask first-minus-second and two ask the reverse.
                    prompts=(
                        (f'Compute {a} minus {b}. Give the signed answer.',delta,'forward'),
                        (f'How much does the first total {a} exceed the second total {b}? The signed advantage may be negative.',delta,'forward'),
                        (f'Compute {b} minus {a}. Give the signed answer.',-delta,'reverse'),
                        (f'How much does the second total {b} exceed the first total {a}? The signed advantage may be negative.',-delta,'reverse'),
                    )
                    for prompt,answer,direction in prompts:
                        rows.append(Card(rung,split,family,(a,b),answer,prompt,
                                         abs(answer)<=16,answer==0,(a,b),
                                         f'{contrast}:{direction}',contrast))
    return rows


def make_cards(rung: str, seed: int) -> list[Card]:
    if rung.startswith('Z'):
        return cancellation_cards(rung,seed)
    if rung.startswith('R'):
        return transition_cards(rung,seed)
    return game_cards(rung,seed)



def scored_band(answer: int) -> str:
    """Classify by the scored ANSWER, not the source displacement Card.near."""
    if answer == 0:
        return 'zero'
    direction = 'positive' if answer > 0 else 'negative'
    return ('near_' if abs(answer) <= NEAR_RESIDUAL_LIMIT else 'far_') + direction


def band_counts(cards: list[Card]) -> dict[str, int]:
    counts = {name: 0 for name in ('zero', 'near_positive', 'near_negative',
                                   'far_positive', 'far_negative')}
    for card in cards:
        counts[scored_band(card.answer)] += 1
    return counts



def residual_scale_band(answer: int) -> str:
    """Magnitudes that R0 explicitly expands; class zero and +/- near separately."""
    if answer==0:
        return 'zero'
    direction='positive' if answer>0 else 'negative'
    magnitude=abs(answer)
    if magnitude<=NEAR_RESIDUAL_LIMIT:
        return 'near_'+direction
    if magnitude<=128:
        scale='17_128'
    elif magnitude<=4096:
        scale='129_4096'
    elif magnitude<=100_000:
        scale='4097_100000'
    else:
        scale='100001_1000000'
    return 'far_'+direction+'_'+scale


def sample_split(cards: list[Card], split: str, limit: int, seed: int) -> list[Card]:
    if limit < 20 or limit % 2:
        raise ValueError('QW5_CARDS_MUST_BE_EVEN_AT_LEAST_20')
    pool = [r for r in cards if r.split == split]
    if cards and cards[0].rung == 'R2':
        return sample_directional_contrasts(pool,split,limit,seed)
    # Sample COUNTERFACTUAL PAIRS as a unit. Their answer must remain the same
    # under reversal of zero-line operands or common payoff translation.
    pairs = {}
    for row in pool:
        pairs.setdefault(row.pair_id, []).append(row)
    if is_r0(cards[0].rung):
        labels=rung_scale_names(cards[0].rung)
    else:
        labels=('zero','near_positive','near_negative','far_positive','far_negative')
    categories = {k: [] for k in labels}
    for pair_id,pair in pairs.items():
        if len(pair)!=2 or len({r.answer for r in pair})!=1:
            raise AssertionError('QW5_BROKEN_INVARIANCE_PAIR:'+pair_id)
        r=pair[0]
        # Card.near describes the source displacement, not this target band.
        key = residual_scale_band(r.answer) if is_r0(cards[0].rung) else scored_band(r.answer)
        categories[key].append(pair)
    rng = random.Random(f'qw5-sample:{seed}:{split}:{cards[0].rung if cards else "none"}')
    for group in categories.values():
        rng.shuffle(group)
    if is_r0(cards[0].rung) and split == 'train':
        # Each new stage actually TRAINS on the exact ceiling (both signs),
        # while DEV/TEST use different, strictly held-out magnitudes.
        ceiling={'R0a':4096,'R0b':100_000,'R0c':1_000_000}[cards[0].rung]
        for target in (-ceiling,ceiling):
            group=categories[residual_scale_band(target)]
            slot=next((i for i,pair in enumerate(group) if pair[0].answer==target),None)
            if slot is None:
                raise RuntimeError('QW5_R0_MISSING_TRAIN_CEILING:'+str(target))
            group[0],group[slot]=group[slot],group[0]
    required = min(limit//(2*len(categories)), min(map(len, categories.values())))
    min_pairs=1 if is_r0(cards[0].rung) else 2
    if required < min_pairs:
        raise RuntimeError(f'QW5_INSUFFICIENT_BALANCE_COVERAGE:{split}:{dict((k,len(v)) for k,v in categories.items())}')
    selected_pairs = [r for group in categories.values() for r in group[:required]]
    remaining = [r for group in categories.values() for r in group[required:]]
    rng.shuffle(remaining)
    selected_pairs.extend(remaining[:limit//2-len(selected_pairs)])
    chosen=[r for pair in selected_pairs for r in pair]
    if len(chosen)!=limit:
        raise AssertionError('QW5_INCOMPLETE_PAIR_SAMPLE')
    coverage = band_counts(chosen)
    if any(n < 2 for n in coverage.values()):
        raise RuntimeError(f'QW5_SAMPLED_MISSING_SCORED_BAND:{split}:{coverage}')
    rng.shuffle(chosen)
    return chosen



def sample_directional_contrasts(pool: list[Card], split: str, limit: int, seed: int) -> list[Card]:
    """R2: preserve four-example contrast groups and both invariant pairs."""
    if limit % 4:
        raise ValueError('QW5_R2_CARDS_MULTIPLE_OF_FOUR_REQUIRED')
    contrasts={}
    for row in pool:
        contrasts.setdefault(row.contrast_id, []).append(row)
    categories={'zero':[],'near':[],'far':[]}
    for key, group in contrasts.items():
        if key is None or len(group)!=4 or len({r.operands for r in group})!=1:
            raise AssertionError('QW5_R2_BROKEN_CONTRAST:'+str(key))
        pairs={}
        for r in group:
            pairs.setdefault(r.pair_id,[]).append(r)
        if len(pairs)!=2 or any(len(v)!=2 or len({r.answer for r in v})!=1 for v in pairs.values()):
            raise AssertionError('QW5_R2_BROKEN_DIRECTION_PAIRS')
        labels={v[0].answer for v in pairs.values()}
        if len(labels)!= (1 if group[0].answer==0 else 2):
            raise AssertionError('QW5_R2_MISSING_SIGN_REVERSAL')
        value=abs(group[0].answer)
        categories['zero' if value==0 else ('near' if value<=NEAR_RESIDUAL_LIMIT else 'far')].append(group)
    rng=random.Random(f'qw5-r2-contrasts:{seed}:{split}')
    for groups in categories.values():
        rng.shuffle(groups)
    selected=[]
    for name in ('zero','near','far'):
        if not categories[name]:
            raise RuntimeError('QW5_R2_MISSING_BALANCE_CLASS:'+name)
        selected.append(categories[name].pop())
    remaining=[g for groups in categories.values() for g in groups]
    rng.shuffle(remaining)
    selected.extend(remaining[:limit//4-len(selected)])
    result=[r for contrast in selected for r in contrast]
    if len(result)!=limit or any(n<2 for n in band_counts(result).values()):
        raise AssertionError('QW5_R2_INSUFFICIENT_COVERAGE')
    rng.shuffle(result)
    return result


def audit_splits(cards: list[Card]):
    mapping = {}
    boundaries = {}
    for r in cards:
        if r.family in mapping and mapping[r.family] != r.split:
            raise AssertionError('QW5_FAMILY_SPLIT_LEAKAGE')
        mapping[r.family] = r.split
        if r.rung.startswith('T') or r.rung == 'B0':
            boundary = ':'.join(r.family.split(':')[:3])
            if boundary in boundaries and boundaries[boundary] != r.split:
                raise AssertionError('QW5_BOUNDARY_SPLIT_LEAKAGE')
            boundaries[boundary] = r.split
    if {r.split for r in cards} != {'train','dev','test'}:
        raise AssertionError('QW5_MISSING_SPLIT')
    return {'families': len(mapping), 'counts': {s: sum(r.split == s for r in cards) for s in ('train','dev','test')}}


def cards_for_rung(args, rung: str, split: str) -> int:
    if is_r0(rung):
        return getattr(args, 'r0_'+split+'_cards', {'train':512,'dev':128,'test':128}[split])
    return getattr(args,split+'_cards')


def preview(args):
    report = []
    for rung in RUNG_ORDER:
        cards = make_cards(rung, args.seed)
        audit = audit_splits(cards)
        selected = {s: sample_split(cards,s, cards_for_rung(args,rung,s), args.seed)
                    for s in ('train','dev','test')}
        report.append({'rung':rung, **audit, 'selected':{k: len(v) for k,v in selected.items()},
                       'scored_bands':{k:band_counts(v) for k,v in selected.items()},
                       'residual_scale_bands':{k:{band:sum(residual_scale_band(c.answer)==band for c in v)
                          for band in sorted({residual_scale_band(c.answer) for c in v})}
                          for k,v in selected.items()} if is_r0(rung) else None,
                       'heldout_far_residuals': {split:sorted({c.answer for c in cards if c.split==split and abs(c.answer)>16})[:8]
                          for split in ('train','dev','test')} if is_r0(rung) else None,
                       'zero_in_dev':sum(c.zero for c in selected['dev']),
                       'near_in_dev':sum(c.near for c in selected['dev']),
                       'example': selected['dev'][0].prompt[:240]})
    return report



def example_cards(args):
    """Actual TRAIN sample cards, exact prompts and tensor operands, no Qwen."""
    n=args.examples_per_rung
    if n<1 or n>128:
        raise ValueError('QW5_BAD_EXAMPLE_COUNT')
    result={}
    for rung in (*R0_RUNGS,'R1','R2'):
        sampled=sample_split(make_cards(rung,args.seed),'train',
                             max(40, (n+3)//4*4),args.seed)
        # Intact contrast/invariance blocks; output may exceed n slightly.
        groups={}
        for card in sampled:
            key=card.contrast_id if rung=='R2' else card.pair_id
            groups.setdefault(key,[]).append(card)
        examples=[]
        for group in groups.values():
            if examples and len(examples)>=n:
                break
            for card in group:
                examples.append({'rung':rung,'split':card.split,'pair_id':card.pair_id,
                    'contrast_id':card.contrast_id,'operands':list(card.operands),
                    'prompt':card.prompt,'target':card.answer,
                    'signed_band':scored_band(card.answer)})
        result[rung]=examples
    return result


def encode(qwen, tokenizer, cards, device):
    import torch
    from tools import nanojev_qw5 as qw
    tensors = []
    qwen.backbone.eval()
    with torch.inference_mode():
        for card in cards:
            ids = tokenizer(card.prompt, add_special_tokens=False, return_tensors='pt')['input_ids'].to(device)
            out = qwen.backbone(input_ids=ids, output_hidden_states=True,
                                use_cache=False, return_dict=True)
            tensors.append(torch.stack([out.hidden_states[t][0,-1].float()
                                        for t in qw.QWEN_TAPS]).cpu())
    return torch.stack(tensors)


def batch(cards, h, device):
    import torch
    from tools.nanojev_zero_line_zero_only import input_tensor
    return (input_tensor([(r.operands[0], r.operands[1], r.answer) for r in cards],device),
            h.to(device), torch.tensor([r.answer for r in cards],dtype=torch.long,device=device))


def assess(model, pack, cards):
    import torch
    from tools.nanojev_zero_line_ladder import prediction_values
    operands, states, labels = pack
    model.eval()
    with torch.inference_mode():
        intact = prediction_values(model(operands,states)['answer'])[0]
        zero = prediction_values(model(operands,torch.zeros_like(states))['answer'])[0]
        mismatched = prediction_values(model(operands,states.roll(1,0))['answer'])[0]
        def acc(pred, mask):
            n=int(mask.sum().item())
            return {'n':n,'exact':float((pred[mask]==labels[mask]).float().mean().item()) if n else None}
        masks = {'total':torch.ones_like(labels,dtype=torch.bool),
                 'zero':labels==0,'near_positive':(labels>0)&(labels.abs()<=NEAR_RESIDUAL_LIMIT),
                 'near_negative':(labels<0)&(labels.abs()<=NEAR_RESIDUAL_LIMIT),
                 'far_positive':(labels>NEAR_RESIDUAL_LIMIT),'far_negative':labels< -NEAR_RESIDUAL_LIMIT}
        actual = {k:acc(intact,mask) for k,mask in masks.items()}
        base = acc(zero,masks['total'])['exact']
        mismatch = acc(mismatched,masks['total'])['exact']
        paired={}
        for j,card in enumerate(cards):
            paired.setdefault(card.pair_id, []).append(j)
        if any(len(v)!=2 for v in paired.values()):
            raise AssertionError('QW5_INVARIANCE_PAIR_MISSING')
        agreed=sum(bool(intact[i].item()==intact[j].item()) for i,j in paired.values())
        both_right=sum(bool(intact[i].item()==labels[i].item() and
                            intact[j].item()==labels[j].item()) for i,j in paired.values())
        directional={}
        for j,card in enumerate(cards):
            if card.contrast_id is not None:
                directional.setdefault(card.contrast_id,[]).append(j)
        if directional and any(len(v)!=4 for v in directional.values()):
            raise AssertionError('QW5_INCOMPLETE_DIRECTION_CONTRAST')
        contrast_correct = (sum(all(bool(intact[i].item()==labels[i].item()) for i in group)
                                for group in directional.values())/len(directional) if directional else None)
        scales={}
        if cards and is_r0(cards[0].rung):
            for key in rung_scale_names(cards[0].rung):
                mask=torch.tensor([residual_scale_band(card.answer)==key for card in cards],
                                  device=labels.device,dtype=torch.bool)
                scales[key]=acc(intact,mask)
        return {'residual_scale_groups':scales,
                'directional_contrasts':len(directional),
                'directional_contrast_accuracy':contrast_correct,
                'groups':actual, 'invariant_pairs':len(paired),
                'paired_agreement':agreed/len(paired),
                'paired_invariant_accuracy':both_right/len(paired),
                'zeroed_exact':base,'mismatched_exact':mismatch,
                'conditioning_gain':actual['total']['exact']-base,
                'mismatch_gain':actual['total']['exact']-mismatch}


def pass_gate(now, retained, args, requires_qwen):
    group = now['groups']
    issues=[]
    if group['total']['exact'] < args.min_exact:
        issues.append('exact')
    for key in ('zero','near_positive','near_negative','far_positive','far_negative'):
        if group[key]['n'] < 2 or group[key]['exact'] < args.min_balance:
            issues.append(key)
    if now['paired_invariant_accuracy'] < args.min_balance:
        issues.append('paired_invariance')
    for name,measure in now.get('residual_scale_groups',{}).items():
        if measure['n']<2 or measure['exact']<args.min_balance:
            issues.append('residual_scale:'+name)
    if now.get('directional_contrasts',0) and (now['directional_contrast_accuracy'] < args.min_balance):
        issues.append('directional_contrast')
    if requires_qwen and (now['conditioning_gain'] < args.min_conditioning_gain or
                          now['mismatch_gain'] < args.min_conditioning_gain):
        issues.append('qwen_conditioning')
    for rung, report in retained.items():
        for name,metrics in report['groups'].items():
            if metrics['n'] < 2 or metrics['exact'] < args.min_retention:
                issues.append('retention:'+rung+':'+name)
        if report['paired_invariant_accuracy'] < args.min_retention:
            issues.append('retention:'+rung+':paired_invariance')
        for name,measure in report.get('residual_scale_groups',{}).items():
            if measure['n']<2 or measure['exact']<args.min_retention:
                issues.append('retention:'+rung+':residual_scale:'+name)
        if report.get('directional_contrasts',0) and report['directional_contrast_accuracy'] < args.min_retention:
            issues.append('retention:'+rung+':directional_contrast')
        if rung=='R2' or (rung.startswith('T') and rung not in ('T00',)):
            if (report['conditioning_gain'] < args.min_conditioning_gain or
                    report['mismatch_gain'] < args.min_conditioning_gain):
                issues.append('retention:'+rung+':qwen_conditioning')
    return not issues, issues


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n',encoding='utf8')
    os.replace(tmp,path)


def _write_pt(path, data):
    import torch
    tmp=path.with_suffix(path.suffix+'.tmp')
    torch.save(data,tmp)
    os.replace(tmp,path)


def sha(path):
    from tools.nanojev_qw5 import sha as digest
    return digest(path)


def run(args):
    import torch
    from tools import nanojev_qw5_join as join, nanojev_qw5 as qw
    from tools import nanojev_qwen_hybridagent as hybrid
    if args.steps_per_rung < 1 or args.probe_every < 1:
        raise ValueError('QW5_INVALID_STEP_COUNT')
    for split in ('train','dev','test'):
        count=cards_for_rung(args,'R0c',split)
        if count < 128 or count%2:
            raise ValueError('QW5_R0_CARDS_MUST_BE_EVEN_AT_LEAST_128')
    source = args.qw5_run.resolve()
    output = args.output.resolve()
    if source == output:
        raise ValueError('QW5_MUST_NOT_OVERWRITE_SOURCE_RUN')
    cp_path=source/'latest.pt'
    cfg_path=source/'config.json'
    # Ensure the loaded stage-1 weights and the EMP source remain authentic.
    model, src_cp, src_cfg = join.restore(source, args.device)
    if src_cp['step'] < 1:
        raise RuntimeError('QW5_SOURCE_NOT_TRAINED')
    provenance={'qw5_config_sha256':sha(cfg_path),'qw5_checkpoint_sha256':sha(cp_path),
                'qw5_run':str(source), 'source_step':src_cp['step']}
    warm_source = Path(args.seed_accepted_from).resolve() if args.seed_accepted_from else None
    if warm_source == output:
        raise ValueError('QW5_SEED_CANNOT_EQUAL_OUTPUT')
    warm_provenance = None
    if warm_source is not None:
        if not (warm_source/'config.json').is_file():
            raise FileNotFoundError('QW5_SEED_CONFIG_MISSING')
        warm_provenance={'directory':str(warm_source),
                         'config_sha256':sha(warm_source/'config.json'),
                         'accepted_sha256':sha(warm_source/'accepted.pt'),
                         'ledger_sha256':sha(warm_source/'ledger.json')}
    config={'schema':SCHEMA,'source':provenance,'seed':args.seed,
            'seed_accepted_from':warm_provenance,
            'train_cards':args.train_cards,'dev_cards':args.dev_cards,'test_cards':args.test_cards,
            'r0_train_cards':args.r0_train_cards,'r0_dev_cards':args.r0_dev_cards,
            'r0_test_cards':args.r0_test_cards,
            'r0_far_value_partition':'nonoverlapping_magnitude_by_split_v3',
            'min_exact':args.min_exact,'min_balance':args.min_balance,
            'min_retention':args.min_retention,'min_conditioning_gain':args.min_conditioning_gain,
            'lr':args.lr,'steps_per_rung':args.steps_per_rung,
            'run_order':list(RUNG_ORDER), 'qwen_model':src_cfg['qwen']['model'],
            'qwen_revision':src_cfg['qwen']['revision'],
            'status':'TRAINING_EXPERIMENT_ONLY',
            'hybridlink_feedback':'DISABLED','output_authority':'DISABLED'}
    config_path=output/'config.json'
    if config_path.is_file():
        if json.loads(config_path.read_text('utf8')) != config:
            raise RuntimeError('QW5_LADDER_CONFIG_OR_SOURCE_DRIFT')
        ledger=json.loads((output/'ledger.json').read_text('utf8'))
        committed=torch.load(output/'accepted.pt',map_location='cpu',weights_only=True)
        if committed['schema']!=SCHEMA+'.accepted' or committed['passed']!=ledger['passed']:
            raise RuntimeError('QW5_LADDER_CHECKPOINT_DRIFT')
        with torch.no_grad():
            for name,param in model.named_parameters():
                if param.requires_grad:
                    param.copy_(committed['trainable'][name].to(args.device))
    else:
        if output.exists() and any(output.iterdir()):
            raise FileExistsError('QW5_LADDER_REFUSES_NONEMPTY_OUTPUT')
        output.mkdir(parents=True,exist_ok=True)
        if warm_source is not None:
            ancestor=json.loads((warm_source/'config.json').read_text('utf8'))
            prev_ledger=json.loads((warm_source/'ledger.json').read_text('utf8'))
            prev_cp=torch.load(warm_source/'accepted.pt',map_location='cpu',weights_only=True)
            legacy_order=('Z0','Z1','Z2','B0',*(f'T{i:02d}' for i in range(9)))
            previous_bridge=('Z0','Z1','Z2','R0','R1','R2','B0',*(f'T{i:02d}' for i in range(9)))
            if (ancestor.get('run_order') not in (list(legacy_order),list(previous_bridge)) or
                    ancestor.get('source')!=provenance or
                    ancestor.get('seed')!=args.seed or
                    prev_ledger.get('passed')!=['Z0','Z1','Z2'] or
                    prev_cp.get('passed')!=['Z0','Z1','Z2'] or
                    prev_ledger.get('independent_test_failed')):
                raise RuntimeError('QW5_SEED_NOT_ACCEPTED_Z2_SAME_SOURCE')
            trainable={n:p for n,p in model.named_parameters() if p.requires_grad}
            if set(prev_cp['trainable'])!=set(trainable):
                raise RuntimeError('QW5_SEED_TRAINABLE_SCHEMA_DRIFT')
            with torch.no_grad():
                for name,param in trainable.items():
                    weight=prev_cp['trainable'][name]
                    if tuple(weight.shape)!=tuple(param.shape):
                        raise RuntimeError('QW5_SEED_WEIGHT_SHAPE_DRIFT:'+name)
                    param.copy_(weight.to(args.device))
            passed=['Z0','Z1','Z2']
        else:
            passed=[]
        ledger={'schema':SCHEMA+'.ledger','passed':passed,'attempts':[], 'stopped_at':None}
        _write_json(config_path,config)
        _write_json(output/'ledger.json',ledger)
        _write_pt(output/'accepted.pt',{'schema':SCHEMA+'.accepted','passed':list(passed),
             'trainable':{n:p.detach().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}})
    if ledger.get('independent_test_failed'):
        raise RuntimeError('QW5_INDEPENDENT_TEST_FAILED_NEW_CAMPAIGN_REQUIRED')
    next_index=len(ledger['passed'])
    if next_index == len(RUNG_ORDER):
        print('QW5_LADDER_COMPLETE='+json.dumps({'passed':ledger['passed'],'output':str(output)}));return
    if args.through not in RUNG_ORDER or RUNG_ORDER.index(args.through)<next_index:
        raise ValueError('QW5_INVALID_THROUGH_RUNG')
    # Test cards are never used for optimizer selection or advancement.
    qwen,tok,details=hybrid.load_qwen(src_cfg['qwen']['model'],src_cfg['qwen']['revision'],
                            device=args.device,precision=args.precision,
                            allow_download=args.allow_model_download)
    if details['hidden_size']!=1024:
        raise RuntimeError('QW5_QWEN_WIDTH_CHANGED')
    qwen.eval().requires_grad_(False)
    decks={}
    def get_deck(rung,split):
        key=(rung,split)
        if key not in decks:
            cards=make_cards(rung,args.seed)
            audit_splits(cards)
            count = cards_for_rung(args,rung,split)
            cards=sample_split(cards,split,count,args.seed)
            print(f'QW5_LADDER_ENCODING={rung}:{split}:{len(cards)}',flush=True)
            deck=(cards,batch(cards,encode(qwen,tok,cards,args.device),args.device))
            decks[key]=deck
        return decks[key]
    try:
        for index in range(next_index,RUNG_ORDER.index(args.through)+1):
            rung=RUNG_ORDER[index]
            training=get_deck(rung,'train')[1]
            dev_cards,dev_pack=get_deck(rung,'dev')
            optimizer=torch.optim.AdamW((p for p in model.parameters() if p.requires_grad),lr=args.lr,weight_decay=0.)
            best=None
            for step in range(1,args.steps_per_rung+1):
                model.train()
                ix=torch.randint(len(training[2]),(args.batch_size,),device=args.device)
                output_logits=model(training[0][ix],training[1][ix])
                loss=qw.qw5_loss(output_logits,training[2][ix])
                if not bool(torch.isfinite(loss)):
                    raise RuntimeError('QW5_NONFINITE_LADDER_LOSS')
                optimizer.zero_grad(set_to_none=True);loss.backward()
                torch.nn.utils.clip_grad_norm_((p for p in model.parameters() if p.requires_grad),1.)
                optimizer.step()
                if step%args.probe_every and step!=args.steps_per_rung:
                    continue
                current=assess(model,dev_pack,dev_cards)
                retained={past:assess(model,get_deck(past,'dev')[1],get_deck(past,'dev')[0])
                          for past in ledger['passed']}
                ok,issues=pass_gate(current,retained,args,requires_qwen=rung in ('R2','T01','T02','T03','T04','T05','T06','T07','T08'))
                print('QW5_LADDER_PROBE='+json.dumps({'rung':rung,'step':step,'dev':current,
                      'retention':{r:x['groups']['total']['exact'] for r,x in retained.items()},
                      'gate':'PASS' if ok else 'FAIL','issues':issues},sort_keys=True),flush=True)
                record={'rung':rung,'step':step,'dev':current,'retention':retained,
                        'gate':'PASS' if ok else 'FAIL','issues':issues}
                if best is None or (ok and best['gate']!='PASS') or (ok == (best['gate']=='PASS') and
                  current['groups']['total']['exact']>best['dev']['groups']['total']['exact']):
                    best=record
                if ok:
                    # Independent TEST is evaluated only AFTER DEV promotion.
                    test_cards,test_pack=get_deck(rung,'test')
                    test=assess(model,test_pack,test_cards)
                    record['test']=test
                    test_ok,test_issues=pass_gate(test,{},args,requires_qwen=rung in
                          ('T01','T02','T03','T04','T05','T06','T07','T08'))
                    if not test_ok:
                        # Fail once and lock. Never adjust the same campaign
                        # on the basis of its independent TEST labels.
                        record['gate']='DEV_PASS_TEST_FAIL'
                        record['test_issues']=test_issues
                        ledger['attempts'].append(record)
                        ledger['stopped_at']=rung
                        ledger['independent_test_failed']=True
                        _write_json(output/'ledger.json',ledger)
                        print('QW5_LADDER_TEST_REFUSED='+json.dumps({'rung':rung,
                            'issues':test_issues,'passed':ledger['passed']},sort_keys=True),flush=True)
                        return
                    # Independent TEST passed once; never train to it.
                    ledger['passed'].append(rung)
                    ledger['attempts'].append(record)
                    ledger['stopped_at']=None
                    _write_pt(output/'accepted.pt',{'schema':SCHEMA+'.accepted','passed':list(ledger['passed']),
                        'trainable':{n:p.detach().cpu().clone() for n,p in model.named_parameters() if p.requires_grad}})
                    _write_json(output/'ledger.json',ledger)
                    print('QW5_LADDER_ADVANCED='+json.dumps({'rung':rung,'test':test,
                          'passed':ledger['passed']},sort_keys=True),flush=True)
                    break
            else:
                ledger['attempts'].append(best)
                ledger['stopped_at']=rung
                _write_json(output/'ledger.json',ledger)
                print('QW5_LADDER_STOPPED='+json.dumps({'rung':rung,'passed':ledger['passed'],
                    'best':best,'resume_policy':'starts again from last accepted model'},sort_keys=True),flush=True)
                return
    finally:
        del qwen
    print('QW5_LADDER_RESULT='+json.dumps({'passed':ledger['passed'],'next':
          RUNG_ORDER[len(ledger['passed'])] if len(ledger['passed'])<len(RUNG_ORDER) else None,
          'report':str(output/'ledger.json')},sort_keys=True),flush=True)


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('preview','examples','run','status'))
    p.add_argument('--qw5-run',type=Path, default=Path.home()/'NanoJev'/'runs'/'qw5_phase1_v1')
    p.add_argument('--output',type=Path,default=Path.home()/'NanoJev'/'runs'/'qw5_zero_balance_ladder_v1')
    p.add_argument('--seed',type=int,default=20261027)
    p.add_argument('--seed-accepted-from',type=Path, default=None,
                   help='Optional previous ladder v1 accepted Z0–Z2 checkpoint; new output required')
    p.add_argument('--train-cards',type=int,default=128)
    p.add_argument('--dev-cards',type=int,default=64)
    p.add_argument('--test-cards',type=int,default=64)
    p.add_argument('--r0-train-cards',type=int,default=512,
                   help='Per-R0a/b/c train cards, paired and magnitude-balanced')
    p.add_argument('--r0-dev-cards',type=int,default=128)
    p.add_argument('--r0-test-cards',type=int,default=128)
    p.add_argument('--through',choices=RUNG_ORDER,default='T08')
    p.add_argument('--examples-per-rung',type=int,default=12)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--precision',choices=('auto','float16','float32','bfloat16'),default='auto')
    p.add_argument('--allow-model-download',action='store_true')
    p.add_argument('--steps-per-rung',type=int,default=1000)
    p.add_argument('--probe-every',type=int,default=100)
    p.add_argument('--batch-size',type=int,default=16)
    p.add_argument('--lr',type=float,default=2e-4)
    p.add_argument('--min-exact',type=float,default=.80)
    p.add_argument('--min-balance',type=float,default=.80)
    p.add_argument('--min-retention',type=float,default=.80)
    p.add_argument('--min-conditioning-gain',type=float,default=.02)
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        if args.command=='preview':
            print('QW5_LADDER_PREVIEW='+json.dumps(preview(args),sort_keys=True))
        elif args.command=='examples':
            print('QW5_TRANSITION_EXAMPLES='+json.dumps(example_cards(args),sort_keys=True))
        elif args.command=='status':
            report=json.loads((args.output/'ledger.json').read_text('utf8'))
            print('QW5_LADDER_STATUS='+json.dumps(report,sort_keys=True))
        else:
            run(args)
        return 0
    except (ValueError,RuntimeError,FileNotFoundError,OSError,AssertionError) as e:
        print('QW5_LADDER_REFUSED:'+str(e),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
