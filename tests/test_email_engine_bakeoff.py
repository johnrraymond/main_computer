from __future__ import annotations

from email.parser import BytesParser
from email.policy import default
import ipaddress
import json
import sys

import pytest
from tools import email_engine_bakeoff as lab
from tools import email_engine_bakeoff_probe as probe


def test_fixed_inventory_and_unknown_not_a_pass():
    checks = lab.initial_checks()
    assert len(checks) == 50
    assert sum(weight for weight, _ in lab.GROUPS.values()) == 100
    assert lab.score(checks) == {'earned_points':0.0,'possible_upper_bound':100.0,'coverage':0,'mandatory_gates_pass':False}
    for name in lab.GATES:
        checks[name]['status']='PASS'
    assert lab.score(checks)['mandatory_gates_pass']
    checks[lab.GATES[0]]['status']='FAIL'
    assert not lab.score(checks)['mandatory_gates_pass']
    assert lab.score(checks)['possible_upper_bound'] < 100


@pytest.mark.parametrize('candidate',lab.CANDIDATES)
def test_compose_private_disposable(candidate):
    spec=lab.compose_spec(candidate,'a'*64,10000,600)
    assert spec['networks']['default']['internal'] is True
    assert set(spec['networks']) == {'default'}
    assert not any('ports' in s or 'container_name' in s or 'network_mode' in s or 'privileged' in s for s in spec['services'].values())
    assert 'fixture' in spec['services'] and 'engine' in spec['services']
    for name,service in spec['services'].items():
        for volume in service.get('volumes',[]):
            if isinstance(volume,dict):
                assert name=='probe' and volume['read_only'] and volume['source']==str(lab.PROBE)
            else:
                assert volume.split(':',1)[0] in spec['volumes']
    assert 'frontend' not in spec['services']
    assert 'auth.disabled' not in spec['services']['fixture']['environment']['GREENMAIL_OPTS']


def test_fixture_is_deterministic_and_binary_safe():
    a=probe.fixture_message('alpha',0).as_bytes()
    assert a==probe.fixture_message('alpha',0).as_bytes()
    msg=BytesParser(policy=default).parsebytes(a)
    assert msg['Message-ID']=='<bakeoff-alpha-000000@lab.test>'
    assert next(msg.iter_attachments()).get_payload(decode=True)==probe.ATTACHMENT
    assert probe.fixture_message('beta',0)['Message-ID']!=msg['Message-ID']


def test_unknown_routes_not_certified_as_denied():
    class Client:
        def request(self,*args): return 404,'text/html',b'not found'
    with pytest.raises(AssertionError): probe.denied(Client(),'/api/accounts')


def test_score_failure_and_blocked_distinct():
    checks=lab.initial_checks()
    checks['cold_ready']['status']='PASS'
    checks['recreate_ready']['status']='FAIL'
    checks['headless_auth']['status']='BLOCKED'
    assert lab.score(checks)['coverage']==2
    assert lab.score(checks)['earned_points']==2.5
    assert lab.score(checks)['possible_upper_bound']==97.5


def test_mailflow_requests_require_structured_data(monkeypatch):
    c=probe.Client('mailflow')
    monkeypatch.setattr(c,'request',lambda *args:(200,'text/html',b'<html>login</html>'))
    with pytest.raises(AssertionError): c.obj('/api/accounts')


def test_reports_docker_blocked(monkeypatch,tmp_path):
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    def unavailable(*args,**kwargs): raise lab.CommandError('docker unavailable')
    monkeypatch.setattr(lab,'command',unavailable)
    assert lab.main(['run','--all','--messages','2','--idle-seconds','0'])==2
    import json
    result=json.loads(next(tmp_path.rglob('summary.json')).read_text())
    assert result['status']=='BLOCKED_DOCKER' and result['winner'] is None


