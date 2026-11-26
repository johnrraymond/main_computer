"""Runs INSIDE the isolated bakeoff network. Never reads real mail or host secrets."""
from __future__ import annotations

import email.policy
import hashlib
import http.cookiejar
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler

USERS = ('alpha', 'beta')
FIXTURE = 'fixture'
FIXTURE_TLS = os.environ.get('BAKEOFF_FIXTURE_TLS') == '1'
FIXTURE_CA = '/tmp/bakeoff-fixture-ca.pem'
GOFER_IMAP_PORT = 3993
GOFER_SMTP_PORT = 3465
SECRET = os.environ.get('BAKEOFF_SECRET', '')
COUNT = int(os.environ.get('BAKEOFF_MESSAGES', '10000'))
TIMEOUT = int(os.environ.get('BAKEOFF_SYNC_TIMEOUT', '600'))
ATTACHMENT = b'Main Computer email bakeoff\x00\x01\xff\n'
COOKIE_FILE = '/tmp/bakeoff.cookies'
STATE_FILE = Path('/tmp/bakeoff-state.json')
BASES = {'gofer': os.environ.get('BAKEOFF_GOFER_BASE_URL', 'http://engine:8090'), 'mailflow': 'http://engine:3000', 'cypht': 'http://engine:80'}


def fixture_message(user, index):
    msg = EmailMessage(policy=email.policy.SMTP)
    marker = f'bakeoff-{user}-{index:06d}'
    msg['From'] = 'sender@lab.test'
    msg['To'] = user+'@lab.test'
    msg['Subject'] = f'commonmarker {marker}'
    msg['Message-ID'] = f'<{marker}@lab.test>'
    # Deterministic corpus: fixed date, unique IDs, body and binary attachment.
    msg['Date'] = formatdate(1700000000+index, usegmt=True)
    msg.set_content(f'bodymarker {marker}')
    if index == 0:
        msg.add_attachment(ATTACHMENT, maintype='application', subtype='octet-stream', filename='fixture.bin')
        msg.set_boundary('bakeoff-fixed-'+user)
    return msg


def fixture_tls_context():
    # Use only the ephemeral fixture CA; hostname and chain checks stay enabled.
    return ssl.create_default_context(cafile=FIXTURE_CA)


def fixture_imap():
    if FIXTURE_TLS:
        return imaplib.IMAP4_SSL(FIXTURE, GOFER_IMAP_PORT, ssl_context=fixture_tls_context(), timeout=15)
    return imaplib.IMAP4(FIXTURE, 3143, timeout=15)


def fixture_smtp():
    if FIXTURE_TLS:
        return smtplib.SMTP_SSL(FIXTURE, GOFER_SMTP_PORT, context=fixture_tls_context(), timeout=15)
    return smtplib.SMTP(FIXTURE, 3025, timeout=15)


def mailbox(user):
    c = fixture_imap()
    try:
        c.login(user, SECRET)
        return c
    except Exception:
        c.shutdown()
        raise


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


def eventually(fn, timeout=TIMEOUT, interval=5):
    deadline = time.monotonic()+timeout
    while True:
        try:
            value = fn()
            if value:
                return value
        except (URLError, OSError, imaplib.IMAP4.error):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError('Expected observation not obtained within deadline')
        time.sleep(min(interval, max(0, deadline-time.monotonic())))


def progress(message):
    print(f'BAKEOFF_PROGRESS {message}', file=sys.stderr, flush=True)


