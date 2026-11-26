"""Isolated email engine bakeoff. No production settings, accounts or Docker stacks.

Run: python -m tools.email_engine_bakeoff run --all
Reports are generated in runtime/reports/email_engine_bakeoff/<run-id>/.
A partial result is never a release recommendation. Provider OAuth requires a
separate live gate; the disposable internal network cannot reach providers.
"""
from __future__ import annotations

import argparse
import json
import ipaddress
import re
import threading
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).with_name('email_engine_bakeoff_probe.py')
CANDIDATES = ('gofer', 'mailflow', 'cypht')
# Fixed inventory. Resource observations are not quality scores without a policy.
GROUPS = {
    'backend': (20, 'cold_ready recreate_ready headless_auth structured_accounts structured_messages structured_detail structured_body structured_search'),
    'provider': (15, 'imap_two_accounts smtp_delivery gmail_connect gmail_refresh outlook_connect outlook_refresh'),
    'message': (20, 'unified_inbox account_scope message_body raw_mime attachment_metadata attachment_bytes read_flag unread_flag star_flag unstar_flag send_reply move_message'),
    'sync': (15, 'initial_corpus new_delivery search_cross_account search_negative search_body pagination no_duplicates outgoing_sync'),
    'persistence': (10, 'session_recreate accounts_recreate messages_recreate fresh_delivery_recreate restart_recovery offline_recovery'),
    'security': (15, 'anonymous_accounts anonymous_messages anonymous_detail csrf_rejected credential_redaction cross_user_isolation'),
    'resource': (5, 'idle_cpu idle_memory loaded_sync_latency storage_overhead'),
}
INVENTORY = {test: group for group, (_, names) in GROUPS.items() for test in names.split()}
GATES = ('headless_auth', 'structured_messages', 'imap_two_accounts', 'accounts_recreate',
         'fresh_delivery_recreate', 'messages_recreate', 'smtp_delivery', 'anonymous_messages', 'cross_user_isolation')


def initial_checks():
    return {name: {'status': 'NOT_RUN', 'evidence': 'No observed test evidence'} for name in INVENTORY}


def score(checks):
    # Unknown is not failure or success. Report bounds, not a misleading rank.
    lower = upper = 0.0
    for group, (weight, names) in GROUPS.items():
        names = names.split()
        lower += weight * sum(checks[n]['status'] == 'PASS' for n in names) / len(names)
        upper += weight * sum(checks[n]['status'] != 'FAIL' for n in names) / len(names)
    return {'earned_points': round(lower, 2), 'possible_upper_bound': round(upper, 2),
            'coverage': sum(v['status'] in ('PASS', 'FAIL') for v in checks.values()),
            'mandatory_gates_pass': all(checks[n]['status'] == 'PASS' for n in GATES)}


def gofer_subnet(command_fn):
    """Choose a private /24 disjoint from all existing Docker IPv4 networks."""
    ids = command_fn(['docker', 'network', 'ls', '-q'], 30).split()
    existing = []
    if ids:
        for network in json.loads(command_fn(['docker', 'network', 'inspect', *ids], 30)):
            for config in (network.get('IPAM') or {}).get('Config') or []:
                if config.get('Subnet'):
                    subnet = ipaddress.ip_network(config['Subnet'], strict=False)
                    if subnet.version == 4:
                        existing.append(subnet)
    # Randomize the starting point to reduce collisions with concurrent runs.
    start = secrets.randbelow(65536)
    for offset in range(65536):
        index = (start + offset) % 65536
        subnet = ipaddress.ip_network(f'10.{index // 256}.{index % 256}.0/24')
        if not any(subnet.overlaps(other) for other in existing):
            return str(subnet)
    raise CommandError('No available private Gofer test subnet')


