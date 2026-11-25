"""Exact, SID-based ACL policy for the organizational signer only.

Never read key material or recurse into other state. On Windows replace the
wallet directory DACL in one operation, and independently inspect the result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

from .storage import _windows_caller_sid

SYSTEM = 'S-1-5-18'
ADMINISTRATORS = 'S-1-5-32-544'
FULL_CONTROL = 2032127

_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $env:MCEL_R01_ACL_PATH
# Inspect every raw ACE, including entries GetAccessRules might omit.
$raw = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorBinaryForm(), 0)
# Windows PowerShell 5.1 can fail while processing raw ACEs through
# the pipeline. Index the RawAcl and operate on integer flag masks;
# do not use enum-valued operands with PowerShell's bitwise operators.
$dacl = $raw.DiscretionaryAcl
$rules = @(if ($null -ne $dacl) {
  for ($i = 0; $i -lt $dacl.Count; $i++) {
    $ace = $dacl.get_Item($i)
    $known = $ace -is [System.Security.AccessControl.KnownAce]
    $flags = [int]$ace.AceFlags
    $type = [int]$ace.AceType
    $sid = $null
    $rights = $null
    if ($known) {
      $sid = $ace.SecurityIdentifier.Value
      $rights = [int]$ace.AccessMask
    }
    [pscustomobject]@{
      sid=$sid
      allow=($type -eq 0)
      inherited=(($flags -band 16) -ne 0)
      inherit_only=(($flags -band 8) -ne 0)
      rights=$rights
    }
  }
})
[pscustomobject]@{owner=$raw.Owner.Value; protected=$acl.AreAccessRulesProtected; rules=$rules} | ConvertTo-Json -Depth 4 -Compress
"""

