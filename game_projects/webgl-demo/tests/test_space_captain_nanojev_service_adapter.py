from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
ADAPTER = ROOT / "game_projects" / "webgl-demo" / "tools" / "space_captain_clef_backend.py"
FLESHED = ROOT / "game_projects" / "webgl-demo" / "tools" / "space_captain_fleshed_combat_smoke.py"
LIVE = ROOT / "game_projects" / "webgl-demo" / "tools" / "space_captain_live_clef_smoke.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_adapter_translates_captain_request_to_generic_pairwise_batch(monkeypatch) -> None:
    module = load_module("space_captain_service_adapter_test", ADAPTER)
    calls = []

    def fake_http_json(url, *, method="GET", payload=None, timeout=300.0):
        calls.append((url, method, payload, timeout))
        assert url.endswith("/api/evaluate-batch")
        assert method == "POST"
        assert payload["schema"] == "nanojev.pairwise-batch.v1"
        assert payload["shared_context"] == {"text": "shared battle state"}
        assert payload["execution"] == {"evidence_mode": "prefix-cache"}
        assert payload["questions"] == [
            {
                "id": "q1",
                "prompt": "Which is better?",
                "candidates": [
                    {"id": "left", "text": "Take the left branch."},
                    {"id": "right", "text": "Take the right branch."},
                ],
            }
        ]
        return {
            "schema": "nanojev.pairwise-batch-result.v1",
            "model": {
                "release_name": "clef-cycle-000136-reuse-002",
                "release_manifest_sha256": "abc123",
                "revision": "deadbeef",
                "source": "huggingface",
            },
            "results": [
                {
                    "id": "q1",
                    "choice": "left",
                    "candidate_ids": ["left", "right"],
                    "probabilities": [0.7, 0.3],
                    "margin": 0.4,
                }
            ],
            "metrics": {
                "model_latency_ms": 12.5,
                "amortized_question_latency_ms": 12.5,
                "head_forward_count": 1,
                "independent_judgment_count": 1,
                "backbone_forward_batch_count": 6,
                "evidence_mode_requested": "prefix-cache",
                "evidence_mode_actual": "prefix-cache",
                "shared_context_chars": 19,
                "shared_prefix_cache_used": True,
            },
        }

    monkeypatch.setattr(module, "http_json", fake_http_json)
    adapter = module.ServiceAdapter("http://127.0.0.1:9765")
    response = adapter.evaluate(
        {
            "schema": "game.captainDecisionRequest.v6",
            "checkpoint": {
                "checkpointId": "clef-cycle-000136-reuse-002",
                "sha256": "abc123",
            },
            "semanticContext": {"text": "shared battle state"},
            "execution": {"evidenceMode": "prefix-cache"},
            "questions": [
                {
                    "id": "q1",
                    "text": "Which is better?",
                    "optionA": "left",
                    "optionB": "right",
                    "optionAText": "Take the left branch.",
                    "optionBText": "Take the right branch.",
                }
            ],
        }
    )
    assert response["schema"] == "game.captainDecisionResponse.v6"
    assert response["checkpointId"] == "clef-cycle-000136-reuse-002"
    assert response["checkpointSha256"] == "abc123"
    assert response["provider"] == "managed-nanojev-clef"
    assert response["captainModelCallCount"] == 1
    assert response["clefHeadForwardCount"] == 1
    assert response["answers"] == [
        {
            "questionId": "q1",
            "choice": "left",
            "candidateIds": ["left", "right"],
            "probabilities": [0.7, 0.3],
            "margin": 0.4,
        }
    ]
    assert len(calls) == 1


def test_adapter_health_uses_managed_service_identity(monkeypatch) -> None:
    module = load_module("space_captain_service_adapter_health_test", ADAPTER)

    monkeypatch.setattr(
        module,
        "http_json",
        lambda *_args, **_kwargs: {
            "ready": True,
            "model_family": "nanojev-clef",
            "checkpoint_selector": "champion",
            "checkpoint_repo": "johnrraymond/NanoJev-CLEF",
            "checkpoint_revision_resolved": "deadbeef",
            "checkpoint_source": "local-disk-fallback",
            "release_name": "clef-cycle-000136-reuse-002",
            "release_manifest_sha256": "abc123",
            "checkpoint_cycle": 136,
            "checkpoint_reuse_depth": 2,
        },
    )
    health = module.ServiceAdapter("http://127.0.0.1:9765").health()
    assert health["checkpointId"] == "clef-cycle-000136-reuse-002"
    assert health["checkpointSha256"] == "abc123"
    assert health["checkpointSource"] == "local-disk-fallback"
    assert health["cycle"] == 136
    assert health["reuseEpoch"] == 2


def test_game_launches_adapter_against_managed_service_not_training_run() -> None:
    fleshed = FLESHED.read_text(encoding="utf-8")
    live = LIVE.read_text(encoding="utf-8")
    for source in (fleshed, live):
        assert '"--service-url"' in source
        assert "nanojev_service_url" in source
    assert 'help="Use the managed NanoJev CLEF service to choose combat actions."' in fleshed
