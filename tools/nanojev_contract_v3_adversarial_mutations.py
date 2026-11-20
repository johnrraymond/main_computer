"""Fail-closed, source-frozen NanoJev V3 adversarial EVM mutation campaign.

Runs existing independently written gold suites unchanged against deliberate
single-site corruptions of *copied* Solidity in disposable Docker Forge roots.
A mutation is 'killed' ONLY by actual failing Forge assertions with complete
suite accounting. Infrastructure failures and compilation failures are NOT kills.
No V3 compiler, language, transition reference, keys, RPC or live deployment.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
from typing import Any

from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_independent_forge_gold as forge_gold

SCHEMA = "nanojev.contract.v3.adversarial_mutation_campaign.v1"
BASELINE_STATUS = "PASS_SEPARATE_GOLD_DOCKER_FORGE_EXECUTION_NOT_EXPERT_CERTIFIED"
PASS = "PASS_MUTANTS_KILLED_EVM_NOT_EXPERT_CERTIFIED"
FAIL = "FAIL_MUTATION_CAMPAIGN_INCOMPLETE"

@dataclass(frozen=True)
class Mutation:
    id: str
    case_id: str
    domain: str
    scope: str
    before: str
    after: str
    witness_signature: str
    witness_actor: str
    witness_outcome: str
    mechanism: str

CASES = (
    "single_odd_wei_silence",
    "two_stage_double_extension",
    "three_stage_mixed_timeout_awards",
    "four_stage_partial_withdraw_refund",
    "five_stage_progressive_silence_policies",
    "large_odd_wei_conditional_disposition",
)

MUTANTS: tuple[Mutation,...] = (
    Mutation("M01",CASES[0],"authorization","fund",'msg.sender == payer && status == Status.Unfunded','status == Status.Unfunded',"fund()","outsider","revert","Allow outsider funding"),
    Mutation("M02",CASES[0],"deadline","resolveMissedDeadline",'block.timestamp > dueAt + extensionDecision(currentMilestone)','block.timestamp >= fundedAt',"resolveMissedDeadline()","outsider","revert","Allow premature refund"),
    Mutation("M03",CASES[0],"authorization","approve",'status == Status.Submitted && msg.sender == payer','status == Status.Submitted',"approve()","outsider","revert","Permit outsider approval"),
    Mutation("M04",CASES[0],"silence-policy","resolveReviewSilence",'_refund();','_award();',"resolveReviewSilence()","outsider","success","Pay worker instead of payer on review silence"),
    Mutation("M05",CASES[1],"extension-count","grantExtension",'extensionsGranted < extensionLimit(currentMilestone)','extensionsGranted <= extensionLimit(currentMilestone)',"grantExtension()","payer","revert","One additional discretionary extension"),
    Mutation("M06",CASES[1],"authorization","grantExtension",'msg.sender == payer && status == Status.Active','status == Status.Active',"grantExtension()","worker","revert","Permit worker to extend deadlines"),
    Mutation("M07",CASES[2],"accounting","_refund",'_credit(payer, unallocated);','_credit(worker, unallocated);',"withdraw()","payer","success","Refund leftover escrow to worker"),
    Mutation("M08",CASES[2],"arbitration","awardWorker",'msg.sender == arbitrator, "appointed arbitrator only"','msg.sender != address(0), "appointed arbitrator only"',"awardWorker()","outsider","revert","Permit outsider to issue an award"),
    Mutation("M09",CASES[3],"accounting","_award",'_credit(worker, milestoneAmount(currentMilestone));','_credit(payer, milestoneAmount(currentMilestone));',"withdraw()","worker","success","Credit awarded milestone to payer"),
    Mutation("M10",CASES[3],"withdrawal","withdrawTo",'destination.call{value: amount}("")','destination.call{value: amount - 1}("")',"withdraw()","worker","success","Short-pay one wei and retain dust"),
    Mutation("M11",CASES[4],"appointment","resolveUnappointed",'block.timestamp > disputedAt + APPOINTMENT_SECONDS','block.timestamp >= disputedAt',"resolveUnappointed()","outsider","revert","Close appointment window immediately"),
    Mutation("M12",CASES[4],"silence-policy","resolveArbitratorSilence",'_award();','_refund();',"resolveArbitratorSilence()","outsider","success","Reverse arbitrator-silence disposition"),
    Mutation("M13",CASES[5],"arbitration","awardPayer",'_refund();','_award();',"awardPayer()","arbitrator","success","Reverse explicit payer ruling"),
    Mutation("M14",CASES[5],"withdrawal","withdrawTo",'destination.call{value: amount}("")','payer.call{value: amount}("")',"withdraw()","worker","success","Redirect withdrawal to payer"),
    Mutation("M15",CASES[5],"accounting","_credit",'pendingWithdrawals[beneficiary] += amount;','pendingWithdrawals[payer] += amount;',"withdraw()","worker","success","Rewrite arbitrary beneficiary credit to payer"),
    Mutation("M16",CASES[4],"permissionless-resolution","resolveMissedDeadline",'require(status == Status.Active, "not active");','require(status == Status.Active && msg.sender == payer, "not active");',"resolveMissedDeadline()","outsider","success","Require payer to trigger missed deadline resolution"),
    Mutation("M17",CASES[2],"filing-deadline","fileDispute",'block.timestamp <= rejectedAt + FILING_SECONDS','block.timestamp >= rejectedAt',"fileDispute()","worker","success","Allow worker to file dispute after expiry"),
    Mutation("M18",CASES[4],"appointment-boundary","resolveUnappointed",'block.timestamp > disputedAt + APPOINTMENT_SECONDS','block.timestamp >= disputedAt + APPOINTMENT_SECONDS',"resolveUnappointed()","outsider","revert","Treat exact appointment deadline as expired"),
    Mutation("M19",CASES[0],"submission-boundary","submit",'block.timestamp <= dueAt','block.timestamp < dueAt',"submit()","worker","success","Refuse submission at exactly due time"),
    Mutation("M20",CASES[3],"review-boundary","approve",'block.timestamp <= submittedAt + REVIEW_SECONDS','block.timestamp < submittedAt + REVIEW_SECONDS',"approve()","payer","success","Refuse payer approval at exact review deadline"),
)

# Explicit extra boundary probes close known blind spots in the original 52 gold
# tests (notably extension-cap attempts *after* the new due time). Generated
# separately, always run against the pristine source FIRST, and then copied
# byte-for-byte into mutant workspaces. They do not modify the frozen gold.
PROBE_BODIES:dict[str,tuple[tuple[str,str],...]]={
    CASES[0]:(
        ('testWorkerMaySubmitExactlyAtDue', 'vm.warp(c.dueAt()); vm.prank(WORKER); c.submit(); require(uint256(c.status()) == 2, "due equality must accept");'),
    ),
    CASES[1]:(
        ('testThirdExtensionAtNewDueMustFail', 'for (uint256 i = 0; i < 2; ++i) { vm.warp(c.dueAt()); vm.prank(PAYER); c.grantExtension(); } vm.warp(c.dueAt()); vm.prank(PAYER); (bool ok,) = address(c).call(abi.encodeWithSignature("grantExtension()")); require(!ok, "extension cap bypassed");'),
        ('testWorkerCannotExtendInsideValidWindow', 'vm.warp(c.dueAt()); vm.prank(WORKER); (bool ok,) = address(c).call(abi.encodeWithSignature("grantExtension()")); require(!ok, "worker extension accepted");'),
    ),
    CASES[2]:(
        ('testWorkerCannotFileAfterFilingExpiry', 'vm.prank(WORKER); c.submit(); vm.prank(PAYER); c.reject(); vm.warp(c.rejectedAt() + c.FILING_SECONDS() + 1); vm.prank(WORKER); (bool ok,) = address(c).call(abi.encodeWithSignature("fileDispute()")); require(!ok, "expired filing accepted");'),
    ),
    CASES[3]:(
        ('testPayerCanApproveAtReviewDeadline', 'vm.prank(WORKER); c.submit(); vm.warp(c.submittedAt() + c.REVIEW_SECONDS()); vm.prank(PAYER); c.approve();'),
    ),
    CASES[4]:(
        ('testUnappointedResolutionDeniedAtDeadlineEquality', 'vm.prank(WORKER); c.submit(); vm.prank(PAYER); c.reject(); vm.prank(WORKER); c.fileDispute(); vm.warp(c.disputedAt() + c.APPOINTMENT_SECONDS()); vm.prank(OUTSIDER); (bool ok,) = address(c).call(abi.encodeWithSignature("resolveUnappointed()")); require(!ok, "premature unappointed resolution");'),
    ),
}

def generated_probes(case_id:str,amount:int)->tuple[str,int]:
    tests=PROBE_BODIES.get(case_id,())
    if not tests:return '',0
    head='''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/NanoJevGoverned01.sol";
interface VmMutationProbe {
    function prank(address) external;
    function deal(address,uint256) external;
    function warp(uint256) external;
}
contract NanoJevAdversarialProbes {
    VmMutationProbe internal constant vm = VmMutationProbe(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant PAYER = address(uint160(0xAAA1));
    address internal constant WORKER = address(uint160(0xBBB1));
    address internal constant OUTSIDER = address(uint160(0xCCC1));
    function fresh() internal returns (NanoJevGoverned01 c) {
        vm.warp(1700000000);
        vm.deal(PAYER, 10000000000000000000000000000);
        vm.prank(PAYER);
        c = new NanoJevGoverned01(payable(WORKER));
        vm.prank(PAYER);
        c.fund{value: FUNDING_WEI}();
    }
'''
    head=head.replace('FUNDING_WEI',str(amount))
    body='\n'.join('    function '+name+'() public { NanoJevGoverned01 c = fresh(); '+code+' }' for name,code in tests)
    return head+body+'\n}\n',len(tests)

SUMMARY = re.compile(r"Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped")

def _range_for_function(src: str, name: str) -> tuple[int,int]:
    starts=list(re.finditer(r'\bfunction\s+'+re.escape(name)+r'\s*\(',src))
    gate.require(len(starts)==1,f"mutation scope missing/ambiguous: {name}")
    a=starts[0].start()
    # Source is the frozen generated one, with functions separated by closing braces.
    brace=src.find('{',starts[0].end())
    gate.require(brace >= 0, f"scope lacks function body: {name}")
    level=0
    for pos in range(brace,len(src)):
        if src[pos]=='{': level+=1
        elif src[pos]=='}':
            level-=1
            if level==0:return a,pos+1
    raise gate.CertificationFailure("unterminated function body: "+name)

def apply_mutation(src: str, mutation: Mutation) -> str:
    a,b=_range_for_function(src, mutation.scope)
    target=src[a:b]
    gate.require(target.count(mutation.before)==1, f"{mutation.id}: mutation anchor not unique in {mutation.scope}")
    changed=target.replace(mutation.before,mutation.after,1)
    gate.require(changed != target and mutation.before != mutation.after,"mutation must alter source")
    result=src[:a]+changed+src[b:]
    gate.require(result.count(mutation.after)>=1,"mutation not applied")
    return result


def witness_count(case:dict[str,Any],mutation:Mutation)->int:
    return sum(1 for seq in case['sequences'] for st in gate._flatten_steps(seq['steps'])
               if st.get('type')=='send' and st.get('signature')==mutation.witness_signature
               and st.get('actor')==mutation.witness_actor and st.get('expect')==mutation.witness_outcome)


def validate_baseline(report:dict[str,Any],gold:dict[str,Any],baseline:dict[str,Any])->dict[str,Any]:
    gate.validate_gold(report,gold)
    gate.require(baseline.get('schema')==gate.CERT_SCHEMA,"wrong baseline certificate schema")
    gate.require(baseline.get('status')==BASELINE_STATUS,"baseline independent Forge gate not passing")
    gate.require(baseline.get('preflight')=='PASS_GOLD_AND_PINNED_ARTIFACT_PREFLIGHT_NOT_EVM_CERTIFIED',"baseline preflight not passing")
    gate.require(baseline.get('source_report_sha256')==gate.digest_json(report),"baseline report digest mismatch")
    gate.require(baseline.get('independent_gold_sha256')==gate.digest_json(gold),"baseline gold digest mismatch")
    gate.require(baseline.get('cases_passed')==6 and baseline.get('forge_gold_tests_passed')==52,"baseline expected 52/52")
    gate.require(baseline.get('no_remote_rpc_no_keys_no_live_deployment') is True,"baseline must be disposable/local")
    gate.require(baseline.get('expert_gold_independence_verified') is False,"unexpected expert independence flag")
    image_id=baseline.get('foundry_image_id','')
    gate.require(isinstance(image_id,str) and bool(re.fullmatch(r'sha256:[a-f0-9]{64}',image_id)),"missing baseline Docker image digest")
    cases=baseline.get('cases')
    gate.require(isinstance(cases,list) and len(cases)==6,"baseline case count mismatch")
    by_id={c['id']:c for c in cases}
    gate.require(len(by_id)==6 and set(by_id)==set(CASES),"baseline case IDs mismatch")
    originals={c['id']:c for c in report['cases'] if c['expect']=='accept'}
    gold_by_id={c['id']:c for c in gold['cases']}
    for cid in CASES:
        c=by_id[cid]
        g=gold_by_id[cid]
        fund=[s for seq in g['sequences'] for s in gate._flatten_steps(seq['steps'])
              if s.get('type')=='send' and s.get('signature')=='fund()' and s.get('expect')=='success']
        gate.require(bool(fund),f"{cid}: missing gold fund")
        amt=gate.parse_wei(fund[0]['value_wei'],"gold funding")
        suite,n=forge_gold.generated_suite(g,amt)
        gate.require(n == c.get('generated_gold_tests') == c.get('passed_gold_tests'),f"{cid}: baseline test count mismatch")
        gate.require(c.get('docker_exit_code')==0,f"{cid}: baseline Docker execution failed")
        gate.require(c.get('source_sha256')==originals[cid]['solidity_sha256'],f"{cid}: baseline source digest mismatch")
        gate.require(c.get('independent_gold_solidity_sha256')==gate.digest(suite.encode()),f"{cid}: gold test Solidity changed")
    return by_id


def classify_execution(exit_code:int,output:str,expected:int)->dict[str,Any]:
    matches=SUMMARY.findall(output)
    if not matches:
        return {'outcome':'INVALID_RUN','reason':'no complete Forge test summary; compile/infrastructure failure cannot count as killed'}
    passed,failed,skipped=map(int,matches[-1])
    if passed+failed+skipped != expected or skipped:
        return {'outcome':'INVALID_RUN','reason':'suite test count mismatch or skipped tests','passed':passed,'failed':failed,'skipped':skipped}
    if failed>0 and exit_code!=0:
        return {'outcome':'KILLED_BY_GOLD_ASSERTION','passed':passed,'failed':failed,'skipped':skipped}
    if failed==0 and exit_code==0:
        return {'outcome':'SURVIVED_GOLD','passed':passed,'failed':0,'skipped':0}
    return {'outcome':'INVALID_RUN','reason':'Docker/Forge exit status contradicts test results','passed':passed,'failed':failed,'skipped':skipped}


def selected_mutations(ids: list[str]|None)->tuple[Mutation,...]:
    if not ids: return MUTANTS
    gate.require(len(set(ids))==len(ids),'duplicate --mutant ID')
    lookup={m.id:m for m in MUTANTS}
    gate.require(set(ids).issubset(lookup),f"unknown mutant IDs: {sorted(set(ids)-set(lookup))}")
    return tuple(lookup[x] for x in ids)


def run_campaign(report:dict[str,Any],gold:dict[str,Any],baseline:dict[str,Any],*,
                 report_root:Path,repo_root:Path,output:Path,
                 evidence_lock:dict[str,Any]|None=None,report_path:Path|None=None,gold_path:Path|None=None,baseline_path:Path|None=None,
                 ids:list[str]|None=None,
                 docker:str='docker',network:str='bridge',timeout:float=360.)->dict[str,Any]:
    gate.require(not output.exists(),f"mutation campaign report already exists: {output}")
    gate.require(evidence_lock is not None and report_path is not None and gold_path is not None and baseline_path is not None,
                 'content-addressed evidence lock and original file paths are required')
    from tools import nanojev_contract_v3_evidence_freeze as freeze
    freeze.verify_lock(evidence_lock,report,gold,baseline,report_path=report_path,
                       gold_path=gold_path,baseline_path=baseline_path,
                       workspaces_root=report_root,repo_root=repo_root)
    gate.require(shutil.which(docker) is not None,'Docker executable required')
    gate.preflight_gold_runtime(report,gold,report_root,repo_root=repo_root)
    base=validate_baseline(report,gold,baseline)
    image=baseline['foundry_image']
    gate.require(isinstance(image,str) and bool(re.fullmatch(r'[a-zA-Z0-9_./:@-]+',image)),"invalid image")
    image_run=subprocess.run([docker,'image','inspect','--format','{{.Id}}',image],capture_output=True,text=True,timeout=30)
    gate.require(image_run.returncode==0 and image_run.stdout.strip()==baseline['foundry_image_id'],
                 'Foundry image changed since 52/52 baseline; restore pinned image')
    selected=selected_mutations(ids)
    root=output.parent/(output.stem+'.mutation.workspaces')
    gate.require(not root.exists(),f"mutation workspace root exists: {root}")
    originals={c['id']:c for c in report['cases'] if c['expect']=='accept'}
    gold_by_id={c['id']:c for c in gold['cases']}
    root.mkdir(parents=True,exist_ok=False)
    # Challenge tests are independent, explicit additions to the ORIGINAL gold.
    # They must first PASS on frozen, pristine Solidity. A broken challenge
    # never qualifies as evidence that a mutation has been killed.
    probe_baselines=[]
    for cid in dict.fromkeys(m.case_id for m in selected):
        if cid not in PROBE_BODIES:continue
        g=gold_by_id[cid]
        fund=next(s for seq in g['sequences'] for s in gate._flatten_steps(seq['steps'])
                  if s.get('type')=='send' and s.get('signature')=='fund()' and s.get('expect')=='success')
        probe,probe_count=generated_probes(cid,gate.parse_wei(fund['value_wei'],'funding'))
        original=report_root/PureWindowsPath(originals[cid]['workspace']).name
        target=root/('pristine-probe-baseline-'+cid+'.workspace')
        (target/'src').mkdir(parents=True)
        (target/'test').mkdir()
        shutil.copyfile(original/'src/NanoJevGoverned01.sol',target/'src/NanoJevGoverned01.sol')
        shutil.copyfile(original/'foundry.toml',target/'foundry.toml')
        (target/'test/NanoJevAdversarialProbes.t.sol').write_text(probe,encoding='utf8')
        gate.canonical_solidity_sha256(target/'src/NanoJevGoverned01.sol',originals[cid]['solidity_sha256'])
        command=[docker,'run','--rm','--network',network,'--mount',f'type=bind,source={target.resolve()},target=/app',
                 '-w','/app','--entrypoint','forge',image,'test','--match-path','test/NanoJevAdversarialProbes.t.sol','-vv']
        try:
            proc=subprocess.run(command,capture_output=True,text=True,timeout=timeout,check=False)
        except subprocess.TimeoutExpired:
            raise gate.CertificationFailure('pristine boundary probe timeout: '+cid)
        assessment=classify_execution(proc.returncode,proc.stdout+'\n'+proc.stderr,probe_count)
        gate.require(assessment['outcome']=='SURVIVED_GOLD',
                     f'pristine boundary probe FAIL for {cid}: {assessment}; '+(proc.stdout+'\n'+proc.stderr)[-1200:])
        probe_baselines.append({'case_id':cid,'tests_passed':probe_count,
                                'probe_sha256':gate.digest(probe.encode()),
                                'pristine_source_sha256':originals[cid]['solidity_sha256'],
                                'workspace':str(target)})
    evidence=[]
    for mutant in selected:
        g=gold_by_id[mutant.case_id]
        count=witness_count(g,mutant)
        gate.require(count>0,f"{mutant.id}: gold has no stated witness for {mutant.witness_signature}")
        r=originals[mutant.case_id]
        original=report_root/PureWindowsPath(r['workspace']).name
        source=original/'src/NanoJevGoverned01.sol'
        gate.canonical_solidity_sha256(source,r['solidity_sha256'])
        original_content=source.read_text(encoding='utf-8')
        mutated=apply_mutation(original_content,mutant)
        target=root/(mutant.id+'-'+mutant.case_id+'.workspace')
        (target/'src').mkdir(parents=True)
        (target/'test').mkdir()
        # Preserve source's line ending; only the copied workspace is mutated.
        (target/'src/NanoJevGoverned01.sol').write_bytes(mutated.replace('\n','\r\n').encode('utf-8') if b'\r\n' in source.read_bytes() else mutated.encode('utf-8'))
        shutil.copyfile(original/'foundry.toml',target/'foundry.toml')
        fund=next(s for seq in g['sequences'] for s in gate._flatten_steps(seq['steps'])
                  if s.get('type')=='send' and s.get('signature')=='fund()' and s.get('expect')=='success')
        amount=gate.parse_wei(fund['value_wei'],'funding')
        suite, original_expected=forge_gold.generated_suite(g,amount)
        probe, added_probes=generated_probes(mutant.case_id,amount)
        expected=original_expected+added_probes
        gate.require(gate.digest(suite.encode())==base[mutant.case_id]['independent_gold_solidity_sha256'],
                     'gold Solidity changed after baseline')
        (target/'test'/forge_gold.TEST_NAME).write_text(suite,encoding='utf-8')
        if added_probes:
            (target/'test/NanoJevAdversarialProbes.t.sol').write_text(probe,encoding='utf8')
        command=[docker,'run','--rm','--network',network,'--mount',f'type=bind,source={target.resolve()},target=/app',
                 '-w','/app','--entrypoint','forge',image,'test','-vv']
        try:
            proc=subprocess.run(command,capture_output=True,text=True,timeout=timeout,check=False)
            outcome=classify_execution(proc.returncode,proc.stdout+'\n'+proc.stderr,expected)
            exit_code=proc.returncode
            tail=(proc.stdout+'\n'+proc.stderr)[-1400:]
        except subprocess.TimeoutExpired:
            outcome={'outcome':'INVALID_RUN','reason':'timeout executing Docker Forge'}
            exit_code=None;tail=''
        evidence.append({'id':mutant.id,'case_id':mutant.case_id,'domain':mutant.domain,
            'mechanism':mutant.mechanism,'original_source_sha256':r['solidity_sha256'],
            'mutated_source_sha256':gate.digest(mutated.encode()),
            'gold_test_sha256':gate.digest(suite.encode()),
            'expected_gold_tests':original_expected,'extra_pristine_validated_probes':added_probes,
            'expected_total_tests':expected,'probe_sha256':gate.digest(probe.encode()) if probe else None,'witness_count':count,
            'docker_exit_code':exit_code,'workspace':str(target),'execution':outcome,
            'forge_output_tail':tail})
    completed=len(selected)==len(MUTANTS)
    killed=sum(e['execution']['outcome']=='KILLED_BY_GOLD_ASSERTION' for e in evidence)
    survivors=[e['id'] for e in evidence if e['execution']['outcome']=='SURVIVED_GOLD']
    invalid=[e['id'] for e in evidence if e['execution']['outcome']=='INVALID_RUN']
    # Verify frozen inputs remain intact after testing, not only before.
    gate.verify_kernel_on_disk(report,repo_root)
    for cid,c in originals.items():
        gate.canonical_solidity_sha256(report_root/PureWindowsPath(c['workspace']).name/'src/NanoJevGoverned01.sol',
                                     c['solidity_sha256'])
    passed=completed and killed==len(MUTANTS) and not survivors and not invalid
    return {'schema':SCHEMA,'status':PASS if passed else FAIL,'source_report_sha256':gate.digest_json(report),
        'independent_gold_sha256':gate.digest_json(gold),'baseline_certificate_sha256':gate.digest_json(baseline),
        'evidence_lock_sha256':gate.digest_json(evidence_lock),
        'foundry_image':image,'foundry_image_id':baseline['foundry_image_id'],
        'catalog_size':len(MUTANTS),'selected_mutants':len(selected),'campaign_complete':completed,
        'pristine_probe_baselines':probe_baselines,
        'pristine_probe_tests_passed':sum(p['tests_passed'] for p in probe_baselines),
        'killed_by_Forge_assertion':killed,'survived':survivors,'invalid_runs':invalid,
        'no_remote_rpc_no_keys_no_live_deployment':True,
        'expert_gold_independence_verified':False,'frozen_inputs_reverified':True,'mutations':evidence}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--gold',type=Path,required=True)
    p.add_argument('--baseline-cert',type=Path,required=True)
    p.add_argument('--evidence-lock',type=Path,required=True)
    p.add_argument('--workspaces-root',type=Path,default=Path('runtime/reports'))
    p.add_argument('--repo-root',type=Path,default=Path.cwd())
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--docker-network',choices=('none','bridge'),default='bridge')
    p.add_argument('--timeout',type=float,default=360)
    p.add_argument('--mutant',action='append',default=[])
    args=p.parse_args(argv)
    try:
        result=run_campaign(gate.read_json(args.report),gate.read_json(args.gold),gate.read_json(args.baseline_cert),
            report_root=args.workspaces_root,repo_root=args.repo_root,output=args.output,
            evidence_lock=gate.read_json(args.evidence_lock),report_path=args.report,gold_path=args.gold,baseline_path=args.baseline_cert,
            ids=args.mutant,network=args.docker_network,timeout=args.timeout)
        gate.atomic_json(args.output,result)
        print(json.dumps({'status':result['status'],'killed':result['killed_by_Forge_assertion'],
                          'selected':result['selected_mutants'],'survived':result['survived'],
                          'invalid':result['invalid_runs'],'output':str(args.output)}))
        return 0 if result['status']==PASS else 1
    except (gate.CertificationFailure,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as exc:
        result={'schema':SCHEMA,'status':FAIL,'blockers':[str(exc)],'mutations':[]}
        if not args.output.exists():gate.atomic_json(args.output,result)
        print(json.dumps(result),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
