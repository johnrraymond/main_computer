"""Git-backed remote SEH birth, following Hub Control's Coolify application contract.

No private credentials in plans. The active, first-class SEH Coolify identity
selects the host; the caller never supplies its IP, token or environment UUID.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import urlsplit

from tools import coolify_hub_service as hub_coolify
from . import identity as st

DEFAULT_PUBLIC_URL = 'https://external.greatlibrary.io'
DOCKERFILE = '/deploy/seh/Dockerfile'
SOURCES = ('.dockerignore', 'deploy/seh/Dockerfile',
           'main_computer/slh/authenticator.py', 'main_computer/seh/__init__.py',
           'main_computer/seh/relay.py')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_SHA = re.compile(r'[0-9a-f]{40}\Z')


class SEHGitError(ValueError):
    """Refusal with optional, nonexecuted Git repair guidance for the CLI."""

    def __init__(self, message: str, *, git_command: str = ''):
        super().__init__(message)
        self.git_command = git_command


def _ps_quote(value: str) -> str:
    """Quote a literal Git path for PowerShell, matching Hub Control."""
    return "'" + value.replace("'", "''") + "'"


def _git_commit_push_command(paths: list[str]) -> str:
    """Stage only dirty SEH build inputs; never stage unrelated workspace files."""
    selected = sorted(set(paths) & set(SOURCES))
    if not selected:
        return ''
    quoted = ' '.join(_ps_quote(path) for path in selected)
    return (f'git add -- {quoted}; '
            f"if ($LASTEXITCODE -eq 0) {{ git commit --only -m 'Update SEH deployment source' -- {quoted} }}; "
            'if ($LASTEXITCODE -eq 0) { git push origin HEAD }')


def _dirty_source_paths(porcelain: str) -> list[str]:
    """Extract only known source paths from porcelain v1 (fixed safe path set)."""
    known = set(SOURCES)
    paths = []
    for line in porcelain.splitlines():
        if len(line) >= 4 and line[3:] in known:
            paths.append(line[3:])
    return sorted(set(paths))


def _json_digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _api(client, method: str, path: str, body: dict | None = None):
    """Adapt Hub Control's Coolify response to a strict, redacted SEH interface."""
    try:
        answer = client.request(method, path, body)
        if hasattr(answer, 'ok'):
            if not answer.ok:
                raise SEHGitError(f'Coolify {method} {path.split("?")[0]} returned HTTP {answer.status}')
            return answer.body
        return answer
    except SEHGitError:
        raise
    except Exception as exc:
        # Never stringify a connector exception; it might include a bearer token.
        raise SEHGitError(f'Coolify {method} failed: {type(exc).__name__}') from None


def _items(value, key: str) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [v for v in value[key] if isinstance(v, dict)]
    raise SEHGitError('Coolify returned an invalid ' + key + ' inventory')


def _one(rows: list[dict], name: str, kind: str) -> dict:
    matches = [row for row in rows if str(row.get('name') or '').strip() == name]
    if len(matches) != 1:
        raise SEHGitError(f'Expected exactly one {kind} named {name!r}; found {len(matches)}')
    if not isinstance(matches[0].get('uuid'), str) or not matches[0]['uuid']:
        raise SEHGitError(f'Selected {kind} has no UUID')
    return matches[0]


def _origin(controller: dict) -> str:
    value = next((controller[k] for k in ('url','coolify_url','api_url','base_url') if controller.get(k)), None)
    parts = urlsplit(str(value or ''))
    if (parts.scheme not in ('https','http') or not parts.hostname or parts.username or parts.password or
            parts.query or parts.fragment or parts.path not in ('', '/')):
        raise SEHGitError('Invalid Coolify management origin in selected SEH identity')
    return str(value).rstrip('/')


