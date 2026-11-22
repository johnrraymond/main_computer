"""Disposable real-container ONLYOFFICE callback security experiment.

Run from the Main Computer repository root with Docker Desktop running:

    python -m tools.mcel_onlyoffice_docker_lab run

Optional combined ingress invocation:

    python -m tools.mcel_security_ingress_smoke run --docker-onlyoffice

Creates a random Docker Compose project containing a real ONLYOFFICE Document
Server, disposable baseline/secured MC callback fixtures, a document source,
rogue server, and a probe container. There are NO published host ports, NO MC
process mutations, NO production volumes, and NO permanent compose stacks.
It runs the unchanged callback handler against real container networking, with
an isolated candidate signature/URL policy applied ahead of it.

The successful lab does NOT prove that a real editing session from Document
Server can emit or authenticate its native callbacks; that remains UNVERIFIED.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_TIMEOUT = 600


def run(*args: str, timeout: int = COMPOSE_TIMEOUT) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(args), timeout=timeout, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed exit={result.returncode} command={' '.join(args[:7])}")
    return result


def compose_spec(tag: str, shared_secret: str, fixture: Path, probe: Path, source: Path) -> dict:
    # JSON is a valid Docker Compose -f input; no PyYAML dependency.
    binds = [
        {'type': 'bind', 'source': str(fixture.resolve()), 'target': '/work/tools/mcel_onlyoffice_docker_fixture.py', 'read_only': True},
        {'type': 'bind', 'source': str(probe.resolve()), 'target': '/work/tools/mcel_onlyoffice_docker_probe.py', 'read_only': True},
        {'type': 'bind', 'source': str(source.resolve()), 'target': '/work/viewport_routes_onlyoffice.py', 'read_only': True},
    ]

    def python_service(role: str) -> dict:
        return {
            'image': 'python:3.12-slim',
            'restart': 'no',
            'working_dir': '/work',
            'command': ['python', '-u', '/work/tools/mcel_onlyoffice_docker_fixture.py', role],
            'environment': {'LAB_SECRET': shared_secret, 'PYTHONPATH': '/work'},
            'volumes': binds,
            'networks': ['sandbox'],
            'security_opt': ['no-new-privileges:true'],
        }

    services = {role: python_service(role) for role in ('baseline','secured','document','rogue','probe')}
    # Bob is an unprivileged, uncredentialed container in the private network.
    # Do not give Bob the LAB_SECRET, source code, mounted MC state or the
    # Docker control socket. This is distinct from the authorized probe.
    services['bob'] = {
        'image': 'python:3.12-slim', 'restart': 'no',
        'command': ['python','-u','/lab/bob.py'],
        'user': '65534:65534',
        'environment': {},
        'volumes': [{'type':'bind',
                     'source':str((ROOT/'tools'/'mcel_onlyoffice_bob_probe.py').resolve()),
                     'target':'/lab/bob.py','read_only':True}],
        'networks':['sandbox'], 'cap_drop':['ALL'],
        'security_opt':['no-new-privileges:true'], 'read_only':True,
    }
    services['onlyoffice'] = {
        'image': f'onlyoffice/documentserver:{tag}',
        'restart': 'no',
        'environment': {'JWT_ENABLED': 'true', 'JWT_SECRET': shared_secret,
                        'ALLOW_PRIVATE_IP_ADDRESS': 'true', 'ALLOW_META_IP_ADDRESS': 'false'},
        'shm_size': '512m',
        'networks': ['sandbox'],
    }
    return {
        'services': services,
        'networks': {'sandbox': {'internal': True}},
    }


def experiment(tag: str) -> int:
    for command in ('docker',):
        if not shutil.which(command):
            print('ONLYOFFICE_DOCKER_LAB UNVERIFIED docker command unavailable', flush=True)
            return 2
    source = ROOT / 'main_computer' / 'viewport_routes_onlyoffice.py'
    fixture = ROOT / 'tools' / 'mcel_onlyoffice_docker_fixture.py'
    probe = ROOT / 'tools' / 'mcel_onlyoffice_docker_probe.py'
    for p in (source,fixture,probe):
        if not p.is_file():
            print(f'ONLYOFFICE_DOCKER_LAB UNVERIFIED required file missing: {p}', flush=True)
            return 2
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}',tag):
        raise ValueError('unsafe ONLYOFFICE image tag')
    project = 'mc-oosec-' + secrets.token_hex(4)
    shared_secret = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix='mc-onlyoffice-security-lab-') as tmp:
        manifest = Path(tmp) / 'docker-compose.json'
        manifest.write_text(json.dumps(compose_spec(tag, shared_secret, fixture, probe, source),indent=2),encoding='utf-8')
        base = ('docker', 'compose', '-p', project, '-f', str(manifest))
        outcome = 2
        try:
            run('docker', 'version', '--format', '{{.Server.Version}}', timeout=15)
            run(*base, 'config', '--quiet', timeout=30)
            print(f'ONLYOFFICE_DOCKER_LAB project={project} isolated-network=true published-ports=0',flush=True)
            run(*base, 'up', '-d', 'onlyoffice', 'baseline', 'secured', 'document', 'rogue')
            # Check Docker's real port publishing, not just the Compose file.
            for service in ('onlyoffice','baseline','secured','document','rogue'):
                identity = subprocess.run([*base,'ps','-q',service],capture_output=True,text=True,timeout=20,check=True).stdout.strip()
                if not identity:
                    raise RuntimeError(f'no container for {service}')
                check = subprocess.run(['docker','inspect','--format','{{json .HostConfig.PortBindings}}',identity],capture_output=True,text=True,timeout=20,check=True)
                ports = json.loads(check.stdout)
                if ports:
                    raise RuntimeError(f'{service} unexpectedly publishes host ports')
            print('ONLYOFFICE_DOCKER_LAB all-container-host-ports-unpublished PASS',flush=True)
            # Probe runs inside the same private Docker network. It has no host
            # port or mount to MC runtime state, and is removed afterward.
            call = subprocess.run([*base,'run','--rm','--no-deps','probe'],check=False,timeout=300)
            outcome = call.returncode
            if outcome:
                print(f'ONLYOFFICE_DOCKER_LAB correction-unverified probe-exit={outcome}',flush=True)
            else:
                # This deliberately includes a direct document-source
                # access check that can reveal an additional bypass.
                bob = subprocess.run([*base,'run','--rm','--no-deps','bob'],
                                     check=False,timeout=90)
                outcome=bob.returncode
                if outcome:
                    print(f'ONLYOFFICE_DOCKER_LAB bob-adversarial-probe FAIL exit={outcome}',flush=True)
                else:
                    print('ONLYOFFICE_DOCKER_LAB bob-adversarial-probe PASS',flush=True)
        except (subprocess.TimeoutExpired,subprocess.CalledProcessError,RuntimeError) as exc:
            print(f'ONLYOFFICE_DOCKER_LAB UNVERIFIED {type(exc).__name__}: {str(exc)[:300]}',flush=True)
            outcome = 2
        finally:
            try:
                run(*base,'down','-v','--remove-orphans',timeout=180)
                print('ONLYOFFICE_DOCKER_LAB disposable-stack-cleanup PASS',flush=True)
            except Exception as exc:
                print('ONLYOFFICE_DOCKER_LAB disposable-stack-cleanup FAIL '+repr(exc),flush=True)
                print(f'ONLYOFFICE_DOCKER_LAB RECOVERY_PROJECT {project}  # remove only resources bearing this project label',flush=True)
                outcome = 2
        return outcome


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['run'],default='run')
    parser.add_argument('--onlyoffice-tag',default=os.environ.get('MAIN_COMPUTER_ONLYOFFICE_IMAGE_TAG','latest'))
    args=parser.parse_args(argv)
    return experiment(args.onlyoffice_tag)


if __name__=='__main__':
    raise SystemExit(main())
