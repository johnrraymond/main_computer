"""Train Hawk–Dove V3 with optional masked X/Y/OP *input hints*.

The sole scoring and training prediction is the original V3 numerical head.
Legacy numerical sidecar weights are retained for checkpoint compatibility but
NEVER evaluated, predicted with, or given a loss by this runner.

CLI example (tier:scientific-mask=XYZ:weight,...):
  --xyop-mix T02:*=000:3,100:2,010:2,001:1
  --xyop-mix T02:3=000:5,110:1

XYZ is a 3-bit visibility mask for X, Y and OP, respectively. During training,
visible values come from the row associated with the current split; advancement
evaluates the SAME visibility mixture and scores ONLY hidden X/Y targets.
The OP hint is independent of X/Y and is not scored by this numerical head.

In this XYOP runner, --gate-percent is a percentage-point INCREMENT, not a
fixed passing threshold. --gate-percent 10 starts all tiers at 10%; once all
pass, the common bar becomes 20%, then 30%, ... 100%. The run stops at its
--steps limit or once every tier passes the 100% gate. The active bar and
increment persist with the V3 checkpoint, without resetting on resume.
Passing 100% means all scored hidden predictions are within 5% relative error;
it does not mean numerically exact predictions.

--mask-levels T04,T05,T06 excludes those tiers from training, earlier-tier
replay, dev probes and advancement gates. The active tier order is preserved.
The choice is checkpoint-backed; --mask-levels none explicitly re-enables all.

"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from decimal import Decimal, InvalidOperation
import math
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile

_TOOLS = str(Path(__file__).resolve().parent)
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_numeric_sidecar_train as sidecar
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_game_transform_experiment as ex

CONTRACT = 'nanojev.hawk-dove.xyop-masked-input.v1'
BACKUP = 'latest.pre-xyop-masked-input.pt'
MANIFEST = 'xyop-masked-input-migration.json'
SCALE = 1_000_000.0
FEATURES = 6
HIDDEN = 32
DEFAULT = (("000", 1.0),)
GATE_LADDER_SCHEMA = 'nanojev.hawk-dove.xyop-progressive-tier-gates.v1'
LEVEL_MASK_SCHEMA = 'nanojev.hawk-dove.xyop-excluded-tiers.v1'


def make_module(width):
    import torch
    from torch import nn

    class MaskedXYOP(nn.Module):
        def __init__(self):
            super().__init__()
            self.first = nn.Linear(FEATURES, HIDDEN)
            self.activation = nn.GELU()
            self.last = nn.Linear(HIDDEN, width, bias=False)
            nn.init.zeros_(self.last.weight)

        def forward(self, information):
            if information.ndim != 2 or information.shape[1] != FEATURES:
                raise ValueError('XYOP_INPUT_SHAPE')
            # A hidden triplet must have no effect, including after training.
            visibility = information[:, [0, 2, 4]].sum(dim=-1, keepdim=True)
            return self.last(self.activation(self.first(information))) * (visibility > 0).to(information.dtype)
    return MaskedXYOP()


def pack(x, y, pattern, op='MAX'):
    """Separate value and availability. A hidden target's value is always zero."""
    if len(pattern) != 3 or any(v not in '01' for v in pattern):
        raise ValueError('XYOP_BAD_VISIBILITY_PATTERN')
    if op != 'MAX':
        raise ValueError('XYOP_UNSUPPORTED_OPERATION: only MAX in this curriculum')
    for v in (x, y):
        if not math.isfinite(float(v)) or abs(float(v)) > 10_000_000:
            raise ValueError('XYOP_BAD_NUMERICAL_VALUE')
    return (float(pattern[0] == '1'), float(x)/SCALE if pattern[0]=='1' else 0.,
            float(pattern[1] == '1'), float(y)/SCALE if pattern[1]=='1' else 0.,
            float(pattern[2] == '1'), 1. if pattern[2]=='1' else 0.)


def parse_mixes(clauses):
    """Return mapping (tier or *, scientific sublevel or *) -> weighted states."""
    result = {}
    for clause in clauses:
        lhs, eq, rhs = clause.partition('=')
        tier, colon, sub = lhs.partition(':')
        if not eq or not colon or tier not in (*hd.TIERS, '*') or not sub:
            raise ValueError('XYOP_MIX_FORMAT expected T02:3=000:3,100:1')
        if sub != '*':
            if not sub.isdigit() or (tier != '*' and int(sub) >= 2 ** len(hd.SLOTS[tier])):
                raise ValueError('XYOP_INVALID_SUBLEVEL: ' + lhs)
            sub = str(int(sub))
        key = (tier, sub)
        if key in result:
            raise ValueError('XYOP_DUPLICATE_MIX: ' + lhs)
        parsed = []
        states = set()
        for group in rhs.split(','):
            pattern, sep, weight_text = group.partition(':')
            if not sep or len(pattern) != 3 or any(bit not in '01' for bit in pattern):
                raise ValueError('XYOP_INVALID_PATTERN: ' + group)
            try:
                weight = float(weight_text)
            except ValueError as exc:
                raise ValueError('XYOP_INVALID_WEIGHT: ' + group) from exc
            if not math.isfinite(weight) or weight <= 0 or weight > 100000:
                raise ValueError('XYOP_INVALID_WEIGHT: ' + group)
            if pattern in states:
                raise ValueError('XYOP_DUPLICATE_PATTERN: ' + group)
            states.add(pattern)
            parsed.append((pattern, weight))
        result[key] = tuple(parsed)
    return result


