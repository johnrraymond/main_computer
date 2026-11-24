"""Adversarial requester spend enforcement at the real backend submission boundary.

No Hub call, wallet, credit purchase, or production settings change is performed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_computer.credit_units import CREDIT_WEI_PER_CREDIT as W
from main_computer.remote_overflow import RemoteHubExecutionGateway, RemoteOverflowAssessment
from main_computer.requester_spend_guard import (
    RequesterSpendDenied, RequesterSpendLedger, requester_policy, request_digest,
    verify_isolated_backend_guard,
)
from main_computer.viewport_routes_chat_console import ViewportChatConsoleRoutesMixin


class StubCapacity:
    busy = True

    def local_ai_capacity_snapshot(self, **kwargs):
        return {'ok': True, 'busy': self.busy, 'available_now': not self.busy}


class StubViewport(ViewportChatConsoleRoutesMixin):
    def __init__(self, root: Path):
        self.settings = {
            'selectedNetwork': 'dev', 'workerAutoConnectNetwork': 'dev',
            'remoteEnabled': True, 'remoteMode': 'ask-when-busy',
            'remoteDailyLimit': 1, 'remoteAskBeforeSpend': True,
            'remoteOnlyWhenBusy': True, 'remoteCreditsPerToken': '0.1',
            'remoteMaxOutputTokens': 2,
        }
        self.capacity = StubCapacity()
        self.server = SimpleNamespace(
            debug_root=root, chat_ai_processes=self.capacity,
            config=SimpleNamespace(hub_url='http://127.0.0.1:8871', hub_network='dev'),
            signal=lambda *a, **kw: None,
        )
        self.body = {}
        self.response = None

    def _chat_console_load_worker_settings(self):
        return dict(self.settings)

    def _chat_console_worker_multisession_cache(self):
        return {'keys': {}}

    def _chat_console_backend_hub_readiness(self, body):
        # Explicitly fake Hub readiness so no real Hub is contacted.
        return {'ready': True, 'credit_ready': True, 'hub_reachable': True,
                'paid_overflow_enabled': True, 'account': {'available_credit_wei': str(100 * W)},
                'wallet_address': '', 'account_id': '', 'reason_code': 'test_fixture_ready'}

    def _chat_console_remote_hub_submit_engine(self, body):
        assessment = RemoteOverflowAssessment(
            status='authorization_required', action='authorization_required',
            reason_code='remote_authorization_required', user_message='test',
            authorization_required=True, offer_remote=True, cards=[],
        )
        return SimpleNamespace(assess=lambda b: assessment)

    def _read_json(self):
        return dict(self.body)

    def _send_json(self, result, **kw):
        self.response = (result, kw)

    def submit(self, **changes):
        self.body = {**payload(), **changes}
        self._handle_chat_console_remote_overflow_hub_submit()
        return self.response[0]

    def approve(self, **changes):
        self.body = {**payload(), **changes}
        self._handle_chat_console_requester_authorize()
        return self.response[0]


def payload():
    return {'remote_hub_current_request': True, 'remote_worker_intent_mode': 'remote_once',
            'pending_request_id': 'pending-1', 'thread_id': 'thread-1',
            'prompt': 'hello', 'local_only': False, 'authorization_granted_by_user': True,
            'remote_enabled': True, 'remote_daily_limit_credit_wei': str(10**40),
            'credit': {'daily_remote_limit_credit_wei': str(10**40), 'daily_remote_used_credit_wei': '0',
                       'spendable_credit_wei': str(10**40)}}


def test_backend_denies_forged_authorization_and_never_calls_hub(tmp_path, monkeypatch):
    view = StubViewport(tmp_path)
    monkeypatch.setattr(RemoteHubExecutionGateway, 'run', lambda *a, **kw: pytest.fail('Hub contacted'))
    result = view.submit()
    assert result['status'] == 'blocked'
    assert result['reason_code'] == 'backend_spend_authorization_required'
    assert view._chat_console_requester_ledger().remaining(limit_wei=W)[1] == 0


@pytest.mark.parametrize('name,value,reason', [
    ('remoteEnabled', False, 'remote_overflow_disabled'),
    ('remoteDailyLimit', 0, 'daily_remote_limit_exceeded'),
    ('remoteMode', 'never', 'remote_overflow_disabled'),
])
def test_backend_policy_wins_over_browser_claims(tmp_path, monkeypatch, name, value, reason):
    view = StubViewport(tmp_path)
    view.settings[name] = value
    monkeypatch.setattr(RemoteHubExecutionGateway, 'run', lambda *a, **kw: pytest.fail('Hub contacted'))
    result = view.submit()
    assert result['status'] == 'blocked'
    assert result['reason_code'] == reason


def test_local_capacity_is_measured_by_server_not_client(tmp_path, monkeypatch):
    view = StubViewport(tmp_path)
    view.capacity.busy = False
    monkeypatch.setattr(RemoteHubExecutionGateway, 'run', lambda *a, **kw: pytest.fail('Hub contacted'))
    result = view.submit(local_capacity_snapshot={'ok': True, 'busy': True, 'available_now': False})
    assert result['reason_code'] == 'remote_only_when_busy'


def test_approval_is_one_shot_bound_to_exact_request_and_real_gateway_path(tmp_path, monkeypatch):
    view = StubViewport(tmp_path)
    calls = []
    def fake_run(_gateway, body, assessment):
        calls.append(body)
        return {'ok': True, 'status': 'completed', 'simulated': True,
                'remote_overflow_result': {'simulated': True, 'payment': None}}
    monkeypatch.setattr(RemoteHubExecutionGateway, 'run', fake_run)
    grant = view.approve()['spend_authorization_token']
    # If the price changes after authorization, its hash changes.
    view.settings['remoteCreditsPerToken'] = '0.2'
    denied = view.submit(spend_authorization_token=grant)
    assert denied['reason_code'] == 'backend_spend_authorization_required'
    assert not calls
    view.settings['remoteCreditsPerToken'] = '0.1'
    success = view.submit(spend_authorization_token=grant)
    assert success['status'] == 'completed'
    assert len(calls) == 1
    # Simulation actually incurred no charge; reservation is released, but
    # neither approval nor request-id may be replayed.
    assert view._chat_console_requester_ledger().remaining(limit_wei=W)[1] == 0
    assert view.submit(spend_authorization_token=grant)['reason_code'] == 'duplicate_remote_request'
    assert len(calls) == 1


def test_automatic_intent_cannot_silently_mint_per_request_consent(tmp_path):
    view = StubViewport(tmp_path)
    result = view.approve(remote_worker_intent_mode='remote_when_needed_global')
    assert result['reason_code'] == 'explicit_request_approval_required'


def test_ask_mode_requires_consent_even_if_checkbox_is_off(tmp_path):
    ledger = RequesterSpendLedger(tmp_path / 'ledger.sqlite')
    policy = requester_policy({'remoteEnabled': True, 'remoteAskBeforeSpend': False,
                               'remoteMode': 'ask-when-busy', 'remoteDailyLimit': 1})
    with pytest.raises(RequesterSpendDenied, match='backend_spend_authorization_required'):
        ledger.reserve(request_id='ask', digest='ask', policy=policy,
                       required_wei=W // 2, local_busy=True)
    auto_policy = dict(policy, mode='auto-when-busy')
    ledger.reserve(request_id='auto', digest='auto', policy=auto_policy,
                   required_wei=W // 2, local_busy=True)


def test_auth_token_is_not_the_same_as_client_claim(tmp_path):
    view = StubViewport(tmp_path)
    grant = view.approve()['spend_authorization_token']
    assert grant != 'true' and len(grant) > 32
    ctx, policy, amount = view._chat_console_requester_guard_context(payload())
    assert request_digest(payload(), ctx) == request_digest(dict(payload(), authorization_granted_by_user=False), ctx)
    ledger = view._chat_console_requester_ledger()
    with pytest.raises(RequesterSpendDenied, match='backend_spend_authorization_required'):
        ledger.reserve(request_id='forged', digest='wrong-digest', policy=policy,
                       required_wei=amount, local_busy=True, consent_token=grant)


def test_atomic_budget_concurrency_and_restart(tmp_path):
    ledger = RequesterSpendLedger(tmp_path / 'ledger.sqlite')
    policy = requester_policy({'remoteEnabled': True, 'remoteAskBeforeSpend': False,
                               'remoteOnlyWhenBusy': True, 'remoteDailyLimit': 1, 'remoteMode': 'auto-when-busy'})
    def attempt(idx):
        try:
            ledger.reserve(request_id=f'r{idx}', digest=f'h{idx}', required_wei=3*W//4,
                           policy=policy, local_busy=True)
            return 'reserved'
        except RequesterSpendDenied as exc:
            return exc.reason_code
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(attempt, range(12)))
    assert results.count('reserved') == 1
    assert results.count('daily_remote_limit_exceeded') == 11
    assert RequesterSpendLedger(tmp_path / 'ledger.sqlite').remaining(limit_wei=W)[1] == 3 * W // 4


def test_unknown_outcome_keeps_hold_and_refuses_excess_charge(tmp_path):
    ledger = RequesterSpendLedger(tmp_path / 'ledger.sqlite')
    policy = requester_policy({'remoteEnabled': True, 'remoteAskBeforeSpend': False, 'remoteDailyLimit': 1, 'remoteMode': 'auto-when-busy'})
    ledger.reserve(request_id='first', digest='x', policy=policy, required_wei=3*W//4, local_busy=True)
    with pytest.raises(RequesterSpendDenied, match='charge_reconciliation_unverified'):
        ledger.settle('first', charged_wei=W)
    with pytest.raises(RequesterSpendDenied, match='daily_remote_limit_exceeded'):
        ledger.reserve(request_id='second', digest='y', policy=policy, required_wei=W//2, local_busy=True)
    assert RequesterSpendLedger(tmp_path / 'ledger.sqlite').remaining(limit_wei=W)[1] == 3 * W // 4


def test_isolated_backend_verifier_proves_all_required_conditions():
    assert all(verify_isolated_backend_guard().values())
