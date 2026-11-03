"""Regression: no invented organization identity is persisted or accepted."""
import base64
import json
from pathlib import Path
import sqlite3
import tempfile

import pytest

from main_computer.slh.authority import SLHAuthority, SLHError
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.local_service import create_server
from main_computer.slh.seh_autoconnect import read_seh_settings
from main_computer.slh.private_requests import PrivateRequests
from main_computer.slh.pinky_sweep import PinkySweep
from main_computer.slh.organization_wallet import OrganizationWallet


def _columns(db, table):
    return {r[1] for r in db.execute(f'PRAGMA table_info({table})')}


def test_fresh_sl_server_no_extra_organization_identity(tmp_path):
    root = tmp_path / 'server'
    server = create_server(state_dir=root, port=0)
    try:
        auth = server.organization_controller.authority
        signer, pub = generate_identity()
        statement = {'version': 1, 'action': 'slh.admin.bootstrap', 'admin_id': 'owner',
                     'device_id': 'admin-device', 'public_key': pub}
        auth.bootstrap({**statement, 'admin_name': 'Owner', 'proof': sign_object(signer, statement),
                        'bootstrap_secret': (root/'bootstrap.secret').read_text()}, loopback=True)
        status = auth.status()
        assert status['initialized'] and len(status['service_fingerprint']) == 64
        assert not {'organization_id','organization_name','org_id','org_name'} & set(status)
        with sqlite3.connect(root/'organization.sqlite3') as db:
            assert 'organization' not in {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert 'org_id' not in _columns(db, 'bootstrap_transactions')
        assert (root/'seh-service-signing-key.pem').read_bytes().startswith(b'-----BEGIN PRIVATE KEY')
    finally:
        server.server_close()


def test_legacy_connection_metadata_is_removed_without_changing_routing(tmp_path):
    existing = {'enabled': True, 'seh_url': 'wss://external.example.org',
                'slh_id': 'slh-one', 'organization_id':'legacy-label',
                'allow_loopback_lab': False, 'retry_seconds': 2.0}
    file = tmp_path/'seh-connection.json'
    file.write_text(json.dumps(existing))
    result = read_seh_settings(tmp_path)
    assert result.slh_id == 'slh-one' and result.seh_url == 'wss://external.example.org'
    assert 'organization_id' not in json.loads(file.read_text())


def test_pending_legacy_sweep_refuses_rekey(tmp_path):
    dbfile=tmp_path/'private_requests.sqlite3'
    with sqlite3.connect(dbfile) as db:
        db.execute('CREATE TABLE pinky_sweeps (sweep_id TEXT PRIMARY KEY, org_id TEXT, status TEXT)')
        db.execute("INSERT INTO pinky_sweeps VALUES ('s1','old-label','PENDING')")
    class Private:
        import threading
        _lock=threading.RLock()
        _db_path=dbfile
        def _db(self):
            conn=sqlite3.connect(self._db_path)
            conn.row_factory=sqlite3.Row
            return conn
    class Wallet: pass
    with pytest.raises(SLHError, match='LEGACY_PENDING_SWEEP_REQUIRES_RECONCILIATION'):
        PinkySweep(Private(), Wallet())
    with sqlite3.connect(dbfile) as db:
        assert 'org_id' in _columns(db, 'pinky_sweeps')
        assert db.execute('SELECT status FROM pinky_sweeps').fetchone()[0] == 'PENDING'


def test_legacy_confirmed_ledger_migrates_without_rebilling(tmp_path):
    """Paid records retain old receipt, sweep and obligation IDs after removal."""
    dbfile = tmp_path / 'private_requests.sqlite3'
    old_obligation = 'pinky:v1:paid-obligation-preserve-exactly'
    with sqlite3.connect(dbfile) as db:
        db.executescript("""
        CREATE TABLE private_requests (
            org_id TEXT NOT NULL, requester_device_id TEXT NOT NULL,
            request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
            model TEXT NOT NULL, status TEXT NOT NULL,
            worker_device_id TEXT, run_id TEXT, attempt INTEGER NOT NULL DEFAULT 0,
            admitted_at_ns INTEGER NOT NULL, updated_at_ns INTEGER NOT NULL,
            fulfilled_at_ns INTEGER, result_hash TEXT, failure_code TEXT,
            billing_policy_id TEXT NOT NULL DEFAULT 'p2-pre-assessment',
            pinky_policy_revision INTEGER NOT NULL DEFAULT 0,
            pinky_enabled INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(org_id,requester_device_id,request_id));
        CREATE TABLE pinky_policy (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), org_id TEXT NOT NULL,
            revision INTEGER NOT NULL, enabled INTEGER NOT NULL,
            changed_at_ns INTEGER NOT NULL, changed_by TEXT NOT NULL);
        CREATE TABLE pinky_policy_history (
            org_id TEXT NOT NULL, revision INTEGER NOT NULL,
            enabled INTEGER NOT NULL, changed_at_ns INTEGER NOT NULL,
            changed_by TEXT NOT NULL, PRIMARY KEY(org_id,revision));
        CREATE TABLE pinky_obligations (
            obligation_id TEXT PRIMARY KEY, org_id TEXT NOT NULL,
            requester_device_id TEXT NOT NULL, request_id TEXT NOT NULL,
            run_id TEXT NOT NULL, model TEXT NOT NULL,
            result_hash TEXT NOT NULL, policy_id TEXT NOT NULL,
            policy_revision INTEGER NOT NULL, reference_credit_wei TEXT NOT NULL,
            amount_credit_wei TEXT NOT NULL, status TEXT NOT NULL,
            assessed_at_ns INTEGER NOT NULL,
            UNIQUE(org_id,requester_device_id,request_id));
        CREATE TABLE pinky_sweeps (
            sweep_id TEXT PRIMARY KEY, org_id TEXT NOT NULL,
            wallet_address TEXT NOT NULL, reserve_wallet_address TEXT NOT NULL,
            cluster_id TEXT NOT NULL, chain_id TEXT NOT NULL,
            credit_wei TEXT NOT NULL, status TEXT NOT NULL,
            created_at_ns INTEGER NOT NULL, confirmed_at_ns INTEGER, receipt_id TEXT);
        CREATE TABLE pinky_sweep_items (
            obligation_id TEXT PRIMARY KEY REFERENCES pinky_obligations(obligation_id),
            sweep_id TEXT NOT NULL REFERENCES pinky_sweeps(sweep_id));
        """)
        db.execute("INSERT INTO private_requests (org_id,requester_device_id,request_id,request_digest,model,status,run_id,admitted_at_ns,updated_at_ns,fulfilled_at_ns,billing_policy_id,pinky_policy_revision,pinky_enabled) VALUES ('old','requester','r1','digest','model','SUCCEEDED','run1',1,2,3,'fixed_per_request_v1',1,1)")
        db.execute("INSERT INTO pinky_policy VALUES(1,'old',1,1,1,'owner')")
        db.execute("INSERT INTO pinky_policy_history VALUES('old',1,1,1,'owner')")
        db.execute("INSERT INTO pinky_obligations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (old_obligation,'old','requester','r1','run1','model','f'*64,
                    'fixed_per_request_v1',1,'1000000000000000000','100000000000000000','ASSESSED',5))
        db.execute("INSERT INTO pinky_sweeps VALUES ('sweep1','old','wallet','reserve','cluster','42424240','100000000000000000','CONFIRMED',2,9,'receipt1')")
        db.execute("INSERT INTO pinky_sweep_items VALUES(?,?)",(old_obligation,'sweep1'))
    class Owner:
        def devices(self): return []
    ledger = PrivateRequests(tmp_path, Owner(), object())
    assert ledger.obligations()['count'] == 1
    class Private:
        import threading
        _lock=threading.RLock()
        def _db(self): return ledger._db()
    class Wallet: pass
    sweeper=PinkySweep(Private(),Wallet())
    with sqlite3.connect(dbfile) as db:
        for table in ('private_requests','pinky_policy','pinky_policy_history','pinky_obligations','pinky_sweeps'):
            assert 'org_id' not in _columns(db,table)
        assert db.execute('SELECT obligation_id FROM pinky_obligations').fetchone()[0] == old_obligation
        assert db.execute('SELECT receipt_id FROM pinky_sweeps').fetchone()[0] == 'receipt1'
        assert db.execute('SELECT sweep_id FROM pinky_sweep_items').fetchone()[0] == 'sweep1'
        assert db.execute('SELECT COUNT(*) FROM pinky_obligations').fetchone()[0] == 1


