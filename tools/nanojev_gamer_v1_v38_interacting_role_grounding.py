#!/usr/bin/env python3
"""V38: interacting-slot Qwen semantic-grounding ablation for two-action payoff games.

No semantic regex/teacher is permitted in inference. A numeric *transcriber*
locates four integer literals and one rational literal; a learned contextual
Qwen probe assigns R,S,T,P and the probability's C-vs-D referent. Its result
is passed to the independently trained V34 numerical decision head.

This is not certified until real CUDA development AND sealed holdout pass.
Tactical-v6 and V32 authority remain untouched. The task remains limited to
explicit two-action payoff descriptions, not arbitrary game theory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from fractions import Fraction
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import re
import tempfile

import numpy as np

from tools import nanojev_gamer_v1_v33_game_learning as v33
from tools import nanojev_gamer_v1_v34_grounded_game_learning as v34

SCHEMA = 'nanojev.gamer.v1.qwen-interacting-role-grounding.v38'
HEAD_SCHEMA = 'nanojev.gamer.v1.qwen-interacting-role-belief-head.v38'
REPORT_SCHEMA = 'nanojev.gamer.v1.interacting-role-audit.v38'
ROLE_NAMES = ('R', 'S', 'T', 'P')
ROLE_CHOICES = ('CC', 'CD', 'DC', 'DD')
TRAIN_VARIANTS = tuple(range(15))
TEST_VARIANTS = tuple(range(15, 20))
TRAIN_PAIRS = 20
TEST_PAIRS = 20
# These thresholds are precommitted, not selected from held-out performance.
DEV_STRUCT_MIN = 108  # out of 120
DEV_DECISION_MIN = 108
DEV_PAIR_MIN = 54  # out of 60 paired counterfactuals
TEST_STRUCT_MIN = 180  # out of 200
TEST_DECISION_MIN = 180
TEST_PAIR_MIN = 90  # out of 100 counterfactuals
PER_VARIANT_MIN = 32  # out of 40

# The scanner recognizes *numeric syntax only*. It never reads CC/CD/DC/DD,
# action names, probability referents, narrative templates, or expected labels.
_LITERAL = re.compile(r'(?<![\w/])([+-]?\d{1,5}(?:/\d{1,5})?)(?![\w/])')

# These templates are used ONLY to make supervised examples and score an audit.
# No template, regex, label, slot map, or oracle is consulted by the predictor.
_TABLES = (
    '{pair}={value}.',
    'Our payoff if we play {ours} while the rival plays {theirs} is {value}.',
    'When we {our_move} and the rival {their_move}, our score is {value}.',
    'For (our {ours}, other {theirs}) our reward is {value}.',
    'Against their {theirs}, our choice {ours} earns us {value}.',
    'If we choose {ours} and our opponent chooses {theirs}, we receive {value} points.',
    'Choosing {ours} when the other chooses {theirs} gives us {value} units.',
    'Our points are {value} when our move is {ours} and the rival move is {theirs}.',
)
_BELIEFS = (
    'The rival chooses {side} with probability {q}; the other choice takes the remainder.',
    'We assign probability {q} to the opposing player taking action {side}.',
    'The probability the opponent {verb} is {q}; otherwise they take the other action.',
    'Our forecast says their chance of {noun} equals {q}, with the rest for the alternative.',
    'Their chance of choosing {side} is {q}, and of choosing the opposite action is the remainder.',
    'We think {q} of encounters have them {verb}; in the others they choose differently.',
    'In {q} of the encounters the opposing player {verb}, not the opposite move.',
    'Their probability of {noun} is {q}; the remaining probability is for the other action.',
)
_CONTEXTS = (
    'An expedition negotiates passage; each player chooses C (Yield) or D (Contest).',
    'At a supply checkpoint each side selects C (cooperate) or D (defect).',
    'Two traders bargain over scarce material, with C (share) and D (claim).',
    'A ship chooses between C (deescalate) and D (challenge).',
    'Two survey teams face each other and can select C (Yield) or D (Contest).',
    'At a traffic merge two drivers can choose C (Yield) or D (Contest).',
    'Two autonomous agents select C (cooperate) or D (defect) in a new task.',
    'At a remote gate the available moves are C (Yield) and D (Contest).',
)
_VARIANTS = (
    # 15 training-family combinations: same roles under diverse shuffled orders.
    (0, 0), (1, 1), (2, 2), (3, 3), (4, 0),
    (0, 2), (1, 3), (2, 0), (3, 1), (4, 2),
    (0, 3), (1, 0), (2, 1), (3, 2), (4, 3),
    # Five completely withheld *surface families*.
    (5, 4), (6, 5), (7, 6), (5, 7), (7, 4),
)


def require(ok, code):
    if not ok:
        raise ValueError('GAMER_V38_' + code)


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.exists(), 'REFUSE_OVERWRITE_' + path.name)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', prefix='.v38-',
                                     dir=path.parent, delete=False) as fh:
        json.dump(obj, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write('\n')
        tmp = Path(fh.name)
    try:
        os.link(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def transcribe(prompt):
    """Literal digit transcription, never semantic assignment or arithmetic."""
    require(isinstance(prompt, str) and 1 <= len(prompt) <= 4000, 'INVALID_PROMPT')
    found = list(_LITERAL.finditer(prompt))
    require(len(found) == 5, 'FIVE_NUMERIC_LITERALS_REQUIRED')
    ints, fractions = [], []
    for m in found:
        token = m.group(1)
        start, end = m.span(1)
        if '/' in token:
            try:
                value = Fraction(token)
            except (ValueError, ZeroDivisionError) as e:
                raise ValueError('GAMER_V38_INVALID_FRACTION') from e
            require(0 < value < 1, 'PROBABILITY_RANGE')
            fractions.append({'value': value, 'span': (start, end), 'text': token})
        else:
            val = int(token)
            require(-1000 <= val <= 1000, 'PAYOFF_RANGE')
            ints.append({'value': val, 'span': (start, end), 'text': token})
    require(len(ints) == 4 and len(fractions) == 1, 'FOUR_SCORES_ONE_FRACTION_REQUIRED')
    return ints, fractions[0]


def _surface(pair, value, style):
    ours, theirs = pair
    names = {'C': 'Yield', 'D': 'Contest'}
    return _TABLES[style].format(pair=pair, value=value, ours=ours,
             theirs=theirs, our_move=names[ours], their_move=names[theirs])


def _belief(side, q, style):
    verbs = {'C': 'cooperates', 'D': 'defects'}
    nouns = {'C': 'cooperation', 'D': 'defection'}
    return _BELIEFS[style].format(side=side, q=str(q), verb=verbs[side], noun=nouns[side])


def make_deck(split, seed):
    """Minimal pairs where *numeric literals are identical* but meaning changes.

    Two independent interventions:
      - belief: rival's 3/4 refers to C versus D;
      - role: the same numeral slots denote different payoff outcomes.
    Never calls test construction while fitting a model.
    """
    require(split in ('fit', 'holdout'), 'INVALID_SPLIT')
    variants = TRAIN_VARIANTS if split == 'fit' else TEST_VARIANTS
    rows = []
    n_pairs = TRAIN_PAIRS if split == 'fit' else TEST_PAIRS
    for variant in variants:
        table_style, belief_style = _VARIANTS[variant]
        for k in range(n_pairs):
            rng = random.Random(f'v35-{seed}-{split}-{variant}-{k}')
            intervention = 'belief' if k % 2 == 0 else 'role'
            while True:
                values = dict(zip(ROLE_CHOICES, rng.sample(range(-12, 13), 4)))
                # Every third pair is constructed to reverse; the rest have
                # independent asymmetric payoffs and can be non-reversing.
                if k % 3 == 0:
                    values['CC'] = rng.randint(-8, 8)
                    values['CD'] = rng.randint(-8, 8)
                    delta = rng.randint(3, 10) * (1 if rng.randrange(2) else -1)
                    values['DC'] = values['CC'] + delta
                    values['DD'] = values['CD'] - delta
                order = list(ROLE_CHOICES)
                rng.shuffle(order)
                swapped = [('DC' if role == 'CC' else 'CC' if role == 'DC' else role)
                           for role in order]
                # Ordered numbers stay in the same slots for *both* prompts.
                literal_values = [values[role] for role in order]
                if intervention == 'belief':
                    role_orders = (order, order)
                    sides = ('C', 'D')
                else:
                    role_orders = (order, swapped)
                    sides = ('C', 'C')
                truth = []
                for role_order,side in zip(role_orders,sides):
                    by_role = dict(zip(role_order,literal_values))
                    q_c = Fraction(3, 4) if side == 'C' else Fraction(1, 4)
                    margin = q_c*(by_role['DC']-by_role['CC'])+(1-q_c)*(by_role['DD']-by_role['CD'])
                    truth.append(margin)
                if all(value != 0 for value in truth) and (k%3 != 0 or (truth[0]>0)!=(truth[1]>0)):
                    break
            ctx = _CONTEXTS[(variant + k) % len(_CONTEXTS)]
            for choice,(role_order,side) in enumerate(zip(role_orders,sides)):
                clauses = ' '.join(_surface(role, literal, table_style)
                                   for role,literal in zip(role_order,literal_values))
                prompt = (f'{ctx} Our points by outcome: {clauses} '
                          f'{_belief(side,Fraction(3,4),belief_style)} '
                          f'Which choice maximizes expected points: C or D?')
                rows.append({'id': f'{split}.v{variant:02}.p{k:02}.{choice}',
                             'pair': f'{split}.v{variant:02}.p{k:02}',
                             'variant': variant, 'prompt': prompt,
                             'roles': [ROLE_CHOICES.index(role) for role in role_order],
                             'q_side': 0 if side == 'C' else 1,
                             'label': int(truth[choice] > 0),
                             'intervention': intervention,
                             'family': 'forced_reversal' if k%3==0 else 'asymmetric'})
    require(len(rows) == len(variants)*2*n_pairs
            and len({r['prompt'] for r in rows}) == len(rows), 'DATASET_INTEGRITY')
    for a,b in zip(rows[::2],rows[1::2]):
        require(a['pair']==b['pair'] and a['intervention']==b['intervention'], 'PAIR_INTEGRITY')
        ia,qa=transcribe(a['prompt'])
        ib,qb=transcribe(b['prompt'])
        require([c['value'] for c in ia]==[c['value'] for c in ib]
                and qa['value']==qb['value'], 'CONTRAST_NOT_NUMERICALLY_IDENTICAL')
        if a['intervention']=='belief':
            require(a['roles']==b['roles'] and a['q_side']!=b['q_side'], 'BELIEF_PAIR_FAILED')
        else:
            require(a['roles']!=b['roles'] and a['q_side']==b['q_side'], 'ROLE_PAIR_FAILED')
        if a['family']=='forced_reversal':
            require(a['label']!=b['label'], 'REVERSAL_NOT_REAL')
    return rows


def fit_partition(rows):
    fit, dev = [], []
    for row in rows:
        pair_number = int(row['pair'].rsplit('.p',1)[1])
        (fit if pair_number < 16 else dev).append(row)
    require(len(fit) == 480 and len(dev) == 120
            and {r['pair'] for r in fit}.isdisjoint({r['pair'] for r in dev}),
            'FIT_DEV_LEAK')
    return fit,dev


def _last_valid_token(attention_mask):
    import torch
    # Supports left OR right padding, unlike selecting sum(mask)-1.
    idx = torch.arange(attention_mask.shape[1], device=attention_mask.device)
    return (attention_mask.long() * idx.unsqueeze(0)).max(1).values


def complete_clause_end(prompt, span_end):
    """End of enclosing numeric proposition using punctuation, never word roles.

    This selector cannot inspect action names, CC/CD/DC/DD, teaching labels,
    or payoff semantics. It merely identifies when Qwen has seen the entire
    proposition, replacing V35's arbitrary fixed +16 token lookahead.
    """
    require(isinstance(prompt, str) and 0 < span_end <= len(prompt),
            'INVALID_CLAUSE_INPUT')
    trailing = re.search(r'[.;](?=\s|$)', prompt[span_end:])
    require(trailing is not None, 'UNTERMINATED_NUMERIC_CLAUSE')
    return span_end + trailing.end()


def clause_token_index(prompt, span, token_offsets, attention, numeric_indices):
    """Final active Qwen token within the number's own clause, no farther."""
    endpoint = complete_clause_end(prompt, span['span'][1])
    positions = [j for j, (start, stop) in enumerate(token_offsets)
                 if stop > start and stop <= endpoint and bool(attention[j])]
    require(bool(positions), 'CLAUSE_TOKENS_MISSING')
    end = max(positions)
    require(end >= max(numeric_indices), 'CLAUSE_END_PRECEDES_NUMBER')
    return end


