#!/usr/bin/env python3
"""End-to-end ROM template smoke: compile once, bind many, execute in NanoJev.

This experiment tests two connected claims:
1. the current NanoJev training-task mix can be surfaced as one reusable ROM
   template per task family, and
2. a compiled template can be rebound to new external values and consumed by
   NanoJev without recompiling the decision procedure.

Three downstream arms are compared on the same value sets:
  baseline   ordinary natural-language question + current values
  flat       Gemma-compiled ROM with a flat string program + current bindings
  structured Gemma-compiled ROM with object instructions + current bindings

The compiler adapter is intentionally tolerant about serialization.  It records
format drift, but only rejects output when the decision interface/program cannot
be recovered well enough to bind runtime values safely.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Sequence


THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]
TOOLS_DIR = THIS_FILE.parent
for path in (REPO_ROOT, TOOLS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import nanojev_rom_compiler_ollama_smoke as romc  # noqa: E402
from main_computer import rag_gremlin_pyramid_atom_smoke as ollama_stream  # noqa: E402


SCHEMA = "main-computer-nanojev-rom-template-execution-smoke-v2"
DEFAULT_MODEL = os.environ.get("NANOJEV_ROM_COMPILER_MODEL", romc.DEFAULT_MODEL)
DEFAULT_OLLAMA_URL = os.environ.get("NANOJEV_ROM_COMPILER_URL", romc.DEFAULT_URL)
DEFAULT_NANOJEV_URL = os.environ.get("NANOJEV_ROM_SERVICE_URL", "http://127.0.0.1:9765/api/evaluate")
DEFAULT_OUT_ROOT = Path("diagnostics_output") / "nanojev_rom_template_execution_smoke"
DEFAULT_KEEP_ALIVE = "30m"
DEFAULT_NUM_PREDICT = 2200
DEFAULT_CATALOG_NUM_PREDICT = 9000
TRAINER_PATH = TOOLS_DIR / "nanojev_three_backbone_clef_sized_live_train.py"
DECISIONS = ("candidate_a", "candidate_b", "candidate_c", "none")

NATURAL_TEMPLATE_QUESTION = (
    "Three Python implementations are supplied as candidates A, B, and C. "
    "Which candidate has a different normalized Python AST from the other two? "
    "Return NONE if there is no unique outlier because all three have the same normalized AST."
)

PLUS_A = "def combine(left, right):\n    return left + right\n"
PLUS_B = "def combine( left , right ):\n    value = left + right\n    return value\n"
PLUS_C = "def combine(left,right):\n    return (left + right)\n"
MINUS = "def combine(left, right):\n    return left - right\n"

BINDING_CASES: tuple[dict[str, Any], ...] = (
    {"id": "outlier_c", "candidate_a": PLUS_A, "candidate_b": PLUS_B, "candidate_c": MINUS, "gold": "candidate_c"},
    {"id": "outlier_b", "candidate_a": PLUS_A, "candidate_b": MINUS, "candidate_c": PLUS_C, "gold": "candidate_b"},
    {"id": "outlier_a", "candidate_a": MINUS, "candidate_b": PLUS_B, "candidate_c": PLUS_C, "gold": "candidate_a"},
    {"id": "no_outlier", "candidate_a": PLUS_A, "candidate_b": PLUS_B, "candidate_c": PLUS_C, "gold": "none"},
)

# These are not hand-invented training labels.  They are compact natural-language
# renderings of the seven objective families actually wired into
# nanojev_three_backbone_clef_sized_live_train.py.  The smoke discovers the
# authoritative task order/weights/units from that trainer at runtime and refuses
# to silently omit a task that is not covered here.
TASK_FAMILY_CONTRACTS: dict[str, dict[str, Any]] = {
    "legacy": {
        "question": (
            "A code prefix and two candidate continuations are supplied. Select the candidate "
            "that is the exact true continuation of the prefix rather than a garbled continuation."
        ),
        "min_inputs": 3,
        "suggested_inputs": ["code_prefix", "continuation_a", "continuation_b"],
        "suggested_output": "selected_continuation",
    },
    "mutation": {
        "question": (
            "A reference Python program and a candidate Python program are supplied. Decide whether "
            "the candidate is behavior-preserving or behavior-changing relative to the reference."
        ),
        "min_inputs": 2,
        "suggested_inputs": ["reference_program", "candidate_program"],
        "suggested_output": "mutation_relation",
    },
    "ast": {
        "question": (
            "A reference Python program and a candidate Python program are supplied. Decide whether "
            "they have the same normalized Python AST or a different normalized Python AST."
        ),
        "min_inputs": 2,
        "suggested_inputs": ["reference_program", "candidate_program"],
        "suggested_output": "ast_relation",
    },
    "consensus": {
        "question": NATURAL_TEMPLATE_QUESTION,
        "min_inputs": 3,
        "suggested_inputs": ["candidate_a", "candidate_b", "candidate_c"],
        "suggested_output": "outlier",
    },
    "triad": {
        "question": (
            "Two Python programs are supplied. Decide whether they have the same normalized Python "
            "AST or different normalized Python ASTs."
        ),
        "min_inputs": 2,
        "suggested_inputs": ["left_program", "right_program"],
        "suggested_output": "relation",
    },
    "dictionary_definition": {
        "question": (
            "A headword, part of speech, and two candidate definitions are supplied. Select the "
            "definition that correctly matches the headword and is also consistent in the reverse "
            "definition-to-headword direction."
        ),
        "min_inputs": 4,
        "suggested_inputs": ["headword", "part_of_speech", "definition_a", "definition_b"],
        "suggested_output": "selected_definition",
    },
    "english_code": {
        "question": (
            "A text object and a requested classification kind are supplied. If the kind is english, "
            "decide whether the text is English; if the kind is code, decide whether the text is code. "
            "Return yes or no."
        ),
        "min_inputs": 2,
        "suggested_inputs": ["text", "classification_kind"],
        "suggested_output": "yes_or_no",
    },
}


CATALOG_SYSTEM_PROMPT = r"""You are the ROM Training-Mix Compiler for the Main Computer.

You receive a JSON description of ONE current NanoJev training-task family.
Compile exactly one reusable parameterized ROM template for that task.  Future
training examples will reuse that same template with different runtime bindings.

You are a compiler, not the decision maker.  Return JSON only, with no Markdown
fences or prose.  Do not answer any task instance.

TASK TEMPLATE INVARIANTS
- Preserve the exact input task name in the top-level "task" field.
- Use the supplied natural_question as the semantic source of truth.
- Treat suggested_inputs and suggested_output as strong interface hints, but you
  may choose clearer semantic port names when the meaning is preserved.
- Infer the smallest reusable external input interface for the task.
- Runtime values are not present during compilation.  Every inferred input must
  therefore remain UNBOUND unless the task description itself explicitly names
  an external source path.  Never invent a source path.
- Derived comparisons, relations, scores, or intermediate values belong in
  locals, not inputs.
- Leave decision null.  The compiled template must be reusable with new values.
- Keep the program explicit enough that later software can bind external values
  and supervise intermediate structure.

ROM LANGUAGE
Paired control operations:
  BEGIN <-> END
  OPEN <-> SEAL
  NEXT <-> DONE
  HOLD <-> COMMIT
  KNOWN <-> UNKNOWN
  SAME <-> DIFFERENT

