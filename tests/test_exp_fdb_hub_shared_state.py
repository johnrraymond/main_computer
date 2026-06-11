from __future__ import annotations

from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def test_exp_fdb_hub_replaces_shared_hub_state_stores() -> None:
    module = (_repo() / "main_computer" / "exp_fdb_hub.py").read_text(encoding="utf-8")

    assert "ExperimentalFoundationDbRegistry" in module
    assert "ExperimentalFoundationDbRequestStateStore" in module
    assert "ExperimentalFoundationDbQuoteStateStore" in module
    assert "ExperimentalFoundationDbSecureSessionStore" in module
    assert "ExperimentalFoundationDbMultiSessionKeyStore" in module
    assert "ExperimentalFoundationDbEnergyCreditLedger" in module
    assert "request_store=self.request_store" in module
    assert "quote_store=self.quote_store" in module
    assert "secure_session_store=self.secure_session_store" in module


def test_exp_fdb_state_defines_atomic_worker_pull_claim_and_scheduler_index() -> None:
    module = (_repo() / "main_computer" / "exp_fdb_hub_state.py").read_text(encoding="utf-8")

    assert "class ExperimentalFoundationDbRequestStateStore" in module
    assert "def claim_worker_pull_lease" in module
    assert 'record.state != "queued"' in module
    assert '"worker_pull.lease.granted"' in module
    assert "idx_worker_available" in module
    assert "available_workers_by_network_ring_model_price" in module


def test_worker_pull_uses_atomic_claim_when_store_supports_it() -> None:
    module = (_repo() / "main_computer" / "hub_plex_service.py").read_text(encoding="utf-8")

    assert 'hasattr(self.request_store, "claim_worker_pull_lease")' in module
    assert "claimed_record is None" in module
    assert "success=True" in module


def test_multisession_and_secure_sessions_can_use_injected_stores() -> None:
    hub_module = (_repo() / "main_computer" / "hub.py").read_text(encoding="utf-8")
    plex_module = (_repo() / "main_computer" / "hub_plex_service.py").read_text(encoding="utf-8")

    assert "self.multisession_key_store = None" in hub_module
    assert 'getattr(self.server, "multisession_key_store", None)' in hub_module
    assert "secure_session_store: Any | None = None" in plex_module
    assert "def _store_secure_session" in plex_module
    assert "def _load_secure_session" in plex_module
