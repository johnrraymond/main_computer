"""Read-only pinned TLS observation of an existing private-network SLH.

No DNS names, loopback, public hosts, redirections, forwarded credentials,
organization setup, or TOFU. A mismatch is rejected before an HTTP request.
This API intentionally does not confer or simulate administrator authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import ssl


class NetworkSlhError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_target(address: str, port: int, tls_sha256: str):
    if not isinstance(address, str) or len(address) > 60:
        raise NetworkSlhError("NETWORK_ADDRESS_INVALID")
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise NetworkSlhError("PRIVATE_IP_LITERAL_REQUIRED") from exc
    # No DNS resolution or subnet guessing. A network SLH must be a distinct
    # RFC1918/ULA address, not the loopback Local Management Service.
    private4 = tuple(ipaddress.ip_network(net) for net in (
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
    private6 = ipaddress.ip_network("fc00::/7")
    if not ((ip.version == 4 and any(ip in net for net in private4)) or
            (ip.version == 6 and ip in private6)):
        raise NetworkSlhError("PRIVATE_NETWORK_ADDRESS_REQUIRED")
    if type(port) is not int or not 1 <= port <= 65535:
        raise NetworkSlhError("NETWORK_PORT_INVALID")
    if not isinstance(tls_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", tls_sha256):
        raise NetworkSlhError("TLS_PIN_REQUIRED")
    return str(ip), port, tls_sha256.lower()


def _read_response(connection, path: str) -> dict:
    connection.request("GET", path, headers={"Accept":"application/json"})
    reply=connection.getresponse()
    content=reply.read(65537)
    if reply.status != 200 or len(content) > 65536 or not reply.getheader("Content-Type", "").startswith("application/json"):
        raise NetworkSlhError("NETWORK_IDENTITY_RESPONSE_INVALID")
    try:
        data=json.loads(content)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NetworkSlhError("NETWORK_IDENTITY_RESPONSE_INVALID") from exc
    if not isinstance(data, dict):
        raise NetworkSlhError("NETWORK_IDENTITY_RESPONSE_INVALID")
    return data


def inspect_network_target(address: str, port: int, tls_sha256: str, *, connection_factory=None):
    host, api_port, pin=validate_target(address,port,tls_sha256)
    if connection_factory is None:
        connection_factory=http.client.HTTPSConnection
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname=False
    context.verify_mode=ssl.CERT_NONE
    # This context is intentionally untrusted until the exact DER fingerprint
    # is checked on the SAME TLS connection before any HTTP request.
    connection=connection_factory(host,api_port,timeout=3,context=context)
    try:
        connection.connect()
        actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual,pin):
            raise NetworkSlhError("TLS_PIN_MISMATCH")
        identity=_read_response(connection,"/v1/org/identity")
        if identity.get("schema") != "main-computer.slh-org-identity.v1" or not isinstance(identity.get("tls_sha256"),str) or not hmac.compare_digest(identity["tls_sha256"].lower(),pin):
            raise NetworkSlhError("NETWORK_SERVICE_IDENTITY_MISMATCH")
        status=_read_response(connection,"/v1/org/status")
        if status.get("schema")!="main-computer.slh-org-status.v1" or type(status.get("operational")) is not bool:
            raise NetworkSlhError("NETWORK_SERVICE_STATUS_INVALID")
        label=(f"[{host}]" if ":" in host else host)+f":{api_port}"
        return {"endpoint":label,"tlsSha256":pin,
                "organizationState":"RUNNING" if status["operational"] else "NOT_RUNNING",
                "authenticationState":"NOT_AUTHENTICATED",
                "observedAt":datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "explanation":"Pinned TLS identity verified; remote administration requires a separate authenticated device session."}
    except NetworkSlhError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as exc:
        raise NetworkSlhError("NETWORK_SLH_UNAVAILABLE") from exc
    finally:
        connection.close()
