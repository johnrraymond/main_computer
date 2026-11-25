"""Startup/restart and renewal against real Stable Hub, no public network calls."""
from datetime import datetime, timedelta, timezone
import json
import time

import pytest

from main_computer.slh import financial_readiness
from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import AutomaticMarketplace, read_settings, update_settings, load_authorization
from main_computer.slh.organization_wallet import OrganizationWallet, HubCreditReader
from main_computer.slh.pinky_sweep import SIGNER_ENV
from tools.slh_seh_lab.org_msk_passthrough import _wallet, _keys_for_wallet
from tools.slh_seh_lab.run_lab import _in_memory_hub
from tests.test_slh_marketplace_automatic import ControlledProxy


@pytest.fixture
def lab(tmp_path, monkeypatch):
    hub, thread = _in_memory_hub()
    try:
        owner = _wallet()
        signer = tmp_path / 'signer.private'
        signer.write_text(owner.private_key, encoding='ascii')
        signer.chmod(0o600)
        monkeypatch.setenv(SIGNER_ENV, str(signer))
        state = tmp_path / 'state'
        url = f'http://127.0.0.1:{hub.server_port}'
        update_settings(state, {'hub_url': url}, 0)
        wallet = OrganizationWallet(state)
        proxy = ControlledProxy()
        controller = AutomaticMarketplace(proxy, state, wallet_service=wallet, financial_startup=True)
        yield controller, hub.multisession_key_store, owner, signer
        controller.stop()
    finally:
        hub.shutdown(); hub.server_close(); thread.join(timeout=3)


def assert_ready(controller, store, owner):
    status = controller.status()
    assert status['financial_readiness']['state'] == 'READY', status['financial_readiness']
    wallet = status['organization_wallet']
    assert wallet['state'] == 'CURRENT'
    assert wallet['binding']['wallet_address'] == owner.address
    key = load_authorization(controller.state_dir)['multisession_key_id']
    row = _keys_for_wallet(store, owner.address)[key]
    assert row['origin'] == 'org-slh'
    assert row['verified']['recovered_address'] == owner.address
    assert key not in json.dumps(status) and owner.private_key not in json.dumps(status)
    return key


def test_startup_without_admin_api_and_restart_reuse(lab):
    c, store, owner, _ = lab
    assert not _keys_for_wallet(store, owner.address)
    c.start()  # Actual background startup, without issue or bind API calls.
    end = time.monotonic() + 15
    while time.monotonic() < end:
        if c.status()['financial_readiness']['state'] != 'INITIALIZING':
            break
        time.sleep(.05)
    key = assert_ready(c, store, owner)
    assert read_settings(c.state_dir)['enabled'] is False
    assert not c.market.calls
    c.stop()
    other = AutomaticMarketplace(ControlledProxy(), c.state_dir,
                wallet_service=OrganizationWallet(c.state_dir), financial_startup=True)
    other.reconcile()
    assert assert_ready(other, store, owner) == key
    assert len(_keys_for_wallet(store, owner.address)) == 1


@pytest.mark.parametrize('minutes', [30, -30])
def test_renewal_before_or_after_expiration(lab, minutes):
    c, store, owner, _ = lab
    c.reconcile()
    key = assert_ready(c, store, owner)
    doc = store.load()
    doc['keys'][key]['key_expires_at'] = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    store.save(doc)
    c.reconcile()
    new = assert_ready(c, store, owner)
    assert new != key
    c.reconcile()
    assert assert_ready(c, store, owner) == new
    assert len(_keys_for_wallet(store, owner.address)) == 2


def test_revocation_is_not_renewed_and_existing_slots_drain(lab):
    c, store, owner, _ = lab
    c.reconcile()
    key = assert_ready(c, store, owner)
    c.configure({'enabled': True}, read_settings(c.state_dir)['revision'])
    c.reconcile()
    assert c.market.sessions
    store.revoke_key_record(key_id=key, wallet_address=owner.address)
    c.reconcile()
    assert c.financial_readiness['reason'] == 'HUB_MSK_INACTIVE_ADMIN_REQUIRED'
    assert c.wallet_service.status()['state'] == 'STALE'
    assert not c.market.sessions
    assert len(_keys_for_wallet(store, owner.address)) == 1
    assert read_settings(c.state_dir)['enabled'] is True  # preserve intent


def test_outage_does_not_issue_again_and_recovers(lab, monkeypatch):
    c, store, owner, _ = lab
    c.reconcile()
    key = assert_ready(c, store, owner)
    with monkeypatch.context() as m:
        def outage(*args, **kwargs):
            raise SLHError('HUB_ACCOUNT_UNAVAILABLE', 503)
        m.setattr(HubCreditReader, 'request', outage)
        c.reconcile()
        assert c.financial_readiness['state'] == 'BLOCKED'
        assert c.wallet_service.status()['state'] == 'STALE'
    c.reconcile()
    assert assert_ready(c, store, owner) == key
    assert len(_keys_for_wallet(store, owner.address)) == 1