def test_gofer_private_ip_and_probe_url_agree():
    spec = lab.compose_spec('gofer', 'a'*64, 100, 600, '10.42.17.0/24')
    network = spec['networks']['default']['ipam']['config'][0]
    engine = spec['services']['engine']
    ip = ipaddress.ip_address(engine['networks']['default']['ipv4_address'])
    assert ip in ipaddress.ip_network(network['subnet'])
    assert ip not in ipaddress.ip_network(network['ip_range'])
    assert engine['environment']['GOFER_BASE_URL'] == f'http://{ip}:8090'
    assert spec['services']['probe']['environment']['BAKEOFF_GOFER_BASE_URL'] == engine['environment']['GOFER_BASE_URL']
    assert engine['environment']['GOFER_AUTH_MODE'] == 'personal'
    assert engine['environment']['GOFER_ALLOW_INSECURE_LAN'] == 'true'
    assert 'ports' not in engine


def test_subnet_skips_existing_networks(monkeypatch):
    monkeypatch.setattr(lab.secrets, 'randbelow', lambda n: 0)
    def docker(args, timeout):
        if args[2] == 'ls': return 'one\ntwo\n'
        return json.dumps([{'IPAM': {'Config': [{'Subnet':'10.0.0.0/16'}]}},
                           {'IPAM': {'Config': [{'Subnet':'fd00::/64'}]}}])
    assert lab.gofer_subnet(docker) == '10.1.0.0/24'


def test_exited_container_never_waits_for_http():
    def compose(*args, **kwargs):
        if '-q' in args: return 'container-id'
        return json.dumps({'State':'exited','ExitCode':1})
    def never(*args, **kwargs): pytest.fail('HTTP probe invoked for dead engine')
    with pytest.raises(lab.CommandError, match='state=exited exit=1'):
        lab.await_ready('gofer', compose, never, timeout=180)


def test_engine_exit_during_http_attempt():
    states = iter(['running','exited'])
    def compose(*args, **kwargs):
        if '-q' in args: return 'container-id'
        return json.dumps([{'State':next(states),'ExitCode':1}])
    with pytest.raises(lab.CommandError, match='state=exited'):
        lab.await_ready('gofer', compose, lambda *a,**k:{'http':200,'scope':'HTTP readiness only'})


def test_readiness_retries_and_requires_real_evidence():
    def compose(*args, **kwargs):
        return 'container-id' if '-q' in args else json.dumps({'State':'running','ExitCode':0})
    attempts = iter([{'http':200,'scope':'not ready'}, {'http':503}, {'http':200,'scope':'HTTP readiness only'}])
    events = []
    observed = lab.await_ready('gofer', compose, lambda *a,**k:next(attempts), interval=0, emit=events.append)
    assert observed['http'] == 200
    assert len([x for x in events if x.startswith('HTTP_PROBE')]) == 3
    assert events[-1]=='READY http=200'


def test_probe_readiness_connection_refusal(monkeypatch):
    from urllib.error import URLError
    def refused(*args, **kwargs): raise URLError('connection refused')
    monkeypatch.setattr(probe.Client, 'request', refused)
    assert probe.ready_once('gofer') == {'http':None,'scope':'not ready'}


def test_probe_readiness_rejects_wrong_health_json(monkeypatch):
    monkeypatch.setattr(probe.Client, 'request', lambda *a,**k:(200,'application/json',b'[]'))
    assert probe.ready_once('mailflow')['scope'] == 'not ready'


def test_process_stdin_closed_and_progress_streamed(capsys):
    script = "import sys,time; assert sys.stdin.read()==''; print('BAKEOFF_PROGRESS seed alpha 1/1',file=sys.stderr,flush=True); time.sleep(.15); print('result')"
    output = lab.command([sys.executable,'-c',script], timeout=5, label='TEST', heartbeat=.05)
    assert output.strip() == 'result'
    console = capsys.readouterr().out
    assert 'BAKEOFF_PROGRESS seed alpha 1/1' in console
    assert 'WAIT elapsed=' in console


def test_log_redaction():
    log = lab.safe_diagnostic('setup token: ABC123\nunsafe HTTP configuration: GOFER_BASE_URL must use https\nopaque=xyz', 'xyz')
    assert 'ABC123' not in log and 'xyz' not in log
    assert 'unsafe HTTP configuration' in log


