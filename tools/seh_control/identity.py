"""SEH identity ownership in Mother's verified private infrastructure state.

SEH mutations use Mother's recovery-aware compare-and-swap, never direct YAML
writes. Copied controller credentials are private, independently owned values.
This module is reusable by CLI, SLH service, and future SLH admin API.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import urlsplit

from main_computer.slh.config import SEHTarget
from tools.mother.common.errors import MotherError
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    read_private_state, prepare_private_state_successor,
    replace_verified_private_state,
)

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SUPPORTED = ("mainnet", "testnet", "test", "dev")


class SEHIdentityError(ValueError):
    pass


def ident(value: str, name: str) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value) or value in (".", ".."):
        raise SEHIdentityError(f"Invalid {name}")
    return value


def operation(network: str, action: str, operation_id: str) -> OperationIdentity:
    ident(network, 'network')
    ident(operation_id, 'operation_id')
    return OperationIdentity(operation_id=operation_id, request_id=operation_id,
                             network=network, operation_kind="MOTHER-OP-IDENTITY-ROTATION")


def read(root: Path, network: str, action: str = 'read'):
    paths = MotherPaths(runtime_state_root=root).resolve_private_state_paths()
    result = read_private_state(paths, operation=operation(network, action, f'seh-{action}-read'))
    return paths, result, json.loads(result.canonical_object_bytes.decode('utf-8'))


def network_doc(document: dict, network: str) -> dict:
    ident(network, 'network')
    networks = document.get('networks')
    body = networks.get(network) if isinstance(networks, dict) else None
    if not isinstance(body, dict):
        raise SEHIdentityError('Selected network not found in verified infrastructure state')
    return body


def seh_doc(document: dict, network: str, seh: str) -> dict:
    ident(seh, 'seh')
    body = network_doc(document, network)
    record = (body.get('sehs') or {}).get(seh)
    if type(record) is not dict:
        raise SEHIdentityError('Selected SEH is not registered')
    validate_seh(record)
    return record


def validate_seh(record: dict) -> None:
    if type(record) is not dict or record.get('schema') != 'main-computer.seh-identity.v1':
        raise SEHIdentityError('Invalid SEH identity schema')
    ident(record.get('organization_id'), 'organization_id')
    ident(record.get('slh_id'), 'slh_id')
    coolify = record.get('coolify')
    if type(coolify) is not dict or type(coolify.get('identities')) is not dict:
        raise SEHIdentityError('Invalid SEH Coolify identity registry')
    selected = coolify.get('active_identity')
    if selected is not None and selected not in coolify['identities']:
        raise SEHIdentityError('Active Coolify identity is missing')
    for name, entry in coolify['identities'].items():
        ident(name, 'Coolify identity')
        if type(entry) is not dict or type(entry.get('controller')) is not dict:
            raise SEHIdentityError('Invalid copied Coolify identity')
        _validate_controller(entry['controller'])
        if not isinstance(entry.get('copied_from'), str) or type(entry.get('source_generation')) is not int:
            raise SEHIdentityError('Missing copied identity provenance')
    if type(record.get('deployment')) is not dict:
        raise SEHIdentityError('Missing SEH deployment configuration')


def _validate_controller(controller: dict) -> None:
    # FDB's shared-state conventions: url/base_url and api_token/token are supported.
    if not any(isinstance(controller.get(k), str) and controller[k].strip() for k in ('url', 'coolify_url', 'api_url', 'base_url')):
        raise SEHIdentityError('Copied Coolify controller requires URL')
    if not any(isinstance(controller.get(k), str) and controller[k].strip() for k in ('api_token', 'token', 'coolify_token')):
        raise SEHIdentityError('Copied Coolify controller requires resolved token')
    for k in ('project_uuid','server_uuid'):
        if controller.get(k) is not None:
            ident(controller[k], k)


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return '<redacted-url>'
    return value


def sources(document: dict, network: str | None = None) -> list[dict]:
    """Supported, exact source addresses; never include controller values."""
    out = []
    networks = document.get('networks') or {}
    for net, body in sorted(networks.items()):
        if network is not None and net != network:
            continue
        if not isinstance(body, dict):
            continue
        controllers = (body.get('coolify') or {}).get('controllers') or {}
        if isinstance(controllers, dict):
            for name, raw in sorted(controllers.items()):
                if isinstance(raw, dict):
                    out.append({'path':f'networks.{net}.coolify.controllers.{name}',
                                'network':net, 'identity':name, 'kind':'network-controller',
                                'url':_safe_url(next((raw[k] for k in ('url','base_url','coolify_url','api_url') if raw.get(k)), None)),
                                'enabled':raw.get('enabled', True)})
        sehs = body.get('sehs') or {}
        if isinstance(sehs, dict):
            for seh, rec in sorted(sehs.items()):
                entries = ((rec.get('coolify') or {}).get('identities') or {}) if isinstance(rec, dict) else {}
                for name, entry in sorted(entries.items()):
                    controller = entry.get('controller') if isinstance(entry, dict) else None
                    if isinstance(controller, dict):
                        out.append({'path':f'networks.{net}.sehs.{seh}.coolify.identities.{name}',
                                    'network':net,'identity':name,'kind':'seh-owned',
                                    'url':_safe_url(next((controller[k] for k in ('url','base_url','coolify_url','api_url') if controller.get(k)), None)),
                                    'enabled':controller.get('enabled', True)})
    return out


def source_controller(document: dict, path: str) -> dict:
    parts = path.split('.')
    if len(parts) == 5 and parts[0] == 'networks' and parts[2:4] == ['coolify', 'controllers']:
        return deepcopy(network_doc(document, parts[1]).get('coolify', {}).get('controllers', {}).get(parts[4]))
    if len(parts) == 6 and parts[0] == 'networks' and parts[2] == 'sehs' and parts[4] == 'coolify':
        raise SEHIdentityError('Incomplete SEH-owned identity source path')
    if len(parts) == 7 and parts[0] == 'networks' and parts[2] == 'sehs' and parts[4:6] == ['coolify','identities']:
        source = seh_doc(document, parts[1], parts[3])
        entry = source['coolify']['identities'].get(parts[6])
        return deepcopy(entry['controller']) if isinstance(entry, dict) else None
    raise SEHIdentityError('Unsupported Coolify identity source path')


def _materialize_controller(raw: dict, base_dir: Path) -> dict:
    if type(raw) is not dict:
        raise SEHIdentityError('Selected Coolify source does not exist')
    if raw.get('enabled') is False:
        raise SEHIdentityError('Disabled Coolify source cannot be copied')
    controller = deepcopy(raw)
    from tools.fdb_control.common.privates import _resolve_token
    try:
        token, _ = _resolve_token(controller, 'seh-source', base_dir=base_dir)
    except Exception:
        raise SEHIdentityError('Source Coolify token could not be resolved') from None
    if not token:
        raise SEHIdentityError('Source Coolify token is absent')
    for key in ('token','coolify_token','api_token_env','token_env','coolify_token_env',
                'api_token_file','token_file','coolify_token_file'):
        controller.pop(key, None)
    controller['api_token'] = token
    _validate_controller(controller)
    return controller


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def mutate(root: Path, network: str, expected_generation: int | None, operation_id: str,
           transform: Callable[[dict, int, Path], None]) -> dict:
    """Commit against the exact private-state binding observed by this operation.

    Callers need not supply a generation. An optional explicit generation can
    pin an earlier observation. Mother's verified compare-and-swap still
    rejects a concurrent update between the read and commit.
    """
    paths, current, document = read(root, network, 'mutation')
    if expected_generation is not None and (
        type(expected_generation) is not int or current.binding.generation != expected_generation
    ):
        raise SEHIdentityError('Private-state generation conflict; re-read and re-approve')
    successor = deepcopy(document)
    transform(successor, current.binding.generation, paths.identity_file.parent)
    if successor == document:
        return {'status':'UNCHANGED','generation':current.binding.generation}
    op = operation(network, 'mutation', operation_id)
    closure = prepare_private_state_successor(current, successor,
        updated_at=_now(), updated_by_action_id=operation_id, operation=op)
    try:
        install = replace_verified_private_state(paths, closure, current.binding, operation=op)
    except MotherError as exc:
        if exc.code == 'MOTHER_STATE_PRIVATE_STATE_CONFLICT':
            raise SEHIdentityError('Private state changed concurrently; retry the command') from None
        raise
    if not install.installed:
        raise SEHIdentityError('Verified SEH successor was not installed')
    verified = read_private_state(paths, operation=op)
    if verified.binding != closure.binding:
        raise SEHIdentityError('SEH state successor could not be verified')
    return {'status':'COMMITTED', 'generation':verified.binding.generation,
            'content_sha256':verified.binding.content_hash.digest}


def reserve(document: dict, network: str, seh: str, organization_id: str, slh_id: str) -> None:
    ident(seh,'seh');ident(organization_id,'organization_id');ident(slh_id,'slh_id')
    body = network_doc(document, network)
    records = body.setdefault('sehs', {})
    if type(records) is not dict or seh in records:
        raise SEHIdentityError('SEH identity already exists or registry is invalid')
    records[seh] = {'schema':'main-computer.seh-identity.v1', 'organization_id':organization_id,
                    'slh_id':slh_id, 'coolify':{'active_identity':None,'identities':{}},
                    'deployment':{'enabled':False}}


def copy_identity(document: dict, network: str, seh: str, alias: str, source: str,
                  generation: int, base_dir: Path, replace: bool = False) -> None:
    ident(alias,'identity alias')
    dest = seh_doc(document, network, seh)['coolify']
    entries = dest['identities']
    if alias in entries and not replace:
        raise SEHIdentityError('Coolify identity exists; explicit --replace required')
    controller = _materialize_controller(source_controller(document, source), base_dir)
    entries[alias] = {'controller': controller, 'copied_from':source,
                      'source_generation':generation, 'copied_at':_now()}
    if dest['active_identity'] is None:
        dest['active_identity'] = alias
    validate_seh(seh_doc(document, network, seh))


def select_identity(document: dict, network: str, seh: str, alias: str) -> None:
    entry = seh_doc(document,network,seh)
    if alias not in entry['coolify']['identities']:
        raise SEHIdentityError('Coolify identity not registered')
    entry['coolify']['active_identity'] = alias


def configure(document: dict, network: str, seh: str, changes: dict) -> None:
    record = seh_doc(document,network,seh)
    permitted = {'public_url','image','image_digest','environment_uuid','project_uuid',
                 'server_uuid','slh_public_key_b64','internal_port','local_lab','enabled'}
    if set(changes)-permitted:
        raise SEHIdentityError('Unsupported deployment field')
    for k,v in changes.items():
        record['deployment'][k] = v
    # Enabling requires complete, validated configuration, not merely a flag.
    if record['deployment'].get('enabled'):
        resolve(document, network, seh)


@dataclass(frozen=True, repr=False)
class ResolvedSEH:
    target: SEHTarget
    token: str
    generation: int
    identity_name: str

    def __repr__(self):
        return f'ResolvedSEH(target_id={self.target.target_id!r}, identity_name={self.identity_name!r}, generation={self.generation}, token=<redacted>)'


def resolve(document: dict, network: str, seh: str, generation: int = 0) -> ResolvedSEH:
    record = seh_doc(document, network, seh)
    selected = record['coolify']['active_identity']
    if not selected:
        raise SEHIdentityError('SEH has no active Coolify identity')
    ctl = record['coolify']['identities'][selected]['controller']
    if ctl.get('enabled') is False:
        raise SEHIdentityError('Active Coolify controller is disabled')
    opts = record['deployment']
    if not opts.get('enabled'):
        raise SEHIdentityError('SEH deployment is not enabled')
    data = dict(opts)
    for key in ('enabled',):
        data.pop(key, None)
    def present(key):
        return next((ctl[k] for k in (key,) if ctl.get(k)),None)
    data.update({'target_id':seh, 'organization_id':record['organization_id'],
                 'slh_id':record['slh_id'],
                 'coolify_url':next((ctl[k] for k in ('url','coolify_url','api_url','base_url') if ctl.get(k)),None),
                 'project_uuid':data.get('project_uuid') or present('project_uuid'),
                 'server_uuid':data.get('server_uuid') or present('server_uuid')})
    # Avoid changing schema: SEHTarget still has no secret field.
    target = SEHTarget.from_dict(data)
    return ResolvedSEH(target, ctl['api_token'], generation, selected)


def public_inspect(document: dict, network: str, seh: str) -> dict:
    entry = seh_doc(document,network,seh)
    coolify = entry['coolify']
    return {'schema':entry['schema'], 'seh':seh,'network':network,
            'organization_id':entry['organization_id'], 'slh_id':entry['slh_id'],
            'active_identity':coolify['active_identity'],
            'identities':{name:{'copied_from':value['copied_from'],
                                'source_generation':value['source_generation'],
                                'url':_safe_url(next((value['controller'][k] for k in ('url','base_url','coolify_url','api_url') if value['controller'].get(k)), None)),
                                'has_token':True}
                          for name,value in coolify['identities'].items()},
            'deployment':deepcopy(entry['deployment'])}
