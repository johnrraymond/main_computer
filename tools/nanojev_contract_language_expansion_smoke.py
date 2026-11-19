#!/usr/bin/env python3
"""Restricted multi-family Contract Language -> Solidity -> Forge Docker acceptance smoke.

This adds two genuinely different contract structures (timelocked gift and
atomic split payout), while running the original escrow pipeline unmodified as
a regression. Each LLM input is natural language; reference semantics are in a
separate pinned fixture and NEVER passed to Ollama. Emitted Solidity comes from
reviewable deterministic templates, NOT from the LLM. This is a diagnostic, not
an audit, formal proof, independent expert corpus, or production compiler.

    & $py -m tools.nanojev_contract_language_expansion_smoke \
      --llm-model gemma4:26b-a4b-it-q4_K_M \
      --output runtime/reports/nanojev-contract-language-expansion-smoke-0001.json

Requires local Ollama, Docker and a working Forge image; no deployments, keys,
RPC URLs, model updates, or production mutations.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from tools import nanojev_contract_solidity_smoke as v1

ESCROW_RUNNER_SHA256 = "2d968f58146c7977dc85f2b09610d760adc64d6d9ec0d669cbfdf49ce936f55d"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nanojev_contract_language_expansion_v1.json"
FIXTURE_SHA256 = "de9d9e91425efe9aa02cfc65edd87adc5741c3aa79911b2a18d08d33de9db81c"

GIFT_LANG = "nanojev.contract.timelocked_gift.v1"
SPLIT_LANG = "nanojev.contract.split_payout.v1"
GIFT_FIXED: dict[str, Any] = {
    "language": GIFT_LANG,
    "asset": "native_eth",
    "depositor": "deployer",
    "beneficiary": "constructor_beneficiary",
    "funding_authorizer": "depositor",
    "claim_authorizer": "beneficiary",
    "claim_trigger": "elapsed_since_funding",
    "claim_window": "at_or_after_maturity",
    "refund": "none",
    "settlement": "one_time_claim",
}
SPLIT_FIXED: dict[str, Any] = {
    "language": SPLIT_LANG,
    "asset": "native_eth",
    "payer": "deployer",
    "primary": "constructor_primary",
    "secondary": "constructor_secondary",
    "funding_authorizer": "payer",
    "distribution_authorizer": "payer",
    "distribution_trigger": "payer_calls_distribute",
    "refund": "none",
    "settlement": "one_time_atomic_split",
}

PROMPT = """You compile ordinary requests into a TYPED Contract Language.
Respond with exactly one JSON object, no markdown, no extra keys.
If essential details are missing, contradictory or unsupported return
{"status":"needs_clarification","questions":["concrete question", ...]}.
Otherwise return {"status":"contract","contract":{all fields listed below}}.
Do not invent values, silently resolve contradictions, add refund permissions,
change actor authorities, or reinterpret 'not obligated' as 'forbidden'.

Exactly TWO supported kinds follow. An unfamiliar request must be refused.
All integer quantities must be JSON integers, not strings. 1 ETH = 10^18 wei.
1 day = 86400 seconds. Percentages map to 10000 basis points in total.

TIMED GIFT: native ETH locked since funding, deposited exactly once by deployer,
beneficiary provided as distinct payable constructor address, only beneficiary
claims all funds at or after maturity, no funder cancellation/refund, one claim.
All those terms must be EXPLICIT in user intent. Mandatory exact fields:
 language:'nanojev.contract.timelocked_gift.v1'
 asset:'native_eth' depositor:'deployer'
 beneficiary:'constructor_beneficiary'
 funding_authorizer:'depositor' claim_authorizer:'beneficiary'
 claim_trigger:'elapsed_since_funding' claim_window:'at_or_after_maturity'
 refund:'none' settlement:'one_time_claim'
 amount_wei:<positive integer under 2^128> lock_seconds:<positive integer>

ATOMIC SPLIT: native ETH once-funded by deployer, two different non-deployer
payable recipients from constructor, only deployer triggers full atomic payment
to both in specified shares, no refunds or repeat distributions. All these
terms must be EXPLICIT. Mandatory exact fields:
 language:'nanojev.contract.split_payout.v1'
 asset:'native_eth' payer:'deployer'
 primary:'constructor_primary' secondary:'constructor_secondary'
 funding_authorizer:'payer' distribution_authorizer:'payer'
 distribution_trigger:'payer_calls_distribute'
 refund:'none' settlement:'one_time_atomic_split'
 amount_wei:<positive integer under 2^128>
 primary_bps:<integer 1..9999> secondary_bps:<integer 1..9999>