def test_failed_candidate_does_not_prevent_next_candidate(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    visited=[]
    def fake(args,timeout=600,**kwargs):
        if args[:3] == ['docker','network','ls']: return ''
        if args[:3] == ['docker','image','inspect']:
            return '[]' if '{{json .RepoDigests}}' in args else 'sha256:'+'1'*64
        if '--project-name' in args:
            project=args[args.index('--project-name')+1]
            command=args[args.index('--file')+2:]
            if command[0]=='pull': visited.append(project.split('-')[2])
            if command[0]=='exec': return json.dumps({'status':'PASS','messages':100})
            if command[0]=='ps':
                return 'dead-container' if '-q' in command else json.dumps({'State':'exited','ExitCode':1})
            if command[0]=='logs': return 'unsafe HTTP configuration\nsetup token: DONT_SHOW'
        return ''
    monkeypatch.setattr(lab,'command',fake)
    assert lab.main(['run','--all','--idle-seconds','0']) == 2
    assert visited == list(lab.CANDIDATES)
    summary=json.loads(next(tmp_path.rglob('summary.json')).read_text())
    assert len(summary['candidates'])==3
    for name in lab.CANDIDATES:
        record=json.loads(next(tmp_path.rglob(name+'.json')).read_text())
        assert record['checks']['cold_ready']['status']=='BLOCKED'
        assert record['cleanup']=='PASS'
        assert 'unsafe HTTP configuration' in record['diagnostics']
        assert 'DONT_SHOW' not in json.dumps(record)
    assert 'DONT_SHOW' not in capsys.readouterr().out


def test_gofer_bootstrap_secret_is_disposable_and_not_published():
    spec=lab.compose_spec('gofer','lab-secret',100,600)
    assert spec['services']['engine']['environment']['GOFER_SETUP_TOKEN']=='lab-secret'
    assert spec['networks']['default']['internal'] is True


def test_gofer_control_forms_extract_only_hidden_tokens():
    html=b'<form action="/settings/security/mail/private-target"><input type="hidden" name="csrf_token" value="a&amp;b"><input name="password" value="ignored"></form><form hx-post="/other"><input type="hidden" name="csrf_token" value="other"></form>'
    forms=probe.GoferForms(html)
    assert forms.forms['/settings/security/mail/private-target']=={'csrf_token':'a&b'}
    assert forms.forms['/other']=={'csrf_token':'other'}


def test_gofer_native_ids_reject_incomplete_or_html():
    class C:
        def json(self,path): return {'ids':[{'id':'1','account':'a'}],'total':2,'truncated':True}
    with pytest.raises(AssertionError,match='truncated'): probe.gofer_ids(C())
    class Bad:
        def call(self,path): return 200,{'Content-Type':'text/html'},b'<html>not a message</html>'
    with pytest.raises(AssertionError): probe.gofer_mime(Bad(),{'id':'1'})


def test_gofer_hydration_checks_exact_message_identity_and_account():
    class C:
        def call(self,path):
            u='alpha' if '/1/' in path else 'beta'
            return 200,{'Content-Type':'message/rfc822'},probe.fixture_message(u,0).as_bytes()
    rows=[{'id':'1','account':'a'},{'id':'2','account':'b'}]
    ids={'alpha@lab.test':'a','beta@lab.test':'b'}
    assert len(probe.gofer_hydrate(C(),rows,ids,2))==2
    with pytest.raises(AssertionError,match='wrong account'):
        probe.gofer_hydrate(C(),[{'id':'1','account':'b'},rows[1]],ids,2)
    with pytest.raises(AssertionError,match='duplicate'):
        probe.gofer_hydrate(C(),[rows[0],rows[0]],ids,2)


def test_gofer_anonymous_redirect_is_not_generic_200_or_404():
    class C:
        def __init__(self,code,location=''): self.code,self.location=code,location
        def call(self,path): return self.code,{'Location':self.location},b''
    assert probe.gofer_denied(C(303,'/login?next=x'),'/mail/search/ids')
    for code,location in [(200,''),(404,''),(303,'/other')]:
        with pytest.raises(AssertionError): probe.gofer_denied(C(code,location),'/mail/search/ids')


def test_cypht_uses_html_not_ajax_and_short_200_not_ready(monkeypatch):
    def response(self,path,**kw):
        assert kw['html'] is True and kw['csrf'] is False
        return 200,'text/html',b'Cypht invalid request'
    monkeypatch.setattr(probe.Client,'request',response)
    result=probe.ready_once('cypht')
    assert result['scope']=='not ready' and result['response_bytes']==21
    monkeypatch.setattr(probe.Client,'request',lambda *a,**k:(200,'text/html',b'<html>Cypht '+b'x'*500+b'</html>'))
    assert probe.ready_once('cypht')['scope']=='HTTP readiness only'


def test_live_provider_requirements_explicit_and_not_certified(monkeypatch,tmp_path):
    monkeypatch.setattr(lab,'ROOT',tmp_path)
    def unavailable(*args,**kwargs): raise lab.CommandError('docker unavailable')
    monkeypatch.setattr(lab,'command',unavailable)
    lab.main(['run','--candidate','gofer'])
    data=json.loads(next(tmp_path.rglob('summary.json')).read_text())
    assert data['live_provider_certification']==dict.fromkeys(('generic_tls_imap_smtp','generic_pop3','gmail','outlook','yahoo'),'NOT_RUN')
    assert data['winner'] is None


class FakeGoferEnrollment:
    def __init__(self, *, header='', save=True, preflight='ok'):
        self.header=header
        self.save=save
        self.preflight=preflight
        self.cookies=[]
        self.forms=probe.GoferForms(b'')
        self.created=0
        self.inventory=[{'ID':'local','Email':'','Provider':'local'}]
    def page(self,path): return b''
    def call(self,path):
        assert path=='/api/accounts'
        return 200,{'Content-Type':'application/json'},json.dumps(self.inventory).encode()
    def post(self,path,fields,timeout=30):
        assert fields['email_address']=='alpha@lab.test'
        assert timeout>=120
        if path=='/api/accounts/test':
            return 200,{'Content-Type':'text/html','X-Gofer-Connection-Test':self.preflight},b'<p>IMAP connection refused</p>'
        assert path=='/api/accounts'
        self.created+=1
        if self.save:
            self.inventory.append({'ID':'account-1','Email':'alpha@lab.test','Provider':'imap'})
        return 200,{'Content-Type':'text/html','X-Gofer-Account-ID':self.header},b'<p>Mailbox result</p>'


def test_gofer_enrollment_missing_header_can_recover_from_inventory():
    c=FakeGoferEnrollment()
    assert probe.gofer_create_fixture_account(c,'alpha')=='account-1'
    assert c.created==1


@pytest.mark.parametrize('header,save', [('',False),('account-1',False),('wrong-id',True)])
def test_gofer_enrollment_does_not_accept_http_200_or_header_alone(header,save):
    c=FakeGoferEnrollment(header=header,save=save)
    with pytest.raises(AssertionError,match='account creation not verified'):
        probe.gofer_create_fixture_account(c,'alpha')
    assert c.created==1


def test_gofer_enrollment_transport_failure_is_diagnosed_without_write():
    c=FakeGoferEnrollment(preflight='failed')
    with pytest.raises(AssertionError,match='IMAP connection refused'):
        probe.gofer_create_fixture_account(c,'alpha')
    assert c.created==0


def test_gofer_diagnostic_omits_markup_and_secrets(monkeypatch):
    monkeypatch.setattr(probe,'SECRET','disposable-key')
    c=FakeGoferEnrollment()
    c.forms=probe.GoferForms(b'<form action="/a"><input type="hidden" name="csrf" value="csrf-value"></form>')
    raw=b'<p>Connection refused disposable-key csrf-value</p><script>DO_NOT_LOG</script><input value="HIDDEN">'
    detail=probe.gofer_response_diagnostic(c,200,{'Content-Type':'text/html'},raw)
    assert 'Connection refused' in detail
    assert all(x not in detail for x in ['disposable-key','csrf-value','DO_NOT_LOG','HIDDEN','<p>'])


def test_gofer_inventory_rejects_duplicate_accounts():
    c=FakeGoferEnrollment()
    c.inventory.extend([{'ID':'a','Email':'alpha@lab.test','Provider':'imap'}, {'ID':'b','Email':'alpha@lab.test','Provider':'imap'}])
    with pytest.raises(AssertionError,match='duplicate'):
        probe.gofer_inventory(c)


def test_gofer_post_create_wizard_without_inventory_or_header(monkeypatch):
    class NoInventory(FakeGoferEnrollment):
        def call(self,path): return 405,{'Content-Type':'text/plain'},b'method not allowed'
    c=NoInventory()
    monkeypatch.setattr(probe,'gofer_ids',lambda client:[])
    monkeypatch.setattr(probe,'gofer_account_from_fixture',lambda client,u,previous:'account-1')
    assert probe.gofer_create_fixture_account(c,'alpha')=='account-1'
    assert c.created==1
    c=NoInventory(header='wrong-id')
    with pytest.raises(AssertionError,match='disagrees'):
        probe.gofer_create_fixture_account(c,'alpha')
    assert c.created==1


def test_gofer_wizard_not_treated_as_success_without_native_proof(monkeypatch):
    class NoInventory(FakeGoferEnrollment):
        def call(self,path): return 405,{'Content-Type':'text/plain'},b'method not allowed'
    c=NoInventory()
    monkeypatch.setattr(probe,'gofer_ids',lambda client:[])
    def missing(*args): raise TimeoutError('no message')
    monkeypatch.setattr(probe,'gofer_account_from_fixture',missing)
    with pytest.raises(AssertionError,match='native fixture proof failed'):
        probe.gofer_create_fixture_account(c,'alpha')
    assert c.created==1


def test_gofer_fixture_account_discovery_uses_mime_and_excludes_old_account(monkeypatch):
    rows=[{'id':'1','account':'old'},{'id':'2','account':'new'}]
    monkeypatch.setattr(probe,'gofer_ids',lambda c:rows)
    monkeypatch.setattr(probe,'gofer_mime',lambda c,row:probe.fixture_message('alpha',0))
    assert probe.gofer_account_from_fixture(None,'alpha',{'old'})=='new'
    with pytest.raises(AssertionError,match='Duplicate'):
        probe.gofer_account_from_fixture(None,'alpha',set())


def test_gofer_fixture_account_discovery_rejects_wrong_recipient(monkeypatch):
    monkeypatch.setattr(probe,'gofer_ids',lambda c:[{'id':'2','account':'new'}])
    msg=probe.fixture_message('alpha',0)
    msg.replace_header('To','beta@lab.test')
    monkeypatch.setattr(probe,'gofer_mime',lambda c,row:msg)
    with pytest.raises(AssertionError,match='incorrect recipient'):
        probe.gofer_account_from_fixture(None,'alpha',set())


def test_gofer_fixture_tls_configuration_and_certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID
    spec = lab.compose_spec('gofer', 'a'*64, 100, 600)
    info = lab.configure_gofer_tls(spec, tmp_path, 'a'*64)
    services = spec['services']
    assert info['mode'] == 'implicit_tls'
    assert (info['imap_port'], info['smtp_port']) == (3993, 3465)
    assert services['engine']['environment']['SSL_CERT_FILE'] == '/tmp/bakeoff-fixture-ca.pem'
    assert services['probe']['environment']['BAKEOFF_FIXTURE_TLS'] == '1'
    assert 'greenmail.tls.keystore.file=/tmp/bakeoff-fixture.p12' in services['fixture']['environment']['GREENMAIL_OPTS']
    ca = x509.load_pem_x509_certificate((tmp_path/'fixture-ca.pem').read_bytes())
    key, leaf, chain = pkcs12.load_key_and_certificates((tmp_path/'fixture.p12').read_bytes(), b'a'*64)
    assert key is not None and chain == [ca]
    leaf.verify_directly_issued_by(ca)
    assert leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName) == ['fixture']
    assert ExtendedKeyUsageOID.SERVER_AUTH in leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert set(p.name for p in tmp_path.iterdir()) == {'fixture-ca.pem','fixture.p12'}
    for name in ('engine','probe'):
        assert all(not isinstance(m,dict) or not m['source'].endswith('.p12') for m in services[name]['volumes'])
    assert all(m['read_only'] for m in services['fixture']['volumes'])
    assert not any('ports' in s for s in services.values())


