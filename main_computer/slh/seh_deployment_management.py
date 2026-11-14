"""LMS-owned adapter for the existing SEH operator harness and Mother identity.

This is a direct SLH management capability, not an SEH forwarding endpoint.
It invokes only the fixed, installation-owned SEH harness executable, never
accepts caller-supplied command paths or arbitrary arguments, and never
allows a caller with an SEH relay ticket to run Coolify operations. Mother verified CAS is the
only writer of controller identities; the existing harness owns remote changes.
"""
from __future__ import annotations

from pathlib import Path
import secrets
import re
import os
import json
import subprocess
import sys
import tempfile
import threading
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
    'activate-seh': 'ACTIVATE_SEH',
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
        from .seh_management_journal import ManagementJournal
        self.operations = ManagementJournal(self.root,self.network)

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
            self.operations.ensure_idle()
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
            self.operations.ensure_idle()
            result = self.control.select_controller(
                self.network, name, expected_generation=generation,
                operation_id=operation_id)
            return _public_only({**result, 'deployment_mutation': False})
        except Exception as exc:
            self._failure(exc)


    def _redaction_values(self):
        """Read known controller credentials only to prevent them entering logs."""
        try:
            _, _, document = st.read(self.root, self.network)
            controllers = document.get('networks', {}).get(self.network, {}).get('coolify', {}).get('controllers', {})
            values = []
            for item in controllers.values():
                if type(item) is dict:
                    for key in ('api_token', 'token', 'coolify_token'):
                        secret = item.get(key)
                        if type(secret) is str and len(secret) >= 6:
                            values.append(secret)
            return tuple(sorted(set(values), key=len, reverse=True))
        except Exception:
            # A failed read cannot authorize mutation or permit raw log output:
            # the fixed structured scrubber below remains active.
            return ()

    @staticmethod
    def _sanitize_console(value: str, secrets_to_hide=()):
        """Scrub credentials and terminal controls before persistence or IPC."""
        value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
        value = ''.join(c if c in '\t\r\n' or ord(c) >= 32 else '?' for c in value)
        for secret in secrets_to_hide:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)\b(Bearer)\s+[^\s\"\']+', r'\1 [REDACTED]', value)
        value = re.sub(r'(?i)\b((?:api[_-]?token|coolify[_-]?token|authorization|password|secret|private[_-]?key)\s*[=:]\s*)[^\s,}]+',
                       r'\1[REDACTED]', value)
        return value

    @staticmethod
    def _harness_script():
        return Path(__file__).resolve().parents[2] / 'seh_mutate_harness.py'

    def _run_cli_with_output(self, action, execute, operation_id):
        """Isolated Python harness with genuine stdout and stderr capture.

        The user selects a semantic operation; executable and arguments are
        installation-owned. The CLI's --report is a private, bounded result
        channel distinct from the human-readable process streams.
        """
        from .storage import secure_machine_state_dir, secure_private_state_file
        repo = Path(__file__).resolve().parents[2]
        script = self._harness_script()
        secure_machine_state_dir(self.operations.path)
        fd, report = tempfile.mkstemp(prefix='.seh-cli-', suffix='.receipt', dir=self.operations.path)
        with os.fdopen(fd, 'wb') as private_report:
            private_report.write(b'{}\n')
        secure_private_state_file(Path(report))
        command = [sys.executable, '-u', str(script), action,
                   '--network', self.network, '--runtime-state-root', str(self.root),
                   '--report', report]
        if action in ('deploy-seh', 'undeploy-seh'):
            command.extend(['--slh-state-dir', str(self.state_dir)])
        if execute:
            command.extend(['--execute-mutations', '--yes-i-know-this-mutates-seh'])
        redact_values = self._redaction_values()
        def output(channel, raw):
            cleaned = self._sanitize_console(raw, redact_values)
            for i in range(0, len(cleaned), 256):
                self.operations.append_output(operation_id, channel, cleaned[i:i+256])
        process = None
        try:
            process = subprocess.Popen(command, cwd=repo, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       bufsize=0, close_fds=True,
                                       **({'creationflags':subprocess.CREATE_NO_WINDOW}
                                          if os.name == 'nt' else {}))
            def drain(pipe, channel):
                try:
                    # Never emit arbitrary fragments from an oversized line:
                    # a credential could otherwise straddle two fragments
                    # and evade the redactor. Drop that entire line instead.
                    while True:
                        raw_line = pipe.readline(8193)
                        if not raw_line:
                            break
                        if len(raw_line) > 8192 and not raw_line.endswith(b'\n'):
                            output(channel, '[oversized harness output line omitted]')
                            while raw_line and not raw_line.endswith(b'\n'):
                                raw_line = pipe.readline(8193)
                            continue
                        output(channel, raw_line.decode('utf-8', errors='replace').rstrip('\r\n'))
                finally:
                    pipe.close()
            stdout_thread = threading.Thread(target=drain, args=(process.stdout,'stdout'), daemon=True)
            stderr_thread = threading.Thread(target=drain, args=(process.stderr,'stderr'), daemon=True)
            stdout_thread.start(); stderr_thread.start()
            exit_code = process.wait()
            stdout_thread.join(); stderr_thread.join()
            if os.path.getsize(report) > 131072:
                raise SEHManagementRefused('SEH_HARNESS_REPORT_OVERSIZED',502)
            raw = Path(report).read_text(encoding='utf-8')
            result = json.loads(raw) if raw else None
            if type(result) is not dict or type(result.get('status')) is not str:
                raise SEHManagementRefused('SEH_HARNESS_REPORT_INVALID',502)
            # The CLI deliberately returns 3 for a queued, unverified build.
            # Its status receipt, not the process exit code, is authoritative.
            def scrub_result(obj):
                if type(obj) is str:
                    return self._sanitize_console(obj, redact_values)
                if type(obj) is dict:
                    return {k:scrub_result(v) for k,v in obj.items()}
                if type(obj) is list:
                    return [scrub_result(v) for v in obj]
                return obj
            return _public_only(scrub_result(result))
        finally:
            if process is not None and process.poll() is None:
                process.kill()
            try:
                os.unlink(report)
            except FileNotFoundError:
                pass

    def harness(self, body: dict) -> dict:
        if type(body) is not dict or set(body) not in (
                {'action','execute','expected_generation','confirm'},
                {'action','execute','expected_generation','confirm','operation_id'}):
            raise SEHManagementRefused('INVALID_SEH_MANAGEMENT_REQUEST',400)
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
            if not execute:
                self.operations.ensure_idle()
            def validate_generation():
                current=self.control.list(self.network)
                if current['generation']!=expected_generation:
                    raise SEHManagementRefused('SEH_GENERATION_CONFLICT')
            def run_action_for(harness_action, mutate):
                from seh_mutate_harness import _parser, run
                args=_parser().parse_args([
                    harness_action,'--network',self.network,
                    '--runtime-state-root',str(self.root),
                    *(['--slh-state-dir',str(self.state_dir)]
                      if harness_action in ('undeploy-seh','deploy-seh') else []),
                    *(['--execute-mutations','--yes-i-know-this-mutates-seh'] if mutate else []),
                ])
                if self.harness_runner is not None:
                    outcome=self.harness_runner(args,control=self.control)
                elif execute:
                    outcome=self._run_cli_with_output(harness_action,mutate,operation_id)
                else:
                    outcome=run(args,control=self.control)
                if type(outcome) is not dict:
                    raise SEHManagementRefused('SEH_HARNESS_INVALID_RESPONSE',502)
                return _public_only(outcome)
            operation_id=body.get('operation_id') if execute else None
            if execute and operation_id is None:
                operation_id='seh-lms-'+secrets.token_hex(16)
            def invoke():
                if not execute:
                    return run_action_for(action,False)
                validate_generation()
                # The canonical operational record, not the browser, decides
                # whether Deploy creates a replacement or resumes activation.
                # A registered UUID must NEVER be passed to deploy-seh again.
                chosen_action = action
                if action == 'deploy-seh':
                    phase = self.inspect({})['deployment_phase']
                    if phase == 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED':
                        chosen_action = 'activate-seh'
                    # No canonical history must still be refused by the existing
                    # deploy-seh harness; LMS never invents a first-time path.
                def preflight(harness_action, *, stage):
                    self.operations.update_stage(operation_id,stage)
                    try:
                        preview = run_action_for(harness_action,False)
                    except Exception as exc:
                        return {'status':'REFUSED',
                                'error_code':'SEH_' + ('ACTIVATION' if harness_action=='activate-seh' else 'DEPLOYMENT') + '_PREFLIGHT_UNAVAILABLE',
                                'error_type':type(exc).__name__, 'remote_mutation':False}
                    if preview.get('status') != 'PLAN_ONLY' or (
                            harness_action == 'undeploy-seh' and preview.get('ready_for_undeploy') is not True):
                        return {'status':'REFUSED',
                                'error_code':'SEH_' + ('ACTIVATION' if harness_action=='activate-seh' else 'DEPLOYMENT') + '_PREFLIGHT_REFUSED',
                                'preflight_status':str(preview.get('status','UNKNOWN'))[:100],
                                'remote_mutation':False}
                    return None
                def activate(deployment_result=None):
                    declined = preflight('activate-seh',stage='ACTIVATION_PREFLIGHT')
                    if declined:
                        if deployment_result is None:
                            return declined
                        # The deployment already changed the remote state;
                        # never claim a preflight-only refusal or safe retry.
                        return {'status':'DEPLOYMENT_REGISTERED_ACTIVATION_REFUSED',
                                'error_code':declined['error_code'],
                                'activation_preflight_status':declined.get('preflight_status','UNAVAILABLE'),
                                'deployment_status':deployment_result['status'],
                                'application_uuid':deployment_result.get('application_uuid'),
                                'remote_mutation':deployment_result.get('remote_mutation','UNKNOWN')}
                    validate_generation()
                    self.operations.update_stage(operation_id,'ACTIVATION_EXECUTING')
                    activated = run_action_for('activate-seh',True)
                    if deployment_result is not None:
                        return {**activated, 'deployment_status':deployment_result['status'],
                                'deployment_remote_mutation':deployment_result.get('remote_mutation','UNKNOWN'),
                                'application_uuid':activated.get('application_uuid') or deployment_result.get('application_uuid')}
                    return activated
                if chosen_action == 'activate-seh':
                    if self.inspect({})['deployment_phase'] != 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED':
                        return {'status':'REFUSED', 'error_code':'SEH_ACTIVATION_NOT_ELIGIBLE',
                                'remote_mutation':False}
                    return activate()
                declined = preflight(chosen_action,stage='PREFLIGHT_EXECUTING')
                if declined:
                    return declined
                validate_generation()
                self.operations.update_stage(operation_id,'MUTATION_EXECUTING')
                result = run_action_for(chosen_action,True)
                # A queued canonical application is NOT runtime-verified. LMS
                # keeps the same operation ID, then invokes the existing
                # activation harness with its own read-only admission gate.
                if (action == 'deploy-seh' and
                        result.get('status') == 'REPLACEMENT_QUEUED_NOT_VERIFIED' and
                        type(result.get('application_uuid')) is str and
                        result['application_uuid']):
                    # Read back the exact new UUID before authorizing activation.
                    # A queue acknowledgement alone cannot prove canonical ownership.
                    observed = self.inspect({})
                    live = (observed.get('deployment') or {}).get('deployment') or {}
                    if (observed['deployment_phase'] != 'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED' or
                            live.get('application_uuid') != result['application_uuid']):
                        return {**result, 'status':'DEPLOYMENT_QUEUED_ACTIVATION_PENDING',
                                'activation_reason':'CANONICAL_APPLICATION_READBACK_UNVERIFIED'}
                    return activate(result)
                return result
            if not execute:
                return invoke()
            from .seh_management_journal import ManagementJournalError
            from .seh_lms_ipc import CURRENT_PRINCIPAL
            details={'action':action,'principal':CURRENT_PRINCIPAL.get(),
                     'expected_generation':expected_generation,
                     'confirm':body['confirm'],'execute':True}
            return self.operations.submit(operation_id,details,invoke,validate=validate_generation)
        except SEHManagementRefused:
            raise
        except ManagementJournalError as exc:
            raise SEHManagementRefused(exc.code,exc.status) from None
        except Exception as exc:
            self._failure(exc)

    def operation_snapshot(self) -> dict:
        from .seh_management_journal import ManagementJournalError
        try:
            return _public_only(self.operations.snapshot())
        except ManagementJournalError as exc:
            raise SEHManagementRefused(exc.code,exc.status) from None

    def cancel_operation(self, operation_id: str) -> dict:
        from .seh_management_journal import ManagementJournalError
        try:
            return _public_only(self.operations.cancel(operation_id))
        except ManagementJournalError as exc:
            raise SEHManagementRefused(exc.code,exc.status) from None

    def operation_status(self, operation_id: str) -> dict:
        from .seh_management_journal import ManagementJournalError
        try:
            return _public_only(self.operations.status(operation_id))
        except ManagementJournalError as exc:
            raise SEHManagementRefused(exc.code,exc.status) from None

    @staticmethod
    def _failure(exc):
        if isinstance(exc, SEHManagementRefused):
            raise exc
        from .seh_management_journal import ManagementJournalError
        if isinstance(exc, ManagementJournalError):
            raise SEHManagementRefused(exc.code,exc.status) from None
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
    if method == 'GET' and suffix == '/operations':
        if body:
            raise SEHManagementRefused('INVALID_SEH_MANAGEMENT_REQUEST',400)
        return manager.operation_snapshot()
    if method == 'POST' and suffix.startswith('/operations/') and suffix.endswith('/cancel'):
        if body:
            raise SEHManagementRefused('INVALID_SEH_MANAGEMENT_REQUEST',400)
        return manager.cancel_operation(suffix[len('/operations/'):-len('/cancel')])
    if method == 'GET' and suffix.startswith('/operations/'):
        if body:
            raise SEHManagementRefused('INVALID_SEH_MANAGEMENT_REQUEST',400)
        return manager.operation_status(suffix[len('/operations/'):])
    operation = dispatch.get((method, suffix))
    if operation is None:
        raise SEHManagementRefused('SEH_MANAGEMENT_ROUTE_NOT_FOUND', 404)
    return operation(body)
