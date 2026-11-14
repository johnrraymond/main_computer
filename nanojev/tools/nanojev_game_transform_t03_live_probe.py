#!/usr/bin/env python3
"""One-command, read-only probe of the latest T03-tiered training checkpoint.

Run from the main_computer repository:

    python -m tools.nanojev_game_transform_t03_live_probe

Unlike editing the tiered generator, this wrapper does not change the SHA sealed
in the active training lineage. It takes an isolated checkpoint snapshot before
invoking the existing oracle-based tier probe and summarizes the results.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
from io import StringIO
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from tools import nanojev_game_transform_t03_tiered as tiered


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_checkpoint(source: Path, directory: Path) -> tuple[Path, int, str]:
    """Copy a whole *published* checkpoint to an independent, validated file.

    The V2 trainer publishes latest.pt via os.replace from latest-tmp.pt.
    Opening latest.pt captures either the old or new complete file, not an
    intermediate serialization. Do not hash the actively changing source.
    """
    import torch

    if not source.is_file():
        raise FileNotFoundError(f'No saved T03 checkpoint at {source}')
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.t03-capture-', suffix='.pt', dir=directory)
    os.close(fd)
    temporary = Path(tmp_name)
    try:
        shutil.copyfile(source, temporary)
        cp = torch.load(temporary, map_location='cpu', weights_only=True)
        if not isinstance(cp, dict) or not isinstance(cp.get('step'), int) or cp['step'] < 1:
            raise RuntimeError('T03_SNAPSHOT_INVALID_STEP')
        if not all(field in cp for field in ('decoder', 'game_operator_head', 'source_sha256', 'database_sha256')):
            raise RuntimeError('T03_SNAPSHOT_INCOMPLETE_CHECKPOINT')
        step = cp['step']
        digest = _sha256(temporary)
        return temporary, step, digest
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def format_summary(report: dict) -> str:
    """Report development/test learning and transfer separately, as percentages."""
    scores = report['scores']
    lines = [f"T03 LIVE PROBE  |  baseline step {report['steps']['baseline']:,}  ->  latest step {report['steps']['trained']:,}",
             'Paired X/Y accuracy, tolerance 10%; pp = percentage-point change',
             f"{'Tier':<15} {'N':>4} {'Before':>8} {'Now':>8} {'Delta':>9} {'X now':>8} {'Y now':>8}"]
    for tier in tiered.EVAL_TIERS:
        splits = ('dev', 'test') if tier != 'T03i' else ('dev', 'test')
        numerator = dict(baseline=0.0, trained=0.0, x=0.0, y=0.0)
        total = 0
        for split in splits:
            old = scores.get(f'baseline/{split}/{tier}', {}).get('0.1')
            new = scores.get(f'trained/{split}/{tier}', {}).get('0.1')
            if old is None or new is None:
                continue
            if old['n'] != new['n']:
                raise RuntimeError(f'T03_PROBE_DENOMINATOR_MISMATCH: {split}/{tier}')
            n = new['n']
            total += n
            for label, score in (('baseline', old['paired_xy']), ('trained', new['paired_xy']),
                                 ('x', new['x']), ('y', new['y'])):
                numerator[label] += n * score
        if not total:
            continue
        b = 100 * numerator['baseline'] / total
        t = 100 * numerator['trained'] / total
        x = 100 * numerator['x'] / total
        y = 100 * numerator['y'] / total
        lines.append(f'{tier:<15} {total:>4} {b:>7.1f}% {t:>7.1f}% {t-b:>+8.1f}pp {x:>7.1f}% {y:>7.1f}%')
    old = scores.get('baseline/game_transfer/T03i', {}).get('0.1')
    new = scores.get('trained/game_transfer/T03i', {}).get('0.1')
    if old and new:
        if old['n'] != new['n']:
            raise RuntimeError('T03_PROBE_TRANSFER_DENOMINATOR_MISMATCH')
        lines.append(f"{'T03i transfer':<15} {new['n']:>4} {100*old['paired_xy']:>7.1f}% {100*new['paired_xy']:>7.1f}% {100*(new['paired_xy']-old['paired_xy']):>+8.1f}pp {100*new['x']:>7.1f}% {100*new['y']:>7.1f}%")
    return '\n'.join(lines)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, default=Path.home() / 'NanoJev/runs/game_transform_t03_tiered',
                   help='Training output directory. Default: active T03-tiered lineage')
    p.add_argument('--checkpoint', type=Path, default=None,
                   help='Optional saved checkpoint to probe, otherwise RUN/latest.pt')
    p.add_argument('--report-dir', type=Path, default=None,
                   help='Report directory (default: RUN/live-probes)')
    p.add_argument('--limit', type=int, default=48)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--dtype', choices=('float16', 'bfloat16', 'float32'), default='float16')
    p.add_argument('--database', type=Path, default=Path('runtime/game-transformation-experiment/game_transformations.sqlite3'))
    p.add_argument('--source-checkpoint', type=Path, default=Path('runtime/game-transform-experiment/source-v4.pt'))
    p.add_argument('--from-output', type=Path, default=Path.home() / 'NanoJev/runs/game_transform_ladder_t02_corrected')
    p.add_argument('--seed', type=int, default=20261015)
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--keep-snapshot', action='store_true',
                   help='Retain the checkpoint snapshot (default: delete after successful or failed probe)')
    return p


def run(args) -> tuple[dict, Path]:
    if args.limit < 1:
        raise ValueError('PROBE_LIMIT_MUST_BE_POSITIVE')
    run_dir = args.run.resolve()
    live = args.checkpoint.resolve() if args.checkpoint else run_dir / 'latest.pt'
    report_dir = (args.report_dir.resolve() if args.report_dir else run_dir / 'live-probes')
    snapshot, step, digest = snapshot_checkpoint(live, report_dir)
    now = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    report_path = report_dir / f't03-step-{step:09d}-{now}-{uuid.uuid4().hex[:8]}.json'
    print(f'Probing saved step {step:,} from {live}', flush=True)
    retained = False
    try:
        argv = ['probe', '--experimental-checkpoint', str(snapshot), '--report', str(report_path),
                '--database', str(args.database), '--source-checkpoint', str(args.source_checkpoint),
                '--from-output', str(args.from_output), '--limit', str(args.limit),
                '--device', args.device, '--dtype', args.dtype, '--seed', str(args.seed)]
        if args.allow_model_download:
            argv.append('--allow-model-download')
        evaluate_args = tiered.parser().parse_args(argv)
        with redirect_stdout(StringIO()):
            report = tiered.evaluate(evaluate_args)
        if report['steps']['trained'] != step or report['checkpoint_sha256'] != digest:
            raise RuntimeError('T03_LIVE_PROBE_SNAPSHOT_IDENTITY_MISMATCH')
        print(format_summary(report), flush=True)
        print(f'Report: {report_path}', flush=True)
        if args.keep_snapshot:
            kept = report_dir / f't03-checkpoint-step-{step:09d}-{digest[:12]}.pt'
            if kept.exists():
                if _sha256(kept) != digest:
                    raise RuntimeError('T03_SNAPSHOT_NAME_COLLISION')
                snapshot.unlink()
            else:
                os.replace(snapshot, kept)
            print(f'Saved checkpoint snapshot: {kept}', flush=True)
            retained = True
        return report, report_path
    finally:
        if snapshot.exists() and not retained:
            snapshot.unlink()


def main(argv=None) -> int:
    # No-argument probe follows the newest V2 lineage once installed. Any
    # explicit V1 arguments retain their original meanings and behavior.
    import sys
    arguments = list(sys.argv[1:] if argv is None else argv)
    v21_root = Path.home() / 'NanoJev/runs/game_transform_t03_tiered_v21'
    v2_root = Path.home() / 'NanoJev/runs/game_transform_t03_tiered_v2'
    if not arguments and (v21_root/'stage-00'/'latest.pt').is_file():
        from tools import nanojev_game_transform_t03_tiered_v21 as v21
        v21.run_probe(v21.parser().parse_args(['probe']))
    elif not arguments and (v2_root/'stage-00'/'latest.pt').is_file():
        from tools import nanojev_game_transform_t03_tiered_v2 as v2
        v2.run_probe(v2.parser().parse_args(['probe']))
    else:
        run(parser().parse_args(arguments))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
