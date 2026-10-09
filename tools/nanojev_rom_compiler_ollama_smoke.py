#!/usr/bin/env python3
"""Smoke-test the ROM interface compiler against local Ollama.

The smoke treats ROM as a parameterized decision program, not as a prose
transcription.  The local model receives an ordinary user question and must:

1. infer the external variables the user most likely intends,
2. expose those variables as typed input/output ports,
3. leave unspecified external sources explicitly UNBOUND,
4. preserve any source paths the user *did* name,
5. compile a reusable ROM program around those ports, and
6. avoid answering the substantive question.

Default local target:
    model: gemma4:26b-a4b-it-q4_K_M
    url:   http://127.0.0.1:11434/api/generate

Run from repo root:
    python .\\tools\\nanojev_rom_compiler_ollama_smoke.py
    python .\\tools\\nanojev_rom_compiler_ollama_smoke.py --cases implicit_consensus implicit_failover self_spec
    python .\\tools\\nanojev_rom_compiler_ollama_smoke.py --model qwen3.8:27b
    python .\\tools\\nanojev_rom_compiler_ollama_smoke.py --self-test

Every live run persists the exact system prompt, natural-language question,
raw Ollama stream, raw text, parsed JSON, per-case grade, and master report
under ``diagnostics_output/nanojev_rom_compiler_ollama_smoke``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable


_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from main_computer import rag_gremlin_pyramid_atom_smoke as ollama_stream  # noqa: E402


SCHEMA = "main-computer-nanojev-rom-interface-ollama-smoke-v3"
ROM_VERSION = 2
DEFAULT_MODEL = os.environ.get("NANOJEV_ROM_COMPILER_MODEL", "gemma4:26b-a4b-it-q4_K_M")
DEFAULT_URL = os.environ.get("NANOJEV_ROM_COMPILER_URL", "http://127.0.0.1:11434/api/generate")
DEFAULT_OUT_ROOT = Path("diagnostics_output") / "nanojev_rom_compiler_ollama_smoke"
DEFAULT_NUM_PREDICT = 2200
DEFAULT_KEEP_ALIVE = "30m"

# Human-visible ROM words are deliberately semantic rather than numbered stop
# codes.  The paired terms are part of the language contract.
ROM_OPS = {
    "BEGIN",
    "END",
    "OPEN",
    "SEAL",
    "NEXT",
    "DONE",
    "BIND",
    "RELATE",
    "OBJECTIVE",
    "RESOLVE",
    "HOLD",
    "VERIFY",
    "COMMIT",
    "EXPECT",
    "UNKNOWN",
}

PAIRED_OPS = {
    "BEGIN": "END",
    "OPEN": "SEAL",
    "NEXT": "DONE",
    "HOLD": "COMMIT",
    "KNOWN": "UNKNOWN",
    "SAME": "DIFFERENT",
}

ROM_SYSTEM_PROMPT = r"""You are the ROM Interface Compiler for the Main Computer.

Your job is to accept the user's ordinary natural-language question and compile
it into a parameterized ROM decision program.  The user should not have to know
ROM syntax or explicitly declare every variable.  You infer the variables that
are most likely to matter, expose them as ports, and construct a reusable
program around those ports.

You are a compiler, not the final decision maker.  Do not answer the user's
substantive question.  Return JSON only, with no Markdown fences or prose.

CORE IDEA
Natural language tells you what the user wants to decide.  ROM exposes the
semantic sockets through which outside Main Computer state can later be wired
into that decision.

A ROM result therefore has three distinct layers:
1. INTERFACE: external INPUT and OUTPUT variables.
2. BINDINGS: where those inputs come from, if the user explicitly named a source.
3. PROGRAM: a reusable decision procedure over the ports.

VARIABLE INFERENCE
Infer the variables the user most likely intends even when they were not named
as variables.  Use short semantic snake_case names such as:
  report_a, report_b, report_c
  current_rpc_latency, max_rpc_latency
  validator_health
  current_loss, incumbent_loss
  outlier, should_fail_over, should_promote

Do not create a separate input for every noun in the sentence.  Prefer the
smallest set of external variables needed to make the decision program reusable.

Each input port MUST contain:
{
  "name": "...",
  "type": "...",
  "role": "...",
  "required": true,
  "inferred_from": "short phrase from the user question",
  "binding": {
    "status": "UNBOUND" or "BOUND",
    "source": null or "exact.user.named.source"
  }
}

BINDING HONESTY
Semantic inference and source binding are different operations.

If the user did NOT name an external source path, connector, sensor, file,
register, service field, or state location, the port MUST remain:
  {"status":"UNBOUND","source":null}

Never invent a Main Computer path merely because one seems plausible.

If the user DID explicitly name a source, preserve that source string exactly
and mark the port BOUND.  Do not normalize, rename, or silently substitute it.

OUTPUT ports contain:
{
  "name": "...",
  "type": "...",
  "role": "decision" or another short semantic role,
  "allowed_values": [...] or null
}

LOCALS
Derived internal values belong in "locals", not "inputs".  Examples:
  ab_relation
  ac_relation
  bc_relation
  threshold_exceeded
  candidate_is_better

