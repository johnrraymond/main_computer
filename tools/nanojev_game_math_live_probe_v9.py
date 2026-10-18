"""Read live NanoJev V9 training probes or request an independent checkpoint probe.

Runs with the NanoJev Python interpreter from the main_computer repository root.
Does not write model weights, checkpoint state, or training data.

Examples:
  python -m tools.nanojev_game_math_live_probe_v9
  python -m tools.nanojev_game_math_live_probe_v9 --watch --interval 20
  python -m tools.nanojev_game_math_live_probe_v9 --run --checkpoint PATH_TO_LATEST_PT

The --run mode invokes the existing tools.nanojev_game_math_curriculum_v9
'probe' command. No separate model interpretation is implemented here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any

V9_SCHEMA = "nanojev.game-math-xy-coupled-diagnostic.v9"
DEFAULT_RUN = Path.home() / "NanoJev" / "runs" / "game_math_xy_v9"
DEFAULT_TEACHER = Path("runtime/game-math-curriculum-v9/teacher.sqlite3")
DEFAULT_LEGACY = Path("runtime/game-transformation-experiment/game_transformations.sqlite3")
DEFAULT_SOURCE = Path("runtime/game-transform-experiment/source-v4.pt")


def read_report(path: Path) -> dict[str, Any]:
    """Parse an on-disk report only when it is a complete V9 diagnostic."""
    raw = path.read_bytes()
    enc = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    value = json.loads(raw.decode(enc))
    if not isinstance(value, dict) or value.get("schema") != V9_SCHEMA:
        raise ValueError(f"Not a V9 XY diagnostic: {path}")
    if not isinstance(value.get("scores"), dict):
        raise ValueError(f"V9 report lacks scores: {path}")
    return value


def latest_report(folder: Path) -> tuple[Path, dict[str, Any]] | None:
    """Only consider reports that actually parse; ignore files being written."""
    if not folder.is_dir():
        return None
    paths = sorted(folder.rglob("probe*.json"), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
    for path in paths:
        try:
            return path, read_report(path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            continue
    return None


def level_key(name: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"T(\d\d)(.*)", name)
    if not match:
        return (100, 0, name)
    tier = int(match.group(1))
    suffix = match.group(2)
    # Main rung appears before its diagnostic sublayers.
    return (tier, 0 if not suffix else 1, suffix)


def pct(number: Any) -> str:
    return "  n/a" if number is None else f"{float(number) * 100:5.1f}%"


def report_table(data: dict[str, Any], *, min_cases: int = 16, operations: bool = False) -> str:
    threshold = float(data.get("minimum_success_fraction", .8))
    tolerance = float(data.get("tolerance", .05))
    first = data.get("first_failing_level", "unknown")
    focus = data.get("focus", "unknown")
    lines = [
        f"V9 probe: step={data.get('step', '?')} split={data.get('split', '?')} "
        f"focus={focus} first_failing={first}",
        f"Gate: joint X/Y >= {threshold * 100:.0f}% within {tolerance * 100:g}% error; "
        f"min_cases={min_cases}; coupled Z is DIAGNOSTIC ONLY",
        f"{'Level':<9} {'cases':>5} {'X':>7} {'Y':>7} {'joint':>7} {'coupled Z':>10}  Gate",
        "-" * 67,
    ]
    for tier, score in sorted(data['scores'].items(), key=lambda kv: level_key(kv[0])):
        n = int(score.get('scorable', score.get('n', 0)) or 0)
        joint = score.get('joint_xy')
        gate = 'LOW-N' if n < min_cases else (
            'PASS' if joint is not None and float(joint) >= threshold else 'FAIL'
        )
        lines.append(f"{tier:<9} {n:>5} {pct(score.get('x')):>7} {pct(score.get('y')):>7} "
                     f"{pct(joint):>7} {pct(score.get('coupled_Z')):>10}  {gate}")
    if operations:
        byop = data.get('by_operation') or {}
        for tier in (focus, first):
            if tier not in byop:
                continue
            lines += ['', f"Operation breakdown: {tier}",
                      f"{'Operator':<12} {'n':>4} {'X':>7} {'Y':>7} {'joint':>7} {'coupled Z':>10}"]
            for name, score in sorted(byop[tier].items()):
                n = int(score.get('scorable', score.get('n', 0)) or 0)
                lines.append(f"{name:<12} {n:>4} {pct(score.get('x')):>7} "
                             f"{pct(score.get('y')):>7} {pct(score.get('joint_xy')):>7} "
                             f"{pct(score.get('coupled_Z')):>10}")
    if data.get('consecutive_passes') is not None:
        lines.append(f"Reported consecutive passes: {data['consecutive_passes']}")
    lines.append('Note: Z can look correct even when X/Y are wrong (e.g., ARGMAX); do not gate on Z.')
    return '\n'.join(lines)


def choose_checkpoint(folder: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.is_file():
            raise FileNotFoundError(f"Checkpoint does not exist: {explicit}")
        return explicit.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"Run folder not found: {folder}; supply --checkpoint")
    matches = [p for p in folder.rglob('*.pt') if
               p.name == 'latest.pt' or p.name == 'best.pt' or
               p.name.startswith('checkpoint') or p.name.startswith('step-')]
    if not matches:
        raise FileNotFoundError('No saved V9 checkpoint found; supply --checkpoint explicitly. '
                                'Live in-memory weights cannot be probed from another process.')
    return max(matches, key=lambda p: (p.stat().st_mtime_ns, str(p))).resolve()


def snapshot_checkpoint(path: Path, folder: Path, retries: int = 4) -> Path:
    """Read-only snapshot to avoid the training process rewriting a file while probing."""
    target = folder / "checkpoint-snapshot.pt"
    for attempt in range(retries):
        try:
            before = path.stat()
            if before.st_size == 0:
                raise OSError('Empty checkpoint')
            with path.open('rb') as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and \
                    target.stat().st_size == before.st_size:
                return target
        except (OSError, FileNotFoundError):
            pass
        time.sleep(.3 * (attempt + 1))
    raise RuntimeError('Checkpoint changed while copying; retry after the next completed save')


def make_probe_command(*, python: str, teacher: Path, legacy: Path, source: Path,
                       checkpoint: Path, device: str, limit: int) -> list[str]:
    return [python, '-m', 'tools.nanojev_game_math_curriculum_v9', 'probe',
            '--teacher-db', str(teacher), '--legacy-db', str(legacy),
            '--source-checkpoint', str(source), '--parent-checkpoint', str(checkpoint),
            '--probe-limit', str(limit), '--device', device]


def invoke_probe(args: argparse.Namespace) -> int:
    needed = {'teacher database': args.teacher_db, 'legacy database': args.legacy_db,
              'source checkpoint': args.source_checkpoint}
    for label, path in needed.items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing {label}: {path}")
    cp = choose_checkpoint(args.run_dir, args.checkpoint)
    print(f"Saved checkpoint selected: {cp}", flush=True)
    print('This probes a SAVED checkpoint, not the trainer\'s unsaved in-memory weights.', flush=True)
    if args.device == 'cuda':
        print('Warning: independent CUDA probing loads another model alongside training.', flush=True)
    with tempfile.TemporaryDirectory(prefix='nanojev-v9-probe-') as folder:
        input_cp = cp if args.no_snapshot else snapshot_checkpoint(cp, Path(folder))
        cmd = make_probe_command(python=sys.executable, teacher=args.teacher_db,
                                 legacy=args.legacy_db, source=args.source_checkpoint,
                                 checkpoint=input_cp, device=args.device, limit=args.limit)
        before = latest_report(args.run_dir)
        before_key = (str(before[0]), before[0].stat().st_mtime_ns) if before else None
        result = subprocess.run(cmd, check=False)
        if result.returncode:
            print(f"Independent V9 probe exited with code {result.returncode}", file=sys.stderr)
            return result.returncode
        after = latest_report(args.run_dir)
        if after:
            after_key = (str(after[0]), after[0].stat().st_mtime_ns)
            if before_key != after_key:
                print(f"\nReport: {after[0]}\n{report_table(after[1], min_cases=args.min_cases, operations=args.operations)}")
                return 0
        print('Probe exited successfully, but no new report was detected in the configured run folder. '
              'Check the report path printed by V9.', flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = p.add_mutually_exclusive_group()
    action.add_argument('--run', action='store_true', help='Invoke the existing independent V9 model probe')
    action.add_argument('--watch', action='store_true', help='Watch training-generated probe reports; no CUDA used')
    p.add_argument('--run-dir', type=Path, default=DEFAULT_RUN, help='Folder containing V9 reports/checkpoints')
    p.add_argument('--report', type=Path, help='Analyze this exact JSON diagnostic instead of discovering latest')
    p.add_argument('--interval', type=float, default=20, help='Seconds between report checks in --watch mode')
    p.add_argument('--min-cases', type=int, default=16)
    p.add_argument('--operations', action='store_true', help='Show operator breakdown for focus/failing tiers')
    p.add_argument('--checkpoint', type=Path, help='Saved checkpoint to probe in --run mode')
    p.add_argument('--no-snapshot', action='store_true', help='Pass checkpoint in place (avoid unless needed)')
    p.add_argument('--teacher-db', type=Path, default=DEFAULT_TEACHER)
    p.add_argument('--legacy-db', type=Path, default=DEFAULT_LEGACY)
    p.add_argument('--source-checkpoint', type=Path, default=DEFAULT_SOURCE)
    p.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    p.add_argument('--limit', type=int, default=48, help='--probe-limit forwarded to V9')
    args = p.parse_args(argv)
    if args.interval <= 0 or args.min_cases < 1 or args.limit < 1:
        p.error('--interval, --min-cases, and --limit must be positive')
    try:
        if args.run:
            if args.report:
                p.error('--report does not apply to --run')
            return invoke_probe(args)
        if args.report:
            print(f"Report: {args.report}\n{report_table(read_report(args.report), min_cases=args.min_cases, operations=args.operations)}")
            return 0
        last: tuple[str, int] | None = None
        while True:
            current = latest_report(args.run_dir)
            if current:
                path, data = current
                signature = (str(path), path.stat().st_mtime_ns)
                if signature != last:
                    print(f"\nReport: {path}\n{report_table(data, min_cases=args.min_cases, operations=args.operations)}", flush=True)
                    last = signature
            elif not args.watch:
                print(f"No complete V9 probe reports found under {args.run_dir}", file=sys.stderr)
                return 2
            if not args.watch:
                return 0
            if current is None and last is None:
                print(f"Waiting for V9 probe reports in {args.run_dir} ...", flush=True)
                last = ('', 0)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nStopped watching; training was not touched.', flush=True)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Probe error: {exc}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
