#!/usr/bin/env python3
"""Frozen-kernel, post-kernel v3 generalization gate over new structural cases.

Standalone regression: validates six deliberately distinct accepted contracts and
seven refusal counterexamples using the *unchanged* v3 implementation. Positive
cases use a manually specified trace oracle, an independent Python reference
interpreter, generic Solidity and generated Forge tests. With --forge, additionally
run manually specified on-chain trace tests in Docker.

No LLM, RPC, keys, deployment, source mutation, or independent human gold.
  python -m tools.nanojev_contract_v3_generalization --output runtime/reports/v3-heldout-0001.json
  python -m tools.nanojev_contract_v3_generalization --forge --output runtime/reports/v3-heldout-forge-0001.json
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import traceback
from typing import Any

from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_reference as reference
from tools import nanojev_contract_v3_smoke as smoke
from tools import nanojev_contract_v3_solidity as solidity
from tools import nanojev_contract_solidity_smoke as base

HERE = Path(__file__).resolve().parent
CORPUS = HERE / 'fixtures' / 'nanojev_contract_v3_generalization_v1'
KERNEL_SHA256 = {
    'nanojev_contract_v3_language.py': 'a52922369eb85934bc55801fe17ae9f15fa998443983852e34f014f7b9549239',
    'nanojev_contract_v3_verifier.py': '01df7dafc4e20d6120eefc7ad672c2067e3af0d9e11e7beb508d2c95f139b167',
    'nanojev_contract_v3_reference.py': 'c67fce8586a0dfbb408501e2b26b948e71e610fefd997a2f5ae30b5c4e1020b0',
    'nanojev_contract_v3_solidity.py': '664453aba7ec2938f9f66b5769b9d4a444490d1097511c71a108558a45feba52',
    'nanojev_contract_v3_foundry.py': 'bbb4803f8b5c01ebcb062c6f7056c8b520e93855756a7e73e1237c6f248b1f02',
    'nanojev_contract_v3_smoke.py': '8095e15879665fe2f041bfb5652e9d9dfac62235c6fa66fcfc7d03bcfbe023e3',
}
# Pinned below after creation of the separately authored held-out fixture corpus.
CORPUS_MANIFEST_SHA256 = 'ba452d156759179abad568f5d6c171fc1d090bb408734dfca2d7598f2fe18fa2'
REQUIRED_IDS = ('odd_wei_terminal_split','three_actor_fee_chain','three_party_staged_incentives',
                'zero_credit_two_phase_handshake','large_value_early_advance',
                'reviewer_initiated_payments','divergent_competing_refunds',
                'payer_unavailable_trap','terminal_residual_escrow','partial_double_allocation',
                'unexecutable_time_guard','cyclic_contract_rejected','invalid_credit_recipient')


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def preflight() -> tuple[dict, list[dict]]:
    """Fail closed on any drift in old kernel, manifest or authored cases."""
    for name, expected in KERNEL_SHA256.items():
        p = HERE / name
        if not p.is_file() or sha(p.read_bytes()) != expected:
            raise lang.V3Error('kernel_freeze', f'v3 kernel mismatch: {name}; no unmodified-kernel generalization claim')
    manifest_file = CORPUS / 'manifest.json'
    if not manifest_file.is_file() or sha(manifest_file.read_bytes()) != CORPUS_MANIFEST_SHA256:
        raise lang.V3Error('corpus_freeze', 'Held-out manifest changed; re-baseline as a new experiment')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'nanojev.contract.v3.generalization_corpus.v1' or manifest.get('provenance') != 'ASSISTANT_AUTHORED_POST_KERNEL_HOLDOUT_NOT_INDEPENDENT_EXTERNAL_GOLD':
        raise lang.V3Error('corpus_freeze', 'Unknown corpus schema/provenance')
    cases = manifest.get('cases')
    if not isinstance(cases, list) or tuple(x.get('id') for x in cases) != REQUIRED_IDS:
        raise lang.V3Error('corpus_freeze', 'Unexpected case list/order')
    if sum(x.get('expect') == 'accept' for x in cases) != 6 or sum(x.get('expect') == 'reject' for x in cases) != 7:
        raise lang.V3Error('corpus_freeze', 'Incorrect accepted/rejected corpus composition')
    for case in cases:
        file = case['file']
        if not isinstance(file, str) or not re.fullmatch(r'[a-z][a-z0-9_]{1,75}\.json',file):
            raise lang.V3Error('corpus_freeze','Unsafe case filename')
        if file != case['id']+'.json' or sha((CORPUS/file).read_bytes()) != case['sha256']:
            raise lang.V3Error('corpus_freeze', f'Case digest mismatch: {case["id"]}')
        if case['expect'] == 'accept' and (not case.get('trace_oracles') or case.get('expected_blockers')):
            raise lang.V3Error('corpus_freeze','Missing manual accepted-case oracle')
    return manifest, cases


def check_manual_oracles(spec: dict, case: dict) -> list[dict]:
    """Independent transition implementation checked against *literal* supplied balances."""
    oracles = []
    for oracle in case['trace_oracles']:
        model = reference.ReferenceEscrow(spec)
        model.fund('payer', spec['amount_wei'])
        steps = oracle['actions']
        for action in steps:
            if action == 'wait_until_deadline':
                model.tick_to_deadline()
            elif action.startswith('withdraw:'):
                model.withdraw(action.split(':',1)[1])
            else:
                model.execute(action,model.rules[action]['actor'])
            if not model.conserved():
                raise lang.V3Error('manual_oracle','Reference interpreter violated conservation')
        state, _, remaining, owed, withdrawn = model.snapshot()
        expected = oracle['expected']
        actual = {'state':state,'unallocated_wei':remaining,
                  'pending_wei':dict(zip(lang.ROLES,owed)),
                  'withdrawn_wei':dict(zip(lang.ROLES,withdrawn))}
        if actual != expected:
            raise lang.V3Error('manual_oracle',f'Gold/reference mismatch in {case["id"]}: {steps} expected={expected} found={actual}')
        oracles.append({'actions':steps,'expected':expected})
    return oracles


def manual_foundry_suite(spec: dict, case: dict) -> tuple[str, int]:
    """Golden Solidity checks from hand-authored case trace snapshots, not compiler outputs."""
    name='NanoJevProgressive01'
    ids=solidity.state_ids(spec)[1]
    functions=[]
    for i, oracle in enumerate(case['trace_oracles'],1):
        lines=[f'        {name} c = new {name}(WORKER,REVIEWER);',
               f'        c.fund{{value: {spec["amount_wei"]}}}();']
        for action in oracle['actions']:
            if action == 'wait_until_deadline':
                lines.append(f'        vm.warp(c.fundedAt() + {spec["deadline_seconds"]});')
            elif action.startswith('withdraw:'):
                role=action.split(':',1)[1]
                if role != 'payer':
                    lines.append(f'        vm.prank({role.upper()});')
                dest='payable(address(this))' if role == 'payer' else role.upper()
                lines.append(f'        c.withdrawTo({dest});')
            else:
                rule=next(r for r in spec['rules'] if r['name']==action)
                if rule['actor'] != 'payer':
                    lines.append(f'        vm.prank({rule["actor"].upper()});')
                lines.append(f'        c.{action}();')
            lines.append('        require(c.escrowConserved(),"conservation at step");')
        exp=oracle['expected']
        lines += [f'        require(uint256(c.state()) == {ids[exp["state"]]}, "gold state");',
                  f'        require(c.unallocated() == {exp["unallocated_wei"]}, "gold unallocated");',
                  f'        require(c.complete() == {str(exp["state"] in lang.terminals(spec)).lower()}, "gold complete");',
                  f'        require(c.totalPending() == {sum(exp["pending_wei"].values())}, "gold pending");',
                  f'        require(c.totalWithdrawn() == {sum(exp["withdrawn_wei"].values())}, "gold withdrawn");']
        for role in lang.ROLES:
            lines.append(f'        require(c.pendingWithdrawals({"address(this)" if role=="payer" else role.upper()}) == {exp["pending_wei"][role]}, "gold owed {role}");')
        functions.append('    function testManualOracle'+str(i)+'() public {\n'+'\n'.join(lines)+'\n    }')
    text=f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface VmHeldout {{
    function deal(address who, uint256 amount) external;
    function prank(address who) external;
    function warp(uint256 timestamp) external;
}}
contract NanoJevV3ManualOracleTest {{
    VmHeldout private constant vm = VmHeldout(address(uint160(uint256(keccak256("hevm cheat code")))));
    address payable private constant WORKER = payable(address(0xBEEF));
    address payable private constant REVIEWER = payable(address(0xCAFE));
    function setUp() public {{ vm.deal(address(this), {spec['amount_wei']} * 5); }}
{chr(10).join(functions)}
    receive() external payable {{}}
}}
'''
    return text,len(functions)


def run_forge(workspace: Path, expected_count: int, timeout: float, network: str) -> dict:
    if shutil.which('docker') is None:
        raise lang.V3Error('forge_preflight','Docker executable is required for genuine Forge verification')
    stages={}
    for action in ('build','test'):
        print(f'[V3-HELDOUT] Docker Forge {action}: {workspace.name}',flush=True)
        proc=base.run_command(base.docker_args(workspace,smoke.IMAGE,network,action),timeout)
        stages[action]={'exit_code':proc.get('exit_code'),'stdout_tail':proc.get('stdout_tail','')[-1500:],
                        'stderr_tail':proc.get('stderr_tail','')[-1500:]}
        if proc.get('exit_code') != 0:
            raise lang.V3Error('forge_'+action, f'Forge {action} failed: {proc.get("stderr_tail","")[-450:]}')
    # Do not call a fake subprocess or accept a successful exit without the expected tests.
    tail=proc.get('stdout_tail','')
    counts=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',tail)
    if not counts or tuple(map(int,counts[-1])) != (expected_count,0,0):
        raise lang.V3Error('forge_test',f'Unexpected Forge count: wanted {expected_count} passing tests, found {counts[-1] if counts else "none"}')
    artifact=workspace/'out'/'NanoJevProgressive01.sol'/'NanoJevProgressive01.json'
    if not artifact.is_file():
        raise lang.V3Error('forge_artifact','No ABI/bytecode for generated contract')
    parsed=json.loads(artifact.read_text(encoding='utf-8'))
    abi={x.get('name') for x in parsed.get('abi',[]) if x.get('type')=='function'}
    if not {'fund','withdraw','withdrawTo','unallocated','totalPending','totalWithdrawn','escrowConserved'} <= abi or not parsed.get('bytecode',{}).get('object'):
        raise lang.V3Error('forge_artifact','Required public API or bytecode missing')
    return {'forge_build':stages['build'],'forge_test':stages['test'], 'compiled_artifact_sha256':sha(artifact.read_bytes()), 'passed_tests':expected_count}


def execute(output: Path, *, forge: bool=False, timeout: float=360.0, network: str='bridge') -> dict:
    out=output.resolve()
    result={'schema':'nanojev.contract.v3.heldout_generalization.v1','status':'BLOCKED_NOT_VERIFIED',
            'started_at':utc_now(),'source':'NEW_ASSISTANT_AUTHORED_STRUCTURAL_CASES_NO_LLM',
            'gold_independence':'NOT_INDEPENDENT_EXPERT_GOLD',
            'forge':'NOT_REQUESTED' if not forge else 'NOT_RUN',
            'kernel_frozen':False,'cases':[],'blockers':[], 'expected_forge_tests':0,
            'passed_forge_tests':0}
    try:
        if out.exists() or out == (CORPUS/'manifest.json').resolve():
            raise lang.V3Error('preflight','Existing report cannot be overwritten')
        if network not in ('bridge','none') or timeout <= 0:
            raise lang.V3Error('preflight','Invalid Docker controls')
        manifest,cases=preflight()
        result['kernel_frozen']=True
        result['kernel_sha256']=KERNEL_SHA256
        result['corpus_manifest_sha256']=CORPUS_MANIFEST_SHA256
        result['corpus_count']=len(cases)
        if forge and shutil.which('docker') is None:
            raise lang.V3Error('forge_preflight','Docker unavailable; run without --forge for static verification')
        for case in cases:
            ident=case['id']
            print(f'[V3-HELDOUT] {ident}: expected {case["expect"]}',flush=True)
            spec_file=CORPUS/case['file']
            detail={'id':ident,'expect':case['expect'],'topology':case['topology'],
                    'spec_sha256':case['sha256'],'result':'BLOCKED'}
            case_report=out.with_name(out.stem+'-'+ident+'.json')
            if case_report.exists() or case_report.with_name(case_report.stem+'.workspace').exists():
                raise lang.V3Error('preflight',f'Output/workspace exists for case {ident}')
            base_result=smoke.execute(spec_file,case_report,forge=False)
            audit=base_result.get('graph_audit')
            detail['graph_status']=audit.get('status') if isinstance(audit,dict) else 'SCHEMA_REFUSED'
            if case['expect']=='reject':
                if base_result['status']!='BLOCKED_NOT_VERIFIED' or audit and audit.get('emission_authorized_by_graph_audit'):
                    raise lang.V3Error('counterexample',f'Unsafe contract accepted: {ident}')
                stage=case['expected_stage']
                if stage=='schema':
                    if not any(x.get('stage')=='schema' for x in base_result['blockers']):
                        raise lang.V3Error('counterexample',f'Expected schema rejection: {ident}')
                elif not audit or not set(case['expected_blockers']) <= set(audit.get('blockers',[])):
                    raise lang.V3Error('counterexample',f'Missing expected graph blocker: {ident}')
                if case_report.with_name(case_report.stem+'.workspace').exists():
                    raise lang.V3Error('counterexample',f'Unsafe Solidity workspace was emitted: {ident}')
                detail['result']='PASS_REFUSED';detail['blockers']=audit.get('blockers') if audit else base_result['blockers']
            else:
                if base_result['status']!='PASS_V3_IR_AND_SOLIDITY_GENERATED_FORGE_NOT_RUN' or not audit or not audit['emission_authorized_by_graph_audit']:
                    raise lang.V3Error('compiler',f'Valid held-out contract failed: {ident}; {base_result["blockers"]}')
                spec=lang.validate(json.loads(spec_file.read_text(encoding='utf-8')))
                oracles=check_manual_oracles(spec,case)
                suite,count=manual_foundry_suite(spec,case)
                workspace=case_report.with_name(case_report.stem+'.workspace')
                file=workspace/'test'/'NanoJevProgressive01ManualOracle.t.sol'
                file.write_text(suite,encoding='utf-8')
                generated=base_result['generated']
                if generated['pinned_fixture_tested'] or generated['pinned_adversarial_forge_test_count'] != 0:
                    raise lang.V3Error('corpus_independence',f'Unexpected pinned original adversarial fixture in {ident}')
                target=generated['expected_forge_tests']+count
                result['expected_forge_tests']+=target
                detail.update(result='PASS_STATIC',graph_nodes=audit['model']['reachable_nodes'],
                              manual_oracles=len(oracles),generic_forge_tests=generated['generic_forge_test_count'],
                              manually_authored_forge_tests=count,expected_forge_tests=target,
                              emitted_solidity_sha256=generated['solidity_sha256'])
                if forge:
                    forge_result=run_forge(workspace,target,timeout,network)
                    result['passed_forge_tests']+=forge_result['passed_tests']
                    detail['result']='PASS_FORGE';detail['forge']=forge_result
            result['cases'].append(detail)
        accepted=sum(x['expect']=='accept' and x['result'] in ('PASS_STATIC','PASS_FORGE') for x in result['cases'])
        rejected=sum(x['expect']=='reject' and x['result']=='PASS_REFUSED' for x in result['cases'])
        result['accepted_contracts']=accepted
        result['rejected_counterexamples']=rejected
        if accepted!=6 or rejected!=7:
            raise lang.V3Error('acceptance','Corpus acceptance counts incomplete')
        if forge and result['passed_forge_tests']!=result['expected_forge_tests']:
            raise lang.V3Error('forge_test','Full corpus Forge test count incomplete')
        result['status']='PASS_FORGE_HELDOUT_V3_CORPUS_NOT_CERTIFIED' if forge else 'PASS_STATIC_HELDOUT_V3_CORPUS_FORGE_NOT_RUN'
        result['forge']='PASS_LOCAL_DOCKER_FORGE' if forge else 'NOT_REQUESTED'
    except (lang.V3Error, ValueError, OSError, KeyError, TypeError) as exc:
        result['blockers'].append({'stage':getattr(exc,'stage','internal'),'reason':getattr(exc,'reason',str(exc))[:1000]})
    except Exception as exc:
        result['blockers'].append({'stage':'internal','reason':type(exc).__name__+': '+str(exc)[:500]})
        result['traceback_tail']=traceback.format_exc()[-1600:]
    finally:
        result['finished_at']=utc_now()
        if not out.exists():
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv: list[str] | None=None) -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--forge',action='store_true',help='Run actual Docker/Forge on all six held-out accepted contracts')
    ap.add_argument('--network',choices=('bridge','none'),default='bridge')
    ap.add_argument('--forge-timeout-s',type=float,default=360)
    options=ap.parse_args(argv)
    report=execute(options.output,forge=options.forge,timeout=options.forge_timeout_s,network=options.network)
    print(json.dumps({'status':report['status'],'accepted_contracts':report.get('accepted_contracts'),
                      'rejected_counterexamples':report.get('rejected_counterexamples'),
                      'expected_forge_tests':report['expected_forge_tests'],
                      'passed_forge_tests':report['passed_forge_tests'],
                      'blockers':report['blockers'],'report':str(options.output)},separators=(',',':')))
    return 0 if report['status'].startswith('PASS_') else 1

if __name__=='__main__':
    raise SystemExit(main())
