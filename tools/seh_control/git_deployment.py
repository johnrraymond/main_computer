"""Git-backed SEH deployment and guarded canonical Coolify metadata reconciliation.

The migrated Mother record owns the controller and the existing application UUID.
No copied aliases or legacy application names authorize a new target.
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
    # The operator currently permits HTTP for the configured Coolify controller.
    # Preserve the verified origin exactly; do not silently upgrade, redirect,
    # or change the public SEH URL. HTTP transmits the API token unencrypted.
    return hub_coolify.CoolifyClient(_origin(controller), controller['api_token'], retries=0)


# Generic Coolify display text, never a selection key or ownership marker.
CANONICAL_APPLICATION_NAME = 'Secure External Hub'
CANONICAL_DESCRIPTION = 'Main Computer external relay'
_LEGACY_MARKER = re.compile(
    r'main-computer-seh:git-v(?:1|2|3) target=([A-Za-z0-9_.-]+)'
    r'(?: org=[A-Za-z0-9_.-]+)?(?: slh=[A-Za-z0-9_.-]+)?\Z'
)


def _canonical_selection(root: Path, network: str) -> tuple[dict, dict, int, str]:
    """Resolve the one migrated deployment without any user-selected alias."""
    _, verified, document = st.read(root, network, 'remote-metadata')
    record = st.deployment_record(document, network)
    if record is None:
        raise SEHGitError('No canonical SEH deployment; migrate Mother state first')
    uuid = record['deployment'].get('application_uuid')
    if not isinstance(uuid, str) or not _ID.fullmatch(uuid):
        raise SEHGitError('Canonical deployment has no valid Coolify application UUID')
    ctl = record['coolify']['controller']
    if ctl.get('enabled') is False:
        raise SEHGitError('Canonical Coolify controller is disabled')
    return record, ctl, verified.binding.generation, verified.binding.content_hash.digest


def _canonical_placement(client, record: dict) -> dict:
    """Bind UUID to the project's environment and server, not application name."""
    git = record['deployment'].get('git') or {}
    ids = {k: git.get(k) for k in ('project_uuid', 'environment_uuid', 'server_uuid')}
    if any(not isinstance(v, str) or not _ID.fullmatch(v) for v in ids.values()):
        raise SEHGitError('Canonical deployment is missing verified Coolify placement UUIDs')
    environments = _items(_api(client, 'GET',
                       f"/api/v1/projects/{ids['project_uuid']}/environments"), 'environments')
    chosen = [e for e in environments if e.get('uuid') == ids['environment_uuid']]
    if len(chosen) != 1 or not isinstance(chosen[0].get('name'), str) or not chosen[0]['name']:
        raise SEHGitError('Canonical SEH environment UUID is not uniquely registered')
    return {**ids, 'environment_name': chosen[0]['name']}


def _verify_canonical_metadata(app: dict) -> str:
    """Recognize the exact older service convention only for guarded migration."""
    name, marker = app.get('name'), app.get('description')
    if name == CANONICAL_APPLICATION_NAME and marker == CANONICAL_DESCRIPTION:
        return 'CANONICAL'
    if not isinstance(name, str) or not isinstance(marker, str):
        raise SEHGitError('Unrecognized Coolify application metadata; refusing adoption')
    match = _LEGACY_MARKER.fullmatch(marker)
    if not match or name != 'mc-seh-' + match.group(1):
        raise SEHGitError('Unrecognized Coolify ownership marker; refusing adoption')
    return 'LEGACY_VERIFIED'


# Coolify can report a GitHub application repository as a full HTTPS URL, an
# SSH clone URL, or an owner/repository pair. All forms must resolve to this
# EXACT repository; do not accept an arbitrary GitHub URL on UUID alone.
_EXPECTED_GITHUB_REPOSITORY = 'johnrraymond/main_computer'