def test_missing_or_changed_signer_blocks_without_new_wallet(lab, monkeypatch):
    c, store, owner, signer = lab
    with monkeypatch.context() as m:
        m.delenv(SIGNER_ENV)
        c.reconcile()
        assert c.financial_readiness['reason'] == 'PINKY_SWEEP_CONFIGURATION_REQUIRED'
        assert c.wallet_service.status()['state'] == 'UNBOUND'
        assert not _keys_for_wallet(store, owner.address)
    c.reconcile()
    key = assert_ready(c, store, owner)
    signer.write_text(_wallet().private_key, encoding='ascii')
    c.reconcile()
    assert c.financial_readiness['reason'] == 'WALLET_MIGRATION_REQUIRED'
    assert load_authorization(c.state_dir)['multisession_key_id'] == key
    assert len(store.load()['keys']) == 1


def test_registry_default_discovery_never_enables_market(tmp_path, monkeypatch):
    from main_computer.hub_networks import load_hub_network_registry
    signer = tmp_path / 'signer'
    signer.write_text(_wallet().private_key, encoding='ascii'); signer.chmod(0o600)
    monkeypatch.setenv(SIGNER_ENV, str(signer))
    c = AutomaticMarketplace(ControlledProxy(), tmp_path/'state',
           wallet_service=OrganizationWallet(tmp_path/'state'), financial_startup=True)
    observed = []
    def offline(self, *args, **kwargs):
        observed.append(self.host)
        raise SLHError('HUB_ACCOUNT_UNAVAILABLE', 503)
    monkeypatch.setattr(HubCreditReader, 'request', offline)
    c.reconcile()
    registry = load_hub_network_registry()
    settings = read_settings(c.state_dir)
    assert settings['hub_url'] == registry.networks[registry.default_network].hub_url
    assert settings['enabled'] is False and not c.market.calls
    assert observed and load_authorization(c.state_dir) is None


def test_wrong_hub_identity_does_not_sign_or_advertise(lab, monkeypatch):
    c, store, owner, _ = lab
    original = HubCreditReader.request
    def wrong_identity(self, method, path, *args, **kwargs):
        result = original(self, method, path, *args, **kwargs)
        if path.endswith('/hub-identity'):
            result['network']['chain_id'] = 999
        return result
    monkeypatch.setattr(HubCreditReader, 'request', wrong_identity)
    c.configure({'enabled': True}, read_settings(c.state_dir)['revision'])
    c.reconcile()
    assert c.financial_readiness['state'] == 'BLOCKED'
    assert c.financial_readiness['reason'] == 'HUB_NETWORK_NOT_CONFIGURED'
    assert not c.market.calls and not _keys_for_wallet(store, owner.address)


def test_resident_creates_wallet_and_msk_without_environment(lab, monkeypatch):
    from types import SimpleNamespace
    from main_computer.slh.organization_signer import resolve_organization_signer
    c, store, _, _ = lab
    monkeypatch.delenv(SIGNER_ENV)
    monkeypatch.delenv('MAIN_COMPUTER_RESERVE_WALLET_ADDRESS', raising=False)
    c.provision_wallet = True
    c.start()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if c.status()['financial_readiness']['state'] != 'INITIALIZING':
            break
        time.sleep(.05)
    address, key = resolve_organization_signer(c.wallet_service)
    owner = SimpleNamespace(address=address, private_key=key)
    msk = assert_ready(c, store, owner)
    assert not read_settings(c.state_dir)['enabled']
    assert not c.market.calls
    c.stop()
    other = AutomaticMarketplace(ControlledProxy(), c.state_dir,
                wallet_service=OrganizationWallet(c.state_dir), financial_startup=True,
                provision_wallet=True)
    other.reconcile()
    assert assert_ready(other, store, owner) == msk
    assert len(_keys_for_wallet(store, owner.address)) == 1
    assert resolve_organization_signer(other.wallet_service) == (address, key)


def test_existing_bound_legacy_wallet_is_imported_without_rotation(lab, monkeypatch):
    from main_computer.slh.organization_signer import resolve_organization_signer
    c, store, owner, legacy = lab
    c.reconcile()
    msk = assert_ready(c, store, owner)
    c.provision_wallet = True
    c.reconcile()
    assert assert_ready(c, store, owner) == msk
    monkeypatch.delenv(SIGNER_ENV)
    c.reconcile()
    assert assert_ready(c, store, owner) == msk
    assert resolve_organization_signer(c.wallet_service) == (owner.address, owner.private_key)
    assert len(_keys_for_wallet(store, owner.address)) == 1


def test_existing_bound_wallet_missing_legacy_key_never_rotates(lab, monkeypatch):
    c, store, owner, _ = lab
    c.reconcile()
    msk = assert_ready(c, store, owner)
    monkeypatch.delenv(SIGNER_ENV)
    c.provision_wallet = True
    c.reconcile()
    assert c.financial_readiness['reason'] == 'ORG_SIGNER_RECOVERY_REQUIRED'
    assert load_authorization(c.state_dir)['multisession_key_id'] == msk
    assert len(_keys_for_wallet(store, owner.address)) == 1
