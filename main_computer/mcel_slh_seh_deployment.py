"""Selected SLH deployment management, carried only on direct authenticated TLS.

No SEH relay ticket can authorize this interface. The SLH-owned backend
resolves Mother/Coolify identities and supplies generation-bound harness args.
"""
from __future__ import annotations

import re
import secrets

from main_computer.mcel_slh_worker_bridge import (
    OrganizationWorkerClient, WorkerBridgeError, validate_admin_bearer)
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError


class SEHDeploymentUIError(ValueError):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


_OPERATIONS = {'inspect', 'register', 'select', 'preview-deploy', 'deploy',
               'preview-undeploy', 'undeploy'}
_HARNESS = {'preview-deploy': ('deploy-seh', False), 'deploy': ('deploy-seh', True),
            'preview-undeploy': ('undeploy-seh', False), 'undeploy': ('undeploy-seh', True)}


def _validate(operation, arguments):
    if operation not in _OPERATIONS or type(arguments) is not dict:
        raise SEHDeploymentUIError('SEH_DEPLOYMENT_OPERATION_REFUSED', 403)
    expected = ({'name', 'url', 'apiToken'} if operation == 'register' else
                {'name'} if operation == 'select' else set())
    if set(arguments) != expected:
        raise SEHDeploymentUIError('SEH_DEPLOYMENT_ARGUMENTS_INVALID', 400)
    if operation in ('register', 'select'):
        value = arguments['name']
        if type(value) is not str or not re.fullmatch(r'coolify-[A-Za-z0-9_-]{1,116}', value):
            raise SEHDeploymentUIError('INVALID_COOLIFY_IDENTITY', 400)
    if operation == 'register':
        if (type(arguments['url']) is not str or len(arguments['url']) > 250 or
                type(arguments['apiToken']) is not str or not 6 <= len(arguments['apiToken']) <= 4096):
            raise SEHDeploymentUIError('INVALID_COOLIFY_CREDENTIAL', 400)


def execute_seh_deployment(operation, arguments, *, target, local_token='',
                           remote_ticket='', local_client=None, network_client=None):
    _validate(operation, arguments)
    if target not in ('local', 'network'):
        raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_REFUSED', 403)
    try:
        if target == 'local':
            if remote_ticket:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_MISMATCH', 403)
            bearer = validate_admin_bearer(local_token)
            client = local_client or OrganizationWorkerClient()
            def request(method, path, payload=None):
                return client.request(method, path, bearer, payload)
        else:
            if local_token:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_MISMATCH', 403)
            network = network_client or NetworkSlhClient()
            def request(method, path, payload=None):
                return network.seh_deployment_request(remote_ticket, method, path, payload)
        root = '/v1/org/admin/seh/deployment'
        if operation == 'inspect':
            response = request('GET', root)
            if response.get('schema') != 'main-computer.slh-seh-management.v1':
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            generation = response.get('generation')
            if type(generation) is not int or generation < 1:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            controllers = response.get('controllers')
            if type(controllers) is not list or len(controllers) > 128:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            names=[]
            for item in controllers:
                if (type(item) is not dict or type(item.get('identity')) is not str or
                        not re.fullmatch(r'coolify-[A-Za-z0-9_-]{1,116}', item['identity'])):
                    raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
                names.append({'identity': item['identity']})
            deployment = response.get('deployment') or {}
            binding = deployment.get('coolify') or {}
            source = binding.get('source') or ''
            if type(source) is not str:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            selected = source.rsplit('.',1)[-1] if source else ''
            deployed = deployment.get('deployment') or {}
            if (type(source) is not str or type(deployed) is not dict or
                    (selected and selected not in [c['identity'] for c in names])):
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            app = deployed.get('application_uuid') or ''
            if type(app) is not str or len(app) > 128:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            # The canonical Mother record is not the live operational record.
            # A verified undeploy receipt is reflected through the SLH manager.
            phase = response.get('deployment_phase', 'NOT_CONFIGURED' if not selected else 'TARGET_SELECTED')
            if type(phase) is not str or len(phase) > 100:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
            return {'generation': generation, 'selectedController': selected,
                    'applicationUuid': app, 'deploymentStatus': phase,
                    'controllers': names}
        observation = request('GET',root)
        generation=observation.get('generation')
        if type(generation) is not int or generation < 1:
            raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
        if operation == 'register':
            mutation = ('/controllers', {'name': arguments['name'], 'url': arguments['url'],
                         'api_token': arguments['apiToken'], 'expected_generation': generation,
                         'operation_id': 'mcel-coolify-' + secrets.token_hex(12)})
        elif operation == 'select':
            mutation = ('/selection', {'name': arguments['name'],
                         'expected_generation': generation,
                         'operation_id': 'mcel-selection-' + secrets.token_hex(12)})
        else:
            harness_action, execute = _HARNESS[operation]
            if execute:
                preview = request('POST', root+'/harness', {'action': harness_action,
                          'execute': False, 'expected_generation': None, 'confirm': None})
                if preview.get('status') != 'PLAN_ONLY' or (
                        harness_action == 'undeploy-seh' and
                        preview.get('ready_for_undeploy') is not True):
                    raise SEHDeploymentUIError('SEH_DEPLOYMENT_PREFLIGHT_REFUSED', 409)
            mutation = ('/harness', {'action': harness_action,
                       'execute': execute, 'expected_generation': generation if execute else None,
                       'confirm': ('DEPLOY_SEH' if harness_action == 'deploy-seh' else 'UNDEPLOY_SEH')
                                  if execute else None})
        result = request('POST', root+mutation[0], mutation[1])
        if type(result) is not dict or type(result.get('status')) is not str:
            raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
        status = result['status']
        # A queued build is NOT a verified connection. Report the actual
        # harness state, never fabricate a successful deployment.
        if len(status) > 100:
            raise SEHDeploymentUIError('SEH_DEPLOYMENT_RESPONSE_INVALID', 502)
        outcome = ('unknown' if status == 'INCOMPLETE' else
                   'refused' if status == 'REFUSED' else
                   'accepted-deferred' if status in ('REPLACEMENT_QUEUED_NOT_VERIFIED',
                                                     'DEPLOYMENT_IN_PROGRESS') else status)
        message = ('Operation incomplete; inspect recovery receipt before retrying' if outcome == 'unknown' else
                   'SEH harness refused the operation' if outcome == 'refused' else
                   'Build submitted but not yet verified' if outcome == 'accepted-deferred' else
                   status.replace('_',' ').title())
        return {'outcome':outcome,'message':message,
                'applicationUuid': str(result.get('application_uuid') or '')[:128]}
    except (WorkerBridgeError, NetworkConnectionError) as exc:
        raise SEHDeploymentUIError(exc.code, exc.status) from None
