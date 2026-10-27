#!/usr/bin/env python3
"""Recover / pin *exact* source checkpoints for a previously trained QW5 run.

The stage-1 QW5 model stores trainable adapters, not its frozen EMP banks.
A changing Hard EMP latest.pt can therefore invalidate the original lineage.
This command never rewrites recorded SHA, source EMP checkpoints or QW5 weights.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tools import nanojev_qw5 as qw
from tools import nanojev_qw5_join as join

KEYS = ('stage', 'hard', 'left', 'right')


def config_and_live(qw5_run):
    cfg = json.loads((qw5_run/'config.json').read_text(encoding='utf-8'))
    if cfg.get('schema') != join.SCHEMA:
        raise RuntimeError('QW5_CONFIG_SCHEMA_CHANGED')
    files = qw.source_paths(cfg['source']['campaign'], cfg['source']['stage_radius'],
                            hard_state_dir=cfg['source'].get('hard_state_dir', 'hard_emp'),
                            allow_missing=True)
    if set(cfg['source'].get('sha256', {})) != set(KEYS):
        raise RuntimeError('QW5_SOURCE_HASHES_MISSING')
    return cfg, files


def inspect(run):
    cfg, files = config_and_live(run)
    result = {}
    for key in KEYS:
        expected = cfg['source']['sha256'][key]
        live = files[key]
        archived = run/'source_archive'/(key+'.pt')
        live_hash = qw.sha(live) if live.is_file() else None
        archived_hash = qw.sha(archived) if archived.is_file() else None
        result[key] = {'expected_sha256': expected,
                       'live_file': str(live),
                       'live_match': live_hash == expected,
                       'archive_file': str(archived),
                       'archive_match': archived_hash == expected,
                       'recoverable_now': expected in (live_hash, archived_hash)}
    return result


def resolve(run, *, hard_checkpoint=None, search_roots=()):
    cfg, files = config_and_live(run)
    expected = cfg['source']['sha256']
    recovered = {}
    missing = []
    for key in KEYS:
        live = files[key]
        archive = run/'source_archive'/(key+'.pt')
        candidates = [archive, live]
        if key == 'hard' and hard_checkpoint is not None:
            candidates.insert(0, hard_checkpoint)
        recovered[key] = next((p for p in candidates if p.is_file() and qw.sha(p) == expected[key]), None)
        if recovered[key] is None:
            missing.append(key)
    if missing:
        # Searches are opt-in because a NanoJev run directory may contain
        # hundreds of gigabytes of checkpoints. We inspect only .pt files.
        for root in search_roots:
            if not root.is_dir():
                raise FileNotFoundError('QW5_SEARCH_ROOT_MISSING:'+str(root))
            for p in root.rglob('*.pt'):
                if not missing:
                    break
                if not p.is_file():
                    continue
                actual = qw.sha(p)
                for key in missing[:]:
                    if actual == expected[key]:
                        recovered[key] = p
                        missing.remove(key)
            if not missing:
                break
    if missing:
        raise RuntimeError('QW5_ORIGINAL_SOURCE_UNAVAILABLE:' + ','.join(missing) +
                           ':provide a checkpoint with exact original SHA; '
                           'never replace the trained source by a new Hard EMP model')
    # Verify the checkpoint's internal relationship as well as its byte SHA,
    # BEFORE putting source files into an archival run.
    _, provenance = qw.load_five(cfg['source']['campaign'], cfg['source']['ladder_output'],
                                stage=cfg['source']['stage_radius'], device='cpu',
                                hard_state_dir=cfg['source'].get('hard_state_dir','hard_emp'),
                                source_files=recovered)
    if provenance != cfg['source']:
        raise RuntimeError('QW5_RECOVERY_PROVENANCE_MISMATCH')
    return cfg, recovered


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect','recover'))
    parser.add_argument('--qw5-run',type=Path,default=Path.home()/'NanoJev'/'runs'/'qw5_phase1_v1')
    parser.add_argument('--hard-checkpoint',type=Path,
                        help='Restored historical Hard EMP checkpoint, must match the original SHA')
    parser.add_argument('--search-root',type=Path,action='append',default=[],
                        help='Optional directory of older .pt checkpoints or backups; may be large')
    args = parser.parse_args(argv)
    try:
        run = args.qw5_run.expanduser().resolve()
        if args.command == 'inspect':
            print('QW5_SOURCE_INSPECT='+json.dumps(inspect(run),sort_keys=True))
        else:
            cfg, recovered = resolve(run,hard_checkpoint=args.hard_checkpoint,
                                     search_roots=args.search_root)
            output = join.archive_sources(run,cfg,recovered)
            if set(join.verify_sources(cfg,run)) != set(KEYS):
                raise RuntimeError('QW5_RECOVERY_FAILED_VERIFICATION')
            print('QW5_SOURCE_RECOVERED='+json.dumps({key:str(path) for key,path in output.items()},sort_keys=True))
        return 0
    except (RuntimeError,FileNotFoundError,OSError,ValueError) as exc:
        print('QW5_SOURCE_RECOVERY_REFUSED:'+str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
