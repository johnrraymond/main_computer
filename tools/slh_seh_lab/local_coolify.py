"""Local SEH Compose preparation and automatic Coolify-managed deployment.

`prepare` creates Compose without changing Coolify. `deploy_local` reuses the
existing Website Builder local Coolify operator to deploy with no user key.
"""
from __future__ import annotations

import base64
from pathlib import Path
import re
import subprocess

from main_computer.slh.authenticator import load_public_key
from tools.slh_seh_connector import service_identity, default_state

ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]{0,200}\Z')


class LocalCoolifyError(RuntimeError):
    pass


def compose(*, org: str, slh: str, public_b64: str, image: str, port: int) -> str:
    if not ID.fullmatch(org) or not ID.fullmatch(slh):
        raise LocalCoolifyError('Invalid organization/SLH identifier')
    if not IMAGE.fullmatch(image) or '@' in image:
        raise LocalCoolifyError('Invalid local image name')
    if not 1024 <= port <= 65535:
        raise LocalCoolifyError('Invalid loopback port')
    try:
        load_public_key(base64.b64decode(public_b64, validate=True).decode('ascii'))
    except (ValueError, UnicodeError) as exc:
        raise LocalCoolifyError('Invalid SLH signing public key') from exc
    return f'''# Local Coolify managed service; deployment is performed by the SLH/SEH lab.
# The image must exist in the same Docker daemon as Coolify.
services:
  seh:
    image: {image}
    pull_policy: never
    restart: unless-stopped
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    environment:
      SEH_ORGANIZATION_ID: '{org}'
      SEH_SLH_ID: '{slh}'
      SEH_SLH_PUBLIC_KEY_B64: '{public_b64}'
      SEH_PORT: '8080'
      SEH_AUDIT_MODE: metadata_only
    expose:
      - '8080'
    ports:
      - '127.0.0.1:{port}:8080'
    tmpfs:
      - /tmp:rw,noexec,nosuid,size=16m
    pids_limit: 128
    mem_limit: 256m
    cpus: 1.0
'''


def prepare(*, org: str = 'my-company', slh: str = 'my-slh',
            image: str = 'main-computer-seh:local', port: int = 18080,
            output: Path = Path('runtime/reports/slh-seh-lab/local-coolify/compose.yml'),
            build: bool = False, root: Path = Path('.'), state: Path | None = None) -> dict:
    root = Path(root).resolve()
    if not (root/'deploy/seh/Dockerfile').is_file():
        raise LocalCoolifyError('Run from repository root with SEH Dockerfile installed')
    try:
        _, public_b64 = service_identity(state or default_state(), create=False)
    except FileNotFoundError as exc:
        raise LocalCoolifyError('Run python -m tools.slh_seh_connector identity first') from exc
    data = compose(org=org, slh=slh, public_b64=public_b64, image=image, port=port)
    image_status = 'NOT_BUILT'
    if build:
        def cmd(*args, timeout=30):
            try:
                # Docker build output contains Unicode status characters. On Windows,
                # text=True uses the process locale (often cp1252); an undecodable
                # byte can crash subprocess's reader thread while the command itself
                # still exits 0. Capture bytes, then decode without losing the exit status.
                proc = subprocess.run(['docker',*args], cwd=root, capture_output=True,
                                      timeout=timeout, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise LocalCoolifyError(f'Docker unavailable: {type(exc).__name__}') from exc
            stdout = proc.stdout.decode('utf-8', errors='replace')
            stderr = proc.stderr.decode('utf-8', errors='replace')
            if proc.returncode:
                raise LocalCoolifyError(f'Docker {args[0]} failed: {(stderr or stdout)[-400:]}')
            return stdout
        ps = cmd('ps','--format','{{.Names}}')
        if not any('coolify' in x.lower() for x in ps.splitlines()):
            raise LocalCoolifyError('Cannot find running Coolify in current Docker engine; select the engine used by Coolify')
        cmd('build','-f','deploy/seh/Dockerfile','-t',image,'.',timeout=600)
        image_id = cmd('image','inspect',image,'--format','{{.Id}}').strip()
        if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', image_id):
            raise LocalCoolifyError('Docker image inspect did not return a valid image ID')
        image_status = 'BUILT_ON_COOLIFY_DOCKER_ENGINE'
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(data,encoding='utf-8')
    return {'schema':'main-computer.seh-local-coolify.v1','status':'PREPARED_NOT_DEPLOYED',
            'compose':str(output.resolve()),'image':image,'image_status':image_status,
            'health_url':f'http://127.0.0.1:{port}/healthz',
            'ready_url':f'http://127.0.0.1:{port}/readyz',
            'coolify_api_key_used':False,'coolify_dashboard_deployment_required':True}


def _verify_seh_health(url: str, *, timeout: float = 3.0) -> bool:
    """A TCP listener or unrelated HTTP 200 is not evidence of an SEH deployment."""
    import json
    from urllib.request import urlopen
    try:
        with urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096).decode('utf-8'))
        return payload.get('service') == 'main_computer.seh' and payload.get('live') is True
    except (OSError, TimeoutError, ValueError, KeyError):
        return False