def _selections(root: Path, network: str, seh: str) -> tuple[dict, dict, dict, int, str]:
    _, state, doc = st.read(root, network)
    record = st.seh_doc(doc, network, seh)
    active = record['coolify']['active_identity']
    if not active:
        raise SEHGitError('SEH has no active Coolify identity')
    controller = record['coolify']['identities'][active]['controller']
    if controller.get('enabled') is False:
        raise SEHGitError('Active Coolify identity is disabled')
    if not controller.get('api_token'):
        raise SEHGitError('Copied Coolify identity has no token')
    return record, controller, doc, state.binding.generation, state.binding.content_hash.digest


def _client(controller: dict):
    # Explicitly scoped temporary HTTP exception, as authorized by the operator.
    # Reuses the established Hub Control client; tokens never enter plan artifacts.
    return hub_coolify.CoolifyClient(_origin(controller), controller['api_token'], retries=0)


def placement(client, controller: dict, *, project_name: str, environment_name: str) -> dict:
    project_id = str(controller.get('project_uuid') or '').strip()
    if project_id:
        project = _api(client, 'GET', f'/api/v1/projects/{project_id}')
        if not isinstance(project, dict) or project.get('uuid') != project_id or project.get('name') != project_name:
            raise SEHGitError('Selected controller project UUID does not match requested project name')
    else:
        project = _one(_items(_api(client, 'GET', '/api/v1/projects'), 'projects'), project_name, 'project')
        project_id = project['uuid']
    envs = _items(_api(client,'GET',f'/api/v1/projects/{project_id}/environments'), 'environments')
    environment = _one(envs, environment_name, 'environment')
    server_id = str(controller.get('server_uuid') or '').strip()
    if server_id:
        server = _api(client,'GET',f'/api/v1/servers/{server_id}')
        if not isinstance(server, dict) or server.get('uuid') != server_id:
            raise SEHGitError('Selected controller server UUID cannot be verified')
    else:
        servers = _items(_api(client, 'GET', '/api/v1/servers'), 'servers')
        usable = [s for s in servers if s.get('is_usable') is not False and s.get('is_reachable') is not False and s.get('uuid')]
        if len(usable) != 1:
            raise SEHGitError('Server choice is ambiguous; configure server_uuid in SEH Coolify identity')
        server = usable[0]
        server_id = server['uuid']
    if server.get('is_usable') is False or server.get('is_reachable') is False:
        raise SEHGitError('Selected Coolify server is not usable')
    return {'project_uuid': project_id, 'project_name': project_name,
            'environment_uuid':environment['uuid'], 'environment_name':environment_name,
            'server_uuid':server_id}


