"""Content-addressed V3 evidence lock; never writes or modifies source artifacts.

Pins original 203-test report, separate 52-test gold, source workspaces and
frozen V3 kernel. Requires the real completed baseline certification JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PureWindowsPath
import sys
from typing import Any

from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_adversarial_mutations as campaign

SCHEMA='nanojev.contract.v3.frozen_evidence_lock.v1'
PASS='PASS_V3_BASELINE_EVIDENCE_LOCKED_NOT_EXPERT_CERTIFIED'


def build_lock(report:dict[str,Any],gold:dict[str,Any],baseline:dict[str,Any],*,
               report_path:Path,gold_path:Path,baseline_path:Path,
               workspaces_root:Path,repo_root:Path)->dict[str,Any]:
    gate.preflight_gold_runtime(report,gold,workspaces_root,repo_root=repo_root)
    campaign.validate_baseline(report,gold,baseline)
    file_fingerprints={}
    for title,path,data in [('heldout_203',report_path,report),('separate_gold',gold_path,gold),('docker_forge_52',baseline_path,baseline)]:
        gate.require(path.is_file(),f'missing original evidence file: {path}')
        gate.require(gate.read_json(path)==data,f'original evidence changed while locking: {path}')
        file_fingerprints[title]={'sha256_bytes':gate.digest(path.read_bytes()),'sha256_canonical_json':gate.digest_json(data),
                                  'name':path.name}
    cases=[]
    for case in report['cases']:
        if case['expect']!='accept':continue
        workspace=workspaces_root/PureWindowsPath(case['workspace']).name
        source=workspace/'src/NanoJevGoverned01.sol'
        gate.canonical_solidity_sha256(source,case['solidity_sha256'])
        cases.append({'id':case['id'],'workspace_name':workspace.name,
                      'solidity_sha256_canonical':case['solidity_sha256'],
                      'solidity_sha256_bytes':gate.digest(source.read_bytes())})
    return {'schema':SCHEMA,'status':PASS,'source_report_sha256':gate.digest_json(report),
            'independent_gold_sha256':gate.digest_json(gold),
            'baseline_certificate_sha256':gate.digest_json(baseline),
            'evidence_files':file_fingerprints,'kernel_sha256':report['kernel_sha256'],
            'corpus_manifest_sha256':report['corpus_manifest_sha256'],
            'cases':cases,'foundry_image_id':baseline['foundry_image_id'],
            'foundry_image':baseline['foundry_image'],
            'original_governance_forge_tests':203,'original_separate_gold_forge_tests':52,
            'original_workspaces_untouched':True,'expert_independence_verified':False,
            'no_remote_rpc_no_keys_no_live_deployment':True}


def verify_lock(lock:dict[str,Any],report:dict[str,Any],gold:dict[str,Any],baseline:dict[str,Any],*,
                report_path:Path,gold_path:Path,baseline_path:Path,
                workspaces_root:Path,repo_root:Path)->None:
    gate.require(lock.get('schema')==SCHEMA and lock.get('status')==PASS,'missing passing V3 evidence lock')
    recreated=build_lock(report,gold,baseline,report_path=report_path,gold_path=gold_path,
                         baseline_path=baseline_path,workspaces_root=workspaces_root,repo_root=repo_root)
    gate.require(lock==recreated,'V3 evidence has changed since freeze (byte hashes, source workspaces, or baseline)')


def main(argv:list[str]|None=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    parser.add_argument('--gold',required=True,type=Path)
    parser.add_argument('--baseline-cert',required=True,type=Path)
    parser.add_argument('--workspaces-root',type=Path,default=Path('runtime/reports'))
    parser.add_argument('--repo-root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        gate.require(not args.output.exists(),'refuse overwrite of existing V3 evidence lock')
        paths=[args.report,args.gold,args.baseline_cert]
        values=[gate.read_json(p) for p in paths]
        evidence=build_lock(*values,report_path=paths[0],gold_path=paths[1],baseline_path=paths[2],
                            workspaces_root=args.workspaces_root,repo_root=args.repo_root)
        gate.atomic_json(args.output,evidence)
        print(json.dumps({'status':evidence['status'],'cases':len(evidence['cases']),
                          'lock_sha256':gate.digest_json(evidence),'output':str(args.output)}))
        return 0
    except (gate.CertificationFailure,ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'status':'FAIL_V3_EVIDENCE_NOT_LOCKED','blockers':[str(exc)]}),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
