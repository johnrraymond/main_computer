"""Stable command-line entry point for translation and acceptance checks."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import TranslationError, translate
from .coverage import main as coverage_main, run_probes
from .feature_cases import PROBES


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'coolify':
        from .coolify import main as coolify_main
        return coolify_main(args[1:])

    if args and args[0] == 'probe':
        parser = argparse.ArgumentParser(
            prog='python -m tools.python3_to_bend2 probe',
            description='Compile and execute one diagnostic feature; not the full acceptance gate.')
        parser.add_argument('feature', choices=tuple(PROBES))
        parser.add_argument('--wsl', action='store_true')
        parser.add_argument('--bend', default='bend')
        opts = parser.parse_args(args[1:])
        result, = run_probes(check_bend=True, bend=opts.bend, wsl=opts.wsl,
                             feature=opts.feature)
        print(f"{result['feature']}: {result['source']} / {result['native']} {result['detail']}")
        print('SINGLE_PROBE_ONLY: full native acceptance not evaluated')
        return 0 if result['native'] == 'VERIFIED' else 2

    if args and args[0] in {'coverage', 'smoke'}:
        command = args.pop(0)
        if command == 'smoke' and not any(x in args for x in ('--require-source-95', '--require-95')):
            args.append('--require-95')
        return coverage_main(args)

    parser = argparse.ArgumentParser(
        prog='python -m tools.python3_to_bend2',
        description='Translate Python 3 to Bend 2. Commands: translate, coverage, smoke, probe, coolify.')
    parser.add_argument('command', choices=['translate'])
    parser.add_argument('file', type=Path, help='Python source file')
    parser.add_argument('-o', '--output', type=Path, help='Bend output file; defaults to stdout')
    parser.add_argument('--show-return', action='store_true')
    parser.add_argument('--gc-threshold', type=int, default=8)
    opts = parser.parse_args(args)
    if opts.gc_threshold < 1:
        parser.error('--gc-threshold must be positive')
    try:
        code = translate(opts.file.read_text(encoding='utf-8'), str(opts.file),
                         show_return=opts.show_return, gc_threshold=opts.gc_threshold)
        if opts.output:
            opts.output.write_text(code, encoding='utf-8')
        else:
            sys.stdout.write(code + ('\n' if not code.endswith('\n') else ''))
    except (OSError, UnicodeError, SyntaxError, TranslationError) as error:
        print(f'TRANSLATION_FAILED: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
