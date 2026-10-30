"""Read-only SEH deployment discovery from verified identity and Coolify inventory.

Never invent registry artifacts, forward tokens to public HTTP, or write Mother
state. A Docker image ID is NOT an externally pullable registry manifest digest.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import re
from pathlib import Path
from urllib.parse import urlsplit

from main_computer.slh.coolify import CoolifyHTTP, SEHDeploymentError
from . import identity as st

DEFAULT_PUBLIC_URL = 'https://external.greatlibrary.io'
_DIGESTED_IMAGE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9./:_-]*)@(sha256:[a-f0-9]{64})$')


def _entries(payload, name):
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, dict)]
    if isinstance(payload, dict):
        value = payload.get(name)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, dict)]
    raise st.SEHIdentityError(f'Coolify returned invalid {name} inventory')


def _published_image(image: object) -> tuple[str, str] | None:
    if not isinstance(image, str):
        return None
    matched = _DIGESTED_IMAGE.fullmatch(image.strip().strip("'\""))
    if not matched:
        return None
    repo, digest = matched.groups()
    first = repo.split('/')[0].lower()
    # A local image ID, an unqualified tag, or a non-published local image is
    # not evidence of remotely retrievable registry content.
    if not ('.' in first or ':' in first or first == 'localhost'):
        return None
    if first in ('localhost', '127.0.0.1') or first.endswith('.invalid') or first.endswith('.example'):
        return None
    if 'seh' not in repo.lower() or digest == 'sha256:' + '0'*64:
        return None
    return repo, digest


def _from_compose(service: dict) -> tuple[str, str] | None:
    for key in ('image', 'container_image'):
        ref = _published_image(service.get(key))
        if ref:
            return ref
    for field in ('docker_compose', 'compose', 'docker_compose_raw'):
        raw = service.get(field)
        if not isinstance(raw, str):
            continue
        if field == 'docker_compose_raw' and not raw.lstrip().startswith(('services:', 'version:')):
            try:
                raw = base64.b64decode(raw, validate=True).decode('utf-8')
            except (ValueError, UnicodeError):
                continue
        for line in raw.splitlines():
            # Only consider explicit pinned image declarations. Never accept a
            # naked docker image ID or a mutable :latest tag as a digest.
            match = re.fullmatch(r'\s*image:\s*[\'\"]?([^\s\'\"]+)[\'\"]?\s*(?:#.*)?', line)
            if match:
                ref = _published_image(match.group(1))
                if ref:
                    return ref
    return None


def _trusted_origin(origin: str) -> bool:
    parsed = urlsplit(origin)
    if parsed.hostname and (parsed.hostname.endswith(('.example.org', '.example.com', '.invalid', '.test'))):
        return False
    return parsed.scheme == 'https' or (
        parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')
    )


def _available_published_images(document: dict, network: str) -> set[tuple[str, str]]:
    images = set()
    for record in (st.network_doc(document, network).get('sehs') or {}).values():
        if not isinstance(record, dict):
            continue
        deployment = record.get('deployment') or {}
        if isinstance(deployment, dict):
            image, digest = deployment.get('image'), deployment.get('image_digest')
            ref = _published_image(f'{image}@{digest}')
            if ref:
                images.add(ref)
    return images


def discover(root: Path, network: str, seh: str, *, existing: dict | None = None,
             transport=None) -> dict:
    """Find nonsecret draft fields; return reasons when remote discovery is blocked.

    May call the SELECTED controller's read-only GET API. Never uses an untrusted
    origin for an API token, and never creates or changes a remote resource.
    """
    _, state, document = st.read(root, network)
    record = st.seh_doc(document, network, seh)
    alias = record['coolify']['active_identity']
    if not alias:
        raise st.SEHIdentityError('Copy or select a Coolify identity before discovering deployment')
    controller = record['coolify']['identities'][alias]['controller']
    deployment = record['deployment']
    if existing is not None and (type(existing) is not dict or set(existing) - {
        'public_url', 'image', 'image_digest', 'environment_uuid',
        'slh_public_key_b64', 'project_uuid', 'server_uuid',
        'enabled', 'internal_port', 'local_lab', 'service_uuid',
    }):
        raise st.SEHIdentityError('Unsupported deployment fields in draft (secrets forbidden)')
    result = deepcopy(existing or {})
    source = {}
    blockers = []

    def fill(name, value, where):
        if not result.get(name) and isinstance(value, str) and value.strip():
            result[name] = value.strip()
            source[name] = where

    fill('public_url', DEFAULT_PUBLIC_URL, 'default-hostname-unverified-dns')
    for field in ('project_uuid', 'server_uuid', 'environment_uuid'):
        fill(field, deployment.get(field), 'verified-seh-state')
        fill(field, controller.get(field), 'copied-coolify-identity')
    for field in ('image', 'image_digest', 'slh_public_key_b64'):
        fill(field, deployment.get(field), 'verified-seh-state')
    if not result.get('slh_public_key_b64'):
        try:
            from tools.slh_seh_connector import default_state, service_identity
            _, public = service_identity(default_state(), create=False)
            fill('slh_public_key_b64', public, 'existing-local-slh-public-identity')
        except (FileNotFoundError, OSError, ValueError):
            pass
    result.setdefault('enabled', True)

    images = _available_published_images(document, network)
    # Some existing controller identities already carry an approved artifact.
    configured_image = _published_image(
        f"{controller.get('image')}@{controller.get('image_digest')}")
    if configured_image:
        images.add(configured_image)
    wants_env = not result.get('environment_uuid')
    wants_image = not (result.get('image') and result.get('image_digest'))
    # All remote inventory comes from the selected controller. A direct public
    # HTTP management URL would expose its bearer token in transit.
    origin = next((controller[k] for k in ('url', 'coolify_url', 'api_url', 'base_url')
                   if controller.get(k)), '')
    if (wants_env or wants_image) and not _trusted_origin(origin):
        blockers.append('COOLIFY_MANAGEMENT_TRANSPORT_INSECURE: configure an HTTPS management origin or authenticated local tunnel before credentialed discovery')
    elif wants_env or wants_image:
        try:
            client = transport if transport is not None else CoolifyHTTP(origin, controller['api_token'])
            if wants_env:
                project = result.get('project_uuid')
                if not project:
                    blockers.append('COOLIFY_PROJECT_UUID_MISSING')
                else:
                    envs = _entries(client.request('GET', f'/api/v1/projects/{project}/environments'), 'environments')
                    preferred = controller.get('environment_name') or deployment.get('environment_name')
                    candidates = [e for e in envs if isinstance(e.get('uuid'), str) and e['uuid']]
                    if preferred:
                        candidates = [e for e in candidates if e.get('name') == preferred]
                    if len(candidates) == 1:
                        fill('environment_uuid', candidates[0]['uuid'], 'coolify-project-environment-inventory')
                    elif len(candidates) == 0:
                        blockers.append('COOLIFY_ENVIRONMENT_NOT_FOUND')
                    else:
                        blockers.append('COOLIFY_ENVIRONMENT_AMBIGUOUS: multiple environments; select one explicitly')
            if wants_image:
                services = _entries(client.request('GET', '/api/v1/services'), 'services')
                for service in services:
                    description = str(service.get('description') or '')
                    # Only actual MC SEH services are acceptable provenance.
                    if not description.startswith('main-computer-seh:v1 '):
                        continue
                    ref = _from_compose(service)
                    if not ref and service.get('uuid'):
                        detail = client.request('GET', '/api/v1/services/' + str(service['uuid']))
                        if isinstance(detail, dict):
                            ref = _from_compose(detail)
                    if ref:
                        images.add(ref)
        except (SEHDeploymentError, st.SEHIdentityError, OSError, ValueError) as exc:
            blockers.append(f'COOLIFY_DISCOVERY_UNAVAILABLE: {type(exc).__name__}')
    if wants_image and not (result.get('image') and result.get('image_digest')):
        if len(images) == 1:
            image, digest = next(iter(images))
            fill('image', image, 'verified-seh-registry-reference')
            fill('image_digest', digest, 'verified-seh-registry-reference')
        elif len(images) > 1:
            blockers.append('SEH_IMAGE_AMBIGUOUS: multiple published digest references; select one')
        else:
            blockers.append('SEH_PUBLISHED_IMAGE_NOT_FOUND: local Docker builds are not remote registry artifacts')

    required = ('public_url', 'image', 'image_digest', 'environment_uuid',
                'slh_public_key_b64', 'project_uuid', 'server_uuid')
    missing = [key for key in required if not isinstance(result.get(key), str) or not result[key].strip()]
    return {'status': 'DISCOVERED_READ_ONLY', 'network': network, 'seh': seh,
            'state_generation': state.binding.generation, 'values': result,
            'discovered_fields': source, 'missing_fields': missing,
            'blockers': blockers, 'remote_mutation': False, 'identity_committed': False,
            'public_dns_verified': False, 'public_tls_verified': False}
