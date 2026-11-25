"""Canonical wallet-signed, cluster-scoped internal credit transfer."""
from __future__ import annotations
import json
import re
from main_computer.multisession_key_signing import recover_personal_sign_address

SCHEMA = 'main-computer.org-credit-transfer.v1'
ADDRESS = re.compile(r'0x[0-9a-f]{40}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
FIELDS = {'schema', 'transfer_id', 'cluster_id', 'chain_id', 'wallet_address',
          'recipient_wallet_address', 'credit_wei', 'expires_at'}


def canonical_transfer_intent(intent):
    if type(intent) is not dict or set(intent) != FIELDS or intent['schema'] != SCHEMA:
        raise ValueError('invalid_credit_transfer_intent')
    for field in ('transfer_id', 'cluster_id', 'chain_id'):
        if not isinstance(intent[field], str) or not IDENTITY.fullmatch(intent[field]):
            raise ValueError('invalid_credit_transfer_identity')
    for field in ('wallet_address', 'recipient_wallet_address'):
        value = intent[field]
        if not isinstance(value, str) or not ADDRESS.fullmatch(value) or value == '0x' + '0'*40:
            raise ValueError('invalid_credit_transfer_wallet')
    if intent['wallet_address'] == intent['recipient_wallet_address']:
        raise ValueError('credit_transfer_self_recipient')
    value = intent['credit_wei']
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,76}', value):
        raise ValueError('invalid_credit_transfer_amount')
    if type(intent['expires_at']) is not int or not 0 < intent['expires_at'] < 2**32:
        raise ValueError('invalid_credit_transfer_expiry')
    return json.dumps(intent, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def verify_transfer_signature(intent, signature):
    message = canonical_transfer_intent(intent)
    if not isinstance(signature, str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}', signature):
        raise ValueError('invalid_credit_transfer_signature')
    if recover_personal_sign_address(message, signature).lower() != intent['wallet_address']:
        raise ValueError('credit_transfer_signer_mismatch')
    return message
