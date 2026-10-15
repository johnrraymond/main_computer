from __future__ import annotations

import hashlib
from functools import wraps
import json
import os
import platform
import secrets
import sys
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from main_computer.foundationdb_native_client_activation import activate_native_client_directory

from main_computer.credit_units import (
    credit_decimal_text_to_wei,
    credit_wei_to_decimal_text,
    credit_wei_to_whole_credits_floor,
)
from main_computer.hub_credit_models import make_worker_commitment, normalize_address, stable_id
from main_computer.hub_credit_indexer import wallet_account_id


STABLE_WORKER_SESSION_STORE_VERSION = "main-computer-stable-hub-worker-live-sessions-v1"
STABLE_WORKER_SESSION_FDB_TRANSACTION_TIMEOUT_MS = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_TIMEOUT_MS", "20000"))
STABLE_WORKER_SESSION_FDB_RETRY_LIMIT = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_RETRY_LIMIT", "30"))
STABLE_WORKER_SESSION_FDB_VALUE_CHUNK_BYTES = int(
    os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_VALUE_CHUNK_BYTES", "60000")
)
STABLE_HUB_DEV_ACCOUNT_CREDITS = Decimal(os.environ.get("MAIN_COMPUTER_STABLE_HUB_DEV_ACCOUNT_CREDITS", "1000"))


class StableHubWorkerSessionError(ValueError):
    """Raised when a stable Hub worker live-session request is malformed."""


_SHARED_STORE_LOCKS_GUARD = threading.Lock()
_SHARED_STORE_LOCKS: dict[tuple[Any, ...], threading.Lock] = {}


def _shared_store_lock_key(store: Any) -> tuple[Any, ...]:
    cluster_file = getattr(store, "cluster_file", None)
    namespace = str(getattr(store, "namespace", "") or "")
    if cluster_file is not None and namespace:
        return ("foundationdb", str(Path(cluster_file).expanduser()), namespace)
    path = getattr(store, "path", None)
    if path is not None:
        return ("local-json", str(Path(path).expanduser()))
    return ("object", id(store))


def _shared_store_lock(store: Any) -> threading.Lock:
    """Return the process-local mutation lock for one durable Stable Hub store.

    Stable's directories all operate on one underlying backend document.  Each
    directory used to have its own lock, so two directory instances could
    interleave load/mutate/save and overwrite one another.  The shared lock is
    keyed by backend identity, not directory identity, so Stable-native session,
    market, payout, and settlement mutations in one Hub process serialize at the
    same boundary as the persisted document.  FoundationDB still remains the
    durable backend; this closes the in-process stale-save hole without forcing
    Stable's richer session/handoff model into the exp Hub code shape.
    """

    key = _shared_store_lock_key(store)
    with _SHARED_STORE_LOCKS_GUARD:
        lock = _SHARED_STORE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _SHARED_STORE_LOCKS[key] = lock
        return lock


def _atomic_stable_store_mutation(method: Any) -> Any:
    """Replay a whole Stable directory mutation inside one FDB transaction.

    The callback, not only its final save, must be retried on a read/write
    conflict. Local JSON/in-memory stores retain their existing shared lock.
    """

    @wraps(method)
    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        atomic = getattr(self.store, "atomic", None)
        if callable(atomic):
            return atomic(lambda: method(self, *args, **kwargs))
        return method(self, *args, **kwargs)

    return wrapped


class StableHubWorkerSessionStore(Protocol):
    def load(self) -> dict[str, Any]:
        ...

    def save(self, data: dict[str, Any]) -> None:
        ...


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_worker_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise StableHubWorkerSessionError("worker_id is required.")
    if len(text) > 128:
        raise StableHubWorkerSessionError("worker_id must be at most 128 characters.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in text):
        raise StableHubWorkerSessionError(
            "worker_id may only contain letters, digits, dot, underscore, colon, and dash."
        )
    return text


def new_connection_id() -> str:
    return "conn_" + secrets.token_urlsafe(24).rstrip("=")


def new_session_id() -> str:
    return "sess_" + secrets.token_urlsafe(24).rstrip("=")


def new_run_id() -> str:
    return "run_" + secrets.token_urlsafe(24).rstrip("=")


def _stable_digest_id(prefix: str, *parts: Any) -> str:
    material = "\0".join(str(part or "") for part in parts)
    return f"{prefix}_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def normalize_request_id(value: Any) -> str:
    return _normalize_market_token(value, field_name="request_id", max_length=128)


def normalize_session_id(value: Any) -> str:
    return _normalize_market_token(value, field_name="session_id", max_length=128)


def stable_task_queue_for_partition(partition: Any) -> str:
    partition_key = _normalize_market_token(partition, field_name="partition", max_length=128)
    return f"main-computer-work-{partition_key}"


def stable_temporal_namespace_for_topology(topology: Any) -> str:
    """Return the Temporal namespace for accepted Stable Hub work sessions.

    Temporal begins after a worker accepts an offered work session. The stable Hub
    records the deterministic Temporal path with the accepted session so later
    lifecycle/streaming code can continue from durable state instead of entry-Hub
    memory. The dev topology's network_key maps to the first namespace shape.
    """

    network = getattr(topology, "network", {}) or {}
    network_key = _normalize_market_token(
        network.get("network_key") or "dev",
        field_name="network.network_key",
        max_length=64,
    ).lower()
    return f"main-computer-{network_key}"


def stable_temporal_execution_metadata(
    topology: Any,
    *,
    session_id: str,
    task_queue: str,
) -> dict[str, str]:
    """Build the durable Temporal identity/path for an accepted work session."""

    session_id = normalize_session_id(session_id)
    task_queue = _normalize_market_token(task_queue, field_name="task_queue", max_length=256)
    return {
        "backend": "temporal",
        "namespace": stable_temporal_namespace_for_topology(topology),
        "workflow_type": "WorkSessionWorkflow",
        "workflow_id": session_id,
        "task_queue": task_queue,
        "status": "accepted",
    }


def _normalize_market_token(value: Any, *, field_name: str, max_length: int = 128) -> str:
    text = str(value or "").strip()
    if not text:
        raise StableHubWorkerSessionError(f"{field_name} is required.")
    if len(text) > max_length:
        raise StableHubWorkerSessionError(f"{field_name} must be at most {max_length} characters.")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in text):
        raise StableHubWorkerSessionError(
            f"{field_name} may only contain letters, digits, dot, underscore, colon, and dash."
        )
    return text


def _normalize_market_tokens(value: Any, *, field_name: str) -> list[str]:
    if value is None or value == "":
        return []
    raw_values = value if isinstance(value, list) else [value]
    tokens: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        token = _normalize_market_token(item, field_name=field_name)
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _decimal_from_value(value: Any, *, field_name: str) -> Decimal:
    text = str(value).strip()
    if not text:
        raise StableHubWorkerSessionError(f"{field_name} is required.")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise StableHubWorkerSessionError(f"{field_name} must be a decimal number.") from exc
    if number < Decimal("0"):
        raise StableHubWorkerSessionError(f"{field_name} must be zero or greater.")
    return number


