"""Controlled fork and training runner for ordered-exponent X/Y supervision.

Forks *unchanged bytes* of a stopped scientific-v2 checkpoint to control and
augmented lineages; never changes the checkpoint-bound scientific trainer code.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from tools import nanojev_hawk_dove_ordered_exponent as ordered
from tools import nanojev_hawk_dove_scientific_train as science

SCHEMA = 'nanojev.hawk-dove.ordered-exponent-ab.v1'
ROLE_OPTIONS = ('control', 'ordered')


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda: file.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data):
    path.write_text(json.dumps(data, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def fork(donor: Path, root: Path, source: Path, teacher: Path,
         exponent_weight: float, magnitude_weight: float) -> dict:
    """Atomic two-branch fork. Never touches donor or pre-existing branches."""
    import torch
    donor, root = donor.expanduser().resolve(), root.expanduser().resolve()
    if not donor.is_dir() or not (donor / 'latest.pt').is_file():
        raise FileNotFoundError('STOP_TRAINER_AND_SAVE_DONOR_CHECKPOINT_FIRST')
    if root.exists():
        raise FileExistsError('FORK_DESTINATION_MUST_NOT_EXIST: ' + str(root))
    if root == donor or donor in root.parents or root in donor.parents:
        raise ValueError('ISOLATED_AB_FORK_REQUIRED')
    ckpt = donor / 'latest.pt'
    original_hash = sha(ckpt)
    checkpoint = torch.load(ckpt, map_location='cpu', weights_only=True)
    identity = json.loads((donor / 'config.json').read_text(encoding='utf-8'))
    if checkpoint.get('schema') != science.CHECKPOINT_SCHEMA or checkpoint.get('config') != identity:
        raise ValueError('DONOR_CHECKPOINT_IDENTITY_DRIFT')
    if identity['trainer_sha256'] != sha(Path(science.__file__)):
        raise ValueError('DONOR_SCIENCE_TRAINER_SHA_DRIFT')
    if identity['source_sha256'] != sha(source) or identity['teacher_sha256'] != science.teacher_info(teacher)['sha256']:
        raise ValueError('DONOR_SOURCE_OR_TEACHER_DRIFT')
    if checkpoint.get('step', -1) < 1 or not {'decoder', 'optimizer', 'rng_cpu', 'game_operator_head', 'focus', 'streak'} <= set(checkpoint):
        raise ValueError('INCOMPLETE_DONOR_STATE')
    # Fail on invalid hyperparameters before creating any directories.
    validate_weights(exponent_weight, magnitude_weight)
    policy_files = ('config.json', 'tier-gates.json', 'mask-weights.json')
    for file in policy_files[:2]:
        if not (donor / file).is_file():
            raise FileNotFoundError('DONOR_POLICY_MISSING: ' + file)
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.ordered-exponent-staging-', dir=root.parent))
    try:
        for role in ROLE_OPTIONS:
            out = staging / role
            out.mkdir()
            for name in policy_files:
                if (donor / name).exists():
                    shutil.copy2(donor / name, out / name)
            shutil.copy2(ckpt, out / 'latest.pt')
            if sha(out / 'latest.pt') != original_hash:
                raise RuntimeError('CHECKPOINT_FORK_BYTES_CHANGED')
            payload = {
                'schema': SCHEMA, 'role': role,
                'donor_checkpoint_sha256': original_hash,
                'donor_step': int(checkpoint['step']), 'donor_focus': checkpoint['focus'],
                'config_sha256': sha(out / 'config.json'),
                'scientific_trainer_sha256': sha(Path(science.__file__)),
                'cached_train_sha256': sha(Path(science.__file__).with_name('nanojev_hawk_dove_cached_train.py')),
                'supervision_sha256': sha(Path(ordered.__file__)),
                'experiment_runner_sha256': sha(Path(__file__)),
                'source_sha256': sha(source),
                'teacher_sha256': science.teacher_info(teacher)['sha256'],
                'exponent_distance_weight': exponent_weight if role == 'ordered' else 0.,
                'log_magnitude_weight': magnitude_weight if role == 'ordered' else 0.,
                'objective': ('existing_sign_ce_exponent_ce_mantissa_huber_plus_ordered_regression'
                              if role == 'ordered' else 'unaltered_sign_ce_exponent_ce_mantissa_huber'),
            }
            write_json(out / 'ab-experiment.json', payload)
        if sha(ckpt) != original_hash:
            raise RuntimeError('DONOR_CHECKPOINT_CHANGED_DURING_FORK')
        staging.rename(root)
        staging = None
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    return {'status': 'PASS', 'step': int(checkpoint['step']), 'focus': checkpoint['focus'],
            'donor_checkpoint_untouched': sha(ckpt) == original_hash,
            'forks_identical': sha(root / 'control/latest.pt') == sha(root / 'ordered/latest.pt'),
            'root': str(root)}


def validate_weights(exponent_weight, magnitude_weight):
    import math
    for weight in (exponent_weight, magnitude_weight):
        if not math.isfinite(weight) or weight < 0 or weight > 10:
            raise ValueError('UNSAFE_OR_INVALID_SUPERVISION_WEIGHT')
    if exponent_weight + magnitude_weight <= 0:
        raise ValueError('NO_NEW_SUPERVISION_SPECIFIED')


def validate_branch(root: Path, role: str, source: Path, teacher: Path) -> tuple[Path, dict]:
    if role not in ROLE_OPTIONS:
        raise ValueError('UNKNOWN_AB_ROLE')
    dest = root.expanduser().resolve() / role
    manifest = json.loads((dest / 'ab-experiment.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != SCHEMA or manifest.get('role') != role:
        raise ValueError('AB_BRANCH_IDENTITY_INVALID')
    if sha(Path(__file__)) != manifest['experiment_runner_sha256'] or sha(Path(ordered.__file__)) != manifest['supervision_sha256']:
        raise ValueError('AB_SUPERVISION_CODE_DRIFT')
    if sha(Path(science.__file__)) != manifest['scientific_trainer_sha256'] or sha(Path(science.__file__).with_name('nanojev_hawk_dove_cached_train.py')) != manifest['cached_train_sha256']:
        raise ValueError('AB_TRAINER_CODE_DRIFT')
    if sha(source) != manifest['source_sha256'] or science.teacher_info(teacher)['sha256'] != manifest['teacher_sha256']:
        raise ValueError('AB_DATA_OR_SOURCE_DRIFT')
    if sha(dest / 'config.json') != manifest['config_sha256']:
        raise ValueError('AB_CONFIG_DRIFT')
    import torch
    cp = torch.load(dest / 'latest.pt', map_location='cpu', weights_only=True)
    if cp.get('schema') != science.CHECKPOINT_SCHEMA or cp.get('config') != json.loads((dest / 'config.json').read_text(encoding='utf-8')):
        raise ValueError('AB_CHECKPOINT_LINEAGE_DRIFT')
    stamp = cp.get('ordered_exponent_ab')
    if stamp is None:
        if int(cp['step']) != manifest['donor_step'] or sha(dest / 'latest.pt') != manifest['donor_checkpoint_sha256']:
            raise ValueError('UNSTAMPED_AB_CHECKPOINT_REFUSED')
    elif stamp != checkpoint_stamp(manifest):
        raise ValueError('AB_OBJECTIVE_STAMP_DRIFT')
    return dest, manifest


def checkpoint_stamp(manifest):
    return {'schema': SCHEMA, 'role': manifest['role'],
            'donor_checkpoint_sha256': manifest['donor_checkpoint_sha256'],
            'supervision_sha256': manifest['supervision_sha256'],
            'experiment_runner_sha256': manifest['experiment_runner_sha256'],
            'exponent_distance_weight': manifest['exponent_distance_weight'],
            'log_magnitude_weight': manifest['log_magnitude_weight']}

def train(args):
    dest, manifest = validate_branch(args.root, args.branch, args.source, args.teacher)
    from tools import nanojev_altreg_math_v3_decoder_experiment as v3
    from tools import nanojev_hawk_dove_ordered_exponent as ordered
    import torch
    cp = torch.load(dest / 'latest.pt', map_location='cpu', weights_only=True)
    step = int(cp['step'])
    if args.additional_steps < 1 or args.chunk_steps < 1:
        raise ValueError('INVALID_ADDITIONAL_STEPS')
    target = step + args.additional_steps
    if target > (2**31 - 1):
        raise ValueError('TRAINING_TARGET_OUT_OF_RANGE')
    tokens = ['--cache-memory-mb', str(args.cache_memory_mb), 'train',
              '--teacher', str(args.teacher), '--source', str(args.source),
              '--output', str(dest), '--device', args.device,
              '--steps', str(target), '--chunk-steps', str(args.chunk_steps)]
    from tools import nanojev_hawk_dove_cached_train as cached
    original = v3.objective
    previous_save = torch.save
    def save_with_objective_stamp(obj, file, *a, **kw):
        # Original trainer writes latest.tmp.pt then atomically replaces latest.pt.
        # Embed the objective into every committed optimizer checkpoint so a
        # later uninstrumented resume is detected and rejected by this runner.
        if (str(file).endswith('latest.tmp.pt') and isinstance(obj, dict) and
            obj.get('schema') == science.CHECKPOINT_SCHEMA):
            obj = {**obj, 'ordered_exponent_ab': checkpoint_stamp(manifest)}
        return previous_save(obj, file, *a, **kw)
    torch.save = save_with_objective_stamp
    if args.branch == 'ordered':
        def enhanced(out, true_xy, base, arm, **kwargs):
            return ordered.enhanced_objective(
                original, out, true_xy, base, arm,
                exponent_weight=manifest['exponent_distance_weight'],
                magnitude_weight=manifest['log_magnitude_weight'], **kwargs)
        v3.objective = enhanced
    try:
        print(json.dumps({'event':'ordered_exponent_ab_train_start','branch':args.branch,
              'start_step':step,'target_step':target,'added_steps':args.additional_steps,
              'exponent_weight':manifest['exponent_distance_weight'],
              'magnitude_weight':manifest['log_magnitude_weight']}),flush=True)
        cached.main(tokens)
    finally:
        v3.objective = original
        torch.save = previous_save


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('fork','train','verify'))
    p.add_argument('--donor',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_million_scientific_v2')
    p.add_argument('--root',type=Path,default=Path.home()/'NanoJev/runs/hawk_dove_million_ordered_exponent_ab_v1')
    p.add_argument('--source',type=Path,default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--teacher',type=Path,default=Path('runtime/hawk-dove-million/teacher-scientific-v2.sqlite3'))
    p.add_argument('--branch',choices=ROLE_OPTIONS,default='ordered')
    p.add_argument('--exponent-weight',type=float,default=ordered.DEFAULT_EXPONENT_WEIGHT)
    p.add_argument('--magnitude-weight',type=float,default=ordered.DEFAULT_MAGNITUDE_WEIGHT)
    p.add_argument('--additional-steps',type=int,default=12000)
    p.add_argument('--chunk-steps',type=int,default=300)
    p.add_argument('--cache-memory-mb',type=int,default=1024)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    if args.mode=='fork':
        print(json.dumps(fork(args.donor,args.root,args.source,args.teacher,
                              args.exponent_weight,args.magnitude_weight),sort_keys=True))
    elif args.mode=='verify':
        dest,manifest=validate_branch(args.root,args.branch,args.source,args.teacher)
        print(json.dumps({'status':'PASS','branch':args.branch,'checkpoint':str(dest/'latest.pt'),
                          'weights':(manifest['exponent_distance_weight'],manifest['log_magnitude_weight'])}))
    else:
        train(args)

if __name__=='__main__':
    main()
