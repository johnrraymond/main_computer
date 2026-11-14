#!/usr/bin/env python3
"""Start/resume a user-weighted T03 ladder phase directly from a finished T02 lineage.

This cutover creates its own output directory without editing either the original
checkpoint or the existing ladder generator, which keeps prior diagnostic SHA
checks valid. The inherited optimizer/RNG/decoder state is resumed by the
existing ladder trainer, not reconstructed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from tools import nanojev_game_transform_ladder_map as ladder
from tools import nanojev_game_transform_experiment as ex

PHASE = 't03_priority'
PROVENANCE = 'cutover-provenance.json'
DEFAULT_SLOTS = 'T03=30,T01=1,T02f=1,T04=2,T05=2,T06=2,T07=2,retention=1'


def parse_slots(text: str) -> dict[str, int]:
    """No implicit T08 and no truncation of the user's requested quotas."""
    result: dict[str, int] = {}
    for field in text.split(','):
        if '=' not in field:
            raise ValueError('SLOT_MUST_BE_LEVEL_EQUALS_INTEGER:'+field)
        name, value = (s.strip() for s in field.split('=', 1))
        if name in result:
            raise ValueError('DUPLICATE_SLOT:'+name)
        if name == 'T08':
            raise ValueError('T08_RESERVED_FOR_HELD_OUT_TRANSFER')
        if name not in ladder.TRAINABLE or not value.isdecimal() or int(value) < 1:
            raise ValueError('INVALID_TRAINING_SLOT:'+field)
        result[name] = int(value)
    if result.get('retention', 0) < 1 or result.get('T03', 0) < 1:
        raise ValueError('T03_AND_RETENTION_REQUIRED')
    if sum(result.values()) > 128:
        raise ValueError('LEVEL_MAP_EXCEEDS_128_LOGICAL_SLOTS')
    return result


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def _input_hashes(args) -> dict:
    inputs = {'source_checkpoint_sha256': args.source_checkpoint,
              'database_sha256': args.database,
              'diagnostic_sha256': args.diagnostic}
    for path in inputs.values():
        if not path.is_file():
            raise FileNotFoundError(str(path))
    # Validate diagnostic against the UNMODIFIED existing trainer generator.
    ladder.check_diagnostic(args.diagnostic, args.database, args.source_checkpoint)
    return {field: ex.filehash(path) for field, path in inputs.items()}


def _validate_ancestor(args, config: dict, cp: dict, hashes: dict) -> int:
    if config.get('schema') != ladder.SCHEMA or config.get('trainer_version') != ladder.TRAINER_VERSION:
        raise RuntimeError('CUTOVER_PARENT_CONFIG_INCOMPATIBLE')
    for field, digest in hashes.items():
        if config.get(field) != digest:
            raise RuntimeError('CUTOVER_PARENT_INPUT_DRIFT:'+field)
    if cp.get('schema') != ladder.SCHEMA or cp.get('trainer_version') != ladder.TRAINER_VERSION:
        raise RuntimeError('CUTOVER_PARENT_CHECKPOINT_INCOMPATIBLE')
    if cp.get('source_sha256') != hashes['source_checkpoint_sha256'] or \
       cp.get('database_sha256') != hashes['database_sha256']:
        raise RuntimeError('CUTOVER_PARENT_CHECKPOINT_INPUT_DRIFT')
    if not all(name in cp for name in ('decoder','game_operator_head','optimizer',
                                        'torch_rng_cpu','phase_history')):
        raise RuntimeError('CUTOVER_PARENT_CHECKPOINT_STATE_INCOMPLETE')
    if not isinstance(config.get('phase_definitions'), dict):
        raise RuntimeError('CUTOVER_PARENT_MISSING_PHASES')
    if not cp['phase_history']:
        raise RuntimeError('CUTOVER_PARENT_MISSING_HISTORY')
    step = int(cp.get('step',0))
    if step <= 0:
        raise RuntimeError('CUTOVER_PARENT_STEP_INVALID')
    return step


