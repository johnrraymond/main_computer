"""Only the wallet directory is repaired; key bytes and file ACL remain intact."""
import os
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from main_computer.slh import wallet_acl as acl
from main_computer.slh import organization_signer as signer
from main_computer.slh.organization_wallet import OrganizationWallet
from main_computer.slh.authority import SLHError

CALLER = 'S-1-5-21-1-2-3-1000'


def private():
    return {'owner': CALLER, 'protected': True, 'rules': [
        {'sid': sid, 'allow': True, 'inherited': False, 'inherit_only': False, 'rights': acl.FULL_CONTROL}
        for sid in (CALLER, acl.SYSTEM, acl.ADMINISTRATORS)]}


@pytest.fixture
def directory(tmp_path, monkeypatch):
    root = tmp_path / 'wallet-private'
    root.mkdir()
    monkeypatch.setattr(acl, '_windows_caller_sid', lambda: CALLER)
    return root


@pytest.mark.parametrize('defect', ['extra', 'unprotected', 'inherited', 'missing', 'denial', 'inherit-only'])
def test_directory_reconciles_then_reads_back(directory, monkeypatch, defect):
    old = private()
    if defect == 'extra': old['rules'].append({'sid': 'S-1-5-21-4-5-6-1000', 'allow': True, 'inherited': False, 'inherit_only': False, 'rights': 1})
    if defect == 'unprotected': old['protected'] = False
    if defect == 'inherited': old['rules'][0]['inherited'] = True
    if defect == 'missing': old['rules'].pop()
    if defect == 'denial': old['rules'][0]['allow'] = False
    if defect == 'inherit-only': old['rules'][0]['inherit_only'] = True
    results = iter((old, private()))
    calls = []
    monkeypatch.setattr(acl, 'acl_record', lambda p: next(results))
    monkeypatch.setattr(acl, '_run', lambda p,s: calls.append((p,s)))
    acl.ensure_windows_wallet_directory(directory, repair=True)
    assert calls == [(directory, acl._DIRECTORY_SCRIPT)]
    assert '[System.IO.Directory]::SetAccessControl' in calls[0][1]
    assert 'SetSecurityDescriptorSddlForm' in calls[0][1]
    assert "'D:P'" in calls[0][1]
    assert 'PurgeAccessRules(' not in calls[0][1]


@pytest.mark.parametrize('mode', ['already-private', 'read-only', 'foreign-owner', 'repair-failed'])
def test_directory_no_unverified_success(directory, monkeypatch, mode):
    old = private()
    if mode != 'already-private': old['protected'] = False
    if mode == 'foreign-owner': old['owner'] = 'S-1-1-0'
    writes = []
    monkeypatch.setattr(acl, 'acl_record', lambda p: deepcopy(old))
    monkeypatch.setattr(acl, '_run', lambda p,s: writes.append(p))
    if mode == 'already-private':
        acl.ensure_windows_wallet_directory(directory, repair=True)
    else:
        with pytest.raises(PermissionError, match='DIRECTORY_ACL_UNVERIFIED'):
            acl.ensure_windows_wallet_directory(directory, repair=mode != 'read-only')
    assert writes == ([directory] if mode == 'repair-failed' else [])


def test_insecure_file_is_refused_not_repaired(directory, monkeypatch):
    path = directory / signer.FILENAME
    path.write_text('not-a-key')
    old = private()
    old['rules'].append({'sid':'S-1-1-0','allow':True,'inherited':False,'inherit_only':False,'rights':1})
    monkeypatch.setattr(acl, 'acl_record', lambda p: old)
    monkeypatch.setattr(acl, '_run', lambda *a: pytest.fail('must not mutate file'))
    with pytest.raises(PermissionError, match='FILE_ACL_UNVERIFIED: UNEXPECTED_PRINCIPAL'):
        acl.ensure_windows_wallet_file(path)
    assert path.read_text() == 'not-a-key'