# Replace the entire protected DACL, rather than enumerating and purging
# individual rules on the existing descriptor. Persist only the Access
# section using DirectorySecurity, not the PowerShell provider's Set-Acl.
_DIRECTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$p = $env:MCEL_R01_ACL_PATH
$item = Get-Item -LiteralPath $p -Force
if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'unsafe directory' }
$caller = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$trusted = @($caller, 'S-1-5-18', 'S-1-5-32-544') | Select-Object -Unique
$existing = Get-Acl -LiteralPath $p
$sidType = [System.Security.Principal.SecurityIdentifier]
if ($trusted -notcontains $existing.GetOwner($sidType).Value) { throw 'unexpected owner' }
# D:P = protected DACL; OICI = inheritance to new files and directories;
# FA = file full access. No owner/group/SACL section is supplied.
$sddl = 'D:P' + (($trusted | ForEach-Object { '(A;OICI;FA;;;' + $_ + ')' }) -join '')
$replacement = [System.Security.AccessControl.DirectorySecurity]::new()
$replacement.SetSecurityDescriptorSddlForm($sddl, [System.Security.AccessControl.AccessControlSections]::Access)
[System.IO.Directory]::SetAccessControl($item.FullName, $replacement)
"""


def _run(path, script):
    try:
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                              env={**os.environ, 'MCEL_R01_ACL_PATH': str(path)},
                              capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PermissionError('ORG_SIGNER_ACL_OPERATION_FAILED') from exc


def acl_record(path):
    return json.loads(_run(path, _ACL_SCRIPT).stdout)


def acl_failure_reasons(record, caller):
    trusted = {caller, SYSTEM, ADMINISTRATORS}
    if not isinstance(record, dict):
        return ['INVALID_ACL_RECORD']
    reasons = set()
    if record.get('owner') not in trusted:
        reasons.add('UNTRUSTED_OWNER')
    if record.get('protected') is not True:
        reasons.add('INHERITANCE_NOT_PROTECTED')
    rules = record.get('rules')
    if not isinstance(rules, list) or not rules:
        return sorted(reasons | {'MISSING_ACCESS_RULES'})
    granted = set()
    for rule in rules:
        if not isinstance(rule, dict) or type(rule.get('rights')) is not int:
            reasons.add('INVALID_ACCESS_RULE')
            continue
        if rule.get('sid') not in trusted:
            reasons.add('UNEXPECTED_PRINCIPAL')
        if rule.get('inherited') is not False:
            reasons.add('INHERITED_ACCESS_RULE')
        if rule.get('allow') is not True:
            reasons.add('NON_ALLOW_ACCESS_RULE')
        if rule.get('inherit_only') is not False:
            reasons.add('INEFFECTIVE_OR_UNKNOWN_ACCESS_RULE')
        if (rule.get('sid') in trusted and rule.get('allow') is True
                and rule.get('inherited') is False and rule.get('inherit_only') is False
                and rule['rights'] & FULL_CONTROL == FULL_CONTROL):
            granted.add(rule['sid'])
    if granted != trusted:
        reasons.add('TRUSTED_FULL_CONTROL_MISSING')
    return sorted(reasons)


_WELL_KNOWN = {'S-1-1-0': 'EVERYONE', 'S-1-3-0': 'CREATOR_OWNER',
               'S-1-3-1': 'CREATOR_GROUP', 'S-1-3-4': 'OWNER_RIGHTS',
               'S-1-5-11': 'AUTHENTICATED_USERS', 'S-1-5-32-545': 'BUILTIN_USERS'}


def unexpected_principal_details(record, caller):
    """Bounded metadata only: never output account names, SIDs or key bytes."""
    trusted = {caller, SYSTEM, ADMINISTRATORS}
    rules = record.get('rules') if isinstance(record, dict) else None
    if not isinstance(rules, list):
        return []
    details = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        sid = rule.get('sid')
        if not isinstance(sid, str) or sid in trusted:
            continue
        details.append({'principal': _WELL_KNOWN.get(sid, 'OTHER_PRINCIPAL'),
                        'allow': rule.get('allow') is True,
                        'inherited': rule.get('inherited') is True,
                        'inherit_only': rule.get('inherit_only') is True,
                        'rights': rule.get('rights') if type(rule.get('rights')) is int else None})
    return details[:16]


def acl_is_private(record, caller):
    return not acl_failure_reasons(record, caller)


def safe_node(path, *, directory):
    info = Path(path).lstat()
    if Path(path).is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        return False
    return stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)


def ensure_windows_wallet_directory(path, *, repair=False):
    """Verify; optionally reconcile the existing directory, never its contents."""
    path = Path(path)
    if not safe_node(path.parent, directory=True) or not safe_node(path, directory=True):
        raise PermissionError('ORG_SIGNER_UNSAFE_STORAGE')
    caller = _windows_caller_sid()
    before = acl_record(path)
    reasons = acl_failure_reasons(before, caller)
    if reasons and repair:
        # An untrusted owner can change the DACL at will. Do not silently take
        # ownership or claim an unknown authority is safe.
        if 'UNTRUSTED_OWNER' not in reasons and 'INVALID_ACL_RECORD' not in reasons:
            _run(path, _DIRECTORY_SCRIPT)
            after = acl_record(path)
            reasons = acl_failure_reasons(after, caller)
            if isinstance(after, dict) and after.get('owner') != before.get('owner'):
                reasons.append('OWNER_CHANGED_DURING_REPAIR')
    if reasons:
        raise PermissionError('ORG_SIGNER_DIRECTORY_ACL_UNVERIFIED: ' + ','.join(reasons))


def ensure_windows_wallet_file(path):
    path = Path(path)
    if not safe_node(path, directory=False):
        raise PermissionError('ORG_SIGNER_UNSAFE_STORAGE')
    reasons = acl_failure_reasons(acl_record(path), _windows_caller_sid())
    if reasons:
        raise PermissionError('ORG_SIGNER_FILE_ACL_UNVERIFIED: ' + ','.join(reasons))