The shares must add to exactly 10000 basis points.
No optional fields; no raw Solidity; do not guess authorizers or time boundaries.
"""


@dataclass(frozen=True)
class Options:
    output: Path
    ollama_url: str = v1.DEFAULT_URL
    model: str = v1.DEFAULT_MODEL
    foundry_image: str = v1.DEFAULT_IMAGE
    docker_network: str = "bridge"
    llm_timeout_s: float = 240.0
    forge_timeout_s: float = 360.0
    dry_run: bool = False
    independent_cases: Path | None = None


def load_fixture() -> dict[str, Any]:
    if v1.digest(Path(v1.__file__).read_bytes()) != ESCROW_RUNNER_SHA256:
        raise v1.SmokeFailure("preflight", "Frozen v1 escrow runner changed; review the regression before proceeding")
    v1.fixture()  # also enforces the v1 oracle fixture fingerprint
    binary = FIXTURE.read_bytes()
    if v1.digest(binary) != FIXTURE_SHA256:
        raise v1.SmokeFailure("preflight", "Expansion holdout fixture fingerprint changed")
    result = json.loads(binary.decode("utf-8"))
    if result.get("schema") != "nanojev.contract-language-expansion.fixtures.v1":
        raise v1.SmokeFailure("preflight", "Unrecognized expansion holdout schema")
    cases = result.get("cases", [])
    if len(cases) != 10 or len({c["id"] for c in cases}) != 10:
        raise v1.SmokeFailure("preflight", "Expansion fixture must contain ten unique cases")
    if [c["id"] for c in cases] != [
        "gift_baseline", "gift_paraphrase", "gift_duration_change", "gift_ambiguous", "gift_contradiction",
        "split_baseline", "split_paraphrase", "split_ratio_change", "split_ambiguous", "split_contradiction"
    ]:
        raise v1.SmokeFailure("preflight", "Expansion fixture case identities changed")
    return result


def load_independent_cases(path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    """Optional separately supplied gold, never included in the LLM prompt.

    Authorship and quality of the external annotations cannot be verified by
    this harness. This is a test input, NOT expert certification.
    """
    if path is None:
        return [], None
    try:
        raw = path.resolve().read_bytes()
    except OSError as exc:
        raise v1.SmokeFailure("preflight", f"Independent corpus cannot be read: {type(exc).__name__}") from exc
    if len(raw) > 128_000:
        raise v1.SmokeFailure("preflight", "Independent corpus exceeds 128KB")
    try:
        obj = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise v1.SmokeFailure("preflight", "Independent gold is not valid UTF-8 JSON") from exc
    if not isinstance(obj, dict) or obj.get("schema") != "nanojev.contract-language-expansion.external.v1":
        raise v1.SmokeFailure("preflight", "Independent corpus schema invalid")
    cases = obj.get("cases")
    if not isinstance(cases, list) or not 2 <= len(cases) <= 16:
        raise v1.SmokeFailure("preflight", "Independent corpus needs 2..16 cases")
    seen: set[str] = set()
    reserved = {x["id"] for x in v1.fixture()["cases"]} | {x["id"] for x in load_fixture()["cases"]}
    positive = negative = 0
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "type", "family", "request", "expected", "missing"}:
            raise v1.SmokeFailure("preflight", "Independent case has unexpected keys")
        key = case.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or key in seen or key in reserved:
            raise v1.SmokeFailure("preflight", "Independent case IDs must be unique, nonreserved ASCII identifiers")
        seen.add(key)
        if case.get("type") not in {"timelocked_gift", "split_payout"}:
            raise v1.SmokeFailure("preflight", "Independent case uses unsupported contract family")
        text = case.get("request")
        if not isinstance(text, str) or not 40 <= len(text) <= 10000:
            raise v1.SmokeFailure("preflight", "Independent case request length invalid")
        if case.get("family") == "positive":
            positive += 1
            expected = case.get("expected")
            names = {"amount_wei", "lock_seconds"} if case["type"] == "timelocked_gift" else {
                "amount_wei", "primary_bps", "secondary_bps"}
            if not isinstance(expected, dict) or set(expected) != names or any(type(v) is not int for v in expected.values()):
                raise v1.SmokeFailure("preflight", "Independent gold expected fields invalid")
            if case["type"] == "split_payout" and expected["primary_bps"] + expected["secondary_bps"] != 10000:
                raise v1.SmokeFailure("preflight", "Independent split gold shares do not total 10000")
            if "missing" in case:
                raise v1.SmokeFailure("preflight", "Independent positive case cannot have missing keys")
        elif case.get("family") == "needs_clarification":
            negative += 1
            missing = case.get("missing")
            if (not isinstance(missing, list) or not missing or
                any(x not in {"amount", "lock_duration", "beneficiary", "refund_conflict",
                              "split_ratio", "recipient_roles", "split_conflict"} for x in missing)):
                raise v1.SmokeFailure("preflight", "Independent clarification keys invalid")
            if "expected" in case:
                raise v1.SmokeFailure("preflight", "Independent clarification has positive gold")
        else:
            raise v1.SmokeFailure("preflight", "Independent case family invalid")
    if positive == 0 or negative == 0:
        raise v1.SmokeFailure("preflight", "Independent corpus must contain both positive and negative cases")
    return cases, v1.digest(raw)


def gift_spec(amount: int, lock: int) -> dict[str, Any]:
    return {**GIFT_FIXED, "amount_wei": amount, "lock_seconds": lock}


def split_spec(amount: int, primary: int) -> dict[str, Any]:
    return {**SPLIT_FIXED, "amount_wei": amount, "primary_bps": primary, "secondary_bps": 10000 - primary}


def expected(case: dict[str, Any]) -> dict[str, Any]:
    gold = case["expected"]
    if case["type"] == "timelocked_gift":
        return gift_spec(gold["amount_wei"], gold["lock_seconds"])
    if case["type"] == "split_payout":
        return {**SPLIT_FIXED, **gold}
    raise v1.SmokeFailure("preflight", f"Unknown test family {case['type']}")


def validate(case: dict[str, Any], output: dict[str, Any]) -> dict[str, Any] | None:
    """Validate with pinned gold inaccessible to the LLM. Strictly fail closed."""
    if case["family"] == "needs_clarification":
        if set(output) != {"status", "questions"} or output.get("status") != "needs_clarification":
            raise v1.SmokeFailure("semantic_oracle", f"LLM accepted ambiguous/contradictory case {case['id']}")
        questions = output.get("questions")
        if (not isinstance(questions, list) or not 1 <= len(questions) <= 12 or
            any(not isinstance(q, str) or not q.strip() or len(q) > 400 for q in questions)):
            raise v1.SmokeFailure("semantic_oracle", "Invalid or missing clarification questions")
        text = " ".join(questions).lower()
        keywords = {
            "amount": ("amount", "eth", "ether", "how much", "sum"),
            "lock_duration": ("long", "duration", "time", "delay", "week", "day", "when", "maturity", "lock"),
            "beneficiary": ("beneficiary", "recipient", "who", "address"),
            "refund_conflict": ("refund", "cancel", "reclaim", "return", "take back", "contradict", "conflict"),
            "split_ratio": ("percent", "ratio", "share", "split", "portion", "allocation", "proportion"),
            "recipient_roles": ("beneficiary", "recipient", "who", "address", "primary", "secondary"),
            "split_conflict": ("percent", "ratio", "share", "split", "contradict", "conflict", "allocation"),
        }
        missing = [key for key in case["missing"] if not any(k in text for k in keywords[key])]
        if missing:
            raise v1.SmokeFailure("semantic_oracle", f"Clarification omitted {missing} in {case['id']}")
        return None
    if set(output) != {"status", "contract"} or output.get("status") != "contract":
        raise v1.SmokeFailure("semantic_oracle", f"LLM refused a supported contract: {case['id']}")
    spec = output["contract"]
    if not isinstance(spec, dict):
        raise v1.SmokeFailure("contract_validation", "Expected a structured contract dictionary")
    constants = GIFT_FIXED if case["type"] == "timelocked_gift" else SPLIT_FIXED
    numerics = {"amount_wei", "lock_seconds"} if case["type"] == "timelocked_gift" else {
        "amount_wei", "primary_bps", "secondary_bps"}
    if set(spec) != set(constants) | numerics:
        raise v1.SmokeFailure("contract_validation", f"Wrong field set in {case['id']}")
    for key, value in constants.items():
        if type(spec[key]) is not type(value) or spec[key] != value:
            raise v1.SmokeFailure("contract_validation", f"Unsupported semantic field {key} in {case['id']}")
    if any(type(spec[key]) is not int for key in numerics):
        raise v1.SmokeFailure("contract_validation", f"Invalid integer value in {case['id']}")
    if not 0 < spec["amount_wei"] < 2**128:
        raise v1.SmokeFailure("contract_validation", f"Unsupported amount in {case['id']}")
    if case["type"] == "timelocked_gift":
        if not 0 < spec["lock_seconds"] <= 365 * 86400:
            raise v1.SmokeFailure("contract_validation", "Invalid gift maturity")
    elif not (1 <= spec["primary_bps"] <= 9999 and 1 <= spec["secondary_bps"] <= 9999 and
              spec["primary_bps"] + spec["secondary_bps"] == 10000):
        raise v1.SmokeFailure("contract_validation", "Split shares must add to 10000 bps")
    oracle = expected(case)
    if spec != oracle:
        differences = {key: {"predicted": spec.get(key), "expected": gold}
                       for key, gold in oracle.items() if spec.get(key) != gold}
        raise v1.SmokeFailure("semantic_oracle", f"Intent-to-spec mismatch {case['id']}: {v1.canonical(differences)}")
    return spec


def call_ollama_extension(*, base_url: str, model: str, text: str, timeout: float) -> str:
    base_url = v1.local_url(base_url)
    if not model.strip() or len(model) > 256:
        raise v1.SmokeFailure("preflight", "Invalid Ollama model identifier")
    body = {
        "model": model, "stream": False, "think": False, "format": "json",
        "options": {"temperature": 0, "seed": 261119, "num_predict": 1200},
        "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}],
    }
    try:
        req = Request(f"{base_url}/api/chat", data=v1.canonical(body).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        with urlopen(req, timeout=timeout) as response:
            reply = response.read(200001)
    except Exception as exc:
        raise v1.SmokeFailure("llm", f"Local Ollama request failed: {type(exc).__name__}: {str(exc)[:300]}") from exc
    if len(reply) > 200000:
        raise v1.SmokeFailure("llm", "Ollama reply too large")
    try:
        output = json.loads(reply.decode("utf-8", errors="strict"))["message"]["content"]
    except (UnicodeError, ValueError, TypeError, KeyError) as exc:
        raise v1.SmokeFailure("llm", "Malformed local Ollama response") from exc
    if not isinstance(output, str) or not output.strip():
        raise v1.SmokeFailure("llm", "Empty Ollama head response")
    return output


def gift_source(spec: dict[str, Any], name: str) -> str:
    if (set(spec) != set(GIFT_FIXED) | {"amount_wei", "lock_seconds"} or
            type(spec["amount_wei"]) is not int or not 0 < spec["amount_wei"] < 2**128 or
            type(spec["lock_seconds"]) is not int or not 0 < spec["lock_seconds"] <= 365*86400 or
            spec != gift_spec(spec["amount_wei"], spec["lock_seconds"]) or
            not re.fullmatch(r"NanoJevGift[A-Za-z0-9_]*", name)):
        raise v1.SmokeFailure("solidity_emit", "Unvalidated gift spec or invalid identifier")
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Smoke-only typed gift compiler output. Not audited for deployment.
contract {name} {{
    enum State {{ Unfunded, Funded, Claimed }}
    address public immutable depositor;
    address payable public immutable beneficiary;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant LOCK_SECONDS = {spec['lock_seconds']};
    uint256 public fundedAt;
    State public state;

    constructor(address payable beneficiary_) {{
        require(beneficiary_ != address(0), "zero beneficiary");
        require(beneficiary_ != msg.sender, "identical parties");
        depositor = msg.sender;
        beneficiary = beneficiary_;
    }}
    function fund() external payable {{
        require(msg.sender == depositor, "depositor only");
        require(state == State.Unfunded, "already funded");
        require(msg.value == AMOUNT_WEI, "wrong deposit");
        fundedAt = block.timestamp;
        state = State.Funded;
    }}
    function claim() external {{
        require(msg.sender == beneficiary, "beneficiary only");
        require(state == State.Funded, "not funded");
        require(block.timestamp >= fundedAt + LOCK_SECONDS, "locked");
        state = State.Claimed;
        (bool ok,) = beneficiary.call{{value: AMOUNT_WEI}}("");
        require(ok, "claim transfer failed");
    }}
}}
'''


def gift_test(spec: dict[str, Any], name: str) -> str:
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface VmGift{name} {{
    function deal(address who, uint256 newBalance) external;
    function warp(uint256 newTimestamp) external;
    function prank(address sender) external;
}}
contract {name}SmokeTest {{
    VmGift{name} internal constant vm = VmGift{name}(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {spec['amount_wei']};
    uint256 internal constant DELAY = {spec['lock_seconds']};
    address payable internal constant RECIPIENT = payable(address(0xBEEF));
    address internal constant STRANGER = address(0xBAD);
    receive() external payable {{}}
    function fresh() internal returns ({name} c) {{
        c = new {name}(RECIPIENT);
        vm.deal(address(this), AMOUNT * 4);
    }}
    function funded() internal returns ({name} c) {{
        c = fresh();
        c.fund{{value: AMOUNT}}();
    }}
    function testBeneficiaryClaimsAtMaturity() public {{
        {name} c = funded();
        uint256 beforeBalance = RECIPIENT.balance;
        vm.warp(c.fundedAt() + DELAY);
        vm.prank(RECIPIENT);
        c.claim();
        require(uint256(c.state()) == 2, "not claimed");
        require(RECIPIENT.balance - beforeBalance == AMOUNT, "wrong payout");
        require(address(c).balance == 0, "remaining gift balance");
    }}
    function testNoEarlyClaim() public {{
        {name} c = funded();
        vm.warp(c.fundedAt() + DELAY - 1);
        vm.prank(RECIPIENT);
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.claim.selector));
        require(!ok, "early claim allowed");
    }}
    function testOnlyBeneficiaryMayClaim() public {{
        {name} c = funded();
        vm.warp(c.fundedAt() + DELAY);
        vm.prank(STRANGER);
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.claim.selector));
        require(!ok, "outsider claim allowed");
    }}
    function testOnlyDepositorCanFund() public {{
        {name} c = fresh();
        vm.deal(STRANGER, AMOUNT);
        vm.prank(STRANGER);
        (bool ok,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "outsider funding allowed");
    }}
    function testExactDepositAndOnlyOnce() public {{
        {name} c = fresh();
        (bool wrong,) = address(c).call{{value: AMOUNT - 1}}(abi.encodeWithSelector(c.fund.selector));
        require(!wrong, "wrong amount accepted");
        c.fund{{value: AMOUNT}}();
        (bool twice,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!twice, "second deposit allowed");
    }}
    function testNoUnfundedClaim() public {{
        {name} c = fresh();
        vm.prank(RECIPIENT);
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.claim.selector));
        require(!ok, "unfunded claim allowed");
    }}
    function testNoDoubleClaim() public {{
        {name} c = funded();
        vm.warp(c.fundedAt() + DELAY);
        vm.prank(RECIPIENT);
        c.claim();
        vm.prank(RECIPIENT);
        (bool twice,) = address(c).call(abi.encodeWithSelector(c.claim.selector));
        require(!twice, "double claim allowed");
    }}
    function testDepositorCannotTakeBackGift() public {{
        {name} c = funded();
        vm.warp(c.fundedAt() + DELAY);
        (bool attempt,) = address(c).call(abi.encodeWithSignature("refund()"));
        require(!attempt, "revocation allowed");
    }}
    function testFuzzWrongDeposit(uint96 seed) public {{
        {name} c = fresh();
        uint256 wrong = uint256(seed) % AMOUNT;
        (bool ok,) = address(c).call{{value: wrong}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "wrong fuzz amount accepted");
    }}
}}
'''


def split_source(spec: dict[str, Any], name: str) -> str:
    if (set(spec) != set(SPLIT_FIXED) | {"amount_wei", "primary_bps", "secondary_bps"} or
            type(spec["amount_wei"]) is not int or not 0 < spec["amount_wei"] < 2**128 or
            type(spec["primary_bps"]) is not int or not 1 <= spec["primary_bps"] <= 9999 or
            type(spec["secondary_bps"]) is not int or not 1 <= spec["secondary_bps"] <= 9999 or
            spec != split_spec(spec["amount_wei"], spec["primary_bps"]) or
            not re.fullmatch(r"NanoJevSplit[A-Za-z0-9_]*", name)):
        raise v1.SmokeFailure("solidity_emit", "Unvalidated split spec or invalid identifier")
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
/// @notice Smoke-only deterministic atomic split. Not audited for deployment.
contract {name} {{
    enum State {{ Unfunded, Funded, Distributed }}
    address public immutable payer;
    address payable public immutable primary;
    address payable public immutable secondary;
    uint256 public constant AMOUNT_WEI = {spec['amount_wei']};
    uint256 public constant PRIMARY_BPS = {spec['primary_bps']};
    uint256 public constant SECONDARY_BPS = {spec['secondary_bps']};
    State public state;

    constructor(address payable primary_, address payable secondary_) {{
        require(primary_ != address(0) && secondary_ != address(0), "zero recipient");
        require(primary_ != secondary_, "same recipients");
        require(primary_ != msg.sender && secondary_ != msg.sender, "payer is recipient");
        payer = msg.sender;
        primary = primary_;
        secondary = secondary_;
    }}
    function fund() external payable {{
        require(msg.sender == payer, "payer only");
        require(state == State.Unfunded, "already funded");
        require(msg.value == AMOUNT_WEI, "wrong deposit");
        state = State.Funded;
    }}
    function distribute() external {{
        require(msg.sender == payer, "payer only");
        require(state == State.Funded, "not funded");
        uint256 primaryAmount = (AMOUNT_WEI * PRIMARY_BPS) / 10000;
        uint256 secondaryAmount = AMOUNT_WEI - primaryAmount;
        state = State.Distributed; // effects before external interactions
        (bool ok1,) = primary.call{{value: primaryAmount}}("");
        require(ok1, "primary transfer failed");
        (bool ok2,) = secondary.call{{value: secondaryAmount}}("");
        require(ok2, "secondary transfer failed");
    }}
}}
'''


def split_test(spec: dict[str, Any], name: str) -> str:
    return f'''// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;
import "../src/{name}.sol";
interface VmSplit{name} {{
    function deal(address who, uint256 newBalance) external;
    function prank(address sender) external;
}}
contract RejectETH{name} {{
    receive() external payable {{ revert("reject ether"); }}
}}
contract {name}SmokeTest {{
    VmSplit{name} internal constant vm = VmSplit{name}(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 internal constant AMOUNT = {spec['amount_wei']};
    uint256 internal constant PRIMARY_BPS = {spec['primary_bps']};
    address payable internal constant PRIMARY = payable(address(0xBEEF));
    address payable internal constant SECONDARY = payable(address(0xCAFE));
    address internal constant STRANGER = address(0xBAD);
    receive() external payable {{}}
    function fresh() internal returns ({name} c) {{
        c = new {name}(PRIMARY, SECONDARY);
        vm.deal(address(this), AMOUNT * 4);
    }}
    function funded() internal returns ({name} c) {{
        c = fresh();
        c.fund{{value: AMOUNT}}();
    }}
    function testAtomicSplitAmountsAndZeroRemainder() public {{
        {name} c = funded();
        uint256 before1 = PRIMARY.balance;
        uint256 before2 = SECONDARY.balance;
        c.distribute();
        uint256 expectedPrimary = AMOUNT * PRIMARY_BPS / 10000;
        require(PRIMARY.balance - before1 == expectedPrimary, "primary amount wrong");
        require(SECONDARY.balance - before2 == AMOUNT - expectedPrimary, "secondary amount wrong");
        require(uint256(c.state()) == 2, "not distributed");
        require(address(c).balance == 0, "remaining funds");
    }}
    function testOnlyPayerMayFund() public {{
        {name} c = fresh();
        vm.deal(STRANGER, AMOUNT);
        vm.prank(STRANGER);
        (bool ok,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "outsider funding allowed");
    }}
    function testOnlyPayerMayDistribute() public {{
        {name} c = funded();
        vm.prank(STRANGER);
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.distribute.selector));
        require(!ok, "outsider distribution allowed");
    }}
    function testCannotDistributeBeforeFunding() public {{
        {name} c = fresh();
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.distribute.selector));
        require(!ok, "unfunded distribution allowed");
    }}
    function testExactOnceDeposit() public {{
        {name} c = fresh();
        (bool wrong,) = address(c).call{{value: AMOUNT - 1}}(abi.encodeWithSelector(c.fund.selector));
        require(!wrong, "wrong deposit accepted");
        c.fund{{value: AMOUNT}}();
        (bool twice,) = address(c).call{{value: AMOUNT}}(abi.encodeWithSelector(c.fund.selector));
        require(!twice, "repeated funding accepted");
    }}
    function testNoDoubleDistribution() public {{
        {name} c = funded();
        c.distribute();
        (bool twice,) = address(c).call(abi.encodeWithSelector(c.distribute.selector));
        require(!twice, "repeated distribution accepted");
    }}
    function testNoRefundFunction() public {{
        {name} c = funded();
        (bool ok,) = address(c).call(abi.encodeWithSignature("refund()"));
        require(!ok, "unsupported refund succeeded");
    }}
    function testRejectingRecipientRollsBackEntireDistribution() public {{
        RejectETH{name} refusing = new RejectETH{name}();
        {name} c = new {name}(payable(address(refusing)), SECONDARY);
        vm.deal(address(this), AMOUNT * 4);
        c.fund{{value: AMOUNT}}();
        uint256 beforeSecondary = SECONDARY.balance;
        (bool ok,) = address(c).call(abi.encodeWithSelector(c.distribute.selector));
        require(!ok, "partial transfer succeeded");
        require(uint256(c.state()) == 1, "state not rolled back");
        require(address(c).balance == AMOUNT, "funds not rolled back");
        require(SECONDARY.balance == beforeSecondary, "partial secondary payout");
    }}
    function testConstructorRejectsIdenticalRecipients() public {{
        try new {name}(PRIMARY, PRIMARY) returns ({name} instance) {{
            require(address(instance) != address(0), "unexpected zero contract");
            revert("same recipients accepted");
        }} catch {{}}
    }}
    function testFuzzWrongDeposit(uint96 seed) public {{
        {name} c = fresh();
        uint256 wrong = uint256(seed) % AMOUNT;
        (bool ok,) = address(c).call{{value: wrong}}(abi.encodeWithSelector(c.fund.selector));
        require(!ok, "wrong fuzz amount accepted");
    }}
}}
'''


def add_family_workspace(workspace: Path, specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for ident, kind, name, src_fn, tst_fn in [
        ("gift_baseline", "timelocked_gift", "NanoJevGiftBaseline", gift_source, gift_test),
        ("gift_duration_change", "timelocked_gift", "NanoJevGiftDurationVariant", gift_source, gift_test),
        ("split_baseline", "split_payout", "NanoJevSplitBaseline", split_source, split_test),
        ("split_ratio_change", "split_payout", "NanoJevSplitRatioVariant", split_source, split_test),
    ]:
        spec = specs[ident]
        solidity, test = src_fn(spec, name), tst_fn(spec, name)
        (workspace / "src" / f"{name}.sol").write_text(solidity, encoding="utf-8")
        (workspace / "test" / f"{name}.t.sol").write_text(test, encoding="utf-8")
        rendered[name] = {"kind": kind, "spec_sha256": v1.digest(v1.canonical(spec)),
                          "source_sha256": v1.digest(solidity), "test_sha256": v1.digest(test)}
    return rendered


def add_independent_workspace(workspace: Path, cases: list[dict[str, Any]],
                              specs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for i, case in enumerate(cases):
        if case["family"] != "positive":
            continue
        name = (f"NanoJevGiftExternal{i:03d}" if case["type"] == "timelocked_gift"
                else f"NanoJevSplitExternal{i:03d}")
        spec = specs[case["id"]]
        src_fn, tst_fn = ((gift_source, gift_test) if case["type"] == "timelocked_gift"
                          else (split_source, split_test))
        solidity = src_fn(spec, name)
        tests = tst_fn(spec, name)
        (workspace / "src" / f"{name}.sol").write_text(solidity, encoding="utf-8")
        (workspace / "test" / f"{name}.t.sol").write_text(tests, encoding="utf-8")
        extra[name] = {"kind": case["type"], "origin": "supplied_external_gold_unverified",
                       "spec_sha256": v1.digest(v1.canonical(spec)),
                       "source_sha256": v1.digest(solidity), "test_sha256": v1.digest(tests)}
    return extra


def check_relations(specs: dict[str, dict[str, Any]]) -> None:
    checks = [
        (specs["gift_baseline"] == specs["gift_paraphrase"], "Gift paraphrase changed meaning"),
        (specs["split_baseline"] == specs["split_paraphrase"], "Split paraphrase changed meaning"),
        ({k: v for k, v in specs["gift_baseline"].items() if k != "lock_seconds"} ==
         {k: v for k, v in specs["gift_duration_change"].items() if k != "lock_seconds"} and
         specs["gift_baseline"]["lock_seconds"] != specs["gift_duration_change"]["lock_seconds"],
         "Gift duration contrast changed other fields"),
        ({k: v for k, v in specs["split_baseline"].items() if k not in {"primary_bps", "secondary_bps"}} ==
         {k: v for k, v in specs["split_ratio_change"].items() if k not in {"primary_bps", "secondary_bps"}} and
         specs["split_baseline"]["primary_bps"] != specs["split_ratio_change"]["primary_bps"],
         "Split ratio contrast changed other fields"),
    ]
    for passed, reason in checks:
        if not passed:
            raise v1.SmokeFailure("semantic_oracle", reason)


def run(options: Options, *, head: Callable[[str, str], str] | None = None,
        docker: Callable[[list[str], float], dict[str, Any]] | None = None,
        docker_available: bool | None = None) -> dict[str, Any]:
    out = options.output.resolve()
    workspace = out.with_name(out.stem + ".workspace")
    report: dict[str, Any] = {
        "schema": "nanojev.contract-language-expansion-smoke.v1", "status": "BLOCKED_NOT_VERIFIED",
        "started_at": v1.now(), "mode": "READ_ONLY_NO_DEPLOYMENT_NO_KEYS_NO_MODEL_MUTATION",
        "head": "Ollama LLM, not native NanoJev CLEF",
        "assurance_limit": "Two additional restricted contract families; developer-authored test intent, not independent expert gold; compiled templates not audited",
        "model": options.model, "forge_image": options.foundry_image,
        "independent_gold": "NOT_PROVIDED",
        "fixture_sha256": v1.digest(FIXTURE.read_bytes()), "v1_runner_sha256": v1.digest(Path(v1.__file__).read_bytes()),
        "runner_sha256": v1.digest(Path(__file__).read_bytes()),
        "cases": [], "stages": {}, "blockers": [], "workspace": str(workspace), "output": str(out),
    }
    try:
        if out.exists() or workspace.exists():
            raise v1.SmokeFailure("preflight", "Output/workspace already exists; choose a new report filename")
        v1.local_url(options.ollama_url)
        data = load_fixture()
        external_cases, external_hash = load_independent_cases(options.independent_cases)
        if external_hash is not None:
            report["independent_gold"] = "SUPPLIED_AUTHORSHIP_AND_ACCURACY_UNVERIFIED"
            report["independent_gold_sha256"] = external_hash
            report["independent_gold_cases"] = len(external_cases)
        baseline = v1.fixture()["cases"]
        if options.dry_run:
            report["status"] = "PLAN_ONLY_NOT_EXECUTED"
            report["stages"]["preflight"] = "PLAN_ONLY"
            report["planned_cases"] = [c["id"] for c in baseline + data["cases"] + external_cases]
            return report
        if options.llm_timeout_s <= 0 or options.forge_timeout_s <= 0:
            raise v1.SmokeFailure("preflight", "Timeout must be positive")
        if docker_available is None:
            docker_available = shutil.which("docker") is not None
        if not docker_available:
            raise v1.SmokeFailure("preflight", "Docker CLI missing; Forge compilation not attempted")
        out.parent.mkdir(parents=True, exist_ok=True)
        workspace.mkdir()
        report["stages"]["preflight"] = "PASS"
        def actual_head(mode: str, text: str) -> str:
            if head is not None:
                return head(mode, text)
            if mode == "escrow":
                return v1.call_ollama(base_url=options.ollama_url, model=options.model,
                                      text=text, timeout=options.llm_timeout_s)
            return call_ollama_extension(base_url=options.ollama_url, model=options.model,
                                         text=text, timeout=options.llm_timeout_s)
        escrows: dict[str, dict[str, Any]] = {}
        expanded: dict[str, dict[str, Any]] = {}
        external_specs: dict[str, dict[str, Any]] = {}
        all_cases = [("escrow", c, "frozen_escrow") for c in baseline] + [
            (c["type"], c, "authored_expansion") for c in data["cases"]] + [
            (c["type"], c, "supplied_external") for c in external_cases]
        for mode, case, origin in all_cases:
            print(f"[CONTRACT_EXPANSION] LLM {mode}: {case['id']}", flush=True)
            item = {"id": case["id"], "kind": mode, "family": case["family"],
                    "origin": origin,
                    "request_sha256": v1.digest(case["request"])}
            report["cases"].append(item)
            raw = actual_head(mode, case["request"])
            item["llm_output_sha256"] = v1.digest(raw)
            item["llm_output"] = raw[:4000]
            parsed = v1.parse_head_output(raw)
            spec = v1.validate_response(case, parsed) if mode == "escrow" else validate(case, parsed)
            if spec is None:
                item["result"] = "CORRECT_CLARIFICATION"
            else:
                item["result"] = "EXACT_INTENT_MATCH"
                item["spec_sha256"] = v1.digest(v1.canonical(spec))
                (escrows if mode == "escrow" else external_specs if origin == "supplied_external"
                 else expanded)[case["id"]] = spec
        if escrows["escrow_baseline"] != escrows["escrow_paraphrase"]:
            raise v1.SmokeFailure("semantic_oracle", "v1 escrow paraphrase regression")
        if {k: v for k, v in escrows["escrow_baseline"].items() if k != "amount_wei"} != {
            k: v for k, v in escrows["escrow_amount_change"].items() if k != "amount_wei"}:
            raise v1.SmokeFailure("semantic_oracle", "v1 escrow amount contrast regression")
        check_relations(expanded)
        report["stages"]["head"] = "PASS_LIVE" if head is None else "PASS_INJECTED_TEST_HEAD"
        report["stages"]["contract_language_validation"] = "PASS"
        report["stages"]["semantic_paraphrase_and_contrast"] = "PASS"
        print("[CONTRACT_EXPANSION] semantic checks PASS; emitting six Solidity contracts", flush=True)
        generated = v1.write_foundry_workspace(workspace, escrows["escrow_baseline"],
                                               escrows["escrow_amount_change"])
        rendered = add_family_workspace(workspace, expanded)
        generated.update(rendered)
        generated.update(add_independent_workspace(workspace, external_cases, external_specs))
        report["generated"] = generated
        report["stages"]["solidity_emit"] = "PASS"
        test_sources = list((workspace / "test").glob("*.t.sol"))
        required_count = 6 + len(external_specs)
        if len(generated) != required_count or len(test_sources) != required_count:
            raise v1.SmokeFailure("solidity_emit", f"Expected {required_count} Solidity source and test files")
        test_names = [name for source in test_sources
                      for name in re.findall(r"\bfunction\s+(test\w*)\s*\(",
                                             source.read_text(encoding="utf-8"))]
        if any(name.startswith("testFail") for name in test_names):
            raise v1.SmokeFailure("solidity_emit", "Forge removed legacy testFail* tests; rename generated tests before compilation")
        expected_tests = len(test_names)
        report["expected_foundry_tests"] = expected_tests
        if expected_tests < 50:
            raise v1.SmokeFailure("solidity_emit", "Unexpected test count below 50")
        invoke = docker or v1.run_command
        for operation in ("build", "test"):
            print(f"[CONTRACT_EXPANSION] Docker Forge {operation}", flush=True)
            args = v1.docker_args(workspace, options.foundry_image, options.docker_network, operation)
            outcome = invoke(args, options.forge_timeout_s)
            report["stages"]["forge_" + operation] = outcome
            if type(outcome) is not dict or type(outcome.get("exit_code")) is not int:
                raise v1.SmokeFailure("forge_" + operation, "Malformed subprocess result")
            if outcome["exit_code"] != 0:
                raise v1.SmokeFailure("forge_" + operation, f"Forge {operation} exited {outcome['exit_code']}")
            if operation == "test":
                stdout = outcome.get("stdout_tail", "")
                counts = [int(x) for x in re.findall(r"(\d+)\s+tests?\s+passed", stdout, flags=re.I)]
                if not counts or max(counts) < expected_tests or re.search(r"\b[1-9]\d*\s+failed\b", stdout, flags=re.I):
                    raise v1.SmokeFailure("forge_test", f"Forge did not verify {expected_tests} passing tests, zero failures")
                report["stages"]["forge_tests_passed"] = max(counts)
        for name, record in generated.items():
            artifact = workspace / "out" / f"{name}.sol" / f"{name}.json"
            if docker is None and not artifact.exists():
                raise v1.SmokeFailure("forge_build", f"Compiled artifact missing: {name}")
            if artifact.exists():
                obj = json.loads(artifact.read_text(encoding="utf-8"))
                code = obj.get("bytecode", {}).get("object")
                if not isinstance(code, str) or not code:
                    raise v1.SmokeFailure("forge_build", f"Compiled bytecode missing: {name}")
                expected_methods = ({"fund", "confirmDelivery", "refund"} if name.startswith("NanoJevEscrow")
                                    else {"fund", "claim"} if name.startswith("NanoJevGift")
                                    else {"fund", "distribute"})
                abi_methods = {item["name"] for item in obj.get("abi", []) if item.get("type") == "function"}
                if not expected_methods <= abi_methods:
                    raise v1.SmokeFailure("forge_build", f"Unexpected ABI methods in {name}")
                record["artifact_sha256"] = v1.digest(artifact.read_bytes())
        report["stages"]["compiled_artifacts"] = "PASS" if docker is None else "INJECTED_TEST_RUNNER"
        report["status"] = "PASS_SMOKE_NOT_CERTIFIED" if head is None and docker is None else "PASS_INJECTED_OFFLINE_TEST_ONLY"
    except v1.SmokeFailure as exc:
        report["blockers"].append({"stage": exc.stage, "reason": exc.reason})
    except Exception as exc:
        report["blockers"].append({"stage": "internal", "reason": f"{type(exc).__name__}: {str(exc)[:400]}"})
        report["internal_traceback_tail"] = traceback.format_exc()[-2000:]
    finally:
        report["finished_at"] = v1.now()
        if not options.dry_run and not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime/reports/nanojev-contract-language-expansion-smoke-0001.json")
    parser.add_argument("--ollama-url", default=v1.DEFAULT_URL)
    parser.add_argument("--llm-model", default=v1.DEFAULT_MODEL)
    parser.add_argument("--foundry-image", default=v1.DEFAULT_IMAGE)
    parser.add_argument("--docker-network", default="bridge", choices=("bridge", "none"))
    parser.add_argument("--llm-timeout-s", type=float, default=240.0)
    parser.add_argument("--forge-timeout-s", type=float, default=360.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--independent-cases", type=Path, default=None,
                        help="Optional separately authored gold JSON, never sent to the LLM; authorship unverified")
    args = parser.parse_args(argv)
    report = run(Options(output=Path(args.output), ollama_url=args.ollama_url,
                         model=args.llm_model, foundry_image=args.foundry_image,
                         docker_network=args.docker_network,
                         llm_timeout_s=args.llm_timeout_s, forge_timeout_s=args.forge_timeout_s,
                         dry_run=args.dry_run, independent_cases=args.independent_cases))
    print(json.dumps({"status": report["status"], "blockers": report["blockers"],
                      "stages": v1.summarize_stages(report["stages"]),
                      "expected_foundry_tests": report.get("expected_foundry_tests"),
                      "report": str(args.output)}, ensure_ascii=False))
    return 0 if report["status"] in {"PASS_SMOKE_NOT_CERTIFIED", "PLAN_ONLY_NOT_EXECUTED"} else 1


if __name__ == "__main__":
    sys.exit(main())
