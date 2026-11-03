#!/usr/bin/env python3
"""Operator-level SEH Coolify deployment harness.

Like mother_mutate_harness.py, fdb_mutate_harness.py, and hub_mutate_harness.py,
operators select a logical target and mutation intent. The harness orchestrates
existing SEH Control stages; it never manages Coolify API tokens itself.

Examples:
    python seh_mutate_harness.py list --network mainnet
    python seh_mutate_harness.py inspect
    python seh_mutate_harness.py diagnostic
    python seh_mutate_harness.py deploy-seh --network mainnet --seh company-primary \
        --host coolify-c --execute-mutations --yes-i-know-this-mutates-seh

    This retained command provisions SEH infrastructure ONLY. It is not the
    SLH-owned provisioning lifecycle: it neither configures the resident SLH
    connection supervisor nor verifies an authenticated tunnel. New relays
    remain unbound. Do not treat a queued deployment as operational readiness.

    New SEHs use https://external.greatlibrary.io by default. An already
    registered SEH retains its verified public origin and Coolify identity.

A Coolify deploy trigger is QUEUED, not proof of public HTTPS/WSS readiness.
No mutation occurs without both approval switches.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from tools.seh_control.commands import SEHControl
from tools.seh_control.identity import SEHIdentityError, ident
from tools.seh_control.git_deployment import DEFAULT_PUBLIC_URL, SEHGitError


class HarnessError(ValueError):
    pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('operation', choices=('list', 'inspect', 'migrate-state', 'reconcile-metadata', 'deploy-seh', 'bind-slh', 'diagnostic'))
    p.add_argument('--approve-generation', type=int, help='Exact Mother generation for local migration')
    p.add_argument('--network', choices=('mainnet', 'testnet', 'test', 'dev'), default='mainnet')
    p.add_argument('--seh', help='Legacy SEH selection (mutation operations only; never inspect/diagnostic)')
    p.add_argument('--host', help='Verified Coolify controller; optional for an existing active SEH identity')
    p.add_argument('--slh-id', help='Optional SLH association metadata; not required for deployment')
    p.add_argument('--public-url', help='Public HTTPS origin; defaults for a new SEH only')
    p.add_argument('--project-name', default='My first project')
    p.add_argument('--environment-name', help='Defaults to <network>-hubs')
    p.add_argument('--runtime-state-root', type=Path, default=Path('runtime/state'))
    p.add_argument('--report', type=Path, help='Optional JSON receipt path')
    p.add_argument('--replace-existing-trust', action='store_true',
                   help='Explicitly authorize replacing the key already trusted by this SEH')
    p.add_argument('--slh-state-dir', type=Path,
                   help='Location of the existing local SLH signing key; never creates one')
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


def _single_read_only_target(control: SEHControl, network: str) -> tuple[dict | None, dict]:
    """Resolve zero or one *legacy* SEH from a verified, read-only Mother view.

    Do not choose a name, pick the first item, read keys, or mutate private
    state. The named registry is only a migration input until Patch 2.
    """
    listing = control.list(network)
    if listing.get('status') == 'READ_ONLY' and 'deployment' in listing:
        canonical = listing['deployment']
        if not isinstance(canonical, dict):
            raise HarnessError('Invalid canonical deployment')
        return canonical, listing
    rows = listing.get('sehs')
    if listing.get('status') != 'READ_ONLY' or not isinstance(rows, list):
        raise HarnessError('Verified SEH registry inspection was unavailable')
    if len(rows) > 1:
        raise HarnessError('Multiple legacy SEH records exist; refuse automatic selection until reconciled')
    if rows and (not isinstance(rows[0], dict) or not isinstance(rows[0].get('seh'), str)):
        raise HarnessError('Legacy SEH registration is malformed; refusing selection')
    return (rows[0] if rows else None), listing


def _record_step(events: list[dict], stage: str, status: str, detail: dict | None = None) -> None:
    item = {'stage': stage, 'status': status}
    if detail:
        # Only selected public evidence is allowed in the operator receipt.
        for key in ('generation', 'plan_digest', 'application_uuid', 'public_url', 'coolify_status',
                    'dns_ready', 'ready', 'remote_mutation', 'created', 'git_revision'):
            if key in detail:
                item[key] = detail[key]
    events.append(item)



def run(args: argparse.Namespace, *, control: SEHControl | None = None,
        slh_public_key_provider=None) -> dict:
    control = control or SEHControl(args.runtime_state_root)
    network = args.network
    if args.operation == 'list':
        return control.list(network)
    if args.operation == 'reconcile-metadata':
        # Patch 3 is a UUID-bound metadata reconciliation, not an SEH deploy.
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir)):
            raise HarnessError('Metadata reconciliation accepts no SEH name, host or identity input')
        if args.execute_mutations != args.yes_i_know_this_mutates_seh:
            raise HarnessError('Both mutation approvals are required together')
        from tools.seh_control.git_deployment import reconcile_metadata
        return reconcile_metadata(control.root, network,
                 execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
    if args.operation == 'migrate-state':
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir)):
            raise HarnessError('Local Mother migration accepts no SEH name, host, or SLH key input')
        if not (args.execute_mutations and args.yes_i_know_this_mutates_seh):
            raise HarnessError('Mother migration requires both explicit mutation approvals')
        if args.approve_generation is None:
            raise HarnessError('Mother migration requires --approve-generation from inspect')
        return control.migrate_single(network, args.approve_generation,
                                      'seh-single-migration-' + uuid.uuid4().hex[:18])
    if args.operation in ('inspect', 'diagnostic'):
        if args.seh or args.host or args.slh_id or args.public_url:
            raise HarnessError('inspect and diagnostic use the verified SLH deployment; omit target-selection flags')
        existing, listing = _single_read_only_target(control, network)
        if args.operation == 'inspect':
            if existing is None:
                return {'status': 'READ_ONLY', 'network': network,
                        'generation': listing.get('generation'), 'registered': False,
                        'deployment': None}
            # The obsolete registry key is needed internally until migration,
            # but is not a new operator-visible identity.
            return {'status': 'READ_ONLY', 'network': network,
                    'generation': listing.get('generation'), 'registered': True,
                    **{key: value for key, value in existing.items() if key != 'seh'}}
        if existing is None:
            return {'status': 'REFUSED', 'operation': 'diagnostic', 'network': network,
                    'reason': 'No SEH deployment is registered for this SLH',
                    'mutation_performed': False}
        if 'seh' not in existing:
            return {'status': 'REFUSED', 'operation': 'diagnostic', 'network': network,
                    'reason': 'Canonical deployment requires the updated Coolify trust diagnostic (Patch 3)',
                    'mutation_performed': False}
        seh = ident(existing['seh'], 'legacy SEH registry key')
    else:
        if not args.seh:
            raise HarnessError('--seh remains required for legacy mutation operations')
        seh = ident(args.seh, 'seh')
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
        for field, selected in (('slh_id', args.slh_id),):
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
        if args.slh_id:
            ident(args.slh_id, 'slh_id')
    prior_url = ((existing or {}).get('deployment') or {}).get('public_url')
    if args.public_url:
        public_url = _origin(args.public_url)
        if prior_url and _origin(prior_url) != public_url:
            raise HarnessError('Existing SEH public URL differs; deploy-seh will not silently retarget it')
    elif prior_url:
        public_url = _origin(prior_url)
    else:
        # Never override the existing application's verified domain with the
        # default; a missing existing URL requires read-only discovery.
        public_url = None if existing else _origin(DEFAULT_PUBLIC_URL)
    if args.operation == 'diagnostic':
        if not existing or not (existing.get('deployment') or {}).get('application_uuid'):
            raise HarnessError('Trust diagnostic requires an existing registered SEH application')
        # A diagnostic never creates a signing identity and never needs the
        # private key when it is unavailable. It can still report remote IDs.
        local_key = None
        local_key_state = 'UNAVAILABLE'
        try:
            if slh_public_key_provider is not None:
                local_key = slh_public_key_provider(slh_id=existing.get('slh_id'))
            else:
                from tools.slh_seh_connector import default_state, service_identity
                _, local_key = service_identity(args.slh_state_dir or default_state(), create=False)
            local_key_state = 'READABLE' if isinstance(local_key, str) and bool(local_key) else 'UNAVAILABLE'
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            result = control.diagnostic_slh_trust(
                network, seh, slh_id=existing.get('slh_id'), public_key_b64=local_key)
            return {'controller': host, 'local_key_state': local_key_state,
                    **{key: value for key, value in result.items() if key != 'seh'}}
        except (SEHIdentityError, SEHGitError, HarnessError) as exc:
            return {'status': 'REFUSED', 'operation': 'diagnostic', 'network': network,
                    'controller': host, 'error_type': type(exc).__name__,
                    'reason': str(exc), 'mutation_performed': False}

    if args.operation == 'bind-slh':
        if not existing or not (existing.get('deployment') or {}).get('application_uuid'):
            raise HarnessError('Bind requires an existing registered SEH application')
        slh = args.slh_id or existing.get('slh_id')
        if not slh:
            raise HarnessError('Bind requires an approved SLH signing identity')
        if existing.get('slh_id') and slh != existing['slh_id']:
            raise HarnessError('SLH binding differs from existing Mother SEH association')
        stage = 'slh-trust-binding'
        try:
            if slh_public_key_provider is not None:
                public_key = slh_public_key_provider(slh_id=slh)
            else:
                from tools.slh_seh_connector import default_state, service_identity
                _, public_key = service_identity(args.slh_state_dir or default_state(), create=False)
            receipt = control.bind_slh(network, seh, slh_id=slh,
                                       public_key_b64=public_key,
                                       replace_existing=args.replace_existing_trust,
                                       execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
            return {'controller': host, 'operation': 'bind-slh', **receipt}
        except (SEHIdentityError, SEHGitError, HarnessError, OSError, ValueError) as exc:
            return {'status': 'REFUSED', 'network': network, 'seh': seh, 'controller': host,
                    'failed_stage': stage, 'error_type': type(exc).__name__,
                    'reason': str(exc) if isinstance(exc, (SEHIdentityError, SEHGitError, HarnessError))
                    else type(exc).__name__, 'ready': False, 'public_wss_verified': False}

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
                                     slh_id=args.slh_id)
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
        # Provision the Coolify infrastructure independently of any SLH state.
        # Existing remote SLH trust is left untouched. A new relay is unbound
        # and refuses all tunnels until a separate, authenticated binding step.
        stage = 'prepare'
        prepared = control.prepare_git(network, seh, project_name=args.project_name,
                                       environment_name=args.environment_name, public_url=public_url)
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



def _powershell_argument(value: object) -> str:
    """Quote an operator-supplied CLI value as one literal PowerShell argument."""
    raw = str(value)
    if re.fullmatch(r'[A-Za-z0-9_./:\\-]+', raw):
        return raw
    return "'" + raw.replace("'", "''") + "'"


def _deploy_retry_command(args: argparse.Namespace, *, require_replace: bool = False) -> str:
    """Reconstruct the original logical target without copying credentials."""
    tokens = ['python', r'.\seh_mutate_harness.py', args.operation]
    for attr, flag in (
        ('network', '--network'),
        ('seh', '--seh'),
        ('host', '--host'),
        ('slh_id', '--slh-id'),
        ('public_url', '--public-url'),
    ):
        value = getattr(args, attr, None)
        if value:
            tokens.extend((flag, _powershell_argument(value)))
    for attr, flag, default in (
        ('project_name', '--project-name', 'My first project'),
        ('environment_name', '--environment-name', None),
        ('runtime_state_root', '--runtime-state-root', Path('runtime/state')),
    ):
        value = getattr(args, attr, None)
        if value is not None and value != default:
            tokens.extend((flag, _powershell_argument(value)))
    if args.operation == 'bind-slh':
        if args.replace_existing_trust or require_replace:
            tokens.append('--replace-existing-trust')
        if args.slh_state_dir:
            tokens.extend(('--slh-state-dir', _powershell_argument(args.slh_state_dir)))
    tokens.extend(('--execute-mutations', '--yes-i-know-this-mutates-seh'))
    return ' '.join(tokens)


def _print_next_operator_command(args: argparse.Namespace, result: dict) -> None:
    """Show the Hub-style, copyable next step without breaking JSON stdout."""
    if args.operation == 'diagnostic' and result.get('issues'):
        print('\nSEH trust diagnostic findings (read-only):', file=sys.stderr)
        for issue in result['issues']:
            print('  ' + issue, file=sys.stderr)
        return
    if args.operation not in ('deploy-seh', 'bind-slh'):
        return
    git_command = result.get('git_repair_command')
    if result.get('status') == 'REFUSED' and isinstance(git_command, str) and git_command.strip():
        print('\n=== Next SEH command (PowerShell) ===', file=sys.stderr)
        print('After publishing the SEH source, rerun:', file=sys.stderr)
        print(_deploy_retry_command(args), file=sys.stderr)
        print('\nRequired Git publication command (PowerShell; copy and paste):', file=sys.stderr)
        print(git_command.strip(), file=sys.stderr, flush=True)
    elif result.get('status') == 'PLAN_ONLY':
        print('\n=== Next SEH command (PowerShell) ===', file=sys.stderr)
        print('Stopping before live mutation. Rerun with mutation authorization:', file=sys.stderr)
        print(_deploy_retry_command(args, require_replace=result.get('replacement_requires_ack', False)),
              file=sys.stderr, flush=True)
    elif args.operation == 'bind-slh' and result.get('status') == 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED':
        # This legacy operator does not activate the resident SLH supervisor.
        # Never prescribe a second, standalone connector as if it completed deployment.
        print('SEH trust redeploy queued, not verified. Resident SLH auto-connection '
              'is not configured here; operational acceptance remains incomplete.',
              file=sys.stderr, flush=True)

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
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    _print_next_operator_command(args, result)
    status = result.get('status')
    if status in ('REFUSED', 'INCOMPLETE'):
        return 2
    # Nonzero until the requested live operation is fully verified. Scripts
    # must not equate an accepted Coolify deploy with an authenticated tunnel.
    if status in ('DEPLOY_QUEUED_NOT_VERIFIED', 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED'):
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