def find_healthy_deployed(*, org: str, slh: str, port: int,
                          root: Path = Path('.'), adapter=None) -> dict | None:
    """Read-only Coolify service discovery. Never mistake a random HTTP server
    on 18080 for a Coolify-managed SEH. An absent/old service is refreshed by
    deploy_local; an existing compatible one is left running.
    """
    import json
    from http.client import HTTPConnection
    from urllib.parse import urlsplit
    from main_computer.publishing.local_server_prepare import _load_coolify_local_docker
    if not ID.fullmatch(org) or not ID.fullmatch(slh) or not 1024 <= port <= 65535:
        raise LocalCoolifyError('Invalid local Coolify SEH identity/port')
    c = HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        c.request('GET','/lab-capabilities')
        res=c.getresponse()
        body=json.loads(res.read(4096)) if res.status == 200 else {}
        if not (body.get('service') == 'main_computer.seh' and
                body.get('isolated_lab_channels') is True):
            return None
    except (OSError,TimeoutError,ValueError):
        return None
    finally:
        c.close()
    root=Path(root).resolve()
    api=adapter if adapter is not None else _load_coolify_local_docker(root)
    dash=urlsplit(api.dashboard_url(root))
    if dash.scheme!='http' or dash.hostname not in ('127.0.0.1','localhost') or dash.username or dash.password:
        raise LocalCoolifyError('Refusing non-local Coolify service lookup')
    ok,_,token=api.ensure_api_token(root)
    if not ok or not token:
        raise LocalCoolifyError('Local Coolify service lookup has no authorization')
    name=f'main-computer-seh-{org.lower()}-{slh.lower()}'
    ok,_,uuid=api.find_service_uuid_via_api(root,token,name)
    if not ok or not uuid:return None
    return {'status':'DEPLOYED_VERIFIED', 'service_uuid':uuid, 'service_name':name,
            'image_status':'EXISTING_IMAGE_REUSED',
            'health_url':f'http://127.0.0.1:{port}/healthz',
            'existing_service_reused':True}


