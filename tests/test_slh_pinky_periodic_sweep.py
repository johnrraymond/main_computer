"""P4: real Hub accounting, aggregate local sweep, crash/idempotency and refusal."""
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address,sign_personal_message
from main_computer.pinky_reserve_transfer import SWEEP_SCHEMA,canonical_sweep_intent
from main_computer.slh.authority import SLHError
from main_computer.slh.pinky_sweep import PinkySweep,RESERVE_ENV,SIGNER_ENV
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import (InMemoryStableWorkerSessionStore,
        StableHubPayoutLedgerDirectory,StableHubWorkerSessionError)

KEY='11'*32
RESERVE_KEY='22'*32
WALLET=private_key_to_address(KEY)
RESERVE=private_key_to_address(RESERVE_KEY)


class Private:
    org_id='private-lab'
    def __init__(self,root):
        self.db_path=Path(root)/'private.sqlite3'
        self._lock=threading.RLock()
        with self._db() as db:
            db.execute('''CREATE TABLE pinky_obligations (
               obligation_id TEXT PRIMARY KEY,org_id TEXT, assessed_at_ns INTEGER)''')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.db_path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def assess(self,n):
        with self._lock,self._db() as db:
            for i in range(n):
                db.execute('INSERT INTO pinky_obligations VALUES (?,?,?)',(f'oid-{i}',self.org_id,i))


class Wallet:
    def __init__(self,ledger):self.ledger=ledger
    def reconcile(self):
        account=self.ledger.get_account(wallet_account_id(WALLET))
        return {'state':'CURRENT','binding':{'wallet_address':WALLET,
            'hub_url':'http://127.0.0.1:1','tls_sha256':None,
            'cluster_id':self.ledger.cluster_id,
            'chain_id':str(self.ledger.topology.network['chain_id'])},
            'balance':{'available_credit_wei':account['available_credit_wei']}}


class Reader:
    def __init__(self,ledger):self.ledger=ledger;self.lost_once=False
    def request(self,method,path,data):
        assert (method,path)==('POST','/api/hub/v1/credits/pinky-reserve/sweep')
        result=self.ledger.transfer_pinky_reserve(intent=data['intent'],signature=data['signature'],
                         configured_reserve_wallet=RESERVE)
        if self.lost_once:
            self.lost_once=False
            raise SLHError('HUB_ACCOUNT_UNAVAILABLE',503)
        return result


@pytest.fixture
def env():
    with tempfile.TemporaryDirectory() as tmp:
        private=Private(tmp)
        topology=load_stable_hub_topology(Path('deploy/hub-topology/dev-topology.json'))
        ledger=StableHubPayoutLedgerDirectory(topology=topology,hub_id='dev-hub1',
                                              store=InMemoryStableWorkerSessionStore())
        ledger.fund_account(account_id=wallet_account_id(WALLET),wallet_address=WALLET,
                            credits='3',replace=True)
        wallet=Wallet(ledger)
        keyfile=Path(tmp)/'signer.key';keyfile.write_text(KEY)
        if os.name=='posix':keyfile.chmod(0o600)
        reader=Reader(ledger)
        with patch.dict(os.environ,{RESERVE_ENV:RESERVE,SIGNER_ENV:str(keyfile)}):
            yield private,ledger,wallet,reader