def configure_gofer_tls(spec, directory, secret):
    """Ephemeral CA and leaf for the isolated GreenMail fixture only.

    The CA private key is never persisted or mounted. Only GreenMail gets the
    encrypted server key; Gofer and the oracle get the public CA certificate.
    """
    from datetime import timedelta
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    directory = Path(directory)
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'MC disposable email bakeoff CA')])
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'fixture')])

    def builder(subject, issuer, key):
        return (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=10))
                .not_valid_after(now+timedelta(days=2)))

    ca = (builder(ca_name, ca_name, ca_key)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
               key_encipherment=False, data_encipherment=False, key_agreement=False,
               key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (builder(leaf_name, ca_name, leaf_key)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('fixture')]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    ca_path = directory / 'fixture-ca.pem'
    key_path = directory / 'fixture.p12'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(pkcs12.serialize_key_and_certificates(
        b'fixture', leaf_key, leaf, [ca], serialization.BestAvailableEncryption(secret.encode())))
    # The temporary parent is private; the non-root container needs read access.
    ca_path.chmod(0o644)
    key_path.chmod(0o644)

    services = spec['services']
    def mount(service, source, target):
        services[service].setdefault('volumes', []).append(
            {'type': 'bind', 'source': str(source), 'target': target, 'read_only': True})

    mount('fixture', key_path, '/tmp/bakeoff-fixture.p12')
    services['fixture']['environment']['GREENMAIL_OPTS'] += (
        ' -Dgreenmail.tls.keystore.file=/tmp/bakeoff-fixture.p12'
        f' -Dgreenmail.tls.keystore.password={secret} -Dgreenmail.tls.key.password={secret}')
    for service in ('engine', 'probe'):
        mount(service, ca_path, '/tmp/bakeoff-fixture-ca.pem')
    trust_var = 'NODE_EXTRA_CA_CERTS' if 'db' in services else 'SSL_CERT_FILE'
    services['engine']['environment'][trust_var] = '/tmp/bakeoff-fixture-ca.pem'
    services['probe']['environment']['BAKEOFF_FIXTURE_TLS'] = '1'
    return {'mode': 'implicit_tls', 'imap_port': 3993, 'smtp_port': 3465,
            'certificate_sha256': leaf.fingerprint(hashes.SHA256()).hex()}


def compose_spec(candidate, secret, messages, sync_timeout, subnet='10.203.84.0/24'):
    probe = {'image': 'python:3.12-slim', 'command': ['python', '-c', 'import time; time.sleep(86400)'],
             'environment': {'BAKEOFF_SECRET': secret, 'BAKEOFF_MESSAGES': str(messages),
                             'BAKEOFF_SYNC_TIMEOUT': str(sync_timeout)},
             'volumes': [{'type': 'bind', 'source': str(PROBE), 'target': '/probe.py', 'read_only': True}]}
    # Real test mail server; auth enabled, no host ports, no external networks.
    fixture = {'image': 'greenmail/standalone:2.1.15', 'environment': {'GREENMAIL_OPTS':
        '-Dgreenmail.setup.test.all -Dgreenmail.hostname=0.0.0.0 '
        f'-Dgreenmail.users=alpha:{secret}@lab.test,beta:{secret}@lab.test'}}
    services = {'probe': probe, 'fixture': fixture}
    volumes = {}
    network = {'internal': True}
    if candidate == 'gofer':
        net = ipaddress.ip_network(subnet)
        if net.version != 4 or net.prefixlen != 24 or not net.subnet_of(ipaddress.ip_network('10.0.0.0/8')):
            raise ValueError('Gofer requires an isolated RFC1918 /24')
        # Keep static addresses outside the dynamic allocation range.
        ip = str(net.network_address + 10)
        url = f'http://{ip}:8090'
        network['ipam'] = {'config': [{'subnet': str(net), 'ip_range': str(list(net.subnets(new_prefix=25))[1])}]}
        probe['environment']['BAKEOFF_GOFER_BASE_URL'] = url
        services['engine'] = {'image': 'ghcr.io/cristianadrielbraun/gofer:latest',
            'environment': {'GOFER_BASE_URL': url, 'GOFER_ALLOW_INSECURE_LAN': 'true', 'GOFER_AUTH_MODE': 'personal', 'GOFER_SETUP_TOKEN': secret},
            'networks': {'default': {'ipv4_address': ip}},
            'volumes': ['engine-data:/data']}
        volumes['engine-data'] = {}
    elif candidate == 'mailflow':
        services['db'] = {'image': 'postgres:16-alpine', 'environment': {'POSTGRES_DB': 'mailflow', 'POSTGRES_USER': 'mailflow', 'POSTGRES_PASSWORD': secret},
                          'volumes': ['db-data:/var/lib/postgresql/data'], 'healthcheck': {'test': ['CMD','pg_isready','-U','mailflow','-d','mailflow'], 'interval': '2s','timeout': '3s','retries': 60}}
        services['engine'] = {'image': 'ghcr.io/maathimself/mailflow-backend:latest',
            'environment': {'NODE_ENV': 'production', 'PORT': '3000', 'SESSION_SECRET': secret, 'ENCRYPTION_KEY': secret,
                            'APP_URL': 'http://engine:3000', 'FRONTEND_URL': 'http://engine:3000', 'TRUST_PROXY_HOPS': '1',
                            'DB_HOST': 'db', 'DB_PORT': '5432', 'DB_NAME': 'mailflow', 'DB_USER': 'mailflow', 'DB_PASSWORD': secret,
                            'FIRST_USER_REGISTRATION': 'true'},
            'depends_on': {'db': {'condition': 'service_healthy'}}}
        volumes['db-data'] = {}
    elif candidate == 'cypht':
        services['db'] = {'image': 'mariadb:10', 'environment': {'MYSQL_ROOT_PASSWORD': secret, 'MYSQL_DATABASE': 'cypht', 'MYSQL_USER': 'cypht', 'MYSQL_PASSWORD': secret},
                          'volumes': ['db-data:/var/lib/mysql'], 'healthcheck': {'test': ['CMD-SHELL','mariadb-admin ping -h localhost --silent'], 'interval': '2s','timeout': '3s','retries': 60}}
        services['engine'] = {'image': 'cypht/cypht:2.12.2', 'environment': {'AUTH_USERNAME': 'bakeoff', 'AUTH_PASSWORD': secret,
                            'DB_CONNECTION_TYPE': 'host','DB_DRIVER': 'mysql','DB_HOST': 'db','DB_NAME': 'cypht','DB_USER': 'cypht','DB_PASS': secret,
                            'SESSION_TYPE': 'DB','USER_CONFIG_TYPE': 'DB'},
                            'depends_on': {'db': {'condition': 'service_healthy'}}}
        volumes['db-data'] = {}
    else:
        raise ValueError(candidate)
    return {'services': services, 'volumes': volumes, 'networks': {'default': network}}


class CommandError(RuntimeError):
    pass


def safe_diagnostic(value, secret=''):
    """Never echo bootstrap credentials or raw token-bearing log lines."""
    value = str(value)
    if secret:
        value = value.replace(secret, '[REDACTED]')
    lines = []
    for line in value.splitlines():
        if re.search(r'token|password|passwd|secret|credential|authorization|cookie|api[-_ ]?key|bearer|https?://[^\s]+[?@]', line, re.I):
            lines.append('[sensitive diagnostic omitted]')
        else:
            lines.append(line[:500])
    return '\n'.join(lines)


def command(args, timeout=600, *, label=None, secret='', heartbeat=10):
    """Capture machine output, stream only trusted probe progress, and never read stdin."""
    try:
        proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace')
    except OSError as exc:
        raise CommandError(f'{args[0]} {args[1]}: {type(exc).__name__}') from None
    stdout, stderr = [], []
    def drain(stream, output, is_stderr=False):
        with stream:
            for line in stream:
                output.append(line)
                if label and is_stderr and re.fullmatch(r'BAKEOFF_PROGRESS (?:seed (?:alpha|beta) \d+/\d+|seed waiting (?:alpha|beta)|gofer hydrate \d+/\d+|gofer grouping (?:originals=\d+/\d+|blocked)|(?:gofer|mailflow) recovery attempt=\d+ (?:originals=\d+/\d+ duplicates=\d+ wrong_account=\d+ unavailable_sources=\d+|inventory_unavailable=[A-Za-z0-9_]+)|test [a-z_]+ (?:BEGIN|PASS|FAIL|BLOCKED))\s*', line):
                    print(f'{label} {line.strip()}', flush=True)
    threads = [threading.Thread(target=drain, args=(proc.stdout, stdout), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, stderr, True), daemon=True)]
    for thread in threads:
        thread.start()
    start = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                proc.wait(timeout=min(heartbeat, remaining))
                break
            except subprocess.TimeoutExpired:
                if label:
                    print(f'{label} WAIT elapsed={int(time.monotonic()-start)}s timeout={timeout}s', flush=True)
    except BaseException as exc:
        proc.kill()
        proc.wait()
        for thread in threads:
            thread.join(timeout=2)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise CommandError(f'{args[0]} {args[1]}: timeout after {timeout}s') from None
        raise
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        raise CommandError('Subprocess output did not close')
    if proc.returncode:
        raise CommandError(f'{args[0]} {args[1]}: exit={proc.returncode}; {safe_diagnostic("".join(stderr)[-1500:], secret)}')
    return ''.join(stdout)