def _expected_spec(parent_config: dict, slots: dict) -> dict:
    phases = dict(parent_config['phase_definitions'])
    if PHASE in phases and phases[PHASE] != slots:
        raise RuntimeError('CUTOVER_PHASE_ALREADY_EXISTS_DIFFERENTLY')
    phases[PHASE] = slots
    return {'schema': ladder.SCHEMA,
            'description': 'Explicit T03 priority; T08 always withheld for transfer tests.',
            'phases': phases}


def _validate_existing(args, slots: dict, hashes: dict) -> dict:
    out = args.output
    provenance = _load_json(out / PROVENANCE)
    spec, actual = ladder.read_map(out/'level-map.json', PHASE)
    config = _load_json(out/'ladder-config.json')
    if provenance.get('schema') != 'nanojev.game-transform-phase-cutover.v1' or \
       provenance.get('phase') != PHASE or provenance.get('quotas') != slots or \
       provenance.get('fork_step', 0) < 1:
        raise RuntimeError('CUTOVER_OUTPUT_BELONGS_TO_ANOTHER_LINEAGE')
    if actual != slots or config.get('map_sha256') != ex.filehash(out/'level-map.json') or \
       config.get('phase_definitions') != spec['phases']:
        raise RuntimeError('CUTOVER_OUTPUT_MAP_DRIFT')
    for field, digest in hashes.items():
        if config.get(field) != digest:
            raise RuntimeError('CUTOVER_OUTPUT_INPUT_DRIFT:'+field)
    if not (out/'latest.pt').is_file():
        raise RuntimeError('CUTOVER_CHECKPOINT_MISSING')
    # On resume, leave the checkpoint intact and let ladder.train validate it.
    return config


def prepare(args) -> tuple[dict, bool]:
    args.output = args.output.expanduser().resolve()
    args.from_output = args.from_output.expanduser().resolve()
    args.database = args.database.expanduser().resolve()
    args.source_checkpoint = args.source_checkpoint.expanduser().resolve()
    args.diagnostic = args.diagnostic.expanduser().resolve()
    if args.from_output == args.output:
        raise ValueError('CUTOVER_MUST_HAVE_SEPARATE_OUTPUT')
    if args.output in (args.database.parent, args.source_checkpoint.parent):
        raise ValueError('CUTOVER_OUTPUT_COLLIDES_WITH_SOURCE')
    slots = parse_slots(args.slots)
    hashes = _input_hashes(args)
    if args.output.exists():
        return _validate_existing(args, slots, hashes), False

    src_cp = args.from_output/'latest.pt'
    src_config = args.from_output/'ladder-config.json'
    if not src_cp.is_file():
        raise FileNotFoundError('CUTOVER_PARENT_CHECKPOINT_MISSING:'+str(src_cp))
    parent = _load_json(src_config)
    # Lazy import: --help and static quota validation need no torch runtime.
    import torch
    cp = torch.load(src_cp, map_location='cpu', weights_only=True)
    fork_step = _validate_ancestor(args, parent, cp, hashes)
    if args.steps <= fork_step:
        raise ValueError('STEPS_MUST_EXCEED_FORK_STEP')
    slots = parse_slots(args.slots)
    spec = _expected_spec(parent, slots)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.'+args.output.name+'-stage-',dir=args.output.parent))
    try:
        map_path = temp/'level-map.json'
        map_path.write_text(json.dumps(spec,indent=2)+'\n', encoding='utf-8')
        _, checked = ladder.read_map(map_path, PHASE)
        assert checked == slots
        inherited = dict(parent)
        inherited['map_sha256'] = ex.filehash(map_path)
        inherited['phase_definitions'] = spec['phases']
        (temp/'ladder-config.json').write_text(json.dumps(inherited,indent=2)+'\n',encoding='utf-8')
        shutil.copy2(src_cp,temp/'latest.pt')
        old_hash = ex.filehash(src_cp)
        if ex.filehash(temp/'latest.pt') != old_hash:
            raise RuntimeError('CUTOVER_CHECKPOINT_COPY_CORRUPT')
        provenance = {'schema':'nanojev.game-transform-phase-cutover.v1',
                      'parent_output':str(args.from_output),
                      'parent_checkpoint_sha256':old_hash,
                      'fork_step':fork_step,'phase':PHASE,'quotas':slots,
                      'new_map_sha256':ex.filehash(map_path),
                      'source_checkpoint_sha256':hashes['source_checkpoint_sha256'],
                      'database_sha256':hashes['database_sha256']}
        (temp/PROVENANCE).write_text(json.dumps(provenance,indent=2)+'\n',encoding='utf-8')
        # Never overwrite the parent's completed experiment or an existing run.
        if args.output.exists():
            raise FileExistsError('CUTOVER_OUTPUT_CREATED_CONCURRENTLY')
        temp.rename(args.output)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return inherited, True


