"""Read-only, verified identity + SEH state -> Coolify -> SLH resolution.

This is invoked by the resident gateway, not a separate connector process.
Neither Mother, Coolify nor the SLH directory is modified.  Coolify's legacy
route component is read only as a temporary wire-protocol compatibility value;
no organization or SEH alias is created or persisted.
"""
from __future__ import annotations

from pathlib import Path


from .seh_autoconnect import SEHConnectionSettings
from .storage import machine_root
from tools.seh_control import git_deployment as git
from tools.seh_control.dns_probe import domain_hosts


class SEHConnectionResolutionError(ValueError):
    """Refuse a target whose existing identity and trust cannot be verified."""


class SEHApplicationDeleted(SEHConnectionResolutionError):
    """Coolify explicitly returned 404 for the SEH-owned application UUID."""


def resolve_canonical_connection(state_dir: Path, *, mother_root: Path | None = None,
                                 network: str = 'mainnet', client=None,
                                 require_enabled: bool = True) -> SEHConnectionSettings:
    """Derive ephemeral connection settings from the sole registered Coolify UUID.

    A remote trust mismatch is a refusal; never generate, export, rotate or
    overwrite the local SLH's signing key to match the remote application.
    Only read-only diagnostics may set require_enabled=False; the resident
    connection supervisor always requires explicit deployment enablement.
    """
    root = Path(mother_root) if mother_root is not None else machine_root()
    record, controller, generation, binding = git._canonical_selection(root, network)
    if require_enabled:
        from .organization_state import read_config
        if read_config(state_dir).get('seh_enabled') is not True:
            raise SEHConnectionResolutionError('SLH outbound SEH connection is not enabled')
    application_uuid = record['deployment']['application_uuid']
    api = client if client is not None else git._client(controller)
    app = git._registered_application(api, application_uuid)
    if app is None:
        raise SEHApplicationDeleted('Canonical Coolify application was deleted')
    if not isinstance(app, dict) or app.get('uuid') != application_uuid:
        raise SEHConnectionResolutionError('Canonical Coolify application UUID cannot be verified')
    place = git._canonical_placement(api, record)
    git._verify_application_placement(api, app, place)
    git._verify_canonical_source(app)
    if git._verify_canonical_metadata(app) != 'CANONICAL':
        raise SEHConnectionResolutionError('SEH application metadata has not been reconciled')
    hosts = domain_hosts(app)
    if len(hosts) != 1:
        raise SEHConnectionResolutionError('Canonical SEH must have exactly one verified public hostname')
    url = 'wss://' + hosts[0]

    # Retrieve public material only. The private key remains at its original
    # protected SLH path, and the already-registered public key must match.
    from tools.slh_seh_connector import service_identity
    _, public_key_b64 = service_identity(Path(state_dir), create=False)
    rows = git._trust_snapshot(api, application_uuid)
    if any(row['key'] == 'SEH_ORGANIZATION_ID' for row in rows):
        raise SEHConnectionResolutionError('Obsolete organization identity remains in remote SEH trust')
    key = git._diagnostic_env_value(rows, 'SEH_SLH_PUBLIC_KEY_B64')
    if key['state'] != 'VISIBLE':
        raise SEHConnectionResolutionError('Remote SEH signing-key trust cannot be verified')
    if key['value'] != public_key_b64:
        raise SEHConnectionResolutionError('Remote SEH public-key trust differs from preserved SLH identity')
    from tools.seh_control.deployment_state import identity_binding
    later_record, _, _, _ = git._canonical_selection(root, network)
    if (identity_binding(record) != identity_binding(later_record) or
            later_record['deployment'].get('application_uuid') != application_uuid):
        raise SEHConnectionResolutionError('SEH identity or application changed during connection resolution')
    return SEHConnectionSettings(enabled=True, seh_url=url,
                                 allow_loopback_lab=False, retry_seconds=2.0)