def deploy_local(*, org: str = 'my-company', slh: str = 'my-slh',
                 image: str = 'main-computer-seh:local', port: int = 18080,
                 output: Path = Path('runtime/reports/slh-seh-lab/local-coolify/compose.yml'),
                 root: Path = Path('.'), state: Path | None = None,
                 build: bool = True, coolify=None, health_probe=None,
                 wait_seconds: int = 90) -> dict:
    """Build, reconcile, deploy and verify SEH using Website Builder's LOCAL Coolify operator.

    Coolify's already-provisioned local API token is acquired automatically by
    the same helper used by Website Builder. It is never returned or logged.
    There is no remote Coolify target or dashboard action. Reconciliation is
    idempotent and scoped to the SEH service name.
    """
    import time
    from urllib.parse import urlsplit
    from main_computer.publishing.local_server_prepare import _load_coolify_local_docker

    root = Path(root).resolve()
    prepared = prepare(org=org, slh=slh, image=image, port=port, output=output,
                       root=root, state=state, build=build)
    adapter = coolify if coolify is not None else _load_coolify_local_docker(root)
    dashboard = urlsplit(adapter.dashboard_url(root))
    if (dashboard.scheme != 'http' or dashboard.hostname not in ('127.0.0.1', 'localhost')
            or dashboard.username or dashboard.password or dashboard.query or dashboard.fragment):
        raise LocalCoolifyError('Refusing non-loopback Coolify deployment target')

    # Reuse Website Builder's local self-SSH, network and API bootstrap.
    ok, detail = adapter.ensure_infra_status(root)
    if not ok:
        raise LocalCoolifyError('Local Coolify infrastructure is not ready: ' + str(detail)[-500:])
    ok, detail, token = adapter.ensure_api_token(root)
    if not ok or not token:
        raise LocalCoolifyError('Cannot acquire local Coolify deployment authorization')
    ok, detail, target = adapter.local_deploy_target_from_db(root)
    if not ok or not target.get('server_uuid') or not target.get('destination_uuid'):
        raise LocalCoolifyError('No verified local Coolify deployment target: ' + str(detail)[-300:])
    ok, detail, project_uuid = adapter.find_local_project_uuid_via_api(root, token)
    if not ok or not project_uuid:
        raise LocalCoolifyError('Local Coolify project unavailable: ' + str(detail)[-300:])
    ok, detail = adapter.ensure_project_environment_via_api_or_db(root, token, project_uuid)
    if not ok:
        raise LocalCoolifyError('Local Coolify environment unavailable: ' + str(detail)[-300:])

    # Avoid touching Website Builder services, or a different org's SEH.
    service_name = f'main-computer-seh-{org.lower()}-{slh.lower()}'
    if len(service_name) > 120:
        raise LocalCoolifyError('Organization/SLH names yield an oversized Coolify service name')
    compose_raw = Path(prepared['compose']).read_text(encoding='utf-8')
    ok, detail, service_uuid = adapter.ensure_docker_compose_service_via_api(
        root, token, project_uuid, target, service_name=service_name,
        description=f'Main Computer local SEH for {org}/{slh} (SLH public key only)',
        docker_compose_raw=compose_raw, urls=[])
    if not ok or not service_uuid:
        raise LocalCoolifyError('SEH Coolify service reconciliation failed: ' + str(detail)[-500:])

    # The same deploy and queue-drain sequence that the local Coolify smoke uses.
    ok, detail, deployment_uuid = adapter.trigger_smoke_service_deploy_via_api(root, token, service_uuid)
    if not ok:
        raise LocalCoolifyError('Coolify SEH /deploy failed: ' + str(detail)[-500:])
    ok, detail = adapter.drain_local_coolify_deployment_queue(root)
    if not ok:
        raise LocalCoolifyError('Coolify SEH deploy queue failed: ' + str(detail)[-500:])

    probe = health_probe or _verify_seh_health
    deadline = time.monotonic() + max(1, wait_seconds)
    while time.monotonic() < deadline:
        if probe(prepared['health_url']):
            break
        time.sleep(min(2, max(.05, deadline - time.monotonic())))
    else:
        raise LocalCoolifyError('Coolify deployment did not yield a healthy SEH at ' + prepared['health_url'])
    return {**prepared, 'schema':'main-computer.seh-local-coolify.v2',
            'status':'DEPLOYED_VERIFIED', 'service_name':service_name,
            'service_uuid':service_uuid, 'deployment_uuid':deployment_uuid,
            'coolify_dashboard_deployment_required':False,
            'local_coolify_automatic_authorization':True,
            'coolify_api_key_required_from_user':False,
            'coolify_api_key_used_internally':True}
