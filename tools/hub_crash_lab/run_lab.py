"""Two-lane Hub crash lab: post-commit restart and in-flight post-commit kill.

All fault injection lives in lab child processes; normal Hub modules unchanged.
No test in this tool broadcasts an on-chain payout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STABLE_TOPOLOGY = ROOT / 'deploy/hub-topology/smoke-topology.json'
DEFAULT_STABLE_TESTS = (
    'tests/test_stable_hub_lab_contract.py',
    'tests/test_stable_hub_fdb_atomic_accounting.py',
    'tests/test_stable_hub_integrated_market_stress.py',
    'tests/test_stable_hub_credit_bridge_patch2.py',
    'tests/test_stable_hub_historical_funding_acceptance.py',
)
STABLE_FILES = ('main_computer/stable_hub.py', 'main_computer/stable_hub_topology.py',
                'main_computer/stable_hub_worker_sessions.py', 'tools/stable_hub_lab/run_lab.py')


class LabError(RuntimeError):
    pass


def stable_fingerprint(root: Path = ROOT) -> dict[str, str]:
    """Fail closed if the expected Stable source files are absent."""
    result = {}
    relatives = set(STABLE_FILES)
    relatives.update(str(path.relative_to(root)).replace('\\', '/') for path in
                     (root / 'main_computer').glob('stable_hub_*.py'))
    relatives.update(str(path.relative_to(root)).replace('\\', '/') for path in
                     (root / 'tools/stable_hub_lab').glob('*.py'))
    for rel in sorted(relatives):
        path = root / rel
        if not path.is_file():
            raise LabError(f'Stable baseline source missing: {path}')
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _only_loopback(url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    return host in ('127.0.0.1', 'localhost', '::1')


def _ports_free(ports: list[int]) -> None:
    for port in ports:
        if not 1024 <= port <= 65535:
            raise LabError(f'Invalid dev-lab port: {port}')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', port))
            except OSError as exc:
                raise LabError(f'Port {port} already occupied; refusing to mix lab and existing services') from exc


def _status(url: str, *, timeout: float = 2.0) -> dict[str, Any]:
    if not _only_loopback(url):
        raise LabError(f'Non-loopback HTTP request refused: {url}')
    with urlopen(url, timeout=timeout) as response:
        body = json.loads(response.read())
    if not isinstance(body, dict):
        raise LabError(f'Expected JSON object from {url}')
    return body


def _wait_health(urls: list[str], *, service: str, cluster_id: str, deadline: float,
                 processes: list[subprocess.Popen] | None = None) -> None:
    pending = set(urls)
    last_error = ''
    while pending and time.monotonic() < deadline:
        for proc in processes or []:
            if proc.poll() is not None:
                raise LabError(f'Hub process {proc.pid} exited early (code={proc.returncode})')
        for url in tuple(pending):
            try:
                h = _status(url + '/health')
                identity = _status(url + '/api/hub/v1/hub-identity')
                if (h.get('ok') is True and h.get('service') == service
                        and identity.get('service') == service
                        and identity.get('cluster_id') == cluster_id):
                    pending.remove(url)
                else:
                    last_error = f'{url} unexpected identity: health={h} identity={identity}'
            except (OSError, ValueError, LabError) as exc:
                last_error = f'{url}: {type(exc).__name__}: {exc}'
        if pending:
            time.sleep(.25)
    if pending:
        raise LabError(f'Health timeout waiting for {sorted(pending)}: {last_error}')


def _docker_engine_status() -> tuple[bool, str]:
    """Check the actual daemon, not merely the presence of docker.exe."""
    try:
        result = subprocess.run(
            ['docker', 'info', '--format', '{{.ServerVersion}}'],
            capture_output=True, text=True, timeout=7, errors='replace',
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f'{type(exc).__name__}: {exc}'
    return result.returncode == 0, (result.stderr or result.stdout or '').strip()[-400:]


def _start_docker_desktop_for_local_lab() -> None:
    """Start an existing Windows Docker Desktop install, without altering its configuration."""
    if os.name != 'nt':
        raise LabError('Local Docker FoundationDB requires a running Docker daemon; '
                       'automatic Docker Desktop launch is supported on Windows only. No Hub started.')
    candidates = [
        Path(os.environ.get('PROGRAMFILES', '')) / 'Docker/Docker/Docker Desktop.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/DockerDesktop/Docker Desktop.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Docker/Docker Desktop.exe',
    ]
    desktop = next((path for path in candidates if path.is_file()), None)
    if desktop is None:
        raise LabError('Docker daemon is unavailable and Docker Desktop.exe was not found; '
                       'install Docker Desktop or configure its location. No Hub started.')
    print(f'Crash lab: starting installed Docker Desktop ({desktop})', flush=True)
    try:
        subprocess.Popen([str(desktop)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise LabError(f'Could not launch Docker Desktop: {exc}. No Hub started.') from exc
    deadline = time.monotonic() + 150.0
    detail = 'not ready'
    while time.monotonic() < deadline:
        ready, detail = _docker_engine_status()
        if ready:
            print('Crash lab: Docker engine ready', flush=True)
            return
        time.sleep(3.0)
    raise LabError(f'Docker Desktop did not become ready within 150 seconds: {detail}. No Hub started.')


def _local_fdb_coordinator_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _require_local_docker_engine() -> None:
    """Refuse to bootstrap a lab database on a remote Docker context."""
    explicit_host = os.environ.get('DOCKER_HOST', '').strip()
    if explicit_host:
        endpoint = explicit_host
    else:
        try:
            result = subprocess.run(
                ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                capture_output=True, text=True, timeout=7, errors='replace',
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LabError(f'Cannot verify the local Docker endpoint: {exc}. No Hub started.') from exc
        if result.returncode != 0:
            raise LabError('Cannot verify Docker context endpoint: '
                           f'{(result.stderr or result.stdout).strip()[-300:]}. No Hub started.')
        endpoint = result.stdout.strip()
    from urllib.parse import urlsplit
    address = urlsplit(endpoint)
    if not (address.scheme in ('npipe', 'unix')
            or (address.scheme == 'tcp' and address.hostname in ('localhost', '127.0.0.1', '::1'))):
        raise LabError(f'Docker endpoint {endpoint!r} is not local; refusing to start the lab FDB '
                       'container outside this machine. No Hub started.')


def _start_existing_local_fdb_bootstrap(cluster_file: Path, *, port: int) -> None:
    """Reuse the repository's existing Docker FDB startup; never reset its container."""
    from scripts.smoke_foundationdb_credit_ledger_primitives import (
        parse_args, start_foundationdb_container, SmokeFailure,
    )
    args = parse_args([
        '--cluster-file', str(cluster_file), '--fdb-port', str(port),
        '--reuse-container', '--keep-container',
        '--no-bootstrap-native-client',
    ])
    print(f'Crash lab: starting/reusing existing local FoundationDB on 127.0.0.1:{port}', flush=True)
    try:
        runtime = start_foundationdb_container(args)
    except (SmokeFailure, OSError, ValueError, subprocess.SubprocessError) as exc:
        raise LabError(f'Local FoundationDB bootstrap failed: {exc}. No Hub started.') from exc
    if runtime.cluster_file.resolve() != cluster_file.resolve():
        raise LabError('FoundationDB bootstrap selected the wrong cluster file. No Hub started.')


