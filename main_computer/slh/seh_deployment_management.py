"""SLH-owned adapter for the existing SEH operator harness and Mother identity.

This is a direct SLH management capability, not an SEH forwarding endpoint.
It never executes a CLI subprocess, accepts arbitrary paths, or allows a caller
with an SEH relay ticket to run Coolify operations. Mother verified CAS is the
only writer of controller identities; the existing harness owns remote changes.
"""
from __future__ import annotations

from pathlib import Path
import re
from urllib.parse import urlsplit

from tools.seh_control.commands import SEHControl
from tools.seh_control import identity as st


_NAME = re.compile(r'coolify-[A-Za-z0-9_-]{1,116}\Z')
_OPERATIONS = {
    'redeploy-runtime': 'REDEPLOY_RUNTIME',
    'recover-runtime': 'RECOVER_RUNTIME',
    'reconcile-metadata': 'RECONCILE_METADATA',
    'undeploy-seh': 'UNDEPLOY_SEH',
    'deploy-seh': 'DEPLOY_SEH',
}


class SEHManagementRefused(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def _expected(body: dict, keys: set[str]) -> None:
    if type(body) is not dict or set(body) != keys:
        raise SEHManagementRefused('INVALID_SEH_MANAGEMENT_REQUEST', 400)


def _mutation_identity(body: dict) -> tuple[int, str]:
    generation = body.get('expected_generation')
    operation_id = body.get('operation_id')
    if type(generation) is not int or generation < 1:
        raise SEHManagementRefused('SEH_GENERATION_REQUIRED', 400)
    try:
        st.ident(operation_id, 'operation_id')
    except (ValueError, TypeError):
        raise SEHManagementRefused('SEH_OPERATION_ID_REQUIRED', 400) from None
    return generation, operation_id


def _controller_name(name: object) -> str:
    if type(name) is not str or not _NAME.fullmatch(name):
        raise SEHManagementRefused('INVALID_COOLIFY_IDENTITY', 400)
    return name


def _new_origin(url: object) -> str:
    # Never introduce a new plaintext Coolify credential channel. Historic
    # controller sources already in verified state are still readable.
    if type(url) is not str or len(url) > 250 or any(ord(x) < 32 for x in url):
        raise SEHManagementRefused('INVALID_COOLIFY_ORIGIN', 400)
    try:
        parts = urlsplit(url)
        valid_port = parts.port or 443
    except ValueError:
        raise SEHManagementRefused('INVALID_COOLIFY_ORIGIN', 400) from None
    if (parts.scheme != 'https' or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or parts.path not in ('', '/') or
            not 1 <= valid_port <= 65535):
        raise SEHManagementRefused('COOLIFY_REQUIRES_HTTPS_ORIGIN', 400)
    return url.rstrip('/')


def _public_only(data):
    """Last-line response guard; credentials never leave the backend."""
    forbidden = ('token', 'password', 'secret', 'private_key', 'authorization')
    if isinstance(data, dict):
        return {k: _public_only(v) for k, v in data.items()
                if not any(part in k.lower() for part in forbidden)}
    if isinstance(data, list):
        return [_public_only(v) for v in data]
    return data


class SLHSEHDeploymentManager:
    def __init__(self, *, root: Path | None = None, network: str = 'mainnet',
                 control: SEHControl | None = None, harness_runner=None,
                 state_dir: Path | None = None):
        st.ident(network, 'network')
        if network not in ('mainnet', 'testnet', 'test', 'dev'):
            raise ValueError('Unsupported SEH network')
        # An absolute, installation-owned state root, NEVER a request-supplied path.
        self.root = Path(root or Path(__file__).resolve().parents[2] / 'runtime' / 'state').resolve()
        self.network = network
        from .storage import slh_state_dir
        # Installation-owned resident SLH state, never a request-supplied path.
        self.state_dir = Path(state_dir or slh_state_dir()).resolve()
        self.control = control or SEHControl(self.root)
        self.harness_runner = harness_runner

    def inspect(self, body: dict) -> dict:
        _expected(body, set())
        try:
            records = self.control.list(self.network)
            controllers = self.control.sources(self.network)
            deployment = records.get('deployment')
            if deployment:
                # Mother owns the immutable Coolify/controller association;
                # the verified operational state owns the current app UUID.
                # After a documented undeploy/recreation, Mother's historical
                # deployment UUID must not be misrepresented as the live app.
                from tools.seh_control import deployment_state as ds
                identity = st.deployment_record(st.read(self.root, self.network)[2], self.network)
                if identity is None:
                    raise SEHManagementRefused('SEH_DEPLOYMENT_BINDING_UNVERIFIED', 409)
                operational = ds.selected(self.root, self.network, identity)['deployment']
                deployment = {**deployment, 'deployment': operational}
            return _public_only({
                'schema': 'main-computer.slh-seh-management.v1',
                'status': 'READ_ONLY', 'network': self.network,
                'generation': records['generation'],
                'controller_connectivity_verified': False,
                'controllers': [s for s in controllers['sources']
                                if s.get('kind') == 'network-controller' and
                                s.get('network') == self.network],
                'deployment': deployment,
                'deployment_phase': self._phase(deployment),
                'legacy_migration_required': 'sehs' in records and bool(records['sehs']),
            })
        except Exception as exc:
            self._failure(exc)

    def _phase(self, deployment: dict | None) -> str:
        if not deployment:
            return 'NOT_CONFIGURED'
        from tools.seh_control import deployment_state as ds
        record = st.read(self.root, self.network)[2]
        canonical = st.deployment_record(record, self.network)
        if canonical is None:
            return 'LEGACY_MIGRATION_REQUIRED'
        operational = ds.selected(self.root, self.network, canonical)['deployment']
        if operational.get('undeployed') is True:
            return 'UNDEPLOYED'
        if operational.get('application_uuid'):
            return 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED'
        return 'TARGET_SELECTED'

    def register(self, body: dict) -> dict:
        _expected(body, {'name', 'url', 'api_token', 'expected_generation', 'operation_id'})
        generation, operation_id = _mutation_identity(body)
        name = _controller_name(body['name'])
        url = _new_origin(body['url'])
        token = body['api_token']
        if type(token) is not str or not 6 <= len(token) <= 4096 or any(c in token for c in '\r\n\x00'):
            raise SEHManagementRefused('INVALID_COOLIFY_CREDENTIAL', 400)
        try:
            result = self.control.register_controller(
                self.network, name, {'url': url, 'api_token': token},
                expected_generation=generation, operation_id=operation_id)
            return _public_only({**result, 'controller_connectivity_verified': False})
        except Exception as exc:
            self._failure(exc)

    def select(self, body: dict) -> dict:
        _expected(body, {'name', 'expected_generation', 'operation_id'})
        generation, operation_id = _mutation_identity(body)
        name = _controller_name(body['name'])
        try:
            result = self.control.select_controller(
                self.network, name, expected_generation=generation,
                operation_id=operation_id)
            return _public_only({**result, 'deployment_mutation': False})
        except Exception as exc:
            self._failure(exc)

    def harness(self, body: dict) -> dict:
        _expected(body, {'action', 'execute', 'expected_generation', 'confirm'})
        action = body['action']
        execute = body['execute']
        if type(action) is not str or type(execute) is not bool:
            raise SEHManagementRefused('INVALID_SEH_HARNESS_OPERATION', 400)
        if action in ('deploy', 'undeploy'):
            # Named/legacy deploy remains unavailable; never fall back to
            # a user-selected SEH or controller.
            raise SEHManagementRefused('SEH_HARNESS_OPERATION_NOT_AVAILABLE', 501)
        if action not in ('inspect', 'diagnostic', *_OPERATIONS):
            raise SEHManagementRefused('INVALID_SEH_HARNESS_OPERATION', 400)
        if execute and action not in _OPERATIONS:
            raise SEHManagementRefused('SEH_READ_ONLY_OPERATION', 400)
        if execute:
            expected_generation = body['expected_generation']
            if type(expected_generation) is not int or expected_generation < 1:
                raise SEHManagementRefused('SEH_GENERATION_REQUIRED', 400)
            if body['confirm'] != _OPERATIONS.get(action):
                raise SEHManagementRefused('SEH_MUTATION_CONFIRMATION_REQUIRED', 400)
        elif body['confirm'] is not None or body['expected_generation'] is not None:
            raise SEHManagementRefused('SEH_READ_ONLY_ARGUMENTS_INVALID', 400)
        try:
            if execute:
                current = self.control.list(self.network)
                if current['generation'] != expected_generation:
                    raise SEHManagementRefused('SEH_GENERATION_CONFLICT')
            from seh_mutate_harness import _parser, run
            args = _parser().parse_args([
                action, '--network', self.network,
                '--runtime-state-root', str(self.root),
                *(['--slh-state-dir', str(self.state_dir)] if action in ('undeploy-seh', 'deploy-seh') else []),
                *(['--execute-mutations', '--yes-i-know-this-mutates-seh'] if execute else []),
            ])
            outcome = (self.harness_runner(args, control=self.control)
                       if self.harness_runner is not None else run(args, control=self.control))
            if type(outcome) is not dict:
                raise SEHManagementRefused('SEH_HARNESS_INVALID_RESPONSE', 502)
            return _public_only(outcome)
        except SEHManagementRefused:
            raise
        except Exception as exc:
            self._failure(exc)

    @staticmethod
    def _failure(exc):
        if isinstance(exc, SEHManagementRefused):
            raise exc
        if isinstance(exc, (st.SEHIdentityError, ValueError)):
            raise SEHManagementRefused('SEH_IDENTITY_OR_HARNESS_REFUSED') from None
        # Never surface transport exception messages, which may include tokens.
        raise SEHManagementRefused('SEH_MANAGEMENT_UNAVAILABLE', 503) from None


_LOCAL = '/v1/local/seh/deployment'
_DIRECT = '/v1/org/admin/seh/deployment'


def management_request(manager: SLHSEHDeploymentManager, method: str,
                       path: str, body: dict) -> dict:
    """Same backend contract on local and directly authenticated ORG-SLH.

    The SEH-restricted HTTPS listener must NEVER call this router.
    """
    suffix = None
    for prefix in (_LOCAL, _DIRECT):
        if path == prefix or path.startswith(prefix + '/'):
            suffix = path[len(prefix):]
            break
    if suffix is None:
        raise SEHManagementRefused('SEH_MANAGEMENT_ROUTE_NOT_FOUND', 404)
    dispatch = {
        ('GET', ''): manager.inspect,
        ('POST', '/controllers'): manager.register,
        ('POST', '/selection'): manager.select,
        ('POST', '/harness'): manager.harness,
    }
    operation = dispatch.get((method, suffix))
    if operation is None:
        raise SEHManagementRefused('SEH_MANAGEMENT_ROUTE_NOT_FOUND', 404)
    return operation(body)
