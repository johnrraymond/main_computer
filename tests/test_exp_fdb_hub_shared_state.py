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
    assert 'worker_instance_id: str = ""' in module
    assert "clean_worker_instance_id" in module
    assert "preferred_worker_instance_id" in module
    assert '"worker_instance_id": clean_worker_instance_id' in module


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


def test_bridge_payout_requires_quiet_wallet_before_ledger_mutation() -> None:
    hub_module = (_repo() / "main_computer" / "hub.py").read_text(encoding="utf-8")
    smoke_module = (_repo() / "main_computer" / "temporal_fdb_hub_node_market_smoke.py").read_text(encoding="utf-8")

    assert '"error_type": "wallet_active_worker_leases"' in hub_module
    assert "wallet has active worker leases; payout requires a quiet wallet" in hub_module
    assert "if wallet_address:" in hub_module
    assert "if worker_node_id:" not in hub_module[hub_module.find('if path == "/api/hub/v1/bridge/payouts"'):hub_module.find('if path == "/api/hub/v1/bridge/payouts/confirm"')]
    assert "hub_bridge_payout_rejected_active_work" in smoke_module
    assert "surprise_payout_rejected_active_work" in smoke_module


def test_bridge_payout_request_does_not_create_long_lived_wallet_lock() -> None:
    ledger_module = (_repo() / "main_computer" / "exp_fdb_credit_ledger.py").read_text(encoding="utf-8")
    plex_module = (_repo() / "main_computer" / "hub_plex_service.py").read_text(encoding="utf-8")

    assert 'event_type="bridge.payout.requested"' in ledger_module
    assert 'event_type="bridge.payout.confirmed"' in ledger_module
    assert 'event_type="bridge.payout.failed"' in ledger_module
    assert 'event_type="bridge.wallet.locked"' not in ledger_module
    assert 'event_type="bridge.wallet.unlocked"' not in ledger_module
    assert 'self._write_dict(tr, "wallet_lock"' not in ledger_module
    assert "is_wallet_locked" not in plex_module


def test_bridge_audit_records_work_and_payout_failure_recovery_events() -> None:
    ledger_module = (_repo() / "main_computer" / "exp_fdb_credit_ledger.py").read_text(encoding="utf-8")
    smoke_module = (_repo() / "main_computer" / "temporal_fdb_hub_node_market_smoke.py").read_text(encoding="utf-8")

    assert 'event_type="hub.hold.created"' in ledger_module
    assert 'event_type="hub.hold.charged"' in ledger_module
    assert 'event_type="hub.worker.earning.recorded"' in ledger_module
    assert 'event_type="bridge.payout.failed"' in ledger_module
    assert "hub_bridge_payout_failed_recovered" in smoke_module
    assert "bridge_audit_readback_ok" in smoke_module
    assert "payout_failure_recovered" in smoke_module



def test_fdb_registry_ring_admission_audit_writes_to_shared_namespace(tmp_path) -> None:
    from main_computer.exp_fdb_hub_state import ExperimentalFoundationDbRegistry

    class _RangeTransaction:
        def __init__(self) -> None:
            self.store: dict[bytes, bytes] = {}

        def __setitem__(self, key: bytes, value: bytes) -> None:
            self.store[bytes(key)] = bytes(value)

        def get_range(self, start: bytes, stop: bytes, **kwargs):
            keys = sorted(key for key in self.store if start <= key < stop)
            if kwargs.get("reverse"):
                keys = list(reversed(keys))
            limit = kwargs.get("limit")
            if limit is not None:
                keys = keys[: int(limit)]

            class _Item:
                def __init__(self, key: bytes, value: bytes) -> None:
                    self.key = key
                    self.value = value

            return [_Item(key, self.store[key]) for key in keys]

        def clear_range(self, start: bytes, stop: bytes) -> None:
            for key in [key for key in self.store if start <= key < stop]:
                del self.store[key]

    class _FakeFdb:
        def transactional(self, func):
            return func

    class _FakeState:
        def __init__(self) -> None:
            self.fdb = _FakeFdb()
            self.db = _RangeTransaction()

        def pack(self, *parts) -> bytes:
            return b"\x00".join(str(part).encode("utf-8") for part in parts)

        def range_for(self, *parts) -> slice:
            prefix = self.pack(*parts)
            return slice(prefix + b"\x00", prefix + b"\xff")

    registry = ExperimentalFoundationDbRegistry(_FakeState(), root=tmp_path)
    event = registry.record_ring_admission_rejection(
        {
            "node_id": "requester-0001",
            "requested_ring": 2,
            "minimum_allowed_ring": 3,
            "fallback_ring": 3,
            "error": "ring_not_allowed",
        }
    )

    assert event["event_type"] == "ring_admission_rejected"
    assert event["event_id"].startswith("ring-admission-")
    assert registry.ring_admission_audit_count() == 1
    audit_events = registry.list_ring_admission_audit()
    assert audit_events
    assert audit_events[-1]["event_id"] == event["event_id"]
    assert audit_events[-1]["node_id"] == "requester-0001"
