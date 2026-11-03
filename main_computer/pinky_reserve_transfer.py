"""Canonical signed instruction for one aggregate SLH -> Reserve credit transfer.

This is an internal Hub credit-account transfer, not a blockchain withdrawal.
Only the bound wallet signer can authorize it; the recipient is fixed by Hub
configuration. The sweep ID makes a retry idempotent on the Hub ledger.
"""
from __future__ import annotations

import json
import re

from main_computer.hub_credit_models import normalize_address
from main_computer.multisession_key_signing import recover_personal_sign_address

SWEEP_SCHEMA = 'main-computer.pinky-reserve-sweep.v2'
_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
_POSITIVE = re.compile(r'^[1-9][0-9]{0,76}$')


def canonical_sweep_intent(intent):
    keys = {'schema','sweep_id','cluster_id','chain_id',
            'wallet_address','reserve_wallet_address','credit_wei'}
    if not isinstance(intent,dict) or set(intent)!=keys:
        raise ValueError('invalid_pinky_sweep_intent')
    for field in ('sweep_id','cluster_id','chain_id'):
        if not isinstance(intent[field],str) or not _ID.fullmatch(intent[field]):
            raise ValueError('invalid_pinky_sweep_identity')
    if intent['schema'] != SWEEP_SCHEMA:
        raise ValueError('invalid_pinky_sweep_schema')
    if not isinstance(intent['credit_wei'],str) or not _POSITIVE.fullmatch(intent['credit_wei']):
        raise ValueError('invalid_pinky_sweep_amount')
    source = normalize_address(intent['wallet_address'])
    reserve = normalize_address(intent['reserve_wallet_address'])
    if source==reserve or not re.fullmatch(r'0x[0-9a-f]{40}',source) or not re.fullmatch(r'0x[0-9a-f]{40}',reserve):
        raise ValueError('invalid_pinky_sweep_wallet')
    if source!=intent['wallet_address'] or reserve!=intent['reserve_wallet_address']:
        raise ValueError('noncanonical_pinky_sweep_wallet')
    return json.dumps(intent,sort_keys=True,separators=(',',':'),ensure_ascii=True)


def verify_sweep_signature(intent,signature):
    message = canonical_sweep_intent(intent)
    if not isinstance(signature,str) or len(signature)!=132:
        raise ValueError('invalid_pinky_sweep_signature')
    try:
        recovered=recover_personal_sign_address(message,signature)
    except (ValueError,TypeError) as exc:
        raise ValueError('invalid_pinky_sweep_signature') from exc
    if normalize_address(recovered)!=intent['wallet_address']:
        raise ValueError('pinky_sweep_signer_mismatch')
    return message
