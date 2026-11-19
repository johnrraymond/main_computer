#!/usr/bin/env python3
"""Read-only NL -> live Ollama head -> typed contract -> Forge Docker smoke.

This is an intentionally narrow, independent escrow acceptance smoke, not a
production contract compiler. No keys, RPC, deployment, transaction signing,
model training, or production mutation. The known-intent oracle is *never*
passed to the LLM. Docker mounts only a new, private output workspace.

Run from the repository root:
    & $py -m tools.nanojev_contract_solidity_smoke \
        --llm-model gemma4:26b-a4b-it-q4_K_M \
        --output runtime/reports/nanojev-contract-solidity-smoke-0001.json

Requires local Ollama at 127.0.0.1:11434, Docker daemon, and the Foundry image.
Docker 'bridge' networking allows the image to acquire solc 0.8.24 when absent;
it is never given an RPC endpoint or any credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

SCHEMA = "nanojev.contract.escrow.v1"
DEFAULT_MODEL = "gemma4:26b-a4b-it-q4_K_M"  # MainComputerConfig snapshot default
DEFAULT_IMAGE = "ghcr.io/foundry-rs/foundry:latest"  # existing repo convention
DEFAULT_URL = "http://127.0.0.1:11434"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "nanojev_contract_solidity_smoke_v1.json"
EXPECTED_FIXTURE_SHA256 = "90ea98ea8f03eb1a7eb6028d73093d35eeb773503ce767b6822ab556e0454f1a"
FIXED_VALUES: dict[str, Any] = {
    "language": SCHEMA,
    "asset": "native_eth",
    "buyer": "deployer",
    "seller": "constructor_payee",
    "funding_authorizer": "buyer",
    "release_authorizer": "buyer",
    "release_trigger": "buyer_confirms_delivery",
    "release_window": "strictly_before_deadline",
    "refund_authorizer": "buyer",
    "refund_trigger": "deadline_elapsed_without_release",
    "timeout_seconds": 2592000,
    "settlement": "one_of_release_or_refund_once",
}
CONTRACT_FIELDS = frozenset({*FIXED_VALUES, "amount_wei"})
SYSTEM_PROMPT = """You are the natural-language-to-typed-contract translation head in a smoke test.
Return ONLY one JSON object. There are two possible results:
- If ALL essential conditions are explicit: {"status":"contract","contract":{...}}.
- If ANY essential condition is missing, uncertain, inconsistent, or beyond schema:
  {"status":"needs_clarification","questions":["specific missing question",...]}.
Never infer absent terms. No Solidity, no markdown, no explanations, no extra keys.

The ONLY supported contract is a funded native-ETH escrow in which the buyer
is the deployment address, the seller is supplied in the constructor, the buyer
funds an EXACT positive amount, only the buyer confirms delivery to RELEASE to
seller strictly BEFORE the timeout from funding, and only the buyer can REFUND
at or AFTER timeout if there was no release. There is at most one settlement.
You must not convert 'not obligated' into prohibited, or treat non-operative
proposals as operative. The wording must explicitly provide all these terms.
For supported requests, the 'contract' object has EXACTLY these fields:
 language = 'nanojev.contract.escrow.v1'
 asset = 'native_eth'
 buyer = 'deployer'
 seller = 'constructor_payee'
 amount_wei = exact integer number of wei described by user (1 ETH = 10^18 wei)
 funding_authorizer = 'buyer'
 release_authorizer = 'buyer'
 release_trigger = 'buyer_confirms_delivery'
 release_window = 'strictly_before_deadline'
 refund_authorizer = 'buyer'
 refund_trigger = 'deadline_elapsed_without_release'
 timeout_seconds = exact integer seconds from user (one day = 86400 seconds)
 settlement = 'one_of_release_or_refund_once'
