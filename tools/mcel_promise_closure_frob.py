#!/usr/bin/env python3
"""Consolidate real MCEL Wave 4/5 evidence without upgrading a partial pass.

This is a read-only certificate, not another greenfield application or an
approval mechanism. Missing, stale, or narrower observations remain explicit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'runtime/reports'
DEFAULT_OUTPUT = REPORTS / 'mcel-promise-closure' / 'report.json'
SCHEMA = 'mcel.promise-closure-evidence.v1'
WAVES = {
    'task-manager': ('mcel-promise-wave4', 'mcel.promise-wave4-intelligent-design.v1'),
    'file-explorer': ('mcel-promise-wave5', 'mcel.promise-wave5-second-app.v1'),
}
SOURCES = {
    'task-manager': ('tools/fixtures/mcel_candidates/task-manager-notebook/application.js',
                     'main_computer/web/applications/scripts/mcel-task-manager-notebook-candidate-ir.js'),
    'file-explorer': ('tools/fixtures/mcel_candidates/file-explorer-candidate/application.js',
                      'main_computer/web/applications/scripts/mcel-file-explorer-candidate-ir.js'),
}
SHARED_CORE = ('main_computer/web/applications/scripts/mcel-semantic-application-shell.js',
               'main_computer/web/applications/scripts/mcel-intent-effect-runtime.js',
               'main_computer/mcel_semantic_application_shell.py')
FORBIDDEN_NAMES = ('task-manager', 'file-explorer', 'taskManager', 'fileExplorer')
NEW_HOST_CHECKS = frozenset(('exact-root-identity-parity', 'exact-entry-identity-parity',
                            'refresh-version-advanced', 'refresh-operation-completed',
                            'actual-read-receipt'))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_report(path: Path, expected_schema: str) -> dict[str, Any]:
    if not path.is_file():
        return {'status':'BLOCKED', 'reason':'report absent', 'path':str(path)}
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        return {'status':'FAIL','reason':f'cannot parse report: {str(exc)[:150]}', 'path':str(path)}
    if not isinstance(obj, dict) or obj.get('schema') != expected_schema:
        return {'status':'FAIL','reason':'wrong report schema','path':str(path)}
    return {'status':'AVAILABLE','path':str(path),'sha256':sha(path),'data':obj}


def evidence(name: str, loaded: dict[str, Any]) -> dict[str, Any]:
    if loaded['status'] != 'AVAILABLE':
        return loaded
    raw=loaded['data'];cases=raw.get('cases' if name=='task-manager' else 'browserCases', [])
    hosts=raw.get('hostCases', [])
    if not isinstance(cases, list) or not isinstance(hosts, list):
        return {'status':'FAIL','reason':'invalid browser-case structure'}
    profiles={(x.get('profile'),x.get('width')) for x in cases if isinstance(x,dict)}
    expected={(profile,width) for profile in ('operational','analytical') for width in (320,390,560,1100,1272)}
    offline=(raw.get('offline')=='PASS' and profiles==expected and len(cases)==10 and
             all(isinstance(x,dict) and x.get('status')=='PASS' and
                 isinstance(x.get('checks'),dict) and all(x['checks'].values()) for x in cases))
    hosted=(raw.get('hosted')=='PASS' and len(hosts)==2 and
            {h.get('profile') for h in hosts if isinstance(h,dict)}=={'operational','analytical'} and
            all(h.get('status')=='PASS' and h.get('checks') and all(h['checks'].values()) for h in hosts))
    if name=='file-explorer':
        # Previous Wave 5 allowed all([]) for read receipts and only compared counts.
        # Those old PASS reports are useful historical evidence, not strengthened proof.
        hosted=hosted and all(NEW_HOST_CHECKS.issubset(h['checks']) and
                             all(h['checks'][k] is True for k in NEW_HOST_CHECKS) for h in hosts)
    source=raw.get('source',{})
    src_status=source.get('status','UNVERIFIED') if isinstance(source,dict) else 'UNVERIFIED'
    result={'status':'PASS' if offline and hosted and src_status=='PASS' else 'INCOMPLETE',
            'reportPath':loaded['path'],'reportSha256':loaded['sha256'],
            'sourceStatus':src_status,'offline':'PASS' if offline else 'UNVERIFIED_OR_FAIL',
            'hosted':'PASS' if hosted else 'UNVERIFIED_OR_FAIL',
            'sourceReportedSha256':source.get('sourceSha256') if isinstance(source,dict) else None,
            'artifactReportedSha256':source.get('browserArtifactSha256') if isinstance(source,dict) else None,
            'scope':'generated bounded read-side interface and declared read operations',
            'sourceReportAcceptance':raw.get('acceptance'),
            'hostCaseCount':len(hosts),'offlineCaseCount':len(cases)}
    if name=='file-explorer' and raw.get('hosted')=='PASS' and not hosted:
        result['reason']='old hosted evidence lacks exact identity parity and a fresh completed read receipt; rerun Wave 5'
    return result


def provenance(name: str, wave: dict[str, Any], root: Path) -> dict[str, Any]:
    source_rel,artifact_rel=SOURCES[name]
    source,artifact=root/source_rel,root/artifact_rel
    result={'source':source_rel,'artifact':artifact_rel,
            'sourcePresent':source.is_file(),'artifactPresent':artifact.is_file(),
            'status':'BLOCKED' if not source.is_file() else 'UNVERIFIED'}
    if artifact.is_file():
        result['artifactSha256']=sha(artifact)
        if wave.get('artifactReportedSha256') and wave['artifactReportedSha256']!=result['artifactSha256']:
            result.update(status='FAIL',reason='report browser artifact differs from current generated browser artifact')
    if source.is_file():
        result['sourceSha256']=sha(source)
        if wave.get('sourceReportedSha256') and wave['sourceReportedSha256']!=result['sourceSha256']:
            result.update(status='FAIL',reason='report source hash differs from current author-owned source')
        elif result['status']!='FAIL' and wave.get('sourceStatus')=='PASS': result['status']='PASS'
    else:
        result['reason']='author-owned DSL omitted from snapshot; generated artifact cannot replace source evidence'
    return result


def generic_core(root: Path) -> dict[str, Any]:
    issues=[];files=[]
    for rel in SHARED_CORE:
        path=root/rel
        if not path.is_file(): issues.append(f'missing shared core: {rel}');continue
        value=path.read_text(encoding='utf-8')
        for name in FORBIDDEN_NAMES:
            if name in value: issues.append(f'app-specific name {name} in {rel}')
        files.append({'path':rel,'sha256':sha(path)})
    return {'status':'PASS' if not issues else 'FAIL', 'issues':issues,'files':files,
            'scope':'literal application-name branch audit, not a full static proof of generality'}


def economy_inventory(root: Path) -> dict[str, Any]:
    sources={}
    for name,(rel,_) in SOURCES.items():
        path=root/rel
        sources[name]={'path':rel,'available':path.is_file(),
                       'lines':len(path.read_text(encoding='utf-8').splitlines()) if path.is_file() else None}
    return {'status':'UNVERIFIED', 'sourceInventory':sources,
            'secondDesignAdditionalSemanticSources':0,
            'equivalentLegacyVsMcelTrial':'NOT_RECORDED',
            'independentDecisions':'NOT_MEASURED',
            'manualWiring':'NOT_MEASURED','manualCss':'NOT_MEASURED',
            'correctiveIterations':'NOT_MEASURED','authorMinutes':'NOT_MEASURED',
            'reason':'source lines or two design profiles cannot establish labor saved'}


def consolidate(root: Path=ROOT, wave4:Path|None=None, wave5:Path|None=None) -> dict[str, Any]:
    wave4=wave4 or root/'runtime/reports/mcel-promise-wave4/report.json'
    wave5=wave5 or root/'runtime/reports/mcel-promise-wave5/report.json'
    reports={name:evidence(name,read_report(path,WAVES[name][1])) for name,path in
             (('task-manager',wave4),('file-explorer',wave5))}
    provenance_map={name:provenance(name,reports[name],root) for name in WAVES}
    core=generic_core(root)
    statuses={
        'sourceAuthorship':'PASS' if all(p['status']=='PASS' for p in provenance_map.values()) else 'INCOMPLETE',
        'twoRealHostedApplications':'PASS' if all(r['status']=='PASS' for r in reports.values()) else 'INCOMPLETE',
        'genericCoreNoAppNameBranches':core['status'],
        'readSideEffects':'PASS' if all(r['status']=='PASS' for r in reports.values()) else 'INCOMPLETE',
        'consequentialEffects':'NOT_ADMITTED',
        'independentVisualReview':'NOT_ACCEPTED',  # user personally disliked Wave 4 first renders
        'comparativeAuthoringEconomy':'UNVERIFIED',
        'greenfieldPublicAuthoring':'UNVERIFIED',
        'unrestrictedApplicationPromise':'INCOMPLETE',
    }
    return {'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'basis':'offline repository inventory + explicit Wave 4/5 external evidence; no live host accessed',
            'reports':reports,'provenance':provenance_map,'genericCore':core,
            'authoringEconomy':economy_inventory(root),'gates':statuses,
            'remainingObligations':[
                'Independent acceptance of attractive first renders; previous user judgment was negative',
                'Equivalent-scope, independently measured authored decisions, corrections and labor',
                'Greenfield authoring task through the supported public DSL; second-app migration is not sufficient',
                'Consequential intents with explicit scoped authority, negative controls and outcome receipts where needed',
                'Task Manager per-request backend provenance rather than only advancing published snapshots',
                'Current, source-hash-matched, strengthened real-host Wave 4 and Wave 5 observations'],
            'verdict':'PASS' if all(value=='PASS' for value in statuses.values()) else 'INCOMPLETE',
            'claim':'bounded two-app architectural evidence, not full MCEL product promise'}


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wave4-report',type=Path)
    parser.add_argument('--wave5-report',type=Path)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    args=parser.parse_args(argv)
    result=consolidate(wave4=args.wave4_report,wave5=args.wave5_report)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('MCEL_PROMISE_CLOSURE',result['verdict'])
    for key,value in result['gates'].items(): print(key,value)
    print('report',args.output)
    return 0 if result['verdict']=='PASS' else 2  # Incomplete promise is never a green CLI exit

if __name__=='__main__':raise SystemExit(main())