def _github_repository(value: object) -> str | None:
    """Canonicalize a limited set of credential-free GitHub source forms."""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw or any(c.isspace() for c in raw):
        return None
    if raw.startswith('git@github.com:'):
        slug = raw[len('git@github.com:'):]
    elif '://' in raw:
        parts = urlsplit(raw)
        try:
            invalid_port = parts.port is not None
        except ValueError:
            return None
        if (parts.hostname or '').lower() != 'github.com' or parts.password or \
                parts.query or parts.fragment or invalid_port:
            return None
        if parts.scheme == 'https' and parts.username is None:
            slug = parts.path.lstrip('/')
        elif parts.scheme == 'ssh' and parts.username == 'git':
            slug = parts.path.lstrip('/')
        else:
            return None
    else:
        slug = raw
    slug = slug.rstrip('/')
    if slug.lower().endswith('.git'):
        slug = slug[:-4]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', slug):
        return None
    return slug.lower()


def _verify_canonical_source(app: dict) -> None:
    """Verify the configured Git repo, not whether the operator pushed HEAD.

    Coolify's GET application payload uses git_repository for Git apps;
    git_full_url is a documented alternate presentation. Never treat a
    missing or different repository as proof that a git push is needed.
    """
    sources = [(field, app[field]) for field in ('git_repository', 'repository', 'git_full_url')
               if isinstance(app.get(field), str) and app[field].strip()]
    if not sources:
        raise SEHGitError('Coolify application Git repository cannot be verified: '
                          'no repository source field was returned for the registered UUID. '
                          'A Git push will not change this Coolify configuration')
    # Refuse contradictory API fields rather than trusting whichever happens
    # to be first. A verified UUID is not authorization to adopt a new source.
    for source_field, source_value in sources:
        repo = _github_repository(source_value)
        if repo != _EXPECTED_GITHUB_REPOSITORY:
            observed = 'github.com/' + repo if repo else 'unrecognized or unsafe repository value'
            raise SEHGitError('Coolify application Git repository cannot be verified: '
                              f'{source_field} identifies {observed}, not the expected Main Computer repository. '
                              'A Git push will not change this Coolify configuration')
    dockerfile = app.get('dockerfile_location')
    if dockerfile not in (DOCKERFILE, DOCKERFILE.lstrip('/')):
        raise SEHGitError('Coolify application SEH Dockerfile cannot be verified: '
                          'the existing application is not configured with the expected SEH Dockerfile. '
                          'A Git push will not change this Coolify configuration')


