"""R01 read-only resident restart and Windows signer ACL evidence.

No private key/MSK access, ACL modification, restart or marketplace mutation.
The operator performs the normal restart between two observations.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import time

import psutil

from main_computer.slh.organization_process import read_record, _matching_process
from main_computer.slh.organization_signer import DIRECTORY, FILENAME
from main_computer.slh.storage import _windows_caller_sid
from main_computer.slh.wallet_acl import (acl_record as _acl_record, acl_is_private,
    acl_failure_reasons, unexpected_principal_details, safe_node as _safe_node)

SCHEMA = 'mcel.slh.resident-restart.v1'
POLICY_PATH = '/v1/org/admin/marketplace/requester-policy'


def process_identity(state_dir):
    record = read_record(state_dir)
    if not record or _matching_process(record, state_dir) is None:
        raise RuntimeError('RESIDENT_PROCESS_UNVERIFIED')
    return {'pid': record['pid'], 'created': record['created']}


def signer_acl(state_dir):
    if os.name != 'nt':
        return {'platform': 'NON_WINDOWS', 'directory_private': False, 'file_private': False}
    root = Path(state_dir) / DIRECTORY
    signer = root / FILENAME
    caller = _windows_caller_sid()
    # Fail closed on junctions, symlinks and unexpected file types.
    safe = (_safe_node(Path(state_dir), directory=True) and _safe_node(root, directory=True)
            and _safe_node(signer, directory=False))
    directory_record = _acl_record(root) if safe else None
    file_record = _acl_record(signer) if safe else None
    directory_reasons = acl_failure_reasons(directory_record, caller) if safe else ['UNSAFE_PATH']
    file_reasons = acl_failure_reasons(file_record, caller) if safe else ['UNSAFE_PATH']
    result = {'platform': 'WINDOWS', 'directory_private': not directory_reasons,
              'file_private': not file_reasons}
    if directory_reasons:
        result['directory_failure_reasons'] = directory_reasons
        result['directory_unexpected_principals'] = unexpected_principal_details(directory_record, caller)
    if file_reasons:
        result['file_failure_reasons'] = file_reasons
        result['file_unexpected_principals'] = unexpected_principal_details(file_record, caller)
    return result


def policy_evidence(cfg, policy):
    """Compare only administrative policy, not changing spent/held counters."""
    market = {k: cfg[k] for k in ('enabled', 'revision', 'hub_url', 'interval_seconds')}
    grant = {k: policy[k] for k in ('enabled', 'revision', 'max_request_credit_wei', 'budget_credit_wei')}
    for obj in (market, grant):
        if type(obj['enabled']) is not bool or type(obj['revision']) is not int or obj['revision'] < 0:
            raise ValueError('invalid policy')
    if (not isinstance(market['hub_url'], str) or type(market['interval_seconds']) is not int
            or not 1 <= market['interval_seconds'] <= 3600 or
            any(not isinstance(grant[k], str) or not re.fullmatch(r'[0-9]{1,78}', grant[k])
                for k in ('max_request_credit_wei', 'budget_credit_wei'))):
        raise ValueError('invalid policy')
    return {'marketplace': market, 'requester': grant}


def binding_evidence(binding):
    result = {k: binding[k] for k in ('wallet_address', 'hub_id', 'cluster_id', 'chain_id', 'account_id', 'hub_url', 'trust_mode')}
    if not all(isinstance(v, str) and v for v in result.values()):
        raise ValueError('invalid binding')
    return result


def _valid_process(value):
    return (isinstance(value, dict) and type(value.get('pid')) is int and value['pid'] > 0
            and type(value.get('created')) in (int, float) and math.isfinite(value['created']) and value['created'] > 0)


def old_process_gone(process):
    if not _valid_process(process):
        return False
    try:
        proc = psutil.Process(process['pid'])
        return abs(proc.create_time() - process['created']) > .05 or not proc.is_running()
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def _evidence(report):
    value = report.get('restart_evidence') if isinstance(report, dict) else None
    return value if isinstance(value, dict) else {}


def snapshot_checks(report):
    r = _evidence(report)
    acl = r.get('signer_acl') if isinstance(r.get('signer_acl'), dict) else {}
    p = r.get('process')
    checked = r.get('readiness_checked_at')
    captured = r.get('captured_at')
    fresh = (_valid_process(p) and type(checked) in (int, float) and type(captured) in (int, float)
             and math.isfinite(checked) and math.isfinite(captured)
             and p['created'] - .05 <= checked <= captured and 0 <= captured - checked <= 120)
    return {'resident_preflight': isinstance(report, dict) and report.get('status') == 'PASS_PREFLIGHT_ONLY',
            'verified_process_stable': bool(r.get('schema') == SCHEMA and _valid_process(p) and r.get('process_stable') is True),
            'fresh_financial_readiness': bool(fresh),
            'windows_signer_directory_private': acl.get('platform') == 'WINDOWS' and acl.get('directory_private') is True,
            'windows_signer_file_private': acl.get('platform') == 'WINDOWS' and acl.get('file_private') is True,
            'binding_and_policy_observed': isinstance(r.get('binding'), dict) and bool(r['binding']) and isinstance(r.get('policy'), dict) and bool(r['policy'])}


def snapshot_valid(report):
    return all(snapshot_checks(report).values())


def compare(before, after):
    """A different verified process, same authority/policy, both fresh READY."""
    a, b = _evidence(before), _evidence(after)
    valid_a, valid_b = snapshot_valid(before), snapshot_valid(after)
    old, new = a.get('process'), b.get('process')
    checks = {'baseline_valid': valid_a, 'current_valid': valid_b,
              'actual_restart': bool(valid_a and valid_b and new['created'] > a['captured_at'] and old_process_gone(old)),
              'same_wallet_and_hub_binding': bool(valid_a and valid_b and a['binding'] == b['binding']),
              'same_marketplace_and_spending_policy': bool(valid_a and valid_b and a['policy'] == b['policy'])}
    return {'schema': SCHEMA, 'status': 'PASS_RESTART' if all(checks.values()) else 'BLOCKED',
            'checks': checks, 'baseline_checks': snapshot_checks(before), 'current_checks': snapshot_checks(after),
            'baseline': before, 'current': after,
            'limitations': ['read-only, no restart initiated by test', 'current MSK verified by resident readiness; exact reuse/renewal tested in isolated fixtures', 'no funded transactions or Testnet certification']}
