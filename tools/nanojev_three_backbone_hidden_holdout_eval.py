#!/usr/bin/env python3
"""One-shot hidden-holdout evaluator for the current three-backbone NanoJev head.

This is a measurement instrument, not a trainer.

Contract:
  * preregister one or more committed checkpoints before hidden questions exist;
  * build a fresh 384-question primary population:
        consensus 64 + dictionary-definition 128 + English/code 128 + triad 64;
  * keep relative-candidate correctness separate as a derived secondary diagnostic;
  * construct code questions only from an audited legacy test-manifest pool that
    excludes every discovered historical manifest/probe source path;
  * construct dictionary/English-code questions against a disposable lexical DB clone;
  * fingerprint and seal the complete population before model scoring;
  * abort before scoring on source-path or historical-question overlap;
  * score each preregistered checkpoint once with the current production head path;
  * never load optimizer state, train, backpropagate, fit thresholds, or calibrate on
    hidden gold labels;
  * prove checkpoint-relevant model state and protected experiment files did not change.

After a successful run the sealed population is exposed validation data.  Reuse it
for diagnosis, not for another claim of hidden generalization.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import sys
import tempfile
import warnings
import time
from typing import Any, Iterable, Sequence

from nanojev_dictionary_code_store import DictionaryCodeStore, stable_id
from nanojev_objective_api import ObjectQuestion, question_fingerprint, validate_questions


SCHEMA = "main-computer-nanojev-three-backbone-hidden-holdout-eval-v1"
POPULATION_SCHEMA = "main-computer-nanojev-three-backbone-hidden-population-v1"
RESULT_SCHEMA = "main-computer-nanojev-three-backbone-hidden-result-v1"
BROAD_SCHEMA = "main-computer-nanojev-three-backbone-latent-top2-broad-curriculum-v1"
PARENT_SCHEMA = "main-computer-nanojev-three-backbone-latent-top2-load-balanced-curriculum-v1"
DEFAULT_EXPERIMENT = (
    r"C:\Users\subsi\NanoJev\runs\main_computer_three_backbone_latent_top2_broad_curriculum_v1"
)
DEFAULT_SEED = 20261001
DEFAULT_HIDDEN_CYCLE = 990001
DEFAULT_MAX_PROMPT_TOKENS = 768
DEFAULT_MAX_ANSWER_TOKENS = 128
DEFAULT_RELATIVE_MAX_PROMPT_TOKENS = 1536
DEFAULT_CACHE_QWEN_BATCH = 4
DEFAULT_EVAL_BATCH = 32
DEFAULT_PRECISION = "bf16"
DEFAULT_TRAIN_FILES_PER_CYCLE = 40
DEFAULT_TRIAD_MAX_CODE_TOKENS = 128
DEFAULT_CONSENSUS_MAX_CODE_TOKENS = 128

PRIMARY_PLAN = {
    "consensus": 64,
    "dictionary_definition": 128,
    "english_code": 128,
    "triad": 64,
}
PRIMARY_TOTAL = sum(PRIMARY_PLAN.values())

TOOLS = Path(__file__).resolve().parent
SOURCE_CODE_FILES = (
    "nanojev_three_backbone_hidden_holdout_eval.py",
    "nanojev_three_backbone_latent_top2_broad_curriculum_train.py",
    "nanojev_three_backbone_latent_top2_cutover.py",
    "nanojev_three_backbone_consensus_train.py",
    "nanojev_three_backbone_objective_smoke.py",
    "nanojev_dictionary_definition_curriculum_train.py",
    "nanojev_triad_curriculum_train.py",
    "nanojev_dictionary_code_store.py",
    "nanojev_objective_api.py",
    "nanojev_code_lexeme_data.py",
    "nanojev_code_mutation_train.py",
    "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
    "nanojev_frozen_qwen_ordered_signal_smoke.py",
)


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, sort_keys=True, allow_nan=False), flush=True)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def atomic_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
    os.replace(temp, path)


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def stable_seed(*parts: object) -> int:
    raw = "\0".join(str(part) for part in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def normalize_source_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.casefold()


def row_source_path(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    for key in ("relative", "source_path", "repo_relative_path", "source_group_id"):
        value = row.get(key)
        if value:
            normalized = normalize_source_path(value)
            if normalized:
                return normalized
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in ("source_path", "repo_relative_path", "source_group_id"):
            value = metadata.get(key)
            if value:
                normalized = normalize_source_path(value)
                if normalized:
                    return normalized
    return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise RuntimeError(f"JSONL row is not an object: {path}:{line_number}")
            rows.append(value)
    return rows


def tree_snapshot(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"exists": False}
    if path.is_file():
        stat = path.stat()
        return {
            "exists": True,
            "kind": "file",
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": sha256_file(path),
        }
    files = []
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        stat = child.stat()
        files.append({
            "relative": child.relative_to(path).as_posix(),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "sha256": sha256_file(child),
        })
    return {
        "exists": True,
        "kind": "directory",
        "files": files,
        "tree_sha256": sha256_json([
            {"relative": row["relative"], "size": row["size"], "sha256": row["sha256"]}
            for row in files
        ]),
    }


def protected_filesystem_snapshot(experiment_dir: Path, checkpoints: Sequence[Path]) -> dict[str, Any]:
    experiment_dir = Path(experiment_dir)
    protected: dict[str, Any] = {}
    for name in ("state.json", "experiment.json", "lexical.db", "lexical.db-wal", "lexical.db-shm"):
        path = experiment_dir / name
        protected[str(path)] = tree_snapshot(path)
    for checkpoint in checkpoints:
        checkpoint = Path(checkpoint)
        protected[str(checkpoint)] = tree_snapshot(checkpoint)
    return protected


def model_state_sha256(model) -> str:
    """Hash every state_dict tensor, including persistent buffers, without mutation."""
    import torch

    h = hashlib.sha256()
    state = model.state_dict()
    for name in sorted(state):
        tensor = state[name]
        if not torch.is_tensor(tensor):
            continue
        value = tensor.detach().cpu().contiguous()
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(value.dtype).encode("ascii"))
        h.update(b"\0")
        h.update(json.dumps(list(value.shape), separators=(",", ":")).encode("ascii"))
        h.update(b"\0")
        h.update(value.view(torch.uint8).reshape(-1).numpy().tobytes())
        h.update(b"\0")
    return h.hexdigest()


def clone_sqlite_readonly(source: Path, target: Path) -> None:
    source = Path(source).expanduser().resolve(strict=True)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise RuntimeError(f"disposable lexical DB already exists: {target}")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        dst.close()
        src.close()


def source_code_seal() -> list[dict[str, Any]]:
    rows = []
    for name in SOURCE_CODE_FILES:
        path = (TOOLS / name).resolve(strict=True)
        rows.append({"path": str(path), "sha256": sha256_file(path)})
    return rows


def resolve_candidates(experiment_dir: Path, explicit: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    manifest_path = experiment_dir / "experiment.json"
    state_path = experiment_dir / "state.json"
    manifest = read_json(manifest_path)
    state = read_json(state_path)
    if manifest.get("schema_version") != BROAD_SCHEMA:
        raise RuntimeError(
            f"hidden evaluator requires current broad-curriculum experiment; got {manifest.get('schema_version')}"
        )

    requested = list(explicit)
    if not requested:
        latest = state.get("latest_checkpoint")
        if not latest:
            raise RuntimeError(f"experiment has no committed latest checkpoint: {experiment_dir}")
        requested = [str(latest)]

    experiment_manifest_sha = sha256_file(manifest_path)
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for raw in requested:
        checkpoint = Path(raw).expanduser().resolve(strict=True)
        key = os.path.normcase(str(checkpoint))
        if key in seen:
            raise RuntimeError(f"duplicate checkpoint candidate: {checkpoint}")
        seen.add(key)
        head_path = checkpoint / "head.safetensors"
        meta_path = checkpoint / "meta.json"
        if not head_path.is_file() or not meta_path.is_file():
            raise RuntimeError(f"candidate is not a committed NanoJev checkpoint: {checkpoint}")
        meta = read_json(meta_path)
        if meta.get("schema_version") not in {BROAD_SCHEMA, PARENT_SCHEMA}:
            raise RuntimeError(
                f"candidate checkpoint schema is not compatible with this top-2 model: "
                f"{checkpoint} schema={meta.get('schema_version')}"
            )
        tree = tree_snapshot(checkpoint)
        candidates.append({
            "checkpoint": str(checkpoint),
            "schema_version": meta.get("schema_version"),
            "cycle": int(meta.get("cycle", -1)),
            "global_step": int(meta.get("global_step", -1)),
            "head_safetensors_sha256": sha256_file(head_path),
            "checkpoint_meta_sha256": sha256_file(meta_path),
            "checkpoint_tree_sha256": tree.get("tree_sha256"),
            "experiment_manifest_sha256": experiment_manifest_sha,
            "inherited_head_sha256": meta.get("inherited_head_sha256"),
            "inherited_head_trainable": meta.get("inherited_head_trainable"),
            "fixed_metrics": extract_fixed_metrics(meta),
        })
    return manifest, state, candidates


def extract_fixed_metrics(meta: dict[str, Any]) -> dict[str, Any] | None:
    try:
        evaluation = meta["metrics"]["eval"]
        preservation = evaluation["preservation"]
        by_task = {
            "consensus": evaluation["consensus"],
            "dictionary_definition": preservation["dictionary_definition"],
            "english_code": preservation["english_code"]["overall"],
            "triad": evaluation["triad"],
        }
        total = sum(int(by_task[name]["questions"]) for name in PRIMARY_PLAN)
        correct = sum(
            float(by_task[name]["accuracy"]) * int(by_task[name]["questions"])
            for name in PRIMARY_PLAN
        )
        relative = evaluation.get("relative_candidate", {}).get("overall", {})
        return {
            "primary": {
                "questions": total,
                "correct": int(round(correct)),
                "accuracy": correct / total if total else None,
                "by_task": {
                    name: {
                        "questions": int(by_task[name]["questions"]),
                        "accuracy": float(by_task[name]["accuracy"]),
                    }
                    for name in PRIMARY_PLAN
                },
            },
            "relative_candidate": {
                "questions": relative.get("questions"),
                "accuracy": relative.get("relative_candidate_accuracy"),
            },
        }
    except (KeyError, TypeError, ValueError):
        return None


def known_experiment_dirs(experiment_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    raw_values: list[Any] = [experiment_dir]
    source = dict(manifest.get("source") or {})
    branch_parent = dict(manifest.get("branch_parent") or {})
    for key in ("source_experiment", "probe_experiment", "legacy_experiment"):
        if source.get(key):
            raw_values.append(source[key])
    if branch_parent.get("experiment"):
        raw_values.append(branch_parent["experiment"])

    out: list[Path] = []
    seen: set[str] = set()
    for raw in raw_values:
        try:
            path = Path(str(raw)).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not path.is_dir():
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def discover_manifest_files(known_dirs: Sequence[Path], legacy_meta: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for raw in (legacy_meta.get("manifests") or {}).values():
        try:
            path = Path(str(raw)).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if path.is_file():
            paths.append(path)
    for directory in known_dirs:
        manifests_dir = directory / "manifests"
        if manifests_dir.is_dir():
            paths.extend(sorted(p.resolve() for p in manifests_dir.glob("*.json") if p.is_file()))
    dedup: dict[str, Path] = {}
    for path in paths:
        dedup[os.path.normcase(str(path))] = path
    return sorted(dedup.values(), key=lambda p: str(p).casefold())


def manifest_paths(path: Path) -> set[str]:
    payload = read_json(path)
    if not isinstance(payload, list):
        return set()
    return {source for row in payload if (source := row_source_path(row))}


def discover_probe_files(known_dirs: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in known_dirs:
        probe_dir = directory / "probes"
        if probe_dir.is_dir():
            files.extend(sorted(p.resolve() for p in probe_dir.glob("*.jsonl") if p.is_file()))
    dedup: dict[str, Path] = {}
    for path in files:
        dedup[os.path.normcase(str(path))] = path
    return sorted(dedup.values(), key=lambda p: str(p).casefold())


def build_source_split_audit(*, candidate_manifest_path: Path, manifest_files: Sequence[Path],
                             probe_files: Sequence[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_manifest_path = Path(candidate_manifest_path).expanduser().resolve(strict=True)
    candidate_rows = read_json(candidate_manifest_path)
    if not isinstance(candidate_rows, list) or not candidate_rows:
        raise RuntimeError(f"candidate hidden source manifest is empty: {candidate_manifest_path}")
    candidate_raw_paths = {source for row in candidate_rows if (source := row_source_path(row))}
    if not candidate_raw_paths:
        raise RuntimeError(f"candidate hidden source manifest has no source paths: {candidate_manifest_path}")

    forbidden: set[str] = set()
    manifest_audit: list[dict[str, Any]] = []
    candidate_key = os.path.normcase(str(candidate_manifest_path))
    for path in manifest_files:
        paths = manifest_paths(path)
        is_candidate = os.path.normcase(str(path)) == candidate_key
        raw_overlap = candidate_raw_paths & paths if not is_candidate else set()
        if not is_candidate:
            forbidden.update(paths)
        manifest_audit.append({
            "path": str(path),
            "sha256": sha256_file(path),
            "source_paths": len(paths),
            "candidate_manifest": is_candidate,
            "raw_candidate_overlap": len(raw_overlap),
        })

    probe_audit: list[dict[str, Any]] = []
    canonical_probe_paths: set[str] = set()
    for path in probe_files:
        rows = read_jsonl(path)
        paths = {source for row in rows if (source := row_source_path(row))}
        canonical_probe_paths.update(paths)
        probe_audit.append({
            "path": str(path),
            "sha256": sha256_file(path),
            "source_paths": len(paths),
            "raw_candidate_overlap": len(candidate_raw_paths & paths),
        })
    forbidden.update(canonical_probe_paths)

    eligible_rows = [
        row for row in candidate_rows
        if (source := row_source_path(row)) and source not in forbidden
    ]
    eligible_paths = {row_source_path(row) for row in eligible_rows}
    eligible_paths.discard(None)
    overlap = set(eligible_paths) & forbidden
    if overlap:
        raise RuntimeError(f"internal source-audit failure: {len(overlap)} prohibited paths survived")
    python_rows = [row for row in eligible_rows if str(row.get("language", "")).casefold() == "python"]
    if not python_rows:
        raise RuntimeError("audited hidden source pool contains no Python files")

    excluded = candidate_raw_paths & forbidden
    audit = {
        "candidate_manifest": str(candidate_manifest_path),
        "candidate_manifest_sha256": sha256_file(candidate_manifest_path),
        "candidate_raw_source_paths": len(candidate_raw_paths),
        "candidate_eligible_source_paths": len(eligible_paths),
        "candidate_eligible_python_rows": len(python_rows),
        "excluded_prohibited_source_paths": len(excluded),
        "excluded_prohibited_source_path_set_sha256": sha256_json(sorted(excluded)),
        "manifests_examined": manifest_audit,
        "canonical_probe_files_examined": probe_audit,
        "total_prohibited_path_overlap": len(overlap),
        "invariant": "every code source available to hidden consensus/triad generation is absent from every discovered historical manifest and canonical probe source path",
    }
    return eligible_rows, audit


def load_historical_questions(*, known_dirs: Sequence[Path], direct, ordered_api,
                              dictionary_curriculum, curriculum, repo_root: Path) -> tuple[list[ObjectQuestion], list[dict[str, Any]]]:
    questions: list[ObjectQuestion] = []
    sources: list[dict[str, Any]] = []
    seen_files: set[str] = set()

    # Persisted current-generation holdouts are strongest because they preserve the
    # exact question content rather than requiring reconstruction.
    for directory in known_dirs:
        for path in sorted(directory.rglob("*holdout*.json")):
            if not path.is_file():
                continue
            key = os.path.normcase(str(path.resolve()))
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                payload = read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            raw_questions = payload.get("questions") if isinstance(payload, dict) else None
            if not isinstance(raw_questions, list) or not raw_questions:
                continue
            parsed: list[ObjectQuestion] = []
            try:
                for row in raw_questions:
                    if isinstance(row, dict) and {"question_id", "task", "candidates", "gold_index"}.issubset(row):
                        parsed.append(dictionary_curriculum.question_from_dict(row))
            except Exception:
                parsed = []
            if parsed:
                validate_questions(parsed)
                questions.extend(parsed)
                sources.append({
                    "kind": "persisted_holdout",
                    "path": str(path.resolve()),
                    "sha256": sha256_file(path),
                    "questions": len(parsed),
                })

    # Canonical probe files are reconstructed through the same objectizer used by
    # the current trainer, so their fingerprints are directly comparable.
    reverse_probe_files = {str(filename): str(task) for task, filename in direct.PROBE_FILES.items()}
    for directory in known_dirs:
        probe_dir = directory / "probes"
        if not probe_dir.is_dir():
            continue
        for path in sorted(p for p in probe_dir.glob("*.jsonl") if p.is_file()):
            task = reverse_probe_files.get(path.name)
            if task is None:
                continue
            try:
                raw = direct.read_jsonl(path)
                parsed = direct.objectize(task, raw, repo_root=repo_root, ordered_api=ordered_api)
                validate_questions(parsed)
            except Exception as exc:
                sources.append({
                    "kind": "canonical_probe_unparsed",
                    "path": str(path.resolve()),
                    "sha256": sha256_file(path),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                continue
            questions.extend(parsed)
            sources.append({
                "kind": "canonical_probe",
                "task": task,
                "path": str(path.resolve()),
                "sha256": sha256_file(path),
                "questions": len(parsed),
            })

    # Previous one-shot hidden runs are exposed data.  Pull their sealed fingerprints
    # into the historical set even when the question payload is not reconstructed.
    return questions, sources


def discover_prior_hidden_fingerprints(experiment_dir: Path, current_output: Path) -> tuple[set[str], list[dict[str, Any]]]:
    parent = Path(experiment_dir).parent
    fingerprints: set[str] = set()
    sources: list[dict[str, Any]] = []
    if not parent.is_dir():
        return fingerprints, sources
    for path in sorted(parent.glob("*/hidden_holdout_manifest.json")):
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        try:
            if current_output in resolved.parents:
                continue
        except Exception:
            pass
        try:
            payload = read_json(resolved)
        except Exception:
            continue
        if payload.get("schema_version") != SCHEMA:
            continue
        current = set(payload.get("primary_fingerprints") or ()) | set(payload.get("relative_fingerprints") or ())
        fingerprints.update(str(value) for value in current)
        sources.append({
            "kind": "prior_hidden_manifest",
            "path": str(resolved),
            "sha256": sha256_file(resolved),
            "fingerprints": len(current),
        })
    return fingerprints, sources


def serialize_question(dictionary_curriculum, question: ObjectQuestion) -> dict[str, Any]:
    # Serialize the generic object-question contract. Legacy consensus/triad
    # objectizers may omit the optional objective-owned ``stratum`` attribute.
    return {
        "question_id": str(question.question_id),
        "task": str(question.task),
        "stratum": str(getattr(question, "stratum", "") or ""),
        "gold_index": int(question.gold_index),
        "candidates": [
            {
                "candidate_id": str(candidate.candidate_id),
                "paths": [
                    {"prompt": str(path.prompt), "answer": str(path.answer)}
                    for path in candidate.paths
                ],
            }
            for candidate in question.candidates
        ],
    }


def harvest_audited_code_snippets(*, store: DictionaryCodeStore, repo_root: Path,
                                  eligible_manifest: Sequence[dict[str, Any]],
                                  minimum_eligible: int, rng: random.Random) -> dict[str, Any]:
    """Populate only a bounded hidden code reserve from audited Python files.

    The original hidden evaluator walked every eligible Python row and inserted every
    usable statement even though English/code needs only 32 code objects.  That made
    population construction scale with the entire test manifest.  This version scans
    a deterministic seed-shuffled subset and stops as soon as a small reserve exists.
    """
    repo_root = Path(repo_root).resolve(strict=True)
    eligible_paths = {
        source for row in eligible_manifest if (source := row_source_path(row))
    }
    if minimum_eligible <= 0:
        return {
            "target_eligible_unused": 0,
            "eligible_unused_before": 0,
            "eligible_unused_after": 0,
            "files_scanned": 0,
            "snippets_inserted": 0,
        }

    def unused_eligible_count() -> int:
        return sum(
            1
            for row in store.conn.execute(
                """
                SELECT source_path
                FROM code_snippets
                WHERE train_count=0 AND eval_count=0
                """
            ).fetchall()
            if normalize_source_path(row["source_path"]) in eligible_paths
        )

    before = unused_eligible_count()
    if before >= minimum_eligible:
        return {
            "target_eligible_unused": int(minimum_eligible),
            "eligible_unused_before": before,
            "eligible_unused_after": before,
            "files_scanned": 0,
            "snippets_inserted": 0,
        }

    # De-duplicate paths before shuffling so a manifest with repeated rows cannot make
    # us parse the same source file repeatedly.  The shuffle is seed-controlled by the
    # caller and therefore reproducible without being biased toward lexical path order.
    candidate_paths: list[str] = []
    seen_paths: set[str] = set()
    for row in eligible_manifest:
        if str(row.get("language", "")).casefold() != "python":
            continue
        source_path = row_source_path(row)
        if not source_path or source_path in seen_paths:
            continue
        seen_paths.add(source_path)
        candidate_paths.append(source_path)
    rng.shuffle(candidate_paths)

    eligible_unused = before
    inserted = 0
    files_scanned = 0
    for relative in candidate_paths:
        path = repo_root / Path(relative)
        try:
            if path.stat().st_size > 768 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            # Invalid escape sequences inside old source strings are irrelevant to AST
            # statement harvesting and otherwise flood the console with <unknown> warnings.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(text)
        except (OSError, SyntaxError, UnicodeError):
            continue
        files_scanned += 1
        lines = text.splitlines()
        candidates: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue
            if isinstance(node, ast.Expr):
                value = getattr(node, "value", None)
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    continue
            start = int(getattr(node, "lineno", 0) or 0)
            end = int(getattr(node, "end_lineno", 0) or 0)
            if start <= 0 or end < start or end - start > 12:
                continue
            snippet = "\n".join(lines[start - 1:end]).strip()
            if not 16 <= len(snippet) <= 320:
                continue
            if not any(ch in snippet for ch in "()[]{}=:.+"):
                continue
            candidates.append((start, snippet))
        for start, snippet in sorted(set(candidates)):
            snippet_id = stable_id(relative, str(start), snippet)
            cur = store.conn.execute(
                "INSERT OR IGNORE INTO code_snippets(snippet_id, source_path, start_line, snippet_text) "
                "VALUES(?, ?, ?, ?)",
                (snippet_id, relative, start, snippet),
            )
            if cur.rowcount > 0:
                inserted += 1
                eligible_unused += 1
                if eligible_unused >= minimum_eligible:
                    store.conn.commit()
                    return {
                        "target_eligible_unused": int(minimum_eligible),
                        "eligible_unused_before": before,
                        "eligible_unused_after": eligible_unused,
                        "files_scanned": files_scanned,
                        "snippets_inserted": inserted,
                    }
    store.conn.commit()
    return {
        "target_eligible_unused": int(minimum_eligible),
        "eligible_unused_before": before,
        "eligible_unused_after": eligible_unused,
        "files_scanned": files_scanned,
        "snippets_inserted": inserted,
    }

def strict_hidden_english_rows(*, store: DictionaryCodeStore, repo_root: Path,
                               eligible_manifest: Sequence[dict[str, Any]], count: int,
                               rng: random.Random, harvest_rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Select English/code objects never used for train *or* prior eval.

    The stock continual-eval helpers intentionally permit reuse of prior eval rows.
    A one-shot hidden holdout cannot: both definitions and code snippets must have
    train_count=0 and eval_count=0, and code snippets must come only from the audited
    hidden source-file pool.
    """
    if count <= 0:
        return [], [], {
            "target_eligible_unused": 0,
            "eligible_unused_before": 0,
            "eligible_unused_after": 0,
            "files_scanned": 0,
            "snippets_inserted": 0,
        }
    eligible_paths = {
        source for row in eligible_manifest if (source := row_source_path(row))
    }
    definition_rows = store.conn.execute(
        """
        SELECT definition_id, definition_text, pos
        FROM definitions
        WHERE train_count=0 AND eval_count=0
        ORDER BY definition_id
        """
    ).fetchall()
    if len(definition_rows) < count:
        raise RuntimeError(
            f"not enough never-trained/never-evaluated definitions for hidden English/code: "
            f"requested={count} available={len(definition_rows)}"
        )
    definitions = rng.sample(list(definition_rows), count)

    # Two code candidates per required object gives the length matcher useful choice
    # without turning a 32-object hidden sample into a whole-repository AST crawl.
    harvest_target = max(count, count * 2)
    harvest_stats = harvest_audited_code_snippets(
        store=store,
        repo_root=repo_root,
        eligible_manifest=eligible_manifest,
        minimum_eligible=harvest_target,
        rng=harvest_rng,
    )
    code_rows = [
        row for row in store.conn.execute(
            """
            SELECT * FROM code_snippets
            WHERE train_count=0 AND eval_count=0
            ORDER BY snippet_id
            """
        ).fetchall()
        if normalize_source_path(row["source_path"]) in eligible_paths
    ]
    if len(code_rows) < count:
        raise RuntimeError(
            "not enough never-trained/never-evaluated code snippets inside the audited hidden source pool: "
            f"requested={count} available={len(code_rows)} eligible_source_paths={len(eligible_paths)}"
        )
    target_lengths = [len(str(row["definition_text"])) for row in definitions]
    chosen_code = store._choose_length_matched_code(code_rows, target_lengths, rng)

    with store.transaction():
        store.conn.executemany(
            "UPDATE definitions SET eval_count=eval_count+1 WHERE definition_id=?",
            ((str(row["definition_id"]),) for row in definitions),
        )
        store.conn.executemany(
            "UPDATE code_snippets SET eval_count=eval_count+1 WHERE snippet_id=?",
            ((str(row["snippet_id"]),) for row in chosen_code),
        )
    return [dict(row) for row in definitions], [dict(row) for row in chosen_code], harvest_stats