Do not output a contract if the request does not explicitly specify the exact
amount, buyer/seller roles, funding, confirmation authority, release timing,
refund authority, refund conditions, deadline, and exclusive settlement.
"""


class SmokeFailure(Exception):
    def __init__(self, stage: str, reason: str):
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


def digest(payload: bytes | str) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_url(raw: str) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment):
        raise SmokeFailure("preflight", "Only a local HTTP Ollama base URL is accepted")
    return raw.rstrip("/")


def fixture() -> dict[str, Any]:
    content = FIXTURE_PATH.read_bytes()
    if digest(content) != EXPECTED_FIXTURE_SHA256:
        raise SmokeFailure("preflight", "Independent smoke fixture hash changed; candidate locked")
    data = json.loads(content.decode("utf-8"))
    if data.get("schema") != "nanojev.contract-solidity-smoke.fixtures.v1":
        raise SmokeFailure("preflight", "Unexpected independent-oracle fixture schema")
    cases = data.get("cases")
    if not isinstance(cases, list) or [x.get("id") for x in cases] != [
        "escrow_baseline", "escrow_paraphrase", "escrow_amount_change", "escrow_ambiguous"
    ]:
        raise SmokeFailure("preflight", "Smoke fixture case identity or order changed")
    return data


def expected_spec(amount: int) -> dict[str, Any]:
    return {**FIXED_VALUES, "amount_wei": amount}


def parse_head_output(raw: str) -> dict[str, Any]:
    if len(raw) > 40_000:
        raise SmokeFailure("llm", "Head JSON exceeds 40000 characters")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SmokeFailure("llm", "Head response is not a standalone JSON object") from exc
    if not isinstance(data, dict):
        raise SmokeFailure("llm", "Head response must be a JSON object")
    return data


def validate_response(case: dict[str, Any], data: dict[str, Any]) -> dict[str, Any] | None:
    if case["family"] == "needs_clarification":
        if set(data) != {"status", "questions"} or data["status"] != "needs_clarification":
            raise SmokeFailure("semantic_oracle", "Ambiguous request was not explicitly refused")
        questions = data["questions"]
        if (not isinstance(questions, list) or not questions or len(questions) > 12
                or any(not isinstance(x, str) or not x.strip() or len(x) > 400 for x in questions)):
            raise SmokeFailure("semantic_oracle", "Ambiguity questions are empty or invalid")
        all_questions = " ".join(questions).lower()
        required = {
            "amount": ("amount", "how much", "sum", "value", "ether", "eth"),
            "deadline": ("deadline", "when", "time", "duration", "days", "period"),
            "release_authority": ("who", "authorize", "confirm", "approval", "approve", "release"),
            "refund_condition": ("refund", "return", "condition", "trigger", "failure"),
        }
        if any(not any(s in all_questions for s in required[key]) for key in case["missing"]):
            raise SmokeFailure("semantic_oracle", "Clarification did not identify every material ambiguity")
        return None
    if set(data) != {"status", "contract"} or data.get("status") != "contract":
        raise SmokeFailure("semantic_oracle", f"Valid request {case['id']} was not translated")
    obj = data["contract"]
    if not isinstance(obj, dict) or set(obj) != CONTRACT_FIELDS:
        raise SmokeFailure("contract_validation", f"Contract field set unsupported in {case['id']}")
    amount = obj["amount_wei"]
    deadline = obj["timeout_seconds"]
    if type(amount) is not int or not 0 < amount < 2**128 or type(deadline) is not int:
        raise SmokeFailure("contract_validation", f"Numeric contract fields invalid in {case['id']}")
    for key, value in FIXED_VALUES.items():
        if type(obj[key]) is not type(value) or obj[key] != value:
            raise SmokeFailure("contract_validation", f"Unsupported or inconsistent field {key} in {case['id']}")
    # Separate independent oracle. The head receives ONLY request text and generic schema.
    if obj != expected_spec(case["expected_amount_wei"]):
        mistakes = {key: {"got": obj.get(key), "required": value}
                    for key, value in expected_spec(case["expected_amount_wei"]).items()
                    if obj.get(key) != value}
        raise SmokeFailure("semantic_oracle", f"Intent-to-contract mismatch in {case['id']}: {canonical(mistakes)}")
    return obj


def call_ollama(*, base_url: str, model: str, text: str, timeout: float) -> str:
    base_url = local_url(base_url)
    if not model.strip() or len(model) > 256:
        raise SmokeFailure("preflight", "Invalid Ollama model identifier")
    request = {
        "model": model,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0, "seed": 261118, "num_predict": 900},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": text}],
    }
    try:
        req = Request(f"{base_url}/api/chat", data=canonical(request).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        with urlopen(req, timeout=timeout) as res:
            raw = res.read(200_001)
    except Exception as exc:
        raise SmokeFailure("llm", f"Local Ollama request failed: {type(exc).__name__}: {str(exc)[:300]}") from exc
    if len(raw) > 200_000:
        raise SmokeFailure("llm", "Ollama HTTP reply too large")
    try:
        response = json.loads(raw.decode("utf-8", errors="strict"))
        content = response["message"]["content"]
    except (UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        raise SmokeFailure("llm", "Ollama reply has no valid JSON message.content") from exc
    if not isinstance(content, str) or not content.strip():
        raise SmokeFailure("llm", "Ollama head returned no text")
    return content


def source_for(spec: dict[str, Any], name: str) -> str:
    if not name.isidentifier() or not name.startswith("NanoJevEscrow"):
        raise SmokeFailure("solidity_emit", "Invalid generated Solidity identifier")
    if spec != expected_spec(spec["amount_wei"]):
        raise SmokeFailure("solidity_emit", "Code generation requires validated supported spec")
    amount = spec["amount_wei"]
    timeout = spec["timeout_seconds"]
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Smoke-only deterministic implementation of {SCHEMA}.
/// @dev Not audited, not for deployment. This source is never supplied by the LLM.
contract {name} {{
    enum State {{ Unfunded, Funded, Released, Refunded }}
    address public immutable buyer;
    address payable public immutable seller;
    uint256 public constant AMOUNT_WEI = {amount};
    uint256 public constant TIMEOUT_SECONDS = {timeout};
    uint256 public fundedAt;
    State public state;

    constructor(address payable seller_) {{
        require(seller_ != address(0), "zero seller");
        require(seller_ != msg.sender, "identical parties");
        buyer = msg.sender;
        seller = seller_;
    }}
    modifier buyerOnly() {{
        require(msg.sender == buyer, "buyer only");
        _;
    }}
    function fund() external payable buyerOnly {{
        require(state == State.Unfunded, "already funded");
        require(msg.value == AMOUNT_WEI, "exact deposit required");
        fundedAt = block.timestamp;
        state = State.Funded;
    }}
    function confirmDelivery() external buyerOnly {{
        require(state == State.Funded, "not funded");
        require(block.timestamp < fundedAt + TIMEOUT_SECONDS, "too late");
        state = State.Released;  // effects before external payment
        (bool ok,) = seller.call{{value: AMOUNT_WEI}}("");
        require(ok, "release transfer failed");
    }}
    function refund() external buyerOnly {{
        require(state == State.Funded, "not funded");
        require(block.timestamp >= fundedAt + TIMEOUT_SECONDS, "too early");
        state = State.Refunded;  // effects before external payment
        (bool ok,) = payable(buyer).call{{value: AMOUNT_WEI}}("");
        require(ok, "refund transfer failed");
    }}
}}
'''


def test_for(spec: dict[str, Any], name: str) -> str:
    amount = spec["amount_wei"]
    timeout = spec["timeout_seconds"]
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface Vm {{
    function deal(address who, uint256 newBalance) external;
    function warp(uint256 newTimestamp) external;
    function prank(address sender) external;
}}
contract {name}SmokeTest {{
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {amount};
    uint256 internal constant TIMEOUT = {timeout};
    address payable internal constant SELLER = payable(address(0xBEEF));
    address internal constant STRANGER = address(0xBAD);
    receive() external payable {{}}

    function fresh() internal returns ({name} c) {{
        c = new {name}(SELLER);  // this test contract is buyer
        vm.deal(address(this), AMOUNT * 4);
    }}
    function funded() internal returns ({name} c) {{
        c = fresh();
        c.fund{{value: AMOUNT}}();
    }}
    function testReleaseBeforeDeadline() public {{
        {name} c = funded();
        uint256 previous = SELLER.balance;
        c.confirmDelivery();
        require(uint256(c.state()) == 2, "not released");
        require(SELLER.balance - previous == AMOUNT, "incorrect seller payout");
        require(address(c).balance == 0, "nonzero escrow after settlement");
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.refund.selector));
        require(!ok, "double settlement");
    }}
    function testRefundAtDeadline() public {{
        {name} c = funded();
        uint256 beforeBuyer = address(this).balance;
        vm.warp(c.fundedAt() + TIMEOUT);
        c.refund();
        require(uint256(c.state()) == 3, "not refunded");
        require(address(this).balance - beforeBuyer == AMOUNT, "incorrect refund");
        require(address(c).balance == 0, "nonzero escrow after refund");
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.confirmDelivery.selector));
        require(!ok, "release after refund");
    }}
    function testCannotRefundEarly() public {{
        {name} c = funded();
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.refund.selector));
        require(!ok, "early refund");
    }}
    function testCannotReleaseAfterDeadline() public {{
        {name} c = funded();
        vm.warp(c.fundedAt() + TIMEOUT);
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.confirmDelivery.selector));
        require(!ok, "late release");
    }}
    function testOnlyBuyerCanReleaseOrRefund() public {{
        {name} c = funded();
        vm.prank(STRANGER);
        (bool releaseOk,) = address(c).call(abi.encodeWithSelector(c.confirmDelivery.selector));
        require(!releaseOk, "outsider release");
        vm.warp(c.fundedAt() + TIMEOUT);
        vm.prank(STRANGER);
        (bool refundOk,) = address(c).call(abi.encodeWithSelector(c.refund.selector));
        require(!refundOk, "outsider refund");
    }}
    function testRejectWrongFundingAndSecondFunding() public {{
        {name} c = fresh();
        (bool wrong,) = address(c).call{{value: AMOUNT - 1}}(abi.encodeWithSelector(c.fund.selector));
        require(!wrong, "wrong funding amount accepted");
        c.fund{{value: AMOUNT}}();
        (bool again,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!again, "second funding accepted");
    }}
    function testRejectUnauthorizedFunding() public {{
        {name} c = fresh();
        vm.deal(STRANGER, AMOUNT);
        vm.prank(STRANGER);
        (bool ok,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "outsider funding accepted");
    }}
    function testCannotReleaseBeforeFunding() public {{
        {name} c = fresh();
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.confirmDelivery.selector));
        require(!ok, "unfunded release accepted");
    }}
    function testFuzzWrongDeposit(uint96 input) public {{
        {name} c = fresh();
        uint256 wrong = uint256(input) % AMOUNT;
        (bool ok,) = address(c).call{{value: wrong}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "wrong fuzz amount accepted");
    }}
}}
'''


def write_foundry_workspace(path: Path, baseline: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    src = path / "src"
    tests = path / "test"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (path / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\ntest = "test"\nout = "out"\n'
        'libs = []\nsolc_version = "0.8.24"\noptimizer = true\noptimizer_runs = 200\n',
        encoding="utf-8",
    )
    sources = {}
    for spec, name in [(baseline, "NanoJevEscrowBaseline"),
                       (variant, "NanoJevEscrowAmountVariant")]:
        solidity = source_for(spec, name)
        test = test_for(spec, name)
        (src / f"{name}.sol").write_text(solidity, encoding="utf-8")
        (tests / f"{name}.t.sol").write_text(test, encoding="utf-8")
        sources[name] = {"solidity_sha256": digest(solidity), "test_sha256": digest(test),
                         "amount_wei": spec["amount_wei"]}
    return sources


def docker_args(workspace: Path, image: str, network: str, operation: str) -> list[str]:
    if network not in {"bridge", "none"}:
        raise SmokeFailure("preflight", "Unsupported Docker networking mode")
    if not image or any(ch.isspace() for ch in image):
        raise SmokeFailure("preflight", "Invalid Forge image name")
    mount = workspace.resolve().as_posix() if os.name == "nt" else str(workspace.resolve())
    return ["docker", "run", "--rm", "--network", network,
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL", "--pids-limit", "256",
            "-e", "NO_COLOR=1", "-v", f"{mount}:/work", "-w", "/work",
            "--entrypoint", "forge", image, operation, *(["-vv"] if operation == "test" else [])]


def run_command(args: list[str], timeout: float) -> dict[str, Any]:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure("forge", f"Docker invocation failed: {type(exc).__name__}: {str(exc)[:300]}") from exc
    return {"exit_code": completed.returncode, "stdout_tail": completed.stdout[-16000:],
            "stderr_tail": completed.stderr[-6000:]}


@dataclass(frozen=True)
class Options:
    output: Path
    ollama_url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    foundry_image: str = DEFAULT_IMAGE
    docker_network: str = "bridge"
    llm_timeout_s: float = 240.0
    forge_timeout_s: float = 360.0
    dry_run: bool = False


def execute(options: Options, *, head: Callable[[str], str] | None = None,
            docker: Callable[[list[str], float], dict[str, Any]] | None = None,
            docker_available: bool | None = None) -> dict[str, Any]:
    """Execute smoke. Optional callables are for unit tests, never production certification."""
    output = options.output.resolve()
    workspace = output.with_name(output.stem + ".workspace")
    result: dict[str, Any] = {
        "schema": "nanojev.contract-solidity-smoke.v1",
        "status": "BLOCKED_NOT_VERIFIED",
        "started_at": now(),
        "mode": "READ_ONLY_NO_DEPLOYMENT_NO_KEYS_NO_MODEL_MUTATION",
        "implementation": "live_ollama_head_not_nanojev_clef",
        "model": options.model,
        "foundry_image": options.foundry_image,
        "docker_network": options.docker_network,
        "fixture_sha256": digest(FIXTURE_PATH.read_bytes()),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "assurance_limit": "Restricted escrow DSL; deterministic template; authored oracle; no independent audit or contract-language generalization certification",
        "stages": {},
        "cases": [],
        "blockers": [],
        "output": str(output),
        "workspace": str(workspace),
    }
    try:
        if output.exists() or workspace.exists():
            raise SmokeFailure("preflight", "Output report or workspace already exists; choose a new filename")
        local_url(options.ollama_url)
        data = fixture()
        if options.dry_run:
            result["status"] = "PLAN_ONLY_NOT_EXECUTED"
            result["stages"]["preflight"] = "PLAN_ONLY"
            result["llm_endpoint"] = options.ollama_url
            result["planned_cases"] = [c["id"] for c in data["cases"]]
            return result
        if options.llm_timeout_s <= 0 or options.forge_timeout_s <= 0:
            raise SmokeFailure("preflight", "Timeouts must be positive")
        if docker_available is None:
            docker_available = shutil.which("docker") is not None
        if not docker_available:
            raise SmokeFailure("preflight", "Docker CLI missing; Forge Docker compile was not attempted")
        output.parent.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True)
        result["stages"]["preflight"] = "PASS"
        actual_head = head or (lambda text: call_ollama(
            base_url=options.ollama_url, model=options.model, text=text, timeout=options.llm_timeout_s))
        specs: dict[str, dict[str, Any]] = {}
        for case in data["cases"]:
            print(f"[CONTRACT_SMOKE] LLM interpretation {case['id']}", flush=True)
            entry = {"id": case["id"], "family": case["family"],
                     "request_sha256": digest(case["request"])}
            result["cases"].append(entry)
            raw = actual_head(case["request"])
            entry["llm_output_sha256"] = digest(raw)
            entry["llm_output"] = raw[:4000]  # bounded diagnostic for failed semantics
            parsed = parse_head_output(raw)
            spec = validate_response(case, parsed)
            if spec is None:
                entry["result"] = "CORRECT_ABSTENTION"
            else:
                entry["result"] = "EXACT_INTENT_MATCH"
                entry["contract_sha256"] = digest(canonical(spec))
                specs[case["id"]] = spec
        if specs["escrow_baseline"] != specs["escrow_paraphrase"]:
            raise SmokeFailure("semantic_oracle", "Paraphrase changed canonical contract semantics")
        if (specs["escrow_amount_change"]["amount_wei"] == specs["escrow_baseline"]["amount_wei"]
                or {k:v for k,v in specs["escrow_amount_change"].items() if k != "amount_wei"}
                != {k:v for k,v in specs["escrow_baseline"].items() if k != "amount_wei"}):
            raise SmokeFailure("semantic_oracle", "Amount change did not alter only amount_wei")
        result["stages"]["llm_head"] = "PASS_LIVE" if head is None else "PASS_INJECTED_TEST_HEAD"
        result["stages"]["contract_language_validation"] = "PASS"
        result["stages"]["semantic_equivalence_and_contrast"] = "PASS"
        print("[CONTRACT_SMOKE] semantic checks PASS; generating Solidity and Forge tests", flush=True)
        generated = write_foundry_workspace(workspace, specs["escrow_baseline"],
                                            specs["escrow_amount_change"])
        result["generated"] = generated
        result["stages"]["solidity_emit"] = "PASS"
        forge_run = docker or run_command
        for operation in ("build", "test"):
            print(f"[CONTRACT_SMOKE] Docker Forge {operation}", flush=True)
            args = docker_args(workspace, options.foundry_image, options.docker_network, operation)
            check = forge_run(args, options.forge_timeout_s)
            result["stages"][f"forge_{operation}"] = check
            if check["exit_code"] != 0:
                raise SmokeFailure(f"forge_{operation}", f"Docker Forge {operation} returned {check['exit_code']}")
            if operation == "test":
                # Exit zero alone is insufficient: require evidence that our tests ran.
                counts = [int(value) for value in re.findall(
                    r"(\d+)\s+tests?\s+passed", check.get("stdout_tail", ""), flags=re.I)]
                if not counts or max(counts) < 18:
                    raise SmokeFailure("forge_test", "Forge did not confirm at least 18 passing tests; success is not established")
                if re.search(r"\b[1-9]\d*\s+failed\b", check.get("stdout_tail", ""), flags=re.I):
                    raise SmokeFailure("forge_test", "Forge reported at least one failed test")
                result["stages"]["forge_tests_passed"] = max(counts)
        for name in generated:
            artifact = workspace / "out" / f"{name}.sol" / f"{name}.json"
            # In the real Docker run, forge build must emit the compiled artifact.
            if docker is None and not artifact.exists():
                raise SmokeFailure("forge_build", f"Missing real compiled artifact: {artifact.name}")
            if artifact.exists():
                content = json.loads(artifact.read_text(encoding="utf-8"))
                if not isinstance(content.get("bytecode", {}).get("object"), str) or not content["bytecode"]["object"]:
                    raise SmokeFailure("forge_build", f"Compiled artifact has no bytecode: {name}")
                generated[name]["artifact_sha256"] = digest(artifact.read_bytes())
        result["stages"]["compiled_artifact"] = "PASS" if docker is None else "INJECTED_TEST_RUNNER"
        if head is None and docker is None:
            result["status"] = "PASS_SMOKE_NOT_CERTIFIED"
        else:
            result["status"] = "PASS_INJECTED_OFFLINE_TEST_ONLY"
    except SmokeFailure as exc:
        result["blockers"].append({"stage": exc.stage, "reason": exc.reason})
    except Exception as exc:  # preserve a diagnostic report even for unexpected failures
        result["blockers"].append({"stage": "internal", "reason": f"{type(exc).__name__}: {str(exc)[:400]}"})
        result["internal_traceback_tail"] = traceback.format_exc()[-1800:]
    finally:
        result["finished_at"] = now()
        if not options.dry_run and not output.exists():
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def summarize_stages(stages: dict[str, Any]) -> dict[str, Any]:
    """Report subprocess exit codes without assuming every stage is a mapping.

    Stages include descriptive strings, Docker result dicts, and numeric test
    counts. A successful Forge test count must not crash final reporting.
    """
    return {name: (value.get("exit_code", value) if isinstance(value, dict) else value)
            for name, value in stages.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime/reports/nanojev-contract-solidity-smoke-0001.json")
    parser.add_argument("--ollama-url", default=DEFAULT_URL)
    parser.add_argument("--llm-model", default=DEFAULT_MODEL)
    parser.add_argument("--foundry-image", default=DEFAULT_IMAGE)
    parser.add_argument("--docker-network", choices=("bridge", "none"), default="bridge")
    parser.add_argument("--llm-timeout-s", type=float, default=240.0)
    parser.add_argument("--forge-timeout-s", type=float, default=360.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    opts = Options(output=Path(args.output), ollama_url=args.ollama_url,
                   model=args.llm_model, foundry_image=args.foundry_image,
                   docker_network=args.docker_network, llm_timeout_s=args.llm_timeout_s,
                   forge_timeout_s=args.forge_timeout_s, dry_run=args.dry_run)
    report = execute(opts)
    print(json.dumps({"status": report["status"], "blockers": report["blockers"],
                      "stages": summarize_stages(report["stages"]),
                      "report": str(opts.output)}, ensure_ascii=False))
    return 0 if report["status"] in {"PASS_SMOKE_NOT_CERTIFIED", "PLAN_ONLY_NOT_EXECUTED"} else 1


if __name__ == "__main__":
    sys.exit(main())
