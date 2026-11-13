"""Git-backed SEH deployment and guarded canonical Coolify metadata reconciliation.

Mother private state provides the verified controller identity only.
SEH-owned durable state controls application UUID and deployment lifecycle.
"""
from __future__ import annotations

import ast
import base64
from contextlib import nullcontext as _nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, quote, urlencode

from tools import coolify_hub_service as hub_coolify
from tools.hub_control.common.deployment import _render_git_commit_push_command
from . import identity as st
from . import deployment_state as ds

DEFAULT_PUBLIC_URL = 'https://external.greatlibrary.io'
DOCKERFILE = '/deploy/seh/Dockerfile'
SOURCES = ('.dockerignore', 'deploy/seh/Dockerfile',
           'main_computer/slh/authenticator.py', 'main_computer/seh/__init__.py',
           'main_computer/seh/relay.py',
           'main_computer/seh/stream_writer.py')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_SHA = re.compile(r'[0-9a-f]{40}\Z')


# Like Hub Control's _hub_git_source_paths, derive the deployed Python import
# closure. The static list above is only the minimum image/build surface.
SEH_ENTRY_MODULES = ('main_computer.seh.relay', 'main_computer.slh.authenticator')


def _seh_source_closure(paths: set[str], read_source, *, strict: bool = True) -> set[str]:
    """Discover transitive local imports from the SEH entry modules.

    read_source accepts a repository-relative path and returns its contents.
    It can read either the worktree or immutable blobs from one Git commit.
    """
    modules: dict[str, str] = {}
    for rel in paths:
        if not rel.startswith('main_computer/') or not rel.endswith('.py'):
            continue
        name = rel[:-3].replace('/', '.')
        if name.endswith('.__init__'):
            name = name[:-9]
        modules[name] = rel
    result: set[str] = set(SOURCES)
    queue = list(SEH_ENTRY_MODULES)
    visited: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        rel = modules.get(name)
        if rel is None:
            # Required project modules cannot silently disappear from an image.
            raise SEHGitPublicationError('SEH Git source missing imported module: ' + name)
        result.add(rel)
        parts = rel.split('/')[:-1]
        for count in range(1, len(parts) + 1):
            initializer = '/'.join(parts[:count]) + '/__init__.py'
            if initializer in paths:
                result.add(initializer)
        try:
            tree = ast.parse(read_source(rel), filename=rel)
        except (SyntaxError, UnicodeError, ValueError, OSError) as exc:
            if not strict:
                # Hub also allows syntax-broken local changes to be detected
                # by Git status, so the operator can publish a repair.
                continue
            raise SEHGitPublicationError('SEH Git module cannot be inspected: ' + rel +
                                         ' (' + type(exc).__name__ + ')') from None
        package = name.rsplit('.', 1)[0]
        for node in ast.walk(tree):
            candidates = []
            if isinstance(node, ast.Import):
                candidates.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split('.')
                    ascend = int(node.level) - 1
                    if ascend:
                        parts = parts[:-ascend]
                    base = '.'.join(parts + ([node.module] if node.module else []))
                else:
                    base = node.module or ''
                candidates.append(base)
                candidates.extend(base + '.' + item.name for item in node.names
                                  if item.name != '*' and base)
            for candidate in candidates:
                if candidate == 'main_computer' or candidate.startswith('main_computer.'):
                    if candidate in modules and candidate not in visited:
                        queue.append(candidate)
                    elif candidate.startswith('main_computer.seh.') and candidate not in modules:
                        # A missing SEH submodule cannot be handled by a
                        # source-scope exemption: the runtime import may crash.
                        if isinstance(node, ast.Import) or candidate == (base if isinstance(node, ast.ImportFrom) else ''):
                            raise SEHGitPublicationError('SEH Git source missing imported module: ' + candidate)
    return result


def _seh_git_source_paths(repo_root: Path) -> tuple[str, ...]:
    """Hub-style import closure for the local SEH build worktree."""
    root = Path(repo_root)
    paths = {p.relative_to(root).as_posix() for p in (root / 'main_computer').rglob('*.py')}
    return tuple(sorted(_seh_source_closure(paths, lambda rel: (root / rel).read_text(encoding='utf-8'), strict=False)))


def _published_seh_image_check(git, revision: str) -> tuple[str, ...]:
    """Inspect EXACT published Git blobs, not worktree files or SHA recency.

    Coolify may have an independent wget healthcheck.  Every runtime import
    must be copied into the minimal SEH image at its Python package path.
    """
    if not _SHA.fullmatch(revision):
        raise SEHGitPublicationError('Invalid published SEH image revision')
    try:
        tracked = set(git('ls-tree', '-r', '--name-only', revision,
                          '--', 'main_computer').splitlines())
        dockerfile = git('show', revision + ':deploy/seh/Dockerfile')
        dockerignore = git('show', revision + ':.dockerignore')
    except SEHGitPublicationError:
        raise SEHGitPublicationError('Published SEH image files not available in the verified commit') from None
    required = _seh_source_closure(tracked, lambda rel: git('show', revision + ':' + rel))
    # The build context is a single, repository-root Docker context. Refuse
    # ambiguous COPY forms rather than guess about which files reach the image.
    copies: dict[str, str] = {}
    for instruction in dockerfile.replace('\\\n', ' ').splitlines():
        stripped = instruction.strip()
        if not stripped.startswith('COPY '):
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError:
            raise SEHGitPublicationError('Unverifiable SEH Dockerfile COPY instruction') from None
        if len(fields) != 3 or fields[0] != 'COPY':
            raise SEHGitPublicationError('Unverifiable SEH Dockerfile COPY instruction')
        copies[fields[1]] = fields[2]
    # The Dockerfile generates these two package initializers itself.
    generated = {'main_computer/__init__.py', 'main_computer/slh/__init__.py'}
    for path in sorted(required - {'.dockerignore', 'deploy/seh/Dockerfile'} - generated):
        if path not in tracked:
            raise SEHGitPublicationError('Published SEH image source is missing: ' + path)
        if copies.get(path) != path:
            raise SEHGitPublicationError('Published SEH Dockerfile does not COPY required module: ' + path)
    # A package initializer must exist, whether copied or explicitly generated.
    for path in generated:
        if path not in dockerfile:
            raise SEHGitPublicationError('Published SEH Dockerfile does not generate package: ' + path)
    normalized = dockerfile.replace('\\\n', ' ')
    installs = [line for line in normalized.splitlines()
                if 'apt-get install' in line]
    if not any('wget' in line.split() for line in installs):
        raise SEHGitPublicationError('Published SEH Dockerfile does not install Coolify healthcheck wget')
    if ('PYTHONPATH=/opt/seh' not in dockerfile or
            '"main_computer.seh.relay"' not in dockerfile or
            '/healthz' not in dockerfile):
        raise SEHGitPublicationError('Published SEH Dockerfile lacks relay runtime/health contract')
    # Prevent an explicit Docker ignore rule from discarding a verified COPY
    # source; complex patterns are rejected rather than treated as proof.
    ignore_rules = [line.strip() for line in dockerignore.splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    for path in required - {'.dockerignore', 'deploy/seh/Dockerfile'}:
        if path in ignore_rules or any(rule.rstrip('/') == path.rsplit('/', 1)[0]
                                       for rule in ignore_rules if not rule.startswith('!')):
            raise SEHGitPublicationError('Published Docker ignore excludes SEH source: ' + path)
    return tuple(sorted(required))


class SEHGitError(ValueError):
    """Verified SEH Git/source refusal (no automatic publication)."""

    def __init__(self, message: str, *, git_command: str = ''):
        super().__init__(message)
        self.git_command = git_command


class SEHRemoteUnreachable(SEHGitError):
    """Only Coolify transport unavailability, never a remote trust refusal."""


class SEHGitPublicationError(SEHGitError):
    """Refusal tied specifically to the local/published Git-source boundary."""



def _json_digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _api(client, method: str, path: str, body: dict | None = None):
    """Adapt Hub Control's Coolify response to a strict, redacted SEH interface."""
    try:
        answer = client.request(method, path, body)
        if hasattr(answer, 'ok'):
            if not answer.ok:
                if answer.status == 0:
                    raise SEHRemoteUnreachable('Coolify transport unavailable')
                raise SEHGitError(f'Coolify {method} {path.split("?")[0]} returned HTTP {answer.status}')
            return answer.body
        return answer
    except SEHGitError:
        raise
    except Exception as exc:
        # Never stringify a connector exception; it might include a bearer token.
        raise SEHGitError(f'Coolify {method} failed: {type(exc).__name__}') from None


def _registered_application(client, application_uuid: str) -> dict | None:
    """Only an explicit Coolify HTTP 404 means the UUID was deleted.

    Unauthorized, unreachable, malformed, or conflicting responses are NOT
    permission to create a replacement application.
    """
    path = f'/api/v1/applications/{application_uuid}'
    try:
        response = client.request('GET', path)
    except Exception as exc:
        raise SEHGitError('Coolify application lookup failed: ' + type(exc).__name__) from None
    if hasattr(response, 'ok'):
        if not response.ok:
            if response.status == 404:
                return None
            if response.status == 0:
                raise SEHRemoteUnreachable('Coolify application lookup unavailable')
            raise SEHGitError('Coolify application lookup returned HTTP ' + str(response.status))
        response = response.body
    if not isinstance(response, dict) or response.get('uuid') != application_uuid:
        raise SEHGitError('Coolify application lookup returned a conflicting or malformed UUID')
    return response


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
    record = ds.selected(root, network, st.seh_doc(doc, network, seh))
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
    identity_record = st.deployment_record(document, network)
    if identity_record is None:
        raise SEHGitError('No verified SEH controller identity; migrate identity first')
    record = ds.selected(root, network, identity_record)
    uuid = record['deployment'].get('application_uuid')
    if not isinstance(uuid, str) or not _ID.fullmatch(uuid):
        raise SEHGitError('Canonical deployment has no valid Coolify application UUID')
    ctl = record['coolify']['controller']
    if ctl.get('enabled') is False:
        raise SEHGitError('Canonical Coolify controller is disabled')
    return record, ctl, verified.binding.generation, verified.binding.content_hash.digest


# A verified undeploy keeps the former UUID as an audit tombstone.  It is
# deliberately NOT the same as an unexpected Coolify 404: only a persisted
# removal receipt can certify that this application was intentionally removed.
_UNDEPLOY_RECEIPT = 'undeploy-receipt-v1-'  # UUID-scoped history survives later deployments
_UNDEPLOY_DELETE_PATH_SUFFIX = ('?delete_volumes=false&docker_cleanup=false'
                                '&delete_connected_networks=false')


def _undeploy_receipt(root: Path, network: str, uuid: str) -> dict | None:
    if not isinstance(uuid, str) or not _ID.fullmatch(uuid):
        raise SEHGitError('Invalid undeploy receipt UUID')
    path = ds.directory(root, network) / (_UNDEPLOY_RECEIPT + uuid + '.json')
    if path.is_symlink():
        raise SEHGitError('SEH undeploy receipt cannot be a symlink')
    if not path.exists():
        return None
    try:
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise SEHGitError('SEH undeploy receipt has unsafe permissions')
        receipt = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SEHGitError('SEH undeploy receipt is unreadable: ' + type(exc).__name__) from None
    if (type(receipt) is not dict or
            set(receipt) != {'schema', 'application_uuid', 'identity_binding', 'phase', 'requested_utc'} or
            receipt['schema'] != 'main-computer.seh-undeploy.v1' or
            type(receipt['application_uuid']) is not str or
            not _ID.fullmatch(receipt['application_uuid']) or
            type(receipt['identity_binding']) is not str or
            not re.fullmatch(r'[0-9a-f]{64}', receipt['identity_binding']) or
            receipt['phase'] not in ('INTENT', 'VERIFIED') or
            type(receipt['requested_utc']) is not str):
        raise SEHGitError('SEH undeploy receipt is invalid')
    return receipt


def _save_undeploy_receipt(root: Path, network: str, receipt: dict) -> None:
    folder = ds.directory(root, network)
    from main_computer.slh.storage import secure_machine_state_dir, secure_private_state_file
    secure_machine_state_dir(folder)
    destination = folder / (_UNDEPLOY_RECEIPT + receipt['application_uuid'] + '.json')
    if destination.is_symlink():
        raise SEHGitError('SEH undeploy receipt cannot be a symlink')
    descriptor, scratch = tempfile.mkstemp(prefix='.seh-undeploy-', dir=folder)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as out:
            json.dump(receipt, out, sort_keys=True, separators=(',', ':'))
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        secure_private_state_file(Path(scratch))
        os.replace(scratch, destination)
        if os.name != 'nt':
            handle = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def undeploy_canonical_runtime(root: Path, network: str, *, execute: bool = False,
                               client=None, state_dir: Path | None = None,
                               probe=None) -> dict:
    """Delete only the verified canonical SEH application via Coolify.

    No remote DELETE follows an unresolved intent.  Coolify may lose the
    acknowledgment after accepting DELETE, so subsequent calls reconcile by
    GET and NEVER issue a speculative second DELETE.  This harness does not
    itself drain work; executing requires the resident SLH's outbound relay
    to have been disabled and the public SEH to confirm no SLH connection.
    """
    from main_computer.slh.organization_state import read_config
    from main_computer.slh.storage import slh_state_dir
    from tools.slh_seh_connector import service_identity
    from .dns_probe import domain_hosts

    state_dir = Path(state_dir) if state_dir is not None else slh_state_dir()
    lock = ds.mutation_lock(root, network) if execute else _nullcontext()
    with lock:
        record, controller, generation, _ = _canonical_selection(root, network)
        uuid = record['deployment']['application_uuid']
        binding = ds.identity_binding(record)
        remote = client if client is not None else _client(controller)
        receipt = _undeploy_receipt(root, network, uuid)
        if receipt and (receipt['application_uuid'] != uuid or
                        receipt['identity_binding'] != binding):
            raise SEHGitError('Undeploy receipt belongs to a different SEH identity or UUID')
        tombstoned = record['deployment'].get('undeployed') is True
        result = {'network': network, 'application_uuid': uuid,
                  'mother_generation': generation, 'mother_mutation': 'UNCHANGED',
                  'remote_mutation': False}
        if tombstoned and (receipt is None or receipt['phase'] != 'VERIFIED'):
            raise SEHGitError('Undeployed SEH has no verified removal receipt')

        app = _registered_application(remote, uuid)
        if app is None:
            if not receipt:
                raise SEHGitError('Coolify application missing without intentional undeploy receipt; use recovery inspection')
            if not execute:
                return {**result, 'status': 'PLAN_ONLY',
                        'action': 'VERIFY_PRIOR_UNDEPLOY', 'prior_phase': receipt['phase']}
            if receipt['phase'] != 'VERIFIED':
                # The intent was durable before deletion; a confirmed 404
                # completes the operation even when DELETE acknowledgment was lost.
                _finish_canonical_undeploy(root, network, record, uuid, receipt)
            return {**result, 'status': 'ALREADY_UNDEPLOYED',
                    'application_removed_verified': True}
        if tombstoned:
            raise SEHGitError('Application reappeared after verified undeployment; refusing unrelated deletion')
        if receipt:
            # A prior DELETE may have succeeded but the Coolify API is not yet
            # consistent. No second DELETE is safe, even if GET returns the app.
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                    'failed_stage': 'prior_undeploy_still_visible'}

        place = _canonical_placement(remote, record)
        _verify_application_placement(remote, app, place)
        _verify_canonical_source(app)
        if _verify_canonical_metadata(app) != 'CANONICAL':
            raise SEHGitError('Noncanonical SEH metadata; reconcile before undeploy')
        expected_url = record['deployment'].get('public_url') or DEFAULT_PUBLIC_URL
        parsed = urlsplit(expected_url)
        hosts = domain_hosts(app)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path not in ('', '/') or parsed.query or
                parsed.fragment or len(hosts) != 1 or
                hosts[0].lower() != parsed.hostname.lower()):
            raise SEHGitError('SEH public domain does not match owned deployment')
        _, public_key = service_identity(state_dir, create=False)
        trust = _trust_snapshot(remote, uuid)
        if any(row['key'] == 'SEH_ORGANIZATION_ID' for row in trust):
            raise SEHGitError('Obsolete organization trust field prevents undeploy')
        key = _diagnostic_env_value(trust, 'SEH_SLH_PUBLIC_KEY_B64')
        if key != {'state': 'VISIBLE', 'value': public_key}:
            raise SEHGitError('SEH signing-key ownership cannot be verified')

        cfg = read_config(state_dir)
        # A policy bit alone is insufficient: the actual public relay must
        # attest that the SLH tunnel is absent.  A failed probe is not proof
        # of drained Worker/requester traffic.
        observer = probe or _public_runtime_probe
        observed = observer(expected_url)
        disconnected = (cfg['seh_enabled'] is False and
                        type(observed) is dict and
                        observed.get('status') == 'PUBLIC_RUNTIME_PRESENT' and
                        observed.get('slh_connected') is False)
        # Refuse concurrent, unfinished build/recovery operations.
        pending_build = _redeploy_checkpoint(root, network)
        if pending_build and pending_build['phase'] != 'VERIFIED':
            raise SEHGitError('Pending redeploy must finish before undeploy')
        recovery = _recovery_checkpoint(state_dir)
        recovery_state = _recovery_undeploy_evidence(recovery, uuid, remote, app,
                                                       public_key) if recovery else None
        if not execute:
            return {**result, 'status': 'PLAN_ONLY',
                    'action': 'DELETE_CANONICAL_SEH_APPLICATION',
                    'outbound_disconnected_verified': disconnected,
                    'ready_for_undeploy': disconnected,
                    'recovery_receipt': recovery_state or 'NONE'}
        if not disconnected:
            raise SEHGitError('SEH outbound tunnel must be disabled and disconnected before undeploy')
        current, _, _, _ = _canonical_selection(root, network)
        if _operational_binding(current) != _operational_binding(record):
            raise SEHGitError('SEH deployment identity changed during undeploy preflight')
        # Durable intent precedes Coolify DELETE. No retry after uncertainty.
        receipt = {'schema': 'main-computer.seh-undeploy.v1',
                   'application_uuid': uuid, 'identity_binding': binding,
                   'phase': 'INTENT',
                   'requested_utc': datetime.now(timezone.utc).isoformat()}
        _save_undeploy_receipt(root, network, receipt)
        try:
            _api(remote, 'DELETE', f'/api/v1/applications/{uuid}' +
                 _UNDEPLOY_DELETE_PATH_SUFFIX)
        except SEHGitError:
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                    'failed_stage': 'coolify_delete_unacknowledged'}
        try:
            remaining = _registered_application(remote, uuid)
        except SEHGitError:
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': True,
                    'failed_stage': 'coolify_delete_readback_unavailable'}
        if remaining is not None:
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': True,
                    'failed_stage': 'coolify_delete_not_yet_verified'}
        try:
            _finish_canonical_undeploy(root, network, record, uuid, receipt)
        except (SEHGitError, ds.SEHDeploymentStateError, OSError, ValueError):
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': True,
                    'failed_stage': 'undeploy_state_commit'}
        return {**result, 'status': 'SEH_UNDEPLOYED', 'remote_mutation': True,
                'application_removed_verified': True}


