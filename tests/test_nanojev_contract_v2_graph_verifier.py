"""Adversarial checks of the general Contract Language v2 graph audit.

Reference checker below is an independently written fixed-point graph solver.
The production analyzer uses forward BFS plus per-scenario recovery BFS.
This tests mathematical agreement without using the production traversal.
The historical liveness checker supplies a third implementation for comparison.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
import copy
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest

from tools import nanojev_contract_v2_verifier as verifier
from tools import nanojev_contract_pull_payments as v2
from tools import nanojev_contract_liveness as prior
from tools import nanojev_contract_pull_smoke as runner
from tools.nanojev_contract_solidity_smoke import SmokeFailure


PARTIES = ('payer', 'worker', 'reviewer')
CLOCKS = ('before_deadline', 'at_or_after_deadline')


def reference(spec):
    """Fixed-point model checking, *not* the verifier's queue/recovery algorithm."""
    states = {'funded'}
    terminal = set()
    for r in spec['rules']:
        states.update((r['from'], r['to']))
        if r['payments']:
            terminal.add(r['to'])
    universe = {(state, when) for state in states for when in CLOCKS}

    def choices(node, absent):
        state, clock = node
        moves = set()
        if clock == CLOCKS[0]:
            moves.add(((state, CLOCKS[1]), False))
        for rule in spec['rules']:
            if rule['from'] != state or rule['actor'] == absent:
                continue
            if rule['when'] != 'always' and rule['when'] != clock:
                continue
            moves.add(((rule['to'], clock), bool(rule['payments'])))
        return moves

    reached = {('funded', CLOCKS[0])}
    while True:
        grow = set(reached)
        for node in reached:
            grow.update(target for target, _settles in choices(node, None))
        if grow == reached:
            break
        reached = grow
    assert reached <= universe

    def fixed_point(absent):
        # Backwards least-fixed-point: existential path to any payout transition.
        winning = set()
        while True:
            next_set = set(winning)
            for node in universe:
                if any(is_settlement or destination in winning
                       for destination, is_settlement in choices(node, absent)):
                    next_set.add(node)
            if next_set == winning:
                return winning
            winning = next_set

    traps = set()
    for node in reached:
        if node[0] in terminal:
            continue
        for role in PARTIES:
            if node not in fixed_point(role):
                traps.add((*node, role))
    none_wins = fixed_point(None)
    unconditional = {node for node in reached if node[0] not in terminal and node not in none_wins}

    conflicts = set()
    for state, clock in reached:
        if state in terminal:
            continue
        applicable = [r for r in spec['rules'] if r['from'] == state and
                      (r['when'] == 'always' or r['when'] == clock)]
        for a, b in combinations(applicable, 2):
            # A reference outcome is (next_state, exact recipient credit map).
            def outcome(rule):
                payout = rule['payments']
                amount = spec['amount_wei']
                allocations = {}
                rem = amount
                for idx, x in enumerate(payout):
                    exact = int(Fraction(amount * x['bps'], 10000)) if idx < len(payout)-1 else rem
                    rem -= exact
                    allocations[x['to']] = exact
                return (rule['to'], tuple(sorted(allocations.items())))
            if outcome(a) != outcome(b):
                conflicts.add((state, clock, tuple(sorted((a['name'], b['name'])))))
    return {'reachable': reached, 'traps': traps,
            'unconditional': unconditional, 'divergent': conflicts}


def cases():
    return [x for x in runner.cases() if x['family'] != 'needs_clarification']


def check_against_references(spec):
    spec = v2.validate(spec)
    got = verifier.verify(spec)
    ref = reference(spec)
    seen = {(x['state'], x['phase']) for x in got['graph']['reachable_nodes']}
    assert seen == ref['reachable']
    trapped = {(x['state'], x['phase'], x['unavailable_role'])
               for x in got['liveness']['single_role_traps']}
    assert trapped == ref['traps']
    unconditional = {(x['state'], x['phase']) for x in got['liveness']['unconditional_traps']}
    assert unconditional == ref['unconditional']
    divergent = {(x['state'], x['phase'], tuple(x['actions']))
                 for x in got['conflicts']['divergent']}
    assert divergent == ref['divergent']
    # Historical liveness engine uses independently implemented graph traversal.
    old = prior.evaluate(v2.old(spec))
    old_traps = {(x['state'], x['phase'], x['unavailable_role']) for x in old['single_role_traps']}
    assert old_traps == trapped
    assert len(old['unconditional_traps']) == len(unconditional)
    assert got['liveness']['checked_single_role_scenarios'] == (
        got['graph']['unsettled_count'] * 3)
    assert got['conservation']['status'] == 'PASS_IR'
    assert got['authorization']['status'] == 'PASS_IR'
    for payout in got['conservation']['terminal_settlements']:
        assert payout['credited_total_wei'] == spec['amount_wei']
        assert all(v > 0 for v in payout['credits_wei'].values())
    assert got['emission_authorized_by_graph_audit'] is (
        not trapped and not unconditional and not divergent)
    return got