def test_gofer_native_enrollment_requires_tls_fields():
    class TLSOnly(FakeGoferEnrollment):
        def post(self,path,fields,timeout=30):
            assert (fields['imap_port'],fields['imap_tls_mode']) == (3993,'tls')
            assert (fields['smtp_port'],fields['smtp_tls_mode']) == (3465,'tls')
            return super().post(path,fields,timeout)
    assert probe.gofer_create_fixture_account(TLSOnly(),'alpha') == 'account-1'


def test_gofer_enrollment_approves_only_secure_private_endpoints(monkeypatch):
    calls=[]
    class C:
        def page(self,path):
            self.forms=probe.GoferForms(b'<form action="/settings/security/mail/private-target"></form>')
        def post(self,path,fields):
            calls.append((path,fields['protocol'],fields['port']))
            return 303,{'Location':'/settings/security?mail_notice=ok'},b''
    monkeypatch.setattr(probe,'gofer_create_fixture_account',lambda c,u:u)
    assert probe.gofer_enroll(C()) == {'alpha@lab.test':'alpha','beta@lab.test':'beta'}
    assert calls == [('/settings/security/mail/private-target','imap',3993),('/settings/security/mail/private-target','smtp',3465)]


def test_fixture_secure_oracle_uses_verified_tls(tmp_path,monkeypatch):
    import ssl
    lab.configure_gofer_tls(lab.compose_spec('gofer','a'*64,100,600),tmp_path,'a'*64)
    monkeypatch.setattr(probe,'FIXTURE_CA',str(tmp_path/'fixture-ca.pem'))
    monkeypatch.setattr(probe,'FIXTURE_TLS',True)
    calls=[]
    def imap(host,port,**kw):
        calls.append((host,port,kw['ssl_context']))
    def smtp(host,port,**kw):
        calls.append((host,port,kw['context']))
    monkeypatch.setattr(probe.imaplib,'IMAP4_SSL',imap)
    monkeypatch.setattr(probe.smtplib,'SMTP_SSL',smtp)
    probe.fixture_imap()
    probe.fixture_smtp()
    assert [(h,p) for h,p,_ in calls] == [('fixture',3993),('fixture',3465)]
    for _,_,ctx in calls:
        assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.cert_store_stats()['x509_ca'] == 1