def await_ready(candidate, compose, probe, *, timeout=180, interval=2, emit=print):
    """Check container liveness before and after each bounded HTTP attempt."""
    deadline = time.monotonic() + timeout
    attempt = 0
    def check_state():
        ids = compose('ps', '-a', '-q', 'engine', timeout=15).split()
        if len(ids) != 1:
            raise CommandError('Expected exactly one engine container')
        state = json.loads(compose('ps', '-a', '--format', 'json', 'engine', timeout=15).strip())
        # Compose versions emit either a single JSON object or an array.
        if isinstance(state, list):
            if len(state) != 1:
                raise CommandError('Expected exactly one engine state')
            state = state[0]
        status = str(state.get('State', '')).lower()
        if status in ('exited', 'dead', 'removing', 'paused', 'restarting'):
            raise CommandError(f'Engine state={status} exit={state.get("ExitCode", "unknown")}')
        if status not in ('running', 'created'):
            raise CommandError(f'Unexpected engine state={status}')
        return status
    while True:
        state = check_state()
        attempt += 1
        emit(f'HTTP_PROBE attempt={attempt} container={state}')
        if state == 'running':
            observed = probe('ready-once', candidate, timeout=20)
            check_state()
            if observed.get('http') == 200 and observed.get('scope') == 'HTTP readiness only':
                emit('READY http=200')
                return observed
            emit(f'HTTP_NOT_READY status={observed.get("http", "unavailable")} bytes={observed.get("response_bytes", "unknown")}')
        if time.monotonic() >= deadline:
            raise CommandError(f'HTTP readiness deadline exceeded ({timeout}s)')
        time.sleep(min(interval, max(0, deadline-time.monotonic())))


