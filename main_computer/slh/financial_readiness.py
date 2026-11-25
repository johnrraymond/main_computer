"""Resident ORG startup and maintenance, using the existing wallet authority.

Provision an initial signer only in the resident startup lane. Never switch
a binding, enable employment, or grant spending here.
The caller serializes this operation with marketplace admission/configuration.
"""
from datetime import datetime, timezone

from .authority import SLHError

RENEW_BEFORE_SECONDS = 3600


def maintain(controller):
    from .marketplace_auto import read_settings, load_authorization
    from .pinky_sweep import load_organization_signer
    from .organization_wallet import HubCreditReader
    from main_computer.hub_networks import load_hub_network_registry

    wallet = controller.wallet_service
    if wallet is None:
        raise SLHError('ORG_WALLET_SERVICE_UNAVAILABLE', 503)
    signer, _ = load_organization_signer(wallet, provision=controller.provision_wallet)
    settings = read_settings(controller.state_dir)
    with wallet._lock, wallet._db() as db:
        binding = wallet._binding(db)
    registry = load_hub_network_registry()
    url = settings['hub_url']
    if not url:
        if binding:
            raise SLHError('WALLET_BINDING_CHANGED', 409)
        # Default from canonical registry, not a hardcoded endpoint. The
        # resident service alone enables this automatic default discovery.
        url = registry.networks[registry.default_network].hub_url
        settings = controller.configure({'hub_url': url}, settings['revision'])
    pin = binding['tls_sha256'] if binding else None
    if binding and (binding['hub_url'] != url or binding['wallet_address'] != signer):
        raise SLHError('WALLET_MIGRATION_REQUIRED', 409)
    reader = HubCreditReader(url, pin)
    identity = reader.request('GET', '/api/hub/v1/hub-identity')
    network = identity.get('network') or {}
    chain = str(network.get('chain_id') or '')
    profile = registry.networks.get(network.get('network_key'))
    lab = (reader.trust_mode == 'LAB_ONLY' and profile is not None
           and profile.network_key == 'dev' and network.get('network_kind') == 'dev')
    if (identity.get('service') != 'main_computer.stable_hub' or not identity.get('hub_id')
            or not identity.get('cluster_id') or profile is None or str(profile.chain_id) != chain
            or (not lab and (reader.trust_mode == 'LAB_ONLY' or
                             profile.hub_url.rstrip('/') != url.rstrip('/')))):
        raise SLHError('HUB_NETWORK_NOT_CONFIGURED', 409)
    hub, cluster = identity['hub_id'], identity['cluster_id']
    if binding and (hub, cluster, chain) != (binding['hub_id'], binding['cluster_id'], binding['chain_id']):
        raise SLHError('HUB_IDENTITY_MISMATCH', 409)

    auth = load_authorization(controller.state_dir)
    renew = auth is None
    if auth:
        result = reader.request('POST', '/api/hub/v1/credits/multisession-keys/validate',
                                {'multisession_authorization': auth})
        if ((result.get('hub_id'), result.get('cluster_id')) != (hub, cluster) or
                (result.get('chain_id') is not None and str(result['chain_id']) != chain)):
            raise SLHError('HUB_IDENTITY_MISMATCH', 409)
        if result.get('multisession_key_id') != auth['multisession_key_id']:
            raise SLHError('HUB_MSK_INVALID', 409)
        if result.get('ok') is not True:
            raise SLHError('HUB_MSK_INVALID', 409)
        if result.get('valid') is not True or result.get('ready') is not True:
            # Expiration is renewable; inactive/revoked/unknown keys are not.
            # Do not undo an administrator's explicit revocation.
            if result.get('reason_code') != 'key_expired':
                raise SLHError('HUB_MSK_INACTIVE_ADMIN_REQUIRED', 409)
            renew = True
        else:
            if str(result.get('chain_id') or '') != chain:
                raise SLHError('HUB_IDENTITY_MISMATCH', 409)
            if result.get('wallet_address') != signer:
                raise SLHError('HUB_WALLET_MISMATCH', 409)
            try:
                expiry = datetime.fromisoformat(result['key']['expires_at'].replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    raise ValueError()
                renew = (expiry - datetime.now(timezone.utc)).total_seconds() <= RENEW_BEFORE_SECONDS
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                raise SLHError('HUB_MSK_EXPIRY_UNVERIFIED', 409) from exc
    if renew:
        # Do not repeatedly mint keys while a successful renewal is being
        # drained. issue_authorization validates the replacement before saving.
        controller.issue_authorization()
    # Rebind after initial issuance or interrupted rotation, but only against
    # the same signer, Hub, chain and cluster already checked above.
    with wallet._lock, wallet._db() as db:
        binding = wallet._binding(db)
    status = wallet.status()
    if binding is None or status.get('reason') == 'WALLET_BINDING_CHANGED':
        status = wallet.bind(expected_revision=binding['revision'] if binding else 0,
                             expected_hub_id=hub, expected_cluster_id=cluster,
                             expected_chain_id=chain, tls_sha256=pin)
    else:
        status = wallet.reconcile()
    if status.get('state') != 'CURRENT' or (status.get('binding') or {}).get('wallet_address') != signer:
        raise SLHError('ORG_WALLET_NOT_CURRENT', 409)
    return {'state': 'READY', 'network': profile.network_key,
            'wallet_address': signer, 'checked_at': wallet.clock()}