Additional operations:
  BIND       connect an input port to a ROM object
  RELATE     derive/request a relation into a local
  EXPECT     express an allowed/expected state when useful
  OBJECTIVE  declare the decision objective
  RESOLVE    derive the output candidate
  VERIFY     check the candidate against task constraints

Use semantic operands.  Example consensus shape:
  BEGIN
  OPEN A
  BIND A <- candidate_a
  SEAL A
  NEXT
  OPEN B
  BIND B <- candidate_b
  SEAL B
  NEXT
  OPEN C
  BIND C <- candidate_c
  SEAL C
  DONE
  RELATE A B USING EQUIVALENCE -> ab_relation
  RELATE A C USING EQUIVALENCE -> ac_relation
  RELATE B C USING EQUIVALENCE -> bc_relation
  OBJECTIVE outlier_or_none
  RESOLVE outlier FROM ab_relation ac_relation bc_relation
  HOLD outlier
  VERIFY consensus_topology
  COMMIT outlier
  END

Do not fill relation locals with SAME/DIFFERENT unless those results are supplied
as facts.  The template must work after the caller substitutes different values.

INPUT PORT SHAPE
{
  "name": "semantic_snake_case_name",
  "type": "semantic_type",
  "role": "short semantic role",
  "required": true,
  "inferred_from": "short source phrase",
  "binding": {"status":"UNBOUND","source":null}
}

OUTPUT PORT SHAPE
{
  "name": "semantic_snake_case_name",
  "type": "semantic_type",
  "role": "decision",
  "allowed_values": null
}

TOP-LEVEL OUTPUT SCHEMA
{
  "task": "exact input task name",
  "rom_version": 2,
  "question_intent": "short generic description",
  "inputs": [...],
  "outputs": [...],
  "locals": [...],
  "program": [...],
  "unresolved": [],
  "decision": null
}

Return exactly one task template object.
"""


CATALOG_SERIALIZATION_SUFFIX: dict[str, str] = {
    "flat": r"""
SERIALIZATION REQUIREMENT: FLAT PROGRAM
For every template, program MUST be a JSON array of instruction strings only.
Examples:
  "BEGIN"
  "OPEN A"
  "BIND A <- candidate_a"
  "RELATE A B USING EQUIVALENCE -> ab_relation"
  "RESOLVE outlier FROM ab_relation ac_relation bc_relation"
Do not place JSON instruction objects inside program.
""",
    "structured": r"""
SERIALIZATION REQUIREMENT: STRUCTURED PROGRAM
For every template, program MUST be a JSON array of instruction objects only.
Every instruction object must contain "op" plus explicit operand fields when
needed.  Examples:
  {"op":"BEGIN"}
  {"op":"OPEN","object":"A"}
  {"op":"BIND","object":"A","input":"candidate_a"}
  {"op":"RELATE","left":"A","right":"B","using":"EQUIVALENCE","into":"ab_relation"}
  {"op":"RESOLVE","output":"outlier","from":["ab_relation","ac_relation","bc_relation"]}
Do not place bare instruction strings inside program.
""",
}

CATALOG_RECOMPOSER_SYSTEM_PROMPT = r"""You are a ROM question recomposer.
You receive only a compiled ROM decision template; you do not receive the
original question or any runtime values. Reconstruct the generic natural-language
decision question represented by the template.

Return JSON only:
{
  "question": "one generic natural-language question",
  "inputs": ["semantic input names"],
  "output": "semantic output name",
  "decision": null
}

Do not answer the decision. Do not invent runtime source paths or values. Preserve
the number and roles of the inputs and the outlier/NONE semantics when present.
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, _pretty(value) + "\n")


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _trainer_literals(trainer_path: Path) -> dict[str, Any]:
    """Read the trainer's literal curriculum constants without importing torch/model code."""
    tree = ast.parse(Path(trainer_path).read_text(encoding="utf-8"), filename=str(trainer_path))
    wanted = {
        "DEFAULT_TRAIN_QUESTIONS",
        "DEFAULT_DEV_QUESTIONS",
        "TASK_WEIGHTS",
        "TASK_UNITS",
    }
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value_node = node.value
        else:
            targets = [node.target.id] if isinstance(node.target, ast.Name) else []
            value_node = node.value
        for name in targets:
            if name in wanted and value_node is not None:
                values[name] = ast.literal_eval(value_node)
    missing = wanted - set(values)
    if missing:
        raise RuntimeError(f"trainer curriculum constants missing: {sorted(missing)}")
    return values


def curriculum_plan_from_mix(total_questions: int, weights: Mapping[str, float], units: Mapping[str, int]) -> dict[str, int]:
    """Mirror the trainer's exact native-unit-aware allocation algorithm."""
    tasks = tuple(weights)
    total_questions = int(total_questions)
    if total_questions <= 0:
        raise ValueError("population size must be positive")
    if total_questions < sum(int(units[task]) for task in tasks):
        raise ValueError("population is smaller than one native unit per task")
    total_weight = sum(float(weights[task]) for task in tasks)
    targets = {task: total_questions * float(weights[task]) / total_weight for task in tasks}
    counts = {task: int(units[task]) for task in tasks}
    while sum(counts.values()) < total_questions:
        used = sum(counts.values())
        candidates: list[tuple[float, str]] = []
        for task in tasks:
            unit = int(units[task])
            if used + unit > total_questions:
                continue
            proposed = dict(counts)
            proposed[task] += unit
            score = sum(
                ((proposed[name] - targets[name]) ** 2) / max(1.0, targets[name])
                for name in tasks
            )
            candidates.append((score, task))
        if not candidates:
            raise RuntimeError(f"cannot allocate exact population size {total_questions}: {counts}")
        _score, task = min(candidates, key=lambda row: (row[0], tasks.index(row[1])))
        counts[task] += int(units[task])
    return counts


def discover_training_mix(trainer_path: Path = TRAINER_PATH) -> dict[str, Any]:
    values = _trainer_literals(trainer_path)
    weights = {str(k): float(v) for k, v in dict(values["TASK_WEIGHTS"]).items()}
    units = {str(k): int(v) for k, v in dict(values["TASK_UNITS"]).items()}
    tasks = tuple(weights)
    if tuple(units) != tasks:
        raise RuntimeError(f"TASK_UNITS order/keys drifted: weights={tasks} units={tuple(units)}")
    missing_contracts = [task for task in tasks if task not in TASK_FAMILY_CONTRACTS]
    extra_contracts = [task for task in TASK_FAMILY_CONTRACTS if task not in tasks]
    if missing_contracts or extra_contracts:
        raise RuntimeError(
            "ROM task-family contract coverage drifted: "
            f"missing={missing_contracts} extra={extra_contracts}"
        )
    train_total = int(values["DEFAULT_TRAIN_QUESTIONS"])
    dev_total = int(values["DEFAULT_DEV_QUESTIONS"])
    train_plan = curriculum_plan_from_mix(train_total, weights, units)
    dev_plan = curriculum_plan_from_mix(dev_total, weights, units)
    total_weight = sum(weights.values())
    return {
        "source": str(Path(trainer_path)),
        "tasks": list(tasks),
        "task_weights": weights,
        "normalized_weight_fraction": {task: weights[task] / total_weight for task in tasks},
        "task_units": units,
        "default_train_questions": train_total,
        "default_dev_questions": dev_total,
        "train_plan": train_plan,
        "dev_plan": dev_plan,
        "task_contracts": {task: TASK_FAMILY_CONTRACTS[task] for task in tasks},
    }