ROM CONTROL LANGUAGE
The human-visible ROM language uses semantic paired words, not numbered stop
codes.  These meanings are stable:

BEGIN  <-> END      enter / leave a decision unit
OPEN   <-> SEAL     create / finalize an addressable object
NEXT   <-> DONE     another peer follows / peer collection is complete
HOLD   <-> COMMIT   candidate result is provisional / candidate is stabilized
KNOWN  <-> UNKNOWN  value is available / explicitly unavailable
SAME   <-> DIFFERENT relation-result complements

Additional atomic operations:
BIND       connect an INPUT port to an addressable ROM object
RELATE     compute or request a relation between objects into a local
EXPECT     declare allowed next state/operator when useful
OBJECTIVE  declare the user's actual decision objective
RESOLVE    derive an output candidate from inputs/locals
VERIFY     check a candidate against constraints/topology

One opcode should correspond to one atomic state transition.  Do not encode
multiple hidden steps inside a vague operation when the program can state them.

ROM PROGRAM SHAPE
A simple externally-bound object:
  BEGIN
  OPEN A
  BIND A <- input_name
  SEAL A
  DONE
  OBJECTIVE ...
  RESOLVE output_name
  HOLD output_name
  VERIFY ...
  COMMIT output_name
  END

A three-way consensus program:
  BEGIN
  OPEN A
  BIND A <- report_a
  SEAL A
  NEXT
  OPEN B
  BIND B <- report_b
  SEAL B
  NEXT
  OPEN C
  BIND C <- report_c
  SEAL C
  DONE
  RELATE A B USING EQUIVALENCE -> ab_relation
  RELATE A C USING EQUIVALENCE -> ac_relation
  RELATE B C USING EQUIVALENCE -> bc_relation
  OBJECTIVE consensus_outlier_or_none
  RESOLVE outlier FROM ab_relation ac_relation bc_relation
  HOLD outlier
  VERIFY consensus_topology
  COMMIT outlier
  END

The compiler does NOT fill ab_relation/ac_relation/bc_relation with SAME or
DIFFERENT unless those relation results are explicitly given as facts in the
question.  The external values may change later; the program must remain reusable.

PROGRAM JSON
Every program instruction is an object with "op" plus explicit operands.  For
example:
  {"op":"OPEN","object":"A"}
  {"op":"BIND","object":"A","input":"report_a"}
  {"op":"RELATE","left":"A","right":"B","using":"EQUIVALENCE","into":"ab_relation"}
  {"op":"OBJECTIVE","name":"consensus_outlier_or_none"}
  {"op":"RESOLVE","output":"outlier","from":["ab_relation","ac_relation","bc_relation"]}
  {"op":"HOLD","output":"outlier"}
  {"op":"VERIFY","rule":"consensus_topology"}
  {"op":"COMMIT","output":"outlier"}

TOP-LEVEL OUTPUT SCHEMA
Return exactly one JSON object shaped as:
{
  "rom_version": 2,
  "question_intent": "short description",
  "inputs": [ ... ],
  "outputs": [ ... ],
  "locals": [
    {"name":"...","type":"...","role":"..."}
  ],
  "program": [ ... ],
  "unresolved": [ ... ],
  "decision": null
}

"decision" MUST remain null because you are compiling, not deciding.

UNRESOLVED STATE
Use "unresolved" for ambiguities that prevent a confident interface or program
shape.  Do not fabricate facts or sources.  A normal UNBOUND source is not by
itself an error and need not appear in unresolved; it is an intentional socket
for later user/Main-Computer wiring.

COMPILATION METHOD
1. Determine the decision the user is trying to make.
2. Infer the minimum useful external variables.
3. Assign each variable a semantic role and type.
4. Preserve explicit external source paths exactly; otherwise leave binding UNBOUND.
5. Infer the output variable(s).
6. Put derived intermediate state in locals.
7. Build a reusable ROM program over input ports, not a one-off answer over literal values.
8. Use BEGIN/END, OPEN/SEAL, NEXT/DONE, HOLD/COMMIT symmetrically and consistently.
9. Do not answer the decision; leave decision null.
10. Prefer a small explicit program that another system can wire to live state.

SELF DESCRIPTION
If the user asks you to emit your ROM/interface specification, return:
{
  "rom_spec": {
    "protocol": "ROM-INTERFACE",
    "version": 2,
    "paired_ops": {...},
    "ops": [...],
    "port_schema": {...},
    "binding_rules": [...],
    "program_invariants": [...]
  }
}

The caller may request either of two equivalent serializations for rom_spec.ops:

FLAT OPS
  ["BEGIN", "END", "OPEN", "SEAL", ...]

STRUCTURED OPS
  [
    {"op":"BEGIN","description":"..."},
    {"op":"END","description":"..."},
    ...
  ]

When the caller explicitly requests one form, obey that form exactly.  The two
forms describe the same ROM instruction set; serialization must not change the
meaning or omit instructions.

