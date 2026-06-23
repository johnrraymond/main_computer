from __future__ import annotations

import json
import os
import re
import secrets
import platform
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import (
    normalize_address,
    normalize_chain_id,
    verify_personal_sign_blob,
)


STABLE_MSK_STORE_VERSION = "main-computer-stable-hub-multisession-keys-v1"
STABLE_MSK_SIGNED_REQUEST_MAX_AGE_MINUTES = 15
STABLE_MSK_USER_SLUG_MIN_CHARS = 32
STABLE_MSK_USER_SLUG_MAX_CHARS = 256
STABLE_MSK_HUB_SLUG_BYTES = 32
STABLE_MSK_FDB_TRANSACTION_TIMEOUT_MS = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_TIMEOUT_MS", "5000"))
STABLE_MSK_FDB_RETRY_LIMIT = int(os.environ.get("MAIN_COMPUTER_STABLE_HUB_FDB_RETRY_LIMIT", "1"))
_STABLE_MSK_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class StableHubMultiSessionKeyError(ValueError):
    """Raised when a stable Hub multi-session key request is malformed."""


class StableHubMultiSessionKeyStore(Protocol):
    def load(self) -> dict[str, Any]:
        ...

    def save(self, data: dict[str, Any]) -> None:
        ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_store(data: dict[str, Any] | None) -> dict[str, Any]:
    clean = dict(data) if isinstance(data, dict) else {}
    keys = clean.get("keys")
    if not isinstance(keys, dict):
        keys = {}
    clean["keys"] = {str(key): dict(value) for key, value in keys.items() if isinstance(value, dict)}
    clean.setdefault("version", STABLE_MSK_STORE_VERSION)
    return clean


