from __future__ import annotations

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_generated_operation_contract_contains_accept_and_refusal_paths() -> None:
    intents = (PACKAGE_ROOT / "contracts" / "intents.js").read_text(encoding="utf-8")
    adapter = (PACKAGE_ROOT / "contracts" / "adapter.js").read_text(encoding="utf-8")

    assert 'id: "increment"' in intents
    assert 'id: "reset"' in intents
    assert 'id: "direct-set"' in intents
    assert 'kind: "prohibited"' in intents
    assert 'code: "REVISION_STALE"' in adapter
    assert 'code: "INTENT_PROHIBITED"' in adapter
    assert "state.count + 1" in adapter
    assert "state.revision + 1" in adapter


def test_generated_adapter_targets_the_live_scm_application_runtime() -> None:
    domain = (PACKAGE_ROOT / "contracts" / "domain.js").read_text(encoding="utf-8")
    adapter = (PACKAGE_ROOT / "contracts" / "adapter.js").read_text(encoding="utf-8")

    assert 'invariantReads: Object.freeze(["state.count", "state.revision"])' in domain
    assert 'currentRuntimeStatus: "scm-controlled"' in adapter
    assert "validateEffects" in adapter
