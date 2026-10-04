#!/usr/bin/env python3
"""Serve the current trained TinyStories+CLEF checkpoint for captain smoke calls.

This backend intentionally uses the same three-backbone evidence extractor and CLEF
head class as the live TinyStories training path.  Qwen and Pythia remain frozen
context providers; the checkpoint supplies the evolving TinyStories and CLEF-head
weights.  The HTTP surface is deliberately tiny so the browser/game can later call
exactly the same boundary.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DEFAULT_RUN = Path(
    r"C:\Users\subsi\NanoJev\runs\three_backbone_clef_tinystories_consensus_pairwise_unique2560_stream1_train_v1"
)


def load_local_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def composite_checkpoint_sha(checkpoint: Path) -> str:
    payload = {
        "head.safetensors": sha256_file(checkpoint / "head.safetensors"),
        "tinystories.safetensors": sha256_file(checkpoint / "tinystories.safetensors"),
        "meta.json": sha256_file(checkpoint / "meta.json"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def resolve_run_dir(requested: str | None) -> Path:
    if requested:
        return Path(requested).expanduser().resolve(strict=True)
    if DEFAULT_RUN.is_dir():
        return DEFAULT_RUN.resolve(strict=True)
    runs = Path.home() / "NanoJev" / "runs"
    candidates = []
    if runs.is_dir():
        for candidate in runs.glob("three_backbone_clef_tinystories_consensus_pairwise_*train_v1"):
            if (candidate / "training_state.json").is_file() and (candidate / "experiment.json").is_file():
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError(
            "could not locate the live TinyStories+CLEF training run; pass --run-dir"
        )
    return max(candidates, key=lambda path: (path / "training_state.json").stat().st_mtime).resolve()


class NullLogger:
    def emit(self, *_args, **_kwargs) -> None:
        return None


class CaptainClefModel:
    def __init__(self, run_dir: Path, checkpoint: Path | None = None) -> None:
        import torch
        from safetensors.torch import load_file

        if not torch.cuda.is_available():
            raise RuntimeError("live captain CLEF backend requires CUDA")
        if not torch.cuda.is_bf16_supported():
            raise RuntimeError("live captain CLEF backend requires bf16-capable CUDA")

        self.torch = torch
        self.run_dir = Path(run_dir).resolve(strict=True)
        experiment = read_json(self.run_dir / "experiment.json")
        state = read_json(self.run_dir / "training_state.json")
        if checkpoint is None:
            checkpoint = Path(str(state["latest_checkpoint"]))
        self.checkpoint = Path(checkpoint).expanduser().resolve(strict=True)
        for name in ("head.safetensors", "tinystories.safetensors", "meta.json"):
            if not (self.checkpoint / name).is_file():
                raise RuntimeError(f"checkpoint missing {name}: {self.checkpoint}")

        self.checkpoint_id = self.checkpoint.name
        self.checkpoint_sha256 = composite_checkpoint_sha(self.checkpoint)
        self.checkpoint_meta = read_json(self.checkpoint / "meta.json")

        cutover_dir = Path(str(experiment["cutover_dir"])).expanduser().resolve(strict=True)
        cutover = read_json(cutover_dir / "cutover.json")
        source_experiment = Path(
            str(cutover["question_source_experiment"])
        ).expanduser().resolve(strict=True)
        source_manifest = read_json(source_experiment / "experiment.json")
        source = dict(source_manifest.get("source") or {})
        required = ("model", "revision")
        missing = [name for name in required if not source.get(name)]
        if missing:
            raise RuntimeError(f"question-source lineage missing fields: {missing}")

        self.train = load_local_module(
            "space_captain_live_clef_train",
            TOOLS / "nanojev_three_backbone_clef_tinystories_consensus_pairwise_train.py",
        )
        self.smoke = self.train.smoke
        self.objective_api = load_local_module(
            "space_captain_live_objective_api", TOOLS / "nanojev_objective_api.py"
        )

        torch.backends.cuda.matmul.allow_tf32 = False
        torch.manual_seed(int(experiment.get("seed", 20261003)))
        torch.cuda.manual_seed_all(int(experiment.get("seed", 20261003)))
        self.bundles, _ = self.smoke.load_backbones(
            source=source,
            local_files_only=True,
            logger=NullLogger(),
        )
        hidden_sizes = {label: bundle.hidden_size for label, bundle in self.bundles.items()}
        expected_hidden = {"qwen": 1024, "pythia": 512, "tinystories": 768}
        if hidden_sizes != expected_hidden:
            raise RuntimeError(
                f"unexpected backbone hidden sizes: expected={expected_hidden} observed={hidden_sizes}"
            )

        self.bundles[self.train.TRAINABLE_LABEL].lm.load_state_dict(
            load_file(str(self.checkpoint / "tinystories.safetensors"), device="cpu"),
            strict=True,
        )
        for bundle in self.bundles.values():
            bundle.lm.eval()

        Head = self.smoke.build_head_class()
        head = Head(hidden_sizes)
        head.load_state_dict(
            load_file(str(self.checkpoint / "head.safetensors"), device="cpu"),
            strict=True,
        )
        self.head = head.to(device="cuda", dtype=torch.bfloat16).eval()

        hparams = dict(experiment.get("hyperparameters") or {})
        self.evidence_args = SimpleNamespace(
            max_prompt_tokens=int(hparams.get("max_prompt_tokens", self.smoke.DEFAULT_MAX_PROMPT_TOKENS)),
            max_answer_tokens=int(hparams.get("max_answer_tokens", self.smoke.DEFAULT_MAX_ANSWER_TOKENS)),
            prompt_evidence_tokens=int(hparams.get("prompt_evidence_tokens", self.smoke.DEFAULT_PROMPT_EVIDENCE_TOKENS)),
            answer_evidence_tokens=int(hparams.get("answer_evidence_tokens", self.smoke.DEFAULT_ANSWER_EVIDENCE_TOKENS)),
            path_batch=max(3, int(hparams.get("path_batch", self.smoke.DEFAULT_PATH_BATCH))),
            tinystories_path_batch=int(hparams.get("tinystories_path_batch", 1)),
        )
        self.loaded_unix = time.time()
        self.parameter_counts = {
            "head": sum(int(p.numel()) for p in self.head.parameters()),
            "tinystories": sum(
                int(p.numel()) for p in self.bundles[self.train.TRAINABLE_LABEL].lm.parameters()
            ),
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "game.captainClefHealth.v1",
            "provider": "live-tinystories-clef",
            "runDir": str(self.run_dir),
            "checkpointPath": str(self.checkpoint),
            "checkpointId": self.checkpoint_id,
            "checkpointSha256": self.checkpoint_sha256,
            "cycle": self.checkpoint_meta.get("cycle"),
            "reuseEpoch": self.checkpoint_meta.get("reuse_epoch"),
            "parameterCounts": self.parameter_counts,
            "loadedUnix": self.loaded_unix,
        }

    def _ensemble_question(self, payload: dict[str, Any]):
        """Compile one captain timepoint into one three-way CLEF object question.

        The jacket may generate many redundant probes, but a captain timepoint is one
        model call.  The probes are therefore context inside a single prompt and the
        CLEF head scores CLOSE/HOLD/WITHDRAW once for the whole timepoint.
        """
        jacket = dict(payload.get("jacket") or {})
        observation = dict(payload.get("observation") or {})
        timestep = dict(payload.get("timestep") or {})
        probes = list(payload.get("questions") or [])
        if not probes:
            raise ValueError("captain request contains no jacket probes")
        if len(probes) > 64:
            raise ValueError("captain request exceeds 64-probe live smoke bound")

        jacket_id = str(jacket.get("id") or "captain.unknown")
        label = str(jacket.get("label") or jacket_id)
        narrative = str(jacket.get("narrative") or "").strip()
        utilities = dict(jacket.get("actionUtility") or {})
        expected = str(jacket.get("expectedIntent") or "").strip().lower()
        sample_time = float(timestep.get("sampleTimeSeconds") or 0.0)

        lines = [
            f"Captain: {label} ({jacket_id}).",
            f"Jacket: {narrative}",
            "Structured doctrine utilities: "
            + ", ".join(
                f"{intent}={float(utilities.get(intent, 0.0)):.3f}"
                for intent in ("close", "hold", "withdraw")
            )
            + ".",
            (
                "Current physical observation: "
                f"range={float(observation.get('rangeM') or 0.0):.3f} m, "
                f"radial_velocity={float(observation.get('radialVelocityMps') or 0.0):.6f} m/s, "
                f"simulation_time={sample_time:.6f} s."
            ),
            "The following jacket probes are deliberately redundant. A minority may be noisy or internally inconsistent; infer the captain's stable policy across the whole set:",
        ]
        for index, row in enumerate(probes, 1):
            text = str(row.get("text") or "").strip()
            if not text:
                raise ValueError(f"captain probe {index} has no text")
            lines.append(f"{index}. {text}")
        lines.extend([
            "Choose exactly one captain intent for this timepoint: CLOSE, HOLD, or WITHDRAW.",
            "Return the intent that best represents the captain's jacket under the observed physical state.",
            "Answer:",
        ])
        prompt = "\n".join(lines)
        candidates = tuple(
            self.objective_api.ObjectCandidate(
                intent,
                (self.objective_api.ObjectPath(prompt, " " + intent.upper()),),
            )
            for intent in ("close", "hold", "withdraw")
        )
        question = self.objective_api.ObjectQuestion(
            question_id=f"{jacket_id}@{sample_time:.6f}",
            task="captain_ensemble",
            candidates=candidates,
            gold_index=("close", "hold", "withdraw").index(expected) if expected in {"close", "hold", "withdraw"} else 0,
            stratum="captain",
        )
        return question, prompt

    def evaluate_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        checkpoint = dict(payload.get("checkpoint") or {})
        requested_id = str(checkpoint.get("checkpointId") or "")
        requested_sha = str(checkpoint.get("sha256") or "")
        if requested_id and requested_id != self.checkpoint_id:
            raise ValueError(
                f"request checkpoint id mismatch: loaded={self.checkpoint_id} requested={requested_id}"
            )
        if requested_sha and requested_sha != self.checkpoint_sha256:
            raise ValueError(
                "request checkpoint sha mismatch: "
                f"loaded={self.checkpoint_sha256} requested={requested_sha}"
            )

        torch = self.torch
        question, compiled_prompt = self._ensemble_question(payload)
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        started = time.perf_counter()
        evidence: dict[str, dict[str, Any]] = {}
        evidence_stats: dict[str, Any] = {}
        backbone_forward_batches = 0
        with torch.inference_mode():
            for label, bundle in self.bundles.items():
                direct, stats = self.train.extract_one_bundle(
                    torch=torch,
                    bundle=bundle,
                    question=question,
                    args=self.evidence_args,
                    track_grad=False,
                )
                evidence[label] = direct
                evidence_stats[label] = stats
                path_count = int(stats.get("path_count") or 0)
                path_batch = max(1, int(self.evidence_args.path_batch))
                backbone_forward_batches += int(math.ceil(path_count / path_batch))
            logits = self.head(evidence)
            probabilities = logits.detach().float().softmax(dim=-1).cpu().tolist()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
        total_ms = (time.perf_counter() - started) * 1000.0

        candidate_ids = [str(candidate.candidate_id) for candidate in question.candidates]
        predicted_index = max(range(len(probabilities)), key=lambda index: probabilities[index])
        ordered = sorted(probabilities, reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        scores = {candidate_ids[index]: float(probabilities[index]) for index in range(len(candidate_ids))}
        result = {
            "schema": "game.captainDecisionResponse.v2",
            "checkpointId": self.checkpoint_id,
            "checkpointSha256": self.checkpoint_sha256,
            "provider": "live-tinystories-clef",
            "intent": candidate_ids[predicted_index],
            "intentProbabilities": scores,
            "margin": float(margin),
            "modelLatencyMs": float(total_ms),
            "captainModelCallCount": 1,
            "clefHeadForwardCount": 1,
            "backboneForwardBatchCount": int(backbone_forward_batches),
            "probeCount": len(payload.get("questions") or []),
            "compiledPromptChars": len(compiled_prompt),
            "evidenceStats": evidence_stats,
        }
        del evidence, logits
        return result


class CaptainServer(HTTPServer):
    model: CaptainClefModel


class Handler(BaseHTTPRequestHandler):
    server_version = "MainComputerCaptainCLEF/1"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:
        print(format % args, file=sys.stderr, flush=True)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, self.server.model.health())
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/captain/evaluate":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("invalid captain request content length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            response = self.server.model.evaluate_request(payload)
            self._json(200, response)
        except Exception as exc:
            self._json(400, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the live TinyStories+CLEF captain checkpoint")
    parser.add_argument("--run-dir", default="")
    parser.add_argument("--checkpoint", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    run_dir = resolve_run_dir(args.run_dir or None)
    checkpoint = Path(args.checkpoint).expanduser().resolve(strict=True) if args.checkpoint else None
    model = CaptainClefModel(run_dir, checkpoint)
    server = CaptainServer((args.host, int(args.port)), Handler)
    server.model = model
    print(json.dumps({
        "event": "space_captain_clef_backend_ready",
        "host": args.host,
        "port": server.server_address[1],
        **model.health(),
    }), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