def training_task_prompt(mix: Mapping[str, Any], task: str) -> str:
    contract = TASK_FAMILY_CONTRACTS[str(task)]
    return _pretty({
        "request": "Compile this NanoJev training task family into one reusable ROM template.",
        "compile_policy": "compile once; future examples rebind runtime values without recompiling",
        "task": task,
        "weight": mix["task_weights"][task],
        "native_unit": mix["task_units"][task],
        "default_train_count": mix["train_plan"][task],
        "default_dev_count": mix["dev_plan"][task],
        "natural_question": contract["question"],
        "suggested_inputs": contract["suggested_inputs"],
        "suggested_output": contract["suggested_output"],
    })


def training_mix_prompt(mix: Mapping[str, Any]) -> str:
    return _pretty({
        "request": "Compile the current NanoJev training task mix into reusable ROM templates.",
        "compile_policy": "one template per task family; future examples rebind values without recompiling",
        "tasks": [json.loads(training_task_prompt(mix, str(task))) for task in mix["tasks"]],
    })


def _single_task_template(parsed: Mapping[str, Any] | None, task: str) -> Mapping[str, Any] | None:
    """Recover one task template from the common shapes a local compiler emits.

    A one-task call should normally return the template directly.  We also accept
    a single `template`, a one-element `templates` list, or a `templates` mapping
    keyed by task so harmless wrapper drift does not invalidate the semantic result.
    """
    if not isinstance(parsed, Mapping):
        return None
    if str(parsed.get("task", "")).strip() == task and isinstance(parsed.get("program"), list):
        return parsed
    wrapped = parsed.get("template")
    if isinstance(wrapped, Mapping):
        row = dict(wrapped)
        row.setdefault("task", task)
        return row
    raw = parsed.get("templates")
    if isinstance(raw, Mapping):
        candidate = raw.get(task)
        if isinstance(candidate, Mapping):
            row = dict(candidate)
            row.setdefault("task", task)
            return row
    if isinstance(raw, list):
        matches = [item for item in raw if isinstance(item, Mapping) and str(item.get("task", "")).strip() == task]
        if len(matches) == 1:
            return matches[0]
        mappings = [item for item in raw if isinstance(item, Mapping)]
        if len(mappings) == 1:
            row = dict(mappings[0])
            row.setdefault("task", task)
            return row
    return None


def _catalog_templates(parsed: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(parsed, Mapping):
        return []
    raw = parsed.get("templates")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, Mapping)]
    if isinstance(raw, Mapping):
        out = []
        for task, item in raw.items():
            if isinstance(item, Mapping):
                row = dict(item)
                row.setdefault("task", str(task))
                out.append(row)
        return out
    return []


def recover_catalog_template(payload: Mapping[str, Any] | None, requested_style: str, *, min_inputs: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"usable": False, "reasons": ["template was not a JSON object"], "warnings": []}
    program = payload.get("program")
    style = observed_program_style(program)
    ops = program_ops(program)
    names = input_names(payload)
    outputs = output_names(payload)
    reasons: list[str] = []
    warnings: list[str] = []
    if not names:
        reasons.append("no external input ports were recoverable")
    elif len(names) < int(min_inputs):
        warnings.append(
            f"compiler collapsed the preferred {min_inputs}+ external ports into {len(names)} port(s); "
            "inspect whether a collection/object port still preserves rebinding"
        )
    if not outputs:
        reasons.append("no output port was recoverable")
    for required in ("BEGIN", "BIND", "RESOLVE", "COMMIT", "END"):
        if required not in ops:
            reasons.append(f"program omitted required semantic operation {required}")
    if "OBJECTIVE" not in ops:
        warnings.append("program omitted explicit OBJECTIVE; question_intent/output may still preserve it")
    if payload.get("decision", None) is not None:
        reasons.append("compiler executed the task instead of leaving decision null")
    invented_bound_sources: list[str] = []
    raw_inputs = payload.get("inputs", [])
    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            if not isinstance(item, Mapping):
                continue
            binding = item.get("binding")
            if not isinstance(binding, Mapping):
                continue
            if str(binding.get("status", "")).upper() == "BOUND":
                invented_bound_sources.append(
                    f"{item.get('name', '')}={binding.get('source')!r}"
                )
    if invented_bound_sources:
        reasons.append(
            "task-family prompt named no runtime source paths, but compiler created BOUND inputs: "
            + ", ".join(invented_bound_sources)
        )
    if style == "invalid":
        reasons.append("program serialization was not recoverable")
    elif style != requested_style:
        warnings.append(f"requested {requested_style} serialization but observed {style}")
    return {
        "usable": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "requested_style": requested_style,
        "observed_style": style,
        "style_compliant": style == requested_style,
        "input_names": names,
        "output_names": outputs,
        "program_ops": ops,
    }


def recover_catalog(parsed: Mapping[str, Any] | None, requested_style: str, mix: Mapping[str, Any]) -> dict[str, Any]:
    by_task: dict[str, Mapping[str, Any]] = {}
    duplicates: list[str] = []
    for template in _catalog_templates(parsed):
        task = str(template.get("task", "")).strip()
        if not task:
            continue
        if task in by_task:
            duplicates.append(task)
        by_task[task] = template
    expected = [str(task) for task in mix["tasks"]]
    missing = [task for task in expected if task not in by_task]
    unexpected = [task for task in by_task if task not in expected]
    task_results: dict[str, Any] = {}
    for task in expected:
        payload = by_task.get(task)
        contract = TASK_FAMILY_CONTRACTS[task]
        recovery = recover_catalog_template(payload, requested_style, min_inputs=int(contract["min_inputs"]))
        task_results[task] = {
            "task": task,
            "template": payload,
            "template_id": sha256_json(payload) if payload is not None else None,
            "recovery": recovery,
        }
    reasons: list[str] = []
    if missing:
        reasons.append(f"catalog omitted tasks: {missing}")
    if unexpected:
        reasons.append(f"catalog emitted unexpected tasks: {unexpected}")
    if duplicates:
        reasons.append(f"catalog emitted duplicate tasks: {sorted(set(duplicates))}")
    unusable = [task for task, item in task_results.items() if not item["recovery"]["usable"]]
    if unusable:
        reasons.append(f"unusable task templates: {unusable}")
    return {
        "usable": not reasons,
        "reasons": reasons,
        "requested_style": requested_style,
        "task_results": task_results,
        "missing_tasks": missing,
        "unexpected_tasks": unexpected,
        "duplicate_tasks": sorted(set(duplicates)),
    }


