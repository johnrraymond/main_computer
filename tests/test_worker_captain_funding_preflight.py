"""No network access, private state, or deposits in these funding-unit tests."""
import pytest

from tools.worker_captain_funding_preflight import assess_funding, FundingEvidenceError

U = 10**18
WALLET = '0x' + 'a' * 40


def ledger(available, completed, *, wallet=WALLET):
    return {'ok': True, 'wallet_address': wallet, 'account': {
        'wallet_address': wallet, 'owner_address': wallet,
        'available_credit_wei': str(available),
        'bridge_completed_credit_wei': str(completed),
    }}


def test_historical_held_credit_blocks_further_spending_after_cap():
    state = assess_funding(ledger(976 * U // 1000, 2 * U),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is False
    assert state['bridgeFundingRequired'] is False
    assert state['reason'] == 'insufficient_credit_under_cumulative_cap'
    assert state['possibleTopUpCreditWei'] == '0'


def test_existing_funds_prevent_any_new_bridge():
    state = assess_funding(ledger(1500 * U // 1000, 1600 * U // 1000),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is True
    assert state['verdict'] == 'existing_credits_sufficient'
    assert state['bridgeFundingRequired'] is False


def test_bounded_top_up_possible_but_not_guaranteed():
    state = assess_funding(ledger(1000 * U // 1000, 1500 * U // 1000),
                           wallet_address=WALLET, required_credit_wei=1024 * U // 1000,
                           ceiling_credits=2)
    assert state['fundable'] is True
    assert state['bridgeFundingRequired'] is True
    assert state['possibleTopUpCreditWei'] == str(500 * U // 1000)


def test_one_credit_mode_does_not_bridge():
    state = assess_funding(ledger(100 * U // 1000, U), wallet_address=WALLET,
                           required_credit_wei=900 * U // 1000, ceiling_credits=1)
    assert not state['fundable']
    assert state['possibleTopUpCreditWei'] == '0'


@pytest.mark.parametrize('untrusted', [
    ledger(1, 2, wallet='0x' + 'b'*40),
    ledger('not-a-number', 0),
    {'ok': True, 'wallet_address': WALLET},
    {'ok': False, 'wallet_address': WALLET},
    ledger(-1, 0),
])
def test_malformed_or_cross_wallet_hub_ledger_fails_closed(untrusted):
    with pytest.raises(FundingEvidenceError, match='invalid_hub_credit_evidence'):
        assess_funding(untrusted, wallet_address=WALLET,
                       required_credit_wei=U, ceiling_credits=1)


def test_invalid_prices_fail_closed():
    for required, ceiling in [(0, 1), (U + 1, 1), (2 * U + 1, 2), (1, 3)]:
        with pytest.raises(FundingEvidenceError):
            assess_funding(ledger(U, U), wallet_address=WALLET,
                           required_credit_wei=required, ceiling_credits=ceiling)



def _stub_live_captain(monkeypatch):
    """No private state or network: fake only the already established Captain identity."""
    from types import SimpleNamespace
    import tools.worker_captain_funding_preflight as funding
    runtime = SimpleNamespace(
        network='testnet', chain_id=42424241,
        config=SimpleNamespace(hub_url=funding.TESTNET_HUB),
        wallet=SimpleNamespace(address=WALLET, private_key='not-a-real-key'),
    )
    monkeypatch.setattr(funding, 'MainComputerConfig', SimpleNamespace(from_env=lambda: object()))
    monkeypatch.setattr(funding, '_apply_captain_network_defaults', lambda *a, **k: None)
    monkeypatch.setattr(funding, 'build_captain_runtime', lambda *a, **k: runtime)
    return funding, runtime


def test_live_ledger_requires_explicit_captain_signing_permission(monkeypatch, tmp_path):
    funding, runtime = _stub_live_captain(monkeypatch)
    def reject(*args, **kwargs):
        raise AssertionError('unauthorized preflight must not access the ledger or issue MSK')
    monkeypatch.setattr(funding, '_fetch_hub_credit_balance', reject)
    import main_computer.hub_cli_live_session as session
    monkeypatch.setattr(session, 'identify_hub_protocol', reject)
    monkeypatch.setattr(session, 'issue_cli_multisession_authorization', reject)
    with pytest.raises(FundingEvidenceError, match='captain_wallet_signing_not_authorized'):
        funding.probe_live(required_credit_wei=512 * U // 1000,
                           ceiling_credits=1, repo_root=tmp_path)


def test_live_ledger_uses_verified_captain_msk_for_balance(monkeypatch, tmp_path):
    funding, runtime = _stub_live_captain(monkeypatch)
    import main_computer.hub_cli_live_session as session
    identity={'service':'main_computer.stable_hub','network':{'chain_id':'42424241'},
              'cluster_id':'main-computer-testnet-hubs'}
    seen=[]
    monkeypatch.setattr(session, 'identify_hub_protocol',
                        lambda url, **kw: identity)
    def authorize(url, *, wallet, stable_identity, timeout_s):
        assert url == funding.TESTNET_HUB
        assert wallet is runtime.wallet and stable_identity is identity
        seen.append('authorized')
        return {'multisession_key_id':'opaque-valid-msk-id'}
    monkeypatch.setattr(session, 'issue_cli_multisession_authorization', authorize)
    def read_balance(url, *, wallet_address, timeout_s, authorization):
        assert url == funding.TESTNET_HUB and wallet_address == WALLET
        assert authorization == {'multisession_key_id':'opaque-valid-msk-id'}
        seen.append('balance')
        return ledger(U, U)
    monkeypatch.setattr(funding, '_fetch_hub_credit_balance', read_balance)
    result=funding.probe_live(required_credit_wei=512 * U // 1000,
                              ceiling_credits=1,repo_root=tmp_path,
                              authorize_captain_wallet_signing=True)
    assert seen==['authorized','balance']
    assert result['fundable'] is True
    assert result['verdict']=='existing_credits_sufficient'
    assert result['bridgeFundingRequired'] is False


def test_live_ledger_authorization_failure_is_not_misreported_as_ledger_outage(monkeypatch, tmp_path):
    funding, _ = _stub_live_captain(monkeypatch)
    import main_computer.hub_cli_live_session as session
    monkeypatch.setattr(session, 'identify_hub_protocol', lambda *a, **k:
                        {'service':'main_computer.stable_hub','network':{'chain_id':'42424241'}})
    def deny(*args, **kwargs):
        raise RuntimeError('test-only authorization failure')
    monkeypatch.setattr(session, 'issue_cli_multisession_authorization', deny)
    with pytest.raises(FundingEvidenceError, match='captain_msk_authorization_unavailable'):
        funding.probe_live(required_credit_wei=512 * U // 1000,
                           ceiling_credits=1,repo_root=tmp_path,
                           authorize_captain_wallet_signing=True)