def build_population(*, manifest: dict[str, Any], eligible_manifest: Sequence[dict[str, Any]],
                     tokenizer, modules: dict[str, Any], seed: int, hidden_cycle: int,
                     lexical_clone: Path) -> tuple[list[ObjectQuestion], list[ObjectQuestion], dict[str, Any]]:
    curriculum = modules["curriculum"]
    smoke = modules["smoke"]
    direct = modules["direct"]
    dictionary_curriculum = modules["dictionary_curriculum"]
    triad_curriculum = modules["triad_curriculum"]
    data = modules["data"]
    mutation = modules["mutation"]
    source_sampler = modules["source_sampler"]
    ordered_api = modules["ordered_api"]

    source = dict(manifest["source"])
    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)

    with DictionaryCodeStore(lexical_clone) as store:
        consensus_objective = curriculum.ConsensusObjective(
            direct=direct,
            source_sampler=source_sampler,
            data=data,
            mutation=mutation,
            ordered_api=ordered_api,
            tokenizer=tokenizer,
            repo_root=repo_root,
            train_manifest=eligible_manifest,
            max_length=int(legacy_meta["max_length"]),
            max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
            max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
            train_files_per_cycle=DEFAULT_TRAIN_FILES_PER_CYCLE,
            max_code_tokens=DEFAULT_CONSENSUS_MAX_CODE_TOKENS,
            seed=seed,
        )
        triad_objective = triad_curriculum.TriadObjective(
            direct=direct,
            source_sampler=source_sampler,
            data=data,
            mutation=mutation,
            ordered_api=ordered_api,
            tokenizer=tokenizer,
            repo_root=repo_root,
            train_manifest=eligible_manifest,
            max_length=int(legacy_meta["max_length"]),
            max_prompt_tokens=DEFAULT_MAX_PROMPT_TOKENS,
            max_answer_tokens=DEFAULT_MAX_ANSWER_TOKENS,
            train_files_per_cycle=DEFAULT_TRAIN_FILES_PER_CYCLE,
            max_code_tokens=DEFAULT_TRIAD_MAX_CODE_TOKENS,
            seed=seed,
        )
        dictionary_objective = dictionary_curriculum.DictionaryDefinitionObjective(store)
        english_objective = smoke.EnglishCodeObjective(store, repo_root)

        rngs = {
            name: random.Random(stable_seed(seed, hidden_cycle, name, "hidden-primary"))
            for name in PRIMARY_PLAN
        }
        primary_by_task = {
            "consensus": consensus_objective.generate_eval(
                count=PRIMARY_PLAN["consensus"], cycle=hidden_cycle, rng=rngs["consensus"]
            ),
            "dictionary_definition": dictionary_objective.generate_eval(
                count=PRIMARY_PLAN["dictionary_definition"], cycle=hidden_cycle,
                rng=rngs["dictionary_definition"],
            ),
            "english_code": [],
            "triad": triad_objective.generate_eval(
                count=PRIMARY_PLAN["triad"], cycle=hidden_cycle, rng=rngs["triad"]
            ),
        }
        english_objects_per_class = PRIMARY_PLAN["english_code"] // 4
        hidden_code_harvest_target = max(english_objects_per_class, english_objects_per_class * 2)
        emit(
            "hidden_code_reserve_harvest_start",
            required_code_objects=english_objects_per_class,
            target_eligible_unused=hidden_code_harvest_target,
            eligible_python_rows=sum(
                1 for row in eligible_manifest if str(row.get("language", "")).casefold() == "python"
            ),
        )
        hidden_definitions, hidden_code, hidden_code_harvest = strict_hidden_english_rows(
            store=store,
            repo_root=repo_root,
            eligible_manifest=eligible_manifest,
            count=english_objects_per_class,
            rng=rngs["english_code"],
            harvest_rng=random.Random(stable_seed(seed, hidden_cycle, "english_code_harvest", "hidden-primary")),
        )
        emit("hidden_code_reserve_harvested", **hidden_code_harvest)
        primary_by_task["english_code"] = english_objective._questions(
            definitions=hidden_definitions,
            code=hidden_code,
            cycle=hidden_cycle,
            split="hidden",
        )
        hidden_code_source_paths = sorted({normalize_source_path(row["source_path"]) for row in hidden_code})
        eligible_paths = {source for row in eligible_manifest if (source := row_source_path(row))}
        unexpected_code_paths = set(hidden_code_source_paths) - eligible_paths
        if unexpected_code_paths:
            raise RuntimeError(
                f"hidden English/code selected code from prohibited source pool: {sorted(unexpected_code_paths)[:5]}"
            )

        lexical_stats_after = store.stats()
        lexical_reserve_after = store.reserve_counts()

    for task, expected in PRIMARY_PLAN.items():
        actual = len(primary_by_task[task])
        if actual != expected:
            raise RuntimeError(f"hidden {task} count changed: {actual} != {expected}")
    primary = [question for task in PRIMARY_PLAN for question in primary_by_task[task]]
    validate_questions(primary)
    if len(primary) != PRIMARY_TOTAL:
        raise RuntimeError(f"hidden primary population changed: {len(primary)} != {PRIMARY_TOTAL}")

    relative: list[ObjectQuestion] = []
    for source_question in primary:
        for candidate_index in range(len(source_question.candidates)):
            relative.append(curriculum.build_relative_eval_variant(
                question=source_question,
                candidate_index=candidate_index,
            ))
    validate_questions(relative)

    generation = {
        "seed": int(seed),
        "hidden_cycle": int(hidden_cycle),
        "primary_plan": dict(PRIMARY_PLAN),
        "primary_questions": len(primary),
        "relative_candidate_variants": len(relative),
        "strict_hidden_english_code": {
            "definition_objects": english_objects_per_class,
            "code_objects": english_objects_per_class,
            "bounded_audited_code_harvest": hidden_code_harvest,
            "definition_requirement": "train_count=0 AND eval_count=0",
            "code_requirement": "train_count=0 AND eval_count=0 AND source_path in audited hidden source pool",
            "selected_code_source_paths": hidden_code_source_paths,
        },
        "lexical_clone_stats_after_generation": lexical_stats_after,
        "lexical_clone_reserve_after_generation": lexical_reserve_after,
        "relative_contract": (
            "derived from the sealed primary questions; every candidate receives an Is this candidate correct? "
            "probe; relative accuracy is reported separately from the 384-question primary denominator"
        ),
    }
    return primary, relative, generation