def _finish_canonical_undeploy(root: Path, network: str, record: dict,
                               uuid: str, receipt: dict) -> None:
    """Commit a tombstone without erasing Mother's controller or the former UUID."""
    _, _, doc = st.read(root, network)
    identity = st.deployment_record(doc, network)
    if identity is None or ds.identity_binding(identity) != ds.identity_binding(record):
        raise SEHGitError('Coolify identity changed before undeploy state commit')
    current = ds.selected(root, network, identity)
    if current['deployment']['application_uuid'] != uuid:
        raise SEHGitError('SEH application UUID changed before undeploy state commit')
    if current['deployment'].get('undeployed') is not True:
        updated = deepcopy(current['deployment'])
        updated['enabled'] = False
        updated['undeployed'] = True
        updated['undeployed_at'] = receipt['requested_utc']
        ds.write(root, network, identity, expected_uuid=uuid, deployment=updated)
    _save_undeploy_receipt(root, network, {**receipt, 'phase': 'VERIFIED'})


def _operational_binding(record: dict) -> tuple[str, str]:
    return ds.identity_binding(record), _json_digest(record['deployment'])


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
    """Reconcile generic metadata and remove a verified obsolete organization env.

    Operates only on the UUID in SEH-owned operational state. Existing signing-key trust,
    route, public domain, Git source, deployment state, and private identity
    are never replaced. No deployment trigger or Mother mutation occurs.
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
    legacy = next((row for row in trust_before if row['key'] == 'SEH_ORGANIZATION_ID'), None)
    # Do not reveal a legacy value, even in dry-run receipts. This is a
    # configuration remnant, never an authority to select a different SEH.
    legacy_state = 'PRESENT' if legacy else 'ABSENT'
    detail = {'network': network, 'application_uuid': uuid, 'mother_generation': generation,
              'current_metadata': kind, 'remote_mutation': False, 'trust_untouched': True,
              'management_transport': 'OPERATOR_ACCEPTED_HTTP' if origin.startswith('http://') else 'HTTPS',
              'new_name': CANONICAL_APPLICATION_NAME,
              'new_description': CANONICAL_DESCRIPTION,
              'legacy_organization_env': legacy_state,
              'signing_key_untouched': True}
    if not execute:
        return {'status': 'PLAN_ONLY', **detail}
    if kind == 'CANONICAL' and legacy is None:
        return {'status': 'UNCHANGED', **detail}

    # A remote identity variable may be deleted only if the SEH already
    # trusts the same public signing key preserved by the local SLH. A missing,
    # masked, or changed key is not evidence of authorization to clean trust.
    if legacy is not None:
        env_uuid = legacy.get('uuid')
        if not isinstance(env_uuid, str) or not _ID.fullmatch(env_uuid):
            raise SEHGitError('Obsolete Coolify organization env has no verifiable UUID; refusing deletion')
        from main_computer.slh.storage import slh_state_dir
        from tools.slh_seh_connector import service_identity
        try:
            _, local_public_key = service_identity(slh_state_dir(), create=False)
        except (OSError, RuntimeError, ValueError):
            raise SEHGitError('Existing local SLH signing identity cannot be verified; no remote change made') from None
        remote_key = _diagnostic_env_value(trust_before, 'SEH_SLH_PUBLIC_KEY_B64')
        if remote_key['state'] != 'VISIBLE' or remote_key['value'] != local_public_key:
            raise SEHGitError('Existing SEH signing trust is unverifiable or differs from local SLH; '
                              'refusing legacy environment deletion')

    # Recheck verified controller identity, SEH lifecycle and remote object before
    # the first write. This never creates a Coolify application or key.
    later, _, _, _ = _canonical_selection(root, network)
    if _operational_binding(later) != _operational_binding(record):
        raise SEHGitError('SEH identity or deployment changed during metadata reconciliation')
    current = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if current != app or _trust_snapshot(host, uuid) != trust_before:
        raise SEHGitError('Coolify application or trust changed during metadata reconciliation')

    if kind != 'CANONICAL':
        _api(host, 'PATCH', f'/api/v1/applications/{uuid}',
             {'name': CANONICAL_APPLICATION_NAME, 'description': CANONICAL_DESCRIPTION})
    updated = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if not isinstance(updated, dict) or updated.get('uuid') != uuid or \
            _verify_canonical_metadata(updated) != 'CANONICAL':
        raise SEHGitError('Coolify metadata readback failed; do not retry blindly')
    _verify_application_placement(host, updated, place)
    _verify_canonical_source(updated)
    protected_fields = ('domains', 'git_repository', 'repository', 'git_branch',
                        'git_commit_sha', 'dockerfile_location', 'build_pack',
                        'ports_exposes', 'project_uuid', 'environment_uuid',
                        'environment_id', 'server_uuid')
    if any(updated.get(key) != app.get(key) for key in protected_fields):
        raise SEHGitError('Coolify changed protected application configuration; reconciliation incomplete')
    if _trust_snapshot(host, uuid) != trust_before:
        raise SEHGitError('SEH signing trust unexpectedly changed; operation is incomplete')

    if legacy is not None:
        # Delete the one verified obsolete env row; do not perform a bulk
        # overwrite or touch SEH_SLH_PUBLIC_KEY_B64.
        current_record, _, _, _ = _canonical_selection(root, network)
        if _operational_binding(current_record) != _operational_binding(record):
            raise SEHGitError('SEH identity or deployment changed before env deletion')
        if _trust_snapshot(host, uuid) != trust_before:
            raise SEHGitError('Remote SEH trust changed before obsolete env deletion')
        _api(host, 'DELETE', f'/api/v1/applications/{uuid}/envs/{env_uuid}')
        expected = [row for row in trust_before if row['key'] != 'SEH_ORGANIZATION_ID']
        if _trust_snapshot(host, uuid) != expected:
            raise SEHGitError('Obsolete SEH organization env deletion did not verify; '
                              'inspect remote state before retry')
        detail['legacy_organization_env'] = 'REMOVED'

    final = _api(host, 'GET', f'/api/v1/applications/{uuid}')
    if not isinstance(final, dict) or final.get('uuid') != uuid or \
            any(final.get(key) != app.get(key) for key in protected_fields):
        raise SEHGitError('Coolify application configuration changed during cleanup; inspect before retry')
    last_record, _, _, _ = _canonical_selection(root, network)
    if _operational_binding(last_record) != _operational_binding(record):
        raise SEHGitError('SEH identity or deployment changed during metadata update')
    status = 'METADATA_RECONCILED' if kind != 'CANONICAL' else 'LEGACY_ENV_REMOVED'
    return {'status': status, **{**detail, 'remote_mutation': True}}


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



def _seh_git_operator_command(mismatches: list[str]) -> str:
    """Reuse Stable Hub's exact path-scoped Git command renderer.

    SEH supplies only the differing deployment paths. The Git operation and
    PowerShell syntax remain owned by Hub Control; SEH never runs them.
    """
    return _render_git_commit_push_command(
        [{'status': ' M', 'path': path} for path in sorted(set(mismatches))]
    )


def git_source(repo_root: Path, *, branch: str = 'main') -> dict:
    """Validate the *published* SEH image against the current build inputs.

    Like Hub Control, scope the source guard to the deployed Python import
    closure and build inputs. Unlike the former SEH guard, never infer a Git
    publication requirement from local HEAD ancestry or Git porcelain alone:
    an operator can publish an isolated source commit without moving local
    HEAD, or leave a local file 'dirty' whose bytes are already published.

    This is read-only. Publication is the operator's responsibility. If
    relevant source differs, reuse the Stable Hub's path-scoped publication
    command only when HEAD is the published tip. Otherwise report the same
    unpushed-HEAD condition as Stable Hub without inventing another Git
    repair workflow. The guard never modifies Git or Coolify.
    """
    repo_root = Path(repo_root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9._/-]+', branch) or '..' in branch:
        raise SEHGitPublicationError('Unsafe Git branch')

    def git(*args):
        try:
            proc = subprocess.run(['git', '-C', str(repo_root), *args],
                                  capture_output=True, encoding='utf-8', errors='replace',
                                  timeout=25, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SEHGitPublicationError('Git source verification unavailable: ' +
                                         type(exc).__name__) from None
        if proc.returncode != 0:
            raise SEHGitPublicationError('Git source verification failed for ' + str(args[0]))
        return proc.stdout.rstrip('\r\n')

    origin = git('remote', 'get-url', 'origin')
    normalized = origin.removesuffix('.git').replace('git@github.com:', 'https://github.com/')
    if normalized.rstrip('/') != 'https://github.com/johnrraymond/main_computer':
        raise SEHGitPublicationError('Git origin is not the expected Main Computer repository')
    remote_row = git('ls-remote', '--exit-code', 'origin', f'refs/heads/{branch}').split()
    if (len(remote_row) != 2 or remote_row[1] != f'refs/heads/{branch}' or
            not _SHA.fullmatch(remote_row[0])):
        raise SEHGitPublicationError('Published SEH branch cannot be verified')
    remote = remote_row[0]
    # ls-remote returns a SHA, not the corresponding tree. The exact remote
    # commit must already be fetched; never silently substitute local HEAD.
    try:
        git('cat-file', '-e', f'{remote}^{{commit}}')
    except SEHGitPublicationError:
        raise SEHGitPublicationError(
            f'Published origin/{branch} commit is not available locally; '
            'Git state must be reconciled before retrying SEH activation') from None

    local_paths = set(_seh_git_source_paths(repo_root))
    tracked_remote = set(git('ls-tree', '-r', '--name-only', remote,
                             '--', 'main_computer').splitlines())
    remote_paths = _seh_source_closure(
        tracked_remote, lambda rel: git('show', f'{remote}:{rel}'))
    required = sorted(local_paths | remote_paths | set(SOURCES))
    mismatches = []
    mismatch_paths = []
    missing_local_paths = []
    for rel in required:
        live = repo_root / rel
        if not live.is_file():
            mismatches.append(rel + ' (missing locally)')
            mismatch_paths.append(rel)
            missing_local_paths.append(rel)
            continue
        try:
            published_blob = git('rev-parse', '--verify', f'{remote}:{rel}')
        except SEHGitPublicationError:
            mismatches.append(rel + ' (missing from published commit)')
            mismatch_paths.append(rel)
            continue
        # --path applies Git's normal clean filters (including .gitattributes),
        # so this is the blob that would actually be committed from the file.
        actual_blob = git('hash-object', f'--path={rel}', '--', rel)
        if not _SHA.fullmatch(published_blob) or actual_blob != published_blob:
            mismatches.append(rel)
            mismatch_paths.append(rel)
    if mismatches:
        preview = ', '.join(mismatches[:12])
        if len(mismatches) > 12:
            preview += f', ... (+{len(mismatches) - 12} more)'
        if missing_local_paths:
            # Never suggest committing a deletion of a required runtime file.
            raise SEHGitPublicationError(
                f'SEH build inputs missing locally: {", ".join(missing_local_paths[:12])}. '
                'Restore the required SEH source before publishing')
        head = git('rev-parse', 'HEAD')
        if head != remote:
            # Match Stable Hub redeploy.git_revision's unpushed-HEAD handling.
            # In particular, do not invent a fetch/diff/rebase or isolated
            # worktree script, and do not advertise a scoped push that cannot
            # fast-forward the already different published branch.
            raise SEHGitPublicationError(
                f'SEH build inputs differ from published origin/{branch} '
                f'({remote[:12]}): {preview}. '
                'Local HEAD is not the published revision. '
                'If HEAD is the intended revision, run: git push origin HEAD '
                '(resolve any remote divergence before retrying)')
        command = _seh_git_operator_command(mismatch_paths)
        raise SEHGitPublicationError(
            f'SEH build inputs differ from published origin/{branch} '
            f'({remote[:12]}): {preview}. SEH activation will not change Git; '
            'copy/paste the emitted Stable Hub Git commit/push command, '
            'then rerun activation',
            git_command=command)

    # Verify the exact image Coolify will build; neither a newer SHA nor a
    # matching worktree can compensate for missing runtime COPY or health deps.
    _published_seh_image_check(git, remote)
    return {'repository': 'https://github.com/johnrraymond/main_computer',
            'branch': branch, 'revision': remote,
            'dockerfile_location': DOCKERFILE}


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
        result['SEH_SLH_PUBLIC_KEY_B64'] = plan['slh_public_key_b64']
    return result


def preflight(root: Path, network: str, seh: str, *, repo_root: Path | None = None,
              client=None, source=None) -> dict:
    plan = _loaded(root,network,seh)
    record, controller, _, generation, state_hash = _selections(root,network,seh)
    if (plan['state_generation'],plan['state_content_sha256'],plan['coolify_identity'],plan['coolify_origin']) != (
            generation,state_hash,record['coolify']['active_identity'],_origin(controller)):
        raise SEHGitError('Identity changed since prepare; run prepare again')
    if plan['application_uuid'] != str(record['deployment'].get('application_uuid') or ''):
        raise SEHGitError('SEH deployment changed since prepare; run prepare again')
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
    if plan['application_uuid'] != str(record['deployment'].get('application_uuid') or ''):
        raise SEHGitError('SEH deployment changed since prepare; run prepare again')
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
    # Install only SEH operational lifecycle state, never a Mother mutation.
    if not record['deployment'].get('application_uuid'):
        deployment = deepcopy(record['deployment'])
        deployment['application_uuid'] = uuid
        deployment['git'] = {'mode':'coolify-git', 'enabled':True,
                             'project_uuid':plan['placement']['project_uuid'],
                             'server_uuid':plan['placement']['server_uuid'],
                             'environment_uuid':plan['placement']['environment_uuid']}
        with ds.mutation_lock(root, network):
            _, _, doc = st.read(root, network)
            identity_record = st.seh_doc(doc, network, seh)
            ds.write(root, network, identity_record, expected_uuid=None, deployment=deployment)
    elif record['deployment']['application_uuid'] != uuid:
        raise SEHGitError('Existing SEH application UUID differs from prepared target')
    # Capture the existing remote trust binding to prove an infrastructure
    # redeploy never drops or mutates it. We do not read local SLH state.
    trust_keys = {'SEH_SLH_PUBLIC_KEY_B64'}
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
            'seh_deployment_recorded':True,'ready':False, 'public_wss_verified':False}




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

    keys = ('SEH_SLH_PUBLIC_KEY_B64',)
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
                   {'SEH_SLH_PUBLIC_KEY_B64': public_key_b64})
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
    if current != {'SEH_SLH_PUBLIC_KEY_B64': public_key_b64}:
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
    if (ds.identity_binding(later) != ds.identity_binding(record) or
            later['deployment'].get('application_uuid') != uuid):
        raise SEHGitError('SEH controller identity or deployment changed during trust update')
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



def _public_runtime_probe(public_url: str) -> dict:
    """Observe public relay availability, not Docker existence or WSS readiness.

    A proxy 404/502/503/504 is an outage signal, NOT proof of why the
    upstream is missing. Only the registered HTTPS hostname is probed.
    """
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None

    request = Request(public_url.rstrip('/') + '/healthz',
                      headers={'Accept': 'application/json',
                               'User-Agent': 'Mozilla/5.0'}, method='GET')
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=6) as response:
            code = response.status
            content = response.read(4096)
    except HTTPError as exc:
        code = exc.code
        content = b''
    except (URLError, TimeoutError, OSError) as exc:
        raise SEHGitError('Public SEH health could not be verified: ' +
                          type(exc).__name__ + '; refusing speculative redeployment') from None
    if code in (404, 502, 503, 504):
        return {'status': 'PUBLIC_RUNTIME_UNAVAILABLE', 'http_status': code}
    if code != 200:
        raise SEHGitError(f'Public SEH health returned HTTP {code}; '
                          'refusing speculative redeployment')
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise SEHGitError('Public SEH returned malformed health response; refusing redeployment') from None
    if (not isinstance(payload, dict) or payload.get('service') != 'main_computer.seh' or
            payload.get('live') is not True):
        raise SEHGitError('Public endpoint did not identify the expected live SEH; refusing redeployment')
    return {'status': 'PUBLIC_RUNTIME_PRESENT', 'http_status': 200,
            'slh_connected': payload.get('slh_connected') is True}


_RECOVERY_CHECKPOINT = 'seh-coolify-recovery-v1.json'


def _recovery_checkpoint(state_dir: Path) -> dict | None:
    """Read only this SLH's local transaction receipt, never a Coolify alias."""
    path = Path(state_dir) / _RECOVERY_CHECKPOINT
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError):
        raise SEHGitError('SLH SEH recovery receipt is unreadable; do not create another app') from None
    if (not isinstance(data, dict) or data.get('schema') != 'seh-coolify-recovery.v1' or
            any(not isinstance(data.get(k), str) for k in
                ('old_uuid', 'new_uuid', 'mother_digest', 'git_revision', 'key_sha256'))):
        raise SEHGitError('SLH SEH recovery receipt is invalid; do not create another app')
    if ('retry_revision' in data and data['retry_revision'] is not None and
            (not isinstance(data['retry_revision'], str) or
             not _SHA.fullmatch(data['retry_revision']))):
        raise SEHGitError('SEH retry revision in recovery receipt is invalid')
    if ('build_submission' in data and
            data['build_submission'] not in ('INTENT', 'ACKNOWLEDGED')):
        raise SEHGitError('SEH recovery build submission receipt is invalid')
    if ('deployment_uuid' in data and
            (not isinstance(data['deployment_uuid'], str) or
             not _ID.fullmatch(data['deployment_uuid']))):
        raise SEHGitError('SEH recovery build UUID receipt is invalid')
    history = data.get('previous_failed_builds', [])
    if (not isinstance(history, list) or len(history) > 128 or
            any(not isinstance(row, dict) or
                not isinstance(row.get('deployment_uuid'), str) or
                not _ID.fullmatch(row['deployment_uuid']) or
                not isinstance(row.get('git_revision'), str) or
                not _SHA.fullmatch(row['git_revision']) for row in history)):
        raise SEHGitError('SEH previous failed build history is invalid')
    return data



