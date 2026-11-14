#!/usr/bin/env python3
"""Train the published NanoJev CLEF champion with fixed structured-ROM supervision.

This experiment deliberately avoids a question->ROM compiler.  The seven current
NanoJev task families use fixed, versioned ROM templates.  Existing objective
generators still provide fresh problem instances and gold labels, but every
problem is converted deterministically into a structured ROM prompt before any
frozen-backbone evidence is extracted.

Parent/cutover contract:
* resolve ``johnrraymond/NanoJev-CLEF@champion`` once through Hugging Face;
* pin the returned immutable commit in experiment.json;
* load the exact published head and TinyStories state;
* freeze Qwen3-0.6B, Pythia-70M, and TinyStories-33M;
* train only the service-compatible CLEF/residual head;
* baseline the published parent on a fixed held-out ROM selection bank;
* train and evaluate every reuse depth on that same ROM representation;
* by default choose winners by ROM selection accuracy;
* with ``--use-loss``, choose winners by strictly lower held-out ROM mean loss;
* with ``--memorize``, reuse cached frozen-backbone evidence for many head-only
  rounds, concentrate a bounded optimizer budget on high-loss examples, auto-backoff
  when loss improvement per step fades, and run exact/held-out checks sparsely.

The checkpoint/experiment schema intentionally remains compatible with the
existing ``nanojev_clef_publish.py`` release path.  The trainer variant and ROM
contracts are recorded explicitly in experiment.json and checkpoint metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import sys
import threading
import time
import traceback
from typing import Any, Mapping, Sequence

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent


def load_local_module(name: str, path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load_local_module(
    "nanojev_three_backbone_clef_rom_base",
    TOOLS / "nanojev_three_backbone_clef_sized_live_train.py",
)
champion = load_local_module(
    "nanojev_three_backbone_clef_rom_champion_architecture",
    TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
)
smoke = base.smoke

# Keep the current publisher-compatible checkpoint schema.  This is a file-layout
# compatibility statement; the distinct training policy is identified below.
SCHEMA = champion.SCHEMA
TRAINER_VARIANT = "three-frozen-backbone-rom-structured-supervision-v3"
ROM_SCHEMA = "nanojev-rom-structured-training-v1"
ROM_TEMPLATE_VERSION = 1
TRAINING_TASK_MIX_SCHEMA = "rom-easy-total10-mutation22.5-triad30-consensus37.5-v1"
DEFAULT_PARENT_REPO = "johnrraymond/NanoJev-CLEF"
# The NanoJev service itself defaults to this moving selector.  It is immediately
# resolved to a commit and never followed again during a run.
DEFAULT_PARENT_REVISION = "champion"
DEFAULT_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom_structured_supervision_train_v3"
)
DEFAULT_LOSS_OUTPUT = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom_structured_supervision_train_v3_loss"
)
DEFAULT_MEMORIZE_OUTPUT_ROOT = (
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom_structured_supervision_train_v5_hammer"
)
DEFAULT_LOSS_MEMORIZE_OUTPUT_ROOT = (
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_rom_structured_supervision_train_v5_loss_hammer"
)
DEFAULT_QUESTION_SOURCE_EXPERIMENT = base.DEFAULT_SOURCE_EXPERIMENT
DEFAULT_SEED = 20261009
DEFAULT_DATA_CYCLE_BASE = 996000
DEFAULT_TRAIN_QUESTIONS = 480
DEFAULT_SELECTION_QUESTIONS = 512
DEFAULT_DEV_QUESTIONS = 48
DEFAULT_MAX_CYCLES = 20
DEFAULT_EPOCHS_PER_CYCLE = 4
DEFAULT_GRAD_ACCUMULATION = 4
DEFAULT_HEAD_LR = champion.DEFAULT_CLEF_HEAD_LR
DEFAULT_WEIGHT_DECAY = 0.01
DEFAULT_GRAD_CLIP = 1.0
DEFAULT_KEEP_CHECKPOINTS = 3
DEFAULT_PATH_BATCH = smoke.DEFAULT_PATH_BATCH
DEFAULT_MAX_PROMPT_TOKENS = smoke.DEFAULT_MAX_PROMPT_TOKENS
DEFAULT_MAX_ANSWER_TOKENS = smoke.DEFAULT_MAX_ANSWER_TOKENS
DEFAULT_PROMPT_EVIDENCE_TOKENS = smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS
DEFAULT_ANSWER_EVIDENCE_TOKENS = smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS
DEFAULT_TINYSTORIES_PATH_BATCH = champion.DEFAULT_TINYSTORIES_PATH_BATCH
DEFAULT_MEMORIZE_MAX_EPOCHS = 32
DEFAULT_MEMORIZE_TARGET_ACCURACY = 0.98
DEFAULT_MEMORIZE_TARGET_LOSS = 0.10
DEFAULT_MEMORIZE_EVAL_EVERY = 6
DEFAULT_MEMORIZE_STALL_EPOCHS = 6
DEFAULT_MEMORIZE_MIN_LOSS_IMPROVEMENT = 1e-4
DEFAULT_HAMMER_BACKOFF_WINDOW = 2
DEFAULT_HAMMER_BACKOFF_THRESHOLD = 0.20
DEFAULT_HAMMER_BACKOFF_FACTOR = 0.65
DEFAULT_HAMMER_MIN_STRENGTH = 0.50

HAMMER_SCHEMES: dict[str, dict[str, float | int | bool]] = {
    "soft": {
        "strength": 1.5,
        "budget": 1.0,
        "refresh_fraction": 0.60,
        "selection_eval_every": 4,
    },
    "medium": {
        "strength": 3.0,
        "budget": 1.25,
        "refresh_fraction": 0.30,
        "selection_eval_every": 6,
    },
    "hard": {
        "strength": 5.0,
        "budget": 1.50,
        "refresh_fraction": 0.10,
        "selection_eval_every": 8,
    },
}

# Requested training distribution.  Selection/dev use the historical task mix,
# but every question is formatted through the same structured-ROM representation
# used for training.
TRAIN_TASK_PERCENT = {
    "legacy": 2.5,
    "mutation": 22.5,
    "ast": 2.5,
    "consensus": 37.5,
    "triad": 30.0,
    "dictionary_definition": 2.5,
    "english_code": 2.5,
}
EASY_TASKS = ("legacy", "ast", "dictionary_definition", "english_code")


def _op(op: str, **fields: Any) -> dict[str, Any]:
    return {"op": op, **fields}


ROM_TEMPLATES: dict[str, dict[str, Any]] = {
    "legacy": {
        "inputs": ("code_prefix", "continuation_a", "continuation_b"),
        "output": "selected_continuation",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="P"), _op("BIND", object="P", input="code_prefix"), _op("SEAL", object="P"),
            _op("OPEN", object="A"), _op("BIND", object="A", input="continuation_a"), _op("SEAL", object="A"),
            _op("OPEN", object="B"), _op("BIND", object="B", input="continuation_b"), _op("SEAL", object="B"),
            _op("RELATE", left="P", right="A", using="EXACT_CONTINUATION", into="continuation_a_relation"),
            _op("RELATE", left="P", right="B", using="EXACT_CONTINUATION", into="continuation_b_relation"),
            _op("OBJECTIVE", target="selected_continuation"),
            _op("RESOLVE", output="selected_continuation", from_=["continuation_a_relation", "continuation_b_relation"]),
            _op("HOLD", object="selected_continuation"),
            _op("VERIFY", constraint="EXACT_TRUE_CONTINUATION"),
            _op("COMMIT", object="selected_continuation"),
            _op("END"),
        ),
    },
    "mutation": {
        "inputs": ("reference_program", "candidate_program"),
        "output": "mutation_relation",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="REF"), _op("BIND", object="REF", input="reference_program"), _op("SEAL", object="REF"),
            _op("OPEN", object="CAND"), _op("BIND", object="CAND", input="candidate_program"), _op("SEAL", object="CAND"),
            _op("RELATE", left="REF", right="CAND", using="BEHAVIORAL_EQUIVALENCE", into="behavior_relation"),
            _op("OBJECTIVE", target="mutation_relation"),
            _op("RESOLVE", output="mutation_relation", from_=["behavior_relation"]),
            _op("HOLD", object="mutation_relation"),
            _op("VERIFY", constraint="PRESERVING_OR_CHANGING"),
            _op("COMMIT", object="mutation_relation"),
            _op("END"),
        ),
    },
    "ast": {
        "inputs": ("reference_program", "candidate_program"),
        "output": "ast_relation",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="REF"), _op("BIND", object="REF", input="reference_program"), _op("SEAL", object="REF"),
            _op("OPEN", object="CAND"), _op("BIND", object="CAND", input="candidate_program"), _op("SEAL", object="CAND"),
            _op("RELATE", left="REF", right="CAND", using="NORMALIZED_AST_EQUIVALENCE", into="ast_comparison"),
            _op("OBJECTIVE", target="ast_relation"),
            _op("RESOLVE", output="ast_relation", from_=["ast_comparison"]),
            _op("HOLD", object="ast_relation"),
            _op("VERIFY", constraint="SAME_OR_DIFFERENT"),
            _op("COMMIT", object="ast_relation"),
            _op("END"),
        ),
    },
    "consensus": {
        "inputs": ("candidate_a", "candidate_b", "candidate_c"),
        "output": "outlier",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="A"), _op("BIND", object="A", input="candidate_a"), _op("SEAL", object="A"),
            _op("OPEN", object="B"), _op("BIND", object="B", input="candidate_b"), _op("SEAL", object="B"),
            _op("OPEN", object="C"), _op("BIND", object="C", input="candidate_c"), _op("SEAL", object="C"),
            _op("RELATE", left="A", right="B", using="NORMALIZED_AST_EQUIVALENCE", into="ab_relation"),
            _op("RELATE", left="A", right="C", using="NORMALIZED_AST_EQUIVALENCE", into="ac_relation"),
            _op("RELATE", left="B", right="C", using="NORMALIZED_AST_EQUIVALENCE", into="bc_relation"),
            _op("OBJECTIVE", target="outlier"),
            _op("RESOLVE", output="outlier", from_=["ab_relation", "ac_relation", "bc_relation"]),
            _op("HOLD", object="outlier"),
            _op("VERIFY", constraint="UNIQUE_OUTLIER_OR_NONE"),
            _op("COMMIT", object="outlier"),
            _op("END"),
        ),
    },
    "triad": {
        "inputs": ("left_program", "right_program"),
        "output": "ast_equivalence_relation",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="A"), _op("BIND", object="A", input="left_program"), _op("SEAL", object="A"),
            _op("OPEN", object="B"), _op("BIND", object="B", input="right_program"), _op("SEAL", object="B"),
            _op("RELATE", left="A", right="B", using="NORMALIZED_AST_EQUIVALENCE", into="ast_comparison_result"),
            _op("OBJECTIVE", target="ast_equivalence_relation"),
            _op("RESOLVE", output="ast_equivalence_relation", from_=["ast_comparison_result"]),
            _op("HOLD", object="ast_equivalence_relation"),
            _op("VERIFY", constraint="SAME_OR_DIFFERENT"),
            _op("COMMIT", object="ast_equivalence_relation"),
            _op("END"),
        ),
    },
    "dictionary_definition": {
        "inputs": ("headword", "part_of_speech", "definition_a", "definition_b"),
        "output": "selected_definition",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="H"), _op("BIND", object="H", input="headword"), _op("SEAL", object="H"),
            _op("OPEN", object="POS"), _op("BIND", object="POS", input="part_of_speech"), _op("SEAL", object="POS"),
            _op("OPEN", object="A"), _op("BIND", object="A", input="definition_a"), _op("SEAL", object="A"),
            _op("OPEN", object="B"), _op("BIND", object="B", input="definition_b"), _op("SEAL", object="B"),
            _op("RELATE", left="H", right="A", using="DICTIONARY_FORWARD_REVERSE_CONSISTENCY", into="definition_a_relation"),
            _op("RELATE", left="H", right="B", using="DICTIONARY_FORWARD_REVERSE_CONSISTENCY", into="definition_b_relation"),
            _op("RELATE", left="POS", right="A", using="PART_OF_SPEECH_COMPATIBILITY", into="definition_a_pos"),
            _op("RELATE", left="POS", right="B", using="PART_OF_SPEECH_COMPATIBILITY", into="definition_b_pos"),
            _op("OBJECTIVE", target="selected_definition"),
            _op("RESOLVE", output="selected_definition", from_=["definition_a_relation", "definition_b_relation", "definition_a_pos", "definition_b_pos"]),
            _op("HOLD", object="selected_definition"),
            _op("VERIFY", constraint="FORWARD_AND_REVERSE_MATCH"),
            _op("COMMIT", object="selected_definition"),
            _op("END"),
        ),
    },
    "english_code": {
        "inputs": ("text", "classification_kind"),
        "output": "yes_or_no",
        "program": (
            _op("BEGIN"),
            _op("OPEN", object="TEXT"), _op("BIND", object="TEXT", input="text"), _op("SEAL", object="TEXT"),
            _op("OPEN", object="KIND"), _op("BIND", object="KIND", input="classification_kind"), _op("SEAL", object="KIND"),
            _op("RELATE", left="TEXT", right="KIND", using="CLASSIFICATION_MATCH", into="classification_match"),
            _op("OBJECTIVE", target="yes_or_no"),
            _op("RESOLVE", output="yes_or_no", from_=["classification_match"]),
            _op("HOLD", object="yes_or_no"),
            _op("VERIFY", constraint="YES_OR_NO"),
            _op("COMMIT", object="yes_or_no"),
            _op("END"),
        ),
    },
}

# JSON cannot contain a key named ``from_`` in the intended wire form.  Keep the
# Python literal readable above, then normalize once at module load.
for _template in ROM_TEMPLATES.values():
    normalized = []
    for _instruction in _template["program"]:
        row = dict(_instruction)
        if "from_" in row:
            row["from"] = row.pop("from_")
        normalized.append(row)
    _template["program"] = tuple(normalized)


CONSOLE_QUIET_EVENTS = frozenset({
    "clef_sized_live_evidence",
    "clef_rom_eval_question",
    "clef_rom_training_evidence_cached",
    "clef_rom_optimizer_step",
})


class EventLog:
    def __init__(self, output_dir: Path, *, heartbeat_seconds: float = 35.0):
        self.output_dir = Path(output_dir)
        self.events = self.output_dir / "events.jsonl"
        self.progress = self.output_dir / "progress.json"
        self.stage = "starting"
        self.stage_fields: dict[str, Any] = {}
        self.heartbeat_seconds = float(heartbeat_seconds)
        self._last_console_emit_monotonic = time.monotonic()
        self._quiet_progress: dict[str, Any] = {}
        self._write_lock = threading.Lock()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @staticmethod
    def _compact_progress(event: str, fields: dict[str, Any]) -> dict[str, Any]:
        keep = (
            "phase", "cycle", "epoch", "index", "total", "task",
            "global_step", "optimizer_step_in_epoch", "correct",
        )
        row = {"event": event}
        for key in keep:
            if key in fields:
                row[key] = fields[key]
        return row

    def emit(self, event: str, **fields: Any) -> None:
        row = {"event": event, **fields}
        encoded = json.dumps(row, sort_keys=True)
        console_quiet = event in CONSOLE_QUIET_EVENTS
        with self._write_lock:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            with self.events.open("a", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
                handle.flush()
            if console_quiet:
                compact = self._compact_progress(event, fields)
                if len(compact) > 1:
                    self._quiet_progress = compact
            else:
                print(encoded, flush=True)
                self._last_console_emit_monotonic = time.monotonic()

    def set_stage(self, stage: str, **fields: Any) -> None:
        self.stage = stage
        self.stage_fields = dict(fields)
        payload = {"stage": stage, "updated_unix": time.time(), **fields}
        smoke.atomic_json(self.progress, payload)
        self.emit("clef_rom_train_stage", **payload)

    def start_heartbeat(self) -> None:
        if self.heartbeat_seconds <= 0 or self._heartbeat_thread is not None:
            return
        self._heartbeat_stop.clear()

        def worker() -> None:
            poll = max(0.05, min(1.0, self.heartbeat_seconds / 4.0))
            while not self._heartbeat_stop.wait(poll):
                silent_seconds = time.monotonic() - self._last_console_emit_monotonic
                if silent_seconds < self.heartbeat_seconds:
                    continue
                heartbeat_fields = {
                    "stage": self.stage,
                    "silent_seconds": round(float(silent_seconds), 1),
                    "updated_unix": time.time(),
                    **self.stage_fields,
                }
                if self._quiet_progress:
                    heartbeat_fields["progress"] = dict(self._quiet_progress)
                self.emit("clef_rom_train_heartbeat", **heartbeat_fields)

        self._heartbeat_thread = threading.Thread(
            target=worker, name="clef-rom-heartbeat", daemon=True
        )
        self._heartbeat_thread.start()

    def stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(1.0, min(2.0, self.heartbeat_seconds)))
        self._heartbeat_thread = None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rom_template_id(task: str) -> str:
    template = ROM_TEMPLATES[task]
    payload = {
        "version": ROM_TEMPLATE_VERSION,
        "task": task,
        "inputs": list(template["inputs"]),
        "output": template["output"],
        "program": list(template["program"]),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_rom_templates() -> None:
    if tuple(ROM_TEMPLATES) != tuple(base.TASKS):
        raise RuntimeError(
            f"ROM task order drifted: expected={base.TASKS} observed={tuple(ROM_TEMPLATES)}"
        )
    required = {"BEGIN", "BIND", "OBJECTIVE", "RESOLVE", "COMMIT", "END"}
    for task, template in ROM_TEMPLATES.items():
        inputs = tuple(template.get("inputs") or ())
        if len(inputs) < 2 or len(set(inputs)) != len(inputs):
            raise RuntimeError(f"invalid ROM inputs for {task}: {inputs}")
        if not str(template.get("output") or ""):
            raise RuntimeError(f"ROM output missing for {task}")
        program = tuple(template.get("program") or ())
        if not program or any(not isinstance(row, dict) for row in program):
            raise RuntimeError(f"ROM program malformed for {task}")
        ops = [str(row.get("op") or "") for row in program]
        missing = sorted(required - set(ops))
        if missing:
            raise RuntimeError(f"ROM program {task} missing required ops: {missing}")
        if ops[0] != "BEGIN" or ops[-1] != "END":
            raise RuntimeError(f"ROM program {task} must be BEGIN...END: {ops}")


def training_curriculum_plan(total_questions: int) -> dict[str, int]:
    """Allocate the requested 10/15/10/25/20/10/10 training distribution."""
    total_questions = int(total_questions)
    if total_questions <= 0:
        raise ValueError("training population must be positive")
    if abs(sum(TRAIN_TASK_PERCENT.values()) - 100.0) > 1e-9:
        raise RuntimeError(f"training percentages no longer sum to 100: {TRAIN_TASK_PERCENT}")
    minimum = sum(int(base.TASK_UNITS[task]) for task in base.TASKS)
    if total_questions < minimum:
        raise ValueError(f"training population too small: minimum={minimum}")

    targets = {
        task: total_questions * float(TRAIN_TASK_PERCENT[task]) / 100.0
        for task in base.TASKS
    }
    counts = {task: int(base.TASK_UNITS[task]) for task in base.TASKS}
    while sum(counts.values()) < total_questions:
        used = sum(counts.values())
        choices: list[tuple[float, int, str]] = []
        for index, task in enumerate(base.TASKS):
            unit = int(base.TASK_UNITS[task])
            if used + unit > total_questions:
                continue
            proposed = dict(counts)
            proposed[task] += unit
            score = sum(
                ((float(proposed[name]) - targets[name]) ** 2) / max(1.0, targets[name])
                for name in base.TASKS
            )
            choices.append((score, index, task))
        if not choices:
            raise RuntimeError(f"cannot allocate exact ROM training population {total_questions}: {counts}")
        _score, _index, task = min(choices)
        counts[task] += int(base.TASK_UNITS[task])
    base.validate_plan(counts, expected_total=total_questions)
    return counts


def verify_public_release(root: Path) -> dict[str, Any]:
    """Verify the same four-file public release contract consumed by the service."""
    root = Path(root).resolve(strict=True)
    release = smoke.read_json(root / "release.json")
    manifest = smoke.read_json(root / "SHA256_MANIFEST.json")
    if release.get("schema_version") != "main-computer-nanojev-clef-release-v1":
        raise RuntimeError(f"unsupported NanoJev CLEF release schema: {release.get('schema_version')!r}")
    if manifest.get("schema_version") != "main-computer-nanojev-clef-sha256-manifest-v1":
        raise RuntimeError(f"unsupported NanoJev CLEF manifest schema: {manifest.get('schema_version')!r}")
    if manifest.get("release_name") != release.get("release_name"):
        raise RuntimeError("release.json and SHA256_MANIFEST.json disagree on release_name")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("NanoJev CLEF manifest has no files map")
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        row = files.get(filename)
        path = root / filename
        if not isinstance(row, dict) or not path.is_file():
            raise RuntimeError(f"public CLEF release missing manifest-backed {filename}")
        expected = str(row.get("sha256") or "")
        observed = sha256_file(path)
        if observed != expected:
            raise RuntimeError(
                f"public CLEF SHA256 mismatch {filename}: expected={expected} observed={observed}"
            )
    return release


def resolve_parent_release(
    repo_id: str,
    revision: str,
    *,
    api: Any | None = None,
    snapshot_download_fn: Any | None = None,
) -> dict[str, Any]:
    """Resolve a moving HF selector once, then download/verify that immutable commit."""
    if api is None or snapshot_download_fn is None:
        from huggingface_hub import HfApi, snapshot_download
        api = HfApi() if api is None else api
        snapshot_download_fn = snapshot_download if snapshot_download_fn is None else snapshot_download_fn
    info = api.model_info(repo_id=repo_id, revision=revision)
    commit = str(getattr(info, "sha", "") or "").strip()
    if not commit:
        raise RuntimeError(f"Hugging Face did not resolve {repo_id}@{revision} to a commit")
    snapshot = Path(snapshot_download_fn(
        repo_id=repo_id,
        revision=commit,
        allow_patterns=[
            "head.safetensors",
            "tinystories.safetensors",
            "release.json",
            "SHA256_MANIFEST.json",
        ],
    )).resolve(strict=True)
    release = verify_public_release(snapshot)
    return {
        "repo_id": str(repo_id),
        "requested_revision": str(revision),
        "resolved_revision": commit,
        "snapshot": str(snapshot),
        "release_name": str(release.get("release_name") or ""),
        "manifest_sha256": sha256_file(snapshot / "SHA256_MANIFEST.json"),
        "release": release,
    }


_PROGRAM_PAIR_RE = re.compile(
    r"Program A:\n```python\n(?P<a>.*?)\n```\nProgram B:\n```python\n(?P<b>.*?)\n```",
    re.DOTALL,
)


def _program_pair(prompt: str) -> tuple[str, str]:
    match = _PROGRAM_PAIR_RE.search(str(prompt))
    if match is None:
        raise RuntimeError("cannot recover Program A/B bindings from composed relational question")
    return match.group("a"), match.group("b")


def _dictionary_bindings(question) -> tuple[dict[str, str], list[str]]:
    if len(question.candidates) != 2:
        raise RuntimeError(f"dictionary question must have two candidates: {question.question_id}")
    forward = str(question.candidates[0].paths[0].prompt)
    match = re.search(
        r"^Dictionary entry\nHeadword: (?P<headword>.+)\nPart of speech: (?P<pos>.+)\nDefinition:$",
        forward,
    )
    if match is None:
        raise RuntimeError(f"cannot parse dictionary bindings: {question.question_id}")
    try:
        headword = str(json.loads(match.group("headword")))
    except Exception as exc:
        raise RuntimeError(f"dictionary headword is not JSON text: {question.question_id}") from exc
    values = [str(candidate.paths[0].answer).strip() for candidate in question.candidates]
    return {
        "headword": headword,
        "part_of_speech": match.group("pos").strip(),
        "definition_a": values[0],
        "definition_b": values[1],
    }, values


def _english_code_bindings(question) -> tuple[dict[str, str], list[str]]:
    prompt = str(question.candidates[0].paths[0].prompt)
    if prompt.startswith("Is this English?\n\n"):
        kind = "english"
        prefix = "Is this English?\n\n"
    elif prompt.startswith("Is this code?\n\n"):
        kind = "code"
        prefix = "Is this code?\n\n"
    else:
        raise RuntimeError(f"cannot parse english/code classification kind: {question.question_id}")
    suffix = "\n\nAnswer:"
    if not prompt.endswith(suffix):
        raise RuntimeError(f"cannot parse english/code text boundary: {question.question_id}")
    text = prompt[len(prefix):-len(suffix)]
    values = [str(candidate.paths[0].answer).strip().lower() for candidate in question.candidates]
    return {"text": text, "classification_kind": kind}, values


def _relational_bindings(question) -> tuple[dict[str, str], list[str]]:
    task = str(question.task)
    if task in {"mutation", "ast", "triad"}:
        left, right = _program_pair(question.candidates[0].paths[0].prompt)
        names = ROM_TEMPLATES[task]["inputs"]
        values = [str(candidate.paths[0].answer).strip().upper() for candidate in question.candidates]
        return {str(names[0]): left, str(names[1]): right}, values
    if task == "consensus":
        paths = tuple(question.candidates[0].paths)
        if len(paths) < 4:
            raise RuntimeError(f"consensus question lost pair geometry: {question.question_id}")
        a1, b = _program_pair(paths[0].prompt)
        a2, c = _program_pair(paths[2].prompt)
        if a1 != a2:
            raise RuntimeError(f"consensus A binding drifted across AB/AC pairs: {question.question_id}")
        label = {"a": "A", "b": "B", "c": "C", "none": "NONE"}
        try:
            values = [label[str(candidate.candidate_id).lower()] for candidate in question.candidates]
        except KeyError as exc:
            raise RuntimeError(f"unexpected consensus candidate id: {exc.args[0]}") from exc
        return {"candidate_a": a1, "candidate_b": b, "candidate_c": c}, values
    raise ValueError(task)


def _legacy_bindings(question) -> tuple[dict[str, str], list[str]]:
    if len(question.candidates) != 2:
        raise RuntimeError(f"legacy question must have two candidates: {question.question_id}")
    prefix = str(question.candidates[0].paths[0].prompt)
    if any(str(candidate.paths[0].prompt) != prefix for candidate in question.candidates):
        raise RuntimeError(f"legacy candidates disagree on prefix: {question.question_id}")
    values = [str(candidate.paths[0].answer) for candidate in question.candidates]
    return {
        "code_prefix": prefix,
        "continuation_a": values[0],
        "continuation_b": values[1],
    }, values


def rom_bindings_and_candidate_values(question) -> tuple[dict[str, Any], list[str]]:
    task = str(question.task)
    if task == "legacy":
        return _legacy_bindings(question)
    if task in {"mutation", "ast", "consensus", "triad"}:
        return _relational_bindings(question)
    if task == "dictionary_definition":
        return _dictionary_bindings(question)
    if task == "english_code":
        return _english_code_bindings(question)
    raise RuntimeError(f"no fixed ROM template for task={task!r}")


def rom_prompt(task: str, bindings: Mapping[str, Any]) -> str:
    template = ROM_TEMPLATES[task]
    expected = tuple(template["inputs"])
    if set(bindings) != set(expected):
        raise RuntimeError(
            f"ROM binding mismatch task={task}: expected={expected} observed={tuple(bindings)}"
        )
    payload = {
        "schema": ROM_SCHEMA,
        "template_version": ROM_TEMPLATE_VERSION,
        "template_id": rom_template_id(task),
        "task": task,
        "inputs": {name: bindings[name] for name in expected},
        "program": list(template["program"]),
        "output": template["output"],
        "decision": None,
    }
    return canonical_json(payload) + "\nROM_OUTPUT:"


def rom_candidate_answer(task: str, value: str) -> str:
    output = str(ROM_TEMPLATES[task]["output"])
    return " " + canonical_json({output: value})


def romify_question(question):
    task = str(question.task)
    bindings, candidate_values = rom_bindings_and_candidate_values(question)
    if len(candidate_values) != len(question.candidates):
        raise RuntimeError(f"ROM candidate value count mismatch: {question.question_id}")
    prompt = rom_prompt(task, bindings)
    candidates = []
    for candidate, value in zip(question.candidates, candidate_values):
        path_template = candidate.paths[0]
        path = path_template.__class__(prompt, rom_candidate_answer(task, value))
        candidates.append(candidate.__class__(str(candidate.candidate_id), (path,)))
    kwargs = {
        "question_id": str(question.question_id),
        "task": task,
        "candidates": tuple(candidates),
        "gold_index": int(question.gold_index),
    }
    if hasattr(question, "stratum"):
        kwargs["stratum"] = str(getattr(question, "stratum") or "")
    try:
        return question.__class__(**kwargs)
    except TypeError:
        kwargs.pop("stratum", None)
        return question.__class__(**kwargs)


def romify_population(questions: Sequence[Any]) -> list[Any]:
    return [romify_question(question) for question in questions]


def rom_population_manifest(questions: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": str(question.question_id),
            "task": str(question.task),
            "template_id": rom_template_id(str(question.task)),
            "gold_index": int(question.gold_index),
            "candidate_ids": [str(candidate.candidate_id) for candidate in question.candidates],
        }
        for question in questions
    ]


ROM_SELECTION_ACCURACY_POLICY = "rom-selection-accuracy-strict-improvement-v1"
ROM_SELECTION_LOSS_POLICY = "rom-selection-loss-strict-improvement-v1"
# Backward-compatible name for existing callers/tests that mean the default policy.
ROM_SELECTION_POLICY = ROM_SELECTION_ACCURACY_POLICY


def rom_selection_policy(*, use_loss: bool) -> str:
    return ROM_SELECTION_LOSS_POLICY if bool(use_loss) else ROM_SELECTION_ACCURACY_POLICY


def rom_accuracy_prefers_candidate(*, candidate_accuracy: float, incumbent_accuracy: float) -> bool:
    """Default ROM cutover winner rule: strict held-out accuracy improvement."""
    return float(candidate_accuracy) > float(incumbent_accuracy)


def rom_loss_prefers_candidate(*, candidate_loss: float, incumbent_loss: float) -> bool:
    """Loss-mode ROM cutover winner rule: strict held-out mean-loss reduction."""
    return float(candidate_loss) < float(incumbent_loss)


def choose_rom_accuracy_winner(*, incumbent_accuracy: float, attempts: Sequence[Mapping[str, Any]]):
    improving = [
        row for row in attempts
        if rom_accuracy_prefers_candidate(
            candidate_accuracy=float(row["predev"]["overall"]["accuracy"]),
            incumbent_accuracy=float(incumbent_accuracy),
        )
    ]
    if not improving:
        return None
    # Highest ROM accuracy wins.  On an exact tie, prefer the shallower reuse depth.
    return max(
        improving,
        key=lambda row: (
            float(row["predev"]["overall"]["accuracy"]),
            -int(row["reuse_depth"]),
        ),
    )


def choose_rom_loss_winner(*, incumbent_loss: float, attempts: Sequence[Mapping[str, Any]]):
    improving = [
        row for row in attempts
        if rom_loss_prefers_candidate(
            candidate_loss=float(row["predev"]["overall"]["mean_loss"]),
            incumbent_loss=float(incumbent_loss),
        )
    ]
    if not improving:
        return None
    # Lowest ROM loss wins.  On an exact tie, prefer the shallower reuse depth.
    return min(
        improving,
        key=lambda row: (
            float(row["predev"]["overall"]["mean_loss"]),
            int(row["reuse_depth"]),
        ),
    )


def _hammer_override_fields(args) -> dict[str, Any]:
    fields = {
        "strength": getattr(args, "hammer_strength", None),
        "budget": getattr(args, "hammer_budget", None),
        "refresh_fraction": getattr(args, "hammer_refresh_fraction", None),
        "selection_eval_every": getattr(args, "memorize_eval_every", None),
        "backoff_window": getattr(args, "hammer_backoff_window", None),
        "backoff_threshold": getattr(args, "hammer_backoff_threshold", None),
        "backoff_factor": getattr(args, "hammer_backoff_factor", None),
        "min_strength": getattr(args, "hammer_min_strength", None),
        "auto_backoff": getattr(args, "hammer_auto_backoff", None),
    }
    return {key: value for key, value in fields.items() if value is not None}


def resolve_hammer_config(args) -> dict[str, Any]:
    """Resolve preset + explicit overrides into one reproducible hammer contract."""
    scheme = str(getattr(args, "training_scheme", "medium") or "medium")
    overrides = _hammer_override_fields(args)
    if scheme == "custom":
        required = ("strength", "budget", "refresh_fraction")
        missing = [key for key in required if key not in overrides]
        if missing:
            raise ValueError(
                "--training-scheme custom requires explicit "
                + ", ".join("--hammer-" + key.replace("_", "-") for key in missing)
            )
        resolved = {
            "strength": float(overrides["strength"]),
            "budget": float(overrides["budget"]),
            "refresh_fraction": float(overrides["refresh_fraction"]),
            "selection_eval_every": int(overrides.get("selection_eval_every", DEFAULT_MEMORIZE_EVAL_EVERY)),
        }
    else:
        if scheme not in HAMMER_SCHEMES:
            raise ValueError(f"unknown training scheme: {scheme!r}")
        resolved = dict(HAMMER_SCHEMES[scheme])
        for key in ("strength", "budget", "refresh_fraction", "selection_eval_every"):
            if key in overrides:
                resolved[key] = overrides[key]

    resolved.update({
        "backoff_window": int(overrides.get("backoff_window", DEFAULT_HAMMER_BACKOFF_WINDOW)),
        "backoff_threshold": float(overrides.get("backoff_threshold", DEFAULT_HAMMER_BACKOFF_THRESHOLD)),
        "backoff_factor": float(overrides.get("backoff_factor", DEFAULT_HAMMER_BACKOFF_FACTOR)),
        "min_strength": float(overrides.get("min_strength", DEFAULT_HAMMER_MIN_STRENGTH)),
        "auto_backoff": bool(overrides.get("auto_backoff", True)),
    })
    resolved["strength"] = float(resolved["strength"])
    resolved["budget"] = float(resolved["budget"])
    resolved["refresh_fraction"] = float(resolved["refresh_fraction"])
    resolved["selection_eval_every"] = int(resolved["selection_eval_every"])
    resolved["requested_scheme"] = scheme
    resolved["resolved_scheme"] = (
        scheme if not overrides else ("custom" if scheme == "custom" else f"{scheme}+overrides")
    )
    resolved["overrides"] = dict(overrides)
    return resolved


def validate_hammer_config(config: Mapping[str, Any]) -> None:
    if float(config["strength"]) < 0.0:
        raise ValueError("--hammer-strength must be >= 0")
    if float(config["budget"]) < 1.0:
        raise ValueError("--hammer-budget must be >= 1.0 because every round must cover the full training bank")
    if not 0.0 <= float(config["refresh_fraction"]) <= 1.0:
        raise ValueError("--hammer-refresh-fraction must be between 0 and 1")
    if int(config["selection_eval_every"]) <= 0:
        raise ValueError("--memorize-eval-every must be positive")
    if int(config["backoff_window"]) <= 0:
        raise ValueError("--hammer-backoff-window must be positive")
    if not 0.0 <= float(config["backoff_threshold"]) <= 1.0:
        raise ValueError("--hammer-backoff-threshold must be between 0 and 1")
    if not 0.0 < float(config["backoff_factor"]) < 1.0:
        raise ValueError("--hammer-backoff-factor must be between 0 and 1 (exclusive)")
    if float(config["min_strength"]) < 0.0:
        raise ValueError("--hammer-min-strength must be >= 0")
    if float(config["min_strength"]) > float(config["strength"]):
        raise ValueError("--hammer-min-strength cannot exceed --hammer-strength")


def default_memorize_output(*, use_loss: bool, scheme: str) -> Path:
    root = DEFAULT_LOSS_MEMORIZE_OUTPUT_ROOT if use_loss else DEFAULT_MEMORIZE_OUTPUT_ROOT
    return Path(f"{root}_{scheme}")


def memorization_contract(args) -> dict[str, Any]:
    hammer = dict(getattr(args, "hammer_config", None) or resolve_hammer_config(args))
    return {
        "enabled": bool(args.memorize),
        "max_epochs": int(args.memorize_max_epochs),
        "target_accuracy": float(args.memorize_target_accuracy),
        "target_loss": float(args.memorize_target_loss),
        "selection_eval_every": int(hammer["selection_eval_every"]),
        "stall_measurements": int(args.memorize_stall_epochs),
        "min_loss_improvement": float(args.memorize_min_loss_improvement),
        "training_evidence": "cached-frozen-backbone-evidence",
        "train_measurement_policy": "mandatory-full-bank-first-exposure-v1; no-second-train-eval",
        "hard_replay_policy": "full-bank-plus-additive-weighted-replay-v1",
        "hammer": hammer,
    }


def memorization_stop_reason(
    *, epoch: int, accuracy: float, mean_loss: float, stalled_measurements: int, args,
) -> str | None:
    if not bool(getattr(args, "use_loss", False)) and float(accuracy) >= float(args.memorize_target_accuracy):
        return "target_accuracy"
    if float(mean_loss) <= float(args.memorize_target_loss):
        return "target_loss"
    if int(stalled_measurements) >= int(args.memorize_stall_epochs):
        return "stalled"
    if int(epoch) >= int(args.memorize_max_epochs):
        return "max_epochs"
    return None


def initial_hammer_state(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "active_strength": float(config["strength"]),
        "active_budget": float(config["budget"]),
        "active_refresh_fraction": float(config["refresh_fraction"]),
        "peak_effectiveness": 0.0,
        "low_effectiveness_rounds": 0,
        "backoff_count": 0,
        "last_action": "INIT",
    }


def _stable_hardness_weights(losses: Mapping[int, float], *, count: int, strength: float) -> list[float]:
    if count <= 0:
        return []
    safe = [max(1e-8, float(losses.get(index, 1.0))) for index in range(count)]
    if float(strength) <= 0.0:
        return [1.0] * count
    logs = [float(strength) * math.log(value) for value in safe]
    top = max(logs)
    weights = [math.exp(value - top) for value in logs]
    total = sum(weights)
    if not math.isfinite(total) or total <= 0.0:
        return [1.0] * count
    return weights


def _weighted_full_bank_order(
    *, count: int, losses: Mapping[int, float], strength: float, rng: random.Random,
) -> list[int]:
    """Return every example exactly once, with harder examples biased earlier."""
    if count <= 0:
        return []
    if not losses or float(strength) <= 0.0:
        order = list(range(count))
        rng.shuffle(order)
        return order
    weights = _stable_hardness_weights(losses, count=count, strength=float(strength))
    keyed = []
    for index, weight in enumerate(weights):
        # Exponential race: larger weights tend to appear earlier, without replacement.
        u = max(rng.random(), 1e-12)
        keyed.append((-math.log(u) / max(float(weight), 1e-12), index))
    keyed.sort(key=lambda item: item[0])
    return [index for _key, index in keyed]


def hammer_training_indices(
    *, count: int, previous_losses: Mapping[int, float], state: Mapping[str, Any],
    seed: int, cycle: int, epoch: int,
) -> list[int]:
    """Full-bank coverage first; any hammer replay is strictly additive."""
    if count <= 0:
        return []
    rng = random.Random(base.stable_seed(seed, cycle, epoch, "rom-hammer-order"))
    base_order = _weighted_full_bank_order(
        count=count, losses=previous_losses,
        strength=float(state["active_strength"]), rng=rng,
    )
    # Round one establishes hardness for every example; replay before that would
    # be untargeted work and would only slow the experiment down.
    if not previous_losses:
        return base_order
    total = max(count, int(math.ceil(count * float(state["active_budget"]))))
    replay_count = max(0, total - count)
    if replay_count == 0:
        return base_order

    refresh_count = min(
        replay_count, int(round(replay_count * float(state["active_refresh_fraction"])))
    )
    hard_count = replay_count - refresh_count
    weights = _stable_hardness_weights(
        previous_losses, count=count, strength=float(state["active_strength"]),
    )
    hard = rng.choices(range(count), weights=weights, k=hard_count) if hard_count else []
    refresh = [rng.randrange(count) for _ in range(refresh_count)]
    replay = hard + refresh
    rng.shuffle(replay)
    return base_order + replay


def hammer_global_effectiveness(
    previous_global_loss: float | None, current_global_loss: float, *, optimizer_steps: int,
) -> dict[str, Any] | None:
    """Comparable backoff signal from mandatory full-bank first exposures."""
    if previous_global_loss is None or optimizer_steps <= 0:
        return None
    loss_drop = float(previous_global_loss) - float(current_global_loss)
    return {
        "previous_global_loss": float(previous_global_loss),
        "current_global_loss": float(current_global_loss),
        "loss_drop": loss_drop,
        "loss_drop_per_100_steps": loss_drop * 100.0 / float(optimizer_steps),
    }


def update_hammer_state(
    state: dict[str, Any], *, effectiveness: Mapping[str, Any] | None, config: Mapping[str, Any],
) -> str:
    if effectiveness is None:
        state["last_action"] = "HOLD_NO_BASELINE"
        return str(state["last_action"])

    score = float(effectiveness["loss_drop_per_100_steps"])
    peak = max(float(state.get("peak_effectiveness", 0.0)), max(0.0, score))
    state["peak_effectiveness"] = peak
    threshold = peak * float(config["backoff_threshold"])
    low = score <= 0.0 or (peak > 0.0 and score < threshold)
    state["low_effectiveness_rounds"] = int(state.get("low_effectiveness_rounds", 0)) + 1 if low else 0
    if not bool(config["auto_backoff"]):
        state["last_action"] = "HOLD_AUTO_BACKOFF_DISABLED"
        return str(state["last_action"])
    if int(state["low_effectiveness_rounds"]) < int(config["backoff_window"]):
        state["last_action"] = "HOLD"
        return str(state["last_action"])

    factor = float(config["backoff_factor"])
    before = (
        float(state["active_strength"]),
        float(state["active_budget"]),
        float(state["active_refresh_fraction"]),
    )
    state["active_strength"] = max(
        float(config["min_strength"]), float(state["active_strength"]) * factor,
    )
    if float(state["active_budget"]) > 1.0:
        state["active_budget"] = 1.0 + (float(state["active_budget"]) - 1.0) * factor
    # Move refresh toward uniform coverage as concentrated hammering loses efficiency.
    state["active_refresh_fraction"] = 1.0 - (
        (1.0 - float(state["active_refresh_fraction"])) * factor
    )
    state["low_effectiveness_rounds"] = 0
    after = (
        float(state["active_strength"]),
        float(state["active_budget"]),
        float(state["active_refresh_fraction"]),
    )
    if all(abs(a - b) < 1e-12 for a, b in zip(before, after)):
        state["last_action"] = "AT_FLOOR"
    else:
        state["backoff_count"] = int(state.get("backoff_count", 0)) + 1
        state["last_action"] = "BACKOFF"
    return str(state["last_action"])


def _module_state_cpu(module) -> dict[str, Any]:
    return {
        name: tensor.detach().cpu().contiguous().clone()
        for name, tensor in module.state_dict().items()
    }


def _checkpoint_dir(output_dir: Path, cycle: int, reuse_epoch: int | None = None) -> Path:
    stem = f"cycle-{int(cycle):06d}"
    if reuse_epoch is not None:
        stem += f"-reuse-{int(reuse_epoch):03d}"
    return Path(output_dir) / "checkpoints" / stem


def save_checkpoint(
    *, torch, output_dir: Path, head, tinystories_lm, optimizer, cycle: int,
    global_step: int, cycle_metrics: dict[str, Any], parent: dict[str, Any], logger: EventLog,
    reuse_epoch: int | None = None,
) -> Path:
    from safetensors.torch import save_file

    final = _checkpoint_dir(output_dir, cycle, reuse_epoch)
    temp = final.with_name(final.name + ".tmp")
    if final.exists() or temp.exists():
        raise RuntimeError(f"checkpoint already exists: {final}")
    temp.mkdir(parents=True, exist_ok=False)
    try:
        save_file(_module_state_cpu(head), str(temp / "head.safetensors"))
        save_file(_module_state_cpu(tinystories_lm), str(temp / "tinystories.safetensors"))
        torch.save(optimizer.state_dict(), temp / "optimizer.pt")
        torch.save(
            {
                "python_random": random.getstate(),
                "torch_cpu": torch.get_rng_state(),
                "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
            },
            temp / "rng_state.pt",
        )
        smoke.atomic_json(
            temp / "meta.json",
            {
                "schema_version": SCHEMA,
                "trainer_variant": TRAINER_VARIANT,
                "cycle": int(cycle),
                "reuse_epoch": None if reuse_epoch is None else int(reuse_epoch),
                "cycle_complete": True,
                "global_step": int(global_step),
                "parent_hf": {
                    key: parent[key]
                    for key in ("repo_id", "requested_revision", "resolved_revision", "release_name", "manifest_sha256")
                    if key in parent
                },
                "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
                "structured_supervision_schema": ROM_SCHEMA,
                "metrics": cycle_metrics,
            },
        )
        os.replace(temp, final)
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    logger.emit(
        "clef_rom_checkpoint_saved",
        cycle=int(cycle),
        reuse_epoch=None if reuse_epoch is None else int(reuse_epoch),
        global_step=int(global_step),
        checkpoint=str(final.resolve()),
    )
    return final.resolve()


def load_checkpoint(*, torch, head, tinystories_lm, optimizer, checkpoint: Path) -> dict[str, Any]:
    from safetensors.torch import load_file

    checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
    meta = smoke.read_json(checkpoint / "meta.json")
    if meta.get("schema_version") != SCHEMA or meta.get("trainer_variant") != TRAINER_VARIANT:
        raise RuntimeError(
            f"unsupported ROM checkpoint contract at {checkpoint}: "
            f"schema={meta.get('schema_version')!r} variant={meta.get('trainer_variant')!r}"
        )
    head.load_state_dict(load_file(str(checkpoint / "head.safetensors"), device="cpu"), strict=True)
    tinystories_lm.load_state_dict(
        load_file(str(checkpoint / "tinystories.safetensors"), device="cpu"), strict=True
    )
    optimizer.load_state_dict(
        torch.load(checkpoint / "optimizer.pt", map_location="cpu", weights_only=False)
    )
    rng = torch.load(checkpoint / "rng_state.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python_random"])
    torch.set_rng_state(rng["torch_cpu"])
    if torch.cuda.is_available() and rng.get("torch_cuda"):
        torch.cuda.set_rng_state_all(rng["torch_cuda"])
    return meta


def optimizer_to_cuda(optimizer) -> None:
    for state in optimizer.state.values():
        for key, value in list(state.items()):
            if hasattr(value, "to"):
                state[key] = value.to(device="cuda")


def build_training_evidence_cache(
    *, torch, bundles, questions, args, logger: EventLog, cycle: int,
) -> list[dict[str, dict[str, Any]]]:
    cache = []
    for index, question in enumerate(questions, 1):
        evidence = champion.extract_live_evidence(
            torch=torch, bundles=bundles, question=question, args=args, logger=logger
        )
        cache.append({
            label: champion._cpu_detached_evidence(row)
            for label, row in evidence.items()
        })
        del evidence
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.emit(
            "clef_rom_training_evidence_cached",
            cycle=int(cycle), index=index, total=len(questions),
            question_id=str(question.question_id), task=str(question.task),
        )
    return cache


def _cuda_cached_evidence(cache_row: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {label: champion._cuda_evidence(row) for label, row in cache_row.items()}


def metric_row(*, question, logits, loss, ce, brier) -> dict[str, Any]:
    return base.metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)


def evaluate_population(*, torch, head, bundles, questions, args, logger: EventLog, phase: str):
    head.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        with torch.no_grad():
            evidence = champion.extract_live_evidence(
                torch=torch, bundles=bundles, question=question, args=args, logger=logger
            )
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        rows.append(row)
        logger.emit(
            "clef_rom_eval_question",
            phase=phase, index=index, total=len(questions), **row,
        )
        del evidence, logits, loss, ce, brier
    return {"summary": base.summarize_rows(rows), "rows": rows}


def evaluate_cached_population(
    *, torch, head, questions, evidence_cache, phase: str, logger: EventLog | None = None,
):
    """Evaluate the head against already-cached frozen-backbone evidence."""
    head.eval()
    rows = []
    for index, question in enumerate(questions, 1):
        with torch.no_grad():
            evidence = _cuda_cached_evidence(evidence_cache[index - 1])
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
        row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
        rows.append(row)
        del evidence, logits, loss, ce, brier
    summary = base.summarize_rows(rows)
    if logger is not None:
        logger.emit(
            "clef_rom_memorization_train_eval",
            phase=str(phase),
            questions=len(rows),
            accuracy=float(summary["overall"]["accuracy"]),
            mean_loss=float(summary["overall"]["mean_loss"]),
        )
    return {"summary": summary, "rows": rows}


def train_population(
    *, torch, head, optimizer, questions, evidence_cache, args, logger: EventLog,
    cycle: int, epoch: int, global_step: int, sample_indices: Sequence[int] | None = None,
):
    order = list(sample_indices) if sample_indices is not None else list(range(len(questions)))
    if sample_indices is None:
        random.Random(base.stable_seed(args.seed, cycle, epoch, "rom-train-order")).shuffle(order)
    rows = []
    first_rows_by_index: dict[int, dict[str, Any]] = {}
    last_loss_by_index: dict[int, float] = {}
    last_correct_by_index: dict[int, bool] = {}
    maximum_grad_norm = 0.0
    optimizer_steps = 0
    head.train()
    for group_start in range(0, len(order), int(args.grad_accumulation)):
        group = order[group_start: group_start + int(args.grad_accumulation)]
        optimizer.zero_grad(set_to_none=True)
        group_tasks = []
        for local_index, question_index in enumerate(group, 1):
            question = questions[question_index]
            evidence = _cuda_cached_evidence(evidence_cache[question_index])
            logits = head(evidence)
            loss, ce, brier = smoke.loss_parts(torch, logits, question.gold_index)
            if not bool(torch.isfinite(loss).item()):
                raise RuntimeError(
                    f"non-finite ROM loss cycle={cycle} epoch={epoch} question={question.question_id}"
                )
            row = metric_row(question=question, logits=logits, loss=loss, ce=ce, brier=brier)
            row["cycle"] = int(cycle)
            row["epoch"] = int(epoch)
            row["question_index"] = int(question_index)
            rows.append(row)
            if int(question_index) not in first_rows_by_index:
                first_rows_by_index[int(question_index)] = dict(row)
            last_loss_by_index[int(question_index)] = float(row["loss"])
            last_correct_by_index[int(question_index)] = bool(row["correct"])
            group_tasks.append(str(question.task))
            (loss / len(group)).backward()
            del evidence, logits, loss, ce, brier
        grad_norm = float(
            torch.nn.utils.clip_grad_norm_(head.parameters(), float(args.grad_clip)).item()
        )
        if not math.isfinite(grad_norm):
            raise RuntimeError(f"non-finite ROM gradient norm cycle={cycle}: {grad_norm}")
        maximum_grad_norm = max(maximum_grad_norm, grad_norm)
        optimizer.step()
        global_step += 1
        optimizer_steps += 1
        logger.emit(
            "clef_rom_optimizer_step",
            cycle=int(cycle), epoch=int(epoch), global_step=int(global_step),
            optimizer_step_in_epoch=optimizer_steps, accumulated_questions=len(group),
            tasks=group_tasks, grad_norm_preclip=grad_norm,
        )
    first_rows = [first_rows_by_index[index] for index in sorted(first_rows_by_index)]
    coverage_summary = base.summarize_rows(first_rows)
    return {
        "summary": base.summarize_rows(rows),
        "rows": rows,
        "optimizer_steps": optimizer_steps,
        "sample_exposures": len(order),
        "unique_examples": len(set(order)),
        "base_exposures": len(first_rows),
        "replay_exposures": max(0, len(order) - len(first_rows)),
        "full_bank_coverage": len(first_rows) == len(questions),
        "coverage_summary": coverage_summary,
        "coverage_loss_by_index": {
            int(index): float(row["loss"]) for index, row in first_rows_by_index.items()
        },
        "coverage_correct_by_index": {
            int(index): bool(row["correct"]) for index, row in first_rows_by_index.items()
        },
        "last_loss_by_index": last_loss_by_index,
        "last_correct_by_index": last_correct_by_index,
        "maximum_grad_norm": maximum_grad_norm,
    }, global_step, maximum_grad_norm


def prune_checkpoints(output_dir: Path, *, keep: int, latest: Path, best: Path, logger: EventLog) -> None:
    root = Path(output_dir) / "checkpoints"
    checkpoints = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith("cycle-")),
        key=lambda p: p.name,
    )
    protected = {Path(latest).resolve(), Path(best).resolve()}
    protected.update(path.resolve() for path in checkpoints[-max(0, int(keep)):])
    removed = []
    for path in checkpoints:
        if path.resolve() in protected:
            continue
        shutil.rmtree(path)
        removed.append(str(path))
    if removed:
        logger.emit("clef_rom_checkpoints_pruned", removed=removed)


def prepare_new_output(output_dir: Path) -> Path:
    output_dir = Path(output_dir).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(f"output directory is not empty; use --resume or a new path: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "checkpoints").mkdir(exist_ok=True)
    (output_dir / "cycles").mkdir(exist_ok=True)
    return output_dir.resolve()


def classify_output_start(output_dir: Path, *, resume: bool) -> str:
    """Return ``new``, ``bootstrap_recovery``, or ``resume`` without mutating disk."""
    output_dir = Path(output_dir).expanduser()
    if not output_dir.exists() or not any(output_dir.iterdir()):
        if resume:
            raise RuntimeError(f"cannot resume missing/uninitialized output directory: {output_dir}")
        return "new"
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    if state_path.is_file():
        if not resume:
            raise RuntimeError(
                f"output directory contains an established run; use --resume or a new path: {output_dir}"
            )
        if not experiment_path.is_file():
            raise RuntimeError(f"training state exists without experiment metadata: {output_dir}")
        return "resume"
    if experiment_path.is_file():
        return "bootstrap_recovery"
    raise RuntimeError(
        f"output directory is non-empty but is not a recognizable ROM run: {output_dir}"
    )


def validate_resume_experiment(
    experiment: Mapping[str, Any], *, train_plan: Mapping[str, int],
    selection_plan: Mapping[str, int], dev_plan: Mapping[str, int],
    winner_criterion: str, memorization: Mapping[str, Any] | None = None,
) -> None:
    if experiment.get("schema_version") != SCHEMA or experiment.get("trainer_variant") != TRAINER_VARIANT:
        raise RuntimeError("resume experiment is not a ROM structured-supervision run")
    if experiment.get("train_plan") != dict(train_plan):
        raise RuntimeError(f"resume train plan drifted: {experiment.get('train_plan')} != {dict(train_plan)}")
    if experiment.get("selection_plan") != dict(selection_plan) or experiment.get("dev_plan") != dict(dev_plan):
        raise RuntimeError("resume evaluation plan drifted")
    existing_criterion = str((experiment.get("contract") or {}).get("winner_criterion") or "")
    if existing_criterion != str(winner_criterion):
        raise RuntimeError(
            f"resume winner criterion drifted: {existing_criterion!r} != {str(winner_criterion)!r}"
        )
    existing_memorization = (experiment.get("contract") or {}).get("memorization")
    if existing_memorization is not None or memorization is not None:
        if existing_memorization != (None if memorization is None else dict(memorization)):
            raise RuntimeError(
                f"resume memorization contract drifted: {existing_memorization!r} != "
                f"{None if memorization is None else dict(memorization)!r}"
            )


def restore_pinned_parent_release(
    parent_record: Mapping[str, Any], *, api: Any | None = None,
    snapshot_download_fn: Any | None = None,
) -> dict[str, Any]:
    """Rehydrate an interrupted bootstrap from the exact already-pinned HF commit."""
    expected = dict(parent_record)
    pinned = resolve_parent_release(
        str(expected["repo_id"]), str(expected["resolved_revision"]),
        api=api, snapshot_download_fn=snapshot_download_fn,
    )
    if str(pinned["resolved_revision"]) != str(expected["resolved_revision"]):
        raise RuntimeError("pinned Hugging Face parent resolved to a different commit")
    if str(pinned["release_name"]) != str(expected["release_name"]):
        raise RuntimeError("pinned Hugging Face parent release name drifted")
    if str(pinned["manifest_sha256"]) != str(expected["manifest_sha256"]):
        raise RuntimeError("pinned Hugging Face parent manifest drifted")
    pinned["requested_revision"] = str(expected["requested_revision"])
    return pinned


def _portable_backbone_source(release: Mapping[str, Any]) -> dict[str, str]:
    source = ((release.get("backbone_provenance") or {}).get("source") or {})
    model = str(source.get("model") or "Qwen/Qwen3-0.6B")
    revision = str(source.get("revision") or "main")
    return {"model": model, "revision": revision}


def _parent_public_record(parent: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: parent[key]
        for key in ("repo_id", "requested_revision", "resolved_revision", "release_name", "manifest_sha256")
    }


def _write_error(output_dir: Path, logger: EventLog | None, exc: BaseException) -> None:
    payload = {
        "event": "clef_rom_train_failed",
        "stage": None if logger is None else logger.stage,
        "exception_type": type(exc).__name__,
        "exception": str(exc),
        "traceback": traceback.format_exc(),
    }
    try:
        smoke.atomic_json(Path(output_dir) / "error.json", payload)
    except Exception:
        pass
    print(json.dumps(payload, sort_keys=True), flush=True)


def run(args, logger: EventLog) -> None:
    validate_rom_templates()
    train_plan = training_curriculum_plan(int(args.train_questions_per_cycle))
    selection_plan = base.curriculum_plan(int(args.selection_questions))
    dev_plan = base.curriculum_plan(int(args.dev_questions_per_cycle))
    winner_criterion = rom_selection_policy(use_loss=bool(args.use_loss))
    memorize_contract = memorization_contract(args) if bool(args.memorize) else None

    output_dir = Path(args.output_dir).expanduser()
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    selection_path = output_dir / "selection_questions.json"
    training_db = output_dir / "lexical.db"

    start_mode = classify_output_start(output_dir, resume=bool(args.resume))
    if start_mode == "new":
        output_dir = prepare_new_output(output_dir)
    else:
        output_dir = output_dir.resolve(strict=True)
    experiment_path = output_dir / "experiment.json"
    state_path = output_dir / "training_state.json"
    selection_path = output_dir / "selection_questions.json"
    training_db = output_dir / "lexical.db"
    logger.output_dir = output_dir
    logger.events = output_dir / "events.jsonl"
    logger.progress = output_dir / "progress.json"
    logger.start_heartbeat()

    if start_mode == "resume":
        logger.set_stage("resume_load")
        experiment = smoke.read_json(experiment_path)
        state = smoke.read_json(state_path)
        validate_resume_experiment(
            experiment, train_plan=train_plan, selection_plan=selection_plan, dev_plan=dev_plan,
            winner_criterion=winner_criterion, memorization=memorize_contract,
        )
        parent = dict(experiment["parent_hf"])
        backbone_source = dict(experiment["backbone_source"])
        start_cycle = int(state["cycle"]) + 1
        global_step = int(state["global_step"])
        latest_checkpoint = Path(str(state["latest_checkpoint"])).resolve(strict=True)
        best_checkpoint = Path(str(state["best_checkpoint"])).resolve(strict=True)
        best_selection_accuracy = float(state["best_selection_accuracy"])
        best_selection_loss = float(state["best_selection_loss"])
        rom_baseline_accuracy = float(state["rom_baseline_accuracy"])
        rom_baseline_loss = float(state["rom_baseline_loss"])
        create_db = False
        selection_payload = smoke.read_json(selection_path)
    elif start_mode == "bootstrap_recovery":
        logger.set_stage("bootstrap_recovery")
        experiment = smoke.read_json(experiment_path)
        validate_resume_experiment(
            experiment, train_plan=train_plan, selection_plan=selection_plan, dev_plan=dev_plan,
            winner_criterion=winner_criterion, memorization=memorize_contract,
        )
        parent = restore_pinned_parent_release(experiment["parent_hf"])
        backbone_source = dict(experiment["backbone_source"])
        start_cycle = 1
        global_step = 0
        latest_checkpoint = None
        best_checkpoint = None
        best_selection_accuracy = -math.inf
        best_selection_loss = math.inf
        rom_baseline_accuracy = -math.inf
        rom_baseline_loss = math.inf
        create_db = not training_db.is_file()
        selection_payload = smoke.read_json(selection_path) if selection_path.is_file() else None
        logger.emit(
            "clef_rom_bootstrap_recovered",
            output_dir=str(output_dir),
            parent_resolved_revision=parent["resolved_revision"],
            reused_training_db=not create_db,
            reused_selection_bank=selection_payload is not None,
        )
    else:
        logger.set_stage("parent_resolve")
        parent = resolve_parent_release(args.parent_repo, args.parent_revision)
        release = dict(parent["release"])
        backbone_source = _portable_backbone_source(release)
        experiment = {
            "schema_version": SCHEMA,
            "trainer_variant": TRAINER_VARIANT,
            "created_unix": time.time(),
            "parent_hf": _parent_public_record(parent),
            "backbone_source": backbone_source,
            "question_source_experiment": str(Path(args.question_source_experiment).expanduser().resolve(strict=True)),
            "seed": int(args.seed),
            "data_cycle_base": int(args.data_cycle_base),
            "train_plan": train_plan,
            "selection_plan": selection_plan,
            "dev_plan": dev_plan,
            "source": backbone_source,
            "contract": {
                "frozen_backbones": ["Qwen/Qwen3-0.6B", "EleutherAI/pythia-70m", "roneneldan/TinyStories-33M"],
                "tinystories_layer_tap_schema": champion.TINYSTORIES_LAYER_TAP_SCHEMA,
                "tinystories_tapped_layers": list(champion.TINYSTORIES_TAPPED_LAYERS),
                "tinystories_residual_layers": list(champion.TINYSTORIES_RESIDUAL_LAYERS),
                "tinystories_anchor_layer": champion.TINYSTORIES_FINAL_LAYER,
                "structured_supervision_schema": ROM_SCHEMA,
                "structured_supervision_training_only": False,
                "structured_supervision_affects_inference": True,
                "evaluation_representation": "structured_rom",
                "winner_criterion": winner_criterion,
                "use_loss": bool(args.use_loss),
                "memorization": memorize_contract,
                "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
                "task_composition": base.TASK_COMPOSITION_VERSION,
                "evidence_contract": base.EVIDENCE_CONTRACT_VERSION,
                "parent_optimizer_state": "not-published-reset-at-fork",
            },
            "rom_templates": {
                task: {
                    "template_id": rom_template_id(task),
                    "inputs": list(template["inputs"]),
                    "output": template["output"],
                    "program": list(template["program"]),
                }
                for task, template in ROM_TEMPLATES.items()
            },
            "hyperparameters": {
                "train_questions_per_cycle": int(args.train_questions_per_cycle),
                "selection_questions": int(args.selection_questions),
                "dev_questions_per_cycle": int(args.dev_questions_per_cycle),
                "epochs_per_cycle": int(args.epochs_per_cycle),
                "memorization": memorize_contract,
                "grad_accumulation": int(args.grad_accumulation),
                "head_lr": float(args.head_lr),
                "weight_decay": float(args.weight_decay),
                "grad_clip": float(args.grad_clip),
                "train_task_percent": TRAIN_TASK_PERCENT,
            },
        }
        smoke.atomic_json(experiment_path, experiment)
        start_cycle = 1
        global_step = 0
        latest_checkpoint = None
        best_checkpoint = None
        best_selection_accuracy = -math.inf
        best_selection_loss = math.inf
        rom_baseline_accuracy = -math.inf
        rom_baseline_loss = math.inf
        create_db = True
        selection_payload = None

    question_source = Path(experiment["question_source_experiment"]).expanduser().resolve(strict=True)
    logger.emit(
        "clef_rom_train_start",
        parent_hf=_parent_public_record(parent),
        output_dir=str(output_dir), resume=start_mode == "resume", bootstrap_recovery=start_mode == "bootstrap_recovery", start_cycle=start_cycle,
        train_plan=train_plan, selection_plan=selection_plan, dev_plan=dev_plan,
        rom_schema=ROM_SCHEMA, task_mix_schema=TRAINING_TASK_MIX_SCHEMA,
        selection_representation="structured_rom", winner_criterion=winner_criterion,
        memorization=memorize_contract,
    )

    logger.set_stage("question_source")
    with champion.EfficientQuestionFactory(
        source_experiment=question_source,
        training_db=training_db,
        seed=int(args.seed),
        create_db=create_db,
        logger=logger,
    ) as factory:
        if selection_payload is not None:
            natural_selection = [
                base.deserialize_question(row, factory.objective_api)
                for row in selection_payload["questions"]
            ]
        else:
            natural_selection = factory.generate_selection(
                data_cycle=int(args.data_cycle_base),
                selection_plan=selection_plan,
                seed=int(args.seed),
            )
            selection_payload = {
                "schema_version": SCHEMA,
                "trainer_variant": TRAINER_VARIANT,
                "data_cycle": int(args.data_cycle_base),
                "plan": selection_plan,
                "count": len(natural_selection),
                "questions": [base.serialize_question(question) for question in natural_selection],
            }
            smoke.atomic_json(selection_path, selection_payload)
        selection_fingerprints = {
            factory.question_fingerprint(question) for question in natural_selection
        }
        if len(selection_fingerprints) != len(natural_selection):
            raise RuntimeError("fixed selection bank contains duplicate fingerprints")
        rom_selection = romify_population(natural_selection)

        logger.set_stage("backbone_load")
        import torch
        from safetensors.torch import load_file

        if not torch.cuda.is_available():
            raise RuntimeError("ROM training requires CUDA")
        torch.manual_seed(int(args.seed))
        torch.cuda.manual_seed_all(int(args.seed))
        bundles, _frozen_total = smoke.load_backbones(
            source=backbone_source,
            local_files_only=bool(args.local_files_only),
            logger=logger,
        )
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        head, _ = champion.build_layer_tap_head(torch=torch, hidden_sizes=hidden_sizes)
        optimizer = torch.optim.AdamW(
            head.parameters(),
            lr=float(args.head_lr),
            weight_decay=float(args.weight_decay),
            foreach=False,
        )

        if args.resume:
            load_checkpoint(
                torch=torch, head=head, tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                optimizer=optimizer, checkpoint=latest_checkpoint,
            )
        else:
            parent_snapshot = Path(str(parent["snapshot"])).resolve(strict=True)
            bundles[champion.TRAINABLE_LABEL].lm.load_state_dict(
                load_file(str(parent_snapshot / "tinystories.safetensors"), device="cpu"),
                strict=True,
            )
            head.load_state_dict(
                load_file(str(parent_snapshot / "head.safetensors"), device="cpu"),
                strict=True,
            )
        backbone_trainable = champion.configure_backbone_trainability(bundles)
        if any(backbone_trainable.values()):
            raise RuntimeError(f"three-frozen-backbone invariant failed: {backbone_trainable}")
        for parameter in head.parameters():
            parameter.requires_grad_(True)
        head = head.to(device="cuda", dtype=torch.bfloat16)
        optimizer_to_cuda(optimizer)
        frozen_before = smoke.frozen_signatures(bundles)
        logger.emit(
            "clef_rom_model_ready",
            hidden_sizes=hidden_sizes,
            head_parameters=smoke.count_parameters(head),
            trainable_head_parameters=sum(int(p.numel()) for p in head.parameters() if p.requires_grad),
            backbone_trainable_parameters=backbone_trainable,
            parent_release_name=parent.get("release_name"),
            parent_resolved_revision=parent.get("resolved_revision"),
        )

        if latest_checkpoint is None:
            logger.set_stage("parent_rom_baseline", cycle=0)
            baseline = evaluate_population(
                torch=torch, head=head, bundles=bundles, questions=rom_selection,
                args=args, logger=logger, phase="cycle-000000-rom-selection",
            )
            best_selection_accuracy = float(baseline["summary"]["overall"]["accuracy"])
            best_selection_loss = float(baseline["summary"]["overall"]["mean_loss"])
            rom_baseline_accuracy = best_selection_accuracy
            rom_baseline_loss = best_selection_loss
            metrics = {
                "cycle": 0,
                "global_step": 0,
                "parent_baseline": True,
                "selection": baseline["summary"],
                "selection_representation": "structured_rom",
                "training": [],
                "structured_supervision_schema": ROM_SCHEMA,
                "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
                "frozen_backbones": smoke.verify_frozen_unchanged(bundles, frozen_before),
            }
            latest_checkpoint = save_checkpoint(
                torch=torch, output_dir=output_dir, head=head,
                tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                optimizer=optimizer, cycle=0, global_step=0,
                cycle_metrics=metrics, parent=parent, logger=logger,
            )
            best_checkpoint = latest_checkpoint
            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "trainer_variant": TRAINER_VARIANT,
                    "cycle": 0,
                    "data_cycle": int(args.data_cycle_base),
                    "global_step": 0,
                    "latest_checkpoint": str(latest_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_accuracy": best_selection_accuracy,
                    "best_selection_loss": best_selection_loss,
                    "rom_baseline_accuracy": rom_baseline_accuracy,
                    "rom_baseline_loss": rom_baseline_loss,
                    "updated_unix": time.time(),
                },
            )
            logger.emit(
                "clef_rom_parent_baseline",
                selection_accuracy=best_selection_accuracy,
                selection_loss=best_selection_loss,
                representation="structured_rom",
                checkpoint=str(latest_checkpoint),
            )

        stop_cycle = start_cycle + int(args.max_cycles) - 1 if int(args.max_cycles) > 0 else None
        cycle = start_cycle
        while stop_cycle is None or cycle <= stop_cycle:
            data_cycle = int(args.data_cycle_base) + cycle
            cycle_dir = output_dir / "cycles" / f"cycle-{cycle:06d}"
            cycle_dir.mkdir(parents=True, exist_ok=False)
            logger.set_stage("question_generation", cycle=cycle, data_cycle=data_cycle)
            natural_train, natural_dev, question_meta = factory.generate(
                data_cycle=data_cycle,
                train_plan=train_plan,
                dev_plan=dev_plan,
                seed=int(args.seed),
                blocked_fingerprints=selection_fingerprints,
            )
            rom_train = romify_population(natural_train)
            rom_dev = romify_population(natural_dev)
            question_meta["rom_train"] = rom_population_manifest(rom_train)
            question_meta["rom_dev"] = rom_population_manifest(rom_dev)
            smoke.atomic_json(cycle_dir / "questions.json", question_meta)

            incumbent_checkpoint = Path(best_checkpoint).resolve(strict=True)
            incumbent_accuracy = float(best_selection_accuracy)
            incumbent_loss = float(best_selection_loss)
            tracked = next(iter(head.parameters()))
            tracked_before = smoke.sampled_parameter_signature(tracked)
            logger.set_stage("training_evidence_cache", cycle=cycle)
            evidence_cache = build_training_evidence_cache(
                torch=torch, bundles=bundles, questions=rom_train, args=args,
                logger=logger, cycle=cycle,
            )
            train_passes = []
            reuse_attempts = []
            cycle_max_grad = 0.0
            best_train_loss = math.inf
            stalled_measurements = 0
            memorization_stop = None
            loss_cache: dict[int, float] = {}
            previous_global_loss: float | None = None
            hammer_config = dict(args.hammer_config) if args.memorize else None
            hammer_state = initial_hammer_state(hammer_config) if hammer_config else None
            epoch_limit = (
                int(args.memorize_max_epochs) if args.memorize
                else int(args.epochs_per_cycle)
            )
            logger.set_stage("memorization" if args.memorize else "training", cycle=cycle)
            for epoch in range(1, epoch_limit + 1):
                if args.memorize:
                    sample_indices = hammer_training_indices(
                        count=len(rom_train), previous_losses=loss_cache,
                        state=hammer_state, seed=int(args.seed), cycle=cycle, epoch=epoch,
                    )
                else:
                    sample_indices = None

                previous_losses = dict(loss_cache)
                result, global_step, epoch_max_grad = train_population(
                    torch=torch, head=head, optimizer=optimizer, questions=rom_train,
                    evidence_cache=evidence_cache, args=args, logger=logger,
                    cycle=cycle, epoch=epoch, global_step=global_step,
                    sample_indices=sample_indices,
                )
                result["epoch"] = epoch
                cycle_max_grad = max(cycle_max_grad, epoch_max_grad)

                if args.memorize:
                    if not bool(result.get("full_bank_coverage")):
                        raise RuntimeError(
                            f"memorization round lost full-bank coverage cycle={cycle} epoch={epoch}: "
                            f"base_exposures={result.get('base_exposures')} expected={len(rom_train)}"
                        )
                    current_losses = {
                        int(index): float(value)
                        for index, value in result.get("coverage_loss_by_index", {}).items()
                    }
                    global_accuracy = float(result["coverage_summary"]["overall"]["accuracy"])
                    global_loss = float(result["coverage_summary"]["overall"]["mean_loss"])
                    effectiveness = hammer_global_effectiveness(
                        previous_global_loss, global_loss,
                        optimizer_steps=int(result["optimizer_steps"]),
                    )
                    previous_global_loss = global_loss
                    loss_cache = dict(current_losses)
                    backoff_action = update_hammer_state(
                        hammer_state, effectiveness=effectiveness, config=hammer_config,
                    )

                    if global_loss < best_train_loss - float(args.memorize_min_loss_improvement):
                        best_train_loss = global_loss
                        stalled_measurements = 0
                    else:
                        stalled_measurements += 1
                    memorization_stop = memorization_stop_reason(
                        epoch=epoch, accuracy=global_accuracy, mean_loss=global_loss,
                        stalled_measurements=stalled_measurements, args=args,
                    )
                    eval_every = int(hammer_config["selection_eval_every"])
                    selection_due = (
                        epoch % eval_every == 0
                        or epoch >= epoch_limit
                        or memorization_stop is not None
                    )

                    # The mandatory first exposure of every example is the global train
                    # measurement.  Do not perform a second 480-example inference pass.
                    result["memorization_post"] = result["coverage_summary"]
                    result["memorization_stop_reason"] = memorization_stop
                    result["hammer_state"] = dict(hammer_state)
                    result["hammer_effectiveness"] = effectiveness
                    logger.emit(
                        "clef_rom_memorization_epoch",
                        cycle=int(cycle), epoch=int(epoch), global_step=int(global_step),
                        global_train_accuracy=global_accuracy, global_train_loss=global_loss,
                        sample_accuracy=float(result["summary"]["overall"]["accuracy"]),
                        sample_loss=float(result["summary"]["overall"]["mean_loss"]),
                        best_train_loss=(None if math.isinf(best_train_loss) else float(best_train_loss)),
                        stalled_measurements=int(stalled_measurements),
                        sample_exposures=int(result.get("sample_exposures", 0)),
                        unique_examples=int(result.get("unique_examples", 0)),
                        base_exposures=int(result.get("base_exposures", 0)),
                        replay_exposures=int(result.get("replay_exposures", 0)),
                        full_bank_coverage=bool(result.get("full_bank_coverage")),
                        optimizer_steps=int(result.get("optimizer_steps", 0)),
                        hammer_strength=float(hammer_state["active_strength"]),
                        hammer_budget=float(hammer_state["active_budget"]),
                        hammer_refresh_fraction=float(hammer_state["active_refresh_fraction"]),
                        hammer_effectiveness=(
                            None if effectiveness is None
                            else float(effectiveness["loss_drop_per_100_steps"])
                        ),
                        hammer_backoff_action=backoff_action,
                        hammer_backoff_count=int(hammer_state["backoff_count"]),
                        selection_due=bool(selection_due),
                        stop_reason=memorization_stop,
                    )
                else:
                    selection_due = True

                train_passes.append(result)
                if not selection_due:
                    continue

                logger.set_stage("rom_selection", cycle=cycle, reuse_depth=epoch)
                selection_after = evaluate_population(
                    torch=torch, head=head, bundles=bundles, questions=rom_selection,
                    args=args, logger=logger,
                    phase=f"cycle-{cycle:06d}-reuse-{epoch:03d}-rom-selection",
                )
                selection_accuracy = float(selection_after["summary"]["overall"]["accuracy"])
                selection_loss = float(selection_after["summary"]["overall"]["mean_loss"])
                if args.use_loss:
                    preferred_to_incumbent = rom_loss_prefers_candidate(
                        candidate_loss=selection_loss,
                        incumbent_loss=incumbent_loss,
                    )
                else:
                    preferred_to_incumbent = rom_accuracy_prefers_candidate(
                        candidate_accuracy=selection_accuracy,
                        incumbent_accuracy=incumbent_accuracy,
                    )
                training_summary = (
                    result.get("memorization_post") if args.memorize else result["summary"]
                )
                attempt_metrics = {
                    "cycle": int(cycle),
                    "reuse_depth": int(epoch),
                    "global_step": int(global_step),
                    "training": training_summary,
                    "training_online": result["summary"],
                    "selection": selection_after["summary"],
                    "candidate_preferred_to_incumbent": bool(preferred_to_incumbent),
                    "rom_incumbent_before": {
                        "accuracy": incumbent_accuracy,
                        "mean_loss": incumbent_loss,
                        "checkpoint": str(incumbent_checkpoint),
                    },
                    "maximum_grad_norm": float(epoch_max_grad),
                    "structured_supervision_schema": ROM_SCHEMA,
                    "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
                    "rom_selection_policy": winner_criterion,
                    "selection_representation": "structured_rom",
                    "memorization": memorize_contract,
                    "memorization_stop_reason": memorization_stop,
                }
                checkpoint = save_checkpoint(
                    torch=torch, output_dir=output_dir, head=head,
                    tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                    optimizer=optimizer, cycle=cycle, reuse_epoch=epoch,
                    global_step=global_step, cycle_metrics=attempt_metrics,
                    parent=parent, logger=logger,
                )
                reuse_attempts.append({
                    "reuse_depth": int(epoch),
                    "checkpoint": str(checkpoint),
                    "predev": selection_after["summary"],
                })
                if args.memorize and memorization_stop:
                    logger.emit(
                        "clef_rom_memorization_stop",
                        cycle=int(cycle), epoch=int(epoch), reason=str(memorization_stop),
                        train_accuracy=float(training_summary["overall"]["accuracy"]),
                        train_loss=float(training_summary["overall"]["mean_loss"]),
                    )
                    break

            del evidence_cache
            torch.cuda.empty_cache()

            if args.use_loss:
                winner = choose_rom_loss_winner(
                    incumbent_loss=incumbent_loss,
                    attempts=reuse_attempts,
                )
            else:
                winner = choose_rom_accuracy_winner(
                    incumbent_accuracy=incumbent_accuracy,
                    attempts=reuse_attempts,
                )
            if winner is None:
                best_checkpoint = incumbent_checkpoint
                best_selection_accuracy = incumbent_accuracy
                best_selection_loss = incumbent_loss
                winning_reuse_depth = 0
            else:
                best_checkpoint = Path(str(winner["checkpoint"])).resolve(strict=True)
                best_selection_accuracy = float(winner["predev"]["overall"]["accuracy"])
                best_selection_loss = float(winner["predev"]["overall"]["mean_loss"])
                winning_reuse_depth = int(winner["reuse_depth"])
                logger.emit(
                    "clef_rom_new_best",
                    cycle=cycle,
                    reuse_depth=winning_reuse_depth,
                    checkpoint=str(best_checkpoint),
                    selection_accuracy=best_selection_accuracy,
                    selection_loss=best_selection_loss,
                    winner_criterion=winner_criterion,
                )

            # The next cycle must continue from the selected champion depth, not
            # blindly from the deepest reuse attempt.
            attempt_global_step_end = int(global_step)
            selected_meta = load_checkpoint(
                torch=torch, head=head,
                tinystories_lm=bundles[champion.TRAINABLE_LABEL].lm,
                optimizer=optimizer, checkpoint=best_checkpoint,
            )
            optimizer_to_cuda(optimizer)
            global_step = int(selected_meta["global_step"])
            latest_checkpoint = best_checkpoint

            logger.set_stage("fresh_dev", cycle=cycle, winning_reuse_depth=winning_reuse_depth)
            fresh_dev = evaluate_population(
                torch=torch, head=head, bundles=bundles, questions=rom_dev,
                args=args, logger=logger, phase=f"cycle-{cycle:06d}-fresh-rom",
            )

            tracked_after = smoke.sampled_parameter_signature(tracked)
            tracked_delta = float((tracked_after - tracked_before).abs().max().item())
            frozen = smoke.verify_frozen_unchanged(bundles, frozen_before)
            if not all(
                row["unchanged"] and not row["requires_grad_any"] and not row["grad_present_any"]
                for row in frozen.values()
            ):
                raise RuntimeError(f"three-frozen-backbone invariant failed at cycle {cycle}: {frozen}")
            if cycle_max_grad <= 0.0:
                raise RuntimeError(f"head produced no nonzero gradient during cycle {cycle}")
            # tracked_delta is diagnostic only.  A selected ROM winner is established by
            # held-out ROM accuracy, not by whether one arbitrarily sampled head tensor
            # happened to move.  Some head parameters can legitimately remain unchanged.

            cycle_metrics = {
                "cycle": int(cycle),
                "data_cycle": int(data_cycle),
                "global_step": int(global_step),
                "attempt_global_step_end": int(attempt_global_step_end),
                "training": [row["summary"] for row in train_passes],
                "memorization_training": [
                    row.get("memorization_post") for row in train_passes
                    if row.get("memorization_post") is not None
                ],
                "memorization": memorize_contract,
                "memorization_stop_reason": memorization_stop,
                "reuse_attempts": reuse_attempts,
                "winning_reuse_depth": int(winning_reuse_depth),
                "rom_selection_policy": winner_criterion,
                "selection_representation": "structured_rom",
                "rom_incumbent_before": {
                    "accuracy": incumbent_accuracy,
                    "mean_loss": incumbent_loss,
                    "checkpoint": str(incumbent_checkpoint),
                },
                "champion_after": {
                    "accuracy": best_selection_accuracy,
                    "mean_loss": best_selection_loss,
                    "checkpoint": str(best_checkpoint),
                },
                "rom_reuse_zero_baseline": {
                    "accuracy": rom_baseline_accuracy,
                    "mean_loss": rom_baseline_loss,
                },
                "rom_accuracy_gain_from_reuse_zero": best_selection_accuracy - rom_baseline_accuracy,
                "rom_loss_reduction_from_reuse_zero": rom_baseline_loss - best_selection_loss,
                "fresh_dev": fresh_dev["summary"],
                "fresh_dev_representation": "structured_rom",
                "maximum_grad_norm": cycle_max_grad,
                "tracked_head_max_abs_delta": tracked_delta,
                "frozen_backbones": frozen,
                "structured_supervision_schema": ROM_SCHEMA,
                "training_task_mix_schema": TRAINING_TASK_MIX_SCHEMA,
            }
            smoke.atomic_json(cycle_dir / "metrics.json", cycle_metrics)

            smoke.atomic_json(
                state_path,
                {
                    "schema_version": SCHEMA,
                    "trainer_variant": TRAINER_VARIANT,
                    "cycle": int(cycle),
                    "data_cycle": int(data_cycle),
                    "global_step": int(global_step),
                    "latest_checkpoint": str(latest_checkpoint),
                    "best_checkpoint": str(best_checkpoint),
                    "best_selection_accuracy": best_selection_accuracy,
                    "best_selection_loss": best_selection_loss,
                    "rom_baseline_accuracy": rom_baseline_accuracy,
                    "rom_baseline_loss": rom_baseline_loss,
                    "winning_reuse_depth": int(winning_reuse_depth),
                    "memorization": memorize_contract,
                    "memorization_stop_reason": memorization_stop,
                    "latest_dev_accuracy": float(fresh_dev["summary"]["overall"]["accuracy"]),
                    "latest_dev_loss": float(fresh_dev["summary"]["overall"]["mean_loss"]),
                    "updated_unix": time.time(),
                },
            )
            prune_checkpoints(
                output_dir, keep=int(args.keep_checkpoints),
                latest=latest_checkpoint, best=best_checkpoint, logger=logger,
            )
            logger.emit(
                "clef_rom_cycle_complete",
                cycle=cycle,
                winning_reuse_depth=int(winning_reuse_depth),
                latest_checkpoint=str(latest_checkpoint),
                best_checkpoint=str(best_checkpoint),
                best_selection_accuracy=best_selection_accuracy,
                best_selection_loss=best_selection_loss,
                rom_baseline_accuracy=rom_baseline_accuracy,
                rom_accuracy_gain=best_selection_accuracy - rom_baseline_accuracy,
                rom_loss_reduction=rom_baseline_loss - best_selection_loss,
                winner_criterion=winner_criterion,
                memorization=memorize_contract,
                memorization_stop_reason=memorization_stop,
                fresh_dev_accuracy=float(fresh_dev["summary"]["overall"]["accuracy"]),
                fresh_dev_loss=float(fresh_dev["summary"]["overall"]["mean_loss"]),
            )
            cycle += 1

    logger.set_stage("complete")


def self_test() -> dict[str, Any]:
    validate_rom_templates()
    plan = training_curriculum_plan(480)
    expected = {
        "legacy": 12,
        "mutation": 108,
        "ast": 12,
        "consensus": 180,
        "triad": 144,
        "dictionary_definition": 12,
        "english_code": 12,
    }
    if plan != expected:
        raise RuntimeError(f"480-question ROM plan drifted: {plan} != {expected}")
    easy_total = sum(TRAIN_TASK_PERCENT[task] for task in EASY_TASKS)
    if easy_total != 10.0:
        raise RuntimeError(f"easy-task total is not 10%: {easy_total}")
    for task in EASY_TASKS:
        if TRAIN_TASK_PERCENT[task] != 2.5:
            raise RuntimeError(f"easy task does not have equal 2.5% share: {task}={TRAIN_TASK_PERCENT[task]}")
    if not (
        TRAIN_TASK_PERCENT["mutation"]
        < TRAIN_TASK_PERCENT["triad"]
        < TRAIN_TASK_PERCENT["consensus"]
    ):
        raise RuntimeError("hard-task budget ordering must be mutation < triad < consensus")
    return {
        "ok": True,
        "schema": SCHEMA,
        "trainer_variant": TRAINER_VARIANT,
        "parent": f"{DEFAULT_PARENT_REPO}@{DEFAULT_PARENT_REVISION}",
        "accuracy_winner_criterion": ROM_SELECTION_ACCURACY_POLICY,
        "loss_winner_criterion": ROM_SELECTION_LOSS_POLICY,
        "train_plan_480": plan,
        "template_ids": {task: rom_template_id(task) for task in base.TASKS},
    }


def parse_args(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-repo", default=DEFAULT_PARENT_REPO)
    parser.add_argument("--parent-revision", default=DEFAULT_PARENT_REVISION)
    parser.add_argument("--question-source-experiment", type=Path, default=DEFAULT_QUESTION_SOURCE_EXPERIMENT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--data-cycle-base", type=int, default=DEFAULT_DATA_CYCLE_BASE)
    parser.add_argument("--train-questions-per-cycle", type=int, default=DEFAULT_TRAIN_QUESTIONS)
    parser.add_argument("--selection-questions", type=int, default=DEFAULT_SELECTION_QUESTIONS)
    parser.add_argument("--dev-questions-per-cycle", type=int, default=DEFAULT_DEV_QUESTIONS)
    parser.add_argument("--max-cycles", type=int, default=DEFAULT_MAX_CYCLES)
    parser.add_argument("--epochs-per-cycle", type=int, default=DEFAULT_EPOCHS_PER_CYCLE)
    parser.add_argument("--grad-accumulation", type=int, default=DEFAULT_GRAD_ACCUMULATION)
    parser.add_argument("--head-lr", type=float, default=DEFAULT_HEAD_LR)
    parser.add_argument("--weight-decay", type=float, default=DEFAULT_WEIGHT_DECAY)
    parser.add_argument("--grad-clip", type=float, default=DEFAULT_GRAD_CLIP)
    parser.add_argument("--keep-checkpoints", type=int, default=DEFAULT_KEEP_CHECKPOINTS)
    parser.add_argument("--path-batch", type=int, default=DEFAULT_PATH_BATCH)
    parser.add_argument("--tinystories-path-batch", type=int, default=DEFAULT_TINYSTORIES_PATH_BATCH)
    parser.add_argument("--max-prompt-tokens", type=int, default=DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--prompt-evidence-tokens", type=int, default=DEFAULT_PROMPT_EVIDENCE_TOKENS)
    parser.add_argument("--answer-evidence-tokens", type=int, default=DEFAULT_ANSWER_EVIDENCE_TOKENS)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--use-loss", action="store_true",
        help="select/promote ROM checkpoints by strictly lower held-out ROM mean loss instead of accuracy",
    )
    parser.add_argument(
        "--memorize", action="store_true",
        help=(
            "hammer cached ROM training evidence into the head with many cheap head-only epochs; "
            "held-out ROM selection runs periodically instead of every epoch"
        ),
    )
    parser.add_argument("--memorize-max-epochs", type=int, default=DEFAULT_MEMORIZE_MAX_EPOCHS)
    parser.add_argument("--memorize-target-accuracy", type=float, default=DEFAULT_MEMORIZE_TARGET_ACCURACY)
    parser.add_argument("--memorize-target-loss", type=float, default=DEFAULT_MEMORIZE_TARGET_LOSS)
    parser.add_argument(
        "--memorize-eval-every", type=int, default=None,
        help="override the scheme's held-out ROM selection cadence; exact train-bank checks share this cadence",
    )
    parser.add_argument("--memorize-stall-epochs", type=int, default=DEFAULT_MEMORIZE_STALL_EPOCHS)
    parser.add_argument(
        "--memorize-min-loss-improvement", type=float, default=DEFAULT_MEMORIZE_MIN_LOSS_IMPROVEMENT
    )
    parser.add_argument(
        "--training-scheme", choices=("soft", "medium", "hard", "custom"), default="medium",
        help="hammer preset; explicit --hammer-* values override soft/medium/hard presets",
    )
    parser.add_argument("--hammer-strength", type=float, default=None)
    parser.add_argument("--hammer-budget", type=float, default=None)
    parser.add_argument("--hammer-refresh-fraction", type=float, default=None)
    parser.add_argument("--hammer-backoff-window", type=int, default=None)
    parser.add_argument("--hammer-backoff-threshold", type=float, default=None)
    parser.add_argument("--hammer-backoff-factor", type=float, default=None)
    parser.add_argument("--hammer-min-strength", type=float, default=None)
    parser.add_argument(
        "--hammer-auto-backoff", action=argparse.BooleanOptionalAction, default=None,
        help="automatically relax strength/budget toward uniform training when loss improvement per step fades",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    try:
        args.hammer_config = resolve_hammer_config(args)
        validate_hammer_config(args.hammer_config)
    except ValueError as exc:
        parser.error(str(exc))

    hammer_overrides_requested = bool(_hammer_override_fields(args))
    if not args.memorize and (
        args.training_scheme != "medium" or hammer_overrides_requested or args.memorize_eval_every is not None
    ):
        parser.error("--training-scheme/--hammer-* controls require --memorize")

    if args.output_dir is None:
        if args.memorize:
            args.output_dir = default_memorize_output(
                use_loss=bool(args.use_loss), scheme=str(args.training_scheme),
            )
        elif args.use_loss:
            args.output_dir = DEFAULT_LOSS_OUTPUT
        else:
            args.output_dir = DEFAULT_OUTPUT
    for name in (
        "train_questions_per_cycle", "selection_questions", "dev_questions_per_cycle",
        "epochs_per_cycle", "grad_accumulation", "keep_checkpoints", "path_batch",
        "tinystories_path_batch", "max_prompt_tokens", "max_answer_tokens",
        "prompt_evidence_tokens", "answer_evidence_tokens",
        "memorize_max_epochs", "memorize_stall_epochs",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if int(args.max_cycles) < 0:
        parser.error("--max-cycles must be >= 0")
    if not 0.0 <= float(args.memorize_target_accuracy) <= 1.0:
        parser.error("--memorize-target-accuracy must be between 0 and 1")
    if float(args.memorize_target_loss) < 0.0:
        parser.error("--memorize-target-loss must be >= 0")
    if float(args.memorize_min_loss_improvement) < 0.0:
        parser.error("--memorize-min-loss-improvement must be >= 0")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), indent=2, sort_keys=True))
        return 0
    validate_rom_templates()
    if args.dry_run:
        print(json.dumps({
            "ok": True,
            "dry_run": True,
            "parent_repo": args.parent_repo,
            "parent_revision": args.parent_revision,
            "output_dir": str(args.output_dir),
            "train_plan": training_curriculum_plan(args.train_questions_per_cycle),
            "selection_plan": base.curriculum_plan(args.selection_questions),
            "dev_plan": base.curriculum_plan(args.dev_questions_per_cycle),
            "rom_schema": ROM_SCHEMA,
            "selection_representation": "structured_rom",
            "winner_criterion": rom_selection_policy(use_loss=bool(args.use_loss)),
            "use_loss": bool(args.use_loss),
            "memorize": bool(args.memorize),
            "memorization": memorization_contract(args) if args.memorize else None,
            "effective_epochs_per_cycle": (
                int(args.memorize_max_epochs) if args.memorize else int(args.epochs_per_cycle)
            ),
            "template_ids": {task: rom_template_id(task) for task in base.TASKS},
        }, indent=2, sort_keys=True))
        return 0

    logger = EventLog(Path(args.output_dir).expanduser())
    try:
        run(args, logger)
        return 0
    except Exception as exc:
        _write_error(Path(args.output_dir).expanduser(), logger, exc)
        return 1
    finally:
        logger.stop_heartbeat()


if __name__ == "__main__":
    raise SystemExit(main())