def _preflight_local_docker_fdb(cluster_file: Path) -> None:
    """Auto-start only the known loopback Docker FDB used by the isolated lab.

    For all other cluster descriptors, never launch Docker or mutate the cluster.
    """
    description = cluster_file.read_text(encoding='utf-8').strip()
    if '@' not in description:
        raise LabError(f'Invalid FoundationDB cluster descriptor: {cluster_file}')
    cluster_id, coordinators = description.split('@', 1)
    if cluster_id != 'docker:docker':
        return
    if ',' in coordinators:
        raise LabError('Automatic local Docker FDB startup requires one loopback coordinator; '
                       'refusing ambiguous cluster file. No Hub started.')
    host, separator, port_text = coordinators.strip().rpartition(':')
    if not separator or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise LabError(f'Invalid FoundationDB coordinator in {cluster_file}: {coordinators}')
    if host not in ('127.0.0.1', 'localhost'):
        return  # A remote coordinator is not the Docker smoke container.
    port = int(port_text)
    if host != '127.0.0.1':
        raise LabError('Automatic local Docker FDB startup requires 127.0.0.1, '
                       'matching the existing bootstrap contract. No Hub started.')
    ready, detail = _docker_engine_status()
    if not ready:
        print(f'Crash lab: Docker daemon unavailable ({detail}); starting Docker Desktop', flush=True)
        _start_docker_desktop_for_local_lab()
    if not _local_fdb_coordinator_ready(host, port):
        _require_local_docker_engine()
        _start_existing_local_fdb_bootstrap(cluster_file, port=port)
    if not _local_fdb_coordinator_ready(host, port):
        raise LabError(f'Local Docker FoundationDB coordinator {host}:{port} remained unreachable '
                       'after bootstrap. No Hub started.')
    print(f'Crash lab: local FoundationDB coordinator ready at {host}:{port}', flush=True)


def _run_baseline(out: Path, stable_files: dict[str, str], *, timeout: float,
                  stable_tests: tuple[str, ...] = DEFAULT_STABLE_TESTS) -> dict[str, Any]:
    command = [sys.executable, '-m', 'pytest', '-q', *stable_tests]
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                                   errors='replace')
        payload = {'ok': completed.returncode == 0, 'command': command,
                   'exit_code': completed.returncode, 'stdout': completed.stdout[-12000:],
                   'stderr': completed.stderr[-12000:], 'stable_sources_sha256': stable_files}
    except subprocess.TimeoutExpired as exc:
        payload = {'ok': False, 'command': command, 'error': f'baseline timed out after {timeout}s',
                   'stdout': str(exc.stdout or '')[-12000:], 'stderr': str(exc.stderr or '')[-12000:],
                   'stable_sources_sha256': stable_files}
    _json_atomic(out / 'stable-baseline.json', payload)
    return payload


def _topology(base: Path, target: Path, *, run_id: str, lane: str, ports: list[int],
              cluster_file: Path) -> dict[str, Any]:
    raw = json.loads(base.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or len(raw.get('hubs', [])) != len(ports):
        raise LabError('Expected exactly three nodes in Stable smoke topology')
    network = raw.get('network') or {}
    if (network.get('network_kind') != 'smoke' or
            not _only_loopback(str(network.get('chain_rpc_url') or '')) or
            (raw.get('contract') or {}).get('topology_scope') != 'local-smoke-lab'):
        raise LabError('Refusing non-smoke/non-loopback Stable topology in crash lab')
    raw['cluster_id'] = f'crash-lab-{lane}-{run_id}'
    raw['storage']['cluster_file'] = str(cluster_file.resolve())
    raw['storage']['namespace'] = f'mc-hub-crash-{lane}-{run_id}'
    raw['network']['network_key'] = f'crash-{lane}-{run_id}'
    raw['entry_urls'] = [f'http://127.0.0.1:{port}' for port in ports]
    for node, port in zip(raw['hubs'], ports):
        node['hub_url'] = f'http://127.0.0.1:{port}'
        node['public_url'] = node['hub_url']
    _json_atomic(target, raw)
    return raw


def _stop(proc: subprocess.Popen | None, *, kill: bool) -> None:
    if proc is None or proc.poll() is not None:
        return
    if kill:
        proc.kill()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=8)


def _start(cmd: list[str], *, log: Path, env: dict[str, str]) -> tuple[subprocess.Popen, Any]:
    log.parent.mkdir(parents=True, exist_ok=True)
    f = log.open('a', encoding='utf-8')
    try:
        proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=f, stderr=subprocess.STDOUT)
        return proc, f
    except BaseException:
        f.close()
        raise


def _stable_snapshot(results: dict[str, Any]) -> list[dict[str, str]]:
    if results.get('ok') is not True:
        raise LabError('Stable integrated workload failed')
    observations = []
    for phase in ('success', 'failure', 'reconnect_success'):
        item = (results.get('traffic') or {}).get(phase) or {}
        continuation = item.get('continuation') or {}
        accepted = continuation.get('accepted_session') or {}
        session_id = accepted.get('session_id') or (item.get('response') or {}).get('session_id')
        hub_url = (item.get('response') or {}).get('continuation_url', '')
        if not session_id or not _only_loopback(hub_url):
            raise LabError(f'Missing Stable persistent session/continuation for {phase}')
        payout = accepted.get('payout') or {}
        observations.append({'phase': phase, 'url': hub_url, 'session_id': session_id,
                             'status': str(continuation.get('status') or accepted.get('status') or ''),
                             'hold_id': str(payout.get('hold_id') or ''),
                             'charge_id': str(payout.get('charge_id') or ''),
                             'worker_earning_id': str(payout.get('worker_earning_id') or '')})
    return observations


