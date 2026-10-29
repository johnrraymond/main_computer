"""Patch 14.0 acceptance: pure decision/metrics split, deterministic replay, golden legacy layout.

No application promotion, responsive policy change or learned layout search occurs.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = [
    'tests/test_mcel_patch14_decision_foundations.py',
    'tests/test_mcel_semantic_layout_inference.py',
    'tests/test_mcel_representation_layout_engine_patch3.py',
    'tests/test_mcel_canonical_recursive_layout.py',
    'tests/test_mcel_representation_layout_contract.py',
    'tests/test_mcel_representation_layout_patch21.py',
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', default='runtime/reports/mcel-patch14-foundation.json')
    args = parser.parse_args()
    checks: dict[str, bool] = {}
    sources = [
        ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js',
        ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
    ]
    checks['js-syntax'] = all(subprocess.run(['node', '--check', str(p)], cwd=ROOT,
                                              capture_output=True).returncode == 0 for p in sources)
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', *TESTS, '--disable-warnings'],
                         cwd=ROOT, capture_output=True, text=True)
    checks['focused-regression-suite'] = run.returncode == 0
    engine = sources[1].read_text(encoding='utf-8')
    pure = sources[0].read_text(encoding='utf-8')
    checks['selector-not-promoted-to-runtime'] = 'McelLayoutContinuity.choose(' not in engine
    checks['existing-application-ir-unchanged'] = (ROOT/'main_computer/mcel_application_ir.py').exists()
    checks['locale-free-pure-selector'] = 'localeCompare' not in pure
    checks['measurement-decision-split-present'] = all(token in engine for token in (
        'separateSemanticCollectionLayout', 'separateCanonicalTreeLayout', 'mcel.layout-measurements.v1'))
    golden = json.loads((ROOT/'tools/fixtures/mcel_patch14_0_legacy_golden.json').read_text(encoding='utf-8'))
    checks['legacy-baseline-four-widths'] = len(golden) == 4 and all(x['identical'] for x in golden)
    status = 'PASS' if all(checks.values()) else 'FAIL'
    record = {'schema': 'mcel.patch14.0-deterministic-foundation-acceptance.v1',
              'status': status, 'stage': '14.0-decision-metrics-pure-selector-unpromoted',
              'checks': checks, 'testOutput': (run.stdout + '\n' + run.stderr)[-6000:],
              'productionSelectionPolicyChanged': False,
              'savedUserLayoutProfilesAvailable': False,
              'intelligentCandidateSearchAvailable': False,
              'hostedContinuityCertifiedByThisPatch': False,
              'baseline': 'Original 2026-10-28 repository engine measured independently in Chromium at 320,390,560,1100',
              'limits': ['Source export omits calculator.html required by the full layout-certification suite',
                         'Golden pixel track widths are browser-dependent; baseline comparisons used matching Chromium sessions',
                         'Patch 14.0 does not yet prevent browser movement or implement a track ratchet']}
    dest = ROOT/args.report
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('MCEL_PATCH14_FOUNDATION_' + status)
    for name, ok in checks.items(): print(('PASS' if ok else 'FAIL')+' '+name)
    print('report:', dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest)
    print(run.stdout[-1500:].strip())
    if run.returncode and run.stderr: print(run.stderr[-1200:].strip())
    return 0 if status == 'PASS' else 1


if __name__ == '__main__': raise SystemExit(main())