def _recovery_undeploy_evidence(checkpoint: dict, current_uuid: str, client,
                                 application: dict, public_key_b64: str) -> str:
    """Classify a historical recovery receipt without ignoring unfinished work.

    Recovery receipts are retained after successful replacement and remain
    useful audit records. They are not all outstanding transactions. Allow a
    completed, independently verified build of this *exact* canonical UUID;
    ambiguous, pending, failed, and mismatched receipts still prevent DELETE.
    """
    if checkpoint['new_uuid'] != current_uuid or checkpoint['old_uuid'] == current_uuid:
        raise SEHGitError('Recovery receipt refers to another operational UUID; reconcile before undeploy')
    try:
        public_der = base64.b64decode(public_key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise SEHGitError('SLH public signing identity invalid; refusing undeploy') from None
    if checkpoint['key_sha256'] != hashlib.sha256(public_der).hexdigest():
        raise SEHGitError('Recovery receipt signing identity mismatch; refusing undeploy')
    build_uuid = checkpoint.get('deployment_uuid')
    if not isinstance(build_uuid, str) or not _ID.fullmatch(build_uuid):
        raise SEHGitError('Recovery receipt has no verified build UUID; reconcile before undeploy')
    if checkpoint.get('build_submission') == 'INTENT':
        raise SEHGitError('Recovery build submission remains unacknowledged; reconcile before undeploy')
    revision = checkpoint.get('retry_revision') or checkpoint['git_revision']
    if not _SHA.fullmatch(revision):
        raise SEHGitError('Recovery receipt Git revision is unverified; refusing undeploy')
    proof = _verify_seh_build_receipt(client, current_uuid, build_uuid, revision,
                                     app_id=application.get('id'))
    if proof.get('status') != 'VERIFIED':
        raise SEHGitError('Recovery build receipt is not independently verified; reconcile before undeploy')
    if proof.get('deployment_status', '').lower() not in _REDEPLOY_TERMINAL_OK:
        raise SEHGitError('Recovery build is not successfully finished; reconcile before undeploy')
    return 'COMPLETED_VERIFIED'


def _save_recovery_checkpoint(state_dir: Path, data: dict) -> None:
    """Persist Coolify's returned UUID before any environment/Mother writes.

    This local recovery evidence is not a second identity authority. It only
    authorizes resuming an exact POST already acknowledged by Coolify.
    """
    directory = Path(state_dir)
    if not directory.is_dir():
        raise SEHGitError('SLH recovery state directory unavailable after Coolify creation')
    descriptor, scratch = tempfile.mkstemp(prefix='.seh-recovery-', dir=directory)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as out:
            json.dump(data, out, sort_keys=True)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(scratch, directory / _RECOVERY_CHECKPOINT)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


def _forced_seh_build(client, application_uuid: str) -> str:
    """Submit a forced Git build; never fall back to /start or /restart.

    Mirror Stable Hub's forced-build endpoint and mandatory deployment UUID.
    A missing ID is an uncertain submission, never a verified build.
    """
    path = '/api/v1/deploy?' + urlencode({'uuid': application_uuid, 'force': 'true'})
    response = _api(client, 'POST', path)
    def one(value):
        if isinstance(value, dict):
            if value.get('resource_uuid') not in (None, application_uuid):
                raise SEHGitError('Coolify build acknowledgment targets a different application')
            if isinstance(value.get('deployment_uuid'), str) and _ID.fullmatch(value['deployment_uuid']):
                return value['deployment_uuid']
            for key in ('deployments', 'body'):
                if key in value:
                    found = one(value[key])
                    if found:
                        return found
        elif isinstance(value, list):
            for item in value:
                found = one(item)
                if found:
                    return found
        return None
    deployment_uuid = one(response)
    if not deployment_uuid:
        raise SEHGitError('Forced SEH build acknowledgment has no verified deployment UUID')
    return deployment_uuid


def _verify_seh_build_receipt(client, uuid: str, deployment_uuid: str,
                              revision: str, *, app_id: int | None = None) -> dict:
    """Verify Coolify's durable forced-build record, not merely an HTTP 200.

    A newly queued build can briefly be absent from GET. Report that as
    unverified, retaining the UUID for reconciliation; never retry blindly.
    """
    path = '/api/v1/deployments/' + quote(deployment_uuid, safe='')
    try:
        response = client.request('GET', path)
    except Exception as exc:
        return {'status': 'READBACK_UNAVAILABLE', 'error_class': type(exc).__name__}
    if hasattr(response, 'ok'):
        if not response.ok:
            return {'status': 'NOT_YET_OBSERVED' if response.status == 404 else 'READBACK_UNAVAILABLE',
                    'http_status': response.status}
        response = response.body
    if not isinstance(response, dict) or response.get('deployment_uuid') != deployment_uuid:
        return {'status': 'CONFLICT', 'reason': 'deployment_uuid_mismatch'}
    if (response.get('force_rebuild') is not True or response.get('is_api') is not True or
            response.get('restart_only') is True):
        return {'status': 'CONFLICT', 'reason': 'not_a_verified_forced_api_build'}
    if response.get('resource_uuid') not in (None, uuid):
        return {'status': 'CONFLICT', 'reason': 'application_uuid_mismatch'}
    if (app_id is not None and response.get('application_id') not in (None, app_id, str(app_id))):
        return {'status': 'CONFLICT', 'reason': 'application_id_mismatch'}
    commit = response.get('commit')
    if not isinstance(commit, str) or not _SHA.fullmatch(commit.lower()) or commit.lower() != revision.lower():
        return {'status': 'CONFLICT', 'reason': 'git_revision_unverified'}
    return {'status': 'VERIFIED', 'deployment_status': str(response.get('status') or 'unknown')}




# A forced redeployment is independent of missing-runtime recovery. Its durable
# checkpoint belongs to SEH operational state (not SLH identity or Mother).
_REDEPLOY_CHECKPOINT = 'seh-canonical-redeploy-v1.json'
_REDEPLOY_SCHEMA = 'seh-canonical-redeploy.v1'
_REDEPLOY_TERMINAL_OK = frozenset(('finished', 'success', 'succeeded', 'completed', 'complete'))
_REDEPLOY_TERMINAL_FAILED = frozenset(('failed', 'failure', 'error', 'cancelled', 'canceled'))


def _redeploy_checkpoint(root: Path, network: str) -> dict | None:
    path = ds.directory(root, network) / _REDEPLOY_CHECKPOINT
    if path.is_symlink():
        raise SEHGitError('SEH redeployment receipt cannot be a symlink')
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError):
        raise SEHGitError('SEH redeployment receipt unreadable; refusing duplicate submission') from None
    required = ('schema', 'network', 'application_uuid', 'identity_binding',
                'git_revision', 'phase')
    if (type(data) is not dict or any(type(data.get(key)) is not str for key in required)
            or data['schema'] != _REDEPLOY_SCHEMA or data['network'] != network
            or not _ID.fullmatch(data['application_uuid'])
            or not re.fullmatch('[0-9a-f]{64}', data['identity_binding'])
            or not _SHA.fullmatch(data['git_revision'])
            or data['phase'] not in ('INTENT', 'ACKNOWLEDGED', 'VERIFIED', 'FAILED')
            or (data['phase'] in ('ACKNOWLEDGED', 'VERIFIED', 'FAILED') and
                not isinstance(data.get('deployment_uuid'), str))
            or ('deployment_uuid' in data and not _ID.fullmatch(data['deployment_uuid']))):
        raise SEHGitError('SEH redeployment receipt invalid; refusing duplicate submission')
    return data