def _exp_snapshot(results: dict[str, Any]) -> list[dict[str, str]]:
    if results.get('ok') is not True:
        raise LabError('Experimental handoff workload failed')
    observations = []
    for idx, item in enumerate(results.get('requests') or []):
        response = item.get('response') or {}
        accepted = (item.get('continuation') or {}).get('accepted_session') or {}
        sid = response.get('session_id') or (item.get('offer') or {}).get('session_id')
        url = str(item.get('worker_hub_url') or '')
        if not sid or not _only_loopback(url):
            raise LabError(f'Missing Experimental canonical session/worker URL at request {idx}')
        continuation = item.get('continuation') or {}
        payout = (item.get('continuation') or {}).get('payout') or accepted.get('payout') or {}
        observations.append({'phase': f'request_{idx}', 'url': f'{url}/api/hub/v1/work/sessions/{sid}/stream',
                             'session_id': sid, 'request_id': str((item.get('offer') or {}).get('request_id') or ''), 'status': str(continuation.get('status') or accepted.get('status') or ''),
                             'hold_id': str(payout.get('hold_id') or ''),
                             'charge_id': str(payout.get('charge_id') or ''),
                             'worker_earning_id': str(payout.get('worker_earning_id') or '')})
    if not observations:
        raise LabError('Experimental workload produced no persistable accepted sessions')
    return observations


def _persistent_fields(doc: dict[str, Any]) -> dict[str, str]:
    accepted = doc.get('accepted_session') or {}
    payout = doc.get('payout') or accepted.get('payout') or {}
    return {'session_id': str(accepted.get('session_id') or doc.get('session_id') or ''),
            'status': str(doc.get('status') or accepted.get('status') or ''),
            'hold_id': str(payout.get('hold_id') or ''),
            'charge_id': str(payout.get('charge_id') or ''),
            'worker_earning_id': str(payout.get('worker_earning_id') or '')}


def _assert_same(observations: list[dict[str, str]]) -> list[dict[str, Any]]:
    checks = []
    for record in observations:
        observed = _persistent_fields(_status(record['url']))
        expected = {key: record[key] for key in ('session_id', 'status', 'hold_id', 'charge_id', 'worker_earning_id')}
        result = {'phase': record['phase'], 'ok': observed == expected,
                  'expected': expected, 'actual': observed}
        checks.append(result)
    if not checks or not all(item['ok'] for item in checks):
        raise LabError('Persisted accepted-session identity/status/payout differs after restart')
    return checks


def _stable_financial_snapshot(base_url: str) -> dict[str, Any]:
    doc = _status(base_url + '/api/hub/v1/payout/status')
    payout = doc.get('payout') or {}
    if not isinstance(payout, dict):
        raise LabError('Stable payout/status has no payout ledger')
    keys = ('accounts', 'holds', 'charges', 'worker_earnings', 'worker_claims',
            'settlements', 'bridge_payouts', 'transactions', 'deposits', 'bridge_audit')
    result = {}
    for key in keys:
        entries = payout.get(key, [])
        if not isinstance(entries, list):
            raise LabError(f'Stable payout ledger {key} is not a list')
        # Isolated namespace, no independent writers during this lab. Compare
        # exact core financial rows to detect lost/duplicated settlement records.
        result[key] = sorted(entries, key=lambda row: json.dumps(row, sort_keys=True))
    if not result['charges'] or not result['worker_earnings']:
        raise LabError('Stable baseline produced no payable credit records')
    return result


def _exp_charge_snapshot(observations: list[dict[str, str]]) -> list[dict[str, Any]]:
    checks = []
    for obs in observations:
        if not obs.get('request_id'):
            raise LabError('Experimental canonical request ID missing')
        from urllib.parse import urlparse
        parsed = urlparse(obs['url'])
        endpoint = f'{parsed.scheme}://{parsed.netloc}/api/hub/v1/requests/{obs["request_id"]}/charges'
        checks.append(_status(endpoint))
    return checks