def historical_fingerprint_audit(*, primary: Sequence[ObjectQuestion], relative: Sequence[ObjectQuestion],
                                 historical_questions: Sequence[ObjectQuestion], curriculum,
                                 prior_hidden: set[str], historical_sources: Sequence[dict[str, Any]],
                                 prior_sources: Sequence[dict[str, Any]]) -> dict[str, Any]:
    primary_fps = [question_fingerprint(question) for question in primary]
    relative_fps = [question_fingerprint(question) for question in relative]
    if len(set(primary_fps)) != len(primary_fps):
        raise RuntimeError("hidden primary population contains duplicate content fingerprints")
    if len(set(relative_fps)) != len(relative_fps):
        raise RuntimeError("hidden relative population contains duplicate content fingerprints")

    historical_fps: set[str] = {question_fingerprint(question) for question in historical_questions}
    # Historical relative variants are derivable from every historical primary question.
    for question in historical_questions:
        for candidate_index in range(len(question.candidates)):
            try:
                variant = curriculum.build_relative_eval_variant(
                    question=question, candidate_index=candidate_index
                )
            except Exception:
                continue
            historical_fps.add(question_fingerprint(variant))
    historical_fps.update(prior_hidden)

    primary_overlap = set(primary_fps) & historical_fps
    relative_overlap = set(relative_fps) & historical_fps
    if primary_overlap or relative_overlap:
        raise RuntimeError(
            "hidden population overlaps historical/exposed questions: "
            f"primary={len(primary_overlap)} relative={len(relative_overlap)}"
        )
    return {
        "historical_question_sources": [*historical_sources, *prior_sources],
        "historical_fingerprints": len(historical_fps),
        "hidden_primary_fingerprints": len(primary_fps),
        "hidden_relative_fingerprints": len(relative_fps),
        "historical_primary_fingerprint_overlap": 0,
        "historical_relative_fingerprint_overlap": 0,
        "invariant": "no sealed primary or derived relative question fingerprint has appeared in discovered historical/exposed evaluation data",
    }