def train_from_cutover(args):
    config, created = prepare(args)
    _, quotas = ladder.read_map(args.output/'level-map.json', PHASE)
    if quotas != parse_slots(args.slots):
        raise RuntimeError('CUTOVER_QUOTAS_CHANGED')
    inherited = ['--database',str(args.database),
                 '--source-checkpoint',str(args.source_checkpoint),
                 '--diagnostic',str(args.diagnostic),
                 '--level-map',str(args.output/'level-map.json'),
                 '--phase',PHASE,'--output',str(args.output),
                 '--steps',str(args.steps),'--save-every',str(args.save_every),
                 '--report-every',str(args.report_every),
                 '--max-wall-hours',str(args.max_wall_hours),
                 '--seed',str(config['seed']),'--lr',str(config['lr']),
                 '--op-weight',str(config['op_weight']),
                 '--cache-per-level',str(config['cache_per_level']),
                 '--retention-cache',str(config['retention_cache']),
                 '--refresh-every',str(config['refresh_every']),
                 '--device',args.device,'--dtype',args.dtype]
    if args.allow_model_download:
        inherited.append('--allow-model-download')
    train_args = ladder.parser().parse_args(['train',*inherited])
    print(ex.canon({'event':'ladder_phase_cutover_ready',
                    'created':created,'parent':str(args.from_output),
                    'output':str(args.output),'phase':PHASE,
                    'quotas':quotas,'requested_target_step':args.steps,
                    'retained_optimizer':True,'operator_loss_weight':config['op_weight']}),flush=True)
    result = ladder.train(train_args)
    print(ex.canon({'event':'ladder_map_train_complete',**result}),flush=True)
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-output',type=Path,
                   default=Path.home()/'NanoJev/runs/game_transform_ladder_t02_corrected')
    p.add_argument('--output',type=Path,
                   default=Path.home()/'NanoJev/runs/game_transform_ladder_t03_priority')
    p.add_argument('--database',type=Path,
                   default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--source-checkpoint',type=Path,
                   default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--diagnostic',type=Path,
                   default=Path('runtime/game-transform-experiment/t02-isolation-v2.json'))
    p.add_argument('--slots',default=DEFAULT_SLOTS,
                   help='Integer map; T08 forbidden. E.g. '+DEFAULT_SLOTS)
    p.add_argument('--steps',type=int,default=420000,
                   help='Cumulative optimizer-step target; parent checkpoint is already at step 220000')
    p.add_argument('--save-every',type=int,default=1000)
    p.add_argument('--report-every',type=int,default=500)
    p.add_argument('--max-wall-hours',type=float,default=8.0)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--dtype',choices=('float16','bfloat16','float32'),default='float16')
    p.add_argument('--allow-model-download',action='store_true')
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    train_from_cutover(args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
