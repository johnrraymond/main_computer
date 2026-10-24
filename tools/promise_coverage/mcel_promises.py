#!/usr/bin/env python3
"""Additive MCEL promise coverage view, consuming existing proof authorities.

No claim here can promote an app or override McelAppTruthGate. A normal invocation
runs only the read-only repository audit and reads a local engine probe, if any.
--run-canonical explicitly invokes the established browser/acceptance producers.
"""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

SCHEMA = 'mcel.promise-coverage.v1'
PROBE_SCHEMA = 'mcel.engine-contract-probe.v1'
MANIFEST = Path('contracts/mcel_promise_evidence.v1.json')
PROBE = Path('runtime/reports/mcel-promises/engine-contract-probe.json')
STATUS = {'verified', 'failed', 'unverified', 'blocked', 'out-of-scope'}
REQUIRED_APPS = ('calculator', 'code-editor')


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    canonical = json.dumps(manifest, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def read_object(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def execute(repo: Path, args: list[str], timeout: float) -> dict[str, Any]:
    result: dict[str, Any] = {'command': args, 'status': 'blocked'}
    try:
        proc = subprocess.run(args, cwd=repo, capture_output=True, text=True,
                              timeout=timeout, check=False)
        result.update(exitCode=proc.returncode,
                      status='pass' if proc.returncode == 0 else 'fail',
                      stdoutTail=proc.stdout[-1400:], stderrTail=proc.stderr[-1400:])
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['error'] = str(exc)
        return result


def fresh(iso: Any, now: datetime, max_hours: float) -> bool:
    if not isinstance(iso, str):
        return False
    try:
        moment = datetime.fromisoformat(iso.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            return False
        age = (now - moment).total_seconds()
        return -300 <= age <= max_hours * 3600
    except (TypeError, ValueError, OverflowError):
        return False


def run_readonly_audit(repo: Path, timeout: float, max_age_hours: float = 168) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    cmd = [sys.executable, 'main_computer/mcel_truth_audit.py', '--no-write', '--json',
           '--max-evidence-age-hours', str(max_age_hours)]
    try:
        proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, {'status':'blocked', 'authority':cmd[1], 'error':str(exc)}
    if proc.returncode != 0:
        return None, {'status':'blocked', 'authority':cmd[1],
                      'exitCode':proc.returncode,'stderrTail':proc.stderr[-1200:]}
    try:
        value = json.loads(proc.stdout)
    except ValueError:
        value = None
    if not isinstance(value, dict) or value.get('schema') != 'mcel-repository-truth-audit-v1':
        return None, {'status':'blocked', 'error':'Existing MCEL truth audit returned no valid JSON object.'}
    return value, {'status':'pass', 'authority':cmd[1], 'mode':'read-only', 'schema':value['schema']}


def canonical_acceptance_failures(repo: Path, audit: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose existing acceptance failures without rerunning or re-adjudicating them."""
    path = Path('runtime/reports/mcel-acceptance/mcel-acceptance-report.json')
    evidence = read_object(repo / path)
    result: dict[str, Any] = {'path':path.as_posix(), 'status':'unavailable', 'failedContracts':[]}
    if not audit or not evidence:
        return result
    actual_fp = (evidence.get('repositoryProvenance') or {}).get('fingerprint')
    required_fp = (audit.get('repositoryProvenance') or {}).get('fingerprint')
    if (evidence.get('schema') != 'mcel-acceptance-evidence-report-v1' or
        (evidence.get('evidenceScope') or {}).get('canonical') is not True or
        not actual_fp or actual_fp != required_fp):
        result['status']='refused-noncanonical-or-source-mismatched'
        return result
    result['status']='admitted'
    for app in evidence.get('results') or []:
        if not isinstance(app,dict):
            continue
        for c in app.get('contracts') or []:
            if not isinstance(c,dict) or c.get('enforceable') is not True or c.get('status')=='pass':
                continue
            result['failedContracts'].append({
                'appId':app.get('appId'), 'contractId':c.get('contractId'),
                'status':c.get('status'), 'reason':c.get('reason'),
                'selectors':c.get('selectors') or [],
                'returnCode':c.get('returnCode'),
                'stdoutTail':str(c.get('stdout') or '')[-800:],
                'stderrTail':str(c.get('stderr') or '')[-400:]})
    return result


def canonical_runtime_binding_diagnostics(repo: Path, audit: Mapping[str, Any] | None) -> dict[str, Any]:
    """Describe FLOG browser binding witnesses without promoting their verdicts."""
    path = Path('runtime/reports/flog/mcel-runtime/mcel-runtime-flog-report.json')
    report = read_object(repo / path)
    result: dict[str, Any] = {'path': path.as_posix(), 'status': 'unavailable', 'applications': []}
    if not report or not audit:
        return result
    current = (audit.get('repositoryProvenance') or {}).get('fingerprint')
    observed = (report.get('repositoryProvenance') or {}).get('fingerprint')
    scope = report.get('evidenceScope') or {}
    if not current or observed != current or scope.get('canonical') is not True:
        result['status'] = 'refused-noncanonical-or-source-mismatched'
        return result
    result['status'] = 'admitted'
    for app_id in ('file-explorer', 'git-tools', 'mcel-lab', 'website-builder'):
        trials = [trial for trial in report.get('trials') or []
                  if isinstance(trial, dict) and trial.get('app') == app_id]
        witnesses = [trial.get('semanticBinding') for trial in trials
                     if isinstance(trial.get('semanticBinding'), dict)
                     and trial['semanticBinding'].get('appId') == app_id]
        entry: dict[str, Any] = {'appId': app_id, 'status': 'witness-absent'}
        if len(trials) == 1:
            trial = trials[0]
            probe = trial.get('semanticBindingProbe') or {}
            binding_source = trial.get('semanticBindingSource') or {}
            if not isinstance(probe, dict):
                probe = {}
            if not isinstance(binding_source, dict):
                binding_source = {}
            entry['browserProbe'] = probe
            entry['servedSource'] = binding_source
            if binding_source.get('status') == 'blocked':
                entry['status'] = 'served-source-unbound'
            elif not probe.get('registryPresent'):
                entry['status'] = 'browser-registry-unavailable'
            elif not probe.get('registeredAdapterPresent'):
                entry['status'] = 'browser-adapter-unregistered'
            elif not probe.get('coverageAvailable'):
                entry['status'] = 'browser-binding-coverage-unavailable'
            elif not witnesses:
                entry['status'] = 'browser-binding-witness-missing'
        if len(trials) == 1 and len(witnesses) == 1:
            witness = witnesses[0]
            coverage = witness.get('runtimeBindingCoverage') or {}
            bindings = coverage.get('entries') or []
            if not isinstance(bindings, list):
                bindings = []
            entry = {
                'appId': app_id,
                'status': ('served-source-unbound' if entry.get('servedSource', {}).get('status') != 'exact'
                           else ('bound' if witness.get('runtimeBindingAuditReady') is True and
                                 witness.get('operationalSemanticRuntimeReady') is True else 'unproven')),
                'browserProbe': entry.get('browserProbe', {}),
                'servedSource': entry.get('servedSource', {}),
                'adapterId': witness.get('adapterId'),
                'executableIntentCount': witness.get('executableIntentCount'),
                'runtimeBindingAuditReady': witness.get('runtimeBindingAuditReady') is True,
                'unbound': [
                    {'intentId': binding.get('intentId'), 'bindingSource': binding.get('bindingSource')}
                    for binding in bindings if isinstance(binding, dict) and
                    (binding.get('runtimeBound') is not True or binding.get('status') == 'unbound')
                ],
                'coveragePresent': bool(coverage),
            }
        result['applications'].append(entry)
    return result


def check_browser_source_binding(repo: Path, page_html: str) -> dict[str, Any]:
    """Require the browser-served engine text to contain exact local engine source.

    Applications HTML inlines mcel-engine.js through viewport_pages._expand_includes.
    The page is assembled when viewport_pages is imported, so a server restart is
    necessary after editing the engine. A fresh Playwright context is insufficient.
    """
    source_file = repo / 'main_computer/web/applications/scripts/mcel-engine.js'
    try:
        source = source_file.read_text(encoding='utf-8')
    except OSError as exc:
        return {'status':'blocked', 'reason':'local-engine-source-unreadable', 'error':str(exc)}
    source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
    if not source or source not in page_html:
        return {'status':'blocked', 'reason':'served-engine-source-mismatch',
                'explanation':'Live /applications/mcel-lab HTML is not serving the current repository mcel-engine.js; restart the web server.',
                'localEngineSha256':source_hash,
                'responseHtmlSha256':hashlib.sha256(page_html.encode('utf-8')).hexdigest(),
                'servedHasEngineMarker':'function runContractTests()' in page_html}
    return {'status':'pass', 'reason':'served-engine-exact-source-match',
            'localEngineSha256':source_hash}


def browser_loaded_engine_source(page: Any) -> str:
    """Read the source loaded by Chromium, not the evictable HTTP response body."""
    return page.evaluate("""() => Array.from(document.scripts)
      .map(script => script.textContent || '')
      .find(source => source.includes('function runContractTests()')) || ''
    """)


def capture_engine_probe(repo: Path, base_url: str, timeout: float) -> dict[str, Any]:
    """Invoke the *existing* browser engine contract test authority, not pytest text assertions."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {'status':'blocked','reason':f'Playwright not installed: {exc}'}
    try:
        with sync_playwright() as pw:
            chromium = (os.environ.get('MCEL_CHROMIUM_EXECUTABLE')
                        or os.environ.get('CHROMIUM_EXECUTABLE')
                        or shutil.which('chromium') or shutil.which('chromium-browser'))
            options: dict[str, Any] = {'headless': True}
            if chromium:
                options.update(executable_path=chromium,
                               args=['--no-sandbox', '--disable-dev-shm-usage'])
            browser = pw.chromium.launch(**options)
            try:
                page = browser.new_page()
                response = page.goto(base_url.rstrip('/') + '/applications/mcel-lab',
                          wait_until='domcontentloaded', timeout=int(timeout * 1000))
                if response is None or response.status != 200:
                    return {'status':'blocked', 'reason':'live-applications-route-unavailable',
                            'httpStatus':response.status if response else None}
                # The server inlines this script into the Applications HTML.
                # Reading response.text() can fail for large pages when Chromium
                # evicts the body from its DevTools Network inspector cache.
                # Inspect the script in the *actual loaded document* instead.
                served_engine = browser_loaded_engine_source(page)
                binding = check_browser_source_binding(repo, served_engine)
                if binding['status'] != 'pass':
                    return binding
                page.wait_for_function('typeof window.McelLabEngine !== "undefined"',
                                       timeout=int(timeout * 1000))
                result = page.evaluate('''() => {
                  const report = window.McelLabEngine.runContractTests();
                  return {passed:report.passed, failed:report.failed,
                    failedGuarantees: report.failedGuarantees,
                    uncoveredGuarantees: report.uncoveredGuarantees,
                    guaranteeResults:report.guaranteeResults,
                    tests:report.tests};
                }''')
            finally:
                browser.close()
        if not isinstance(result, dict):
            raise ValueError('Contract engine returned no result object.')
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from main_computer.mcel_evidence_provenance import build_repository_provenance
        payload = {'schema':PROBE_SCHEMA, 'generatedAt':utc_now(),
                   'producer':'McelLabEngine.runContractTests', 'route':'/applications/mcel-lab',
                   'repositoryProvenance':build_repository_provenance(repo),
                   'manifestSha256':manifest_digest(read_object(repo/MANIFEST) or {}),
                   'browserSourceBinding':binding,
                   'result':result}
        path = repo / PROBE
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True)+'\n', encoding='utf-8')
        return {'status':'pass' if result.get('failed') == 0 and not result.get('failedGuarantees')
                and not result.get('uncoveredGuarantees') else 'fail',
                'path':PROBE.as_posix(), 'failed':result.get('failed')}
    except Exception as exc:
        return {'status':'blocked', 'reason':f'Engine browser probe unavailable: {exc}'}


def admitted_probe(probe: Mapping[str, Any] | None, audit: Mapping[str, Any],
                   now: datetime, hours: float, manifest: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], str]:
    if not probe:
        return {}, 'absent'
    if probe.get('schema') != PROBE_SCHEMA or probe.get('producer') != 'McelLabEngine.runContractTests':
        return {}, 'schema-or-producer-mismatch'
    if probe.get('manifestSha256') != manifest_digest(manifest):
        return {}, 'promise-manifest-mismatch'
    source_binding = probe.get('browserSourceBinding') or {}
    if (source_binding.get('status') != 'pass' or
        source_binding.get('reason') != 'served-engine-exact-source-match' or
        not isinstance(source_binding.get('localEngineSha256'), str) or
        len(source_binding['localEngineSha256']) != 64):
        return {}, 'live-engine-source-unbound'
    if not fresh(probe.get('generatedAt'), now, hours):
        return {}, 'stale-or-invalid-timestamp'
    current = (audit.get('repositoryProvenance') or {}).get('fingerprint')
    provenance = probe.get('repositoryProvenance') or {}
    if not current or not provenance.get('fingerprint') or current != provenance.get('fingerprint'):
        return {}, 'repository-fingerprint-mismatch'
    result = probe.get('result') or {}
    entries = result.get('guaranteeResults')
    if not isinstance(entries, list) or not entries or not isinstance(result.get('tests'), list):
        return {}, 'malformed-engine-result'
    seen: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or entry['id'] in seen:
            return {}, 'duplicate-or-invalid-guarantee'
        if not isinstance(entry.get('passed'), bool) or not isinstance(entry.get('supportingTests'), list):
            return {}, 'invalid-guarantee-verdict'
        seen[entry['id']] = entry
    # No unreported failed tests can be ignored by the promise projection.
    if (not isinstance(result.get('failed'), int) or result.get('failed') < 0
            or not isinstance(result.get('passed'), int) or result.get('passed') < 0
            or result['passed'] + result['failed'] != len(result['tests'])):
        return {}, 'invalid-test-counts'
    tests = result['tests']
    by_name = {}
    for test in tests:
        if (not isinstance(test, dict) or not isinstance(test.get('name'), str)
                or not isinstance(test.get('passed'), bool)
                or not isinstance(test.get('guarantees'), list)
                or test['name'] in by_name):
            return {}, 'malformed-or-duplicate-engine-tests'
        by_name[test['name']] = test
    for guarantee_id, guarantee in seen.items():
        names = guarantee['supportingTests']
        expected = {name for name, test in by_name.items()
                    if guarantee_id in test['guarantees']}
        if (not names or len(names) != len(set(names)) or set(names) != expected):
            return {}, 'incorrect-guarantee-test-mapping'
        if guarantee['passed'] != all(by_name[name]['passed'] for name in names):
            return {}, 'inconsistent-test-and-guarantee-outcomes'
    reported_failed = result.get('failedGuarantees')
    reported_uncovered = result.get('uncoveredGuarantees')
    if (not isinstance(reported_failed, list) or not isinstance(reported_uncovered, list)
        or set(reported_failed) != {k for k,v in seen.items() if not v['passed']}
        or reported_uncovered):
        return {}, 'inconsistent-engine-failure-ledger'
    return seen, 'admitted'


def coverage(manifest: Mapping[str, Any], audit: Mapping[str, Any] | None,
             probe: Mapping[str, Any] | None, *, now: datetime | None = None,
             max_age_hours: float = 168) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    entries = manifest.get('promises', [])
    if manifest.get('schema') != 'mcel.promise-evidence-manifest.v1' or not isinstance(entries, list):
        raise ValueError('Invalid promise evidence manifest.')
    ids = [item.get('id') for item in entries]
    if not ids or any(not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError('Promise IDs must be present and unique.')
    if audit is None:
        return {'schema':SCHEMA, 'generatedAt':now.isoformat(), 'status':'blocked',
                'reason':'Independent repository truth audit unavailable.',
                'promises':[{'id':item['id'],'status':'blocked','reason':'No independent repository audit.'}
                            for item in entries]}
    if audit.get('schema') != 'mcel-repository-truth-audit-v1':
        raise ValueError('Unexpected repository truth audit schema.')
    guarantee_results, probe_status = admitted_probe(probe, audit, now, max_age_hours, manifest)
    summary = audit.get('summary') or {}
    inputs = audit.get('evidenceInputs') or {}
    exact = all((inputs.get(kind) or {}).get('repositoryBinding', {}).get('status') == 'exact'
                and (inputs.get(kind) or {}).get('evidenceScope', {}).get('canonical') is True
                and (inputs.get(kind) or {}).get('freshness', {}).get('fresh') is True
                for kind in ('runtime', 'acceptance'))
    app_rows = {a.get('appId'): a for a in audit.get('apps', []) if isinstance(a, dict)}
    enrolled = [a for a in app_rows.values() if a.get('declaredLevel') == 'semantic-runtime']
    output = []
    for entry in entries:
        rule = entry.get('rule')
        item = {'id':entry['id'], 'boundedClaim':entry.get('claim'),
                'nonGuarantees':entry.get('nonGuarantees'),
                'authority':'McelAppTruthGate via repository audit' if rule == 'canonical-app-truth' else (
                    'McelLabEngine.runContractTests' if rule == 'engine-contract' else (
                    'MCEL portable certificate verifier' if rule == 'portable-cert' else 'not yet implemented')),
                'status':'unverified'}
        if rule == 'engine-contract':
            guars = entry.get('guarantees') or []
            item['requiredGuaranteeIds'] = guars
            item['evidence'] = {'path':PROBE.as_posix(), 'admission':probe_status}
            if probe_status == 'admitted' and guars and all(g in guarantee_results for g in guars):
                groups = [guarantee_results[g] for g in guars]
                tests = (probe.get('result') or {}).get('tests') or []
                supporting = {name for group in groups for name in group.get('supportingTests', [])}
                item['evidence']['supportingTests'] = sorted(supporting)
                item['evidence']['failedTests'] = [
                    {'name':test['name'], 'details':test.get('details','')}
                    for test in tests if test.get('name') in supporting and test.get('passed') is False
                ]
                if any(g.get('passed') is False for g in groups):
                    item.update(status='failed', reason='Engine reported a failing guarantee: '+
                        ', '.join(test['name'] for test in item['evidence']['failedTests']))
                elif all(g.get('passed') is True and g.get('supportingTests') for g in groups):
                    item.update(status='verified',reason='Bounded engine contract guarantee passed.')
            if item['status']=='unverified':
                item['reason']='No current admitted engine contract result covering each required guarantee.'
        elif rule == 'canonical-app-truth':
            unproven = sorted(({'appId':a['appId'],
                                'findingCodes':list(a.get('findingCodes') or []),
                                'acceptanceProven':(a.get('claims') or {}).get('acceptanceProven'),
                                'runtimeSurfaceProven':(a.get('claims') or {}).get('runtimeSurfaceProven')}
                               for a in enrolled if (a.get('claims') or {}).get('semanticRuntimeProven') is not True),
                              key=lambda row:row['appId'])
            item['evidence'] = {'authority':'main_computer/mcel_truth_audit.py',
                                'canonicalEvidenceExactAndFresh':exact,
                                'inputs':{kind:{'path':(inputs.get(kind) or {}).get('path'),
                                                'scope':(inputs.get(kind) or {}).get('evidenceScope'),
                                                'freshness':(inputs.get(kind) or {}).get('freshness'),
                                                'repositoryBinding':(inputs.get(kind) or {}).get('repositoryBinding')}
                                          for kind in ('runtime','acceptance')},
                                'enrolled':sorted(a['appId'] for a in enrolled),
                                'unproven':unproven,
                                'semanticRuntimeProven':sorted(a['appId'] for a in enrolled
                                    if (a.get('claims') or {}).get('semanticRuntimeProven') is True)}
            if exact and enrolled:
                if not unproven:
                    item.update(status='verified',reason='Canonical authority proves every enrolled semantic-runtime app.')
                else:
                    item.update(status='failed',reason='Not proven: '+', '.join(a['appId'] for a in unproven))
            else:
                item['reason']='Canonical acceptance/runtime evidence missing, stale, scoped or not source-bound.'
        elif rule == 'portable-cert':
            certificate = audit.get('portableCertification') or {}
            item['evidence'] = {'path':certificate.get('path'),
                                'status':certificate.get('status'),
                                'reasons':list(certificate.get('reasons') or []),
                                'eligibleAppIds':certificate.get('eligibleAppIds')}
            if certificate.get('passed') is True and set(REQUIRED_APPS).issubset(set(certificate.get('eligibleAppIds') or [])):
                item.update(status='verified',reason='Existing verifier accepts bounded portable fixtures for both apps.')
            elif certificate.get('status') not in ('missing', None):
                item.update(status='failed',reason='Portable authority '+str(certificate.get('status'))+
                    ': '+', '.join(certificate.get('reasons') or ['no reasons provided']))
            else:
                item['reason']='Source-bound portable certificate absent.'
        elif rule == 'experiment':
            item['reason']='No existing independent execution result admitted for this broader promise.'
            item['nextExperiment'] = entry.get('nextExperiment')
        else:
            raise ValueError('Unknown promise rule for '+entry['id'])
        assert item['status'] in STATUS
        output.append(item)
    from collections import Counter
    counts = dict(sorted(Counter(item['status'] for item in output).items()))
    overall = 'failed' if counts.get('failed') else ('verified' if counts.get('verified') == len(output) else 'incomplete')
    return {'schema':SCHEMA, 'generatedAt':now.isoformat(), 'status':overall,
            'counts':counts, 'repositoryProvenance':audit.get('repositoryProvenance'),
            'truthAudit':{'schema':audit.get('schema'), 'mode':summary.get('mode'),
                          'status':summary.get('status'), 'evidenceInputs':summary.get('evidenceInputStatuses'),
                          'evidenceBinding':summary.get('evidenceBindingStatuses'),
                          'semanticRuntimeProvenCount':summary.get('semanticRuntimeProvenCount')},
            'engineProbeAdmission':probe_status,
            'promises':output}


def markdown(report: Mapping[str, Any]) -> str:
    lines = ['# MCEL promise coverage', '',
             f"- Coverage status: `{report['status']}`", f"- Generated: `{report['generatedAt']}`",'']
    if report.get('reason'):
        lines += [f"- Blocker: {report['reason']}", '']
    counts = report.get('counts') or {}
    lines.append('- Promise counts: '+', '.join(f'{k}: {v}' for k,v in counts.items()))
    lines += ['', '| Promise | State | Scope / evidence gap |', '|---|---|---|']
    for item in report['promises']:
        details = item.get('reason') or ''
        lines.append(f"| `{item['id']}` | {item['status']} | {details.replace('|','/')} |")
    failures = [item for item in report['promises'] if item['status']=='failed']
    if failures:
        lines += ['', '## Failed-claim diagnostics', '']
        for item in failures:
            lines.append(f"### `{item['id']}`")
            evidence = item.get('evidence') or {}
            if evidence.get('failedTests'):
                for t in evidence['failedTests']:
                    lines.append(f"- Engine test: `{t['name']}`; details: {t.get('details') or '(none)'}")
            for app in evidence.get('unproven') or []:
                codes = ', '.join(app.get('findingCodes') or []) or 'no finding code'
                lines.append(f"- Unproven app `{app['appId']}`: {codes}; acceptance={app.get('acceptanceProven')}; runtime={app.get('runtimeSurfaceProven')}")
            for reason in evidence.get('reasons') or []:
                lines.append(f'- Portable authority: `{reason}`')
            lines.append('')
    accept = report.get('acceptanceDiagnostics') or {}
    if accept.get('status') == 'admitted' and accept.get('failedContracts'):
        lines += ['', '## Failed canonical acceptance contracts', '']
        for c in accept['failedContracts']:
            lines.append(f"- `{c['appId']}` / `{c['contractId']}`: `{c['status']}`; reason: {c.get('reason') or '(not provided)'}")
            if c.get('selectors'):
                lines.append('  - Selectors: '+', '.join(f'`{selector}`' for selector in c['selectors']))
            if c.get('stdoutTail'):
                lines.append('  - Pytest tail: '+str(c['stdoutTail'])[-350:].replace('\n',' | '))
            if c.get('stderrTail'):
                lines.append('  - Error tail: '+str(c['stderrTail'])[-250:].replace('\n',' | '))
        lines.append('')
    elif accept.get('status') == 'refused-noncanonical-or-source-mismatched':
        lines += ['', '- Canonical acceptance report refused for diagnostics: source binding or scope mismatch.', '']
    binding = report.get('runtimeBindingDiagnostics') or {}
    if binding.get('status') == 'admitted':
        lines += ['', '## Browser runtime-binding diagnostics', '']
        for app in binding.get('applications') or []:
            lines.append(f"- `{app['appId']}`: `{app['status']}`; binding audit={app.get('runtimeBindingAuditReady', False)}")
            source = app.get('servedSource') or {}
            if source.get('status') == 'blocked':
                lines.append(f"  - Browser source: `{source.get('reason')}`; missing: {', '.join(source.get('missingSources') or []) or 'not determined'}")
            probe = app.get('browserProbe') or {}
            if probe:
                lines.append(f"  - Registry present={probe.get('registryPresent')}; adapter registered={probe.get('registeredAdapterPresent')}; exported={probe.get('exportedAdapterPresent')}; callable audit={probe.get('exportedAdapterCoverageCallable')}")
                if probe.get('readinessError') or probe.get('coverageError'):
                    lines.append(f"  - Registry error: {probe.get('readinessError') or probe.get('coverageError')}")
                if probe.get('unboundIntentIds'):
                    lines.append(f"  - Unbound intent IDs: {', '.join(map(str, probe['unboundIntentIds']))}")
            for missing in app.get('unbound') or []:
                lines.append(f"  - Unbound intent `{missing.get('intentId')}`: `{missing.get('bindingSource')}`")
        lines.append('')
    elif binding.get('status') == 'refused-noncanonical-or-source-mismatched':
        lines += ['', '- Runtime-binding diagnostics refused: canonical source binding is invalid.', '']
    stages = report.get('stages') or {}
    if stages:
        lines += ['## Existing authority execution', '']
        for name, stage in stages.items():
            lines.append(f"- `{name}`: `{stage.get('status', 'unknown')}`" +
                         (f" (exit {stage['exitCode']})" if 'exitCode' in stage else '')+
                         (f"; reason: `{stage['reason']}`" if stage.get('reason') else '')+
                         (f"; proof scope: `{stage['proofScope']}`; not full semantic-runtime proof by itself; canonical truth gate decides final certification"
                          if stage.get('proofScope') else ''))
        lines.append('')
    lines += ['## Authority boundary', '',
              'The repository audit alone owns application verdicts. This report cannot promote, certify, or replace its gates.',
              'An absent canonical report does not mean a previous live session failed.',
              'All experimental platform claims remain unverified until independent experiments exist.', '']
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo-root',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--run-canonical',action='store_true',help='Run browser FLOG, canonical acceptance, two scoped host-bound proofs and engine contracts before reporting.')
    parser.add_argument('--base-url', default='http://127.0.0.1:8765')
    parser.add_argument('--timeout-seconds',type=float,default=900)
    parser.add_argument('--max-evidence-age-hours',type=float,default=168)
    parser.add_argument('--output',type=Path,help='Optional JSON output; default is no report writes.')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--require-all',action='store_true',help='Return nonzero unless every promise is independently verified.')
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    manifest = read_object(repo/MANIFEST)
    if not manifest:
        parser.error(f'Missing promise manifest: {repo/MANIFEST}')
    run_stages = {}
    source_binding_blocked = False
    if args.run_canonical:
        run_stages['engine-contract'] = capture_engine_probe(repo,args.base_url,args.timeout_seconds)
        source_binding_blocked = run_stages['engine-contract'].get('reason') == 'served-engine-source-mismatch'
        if source_binding_blocked:
            run_stages['canonical-flog'] = {'status':'blocked','reason':'served-engine-source-mismatch'}
        else:
            run_stages['canonical-flog'] = execute(repo,[sys.executable,'main_computer/flog_mcel_runtime_smoke.py',
                                                        '--base-url',args.base_url],args.timeout_seconds)
        run_stages['canonical-acceptance'] = execute(repo,[sys.executable,
            'main_computer/mcel_acceptance_runner.py','--check'],args.timeout_seconds)
        # Calculator and Code Editor declare host-bound runtime projections.
        # They redirect away from mcel-package-host.html, so the generic
        # package-host observation runner cannot prove them. Use their existing
        # fresh IR/browser Phase One authority, whose report deliberately says
        # fullSemanticRuntimeProven=false. Never substitute it for app truth.
        for app_id in ('calculator', 'code-editor'):
            key = 'host-bound-proof-' + app_id
            if source_binding_blocked:
                stage = {'status':'blocked','reason':'served-engine-source-mismatch'}
            else:
                stage = execute(repo,[sys.executable,
                    'tools/mcel_phase1_host_bound_prove.py','--app',app_id,
                    '--check','--semantic-witness'],args.timeout_seconds)
            stage['proofScope'] = 'host-bound-ir-native-authoritative'
            stage['fullSemanticRuntimeProven'] = False  # The separate truth gate makes the final verdict.
            run_stages[key] = stage
        if source_binding_blocked:
            run_stages['release-gate'] = {'status':'blocked','reason':'served-engine-source-mismatch'}
        else:
            run_stages['release-gate'] = execute(repo,[sys.executable,
                'main_computer/mcel_truth_audit.py','--release-gate','--no-write'],args.timeout_seconds)
    audit, audit_stage = run_readonly_audit(repo,args.timeout_seconds,args.max_evidence_age_hours)
    # A failed current probe cannot fall back to a previous successful receipt.
    probe = read_object(repo/PROBE) if not (args.run_canonical and
             run_stages['engine-contract']['status']=='blocked') else None
    report = coverage(manifest,audit,probe,max_age_hours=args.max_evidence_age_hours)
    if source_binding_blocked:
        report['status'] = 'blocked'
        report['reason'] = 'Cannot bind live browser evidence to this repository: server is serving an older or different mcel-engine.js.'
    report['acceptanceDiagnostics'] = canonical_acceptance_failures(repo,audit)
    report['runtimeBindingDiagnostics'] = canonical_runtime_binding_diagnostics(repo,audit)
    report['stages'] = {'readOnlyAudit':audit_stage,**run_stages}
    if args.output:
        path = args.output if args.output.is_absolute() else repo / args.output
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        path.with_suffix('.md').write_text(markdown(report),encoding='utf-8')
    if args.json:
        print(json.dumps(report,indent=2,sort_keys=True))
    else:
        print(markdown(report))
    return 1 if args.require_all and report['status']!='verified' else 0


if __name__=='__main__':
    sys.exit(main())
