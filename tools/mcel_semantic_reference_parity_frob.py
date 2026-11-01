"""Source-grounded semantic-reference-app parity audit (no implicit promotion)."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT / "runtime/reports/mcel-semantic-reference-parity/report.json"
BASELINES={
 "file-manager": {
   "sources":["main_computer/web/applications/scripts/file-explorer.js","main_computer/web/applications/apps/file-explorer.html"],
   "required_tokens":["searchSystemFileExplorer", "systemFileExplorerSelectLocation", "systemFileExplorerOpenEntry"],
   "baseline_features":["roots","nested-directory-navigation","parent-navigation","directory-listing","search","file-preview","rich-file-tree"],
   "feature_evidence": {
       "roots": ("listRoots", "intent:load-roots"),
       "nested-directory-navigation": ("openDirectory", "intent:enter-directory"),
       "parent-navigation": ("data-mcel-parent-directory",),
       "directory-listing": ("listDirectory", "intent:browse"),
       "search": ("searchEntries", "intent:search"),
       "file-preview": ("readPreview", "intent:preview-entry"),
       "rich-file-tree": ("MainComputerWunderbaum",),
   },
   "write_authority":"not-present-in-original-read-only-file-browser"
 },
 "task-manager": {
   "sources":["main_computer/web/applications/scripts/task-manager.js","main_computer/web/applications/apps/task-manager.html"],
   "required_tokens":["server_shutdown", "terminate_pid", "task-notebook-tabs", "task-ai-analyze"],
   "baseline_features":["process-observation","four-real-notebook-views","connections","cpu-gpu","auto-refresh","server-control","process-terminate-kill","schedules","ai-operations"],
   "feature_evidence": {
       "process-observation": ("observeProcesses", "intent:refresh"),
       "four-real-notebook-views": ("observeAllProcesses", "observeConnections", "observeHardware", "switch-view"),
       "connections": ("observeConnections", "intent:refresh-connections"),
       "cpu-gpu": ("observeHardware", "intent:refresh-hardware"),
       "auto-refresh": ("data-mcel-monitor-auto-refresh",),
       "server-control": ("intent:server-shutdown",),
       "process-terminate-kill": ("intent:terminate-process",),
       "schedules": ("intent:list-schedules",),
       "ai-operations": ("intent:task-ai",),
   },
   "write_authority":"consequential-operations-only-through-existing-governed-backend"
 },
 "code-editor": {
   "sources":["mcel_apps/code-editor/application.js","main_computer/web/applications/scripts/code-editor-monaco-adapter.js"],
   "required_tokens":["previewAiderPlan", "applyReviewedPatch", "function mount(options = {})", "code-studio"],
   "baseline_features":["monaco","file-tree","source-read","draft-edit","save","discard","close-file","aider-preview","reviewed-patch"],
   "feature_evidence": {
       "monaco": ("MainComputerMonacoAdapter", "document-editor"),
       "file-tree": ("listDirectory", "intent:browse-directory"),
       "source-read": ("readSource", "intent:open-source"),
       "draft-edit": ('rendererLocal("draft"', 'data-mcel-document-dirty'),
       "save": ("intent:save-source", "save-receipt-invalid"),
       "discard": ("data-mcel-document-discard",),
       "close-file": ("data-mcel-document-close",),
       "aider-preview": ("intent:preview-aider-plan",),
       "reviewed-patch": ("intent:apply-reviewed-change",),
       "document-tabs": ("data-mcel-document-tabs",),
   },
   "write_authority":"source-write-only-on-explicit-save-with-stale-source-hash"
 }
}

def run(repo:Path=ROOT)->dict:
    findings={}; checks={}
    adapter=(repo/'main_computer/web/applications/scripts/mcel-semantic-read-ports.js').read_text(encoding='utf-8')
    runtime=(repo/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text(encoding='utf-8')
    for app,requirements in BASELINES.items():
        baseline='\n'.join((repo/x).read_text(encoding='utf-8') for x in requirements['sources'])
        checks[f'{app}.baseline-sources-present']=bool(baseline)
        checks[f'{app}.baseline-evidence-present']=all(token in baseline for token in requirements['required_tokens'])
        ir=compile_semantic_app(app,repo)
        checks[f'{app}.paired-semantic-source']=len(ir['provenance']['frontend']['sourceFiles'])==2 and not ir['layouts']
        candidate_source=(repo/'tools/fixtures/mcel_semantic_other_apps'/app/'application.js').read_text(encoding='utf-8')
        evidence_text='\n'.join((candidate_source, adapter, runtime))
        supported={feature:all(token in evidence_text for token in tokens) for feature,tokens in requirements['feature_evidence'].items()}
        # Source declarations are necessary evidence only. They are not proof
        # of live effect execution, successful UI interaction, or feature parity.
        findings[app]={
            'baseline_sources':requirements['sources'],
            'baseline_features':requirements['baseline_features'],
            'source_feature_evidence':{key:('DECLARED_NOT_LIVE_VERIFIED' if passed else 'MISSING') for key,passed in supported.items()},
            'not_yet_migrated':[key for key,passed in supported.items() if not passed],
            'not_live_verified':[key for key,passed in supported.items() if passed],
            'authority_boundary':requirements['write_authority'],
            'candidate_intents':sorted(i['id'] for i in ir['intents']),
            'candidate_effect_risks':sorted(set(e['risk'] for e in ir['effects'])),
            'parity':'INCOMPLETE'
        }
        if app=='code-editor':
            doc=[n for n in ir['surfaces'][0]['nodes'] if n['nodeKind']=='document-editor']
            checks['code-editor.monaco-semantic-workarea']=len(doc)==1 and doc[0].get('saveIntent')=={'ref':'intent:save-source'}
            checks['code-editor.source-write-risk-exact']={e['id'] for e in ir['effects'] if e['risk']=='source-write'}=={'effect:save-source.request','effect:apply-reviewed-change.request'}
        else:
            checks[f'{app}.no-new-write-authority']=not any(e['risk']!='external-read' for e in ir['effects'])
    checks['production-legacy-editor-unchanged']=(repo/'mcel_apps/code-editor/application.js').exists()
    checks['semantic-implementation-not-app-layout']=(
        'semantic-code-editor' not in (repo/'main_computer/web/applications/styles/mcel-semantic-workbench.css').read_text())
    result={
       'schema':'mcel.semantic-reference-parity-audit.v2',
       'verdict':'PARITY_INCOMPLETE' if all(checks.values()) else 'FAIL_CONTRACT',
       'checks':checks,
       'applications':findings,
       'browser_proof':'SEE tests/test_mcel_semantic_editor_migration.py (mocked Monaco and API)',
       'live_monaco':'NOT_VERIFIED', 'hosted_windows_save':'NOT_VERIFIED',
       'promotion':'BLOCKED_UNTIL_FUNCTIONAL_PARITY',
       'source_audit_pass':all(checks.values()),
       'full_parity_pass':False
    }
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,default=OUT)
    parser.add_argument('--audit-only',action='store_true',help='Exit successfully when source audit passes; NEVER means functional parity passed.')
    args=parser.parse_args()
    result=run()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('MCEL_SEMANTIC_REFERENCE_PARITY',result['verdict'])
    for k,v in result['checks'].items():print(' ',k+':','PASS' if v else 'FAIL')
    for app,report in result['applications'].items():
        print(' ',app+':',report['parity'],', '.join(report['not_yet_migrated']))
    print('  live Monaco:',result['live_monaco'])
    print('  hosted Windows save:',result['hosted_windows_save'])
    print('  report:',args.report)
    print('  FULL_APPLICATION_PARITY: NOT_VERIFIED — NOT A PASS')
    raise SystemExit(0 if args.audit_only and result['source_audit_pass'] else (1 if not result['source_audit_pass'] else 2))
if __name__=='__main__':main()