def resolved_mix(mixes, tier, mask):
    sub = str(int(mask))
    for key in ((tier, sub), (tier, '*'), ('*', sub), ('*', '*')):
        if key in mixes:
            return mixes[key]
    return DEFAULT


def choose_pattern(mixes, row, seed, step, view):
    weights = resolved_mix(mixes, row['tier'], row['mask'])
    data = f'{seed}/{step}/{view}/{row["case_id"]}'.encode('utf8')
    entropy = int.from_bytes(hashlib.sha256(data).digest()[:8], 'big')
    return random.Random(entropy).choices([x[0] for x in weights], weights=[x[1] for x in weights], k=1)[0]


def contract(width):
    return {'schema': CONTRACT, 'features': FEATURES, 'hidden': HIDDEN,
            'width': int(width), 'scale': int(SCALE),
            'scoring': 'V3_ONLY', 'evaluation_visibility': 'MATCH_TRAIN_XYOP_MIX',
            'source': 'train_row_target_only_when_explicitly_visible',
            'operation_vocabulary': ['MAX']}


def verify(ck):
    if not isinstance(ck.get('xyop_mask_contract'), dict):
        raise ValueError('XYOP_UPGRADE_REQUIRED')
    value = ck['xyop_mask_contract']
    expected_contract = contract(adapter.checkpoint_width(ck))
    # Old checkpoints recorded the v1 all-hidden evaluator. Accept them for
    # non-mutating probes; the next ordinary training save records the new
    # mixture-matched evaluator, without another checkpoint migration.
    old_contract = dict(expected_contract, evaluation_visibility='000')
    if value not in (expected_contract, old_contract):
        raise ValueError('XYOP_CONTRACT_MISMATCH')
    prefix = 'xyop_mask_adapter.'
    prototype = make_module(value['width'])
    expected = {prefix + name for name in prototype.state_dict()}
    actual = {name for name in ck['decoder'] if name.startswith(prefix)}
    if expected != actual:
        raise ValueError('XYOP_PARAMETER_LAYOUT_DRIFT')
    optimizer = ck['optimizer']
    groups = optimizer['param_groups']
    if len(groups) != 1 or len(groups[0]['params']) != len(ck['decoder']):
        raise ValueError('XYOP_OPTIMIZER_LAYOUT_DRIFT')
    if len(set(groups[0]['params'])) != len(groups[0]['params']):
        raise ValueError('XYOP_DUPLICATE_OPTIMIZER_PARAM')
    if set(optimizer['state']) - set(groups[0]['params']):
        raise ValueError('XYOP_UNKNOWN_OPTIMIZER_PARAM')
    return value


def upgrade(run):
    """Backup then extend current checkpoint, preserving V3/sidecar tensors and Adam moments."""
    import torch
    ck, path = adapter.load_run_state(run)
    sidecar.verify_sidecar(ck)
    adapter.verify_saved(ck)
    root = path.parent
    backup, manifest = root / BACKUP, root / MANIFEST
    if 'xyop_mask_contract' in ck:
        verify(ck)
        if not backup.exists() or not manifest.exists():
            raise ValueError('XYOP_MIGRATION_EVIDENCE_MISSING')
        record = json.loads(manifest.read_text(encoding='utf8'))
        if record.get('backup_sha256') != sidecar.sha(backup):
            raise ValueError('XYOP_BACKUP_DRIFT')
        print(json.dumps({'event':'xyop_migration_reused','step':ck['step']}),flush=True)
        return
    if backup.exists() or manifest.exists():
        raise ValueError('XYOP_PARTIAL_UPGRADE_REFUSAL')
    width = adapter.checkpoint_width(ck)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(20261019)
        module = make_module(width)
    initial = {k:v.detach().clone() for k,v in ck['decoder'].items()}
    old_ids = ck['optimizer']['param_groups'][0]['params']
    if len(old_ids) != len(initial) or set(ck['optimizer']['state'])-set(old_ids):
        raise ValueError('XYOP_PRE_UPGRADE_OPTIMIZER_LAYOUT_INVALID')
    original_sha = sidecar.sha(path)
    with backup.open('xb') as dst, path.open('rb') as src:
        shutil.copyfileobj(src,dst)
        dst.flush();os.fsync(dst.fileno())
    if sidecar.sha(backup) != original_sha:
        raise ValueError('XYOP_BACKUP_HASH_MISMATCH')
    for k, v in module.state_dict().items():
        ck['decoder']['xyop_mask_adapter.'+k] = v.clone()
    count = len(list(module.parameters()))
    ck['optimizer']['param_groups'][0]['params'] = old_ids + list(range(max(old_ids)+1, max(old_ids)+1+count))
    ck['xyop_mask_contract'] = contract(width)
    ck['xyop_mask_step_at_migration'] = ck['step']
    with tempfile.NamedTemporaryFile('wb', dir=root, prefix='xyop-',suffix='.pt',delete=False) as dst:
        temp=Path(dst.name)
    try:
        torch.save(ck,temp)
        check=torch.load(temp,map_location='cpu',weights_only=True)
        verify(check)
        for k, v in initial.items():
            if not torch.equal(check['decoder'][k],v):
                raise ValueError('XYOP_ORIGINAL_WEIGHT_DRIFT: '+k)
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)
    record={'schema':CONTRACT,'step':ck['step'],'backup_sha256':original_sha,
            'upgraded_checkpoint_sha256':sidecar.sha(path),
            'old_head_preserved':True,'old_optimizer_moments_preserved':True}
    temp_manifest=manifest.with_suffix('.json.tmp')
    temp_manifest.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf8')
    os.replace(temp_manifest,manifest)
    print(json.dumps({'event':'xyop_upgraded_in_place','step':ck['step'],
                      'backup':str(backup),'v3_preserved':True,
                      'old_optimizer_moments_preserved':True}),flush=True)


