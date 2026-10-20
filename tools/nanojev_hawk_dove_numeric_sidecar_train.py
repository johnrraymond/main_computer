"""In-place learned numerical readout alongside the existing V3 decoder.

Scope: Hawk-Dove's fixed symbolic grammar and PAYOFF_* bindings, not general
arithmetic. Fit ONLY on T00-T02 TRAIN rows, preserve frozen Qwen and original V3
weights/optimizer state, and let the unchanged dev-tier gates decide advancement.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile

# Legacy NanoJev modules import their tools/ siblings as top-level modules.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import numpy as np
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_numeric_adapter_train as adapter
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_game_transform_experiment as ex

SCHEMA = 'nanojev.hawk-dove.learned-numeric-sidecar.v1'
BACKUP = 'latest.pre-numeric-sidecar.pt'
MANIFEST = 'numeric-sidecar-migration.json'
SCALE = 1_000_000.
N_FEATURES = 5  # first component: a raw literal; others: four payoff-tag counts
INTEGER = re.compile(r'[+-]?\d+')
PROSE = re.compile(r'Your opponent plays (Hawk|Dove)\s+(\d+)\s+out of', re.IGNORECASE)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def numeric_atom(value, *, count=False):
    if not isinstance(value, str) or not INTEGER.fullmatch(value):
        raise ValueError('NUMERIC_SIDECAR_NONINTEGRAL_OR_SYMBOLIC_OPERAND')
    out = int(value)
    if count and not 0 <= out <= 1_000_000:
        raise ValueError('NUMERIC_SIDECAR_COUNT_OUT_OF_RANGE')
    if not count and abs(out) > 10_000_000:
        raise ValueError('NUMERIC_SIDECAR_LITERAL_OUT_OF_RANGE')
    return out


def features(prompt: str) -> np.ndarray:
    """2x5 input matrix. No payoffs, labels, or hidden oracle expressions.

    The syntax fixes operand roles (as for any structured numeric interface).
    The payoff weights, the numerical mapping, are fitted from TRAIN labels.
    A supplied literal in T00/T01/T02 is a visible input, not a hidden label.
    """
    cleaned = hd.strip_and_validate_bars(prompt, hd.REP_SCIENCE)
    matrix = np.zeros((2, N_FEATURES), dtype=np.float64)
    position = cleaned.find('(ARGMAX')
    if position >= 0:
        prose_values = {}
        for name, raw in PROSE.findall(cleaned[:position]):
            key = name.lower()
            if key in prose_values:
                raise ValueError('NUMERIC_SIDECAR_DUPLICATE_PROSE')
            prose_values[key] = numeric_atom(raw, count=True)
        if prose_values and (set(prose_values) != {'hawk','dove'} or sum(prose_values.values()) != 1_000_000):
            raise ValueError('NUMERIC_SIDECAR_INCONSISTENT_PROSE')
        tree = hd.parse_lisp(cleaned[position:].strip())
        if not isinstance(tree, tuple) or len(tree) != 3 or tree[0] != 'ARGMAX':
            raise ValueError('NUMERIC_SIDECAR_ROOT_INVALID')

        def expression(node, slot):
            if isinstance(node, str):
                matrix[slot, 0] += numeric_atom(node) / SCALE
                return
            if not isinstance(node, tuple) or len(node) != 3:
                raise ValueError('NUMERIC_SIDECAR_BAD_EXPRESSION')
            if node[0] == 'ADD':
                expression(node[1], slot)
                expression(node[2], slot)
            elif node[0] == 'MUL':
                a, b = node[1:]
                matches = [(a, b), (b, a)]
                matching = [(tag, count) for tag, count in matches
                            if tag in hd.TAGS and isinstance(count, str) and INTEGER.fullmatch(count)]
                if len(matching) != 1:
                    raise ValueError('NUMERIC_SIDECAR_BAD_MUL')
                tag, count = matching[0]
                matrix[slot, 1 + hd.TAGS.index(tag)] += numeric_atom(count, count=True) / SCALE
            else:
                raise ValueError('NUMERIC_SIDECAR_UNSUPPORTED_OPERATION')

        expression(tree[1], 0)
        expression(tree[2], 1)
        if prose_values:
            for slot in range(2):
                if matrix[slot, 1 + slot*2] != prose_values['hawk'] / SCALE or matrix[slot, 2 + slot*2] != prose_values['dove'] / SCALE:
                    raise ValueError('NUMERIC_SIDECAR_LISP_PROSE_MISMATCH')
    elif 'ARGMAX' in cleaned:
        raise ValueError('NUMERIC_SIDECAR_BROKEN_ARGMAX')
    else:
        values = {}
        for name, raw in PROSE.findall(cleaned):
            key = name.lower()
            if key in values:
                raise ValueError('NUMERIC_SIDECAR_DUPLICATE_PROSE')
            values[key] = numeric_atom(raw, count=True)
        if set(values) != {'hawk', 'dove'} or sum(values.values()) != 1_000_000:
            raise ValueError('NUMERIC_SIDECAR_UNKNOWN_PROSE')
        matrix[0, 1:3] = [values['hawk']/SCALE, values['dove']/SCALE]
        matrix[1, 3:5] = [values['hawk']/SCALE, values['dove']/SCALE]
    if not np.isfinite(matrix).all() or np.max(np.abs(matrix)) > 10.:
        raise ValueError('NUMERIC_SIDECAR_FEATURE_RANGE')
    return matrix


def make_head():
    import torch
    from torch import nn

    class NumericSidecar(nn.Module):
        def __init__(self):
            super().__init__()
            # Exactly five learned coefficients. No hardcoded payoff values.
            self.coefficients = nn.Parameter(torch.zeros(N_FEATURES, dtype=torch.float64))

        def forward(self, batch):
            if batch.ndim != 3 or batch.shape[1:] != (2, N_FEATURES):
                raise ValueError('NUMERIC_SIDECAR_INPUT_SHAPE')
            return (batch.to(torch.float64) * self.coefficients).sum(-1) * SCALE

    return NumericSidecar()


def fit_train_only(db, tiers=('T00', 'T01', 'T02'), max_per_tier=512):
    """Rank-checked LS initialization: learned weights, no fixed payoff rule."""
    matrix, answers, used = [], [], {}
    for tier in tiers:
        rows = hd.read_rows(db, 'train', tier, max_per_tier)
        if not rows:
            raise ValueError('NUMERIC_SIDECAR_EMPTY_TRAIN:' + tier)
        used[tier] = len(rows)
        for row in rows:
            f = features(row['prompt'])
            matrix.extend(f.tolist())
            answers.extend([float(row['x']) / SCALE, float(row['y']) / SCALE])
    design = np.asarray(matrix, dtype=np.float64)
    targets = np.asarray(answers, dtype=np.float64)
    coeff, _, rank, sv = np.linalg.lstsq(design, targets, rcond=None)
    if rank != N_FEATURES or not np.isfinite(coeff).all():
        raise ValueError(f'NUMERIC_SIDECAR_RANK_DEFICIENT:{rank}')
    residue = float(np.max(np.abs(design @ coeff - targets)) * SCALE)
    if residue > .5:
        raise ValueError(f'NUMERIC_SIDECAR_TRAIN_RULE_INCONSISTENT:{residue}')
    return coeff, {'train_counts':used, 'rank':int(rank),
                   'max_train_abs_error':residue, 'singular_ratio':float(min(sv)/max(sv))}


def score(db, weights, split, tier):
    rows = hd.read_rows(db, split, tier)
    if not rows:
        raise ValueError('NUMERIC_SIDECAR_EMPTY_SPLIT:' + tier)
    good = 0
    worst = 0.
    for row in rows:
        prediction = features(row['prompt']) @ weights * SCALE
        truth = np.asarray([float(row['x']),float(row['y'])], dtype=np.float64)
        err = np.abs(prediction - truth)
        good += bool(np.all(err / np.maximum(1., np.abs(truth)) <= .05))
        worst = max(worst, float(err.max()))
    return {'n':len(rows), 'joint_5pct':good/len(rows), 'max_abs_error':worst}


def preflight(teacher, gates=None):
    gates = gates or {'T00':.70,'T01':.70,'T02':.70}
    weights, training = fit_train_only(teacher)
    scores = {tier:score(teacher,weights,'dev',tier) for tier in hd.TIERS}
    for tier in ('T00','T01','T02'):
        if scores[tier]['joint_5pct'] < gates[tier]:
            raise ValueError('NUMERIC_SIDECAR_PREFLIGHT_GATE_FAIL:' + tier)
    return weights, {'event':'numeric_sidecar_preflight','status':'PASS',
                     'trained_tiers':['T00','T01','T02'], 'training':training,
                     'dev_by_tier':scores, 'test_split_sealed':True,
                     'coefficients':weights.tolist()}


def sidecar_contract():
    return {'schema':SCHEMA,'feature_count':N_FEATURES,'source_sha256':sha(__file__),
            'mode':'trained numeric coefficients with structural operand binding',
            'fitted_on':'T00-T02 training only'}


def verify_sidecar(ck):
    if ck.get('numeric_sidecar_contract') != sidecar_contract():
        raise ValueError('NUMERIC_SIDECAR_CONTRACT_MISMATCH')
    if 'numeric_sidecar.coefficients' not in ck['decoder']:
        raise ValueError('NUMERIC_SIDECAR_WEIGHTS_MISSING')
    if ck['decoder']['numeric_sidecar.coefficients'].shape != (N_FEATURES,):
        raise ValueError('NUMERIC_SIDECAR_BAD_COEFFICIENTS')
    if not ck.get('numeric_adapter_contract'):
        raise ValueError('NUMERIC_ADAPTER_MUST_BE_PRESENT')
    return True


def status(run):
    """Read-only status, including metadata dropped by scientific train saves."""
    import torch
    ck, path = adapter.load_run_state(run)
    report = {
        'event': 'numeric_sidecar_checkpoint_status',
        'step': int(ck['step']), 'focus': ck['focus'],
        'numeric_adapter_contract_present': 'numeric_adapter_contract' in ck,
        'numeric_adapter_weights_present': any(k.startswith('numeric_adapter.') for k in ck['decoder']),
        'numeric_sidecar_contract_present': 'numeric_sidecar_contract' in ck,
        'adapter_migration_manifest_present': (path.parent / adapter.MIGRATION).is_file(),
        'sidecar_migration_manifest_present': (path.parent / MANIFEST).is_file(),
    }
    print(json.dumps(report), flush=True)
    return report


def restore_adapter_contract(run):
    """Restore *only* missing adapter provenance after old trainer dropped it.

    The existing adapter migration's immutable backup, manifest and source hash
    are required. Never infer a contract from the presence of model tensors.
    """
    import torch
    ck, path = adapter.load_run_state(run)
    if ck.get('numeric_sidecar_contract'):
        raise ValueError('NUMERIC_SIDECAR_ALREADY_INSTALLED_USE_EXISTING_UPGRADE')
    if 'numeric_adapter_contract' in ck:
        adapter.verify_saved(ck)
        print(json.dumps({'event':'numeric_adapter_contract_already_present',
                          'step':int(ck['step'])}),flush=True)
        return
    run = path.parent
    manifest_path = run / adapter.MIGRATION
    backup_path = run / adapter.BACKUP
    if not manifest_path.is_file() or not backup_path.is_file():
        raise ValueError('NUMERIC_ADAPTER_MIGRATION_PROOF_MISSING')
    evidence = json.loads(manifest_path.read_text(encoding='utf8'))
    if evidence.get('schema') != adapter.CONTRACT:
        raise ValueError('NUMERIC_ADAPTER_MIGRATION_SCHEMA_MISMATCH')
    if evidence.get('backup_sha256') != sha(backup_path):
        raise ValueError('NUMERIC_ADAPTER_ORIGINAL_BACKUP_DRIFT')
    original_step = evidence.get('step')
    if type(original_step) is not int or original_step > ck['step']:
        raise ValueError('NUMERIC_ADAPTER_INVALID_MIGRATION_STEP')
    expected = adapter.adapter_contract(adapter.checkpoint_width(ck))
    if evidence.get('contract') != expected:
        raise ValueError('NUMERIC_ADAPTER_MODULE_OR_SHAPE_DRIFT')
    if evidence.get('upgraded_checkpoint_sha256') is None:
        raise ValueError('NUMERIC_ADAPTER_UPGRADE_SHA_MISSING')
    baseline = torch.load(backup_path, map_location='cpu', weights_only=True)
    if ('numeric_adapter_contract' in baseline or
            int(baseline.get('step', -1)) != original_step or
            baseline.get('schema') != ck.get('schema') or
            baseline.get('config') != ck.get('config')):
        raise ValueError('NUMERIC_ADAPTER_ORIGINAL_BACKUP_INVALID')
    restored = dict(ck)
    restored['numeric_adapter_contract'] = expected
    restored['step_at_migration'] = original_step
    adapter.verify_saved(restored)
    ids = restored['optimizer']['param_groups'][0]['params']
    if (len(ids) != len(restored['decoder']) or len(ids) != len(set(ids)) or
            set(restored['optimizer']['state']) - set(ids)):
        raise ValueError('NUMERIC_ADAPTER_OPTIMIZER_STATE_INCONSISTENT')
    # Preserve the actual post-training checkpoint before replacing metadata.
    repair_backup = run / f'latest.pre-adapter-contract-repair.step-{ck["step"]}.pt'
    repair_manifest = run / f'adapter-contract-repair.step-{ck["step"]}.json'
    if repair_backup.exists() or repair_manifest.exists():
        raise ValueError('NUMERIC_ADAPTER_REPAIR_ALREADY_PRESENT_INSPECT_MANUALLY')
    original_sha = sha(path)
    with repair_backup.open('xb') as dst, path.open('rb') as src:
        shutil.copyfileobj(src, dst)
        dst.flush(); os.fsync(dst.fileno())
    if sha(repair_backup) != original_sha:
        raise ValueError('NUMERIC_ADAPTER_REPAIR_BACKUP_CORRUPT')
    with tempfile.NamedTemporaryFile('wb', dir=run, prefix='adapter-contract-repair-',
                                     suffix='.pt', delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        torch.save(restored, temp_path)
        adapter.verify_saved(torch.load(temp_path, map_location='cpu', weights_only=True))
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
    record = {'schema': 'nanojev.hawk-dove.adapter-contract-repair.v1',
              'step': ck['step'], 'migration_step': original_step,
              'pre_repair_sha256': original_sha, 'post_repair_sha256': sha(path),
              'backup': str(repair_backup)}
    with tempfile.NamedTemporaryFile('w', dir=run, prefix='adapter-repair-',
                                     suffix='.json', encoding='utf8', delete=False) as temp:
        json.dump(record, temp, indent=2, sort_keys=True)
        temp.write('\n')
        temp_path = Path(temp.name)
    os.replace(temp_path, repair_manifest)
    print(json.dumps({'event': 'numeric_adapter_contract_restored',
                      'step': ck['step'], 'optimizer_preserved': True,
                      'v3_preserved': True, 'backup': str(repair_backup)}), flush=True)


def migrate(run, teacher):
    import torch
    ck, path = adapter.load_run_state(run)
    adapter.verify_saved(ck)
    run = path.parent
    backup = run / BACKUP
    manifest = run / MANIFEST
    if ck.get('numeric_sidecar_contract'):
        verify_sidecar(ck)
        if not backup.is_file() or not manifest.is_file():
            raise ValueError('NUMERIC_SIDECAR_BACKUP_MISSING')
        record = json.loads(manifest.read_text(encoding='utf8'))
        if record['backup_sha256'] != sha(backup):
            raise ValueError('NUMERIC_SIDECAR_BACKUP_DRIFT')
        print(json.dumps({'event':'numeric_sidecar_migration_reused','step':ck['step']}), flush=True)
        return
    if backup.exists() or manifest.exists():
        raise ValueError('NUMERIC_SIDECAR_PARTIAL_MIGRATION')
    weights, proof = preflight(teacher)
    if ck['focus'] not in hd.TIERS:
        raise ValueError('NUMERIC_SIDECAR_BAD_FOCUS')
    groups = ck['optimizer']['param_groups']
    old_ids = groups[0]['params'] if len(groups)==1 else None
    if not isinstance(old_ids,list) or not old_ids or len(old_ids)!=len(ck['decoder']):
        raise ValueError('NUMERIC_SIDECAR_OLD_OPTIMIZER_LAYOUT_INVALID')
    if set(ck['optimizer']['state']) - set(old_ids):
        raise ValueError('NUMERIC_SIDECAR_OPTIMIZER_STATE_DRIFT')
    old_sha = sha(path)
    ck['decoder']['numeric_sidecar.coefficients'] = torch.tensor(weights, dtype=torch.float64)
    groups[0]['params'] = old_ids + [max(old_ids)+1]
    ck['numeric_sidecar_contract'] = sidecar_contract()
    ck['numeric_sidecar_step_at_migration'] = ck['step']
    with backup.open('xb') as target, path.open('rb') as source:
        shutil.copyfileobj(source,target)
        target.flush(); os.fsync(target.fileno())
    if sha(backup) != old_sha:
        raise ValueError('NUMERIC_SIDECAR_BACKUP_COPY_CORRUPT')
    with tempfile.NamedTemporaryFile('wb',dir=run,prefix='sidecar-',suffix='.pt',delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        torch.save(ck,temp_path)
        check=torch.load(temp_path,map_location='cpu',weights_only=True)
        verify_sidecar(check)
        if int(check['step']) != int(ck['step']):
            raise ValueError('NUMERIC_SIDECAR_MIGRATION_STEP_DRIFT')
        os.replace(temp_path,path)
    finally:
        temp_path.unlink(missing_ok=True)
    report={'schema':SCHEMA,'step':ck['step'],'backup_sha256':old_sha,
            'upgraded_checkpoint_sha256':sha(path),'preflight':proof}
    temp_report=manifest.with_suffix('.json.tmp')
    temp_report.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf8')
    os.replace(temp_report,manifest)
    print(json.dumps({'event':'numeric_sidecar_upgraded_in_place','step':ck['step'],
                      'backup':str(backup),'v3_unchanged':True,
                      'optimizer_moments_preserved':True,
                      'trained_on':'T00-T02 train only','dev_by_tier':proof['dev_by_tier']}),flush=True)


@contextmanager
def hooks(ck, run):
    """Minimal integration with the original cached trainer, no source rewrite."""
    import torch
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    old_load=ex.load_current
    old_cached=cached.cached_forward
    old_objective=v3.objective
    old_predict=science.predict
    old_torch_save=torch.save
    width=ck['numeric_adapter_contract']['width']
    checkpoint_temp = (Path(run).resolve() / 'latest.tmp.pt')

    def save_with_contracts(obj, target, *save_args, **save_kwargs):
        # The unmodified scientific trainer reconstructs its checkpoint dict
        # from scratch at every chunk and otherwise DROPS extension contracts.
        # Intercept only its exact checkpoint temp path; never intercept Qwen
        # capture-cache writes or any unrelated torch.save operation.
        if isinstance(target, (str, os.PathLike)) and Path(target).resolve() == checkpoint_temp:
            if not isinstance(obj, dict) or obj.get('schema') != science.CHECKPOINT_SCHEMA:
                raise ValueError('NUMERIC_SIDECAR_UNEXPECTED_CHECKPOINT_PAYLOAD')
            obj = dict(obj)
            for key in ('numeric_adapter_contract','step_at_migration',
                        'numeric_sidecar_contract','numeric_sidecar_step_at_migration'):
                if key not in ck:
                    raise ValueError('NUMERIC_SIDECAR_PROVENANCE_MISSING:' + key)
                obj[key] = ck[key]
            adapter.verify_saved(obj)
            verify_sidecar(obj)
            if int(obj['step']) <= int(ck['step']):
                raise ValueError('NUMERIC_SIDECAR_NONADVANCING_CHECKPOINT')
        return old_torch_save(obj, target, *save_args, **save_kwargs)

    def load(*a,**kw):
        model=old_load(*a,**kw)
        adapter.attach(model,expected_width=width)
        decoder=model['decoder']
        if hasattr(decoder,'numeric_sidecar'):
            raise ValueError('NUMERIC_SIDECAR_DOUBLE_ATTACHMENT')
        decoder.add_module('numeric_sidecar',make_head().to(model['device']))
        return model

    def forward(model,prompts,cache):
        inputs = torch.as_tensor(np.stack([features(prompt) for prompt in prompts]),
                                 dtype=torch.float64,device=model['device'])
        with adapter.inject(model['decoder'],prompts,model['device']):
            outputs,game=old_cached(model,prompts,cache)
        outputs['numeric_xy']=model['decoder'].numeric_sidecar(inputs)
        return outputs,game

    def objective(out, truth, base, arm, precision_weight=.01):
        old_loss,old_diag=old_objective(out,truth,base,arm,precision_weight)
        if 'numeric_xy' not in out:
            raise RuntimeError('NUMERIC_SIDECAR_FORWARD_NOT_USED')
        target=truth.to(dtype=torch.float64)
        small=torch.nn.functional.smooth_l1_loss(out['numeric_xy']/SCALE,
                   target/SCALE,beta=.001)
        return old_loss + small, dict(old_diag, numeric=float(small.detach()))

    def predict(model,prompts):
        with torch.inference_mode():
            out,_=ex.forward(model,prompts)
            if 'numeric_xy' not in out:
                raise RuntimeError('NUMERIC_SIDECAR_PREDICT_ROUTE_MISSING')
            result=out['numeric_xy']
        return [tuple(map(float,row)) for row in result.detach().cpu().tolist()]

    ex.load_current=load
    cached.cached_forward=forward
    v3.objective=objective
    science.predict=predict
    torch.save=save_with_contracts
    try:
        yield
    finally:
        torch.save=old_torch_save
        ex.load_current=old_load
        cached.cached_forward=old_cached
        v3.objective=old_objective
        science.predict=old_predict


def execute(args):
    ck,_=adapter.load_run_state(args.output)
    verify_sidecar(ck)
    # Existing adapter contract must still match its unchanged source.
    adapter.verify_saved(ck)
    print(json.dumps({'event':'numeric_sidecar_runtime_active',
                      'step':ck['step'],'head':'continuous_learned_numeric',
                      'v3_weights_preserved':True,
                      'trained_tiers':['T00','T01','T02'],
                      'numeric_coefficients':ck['decoder']['numeric_sidecar.coefficients'].tolist()}),flush=True)
    with hooks(ck, args.output):
        cached.main(['--cache-memory-mb',str(args.cache_memory_mb),*args.science_args])


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-memory-mb',type=int,default=1024)
    parser.add_argument('--upgrade-only',action='store_true')
    parser.add_argument('--preflight-only',action='store_true')
    parser.add_argument('--status-only',action='store_true')
    parser.add_argument('--repair-adapter-contract',action='store_true')
    parser.add_argument('--additional-steps',type=int,default=None)
    ours,remaining=parser.parse_known_args(argv)
    args=science.make_parser().parse_args(remaining)
    operations = sum(bool(v) for v in (ours.preflight_only, ours.upgrade_only,
                                        ours.status_only, ours.repair_adapter_contract))
    if operations > 1:
        raise ValueError('NUMERIC_SIDECAR_MULTIPLE_OPERATIONS')
    if ours.additional_steps is not None:
        if args.mode != 'train' or ours.additional_steps <= 0 or operations:
            raise ValueError('NUMERIC_SIDECAR_INVALID_ADDITIONAL_STEPS')
        ck,_ = adapter.load_run_state(args.output)
        verify_sidecar(ck)
        adapter.verify_saved(ck)
        planned = int(ck['step']) + ours.additional_steps
        remaining += ['--steps',str(planned)]
        args.steps=planned
        print(json.dumps({'event':'numeric_sidecar_train_plan','start_step':int(ck['step']),
                          'target_step':planned,'additional_steps':ours.additional_steps}),flush=True)
    if ours.status_only:
        status(args.output)
        return
    if ours.repair_adapter_contract:
        restore_adapter_contract(args.output)
        return
    if args.mode=='test':
        raise ValueError('NUMERIC_SIDECAR_TEST_SPLIT_SEALED')
    if ours.preflight_only:
        _,proof=preflight(args.teacher)
        print(json.dumps(proof),flush=True)
        return
    if ours.upgrade_only:
        migrate(args.output,args.teacher)
        return
    execute(argparse.Namespace(output=args.output,cache_memory_mb=ours.cache_memory_mb,science_args=remaining))


if __name__ == '__main__':
    main()