def write_report(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding='utf-8')
    tmp.replace(path)


def run_candidate(candidate, project, args, report_path):
    secret = secrets.token_hex(32)
    result = {'candidate': candidate, 'project': project, 'checks': initial_checks(), 'metrics': {}, 'images': {}, 'errors': []}
    checks = result['checks']
    def mark(name, status, evidence):
        checks[name] = {'status': status, 'evidence': evidence}
    def save():
        result['score'] = score(checks)
        write_report(report_path, result)
    def redact(text):
        return str(text).replace(secret, '[REDACTED]')
    with tempfile.TemporaryDirectory(prefix='mc-email-bakeoff-') as tmp:
        file = Path(tmp) / 'compose.json'
        spec = None
        stage = 'CONFIGURE'
        def emit(message):
            print(f'EMAIL_BAKEOFF {candidate} {message}', flush=True)
        base = ['docker','compose','--project-name',project,'--file',str(file)]
        def compose(*cmd, timeout=600):
            return command(base + list(cmd), timeout, label=f'EMAIL_BAKEOFF {candidate} {stage}', secret=secret)
        def probe(*cmd, timeout=600):
            raw = compose('exec','-T','probe','python','/probe.py',*cmd,timeout=timeout)
            return json.loads(raw)
        def merge(observed):
            for name, record in observed.get('checks', {}).items():
                if name not in checks or record['status'] not in ('PASS','FAIL','NOT_RUN','BLOCKED'):
                    raise ValueError('Unexpected probe result')
                checks[name] = record
            result['metrics'].update(observed.get('metrics', {}))
            save()
        def resources(label):
            # Exclude fixture and probe. No fabricated resource grades.
            ids = compose('ps','-q','engine',*(['db'] if 'db' in spec['services'] else [])).split()
            if len(ids) != (2 if 'db' in spec['services'] else 1):
                raise CommandError('Missing engine or database container')
            samples = []
            until = time.monotonic() + args.idle_seconds
            while True:
                emit(f'RESOURCES {label} sample={len(samples)+1}')
                raw = command(['docker','stats','--no-stream','--format','{{json .}}',*ids],30)
                samples.append([json.loads(line) for line in raw.splitlines() if line.strip()])
                if time.monotonic() >= until:
                    break
                time.sleep(min(10, max(0, until-time.monotonic())))
            result['metrics'][label] = {'samples': samples, 'window_seconds': args.idle_seconds,
                                        'includes_database': 'db' in spec['services']}
            save()
        try:
            subnet = gofer_subnet(command) if candidate == 'gofer' else None
            spec = compose_spec(candidate, secret, args.messages, args.sync_timeout, subnet=subnet)
            if candidate in ('gofer','mailflow'):
                result['metrics']['fixture_transport'] = configure_gofer_tls(spec, tmp, secret)
            file.write_text(json.dumps(spec), encoding='utf-8')
            stage = 'PULL'
            emit(stage)
            compose('pull', timeout=1200)
            for service, config in spec['services'].items():
                image = config['image']
                result['images'][service] = {'tag': image, 'id': command(['docker','image','inspect','--format','{{.Id}}',image]).strip(),
                    'repo_digests': json.loads(command(['docker','image','inspect','--format','{{json .RepoDigests}}',image]))}
                # Run the observed image ID, not a mutable tag.
                config['image'] = result['images'][service]['id']
            file.write_text(json.dumps(spec), encoding='utf-8')
            save()
            stage = 'FIXTURE_START'
            emit(stage)
            compose('up','-d','--no-build','--pull','never','fixture','probe')
            stage = 'SEED'
            emit(f'{stage} {args.messages}')
            result['fixture'] = probe('seed', timeout=max(600,args.messages // 5))
            if result['fixture'].get('status') != 'PASS' or result['fixture'].get('messages') != args.messages:
                raise CommandError('Fixture oracle validation failed')
            save()
            stage = 'COLD_START'
            emit(stage)
            started = time.monotonic()
            compose('up','-d','--no-build','--pull','never','engine')
            emit('CONTAINER_START_RETURNED')
            ready = await_ready(candidate, compose, probe, emit=emit)
            result['metrics']['cold_start_seconds'] = round(time.monotonic()-started,3)
            mark('cold_ready','PASS',ready)
            stage = 'RESOURCES_UNCONFIGURED'
            resources('unconfigured_idle')
            if candidate in ('mailflow','gofer'):
                stage = 'NATIVE_API'
                emit(stage)
                merge(probe(candidate,timeout=2*args.sync_timeout+1800))
                if result['metrics'].get('adapter_blocker'):
                    detail = safe_diagnostic(result['metrics']['adapter_blocker'],secret)
                    if '[sensitive diagnostic omitted]' in detail:
                        stage_hint = result['metrics'].get('adapter_blocker_stage')
                        if stage_hint in ('owner_setup','mailbox_enrollment'):
                            detail = f'{stage_hint}: diagnostic text withheld from console; see metrics.adapter_blocker in candidate report'
                    result['errors'].append(detail)
                    emit(f'ADAPTER_BLOCKED {detail}')
                if checks['initial_corpus']['status'] == 'PASS':
                    resources('configured_idle')
            else:
                result['errors'].append('Mailbox API adapter not implemented; no functional credit from readiness or fixture success.')
            stage = 'RECREATE'
            emit(stage)
            started = time.monotonic()
            # Recreate engine AND backing database, keeping only this project's volumes.
            recreate = ['engine'] + (['db'] if 'db' in spec['services'] else [])
            before = {s: compose('ps','-q',s).strip() for s in recreate}
            compose('up','-d','--no-build','--pull','never','--force-recreate',*recreate)
            emit('CONTAINER_START_RETURNED')
            ready = await_ready(candidate, compose, probe, emit=emit)
            after = {s: compose('ps','-q',s).strip() for s in recreate}
            if not all(before[s] and after[s] and before[s] != after[s] for s in recreate):
                raise CommandError('Container recreation not established')
            mark('recreate_ready','PASS',ready)
            result['metrics']['recreate_seconds'] = round(time.monotonic()-started,3)
            if candidate in ('mailflow','gofer') and checks['headless_auth']['status'] == 'PASS':
                merge(probe(candidate+'-recreate',timeout=(3*args.sync_timeout+300 if candidate=='gofer' else args.sync_timeout+300)))
        except (CommandError, ValueError, KeyError, OSError, TypeError, ImportError) as exc:
            detail = safe_diagnostic(exc, secret)
            result['errors'].append(f'{stage}: {detail}')
            emit(f'BLOCKED stage={stage} reason={detail}')
            if stage in ('COLD_START', 'RECREATE'):
                mark('cold_ready' if stage == 'COLD_START' else 'recreate_ready', 'BLOCKED', detail)
            if file.exists():
                try:
                    logs = safe_diagnostic(compose('logs','--no-color','--tail','30','engine',timeout=20),secret)
                    if logs:
                        result['diagnostics'] = logs
                        emit(f'ENGINE_LOGS\n{logs}')
                except CommandError as log_exc:
                    emit(f'LOGS_UNAVAILABLE {safe_diagnostic(log_exc,secret)}')
        finally:
            # No global prune, no existing stack names, no production volume references.
            stage = 'CLEANUP'
            emit(stage)
            try:
                if file.exists():
                    compose('down','--volumes','--remove-orphans',timeout=180)
                result['cleanup'] = 'PASS'
            except CommandError as exc:
                result['cleanup'] = 'FAIL'
                result['errors'].append(safe_diagnostic(exc, secret))
                emit(f'CLEANUP_FAILED {safe_diagnostic(exc,secret)}')
            save()
    print(f'EMAIL_BAKEOFF {candidate} coverage={result["score"]["coverage"]}/50 gates={result["score"]["mandatory_gates_pass"]}', flush=True)
    return result


# Core shared observations, not a substitute for the entire 50-test inventory.
PARITY_CHECKS = ('cold_ready','recreate_ready','headless_auth','structured_messages',
    'structured_detail','structured_body','structured_search','imap_two_accounts',
    'unified_inbox','account_scope','initial_corpus','no_duplicates','search_cross_account',
    'search_negative','message_body','raw_mime','attachment_metadata','attachment_bytes',
    'read_flag','unread_flag','star_flag','unstar_flag','smtp_delivery','send_reply',
    'new_delivery','outgoing_sync','session_recreate','accounts_recreate','messages_recreate',
    'fresh_delivery_recreate','anonymous_messages','anonymous_detail')
PARITY_SECURITY = ('anonymous_accounts','csrf_rejected','credential_redaction','cross_user_isolation')


def memory_mib(value):
    match=re.fullmatch(r'\s*([0-9.]+)\s*(B|kB|KB|KiB|MB|MiB|GB|GiB|TB|TiB)\s*',value.split('/')[0])
    if not match: raise ValueError('Unknown Docker memory unit')
    scales={'B':1,'kB':1000,'KB':1000,'KiB':1024,'MB':10**6,'MiB':2**20,'GB':10**9,'GiB':2**30,'TB':10**12,'TiB':2**40}
    return float(match[1])*scales[match[2]]/2**20


def resource_summary(metrics):
    block=metrics.get('configured_idle',{})
    samples=block.get('samples',[])
    expected=2 if block.get('includes_database') else 1
    try:
        if not samples or any(len(sample)!=expected for sample in samples): return None
        mem=[sum(memory_mib(row['MemUsage']) for row in sample) for sample in samples]
        cpu=[sum(float(row['CPUPerc'].rstrip('%')) for row in sample) for sample in samples]
        return {'samples':len(samples),'includes_database':bool(block.get('includes_database')),
            'window_seconds':block.get('window_seconds'),'mean_total_memory_mib':round(sum(mem)/len(mem),3),
            'mean_total_cpu_percent':round(sum(cpu)/len(cpu),3),'max_total_memory_mib':round(max(mem),3)}
    except (ValueError,TypeError,KeyError,ZeroDivisionError): return None


def compare_finalists(results,count):
    candidates=('gofer','mailflow')
    matrix={name:{c:results[c]['checks'][name]['status'] for c in candidates} for name in PARITY_CHECKS+PARITY_SECURITY}
    core_pass=[name for name in PARITY_CHECKS if all(v=='PASS' for v in matrix[name].values())]
    failure=[name for name in PARITY_CHECKS if 'FAIL' in matrix[name].values()]
    transport=all(results[c].get('fixture',{}).get('status')=='PASS' and results[c]['fixture'].get('messages')==count
        and results[c]['fixture'].get('transport')=='implicit_tls'
        and results[c]['metrics'].get('fixture_transport',{}).get('mode')=='implicit_tls'
        and results[c]['metrics']['fixture_transport'].get('imap_port')==3993
        and results[c]['metrics']['fixture_transport'].get('smtp_port')==3465 for c in candidates)
    transport = transport and results['mailflow']['metrics'].get('native_tls_account_readback')=='PASS'
    pre=all(results[c]['metrics'].get('pre_recreate_mailbox',{}).get('verified') is True for c in candidates)
    core_ok=transport and pre and len(core_pass)==len(PARITY_CHECKS)
    security_ok=all(all(v=='PASS' for v in matrix[name].values()) for name in PARITY_SECURITY)
    return {'status':'LAB_PARITY_PASS' if core_ok else ('LAB_PARITY_FAIL' if failure else 'LAB_PARITY_INCOMPLETE'),
        'same_tls_fixture_profile':transport,'pre_recreate_corpus_verified_both':pre,
        'shared_core_pass':len(core_pass),'shared_core_total':len(PARITY_CHECKS),'matrix':matrix,
        'core_not_passed':[name for name in PARITY_CHECKS if name not in core_pass],
        'additional_security_parity':'PASS' if security_ok else 'INCOMPLETE',
        'full_inventory_pass_both':all(all(v['status']=='PASS' for v in results[c]['checks'].values()) for c in candidates),
        'resources':{c:resource_summary(results[c]['metrics']) for c in candidates},
        'enrollment_to_full_corpus_seconds':{c:results[c]['metrics'].get('enrollment_to_full_corpus_seconds') for c in candidates},
        'provider_certification':'NOT_RUN','production_certification':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('plan')
    run = sub.add_parser('run')
    choice = run.add_mutually_exclusive_group(required=True)
    choice.add_argument('--all', action='store_true')
    choice.add_argument('--finalists', action='store_true', help='Compare Gofer and MailFlow using the identical TLS functional profile')
    choice.add_argument('--candidate', choices=CANDIDATES)
    run.add_argument('--messages', type=int, default=100, help='Total fixture messages, evenly split between accounts')
    run.add_argument('--idle-seconds', type=int, default=15)
    run.add_argument('--sync-timeout', type=int, default=600)
    args = parser.parse_args(argv)
    if args.action == 'plan':
        print(json.dumps({'candidates': CANDIDATES, 'inventory': GROUPS, 'mandatory_gates': GATES},indent=2))
        return 0
    if not (2 <= args.messages <= 100000 and args.messages % 2 == 0 and 0 <= args.idle_seconds <= 3600 and 30 <= args.sync_timeout <= 3600):
        parser.error('messages must be even (2–100000), idle 0–3600, sync timeout 30–3600')
    if args.finalists and args.messages > 200:
        parser.error('Finalist full-MIME functional parity profile supports at most 200 messages; bulk performance profile not yet certified')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4)
    path = ROOT / 'runtime' / 'reports' / 'email_engine_bakeoff' / stamp
    path.mkdir(parents=True)
    summary = {'status': 'INCOMPLETE', 'winner': None, 'production_modified': False,
               'live_provider_certification': {p:'NOT_RUN' for p in ('generic_tls_imap_smtp','generic_pop3','gmail','outlook','yahoo')},
               'parameters': {'messages':args.messages,'idle_seconds':args.idle_seconds,'sync_timeout':args.sync_timeout}, 'candidates': {}}
    try:
        command(['docker','info','--format','{{.ServerVersion}}'],30)
        command(['docker','compose','version'],30)
    except CommandError as exc:
        summary['status'] = 'BLOCKED_DOCKER'
        summary['error'] = str(exc)
        write_report(path/'summary.json',summary)
        print(f'EMAIL_BAKEOFF BLOCKED_DOCKER report={path / "summary.json"}',flush=True)
        return 2
    observed = {}
    selected = CANDIDATES if args.all else (('gofer','mailflow') if args.finalists else (args.candidate,))
    for candidate in selected:
        result = run_candidate(candidate, f'mc-email-{candidate}-{secrets.token_hex(6)}',args,path/f'{candidate}.json')
        observed[candidate] = result
        summary['candidates'][candidate] = {'score':result['score'],'cleanup':result['cleanup'],'errors':result['errors']}
        if all(c in observed for c in ('gofer','mailflow')):
            summary['finalist_parity'] = compare_finalists(observed,args.messages)
            print('EMAIL_BAKEOFF FINALISTS '+summary['finalist_parity']['status'],flush=True)
        write_report(path/'summary.json',summary)
    print(f'EMAIL_BAKEOFF INCOMPLETE report={path / "summary.json"}',flush=True)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
