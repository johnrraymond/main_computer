from __future__ import annotations

from dataclasses import replace
import base64
import ipaddress
import json
import os
import socket
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit
from urllib.request import Request, urlopen

from main_computer.viewport_state import *  # noqa: F401,F403
from main_computer.hub_networks import HubNetworkConfigError, load_hub_network_registry
from main_computer.windows_user_activity import collect_windows_user_activity
from main_computer.credit_units import credit_decimal_text_to_wei, credit_wei_to_decimal_text
from main_computer.chat_ai_subprocess import append_text_log, config_to_payload


class _WorkerHubLiveSessionClient:
    """Minimal stdlib WebSocket client for the Hub worker live-session contract.

    Worker availability is no longer a REST heartbeat.  The Hub owns liveness by
    keeping a worker WebSocket open, sending JSON ``hub.ping`` messages, and
    expecting JSON ``worker.pong`` replies over the same connection.
    """

    endpoint_path = "/api/hub/v1/workers/live-session"

    def __init__(
        self,
        *,
        hub_url: str,
        worker_id: str,
        auth_message: dict[str, Any],
        timeout_s: float = 5.0,
        work_executor: Any | None = None,
        work_canceller: Any | None = None,
    ) -> None:
        self.hub_url = str(hub_url or "").rstrip("/")
        self.worker_id = str(worker_id or "").strip()
        self.auth_message = json.loads(json.dumps(auth_message, ensure_ascii=False))
        self.timeout_s = max(1.0, float(timeout_s or 5.0))
        self.work_executor = work_executor
        self.work_canceller = work_canceller
        self.fingerprint = json.dumps(
            {
                "hub_url": self.hub_url,
                "worker_id": self.worker_id,
                "auth": self._stable_auth_fingerprint_payload(self.auth_message),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.connection_id = ""
        self.accepted: dict[str, Any] = {}
        self.owner: dict[str, Any] = {}
        self.market: dict[str, Any] = {}
        self.last_pong: dict[str, Any] = {}
        self.last_offer: dict[str, Any] = {}
        self.last_result: dict[str, Any] = {}
        self.active_work_count = 0
        self.last_error = ""
        self.opened_at = ""
        self.closed_at = ""
        self.close_reason = ""
        self._socket: socket.socket | None = None
        self._send_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reader_thread: threading.Thread | None = None

    @property
    def is_alive(self) -> bool:
        sock = self._socket
        thread = self._reader_thread
        return (
            sock is not None
            and not self._stop_event.is_set()
            and not self.closed_at
            and (thread is None or thread.is_alive())
        )

    @property
    def has_active_work(self) -> bool:
        with self._state_lock:
            return self.active_work_count > 0

    @staticmethod
    def _stable_auth_fingerprint_payload(auth_message: dict[str, Any]) -> dict[str, Any]:
        """Return only durable auth/market inputs used to decide socket replacement.

        The full ``worker.auth`` payload includes volatile availability snapshots
        such as last user activity and local AI capacity.  Reconnecting whenever
        those fields change can close the selected websocket while an accepted
        live-session job is still running.  The Hub learns current capacity from
        the live session itself; this fingerprint is only for deciding whether a
        viewport-side websocket must be replaced.
        """

        market = dict(auth_message.get("market") or {}) if isinstance(auth_message.get("market"), dict) else {}
        auth = (
            dict(auth_message.get("multisession_authorization") or {})
            if isinstance(auth_message.get("multisession_authorization"), dict)
            else {}
        )
        return {
            "type": str(auth_message.get("type") or ""),
            "worker_id": str(auth_message.get("worker_id") or ""),
            "worker_instance_id": str(auth_message.get("worker_instance_id") or ""),
            "chain_id": str(auth_message.get("chain_id") or auth.get("chain_id") or ""),
            "model": str(auth_message.get("model") or ""),
            "models": [str(item) for item in auth_message.get("models", [])] if isinstance(auth_message.get("models"), list) else [],
            "authorization": {
                "kind": str(auth.get("kind") or ""),
                "key_id": str(auth.get("key_id") or auth.get("multisession_key_id") or ""),
                "wallet_address": str(auth.get("wallet_address") or ""),
                "chain_id": str(auth.get("chain_id") or ""),
            },
            "market": {
                "rings": [str(item) for item in market.get("rings", [])] if isinstance(market.get("rings"), list) else [],
                "partitions": [str(item) for item in market.get("partitions", [])] if isinstance(market.get("partitions"), list) else [],
                "capabilities": [str(item) for item in market.get("capabilities", [])] if isinstance(market.get("capabilities"), list) else [],
                "models": [str(item) for item in market.get("models", [])] if isinstance(market.get("models"), list) else [],
                "max_concurrency": int(market.get("max_concurrency") or auth_message.get("max_concurrency") or 1),
                "price": dict(market.get("price") or {}) if isinstance(market.get("price"), dict) else {},
            },
        }

    def start(self) -> dict[str, Any]:
        self._socket = self._open_socket()
        self.opened_at = datetime.now(timezone.utc).isoformat()
        self._send_json(self.auth_message)

        deadline = time.monotonic() + self.timeout_s
        saw_accepted = False
        saw_pong = False
        while time.monotonic() < deadline:
            try:
                message = self._recv_json()
            except TimeoutError:
                continue
            message_type = str(message.get("type") or "")
            if message_type == "hub.error":
                raise RuntimeError(str(message.get("error") or message))
            self._handle_hub_message(message)
            if message_type == "hub.auth.accepted":
                saw_accepted = True
            elif message_type == "hub.pong.accepted":
                saw_pong = True
            if saw_accepted and saw_pong:
                break

        if not saw_accepted:
            raise RuntimeError("Hub live-session did not accept worker.auth.")
        if not saw_pong:
            raise RuntimeError("Hub live-session did not complete the initial ping/pong keepalive.")

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"main-computer-worker-live-session-{self.worker_id}",
            daemon=True,
        )
        self._reader_thread.start()
        return self.snapshot()

    def close(self, reason: str = "closed_by_runtime") -> None:
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self.close_reason = str(reason or "closed")
        try:
            self._send_json({"type": "worker.close", "reason": self.close_reason})
        except Exception:
            pass
        try:
            self._send_close_frame()
        except Exception:
            pass
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if not self.closed_at:
            self.closed_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "ok": not bool(self.last_error),
                "transport": "websocket",
                "endpoint": self.endpoint_path,
                "hub_url": self.hub_url,
                "worker_id": self.worker_id,
                "connection_id": self.connection_id,
                "opened_at": self.opened_at,
                "closed_at": self.closed_at,
                "close_reason": self.close_reason,
                "last_error": self.last_error,
                "accepted": dict(self.accepted),
                "owner": dict(self.owner),
                "market": dict(self.market),
                "last_pong": dict(self.last_pong),
                "last_offer": dict(self.last_offer),
                "last_result": dict(self.last_result),
                "active_work_count": self.active_work_count,
                "has_active_work": self.active_work_count > 0,
                "alive": self.is_alive,
            }

    def _reader_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    message = self._recv_json()
                except TimeoutError:
                    continue
                self._handle_hub_message(message)
        except Exception as exc:
            with self._state_lock:
                if not self._stop_event.is_set():
                    self.last_error = str(exc)
                    self.close_reason = "reader_error"
                if not self.closed_at:
                    self.closed_at = datetime.now(timezone.utc).isoformat()
            self._stop_event.set()
            sock = self._socket
            self._socket = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass

    def _handle_hub_message(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type == "hub.auth.accepted":
            with self._state_lock:
                self.accepted = dict(message)
                self.connection_id = str(message.get("connection_id") or "")
                self.owner = dict(message.get("owner") or {}) if isinstance(message.get("owner"), dict) else {}
                self.market = dict(message.get("market") or {}) if isinstance(message.get("market"), dict) else {}
            return
        if message_type == "hub.ping":
            ping_id = str(message.get("ping_id") or "")
            connection_id = str(message.get("connection_id") or self.connection_id)
            self._send_json(
                {
                    "type": "worker.pong",
                    "connection_id": connection_id,
                    "ping_id": ping_id,
                }
            )
            return
        if message_type == "hub.pong.accepted":
            with self._state_lock:
                self.last_pong = dict(message)
                self.owner = dict(message.get("owner") or self.owner) if isinstance(message.get("owner"), dict) else self.owner
            return
        if message_type == "hub.work.offer":
            self._handle_work_offer(message)
            return
        if message_type in {"hub.work.result.accepted", "hub.work.failed.accepted", "hub.work.terminal.accepted"}:
            with self._state_lock:
                self.last_result = dict(message)
            return
        if message_type == "hub.error":
            with self._state_lock:
                self.last_error = str(message.get("error") or message)
            return

    def _handle_work_offer(self, offer: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        if not session_id or not request_id:
            raise RuntimeError(f"Hub work offer missing session_id/request_id: {offer}")
        accepted = {
            "type": "worker.work.accepted",
            "session_id": session_id,
            "request_id": request_id,
        }
        run_id = str(offer.get("run_id") or "")
        if run_id:
            accepted["run_id"] = run_id
        self._send_json(accepted)
        with self._state_lock:
            self.last_offer = dict(offer)
            self.active_work_count += 1

        worker_thread = threading.Thread(
            target=self._complete_work_offer,
            args=(dict(offer),),
            name=f"worker-live-session-offer-{session_id[:12] or request_id[:12]}",
            daemon=True,
        )
        worker_thread.start()

    def _complete_work_offer(self, offer: dict[str, Any]) -> None:
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        run_id = str(offer.get("run_id") or "")
        result_message: dict[str, Any] = {}
        try:
            result = self._execute_work_offer(offer)
            result_message = {
                "type": "worker.work.result",
                "session_id": session_id,
                "request_id": request_id,
                "result": result,
            }
            if run_id:
                result_message["run_id"] = run_id
            self._send_json(result_message)
        except BaseException as exc:
            error_payload = self._worker_failure_payload_for_exception(exc, offer)
            result_message = {
                "type": "worker.work.failed",
                "session_id": session_id,
                "request_id": request_id,
                "error": error_payload,
            }
            if run_id:
                result_message["run_id"] = run_id
            try:
                self._send_json(result_message)
            except Exception as send_exc:
                with self._state_lock:
                    self.last_error = (
                        f"Worker live-session local executor failed with {exc.__class__.__name__}: {exc}; "
                        f"also failed to report terminal worker.work.failed: {send_exc}"
                    )
        finally:
            with self._state_lock:
                if result_message:
                    self.last_result = dict(result_message)
                self.active_work_count = max(0, self.active_work_count - 1)

    def _worker_failure_payload_for_exception(self, exc: BaseException, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        session_id = str(offer.get("session_id") or "")
        request_id = str(offer.get("request_id") or "")
        run_id = str(offer.get("run_id") or "")
        return {
            "error": str(exc) or exc.__class__.__name__,
            "error_type": exc.__class__.__name__,
            "status": "failed",
            "transport": "websocket-live-session",
            "worker_id": self.worker_id,
            "session_id": session_id,
            "request_id": request_id,
            "run_id": run_id,
            "model": str(work.get("model") or ""),
            "capabilities": [str(item) for item in work.get("capabilities", [])] if isinstance(work.get("capabilities"), list) else [],
        }

    def _work_offer_timeout_seconds(self, offer: dict[str, Any]) -> float:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        candidates = [
            work.get("timeout_seconds"),
            work.get("max_runtime_seconds"),
            work.get("local_ai_timeout_seconds"),
            offer.get("timeout_seconds"),
            os.environ.get("MAIN_COMPUTER_WORKER_LIVE_SESSION_LOCAL_AI_TIMEOUT_SECONDS"),
        ]
        for value in candidates:
            raw = str(value or "").strip()
            if not raw:
                continue
            try:
                parsed = float(raw)
            except ValueError:
                continue
            if parsed > 0:
                return max(1.0, min(parsed, 3600.0))
        return 45.0

    def _cancel_work_executor_after_timeout(self, executor: Any, offer: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        """Best-effort cancellation for worker local-AI work after the outer offer timeout.

        The live-session client owns the Hub terminal failure timeout, but the actual
        local AI slot is owned by the route/server executor.  Without this hook the
        Hub can see a failed job while the local subprocess remains active and keeps
        the shared local-AI capacity slot busy.
        """

        canceller = self.work_canceller
        if not callable(canceller):
            owner = getattr(executor, "__self__", None)
            canceller = getattr(owner, "_cancel_worker_live_session_offer", None)
        if not callable(canceller):
            return {"ok": False, "cancelled": False, "reason": "no-work-canceller"}
        result = canceller(
            offer,
            reason="worker-live-session-timeout",
            timeout_s=timeout_s,
        )
        return result if isinstance(result, dict) else {"ok": True, "cancelled": True, "result": result}

    def _call_work_executor_with_timeout(self, executor: Any, offer: dict[str, Any]) -> Any:
        timeout_s = self._work_offer_timeout_seconds(offer)
        done = threading.Event()
        box: dict[str, Any] = {}

        def runner() -> None:
            try:
                box["result"] = executor(offer)
            except BaseException as exc:  # propagate through the parent offer thread
                box["exception"] = exc
            finally:
                done.set()

        thread = threading.Thread(
            target=runner,
            name=f"worker-live-session-local-ai-{str(offer.get('session_id') or offer.get('request_id') or '')[:12]}",
            daemon=True,
        )
        thread.start()
        if not done.wait(timeout_s):
            try:
                box["timeout_cancel"] = self._cancel_work_executor_after_timeout(executor, offer, timeout_s=timeout_s)
            except BaseException as cancel_exc:
                box["timeout_cancel_error"] = f"{cancel_exc.__class__.__name__}: {cancel_exc}"
            done.wait(0.25)
            raise TimeoutError(f"Worker live-session local AI executor timed out after {timeout_s:.1f}s.")
        if "exception" in box:
            raise box["exception"]
        return box.get("result")

    def _execute_work_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        if str(input_payload.get("kind") or "").lower() == "echo":
            return self._debug_echo_result_payload_for_offer(offer)
        executor = self.work_executor
        if not callable(executor):
            raise RuntimeError("Worker live-session has no local work executor configured.")
        result = self._call_work_executor_with_timeout(executor, offer)
        if not isinstance(result, dict):
            result = {"response": {"content": str(result or "")}}
        result.setdefault("status", "success")
        response = result.get("response")
        if not isinstance(response, dict):
            result["response"] = {"content": str(response or "")}
        result.setdefault("transport", "websocket-live-session")
        result.setdefault("worker_id", self.worker_id)
        return result

    def _debug_echo_result_payload_for_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        content = str(input_payload.get("value") or input_payload.get("prompt") or "")
        return {
            "status": "success",
            "response": {
                "role": "assistant",
                "content": content,
            },
            "transport": "websocket-live-session",
            "worker_id": self.worker_id,
            "debug_echo": True,
        }

    def _open_socket(self) -> socket.socket:
        parsed = urlparse(self.hub_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError(f"Hub URL must be http(s): {self.hub_url!r}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        raw_sock = socket.create_connection((parsed.hostname, port), timeout=self.timeout_s)
        if parsed.scheme == "https":
            context = ssl.create_default_context()
            sock: socket.socket = context.wrap_socket(raw_sock, server_hostname=parsed.hostname)
        else:
            sock = raw_sock
        sock.settimeout(self.timeout_s)
        request_path = self._websocket_request_path(parsed)
        host = parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {request_path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: MainComputerWorker/0.1\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError(f"Hub live-session websocket handshake closed early: {response!r}")
            response += chunk
            if len(response) > 65536:
                raise RuntimeError("Hub live-session websocket handshake response was too large.")
        first_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in first_line:
            try:
                detail = response.decode("utf-8", errors="replace")
            except Exception:
                detail = repr(response[:1000])
            raise RuntimeError(f"Hub live-session websocket handshake failed: {detail[:1000]}")
        sock.settimeout(1.0)
        return sock

    def _websocket_request_path(self, parsed: Any) -> str:
        prefix = str(parsed.path or "").rstrip("/")
        if not prefix:
            prefix = ""
        return prefix + self.endpoint_path

    def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self._send_frame(opcode=0x1, payload=data)

    def _send_close_frame(self) -> None:
        self._send_frame(opcode=0x8, payload=b"")

    def _send_frame(self, *, opcode: int, payload: bytes) -> None:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        with self._send_lock:
            sock.sendall(bytes(header) + mask + masked)

    def _recv_json(self) -> dict[str, Any]:
        while True:
            opcode, payload = self._recv_frame()
            if opcode == 0x8:
                raise RuntimeError("Hub closed the worker live-session websocket.")
            if opcode == 0x9:
                self._send_frame(opcode=0xA, payload=payload)
                continue
            if opcode != 0x1:
                continue
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Hub live-session returned non-JSON text: {exc}") from exc
            if not isinstance(decoded, dict):
                raise RuntimeError("Hub live-session returned non-object JSON.")
            return decoded

    def _recv_frame(self) -> tuple[int, bytes]:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        header = self._read_exact(2)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        masked = bool(header[1] & 0x80)
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length) if length else b""
        if masked and mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _read_exact(self, length: int) -> bytes:
        sock = self._socket
        if sock is None:
            raise RuntimeError("worker live-session socket is not open.")
        chunks: list[bytes] = []
        remaining = int(length)
        while remaining:
            try:
                chunk = sock.recv(remaining)
            except socket.timeout as exc:
                raise TimeoutError("Timed out waiting for Hub live-session websocket data.") from exc
            if not chunk:
                raise RuntimeError("Hub live-session websocket closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


class ViewportEnergyRoutesMixin:

    _ENERGY_RPC_USER_AGENT = "MainComputerEnergy/1.0"
    _ENERGY_NETWORK_ORDER = ("mainnet", "testnet", "test", "dev")
    _WORKER_DEFAULT_CREDITS_PER_REQUEST = "1.024"
    _WORKER_DEFAULT_CREDITS_PER_TOKEN = "0.001"
    _WORKER_DEFAULT_SELLER_TARGET_TOKENS = 1024
    _WORKER_DEFAULT_SELLER_MODEL = "gemma4:26b"
    _WORKER_SELLER_AVAILABILITY_TOTAL_IDLE = "totally_idle"
    _WORKER_SELLER_AVAILABILITY_AI_IDLE = "ai_idle"
    _WORKER_SELLER_AVAILABILITY_MODES = {_WORKER_SELLER_AVAILABILITY_TOTAL_IDLE, _WORKER_SELLER_AVAILABILITY_AI_IDLE}
    _WORKER_LEGACY_CREDITS_PER_REQUESTS = {"5500123", "5500123.0", "5500123.00", "1.25", "1.250", "1.2500"}
    _WORKER_LEGACY_SELLER_MODELS = {"mock-ai-model-phase9"}
    _ENERGY_EXPECTED_CONTRACTS = (
        ("alpha-beta-lockout", "AlphaBetaLockout"),
        ("xlag-bridge-reserve", "XLagBridgeReserve"),
        ("hub_credit_bridge_escrow", "HubCreditBridgeEscrow"),
    )
    _ANVIL_DEFAULT_OFFICES = {
        "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
        "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
        "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
        "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
    }

    def _energy_bool_query(self, name: str, *, default: bool = True) -> bool:
        query = parse_qs(urlsplit(self.path).query)
        raw = query.get(name, [None])[0]
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    def _energy_hex_to_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(str(value), 16 if str(value).strip().lower().startswith("0x") else 10)
        except (TypeError, ValueError):
            return None

    def _energy_rpc_call(self, rpc_url: str, method: str, params: list[Any] | None = None, *, timeout_s: float = 0.75) -> Any:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode("utf-8")
        request = Request(
            str(rpc_url),
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": self._ENERGY_RPC_USER_AGENT,
            },
            method="POST",
        )
        with urlopen(request, timeout=timeout_s) as response:
            result = json.loads(response.read().decode("utf-8"))
        if isinstance(result, dict) and result.get("error"):
            error = result.get("error")
            if isinstance(error, dict):
                raise ValueError(str(error.get("message") or error))
            raise ValueError(str(error))
        if not isinstance(result, dict):
            raise ValueError("RPC response was not a JSON object.")
        return result.get("result")

    def _energy_profile_manifest_path(self, profile: Any) -> Path:
        path = profile.deployment_manifest_path or Path("runtime") / "deployments" / profile.network_key / "latest.json"
        path = Path(path)
        if path.is_absolute():
            return path
        return self.server.debug_root / path

    def _energy_manifest_display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.server.debug_root.resolve()).as_posix()
        except (OSError, ValueError):
            return str(path)

    def _energy_load_manifest_status(self, profile: Any) -> tuple[Path, dict[str, Any] | None, list[str]]:
        path = self._energy_profile_manifest_path(profile)
        warnings: list[str] = []
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return path, None, [f"Deployment manifest is missing: {path}"]
        except json.JSONDecodeError as exc:
            return path, None, [f"Deployment manifest is not valid JSON: {exc}"]
        if not isinstance(manifest, dict):
            return path, None, ["Deployment manifest root is not an object."]
        return path, manifest, warnings

    def _energy_contract_map(self, manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not isinstance(manifest, dict):
            return {}
        raw = manifest.get("contracts")
        if not isinstance(raw, dict):
            raw = manifest.get("deployments")
        if not isinstance(raw, dict):
            return {}
        contracts: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                contracts[str(key)] = value
        return contracts

    def _energy_contract_inventory(
        self,
        *,
        contracts: dict[str, dict[str, Any]],
        rpc_url: str,
        live: bool,
        rpc_reachable: bool,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        expected_keys = [key for key, _label in self._ENERGY_EXPECTED_CONTRACTS]
        extra_keys = sorted(key for key in contracts if key not in expected_keys)
        inventory: list[dict[str, Any]] = []
        for key, label in [*self._ENERGY_EXPECTED_CONTRACTS, *[(extra, extra) for extra in extra_keys]]:
            raw = contracts.get(key) or {}
            address = str(raw.get("address") or "").strip()
            code_bytes = None
            has_code = None
            code_error = ""
            if key in expected_keys and not raw:
                warnings.append(f"{label} is missing from the deployment manifest.")
            elif not address:
                warnings.append(f"{label} has no deployed address in the manifest.")
            if live and rpc_reachable and address and rpc_url:
                try:
                    code = self._energy_rpc_call(rpc_url, "eth_getCode", [address, "latest"])
                    code_text = str(code or "0x")
                    has_code = code_text not in {"", "0x", "0X"}
                    code_bytes = max(0, (len(code_text.removeprefix("0x").removeprefix("0X")) // 2))
                    if not has_code:
                        warnings.append(f"{label} has no bytecode at {address}.")
                except Exception as exc:
                    code_error = str(exc)
                    warnings.append(f"{label} bytecode check failed: {code_error}")
            inventory.append(
                {
                    "key": key,
                    "label": label,
                    "address": address,
                    "target": str(raw.get("target") or ""),
                    "transaction_hash": str(raw.get("transaction_hash") or ""),
                    "configured": bool(raw),
                    "has_code": has_code,
                    "code_bytes": code_bytes,
                    "code_error": code_error,
                }
            )
        return inventory, warnings

    def _energy_authority_summary(self, manifest: dict[str, Any] | None, *, network_key: str, kind: str) -> tuple[list[dict[str, Any]], list[str], bool]:
        raw_offices = manifest.get("offices") if isinstance(manifest, dict) else None
        offices: list[dict[str, Any]] = []
        warnings: list[str] = []
        default_offices: list[str] = []
        if isinstance(raw_offices, list):
            for office in raw_offices:
                if not isinstance(office, dict):
                    continue
                address = str(office.get("address") or "").strip()
                normalized = address.lower()
                is_default = normalized in self._ANVIL_DEFAULT_OFFICES
                if is_default:
                    default_offices.append(str(office.get("title") or office.get("office") or address))
                offices.append(
                    {
                        "office": str(office.get("office") or ""),
                        "title": str(office.get("title") or ""),
                        "address": address,
                        "default_anvil": is_default,
                    }
                )
        elif manifest is not None:
            warnings.append("Deployment manifest does not include office authority.")
        if default_offices and str(kind).lower() in {"mainnet", "testnet"}:
            warnings.append(
                f"{network_key} authority is unsafe: {', '.join(default_offices)} match default Anvil office identities."
            )
        elif default_offices and str(kind).lower() == "test":
            warnings.append(
                f"{network_key} is using default Anvil office identities for local validation."
            )
        return offices, warnings, bool(default_offices and str(kind).lower() in {"mainnet", "testnet"})

    def _energy_network_status(self, profile: Any, *, live: bool) -> dict[str, Any]:
        manifest_path, manifest, warnings = self._energy_load_manifest_status(profile)
        manifest_chain = manifest.get("chain") if isinstance(manifest, dict) else {}
        if not isinstance(manifest_chain, dict):
            manifest_chain = {}
        manifest_environment = str(manifest.get("environment") or "") if isinstance(manifest, dict) else ""
        manifest_chain_id = self._energy_hex_to_int(manifest_chain.get("chain_id")) if manifest_chain else None
        run_id = str(manifest.get("run_id") or "") if isinstance(manifest, dict) else ""
        created_at = str(manifest.get("created_at") or "") if isinstance(manifest, dict) else ""
        source = manifest.get("source") if isinstance(manifest, dict) else {}
        if not isinstance(source, dict):
            source = {}
        source_kind = str(source.get("kind") or source.get("source_kind") or "")
        rpc_url = str(profile.chain_rpc_url or manifest_chain.get("host_rpc_url") or manifest_chain.get("rpc_url") or "").strip()
        expected_chain_id = profile.chain_id

        if manifest is not None and manifest_environment and manifest_environment != profile.network_key:
            warnings.append(f"Manifest environment {manifest_environment!r} does not match registry network {profile.network_key!r}.")
        if expected_chain_id is not None and manifest_chain_id is not None and int(expected_chain_id) != int(manifest_chain_id):
            warnings.append(f"Manifest chain id {manifest_chain_id} does not match expected chain id {expected_chain_id}.")

        live_chain_id = None
        block_number = None
        rpc_reachable = False
        rpc_error = ""
        if live and rpc_url:
            try:
                live_chain_id = self._energy_hex_to_int(self._energy_rpc_call(rpc_url, "eth_chainId"))
                block_number = self._energy_hex_to_int(self._energy_rpc_call(rpc_url, "eth_blockNumber"))
                rpc_reachable = True
                if expected_chain_id is not None and live_chain_id is not None and int(live_chain_id) != int(expected_chain_id):
                    warnings.append(f"Live RPC chain id {live_chain_id} does not match expected chain id {expected_chain_id}.")
            except Exception as exc:
                rpc_error = str(exc)
                warnings.append(f"RPC unreachable: {rpc_error}")
        elif live and not rpc_url:
            warnings.append("No RPC URL is configured for this network.")

        contracts, contract_warnings = self._energy_contract_inventory(
            contracts=self._energy_contract_map(manifest),
            rpc_url=rpc_url,
            live=live,
            rpc_reachable=rpc_reachable,
        )
        warnings.extend(contract_warnings)
        offices, authority_warnings, unsafe_authority = self._energy_authority_summary(
            manifest,
            network_key=profile.network_key,
            kind=profile.kind,
        )
        warnings.extend(authority_warnings)

        missing_manifest = manifest is None
        chain_mismatch = any("chain id" in warning and "does not match" in warning for warning in warnings)
        contract_missing = any(
            contract["key"] in {key for key, _label in self._ENERGY_EXPECTED_CONTRACTS}
            and (not contract["configured"] or contract.get("has_code") is False)
            for contract in contracts
        )
        if unsafe_authority or chain_mismatch:
            overall = "unsafe"
        elif missing_manifest or (live and not rpc_reachable) or contract_missing or warnings:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "network": profile.network_key,
            "network_key": profile.network_key,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "rank": "primary" if profile.network_key == "mainnet" else "secondary",
            "expected_chain_id": expected_chain_id,
            "configured_rpc_url": rpc_url,
            "hub_url": profile.hub_url,
            "deployment_manifest_path": self._energy_manifest_display_path(manifest_path),
            "deployment_manifest_absolute_path": str(manifest_path),
            "manifest_present": manifest is not None,
            "manifest_environment": manifest_environment,
            "manifest_chain_id": manifest_chain_id,
            "run_id": run_id,
            "created_at": created_at,
            "source_kind": source_kind,
            "rpc_reachable": rpc_reachable,
            "rpc_error": rpc_error,
            "live_chain_id": live_chain_id,
            "chain_id_ok": (
                expected_chain_id is not None
                and live_chain_id is not None
                and int(expected_chain_id) == int(live_chain_id)
            )
            if live
            else None,
            "block_number": block_number,
            "contracts": contracts,
            "offices": offices,
            "warnings": warnings,
            "overall_status": overall,
            "read_only": True,
            "mutation_policy": "monitor-only",
        }

    def _handle_energy_networks_status(self) -> None:
        try:
            live = self._energy_bool_query("live", default=True)
            registry = load_hub_network_registry()
            ordered = [key for key in self._ENERGY_NETWORK_ORDER if key in registry.networks]
            ordered.extend(key for key in registry.networks if key not in ordered)
            networks = [self._energy_network_status(registry.networks[key], live=live) for key in ordered]
            selected = registry.default_network if registry.default_network in registry.networks else ordered[0]
            self.server.signal("api-energy-networks-status", live=live, selected=selected, networks=len(networks))
            self._send_json(
                {
                    "ok": True,
                    "schema": "main-computer.energy-networks.status.v1",
                    "mode": "read-only-monitor",
                    "default_network": selected,
                    "live": live,
                    "networks": networks,
                    "summary": {
                        "total": len(networks),
                        "healthy": sum(1 for network in networks if network["overall_status"] == "healthy"),
                        "degraded": sum(1 for network in networks if network["overall_status"] == "degraded"),
                        "unsafe": sum(1 for network in networks if network["overall_status"] == "unsafe"),
                    },
                }
            )
        except Exception as exc:
            self.server.signal("api-energy-networks-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_energy_register_node(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.register_node(
                node_id=str(body.get("node_id", "")),
                role=str(body.get("role", "worker")),
                endpoint=str(body.get("endpoint", "")),
            )
            self.server.signal("api-energy-register-node", node_id=body.get("node_id", ""))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-register-node-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_energy_issue(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.issue(
                node_id=str(body.get("node_id", "")),
                credits=int(body.get("credits", 0)),
                memo=str(body.get("memo", "")),
            )
            self.server.signal("api-energy-issue", node_id=body.get("node_id", ""), credits=body.get("credits", 0))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-issue-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_energy_spend(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return
            report = self.server.energy_ledger.spend(
                node_id=str(body.get("node_id", "")),
                credits=int(body.get("credits", 0)),
                memo=str(body.get("memo", "")),
            )
            self.server.signal("api-energy-spend", node_id=body.get("node_id", ""), credits=body.get("credits", 0))
            self._send_json(report)
        except Exception as exc:
            self.server.signal("api-energy-spend-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_hub_config_status(self) -> None:
        self.server.signal("api-hub-config-status")
        self._send_json(self._hub_config_payload())

    def _handle_hub_config_save(self) -> None:
        try:
            body = self._read_json()
            if not self._energy_passcode_ok(body):
                self._send_json({"error": "Energy admin passcode is required."}, status=HTTPStatus.FORBIDDEN)
                return

            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            hub_client_node_id = str(body.get("hub_client_node_id") or self.server.config.hub_client_node_id).strip()
            if not hub_client_node_id:
                raise ValueError("Hub client node id is required.")
            hub_high_security = self._coerce_bool(body.get("hub_high_security"), default=self.server.config.hub_high_security)
            try:
                hub_timeout_s = max(1.0, float(body.get("hub_timeout_s", self.server.config.hub_timeout_s)))
            except (TypeError, ValueError) as exc:
                raise ValueError("Hub timeout must be a number.") from exc

            new_config = replace(
                self.server.config,
                hub_url=hub_url,
                hub_client_node_id=hub_client_node_id,
                hub_high_security=hub_high_security,
                hub_timeout_s=hub_timeout_s,
            )
            self.server.config = new_config

            saved = self._save_hub_config(
                {
                    "hub_url": hub_url,
                    "hub_client_node_id": hub_client_node_id,
                    "hub_high_security": hub_high_security,
                    "hub_timeout_s": hub_timeout_s,
                    "upstream_hub_url": self._clean_hub_url(str(body.get("upstream_hub_url") or ""), allow_empty=True),
                }
            )

            connect_report = None
            if self._coerce_bool(body.get("connect_upstream"), default=False):
                upstream_hub_url = saved.get("upstream_hub_url", "")
                if not upstream_hub_url:
                    raise ValueError("Upstream hub URL is required to connect an upstream hub.")
                connect_report = self._register_upstream_hub(
                    local_hub_url=hub_url,
                    upstream_hub_url=str(upstream_hub_url),
                    node_id=str(body.get("upstream_node_id") or "upstream-hub"),
                    credits_per_request=int(body.get("upstream_credits_per_request", self.server.config.hub_credits_per_request) or 1),
                )

            payload = self._hub_config_payload()
            payload["saved"] = saved
            payload["connect_report"] = connect_report
            self.server.signal(
                "api-hub-config-save",
                provider=self.server.config.provider,
                hub_url=hub_url,
                connected=bool(connect_report),
            )
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-hub-config-save-error", error=exc)
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_ui_client_is_local(self) -> bool:
        host = self.client_address[0] if self.client_address else ""
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return host.lower() in {"localhost"}


    def _worker_settings_path(self) -> Path:
        return self.server.debug_root / "worker_settings.json"

    def _worker_credit_amount_text(self, value: Any, default: str | None = None) -> str:
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        amount_wei = credit_decimal_text_to_wei(value, default=default_text, minimum_wei=1)
        return credit_wei_to_decimal_text(amount_wei)

    def _worker_credit_amount_wei_text(self, value: Any, default: str | None = None, *, value_is_wei: bool = False) -> str:
        if value_is_wei:
            try:
                parsed = int(str(value).strip())
                if parsed > 0:
                    return str(parsed)
            except (TypeError, ValueError):
                pass
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        return str(credit_decimal_text_to_wei(value, default=default_text, minimum_wei=1))

    def _worker_legacy_credit_amount_ceiling_text(self, value_wei: Any, default: str | None = None) -> str:
        """Return an integer credit string for legacy Hub fields.

        Older Hubs parse ``credits_per_request`` with ``int(...)`` even when the
        newer pricing payload also carries precise wei and fractional display
        values.  Keep the precise values in pricing fields, but make the legacy
        top-level field conservative and integer-compatible.
        """

        try:
            amount_wei = int(str(value_wei).strip())
        except (TypeError, ValueError):
            default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
            amount_wei = credit_decimal_text_to_wei(default_text, default=default_text, minimum_wei=1)
        credit_base = 10**18
        return str(max(1, (max(1, amount_wei) + credit_base - 1) // credit_base))

    def _worker_seller_credit_amount_text(self, value: Any, default: str | None = None) -> str:
        raw_text = str(value if value is not None else "").strip().replace(",", "")
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_REQUEST)
        if not raw_text or raw_text in self._WORKER_LEGACY_CREDITS_PER_REQUESTS:
            return self._worker_credit_amount_text(default_text, default_text)
        return self._worker_credit_amount_text(value, default_text)

    def _worker_seller_credit_per_token_text(self, value: Any, default: str | None = None) -> str:
        raw_text = str(value if value is not None else "").strip().replace(",", "")
        default_text = str(default or self._WORKER_DEFAULT_CREDITS_PER_TOKEN)
        if not raw_text or raw_text in self._WORKER_LEGACY_CREDITS_PER_REQUESTS:
            return self._worker_credit_amount_text(default_text, default_text)
        return self._worker_credit_amount_text(value, default_text)

    def _worker_estimated_request_credits_from_token_rate(self, credits_per_token: Any, target_output_tokens: Any) -> tuple[str, str]:
        try:
            token_count = int(target_output_tokens)
        except (TypeError, ValueError):
            token_count = self._WORKER_DEFAULT_SELLER_TARGET_TOKENS
        token_count = min(128_000, max(1, token_count))
        credits_per_token_wei = credit_decimal_text_to_wei(
            credits_per_token,
            default=self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
            minimum_wei=1,
        )
        request_wei = credits_per_token_wei * token_count
        return credit_wei_to_decimal_text(request_wei), str(request_wei)

    def _worker_seller_model_text(self, value: Any) -> str:
        if isinstance(value, list):
            models = [str(item).strip() for item in value if str(item).strip()]
        else:
            models = [item.strip() for item in str(value if value is not None else "").split(",") if item.strip()]
        if not models:
            return self._WORKER_DEFAULT_SELLER_MODEL
        if len(models) == 1 and models[0] in self._WORKER_LEGACY_SELLER_MODELS:
            return self._WORKER_DEFAULT_SELLER_MODEL
        return ",".join(dict.fromkeys(models))

    def _normalize_worker_seller_availability_mode(self, value: Any, *, default: str = _WORKER_SELLER_AVAILABILITY_TOTAL_IDLE) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in self._WORKER_SELLER_AVAILABILITY_MODES:
            return normalized
        fallback = str(default or "").strip().lower()
        if fallback in self._WORKER_SELLER_AVAILABILITY_MODES:
            return fallback
        return self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE

    def _sanitize_worker_settings(self, value: Any) -> dict[str, Any]:
        settings = value.get("settings") if isinstance(value, dict) and isinstance(value.get("settings"), dict) else value
        if not isinstance(settings, dict):
            settings = {}

        def boolish(raw: Any, default: bool = False) -> bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw or "").strip().lower()
            if text in {"1", "true", "yes", "on", "enabled", "enable"}:
                return True
            if text in {"0", "false", "no", "off", "disabled", "disable"}:
                return False
            return bool(default)

        def intish(raw: Any, default: int, *, minimum: int = 0, maximum: int = 1_000_000_000) -> int:
            try:
                number = int(raw)
            except (TypeError, ValueError):
                number = default
            return min(maximum, max(minimum, number))

        def text(raw: Any, default: str = "") -> str:
            return str(raw if raw is not None else default).strip()

        def jsonable(raw: Any, default: Any) -> Any:
            try:
                value = json.loads(json.dumps(raw, ensure_ascii=False))
            except (TypeError, ValueError):
                return default
            return value if isinstance(value, type(default)) else default

        selected_network = text(settings.get("selectedNetwork", settings.get("selected_network")), "none").lower()
        if selected_network not in {"mainnet", "testnet", "test", "dev", "none"}:
            selected_network = "none"
        requested_ring = text(settings.get("workerRequestedRing", settings.get("worker_requested_ring")), "3")
        if requested_ring not in {"0", "1", "2", "3"}:
            requested_ring = "3"
        connection_status = text(settings.get("workerConnectionStatus", settings.get("worker_connection_status")), "disconnected")
        if connection_status not in {"disconnected", "connecting", "connected", "failed", "stale"}:
            connection_status = "disconnected"
        runtime_phase = text(settings.get("workerRuntimePhase", settings.get("worker_runtime_phase")), "not_accepting").lower()
        if runtime_phase not in {"not_accepting", "accepting", "draining"}:
            runtime_phase = "not_accepting"
        raw_seller_idle = settings.get(
            "sellerOnlyWhenIdle",
            settings.get("seller_only_when_idle", settings.get("rentalOnlyWhenIdle", settings.get("rental_only_when_idle"))),
        )
        default_availability_mode = self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE if boolish(raw_seller_idle, True) else self._WORKER_SELLER_AVAILABILITY_AI_IDLE
        seller_availability_mode = self._normalize_worker_seller_availability_mode(
            settings.get("sellerAvailabilityMode", settings.get("seller_availability_mode")),
            default=default_availability_mode,
        )
        seller_only_when_idle = seller_availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        signed_connection = settings.get("signedWorkerConnection", settings.get("signed_worker_connection"))
        if isinstance(signed_connection, dict):
            signed_assigned_ring = text(signed_connection.get("assigned_ring"), "")
            if signed_assigned_ring not in {"0", "1", "2", "3"}:
                signed_assigned_ring = ""
            signed_wallet = text(signed_connection.get("wallet_address"), "")
            signed_message = text(signed_connection.get("message"), "")
            signed_signature = text(signed_connection.get("signature"), "")
            legacy_status = text(signed_connection.get("status"), "signed")
            hub_registered = boolish(signed_connection.get("hub_registered"), False)
            hub_registration_error = text(
                signed_connection.get(
                    "hub_registration_error",
                    signed_connection.get("registration_error", signed_connection.get("last_error")),
                ),
                "",
            )
            worker_start_status = text(signed_connection.get("worker_start_status"), "")
            signed_order_status = text(signed_connection.get("signed_order_status"), worker_start_status)
            if signed_order_status == "signed_locally":
                signed_order_status = "ready"
            elif signed_order_status == "signing":
                signed_order_status = "starting"
            elif signed_order_status == "expired":
                signed_order_status = "invalid"
            if signed_order_status not in {"not_started", "starting", "ready", "invalid"}:
                if signed_wallet and (signed_message and signed_signature or signed_connection.get("status") in {"ready", "registering-with-hub", "hub-registered", "hub-registration-failed"}):
                    signed_order_status = "ready"
                else:
                    signed_order_status = "not_started"
            if not worker_start_status:
                worker_start_status = signed_order_status
            hub_registration_status = text(signed_connection.get("hub_registration_status"), "")
            if hub_registration_status not in {"not_submitted", "submitting", "accepted", "failed", "stale"}:
                legacy_status_lower = legacy_status.lower()
                if hub_registered or legacy_status_lower in {"hub-registered", "registered"}:
                    hub_registration_status = "accepted"
                elif hub_registration_error or legacy_status_lower in {"failed", "hub-registration-failed", "registration-failed"}:
                    hub_registration_status = "failed"
                else:
                    hub_registration_status = "not_submitted"
            signed_connection = {
                "network": text(signed_connection.get("network"), selected_network),
                "requested_ring": text(signed_connection.get("requested_ring"), requested_ring),
                "wallet_address": signed_wallet,
                "credit_wallet": text(signed_connection.get("credit_wallet"), ""),
                "hub_url": self._clean_hub_url(text(signed_connection.get("hub_url"), ""), allow_empty=True),
                "chain_id": text(signed_connection.get("chain_id"), ""),
                "message": signed_message,
                "signature": signed_signature,
                "issued_at": text(signed_connection.get("issued_at"), ""),
                "signed_at": text(signed_connection.get("signed_at"), ""),
                "expires_at": text(signed_connection.get("expires_at"), ""),
                "status": legacy_status,
                "worker_start_status": worker_start_status,
                "signed_order_status": signed_order_status,
                "hub_registration_status": hub_registration_status,
                "hub_registration_attempted_at": text(signed_connection.get("hub_registration_attempted_at"), ""),
                "hub_registered_at": text(signed_connection.get("hub_registered_at"), ""),
                "hub_registered": hub_registered,
                "assigned_ring": signed_assigned_ring,
                "worker_id": text(signed_connection.get("worker_id"), ""),
                "pricing_policy": text(signed_connection.get("pricing_policy"), ""),
                "multisession_key_id": text(
                    signed_connection.get("multisession_key_id", signed_connection.get("active_multisession_key_id")),
                    "",
                ),
                "hub_registration_error": hub_registration_error,
                "registration_error": text(signed_connection.get("registration_error"), ""),
                "last_error": text(signed_connection.get("last_error"), ""),
                "hub_registration": jsonable(signed_connection.get("hub_registration"), {}),
                "worker": jsonable(signed_connection.get("worker"), {}),
                "pool": jsonable(signed_connection.get("pool"), {}),
            }
        else:
            signed_connection = {}

        assigned_ring = text(settings.get("workerAssignedRing", settings.get("worker_assigned_ring")), "")
        if assigned_ring not in {"0", "1", "2", "3"}:
            assigned_ring = ""
        cleaned: dict[str, Any] = {
            "selectedNetwork": selected_network,
            "workerRequestedRing": requested_ring,
            "workerAssignedRing": assigned_ring,
            "workerRegisteredId": text(settings.get("workerRegisteredId", settings.get("worker_registered_id")), ""),
            "workerPricingPolicy": text(settings.get("workerPricingPolicy", settings.get("worker_pricing_policy")), ""),
            "workerHubRegistration": jsonable(settings.get("workerHubRegistration", settings.get("worker_hub_registration")), {}),
            "workerPool": jsonable(settings.get("workerPool", settings.get("worker_pool")), {}),
            "workerConnectionStatus": connection_status,
            "workerConnectedAt": text(settings.get("workerConnectedAt", settings.get("worker_connected_at")), ""),
            "workerConnectionError": text(settings.get("workerConnectionError", settings.get("worker_connection_error")), ""),
            "workerConnectedHubUrl": self._clean_hub_url(text(settings.get("workerConnectedHubUrl", settings.get("worker_connected_hub_url")), ""), allow_empty=True),
            "signedWorkerConnection": signed_connection,
            "workerRuntimeEnabled": boolish(settings.get("workerRuntimeEnabled", settings.get("worker_runtime_enabled")), False),
            "workerRuntimePhase": runtime_phase,
            "workerRuntimeActiveJobs": intish(settings.get("workerRuntimeActiveJobs", settings.get("worker_runtime_active_jobs")), 0, minimum=0, maximum=1024),
            "workerRuntimeLastReason": text(settings.get("workerRuntimeLastReason", settings.get("worker_runtime_last_reason")), ""),
            "workerRuntimeLastCheckedAt": text(settings.get("workerRuntimeLastCheckedAt", settings.get("worker_runtime_last_checked_at")), ""),
            "workerRuntimeLastConnectedAt": text(settings.get("workerRuntimeLastConnectedAt", settings.get("worker_runtime_last_connected_at")), ""),
            "workerRuntimeLastDisconnectedAt": text(settings.get("workerRuntimeLastDisconnectedAt", settings.get("worker_runtime_last_disconnected_at")), ""),
            "workerRuntimeLastHeartbeatAt": text(settings.get("workerRuntimeLastHeartbeatAt", settings.get("worker_runtime_last_heartbeat_at")), ""),
            "workerRuntimeLastHeartbeatStatus": text(settings.get("workerRuntimeLastHeartbeatStatus", settings.get("worker_runtime_last_heartbeat_status")), ""),
            "workerRuntimeError": text(settings.get("workerRuntimeError", settings.get("worker_runtime_error")), ""),
            "workerWorkNowOverrideStartedAt": text(settings.get("workerWorkNowOverrideStartedAt", settings.get("worker_work_now_override_started_at")), ""),
            "workerWorkNowOverrideExpiresAt": text(settings.get("workerWorkNowOverrideExpiresAt", settings.get("worker_work_now_override_expires_at")), ""),
            "workerWorkNowOverrideDurationSeconds": intish(
                settings.get("workerWorkNowOverrideDurationSeconds", settings.get("worker_work_now_override_duration_seconds")),
                0,
                minimum=0,
                maximum=7 * 24 * 60 * 60,
            ),
            "workerWorkNowFinishRequestedAt": text(settings.get("workerWorkNowFinishRequestedAt", settings.get("worker_work_now_finish_requested_at")), ""),
            "remoteEnabled": boolish(settings.get("remoteEnabled", settings.get("remote_enabled")), False),
            "remoteMode": text(settings.get("remoteMode", settings.get("remote_mode")), "ask-when-busy"),
            "remoteCreditsPerToken": text(settings.get("remoteCreditsPerToken", settings.get("remote_credits_per_token")), "0.001"),
            "remoteMaxOutputTokens": intish(settings.get("remoteMaxOutputTokens", settings.get("remote_max_output_tokens")), 1024, minimum=1, maximum=128_000),
            "remoteDailyLimit": intish(settings.get("remoteDailyLimit", settings.get("remote_daily_limit")), 100000, minimum=0),
            "remoteAskBeforeSpend": boolish(settings.get("remoteAskBeforeSpend", settings.get("remote_ask_before_spend")), False),
            "remoteOnlyWhenBusy": boolish(settings.get("remoteOnlyWhenBusy", settings.get("remote_only_when_busy")), False),
            "sellerEnabled": boolish(settings.get("sellerEnabled", settings.get("seller_enabled")), False),
            "rentalEnabled": boolish(settings.get("rentalEnabled", settings.get("rental_enabled")), False),
            "sellerAvailabilityMode": seller_availability_mode,
            "sellerOnlyWhenIdle": seller_only_when_idle,
            "rentalOnlyWhenIdle": seller_only_when_idle,
            "registrationHubUrl": self._clean_hub_url(text(settings.get("registrationHubUrl", settings.get("registration_hub_url")), self.server.config.hub_url), allow_empty=True),
            "nodeId": text(settings.get("nodeId", settings.get("node_id")), "local-worker-001"),
            "endpoint": text(settings.get("endpoint"), "http://127.0.0.1:8771"),
            "models": self._worker_seller_model_text(settings.get("models", self._WORKER_DEFAULT_SELLER_MODEL)),
            "sellerTargetTokens": intish(
                settings.get(
                    "sellerTargetTokens",
                    settings.get("seller_target_tokens", settings.get("targetOutputTokens", settings.get("target_output_tokens"))),
                ),
                self._WORKER_DEFAULT_SELLER_TARGET_TOKENS,
                minimum=1,
                maximum=128_000,
            ),
            "capability": text(settings.get("capability"), "chat.completions"),
            "sellerCreditsPerToken": self._worker_seller_credit_per_token_text(
                settings.get(
                    "sellerCreditsPerToken",
                    settings.get("seller_credits_per_token", settings.get("creditsPerToken", settings.get("credits_per_token", settings.get("creditsPerRequest", settings.get("credits_per_request"))))),
                ),
                self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
            ),
            "maxConcurrency": intish(settings.get("maxConcurrency", settings.get("max_concurrency")), 1, minimum=1, maximum=1024),
            "executionMode": text(settings.get("executionMode", settings.get("execution_mode")), "worker_pull_v0"),
        }
        hubs = settings.get("hubs")
        if isinstance(hubs, list):
            cleaned["hubs"] = [
                {
                    "name": text(hub.get("name"), "Hub"),
                    "url": text(hub.get("url"), ""),
                    "role": text(hub.get("role"), "use-provide"),
                }
                for hub in hubs
                if isinstance(hub, dict) and (text(hub.get("name")) or text(hub.get("url")))
            ]
        else:
            cleaned["hubs"] = []
        return cleaned

    def _load_worker_settings(self) -> dict[str, Any]:
        path = self._worker_settings_path()
        try:
            if not path.exists():
                return {}
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._sanitize_worker_settings(data)
        except Exception:
            return {}

    def _save_worker_settings(self, settings: dict[str, Any], *, changed_fields: list[str] | None = None) -> dict[str, Any]:
        incoming = self._sanitize_worker_settings(settings)
        allowed_changes = {str(field or "").strip() for field in (changed_fields or []) if str(field or "").strip()}
        if allowed_changes:
            cleaned = self._sanitize_worker_settings(self._load_worker_settings())
            for key in allowed_changes:
                if key in incoming:
                    cleaned[key] = incoming[key]
            cleaned = self._sanitize_worker_settings(cleaned)
        else:
            cleaned = incoming
        path = self._worker_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return cleaned

    def _handle_worker_settings_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker settings are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            self.server.signal("api-worker-settings-load", saved=bool(settings), remote_enabled=bool(settings.get("remoteEnabled")))
            self._send_json({"ok": True, "settings": settings})
        except Exception as exc:
            self.server.signal("api-worker-settings-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_settings_save(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker settings are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            changed_fields = body.get("changed_fields") if isinstance(body, dict) else None
            if not isinstance(changed_fields, list):
                changed_fields = None
            settings = self._save_worker_settings(body, changed_fields=changed_fields)
            self.server.signal("api-worker-settings-save", remote_enabled=bool(settings.get("remoteEnabled")))
            self._send_json({"ok": True, "settings": settings})
        except Exception as exc:
            self.server.signal("api-worker-settings-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_hub_health(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker hub checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            status = self._fetch_hub_status(hub_url)
            self.server.signal("api-worker-hub-health", hub_url=hub_url, reachable=status.get("reachable"))
            self._send_json({"ok": True, "hub_url": hub_url, "status": status, "reachable": bool(status.get("reachable"))})
        except Exception as exc:
            self.server.signal("api-worker-hub-health-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _worker_runtime_signed_connection_registered(self, settings: dict[str, Any]) -> bool:
        signed = settings.get("signedWorkerConnection")
        if not isinstance(signed, dict):
            return False
        explicit_hub_status = str(signed.get("hub_registration_status") or "").strip()
        if explicit_hub_status:
            return explicit_hub_status == "accepted" and bool(signed.get("hub_registered"))
        if str(signed.get("status") or "") in {"hub-registered", "registered"}:
            return True
        return bool(signed.get("hub_registered"))

    def _worker_runtime_worker_id(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        return str(
            signed.get("worker_id")
            or worker.get("worker_id")
            or worker.get("node_id")
            or settings.get("workerRegisteredId")
            or settings.get("nodeId")
            or ""
        ).strip()

    def _worker_runtime_instance_id(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        worker_id = self._worker_runtime_worker_id(settings)
        return str(
            worker.get("worker_instance_id")
            or signed.get("worker_instance_id")
            or worker_id
        ).strip()

    def _worker_runtime_hub_url(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        return self._clean_hub_url(
            str(settings.get("workerConnectedHubUrl") or signed.get("hub_url") or settings.get("registrationHubUrl") or self.server.config.hub_url),
            allow_empty=True,
        )

    def _worker_local_ai_capacity_snapshot(self, *, max_local_concurrency: int = 1) -> dict[str, Any]:
        manager = getattr(getattr(self, "server", None), "chat_ai_processes", None)
        snapshot_method = getattr(manager, "local_ai_capacity_snapshot", None)
        if callable(snapshot_method):
            snapshot = snapshot_method(thread_id="", max_local_concurrency=max_local_concurrency)
            return snapshot if isinstance(snapshot, dict) else {"ok": False, "available_now": False, "busy": True, "reason_code": "invalid_local_ai_capacity"}
        return {
            "ok": True,
            "scope": "local-ai",
            "available_now": True,
            "busy": False,
            "reason_code": "local_ai_capacity_unavailable_assumed_idle",
            "user_message": "Local AI capacity monitor is unavailable; assuming idle for this app-level worker check.",
            "active_run_count": 0,
            "max_local_concurrency": max(1, int(max_local_concurrency or 1)),
            "active_thread_ids": [],
            "active_runs": [],
        }

    def _parse_worker_utc_datetime(self, value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _worker_work_now_override_state(self, settings: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        now_dt = now or datetime.now(timezone.utc)
        started_at = str(settings.get("workerWorkNowOverrideStartedAt") or "").strip()
        expires_dt = self._parse_worker_utc_datetime(settings.get("workerWorkNowOverrideExpiresAt"))
        duration_seconds = max(0, int(settings.get("workerWorkNowOverrideDurationSeconds") or 0))
        remaining_seconds = 0
        active = False
        expires_at = ""
        if expires_dt is not None:
            expires_at = expires_dt.isoformat()
            remaining_seconds = max(0, int((expires_dt - now_dt).total_seconds()))
            active = remaining_seconds > 0
        return {
            "active": active,
            "started_at": started_at,
            "startedAt": started_at,
            "expires_at": expires_at,
            "expiresAt": expires_at,
            "duration_seconds": duration_seconds,
            "durationSeconds": duration_seconds,
            "remaining_seconds": remaining_seconds,
            "remainingSeconds": remaining_seconds,
            "finish_requested_at": str(settings.get("workerWorkNowFinishRequestedAt") or "").strip(),
            "finishRequestedAt": str(settings.get("workerWorkNowFinishRequestedAt") or "").strip(),
        }

    def _worker_set_work_now_override(self, settings: dict[str, Any], *, duration_seconds: int) -> dict[str, Any]:
        duration = max(60, min(int(duration_seconds or 0), 7 * 24 * 60 * 60))
        now_dt = datetime.now(timezone.utc)
        expires_dt = datetime.fromtimestamp(now_dt.timestamp() + duration, tz=timezone.utc)
        settings["workerWorkNowOverrideStartedAt"] = now_dt.isoformat()
        settings["workerWorkNowOverrideExpiresAt"] = expires_dt.isoformat()
        settings["workerWorkNowOverrideDurationSeconds"] = duration
        settings["workerWorkNowFinishRequestedAt"] = ""
        return settings

    def _worker_clear_work_now_override(self, settings: dict[str, Any]) -> dict[str, Any]:
        settings["workerWorkNowOverrideStartedAt"] = ""
        settings["workerWorkNowOverrideExpiresAt"] = ""
        settings["workerWorkNowOverrideDurationSeconds"] = 0
        settings["workerWorkNowFinishRequestedAt"] = datetime.now(timezone.utc).isoformat()
        return settings

    def _worker_ring_label(self, value: Any) -> str:
        ring = str(value if value is not None else "").strip()
        if ring == "0":
            return "Ring 0 - Operator / direct whitelist"
        if ring == "1":
            return "Ring 1 - Protected trusted worker"
        if ring == "2":
            return "Ring 2 - Public"
        if ring == "3":
            return "Ring 3 - Public untrusted"
        return ""

    def _worker_runtime_signed_order_state(self, settings: dict[str, Any]) -> dict[str, Any]:
        connection = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        wallet = str(connection.get("wallet_address") or "").strip()
        credit_wallet = str(connection.get("credit_wallet") or wallet).strip()
        has_worker_start = bool(
            wallet
            and connection.get("network")
            and connection.get("requested_ring")
            and str(connection.get("status") or "") in {"ready", "registering-with-hub", "hub-registered", "hub-registration-failed"}
        )
        status = str(connection.get("worker_start_status") or connection.get("signed_order_status") or "").strip()
        if status not in {"not_started", "starting", "ready", "invalid", "signed_locally", "signing", "expired"}:
            status = "ready" if has_worker_start else "not_started"
        if status == "signed_locally":
            status = "ready"
        elif status == "signing":
            status = "starting"
        elif status == "expired":
            status = "invalid"
        if not has_worker_start and status not in {"starting", "invalid"}:
            status = "not_started"
        labels = {
            "not_started": "Not started",
            "starting": "Starting",
            "ready": "Ready",
            "invalid": "Needs restart",
        }
        return {
            "status": status,
            "label": labels.get(status, "Not started"),
            "signedAt": "",
            "expiresAt": "",
            "startedAt": str(connection.get("started_at") or connection.get("signed_at") or ""),
            "wallet": wallet,
            "creditWallet": credit_wallet,
            "rawStatus": str(connection.get("status") or ""),
        }

    def _worker_runtime_hub_registration_state(self, settings: dict[str, Any]) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        registration = settings.get("workerHubRegistration") if isinstance(settings.get("workerHubRegistration"), dict) else {}
        signed_registration = signed.get("hub_registration") if isinstance(signed.get("hub_registration"), dict) else {}
        registration = registration or signed_registration
        raw_status = str(signed.get("status") or "").strip().lower()
        last_error = str(
            signed.get("hub_registration_error")
            or signed.get("registration_error")
            or signed.get("last_error")
            or (registration.get("error") if isinstance(registration, dict) else "")
            or settings.get("workerConnectionError")
            or ""
        ).strip()
        status = str(signed.get("hub_registration_status") or "").strip()
        if status not in {"not_submitted", "submitting", "accepted", "failed", "stale"}:
            registered = self._worker_runtime_signed_connection_registered(settings)
            failed = bool(last_error) or raw_status in {"failed", "hub-registration-failed", "registration-failed"}
            if registered:
                status = "accepted"
            elif failed:
                status = "failed"
            else:
                status = "not_submitted"
        elif status == "accepted" and not self._worker_runtime_signed_connection_registered(settings):
            status = "stale"
        labels = {
            "not_submitted": "Not submitted",
            "submitting": "Submitting",
            "accepted": "Accepted",
            "failed": "Failed",
            "stale": "Stale",
        }
        return {
            "status": status,
            "label": labels.get(status, "Not submitted"),
            "lastError": last_error,
            "attemptedAt": str(signed.get("hub_registration_attempted_at") or ""),
            "registeredAt": str(signed.get("hub_registered_at") or ""),
            "rawStatus": raw_status,
        }

    def _worker_runtime_local_policy(self, settings: dict[str, Any], *, user_activity: dict[str, Any] | None = None, active_jobs: int = 0) -> dict[str, Any]:
        seller_enabled = bool(settings.get("sellerEnabled"))
        availability_mode = self._normalize_worker_seller_availability_mode(settings.get("sellerAvailabilityMode"))
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        local_ai_capacity: dict[str, Any] | None = None
        active_ai_jobs = 0
        reasons: list[str] = []

        if not seller_enabled:
            reasons.append("Accept paid jobs is off.")

        if only_when_idle:
            if user_activity is None:
                user_activity = collect_windows_user_activity()
            active = user_activity.get("active") if isinstance(user_activity, dict) else None
            if active is True:
                reasons.append("Waiting for computer to be idle. Windows reports an active interactive user session.")
            elif active is not False:
                reason = str(user_activity.get("reason") or "idle status unavailable") if isinstance(user_activity, dict) else "idle status unavailable"
                reasons.append(f"Waiting for computer idle status. Windows quser could not verify idle status: {reason}.")
        else:
            user_activity = None
            local_ai_capacity = self._worker_local_ai_capacity_snapshot(max_local_concurrency=1)
            try:
                active_ai_jobs = max(0, int(local_ai_capacity.get("active_run_count", 0) or 0))
            except (TypeError, ValueError):
                active_ai_jobs = 0
            ai_available = bool(local_ai_capacity.get("available_now"))
            # While this app is already running a worker job, the local AI slot is
            # expected to be busy. Keep the session alive as busy instead of
            # interpreting the app's own job as a policy failure.
            if active_jobs <= 0 and not ai_available:
                message = str(local_ai_capacity.get("user_message") or local_ai_capacity.get("reason_code") or "").strip()
                reasons.append(f"Local AI is busy. {message}".strip())

        normal_allowed = not reasons
        normal_reason = ""
        if normal_allowed and only_when_idle:
            normal_reason = "Computer is idle."
        elif normal_allowed:
            normal_reason = "AI is idle."
        else:
            normal_reason = " ".join(reasons)

        work_now_override = self._worker_work_now_override_state(settings)
        override_active = seller_enabled and bool(work_now_override.get("active"))
        allowed = normal_allowed or override_active
        if override_active and normal_allowed:
            reason = f"Work-now override is active until {work_now_override.get('expires_at')}; normal policy also allows work."
            label = "Allowed by Work now"
        elif override_active:
            reason = f"Work-now override is active until {work_now_override.get('expires_at')}; normal policy says: {normal_reason}"
            label = "Allowed by Work now"
        else:
            reason = normal_reason
            label = "Allowed" if allowed else "Blocked"

        return {
            "enabled": seller_enabled,
            "mode": availability_mode,
            "allowed": allowed,
            "normal_allowed": normal_allowed,
            "normalAllowed": normal_allowed,
            "label": label,
            "reason": reason,
            "normal_reason": normal_reason,
            "normalReason": normal_reason,
            "work_now_override": work_now_override,
            "workNowOverride": work_now_override,
            "activeAiJobs": active_ai_jobs,
            "active_ai_jobs": active_ai_jobs,
            "user_activity": user_activity,
            "local_ai_capacity": local_ai_capacity,
            "source": "windows_quser_v1" if only_when_idle else "local_ai_capacity_v1",
        }

    def _worker_runtime_policy(self, settings: dict[str, Any], *, user_activity: dict[str, Any] | None = None, active_jobs: int = 0) -> dict[str, Any]:
        """Return whether the app may currently announce availability to the Hub.

        The runtime decision still requires identity, worker start, Hub registration,
        and a Hub URL.  The nested ``local_policy`` field stays independent so the
        UI can truthfully show that local policy may allow work even while another
        blocker, such as Hub registration, keeps the runtime from accepting work.
        """

        selected_network = str(settings.get("selectedNetwork") or "none")
        worker_id = self._worker_runtime_worker_id(settings)
        hub_url = self._worker_runtime_hub_url(settings)
        signed_order = self._worker_runtime_signed_order_state(settings)
        hub_registration = self._worker_runtime_hub_registration_state(settings)
        local_policy = self._worker_runtime_local_policy(settings, user_activity=user_activity, active_jobs=active_jobs)
        seller_enabled = bool(local_policy.get("enabled"))
        availability_mode = self._normalize_worker_seller_availability_mode(local_policy.get("mode"))
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        signed_ready = signed_order.get("status") == "ready"
        hub_accepted = hub_registration.get("status") == "accepted" and self._worker_runtime_signed_connection_registered(settings)
        requirements = {
            "seller_enabled": seller_enabled,
            "network_selected": selected_network != "none",
            "worker_start": signed_ready,
            "worker_start_status": signed_order.get("status"),
            "hub_registered": hub_accepted,
            "hub_registration_status": hub_registration.get("status"),
            "worker_id_present": bool(worker_id),
            "hub_url_present": bool(hub_url),
            "availability_mode": availability_mode,
            "idle_only": only_when_idle,
            "ai_idle": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
            "local_policy_allowed": bool(local_policy.get("allowed")),
            "local_policy_normal_allowed": bool(local_policy.get("normal_allowed", local_policy.get("allowed"))),
            "work_now_override_active": bool((local_policy.get("work_now_override") if isinstance(local_policy.get("work_now_override"), dict) else {}).get("active")),
        }

        reasons: list[str] = []
        if not seller_enabled:
            reasons.append("Accept paid jobs is off.")
        if selected_network == "none":
            reasons.append("No worker network is selected.")
        if not signed_ready:
            start_status = str(signed_order.get("status") or "not_started")
            if start_status == "starting":
                reasons.append("Multi-session key request is in progress.")
            elif start_status == "invalid":
                reasons.append("Worker registration is not ready.")
            else:
                reasons.append("Worker has not been registered with the Hub.")
        elif not hub_accepted:
            hub_status = str(hub_registration.get("status") or "not_submitted")
            if hub_status == "failed" and hub_registration.get("lastError"):
                reasons.append(f"Hub registration failed: {hub_registration['lastError']}")
            elif hub_status == "failed":
                reasons.append("Hub registration failed.")
            elif hub_status == "submitting":
                reasons.append("Worker registration is being submitted to the Hub.")
            elif hub_status == "stale":
                reasons.append("Hub registration is stale.")
            else:
                reasons.append("Worker registration has not been submitted to the Hub.")
        if hub_accepted and not worker_id:
            reasons.append("Worker ID is missing.")
        if selected_network != "none" and not hub_url:
            reasons.append("Hub URL is missing.")
        if not bool(local_policy.get("allowed")) and str(local_policy.get("reason") or "") not in reasons:
            reasons.append(str(local_policy.get("reason") or "Local policy blocks work."))

        allowed = not reasons
        work_now_override = local_policy.get("work_now_override") if isinstance(local_policy.get("work_now_override"), dict) else self._worker_work_now_override_state(settings)
        override_active = bool(work_now_override.get("active"))
        return {
            "allowed_to_accept": allowed,
            "reason": (
                "Hub registration accepted and Work-now override allows work."
                if allowed and override_active and not bool(local_policy.get("normal_allowed", local_policy.get("allowed")))
                else "Hub registration accepted and local policy allows work."
                if allowed
                else " ".join(reason for reason in reasons if reason)
            ),
            "requirements": requirements,
            "user_activity": local_policy.get("user_activity"),
            "local_ai_capacity": local_policy.get("local_ai_capacity"),
            "work_now_override": local_policy.get("work_now_override") if isinstance(local_policy.get("work_now_override"), dict) else self._worker_work_now_override_state(settings),
            "workNowOverride": local_policy.get("work_now_override") if isinstance(local_policy.get("work_now_override"), dict) else self._worker_work_now_override_state(settings),
            "availability_mode": availability_mode,
            "source": "windows_quser_v1" if only_when_idle else "local_ai_capacity_v1",
            "local_policy": local_policy,
            "signed_order": signed_order,
            "hub_registration": hub_registration,
        }

    def _worker_runtime_models(self, settings: dict[str, Any]) -> list[str]:
        models = [item.strip() for item in self._worker_seller_model_text(settings.get("models")).split(",") if item.strip()]
        return models or [self._WORKER_DEFAULT_SELLER_MODEL]

    def _worker_runtime_ring_partition(self, settings: dict[str, Any]) -> str:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        signed_worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        capabilities = signed_worker.get("capabilities") if isinstance(signed_worker.get("capabilities"), dict) else {}
        raw_ring = (
            settings.get("workerAssignedRing")
            or signed.get("assigned_ring")
            or signed_worker.get("assigned_ring")
            or capabilities.get("assigned_ring")
            or settings.get("workerRequestedRing")
            or signed.get("requested_ring")
            or "3"
        )
        text = str(raw_ring or "3").strip().lower()
        if text.startswith("ring-"):
            text = text[5:]
        if text not in {"0", "1", "2", "3"}:
            text = "3"
        return f"ring-{text}"

    def _worker_runtime_market_profile(
        self,
        *,
        settings: dict[str, Any],
        capabilities: dict[str, Any],
        models: list[str],
        active_jobs: int,
    ) -> dict[str, Any]:
        pricing = capabilities.get("pricing") if isinstance(capabilities.get("pricing"), dict) else {}
        price_wei = str(
            pricing.get("credits_per_request_wei")
            or capabilities.get("credits_per_request_wei")
            or capabilities.get("estimated_credits_per_request_wei")
            or ""
        ).strip()
        if price_wei:
            try:
                price_amount = credit_wei_to_decimal_text(price_wei)
            except Exception:
                price_amount = str(pricing.get("credits_per_request") or capabilities.get("credits_per_request") or "1")
        else:
            price_amount = str(pricing.get("credits_per_request") or capabilities.get("credits_per_request") or "1")
        market_capabilities = capabilities.get("capabilities") if isinstance(capabilities.get("capabilities"), list) else []
        clean_capabilities = [str(item).strip() for item in market_capabilities if str(item).strip()]
        if not clean_capabilities:
            clean_capabilities = ["chat.completions"]
        return {
            "rings": [self._worker_runtime_ring_partition(settings)],
            "price": {
                "amount": price_amount,
                "unit": str(pricing.get("unit") or "compute_credit"),
            },
            "capabilities": clean_capabilities,
            "models": models,
            "max_concurrency": 1,
            "active_sessions": max(0, int(active_jobs or 0)),
        }

    def _worker_runtime_multisession_authorization(
        self,
        *,
        settings: dict[str, Any],
        hub_url: str,
    ) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        registration = settings.get("workerHubRegistration") if isinstance(settings.get("workerHubRegistration"), dict) else {}
        registration_worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
        worker_caps = worker.get("capabilities") if isinstance(worker.get("capabilities"), dict) else {}
        registration_caps = registration_worker.get("capabilities") if isinstance(registration_worker.get("capabilities"), dict) else {}
        wallet_address = str(
            signed.get("credit_wallet")
            or signed.get("wallet_address")
            or worker.get("credit_wallet")
            or worker.get("wallet_address")
            or registration.get("credit_wallet")
            or registration.get("wallet_address")
            or registration_worker.get("credit_wallet")
            or registration_worker.get("wallet_address")
            or worker_caps.get("credit_wallet")
            or worker_caps.get("wallet_address")
            or registration_caps.get("credit_wallet")
            or registration_caps.get("wallet_address")
            or ""
        ).strip()
        key_id = str(
            signed.get("multisession_key_id")
            or worker.get("multisession_key_id")
            or registration.get("multisession_key_id")
            or registration_worker.get("multisession_key_id")
            or worker_caps.get("multisession_key_id")
            or registration_caps.get("multisession_key_id")
            or ""
        ).strip()
        chain_id = str(
            signed.get("chain_id")
            or worker.get("chain_id")
            or registration.get("chain_id")
            or registration_worker.get("chain_id")
            or worker_caps.get("chain_id")
            or registration_caps.get("chain_id")
            or ""
        ).strip()
        if key_id and wallet_address:
            return {
                "kind": "multisession_key",
                "wallet_address": self._normalize_worker_wallet_address(wallet_address),
                "multisession_key_id": key_id,
                "key_id": key_id,
                "chain_id": chain_id,
            }
        if wallet_address:
            return self._worker_multisession_authorization_for_wallet(
                hub_url=hub_url,
                wallet_address=wallet_address,
                chain_id=chain_id,
            )
        raise ValueError("Worker live-session requires a saved multi-session key authorization for this Hub.")

    def _worker_live_session_clients(self) -> tuple[threading.RLock, dict[tuple[str, str], _WorkerHubLiveSessionClient]]:
        lock = getattr(self.server, "_worker_live_session_lock", None)
        if lock is None:
            lock = threading.RLock()
            setattr(self.server, "_worker_live_session_lock", lock)
        clients = getattr(self.server, "_worker_live_session_clients", None)
        if not isinstance(clients, dict):
            clients = {}
            setattr(self.server, "_worker_live_session_clients", clients)
        return lock, clients

    def _worker_live_session_active_work_count(self, *, hub_url: str | None = None, worker_id: str | None = None) -> int:
        normalized_hub_url = self._clean_hub_url(hub_url, allow_empty=True) if hub_url is not None else ""
        normalized_worker_id = str(worker_id or "").strip()
        lock, clients = self._worker_live_session_clients()
        total = 0
        with lock:
            for (client_hub_url, client_worker_id), client in clients.items():
                if normalized_hub_url and client_hub_url != normalized_hub_url:
                    continue
                if normalized_worker_id and client_worker_id != normalized_worker_id:
                    continue
                try:
                    snapshot = client.snapshot()
                    total += max(0, int(snapshot.get("active_work_count", 0) or 0))
                except Exception:
                    continue
        return total

    def _close_worker_live_session(self, *, hub_url: str, worker_id: str, reason: str) -> dict[str, Any]:
        key = (self._clean_hub_url(hub_url), str(worker_id or "").strip())
        lock, clients = self._worker_live_session_clients()
        with lock:
            client = clients.get(key)
            if client is not None and client.has_active_work:
                snapshot = client.snapshot()
                snapshot["closed_by_runtime"] = False
                snapshot["close_deferred"] = True
                snapshot["close_deferred_reason"] = "active_live_session_work"
                snapshot["requested_close_reason"] = str(reason or "closed")
                return snapshot
            client = clients.pop(key, None)
        if client is not None:
            client.close(reason=reason)
            snapshot = client.snapshot()
            snapshot["closed_by_runtime"] = True
            return snapshot
        return {
            "ok": True,
            "transport": "websocket",
            "endpoint": _WorkerHubLiveSessionClient.endpoint_path,
            "hub_url": key[0],
            "worker_id": key[1],
            "closed_by_runtime": False,
            "alive": False,
            "reason": reason,
        }

    def _worker_live_session_prompt_for_offer(self, offer: dict[str, Any]) -> str:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        for key in ("prompt", "source", "value", "text"):
            text = str(input_payload.get(key) or "").strip()
            if text:
                return text
        for key in ("prompt", "source", "input"):
            value = work.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        messages = work.get("messages")
        if isinstance(messages, list):
            user_parts: list[str] = []
            all_parts: list[str] = []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                content = str(message.get("content") or "").strip()
                if not content:
                    continue
                all_parts.append(content)
                if str(message.get("role") or "").strip().lower() == "user":
                    user_parts.append(content)
            if user_parts:
                return "\n\n".join(user_parts).strip()
            if all_parts:
                return "\n\n".join(all_parts).strip()
        return ""

    def _worker_live_session_messages_for_offer(self, offer: dict[str, Any], *, source: str) -> list[dict[str, Any]]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        messages = work.get("messages")
        result: list[dict[str, Any]] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "user").strip().lower()
                if role not in {"system", "user", "assistant"}:
                    role = "user"
                content = message.get("content")
                if isinstance(content, str):
                    clean_content: Any = content.strip()
                elif isinstance(content, list):
                    clean_content = content
                else:
                    clean_content = str(content or "").strip()
                if not clean_content:
                    continue
                item: dict[str, Any] = {"role": role, "content": clean_content}
                if isinstance(message.get("attachments"), list):
                    item["attachments"] = message.get("attachments")
                result.append(item)
        if result:
            return result
        return [{"role": "user", "content": str(source or "")}]

    def _worker_live_session_attachments_for_offer(self, offer: dict[str, Any]) -> list[Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        input_payload = dict(work.get("input") or {}) if isinstance(work.get("input"), dict) else {}
        for value in (input_payload.get("attachments"), work.get("attachments")):
            if isinstance(value, list):
                return value
        return []

    def _worker_live_session_thread_id_for_offer(self, offer: dict[str, Any], *, run_id: str = "") -> str:
        session_id = str(offer.get("session_id") or "").strip()
        request_id = str(offer.get("request_id") or "").strip()
        clean_run_id = str(run_id or offer.get("run_id") or "").strip()
        return f"worker-live-session:{session_id or request_id or clean_run_id}"

    def _cancel_worker_live_session_offer(
        self,
        offer: dict[str, Any],
        *,
        reason: str = "worker-live-session-timeout",
        timeout_s: float = 0.0,
    ) -> dict[str, Any]:
        session_id = str(offer.get("session_id") or "").strip()
        request_id = str(offer.get("request_id") or "").strip()
        run_id = str(offer.get("run_id") or "").strip()
        thread_id = self._worker_live_session_thread_id_for_offer(offer, run_id=run_id)
        log_path = self._worker_live_session_log_path(run_id=run_id, session_id=session_id, request_id=request_id)
        manager = getattr(getattr(self, "server", None), "chat_ai_processes", None)
        stop_method = getattr(manager, "stop", None)
        if not callable(stop_method):
            append_text_log(
                log_path,
                "worker live-session local AI cancellation unavailable",
                run_id=run_id,
                session_id=session_id,
                request_id=request_id,
                thread_id=thread_id,
                reason=reason,
                timeout_s=timeout_s,
            )
            return {
                "ok": False,
                "cancelled": False,
                "reason": "local-ai-stop-unavailable",
                "thread_id": thread_id,
                "run_id": run_id,
            }

        append_text_log(
            log_path,
            "worker live-session local AI cancellation requested",
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            thread_id=thread_id,
            reason=reason,
            timeout_s=timeout_s,
        )
        result = stop_method(thread_id=thread_id, run_id=run_id, reason=reason)
        if not isinstance(result, dict):
            result = {"ok": True, "stopped": bool(result)}
        append_text_log(
            log_path,
            "worker live-session local AI cancellation completed",
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            thread_id=thread_id,
            reason=reason,
            timeout_s=timeout_s,
            stop_result=result,
        )
        return {
            **result,
            "thread_id": str(result.get("thread_id") or thread_id),
            "run_id": str(result.get("run_id") or run_id),
        }

    def _worker_live_session_log_path(self, *, run_id: str, session_id: str, request_id: str) -> Path:
        safe_id = "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_"
            for ch in (str(run_id or "") or str(session_id or "") or str(request_id or "") or "worker_live_session")
        )
        safe_id = safe_id[:96] or "worker_live_session"
        root = (self.server.debug_root / "worker_live_session_ai").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{safe_id}.log"

    def _worker_live_session_should_inline_provider(self) -> bool:
        provider = getattr(getattr(getattr(self, "server", None), "computer", None), "provider", None)
        module = str(getattr(getattr(provider, "__class__", None), "__module__", "") or "")
        if not provider or module.startswith("main_computer.providers"):
            return False
        return os.environ.get("MAIN_COMPUTER_DISABLE_INLINE_TEST_PROVIDER", "").strip().lower() not in {"1", "true", "yes", "on"}

    def _execute_worker_live_session_offer(self, offer: dict[str, Any]) -> dict[str, Any]:
        work = dict(offer.get("work") or {}) if isinstance(offer.get("work"), dict) else {}
        capability_values = work.get("capabilities")
        capabilities = [str(item) for item in capability_values] if isinstance(capability_values, list) else []
        if capabilities and "chat.completions" not in capabilities and "text" not in capabilities:
            raise ValueError(f"Unsupported worker live-session capabilities: {capabilities!r}")
        source = self._worker_live_session_prompt_for_offer(offer)
        if not source:
            raise ValueError("Worker live-session chat.completions offer did not include a prompt or user message.")
        attachments = self._worker_live_session_attachments_for_offer(offer)
        messages = self._worker_live_session_messages_for_offer(offer, source=source)
        run_id = str(offer.get("run_id") or "").strip() or f"worker_live_{int(time.time() * 1000)}"
        session_id = str(offer.get("session_id") or "").strip()
        request_id = str(offer.get("request_id") or "").strip()
        thread_id = self._worker_live_session_thread_id_for_offer(offer, run_id=run_id)
        log_path = self._worker_live_session_log_path(run_id=run_id, session_id=session_id, request_id=request_id)

        append_text_log(
            log_path,
            "worker live-session local AI execution starting",
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            worker_id=str(offer.get("worker_id") or self._worker_runtime_worker_id(self._load_worker_settings())),
            model=str(work.get("model") or ""),
            capabilities=capabilities,
            source_chars=len(source),
            source_preview=source[:1000],
        )

        if self._worker_live_session_should_inline_provider():
            if hasattr(self.server.computer, "chat_console_ai"):
                inline_response = self.server.computer.chat_console_ai(source, attachments=attachments)
            else:
                inline_response = self.server.computer.chat(source)
            response_payload = {
                "content": str(getattr(inline_response, "content", "") or ""),
                "provider": str(getattr(inline_response, "provider", "") or ""),
                "model": str(getattr(inline_response, "model", "") or str(work.get("model") or "")),
                "metadata": getattr(inline_response, "metadata", {}) if isinstance(getattr(inline_response, "metadata", {}), dict) else {},
            }
        else:
            manager = getattr(getattr(self, "server", None), "chat_ai_processes", None)
            run_method = getattr(manager, "run", None)
            if not callable(run_method):
                raise RuntimeError("Local AI subprocess manager is not available for worker live-session execution.")
            payload = run_method(
                command={
                    "mode": "worker_live_session_chat_completion",
                    "run_id": run_id,
                    "source": source,
                    "messages": messages,
                    "attachments": attachments,
                    "config": config_to_payload(self.server.config),
                },
                thread_id=thread_id,
                log_file=log_path,
                activity_bus=getattr(self.server, "activity", None),
                cwd=self.server.debug_root,
                max_local_concurrency=1,
            )
            response_payload = payload.get("response") if isinstance(payload.get("response"), dict) else {}

        content = str(response_payload.get("content") or "")
        provider = str(response_payload.get("provider") or "")
        model = str(response_payload.get("model") or str(work.get("model") or ""))
        metadata = response_payload.get("metadata") if isinstance(response_payload.get("metadata"), dict) else {}
        metadata = {
            **metadata,
            "from_live_session": True,
            "source": "main_computer.worker_live_session_ai",
            "request_id": request_id,
            "session_id": session_id,
            "run_id": run_id,
            "log_file": str(log_path),
        }
        append_text_log(
            log_path,
            "worker live-session local AI execution completed",
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            provider=provider,
            model=model,
            response_chars=len(content),
            response_preview=content[:1000],
        )
        return {
            "status": "success",
            "response": {
                "role": "assistant",
                "content": content,
                "provider": provider,
                "model": model,
                "metadata": metadata,
            },
            "transport": "websocket-live-session",
            "worker_id": str(offer.get("worker_id") or self._worker_runtime_worker_id(self._load_worker_settings())),
            "local_ai": True,
            "log_file": str(log_path),
        }

    def _ensure_worker_live_session(
        self,
        *,
        hub_url: str,
        worker_id: str,
        auth_message: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_hub_url = self._clean_hub_url(hub_url)
        key = (normalized_hub_url, str(worker_id or "").strip())
        lock, clients = self._worker_live_session_clients()
        with lock:
            existing = clients.get(key)
            candidate = _WorkerHubLiveSessionClient(
                hub_url=normalized_hub_url,
                worker_id=key[1],
                auth_message=auth_message,
                timeout_s=5.0,
                work_executor=self._execute_worker_live_session_offer,
                work_canceller=self._cancel_worker_live_session_offer,
            )
            if existing is not None and existing.is_alive and existing.fingerprint == candidate.fingerprint:
                return existing.snapshot()
            if existing is not None and existing.is_alive and existing.has_active_work:
                return {
                    **existing.snapshot(),
                    "ok": True,
                    "replacement_deferred": True,
                    "replacement_deferred_reason": "active_live_session_work",
                }
            if existing is not None:
                existing.close(reason="worker_live_session_replaced")
                clients.pop(key, None)
            snapshot = candidate.start()
            clients[key] = candidate
            return snapshot

    def _post_worker_runtime_heartbeat_to_hub(
        self,
        *,
        hub_url: str,
        settings: dict[str, Any],
        phase: str,
        hub_status: str,
        active_jobs: int,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        worker_id = self._worker_runtime_worker_id(settings)
        worker_instance_id = self._worker_runtime_instance_id(settings)
        models = self._worker_runtime_models(settings)
        if not worker_id:
            raise ValueError("Worker runtime live-session requires a worker id.")
        if not hub_url:
            raise ValueError("Worker runtime live-session requires a Hub URL.")

        if str(phase or "") == "not_accepting" or str(hub_status or "") == "offline":
            return self._close_worker_live_session(
                hub_url=hub_url,
                worker_id=worker_id,
                reason="runtime_not_accepting",
            )

        signed_connection = settings.get("signedWorkerConnection")
        signed_worker = signed_connection.get("worker") if isinstance(signed_connection, dict) else {}
        stored_worker = settings.get("workerHubRegistration")
        base_worker = signed_worker if isinstance(signed_worker, dict) else {}
        if not base_worker and isinstance(stored_worker, dict):
            stored_registered_worker = stored_worker.get("worker") if isinstance(stored_worker.get("worker"), dict) else {}
            base_worker = stored_registered_worker if stored_registered_worker else stored_worker
        capabilities = dict(base_worker.get("capabilities", {})) if isinstance(base_worker.get("capabilities"), dict) else {}
        if not capabilities.get("capabilities"):
            capabilities["capabilities"] = ["chat.completions"]

        availability_mode = self._normalize_worker_seller_availability_mode(settings.get("sellerAvailabilityMode"))
        availability = {
            "accept_paid_jobs": bool(settings.get("sellerEnabled")),
            "availability_mode": availability_mode,
            "only_when_idle": availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE,
            "idle_source": "windows_quser_v1" if availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE else "local_ai_capacity_v1",
            "ai_idle_required": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
            "worker_runtime_phase": phase,
            "allowed_to_accept": bool(policy.get("allowed_to_accept")),
            "last_user_activity": policy.get("user_activity"),
            "local_ai_capacity": policy.get("local_ai_capacity"),
        }
        capabilities["availability"] = availability
        capabilities["runtime"] = {
            "phase": phase,
            "source": "main_app_worker_runtime_v1",
            "transport": "websocket",
            "live_session_endpoint": _WorkerHubLiveSessionClient.endpoint_path,
            "drains_before_disconnect": True,
        }

        authorization = self._worker_runtime_multisession_authorization(
            settings=settings,
            hub_url=hub_url,
        )
        chain_id = str(
            authorization.get("chain_id")
            or (signed_connection.get("chain_id") if isinstance(signed_connection, dict) else "")
            or ""
        ).strip()
        market = self._worker_runtime_market_profile(
            settings=settings,
            capabilities=capabilities,
            models=models,
            active_jobs=active_jobs,
        )
        auth_message = {
            "type": "worker.auth",
            "worker_id": worker_id,
            "worker_instance_id": worker_instance_id,
            "chain_id": chain_id,
            "status": hub_status,
            "model": models[0] if models else self._WORKER_DEFAULT_SELLER_MODEL,
            "models": models,
            "queue_depth": 0,
            "active_requests": max(0, int(active_jobs or 0)),
            "max_concurrency": 1,
            "capabilities": capabilities,
            "market": market,
            "multisession_authorization": authorization,
        }
        live_session = self._ensure_worker_live_session(
            hub_url=hub_url,
            worker_id=worker_id,
            auth_message=auth_message,
        )
        return {
            "ok": True,
            "worker": {
                "node_id": worker_id,
                "worker_instance_id": worker_instance_id,
                "status": hub_status,
                "models": models,
                "queue_depth": 0,
                "active_requests": max(0, int(active_jobs or 0)),
                "max_concurrency": 1,
                "capabilities": capabilities,
            },
            "transport": "websocket",
            "endpoint": _WorkerHubLiveSessionClient.endpoint_path,
            "live_session": live_session,
        }

    def _worker_runtime_phase_label(self, phase: Any) -> str:
        normalized = str(phase or "not_accepting")
        if normalized == "accepting":
            return "Accepting work"
        if normalized == "draining":
            return "Finishing current work"
        return "Not accepting"

    def _worker_runtime_primary_status(
        self,
        settings: dict[str, Any],
        *,
        phase: str,
        active_jobs: int,
        can_accept: bool,
        policy: dict[str, Any],
        heartbeat_error: str = "",
    ) -> dict[str, str]:
        signed_order = policy.get("signed_order") if isinstance(policy.get("signed_order"), dict) else self._worker_runtime_signed_order_state(settings)
        hub_registration = policy.get("hub_registration") if isinstance(policy.get("hub_registration"), dict) else self._worker_runtime_hub_registration_state(settings)
        local_policy = policy.get("local_policy") if isinstance(policy.get("local_policy"), dict) else self._worker_runtime_local_policy(settings, active_jobs=active_jobs)
        wallet = str(signed_order.get("wallet") or "").strip()
        credit_wallet = str(signed_order.get("creditWallet") or wallet).strip()
        if heartbeat_error:
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": f"Hub heartbeat failed: {heartbeat_error}",
                "next": "Check the Hub connection and retry.",
            }
        if phase == "draining" and active_jobs > 0:
            return {
                "status": "draining",
                "label": "Finishing current work",
                "reason": "The worker is draining and will disconnect after active work finishes.",
                "next": "Wait for the active job to finish.",
            }
        if not bool(local_policy.get("enabled")):
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Accept paid jobs is off.",
                "next": "Turn on Accept paid jobs when you want this computer to work.",
            }
        if not wallet or not credit_wallet:
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Wallet is not connected.",
                "next": "Connect a wallet.",
            }
        if signed_order.get("status") != "ready":
            start_status = str(signed_order.get("status") or "not_started")
            if start_status == "starting":
                reason = "Multi-session key request is in progress."
                next_action = "Finish the multi-session key wallet prompt."
            elif start_status == "invalid":
                reason = "Worker registration is not ready."
                next_action = "Work now."
            else:
                reason = "Worker has not been registered with the Hub."
                next_action = "Work now."
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": reason,
                "next": next_action,
            }
        hub_status = str(hub_registration.get("status") or "not_submitted")
        hub_accepted = hub_status == "accepted" and self._worker_runtime_signed_connection_registered(settings)
        if not hub_accepted:
            if hub_status == "failed" and hub_registration.get("lastError"):
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": f"Hub registration failed: {hub_registration['lastError']}",
                    "next": "Work now.",
                }
            if hub_status == "failed":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Hub registration failed.",
                    "next": "Work now.",
                }
            if hub_status == "submitting":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Worker registration is being submitted to the Hub.",
                    "next": "Wait for Hub registration to finish.",
                }
            if hub_status == "stale":
                return {
                    "status": "not_accepting",
                    "label": "Not accepting",
                    "reason": "Hub registration is stale.",
                    "next": "Work now.",
                }
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": "Worker registration has not been submitted to the Hub.",
                "next": "Work now.",
            }
        if not bool(local_policy.get("allowed")):
            mode = str(local_policy.get("mode") or "")
            return {
                "status": "not_accepting",
                "label": "Not accepting",
                "reason": str(local_policy.get("reason") or "Local policy blocks work."),
                "next": (
                    "Wait until the computer is idle."
                    if mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
                    else "Wait until local AI work finishes."
                ),
            }
        if phase == "accepting" and can_accept:
            override = local_policy.get("work_now_override") if isinstance(local_policy.get("work_now_override"), dict) else {}
            override_active = bool(override.get("active"))
            return {
                "status": "accepting",
                "label": "Accepting work",
                "reason": (
                    "Hub registration accepted and Work-now override allows work."
                    if override_active and not bool(local_policy.get("normal_allowed", local_policy.get("allowed")))
                    else "Hub registration accepted and local policy allows work."
                ),
                "next": "Waiting for Hub job assignment.",
            }
        return {
            "status": "not_accepting",
            "label": "Not accepting",
            "reason": "Worker is not ready.",
            "next": "Check registration and local policy.",
        }

    def _worker_runtime_status_payload(
        self,
        settings: dict[str, Any],
        *,
        phase: str,
        active_jobs: int,
        can_accept: bool,
        hub_status: str,
        reason: str,
        now: str,
        heartbeat_error: str,
        heartbeat_result: dict[str, Any] | None,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        signed = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        signed_worker = signed.get("worker") if isinstance(signed.get("worker"), dict) else {}
        signed_order = policy.get("signed_order") if isinstance(policy.get("signed_order"), dict) else self._worker_runtime_signed_order_state(settings)
        hub_registration = policy.get("hub_registration") if isinstance(policy.get("hub_registration"), dict) else self._worker_runtime_hub_registration_state(settings)
        local_policy = policy.get("local_policy") if isinstance(policy.get("local_policy"), dict) else self._worker_runtime_local_policy(settings, active_jobs=active_jobs)
        work_now_override = policy.get("work_now_override") if isinstance(policy.get("work_now_override"), dict) else self._worker_work_now_override_state(settings)
        primary = self._worker_runtime_primary_status(
            settings,
            phase=phase,
            active_jobs=active_jobs,
            can_accept=can_accept,
            policy=policy,
            heartbeat_error=heartbeat_error,
        )
        requested_ring = str(settings.get("workerRequestedRing") or signed.get("requested_ring") or "3")
        assigned_ring = str(settings.get("workerAssignedRing") or signed.get("assigned_ring") or signed_worker.get("assigned_ring") or "")
        worker_id = self._worker_runtime_worker_id(settings)
        pricing_policy = str(settings.get("workerPricingPolicy") or signed.get("pricing_policy") or signed_worker.get("pricing_policy") or "")
        wallet = str(signed_order.get("wallet") or signed.get("wallet_address") or "").strip()
        credit_wallet = str(signed_order.get("creditWallet") or signed.get("credit_wallet") or wallet).strip()
        last_heartbeat_status = str(settings.get("workerRuntimeLastHeartbeatStatus") or "")
        hub_availability = last_heartbeat_status or (hub_status if hub_registration.get("status") == "accepted" else "not_announced")
        runtime_last_error = str(heartbeat_error or settings.get("workerRuntimeError") or hub_registration.get("lastError") or "")
        return {
            "ok": True,
            "status": primary["status"],
            "statusLabel": primary["label"],
            "reason": primary["reason"],
            "next": primary["next"],
            "identity": {
                "wallet": wallet,
                "creditWallet": credit_wallet,
                "workerId": worker_id,
                "requestedRing": self._worker_ring_label(requested_ring) or requested_ring,
                "assignedRing": self._worker_ring_label(assigned_ring) if assigned_ring else None,
            },
            "signedOrder": signed_order,
            "hubRegistration": hub_registration,
            "localPolicy": local_policy,
            "workNowOverride": work_now_override,
            "runtime": {
                "enabled": bool(settings.get("workerRuntimeEnabled")),
                "phase": phase,
                "label": self._worker_runtime_phase_label(phase),
                "active_jobs": active_jobs,
                "activeJobs": active_jobs,
                "allowed_to_accept": can_accept,
                "allowedToAccept": can_accept,
                "hub_status": hub_status,
                "hubAvailability": hub_availability,
                "reason": reason,
                "last_checked_at": now,
                "lastCheckedAt": now,
                "last_connected_at": settings.get("workerRuntimeLastConnectedAt", ""),
                "last_disconnected_at": settings.get("workerRuntimeLastDisconnectedAt", ""),
                "last_heartbeat_at": settings.get("workerRuntimeLastHeartbeatAt", ""),
                "lastHeartbeatAt": settings.get("workerRuntimeLastHeartbeatAt", ""),
                "lastError": runtime_last_error,
                "heartbeat_error": heartbeat_error,
                "heartbeat_result": heartbeat_result,
                "policy": policy,
                "work_now_override": work_now_override,
                "workNowOverride": work_now_override,
            },
            "worker": {
                "pricingPolicy": pricing_policy,
                "pool": settings.get("workerPool") if isinstance(settings.get("workerPool"), dict) else None,
            },
            "settings": settings,
        }

    def _worker_runtime_transition(
        self,
        settings: dict[str, Any],
        *,
        action: str = "sync",
        active_jobs: int | None = None,
        send_heartbeat: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cleaned = self._sanitize_worker_settings(settings)
        action = str(action or "sync").strip().lower()
        if action not in {"sync", "activate", "deactivate", "job-start", "job-finish"}:
            raise ValueError("Worker runtime action must be sync, activate, deactivate, job-start, or job-finish.")

        if action == "activate":
            # Legacy callers used to toggle a separate runtime latch.  The golden
            # path now derives worker availability from the saved seller policy,
            # so activation is an alias for enabling paid jobs.
            cleaned["sellerEnabled"] = True
            cleaned["rentalEnabled"] = True
        elif action == "deactivate":
            # Deactivation is likewise just the saved seller policy going false.
            # Active work drains before the Hub sees the worker offline.
            cleaned["sellerEnabled"] = False
            cleaned["rentalEnabled"] = False

        previous_phase = str(cleaned.get("workerRuntimePhase") or "not_accepting")
        previous_active = max(0, int(cleaned.get("workerRuntimeActiveJobs", 0) or 0))
        hub_url_for_active = self._worker_runtime_hub_url(cleaned)
        worker_id_for_active = self._worker_runtime_worker_id(cleaned)
        live_session_active = self._worker_live_session_active_work_count(
            hub_url=hub_url_for_active or None,
            worker_id=worker_id_for_active or None,
        )
        if active_jobs is None:
            if action == "sync":
                # Live-session work is tracked by the websocket client, not by
                # persisted settings.  Do not carry a prior sync's synthetic
                # active count forever after the job finishes.
                active = live_session_active
            else:
                active = previous_active
                if action == "job-start":
                    active += 1
                elif action == "job-finish":
                    active = max(0, active - 1)
                active = max(active, live_session_active)
        else:
            active = max(0, int(active_jobs or 0), live_session_active)

        policy = self._worker_runtime_policy(cleaned, active_jobs=active)
        runtime_enabled = bool(cleaned.get("sellerEnabled"))
        cleaned["workerRuntimeEnabled"] = runtime_enabled
        can_accept = runtime_enabled and bool(policy.get("allowed_to_accept"))

        if can_accept:
            phase = "accepting"
            hub_status = "busy" if active > 0 else "available"
        elif active > 0 and previous_phase in {"accepting", "draining"}:
            phase = "draining"
            hub_status = "draining"
        else:
            phase = "not_accepting"
            hub_status = "offline"

        now = datetime.now(timezone.utc).isoformat()
        reason = str(policy.get("reason") or "")
        heartbeat_result: dict[str, Any] | None = None
        heartbeat_error = ""
        hub_url = self._worker_runtime_hub_url(cleaned)
        should_heartbeat = send_heartbeat and self._worker_runtime_signed_connection_registered(cleaned) and bool(hub_url)
        if should_heartbeat:
            try:
                heartbeat_result = self._post_worker_runtime_heartbeat_to_hub(
                    hub_url=hub_url,
                    settings=cleaned,
                    phase=phase,
                    hub_status=hub_status,
                    active_jobs=active,
                    policy=policy,
                )
            except Exception as exc:
                heartbeat_error = str(exc)
                if phase == "accepting":
                    phase = "not_accepting"
                    hub_status = "offline"
                    reason = f"Hub heartbeat failed: {heartbeat_error}"

        if previous_phase != "accepting" and phase == "accepting":
            cleaned["workerRuntimeLastConnectedAt"] = now
        if previous_phase != "not_accepting" and phase == "not_accepting":
            cleaned["workerRuntimeLastDisconnectedAt"] = now
        cleaned["workerRuntimePhase"] = phase
        cleaned["workerRuntimeActiveJobs"] = active
        cleaned["workerRuntimeLastReason"] = reason
        cleaned["workerRuntimeLastCheckedAt"] = now
        cleaned["workerRuntimeLastHeartbeatStatus"] = hub_status if should_heartbeat and not heartbeat_error else ""
        if should_heartbeat and not heartbeat_error:
            cleaned["workerRuntimeLastHeartbeatAt"] = now
        cleaned["workerRuntimeError"] = heartbeat_error
        saved = self._save_worker_settings(cleaned)

        status = self._worker_runtime_status_payload(
            saved,
            phase=phase,
            active_jobs=active,
            can_accept=can_accept,
            hub_status=hub_status,
            reason=reason,
            now=now,
            heartbeat_error=heartbeat_error,
            heartbeat_result=heartbeat_result,
            policy=policy,
        )
        return saved, status

    def _handle_worker_runtime_status(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker runtime status is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            _saved, status = self._worker_runtime_transition(settings, action="sync", send_heartbeat=False)
            self.server.signal("api-worker-runtime-status", phase=status["runtime"]["phase"], allowed=status["runtime"]["allowed_to_accept"])
            self._send_json(status)
        except Exception as exc:
            self.server.signal("api-worker-runtime-status-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_runtime_sync(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker runtime sync is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            action = str(body.get("action") or "sync")
            active_jobs_raw = body.get("active_jobs")
            active_jobs = int(active_jobs_raw) if active_jobs_raw is not None else None
            settings = self._load_worker_settings()
            incoming_settings = body.get("settings")
            if isinstance(incoming_settings, dict):
                settings.update(self._sanitize_worker_settings(incoming_settings))
                settings = self._save_worker_settings(settings)
            _saved, status = self._worker_runtime_transition(settings, action=action, active_jobs=active_jobs, send_heartbeat=True)
            self.server.signal(
                "api-worker-runtime-sync",
                action=action,
                phase=status["runtime"]["phase"],
                allowed=status["runtime"]["allowed_to_accept"],
                active_jobs=status["runtime"]["active_jobs"],
            )
            self._send_json(status)
        except Exception as exc:
            self.server.signal("api-worker-runtime-sync-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)


    def _worker_network_order(self) -> list[str]:
        return ["mainnet", "testnet", "test", "dev"]

    def _worker_ring_order(self) -> list[dict[str, str]]:
        return [
            {"ring": "0", "label": "Ring 0 - Operator", "description": "operator / direct whitelist"},
            {"ring": "1", "label": "Ring 1 - Protected", "description": "protected trusted workers"},
            {"ring": "2", "label": "Ring 2 - Public", "description": "public workers"},
            {"ring": "3", "label": "Ring 3 - Public untrusted", "description": "public untrusted workers"},
        ]

    def _worker_network_profile_payload(self, profile: Any) -> dict[str, Any]:
        return {
            "network": profile.network_key,
            "network_key": profile.network_key,
            "display_name": profile.display_name,
            "kind": profile.kind,
            "chain_id": profile.chain_id,
            "chain_rpc_url": profile.chain_rpc_url or "",
            "hub_url": profile.hub_url,
            "hub_public_url": profile.hub_public_url or profile.hub_url,
            "hub_bind_host": profile.hub_bind_host,
            "hub_bind_port": profile.hub_bind_port,
            "deployment_manifest_path": str(profile.deployment_manifest_path or ""),
        }

    def _worker_network_profiles_payload(self) -> list[dict[str, Any]]:
        registry = load_hub_network_registry()
        profiles: list[dict[str, Any]] = []
        for key in self._worker_network_order():
            if key not in registry.networks:
                continue
            profiles.append(self._worker_network_profile_payload(registry.get(key)))
        return profiles

    def _normalize_worker_network_key(self, value: Any, *, allow_none: bool = True) -> str:
        key = str(value or "none").strip().lower()
        allowed = set(self._worker_network_order())
        if allow_none:
            allowed.add("none")
        if key not in allowed:
            available = ", ".join([*self._worker_network_order(), "none"] if allow_none else self._worker_network_order())
            raise ValueError(f"Unknown worker network {key!r}. Available networks: {available}.")
        return key

    def _normalize_worker_ring(self, value: Any) -> str:
        ring = str(value if value is not None else "3").strip()
        if ring not in {"0", "1", "2", "3"}:
            raise ValueError("Worker ring must be one of 0, 1, 2, or 3.")
        return ring

    def _worker_network_session_payload(self, settings: dict[str, Any], *, check_hub: bool = False) -> dict[str, Any]:
        profiles = self._worker_network_profiles_payload()
        profiles_by_key = {str(profile["network"]): profile for profile in profiles}
        selected = self._normalize_worker_network_key(settings.get("selectedNetwork", "none"))
        requested_ring = self._normalize_worker_ring(settings.get("workerRequestedRing", "3"))
        signed_connection = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
        hub_registration = settings.get("workerHubRegistration") if isinstance(settings.get("workerHubRegistration"), dict) else {}
        worker_pool = settings.get("workerPool") if isinstance(settings.get("workerPool"), dict) else {}
        signed_worker = signed_connection.get("worker") if isinstance(signed_connection.get("worker"), dict) else {}
        assigned_ring = str(
            settings.get("workerAssignedRing")
            or signed_connection.get("assigned_ring")
            or signed_worker.get("assigned_ring")
            or ""
        )
        worker_id = str(
            settings.get("workerRegisteredId")
            or signed_connection.get("worker_id")
            or signed_worker.get("worker_id")
            or signed_worker.get("node_id")
            or ""
        )
        pricing_policy = str(
            settings.get("workerPricingPolicy")
            or signed_connection.get("pricing_policy")
            or signed_worker.get("pricing_policy")
            or ""
        )

        session: dict[str, Any] = {
            "selected_network": selected,
            "connection_status": "disconnected" if selected == "none" else str(settings.get("workerConnectionStatus") or "stale"),
            "requested_ring": requested_ring,
            "assigned_ring": assigned_ring,
            "worker_id": worker_id,
            "pricing_policy": pricing_policy,
            "connected_at": str(settings.get("workerConnectedAt") or ""),
            "connection_error": str(settings.get("workerConnectionError") or ""),
            "connected_hub_url": str(settings.get("workerConnectedHubUrl") or ""),
            "signed_connection": signed_connection,
            "hub_registration": hub_registration or None,
            "worker_pool": worker_pool or None,
            "profile": None,
            "hub_status": None,
        }

        if selected != "none":
            profile = profiles_by_key.get(selected)
            if not profile:
                session["connection_status"] = "failed"
                session["connection_error"] = f"Selected worker network {selected!r} is not present in the Hub network registry."
            else:
                session["profile"] = profile
                hub_url = str(profile.get("hub_url") or "")
                if check_hub:
                    status = self._fetch_hub_status(hub_url)
                    session["hub_status"] = status
                    session["connected_hub_url"] = hub_url
                    if status.get("reachable"):
                        session["connection_status"] = "connected"
                        session["connection_error"] = ""
                    else:
                        session["connection_status"] = "failed"
                        session["connection_error"] = str(status.get("error") or "Hub is unreachable.")
                elif not session["connected_hub_url"]:
                    session["connected_hub_url"] = hub_url

        return {
            "ok": True,
            "networks": profiles,
            "network_order": self._worker_network_order(),
            "rings": self._worker_ring_order(),
            "session": session,
        }

    def _handle_worker_network_session_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker network sessions are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            settings = self._load_worker_settings()
            payload = self._worker_network_session_payload(settings, check_hub=bool(settings.get("selectedNetwork") not in {None, "", "none"}))
            session = payload["session"]
            if session["selected_network"] != "none":
                settings["workerConnectionStatus"] = session["connection_status"]
                settings["workerConnectedHubUrl"] = session.get("connected_hub_url", "")
                settings["workerConnectionError"] = session.get("connection_error", "")
                if session["connection_status"] == "connected":
                    settings["workerConnectedAt"] = worker_now = datetime.now(timezone.utc).isoformat()
                    session["connected_at"] = worker_now
                self._save_worker_settings(settings)
            self.server.signal("api-worker-network-session-load", selected=session["selected_network"], status=session["connection_status"])
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-worker-network-session-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_network_session_select(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker network sessions are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            selected = self._normalize_worker_network_key(body.get("network"))
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "3"))
            settings = self._load_worker_settings()
            settings["selectedNetwork"] = selected
            settings["workerRequestedRing"] = requested_ring
            settings["workerAssignedRing"] = ""
            settings["workerRegisteredId"] = ""
            settings["workerPricingPolicy"] = ""
            settings["workerHubRegistration"] = {}
            settings["workerPool"] = {}
            settings["signedWorkerConnection"] = {}
            if selected == "none":
                settings.update(
                    {
                        "workerConnectionStatus": "disconnected",
                        "workerConnectedAt": "",
                        "workerConnectionError": "",
                        "workerConnectedHubUrl": "",
                        "workerAssignedRing": "",
                        "workerRegisteredId": "",
                        "workerPricingPolicy": "",
                        "workerHubRegistration": {},
                        "workerPool": {},
                    }
                )
                saved = self._save_worker_settings(settings)
                payload = self._worker_network_session_payload(saved, check_hub=False)
                self.server.signal("api-worker-network-disconnect")
                self._send_json(payload)
                return

            payload = self._worker_network_session_payload(settings, check_hub=True)
            session = payload["session"]
            settings["workerConnectionStatus"] = session["connection_status"]
            settings["workerConnectedHubUrl"] = session.get("connected_hub_url", "")
            settings["workerConnectionError"] = session.get("connection_error", "")
            settings["workerConnectedAt"] = datetime.now(timezone.utc).isoformat() if session["connection_status"] == "connected" else ""
            saved = self._save_worker_settings(settings)
            payload = self._worker_network_session_payload(saved, check_hub=False)
            payload["session"]["hub_status"] = session.get("hub_status")
            self.server.signal("api-worker-network-select", selected=selected, status=payload["session"]["connection_status"])
            self._send_json(payload)
        except Exception as exc:
            self.server.signal("api-worker-network-select-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_network_work_now(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker Work now override is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            action = str(body.get("action") or "work-now").strip().lower()
            if action not in {"work-now", "finish"}:
                raise ValueError("Worker Work now action must be work-now or finish.")

            active_jobs_raw = body.get("active_jobs")
            active_jobs = int(active_jobs_raw) if active_jobs_raw is not None else None

            def send_network_and_runtime_payload(settings_payload: dict[str, Any]) -> None:
                saved, runtime_status = self._worker_runtime_transition(
                    settings_payload,
                    action="sync",
                    active_jobs=active_jobs,
                    send_heartbeat=True,
                )
                payload = self._worker_network_session_payload(saved, check_hub=False)
                payload["runtimeStatus"] = runtime_status
                payload["runtime"] = runtime_status.get("runtime")
                payload["localPolicy"] = runtime_status.get("localPolicy")
                payload["workNowOverride"] = runtime_status.get("workNowOverride")
                self._send_json(payload)

            settings = self._load_worker_settings()
            if action == "finish":
                settings = self._worker_clear_work_now_override(settings)
                settings = self._save_worker_settings(settings)
                self.server.signal("api-worker-network-work-now-finish")
                send_network_and_runtime_payload(settings)
                return

            duration_seconds = int(body.get("duration_seconds") or body.get("durationSeconds") or 0)
            if duration_seconds <= 0:
                raise ValueError("duration_seconds is required for Work now.")
            if duration_seconds > 7 * 24 * 60 * 60:
                raise ValueError("Work now duration may not exceed 7 days.")

            selected = self._normalize_worker_network_key(body.get("network"), allow_none=False)
            requested_ring = self._normalize_worker_ring(body.get("requested_ring", "3"))
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))

            worker_payload = body.get("worker")
            if not isinstance(worker_payload, dict):
                raise ValueError("worker registration payload is required.")
            registration_payload = self._worker_registration_payload_from_ui(worker_payload)

            current = self._normalize_worker_network_key(settings.get("selectedNetwork", "none"))
            if current != selected:
                raise ValueError(f"Cannot start work for {selected!r}; current worker network is {current!r}.")

            session_payload = self._worker_network_session_payload(settings, check_hub=False)
            profile = session_payload["session"].get("profile") if isinstance(session_payload.get("session"), dict) else None
            profile = profile if isinstance(profile, dict) else {}
            hub_url = self._clean_hub_url(str(body.get("hub_url") or profile.get("hub_url") or self.server.config.hub_url))
            profile_hub_url = str(profile.get("hub_url") or "").strip()
            if profile_hub_url and hub_url != self._clean_hub_url(profile_hub_url):
                raise ValueError(f"Worker start Hub {hub_url!r} does not match selected network Hub {profile_hub_url!r}.")
            chain_id = str(profile.get("chain_id") or body.get("chain_id") or "")

            active_multisession_key_id = str(
                body.get("active_multisession_key_id")
                or body.get("multisession_key_id")
                or ""
            ).strip()

            settings = self._worker_set_work_now_override(settings, duration_seconds=duration_seconds)
            settings["workerRequestedRing"] = requested_ring
            settings["workerConnectedHubUrl"] = hub_url
            settings["workerConnectionStatus"] = "connected"
            settings["workerConnectionError"] = ""

            existing_connection = settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else {}
            existing_wallet = self._normalize_worker_wallet_address(existing_connection.get("wallet_address")) if re.fullmatch(r"0x[0-9a-fA-F]{40}", str(existing_connection.get("wallet_address") or "").strip()) else ""
            existing_network = self._normalize_worker_network_key(existing_connection.get("network") or "none")
            existing_ring = self._normalize_worker_ring(existing_connection.get("requested_ring") or requested_ring)
            existing_hub = self._clean_hub_url(str(existing_connection.get("hub_url") or ""), allow_empty=True)
            needs_registration = not (
                self._worker_runtime_signed_connection_registered(settings)
                and existing_network == selected
                and existing_wallet == wallet_address
                and existing_ring == requested_ring
                and existing_hub == hub_url
            )

            if not needs_registration:
                settings["workerConnectedAt"] = settings.get("workerConnectedAt") or datetime.now(timezone.utc).isoformat()
                settings = self._save_worker_settings(settings)
                self.server.signal(
                    "api-worker-network-work-now-override",
                    selected=selected,
                    ring=requested_ring,
                    wallet=wallet_address,
                    duration_seconds=duration_seconds,
                    registered=True,
                )
                send_network_and_runtime_payload(settings)
                return

            started_at = datetime.now(timezone.utc).isoformat()
            worker_connection = {
                "network": selected,
                "requested_ring": requested_ring,
                "wallet_address": wallet_address,
                "credit_wallet": wallet_address,
                "hub_url": hub_url,
                "chain_id": chain_id,
                "started_at": started_at,
                "status": "ready",
                "worker_start_status": "ready",
                "signed_order_status": "ready",
                "hub_registration_status": "not_submitted",
                "hub_registration_error": "",
                "hub_registration_attempted_at": "",
                "hub_registered_at": "",
                "hub_registered": False,
            }
            settings["workerAssignedRing"] = ""
            settings["workerRegisteredId"] = ""
            settings["workerPricingPolicy"] = ""
            settings["workerConnectedHubUrl"] = hub_url
            settings["workerConnectionStatus"] = "connected"
            settings["workerConnectionError"] = ""
            settings["workerHubRegistration"] = {}
            settings["workerPool"] = {}
            settings["signedWorkerConnection"] = worker_connection
            settings = self._save_worker_settings(settings)

            attempted_at = datetime.now(timezone.utc).isoformat()
            worker_connection = dict(settings.get("signedWorkerConnection") if isinstance(settings.get("signedWorkerConnection"), dict) else worker_connection)
            worker_connection.update(
                {
                    "status": "registering-with-hub",
                    "worker_start_status": "ready",
                    "signed_order_status": "ready",
                    "hub_registration_status": "submitting",
                    "hub_registration_attempted_at": attempted_at,
                    "hub_registration_error": "",
                    "last_error": "",
                    "hub_registered": False,
                }
            )
            settings["signedWorkerConnection"] = worker_connection
            settings = self._save_worker_settings(settings)

            authorization_key_id = ""
            try:
                multisession_authorization = self._worker_multisession_authorization_for_wallet(
                    hub_url=hub_url,
                    wallet_address=wallet_address,
                    chain_id=chain_id,
                    requested_key_id=active_multisession_key_id,
                )
                authorization_key_id = str(multisession_authorization.get("key_id") or multisession_authorization.get("multisession_key_id") or "").strip()
                connect_payload = {
                    "hub_url": hub_url,
                    "network": selected,
                    "chain_id": chain_id,
                    "requested_ring": requested_ring,
                    "wallet_address": wallet_address,
                    "credit_wallet": wallet_address,
                    "worker": registration_payload,
                    "multisession_authorization": multisession_authorization,
                }
                registration = self._post_worker_start_to_hub(
                    hub_url=hub_url,
                    payload=connect_payload,
                )
            except Exception as register_exc:
                registration_error = str(register_exc)
                if self._worker_multisession_connect_error_message(registration_error) and authorization_key_id:
                    self._mark_worker_multisession_key_inactive_on_hub(
                        key_id=authorization_key_id,
                        hub_url=hub_url,
                        error_message=registration_error,
                    )
                failed_registration = {
                    "status": "failed",
                    "label": "Failed",
                    "hub_url": hub_url,
                    "error": registration_error,
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
                worker_connection.update(
                    {
                        "status": "hub-registration-failed",
                        "worker_start_status": "ready",
                        "signed_order_status": "ready",
                        "hub_registration_status": "failed",
                        "hub_registered": False,
                        "hub_registration_error": registration_error,
                        "last_error": registration_error,
                        "worker": registration_payload,
                        "hub_registration": failed_registration,
                    }
                )
                settings["workerAssignedRing"] = ""
                settings["workerRegisteredId"] = ""
                settings["workerPricingPolicy"] = ""
                settings["workerConnectedHubUrl"] = hub_url
                settings["workerConnectionStatus"] = "failed"
                settings["workerConnectionError"] = registration_error
                settings["workerHubRegistration"] = failed_registration
                settings["workerPool"] = {}
                settings["signedWorkerConnection"] = worker_connection
                self._save_worker_settings(settings)
                raise

            worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
            pool = registration.get("pool") if isinstance(registration.get("pool"), dict) else {}
            assigned_ring = str(registration.get("assigned_ring") or worker.get("assigned_ring") or requested_ring)
            worker_id = str(registration.get("worker_id") or worker.get("worker_id") or worker.get("node_id") or registration_payload["node_id"])
            pricing_policy = str(registration.get("pricing_policy") or worker.get("pricing_policy") or worker.get("capabilities", {}).get("pricing_policy", ""))
            worker_connection.update(
                {
                    "status": "hub-registered",
                    "worker_start_status": "ready",
                    "signed_order_status": "ready",
                    "hub_registration_status": "accepted",
                    "hub_registered": True,
                    "hub_registered_at": datetime.now(timezone.utc).isoformat(),
                    "hub_registration_error": "",
                    "last_error": "",
                    "assigned_ring": assigned_ring,
                    "worker_id": worker_id,
                    "pricing_policy": pricing_policy,
                    "hub_registration": registration,
                    "worker": worker,
                    "pool": pool,
                    "multisession_key_id": authorization_key_id,
                }
            )
            settings["workerRequestedRing"] = requested_ring
            settings["workerAssignedRing"] = assigned_ring
            settings["workerRegisteredId"] = worker_id
            settings["workerPricingPolicy"] = pricing_policy
            settings["workerConnectedHubUrl"] = hub_url
            settings["workerConnectionStatus"] = "connected"
            settings["workerConnectionError"] = ""
            settings["workerConnectedAt"] = datetime.now(timezone.utc).isoformat()
            settings["workerHubRegistration"] = registration
            settings["workerPool"] = pool
            settings["signedWorkerConnection"] = worker_connection
            saved = self._save_worker_settings(settings)
            self.server.signal(
                "api-worker-network-work-now-hub-register",
                selected=selected,
                ring=requested_ring,
                assigned_ring=assigned_ring,
                wallet=wallet_address,
                worker_id=worker_id,
                duration_seconds=duration_seconds,
            )
            send_network_and_runtime_payload(saved)
        except Exception as exc:
            self.server.signal("api-worker-network-work-now-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_multisession_key_cache_path(self) -> Path:
        # Worker multi-session key ids are authorization credentials. Keep them
        # under the ignored local runtime directory instead of the repository root.
        return self.server.debug_root / ".main_computer" / "worker_multisession_keys.json"

    def _worker_multisession_key_legacy_cache_path(self) -> Path:
        return self.server.debug_root / "worker_multisession_keys.json"

    def _normalize_worker_wallet_address(self, value: Any) -> str:
        address = str(value or "").strip().lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", address):
            raise ValueError("wallet_address must be a valid 0x address.")
        return address

    def _format_worker_credit_units(self, value: int) -> str:
        base = 10**18
        whole, fraction = divmod(max(0, int(value)), base)
        if not fraction:
            return str(whole)
        fraction_text = str(fraction).rjust(18, "0").rstrip("0")
        return f"{whole}.{fraction_text}"

    def _worker_base_units_to_hub_credit_wei(self, value: Any) -> int:
        base_units = int(value or 0)
        if base_units <= 0:
            raise ValueError("payment_amount_base_units must be positive.")
        return base_units

    def _worker_wallet_funding_credits_granted_wei(self, *, receipt: dict[str, Any], body: dict[str, Any], payment_amount_base_units: int) -> int:
        raw_value = receipt.get("credits_granted_wei", body.get("credits_granted_wei", None))
        if raw_value is None or str(raw_value).strip() == "":
            return self._worker_base_units_to_hub_credit_wei(payment_amount_base_units)

        credits_wei = int(raw_value or 0)
        if credits_wei <= 0:
            return self._worker_base_units_to_hub_credit_wei(payment_amount_base_units)
        return credits_wei

    def _worker_wallet_funding_deployment_manifest_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        raw_roots = [
            getattr(self.server, "debug_root", None),
            getattr(self.server.config, "workspace", None),
            getattr(self.server.config, "hub_root", None),
            Path.cwd(),
        ]
        for raw in raw_roots:
            if raw is None:
                continue
            try:
                base = Path(raw).resolve()
            except Exception:
                continue
            for root in [base, *base.parents]:
                candidates.append(root / "runtime" / "deployments" / "dev" / "latest.json")

        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _load_worker_wallet_funding_bridge_config(self) -> dict[str, Any]:
        last_error = ""
        for path in self._worker_wallet_funding_deployment_manifest_candidates():
            try:
                if not path.exists():
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                contracts = payload.get("contracts") if isinstance(payload.get("contracts"), dict) else {}
                escrow = contracts.get("hub_credit_bridge_escrow") if isinstance(contracts.get("hub_credit_bridge_escrow"), dict) else {}
                chain = payload.get("chain") if isinstance(payload.get("chain"), dict) else {}
                source = payload.get("source")
                contract_address = str(escrow.get("address") or "").strip()
                if not re.fullmatch(r"0x[0-9a-fA-F]{40}", contract_address):
                    raise ValueError("contracts.hub_credit_bridge_escrow.address is missing or invalid.")
                controller = str(escrow.get("bridge_controller_address") or "").strip()
                if controller and not re.fullmatch(r"0x[0-9a-fA-F]{40}", controller):
                    raise ValueError("contracts.hub_credit_bridge_escrow.bridge_controller_address is invalid.")
                chain_id = int(escrow.get("chain_id") or chain.get("chain_id") or self.server.config.xlag_chain_id or 0)
                if chain_id <= 0:
                    raise ValueError("deployment chain_id is missing.")
                rpc_url = str(chain.get("rpc_url") or chain.get("host_rpc_url") or self.server.config.energy_chain_rpc_url or "").strip()
                if not rpc_url:
                    raise ValueError("deployment RPC URL is missing.")

                return {
                    "ok": True,
                    "chain_id": chain_id,
                    "chain_id_hex": hex(chain_id),
                    "rpc_url": rpc_url,
                    "hub_credit_bridge_escrow_address": contract_address,
                    "contract_address": contract_address,
                    "bridge_controller_address": controller,
                    "deployment_manifest_path": str(path),
                    "source": source,
                    "funding_model": "hub_credit_bridge_escrow_wallet_v2",
                }
            except Exception as exc:
                last_error = f"{path}: {exc}"
                continue
        detail = f" Last error: {last_error}" if last_error else ""
        raise FileNotFoundError("Could not find runtime/deployments/dev/latest.json with hub_credit_bridge_escrow metadata." + detail)

    def _handle_worker_wallet_funding_config(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding config is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            config = self._load_worker_wallet_funding_bridge_config()
            self.server.signal(
                "api-worker-wallet-funding-config",
                contract_address=config.get("hub_credit_bridge_escrow_address"),
                chain_id=config.get("chain_id"),
            )
            self._send_json(config)
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-config-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_balance(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet balance checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            chain_id_hex = str(self.server.energy_chain.rpc("eth_chainId"))
            chain_id = int(chain_id_hex, 16)
            expected_chain_id = self.server.config.xlag_chain_id
            if expected_chain_id is not None and chain_id != expected_chain_id:
                raise RuntimeError(f"Local RPC chain id {chain_id} does not match expected {expected_chain_id}.")
            balance_base_units = int(self.server.energy_chain.get_balance(wallet_address))
            self.server.signal(
                "api-worker-wallet-balance",
                wallet_address=wallet_address,
                chain_id=chain_id,
                available_credits=self._format_worker_credit_units(balance_base_units),
            )
            self._send_json(
                {
                    "ok": True,
                    "wallet_address": wallet_address,
                    "chain_id": chain_id,
                    "chain_id_hex": chain_id_hex,
                    "expected_chain_id": expected_chain_id,
                    "balance_base_units": str(balance_base_units),
                    "available_credits": self._format_worker_credit_units(balance_base_units),
                    "source": "local-rpc",
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-wallet-balance-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _load_worker_multisession_key_cache(self) -> dict[str, Any]:
        path = self._worker_multisession_key_cache_path()
        source_path = path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            legacy_path = self._worker_multisession_key_legacy_cache_path()
            try:
                data = json.loads(legacy_path.read_text(encoding="utf-8"))
                source_path = legacy_path
            except (OSError, json.JSONDecodeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        keys = data.get("keys")
        if not isinstance(keys, dict):
            keys = {}
        data["keys"] = keys
        data.setdefault("version", "main-computer-worker-multisession-key-cache-v1")
        data["_cache_path"] = str(source_path)
        return data

    def _save_worker_multisession_key_cache(self, data: dict[str, Any]) -> None:
        clean_data = {key: value for key, value in data.items() if key != "_cache_path"}
        path = self._worker_multisession_key_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(clean_data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _normalize_worker_multisession_key_record(
        self,
        key: dict[str, Any],
        *,
        hub_url: str = "",
        fallback_wallet_address: str = "",
    ) -> dict[str, Any]:
        key_id = str(key.get("id") or "").strip()
        if not key_id:
            raise ValueError("multi-session key id is required.")
        wallet_address = self._normalize_worker_wallet_address(key.get("wallet_address") or fallback_wallet_address)
        return {
            "id": key_id,
            "status": str(key.get("status") or "active"),
            "created_at": str(key.get("created_at") or key.get("createdAt") or ""),
            "revoked_at": str(key.get("revoked_at") or key.get("revokedAt") or ""),
            "wallet_address": wallet_address,
            "chain_id": str(key.get("chain_id") or key.get("chainId") or ""),
            "request_id": str(key.get("request_id") or key.get("requestId") or ""),
            "origin": str(key.get("origin") or ""),
            "hub_url": self._clean_hub_url(str(key.get("hub_url") or hub_url), allow_empty=True),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _store_worker_multisession_key_from_hub_result(
        self,
        *,
        hub_url: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        key = result.get("key") if isinstance(result.get("key"), dict) else None
        if not key:
            return None
        verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
        fallback_wallet = (
            key.get("wallet_address")
            or verification.get("wallet_address")
            or verification.get("recovered_address")
            or ""
        )
        record = self._normalize_worker_multisession_key_record(
            key,
            hub_url=hub_url,
            fallback_wallet_address=str(fallback_wallet),
        )
        data = self._load_worker_multisession_key_cache()
        data["keys"][record["id"]] = record
        self._save_worker_multisession_key_cache(data)
        return record

    def _public_worker_multisession_key_record(self, record: dict[str, Any], *, reveal_key: bool = False) -> dict[str, Any]:
        """Return a UI-safe key record.

        The Hub key id is the bearer authorization credential in this lab flow, so
        load/revoke/status responses must not reveal it again after first issue.
        """
        public = {
            "status": str(record.get("status") or ""),
            "created_at": str(record.get("created_at") or ""),
            "revoked_at": str(record.get("revoked_at") or ""),
            "inactive_on_hub_at": str(record.get("inactive_on_hub_at") or ""),
            "wallet_address": str(record.get("wallet_address") or ""),
            "chain_id": str(record.get("chain_id") or ""),
            "hub_url": str(record.get("hub_url") or ""),
            "server_side_key": True,
            "key_redacted": not reveal_key,
            "updated_at": str(record.get("updated_at") or ""),
        }
        if record.get("last_error"):
            public["last_error"] = str(record.get("last_error") or "")
        if reveal_key:
            public["id"] = str(record.get("id") or "")
        return public

    def _public_worker_multisession_key_records(self, records: list[dict[str, Any]], *, reveal_key: bool = False) -> list[dict[str, Any]]:
        return [self._public_worker_multisession_key_record(record, reveal_key=reveal_key) for record in records]

    def _select_worker_multisession_key_record(
        self,
        *,
        wallet_address: str,
        hub_url: str = "",
        key_id: str = "",
        status: str = "active",
    ) -> dict[str, Any] | None:
        normalized_wallet = self._normalize_worker_wallet_address(wallet_address)
        wanted_status = str(status or "").strip().lower()
        records = self._worker_multisession_keys_for_wallet(wallet_address=normalized_wallet, hub_url=hub_url)
        for record in records:
            if key_id and str(record.get("id") or "") != key_id:
                continue
            if wanted_status and str(record.get("status") or "").strip().lower() != wanted_status:
                continue
            return record
        return None

    def _worker_multisession_keys_for_wallet(
        self,
        *,
        wallet_address: str,
        hub_url: str = "",
    ) -> list[dict[str, Any]]:
        normalized_wallet = self._normalize_worker_wallet_address(wallet_address)
        normalized_hub_url = self._clean_hub_url(hub_url, allow_empty=True)
        data = self._load_worker_multisession_key_cache()
        records: list[dict[str, Any]] = []
        for value in data.get("keys", {}).values():
            if not isinstance(value, dict):
                continue
            try:
                record = self._normalize_worker_multisession_key_record(
                    value,
                    hub_url=str(value.get("hub_url") or ""),
                    fallback_wallet_address=str(value.get("wallet_address") or ""),
                )
            except ValueError:
                continue
            if record["wallet_address"] != normalized_wallet:
                continue
            if normalized_hub_url and record.get("hub_url") and record["hub_url"] != normalized_hub_url:
                continue
            records.append(record)
        records.sort(key=lambda item: (item.get("status") != "active", item.get("created_at", ""), item.get("id", "")), reverse=False)
        return records

    def _worker_multisession_authorization_for_wallet(
        self,
        *,
        hub_url: str,
        wallet_address: str,
        chain_id: str = "",
        requested_key_id: str = "",
    ) -> dict[str, Any]:
        normalized_wallet = self._normalize_worker_wallet_address(wallet_address)
        key_id = str(requested_key_id or "").strip()
        records = self._worker_multisession_keys_for_wallet(wallet_address=normalized_wallet, hub_url=hub_url)
        active_records = [record for record in records if str(record.get("status") or "").strip().lower() == "active"]
        if key_id:
            active_records = [record for record in active_records if str(record.get("id") or "") == key_id]
        if not active_records:
            if key_id:
                raise ValueError(
                    "No active saved multi-session key with that id was found for this wallet and Hub. "
                    "Request a multi-session key for this network's Hub before connecting."
                )
            raise ValueError(
                "No active saved multi-session key was found for this wallet and Hub. "
                "Request a multi-session key for this network's Hub before connecting."
            )
        record = active_records[0]
        resolved_key_id = str(record.get("id") or "").strip()
        if not resolved_key_id:
            raise ValueError("Saved multi-session key record is missing its key id.")
        return {
            "kind": "multisession_key",
            "wallet_address": normalized_wallet,
            "multisession_key_id": resolved_key_id,
            "key_id": resolved_key_id,
            "chain_id": str(record.get("chain_id") or chain_id or "").strip(),
        }

    def _handle_worker_multisession_keys_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker multi-session key load is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or ""), allow_empty=True)
            keys = self._worker_multisession_keys_for_wallet(wallet_address=wallet_address, hub_url=hub_url)
            active_key = next((key for key in keys if key.get("status") == "active"), None)
            public_keys = self._public_worker_multisession_key_records(keys, reveal_key=False)
            public_active_key = self._public_worker_multisession_key_record(active_key, reveal_key=False) if active_key else None
            self.server.signal(
                "api-worker-multisession-keys-load",
                hub_url=hub_url,
                wallet_address=wallet_address,
                key_count=len(keys),
                active_key_present=bool(active_key),
            )
            self._send_json(
                {
                    "ok": True,
                    "wallet_address": wallet_address,
                    "hub_url": hub_url,
                    "keys": public_keys,
                    "active_key": public_active_key,
                    "key_ids_redacted": True,
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-multisession-keys-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_multisession_key_revoke(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker multi-session key revocation is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or ""), allow_empty=True)
            key_id = str(body.get("key_id") or body.get("multisession_key_id") or "").strip()

            data = self._load_worker_multisession_key_cache()
            record = self._select_worker_multisession_key_record(
                wallet_address=wallet_address,
                hub_url=hub_url,
                key_id=key_id,
                status="active",
            )
            if not record:
                raise ValueError("No active saved multi-session key was found for this wallet and Hub.")
            resolved_key_id = str(record.get("id") or "").strip()
            now = datetime.now(timezone.utc).isoformat()
            stored_record = data.get("keys", {}).get(resolved_key_id)
            if not isinstance(stored_record, dict):
                stored_record = dict(record)
            stored_record["status"] = "revoked"
            stored_record["revoked_at"] = now
            stored_record["updated_at"] = now
            stored_record["hub_url"] = self._clean_hub_url(str(stored_record.get("hub_url") or hub_url), allow_empty=True)
            data["keys"][resolved_key_id] = stored_record
            self._save_worker_multisession_key_cache(data)

            hub_revoke: dict[str, Any] = {"ok": False, "skipped": True}
            if hub_url:
                try:
                    hub_revoke = self._post_worker_multisession_key_revoke_to_hub(
                        hub_url=hub_url,
                        key_id=resolved_key_id,
                        wallet_address=wallet_address,
                    )
                except Exception as exc:
                    hub_revoke = {"ok": False, "error": str(exc)}

            keys = self._worker_multisession_keys_for_wallet(wallet_address=wallet_address, hub_url=hub_url)
            self.server.signal(
                "api-worker-multisession-key-revoke",
                hub_url=hub_url,
                wallet_address=wallet_address,
                hub_revoked=bool(hub_revoke.get("ok")),
            )
            self._send_json(
                {
                    "ok": True,
                    "wallet_address": wallet_address,
                    "hub_url": hub_url,
                    "revoked": True,
                    "key": self._public_worker_multisession_key_record(stored_record, reveal_key=False),
                    "keys": self._public_worker_multisession_key_records(keys, reveal_key=False),
                    "active_key": None,
                    "hub_revoke": hub_revoke,
                    "key_ids_redacted": True,
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-multisession-key-revoke-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_balance(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding balance checks are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            balance = self._fetch_worker_wallet_funding_balance_from_hub(hub_url=hub_url, wallet_address=wallet_address)
            self.server.signal(
                "api-worker-wallet-funding-balance",
                hub_url=hub_url,
                wallet_address=wallet_address,
                available_credits=(balance.get("account") or {}).get("available_credits", 0) if isinstance(balance.get("account"), dict) else 0,
            )
            self._send_json({"ok": True, "hub_url": hub_url, **balance})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-balance-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_complete(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding completion is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            receipt = body.get("deposit_receipt")
            if not isinstance(receipt, dict):
                receipt = body
            deposit_id = str(receipt.get("deposit_id", body.get("deposit_id", ""))).strip().lower()
            forwarded = {
                "wallet_address": wallet_address,
                "deposit_id": deposit_id,
                "chain_id": int(receipt.get("chain_id", body.get("chain_id", 0)) or 0),
                "contract_address": str(receipt.get("contract_address", body.get("contract_address", ""))),
                "tx_hash": str(receipt.get("tx_hash", receipt.get("transaction_hash", body.get("tx_hash", "")))),
            }
            result = self._post_worker_wallet_funding_completion_to_hub(hub_url=hub_url, payload=forwarded)
            self.server.signal(
                "api-worker-wallet-funding-complete",
                hub_url=hub_url,
                wallet_address=wallet_address,
                deposit_id=deposit_id,
                tx_hash=forwarded["tx_hash"],
                idempotent=bool(result.get("idempotent")),
            )
            self._send_json({"ok": True, "hub_url": hub_url, **result})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-complete-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_worker_wallet_funding_import(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker wallet funding imports are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            wallet_address = self._normalize_worker_wallet_address(body.get("wallet_address"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            receipt = body.get("deposit_receipt")
            if not isinstance(receipt, dict):
                receipt = body

            payment_amount_base_units = int(receipt.get("payment_amount_base_units", body.get("payment_amount_base_units", 0)) or 0)
            forwarded = {
                "wallet_address": wallet_address,
                "account_id": wallet_address,
                "chain_id": int(receipt.get("chain_id", body.get("chain_id", 0)) or 0),
                "contract_address": str(receipt.get("contract_address", body.get("contract_address", ""))),
                "tx_hash": str(receipt.get("tx_hash", receipt.get("transaction_hash", body.get("tx_hash", "")))),
                "log_index": int(receipt.get("log_index", body.get("log_index", 0)) or 0),
                "block_number": int(receipt.get("block_number", body.get("block_number", 0)) or 0),
                "payer_address": str(receipt.get("payer_address", body.get("payer_address", wallet_address)) or wallet_address),
                "payment_asset": str(receipt.get("payment_asset", body.get("payment_asset", "native")) or "native"),
                "payment_amount_base_units": payment_amount_base_units,
                "credits_granted_wei": self._worker_wallet_funding_credits_granted_wei(
                    receipt=receipt,
                    body=body,
                    payment_amount_base_units=payment_amount_base_units,
                ),
                "memo": str(receipt.get("memo", body.get("memo", "Worker wallet bridge funding import"))),
            }
            result = self._post_worker_wallet_funding_import_to_hub(hub_url=hub_url, payload=forwarded)
            self.server.signal(
                "api-worker-wallet-funding-import",
                hub_url=hub_url,
                wallet_address=wallet_address,
                tx_hash=forwarded["tx_hash"],
                idempotent=bool(result.get("idempotent")),
            )
            self._send_json({"ok": True, "hub_url": hub_url, **result})
        except Exception as exc:
            self.server.signal("api-worker-wallet-funding-import-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _wallet_dns_control_profiles_path(self) -> Path:
        return self.server.debug_root / "wallet_dns_control_profiles.json"

    def _load_wallet_dns_control_profiles(self) -> list[dict[str, Any]]:
        path = self._wallet_dns_control_profiles_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return []
        profiles = payload.get("profiles") if isinstance(payload, dict) else payload
        if not isinstance(profiles, list):
            return []
        return [dict(item) for item in profiles if isinstance(item, dict)][:100]

    def _save_wallet_dns_control_profiles(self, profiles: list[dict[str, Any]]) -> None:
        path = self._wallet_dns_control_profiles_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"profiles": [dict(item) for item in profiles[:100]]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _normalize_wallet_dns_control_mode(self, value: Any) -> str:
        mode = str(value or "cloudflare").strip().lower()
        if mode in {"cloudflare", "cloudflare-managed", "cf"}:
            return "cloudflare"
        if mode in {"self-hosted", "self_hosted", "own-dns", "own_dns", "authoritative"}:
            return "self-hosted"
        raise ValueError("provider_mode must be either cloudflare or self-hosted.")

    def _normalize_wallet_dns_control_text(self, value: Any, *, field: str, required: bool = True, max_length: int = 253) -> str:
        text = str(value or "").strip()
        if required and not text:
            raise ValueError(f"{field} is required.")
        if len(text) > max_length:
            raise ValueError(f"{field} must be {max_length} characters or fewer.")
        if any(ch in text for ch in "\r\n\x00"):
            raise ValueError(f"{field} contains unsafe characters.")
        return text

    def _normalize_wallet_dns_control_zone(self, value: Any) -> str:
        zone = self._normalize_wallet_dns_control_text(value, field="zone").rstrip(".").lower()
        if not re.fullmatch(r"(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", zone):
            raise ValueError("zone must be a valid domain name.")
        return zone

    def _normalize_wallet_dns_control_record_name(self, value: Any) -> str:
        name = self._normalize_wallet_dns_control_text(value or "@", field="record_name", max_length=253)
        return name.rstrip(".") or "@"

    def _normalize_wallet_dns_control_record_type(self, value: Any) -> str:
        record_type = str(value or "A").strip().upper()
        allowed = {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV"}
        if record_type not in allowed:
            raise ValueError("record_type must be one of: A, AAAA, CNAME, MX, TXT, NS, CAA, SRV.")
        return record_type

    def _normalize_wallet_dns_control_ttl(self, value: Any) -> int:
        try:
            ttl = int(str(value or "300").strip())
        except (TypeError, ValueError):
            raise ValueError("ttl must be a whole number of seconds.") from None
        if ttl < 60 or ttl > 86400:
            raise ValueError("ttl must be between 60 and 86400 seconds.")
        return ttl

    def _wallet_dns_control_defaults(self) -> dict[str, Any]:
        return {
            "provider_mode": "cloudflare",
            "ttl": 300,
            "record_type": "A",
            "cloudflare_token_env": "CLOUDFLARE_API_TOKEN",
            "self_hosted_nameserver_hint": "ns1.example.com",
        }

    def _wallet_dns_control_status_message(self, profile: dict[str, Any] | None = None) -> str:
        if not profile:
            return "Connect wallet, choose Cloudflare or self-hosted DNS, then save a control profile."
        provider = "Cloudflare DNS" if profile.get("provider_mode") == "cloudflare" else "self-hosted authoritative DNS"
        return f"Saved {provider} control profile for {profile.get('zone', 'zone')}."

    def _handle_wallet_dns_control_profiles_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet DNS control is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            profiles = self._load_wallet_dns_control_profiles()
            self.server.signal("api-wallet-dns-control-load", count=len(profiles))
            self._send_json({
                "ok": True,
                "defaults": self._wallet_dns_control_defaults(),
                "status_message": self._wallet_dns_control_status_message(profiles[0] if profiles else None),
                "profiles": profiles[:20],
            })
        except Exception as exc:
            self.server.signal("api-wallet-dns-control-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_wallet_dns_control_profile_save(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet DNS control is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            owner_wallet = self._normalize_worker_wallet_address(body.get("owner_wallet", body.get("wallet_address", "")))
            provider_mode = self._normalize_wallet_dns_control_mode(body.get("provider_mode"))
            zone = self._normalize_wallet_dns_control_zone(body.get("zone"))
            record_name = self._normalize_wallet_dns_control_record_name(body.get("record_name", "@"))
            record_type = self._normalize_wallet_dns_control_record_type(body.get("record_type", "A"))
            record_value = self._normalize_wallet_dns_control_text(body.get("record_value"), field="record_value", max_length=2048)
            ttl = self._normalize_wallet_dns_control_ttl(body.get("ttl", 300))
            proxied = self._coerce_bool(body.get("proxied"), default=False)
            nameserver_host = self._normalize_wallet_dns_control_text(body.get("nameserver_host", ""), field="nameserver_host", required=False)
            admin_url = self._normalize_wallet_dns_control_text(body.get("admin_url", ""), field="admin_url", required=False, max_length=500)

            if provider_mode == "self-hosted" and not nameserver_host and not admin_url:
                raise ValueError("self-hosted DNS requires nameserver_host or admin_url.")
            if provider_mode == "cloudflare":
                nameserver_host = ""
                admin_url = ""

            profile = {
                "id": f"dns_profile_{uuid.uuid4().hex[:16]}",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "owner_wallet": owner_wallet,
                "provider_mode": provider_mode,
                "zone": zone,
                "record_name": record_name,
                "record_type": record_type,
                "record_value": record_value,
                "ttl": ttl,
                "proxied": bool(proxied) if provider_mode == "cloudflare" else False,
                "nameserver_host": nameserver_host,
                "admin_url": admin_url,
                "control_actions": [
                    "cloudflare_dns_records" if provider_mode == "cloudflare" else "self_hosted_authoritative_zone",
                    "wallet_owned_dns_profile",
                ],
                "secret_policy": "Store provider API tokens outside the browser; use environment variables or the self-hosted DNS admin service.",
            }
            profiles = [profile, *self._load_wallet_dns_control_profiles()]
            self._save_wallet_dns_control_profiles(profiles)
            self.server.signal(
                "api-wallet-dns-control-save",
                provider_mode=provider_mode,
                zone=zone,
                owner_wallet=owner_wallet,
            )
            self._send_json({
                "ok": True,
                "profile": profile,
                "profiles": profiles[:20],
                "defaults": self._wallet_dns_control_defaults(),
                "status_message": self._wallet_dns_control_status_message(profile),
            })
        except Exception as exc:
            self.server.signal("api-wallet-dns-control-save-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _wallet_agent_credit_grants_path(self) -> Path:
        return self.server.debug_root / "wallet_agent_credit_grants.json"

    def _load_wallet_agent_credit_grants_history(self) -> list[dict[str, Any]]:
        path = self._wallet_agent_credit_grants_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except json.JSONDecodeError:
            return []
        grants = payload.get("grants") if isinstance(payload, dict) else payload
        if not isinstance(grants, list):
            return []
        return [dict(item) for item in grants if isinstance(item, dict)][:100]

    def _save_wallet_agent_credit_grants_history(self, grants: list[dict[str, Any]]) -> None:
        path = self._wallet_agent_credit_grants_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"grants": [dict(item) for item in grants[:100]]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _normalize_wallet_agent_credit_grant_amount(self, value: Any) -> int:
        try:
            credits = int(str(value or "").strip())
        except (TypeError, ValueError):
            raise ValueError("credits must be a whole number.") from None
        if credits < 1 or credits > 100:
            raise ValueError("credits must be between 1 and 100 for wallet helper grants.")
        return credits

    def _handle_wallet_agent_credit_grants_load(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet agent credit grants are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            grants = self._load_wallet_agent_credit_grants_history()
            hub_url = self._clean_hub_url(str(getattr(self.server.config, "hub_url", "") or "http://127.0.0.1:8770"))
            self.server.signal("api-wallet-agent-credit-grants-load", count=len(grants), hub_url=hub_url)
            self._send_json({"ok": True, "hub_url": hub_url, "grants": grants[:20]})
        except Exception as exc:
            self.server.signal("api-wallet-agent-credit-grants-load-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _handle_wallet_agent_credit_grant_create(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Wallet agent credit grants are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            issuer_wallet = self._normalize_worker_wallet_address(body.get("issuer_wallet"))
            recipient_wallet = self._normalize_worker_wallet_address(body.get("recipient_wallet", body.get("account_id", "")))
            credits = self._normalize_wallet_agent_credit_grant_amount(body.get("credits"))
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url or "http://127.0.0.1:8770"))
            memo = str(body.get("memo") or "Agent helper credits for parallel verification workers.").strip()
            if not memo:
                memo = "Agent helper credits for parallel verification workers."

            forwarded = {
                "account_id": recipient_wallet,
                "owner_address": recipient_wallet,
                "credits": credits,
                "memo": memo,
                "metadata": {
                    "source": "wallet_agent_credit_grant",
                    "agent_credit_grant": True,
                    "issuer_wallet": issuer_wallet,
                    "recipient_wallet": recipient_wallet,
                },
            }
            result = self._post_wallet_agent_credit_grant_to_hub(hub_url=hub_url, payload=forwarded)
            transaction = result.get("transaction") if isinstance(result.get("transaction"), dict) else {}
            grant = {
                "id": transaction.get("transaction_id") or f"agent_grant_{uuid.uuid4().hex[:16]}",
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "hub_url": hub_url,
                "issuer_wallet": issuer_wallet,
                "recipient_wallet": recipient_wallet,
                "account_id": recipient_wallet,
                "credits": credits,
                "memo": memo,
                "transaction_id": str(transaction.get("transaction_id", "")),
            }
            grants = [grant, *self._load_wallet_agent_credit_grants_history()]
            self._save_wallet_agent_credit_grants_history(grants)
            self.server.signal(
                "api-wallet-agent-credit-grant-create",
                hub_url=hub_url,
                issuer_wallet=issuer_wallet,
                recipient_wallet=recipient_wallet,
                credits=credits,
                transaction_id=grant["transaction_id"],
            )
            self._send_json({"ok": True, "hub_url": hub_url, "grant": grant, "hub_result": result})
        except Exception as exc:
            self.server.signal("api-wallet-agent-credit-grant-create-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _post_wallet_agent_credit_grant_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/credits/admin/issue",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object agent credit grant response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data


    def _handle_worker_multisession_key_request(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker multi-session key requests are only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            signed_request = body.get("signed_request")
            if not isinstance(signed_request, dict):
                raise ValueError("signed_request object is required.")
            if signed_request.get("kind") != "main_computer_multisession_key_request":
                raise ValueError("signed_request.kind must be main_computer_multisession_key_request.")
            if signed_request.get("signing_method") != "personal_sign":
                raise ValueError("signed_request.signing_method must be personal_sign.")
            if not str(signed_request.get("signature") or "").startswith("0x"):
                raise ValueError("signed_request.signature is required.")

            request_wallet = self._normalize_worker_wallet_address(
                signed_request.get("message", {}).get("wallet_address")
                if isinstance(signed_request.get("message"), dict)
                else signed_request.get("wallet_address") or body.get("wallet_address")
            )
            existing_active = self._select_worker_multisession_key_record(
                wallet_address=request_wallet,
                hub_url=hub_url,
                status="active",
            )
            if existing_active:
                raise ValueError("An active saved multi-session key already exists for this wallet and Hub. Revoke it before requesting a replacement.")

            forwarded = {
                "signed_request": signed_request,
                "client_metadata": dict(body.get("client_metadata", {})) if isinstance(body.get("client_metadata"), dict) else {},
            }
            result = self._post_multisession_key_request_to_hub(hub_url=hub_url, payload=forwarded)
            key = result.get("key") if isinstance(result.get("key"), dict) else {}
            local_record = self._store_worker_multisession_key_from_hub_result(hub_url=hub_url, result=result)
            reveal_key = not bool(result.get("idempotent"))
            response_key = key if reveal_key else self._public_worker_multisession_key_record(local_record or key, reveal_key=False)
            self.server.signal(
                "api-worker-multisession-key-request",
                hub_url=hub_url,
                key_id=key.get("id", ""),
                local_cached=bool(local_record),
                key_revealed=reveal_key,
            )
            response = {
                "ok": True,
                "hub_url": hub_url,
                **{name: value for name, value in result.items() if name != "key"},
                "key": response_key,
                "key_revealed_once": reveal_key,
            }
            if local_record:
                response["local_cache"] = {
                    "stored": True,
                    "key": self._public_worker_multisession_key_record(local_record, reveal_key=False),
                    "path": str(self._worker_multisession_key_cache_path()),
                }
            self._send_json(response)
        except Exception as exc:
            self.server.signal("api-worker-multisession-key-request-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _worker_seller_availability_from_payload(self, worker_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        raw_availability = worker_payload.get("availability")
        availability = dict(raw_availability) if isinstance(raw_availability, dict) else {}
        capabilities = worker_payload.get("capabilities") if isinstance(worker_payload.get("capabilities"), dict) else {}
        if not availability and isinstance(capabilities.get("availability"), dict):
            availability = dict(capabilities["availability"])

        def boolish(raw: Any, default: bool = False) -> bool:
            if isinstance(raw, bool):
                return raw
            text = str(raw or "").strip().lower()
            if text in {"1", "true", "yes", "on", "enabled", "enable"}:
                return True
            if text in {"0", "false", "no", "off", "disabled", "disable"}:
                return False
            return bool(default)

        accept_paid_jobs = boolish(availability.get("accept_paid_jobs", availability.get("seller_enabled", True)), True)
        raw_only_when_idle = availability.get("only_when_idle", availability.get("seller_only_when_idle"))
        default_mode = self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE if boolish(raw_only_when_idle, True) else self._WORKER_SELLER_AVAILABILITY_AI_IDLE
        availability_mode = self._normalize_worker_seller_availability_mode(availability.get("availability_mode"), default=default_mode)
        only_when_idle = availability_mode == self._WORKER_SELLER_AVAILABILITY_TOTAL_IDLE
        cleaned = {
            "accept_paid_jobs": accept_paid_jobs,
            "availability_mode": availability_mode,
            "only_when_idle": only_when_idle,
            "idle_source": "windows_user_activity_v1" if only_when_idle else "local_ai_capacity_v1",
            "ai_idle_required": availability_mode == self._WORKER_SELLER_AVAILABILITY_AI_IDLE,
        }
        user_activity: dict[str, Any] | None = None
        if only_when_idle:
            user_activity = collect_windows_user_activity()
            cleaned["last_user_activity"] = user_activity
            cleaned["idle_verified"] = user_activity.get("active") is False
        else:
            cleaned["idle_verified"] = None

        return cleaned, user_activity

    def _enforce_worker_seller_availability(self, availability: dict[str, Any], user_activity: dict[str, Any] | None) -> None:
        if not bool(availability.get("accept_paid_jobs")):
            raise ValueError("Accept paid jobs is off; this machine will not register a seller offer.")
        if not bool(availability.get("only_when_idle")):
            return
        active = user_activity.get("active") if isinstance(user_activity, dict) else None
        if active is True:
            raise ValueError("Only when totally idle is selected, but Windows reports an active interactive user session.")
        if active is not False:
            reason = str(user_activity.get("reason") or "idle status unavailable") if isinstance(user_activity, dict) else "idle status unavailable"
            raise ValueError(f"Only when totally idle is selected, but this machine's idle status could not be verified: {reason}.")

    def _worker_registration_payload_from_ui(self, worker_payload: dict[str, Any]) -> dict[str, Any]:
        models = [str(item).strip() for item in worker_payload.get("models", []) if str(item).strip()] if isinstance(worker_payload.get("models"), list) else []
        model = str(worker_payload.get("model") or (models[0] if models else "")).strip()
        if model and model not in models:
            models.insert(0, model)
        models_text = self._worker_seller_model_text(models)
        models = [item.strip() for item in models_text.split(",") if item.strip()]
        model = models[0] if models else ""
        if not models:
            raise ValueError("At least one worker model is required.")

        pricing = dict(worker_payload.get("pricing", {})) if isinstance(worker_payload.get("pricing"), dict) else {}

        def target_tokens(raw: Any, default: int = self._WORKER_DEFAULT_SELLER_TARGET_TOKENS) -> int:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                parsed = int(default)
            return min(128_000, max(1, parsed))

        target_output_tokens = target_tokens(
            pricing.get(
                "target_output_tokens",
                pricing.get("target_tokens_per_request", worker_payload.get("target_output_tokens", worker_payload.get("target_tokens"))),
            )
        )
        credits_per_token = self._worker_seller_credit_per_token_text(
            pricing.get("credits_per_token", worker_payload.get("credits_per_token", worker_payload.get("sellerCreditsPerToken"))),
            self._WORKER_DEFAULT_CREDITS_PER_TOKEN,
        )
        credits_per_token_wei_source = pricing.get("credits_per_token_wei", worker_payload.get("credits_per_token_wei"))
        if credits_per_token_wei_source not in (None, ""):
            credits_per_token_wei = self._worker_credit_amount_wei_text(
                credits_per_token_wei_source,
                credits_per_token,
                value_is_wei=True,
            )
        else:
            credits_per_token_wei = self._worker_credit_amount_wei_text(credits_per_token, credits_per_token)
        estimated_credits_per_request, estimated_credits_per_request_wei = self._worker_estimated_request_credits_from_token_rate(
            credits_per_token,
            target_output_tokens,
        )
        credits_per_request = estimated_credits_per_request
        credits_per_request_wei = estimated_credits_per_request_wei
        legacy_credits_per_request = self._worker_legacy_credit_amount_ceiling_text(credits_per_request_wei, credits_per_request)

        execution = dict(worker_payload.get("execution", {})) if isinstance(worker_payload.get("execution"), dict) else {}
        execution_mode = str(execution.get("mode") or worker_payload.get("execution_mode") or "worker_pull_v0").strip() or "worker_pull_v0"
        max_concurrency = max(1, int(execution.get("max_concurrency", worker_payload.get("max_concurrency", 1)) or 1))
        capabilities = dict(worker_payload.get("capabilities", {})) if isinstance(worker_payload.get("capabilities"), dict) else {}
        capabilities.setdefault("capabilities", ["chat.completions"])
        capabilities["pricing"] = {
            "pricing_type": str(pricing.get("pricing_type") or "approx_per_token_v0"),
            "credits_per_token": credits_per_token,
            "credits_per_token_wei": credits_per_token_wei,
            "target_output_tokens": target_output_tokens,
            "estimated_credits_per_request": estimated_credits_per_request,
            "estimated_credits_per_request_wei": estimated_credits_per_request_wei,
            "credits_per_request": credits_per_request,
            "credits_per_request_wei": credits_per_request_wei,
            "unit": str(pricing.get("unit") or "compute_credit"),
        }
        capabilities["execution"] = {
            "mode": execution_mode,
            "max_concurrency": max_concurrency,
        }
        capabilities["phase12_worker_seller_offer_ui"] = True
        availability, user_activity = self._worker_seller_availability_from_payload(worker_payload)
        self._enforce_worker_seller_availability(availability, user_activity)
        capabilities["availability"] = availability
        capabilities["target_output_tokens"] = target_output_tokens

        payload = {
            "node_id": str(worker_payload.get("node_id") or "").strip(),
            "endpoint": self._clean_hub_url(str(worker_payload.get("endpoint") or "")),
            "model": model,
            "models": models,
            "credits_per_token": credits_per_token,
            "credits_per_token_wei": credits_per_token_wei,
            "estimated_credits_per_request": estimated_credits_per_request,
            "estimated_credits_per_request_wei": estimated_credits_per_request_wei,
            "credits_per_request": legacy_credits_per_request,
            "credits_per_request_wei": credits_per_request_wei,
            "target_output_tokens": target_output_tokens,
            "max_concurrency": max_concurrency,
            "queue_depth": max(0, int(worker_payload.get("queue_depth", 0) or 0)),
            "active_requests": max(0, int(worker_payload.get("active_requests", 0) or 0)),
            "pricing": capabilities["pricing"],
            "execution": capabilities["execution"],
            "capabilities": capabilities,
        }
        if not payload["node_id"]:
            raise ValueError("worker node_id is required.")
        return payload

    def _handle_worker_offer_register(self) -> None:
        try:
            if not self._worker_ui_client_is_local():
                self._send_json({"ok": False, "error": "Worker offer registration is only available to local viewport clients."}, status=HTTPStatus.FORBIDDEN)
                return
            body = self._read_json()
            hub_url = self._clean_hub_url(str(body.get("hub_url") or self.server.config.hub_url))
            worker_payload = body.get("worker")
            if not isinstance(worker_payload, dict):
                raise ValueError("worker registration payload is required.")

            payload = self._worker_registration_payload_from_ui(worker_payload)
            registration = self._post_worker_registration_to_hub(hub_url=hub_url, payload=payload)
            worker = registration.get("worker") if isinstance(registration.get("worker"), dict) else {}
            offer = worker.get("offer") if isinstance(worker.get("offer"), dict) else {}
            self.server.signal(
                "api-worker-offer-register",
                hub_url=hub_url,
                node_id=payload["node_id"],
                offer_id=offer.get("offer_id", ""),
            )
            self._send_json(
                {
                    "ok": True,
                    "hub_url": hub_url,
                    "registration": registration,
                    "worker": worker,
                    "offer": offer,
                }
            )
        except Exception as exc:
            self.server.signal("api-worker-offer-register-error", error=exc)
            self._send_json({"ok": False, "error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _hub_config_payload(self) -> dict[str, Any]:
        saved = self._load_hub_config()
        hub_url = self._clean_hub_url(str(saved.get("hub_url") or self.server.config.hub_url))
        return {
            "ok": True,
            "provider": self.server.config.provider,
            "active_provider": self.server.provider_name,
            "model": self.server.config.model,
            "hub_url": self.server.config.hub_url,
            "hub_client_node_id": self.server.config.hub_client_node_id,
            "hub_high_security": self.server.config.hub_high_security,
            "hub_timeout_s": self.server.config.hub_timeout_s,
            "is_hub_provider": self.server.config.provider == "hub",
            "saved": saved,
            "local_hub_status": self._fetch_hub_status(hub_url),
        }

    def _load_hub_config(self) -> dict[str, Any]:
        path = self.server.debug_root / "hub_configuration.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            hub_timeout_s = float(data.get("hub_timeout_s", self.server.config.hub_timeout_s) or self.server.config.hub_timeout_s)
        except (TypeError, ValueError):
            hub_timeout_s = self.server.config.hub_timeout_s
        return {
            "hub_url": str(data.get("hub_url") or self.server.config.hub_url),
            "hub_client_node_id": str(data.get("hub_client_node_id") or self.server.config.hub_client_node_id),
            "hub_high_security": self._coerce_bool(data.get("hub_high_security"), default=self.server.config.hub_high_security),
            "hub_timeout_s": hub_timeout_s,
            "upstream_hub_url": str(data.get("upstream_hub_url") or ""),
        }

    def _save_hub_config(self, data: dict[str, Any]) -> dict[str, Any]:
        path = self.server.debug_root / "hub_configuration.json"
        existing = self._load_hub_config()
        saved = {**existing, **data}
        saved.pop("provider", None)
        saved["hub_url"] = self._clean_hub_url(str(saved.get("hub_url") or self.server.config.hub_url))
        saved["upstream_hub_url"] = self._clean_hub_url(str(saved.get("upstream_hub_url") or ""), allow_empty=True)
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        return saved

    def _clean_hub_url(self, value: str, *, allow_empty: bool = False) -> str:
        clean = str(value or "").strip().rstrip("/")
        if allow_empty and not clean:
            return ""
        if not clean:
            raise ValueError("Hub URL is required.")
        if not clean.startswith(("http://", "https://")):
            raise ValueError("Hub URL must start with http:// or https://.")
        return clean

    def _hub_json_request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": "MainComputerWorker/0.1",
            "Accept": "application/json",
        }
        if extra:
            headers.update({str(key): str(value) for key, value in extra.items()})
        return headers

    def _fetch_hub_status(self, hub_url: str) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/status",
            headers=self._hub_json_request_headers(),
        )
        try:
            with urlopen(request, timeout=2.0) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Hub returned a non-object response.")
            return {"reachable": True, "status": data}
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            detail = body or exc.reason or "Forbidden"
            return {"reachable": False, "http_status": exc.code, "error": f"Hub returned HTTP {exc.code}: {detail}"}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)}

    def _register_upstream_hub(
        self,
        *,
        local_hub_url: str,
        upstream_hub_url: str,
        node_id: str,
        credits_per_request: Any,
    ) -> dict[str, Any]:
        credit_price_wei = credit_decimal_text_to_wei(credits_per_request, default="1", minimum_wei=1)
        payload = {
            "node_id": node_id,
            "endpoint": self._clean_hub_url(upstream_hub_url),
            "credits_per_request": credit_wei_to_decimal_text(credit_price_wei),
            "credits_per_request_wei": str(credit_price_wei),
        }
        request = Request(
            self._clean_hub_url(local_hub_url) + "/api/hub/upstreams/register",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Local hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Local hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Local hub returned a non-object upstream registration response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_multisession_key_request_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/credits/multisession-keys/request",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object multi-session key response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_worker_multisession_key_revoke_to_hub(self, *, hub_url: str, key_id: str, wallet_address: str) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/credits/multisession-keys/revoke",
            data=json.dumps(
                {
                    "key_id": str(key_id or "").strip(),
                    "wallet_address": self._normalize_worker_wallet_address(wallet_address),
                    "reason": "worker-ui-revoke",
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object multi-session key revocation response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _fetch_worker_wallet_funding_balance_from_hub(self, *, hub_url: str, wallet_address: str) -> dict[str, Any]:
        query = urlencode({"wallet_address": wallet_address})
        request = Request(
            self._clean_hub_url(hub_url) + f"/api/hub/v1/credits/balance?{query}",
            headers=self._hub_json_request_headers(),
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object wallet funding balance response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _post_worker_wallet_funding_completion_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Ask Hub to complete a bridge escrow deposit by deposit id.

        The browser wallet has already submitted depositFor(...).  The viewport
        forwards only the deposit id and wallet identity; the Hub must verify the
        amount on-chain before crediting the ledger.
        """

        hub_base = self._clean_hub_url(hub_url)
        wallet_address = str(payload.get("wallet_address", "")).strip().lower()
        deposit_id = str(payload.get("deposit_id", "")).strip().lower()
        if not re.fullmatch(r"0x[0-9a-f]{64}", deposit_id):
            raise ValueError("deposit_id must be a 32-byte 0x-prefixed hex value.")

        route_payload = {
            "deposit_id": deposit_id,
            "wallet_address": wallet_address,
        }
        if payload.get("tx_hash"):
            route_payload["tx_hash"] = str(payload.get("tx_hash", "")).strip()
        if payload.get("contract_address"):
            route_payload["contract_address"] = str(payload.get("contract_address", "")).strip()
        if payload.get("chain_id") is not None:
            route_payload["chain_id"] = int(payload.get("chain_id") or 0)
        encoded = json.dumps(route_payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            hub_base + "/api/hub/v1/credits/wallet-funding/complete",
            data=encoded,
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=30.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code} from /api/hub/v1/credits/wallet-funding/complete: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object wallet funding completion response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        data.setdefault("wallet_address", wallet_address)
        data.setdefault("account_id", wallet_address)
        data.setdefault("funding_model", "hub_credit_bridge_escrow_wallet_v2")
        data["wallet_funding_completion_endpoint"] = "/api/hub/v1/credits/wallet-funding/complete"
        return data

    def _post_worker_wallet_funding_import_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Forward a confirmed wallet-funding receipt to Hub.

        The on-chain transaction has already happened before this helper runs, so a
        missing newest Hub route must not force the user to submit another funding
        transaction.  New Hubs expose the wallet-specific import route; older Hubs
        still expose the normalized deposit/purchase import aliases that record the
        same chain receipt idempotently for the wallet address.
        """

        hub_base = self._clean_hub_url(hub_url)
        import_paths = [
            ("/api/hub/v1/credits/wallet-funding/import", "wallet-funding"),
            ("/api/hub/v1/credits/deposits/import", "legacy-deposit-import"),
            ("/api/hub/v1/credits/purchases/import", "legacy-purchase-import"),
        ]
        route_errors: list[str] = []

        for index, (path, mode) in enumerate(import_paths):
            route_payload = dict(payload)
            wallet_address = str(route_payload.get("wallet_address", "")).strip().lower()
            if wallet_address:
                route_payload["wallet_address"] = wallet_address
                route_payload.setdefault("account_id", wallet_address)
            encoded = json.dumps(route_payload, ensure_ascii=False).encode("utf-8")
            request = Request(
                hub_base + path,
                data=encoded,
                headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
                method="POST",
            )
            try:
                with urlopen(request, timeout=10.0) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                route_errors.append(f"{path} -> HTTP {exc.code}: {body}")
                if exc.code in {HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED} and index + 1 < len(import_paths):
                    continue
                raise RuntimeError(f"Hub returned HTTP {exc.code} from {path}: {body}") from exc
            except URLError as exc:
                raise RuntimeError(f"Hub is unreachable: {exc}") from exc

            if not isinstance(data, dict):
                raise RuntimeError(f"Hub returned a non-object wallet funding import response from {path}.")
            if data.get("error"):
                raise RuntimeError(str(data["error"]))

            if mode != "wallet-funding":
                data = dict(data)
                wallet_address = str(payload.get("wallet_address", ""))
                if wallet_address:
                    data.setdefault("wallet_address", wallet_address)
                    data.setdefault("account_id", wallet_address.lower())
                data.setdefault("funding_model", "hub_credit_bridge_escrow_wallet_v1")
                data["wallet_funding_import_endpoint"] = path
                data["wallet_funding_import_fallback"] = True
                if route_errors:
                    data["wallet_funding_import_route_errors"] = route_errors
            return data

        raise RuntimeError("Hub wallet funding import failed: " + " ; ".join(route_errors))

    def _mark_worker_multisession_key_inactive_on_hub(
        self,
        *,
        key_id: str,
        hub_url: str,
        error_message: str,
    ) -> None:
        key_id = str(key_id or "").strip()
        if not key_id:
            return
        data = self._load_worker_multisession_key_cache()
        record = data.get("keys", {}).get(key_id)
        if not isinstance(record, dict):
            return
        normalized_hub_url = self._clean_hub_url(hub_url, allow_empty=True)
        record_hub_url = self._clean_hub_url(str(record.get("hub_url") or ""), allow_empty=True)
        if normalized_hub_url and record_hub_url and normalized_hub_url != record_hub_url:
            return
        record["status"] = "inactive_on_hub"
        record["inactive_on_hub_at"] = datetime.now(timezone.utc).isoformat()
        record["last_error"] = str(error_message or "The saved multi-session key is not active on this Hub.")
        record["hub_url"] = normalized_hub_url or record_hub_url
        data["keys"][key_id] = record
        self._save_worker_multisession_key_cache(data)

    def _worker_multisession_connect_error_message(self, error_body: str) -> str:
        text = str(error_body or "")
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                text = str(payload.get("error") or payload.get("user_message") or text)
        except Exception:
            pass
        lowered = text.lower()
        if "multi-session" not in lowered and "multisession" not in lowered:
            return ""
        if "not active" in lowered or "not found" in lowered or "unknown" in lowered:
            return (
                "The saved multi-session key is not active on this Hub. "
                "Request a new multi-session key for this network's Hub before connecting."
            )
        if "different wallet" in lowered or "does not belong" in lowered or "wallet" in lowered and "match" in lowered:
            return (
                "The saved multi-session key belongs to a different wallet. "
                "Connect the matching wallet or request a new multi-session key for this network's Hub."
            )
        return (
            "Worker Hub connection requires a valid saved multi-session key for this network's Hub. "
            "Request a multi-session key before connecting. Hub said: "
            + text
        )

    def _post_worker_start_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/connect",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = self._worker_multisession_connect_error_message(body) if exc.code == HTTPStatus.FORBIDDEN else ""
            if message:
                raise RuntimeError(message) from exc
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object worker connect response.")
        if data.get("error"):
            message = self._worker_multisession_connect_error_message(str(data.get("error") or ""))
            raise RuntimeError(message or str(data["error"]))
        return data

    def _post_worker_registration_to_hub(self, *, hub_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._clean_hub_url(hub_url) + "/api/hub/v1/workers/register",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self._hub_json_request_headers({"Content-Type": "application/json"}),
            method="POST",
        )
        try:
            with urlopen(request, timeout=5.0) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hub returned HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            raise RuntimeError(f"Hub is unreachable: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Hub returned a non-object worker registration response.")
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data

    def _energy_passcode_ok(self, body: dict[str, Any]) -> bool:
        required = self.server.config.energy_admin_passcode
        if not required:
            return True
        supplied = str(body.get("passcode") or self.headers.get("X-Main-Computer-Energy-Passcode") or "")
        return supplied == required