def _run_lane(lane: str, *, out: Path, run_id: str, cluster_file: Path, stable_topology: Path,
              port_base: int, timeout: float, stable_files: dict[str, str],
              rounds: int) -> dict[str, Any]:
    from tools.stable_hub_lab import run_lab as stable_lab
    from tools.exp_hub_lab import run_lab as exp_lab

    ports = [port_base + i for i in range(3)]
    _ports_free(ports)
    lane_out = out / lane
    lane_out.mkdir(parents=True, exist_ok=True)
    topology_path = lane_out / 'isolated-topology.json'
    topology = _topology(stable_topology, topology_path, run_id=run_id, lane=lane,
                         ports=ports, cluster_file=cluster_file)
    urls = list(topology['entry_urls'])
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'
    # This first crash gate is intentionally FDB-only. Temporal and on-chain
    # interruption get their own gates; we do not claim they were tested here.
    env.pop('MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS', None)
    children: list[subprocess.Popen] = []
    logs: list[Any] = []
    events: list[dict[str, Any]] = []

    def log_event(item: dict[str, Any]) -> None:
        events.append({'recorded_unix_seconds': time.time(), **item})
        _json_atomic(lane_out / 'events.json', {'lane': lane, 'events': events,
                                                'namespace': topology['storage']['namespace']})

    def spawn(index: int, generation: int) -> subprocess.Popen:
        if lane == 'stable':
            cmd = [sys.executable, '-u', '-m', 'main_computer.stable_hub',
                   '--topology', str(topology_path), '--hub-id', topology['hubs'][index]['hub_id']]
        else:
            # Experimental's three ports intentionally share one OS process.
            cmd = [sys.executable, '-u', '-m', 'main_computer.exp_fdb_hub',
                   '--host', '127.0.0.1', '--ports', ','.join(map(str, ports)),
                   '--topology', str(topology_path), '--namespace', topology['storage']['namespace'],
                   '--cluster-file', str(cluster_file), '--hub-root', str(lane_out / 'hub-runtime'),
                   '--network-key', topology['network']['network_key'],
                   '--network-kind', 'experimental', '--bridge-backend', 'mock-chain',
                   '--no-fdb-autostart', '--require-multisession-auth']
        proc, log = _start(cmd, log=lane_out / f'process-{index}-generation-{generation}.log', env=env)
        logs.append(log)
        log_event({'event': 'started', 'generation': generation, 'index': index, 'pid': proc.pid, 'cmd': cmd})
        return proc

    try:
        for i in range(3 if lane == 'stable' else 1):
            children.append(spawn(i, 0))
        _wait_health(urls, service=f'main_computer.{"stable_hub" if lane == "stable" else "exp_fdb_hub"}',
                     cluster_id=topology['cluster_id'], deadline=time.monotonic() + timeout, processes=children)
        for trial in range(rounds):
            if stable_fingerprint() != stable_files:
                raise LabError('Stable source changed during crash lab; refusing further testing')
            if lane == 'stable':
                result = stable_lab.build_stable_hub_integrated_market_stress_result(
                    topology_path=topology_path, requester_wallet_path=lane_out / 'requester-wallet.json',
                    worker_wallet_path=lane_out / 'worker-wallet.json', timeout=min(30.0, timeout))
                observations = _stable_snapshot(result)
            else:
                result = exp_lab.verify_handoff(hub_urls=urls, stress_requests=3,
                                                failure_indices=(2,), timeout=min(30.0, timeout),
                                                require_multisession_auth=True)
                observations = _exp_snapshot(result)
            # A pre-crash read ensures the baseline was *actually committed*.
            before = _assert_same(observations)
            financial_before = (_stable_financial_snapshot(urls[0]) if lane == 'stable'
                                else _exp_charge_snapshot(observations))
            _json_atomic(lane_out / f'round-{trial}-prekill.json', {
                'ok': True, 'workload': result, 'before': before,
                'financial_before': financial_before, 'observations': observations,
            })
            # Stable: kill the first Hub process (requester and payout entry).
            # Experimental: kill the single three-port Hub process.
            target_idx = trial % 3 if lane == 'stable' else 0
            victim = children[target_idx]
            _stop(victim, kill=True)
            log_event({'event': 'process_killed', 'round': trial, 'pid': victim.pid,
                           'return_code': victim.returncode})
            if victim.returncode is None:
                raise LabError('Cannot confirm victim process termination')
            children[target_idx] = spawn(target_idx, trial + 1)
            _wait_health(urls, service=f'main_computer.{"stable_hub" if lane == "stable" else "exp_fdb_hub"}',
                         cluster_id=topology['cluster_id'], deadline=time.monotonic() + timeout, processes=children)
            after = _assert_same(observations)
            financial_after = (_stable_financial_snapshot(urls[0]) if lane == 'stable'
                               else _exp_charge_snapshot(observations))
            if financial_after != financial_before:
                raise LabError('Financial FDB ledger changed during process crash/restart')
            log_event({'event': 'recovered', 'round': trial, 'checks': len(after), 'pid': children[target_idx].pid})
            _json_atomic(lane_out / f'round-{trial}-recovered.json',
                         {'ok': True, 'after': after, 'financial_after': financial_after})
        return {'ok': True, 'lane': lane, 'rounds': rounds, 'events': events,
                'namespace': topology['storage']['namespace'], 'topology': str(topology_path),
                'scope': 'post_commit_process_death_and_restarted_FDB_record_recovery; no chain broadcast'}
    finally:
        for proc in children:
            _stop(proc, kill=False)
        for log in logs:
            log.close()


def _kill_tree(proc: subprocess.Popen, *, marked_pid: int | None = None) -> None:
    """Kill a lab process and its Windows python.exe interpreter child.

    On Windows a venv executable may create a second interpreter process.
    The fault marker's PID is the actual handler process. Both are children
    of the lab's own isolated Popen launch; never use this on an external PID.
    """
    if marked_pid and marked_pid != proc.pid:
        # Only accept an actual descendant of this lab Popen process.
        if os.name == 'nt':
            command = [
                'powershell', '-NoProfile', '-Command',
                f'$p={marked_pid}; $root={proc.pid}; '
                '$ids=@($p); while($p -and $p -ne $root) {'
                '$q=Get-CimInstance Win32_Process -Filter "ProcessId=$p"; '
                'if(-not $q){exit 2}; $p=[int]$q.ParentProcessId; $ids += $p; '
                'if($ids.Count -gt 12){exit 3}}; if($p -ne $root){exit 4}',
            ]
            check = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            if check.returncode != 0:
                raise LabError(f'Fault marker PID {marked_pid} does not belong to launched Hub {proc.pid}')
        else:
            # On Unix the fault runner is exec'd by the Popen child itself.
            raise LabError('Fault marker PID differs from launched Unix Hub PID')
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       capture_output=True, timeout=20)
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        raise LabError(f'Cannot confirm crash-lab process {proc.pid} terminated')


def _wait_barrier(marker: Path, *, hub_processes: list[subprocess.Popen],
                  client_process: subprocess.Popen, deadline: float) -> dict[str, Any]:
    def diagnostic() -> str:
        details: list[str] = []
        progress = marker.parent / 'client-result.json'
        if progress.exists():
            try:
                info = json.loads(progress.read_text(encoding='utf-8'))
                details.append('client_phase=' + str(info.get('phase')))
                if info.get('error'):
                    details.append('client_error=' + str(info['error'])[:300])
                if info.get('terminal_ack'):
                    details.append('terminal_ack=' + str(info['terminal_ack'])[:300])
            except (OSError, ValueError):
                details.append('client_progress_unreadable')
        trace = marker.parent / 'fault-hook-trace.jsonl'
        if trace.exists():
            try:
                lines = trace.read_text(encoding='utf-8').splitlines()
                details.append('fault_hook_phases=' + ','.join(
                    str(json.loads(line).get('phase')) for line in lines[-8:]))
            except (OSError, ValueError):
                details.append('fault_hook_trace_unreadable')
        return '; '.join(details) or 'no client progress or fault-hook trace'

    while time.monotonic() < deadline:
        if marker.exists():
            try:
                payload = json.loads(marker.read_text(encoding='utf-8'))
                if payload.get('pid') and payload.get('record_id') and payload.get('request_id'):
                    return payload
            except (OSError, ValueError):
                pass  # producer may still be fsyncing the marker
        if client_process.poll() is not None:
            raise LabError(f'Workload exited before crash marker, exit={client_process.returncode}; {diagnostic()}')
        for proc in hub_processes:
            if proc.poll() is not None:
                raise LabError(f'Hub process exited before crash marker, exit={proc.returncode}')
        time.sleep(.1)
    raise LabError(f'Timed out waiting for real after-commit crash marker: {marker}; {diagnostic()}')