def _trust_snapshot(client, uuid: str) -> list[dict]:
    """Compare trust env rows exactly; never modify the remote trust anchor."""
    rows = _items(_api(client, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
    keys = {'SEH_SLH_ID', 'SEH_SLH_PUBLIC_KEY_B64', 'SEH_ORGANIZATION_ID'}
    trust = [deepcopy(r) for r in rows if r.get('is_preview') is not True and r.get('key') in keys]
    if len({r['key'] for r in trust}) != len(trust):
        raise SEHGitError('Duplicate remote SEH trust environment fields; refusing metadata change')
    return sorted(trust, key=lambda r: r['key'])


def reconcile_metadata(root: Path, network: str, *, execute: bool = False, client=None) -> dict:
    """Plan or apply ONLY generic display metadata to the exact registered UUID.

    No application creation, key replacement, deploy trigger, or Mother mutation.
    HTTP management transport is temporarily operator-approved; injected clients
    are used for isolated tests, not remote authentication.
    """
    record, controller, generation, binding = _canonical_selection(root, network)
    uuid = record['deployment']['application_uuid']
    origin = _origin(controller)
    host = client if client is not None else _client(controller)
    app = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if not isinstance(app, dict) or app.get('uuid') != uuid:
        raise SEHGitError('Registered Coolify application UUID is unavailable; no replacement permitted')
    place = _canonical_placement(host, record)
    _verify_application_placement(host, app, place)
    _verify_canonical_source(app)
    kind = _verify_canonical_metadata(app)
    trust_before = _trust_snapshot(host, uuid)
    detail = {'network': network, 'application_uuid': uuid, 'mother_generation': generation,
              'current_metadata': kind, 'remote_mutation': False, 'trust_untouched': True,
              'management_transport': 'OPERATOR_ACCEPTED_HTTP' if origin.startswith('http://') else 'HTTPS',
              'new_name': CANONICAL_APPLICATION_NAME,
              'new_description': CANONICAL_DESCRIPTION}
    if not execute:
        return {'status': 'PLAN_ONLY', **detail}
    if kind == 'CANONICAL':
        return {'status': 'UNCHANGED', **detail}
    # Check both the verified local binding and remote state again immediately
    # before the only write. A stale state or changed target cannot be adopted.
    _, _, later_generation, later_hash = _canonical_selection(root, network)
    if (later_generation, later_hash) != (generation, binding):
        raise SEHGitError('Mother generation changed during SEH metadata reconciliation')
    current = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if current != app or _trust_snapshot(host, uuid) != trust_before:
        raise SEHGitError('Coolify application or trust changed during metadata reconciliation')
    _api(host, 'PATCH', f'/api/v1/applications/{uuid}',
         {'name': CANONICAL_APPLICATION_NAME, 'description': CANONICAL_DESCRIPTION})
    updated = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if not isinstance(updated, dict) or updated.get('uuid') != uuid or \
            _verify_canonical_metadata(updated) != 'CANONICAL':
        raise SEHGitError('Coolify metadata readback failed; do not retry blindly')
    _verify_application_placement(host, updated, place)
    _verify_canonical_source(updated)
    # The PATCH must not silently rewrite domain, source, build or runtime
    # configuration. Ignore Coolify-managed timestamps and status fields.
    protected_fields = ('domains', 'git_repository', 'repository', 'git_branch',
                        'git_commit_sha', 'dockerfile_location', 'build_pack',
                        'ports_exposes', 'project_uuid', 'environment_uuid',
                        'environment_id', 'server_uuid')
    if any(updated.get(key) != app.get(key) for key in protected_fields):
        raise SEHGitError('Coolify changed protected application configuration; reconciliation incomplete')
    if _trust_snapshot(host, uuid) != trust_before:
        raise SEHGitError('SEH trust unexpectedly changed; operation is incomplete')
    _, _, last_gen, last_hash = _canonical_selection(root, network)
    if (last_gen, last_hash) != (generation, binding):
        raise SEHGitError('Mother changed during remote metadata update; reconcile before retry')
    return {'status': 'METADATA_RECONCILED', **{**detail, 'remote_mutation': True}}


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
    """Retained for compatibility with legacy diagnostics; no key creation."""
    try:
        from tools.slh_seh_connector import default_state, service_identity
        _, key = service_identity(default_state(), create=False)
        if key:
            base64.b64decode(key, validate=True)
            return key
    except (OSError, ValueError, ImportError):
        pass
    raise SEHGitError('Existing SLH public relay identity unavailable; restore or initialize SLH through its governed lifecycle')


def _plan_path(root: Path, network: str, seh: str | None = None) -> Path:
    st.ident(network, 'network')
    if seh is None:
        # Canonical deployment: one deterministic location, no named alias.
        return Path(root).parent / 'deployments' / network / 'seh' / 'git-plan.json'
    # Unmigrated v1 plan reader only. Canonical state never selects this path.
    st.ident(seh, 'legacy SEH')
    return Path(root).parent / 'deployments' / network / 'sehs' / seh / 'git-plan.json'


def prepare(root: Path, network: str, seh: str, *, project_name: str = 'My first project',
            environment_name: str | None = None, public_url: str | None = None,
            repo_root: Path | None = None, client=None, source=None, public_key: str | None = None) -> dict:
    record, controller, _, generation, state_hash = _selections(root, network, seh)
    if not record.get('slh_id'):
        raise SEHGitError('SEH requires registered SLH identity before deployment')
    destination = client if client is not None else _client(controller)
    place = placement(destination, controller, project_name=project_name,
                      environment_name=environment_name or f'{network}-hubs')
    git = source if source is not None else git_source(
        repo_root or Path(__file__).resolve().parents[2])
    # Deployment is infrastructure provisioning, not SLH onboarding.
    # The operator path never passes a key and never reads local SLH state.
    # A separately supplied key remains supported for explicit legacy binding.
    if public_key is not None and (not isinstance(public_key, str) or not public_key):
        raise SEHGitError('Invalid explicit SLH public relay key')
    url = public_url or record['deployment'].get('public_url') or DEFAULT_PUBLIC_URL
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port or parts.query or parts.fragment:
        raise SEHGitError('SEH public URL must be a hostname-only HTTPS origin')
    active = record['coolify']['active_identity']
    current_uuid = str(record['deployment'].get('application_uuid') or '')
    slh = record.get('slh_id')
    marker = f'main-computer-seh:git-v3 target={seh} slh={slh}'
    spec = {'schema':'main-computer.seh-git-plan.v1', 'network':network, 'seh':seh,
            'slh_id':slh,
            'coolify_identity':active, 'coolify_origin':_origin(controller),
            'state_generation':generation, 'state_content_sha256':state_hash,
            'placement':place, 'source':git, 'public_url':url,
            'internal_port':8080, 'application_uuid':current_uuid,
            'application_name':f'mc-seh-{seh}',
            'ownership_marker':marker,
            'slh_public_key_b64':public_key or ''}
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
    if plan.get('schema') == 'main-computer.seh-git-plan.v2':
        # Never select a new application by display name after Mother migration.
        uuid = plan.get('application_uuid')
        if not isinstance(uuid, str) or not _ID.fullmatch(uuid):
            raise SEHGitError('Canonical SEH plan requires the previously registered application UUID')
        app = _api(client, 'GET', f'/api/v1/applications/{uuid}')
        if not isinstance(app, dict) or app.get('uuid') != uuid:
            raise SEHGitError('Registered Coolify UUID unavailable; refusing creation')
        _verify_application_placement(client, app, plan['placement'])
        _verify_canonical_source(app)
        if _verify_canonical_metadata(app) != 'CANONICAL':
            raise SEHGitError('Existing Coolify metadata must be reconciled before deployment')
        return uuid, True
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
    # Coolify existing SLH binding variables are NEVER rewritten during an
    # infrastructure-only redeploy. On a fresh deploy they are deliberately
    # absent: the relay starts unbound and rejects all tunnels.
    result = {'SEH_PUBLIC_ORIGIN':plan['public_url'],
              'SEH_AUDIT_MODE':'metadata_only', 'SEH_PORT':str(plan['internal_port'])}
    if plan.get('slh_public_key_b64'):
        if not plan.get('slh_id'):
            raise SEHGitError('Explicit SLH binding requires complete association')
        result.update(SEH_SLH_ID=plan['slh_id'],
                      SEH_SLH_PUBLIC_KEY_B64=plan['slh_public_key_b64'])
    return result


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
    if existing and plan.get('slh_public_key_b64'):
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
    if existing and plan.get('slh_public_key_b64'):
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
    # Capture the existing remote trust binding to prove an infrastructure
    # redeploy never drops or mutates it. We do not read local SLH state.
    trust_keys = {'SEH_SLH_ID', 'SEH_SLH_PUBLIC_KEY_B64'}
    original_trust = {}
    if existing and not plan.get('slh_public_key_b64'):
        rows = _items(_api(destination, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
        for row in rows:
            if row.get('is_preview') is True or row.get('key') not in trust_keys:
                continue
            key = row['key']
            if key in original_trust:
                raise SEHGitError('Ambiguous existing SEH relay trust variables; deployment refused')
            original_trust[key] = (row.get('real_value') or row.get('value'))
        if original_trust and set(original_trust) != trust_keys:
            raise SEHGitError('Incomplete existing SEH relay binding; deployment refused')
    if existing:
        changes = {k:v for k,v in _application_payload(plan).items()
                   if k not in ('project_uuid','server_uuid','environment_name','git_repository')}
        _api(destination,'PATCH',f'/api/v1/applications/{uuid}',changes)
    payload = {'data':[{'key':k,'value':v,'is_literal':True,'is_preview':False,'is_runtime':True,
                         'is_buildtime':False,'is_multiline':False} for k,v in _envs(plan).items()]}
    _api(destination,'PATCH',f'/api/v1/applications/{uuid}/envs/bulk',payload)
    items = _items(_api(destination,'GET',f'/api/v1/applications/{uuid}/envs'),'envs')
    seen = {row.get('key'):row for row in items if row.get('is_preview') is not True}
    if original_trust:
        for key, prior in original_trust.items():
            current = seen.get(key)
            if current is None or (current.get('real_value') or current.get('value')) != prior:
                raise SEHGitError('Existing SEH relay binding changed during infrastructure deployment; refusing trigger')
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




def _diagnostic_env_value(rows: list[dict], name: str) -> dict:
    """Resolve a Coolify env value by *validation*, never field precedence.

    Coolify may provide a masked/encoded ``real_value`` alongside the usable
    ``value``. Accept a well-formed value from either only if the other field
    does not contain a different well-formed value. Ambiguity is fail-closed.
    Public keys are validated as P-256 PEM before any comparison.
    """
    from main_computer.slh.authenticator import load_public_key

    selected = [row for row in rows if row.get('key') == name and row.get('is_preview') is not True]
    if len(selected) > 1:
        return {'state': 'DUPLICATE', 'value': None}
    if not selected:
        return {'state': 'MISSING', 'value': None}

    candidates = set()
    masked = False
    nonempty = False
    for attribute in ('value', 'real_value'):
        raw = selected[0].get(attribute)
        if not isinstance(raw, str) or not raw.strip():
            continue
        nonempty = True
        actual = raw.strip()
        if (re.fullmatch(r'[\*\x95\u2022Xx]{4,}', actual) or
                actual.lower() in ('[hidden]', '[redacted]', '<redacted>')):
            masked = True
            continue
        # Some Coolify versions return a JSON-quoted real_value. Do not
        # interpret arbitrary JSON types or transform real key material.
        if actual.startswith('"') and actual.endswith('"'):
            try:
                decoded = json.loads(actual)
                if isinstance(decoded, str):
                    actual = decoded
            except ValueError:
                pass
        if name == 'SEH_SLH_PUBLIC_KEY_B64':
            try:
                pem = base64.b64decode(actual, validate=True)
                load_public_key(pem.decode('ascii'))
            except (ValueError, TypeError, UnicodeError):
                continue
        elif not _ID.fullmatch(actual):
            continue
        candidates.add(actual)

    if len(candidates) > 1:
        return {'state': 'AMBIGUOUS', 'value': None}
    if candidates:
        return {'state': 'VISIBLE', 'value': candidates.pop()}
    return {'state': 'MASKED' if masked else 'INVALID' if nonempty else 'EMPTY', 'value': None}


def diagnostic_slh_trust(root: Path, network: str, seh: str, *, slh_id: str | None = None, public_key_b64: str | None = None,
                      client=None) -> dict:
    """Read-only comparison of Mother, local signing identity, and Coolify.

    GET-only: never creates an identity, mutates environment, triggers a
    deployment, or reads any SQL. An opaque/masked Coolify key is UNKNOWN,
    never proof of a mismatch or proof of a match.
    """
    from .dns_probe import domain_hosts
    from main_computer.slh.authenticator import load_public_key

    record, controller, _, generation, _ = _selections(root, network, seh)
    slh = slh_id if slh_id is not None else record.get('slh_id')
    for field, value in (('slh_id', slh),):
        if value:
            st.ident(value, field)
    uuid = record['deployment'].get('application_uuid')
    if not isinstance(uuid, str) or not uuid:
        raise SEHGitError('SEH trust diagnostic requires an existing registered Coolify application')
    host = client if client is not None else _client(controller)
    application = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    marker = f'main-computer-seh:git-v3 target={seh} slh={slh}'
    if (not isinstance(application, dict) or application.get('uuid') != uuid or
            application.get('name') != f'mc-seh-{seh}' or application.get('description') != marker):
        raise SEHGitError('SEH Coolify application identity or ownership marker does not match')
    domains = domain_hosts(application)
    rows = _items(_api(host, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
    expected_key_digest = None
    if public_key_b64 is not None:
        try:
            public_pem = base64.b64decode(public_key_b64, validate=True)
            load_public_key(public_pem.decode('ascii'))
        except (ValueError, UnicodeDecodeError, TypeError):
            raise SEHGitError('Local SLH public key is not a valid P-256 PEM public key') from None
        expected_key_digest = hashlib.sha256(public_pem).hexdigest()

    fields = {}
    problem_fields = []
    for var, expected in (('SEH_SLH_ID', slh),):
        found = _diagnostic_env_value(rows, var)
        state = found['state']
        actual = found['value']
        if state == 'INVALID':
            status = 'INVALID_REMOTE_VALUE'
        elif state == 'VISIBLE' and expected is None:
            status = 'NO_LOCAL_BINDING'
        elif state == 'VISIBLE':
            status = 'MATCH' if actual == expected else 'MISMATCH'
        else:
            status = state
        fields[var] = {'status': status, 'expected': expected,
                       'observed': actual if status in ('MATCH', 'MISMATCH', 'NO_LOCAL_BINDING') else None}
        if status != 'MATCH':
            problem_fields.append(var)
    found = _diagnostic_env_value(rows, 'SEH_SLH_PUBLIC_KEY_B64')
    remote_digest = None
    if found['state'] == 'VISIBLE':
        try:
            remote_pem = base64.b64decode(found['value'], validate=True)
            load_public_key(remote_pem.decode('ascii'))
            remote_digest = hashlib.sha256(remote_pem).hexdigest()
            key_status = ('LOCAL_KEY_UNAVAILABLE' if expected_key_digest is None else
                          'MATCH' if expected_key_digest == remote_digest else 'MISMATCH')
        except (ValueError, UnicodeDecodeError, TypeError):
            key_status = 'INVALID_REMOTE_KEY'
    else:
        key_status = 'INVALID_REMOTE_KEY' if found['state'] == 'INVALID' else found['state']
    fields['SEH_SLH_PUBLIC_KEY_B64'] = {
        'status': key_status, 'local_public_key_sha256': expected_key_digest,
        'remote_public_key_sha256': remote_digest,
    }
    if key_status != 'MATCH':
        problem_fields.append('SEH_SLH_PUBLIC_KEY_B64')
    issues = [f'{name}: {fields[name]["status"]}' for name in problem_fields]
    return {
        'status': 'TRUST_MATCH' if not issues else 'TRUST_DIAGNOSTIC_FINDINGS',
        'schema': 'main-computer.seh-trust-diagnostic.v1',
        'network': network, 'seh': seh, 'application_uuid': uuid,
        'mother_generation': generation, 'public_url': 'https://' + domains[0] if len(domains) == 1 else None,
        'fields': fields, 'issues': issues,
        'mutation_performed': False, 'sql_used': False,
        'live_relay_verified': False,
        'interpretation': (
            'Coolify configured values match local expected values; deployed relay and WSS authentication remain unverified.'
            if not issues else
            'One or more trust fields are mismatched, missing, masked, or unverifiable. No change was made. '
            'A binding mismatch is not authorized by --replace-existing-trust.'
        ),
    }


def bind_slh(root: Path, network: str, seh: str, *, slh_id: str, public_key_b64: str, replace_existing: bool = False,
             execute: bool = False, client=None) -> dict:
    """Explicit SEH trust enrollment / key rotation, separate from deployment.

    Only the public P-256 key is transmitted. The target is the already owned,
    registered Coolify application; no SLH database or service is consulted.
    A change of an existing trust anchor requires a separate operator approval.
    """
    from main_computer.slh.authenticator import load_public_key

    st.ident(slh_id, 'slh_id')
    if not isinstance(public_key_b64, str) or len(public_key_b64) > 8192:
        raise SEHGitError('Invalid SLH public key encoding')
    try:
        public_pem = base64.b64decode(public_key_b64, validate=True)
        load_public_key(public_pem.decode('ascii'))
    except (ValueError, UnicodeDecodeError, TypeError):
        raise SEHGitError('SLH public key must be a valid P-256 PEM public key') from None

    record, controller, _, generation, _ = _selections(root, network, seh)
    prior_slh = record.get('slh_id')
    if prior_slh and prior_slh != slh_id:
        raise SEHGitError('Registered SEH SLH association differs; trust binding refused')
    uuid = record['deployment'].get('application_uuid')
    if not isinstance(uuid, str) or not uuid:
        raise SEHGitError('SEH must have a registered deployed Coolify application before binding')
    host = client if client is not None else _client(controller)
    application = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    marker = f'main-computer-seh:git-v3 target={seh} slh={prior_slh or slh_id}'
    # Only a *verified* Mother legacy record authorizes adoption of its former
    # marker. The old application UUID and SLH public key are preserved.
    schema = record['schema']
    legacy_marker = None
    if schema == 'main-computer.seh-identity.v1':
        legacy_marker = (f'main-computer-seh:git-v1 target={seh} '
                         f'org={record["organization_id"]} slh={record["slh_id"]}')
    elif schema == 'main-computer.seh-identity.v2':
        legacy_marker = f'main-computer-seh:git-v2 target={seh}'
    observed_marker = application.get('description') if isinstance(application, dict) else None
    if (not isinstance(application, dict) or application.get('uuid') != uuid or
            application.get('name') != f'mc-seh-{seh}' or
            observed_marker not in ({marker} if legacy_marker is None else {marker, legacy_marker})):
        raise SEHGitError('SEH Coolify application identity or ownership marker does not match')
    migrating_marker = observed_marker != marker
    from .dns_probe import domain_hosts
    hosts = domain_hosts(application)
    public_url = 'https://' + hosts[0] if len(hosts) == 1 else None

    keys = ('SEH_SLH_ID', 'SEH_SLH_PUBLIC_KEY_B64')
    def read_trust():
        rows = _items(_api(host, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
        found = {}
        for row in rows:
            if row.get('is_preview') is True or row.get('key') not in keys:
                continue
            key = row['key']
            if key in found:
                raise SEHGitError('Duplicate SEH trust environment variable; refusing modification')
            observed = _diagnostic_env_value(rows, key)
            if observed['state'] != 'VISIBLE':
                raise SEHGitError(f'Coolify {key} is {observed["state"]}; refusing unverified trust replacement')
            found[key] = observed['value']
        if found and set(found) != set(keys):
            raise SEHGitError('Incomplete SEH trust binding; refusing automatic repair')
        return found

    previous = read_trust()
    legacy_env = [r for r in _items(_api(host, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
                  if r.get('key') == 'SEH_ORGANIZATION_ID' and r.get('is_preview') is not True]
    if len(legacy_env) > 1:
        raise SEHGitError('Ambiguous legacy identity environment; refusing migration')
    if legacy_env and (not isinstance(legacy_env[0].get('uuid'), str) or
                       not _ID.fullmatch(legacy_env[0]['uuid'])) and execute:
        raise SEHGitError('Legacy identity environment UUID unavailable; refusing deletion')
    if previous:
        if previous['SEH_SLH_ID'] != slh_id:
            raise SEHGitError('Remote SEH SLH identity differs; refusing key replacement')
    old_key = previous.get('SEH_SLH_PUBLIC_KEY_B64')
    changed = old_key != public_key_b64
    replace_required = old_key is not None and changed
    if execute and replace_required and not replace_existing:
        raise SEHGitError('Existing SLH trust key differs; --replace-existing-trust is required')
    result = {'seh': seh, 'network': network, 'application_uuid': uuid,
              'slh_id': slh_id,
              'public_key_sha256': hashlib.sha256(public_pem).hexdigest(),
              'existing_binding': bool(previous), 'key_change': changed,
              'replacement_requires_ack': replace_required,
              'public_url': public_url, 'ready': False, 'public_wss_verified': False,
              'remote_mutation': False, 'mother_generation_observed': generation}
    if not execute:
        return {**result, 'status': 'PLAN_ONLY',
                'reason': 'Both mutation acknowledgments required to bind SLH trust'}

    if migrating_marker and not previous:
        raise SEHGitError('Legacy SEH has no verified trust binding; refusing automatic marker migration')
    if changed or not previous:
        if read_trust() != previous:
            raise SEHGitError('SEH trust binding changed during planning; refusing mutation')
        updates = ({'SEH_SLH_PUBLIC_KEY_B64': public_key_b64} if previous else
                   dict(zip(keys, (slh_id, public_key_b64))))
        payload = {'data': [
            {'key': key, 'value': value, 'is_literal': True,
             'is_preview': False, 'is_runtime': True, 'is_buildtime': False,
             'is_multiline': False}
            for key, value in updates.items()
        ]}
        _api(host, 'PATCH', f'/api/v1/applications/{uuid}/envs/bulk', payload)
        result['remote_mutation'] = True
    try:
        current = read_trust()
    except SEHGitError:
        raise SEHGitError('SLH trust update did not verify on Coolify readback; deployment not triggered') from None
    if current != dict(zip(keys, (slh_id, public_key_b64))):
        raise SEHGitError('SLH trust update did not verify on Coolify readback; deployment not triggered')
    # Remove the obsolete remote identity variable by its verified Coolify
    # env UUID. This is an authorized, targeted DELETE, never an env-bulk
    # overwrite that could retain the old variable indefinitely.
    if legacy_env:
        env_uuid = legacy_env[0]['uuid']
        again = [r for r in _items(_api(host, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
                 if r.get('key') == 'SEH_ORGANIZATION_ID' and r.get('is_preview') is not True]
        if len(again) != 1 or again[0].get('uuid') != env_uuid:
            raise SEHGitError('Legacy identity environment changed before deletion')
        _api(host, 'DELETE', f'/api/v1/applications/{uuid}/envs/{env_uuid}')
        result['remote_mutation'] = True
        after = _items(_api(host, 'GET', f'/api/v1/applications/{uuid}/envs'), 'envs')
        if any(r.get('key') == 'SEH_ORGANIZATION_ID' and r.get('is_preview') is not True for r in after):
            raise SEHGitError('Legacy identity environment removal not verified')
    if migrating_marker:
        before = _api(host, 'GET', f'/api/v1/applications/{uuid}')
        if not isinstance(before, dict) or before.get('description') != observed_marker:
            raise SEHGitError('SEH ownership marker changed during migration')
        _api(host, 'PATCH', f'/api/v1/applications/{uuid}', {'description': marker})
        result['remote_mutation'] = True
        after = _api(host, 'GET', f'/api/v1/applications/{uuid}')
        if not isinstance(after, dict) or after.get('description') != marker:
            raise SEHGitError('SEH ownership marker migration readback failed')
    # Identity must remain on the same verified Mother generation while we
    # operate; do not silently deploy to a changed controller or application.
    later, _, _, later_generation, _ = _selections(root, network, seh)
    if later_generation != generation or later['deployment'].get('application_uuid') != uuid:
        raise SEHGitError('Mother infrastructure identity changed during trust update; deployment not triggered')
    if schema != 'main-computer.seh-identity.v3':
        def commit_no_org(doc, gen, base):
            item = st.seh_doc(doc, network, seh)
            if item['deployment'].get('application_uuid') != uuid:
                raise st.SEHIdentityError('SEH application changed during legacy identity migration')
            item['schema'] = 'main-computer.seh-identity.v3'
            item['slh_id'] = slh_id
            item.pop('organization_id', None)
            st.validate_seh(item)
        st.mutate(root, network, generation, f'seh-trust-migrate-{seh}', commit_no_org)
        result['mother_identity_migrated'] = True
    try:
        hub_coolify.trigger_deploy(host, application_uuid=uuid, force=True, tried=[])
    except Exception as exc:
        raise SEHGitError(f'SEH trust updated but redeploy trigger failed: {type(exc).__name__}') from None
    return {**result, 'status': 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED',
            'remote_mutation': True, 'trust_readback_verified': True}


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
            f'main-computer-seh:git-v3 target={seh} slh={record.get("slh_id")}'):
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