class InMemoryStableMultiSessionKeyStore:
    """Small shared test/dev store for stable Hub MSK records."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data = _normalize_store(initial)
        self._lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._data = _normalize_store(data)


class JsonStableMultiSessionKeyStore:
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


@dataclass(frozen=True)
class _NativeClientTarget:
    runtime_id: str
    library_name: str


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
    timeout = max(1, int(STABLE_MSK_FDB_TRANSACTION_TIMEOUT_MS))
    retry_limit = max(0, int(STABLE_MSK_FDB_RETRY_LIMIT))
    set_timeout = getattr(options, "set_timeout", None)
    if callable(set_timeout):
        set_timeout(timeout)
    set_retry_limit = getattr(options, "set_retry_limit", None)
    if callable(set_retry_limit):
        set_retry_limit(retry_limit)


class FoundationDbStableMultiSessionKeyStore:
    """Shared stable Hub MSK store backed by the topology's FoundationDB cluster.

    The connection is opened lazily so stable Hub identity/health startup remains
    lightweight even before the dev FDB container is running.
    """

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
            raise StableHubMultiSessionKeyError("storage.namespace is required for FoundationDB MSK storage.")
        self.repo_root = Path(repo_root)
        self.api_version = int(api_version)
        self.native_client_library: Path | None = None
        self._lock = threading.Lock()
        self._opened = False
        self._fdb: Any = None
        self._db: Any = None

    @property
    def backend_name(self) -> str:
        return "foundationdb"

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
                    "Stable Hub MSK storage requires the foundationdb Python package for this topology."
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
        return self._fdb.tuple.pack((self.namespace, "stable-hub", "multisession_keys"))

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
                "FoundationDB MSK store load failed or timed out. "
                f"cluster_file={self.cluster_file} namespace={self.namespace} "
                f"timeout_ms={STABLE_MSK_FDB_TRANSACTION_TIMEOUT_MS}"
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
                "FoundationDB MSK store save failed or timed out. "
                f"cluster_file={self.cluster_file} namespace={self.namespace} "
                f"timeout_ms={STABLE_MSK_FDB_TRANSACTION_TIMEOUT_MS}"
            ) from exc


def stable_multisession_store_from_topology(
    topology: Any,
    *,
    repo_root: str | Path = ".",
) -> StableHubMultiSessionKeyStore:
    storage = dict(getattr(topology, "storage", {}) or {})
    backend = str(storage.get("backend") or "").strip().lower()
    if backend == "foundationdb":
        return FoundationDbStableMultiSessionKeyStore(
            cluster_file=storage.get("cluster_file") or ".foundationdb/docker.cluster",
            namespace=storage.get("namespace") or "",
            repo_root=repo_root,
            api_version=int(storage.get("api_version") or 740),
        )
    if backend == "local-json":
        store_path = storage.get("multisession_key_store_path") or storage.get("path")
        if not store_path:
            namespace = str(storage.get("namespace") or "stable-hub-dev").strip() or "stable-hub-dev"
            store_path = Path("runtime") / "stable-hub" / namespace / "multisession_keys.json"
        return JsonStableMultiSessionKeyStore(store_path)
    raise StableHubMultiSessionKeyError(f"Unsupported stable Hub MSK storage backend: {backend!r}")


def _canonical_hash(payload: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_msk_slug(value: Any, *, field_name: str) -> str:
    slug = str(value or "").strip()
    if not slug:
        raise StableHubMultiSessionKeyError(f"{field_name} is required.")
    if len(slug) < STABLE_MSK_USER_SLUG_MIN_CHARS:
        raise StableHubMultiSessionKeyError(
            f"{field_name} must be at least {STABLE_MSK_USER_SLUG_MIN_CHARS} characters."
        )
    if len(slug) > STABLE_MSK_USER_SLUG_MAX_CHARS:
        raise StableHubMultiSessionKeyError(
            f"{field_name} must be at most {STABLE_MSK_USER_SLUG_MAX_CHARS} characters."
        )
    if not _STABLE_MSK_SLUG_RE.match(slug):
        raise StableHubMultiSessionKeyError(
            f"{field_name} may only contain letters, digits, underscore, and dash."
        )
    return slug


def _new_hub_slug() -> str:
    # token_urlsafe(32) provides 256 bits from the Hub side. The signed
    # user_slug contributes independent user-side entropy.
    return secrets.token_urlsafe(STABLE_MSK_HUB_SLUG_BYTES).rstrip("=")


def _multisession_key_id(*, user_slug: str, hub_slug: str) -> str:
    return f"msk_{user_slug}_{hub_slug}"


def _public_verification(verification: dict[str, Any]) -> dict[str, Any]:
    payload = dict(verification)
    payload.pop("signature", None)
    payload.pop("message", None)
    return payload


def _public_key_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(record.get("id") or ""),
        "status": str(record.get("status") or ""),
        "wallet_address": str(record.get("wallet_address") or ""),
        "account_id": str(record.get("account_id") or ""),
        "chain_id": str(record.get("chain_id") or ""),
        "created_at": str(record.get("created_at") or ""),
        "revoked_at": str(record.get("revoked_at") or ""),
        "replaced_by": str(record.get("replaced_by") or ""),
        "request_id": str(record.get("request_id") or ""),
        "user_slug": str(record.get("user_slug") or ""),
        "hub_slug": str(record.get("hub_slug") or ""),
        "origin": str(record.get("origin") or ""),
        "cluster_id": str(record.get("cluster_id") or ""),
        "issued_by_hub_id": str(record.get("issued_by_hub_id") or ""),
        "signed_request_hash": str(record.get("signed_request_hash") or ""),
        "has_signed_request": isinstance(record.get("signed_request"), dict),
    }


def _authorization_from_body(body: dict[str, Any]) -> dict[str, Any]:
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    for key in ("multisession_authorization", "payment_authorization"):
        value = body.get(key)
        if isinstance(value, dict):
            return dict(value)
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if isinstance(value, dict):
            return dict(value)
    # Readiness calls commonly pass fields at top level.
    if any(key in body for key in ("wallet_address", "multisession_key_id", "key_id")):
        return dict(body)
    return {}


class StableHubMultiSessionKeyService:
    """Stable Hub MSK issue/validate service.

    This is intentionally limited to the production contract surface:
    issue a wallet-signed MSK and validate bearer-style MSK authorization.
    It does not perform credit holds, worker registration, dispatch, or legacy
    heartbeat behavior.
    """

    def __init__(
        self,
        *,
        topology: Any,
        hub_id: str,
        store: StableHubMultiSessionKeyStore,
    ) -> None:
        self.topology = topology
        self.hub_id = str(hub_id)
        self.store = store
        self._lock = threading.Lock()

    @property
    def expected_chain_id(self) -> str:
        network = dict(getattr(self.topology, "network", {}) or {})
        return normalize_chain_id(network.get("chain_id"))

    @property
    def cluster_id(self) -> str:
        return str(getattr(self.topology, "cluster_id", "") or "")

    def request_key(self, body: dict[str, Any]) -> dict[str, Any]:
        signed_request = body.get("signed_request")
        if not isinstance(signed_request, dict):
            raise StableHubMultiSessionKeyError("signed_request object is required.")

        verification = verify_personal_sign_blob(
            signed_request,
            expected_chain_id=self.expected_chain_id,
            max_age_minutes=STABLE_MSK_SIGNED_REQUEST_MAX_AGE_MINUTES,
        )
        message = verification.get("message")
        if not isinstance(message, dict):
            raise StableHubMultiSessionKeyError("signed_request message is required.")
        user_slug = _normalize_msk_slug(message.get("user_slug"), field_name="user_slug")

        wallet_address = normalize_address(verification["wallet_address"])
        chain_id = normalize_chain_id(verification.get("chain_id"))
        request_id = str(verification.get("request_id") or "")
        account_id = wallet_account_id(wallet_address)
        signed_request_hash = _canonical_hash(signed_request)
        now = _utc_now()

        with self._lock:
            data = self.store.load()
            keys = data.setdefault("keys", {})
            existing = self._key_for_signed_request_hash(data, signed_request_hash)
            if existing:
                key_payload = existing
                idempotent = True
            else:
                # The user contributes signed entropy through user_slug; the Hub
                # contributes independent entropy through hub_slug. The MSK id is
                # the combination, so neither side is solely responsible for the
                # bearer identifier entropy.
                for _ in range(8):
                    hub_slug = _new_hub_slug()
                    key_id = _multisession_key_id(user_slug=user_slug, hub_slug=hub_slug)
                    if key_id not in keys:
                        break
                else:  # pragma: no cover - practically unreachable with 256-bit hub slug
                    raise StableHubMultiSessionKeyError("could not allocate a unique multi-session key id.")

                key_payload = {
                    "id": key_id,
                    "status": "active",
                    "created_at": now,
                    "revoked_at": "",
                    "replaced_by": "",
                    "wallet_address": wallet_address,
                    "account_id": account_id,
                    "chain_id": chain_id,
                    "request_id": request_id,
                    "user_slug": user_slug,
                    "hub_slug": hub_slug,
                    "origin": str(verification.get("origin") or ""),
                    "cluster_id": self.cluster_id,
                    "issued_by_hub_id": self.hub_id,
                    "signed_request_hash": signed_request_hash,
                    "signed_request": json.loads(json.dumps(signed_request)),
                    "signed_message": json.loads(json.dumps(message)),
                    "verified": {
                        "wallet_address": wallet_address,
                        "account_id": account_id,
                        "chain_id": chain_id,
                        "request_id": request_id,
                        "user_slug": user_slug,
                        "origin": str(verification.get("origin") or ""),
                        "issued_at": verification.get("issued_at"),
                        "expires_at": verification.get("expires_at"),
                        "recovered_address": verification.get("recovered_address"),
                    },
                }
                keys[key_id] = key_payload
                self.store.save(data)
                idempotent = False

        key_id = str(key_payload.get("id") or "")
        authorization = {
            "kind": "multisession_key",
            "multisession_key_id": key_id,
            "key_id": key_id,
            "chain_id": chain_id,
            "cluster_id": self.cluster_id,
        }
        return {
            "ok": True,
            "idempotent": idempotent,
            "hub_id": self.hub_id,
            "cluster_id": self.cluster_id,
            "verification": _public_verification(verification),
            "key": _public_key_record(key_payload),
            "multisession_authorization": authorization,
        }

    def validate_key(self, body: dict[str, Any]) -> dict[str, Any]:
        authorization = _authorization_from_body(body)
        if not authorization:
            return self._invalid(
                reason_code="missing_multisession_authorization",
                user_message="A multi-session key authorization is required.",
            )

        key_id = str(authorization.get("multisession_key_id") or authorization.get("key_id") or "").strip()
        if not key_id:
            return self._invalid(
                reason_code="missing_multisession_key_id",
                user_message="An active multi-session key id is required before this Hub can validate authorization.",
            )

        expected_chain_id = self.expected_chain_id
        requested_chain_id = normalize_chain_id(authorization.get("chain_id") or body.get("chain_id") or "")
        if requested_chain_id and expected_chain_id and requested_chain_id != expected_chain_id:
            return self._invalid(
                reason_code="unsupported_chain_id",
                user_message="The multi-session key authorization targets a different chain than this stable Hub topology.",
                multisession_key_id=key_id,
                chain_id=requested_chain_id,
                expected_chain_id=expected_chain_id,
            )

        with self._lock:
            data = self.store.load()
            record = data.get("keys", {}).get(key_id)
            record = dict(record) if isinstance(record, dict) else None

        if not record or record.get("status") != "active":
            return self._invalid(
                reason_code="key_not_active",
                user_message="The saved multi-session key is not active on this Hub. Request a new multi-session key for this network's Hub before proceeding.",
                multisession_key_id=key_id,
                chain_id=requested_chain_id or expected_chain_id,
            )

        try:
            wallet_address = normalize_address(record.get("wallet_address"))
        except ValueError:
            return self._invalid(
                reason_code="stored_key_wallet_invalid",
                user_message="The selected multi-session key record is malformed on this Hub.",
                multisession_key_id=key_id,
            )
        account_id = str(record.get("account_id") or wallet_account_id(wallet_address))

        supplied_wallet = authorization.get("wallet_address")
        if supplied_wallet:
            try:
                supplied_wallet_address = normalize_address(supplied_wallet)
            except ValueError:
                return self._invalid(
                    reason_code="bad_wallet_address",
                    user_message="The supplied wallet address is malformed.",
                    multisession_key_id=key_id,
                )
            if supplied_wallet_address != wallet_address:
                return self._invalid(
                    reason_code="key_wallet_mismatch",
                    user_message="The selected multi-session key belongs to a different wallet.",
                    wallet_address=supplied_wallet_address,
                    stored_wallet_address=wallet_address,
                    multisession_key_id=key_id,
                    chain_id=requested_chain_id or expected_chain_id,
                )

        record_chain_id = normalize_chain_id(record.get("chain_id"))
        if record_chain_id and expected_chain_id and record_chain_id != expected_chain_id:
            return self._invalid(
                reason_code="record_chain_id_mismatch",
                user_message="The selected multi-session key was issued for a different chain.",
                wallet_address=wallet_address,
                multisession_key_id=key_id,
                record_chain_id=record_chain_id,
                expected_chain_id=expected_chain_id,
            )
        if requested_chain_id and record_chain_id and requested_chain_id != record_chain_id:
            return self._invalid(
                reason_code="chain_id_mismatch",
                user_message="The selected multi-session key authorization does not match the key's chain.",
                wallet_address=wallet_address,
                multisession_key_id=key_id,
                chain_id=requested_chain_id,
                record_chain_id=record_chain_id,
            )

        return {
            "ok": True,
            "valid": True,
            "ready": True,
            "hub_reachable": True,
            "reason_code": "active",
            "user_message": "The selected multi-session key is active on this stable Hub cluster.",
            "hub_id": self.hub_id,
            "cluster_id": self.cluster_id,
            "wallet_address": wallet_address,
            "account_id": account_id,
            "multisession_key_id": key_id,
            "chain_id": requested_chain_id or record_chain_id,
            "key": _public_key_record(record),
            "authorization": {
                "kind": "multisession_key",
                "multisession_key_id": key_id,
                "key_id": key_id,
                "chain_id": requested_chain_id or record_chain_id,
                "cluster_id": self.cluster_id,
            },
        }

    def _key_for_signed_request_hash(
        self,
        data: dict[str, Any],
        signed_request_hash: str,
    ) -> dict[str, Any] | None:
        for record in data.get("keys", {}).values():
            if not isinstance(record, dict):
                continue
            if record.get("signed_request_hash") == signed_request_hash:
                return dict(record)
        return None

    def _invalid(self, *, reason_code: str, user_message: str, **extra: Any) -> dict[str, Any]:
        payload = {
            "ok": True,
            "valid": False,
            "ready": False,
            "hub_reachable": True,
            "reason_code": reason_code,
            "user_message": user_message,
            "hub_id": self.hub_id,
            "cluster_id": self.cluster_id,
        }
        payload.update(extra)
        if "wallet_address" in payload:
            try:
                payload["account_id"] = wallet_account_id(str(payload["wallet_address"]))
            except Exception:
                pass
        return payload