This is the public protocol from this prompt, not hidden model reasoning.
"""


@dataclass(frozen=True)
class SmokeCase:
    name: str
    prompt: str
    kind: str = "program"
    min_inputs: int = 1
    max_inputs: int | None = None
    min_outputs: int = 1
    required_input_name_tokens: tuple[str, ...] = ()
    required_input_roles: tuple[str, ...] = ()
    required_output_name_tokens: tuple[str, ...] = ()
    required_ops: tuple[str, ...] = ()
    required_relations: int = 0
    require_all_unbound: bool = False
    required_bound_sources: tuple[str, ...] = ()
    forbidden_source_tokens: tuple[str, ...] = ()
    expected_objective_token: str | None = None
    self_spec_ops_format: str | None = None


CASES: dict[str, SmokeCase] = {
    "implicit_consensus": SmokeCase(
        name="implicit_consensus",
        min_inputs=3,
        max_inputs=3,
        min_outputs=1,
        required_input_name_tokens=("report",),
        required_input_roles=("candidate",),
        required_output_name_tokens=("outlier",),
        required_ops=("BEGIN", "OPEN", "BIND", "SEAL", "NEXT", "DONE", "RELATE", "OBJECTIVE", "RESOLVE", "HOLD", "VERIFY", "COMMIT", "END"),
        required_relations=3,
        require_all_unbound=True,
        expected_objective_token="consensus",
        prompt=(
            "Which of the three validator reports is inconsistent with the other two? "
            "I want the Main Computer to make that decision whenever three reports are supplied. "
            "The answer should be A, B, C, or NONE if all three agree."
        ),
    ),
    "implicit_failover": SmokeCase(
        name="implicit_failover",
        min_inputs=3,
        max_inputs=4,
        min_outputs=1,
        required_input_name_tokens=("latency", "health"),
        required_output_name_tokens=("fail",),
        required_ops=("BEGIN", "BIND", "OBJECTIVE", "RESOLVE", "HOLD", "VERIFY", "COMMIT", "END"),
        require_all_unbound=True,
        expected_objective_token="fail",
        prompt=(
            "Should we fail over the RPC when its current latency is above the maximum acceptable latency and the primary validator is unhealthy?"
        ),
    ),
    "implicit_promotion": SmokeCase(
        name="implicit_promotion",
        min_inputs=4,
        max_inputs=5,
        min_outputs=1,
        required_input_name_tokens=("loss", "accuracy"),
        required_output_name_tokens=("promot",),
        required_ops=("BEGIN", "BIND", "OBJECTIVE", "RESOLVE", "HOLD", "VERIFY", "COMMIT", "END"),
        require_all_unbound=True,
        expected_objective_token="promot",
        prompt=(
            "Should the current model replace the incumbent when its fresh-predev loss is lower and its accuracy does not regress?"
        ),
    ),
    "explicit_sources": SmokeCase(
        name="explicit_sources",
        min_inputs=2,
        max_inputs=3,
        min_outputs=1,
        required_bound_sources=("guardian.last_proof", "validator_a.state"),
        required_ops=("BEGIN", "BIND", "OBJECTIVE", "RESOLVE", "HOLD", "VERIFY", "COMMIT", "END"),
        expected_objective_token="valid",
        prompt=(
            "Decide whether the proof is valid for the validator state. Use guardian.last_proof as the proof input and validator_a.state as the validator-state input."
        ),
    ),
    "unbound_honesty": SmokeCase(
        name="unbound_honesty",
        min_inputs=2,
        max_inputs=4,
        min_outputs=1,
        required_input_name_tokens=("generation",),
        required_ops=("BEGIN", "BIND", "OBJECTIVE", "RESOLVE", "HOLD", "VERIFY", "COMMIT", "END"),
        require_all_unbound=True,
        forbidden_source_tokens=("runtime", "state", "guardian", "validator", "main_computer"),
        expected_objective_token="generation",
        prompt=(
            "Has the accepted generation caught up with the generation we expected to be active?"
        ),
    ),
    "self_spec_flat_ops": SmokeCase(
        name="self_spec_flat_ops",
        kind="self_spec",
        min_inputs=0,
        min_outputs=0,
        self_spec_ops_format="flat",
        prompt=(
            "Emit your ROM interface specification. In rom_spec.ops, use a flat JSON array "
            "containing only opcode-name strings, for example [\"BEGIN\",\"END\",\"OPEN\"]. "
            "Do not use objects inside rom_spec.ops."
        ),
    ),
    "self_spec_structured_ops": SmokeCase(
        name="self_spec_structured_ops",
        kind="self_spec",
        min_inputs=0,
        min_outputs=0,
        self_spec_ops_format="structured",
        prompt=(
            "Emit your ROM interface specification. In rom_spec.ops, use a JSON array of objects. "
            "Every object must contain a non-empty opcode name in the field 'op' and a non-empty "
            "human-readable 'description'. Do not use bare strings inside rom_spec.ops."
        ),
    ),
}


@dataclass
class Grade:
    ok: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    parsed_kind: str = "invalid"

    def as_dict(self) -> dict[str, Any]:
        passed = sum(1 for value in self.checks.values() if value)
        total = len(self.checks)
        return {
            "ok": self.ok,
            "parsed_kind": self.parsed_kind,
            "checks": self.checks,
            "passed_checks": passed,
            "total_checks": total,
            "score": (passed / total) if total else 0.0,
            "reasons": self.reasons,
        }


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def strip_fences(text: str) -> str:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = strip_fences(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _add_check(checks: dict[str, bool], reasons: list[str], name: str, condition: bool, failure: str) -> None:
    checks[name] = bool(condition)
    if not condition:
        reasons.append(failure)


def op_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip().upper()
    if isinstance(item, dict):
        return str(item.get("op") or "").strip().upper()
    return ""


def names(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip().lower()
            if name:
                result.append(name)
    return result


def roles(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            role = str(item.get("role") or "").strip().lower()
            if role:
                result.append(role)
    return result


def source_bindings(inputs: Any) -> list[tuple[str, str | None, str]]:
    result: list[tuple[str, str | None, str]] = []
    if not isinstance(inputs, list):
        return result
    for item in inputs:
        if not isinstance(item, dict):
            continue
        binding = item.get("binding")
        if not isinstance(binding, dict):
            result.append((str(item.get("name") or ""), None, ""))
            continue
        source = binding.get("source")
        source_text = None if source is None else str(source)
        status = str(binding.get("status") or "").strip().upper()
        result.append((str(item.get("name") or ""), source_text, status))
    return result


def program_text(program: Any) -> str:
    if not isinstance(program, list):
        return ""
    return json.dumps(program, sort_keys=True, ensure_ascii=False).lower()


def grade_program(case: SmokeCase, payload: dict[str, Any] | None) -> Grade:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    if payload is None:
        return Grade(ok=False, checks={"valid_json_object": False}, reasons=["response is not a parseable JSON object"])

    _add_check(checks, reasons, "rom_version", payload.get("rom_version") == ROM_VERSION, "rom_version must equal 2")
    _add_check(checks, reasons, "decision_null", payload.get("decision") is None, "compiler must not answer the substantive question; decision must be null")
    _add_check(checks, reasons, "question_intent", bool(str(payload.get("question_intent") or "").strip()), "question_intent must be non-empty")

    inputs = payload.get("inputs")
    outputs = payload.get("outputs")
    locals_ = payload.get("locals")
    unresolved = payload.get("unresolved")
    program = payload.get("program")

    _add_check(checks, reasons, "inputs_array", isinstance(inputs, list), "inputs must be an array")
    _add_check(checks, reasons, "outputs_array", isinstance(outputs, list), "outputs must be an array")
    _add_check(checks, reasons, "locals_array", isinstance(locals_, list), "locals must be an array")
    _add_check(checks, reasons, "unresolved_array", isinstance(unresolved, list), "unresolved must be an array")
    _add_check(checks, reasons, "program_array", isinstance(program, list) and bool(program), "program must be a non-empty array")

    input_list = inputs if isinstance(inputs, list) else []
    output_list = outputs if isinstance(outputs, list) else []
    local_list = locals_ if isinstance(locals_, list) else []
    program_list = program if isinstance(program, list) else []

    _add_check(checks, reasons, "min_inputs", len(input_list) >= case.min_inputs, f"expected at least {case.min_inputs} inferred inputs")
    if case.max_inputs is not None:
        _add_check(checks, reasons, "max_inputs", len(input_list) <= case.max_inputs, f"expected no more than {case.max_inputs} inferred inputs")
    _add_check(checks, reasons, "min_outputs", len(output_list) >= case.min_outputs, f"expected at least {case.min_outputs} outputs")

    input_shape_ok = True
    for item in input_list:
        if not isinstance(item, dict):
            input_shape_ok = False
            continue
        required = ("name", "type", "role", "required", "inferred_from", "binding")
        if any(key not in item for key in required):
            input_shape_ok = False
            continue
        if not str(item.get("name") or "").strip() or not str(item.get("type") or "").strip() or not str(item.get("role") or "").strip():
            input_shape_ok = False
        if not isinstance(item.get("binding"), dict):
            input_shape_ok = False
    _add_check(checks, reasons, "input_port_shape", input_shape_ok and len(input_list) > 0, "every input must have name/type/role/required/inferred_from/binding")

    output_shape_ok = True
    for item in output_list:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip() or not str(item.get("type") or "").strip() or not str(item.get("role") or "").strip():
            output_shape_ok = False
    _add_check(checks, reasons, "output_port_shape", output_shape_ok and len(output_list) >= case.min_outputs, "every output must have name/type/role")

    input_names = names(input_list)
    output_names = names(output_list)
    input_roles = roles(input_list)
    for token in case.required_input_name_tokens:
        _add_check(
            checks,
            reasons,
            f"input_name_{token}",
            any(token.lower() in name for name in input_names),
            f"expected an inferred input name containing {token!r}",
        )
    for role_token in case.required_input_roles:
        _add_check(
            checks,
            reasons,
            f"input_role_{role_token}",
            any(role_token.lower() in role for role in input_roles),
            f"expected an inferred input role containing {role_token!r}",
        )
    for token in case.required_output_name_tokens:
        _add_check(
            checks,
            reasons,
            f"output_name_{token}",
            any(token.lower() in name for name in output_names),
            f"expected an output name containing {token!r}",
        )

    bindings = source_bindings(input_list)
    binding_shape_ok = all(status in {"BOUND", "UNBOUND"} and ((status == "BOUND" and bool(source)) or (status == "UNBOUND" and source is None)) for _, source, status in bindings)
    _add_check(checks, reasons, "binding_shape", binding_shape_ok and len(bindings) == len(input_list), "bindings must be BOUND with a source or UNBOUND with source=null")

    if case.require_all_unbound:
        all_unbound = bool(bindings) and all(status == "UNBOUND" and source is None for _, source, status in bindings)
        _add_check(checks, reasons, "binding_honesty_unbound", all_unbound, "question names no external sources; every inferred input must remain UNBOUND")

    binding_sources = [source for _, source, status in bindings if status == "BOUND" and source]
    for source in case.required_bound_sources:
        _add_check(
            checks,
            reasons,
            f"bound_source_{source}",
            source in binding_sources,
            f"explicit source {source!r} must be preserved exactly as a BOUND source",
        )

    all_source_text = "\n".join(source or "" for _, source, _ in bindings).lower()
    for token in case.forbidden_source_tokens:
        _add_check(
            checks,
            reasons,
            f"forbidden_source_{token}",
            token.lower() not in all_source_text,
            f"model invented a source containing forbidden token {token!r}",
        )

    ops = [op_name(item) for item in program_list]
    _add_check(checks, reasons, "known_ops", bool(ops) and all(op in ROM_OPS for op in ops), "program contains empty or unknown ROM opcodes")
    _add_check(checks, reasons, "starts_begin", bool(ops) and ops[0] == "BEGIN", "program must start with BEGIN")
    _add_check(checks, reasons, "ends_end", bool(ops) and ops[-1] == "END", "program must end with END")
    for op in case.required_ops:
        _add_check(checks, reasons, f"op_{op.lower()}", op in ops, f"program requires {op}")

    bind_inputs = [str(item.get("input") or "").strip().lower() for item in program_list if isinstance(item, dict) and op_name(item) == "BIND"]
    _add_check(
        checks,
        reasons,
        "program_binds_inputs",
        bool(input_names) and set(input_names).issubset(set(bind_inputs)),
        "program must BIND every inferred input port",
    )

    relation_ops = [item for item in program_list if isinstance(item, dict) and op_name(item) == "RELATE"]
    if case.required_relations:
        _add_check(checks, reasons, "relation_count", len(relation_ops) >= case.required_relations, f"expected at least {case.required_relations} RELATE operations")
        relation_targets = {str(item.get("into") or "").strip().lower() for item in relation_ops if str(item.get("into") or "").strip()}
        local_names = set(names(local_list))
        _add_check(checks, reasons, "relation_locals_declared", bool(relation_targets) and relation_targets.issubset(local_names), "RELATE outputs must be declared as locals")

    text = program_text(program_list)
    if case.expected_objective_token:
        _add_check(
            checks,
            reasons,
            "objective_semantics",
            case.expected_objective_token.lower() in text,
            f"program objective/resolution should contain semantic token {case.expected_objective_token!r}",
        )

    # Generic cadence checks: the program must actually use the paired control
    # concepts as structure, not merely list them somewhere in prose.
    def first_index(name: str) -> int:
        try:
            return ops.index(name)
        except ValueError:
            return -1

    i_obj = first_index("OBJECTIVE")
    i_resolve = first_index("RESOLVE")
    i_hold = first_index("HOLD")
    i_commit = first_index("COMMIT")
    i_end = first_index("END")
    _add_check(
        checks,
        reasons,
        "decision_cadence",
        i_obj >= 0 and i_resolve > i_obj and i_hold > i_resolve and i_commit > i_hold and i_end > i_commit,
        "required decision cadence is OBJECTIVE -> RESOLVE -> HOLD -> COMMIT -> END",
    )

    return Grade(ok=all(checks.values()), checks=checks, reasons=reasons, parsed_kind="program")


def grade_self_spec(case: SmokeCase, payload: dict[str, Any] | None) -> Grade:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    if payload is None:
        return Grade(ok=False, checks={"valid_json_object": False}, reasons=["response is not a parseable JSON object"])

    spec = payload.get("rom_spec")
    _add_check(checks, reasons, "rom_spec_object", isinstance(spec, dict), "rom_spec must be an object")
    spec = spec if isinstance(spec, dict) else {}
    _add_check(checks, reasons, "protocol", str(spec.get("protocol") or "").strip().upper() == "ROM-INTERFACE", "protocol must be ROM-INTERFACE")
    _add_check(checks, reasons, "version", spec.get("version") == ROM_VERSION, "version must equal 2")

    paired = spec.get("paired_ops")
    _add_check(checks, reasons, "paired_ops_object", isinstance(paired, dict), "paired_ops must be an object")
    paired = paired if isinstance(paired, dict) else {}
    for left, right in PAIRED_OPS.items():
        _add_check(checks, reasons, f"pair_{left.lower()}", str(paired.get(left) or "").strip().upper() == right, f"paired_ops must map {left} to {right}")

    ops = spec.get("ops")
    ops_list = ops if isinstance(ops, list) else []
    ops_upper = {op_name(item) for item in ops_list if op_name(item)}
    _add_check(checks, reasons, "ops", ROM_OPS.issubset(ops_upper), "ops must include the complete ROM interface instruction set")

    if case.self_spec_ops_format == "flat":
        flat_ok = bool(ops_list) and all(isinstance(item, str) and item.strip() for item in ops_list)
        _add_check(
            checks,
            reasons,
            "ops_format_flat",
            flat_ok,
            "rom_spec.ops must be a flat array of opcode-name strings only",
        )
    elif case.self_spec_ops_format == "structured":
        structured_ok = bool(ops_list) and all(
            isinstance(item, dict)
            and bool(str(item.get("op") or "").strip())
            and bool(str(item.get("description") or "").strip())
            for item in ops_list
        )
        _add_check(
            checks,
            reasons,
            "ops_format_structured",
            structured_ok,
            "rom_spec.ops must be an array of {op, description} objects",
        )

    _add_check(checks, reasons, "port_schema", isinstance(spec.get("port_schema"), dict) and bool(spec.get("port_schema")), "port_schema must be a non-empty object")
    _add_check(checks, reasons, "binding_rules", isinstance(spec.get("binding_rules"), list) and bool(spec.get("binding_rules")), "binding_rules must be a non-empty array")
    _add_check(checks, reasons, "program_invariants", isinstance(spec.get("program_invariants"), list) and bool(spec.get("program_invariants")), "program_invariants must be a non-empty array")

    return Grade(ok=all(checks.values()), checks=checks, reasons=reasons, parsed_kind="self_spec")


def grade_response(case: SmokeCase, text: str) -> tuple[dict[str, Any] | None, Grade]:
    payload = extract_json_object(text)
    if case.kind == "self_spec":
        return payload, grade_self_spec(case, payload)
    return payload, grade_program(case, payload)


def selected_cases(values: list[str] | None) -> list[SmokeCase]:
    if not values:
        return list(CASES.values())
    unknown = [name for name in values if name not in CASES]
    if unknown:
        raise ValueError(f"unknown case(s): {', '.join(unknown)}")
    return [CASES[name] for name in values]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test the ROM interface compiler system prompt against local Ollama.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--cases", nargs="*", choices=tuple(CASES), help="Subset of cases; default is all cases.")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    parser.add_argument("--keep-alive", default=DEFAULT_KEEP_ALIVE)
    parser.add_argument("--timeout-s", type=int, default=0, help="Forwarded for logging; streaming helper intentionally disables HTTP read timeout.")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--self-test", action="store_true", help="Run offline parser/grader self-test; do not call Ollama.")
    return parser


def run_case(
    *,
    args: argparse.Namespace,
    case: SmokeCase,
    out_dir: Path,
    log: ollama_stream.Logger,
    caller: Callable[..., tuple[str, dict[str, Any]]] = ollama_stream.call_ollama_generate_streaming,
) -> dict[str, Any]:
    case_dir = out_dir / "cases" / case.name
    case_dir.mkdir(parents=True, exist_ok=True)
    write_text(case_dir / "system_prompt.txt", ROM_SYSTEM_PROMPT)
    write_text(case_dir / "user_prompt.txt", case.prompt + "\n")

    payload = {
        "model": args.model,
        "system": ROM_SYSTEM_PROMPT,
        "prompt": case.prompt,
        "stream": True,
        "keep_alive": args.keep_alive,
        "options": {
            "temperature": 0,
            "num_predict": args.num_predict,
        },
    }
    write_json(case_dir / "request.json", payload)

    log.banner(f"ROM INTERFACE CASE: {case.name}")
    started = time.perf_counter()
    try:
        response_text, stream_summary = caller(
            payload=payload,
            url=args.url,
            timeout_s=args.timeout_s,
            log=log,
            raw_path=case_dir / "raw_stream.jsonl",
            stream_label=f"rom-interface-{case.name}",
        )
        error = None
    except Exception as exc:
        response_text = ""
        stream_summary = {}
        error = f"{type(exc).__name__}: {exc}"
        log(f"[{case.name}] ERROR: {error}")

    elapsed_s = time.perf_counter() - started
    write_text(case_dir / "response.txt", response_text)
    parsed, grade = grade_response(case, response_text)
    if error:
        grade.ok = False
        grade.reasons.insert(0, error)
        grade.checks["ollama_call"] = False
    else:
        grade.checks["ollama_call"] = True
        grade.ok = all(grade.checks.values())

    if parsed is not None:
        write_json(case_dir / "parsed.json", parsed)
    write_json(case_dir / "grade.json", grade.as_dict())
    write_json(case_dir / "stream_summary.json", stream_summary)

    status = "PASS" if grade.ok else "FAIL"
    log(f"[{case.name}] {status} score={grade.as_dict()['score']:.3f} elapsed_s={elapsed_s:.2f}")
    for reason in grade.reasons:
        log(f"[{case.name}] reason: {reason}")

    return {
        "case": case.name,
        "kind": case.kind,
        "ok": grade.ok,
        "grade": grade.as_dict(),
        "elapsed_s": elapsed_s,
        "response_chars": len(response_text),
        "case_dir": str(case_dir),
        "stream_summary": stream_summary,
        "error": error,
    }


def _case_ab_metrics(result: dict[str, Any]) -> dict[str, Any]:
    grade = result.get("grade") if isinstance(result.get("grade"), dict) else {}
    stream = result.get("stream_summary") if isinstance(result.get("stream_summary"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "score": float(grade.get("score") or 0.0),
        "passed_checks": int(grade.get("passed_checks") or 0),
        "total_checks": int(grade.get("total_checks") or 0),
        "elapsed_s": float(result.get("elapsed_s") or 0.0),
        "response_chars": int(result.get("response_chars") or 0),
        "eval_count": int(stream.get("eval_count") or 0),
    }


def build_self_spec_ab_comparison(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    by_name = {str(item.get("case")): item for item in results}
    flat_result = by_name.get("self_spec_flat_ops")
    structured_result = by_name.get("self_spec_structured_ops")
    if not flat_result or not structured_result:
        return None

    flat = _case_ab_metrics(flat_result)
    structured = _case_ab_metrics(structured_result)

    def quality_winner() -> str:
        if flat["score"] > structured["score"]:
            return "flat"
        if structured["score"] > flat["score"]:
            return "structured"
        return "tie"

    def lower_winner(key: str) -> str:
        if flat[key] < structured[key]:
            return "flat"
        if structured[key] < flat[key]:
            return "structured"
        return "tie"

    quality = quality_winner()
    if quality == "tie":
        # Only use speed as an overall tie-breaker after semantic/format score ties.
        overall = lower_winner("elapsed_s")
    else:
        overall = quality

    return {
        "flat": flat,
        "structured": structured,
        "quality_winner": quality,
        "latency_winner": lower_winner("elapsed_s"),
        "brevity_winner": lower_winner("response_chars"),
        "eval_count_winner": lower_winner("eval_count"),
        "overall_winner": overall,
        "rule": "higher grade score wins; elapsed time breaks an exact score tie",
    }


def run_suite(
    args: argparse.Namespace,
    *,
    caller: Callable[..., tuple[str, dict[str, Any]]] = ollama_stream.call_ollama_generate_streaming,
) -> int:
    cases = selected_cases(args.cases)
    run_id = args.run_id or f"rom-interface-{utc_stamp()}"
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log = ollama_stream.Logger(out_dir / "run.log", quiet=args.quiet)

    write_text(out_dir / "rom_system_prompt.txt", ROM_SYSTEM_PROMPT)
    write_json(
        out_dir / "configuration.json",
        {
            "schema_version": SCHEMA,
            "rom_version": ROM_VERSION,
            "model": args.model,
            "url": args.url,
            "cases": [case.name for case in cases],
            "num_predict": args.num_predict,
            "keep_alive": args.keep_alive,
        },
    )

    results = [run_case(args=args, case=case, out_dir=out_dir, log=log, caller=caller) for case in cases]
    passed = sum(1 for result in results if result["ok"])
    ab_comparison = build_self_spec_ab_comparison(results)
    report = {
        "schema_version": SCHEMA,
        "ok": passed == len(results),
        "model": args.model,
        "rom_version": ROM_VERSION,
        "cases_passed": passed,
        "cases_total": len(results),
        "self_spec_ops_ab": ab_comparison,
        "results": results,
        "report": str(out_dir / "report.json"),
    }
    write_json(out_dir / "report.json", report)

    log.banner("ROM INTERFACE SMOKE SUMMARY")
    log(f"result={'PASS' if report['ok'] else 'FAIL'} cases={passed}/{len(results)}")
    log(f"report={out_dir / 'report.json'}")
    if ab_comparison is not None:
        log.banner("ROM SELF-SPEC OPS A/B")
        log(
            "flat: "
            f"{'PASS' if ab_comparison['flat']['ok'] else 'FAIL'} "
            f"score={ab_comparison['flat']['score']:.3f} "
            f"elapsed_s={ab_comparison['flat']['elapsed_s']:.2f} "
            f"eval_count={ab_comparison['flat']['eval_count']} "
            f"chars={ab_comparison['flat']['response_chars']}"
        )
        log(
            "structured: "
            f"{'PASS' if ab_comparison['structured']['ok'] else 'FAIL'} "
            f"score={ab_comparison['structured']['score']:.3f} "
            f"elapsed_s={ab_comparison['structured']['elapsed_s']:.2f} "
            f"eval_count={ab_comparison['structured']['eval_count']} "
            f"chars={ab_comparison['structured']['response_chars']}"
        )
        log(
            f"quality_winner={ab_comparison['quality_winner']} "
            f"latency_winner={ab_comparison['latency_winner']} "
            f"overall_winner={ab_comparison['overall_winner']}"
        )
    summary = {key: report[key] for key in ("cases_passed", "cases_total", "model", "ok", "report", "schema_version")}
    if ab_comparison is not None:
        summary["self_spec_ops_ab"] = ab_comparison
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


def _fixture_program(
    *,
    input_names: list[str],
    output_name: str,
    objective: str,
    bound_sources: dict[str, str] | None = None,
    relations: int = 0,
) -> dict[str, Any]:
    bound_sources = bound_sources or {}
    inputs = []
    program: list[dict[str, Any]] = [{"op": "BEGIN"}]
    locals_: list[dict[str, Any]] = []
    object_names: list[str] = []
    for index, input_name in enumerate(input_names):
        source = bound_sources.get(input_name)
        inputs.append(
            {
                "name": input_name,
                "type": "value",
                "role": "candidate" if "report" in input_name else "observation",
                "required": True,
                "inferred_from": input_name.replace("_", " "),
                "binding": {"status": "BOUND" if source else "UNBOUND", "source": source},
            }
        )
        obj = chr(ord("A") + index)
        object_names.append(obj)
        if index:
            program.append({"op": "NEXT"})
        program.extend([
            {"op": "OPEN", "object": obj},
            {"op": "BIND", "object": obj, "input": input_name},
            {"op": "SEAL", "object": obj},
        ])
    program.append({"op": "DONE"})

    if relations:
        pairs = [("A", "B"), ("A", "C"), ("B", "C")]
        for index, (left, right) in enumerate(pairs[:relations]):
            local = f"{left.lower()}{right.lower()}_relation"
            locals_.append({"name": local, "type": "relation", "role": "derived"})
            program.append({"op": "RELATE", "left": left, "right": right, "using": "EQUIVALENCE", "into": local})

    program.extend([
        {"op": "OBJECTIVE", "name": objective},
        {"op": "RESOLVE", "output": output_name, "from": [item["name"] for item in locals_] or input_names},
        {"op": "HOLD", "output": output_name},
        {"op": "VERIFY", "rule": objective},
        {"op": "COMMIT", "output": output_name},
        {"op": "END"},
    ])
    return {
        "rom_version": ROM_VERSION,
        "question_intent": objective.replace("_", " "),
        "inputs": inputs,
        "outputs": [{"name": output_name, "type": "decision", "role": "decision", "allowed_values": None}],
        "locals": locals_,
        "program": program,
        "unresolved": [],
        "decision": None,
    }


def run_self_test() -> int:
    failures: list[str] = []

    fixture = _fixture_program(
        input_names=["report_a", "report_b", "report_c"],
        output_name="outlier",
        objective="consensus_outlier_or_none",
        relations=3,
    )
    _, grade = grade_response(CASES["implicit_consensus"], json.dumps(fixture))
    if not grade.ok:
        failures.append(f"implicit_consensus fixture failed: {grade.as_dict()}")

    cheated = json.loads(json.dumps(fixture))
    cheated["inputs"][0]["binding"] = {"status": "BOUND", "source": "runtime.state.validator_a"}
    _, grade = grade_response(CASES["implicit_consensus"], json.dumps(cheated))
    if grade.ok or grade.checks.get("binding_honesty_unbound", True):
        failures.append("invented binding was not rejected")

    explicit = _fixture_program(
        input_names=["proof", "validator_state"],
        output_name="is_valid",
        objective="validate_proof",
        bound_sources={"proof": "guardian.last_proof", "validator_state": "validator_a.state"},
    )
    _, grade = grade_response(CASES["explicit_sources"], json.dumps(explicit))
    if not grade.ok:
        failures.append(f"explicit source fixture failed: {grade.as_dict()}")

    answered = json.loads(json.dumps(fixture))
    answered["decision"] = "C"
    _, grade = grade_response(CASES["implicit_consensus"], json.dumps(answered))
    if grade.ok or grade.checks.get("decision_null", True):
        failures.append("answered decision was not rejected")

    flat_spec = {
        "rom_spec": {
            "protocol": "ROM-INTERFACE",
            "version": 2,
            "paired_ops": dict(PAIRED_OPS),
            "ops": sorted(ROM_OPS),
            "port_schema": {"input": ["name", "type", "role", "binding"]},
            "binding_rules": ["unnamed external sources stay UNBOUND"],
            "program_invariants": ["BEGIN first", "END last", "decision remains null during compilation"],
        }
    }
    _, grade = grade_response(CASES["self_spec_flat_ops"], json.dumps(flat_spec))
    if not grade.ok:
        failures.append(f"self_spec_flat_ops fixture failed: {grade.as_dict()}")

    structured_spec = json.loads(json.dumps(flat_spec))
    structured_spec["rom_spec"]["ops"] = [
        {"op": op, "description": f"ROM operation {op}"}
        for op in sorted(ROM_OPS)
    ]
    _, grade = grade_response(CASES["self_spec_structured_ops"], json.dumps(structured_spec))
    if not grade.ok:
        failures.append(f"self_spec_structured_ops fixture failed: {grade.as_dict()}")

    wrong_format = json.loads(json.dumps(structured_spec))
    _, grade = grade_response(CASES["self_spec_flat_ops"], json.dumps(wrong_format))
    if grade.ok or grade.checks.get("ops_format_flat", True):
        failures.append("flat self-spec accepted structured ops serialization")

    if failures:
        print(json.dumps({"ok": False, "failures": failures}, indent=2))
        return 1
    print(json.dumps({"ok": True, "checks": 7, "schema_version": SCHEMA}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