def test_subprocess_failure_is_closed(directory, monkeypatch):
    def fail(*a, **kw):
        raise subprocess.TimeoutExpired('powershell', 20)
    monkeypatch.setattr(acl.subprocess, 'run', fail)
    with pytest.raises(PermissionError, match='ORG_SIGNER_ACL_OPERATION_FAILED'):
        acl.acl_record(directory)


@pytest.mark.skipif(os.name != 'posix', reason='simulated Windows policy over disposable POSIX files')
def test_existing_signer_startup_reconciles_without_rewriting(tmp_path, monkeypatch):
    monkeypatch.delenv(signer.SIGNER_ENV, raising=False)
    wallet = OrganizationWallet(tmp_path / 'state')
    original = signer.resolve_organization_signer(wallet, provision=True)
    path = wallet.path.parent / signer.DIRECTORY / signer.FILENAME
    before = path.read_bytes()
    calls = []
    # Only alter the signer's platform branch, never the global os module.
    monkeypatch.setattr(signer, 'os', SimpleNamespace(name='nt', path=os.path))
    monkeypatch.setattr(signer, 'ensure_windows_wallet_directory', lambda p, *, repair: calls.append((p, repair)))
    monkeypatch.setattr(signer, 'ensure_windows_wallet_file', lambda p: calls.append(p))
    assert signer.resolve_organization_signer(wallet, provision=True) == original
    assert calls == [(path.parent, True), path]
    assert path.read_bytes() == before
    calls.clear()
    assert signer.resolve_organization_signer(wallet) == original
    assert calls == [(path.parent, False), path]
    def refuse(p): raise PermissionError('test refusal')
    monkeypatch.setattr(signer, 'ensure_windows_wallet_file', refuse)
    with pytest.raises(SLHError, match='ORG_SIGNER_ACL_UNVERIFIED'):
        signer.resolve_organization_signer(wallet, provision=True)
    assert path.read_bytes() == before


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows NTFS and PowerShell')
@pytest.mark.parametrize('extra_ace', ['*S-1-5-32-545:R', '*S-1-3-0:(OI)(CI)(IO)R',
                                       '*S-1-5-21-900001-900002-900003-1001:R'])
def test_windows_existing_directory_extra_ace_is_removed_without_key_replacement(tmp_path, monkeypatch, extra_ace):
    monkeypatch.delenv(signer.SIGNER_ENV, raising=False)
    wallet = OrganizationWallet(tmp_path / 'state')
    original = signer.resolve_organization_signer(wallet, provision=True)
    path = wallet.path.parent / signer.DIRECTORY / signer.FILENAME
    before = path.read_bytes()
    # This is a disposable wallet. The protected file must stay private
    # even when the injected directory ACE is inheritable.
    before_file_acl = acl.acl_record(path)
    before_owner = acl.acl_record(path.parent)['owner']
    expected_sid = extra_ace.split(':', 1)[0].lstrip('*')
    if expected_sid == 'S-1-5-21-900001-900002-900003-1001':
        # icacls can refuse an unmapped SID (Win32 error 1332) before
        # writing the ACE. Use the raw SDDL injection verified on NTFS
        # by the operator's disposable-wallet Twiddle instead.
        acl._run(path.parent, r"""
$ErrorActionPreference = 'Stop'
$p = $env:MCEL_R01_ACL_PATH
$section = [System.Security.AccessControl.AccessControlSections]::Access
$current = Get-Acl -LiteralPath $p
$sddl = $current.GetSecurityDescriptorSddlForm($section)
$sddl += '(A;;FR;;;S-1-5-21-900001-900002-900003-1001)'
$replacement = [System.Security.AccessControl.DirectorySecurity]::new()
$replacement.SetSecurityDescriptorSddlForm($sddl, $section)
[System.IO.Directory]::SetAccessControl($p, $replacement)
""")
    else:
        subprocess.run(['icacls.exe', str(path.parent), '/grant', extra_ace], check=True, capture_output=True)
    caller = acl._windows_caller_sid()
    injected = acl.acl_record(path.parent)
    assert any(rule['sid'] == expected_sid for rule in injected['rules']), 'fixture did not inject the requested SID'
    assert not acl.acl_is_private(injected, caller)
    assert acl.acl_is_private(acl.acl_record(path), caller)
    assert signer.resolve_organization_signer(wallet, provision=True) == original
    assert acl.acl_is_private(acl.acl_record(path.parent), caller)
    assert acl.acl_is_private(acl.acl_record(path), caller)
    assert path.read_bytes() == before

    assert acl.acl_record(path.parent)['owner'] == before_owner
    assert acl.acl_record(path) == before_file_acl