def compile_training_mix_catalog(*, style: str, args: argparse.Namespace, out_dir: Path,
                                 log: ollama_stream.Logger, mix: Mapping[str, Any]) -> dict[str, Any]:
    """Compile each task family independently, then assemble the catalog locally.

    This keeps the semantic compile-once invariant while avoiding a large seven-task
    generation whose outer JSON can be truncated and accidentally recovered as an
    inner object by the tolerant JSON extractor.
    """
    root_dir = out_dir / "training_mix_compiler" / style
    root_dir.mkdir(parents=True, exist_ok=True)
    system = CATALOG_SYSTEM_PROMPT + "\n" + CATALOG_SERIALIZATION_SUFFIX[style]
    _write_text(root_dir / "system_prompt.txt", system)
    _write_text(root_dir / "training_mix_prompt.json", training_mix_prompt(mix) + "\n")
    log.banner(f"ROM TRAINING MIX COMPILE: {style}")

    templates: list[Mapping[str, Any]] = []
    task_calls: dict[str, Any] = {}
    total_elapsed = 0.0
    total_chars = 0
    total_eval_count = 0
    all_done = True

    for task_value in mix["tasks"]:
        task = str(task_value)
        case_dir = root_dir / task
        case_dir.mkdir(parents=True, exist_ok=True)
        prompt = training_task_prompt(mix, task)
        request_payload = {
            "model": args.model,
            "system": system,
            "prompt": prompt,
            "stream": True,
            "keep_alive": args.keep_alive,
            "options": {"temperature": 0, "num_predict": args.catalog_num_predict},
        }
        _write_text(case_dir / "task_prompt.json", prompt + "\n")
        _write_json(case_dir / "request.json", request_payload)
        started = time.perf_counter()
        try:
            response, stream = ollama_stream.call_ollama_generate_streaming(
                payload=request_payload,
                url=args.ollama_url,
                timeout_s=args.timeout_s,
                log=log,
                raw_path=case_dir / "raw_stream.jsonl",
                stream_label=f"rom-training-mix-{style}-{task}",
            )
            error = None
        except Exception as exc:
            response, stream = "", {}
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - started
        total_elapsed += elapsed
        total_chars += len(response)
        total_eval_count += int(stream.get("eval_count") or 0) if isinstance(stream, Mapping) else 0
        if isinstance(stream, Mapping) and stream.get("done") is False:
            all_done = False
        _write_text(case_dir / "response.txt", response)
        parsed = romc.extract_json_object(response)
        if parsed is not None:
            _write_json(case_dir / "parsed.json", parsed)
        template = _single_task_template(parsed, task)
        recovery = recover_catalog_template(
            template, style, min_inputs=int(TASK_FAMILY_CONTRACTS[task]["min_inputs"])
        )
        if error:
            recovery["usable"] = False
            recovery.setdefault("reasons", []).insert(0, error)
        if template is None and not error:
            recovery["usable"] = False
            recovery.setdefault("reasons", []).insert(
                0, "compiler response did not contain a recoverable template for this task"
            )
        if template is not None:
            row = dict(template)
            row["task"] = task
            templates.append(row)
            _write_json(case_dir / "template.json", row)
        task_calls[task] = {
            "error": error,
            "elapsed_s": elapsed,
            "response_chars": len(response),
            "stream_summary": stream,
            "parsed": parsed,
            "template": template,
            "recovery": recovery,
        }
        _write_json(case_dir / "recovery.json", recovery)
        log(
            f"[training-mix:{style}:{task}] {'USABLE' if recovery['usable'] else 'UNUSABLE'} "
            f"elapsed_s={elapsed:.2f} eval_count={int(stream.get('eval_count') or 0) if isinstance(stream, Mapping) else 0}"
        )
        for reason in recovery.get("reasons", []):
            log(f"[training-mix:{style}:{task}] reason: {reason}")

    parsed_catalog = {"catalog_version": 1, "templates": templates}
    _write_json(root_dir / "parsed.json", parsed_catalog)
    recovery = recover_catalog(parsed_catalog, style, mix)
    result = {
        "style": style,
        "error": None,
        "elapsed_s": total_elapsed,
        "response_chars": total_chars,
        "stream_summary": {
            "eval_count": total_eval_count,
            "done": all_done,
            "task_count": len(task_calls),
        },
        "parsed": parsed_catalog,
        "recovery": recovery,
        "catalog_id": sha256_json(parsed_catalog),
        "task_calls": task_calls,
    }
    _write_json(root_dir / "recovery.json", recovery)
    log(
        f"[training-mix:{style}] {'USABLE' if recovery['usable'] else 'UNUSABLE'} "
        f"tasks={len(recovery['task_results'])} elapsed_s={total_elapsed:.2f}"
    )
    for reason in recovery.get("reasons", []):
        log(f"[training-mix:{style}] reason: {reason}")
    return result


def consensus_compile_result_from_catalog(catalog_result: Mapping[str, Any]) -> dict[str, Any]:
    style = str(catalog_result["style"])
    task_result = catalog_result.get("recovery", {}).get("task_results", {}).get("consensus", {})
    parsed = task_result.get("template") if isinstance(task_result, Mapping) else None
    recovery = recover_template(parsed, style)
    return {
        "style": style,
        "error": catalog_result.get("error"),
        "elapsed_s": catalog_result.get("elapsed_s", 0.0),
        "response_chars": catalog_result.get("response_chars", 0),
        "stream_summary": catalog_result.get("stream_summary", {}),
        "parsed": parsed,
        "recovery": recovery,
        "template_id": sha256_json(parsed) if isinstance(parsed, Mapping) else None,
    }


