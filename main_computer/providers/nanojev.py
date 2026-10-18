"""Local NanoJev/CLEF structured-solution provider.

This module intentionally has no Hub transport, worker, or settlement integration.
CLEF is not a conversational model; preserve its typed result and checkpoint
metadata without translating arbitrary chat prompts into CLEF questions.
"""
from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.base import LLMProvider

CLEF_EVALUATE = "nanojev.clef.evaluate.v1"
CLEF_PAIRWISE = "nanojev.clef.pairwise-batch.v1"
CLEF_CAPABILITIES = (CLEF_EVALUATE, CLEF_PAIRWISE)
CLEF_MODEL = "nanojev-clef"
CLEF_BASE_URL = "http://127.0.0.1:9765"


class NanoJevProviderError(RuntimeError):
    """CLEF service failed or its response violated the local provider contract."""


class NanoJevClefProvider(LLMProvider):
    name = "nanojev"
    capabilities = CLEF_CAPABILITIES

    def __init__(self, *, model: str = CLEF_MODEL, base_url: str = CLEF_BASE_URL,
                 timeout_s: float = 600.0) -> None:
        self.model = str(model).strip() or CLEF_MODEL
        if self.model != CLEF_MODEL:
            raise NanoJevProviderError(f"Unsupported CLEF model: {self.model!r}")
        self.base_url = str(base_url).rstrip("/")
        self.timeout_s = float(timeout_s)
        parsed = urlparse(self.base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
            raise NanoJevProviderError("CLEF base URL must be an http(s) origin without credentials or path")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise NanoJevProviderError("Remote CLEF service requires HTTPS")
        if not 0 < self.timeout_s < float("inf"):
            raise NanoJevProviderError("CLEF timeout must be finite and positive")

    def _json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path, data=body, method="POST" if body is not None else "GET",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                # Prevent accidental unbounded reads from an incorrectly configured endpoint.
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise NanoJevProviderError("CLEF response exceeds 16 MiB")
                data = json.loads(raw.decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            raise NanoJevProviderError(f"CLEF {path} HTTP {exc.code}: {detail}") from exc
        except (URLError, OSError, ValueError) as exc:
            raise NanoJevProviderError(f"CLEF {path} transport/JSON failure: {exc}") from exc
        if not isinstance(data, dict) or data.get("ok") is False:
            raise NanoJevProviderError(f"CLEF {path} returned a failure or non-object response")
        return data

    def health(self) -> dict[str, Any]:
        health = self._json("/api/health")
        if (health.get("ready") is not True or
                health.get("model_family") != CLEF_MODEL or
                health.get("model_loaded_once") is not True):
            raise NanoJevProviderError("CLEF service is not ready with a loaded nanojev-clef checkpoint")
        return health

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        raise NanoJevProviderError(
            "NanoJev/CLEF is a structured solution provider, not chat.completions. "
            "Use the structured solve API with kind nanojev.clef.evaluate.v1 or "
            "nanojev.clef.pairwise-batch.v1."
        )

    @staticmethod
    def _result_model(result: dict[str, Any]) -> dict[str, Any]:
        model = result.get("model")
        if not isinstance(model, dict) or model.get("family") != CLEF_MODEL:
            raise NanoJevProviderError("CLEF result did not identify the nanojev-clef model family")
        sha = model.get("release_manifest_sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
            raise NanoJevProviderError("CLEF result lacks a valid checkpoint manifest SHA256")
        if not str(model.get("release_name") or "").strip():
            raise NanoJevProviderError("CLEF result lacks a checkpoint release name")
        return model

    @staticmethod
    def _validate_evaluate(payload: dict[str, Any], result: dict[str, Any]) -> None:
        rows = payload.get("states")
        outputs = result.get("states")
        if not isinstance(rows, list) or not rows or not isinstance(outputs, list) or len(rows) != len(outputs):
            raise NanoJevProviderError("CLEF evaluate result does not match request states")
        by_id = {r.get("id"): r for r in outputs if isinstance(r, dict) and isinstance(r.get("id"), str)}
        requested_ids = [r.get("id") for r in rows if isinstance(r, dict)]
        if (len(by_id) != len(rows) or len(requested_ids) != len(rows)
                or len(set(requested_ids)) != len(rows)
                or set(by_id) != set(requested_ids)):
            raise NanoJevProviderError("CLEF evaluate result contains duplicate or invalid state IDs")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("questions"), dict):
                raise NanoJevProviderError("CLEF evaluate request has invalid states/questions")
            output = by_id.get(row.get("id"))
            if not isinstance(output, dict) or not isinstance(output.get("answers"), dict):
                raise NanoJevProviderError("CLEF evaluate result missing a requested state")
            if set(output["answers"]) != set(row["questions"]):
                raise NanoJevProviderError("CLEF evaluate result omitted or invented question IDs")
            for qid, spec in row["questions"].items():
                answer = output["answers"][qid]
                if not isinstance(answer, dict) or answer.get("type") != spec.get("type"):
                    raise NanoJevProviderError("CLEF evaluate result has a wrong answer type")
                probs = answer.get("probabilities")
                if not isinstance(probs, dict) or not probs:
                    raise NanoJevProviderError("CLEF evaluate result missing candidate probabilities")
                vals = list(probs.values())
                if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and
                            math.isfinite(v) and 0 <= v <= 1 for v in vals) or
                        abs(math.fsum(vals) - 1) > 1e-5):
                    raise NanoJevProviderError("CLEF evaluate probabilities are not a distribution")
                if spec.get("type") == "choice" and set(probs) != set(spec.get("criteria") or {}):
                    raise NanoJevProviderError("CLEF evaluate candidate IDs do not match request")
                if spec.get("type") == "boolean" and set(probs) != {"false", "true"}:
                    raise NanoJevProviderError("CLEF boolean candidate IDs are invalid")
                if spec.get("type") == "score" and set(probs) != {str(i) for i in range(len(spec.get("criteria") or []))}:
                    raise NanoJevProviderError("CLEF score candidate IDs do not match request")
                if spec.get("type") == "choice" and (answer.get("choice") not in probs or answer.get("value") != answer.get("choice")):
                    raise NanoJevProviderError("CLEF choice does not match candidate IDs")
                if spec.get("type") == "boolean" and not isinstance(answer.get("value"), bool):
                    raise NanoJevProviderError("CLEF boolean answer has invalid value")
                if spec.get("type") == "score" and not isinstance(answer.get("level"), int):
                    raise NanoJevProviderError("CLEF score answer has invalid level")

    @staticmethod
    def _validate_pairwise(payload: dict[str, Any], result: dict[str, Any]) -> None:
        if result.get("schema") != "nanojev.pairwise-batch-result.v1":
            raise NanoJevProviderError("CLEF pairwise result has an unexpected schema")
        questions = payload.get("questions")
        results = result.get("results")
        if not isinstance(questions, list) or not questions or not isinstance(results, list) or len(questions) != len(results):
            raise NanoJevProviderError("CLEF pairwise batch result count mismatch")
        by_id = {r.get("id"): r for r in results if isinstance(r, dict) and isinstance(r.get("id"), str)}
        requested_ids = [q.get("id") for q in questions if isinstance(q, dict)]
        if (len(by_id) != len(questions) or len(requested_ids) != len(questions)
                or len(set(requested_ids)) != len(questions)
                or set(by_id) != set(requested_ids)):
            raise NanoJevProviderError("CLEF pairwise result has duplicate/invalid IDs")
        for question in questions:
            if not isinstance(question, dict):
                raise NanoJevProviderError("CLEF pairwise question must be an object")
            result_row = by_id.get(question.get("id"))
            candidate_ids = {c.get("id") for c in question.get("candidates", []) if isinstance(c, dict)}
            if not isinstance(result_row, dict) or len(candidate_ids) != 2 or result_row.get("choice") not in candidate_ids:
                raise NanoJevProviderError("CLEF pairwise result contains an invalid choice or question ID")
            ordered_ids = result_row.get("candidate_ids")
            probabilities = result_row.get("probabilities")
            if (not isinstance(ordered_ids, list) or len(ordered_ids) != 2 or set(ordered_ids) != candidate_ids
                    or not isinstance(probabilities, list) or len(probabilities) != 2
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               and math.isfinite(v) and 0 <= v <= 1 for v in probabilities)
                    or abs(math.fsum(probabilities) - 1) > 1e-5):
                raise NanoJevProviderError("CLEF pairwise probabilities/candidates are invalid")

    def evaluate(self, payload: dict[str, Any], *, capability: str) -> dict[str, Any]:
        if capability not in self.capabilities:
            raise NanoJevProviderError(f"Unsupported NanoJev/CLEF capability: {capability!r}")
        if not isinstance(payload, dict):
            raise NanoJevProviderError("CLEF request payload must be a JSON object")
        # Do not execute against an unready service or an unexpected checkpoint.
        health = self.health()
        path = "/api/evaluate" if capability == CLEF_EVALUATE else "/api/evaluate-batch"
        result = self._json(path, payload)
        model = self._result_model(result)
        if health.get("release_manifest_sha256") and model["release_manifest_sha256"] != health["release_manifest_sha256"]:
            raise NanoJevProviderError("CLEF checkpoint changed between health and result")
        if capability == CLEF_EVALUATE:
            self._validate_evaluate(payload, result)
        else:
            self._validate_pairwise(payload, result)
        return result

    def solve(self, *, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a typed local solution independent of any Hub wire contract.

        Checkpoint identity is self-reported by the CLEF service and checked for
        internal consistency; this is not independent attestation of correctness.
        """
        result = self.evaluate(payload, capability=capability)
        return {
            "schema": "main-computer.nanojev-solution.v1",
            "provider": self.name,
            "model": result["model"],
            "capability": capability,
            "result": result,
        }
