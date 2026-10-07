#!/usr/bin/env python3
"""Space Captain protocol adapter for the generic managed NanoJev service.

This process owns only the game-specific HTTP contract.  It does not discover training
runs, select checkpoints, load model weights, or own CUDA.  All model lifecycle and
inference are delegated to the generic NanoJev service at /api/evaluate-batch.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import time
from typing import Any
import urllib.error
import urllib.request

DEFAULT_SERVICE_URL = "http://127.0.0.1:9765"
MAX_BODY_BYTES = 2_000_000


def emit_diagnostic(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), default=str), file=sys.stderr, flush=True)


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 300.0) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url=url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        detail = raw.decode("utf-8", errors="replace")
        raise RuntimeError(f"NanoJev service HTTP {exc.code}: {detail}") from exc
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("NanoJev service returned a non-object JSON response")
    return value


class ServiceAdapter:
    def __init__(self, service_url: str) -> None:
        self.service_url = service_url.rstrip("/")

    def service_health(self) -> dict[str, Any]:
        health = http_json(self.service_url + "/api/health", timeout=300.0)
        if not bool(health.get("ready")) or health.get("model_family") != "nanojev-clef":
            raise RuntimeError(f"managed NanoJev service is not ready for CLEF: {health}")
        return health

    @staticmethod
    def _checkpoint_id(health: dict[str, Any]) -> str:
        return str(health.get("release_name") or health.get("checkpoint_revision_resolved") or health.get("checkpoint_selector") or "")

    @staticmethod
    def _checkpoint_sha(health: dict[str, Any]) -> str:
        return str(health.get("release_manifest_sha256") or health.get("checkpoint_revision_resolved") or "")

    def health(self) -> dict[str, Any]:
        health = self.service_health()
        return {
            "ok": True,
            "schema": "game.captainClefHealth.v1",
            "provider": "managed-nanojev-clef",
            "serviceUrl": self.service_url,
            "checkpointId": self._checkpoint_id(health),
            "checkpointSha256": self._checkpoint_sha(health),
            "cycle": health.get("checkpoint_cycle"),
            "reuseEpoch": health.get("checkpoint_reuse_depth"),
            "checkpointSource": health.get("checkpoint_source"),
            "checkpointSelector": health.get("checkpoint_selector"),
            "checkpointRepo": health.get("checkpoint_repo"),
            "checkpointRevisionResolved": health.get("checkpoint_revision_resolved"),
            "releaseName": health.get("release_name"),
            "modelFamily": health.get("model_family"),
        }

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        if str(payload.get("schema") or "") != "game.captainDecisionRequest.v6":
            raise ValueError("unsupported captain request schema")
        semantic_context = payload.get("semanticContext") or {}
        shared_context = str(semantic_context.get("text") or "").strip()
        if not shared_context:
            raise ValueError("captain request is missing semanticContext.text")
        question_rows = payload.get("questions")
        if not isinstance(question_rows, list) or not question_rows:
            raise ValueError("captain request contains no questions")
        if len(question_rows) > 64:
            raise ValueError("captain request exceeds 64 questions")
        questions = []
        for row in question_rows:
            if not isinstance(row, dict):
                raise ValueError("captain questions must be objects")
            qid = str(row.get("id") or row.get("questionId") or "").strip()
            prompt = str(row.get("text") or "").strip()
            a = str(row.get("optionA") or "").strip()
            b = str(row.get("optionB") or "").strip()
            at = str(row.get("optionAText") or "").strip()
            bt = str(row.get("optionBText") or "").strip()
            if not all((qid, prompt, a, b, at, bt)) or a == b:
                raise ValueError(f"invalid captain pairwise question: {row}")
            questions.append({
                "id": qid,
                "prompt": prompt,
                "candidates": [{"id": a, "text": at}, {"id": b, "text": bt}],
            })
        execution = payload.get("execution") or {}
        evidence_mode = str(execution.get("evidenceMode") or "auto").strip().lower()
        generic_request = {
            "schema": "nanojev.pairwise-batch.v1",
            "shared_context": {"text": shared_context},
            "execution": {"evidence_mode": evidence_mode},
            "questions": questions,
        }
        started = time.perf_counter()
        response = http_json(
            self.service_url + "/api/evaluate-batch",
            method="POST",
            payload=generic_request,
            timeout=300.0,
        )
        wall_ms = (time.perf_counter() - started) * 1000.0
        if response.get("schema") != "nanojev.pairwise-batch-result.v1":
            raise RuntimeError(f"unexpected NanoJev batch response: {response}")
        model = dict(response.get("model") or {})
        checkpoint_id = str(model.get("release_name") or model.get("revision") or "")
        checkpoint_sha = str(model.get("release_manifest_sha256") or model.get("revision") or "")
        requested = dict(payload.get("checkpoint") or {})
        requested_id = str(requested.get("checkpointId") or "")
        requested_sha = str(requested.get("sha256") or "")
        if requested_id and requested_id != checkpoint_id:
            raise RuntimeError(
                f"captain checkpoint changed during episode: expected {requested_id}, got {checkpoint_id}"
            )
        if requested_sha and requested_sha != checkpoint_sha:
            raise RuntimeError(
                f"captain checkpoint sha changed during episode: expected {requested_sha}, got {checkpoint_sha}"
            )
        metrics = dict(response.get("metrics") or {})
        answers = []
        for row in response.get("results") or []:
            answers.append({
                "questionId": str(row.get("id") or ""),
                "choice": str(row.get("choice") or ""),
                "candidateIds": [str(v) for v in (row.get("candidate_ids") or [])],
                "probabilities": [float(v) for v in (row.get("probabilities") or [])],
                "margin": float(row.get("margin") or 0.0),
            })
        model_ms = float(metrics.get("model_latency_ms") or 0.0)
        return {
            "schema": "game.captainDecisionResponse.v6",
            "checkpointId": checkpoint_id,
            "checkpointSha256": checkpoint_sha,
            "provider": "managed-nanojev-clef",
            "modelLatencyMs": model_ms,
            "amortizedQuestionLatencyMs": float(metrics.get("amortized_question_latency_ms") or 0.0),
            "captainModelCallCount": 1,
            "clefHeadForwardCount": int(metrics.get("head_forward_count") or 0),
            "independentJudgmentCount": int(metrics.get("independent_judgment_count") or len(answers)),
            "backboneForwardBatchCount": int(metrics.get("backbone_forward_batch_count") or 0),
            "batchingMode": "independent-pairwise-questions",
            "evidenceExecutionModeRequested": str(metrics.get("evidence_mode_requested") or evidence_mode),
            "evidenceExecutionModeActual": str(metrics.get("evidence_mode_actual") or ""),
            "semanticChoicePromptMode": "compact-shared-context-a-b-v2",
            "semanticContextMode": "compact-shared-context-v2",
            "sharedContextChars": int(metrics.get("shared_context_chars") or len(shared_context)),
            "meanQuestionTextChars": float(sum(len(str(row.get("text") or "")) for row in question_rows) / len(question_rows)),
            "sharedPrefixCacheUsed": bool(metrics.get("shared_prefix_cache_used")),
            "sharedPrefixCacheByBackbone": dict(metrics.get("shared_prefix_cache_by_backbone") or {}),
            "sharedPrefixTokensByBackbone": dict(metrics.get("shared_prefix_tokens_by_backbone") or {}),
            "sharedPrefixCandidateTokensByBackbone": dict(metrics.get("shared_prefix_candidate_tokens_by_backbone") or {}),
            "sharedPrefixCacheFailureByBackbone": dict(metrics.get("shared_prefix_cache_failure_by_backbone") or {}),
            "serverDiagnostics": {
                "adapterWallMs": float(wall_ms),
                "modelMeasuredMs": model_ms,
                "checkpointSource": model.get("source"),
            },
            "answers": answers,
            "evidenceStats": dict(metrics.get("evidence_stats") or {}),
        }


class AdapterServer(ThreadingHTTPServer):
    daemon_threads = True
    adapter: ServiceAdapter


class Handler(BaseHTTPRequestHandler):
    server: AdapterServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"space-captain-nanojev-adapter {self.address_string()} {fmt % args}", file=sys.stderr, flush=True)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            try:
                self._json(200, self.server.adapter.health())
            except Exception as exc:
                self._json(503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/captain/evaluate":
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("invalid captain request content length")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("captain request must be an object")
            self._json(200, self.server.adapter.evaluate(payload))
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            emit_diagnostic("captain_adapter_error", errorType=type(exc).__name__, error=str(exc))
            self._json(503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Adapt Space Captain requests to the managed NanoJev service")
    parser.add_argument("--service-url", default=DEFAULT_SERVICE_URL)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    # Accepted only so older smoke launch commands do not regain model ownership.
    parser.add_argument("--run-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--checkpoint", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    adapter = ServiceAdapter(str(args.service_url))
    initial = adapter.health()  # Also wakes the lazy managed service.
    server = AdapterServer((args.host, int(args.port)), Handler)
    server.adapter = adapter
    print(json.dumps({
        "event": "space_captain_nanojev_adapter_ready",
        "host": args.host,
        "port": server.server_address[1],
        **initial,
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