def score_cached_rows(*, model, cached_questions: Sequence, source_questions: Sequence[ObjectQuestion],
                      batch_questions: int) -> list[dict[str, Any]]:
    import torch

    source_by_id = {question.question_id: question for question in source_questions}
    rows: list[dict[str, Any]] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(cached_questions), batch_questions):
            batch = list(cached_questions[start:start + batch_questions])
            logits, _ = model.score_cached_questions(batch)
            for index, cached in enumerate(batch):
                source = source_by_id.get(cached.question_id)
                if source is None:
                    raise RuntimeError(f"cached primary question lost source row: {cached.question_id}")
                count = len(cached.candidate_ids)
                scores = logits[index, :count].detach().float()
                probabilities = scores.softmax(-1)
                predicted_index = int(scores.argmax().item())
                gold_index = int(cached.gold_index)
                sorted_scores = torch.sort(scores, descending=True).values
                margin = float((sorted_scores[0] - sorted_scores[1]).item()) if count >= 2 else None
                rows.append({
                    "question_id": source.question_id,
                    "fingerprint": question_fingerprint(source),
                    "task": source.task,
                    "stratum": str(getattr(source, "stratum", "") or ""),
                    "candidate_ids": list(cached.candidate_ids),
                    "gold_index": gold_index,
                    "gold_candidate_id": str(cached.candidate_ids[gold_index]),
                    "predicted_index": predicted_index,
                    "predicted_candidate_id": str(cached.candidate_ids[predicted_index]),
                    "correct": predicted_index == gold_index,
                    "logits": [float(value) for value in scores.tolist()],
                    "probabilities": [float(value) for value in probabilities.tolist()],
                    "top1_margin": margin,
                    "gold_nll": -math.log(max(float(probabilities[gold_index].item()), 1e-30)),
                })
    return rows


