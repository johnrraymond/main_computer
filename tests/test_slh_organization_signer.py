"""Resident wallet create-once behavior, migration and loss refusal."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import pytest

from main_computer.slh import organization_signer as signer
from main_computer.slh.authority import SLHError
from main_computer.slh.marketplace_auto import save_authorization
from main_computer.slh.organization_wallet import OrganizationWallet
from main_computer.slh.pinky_sweep import load_organization_signer
from main_computer.multisession_key_signing import private_key_to_address


@pytest.fixture
def wallet(tmp_path, monkeypatch):
    monkeypatch.delenv(signer.SIGNER_ENV, raising=False)
    return OrganizationWallet(tmp_path / 'state')


def path(wallet):
    return wallet.path.parent / signer.DIRECTORY / signer.FILENAME


def test_create_once_and_reload_across_wallet_instances(wallet):
    addr, key = signer.resolve_organization_signer(wallet, provision=True)
    assert addr == private_key_to_address(key)
    first = path(wallet).read_bytes()
    fresh = OrganizationWallet(wallet.path.parent)
    assert load_organization_signer(fresh) == (addr, key)
    assert signer.resolve_organization_signer(fresh, provision=True) == (addr, key)
    assert path(wallet).read_bytes() == first
    if os.name == 'posix':
        assert path(wallet).stat().st_mode & 0o777 == 0o600
        assert path(wallet).parent.stat().st_mode & 0o777 == 0o700
    assert key not in json.dumps(fresh.status())


def test_concurrent_first_start_serialized(wallet):
    # Distinct DB connections/locks; not just a single controller mutex.
    def initialize(_):
        return signer.resolve_organization_signer(OrganizationWallet(wallet.path.parent), provision=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        values = list(pool.map(initialize, range(12)))
    assert len(set(values)) == 1
    assert signer._load(path(wallet)) == values[0]


def test_key_loss_never_rotates_wallet_even_before_binding(wallet):
    signer.resolve_organization_signer(wallet, provision=True)
    path(wallet).unlink()
    with pytest.raises(SLHError, match='ORG_SIGNER_RECOVERY_REQUIRED'):
        signer.resolve_organization_signer(OrganizationWallet(wallet.path.parent), provision=True)
    assert not path(wallet).exists()


@pytest.mark.parametrize('replacement', ['invalid', 'wrong-address', 'different-key'])
def test_corrupt_or_substituted_signer_is_not_replaced(wallet, replacement):
    signer.resolve_organization_signer(wallet, provision=True)
    p = path(wallet)
    doc = json.loads(p.read_text())
    if replacement == 'invalid':
        p.write_text('{broken')
    else:
        doc['private_key'] = '0x' + '11' * 32
        if replacement == 'different-key':
            doc['address'] = private_key_to_address(doc['private_key'])
        p.write_text(json.dumps(doc))
    before = p.read_bytes()
    with pytest.raises(SLHError, match='ORG_SIGNER_(INVALID|IDENTITY_MISMATCH)'):
        signer.resolve_organization_signer(wallet, provision=True)
    assert p.read_bytes() == before


def test_existing_unbound_msk_requires_recovery(wallet):
    save_authorization(wallet.path.parent, {'kind': 'multisession_key', 'multisession_key_id': 'existing'})
    with pytest.raises(SLHError, match='ORG_SIGNER_RECOVERY_REQUIRED'):
        signer.resolve_organization_signer(wallet, provision=True)
    assert not path(wallet).exists()


def test_legacy_key_migrates_and_no_longer_requires_env(wallet, tmp_path, monkeypatch):
    key = '11' * 32
    legacy = tmp_path / 'legacy.key'
    legacy.write_text(key); legacy.chmod(0o600)
    monkeypatch.setenv(signer.SIGNER_ENV, str(legacy))
    expected = private_key_to_address(key), key
    assert signer.resolve_organization_signer(wallet, provision=True) == expected
    assert json.loads(path(wallet).read_text())['source'] == 'legacy-import'
    monkeypatch.delenv(signer.SIGNER_ENV)
    assert signer.resolve_organization_signer(wallet, provision=True) == expected
    assert legacy.read_text() == key


def test_invalid_legacy_path_does_not_create_replacement(wallet, tmp_path, monkeypatch):
    monkeypatch.setenv(signer.SIGNER_ENV, str(tmp_path / 'missing'))
    with pytest.raises(SLHError, match='PINKY_SWEEP_SIGNER_'):
        signer.resolve_organization_signer(wallet, provision=True)
    assert not path(wallet).exists()


def test_no_provision_from_ordinary_signing_operation(wallet):
    with pytest.raises(SLHError, match='PINKY_SWEEP_CONFIGURATION_REQUIRED'):
        load_organization_signer(wallet)
    assert not path(wallet).exists()


def test_failed_private_acl_never_publishes_wallet(wallet, monkeypatch):
    def denied(_):
        raise PermissionError('test ACL failure')
    monkeypatch.setattr(signer, 'secure_private_state_file', denied)
    with pytest.raises(PermissionError):
        signer.resolve_organization_signer(wallet, provision=True)
    assert not path(wallet).exists()
    assert not list(path(wallet).parent.iterdir())


@pytest.mark.skipif(os.name != 'posix', reason='POSIX permission test')
def test_insecure_permissions_block_signing(wallet):
    signer.resolve_organization_signer(wallet, provision=True)
    path(wallet).chmod(0o644)
    with pytest.raises(SLHError, match='ORG_SIGNER_PERMISSIONS'):
        load_organization_signer(wallet)


@pytest.mark.skipif(os.name != 'posix', reason='symlink privileges differ on Windows')
def test_broken_symlink_is_not_missing_wallet(wallet):
    p = path(wallet)
    p.parent.mkdir(mode=0o700)
    p.symlink_to(p.parent / 'nonexistent')
    with pytest.raises(SLHError, match='ORG_SIGNER_UNSAFE_STORAGE'):
        signer.resolve_organization_signer(wallet, provision=True)
    assert p.is_symlink()


def test_recover_atomic_publication_before_db_commit(wallet):
    # Simulate interruption after the secure file rename, before anchor commit.
    p = path(wallet)
    value = signer._generate()
    signer._persist(p, value, 'generated')
    assert signer.resolve_organization_signer(wallet, provision=True) == value
    p.unlink()
    with pytest.raises(SLHError, match='ORG_SIGNER_RECOVERY_REQUIRED'):
        signer.resolve_organization_signer(wallet, provision=True)


@pytest.mark.skipif(os.name != 'nt', reason='requires actual Windows ACL metadata')
def test_generated_signer_windows_acl(wallet):
    from tools.slh_seh_lab.resident_restart import signer_acl
    signer.resolve_organization_signer(wallet, provision=True)
    result = signer_acl(wallet.path.parent)
    assert result == {'platform':'WINDOWS','directory_private':True,'file_private':True}
