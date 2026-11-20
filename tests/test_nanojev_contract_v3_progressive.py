"""v3 accounting kernel: generic tests, independent model, and adversarial cases.

No Forge/docker/network needed for these tests. The optional script runs Forge
on the user's machine and adds a separate pinned Solidity adversarial suite.
"""
from __future__ import annotations
from collections import deque
import copy
import json
from pathlib import Path
import random
import re

import pytest

from tools import nanojev_contract_v3_language as lang
from tools import nanojev_contract_v3_verifier as check
from tools import nanojev_contract_v3_solidity as emit
from tools import nanojev_contract_v3_reference as ref
from tools import nanojev_contract_v3_foundry as oracle

FIXTURE = Path(__file__).resolve().parents[1]/'tools/fixtures/nanojev_contract_v3_progressive_escrow_v1.json'


def sample():
    return json.loads(FIXTURE.read_text())


def audit(v):
    return check.verify(v)


def test_v3_progressive_positive_and_accounting_coverage():
    spec = sample()
    result = audit(spec)
    assert result['status'] == 'PASS_CONDITIONAL', result['blockers']
    assert result['emission_authorized_by_graph_audit']
    assert result['model']['reachable_nodes'] >= 20
    assert result['model']['partial_nonterminal_rules'] == ['approve_beta','approve_prototype']
    assert not result['liveness']['single_role_traps']
    assert not result['conservation']['violations']
    assert not result['conflicts']['divergent']


def test_reference_model_three_stages_and_withdrawal_order():
    machine = ref.ReferenceEscrow(sample())
    with pytest.raises(ref.ReferenceRevert):
        machine.execute('approve_prototype','payer')
    machine.fund('payer',12*10**18)
    machine.execute('approve_prototype','payer')
    assert machine.credits['worker'] == 3*10**18
    assert machine.unallocated == 9*10**18
    assert machine.withdraw('worker') == 3*10**18
    machine.execute('approve_beta','payer')
    assert machine.credits['worker'] == 4*10**18
    assert machine.unallocated == 5*10**18
    machine.execute('approve_final','payer')
    assert machine.state == 'completed' and machine.conserved()
    assert machine.withdraw('worker') == 9*10**18
    assert machine.paid_out['worker'] == 12*10**18
    assert machine.unallocated == 0 and machine.conserved()


def test_reference_refund_keeps_earned_credits_separate():
    machine = ref.ReferenceEscrow(sample())
    machine.fund('payer',12*10**18)
    machine.execute('approve_prototype','payer')
    machine.execute('approve_beta','payer')
    machine.tick_to_deadline()
    machine.execute('refund_beta_worker','worker')
    assert machine.state == 'refunded'
    assert (machine.credits['worker'],machine.credits['payer']) == (7*10**18,5*10**18)
    assert machine.conserved()
    assert machine.withdraw('payer') == 5*10**18
    assert machine.withdraw('worker') == 7*10**18
    assert machine.conserved()


def test_reference_rejected_withdrawal_is_atomic():
    machine = ref.ReferenceEscrow(sample())
    machine.fund('payer',12*10**18)
    machine.execute('approve_prototype','payer')
    initial = machine.snapshot()
    with pytest.raises(ref.ReferenceRevert):
        machine.withdraw('worker',destination_accepts=False)
    assert machine.snapshot() == initial


def test_differential_reference_exhaustive_reachable_ledger_nodes():
    spec=lang.validate(sample())
    paths={check.initial(spec):[]}
    queue=deque(paths)
    rules={r['name']:r for r in spec['rules']}
    while queue:
        cur=queue.popleft()
        m=ref.ReferenceEscrow(spec)
        m.fund('payer',spec['amount_wei'])
        for name in paths[cur]:
            if name=='wait_until_deadline':
                m.tick_to_deadline()
            elif name.startswith('withdraw:'):
                m.withdraw(name.split(':')[1])
            else:
                m.execute(name,rules[name]['actor'])
        assert m.snapshot() == (cur.state,cur.phase,cur.unallocated,cur.pending,cur.withdrawn)
        assert m.conserved()
        for label,nxt in check.successors(spec,cur):
            if nxt not in paths:
                paths[nxt]=paths[cur]+[label]
                queue.append(nxt)
    assert len(paths) == audit(spec)['model']['reachable_nodes']


def test_recovery_works_no_matter_which_single_role_absent():
    spec=lang.validate(sample())
    for absent in lang.ROLES:
        assert check.recovery(spec,check.initial(spec),lang.terminals(spec),absent) is not None


def test_refuses_double_claim_from_same_remaining_escrow():
    bad=sample()
    for r in bad['rules']:
        if r['name']=='approve_beta':
            r['credits'][0]['amount_wei']=10*10**18  # <= deposit, exceeds post-prototype remaining
    got=audit(bad)
    assert 'INVALID_OR_NONCONSERVING_CREDIT_PATH' in got['blockers']
    with pytest.raises(lang.V3Error):
        emit.source(bad,'NanoJevProgressive01')


def test_refuses_terminal_with_unallocated_residue():
    bad=sample()
    for r in bad['rules']:
        if r['name']=='approve_final':
            r['credits'][0]['amount_wei']=4*10**18
    got=audit(bad)
    assert any(x['reason']=='TERMINAL_RESIDUAL_ESCROW' for x in got['conservation']['violations'])
    with pytest.raises(lang.V3Error):
        emit.source(bad,'NanoJevProgressive01')