def test_owner_change_after_repair_is_refused(directory, monkeypatch):
    old, new = private(), private()
    old['rules'].append({'sid':'S-1-1-0','allow':True,'inherited':False,'inherit_only':False,'rights':1})
    new['owner'] = acl.ADMINISTRATORS
    results = iter((old, new))
    monkeypatch.setattr(acl, 'acl_record', lambda p: next(results))
    monkeypatch.setattr(acl, '_run', lambda *a: None)
    with pytest.raises(PermissionError, match='OWNER_CHANGED_DURING_REPAIR'):
        acl.ensure_windows_wallet_directory(directory, repair=True)


def test_unexpected_principals_report_only_bounded_metadata():
    record = private()
    foreign = 'S-1-5-21-123-456-789-1001'
    record['rules'] += [dict(sid=sid, allow=True, inherited=False, inherit_only=True, rights=1)
                        for sid in ('S-1-3-0', foreign)]
    result = acl.unexpected_principal_details(record, CALLER)
    assert [x['principal'] for x in result] == ['CREATOR_OWNER', 'OTHER_PRINCIPAL']
    assert all(x['inherit_only'] for x in result)
    assert foreign not in str(result) and CALLER not in str(result)
    assert 'UNEXPECTED_PRINCIPAL' in acl.acl_failure_reasons(record, CALLER)


def test_unknown_ace_and_inherit_only_grant_fail_closed():
    record = private()
    record['rules'].append({'sid': None, 'allow':False, 'inherited':False, 'inherit_only':False, 'rights':None})
    assert 'INVALID_ACCESS_RULE' in acl.acl_failure_reasons(record, CALLER)
    record = private()
    record['rules'][0]['inherit_only'] = True
    assert 'TRUSTED_FULL_CONTROL_MISSING' in acl.acl_failure_reasons(record, CALLER)


def test_raw_acl_reader_uses_indexed_aces_and_integer_flags():
    # Structural regression for the Windows PowerShell 5.1 failure. Actual
    # conversion and NTFS behavior are covered by the Windows tests below.
    script = acl._ACL_SCRIPT
    assert '$dacl.get_Item($i)' in script
    assert '$raw.DiscretionaryAcl |' not in script
    assert '$flags = [int]$ace.AceFlags' in script
    assert '$flags -band 16' in script
    assert '$flags -band 8' in script
    assert '$null -ne $dacl' in script


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows NTFS and PowerShell')
def test_windows_raw_acl_reader_before_wallet_provisioning(tmp_path):
    # Reproduce the exact pre-provision read that crashed in all three
    # repair cases. No signer/key is created by this test.
    directory = tmp_path / 'raw-acl-read'
    directory.mkdir()
    record = acl.acl_record(directory)
    assert record['owner']
    assert isinstance(record['protected'], bool)
    assert isinstance(record['rules'], list) and record['rules']
    for rule in record['rules']:
        assert type(rule['inherited']) is bool
        assert type(rule['inherit_only']) is bool
        assert type(rule['allow']) is bool
    # An independently injected inherit-only ACE must be visible in the
    # raw reader rather than lost through GetAccessRules filtering.
    subprocess.run(['icacls.exe', str(directory), '/grant',
                    '*S-1-3-0:(OI)(CI)(IO)R'], check=True, capture_output=True)
    record = acl.acl_record(directory)
    assert any(rule['sid'] == 'S-1-3-0' and rule['inherit_only'] is True
               and rule['allow'] is True and rule['inherited'] is False
               for rule in record['rules'])
    assert not acl.acl_is_private(record, acl._windows_caller_sid())