def _save_redeploy_checkpoint(root: Path, network: str, data: dict) -> None:
    """Atomic fsynced evidence, never a second application identity authority."""
    folder = ds.directory(root, network)
    if folder.is_symlink():
        raise SEHGitError('SEH redeployment directory cannot be a symlink')
    from main_computer.slh.storage import secure_machine_state_dir, secure_private_state_file
    secure_machine_state_dir(folder)
    target = folder / _REDEPLOY_CHECKPOINT
    if target.is_symlink():
        raise SEHGitError('SEH redeployment receipt cannot be a symlink')
    fd, scratch = tempfile.mkstemp(prefix='.seh-redeploy-', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        secure_private_state_file(Path(scratch))
        os.replace(scratch, target)
        if os.name != 'nt':
            handle = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(handle)
            finally:
                os.close(handle)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)



def _reconcile_redeploy_intent(client, checkpoint: dict, *, app_id: int | None) -> str | None:
    """Recover a lost POST acknowledgment without authorizing a second POST.

    Only a uniquely identified, receipt-verified forced API deployment for
    this exact application and pinned revision, created after the persisted
    intent, may be adopted. Missing metadata is an unresolved outcome, not
    permission to retry the mutation.
    """
    try:
        started = datetime.fromisoformat(checkpoint['created_utc'].replace('Z', '+00:00'))
        if started.tzinfo is None:
            return None
        candidates = _items(_api(client, 'GET', '/api/v1/deployments'), 'deployments')
    except (SEHGitError, ValueError, KeyError, TypeError):
        return None
    accepted = []
    for row in candidates:
        if row.get('resource_uuid', row.get('application_uuid')) != checkpoint['application_uuid']:
            continue
        uuid = row.get('deployment_uuid')
        created = row.get('created_at')
        if not isinstance(uuid, str) or not _ID.fullmatch(uuid) or not isinstance(created, str):
            continue
        try:
            timestamp = datetime.fromisoformat(created.replace('Z', '+00:00'))
            if timestamp.tzinfo is None or timestamp < started:
                continue
        except ValueError:
            continue
        proof = _verify_seh_build_receipt(client, checkpoint['application_uuid'], uuid,
                                          checkpoint['git_revision'], app_id=app_id)
        if proof['status'] == 'VERIFIED':
            accepted.append(uuid)
    if len(set(accepted)) > 1:
        raise SEHGitError('Multiple plausible SEH forced builds after ambiguous POST; refusing adoption')
    return accepted[0] if accepted else None


