#!/usr/bin/env python3
"""Head-independent, deterministic contract-completeness safety gate.

This module consumes an independently pinned structured statement of the
contract requirements. It never reads LLM output or counts words in the LLM's
questions as evidence of safety. It is an expressiveness/completeness audit,
NOT a formal proof of a future milestone implementation or independent expert
annotation of the user's intentions.

For existing, representable v2 state machines, a separate entrypoint reuses
the bounded graph liveness oracle to examine actually reachable states.
"""
from __future__ import annotations

from tools import nanojev_contract_liveness as liveness
from tools import nanojev_contract_pull_payments as pull
from tools import nanojev_contract_v2_verifier as graph_verifier
from tools.nanojev_contract_solidity_smoke import SmokeFailure

# These are structural capabilities of the *existing* v2 validator/compiler.
# Each row is independently pinned and verified against the schema ceiling by
# the caller; it is not supplied by the LLM head or inferred from a refusal.
V2_SUPPORT = {
    'progressive_partial_disbursement': False,
    'per_milestone_deadlines': False,
    'discretionary_milestone_extension': False,
    'rejection_and_dispute_workflow': False,
    'arbitrator_appointment_and_authority': False,
    'unearned_balance_refund': False,
    'dispute_nonresponse_recovery': False,
    'recipient_isolated_withdrawals': True,
    'single_upfront_escrow_deposit': True,
}

# Each checkpoint represents a *requested* escrow situation with value held.
# These are obligation surfaces, not actual reachable states in v2 (which
# cannot represent this contract). Therefore unresolved recovery is reported
# as UNPROVEN, not as a demonstrated permanent lock or a liveness proof.
SURFACES = (
    ('awaiting_acceptance', 'payer', 'What happens when the client never accepts or rejects the milestone?'),
    ('extension_decision_pending', 'payer', 'What happens if the client never grants or refuses the extension?'),
    ('awaiting_dispute_filing', 'worker', 'How long may the developer wait before disputing a rejection?'),
    ('arbitrator_appointment_pending', 'payer', 'How is the arbitrator appointed or replaced if the client is silent?'),
    ('arbitrator_appointment_pending', 'worker', 'How is the arbitrator appointed or replaced if the developer is silent?'),
    ('dispute_resolution_pending', 'arbitrator', 'What is the timeout and binding default if the arbitrator never rules?'),
    ('failed_milestone_refund_pending', 'payer', 'Who can trigger a refund to the client if the client takes no action?'),
)

REQUIRED_USER_TERMS = {
    'deadline_schedule': 'Exact per-milestone deadlines and the event from which each is measured',
    'acceptance_evidence': 'What evidence establishes acceptance or rejection of each deliverable',
    'extension_policy': 'Who grants extensions, how often, and consequences of silence',
    'arbitrator_appointment': 'Mutual appointment/replacement procedure and authorization',
    'dispute_resolution_timeout': 'Dispute filing and arbitrator decision deadlines and binding fallback',
    'inactivity_default': 'Executable recovery/settlement rules for every funded state when a party goes silent',
}


