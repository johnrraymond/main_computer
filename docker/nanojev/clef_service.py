#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
from typing import Any, Mapping

MAX_STATES = 32
MAX_QUESTIONS = 96
MAX_CANDIDATE_PATHS = 256
RELEASE_SCHEMA = "main-computer-nanojev-clef-release-v1"
MANIFEST_SCHEMA = "main-computer-nanojev-clef-sha256-manifest-v1"
DEFAULT_REPO_ID = "johnrraymond/NanoJev-CLEF"
DEFAULT_REVISION = "champion"
HF_REACHABILITY_TIMEOUT_SECONDS = 5.0
LOCAL_STATE_SCHEMA = "main-computer-nanojev-clef-local-state-v1"
TOOLS = Path("/opt/main-computer-tools")


@dataclass(frozen=True)
class PathSpec:
    prompt: str
    answer: str


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    paths: tuple[PathSpec, ...]


@dataclass(frozen=True)
class QuestionSpec:
    question_id: str
    task: str
    candidates: tuple[CandidateSpec, ...]
    gold_index: int = -1


class NullLogger:
    def emit(self, _event: str, **_fields: Any) -> None:
        return None


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def verify_release(root: Path) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    release_path = root / "release.json"
    manifest_path = root / "SHA256_MANIFEST.json"
    release = read_json(release_path)
    manifest = read_json(manifest_path)
    if release.get("schema_version") != RELEASE_SCHEMA:
        raise RuntimeError(f"unsupported CLEF release schema: {release.get('schema_version')!r}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise RuntimeError(f"unsupported CLEF manifest schema: {manifest.get('schema_version')!r}")
    release_name = str(release.get("release_name") or "")
    if not release_name or manifest.get("release_name") != release_name:
        raise RuntimeError("CLEF release and manifest disagree on release_name")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("CLEF manifest has no files map")
    for filename in ("head.safetensors", "tinystories.safetensors", "release.json"):
        row = files.get(filename)
        path = root / filename
        if not isinstance(row, dict) or not path.is_file():
            raise RuntimeError(f"CLEF release is missing manifest-backed file: {filename}")
        expected = str(row.get("sha256") or "")
        observed = sha256_file(path)
        if expected != observed:
            raise RuntimeError(
                f"CLEF release SHA256 mismatch for {filename}: expected={expected} observed={observed}"
            )
    return release


def hf_home() -> Path:
    configured = os.environ.get("HF_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".cache" / "huggingface").resolve()


def local_state_path() -> Path:
    return hf_home() / "main-computer" / "nanojev-clef-last-verified.json"


def _repo_cache_root(repo_id: str) -> Path:
    return hf_home() / "hub" / ("models--" + repo_id.replace("/", "--"))


def _write_local_state(
    *,
    repo_id: str,
    selector: str,
    resolved_revision: str,
    snapshot: Path,
    release: Mapping[str, Any],
) -> None:
    path = local_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": LOCAL_STATE_SCHEMA,
        "repo_id": repo_id,
        "selector": selector,
        "resolved_revision": resolved_revision,
        "snapshot": str(Path(snapshot).resolve()),
        "release_name": str(release.get("release_name") or ""),
        "verified_at_unix": time.time(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_local_state(repo_id: str) -> tuple[Path, str, dict[str, Any]] | None:
    path = local_state_path()
    if not path.is_file():
        return None
    try:
        state = read_json(path)
        if state.get("schema_version") != LOCAL_STATE_SCHEMA or state.get("repo_id") != repo_id:
            return None
        snapshot = Path(str(state.get("snapshot") or "")).resolve(strict=True)
        release = verify_release(snapshot)
        resolved = str(state.get("resolved_revision") or "").strip()
        if not resolved:
            return None
        return snapshot, resolved, release
    except Exception:
        return None


def _recover_verified_snapshot_from_cache(repo_id: str) -> tuple[Path, str, dict[str, Any]] | None:
    snapshots = _repo_cache_root(repo_id) / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in snapshots.iterdir():
        if not path.is_dir():
            continue
        try:
            release = verify_release(path)
        except Exception:
            continue
        try:
            stamp = max(
                (path / name).stat().st_mtime_ns
                for name in ("release.json", "head.safetensors", "tinystories.safetensors")
            )
        except OSError:
            continue
        candidates.append((stamp, path, release))
    if not candidates:
        return None
    _stamp, snapshot, release = max(candidates, key=lambda row: row[0])
    resolved = snapshot.name
    _write_local_state(
        repo_id=repo_id,
        selector="local-cache-recovery",
        resolved_revision=resolved,
        snapshot=snapshot,
        release=release,
    )
    return snapshot, resolved, release


def load_last_verified_release(repo_id: str) -> tuple[Path, str, dict[str, Any]]:
    resolved = _read_local_state(repo_id) or _recover_verified_snapshot_from_cache(repo_id)
    if resolved is None:
        raise RuntimeError(
            f"Hugging Face is unavailable and no verified local NanoJev CLEF release exists for {repo_id}"
        )
    return resolved


def _is_transient_hf_failure(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code >= 500 or status_code in {408, 429}
    try:
        import httpx

        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
    except Exception:
        pass
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


def resolve_public_release(
    repo_id: str,
    revision: str,
    *,
    reachability_timeout_seconds: float = HF_REACHABILITY_TIMEOUT_SECONDS,
) -> tuple[Path, str, dict[str, Any], str, str]:
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi()
    try:
        info = api.model_info(
            repo_id=repo_id,
            revision=revision,
            timeout=float(reachability_timeout_seconds),
        )
        commit = str(info.sha or "").strip()
        if not commit:
            raise RuntimeError(f"Hugging Face did not resolve {repo_id}@{revision} to a commit")
        snapshot = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=commit,
                etag_timeout=float(reachability_timeout_seconds),
                allow_patterns=[
                    "head.safetensors",
                    "tinystories.safetensors",
                    "release.json",
                    "SHA256_MANIFEST.json",
                ],
            )
        )
        release = verify_release(snapshot)
        _write_local_state(
            repo_id=repo_id,
            selector=revision,
            resolved_revision=commit,
            snapshot=snapshot,
            release=release,
        )
        return snapshot, commit, release, "huggingface", ""
    except Exception as exc:
        if not _is_transient_hf_failure(exc):
            raise
        snapshot, commit, release = load_last_verified_release(repo_id)
        return snapshot, commit, release, "local-disk-fallback", f"{type(exc).__name__}: {exc}"


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_request(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"states"}:
        raise ValueError('request must be exactly {"states": [...]}')
    states = payload["states"]
    if not isinstance(states, list) or not states or len(states) > MAX_STATES:
        raise ValueError(f"states must contain 1..{MAX_STATES} entries")
    seen_state_ids: set[str] = set()
    question_count = 0
    candidate_paths = 0
    for row in states:
        if not isinstance(row, dict) or set(row) != {"id", "state", "questions"}:
            raise ValueError("each state must contain exactly id, state, questions")
        sid = row["id"]
        if not _nonempty_text(sid) or sid in seen_state_ids:
            raise ValueError("state id must be a unique nonempty string")
        seen_state_ids.add(sid)
        if not isinstance(row["state"], (str, dict, list)) or not row["state"]:
            raise ValueError("state must be a nonempty string, object, or array")
        questions = row["questions"]
        if not isinstance(questions, dict) or not questions:
            raise ValueError("questions must be a nonempty object")
        for qid, question in questions.items():
            question_count += 1
            if question_count > MAX_QUESTIONS:
                raise ValueError(f"request exceeds {MAX_QUESTIONS} questions")
            if not _nonempty_text(qid) or not isinstance(question, dict):
                raise ValueError("question IDs must be nonempty strings and questions must be objects")
            if set(question) - {"type", "instructions", "criteria"}:
                raise ValueError(f"{sid}:{qid} contains unsupported question fields")
            typ = question.get("type")
            if typ not in {"boolean", "choice", "score"} or not _nonempty_text(question.get("instructions")):
                raise ValueError(f"{sid}:{qid} has invalid type/instructions")
            if typ == "boolean":
                criteria = question.get("criteria")
                if criteria is not None:
                    if not isinstance(criteria, dict) or set(criteria) - {"false", "true"}:
                        raise ValueError("boolean criteria may contain only false/true")
                    if not all(_nonempty_text(value) for value in criteria.values()):
                        raise ValueError("boolean criteria must be nonempty strings")
                candidate_paths += 2
            elif typ == "choice":
                criteria = question.get("criteria")
                if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                    raise ValueError("choice criteria must contain 2..255 entries")
                if not all(_nonempty_text(k) and _nonempty_text(v) for k, v in criteria.items()):
                    raise ValueError("choice IDs and descriptions must be nonempty strings")
                candidate_paths += len(criteria)
            else:
                criteria = question.get("criteria")
                if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                    raise ValueError("score criteria must contain 2..10 ordered entries")
                if not all(_nonempty_text(value) for value in criteria):
                    raise ValueError("score levels must be nonempty strings")
                candidate_paths += len(criteria)
            if candidate_paths > MAX_CANDIDATE_PATHS:
                raise ValueError(f"request exceeds {MAX_CANDIDATE_PATHS} CLEF candidate paths")
    return states


def build_question(state: Any, qid: str, question: Mapping[str, Any]) -> tuple[QuestionSpec, list[str], str]:
    typ = str(question["type"])
    prompt = f"State:\n{state}\nQuestion type: {typ}\nQuestion:\n{question['instructions']}\n"
    criteria = question.get("criteria")
    if typ == "boolean" and isinstance(criteria, Mapping):
        for key, label in (("false", "False"), ("true", "True")):
            if key in criteria:
                prompt += f"{label} criterion: {criteria[key]}\n"
    prompt += "Answer:"

    if typ == "boolean":
        ids = ["false", "true"]
        false_text = str(criteria.get("false")) if isinstance(criteria, Mapping) and "false" in criteria else "The proposition is false."
        true_text = str(criteria.get("true")) if isinstance(criteria, Mapping) and "true" in criteria else "The proposition is true."
        texts = [false_text, true_text]
    elif typ == "choice":
        assert isinstance(criteria, Mapping)
        ids = [str(key) for key in criteria]
        texts = [f"{key}: {criteria[key]}" for key in criteria]
    else:
        assert isinstance(criteria, list)
        ids = [str(index) for index in range(len(criteria))]
        texts = [str(value) for value in criteria]

    candidates = tuple(
        CandidateSpec(candidate_id=cid, paths=(PathSpec(prompt=prompt, answer=" " + text),))
        for cid, text in zip(ids, texts)
    )
    return QuestionSpec(question_id=str(qid), task="service", candidates=candidates), ids, typ


def answer_from_probabilities(ids: list[str], typ: str, probabilities: list[float]) -> dict[str, Any]:
    if len(probabilities) != len(ids) or not all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probabilities):
        raise RuntimeError("CLEF produced invalid probabilities")
    total = math.fsum(probabilities)
    if not math.isfinite(total) or abs(total - 1.0) > 1e-5:
        raise RuntimeError(f"CLEF probabilities do not sum to one: {total}")
    best = max(range(len(ids)), key=probabilities.__getitem__)
    result: dict[str, Any] = {"type": typ, "probabilities": dict(zip(ids, probabilities))}
    if typ == "boolean":
        result.update(p_true=probabilities[1], value=bool(best))
    elif typ == "choice":
        result.update(choice=ids[best], value=ids[best])
    else:
        score = math.fsum(index * p for index, p in enumerate(probabilities))
        result.update(score=score, level=best, value=score)
    return result


class ClefRuntime:
    def __init__(self, *, repo_id: str, revision: str) -> None:
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        self.repo_id = repo_id
        self.revision = revision
        (
            self.snapshot,
            self.resolved_revision,
            self.release,
            self.checkpoint_source,
            self.checkpoint_resolution_error,
        ) = resolve_public_release(repo_id, revision)
        self.release_name = str(self.release["release_name"])
        self.release_manifest_sha256 = sha256_file(self.snapshot / "SHA256_MANIFEST.json")
        self.lock = threading.RLock()
        self.provider_calls = 0
        self._load()

    def _load(self) -> None:
        import torch
        from safetensors.torch import load_file

        structured = load_local_module(
            "nanojev_structured_service_runtime",
            TOOLS / "nanojev_three_backbone_clef_tinystories_structured_supervision_train.py",
        )
        smoke = structured.smoke
        source = ((self.release.get("backbone_provenance") or {}).get("source") or {})
        model = str(source.get("model") or "Qwen/Qwen3-0.6B")
        revision = source.get("revision")
        source_spec = {"model": model, "revision": revision or "main"}
        logger = NullLogger()
        bundles, _ = smoke.load_backbones(
            source=source_spec,
            local_files_only=(self.checkpoint_source == "local-disk-fallback"),
            logger=logger,
        )
        bundles[structured.TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(self.snapshot / "tinystories.safetensors"), device="cpu"),
            strict=True,
        )
        for bundle in bundles.values():
            smoke.freeze_module(bundle.lm)
            bundle.lm.eval()
        hidden_sizes = {label: bundle.hidden_size for label, bundle in bundles.items()}
        head, _ = structured.build_layer_tap_head(torch=torch, hidden_sizes=hidden_sizes)
        head.load_state_dict(
            load_file(str(self.snapshot / "head.safetensors"), device="cpu"),
            strict=True,
        )
        head = head.to(device="cuda", dtype=torch.bfloat16)
        for parameter in head.parameters():
            parameter.requires_grad_(False)
        head.eval()
        self.torch = torch
        self.structured = structured
        self.train = structured
        self.smoke = smoke
        self.objective_api = load_local_module(
            "nanojev_service_objective_api", TOOLS / "nanojev_objective_api.py"
        )
        self.head = head
        self.bundles = bundles
        self.args = SimpleNamespace(
            max_prompt_tokens=int(smoke.DEFAULT_MAX_PROMPT_TOKENS),
            max_answer_tokens=int(smoke.DEFAULT_MAX_ANSWER_TOKENS),
            prompt_evidence_tokens=int(smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS),
            answer_evidence_tokens=int(smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS),
            path_batch=int(smoke.DEFAULT_PATH_BATCH),
            tinystories_path_batch=int(structured.DEFAULT_TINYSTORIES_PATH_BATCH),
        )
        self.logger = logger
        self.evidence_args = self.args

    def health(self) -> dict[str, Any]:
        return {
            "ready": True,
            "model_loaded_once": True,
            "provider_calls": self.provider_calls,
            "model_family": "nanojev-clef",
            "checkpoint_selector": self.revision,
            "checkpoint_repo": self.repo_id,
            "checkpoint_revision_resolved": self.resolved_revision,
            "checkpoint_source": self.checkpoint_source,
            "checkpoint_resolution_error": self.checkpoint_resolution_error,
            "release_name": self.release_name,
            "release_manifest_sha256": self.release_manifest_sha256,
            "checkpoint_cycle": ((self.release.get("checkpoint") or {}).get("cycle")),
            "checkpoint_reuse_depth": ((self.release.get("checkpoint") or {}).get("reuse_depth")),
        }

    @staticmethod
    def _common_prefix_length(sequences: list[list[int]]) -> int:
            if not sequences:
                return 0
            limit = min(len(sequence) for sequence in sequences)
            for index in range(limit):
                token = sequences[0][index]
                if any(sequence[index] != token for sequence in sequences[1:]):
                    return index
            return limit
    def _repeat_legacy_cache(self, cache, repeats: int):
            """Repeat a one-row HF causal-LM cache across a suffix batch.

            Recent transformers may return Cache objects while older GPT-Neo/GPT-NeoX
            builds return legacy tuples.  Convert when possible; otherwise signal the
            caller to fall back to the ordinary full-sequence batch path.
            """
            torch = self.torch
            if cache is None:
                raise RuntimeError("backbone did not return a reusable prefix cache")
            if hasattr(cache, "to_legacy_cache"):
                cache = cache.to_legacy_cache()
            if not isinstance(cache, (tuple, list)):
                raise RuntimeError(f"unsupported causal-LM cache type: {type(cache)!r}")

            def repeat(value):
                if torch.is_tensor(value):
                    if value.shape[0] != 1:
                        raise RuntimeError(
                            f"prefix cache tensor has unexpected batch dimension {value.shape[0]}"
                        )
                    return value.repeat_interleave(repeats, dim=0)
                if isinstance(value, (tuple, list)):
                    return tuple(repeat(item) for item in value)
                raise RuntimeError(f"unsupported value in causal-LM cache: {type(value)!r}")

            return tuple(repeat(layer) for layer in cache)
    def _repeat_cache(self, cache, repeats: int):
            """Repeat a one-row HF cache without discarding its native cache type.

            Modern Transformers models commonly return Cache objects whose native batch
            operations carry model-specific bookkeeping that is lost by eager conversion
            to a legacy tuple.  Prefer the native batch repeater when available, then fall
            back to the legacy tensor recursion for older GPT-Neo/GPT-NeoX builds.
            """
            if cache is None:
                raise RuntimeError("backbone did not return a reusable prefix cache")
            native_repeat = getattr(cache, "batch_repeat_interleave", None)
            if callable(native_repeat):
                repeated = native_repeat(repeats)
                return cache if repeated is None else repeated
            return self._repeat_legacy_cache(cache, repeats)
    def _cached_suffix_position_kwargs(backbone, *, prefix_len: int, suffix_width: int, batch_size: int, device):
            """Provide explicit cached positions only to backbones that accept them."""
            try:
                parameters = inspect.signature(backbone.forward).parameters
            except (TypeError, ValueError):
                return {}
            import torch
            positions = torch.arange(prefix_len, prefix_len + suffix_width, device=device, dtype=torch.long)
            kwargs = {}
            if "position_ids" in parameters:
                kwargs["position_ids"] = positions.unsqueeze(0).expand(batch_size, -1)
            if "cache_position" in parameters:
                kwargs["cache_position"] = positions
            return kwargs
    def _residual_hidden_states_from_output(self, bundle, output):
            """Return TinyStories residual-source layers plus the final-layer anchor.

            Training keeps the proven final TinyStories layer as the 768-wide base
            evidence and exposes only transformer layers 1 and 2 as a separate
            1536-wide residual source.  Non-TinyStories backbones have no residual
            source and use only their final hidden state.
            """
            final_hidden = output.last_hidden_state
            if bundle.label != self.train.TRAINABLE_LABEL:
                return (), final_hidden

            hidden_states = tuple(output.hidden_states or ())
            transformer_layers = len(hidden_states) - 1
            residual_layers = tuple(int(layer) for layer in self.train.TINYSTORIES_RESIDUAL_LAYERS)
            final_layer = int(self.train.TINYSTORIES_FINAL_LAYER)
            if transformer_layers < max((*residual_layers, final_layer)):
                raise RuntimeError(
                    "TinyStories residual-tap contract exceeds available transformer depth: "
                    f"residual_layers={residual_layers} final_layer={final_layer} "
                    f"available={transformer_layers}"
                )
            residual = tuple(hidden_states[layer] for layer in residual_layers)
            expected = int(self.train.TINYSTORIES_BASE_HIDDEN_SIZE)
            if int(final_hidden.shape[-1]) != expected or any(
                int(hidden.shape[-1]) != expected for hidden in residual
            ):
                raise RuntimeError(
                    "TinyStories residual-tap hidden width drifted: "
                    f"expected={expected} final={int(final_hidden.shape[-1])} "
                    f"residual={[int(hidden.shape[-1]) for hidden in residual]}"
                )
            return residual, final_hidden
    def _extract_bundle_batch(self, bundle, questions, evidence_execution_mode: str = "auto"):
            """Extract batched evidence using the exact current training contract.

            TinyStories keeps final-layer-4 evidence 768-wide and separately carries
            layers 1+2 as 1536-wide residual sources.  Prefix-cache and full-batch
            execution therefore differ only in how backbone states are obtained; both
            paths emit byte-for-byte equivalent evidence fields for the residual head.
            """
            if evidence_execution_mode not in {"auto", "prefix-cache", "full-batch"}:
                raise ValueError(f"unsupported batch evidence execution mode: {evidence_execution_mode}")

            torch = self.torch
            question_rows = []
            occurrence_counts = []
            flat_rows = []
            for question_index, question in enumerate(questions):
                rows, occurrence_count = self.smoke._model_sequences(
                    bundle,
                    question,
                    max_prompt_tokens=int(self.evidence_args.max_prompt_tokens),
                    max_answer_tokens=int(self.evidence_args.max_answer_tokens),
                )
                if len(question.candidates) != 2:
                    raise RuntimeError(
                        f"pairwise batch inference requires binary questions: {question.question_id}"
                    )
                question_rows.append(rows)
                occurrence_counts.append(int(occurrence_count))
                flat_rows.extend((question_index, row) for row in rows)
            if not flat_rows:
                raise RuntimeError("pairwise batch inference produced no model paths")

            question_count = len(questions)

            def new_accumulators():
                return (
                    [[] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                    [[[] for _ in range(2)] for _ in range(question_count)],
                )

            (
                memory_parts,
                option_answer,
                option_predictor,
                option_terminal,
                option_prompt,
                option_lexical,
                option_logp,
                residual_memory_parts,
                residual_option_answer,
                residual_option_predictor,
                residual_option_terminal,
                residual_option_prompt,
            ) = new_accumulators()
            device = bundle.output_weight.device
            pad = int(bundle.tokenizer.pad_token_id)
            path_batch = max(int(self.evidence_args.path_batch), min(64, len(flat_rows)))
            forward_batches = 0
            prefix_cache_used = False
            shared_prefix_tokens = 0
            prefix_cache_candidate_tokens = 0
            prefix_cache_failure_reason = None

            residual_tap = bundle.label == self.train.TRAINABLE_LABEL

            def consume_row(question_index, row, residual_hidden_rows, final_hidden_row, token_row):
                plen = len(row["prompt_ids"])
                alen = len(row["answer_ids"])
                prompt_idx = self.smoke.balanced_indices(
                    0, plen, int(self.evidence_args.prompt_evidence_tokens)
                )
                answer_idx = self.smoke.balanced_indices(
                    plen, plen + alen, int(self.evidence_args.answer_evidence_tokens)
                )
                evidence_idx = prompt_idx + answer_idx

                path_logp, predictor_mean = self.smoke._continuation_mean_logp(
                    torch=torch,
                    hidden=final_hidden_row,
                    tokens=token_row,
                    prompt_length=plen,
                    answer_length=alen,
                    output_weight=bundle.output_weight,
                )
                memory_parts[question_index].append(
                    final_hidden_row[evidence_idx].detach().clone()
                )
                prompt_mean = final_hidden_row[:plen].mean(dim=0).detach().clone()
                answer_mean = final_hidden_row[plen:plen + alen].mean(dim=0).detach().clone()
                terminal = final_hidden_row[plen + alen - 1].detach().clone()
                answer_token_ids = token_row[plen:plen + alen]
                lexical = bundle.output_weight[answer_token_ids].mean(dim=0).detach().clone()
                predictor_mean = predictor_mean.detach().clone()
                path_logp = path_logp.detach().clone()

                residual_prompt_mean = None
                residual_answer_mean = None
                residual_predictor_mean = None
                residual_terminal = None
                if residual_tap:
                    if len(residual_hidden_rows) != len(self.train.TINYSTORIES_RESIDUAL_LAYERS):
                        raise RuntimeError(
                            "TinyStories residual source layer count drifted: "
                            f"expected={len(self.train.TINYSTORIES_RESIDUAL_LAYERS)} "
                            f"observed={len(residual_hidden_rows)}"
                        )
                    residual_hidden = torch.cat(residual_hidden_rows, dim=-1)
                    expected_residual = int(self.train.TINYSTORIES_RESIDUAL_SOURCE_HIDDEN_SIZE)
                    if int(residual_hidden.shape[-1]) != expected_residual:
                        raise RuntimeError(
                            "TinyStories residual source width drifted: "
                            f"expected={expected_residual} observed={int(residual_hidden.shape[-1])}"
                        )
                    residual_memory_parts[question_index].append(
                        residual_hidden[evidence_idx].detach().clone()
                    )
                    residual_prompt_mean = residual_hidden[:plen].mean(dim=0).detach().clone()
                    residual_answer_mean = residual_hidden[plen:plen + alen].mean(dim=0).detach().clone()
                    residual_terminal = residual_hidden[plen + alen - 1].detach().clone()
                    residual_predictor_mean = torch.cat([
                        self.train._continuation_predictor_mean(
                            hidden, prompt_length=plen, answer_length=alen
                        )
                        for hidden in residual_hidden_rows
                    ], dim=-1).detach().clone()

                for candidate in row["candidates"]:
                    option_prompt[question_index][candidate].append(prompt_mean)
                    option_answer[question_index][candidate].append(answer_mean)
                    option_predictor[question_index][candidate].append(predictor_mean)
                    option_terminal[question_index][candidate].append(terminal)
                    option_lexical[question_index][candidate].append(lexical)
                    option_logp[question_index][candidate].append(path_logp)
                    if residual_tap:
                        residual_option_prompt[question_index][candidate].append(residual_prompt_mean)
                        residual_option_answer[question_index][candidate].append(residual_answer_mean)
                        residual_option_predictor[question_index][candidate].append(residual_predictor_mean)
                        residual_option_terminal[question_index][candidate].append(residual_terminal)

            prompt_sequences = [list(row["prompt_ids"]) for _, row in flat_rows]
            prefix_len = self._common_prefix_length(prompt_sequences)
            prefix_cache_candidate_tokens = int(prefix_len)
            prefix_cache_eligible = len(flat_rows) <= 64 and prefix_len >= 8
            if evidence_execution_mode != "full-batch" and prefix_cache_eligible:
                try:
                    with torch.no_grad():
                        prefix_tokens = torch.tensor(
                            [prompt_sequences[0][:prefix_len]], dtype=torch.long, device=device
                        )
                        prefix_attention = torch.ones_like(prefix_tokens, dtype=torch.long)
                        prefix_output = bundle.backbone(
                            input_ids=prefix_tokens,
                            attention_mask=prefix_attention,
                            use_cache=True,
                            output_hidden_states=residual_tap,
                            return_dict=True,
                        )
                        cache = self._repeat_cache(prefix_output.past_key_values, len(flat_rows))
                        suffixes = [
                            row["prompt_ids"][prefix_len:] + row["answer_ids"]
                            for _, row in flat_rows
                        ]
                        suffix_width = max(len(sequence) for sequence in suffixes)
                        suffix_tokens = torch.full(
                            (len(flat_rows), suffix_width), pad, dtype=torch.long, device=device
                        )
                        full_attention = torch.zeros(
                            (len(flat_rows), prefix_len + suffix_width),
                            dtype=torch.long,
                            device=device,
                        )
                        full_attention[:, :prefix_len] = 1
                        for batch_index, sequence in enumerate(suffixes):
                            suffix_tokens[batch_index, :len(sequence)] = torch.tensor(
                                sequence, dtype=torch.long, device=device
                            )
                            full_attention[batch_index, prefix_len:prefix_len + len(sequence)] = 1
                        cached_position_kwargs = self._cached_suffix_position_kwargs(
                            bundle.backbone,
                            prefix_len=prefix_len,
                            suffix_width=suffix_width,
                            batch_size=len(flat_rows),
                            device=device,
                        )
                        suffix_output = bundle.backbone(
                            input_ids=suffix_tokens,
                            attention_mask=full_attention,
                            past_key_values=cache,
                            use_cache=False,
                            output_hidden_states=residual_tap,
                            return_dict=True,
                            **cached_position_kwargs,
                        )
                        prefix_residual, prefix_final = self._residual_hidden_states_from_output(
                            bundle, prefix_output
                        )
                        suffix_residual, suffix_final = self._residual_hidden_states_from_output(
                            bundle, suffix_output
                        )
                        staged = []
                        for batch_index, (question_index, row) in enumerate(flat_rows):
                            suffix_len = len(suffixes[batch_index])
                            full_residual = tuple(
                                torch.cat(
                                    [prefix_hidden[0].detach(), suffix_hidden[batch_index, :suffix_len]],
                                    dim=0,
                                )
                                for prefix_hidden, suffix_hidden in zip(prefix_residual, suffix_residual)
                            )
                            full_final = torch.cat(
                                [prefix_final[0].detach(), suffix_final[batch_index, :suffix_len]],
                                dim=0,
                            )
                            full_tokens = torch.tensor(
                                row["prompt_ids"] + row["answer_ids"],
                                dtype=torch.long,
                                device=device,
                            )
                            staged.append(
                                (question_index, row, full_residual, full_final, full_tokens)
                            )
                        for staged_row in staged:
                            consume_row(*staged_row)
                        forward_batches = 2
                        prefix_cache_used = True
                        shared_prefix_tokens = int(prefix_len)
                        del (
                            prefix_output,
                            suffix_output,
                            prefix_residual,
                            suffix_residual,
                            prefix_final,
                            suffix_final,
                            prefix_tokens,
                            prefix_attention,
                            suffix_tokens,
                            full_attention,
                            cache,
                            staged,
                            cached_position_kwargs,
                        )
                except Exception as exc:
                    if evidence_execution_mode == "prefix-cache":
                        raise RuntimeError(
                            f"forced prefix-cache execution failed for {bundle.label}: {type(exc).__name__}: {exc}"
                        ) from exc
                    (
                        memory_parts,
                        option_answer,
                        option_predictor,
                        option_terminal,
                        option_prompt,
                        option_lexical,
                        option_logp,
                        residual_memory_parts,
                        residual_option_answer,
                        residual_option_predictor,
                        residual_option_terminal,
                        residual_option_prompt,
                    ) = new_accumulators()
                    forward_batches = 0
                    prefix_cache_used = False
                    shared_prefix_tokens = 0
                    prefix_cache_failure_reason = f"{type(exc).__name__}: {exc}"[:320]

            if evidence_execution_mode == "prefix-cache" and not prefix_cache_used:
                raise RuntimeError(
                    f"forced prefix-cache execution unavailable for {bundle.label}: "
                    f"paths={len(flat_rows)} common_prefix_tokens={prefix_len}"
                )

            if not prefix_cache_used:
                with torch.no_grad():
                    for offset in range(0, len(flat_rows), path_batch):
                        chunk = flat_rows[offset: offset + path_batch]
                        lengths = [
                            len(row["prompt_ids"]) + len(row["answer_ids"])
                            for _, row in chunk
                        ]
                        width = max(lengths)
                        tokens = torch.full(
                            (len(chunk), width), pad, dtype=torch.long, device=device
                        )
                        attention = torch.zeros(
                            (len(chunk), width), dtype=torch.long, device=device
                        )
                        for batch_index, (_question_index, row) in enumerate(chunk):
                            sequence = row["prompt_ids"] + row["answer_ids"]
                            tokens[batch_index, :len(sequence)] = torch.tensor(
                                sequence, dtype=torch.long, device=device
                            )
                            attention[batch_index, :len(sequence)] = 1
                        output = bundle.backbone(
                            input_ids=tokens,
                            attention_mask=attention,
                            use_cache=False,
                            output_hidden_states=residual_tap,
                            return_dict=True,
                        )
                        forward_batches += 1
                        residual_hidden, final_hidden = self._residual_hidden_states_from_output(
                            bundle, output
                        )
                        for batch_index, (question_index, row) in enumerate(chunk):
                            sequence_len = len(row["prompt_ids"]) + len(row["answer_ids"])
                            consume_row(
                                question_index,
                                row,
                                tuple(
                                    hidden[batch_index, :sequence_len]
                                    for hidden in residual_hidden
                                ),
                                final_hidden[batch_index, :sequence_len],
                                tokens[batch_index, :sequence_len],
                            )
                        del output, residual_hidden, final_hidden, tokens, attention

            def stack_mean(groups, label: str, question_id: str):
                values = []
                for candidate, items in enumerate(groups):
                    if not items:
                        raise RuntimeError(
                            f"{bundle.label} missing {label} evidence for {question_id} candidate {candidate}"
                        )
                    values.append(torch.stack(items, dim=0).mean(dim=0))
                return torch.stack(values, dim=0)

            evidence_rows = []
            stats_rows = []
            for question_index, question in enumerate(questions):
                memory = torch.cat(memory_parts[question_index], dim=0)
                row = {
                    "memory": memory,
                    "option_context": stack_mean(
                        option_answer[question_index], "answer", question.question_id
                    ),
                    "option_predictor": stack_mean(
                        option_predictor[question_index], "predictor", question.question_id
                    ),
                    "option_terminal": stack_mean(
                        option_terminal[question_index], "terminal", question.question_id
                    ),
                    "option_question": stack_mean(
                        option_prompt[question_index], "prompt", question.question_id
                    ),
                    "option_lexical": stack_mean(
                        option_lexical[question_index], "lexical", question.question_id
                    ),
                    "option_logp": stack_mean(
                        option_logp[question_index], "logp", question.question_id
                    ),
                    "global": memory.mean(dim=0),
                }
                if residual_tap:
                    residual_memory = torch.cat(residual_memory_parts[question_index], dim=0)
                    if int(residual_memory.shape[0]) != int(memory.shape[0]):
                        raise RuntimeError("TinyStories base/residual memory token counts diverged")
                    row.update({
                        "residual_source_memory": residual_memory,
                        "residual_source_option_context": stack_mean(
                            residual_option_answer[question_index], "residual answer", question.question_id
                        ),
                        "residual_source_option_predictor": stack_mean(
                            residual_option_predictor[question_index], "residual predictor", question.question_id
                        ),
                        "residual_source_option_terminal": stack_mean(
                            residual_option_terminal[question_index], "residual terminal", question.question_id
                        ),
                        "residual_source_option_question": stack_mean(
                            residual_option_prompt[question_index], "residual prompt", question.question_id
                        ),
                        "residual_source_global": residual_memory.mean(dim=0),
                    })
                evidence_rows.append(row)
                stats_rows.append({
                    "path_count": occurrence_counts[question_index],
                    "unique_path_count": len(question_rows[question_index]),
                    "memory_tokens": int(memory.shape[0]),
                    "shared_prefix_tokens": shared_prefix_tokens,
                    "shared_prefix_cache_used": prefix_cache_used,
                })
            return (
                evidence_rows,
                stats_rows,
                forward_batches,
                prefix_cache_used,
                shared_prefix_tokens,
                prefix_cache_candidate_tokens,
                prefix_cache_failure_reason,
            )
    def _pack_evidence_batch(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
            torch = self.torch
            if not rows:
                raise RuntimeError("cannot pack empty pairwise evidence batch")
            candidate_counts = {int(row["option_context"].shape[0]) for row in rows}
            if candidate_counts != {2}:
                raise RuntimeError(f"pairwise evidence candidate mismatch: {candidate_counts}")
            hidden = int(rows[0]["memory"].shape[-1])
            max_memory = max(int(row["memory"].shape[0]) for row in rows)
            device = rows[0]["memory"].device
            dtype = rows[0]["memory"].dtype
            memory = torch.zeros(
                (len(rows), max_memory, hidden), device=device, dtype=dtype
            )
            memory_valid = torch.zeros(
                (len(rows), max_memory), device=device, dtype=torch.bool
            )
            for index, row in enumerate(rows):
                count = int(row["memory"].shape[0])
                memory[index, :count] = row["memory"]
                memory_valid[index, :count] = True
            packed = {
                "memory": memory,
                "memory_valid": memory_valid,
                "option_context": torch.stack([row["option_context"] for row in rows], dim=0),
                "option_predictor": torch.stack([row["option_predictor"] for row in rows], dim=0),
                "option_terminal": torch.stack([row["option_terminal"] for row in rows], dim=0),
                "option_question": torch.stack([row["option_question"] for row in rows], dim=0),
                "option_lexical": torch.stack([row["option_lexical"] for row in rows], dim=0),
                "option_logp": torch.stack([row["option_logp"] for row in rows], dim=0),
                "global": torch.stack([row["global"] for row in rows], dim=0),
            }
            residual_keys = [
                "residual_source_memory",
                "residual_source_option_context",
                "residual_source_option_predictor",
                "residual_source_option_terminal",
                "residual_source_option_question",
                "residual_source_global",
            ]
            has_residual = [key in rows[0] for key in residual_keys]
            if any(has_residual):
                if not all(has_residual) or any(
                    any((key in row) != has_residual[index] for index, key in enumerate(residual_keys))
                    for row in rows
                ):
                    raise RuntimeError("pairwise residual evidence fields are incomplete")
                residual_hidden = int(rows[0]["residual_source_memory"].shape[-1])
                residual_memory = torch.zeros(
                    (len(rows), max_memory, residual_hidden), device=device, dtype=dtype
                )
                for index, row in enumerate(rows):
                    count = int(row["residual_source_memory"].shape[0])
                    if count != int(row["memory"].shape[0]):
                        raise RuntimeError("pairwise base/residual memory lengths disagree")
                    residual_memory[index, :count] = row["residual_source_memory"]
                packed.update({
                    "residual_source_memory": residual_memory,
                    "residual_source_option_context": torch.stack(
                        [row["residual_source_option_context"] for row in rows], dim=0
                    ),
                    "residual_source_option_predictor": torch.stack(
                        [row["residual_source_option_predictor"] for row in rows], dim=0
                    ),
                    "residual_source_option_terminal": torch.stack(
                        [row["residual_source_option_terminal"] for row in rows], dim=0
                    ),
                    "residual_source_option_question": torch.stack(
                        [row["residual_source_option_question"] for row in rows], dim=0
                    ),
                    "residual_source_global": torch.stack(
                        [row["residual_source_global"] for row in rows], dim=0
                    ),
                })
            return packed
    def _batched_head_forward(self, evidence: dict[str, dict[str, Any]]):
            """Vectorized equivalent of the trained CLEF head's single-question forward."""
            torch = self.torch
            import torch.nn.functional as F

            head = self.head
            if set(evidence) != set(head.labels):
                raise RuntimeError(
                    f"head evidence labels mismatch: expected={head.labels} observed={tuple(evidence)}"
                )
            option_count = None
            memory_parts = []
            memory_masks = []
            option_query_parts = []
            field_parts = []
            global_parts = []
            lexical_parts = []
            logp_prior_parts = []
            for label in head.labels:
                row = evidence[label]
                module = head.backbone_modules[label]

                def residual_corrected(field: str):
                    base_value = row[field]
                    if label != self.train.TRAINABLE_LABEL or not hasattr(head, "tinystories_residual"):
                        return base_value
                    source_key = f"residual_source_{field}"
                    if source_key not in row:
                        raise RuntimeError(f"TinyStories residual evidence missing: {source_key}")
                    source = head._normalize_residual_source(row[source_key])
                    correction = head.tinystories_residual[field](source).to(
                        device=base_value.device, dtype=base_value.dtype
                    )
                    return base_value + correction

                memory = module.hidden_norm(residual_corrected("memory"))
                option_context = module.hidden_norm(residual_corrected("option_context"))
                option_predictor = module.hidden_norm(residual_corrected("option_predictor"))
                option_terminal = module.hidden_norm(residual_corrected("option_terminal"))
                option_question = module.hidden_norm(residual_corrected("option_question"))
                lexical = module.hidden_norm(row["option_lexical"])
                global_vector = module.hidden_norm(residual_corrected("global"))
                option_logp = row["option_logp"].to(
                    device=option_context.device, dtype=option_context.dtype
                ).reshape(option_context.shape[0], option_context.shape[1], 1)
                centered_logp = option_logp - option_logp.mean(dim=1, keepdim=True)
                if option_count is None:
                    option_count = int(option_context.shape[1])
                elif option_count != int(option_context.shape[1]):
                    raise RuntimeError("backbone evidence disagrees on candidate count")
                embed = head.model_embeddings[label]
                memory_parts.append(module.memory_projection(memory) + embed)
                memory_masks.append(row["memory_valid"])
                option_query_parts.append(
                    module.option_context_projection(
                        (option_context + option_predictor + option_terminal) / math.sqrt(3.0)
                    )
                    + module.option_lexical_projection(lexical)
                    + module.option_question_projection(option_question)
                    + module.option_logp_projection(centered_logp)
                )
                field_parts.append(module.question_projection(option_question.mean(dim=1)))
                global_parts.append(module.global_projection(global_vector))
                lexical_parts.append(module.option_lexical_projection(lexical))
                logp_prior_parts.append(module.option_logp_scalar(centered_logp).squeeze(-1))

            scale = 1.0 / math.sqrt(float(len(head.labels)))
            memory = torch.cat(memory_parts, dim=1)
            memory_valid = torch.cat(memory_masks, dim=1)
            options = torch.stack(option_query_parts, dim=0).sum(dim=0) * scale
            lexical = torch.stack(lexical_parts, dim=0).sum(dim=0) * scale
            logp_prior = torch.stack(logp_prior_parts, dim=0).sum(dim=0) * scale
            base_field = torch.stack(field_parts, dim=0).sum(dim=0) * scale
            global_vector = torch.stack(global_parts, dim=0).sum(dim=0) * scale

            routed = options
            for layer in head.evidence_layers:
                q = layer.query_norm(routed)
                m = layer.memory_norm(memory)
                routed_delta, _ = layer.attention(
                    q,
                    m,
                    m,
                    key_padding_mask=~memory_valid,
                    need_weights=False,
                )
                queries = routed + routed_delta
                routed = queries + layer.ff(layer.ff_norm(queries))

            routing_weights = torch.softmax(
                torch.sum(routed * base_field.unsqueeze(1), dim=-1)
                / math.sqrt(float(head.width)),
                dim=1,
            )
            option_summary = torch.sum(routing_weights.unsqueeze(-1) * routed, dim=1)
            field = base_field + head.option_summary_norm(option_summary) + global_vector
            field = field + head.type_embedding.weight[1]
            field = field + head.fusion_ff(head.fusion_norm(field))
            target = field.unsqueeze(1)
            for layer in head.layers:
                target = layer(
                    target,
                    memory,
                    memory_key_padding_mask=~memory_valid,
                )
            field = head.field_norm(target[:, 0])

            normalized_lexical = F.normalize(lexical, dim=-1)
            normalized_field = F.normalize(field, dim=-1).unsqueeze(1).expand_as(lexical)
            lexical_prior = F.cosine_similarity(
                normalized_lexical,
                normalized_field,
                dim=-1,
            )
            prior_scale = head.prior_logit_scale.clamp(max=math.log(100.0)).exp()
            option_values = head.option_norm(routed)
            repeated_field = field.unsqueeze(1).expand_as(option_values)
            cosine = F.cosine_similarity(repeated_field, option_values, dim=-1)
            features = torch.cat(
                [
                    repeated_field,
                    option_values,
                    repeated_field * option_values,
                    torch.abs(repeated_field - option_values),
                ],
                dim=-1,
            )
            residual = head.residual_scorer(features).squeeze(-1)
            joint_scale = head.joint_logit_scale.clamp(max=math.log(100.0)).exp()
            joint = joint_scale * cosine + residual
            return (
                prior_scale * lexical_prior
                + logp_prior
                + torch.sigmoid(head.residual_gate) * joint
            )

    def _pairwise_question(self, row: Mapping[str, Any], shared_context: str):
        question_id = str(row.get("id") or "").strip()
        prompt = str(row.get("prompt") or row.get("question") or "").strip()
        candidates = row.get("candidates")
        if not question_id or not prompt:
            raise ValueError("each pairwise question requires nonempty id and prompt")
        if not isinstance(candidates, list) or len(candidates) != 2:
            raise ValueError(f"{question_id}: candidates must contain exactly two entries")
        normalized = []
        seen = set()
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or set(candidate) != {"id", "text"}:
                raise ValueError(f"{question_id}: candidate entries must contain exactly id and text")
            cid = str(candidate.get("id") or "").strip()
            ctext = str(candidate.get("text") or "").strip()
            if not cid or cid in seen or not ctext:
                raise ValueError(f"{question_id}: candidate ids/text must be unique and nonempty")
            if len(cid) > 128 or len(ctext) > 4000:
                raise ValueError(f"{question_id}: candidate id/text exceeds service bounds")
            seen.add(cid)
            normalized.append((cid, ctext))
        if not shared_context or len(shared_context) > 16000:
            raise ValueError("shared_context must be 1..16000 characters")
        if len(prompt) > 4000:
            raise ValueError(f"{question_id}: prompt exceeds service bound")
        choice_prompt = (
            shared_context.rstrip()
            + "\n"
            + prompt.rstrip()
            + f"\nA: {normalized[0][1]}"
            + f"\nB: {normalized[1][1]}"
            + "\nAnswer A or B:"
        )
        object_candidates = (
            self.objective_api.ObjectCandidate(
                normalized[0][0], (self.objective_api.ObjectPath(choice_prompt, " A"),)
            ),
            self.objective_api.ObjectCandidate(
                normalized[1][0], (self.objective_api.ObjectPath(choice_prompt, " B"),)
            ),
        )
        return self.objective_api.ObjectQuestion(
            question_id=question_id,
            task="service_pairwise_batch",
            candidates=object_candidates,
            gold_index=0,
            stratum="service",
        )

    def evaluate_pairwise_batch(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("pairwise batch request must be an object")
        allowed = {"schema", "shared_context", "execution", "questions"}
        if set(payload) - allowed:
            raise ValueError("pairwise batch request contains unsupported fields")
        if str(payload.get("schema") or "") != "nanojev.pairwise-batch.v1":
            raise ValueError("schema must be nanojev.pairwise-batch.v1")
        context = payload.get("shared_context")
        if isinstance(context, Mapping):
            if set(context) != {"text"}:
                raise ValueError("shared_context object may contain only text")
            shared_context = str(context.get("text") or "").strip()
        else:
            shared_context = str(context or "").strip()
        rows = payload.get("questions")
        if not isinstance(rows, list) or not rows or len(rows) > 64:
            raise ValueError("questions must contain 1..64 entries")
        seen_ids = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("pairwise questions must be objects")
            qid = str(row.get("id") or "").strip()
            if not qid or qid in seen_ids:
                raise ValueError("pairwise question ids must be unique nonempty strings")
            seen_ids.add(qid)
        execution = payload.get("execution") or {}
        if not isinstance(execution, Mapping) or set(execution) - {"evidence_mode"}:
            raise ValueError("execution may contain only evidence_mode")
        requested_mode = str(execution.get("evidence_mode") or "auto").strip().lower()
        if requested_mode not in {"auto", "prefix-cache", "full-batch"}:
            raise ValueError("evidence_mode must be auto, prefix-cache, or full-batch")
        questions = [self._pairwise_question(row, shared_context) for row in rows]
        torch = self.torch
        started = time.perf_counter()
        by_label = {}
        evidence_stats = {}
        prefix_cache_by_label = {}
        shared_prefix_tokens_by_label = {}
        prefix_cache_candidate_tokens_by_label = {}
        prefix_cache_failure_by_label = {}
        backbone_forward_batches = 0
        with self.lock, torch.inference_mode():
            for label, bundle in self.bundles.items():
                (
                    evidence_rows,
                    stats,
                    forward_batches,
                    prefix_cache_used,
                    shared_prefix_tokens,
                    prefix_cache_candidate_tokens,
                    prefix_cache_failure_reason,
                ) = self._extract_bundle_batch(
                    bundle, questions, evidence_execution_mode=requested_mode
                )
                by_label[label] = self._pack_evidence_batch(evidence_rows)
                evidence_stats[label] = stats
                prefix_cache_by_label[label] = bool(prefix_cache_used)
                shared_prefix_tokens_by_label[label] = int(shared_prefix_tokens)
                prefix_cache_candidate_tokens_by_label[label] = int(prefix_cache_candidate_tokens)
                prefix_cache_failure_by_label[label] = prefix_cache_failure_reason
                backbone_forward_batches += int(forward_batches)
            logits = self._batched_head_forward(by_label)
            probabilities = logits.detach().float().softmax(dim=-1).cpu()
            if torch.cuda.is_available():
                torch.cuda.synchronize()
        total_ms = (time.perf_counter() - started) * 1000.0
        results = []
        for index, question in enumerate(questions):
            candidate_ids = [str(candidate.candidate_id) for candidate in question.candidates]
            values = [float(v) for v in probabilities[index].tolist()]
            best = max(range(len(values)), key=values.__getitem__)
            ordered = sorted(values, reverse=True)
            margin = ordered[0] - ordered[1]
            results.append({
                "id": question.question_id,
                "choice": candidate_ids[best],
                "candidate_ids": candidate_ids,
                "probabilities": values,
                "margin": float(margin),
            })
        if prefix_cache_by_label and all(prefix_cache_by_label.values()):
            actual_mode = "prefix-cache"
        elif prefix_cache_by_label and not any(prefix_cache_by_label.values()):
            actual_mode = "full-batch"
        else:
            actual_mode = "mixed"
        return {
            "schema": "nanojev.pairwise-batch-result.v1",
            "model": {
                "family": "nanojev-clef",
                "repo_id": self.repo_id,
                "selector": self.revision,
                "revision": self.resolved_revision,
                "release_name": self.release_name,
                "release_manifest_sha256": self.release_manifest_sha256,
                "source": self.checkpoint_source,
            },
            "results": results,
            "metrics": {
                "model_latency_ms": float(total_ms),
                "amortized_question_latency_ms": float(total_ms / len(results)),
                "head_forward_count": 1,
                "independent_judgment_count": len(results),
                "backbone_forward_batch_count": int(backbone_forward_batches),
                "evidence_mode_requested": requested_mode,
                "evidence_mode_actual": actual_mode,
                "shared_context_chars": len(shared_context),
                "shared_prefix_cache_used": bool(prefix_cache_by_label) and all(prefix_cache_by_label.values()),
                "shared_prefix_cache_by_backbone": prefix_cache_by_label,
                "shared_prefix_tokens_by_backbone": shared_prefix_tokens_by_label,
                "shared_prefix_candidate_tokens_by_backbone": prefix_cache_candidate_tokens_by_label,
                "shared_prefix_cache_failure_by_backbone": prefix_cache_failure_by_label,
                "evidence_stats": evidence_stats,
            },
        }

    def evaluate(self, payload: Any) -> dict[str, Any]:
        states = validate_request(payload)
        output_states: list[dict[str, Any]] = []
        with self.lock, self.torch.no_grad():
            for row in states:
                answers: dict[str, Any] = {}
                for qid, raw_question in row["questions"].items():
                    question, ids, typ = build_question(row["state"], str(qid), raw_question)
                    evidence = self.structured.extract_live_evidence(
                        torch=self.torch,
                        bundles=self.bundles,
                        question=question,
                        args=self.args,
                        logger=self.logger,
                    )
                    logits = self.head(evidence)
                    probabilities = self.torch.softmax(logits.float(), dim=-1).detach().cpu().tolist()
                    answers[str(qid)] = answer_from_probabilities(ids, typ, probabilities)
                    del evidence, logits
                output_states.append({"id": row["id"], "answers": answers})
        return {
            "states": output_states,
            "model": {
                "family": "nanojev-clef",
                "repo_id": self.repo_id,
                "selector": self.revision,
                "revision": self.resolved_revision,
                "release_name": self.release_name,
            "release_manifest_sha256": self.release_manifest_sha256,
            "checkpoint_cycle": ((self.release.get("checkpoint") or {}).get("cycle")),
            "checkpoint_reuse_depth": ((self.release.get("checkpoint") or {}).get("reuse_depth")),
            },
        }


class ClefServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], runtime: ClefRuntime) -> None:
        super().__init__(address, ClefHandler)
        self.runtime = runtime


class ClefHandler(BaseHTTPRequestHandler):
    server: ClefServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"nanojev-clef {self.address_string()} {fmt % args}", flush=True)

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/health":
            self._send(200, self.server.runtime.health())
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/api/evaluate", "/api/evaluate-batch"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > 16 * 1024 * 1024:
                raise ValueError("request body must be 1..16777216 bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/api/evaluate-batch":
                result = self.server.runtime.evaluate_pairwise_batch(payload)
            else:
                result = self.server.runtime.evaluate(payload)
            self._send(200, result)
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a released Main Computer NanoJev CLEF checkpoint")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9765)
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runtime = ClefRuntime(repo_id=args.repo_id, revision=args.revision)
    server = ClefServer((args.host, int(args.port)), runtime)
    print(
        json.dumps(
            {
                "event": "nanojev_clef_service_ready",
                "host": args.host,
                "port": int(args.port),
                **runtime.health(),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
