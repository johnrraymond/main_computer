"""Read-only monitor for G2-0061 frozen feature capture.

No torch imports, no CUDA calls, no file mutation, no experiments started.
Run from main_computer/nanojev while learn-capture runs in another terminal.
"""
from __future__ import annotations

import argparse
import math
import shutil
import time
from datetime import datetime
from pathlib import Path


DEFAULT_ROOT = Path('..') / 'runtime' / 'reports' / 'gamer-v1' / 'graph-g2-0061-context-capture'


def _fmt_seconds(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return 'unknown'
    minutes = int(round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes:02d}m' if hours else f'{minutes}m'


def _snapshot(root: Path, target_graphs: int, pairs_per_shard: int) -> str:
    now = time.time()
    stamp = datetime.fromtimestamp(now).strftime('%H:%M:%S')
    if not root.is_dir():
        return f'[{stamp}] CAPTURE_DIRECTORY_NOT_FOUND {root}; check --capture-root'

    # The producer commits immutable .safetensors shards. We stat filenames only;
    # reading a shard during a concurrent write would be unnecessary and risk I/O.
    all_shards = [p for p in root.rglob('*.safetensors') if p.is_file()]
    train = [p for p in all_shards if 'train' in str(p.relative_to(root)).lower()]
    validation = [p for p in all_shards if 'validation' in str(p.relative_to(root)).lower()]
    if not train:
        recent = sorted((p for p in root.rglob('*') if p.is_file()),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:4]
        examples = ', '.join(str(p.relative_to(root)) for p in recent) or '(none)'
        return (f'[{stamp}] No train .safetensors shards recognized yet; '
                f'other safetensors={len(all_shards)}, recent files: {examples}. '
                'Progress cannot be inferred safely from filenames.')

    times = sorted(p.stat().st_mtime for p in train)
    target_pairs = (target_graphs + 1) // 2
    target_shards = math.ceil(target_pairs / pairs_per_shard)
    represented_pairs_est = min(target_pairs, len(train) * pairs_per_shard)
    graph_est = min(target_graphs, represented_pairs_est * 2)
    pct = 100.0 * graph_est / target_graphs
    since_last = now - times[-1]

    # Recent commit interval is a *rough* ETA, not a promise. Resumed runs can
    # include a large pause between the old and new shard timestamps.
    recent = times[-9:]
    gaps = [b - a for a, b in zip(recent, recent[1:]) if b > a]
    if gaps:
        gaps_sorted = sorted(gaps)
        typical_gap = gaps_sorted[len(gaps_sorted) // 2]
        eta = typical_gap * max(0, target_shards - len(train))
        rate = 60 / typical_gap if typical_gap > 0 else None
    else:
        eta = None
        rate = None

    disk = shutil.disk_usage(root)
    rate_text = f'{rate:.2f} shards/min' if rate else 'unknown'
    quiet_note = ' (no new saved shard recently; may still be processing)' if since_last > 300 else ''
    return (f'[{stamp}] TRAIN {len(train)}/{target_shards} saved shards | '
            f'~{graph_est:,}/{target_graphs:,} graphs ({pct:.1f}%) | '
            f'VALIDATION {len(validation)} shards | '
            f'last committed {_fmt_seconds(since_last)} ago{quiet_note} | '
            f'recent rate {rate_text} | ETA ~{_fmt_seconds(eta)} | '
            f'free {disk.free / (1024 ** 3):.1f} GiB')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture-root', type=Path, default=DEFAULT_ROOT)
    ap.add_argument('--train-graphs', type=int, default=5000)
    ap.add_argument('--pairs-per-shard', type=int, default=16)
    ap.add_argument('--interval', type=float, default=30.0)
    ap.add_argument('--once', action='store_true')
    args = ap.parse_args()
    if args.train_graphs < 1 or args.pairs_per_shard < 1 or args.interval <= 0:
        ap.error('train-graphs, pairs-per-shard, and interval must be positive')
    root = args.capture_root.resolve()
    print('Read-only G2 capture probe; no CUDA, no training, no writes. Ctrl+C stops.', flush=True)
    print(f'Capture directory: {root}', flush=True)
    try:
        while True:
            print(_snapshot(root, args.train_graphs, args.pairs_per_shard), flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nProbe stopped. Capture process was not affected.', flush=True)


if __name__ == '__main__':
    main()