def git_source(repo_root: Path, *, branch: str = 'main') -> dict:
    """Require locally clean, exact HEAD and remote published branch; never push."""
    repo_root = Path(repo_root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9._/-]+', branch) or '..' in branch:
        raise SEHGitError('Unsafe Git branch')
    for path in SOURCES:
        if not (repo_root / path).is_file():
            raise SEHGitError(f'Missing SEH Git build source: {path}')
    def git(*args):
        try:
            result = subprocess.run(['git', '-C', str(repo_root), *args],
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=25, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SEHGitError(f'Git source verification unavailable: {type(exc).__name__}') from None
        if result.returncode != 0:
            raise SEHGitError('Git source verification failed for ' + str(args[0]))
        # Preserve porcelain's leading status columns (e.g. ' M path');
        # strip only Git's trailing line terminator.
        return result.stdout.rstrip('\r\n')
    local = git('rev-parse', 'HEAD')
    if not _SHA.fullmatch(local):
        raise SEHGitError('Invalid Git HEAD revision')
    worktree = git('status','--porcelain=v1','--untracked-files=all','--',*SOURCES)
    if worktree:
        paths = _dirty_source_paths(worktree)
        command = _git_commit_push_command(paths)
        raise SEHGitError('SEH runtime source has uncommitted changes; '
                          'run the emitted SEH_GIT_COMMIT_PUSH_COMMAND, then retry prepare',
                          git_command=command)
    for path in SOURCES:
        git('cat-file', '-e', f'HEAD:{path}')
    remote = git('ls-remote', '--exit-code', 'origin', f'refs/heads/{branch}').split()
    if not remote or remote[0] != local:
        raise SEHGitError('SEH Git HEAD is not the pushed origin branch revision; '
                          'push the committed SEH source, then retry prepare',
                          git_command='git push origin HEAD')
    origin = git('remote', 'get-url', 'origin')
    # Existing Hub deployment uses the same GitHub repository; no private SSH
    # remote paths may be sent to Coolify as a public Git URL.
    normalized = origin.removesuffix('.git').replace('git@github.com:', 'https://github.com/')
    if normalized.rstrip('/') != 'https://github.com/johnrraymond/main_computer':
        raise SEHGitError('Git origin is not the expected Main Computer repository')
    return {'repository':'https://github.com/johnrraymond/main_computer', 'branch':branch, 'revision':local,
            'dockerfile_location':DOCKERFILE}


def _public_key() -> str:
    try:
        from tools.slh_seh_connector import default_state, service_identity
        _, key = service_identity(default_state(), create=False)
        if key:
            base64.b64decode(key, validate=True)
            return key
    except (OSError, ValueError, ImportError):
        pass
    raise SEHGitError('Existing SLH public relay identity unavailable; run slh_seh_connector identity first')


def _plan_path(root: Path, network: str, seh: str) -> Path:
    st.ident(network,'network'); st.ident(seh,'seh')
    return Path(root).parent / 'deployments' / network / 'sehs' / seh / 'git-plan.json'


def prepare(root: Path, network: str, seh: str, *, project_name: str = 'My first project',
            environment_name: str | None = None, public_url: str | None = None,
            repo_root: Path | None = None, client=None, source=None, public_key: str | None = None) -> dict:
    record, controller, _, generation, state_hash = _selections(root, network, seh)
    destination = client if client is not None else _client(controller)
    place = placement(destination, controller, project_name=project_name,
                      environment_name=environment_name or f'{network}-hubs')
    git = source if source is not None else git_source(
        repo_root or Path(__file__).resolve().parents[2])
    key = public_key if public_key is not None else _public_key()
    if not isinstance(key, str) or not key:
        raise SEHGitError('SLH public relay identity is required')
    url = public_url or record['deployment'].get('public_url') or DEFAULT_PUBLIC_URL
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port or parts.query or parts.fragment:
        raise SEHGitError('SEH public URL must be a hostname-only HTTPS origin')
    active = record['coolify']['active_identity']
    current_uuid = str(record['deployment'].get('application_uuid') or '')
    spec = {'schema':'main-computer.seh-git-plan.v1', 'network':network, 'seh':seh,
            'organization_id':record['organization_id'], 'slh_id':record['slh_id'],
            'coolify_identity':active, 'coolify_origin':_origin(controller),
            'state_generation':generation, 'state_content_sha256':state_hash,
            'placement':place, 'source':git, 'public_url':url,
            'internal_port':8080, 'application_uuid':current_uuid,
            'application_name':f'mc-seh-{seh}',
            'ownership_marker':f'main-computer-seh:git-v1 target={seh} org={record["organization_id"]} slh={record["slh_id"]}',
            'slh_public_key_b64':key}
    spec['plan_digest'] = _json_digest(spec)
    path = _plan_path(root,network,seh)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(spec,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return {'status':'PREPARED','network':network,'seh':seh, 'plan_digest':spec['plan_digest'],
            'plan_path':str(path),'controller':active,'coolify_origin':spec['coolify_origin'],
            'placement':place,'git_revision':git['revision'],'application_uuid':current_uuid,
            'public_url':url,'remote_mutation':False,
            'management_transport':'OPERATOR_ACCEPTED_HTTP' if spec['coolify_origin'].startswith('http://') else 'HTTPS'}


def _loaded(root,network,seh):
    path = _plan_path(root,network,seh)
    if not path.is_file():
        raise SEHGitError('No prepared Git deployment: run seh_harness prepare first')
    plan = json.loads(path.read_text(encoding='utf-8'))
    digest = plan.pop('plan_digest','')
    if _json_digest(plan) != digest or plan.get('schema') != 'main-computer.seh-git-plan.v1' or (plan.get('network'),plan.get('seh')) != (network,seh):
        raise SEHGitError('Prepared Git plan was modified or does not match selected SEH')
    plan['plan_digest'] = digest
    return plan


def _application_payload(plan: dict) -> dict:
    place = plan['placement']
    url = plan['public_url'].rstrip('/')
    return {'name':plan['application_name'],'description':plan['ownership_marker'],
            'project_uuid':place['project_uuid'], 'server_uuid':place['server_uuid'],
            'environment_name':place['environment_name'],
            'git_repository':plan['source']['repository'], 'git_branch':plan['source']['branch'],
            'git_commit_sha':plan['source']['revision'], 'build_pack':'dockerfile',
            'base_directory':'/', 'dockerfile_location':plan['source']['dockerfile_location'],
            'ports_exposes':str(plan['internal_port']), 'domains':f'{url}:{plan["internal_port"]}',
            'health_check_enabled':True,'health_check_path':'/healthz', 'instant_deploy':False}


def _verify_application_placement(client, candidate: dict, place: dict) -> None:
    """Verify live placement using Coolify GET fields, not POST-only UUIDs.

    Coolify's application-detail API documents environment_id, but does not
    promise project_uuid, server_uuid or environment_name. The environment ID
    is checked against the selected project's environment; the application's
    membership in the selected server's resources verifies its server. No
    missing field is treated as a matching value.
    """
    environment = candidate.get('environment')
    nested = environment if isinstance(environment, dict) else {}

    project_uuid = candidate.get('project_uuid')
    if project_uuid and project_uuid != place['project_uuid']:
        raise SEHGitError('SEH application project does not match requested placement')
    server_uuid = candidate.get('server_uuid')
    if server_uuid and server_uuid != place['server_uuid']:
        raise SEHGitError('SEH application server does not match requested placement')
    env_name = candidate.get('environment_name') or nested.get('name')
    if env_name and env_name != place['environment_name']:
        raise SEHGitError('SEH application environment does not match requested placement')
    env_uuid = candidate.get('environment_uuid') or nested.get('uuid')
    if env_uuid and env_uuid != place['environment_uuid']:
        raise SEHGitError('SEH application environment UUID does not match requested placement')

    # The real Coolify GET /applications/{uuid} usually provides an integer
    # environment_id instead of a project UUID. Fetch the environment *within*
    # the already verified project, and compare the actual database IDs.
    environment_id = candidate.get('environment_id')
    if environment_id is not None:
        expected = _api(client, 'GET',
                        f"/api/v1/projects/{place['project_uuid']}/{place['environment_uuid']}")
        if (not isinstance(expected, dict) or expected.get('id') is None or
                str(expected['id']) != str(environment_id)):
            raise SEHGitError('SEH application environment ID does not match requested project/environment')
    elif not (project_uuid == place['project_uuid'] and
              (env_name == place['environment_name'] or env_uuid == place['environment_uuid'])):
        raise SEHGitError('SEH application project/environment cannot be verified: no environment_id')

    if not server_uuid:
        # The application GET response may omit server_uuid too. Coolify's
        # /servers/{uuid}/resources endpoint independently lists the actual
        # resources hosted by that server, including this application's UUID.
        resources = _items(_api(client, 'GET',
                       f"/api/v1/servers/{place['server_uuid']}/resources"), 'resources')
        if not any(resource.get('uuid') == candidate.get('uuid') for resource in resources):
            raise SEHGitError('SEH application is not listed on selected Coolify server')


def _lookup(client,plan) -> tuple[str,bool]:
    apps = _items(_api(client,'GET','/api/v1/applications'),'applications')
    matches = [a for a in apps if a.get('name') == plan['application_name']]
    known_uuid = plan['application_uuid']
    if known_uuid:
        if matches and any(m.get('uuid') != known_uuid for m in matches):
            raise SEHGitError('Conflicting application name on selected Coolify host')
        details = _api(client,'GET',f'/api/v1/applications/{known_uuid}')
        if not isinstance(details,dict) or details.get('uuid') != known_uuid:
            raise SEHGitError('Recorded application UUID is not accessible on selected controller')
        matches = [details]
    if len(matches) > 1:
        raise SEHGitError('Multiple Coolify applications share the intended SEH name')
    if not matches:
        return '',False
    candidate = matches[0]
    if not known_uuid:
        uuid = str(candidate.get('uuid') or '')
        if not uuid:
            raise SEHGitError('SEH application listing has no UUID')
        details = _api(client,'GET',f'/api/v1/applications/{uuid}')
        if not isinstance(details,dict) or details.get('uuid') != uuid:
            raise SEHGitError('SEH application detail invalid')
        candidate = details
    if candidate.get('name') != plan['application_name'] or candidate.get('description') != plan['ownership_marker']:
        raise SEHGitError('SEH application name collision without matching ownership marker')
    _verify_application_placement(client, candidate, plan['placement'])
    return str(candidate['uuid']), True


def _verify_existing_slh_public_key(client, application_uuid: str, expected_key: str) -> None:
    """Refuse silent SLH trust-key rotation on an owned Coolify application.

    Some Coolify installations mask env values; an opaque existing key cannot
    be treated as permission to overwrite the service's current trust anchor.
    No existing env row is allowed during initial configuration.
    """
    envs = _items(_api(client, 'GET', f'/api/v1/applications/{application_uuid}/envs'), 'envs')
    rows = [row for row in envs if row.get('key') == 'SEH_SLH_PUBLIC_KEY_B64'
            and row.get('is_preview') is not True]
    if len(rows) > 1:
        raise SEHGitError('Existing SEH has ambiguous SLH public trust identities')
    if not rows:
        return
    row = rows[0]
    actual = row.get('real_value') or row.get('value')
    if not isinstance(actual, str) or actual != expected_key:
        raise SEHGitError('Existing SEH SLH public trust key differs or is masked; '
                          'refusing silent key rotation or unknown trust replacement')


def _envs(plan):
    return {'SEH_PUBLIC_ORIGIN':plan['public_url'], 'SEH_AUDIT_MODE':'metadata_only',
            'SEH_ORGANIZATION_ID':plan['organization_id'], 'SEH_SLH_ID':plan['slh_id'],
            'SEH_SLH_PUBLIC_KEY_B64':plan['slh_public_key_b64'], 'SEH_PORT':str(plan['internal_port'])}


def preflight(root: Path, network: str, seh: str, *, repo_root: Path | None = None,
              client=None, source=None) -> dict:
    plan = _loaded(root,network,seh)
    record, controller, _, generation, state_hash = _selections(root,network,seh)
    if (plan['state_generation'],plan['state_content_sha256'],plan['coolify_identity'],plan['coolify_origin']) != (
            generation,state_hash,record['coolify']['active_identity'],_origin(controller)):
        raise SEHGitError('Identity changed since prepare; run prepare again')
    actual_source = source if source is not None else git_source(repo_root or Path(__file__).resolve().parents[2], branch=plan['source']['branch'])
    if actual_source != plan['source']:
        raise SEHGitError('Git source changed since prepare; run prepare again')
    host = client if client is not None else _client(controller)
    actual_place = placement(host,controller,project_name=plan['placement']['project_name'],
                             environment_name=plan['placement']['environment_name'])
    if actual_place != plan['placement']:
        raise SEHGitError('Coolify placement changed since prepare')
    app, existing = _lookup(host,plan)
    if existing:
        _verify_existing_slh_public_key(host, app, plan['slh_public_key_b64'])
    return {'status':'EXISTING_APPLICATION_VERIFIED' if existing else 'CREATE_ELIGIBLE',
            'seh':seh,'network':network,'plan_digest':plan['plan_digest'],
            'application_uuid':app or None,'coolify_identity':plan['coolify_identity'],
            'placement':actual_place,'remote_mutation':False}


def deploy(root: Path, network: str, seh: str, *, operation_id: str,
           repo_root: Path | None = None, client=None, source=None) -> dict:
    plan = _loaded(root,network,seh)
    record, controller, _, generation, state_hash = _selections(root,network,seh)
    if (plan['state_generation'],plan['state_content_sha256'],plan['coolify_identity'],plan['coolify_origin']) != (
            generation,state_hash,record['coolify']['active_identity'],_origin(controller)):
        raise SEHGitError('Identity changed since prepare; run prepare again')
    current = source if source is not None else git_source(repo_root or Path(__file__).resolve().parents[2], branch=plan['source']['branch'])
    if current != plan['source']:
        raise SEHGitError('Git source changed since prepare; run prepare again')
    destination = client if client is not None else _client(controller)
    actual_place = placement(destination,controller,project_name=plan['placement']['project_name'],
                             environment_name=plan['placement']['environment_name'])
    if actual_place != plan['placement']:
        raise SEHGitError('Coolify placement changed since prepare')
    uuid, existing = _lookup(destination,plan)
    if existing:
        _verify_existing_slh_public_key(destination, uuid, plan['slh_public_key_b64'])
    if not uuid:
        response = _api(destination,'POST','/api/v1/applications/public',_application_payload(plan))
        uuid = str((response.get('application') or {}).get('uuid') or response.get('uuid') or '') if isinstance(response,dict) else ''
        if not uuid:
            raise SEHGitError('Coolify application creation ambiguous; inspect inventory before retry')
    # Commit exact adopted/created UUID BEFORE env sync or deploy trigger.
    if not record['deployment'].get('application_uuid'):
        def save(doc,gen,base):
            item = st.seh_doc(doc,network,seh)['deployment']
            if item.get('application_uuid') and item['application_uuid'] != uuid:
                raise st.SEHIdentityError('Conflicting recorded SEH application UUID')
            item['application_uuid'] = uuid
            item['git'] = {'mode':'coolify-git', 'enabled':True, 'project_uuid':plan['placement']['project_uuid'],
                           'server_uuid':plan['placement']['server_uuid'],
                           'environment_uuid':plan['placement']['environment_uuid'],
                           'application_name':plan['application_name']}
        st.mutate(root,network,generation,operation_id,save)
    elif record['deployment']['application_uuid'] != uuid:
        raise SEHGitError('Existing SEH application UUID differs from prepared target')
    if existing:
        changes = {k:v for k,v in _application_payload(plan).items()
                   if k not in ('project_uuid','server_uuid','environment_name','git_repository')}
        _api(destination,'PATCH',f'/api/v1/applications/{uuid}',changes)
    payload = {'data':[{'key':k,'value':v,'is_literal':True,'is_preview':False,'is_runtime':True,
                         'is_buildtime':False,'is_multiline':False} for k,v in _envs(plan).items()]}
    _api(destination,'PATCH',f'/api/v1/applications/{uuid}/envs/bulk',payload)
    items = _items(_api(destination,'GET',f'/api/v1/applications/{uuid}/envs'),'envs')
    seen = {row.get('key'):row for row in items if row.get('is_preview') is not True}
    for key,value in _envs(plan).items():
        if key not in seen or value not in (seen[key].get('value'),seen[key].get('real_value')):
            raise SEHGitError('Coolify SEH runtime environment readback mismatched; deploy not triggered')
    # Hub Control's proven deploy endpoint selection handles Coolify API variants.
    try:
        triggered = hub_coolify.trigger_deploy(destination,application_uuid=uuid,force=True,tried=[])
    except Exception as exc:
        raise SEHGitError(f'Coolify deploy trigger failed: {type(exc).__name__}') from None
    return {'status':'DEPLOY_QUEUED','network':network,'seh':seh,'application_uuid':uuid,
            'created':not existing,'coolify_origin':plan['coolify_origin'],'public_url':plan['public_url'],
            'project':plan['placement']['project_name'],'environment':plan['placement']['environment_name'],
            'git_revision':plan['source']['revision'], 'plan_digest':plan['plan_digest'],
            'identity_committed':True,'ready':False, 'public_wss_verified':False}


def observed_public_origin(root: Path, network: str, seh: str, *, client=None) -> dict:
    """Read-only discovery from the registered application, not a cached plan.

    Only one domain on the exact, previously registered application with the
    expected SEH ownership marker is acceptable. A missing/ambiguous domain
    must be resolved by an operator-supplied --public-url; never silently use
    the Git backend's default or an old plan's hostname.
    """
    from .dns_probe import domain_hosts

    record, controller, _, _, _ = _selections(root, network, seh)
    application_uuid = record['deployment'].get('application_uuid')
    if not isinstance(application_uuid, str) or not application_uuid:
        raise SEHGitError('SEH has no registered Coolify application UUID')
    destination = client if client is not None else _client(controller)
    app = _api(destination, 'GET', f'/api/v1/applications/{application_uuid}')
    if (not isinstance(app, dict) or app.get('uuid') != application_uuid or
            app.get('name') != f'mc-seh-{seh}' or
            app.get('description') !=
            f'main-computer-seh:git-v1 target={seh} org={record["organization_id"]} slh={record["slh_id"]}'):
        raise SEHGitError('Registered SEH Coolify application ownership could not be verified')
    hosts = domain_hosts(app)
    if len(hosts) != 1:
        raise SEHGitError('Registered SEH Coolify application does not have exactly one public domain; pass --public-url')
    return {'status': 'COOLIFY_APPLICATION_DOMAIN_OBSERVED',
            'application_uuid': application_uuid, 'public_url': 'https://' + hosts[0],
            'remote_mutation': False, 'verified_ownership': True}


def status(root: Path,network: str,seh: str,*,client=None, dns_resolver=None) -> dict:
    from .dns_probe import domain_hosts, probe

    record,ctl,_,_,_ = _selections(root,network,seh)
    uuid=record['deployment'].get('application_uuid')
    app = None
    if uuid:
        dest = client if client is not None else _client(ctl)
        app = _api(dest,'GET',f'/api/v1/applications/{uuid}')
        if not isinstance(app,dict) or app.get('uuid') != uuid:
            raise SEHGitError('Recorded SEH application missing from Coolify controller')

    # Prefer the explicitly selected public URL, then the frozen Git plan.
    # The live application domain is a fallback if the operator runs status
    # from a machine without the original prepared-plan file.
    url = record['deployment'].get('public_url')
    if not url and _plan_path(root,network,seh).is_file():
        try:
            url = _loaded(root,network,seh).get('public_url')
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Status must remain usable if local planning evidence is stale.
    if not url and app:
        candidates = domain_hosts(app)
        if len(candidates) == 1:
            url = 'https://' + candidates[0]
    dns = probe(url, app, resolver=dns_resolver)
    result = {'status':'COOLIFY_OBSERVED' if uuid else 'NOT_DEPLOYED',
              'seh':seh,'network':network,
              'public_url':url, 'dns':dns, 'dns_ready':dns['ready'],
              'public_wss_verified':False}
    if uuid:
        result.update(application_uuid=uuid, coolify_status=app.get('status') or 'unknown')
    return result
