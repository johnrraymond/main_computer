"""Prepare a source-honest V3 semantic adjudication dossier and adversarial evidence seal.

The dossier is a drafted interpretation of recorded user intent, NOT an external
expert finding, binding contractual terms, or verified reviewer identity.
The only input used for normative statements is the explicitly recorded user
escrow brief, designated non-primary until separately authenticated. The frozen
compiler/reference/gold/mutants provide technical coverage, never authority.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from typing import Any

from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_adversarial_mutations as attack
from tools import nanojev_contract_v3_semantic_adjudication as semantic

SCHEMA='nanojev.contract.v3.semantic_pre_adjudication.v1'
SEAL_SCHEMA='nanojev.contract.v3.post_mutation_evidence_seal.v1'
STATUS='REVIEW_DRAFT_ALL_14_REQUIRE_EXTERNAL_AUTHORITATIVE_TERMS'

# Recorded earlier user-origin intentions. They are NOT a signed or otherwise
# independently authenticated contract; unprovided specifics are intentionally
# left unresolved. Each entry contains a provisional reading, not a legal verdict.
ANNOTATIONS={
'R01':('User design brief: a 12 ETH escrow funded upfront.','The payer deposits all 12 ETH before performance; the restriction on other funders is a design/implementation choice not established by primary terms.','Identify payer/depositor, treatment of third-party funding, deadline and consequences of an incomplete/wrong deposit.','Unauthorized or underfunded escrow can make performance claims uncollectible.'),
'R02':('User design brief: prototype/beta/final are separate milestones with separate deadlines.','The worker is the likely submitting party, but submission authority, evidence requirements and whether equality at deadline counts are not established by independent terms.','Define absolute deadlines/clock origins, acceptance evidence, the submitting actor, grace periods, and exact inequality at due time.','Work may be rejected or funded despite being late.'),
'R03':('No independently authenticated approval/rejection terms supplied.','Approval and rejection are actions attributed to the payer in the generated implementation; that attribution does not prove contractual authorization.','Define approver, permitted grounds for rejection, review clock start/end and exact equality boundary.','Payer could unilaterally seize an earned milestone or worker could force acceptance.'),
'R04':('No user-origin review-silence disposition established.','The code and gold exercise a selectable pay-worker/refund-payer fallback; neither choice is inherently authorized without party-approved terms.','For every governing variant, state the silence beneficiary, start time and exact deadline, including evidentiary prerequisites.','Silence could transfer funds to the wrong party.'),
'R05':('No user-origin dispute-filing deadline established.','The worker initiates a dispute in the implementation; whether filing is exclusive to the worker and which clock closes it require authority.','Identify the permitted filing actor(s), filing conditions, and exact filing deadline.','A party could be deprived of adjudication or reopen finalized claims.'),
'R06':('User design brief: an arbitrator is mutually appointed.','Mutual appointment is supported at the level of intent; nominee independence, party exclusions, confirmation and deadline mechanics are unresolved.','Specify nomination/confirmation, permitted and disqualified arbitrators, appointment expiry, and replacement process.','One party may capture arbitration or indefinitely prevent appointment.'),
'R07':('User design brief: a mutually appointed arbitrator adjudicates disputes.','Award authority follows the intended neutral decision maker, but the allowable awards, proof standard, and precise ruling window were not supplied.','Specify award scope, authority, evidence rules, ruling deadline and whether exact deadline is timely.','An arbitrator or outsider could transfer funds without authority.'),
'R08':('User design brief: no escrow may be permanently locked.','An unappointed-arbitrator fallback is required for liveness, but which party receives unearned/disputed value is not established.','Specify disposition and triggers on failed appointment, including exact appointment boundary.','Funds may be trapped or misallocated by default.'),
'R09':('User design brief: no escrow may be permanently locked.','Arbitrator silence must eventually resolve; worker-payment and payer-refund alternatives cannot be chosen by the compiler.','Specify the beneficiary and trigger of arbitrator-silence resolution, separately for every contract.','Nonresponse can change the economic bargain.'),
'R10':('User design brief: an optional seven-day extension.','Seven days of added time is supported in the original brief; the number of extensions, who grants them and the decision window are not established as universal requirements.','Specify each milestone extension cap, grant authority, decision-clock bounds and treatment of no decision.','A party can impose indefinite or unauthorized delays.'),
'R11':('User design brief: unearned funds are refundable and escrow cannot stay locked forever.','Refund of unearned amounts is intended, but the triggering deadline, extension-grace handling, and who can invoke resolution require explicit terms.','Define conditions for missed submission, expiry, refund timing, permissionless invocation and termination.','Unearned funds could be trapped or prematurely taken.'),
'R12':('User design brief: total 12 ETH, prototype 3 ETH, beta 4 ETH, final 5 ETH.','The 3+4+5=12 ETH allocation is explicit; exact-wei rounding policy and accounting invariants beyond these round amounts require authoritative generalization for other contracts.','Specify per-contract asset, exact wei denominations, arithmetic conservation, dust handling and beneficiary entitlement.','Small rounding defects or reallocation can cause direct loss.'),
'R13':('User design brief: independent withdrawal credits.','Separate credits are intended, but whether funds may be redirected to a nominated receiver and the full no-reentrancy/once-only requirements need terms.','Specify who may withdraw whose credit, destination authority, partial withdrawals, repeated calls and reentrancy treatment.','Credits can be redirected, double withdrawn, or made unclaimable.'),
'R14':('User design brief: refund unearned funds, preserve earned value and prevent permanent locks.','The intended partial-completion outcome combines earned milestone payments and refunds of unearned residue; proof of vesting and all terminal fund destinations require primary terms.','Define vesting, partial refund priority, terminal conditions, zero-dust settlement and the role of unwithdrawn credits.','Previously earned money can be clawed back or residue stranded.'),
}

# Reasoning is claim-specific. An observed test outcome is not itself a norm.
RATIONALES={
 'R01':'Full pre-funding is necessary for all three promised milestone balances to be available; the test that rejects outsider funding shows one implementation of depositor control, not proof the parties required that control.',
 'R02':'Different milestone deadlines imply independently determinable submission clocks; the equality probe proves the implementation accepts a boundary, but only negotiated terms can select that boundary.',
 'R03':'Review necessarily allocates decision authority and time; failing a forged outsider approval tests code authorization without resolving the contractual standard for rejection.',
 'R04':'An omitted review response changes the disposition of escrow value; the opposite silence policies both pass engineered examples, proving there is no universal beneficiary implied by the code.',
 'R05':'A dispute filing right becomes illusory if its end time or permitted actor is unspecified; a mutation that permits late filing demonstrates detection, not authorization of the deadline.',
 'R06':'Mutual selection constrains unilateral control, but a practical arbitration process also requires explicit confirmation, conflict exclusions, and an expiry/replacement rule.',
 'R07':'An award is a binding disposition of another party’s assets; restricting award calls to the appointed actor does not establish what factual or legal findings justify each award.',
 'R08':'If appointment can fail indefinitely, escrow liveness fails; implementing any fallback solves liveness but may still pay the wrong party.',
 'R09':'Silence after appointment is a distinct failure mode from failure to appoint, so the remedy must be independently specified rather than inferred from the appointment default.',
 'R10':'An optional seven-day extension changes performance time, while a two-extension allowance can add more cumulative time; without identifying the scope of the original terms and each variant, multiplying extensions is not inherently authorized.',
 'R11':'A missed deliverable must eventually release unearned funds under the design intent; the decision window and permissionless trigger control whether the refund is prematurely or indefinitely withheld.',
 'R12':'Three positive obligations of 3, 4 and 5 ETH sum to the escrow of 12 ETH; exact-wei conservation and dust-free settlement for arbitrary new amounts are further generalizations, not signed economic terms.',
 'R13':'Independent credits preserve beneficiary isolation, but transfer destination control and exactly-once withdrawal are separate authorization and accounting obligations requiring explicit approval.',
 'R14':'Partial completion must distinguish earned from unearned amounts before releasing a refund; even a conserving contract can assign balances to the wrong beneficiary, and outstanding withdrawal credits require a liveness mechanism.',
}
MUTANT_RELEVANCE={
 'R01':('M01',), 'R02':('M19',), 'R03':('M03','M20'), 'R04':('M04',),
 'R05':('M17',), 'R06':('M11','M18'), 'R07':('M08','M13'),
 'R08':('M11','M18'), 'R09':('M12',), 'R10':('M05','M06'),
 'R11':('M02','M16'), 'R12':('M07','M09','M10','M15'),
 'R13':('M10','M14','M15'), 'R14':('M07','M09','M10','M16'),
}


def validate_evidence(report:dict[str,Any],gold:dict[str,Any],mutation:dict[str,Any],
                      *, baseline:dict[str,Any]|None=None,
                      evidence_lock:dict[str,Any]|None=None)->None:
    gate.require(report.get('status')=='PASS_FORGE_V3_GOVERNANCE_HELDOUT_NOT_CERTIFIED', '203-test heldout report not passing')
    gate.require(report.get('passed_forge_tests')==203,'wrong heldout Forge test count')
    gate.require(gold.get('schema')=='nanojev.contract.v3.independent_behavior_gold.v1', 'wrong separate gold schema')
    gate.require(mutation.get('schema')==attack.SCHEMA and mutation.get('status')==attack.PASS, 'mutation report not passing')
    gate.require(mutation.get('campaign_complete') is True and mutation.get('frozen_inputs_reverified') is True, 'campaign incomplete/unfrozen')
    gate.require(mutation.get('killed_by_Forge_assertion')==len(attack.MUTANTS),'not all mutants killed by EVM assertions')
    gate.require(mutation.get('selected_mutants')==len(attack.MUTANTS) and mutation.get('catalog_size')==len(attack.MUTANTS), 'mutation inventory mismatched')
    gate.require(not mutation.get('invalid_runs') and not mutation.get('survived'), 'invalid or surviving mutants')
    gate.require(mutation.get('pristine_probe_tests_passed')==sum(len(v) for v in attack.PROBE_BODIES.values()),'pristine probes missing')
    gate.require(mutation.get('source_report_sha256')==gate.digest_json(report),'heldout evidence digest mismatch')
    gate.require(mutation.get('independent_gold_sha256')==gate.digest_json(gold),'gold evidence digest mismatch')
    gate.require(mutation.get('expert_gold_independence_verified') is False,'unexpected expert independence assertion')
    gate.require(mutation.get('no_remote_rpc_no_keys_no_live_deployment') is True,'unsafe/inconsistent execution context')
    ids=[m['id'] for m in mutation.get('mutations',[])]
    gate.require(sorted(ids)==sorted(m.id for m in attack.MUTANTS),'mutation IDs incomplete/duplicated')
    for m in mutation['mutations']:
        gate.require(m.get('execution',{}).get('outcome')=='KILLED_BY_GOLD_ASSERTION' and
                     m.get('execution',{}).get('failed',0)>0, m['id']+' not killed by a real assertion')
    gate.require(len(gold.get('cases',[]))==6 and len([c for c in report['cases'] if c['expect']=='accept'])==6,'six contract inventory mismatch')
    if baseline is not None:
        attack.validate_baseline(report,gold,baseline)
        gate.require(gate.digest_json(baseline)==mutation.get('baseline_certificate_sha256'),'baseline certificate changed')
    if evidence_lock is not None:
        gate.require(evidence_lock.get('status')=='PASS_V3_BASELINE_EVIDENCE_LOCKED_NOT_EXPERT_CERTIFIED','evidence lock not verified')
        gate.require(gate.digest_json(evidence_lock)==mutation.get('evidence_lock_sha256'),'evidence lock changed')
        gate.require(evidence_lock.get('source_report_sha256')==gate.digest_json(report),'lock source mismatch')
        gate.require(evidence_lock.get('independent_gold_sha256')==gate.digest_json(gold),'lock gold mismatch')
        gate.require(evidence_lock.get('baseline_certificate_sha256')==mutation.get('baseline_certificate_sha256'),'lock baseline mismatch')


def make_dossier(report:dict[str,Any],gold:dict[str,Any],mutation:dict[str,Any],*,
                 baseline:dict[str,Any]|None=None,evidence_lock:dict[str,Any]|None=None)->dict[str,Any]:
    validate_evidence(report,gold,mutation,baseline=baseline,evidence_lock=evidence_lock)
    # Technical coverage is read-only and explicitly never normative authority.
    by_case={c['id']:c for c in gold['cases']}
    entries=[]
    for rid,domain,question,signature in semantic.CLAIMS:
        authority,interpretation,open_item,risk=ANNOTATIONS[rid]
        coverage=[]
        for cid,case in by_case.items():
            matches=sorted(set(s['id'] for s in case['sequences']
                               if any(x.get('signature')==signature for x in gate._flatten_steps(s['steps']))))
            if matches:coverage.append({'case_id':cid,'trace_ids':matches})
        matched_mutants=[{'id':m['id'],'mechanism':m['mechanism'],'case_id':m['case_id']} for m in mutation['mutations'] if m['id'] in MUTANT_RELEVANCE[rid]]
        entries.append({
            'id':rid,'domain':domain,'question':question,
            'provisional_decision':'PARTIAL_INTENT_IDENTIFIED_DETAILS_UNDECIDED' if authority.startswith('User design') else 'NO_AUTHORITATIVE_REQUIREMENT_FOR_THIS_QUESTION',
            'certification_disposition':'UNRESOLVED_REQUIRES_OUTSIDE_ADJUDICATION',
            'authority_type':'RECORDED_PRIOR_USER_DESIGN_INTENT_UNAUTHENTICATED' if authority.startswith('User design') else 'NO_PRIMARY_REQUIREMENTS_SUPPLIED',
            'authority_summary':authority,
            'provisional_interpretation':interpretation,
            'rationale':RATIONALES[rid],
            'unresolved_ambiguity':open_item,
            'risk_if_wrong':risk,
            'technical_gold_coverage_not_normative':coverage,
            'coverage_caveat':'Signature-based trace lookup does not measure read-only invariant assertions such as escrowConserved() or pendingWithdrawals().',
            'mutation_witnesses_not_normative':matched_mutants,
            'requested_external_decision':'AGREE / CONFLICT / UNRESOLVED, with executed agreement/authorized requirements section, normative rule and reasoning',
        })
    return {
      'schema':SCHEMA,'status':STATUS,'expert_gold_independence_verified':False,
      'reviewer_identity_authenticated':False,'external_primary_terms_supplied':False,
      'basis':'Technical evidence from frozen V3 records; limited prior user design intent is not an authenticated independent contract.',
      'source_report_sha256':gate.digest_json(report),
      'independent_gold_sha256':gate.digest_json(gold),
      'mutation_report_sha256':gate.digest_json(mutation),
      'baseline_certificate_sha256':mutation.get('baseline_certificate_sha256'),
      'evidence_lock_sha256':mutation.get('evidence_lock_sha256'),
      'baseline_bytes_reverified_in_this_run':baseline is not None,
      'evidence_lock_bytes_reverified_in_this_run':evidence_lock is not None,
      'claims':entries,'total_claims':14,'pending_external_decisions':14,
      'source_of_authority_warning':'Not a certification, independent expert opinion, user approval, party-approved contract or verified outside review.',
    }


def make_seal(dossier:dict[str,Any],mutation:dict[str,Any],*,
              report_path:Path,gold_path:Path,mutation_path:Path,
              baseline_path:Path|None,evidence_lock_path:Path|None)->dict[str,Any]:
    names=[('heldout_report',report_path),('separate_gold',gold_path),('mutation_report',mutation_path)]
    if baseline_path:names.append(('baseline_certificate',baseline_path))
    if evidence_lock_path:names.append(('pre_mutation_evidence_lock',evidence_lock_path))
    return {'schema':SEAL_SCHEMA,'status':'EVIDENCE_SEALED_NOT_SEMANTICALLY_CERTIFIED',
            'evidence':{key:{'filename':path.name,'sha256_bytes':gate.digest(path.read_bytes())} for key,path in names},
            'source_report_sha256':dossier['source_report_sha256'],
            'independent_gold_sha256':dossier['independent_gold_sha256'],
            'mutation_report_sha256':dossier['mutation_report_sha256'],
            'semantic_review_draft_sha256':gate.digest_json(dossier),
            'evidence_lock_sha256':dossier['evidence_lock_sha256'],
            'baseline_certificate_sha256':dossier['baseline_certificate_sha256'],
            'foundry_image_id':mutation['foundry_image_id'],
            'mutation_catalog_size':20,'mutants_killed_by_actual_assertion':20,
            'pristine_boundary_probes_passed':6,
            'authoritative_semantic_review':'PENDING_OUTSIDE_ADJUDICATOR',
            'expert_gold_independence_verified':False}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    for x in ('report','gold','mutation-report','output','evidence-seal'):
        p.add_argument('--'+x,required=True,type=Path)
    p.add_argument('--baseline-cert',type=Path)
    p.add_argument('--evidence-lock',type=Path)
    p.add_argument('--review-packet',type=Path,help='Optional official evidence-bound 14-question packet; requires --baseline-cert')
    p.add_argument('--review-template',type=Path,help='Optional blank outside-review form; requires --review-packet')
    args=p.parse_args(argv)
    try:
        gate.require(not args.output.exists(),'refuse dossier overwrite')
        gate.require(not args.evidence_seal.exists(),'refuse evidence seal overwrite')
        gate.require(not args.review_template or args.review_packet,'review template requires packet')
        gate.require(not args.review_packet or (args.baseline_cert and args.evidence_lock),'official semantic packet requires baseline certificate and evidence lock')
        if args.review_packet:gate.require(not args.review_packet.exists(),'refuse review packet overwrite')
        if args.review_template:gate.require(not args.review_template.exists(),'refuse review template overwrite')
        report,gold,mutation=(gate.read_json(x) for x in (args.report,args.gold,args.mutation_report))
        baseline=gate.read_json(args.baseline_cert) if args.baseline_cert else None
        lock=gate.read_json(args.evidence_lock) if args.evidence_lock else None
        draft=make_dossier(report,gold,mutation,baseline=baseline,evidence_lock=lock)
        seal=make_seal(draft,mutation,report_path=args.report,gold_path=args.gold,mutation_path=args.mutation_report,
                       baseline_path=args.baseline_cert,evidence_lock_path=args.evidence_lock)
        if args.review_packet:
            # Reuse the established, stricter external review packet validator.
            # All original evidence must pass this before producing reviewer files.
            packet=semantic.prepare(report,gold,mutation,baseline)
        gate.atomic_json(args.output,draft)
        gate.atomic_json(args.evidence_seal,seal)
        if args.review_packet:
            gate.atomic_json(args.review_packet,packet)
        if args.review_template:
            gate.atomic_json(args.review_template,semantic.empty_review(packet))
        print(json.dumps({'status':draft['status'],'claims':len(draft['claims']),
            'externally_unresolved':draft['pending_external_decisions'],
            'baseline_bytes_verified':draft['baseline_bytes_reverified_in_this_run'],
            'lock_bytes_verified':draft['evidence_lock_bytes_reverified_in_this_run'],
            'output':str(args.output),'evidence_seal':str(args.evidence_seal),
            'review_packet':str(args.review_packet) if args.review_packet else None,
            'review_template':str(args.review_template) if args.review_template else None}))
        return 0
    except (gate.CertificationFailure,KeyError,ValueError,TypeError,OSError) as exc:
        print(json.dumps({'status':'FAIL_SEMANTIC_DOSSIER_NOT_PREPARED','blockers':[str(exc)]}),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