def evaluate_milestone_requirements(fixture: dict, ceiling: dict) -> dict:
    """Assess structured requirements without ever consulting an LLM reply.

    The underlying fixture must be separately pinned by the caller. A new
    contract needs its own reviewed structured requirements, not these keys.
    """
    if not isinstance(fixture, dict) or not isinstance(ceiling, dict):
        raise SmokeFailure('independent_completeness', 'Requirements/ceiling must be mappings')
    milestones = fixture.get('milestones')
    capabilities = fixture.get('required_capabilities')
    if (not isinstance(milestones, list) or len(milestones) != 3
            or not isinstance(capabilities, list) or
            not all(isinstance(m, dict) for m in milestones)):
        raise SmokeFailure('independent_completeness', 'Milestone requirement structure incomplete')
    amounts = [m.get('wei') for m in milestones]
    if (any(type(x) is not int or x <= 0 for x in amounts)
            or len({m.get('name') for m in milestones}) != 3
            or sum(amounts) != fixture.get('deposit_wei')):
        raise SmokeFailure('independent_completeness', 'Escrow conservation or milestones invalid')
    capability_ids = [c.get('id') for c in capabilities if isinstance(c, dict)]
    if len(capability_ids) != len(V2_SUPPORT) or set(capability_ids) != set(V2_SUPPORT):
        raise SmokeFailure('independent_completeness', 'Capability inventory missing or changed')
    if (ceiling.get('language') != pull.LANG
            or ceiling.get('single_deadline_seconds_field') is not True
            or ceiling.get('partial_nonterminal_disbursement') != 'NOT_SUPPORTED'
            or ceiling.get('milestone_schema_extension') != 'REJECTED'
            or ceiling.get('settlement_mode') != 'pull'
            or set(ceiling.get('allowed_roles', [])) != {'payer', 'worker', 'reviewer'}):
        raise SmokeFailure('independent_completeness', 'Compiler/schema ceiling not verified')
    if any(c['v2'] != ('supported' if V2_SUPPORT[c['id']] else 'unsupported') for c in capabilities):
        raise SmokeFailure('independent_completeness', 'Pinned capabilities contradict compiler ceiling')

    unsupported = [c['id'] for c in capabilities if not V2_SUPPORT[c['id']]]
    supported = [c['id'] for c in capabilities if V2_SUPPORT[c['id']]]
    checkpoints = []
    for m in milestones:
        for state, unavailable, question in SURFACES:
            checkpoints.append({
                'milestone': m['name'], 'escrow_obligation_wei': m['wei'],
                'hypothetical_checkpoint': state, 'unavailable_role': unavailable,
                'recovery_status': 'NOT_DEFINED_OR_PROVABLE_IN_V2',
                'required_user_decision': question,
            })
    # The agreement explicitly requires nonresponse recovery but supplies no
    # executable, exhaustive arbitration/default rules. Merely saying "no one
    # may lock funds" does not instantiate executable transitions.
    return {
        'status': 'BLOCK_UNSUPPORTED_AND_UNRESOLVED',
        'provenance': 'PINNED_USER_REQUEST_STRUCTURED_BY_EXPERIMENT_AUTHOR_NOT_EXPERT_GOLD',
        'head_independent': True,
        'proof_scope': 'STATIC_REQUIREMENT_SURFACES_NOT_REACHABLE_STATE_FORMAL_PROOF',
        'representability': 'FAIL_V2_UNSUPPORTED',
        'supported_capabilities': supported,
        'unsupported_capabilities': unsupported,
        'escrow_conservation': {
            'deposit_wei': fixture['deposit_wei'],
            'milestone_total_wei': sum(amounts),
            'static_amount_sum': 'PASS',
            'dynamic_credit_refund_conservation': 'NOT_PROVED_NO_REPRESENTABLE_PROGRAM',
        },
        'unspecified_user_terms': [
            {'id': k, 'requirement': v, 'status': 'NO_EXECUTABLE_POLICY_SUPPLIED'}
            for k, v in REQUIRED_USER_TERMS.items()
        ],
        'nonresponse_recovery': {
            'status': 'UNRESOLVED_NOT_PROVED_SAFE',
            'checkpoint_count': len(checkpoints),
            'checkpoints': checkpoints,
            'scope_note': 'Hypothetical obligation checkpoints derived from the request, not reachable nodes in an executable IR',
        },
        'emission_authorized': False,
        'blocking_reasons': [
            'V2 lacks seven required milestone/dispute/refund/recovery capabilities',
            'Contract terms omit executable acceptance, deadline, arbitration and inactivity defaults',
            'Single-role unavailability cannot be proven recoverable for milestone/dispute checkpoints',
        ],
    }


def evaluate_existing_v2_graph(spec: dict) -> dict:
    """General v2 graph audit, while preserving the existing liveness API.

    The pinned milestone requirement fixture is intentionally *not* used here.
    This only proves properties of the supplied validated v2 IR.
    """
    report = graph_verifier.verify(spec)
    live = report['liveness']
    return {
        'source': 'VALIDATED_V2_RULE_GRAPH_NOT_NATURAL_LANGUAGE',
        'status': live['status'],
        'graph_audit_status': report['status'],
        'checked_reachable_nodes': live['checked_reachable_unsettled_nodes'],
        'checked_role_unavailability_scenarios': live['checked_single_role_scenarios'],
        'unconditional_traps': live['unconditional_traps'],
        'single_role_traps': live['single_role_traps'],
        'conditional_assumptions': report['assumptions'],
        'recipient_acceptance': 'NOT_REQUIRED_TO_SETTLE_V2_CREDITED_PAYMENTS',
        'emission_authorized_by_liveness': live['status'] == 'PASS_CONDITIONAL',
        'emission_authorized_by_graph_audit': report['emission_authorized_by_graph_audit'],
        'authorization': report['authorization'],
        'conservation': report['conservation'],
        'conflicts': report['conflicts'],
        'graph': report['graph'],
        'blockers': report['blockers'],
        'proof_limitations': report['unverified'],
    }