def _check_marked_fdb_record(lane: str, marker: dict[str, Any], *,
                             cluster_file: Path, namespace: str, base_url: str) -> dict[str, Any]:
    """Verify exact committed financial object and forbid duplicate records."""
    kind = marker['record_kind']
    record_id = marker['record_id']
    request_id = marker['request_id']
    if lane == 'stable':
        payload = _status(base_url + '/api/hub/v1/payout/status')
        payout = payload.get('payout') or {}
        key = 'holds' if kind == 'hold' else 'charges'
        candidates = payout.get(key, [])
        earnings = payout.get('worker_earnings', [])
        if not isinstance(candidates, list) or not isinstance(earnings, list):
            raise LabError(f'Stable payout status missing {key}/earnings arrays')
        rows = [r for r in candidates if isinstance(r, dict) and r.get(kind + '_id') == record_id]
        matching_request = [r for r in candidates if isinstance(r, dict) and r.get('request_id') == request_id]
        earned = [e for e in earnings if isinstance(e, dict) and e.get('request_id') == request_id]
    else:
        from main_computer.exp_fdb_credit_ledger import (
            ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger,
        )
        ledger = ExperimentalFoundationDbCreditLedger(ExperimentalFoundationDbConfig(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT,
        ))
        objects = ledger.list_holds(request_id=request_id, limit=1000) if kind == 'hold' else (
            ledger.list_charges(request_id=request_id, limit=1000)
        )
        candidates = [r.as_dict() for r in objects]
        rows = [r for r in candidates if r.get(kind + '_id') == record_id]
        matching_request = candidates
        earned = [r.as_private_dict() for r in ledger.list_worker_earnings(request_id=request_id, limit=1000)]
    if len(rows) != 1 or len(matching_request) != 1:
        raise LabError(f'{lane} {kind} recovery failure: exact rows={len(rows)} request rows={len(matching_request)}')
    if rows[0].get('request_id') != request_id:
        raise LabError(f'{lane} committed {kind} changed request identity')
    if kind == 'charge':
        earning_id = marker.get('worker_earning_id') or ''
        if not earning_id or len([e for e in earned if e.get('earning_id') == earning_id]) != 1:
            raise LabError(f'{lane} charge/worker earning not preserved after process death')
    return {'kind': kind, 'record': rows[0], 'earning_ids': sorted(e.get('earning_id') for e in earned if e.get('earning_id')),
            'matching_request_count': len(matching_request)}


def _check_before_commit_absence(lane: str, marker: dict[str, Any], *,
                                cluster_file: Path, namespace: str, base_url: str) -> dict[str, Any]:
    """Read committed FDB from a different process. No uncommitted row may survive."""
    if marker.get('point') != 'before_fdb_commit':
        raise LabError('Precommit marker was not inside a transaction')
    request_id = marker['request_id']
    kind = marker['record_kind']
    if kind not in ('hold', 'charge'):
        raise LabError('Precommit verifier cannot inspect this record kind')
    if lane == 'stable':
        payout = _status(base_url + '/api/hub/v1/payout/status').get('payout') or {}
        rows = payout.get('holds' if kind == 'hold' else 'charges') or []
        earnings = payout.get('worker_earnings') or []
        if not isinstance(rows, list) or not isinstance(earnings, list):
            raise LabError('Stable precommit verifier cannot read financial records')
        matches = [row for row in rows if row.get('request_id') == request_id]
        earned = [row for row in earnings if row.get('request_id') == request_id]
    else:
        from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
        ledger = ExperimentalFoundationDbCreditLedger(ExperimentalFoundationDbConfig(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT))
        records = (ledger.list_holds(request_id=request_id, limit=1000) if kind == 'hold'
                   else ledger.list_charges(request_id=request_id, limit=1000))
        matches = [item.as_dict() for item in records]
        earned = [item.as_private_dict() for item in ledger.list_worker_earnings(request_id=request_id, limit=1000)]
    if matches or earned:
        raise LabError(f'{lane} precommit rollback failed: {len(matches)} {kind} rows, {len(earned)} earnings')
    return {'kind': kind, 'request_id': request_id, 'committed_target_rows': 0,
            'committed_worker_earnings': 0, 'precommit_rolled_back': True}


def _check_settlement_record(lane: str, marker: dict[str, Any], *,
                             cluster_file: Path, namespace: str, base_url: str) -> dict[str, Any]:
    """Exact one committed claim/batch; include dependent earning/claim identities."""
    kind, ident, worker = marker['record_kind'], marker['record_id'], marker['request_id']
    if kind not in ('claim', 'batch'):
        raise LabError('Settlement marker does not identify claim or batch')
    if lane == 'stable':
        payout = _status(base_url + '/api/hub/v1/payout/status').get('payout') or {}
        rows = payout.get('worker_claims' if kind == 'claim' else 'settlements') or []
    else:
        from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
        ledger = ExperimentalFoundationDbCreditLedger(ExperimentalFoundationDbConfig(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT))
        @ledger.fdb.transactional
        def get_rows(tr):
            return ledger._list_dicts(tr, 'worker_claim' if kind == 'claim' else 'worker_settlement_batch')
        rows = get_rows(ledger.db)
    if not isinstance(rows, list):
        raise LabError('Settlement collection not readable')
    field = 'claim_id' if kind == 'claim' else 'batch_id'
    found = [row for row in rows if isinstance(row, dict) and str(row.get(field) or '') == ident]
    same_worker = [row for row in rows if isinstance(row, dict) and
                   str(row.get('worker_node_id') or row.get('worker_id') or '') == worker and
                   str(row.get('idempotency_key') or (row.get('metadata') or {}).get('idempotency_key') or '') ==
                   str((found[0].get('idempotency_key') or (found[0].get('metadata') or {}).get('idempotency_key')) if found else '')]
    if len(found) != 1 or len(same_worker) != 1:
        raise LabError(f'{lane} {kind} lost/duplicated: exact={len(found)} idempotent={len(same_worker)}')
    row = found[0]
    if str(row.get('worker_node_id') or row.get('worker_id') or '') != worker:
        raise LabError('Settlement record worker identity changed')
    dependency = 'earning_ids' if kind == 'claim' else 'claim_ids'
    if not row.get(dependency):
        raise LabError('Settlement record has no underlying economic obligations')
    return {'kind': kind, 'record': row, 'matching_ids': 1, 'same_idempotency_key': 1}


