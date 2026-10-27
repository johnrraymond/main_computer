#!/usr/bin/env python3
"""MCEL Promise Gap Frob v1: independent greenfield authoring barrier discovery.

Diagnostic/read-only with respect to the repository and all live applications.
It uses the public scaffold and DSL compiler, and optional browser observation.
No green promise verdict can be inferred from scaffold or compiler success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import shutil
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_scaffolding import generate_application

TASKS = ROOT / 'tools/fixtures/mcel_promise_gap/tasks.v1.json'
FIXTURES = TASKS.parent
OUTPUT = ROOT / 'runtime/reports/mcel-promise-gap'
SCHEMA = 'mcel.promise-gap-frob.v1'
TASK_PACKET_SHA256 = 'c86298069ccd775f5ecf1e4c0ba69ecef9701e66082c87bf8fccfff7de873f61'

# An executed probe is evidence ONLY for this public API surface and version.
PROBES = (
    ('host-bound-baseline', 'public_host_smoke.js', True, 'public-host-bound-compiler'),
    ('invalid-orphan-action', 'public_invalid_action.js', False, 'composition-refusal'),
    ('keyed-collection', 'public_keyed_collection.js', True, 'public-declarative-list-state'),
    ('record-schema', 'public_record.js', True, 'public-declarative-record-state'),
    ('validated-form', 'public_form.js', True, 'public-declarative-form'),
    ('keyed-surface', 'public_keyed_surface.js', True, 'public-declarative-collection-surface'),
)

GAPS = (
    ('composition', 'Missing declarative composition primitives', (),
     'Expose governed keyed state, forms and row-bound actions through one public authoring contract.'),
    ('functional', 'Unproven end-to-end behavior', ('composition',),
     'Independently drive actual add/update/remove/search/sort/refusal in a running application.'),
    ('effects', 'Missing independent effect and authority evidence', ('functional',),
     'Reconcile operation receipts and forbidden effects using independent proof authority.'),
    ('layout', 'Unproven general application layout', ('composition',),
     'Route both structurally different apps through the same layout authority; prove responsive containment.'),
    ('aesthetics', 'Default appearance not independently reviewed', ('layout',),
     'Evaluate uncorrected application first render with independent human design review.'),
    ('flexibility', 'Contrasting valid designs not demonstrated', ('layout',),
     'Produce opposing variants through the same public contracts without custom framework patches.'),
    ('economy', 'Actual author effort and comparison unmeasured', ('functional', 'aesthetics', 'flexibility', 'effects'),
     'Account for authored decisions, wiring, CSS and repairs; compare with an equally correct baseline.'),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_manifest(path: Path = TASKS) -> dict[str, Any]:
    if path.resolve() != TASKS.resolve() or _sha(path) != TASK_PACKET_SHA256:
        raise ValueError('Task packet changed or substituted; version and pin a new task packet before new trials')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'mcel.promise-gap-tasks.v1' or not manifest.get('policy', {}).get('fixedBeforeTrial'):
        raise ValueError('Invalid or mutable promise-gap task authority')
    tasks = manifest.get('tasks', [])
    if len(tasks) < 2 or len({t['id'] for t in tasks}) != len(tasks) or len({t['appId'] for t in tasks}) != len(tasks):
        raise ValueError('Independent, uniquely identified tasks required')
    for t in tasks:
        if not t.get('semanticRequirements') or not t.get('negativeCases') or len(t.get('aestheticDirections', [])) < 2:
            raise ValueError(f'Task {t["id"]} is missing functional, refusal or flexibility obligations')
    return manifest


def _compiler_probe(probe_id: str, file: str, expect_valid: bool, label: str, *, compiler=compile_dsl_application) -> dict[str, Any]:
    path = FIXTURES / file
    report = compiler(path, write_candidate=False)
    codes = sorted({str(d.get('code') or '') for d in report.diagnostics})
    observed = 'accepted' if report.valid else 'refused'
    # Missing capability is a finding, not a failed harness. A valid negative is a PASS.
    if probe_id == 'invalid-orphan-action':
        status = 'PASS' if not report.valid and 'MCEL_DSL_ACTION_INTENT_REQUIRED' in codes else 'FAIL'
    elif probe_id == 'host-bound-baseline':
        status = 'PASS' if report.valid else 'BLOCKED'
    else:
        if report.valid:
            status = 'AVAILABLE_UNPROVEN'
        elif any(d.get('code') == 'MCEL_DSL_EVALUATION_FAILED' and
                 'is not a function' in str(d.get('problem', ''))
                 for d in report.diagnostics):
            status = 'GAP_OBSERVED'
        else:
            status = 'BLOCKED'
    return {
        'id': probe_id, 'contract': label, 'status': status, 'observation': observed,
        'expectedValid': expect_valid, 'actualValid': bool(report.valid),
        'diagnosticCodes': codes, 'diagnostics': [dict(x) for x in report.diagnostics],
        'sourceSha256': _sha(path), 'source': str(path.relative_to(ROOT)).replace('\\', '/'),
        'scope': 'public mcel.dsl.v1 authoring helper only; does not exclude host-bound/IR or private alternative paths',
        'compilerStatus': report.status,
    }


def _scaffold(task: dict[str, Any], destination_root: Path) -> dict[str, Any]:
    result = generate_application(task['appId'], title=task['title'], output_root=destination_root, dry_run=False)
    target = destination_root / task['appId']
    files = [p for p in target.rglob('*') if p.is_file()]
    html = (target / 'src/index.html').read_text(encoding='utf-8')
    known_counter_template = ('counter-controls' in html and 'increment-control' in html)
    return {
        'status': 'PASS' if result.ok and result.validation.ok else 'FAIL',
        'structuralOnly': True, 'taskAppRendered': False, 'previewIsCounterTemplate': known_counter_template,
        'generatedFileCount': len(files), 'generatedBytes': sum(p.stat().st_size for p in files),
        'generatedSourceSha256': hashlib.sha256(b''.join(p.read_bytes() for p in sorted(files))).hexdigest(),
        'unimplementedTargetRequirements': [r['id'] for r in task['semanticRequirements']],
        'packageRetention': 'temporary-validation-only',
        'interpretation': 'A valid starter package is not an authored inventory/work-orders application or first-render proof.',
    }


def _candidate(source: Path | None, task: dict[str, Any]) -> dict[str, Any]:
    if source is None:
        return {'status': 'UNVERIFIED', 'reason': 'No greenfield authored application source supplied.',
                'taskRequirementsProven': [], 'visibleApplicationProven': False}
    if not source.is_file():
        return {'status': 'BLOCKED', 'reason': 'Specified source does not exist.', 'source': str(source),
                'taskRequirementsProven': [], 'visibleApplicationProven': False}
    data = source.read_text(encoding='utf-8')
    report = compile_dsl_application(source, write_candidate=False)
    expected_app = task['appId']
    correct_identity = report.app_id == expected_app
    # Compile does not prove observed semantics, even when the IR declares intents.
    ir = report.normalized_ir or {}
    return {
        'status': 'COMPILABLE_UNPROVEN' if report.valid and correct_identity else 'FAIL',
        'source': str(source.resolve()), 'sourceSha256': _sha(source),
        'authorSourceNonblankLines': sum(bool(line.strip()) for line in data.splitlines()),
        'authorSourceBytes': len(data.encode('utf-8')), 'compilerStatus': report.status,
        'compilerValid': bool(report.valid), 'appIdentityMatchesPacket': correct_identity,
        'diagnosticCodes': sorted({str(d.get('code')) for d in report.diagnostics}),
        'irDeclarations': {k: len(ir.get(k) or []) for k in
                           ('states', 'intents', 'effects', 'surfaces', 'layouts', 'scenarios')},
        'taskRequirementsProven': [], 'visibleApplicationProven': False,
        'interpretation': 'Compilation proves bounded declaration validity, not task behavior, visual quality or effects.'
    }


def _safe_local_url(url: str) -> bool:
    split = urlsplit(url)
    return split.scheme in ('http', 'https') and split.hostname in ('localhost', '127.0.0.1', '::1') and not split.username


def _chromium(playwright: Any) -> Any:
    """Use Playwright's pinned browser when present; otherwise local Chromium.

    The fallback supports development containers lacking the Playwright download.
    Both paths still run real Chromium, not DOM simulation.
    """
    try:
        return playwright.chromium.launch(headless=True)
    except Exception as first:
        browser_path = shutil.which('chromium') or shutil.which('chromium-browser')
        if not browser_path:
            raise first
        return playwright.chromium.launch(headless=True, executable_path=browser_path,
                                         args=['--no-sandbox'])


@contextmanager
def _local_static_preview(root: Path):
    """Serve disposable scaffold assets on loopback, never production app routes."""
    class QuietStaticHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass
    handler = partial(QuietStaticHandler, directory=str(root))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}/index.html'
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _scaffold_browser(package: Path, task: dict[str, Any], out: Path, widths: list[int]) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'status': 'UNVERIFIED', 'reason': 'Playwright is not installed', 'scope': 'scaffold-only'}
    observations: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = _chromium(p)
            try:
                with _local_static_preview(package / 'src') as preview_url:
                    for width in widths:
                        page = browser.new_page(viewport={'width': width, 'height': 820})
                        try:
                            page.goto(preview_url, wait_until='load')
                            box = page.evaluate('''() => ({
                              scroll:document.documentElement.scrollWidth, viewport:innerWidth,
                              title:document.title,
                              runtime:document.querySelector('[data-mcel-runtime-status]')?.getAttribute('data-mcel-runtime-status') || null,
                              interactive:[...document.querySelectorAll('button,input,select,textarea')].length
                            })''')
                            shot = out / f'{task["id"]}-scaffold-{width}.png'
                            page.screenshot(path=str(shot), full_page=True)
                            observations.append({'width': width, 'geometryPass': box['scroll'] <= width + 1,
                                                 'runtimeState': box['runtime'], 'controls': box['interactive'],
                                                 'screenshot': shot.name, 'title': box['title']})
                        finally:
                            page.close()
            finally:
                browser.close()
    except Exception as exc:
        return {'status': 'BLOCKED', 'reason': f'Chromium scaffold observation failed: {type(exc).__name__}: {exc}',
                'scope': 'scaffold-only'}
    return {'status': 'PASS' if all(x['geometryPass'] for x in observations) else 'FAIL',
            'scope': 'scaffold-only—not target application layout, not aesthetic certification',
            'observations': observations, 'targetFirstRenderVerified': False}


def _live_browser(url: str | None, task: dict[str, Any], out: Path, widths: list[int]) -> dict[str, Any]:
    if not url:
        return {'status': 'UNVERIFIED', 'reason': 'No independent running candidate URL supplied.',
                'taskBehaviorsProven': [], 'aestheticJudgment': 'UNVERIFIED', 'effectProof': 'UNVERIFIED'}
    if not _safe_local_url(url):
        return {'status': 'BLOCKED', 'reason': 'Only explicitly supplied localhost candidate URLs may be observed.',
                'taskBehaviorsProven': [], 'aestheticJudgment': 'UNVERIFIED', 'effectProof': 'UNVERIFIED'}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'status': 'BLOCKED', 'reason': 'Playwright unavailable', 'taskBehaviorsProven': []}
    observations = []
    try:
        with sync_playwright() as p:
            browser = _chromium(p)
            try:
                for width in widths:
                    page = browser.new_page(viewport={'width': width, 'height': 820})
                    try:
                        page.goto(url, wait_until='domcontentloaded', timeout=15000)
                        geometry = page.evaluate('''() => ({
                          scroll:document.documentElement.scrollWidth, viewport:innerWidth,
                          heading:document.querySelector('h1')?.textContent.trim() || '',
                          controls:[...document.querySelectorAll('button,input,select,textarea')].length,
                          title:document.title
                        })''')
                        filename = f'{task["id"]}-live-{width}.png'
                        page.screenshot(path=str(out / filename), full_page=True)
                        observations.append({'width': width, 'geometryPass': geometry['scroll'] <= width + 1,
                                             'controls': geometry['controls'], 'heading': geometry['heading'],
                                             'title': geometry['title'], 'screenshot': filename})
                    finally:
                        page.close()
            finally:
                browser.close()
    except Exception as exc:
        return {'status': 'BLOCKED', 'reason': f'Browser observation failed: {type(exc).__name__}: {exc}',
                'taskBehaviorsProven': []}
    # An HTML screenshot is not evidence of CRUD transitions, canonical state or effect accounting.
    return {'status': 'OBSERVED_UNVERIFIED', 'observations': observations,
            'responsiveGeometryPass': all(x['geometryPass'] for x in observations),
            'taskBehaviorsProven': [], 'aestheticJudgment': 'UNVERIFIED', 'effectProof': 'UNVERIFIED',
            'reason': 'Read-only browser observations; no independent task-operation runner or effect receipts supplied.'}


def _barriers(probes: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {p['id']: p for p in probes}
    public_primitives = ('keyed-collection', 'record-schema', 'validated-form', 'keyed-surface')
    missing = [p for p in public_primitives if by_id[p]['status'] == 'GAP_OBSERVED']
    barriers = []
    for key, title, dependencies, repair in GAPS:
        evidence = []
        status = 'UNVERIFIED'
        if key == 'composition':
            evidence = missing
            if any(by_id[p]['status'] == 'BLOCKED' for p in public_primitives):
                status = 'BLOCKED'
            elif missing:
                status = 'GAP_OBSERVED'
            elif any(t.get('compositionSlice', {}).get('status') == 'PASS' for t in tasks):
                status = 'PARTIAL_VERIFIED'
                evidence = ['inventory-c2:independent-browser-collection-slice',
                            'work-orders-independent:held-out-unverified']
        elif key == 'functional':
            evidence = [f'{t["id"]}:no-complete-task-transition-proof' for t in tasks]
            if any(t.get('compositionSlice', {}).get('status') == 'PASS' for t in tasks):
                status = 'PARTIAL_VERIFIED'
                evidence.insert(0, 'inventory-c2:keyed-CRUD-and-refusal-browser-slice')
        elif key == 'layout':
            evidence = [f'{t["id"]}:no-target-layout-proof' for t in tasks]
        elif key == 'effects':
            evidence = [f'{t["id"]}:no-independent-receipts' for t in tasks]
        elif key == 'aesthetics':
            evidence = [f'{t["id"]}:no-independent-human-review' for t in tasks]
        elif key == 'flexibility':
            evidence = [f'{t["id"]}:unproven-contrasting-variants' for t in tasks]
        elif key == 'economy':
            evidence = ['no-equivalent-correct-baseline-comparison']
        barriers.append({'id': key, 'title': title, 'status': status, 'dependencies': list(dependencies),
                         'evidence': evidence, 'nextGeneralRepair': repair,
                         'qualifier': 'Composition gaps apply to the minimal public DSL path; other mechanisms are not ruled out.' if key == 'composition' else ''})
    return barriers


def _option_map(values: list[str], valid_ids: set[str]) -> dict[str, str]:
    result = {}
    for value in values:
        if '=' not in value:
            raise ValueError('Expected TASK_ID=PATH or TASK_ID=URL')
        key, val = value.split('=', 1)
        if key not in valid_ids or not val or key in result:
            raise ValueError(f'Unknown, empty or repeated task binding: {key}')
        result[key] = val
    return result


def execute(*, tasks_path: Path = TASKS, output: Path = OUTPUT,
            candidates: dict[str, str] | None = None, urls: dict[str, str] | None = None,
            browser: bool = True) -> dict[str, Any]:
    packet = _task_manifest(tasks_path)
    ids = {t['id'] for t in packet['tasks']}
    if set(candidates or {}) - ids or set(urls or {}) - ids:
        raise ValueError('Unknown task ID in candidate or URL')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mcel-promise-gap-') as temp:
        local = Path(temp)
        probes = [_compiler_probe(*item) for item in PROBES]
        cases = []
        for task in packet['tasks']:
            target = task['id']
            scaffold = _scaffold(task, local)
            scaffold_browser = (_scaffold_browser(local / task['appId'], task, output, packet['policy']['viewports'])
                                if browser else {'status': 'UNVERIFIED', 'reason': 'Browser disabled'})
            if target == 'inventory-c2':
                authored_source = Path(candidates[target]) if candidates and target in candidates else FIXTURES / 'inventory_application.js'
            else:
                authored_source = Path(candidates[target]) if candidates and target in candidates else None
            candidate_evidence = _candidate(authored_source, task)
            if target != 'inventory-c2':
                composition_slice = {'status': 'UNVERIFIED', 'reason': 'Held-out task; no implementation supplied'}
            elif candidate_evidence['status'] != 'COMPILABLE_UNPROVEN':
                composition_slice = {'status': 'BLOCKED', 'reason': 'Candidate must compile with the pinned task app ID'}
            elif browser:
                from tools.mcel_collection_browser_acceptance import check
                composition_slice = check(authored_source, output / 'inventory-browser',
                                          tuple(packet['policy']['viewports']))
            else:
                composition_slice = {'status': 'UNVERIFIED', 'reason': 'Chromium not requested'}
            case = {
                'id': target,
                'compositionSlice': composition_slice, 'requirements': [r['id'] for r in task['semanticRequirements']],
                'negativeControls': task['negativeCases'],
                'aestheticDirections': task['aestheticDirections'],
                'scaffold': scaffold, 'scaffoldBrowser': scaffold_browser,
                'candidate': candidate_evidence,
                'liveBrowser': _live_browser(urls.get(target) if urls else None, task, output, packet['policy']['viewports']) if browser
                               else {'status': 'UNVERIFIED', 'reason': 'Browser disabled'},
                'requiredBehavior': {r['id']: ('OBSERVED_BOUNDED' if composition_slice['status'] == 'PASS'
                    and r['id'] in ('keyed-items', 'add-form', 'invalid-refusal', 'crud', 'row-actions',
                                     'search-sort', 'multi-instance', 'responsive') else 'UNVERIFIED')
                    for r in task['semanticRequirements']},
                'designReview': 'UNVERIFIED', 'independentEffects': 'UNVERIFIED',
                'variantsProven': [], 'manualRepairSteps': 'UNMEASURED',
            }
            cases.append(case)
    barriers = _barriers(probes, cases)
    harness_health = 'PASS' if (all(c['scaffold']['status'] == 'PASS' for c in cases)
                                and next(p for p in probes if p['id'] == 'host-bound-baseline')['status'] == 'PASS'
                                and next(p for p in probes if p['id'] == 'invalid-orphan-action')['status'] == 'PASS') else 'FAIL'
    result = {
        'schema': SCHEMA,
        'generatedUtc': datetime.now(timezone.utc).isoformat(),
        'packet': {'path': str(tasks_path), 'sha256': _sha(tasks_path), 'schema': packet['schema'],
                   'taskCount': len(cases), 'policy': packet['policy']},
        'status': 'INCOMPLETE' if harness_health == 'PASS' else 'HARNESS_BLOCKED',
        'harnessHealth': harness_health, 'promiseFulfilled': False,
        'claims': {'minimalAuthoring': 'UNVERIFIED', 'attractiveDefaults': 'UNVERIFIED',
                   'flexibility': 'UNVERIFIED', 'correctComposition': 'UNVERIFIED',
                   'independentApplicationCorrectness': 'UNVERIFIED', 'comparativeAdvantage': 'UNVERIFIED'},
        'probes': probes, 'cases': cases, 'barriers': barriers,
        'releaseAuthority': {'mutatesLiveApplication': False, 'promotesCandidate': False,
                             'changesWebsiteBuilder': False, 'canonicalPromiseReportReplaced': False},
        'limitations': [
            'Patch 5 includes one Inventory DSL candidate but does not autonomously author independent applications.',
            'Inventory browser tests mount the unchanged reusable runtime in real Chromium; local URL navigation is blocked in this environment.',
            'No normal production route or durable canonical ledger is asserted.',
            'Scaffold HTML is a generic Counter template, not a passing Inventory or Work Orders implementation.',
            'Compiler and screenshot observations are not runtime behavior or effect proofs.',
            'Subjective default beauty requires independent human review, which is not simulated.',
            'No author-economy advantage may be inferred before comparable complete correct applications exist.',
        ],
    }
    (output / 'report.json').write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    (output / 'barriers.md').write_text(_markdown(result), encoding='utf-8')
    return result


def _markdown(result: dict[str, Any]) -> str:
    lines = ['# MCEL Promise Gap Frob', '', f'Promise status: **{result["status"]}**. No production certification is implied.',
             '', f'Packet SHA-256: `{result["packet"]["sha256"]}`', '', '## Ordered repair backlog', '']
    for i, barrier in enumerate(result['barriers'], 1):
        lines.extend([f'{i}. **{barrier["id"]} — {barrier["status"]}**: {barrier["nextGeneralRepair"]}',
                      f'   Evidence: {", ".join(barrier["evidence"])}', ''])
    lines.extend(['## Public contract probes', ''])
    for probe in result['probes']:
        lines.append(f'- `{probe["id"]}`: {probe["status"]}; diagnostics: {", ".join(probe["diagnosticCodes"]) or "none"}')
    lines.extend(['', '## Independent application evidence', ''])
    for case in result['cases']:
        lines.extend([f'- `{case["id"]}`: scaffold {case["scaffold"]["status"]}, '
                      f'authored candidate {case["candidate"]["status"]}, '
                      f'collection browser slice {case["compositionSlice"]["status"]}, '
                      f'normal-route live observation {case["liveBrowser"]["status"]}, '
                      f'full task correctness UNVERIFIED'])
    lines.extend(['', '## Certification policy', '', 'Do not turn geometry, scaffold, compiler or synthetic receipts into a green promise. '
                  'Retain the separate canonical promise report and Website Builder status.', ''])
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--focus', choices=('task-manager', 'historical'), default='task-manager',
                        help='Default: existing Task Manager. Historical: frozen Inventory/Work Orders packet')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--tasks', type=Path)
    parser.add_argument('--notebook-source', type=Path, help='Existing frozen Phase 3 notebook DSL (Task Manager focus)')
    parser.add_argument('--notebook-fixture', type=Path, help='Existing frozen Phase 3 read-only fixture (Task Manager focus)')
    parser.add_argument('--candidate', action='append', default=[], metavar='TASK_ID=APPLICATION_JS')
    parser.add_argument('--browser-url', action='append', default=[], metavar='TASK_ID=LOCAL_URL')
    parser.add_argument('--no-browser', action='store_true', help='Skip Chromium; mark visual observations UNVERIFIED')
    parser.add_argument('--require-promise', action='store_true', help='Fail unless all full authoring principles are proved')
    args = parser.parse_args(argv)
    if args.focus == 'task-manager':
        if args.tasks is not None or args.candidate or args.browser_url:
            print('MCEL_PROMISE_GAP_FROB_REFUSED: historical packet/candidate/url arguments require --focus historical',
                  file=sys.stderr)
            return 2
        from tools import mcel_task_manager_promise_frob as task_frob
        target = args.output or task_frob.OUTPUT
        report = task_frob.run(output=target, source=args.notebook_source or task_frob.NOTEBOOK,
                               fixture=args.notebook_fixture or task_frob.FIXTURE,
                               browser=not args.no_browser)
        print('MCEL_PROMISE_GAP_FROB_' + report['status'])
        print('focus: existing-task-manager; file-manager-secondary-if-needed')
        print('host:', report['existingApplication']['status'],
              'phase3:', report['notebookCandidate']['status'],
              'browser:', report['browser']['status'],
              'promise_fulfilled:', report['promiseFulfilled'])
        for barrier in report['orderedBarriers']:
            print('barrier', barrier['id'], barrier['status'],
                  'depends_on=', ','.join(barrier['dependsOn']))
        print('report:', (target / 'report.json').resolve())
        print('barriers:', (target / 'barriers.md').resolve())
        return 1 if report['status'] == 'FAIL' or args.require_promise else 0
    if args.notebook_source or args.notebook_fixture:
        print('MCEL_PROMISE_GAP_FROB_REFUSED: notebook arguments require --focus task-manager', file=sys.stderr)
        return 2
    output = args.output or OUTPUT
    tasks = args.tasks or TASKS
    try:
        packet = _task_manifest(tasks)
        ids = {task['id'] for task in packet['tasks']}
        result = execute(tasks_path=tasks, output=output,
                         candidates=_option_map(args.candidate, ids),
                         urls=_option_map(args.browser_url, ids), browser=not args.no_browser)
    except (ValueError, OSError) as exc:
        print(f'MCEL_PROMISE_GAP_FROB_REFUSED: {exc}', file=sys.stderr)
        return 2
    print('MCEL_PROMISE_GAP_FROB_' + result['status'])
    print('harness:', result['harnessHealth'], 'promise_fulfilled:', result['promiseFulfilled'])
    for p in result['probes']:
        print('probe', p['id'], p['status'], ','.join(p['diagnosticCodes']))
    for b in result['barriers']:
        print('barrier', b['id'], b['status'], 'depends_on=', ','.join(b['dependencies']))
    for case in result['cases']:
        preview = case['scaffoldBrowser']
        live = case['liveBrowser']
        print('case', case['id'], 'scaffold_browser=', preview['status'],
              'candidate=', case['candidate']['status'], 'composition_slice=', case['compositionSlice']['status'],
              'live_browser=', live['status'])
        if preview['status'] == 'BLOCKED':
            print('browser_notice:', case['id'], str(preview.get('reason', '')).splitlines()[0])
        if live['status'] == 'BLOCKED':
            print('browser_notice:', case['id'], str(live.get('reason', '')).splitlines()[0])
    print('report:', (output / 'report.json').resolve())
    print('barriers:', (output / 'barriers.md').resolve())
    if result['harnessHealth'] != 'PASS' or (args.require_promise and not result['promiseFulfilled']):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
