"""Real model-bound Worker adapter for one shared NanoJev inference service.

This adapter never owns or loads model weights. Any number of Worker sessions
can use the same existing NanoJev service, subject to its bounded admission.
Worker enrollment, scheduling, and price policy remain in SLH/Stable Hub.
"""
from __future__ import annotations

from typing import Any

from main_computer.model_execution_contract import (
    ModelContractError, market_supports, request_contract, verify_execution_result,
)
from main_computer.providers.nanojev import (
    NanoJevClefProvider, NanoJevModelClient, NanoJevProviderError, CLEF_BASE_URL,
    CLEF_MODEL,
)


class NanoJevSharedWorkerBackend:
    def __init__(self, *, model: str, base_url: str = CLEF_BASE_URL,
                 timeout_s: float = 600.0):
        selected_client = NanoJevClefProvider if model == CLEF_MODEL else NanoJevModelClient
        self.provider = selected_client(model=model, base_url=base_url,
                                        timeout_s=timeout_s)
        self.model = model
        # Discovery here is authoritative: never advertise a model based only
        # on a command-line string, a cached config, or a provider fallback.
        self._advertisement = self.provider.advertised_market()

    def advertisement(self) -> dict[str, Any]:
        return {"models": list(self._advertisement["models"]),
                "capabilities": list(self._advertisement["capabilities"]),
                "checkpoint_sha256": self._advertisement["checkpoint_sha256"]}

    def execute_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = offer.get("work") if isinstance(offer, dict) else None
        if not market_supports(self._advertisement, work):
            raise ModelContractError("Worker cannot execute the requested NanoJev model/operation")
        model, operation, _ = request_contract(work)
        data = work.get("input")
        if (not isinstance(data, dict) or data.get("kind") != operation
                or not isinstance(data.get("payload"), dict) or not data["payload"]):
            raise ModelContractError("NanoJev requires a native structured input payload")
        # Prevent reconfigured/hot-swapped heads from being executed under an
        # old advertisement. All Workers share the service; none loads a copy.
        current = self.provider.advertised_market()
        if current != self._advertisement:
            raise NanoJevProviderError("NanoJev backend changed since Worker advertisement")
        solution = self.provider.solve(capability=operation, payload=data["payload"])
        if (not isinstance(solution, dict) or solution.get("capability") != operation
                or not isinstance(solution.get("model"), dict)
                or solution["model"].get("family") != model
                or solution["model"].get("release_manifest_sha256")
                != self._advertisement["checkpoint_sha256"]):
            raise ModelContractError("NanoJev reported a different executed model/checkpoint")
        result = {
            "status": "success", "solution": solution,
            "response": {"role": "assistant", "content": "", "provider": "nanojev",
                         "model": model,
                         "checkpoint_sha256": self._advertisement["checkpoint_sha256"]},
            "transport": "nanojev-shared-service",
        }
        verify_execution_result(work, result)
        return result

    __call__ = execute_offer
