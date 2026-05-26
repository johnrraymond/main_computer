from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PAID_MOCK_MANIFEST = Path("runtime/hub/paid_mock_dev_manifest.json")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class PaidMockActor:
    role: str
    address: str
    account_id: str = ""
    worker_id: str = ""
    bridge_id: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {
            "role": self.role,
            "address": self.address,
        }
        if self.account_id:
            data["account_id"] = self.account_id
        if self.worker_id:
            data["worker_id"] = self.worker_id
        if self.bridge_id:
            data["bridge_id"] = self.bridge_id
        return data


@dataclass(frozen=True)
class PaidMockManifest:
    path: Path
    schema_version: str
    hub_url: str
    requester: PaidMockActor
    worker: PaidMockActor
    bridge: PaidMockActor
    model: str
    provider: str = "mock"
    max_credits: int = 20
    response_template: str = "mock worker response for request {request_id}"
    raw: dict[str, Any] | None = None

    @property
    def requester_account_id(self) -> str:
        return self.requester.account_id

    @property
    def worker_id(self) -> str:
        return self.worker.worker_id

    @property
    def bridge_id(self) -> str:
        return self.bridge.bridge_id

    def as_config(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "schema_version": self.schema_version,
            "hub_url": self.hub_url,
            "requester": self.requester.as_dict(),
            "worker": self.worker.as_dict(),
            "bridge": self.bridge.as_dict(),
            "mock_ai": {
                "provider": self.provider,
                "model": self.model,
                "max_credits": self.max_credits,
                "response_template": self.response_template,
            },
        }


def paid_mock_manifest_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path:
        return Path(path)
    env_path = os.environ.get("MAIN_COMPUTER_PAID_MOCK_MANIFEST")
    return Path(env_path) if env_path else DEFAULT_PAID_MOCK_MANIFEST


def load_paid_mock_manifest(path: str | os.PathLike[str] | None = None) -> PaidMockManifest:
    manifest_path = paid_mock_manifest_path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Paid mock dev manifest not found: {manifest_path}. "
            "Run scripts/prepare_paid_mock_manifest.py first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Paid mock dev manifest is not valid JSON: {manifest_path}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Paid mock dev manifest root must be a JSON object.")

    actors = payload.get("actors")
    if not isinstance(actors, dict):
        raise ValueError("Paid mock dev manifest is missing actors.")

    requester_payload = actors.get("requester") if isinstance(actors.get("requester"), dict) else {}
    worker_payload = actors.get("worker") if isinstance(actors.get("worker"), dict) else {}
    bridge_payload = actors.get("bridge") if isinstance(actors.get("bridge"), dict) else {}

    mock_ai = payload.get("mock_ai") if isinstance(payload.get("mock_ai"), dict) else {}
    models = mock_ai.get("models") if isinstance(mock_ai.get("models"), list) else []
    model = next((_clean_text(item) for item in models if _clean_text(item)), "") or "mock-fast-chat"

    try:
        max_credits = int(
            payload.get("recommended_env", {}).get("MAIN_COMPUTER_PAID_MOCK_MAX_CREDITS", 20)
            if isinstance(payload.get("recommended_env"), dict)
            else 20
        )
    except (TypeError, ValueError):
        max_credits = 20

    manifest = PaidMockManifest(
        path=manifest_path,
        schema_version=_clean_text(payload.get("schema_version")),
        hub_url=_clean_text((payload.get("hub") or {}).get("url") if isinstance(payload.get("hub"), dict) else "") or "http://127.0.0.1:8770",
        requester=PaidMockActor(
            role="requester",
            address=_clean_text(requester_payload.get("address")),
            account_id=_clean_text(requester_payload.get("account_id")),
        ),
        worker=PaidMockActor(
            role="worker",
            address=_clean_text(worker_payload.get("address")),
            worker_id=_clean_text(worker_payload.get("worker_id")),
        ),
        bridge=PaidMockActor(
            role="bridge",
            address=_clean_text(bridge_payload.get("address")),
            bridge_id=_clean_text(bridge_payload.get("bridge_id")),
        ),
        provider=_clean_text(mock_ai.get("provider")) or "mock",
        model=model,
        max_credits=max(1, max_credits),
        response_template=_clean_text(mock_ai.get("response_template")) or "mock worker response for request {request_id}",
        raw=payload,
    )

    missing = []
    if not manifest.requester.account_id:
        missing.append("actors.requester.account_id")
    if not manifest.requester.address:
        missing.append("actors.requester.address")
    if not manifest.worker.worker_id:
        missing.append("actors.worker.worker_id")
    if not manifest.worker.address:
        missing.append("actors.worker.address")
    if not manifest.bridge.address:
        missing.append("actors.bridge.address")
    if missing:
        raise ValueError("Paid mock dev manifest is missing required fields: " + ", ".join(missing))

    return manifest