def _assert_settlement_replay(lane: str, marker: dict[str, Any], recovered: dict[str, Any], *,
                              topology_path: Path, cluster_file: Path,
                              namespace: str, base_url: str) -> dict[str, Any]:
    """Replay the SAME obligation after process death, proving no new claim/batch."""
    kind, worker, ident = marker['record_kind'], marker['request_id'], marker['record_id']
    row = recovered['record']
    key = str(row.get('idempotency_key') or (row.get('metadata') or {}).get('idempotency_key') or '')
    if not key:
        raise LabError('Settlement replay requires original idempotency key')
    if lane == 'stable':
        from main_computer.stable_hub_topology import load_stable_hub_topology
        from main_computer.stable_hub_worker_sessions import (
            StableHubPayoutLedgerDirectory, FoundationDbStableWorkerSessionStore)
        topology = load_stable_hub_topology(topology_path)
        ledger = StableHubPayoutLedgerDirectory(topology=topology,
            hub_id=topology.hubs[0].hub_id,
            store=FoundationDbStableWorkerSessionStore(
                cluster_file=cluster_file, namespace=namespace, repo_root=ROOT))
        result = (ledger.record_worker_claim(worker_id=worker, earning_ids=row['earning_ids'], idempotency_key=key)
                  if kind == 'claim' else ledger.create_worker_settlement_batch(
                      worker_id=worker, claim_ids=row['claim_ids'], idempotency_key=key))
        repeated_record = result
    else:
        from main_computer.exp_fdb_credit_ledger import ExperimentalFoundationDbConfig, ExperimentalFoundationDbCreditLedger
        ledger = ExperimentalFoundationDbCreditLedger(ExperimentalFoundationDbConfig(
            cluster_file=cluster_file, namespace=namespace, repo_root=ROOT))
        result = (ledger.record_worker_claim(worker_node_id=worker, earning_ids=row['earning_ids'], idempotency_key=key)
                  if kind == 'claim' else ledger.create_worker_settlement_batch(
                      worker_node_id=worker, claim_ids=row['claim_ids'], idempotency_key=key))
        if result.get('idempotent') is not True:
            raise LabError(f'Experimental {kind} retry was not idempotent')
        repeated_record = result.get('claim' if kind == 'claim' else 'batch') or {}
    if str(repeated_record.get('claim_id' if kind == 'claim' else 'batch_id') or '') != ident:
        raise LabError('Retried settlement generated a replacement obligation')
    after = _check_settlement_record(lane, marker, cluster_file=cluster_file,
                                      namespace=namespace, base_url=base_url)
    if after != recovered:
        raise LabError('Retried settlement mutated or duplicated the original obligation')
    return {'same_obligation_id': True, 'unchanged_persistent_record': True,
            'obligation_id': ident, 'idempotency_key': key}


def _assert_surviving_hub_financial_read(lane: str, marker: dict[str, Any], *,
                                         survivor_url: str) -> dict[str, Any]:
    """The live peer, not only an offline FDB client, must serve the committed charge."""
    if marker['record_kind'] == 'charge':
        if lane == 'stable':
            rows = (_status(survivor_url + '/api/hub/v1/payout/status').get('payout') or {}).get('charges') or []
        else:
            from urllib.parse import urlencode
            payload = _status(survivor_url + '/api/hub/v1/credits/charges?' + urlencode({
                'request_id': marker['request_id'], 'limit': 1000}))
            rows = payload.get('charges') or []
        matched = [row for row in rows if row.get('charge_id') == marker['record_id']
                   and row.get('request_id') == marker['request_id']]
        if len(matched) != 1:
            raise LabError(f'Surviving {lane} Hub did not serve committed charge, rows={len(matched)}')
        return {'survivor_url': survivor_url, 'http_charge_verified': True, 'charge': matched[0]}
    # Experimental deprecated the HTTP hold-list API; read-only FDB verifier
    # still checks the record, but don't misreport an HTTP hold verification.
    return {'survivor_url': survivor_url, 'http_charge_verified': False,
            'reason': 'hold endpoint not comparable across lanes'}


