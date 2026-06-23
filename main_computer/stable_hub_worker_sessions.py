from __future__ import annotations

import hashlib
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


STABLE_WORKER_SESSION_STORE_VERSION = "main-computer-stable-hub-worker-live-sessions-v1"
STABLE_WORKER_SESSION_FDB_TRANSACTION_TIMEOUT_MS = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_TIMEOUT_MS", "5000"))
STABLE_WORKER_SESSION_FDB_RETRY_LIMIT = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_RETRY_LIMIT", "1"))
STABLE_HUB_DEV_ACCOUNT_CREDITS = Decimal(os.environ.get("MAIN_COMPUTER_STABLE_HUB_DEV_ACCOUNT_CREDITS", "1000"))


class StableHubWorkerSessionError(ValueError):
    """Raised when a stable Hub worker live-session request is malformed."""


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
    os.environ["PATH"] = native_dir + os.pathsep + os.environ.get("PATH", "")
    if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(native_dir)
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

    def _key(self) -> bytes:
        self._open()
        return self._fdb.tuple.pack((self.namespace, "stable-hub", "worker_live_sessions"))

    def load(self) -> dict[str, Any]:
        self._open()
        key = self._key()

        @self._fdb.transactional
        def _tx(tr: Any) -> dict[str, Any]:
            _configure_fdb_transaction_safety(tr)
            return _normalize_store(_json_loads(tr[key].wait()) or {})

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
        key = self._key()
        clean = _normalize_store(data)

        @self._fdb.transactional
        def _tx(tr: Any) -> None:
            _configure_fdb_transaction_safety(tr)
            tr[key] = _json_dumps(clean)

        try:
            _tx(self._db)
        except Exception as exc:  # pragma: no cover - depends on live FDB timing/state
            raise RuntimeError(
                "FoundationDB worker live-session store save failed or timed out. "
                f"cluster_file={self.cluster_file} namespace={self.namespace} "
                f"timeout_ms={STABLE_WORKER_SESSION_FDB_TRANSACTION_TIMEOUT_MS}"
            ) from exc


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
    """Stable Hub credit lifecycle adapter for accepted work sessions.

    This intentionally mirrors the exp Hub credit lifecycle in stable Hub names:
    create a requester hold before offering work, charge that hold only after a
    worker result, record worker earnings from charges, and release holds on
    timeout/failure. The shared store carries durable accounting metadata and
    audit events; it never carries live worker messages.
    """

    def __init__(self, *, topology: Any, hub_id: str, store: StableHubWorkerSessionStore) -> None:
        self.topology = topology
        self.hub = topology.hub_by_id(hub_id)
        self.store = store
        self._lock = threading.Lock()

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def _event(self, data: dict[str, Any], event_type: str, **fields: Any) -> dict[str, Any]:
        event = {
            "type": event_type,
            "event_id": _stable_digest_id("evt", event_type, fields.get("session_id"), fields.get("hold_id"), fields.get("at") or utc_now()),
            "hub_id": self.hub.hub_id,
            "cluster_id": self.cluster_id,
            "created_at": utc_now(),
        }
        event.update({key: value for key, value in fields.items() if value is not None})
        data.setdefault("payout_events", []).append(event)
        return event

    def _amount(self, value: Any, *, field_name: str) -> Decimal:
        return _decimal_from_value(value, field_name=field_name)

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
            return dict(account)
        initial = STABLE_HUB_DEV_ACCOUNT_CREDITS if default_credits is None else default_credits
        if initial < Decimal("0"):
            raise StableHubWorkerSessionError("initial payout account credits must be zero or greater.")
        account = {
            "account_id": clean_account,
            "wallet_address": str(wallet_address or ""),
            "available_credits": _decimal_to_string(initial),
            "held_credits": "0",
            "spent_credits": "0",
            "earned_credits": "0",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "cluster_id": self.cluster_id,
        }
        accounts[clean_account] = account
        self._event(data, "stable.work.payout.account_created", account_id=clean_account)
        return dict(account)

    def fund_account(
        self,
        *,
        account_id: str,
        wallet_address: str = "",
        credits: Any,
        replace: bool = False,
    ) -> dict[str, Any]:
        amount = self._amount(credits, field_name="credits")
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
            available = self._amount(account.get("available_credits", "0"), field_name="available_credits")
            account["available_credits"] = _decimal_to_string(amount if replace else available + amount)
            if wallet_address:
                account["wallet_address"] = str(wallet_address)
            account["updated_at"] = utc_now()
            data.setdefault("payout_accounts", {})[clean_account] = account
            self._event(
                data,
                "stable.work.payout.account_funded",
                account_id=clean_account,
                credits=account["available_credits"],
                replace=replace,
            )
            self.store.save(data)
            return dict(account)

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        clean_account = str(account_id or "").strip()
        if not clean_account:
            return None
        with self._lock:
            data = self.store.load()
            account = data.get("payout_accounts", {}).get(clean_account)
            return dict(account) if isinstance(account, dict) else None

    def create_hold(
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
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = normalize_request_id(request_id)
        session_id = normalize_session_id(session_id)
        worker_id = normalize_worker_id(worker_id)
        price = normalize_price(selected_price, field_name="selected worker price")
        amount = self._amount(price.get("amount"), field_name="selected worker price.amount")
        max_price = normalize_price(requester_max_price, field_name="requester max_price") if requester_max_price else None
        if max_price is not None:
            if str(max_price.get("unit")) != str(price.get("unit")):
                raise StableHubWorkerSessionError("selected worker price unit does not match requester max_price unit.")
            if amount > self._amount(max_price.get("amount"), field_name="requester max_price.amount"):
                raise StableHubWorkerSessionError("selected worker price exceeds requester max_price.")
        hold_id = _stable_digest_id("hold", self.cluster_id, str(account_id), request_id)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            holds = data.setdefault("payout_holds", {})
            existing = holds.get(hold_id)
            if isinstance(existing, dict):
                return dict(existing)
            account = self._ensure_account(data, account_id=str(account_id), wallet_address=wallet_address)
            available = self._amount(account.get("available_credits", "0"), field_name="available_credits")
            held = self._amount(account.get("held_credits", "0"), field_name="held_credits")
            if available < amount:
                self._event(
                    data,
                    "stable.work.payout.hold_rejected",
                    hold_id=hold_id,
                    session_id=session_id,
                    request_id=request_id,
                    account_id=str(account_id),
                    worker_id=worker_id,
                    reason="insufficient_requester_credits",
                )
                self.store.save(data)
                raise StableHubWorkerSessionError("requester_funds_unavailable")
            account["available_credits"] = _decimal_to_string(available - amount)
            account["held_credits"] = _decimal_to_string(held + amount)
            account["updated_at"] = now
            data.setdefault("payout_accounts", {})[str(account_id)] = account
            hold = {
                "hold_id": hold_id,
                "status": "held",
                "account_id": str(account_id),
                "wallet_address": str(wallet_address or ""),
                "request_id": request_id,
                "session_id": session_id,
                "run_id": str(run_id),
                "worker_id": worker_id,
                "partition": str(partition),
                "amount": _decimal_to_string(amount),
                "unit": str(price.get("unit") or "credit"),
                "selected_price": dict(price),
                "requester_max_price": dict(max_price) if max_price else {},
                "created_at": now,
                "updated_at": now,
                "metadata": json.loads(json.dumps(metadata or {})),
            }
            holds[hold_id] = hold
            self._event(
                data,
                "stable.work.payout.hold_created",
                hold_id=hold_id,
                session_id=session_id,
                request_id=request_id,
                account_id=str(account_id),
                worker_id=worker_id,
                amount=hold["amount"],
                unit=hold["unit"],
            )
            self.store.save(data)
            return dict(hold)

    def release_hold(
        self,
        *,
        hold_id: str,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        if not clean_hold:
            raise StableHubWorkerSessionError("hold_id is required.")
        with self._lock:
            data = self.store.load()
            holds = data.setdefault("payout_holds", {})
            hold = holds.get(clean_hold)
            if not isinstance(hold, dict):
                raise StableHubWorkerSessionError("payout hold does not exist.")
            if hold.get("status") != "held":
                return dict(hold)
            account = self._ensure_account(data, account_id=str(hold.get("account_id") or ""), wallet_address=str(hold.get("wallet_address") or ""), default_credits=Decimal("0"))
            amount = self._amount(hold.get("amount", "0"), field_name="hold.amount")
            available = self._amount(account.get("available_credits", "0"), field_name="available_credits")
            held = self._amount(account.get("held_credits", "0"), field_name="held_credits")
            account["available_credits"] = _decimal_to_string(available + amount)
            account["held_credits"] = _decimal_to_string(max(Decimal("0"), held - amount))
            account["updated_at"] = utc_now()
            hold = dict(hold)
            hold["status"] = "released"
            hold["release_reason"] = str(reason)
            hold["released_at"] = utc_now()
            hold["updated_at"] = hold["released_at"]
            hold["release_metadata"] = json.loads(json.dumps(metadata or {}))
            data.setdefault("payout_accounts", {})[account["account_id"]] = account
            holds[clean_hold] = hold
            self._event(
                data,
                "stable.work.payout.hold_released",
                hold_id=clean_hold,
                session_id=hold.get("session_id"),
                request_id=hold.get("request_id"),
                account_id=hold.get("account_id"),
                worker_id=hold.get("worker_id"),
                reason=str(reason),
            )
            self.store.save(data)
            return dict(hold)

    def charge_hold(
        self,
        *,
        hold_id: str,
        session_id: str,
        request_id: str,
        worker_id: str,
        result: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_hold = str(hold_id or "").strip()
        if not clean_hold:
            raise StableHubWorkerSessionError("hold_id is required.")
        session_id = normalize_session_id(session_id)
        request_id = normalize_request_id(request_id)
        worker_id = normalize_worker_id(worker_id)
        charge_id = _stable_digest_id("charge", self.cluster_id, clean_hold, session_id)
        earning_id = _stable_digest_id("earn", self.cluster_id, charge_id, worker_id)
        with self._lock:
            data = self.store.load()
            holds = data.setdefault("payout_holds", {})
            hold = holds.get(clean_hold)
            if not isinstance(hold, dict):
                raise StableHubWorkerSessionError("payout hold does not exist.")
            existing_charge = data.setdefault("payout_charges", {}).get(charge_id)
            existing_earning = data.setdefault("worker_earnings", {}).get(earning_id)
            if isinstance(existing_charge, dict) and isinstance(existing_earning, dict):
                return {
                    "hold": dict(hold),
                    "charge": dict(existing_charge),
                    "worker_earning": dict(existing_earning),
                    "idempotent": True,
                }
            if hold.get("status") != "held":
                raise StableHubWorkerSessionError("payout hold is not held.")
            if str(hold.get("session_id") or "") != session_id:
                raise StableHubWorkerSessionError("payout hold session_id mismatch.")
            if str(hold.get("request_id") or "") != request_id:
                raise StableHubWorkerSessionError("payout hold request_id mismatch.")
            if str(hold.get("worker_id") or "") != worker_id:
                raise StableHubWorkerSessionError("payout hold worker_id mismatch.")
            account = self._ensure_account(data, account_id=str(hold.get("account_id") or ""), wallet_address=str(hold.get("wallet_address") or ""), default_credits=Decimal("0"))
            amount = self._amount(hold.get("amount", "0"), field_name="hold.amount")
            held = self._amount(account.get("held_credits", "0"), field_name="held_credits")
            spent = self._amount(account.get("spent_credits", "0"), field_name="spent_credits")
            account["held_credits"] = _decimal_to_string(max(Decimal("0"), held - amount))
            account["spent_credits"] = _decimal_to_string(spent + amount)
            account["updated_at"] = utc_now()
            hold = dict(hold)
            hold["status"] = "charged"
            hold["charged_at"] = utc_now()
            hold["updated_at"] = hold["charged_at"]
            charge = {
                "charge_id": charge_id,
                "hold_id": clean_hold,
                "session_id": session_id,
                "request_id": request_id,
                "worker_id": worker_id,
                "account_id": hold.get("account_id"),
                "amount": hold.get("amount"),
                "unit": hold.get("unit"),
                "status": "charged",
                "created_at": utc_now(),
                "metadata": json.loads(json.dumps(metadata or {})),
            }
            earning = {
                "earning_id": earning_id,
                "charge_id": charge_id,
                "hold_id": clean_hold,
                "session_id": session_id,
                "request_id": request_id,
                "worker_id": worker_id,
                "amount": hold.get("amount"),
                "unit": hold.get("unit"),
                "status": "earned",
                "claim_status": "unclaimed",
                "settlement_status": "not_settled",
                "created_at": utc_now(),
                "result": json.loads(json.dumps(result)),
            }
            data.setdefault("payout_accounts", {})[account["account_id"]] = account
            holds[clean_hold] = hold
            data.setdefault("payout_charges", {})[charge_id] = charge
            data.setdefault("worker_earnings", {})[earning_id] = earning
            self._event(
                data,
                "stable.work.payout.charged",
                hold_id=clean_hold,
                charge_id=charge_id,
                earning_id=earning_id,
                session_id=session_id,
                request_id=request_id,
                worker_id=worker_id,
                amount=hold.get("amount"),
                unit=hold.get("unit"),
            )
            self.store.save(data)
            return {
                "hold": dict(hold),
                "charge": dict(charge),
                "worker_earning": dict(earning),
                "idempotent": False,
            }

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
                ]
            if key:
                for claim in data.setdefault("worker_claims", {}).values():
                    if isinstance(claim, dict) and claim.get("worker_id") == worker_id and claim.get("idempotency_key") == key:
                        return dict(claim)
            amount = Decimal("0")
            unit = "credit"
            for earning_id in selected_ids:
                earning = earnings.get(earning_id)
                if not isinstance(earning, dict) or earning.get("worker_id") != worker_id:
                    raise StableHubWorkerSessionError("worker earning is not claimable by this worker.")
                if earning.get("claim_status") != "unclaimed":
                    continue
                unit = str(earning.get("unit") or unit)
                amount += self._amount(earning.get("amount", "0"), field_name="earning.amount")
                earning = dict(earning)
                earning["claim_status"] = "claimed"
                earnings[earning_id] = earning
            claim_id = _stable_digest_id("claim", self.cluster_id, worker_id, key or ",".join(sorted(selected_ids)))
            claim = {
                "claim_id": claim_id,
                "worker_id": worker_id,
                "earning_ids": selected_ids,
                "amount": _decimal_to_string(amount),
                "unit": unit,
                "status": "claimed",
                "settlement_status": "not_settled",
                "idempotency_key": key,
                "created_at": utc_now(),
            }
            data.setdefault("worker_claims", {})[claim_id] = claim
            self._event(data, "stable.work.payout.claimed", claim_id=claim_id, worker_id=worker_id, amount=claim["amount"], unit=unit)
            self.store.save(data)
            return dict(claim)

    def create_worker_settlement_batch(self, *, worker_id: str, claim_ids: list[str] | None = None, idempotency_key: str = "") -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        key = str(idempotency_key or "").strip()
        with self._lock:
            data = self.store.load()
            if key:
                for batch in data.setdefault("payout_settlements", {}).values():
                    if isinstance(batch, dict) and batch.get("worker_id") == worker_id and batch.get("idempotency_key") == key:
                        return dict(batch)
            claims = data.setdefault("worker_claims", {})
            selected_ids = [str(item) for item in (claim_ids or []) if str(item)]
            if not selected_ids:
                selected_ids = [
                    cid
                    for cid, claim in claims.items()
                    if isinstance(claim, dict)
                    and claim.get("worker_id") == worker_id
                    and claim.get("settlement_status") == "not_settled"
                ]
            amount = Decimal("0")
            unit = "credit"
            for claim_id in selected_ids:
                claim = claims.get(claim_id)
                if not isinstance(claim, dict) or claim.get("worker_id") != worker_id:
                    raise StableHubWorkerSessionError("worker claim is not settleable by this worker.")
                unit = str(claim.get("unit") or unit)
                amount += self._amount(claim.get("amount", "0"), field_name="claim.amount")
                claim = dict(claim)
                claim["settlement_status"] = "batched"
                claims[claim_id] = claim
            batch_id = _stable_digest_id("settle", self.cluster_id, worker_id, key or ",".join(sorted(selected_ids)))
            batch = {
                "batch_id": batch_id,
                "worker_id": worker_id,
                "claim_ids": selected_ids,
                "amount": _decimal_to_string(amount),
                "unit": unit,
                "status": "batched",
                "idempotency_key": key,
                "created_at": utc_now(),
            }
            data.setdefault("payout_settlements", {})[batch_id] = batch
            self._event(data, "stable.work.payout.settlement_batched", batch_id=batch_id, worker_id=worker_id, amount=batch["amount"], unit=unit)
            self.store.save(data)
            return dict(batch)

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
            if batch.get("status") == "settled":
                return dict(batch)
            batch = dict(batch)
            batch["status"] = "settled"
            batch["settled_at"] = utc_now()
            batch["settlement_reference"] = str(settlement_reference or clean_batch)
            batch["settlement_idempotency_key"] = str(idempotency_key or "")
            batches[clean_batch] = batch
            for claim_id in batch.get("claim_ids", []):
                claim = data.setdefault("worker_claims", {}).get(str(claim_id))
                if isinstance(claim, dict):
                    claim = dict(claim)
                    claim["settlement_status"] = "settled"
                    data["worker_claims"][str(claim_id)] = claim
            self._event(data, "stable.work.payout.settled", batch_id=clean_batch, worker_id=batch.get("worker_id"), amount=batch.get("amount"), unit=batch.get("unit"))
            self.store.save(data)
            return dict(batch)

    def request_bridge_payout(self, *, worker_id: str, batch_id: str, idempotency_key: str = "") -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        clean_batch = str(batch_id or "").strip()
        if not clean_batch:
            raise StableHubWorkerSessionError("batch_id is required.")
        payout_id = _stable_digest_id("bridge", self.cluster_id, worker_id, clean_batch, idempotency_key)
        with self._lock:
            data = self.store.load()
            existing = data.setdefault("bridge_payouts", {}).get(payout_id)
            if isinstance(existing, dict):
                return dict(existing)
            batch = data.setdefault("payout_settlements", {}).get(clean_batch)
            if not isinstance(batch, dict):
                raise StableHubWorkerSessionError("worker settlement batch does not exist.")
            payout = {
                "bridge_payout_id": payout_id,
                "worker_id": worker_id,
                "batch_id": clean_batch,
                "amount": batch.get("amount"),
                "unit": batch.get("unit"),
                "status": "requested",
                "idempotency_key": str(idempotency_key or ""),
                "created_at": utc_now(),
            }
            data["bridge_payouts"][payout_id] = payout
            self._event(data, "stable.work.payout.bridge_requested", bridge_payout_id=payout_id, worker_id=worker_id, batch_id=clean_batch)
            self.store.save(data)
            return dict(payout)

    def confirm_bridge_payout(self, *, bridge_payout_id: str, settlement_reference: str = "") -> dict[str, Any]:
        payout_id = str(bridge_payout_id or "").strip()
        if not payout_id:
            raise StableHubWorkerSessionError("bridge_payout_id is required.")
        with self._lock:
            data = self.store.load()
            payout = data.setdefault("bridge_payouts", {}).get(payout_id)
            if not isinstance(payout, dict):
                raise StableHubWorkerSessionError("bridge payout does not exist.")
            if payout.get("status") == "confirmed":
                return dict(payout)
            payout = dict(payout)
            payout["status"] = "confirmed"
            payout["confirmed_at"] = utc_now()
            payout["settlement_reference"] = str(settlement_reference or payout_id)
            data["bridge_payouts"][payout_id] = payout
            self._event(data, "stable.work.payout.bridge_confirmed", bridge_payout_id=payout_id, worker_id=payout.get("worker_id"), batch_id=payout.get("batch_id"))
            self.store.save(data)
            return dict(payout)

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
            if payout.get("status") != "confirmed":
                payout["status"] = "failed"
                payout["failed_at"] = utc_now()
                payout["failure_reason"] = str(reason)
                data["bridge_payouts"][payout_id] = payout
                self._event(data, "stable.work.payout.bridge_failed", bridge_payout_id=payout_id, worker_id=payout.get("worker_id"), reason=str(reason))
                self.store.save(data)
            return dict(payout)

    def status(self) -> dict[str, Any]:
        with self._lock:
            data = self.store.load()
            return {
                "ok": True,
                "accounts": list(data.get("payout_accounts", {}).values()),
                "holds": list(data.get("payout_holds", {}).values()),
                "charges": list(data.get("payout_charges", {}).values()),
                "worker_earnings": list(data.get("worker_earnings", {}).values()),
                "worker_claims": list(data.get("worker_claims", {}).values()),
                "settlements": list(data.get("payout_settlements", {}).values()),
                "bridge_payouts": list(data.get("bridge_payouts", {}).values()),
                "events": list(data.get("payout_events", [])),
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
        self._lock = threading.Lock()

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

    def record_worker_live(
        self,
        *,
        worker_id: str,
        owner: dict[str, Any],
        market_profile: dict[str, Any] | None,
        worker_msk_id: str,
        worker_wallet_address: str,
        worker_account_id: str,
    ) -> dict[str, Any]:
        worker_id = normalize_worker_id(worker_id)
        profile = normalize_worker_market_profile(market_profile)
        now = utc_now()
        with self._lock:
            data = self.store.load()
            records = data.setdefault("market_workers", {})
            previous = records.get(worker_id)
            active_sessions = int(profile.get("active_sessions") or 0)
            if isinstance(previous, dict) and previous.get("status") == "live":
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
                "connection_id": str(owner.get("connection_id") or ""),
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
        self._lock = threading.Lock()

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        session_id = normalize_session_id(session_id)
        with self._lock:
            data = self.store.load()
            record = data.get("accepted_sessions", {}).get(session_id)
            return dict(record) if isinstance(record, dict) else None

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
            "completed_at": "",
            "failed_at": "",
        }
        with self._lock:
            data = self.store.load()
            sessions = data.setdefault("accepted_sessions", {})
            sessions[session_id] = record
            self.store.save(data)
            return dict(record)

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
            execution = dict(record.get("execution") or {})
            execution["status"] = "succeeded"
            record["execution"] = execution
            sessions[session_id] = record
            self.store.save(data)
            return dict(record)

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
        self._lock = threading.Lock()

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def get_owner(self, worker_id: str) -> dict[str, Any] | None:
        worker_id = normalize_worker_id(worker_id)
        with self._lock:
            data = self.store.load()
            record = data.get("workers", {}).get(worker_id)
            return dict(record) if isinstance(record, dict) else None

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
