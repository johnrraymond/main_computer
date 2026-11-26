"""Interactive, disposable provider-auth smoke. Never logs provider secrets or mail.

Requires the finalist parity patch. Uses real provider consent in a normal browser;
only engine-native read APIs are called by the harness. No automated mail sending.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import secrets
import socket
import tempfile
import time
from datetime import datetime, timezone
from email.utils import getaddresses
from pathlib import Path
from urllib.error import URLError

from tools import email_engine_bakeoff as lab
from tools import email_engine_bakeoff_probe as probe

MODES = {
    'outlook-device': ('outlook', 'Use the built-in personal Outlook/Microsoft device-code sign-in.'),
    'microsoft365-oauth': ('outlook', 'Configure your organizational Microsoft OAuth app if required; use the work/school OAuth flow.'),
    'gmail-oauth': ('gmail', 'Configure your own Google OAuth client using the callback URL displayed by the engine; authorize using Google sign-in.'),
    'gmail-app-password': ('gmail', 'Use Gmail IMAP/SMTP with a Google-generated app password, not your ordinary password.'),
    'yahoo-app-password': ('yahoo', 'Use Yahoo IMAP/SMTP with a Yahoo-generated app password, not your ordinary password.'),
    'generic-tls': ('generic', 'Use a disposable generic IMAP/SMTP account with certificate-verified TLS or STARTTLS.'),
}
CHECKS = ('startup', 'anonymous_refused', 'local_session', 'operator_enrollment',
          'live_message_before', 'recreate_ready', 'session_after', 'live_message_after',
          'same_account', 'token_refresh', 'provider_revocation', 'outbound_mail')


def spec_for(candidate, secret, port):
    # Separate project, no fixture/probe container, no existing volumes or global networks.
    spec = lab.compose_spec(candidate, secret, 100, 600)
    services = spec['services']
    services.pop('probe'); services.pop('fixture')
    spec['networks'] = {'default': {'driver': 'bridge'}}  # real provider egress
    url = f'http://localhost:{port}'
    if candidate == 'gofer':
        engine = services['engine']
        engine.pop('networks', None)
        engine['environment'].update(GOFER_BASE_URL=url)
        engine['environment'].pop('GOFER_ALLOW_INSECURE_LAN', None)
        engine['ports'] = [f'127.0.0.1:{port}:8090']
    elif candidate == 'mailflow':
        backend = services.pop('engine')
        backend['environment'].update(APP_URL=url, FRONTEND_URL=url)
        backend['healthcheck'] = {'test': ['CMD-SHELL', 'wget -qO- http://localhost:3000/api/health || exit 1'],
                                  'interval': '3s', 'timeout': '3s', 'retries': 60}
        services['backend'] = backend
        services['frontend'] = {'image': 'ghcr.io/maathimself/mailflow-frontend:latest',
                                'ports': [f'127.0.0.1:{port}:80'],
                                'environment': {'TLS_MODE': 'off', 'NGINX_TLS': 'off'},
                                'depends_on': {'backend': {'condition': 'service_healthy'}}}
    else:
        raise ValueError('Unsupported finalist')
    return spec, url


def emit(message):
    print('EMAIL_PROVIDER_AUTH ' + message, flush=True)


def account_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def recipient_matches(msg, address):
    fields = [str(v) for name in ('To', 'Cc', 'Delivered-To', 'X-Original-To') for v in msg.get_all(name, [])]
    return address.casefold() in {email.casefold() for _, email in getaddresses(fields)}


def verify_marker(candidate, client, address, marker):
    """Return native account ID and identity, never content, for an exact new message."""
    if candidate == 'gofer':
        rows = probe.gofer_ids(client, q=marker)
        match = []
        for row in rows:
            msg = probe.gofer_mime(client, row)
            if str(msg.get('Subject')) == marker and recipient_matches(msg, address):
                match.append((str(row['account']), str(msg['Message-ID'])))
    else:
        ids = probe.account_ids(client)
        aid = next((str(v) for k, v in ids.items() if k.casefold() == address.casefold()), None)
        if aid is None:
            return None
        match = []
        for row in probe.search(client, 'subject:' + marker):
            if str(row.get('account_id')) != aid or row.get('subject') != marker:
                continue
            msg = probe.mailflow_mime(client, row)
            if str(msg.get('Subject')) == marker and recipient_matches(msg, address):
                match.append((aid, str(msg['Message-ID'])))
    if len(match) > 1:
        raise AssertionError('Nonunique challenge message')
    return match[0] if match else None


def wait_marker(candidate, client, address, marker, timeout, *, clock=time.monotonic, sleep=time.sleep):
    end = clock() + timeout
    attempt = 0
    while clock() < end:
        attempt += 1
        emit(f'challenge poll={attempt}')
        try:
            result = verify_marker(candidate, client, address, marker)
        except (URLError, OSError):
            result = None
        if result:
            return result
        sleep(min(5, max(0, end-clock())))
    raise TimeoutError('Fresh provider challenge not observed')


def port_available(port):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))


def ready(candidate, client, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            code, _, _ = client.request('/healthz' if candidate == 'gofer' else '/api/health', timeout=5)
            if code == 200:
                return
        except (URLError, OSError):
            pass
        emit('waiting for engine HTTP readiness')
        time.sleep(3)
    raise TimeoutError('Engine HTTP readiness deadline')


def local_auth(candidate, url, setup, password):
    # Reuse the exact native enrollment contracts already exercised in the lab.
    probe.BASES[candidate] = url
    if candidate == 'gofer':
        c = probe.GoferClient()
        c.page('/setup')
        code, h, _ = c.post('/setup', {'token': setup})
        probe.expect(code in (302,303) and '/setup/owner' in h.get('Location',''), 'Owner setup refused')
        c.page('/setup/owner')
        code, h, _ = c.post('/setup/owner', {'name':'Provider Smoke Owner','username':'bakeoff',
                                                 'password':password,'password_confirmation':password})
        probe.expect(code in (302,303) and '/settings/security' in h.get('Location',''), 'Owner registration refused')
        probe.expect(len(c.cookies)>0 and probe.gofer_ids(c)==[], 'Fresh owner session not established')
        probe.gofer_set_grouping(c, False)
    else:
        c = probe.Client('mailflow')
        data = c.obj('/api/auth/register','POST',{'username':'bakeoff','password':password})
        probe.expect(data.get('user',{}).get('username')=='bakeoff' and len(c.cookies)>0, 'Local registration refused')
        probe.expect(probe.account_ids(c)=={}, 'Fresh account inventory not empty')
    return c


def check_session(candidate, client):
    if candidate == 'gofer':
        probe.gofer_ids(client)
    else:
        probe.account_ids(client)


def anonymous_refused(candidate):
    c = probe.GoferClient() if candidate == 'gofer' else probe.Client(candidate)
    if candidate == 'gofer':
        probe.gofer_denied(c, '/mail/search/ids?scope=all')
    else:
        probe.denied(c, '/api/accounts')


def report_status(report):
    required = CHECKS[:9]
    if any(report['checks'][key]=='FAIL' for key in required):
        return 'FAIL'
    if all(report['checks'][key]=='PASS' for key in required) and report['cleanup']=='PASS':
        return 'AUTH_SMOKE_PASS_OPERATOR_ASSISTED'
    return 'INCOMPLETE'


def run(args):
    if not args.authorize_disposable_account:
        raise SystemExit('Use --authorize-disposable-account only with a dedicated test mailbox you control.')
    if not 1024 <= args.port <= 65535 or not 15 <= args.timeout <= 3600:
        raise SystemExit('Invalid port or timeout')
    port_available(args.port)
    password = getpass.getpass('New temporary LOCAL engine password (not a provider password; >=12 characters): ')
    if len(password)<12:
        raise SystemExit('Local engine password too short')
    address = input('Dedicated test mailbox address (not saved to report): ').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', address):
        raise SystemExit('Invalid mailbox address')
    setup = secrets.token_hex(32)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)
    project = 'mc-email-provider-'+args.candidate+'-'+secrets.token_hex(6)
    root = lab.ROOT/'runtime'/'reports'/'email_engine_provider_acceptance'/run_id
    root.mkdir(parents=True, exist_ok=False)
    target = root/'summary.json'
    report = {'candidate':args.candidate,'provider':MODES[args.mode][0],'mode':args.mode,
              'project':project,'production_modified':False,'checks':dict.fromkeys(CHECKS,'NOT_RUN'),
              'cleanup':'NOT_RUN','status':'INCOMPLETE','mode_evidence':'operator_attestation_only',
              'limitations':['No forced OAuth token expiry or refresh observation','No provider revocation assertion',
                             'No automated outbound email','Loopback lab HTTP is not production gateway certification']}
    def save():
        report['status']=report_status(report)
        lab.write_report(target,report)
    def mark(key,status='PASS'):
        report['checks'][key]=status
        emit(f'{key} {status}')
        save()
    save()
    stage='startup'
    with tempfile.TemporaryDirectory(prefix='mc-email-provider-') as d:
        file=Path(d)/'compose.json'
        spec,url=spec_for(args.candidate,setup,args.port)
        file.write_text(json.dumps(spec),encoding='utf-8')
        def compose(*parts,timeout=600):
            # No raw docker logs or exceptions are emitted; OAuth callback URLs may contain codes.
            return lab.command(['docker','compose','-p',project,'-f',str(file),*parts],timeout, label='EMAIL_PROVIDER_AUTH docker',secret=setup)
        try:
            emit('starting private loopback test stack (internet egress enabled)')
            compose('pull',timeout=1200)
            compose('up','-d',timeout=600)
            probe.BASES[args.candidate]=url
            ready(args.candidate,probe.Client(args.candidate))
            # Record actual image identities without dumping container environments.
            report['images']={}
            for service in spec['services']:
                cid=compose('ps','-q',service,timeout=30).strip()
                image=lab.command(['docker','inspect','--format','{{.Image}}',cid],30).strip()
                if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
                    raise AssertionError('Image identity unavailable')
                report['images'][service]=image
            mark(stage)
            stage='anonymous_refused'; anonymous_refused(args.candidate); mark(stage)
            stage='local_session'; client=local_auth(args.candidate,url,setup,password); mark(stage)
            stage='operator_enrollment'
            print(f'Open {url} in a NEW private browser window. Sign in as bakeoff using your temporary local password.\n'
                  f'{MODES[args.mode][1]}\n'
                  'Use only your dedicated test mailbox. Keep TLS verification enabled for mail transport.\n'
                  'For OAuth, complete the provider consent page yourself. Do not paste credentials or codes into this terminal.\n'
                  'Wait for the engine to report the account connected.',flush=True)
            if input('Type CONNECTED only if the requested mode completed (or press Enter to stop): ').strip()!='CONNECTED':
                mark(stage,'BLOCKED'); return report
            mark(stage)
            stage='live_message_before'
            first='mc-auth-'+secrets.token_hex(12)
            print('From independent provider webmail or another account, deliver a NEW email to the test mailbox with this exact subject:\n'+first,flush=True)
            input('Press Enter after sending the challenge: ')
            before=wait_marker(args.candidate,client,address,first,args.timeout)
            mark(stage)
            stage='recreate_ready'
            names=['engine'] if args.candidate=='gofer' else ['backend','frontend']
            compose('up','-d','--force-recreate','--no-deps',*names,timeout=600)
            ready(args.candidate,probe.Client(args.candidate)); mark(stage)
            stage='session_after'; check_session(args.candidate,client); mark(stage)
            stage='live_message_after'
            second='mc-auth-'+secrets.token_hex(12)
            print('WITHOUT reconnecting the account, deliver another NEW email from independent webmail with exact subject:\n'+second,flush=True)
            input('Press Enter after sending the second challenge: ')
            after=wait_marker(args.candidate,client,address,second,args.timeout); mark(stage)
            stage='same_account'
            if before[0]!=after[0] or before[1]==after[1]:
                raise AssertionError('Account changed or challenge identity reused')
            report['account_fingerprint']=account_digest(before[0]); mark(stage)
            print('Smoke evidence collected. Revoke the test app authorization/app password at the provider when finished. Local volumes will be deleted.',flush=True)
        except KeyboardInterrupt:
            mark(stage,'BLOCKED')
        except Exception as exc:
            # Never report exception text: URLs and payloads can contain live tokens or mail.
            report['failure_type']=type(exc).__name__; report['failure_stage']=stage
            mark(stage,'FAIL')
        finally:
            try:
                compose('down','--volumes','--remove-orphans',timeout=180)
                report['cleanup']='PASS'
            except Exception:
                report['cleanup']='FAIL'
                emit(f'cleanup failed; inspect only project {project}')
            save()
            emit(f'{report["status"]} report={target}')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',choices=('gofer','mailflow'),required=True)
    parser.add_argument('--mode',choices=tuple(MODES),required=True)
    parser.add_argument('--port',type=int,default=18896)
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--authorize-disposable-account',action='store_true')
    args=parser.parse_args(argv)
    result=run(args)
    return 0 if result['status']=='AUTH_SMOKE_PASS_OPERATOR_ASSISTED' else 1


if __name__=='__main__':
    raise SystemExit(main())