def test_gofer_recovery_uses_fresh_scopes_and_stable_mime_ids(monkeypatch):
    ids={'alpha@lab.test':'a','beta@lab.test':'b'}
    calls=[]
    def listing(c,**params):
        aid=params['scope_account_id']; calls.append(aid)
        return [{'account':aid,'id':'91' if aid=='a' else '92'}]
    monkeypatch.setattr(probe,'gofer_ids',listing)
    monkeypatch.setattr(probe,'gofer_mime',lambda c,row:probe.fixture_message('alpha' if row['account']=='a' else 'beta',0))
    observed=probe.gofer_recovery_observation(None,ids,2)
    assert calls==['a','b'] and observed['found']==2 and not observed['missing']


def test_gofer_recovery_waits_for_originals(monkeypatch):
    partial={'expected':2,'found':1,'missing':['<bakeoff-alpha-000000@lab.test>'],'duplicates':[],'wrong_account':[],'unavailable_sources':0}
    full={**partial,'found':2,'missing':[]}
    snapshots=iter([partial,full]); r=probe.Results()
    monkeypatch.setattr(probe,'gofer_recovery_observation',lambda *a:next(snapshots))
    monkeypatch.setattr(probe.time,'sleep',lambda *a:None)
    assert 'All 2' in probe.gofer_recover_messages(None,{},2,r,timeout=10)
    assert r.metrics['mailbox_recovery']['attempts']==2


