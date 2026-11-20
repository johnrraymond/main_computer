"""Prepare and validate an OUTSIDE-reviewer semantic adjudication packet.

Does not conflate a separate Forge gold corpus with an independent contractual
source. Never claims reviewer identity or independence has been authenticated.
Only externally supplied primary normative terms can support 'agree' findings.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import Any

from tools import nanojev_contract_v3_independent_behavior as gate
from tools import nanojev_contract_v3_adversarial_mutations as mutations

PACKET_SCHEMA='nanojev.contract.v3.semantic_review_packet.v1'
REVIEW_SCHEMA='nanojev.contract.v3.external_semantic_review.v1'
RESULT_SCHEMA='nanojev.contract.v3.semantic_review_validation.v1'

# Normative questions, not assertions of semantic truth. Source-of-authority
# and material consequences must be adjudicated by someone outside the build.
CLAIMS=(
 ('R01','funding','Who may fund the escrow, and what exact amount is authorized?','fund()'),
 ('R02','submission','Who may submit each milestone, and exactly when does its due period expire?','submit()'),
 ('R03','review','Who may approve or reject, and which endpoints define the review window?','approve()'),
 ('R04','review_silence','What is owed on payer silence, separately for each contract policy?','resolveReviewSilence()'),
 ('R05','filing','Who may file a dispute and when does the filing window close?','fileDispute()'),
 ('R06','appointment','What nomination and confirmation authorizations, exclusions and time windows are mandatory?','confirmArbitrator(address)'),
 ('R07','ruling','Who may issue a worker or payer award, and through what ruling deadline?','awardPayer()'),
 ('R08','unappointed','What is the explicit default if no arbitrator is appointed on time?','resolveUnappointed()'),
 ('R09','arbitrator_silence','What is the explicit default if the arbitrator does not rule on time?','resolveArbitratorSilence()'),
 ('R10','extensions','Who may extend, how often, within which window, and by how much?','grantExtension()'),
 ('R11','missed_submission','What recovery must occur after missing a submission deadline and extension decision window?','resolveMissedDeadline()'),
 ('R12','accounting','Must milestone sums and entitlements equal the exact deposit, including odd wei?','escrowConserved()'),
 ('R13','withdrawals','May a beneficiary withdraw only its own credit, only once, in any permitted order?','withdraw()'),
 ('R14','partial_completion','Does a later refund preserve earlier earned credits and guarantee zero stranded contract balance?','pendingWithdrawals(address)'),
)


def prepare(report:dict[str,Any],gold:dict[str,Any],mutation_report:dict[str,Any],baseline:dict[str,Any])->dict[str,Any]:
    mutations.validate_baseline(report,gold,baseline)
    gate.require(mutation_report.get('schema')==mutations.SCHEMA,'wrong mutation report schema')
    gate.require(mutation_report.get('status')==mutations.PASS,'mutation campaign not wholly killed')
    gate.require(mutation_report.get('campaign_complete') is True and mutation_report.get('killed_by_Forge_assertion')==len(mutations.MUTANTS),
                 'incomplete mutation campaign')
    gate.require(mutation_report.get('pristine_probe_tests_passed')==sum(len(v) for v in mutations.PROBE_BODIES.values()),
                 'pristine adversarial challenge baseline not verified')
    gate.require(mutation_report.get('frozen_inputs_reverified') is True,
                 'frozen inputs were not reverified after attack')
    gate.require(mutation_report.get('source_report_sha256')==gate.digest_json(report),'mutation report held-out source mismatch')
    gate.require(mutation_report.get('independent_gold_sha256')==gate.digest_json(gold),'mutation report gold mismatch')
    gate.require(mutation_report.get('baseline_certificate_sha256')==gate.digest_json(baseline),'mutation report baseline mismatch')
    gate.require(mutation_report.get('foundry_image_id')==baseline.get('foundry_image_id'),'mutation report Foundry image changed')
    by_case={c['id']:c for c in gold['cases']}
    claims=[]
    for rid,domain,question,sig in CLAIMS:
        coverage=[]
        for cid,case in by_case.items():
            matches=sorted(set(s['id'] for s in case['sequences']
                               if any(x.get('signature')==sig for x in gate._flatten_steps(s['steps']))))
            if matches:coverage.append({'case_id':cid,'gold_sequence_ids':matches})
        claims.append({'id':rid,'domain':domain,'question':question,
                       'gold_coverage':coverage,
                       'adjudication_requirement':'Establish answer from independent, primary contractual requirements; compare to gold and code; document discrepancies.'})
    return {'schema':PACKET_SCHEMA,'status':'REVIEW_REQUIRED_NO_EXPERT_CERTIFICATION',
            'source_report_sha256':gate.digest_json(report),'independent_gold_sha256':gate.digest_json(gold),
            'mutation_report_sha256':gate.digest_json(mutation_report),
            'baseline_certificate_sha256':gate.digest_json(baseline),'claims':claims,
            'review_source_requirement':'Independent authoritative contractual terms, not generated Solidity, V3 reference model, gold vectors, or this questionnaire',
            'reviewer_identity_authenticated':False,'expert_gold_independence_verified':False}


def _inside_repository(repo_root:Path,relative:str)->Path:
    gate.require(isinstance(relative,str) and bool(relative.strip()),'normative source path missing')
    path=Path(relative)
    gate.require(not path.is_absolute() and '..' not in path.parts and not re.match(r'^[A-Za-z]:',relative),
                 'external normative source must use a relative path inside repository')
    gate.require(not {'tools','runtime','src','test','out','cache'}.intersection({part.lower() for part in path.parts}),
                 'generated implementation or report path cannot serve as independent primary contractual terms')
    actual=(repo_root/path).resolve()
    gate.require(actual.is_relative_to(repo_root.resolve()),'normative source path escapes repository')
    gate.require(actual.is_file(),'independent primary terms missing: '+str(actual))
    return actual


def adjudicate(packet:dict[str,Any],review:dict[str,Any],*,repo_root:Path)->dict[str,Any]:
    gate.require(packet.get('schema')==PACKET_SCHEMA,'wrong semantic packet schema')
    gate.require(packet.get('status')=='REVIEW_REQUIRED_NO_EXPERT_CERTIFICATION','semantic packet not pending review')
    gate.require(review.get('schema')==REVIEW_SCHEMA,'wrong review schema')
    gate.require(review.get('packet_sha256')==gate.digest_json(packet),'review not bound to packet')
    reviewer=review.get('reviewer',{})
    for key in ('full_name','affiliation','qualification','independence_declaration','review_date'):
        gate.require(isinstance(reviewer.get(key),str) and len(reviewer[key].strip())>=8,
                     'missing reviewer metadata: '+key)
    gate.require(reviewer.get('not_author_of_V3_code_or_gold') is True,'reviewer conflict declaration missing')
    gate.require(reviewer.get('outside_contract_specification_provided') is True,'reviewer lacks independent primary contractual terms')
    gate.require(not any(x in (reviewer['full_name']+' '+reviewer['affiliation']).lower() for x in ('chatgpt','openai','language model','assistant')),
                 'AI-authored or unverified reviewer identity cannot satisfy outside-review gate')
    try:datetime.fromisoformat(reviewer['review_date'].replace('Z','+00:00'))
    except ValueError:raise gate.CertificationFailure('invalid reviewer date')
    sources=review.get('primary_sources')
    gate.require(isinstance(sources,list) and bool(sources),'no primary contractual terms supplied')
    s_by_id={s['id']:s for s in sources}
    gate.require(len(s_by_id)==len(sources),'duplicate primary source ID')
    for s in sources:
        gate.require(s.get('type') in ('executed_agreement','authorized_requirements','party_approved_terms'),
                     'non-primary document cannot establish contract semantics')
        gate.require(isinstance(s.get('sha256'),str) and bool(re.fullmatch(r'[a-f0-9]{64}',s['sha256'])),
                     'primary source hash missing')
        path=_inside_repository(repo_root,s.get('path'))
        gate.require(gate.digest(path.read_bytes())==s['sha256'],'external terms changed: '+str(path))
    findings=review.get('findings')
    gate.require(isinstance(findings,list) and len(findings)==len(CLAIMS),'must adjudicate every normative question')
    by_id={f['id']:f for f in findings}
    gate.require(len(by_id)==len(findings) and set(by_id)=={c[0] for c in CLAIMS},'review finding IDs mismatch')
    conflicts=[];unresolved=[]
    for rid,_,_,_ in CLAIMS:
        item=by_id[rid]
        finding=item.get('finding')
        gate.require(finding in ('AGREE','CONFLICT','UNRESOLVED'),'invalid adjudication finding')
        references=item.get('primary_source_ids')
        gate.require(isinstance(references,list) and bool(references) and set(references).issubset(s_by_id),
                     f'{rid}: primary source citation required')
        gate.require(isinstance(item.get('normative_requirement'),str) and len(item['normative_requirement'].strip())>=30,
                     f'{rid}: independently stated normative requirement missing')
        gate.require(isinstance(item.get('reasoning'),str) and len(item['reasoning'].strip())>=60,
                     f'{rid}: substantive reasoning missing')
        gate.require(isinstance(item.get('reviewer_reference_location'),str) and len(item['reviewer_reference_location'].strip())>=3,
                     f'{rid}: source page/section locator missing')
        if finding=='CONFLICT':conflicts.append(rid)
        elif finding=='UNRESOLVED':unresolved.append(rid)
    approved=not conflicts and not unresolved
    return {'schema':RESULT_SCHEMA,
            'status':'PASS_DOCUMENTED_EXTERNAL_REVIEW_ATTESTATIONS_NOT_IDENTITY_VERIFIED' if approved else 'FAIL_SEMANTIC_REVIEW_CONFLICT_OR_UNRESOLVED',
            'review_packet_sha256':gate.digest_json(packet),'review_sha256':gate.digest_json(review),
            'reviewer_name_claimed':reviewer['full_name'],'reviewer_identity_authenticated':False,
            'expert_independence_verified':False,
            'all_findings_addressed':True,'findings_checked':len(findings),'conflicts':conflicts,
            'unresolved':unresolved,'requires_outside_authentication':True}


def empty_review(packet:dict[str,Any])->dict[str,Any]:
    return {'schema':REVIEW_SCHEMA,'packet_sha256':gate.digest_json(packet),
        'reviewer':{'full_name':'','affiliation':'','qualification':'','independence_declaration':'',
                    'review_date':'','not_author_of_V3_code_or_gold':False,
                    'outside_contract_specification_provided':False},
        'primary_sources':[],
        'findings':[{'id':rid,'finding':'UNRESOLVED','primary_source_ids':[],
                     'reviewer_reference_location':'','normative_requirement':'','reasoning':''}
                    for rid,_,_,_ in CLAIMS]}


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=('prepare','adjudicate'),required=True)
    p.add_argument('--report',type=Path)
    p.add_argument('--gold',type=Path)
    p.add_argument('--baseline-cert',type=Path)
    p.add_argument('--mutation-report',type=Path)
    p.add_argument('--packet',type=Path)
    p.add_argument('--review',type=Path)
    p.add_argument('--review-template',type=Path)
    p.add_argument('--repo-root',type=Path,default=Path.cwd())
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    try:
        gate.require(not args.output.exists(),'output exists; refuse overwrite')
        if args.mode=='prepare':
            gate.require(all([args.report,args.gold,args.baseline_cert,args.mutation_report]),'prepare requires report, gold, baseline and mutation report')
            packet=prepare(*(gate.read_json(x) for x in (args.report,args.gold,args.mutation_report,args.baseline_cert)))
            gate.atomic_json(args.output,packet)
            if args.review_template:
                gate.require(not args.review_template.exists(),'review template exists; refuse overwrite')
                gate.atomic_json(args.review_template,empty_review(packet))
            print(json.dumps({'status':packet['status'],'claims':len(packet['claims']),'output':str(args.output)}))
            return 0
        gate.require(args.packet and args.review,'adjudicate requires packet and independently authored review')
        result=adjudicate(gate.read_json(args.packet),gate.read_json(args.review),repo_root=args.repo_root)
        gate.atomic_json(args.output,result)
        print(json.dumps({'status':result['status'],'conflicts':result['conflicts'],'unresolved':result['unresolved']}))
        return 0 if result['status'].startswith('PASS_') else 1
    except (gate.CertificationFailure,ValueError,KeyError,OSError,TypeError) as exc:
        result={'schema':RESULT_SCHEMA,'status':'FAIL_SEMANTIC_ADJUDICATION_NOT_VERIFIED','blockers':[str(exc)]}
        if not args.output.exists():gate.atomic_json(args.output,result)
        print(json.dumps(result),file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
