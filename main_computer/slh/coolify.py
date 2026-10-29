"""SEH-only Coolify control plane; no marketplace Hub mutations.

A backend API usable later by the SLH local application.  By design, this
phase never grants relay identity, device trust or end-to-end channel keys.
Deployment success means only that Coolify accepted a service/start request.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import urllib.request
import urllib.error
from typing import Any, Protocol

from .config import SEHTarget


class SEHDeploymentError(RuntimeError):
    pass


class CoolifyTransport(Protocol):
    def request(self, method: str, path: str, body: dict | None = None) -> Any: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise SEHDeploymentError("Coolify API redirect refused; token will not be forwarded")


class CoolifyHTTP:
    """Token in memory only. Never follow redirects or include it in errors."""
    def __init__(self, origin: str, token: str, timeout: float = 12.0):
        if not token or not token.strip():
            raise SEHDeploymentError("SLH_COOLIFY_TOKEN is required for live Coolify calls")
        self.origin = origin.rstrip("/")
        self.token = token.strip()
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        if not path.startswith("/api/v1/") or ".." in path or "?" in path:
            raise SEHDeploymentError("Invalid Coolify API path")
        data = json.dumps(body, sort_keys=True).encode() if body is not None else None
        req = urllib.request.Request(
            self.origin + path, data=data, method=method.upper(),
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json",
                     "Content-Type": "application/json"},
        )
        try:
            with self.opener.open(req, timeout=self.timeout) as res:
                result = res.read(2_000_000)
                return json.loads(result) if result else {}
        except urllib.error.HTTPError as e:
            # Remote error bodies can contain user-supplied sensitive values.
            raise SEHDeploymentError(f"Coolify returned HTTP {e.code} for {method} {path}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SEHDeploymentError(f"Coolify request failed for {method} {path}: {type(e).__name__}") from None


def compose_yaml(target: SEHTarget) -> str:
    """Pinned actual SEH relay image, hardened container; public key only."""
    # All substituted strings validated by SEHTarget.from_dict before invocation.
    return (
        "services:\n"
        "  seh:\n"
        f"    image: {target.image}@{target.image_digest}\n"
        "    restart: unless-stopped\n"
        "    read_only: true\n"
        "    security_opt:\n"
        "      - no-new-privileges:true\n"
        "    cap_drop:\n"
        "      - ALL\n"
        "    environment:\n"
        f"      SEH_PUBLIC_ORIGIN: {target.public_url}\n"
        "      SEH_AUDIT_MODE: metadata_only\n"
        f"      SEH_ORGANIZATION_ID: {target.organization_id}\n"
        f"      SEH_SLH_ID: {target.slh_id}\n"
        f"      SEH_SLH_PUBLIC_KEY_B64: '{target.slh_public_key_b64}'\n"
        f"      SEH_PORT: '{target.internal_port}'\n"
        "    expose:\n"
        f"      - \"{target.internal_port}\"\n"
        "    tmpfs:\n"
        "      - /tmp:rw,noexec,nosuid,size=16m\n"
        "    pids_limit: 128\n"
        "    mem_limit: 256m\n"
        "    cpus: 1.0\n"
    )


def desired_state(target: SEHTarget) -> dict[str, Any]:
    return {"schema": "main-computer.seh-deployment-plan.v1", "target": target.to_dict(),
            "resource_name": f"mc-seh-{target.target_id}",
            "resource_marker": f"main-computer-seh:v1 target={target.target_id} org={target.organization_id} slh={target.slh_id}",
            "compose": compose_yaml(target),
            "contract_status": "real-relay-image-local-tested-deployment-not-verified",
            "domain_status": "coolify-proxy-not-verified"}


def plan(target: SEHTarget) -> dict:
    state = desired_state(target)
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema": "main-computer.seh-plan.v1", "target_id": target.target_id,
            "coolify_origin": target.coolify_url, "public_url": target.public_url,
            "operation": "adopt_existing" if target.service_uuid else "create_new",
            "service_uuid": target.service_uuid, "resource_name": state["resource_name"],
            "image": f"{target.image}@{target.image_digest}",
            "internal_port": target.internal_port, "plan_digest": digest,
            "status": "PLANNED_ONLY", "constraints": ["Real SEH runtime local tested; public Coolify deployment requires live verification", "No deployment without exact digest approval", "No E2E identity material in Coolify"]}


def _require_list(data: Any) -> list:
    if not isinstance(data, list):
        raise SEHDeploymentError("Coolify returned an invalid service list")
    return data


@dataclass
class SEHDeployer:
    target: SEHTarget
    client: CoolifyTransport

    def preflight(self) -> dict:
        """Read-only inventory; never infers authorization from resource name alone."""
        project = self.client.request("GET", f"/api/v1/projects/{self.target.project_uuid}")
        if not isinstance(project, dict) or project.get("uuid") != self.target.project_uuid:
            raise SEHDeploymentError("Coolify project UUID not verified")
        server = self.client.request("GET", f"/api/v1/servers/{self.target.server_uuid}")
        if not isinstance(server, dict) or server.get("uuid") != self.target.server_uuid:
            raise SEHDeploymentError("Coolify server UUID not verified")
        if server.get("is_usable") is False or server.get("is_reachable") is False:
            raise SEHDeploymentError("Coolify server reports unusable or unreachable")
        environment = self.client.request(
            "GET", f"/api/v1/projects/{self.target.project_uuid}/{self.target.environment_uuid}")
        # Environment response may not include UUID in Coolify versions that
        # show numeric ID and name only. Successful scoped lookup is decisive.
        if not isinstance(environment, dict) or not environment.get("name"):
            raise SEHDeploymentError("Coolify project environment not verified")
        items = _require_list(self.client.request("GET", "/api/v1/services"))
        desired = desired_state(self.target)
        names = [i for i in items if isinstance(i, dict) and i.get("name") == desired["resource_name"]]
        if self.target.service_uuid:
            data = self.client.request("GET", f"/api/v1/services/{self.target.service_uuid}")
            if not isinstance(data, dict) or data.get("uuid") != self.target.service_uuid:
                raise SEHDeploymentError("Selected service UUID could not be verified")
            if data.get("name") != desired["resource_name"] or data.get("description") != desired["resource_marker"]:
                raise SEHDeploymentError("Refusing to adopt service without exact SEH ownership marker")
            # Even explicitly selected services are not silently overwritten by phase 1.
            status = "EXISTING_SEH_READ_ONLY"
        else:
            if names:
                raise SEHDeploymentError("A service with this target name already exists; recover its UUID before retry")
            status = "CREATE_ELIGIBLE"
        return {"schema": "main-computer.seh-preflight.v1", "status": status,
                "target_id": self.target.target_id, "plan_digest": plan(self.target)["plan_digest"],
                "checks": {"target_type": "seh", "coolify_services_read": True, "project_verified": True,
                           "server_verified": True, "environment_verified": True,
                           "service_identity_collision": False,
                           "e2e_auth": "NOT_TESTED", "public_wss": "NOT_TESTED",
                           "marketplace": "NOT_TESTED", "dns_tls": "NOT_TESTED"}}

    def deploy(self, *, approved_digest: str, approved_target: str) -> dict:
        """Explicit mutation. Existing resource update not implemented in this slice."""
        p = plan(self.target)
        if approved_digest != p["plan_digest"] or approved_target != self.target.target_id:
            raise SEHDeploymentError("Exact target and plan digest approval required")
        if not self.target.slh_public_key_b64:
            raise SEHDeploymentError('SEH deployment requires SLH relay public key; never deploy an unbound relay')
        status = self.preflight()
        if status["status"] != "CREATE_ELIGIBLE":
            raise SEHDeploymentError("Creation refused; existing SEH management requires a subsequent reviewed patch")
        desired = desired_state(self.target)
        payload = {
            "name": desired["resource_name"], "description": desired["resource_marker"],
            "project_uuid": self.target.project_uuid, "environment_uuid": self.target.environment_uuid,
            "server_uuid": self.target.server_uuid,
            "instant_deploy": False, "redact_all_env_values_in_logs": True,
            "force_domain_override": False,
            "docker_compose_raw": base64.b64encode(desired["compose"].encode()).decode(),
        }
        # No automatic retries of this POST. An ambiguous response must be reconciled
        # against /services before another attempt, to avoid duplicate resources.
        result = self.client.request("POST", "/api/v1/services", payload)
        uuid = result.get("uuid") if isinstance(result, dict) else None
        if not isinstance(uuid, str) or not uuid:
            raise SEHDeploymentError("Coolify creation result ambiguous: inspect service list before retry")
        return {"schema": "main-computer.seh-deploy-receipt.v1", "target_id": self.target.target_id,
                "status": "CREATED_NOT_STARTED", "service_uuid": uuid, "plan_digest": p["plan_digest"],
                "next": "Record service_uuid in target; set domain mapping and verify pinned image, then run guarded start", "ready": False}

    def start_existing(self, *, approved_digest: str, approved_uuid: str) -> dict:
        """Queue a known, pre-existing SEH. Does not declare relay ready."""
        p = plan(self.target)
        if not self.target.service_uuid or approved_uuid != self.target.service_uuid or approved_digest != p["plan_digest"]:
            raise SEHDeploymentError("Existing SEH UUID and exact plan digest approval required")
        if self.preflight()["status"] != "EXISTING_SEH_READ_ONLY":
            raise SEHDeploymentError("Service not verified")
        self.client.request("POST", f"/api/v1/services/{approved_uuid}/start")
        return {"schema": "main-computer.seh-start-receipt.v1", "service_uuid": approved_uuid,
                "status": "START_QUEUED", "ready": False,
                "missing_gates": ["public_https", "wss", "outbound_slh_tunnel", "e2e_auth", "isolation"]}
