"""Independent, fail-closed *EVM execution* gate for NanoJev V3 governance.

This module MUST NOT import V3's language, reference, verifier, or generator.
The gold file must be authored separately from the compiler and must state its
own transactions, denied actions, money movements, and terminal entitlements.

No remote RPC, private keys, contract deployment outside disposable local Anvil,
or production deployment is supported. Requires local Foundry (forge/cast/anvil).
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import random
from pathlib import Path, PureWindowsPath
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable

REPORT_SCHEMA = "nanojev.contract.v3.governance_heldout_report.v1"
GOLD_SCHEMA = "nanojev.contract.v3.independent_behavior_gold.v1"
CERT_SCHEMA = "nanojev.contract.v3.independent_behavior_certificate.v1"
EXPECTED_ACCEPTED = 6
EXPECTED_REJECTED = 9
MAX_PERMUTATIONS = 24
MAX_STEPS = 300


class CertificationFailure(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CertificationFailure(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_json(data: Any) -> str:
    return digest(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())


def read_json(path: Path) -> dict[str, Any]:
    content = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(content, dict), f"expected JSON object: {path}")
    return content


def atomic_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def parse_wei(value: Any, context: str) -> int:
    require(not isinstance(value, bool) and isinstance(value, (str, int)), f"{context}: wei must be decimal integer")
    s = str(value)
    require(s.lstrip("-").isdigit(), f"{context}: expected decimal wei integer, got {value!r}")
    return int(s, 10)


def hex_quantity(value: int) -> str:
    require(value >= 0, "negative Ethereum RPC quantity")
    return hex(value)


def validate_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    require(report.get("schema") == REPORT_SCHEMA, "wrong held-out report schema")
    require(report.get("status") == "PASS_FORGE_V3_GOVERNANCE_HELDOUT_NOT_CERTIFIED", "held-out gate did not pass")
    require(report.get("forge") == "PASS_LOCAL_DOCKER_FORGE", "held-out Docker Forge not verified")
    require(report.get("kernel_frozen") is True, "compiler kernel not frozen")
    require(report.get("blockers") == [], "held-out report contains blockers")
    require(report.get("gold_independence") == "NOT_INDEPENDENT_EXPERT_GOLD", "report must be pre-independent")
    require(report.get("accepted_contracts") == EXPECTED_ACCEPTED, "unexpected valid corpus size")
    require(report.get("rejected_counterexamples") == EXPECTED_REJECTED, "unexpected negative corpus size")
    require(report.get("passed_forge_tests") == report.get("total_forge_tests") == 203, "held-out Forge coverage changed")
    require(isinstance(report.get("kernel_sha256"), dict) and report["kernel_sha256"], "missing frozen kernel digests")
    require(isinstance(report.get("corpus_manifest_sha256"), str), "missing corpus manifest hash")
    accepted = [c for c in report.get("cases", []) if c.get("expect") == "accept"]
    rejected = [c for c in report.get("cases", []) if c.get("expect") == "reject"]
    require(len(accepted) == EXPECTED_ACCEPTED and len(rejected) == EXPECTED_REJECTED, "case population mismatch")
    require(len({c.get("id") for c in report["cases"]}) == 15, "duplicate case IDs")
    require(all(c.get("result") == "PASS_FORGE" and c.get("passed_forge_tests") == c.get("expected_forge_tests") for c in accepted), "accepted case not green")
    require(all(c.get("result") == "PASS_REFUSED" for c in rejected), "rejected case not refused")
    return accepted


def verify_kernel_on_disk(report: dict[str, Any], repo_root: Path) -> None:
    """Pin real V3 source bytes; report or gold metadata alone is insufficient."""
    for basename, expected in report["kernel_sha256"].items():
        require(Path(basename).name == basename and basename.endswith(".py"), "unsafe kernel filename")
        require(isinstance(expected, str) and len(expected) == 64, "invalid frozen kernel hash")
        candidate = repo_root / "tools" / basename
        require(candidate.is_file(), f"frozen V3 kernel missing: {candidate}")
        actual = digest(candidate.read_bytes())
        require(actual == expected, f"frozen V3 kernel changed: {basename}")


def validate_gold(report: dict[str, Any], gold: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    accepted = validate_report(report)
    require(gold.get("schema") == GOLD_SCHEMA, "wrong independent-gold schema")
    require(gold.get("corpus_manifest_sha256") == report["corpus_manifest_sha256"], "gold targets a different held-out corpus")
    require(gold.get("kernel_sha256") == report["kernel_sha256"], "gold targets a different kernel")
    authorship = gold.get("authorship")
    require(isinstance(authorship, dict), "missing independent authorship attestation")
    require(authorship.get("reference_model_used") is False, "gold must not use V3 reference model")
    require(isinstance(authorship.get("reviewer"), str) and bool(authorship["reviewer"].strip()), "reviewer not identified")
    require(isinstance(authorship.get("method"), str) and len(authorship["method"].strip()) >= 20, "independent method not described")
    cases = gold.get("cases")
    require(isinstance(cases, list) and len(cases) == len(accepted), "independent gold must cover all accepted cases")
    by_id = {c.get("id"): c for c in cases if isinstance(c, dict)}
    require(len(by_id) == len(cases), "duplicate independent-gold case IDs")
    require(set(by_id) == {c["id"] for c in accepted}, "gold corpus case ID mismatch")
    result = []
    total_permutations = 0
    for expected in accepted:
        case = by_id[expected["id"]]
        require(case.get("spec_sha256") == expected["spec_sha256"], f"{expected['id']}: specification hash mismatch")
        require(case.get("solidity_sha256") == expected["solidity_sha256"], f"{expected['id']}: Solidity hash mismatch")
        require(isinstance(case.get("actors"), dict) and "payer" in case["actors"] and "worker" in case["actors"] and "outsider" in case["actors"], f"{expected['id']}: need payer, worker, outsider actor positions")
        require(len(set(case["actors"].values())) == len(case["actors"]), f"{expected['id']}: duplicate actor positions")
        require(all(type(v) is int and 0 <= v < 10 for v in case["actors"].values()), f"{expected['id']}: actor indices must be integers 0..9")
        constructor = case.get("constructor", {})
        require(isinstance(constructor, dict) and isinstance(constructor.get("args", []), list), f"{expected['id']}: malformed constructor")
        sequences = case.get("sequences")
        require(isinstance(sequences, list) and len(sequences) >= 2, f"{expected['id']}: at least two independent traces required")
        require(len({s.get("id") for s in sequences if isinstance(s, dict)}) == len(sequences), f"{expected['id']}: duplicate sequence ID")
        saw_terminal = False
        saw_unauthorized = False
        saw_timing = False
        saw_boundary_flip = False
        saw_money = False
        for seq in sequences:
            require(isinstance(seq, dict) and isinstance(seq.get("steps"), list), f"{expected['id']}: invalid sequence")
            require(0 < len(seq["steps"]) <= MAX_STEPS, "empty or excessive trace")
            terminal = seq.get("terminal") is True
            if terminal:
                saw_terminal = True
                final = seq.get("final", {})
                require(isinstance(final, dict) and parse_wei(final.get("contract_balance_wei"), "terminal balance") == 0, f"{expected['id']}: terminal trace must assert zero trapped wei")
                require(isinstance(final.get("balances_delta_wei"), dict) and bool(final["balances_delta_wei"]), "terminal trace must assert beneficiary deltas")
            flattened = list(_flatten_steps(seq["steps"]))
            # An actual boundary oracle must exercise the *same* action on
            # both sides of a clock change with opposite expected outcomes.
            for index, timed in enumerate(flattened):
                if isinstance(timed, dict) and timed.get("type") == "time":
                    earlier = {(_action_key(st)): st["expect"] for st in flattened[:index]
                               if isinstance(st, dict) and st.get("type") == "send"}
                    later = {(_action_key(st)): st["expect"] for st in flattened[index + 1:]
                             if isinstance(st, dict) and st.get("type") == "send"}
                    if any(k in later and later[k] != outcome for k, outcome in earlier.items()):
                        saw_boundary_flip = True
            for st in flattened:
                require(isinstance(st, dict) and st.get("type") in {"send", "time", "assert", "view"}, "invalid independent trace step")
                if st["type"] == "time":
                    saw_timing = True
                    require(type(st.get("advance_seconds")) is int and st["advance_seconds"] > 0, "time steps must advance positively")
                if st["type"] == "send":
                    require(st.get("actor") in case["actors"], "unknown actor")
                    require(st.get("expect") in {"success", "revert"}, "send needs explicit success/revert oracle")
                    require(isinstance(st.get("signature"), str) and "(" in st["signature"], "send needs ABI function signature")
                    require(isinstance(st.get("args", []), list), "send args must be list")
                    require(parse_wei(st.get("value_wei", "0"), "send value") >= 0, "negative sent wei")
                    if st["actor"] == "outsider" and st["expect"] == "revert":
                        saw_unauthorized = True
                    if st.get("changes_wei") is not None:
                        require(isinstance(st["changes_wei"], dict), "changes_wei must be object")
                        saw_money = True
                if st["type"] == "assert":
                    require("balances_delta_wei" in st or "contract_balance_wei" in st, "assert needs economic oracle")
                    saw_money = True
                if st["type"] == "view":
                    require(st.get("actor") in case["actors"], "view actor unknown")
                    require(st.get("expect_uint") is not None or st.get("expect_bool") is not None or st.get("expect_hex") is not None, "view requires explicit expected return value")
            for step in seq["steps"]:
                if step.get("type") == "permutation":
                    total_permutations += 1
        require(saw_terminal and saw_unauthorized and saw_timing and saw_money and saw_boundary_flip, f"{expected['id']}: missing terminal/outsider/deadline-boundary/economic coverage")
        result.append((expected, case))
    require(total_permutations >= 1, "corpus must include at least one transaction-order permutation")
    return result


def _action_key(step: dict[str, Any]) -> tuple[str, str, str, str]:
    return (step.get("actor", ""), step.get("signature", ""),
            json.dumps(step.get("args", []), sort_keys=True), str(step.get("value_wei", "0")))


def _flatten_steps(steps: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for st in steps:
        if isinstance(st, dict) and st.get("type") == "permutation":
            inner = st.get("steps")
            require(isinstance(inner, list) and 2 <= len(inner) <= 5, "permutation group size must be 2..5")
            require(all(isinstance(i, dict) and i.get("type") == "send" for i in inner), "only transactions can be permuted")
            yield from inner
        else:
            yield st


def expanded_sequences(steps: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[list[dict[str, Any]]]] = []
    for step in steps:
        if step.get("type") == "permutation":
            choices = list(itertools.permutations(step["steps"]))
            if len(choices) > MAX_PERMUTATIONS:
                require(type(step.get("seed")) is int and step["seed"] >= 0,
                        "large permutation group needs deterministic nonnegative seed")
                choices = random.Random(step["seed"]).sample(choices, MAX_PERMUTATIONS)
            groups.append([list(choice) for choice in choices])
        else:
            groups.append([[step]])
    count = 1
    for group in groups:
        count *= len(group)
        require(count <= MAX_PERMUTATIONS, "cross-product of transaction permutations exceeds cap")
    return [[st for component in combination for st in component] for combination in itertools.product(*groups)]


class RPC:
    def __init__(self, url: str):
        require(url.startswith("http://127.0.0.1:"), "only dedicated local Anvil RPC allowed")
        self.url = url
        self.sequence = 0

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        self.sequence += 1
        payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or [], "id": self.sequence}).encode()
        request = urllib.request.Request(self.url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        if "error" in data:
            raise CertificationFailure(f"RPC {method}: {data['error']}")
        return data["result"]

    def wait_receipt(self, tx_hash: str) -> dict[str, Any]:
        for _ in range(60):
            receipt = self.call("eth_getTransactionReceipt", [tx_hash])
            if receipt is not None:
                return receipt
            time.sleep(0.05)
        raise CertificationFailure(f"transaction has no receipt: {tx_hash}")


class LocalAnvil:
    def __init__(self, executable: str = "anvil"):
        self.executable = executable
        self.proc: subprocess.Popen[bytes] | None = None
        self.rpc: RPC | None = None

    def __enter__(self) -> RPC:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.rpc = RPC(f"http://127.0.0.1:{port}")
        command = [self.executable, "--host", "127.0.0.1", "--port", str(port), "--gas-price", "0", "--base-fee", "0", "--silent"]
        self.proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                require(self.proc.poll() is None, "Anvil exited during startup")
                try:
                    chain_id = self.rpc.call("eth_chainId")
                    require(chain_id == "0x7a69", "unexpected local Anvil chain ID")
                    return self.rpc
                except (OSError, urllib.error.URLError, TimeoutError):
                    time.sleep(0.05)
            raise CertificationFailure("local Anvil failed to start")
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_: object) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.communicate()


def cast_encode(signature: str, args: list[Any], *, constructor: bool = False, cast: str = "cast") -> str:
    command = [cast, "abi-encode" if constructor else "calldata", signature]
    command.extend(str(x) if not isinstance(x, (list, dict)) else json.dumps(x, separators=(",", ":")) for x in args)
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    require(completed.returncode == 0, f"cast encoding failed: {completed.stderr.strip()}")
    data = completed.stdout.strip()
    require(data.startswith("0x") and all(x in "0123456789abcdefABCDEF" for x in data[2:]), "cast returned invalid ABI calldata")
    return data


def canonical_solidity_sha256(source: Path, expected_hash: str) -> str:
    """Accept exact source or Windows CRLF-only serialization of frozen LF text.

    The frozen compiler hashes the original LF text; Windows archive copies may
    serialize it with CRLF. Only that exact reversible newline translation is
    permitted, not arbitrary whitespace normalization.
    """
    raw = source.read_bytes()
    actual = digest(raw)
    if actual == expected_hash:
        return actual
    if b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n") and raw.count(b"\r") == raw.count(b"\r\n"):
        normalized = digest(raw.replace(b"\r\n", b"\n"))
        require(normalized == expected_hash,
                f"Solidity source hash mismatch (raw={actual}, LF-normalized={normalized}): {source}")
        return normalized
    raise CertificationFailure(f"Solidity source hash mismatch ({actual}): {source}")


def source_and_artifact(workspace: Path, expected_hash: str, forge: str) -> tuple[dict[str, Any], Path]:
    require(workspace.is_dir(), f"missing generated workspace: {workspace}")
    source = workspace / "src" / "NanoJevGoverned01.sol"
    require(source.is_file(), f"missing governed Solidity: {source}")
    canonical_solidity_sha256(source, expected_hash)
    artifact = workspace / "out" / "NanoJevGoverned01.sol" / "NanoJevGoverned01.json"
    # Each workspace is already compiler output. Forge build recompiles locally, never modifies the V3 kernel.
    completed = subprocess.run([forge, "build", "--root", str(workspace)], capture_output=True, text=True, timeout=180, check=False)
    require(completed.returncode == 0, f"Forge build failure {workspace}: {completed.stderr[-2000:]}")
    require(artifact.is_file(), f"Forge artifact missing: {artifact}")
    content = read_json(artifact)
    require(isinstance(content.get("abi"), list), "Forge artifact missing ABI")
    bytecode = content.get("bytecode", {}).get("object", "")
    require(isinstance(bytecode, str) and bytecode.startswith("0x") and len(bytecode) > 2, "empty creation bytecode")
    require("__" not in bytecode, "unlinked Solidity bytecode")
    return content, source


def signature_set(artifact: dict[str, Any]) -> set[str]:
    return {a["name"] + "(" + ",".join(arg["type"] for arg in a["inputs"]) + ")" for a in artifact["abi"] if a.get("type") == "function"}


def constructor_signature(artifact: dict[str, Any]) -> str:
    entry = next((a for a in artifact["abi"] if a.get("type") == "constructor"), None)
    return "constructor(" + ",".join(arg["type"] for arg in (entry or {}).get("inputs", [])) + ")"


def balances(rpc: RPC, actors: dict[str, str], contract: str) -> dict[str, int]:
    result = {alias: int(rpc.call("eth_getBalance", [address, "latest"]), 16) for alias, address in actors.items()}
    result["contract"] = int(rpc.call("eth_getBalance", [contract, "latest"]), 16)
    return result


def verify_deltas(before: dict[str, int], after: dict[str, int], changes: dict[str, Any], context: str) -> None:
    require(set(changes).issubset(before), f"{context}: unknown beneficiary")
    for alias, amount in changes.items():
        actual = after[alias] - before[alias]
        require(actual == parse_wei(amount, context + " " + alias), f"{context}: {alias} expected delta {amount} wei, got {actual}")


def verify_conservation(before: dict[str, int], after: dict[str, int], context: str) -> None:
    require(sum(after.values()) == sum(before.values()), f"{context}: tracked funds not conserved; untracked recipient or gas cost")


def apply_step(step: dict[str, Any], rpc: RPC, case: dict[str, Any], actor_addrs: dict[str, str], contract: str, artifact: dict[str, Any], baseline: dict[str, int], previous: dict[str, int], cast: str) -> dict[str, int]:
    label = step["type"]
    if label == "time":
        rpc.call("evm_increaseTime", [step["advance_seconds"]])
        rpc.call("evm_mine")
        return previous
    if label == "assert":
        if "balances_delta_wei" in step:
            verify_deltas(baseline, previous, step["balances_delta_wei"], "independent total balance oracle")
        if "contract_balance_wei" in step:
            require(previous["contract"] == parse_wei(step["contract_balance_wei"], "assert contract"), "contract balance mismatched")
        return previous
    signature = step["signature"]
    require(signature in signature_set(artifact), f"method {signature} absent from candidate ABI")
    actual_args = [actor_addrs[a[1:]] if isinstance(a, str) and a.startswith("$") and a[1:] in actor_addrs else a for a in step.get("args", [])]
    calldata = cast_encode(signature, actual_args, cast=cast)
    if label == "view":
        raw = rpc.call("eth_call", [{"from": actor_addrs[step["actor"]], "to": contract, "data": calldata}, "latest"])
        require(isinstance(raw, str) and raw.startswith("0x"), "invalid return data")
        if "expect_hex" in step:
            require(raw.lower() == step["expect_hex"].lower(), "independent raw return oracle mismatch")
        if "expect_uint" in step:
            require(len(raw) == 66 and int(raw[2:], 16) == parse_wei(step["expect_uint"], "view uint"), "independent integer return oracle mismatch")
        if "expect_bool" in step:
            require(len(raw) == 66 and int(raw[2:], 16) == int(step["expect_bool"]), "independent boolean return oracle mismatch")
        return previous
    require(label == "send", "unknown step")
    payload = {"from": actor_addrs[step["actor"]], "to": contract, "data": calldata, "value": hex_quantity(parse_wei(step.get("value_wei", "0"), "send value")), "gas": hex_quantity(12_000_000), "gasPrice": "0x0"}
    # Anvil's built-in development accounts are unlocked. No secret keys are read.
    receipt = rpc.wait_receipt(rpc.call("eth_sendTransaction", [payload]))
    success = int(receipt["status"], 16) == 1
    require(success == (step["expect"] == "success"), f"{signature}: expected {step['expect']}, transaction success={success}")
    after = balances(rpc, actor_addrs, contract)
    verify_conservation(previous, after, signature)
    if step.get("changes_wei") is not None:
        verify_deltas(previous, after, step["changes_wei"], signature)
    return after


def execute_sequence(rpc: RPC, case: dict[str, Any], sequence: dict[str, Any], artifact: dict[str, Any], cast: str) -> int:
    addresses = rpc.call("eth_accounts")
    require(len(addresses) >= 10, "Anvil did not expose ten development accounts")
    actor_addrs = {alias: addresses[position] for alias, position in case["actors"].items()}
    constructor = case.get("constructor", {})
    signature = constructor_signature(artifact)
    args = constructor.get("args", [])
    resolved_args = [actor_addrs[a[1:]] if isinstance(a, str) and a.startswith("$") and a[1:] in actor_addrs else a for a in args]
    require(len(resolved_args) == (0 if signature == "constructor()" else signature.count(",") + 1), "constructor argument count mismatches artifact")
    encoded = "" if signature == "constructor()" else cast_encode(signature, resolved_args, constructor=True, cast=cast)[2:]
    data = artifact["bytecode"]["object"] + encoded
    sender = actor_addrs[constructor.get("actor", "payer")]
    tx = rpc.call("eth_sendTransaction", [{"from": sender, "data": data, "value": hex_quantity(parse_wei(constructor.get("value_wei", "0"), "constructor value")), "gas": hex_quantity(25_000_000), "gasPrice": "0x0"}])
    receipt = rpc.wait_receipt(tx)
    require(receipt.get("status") == "0x1" and receipt.get("contractAddress"), f"deployment failed: {sequence['id']}")
    contract = receipt["contractAddress"]
    baseline = balances(rpc, actor_addrs, contract)
    executed = 0
    for variation in expanded_sequences(sequence["steps"]):
        # Snapshot every variation after deployment; time / accounting effects are isolated.
        snap = rpc.call("evm_snapshot")
        previous = baseline
        for step in variation:
            previous = apply_step(step, rpc, case, actor_addrs, contract, artifact, baseline, previous, cast)
        final = sequence.get("final", {})
        if "contract_balance_wei" in final:
            require(previous["contract"] == parse_wei(final["contract_balance_wei"], "terminal balance"), f"{sequence['id']}: stranded/incorrect contract balance")
        if "balances_delta_wei" in final:
            verify_deltas(baseline, previous, final["balances_delta_wei"], sequence["id"] + " final")
        executed += 1
        require(rpc.call("evm_revert", [snap]) is True, "Anvil snapshot restore failed")
    return executed


def run_certificate(report: dict[str, Any], gold: dict[str, Any], workspaces_root: Path | None = None, *, repo_root: Path | None = None, forge: str = "forge", cast: str = "cast", anvil: str = "anvil", anvil_factory: Callable[..., Any] = LocalAnvil) -> dict[str, Any]:
    pairs = validate_gold(report, gold)
    verify_kernel_on_disk(report, repo_root or Path.cwd())
    require(all(shutil.which(tool) is not None for tool in [forge, cast, anvil]), "local Foundry forge/cast/anvil required")
    proof = []
    for expected, case in pairs:
        # Explicit local paths from the report are used only when running on its originating host.
        workspace_name = PureWindowsPath(expected["workspace"]).name
        require(workspace_name.endswith(".workspace") and expected["id"] in workspace_name, "unexpected workspace name in report")
        workspace = (workspaces_root / workspace_name) if workspaces_root is not None else Path(expected["workspace"])
        artifact, source = source_and_artifact(workspace, expected["solidity_sha256"], forge)
        tested = 0
        for sequence in case["sequences"]:
            # Fresh Anvil per independently-authored trace: no state leakage.
            with anvil_factory(anvil) as rpc:
                tested += execute_sequence(rpc, case, sequence, artifact, cast)
        proof.append({"id": expected["id"], "source_sha256": expected["solidity_sha256"],
                      "source_raw_bytes_sha256": digest(source.read_bytes()), "sequence_count": len(case["sequences"]), "execution_variants": tested, "result": "PASS_INDEPENDENT_RUNTIME_TRACES"})
    return {"schema": CERT_SCHEMA, "status": "PASS_STRUCTURALLY_INDEPENDENT_LOCAL_EVM_BEHAVIOR_NOT_EXPERT_CERTIFIED", "source_report_sha256": digest_json(report), "independent_gold_sha256": digest_json(gold), "kernel_sha256": report["kernel_sha256"], "corpus_manifest_sha256": report["corpus_manifest_sha256"], "authorship_attested_not_verified": True, "expert_gold_independence_verified": False, "no_keys_no_remote_rpc_no_live_deployment": True, "cases_passed": len(proof), "cases": proof}


def preflight_gold_runtime(report: dict[str, Any], gold: dict[str, Any],
                           workspaces_root: Path | None = None, *, repo_root: Path | None = None) -> dict[str, Any]:
    """Source/ABI/vector preflight only; NEVER claim this executes on an EVM."""
    pairs = validate_gold(report, gold)
    verify_kernel_on_disk(report, repo_root or Path.cwd())
    records = []
    total_traces = total_variants = total_transactions = 0
    for expected, case in pairs:
        workspace_name = PureWindowsPath(expected["workspace"]).name
        require(workspace_name.endswith(".workspace") and expected["id"] in workspace_name,
                f"unexpected workspace name: {workspace_name}")
        workspace = (workspaces_root / workspace_name) if workspaces_root else Path(expected["workspace"])
        require(workspace.is_dir(), f"workspace missing: {workspace}")
        source = workspace / "src" / "NanoJevGoverned01.sol"
        require(source.is_file(), f"source missing: {source}")
        canonical_solidity_sha256(source, expected["solidity_sha256"])
        artifact = workspace / "out" / "NanoJevGoverned01.sol" / "NanoJevGoverned01.json"
        require(artifact.is_file(), f"compiled artifact missing: {artifact}")
        compiled = read_json(artifact)
        require(isinstance(compiled.get("abi"), list), f"malformed ABI: {expected['id']}")
        require(isinstance(compiled.get("bytecode", {}).get("object"), str)
                and len(compiled["bytecode"]["object"]) > 2,
                f"missing bytecode: {expected['id']}")
        methods = signature_set(compiled)
        constructor = constructor_signature(compiled)
        require(len(case["constructor"].get("args", [])) == (0 if constructor == "constructor()" else constructor.count(",") + 1),
                f"constructor arguments disagree with ABI: {expected['id']}")
        variants = 0
        transactions = 0
        for seq in case["sequences"]:
            variations = expanded_sequences(seq["steps"])
            variants += len(variations)
            for step in _flatten_steps(seq["steps"]):
                if step.get("type") in {"send", "view"}:
                    require(step["signature"] in methods,
                            f"{expected['id']} {seq['id']}: signature absent from ABI: {step['signature']}")
                    require(len(step.get("args", [])) == (0 if step["signature"].endswith("()") else
                             step["signature"].count(",") + 1),
                            f"{expected['id']} {seq['id']}: wrong argument count: {step['signature']}")
                    if step["type"] == "send":
                        transactions += 1
        total_traces += len(case["sequences"])
        total_variants += variants
        total_transactions += transactions
        records.append({"id": expected["id"],
                        "source_sha256": expected["solidity_sha256"],
                        "source_raw_bytes_sha256": digest(source.read_bytes()),
                        "sequences": len(case["sequences"]), "execution_variants": variants,
                        "transaction_steps_before_permutations": transactions})
    return {"schema": CERT_SCHEMA,
            "status": "PASS_GOLD_AND_PINNED_ARTIFACT_PREFLIGHT_NOT_EVM_CERTIFIED",
            "source_report_sha256": digest_json(report),
            "independent_gold_sha256": digest_json(gold),
            "kernel_sha256": report["kernel_sha256"],
            "cases_preflighted": len(records), "traces": total_traces,
            "execution_variants": total_variants,
            "transaction_steps_before_permutations": total_transactions,
            "forged_execution": False, "local_anvil_execution": False,
            "expert_gold_independence_verified": False,
            "cases": records}


def gold_template(report: dict[str, Any]) -> dict[str, Any]:
    accepted = validate_report(report)
    return {"schema": GOLD_SCHEMA, "corpus_manifest_sha256": report["corpus_manifest_sha256"], "kernel_sha256": dict(report["kernel_sha256"]), "authorship": {"reviewer": "", "reference_model_used": False, "method": "Describe the independently written contract semantics and payout calculations"}, "cases": [{"id": c["id"], "spec_sha256": c["spec_sha256"], "solidity_sha256": c["solidity_sha256"], "actors": {"payer": 0, "worker": 1, "outsider": 2, "arbitrator": 3}, "constructor": {"actor": "payer", "args": [], "value_wei": "0"}, "sequences": []} for c in accepted]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True, help="V3 held-out JSON report")
    parser.add_argument("--gold", type=Path, help="independently authored immutable test vectors")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scaffold", action="store_true", help="write an INCOMPLETE gold template instead of certifying")
    parser.add_argument("--preflight-only", action="store_true", help="verify hashes, source, compiled ABI and independent trace structure; NO EVM certificate")
    parser.add_argument("--workspaces-root", type=Path, help="directory containing generated .workspace folders")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="repository root containing frozen V3 tools modules")
    parser.add_argument("--forge-bin", default="forge")
    parser.add_argument("--cast-bin", default="cast")
    parser.add_argument("--anvil-bin", default="anvil")
    args = parser.parse_args(argv)
    try:
        report = read_json(args.report)
        if args.scaffold:
            require(args.gold is None, "--scaffold does not accept --gold")
            atomic_json(args.output, gold_template(report))
            print("INCOMPLETE GOLD TEMPLATE - not certified:", args.output)
            return 0
        require(args.gold is not None, "--gold required to execute certification")
        gold = read_json(args.gold)
        if args.preflight_only:
            result = preflight_gold_runtime(report, gold, args.workspaces_root, repo_root=args.repo_root)
        else:
            result = run_certificate(report, gold, args.workspaces_root, repo_root=args.repo_root, forge=args.forge_bin, cast=args.cast_bin, anvil=args.anvil_bin)
        atomic_json(args.output, result)
        print(json.dumps({"status": result["status"], "cases_checked": result.get("cases_passed", result.get("cases_preflighted", 0)), "output": str(args.output)}))
        return 0
    except (CertificationFailure, ValueError, OSError, KeyError, subprocess.TimeoutExpired) as exc:
        failure = {"schema": CERT_SCHEMA, "status": "FAIL_INDEPENDENT_BEHAVIOR_NOT_CERTIFIED", "blockers": [str(exc)]}
        atomic_json(args.output, failure)
        print(json.dumps(failure), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
