"""Pinned, admin-authenticated Worker Bank bridge for local MCEL SLH.

This Worker bridge neither signs credentials nor starts resident services, and
never proxies arbitrary requests. The separate first-run adapter is responsible
for explicit local startup and Known Device signing. In-app login supplies a
short-lived Organization bearer, held in the browser closure only. Every Worker
call remains checked by the Organization Service's existing admin authority.
"""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import re
import ssl

from main_computer.slh.organization_state import read_config
from main_computer.slh.storage import slh_state_dir

MAX_BODY = 262144
_DEVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")


class WorkerBridgeError(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code = code
        self.status = status


def validate_admin_bearer(value: object) -> str:
    if (not isinstance(value, str) or not 24 <= len(value) <= 256 or
            not value.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise WorkerBridgeError("ADMIN_SESSION_REQUIRED", 401)
    return value


class OrganizationWorkerClient:
    """A fixed set of requests to the separately authenticated Org process."""

    def __init__(self, *, state_dir: Path | None = None):
        self.state_dir = Path(state_dir) if state_dir is not None else slh_state_dir()

    def request(self, method: str, path: str, token: str, body: dict | None = None) -> dict:
        authentication = (method, path) in {
            ("POST", "/v1/auth/challenge"), ("POST", "/v1/auth/verify")}
        if not authentication:
            token = validate_admin_bearer(token)
        if (method, path) not in {
            ("GET", "/v1/admin/devices"),
            ("GET", "/v1/admin/devices/trash"),
            ("GET", "/v1/admin/enrollments"),
            ("GET", "/v1/admin/policies"),
            ("PATCH", "/v1/admin/policies"),
            ("POST", "/v1/enrollment/approvals"),
            ("POST", "/v1/admin/devices/trash/empty"),
            ("GET", "/v1/org/admin/services"),
            ("GET", "/v1/org/admin/marketplace/status"),
            ("GET", "/v1/org/admin/seh/deployment"),
            ("POST", "/v1/org/admin/seh/deployment/controllers"),
            ("POST", "/v1/org/admin/seh/deployment/selection"),
            ("POST", "/v1/org/admin/seh/deployment/harness"),
            ("GET", "/v1/auth/session"),
            ("POST", "/v1/auth/logout"),
            ("POST", "/v1/auth/challenge"),
            ("POST", "/v1/auth/verify"),
        } and not (method == "POST" and re.fullmatch(
                   r"/v1/admin/enrollments/[0-9a-f]{32}/deny", path)) and not (
                   method == "POST" and re.fullmatch(
                   r"/v1/admin/devices/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}/(?:revoke|reactivate|remove)",path)) and not (method == "PATCH" and path.startswith("/v1/admin/devices/") and
                   _DEVICE_ID.fullmatch(path.removeprefix("/v1/admin/devices/"))):
            raise WorkerBridgeError("OPERATION_NOT_ADMITTED", 403)
        if authentication:
            if not isinstance(body, dict) or (path.endswith('/challenge') and set(body) != {'device_id'}) or (
                path.endswith('/verify') and set(body) != {'device_id','challenge_id','signature'}):
                raise WorkerBridgeError("INVALID_AUTH_REQUEST", 400)
            if any(not isinstance(v, str) or len(v) > 512 for v in body.values()):
                raise WorkerBridgeError("INVALID_AUTH_REQUEST", 400)
        elif body is not None:
            allowed_body = (
                ((method,path)==('POST','/v1/org/admin/seh/deployment/controllers') and
                 isinstance(body,dict) and set(body)=={'name','url','api_token','expected_generation','operation_id'}) or
                ((method,path)==('POST','/v1/org/admin/seh/deployment/selection') and
                 isinstance(body,dict) and set(body)=={'name','expected_generation','operation_id'}) or
                ((method,path)==('POST','/v1/org/admin/seh/deployment/harness') and
                 isinstance(body,dict) and set(body)=={'action','execute','expected_generation','confirm'}) or
                ((method,path)==('POST','/v1/auth/logout') and body=={}) or
                ((method,path)==('POST','/v1/enrollment/approvals') and
                 set(body)=={'request_id'} and isinstance(body['request_id'],str) and
                 re.fullmatch(r'[0-9a-f]{32}',body['request_id'])) or
                ((method,path)==('POST','/v1/admin/devices/trash/empty') and body=={'confirm':'EMPTY_TRASH'}) or
                ((method,path)==('PATCH','/v1/admin/policies') and
                 set(body)=={'remove_trash_after_30_days'} and type(body['remove_trash_after_30_days']) is bool) or
                (method=='POST' and re.fullmatch(r'/v1/admin/devices/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}/remove',path)
                 and body=={'confirm':'REMOVE_DEVICE'}) or
                (method=='POST' and path.endswith(('/revoke','/reactivate','/deny')) and body=={}) or
                (method=='PATCH' and path.startswith('/v1/admin/devices/') and
                 set(body)=={'name'} and isinstance(body['name'],str))
            )
            if not allowed_body:raise WorkerBridgeError('OPERATION_NOT_ADMITTED',403)
        try:
            cfg = read_config(self.state_dir)
            if not cfg["enabled"] or not cfg["configured"]:
                raise WorkerBridgeError("ORGANIZATION_NOT_RUNNING", 503)
            cert_path = self.state_dir / "managed-tls-cert.pem"
            if cert_path.is_symlink() or not cert_path.is_file():
                raise WorkerBridgeError("ORG_TLS_PIN_UNAVAILABLE", 503)
            # Check a locally installed certificate pin, never disable verification
            # *without* independently verifying the peer's exact certificate.
            expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(
                cert_path.read_text(encoding="ascii"))).digest()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            hostname = "127.0.0.1" if cfg["host"] == "0.0.0.0" else cfg["host"]
            conn = http.client.HTTPSConnection(hostname, cfg["api_port"], timeout=3, context=ctx)
            try:
                conn.connect()
                if hashlib.sha256(conn.sock.getpeercert(binary_form=True)).digest() != expected:
                    raise WorkerBridgeError("ORG_TLS_PIN_MISMATCH", 503)
                headers = {"Accept": "application/json", "Connection": "close"}
                if not authentication:
                    headers["Authorization"] = "Bearer " + token
                raw = None
                if body is not None:
                    raw = json.dumps(body, separators=(",", ":")).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                conn.request(method, path, body=raw, headers=headers)
                reply = conn.getresponse()
                data = reply.read(MAX_BODY + 1)
                if len(data) > MAX_BODY:
                    raise WorkerBridgeError("ORG_RESPONSE_TOO_LARGE", 502)
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
                if reply.status not in (200,201):
                    error = result.get("error") if isinstance(result.get("error"), dict) else {}
                    code = error.get("code", "ORG_REQUEST_REFUSED")
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{3,64}", code):
                        code = "ORG_REQUEST_REFUSED"
                    raise WorkerBridgeError(code, reply.status if reply.status in (400, 401, 403, 404, 409) else 503)
                return result
            finally:
                conn.close()
        except WorkerBridgeError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException, ValueError,
                TypeError, UnicodeError) as exc:
            raise WorkerBridgeError("ORG_TRANSPORT_UNAVAILABLE", 503) from exc

    def login_with_signer(self, device_id: str, signer) -> dict:
        """Organization login signed by a trusted identity provider (LMS-SLH).

        The caller supplies a signer for the exact challenge; key material is
        never transferred into the viewport adapter or browser.
        """
        if not isinstance(device_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", device_id):
            raise WorkerBridgeError("INVALID_ADMIN_DEVICE_ID", 400)
        challenge = self.request('POST', '/v1/auth/challenge', '', {'device_id': device_id})
        if challenge.get('device_id') != device_id or not isinstance(challenge.get('challenge_id'), str):
            raise WorkerBridgeError('INVALID_AUTH_CHALLENGE', 503)
        proof = signer(challenge)
        if not isinstance(proof, str) or not 1 <= len(proof) <= 512:
            raise WorkerBridgeError('INVALID_AUTH_SIGNATURE', 503)
        session = self.request('POST', '/v1/auth/verify', '',
                               {'device_id': device_id, 'challenge_id': challenge['challenge_id'],
                                'signature': proof})
        token = validate_admin_bearer(session.get('access_token'))
        self.request('GET', '/v1/admin/devices', token)
        if session.get('role') != 'admin':
            raise WorkerBridgeError('ADMIN_REQUIRED', 403)
        return {'access_token': token, 'expires_at': session.get('expires_at'),
                'device_id': device_id, 'role': 'admin'}

    def login_from_local_identity(self, device_id: str, identity_dir: Path | None = None) -> dict:
        """CLI compatibility: load an explicitly local key, then verify Org role."""
        from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
        from main_computer.slh.authenticator import sign_object
        if not isinstance(device_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", device_id):
            raise WorkerBridgeError("INVALID_ADMIN_DEVICE_ID", 400)
        try:
            key = LocalIdentityStore(identity_dir).load_private_key(device_id)
        except (IdentityStoreError, OSError, ValueError, TypeError) as exc:
            raise WorkerBridgeError("LOCAL_ADMIN_IDENTITY_UNAVAILABLE", 403) from exc
        return self.login_with_signer(device_id, lambda challenge: sign_object(key, challenge))

    def logout(self, token: str) -> dict:
        reply = self.request('POST', '/v1/auth/logout', token, {})
        if reply.get('status') != 'LOGGED_OUT':
            raise WorkerBridgeError('ORG_LOGOUT_UNVERIFIED', 503)
        return {'outcome': 'SIGNED_OUT', 'message': 'Organization session revoked', 'deviceId': ''}

    def inventory(self, token: str) -> dict:
        devices = self.request("GET", "/v1/admin/devices", token)
        services = self.request("GET", "/v1/org/admin/services", token)
        if not isinstance(devices.get("devices"), list) or type(services.get("worker_serving_enabled")) is not bool:
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        connected = []
        if services["worker_serving_enabled"]:
            market = self.request("GET", "/v1/org/admin/marketplace/status", token)
            if market.get("schema") != "main-computer.slh-org-marketplace.v1" or not isinstance(market.get("workers"), list):
                raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
            connected = market["workers"]
        return {"devices": devices["devices"], "workers": connected,
                "worker_serving_enabled": services["worker_serving_enabled"]}

    def rename(self, token: str, device_id: str, label: str) -> dict:
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise WorkerBridgeError("INVALID_WORKER_ID", 400)
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 160 or any(ord(c) < 32 for c in label):
            raise WorkerBridgeError("INVALID_WORKER_LABEL", 400)
        # The Organization Service itself enforces device/admin authorization
        # and records the durable audit event for this mutation.
        inventory = self.request("GET", "/v1/admin/devices", token)
        candidates = [d for d in inventory.get("devices", ()) if isinstance(d, dict) and
                      d.get("id") == device_id and d.get("kind") == "worker" and d.get("status") == "active"]
        if len(candidates) != 1:
            raise WorkerBridgeError("WORKER_NOT_ACTIVE", 409)
        self.request("PATCH", "/v1/admin/devices/" + device_id, token, {"name": label.strip()})
        # Read-after-write; only report success after observing the real result.
        verified = self.request("GET", "/v1/admin/devices", token)
        rows = verified.get("devices", ())
        if not any(isinstance(d, dict) and d.get("id") == device_id and
                   d.get("kind") == "worker" and d.get("name") == label.strip() for d in rows):
            raise WorkerBridgeError("WORKER_UPDATE_UNVERIFIED", 409)
        return {"outcome": "UPDATED", "message": "Worker label verified in organization directory",
                "deviceId": device_id}


def project_inventory(raw: dict, observed_at: str) -> tuple[list[dict], dict]:
    """Enrolled Worker identities and live managed connections are separate facts."""
    devices, sessions = raw.get("devices"), raw.get("workers")
    if not isinstance(devices, list) or not isinstance(sessions, list) or len(devices) > 4096 or len(sessions) > 4096:
        raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
    live = {}
    for session in sessions:
        if not isinstance(session, dict) or not isinstance(session.get("device_id"), str):
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        if session["device_id"] in live:
            raise WorkerBridgeError("ORG_DUPLICATE_WORKER_SESSION", 502)
        live[session["device_id"]] = session
    rows = []
    for item in devices:
        if not isinstance(item, dict):
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        if item.get("kind") != "worker":
            continue
        did = item.get("id")
        if not isinstance(did, str) or not _DEVICE_ID.fullmatch(did):
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        session = live.get(did) if item.get("status") == "active" else None
        connected = session is not None
        busy = bool(session.get("reserved", 0)) if connected else False
        revoked = item.get("status") != "active"
        models = session.get("models", []) if connected else []
        if not isinstance(models, list) or len(models) > 64 or any(not isinstance(m, str) for m in models):
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        label = item.get("name")
        if not isinstance(label, str):
            raise WorkerBridgeError("ORG_RESPONSE_INVALID", 502)
        rows.append({"id": did, "label": label[:160],
                     "state": "REVOKED" if revoked else "DISCONNECTED" if not connected else "BUSY" if busy else "AVAILABLE",
                     "models": ", ".join(models)[:512] if connected else "NOT_OBSERVED",
                     "assignment": ("private" if session.get("private_capacity_claimed") else
                                    "public withdrawal pending" if session.get("withdrawal_requested") else
                                    "public" if session.get("public_advertised") else
                                    "reserved" if busy else "idle") if connected else "NOT_OBSERVED",
                     "marketplace": ("advertised" if session.get("public_advertised") else "not advertised") if connected else "NOT_OBSERVED",
                     "source": "SLH Organization Service / authenticated directory + live scheduler"})
    if len(rows) > 128:
        raise WorkerBridgeError("WORKER_DISPLAY_LIMIT", 409)
    count = sum(r["state"] in {"BUSY", "AVAILABLE"} for r in rows)
    return rows, {"observationState": "AUTHENTICATED", "explanation":
                  "Authenticated organization directory and live scheduler; disconnected and revoked Workers remain visible.",
                  "observedAt": observed_at,
                  "source": "SLH Organization Service /v1/admin/devices + managed scheduler",
                  "enrolledCount": str(len(rows)), "connectedCount": str(count)}