def test_legacy_initialized_authority_removes_organization_row_and_preserves_admin(tmp_path):
    state = tmp_path/'slh'
    state.mkdir()
    with sqlite3.connect(state/'organization.sqlite3') as db:
        db.execute('CREATE TABLE organization (id TEXT PRIMARY KEY, name TEXT, initialized_at INTEGER)')
        db.execute("INSERT INTO organization VALUES('old-id','old-name',123)")
        db.execute('CREATE TABLE users(id TEXT PRIMARY KEY,name TEXT NOT NULL,role TEXT NOT NULL,status TEXT NOT NULL)')
        db.execute("INSERT INTO users VALUES('owner','Owner','admin','active')")
    authority = SLHAuthority(state)
    assert authority.initialized()
    with sqlite3.connect(state/'organization.sqlite3') as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'organization' not in tables
        assert db.execute('SELECT initialized_at FROM slh_setup').fetchone()[0] == 123
        assert db.execute('SELECT id FROM users').fetchone()[0] == 'owner'


def test_legacy_owned_coolify_app_removes_remote_identifier_without_recreating(tmp_path, monkeypatch):
    """Only verified legacy ownership authorizes remote identifier deletion."""
    from tools.seh_control import identity as identity
    from tools.seh_control import git_deployment as git
    from tests.test_seh_git_remote_deployment import setup, FakeHost
    from tests.test_seh_operator_trust_binding import public_b64
    root, control = setup(tmp_path)
    def migrate_to_old(doc, gen, base):
        rec = identity.seh_doc(doc,'mainnet','company')
        rec['schema'] = 'main-computer.seh-identity.v1'
        rec['organization_id'] = 'old-id'
        rec['deployment']['application_uuid'] = 'app-one'
    identity.mutate(root,'mainnet',None,'fixture-old-record',migrate_to_old)
    public = public_b64()
    host = FakeHost()
    host.apps.append({'uuid':'app-one','name':'mc-seh-company',
                      'description':'main-computer-seh:git-v1 target=company org=old-id slh=slh',
                      'domains':'https://seh.example.org','project_uuid':'proj-a',
                      'server_uuid':'server-a','environment_name':'mainnet-hubs'})
    host.envs['app-one'] = [
        {'uuid':'old-env','key':'SEH_ORGANIZATION_ID','value':'old-id'},
        {'key':'SEH_SLH_ID','value':'slh'},
        {'key':'SEH_SLH_PUBLIC_KEY_B64','value':public},
    ]
    monkeypatch.setattr(git.hub_coolify,'trigger_deploy',lambda *a,**k:{'ok':True})
    result = git.bind_slh(root,'mainnet','company',slh_id='slh',public_key_b64=public,
                          client=host,execute=True)
    assert result['status'] == 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED'
    assert ('DELETE','/api/v1/applications/app-one/envs/old-env') in host.calls
    assert host.apps[0]['uuid']=='app-one'
    assert host.apps[0]['description']=='main-computer-seh:git-v3 target=company slh=slh'
    assert {r['key'] for r in host.envs['app-one']} == {'SEH_SLH_ID','SEH_SLH_PUBLIC_KEY_B64'}
    _,_,doc=identity.read(root,'mainnet')
    rec=identity.seh_doc(doc,'mainnet','company')
    assert rec['schema']=='main-computer.seh-identity.v3'
    assert rec['slh_id']=='slh' and rec['deployment']['application_uuid']=='app-one'
    assert 'organization_id' not in rec
