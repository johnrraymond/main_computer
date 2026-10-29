"""SLH-owned SEH Coolify harness (backend-only, no UI, no remote mutation by default).

Usage:
    python -m tools.seh_harness profiles
    python -m tools.seh_harness contracts
    python -m tools.seh_harness plan --target deploy/seh/target.example.json
    python -m tools.seh_harness verify-local --target deploy/seh/target.example.json

Read-only remote preflight uses SLH_COOLIFY_TOKEN. Creation or start requires
--execute and exact plan/target approvals, and DOES NOT claim a working SEH.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from tools.seh_control.commands import SEHControl
from tools.seh_control.identity import SEHIdentityError, ident

from main_computer.slh import SLHControlBackend, SEHTarget
from main_computer.slh.coolify import CoolifyHTTP, SEHDeploymentError, compose_yaml
from main_computer.slh.config import SLHConfigError


class _FakeCoolify:
    def __init__(self):
        self.services = []
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == 'GET' and path.startswith('/api/v1/projects/'):
            parts = path.split('/')
            if len(parts) == 5:
                return {'uuid': parts[-1], 'name': 'Mock project'}
            if len(parts) == 6:
                return {'name': 'Mock environment', 'id': 2}
        if method == 'GET' and path.startswith('/api/v1/servers/'):
            return {'uuid': path.split('/')[-1], 'name': 'Mock server', 'is_usable': True}
        if method == 'GET' and path == '/api/v1/services':
            return list(self.services)
        if method == 'GET' and path.startswith('/api/v1/services/'):
            key = path.rsplit('/', 1)[-1]
            return next((x for x in self.services if x['uuid'] == key), {})
        if method == 'POST' and path == '/api/v1/services':
            x = {"uuid": "mock-seh-uuid", "name": body['name'], "description": body['description']}
            self.services.append(x)
            return {"uuid": x['uuid'], "domains": []}
        if method == 'POST' and path.endswith('/start'):
            return {"message": "Mock start queued"}
        raise AssertionError((method, path))


def _deployment_config_path(runtime_state_root: Path, network: str, seh: str) -> Path:
    """Per-SEH nonsecret deployment input, adjacent to existing runtime deployments.

    The verified Mother private identity state remains the only committed
    authority; this file is editable input to a guarded `configure` mutation.
    """
    ident(network, 'network')
    ident(seh, 'seh')
    return Path(runtime_state_root).parent / 'deployments' / network / 'sehs' / seh / 'deployment.json'



def _missing_deployment_fields(root: Path, network: str, seh: str, changes: dict) -> list[str]:
    """Check missing required fields without including secrets in any output."""
    from tools.seh_control.identity import read, seh_doc
    _, _, document = read(root, network)
    entry = seh_doc(document, network, seh)
    selected = entry['coolify'].get('active_identity')
    controller = (entry['coolify']['identities'].get(selected) or {}).get('controller', {}) if selected else {}
    required = ('public_url', 'image', 'image_digest', 'environment_uuid',
                'slh_public_key_b64', 'project_uuid', 'server_uuid')
    missing = []
    for key in required:
        value = changes.get(key) or entry['deployment'].get(key)
        if key in ('project_uuid', 'server_uuid'):
            value = value or controller.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    return missing


def _create_deployment_draft(runtime_state_root: Path, network: str, seh: str, path: Path) -> dict:
    """Create a nonsecret editable input exactly once; never commit fake remote values."""
    from tools.seh_control.identity import read, seh_doc
    _, binding, document = read(runtime_state_root, network)
    entry = seh_doc(document, network, seh)
    active = entry['coolify'].get('active_identity')
    if not active:
        raise SEHIdentityError('Copy or select a Coolify identity before configuring the SEH')
    controller = entry['coolify']['identities'][active]['controller']
    previous = entry['deployment']
    fields = ('public_url', 'image', 'image_digest', 'environment_uuid',
              'slh_public_key_b64', 'project_uuid', 'server_uuid')
    draft = {'enabled': True}
    for name in fields:
        # Controller carries nonsecret deployment placement as well as the token;
        # never serialize the controller, its URL, or the token into this file.
        value = previous.get(name) or (controller.get(name) if name in
                  ('project_uuid', 'environment_uuid', 'server_uuid') else None)
        draft[name] = value if isinstance(value, str) else ''
    if not draft['slh_public_key_b64']:
        try:
            from tools.slh_seh_connector import default_state, service_identity
            _, public = service_identity(default_state(), create=False)
            draft['slh_public_key_b64'] = public
        except (FileNotFoundError, OSError, ValueError):
            # No local SLH key is a normal remote-management condition.
            # Never create a new identity or guess the public key here.
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as output:
            json.dump(draft, output, indent=2, sort_keys=True)
            output.write('\n')
    except FileExistsError:
        raise SEHIdentityError('Deployment draft was created concurrently; review it before configuring') from None
    return {'status': 'DRAFT_CREATED', 'network': network, 'seh': seh,
            'path': str(path), 'state_generation': binding.binding.generation,
            'missing_fields': _missing_deployment_fields(runtime_state_root, network, seh, draft),
            'identity_committed': False, 'remote_mutation': False}


def _target(path):
    return SEHTarget.from_dict(json.loads(Path(path).read_text(encoding='utf-8')))


def _local_verify(target):
    backend = SLHControlBackend()
    # Mock creation must still include a valid public SLH service identity.
    # This is generated only for the isolated fake Coolify target, never used
    # as an alternative to real administrator provisioning.
    if not target.slh_public_key_b64:
        import base64
        from dataclasses import replace
        from main_computer.slh.authenticator import generate_identity
        _, public = generate_identity()
        target = replace(target, slh_public_key_b64=base64.b64encode(public.encode('ascii')).decode('ascii'))
    p = backend.seh_plan(target)
    fake = _FakeCoolify()
    gates = {}
    try:
        gates['P0_plan_is_dry_run'] = p['status'] == 'PLANNED_ONLY' and fake.calls == []
        gates['P1_connection_profiles'] = set(x['key'] for x in backend.connection_catalog()['profiles']) == {'slh','seh','mainnet','testnet','test','dev'}
        gates['P2_contract_declared_not_live'] = backend.managed_work_api()['implementation'] == 'NOT_IMPLEMENTED'
        gates['P3_compose_pinned_and_nonprivileged'] = f"@{target.image_digest}" in compose_yaml(target) and 'cap_drop:' in compose_yaml(target) and 'ports:' not in compose_yaml(target)
        if target.service_uuid:
            gates['P4_isolated_create'] = 'NOT_TESTED_TARGET_HAS_SERVICE_UUID'
            gates['P5_preflight_collision'] = 'NOT_TESTED_TARGET_HAS_SERVICE_UUID'
        else:
            pre = backend.seh_preflight(target, fake)
            gates['P4_isolated_create'] = pre['status'] == 'CREATE_ELIGIBLE'
            try:
                backend.seh_create(target, fake, digest='wrong', target_id=target.target_id)
                gates['P5_wrong_approval_refused'] = False
            except SEHDeploymentError:
                gates['P5_wrong_approval_refused'] = True
            gates['P6_refused_mutation_no_post'] = not any(method == 'POST' for method, _ in fake.calls)
            receipt = backend.seh_create(target, fake, digest=p['plan_digest'], target_id=target.target_id)
            gates['P7_mock_create_not_ready'] = receipt['status'] == 'CREATED_NOT_STARTED' and not receipt['ready']
            try:
                backend.seh_create(target, fake, digest=p['plan_digest'], target_id=target.target_id)
                gates['P8_duplicate_refused'] = False
            except SEHDeploymentError:
                gates['P8_duplicate_refused'] = True
    except Exception as exc:
        gates['internal_error'] = type(exc).__name__
    ok = all(x is True for x in gates.values())
    return {'schema': 'main-computer.seh-harness-report.v1', 'mode': 'isolated-mock',
            'status': 'PASS' if ok else 'INCOMPLETE', 'gates': gates,
            'real_seh_runtime': 'NOT_TESTED', 'coolify_live': 'NOT_TESTED',
            'slh_e2e_auth': 'NOT_IMPLEMENTED', 'public_wss': 'NOT_TESTED',
            'worker_delegation': 'NOT_IMPLEMENTED', 'claim': 'Control-plane contract tests only'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('profiles','contracts','probe-hub','plan','preflight','verify-coolify','deploy','start','verify-local','list','inspect','coolify-sources','reserve','coolify-copy','coolify-refresh','coolify-select','configure','status','recover'))
    parser.add_argument('--target', type=Path)
    parser.add_argument('--network', choices=('mainnet','testnet','test','dev'))
    parser.add_argument('--isolate', action='store_true', help='Explicit local isolated mock mode')
    parser.add_argument('--read-only', action='store_true', help='Affirm read-only remote preflight')
    parser.add_argument('--execute', action='store_true', help='Required for Coolify mutation')
    parser.add_argument('--approve-digest', default='')
    parser.add_argument('--approve-target', default='')
    parser.add_argument('--approve-uuid', default='')
    parser.add_argument('--report', type=Path, help='Write JSON report to this location')
    parser.add_argument('--seh', help='Registered SEH name (state-driven mode)')
    parser.add_argument('--runtime-state-root', type=Path, default=Path('runtime/state'))
    parser.add_argument('--approve-generation', type=int, help='Optional strict state-generation pin; the harness normally selects it automatically')
    parser.add_argument('--operation-id', default='')
    parser.add_argument('--organization-id', default='')
    parser.add_argument('--slh-id', default='')
    parser.add_argument('--source', default='', help='Exact existing Coolify state path')
    parser.add_argument('--as', dest='alias', default='', help='SEH-owned Coolify identity alias')
    parser.add_argument('--replace', action='store_true', help='Explicitly replace existing copied identity')
    parser.add_argument('--deployment-file', type=Path, help='Override the default runtime/deployments/<network>/sehs/<seh>/deployment.json input')

    a = parser.parse_args(argv)
    try:
        backend = SLHControlBackend()
        if a.command == 'profiles':
            result = backend.connection_catalog()
        elif a.command == 'contracts':
            result = backend.managed_work_api()
        elif a.command == 'probe-hub':
            if not a.network:
                parser.error('--network required')
            result = backend.probe_marketplace_hub(a.network)
        elif a.command in ('list','inspect','coolify-sources','reserve','coolify-copy','coolify-refresh','coolify-select','configure','status','recover') or (a.seh and not a.target):
            control=SEHControl(a.runtime_state_root)
            if a.command=='coolify-sources':
                result=control.sources(a.network)
            elif not a.network:
                parser.error('--network required for state-driven SEH commands')
            elif a.command=='list':
                result=control.list(a.network)
            else:
                if not a.seh:
                    parser.error('--seh required')
                if a.command=='inspect':
                    result=control.inspect(a.network,a.seh)
                elif a.command=='plan':
                    result=control.plan(a.network,a.seh)
                elif a.command in ('preflight','verify-coolify'):
                    if a.command=='verify-coolify' and not a.read_only:
                        raise SEHIdentityError('verify-coolify requires --read-only')
                    result=control.preflight(a.network,a.seh)
                elif a.command=='status':
                    result=control.status(a.network,a.seh)
                else:
                    if not a.execute:
                        raise SEHIdentityError('Mutation requires --execute')
                    operation_id=a.operation_id or f'seh-{a.command}-{uuid.uuid4().hex[:14]}'
                    if a.command in ('reserve','coolify-copy','coolify-refresh','coolify-select','configure'):
                        if a.command=='configure':
                            config_path = a.deployment_file or _deployment_config_path(
                                a.runtime_state_root, a.network, a.seh)
                            if not config_path.is_file():
                                if a.deployment_file:
                                    raise SEHIdentityError(f'Explicit deployment configuration not found: {config_path}')
                                result = _create_deployment_draft(a.runtime_state_root, a.network, a.seh, config_path)
                                if a.report:
                                    a.report.parent.mkdir(parents=True, exist_ok=True)
                                    a.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
                                print(json.dumps(result, indent=2, sort_keys=True))
                                return 0
                            changes=json.loads(config_path.read_text(encoding='utf-8'))
                            if type(changes) is not dict:
                                raise SEHIdentityError('Deployment configuration must be an object')
                            if changes.get('enabled'):
                                missing = _missing_deployment_fields(a.runtime_state_root, a.network, a.seh, changes)
                                if missing:
                                    result = {'status': 'NEEDS_CONFIGURATION', 'network': a.network,
                                              'seh': a.seh, 'path': str(config_path),
                                              'missing_fields': missing, 'identity_committed': False}
                                    print(json.dumps(result, indent=2, sort_keys=True))
                                    return 2
                        else:
                            changes=None
                        result=control.change(a.command,a.network,a.seh,a.approve_generation,operation_id,
                                              organization_id=a.organization_id,slh_id=a.slh_id,
                                              source=a.source,alias=a.alias,replace_identity=a.replace,
                                              changes=changes)
                    elif a.command=='deploy':
                        result=control.deploy(a.network,a.seh,generation=a.approve_generation,
                                              digest=a.approve_digest,target_id=a.approve_target,
                                              operation_id=operation_id)
                    elif a.command=='start':
                        result=control.start(a.network,a.seh,generation=a.approve_generation,
                                             digest=a.approve_digest,uuid=a.approve_uuid)
                    elif a.command=='recover':
                        result=control.recover(a.network,a.seh,generation=a.approve_generation,
                                               uuid=a.approve_uuid,operation_id=operation_id)
                    else:
                        raise SEHIdentityError('Unsupported state command')
        else:
            if not a.target and a.command != 'verify-local':
                parser.error('--target required')
            target = _target(a.target or Path(__file__).resolve().parents[1] / 'deploy/seh/target.example.json')
            if a.command == 'plan':
                result = backend.seh_plan(target)
            elif a.command == 'verify-local':
                result = _local_verify(target)
            else:
                if a.command in ('deploy','start') and not a.execute:
                    raise SEHDeploymentError('Mutation refused: --execute required. Run plan/preflight first.')
                if a.command in ('deploy','start') and (
                    target.image.endswith('.invalid/main-computer/seh') or target.image_digest == 'sha256:' + '0'*64
                ):
                    raise SEHDeploymentError('Placeholder image cannot be deployed')
                client = CoolifyHTTP(target.coolify_url, os.environ.get('SLH_COOLIFY_TOKEN', ''))
                if a.command in ('preflight', 'verify-coolify'):
                    if a.command == 'verify-coolify' and not a.read_only:
                        raise SEHDeploymentError('verify-coolify requires --read-only')
                    result = backend.seh_preflight(target, client)
                elif a.command == 'deploy':
                    result = backend.seh_create(target, client, digest=a.approve_digest, target_id=a.approve_target)
                else:
                    result = backend.seh_start(target, client, digest=a.approve_digest, service_uuid=a.approve_uuid)
        if a.report:
            a.report.parent.mkdir(parents=True, exist_ok=True)
            # Never include private keys/tokens in reports; result is a constrained schema.
            a.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get('status') != 'INCOMPLETE' else 2
    except (SEHDeploymentError, SLHConfigError, SEHIdentityError, OSError, ValueError) as exc:
        print(json.dumps({'status':'REFUSED', 'reason': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
