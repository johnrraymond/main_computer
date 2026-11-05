"""Read-only V1 four-app production-parity evidence inventory.

A source declaration proves neither a live effect nor the quality of a design.
Keep those obligations open until real hosted acceptance. This is not a
promotion/certification command and never mutates an application.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from main_computer.mcel_calculator_expanded_host import compile_expanded
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runtime/reports/mcel-semantic-reference-parity/report.json'

# Current production code is the comparison baseline. The older host-bound
# semantic packages are NOT V1 qualification candidates.
APPLICATIONS = {
    'file-manager': {
        'production_route': '/applications/file-explorer',
        'semantic_route': '/applications/mcel-semantic-file-manager',
        'production_sources': [
            'main_computer/web/applications/apps/file-explorer.html',
            'main_computer/web/applications/scripts/file-explorer.js',
        ],
        'features': {
            'roots': (('load-roots', 'choose-root'), ('intent:load-roots',)),
            'nested-directory-navigation': (('enter-directory',), ('intent:enter-directory',)),
            'parent-navigation': (('enter-directory',), ('data-mcel-parent-directory',)),
            'retained-folder-hierarchy': (('enter-directory',), ('data-mcel-folder-tree',)),
            'directory-listing': (('browse',), ('intent:browse',)),
            'current-folder-search': (('search',), ('relative_path:lastListing?.',)),
            'file-preview': (('preview-entry',), ('intent:preview-entry',)),
            'suggested-app-handoff': (('open-with',), ('intent:open-with',)),
        },
        'authorized_effects': {'external-read'},
        'live_gaps': ['real roots, file counts/identities, and Windows filesystem scope',
                      'actual application handoff and human interface approval'],
    },
    'task-manager': {
        'production_route': '/applications/task-manager',
        'semantic_route': '/applications/mcel-semantic-task-manager',
        'production_sources': [
            'main_computer/web/applications/apps/task-manager.html',
            'main_computer/web/applications/scripts/task-manager.js',
        ],
        'features': {
            'process-observation': (('refresh',), ('intent:refresh',)),
            'four-notebook-perspectives': (('switch-view', 'refresh-all', 'refresh-connections', 'refresh-hardware'), ('data-mcel-mode-tabs',)),
            'continuous-selected-view-refresh': (('refresh', 'refresh-all', 'refresh-connections', 'refresh-hardware'), ('selectedMonitorIntent', 'data-mcel-auto-refresh')),
            'server-status': (('server-status',), ('intent:server-status',)),
            'process-terminate-kill': (('terminate-process', 'kill-process'), ('intent:terminate-process', 'intent:kill-process')),
            'server-lifecycle': (('start-server', 'restart-server', 'shutdown-server'), ('intent:shutdown-server',)),
            'schedule-authority': (('list-schedules', 'choose-schedule', 'create-schedule', 'run-schedule', 'delete-schedule'), ('observedSchedules',)),
            'ai-briefing': (('task-ai',), ('intent:task-ai',)),
        },
        'authorized_effects': {'external-read', 'consequential'},
        'live_gaps': ['real Windows PowerShell controller target fidelity and side effects',
                      'live process-generation, scheduling, telemetry, provider and human design approval'],
    },
    'code-editor': {
        'production_route': '/applications/code-editor',
        'semantic_route': '/applications/mcel-semantic-code-editor',
        'production_sources': [
            'main_computer/web/applications/apps/code-editor.html',
            'main_computer/web/applications/scripts/code-editor.js',
            'main_computer/web/applications/scripts/code-editor-monaco-adapter.js',
            'main_computer/web/applications/scripts/code-editor-mcel-studio.js',
            'main_computer/web/applications/scripts/code-editor-documentation-viewport.js',
            'main_computer/web/applications/scripts/code-editor-aider-actions.js',
            'main_computer/web/applications/scripts/code-editor-aider-context.js',
            'main_computer/web/applications/scripts/code-editor-aider-output.js',
            'main_computer/web/applications/scripts/code-editor-file-map.js',
            'main_computer/web/applications/scripts/code-editor-layout-contract.js',
        ],
        'features': {
            'monaco-editing': (('open-source', 'save-source'), ('MainComputerMonacoAdapter',)),
            'project-navigation': (('list-files', 'browse-directory', 'select-file'), ('data-mcel-folder-tree',)),
            'independent-document-drafts': (('open-source', 'save-source'), ('data-mcel-document-tabs',)),
            'guarded-save': (('save-source',), ('intent:save-source',)),
            'reviewed-project-changes': (('prepare-reviewed-change', 'apply-reviewed-change'), ('intent:apply-reviewed-change',)),
            'aider-preview-and-run': (('preview-aider-plan', 'run-aider'), ('intent:run-aider',)),
            'aider-history-and-jobs': (('aider-context', 'aider-jobs', 'archive-aider-context', 'reset-aider-context', 'load-aider-archive'), ('pendingAiderJobs',)),
            'component-docs-manifest-read': (('list-component-docs', 'read-component-doc'), ('/api/applications/component-docs/read', 'observedComponentDocs')),
            'scm-definition-readonly-inspection': (('inspect-scm-component-contract','inspect-scm-route-contract'), ('McelCodeStudioScm','scm-component-definition')),
            'studio-source-checks-and-serialization-preview': (('check-studio-draft','preview-studio-serialization'), ('studio-source-static-checks','scmSerializationGate')),
            'documentation-viewport-config-read': (('read-component-doc-viewport-config',), ('/api/applications/component-docs/viewport-config',)),
            'studio-scm-route-and-draft-lifecycle': (('studio-runtime-route-enter','studio-runtime-stage',
                'studio-runtime-commit','studio-runtime-route-leave','studio-runtime-replay'),
                ('scm.enterRoute','scm.leaveRoute','scm.transition(instance,"commitDraft"')),
            'sandboxed-documentation-pixel-script': (('run-component-doc-script',), ('McelSandboxedViewport.run',)),
            'scm-real-kernel-proof-inspection': (('studio-proof-list','studio-proof-inspect','studio-proof-export'),
                ('scm.exportEvidence','scm.exportRouteEvidence','mcel-semantic-scm-proof-packet')),
            'scm-isolated-kernel-regressions': (('studio-regression-run',),
                ('kernelRegressionFixtures','legacyDomFixtures:"not-executed"')),
            'scm-evidence-grounded-ai-repair-proposal': (('studio-repair-packet',),
                ('mcel-semantic-scm-ai-repair-proposal','aiProviderCalled:false')),
        },
        'authorized_effects': {'external-read', 'source-write', 'consequential'},
        'live_gaps': ['actual Windows Monaco lifecycle and multi-file source persistence',
                      'live Aider execution, reviewed project transactions and human design approval'],
        'additional_production_workflows_to_trace': {
            'MCEL Studio validate/serialize': ('code-studio-validate', 'code-studio-serialize'),
            'MCEL Studio runtime workspace lifecycle': ('code-studio-mount-runtime', 'code-studio-repair-runtime'),
            'SCM evidence and repair tools': ('code-studio-run-scm-regression-harness', 'code-studio-generate-scm-repair-prompt'),
            'Documentation viewport tools': ('code-editor-doc-load', 'code-editor-doc-script-run'),
        },
    },
    'calculator': {
        'production_route': '/applications/calculator',
        'semantic_route': '/applications/mcel-semantic-calculator-expanded',
        'production_sources': [
            'main_computer/web/applications/apps/calculator.html',
            'main_computer/web/applications/scripts/calculator.js',
            'main_computer/web/applications/scripts/calculator-core.js',
            'main_computer/web/applications/scripts/calculator-capabilities.js',
        ],
        'features': {
            'deterministic-arithmetic': (('evaluate',), ('intent:evaluate',)),
            'expression-keypad': (('evaluate',), ('data-mcel-expression-pad',)),
            'graph-and-discontinuity': (('draw-graph',), ('intent:draw-graph', 'Do not interpolate across missing')),
            'symbolic-mathics': (('evaluate-symbolic',), ('intent:evaluate-symbolic',)),
            'arithmetic-graph-symbolic-assistance': (('ask-arithmetic-model', 'ask-graph-model', 'ask-symbolic-model'), ('intent:ask-symbolic-model',)),
            'result-question': (('ask-result-question',), ('intent:ask-result-question',)),
            'mode-switching': (('switch-mode',), ('intent:switch-mode',)),
        },
        'authorized_effects': {'external-read'},
        'live_gaps': ['hosted Calculator projection fingerprint and full live feature parity',
                      'real Mathics/model service, all production modes and human design approval'],
    },
}


def run(repo: Path = ROOT) -> dict:
    runtime = (repo/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text(encoding='utf-8')
    scoped_port = (repo/'main_computer/web/applications/scripts/mcel-semantic-read-ports.js').read_text(encoding='utf-8')
    calc_port = (repo/'main_computer/web/applications/scripts/mcel-calculator-expanded-port.js').read_text(encoding='utf-8')
    router = (repo/'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8')
    host_other = (repo/'main_computer/mcel_semantic_other_apps_host.py').read_text(encoding='utf-8')
    host_calc = (repo/'main_computer/mcel_calculator_expanded_host.py').read_text(encoding='utf-8')
    checks = {}
    applications = {}
    for app, spec in APPLICATIONS.items():
        ir = compile_expanded(repo) if app == 'calculator' else compile_semantic_app(app, repo)
        app_id = ir['application']['appId']
        intents = {item['id'].removeprefix('intent:') for item in ir['intents']}
        source_files = ir['provenance']['frontend']['sourceFiles']
        effects = ir['effects']
        checks[f'{app}.paired-source'] = len(source_files) == 2 and bool(ir['surfaces'][0]['interfaceIntent']['hierarchy']) and not ir['layouts']
        checks[f'{app}.effect-risk-scoped'] = {effect['risk'] for effect in effects} == spec['authorized_effects']
        checks[f'{app}.candidate-route-declared'] = spec['semantic_route'] in router
        # The paired apps and the Calculator differ only in the allowed
        # backend adapter; the same operational runtime owns presentation.
        host_text = host_calc if app == 'calculator' else host_other
        checks[f'{app}.shared-runtime-host'] = 'mcel-semantic-operational-runtime.js' in host_text
        adapter = calc_port if app == 'calculator' else scoped_port
        combined = '\n'.join((runtime, adapter, host_text))
        features = {}
        for feature, (required_intents, evidence) in spec['features'].items():
            present = all(intent in intents for intent in required_intents) and all(token in combined for token in evidence)
            features[feature] = 'DECLARED_NOT_LIVE_VERIFIED' if present else 'MISSING_SOURCE_CONTRACT'
            checks[f'{app}.{feature}.source'] = present
        source_presence = {path:(repo/path).is_file() for path in spec['production_sources']}
        production_baseline = None
        if app == 'calculator':
            # Calculator's incumbent HTML is generated from the canonical
            # mcel_apps/calculator/application.js at server startup; a
            # checked-in calculator.html is not its presentation authority.
            from tools.mcel_calculator_production_baseline_diagnostic import inspect_baseline
            production_baseline = inspect_baseline(repo)
        projection_verified = bool(production_baseline and
            production_baseline['canonical_projection_present'] and
            production_baseline['status'] in {
                'PROJECTED_SOURCE_BASELINE_READY_FOR_PARITY_REVIEW',
                'SOURCE_BASELINE_READY_FOR_PARITY_REVIEW',
            })
        missing_baseline = [path for path, present in source_presence.items()
            if not present and not (app == 'calculator' and projection_verified and
                                  path == 'main_computer/web/applications/apps/calculator.html')]
        # These existing production controls are outside the small core-editor
        # parity inventory. They are evidence leads, not automatically approved
        # exclusions or proof that their behavior is missing. Trace each to a
        # working implementation before determining a replacement obligation.
        markup_path = repo/'main_computer/web/applications/apps/code-editor.html'
        markup = markup_path.read_text(encoding='utf-8') if app == 'code-editor' and markup_path.is_file() else ''
        qualifications = {
            'MCEL Studio validate/serialize': 'BOUNDED_DOCUMENT_SCM_KERNEL_NOT_FULL_WORKBENCH',
            'MCEL Studio runtime workspace lifecycle': 'BOUNDED_DOCUMENT_ROUTE_AND_DRAFT_NO_HOST_COMMIT',
            'SCM evidence and repair tools': 'BOUNDED_KERNEL_EVIDENCE_REPLAY_NOT_FULL_STUDIO_PROOF_DOCK',
            'Documentation viewport tools': 'BOUNDED_OPAQUE_ORIGIN_PIXEL_SCRIPT_NOT_FULL_VIEWPORT_PARITY',
        }
        unmapped_controls = {name:{'control_ids':list(ids), 'visible_in_production_html':all(id in markup for id in ids),
                                   'qualification':qualifications.get(name,'REQUIRES_FEATURE_PARITY_DECISION')
                                   if app=='code-editor' else 'REQUIRES_FEATURE_PARITY_DECISION'}
                             for name,ids in spec.get('additional_production_workflows_to_trace',{}).items()}
        if app == 'code-editor':
            checks['code-editor.viewport-sandbox-isolated-asset'] = (
                (repo/'main_computer/web/applications/scripts/mcel-sandboxed-viewport.js').is_file()
                and 'mcel-sandboxed-viewport.js' in host_other)

        # A missing production baseline is not a compiler defect and does not
        # make the replacement or its operations automatically incomplete.
        applications[app] = {
            'production_route': spec['production_route'],
            'semantic_route': spec['semantic_route'],
            'production_sources': source_presence,
            'production_baseline_complete_in_export': not missing_baseline,
            'production_baseline_missing': missing_baseline,
            'production_baseline_provenance': (production_baseline['baseline_provenance']
                if production_baseline is not None else 'checked-in-production-sources'),
            'production_projection_binding_ids_valid': (bool(projection_verified)
                if production_baseline is not None else None),
            'source_feature_evidence': features,
            'missing_source_contracts': [f for f,s in features.items() if s=='MISSING_SOURCE_CONTRACT'],
            'actual_host_and_design_verification_still_required': spec['live_gaps'],
            'additional_production_workflows_not_yet_reconciled': unmapped_controls,
            'candidate_intents': sorted(intents),
            'candidate_effect_risks': sorted({e['risk'] for e in effects}),
            'parity': 'INCOMPLETE_LIVE_EVIDENCE',
            'certification': 'NOT_AUTHORIZED',
        }
    # The original applications must remain present; do not confuse the older
    # host-bound editor/calculator packages with the paired V1 sources.
    checks['legacy-production-editor-preserved'] = (repo/'main_computer/web/applications/apps/code-editor.html').is_file()
    checks['legacy-production-file-manager-preserved'] = (repo/'main_computer/web/applications/apps/file-explorer.html').is_file()
    checks['legacy-production-task-manager-preserved'] = (repo/'main_computer/web/applications/apps/task-manager.html').is_file()
    checks['older-host-bound-packages-retained'] = all((repo/f'mcel_apps/{name}/application.js').is_file() for name in ('code-editor','calculator'))
    checks['no-application-id-layout-exception'] = 'semantic-code-editor' not in (repo/'main_computer/web/applications/styles/mcel-semantic-workbench.css').read_text(encoding='utf-8')
    evidence_missing = {app: value['production_baseline_missing']
                        for app,value in applications.items() if not value['production_baseline_complete_in_export']}
    ok = all(checks.values())
    return {
        'schema':'mcel.semantic-four-app-parity-evidence.v3',
        'verdict': 'PARITY_INCOMPLETE' if ok else 'FAIL_SOURCE_CONTRACT',
        'source_audit_pass': ok,
        'full_parity_pass': False,
        'promotion': 'BLOCKED_UNTIL_HOSTED_PARITY_AND_HUMAN_DESIGN',
        'checks': checks,
        'applications': applications,
        'missing_production_baseline_evidence': evidence_missing,
        'meaning_of_source_pass': 'Declared capabilities and bounded source contracts, not executed real-host parity',
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--report',type=Path,default=OUT)
    parser.add_argument('--audit-only',action='store_true',help='Successful exit for a passing source audit; NEVER certifies parity')
    args = parser.parse_args()
    result = run()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('MCEL_FOUR_APP_PARITY', result['verdict'])
    for name,application in result['applications'].items():
        print(' ',name,'source contracts missing:',application['missing_source_contracts'],
              'baseline missing:', application['production_baseline_missing'])
    print('  source audit:', 'PASS' if result['source_audit_pass'] else 'FAIL')
    print('  hosted and human parity: UNVERIFIED; promotion BLOCKED')
    print('  report:',args.report)
    raise SystemExit(0 if args.audit_only and result['source_audit_pass'] else (1 if not result['source_audit_pass'] else 2))

if __name__ == '__main__':
    main()