@pytest.mark.parametrize('present',[True,False])
def test_gofer_recovery_deadline_checks_independent_oracle(monkeypatch,present):
    mid='<bakeoff-alpha-000000@lab.test>'
    partial={'expected':2,'found':1,'missing':[mid],'duplicates':[],'wrong_account':[],'unavailable_sources':0}
    r=probe.Results()
    monkeypatch.setattr(probe,'gofer_recovery_observation',lambda *a:partial)
    monkeypatch.setattr(probe,'fixture_find',lambda *a:[probe.fixture_message('alpha',0)] if present else [])
    if present:
        with pytest.raises(AssertionError,match='independent fixture confirms'):
            probe.gofer_recover_messages(None,{},2,r,timeout=0)
    else:
        assert probe.gofer_recover_messages(None,{},2,r,timeout=0) is None
        assert r.checks['messages_recreate']['status']=='BLOCKED'
    assert r.metrics['mailbox_recovery']['oracle_missing_example']['present_once']==present


def test_gofer_recovery_inventory_unavailable_is_not_loss(monkeypatch):
    def unavailable(*a): raise AssertionError('native route unavailable')
    monkeypatch.setattr(probe,'gofer_recovery_observation',unavailable)
    r=probe.Results()
    assert probe.gofer_recover_messages(None,{},2,r,timeout=0) is None
    assert r.checks['messages_recreate']['status']=='BLOCKED'