def capture(model, tok, torch, prompts, *, device='cuda', batch_size=4, tag='capture'):
    """Qwen hidden states from actual numeral spans, with global context.

    Does not inspect game-role strings, map CC/CD/DC/DD, or read target labels.
    """
    require(len(prompts) > 0 and 1 <= batch_size <= 16, 'CAPTURE_ARGUMENTS')
    int_items, belief_items = [], []
    for start in range(0,len(prompts),batch_size):
        batch = prompts[start:start+batch_size]
        spans = [transcribe(p) for p in batch]
        enc = tok(batch, return_tensors='pt', padding=True, truncation=False,
                  return_offsets_mapping=True, add_special_tokens=True)
        require('offset_mapping' in enc and 'attention_mask' in enc, 'FAST_TOKENIZER_OFFSETS_REQUIRED')
        offsets = enc.pop('offset_mapping').cpu().tolist()
        attn_cpu = enc['attention_mask'].cpu()
        require(bool((attn_cpu.sum(1) <= 512).all()), 'CONTEXT_TOO_LONG')
        inputs = {k:v.to(device) for k,v in enc.items()}
        with torch.inference_mode():
            output = model(**inputs, output_hidden_states=True, use_cache=False)
            states = output.hidden_states
            require(len(states) >= 20, 'QWEN_LAYERS_UNAVAILABLE')
            last = _last_valid_token(inputs['attention_mask'])
            full = torch.nn.functional.normalize(states[18][torch.arange(len(batch),device=device),last,:].float(),dim=-1)
            for b,(int_spans,q_span) in enumerate(spans):
                def span_rep(span):
                    left,right = span['span']
                    ids = [j for j,(a,z) in enumerate(offsets[b])
                           if z>a and a<right and z>left and bool(attn_cpu[b,j])]
                    require(bool(ids), 'NUMERIC_TOKENS_NOT_ALIGNED')
                    h9 = states[9][b,ids,:].float().mean(0)
                    h18 = states[18][b,ids,:].float().mean(0)
                    # V35's arbitrary lookahead can cross into the next
                    # payoff clause and mix numerical-role evidence. The
                    # clause endpoint includes all words describing this
                    # number but none from the following payoff statement.
                    clause_end = clause_token_index(batch[b], span, offsets[b],
                                                     attn_cpu[b].tolist(), ids)
                    post = states[18][b,clause_end,:].float()
                    return torch.cat((torch.nn.functional.normalize(h9,dim=0),
                                      torch.nn.functional.normalize(h18,dim=0),
                                      torch.nn.functional.normalize(post,dim=0),
                                      full[b]),dim=0).cpu().numpy().astype(np.float32)
                int_items.append(np.stack([span_rep(s) for s in int_spans]))
                belief_items.append(span_rep(q_span))
        if start+len(batch) == len(prompts) or (start+len(batch)) % 32 == 0:
            print(f'GAMER_V38_CAPTURE {tag} {min(start+len(batch),len(prompts))}/{len(prompts)}',flush=True)
    return np.stack(int_items), np.stack(belief_items)


