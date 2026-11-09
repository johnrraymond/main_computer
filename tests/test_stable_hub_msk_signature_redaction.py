"""No persisted wallet signatures in new or migrated Stable Hub MSK records."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.stable_hub_msk import (
    FoundationDbStableMultiSessionKeyStore,
    InMemoryStableMultiSessionKeyStore,
    JsonStableMultiSessionKeyStore,
    _without_saved_authorization,
)
from tools.stable_hub_msk_redact import main as redact_main


SIGNED = {"message": {"request_id": "nonce"}, "signature": "0x" + "a5" * 65}
OLD_RECORD = {
    "id": "msk_existing", "status": "active", "wallet_address": "0x" + "12" * 20,
    "signed_request_hash": "hash1", "signed_request": SIGNED,
    "signed_message": SIGNED["message"], "signature": SIGNED["signature"],
}


def _has_auth(record):
    return any(key in record for key in ("signed_request", "signed_message", "signature"))


def test_normalization_prevents_in_memory_restorage_of_signature():
    store = InMemoryStableMultiSessionKeyStore({"keys": {"msk_existing": OLD_RECORD}})
    assert not _has_auth(store.load()["keys"]["msk_existing"])
    store.save({"keys": {"msk_existing": OLD_RECORD}})
    assert not _has_auth(store.load()["keys"]["msk_existing"])


def test_json_save_never_writes_wallet_signature_and_preserves_identity(tmp_path):
    path = tmp_path / "msk.json"
    store = JsonStableMultiSessionKeyStore(path)
    store.save({"keys": {"msk_existing": OLD_RECORD}})
    raw = path.read_text(encoding="utf-8")
    assert SIGNED["signature"] not in raw
    assert '"signed_request"' not in raw
    assert '"signed_message"' not in raw
    saved = json.loads(raw)["keys"]["msk_existing"]
    assert saved["wallet_address"] == OLD_RECORD["wallet_address"]
    assert saved["signed_request_hash"] == "hash1"


def test_json_legacy_redaction_is_explicit_idempotent_and_preserves_status(tmp_path):
    path = tmp_path / "msk.json"
    path.write_text(json.dumps({"keys": {"msk_existing": OLD_RECORD}}), encoding="utf-8")
    store = JsonStableMultiSessionKeyStore(path)
    preview = store.redact_stored_authorizations()
    assert preview == {"backend": "local-json", "records_scanned": 1, "records_redacted": 1, "executed": False}
    assert SIGNED["signature"] in path.read_text(encoding="utf-8")
    applied = store.redact_stored_authorizations(execute=True)
    assert applied["records_redacted"] == 1
    saved = json.loads(path.read_text(encoding="utf-8"))["keys"]["msk_existing"]
    assert not _has_auth(saved)
    assert saved["wallet_address"] == OLD_RECORD["wallet_address"]
    assert saved["signed_request_hash"] == "hash1"
    assert store.redact_stored_authorizations(execute=True)["records_redacted"] == 0


class _Tuple:
    @staticmethod
    def pack(parts):
        return b"|".join(str(x).encode() for x in parts) + b"|"

    @staticmethod
    def range(parts):
        prefix = _Tuple.pack(parts)
        return SimpleNamespace(start=prefix, stop=prefix + b"\xff")


class _FakeDb:
    def __init__(self):
        self.data = {}
        self.options = None

    def __getitem__(self, key):
        return SimpleNamespace(wait=lambda: self.data.get(key))

    def __setitem__(self, key, value):
        self.data[key] = value

    def __delitem__(self, key):
        del self.data[key]

    def get_range(self, start, end, limit=None):
        keys = [k for k in sorted(self.data) if start <= k < end]
        if limit is not None:
            keys = keys[:limit]
        return [SimpleNamespace(key=k, value=self.data[k]) for k in keys]


class _FakeFDB:
    tuple = _Tuple

    @staticmethod
    def transactional(fn):
        return fn


def _fdb(monkeypatch):
    store = FoundationDbStableMultiSessionKeyStore(cluster_file="never-opens", namespace="unit")
    db = _FakeDb()
    store._db = db
    store._fdb = _FakeFDB
    monkeypatch.setattr(store, "_open", lambda: None)
    return store, db


def test_fdb_record_create_strips_signature_before_writing(monkeypatch):
    store, db = _fdb(monkeypatch)
    result = store.create_key_record_if_absent(signed_request_hash="hash1", record=OLD_RECORD)
    assert result["idempotent"] is False
    encoded = db.data[store._record_key("msk_existing")]
    assert SIGNED["signature"].encode() not in encoded
    assert not _has_auth(json.loads(encoded))
    repeat = store.create_key_record_if_absent(signed_request_hash="hash1", record=OLD_RECORD)
    assert repeat["idempotent"] is True
    assert repeat["record"]["id"] == "msk_existing"
    assert db.data[store._hash_key("hash1")] == b"msk_existing"


def test_fdb_legacy_redaction_preserves_index_and_all_key_authority(monkeypatch):
    store, db = _fdb(monkeypatch)
    db.data[store._record_key("msk_existing")] = json.dumps(OLD_RECORD).encode()
    db.data[store._hash_key("hash1")] = b"msk_existing"
    legacy = {"keys": {"msk_existing": OLD_RECORD}}
    db.data[store._legacy_key()] = json.dumps(legacy).encode()
    chunk = json.dumps(legacy).encode()
    db.data[store._legacy_chunk_meta_key()] = json.dumps({"chunk_count": 2}).encode()
    db.data[store._legacy_chunk_key(0)] = chunk[:len(chunk)//2]
    db.data[store._legacy_chunk_key(1)] = chunk[len(chunk)//2:]

    preview = store.redact_stored_authorizations()
    assert preview["executed"] is False
    assert preview["records_redacted"] == 1
    assert preview["legacy_records_redacted"] == 1
    assert preview["legacy_chunks_redacted"] == 1
    assert SIGNED["signature"].encode() in db.data[store._legacy_key()]

    applied = store.redact_stored_authorizations(execute=True)
    assert applied["records_redacted"] == 1
    assert applied["legacy_records_redacted"] == 1
    assert applied["legacy_chunks_redacted"] == 1
    for raw in (db.data[store._record_key("msk_existing")], db.data[store._legacy_key()]):
        assert SIGNED["signature"].encode() not in raw
    chunk_count = json.loads(db.data[store._legacy_chunk_meta_key()])["chunk_count"]
    chunk_bytes = b"".join(db.data[store._legacy_chunk_key(i)] for i in range(chunk_count))
    assert SIGNED["signature"].encode() not in chunk_bytes
    assert db.data[store._hash_key("hash1")] == b"msk_existing"
    assert json.loads(db.data[store._record_key("msk_existing")])["status"] == "active"
    assert store.redact_stored_authorizations(execute=True)["records_redacted"] == 0


def test_fdb_paginated_redaction_visits_all_records(monkeypatch):
    store, db = _fdb(monkeypatch)
    for i in range(41):
        db.data[store._record_key(f"msk_{i:03d}")] = json.dumps({**OLD_RECORD, "id": f"msk_{i:03d}"}).encode()
    result = store.redact_stored_authorizations(execute=True)
    assert result["records_scanned"] == 41
    assert result["records_redacted"] == 41
    assert all(not _has_auth(json.loads(raw)) for raw in db.data.values())


def test_redact_cli_never_mutates_without_both_flags(monkeypatch, capsys):
    class _Store:
        calls = []
        def redact_stored_authorizations(self, *, execute=False):
            self.calls.append(execute)
            return {"backend": "local-json", "records_redacted": 1, "executed": execute}
    stub = _Store()
    topo = SimpleNamespace(cluster_id="test-cluster")
    monkeypatch.setattr("tools.stable_hub_msk_redact.load_stable_hub_topology", lambda _: topo)
    monkeypatch.setattr("tools.stable_hub_msk_redact.stable_multisession_store_from_topology", lambda t: stub)
    assert redact_main(["--topology", "test.json"]) == 0
    assert stub.calls == [False]
    with pytest.raises(SystemExit):
        redact_main(["--topology", "test.json", "--execute"])
    assert stub.calls == [False]
    assert redact_main(["--topology", "test.json", "--execute", "--yes-i-know-this-mutates-msk-storage"]) == 0
    assert stub.calls == [False, True]
    assert "0xa5" not in capsys.readouterr().out