def test_gofer_recovery_rejects_duplicate_originals(monkeypatch):
    partial={'expected':2,'found':2,'missing':[],'duplicates':['one'],'wrong_account':[],'unavailable_sources':0}
    monkeypatch.setattr(probe,'gofer_recovery_observation',lambda *a:partial)
    with pytest.raises(AssertionError,match='duplicate original IDs'):
        probe.gofer_recover_messages(None,{},2,probe.Results(),timeout=0)


def test_mailflow_uses_same_ephemeral_trust_and_transport(tmp_path):
    spec=lab.compose_spec('mailflow','a'*64,100,600)
    info=lab.configure_gofer_tls(spec,tmp_path,'a'*64)
    assert info['mode']=='implicit_tls'
    env=spec['services']['engine']['environment']
    assert env['NODE_EXTRA_CA_CERTS']=='/tmp/bakeoff-fixture-ca.pem'
    assert 'NODE_TLS_REJECT_UNAUTHORIZED' not in env
    assert spec['services']['probe']['environment']['BAKEOFF_FIXTURE_TLS']=='1'
    assert all(not isinstance(m,dict) or not m['source'].endswith('.p12') for m in spec['services']['engine']['volumes'])


def test_mailflow_tls_readback_refuses_insecure_or_plaintext():
    data={'imap_host':'fixture','smtp_host':'fixture','imap_port':3993,'smtp_port':3465,
          'imap_tls':True,'imap_skip_tls_verify':False,'smtp_tls':'SSL'}
    probe.verify_mailflow_tls_account(data)
    for key,value in [('imap_tls',False),('imap_skip_tls_verify',True),('smtp_tls','none'),('smtp_port',3025)]:
        with pytest.raises(AssertionError): probe.verify_mailflow_tls_account({**data,key:value})


def test_gofer_grouping_setting_is_native_and_verified():
    class C:
        value=None
        def page(self,path): assert path=='/'
        def call(self,path,method,*,json_data):
            assert path=='/api/settings/ui' and method=='PATCH'
            self.value=json_data['group_conversations']
            return 200,{},b'{"ok":true}'
        def json(self,path):
            assert path=='/api/settings/ui'
            return {'group_conversations':self.value}
    c=C()
    assert probe.gofer_set_grouping(c,True)=='true'
    assert probe.gofer_set_grouping(c,False)=='false'
    c.json=lambda path:{'group_conversations':'true'}
    with pytest.raises(AssertionError,match='readback'): probe.gofer_set_grouping(c,False)