def head(torch, width):
    """Interpret all four Qwen clause representations jointly.

    The V37 additive role scorer could only see each payoff clause in
    isolation: changing another clause could not change a slot's logits.
    Here self-attention over *four* clauses permits such cross-clause
    interactions. There are no slot-position embeddings and no strings,
    lexical role features, oracle labels, or payoff values at this boundary.
    Training still uses the same full-permutation loss as V37.
    """
    class SemanticHead(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.slot = torch.nn.Sequential(
                torch.nn.LayerNorm(width),
                torch.nn.Linear(width, 96),
                torch.nn.GELU(),
                torch.nn.LayerNorm(96),
            )
            # No positional embeddings: reordering the four payoff clauses
            # reorders their predicted roles, rather than changing their
            # semantic meaning through a hidden index shortcut.
            self.cross = torch.nn.MultiheadAttention(
                embed_dim=96, num_heads=4, dropout=0.0, batch_first=True)
            self.fused = torch.nn.LayerNorm(96)
            self.role = torch.nn.Sequential(
                torch.nn.LayerNorm(192),
                torch.nn.Linear(192, 128),
                torch.nn.GELU(),
                torch.nn.Linear(128, 4))
            # Exactly the previously accepted probability-referent head.
            self.belief = torch.nn.Sequential(torch.nn.LayerNorm(width),
                    torch.nn.Linear(width, 128), torch.nn.GELU(),
                    torch.nn.Linear(128, 2))

        def forward(self, ints, fractions):
            require(ints.ndim == 3 and ints.shape[1] == 4 and
                    ints.shape[2] == fractions.shape[-1] and
                    fractions.shape[0] == ints.shape[0],
                    'JOINT_INPUT_SHAPE')
            z = self.slot(ints)
            attended, _ = self.cross(z, z, z, need_weights=False)
            contextual = self.fused(z + attended)
            role_logits = self.role(torch.cat((z, contextual), dim=-1))
            return role_logits, self.belief(fractions)
    return SemanticHead()


_PERMUTATIONS = tuple(itertools.permutations(range(4)))
_PERMUTATION_INDEX = {perm: idx for idx, perm in enumerate(_PERMUTATIONS)}


def permutation_logits(role_logits):
    """24 possible full assignments, exactly matching assign_roles().

    Uses only four learned per-number role logits. At inference it receives no
    action label, true roles, prompt text, or payoff oracle output.
    """
    import torch
    require(role_logits.ndim == 3 and tuple(role_logits.shape[1:]) == (4, 4),
            'ROLE_LOGIT_SHAPE')
    p = torch.as_tensor(_PERMUTATIONS, device=role_logits.device, dtype=torch.long)
    slots = torch.arange(4, device=role_logits.device).view(1, 4)
    return role_logits[:, slots, p].sum(-1)



def assign_roles(logits):
    """Learned logits plus only the structural one-of-each-role constraint."""
    require(logits.shape[1:] == (4,4), 'ROLE_LOGIT_SHAPE')
    return [max(_PERMUTATIONS,key=lambda perm: sum(float(row[i,perm[i]]) for i in range(4)))
            for row in logits]


def predict_slots(net, x, q):
    import torch
    net.eval()
    with torch.inference_mode():
        logits, side = net(torch.from_numpy(x.copy()).float(),
                           torch.from_numpy(q.copy()).float())
    return assign_roles(logits.cpu().numpy()), side.argmax(-1).cpu().tolist()


def numeric_input(prompt, roles, q_side):
    """No teacher or role regex; consumes learned 4-role permutation + side."""
    ints, q = transcribe(prompt)
    require(sorted(roles) == [0,1,2,3] and q_side in (0,1), 'INVALID_LEARNED_ASSIGNMENT')
    canonical_nums = [0] * 4
    for span,role in zip(ints,roles):
        canonical_nums[role] = span['value']
    p = q['value'] if q_side == 0 else 1-q['value']
    return np.array([n/20. for n in canonical_nums] + [float((p-Fraction(1,2))*2)],dtype=np.float32)


def run_decisions(v34_model, rows, roles, sides):
    # This is the existing TRAIN-fitted V34 head, not an oracle action formula.
    x=np.stack([numeric_input(row['prompt'], r, s)
                for row,r,s in zip(rows,roles,sides)])
    return v34.predict(v34_model,x)


def scores(rows, roles, sides, decisions):
    require(len(rows)==len(roles)==len(sides)==len(decisions), 'SCORE_LENGTH')
    structural = [tuple(r)==tuple(item['roles']) and s==item['q_side']
                  for item,r,s in zip(rows,roles,sides)]
    decision = [d==item['label'] for item,d in zip(rows,decisions)]
    pairs = [(a,b, i) for i,(a,b) in enumerate(zip(range(0,len(rows),2),range(1,len(rows),2)))]
    require(all(rows[a]['pair']==rows[b]['pair'] for a,b,_ in pairs), 'SCORE_PAIR_ORDER')
    pair_exact = sum(decision[a] and decision[b] for a,b,_ in pairs)
    contrast_pairs = [(a,b) for a,b,_ in pairs if rows[a]['label'] != rows[b]['label']]
    contrast_exact = sum(decision[a] and decision[b] for a,b in contrast_pairs)
    # With identical numbers in each pair, a purely numeric model must give
    # the same answer twice. For every true reversal at least one is wrong.
    identical_numeric_ceiling=len(rows)-len(contrast_pairs)
    return {'structural_exact':sum(structural), 'decision_exact':sum(decision),
            'numeric_only_accuracy_ceiling':identical_numeric_ceiling,
            'total':len(rows), 'pairs_exact':pair_exact, 'pairs_total':len(pairs),
            'reversal_pairs_exact':contrast_exact,'reversal_pairs_total':len(contrast_pairs),
            'role_slot_exact':sum(r==x for row,p in zip(rows,roles) for r,x in zip(row['roles'],p)),
            'role_slot_total':4*len(rows),
            'belief_exact':sum(s==row['q_side'] for row,s in zip(rows,sides))}


def fit_head(x,q,rows,dx,dq,dev_rows, v34_model, *, epochs=120, seed=35):
    import torch
    require(epochs>0 and x.shape[0]==len(rows)==480 and dx.shape[0]==len(dev_rows)==120
            and x.shape[1]==dx.shape[1]==4 and q.shape[1]==dq.shape[1]==x.shape[2],
            'INVALID_TRAIN_CONFIG')
    torch.manual_seed(seed)
    net = head(torch,x.shape[-1])
    optimizer = torch.optim.AdamW(net.parameters(),lr=.001,weight_decay=.015)
    xt,qt = torch.from_numpy(x.copy()),torch.from_numpy(q.copy())
    # Supervision is the complete role permutation; learned cross-slot
    # attention (not merely this loss) makes each slot depend on the others.
    # The same 24-way assignment used by inference is optimized during fitting.
    perm_targets=torch.tensor([_PERMUTATION_INDEX[tuple(r['roles'])] for r in rows],
                              dtype=torch.long)
    side_targets=torch.tensor([r['q_side'] for r in rows],dtype=torch.long)
    best=-1; saved=None; history=[]; best_preds=None
    for epoch in range(1,epochs+1):
        net.train()
        order=torch.randperm(len(rows),generator=torch.Generator().manual_seed(seed*10000+epoch))
        for inds in order.split(32):
            optimizer.zero_grad(set_to_none=True)
            role_logits,side_logits=net(xt[inds],qt[inds])
            loss=(torch.nn.functional.cross_entropy(permutation_logits(role_logits),perm_targets[inds])
                  +torch.nn.functional.cross_entropy(side_logits,side_targets[inds]))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(net.parameters(),2.0)
            optimizer.step()
        roles,sides=predict_slots(net,dx,dq)
        decisions=run_decisions(v34_model,dev_rows,roles,sides)
        score=scores(dev_rows,roles,sides,decisions)
        # Structural grounding takes precedence over numerical downstream score.
        ranking=(score['structural_exact'],score['decision_exact'],score['pairs_exact'])
        history.append({'epoch':epoch,**score})
        if best == -1 or ranking > best:
            best=ranking
            saved={k:v.detach().cpu().clone() for k,v in net.state_dict().items()}
            best_preds=(roles,sides,decisions)
        if epoch%20==0 or epoch==epochs:
            print(f'GAMER_V38_EPOCH {epoch}/{epochs} semantic={score["structural_exact"]}/120 decision={score["decision_exact"]}/120',flush=True)
    return saved,history,best,best_preds


def checkpoint(root):
    return Path(root)/'semantic_head.pt'


def _load_v34(v34_root,project):
    """Load certified V34 weights without invoking its label-generating replay.

    The original V34 load_head() regenerates train and test oracle labels for
    provenance. This route checks exact saved fingerprints instead, so *no*
    teaching/evaluation oracle runs while a new prompt is being answered.
    """
    import torch
    root=Path(v34_root)
    manifest=v34.read(root/'train_manifest.json')
    train_receipt=v34.read(root/'train_result.json')
    report=v34.read(root/'holdout_acceptance.json')
    require(manifest.get('schema')==v34.SCHEMA and
            manifest.get('head_schema')==v34.HEAD_SCHEMA and
            manifest.get('grounding')=='prompt-only-raw-R-S-T-P-q' and
            manifest.get('oracle_inference') is False and
            manifest.get('dev_pass') is True and
            manifest.get('head_only') is True and
            manifest.get('frozen_qwen_feature_used') is False and
            report.get('status')=='PASS_SEALED_HOLDOUT_SHADOW_ONLY' and
            report.get('heldout_pass') is True, 'NUMERICAL_BASELINE_NOT_ACCEPTED')
    require(sha(v34.checkpoint(root))==train_receipt.get('checkpoint_sha256')==report.get('head_checkpoint_sha256')
            and sha(root/'train_manifest.json')==report.get('manifest_sha256')
            and sha(root/'holdout_predictions.json')==report.get('predictions_sha256'),
            'NUMERICAL_BASELINE_FINGERPRINT_CHANGED')
    v33.verify_activation(project,manifest['lineage'])
    data=torch.load(v34.checkpoint(root),weights_only=True,map_location='cpu')
    require(data['schema']==v34.HEAD_SCHEMA and
            data['manifest_sha256']==v34.hashbytes(v34.canonical(manifest).encode()),
            'NUMERICAL_BASELINE_CHECKPOINT_MISMATCH')
    net=v34.head(torch)
    net.load_state_dict(data['state'],strict=True)
    net.eval()
    return manifest,net


def train(root, *, project='.', v34_root, device='cuda', seed=35, epochs=120,batch_size=4):
    import torch
    root=Path(root)
    require(not root.exists(),'OUTPUT_EXISTS')
    lineage=v33.activation(project)
    v34_manifest,numeric_model=_load_v34(v34_root,project)
    rows,dev_rows=fit_partition(make_deck('fit',seed))
    root.mkdir(parents=True,exist_ok=False)
    t,tok,model=v33.model_context(lineage['model_id'],device)
    try:
        x,q=capture(model,tok,t,[r['prompt'] for r in rows],device=device,batch_size=batch_size,tag='train')
        dx,dq=capture(model,tok,t,[r['prompt'] for r in dev_rows],device=device,batch_size=batch_size,tag='dev')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    state,history,best,dev_pred=fit_head(x,q,rows,dx,dq,dev_rows,numeric_model,epochs=epochs,seed=seed)
    measure=scores(dev_rows,*dev_pred)
    # DEV-only diagnostic: ground truth semantic slots go only to a separate
    # comparison, never into inference, fit gradients, or gate selection.
    gold_roles=[tuple(row['roles']) for row in dev_rows]
    gold_sides=[row['q_side'] for row in dev_rows]
    gold_decisions=run_decisions(numeric_model,dev_rows,gold_roles,gold_sides)
    gold_numeric_dev_exact=sum(p==row['label'] for p,row in zip(gold_decisions,dev_rows))
    dev_roles,dev_sides,dev_actions=dev_pred
    failure_rows=[]
    for row,pred_roles,pred_side,pred_action,gold_decision in zip(
            dev_rows,dev_roles,dev_sides,dev_actions,gold_decisions):
        if (tuple(pred_roles)!=tuple(row['roles']) or pred_side!=row['q_side']
                or pred_action!=row['label']):
            failure_rows.append({'id':row['id'],'variant':row['variant'],
                                 'intervention':row['intervention'],
                                 'true_roles':row['roles'],
                                 'predicted_roles':list(pred_roles),
                                 'true_belief_side':row['q_side'],
                                 'predicted_belief_side':pred_side,
                                 'true_action':row['label'],
                                 'predicted_action':pred_action,
                                 'gold_semantic_numeric_action':gold_decision})
    dev_pass=(measure['structural_exact']>=DEV_STRUCT_MIN and
              measure['decision_exact']>=DEV_DECISION_MIN and measure['pairs_exact']>=DEV_PAIR_MIN
              and measure['decision_exact']>measure['numeric_only_accuracy_ceiling'])
    v33.verify_activation(project,lineage)
    write_new(root/'dev_failure_diagnostics.json',{
        'scope':'DEV_ONLY_NO_TEST_NO_PROMOTION',
        'feature_extraction':'v36-clause-end-v38-cross-slot-attention',
        'v34_with_gold_dev_semantics_exact':gold_numeric_dev_exact,
        'v34_with_gold_dev_semantics_total':len(dev_rows),
        'failure_count':len(failure_rows),'failures':failure_rows})
    write_new(root/'training_data_provenance.json',{
        'fit_hash':digest(rows),'dev_hash':digest(dev_rows),
        'fit_count':len(rows),'dev_count':len(dev_rows),
        'train_variants':list(TRAIN_VARIANTS)})
    manifest={'schema':SCHEMA,'head_schema':HEAD_SCHEMA,'status':'TRAIN_NO_TEST_NO_PROMOTION',
              'model_id':lineage['model_id'],'lineage':lineage,'seed':seed,'best':best,
              'feature_width':int(x.shape[-1]),'epochs':epochs,
              'context_rule':'causal-layer18-at-clause-end-punctuation-only',
              'role_training_rule':'equivariant-cross-slot-attention-joint-24-permutation-ce',
              'dev_diagnostics_sha256':sha(root/'dev_failure_diagnostics.json'),
              'fit_hash':digest(rows),'dev_hash':digest(dev_rows),
              'training_provenance_sha256':sha(root/'training_data_provenance.json'),
              'numeric_head_sha256':sha(v34.checkpoint(v34_root)),
              'numeric_manifest_sha256':sha(Path(v34_root)/'train_manifest.json'),
              'v34_root':str(Path(v34_root).resolve()),
              'train_variants':list(TRAIN_VARIANTS),'sealed_variants':list(TEST_VARIANTS),
              'dev_pass':dev_pass,'dev_scores':measure,
              'frozen_qwen_used':True,'oracle_prediction':False,
              'numerical_semantic_parser_used':False,'tactical_authority_changed':False}
    torch.save({'schema':HEAD_SCHEMA,'manifest_sha256':digest(manifest),
                'state':state},checkpoint(root))
    write_new(root/'train_manifest.json',manifest)
    write_new(root/'train_history.json',history)
    result={'status':'PASS_DEV_HOLDOUT_STILL_SEALED' if dev_pass else 'FAIL_DEV_HOLDOUT_STILL_SEALED',
            'dev':measure,'checkpoint_sha256':sha(checkpoint(root)),'holdout_evaluated':False,
            'production_modified':False}
    write_new(root/'train_result.json',result)
    return result


def load_head(root,project,*,audit_required=False):
    import torch
    root=Path(root)
    manifest=read(root/'train_manifest.json')
    require(manifest['schema']==SCHEMA and manifest['head_schema']==HEAD_SCHEMA
            and manifest['status']=='TRAIN_NO_TEST_NO_PROMOTION'
            and manifest['frozen_qwen_used'] is True and manifest['oracle_prediction'] is False
            and manifest['numerical_semantic_parser_used'] is False
            and manifest['train_variants']==list(TRAIN_VARIANTS)
            and manifest['sealed_variants']==list(TEST_VARIANTS), 'BAD_MANIFEST')
    require(sha(checkpoint(root))==read(root/'train_result.json')['checkpoint_sha256'], 'CHECKPOINT_CHANGED')
    require(manifest.get('context_rule')=='causal-layer18-at-clause-end-punctuation-only' and
            manifest.get('role_training_rule')=='equivariant-cross-slot-attention-joint-24-permutation-ce' and
            sha(root/'dev_failure_diagnostics.json')==manifest.get('dev_diagnostics_sha256'),
            'DEV_DIAGNOSTICS_CHANGED')
    require(sha(root/'training_data_provenance.json')==manifest['training_provenance_sha256'],
            'TRAIN_PROVENANCE_CHANGED')
    provenance=read(root/'training_data_provenance.json')
    require(provenance=={'fit_hash':manifest['fit_hash'],'dev_hash':manifest['dev_hash'],
            'fit_count':480,'dev_count':120,'train_variants':list(TRAIN_VARIANTS)},
            'TRAIN_DECK_PROVENANCE_INVALID')
    v33.verify_activation(project,manifest['lineage'])
    v34root=manifest['v34_root']
    require(sha(v34.checkpoint(v34root))==manifest['numeric_head_sha256'] and
            sha(Path(v34root)/'train_manifest.json')==manifest['numeric_manifest_sha256'],
            'NUMERIC_HEAD_CHANGED')
    checkpoint_data=torch.load(checkpoint(root),map_location='cpu',weights_only=True)
    require(checkpoint_data['schema']==HEAD_SCHEMA and
            checkpoint_data['manifest_sha256']==digest(manifest),'CHECKPOINT_MANIFEST_CHANGED')
    model=head(torch,manifest['feature_width'])
    model.load_state_dict(checkpoint_data['state'],strict=True)
    model.eval()
    if audit_required:
        report=read(root/'holdout_acceptance.json')
        require(report['schema']==REPORT_SCHEMA and
                report['status']=='PASS_V38_SEMANTIC_SHADOW_ONLY' and
                report['passed'] is True and
                report['head_sha256']==sha(checkpoint(root)), 'NOT_CERTIFIED')
        ledger_path=root/'holdout_predictions.json'
        features_path=root/'holdout_features.npz'
        require(sha(ledger_path)==report['predictions_sha256'] and
                sha(features_path)==report['features_sha256'], 'AUDIT_ARTIFACT_CHANGED')
        # Independently rerun the saved checkpoint, instead of trusting JSON
        # predictions that might themselves be made up or copied from labels.
        with np.load(features_path,allow_pickle=False) as data:
            role_x=np.array(data['role_features'],dtype=np.float32)
            belief_x=np.array(data['belief_features'],dtype=np.float32)
        # Stored sealed audit deck, not a newly generated teacher call.
        rows=read(root/'holdout_deck.json')
        require(sha(root/'holdout_deck.json')==report['holdout_deck_file_sha256']
                and digest(rows)==report['holdout_deck_sha256']
                and len(rows)==200 and {r['variant'] for r in rows}==set(TEST_VARIANTS),
                'HOLDOUT_DECK_CHANGED')
        require(role_x.shape==(len(rows),4,manifest['feature_width']) and
                belief_x.shape==(len(rows),manifest['feature_width']) and
                bool(np.isfinite(role_x).all()) and bool(np.isfinite(belief_x).all()),
                'BAD_AUDIT_FEATURES')
        roles,sides=predict_slots(model,role_x,belief_x)
        _,numeric=_load_v34(v34root,project)
        decisions=run_decisions(numeric,rows,roles,sides)
        evidence=read(ledger_path)
        require(len(evidence)==len(rows), 'AUDIT_LEDGER_LENGTH')
        for row,role,side,decision,record in zip(rows,roles,sides,decisions,evidence):
            require(record=={'id':row['id'],
                    'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
                    'roles':list(role),'q_side':side,'decision':decision,
                    'truth':row['label']}, 'PREDICTION_CHECKPOINT_REPLAY_MISMATCH')
        stat=scores(rows,roles,sides,decisions)
        require(all(report.get(field)==value for field,value in stat.items())
                and report['holdout_deck_sha256']==digest(rows)
                and report['numeric_head_sha256']==sha(v34.checkpoint(v34root)),
                'AUDIT_REPORT_INCONSISTENT')
    return manifest,model


def audit(root, *,project='.',device='cuda',batch_size=4):
    root=Path(root)
    require(not (root/'holdout_consumed.json').exists() and
            not (root/'holdout_acceptance.json').exists(),'HOLDOUT_ALREADY_OPENED')
    manifest,net=load_head(root,project)
    require(manifest['dev_pass'] is True,'DEV_GATE_FAILED_HOLDOUT_SEALED')
    write_new(root/'holdout_consumed.json',{'status':'CONSUMED_NO_RETRY',
              'manifest_sha256':sha(root/'train_manifest.json'),
              'head_sha256':sha(checkpoint(root))})
    # Sealed deck is first constructed AFTER irrevocable consumption marker.
    rows=make_deck('holdout',manifest['seed'])
    write_new(root/'holdout_deck.json',rows)
    numeric_manifest,numeric_model=_load_v34(manifest['v34_root'],project)
    import torch
    t,tok,model=v33.model_context(manifest['model_id'],device)
    try:
        x,q=capture(model,tok,t,[r['prompt'] for r in rows],device=device,batch_size=batch_size,tag='holdout')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    # Record frozen-Qwen features for independent checkpoint replay.
    with (root/'holdout_features.npz').open('xb') as out:
        np.savez_compressed(out,role_features=x,belief_features=q)
    roles,sides=predict_slots(net,x,q)
    decisions=run_decisions(numeric_model,rows,roles,sides)
    stats=scores(rows,roles,sides,decisions)
    by_variant={str(v): {'structural_exact':sum(tuple(r)==tuple(row['roles']) and s==row['q_side']
                         for row,r,s in zip(rows,roles,sides) if row['variant']==v),
                         'decision_exact':sum(d==row['label'] for row,d in zip(rows,decisions)
                         if row['variant']==v), 'total':40} for v in TEST_VARIANTS}
    numeric_only_baseline=sum(row['label']==0 for row in rows)
    passed=(stats['structural_exact']>=TEST_STRUCT_MIN and
            stats['decision_exact']>=TEST_DECISION_MIN and
            stats['pairs_exact']>=TEST_PAIR_MIN and
            stats['decision_exact']>stats['numeric_only_accuracy_ceiling'] and
            stats['reversal_pairs_total']>=50 and
            all(record['structural_exact']>=PER_VARIANT_MIN and
                record['decision_exact']>=PER_VARIANT_MIN for record in by_variant.values()) and
            stats['decision_exact']>numeric_only_baseline)
    ledger=[{'id':row['id'],'prompt_sha256':hashlib.sha256(row['prompt'].encode()).hexdigest(),
             'roles':list(r),'q_side':s,'decision':d,'truth':row['label']}
            for row,r,s,d in zip(rows,roles,sides,decisions)]
    write_new(root/'holdout_predictions.json',ledger)
    report={'schema':REPORT_SCHEMA,'status':'PASS_V38_SEMANTIC_SHADOW_ONLY' if passed
            else 'FAIL_V38_SEMANTIC_SHADOW_ONLY','passed':passed,**stats,
            'per_variant':by_variant,'constant_baseline':numeric_only_baseline,
            'head_sha256':sha(checkpoint(root)),
            'numeric_head_sha256':sha(v34.checkpoint(manifest['v34_root'])),
            'predictions_sha256':sha(root/'holdout_predictions.json'),
            'features_sha256':sha(root/'holdout_features.npz'),
            'holdout_deck_sha256':digest(rows),
            'holdout_deck_file_sha256':sha(root/'holdout_deck.json'),
            'production_modified':False,
            'limitations':'Qwen contextual semantic probe; strict five-numeral two-action input; external action values; no arbitrary game or tactical certification.'}
    write_new(root/'holdout_acceptance.json',report)
    return report


def infer(root,prompt,*,project='.',device='cuda'):
    manifest,net=load_head(root,project,audit_required=True)
    _,numeric_model=_load_v34(manifest['v34_root'],project)
    import torch
    t,tok,model=v33.model_context(manifest['model_id'],device)
    try:
        x,q=capture(model,tok,t,[prompt],device=device,batch_size=1,tag='infer')
    finally:
        del model,tok
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    roles,sides=predict_slots(net,x,q)
    v=v34.predict(numeric_model,numeric_input(prompt,roles[0],sides[0])[None,:])[0]
    v33.verify_activation(project,manifest['lineage'])
    return {'status':'V38_SEMANTIC_INFERENCE_SHADOW_ONLY',
            'action':v34.CHOICES[v],'role_assignment':list(roles[0]),
            'probability_side':'C' if sides[0]==0 else 'D',
            'numerical_baseline':'v34-trained-neural-head',
            'semantic_model':'frozen-Qwen-token-probe','production_modified':False}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('run','train','audit','infer','status'))
    p.add_argument('--run',required=True)
    p.add_argument('--v34-run',default='runtime/reports/gamer-v1/transition-005-v34-grounded-learning-0001')
    p.add_argument('--root',default='.')
    p.add_argument('--device',default='cuda')
    p.add_argument('--epochs',type=int,default=120)
    p.add_argument('--batch-size',type=int,default=4)
    p.add_argument('--seed',type=int,default=35)
    p.add_argument('--prompt')
    return p