def explain_mix(mixes):
    result=[]
    for tier in hd.TIERS:
        nondefault=[]
        for mask,_ in hd.masks(tier):
            mix=resolved_mix(mixes,tier,mask)
            if mix != DEFAULT:
                nondefault.append({'sublevel':mask, 'mix':dict(mix)})
        result.append({'tier':tier,'sublevels':2**len(hd.SLOTS[tier]),
                       'nondefault':nondefault})
    return result



def conditional_eval_tier(model, rows, mixes, seed, expected):
    """Development advancement on the TRAIN visibility distribution.

    Use the same weighted visibility configurations for every numerical row
    (instead of random dev-label-dependent sampling). Any visible X/Y is an
    input hint, never a scored prediction. Only hidden X/Y determine the gate.
    All predictions are made by the unmodified V3 head; no oracle answers
    are substituted. The sealed test split is never used by this runner.
    """
    from tools import nanojev_game_math_curriculum_v10_runtime as rt
    rows = list(rows)
    stats = {p: {'weight': 0., 'cases': 0} for p in ('000','001','010','011','100','101','110','111')}
    tasks = []
    eligible = 0
    skipped_weight = 0.
    for row in rows:
        mix = resolved_mix(mixes, row['tier'], row['mask'])
        total = sum(w for _, w in mix)
        is_eligible = rt.token_eligible(model, row['prompt'])
        eligible += int(is_eligible)
        for pattern, weight in mix:
            share = weight / total
            # With both numeric results revealed, V3 has no hidden X/Y
            # prediction to evaluate. OP-only reconstruction is not this head.
            if pattern[:2] == '11':
                skipped_weight += share
                continue
            stats[pattern]['weight'] += share
            stats[pattern]['cases'] += 1
            tasks.append((row, pattern, share, is_eligible))
    total_weight = sum(w for _, _, w, _ in tasks)
    if total_weight <= 0:
        raise ValueError('XYOP_GATE_NO_HIDDEN_NUMERIC_TARGET')

    gate_hits = 0.
    x_hits = y_hits = x_weight = y_weight = 0.
    failures = []
    z_hits = z_weight = 0.
    for start in range(0, len(tasks), 4):
        batch = tasks[start:start + 4]
        viable = [(row, pattern, weight) for row, pattern, weight, ok in batch if ok]
        if not viable:
            continue
        if expected:
            raise RuntimeError('XYOP_EVAL_QUEUE_NOT_EMPTY_AT_BATCH_START')
        for row, pattern, _ in viable:
            expected.append((row['prompt'], pack(row['x'], row['y'], pattern)))
        try:
            predictions = science.predict(model, [row['prompt'] for row, _, _ in viable])
            if len(predictions) != len(viable):
                raise RuntimeError('XYOP_EVAL_PREDICTION_COUNT_MISMATCH')
            if expected:
                raise RuntimeError('XYOP_EVAL_QUEUE_NOT_CONSUMED')
        finally:
            expected.clear()
        for (row, pattern, weight), (px, py) in zip(viable, predictions):
            x, y = float(row['x']), float(row['y'])
            x_pass = abs(px - x) / max(1., abs(x)) <= .05
            y_pass = abs(py - y) / max(1., abs(y)) <= .05
            # A visible numeric target never contributes to its own score.
            if pattern[0] == '0':
                x_weight += weight
                x_hits += weight * x_pass
            if pattern[1] == '0':
                y_weight += weight
                y_hits += weight * y_pass
            conditional_pass = ((pattern[0] == '1' or x_pass) and
                                (pattern[1] == '1' or y_pass))
            gate_hits += weight * conditional_pass
            if pattern[:2] == '00' and row.get('z') is not None:
                z_weight += weight
                zpred = 'TIE' if abs(px-py) < 1e-5 else 'HAWK' if px>py else 'DOVE'
                z_hits += weight * (zpred == row['z'])
            if not conditional_pass and len(failures) < 3:
                failures.append({'id':row['case_id'], 'visibility':pattern,
                                 'true':[x,y], 'pred':[float(px),float(py)],
                                 'hidden_x_pass':x_pass if pattern[0]=='0' else None,
                                 'hidden_y_pass':y_pass if pattern[1]=='0' else None})
    depths = {str(depth):{'n':sum(r['full_count']==depth for r in rows)}
              for depth in sorted({r['full_count'] for r in rows})}
    return {'logbar_depths':depths, 'n':len(rows), 'eligible':eligible,
            'joint_5pct':gate_hits/total_weight,
            'x_5pct':x_hits/x_weight if x_weight else None,
            'y_5pct':y_hits/y_weight if y_weight else None,
            'z_diag_accuracy':z_hits/z_weight if z_weight else None,
            'objective':'masked_hidden_xy_v3', 'assessment':'MATCH_TRAIN_XYOP_MIX',
            'visibility_weights':{k:round(v['weight'],8) for k,v in stats.items() if v['cases']},
            'visible_both_excluded_weight':round(skipped_weight,8),
            'hidden_x_weight':x_weight, 'hidden_y_weight':y_weight,
            'failures':failures}



