"""Wallet-scoped ledger inspection. Read proofs cannot authorize spending."""
from __future__ import annotations
import json
import re
import time
from main_computer.org_credit_transfer import ADDRESS, IDENTITY
from main_computer.multisession_key_signing import recover_personal_sign_address

READ_SCHEMA = 'main-computer.wallet-read.v1'

def canonical_read(intent):
    if type(intent) is not dict or set(intent) != {'schema','cluster_id','chain_id','wallet_address','expires_at'} or intent['schema'] != READ_SCHEMA:
        raise ValueError('invalid_wallet_read_intent')
    for key in ('cluster_id','chain_id'):
        if not isinstance(intent[key],str) or not IDENTITY.fullmatch(intent[key]):
            raise ValueError('invalid_wallet_read_scope')
    if not isinstance(intent['wallet_address'],str) or not ADDRESS.fullmatch(intent['wallet_address']):
        raise ValueError('invalid_wallet_read_address')
    if type(intent['expires_at']) is not int:
        raise ValueError('invalid_wallet_read_expiry')
    return json.dumps(intent,sort_keys=True,separators=(',',':'),ensure_ascii=True)

def verify_read(intent,signature,cluster,chain,now=None):
    message=canonical_read(intent)
    now=int(time.time()) if now is None else now
    if intent['cluster_id']!=cluster or intent['chain_id']!=str(chain) or not now < intent['expires_at'] <= now+86400:
        raise ValueError('wallet_read_scope_or_expiry')
    if not isinstance(signature,str) or not re.fullmatch(r'0x[0-9a-fA-F]{130}',signature) or recover_personal_sign_address(message,signature).lower()!=intent['wallet_address']:
        raise ValueError('wallet_read_signer_mismatch')
    return intent['wallet_address']

def ledger_view(directory,wallet,*,limit=50,before=''):
    from main_computer.hub_credit_indexer import wallet_account_id
    account_id=wallet_account_id(wallet)
    if type(limit) is not int or not 1<=limit<=100 or not isinstance(before,str) or len(before)>200:
        raise ValueError('invalid_wallet_history_page')
    # One consistent store read; never disclose other account rows or private metadata.
    with directory._lock:
        data=directory.store.load()
        account=data.get('payout_accounts',{}).get(account_id) or {}
        if account and account.get('wallet_address')!=wallet:
            raise ValueError('wallet_account_mismatch')
        balances={k:str(int(account.get(k,0))) for k in ('available_credit_wei','held_credit_wei','spent_credit_wei','earned_credit_wei','bridge_completed_credit_wei')}
        txs=[t for t in data.get('payout_transactions',{}).values() if t.get('account_id')==account_id]
        txs.sort(key=lambda t:(str(t.get('created_at','')),str(t.get('transaction_id',''))),reverse=True)
        if before:
            pos=next((i for i,t in enumerate(txs) if t.get('transaction_id')==before),None)
            if pos is None: raise ValueError('wallet_history_cursor_unknown')
            txs=txs[pos+1:]
        transactions=[{k:str(t.get(k,'')) for k in ('transaction_id','transaction_type','credit_wei','created_at','deposit_id')} for t in txs[:limit]]
        # Prepared deposits are not credits; completed deposits are already in available/spent totals.
        deposits=[]
        for key,d in data.get('payout_deposit_intents',{}).items():
            if d.get('account_id')!=account_id or d.get('wallet_address')!=wallet: continue
            completed=data.get('payout_deposits',{}).get(key) or {}
            deposits.append({**{k:str(d.get(k,'')) for k in ('deposit_id','chain_id','contract_address','deposit_amount_units','status','created_at')},'completion_tx_hash':str(completed.get('completion_tx_hash','')),'funding_tx_hash':str(completed.get('funding_tx_hash',''))})
        deposits.sort(key=lambda d:(d['created_at'],d['deposit_id']),reverse=True)
        return {'bridge_deposits':deposits[:50],'bridge_deposit_count':len(deposits),'ok':True,'wallet_address':wallet,'account_id':account_id,'cluster_id':directory.cluster_id,'chain_id':str(directory.topology.network.get('chain_id') or ''),'balance':balances,'transactions':transactions,'next_cursor':transactions[-1]['transaction_id'] if len(txs)>limit else '', 'observed_at':int(time.time())}


def transfer_status(directory,intent,signature):
    from main_computer.org_credit_transfer import verify_transfer_signature
    from main_computer.hub_credit_indexer import wallet_account_id
    import hashlib
    message=verify_transfer_signature(intent,signature)
    if intent['cluster_id']!=directory.cluster_id or intent['chain_id']!=str(directory.topology.network.get('chain_id') or ''):
        raise ValueError('credit_transfer_scope_mismatch')
    key=wallet_account_id(intent['wallet_address'])+':'+intent['transfer_id']
    def observe():
        with directory._lock:
            data=directory.store.load()
            receipts=data.setdefault('org_credit_transfers',{})
            existing=receipts.get(key)
            digest=hashlib.sha256(message.encode('ascii')).hexdigest()
            if existing:
                if existing['intent_digest']!=digest:
                    raise ValueError('credit_transfer_idempotency_conflict')
                return dict(existing)
            if int(time.time())<=intent['expires_at']+60:
                return {'ok':True,'status':'not_found',**intent}
            # Seal the absence with an atomic ledger tombstone. A competing
            # commit must conflict or see the tombstone; no late debit follows.
            expired={'ok':True,'status':'expired_not_found','intent_digest':digest,**intent}
            receipts[key]=expired
            directory.store.save(data)
            return dict(expired)
    atomic=getattr(directory.store,'atomic',None)
    return atomic(observe) if callable(atomic) else observe()