def summarize_primary_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def summarize(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
        total = len(group)
        correct = sum(bool(row["correct"]) for row in group)
        return {
            "questions": total,
            "correct": correct,
            "errors": total - correct,
            "accuracy": correct / total if total else None,
            "mean_nll": sum(float(row["gold_nll"]) for row in group) / total if total else None,
        }

    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task"])].append(row)
        if row.get("stratum"):
            by_stratum[f"{row['task']}::{row['stratum']}"] .append(row)
    overall = summarize(rows)
    overall["by_task"] = {task: summarize(group) for task, group in sorted(by_task.items())}
    overall["by_stratum"] = {name: summarize(group) for name, group in sorted(by_stratum.items())}
    return overall


def summarize_relative_rows_direct(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def summarize(group: Sequence[dict[str, Any]]) -> dict[str, Any]:
        total = len(group)
        correct = sum(bool(row["relative_correct"]) for row in group)
        primary_correct = sum(bool(row["primary_correct"]) for row in group)
        disagreements = sum(bool(row["disagree"]) for row in group)
        corrections = sum(
            (not bool(row["primary_correct"])) and bool(row["relative_correct"])
            for row in group
        )
        regressions = sum(
            bool(row["primary_correct"]) and (not bool(row["relative_correct"]))
            for row in group
        )
        return {
            "questions": total,
            "relative_correct": correct,
            "relative_candidate_accuracy": correct / total if total else None,
            "primary_accuracy_same_population": primary_correct / total if total else None,
            "disagreements": disagreements,
            "corrections": corrections,
            "regressions": regressions,
            "relative_ties": sum(bool(row["relative_tie"]) for row in group),
        }

    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row["task"])].append(row)
    overall = summarize(rows)
    overall["by_task"] = {task: summarize(group) for task, group in sorted(by_task.items())}
    overall["contract"] = {
        "secondary_only": True,
        "decision_rule": "choose the candidate with the largest relative verifier YES-minus-NO evidence",
        "threshold_sweep": False,
        "calibration": False,
        "included_in_primary_384_denominator": False,
    }
    return overall


def cache_sealed_population(*, direct, model, tokenizer, primary: Sequence[ObjectQuestion],
                            relative: Sequence[ObjectQuestion], precision: str,
                            max_prompt_tokens: int, relative_max_prompt_tokens: int,
                            max_answer_tokens: int, cache_batch: int) -> tuple[list, dict, dict[str, Any]]:
    filtered_primary, primary_filter = direct.filter_bounded_questions(
        primary,
        tokenizer,
        max_prompt_tokens=max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    if len(filtered_primary) != len(primary):
        raise RuntimeError(
            f"sealed primary population was filtered after sealing: {len(filtered_primary)} != {len(primary)}; "
            f"stats={primary_filter}"
        )
    primary_cached, primary_cache = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=filtered_primary,
        max_prompt_tokens=max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=precision,
        qwen_batch_questions=cache_batch,
    )

    filtered_relative, relative_filter = direct.filter_bounded_questions(
        relative,
        tokenizer,
        max_prompt_tokens=relative_max_prompt_tokens,
        max_answer_tokens=max_answer_tokens,
    )
    if len(filtered_relative) != len(relative):
        raise RuntimeError(
            f"sealed relative population was filtered after sealing: {len(filtered_relative)} != {len(relative)}; "
            f"stats={relative_filter}"
        )
    relative_cached, relative_cache = direct.materialize_cached_questions(
        model=model,
        tokenizer=tokenizer,
        questions=filtered_relative,
        max_prompt_tokens=relative_max_prompt_tokens,
        pad_token_id=int(tokenizer.pad_token_id),
        precision=precision,
        qwen_batch_questions=cache_batch,
    )

    source_key_by_variant_qid: dict[str, tuple[str, int]] = {}
    for source in primary:
        for candidate_index in range(len(source.candidates)):
            # Must exactly reproduce the variant already sealed in `relative`.
            digest = hashlib.sha256(
                f"relative-eval\0{source.question_id}\0{candidate_index}".encode("utf-8")
            ).hexdigest()[:20]
            source_key_by_variant_qid[f"relative-eval:{digest}"] = (source.question_id, candidate_index)
    relative_cached_by_key = {
        source_key_by_variant_qid[cached.question_id]: cached for cached in relative_cached
    }
    expected_relative = sum(len(question.candidates) for question in primary)
    if len(relative_cached_by_key) != expected_relative:
        raise RuntimeError(
            f"relative cache key mapping changed: {len(relative_cached_by_key)} != {expected_relative}"
        )
    return primary_cached, relative_cached_by_key, {
        "primary": {**primary_cache, "filter": primary_filter},
        "relative": {**relative_cache, "filter": relative_filter},
    }


def load_generation_modules() -> dict[str, Any]:
    return {
        "broad": load_local_module(
            "nanojev_hidden_broad_trainer", TOOLS / "nanojev_three_backbone_latent_top2_broad_curriculum_train.py"
        ),
        "curriculum": load_local_module(
            "nanojev_hidden_consensus_curriculum", TOOLS / "nanojev_three_backbone_consensus_train.py"
        ),
        "smoke": load_local_module(
            "nanojev_hidden_objective_smoke", TOOLS / "nanojev_three_backbone_objective_smoke.py"
        ),
        "direct": load_local_module(
            "nanojev_hidden_top2_direct", TOOLS / "nanojev_three_backbone_latent_top2_cutover.py"
        ),
        "dictionary_curriculum": load_local_module(
            "nanojev_hidden_dictionary_curriculum", TOOLS / "nanojev_dictionary_definition_curriculum_train.py"
        ),
        "triad_curriculum": load_local_module(
            "nanojev_hidden_triad_curriculum", TOOLS / "nanojev_triad_curriculum_train.py"
        ),
        "data": load_local_module(
            "nanojev_hidden_lexeme_data", TOOLS / "nanojev_code_lexeme_data.py"
        ),
        "mutation": load_local_module(
            "nanojev_hidden_mutation", TOOLS / "nanojev_code_mutation_train.py"
        ),
        "source_sampler": load_local_module(
            "nanojev_hidden_source_sampler",
            TOOLS / "nanojev_code_sparse_register_k1000_s_first_r2_full_head_dictionary_train.py",
        ),
        "ordered_api": load_local_module(
            "nanojev_hidden_ordered_api", TOOLS / "nanojev_frozen_qwen_ordered_signal_smoke.py"
        ),
    }


def prepare_output_dir(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    if path.exists():
        if not path.is_dir():
            raise RuntimeError(f"hidden output path exists and is not a directory: {path}")
        if any(path.iterdir()):
            raise RuntimeError(
                f"hidden output directory must be new/empty; refusing to rescore an exposed set: {path}"
            )
    else:
        path.mkdir(parents=True, exist_ok=False)
    return path



CANDIDATE_SEAL_KEYS = (
    "checkpoint",
    "schema_version",
    "cycle",
    "global_step",
    "head_safetensors_sha256",
    "checkpoint_meta_sha256",
    "checkpoint_tree_sha256",
    "experiment_manifest_sha256",
    "inherited_head_sha256",
    "inherited_head_trainable",
)


def candidate_seal_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in CANDIDATE_SEAL_KEYS}


