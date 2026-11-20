#!/usr/bin/env python3
"""Frozen-kernel governed-v3 compositional generalization and temporal differential gate.

Holdout cases and literal final accounting oracles are author-specified after the
kernel was frozen, not independently authored expert contract gold. The runtime
interpreter does not import the production timed transition model. With --forge
it generates and executes on-chain state assertions after EVERY trace operation,
including exact time-boundary transitions, against those interpreter snapshots.

No model calls, keys, RPC, contracts deployment, or mutation of source modules.
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
from tools import nanojev_contract_v3_governance_language as language
from tools import nanojev_contract_v3_governance_reference as independent
from tools import nanojev_contract_v3_governance_verifier as verifier
from tools import nanojev_contract_v3_governance_solidity as solidity
from tools import nanojev_contract_v3_governance_foundry as foundry
from tools import nanojev_contract_v3_governance_smoke as smoke
from tools import nanojev_contract_solidity_smoke as base
from tools.nanojev_contract_v3_language import V3Error

HERE=Path(__file__).resolve().parent
CORPUS=HERE/'fixtures'/'nanojev_contract_v3_governance_heldout_v1'
# Golden SHA values belong to the already frozen accounting and governance code.
FROZEN={
 **smoke.KERNEL_SHA256,
 'nanojev_contract_v3_governance_language.py':'78dc60ea6bb97afa20eba3d60b729ef8a6353d6472eb1ea1c3ce88bf7b24bc45',
 'nanojev_contract_v3_governance_model.py':'10e2011447cd6c4f066b5dd55241d644bbf924186eb6fd79e0a343534aaa5290',
 'nanojev_contract_v3_governance_verifier.py':'09739b091224f32536b381e5c59d75907c2239d5cb58d7ce2d77263628a07d01',
 'nanojev_contract_v3_governance_reference.py':'7333c751eca721645251326d0369beadcf68c1f48d2f57721137edb4e1e5acd4',
 'nanojev_contract_v3_governance_solidity.py':'a00e19c6fb626bf82f25c8d9af3cbe40a6ddac6c881921c00e2bcc1bfce6de19',
 'nanojev_contract_v3_governance_foundry.py':'7fc31900836ea020c8cad1657a68d56b74a52e70f36234abc7ffcaf7f9873efc',
 'nanojev_contract_v3_governance_smoke.py':'ab2c77ac78bf21fcf758d8a4e4e931090142dfbb66f1b901eb35a247eba8e9e9',
}
MANIFEST_SHA256='8b76f1715f6e401e6bffffc56adb2d5ffe86376c519c1d574a25c5d596758afc'
START=1700000000
WORKER='WORKER'
ARBITRATOR='ARBITRATOR'
STRANGER='STRANGER'
ROLE_ADDR={'payer':'address(this)','worker':WORKER,'arbitrator':ARBITRATOR,'anyone':STRANGER}
METHODS={
 'submit':'submit()', 'grant_extension':'grantExtension()',
 'resolve_missed_deadline':'resolveMissedDeadline()',
 'approve':'approve()', 'reject':'reject()',
 'resolve_review_silence':'resolveReviewSilence()',
 'file_dispute':'fileDispute()', 'resolve_undisputed':'resolveUndisputed()',
 'nominate_arbitrator':'nominateArbitrator(ARBITRATOR)',
 'confirm_arbitrator':'confirmArbitrator(ARBITRATOR)',
 'award_worker':'awardWorker()', 'award_payer':'awardPayer()',
 'resolve_unappointed':'resolveUnappointed()',
 'resolve_arbitrator_silence':'resolveArbitratorSilence()',
}
SIGS={
 'submit':'submit()', 'grant_extension':'grantExtension()',
 'resolve_missed_deadline':'resolveMissedDeadline()',
 'approve':'approve()', 'reject':'reject()',
 'resolve_review_silence':'resolveReviewSilence()',
 'file_dispute':'fileDispute()', 'resolve_undisputed':'resolveUndisputed()',
 'nominate_arbitrator':'nominateArbitrator(address)',
 'confirm_arbitrator':'confirmArbitrator(address)',
 'award_worker':'awardWorker()', 'award_payer':'awardPayer()',
 'resolve_unappointed':'resolveUnappointed()',
 'resolve_arbitrator_silence':'resolveArbitratorSilence()',
}
STATUS={'active':1,'submitted':2,'rejected':3,'disputed':4,'completed':5,'refunded':6}

def digest(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def utc()->str:return datetime.now(timezone.utc).isoformat()

def preflight():
    for name,expected in FROZEN.items():
        f=HERE/name
        if not expected or not f.is_file() or digest(f.read_bytes())!=expected:
            raise V3Error('kernel_freeze',f'Frozen implementation mismatch: {name}')
    f=CORPUS/'manifest.json'
    if not MANIFEST_SHA256 or not f.is_file() or digest(f.read_bytes())!=MANIFEST_SHA256:
        raise V3Error('corpus_freeze','Governance heldout manifest changed')
    m=json.loads(f.read_text(encoding='utf8'))
    if m.get('schema')!='nanojev.contract.v3.governance_heldout.v1' or m.get('provenance')!='ASSISTANT_AUTHORED_POST_FROZEN_GOVERNANCE_NO_LLM_NO_EXPERT_GOLD':
        raise V3Error('corpus_freeze','Unrecognized corpus or provenance')
    cases=m.get('cases')
    if not isinstance(cases,list) or len(cases)<12 or len({c.get('id') for c in cases})!=len(cases):
        raise V3Error('corpus_freeze','Invalid case inventory')
    if sum(c.get('expect')=='accept' for c in cases)<6 or sum(c.get('expect')=='reject' for c in cases)<6:
        raise V3Error('corpus_freeze','Insufficient positive and negative cases')
    for c in cases:
        if not re.fullmatch(r'[a-z][a-z0-9_]{2,70}',c.get('id','')) or c.get('expect') not in ('accept','reject'):
            raise V3Error('corpus_freeze','Invalid case definition')
        p=CORPUS/(c['id']+'.json')
        if not p.is_file() or digest(p.read_bytes())!=c.get('sha256'):
            raise V3Error('corpus_freeze','Case SHA mismatch: '+c['id'])
        if c['expect']=='accept' and (not c.get('traces') or c.get('expected_stage')):
            raise V3Error('corpus_freeze','Positive case has no independently authored traces')
        if c['expect']=='reject' and not c.get('expected_stage'):
            raise V3Error('corpus_freeze','Negative case requires specified refusal stage')
    return m,cases

def _time_target(ref,step):
    clock=step['clock'];delta=step['offset']
    if type(delta) is not int or not -1<=delta<=1:
        raise V3Error('oracle','Only -1/0/+1 second boundary offsets are allowed')
    policy=ref.spec['policy'];ms=ref.spec['milestones'][ref.milestone]
    if clock=='due':end=ref.due
    elif clock=='extension_decision':end=ref.due+ms['extension_decision_seconds']
    elif clock=='review':
        if ref.submitted is None:raise V3Error('oracle','No submission clock')
        end=ref.submitted+policy['review_seconds']
    elif clock=='filing':
        if ref.rejected is None:raise V3Error('oracle','No rejection clock')
        end=ref.rejected+policy['dispute_filing_seconds']
    elif clock=='appointment':
        if ref.disputed is None:raise V3Error('oracle','No dispute clock')
        end=ref.disputed+policy['appointment_seconds']
    elif clock=='ruling':
        if ref.disputed is None:raise V3Error('oracle','No dispute clock')
        end=ref.disputed+policy['ruling_seconds']
    else:raise V3Error('oracle','Unrecognized clock '+str(clock))
    target=end+delta
    if target<ref.now:raise V3Error('oracle','Trace asks time to go backwards')
    return target

def _snapshot(ref):
    return {'status':ref.state,'milestone_index':ref.milestone,
            'unallocated_wei':ref.left,'pending_wei':dict(ref.owed),
            'withdrawn_wei':dict(ref.withdrawn),
            'due_rel':ref.due,'extensions':ref.extensions,
            'submitted_rel':ref.submitted,'rejected_rel':ref.rejected,
            'disputed_rel':ref.disputed,'nominated':ref.candidate is not None,
            'appointed':ref.appointee is not None}

def replay_trace(spec,trace):
    """Separate global-clock model: report expected states after every operation."""
    r=independent.Protocol(spec);history=[];boundary_clocks=set();denials=0
    if not isinstance(trace.get('steps'),list) or not trace['steps']:
        raise V3Error('oracle','Missing trace steps')
    for step in trace['steps']:
        op=step.get('op');refused=step.get('expect')=='reject';clock_target=None
        if op=='warp':
            if refused:raise V3Error('oracle','Time warp cannot be a rejected transaction')
            clock_target=_time_target(r,step);boundary_clocks.add(step['clock']+':'+str(step['offset']))
            r.warp(clock_target-r.now)
        elif op=='withdraw':
            role=step['role']
            try:
                r.withdraw(role)
            except independent.Rejected:
                if not refused:raise V3Error('oracle','Reference rejected a desired withdrawal')
                denials+=1
            else:
                if refused:raise V3Error('oracle','Expected withdrawal refusal but reference accepted')
        elif op in METHODS:
            role=step['role']
            try:
                r.invoke(op,role)
            except independent.Rejected:
                if not refused:raise V3Error('oracle',f'Reference refused {op} for {role}')
                denials+=1
            else:
                if refused:raise V3Error('oracle',f'Expected rejection for {op}, reference accepted')
        else:raise V3Error('oracle','Unsupported step operation')
        r.assert_conserved()
        history.append({'step':step,'time_rel':r.now,'expected':_snapshot(r),'refused':refused})
    expected=trace.get('expected')
    actual={k:v for k,v in _snapshot(r).items() if k in ('status','milestone_index','unallocated_wei','pending_wei','withdrawn_wei')}
    if expected != actual:
        raise V3Error('oracle',f'Manual expected balances disagree with separate global-clock interpreter in {trace.get("id")}: {actual} != {expected}')
    return {'id':trace['id'],'history':history,'expected':actual,'denials':denials,'boundaries':sorted(boundary_clocks)}

def _assert_snapshot(snapshot, tag):
    """Compact on-chain checkpoints with machine-readable failure identifiers.

    Error code = trace_number*10000 + step_number*100 + field_number.
    This avoids hundreds of unique revert strings and the 24 KiB EIP-170
    deployment-size problem in long temporal traces.
    """
    e=snapshot
    try:
        first,second=tag[1:].split('s',1)
        base=int(first)*10000+int(second)*100
    except (ValueError,IndexError):
        raise V3Error('oracle','Invalid differential trace tag')
    terms=[
        f'uint256(c.status()) == {STATUS[e["status"]]}',
        f'c.currentMilestone() == {e["milestone_index"]}',
        f'c.unallocated() == {e["unallocated_wei"]}',
        f'c.dueAt() == START + {e["due_rel"]}',
        f'c.extensionsGranted() == {e["extensions"]}',
        f'c.complete() == {"true" if e["status"] in ("completed","refunded") else "false"}',
        f'c.pendingWithdrawals(address(this)) == {e["pending_wei"]["payer"]}',
        f'c.pendingWithdrawals(WORKER) == {e["pending_wei"]["worker"]}',
        f'c.totalPending() == {sum(e["pending_wei"].values())}',
        f'c.totalWithdrawn() == {sum(e["withdrawn_wei"].values())}',
        'c.escrowConserved()',
        f'c.nominatedArbitrator() == {"ARBITRATOR" if e["nominated"] else "address(0)"}',
        f'c.arbitrator() == {"ARBITRATOR" if e["appointed"] else "address(0)"}',
    ]
    for property_,relative in (('submittedAt','submitted_rel'),('rejectedAt','rejected_rel'),('disputedAt','disputed_rel')):
        val=e[relative]
        terms.append(f'c.{property_}() == {"0" if val is None else f"START + {val}"}')
    return [f'_check({condition}, {base+i});' for i,condition in enumerate(terms,1)]

def onchain_suite(spec,trace_results,name='NanoJevGoverned01'):
    funcs=[]
    for idx,trace in enumerate(trace_results,1):
        lines=['vm.warp(START);',f'{name} c = new {name}(payable(WORKER));',
               'vm.deal(address(this), AMOUNT * 4);','c.fund{value: AMOUNT}();',
               'require(c.fundedAt() == START,"funding clock");']
        for i,record in enumerate(trace['history']):
            step=record['step'];op=step['op'];role=step.get('role');refused=record['refused']
            if op=='warp':
                lines.append(f'vm.warp(START + {record["time_rel"]});')
            elif op=='withdraw':
                if role!='payer':lines.append(f'vm.prank({ROLE_ADDR[role]});')
                if refused:
                    lines.append(f'(bool ok{i},) = address(c).call(abi.encodeWithSignature("withdrawTo(address)", RECEIVER));')
                    lines.append(f'require(!ok{i},"step {i} expected withdrawal rejection");')
                else:
                    lines.append('c.withdrawTo(payable(RECEIVER));')
            else:
                if role!='payer':lines.append(f'vm.prank({ROLE_ADDR[role]});')
                if refused:
                    arg=', ARBITRATOR' if op in ('nominate_arbitrator','confirm_arbitrator') else ''
                    lines.append(f'(bool ok{i},) = address(c).call(abi.encodeWithSignature("{SIGS[op]}"{arg}));')
                    lines.append(f'require(!ok{i},"step {i} expected denial");')
                else:lines.append('c.'+METHODS[op]+';')
            lines += _assert_snapshot(record['expected'],f't{idx}s{i}')
        funcs.append(f'''contract NanoJevGovernanceHeldoutTrace{idx} {{
    VmGovHeldout private constant vm = VmGovHeldout(address(uint160(uint256(keccak256("hevm cheat code")))));
    address private constant WORKER = address(0xBEEF);
    address private constant ARBITRATOR = address(0xA11CE);
    address private constant STRANGER = address(0xCAFE);
    address private constant RECEIVER = address(0xF00D);
    uint256 private constant START = {START};
    uint256 private constant AMOUNT = {spec['amount_wei']};
    error DifferentialMismatch(uint256 code);
    function _check(bool condition, uint256 code) internal pure {{
        if (!condition) revert DifferentialMismatch(code);
    }}
    function testDifferentialTrace{idx}() public {{
{chr(10).join('        '+l for l in lines)}
    }}
    receive() external payable {{}}
}}''')
    suite=f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface VmGovHeldout {{
    function prank(address) external;
    function deal(address,uint256) external;
    function warp(uint256) external;
}}
{chr(10).join(funcs)}
'''
    return suite,len(funcs)

def run_forge(workspace, expected, timeout, network):
    if not shutil.which('docker'):
        raise V3Error('forge_preflight','Real Docker required; never mark injected command as Forge acceptance')
    output={}
    for task in ('build','test'):
        print(f'[V3-GOV-HELDOUT] Docker Forge {task}: {workspace.name}',flush=True)
        resp=base.run_command(base.docker_args(workspace,smoke.IMAGE,network,task),timeout)
        output[task]={'exit_code':resp.get('exit_code'),'stdout_tail':str(resp.get('stdout_tail',''))[-1800:],
                      'stderr_tail':str(resp.get('stderr_tail',''))[-1800:]}
        if type(resp.get('exit_code')) is not int or resp['exit_code']!=0:
            raise V3Error('forge_'+task,task+' failed '+str(resp.get('stderr_tail',''))[-450:])
    text=resp.get('stdout_tail','')
    counts=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',text)
    if not counts or tuple(map(int,counts[-1]))!=(expected,0,0):
        raise V3Error('forge_test',f'Forge test count/status invalid, expected {expected}: {counts[-1:]!r}')
    artifact=workspace/'out'/'NanoJevGoverned01.sol'/'NanoJevGoverned01.json'
    if not artifact.is_file():raise V3Error('artifact','No compiled ABI/bytecode')
    data=json.loads(artifact.read_text(encoding='utf8'))
    names={f.get('name') for f in data.get('abi',[]) if f.get('type')=='function'}
    if not {'fund','submit','approve','reject','fileDispute','grantExtension','resolveReviewSilence',
            'resolveArbitratorSilence','withdrawTo','escrowConserved'}<=names or not data.get('bytecode',{}).get('object'):
        raise V3Error('artifact','Missing critical governance ABI/bytecode')
    return output,digest(artifact.read_bytes())

def execute(output,forge=False,timeout=360,network='bridge'):
    out=Path(output).resolve(); report={'schema':'nanojev.contract.v3.governance_heldout_report.v1',
        'status':'BLOCKED_NOT_VERIFIED','started_at':utc(),'mode':'NO_LLM_NO_KEYS_NO_RPC_NO_DEPLOYMENT',
        'forge':'NOT_REQUESTED' if not forge else 'NOT_RUN','blockers':[],'cases':[],
        'gold_independence':'NOT_INDEPENDENT_EXPERT_GOLD',
        'source':'HELDOUT_AUTHOR_SPECIFIED_GOVERNANCE_AND_MANUAL_FINAL_BALANCES',
        'kernel_frozen':False,'total_forge_tests':0,'passed_forge_tests':0}
    try:
        if out.exists() or not out.parent.exists() or not out.parent.is_dir():
            raise V3Error('preflight','Output must be fresh and parent folder must exist')
        if forge and (not shutil.which('docker') or timeout<=0 or network not in ('none','bridge')):
            raise V3Error('preflight','Real Docker and valid options required')
        manifest,cases=preflight();report['kernel_frozen']=True
        report['corpus_manifest_sha256']=MANIFEST_SHA256
        report['kernel_sha256']={f:digest((HERE/f).read_bytes()) for f in FROZEN}
        report['corpus_count']=len(cases)
        accepted=rejected=0;total_differential=total_denials=0
        for c in cases:
            report['case_in_progress']=c['id']
            print(f'[V3-GOV-HELDOUT] {c["id"]}: expected {c["expect"]}',flush=True)
            spec=json.loads((CORPUS/(c['id']+'.json')).read_text(encoding='utf8'))
            row={'id':c['id'],'expect':c['expect'],'topology':c['topology'],'spec_sha256':c['sha256']}
            try:
                normalized=language.validate(spec)
                audit=verifier.verify(normalized)
                if not audit['emission_authorized_by_graph_audit']:
                    raise V3Error('graph',';'.join(audit['blockers']))
                if c['expect']=='reject':raise V3Error('oracle','An expected-negative case was accepted')
                traces=[replay_trace(normalized,t) for t in c['traces']]
                manual=len(traces);denials=sum(t['denials'] for t in traces)
                if manual<3 or not any(':' in b for t in traces for b in t['boundaries']):
                    raise V3Error('oracle','Missing temporal differential test coverage')
                generic,gcount=foundry.tests(normalized)
                onchain,ocount=onchain_suite(normalized,traces)
                if len(re.findall(r'\bfunction\s+test\w*\s*\(',generic))!=gcount or len(re.findall(r'\bfunction\s+test\w*\s*\(',onchain))!=ocount:
                    raise V3Error('oracle','Generated test count mismatch')
                code=solidity.source(normalized)
                workspace=out.parent/(out.stem+'-'+c['id']+'.workspace')
                if workspace.exists():raise V3Error('preflight','Workspace exists '+str(workspace))
                (workspace/'src').mkdir(parents=True)
                (workspace/'test').mkdir()
                (workspace/'foundry.toml').write_text('[profile.default]\nsrc = "src"\ntest = "test"\nsolc = "0.8.24"\noptimizer = true\noptimizer_runs = 200\n',encoding='utf8')
                (workspace/'src'/'NanoJevGoverned01.sol').write_text(code,encoding='utf8')
                (workspace/'test'/'NanoJevGoverned01Generated.t.sol').write_text(generic,encoding='utf8')
                (workspace/'test'/'NanoJevGoverned01Differential.t.sol').write_text(onchain,encoding='utf8')
                expected=gcount+ocount
                row.update(result='PASS_IR_ORACLES_FORGE_NOT_RUN',graph_status=audit['status'],
                           graph_nodes=audit['reachable_nodes'],generic_forge_tests=gcount,
                           differential_forge_tests=ocount,expected_forge_tests=expected,
                           denied_reference_actions=denials,
                           boundary_points=sorted({b for t in traces for b in t['boundaries']}),
                           manual_final_oracles=manual,solidity_sha256=digest(code.encode('utf8')),
                           differential_suite_sha256=digest(onchain.encode('utf8')),
                           workspace=str(workspace))
                if forge:
                    row['forge'],row['artifact_sha256']=run_forge(workspace,expected,timeout,network)
                    row['result']='PASS_FORGE'
                    row['passed_forge_tests']=expected
                    report['passed_forge_tests']+=expected
                report['total_forge_tests']+=expected
                total_differential+=manual;total_denials+=denials;accepted+=1
            except V3Error as exc:
                if c['expect']=='accept':raise
                if exc.stage!=c['expected_stage']:
                    raise V3Error('oracle',f'Wrong refusal class for {c["id"]}: {exc.stage} != {c["expected_stage"]}')
                row.update(result='PASS_REFUSED',rejection_stage=exc.stage,reason=exc.reason)
                rejected+=1
            report['cases'].append(row)
        if accepted!=sum(c['expect']=='accept' for c in cases) or rejected!=sum(c['expect']=='reject' for c in cases):
            raise V3Error('oracle','Missing cases or wrong acceptance count')
        report.pop('case_in_progress',None)
        report.update(accepted_contracts=accepted,rejected_counterexamples=rejected,
                      differential_trace_count=total_differential,reference_denials=total_denials,
                      forge='PASS_LOCAL_DOCKER_FORGE' if forge else 'NOT_REQUESTED',
                      status='PASS_FORGE_V3_GOVERNANCE_HELDOUT_NOT_CERTIFIED' if forge else 'PASS_V3_GOVERNANCE_HELDOUT_IR_NOT_CERTIFIED')
    except (V3Error,ValueError,OSError,KeyError,TypeError,AssertionError) as exc:
        report['blockers'].append({'stage':getattr(exc,'stage','internal'), 'reason':str(exc)[:500],
                                   'case_id':report.get('case_in_progress')})
    except Exception as exc:
        report['blockers'].append({'stage':'internal','reason':str(exc)[:500],
                                   'case_id':report.get('case_in_progress')})
        report['traceback_tail']=traceback.format_exc()[-2500:]
    report['finished_at']=utc()
    if not out.exists():
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    return report

def main(argv=None):
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--forge',action='store_true')
    a.add_argument('--output',type=Path,required=True)
    a.add_argument('--forge-timeout-s',type=float,default=360)
    a.add_argument('--network',choices=['none','bridge'],default='bridge')
    args=a.parse_args(argv)
    result=execute(args.output,forge=args.forge,timeout=args.forge_timeout_s,network=args.network)
    print(json.dumps({'status':result['status'],'accepted_contracts':result.get('accepted_contracts',0),
        'rejected_counterexamples':result.get('rejected_counterexamples',0),
        'differential_traces':result.get('differential_trace_count',0),
        'expected_forge_tests':result.get('total_forge_tests',0),
        'passed_forge_tests':result.get('passed_forge_tests',0),
        'blockers':result['blockers'],'report':str(args.output)},separators=(',',':')))
    return 0 if result['status'].startswith('PASS_') else 1
if __name__=='__main__':raise SystemExit(main())