def parse_mask_levels(value):
    """Canonical excluded tier names; 'none' explicitly removes the filter."""
    if value is None:
        return ()
    if not isinstance(value, str):
        raise ValueError('XYOP_BAD_LEVEL_MASK')
    if value.lower() == 'none':
        return ()
    names = value.split(',')
    if not names or len(set(names)) != len(names) or any(t not in hd.TIERS for t in names):
        raise ValueError('XYOP_BAD_LEVEL_MASK: expected comma-separated T00..T08 or none')
    if len(names) == len(hd.TIERS):
        raise ValueError('XYOP_LEVEL_MASK_EMPTY_CURRICULUM')
    return tuple(t for t in hd.TIERS if t in names)


def resolve_level_mask(ck, requested):
    """Reuse a masked resume by default; an explicit CLI mask can replace it."""
    saved = ck.get('xyop_excluded_tiers')
    if saved is not None:
        if (not isinstance(saved, dict) or saved.get('schema') != LEVEL_MASK_SCHEMA or
                not isinstance(saved.get('levels'), list)):
            raise ValueError('XYOP_LEVEL_MASK_CHECKPOINT_SCHEMA_MISMATCH')
        raw = saved['levels']
        previous = parse_mask_levels(','.join(raw)) if raw else ()
        if list(previous) != raw:
            raise ValueError('XYOP_LEVEL_MASK_CHECKPOINT_NONCANONICAL')
    else:
        previous = ()
    excluded = parse_mask_levels(requested) if requested is not None else previous
    source = 'cli' if requested is not None else 'checkpoint' if saved is not None else 'default'
    return excluded, source


def level_mask_snapshot(excluded):
    return {'schema': LEVEL_MASK_SCHEMA, 'levels': list(excluded)}


def active_probe(model, db, limit, seed, gates, active, split='dev'):
    """Scientific probe's contract, but never evaluate an excluded tier."""
    model['decoder'].eval()
    model['gamehead'].eval()
    scores = {}
    for tier in active:
        rows = science.rows_for_probe(db, split, tier, limit, seed)
        scores[tier] = science.eval_tier(model, rows)
    first = next((t for t in active if scores[t]['joint_5pct'] < gates[t] or
                  scores[t]['eligible'] != scores[t]['n']), None)
    active_gates = {t: gates[t] for t in active}
    unique = set(active_gates.values())
    return {'scores':scores, 'first_failing':first,
            'gate':next(iter(unique)) if len(unique)==1 else None,
            'gate_fraction_by_tier':active_gates,
            'gate_percent_by_tier':{t:100*gates[t] for t in active},
            'split':split, 'z_loss_weight':0, 'no_logits_labels':True}