def redeploy_canonical_runtime(root: Path, network: str, *, execute: bool = False,
                               client=None, probe=None, timeout_s: float = 900.0,
                               poll_s: float = 5.0, clock=None, sleep=None) -> dict:
    """Force-rebuild the ONE verified SEH UUID and certify its replacement.

    Unlike recover-runtime, a healthy live relay does not block this explicitly
    approved operation. Verification never authorizes changing trust, Git pin,
    app identity, deployment placement, or Mother state. An ambiguous POST is
    checkpointed as INTENT and cannot silently be repeated.
    """
    from tools.seh_control.dns_probe import domain_hosts

    if not 1 <= timeout_s <= 3600 or not 0 < poll_s <= 60:
        raise SEHGitError('Invalid SEH deployment wait parameters')
    now = clock or time.monotonic
    pause = sleep or time.sleep
    observe = probe or _public_runtime_probe

    # Plan mode does not take the mutation lock or write durable state.
    lock = ds.mutation_lock(root, network) if execute else _nullcontext()
    with lock:
        record, controller, mother_generation, _ = _canonical_selection(root, network)
        uuid = record['deployment']['application_uuid']
        remote = client if client is not None else _client(controller)
        app = _api(remote, 'GET', f'/api/v1/applications/{uuid}')
        if not isinstance(app, dict) or app.get('uuid') != uuid:
            raise SEHGitError('Canonical SEH application missing or UUID changed; refusing redeploy')
        place = _canonical_placement(remote, record)
        _verify_application_placement(remote, app, place)
        _verify_canonical_source(app)
        if _verify_canonical_metadata(app) != 'CANONICAL':
            raise SEHGitError('Existing SEH application metadata is not canonical')
        revision = app.get('git_commit_sha')
        if not isinstance(revision, str) or not _SHA.fullmatch(revision.lower()):
            raise SEHGitError('Existing SEH Git revision is not a verified 40-character pin')
        revision = revision.lower()
        domain = domain_hosts(app)
        expected_url = record['deployment'].get('public_url') or DEFAULT_PUBLIC_URL
        public = urlsplit(expected_url)
        if (public.scheme != 'https' or not public.hostname or public.username or public.password
                or public.path not in ('', '/') or public.query or public.fragment
                or len(domain) != 1 or domain[0].lower() != public.hostname.lower()):
            raise SEHGitError('Canonical SEH public domain cannot be verified')
        public_url = 'https://' + domain[0]
        trust = _trust_snapshot(remote, uuid)
        keys = {row['key'] for row in trust}
        if 'SEH_SLH_PUBLIC_KEY_B64' not in keys:
            raise SEHGitError('Canonical SEH signing-key trust is incomplete')
        binding = ds.identity_binding(record)
        checkpoint = _redeploy_checkpoint(root, network)
        result = {'network': network, 'application_uuid': uuid,
                  'git_revision': revision, 'public_url': public_url,
                  'mother_generation': mother_generation,
                  'mother_mutation': 'UNCHANGED', 'signing_key_trust': 'MATCHED',
                  'remote_mutation': False}
        if checkpoint and (checkpoint['application_uuid'] != uuid or
                           checkpoint['identity_binding'] != binding):
            raise SEHGitError('Previous redeployment receipt belongs to a different canonical SEH')
        if checkpoint and checkpoint['phase'] != 'VERIFIED' and checkpoint['git_revision'] != revision:
            raise SEHGitError('SEH Git pin changed while a redeployment was unresolved')
        if not execute:
            return {**result, 'status': 'PLAN_ONLY', 'action': 'FORCE_REDEPLOY_EXISTING_UUID',
                    'prior_redeployment_phase': checkpoint['phase'] if checkpoint else None}

        if checkpoint and checkpoint['phase'] == 'INTENT':
            accepted_uuid = _reconcile_redeploy_intent(remote, checkpoint, app_id=app.get('id'))
            if accepted_uuid is None:
                return {**result, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                        'failed_stage': 'unacknowledged_prior_deployment',
                        'reason': 'POST outcome still ambiguous; no second build authorized'}
            checkpoint = {**checkpoint, 'phase': 'ACKNOWLEDGED',
                          'deployment_uuid': accepted_uuid}
            _save_redeploy_checkpoint(root, network, checkpoint)
        if checkpoint and checkpoint['phase'] == 'FAILED':
            return {**result, 'status': 'INCOMPLETE', 'failed_stage': 'prior_deployment_failed',
                    'deployment_uuid': checkpoint['deployment_uuid']}

        deployment_uuid = None
        if checkpoint and checkpoint['phase'] == 'ACKNOWLEDGED':
            deployment_uuid = checkpoint['deployment_uuid']
        else:
            # Mutating preflight is repeated under the SEH-owned exclusive lock.
            later, _, _, _ = _canonical_selection(root, network)
            if _operational_binding(later) != _operational_binding(record):
                raise SEHGitError('SEH operational identity changed before deployment')
            recent_app = _api(remote, 'GET', f'/api/v1/applications/{uuid}')
            if recent_app != app or _trust_snapshot(remote, uuid) != trust:
                raise SEHGitError('SEH Coolify application or signing trust changed before deployment')
            observed = str(app.get('status') or '').lower()
            if any(word in observed for word in ('building', 'deploying', 'starting', 'restarting')):
                raise SEHGitError('SEH has an active build or replacement; refusing overlapping deploy')
            active = _items(_api(remote, 'GET', '/api/v1/deployments'), 'deployments')
            for dep in active:
                # Some Coolify releases include historical terminal entries in
                # the deployments listing; these do not constitute an active
                # build. Unknown status remains fail-closed.
                stage = str(dep.get('status') or '').strip().lower()
                if stage in _REDEPLOY_TERMINAL_OK | _REDEPLOY_TERMINAL_FAILED:
                    continue
                matched = (dep.get('resource_uuid') == uuid or dep.get('application_uuid') == uuid
                           or (app.get('id') is not None and str(dep.get('application_id')) == str(app['id'])))
                if matched:
                    raise SEHGitError('SEH already has an active Coolify deployment')
                if not any(k in dep for k in ('resource_uuid', 'application_uuid', 'application_id')):
                    raise SEHGitError('Coolify active deployment identity is ambiguous; refusing redeploy')
            intent = {'schema': _REDEPLOY_SCHEMA, 'network': network,
                      'application_uuid': uuid, 'identity_binding': binding,
                      'git_revision': revision, 'phase': 'INTENT',
                      'created_utc': datetime.now(timezone.utc).isoformat()}
            _save_redeploy_checkpoint(root, network, intent)
            try:
                deployment_uuid = _forced_seh_build(remote, uuid)
            except SEHGitError as exc:
                return {**result, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                        'failed_stage': 'forced_build_submission', 'reason': str(exc)}
            checkpoint = {**intent, 'phase': 'ACKNOWLEDGED',
                          'deployment_uuid': deployment_uuid}
            try:
                _save_redeploy_checkpoint(root, network, checkpoint)
            except (OSError, SEHGitError):
                return {**result, 'status': 'INCOMPLETE', 'remote_mutation': True,
                        'deployment_uuid': deployment_uuid,
                        'failed_stage': 'deployment_receipt_persistence'}

        result.update(remote_mutation=True, deployment_uuid=deployment_uuid)
        if client is None:
            print(f'SEH_PROGRESS: forced build {deployment_uuid} on {uuid}; waiting for Coolify',
                  file=sys.stderr, flush=True)
        deadline = now() + timeout_s
        last_log_status = None
        last_log_time = -float('inf')
        while True:
            proof = _verify_seh_build_receipt(remote, uuid, deployment_uuid, revision,
                                              app_id=app.get('id'))
            status_label = proof.get('deployment_status', proof['status'])
            moment = now()
            if client is None and (status_label != last_log_status or moment - last_log_time >= 30):
                print(f'SEH_PROGRESS: Coolify deployment status={status_label}',
                      file=sys.stderr, flush=True)
                last_log_status, last_log_time = status_label, moment
            if proof['status'] == 'CONFLICT':
                return {**result, 'status': 'INCOMPLETE', 'failed_stage': 'deployment_receipt_conflict',
                        'build_verification_reason': proof.get('reason')}
            if proof['status'] == 'VERIFIED':
                state = proof['deployment_status'].strip().lower()
                if state in _REDEPLOY_TERMINAL_FAILED:
                    _save_redeploy_checkpoint(root, network, {**checkpoint, 'phase': 'FAILED'})
                    return {**result, 'status': 'INCOMPLETE', 'failed_stage': 'deployment_build_failed',
                            'build_status': state}
                if state in _REDEPLOY_TERMINAL_OK:
                    break
            if now() >= deadline:
                return {**result, 'status': 'INCOMPLETE', 'failed_stage': 'deployment_wait_timeout',
                        'build_verification': proof['status'],
                        'build_status': proof.get('deployment_status')}
            pause(min(poll_s, max(0, deadline - now())))

        # A completed build is NOT proof that the application, signing key, or
        # actual public endpoint still belongs to this canonical deployment.
        if client is None:
            print('SEH_PROGRESS: Coolify build finished; verifying public runtime and signing trust',
                  file=sys.stderr, flush=True)
        while True:
            later, _, _, _ = _canonical_selection(root, network)
            if _operational_binding(later) != _operational_binding(record):
                raise SEHGitError('Canonical SEH identity changed during deployment verification')
            replacement = _api(remote, 'GET', f'/api/v1/applications/{uuid}')
            if not isinstance(replacement, dict) or replacement.get('uuid') != uuid:
                raise SEHGitError('SEH application identity changed after deployment')
            _verify_application_placement(remote, replacement, place)
            _verify_canonical_source(replacement)
            if (_verify_canonical_metadata(replacement) != 'CANONICAL'
                    or replacement.get('git_commit_sha', '').lower() != revision
                    or _trust_snapshot(remote, uuid) != trust):
                raise SEHGitError('SEH source, metadata or signing trust changed after deployment')
            try:
                runtime = observe(public_url)
            except (SEHGitError, ValueError) as exc:
                # Access-control denials at the public edge are not missing
                # runtimes. Keep the acknowledged build for a later verified
                # retry; do not spin for 900 seconds or submit again.
                if isinstance(exc, SEHGitError) and 'HTTP 403' in str(exc):
                    return {**result, 'status': 'INCOMPLETE',
                            'failed_stage': 'public_health_edge_denied',
                            'http_status': 403, 'build_status': 'finished'}
                runtime = {'status': 'UNVERIFIED', 'reason': type(exc).__name__}
            if (isinstance(runtime, dict) and runtime.get('status') == 'PUBLIC_RUNTIME_PRESENT'
                    and runtime.get('http_status') == 200):
                # Do not certify trust based on an observation taken before the
                # public health request. A key rotation between the two
                # observations must invalidate the deployment acceptance.
                if _trust_snapshot(remote, uuid) != trust:
                    raise SEHGitError('SEH signing trust changed during post-deployment health verification')
                final_record, _, _, _ = _canonical_selection(root, network)
                if _operational_binding(final_record) != _operational_binding(record):
                    raise SEHGitError('SEH canonical identity changed during public health verification')
                _save_redeploy_checkpoint(root, network, {**checkpoint, 'phase': 'VERIFIED'})
                return {**result, 'status': 'SEH_REDEPLOY_HARNESS_COMPLETE',
                        'build_status': 'finished', 'build_verification': 'VERIFIED',
                        'public_health': 'VERIFIED', 'http_status': runtime.get('http_status'),
                        'slh_connected': runtime.get('slh_connected') is True}
            if now() >= deadline:
                return {**result, 'status': 'INCOMPLETE', 'failed_stage': 'public_runtime_verification',
                        'runtime_observation': runtime.get('status') if isinstance(runtime, dict) else 'UNVERIFIED'}
            pause(min(poll_s, max(0, deadline - now())))