def test_refuses_role_unavailability_trap():
    bad=sample()
    bad['rules']=[r for r in bad['rules'] if r['name']!='refund_funded_worker']
    got=audit(bad)
    assert 'SINGLE_UNAVAILABLE_ROLE_TRAP' in got['blockers']
    assert any(t['unavailable_role']=='payer' for t in got['liveness']['single_role_traps'])


def test_refuses_divergent_refund_race():
    bad=sample()
    for rule in bad['rules']:
        if rule['name']=='refund_funded_worker':
            rule['credits'][0]['to']='worker'
    got=audit(bad)
    assert 'DIVERGENT_SIMULTANEOUS_ACTIONS_REQUIRES_INTENT_REVIEW' in got['blockers']


def test_v3_second_independently_structured_contract():
    spec=sample()
    wei=10**18
    spec['amount_wei']=5*wei
    spec['deadline_seconds']=3*86400
    spec['rules']=[
       {'name':'approve_stage','actor':'payer','from':'funded','to':'stage','when':'before_deadline','credits':[{'to':'worker','amount_wei':2*wei}]},
       {'name':'approve_finish','actor':'payer','from':'stage','to':'done','when':'before_deadline','credits':[{'to':'reviewer','amount_wei':'remaining'}]},
    ]
    for state in ('funded','stage'):
        for role in ('payer','worker'):
            spec['rules'].append({'name':f'cancel_{state}_{role}','actor':role,'from':state,'to':'cancelled','when':'at_or_after_deadline', 'credits':[{'to':'payer','amount_wei':'remaining'}]})
    got=audit(spec)
    assert got['status']=='PASS_CONDITIONAL',got['blockers']
    assert got['model']['reachable_nodes'] >= 10
    assert 'approve_stage' in got['model']['partial_nonterminal_rules']
    assert 'function approve_stage() external' in emit.source(spec,'NanoJevProgressive02')
    assert len(oracle.witnesses(lang.validate(spec))) == len(spec['rules'])


@pytest.mark.parametrize('bad_change', [
    lambda s: s.update(language='nanojev.contract.state_machine.v2'),
    lambda s: s.update(settlement_mode='push'),
    lambda s: s.update(unauthorized=True),
    lambda s: s.update(amount_wei=True),
    lambda s: s['rules'][0].update(name='withdrawTo'),
    lambda s: s['rules'][0].update(**{'from':'unfunded'}),
    lambda s: s['rules'][0].update(actor='outsider'),
    lambda s: s['rules'][0].update(when='after_two_deadlines'),
    lambda s: s['rules'][0]['credits'][0].update(amount_wei=-1),
    lambda s: s['rules'][0]['credits'][0].update(amount_wei=0),
])
def test_v3_schema_fail_closed(bad_change):
    spec=sample()
    bad_change(spec)
    with pytest.raises(lang.V3Error):
        lang.validate(spec)


def test_v3_cycle_rejected():
    bad=sample()
    bad['rules'].append({'name':'go_backward','actor':'payer','from':'beta','to':'prototype','when':'always','credits':[]})
    with pytest.raises(lang.V3Error,match='Cycles'):
        lang.validate(bad)


def test_v3_injected_ir_identifier_rejected():
    bad=sample()
    bad['rules'][0]['name']='admin() { selfdestruct'
    with pytest.raises(lang.V3Error):
        lang.validate(bad)


def test_generated_solidity_is_versioned_and_has_pull_credit_invariants():
    source=emit.source(sample(),'NanoJevProgressive01')
    assert 'contract NanoJevProgressive01 {' in source
    assert 'uint256 public unallocated;' in source
    assert 'mapping(address => uint256) public pendingWithdrawals;' in source
    assert 'unallocated + totalPending + totalWithdrawn == AMOUNT_WEI' in source
    assert 'credit0 <= unallocated' in source
    assert 'withdrawalLock = true;' in source
    assert 'complete = true;' in source
    assert re.search(r'function approve_prototype\(\) external',source)
    assert 'function withdrawTo(address payable destination) public' in source
    assert 'UNAUDITED' in source


def test_forbidden_contract_name_rejected():
    with pytest.raises(lang.V3Error):
        emit.source(sample(),'SneakySolidity')


def test_generated_forge_oracle_covers_every_action_and_has_no_legacy_testfail():
    suite,n=oracle.tests(sample(),'NanoJevProgressive01')
    assert n==4+len(sample()['rules'])*2
    assert len(re.findall(r'\bfunction\s+test\w*\(',suite))==n
    assert 'testFail' not in suite
    assert 'testSemanticsApprovePrototype' in suite
    assert 'testSemanticsRefundBetaWorker' in suite
    assert 'testUnauthorizedRefundFundedPayer' in suite


def test_randomized_differential_legal_action_sequences():
    rng=random.Random(311701)
    spec=lang.validate(sample())
    actions={r['name']:r for r in spec['rules']}
    for _ in range(120):
        m=ref.ReferenceEscrow(spec)
        m.fund('payer',spec['amount_wei'])
        node=check.initial(spec)
        for _step in range(18):
            moves=list(check.successors(spec,node))
            if not moves:break
            label,next_node=rng.choice(moves)
            if label=='wait_until_deadline': m.tick_to_deadline()
            elif label.startswith('withdraw:'):m.withdraw(label.split(':',1)[1])
            else:m.execute(label,actions[label]['actor'])
            assert m.snapshot()==(next_node.state,next_node.phase,next_node.unallocated,next_node.pending,next_node.withdrawn)
            assert m.conserved()
            node=next_node