def load_sealed_resume(*, experiment_dir: Path, output_dir: Path, explicit_checkpoints: Sequence[str],
                       expected_manifest_sha256: str | None, dictionary_curriculum) -> tuple[
                           dict[str, Any], list[dict[str, Any]], list[ObjectQuestion],
                           list[ObjectQuestion], str, str
                       ]:
    """Resume scoring from an already sealed, never-scored hidden population.

    This path exists specifically for evaluator failures after ``hidden_holdout_sealed``.
    It never regenerates questions.  The sealed manifest and population are treated as
    immutable inputs; only the evaluator itself is allowed to have changed, because a
    bug fix is what makes resumption necessary.
    """
    experiment_dir = Path(experiment_dir).expanduser().resolve(strict=True)
    output_dir = Path(output_dir).expanduser().resolve(strict=True)
    manifest_path = output_dir / "hidden_holdout_manifest.json"
    population_path = output_dir / "hidden_holdout_population.json"
    rows_path = output_dir / "hidden_holdout_rows.jsonl"
    result_path = output_dir / "hidden_holdout_result.json"
    if not manifest_path.is_file() or not population_path.is_file():
        raise RuntimeError(
            f"--resume-sealed requires existing sealed manifest and population in {output_dir}"
        )
    if rows_path.exists() or result_path.exists():
        raise RuntimeError(
            "refusing --resume-sealed because scoring output already exists; "
            "this mode is only for a sealed population whose scoring never completed"
        )

    sealed_manifest_sha = sha256_file(manifest_path)
    if expected_manifest_sha256:
        expected = str(expected_manifest_sha256).strip().casefold()
        if sealed_manifest_sha.casefold() != expected:
            raise RuntimeError(
                "sealed manifest SHA-256 does not match the preregistered console value: "
                f"expected={expected} actual={sealed_manifest_sha}"
            )
    sealed = read_json(manifest_path)
    if sealed.get("schema_version") != SCHEMA:
        raise RuntimeError(f"sealed manifest schema mismatch: {sealed.get('schema_version')}")
    if sealed.get("status_at_seal") != "sealed_unexposed":
        raise RuntimeError(f"sealed manifest is not resumable: status={sealed.get('status_at_seal')}")
    sealed_experiment = Path(str(sealed.get("experiment"))).expanduser().resolve(strict=True)
    if os.path.normcase(str(sealed_experiment)) != os.path.normcase(str(experiment_dir)):
        raise RuntimeError(
            f"sealed manifest belongs to another experiment: {sealed_experiment} != {experiment_dir}"
        )
    sealed_population_path = Path(str(sealed.get("population_file"))).expanduser().resolve(strict=True)
    if os.path.normcase(str(sealed_population_path)) != os.path.normcase(str(population_path.resolve(strict=True))):
        raise RuntimeError(
            f"sealed population path changed: {sealed_population_path} != {population_path}"
        )
    population_sha = sha256_file(population_path)
    if population_sha != str(sealed.get("population_sha256")):
        raise RuntimeError(
            "sealed population SHA-256 changed: "
            f"manifest={sealed.get('population_sha256')} actual={population_sha}"
        )
    source_audit = sealed.get("source_split_audit") or {}
    fingerprint_audit = sealed.get("fingerprint_audit") or {}
    if int(source_audit.get("total_prohibited_path_overlap", -1)) != 0:
        raise RuntimeError("sealed source audit did not prove zero prohibited overlap")
    if int(fingerprint_audit.get("historical_primary_fingerprint_overlap", -1)) != 0:
        raise RuntimeError("sealed primary fingerprint audit did not prove zero overlap")
    if int(fingerprint_audit.get("historical_relative_fingerprint_overlap", -1)) != 0:
        raise RuntimeError("sealed relative fingerprint audit did not prove zero overlap")

    sealed_candidates = list(sealed.get("candidates") or ())
    if not sealed_candidates:
        raise RuntimeError("sealed manifest has no preregistered checkpoint candidates")
    sealed_checkpoint_paths = [str(row["checkpoint"]) for row in sealed_candidates]
    if explicit_checkpoints:
        _manifest_explicit, _state_explicit, explicit_rows = resolve_candidates(
            experiment_dir, explicit_checkpoints
        )
        if [candidate_seal_identity(row) for row in explicit_rows] != [
            candidate_seal_identity(row) for row in sealed_candidates
        ]:
            raise RuntimeError(
                "--checkpoint candidates do not exactly match the candidates preregistered in the sealed manifest"
            )
    experiment_manifest, _state, current_candidates = resolve_candidates(
        experiment_dir, sealed_checkpoint_paths
    )
    if [candidate_seal_identity(row) for row in current_candidates] != [
        candidate_seal_identity(row) for row in sealed_candidates
    ]:
        raise RuntimeError("a preregistered checkpoint changed after the hidden population was sealed")

    sealed_source_rows = list(sealed.get("source_code_seal") or ())
    source_mismatches: list[dict[str, str]] = []
    evaluator_name = Path(__file__).name
    for row in sealed_source_rows:
        path = Path(str(row.get("path"))).expanduser().resolve(strict=True)
        expected_sha = str(row.get("sha256"))
        actual_sha = sha256_file(path)
        if actual_sha == expected_sha:
            continue
        if path.name == evaluator_name:
            source_mismatches.append({
                "path": str(path),
                "sealed_sha256": expected_sha,
                "current_sha256": actual_sha,
                "allowed_reason": "resume bug fix to evaluator only",
            })
            continue
        raise RuntimeError(
            "non-evaluator source code changed after hidden seal: "
            f"{path} sealed={expected_sha} current={actual_sha}"
        )

    payload = read_json(population_path)
    if payload.get("schema_version") != POPULATION_SCHEMA:
        raise RuntimeError(f"sealed population schema mismatch: {payload.get('schema_version')}")
    primary = [dictionary_curriculum.question_from_dict(row) for row in payload.get("primary") or ()]
    relative = [
        dictionary_curriculum.question_from_dict(row)
        for row in payload.get("relative_variants") or ()
    ]
    validate_questions(primary)
    validate_questions(relative)
    if len(primary) != PRIMARY_TOTAL:
        raise RuntimeError(f"sealed primary denominator changed: {len(primary)} != {PRIMARY_TOTAL}")
    expected_relative = sum(len(question.candidates) for question in primary)
    if len(relative) != expected_relative:
        raise RuntimeError(
            f"sealed relative population changed: {len(relative)} != {expected_relative}"
        )
    primary_fps = [question_fingerprint(question) for question in primary]
    relative_fps = [question_fingerprint(question) for question in relative]
    if primary_fps != list(sealed.get("primary_fingerprints") or ()):
        raise RuntimeError("sealed primary fingerprints do not reproduce from the population file")
    if relative_fps != list(sealed.get("relative_fingerprints") or ()):
        raise RuntimeError("sealed relative fingerprints do not reproduce from the population file")

    emit(
        "hidden_holdout_resume_verified",
        manifest=str(manifest_path),
        manifest_sha256=sealed_manifest_sha,
        population=str(population_path),
        population_sha256=population_sha,
        primary_questions=len(primary),
        relative_variants=len(relative),
        candidates=len(current_candidates),
        evaluator_source_mismatches=source_mismatches,
    )
    return experiment_manifest, current_candidates, primary, relative, sealed_manifest_sha, population_sha