def _restore_deleted_unlocked(root: Path, network: str, *, execute: bool,
                                          state_dir: Path, client=None, source=None,
                                          repo_root: Path | None = None) -> dict:
    """Recover a verified deleted Coolify app, using SEH-owned operational CAS.

    Mother supplies verified controller identity only and is never mutated.
    A deploy queue is never considered connected.
    """
    from tools.slh_seh_connector import service_identity
    from tools.seh_control.dns_probe import domain_hosts
    from tools.mother.common.errors import MotherError

    record, controller, generation, digest = _canonical_selection(root, network)
    old_uuid = record['deployment']['application_uuid']
    remote = client if client is not None else _client(controller)
    existing = _registered_application(remote, old_uuid)
    if existing is not None:
        raise SEHGitError('Canonical application still exists; do not recreate it')

    place = _canonical_placement(remote, record)
    # Check the registered server independently before creating anything.
    server = _api(remote, 'GET', f"/api/v1/servers/{place['server_uuid']}")
    if not isinstance(server, dict) or server.get('uuid') != place['server_uuid'] or \
            server.get('is_usable') is False or server.get('is_reachable') is False:
        raise SEHGitError('Canonical Coolify server is not verified for SEH recovery')
    apps = _items(_api(remote, 'GET', '/api/v1/applications'), 'applications')
    if any(a.get('uuid') == old_uuid for a in apps):
        raise SEHGitError('Coolify inventory still lists deleted UUID; refusing ambiguous recreation')
    # An earlier recovery may have completed creation and trust binding before
    # SEH local state was committed. One such orphan can be resumed, but only
    # after independent placement, source, ownership and live key checks.
    candidates = [a for a in apps if a.get('name') == CANONICAL_APPLICATION_NAME or
                  'external.greatlibrary.io' in str(a.get('domains') or '')]
    if len(candidates) > 1:
        raise SEHGitError('Coolify has multiple possible SEH applications; refusing duplicate creation')
    checkpoint = _recovery_checkpoint(state_dir)
    try:
        _, public = service_identity(Path(state_dir), create=False)
        public_der = base64.b64decode(public, validate=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SEHGitError('Preserved SLH signing public key unavailable: ' + type(exc).__name__) from None
    # A route is a deterministic encoding of existing cryptographic identity,
    # not an operator-selected SLH or organization name.
    route = 'slh-' + hashlib.sha256(public_der).hexdigest()[:32]
    published = source if source is not None else git_source(
        repo_root or Path(__file__).resolve().parents[2])
    if published.get('repository') != 'https://github.com/' + _EXPECTED_GITHUB_REPOSITORY or \
            published.get('dockerfile_location') != DOCKERFILE or \
            not _SHA.fullmatch(str(published.get('revision', ''))) or \
            not re.fullmatch(r'[A-Za-z0-9._/-]+', str(published.get('branch', ''))):
        raise SEHGitError('Published SEH source cannot be verified for reconstruction')
    plan = {'application_name': CANONICAL_APPLICATION_NAME,
            'ownership_marker': CANONICAL_DESCRIPTION,
            'public_url': DEFAULT_PUBLIC_URL, 'internal_port': 8080,
            'placement': place, 'source': published}
    key_digest = hashlib.sha256(public_der).hexdigest()
    # A durable POST receipt allows resuming partial environment configuration
    # without treating a matching Coolify display name as identity evidence.
    receipt_matches = bool(checkpoint and all((
        checkpoint['old_uuid'] == old_uuid,
        checkpoint['mother_digest'] == digest,
        checkpoint['git_revision'] == published['revision'],
        checkpoint['key_sha256'] == key_digest,
    )))
    if checkpoint and not receipt_matches and candidates:
        raise SEHGitError('SLH recovery receipt conflicts with canonical authority; refusing orphan adoption')
    out = {'network': network, 'former_application_uuid': old_uuid,
           'mother_generation': generation, 'remote_mutation': False,
           'mother_mutation': 'UNCHANGED', 'public_url': DEFAULT_PUBLIC_URL,
           'signing_key_untouched': True,
           'management_transport': ('OPERATOR_ACCEPTED_HTTP'
                                    if _origin(controller).startswith('http://') else 'HTTPS')}
    if not execute:
        return {**out, 'status': 'PLAN_ONLY',
                'action': ('VERIFY_AND_ADOPT_EXISTING_APPLICATION' if candidates else
                           'RECREATE_DELETED_APPLICATION'),
                'git_revision': published['revision']}
    # Re-observe all authority and uniqueness conditions at mutation boundary.
    newer, _, new_gen, new_hash = _canonical_selection(root, network)
    if (_operational_binding(newer) != _operational_binding(record) or
            newer['deployment'].get('application_uuid') != old_uuid):
        raise SEHGitError('SEH controller identity or deployment changed during recovery')
    if _registered_application(remote, old_uuid) is not None:
        raise SEHGitError('Former Coolify UUID reappeared; refusing creation')
    if _items(_api(remote, 'GET', '/api/v1/applications'), 'applications') != apps:
        raise SEHGitError('Coolify application topology changed during recovery')

    # A duplicate is not authority. A previously created app must have the
    # canonical provenance and exact current signing-key trust to be adopted.
    resumed = bool(candidates)
    if resumed:
        new_uuid = candidates[0].get('uuid')
        if not isinstance(new_uuid, str) or not _ID.fullmatch(new_uuid) or new_uuid == old_uuid:
            raise SEHGitError('Possible SEH application has no valid replacement UUID; refusing duplicate creation')
        try:
            candidate = _registered_application(remote, new_uuid)
            if candidate is None:
                raise SEHGitError('Possible SEH application disappeared before adoption')
            _verify_application_placement(remote, candidate, place)
            _verify_canonical_source(candidate)
            if candidate.get('git_branch') not in (None, published['branch']) or \
                    candidate.get('git_commit_sha') not in (None, published['revision']):
                raise SEHGitError('Possible SEH application has a different published Git source')
            if _verify_canonical_metadata(candidate) != 'CANONICAL':
                raise SEHGitError('Possible SEH application has unverified ownership')
            if domain_hosts(candidate) != ['external.greatlibrary.io']:
                raise SEHGitError('Possible SEH application has unverified public domain')
            actual = _trust_snapshot(remote, new_uuid)
            expected_trust = {'SEH_SLH_PUBLIC_KEY_B64': public}
            missing_trust = []
            for key, expected in expected_trust.items():
                got = _diagnostic_env_value(actual, key)
                if got['state'] == 'MISSING':
                    missing_trust.append(key)
                elif got != {'state': 'VISIBLE', 'value': expected}:
                    raise SEHGitError('Possible SEH application trust is unverified or differs')
            # Only a previously acknowledged Coolify POST bound to this SLH's
            # durable receipt may install missing trust. Never overwrite or
            # invent trust on an unrelated application with a matching name.
            if missing_trust and not (receipt_matches and checkpoint['new_uuid'] == new_uuid):
                raise SEHGitError('Partial trust cannot be adopted without matching SLH creation receipt')
        except SEHGitError as exc:
            raise SEHGitError('Coolify has a possible SEH application; duplicate creation refused because adoption is not verified: ' +
                              str(exc)) from None
    else:
        # POST can succeed while the response is lost. A retry must observe
        # inventory and verify the orphan before considering another POST.
        try:
            response = _api(remote, 'POST', '/api/v1/applications/public',
                            _application_payload(plan))
        except SEHGitError:
            return {**out, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                    'failed_stage': 'coolify_application_create_unconfirmed'}
        new_uuid = ((response.get('application') or {}).get('uuid') or response.get('uuid')) \
            if isinstance(response, dict) else None
        if not isinstance(new_uuid, str) or not _ID.fullmatch(new_uuid) or new_uuid == old_uuid:
            return {**out, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                    'failed_stage': 'coolify_creation_uuid_unverified'}
        try:
            _save_recovery_checkpoint(state_dir, {
                'schema': 'seh-coolify-recovery.v1', 'old_uuid': old_uuid,
                'new_uuid': new_uuid, 'mother_digest': digest,
                'git_revision': published['revision'], 'key_sha256': key_digest})
        except (OSError, SEHGitError) as exc:
            return {**out, 'status': 'INCOMPLETE', 'application_uuid': new_uuid,
                    'remote_mutation': True, 'failed_stage': 'recovery_receipt_write',
                    'error_class': type(exc).__name__}
    changed = {**out, 'application_uuid': new_uuid, 'remote_mutation': not resumed,
               'recovery_action': ('ADOPT_VERIFIED_APPLICATION' if resumed else 'CREATED_APPLICATION')}
    try:
        app = _registered_application(remote, new_uuid)
        if app is None:
            raise SEHGitError('New application not available for readback')
        _verify_application_placement(remote, app, place)
        _verify_canonical_source(app)
        if _verify_canonical_metadata(app) != 'CANONICAL':
            raise SEHGitError('New application metadata does not match canonical service')
        if app.get('git_branch') not in (None, published['branch']) or \
                app.get('git_commit_sha') not in (None, published['revision']):
            raise SEHGitError('New application Git source revision changed before trust binding')
        domains = domain_hosts(app)
        if domains != ['external.greatlibrary.io']:
            raise SEHGitError('New Coolify application public domain could not be verified')
        env_data = {
            'SEH_PUBLIC_ORIGIN': DEFAULT_PUBLIC_URL,
            'SEH_AUDIT_MODE': 'metadata_only', 'SEH_PORT': '8080',
            'SEH_SLH_PUBLIC_KEY_B64': public}
        existing_rows = _items(_api(remote, 'GET', f'/api/v1/applications/{new_uuid}/envs'), 'envs')
        if not resumed and _trust_snapshot(remote, new_uuid):
            raise SEHGitError('New application has preexisting remote trust; refusing overwrite')
        # Write only absent variables on a receipt-backed retry, preserving
        # any previously installed matching trust values.
        absent = {}
        for key, value in env_data.items():
            rows_for_key = [row for row in existing_rows if row.get('key') == key
                            and row.get('is_preview') is not True]
            if len(rows_for_key) > 1:
                raise SEHGitError('Duplicate environment field prevents safe SEH recovery: ' + key)
            if not rows_for_key:
                absent[key] = value
            elif key in ('SEH_SLH_PUBLIC_KEY_B64',):
                if _diagnostic_env_value(existing_rows, key) != {'state': 'VISIBLE', 'value': value}:
                    raise SEHGitError('SEH signing trust changed before recovery: ' + key)
            elif value not in (rows_for_key[0].get('real_value'), rows_for_key[0].get('value')):
                raise SEHGitError('SEH runtime environment changed before recovery: ' + key)
        if absent:
            _api(remote, 'PATCH', f'/api/v1/applications/{new_uuid}/envs/bulk',
                 {'data': [{'key': key, 'value': value, 'is_literal': True,
                            'is_preview': False, 'is_runtime': True,
                            'is_buildtime': False, 'is_multiline': False}
                           for key, value in absent.items()]})
        rows = _items(_api(remote, 'GET', f'/api/v1/applications/{new_uuid}/envs'), 'envs')
        if any(row.get('key') == 'SEH_ORGANIZATION_ID' and row.get('is_preview') is not True
               for row in rows):
            raise SEHGitError('Unexpected obsolete organization identity in new SEH')
        for key, value in env_data.items():
            if key in ('SEH_SLH_PUBLIC_KEY_B64',):
                got = _diagnostic_env_value(rows, key)
                verified = got['state'] == 'VISIBLE' and got['value'] == value
            else:
                found = [row for row in rows if row.get('key') == key and
                         row.get('is_preview') is not True]
                verified = len(found) == 1 and value in (
                    found[0].get('real_value'), found[0].get('value'))
            if not verified:
                raise SEHGitError('New SEH environment readback failed for ' + key)
        # Adopt into SEH's own durable lifecycle file. The verified Mother
        # controller identity is read-only; unrelated Mother generations do
        # not alter or authorize SEH deployment decisions.
        newer, _, _, _ = _canonical_selection(root, network)
        if (_operational_binding(newer) != _operational_binding(record) or
                newer['deployment'].get('application_uuid') != old_uuid):
            raise SEHGitError('SEH controller identity or UUID changed after Coolify creation')
        if _registered_application(remote, new_uuid) != app:
            raise SEHGitError('New Coolify application changed before SEH adoption')
        _, _, identity_doc = st.read(root, network, 'deployment-identity-read')
        identity_record = st.deployment_record(identity_doc, network)
        if identity_record is None or ds.identity_binding(identity_record) != ds.identity_binding(record):
            raise SEHGitError('Verified SEH controller identity changed before adoption')
        successor = deepcopy(newer['deployment'])
        successor['application_uuid'] = new_uuid
        # A new, explicitly approved deployment ends the prior UUID's tombstone.
        # The UUID-scoped undeploy receipt remains as independent audit evidence.
        successor.pop('undeployed', None)
        successor.pop('undeployed_at', None)
        committed = ds.write(root, network, identity_record,
                             expected_uuid=old_uuid, deployment=successor)
        changed['seh_deployment_revision'] = committed['revision']
        changed['mother_mutation'] = 'UNCHANGED'
    except MotherError as exc:
        return {**changed, 'status': 'INCOMPLETE',
                'failed_stage': 'identity_readback_unavailable',
                'identity_error_code': exc.code, 'identity_retry_class': exc.retry_class}
    except (SEHGitError, st.SEHIdentityError, ds.SEHDeploymentStateError, OSError) as exc:
        return {**changed, 'status': 'INCOMPLETE',
                'failed_stage': 'seh_deployment_state_commit_or_trust',
                'error_class': type(exc).__name__}

    # Persist the submission intent BEFORE calling Coolify. A lost POST
    # acknowledgment must not cause a second speculative forced build.
    try:
        receipt = _recovery_checkpoint(state_dir)
        if not receipt or receipt['new_uuid'] != new_uuid:
            raise SEHGitError('Recovery receipt missing before forced build')
        if receipt.get('deployment_uuid'):
            return {**changed, 'status': 'INCOMPLETE',
                    'failed_stage': 'prior_recovery_build_requires_reconciliation'}
        _save_recovery_checkpoint(state_dir, {**receipt, 'build_submission': 'INTENT'})
    except (OSError, SEHGitError) as exc:
        return {**changed, 'status': 'INCOMPLETE',
                'failed_stage': 'build_submission_intent_write', 'error_class': type(exc).__name__}
    try:
        deployment_uuid = _forced_seh_build(remote, new_uuid)
    except SEHGitError as exc:
        return {**changed, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                'failed_stage': 'new_application_forced_build',
                'build_submission_reason': str(exc)}
    changed['deployment_uuid'] = deployment_uuid
    changed['git_revision'] = published['revision']
    # Keep the accepted build UUID as recovery evidence even when Coolify's
    # post-queue readback is temporarily unavailable.
    try:
        receipt = _recovery_checkpoint(state_dir)
        if receipt and receipt.get('new_uuid') == new_uuid:
            _save_recovery_checkpoint(state_dir, {**receipt, 'deployment_uuid': deployment_uuid,
                                                  'build_submission': 'ACKNOWLEDGED'})
    except (OSError, SEHGitError) as exc:
        return {**changed, 'status': 'INCOMPLETE', 'remote_mutation': True,
                'failed_stage': 'deployment_receipt_persist', 'error_class': type(exc).__name__}
    proof = _verify_seh_build_receipt(remote, new_uuid, deployment_uuid,
                                    published['revision'], app_id=app.get('id'))
    if proof['status'] != 'VERIFIED':
        return {**changed, 'status': 'INCOMPLETE', 'remote_mutation': True,
                'failed_stage': 'new_application_build_readback',
                'build_verification': proof['status'],
                **({'build_verification_reason': proof['reason']} if 'reason' in proof else {})}
    if proof['deployment_status'].lower() in ('failed', 'failure', 'error', 'cancelled', 'canceled'):
        return {**changed, 'status': 'INCOMPLETE', 'remote_mutation': True,
                'failed_stage': 'new_application_build_failed',
                'build_verification': 'VERIFIED', 'build_status': proof['deployment_status']}
    return {**changed, 'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
            'remote_mutation': True, 'build_verification': 'VERIFIED',
            'build_status': proof['deployment_status'],
            'outbound_tunnel_authenticated': False}



def restore_deleted_canonical_application(root: Path, network: str, *, execute: bool,
                                          state_dir: Path, client=None, source=None,
                                          repo_root: Path | None = None) -> dict:
    """Serialize Coolify creation and SEH operational-state adoption."""
    if not execute:
        return _restore_deleted_unlocked(root, network, execute=False, state_dir=state_dir,
                                         client=client, source=source, repo_root=repo_root)
    with ds.mutation_lock(root, network):
        return _restore_deleted_unlocked(root, network, execute=True, state_dir=state_dir,
                                         client=client, source=source, repo_root=repo_root)



def recover_canonical_runtime(root: Path, network: str, *, execute: bool = False,
                              client=None, probe=None, state_dir: Path | None = None) -> dict:
    """Recover a lost *runtime* using the existing canonical Coolify UUID.

    Does not create/delete an application, alter trust, mutate Mother, edit
    Docker/Traefik, or call an SLH connector. The resident SLH supervisor is
    responsible for reconnecting when the replacement SEH becomes available.
    """
    from main_computer.slh.seh_canonical_connection import resolve_canonical_connection
    from main_computer.slh.storage import slh_state_dir
    record, controller, generation, digest = _canonical_selection(root, network)
    uuid = record['deployment']['application_uuid']
    from main_computer.slh.organization_state import read_config
    if read_config(state_dir or slh_state_dir()).get('seh_enabled') is not True:
        raise SEHGitError('SLH outbound SEH connection is disabled; refusing runtime recovery')
    remote = client if client is not None else _client(controller)
    settings = resolve_canonical_connection(
        state_dir or slh_state_dir(), mother_root=root, network=network,
        client=remote, require_enabled=True)
    app = _api(remote, 'GET', f'/api/v1/applications/{uuid}')
    if not isinstance(app, dict) or app.get('uuid') != uuid:
        raise SEHGitError('Canonical application UUID disappeared from Coolify; refusing replacement creation')
    place = _canonical_placement(remote, record)
    _verify_application_placement(remote, app, place)
    _verify_canonical_source(app)
    if _verify_canonical_metadata(app) != 'CANONICAL':
        raise SEHGitError('Coolify application metadata is no longer canonical')
    trust = _trust_snapshot(remote, uuid)
    url = 'https://' + settings.seh_url.removeprefix('wss://')
    observe = probe or _public_runtime_probe
    observation = observe(url)
    if not isinstance(observation, dict) or observation.get('status') not in (
            'PUBLIC_RUNTIME_PRESENT', 'PUBLIC_RUNTIME_UNAVAILABLE'):
        raise SEHGitError('Public runtime observation was inconclusive; refusing deployment')
    result = {'network': network, 'application_uuid': uuid,
              'mother_generation': generation, 'remote_mutation': False,
              'mother_mutation': 'UNCHANGED', 'public_url': url,
              'runtime_observation': observation['status'],
              'http_status': observation.get('http_status'),
              'signing_key_trust': 'MATCHED'}
    if observation['status'] == 'PUBLIC_RUNTIME_PRESENT':
        return {**result, 'status': 'RUNTIME_PRESENT',
                'relay_reports_slh_connected': observation.get('slh_connected') is True,
                'resident_tunnel': 'NOT_VERIFIED_BY_RUNTIME_PROBE'}

    # Reconcile a previous recovery's exact forced-build UUID before ever
    # requesting another build. Failed/pending/unknown jobs are not permission
    # for another speculative deployment. A finished job with no live relay
    # is a distinct runtime failure, not proof a fresh build should be queued.
    checkpoint = _recovery_checkpoint(state_dir or slh_state_dir())
    if checkpoint and checkpoint.get('new_uuid') == uuid and checkpoint.get('deployment_uuid'):
        proof = _verify_seh_build_receipt(
            remote, uuid, checkpoint['deployment_uuid'], checkpoint['git_revision'],
            app_id=app.get('id'))
        prior = {**result, 'deployment_uuid': checkpoint['deployment_uuid'],
                 'build_verification': proof['status']}
        if proof['status'] != 'VERIFIED':
            return {**prior, 'status': 'INCOMPLETE',
                    'failed_stage': 'prior_recovery_build_readback'}
        status = proof['deployment_status'].lower()
        if status in ('failed', 'failure', 'error', 'cancelled', 'canceled'):
            return {**prior, 'status': 'INCOMPLETE',
                    'failed_stage': 'prior_recovery_build_failed', 'build_status': status}
        if status in ('finished', 'success', 'succeeded', 'completed', 'complete'):
            # A completed build is not a permanent veto on replacing a later
            # failed container. Require both a missing public relay and an
            # explicitly exited/stopped Coolify runtime before another build.
            current_state = str(app.get('status') or '').lower()
            if not any(word in current_state for word in ('exited', 'stopped')):
                return {**prior, 'status': 'INCOMPLETE',
                        'failed_stage': 'public_runtime_missing_after_completed_build',
                        'build_status': status}
        else:
            return {**prior, 'status': 'DEPLOYMENT_IN_PROGRESS',
                    'replacement_queued': True, 'build_status': status}

    # Coolify application status is not proof of Docker topology, but a
    # transitional status must prevent repeated deploy requests. The proxy
    # can be unavailable while a healthy replacement is still building.
    observed_state = str(app.get('status') or '').lower()
    if any(word in observed_state for word in ('building', 'deploying', 'restarting', 'starting')):
        return {**result, 'status': 'DEPLOYMENT_IN_PROGRESS',
                'replacement_queued': True}
    # Coolify reports running deployments separately from application status.
    # Prevent repeated requests for an already queued/building replacement.
    deployments = _items(_api(remote, 'GET', '/api/v1/deployments'), 'deployments')
    app_id = app.get('id')
    for dep in deployments:
        match_uuid = dep.get('resource_uuid') == uuid or dep.get('application_uuid') == uuid
        match_id = isinstance(app_id, int) and not isinstance(app_id, bool) and dep.get('application_id') == app_id
        if match_uuid or match_id:
            return {**result, 'status': 'DEPLOYMENT_IN_PROGRESS',
                    'replacement_queued': True}
        if not (dep.get('resource_uuid') or dep.get('application_uuid') or
                (isinstance(app_id, int) and 'application_id' in dep)):
            raise SEHGitError('Running Coolify deployment identity is ambiguous; refusing duplicate queue')
    if not execute:
        return {**result, 'status': 'PLAN_ONLY', 'action': 'REDEPLOY_EXISTING_UUID'}

    # Recheck Mother and all protected Coolify fields at the mutation boundary.
    later, _, later_generation, later_digest = _canonical_selection(root, network)
    if (_operational_binding(later) != _operational_binding(record) or
            later['deployment'].get('application_uuid') != uuid):
        raise SEHGitError('SEH controller identity or deployment changed during runtime recovery')
    current = _api(remote, 'GET', f'/api/v1/applications/{uuid}')
    if current != app or _trust_snapshot(remote, uuid) != trust:
        raise SEHGitError('Coolify application or signing trust changed; refusing runtime recovery')
    repeated = observe(url)
    if repeated.get('status') != 'PUBLIC_RUNTIME_UNAVAILABLE':
        return {**result, 'status': 'INCOMPLETE',
                'reason': 'Public runtime changed during recovery; no deployment requested'}

    # Only a forced Git build of the SAME verified UUID. Never start/restart.
    try:
        deployment_uuid = _forced_seh_build(remote, uuid)
    except SEHGitError as exc:
        return {**result, 'status': 'INCOMPLETE', 'remote_mutation': 'UNKNOWN',
                'failed_stage': 'existing_uuid_forced_build',
                'build_submission_reason': str(exc)}
    if checkpoint and checkpoint.get('new_uuid') == uuid:
        # Record the newly accepted build before returning. A subsequent call
        # must reconcile that ID instead of requeueing the old completed one.
        try:
            _save_recovery_checkpoint(state_dir or slh_state_dir(),
                                      {**checkpoint, 'deployment_uuid': deployment_uuid})
        except (SEHGitError, OSError) as exc:
            return {**result, 'status': 'INCOMPLETE', 'remote_mutation': True,
                    'failed_stage': 'runtime_build_receipt_persist',
                    'deployment_uuid': deployment_uuid,
                    'error_class': type(exc).__name__}
    return {**result, 'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
            'remote_mutation': True, 'replacement_queued': True,
            'deployment_uuid': deployment_uuid,
            'outbound_tunnel_authenticated': False}

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



_FAILED_BUILD_STATUSES = frozenset(('failed', 'failure', 'error', 'cancelled', 'canceled'))


def _retry_runtime_blocker(app: dict, prior: dict, phase: str) -> dict | None:
    status = str(app.get('status') or '').strip().lower()
    # Coolify may append container health to the lifecycle state.
    lifecycle = re.split(r'[:(]', status, maxsplit=1)[0].strip()
    if lifecycle in ('exited', 'stopped', 'dead'):
        return None
    active = lifecycle in ('building', 'deploying', 'restarting', 'starting',
                           'running', 'healthy', 'queued')
    return {**prior, 'failed_stage': ('retry_runtime_or_build_active' if active
                                      else 'retry_runtime_state_unverified'),
            'coolify_status': app.get('status'), 'retry_phase': phase}


def _retry_failed_recovery_build(root: Path, network: str, *, state_dir: Path,
                                 checkpoint: dict, record: dict,
                                 remote, app: dict, execute: bool,
                                 source: dict | None = None,
                                 repo_root: Path | None = None) -> dict:
    """Recover a *verified terminal failure*, never an uncertain deploy request.

    A new published source SHA is required because a forced build of the old
    pinned commit would repeat the broken image. Coolify's pin is changed only
    on this receipt-bound application, and an intent is persisted before each
    potentially ambiguous remote action. All retries are serialized by the
    caller's SEH-owned deployment lock.
    """
    from tools.seh_control.dns_probe import domain_hosts
    from tools.slh_seh_connector import service_identity

    uuid = checkpoint['new_uuid']
    old_revision = checkpoint['git_revision']
    old_build = checkpoint['deployment_uuid']
    prior = {'status': 'INCOMPLETE', 'application_uuid': uuid,
             'former_application_uuid': checkpoint['old_uuid'],
             'deployment_uuid': old_build, 'build_status': 'failed',
             'remote_mutation': False}
    if checkpoint.get('build_submission') == 'INTENT':
        return {**prior, 'failed_stage': 'unacknowledged_build_submission',
                'remote_mutation': 'UNKNOWN'}
    try:
        published = source if source is not None else git_source(
            repo_root or Path(__file__).resolve().parents[2])
    except SEHGitPublicationError as exc:
        return {**prior, 'failed_stage': 'seh_git_source_not_published',
                'git_source_reason': str(exc),
                **({'git_repair_command': exc.git_command} if exc.git_command else {})}
    if (not isinstance(published, dict) or
            published.get('repository') != 'https://github.com/johnrraymond/main_computer' or
            published.get('branch') != 'main' or
            published.get('dockerfile_location') != DOCKERFILE or
            not isinstance(published.get('revision'), str) or
            not _SHA.fullmatch(published['revision'])):
        raise SEHGitError('Published retry source has an invalid repository, branch or revision')
    revised = published['revision']
    if revised == old_revision:
        return {**prior, 'failed_stage': 'corrected_source_not_published',
                'git_revision': revised}
    if (app.get('git_branch') != published['branch'] or
            app.get('git_commit_sha') not in (old_revision, revised)):
        return {**prior, 'failed_stage': 'retry_git_pin_unverified'}
    if checkpoint.get('retry_revision') not in (None, revised):
        return {**prior, 'failed_stage': 'retry_published_revision_changed'}
    # If the pin is already new, only our durable, pre-PATCH intent allows
    # adopting it. A remote change without that receipt must not be claimed.
    if app.get('git_commit_sha') == revised and checkpoint.get('retry_revision') != revised:
        return {**prior, 'failed_stage': 'retry_unreceipted_git_pin_change'}
    blocker = _retry_runtime_blocker(app, prior, 'before_pin')
    if blocker:
        return blocker
    # The list endpoint is a deployment inventory, not proof that every item
    # is active. Permit only the *exact* old verified failed job; other builds
    # for this app (or unattributed jobs) prevent a second submission.
    deployments = _items(_api(remote, 'GET', '/api/v1/deployments'), 'deployments')
    app_id = app.get('id')
    for dep in deployments:
        match = (dep.get('resource_uuid') == uuid or
                 dep.get('application_uuid') == uuid or
                 (app_id is not None and dep.get('application_id') in (app_id, str(app_id))))
        if match and (dep.get('deployment_uuid') != old_build or
                      str(dep.get('status') or '').lower() not in _FAILED_BUILD_STATUSES):
            return {**prior, 'failed_stage': 'retry_other_deployment_present'}
        if not (dep.get('resource_uuid') or dep.get('application_uuid') or
                dep.get('application_id')):
            return {**prior, 'failed_stage': 'retry_unattributed_deployment'}
    if not execute:
        return {**prior, 'status': 'PLAN_ONLY', 'action': 'RETRY_FAILED_BUILD_WITH_PUBLISHED_SOURCE',
                'retry_git_revision': revised}
    # The operational UUID, controller identity, placement, source, and trust
    # must remain fixed. No Mother mutation or application creation is needed.
    latest, _, _, _ = _canonical_selection(root, network)
    if (_operational_binding(latest) != _operational_binding(record) or
            latest['deployment']['application_uuid'] != uuid):
        raise SEHGitError('SEH operational identity changed before build retry')
    actual = _registered_application(remote, uuid)
    if actual != app:
        return {**prior, 'failed_stage': 'retry_application_changed'}
    _verify_application_placement(remote, app, _canonical_placement(remote, record))
    _verify_canonical_source(app)
    if (_verify_canonical_metadata(app) != 'CANONICAL' or
            domain_hosts(app) != ['external.greatlibrary.io']):
        raise SEHGitError('Retry Coolify application ownership or domain changed')
    _, public = service_identity(Path(state_dir), create=False)
    public_digest = hashlib.sha256(base64.b64decode(public, validate=True)).hexdigest()
    if checkpoint['key_sha256'] != public_digest:
        raise SEHGitError('Retry signing identity changed; refusing build')
    expected = {'SEH_SLH_PUBLIC_KEY_B64': public}
    before_trust = _trust_snapshot(remote, uuid)
    for key, value in expected.items():
        if _diagnostic_env_value(before_trust, key) != {'state': 'VISIBLE', 'value': value}:
            raise SEHGitError('Retry signing trust changed: ' + key)
    if checkpoint.get('retry_revision') is None:
        # Durable pin-change intent covers lost PATCH acknowledgment. Keep the
        # old deployment receipt until the new forced build is acknowledged.
        _save_recovery_checkpoint(state_dir, {**checkpoint, 'retry_revision': revised})
        checkpoint = _recovery_checkpoint(state_dir)
    if app['git_commit_sha'] == old_revision:
        try:
            _api(remote, 'PATCH', f'/api/v1/applications/{uuid}',
                 {'git_commit_sha': revised})
        except SEHGitError:
            return {**prior, 'failed_stage': 'retry_git_pin_update_unconfirmed',
                    'remote_mutation': 'UNKNOWN'}
        prior['remote_mutation'] = True
    updated = _registered_application(remote, uuid)
    if (updated is None or updated.get('git_commit_sha') != revised or
            any(updated.get(k) != app.get(k) for k in
                ('uuid', 'name', 'description', 'git_repository', 'git_branch',
                 'dockerfile_location', 'domains', 'project_uuid',
                 'environment_uuid', 'server_uuid', 'environment_id'))):
        return {**prior, 'failed_stage': 'retry_git_pin_readback_unverified',
                'remote_mutation': 'UNKNOWN'}
    if _trust_snapshot(remote, uuid) != before_trust:
        return {**prior, 'failed_stage': 'retry_signing_trust_changed',
                'remote_mutation': 'UNKNOWN'}
    blocker = _retry_runtime_blocker(updated, prior, 'after_pin')
    if blocker:
        return blocker
    # A possible lost POST response is never permission to re-POST.
    if checkpoint.get('build_submission') == 'INTENT':
        return {**prior, 'failed_stage': 'unacknowledged_build_submission',
                'remote_mutation': 'UNKNOWN'}
    # PATCHing the Git pin and publication can race a separately queued job.
    # Re-observe inventory at the final POST boundary; never queue alongside
    # any newly discovered deployment, even if it belongs to the same UUID.
    current_deployments = _items(_api(remote, 'GET', '/api/v1/deployments'), 'deployments')
    if current_deployments != deployments:
        return {**prior, 'failed_stage': 'retry_deployment_inventory_changed'}
    _save_recovery_checkpoint(state_dir, {**checkpoint, 'build_submission': 'INTENT'})
    try:
        new_build = _forced_seh_build(remote, uuid)
    except SEHGitError:
        return {**prior, 'failed_stage': 'retry_build_submission_unconfirmed',
                'remote_mutation': 'UNKNOWN'}
    # This is an immutable acknowledgment: the old failed job stays in the
    # receipt's history, and the active receipt now binds to the new SHA.
    history = list(checkpoint.get('previous_failed_builds') or [])
    if not any(item.get('deployment_uuid') == old_build for item in history):
        history.append({'deployment_uuid': old_build, 'git_revision': old_revision})
    try:
        _save_recovery_checkpoint(state_dir, {
            **checkpoint, 'deployment_uuid': new_build, 'git_revision': revised,
            'previous_failed_builds': history,
            'build_submission': 'ACKNOWLEDGED', 'retry_revision': None})
    except (OSError, SEHGitError):
        return {**prior, 'failed_stage': 'retry_build_receipt_persist_failed',
                'remote_mutation': True, 'new_deployment_uuid': new_build}
    proof = _verify_seh_build_receipt(remote, uuid, new_build, revised,
                                     app_id=updated.get('id'))
    result = {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
              'application_uuid': uuid, 'former_application_uuid': checkpoint['old_uuid'],
              'deployment_uuid': new_build, 'git_revision': revised,
              'previous_failed_deployment_uuid': old_build,
              'remote_mutation': True, 'build_verification': proof['status']}
    if proof['status'] != 'VERIFIED':
        return {**result, 'status': 'INCOMPLETE',
                'failed_stage': 'retry_build_readback_unverified'}
    status = proof['deployment_status'].lower()
    if status in _FAILED_BUILD_STATUSES:
        return {**result, 'status': 'INCOMPLETE',
                'failed_stage': 'retry_build_failed', 'build_status': status}
    return {**result, 'build_status': status}


def reconcile_pending_recovery(root: Path, network: str, *, state_dir: Path,
                               execute: bool, client=None, source=None,
                               repo_root: Path | None = None) -> dict | None:
    """Resume a receipt-backed build after SEH UUID CAS, never repeat CREATE.

    A lost build acknowledgment requires operator reconciliation, not another
    speculative POST. A known receipt is always checked before another build.
    """
    from tools.slh_seh_connector import service_identity
    from tools.seh_control.dns_probe import domain_hosts
    checkpoint = _recovery_checkpoint(state_dir)
    if checkpoint is None:
        return None
    with ds.mutation_lock(root, network) if execute else _nullcontext():
        record, controller, generation, _ = _canonical_selection(root, network)
        uuid = record['deployment']['application_uuid']
        if checkpoint['new_uuid'] != uuid:
            # A legacy receipt can describe a replacement that was not yet
            # installed in SEH state. The deleted-app recovery owns that case.
            if checkpoint['old_uuid'] == uuid:
                return None
            raise SEHGitError('SEH receipt conflicts with operational UUID; no build authorized')
        _, public = service_identity(Path(state_dir), create=False)
        public_digest = hashlib.sha256(base64.b64decode(public, validate=True)).hexdigest()
        if checkpoint['key_sha256'] != public_digest:
            raise SEHGitError('SEH recovery signing identity changed; no build authorized')
        remote = client if client is not None else _client(controller)
        app = _registered_application(remote, uuid)
        if app is None:
            raise SEHGitError('Receipt-bound replacement application disappeared')
        _verify_application_placement(remote, app, _canonical_placement(remote, record))
        _verify_canonical_source(app)
        if _verify_canonical_metadata(app) != 'CANONICAL' or domain_hosts(app) != ['external.greatlibrary.io']:
            raise SEHGitError('Receipt-bound application ownership or public URL changed')
        # A receipt-backed failed-build retry may have already PATCHed the
        # pinned SHA before a lost acknowledgment. Only that exact durable
        # retry intent permits the pending revision here.
        allowed_commits = (None, checkpoint['git_revision'], checkpoint.get('retry_revision'))
        if app.get('git_commit_sha') not in allowed_commits:
            raise SEHGitError('Receipt-bound application Git revision changed')
        rows = _trust_snapshot(remote, uuid)
        route = 'slh-' + public_digest[:32]
        for key, value in (('SEH_SLH_PUBLIC_KEY_B64', public),):
            if _diagnostic_env_value(rows, key) != {'state': 'VISIBLE', 'value': value}:
                raise SEHGitError('Receipt-bound signing trust is not verified: ' + key)
        if checkpoint.get('deployment_uuid'):
            proof = _verify_seh_build_receipt(remote, uuid, checkpoint['deployment_uuid'],
                                              checkpoint['git_revision'], app_id=app.get('id'))
            if proof['status'] != 'VERIFIED':
                return {'status': 'INCOMPLETE', 'failed_stage': 'prior_recovery_build_readback',
                        'build_verification': proof['status'], 'application_uuid': uuid}
            if proof['deployment_status'].lower() in _FAILED_BUILD_STATUSES:
                return _retry_failed_recovery_build(
                    root, network, state_dir=state_dir, checkpoint=checkpoint,
                    record=record, remote=remote, app=app,
                    execute=execute, source=source, repo_root=repo_root)
            return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                    'application_uuid': uuid, 'former_application_uuid': checkpoint['old_uuid'],
                    'mother_generation': generation, 'remote_mutation': False,
                    'deployment_uuid': checkpoint['deployment_uuid'],
                    'build_verification': 'VERIFIED', 'build_status': proof['deployment_status']}
        if checkpoint.get('build_submission') == 'INTENT':
            return {'status': 'INCOMPLETE', 'failed_stage': 'unacknowledged_build_submission',
                    'application_uuid': uuid, 'remote_mutation': 'UNKNOWN'}
        if not execute:
            return {'status': 'PLAN_ONLY', 'action': 'RECONCILE_RECOVERY_BUILD',
                    'application_uuid': uuid, 'former_application_uuid': checkpoint['old_uuid'],
                    'mother_generation': generation, 'remote_mutation': False}
        # No acknowledged build UUID. Inventory must show no prior submission.
        # UNKNOWN/malformed inventory is a hard stop; a queue cannot be guessed.
        deployments = _items(_api(remote, 'GET', '/api/v1/deployments'), 'deployments')
        app_id = app.get('id')
        for dep in deployments:
            if (dep.get('resource_uuid') == uuid or dep.get('application_uuid') == uuid or
                (app_id is not None and dep.get('application_id') in (app_id, str(app_id)))):
                return {'status': 'INCOMPLETE', 'failed_stage': 'unacknowledged_prior_build',
                        'application_uuid': uuid, 'remote_mutation': 'UNKNOWN'}
            if not (dep.get('resource_uuid') or dep.get('application_uuid') or dep.get('application_id')):
                return {'status': 'INCOMPLETE', 'failed_stage': 'unattributed_build_inventory',
                        'application_uuid': uuid, 'remote_mutation': 'UNKNOWN'}
        if any(v in str(app.get('status') or '').lower() for v in
               ('building', 'deploying', 'starting', 'running', 'healthy')):
            return {'status': 'INCOMPLETE', 'failed_stage': 'possible_prior_build_active',
                    'application_uuid': uuid, 'remote_mutation': 'UNKNOWN'}
        # Crash-safe intent: never re-POST an unacknowledged submission.
        _save_recovery_checkpoint(state_dir, {**checkpoint, 'build_submission': 'INTENT'})
        try:
            deployment_uuid = _forced_seh_build(remote, uuid)
        except SEHGitError:
            return {'status': 'INCOMPLETE', 'failed_stage': 'new_application_forced_build',
                    'application_uuid': uuid, 'remote_mutation': 'UNKNOWN'}
        _save_recovery_checkpoint(state_dir, {**checkpoint, 'deployment_uuid': deployment_uuid,
                                              'build_submission': 'ACKNOWLEDGED'})
        proof = _verify_seh_build_receipt(remote, uuid, deployment_uuid,
                                          checkpoint['git_revision'], app_id=app.get('id'))
        if proof['status'] != 'VERIFIED':
            return {'status': 'INCOMPLETE', 'failed_stage': 'new_application_build_readback',
                    'application_uuid': uuid, 'deployment_uuid': deployment_uuid,
                    'build_verification': proof['status']}
        return {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED',
                'application_uuid': uuid, 'former_application_uuid': checkpoint['old_uuid'],
                'mother_generation': generation, 'remote_mutation': True,
                'deployment_uuid': deployment_uuid, 'build_verification': 'VERIFIED',
                'build_status': proof['deployment_status']}