def main(argv=None):
    a=parser().parse_args(argv)
    if a.stage in ('train','run'):
        result=train(a.run,project=a.root,v34_root=a.v34_run,device=a.device,
                     seed=a.seed,epochs=a.epochs,batch_size=a.batch_size)
        print('GAMER_V38_TRAIN '+canonical(result),flush=True)
        if a.stage=='run' and result['status']=='PASS_DEV_HOLDOUT_STILL_SEALED':
            result=audit(a.run,project=a.root,device=a.device,batch_size=a.batch_size)
            print('GAMER_V38_HOLDOUT '+canonical(result),flush=True)
    elif a.stage=='audit':
        result=audit(a.run,project=a.root,device=a.device,batch_size=a.batch_size)
    elif a.stage=='infer':
        require(bool(a.prompt),'PROMPT_REQUIRED')
        result=infer(a.run,a.prompt,project=a.root,device=a.device)
    else:
        result={'status':'V38_STATUS', 'train':read(Path(a.run)/'train_result.json'),
                'holdout':read(Path(a.run)/'holdout_acceptance.json')
                if (Path(a.run)/'holdout_acceptance.json').exists() else None}
    print('GAMER_V38_'+result['status']+' '+canonical(result),flush=True)
    return int(result['status'].startswith('FAIL_'))

if __name__=='__main__':
    raise SystemExit(main())
