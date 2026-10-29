"""UI-independent backend facade for the future locally administered SLH app."""
from __future__ import annotations
from pathlib import Path
import urllib.request
import urllib.error
from .config import SEHTarget, load_connection_profiles
from .coolify import SEHDeployer, CoolifyTransport, plan


class SLHControlBackend:
    def __init__(self, *, network_registry_path: Path | None = None):
        self.network_registry_path = network_registry_path

    def connection_catalog(self, *, slh_local_url: str | None = None, seh_url: str | None = None) -> dict:
        profiles = load_connection_profiles(network_registry_path=self.network_registry_path,
                                            slh_local_url=slh_local_url, seh_public_url=seh_url)
        return {"schema": "main-computer.slh-connection-catalog.v1", "profiles": [p.to_dict() for p in profiles],
                "wallet_policy": {"direct_marketplace": "client_wallet_required",
                                  "managed_via_slh": "slh_wallet_delegated_not_implemented"},
                "separate_planes": ["private_lan", "optional_seh_transport", "marketplace_upstream"]}

    def seh_plan(self, target: SEHTarget) -> dict:
        return plan(target)

    def seh_preflight(self, target: SEHTarget, client: CoolifyTransport) -> dict:
        return SEHDeployer(target, client).preflight()

    def seh_create(self, target: SEHTarget, client: CoolifyTransport, *, digest: str, target_id: str) -> dict:
        return SEHDeployer(target, client).deploy(approved_digest=digest, approved_target=target_id)

    def seh_start(self, target: SEHTarget, client: CoolifyTransport, *, digest: str, service_uuid: str) -> dict:
        return SEHDeployer(target, client).start_existing(approved_digest=digest, approved_uuid=service_uuid)

    def managed_work_api(self) -> dict:
        """Machine-readable contract, not an authorization or listener implementation."""
        return {"schema": "main-computer.slh-managed-api.v1", "implementation": "NOT_IMPLEMENTED",
                "message_families": ["slh.device.enrollment.request", "slh.device.enrollment.approve",
                                     "slh.device.known.authenticate", "slh.device.revoke",
                                     "slh.auth.challenge", "slh.auth.proof", "slh.worker.hello",
                                     "slh.worker.heartbeat", "slh.work.offer", "slh.work.accepted",
                                     "slh.work.authorized", "slh.work.result", "slh.work.failed",
                                     "slh.route.request", "slh.route.grant", "slh.accounting.reconcile",
                                     "seh.tunnel.connect", "seh.relay.frame"],
                "requirements": ["administrator_first_enrollment", "device_key_proof",
                                 "individual_authenticator_policy", "no_wallet_on_worker",
                                 "public_offer_before_work", "capacity_fencing", "encrypted_e2e_seh"]}

    def probe_marketplace_hub(self, network: str, *, timeout_s: float = 4.0) -> dict:
        """Safe read-only HTTP health probe, NOT an authenticated worker session."""
        from .config import MARKET_NETWORKS
        if network not in MARKET_NETWORKS:
            raise ValueError("Unknown marketplace network")
        profile = next(p for p in load_connection_profiles(network_registry_path=self.network_registry_path)
                       if p.key == network)
        result = {"schema": "main-computer.slh-marketplace-probe.v1", "network": network,
                  "hub_url": profile.url, "status": "NOT_TESTED", "authorized": False,
                  "wallet_connected": False}
        if not profile.url:
            result["status"] = "NOT_CONFIGURED"
            return result
        try:
            request = urllib.request.Request(profile.url.rstrip('/') + '/api/hub/v1/health',
                                             method='GET', headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                result['http_status'] = response.status
                result['status'] = 'HTTP_REACHABLE' if 200 <= response.status < 300 else 'HTTP_UNEXPECTED'
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            result['status'] = 'UNREACHABLE'
            result['error_type'] = type(exc).__name__
        return result