def choose_active_rows(db, focus, step, seed, views, weights_by_tier, active):
    """Preserve the scientific sampler and RNG, excluding masked replay tiers."""
    if focus not in active:
        raise ValueError('XYOP_MASKED_TIER_TRAINING_FOCUS:' + str(focus))
    pool = hd.read_rows(db, 'train', focus)
    previous = active[:active.index(focus)]
    rng = random.Random(f'hd-train/{seed}/{step}')
    chosen = []
    for i in range(views):
        tier = focus if i < max(1,views-1) or not previous else rng.choice(previous)
        candidates = pool if tier == focus else hd.read_rows(db, 'train', tier)
        weights = (weights_by_tier or {}).get(tier,{}) if tier == focus else {}
        if not weights or all(float(v)==1 for v in weights.values()):
            chosen.append(candidates[rng.randrange(len(candidates))])
        else:
            by_mask = {}
            for row in candidates:
                by_mask.setdefault(str(row['mask']), []).append(row)
            masks = sorted(by_mask, key=int)
            selected = rng.choices(masks, weights=[weights.get(m,1) for m in masks], k=1)[0]
            members = by_mask[selected]
            chosen.append(members[rng.randrange(len(members))])
    return chosen


def gate_decimal(value):
    """Exact decimal percentages; reject NaN, infinity and absurd precision."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('XYOP_INVALID_GATE_INCREMENT') from exc
    if not number.is_finite() or not (0 < number <= 100) or number.as_tuple().exponent < -4:
        raise ValueError('XYOP_INVALID_GATE_INCREMENT')
    return number


class ProgressiveGates:
    """Checkpoint-backed ascending, uniform tier gate for the masked V3 runner.

    The scientific trainer is unchanged, including its checkpoint lineage SHA.
    Its existing probe computes the 5%-relative-error, hidden-only per-tier
    scores. Increasing a threshold only reinterprets that SAME held-out probe;
    we never substitute an oracle score or modify the predictor.
    """
    def __init__(self, ck, requested_increment, mode='train', masked_levels=()):
        saved = ck.get('xyop_gate_schedule')
        if saved is None:
            if requested_increment is None and mode == 'train':
                raise ValueError('XYOP_GATE_INCREMENT_REQUIRED: pass --gate-percent 10')
            self.increment = gate_decimal(requested_increment if requested_increment is not None else 10)
            self.current = self.increment
        else:
            if not isinstance(saved, dict) or saved.get('schema') != GATE_LADDER_SCHEMA:
                raise ValueError('XYOP_GATE_SCHEDULE_SCHEMA_MISMATCH')
            self.increment = gate_decimal(saved.get('increment_percent'))
            self.current = gate_decimal(saved.get('active_percent'))
            if (self.current != 100 and self.current % self.increment != 0) or (
                    int(saved.get('checkpoint_step', -1)) != int(ck['step'])):
                raise ValueError('XYOP_GATE_SCHEDULE_CHECKPOINT_DRIFT')
            if requested_increment is not None and gate_decimal(requested_increment) != self.increment:
                raise ValueError('XYOP_GATE_INCREMENT_CHANGED_ON_RESUME')
        self.masked_levels = tuple(masked_levels)
        self.active_tiers = tuple(t for t in hd.TIERS if t not in self.masked_levels)
        if not self.active_tiers:
            raise ValueError('XYOP_LEVEL_MASK_EMPTY_CURRICULUM')
        self.policy_file = None
        self.policy = None
        self.advance = mode == 'train'
        self.complete = False

    def snapshot(self, checkpoint_step):
        return {'schema': GATE_LADDER_SCHEMA,
                'increment_percent': float(self.increment),
                'active_percent': float(self.current),
                'checkpoint_step': int(checkpoint_step)}

    def set_gate(self, gates):
        for tier in hd.TIERS:
            gates[tier] = float(self.current / Decimal(100))
        if self.policy is not None:
            self.policy['gate_percent_by_tier'] = {tier: float(self.current) for tier in hd.TIERS}

    def on_probe(self, report, gates):
        """Raise the common gate until the first nonpassing level or 100%."""
        if report.get('split') != 'dev':
            return report
        if not self.advance:
            report['xyop_gate_ladder'] = {'increment_percent':float(self.increment),
                                          'active_percent':float(self.current)}
            return report
        while report['first_failing'] is None and self.current < 100:
            old = self.current
            self.current = min(Decimal(100), self.current + self.increment)
            self.set_gate(gates)
            report['first_failing'] = next(
                (tier for tier in self.active_tiers if
                 report['scores'][tier]['joint_5pct'] < gates[tier] or
                 report['scores'][tier]['eligible'] != report['scores'][tier]['n']), None)
            print(json.dumps({'event': 'xyop_gate_stage_advanced',
                              'from_percent': float(old),
                              'to_percent': float(self.current),
                              'first_failing': report['first_failing']}), flush=True)
        self.complete = self.current == 100 and report['first_failing'] is None
        report['gate'] = float(self.current / Decimal(100))
        report['gate_fraction_by_tier'] = {tier:gates[tier] for tier in self.active_tiers}
        report['gate_percent_by_tier'] = {tier: float(self.current) for tier in self.active_tiers}
        report['xyop_gate_ladder'] = {'increment_percent':float(self.increment),
                                      'active_percent':float(self.current)}
        report['xyop_all_tiers_100_percent_pass'] = self.complete
        report['masked_levels'] = list(self.masked_levels)
        report['active_levels'] = list(self.active_tiers)
        if self.complete:
            print(json.dumps({'event': 'xyop_all_tiers_100_percent_pass',
                              'active_percent': 100.0,
                              'predictor': 'V3_ONLY'}), flush=True)
        return report


def replace_science_gate_arg(rest, current):
    """Scientific CLI receives the CURRENT level, not the requested increment."""
    out = []
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == '--gate-percent':
            if i + 1 >= len(rest):
                raise ValueError('XYOP_MISSING_GATE_PERCENT')
            i += 2
            continue
        if token.startswith('--gate-percent='):
            i += 1
            continue
        if token == '--tier-gate' or token.startswith('--tier-gate='):
            raise ValueError('XYOP_PROGRESSIVE_GATES_INCOMPATIBLE_WITH_TIER_OVERRIDES')
        out.append(token)
        i += 1
    return [*out, '--gate-percent', str(float(current))]

@contextmanager
def live_v3_only(ck, run, mixes, seed, gate_ladder=None, masked_levels=()):
    """Use existing Qwen cache and adapter; sidecar weights are NEVER scored.

    Patch bounded module globals in the same execution that previously loaded
    the sidecar, restoring everything in finally. A strict queue pairs each
    original training row to its chosen visibility mask; every dev/test call
    receives only configured visible hints; hidden X/Y labels never enter V3.
    """
    import torch
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    masked_levels = tuple(masked_levels)
    active_tiers = tuple(t for t in hd.TIERS if t not in masked_levels)
    if not active_tiers:
        raise ValueError('XYOP_LEVEL_MASK_EMPTY_CURRICULUM')
    checkpoint_tmp=(Path(run).resolve()/'latest.tmp.pt')
    original_predict=science.predict
    original_objective=v3.objective
    original_choose=science.choose_rows
    original_eval_tier=science.eval_tier
    original_probe=science.probe
    original_resolve_gate_policy=science.resolve_gate_policy
    # Capture the original frozen-Qwen/V3 forward BEFORE sidecar hooks replace it.
    # Unlike the sidecar forward, it never constructs a calculator answer.
    core=cached.cached_forward
    if core.__module__ != cached.__name__:
        raise RuntimeError('XYOP_UNABLE_TO_VERIFY_UNMODIFIED_CACHED_FORWARD')
    with sidecar.hooks(ck,run):
        previous_load=ex.load_current
        previous_forward=cached.cached_forward  # NEVER CALL: computes sidecar answer
        previous_save=torch.save
        pending=[]
        eval_pending=[]
        stats={}

        def load(*a,**kw):
            model=previous_load(*a,**kw)
            dec=model['decoder']
            if hasattr(dec,'xyop_mask_adapter'):
                raise ValueError('XYOP_DOUBLE_ATTACHMENT')
            dec.add_module('xyop_mask_adapter',make_module(ck['xyop_mask_contract']['width']).to(model['device']))
            return model

        def choose(*args,**kwargs):
            if pending or eval_pending:
                raise RuntimeError('XYOP_UNCONSUMED_TRAIN_OR_EVAL_BATCH')
            step=int(args[2]) if len(args)>2 else int(kwargs['step'])
            if masked_levels:
                db = args[0] if len(args)>0 else kwargs['db']
                focus = args[1] if len(args)>1 else kwargs['focus']
                views = args[4] if len(args)>4 else kwargs['views']
                weights = args[5] if len(args)>5 else kwargs.get('mask_weights_by_tier')
                rows = choose_active_rows(db,focus,step,seed,views,weights,active_tiers)
            else:
                rows=original_choose(*args,**kwargs)
            for view, row in enumerate(rows):
                pattern=choose_pattern(mixes,row,seed,step,view)
                pending.append((row['prompt'],pack(row['x'],row['y'],pattern)))
                k=f'{row["tier"]}:{row["mask"]}:{pattern}'
                stats[k]=stats.get(k,0)+1
            return rows

        def forward(model,prompts,cache):
            dec=model['decoder']
            if pending:
                if eval_pending or len(prompts)!=1 or pending[0][0]!=prompts[0]:
                    raise RuntimeError('XYOP_TRAIN_BATCH_ORDER_CHANGED')
                _, channel=pending.pop(0)
                rows=[channel]
            elif eval_pending:
                # Dev advancement deliberately supplies ONLY configured visible
                # values. A hidden target's value is never passed to V3.
                if len(prompts)>len(eval_pending):
                    raise RuntimeError('XYOP_EVAL_BATCH_TOO_LARGE')
                rows=[]
                for prompt in prompts:
                    expected_prompt, channel=eval_pending.pop(0)
                    if expected_prompt!=prompt:
                        raise RuntimeError('XYOP_EVAL_BATCH_ORDER_CHANGED')
                    rows.append(channel)
            else:
                # Other diagnostics remain completely unassisted.
                rows=[(0.,)*FEATURES for _ in prompts]
            features=torch.tensor(rows,dtype=torch.float32,device=model['device'])
            old_dec_forward=dec.forward
            def injected(h,parent_xy,operations,tokens=None,mask=None):
                delta=dec.xyop_mask_adapter(features.to(h.device))
                if delta.shape!=h.shape:
                    raise RuntimeError('XYOP_FEATURE_DIMENSION_MISMATCH')
                return old_dec_forward(h+delta,parent_xy,operations,tokens,mask)
            dec.forward=injected
            try:
                with adapter.inject(dec,prompts,model['device']):
                    return core(model,prompts,cache)
            finally:
                dec.forward=old_dec_forward

        def eval_tier(model,rows,batch_size=4):
            if pending or eval_pending:
                raise RuntimeError('XYOP_EVAL_STARTED_WITH_PENDING_TRAIN_ROWS')
            return conditional_eval_tier(model,rows,mixes,seed,eval_pending)

        def resolve_gate_policy(*args, **kwargs):
            filename, policy = original_resolve_gate_policy(*args, **kwargs)
            if gate_ladder is not None:
                gate_ladder.policy_file = filename
                gate_ladder.policy = policy
                gate_ladder.set_gate({})  # Update policy's live gate percentages.
            return filename, policy

        def progressive_probe(model, db, limit, seed, gates, split='dev'):
            report = (active_probe(model, db, limit, seed, gates, active_tiers, split=split)
                      if masked_levels else original_probe(model, db, limit, seed, gates, split=split))
            report['masked_levels'] = list(masked_levels)
            report['active_levels'] = list(active_tiers)
            if gate_ladder is not None:
                return gate_ladder.on_probe(report, gates)
            return report

        def save(obj,target,*args,**kwargs):
            if isinstance(target,(str,os.PathLike)) and Path(target).resolve()==checkpoint_tmp:
                if pending:
                    raise RuntimeError('XYOP_UNCONSUMED_BATCH_AT_CHECKPOINT')
                obj=dict(obj)
                obj['xyop_mask_contract']=contract(ck['xyop_mask_contract']['width'])
                obj['xyop_mask_step_at_migration']=ck['xyop_mask_step_at_migration']
                obj['xyop_mixture_last_chunk']=dict(sorted(stats.items()))
                obj['xyop_advancement_assessment']='MATCH_TRAIN_XYOP_MIX_HIDDEN_XY_ONLY'
                obj['xyop_excluded_tiers'] = level_mask_snapshot(masked_levels)
                if gate_ladder is not None:
                    obj['xyop_gate_schedule'] = gate_ladder.snapshot(obj['step'])
                verify(obj)
            result = previous_save(obj,target,*args,**kwargs)
            if (gate_ladder is not None and isinstance(target,(str,os.PathLike)) and
                    Path(target).resolve()==checkpoint_tmp and gate_ladder.policy_file is not None):
                # The checkpoint is authoritative; the old tier-gates sidecar
                # remains consistent for humans and the next resume.
                science.jwrite(gate_ladder.policy_file,gate_ladder.policy)
            return result

        ex.load_current=load
        cached.cached_forward=forward
        science.choose_rows=choose
        science.eval_tier=eval_tier
        science.resolve_gate_policy=resolve_gate_policy
        science.probe=progressive_probe
        science.predict=original_predict
        v3.objective=original_objective
        torch.save=save
        try:
            print(json.dumps({'event':'xyop_v3_only_runtime',
                              'step':ck['step'],'predictor':'V3_ONLY',
                              'sidecar_prediction_disabled':True,
                              'sidecar_loss_disabled':True,
                              'eval_visibility':'MATCH_TRAIN_XYOP_MIX',
                              'advancement_scoring':'HIDDEN_XY_ONLY',
                              'eval_uses_dev_visible_hints':True,
                              'gate_step_percent': (float(gate_ladder.increment) if gate_ladder else None),
                              'gate_stage_percent': (float(gate_ladder.current) if gate_ladder else None),
                              'masked_levels':list(masked_levels),
                              'active_levels':list(active_tiers)}),flush=True)
            yield stats
        finally:
            torch.save=previous_save
            ex.load_current=previous_load
            cached.cached_forward=previous_forward
            science.choose_rows=original_choose
            science.eval_tier=original_eval_tier
            science.probe=original_probe
            science.resolve_gate_policy=original_resolve_gate_policy
            # Restore hook-owned functions before sidecar.hooks exits.
            science.predict=original_predict
            v3.objective=original_objective


def persist_100_percent_completion(run, ladder):
    """Persist a no-step finish when the initial dev probe already passes 100%.

    The underlying scientific runner returns before constructing a checkpoint
    in this exceptional case. Save ONLY the new schedule metadata, retaining
    the exact old head weights, optimizer, RNG and step.
    """
    if not ladder.complete:
        return
    import torch
    ck, path = adapter.load_run_state(run)
    existing = ck.get('xyop_gate_schedule')
    if (existing is not None and gate_decimal(existing['active_percent']) == 100 and
            ck.get('xyop_excluded_tiers') == level_mask_snapshot(ladder.masked_levels)):
        return
    ck['xyop_gate_schedule'] = ladder.snapshot(ck['step'])
    ck['xyop_excluded_tiers'] = level_mask_snapshot(ladder.masked_levels)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent,
                                     prefix='xyop-final-gate-',suffix='.pt',delete=False) as target:
        staging = Path(target.name)
    try:
        torch.save(ck,staging)
        checked = torch.load(staging,map_location='cpu',weights_only=True)
        verify(checked)
        adapter.verify_saved(checked)
        sidecar.verify_sidecar(checked)
        if (checked['step'] != ck['step'] or
                checked['xyop_gate_schedule'] != ck['xyop_gate_schedule'] or
                checked['xyop_excluded_tiers'] != ck['xyop_excluded_tiers']):
            raise ValueError('XYOP_COMPLETION_METADATA_DRIFT')
        for name, value in ck['decoder'].items():
            if not torch.equal(value,checked['decoder'][name]):
                raise ValueError('XYOP_COMPLETION_WEIGHT_DRIFT:'+name)
        os.replace(staging,path)
    finally:
        staging.unlink(missing_ok=True)
    if ladder.policy_file is not None:
        science.jwrite(ladder.policy_file,ladder.policy)
    print(json.dumps({'event':'xyop_gate_completion_saved_without_training',
                      'step':ck['step'],'active_percent':100.0,
                      'optimizer_unchanged':True}),flush=True)


def run(args,rest,mixes):
    ck,_=adapter.load_run_state(args.output)
    sidecar.verify_sidecar(ck)
    adapter.verify_saved(ck)
    verify(ck)
    if args.mode=='test':
        raise ValueError('XYOP_TEST_SPLIT_SEALED')
    masked_levels, mask_source = resolve_level_mask(ck, args.mask_levels)
    ladder = ProgressiveGates(ck, args.gate_percent, args.mode, masked_levels)
    scientific_rest = replace_science_gate_arg(rest, ladder.current)
    print(json.dumps({'event':'xyop_level_mask', 'masked_levels':list(masked_levels),
                      'active_levels':list(ladder.active_tiers), 'source':mask_source}), flush=True)
    print(json.dumps({'event': 'xyop_gate_schedule',
                      'increment_percent':float(ladder.increment),
                      'active_percent':float(ladder.current),
                      'stop_at_all_tiers_100_percent':True}), flush=True)
    with live_v3_only(ck,args.output,mixes,args.seed,ladder,masked_levels) as stats:
        cached.main(['--cache-memory-mb',str(args.cache_memory_mb),*scientific_rest])
    if args.mode=='train':
        persist_100_percent_completion(args.output,ladder)
        print(json.dumps({'event':'xyop_training_mix_counts','counts':dict(sorted(stats.items())),
                          'source':'train_only','assessment':'V3_ONLY'}),flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__,add_help=False)
    p.add_argument('--xyop-mix',action='append',default=[],metavar='TIER:MASK=XYZ:WEIGHT,...')
    p.add_argument('--mask-levels',default=None,metavar='T04,T05,T06',
                   help='Exclude these tiers from train/replay/evaluation/gates; none restores all')
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--additional-steps',type=int,default=None)
    p.add_argument('--help-xyop',action='store_true')
    ours,rest=p.parse_known_args(argv)
    mixes=parse_mixes(ours.xyop_mix)
    if ours.mask_levels is not None:
        parse_mask_levels(ours.mask_levels)
    if ours.help_xyop:
        print(__doc__)
        return
    if rest and rest[0]=='plan':
        print(json.dumps({'event':'xyop_mixture_plan','default':'000',
                          'masks':'X Y OP, 1 means visible',
                          'resolved':explain_mix(mixes)},indent=2))
        return
    if rest and rest[0]=='upgrade':
        science_args=science.make_parser().parse_args(['probe',*rest[1:]])
        if ours.additional_steps is not None:
            raise ValueError('XYOP_ADDITIONAL_STEPS_DURING_UPGRADE')
        upgrade(science_args.output)
        return
    args=science.make_parser().parse_args(rest)
    if ours.additional_steps is not None:
        if args.mode!='train' or ours.additional_steps<1:
            raise ValueError('XYOP_ADDITIONAL_STEPS_TRAIN_ONLY')
        ck,_=adapter.load_run_state(args.output)
        next_step=int(ck['step'])+ours.additional_steps
        rest=[*rest,'--steps',str(next_step)]
        print(json.dumps({'event':'xyop_train_plan','start_step':ck['step'],
                          'target_step':next_step,'additional_steps':ours.additional_steps}),flush=True)
    run(argparse.Namespace(output=args.output,mode=args.mode,seed=args.seed,
                           gate_percent=args.gate_percent,
                           cache_memory_mb=ours.cache_memory_mb,
                           mask_levels=ours.mask_levels),rest,mixes)


if __name__=='__main__':
    main()