@pytest.mark.parametrize('case', cases(), ids=lambda c:c['id'])
def test_existing_live_contracts_match_both_independent_reference_implementations(case):
    result = check_against_references(case['expected'])
    if case['family'] == 'unsafe_compilable':
        assert result['status'] == 'BLOCKED_BY_V2_GRAPH_AUDIT'
        assert result['liveness']['single_role_traps']
    else:
        assert result['status'] == 'PASS_CONDITIONAL'


def test_divergent_concurrent_settlement_is_flagged_not_misclassified_as_double_spend():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['rules'] = [
        {'name':'refund','actor':'payer','from':'funded','to':'refunded',
         'when':'always','payments':[{'to':'payer','bps':10000}]},
        {'name':'release','actor':'reviewer','from':'funded','to':'settled',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
    ]
    result = check_against_references(spec)
    assert result['liveness']['status'] == 'PASS_CONDITIONAL'
    assert result['status'] == 'BLOCKED_BY_V2_GRAPH_AUDIT'
    assert len(result['conflicts']['divergent']) == 2
    assert 'DIVERGENT_SAME_PHASE_TRANSITION_RACE_REQUIRES_INTENT_REVIEW' in result['blockers']
    assert not result['liveness']['single_role_traps']


def test_same_outcome_separate_actors_is_not_divergent():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['rules'] = [
        {'name':'release','actor':'payer','from':'funded','to':'settled',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
        {'name':'rescue','actor':'reviewer','from':'funded','to':'settled',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
    ]
    result = check_against_references(spec)
    assert result['status'] == 'PASS_CONDITIONAL'
    assert len(result['conflicts']['equivalent_effects']) == 2


def test_deadline_cutoff_leaves_a_real_trapped_phase():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['rules'] = [
        {'name':'early_release','actor':'payer','from':'funded','to':'settled',
         'when':'before_deadline','payments':[{'to':'worker','bps':10000}]},
        {'name':'early_refund','actor':'reviewer','from':'funded','to':'refunded',
         'when':'before_deadline','payments':[{'to':'payer','bps':10000}]},
    ]
    result = check_against_references(spec)
    assert any(x['phase']=='at_or_after_deadline' for x in result['liveness']['unconditional_traps'])
    assert any(x['phase']=='at_or_after_deadline' for x in result['liveness']['single_role_traps'])


def test_full_liveness_does_not_override_semantic_race():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['rules'] = [
        {'name':'alpha','actor':'payer','from':'funded','to':'a',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
        {'name':'bravo','actor':'reviewer','from':'funded','to':'b',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
    ]
    res=check_against_references(spec)
    assert res['status']=='BLOCKED_BY_V2_GRAPH_AUDIT'  # observable terminal-state difference


def test_clock_guard_never_mistaken_for_automatic_settlement():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['rules'] = [
        {'name':'before','actor':'payer','from':'funded','to':'settled',
         'when':'before_deadline','payments':[{'to':'worker','bps':10000}]},
        {'name':'after','actor':'payer','from':'funded','to':'settled',
         'when':'at_or_after_deadline','payments':[{'to':'worker','bps':10000}]},
    ]
    res=check_against_references(spec)
    assert res['liveness']['status']=='FAIL_TRAPPED_FUNDS_RISK'
    assert res['liveness']['single_role_traps']  # payer absent, no other caller


def test_progressive_payout_requires_new_language_not_v2():
    spec=copy.deepcopy(cases()[2]['expected'])
    # Claiming the whole deposit while 'funded' continues is unsafe and invalid.
    spec['rules'][0]['payments']=[{'to':'worker','bps':5000}]
    with pytest.raises(SmokeFailure):
        verifier.verify(spec)


@pytest.mark.parametrize('tamper', [
    lambda s: s.update(amount_wei=True),
    lambda s: s.update(settlement_mode='push'),
    lambda s: s.update(language='nanojev.contract.state_machine.v3'),
    lambda s: s['rules'][0].update(actor='arbitrator'),
    lambda s: s['rules'][0].update(when='after_two_deadlines'),
    lambda s: s['rules'][0].update(name='withdraw'),
    lambda s: s['rules'][0].update(to='funded'),
    lambda s: s['rules'][0].update(payments=[{'to':'payer','bps':5000}]),
    lambda s: s['rules'].append(copy.deepcopy(s['rules'][0])),
    lambda s: s['rules'][0].update(**{'from':'unreachable'}),
])
def test_invalid_contracts_refused_before_graph_audit(tamper):
    specimen=copy.deepcopy(cases()[2]['expected'])
    tamper(specimen)
    with pytest.raises(SmokeFailure):
        verifier.verify(specimen)


def random_specs():
    rnd=random.Random(0xC0DE1831)
    for i in range(110):
        # Each case is built from generic v2 *primitives*, not a contract-family
        # template. Randomized role bindings, guards, states and payout ratios.
        amount=rnd.choice([10000, 19876, 4*10**18])
        recipient=rnd.choice(PARTIES)
        def rule(name,actor,src,dst,when,weights):
            return {'name':name,'actor':actor,'from':src,'to':dst,
                    'when':when,'payments':weights}
        def pay(who):
            if rnd.choice([True, False]):
                return [{'to':who,'bps':10000}]
            other=rnd.choice([x for x in PARTIES if x!=who])
            first=rnd.choice([3000,5000,7000])
            return [{'to':who,'bps':first}, {'to':other,'bps':10000-first}]
        branches=[]
        branches.append(rule('submit',rnd.choice(PARTIES),'funded','review',
                             rnd.choice(['always','before_deadline','at_or_after_deadline']),[]))
        branches.append(rule('approve',rnd.choice(PARTIES),'review','paid',
                             rnd.choice(['always','before_deadline','at_or_after_deadline']),
                             pay(recipient)))
        branches.append(rule('refund',rnd.choice(PARTIES),'funded','refunded',
                             rnd.choice(['always','before_deadline','at_or_after_deadline']),
                             pay('payer')))
        if rnd.choice([True,False]):
            branches.append(rule('review_rescue',rnd.choice(PARTIES),'review','paid',
                                 rnd.choice(['always','at_or_after_deadline']),
                                 pay(recipient)))
        if rnd.choice([True,False]):
            branches.append(rule('rescue',rnd.choice(PARTIES),'funded','refunded',
                                 rnd.choice(['always','at_or_after_deadline']),
                                 pay('payer')))
        yield {'language':v2.LANG,'settlement_mode':'pull','asset':'native_eth',
               'payer':'deployer','worker':'constructor_worker','reviewer':'constructor_reviewer',
               'amount_wei':amount,'deadline_seconds':rnd.choice([1,3600,432000]),
               'rules':branches}


def test_110_seeded_valid_compositions_match_reference_engines():
    actual=0
    for spec in random_specs():
        result=check_against_references(spec)
        assert result['bounded_model']['rules'] in range(3,6)
        actual+=1
    assert actual==110


def test_exact_rounding_and_remainder_preserve_amount():
    spec=copy.deepcopy(cases()[2]['expected'])
    spec['amount_wei']=10001
    # Replace all payment branches with 3 nonzero fractional shares.
    for rule in spec['rules']:
        if rule['payments']:
            rule['payments']=[{'to':'payer','bps':3333},
                              {'to':'worker','bps':3333},
                              {'to':'reviewer','bps':3334}]
    report=check_against_references(spec)
    for x in report['conservation']['terminal_settlements']:
        assert sorted(x['credits_wei'].values())==[3333,3334,3334]


def test_standalone_cli_valid_and_invalid_produces_machine_readable_fail_closed_report(tmp_path):
    spec=cases()[2]['expected']
    input_file=tmp_path/'contract.json'
    input_file.write_text(json.dumps(spec),encoding='utf-8')
    output=tmp_path/'report.json'
    p=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v2_verifier',
                      '--spec',str(input_file),'--output',str(output)],
                     capture_output=True,text=True,timeout=20)
    assert p.returncode==0, p.stderr
    data=json.loads(output.read_text())
    assert data['status']=='PASS_CONDITIONAL'
    assert data['emission_authorized_by_graph_audit'] is True
    assert data['graph']['reachable_count']>0
    assert data['unverified']
    # Existing reports cannot be overwritten, including the source.
    p2=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v2_verifier',
                      '--spec',str(input_file),'--output',str(input_file)],
                     capture_output=True,text=True,timeout=20)
    assert p2.returncode!=0
    assert json.loads(input_file.read_text())==spec
    bad=tmp_path/'bad.json'
    bad.write_text('{ malformed',encoding='utf-8')
    error=tmp_path/'error.json'
    p3=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v2_verifier',
                      '--spec',str(bad),'--output',str(error)],
                     capture_output=True,text=True,timeout=20)
    assert p3.returncode!=0
    assert json.loads(error.read_text())['status']=='BLOCKED_INVALID_INPUT'


