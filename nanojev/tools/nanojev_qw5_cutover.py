#!/usr/bin/env python3
"""Start and audit a NEW five-head QW5 lineage after live Hard EMP mutation.

No live checkpoint is changed. Frozen source bank checkpoints are SHA-pinned
inside the new QW5 run BEFORE Stage-1 training. Legacy QW5 / ladder results
remain historical evidence, not transferable certifications. Optional donor
readouts initialize new adapters but still require new Stage-1 training and
fresh zero-line / representation / payoff ladder certification.

Commands: inspect (read-only), prepare (new isolated lineage), verify (read-only).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import torch

from tools import nanojev_qw5 as qw
from tools import nanojev_qw5_join as join

SCHEMA = 'nanojev.qw5.hybrid-beast-cutover.v1'
KEYS = ('stage', 'hard', 'left', 'right')
EXPECTED_OLD_RUN = 'nanojev.qw5.join-training.v1'


def json_file(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def old_witness(path):
    if path is None:
        return None
    root = Path(path).expanduser().resolve()
    config = root / 'config.json'
    checkpoint = root / 'latest.pt'
    if not config.is_file() or not checkpoint.is_file():
        raise FileNotFoundError('QW5_CUTOVER_LEGACY_RUN_INCOMPLETE:' + str(root))
    cfg = json_file(config)
    if cfg.get('schema') != EXPECTED_OLD_RUN:
        raise RuntimeError('QW5_CUTOVER_LEGACY_SCHEMA_DRIFT')
    old = cfg.get('source', {}).get('sha256', {})
    if set(old) != set(KEYS):
        raise RuntimeError('QW5_CUTOVER_LEGACY_SOURCE_HASHES_MISSING')
    return {'directory': str(root), 'config_sha256': qw.sha(config),
            'checkpoint_sha256': qw.sha(checkpoint),
            'source_sha256': old}


def donor_witness(accepted, old):
    if accepted is None:
        return None, None
    if old is None:
        raise ValueError('QW5_CUTOVER_DONOR_REQUIRES_LEGACY_QW5')
    run = Path(accepted).expanduser().resolve()
    config_file, ledger_file, cp_file = (run / x for x in ('config.json', 'ledger.json', 'accepted.pt'))
    if not all(p.is_file() for p in (config_file, ledger_file, cp_file)):
        raise FileNotFoundError('QW5_CUTOVER_DONOR_FILES_MISSING')
    config = json_file(config_file)
    ledger = json_file(ledger_file)
    cp = torch.load(cp_file, map_location='cpu', weights_only=True)
    expected_source = config.get('source', {})
    if (expected_source.get('qw5_config_sha256') != old['config_sha256'] or
            expected_source.get('qw5_checkpoint_sha256') != old['checkpoint_sha256'] or
            Path(expected_source.get('qw5_run', '')).resolve() != Path(old['directory']) or
            ledger.get('passed') != ['Z0', 'Z1', 'Z2'] or
            cp.get('passed') != ledger.get('passed') or
            ledger.get('independent_test_failed') or
            not isinstance(cp.get('trainable'), dict) or
            not cp.get('schema', '').endswith('.accepted')):
        raise RuntimeError('QW5_CUTOVER_DONOR_NOT_AUTHENTIC_ACCEPTED_Z2')
    info = {'directory': str(run), 'config_sha256': qw.sha(config_file),
            'ledger_sha256': qw.sha(ledger_file), 'accepted_sha256': qw.sha(cp_file),
            'accepted_rungs_historical_only': ['Z0', 'Z1', 'Z2']}
    return info, cp['trainable']


def inspect(args):
    old = old_witness(args.legacy_qw5)
    files = qw.source_paths(args.campaign, args.stage, hard_state_dir=args.hard_state_dir)
    current = {key: qw.sha(file) for key, file in files.items()}
    current_vs_old = ({key: current[key] == old['source_sha256'][key] for key in KEYS}
                      if old else None)
    # Loading through QW5's native code is essential: byte existence is not
    # sufficient to establish a valid Hard EMP/ordinary-stage relationship.
    _, provenance = qw.load_five(args.campaign, args.ladder_output, stage=args.stage,
                                 device='cpu', hard_state_dir=args.hard_state_dir)
    if (provenance['sha256'] != current or
            any(qw.sha(files[key]) != current[key] for key in KEYS)):
        raise RuntimeError('QW5_CUTOVER_LIVE_SOURCE_CHANGED_DURING_INSPECTION')
    status = {'schema': SCHEMA, 'source': provenance,
              'live_source_sha256': current, 'old': old,
              'current_matches_old': current_vs_old,
              'fresh_certification_required': True,
              'hybridlink_feedback': 'DISABLED_UNCERTIFIED',
              'qw5_output_authority': 'DISABLED_UNCERTIFIED'}
    print('QW5_CUTOVER_INSPECT=' + json.dumps(status, sort_keys=True), flush=True)
    return status


def prepare(args):
    output = args.output.expanduser().resolve()
    campaign = args.campaign.expanduser().resolve()
    if not args.ladder_output:
        raise ValueError('QW5_CUTOVER_LADDER_OUTPUT_REQUIRED')
    if output.exists():
        raise FileExistsError('QW5_CUTOVER_OUTPUT_ALREADY_EXISTS:' + str(output))
    old = old_witness(args.legacy_qw5)
    donor, inherited = donor_witness(args.warm_start_accepted, old)
    protected = [Path(old['directory'])] if old else []
    if donor:
        protected.append(Path(donor['directory']))
    if any(output == path or output in path.parents or path in output.parents for path in protected):
        raise ValueError('QW5_CUTOVER_OUTPUT_MUST_BE_ISOLATED_FROM_HISTORICAL_RUNS')
    if output == campaign or campaign in output.parents or output in campaign.parents:
        raise ValueError('QW5_CUTOVER_OUTPUT_MUST_BE_OUTSIDE_LIVE_EMP_CAMPAIGN')
    if output == args.ladder_output.resolve() or args.ladder_output.resolve() in output.parents:
        raise ValueError('QW5_CUTOVER_OUTPUT_MUST_BE_OUTSIDE_LADDER_PARENT')
    # This also checks the five-head architecture and Hard EMP/source pairing.
    preflight = inspect(args)
    from types import SimpleNamespace
    prep_args = SimpleNamespace(
        campaign=campaign, ladder_output=args.ladder_output.resolve(),
        hard_state_dir=args.hard_state_dir, stage=args.stage, output=output,
        model=args.model, revision=args.revision, radius=args.radius,
        train_cards=args.train_cards, dev_cards=args.dev_cards, seed=args.seed,
    )
    join.prep(prep_args)
    cfg = json_file(output / 'config.json')
    # Atomic source snapshots were installed by join.prep; validate these
    # before copying donor parameters and before marking cutover ready.
    archive = join.verify_sources(cfg, output)
    if not all((output / 'source_archive' / (key + '.pt')) == archive[key] for key in KEYS):
        raise RuntimeError('QW5_CUTOVER_UNPINNED_SOURCE')
    if cfg['source']['sha256'] != preflight['live_source_sha256']:
        raise RuntimeError('QW5_CUTOVER_SOURCE_CHANGED_DURING_PREPARE')
    initialized = 'fresh'
    if inherited is not None:
        cp_path = output / 'latest.pt'
        cp = torch.load(cp_path, map_location='cpu', weights_only=True)
        model, _, _ = join.restore(output, 'cpu')
        trainables = {name: p for name,p in model.named_parameters() if p.requires_grad}
        if set(inherited) != set(trainables):
            raise RuntimeError('QW5_CUTOVER_DONOR_TRAINABLE_SCHEMA_DRIFT')
        for name, p in trainables.items():
            if inherited[name].shape != p.shape or not bool(torch.isfinite(inherited[name]).all()):
                raise RuntimeError('QW5_CUTOVER_DONOR_TENSOR_INVALID:' + name)
        # Only the QW5 trainable modules are copied. Never inject frozen banks,
        # optimizer state, old acceptance/certification, or old source hashes.
        cp['trained_state'] = {name: inherited[name].detach().cpu().clone() for name in trainables}
        cp['optimizer'] = None
        cp['step'] = 0
        join.write_state(cp_path, cp)
        initialized = 'experimental_donor_adapter_only'
    manifest = {'schema': SCHEMA, 'qw5_run': str(output),
                'source_sha256': cfg['source']['sha256'],
                'qw5_config_sha256': qw.sha(output / 'config.json'),
                'initial_checkpoint_sha256': qw.sha(output / 'latest.pt'),
                'legacy_witness': old, 'donor_witness': donor,
                'initialization': initialized,
                'fresh_stage1_training_required': True,
                'historical_certifications_transferred': [],
                'new_ladder_starts_at': 'Z0',
                'source_snapshots': {key: str(archive[key]) for key in KEYS},
                'hybridlink_feedback': 'DISABLED_UNCERTIFIED',
                'qw5_output_authority': 'DISABLED_UNCERTIFIED'}
    join.write_json(output / 'cutover.json', manifest)
    print('QW5_CUTOVER_PREPARED=' + json.dumps(manifest, sort_keys=True), flush=True)
    return manifest


def verify(args):
    output = args.output.expanduser().resolve()
    manifest = json_file(output / 'cutover.json')
    if manifest.get('schema') != SCHEMA or manifest.get('qw5_run') != str(output):
        raise RuntimeError('QW5_CUTOVER_MANIFEST_DRIFT')
    cfg = json_file(output / 'config.json')
    if (qw.sha(output / 'config.json') != manifest['qw5_config_sha256'] or
            cfg['source']['sha256'] != manifest['source_sha256']):
        raise RuntimeError('QW5_CUTOVER_SOURCE_CONFIG_DRIFT')
    archive = join.verify_sources(cfg, output)
    if any(archive[key] != output / 'source_archive' / (key + '.pt') for key in KEYS):
        raise RuntimeError('QW5_CUTOVER_SOURCE_NOT_PINNED')
    cp = torch.load(output / 'latest.pt', map_location='cpu', weights_only=True)
    if (cp.get('schema') != join.SCHEMA + '.checkpoint' or
            cp.get('source') != cfg['source'] or
            cfg['stages']['hybridlink_feedback'] != 'DISABLED_UNCERTIFIED' or
            cfg['stages']['qw5_output_authority'] != 'DISABLED_UNCERTIFIED' or
            manifest.get('historical_certifications_transferred') != []):
        raise RuntimeError('QW5_CUTOVER_STAGE_OR_CHECKPOINT_DRIFT')
    # The initial checkpoint hash is fixed only before Stage-1 learning.
    if cp['step'] == 0 and qw.sha(output / 'latest.pt') != manifest['initial_checkpoint_sha256']:
        raise RuntimeError('QW5_CUTOVER_INITIAL_WEIGHTS_DRIFT')
    status = {'qw5_run': str(output), 'step': cp['step'],
              'initialization': manifest['initialization'],
              'new_stage1_trained': cp['step'] > 0,
              'source_snapshots_verified': True,
              'fresh_ladder_starts_at': 'Z0',
              'old_certifications_carried': False,
              'hybridlink_feedback': 'DISABLED_UNCERTIFIED',
              'output_authority': 'DISABLED_UNCERTIFIED'}
    print('QW5_CUTOVER_VERIFIED=' + json.dumps(status, sort_keys=True), flush=True)
    return status


def parser():
    from tools import nanojev_qwen_hybridagent as hybrid
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=('inspect', 'prepare', 'verify'))
    p.add_argument('--campaign', type=Path, default=Path.home() / 'NanoJev' / 'runs' / 'nanojev_emp_dual_bank_outward_v1')
    p.add_argument('--ladder-output', type=Path, default=Path.home() / 'NanoJev' / 'runs' / 'nanojev_zero_line_ladder_v1')
    p.add_argument('--hard-state-dir', default='hard_emp_frontier_v2')
    p.add_argument('--stage', type=int, default=None)
    p.add_argument('--legacy-qw5', type=Path, default=None,
                   help='Read-only historical QW5 run (its obsolete hard SHA is NOT required to exist)')
    p.add_argument('--warm-start-accepted', type=Path, default=None,
                   help='Optional historical Z2 accepted.pt: copy trainable adapters ONLY; no certificates/optimizer')
    p.add_argument('--output', type=Path, default=Path.home()/'NanoJev'/'runs'/'qw5_hybridbeast_cutover_v2')
    p.add_argument('--model', default=hybrid.MODEL_ID)
    p.add_argument('--revision', default=None)
    p.add_argument('--radius', type=int, default=512)
    p.add_argument('--seed', type=int, default=20261026)
    p.add_argument('--train-cards', type=int, default=256)
    p.add_argument('--dev-cards', type=int, default=64)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'inspect':
            inspect(args)
        elif args.command == 'prepare':
            prepare(args)
        else:
            verify(args)
        return 0
    except (RuntimeError, FileNotFoundError, FileExistsError, OSError, ValueError, KeyError) as exc:
        print('QW5_CUTOVER_REFUSED:' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