def score_sealed_run(*, args, experiment_dir: Path, candidates: Sequence[dict[str, Any]],
                     modules: dict[str, Any], source: dict[str, Any], tokenizer,
                     primary: Sequence[ObjectQuestion], relative: Sequence[ObjectQuestion],
                     sealed_manifest_sha: str, population_sha: str, manifest_path: Path,
                     population_path: Path, rows_path: Path, result_path: Path) -> None:
    direct = modules["direct"]
    curriculum = modules["curriculum"]
    # Nothing below this line is allowed to change the sealed population or candidate set.
    checkpoint_paths = [Path(row["checkpoint"]) for row in candidates]
    filesystem_before = protected_filesystem_snapshot(experiment_dir, checkpoint_paths)

    broad = modules["broad"]
    loaded = broad.load_model(
        direct=direct,
        smoke=modules["smoke"],
        source=source,
        tools_dir=TOOLS,
        max_answer_tokens=args.max_answer_tokens,
        router_lr=2e-5,              # optimizer is constructed for helper compatibility only; never restored/used
        weight_decay=0.01,
        local_files_only=args.local_files_only,
        precision=args.precision,
        disable_native_triton=args.disable_native_triton,
    )
    model = loaded["model"]
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)

    # Cache only sealed question evidence.  The cache depends on the frozen language-model
    # backbones; the source cutover state is hashed before/after to prove caching is read-only.
    cache_state_before = model_state_sha256(model)
    primary_cached, relative_cached_by_key, cache_stats = cache_sealed_population(
        direct=direct,
        model=model,
        tokenizer=tokenizer,
        primary=primary,
        relative=relative,
        precision=args.precision,
        max_prompt_tokens=args.max_prompt_tokens,
        relative_max_prompt_tokens=args.relative_max_prompt_tokens,
        max_answer_tokens=args.max_answer_tokens,
        cache_batch=args.cache_qwen_batch_questions,
    )
    cache_state_after = model_state_sha256(model)
    if cache_state_before != cache_state_after:
        raise RuntimeError("model state changed while materializing sealed hidden evidence")

    all_rows: list[dict[str, Any]] = []
    candidate_results: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates):
        checkpoint = Path(candidate["checkpoint"])
        direct.load_own_checkpoint(model, checkpoint)
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
        state_before = model_state_sha256(model)

        primary_rows = score_cached_rows(
            model=model,
            cached_questions=primary_cached,
            source_questions=primary,
            batch_questions=args.eval_batch_questions,
        )
        relative_rows = curriculum.relative_rows(
            model=model,
            primary_cached=primary_cached,
            source_questions=primary,
            relative_cached_by_key=relative_cached_by_key,
            batch_questions=args.eval_batch_questions,
        )
        source_fp = {question.question_id: question_fingerprint(question) for question in primary}
        for row in relative_rows:
            row["fingerprint"] = source_fp[str(row["question_id"])]

        primary_summary = summarize_primary_rows(primary_rows)
        relative_summary = summarize_relative_rows_direct(relative_rows)
        if int(primary_summary["questions"]) != PRIMARY_TOTAL:
            raise RuntimeError("hidden primary score no longer has the 384-question denominator")
        if abs(
            float(primary_summary["accuracy"]) -
            float(relative_summary["primary_accuracy_same_population"])
        ) > 1e-12:
            raise RuntimeError("primary and relative paths disagree on primary accuracy")

        state_after = model_state_sha256(model)
        state_unchanged = state_before == state_after
        if not state_unchanged:
            raise RuntimeError(f"candidate model state mutated during hidden evaluation: {checkpoint}")

        candidate_id = f"candidate-{candidate_index + 1:02d}"
        for row in primary_rows:
            all_rows.append({"candidate_id": candidate_id, "kind": "primary", **row})
        for row in relative_rows:
            all_rows.append({"candidate_id": candidate_id, "kind": "relative_candidate", **row})

        errors = [
            {"question_id": row["question_id"], "fingerprint": row["fingerprint"], "task": row["task"]}
            for row in primary_rows if not bool(row["correct"])
        ]
        candidate_results.append({
            "candidate_id": candidate_id,
            **candidate,
            "hidden_primary": primary_summary,
            "hidden_relative_candidate": relative_summary,
            "primary_errors": errors,
            "model_state_sha256_before": state_before,
            "model_state_sha256_after": state_after,
            "model_state_unchanged": state_unchanged,
        })
        emit(
            "hidden_candidate_scored",
            candidate_id=candidate_id,
            checkpoint=str(checkpoint),
            primary_correct=primary_summary["correct"],
            primary_questions=primary_summary["questions"],
            primary_accuracy=primary_summary["accuracy"],
            primary_errors=primary_summary["errors"],
            relative_candidate_accuracy=relative_summary["relative_candidate_accuracy"],
        )

    atomic_jsonl(rows_path, all_rows)
    rows_sha = sha256_file(rows_path)
    filesystem_after = protected_filesystem_snapshot(experiment_dir, checkpoint_paths)
    filesystem_unchanged = filesystem_before == filesystem_after

    result = {
        "schema_version": RESULT_SCHEMA,
        "completed_unix": time.time(),
        "sealed_manifest": str(manifest_path),
        "sealed_manifest_sha256": sealed_manifest_sha,
        "sealed_population": str(population_path),
        "sealed_population_sha256": population_sha,
        "rows": str(rows_path),
        "rows_sha256": rows_sha,
        "holdout_exposed": True,
        "future_use": "validation_or_diagnostic_only; generate and seal a new non-overlapping population for another hidden claim",
        "cache": cache_stats,
        "cache_model_state_sha256_before": cache_state_before,
        "cache_model_state_sha256_after": cache_state_after,
        "cache_model_state_unchanged": cache_state_before == cache_state_after,
        "candidates": candidate_results,
        "read_only_proof": {
            "optimizer_state_loaded": False,
            "training_steps": 0,
            "backward_calls": 0,
            "threshold_or_calibrator_fit_on_hidden_gold": False,
            "protected_filesystem_unchanged": filesystem_unchanged,
            "protected_before": filesystem_before,
            "protected_after": filesystem_after,
        },
        "headline_contract": {
            "primary_questions": PRIMARY_TOTAL,
            "primary_tasks": dict(PRIMARY_PLAN),
            "relative_candidate_is_secondary": True,
        },
    }
    atomic_json(result_path, result)
    result_sha = sha256_file(result_path)
    emit(
        "hidden_holdout_complete",
        result=str(result_path),
        result_sha256=result_sha,
        rows=str(rows_path),
        rows_sha256=rows_sha,
        protected_filesystem_unchanged=filesystem_unchanged,
        holdout_exposed=True,
    )
    if not filesystem_unchanged:
        raise RuntimeError(
            "protected experiment/checkpoint filesystem changed during evaluation; result was written for diagnosis but read-only proof failed"
        )

