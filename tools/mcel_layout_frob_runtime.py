"""Fixture-only Task Manager activity/effect boundary witnesses. Never calls live services.

These are contract experiments, not evidence of production worker/AI event integration.
"""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import re

STATES = ('submitted', 'acknowledged', 'running', 'succeeded', 'failed', 'canceled', 'unknown')
FINAL = {'succeeded', 'failed', 'canceled'}


def process_identity(row):
    if not isinstance(row.get('pid'), int) or row['pid'] <= 0 or not row.get('created_at'):
        return None
    return f"{row['pid']}@{row['created_at']}"


def classify_python(row):
    text = ' '.join(str(row.get(k) or '') for k in ('name', 'executable', 'cmdline')).lower()
    if not re.search(r'(^|[\\/\s])python(?:w|3(?:\.\d+)?)?(?:\.exe)?(?:\s|$)', text):
        return None
    # A command substring is not authoritative process ownership.
    if row.get('verified_main_computer_worker') is True:
        return {'classification': 'main_computer', 'evidence': 'verified_worker_identity'}
    if row.get('verified_external_origin') is True:
        return {'classification': 'other', 'evidence': 'verified_external_origin'}
    return {'classification': 'unknown', 'evidence': 'insufficient_provenance'}


def port_owners(connections, port, protocol='tcp'):
    if not isinstance(port, int) or not 0 < port < 65536:
        raise ValueError('Invalid port')
    matches = [c for c in connections if c.get('local_port') == port and c.get('protocol', 'tcp').lower() == protocol]
    return [{'port': port, 'protocol': protocol, 'pid': c.get('pid'),
             'identity': process_identity(c.get('process', {})),
             'ownership': 'verified' if process_identity(c.get('process', {})) else 'unknown'} for c in matches]


def redact_ai_call(event):
    allowed = {'request_id', 'attempt_id', 'origin', 'provider', 'model', 'state', 'started_at', 'last_activity_at', 'finished_at', 'latency', 'token_usage_if_available', 'error'}
    return {k: v for k, v in event.items() if k in allowed}


class FixtureOperationStore:
    """In-memory model, not a production dispatcher or wire protocol."""
    def __init__(self):
        self.items = {}
        self.effects = []

    def request(self, *, token, target, action, authorized, confirmed=False, destructive=False):
        if not token or not target:
            raise ValueError('Missing idempotency or target identity')
        if token in self.items:
            prior = self.items[token]
            if (prior['target'], prior['action']) != (target, action):
                raise ValueError('Idempotency token reused for different action')
            return copy.deepcopy(prior)
        if not authorized or destructive and not confirmed:
            return {'status': 'refused', 'reason': 'authorization_or_confirmation_missing'}
        row = {'id': token, 'target': target, 'action': action, 'state': 'submitted', 'events': ['submitted']}
        self.items[token] = row
        self.effects.append({'target': target, 'action': action, 'token': token})
        return copy.deepcopy(row)

    def advance(self, token, state):
        if state not in STATES:
            raise ValueError('Unknown state')
        row = self.items[token]
        if row['state'] in FINAL:
            raise ValueError('Cannot change terminal outcome without reconciliation')
        allowed = {'submitted': {'acknowledged', 'unknown', 'failed'},
                   'acknowledged': {'running', 'unknown', 'failed'},
                   'running': {'succeeded', 'failed', 'canceled', 'unknown'},
                   'unknown': {'acknowledged', 'running', 'succeeded', 'failed', 'canceled'}}
        if state not in allowed.get(row['state'], set()):
            raise ValueError(f'Invalid transition: {row["state"]} -> {state}')
        row['state'] = state
        row['events'].append(state)
        return copy.deepcopy(row)

    def terminate(self, *, token, expected_pid, expected_started, actual, authorized, confirmed):
        identity = process_identity(actual)
        expected = f'{expected_pid}@{expected_started}'
        if identity != expected:
            return {'status': 'refused', 'reason': 'process_identity_changed'}
        return self.request(token=token, target=expected, action='terminate',
                            authorized=authorized, confirmed=confirmed, destructive=True)


def exercise_fixture_boundary():
    store = FixtureOperationStore()
    row = {'pid': 431, 'created_at': '2026-10-24T18:00:00Z'}
    accepted = store.terminate(token='alpha', expected_pid=431, expected_started=row['created_at'],actual=row,authorized=True,confirmed=True)
    acknowledged = store.advance('alpha', 'acknowledged')
    running = store.advance('alpha', 'running')
    duplicate = store.terminate(token='alpha', expected_pid=431, expected_started=row['created_at'],actual=row,authorized=True,confirmed=True)
    unknown = store.advance('alpha', 'unknown')
    final = store.advance('alpha', 'succeeded')
    reused = store.terminate(token='beta', expected_pid=431, expected_started=row['created_at'],actual={'pid':431,'created_at':'2026-10-24T18:10:00Z'},authorized=True,confirmed=True)
    refused = store.request(token='gamma', target='different', action='kill', authorized=False, confirmed=False, destructive=True)
    python_cases = [classify_python({'name':'python.exe', 'cmdline':'python worker.py', 'verified_main_computer_worker':True}),
                    classify_python({'name':'python.exe', 'cmdline':'python script.py', 'verified_external_origin':True}),
                    classify_python({'name':'python.exe', 'cmdline':'python main_computer/worker.py'})]
    owners = port_owners([{'local_port':8765,'pid':431,'process':row}, {'local_port':8765,'pid':None}], 8765)
    ai = redact_ai_call({'request_id':'req1','state':'running','model':'local', 'prompt':'SECRET', 'api_key':'SECRET', 'raw_result':'SECRET'})
    schedule = {'id': 'schedule-1', 'action': 'restart', 'state': 'stored', 'dispatch_evidence': None}
    checks = {
        'receipt_then_running_then_verified_final': accepted['state']=='submitted' and acknowledged['state']=='acknowledged' and running['state']=='running' and final['state']=='succeeded',
        'unknown_not_success': unknown['state']=='unknown',
        'duplicate_retry_has_one_effect': duplicate['id']=='alpha' and len(store.effects)==1,
        'reused_pid_refused': reused['status']=='refused' and len(store.effects)==1,
        'unauthorized_effect_refused': refused['status']=='refused' and len(store.effects)==1,
        'three_class_provenance': [x['classification'] for x in python_cases]==['main_computer','other','unknown'],
        'port_unknown_owner_not_fabricated': len(owners)==2 and owners[0]['ownership']=='verified' and owners[1]['ownership']=='unknown',
        'ai_default_redaction': not any(k in ai for k in ('prompt','api_key','raw_result')),
        'stored_schedule_not_executed': schedule['state']=='stored' and schedule['dispatch_evidence'] is None,
        'no_production_effect': len(store.effects)==1 and all(e['target']=='431@2026-10-24T18:00:00Z' for e in store.effects),
    }
    return {'scope': 'fixture-only semantic/effect model; no production integration',
            'status': 'PASS' if all(checks.values()) else 'FAIL', 'checks': checks,
            'event_history': store.items['alpha']['events'], 'mock_effect_count': len(store.effects),
            'python_cases': python_cases, 'port_owners': owners, 'redacted_ai': ai}
