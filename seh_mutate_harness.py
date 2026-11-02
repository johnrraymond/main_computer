#!/usr/bin/env python3
"""Operator-level SEH Coolify deployment harness.

Like mother_mutate_harness.py, fdb_mutate_harness.py, and hub_mutate_harness.py,
operators select a logical target and mutation intent. The harness orchestrates
existing SEH Control stages; it never manages Coolify API tokens itself.

Examples:
    python seh_mutate_harness.py list --network mainnet
    python seh_mutate_harness.py inspect --network mainnet --seh company-primary
    python seh_mutate_harness.py deploy-seh --network mainnet --seh company-primary \
        --host coolify-c --execute-mutations --yes-i-know-this-mutates-seh

    Existing deployments may omit --public-url only when the registered
    application has a single verifiable public HTTPS origin. New SEHs must
    supply --public-url and their organization/SLH identifiers.

A Coolify deploy trigger is QUEUED, not proof of public HTTPS/WSS readiness.
No mutation occurs without both approval switches.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

from tools.seh_control.commands import SEHControl
from tools.seh_control.identity import SEHIdentityError, ident
from tools.seh_control.git_deployment import SEHGitError


class HarnessError(ValueError):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('operation', choices=('list', 'inspect', 'deploy-seh'))
    p.add_argument('--network', choices=('mainnet', 'testnet', 'test', 'dev'), default='mainnet')
    p.add_argument('--seh', help='Logical SEH identity')
    p.add_argument('--host', help='Verified Coolify controller; optional for an existing active SEH identity')
    p.add_argument('--organization-id', help='Required only to register a new SEH')
    p.add_argument('--slh-id', help='Required only to register a new SEH')
    p.add_argument('--public-url', help='Exact HTTPS origin; optional only if discoverable from an existing SEH')
    p.add_argument('--project-name', default='My first project')
    p.add_argument('--environment-name', help='Defaults to <network>-hubs')
    p.add_argument('--runtime-state-root', type=Path, default=Path('runtime/state'))
    p.add_argument('--report', type=Path, help='Optional JSON receipt path')
    p.add_argument('--execute-mutations', action='store_true')
    p.add_argument('--yes-i-know-this-mutates-seh', action='store_true')
    return p


def _origin(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or
            parts.port or parts.path not in ('', '/') or parts.query or parts.fragment or
            parts.hostname.lower() in ('localhost', '127.0.0.1')):
        raise HarnessError('--public-url must be a public hostname-only HTTPS origin')
    return value.rstrip('/')


def _existing(control: SEHControl, network: str, seh: str) -> dict | None:
    result = control.list(network)
    rows = result.get('sehs')
    if not isinstance(rows, list):
        raise HarnessError('SEH registry did not return a valid identity list')
    return next((row for row in rows if row.get('seh') == seh), None)


def _record_step(events: list[dict], stage: str, status: str, detail: dict | None = None) -> None:
    item = {'stage': stage, 'status': status}
    if detail:
        # Only selected public evidence is allowed in the operator receipt.
        for key in ('generation', 'plan_digest', 'application_uuid', 'public_url', 'coolify_status',
                    'dns_ready', 'ready', 'remote_mutation', 'created', 'git_revision'):
            if key in detail:
                item[key] = detail[key]
    events.append(item)


def _verified_slh_public_key(*, organization_id: str, slh_id: str) -> str:
    """Use/create ONLY this host's existing, matching SLH signing identity.

    A Mother SEH registration is not authority to bootstrap a separate SLH on
    an operator's workstation. The SLH's machine-owned state must exist and
    contain the registered organization before a signing key may be created.
    """
    from main_computer.slh.seh_autoconnect import read_seh_settings
    from main_computer.slh.storage import (guard_legacy_state,
                                           secure_machine_state_dir)
    from tools.slh_seh_connector import default_state, service_identity

    directory = Path(default_state())
    if directory.is_symlink():
        raise HarnessError('SLH state directory is a symlink; refusing identity provisioning')
    try:
        guard_legacy_state(directory)
    except RuntimeError as exc:
        if str(exc).startswith('LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION:'):
            raise HarnessError(
                'LEGACY_SLH_STATE_REQUIRES_OFFLINE_MIGRATION: The existing SLH '
                'identity is stored outside runtime/state/slh. Stop running '
                'SLH processes, run `python -m tools.slh_storage_migrate plan`, '
                'then run '
                '`python -m tools.slh_storage_migrate slh --execute`. '
                'Preserve the source directory; the migration refuses to '
                'overwrite an occupied destination. Retry deploy-seh only '
                'after the migration succeeds.'
            ) from None
        raise HarnessError('SLH storage guard failed: RuntimeError') from None
    database = directory / 'organization.sqlite3'
    if not database.is_file() or database.is_symlink():
        raise HarnessError('SLH public relay identity missing or unverified: no initialized '
                           'SLH authority on this machine. Run deployment from the actual '
                           'organization SLH host; do not generate a relay key on this operator host.')
    try:
        # Read-only: do not construct SLHAuthority, which can create a second
        # organization and bootstrap secret when state is absent.
        uri = 'file:' + quote(database.resolve().as_posix(), safe='/:') + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            row = conn.execute('SELECT org_id FROM organization LIMIT 1').fetchone()
    except sqlite3.Error:
        raise HarnessError('Existing local SLH authority could not be verified read-only') from None
    if row is None or row[0] != organization_id:
        raise HarnessError('Local SLH organization does not match registered SEH organization; '
                           'refusing to create or use an unrelated signing identity')
    try:
        settings = read_seh_settings(directory)
    except (OSError, ValueError, TypeError):
        raise HarnessError('Local SLH SEH connection settings are invalid') from None
    if settings is not None and (settings.organization_id, settings.slh_id) != (organization_id, slh_id):
        raise HarnessError('Local SLH SEH connection identity differs from the registered SEH')
    # Correctly harden ProgramData on Windows before creating private material.
    # This is the same protection used by the resident SLH Local Service.
    try:
        secure_machine_state_dir(directory)
        key_file = directory / 'seh-service-signing-key.pem'
        if key_file.is_symlink():
            raise HarnessError('SLH signing key is a symlink; refusing identity provisioning')
        _, public_key = service_identity(directory, create=True)
    except (OSError, PermissionError, ValueError) as exc:
        raise HarnessError('SLH relay identity could not be verified or securely created: '
                           + type(exc).__name__) from None
    return public_key


def run(args: argparse.Namespace, *, control: SEHControl | None = None,
        slh_public_key_provider=None) -> dict:
    control = control or SEHControl(args.runtime_state_root)
    network = args.network
    if args.operation == 'list':
        return control.list(network)
    if not args.seh:
        raise HarnessError('--seh is required')
    seh = ident(args.seh, 'seh')
    if args.operation == 'inspect':
        return control.inspect(network, seh)
    existing = _existing(control, network, seh)
    active = (existing or {}).get('active_identity')
    identities = (existing or {}).get('identities') or {}
    active_source = (identities.get(active) or {}).get('copied_from')
    # Existing SEH identities own a copy of their Coolify controller. The
    # alias may be "primary"; it is not the Coolify controller name.
    if args.host:
        host = ident(args.host, 'Coolify controller')
    elif existing and isinstance(active_source, str) and active_source.startswith(
            f'networks.{network}.coolify.controllers.'):
        host = active_source.rsplit('.', 1)[-1]
    else:
        raise HarnessError('--host is required when no existing active Coolify controller can be verified')
    if not host.startswith('coolify-'):
        raise HarnessError('--host must name an existing coolify-* controller')
    source = f'networks.{network}.coolify.controllers.{host}'
    if existing:
        for field, selected in (('organization_id', args.organization_id), ('slh_id', args.slh_id)):
            if selected and selected != existing.get(field):
                raise HarnessError(f'Existing SEH {field} differs from explicit request')
        deployment = existing.get('deployment') or {}
        if active_source != source and (deployment.get('application_uuid') or deployment.get('service_uuid')):
            raise HarnessError('Deployed SEH belongs to another Coolify controller; use a new SEH identity')
        if host in identities and identities[host].get('copied_from') != source:
            raise HarnessError('Controller alias collision; refusing to replace existing Coolify identity')
        if not active_source and host not in identities and (deployment.get('application_uuid') or
                                                             deployment.get('service_uuid')):
            raise HarnessError('Deployed SEH has no verified controller binding; recovery is required')
    else:
        if not args.organization_id or not args.slh_id:
            raise HarnessError('New SEH requires --organization-id and --slh-id')
        ident(args.organization_id, 'organization_id')
        ident(args.slh_id, 'slh_id')
    prior_url = ((existing or {}).get('deployment') or {}).get('public_url')
    if args.public_url:
        public_url = _origin(args.public_url)
        if prior_url and _origin(prior_url) != public_url:
            raise HarnessError('Existing SEH public URL differs; deploy-seh will not silently retarget it')
    elif prior_url:
        public_url = _origin(prior_url)
    else:
        public_url = None
    if not public_url and not existing:
        raise HarnessError('New SEH requires --public-url; no hostname is assumed')

    if not (args.execute_mutations and args.yes_i_know_this_mutates_seh):
        return {'status': 'PLAN_ONLY', 'network': network, 'seh': seh, 'controller': host,
                'public_url': public_url, 'registered': existing is not None,
                'public_url_discovery': ('REQUIRED_ON_EXECUTION' if public_url is None else 'NOT_REQUIRED'),
                'remote_mutation': False, 'reason': 'Both --execute-mutations and '
                '--yes-i-know-this-mutates-seh are required for all changes'}

    events: list[dict] = []
    op = 'seh-harness-' + uuid.uuid4().hex[:18]
    result: dict = {'status': 'INCOMPLETE', 'network': network, 'seh': seh,
                    'controller': host, 'public_url': public_url, 'operation_id': op,
                    'steps': events, 'public_wss_verified': False, 'ready': False}
    stage = 'public-url'
    try:
        if public_url is None:
            # Only trust an existing registered application on the selected
            # controller. Coolify must expose one unambiguous HTTPS origin;
            # neither a localhost URL nor DEFAULT_PUBLIC_URL is an approval.
            registered_uuid = (existing.get('deployment') or {}).get('application_uuid')
            if not registered_uuid:
                raise HarnessError('Existing SEH has no application UUID or approved public URL; pass --public-url')
            observed = control.observed_public_origin(network, seh)
            if observed.get('application_uuid') != registered_uuid:
                raise HarnessError('Observed Coolify application UUID differs from registered SEH identity')
            candidate = observed.get('public_url')
            if not candidate:
                raise HarnessError('Coolify did not reveal a single approved public HTTPS hostname; pass --public-url')
            public_url = _origin(candidate)
            result['public_url'] = public_url
            _record_step(events, 'public-url-discovery', 'COOLIFY_APPLICATION_DOMAIN_OBSERVED', observed)
        stage = 'registration'
        if not existing:
            receipt = control.change('reserve', network, seh, None, op + '-reserve',
                                     organization_id=args.organization_id, slh_id=args.slh_id)
            _record_step(events, 'reserve', 'COMPLETED', receipt)
            existing = _existing(control, network, seh)
            if not existing:
                raise HarnessError('Reserved SEH identity not observable in verified state')
        else:
            _record_step(events, 'reserve', 'ALREADY_REGISTERED')
        stage = 'controller'
        identities = existing.get('identities') or {}
        active = existing.get('active_identity')
        active_source = (identities.get(active) or {}).get('copied_from')
        selected_alias = active if active_source == source else host
        if selected_alias not in identities:
            receipt = control.change('coolify-copy', network, seh, None, op + '-copy',
                                     alias=host, source=source)
            _record_step(events, 'coolify-copy', 'COMPLETED', receipt)
            existing = _existing(control, network, seh)
        else:
            _record_step(events, 'coolify-copy', 'EXISTING_VERIFIED')
        if existing.get('active_identity') != selected_alias:
            receipt = control.change('coolify-select', network, seh, None, op + '-select', alias=selected_alias)
            _record_step(events, 'coolify-select', 'COMPLETED', receipt)
        else:
            _record_step(events, 'coolify-select', 'ALREADY_SELECTED')
        stage = 'slh-identity'
        provider = slh_public_key_provider or _verified_slh_public_key
        public_key = provider(organization_id=existing['organization_id'], slh_id=existing['slh_id'])
        _record_step(events, 'slh-identity', 'LOCAL_AUTHORITY_MATCHED')
        stage = 'prepare'
        prepared = control.prepare_git(network, seh, project_name=args.project_name,
                                       environment_name=args.environment_name, public_url=public_url,
                                       public_key=public_key)
        if prepared.get('status') != 'PREPARED':
            raise HarnessError('SEH Git deployment did not produce a prepared plan')
        _record_step(events, 'prepare', 'COMPLETED', prepared)
        stage = 'preflight'
        preflight = control.preflight_git(network, seh)
        if preflight.get('status') not in ('CREATE_ELIGIBLE', 'EXISTING_APPLICATION_VERIFIED'):
            raise HarnessError('Preflight does not authorize deployment')
        if preflight.get('plan_digest') != prepared.get('plan_digest'):
            raise HarnessError('Preflight plan digest differs from prepared identity')
        _record_step(events, 'preflight', preflight['status'], preflight)
        stage = 'deploy'
        deployed = control.deploy_git(network, seh, operation_id=op + '-deploy')
        _record_step(events, 'deploy', deployed.get('status', 'UNKNOWN'), deployed)
        if (deployed.get('status') != 'DEPLOY_QUEUED' or not deployed.get('application_uuid') or
                deployed.get('plan_digest') != prepared.get('plan_digest')):
            raise HarnessError('Remote deploy receipt was incomplete or inconsistent')
        result['application_uuid'] = deployed['application_uuid']
        stage = 'status'
        observed = control.status_git(network, seh)
        _record_step(events, 'status', observed.get('status', 'UNKNOWN'), observed)
        if observed.get('application_uuid') != deployed['application_uuid']:
            raise HarnessError('Post-deploy observed application does not match the receipt')
        result.update(status='DEPLOY_QUEUED_NOT_VERIFIED',
                      public_wss_verified=False, ready=False,
                      coolify_status=observed.get('coolify_status'),
                      dns_ready=bool(observed.get('dns_ready')))
    except (SEHIdentityError, SEHGitError, HarnessError, OSError, ValueError) as exc:
        # Exception descriptions from SEH adapters are constrained; never print private records.
        _record_step(events, stage, 'FAILED')
        result.update(status='REFUSED', failed_stage=stage, error_type=type(exc).__name__,
                      reason=str(exc) if isinstance(exc, (SEHIdentityError, SEHGitError, HarnessError))
                      else type(exc).__name__)
        if isinstance(exc, SEHGitError) and exc.git_command:
            result['git_repair_command'] = exc.git_command
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run(args)
    except (HarnessError, SEHIdentityError, SEHGitError, OSError, ValueError) as exc:
        result = {'status': 'REFUSED', 'error_type': type(exc).__name__,
                  'reason': str(exc) if isinstance(exc, (HarnessError, SEHIdentityError, SEHGitError))
                  else type(exc).__name__}
        if isinstance(exc, SEHGitError) and exc.git_command:
            result['git_repair_command'] = exc.git_command
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result.get('status') in ('REFUSED', 'INCOMPLETE') else 0


if __name__ == '__main__':
    raise SystemExit(main())