def test_replay_a_prior_live_report_fails_closed_if_recorded_gate_disagrees(tmp_path):
    approved=cases()[2]['expected']
    original={'schema':'nanojev.contract-pull-smoke.v1', 'model':'frozen-regression-model',
              'cases':[
                  {'id':'representable','llm_output':json.dumps({'status':'contract','contract':approved}),
                   'policy_action':'EMIT_CREDITED_SETTLEMENT'},
                  {'id':'not_applicable','llm_output':json.dumps({'status':'needs_clarification',
                   'questions':['Unsupported multi-stage escrow']}),
                   'policy_action':'REFUSE_UNDERSPECIFIED'},
              ]}
    good=verifier.verify_prior_report(original)
    assert good['status']=='PASS_REPLAY_IR_GRAPH_AUDIT_NOT_SEMANTIC_CERTIFIED'
    assert good['case_count']==2
    assert good['audited_contracts']==1
    assert good['refusals']==1
    assert good['gold_independence']=='NOT_ESTABLISHED'

    bad=copy.deepcopy(original)
    bad['cases'][0]['policy_action']='BLOCK_UNSAFE_CONTRACT'
    refused=verifier.verify_prior_report(bad)
    assert refused['status']=='BLOCKED_REPLAY_GATE_MISMATCH'
    assert refused['blockers'][0]['case']=='representable'

    variant=copy.deepcopy(approved)
    variant['rules'] = [
        {'name':'refund','actor':'payer','from':'funded','to':'refunded',
         'when':'always','payments':[{'to':'payer','bps':10000}]},
        {'name':'release','actor':'reviewer','from':'funded','to':'settled',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
    ]
    bad['cases'][0]['llm_output']=json.dumps({'status':'contract','contract':variant})
    bad['cases'][0]['policy_action']='EMIT_CREDITED_SETTLEMENT'
    failed=verifier.verify_prior_report(bad)
    assert failed['status']=='BLOCKED_REPLAY_GATE_MISMATCH'
    assert failed['cases'][0]['graph_audit']['conflicts']['divergent']
    assert 'DIVERGENT_SAME_PHASE_TRANSITION_RACE_REQUIRES_INTENT_REVIEW' in failed['blockers'][0]['blockers']

    report=tmp_path/'prior.json'
    output=tmp_path/'audited.json'
    report.write_text(json.dumps(original),encoding='utf-8')
    proc=subprocess.run([sys.executable,'-m','tools.nanojev_contract_v2_verifier',
                         '--from-report',str(report),'--output',str(output)],
                        text=True,capture_output=True,timeout=20)
    assert proc.returncode==0,proc.stderr
    assert json.loads(output.read_text())['audited_contracts']==1


@pytest.mark.parametrize('mutation', [
    lambda x: x.update(schema='unknown'),
    lambda x: x.update(cases=[]),
    lambda x: x['cases'][0].update(llm_output='{invalid'),
    lambda x: x['cases'][0].update(llm_output='false'),
    lambda x: x['cases'].append(copy.deepcopy(x['cases'][0])),
    lambda x: x['cases'][0].pop('llm_output'),
])
def test_prior_report_replay_refuses_malformed_or_forged_inputs(mutation):
    src={'schema':'nanojev.contract-pull-smoke.v1',
         'cases':[{'id':'first',
                   'llm_output':json.dumps({'status':'contract','contract':cases()[2]['expected']}),
                   'policy_action':'EMIT_CREDITED_SETTLEMENT'}]}
    mutation(src)
    with pytest.raises(SmokeFailure):
        verifier.verify_prior_report(src)