def test_mailflow_recovery_uses_all_pages_and_rfc822_ids(monkeypatch):
    ids={'alpha@lab.test':'a','beta@lab.test':'b'}
    calls=[]
    def listing(c,accountId,limit,offset):
        calls.append((accountId,offset))
        return {'total':2,'messages':[{'account_id':accountId,'id':f'{accountId}{offset}'}]}
    monkeypatch.setattr(probe,'messages',listing)
    monkeypatch.setattr(probe,'mailflow_mime',lambda c,row:probe.fixture_message('alpha' if row['account_id']=='a' else 'beta',int(row['id'][1:])))
    obs=probe.mailflow_recovery_observation(None,ids,4)
    assert probe.observation_complete(obs)
    assert calls==[('a',0),('a',1),('b',0),('b',1)]


def test_mailflow_repeated_page_is_refused(monkeypatch):
    monkeypatch.setattr(probe,'messages',lambda *a,**kw:{'total':2,'messages':[{'id':'same'}]})
    with pytest.raises(AssertionError,match='repeated'):list(probe.mailflow_rows(None,'a'))


def test_recovery_requires_pre_restart_baseline(tmp_path,monkeypatch):
    state=tmp_path/'state.json';state.write_text('{"pre_recreate_verified":false}')
    monkeypatch.setattr(probe,'STATE_FILE',state)
    def observer(*a): pytest.fail('must not certify post-only recovery')
    r=probe.Results()
    probe.run_recovery_check(None,{},r,observer,'mailflow')
    assert r.checks['messages_recreate']['status']=='BLOCKED'


def test_pre_restart_observation_saved_to_state(tmp_path,monkeypatch):
    state=tmp_path/'state.json';state.write_text('{"ids":{}}')
    monkeypatch.setattr(probe,'STATE_FILE',state)
    full={'expected':2,'found':2,'missing':[],'duplicates':[],'wrong_account':[],'unavailable_sources':0}
    r=probe.Results();probe.record_pre_recreate(None,{},r,lambda *a:full)
    assert json.loads(state.read_text())['pre_recreate_verified'] is True
    assert r.metrics['pre_recreate_mailbox']['verified'] is True


def test_recovery_progress_actually_streamed(capsys):
    line='BAKEOFF_PROGRESS mailflow recovery attempt=1 originals=99/100 duplicates=0 wrong_account=0 unavailable_sources=0'
    lab.command([sys.executable,'-c',f'import sys; print({line!r},file=sys.stderr)'],10,label='test')
    assert line in capsys.readouterr().out


def test_parity_needs_both_full_corpora_and_never_certifies_live_providers():
    results={}
    for c in ('gofer','mailflow'):
        checks=lab.initial_checks()
        for name in lab.PARITY_CHECKS: checks[name]['status']='PASS'
        results[c]={'checks':checks,'fixture':{'status':'PASS','messages':100,'transport':'implicit_tls'},'metrics':{'native_tls_account_readback':'PASS','fixture_transport':{'mode':'implicit_tls','imap_port':3993,'smtp_port':3465},'pre_recreate_mailbox':{'verified':True}}}
    result=lab.compare_finalists(results,100)
    assert result['status']=='LAB_PARITY_PASS'
    assert result['additional_security_parity']=='INCOMPLETE' and not result['production_certification']
    assert result['provider_certification']=='NOT_RUN'
    results['gofer']['checks']['messages_recreate']['status']='FAIL'
    assert lab.compare_finalists(results,100)['status']=='LAB_PARITY_FAIL'
    results['gofer']['checks']['messages_recreate']['status']='PASS'
    results['mailflow']['metrics']['pre_recreate_mailbox']['verified']=False
    assert lab.compare_finalists(results,100)['status']=='LAB_PARITY_INCOMPLETE'


def test_configured_resources_sum_database_and_engine():
    m={'configured_idle':{'includes_database':True,'window_seconds':15,'samples':[[{'MemUsage':'42 MiB / 1 GiB','CPUPerc':'1.2%'},{'MemUsage':'10 MiB / 1 GiB','CPUPerc':'0.3%'}]]}}
    data=lab.resource_summary(m)
    assert data['mean_total_memory_mib']==52 and data['mean_total_cpu_percent']==1.5
    m['configured_idle']['samples'][0].pop()
    assert lab.resource_summary(m) is None
