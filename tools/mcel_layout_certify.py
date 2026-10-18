#!/usr/bin/env python3
"""Run fresh independent MCEL canonical whole-workspace layout certification."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_layout_certification import SUPPORTED_APPS, run_certification  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--app', choices=SUPPORTED_APPS, action='append', dest='apps')
    parser.add_argument('--output', type=Path, help='Optional JSON report destination (no repo source changes)')
    parser.add_argument('--chromium-executable', help='Chromium executable when Playwright bundled browser is absent')
    parser.add_argument('--with-intent-proof', action='store_true', help='Also run independent host-bound Phase One intent proof')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = run_certification(args.repo_root, app_ids=tuple(args.apps or SUPPORTED_APPS),
                               chromium_executable=args.chromium_executable,
                               with_intent_proof=args.with_intent_proof)
    serialized = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + '\n', encoding='utf-8')
    if args.json:
        print(serialized)
    else:
        print('MCEL canonical layout certification:', report['status'])
        for app in report['applications']:
            print(f"  {app['appId']}: {app['status']}")
            for item in app['scenarios']:
                print(f"    {item['id']}: {item['observed'].get('status')}")
        if report.get('error'): print(report['error'])
        print('Scope:', report['scope'], '(not fullSemanticRuntimeProven)')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
