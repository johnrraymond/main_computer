"""Scientific regression: safety must NOT be purchased by rejecting everything."""
import copy
import json
from pathlib import Path
from tools import nanojev_contract_v3_source_fidelity as f

ROOT=Path(__file__).resolve().parents[1]
FRESH=ROOT/'tools/fixtures/nanojev_v3_fresh_challenge/manifest.json'
FORTY=ROOT/'tools/fixtures/nanojev_v3_generalization/manifest.json'


def load(path):return json.loads(path.read_text())['cases']


def test_all_14_existing_valid_challenge_requests_remain_unblocked():
    cs=load(FRESH)
    allowed=[c for c in cs if c['expected']=='contract']
    assert len(allowed)==14
    for c in allowed:
        audit=f.audit_source(c['request'])
        assert not audit['issues'],c['id']
        assert not f.audit_candidate(c['request'],c['gold'],source_audit=audit)['issues'],c['id']


def test_eight_source_grounded_invalid_challenges_blocked_but_two_omissions_are_not_fabricated():
    cs=load(FRESH);negative=[c for c in cs if c['expected']=='needs_clarification']
    assert len(negative)==10
    blocks={c['id'] for c in negative if f.audit_source(c['request'])['issues']}
    assert blocks=={'fresh_worker_approves','fresh_payer_appoints',
                    'fresh_observer_release','fresh_worker_extension',
                    'fresh_conflict_deadline','fresh_conflict_total',
                    'fresh_conflict_silence','fresh_conflict_extension'}
    for c in negative:
        issue=f.audit_source(c['request'])['issues']
        for it in issue:
            assert it['lines']
            assert all(1<=n<=len(c['request'].splitlines()) for n in it['lines'])


def test_original_40_valid_requests_are_not_overblocked():
    cases=load(FORTY);valid=[c for c in cases if c['expected']=='contract']
    assert len(valid)==23
    for c in valid:
        audit=f.audit_source(c['request'])
        assert not audit['issues'],c['id']
        assert not f.audit_candidate(c['request'],c['gold'],source_audit=audit)['issues'],c['id']


def test_original_40_blockable_negative_requests():
    cases=load(FORTY)
    expected={'authority_unsupported_1','authority_unsupported_2',
              'authority_unsupported_3','authority_unsupported_4',
              'conflict_1','conflict_2','conflict_3','conflict_4','conflict_5','conflict_6',
              'adversarial_hallucinate_3'}
    flagged={c['id'] for c in cases if c['expected']=='needs_clarification'
             and f.audit_source(c['request'])['issues']}
    assert flagged==expected


def test_cited_explicit_candidate_mismatch_before_compiler():
    c=next(c for c in load(FRESH) if c['id']=='inv_q2_v0')
    changed=copy.deepcopy(c['gold'])
    changed['milestones'][1]['deadline_seconds']+=86400
    result=f.audit_candidate(c['request'],changed)
    assert result['status']=='BLOCK_UNSUPPORTED_OR_MISMATCH'
    assert any(x['code']=='CANDIDATE_SOURCE_VALUE_MISMATCH' and
               x['path']=='milestones.delivery.deadline_seconds' for x in result['issues'])


def test_missing_field_in_model_representation_is_not_construed_as_source_omission():
    c=next(c for c in load(FRESH) if c['id']=='inv_q1_v0')
    changed=copy.deepcopy(c['gold']);del changed['policy']['review_seconds']
    result=f.audit_candidate(c['request'],changed)
    assert any(x['path']=='policy.review_seconds' for x in result['issues'])


def test_repeated_identical_clause_does_not_create_false_conflict():
    text='The payer escrows 4 ETH for the worker.\nThe total escrow must equal 4 ETH.\n'
    result=f.audit_source(text)
    assert not result['issues']
    assert result['recognized_values']==2


def test_repeated_conflicting_clauses_report_both_lines_and_values():
    text='The payer escrows 4 ETH for the worker.\nThe total escrow must equal 5 ETH.\n'
    result=f.audit_source(text)
    assert len(result['issues'])==1
    issue=result['issues'][0]
    assert issue['path']=='amount_wei'
    assert issue['lines']==[1,2]
    assert [v['value'] for v in issue['values']]==[4*10**18,5*10**18]


def test_other_actor_authority_is_not_silently_discarded():
    for sentence in [
        'The worker may approve completion without payer consent.',
        'The payer shall appoint the arbitrator alone.',
        'A delegate can release escrow despite payer rejection.',
        'The arbitrator may unilaterally extend the review period.',
        'The observer alone approves payments.',
    ]:
        result=f.audit_source('The payer escrows 4 ETH for the worker.\n'+sentence)
        assert result['issues'] and result['issues'][0]['code']=='EXPLICIT_AUTHORITY_OUTSIDE_V3',sentence


def test_unparsed_paraphrases_do_not_automatically_veto():
    source='For beta, the milestone amount is 4 ETH.\nFor beta, the deadline is 12 days.\n'
    result=f.audit_source(source)
    assert not result['issues']
    assert len(result['unparsed_lines'])==2
    assert result['scope'].endswith('NOT full semantic verification')


def test_source_is_not_reinterpreted_to_fix_conflict():
    source='The payer escrows 4 ETH.\nThe total payer escrow shall be 5 ETH, but 4 ETH remains binding.\n'
    issues=f.audit_source(source)['issues']
    assert len(issues)==1
    assert issues[0]['code']=='CONTRADICTORY_EXPLICIT_VALUES'


def test_max_extension_count_evidence_recognized():
    source='The beta permits a maximum of 1 extensions.\nThe beta extension limit is 1 extensions.\n'
    result=f.audit_source(source)
    assert result['recognized_values']==2
    assert not result['issues']


def test_randomized_conflict_discrimination_across_new_amounts():
    import random
    rng=random.Random(20261121)
    for _ in range(100):
        n=rng.randrange(1,1_000_000)
        other=n+rng.randrange(1,500)
        same=f'The payer escrows {n} ETH for the worker.\nThe total escrow must equal {n} ETH.\n'
        diff=f'The payer escrows {n} ETH for the worker.\nThe total payer escrow shall be {other} ETH.\n'
        assert not f.audit_source(same)['issues']
        bad=f.audit_source(diff)
        assert len(bad['issues'])==1
        assert bad['issues'][0]['path']=='amount_wei'


def test_randomized_milestone_source_to_candidate_numbers():
    import random
    rng=random.Random(45188)
    for _ in range(100):
        x=rng.randrange(2,45)
        source=f'The quasar deadline is {x} days after activation.\n'
        spec={'milestones':[{'id':'quasar','deadline_seconds':x*86400}]}
        assert not f.audit_candidate(source,spec)['issues']
        changed={'milestones':[{'id':'quasar','deadline_seconds':(x+1)*86400}]}
        issues=f.audit_candidate(source,changed)['issues']
        assert issues[0]['code']=='CANDIDATE_SOURCE_VALUE_MISMATCH'