def seed():
    started = time.monotonic()
    for user in USERS:
        progress(f'seed waiting {user}')
        c = eventually(lambda: mailbox(user), timeout=120, interval=2)
        try:
            status, data = c.select('INBOX')
            expect(status == 'OK' and int(data[0]) == 0, 'Fixture mailbox not empty')
            # Common folders for provider tests; IMAP server remains independent oracle.
            for folder in ('Sent', 'Drafts', 'Trash', 'Archive'):
                c.create(folder)
            for i in range(COUNT//2):
                status, _ = c.append('INBOX', None, None, fixture_message(user,i).as_bytes())
                expect(status == 'OK', 'Fixture append failed')
                if (i+1) % 100 == 0 or i+1 == COUNT//2:
                    progress(f'seed {user} {i+1}/{COUNT//2}')
            status, data = c.select('INBOX', readonly=True)
            expect(status == 'OK' and int(data[0]) == COUNT//2, 'Incorrect fixture count')
        finally:
            c.logout()
    # A negative control: ensure the fixture itself is not accepting arbitrary passwords.
    with fixture_imap() as c:
        try:
            c.login('alpha','definitely-wrong-password')
        except imaplib.IMAP4.error:
            pass
        else:
            raise AssertionError('Fixture accepted an invalid password')
    # Establish both secure endpoints before starting the engine.
    if FIXTURE_TLS:
        with fixture_smtp() as smtp:
            code, _ = smtp.ehlo()
            expect(code == 250, f'Fixture SMTP EHLO HTTP-independent status {code}')
            expect(smtp.sock.version() is not None, 'Fixture SMTP TLS not negotiated')
    return {'status':'PASS','accounts':2,'messages':COUNT,'seconds':round(time.monotonic()-started,3),
            'transport':'implicit_tls' if FIXTURE_TLS else 'plaintext',
            'attachment_sha256':hashlib.sha256(ATTACHMENT).hexdigest()}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, candidate, cookies=None):
        self.base = BASES[candidate]
        self.cookies = cookies if cookies is not None else http.cookiejar.CookieJar()
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(self.cookies))

    def request(self, path, method='GET', data=None, csrf=True, timeout=60, html=False):
        expect(path.startswith('/') and not path.startswith('//'), 'Invalid internal path')
        headers = {'Accept':'text/html' if html else 'application/json', 'Origin':self.base}
        if csrf and not html:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        raw = None
        if data is not None:
            raw = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        req = Request(self.base+path, data=raw, headers=headers, method=method)
        try:
            response = self.opener.open(req,timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            payload = response.read(16*1024*1024+1)
            expect(len(payload) <= 16*1024*1024, 'Response exceeded test cap')
            return response.code, response.headers.get('Content-Type',''), payload

    def obj(self,path,method='GET',data=None):
        status,ctype,raw = self.request(path,method,data)
        expect(200 <= status < 300, f'{method} {path.split("?")[0]} returned HTTP {status}')
        expect('json' in ctype.lower(), f'{path.split("?")[0]} returned non-JSON')
        return json.loads(raw)


def ready_once(candidate):
    client = Client(candidate)
    path = {'gofer':'/healthz','mailflow':'/api/health','cypht':'/'}[candidate]
    try:
        code,ctype,raw = client.request(path, timeout=5, html=True, csrf=False) if candidate == 'cypht' else client.request(path, timeout=5)
    except (URLError, OSError):
        return {'http': None, 'scope': 'not ready'}
    observed = {'path':path,'http':code,'content_type':ctype,'scope':'not ready'}
    if code != 200:
        return observed
    if candidate == 'mailflow':
        try:
            if json.loads(raw).get('status') != 'ok':
                return observed
        except (ValueError, AttributeError):
            return observed
    if candidate == 'cypht':
        observed['response_bytes'] = len(raw)
        observed['response_sha256'] = hashlib.sha256(raw).hexdigest()
        if b'cypht' not in raw.lower() or 'html' not in ctype.lower() or len(raw) < 256:
            observed['diagnostic'] = 'Expected Cypht HTML page; short or unexpected response'
            return observed
    observed['scope'] = 'HTTP readiness only'
    return observed


def ready(candidate):
    def check():
        observed = ready_once(candidate)
        return observed if observed['scope'] == 'HTTP readiness only' else None
    return eventually(check,timeout=180,interval=2)


class Results:
    def __init__(self):
        self.checks = {}
        self.metrics = {}
    def mark(self, key, status, evidence):
        self.checks[key] = {'status':status,'evidence':str(evidence).replace(SECRET,'[REDACTED]') if SECRET else str(evidence)}
    def test(self,key,fn):
        progress(f'test {key} BEGIN')
        try:
            value = fn()
        except (AssertionError, TimeoutError, HTTPError, URLError, OSError, ValueError, KeyError, TypeError, imaplib.IMAP4.error, smtplib.SMTPException) as exc:
            self.mark(key,'FAIL',f'{type(exc).__name__}: {exc}')
            progress(f'test {key} FAIL')
            return None
        progress(f'test {key} PASS')
        self.mark(key,'PASS',value if isinstance(value,(str,int,float)) else 'Observed expected result')
        return value if value is not None else True
    def report(self):
        return {'checks':self.checks,'metrics':self.metrics}


def denied(client,path,method='GET',data=None,csrf=True,allowed=(401,403)):
    code,_,_ = client.request(path,method,data,csrf)
    expect(code in allowed,f'Expected authorization refusal, got {code}')
    return f'HTTP {code}'


def messages(client, **kwargs):
    data = client.obj('/api/mail/messages?'+urlencode({'threaded':'false',**kwargs}))
    expect(isinstance(data.get('messages'),list), 'Missing messages array')
    return data


def search(client,q):
    data = client.obj('/api/search?'+urlencode({'q':q,'limit':200}))
    expect(isinstance(data.get('messages'),list), 'Missing search messages array')
    return data['messages']


def marker_row(client,user,index):
    marker = f'bakeoff-{user}-{index:06d}'
    rows = search(client, 'subject:'+marker)
    return next((row for row in rows if row.get('subject') == 'commonmarker '+marker),None)


def fixture_flags(user,mid):
    with mailbox(user) as c:
        c.select('INBOX',readonly=True)
        status,data = c.search(None,'HEADER','Message-ID',f'"{mid}"')
        expect(status=='OK' and len(data[0].split())==1,'Fixture message not uniquely identified')
        status,data = c.fetch(data[0], '(FLAGS)')
        expect(status=='OK','Fixture flag read failed')
        return data[0]


def flag_test(client,row,user,kind,value):
    prop = 'read' if kind=='read' else 'starred'
    flag = b'\\Seen' if kind=='read' else b'\\Flagged'
    client.obj(f'/api/mail/messages/{row["id"]}/{kind}','PATCH',{prop:value})
    eventually(lambda: (flag in fixture_flags(user,row['message_id'])) == value,timeout=45,interval=2)
    detail = client.obj('/api/mail/messages/'+row['id'])
    expect(detail.get('is_'+prop) is value, 'Flag API and fixture disagree')
    return 'Structured detail and independent IMAP flags agree'


def fixture_find(user,folder,field,value):
    with mailbox(user) as c:
        status,_=c.select(folder,readonly=True)
        if status!='OK': return []
        status,data=c.search(None,'HEADER',field,f'"{value}"')
        if status!='OK': return []
        found=[]
        for number in data[0].split():
            status,payload=c.fetch(number,'(BODY.PEEK[])')
            if status=='OK':
                for part in payload:
                    if isinstance(part,tuple):
                        found.append(BytesParser(policy=email.policy.default).parsebytes(part[1]))
        return found


def inject(user,index):
    msg=fixture_message(user,index)
    with fixture_smtp() as smtp:
        smtp.send_message(msg)
    # Confirm independent server delivery before testing engine discovery.
    with mailbox(user) as c:
        c.select('INBOX',readonly=True)
        status, data = c.search(None,'HEADER','Message-ID',f'"{msg["Message-ID"]}"')
        expect(status=='OK' and len(data[0].split())==1,'Fixture SMTP delivery not verified')


def login(client, register=False):
    data = client.obj('/api/auth/'+('register' if register else 'login'),'POST',{'username':'bakeoff','password':SECRET})
    expect(data.get('user',{}).get('username')=='bakeoff','Unexpected user')
    expect(len(client.cookies)>0,'No session cookie issued')
    return 'Session issued to disposable user via JSON API'


def account_ids(client):
    data = client.obj('/api/accounts')
    expect(isinstance(data,list),'Account inventory not an array')
    return {row['email_address']:row['id'] for row in data}


def verify_mailflow_tls_account(account):
    expect(account.get('imap_host')==FIXTURE and account.get('smtp_host')==FIXTURE
           and account.get('imap_port')==GOFER_IMAP_PORT and account.get('smtp_port')==GOFER_SMTP_PORT
           and account.get('imap_tls') is True and account.get('imap_skip_tls_verify') is False
           and account.get('smtp_tls')=='SSL','MailFlow did not preserve verified implicit TLS account settings')


def mailflow():
    r = Results()
    jar = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    client = Client('mailflow',jar)
    anonymous = Client('mailflow')
    r.test('anonymous_accounts',lambda:denied(anonymous,'/api/accounts'))
    r.test('anonymous_messages',lambda:denied(anonymous,'/api/mail/messages'))
    if not r.test('headless_auth',lambda:login(client,True)):
        return r.report()
    jar.save(ignore_discard=True,ignore_expires=True)
    r.test('csrf_rejected',lambda:denied(client,'/api/admin/settings','PATCH',{'allow_private_hosts':True},False))
    enrollment_started=time.monotonic()
    try:
        expect(FIXTURE_TLS,'Parity requires verified TLS fixture')
        # Deliberate exception only within the egress-isolated synthetic test stack.
        client.obj('/api/admin/settings','PATCH',{'allow_private_hosts':True,'allow_nonstandard_ports':True,'allow_insecure_tls':False})
        for user in USERS:
            obj=client.obj('/api/accounts','POST',{'name':user,'email_address':user+'@lab.test','imap_host':FIXTURE,
                'imap_port':GOFER_IMAP_PORT,'imap_skip_tls_verify':False,'smtp_host':FIXTURE,'smtp_port':GOFER_SMTP_PORT,'smtp_tls':'SSL','auth_user':user,'auth_pass':SECRET})
            expect(bool(obj.get('id')),'Account creation returned no ID')
        accounts=client.obj('/api/accounts')
        expect(isinstance(accounts,list) and len(accounts)==len(USERS),'Account inventory mismatch')
        for account in accounts:
            verify_mailflow_tls_account(account)
        ids={row['email_address']:row['id'] for row in accounts}
        expect(set(ids)=={u+'@lab.test' for u in USERS},'Account inventory mismatch')
        r.metrics['native_tls_account_readback']='PASS'
        r.mark('structured_accounts','PASS','Two exact account addresses and IDs')
        STATE_FILE.write_text(json.dumps({'ids':ids}),encoding='utf-8')
    except (AssertionError, ValueError, KeyError, URLError, OSError) as exc:
        r.mark('structured_accounts','FAIL',f'Enrollment failed: {exc}')
        return r.report()
    r.test('credential_redaction',lambda: expect(SECRET.encode() not in client.request('/api/accounts')[2], 'Fixture credential leaked in account API'))
    started=time.monotonic()
    def sync_both():
        rows=[messages(client,accountId=ids[u+'@lab.test'],limit=2) for u in USERS]
        return rows if all(x['messages'] and x['total']==COUNT//2 for x in rows) else None
    if not r.test('initial_corpus',lambda: (eventually(sync_both), 'Exact per-account corpus counts verified')[1]):
        return r.report()
    r.metrics['initial_sync_seconds']=round(time.monotonic()-started,3)
    observed=r.test('initial_corpus',lambda:mailflow_initial_identity(client,ids,COUNT))
    if not observed: return r.report()
    r.metrics['enrollment_to_full_corpus_seconds']=round(time.monotonic()-enrollment_started,3)
    r.mark('no_duplicates','PASS','Full original RFC822 identity corpus verified')
    r.mark('imap_two_accounts','PASS','Two distinct authenticated mailbox corpora returned through engine')
    def unified():
        obj=messages(client,limit=100)
        expect(obj['total']==COUNT,'Unified corpus count differs from oracle')
        expect({row['account_id'] for row in obj['messages']}==set(ids.values()),'Unified listing does not include both accounts')
        return 'Unified count and both account IDs verified'
    if r.test('unified_inbox',unified):
        r.mark('structured_messages','PASS','JSON messages array with fixture counts and account identifiers')
    def scoped():
        for u in USERS:
            aid=ids[u+'@lab.test']
            rows=messages(client,accountId=aid,limit=10)['messages']
            expect(rows and all(row['account_id']==aid for row in rows),'Account filter returned foreign messages')
        return 'Per-account filtering verified'
    r.test('account_scope',scoped)
    def pages():
        # On tiny smoke corpora pagination is still tested with limit=1.
        first=messages(client,limit=1,offset=0)['messages']
        second=messages(client,limit=1,offset=1)['messages']
        expect(len(first)==len(second)==1 and first[0]['id']!=second[0]['id'],'Page boundary overlap')
        return 'Two disjoint pages'
    r.test('pagination',pages)
    def cross_search():
        rows=search(client,'subject:commonmarker')
        expect({row['account_id'] for row in rows}==set(ids.values()),'Search omitted an account')
        return 'Search returned matches from both account IDs'
    start=time.monotonic()
    if r.test('search_cross_account',cross_search):
        r.mark('structured_search','PASS','Structured cross-account search results')
        r.metrics['search_seconds']=round(time.monotonic()-start,3)
    r.test('search_negative',lambda:expect(search(client,'subject:absolutelynonexistentzxcv')==[],'Unexpected search result'))
    row=r.test('structured_detail',lambda: eventually(lambda:marker_row(client,'alpha',0),timeout=60,interval=3))
    if isinstance(row,dict):
        mid=row['id']
        # Avoid copying message data into check evidence; the fixture is synthetic anyway.
        r.mark('structured_detail','PASS','Unique fixture subject resolved to message ID')
        r.test('anonymous_detail',lambda:denied(anonymous,'/api/mail/messages/'+mid))
        def detail():
            actual=client.obj('/api/mail/messages/'+mid)
            expect(actual.get('subject')==row['subject'],'Detail subject mismatch')
            return 'Exact subject verified'
        r.test('structured_detail',detail)
        def body():
            obj=client.obj(f'/api/mail/messages/{mid}/body')
            expect('bodymarker bakeoff-alpha-000000' in json.dumps(obj),'Expected body text missing')
            return obj
        value=r.test('message_body',body)
        if isinstance(value,dict):
            r.mark('message_body','PASS','Exact fixture body marker found in JSON')
            r.mark('structured_body','PASS','JSON body endpoint verified')
            def metadata():
                items=value.get('attachments',[])
                if isinstance(items,str):items=json.loads(items)
                item=next((a for a in items if a.get('filename')=='fixture.bin'),None)
                expect(item and item.get('part'),'Missing fixture attachment metadata')
                return item['part']
            part=r.test('attachment_metadata',metadata)
            if isinstance(part,str):
                def bytes_check():
                    code,_,raw=client.request(f'/api/mail/messages/{mid}/attachments/'+quote(part,safe=''))
                    expect(code==200 and raw==ATTACHMENT,'Attachment byte mismatch')
                    return hashlib.sha256(raw).hexdigest()
                t=time.monotonic()
                if r.test('attachment_bytes',bytes_check):r.metrics['attachment_seconds']=round(time.monotonic()-t,3)
        def raw_mime():
            code,_,raw=client.request(f'/api/mail/messages/{mid}/raw.eml')
            expect(code==200,'Raw MIME not returned')
            msg=BytesParser(policy=email.policy.default).parsebytes(raw)
            expect(msg['Message-ID']=='<bakeoff-alpha-000000@lab.test>' and any(p.get_payload(decode=True)==ATTACHMENT for p in msg.iter_attachments()),'Raw MIME mismatch')
            return 'Message-ID and binary attachment independently decoded'
        r.test('raw_mime',raw_mime)
        for key,kind,val in [('read_flag','read',True),('unread_flag','read',False),('star_flag','star',True),('unstar_flag','star',False)]:
            r.test(key,lambda kind=kind,val=val:flag_test(client,row,'alpha',kind,val))
        def isolation():
            client.obj('/api/admin/settings','PATCH',{'registration_open':True})
            other=Client('mailflow')
            try:
                other.obj('/api/auth/register','POST',{'username':'other-bakeoff','password':SECRET})
                expect(other.obj('/api/accounts')==[],'Foreign user saw accounts')
                denied(other,'/api/mail/messages/'+mid,allowed=(401,403,404))
                return 'Second authenticated user denied access to known message ID'
            finally:
                client.obj('/api/admin/settings','PATCH',{'registration_open':False})
        r.test('cross_user_isolation',isolation)
        # The engine must deliver to the other fixture mailbox, not merely return 200.
        subject='Re: commonmarker bakeoff-alpha-000000'
        def sent():
            client.obj('/api/mail/send','POST',{'accountId':ids['alpha@lab.test'],
                'to':['beta@lab.test'],'subject':subject,'body':'gofer-outbound-body-marker',
                'inReplyTo':row['message_id'],'references':row['message_id'],'undoSeconds':0})
            received=eventually(lambda:fixture_find('beta','INBOX','Subject',subject),timeout=60,interval=2)
            expect(len(received)==1 and received[0]['Subject']==subject and 'alpha@lab.test' in str(received[0]['From']), 'Wrong or duplicate delivery')
            expect('outbound-body-marker' in received[0].get_body(preferencelist=('plain','html')).get_content(),'Wrong delivered body')
            return received[0]
        delivered=r.test('smtp_delivery',sent)
        if delivered is not None:
            r.mark('smtp_delivery','PASS','Independent recipient IMAP confirmed unique delivery and body')
            r.test('send_reply',lambda:expect(str(delivered.get('In-Reply-To'))==row['message_id'],'Reply header mismatch'))
            def outgoing():
                eventually(lambda:fixture_find('alpha','Sent','Subject',subject),timeout=60,interval=2)
                eventually(lambda:any(x.get('subject')==subject for x in search(client,'subject:bakeoff-alpha-000000')),timeout=60,interval=3)
                return 'Sent mailbox and searchable engine copy observed'
            r.test('outgoing_sync',outgoing)
    def incoming():
        inject('beta',COUNT//2)
        eventually(lambda:marker_row(client,'beta',COUNT//2),timeout=TIMEOUT,interval=5)
        return 'SMTP injection confirmed by independent IMAP and discovered through engine'
    r.test('new_delivery',incoming)
    record_pre_recreate(client,ids,r,mailflow_recovery_observation)
    jar.save(ignore_discard=True,ignore_expires=True)
    return r.report()


def mailflow_recreate():
    r=Results()
    jar=http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    jar.load(ignore_discard=True,ignore_expires=True)
    client=Client('mailflow',jar)
    if not STATE_FILE.exists():
        return r.report()
    ids=json.loads(STATE_FILE.read_text())['ids']
    def persisted():
        expect(account_ids(client)==ids,'Account IDs changed after recreation')
        return 'Original session cookie and identical account IDs after recreation'
    if r.test('session_recreate',persisted):
        r.mark('accounts_recreate','PASS','Exact original account IDs')
    else:
        client=Client('mailflow')
        if not r.test('restart_recovery',lambda:login(client,False)):
            return r.report()
        r.test('accounts_recreate',lambda:expect(account_ids(client)==ids,'Account inventory lost'))
    run_recovery_check(client,ids,r,mailflow_recovery_observation,'mailflow')
    def fresh():
        inject('alpha',COUNT//2+1)
        eventually(lambda:marker_row(client,'alpha',COUNT//2+1),timeout=TIMEOUT,interval=5)
        return 'New mail after container and DB recreation, no mailbox credential re-entry'
    r.test('fresh_delivery_recreate',fresh)
    return r.report()


# Gofer's mailbox ID API and RFC822 source are native, machine-readable
# interfaces. HTML is used only to obtain control-plane CSRF form tokens.
class GoferForms(HTMLParser):
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.forms = {}
        self.current = None
        self.meta = {}
        self.feed(raw.decode('utf-8', errors='replace'))
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self.current = a.get('action') or a.get('hx-post') or ''
            self.forms.setdefault(self.current, {})
        elif tag == 'input' and self.current is not None and a.get('type') == 'hidden' and a.get('name'):
            self.forms[self.current][a['name']] = a.get('value','')
        elif tag == 'meta' and a.get('name') and a.get('content'):
            self.meta[a['name']] = a['content']
    def handle_endtag(self, tag):
        if tag == 'form': self.current = None


class GoferClient(Client):
    def __init__(self, cookies=None):
        super().__init__('gofer',cookies)
        self.forms = GoferForms(b'')
    def call(self, path, method='GET', form=None, page=None, csrf=True, timeout=30, json_data=None):
        expect(path.startswith('/') and not path.startswith('//'), 'Invalid Gofer path')
        headers={'Accept':'*/*','User-Agent':'MC-email-bakeoff/1','Origin':self.base,'Referer':self.base+(page or '/')}
        fields=dict(form or {})
        if csrf:
            hidden=self.forms.forms.get(path,{})
            fields={**hidden, **fields}
            token=next((v for k,v in hidden.items() if 'csrf' in k.lower()),None)
            token=token or self.forms.meta.get('csrf-token')
            if token: headers['X-CSRF-Token']=token
            headers['HX-Request']='true' if page is None and method!='GET' else 'false'
        raw=None
        if form is not None:
            raw=urlencode(fields,doseq=True).encode()
            headers['Content-Type']='application/x-www-form-urlencoded'
        if json_data is not None:
            expect(form is None,'Cannot send both form and JSON')
            raw=json.dumps(json_data).encode()
            headers['Content-Type']='application/json'
        req=Request(self.base+path,data=raw,headers=headers,method=method)
        try: response=self.opener.open(req,timeout=timeout)
        except HTTPError as exc: response=exc
        with response:
            payload=response.read(16*1024*1024+1)
            expect(len(payload)<=16*1024*1024,'Gofer response exceeded cap')
            return response.code, response.headers, payload
    def page(self,path):
        code,_,raw=self.call(path)
        expect(code==200,f'Gofer page {path} HTTP {code}')
        self.forms=GoferForms(raw)
        return raw
    def post(self,path,fields,page=None,timeout=30):
        if page: self.page(page)
        return self.call(path,'POST',fields,page,timeout=timeout)
    def json(self,path):
        code,headers,raw=self.call(path)
        expect(code==200 and 'json' in headers.get('Content-Type','').lower(),f'Gofer JSON {path.split("?")[0]} HTTP {code} or non-JSON')
        return json.loads(raw)


def gofer_set_grouping(c, enabled):
    value='true' if enabled else 'false'
    c.page('/')
    code,_,raw=c.call('/api/settings/ui','PATCH',json_data={'group_conversations':value})
    expect(code==200 and json.loads(raw).get('ok') is True,'Gofer grouping settings update not acknowledged')
    expect(c.json('/api/settings/ui').get('group_conversations')==value,'Gofer grouping setting readback mismatch')
    return value


def gofer_ids(client, **params):
    # Native ID selection route, not screen scraping. Require nontruncated
    # results for all corpus and duplicate assertions.
    obj=client.json('/mail/search/ids?'+urlencode({'scope':'all',**params}))
    expect(isinstance(obj,dict) and isinstance(obj.get('ids'),list) and type(obj.get('total')) is int,'Invalid Gofer ID response')
    expect(not obj.get('truncated') and obj['total']==len(obj['ids']),'Gofer ID selection truncated or count mismatch')
    rows=obj['ids']
    expect(all(isinstance(row,dict) and str(row.get('id','')).isdigit() and row.get('account') for row in rows),'Invalid Gofer ID row')
    return rows


def gofer_mime(client,row):
    code,headers,raw=client.call('/api/messages/'+quote(str(row['id']),safe='')+'/source?download=1')
    expect(code==200 and 'message/rfc822' in headers.get('Content-Type','').lower(),f'Gofer raw MIME HTTP {code} or wrong type')
    msg=BytesParser(policy=email.policy.default).parsebytes(raw)
    expect(msg.get('Message-ID') and msg.get('Subject'),'Missing native MIME headers')
    return msg


def gofer_corpus(client,ids,count):
    rows=gofer_ids(client)
    if len(rows)!=count or {str(x['account']) for x in rows}!=set(ids.values()): return None
    return rows


def gofer_hydrate(client,rows,ids,count):
    expected={f'<bakeoff-{u}-{i:06d}@lab.test>':(u,i) for u in USERS for i in range(count//2)}
    found={}
    expect(len(rows)==count,'Wrong Gofer corpus size')
    for row in rows:
        msg=gofer_mime(client,row)
        mid=str(msg['Message-ID'])
        expect(mid in expected and mid not in found,'Unexpected or duplicate MIME Message-ID')
        u,i=expected[mid]
        expect(str(row['account'])==ids[u+'@lab.test'],'MIME belongs to wrong account')
        expect(str(msg['Subject'])==f'commonmarker bakeoff-{u}-{i:06d}','Wrong MIME subject')
        found[mid]=row
        if len(found)%25==0 or len(found)==count: progress(f'gofer hydrate {len(found)}/{count}')
    expect(set(found)==set(expected),'Incomplete native MIME corpus')
    return found


def gofer_bootstrap(c):
    c.page('/setup')
    code,headers,_=c.post('/setup',{'token':SECRET})
    expect(code in (302,303) and '/setup/owner' in headers.get('Location',''),f'Gofer setup token HTTP {code}, no owner redirect')
    c.page('/setup/owner')
    password='McLab!'+SECRET
    code,headers,_=c.post('/setup/owner',{'name':'Bakeoff Owner','username':'bakeoff','password':password,'password_confirmation':password})
    expect(code in (302,303) and '/settings/security' in headers.get('Location',''),f'Gofer owner setup HTTP {code}, no security redirect')
    c.page('/settings/security')
    expect(len(c.cookies)>0,'No owner session cookie')
    # Protected machine-readable request is the independent authentication proof.
    expect(gofer_ids(c)==[],'Unexpected messages in fresh Gofer instance')
    return 'Personal owner setup and authenticated native ID API'


class GoferDiagnosticText(HTMLParser):
    """Visible text only: never preserve form fields, scripts or raw markup."""
    def __init__(self, raw):
        super().__init__(convert_charrefs=True)
        self.parts=[]
        self.hidden=0
        self.feed(raw[:131072].decode('utf-8',errors='replace'))
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','template'): self.hidden+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','template'): self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


def gofer_response_diagnostic(c,code,headers,raw):
    # Disposable fixture only. Retain actionable visible error text, not HTML,
    # credentials, cookies or form values. Never log arbitrary JSON bodies.
    ctype=headers.get('Content-Type','').split(';')[0]
    text=''
    if ctype=='text/html':
        text=' '.join(GoferDiagnosticText(raw).parts)
    elif ctype=='text/plain':
        text=raw[:4096].decode('utf-8',errors='replace')
    sensitive=[SECRET]
    sensitive.extend(cookie.value for cookie in c.cookies)
    sensitive.extend(c.forms.meta.values())
    sensitive.extend(v for form in c.forms.forms.values() for v in form.values())
    for value in sorted({v for v in sensitive if v and len(v)>=4},key=len,reverse=True):
        text=text.replace(value,'[REDACTED]')
    # Omit opaque token-like strings and credential-bearing URLs as well.
    text=re.sub(r'https?://[^\s]+','[URL]',text)
    text=re.sub(r'[A-Za-z0-9_+/=-]{32,}','[OPAQUE]',text)
    text=' '.join(text.split())[:650]
    return f'HTTP {code}; type={ctype or "unknown"}; bytes={len(raw)}; text={text or "(none retained)"}'


def gofer_inventory(c):
    code,h,raw=c.call('/api/accounts')
    expect(code==200 and 'json' in h.get('Content-Type','').lower(),
           f'Gofer account inventory HTTP {code}, expected native JSON')
    data=json.loads(raw)
    expect(isinstance(data,list),'Gofer account inventory not an array')
    ids={}
    for row in data:
        expect(isinstance(row,dict),'Gofer invalid account inventory row')
        # The upstream Account model uses Go field names ID, Email, Provider.
        # Also accept a conventional snake-case JSON representation.
        address=row.get('Email',row.get('email',row.get('email_address')))
        aid=row.get('ID',row.get('id'))
        provider=row.get('Provider',row.get('provider'))
        if provider=='local': continue
        expect(isinstance(address,str) and address and isinstance(aid,str) and aid,
               'Gofer account inventory missing account identity')
        expect(address not in ids and aid not in ids.values(),'Gofer duplicate account identity')
        ids[address]=aid
    return ids,raw


def gofer_account_from_fixture(c,u,previous_accounts):
    """Discover a committed account through native message data, not wizard HTML."""
    expected={f'<bakeoff-{u}-{i:06d}@lab.test>':i for i in range(COUNT//2)}
    def observed():
        rows=gofer_ids(c)
        matches=set()
        for row in rows:
            aid=str(row['account'])
            if aid in previous_accounts: continue
            try:
                msg=gofer_mime(c,row)
            except (AssertionError, URLError, OSError):
                # ID indexing may precede native MIME availability.
                continue
            mid=str(msg['Message-ID'])
            if mid not in expected: continue
            i=expected[mid]
            expect(str(msg['Subject'])==f'commonmarker bakeoff-{u}-{i:06d}',
                   'Fixture identity has incorrect subject')
            expect(str(msg['To'])==u+'@lab.test','Fixture identity has incorrect recipient')
            matches.add(aid)
        expect(len(matches)<=1,'Duplicate fixture account identities observed')
        return next(iter(matches)) if matches else None
    return eventually(observed,timeout=min(TIMEOUT,120),interval=3)


def gofer_create_fixture_account(c,u):
    address=u+'@lab.test'
    fields={'provider':'imap','email_address':address,'display_name':u,
            'imap_host':FIXTURE,'imap_port':GOFER_IMAP_PORT,'imap_tls_mode':'tls',
            'smtp_host':FIXTURE,'smtp_port':GOFER_SMTP_PORT,'smtp_tls_mode':'tls',
            'username':u,'password':SECRET,'auth_method':'plain',
            'smtp_username':u,'smtp_password':SECRET,'proxy':''}
    c.page('/settings/accounts')
    try:
        before,_=gofer_inventory(c)
    except (AssertionError, ValueError, URLError, OSError):
        before=None
    previous_accounts=set()
    if before is None:
        previous_accounts={str(row["account"]) for row in gofer_ids(c)}
        progress(f"gofer enrollment {u} inventory unavailable; native mailbox baseline established")
    if before is not None:
        expect(address not in before,'Gofer fixture account unexpectedly existed before creation')
    # Upstream tests both transports without writing an account and returns
    # X-Gofer-Connection-Test: ok|failed. Explicit failure is never ignored.
    code,h,raw=c.post('/api/accounts/test',fields,timeout=130)
    verdict=h.get('X-Gofer-Connection-Test','')
    expect(code==200 and verdict=='ok',
           f'Gofer {u} transport preflight {verdict or "no result header"}: '+gofer_response_diagnostic(c,code,h,raw))
    progress(f"gofer enrollment {u} TLS transport preflight PASS")
    c.page('/settings/accounts')
    code,h,raw=c.post('/api/accounts',fields,timeout=130)
    aid=h.get('X-Gofer-Account-ID','')
    # Check persistence using an independently fetched account inventory.
    # Never resubmit blindly: a missing response header is not proof that the
    # create was rejected, and retrying can create duplicate accounts.
    if before is None:
        # Current upstream POST creates the account and starts sync BEFORE
        # rendering its optional calendar/contact setup fragment. GET on the
        # same path and the old response header are not guaranteed contracts.
        expect(200<=code<300,
               f'Gofer {u} account creation HTTP {code}; '+gofer_response_diagnostic(c,code,h,raw))
        progress(f'gofer enrollment {u} create HTTP {code}; verifying native fixture messages')
        try:
            observed_id=gofer_account_from_fixture(c,u,previous_accounts)
        except (AssertionError, ValueError, URLError, OSError) as exc:
            raise AssertionError(f'Gofer {u} account creation not verified; inventory unavailable; '
                                 f'native fixture proof failed ({type(exc).__name__}); '
                                 +gofer_response_diagnostic(c,code,h,raw)) from exc
        expect(not aid or aid==observed_id,'Gofer create header disagrees with native fixture account')
        progress(f'gofer enrollment {u} native fixture account verified')
        return observed_id
    try:
        after,_=gofer_inventory(c)
    except (AssertionError, ValueError, URLError, OSError) as exc:
        raise AssertionError(f'Gofer {u} inventory unavailable after create: {exc}; '+gofer_response_diagnostic(c,code,h,raw)) from exc
    expect(200<=code<300 and address in after and
           {k:v for k,v in after.items() if k!=address}==before and
           (not aid or aid==after[address]),
           f'Gofer {u} account creation not verified; header_present={bool(aid)}; '
           f'account_present={address in after}; '+gofer_response_diagnostic(c,code,h,raw))
    return after[address]


def gofer_enroll(c):
    security='/settings/security'
    # Approve only the two TLS endpoints on the disposable private network.
    for kind in ('private-target',):
        for protocol,port in (('imap',GOFER_IMAP_PORT),('smtp',GOFER_SMTP_PORT)):
            action=security+'/mail/'+kind
            c.page(security)
            if action not in c.forms.forms:
                # If the page presents a password verification form, use its exact
                # same-origin action. Never submit to a foreign/unknown endpoint.
                candidates=[p for p in c.forms.forms if p.startswith(security+'/') and ('verify' in p or 'step-up' in p)]
                if len(candidates)==1:
                    c.post(candidates[0],{'password':'McLab!'+SECRET})
                    c.page(security)
            expect(action in c.forms.forms,'Gofer mail security form unavailable (owner verification may be required)')
            code,headers,_=c.post(action,{'protocol':protocol,'host':FIXTURE,'port':port,'acknowledge':'yes'})
            location=headers.get('Location','')
            expect(code in (302,303) and 'mail_notice=' in location and 'mail_error=' not in location,f'Gofer {kind} {protocol} approval not confirmed, HTTP {code}')
    ids={}
    for u in USERS:
        ids[u+'@lab.test']=gofer_create_fixture_account(c,u)
    expect(len(set(ids.values()))==2,'Gofer account IDs not distinct')
    return ids


def gofer_denied(c,path):
    code,h,_=c.call(path)
    location=h.get('Location','')
    expect(code in (401,403) or code in (302,303,307,308) and location.split('?')[0] == '/login',f'Expected login refusal, HTTP {code}')
    return f'HTTP {code} authentication refusal'


def gofer_sync(c):
    code,_,_=c.post('/api/mail/sync',{})
    expect(200<=code<300,f'Gofer sync HTTP {code}')


def gofer_fresh(c,ids,user,index,old_rows):
    inject(user,index)
    before={str(x['id']) for x in old_rows}
    def observed():
        rows=gofer_ids(c,scope='account',scope_account_id=ids[user+'@lab.test'])
        for row in rows:
            if str(row['id']) not in before:
                msg=gofer_mime(c,row)
                if msg['Message-ID']==f'<bakeoff-{user}-{index:06d}@lab.test>': return row
    gofer_sync(c)
    return eventually(observed,timeout=TIMEOUT,interval=5)


def gofer():
    r=Results()
    jar=http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    c=GoferClient(jar)
    try:
        progress('test headless_auth BEGIN')
        gofer_bootstrap(c)
        progress('test headless_auth PASS')
        r.mark('headless_auth','PASS','Owner session and protected native ID API verified')
        r.test('anonymous_messages',lambda:gofer_denied(GoferClient(),'/mail/search/ids'))
        jar.save(ignore_discard=True,ignore_expires=True)
        progress('test gofer_enroll BEGIN')
        enrollment_started=time.monotonic()
        ids=gofer_enroll(c)
        progress('test gofer_enroll PASS')
    except (AssertionError,ValueError,KeyError,TypeError,URLError,OSError) as exc:
        progress('test gofer_enroll FAIL')
        r.metrics['adapter_blocker']=str(exc).replace(SECRET,'[REDACTED]') if SECRET else str(exc)
        r.metrics['adapter_blocker_stage']='owner_setup' if 'headless_auth' not in r.checks else 'mailbox_enrollment'
        if 'headless_auth' not in r.checks: r.mark('headless_auth','BLOCKED',str(exc))
        r.mark('imap_two_accounts','BLOCKED','Gofer enrollment not established; adapter or configuration unresolved')
        return r.report()
    STATE_FILE.write_text(json.dumps({'ids':ids}),encoding='utf-8')
    jar.save(ignore_discard=True,ignore_expires=True)
    # GET /api/accounts is a separate structured contract, not a successful POST.
    def account_inventory():
        data,_=gofer_inventory(c)
        expect(data==ids,'Gofer account inventory mismatch')
        return 'Exact account IDs and addresses from native JSON'
    try:
        gofer_inventory(c)
    except (AssertionError, ValueError, URLError, OSError) as exc:
        r.mark('structured_accounts','BLOCKED',str(exc))
    else:
        r.test('structured_accounts',account_inventory)
        r.test('credential_redaction',lambda:expect(SECRET.encode() not in gofer_inventory(c)[1],'Fixture credential returned in account JSON'))
        r.test('anonymous_accounts',lambda:gofer_denied(GoferClient(),'/api/accounts'))
    started=time.monotonic()
    try:
        gofer_sync(c)
        rows=eventually(lambda:gofer_corpus(c,ids,COUNT),timeout=TIMEOUT,interval=5)
    except (AssertionError,ValueError,KeyError,URLError,OSError) as exc:
        r.mark('initial_corpus','BLOCKED',str(exc)); return r.report()
    r.metrics['initial_sync_seconds']=round(time.monotonic()-started,3)
    if COUNT>200:
        r.mark('initial_corpus','BLOCKED','Full MIME oracle hydration is limited to 200 messages in this functional profile')
        return r.report()
    started=time.monotonic()
    found=r.test('initial_corpus',lambda:gofer_hydrate(c,rows,ids,COUNT))
    if not isinstance(found,dict): return r.report()
    r.metrics['native_id_plus_mime_hydration_seconds']=round(time.monotonic()-started,3)
    r.metrics['enrollment_to_full_corpus_seconds']=round(time.monotonic()-enrollment_started,3)
    r.mark('imap_two_accounts','PASS','Exact independent fixture Message-IDs and two account IDs')
    r.mark('no_duplicates','PASS','Full corpus unique Message-IDs')
    r.mark('structured_messages','PASS','Native JSON ID list plus RFC822 hydration; not a single JSON summary endpoint')
    r.mark('structured_detail','PASS','Native RFC822 headers parsed; not HTML')
    def scoped():
        for u in USERS:
            aid=ids[u+'@lab.test']
            items=gofer_ids(c,scope='account',scope_account_id=aid)
            expect({str(x['id']) for x in items}=={str(x['id']) for x in rows if str(x['account'])==aid},'Account scope differs from full native listing')
        return 'Exact scoped ID sets'
    r.test('account_scope',scoped)
    # Determine an effective search parameter with both positive and negative
    # controls. A parameter ignored by upstream can never earn search credit.
    def search_contract():
        expected_ids={str(x['id']) for x in rows}
        for field in ('q','search','subject'):
            try:
                pos=gofer_ids(c,**{field:'commonmarker'})
                neg=gofer_ids(c,**{field:'absolutelynonexistentzxcv'})
                single=gofer_ids(c,**{field:'bakeoff-alpha-000000'})
            except (AssertionError,ValueError,URLError,OSError):
                continue
            if ({str(x['id']) for x in pos}==expected_ids and not neg and
                {str(x['id']) for x in single}=={str(found['<bakeoff-alpha-000000@lab.test>']['id'])}):
                return field
        raise AssertionError('No tested native search parameter satisfied both positive and negative controls')
    field=r.test('search_cross_account',search_contract)
    if not isinstance(field,str):
        r.mark('search_cross_account','BLOCKED','Search query contract not established; no conclusion about engine capability')
    if isinstance(field,str):
        r.mark('structured_search','PASS','Native JSON ID results match both accounts; negative and unique-subject controls')
        r.mark('search_negative','PASS','Nonexistent subject returned zero rows')
        r.metrics['search_parameter']=field
    # Global search is not the unified inbox. Test its actual folder endpoint.
    def unified():
        for folder in ('inbox','unified-inbox'):
            try: obj=c.json('/mail/folder/'+folder+'/ids')
            except (AssertionError,ValueError,URLError,OSError): continue
            if (isinstance(obj,dict) and not obj.get('truncated') and obj.get('total')==COUNT and
                {str(x.get('id')) for x in obj.get('ids',[])}=={str(x['id']) for x in rows}):
                return 'Exact fixture IDs returned by native inbox folder endpoint '+folder
        raise AssertionError('Unified inbox route not established')
    if not r.test('unified_inbox',unified):
        r.mark('unified_inbox','BLOCKED','Unified folder route not established; no capability conclusion')
    row=found['<bakeoff-alpha-000000@lab.test>']
    path='/api/messages/'+str(row['id'])+'/source?download=1'
    r.test('anonymous_detail',lambda:gofer_denied(GoferClient(),path))
    msg=r.test('raw_mime',lambda:gofer_mime(c,row))
    if not isinstance(msg,EmailMessage): return r.report()
    r.mark('raw_mime','PASS','RFC822 media type and fixture Message-ID verified')
    def body():
        part=msg.get_body(preferencelist=('plain',))
        expect(part is not None and 'bodymarker bakeoff-alpha-000000' in part.get_content(),'MIME body mismatch')
        return 'Native MIME plain text matches fixture'
    if r.test('message_body',body): r.mark('structured_body','PASS','RFC822 MIME parsing; no HTML scraping')
    def attachment():
        parts=[p for p in msg.iter_attachments() if p.get_filename()=='fixture.bin']
        expect(len(parts)==1,'Missing or duplicate attachment')
        return parts[0]
    part=r.test('attachment_metadata',attachment)
    if part is not None:
        r.test('attachment_bytes',lambda:expect(part.get_payload(decode=True)==ATTACHMENT,'Decoded attachment byte mismatch'))
    def flag(kind,value):
        flag=b'\\Seen' if kind=='read' else b'\\Flagged'
        mid=str(msg['Message-ID'])
        if (flag in fixture_flags('alpha',mid))==value:
            # Establish a real transition rather than crediting an initial state.
            code,_,_=c.post('/api/messages/'+str(row['id'])+'/'+kind,{})
            expect(200<=code<300,f'Gofer flag HTTP {code}')
            eventually(lambda:(flag in fixture_flags('alpha',mid))!=value,timeout=30,interval=2)
        code,_,_=c.post('/api/messages/'+str(row['id'])+'/'+kind,{})
        expect(200<=code<300,f'Gofer flag HTTP {code}')
        eventually(lambda:(flag in fixture_flags('alpha',mid))==value,timeout=30,interval=2)
        return 'Independent IMAP flag transition'
    for key,kind,value in [('read_flag','read',True),('unread_flag','read',False),('star_flag','star',True),('unstar_flag','star',False)]:
        r.test(key,lambda k=kind,v=value:flag(k,v))
    r.test('new_delivery',lambda:gofer_fresh(c,ids,'beta',COUNT//2,rows))
    # Submit exactly one outbound message. Independent recipient IMAP, not
    # the HTTP response or a queue status, proves SMTP delivery.
    subject='Re: commonmarker bakeoff-alpha-000000'
    def send():
        c.page('/')
        code,_,_=c.post('/compose',{'account_id':ids['alpha@lab.test'],'to':'beta@lab.test',
            'subject':subject,'body':'gofer-outbound-body-marker','in_reply_to':str(msg['Message-ID']),
            'references':str(msg['Message-ID'])})
        expect(200<=code<300,f'Gofer compose HTTP {code}')
        delivered=eventually(lambda:fixture_find('beta','INBOX','Subject',subject),timeout=min(TIMEOUT,120),interval=3)
        expect(len(delivered)==1 and str(delivered[0]['Subject'])==subject and 'alpha@lab.test' in str(delivered[0]['From']), 'Outbound delivery mismatch')
        body=delivered[0].get_body(preferencelist=('plain','html'))
        expect(body is not None and 'gofer-outbound-body-marker' in body.get_content(),'Outbound body mismatch')
        return delivered[0]
    delivered=r.test('smtp_delivery',send)
    if delivered is not None:
        r.mark('smtp_delivery','PASS','Independent recipient IMAP verified unique outbound delivery and body')
        r.test('send_reply',lambda:expect(str(delivered.get('In-Reply-To'))==str(msg['Message-ID']),'Reply header mismatch'))
        def outgoing():
            eventually(lambda:fixture_find('alpha','Sent','Subject',subject),timeout=60,interval=3)
            def engine_copy():
                for item in gofer_ids(c,scope='account',scope_account_id=ids['alpha@lab.test']):
                    if str(item['id']) not in {str(x['id']) for x in rows} and str(gofer_mime(c,item)['Subject'])==subject:
                        return True
            eventually(engine_copy,timeout=60,interval=3)
            return 'Independent Sent folder and native engine copy'
        r.test('outgoing_sync',outgoing)
    # Observe the same original corpus on either side of the grouping toggle.
    # This is independent of container recreation and never retries enrollment.
    try:
        grouped_setting=gofer_set_grouping(c,True)
        grouped=gofer_recovery_observation(c,ids,COUNT)
        gofer_set_grouping(c,False)
        ungrouped=gofer_recovery_observation(c,ids,COUNT)
        r.metrics['conversation_grouping']={'before_setting':grouped_setting,'before':grouped,'after_setting':'false','after':ungrouped,
            'original_restored_by_toggle': '<bakeoff-alpha-000000@lab.test>' in grouped['missing'] and '<bakeoff-alpha-000000@lab.test>' not in ungrouped['missing']}
        progress(f'gofer grouping originals={grouped["found"]}/{ungrouped["found"]}')
        record_pre_recreate(c,ids,r,gofer_recovery_observation)
    except (AssertionError,ValueError,KeyError,TypeError,URLError,OSError) as exc:
        r.metrics['conversation_grouping']={'status':'BLOCKED','reason':str(exc)}
        progress('gofer grouping blocked')
    # Gofer's personal mode is deliberately single-owner. Do not award
    # cross-user isolation or claim an unobserved send/compose API contract.
    r.mark('cross_user_isolation','BLOCKED','Personal-mode run does not establish second authenticated user isolation')
    jar.save(ignore_discard=True,ignore_expires=True)
    return r.report()


def gofer_recovery_observation(c, ids, count):
    return recovery_observation(c,ids,count,
        lambda client,aid:gofer_ids(client,scope='account',scope_account_id=aid),gofer_mime,'account')


def mailflow_rows(c,aid):
    offset=0
    seen=set()
    total=None
    while True:
        data=messages(c,accountId=aid,limit=50,offset=offset)
        rows=data['messages']
        expect(type(data.get('total')) is int,'MailFlow inventory total missing')
        if total is None: total=data['total']
        expect(data['total']==total,'MailFlow inventory changed during pagination')
        for row in rows:
            key=row['id']
            expect(key not in seen,'MailFlow repeated a page row')
            seen.add(key)
            yield row
        offset+=len(rows)
        if offset==total: return
        expect(rows and offset<total,'MailFlow inventory incomplete')


def mailflow_mime(c,row):
    code,_,raw=c.request('/api/mail/messages/'+quote(str(row['id']),safe='')+'/raw.eml')
    expect(code==200,'MailFlow raw MIME unavailable')
    msg=BytesParser(policy=email.policy.default).parsebytes(raw)
    expect(msg.get('Message-ID') and msg.get('Subject'),'Missing MailFlow MIME headers')
    return msg


def mailflow_recovery_observation(c,ids,count):
    return recovery_observation(c,ids,count,mailflow_rows,mailflow_mime,'account_id')


def observation_complete(obs):
    return obs['found']==obs['expected'] and not any(obs[k] for k in ('missing','duplicates','wrong_account','unavailable_sources'))


def mailflow_initial_identity(c,ids,count):
    obs=mailflow_recovery_observation(c,ids,count)
    expect(observation_complete(obs),'MailFlow initial RFC822 identity corpus mismatch')
    return 'All original RFC822 identities and account scopes verified'


def record_pre_recreate(c,ids,r,observer):
    try:
        obs=observer(c,ids,COUNT)
        verified=observation_complete(obs)
        r.metrics['pre_recreate_mailbox']={'verified':verified,'observation':obs}
    except (AssertionError,ValueError,KeyError,TypeError,URLError,OSError) as exc:
        verified=False
        r.metrics['pre_recreate_mailbox']={'verified':False,'error_type':type(exc).__name__}
    state=json.loads(STATE_FILE.read_text())
    state['pre_recreate_verified']=verified
    STATE_FILE.write_text(json.dumps(state),encoding='utf-8')


def recovery_observation(c, ids, count, list_rows, fetch_mime, account_field):
    """Read fresh account-scoped inventories, and identify mail by RFC822 ID.

    Database row IDs and the default global listing need not survive recreation.
    Extra incoming and sent mail is permitted; each seeded message must occur
    exactly once in its original account.
    """
    expected={f'<bakeoff-{u}-{i:06d}@lab.test>':u for u in USERS for i in range(count//2)}
    seen={}
    duplicate=set()
    wrong_account=set()
    unavailable=0
    for user in USERS:
        aid=ids[user+'@lab.test']
        for row in list_rows(c,aid):
            try:
                msg=fetch_mime(c,row)
            except (AssertionError, URLError, OSError):
                # An indexed row can precede availability of its MIME source.
                unavailable+=1
                continue
            mid=str(msg['Message-ID'])
            if mid not in expected: continue
            marker=mid.removeprefix('<').removesuffix('@lab.test>')
            expect(str(msg.get('Subject'))==f'commonmarker {marker}','Original MIME subject mismatch')
            if expected[mid]!=user or str(row[account_field])!=str(aid):
                wrong_account.add(mid)
                continue
            if mid in seen: duplicate.add(mid)
            seen[mid]=row
    return {'expected':len(expected),'found':len(seen),
            'missing':sorted(set(expected)-set(seen)),
            'duplicates':sorted(duplicate),'wrong_account':sorted(wrong_account),
            'unavailable_sources':unavailable}


def gofer_recover_messages(c,ids,count,r,timeout=TIMEOUT,interval=5):
    return recover_messages(c,ids,count,r,gofer_recovery_observation,'gofer',timeout,interval)


def recover_messages(c,ids,count,r,observer,candidate,timeout=TIMEOUT,interval=5):
    """Separate bounded recovery from an instantaneous readiness snapshot."""
    started=time.monotonic()
    deadline=started+timeout
    attempts=0
    last=None
    last_error=None
    while True:
        attempts+=1
        try:
            last=observer(c,ids,count)
            last_error=None
            progress(f'{candidate} recovery attempt={attempts} originals={last["found"]}/{last["expected"]} '
                     f'duplicates={len(last["duplicates"])} wrong_account={len(last["wrong_account"])} '
                     f'unavailable_sources={last["unavailable_sources"]}')
        except (AssertionError,ValueError,KeyError,TypeError,URLError,OSError) as exc:
            last=None
            last_error=type(exc).__name__
            progress(f'{candidate} recovery attempt={attempts} inventory_unavailable={last_error}')
        elapsed=round(time.monotonic()-started,3)
        r.metrics['mailbox_recovery']={'attempts':attempts,'elapsed_seconds':elapsed,
            'deadline_seconds':timeout,'observation':last,'last_error_type':last_error}
        if last and observation_complete(last):
            return f'All {count} original RFC822 message identities recovered in their original accounts after {elapsed}s ({attempts} attempts)'
        if time.monotonic()>=deadline:
            break
        time.sleep(min(interval,max(0,deadline-time.monotonic())))
    if last is None:
        r.mark('messages_recreate','BLOCKED','Native account inventory unavailable at recovery deadline; no message-loss conclusion')
        return None
    if last['duplicates'] or last['wrong_account']:
        raise AssertionError(f'Recovery inventory mismatch: {len(last["duplicates"])} duplicate original IDs; {len(last["wrong_account"])} wrong-account original IDs')
    # Confirm missing messages still exist on the independent source server.
    # Do not confuse unavailable source data with actual loss from Gofer.
    if last['missing']:
        mid=last['missing'][0]
        user=next(u for u in USERS if mid.startswith(f'<bakeoff-{u}-'))
        try:
            originals=fixture_find(user,'INBOX','Message-ID',mid)
            present=sum(str(m.get('Message-ID'))==mid for m in originals)==1
        except (URLError,OSError,imaplib.IMAP4.error,AssertionError):
            present=False
        r.metrics['mailbox_recovery']['oracle_missing_example']={'message_id':mid,'present_once':present}
        if not present:
            r.mark('messages_recreate','BLOCKED','Independent fixture could not confirm missing original at recovery deadline')
            return None
        raise AssertionError(f'{len(last["missing"])} original message identities unavailable after {elapsed}s; independent fixture confirms {mid} still exists')
    r.mark('messages_recreate','BLOCKED','All originals observed but some native MIME sources unavailable; complete recovery not established')
    return None


def run_recovery_check(c,ids,r,observer,candidate):
    progress('test messages_recreate BEGIN')
    if not json.loads(STATE_FILE.read_text()).get('pre_recreate_verified'):
        r.mark('messages_recreate','BLOCKED','Complete pre-recreation corpus not established')
    else:
        try:
            recovered=recover_messages(c,ids,COUNT,r,observer,candidate)
            if recovered:
                r.mark('messages_recreate','PASS',recovered)
            elif 'messages_recreate' not in r.checks:
                r.mark('messages_recreate','BLOCKED','Recovery not established')
        except (AssertionError,TimeoutError,URLError,OSError,ValueError,KeyError,TypeError,imaplib.IMAP4.error) as exc:
            r.mark('messages_recreate','FAIL',f'{type(exc).__name__}: {exc}')
    progress('test messages_recreate '+r.checks['messages_recreate']['status'])


def gofer_recreate():
    r=Results()
    if not STATE_FILE.exists(): return r.report()
    jar=http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    jar.load(ignore_discard=True,ignore_expires=True)
    c=GoferClient(jar)
    ids=json.loads(STATE_FILE.read_text())['ids']
    rows=r.test('session_recreate',lambda:gofer_ids(c))
    if not isinstance(rows,list): return r.report()
    def persisted():
        def observe():
            current=gofer_ids(c)
            if {str(x['account']) for x in current}!=set(ids.values()): return None
            if not all(gofer_ids(c,scope='account',scope_account_id=aid) for aid in ids.values()): return None
            return 'Original account IDs and scopes visible after recreation'
        return eventually(observe,timeout=TIMEOUT,interval=5)
    if not r.test('accounts_recreate',persisted): return r.report()
    try:
        grouping=c.json('/api/settings/ui').get('group_conversations')
    except (AssertionError,ValueError,KeyError,TypeError,URLError,OSError):
        grouping=None
    r.metrics['grouping_after_recreation']=grouping
    if grouping!='false':
        r.mark('messages_recreate','BLOCKED','Ungrouped enumeration setting not verified after recreation')
        progress('test messages_recreate BLOCKED')
    else:
        run_recovery_check(c,ids,r,gofer_recovery_observation,'gofer')
    # Refresh inventory for the independent fresh-delivery test as well.
    r.test('fresh_delivery_recreate',lambda:gofer_fresh(c,ids,'alpha',COUNT//2+1,gofer_ids(c)))
    return r.report()


def main():
    action=sys.argv[1]
    try:
        if action=='seed': result=seed()
        elif action=='ready': result=ready(sys.argv[2])
        elif action=='ready-once': result=ready_once(sys.argv[2])
        elif action=='gofer': result=gofer()
        elif action=='gofer-recreate': result=gofer_recreate()
        elif action=='mailflow': result=mailflow()
        elif action=='mailflow-recreate': result=mailflow_recreate()
        else: raise ValueError('Unknown probe')
        print(json.dumps(result))
    except Exception as exc:
        # Avoid emitting credentials or raw response bodies to host logs.
        print(f'{type(exc).__name__}: {str(exc).replace(SECRET,"[REDACTED]") if SECRET else str(exc)}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
