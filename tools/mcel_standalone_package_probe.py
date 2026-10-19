#!/usr/bin/env python3
"""Build/test host-HTML-free MCEL packages; fail closed on unimplemented intents.

Use --build-only when Chromium is unavailable.  --structural-only returns 0
solely for a fresh structural browser PASS, never for semantic certification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from main_computer.mcel_application_packages import repository_root
from main_computer.mcel_standalone_package import build_standalone_package, StandaloneProjectionError

REPORT_SCHEMA = "mcel.standalone-package-execution-report.v1"

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass


def _check_assets(directory: Path, manifest: Mapping[str, Any]) -> list[str]:
    problems = []
    for rel, digest in (manifest.get("assetDigests") or {}).items():
        path = (directory / rel).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            problems.append(f"missing-generated-asset:{rel}")
        elif "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append(f"generated-asset-integrity-failed:{rel}")
    return problems


def _browser_probe(output: Path, app_ids: list[str], manifests: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return [{"appId": a, "status": "refused", "reason": f"playwright-unavailable: {exc}"} for a in app_ids]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(output)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    observations = []
    try:
        with sync_playwright() as p:
            choices = [{"headless": True}]
            executable = (os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
                          or os.environ.get("CHROMIUM_EXECUTABLE")
                          or shutil.which("chromium")
                          or shutil.which("chromium-browser")
                          or shutil.which("msedge"))
            if executable:
                choices.append({"headless": True, "executable_path": executable,
                                "args": ["--no-sandbox", "--disable-setuid-sandbox",
                                         "--disable-dev-shm-usage", "--no-proxy-server",
                                         "--proxy-bypass-list=*"]})
            browser = None
            launch_errors = []
            for opts in choices:
                try:
                    browser = p.chromium.launch(**opts)
                    break
                except Exception as exc:
                    launch_errors.append(str(exc))
            if browser is None:
                return [{"appId": a, "status": "refused", "reason": "chromium-unavailable",
                         "detail": " | ".join(launch_errors)[-1200:]} for a in app_ids]
            try:
                for app_id in app_ids:
                    for width in ([1140, 750] if app_id == 'calculator' else [1220, 1050]):
                        page = browser.new_page(viewport={"width": width, "height": 920})
                        requests = []
                        page.on('request', lambda request: requests.append(request.url))
                        base = f"http://127.0.0.1:{server.server_address[1]}/{app_id}/"
                        try:
                            page.goto(base + "index.html", wait_until="networkidle", timeout=25_000)
                            page.wait_for_function("!!window.McelStandalonePackageHost", timeout=25_000)
                            observed = page.evaluate("""() => {
                              const h=window.McelStandalonePackageHost;
                              let refusal = false;
                              try {h.dispatch('not-a-portable-implementation',{});}catch(e){
                                refusal=String(e.message).includes('MCEL_STANDALONE_REFUSED');
                              }
                              return {ready:h.ready===true,structuralVerified:h.structuralVerified===true,
                                fullSemanticRuntimeProven:h.fullSemanticRuntimeProven,
                                nodeIds:h.nodes||[],disabledIntents:h.disabledIntents||[],
                                attemptedDispatchBlocked:refusal,error:h.error||null,
                                viewportWidth:window.innerWidth,
                                documentScrollWidth:document.documentElement.scrollWidth,
                                allDeclaredControlsDisabled:[...document.querySelectorAll('[data-mcel-unimplemented-intent]')]
                                  .every(el=>el.disabled===true)};
                            }""")
                            expected_nodes = []
                            def walk(node: Mapping[str, Any]):
                                expected_nodes.append(node['id'])
                                for child in node.get('children', []):walk(child)
                            walk(manifests[app_id]['layoutTree']['root'])
                            refused = []
                            if not observed.get('ready') or not observed.get('structuralVerified'):
                                refused.append('browser-structural-host-not-ready')
                            if observed.get('nodeIds') != expected_nodes:
                                refused.append('browser-declared-structure-mismatch')
                            if sorted(observed.get('disabledIntents',[])) != sorted(manifests[app_id]['declaredIntents']):
                                refused.append('browser-intent-inventory-mismatch')
                            if not observed.get('allDeclaredControlsDisabled') or not observed.get('attemptedDispatchBlocked'):
                                refused.append('portable-intent-execution-was-not-refused')
                            if observed.get('fullSemanticRuntimeProven') is not False:
                                refused.append('browser-overclaimed-semantic-proof')
                            if observed.get('viewportWidth') != width:
                                refused.append('requested-viewport-not-established')
                            if observed.get('documentScrollWidth', 1e9) > width + 1:
                                refused.append('independent-document-horizontal-overflow')
                            if any(not req.startswith(base) for req in requests):
                                refused.append('outside-package-resource-request')
                            observations.append({'appId': app_id, 'viewport': width,
                                                 'status':'pass' if not refused else 'refused',
                                                 'refusals': refused, 'observed': observed,
                                                 'requestedAssets': requests})
                        except Exception as exc:
                            observations.append({'appId': app_id, 'viewport': width,
                                                 'status': 'refused', 'reason': 'browser-execution-failed',
                                                 'detail': str(exc)[:1000]})
                        finally:
                            page.close()
            finally:
                browser.close()
    except Exception as exc:
        return [{"appId": a, "status": "refused", "reason": "browser-harness-unavailable",
                 "detail": str(exc)[:1000]} for a in app_ids]
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    return observations


def build_report(repo_root: Path, app_ids: list[str], *, browser: bool = True) -> Mapping[str, Any]:
    repo = Path(repo_root).resolve()
    output = repo / 'runtime/build/mcel/standalone-packages'
    manifests = {}
    for app_id in app_ids:
        manifests[app_id] = build_standalone_package(repo, app_id, output)
    asset_errors = {app: _check_assets(output / app, manifest) for app, manifest in manifests.items()}
    observations = _browser_probe(output, app_ids, manifests) if browser else []
    structure_passed = (browser and bool(observations) and
                        all(row['status'] == 'pass' for row in observations) and
                        all(not issues for issues in asset_errors.values()))
    return {
        'schema': REPORT_SCHEMA,
        'scope': 'independent-package-owned-presentation-only',
        'structuralPassed': bool(structure_passed),
        'fullSemanticRuntimeProven': False,
        'passed': False,  # missing portable domain implementations = mandatory refusal
        'status': 'refused',
        'reason': 'required-portable-domain-intent-implementations-missing',
        'applications': [
            {'appId': app_id, 'packageFingerprint': manifests[app_id]['source']['packageFingerprint'],
             'semanticFingerprint': manifests[app_id]['source']['semanticFingerprint'],
             'presentationAuthority': manifests[app_id]['presentationAuthority'],
             'sourceHostAssetsIncluded': False,
             'missingPortableIntents': manifests[app_id]['missingPortableIntents'],
             'assetIntegrityIssues': asset_errors[app_id]}
            for app_id in app_ids],
        'browser': observations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', choices=('calculator', 'code-editor'), action='append')
    parser.add_argument('--repo-root', type=Path, default=None)
    parser.add_argument('--build-only', action='store_true',help='Materialize package presentation assets without Chromium')
    parser.add_argument('--structural-only', action='store_true',help='Exit 0 for independent presentation proof; NOT full semantic proof')
    parser.add_argument('--output', type=Path,default=None)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    apps = sorted(set(args.app or ('calculator','code-editor')))
    try:
        report = build_report(args.repo_root or repository_root(), apps, browser=not args.build_only)
    except (StandaloneProjectionError, ValueError, OSError) as exc:
        report = {'schema':REPORT_SCHEMA,'passed':False,'structuralPassed':False,
                  'fullSemanticRuntimeProven':False,'status':'refused','reason':str(exc)}
    if args.output:
        target = args.output if args.output.is_absolute() else Path.cwd() / args.output
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    if args.json:print(json.dumps(report,indent=2,sort_keys=True))
    else:
        print(f"Standalone MCEL {report['status']}: structuralPassed={report.get('structuralPassed')} "
              f"fullSemanticRuntimeProven={report['fullSemanticRuntimeProven']}")
    if args.build_only:
        return 0 if 'applications' in report and all(not a['assetIntegrityIssues'] for a in report['applications']) else 4
    if args.structural_only and report.get('structuralPassed') is True:
        return 0
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