def test_hub_transfer_exact_debit_credit_and_retry(env):
    private,ledger,_,_=env
    amount=str(CREDIT_WEI_PER_CREDIT//10)
    intent={'schema':SWEEP_SCHEMA,'sweep_id':'psweep-1',
            'cluster_id':ledger.cluster_id,'chain_id':str(ledger.topology.network['chain_id']),
            'wallet_address':WALLET,'reserve_wallet_address':RESERVE,'credit_wei':amount}
    signature=sign_personal_message(canonical_sweep_intent(intent),KEY)
    before=ledger.get_account(wallet_account_id(WALLET))
    receipt=ledger.transfer_pinky_reserve(intent=intent,signature=signature,
                                          configured_reserve_wallet=RESERVE)
    assert receipt['status']=='confirmed'
    assert ledger.transfer_pinky_reserve(intent=intent,signature=signature,
                                         configured_reserve_wallet=RESERVE)==receipt
    assert int(ledger.get_account(wallet_account_id(WALLET))['available_credit_wei'])==int(before['available_credit_wei'])-int(amount)
    assert int(ledger.get_account(wallet_account_id(RESERVE))['available_credit_wei'])==int(amount)
    assert len(ledger.store.load()['pinky_reserve_sweeps'])==1
    assert len([x for x in ledger.store.load()['payout_transactions'].values()
                if x['transaction_type'].startswith('pinky_reserve_')])==2
    with pytest.raises((ValueError,StableHubWorkerSessionError)):
        ledger.transfer_pinky_reserve(intent={**intent,'credit_wei':str(2*int(amount))},
                    signature=sign_personal_message(canonical_sweep_intent({**intent,'credit_wei':str(2*int(amount))}),KEY),
                    configured_reserve_wallet=RESERVE)
    with pytest.raises((ValueError,StableHubWorkerSessionError)):
        ledger.transfer_pinky_reserve(intent=intent,signature=sign_personal_message(canonical_sweep_intent(intent),RESERVE_KEY),
                    configured_reserve_wallet=RESERVE)
    with pytest.raises((ValueError,StableHubWorkerSessionError)):
        ledger.transfer_pinky_reserve(intent=intent,signature=signature,configured_reserve_wallet=WALLET)


def test_aggregate_sweep_and_idempotent_retry_after_lost_receipt(env):
    private,ledger,wallet,reader=env
    private.assess(12)
    sweeper=PinkySweep(private,wallet,reader_factory=lambda *args:reader)
    reader.lost_once=True
    with pytest.raises(SLHError):sweeper.sweep()
    assert sweeper.status()['outstanding_count']==12
    assert sweeper.status()['pending_sweeps']==1
    assert ledger.get_account(wallet_account_id(RESERVE))['available_credits_display']=='1.2'
    assert sweeper.sweep()['status']=='CONFIRMED'
    assert sweeper.status()['outstanding_count']==0
    assert sweeper.status()['pending_sweeps']==0
    assert sweeper.status()['paid_count']==12
    assert sweeper.sweep()['status']=='NOTHING_DUE'
    assert ledger.get_account(wallet_account_id(RESERVE))['available_credits_display']=='1.2'
    second=PinkySweep(private,wallet,reader_factory=lambda *args:reader)
    assert second.status()['outstanding_count']==0
    assert second.sweep()['status']=='NOTHING_DUE'
    assert len(ledger.store.load()['pinky_reserve_sweeps'])==1


def test_partial_funds_carry_forward(env):
    private,ledger,wallet,reader=env
    private.assess(12)
    ledger.fund_account(account_id=wallet_account_id(WALLET),wallet_address=WALLET,credits='0.5',replace=True)
    sweeper=PinkySweep(private,wallet,reader_factory=lambda *args:reader)
    assert sweeper.sweep()['credit_wei']==str(5*10**17)
    assert sweeper.status()['outstanding_count']==7
    assert sweeper.sweep()['status']=='INSUFFICIENT_CREDITS'
    ledger.fund_account(account_id=wallet_account_id(WALLET),wallet_address=WALLET,credits='0.7',replace=False)
    assert sweeper.sweep()['credit_wei']==str(7*10**17)
    assert sweeper.status()['outstanding_count']==0
    assert ledger.get_account(wallet_account_id(RESERVE))['available_credits_display']=='1.2'


def test_configuration_missing_does_not_erase_debt(env):
    private,ledger,wallet,reader=env
    private.assess(2)
    sweeper=PinkySweep(private,wallet,reader_factory=lambda *args:reader)
    with patch.dict(os.environ,{SIGNER_ENV:''}):
        with pytest.raises(SLHError):sweeper.sweep()
    assert sweeper.status()['outstanding_count']==2
    assert ledger.get_account(wallet_account_id(RESERVE)) is None


def test_tampered_signature_and_wrong_sender_rejected_before_debit(env):
    _,ledger,_,_=env
    intent={'schema':SWEEP_SCHEMA,'sweep_id':'psweep-2',
            'cluster_id':ledger.cluster_id,'chain_id':str(ledger.topology.network['chain_id']),
            'wallet_address':WALLET,'reserve_wallet_address':RESERVE,'credit_wei':str(10**17)}
    before=ledger.get_account(wallet_account_id(WALLET))['available_credit_wei']
    signature=sign_personal_message(canonical_sweep_intent(intent),KEY)
    for changed in ({**intent,'chain_id':'9999'}, {**intent,'reserve_wallet_address':WALLET}):
        with pytest.raises((ValueError,StableHubWorkerSessionError)):
            ledger.transfer_pinky_reserve(intent=changed,signature=signature,
                                          configured_reserve_wallet=RESERVE)
    assert ledger.get_account(wallet_account_id(WALLET))['available_credit_wei']==before


def test_stable_hub_http_signed_sweep_and_replay(env):
    from urllib.request import Request,urlopen
    from main_computer.stable_hub import create_stable_hub_server
    import json
    private,ledger,_,_=env
    # The HTTP server and ledger use the same durable Hub store.
    server=create_stable_hub_server(topology=ledger.topology,hub_id='dev-hub1',
        bind_host='127.0.0.1',bind_port=0,worker_session_store=ledger.store)
    t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        intent={'schema':SWEEP_SCHEMA,'sweep_id':'psweep-http',
            'cluster_id':ledger.cluster_id,'chain_id':str(ledger.topology.network['chain_id']),
            'wallet_address':WALLET,'reserve_wallet_address':RESERVE,'credit_wei':str(10**17)}
        payload={'intent':intent,'signature':sign_personal_message(canonical_sweep_intent(intent),KEY)}
        request=Request(f'http://127.0.0.1:{server.server_port}/api/hub/v1/credits/pinky-reserve/sweep',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urlopen(request,timeout=4) as response:
            result=json.loads(response.read())
        with urlopen(request,timeout=4) as response:
            duplicate=json.loads(response.read())
        assert result==duplicate
        assert ledger.get_account(wallet_account_id(RESERVE))['available_credit_wei']==str(10**17)
    finally:
        server.shutdown();t.join(timeout=3);server.server_close()


def test_bad_hub_receipt_remains_pending_without_duplicate_debit(env):
    private,ledger,wallet,reader=env
    private.assess(3)
    class AlteredReader:
        bad=True
        def request(self, method,path,data):
            receipt=reader.request(method,path,data)
            if self.bad:
                self.bad=False
                return {**receipt,'credit_wei':str(int(receipt['credit_wei'])+1)}
            return receipt
    proxy=AlteredReader()
    sweeper=PinkySweep(private,wallet,reader_factory=lambda *args:proxy)
    with pytest.raises(SLHError):sweeper.sweep()
    assert sweeper.status()['pending_sweeps']==1
    assert sweeper.status()['paid_count']==0
    assert sweeper.sweep()['status']=='CONFIRMED'
    assert sweeper.status()['paid_count']==3
    assert ledger.get_account(wallet_account_id(RESERVE))['available_credits_display']=='0.3'


def test_actual_private_fulfillment_creates_batchable_debt(env):
    from tests.test_slh_private_requests import TestPrivateRequests
    private,ledger,wallet,reader=env
    fixture=TestPrivateRequests('test_real_private_worker_success_and_metadata_only_durable_replay')
    fixture.setUp()
    try:
        requester=fixture.enroll('req-p4','requester')
        fixture.worker()
        status, result=fixture.submit(requester,fixture.req('p4-completed'))
        assert status==200 and result['status']=='SUCCEEDED'
        assert fixture.private.obligations()['count']==1
        sweeper=PinkySweep(fixture.private,wallet,reader_factory=lambda *args:reader)
        paid=sweeper.sweep()
        assert paid['credit_wei']==str(10**17)
        assert sweeper.status()['outstanding_credit_wei']=='0'
        assert fixture.private.obligations()['paid_count']==1
        assert ledger.get_account(wallet_account_id(RESERVE))['available_credits_display']=='0.1'
    finally:
        fixture.doCleanups()
