"""Run independently authored V3 behavioral gold against real disposable Docker Forge EVM.

Does not call or import the V3 compiler, transition kernel, language, or reference.
On-chain results are compared with separately supplied, frozen gold JSON.
Refuses expert certification and never deploys outside local Forge tests.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import sys
from typing import Any

from tools import nanojev_contract_v3_independent_behavior as gate

IMAGE = "ghcr.io/foundry-rs/foundry:latest"
TEST_NAME = "NanoJevIndependentGold.t.sol"
WORD = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*\([a-zA-Z0-9_,\[\]]*\)$")
ADDR = {"payer": "PAYER", "worker": "WORKER", "outsider": "OUTSIDER", "arbitrator": "ARBITRATOR", "contract": "address(c)"}
BAL = {"payer": "PAYER.balance", "worker": "WORKER.balance", "outsider": "OUTSIDER.balance", "arbitrator": "ARBITRATOR.balance", "contract": "address(c).balance"}


def _arg(value: Any) -> str:
    if isinstance(value, str) and value.startswith("$"):
        gate.require(value[1:] in ADDR and value[1:] != "contract", "unsupported actor argument")
        return ADDR[value[1:]]
    if type(value) is int or (isinstance(value, str) and value.isdigit()):
        return str(value)
    if type(value) is bool:
        return "true" if value else "false"
    raise gate.CertificationFailure(f"unsupported ABI argument literal: {value!r}")


def _calldata(step: dict[str, Any]) -> str:
    sig = step["signature"]
    gate.require(bool(WORD.fullmatch(sig)), f"unsafe/unsupported signature: {sig}")
    args = ", ".join(_arg(a) for a in step.get("args", []))
    return f'abi.encodeWithSignature("{sig}"' + (", " + args if args else "") + ")"


def _check_amount(value: Any) -> str:
    num = gate.parse_wei(value, "independent Forge gold wei")
    gate.require(abs(num) < 2**254, "expected gold amount exceeds safe signed int range")
    return str(num)


def _generate_one(test_index: int, steps: list[dict[str, Any]], final: dict[str, Any], amount: int) -> str:
    lines = [
        "vm.warp(1700000000);",
        f"vm.deal(PAYER, {amount * 4});",
        f"vm.deal(WORKER, {amount * 4});",
        f"vm.deal(OUTSIDER, {amount * 4});",
        f"vm.deal(ARBITRATOR, {amount * 4});",
        "vm.prank(PAYER);",
        "NanoJevGoverned01 c = new NanoJevGoverned01(payable(WORKER));",
    ]
    for alias in ("payer", "worker", "outsider", "arbitrator", "contract"):
        lines.append(f"int256 base_{alias} = int256({BAL[alias]});")
    for idx,step in enumerate(steps):
        kind=step["type"]
        tag=f"t{test_index}s{idx}"
        if kind=="time":
            lines.append(f"vm.warp(block.timestamp + {step['advance_seconds']});")
        elif kind=="view":
            actor=step["actor"]
            lines.append("{")
            lines.append(f"vm.prank({ADDR[actor]});")
            lines.append(f"(bool ok, bytes memory data) = address(c).staticcall({_calldata(step)});")
            lines.append(f'require(ok && data.length == 32, "{tag}: view failed");')
            if "expect_bool" in step:
                expected="true" if step["expect_bool"] else "false"
                lines.append(f'require(abi.decode(data, (bool)) == {expected}, "{tag}: boolean mismatch");')
            elif "expect_uint" in step:
                lines.append(f'require(abi.decode(data, (uint256)) == {_check_amount(step["expect_uint"])}, "{tag}: uint mismatch");')
            elif "expect_hex" in step:
                raw=str(step["expect_hex"])
                gate.require(raw.startswith("0x") and all(x in "0123456789abcdefABCDEF" for x in raw[2:]), "invalid expected hex")
                lines.append(f'require(keccak256(data) == keccak256(hex"{raw[2:]}"), "{tag}: raw mismatch");')
            else:
                raise gate.CertificationFailure("view without explicit oracle")
            lines.append("}")
        elif kind=="send":
            changes=step.get("changes_wei",{})
            lines.append("{")
            for a in BAL:
                lines.append(f"int256 before_{a} = int256({BAL[a]});")
            lines.append(f"vm.prank({ADDR[step['actor']]});")
            value=_check_amount(step.get("value_wei","0"))
            lines.append(f"(bool ok,) = address(c).call{{value: {value}}}({_calldata(step)});")
            expected="true" if step["expect"]=="success" else "false"
            lines.append(f'require(ok == {expected}, "{tag}: outcome mismatch");')
            sums=" + ".join(f"{BAL[a]}" for a in BAL)
            before_sums=" + ".join(f"uint256(before_{a})" for a in BAL)
            lines.append(f'require({sums} == {before_sums}, "{tag}: conservation failure");')
            for a,v in changes.items():
                gate.require(a in BAL,f"unexpected economic role: {a}")
                lines.append(f'require(int256({BAL[a]}) - before_{a} == {_check_amount(v)}, "{tag}: {a} delta mismatch");')
            lines.append("}")
        elif kind=="assert":
            if "contract_balance_wei" in step:
                lines.append(f'require(address(c).balance == {_check_amount(step["contract_balance_wei"])}, "{tag}: balance mismatch");')
            for alias,v in step.get("balances_delta_wei",{}).items():
                gate.require(alias in BAL,f"unknown economic role: {alias}")
                lines.append(f'require(int256({BAL[alias]}) - base_{alias} == {_check_amount(v)}, "{tag}: final role {alias}");')
        else:
            raise gate.CertificationFailure(f"bad gold step: {kind}")
    if "contract_balance_wei" in final:
        lines.append(f'require(address(c).balance == {_check_amount(final["contract_balance_wei"])}, "gold terminal balance");')
    for alias,v in final.get("balances_delta_wei",{}).items():
        gate.require(alias in BAL, f"unknown final balance alias {alias}")
        lines.append(f'require(int256({BAL[alias]}) - base_{alias} == {_check_amount(v)}, "gold final {alias}");')
    body="\n".join("        "+line for line in lines)
    return f'''contract NanoJevIndependentGoldTrace{test_index} is NanoJevIndependentBase {{
    function testIndependentGoldTrace{test_index}() public {{
{body}
    }}
}}
'''


def generated_suite(case: dict[str, Any], amount: int) -> tuple[str,int]:
    output=['''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/NanoJevGoverned01.sol";
interface VmIndependentGold {
    function prank(address who) external;
    function deal(address who, uint256 amount) external;
    function warp(uint256 timestamp) external;
}
abstract contract NanoJevIndependentBase {
    VmIndependentGold internal constant vm = VmIndependentGold(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant PAYER = address(uint160(0xAAA1));
    address internal constant WORKER = address(uint160(0xBBB1));
    address internal constant OUTSIDER = address(uint160(0xCCC1));
    address internal constant ARBITRATOR = address(uint160(0xDDD1));
}
''']
    count=0
    for seq in case["sequences"]:
        for variation in gate.expanded_sequences(seq["steps"]):
            count+=1
            output.append(_generate_one(count,variation,seq.get("final",{}),amount))
    return "\n".join(output), count


def onchain_docker(report: dict[str,Any],gold: dict[str,Any],*,report_root:Path,repo_root:Path,
                   output:Path,image:str=IMAGE,network:str="bridge",timeout:float=360.0)->dict[str,Any]:
    gate.require(shutil.which("docker") is not None,"Docker executable required for the Docker Forge path")
    gate.require(not output.exists(), f"certificate output exists; refuse overwrite: {output}")
    gate.require(network in {"none","bridge"},"unsupported Docker network")
    gate.require(timeout>0,"positive Docker timeout required")
    gate.require(bool(re.fullmatch(r"[a-zA-Z0-9_./:@-]+",image)),"invalid Docker image reference")
    check=gate.preflight_gold_runtime(report,gold,report_root,repo_root=repo_root)
    image_info=subprocess.run(["docker","image","inspect","--format","{{.Id}}",image],
                              capture_output=True,text=True,timeout=30,check=False)
    gate.require(image_info.returncode==0 and image_info.stdout.strip().startswith("sha256:"),
                 "Docker Foundry image must be present locally; inspect failed: " + image_info.stderr.strip()[-400:])
    image_id=image_info.stdout.strip()
    case_by_id={c["id"]:c for c in gold["cases"]}
    report_by_id={c["id"]:c for c in report["cases"]}
    proof=[]
    for id in case_by_id:
        orig=report_root/PureWindowsPath(report_by_id[id]["workspace"]).name
        compiled=gate.read_json(orig/"out/NanoJevGoverned01.sol/NanoJevGoverned01.json")
        # The amount is a public contract constant, but must agree with the gold funding amount.
        # Inspect only typed gold transactions; do not call the V3 reference model.
        fund_steps=[st for seq in case_by_id[id]["sequences"] for st in gate._flatten_steps(seq["steps"])
                    if st.get("type")=="send" and st["signature"]=="fund()" and st["expect"]=="success"]
        gate.require(fund_steps,"independent vectors lack funding")
        amount=gate.parse_wei(fund_steps[0]["value_wei"],"funding amount")
        gate.require(amount>0 and all(gate.parse_wei(st["value_wei"],"fund") == amount for st in fund_steps),"inconsistent funding in gold")
        suite,expected_tests=generated_suite(case_by_id[id],amount)
        target=output.parent/(output.stem+"-"+id+".independent.workspace")
        gate.require(not target.exists(),f"output workspace already exists, refusing overwrite: {target}")
        (target/"src").mkdir(parents=True)
        (target/"test").mkdir()
        shutil.copyfile(orig/"src/NanoJevGoverned01.sol",target/"src/NanoJevGoverned01.sol")
        shutil.copyfile(orig/"foundry.toml",target/"foundry.toml")
        (target/"test"/TEST_NAME).write_text(suite,encoding="utf-8")
        gate.canonical_solidity_sha256(target/"src/NanoJevGoverned01.sol", report_by_id[id]["solidity_sha256"])
        command=["docker","run","--rm","--network",network,"--mount",f"type=bind,source={target.resolve()},target=/app", "-w","/app",
                 "--entrypoint","forge", image, "test", "--match-path",f"test/{TEST_NAME}","-vv"]
        try:
            execution=subprocess.run(command,capture_output=True,text=True,timeout=timeout,check=False)
        except subprocess.TimeoutExpired:
            raise gate.CertificationFailure(f"Docker Forge gold timeout: {id}")
        combined=execution.stdout+"\n"+execution.stderr
        gate.require(execution.returncode==0,f"Docker Forge gold failed: {id}: {combined[-1600:]}")
        # Fail closed on missing/misreported Forge test summary even with exit 0.
        matches=re.findall(r"Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped",combined)
        gate.require(matches and tuple(map(int,matches[-1]))==(expected_tests,0,0),
                     f"Docker Forge gold count mismatch: {id}, expected {expected_tests}, got {matches[-1:]!r}; tail={combined[-1200:]}")
        proof.append({"id":id,"generated_gold_tests":expected_tests,"passed_gold_tests":expected_tests,
                      "independent_gold_solidity_sha256":gate.digest(suite.encode()),
                      "source_sha256":report_by_id[id]["solidity_sha256"],"workspace":str(target),
                      "docker_exit_code":execution.returncode,"test_stdout_tail":execution.stdout[-600:]})
    return {"schema":gate.CERT_SCHEMA,
            "status":"PASS_SEPARATE_GOLD_DOCKER_FORGE_EXECUTION_NOT_EXPERT_CERTIFIED",
            "source_report_sha256":gate.digest_json(report),"independent_gold_sha256":gate.digest_json(gold),
            "cases_passed":len(proof),"forge_gold_tests_passed":sum(c["passed_gold_tests"] for c in proof),
            "preflight":check["status"],"foundry_image":image,"foundry_image_id":image_id,
            "no_remote_rpc_no_keys_no_live_deployment":True,
            "expert_gold_independence_verified":False,"cases":proof}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,required=True)
    p.add_argument("--gold",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--workspaces-root",type=Path,default=Path("runtime/reports"))
    p.add_argument("--repo-root",type=Path,default=Path.cwd())
    p.add_argument("--docker-network",choices=("none","bridge"),default="bridge")
    p.add_argument("--foundry-image",default=IMAGE)
    p.add_argument("--timeout",type=float,default=360)
    args=p.parse_args(argv)
    try:
        result=onchain_docker(gate.read_json(args.report),gate.read_json(args.gold),
            report_root=args.workspaces_root,repo_root=args.repo_root,output=args.output,
            image=args.foundry_image,network=args.docker_network,timeout=args.timeout)
        gate.atomic_json(args.output,result)
        print(json.dumps({"status":result["status"],"cases_passed":result["cases_passed"],
                          "forge_gold_tests_passed":result["forge_gold_tests_passed"],"output":str(args.output)}))
        return 0
    except (gate.CertificationFailure,OSError,ValueError,KeyError,subprocess.TimeoutExpired) as e:
        result={"schema":gate.CERT_SCHEMA,"status":"FAIL_INDEPENDENT_BEHAVIOR_NOT_CERTIFIED","blockers":[str(e)]}
        gate.atomic_json(args.output,result)
        print(json.dumps(result),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