def write_training_mix_rom_artifacts(*, out_dir: Path, mix: Mapping[str, Any],
                                     catalog_results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    _write_json(out_dir / "training_mix.json", mix)
    jsonl_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for style in ("flat", "structured"):
        result = catalog_results[style]
        _write_json(out_dir / f"rom_catalog_{style}.json", result.get("parsed"))
        task_rows: dict[str, Any] = {}
        task_results = result.get("recovery", {}).get("task_results", {})
        for task in mix["tasks"]:
            item = task_results.get(task, {}) if isinstance(task_results, Mapping) else {}
            recovery = item.get("recovery", {}) if isinstance(item, Mapping) else {}
            template = item.get("template") if isinstance(item, Mapping) else None
            row = {
                "schema": "main-computer-rom-training-task-template-v1",
                "task": task,
                "serialization": style,
                "weight": mix["task_weights"][task],
                "native_unit": mix["task_units"][task],
                "default_train_count": mix["train_plan"][task],
                "default_dev_count": mix["dev_plan"][task],
                "template_id": item.get("template_id") if isinstance(item, Mapping) else None,
                "usable": bool(recovery.get("usable")),
                "style_compliant": bool(recovery.get("style_compliant")),
                "rom_template": template,
            }
            jsonl_rows.append(row)
            task_rows[task] = {
                "usable": row["usable"],
                "style_compliant": row["style_compliant"],
                "template_id": row["template_id"],
                "inputs": recovery.get("input_names", []),
                "outputs": recovery.get("output_names", []),
                "ops": recovery.get("program_ops", []),
                "reasons": recovery.get("reasons", []),
                "warnings": recovery.get("warnings", []),
            }
        summary[style] = {
            "usable": bool(result.get("recovery", {}).get("usable")),
            "catalog_id": result.get("catalog_id"),
            "elapsed_s": result.get("elapsed_s"),
            "eval_count": int(result.get("stream_summary", {}).get("eval_count") or 0),
            "response_chars": result.get("response_chars"),
            "tasks": task_rows,
        }
    templates_path = out_dir / "rom_templates.jsonl"
    _write_text(templates_path, "".join(_json(row) + "\n" for row in jsonl_rows))
    _write_json(out_dir / "rom_catalog_summary.json", summary)
    return {
        "training_mix": str(out_dir / "training_mix.json"),
        "flat_catalog": str(out_dir / "rom_catalog_flat.json"),
        "structured_catalog": str(out_dir / "rom_catalog_structured.json"),
        "templates_jsonl": str(templates_path),
        "summary": str(out_dir / "rom_catalog_summary.json"),
        "template_count": len(jsonl_rows),
    }


def observed_program_style(program: Any) -> str:
    if not isinstance(program, list) or not program:
        return "invalid"
    if all(isinstance(item, str) for item in program):
        return "flat"
    if all(isinstance(item, Mapping) for item in program):
        return "structured"
    return "mixed"


def instruction_op(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("op", "")).strip().upper()
    if isinstance(item, str):
        match = re.match(r"\s*([A-Za-z_]+)", item)
        return match.group(1).upper() if match else ""
    return ""


def program_ops(program: Any) -> list[str]:
    if not isinstance(program, list):
        return []
    return [op for op in (instruction_op(item) for item in program) if op]


def input_names(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("inputs", [])
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            name = str(item.get("name", "")).strip()
            if name:
                result.append(name)
    return result


def output_names(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("outputs", [])
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            name = str(item.get("name", "")).strip()
            if name:
                result.append(name)
    return result


def _binds_from_program(program: Any) -> dict[str, str]:
    """Return object label -> input port from either supported program shape."""
    result: dict[str, str] = {}
    if not isinstance(program, list):
        return result
    for item in program:
        if isinstance(item, Mapping) and instruction_op(item) == "BIND":
            obj = str(item.get("object", item.get("target", ""))).strip().upper()
            port = str(item.get("input", item.get("source", ""))).strip()
            if obj and port:
                result[obj] = port
        elif isinstance(item, str) and instruction_op(item) == "BIND":
            match = re.search(r"\bBIND\s+([A-Za-z0-9_]+)\s*<-\s*([A-Za-z0-9_.-]+)", item, flags=re.I)
            if match:
                result[match.group(1).upper()] = match.group(2)
    return result


def resolve_candidate_ports(payload: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Tolerantly connect canonical A/B/C runtime slots to inferred compiler ports.

    We prefer explicit BIND object labels because they are part of the compiled
    program.  Only then do we fall back to semantic names/suffixes or stable input
    order.  We never invent an external source path or alter the decision program.
    """
    names = input_names(payload)
    unresolved: list[str] = []
    mapping: dict[str, str] = {}
    binds = _binds_from_program(payload.get("program"))
    for letter in ("A", "B", "C"):
        port = binds.get(letter)
        if port in names:
            mapping[f"candidate_{letter.lower()}"] = port

    unused = [name for name in names if name not in mapping.values()]
    for letter in ("a", "b", "c"):
        slot = f"candidate_{letter}"
        if slot in mapping:
            continue
        patterns = (
            rf"(?:^|_){letter}$",
            rf"candidate[_-]?{letter}(?:$|_)",
            rf"report[_-]?{letter}(?:$|_)",
            rf"implementation[_-]?{letter}(?:$|_)",
        )
        matched = [name for name in unused if any(re.search(p, name, flags=re.I) for p in patterns)]
        if len(matched) == 1:
            mapping[slot] = matched[0]
            unused.remove(matched[0])

    missing = [slot for slot in ("candidate_a", "candidate_b", "candidate_c") if slot not in mapping]
    if missing and len(names) == 3 and len(unused) == len(missing):
        for slot, port in zip(missing, unused):
            mapping[slot] = port
        missing = []
    unresolved.extend(missing)
    return mapping, unresolved


def recover_template(payload: Mapping[str, Any] | None, requested_style: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {"usable": False, "reasons": ["compiler response did not contain a JSON object"]}
    program = payload.get("program")
    style = observed_program_style(program)
    ops = program_ops(program)
    port_map, unresolved = resolve_candidate_ports(payload)
    outputs = output_names(payload)
    reasons: list[str] = []
    if len(input_names(payload)) < 3:
        reasons.append("fewer than three external inputs were recoverable")
    if unresolved:
        reasons.append(f"could not map runtime slots: {unresolved}")
    for required in ("BEGIN", "BIND", "RELATE", "RESOLVE", "COMMIT", "END"):
        if required not in ops:
            reasons.append(f"program omitted required semantic operation {required}")
    if not outputs:
        reasons.append("no output port was recoverable")
    if style == "invalid":
        reasons.append("program serialization was not recoverable")
    return {
        "usable": not reasons,
        "reasons": reasons,
        "requested_style": requested_style,
        "observed_style": style,
        "style_compliant": style == requested_style,
        "input_names": input_names(payload),
        "output_names": outputs,
        "runtime_port_map": port_map,
        "program_ops": ops,
    }


def recompose_template(*, compile_result: Mapping[str, Any], args: argparse.Namespace, out_dir: Path, log: ollama_stream.Logger) -> dict[str, Any]:
    style = str(compile_result["style"])
    parsed = compile_result.get("parsed")
    case_dir = out_dir / "recomposition" / style
    case_dir.mkdir(parents=True, exist_ok=True)
    if not isinstance(parsed, Mapping):
        result = {"style": style, "ok": False, "error": "no compiled template to recompose"}
        _write_json(case_dir / "grade.json", result)
        return result
    prompt = _pretty(parsed)
    request_payload = {
        "model": args.model,
        "system": RECOMPOSER_SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": True,
        "keep_alive": args.keep_alive,
        "options": {"temperature": 0, "num_predict": min(args.num_predict, 800)},
    }
    _write_text(case_dir / "system_prompt.txt", RECOMPOSER_SYSTEM_PROMPT)
    _write_text(case_dir / "rom_template.json", prompt + "\n")
    started = time.perf_counter()
    try:
        response, stream = ollama_stream.call_ollama_generate_streaming(
            payload=request_payload,
            url=args.ollama_url,
            timeout_s=args.timeout_s,
            log=log,
            raw_path=case_dir / "raw_stream.jsonl",
            stream_label=f"rom-recompose-{style}",
        )
        error = None
    except Exception as exc:
        response, stream = "", {}
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - started
    parsed_response = romc.extract_json_object(response)
    _write_text(case_dir / "response.txt", response)
    if parsed_response is not None:
        _write_json(case_dir / "parsed.json", parsed_response)
    question = str(parsed_response.get("question", "")) if isinstance(parsed_response, Mapping) else ""
    qlow = question.lower()
    checks = {
        "json": isinstance(parsed_response, Mapping),
        "question": bool(question.strip()),
        "three_way": ("three" in qlow or all(token in qlow for token in (" a", " b", " c"))),
        "outlier_semantics": any(token in qlow for token in ("outlier", "different", "inconsistent")),
        "none_semantics": any(token in qlow for token in ("none", "no unique", "all three", "same behavior", "agree")),
        "decision_null": isinstance(parsed_response, Mapping) and parsed_response.get("decision") is None,
    }
    ok = error is None and all(checks.values())
    result = {
        "style": style,
        "ok": ok,
        "checks": checks,
        "question": question,
        "elapsed_s": elapsed,
        "stream_summary": stream,
        "error": error,
    }
    _write_json(case_dir / "grade.json", result)
    log(f"[recompose:{style}] {'PASS' if ok else 'FAIL'} elapsed_s={elapsed:.2f} question={question!r}")
    return result


def _runtime_bindings(compile_result: Mapping[str, Any], binding_case: Mapping[str, Any]) -> dict[str, str]:
    recovery = compile_result.get("recovery", {})
    port_map = recovery.get("runtime_port_map", {}) if isinstance(recovery, Mapping) else {}
    result: dict[str, str] = {}
    for slot in ("candidate_a", "candidate_b", "candidate_c"):
        port = str(port_map.get(slot, ""))
        if port:
            result[port] = str(binding_case[slot])
    return result


def render_baseline_state(binding_case: Mapping[str, Any]) -> str:
    return "\n\n".join((
        "NATURAL-LANGUAGE DECISION.",
        NATURAL_TEMPLATE_QUESTION,
        f"CANDIDATE A:\n```python\n{binding_case['candidate_a']}```",
        f"CANDIDATE B:\n```python\n{binding_case['candidate_b']}```",
        f"CANDIDATE C:\n```python\n{binding_case['candidate_c']}```",
    ))


def _port_manifest(parsed: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "inputs": parsed.get("inputs", []),
        "outputs": parsed.get("outputs", []),
        "locals": parsed.get("locals", []),
    }


def render_rom_state(compile_result: Mapping[str, Any], binding_case: Mapping[str, Any]) -> str:
    style = str(compile_result["style"])
    parsed = compile_result.get("parsed")
    if not isinstance(parsed, Mapping):
        raise RuntimeError(f"{style} ROM template is unavailable")
    runtime = _runtime_bindings(compile_result, binding_case)
    header = (
        "ROM DECISION TEMPLATE EXECUTION. The template is fixed; only RUNTIME_BINDINGS vary. "
        "Execute the ROM semantics and select the committed output."
    )
    if style == "flat":
        lines = [header, "INTERFACE:", _pretty(_port_manifest(parsed)), "PROGRAM:"]
        for item in parsed.get("program", []):
            if isinstance(item, str):
                lines.append(item)
            elif isinstance(item, Mapping):
                lines.append(_json(item))
        lines.extend(("RUNTIME_BINDINGS:", _pretty(runtime)))
        return "\n".join(lines)
    payload = {
        "rom_version": parsed.get("rom_version", romc.ROM_VERSION),
        "interface": _port_manifest(parsed),
        "program": parsed.get("program", []),
        "runtime_bindings": runtime,
    }
    return header + "\n" + _pretty(payload)


def build_nanojev_payload(compile_results: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    states: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    question = {
        "type": "choice",
        "instructions": "Select the decision produced by the supplied state. Choose exactly one output.",
        "criteria": {
            "candidate_a": "The committed decision selects candidate A.",
            "candidate_b": "The committed decision selects candidate B.",
            "candidate_c": "The committed decision selects candidate C.",
            "none": "The committed decision is NONE because there is no unique outlier.",
        },
    }
    for binding_case in BINDING_CASES:
        for arm in ("baseline", "flat", "structured"):
            if arm != "baseline" and not compile_results[arm]["recovery"]["usable"]:
                continue
            state_id = f"{arm}__{binding_case['id']}"
            state_text = render_baseline_state(binding_case) if arm == "baseline" else render_rom_state(compile_results[arm], binding_case)
            states.append({"id": state_id, "state": state_text, "questions": {"decision": question}})
            meta[state_id] = {"arm": arm, "binding_case": binding_case["id"], "gold": binding_case["gold"], "state_chars": len(state_text)}
    return {"states": states}, meta


def call_nanojev(url: str, payload: Mapping[str, Any], timeout_s: float) -> tuple[dict[str, Any], float]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"NanoJev HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"NanoJev request failed: {exc}") from exc
    elapsed = time.perf_counter() - started
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise RuntimeError("NanoJev response must be a JSON object")
    return decoded, elapsed


def extract_nanojev_rows(response: Mapping[str, Any], meta: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    for state in response.get("states", []):
        if isinstance(state, Mapping):
            by_id[str(state.get("id", ""))] = state
    rows: list[dict[str, Any]] = []
    for state_id, info in meta.items():
        state = by_id.get(state_id)
        if state is None:
            rows.append({**info, "state_id": state_id, "error": "missing NanoJev state", "correct": False})
            continue
        answers = state.get("answers", {})
        answer = answers.get("decision", {}) if isinstance(answers, Mapping) else {}
        probs_raw = answer.get("probabilities", {}) if isinstance(answer, Mapping) else {}
        probs = {key: float(probs_raw.get(key, 0.0) or 0.0) for key in DECISIONS} if isinstance(probs_raw, Mapping) else {key: 0.0 for key in DECISIONS}
        predicted = max(DECISIONS, key=lambda key: (probs[key], -DECISIONS.index(key)))
        gold = str(info["gold"])
        ordered = sorted(probs.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        rows.append({
            **info,
            "state_id": state_id,
            "probabilities": probs,
            "predicted": predicted,
            "gold_probability": probs[gold],
            "top_margin": margin,
            "correct": predicted == gold,
            "error": None,
        })
    return rows


def analyze_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in ("baseline", "flat", "structured"):
        subset = [row for row in rows if row.get("arm") == arm]
        if not subset:
            arms[arm] = {"available": False, "count": 0}
            continue
        arms[arm] = {
            "available": True,
            "count": len(subset),
            "correct": sum(1 for row in subset if row.get("correct")),
            "accuracy": sum(1 for row in subset if row.get("correct")) / len(subset),
            "mean_gold_probability": sum(float(row.get("gold_probability", 0.0)) for row in subset) / len(subset),
            "mean_top_margin": sum(float(row.get("top_margin", 0.0)) for row in subset) / len(subset),
            "mean_state_chars": sum(int(row.get("state_chars", 0)) for row in subset) / len(subset),
        }
    ranked = [arm for arm in ("baseline", "flat", "structured") if arms[arm].get("available")]
    ranked.sort(key=lambda arm: (-arms[arm]["accuracy"], -arms[arm]["mean_gold_probability"], arms[arm]["mean_state_chars"], arm))
    return {"arms": arms, "downstream_winner": ranked[0] if ranked else None, "ranking_rule": "accuracy, then mean gold probability, then shorter rendered state"}


def build_training_records(*, compile_results: Mapping[str, Mapping[str, Any]], recompositions: Mapping[str, Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    row_index = {(str(row.get("arm")), str(row.get("binding_case"))): row for row in rows}
    records: list[dict[str, Any]] = []
    for style in ("flat", "structured"):
        result = compile_results[style]
        if not result["recovery"]["usable"]:
            continue
        for binding_case in BINDING_CASES:
            row = row_index.get((style, str(binding_case["id"])), {})
            records.append({
                "schema": "main-computer-rom-training-example-v1",
                "template_id": result.get("template_id"),
                "serialization": style,
                "natural_question": NATURAL_TEMPLATE_QUESTION,
                "recomposed_question": recompositions.get(style, {}).get("question"),
                "rom_template": result.get("parsed"),
                "runtime_bindings": _runtime_bindings(result, binding_case),
                "gold_decision": binding_case["gold"],
                "nanojev_predicted": row.get("predicted"),
                "nanojev_probabilities": row.get("probabilities"),
            })
    return records


def _self_test() -> None:
    assert "ROM Training-Mix Compiler" in CATALOG_SYSTEM_PROMPT
    assert set(CATALOG_SERIALIZATION_SUFFIX) == {"flat", "structured"}
    assert "instruction strings only" in CATALOG_SERIALIZATION_SUFFIX["flat"]
    assert "instruction objects only" in CATALOG_SERIALIZATION_SUFFIX["structured"]
    mix = discover_training_mix()
    assert mix["tasks"] == [
        "legacy", "mutation", "ast", "consensus", "triad", "dictionary_definition", "english_code"
    ]
    assert mix["train_plan"] == {
        "legacy": 10, "mutation": 18, "ast": 38, "consensus": 28, "triad": 18,
        "dictionary_definition": 24, "english_code": 24,
    }
    assert mix["dev_plan"] == {
        "legacy": 3, "mutation": 6, "ast": 10, "consensus": 8, "triad": 6,
        "dictionary_definition": 7, "english_code": 8,
    }
    structured = {
        "rom_version": 2,
        "inputs": [{"name": "report_a"}, {"name": "report_b"}, {"name": "report_c"}],
        "outputs": [{"name": "outlier"}],
        "program": [
            {"op": "BEGIN"},
            {"op": "BIND", "object": "A", "input": "report_a"},
            {"op": "BIND", "object": "B", "input": "report_b"},
            {"op": "BIND", "object": "C", "input": "report_c"},
            {"op": "RELATE", "left": "A", "right": "B"},
            {"op": "RESOLVE", "output": "outlier"},
            {"op": "COMMIT", "output": "outlier"},
            {"op": "END"},
        ],
    }
    flat = {
        **{k: v for k, v in structured.items() if k != "program"},
        "program": ["BEGIN", "BIND A <- report_a", "BIND B <- report_b", "BIND C <- report_c", "RELATE A B", "RESOLVE outlier", "COMMIT outlier", "END"],
    }
    sr = recover_template(structured, "structured")
    fr = recover_template(flat, "flat")
    assert sr["usable"] and fr["usable"]
    assert sr["runtime_port_map"] == {"candidate_a": "report_a", "candidate_b": "report_b", "candidate_c": "report_c"}
    assert fr["runtime_port_map"] == sr["runtime_port_map"]
    assert observed_program_style(structured["program"]) == "structured"
    assert observed_program_style(flat["program"]) == "flat"
    flat_catalog_templates = []
    structured_catalog_templates = []
    for task in mix["tasks"]:
        contract = TASK_FAMILY_CONTRACTS[task]
        generic_inputs = [
            {"name": name, "binding": {"status": "UNBOUND", "source": None}}
            for name in contract["suggested_inputs"]
        ]
        flat_catalog_templates.append({
            "task": task, "rom_version": 2, "inputs": generic_inputs,
            "outputs": [{"name": contract["suggested_output"]}], "locals": [],
            "program": [
                "BEGIN",
                *[f"BIND X{i} <- {name}" for i, name in enumerate(contract["suggested_inputs"])],
                f"OBJECTIVE {task}", f"RESOLVE {contract['suggested_output']}",
                f"COMMIT {contract['suggested_output']}", "END",
            ],
            "unresolved": [], "decision": None,
        })
        structured_catalog_templates.append({
            "task": task, "rom_version": 2, "inputs": generic_inputs,
            "outputs": [{"name": contract["suggested_output"]}], "locals": [],
            "program": [
                {"op": "BEGIN"},
                *[{"op": "BIND", "object": f"X{i}", "input": name} for i, name in enumerate(contract["suggested_inputs"])],
                {"op": "OBJECTIVE", "name": task},
                {"op": "RESOLVE", "output": contract["suggested_output"]},
                {"op": "COMMIT", "output": contract["suggested_output"]},
                {"op": "END"},
            ],
            "unresolved": [], "decision": None,
        })
    flat_catalog = recover_catalog({"catalog_version": 1, "templates": flat_catalog_templates}, "flat", mix)
    structured_catalog = recover_catalog({"catalog_version": 1, "templates": structured_catalog_templates}, "structured", mix)
    assert flat_catalog["usable"] and structured_catalog["usable"]
    assert set(flat_catalog["task_results"]) == set(mix["tasks"])
    assert set(structured_catalog["task_results"]) == set(mix["tasks"])
    fake_compile = {
        "flat": {"style": "flat", "parsed": flat, "recovery": fr},
        "structured": {"style": "structured", "parsed": structured, "recovery": sr},
    }
    payload, meta = build_nanojev_payload(fake_compile)
    assert len(payload["states"]) == len(BINDING_CASES) * 3
    assert len(meta) == len(payload["states"])
    mock_states = []
    for state in payload["states"]:
        gold = meta[state["id"]]["gold"]
        probs = {key: (0.85 if key == gold else 0.05) for key in DECISIONS}
        mock_states.append({"id": state["id"], "answers": {"decision": {"probabilities": probs}}})
    rows = extract_nanojev_rows({"states": mock_states}, meta)
    analysis = analyze_rows(rows)
    assert all(analysis["arms"][arm]["accuracy"] == 1.0 for arm in ("baseline", "flat", "structured"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile one decision to ROM once, rebind it, and compare downstream NanoJev execution.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--nanojev-url", default=DEFAULT_NANOJEV_URL)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--run-id")
    parser.add_argument("--keep-alive", default=DEFAULT_KEEP_ALIVE)
    parser.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    parser.add_argument("--catalog-num-predict", type=int, default=DEFAULT_CATALOG_NUM_PREDICT)
    parser.add_argument("--timeout-s", type=int, default=0, help="Ollama helper logging value; streaming read timeout remains disabled.")
    parser.add_argument("--nanojev-timeout-s", type=float, default=300.0)
    parser.add_argument("--skip-recompose", action="store_true")
    parser.add_argument("--catalog-only", action="store_true", help="Compile and emit ROM for the current training mix, then stop before recomposition/NanoJev execution.")
    parser.add_argument("--dry-run", action="store_true", help="Build the deterministic fixture/payload shape without calling Ollama or NanoJev.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser


def _catalog_summary(catalog_results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for style in ("flat", "structured"):
        result = catalog_results[style]
        task_results = result.get("recovery", {}).get("task_results", {})
        out[style] = {
            "usable": bool(result.get("recovery", {}).get("usable")),
            "catalog_id": result.get("catalog_id"),
            "elapsed_s": result.get("elapsed_s"),
            "eval_count": int(result.get("stream_summary", {}).get("eval_count") or 0),
            "response_chars": result.get("response_chars"),
            "reasons": result.get("recovery", {}).get("reasons", []),
            "tasks": {
                task: {
                    "usable": bool(item.get("recovery", {}).get("usable")),
                    "style_compliant": bool(item.get("recovery", {}).get("style_compliant")),
                    "template_id": item.get("template_id"),
                    "inputs": item.get("recovery", {}).get("input_names", []),
                    "outputs": item.get("recovery", {}).get("output_names", []),
                    "ops": item.get("recovery", {}).get("program_ops", []),
                    "reasons": item.get("recovery", {}).get("reasons", []),
                }
                for task, item in task_results.items()
            },
        }
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        _self_test()
        print(_pretty({"ok": True, "schema": SCHEMA, "self_test": "passed"}))
        return 0

    mix = discover_training_mix()
    run_id = args.run_id or f"rom-template-{utc_stamp()}"
    out_dir = Path(args.out_root) / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    log = ollama_stream.Logger(out_dir / "run.log", quiet=args.quiet)
    _write_json(out_dir / "fixture.json", {
        "question": NATURAL_TEMPLATE_QUESTION,
        "binding_cases": BINDING_CASES,
        "training_mix": mix,
    })

    if args.dry_run:
        report = {
            "schema": SCHEMA,
            "dry_run": True,
            "training_mix": mix,
            "question": NATURAL_TEMPLATE_QUESTION,
            "binding_cases": BINDING_CASES,
            "arms": ["baseline", "flat", "structured"],
            "catalog_compile_count": {"flat": 1, "structured": 1},
            "catalog_tasks_per_style": len(mix["tasks"]),
            "runtime_instances_per_consensus_template": len(BINDING_CASES),
        }
        _write_json(out_dir / "report.json", report)
        print(_pretty(report))
        return 0

    catalog_results = {
        style: compile_training_mix_catalog(style=style, args=args, out_dir=out_dir, log=log, mix=mix)
        for style in ("flat", "structured")
    }
    rom_artifacts = write_training_mix_rom_artifacts(
        out_dir=out_dir, mix=mix, catalog_results=catalog_results
    )
    catalog_summary = _catalog_summary(catalog_results)

    log.banner("CURRENT NANOJEV TRAINING MIX -> ROM")
    for task in mix["tasks"]:
        log(
            f"task={task} weight={mix['task_weights'][task]:g} unit={mix['task_units'][task]} "
            f"train={mix['train_plan'][task]} dev={mix['dev_plan'][task]}"
        )
        for style in ("flat", "structured"):
            item = catalog_summary[style]["tasks"].get(task, {})
            log(
                f"  rom_{style}: usable={item.get('usable')} style_ok={item.get('style_compliant')} "
                f"template_id={item.get('template_id')} inputs={item.get('inputs')} "
                f"outputs={item.get('outputs')} ops={item.get('ops')}"
            )
    log(f"rom_templates={rom_artifacts['templates_jsonl']}")
    log(f"rom_catalog_flat={rom_artifacts['flat_catalog']}")
    log(f"rom_catalog_structured={rom_artifacts['structured_catalog']}")

    if args.catalog_only:
        catalog_ok = all(item["usable"] for item in catalog_summary.values())
        report = {
            "schema": SCHEMA,
            "ok": catalog_ok,
            "catalog_only": True,
            "training_mix": mix,
            "rom_catalog": catalog_summary,
            "rom_artifacts": rom_artifacts,
            "report": str(out_dir / "report.json"),
        }
        _write_json(out_dir / "report.json", report)
        print(_pretty(report))
        return 0 if catalog_ok else 1

    # Reuse the consensus ROM already produced as part of the full task catalog.
    # This preserves the compile-once invariant: there is no second consensus
    # compiler call merely for the NanoJev execution phase.
    compile_results = {
        style: consensus_compile_result_from_catalog(catalog_results[style])
        for style in ("flat", "structured")
    }
    recompositions: dict[str, Any] = {}
    if not args.skip_recompose:
        recompositions = {
            style: recompose_template(compile_result=compile_results[style], args=args, out_dir=out_dir, log=log)
            for style in ("flat", "structured")
        }

    payload, meta = build_nanojev_payload(compile_results)
    _write_json(out_dir / "nanojev_request.json", payload)
    log.banner("NANOJEV TEMPLATE EXECUTION")
    started = time.perf_counter()
    try:
        response, nanojev_elapsed = call_nanojev(args.nanojev_url, payload, args.nanojev_timeout_s)
        nanojev_error = None
    except Exception as exc:
        response, nanojev_elapsed = {}, time.perf_counter() - started
        nanojev_error = f"{type(exc).__name__}: {exc}"
    _write_json(out_dir / "nanojev_response.json", response)

    rows = extract_nanojev_rows(response, meta) if nanojev_error is None else [
        {**info, "state_id": state_id, "error": nanojev_error, "correct": False}
        for state_id, info in meta.items()
    ]
    analysis = analyze_rows(rows)
    training_records = build_training_records(
        compile_results=compile_results, recompositions=recompositions, rows=rows
    )
    training_path = out_dir / "training_examples.jsonl"
    _write_text(training_path, "".join(_json(record) + "\n" for record in training_records))

    compile_summary = {
        style: {
            "usable": result["recovery"]["usable"],
            "requested_style": style,
            "observed_style": result["recovery"].get("observed_style"),
            "style_compliant": result["recovery"].get("style_compliant"),
            "template_id": result.get("template_id"),
            "reasons": result["recovery"].get("reasons", []),
        }
        for style, result in compile_results.items()
    }
    expected_state_count = len(BINDING_CASES) * 3
    comparison_complete = (
        nanojev_error is None
        and all(item["usable"] for item in catalog_summary.values())
        and all(item["usable"] for item in compile_summary.values())
        and len(rows) == expected_state_count
        and all(row.get("error") is None for row in rows)
    )
    report = {
        "schema": SCHEMA,
        "ok": comparison_complete,
        "comparison_complete": comparison_complete,
        "training_mix": mix,
        "rom_catalog": catalog_summary,
        "rom_artifacts": rom_artifacts,
        "question": NATURAL_TEMPLATE_QUESTION,
        "compile_once_reuse_count": len(BINDING_CASES),
        "consensus_from_catalog": compile_summary,
        "recomposition": recompositions,
        "nanojev": {
            "url": args.nanojev_url,
            "elapsed_s": nanojev_elapsed,
            "error": nanojev_error,
            "rows": rows,
            "analysis": analysis,
        },
        "training_examples": {"count": len(training_records), "path": str(training_path)},
        "report": str(out_dir / "report.json"),
    }
    _write_json(out_dir / "report.json", report)

    log.banner("ROM TEMPLATE EXECUTION SUMMARY")
    for style in ("flat", "structured"):
        item = catalog_summary[style]
        log(
            f"catalog_{style}: usable={item['usable']} tasks={len(item['tasks'])} "
            f"eval_count={item['eval_count']} elapsed_s={item['elapsed_s']:.2f}"
        )
    for arm in ("baseline", "flat", "structured"):
        item = analysis["arms"][arm]
        if item.get("available"):
            log(
                f"nanojev_{arm}: accuracy={item['accuracy']:.3f} "
                f"gold_p={item['mean_gold_probability']:.3f} "
                f"mean_chars={item['mean_state_chars']:.0f}"
            )
        else:
            log(f"nanojev_{arm}: unavailable")
    log(f"downstream_winner={analysis['downstream_winner']}")
    log(f"compile_once_reused_for={len(BINDING_CASES)} consensus bindings")
    log(f"training_mix_rom_templates={rom_artifacts['templates_jsonl']}")
    log(f"training_examples={len(training_records)} path={training_path}")
    log(f"report={out_dir / 'report.json'}")

    summary = {
        "ok": report["ok"],
        "training_mix": {
            "tasks": mix["tasks"],
            "train_plan": mix["train_plan"],
            "dev_plan": mix["dev_plan"],
        },
        "rom_catalog": catalog_summary,
        "rom_artifacts": rom_artifacts,
        "recomposition": {
            style: {"ok": item.get("ok"), "question": item.get("question")}
            for style, item in recompositions.items()
        },
        "nanojev_analysis": analysis,
        "compile_once_reuse_count": len(BINDING_CASES),
        "training_examples": len(training_records),
        "report": report["report"],
    }
    print(_pretty(summary))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