def _run_inflight_one(lane: str, fault: str, *, out: Path, run_id: str,
                      cluster_file: Path, stable_topology: Path, port_base: int,
                      timeout: float, stable_files: dict[str, str],
                      point: str = "after_commit", failover: bool = False) -> dict[str, Any]:
    """True OS process death after FDB commit, before HTTP response.

    The in-flight client request will fail. We verify durable accounting,
    not end-to-end automatic client retry or external chain reconciliation.
    """
    ports = [port_base + i for i in range(3)]
    _ports_free(ports)
    out.mkdir(parents=True, exist_ok=True)
    topology_path = out / 'isolated-topology.json'
    topology = _topology(stable_topology, topology_path, run_id=run_id,
                         lane=lane, ports=ports, cluster_file=cluster_file)
    urls = topology['entry_urls']
    marker = out / ('before-commit-marker.json' if point == 'before_commit' else 'after-commit-marker.json')
    env = dict(os.environ)
    env['PYTHONUNBUFFERED'] = '1'
    env.pop('MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS', None)
    processes: list[subprocess.Popen] = []
    handles: list[Any] = []
    client: subprocess.Popen | None = None
    client_log: Any = None
    events: list[dict[str, Any]] = []
    def event(name: str, **details: Any) -> None:
        item = {'event': name, 'at_unix': time.time(), **details}
        events.append(item)
        _json_atomic(out / 'events.json', {'events': events})

    def start_hub(index: int, generation: int, *, inject: bool) -> subprocess.Popen:
        if lane == 'stable':
            args = ['--topology', str(topology_path), '--hub-id', topology['hubs'][index]['hub_id']]
        else:
            args = ['--host', '127.0.0.1', '--ports', str(ports[index]) if failover else ','.join(map(str, ports)),
                    '--topology', str(topology_path), '--namespace', topology['storage']['namespace'],
                    '--cluster-file', str(cluster_file), '--hub-root', str(out / (f'hub-runtime-{index}' if failover else 'hub-runtime')),
                    '--network-key', topology['network']['network_key'],
                    '--network-kind', 'experimental', '--bridge-backend', 'mock-chain',
                    '--no-fdb-autostart', '--require-multisession-auth']
        if inject:
            cmd = [sys.executable, '-u', '-m', 'tools.hub_crash_lab.fault_runner',
                   '--lane', lane, '--fault', fault, '--point', point, '--marker', str(marker), '--', *args]
        else:
            cmd = [sys.executable, '-u', '-m', 'main_computer.' + ('stable_hub' if lane == 'stable' else 'exp_fdb_hub'), *args]
        proc, handle = _start(cmd, log=out / f'hub-{index}-generation-{generation}.log', env=env)
        handles.append(handle)
        event('hub_started', index=index, generation=generation, injected=inject, pid=proc.pid)
        return proc

    try:
        for i in range(3 if (lane == 'stable' or failover) else 1):
            processes.append(start_hub(i, 0, inject=True))
        health_args = dict(service=f'main_computer.{"stable_hub" if lane == "stable" else "exp_fdb_hub"}',
                           cluster_id=topology['cluster_id'])
        _wait_health(urls, deadline=time.monotonic() + timeout, processes=processes, **health_args)
        client_cmd = [sys.executable, '-u', '-m', 'tools.hub_crash_lab.workload',
                      '--lane', lane, '--topology', str(topology_path),
                      '--output', str(out / 'client-result.json'), '--fault', fault,
                      '--timeout', str(min(30., timeout))]
        if failover and lane == 'stable' and fault == 'charge':
            client_cmd.append('--failover-first-charge')
        client, client_log = _start(client_cmd, log=out / 'client.log', env=env)
        evidence = _wait_barrier(marker, hub_processes=processes, client_process=client,
                                 deadline=time.monotonic() + timeout)
        if evidence['lane'] != lane or evidence['fault'] != fault:
            raise LabError('Fault marker belongs to wrong lane or crash boundary')
        if stable_fingerprint() != stable_files:
            raise LabError('Stable source changed during in-flight lab')
        victim_idx = None
        # On Windows the venv launcher can create an interpreter subprocess;
        # validate the descendant in _kill_tree rather than guessing a PID.
        for i, proc in enumerate(processes):
            if proc.pid == evidence['pid']:
                victim_idx = i
                break
        if victim_idx is None and os.name == 'nt':
            for i, proc in enumerate(processes):
                check = subprocess.run([
                    'powershell', '-NoProfile', '-Command',
                    f'$p={int(evidence["pid"])};$root={proc.pid};while($p -and $p -ne $root){{$x=Get-CimInstance Win32_Process -Filter "ProcessId=$p";if(-not $x){{exit 4}};$p=[int]$x.ParentProcessId}};if($p -eq $root){{exit 0}}else{{exit 5}}'
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                if check.returncode == 0:
                    victim_idx = i
                    break
        if victim_idx is None:
            raise LabError('Refusing to kill marker PID: not a spawned lab Hub descendant')
        required_point = 'before_fdb_commit' if point == 'before_commit' else 'after_fdb_commit_before_call_return'
        if evidence.get('point') != required_point:
            raise LabError(f'Wrong marker boundary: {evidence.get("point")!r}')
        event('fault_observed', marker=evidence)
        _kill_tree(processes[victim_idx], marked_pid=int(evidence['pid']))
        event('hub_killed_inflight', pid=evidence['pid'], spawned_pid=processes[victim_idx].pid,
              exit_code=processes[victim_idx].returncode)
        if client is not None:
            _kill_tree(client)
            client = None
        survivor_evidence = None
        if failover:
            survivors = [urls[i] for i in range(3) if i != victim_idx]
            survivor_procs = [proc for i, proc in enumerate(processes) if i != victim_idx]
            _wait_health(survivors, deadline=time.monotonic() + timeout, processes=survivor_procs, **health_args)
            # Verify the durable record *while the owner is dead*, from a
            # surviving Hub's HTTP endpoint wherever the API exposes it.
            snapshot = _check_marked_fdb_record(
                lane, evidence, cluster_file=cluster_file,
                namespace=topology['storage']['namespace'], base_url=survivors[0])
            http_evidence = _assert_surviving_hub_financial_read(
                lane, evidence, survivor_url=survivors[0])
            survivor_evidence = {'persisted_while_owner_dead': snapshot,
                                 'survivor_http': http_evidence}
            event('surviving_hub_verified_before_owner_restart', evidence=survivor_evidence)
        processes[victim_idx] = start_hub(victim_idx, 1, inject=False)
        _wait_health(urls, deadline=time.monotonic() + timeout, processes=processes, **health_args)
        check = (_check_before_commit_absence if point == 'before_commit'
                 else _check_settlement_record if fault in ('claim', 'batch')
                 else _check_marked_fdb_record)
        recovered = check(lane, evidence, cluster_file=cluster_file,
                          namespace=topology['storage']['namespace'], base_url=urls[0])
        event('durable_record_verified', recovered=recovered)
        replay_evidence = None
        if fault in ('claim', 'batch'):
            replay_evidence = _assert_settlement_replay(
                lane, evidence, recovered, topology_path=topology_path,
                cluster_file=cluster_file, namespace=topology['storage']['namespace'],
                base_url=urls[0])
            event('settlement_idempotent_replay_verified', evidence=replay_evidence)
        # A second restart must not change the committed record either.
        _kill_tree(processes[victim_idx])
        processes[victim_idx] = start_hub(victim_idx, 2, inject=False)
        _wait_health(urls, deadline=time.monotonic() + timeout, processes=processes, **health_args)
        repeated = check(lane, evidence, cluster_file=cluster_file,
                         namespace=topology['storage']['namespace'], base_url=urls[0])
        if recovered != repeated:
            raise LabError('In-flight recovery changed financial record on second restart')
        event('second_restart_verified')
        return {'ok': True, 'lane': lane, 'fault': fault, 'marker': evidence,
                'recovered': recovered, 'survivor_evidence': survivor_evidence, 'replay_evidence': replay_evidence,
                'namespace': topology['storage']['namespace'],
                'scope': ('precommit_rollback_process_death' if point == 'before_commit' else
                          'committed_claim_batch_recovery' if fault in ('claim', 'batch') else
                          'surviving_hub_pre_owner_restart' if failover else
                          'post_FDB_commit_pre_handler_response_process_death'),
                'does_not_prove': 'automatic_client_retry, Temporal unfinished workflow or chain reconciliation'}
    finally:
        if client is not None:
            _kill_tree(client)
        if client_log:
            client_log.close()
        for proc in processes:
            _kill_tree(proc)
        for handle in handles:
            handle.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--hub', choices=('stable', 'exp', 'both'), default='both')
    p.add_argument('--mode', choices=('baseline', 'restart', 'inflight', 'precommit', 'settlement', 'failover'), default='baseline')
    p.add_argument('--fault', choices=('reserve', 'charge', 'claim', 'batch', 'both'), default='charge',
                   help='In-flight FDB commit boundary to interrupt (no on-chain payout)')
    p.add_argument('--stable-topology', type=Path, default=DEFAULT_STABLE_TOPOLOGY)
    p.add_argument('--cluster-file', type=Path, default=ROOT / '.foundationdb/docker.cluster')
    p.add_argument('--stable-port-base', type=int, default=18870)
    p.add_argument('--exp-port-base', type=int, default=18970)
    p.add_argument('--rounds', type=int, default=1)
    p.add_argument('--timeout', type=float, default=90.0)
    p.add_argument('--baseline-timeout', type=float, default=240.0)
    p.add_argument('--report-dir', type=Path, default=ROOT / 'runtime/hub-crash-lab')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    report = {'ok': False, 'mode': args.mode, 'hub': args.hub, 'scope': 'local-only; no live-chain payout',
              'results': {}, 'stable_source_sha256': {}}
    try:
        if args.rounds < 1 or args.rounds > 1000 or args.timeout <= 0:
            raise LabError('--rounds must be 1..1000 and --timeout positive')
        root = ROOT.resolve()
        cluster_file = (ROOT / args.cluster_file).resolve()
        if args.mode in ('restart', 'inflight', 'precommit', 'settlement', 'failover'):
            if not cluster_file.is_file():
                raise LabError(f'Existing FDB cluster file required; never auto-start/reset: {cluster_file}')
            _preflight_local_docker_fdb(cluster_file)
        stable_sources = stable_fingerprint(root)
        report['stable_source_sha256'] = stable_sources
        out = (ROOT / args.report_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        baseline = _run_baseline(out, stable_sources, timeout=args.baseline_timeout)
        report['stable_baseline'] = {'ok': baseline['ok'], 'path': str(out / 'stable-baseline.json'),
                                     'exit_code': baseline.get('exit_code'), 'error': baseline.get('error', '')}
        if not baseline['ok']:
            raise LabError('Existing Stable tests did not pass. No Hub started, killed, or changed.')
        if args.mode == 'restart':
            run_id = secrets.token_hex(8)
            report['run_id'] = run_id
            for lane in (('stable', 'exp') if args.hub == 'both' else (args.hub,)):
                base = args.stable_port_base if lane == 'stable' else args.exp_port_base
                lane_result = _run_lane(lane, out=out / run_id, run_id=run_id,
                                        cluster_file=cluster_file, stable_topology=(ROOT / args.stable_topology).resolve(),
                                        port_base=base, timeout=args.timeout, stable_files=stable_sources,
                                        rounds=args.rounds)
                report['results'][lane] = lane_result
        if args.mode in ('inflight', 'precommit', 'settlement', 'failover'):
            if args.mode == 'settlement':
                selected_faults = ('claim', 'batch') if args.fault == 'both' else (args.fault,)
                if any(fault not in ('claim', 'batch') for fault in selected_faults):
                    raise LabError('Settlement mode needs --fault claim, batch or both')
            else:
                selected_faults = ('reserve', 'charge') if args.fault == 'both' else (args.fault,)
                if any(fault not in ('reserve', 'charge') for fault in selected_faults):
                    raise LabError(f'{args.mode} mode needs --fault reserve, charge or both')
            if args.mode == 'failover' and args.fault == 'reserve':
                # Allowed, but the Experimental legacy HTTP hold-list is disabled.
                # Report will state only the direct FDB hold proof.
                pass
            run_id = secrets.token_hex(8)
            report['run_id'] = run_id
            failures = []
            for lane in (('stable', 'exp') if args.hub == 'both' else (args.hub,)):
                for fault in selected_faults:
                    for trial in range(args.rounds):
                        trial_run = f'{run_id}-{trial}-{fault}'
                        base = args.stable_port_base if lane == 'stable' else args.exp_port_base
                        try:
                            result = _run_inflight_one(
                                lane, fault, out=out / run_id / lane / fault / f'round-{trial}',
                                run_id=trial_run, cluster_file=cluster_file,
                                stable_topology=(ROOT / args.stable_topology).resolve(),
                                port_base=base, timeout=args.timeout, stable_files=stable_sources,
                                point='before_commit' if args.mode == 'precommit' else 'after_commit',
                                failover=args.mode == 'failover',
                            )
                        except Exception as exc:
                            trial_dir = out / run_id / lane / fault / f'round-{trial}'
                            logs = {}
                            for log_path in sorted(trial_dir.glob('*.log')):
                                try:
                                    logs[log_path.name] = log_path.read_text(encoding='utf-8', errors='replace')[-2500:]
                                except OSError:
                                    pass
                            failures.append({'lane': lane, 'fault': fault, 'round': trial,
                                             'error': f'{type(exc).__name__}: {exc}'})
                            result = {'ok': False, 'error': failures[-1]['error'], 'logs': logs}
                        report['results'].setdefault(lane, {}).setdefault(fault, []).append(result)
                        _json_atomic(out / 'last-run.json', report)
                        if failures:
                            break
                    if failures:
                        break
                if failures:
                    break
            if failures:
                raise LabError(f'{args.mode} fault failed; see results: {failures[0]}')
        report['ok'] = stable_fingerprint(root) == stable_sources
    except (OSError, ValueError, LabError, RuntimeError, subprocess.SubprocessError) as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    path = (ROOT / args.report_dir).resolve() / 'last-run.json'
    _json_atomic(path, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f'Stable/Experimental crash lab: {"PASS" if report["ok"] else "FAIL"}')
        print(f'  phase: {args.mode}; hub: {args.hub}; report: {path}')
        if report.get('error'):
            print('  error: ' + report['error'])
        for lane, result in report['results'].items():
            if args.mode in ('inflight', 'precommit', 'settlement', 'failover'):
                for fault, trials in result.items():
                    print(f'  {lane}/{fault}: {"PASS" if all(t.get("ok") for t in trials) else "FAIL"}; rounds={len(trials)}')
            else:
                print(f'  {lane}: {"PASS" if result["ok"] else "FAIL"}; rounds={result["rounds"]}')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