def _decimal_to_string(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def normalize_price(value: Any, *, field_name: str = "price", default_amount: str = "0") -> dict[str, str]:
    if value is None or value == "":
        amount = _decimal_from_value(default_amount, field_name=f"{field_name}.amount")
        unit = "credit"
    elif isinstance(value, dict):
        amount = _decimal_from_value(value.get("amount", default_amount), field_name=f"{field_name}.amount")
        unit = str(value.get("unit") or "credit").strip().lower()
    else:
        amount = _decimal_from_value(value, field_name=f"{field_name}.amount")
        unit = "credit"
    if not unit:
        raise StableHubWorkerSessionError(f"{field_name}.unit is required.")
    unit = _normalize_market_token(unit, field_name=f"{field_name}.unit", max_length=32).lower()
    return {"amount": _decimal_to_string(amount), "unit": unit}


def normalize_worker_market_profile(value: Any | None) -> dict[str, Any]:
    profile = dict(value) if isinstance(value, dict) else {}
    rings = _normalize_market_tokens(
        profile.get("rings", profile.get("ring", profile.get("partitions", profile.get("partition", "ring-1")))),
        field_name="worker market ring",
    )
    if not rings:
        rings = ["ring-1"]
    capabilities = _normalize_market_tokens(profile.get("capabilities", []), field_name="worker capability")
    models = _normalize_market_tokens(profile.get("models", profile.get("model", [])), field_name="worker model")
    raw_max_concurrency = profile.get("max_concurrency", 1)
    max_concurrency = int(raw_max_concurrency if raw_max_concurrency is not None else 1)
    if max_concurrency < 1:
        raise StableHubWorkerSessionError("worker market max_concurrency must be at least 1.")
    raw_active_sessions = profile.get("active_sessions", 0)
    active_sessions = int(raw_active_sessions if raw_active_sessions is not None else 0)
    if active_sessions < 0:
        raise StableHubWorkerSessionError("worker market active_sessions must be zero or greater.")
    if active_sessions > max_concurrency:
        raise StableHubWorkerSessionError("worker market active_sessions cannot exceed max_concurrency.")
    price = normalize_price(profile.get("price"), field_name="worker market price")
    return {
        "rings": rings,
        "partitions": list(rings),
        "capabilities": capabilities,
        "models": models,
        "model": models[0] if models else "",
        "price": price,
        "max_concurrency": max_concurrency,
        "active_sessions": active_sessions,
    }


def normalize_request_market_constraints(work: Any) -> dict[str, Any]:
    request = dict(work) if isinstance(work, dict) else {}
    ring = _normalize_market_token(
        request.get("ring", request.get("partition", "ring-1")),
        field_name="request work ring",
    )
    capabilities = _normalize_market_tokens(
        request.get("capabilities", request.get("required_capabilities", [])),
        field_name="request capability",
    )
    max_price_raw = request.get("max_price")
    max_price = normalize_price(max_price_raw, field_name="request max_price") if max_price_raw is not None else None
    return {
        "ring": ring,
        "partition": ring,
        "capabilities": capabilities,
        "max_price": max_price,
    }


def stable_partition_key_for_work(work: Any) -> str:
    return str(normalize_request_market_constraints(work)["partition"])


def _price_decimal(price: dict[str, Any]) -> Decimal:
    return _decimal_from_value(price.get("amount", "0"), field_name="price.amount")


def _normalize_store(data: dict[str, Any] | None) -> dict[str, Any]:
    clean = dict(data) if isinstance(data, dict) else {}
    workers = clean.get("workers")
    if not isinstance(workers, dict):
        workers = {}
    market_workers = clean.get("market_workers")
    if not isinstance(market_workers, dict):
        market_workers = {}
    accepted_sessions = clean.get("accepted_sessions")
    if not isinstance(accepted_sessions, dict):
        accepted_sessions = {}
    work_request_index = clean.get("work_request_index")
    if not isinstance(work_request_index, dict):
        work_request_index = {}
    payout_accounts = clean.get("payout_accounts")
    if not isinstance(payout_accounts, dict):
        payout_accounts = {}
    payout_holds = clean.get("payout_holds")
    if not isinstance(payout_holds, dict):
        payout_holds = {}
    payout_charges = clean.get("payout_charges")
    if not isinstance(payout_charges, dict):
        payout_charges = {}
    worker_earnings = clean.get("worker_earnings")
    if not isinstance(worker_earnings, dict):
        worker_earnings = {}
    worker_claims = clean.get("worker_claims")
    if not isinstance(worker_claims, dict):
        worker_claims = {}
    payout_settlements = clean.get("payout_settlements")
    if not isinstance(payout_settlements, dict):
        payout_settlements = {}
    bridge_payouts = clean.get("bridge_payouts")
    if not isinstance(bridge_payouts, dict):
        bridge_payouts = {}
    payout_deposit_intents = clean.get("payout_deposit_intents")
    if not isinstance(payout_deposit_intents, dict):
        payout_deposit_intents = {}
    payout_events = clean.get("payout_events")
    if not isinstance(payout_events, list):
        payout_events = []
    clean["workers"] = {str(key): dict(value) for key, value in workers.items() if isinstance(value, dict)}
    clean["market_workers"] = {
        str(key): dict(value) for key, value in market_workers.items() if isinstance(value, dict)
    }
    clean["accepted_sessions"] = {
        str(key): dict(value) for key, value in accepted_sessions.items() if isinstance(value, dict)
    }
    clean["work_request_index"] = {
        str(key): str(value) for key, value in work_request_index.items() if str(key) and str(value)
    }
    clean["payout_accounts"] = {
        str(key): dict(value) for key, value in payout_accounts.items() if isinstance(value, dict)
    }
    clean["payout_holds"] = {
        str(key): dict(value) for key, value in payout_holds.items() if isinstance(value, dict)
    }
    clean["payout_charges"] = {
        str(key): dict(value) for key, value in payout_charges.items() if isinstance(value, dict)
    }
    clean["worker_earnings"] = {
        str(key): dict(value) for key, value in worker_earnings.items() if isinstance(value, dict)
    }
    clean["worker_claims"] = {
        str(key): dict(value) for key, value in worker_claims.items() if isinstance(value, dict)
    }
    clean["payout_settlements"] = {
        str(key): dict(value) for key, value in payout_settlements.items() if isinstance(value, dict)
    }
    clean["bridge_payouts"] = {
        str(key): dict(value) for key, value in bridge_payouts.items() if isinstance(value, dict)
    }
    clean["payout_deposit_intents"] = {
        str(key): dict(value) for key, value in payout_deposit_intents.items() if isinstance(value, dict)
    }
    clean["payout_events"] = [dict(value) for value in payout_events if isinstance(value, dict)]
    clean.setdefault("version", STABLE_WORKER_SESSION_STORE_VERSION)
    return clean


class InMemoryStableWorkerSessionStore:
    """Small shared test/dev store for stable Hub worker owner records."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data = _normalize_store(initial)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._data = _normalize_store(data)


class JsonStableWorkerSessionStore:
    """File-backed store used only when a topology explicitly asks for local-json."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            return _normalize_store(raw if isinstance(raw, dict) else {})

    def save(self, data: dict[str, Any]) -> None:
        clean = _normalize_store(data)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(clean, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


class _NativeClientTarget:
    def __init__(self, runtime_id: str, library_name: str) -> None:
        self.runtime_id = runtime_id
        self.library_name = library_name


def _native_client_target() -> _NativeClientTarget | None:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        if sys.maxsize <= 2**32:
            return None
        if machine in {"amd64", "x86_64"} or machine.endswith("64"):
            return _NativeClientTarget("win-x64", "fdb_c.dll")
        return None
    if sys.platform.startswith("linux"):
        if machine in {"amd64", "x86_64"}:
            return _NativeClientTarget("linux-x64", "libfdb_c.so")
        if machine in {"aarch64", "arm64"}:
            return _NativeClientTarget("linux-arm64", "libfdb_c.so")
        return None
    if sys.platform == "darwin":
        if machine in {"aarch64", "arm64"}:
            return _NativeClientTarget("osx-arm64", "libfdb_c.dylib")
        return None
    return None


def _activate_cached_foundationdb_native_client(repo_root: Path) -> Path | None:
    target = _native_client_target()
    if target is None:
        return None

    base = repo_root.resolve() / ".foundationdb" / "native-client"
    if not base.exists():
        return None

    candidates = sorted(base.glob(f"*/{target.runtime_id}/{target.library_name}"), reverse=True)
    if not candidates:
        return None

    library = candidates[0]
    native_dir = str(library.parent)
    activate_native_client_directory(native_dir)
    return library


def _fdb_value_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw
    try:
        return bytes(raw)
    except TypeError:
        return None


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _json_loads(raw: Any) -> dict[str, Any] | None:
    data = _fdb_value_bytes(raw)
    if data is None:
        return None
    payload = json.loads(data.decode("utf-8"))
    return payload if isinstance(payload, dict) else None


def _configure_fdb_transaction_safety(tr: Any) -> None:
    options = getattr(tr, "options", None)
    if options is None:
        return
    timeout = max(1, int(STABLE_WORKER_SESSION_FDB_TRANSACTION_TIMEOUT_MS))
    retry_limit = max(0, int(STABLE_WORKER_SESSION_FDB_RETRY_LIMIT))
    set_timeout = getattr(options, "set_timeout", None)
    if callable(set_timeout):
        set_timeout(timeout)
    set_retry_limit = getattr(options, "set_retry_limit", None)
    if callable(set_retry_limit):
        set_retry_limit(retry_limit)


class FoundationDbStableWorkerSessionStore:
    """Shared stable Hub worker owner directory backed by FoundationDB."""

    def __init__(
        self,
        *,
        cluster_file: str | Path,
        namespace: str,
        repo_root: str | Path = ".",
        api_version: int = 740,
    ) -> None:
        self.cluster_file = Path(cluster_file)
        self.namespace = str(namespace or "").strip()
        if not self.namespace:
            raise StableHubWorkerSessionError("storage.namespace is required for FoundationDB worker session storage.")
        self.repo_root = Path(repo_root)
        self.api_version = int(api_version)
        self.native_client_library: Path | None = None
        self._lock = threading.Lock()
        self._opened = False
        self._fdb: Any = None
        self._db: Any = None
        self._transaction_local = threading.local()

    def _open(self) -> None:
        if self._opened:
            return

        with self._lock:
            if self._opened:
                return

            self.native_client_library = _activate_cached_foundationdb_native_client(self.repo_root)
            try:
                import fdb  # type: ignore
            except Exception as exc:  # pragma: no cover - depends on local FDB install
                raise RuntimeError(
                    "Stable Hub worker session storage requires the foundationdb Python package for this topology."
                ) from exc

            try:
                fdb.api_version(self.api_version)
            except Exception as exc:  # pragma: no cover - depends on local FDB process state
                message = str(exc).lower()
                if "api version" not in message or "already" not in message:
                    raise RuntimeError(f"Could not activate FoundationDB API version {self.api_version}.") from exc

            import fdb.tuple  # type: ignore  # noqa: F401

            try:
                self._db = fdb.open(cluster_file=str(self.cluster_file))
            except Exception as exc:  # pragma: no cover - depends on local FDB install
                raise RuntimeError(f"Could not open FoundationDB cluster file {self.cluster_file}.") from exc

            self._fdb = fdb
            self._opened = True

    def _legacy_key(self) -> bytes:
        self._open()
        return self._fdb.tuple.pack((self.namespace, "stable-hub", "worker_live_sessions"))

    def _meta_key(self) -> bytes:
        self._open()
        return self._fdb.tuple.pack((self.namespace, "stable-hub", "worker_live_sessions", "chunks", "meta"))

    def _chunk_key(self, index: int) -> bytes:
        self._open()
        return self._fdb.tuple.pack((self.namespace, "stable-hub", "worker_live_sessions", "chunks", int(index)))

    def _key(self) -> bytes:
        # Backward-compatible name for older tests/helpers that referenced the
        # original single-value store key.
        return self._legacy_key()

    def atomic(self, action: Any) -> Any:
        """Run and retry one complete read/modify/write in FoundationDB.

        All Stable directories share the same chunked document. Its metadata
        key is read normally (not as a snapshot) and written on every save,
        allowing FDB conflict detection across different Hub processes.
        """
        self._open()
        if getattr(self._transaction_local, "transaction", None) is not None:
            return action()  # Nested operation joins the outer transaction.

        @self._fdb.transactional
        def _tx(tr: Any) -> Any:
            _configure_fdb_transaction_safety(tr)
            self._transaction_local.transaction = tr
            try:
                return action()
            finally:
                self._transaction_local.transaction = None

        return _tx(self._db)

    def _read_document(self, tr: Any, *, legacy_key: bytes, meta_key: bytes) -> dict[str, Any]:
        meta = _json_loads(tr[meta_key].wait()) or {}
        chunk_count = int(meta.get("chunk_count") or 0) if isinstance(meta, dict) else 0
        if chunk_count > 0:
            parts: list[bytes] = []
            for index in range(chunk_count):
                chunk = _fdb_value_bytes(tr[self._chunk_key(index)].wait())
                if chunk is None:
                    raise RuntimeError(f"Stable Hub FDB chunk {index} is missing.")
                parts.append(chunk)
            payload = json.loads(b"".join(parts).decode("utf-8"))
            return _normalize_store(payload if isinstance(payload, dict) else {})
        # Existing one-key snapshots remain readable and migrate on first save.
        return _normalize_store(_json_loads(tr[legacy_key].wait()) or {})

    def _write_document(self, tr: Any, data: dict[str, Any], *, legacy_key: bytes, meta_key: bytes) -> None:
        encoded = _json_dumps(_normalize_store(data))
        chunk_size = max(4096, int(STABLE_WORKER_SESSION_FDB_VALUE_CHUNK_BYTES))
        chunks = [encoded[index : index + chunk_size] for index in range(0, len(encoded), chunk_size)] or [b"{}"]
        previous_meta = _json_loads(tr[meta_key].wait()) or {}
        previous_count = int(previous_meta.get("chunk_count") or 0) if isinstance(previous_meta, dict) else 0
        for index, chunk in enumerate(chunks):
            tr[self._chunk_key(index)] = chunk
        for index in range(len(chunks), previous_count):
            del tr[self._chunk_key(index)]
        tr[meta_key] = _json_dumps(
            {
                "format": "chunked-json-v1",
                "chunk_count": len(chunks),
                "byte_count": len(encoded),
                "chunk_size": chunk_size,
                "updated_at": utc_now(),
            }
        )
        del tr[legacy_key]

    def load(self) -> dict[str, Any]:
        self._open()
        legacy_key = self._legacy_key()
        meta_key = self._meta_key()
        active = getattr(self._transaction_local, "transaction", None)
        if active is not None:
            return self._read_document(active, legacy_key=legacy_key, meta_key=meta_key)

        @self._fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            _configure_fdb_transaction_safety(tr)
            return self._read_document(tr, legacy_key=legacy_key, meta_key=meta_key)

        try:
            return _tx(self._db)
        except Exception as exc:  # pragma: no cover - depends on live FDB timing/state
            raise RuntimeError(
                "FoundationDB worker live-session store load failed or timed out. "
                f"cluster_file={self.cluster_file} namespace={self.namespace} "
                f"timeout_ms={STABLE_WORKER_SESSION_FDB_TRANSACTION_TIMEOUT_MS}"
            ) from exc

    def save(self, data: dict[str, Any]) -> None:
        self._open()
        legacy_key = self._legacy_key()
        meta_key = self._meta_key()
        active = getattr(self._transaction_local, "transaction", None)
        if active is not None:
            self._write_document(active, data, legacy_key=legacy_key, meta_key=meta_key)
            return
        # Deliberately disallow blind, out-of-transaction writes to live FDB:
        # they can overwrite another Hub's account/session changes.
        raise RuntimeError(
            "FoundationDB Stable Hub save requires store.atomic(...); "
            "a detached load/save would risk cross-Hub lost updates."
        )



def stable_worker_session_store_from_topology(
    topology: Any,
    *,
    repo_root: str | Path = ".",
) -> StableHubWorkerSessionStore:
    storage = dict(getattr(topology, "storage", {}) or {})
    backend = str(storage.get("backend") or "").strip().lower()
    if backend == "foundationdb":
        return FoundationDbStableWorkerSessionStore(
            cluster_file=storage.get("cluster_file") or ".foundationdb/docker.cluster",
            namespace=storage.get("namespace") or "",
            repo_root=repo_root,
            api_version=int(storage.get("api_version") or 740),
        )
    if backend == "local-json":
        store_path = storage.get("worker_session_store_path") or storage.get("path")
        if not store_path:
            namespace = str(storage.get("namespace") or "stable-hub-dev").strip() or "stable-hub-dev"
            store_path = Path("runtime") / "stable-hub" / namespace / "worker_live_sessions.json"
        return JsonStableWorkerSessionStore(store_path)
    raise StableHubWorkerSessionError(f"Unsupported stable Hub worker session storage backend: {backend!r}")




class StableHubPayoutLedgerDirectory:
    """Stable Hub golden-path credit ledger adapter.

    Stable Hub owns requester routing and live worker sockets, but its credit
    lifecycle should look like the already-proven exp Hub ledger: integer
    credit-wei accounting, hold/charge/release transactions, worker earnings,
    worker claims, settlement batches, bridge payouts, wallet/audit records, and
    idempotent golden-path transitions.  The backing Stable Hub store remains
    the shared Stable Hub store so the lab can run against the same topology,
    but record shapes intentionally carry exp-compatible fields.
    """

    def __init__(self, *, topology: Any, hub_id: str, store: StableHubWorkerSessionStore) -> None:
        self.topology = topology
        self.hub = topology.hub_by_id(hub_id)
        self.store = store
        self._lock = _shared_store_lock(store)

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    @property
    def ledger_namespace(self) -> str:
        return str(getattr(self.topology, "fdb_namespace", "") or self.cluster_id or "stable-hub")

    def _event(self, data: dict[str, Any], event_type: str, **fields: Any) -> dict[str, Any]:
        event = {
            "type": event_type,
            "event_type": event_type,
            "event_id": _stable_digest_id("evt", event_type, fields.get("session_id"), fields.get("hold_id"), fields.get("reference_id"), fields.get("at") or utc_now()),
            "hub_id": self.hub.hub_id,
            "cluster_id": self.cluster_id,
            "created_at": utc_now(),
            "metadata": dict(fields.pop("metadata", {}) or {}),
        }
        event.update({key: value for key, value in fields.items() if value is not None})
        data.setdefault("payout_events", []).append(event)
        return event

    def _bridge_audit_event(
        self,
        data: dict[str, Any],
        *,
        event_type: str,
        wallet_address: str = "",
        account_id: str = "",
        worker_id: str = "",
        amount_wei: int | str = 0,
        reference_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_wallet = normalize_address(wallet_address)
        clean_account = str(account_id or "").strip()
        clean_worker = normalize_worker_id(worker_id) if str(worker_id or "").strip() else ""
        wei = max(0, int(amount_wei or 0))
        event = {
            "event_id": stable_id(
                "baudit",
                {
                    "event_type": event_type,
                    "wallet_address": clean_wallet,
                    "account_id": clean_account,
                    "worker_node_id": clean_worker,
                    "amount_wei": str(wei),
                    "reference_id": str(reference_id or ""),
                    "created_at": utc_now(),
                },
            ),
            "event_type": str(event_type or "").strip(),
            "wallet_address": clean_wallet,
            "account_id": clean_account,
            "worker_node_id": clean_worker,
            "worker_id": clean_worker,
            "amount_wei": str(wei),
            "amount_display": credit_wei_to_decimal_text(wei),
            "reference_id": str(reference_id or ""),
            "created_at": utc_now(),
            "metadata": dict(metadata or {}),
        }
        data.setdefault("bridge_audit", {})[event["event_id"]] = event
        return event

    def _transaction(
        self,
        data: dict[str, Any],
        *,
        transaction_type: str,
        account_id: str,
        credit_wei: int | str,
        request_id: str = "",
        worker_id: str = "",
        batch_id: str = "",
        deposit_id: str = "",
        hold_id: str = "",
        memo: str = "",
        metadata: dict[str, Any] | None = None,
        transaction_id: str = "",
    ) -> dict[str, Any]:
        wei = max(0, int(credit_wei or 0))
        tx = {
            "transaction_id": transaction_id
            or stable_id(
                "ctx",
                {
                    "type": transaction_type,
                    "account_id": str(account_id or ""),
                    "request_id": str(request_id or ""),
                    "worker_node_id": str(worker_id or ""),
                    "batch_id": str(batch_id or ""),
                    "deposit_id": str(deposit_id or ""),
                    "hold_id": str(hold_id or ""),
                    "credit_wei": str(wei),
                    "created_at": utc_now(),
                },
            ),
            "account_id": str(account_id or ""),
            "transaction_type": str(transaction_type or ""),
            "credits": credit_wei_to_whole_credits_floor(wei),
            "credit_wei": str(wei),
            "credits_display": credit_wei_to_decimal_text(wei),
            "created_at": utc_now(),
            "request_id": str(request_id or ""),
            "worker_node_id": str(worker_id or ""),
            "worker_id": str(worker_id or ""),
            "batch_id": str(batch_id or ""),
            "deposit_id": str(deposit_id or ""),
            "hold_id": str(hold_id or ""),
            "memo": str(memo or ""),
            "metadata": dict(metadata or {}),
        }
        data.setdefault("payout_transactions", {})[tx["transaction_id"]] = tx
        return tx

    def _amount(self, value: Any, *, field_name: str) -> Decimal:
        return _decimal_from_value(value, field_name=field_name)

    def _amount_to_credit_wei(self, amount: Any, *, field_name: str) -> int:
        decimal_amount = self._amount(amount, field_name=field_name)
        wei = credit_decimal_text_to_wei(_decimal_to_string(decimal_amount), minimum_wei=1)
        if wei <= 0:
            raise StableHubWorkerSessionError(f"{field_name} must be positive.")
        return wei

    def _legacy_amount_fields(self, credit_wei: int | str) -> dict[str, Any]:
        wei = max(0, int(credit_wei or 0))
        amount = credit_wei_to_decimal_text(wei)
        return {
            "amount": amount,
            "credits": credit_wei_to_whole_credits_floor(wei),
            "credit_wei": str(wei),
            "credits_display": amount,
        }

    def _account_payload(
        self,
        *,
        account_id: str,
        wallet_address: str = "",
        available_credit_wei: int | str = 0,
        held_credit_wei: int | str = 0,
        spent_credit_wei: int | str = 0,
        earned_credit_wei: int | str = 0,
        bridge_completed_credit_wei: int | str = 0,
        created_at: str = "",
        updated_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        available = max(0, int(available_credit_wei or 0))
        held = max(0, int(held_credit_wei or 0))
        spent = max(0, int(spent_credit_wei or 0))
        earned = max(0, int(earned_credit_wei or 0))
        bridged = max(0, int(bridge_completed_credit_wei or 0))
        return {
            "account_id": str(account_id or "").strip(),
            "wallet_address": normalize_address(wallet_address),
            "owner_address": normalize_address(wallet_address),
            "available_credit_wei": str(available),
            "held_credit_wei": str(held),
            "spent_credit_wei": str(spent),
            "earned_credit_wei": str(earned),
            "bridge_completed_credit_wei": str(bridged),
            "available_credits": credit_wei_to_whole_credits_floor(available),
            "held_credits": credit_wei_to_whole_credits_floor(held),
            "spent_credits": credit_wei_to_whole_credits_floor(spent),
            "earned_credits": credit_wei_to_whole_credits_floor(earned),
            "bridge_completed_credits": credit_wei_to_whole_credits_floor(bridged),
            "available_credits_display": credit_wei_to_decimal_text(available),
            "held_credits_display": credit_wei_to_decimal_text(held),
            "spent_credits_display": credit_wei_to_decimal_text(spent),
            "earned_credits_display": credit_wei_to_decimal_text(earned),
            "bridge_completed_credits_display": credit_wei_to_decimal_text(bridged),
            "created_at": created_at or now,
            "updated_at": updated_at or now,
            "cluster_id": self.cluster_id,
            "ledger_version": "stable-hub-exp-compatible-credit-ledger-v1",
            "unit": "compute_credit",
            "metadata": dict(metadata or {}),
        }

    def _ensure_account(
        self,
        data: dict[str, Any],
        *,
        account_id: str,
        wallet_address: str = "",
        default_credits: Decimal | None = None,
    ) -> dict[str, Any]:
        clean_account = str(account_id or "").strip()
        if not clean_account:
            raise StableHubWorkerSessionError("requester account_id is required for payout.")
        accounts = data.setdefault("payout_accounts", {})
        account = accounts.get(clean_account)
        if isinstance(account, dict):
            # Migrate older Stable Hub decimal account records into the exp-compatible
            # integer-credit-wei shape without dropping compatibility aliases.
            if "available_credit_wei" not in account:
                available = credit_decimal_text_to_wei(str(account.get("available_credits", "0")))
                held = credit_decimal_text_to_wei(str(account.get("held_credits", "0")))
                spent = credit_decimal_text_to_wei(str(account.get("spent_credits", "0")))
                earned = credit_decimal_text_to_wei(str(account.get("earned_credits", "0")))
                migrated = self._account_payload(
                    account_id=clean_account,
                    wallet_address=str(account.get("wallet_address") or wallet_address or ""),
                    available_credit_wei=available,
                    held_credit_wei=held,
                    spent_credit_wei=spent,
                    earned_credit_wei=earned,
                    bridge_completed_credit_wei=account.get("bridge_completed_credit_wei", 0),
                    created_at=str(account.get("created_at") or ""),
                    updated_at=utc_now(),
                    metadata=dict(account.get("metadata", {}) or {}),
                )
                accounts[clean_account] = migrated
                return dict(migrated)
            if wallet_address and not account.get("wallet_address") and not account.get("owner_address"):
                account = dict(account)
                account["wallet_address"] = normalize_address(wallet_address)
                account["owner_address"] = normalize_address(wallet_address)
                accounts[clean_account] = account
            return dict(account)
        network = dict(getattr(self.topology, "network", {}) or {})
        live_network = str(network.get("network_key") or "").lower() in {"mainnet", "testnet"}
        # A live network must not mint development credits just because a
        # requester wallet first appears. Actual on-chain funding requires a
        # separate verified bridge adapter; absent that, it starts at zero.
        initial_credits = (Decimal("0") if live_network else STABLE_HUB_DEV_ACCOUNT_CREDITS) if default_credits is None else default_credits
        if initial_credits < Decimal("0"):
            raise StableHubWorkerSessionError("initial payout account credits must be zero or greater.")
        initial_wei = credit_decimal_text_to_wei(_decimal_to_string(initial_credits))
        account = self._account_payload(
            account_id=clean_account,
            wallet_address=wallet_address,
            available_credit_wei=initial_wei,
            metadata={"source": "stable_hub_production_zero" if live_network and default_credits is None else ("stable_hub_dev_default" if default_credits is None else "stable_hub_fund_account")},
        )
        accounts[clean_account] = account
        self._event(data, "stable.work.payout.account_created", account_id=clean_account)
        return dict(account)

    @_atomic_stable_store_mutation
    def prepare_bridge_deposit_completion(
        self,
        *,
        account_id: str,
        wallet_address: str,
        deposit_id: str,
        chain_id: int,
        contract_address: str,
        deposit_amount_units: int,
        chain_completed_credit_wei: int,
        already_completed: bool,
    ) -> dict[str, Any]:
        """Persist intent BEFORE signing; distinguish new deposits from history.

        An already-completed escrow deposit that this Stable ledger never
        enrolled must not be replayed as fresh credit, even if it is the only
        historical deposit. A crash after chain completion is retryable because
        the intent was committed in FDB first.
        """
        account = str(account_id or "").strip()
        wallet = normalize_address(wallet_address)
        deposit = str(deposit_id or "").strip().lower()
        contract = normalize_address(contract_address)
        amount = int(deposit_amount_units)
        chain = int(chain_id)
        chain_completed = int(chain_completed_credit_wei)
        if not account or not wallet or not deposit or not contract or amount <= 0 or chain <= 0:
            raise StableHubWorkerSessionError("bridge_deposit_intent_invalid")
        if chain_completed < 0:
            raise StableHubWorkerSessionError("bridge_completed_watermark_invalid")
        with self._lock:
            data = self.store.load()
            intents = data.setdefault("payout_deposit_intents", {})
            expected = {
                "account_id": account,
                "wallet_address": wallet,
                "deposit_id": deposit,
                "chain_id": chain,
                "contract_address": contract,
                "deposit_amount_units": str(amount),
            }
            existing = intents.get(deposit)
            completed_deposits = data.setdefault("payout_deposits", {})
            prior_deposit = completed_deposits.get(deposit)
            if isinstance(prior_deposit, dict):
                if (
                    str(prior_deposit.get("account_id")) != account
                    or str(prior_deposit.get("contract_address")) != contract
                    or str(prior_deposit.get("chain_id")) != str(chain)
                ):
                    raise StableHubWorkerSessionError("bridge_deposit_intent_conflict")
                return {"idempotent": True, "intent": dict(existing or {})}
            account_record = data.setdefault("payout_accounts", {}).get(account)
            if not isinstance(account_record, dict):
                account_record = {}
            owner = normalize_address(account_record.get("wallet_address") or account_record.get("owner_address") or "")
            if owner and owner != wallet:
                raise StableHubWorkerSessionError("bridge_account_wallet_mismatch")
            local_completed = int(account_record.get("bridge_completed_credit_wei", 0) or 0)
            if isinstance(existing, dict):
                if any(str(existing.get(key)) != str(value) for key, value in expected.items()):
                    raise StableHubWorkerSessionError("bridge_deposit_intent_conflict")
                expected_chain_total = local_completed + (amount if already_completed else 0)
                if chain_completed != expected_chain_total:
                    raise StableHubWorkerSessionError("bridge_historical_completion_requires_reconciliation")
                return {"idempotent": True, "intent": dict(existing)}
            if already_completed:
                raise StableHubWorkerSessionError("bridge_preexisting_completed_deposit_requires_reconciliation")
            # Compare the escrow's cumulative completed total with Stable's
            # durable watermark before completing this deposit. A higher chain
            # total can represent credit already issued and spent by another
            # ledger; completing first would strand a newly completed deposit.
            if chain_completed != local_completed:
                error = (
                    "bridge_completed_watermark_ahead_of_chain"
                    if chain_completed < local_completed
                    else "bridge_historical_completion_requires_reconciliation"
                )
                raise StableHubWorkerSessionError(error)
            intent = {**expected, "status": "prepared", "created_at": utc_now()}
            intents[deposit] = intent
            self.store.save(data)
            return {"idempotent": False, "intent": dict(intent)}

    @_atomic_stable_store_mutation
    def record_completed_bridge_deposit(
        self,
        *,
        account_id: str,
        wallet_address: str,
        chain_completed_credit_wei: int | str,
        deposit_id: str,
        deposit_amount_units: int | str | None = None,
        completion_tx_hash: str = "",
        chain_id: int = 0,
        contract_address: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a *verified-by-caller* cumulative chain completion exactly once.

        This internal accounting primitive neither reads a chain nor exposes
        an HTTP crediting endpoint. Patch 2 must verify completion authority
        before calling it. The account, credit delta, and cumulative watermark
        are committed together in the Stable Hub FDB transaction.
        """
        clean_account = str(account_id or "").strip()
        clean_wallet = normalize_address(wallet_address)
        clean_deposit = str(deposit_id or "").strip().lower()
        if not clean_account or not clean_wallet or not clean_deposit:
            raise StableHubWorkerSessionError("account_id, wallet_address, and deposit_id are required.")
        try:
            chain_completed = int(chain_completed_credit_wei)
        except (TypeError, ValueError) as exc:
            raise StableHubWorkerSessionError("chain_completed_credit_wei must be an integer.") from exc
        if chain_completed < 0 or str(chain_completed_credit_wei).strip() != str(chain_completed):
            raise StableHubWorkerSessionError("chain_completed_credit_wei must be a nonnegative integer wei amount.")
        now = utc_now()
        with self._lock:
            data = self.store.load()
            account = self._ensure_account(
                data, account_id=clean_account, wallet_address=clean_wallet, default_credits=Decimal("0")
            )
            owner = normalize_address(account.get("wallet_address") or account.get("owner_address") or "")
            if owner and owner != clean_wallet:
                raise StableHubWorkerSessionError("bridge_account_wallet_mismatch")
            local_completed = int(account.get("bridge_completed_credit_wei", 0) or 0)
            if chain_completed < local_completed:
                raise StableHubWorkerSessionError("bridge_completed_watermark_ahead_of_chain")
            delta = chain_completed - local_completed
            # The Experimental ledger may have accounted for earlier deposits,
            # charges, or refunds under a DIFFERENT FDB namespace. An unseeded
            # Stable account cannot treat the entire historical escrow total as
            # new spendable credit. Only the first individual verified deposit
            # may initialize a fresh watermark; otherwise fail closed pending
            # explicit migration/reconciliation of the historical account.
            if deposit_amount_units is not None and local_completed == 0 and delta > 0:
                try:
                    first_deposit = int(deposit_amount_units)
                except (TypeError, ValueError) as exc:
                    raise StableHubWorkerSessionError("bridge_deposit_amount_invalid") from exc
                if first_deposit <= 0 or str(deposit_amount_units).strip() != str(first_deposit):
                    raise StableHubWorkerSessionError("bridge_deposit_amount_invalid")
                if chain_completed != first_deposit:
                    raise StableHubWorkerSessionError("bridge_historical_completion_requires_reconciliation")
            if delta == 0:
                return {
                    "idempotent": True,
                    "delta_credit_wei": "0",
                    "chain_completed_credit_wei": str(chain_completed),
                    "local_completed_credit_wei": str(local_completed),
                    "deposit_id": clean_deposit,
                    "account": dict(account),
                }
            if deposit_amount_units is not None:
                intent = data.setdefault("payout_deposit_intents", {}).get(clean_deposit)
                if not isinstance(intent, dict) or (
                    str(intent.get("account_id")) != clean_account
                    or str(intent.get("wallet_address")) != clean_wallet
                    or str(intent.get("contract_address")) != normalize_address(contract_address)
                    or str(intent.get("chain_id")) != str(int(chain_id))
                    or str(intent.get("deposit_amount_units")) != str(int(deposit_amount_units))
                ):
                    raise StableHubWorkerSessionError("bridge_completion_missing_verified_intent")
            previous_deposit = data.setdefault("payout_deposits", {}).get(clean_deposit)
            if isinstance(previous_deposit, dict):
                raise StableHubWorkerSessionError("bridge_deposit_replay_conflicts_with_watermark")
            updated = self._account_payload(
                account_id=clean_account,
                wallet_address=clean_wallet,
                available_credit_wei=int(account.get("available_credit_wei", "0") or 0) + delta,
                held_credit_wei=account.get("held_credit_wei", 0),
                spent_credit_wei=account.get("spent_credit_wei", 0),
                earned_credit_wei=account.get("earned_credit_wei", 0),
                bridge_completed_credit_wei=chain_completed,
                created_at=str(account.get("created_at") or ""),
                updated_at=now,
                metadata={**dict(account.get("metadata") or {}), "funding_model": "hub_credit_bridge_escrow_wallet_v2"},
            )
            data.setdefault("payout_accounts", {})[clean_account] = updated
            data["payout_deposits"][clean_deposit] = {
                "deposit_id": clean_deposit,
                "account_id": clean_account,
                "payer_address": clean_wallet,
                "credits_granted": credit_wei_to_whole_credits_floor(delta),
                "credits_granted_wei": str(delta),
                "credits_granted_display": credit_wei_to_decimal_text(delta),
                "status": "bridge_completed",
                "created_at": now,
                "completion_tx_hash": str(completion_tx_hash or "").strip().lower(),
                "chain_completed_credit_wei": str(chain_completed),
                "chain_id": int(chain_id),
                "contract_address": normalize_address(contract_address),
                "metadata": dict(metadata or {}),
            }
            transaction = self._transaction(
                data,
                transaction_type="bridge_deposit_completed",
                account_id=clean_account,
                credit_wei=delta,
                deposit_id=clean_deposit,
                memo="verified cumulative bridge completion",
                metadata={
                    "completion_tx_hash": str(completion_tx_hash or "").strip().lower(),
                    "chain_completed_credit_wei": str(chain_completed),
                    "chain_id": int(chain_id),
                    "contract_address": normalize_address(contract_address),
                    **dict(metadata or {}),
                },
                transaction_id=stable_id(
                    "ctx", {"type": "bridge_deposit_completed", "account_id": clean_account, "deposit_id": clean_deposit}
                ),
            )
            self.store.save(data)
            return {
                "idempotent": False,
                "delta_credit_wei": str(delta),
                "chain_completed_credit_wei": str(chain_completed),
                "local_completed_credit_wei": str(local_completed),
                "deposit_id": clean_deposit,
                "account": dict(updated),
                "transaction": dict(transaction),
            }

    @_atomic_stable_store_mutation
    def fund_account(
        self,
        *,
        account_id: str,
        wallet_address: str = "",
        credits: Any,
        replace: bool = False,
    ) -> dict[str, Any]:
        amount_wei = self._amount_to_credit_wei(credits, field_name="credits")
        clean_account = str(account_id or "").strip()
        if not clean_account:
            raise StableHubWorkerSessionError("account_id is required.")
        with self._lock:
            data = self.store.load()
            account = self._ensure_account(
                data,
                account_id=clean_account,
                wallet_address=wallet_address,
                default_credits=Decimal("0"),
            )
            available = int(account.get("available_credit_wei", "0") or 0)
            updated = self._account_payload(
                account_id=clean_account,
                wallet_address=wallet_address or account.get("wallet_address") or account.get("owner_address") or "",
                available_credit_wei=amount_wei if replace else available + amount_wei,
                held_credit_wei=account.get("held_credit_wei", 0),
                spent_credit_wei=account.get("spent_credit_wei", 0),
                earned_credit_wei=account.get("earned_credit_wei", 0),
                bridge_completed_credit_wei=account.get("bridge_completed_credit_wei", 0),
                created_at=str(account.get("created_at") or ""),
                updated_at=utc_now(),
                metadata=dict(account.get("metadata", {}) or {}),
            )
            data.setdefault("payout_accounts", {})[clean_account] = updated
            deposit_id = stable_id(
                "dep",
                {
                    "account_id": clean_account,
                    "wallet_address": normalize_address(wallet_address),
                    "credit_wei": str(amount_wei),
                    "replace": bool(replace),
                    "created_at": utc_now(),
                    # A clock tick is not an identity: concurrent grants can
                    # have identical timestamps, especially on Windows.
                    "nonce": secrets.token_hex(16),
                },
            )
            deposit = {
                "deposit_id": deposit_id,
                "account_id": clean_account,
                "payer_address": normalize_address(wallet_address),
                "credits_granted": credit_wei_to_whole_credits_floor(amount_wei),
                "credits_granted_wei": str(amount_wei),
                "credits_granted_display": credit_wei_to_decimal_text(amount_wei),
                "status": "indexed",
                "memo": "stable hub dev account funding",
                "created_at": utc_now(),
                "metadata": {"replace": bool(replace), "stable_hub": True},
            }
            data.setdefault("payout_deposits", {})[deposit_id] = deposit
            self._transaction(
                data,
                transaction_type="deposit_indexed",
                account_id=clean_account,
                credit_wei=amount_wei,
                deposit_id=deposit_id,
                memo="stable hub dev account funding",
                metadata={"replace": bool(replace), "stable_hub": True},
            )
            self._event(
                data,
                "stable.work.payout.account_funded",
                account_id=clean_account,
                credit_wei=str(amount_wei),
                credits=credit_wei_to_decimal_text(amount_wei),
                replace=replace,
            )
            self.store.save(data)
            return dict(updated)

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        clean_account = str(account_id or "").strip()
        if not clean_account:
            return None
        with self._lock:
            data = self.store.load()
            account = data.get("payout_accounts", {}).get(clean_account)
            return dict(account) if isinstance(account, dict) else None

    def get_hold_for_request(self, *, account_id: str, request_id: str) -> dict[str, Any] | None:
        clean_account = str(account_id or "").strip()
        if not clean_account:
            return None
        request_id = normalize_request_id(request_id)
        hold_id = _stable_digest_id("hold", self.cluster_id, clean_account, request_id)
        with self._lock:
            data = self.store.load()
            hold = data.get("payout_holds", {}).get(hold_id)
            return dict(hold) if isinstance(hold, dict) else None

    @_atomic_stable_store_mutation
    def create_hold(
        self, *, account_id: str, wallet_address: str, request_id: str,
        session_id: str, worker_id: str, selected_price: dict[str, Any],
        run_id: str = "", partition: str = "",
        requester_max_price: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Reserve one request's price without earning or charging it.

        The reservation and account debit share an FDB transaction. Another
        Hub cannot spend reserved funds, and a retry cannot reserve twice.
        """
        account_id = str(account_id or "").strip()
        request_id = normalize_request_id(request_id)
        session_id = normalize_session_id(session_id)
        worker_id = normalize_worker_id(worker_id)
        price = normalize_price(selected_price, field_name="selected worker price")
        maximum = None
        amount = self._amount_to_credit_wei(price.get("amount"), field_name="selected worker price.amount")
        if requester_max_price:
            maximum = normalize_price(requester_max_price, field_name="requester max_price")
            if maximum.get("unit") != price.get("unit") or amount > self._amount_to_credit_wei(
                maximum.get("amount"), field_name="requester max_price.amount"
            ):
                raise StableHubWorkerSessionError("selected worker price exceeds requester max_price.")
        if not account_id:
            raise StableHubWorkerSessionError("account_id is required.")
        hold_id = _stable_digest_id("hold", self.cluster_id, account_id, request_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            holds = data.setdefault("payout_holds", {})
            existing = holds.get(hold_id)
            if isinstance(existing, dict):
                if (str(existing.get("session_id")) != session_id
                    or str(existing.get("worker_id")) != worker_id
                    or int(existing.get("credit_wei", "0")) != amount
                    or str(existing.get("unit")) != str(price.get("unit"))):
                    raise StableHubWorkerSessionError("request_hold_conflict")
                if existing.get("status") != "held":
                    raise StableHubWorkerSessionError("request_hold_already_finalized")
                return dict(existing)
            account = self._ensure_account(data, account_id=account_id, wallet_address=wallet_address)
            if (normalize_address(account.get("wallet_address") or "")
                and normalize_address(account.get("wallet_address") or "") != normalize_address(wallet_address)):
                raise StableHubWorkerSessionError("request_account_wallet_mismatch")
            available = int(account.get("available_credit_wei", "0"))
            if available < amount:
                raise StableHubWorkerSessionError("requester_funds_unavailable")
            data["payout_accounts"][account_id] = self._account_payload(
                account_id=account_id,
                wallet_address=str(account.get("wallet_address") or wallet_address),
                available_credit_wei=available - amount,
                held_credit_wei=int(account.get("held_credit_wei", "0")) + amount,
                spent_credit_wei=account.get("spent_credit_wei", 0),
                earned_credit_wei=account.get("earned_credit_wei", 0),
                bridge_completed_credit_wei=account.get("bridge_completed_credit_wei", 0),
                created_at=str(account.get("created_at") or ""),
                metadata=dict(account.get("metadata") or {}),
            )
            hold = {
                "hold_id": hold_id, "status": "held", "account_id": account_id,
                "wallet_address": normalize_address(wallet_address),
                "request_id": request_id, "session_id": session_id,
                "run_id": str(run_id), "partition": str(partition),
                "selected_price": dict(price), "requester_max_price": dict(maximum or {}),
                "worker_id": worker_id, "worker_node_id": worker_id,
                "credit_wei": str(amount), "amount": credit_wei_to_decimal_text(amount),
                "unit": price.get("unit"), "created_at": now, "updated_at": now,
            }
            holds[hold_id] = hold
            self._event(data, "stable.work.payout.held", hold_id=hold_id,
                        session_id=session_id, request_id=request_id, account_id=account_id)
            self.store.save(data)
            return dict(hold)

    @_atomic_stable_store_mutation
    def release_hold(self, *, hold_id: str, session_id: str) -> dict[str, Any]:
        """Return reserved funds on failure; never invent a second refund."""
        with self._lock:
            data = self.store.load()
            holds = data.setdefault("payout_holds", {})
            hold = holds.get(hold_id)
            if not isinstance(hold, dict) or str(hold.get("session_id")) != str(session_id):
                raise StableHubWorkerSessionError("request_hold_missing_or_mismatched")
            if hold.get("status") == "released":
                return dict(hold)
            if hold.get("status") != "held":
                raise StableHubWorkerSessionError("request_hold_already_charged")
            account_id = str(hold["account_id"])
            account = data["payout_accounts"][account_id]
            amount = int(hold["credit_wei"])
            held = int(account.get("held_credit_wei", 0))
            if held < amount:
                raise StableHubWorkerSessionError("request_hold_account_inconsistent")
            data["payout_accounts"][account_id] = self._account_payload(
                account_id=account_id,
                wallet_address=str(account.get("wallet_address") or ""),
                available_credit_wei=int(account.get("available_credit_wei", 0)) + amount,
                held_credit_wei=held - amount,
                spent_credit_wei=account.get("spent_credit_wei", 0),
                earned_credit_wei=account.get("earned_credit_wei", 0),
                bridge_completed_credit_wei=account.get("bridge_completed_credit_wei", 0),
                created_at=str(account.get("created_at") or ""),
                metadata=dict(account.get("metadata") or {}),
            )
            hold = dict(hold, status="released", released_at=utc_now(), updated_at=utc_now())
            holds[hold_id] = hold
            self._event(data, "stable.work.payout.released", hold_id=hold_id,
                        session_id=session_id, request_id=hold["request_id"], account_id=account_id)
            self.store.save(data)
            return dict(hold)

    def charge_hold(
        self, *, hold_id: str, session_id: str, request_id: str,
        worker_id: str, result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert exactly one reserved request to a charged worker earning."""
        with self._lock:
            hold = self.store.load().get("payout_holds", {}).get(hold_id)
            if not isinstance(hold, dict):
                raise StableHubWorkerSessionError("request_hold_missing_or_mismatched")
            hold = dict(hold)
        return self.spend_request_credit(
            account_id=str(hold.get("account_id") or ""),
            wallet_address=str(hold.get("wallet_address") or ""),
            request_id=request_id, session_id=session_id,
            run_id=str(hold.get("run_id") or ""), worker_id=worker_id,
            selected_price=dict(hold.get("selected_price") or {
                "amount": hold.get("amount"), "unit": hold.get("unit")
            }), requester_max_price=dict(hold.get("requester_max_price") or {}) or None,
            partition=str(hold.get("partition") or ""), hold_id=hold_id,
            result=result,
        )

    @_atomic_stable_store_mutation
    def spend_request_credit(
        self,
        *,
        account_id: str,
        wallet_address: str,
        request_id: str,
        session_id: str,
        run_id: str,
        worker_id: str,
        selected_price: dict[str, Any],
        requester_max_price: dict[str, Any] | None,
        partition: str,
        worker_account_id: str = "",
        hold_id: str = "",
        result: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = normalize_request_id(request_id)
        session_id = normalize_session_id(session_id)
        worker_id = normalize_worker_id(worker_id)
        # Paid live sessions pass the wallet-derived account validated by the
        # Hub. worker_id here is only the Hub-created execution/connection slot.
        # Standalone ledger calls without a live socket can name the account
        # directly as worker_id.
        earning_account_id = (
            wallet_account_id(worker_account_id)
            if worker_account_id else worker_id
        )
        clean_account = str(account_id or "").strip()
        if not clean_account:
            raise StableHubWorkerSessionError("account_id is required.")
        price = normalize_price(selected_price, field_name="selected worker price")
        amount_wei = self._amount_to_credit_wei(price.get("amount"), field_name="selected worker price.amount")
        max_price = normalize_price(requester_max_price, field_name="requester max_price") if requester_max_price else None
        if max_price is not None:
            if str(max_price.get("unit")) != str(price.get("unit")):
                raise StableHubWorkerSessionError("selected worker price unit does not match requester max_price unit.")
            max_price_wei = self._amount_to_credit_wei(max_price.get("amount"), field_name="requester max_price.amount")
            if amount_wei > max_price_wei:
                raise StableHubWorkerSessionError("selected worker price exceeds requester max_price.")
        now = utc_now()
        charge_id = stable_id(
            "chg",
            {
                "account_id": clean_account,
                "request_id": request_id,
                "session_id": session_id,
                "worker_id": worker_id,
                "credit_wei": str(amount_wei),
                "direct_spend": not bool(hold_id),
            },
        )
        earning_id = stable_id(
            "earn",
            {
                "worker_account_id": earning_account_id,
                "requester_account_id": clean_account,
                "request_id": request_id,
                "earned_credit_wei": str(amount_wei),
            },
        )
        with self._lock:
            data = self.store.load()
            existing_charge = data.setdefault("payout_charges", {}).get(charge_id)
            existing_earning = data.setdefault("worker_earnings", {}).get(earning_id)
            if isinstance(existing_charge, dict):
                if worker_id and not isinstance(existing_earning, dict):
                    raise StableHubWorkerSessionError("request_charge_earning_inconsistent")
                return {
                    "charge": dict(existing_charge),
                    "worker_earning": dict(existing_earning) if isinstance(existing_earning, dict) else {},
                    "idempotent": True,
                    "direct_spend": not bool(hold_id),
                    "legacy_holds_cancelled": [],
                }

            # The logical request is uniquely charged per requester, even if
            # a retry is routed to a different session or worker. A changed
            # charge identity for the same request is a conflict, not revenue.
            for prior in data.setdefault("payout_charges", {}).values():
                if not isinstance(prior, dict):
                    continue
                if str(prior.get("account_id") or "") != clean_account:
                    continue
                if str(prior.get("request_id") or "") != request_id:
                    continue
                if str(prior.get("charge_id") or "") != charge_id:
                    raise StableHubWorkerSessionError("request_charge_conflict")

            account = self._ensure_account(data, account_id=clean_account, wallet_address=wallet_address)
            available = int(account.get("available_credit_wei", "0") or 0)
            held = int(account.get("held_credit_wei", "0") or 0)
            reservation = data.setdefault("payout_holds", {}).get(hold_id) if hold_id else None
            if hold_id:
                if (not isinstance(reservation, dict)
                    or reservation.get("status") != "held"
                    or str(reservation.get("account_id")) != clean_account
                    or str(reservation.get("session_id")) != session_id
                    or str(reservation.get("request_id")) != request_id
                    or str(reservation.get("worker_id")) != worker_id
                    or int(reservation.get("credit_wei", 0)) != amount_wei
                    or str(reservation.get("unit")) != str(price.get("unit"))
                    or (normalize_address(reservation.get("wallet_address") or "")
                        != normalize_address(wallet_address))
                    or held < amount_wei):
                    raise StableHubWorkerSessionError("request_hold_missing_or_mismatched")
            spendable = held if hold_id else available
            if spendable < amount_wei:
                self._event(
                    data,
                    "stable.work.payout.spend_rejected",
                    session_id=session_id,
                    request_id=request_id,
                    account_id=clean_account,
                    worker_id=worker_id,
                    credit_wei=str(amount_wei),
                    amount=credit_wei_to_decimal_text(amount_wei),
                    reason="insufficient_requester_credits",
                )
                self.store.save(data)
                raise StableHubWorkerSessionError("requester_funds_unavailable")

            # Never cancel unrelated reservations. Each accepted session owns
            # exactly one held amount; a direct-spend compatibility call may use
            # only the unreserved available balance.
            collapsed_holds: list[str] = []
            if hold_id:
                data["payout_holds"][hold_id] = dict(
                    reservation, status="charged", charged_at=now, updated_at=now
                )

            updated_account = self._account_payload(
                account_id=account["account_id"],
                wallet_address=account.get("wallet_address") or account.get("owner_address") or wallet_address or "",
                available_credit_wei=available if hold_id else available - amount_wei,
                held_credit_wei=held - amount_wei if hold_id else held,
                spent_credit_wei=int(account.get("spent_credit_wei", "0") or 0) + amount_wei,
                earned_credit_wei=account.get("earned_credit_wei", 0),
                bridge_completed_credit_wei=account.get("bridge_completed_credit_wei", 0),
                created_at=str(account.get("created_at") or ""),
                updated_at=now,
                metadata=dict(account.get("metadata", {}) or {}),
            )

            charge = {
                "charge_id": charge_id,
                "hold_id": hold_id,
                "session_id": session_id,
                "run_id": str(run_id),
                "account_id": clean_account,
                "request_id": request_id,
                "worker_id": earning_account_id,
                "worker_account_id": earning_account_id,
                "worker_node_id": worker_id,
                "amount": credit_wei_to_decimal_text(amount_wei),
                "unit": price.get("unit"),
                "charged_credits": credit_wei_to_whole_credits_floor(amount_wei),
                "charged_credit_wei": str(amount_wei),
                "charged_credits_display": credit_wei_to_decimal_text(amount_wei),
                "released_credits": 0,
                "released_credit_wei": "0",
                "released_credits_display": "0",
                "worker_earning_id": earning_id,
                "status": "charged",
                "created_at": now,
                "metadata": {
                    "direct_spend": not bool(hold_id),
                    "legacy_holds_cancelled": collapsed_holds,
                    **json.loads(json.dumps(metadata or {})),
                },
            }
            earning = {
                "earning_id": earning_id,
                "charge_id": charge_id,
                "hold_id": hold_id,
                "session_id": session_id,
                "run_id": str(run_id),
                "account_id": clean_account,
                "request_id": request_id,
                "worker_id": earning_account_id,
                "worker_account_id": earning_account_id,
                "worker_node_id": worker_id,
                "worker_commitment": make_worker_commitment(
                    worker_node_id=worker_id,
                    request_id=request_id,
                    epoch_salt=self.ledger_namespace,
                ),
                "amount": credit_wei_to_decimal_text(amount_wei),
                "unit": price.get("unit"),
                "credits": credit_wei_to_whole_credits_floor(amount_wei),
                "earned_credit_wei": str(amount_wei),
                "earned_credits_display": credit_wei_to_decimal_text(amount_wei),
                "status": "earned",
                "claim_status": "unclaimed",
                "settlement_status": "not_settled",
                "batch_id": "",
                "created_at": now,
                "result": json.loads(json.dumps(result or {})),
                "metadata": {"stable_hub": True, "direct_spend": not bool(hold_id), **dict(metadata or {})},
            }
            data.setdefault("payout_accounts", {})[account["account_id"]] = updated_account
            data.setdefault("payout_charges", {})[charge_id] = charge
            data.setdefault("worker_earnings", {})[earning_id] = earning
            self._transaction(
                data,
                transaction_type="request_charged",
                account_id=account["account_id"],
                credit_wei=amount_wei,
                request_id=request_id,
                worker_id=worker_id,
                hold_id=hold_id,
                memo=f"spent credits for stable work request {request_id}",
                metadata={
                    "charge_id": charge_id,
                    "session_id": session_id,
                    "direct_spend": not bool(hold_id),
                    "legacy_holds_cancelled": collapsed_holds,
                    **dict(metadata or {}),
                },
            )
            self._bridge_audit_event(
                data,
                event_type="hub.request.charged",
                wallet_address=wallet_address,
                account_id=clean_account,
                worker_id=worker_id,
                amount_wei=amount_wei,
                reference_id=charge_id,
                metadata={"request_id": request_id, "session_id": session_id, "direct_spend": not bool(hold_id), **dict(metadata or {})},
            )
            self._bridge_audit_event(
                data,
                event_type="hub.worker.earning.recorded",
                worker_id=worker_id,
                amount_wei=amount_wei,
                reference_id=earning_id,
                metadata={"request_id": request_id, "charge_id": charge_id, "account_id": clean_account, "direct_spend": not bool(hold_id), **dict(metadata or {})},
            )
            self._event(
                data,
                "stable.work.payout.spent",
                charge_id=charge_id,
                earning_id=earning_id,
                session_id=session_id,
                request_id=request_id,
                account_id=clean_account,
                worker_id=worker_id,
                amount=charge["amount"],
                credit_wei=charge["charged_credit_wei"],
                unit=price.get("unit"),
                direct_spend=not bool(hold_id),
            )
            self.store.save(data)
            return {
                "charge": dict(charge),
                "worker_earning": dict(earning),
                "idempotent": False,
                "direct_spend": not bool(hold_id),
                "legacy_holds_cancelled": collapsed_holds,
                "selected_price": {**dict(price), "credit_wei": str(amount_wei)},
                "requester_max_price": dict(max_price) if max_price else {},
            }

    @_atomic_stable_store_mutation
    def record_worker_claim(self, *, worker_id: str, earning_ids: list[str] | None = None, idempotency_key: str = "") -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        key = str(idempotency_key or "").strip()
        with self._lock:
            data = self.store.load()
            earnings = data.setdefault("worker_earnings", {})
            selected_ids = [str(item) for item in (earning_ids or []) if str(item)]
            if not selected_ids:
                selected_ids = [
                    eid
                    for eid, earning in earnings.items()
                    if isinstance(earning, dict)
                    and earning.get("worker_id") == worker_id
                    and earning.get("claim_status") == "unclaimed"
                    and earning.get("status") == "earned"
                ]
            if key:
                for claim in data.setdefault("worker_claims", {}).values():
                    if isinstance(claim, dict) and claim.get("worker_id") == worker_id and claim.get("idempotency_key") == key:
                        return dict(claim)
            if not selected_ids:
                return {
                    "claim_id": _stable_digest_id("claim-empty", self.cluster_id, worker_id, key or "no-unclaimed-earnings"),
                    "worker_id": worker_id,
                    "worker_node_id": worker_id,
                    "earning_ids": [],
                    "amount": "0",
                    "unit": "credit",
                    "claimed_credits": 0,
                    "claimed_credit_wei": "0",
                    "claimed_credits_display": "0",
                    "status": "empty",
                    "settlement_status": "not_settled",
                    "idempotency_key": key,
                    "created_at": utc_now(),
                    "empty": True,
                }
            amount_wei = 0
            unit = "credit"
            claimable_ids: list[str] = []
            for earning_id in selected_ids:
                earning = earnings.get(earning_id)
                if not isinstance(earning, dict) or earning.get("worker_id") != worker_id:
                    raise StableHubWorkerSessionError("worker earning is not claimable by this worker.")
                if earning.get("claim_status") != "unclaimed" or earning.get("status") != "earned":
                    continue
                unit = str(earning.get("unit") or unit)
                amount_wei += int(earning.get("earned_credit_wei") or credit_decimal_text_to_wei(str(earning.get("amount", "0"))))
                earning = dict(earning)
                earning["claim_status"] = "claimed"
                earnings[earning_id] = earning
                claimable_ids.append(earning_id)
            if not claimable_ids:
                return {
                    "claim_id": _stable_digest_id("claim-empty", self.cluster_id, worker_id, key or "already-claimed"),
                    "worker_id": worker_id,
                    "worker_node_id": worker_id,
                    "earning_ids": [],
                    "amount": "0",
                    "unit": unit,
                    "claimed_credits": 0,
                    "claimed_credit_wei": "0",
                    "claimed_credits_display": "0",
                    "status": "empty",
                    "settlement_status": "not_settled",
                    "idempotency_key": key,
                    "created_at": utc_now(),
                    "empty": True,
                }
            claim_id = stable_id(
                "wclaim",
                {
                    "worker_node_id": worker_id,
                    "earning_ids": claimable_ids,
                    "claimed_credit_wei": str(amount_wei),
                    "idempotency_key": key,
                },
            )
            claim = {
                "claim_id": claim_id,
                "worker_id": worker_id,
                "worker_node_id": worker_id,
                "earning_ids": claimable_ids,
                "amount": credit_wei_to_decimal_text(amount_wei),
                "unit": unit,
                "claimed_credits": credit_wei_to_whole_credits_floor(amount_wei),
                "claimed_credit_wei": str(amount_wei),
                "claimed_credits_display": credit_wei_to_decimal_text(amount_wei),
                "status": "claimed",
                "settlement_status": "not_settled",
                "idempotency_key": key,
                "created_at": utc_now(),
                "metadata": {"stable_hub": True},
            }
            data.setdefault("worker_claims", {})[claim_id] = claim
            self._transaction(
                data,
                transaction_type="worker_claimed",
                account_id=worker_id,
                credit_wei=amount_wei,
                worker_id=worker_id,
                memo=f"worker claimed {len(claimable_ids)} earning(s)",
                metadata={"claim_id": claim_id, "earning_ids": claimable_ids},
            )
            self._event(data, "stable.work.payout.claimed", claim_id=claim_id, worker_id=worker_id, amount=claim["amount"], credit_wei=claim["claimed_credit_wei"], unit=unit)
            self.store.save(data)
            return dict(claim)

    @_atomic_stable_store_mutation
    def create_worker_settlement_batch(self, *, worker_id: str, claim_ids: list[str] | None = None, idempotency_key: str = "") -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        key = str(idempotency_key or "").strip()
        with self._lock:
            data = self.store.load()
            selected_ids = [str(item) for item in (claim_ids or []) if str(item)]
            if len(selected_ids) != len(set(selected_ids)):
                raise StableHubWorkerSessionError("duplicate_claim_ids")
            batches = data.setdefault("payout_settlements", {})
            if key:
                for batch in batches.values():
                    if isinstance(batch, dict) and batch.get("worker_id") == worker_id and batch.get("idempotency_key") == key:
                        # An idempotency key identifies a financial intention, not
                        # merely a prior response. Claim ordering is immaterial.
                        if selected_ids and set(selected_ids) != set(batch.get("claim_ids") or []):
                            raise StableHubWorkerSessionError("settlement_batch_idempotency_conflict")
                        return dict(batch)
            claims = data.setdefault("worker_claims", {})
            if not selected_ids:
                selected_ids = [
                    cid
                    for cid, claim in claims.items()
                    if isinstance(claim, dict)
                    and claim.get("worker_id") == worker_id
                    and claim.get("settlement_status") == "not_settled"
                    and claim.get("status") == "claimed"
                ]
            if not selected_ids:
                return {
                    "batch_id": _stable_digest_id("settle-empty", self.cluster_id, worker_id, key or "no-unsettled-claims"),
                    "worker_id": worker_id,
                    "worker_node_id": worker_id,
                    "claim_ids": [],
                    "amount": "0",
                    "unit": "credit",
                    "total_credits_exact": 0,
                    "total_credits_published": 0,
                    "total_credit_wei_exact": "0",
                    "total_credit_wei_published": "0",
                    "dust_credit_wei": "0",
                    "status": "empty",
                    "idempotency_key": key,
                    "created_at": utc_now(),
                    "empty": True,
                }
            # Validate the entire intention before changing any claim. A
            # pre-existing batch owns its claims even after being settled; the
            # same earning must never be re-batched under a different key.
            already_batched = {
                str(claim_id)
                for batch in batches.values()
                if isinstance(batch, dict) and str(batch.get("status") or "") != "cancelled"
                for claim_id in batch.get("claim_ids", [])
            }
            amount_wei = 0
            unit = "credit"
            for claim_id in selected_ids:
                claim = claims.get(claim_id)
                if not isinstance(claim, dict) or claim.get("worker_id") != worker_id:
                    raise StableHubWorkerSessionError("worker claim is not settleable by this worker.")
                if claim.get("status") != "claimed":
                    raise StableHubWorkerSessionError("worker claim is not settleable from its current status.")
                if claim_id in already_batched or str(claim.get("settlement_status") or "not_settled") != "not_settled":
                    raise StableHubWorkerSessionError("worker_claim_already_batched")
                unit = str(claim.get("unit") or unit)
                amount_wei += int(claim.get("claimed_credit_wei") or credit_decimal_text_to_wei(str(claim.get("amount", "0"))))
            for claim_id in selected_ids:
                claim = dict(claims[claim_id])
                claim["settlement_status"] = "batched"
                claims[claim_id] = claim
            batch_id = stable_id(
                "batch",
                {
                    "window_start": "",
                    "window_end": utc_now(),
                    "total_credit_wei_published": str(amount_wei),
                    "claim_ids": selected_ids,
                    "worker_node_id": worker_id,
                    "idempotency_key": key,
                },
            )
            batch = {
                "batch_id": batch_id,
                "worker_id": worker_id,
                "worker_node_id": worker_id,
                "claim_ids": selected_ids,
                "amount": credit_wei_to_decimal_text(amount_wei),
                "unit": unit,
                "total_credits_exact": credit_wei_to_whole_credits_floor(amount_wei),
                "total_credits_published": credit_wei_to_whole_credits_floor(amount_wei),
                "dust_credits": 0,
                "total_credit_wei_exact": str(amount_wei),
                "total_credit_wei_published": str(amount_wei),
                "dust_credit_wei": "0",
                "total_credits_exact_display": credit_wei_to_decimal_text(amount_wei),
                "total_credits_published_display": credit_wei_to_decimal_text(amount_wei),
                "dust_credits_display": "0",
                "worker_count": 1,
                "status": "batched",
                "idempotency_key": key,
                "created_at": utc_now(),
                "metadata": {"stable_hub": True, "golden_path": True},
            }
            data.setdefault("payout_settlements", {})[batch_id] = batch
            self._event(data, "stable.work.payout.settlement_batched", batch_id=batch_id, worker_id=worker_id, amount=batch["amount"], credit_wei=batch["total_credit_wei_published"], unit=unit)
            self.store.save(data)
            return dict(batch)

    @_atomic_stable_store_mutation
    def settle_worker_settlement_batch(self, *, batch_id: str, settlement_reference: str = "", idempotency_key: str = "") -> dict[str, Any]:
        clean_batch = str(batch_id or "").strip()
        if not clean_batch:
            raise StableHubWorkerSessionError("batch_id is required.")
        with self._lock:
            data = self.store.load()
            batches = data.setdefault("payout_settlements", {})
            batch = batches.get(clean_batch)
            if not isinstance(batch, dict):
                raise StableHubWorkerSessionError("worker settlement batch does not exist.")
            effective_reference = str(settlement_reference or clean_batch)
            effective_idempotency_key = str(idempotency_key or "")
            if batch.get("status") == "settled":
                existing_reference = str(batch.get("settlement_reference") or clean_batch)
                existing_key = str(batch.get("settlement_idempotency_key") or "")
                if existing_reference != effective_reference:
                    raise StableHubWorkerSessionError("settlement_reference_conflict")
                if effective_idempotency_key and existing_key and existing_key != effective_idempotency_key:
                    raise StableHubWorkerSessionError("settlement_idempotency_key_conflict")
                return dict(batch)
            if batch.get("status") not in {"batched", "opened", "approved"}:
                raise StableHubWorkerSessionError("worker settlement batch is not settleable from its current status.")
            batch = dict(batch)
            batch["status"] = "settled"
            batch["settled_at"] = utc_now()
            batch["settlement_reference"] = effective_reference
            batch["settlement_idempotency_key"] = effective_idempotency_key
            batches[clean_batch] = batch
            for claim_id in batch.get("claim_ids", []):
                claim = data.setdefault("worker_claims", {}).get(str(claim_id))
                if isinstance(claim, dict):
                    claim = dict(claim)
                    claim["settlement_status"] = "settled"
                    claim["status"] = "settled"
                    data["worker_claims"][str(claim_id)] = claim
            self._transaction(
                data,
                transaction_type="batch_settled",
                account_id=str(batch.get("worker_id") or ""),
                credit_wei=int(batch.get("total_credit_wei_published") or 0),
                worker_id=str(batch.get("worker_id") or ""),
                batch_id=clean_batch,
                memo=f"stable worker settlement batch settled {clean_batch}",
                metadata={"settlement_reference": batch["settlement_reference"], "claim_ids": batch.get("claim_ids", [])},
            )
            self._event(data, "stable.work.payout.settled", batch_id=clean_batch, worker_id=batch.get("worker_id"), amount=batch.get("amount"), credit_wei=batch.get("total_credit_wei_published"), unit=batch.get("unit"))
            self.store.save(data)
            return dict(batch)

    @_atomic_stable_store_mutation
    def request_bridge_payout(self, *, worker_id: str, batch_id: str, idempotency_key: str = "") -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        clean_batch = str(batch_id or "").strip()
        if not clean_batch:
            raise StableHubWorkerSessionError("batch_id is required.")
        with self._lock:
            data = self.store.load()
            batch = data.setdefault("payout_settlements", {}).get(clean_batch)
            if not isinstance(batch, dict):
                raise StableHubWorkerSessionError("worker settlement batch does not exist.")
            if str(batch.get("worker_id") or "") != worker_id:
                raise StableHubWorkerSessionError("worker settlement batch is not bridgeable by this worker.")
            if str(batch.get("status") or "") != "settled":
                raise StableHubWorkerSessionError("worker settlement batch must be settled before bridge payout.")
            amount_wei = int(batch.get("total_credit_wei_published") or credit_decimal_text_to_wei(str(batch.get("amount", "0"))))
            if amount_wei <= 0:
                raise StableHubWorkerSessionError("worker settlement batch has no bridgeable credits.")
            earning_ids: list[str] = []
            for claim_id in batch.get("claim_ids", []):
                claim = data.setdefault("worker_claims", {}).get(str(claim_id))
                if isinstance(claim, dict):
                    earning_ids.extend([str(item) for item in claim.get("earning_ids", []) if str(item)])
            clean_key = str(idempotency_key or "").strip()
            payouts = data.setdefault("bridge_payouts", {})
            for existing in payouts.values():
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("batch_id") or "") == clean_batch:
                    # A failed or uncertain payout still owns the batch. A new
                    # key must not create a second possible external payment.
                    if str(existing.get("worker_id") or existing.get("worker_node_id") or "") != worker_id:
                        raise StableHubWorkerSessionError("bridge_payout_batch_conflict")
                    if str(existing.get("idempotency_key") or "").strip() != clean_key:
                        raise StableHubWorkerSessionError("bridge_payout_batch_conflict")
                    if int(existing.get("credit_wei") or 0) != amount_wei:
                        raise StableHubWorkerSessionError("bridge_payout_amount_conflict")
                    return dict(existing)
                if (clean_key and str(existing.get("worker_id") or existing.get("worker_node_id") or "") == worker_id
                        and str(existing.get("idempotency_key") or "").strip() == clean_key):
                    raise StableHubWorkerSessionError("bridge_payout_idempotency_conflict")
            payout_id = stable_id(
                "bpayout",
                {
                    "wallet_address": "",
                    "account_id": worker_id,
                    "worker_node_id": worker_id,
                    "earning_ids": earning_ids,
                    "credit_wei": str(amount_wei),
                    "idempotency_key": clean_key,
                },
            )
            existing = payouts.get(payout_id)
            if isinstance(existing, dict):
                if str(existing.get("batch_id") or "") != clean_batch:
                    raise StableHubWorkerSessionError("bridge_payout_idempotency_conflict")
                return dict(existing)
            payout = {
                "bridge_payout_id": payout_id,
                "payout_id": payout_id,
                "wallet_address": "",
                "account_id": worker_id,
                "worker_id": worker_id,
                "worker_node_id": worker_id,
                "batch_id": clean_batch,
                "earning_ids": earning_ids,
                "amount": credit_wei_to_decimal_text(amount_wei),
                "unit": batch.get("unit"),
                "credit_wei": str(amount_wei),
                "credits": credit_wei_to_whole_credits_floor(amount_wei),
                "credits_display": credit_wei_to_decimal_text(amount_wei),
                "status": "requested",
                "bridge_status": "pending",
                "idempotency_key": clean_key,
                "created_at": utc_now(),
                "confirmed_at": "",
                "failed_at": "",
                "metadata": {"stable_hub": True, "exp_status": "pending"},
            }
            data["bridge_payouts"][payout_id] = payout
            self._bridge_audit_event(
                data,
                event_type="bridge.payout.requested",
                worker_id=worker_id,
                amount_wei=amount_wei,
                reference_id=payout_id,
                metadata={"earning_ids": earning_ids, "batch_id": clean_batch},
            )
            self._event(data, "stable.work.payout.bridge_requested", bridge_payout_id=payout_id, payout_id=payout_id, worker_id=worker_id, batch_id=clean_batch, credit_wei=str(amount_wei), amount=payout["amount"])
            self.store.save(data)
            return dict(payout)

    @_atomic_stable_store_mutation
    def confirm_bridge_payout(self, *, bridge_payout_id: str, settlement_reference: str = "") -> dict[str, Any]:
        payout_id = str(bridge_payout_id or "").strip()
        if not payout_id:
            raise StableHubWorkerSessionError("bridge_payout_id is required.")
        with self._lock:
            data = self.store.load()
            payout = data.setdefault("bridge_payouts", {}).get(payout_id)
            if not isinstance(payout, dict):
                raise StableHubWorkerSessionError("bridge payout does not exist.")
            effective_reference = str(settlement_reference or payout_id)
            if payout.get("status") == "confirmed":
                existing_reference = str(payout.get("settlement_reference") or payout_id)
                if existing_reference != effective_reference:
                    raise StableHubWorkerSessionError("bridge_confirmation_reference_conflict")
                return dict(payout)
            # Stable Hub deliberately permits fail -> confirm recovery because the
            # current lab models a retryable bridge rail.  The exp Hub only allows
            # pending -> confirmed; the recovered path records previous_status.
            if str(payout.get("status") or "") not in {"requested", "failed"}:
                raise StableHubWorkerSessionError("bridge payout is not confirmable from its current status.")
            amount_wei = int(payout.get("credit_wei") or 0)
            payout = dict(payout)
            payout["previous_status"] = str(payout.get("status") or "")
            payout["status"] = "confirmed"
            payout["bridge_status"] = "confirmed"
            payout["confirmed_at"] = utc_now()
            payout["settlement_reference"] = effective_reference
            data["bridge_payouts"][payout_id] = payout
            wallet_address = normalize_address(str(payout.get("wallet_address") or ""))
            if wallet_address:
                wallet = data.setdefault("mock_chain_wallets", {}).get(wallet_address)
                if not isinstance(wallet, dict):
                    wallet = {
                        "wallet_address": wallet_address,
                        "available_credit_wei": "0",
                        "created_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                wallet = {
                    **wallet,
                    "available_credit_wei": str(int(wallet.get("available_credit_wei", 0) or 0) + amount_wei),
                    "available_credits_display": credit_wei_to_decimal_text(int(wallet.get("available_credit_wei", 0) or 0) + amount_wei),
                    "updated_at": utc_now(),
                }
                data.setdefault("mock_chain_wallets", {})[wallet_address] = wallet
            for earning_id in [str(item) for item in payout.get("earning_ids", []) if str(item)]:
                earning = data.setdefault("worker_earnings", {}).get(earning_id)
                if isinstance(earning, dict):
                    earning = dict(earning)
                    earning["status"] = "paid"
                    earning["settlement_status"] = "paid"
                    earning["batch_id"] = payout_id
                    data["worker_earnings"][earning_id] = earning
            self._transaction(
                data,
                transaction_type="withdrawal_released",
                account_id=str(payout.get("account_id") or payout.get("worker_id") or payout_id),
                credit_wei=amount_wei,
                worker_id=str(payout.get("worker_id") or ""),
                batch_id=str(payout.get("batch_id") or ""),
                memo=f"stable bridge payout confirmed {payout_id}",
                metadata={"payout_id": payout_id, "settlement_reference": payout.get("settlement_reference")},
            )
            self._bridge_audit_event(
                data,
                event_type="bridge.payout.confirmed",
                wallet_address=wallet_address,
                account_id=str(payout.get("account_id") or ""),
                worker_id=str(payout.get("worker_id") or ""),
                amount_wei=amount_wei,
                reference_id=payout_id,
                metadata={"settlement_reference": payout.get("settlement_reference")},
            )
            self._event(data, "stable.work.payout.bridge_confirmed", bridge_payout_id=payout_id, payout_id=payout_id, worker_id=payout.get("worker_id"), batch_id=payout.get("batch_id"), credit_wei=str(amount_wei), amount=credit_wei_to_decimal_text(amount_wei))
            self.store.save(data)
            return dict(payout)

    @_atomic_stable_store_mutation
    def fail_bridge_payout(self, *, bridge_payout_id: str, reason: str = "") -> dict[str, Any]:
        payout_id = str(bridge_payout_id or "").strip()
        if not payout_id:
            raise StableHubWorkerSessionError("bridge_payout_id is required.")
        with self._lock:
            data = self.store.load()
            payout = data.setdefault("bridge_payouts", {}).get(payout_id)
            if not isinstance(payout, dict):
                raise StableHubWorkerSessionError("bridge payout does not exist.")
            payout = dict(payout)
            if payout.get("status") == "confirmed":
                return dict(payout)
            if payout.get("status") == "failed":
                return dict(payout)
            if str(payout.get("status") or "") != "requested":
                raise StableHubWorkerSessionError("bridge payout is not failable from its current status.")
            amount_wei = int(payout.get("credit_wei") or 0)
            payout["previous_status"] = str(payout.get("status") or "")
            payout["status"] = "failed"
            payout["bridge_status"] = "failed"
            payout["failed_at"] = utc_now()
            payout["failure_reason"] = str(reason)
            data["bridge_payouts"][payout_id] = payout
            self._bridge_audit_event(
                data,
                event_type="bridge.payout.failed",
                worker_id=str(payout.get("worker_id") or ""),
                amount_wei=amount_wei,
                reference_id=payout_id,
                metadata={"reason": reason},
            )
            self._event(data, "stable.work.payout.bridge_failed", bridge_payout_id=payout_id, payout_id=payout_id, worker_id=payout.get("worker_id"), reason=str(reason), credit_wei=str(amount_wei), amount=credit_wei_to_decimal_text(amount_wei))
            self.store.save(data)
            return dict(payout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            data = self.store.load()
            accounts = list(data.get("payout_accounts", {}).values())
            holds = list(data.get("payout_holds", {}).values())
            charges = list(data.get("payout_charges", {}).values())
            earnings = list(data.get("worker_earnings", {}).values())
            claims = list(data.get("worker_claims", {}).values())
            settlements = list(data.get("payout_settlements", {}).values())
            bridge_payouts = list(data.get("bridge_payouts", {}).values())
            transactions = list(data.get("payout_transactions", {}).values())
            deposits = list(data.get("payout_deposits", {}).values())
            bridge_audit = list(data.get("bridge_audit", {}).values())
            wallets = list(data.get("mock_chain_wallets", {}).values())
            # Report actual committed reservations. Previously this read-only
            # projection hid held funds and relabeled live holds as cancelled,
            # which concealed pending payment obligations from lab diagnostics.
            total_available = sum(
                int(account.get("available_credit_wei", 0) or 0)
                for account in accounts if isinstance(account, dict)
            )
            total_held = sum(
                int(account.get("held_credit_wei", 0) or 0)
                for account in accounts if isinstance(account, dict)
            )
            total_spent = sum(int(account.get("spent_credit_wei", 0) or 0) for account in accounts if isinstance(account, dict))
            total_earned = sum(int(item.get("earned_credit_wei", 0) or 0) for item in earnings if isinstance(item, dict))
            return {
                "ok": True,
                "ledger_version": "stable-hub-exp-compatible-credit-ledger-v1",
                "exp_compatible_golden_path": True,
                "credit_unit": "compute_credit",
                "accounts": accounts,
                "holds": holds,
                "charges": charges,
                "worker_earnings": earnings,
                "worker_claims": claims,
                "settlements": settlements,
                "bridge_payouts": bridge_payouts,
                "transactions": transactions,
                "deposits": deposits,
                "bridge_audit": bridge_audit,
                "mock_chain_wallets": wallets,
                "events": list(data.get("payout_events", [])),
                "totals": {
                    "account_count": len(accounts),
                    "hold_count": len(holds),
                    "charge_count": len(charges),
                    "worker_earning_count": len(earnings),
                    "transaction_count": len(transactions),
                    "bridge_audit_count": len(bridge_audit),
                    "total_available_credit_wei": str(total_available),
                    "total_held_credit_wei": str(total_held),
                    "total_spent_credit_wei": str(total_spent),
                    "total_worker_earned_credit_wei": str(total_earned),
                    "total_available_credits_display": credit_wei_to_decimal_text(total_available),
                    "total_held_credits_display": credit_wei_to_decimal_text(total_held),
                    "total_spent_credits_display": credit_wei_to_decimal_text(total_spent),
                    "total_worker_earned_credits_display": credit_wei_to_decimal_text(total_earned),
                },
            }



class StableHubWorkerMarketDirectory:
    """Shared stable Hub market directory for live worker selection.

    Worker socket ownership stays local to the concrete Hub. This directory stores
    the market facts that make a live worker selectable by a requester: ring,
    price, capabilities, capacity, and current owner metadata. It deliberately
    carries only directory/market state, not live work messages.
    """

    def __init__(self, *, topology: Any, hub_id: str, store: StableHubWorkerSessionStore) -> None:
        self.topology = topology
        self.hub = topology.hub_by_id(hub_id)
        self.store = store
        self._lock = _shared_store_lock(store)

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            record = data.get("market_workers", {}).get(worker_id)
            return dict(record) if isinstance(record, dict) else None

    def list_workers(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self.store.load()
            records = [
                dict(record)
                for record in data.get("market_workers", {}).values()
                if isinstance(record, dict)
            ]
        return sorted(records, key=lambda record: str(record.get("worker_id") or ""))

    @_atomic_stable_store_mutation
    def record_worker_live(
        self,
        *,
        worker_id: str,
        owner: dict[str, Any],
        market_profile: dict[str, Any] | None,
        worker_msk_id: str,
        worker_wallet_address: str,
        worker_account_id: str,
        force_active_sessions: bool = False,
    ) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        profile = normalize_worker_market_profile(market_profile)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            previous = records.get(worker_id)
            active_sessions = int(profile.get("active_sessions") or 0)
            owner_connection_id = str(owner.get("connection_id") or "")
            if (
                not force_active_sessions
                and isinstance(previous, dict)
                and previous.get("status") == "live"
                and str(previous.get("connection_id") or "") == owner_connection_id
            ):
                active_sessions = min(
                    int(previous.get("active_sessions") or active_sessions),
                    int(profile.get("max_concurrency") or 1),
                )
            record = {
                "worker_id": worker_id,
                "status": "live",
                "rings": list(profile["rings"]),
                "partitions": list(profile["partitions"]),
                "capabilities": list(profile["capabilities"]),
                "price": dict(profile["price"]),
                "max_concurrency": int(profile["max_concurrency"]),
                "active_sessions": active_sessions,
                "owner_hub_id": str(owner.get("owner_hub_id") or self.hub.hub_id),
                "owner_hub_url": str(owner.get("owner_hub_url") or self.hub.hub_url),
                "connection_id": owner_connection_id,
                "lease_epoch": int(owner.get("lease_epoch") or 0),
                "worker_msk_id": str(worker_msk_id),
                "worker_account_id": str(worker_account_id),
                "worker_wallet_address": str(worker_wallet_address),
                "cluster_id": self.cluster_id,
                "updated_at": now,
                "connected_at": str(owner.get("connected_at") or now),
                "closed_at": "",
            }
            records[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_worker_closed(
        self,
        *,
        worker_id: str,
        connection_id: str,
        reason: str = "socket_closed",
    ) -> dict[str, Any] | None:
        worker_id = normalize_worker_id(worker_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return None
            if str(record.get("connection_id") or "") != str(connection_id):
                return dict(record)
            if str(record.get("owner_hub_id") or "") != self.hub.hub_id:
                return dict(record)
            record = dict(record)
            record["status"] = "closed"
            record["closed_at"] = now
            record["close_reason"] = str(reason)
            records[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def reserve_worker_capacity(
        self,
        *,
        worker_id: str,
        connection_id: str,
        lease_epoch: int | None = None,
    ) -> dict[str, Any]:
        """Atomically reserve one live worker capacity slot before an offer is sent.

        The previous flow selected a worker using a read-only scan and only
        incremented active_sessions after worker acceptance.  Concurrent requesters
        could therefore all observe the same free slot and send multiple offers to
        a max_concurrency=1 worker.  This Stable-native reservation is intentionally
        tied to the selected connection_id and lease_epoch so stale handoff/owner
        observations cannot consume capacity on a newer worker connection.
        """

        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            record = records.get(worker_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("worker_not_live")
            if str(record.get("status") or "") != "live":
                raise StableHubWorkerSessionError("worker_not_live")
            if str(record.get("connection_id") or "") != str(connection_id):
                raise StableHubWorkerSessionError("worker_owner_changed")
            if lease_epoch is not None and int(record.get("lease_epoch") or 0) != int(lease_epoch):
                raise StableHubWorkerSessionError("worker_owner_changed")
            max_concurrency = int(record.get("max_concurrency") or 1)
            active_sessions = int(record.get("active_sessions") or 0)
            if active_sessions >= max_concurrency:
                raise StableHubWorkerSessionError("worker_capacity_unavailable")
            record = dict(record)
            record["active_sessions"] = active_sessions + 1
            record["updated_at"] = utc_now()
            records[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_session_accepted(
        self,
        *,
        worker_id: str,
        connection_id: str,
    ) -> dict[str, Any] | None:
        """Increment a live worker's active session count after socket acceptance."""

        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return None
            if str(record.get("status") or "") != "live":
                return dict(record)
            if str(record.get("connection_id") or "") != str(connection_id):
                return dict(record)
            record = dict(record)
            max_concurrency = int(record.get("max_concurrency") or 1)
            active_sessions = int(record.get("active_sessions") or 0)
            record["active_sessions"] = min(active_sessions + 1, max_concurrency)
            record["updated_at"] = utc_now()
            records[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_session_finished(
        self,
        *,
        worker_id: str,
        connection_id: str,
    ) -> dict[str, Any] | None:
        """Decrement a live worker's active session count after terminal work state."""

        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            record = records.get(worker_id)
            if not isinstance(record, dict):
                return None
            if str(record.get("connection_id") or "") != str(connection_id):
                return dict(record)
            record = dict(record)
            active_sessions = int(record.get("active_sessions") or 0)
            record["active_sessions"] = max(0, active_sessions - 1)
            record["updated_at"] = utc_now()
            records[worker_id] = record
            self.store.save(data)
            return dict(record)

    def select_worker_for_work(self, work: Any) -> dict[str, Any] | None:
        constraints = normalize_request_market_constraints(work)
        required_ring = str(constraints["ring"])
        required_capabilities = set(str(value) for value in constraints["capabilities"])
        max_price = constraints.get("max_price")
        candidates: list[dict[str, Any]] = []
        for record in self.list_workers():
            if record.get("status") != "live":
                continue
            if required_ring not in [str(value) for value in record.get("rings", [])]:
                continue
            worker_capabilities = set(str(value) for value in record.get("capabilities", []))
            if not required_capabilities.issubset(worker_capabilities):
                continue
            max_concurrency = int(record.get("max_concurrency") or 1)
            active_sessions = int(record.get("active_sessions") or 0)
            if active_sessions >= max_concurrency:
                continue
            worker_price = dict(record.get("price") or normalize_price(None))
            if max_price is not None:
                max_price = dict(max_price)
                if str(worker_price.get("unit") or "credit") != str(max_price.get("unit") or "credit"):
                    continue
                if _price_decimal(worker_price) > _price_decimal(max_price):
                    continue
            candidate = dict(record)
            candidate["partition"] = required_ring
            candidate["selection"] = {
                "mode": "deterministic-price-worker-id",
                "partition": required_ring,
                "required_capabilities": sorted(required_capabilities),
            }
            candidates.append(candidate)

        if not candidates:
            return None

        candidates.sort(
            key=lambda record: (
                str((record.get("price") or {}).get("unit") or "credit"),
                _price_decimal(dict(record.get("price") or normalize_price(None))),
                str(record.get("worker_id") or ""),
            )
        )
        return dict(candidates[0])




class StableHubAcceptedWorkSessionDirectory:
    """Shared durable records for work sessions accepted by a stable owner Hub.

    Worker ownership and market selectability answer where live workers are.
    Accepted work sessions answer which requester work was actually accepted by a
    worker over that owner's live connection. Temporal lifecycle execution begins
    after acceptance; this directory records the durable Temporal identity/path
    alongside the accepted session facts.
    """

    def __init__(self, *, topology: Any, hub_id: str, store: StableHubWorkerSessionStore) -> None:
        self.topology = topology
        self.hub = topology.hub_by_id(hub_id)
        self.store = store
        self._lock = _shared_store_lock(store)

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = normalize_session_id(session_id)
        with self._lock:
            data = self.store.load()
            record = data.get("accepted_sessions", {}).get(session_id)
            return dict(record) if isinstance(record, dict) else None

    @_atomic_stable_store_mutation
    def get_session_for_request(self, *, requester_account_id: str, request_id: str) -> dict[str, Any] | None:
        clean_account = str(requester_account_id or "").strip()
        if not clean_account:
            return None
        request_id = normalize_request_id(request_id)
        index_key = _stable_digest_id("request-index", self.cluster_id, clean_account, request_id)
        with self._lock:
            data = self.store.load()
            indexed_session_id = str(data.get("work_request_index", {}).get(index_key) or "")
            if indexed_session_id:
                indexed_record = data.get("accepted_sessions", {}).get(indexed_session_id)
                if isinstance(indexed_record, dict):
                    return dict(indexed_record)
            matches = [
                dict(record)
                for record in data.get("accepted_sessions", {}).values()
                if isinstance(record, dict)
                and str(record.get("requester_account_id") or "") == clean_account
                and str(record.get("request_id") or "") == request_id
            ]
            if not matches:
                return None
            matches.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
            winner = matches[0]
            data.setdefault("work_request_index", {})[index_key] = str(winner.get("session_id") or "")
            self.store.save(data)
            return winner

    def list_open_sessions_for_worker_connection(
        self,
        *,
        worker_id: str,
        connection_id: str | None = None,
        exclude_connection_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return non-terminal accepted/running sessions for a worker connection.

        Live-session work is tied to an exact websocket connection generation.  A
        reconnect must not leave older accepted sessions invisible forever, and a
        closed socket must not keep capacity busy after the worker is gone.
        """

        clean_worker_id = str(worker_id or "").strip()
        if clean_worker_id:
            clean_worker_id = normalize_worker_id(clean_worker_id)
        clean_connection_id = str(connection_id or "").strip()
        clean_exclude_connection_id = str(exclude_connection_id or "").strip()
        with self._lock:
            data = self.store.load()
            matches = []
            for record in data.get("accepted_sessions", {}).values():
                if not isinstance(record, dict):
                    continue
                if clean_worker_id and str(record.get("worker_id") or "") != clean_worker_id:
                    continue
                if str(record.get("status") or "") not in {"accepted", "running"}:
                    continue
                record_connection_id = str(record.get("worker_connection_id") or "")
                if clean_connection_id and record_connection_id != clean_connection_id:
                    continue
                if clean_exclude_connection_id and record_connection_id == clean_exclude_connection_id:
                    continue
                matches.append(dict(record))
        matches.sort(key=lambda record: str(record.get("created_at") or ""))
        return matches

    @_atomic_stable_store_mutation
    def record_accepted(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        requester_msk_id: str,
        requester_account_id: str,
        requester_wallet_address: str,
        worker_id: str,
        worker_connection_id: str,
        owner_hub_id: str,
        owner_hub_url: str,
        partition: str,
        task_queue: str,
        work: dict[str, Any],
        worker_acceptance: dict[str, Any],
        payout: dict[str, Any] | None = None,
        temporal_intent: bool = False,
    ) -> dict[str, Any]:
        session_id = normalize_session_id(session_id)
        request_id = normalize_request_id(request_id)
        worker_id = normalize_worker_id(worker_id)
        now = utc_now()
        execution = stable_temporal_execution_metadata(
            self.topology,
            session_id=session_id,
            task_queue=str(task_queue),
        )
        if temporal_intent:
            execution["temporal_mode"] = "enabled"
        record = {
            "session_id": session_id,
            "run_id": str(run_id),
            "request_id": request_id,
            "requester_msk_id": str(requester_msk_id),
            "requester_account_id": str(requester_account_id),
            "requester_wallet_address": str(requester_wallet_address),
            "worker_id": worker_id,
            "worker_connection_id": str(worker_connection_id),
            "owner_hub_id": str(owner_hub_id),
            "owner_hub_url": str(owner_hub_url),
            "partition": str(partition),
            "task_queue": str(task_queue),
            "status": "accepted",
            "created_at": now,
            "accepted_at": now,
            "cluster_id": self.cluster_id,
            "execution": execution,
            "work": json.loads(json.dumps(work)),
            "worker_acceptance": json.loads(json.dumps(worker_acceptance)),
            "payout": json.loads(json.dumps(payout or {})),
            "stream_events": [
                {
                    "type": "accepted",
                    "seq": 0,
                    "session_id": session_id,
                    "request_id": request_id,
                    "run_id": str(run_id),
                    "status": "accepted",
                    "hub_received_at": now,
                    "created_at": now,
                }
            ],
            "completed_at": "",
            "failed_at": "",
        }
        index_key = _stable_digest_id("request-index", self.cluster_id, str(requester_account_id), request_id)
        with self._lock:
            data = self.store.load()
            sessions = data.setdefault("accepted_sessions", {})
            request_index = data.setdefault("work_request_index", {})
            existing_session_id = str(request_index.get(index_key) or "")
            if existing_session_id and existing_session_id != session_id:
                existing = sessions.get(existing_session_id)
                if isinstance(existing, dict):
                    if json.loads(json.dumps(existing.get("work", {}))) != json.loads(json.dumps(work)):
                        raise StableHubWorkerSessionError("duplicate_request_id_work_mismatch")
                    return dict(existing)
                raise StableHubWorkerSessionError("duplicate_request_id_index_points_to_missing_session")
            existing_record = sessions.get(session_id)
            if isinstance(existing_record, dict):
                return dict(existing_record)
            sessions[session_id] = record
            request_index[index_key] = session_id
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def append_stream_event(self, session_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Append a requester-visible session stream event.

        The accepted-session record is the owner Hub's durable-ish replay buffer for
        exp live-session streaming.  Events stored here must be session/request
        scoped and must not rely on worker ids, socket ids, or wallet addresses as
        public routing facts.
        """

        session_id = normalize_session_id(session_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            sessions = data.setdefault("accepted_sessions", {})
            record = sessions.get(session_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("accepted work session does not exist.")
            events = record.get("stream_events")
            if not isinstance(events, list):
                events = []
            next_seq = 0
            for existing in events:
                if not isinstance(existing, dict):
                    continue
                try:
                    next_seq = max(next_seq, int(existing.get("seq") or 0) + 1)
                except (TypeError, ValueError):
                    continue
            clean_event = json.loads(json.dumps(dict(event), default=str))
            clean_event["seq"] = next_seq
            clean_event["session_id"] = session_id
            clean_event.setdefault("request_id", str(record.get("request_id") or ""))
            clean_event.setdefault("run_id", str(record.get("run_id") or ""))
            clean_event.setdefault("hub_received_at", now)
            clean_event.setdefault("created_at", now)
            if not clean_event.get("type"):
                clean_event["type"] = "event"
            record = dict(record)
            record["stream_events"] = [*events, clean_event]
            sessions[session_id] = record
            self.store.save(data)
            return dict(clean_event)

    def list_stream_events(self, session_id: str, *, after_seq: int = -1) -> list[dict[str, Any]]:
        session_id = normalize_session_id(session_id)
        with self._lock:
            data = self.store.load()
            record = data.get("accepted_sessions", {}).get(session_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("accepted work session does not exist.")
            events = record.get("stream_events")
            if not isinstance(events, list):
                return []
            result: list[dict[str, Any]] = []
            for event in events:
                if not isinstance(event, dict):
                    continue
                try:
                    seq = int(event.get("seq") or 0)
                except (TypeError, ValueError):
                    seq = 0
                if seq > int(after_seq):
                    result.append(dict(event))
            return result

    def _append_terminal_stream_event_unlocked(self, record: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        events = record.get("stream_events")
        if not isinstance(events, list):
            events = []
        next_seq = 0
        for existing in events:
            if not isinstance(existing, dict):
                continue
            try:
                next_seq = max(next_seq, int(existing.get("seq") or 0) + 1)
            except (TypeError, ValueError):
                continue
        clean_event = json.loads(json.dumps(dict(event), default=str))
        clean_event["seq"] = next_seq
        clean_event.setdefault("session_id", str(record.get("session_id") or ""))
        clean_event.setdefault("request_id", str(record.get("request_id") or ""))
        clean_event.setdefault("run_id", str(record.get("run_id") or ""))
        clean_event.setdefault("hub_received_at", utc_now())
        clean_event.setdefault("created_at", clean_event.get("hub_received_at") or utc_now())
        record["stream_events"] = [*events, clean_event]
        return clean_event

    @_atomic_stable_store_mutation
    def record_succeeded(
        self,
        *,
        session_id: str,
        worker_connection_id: str,
        worker_result: dict[str, Any],
        payout: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = normalize_session_id(session_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            sessions = data.setdefault("accepted_sessions", {})
            record = sessions.get(session_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("accepted work session does not exist.")
            if str(record.get("worker_connection_id") or "") != str(worker_connection_id):
                raise StableHubWorkerSessionError("worker result connection_id does not match accepted session.")
            if str(record.get("status") or "") == "succeeded":
                return dict(record)
            if str(record.get("status") or "") not in {"accepted", "running"}:
                raise StableHubWorkerSessionError("accepted work session is already terminal.")
            record = dict(record)
            record["status"] = "succeeded"
            record["completed_at"] = now
            record["worker_result"] = json.loads(json.dumps(worker_result))
            record["payout"] = json.loads(json.dumps(payout))
            result_payload = worker_result.get("result") if isinstance(worker_result.get("result"), dict) else {}
            response_payload = result_payload.get("response") if isinstance(result_payload.get("response"), dict) else {}
            terminal_event: dict[str, Any] = {
                "type": "result",
                "status": "succeeded",
                "content": str(response_payload.get("content") or ""),
                "response": {
                    key: value
                    for key, value in response_payload.items()
                    if key in {"content", "provider", "model", "role"}
                },
            }
            self._append_terminal_stream_event_unlocked(record, terminal_event)
            execution = dict(record.get("execution") or {})
            execution["status"] = "succeeded"
            record["execution"] = execution
            sessions[session_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_failed(
        self,
        *,
        session_id: str,
        worker_connection_id: str,
        worker_failure: dict[str, Any],
        payout: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = normalize_session_id(session_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            sessions = data.setdefault("accepted_sessions", {})
            record = sessions.get(session_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("accepted work session does not exist.")
            if str(record.get("worker_connection_id") or "") != str(worker_connection_id):
                raise StableHubWorkerSessionError("worker failure connection_id does not match accepted session.")
            if str(record.get("status") or "") in {"succeeded", "failed", "cancelled"}:
                return dict(record)
            record = dict(record)
            record["status"] = "failed"
            record["failed_at"] = now
            record["worker_failure"] = json.loads(json.dumps(worker_failure))
            record["payout"] = json.loads(json.dumps(payout))
            error_text = str(worker_failure.get("error") or worker_failure.get("message") or "worker_failed")
            self._append_terminal_stream_event_unlocked(
                record,
                {
                    "type": "failed",
                    "status": "failed",
                    "error": error_text,
                },
            )
            execution = dict(record.get("execution") or {})
            execution["status"] = "failed"
            record["execution"] = execution
            sessions[session_id] = record
            self.store.save(data)
            return dict(record)


class StableHubWorkerSessionDirectory:
    """FDB/shared owner directory for live worker connections.

    It records where the real long-lived connection lives. The socket/stream
    itself remains owned by the concrete Hub process that accepted it.
    """

    def __init__(self, *, topology: Any, hub_id: str, store: StableHubWorkerSessionStore) -> None:
        self.topology = topology
        self.hub = topology.hub_by_id(hub_id)
        self.store = store
        self._lock = _shared_store_lock(store)

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def get_owner(self, worker_id: str) -> dict[str, Any] | None:
        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            record = data.get("workers", {}).get(worker_id)
            return dict(record) if isinstance(record, dict) else None

    @_atomic_stable_store_mutation
    def record_connected(
        self,
        *,
        worker_id: str,
        connection_id: str,
        multisession_key_id: str,
        wallet_address: str,
        account_id: str,
    ) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            workers = data.setdefault("workers", {})
            previous = workers.get(worker_id)
            previous_epoch = int(previous.get("lease_epoch") or 0) if isinstance(previous, dict) else 0
            record = {
                "worker_id": worker_id,
                "status": "live",
                "owner_hub_id": self.hub.hub_id,
                "owner_hub_url": self.hub.hub_url,
                "connection_id": connection_id,
                "multisession_key_id": str(multisession_key_id),
                "wallet_address": str(wallet_address),
                "account_id": str(account_id),
                "cluster_id": self.cluster_id,
                "connected_at": now,
                "last_pong_at": "",
                "closed_at": "",
                "lease_epoch": previous_epoch + 1,
            }
            workers[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_pong(self, *, worker_id: str, connection_id: str) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            workers = data.setdefault("workers", {})
            record = workers.get(worker_id)
            if not isinstance(record, dict):
                raise StableHubWorkerSessionError("worker owner record does not exist.")
            if record.get("connection_id") != connection_id:
                raise StableHubWorkerSessionError("worker connection_id does not match owner record.")
            if record.get("owner_hub_id") != self.hub.hub_id:
                raise StableHubWorkerSessionError("worker owner record belongs to a different Hub.")
            record = dict(record)
            record["status"] = "live"
            record["last_pong_at"] = now
            workers[worker_id] = record
            self.store.save(data)
            return dict(record)

    @_atomic_stable_store_mutation
    def record_closed(self, *, worker_id: str, connection_id: str, reason: str = "socket_closed") -> dict[str, Any] | None:
        worker_id = normalize_worker_id(worker_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            workers = data.setdefault("workers", {})
            record = workers.get(worker_id)
            if not isinstance(record, dict):
                return None
            if record.get("connection_id") != connection_id:
                return dict(record)
            if record.get("owner_hub_id") != self.hub.hub_id:
                return dict(record)
            record = dict(record)
            record["status"] = "closed"
            record["closed_at"] = now
            record["close_reason"] = str(reason)
            workers[worker_id] = record
            self.store.save(data)
            return dict(record)