def self_test() -> None:
    assert normalize_source_path(r".\Foo\BAR.py") == "foo/bar.py"
    assert sha256_json({"b": 2, "a": 1}) == sha256_json({"a": 1, "b": 2})
    sample = [
        {"correct": True, "task": "a", "stratum": "x", "gold_nll": 0.1},
        {"correct": False, "task": "a", "stratum": "x", "gold_nll": 1.1},
    ]
    summary = summarize_primary_rows(sample)
    assert summary["questions"] == 2 and summary["correct"] == 1 and summary["errors"] == 1
    assert abs(float(summary["accuracy"]) - 0.5) < 1e-12

    class _LegacyPath:
        prompt = "p"
        answer = "a"

    class _LegacyCandidate:
        candidate_id = "c0"
        paths = (_LegacyPath(),)

    class _LegacyQuestion:
        question_id = "legacy:q"
        task = "consensus"
        gold_index = 0
        candidates = (_LegacyCandidate(),)

    legacy_row = serialize_question(None, _LegacyQuestion())
    assert legacy_row["stratum"] == ""
    assert legacy_row["question_id"] == "legacy:q"

    with tempfile.TemporaryDirectory(prefix="nanojev-hidden-self-test-") as td:
        root = Path(td)
        candidate = root / "test.json"
        other = root / "train.json"
        probe = root / "probe.jsonl"
        atomic_json(candidate, [
            {"relative": "a.py", "language": "python"},
            {"relative": "b.py", "language": "python"},
            {"relative": "c.py", "language": "python"},
        ])
        atomic_json(other, [{"relative": "A.py", "language": "python"}])
        atomic_jsonl(probe, [{"metadata": {"source_path": "b.py"}}])
        eligible, audit = build_source_split_audit(
            candidate_manifest_path=candidate,
            manifest_files=[candidate, other],
            probe_files=[probe],
        )
        assert [row["relative"] for row in eligible] == ["c.py"]
        assert audit["excluded_prohibited_source_paths"] == 2
        assert audit["total_prohibited_path_overlap"] == 0

        fresh = prepare_output_dir(root / "fresh")
        assert fresh.is_dir()
        (fresh / "x").write_text("x", encoding="utf-8")
        try:
            prepare_output_dir(fresh)
        except RuntimeError:
            pass
        else:
            raise AssertionError("nonempty hidden output directory was not rejected")

    print(json.dumps({"event": "nanojev_hidden_holdout_self_test_ok"}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-dir", default=DEFAULT_EXPERIMENT)
    parser.add_argument(
        "--checkpoint", action="append", default=[],
        help="preregister a checkpoint candidate; repeat to compare multiple candidates once on the same sealed population",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--resume-sealed", action="store_true",
        help="resume scoring an existing sealed population after an evaluator-only failure; never regenerate it",
    )
    parser.add_argument(
        "--expected-manifest-sha256", default=None,
        help="optional preregistered console SHA-256 for the sealed manifest; strongly recommended with --resume-sealed",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--hidden-cycle", type=int, default=DEFAULT_HIDDEN_CYCLE)
    parser.add_argument("--max-prompt-tokens", type=int, default=DEFAULT_MAX_PROMPT_TOKENS)
    parser.add_argument("--max-answer-tokens", type=int, default=DEFAULT_MAX_ANSWER_TOKENS)
    parser.add_argument("--relative-max-prompt-tokens", type=int, default=DEFAULT_RELATIVE_MAX_PROMPT_TOKENS)
    parser.add_argument("--cache-qwen-batch-questions", type=int, default=DEFAULT_CACHE_QWEN_BATCH)
    parser.add_argument("--eval-batch-questions", type=int, default=DEFAULT_EVAL_BATCH)
    parser.add_argument("--precision", choices=("bf16", "fp32"), default=DEFAULT_PRECISION)
    parser.add_argument("--local-files-only", action="store_true", default=True)
    parser.add_argument("--allow-model-download", action="store_false", dest="local_files_only")
    parser.add_argument("--disable-native-triton", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return
    for name in (
        "max_prompt_tokens", "max_answer_tokens", "relative_max_prompt_tokens",
        "cache_qwen_batch_questions", "eval_batch_questions",
    ):
        if int(getattr(args, name)) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.hidden_cycle <= 0:
        parser.error("--hidden-cycle must be positive")

    experiment_dir = Path(args.experiment_dir).expanduser().resolve(strict=True)
    default_output = experiment_dir.parent / f"{experiment_dir.name}_hidden_holdout_seed_{args.seed}"
    requested_output = Path(args.output_dir) if args.output_dir else default_output
    if args.resume_sealed:
        output_dir = requested_output.expanduser().resolve(strict=True)
        if not output_dir.is_dir():
            raise RuntimeError(f"--resume-sealed output is not a directory: {output_dir}")
    else:
        output_dir = prepare_output_dir(requested_output)
    population_path = output_dir / "hidden_holdout_population.json"
    manifest_path = output_dir / "hidden_holdout_manifest.json"
    rows_path = output_dir / "hidden_holdout_rows.jsonl"
    result_path = output_dir / "hidden_holdout_result.json"

    if args.resume_sealed:
        modules = load_generation_modules()
        dictionary_curriculum = modules["dictionary_curriculum"]
        manifest, candidates, primary, relative, sealed_manifest_sha, population_sha = load_sealed_resume(
            experiment_dir=experiment_dir,
            output_dir=output_dir,
            explicit_checkpoints=args.checkpoint,
            expected_manifest_sha256=args.expected_manifest_sha256,
            dictionary_curriculum=dictionary_curriculum,
        )
        source = dict(manifest["source"])
        legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
        from transformers import AutoTokenizer
        tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
        tokenizer = AutoTokenizer.from_pretrained(
            str(tokenizer_dir), local_files_only=True, trust_remote_code=False
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token
        if tokenizer.pad_token_id is None:
            raise RuntimeError("tokenizer has no pad/eos token")
        emit(
            "hidden_checkpoint_candidates_resumed",
            candidates=[candidate_seal_identity(row) for row in candidates],
        )
        score_sealed_run(
            args=args, experiment_dir=experiment_dir, candidates=candidates, modules=modules,
            source=source, tokenizer=tokenizer, primary=primary, relative=relative,
            sealed_manifest_sha=sealed_manifest_sha, population_sha=population_sha,
            manifest_path=manifest_path, population_path=population_path,
            rows_path=rows_path, result_path=result_path,
        )
        return

    # Phase 1: preregister checkpoint candidate(s) before the hidden population exists.
    manifest, _state, candidates = resolve_candidates(experiment_dir, args.checkpoint)
    source = dict(manifest["source"])
    source_seal = source_code_seal()
    emit(
        "hidden_checkpoint_candidates_sealed",
        candidates=[
            {
                "checkpoint": row["checkpoint"],
                "cycle": row["cycle"],
                "global_step": row["global_step"],
                "head_safetensors_sha256": row["head_safetensors_sha256"],
            }
            for row in candidates
        ],
    )

    modules = load_generation_modules()
    direct = modules["direct"]
    curriculum = modules["curriculum"]
    dictionary_curriculum = modules["dictionary_curriculum"]
    ordered_api = modules["ordered_api"]

    legacy_exp = Path(str(source["legacy_experiment"])).expanduser().resolve(strict=True)
    legacy_meta = read_json(legacy_exp / "experiment.json")
    repo_root = Path(str(source["repo_root"])).expanduser().resolve(strict=True)
    test_manifest_raw = (legacy_meta.get("manifests") or {}).get("test")
    if not test_manifest_raw:
        raise RuntimeError("legacy experiment does not identify a test manifest")
    test_manifest = Path(str(test_manifest_raw)).expanduser().resolve(strict=True)
    known_dirs = known_experiment_dirs(experiment_dir, manifest)
    manifest_files = discover_manifest_files(known_dirs, legacy_meta)
    probe_files = discover_probe_files(known_dirs)
    eligible_manifest, source_audit = build_source_split_audit(
        candidate_manifest_path=test_manifest,
        manifest_files=manifest_files,
        probe_files=probe_files,
    )
    if int(source_audit["total_prohibited_path_overlap"]) != 0:
        raise RuntimeError("hidden source split retained prohibited source overlap")
    emit(
        "hidden_source_split_audited",
        raw_paths=source_audit["candidate_raw_source_paths"],
        eligible_paths=source_audit["candidate_eligible_source_paths"],
        eligible_python_rows=source_audit["candidate_eligible_python_rows"],
        excluded_paths=source_audit["excluded_prohibited_source_paths"],
    )

    # Tokenizer is permitted during population construction; no candidate model is loaded.
    from transformers import AutoTokenizer

    tokenizer_dir = legacy_exp / "artifacts" / "tokenizer"
    tokenizer = AutoTokenizer.from_pretrained(
        str(tokenizer_dir), local_files_only=True, trust_remote_code=False
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    if tokenizer.pad_token_id is None:
        raise RuntimeError("tokenizer has no pad/eos token")

    # Phase 2/3: build population against a disposable DB clone, then prove overlap is zero.
    source_db = Path(str(manifest["database"])).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="nanojev-hidden-population-") as td:
        lexical_clone = Path(td) / "lexical.db"
        clone_sqlite_readonly(source_db, lexical_clone)
        primary, relative, generation = build_population(
            manifest=manifest,
            eligible_manifest=eligible_manifest,
            tokenizer=tokenizer,
            modules=modules,
            seed=args.seed,
            hidden_cycle=args.hidden_cycle,
            lexical_clone=lexical_clone,
        )

    historical_questions, historical_sources = load_historical_questions(
        known_dirs=known_dirs,
        direct=direct,
        ordered_api=ordered_api,
        dictionary_curriculum=dictionary_curriculum,
        curriculum=curriculum,
        repo_root=repo_root,
    )
    prior_hidden, prior_sources = discover_prior_hidden_fingerprints(experiment_dir, output_dir)
    fingerprint_audit = historical_fingerprint_audit(
        primary=primary,
        relative=relative,
        historical_questions=historical_questions,
        curriculum=curriculum,
        prior_hidden=prior_hidden,
        historical_sources=historical_sources,
        prior_sources=prior_sources,
    )
    emit(
        "hidden_fingerprint_audited",
        historical_fingerprints=fingerprint_audit["historical_fingerprints"],
        primary_overlap=0,
        relative_overlap=0,
    )

    # Phase 4: serialize and seal the complete population before any candidate model scoring.
    population_payload = {
        "schema_version": POPULATION_SCHEMA,
        "created_unix": time.time(),
        "generation": generation,
        "primary": [serialize_question(dictionary_curriculum, question) for question in primary],
        "relative_variants": [serialize_question(dictionary_curriculum, question) for question in relative],
    }
    atomic_json(population_path, population_payload)
    population_sha = sha256_file(population_path)
    primary_fps = [question_fingerprint(question) for question in primary]
    relative_fps = [question_fingerprint(question) for question in relative]
    sealed_manifest = {
        "schema_version": SCHEMA,
        "created_unix": time.time(),
        "status_at_seal": "sealed_unexposed",
        "experiment": str(experiment_dir),
        "candidates": candidates,
        "source_code_seal": source_seal,
        "source_split_audit": source_audit,
        "fingerprint_audit": fingerprint_audit,
        "population_file": str(population_path),
        "population_sha256": population_sha,
        "primary_plan": dict(PRIMARY_PLAN),
        "primary_fingerprints": primary_fps,
        "relative_fingerprints": relative_fps,
        "contract": {
            "primary_headline_denominator": PRIMARY_TOTAL,
            "relative_candidate_secondary_only": True,
            "checkpoint_candidates_preregistered_before_population_generation": True,
            "candidate_model_loaded_during_population_generation": False,
            "optimizer_state_may_not_be_loaded": True,
            "training_or_backward_forbidden": True,
            "hidden_gold_threshold_selection_forbidden": True,
            "historical_fingerprint_overlap_required": 0,
            "prohibited_source_path_overlap_required": 0,
            "reuse_after_exposure": "validation_or_diagnostic_only",
        },
    }
    atomic_json(manifest_path, sealed_manifest)
    sealed_manifest_sha = sha256_file(manifest_path)
    emit(
        "hidden_holdout_sealed",
        manifest=str(manifest_path),
        manifest_sha256=sealed_manifest_sha,
        population_sha256=population_sha,
        primary_questions=len(primary),
        relative_variants=len(relative),
    )

    score_sealed_run(
        args=args, experiment_dir=experiment_dir, candidates=candidates, modules=modules,
        source=source, tokenizer=tokenizer, primary=primary, relative=relative,
        sealed_manifest_sha=sealed_manifest_sha, population_sha=population_sha,
        manifest_path=manifest_path, population_path=population_path,
        rows_path=rows_path, result_path=result_path,
    )


if __name__ == "__main__":
    main()
