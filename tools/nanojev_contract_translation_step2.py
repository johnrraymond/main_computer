#!/usr/bin/env python3
"""Step 2: live natural-language -> validated v2 Contract Language *draft*.

No Solidity output, no Docker, no wallet, no deployment. This is a diagnostic
translator, not proof of semantic correctness or a substitute for user approval.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Callable

from tools import nanojev_contract_composition_smoke as comp
from tools import nanojev_contract_liveness as liveness
from tools import nanojev_contract_pull_payments as pull
from tools import nanojev_contract_pull_smoke as head

EXAMPLE = ("I want to pay a programmer 3 ETH to complete a project within seven days. "
           "I'll deposit the money upfront. If the programmer submits the work, "
           "I can approve it and release payment. If I don't respond, the programmer "
           "can claim payment after seven days, provided the work was submitted. "
           "If no work was submitted by the deadline, the money should be refundable "
           "to me. Neither party should be able to permanently lock the money by "
           "refusing to cooperate.")

# This is a diagnostic display/example only, not model output and not a gold
# specification that the model is permitted to use or see.
DEMONSTRATION_IR = {
    'language': 'nanojev.contract.state_machine.v2',
    'settlement_mode': 'pull',
    'asset': 'native_eth',
    'payer': 'deployer', 'worker': 'constructor_worker',
    'reviewer': 'constructor_reviewer',
    'amount_wei': 3_000_000_000_000_000_000,
    'deadline_seconds': 7 * 86400,
    'rules': [
        {'name':'submit', 'actor':'worker','from':'funded','to':'submitted',
         'when':'before_deadline','payments':[]},
        {'name':'approve', 'actor':'payer','from':'submitted','to':'settled',
         'when':'always','payments':[{'to':'worker','bps':10000}]},
        {'name':'fallback_approve', 'actor':'worker','from':'submitted','to':'settled',
         'when':'at_or_after_deadline','payments':[{'to':'worker','bps':10000}]},
        {'name':'refund', 'actor':'payer','from':'funded','to':'refunded',
         'when':'at_or_after_deadline','payments':[{'to':'payer','bps':10000}]},
        {'name':'fallback_refund', 'actor':'worker','from':'funded','to':'refunded',
         'when':'at_or_after_deadline','payments':[{'to':'payer','bps':10000}]},
    ],
}

# Interpretive issues never automatically disappear merely because the
# structural validator and liveness checker pass.
EXAMPLE_ISSUES = [
    'The on-chain submit operation proves only that the worker called submit; it does not verify that software was delivered or satisfactory.',
    'The seven-day deadline is measured from funding, not submission. A submission immediately before the deadline enables fallback payment immediately afterward.',
    'The worker-triggered fallback_refund authority is the requested recovery correction; confirm all role permissions before any deployment.',
    'V2 requires a distinct reviewer constructor address even though this example does not use the reviewer; that deployment requirement must be resolved.',
    'A recipient may still decline to withdraw credit, and a transfer to a rejecting destination may fail; credited settlement is independent of recipient acceptance.',
]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def process(request: str, response: str, *, mode: str, example: bool) -> dict:
    """Fail closed on malformed output; do not edit model semantics."""
    if not isinstance(request, str) or not request.strip():
        raise ValueError('A nonempty natural-language request is required')
    obj = comp.parse(response)
    result = {
        'schema':'nanojev.contract.translation_step2.v1',
        'scope':'TRANSLATION_ONLY_NO_SOLIDITY_NO_DEPLOYMENT',
        'mode':mode,
        'request_sha256':digest(request),
        'response_sha256':digest(response),
        'semantic_gold':'NOT_PROVIDED',
        'independent_semantic_correctness':'NOT_ESTABLISHED',
        'candidate':None,
        'liveness':None,
        'user_confirmation_required':True,
        'open_issues':EXAMPLE_ISSUES if example else [
            'Review the generated contract against the original request, including on-chain versus off-chain evidence, all role authorities, deadlines, and recipient allocations.',
            'The language supports only bounded, one-time native-ETH state machines and cannot verify off-chain performance.',
        ],
    }
    if obj.get('status') == 'needs_clarification':
        if set(obj) != {'status','questions'} or not isinstance(obj['questions'],list) or not 1 <= len(obj['questions']) <= 12 or any(
                not isinstance(q,str) or not q.strip() or len(q)>400 for q in obj['questions']):
            raise ValueError('Malformed model clarification')
        result['status']='NEEDS_CLARIFICATION'
        result['questions']=obj['questions']
        return result
    if set(obj) != {'status','contract'} or obj.get('status') != 'contract':
        raise ValueError('Invalid translation status or response fields')
    contract = pull.validate(obj['contract'])
    analysis = liveness.evaluate(pull.old(contract))
    result['candidate']=contract
    # The model checks role unavailability while the v2 compiler credits
    # balances instead of pushing ETH. A withdrawal itself can still fail.
    result['liveness']={
        'status': analysis['status'],
        'checked_reachable_nodes':analysis['checked_reachable_nodes'],
        'checked_role_unavailability_scenarios':analysis['checked_role_unavailability_scenarios'],
        'unconditional_traps':analysis['unconditional_traps'],
        'single_role_traps':analysis['single_role_traps'],
        'settlement_payment_mode':'v2_pull_credit',
        'receiver_refusal_blocks_credit_settlement':False,
        'withdrawal_success_unconditional':False,
        'assumptions':[
            'Other authorized roles eventually call permitted operations',
            'Block time progresses and the chain includes valid transactions',
            'Recipient withdrawals are separately initiated and may fail',
        ],
    }
    result['status'] = ('DRAFT_REQUIRES_USER_CONFIRMATION' if analysis['status']=='PASS_CONDITIONAL'
                        else 'BLOCKED_LIVENESS_RISK')
    return result




def approved_example_recovery(previous: dict, *, source_sha256: str) -> dict:
    """Apply ONLY the two explicitly authorized corrections to the Step 2 example.

    This is a deterministic edit of a previously captured LLM candidate, NOT a
    second LLM interpretation and NOT independent semantic certification. The
    input is guarded so an unrelated contract cannot receive an invented rescue.
    """
    if (not isinstance(previous, dict) or
            previous.get('schema') != 'nanojev.contract.translation_step2.v1' or
            previous.get('scope') != 'TRANSLATION_ONLY_NO_SOLIDITY_NO_DEPLOYMENT' or
            previous.get('mode') != 'LIVE_OLLAMA_LLM' or
            previous.get('status') != 'BLOCKED_LIVENESS_RISK' or
            previous.get('request_sha256') != digest(EXAMPLE)):
        raise ValueError('Recovery is restricted to the reported, blocked live 3 ETH programmer example')

    spec = pull.validate(previous.get('candidate'))
    if (spec['amount_wei'] != 3 * 10**18 or
            spec['deadline_seconds'] != 7 * 86400 or
            spec['settlement_mode'] != 'pull' or len(spec['rules']) != 4):
        raise ValueError('Unexpected contract amount, deadline, settlement mode, or number of rules')

    original_liveness = liveness.evaluate(pull.old(spec))
    expected_traps = {
        ('funded', 'at_or_after_deadline', 'payer'),
        ('submitted', 'at_or_after_deadline', 'worker'),
    }
    traps = {(t['state'], t['phase'], t['unavailable_role'])
             for t in original_liveness['single_role_traps']}
    if (original_liveness['status'] != 'FAIL_TRAPPED_FUNDS_RISK' or
            original_liveness['unconditional_traps'] or traps != expected_traps):
        raise ValueError('Liveness failures do not match the two user-approved corrections')

    def select(actor: str, src: str, guard: str, payments: list[dict]):
        matches = [r for r in spec['rules'] if r['actor'] == actor and
                   r['from'] == src and r['when'] == guard and
                   r['payments'] == payments]
        if len(matches) != 1:
            raise ValueError('Original contractual actions are not uniquely identifiable')
        return matches[0]

    worker_credit = [{'to':'worker', 'bps':10000}]
    payer_credit = [{'to':'payer', 'bps':10000}]
    submission = select('worker', 'funded', 'before_deadline', [])
    if submission['to'] != 'submitted':
        raise ValueError('Submission does not enter the expected intermediate state')
    approval = select('payer', 'submitted', 'before_deadline', worker_credit)
    worker_claim = select('worker', 'submitted', 'at_or_after_deadline', worker_credit)
    refund = select('payer', 'funded', 'at_or_after_deadline', payer_credit)
    if (len({submission['name'], approval['name'], worker_claim['name'], refund['name']}) != 4 or
            approval['to'] != worker_claim['to'] or
            approval['to'] == refund['to'] or
            'fallback_refund' in {r['name'] for r in spec['rules']}):
        raise ValueError('Terminal outcomes or identifiers differ from the approved example')

    repaired = copy.deepcopy(spec)
    for rule in repaired['rules']:
        if rule['name'] == approval['name']:
            rule['when'] = 'always'  # payer can approve even after seven days
    repaired['rules'].append({
        'name':'fallback_refund',
        'actor':'worker',
        'from':'funded',
        'to':refund['to'],
        'when':'at_or_after_deadline',
        'payments':copy.deepcopy(payer_credit),
    })

    outcome = process(EXAMPLE, json.dumps({'status':'contract','contract':repaired}),
                      mode='APPROVED_DETERMINISTIC_RECOVERY_NOT_LLM', example=True)
    if (outcome['status'] != 'DRAFT_REQUIRES_USER_CONFIRMATION' or
            outcome['liveness']['status'] != 'PASS_CONDITIONAL' or
            outcome['liveness']['single_role_traps'] or
            outcome['liveness']['unconditional_traps']):
        raise ValueError('Corrected contract failed full validation and conditional liveness')

    # Postcondition: original actions and all non-guard semantics remain intact.
    old_rules = {r['name']:r for r in spec['rules']}
    new_rules = {r['name']:r for r in outcome['candidate']['rules']}
    if set(new_rules) != set(old_rules) | {'fallback_refund'}:
        raise AssertionError('Unexpected action was added or removed')
    for name, old_rule in old_rules.items():
        expected = copy.deepcopy(old_rule)
        if name == approval['name']:
            expected['when'] = 'always'
        if new_rules[name] != expected:
            raise AssertionError('An unauthorized contractual semantic was changed')
    if any(outcome['candidate'][k] != spec[k] for k in spec if k != 'rules'):
        raise AssertionError('Contract metadata unexpectedly changed')

    def ir_hash(value: dict) -> str:
        return digest(json.dumps(value, sort_keys=True, separators=(',', ':')))

    outcome['original_llm_request_sha256'] = previous['request_sha256']
    outcome['original_llm_response_sha256'] = previous.get('response_sha256')
    outcome['recovery_provenance'] = {
        'method': 'EXPLICIT_USER_AUTHORIZED_DETERMINISTIC_EDIT',
        'source_report_sha256': source_sha256,
        'original_candidate_sha256': ir_hash(spec),
        'revised_candidate_sha256': ir_hash(outcome['candidate']),
        'original_liveness_status': original_liveness['status'],
        'original_traps': original_liveness['single_role_traps'],
        'modifications': [
            {'action': approval['name'], 'field': 'when',
             'from': 'before_deadline', 'to': 'always'},
            {'action': 'fallback_refund', 'change': 'ADD_ACTION',
             'actor': 'worker', 'from_state':'funded', 'to_state':refund['to'],
             'when': 'at_or_after_deadline',
             'recipient': 'payer', 'basis_points':10000},
        ],
        'unchanged_original_actions': len(spec['rules']),
        'no_new_llm_call': True,
        'no_solidity_or_deployment': True,
    }
    return outcome

def execute(request: str, *, model: str, ollama_url: str,
            timeout: float, infer: Callable[[str],str] | None = None,
            offline_json: str | None = None, example: bool = False) -> dict:
    if offline_json is not None:
        return process(request, offline_json, mode='OFFLINE_RESPONSE_NO_LLM',example=example)
    if infer is not None:
        return process(request,infer(request),mode='INJECTED_TEST_HEAD_NOT_LIVE',example=example)
    raw = head.real_head(ollama_url,model,request,timeout)
    return process(request,raw,mode='LIVE_OLLAMA_LLM',example=example)


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--request-file',type=Path,help='UTF-8 file with the natural-language agreement (default: the 3 ETH programmer example)')
    p.add_argument('--llm-model',default='gemma4:26b-a4b-it-q4_K_M')
    p.add_argument('--ollama-url',default='http://127.0.0.1:11434')
    p.add_argument('--llm-timeout-s',type=float,default=240.)
    p.add_argument('--offline-response-file',type=Path,help='Diagnostic only; verify supplied model JSON without calling an LLM')
    p.add_argument('--repair-from-report',type=Path,help='Apply the two explicitly approved recovery corrections to the blocked Step 2 example report; no new LLM call')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    result = {'schema':'nanojev.contract.translation_step2.v1',
              'status':'BLOCKED_UNVERIFIED','scope':'TRANSLATION_ONLY_NO_SOLIDITY_NO_DEPLOYMENT'}
    try:
        if args.repair_from_report is not None:
            if args.request_file is not None or args.offline_response_file is not None:
                raise ValueError('Recovery report cannot be combined with new request or response inputs')
            source = args.repair_from_report.read_bytes()
            prior = json.loads(source.decode('utf-8'))
            result = approved_example_recovery(prior,
                source_sha256=hashlib.sha256(source).hexdigest())
        else:
            request = (args.request_file.read_text(encoding='utf-8')
                       if args.request_file else EXAMPLE)
            response = (args.offline_response_file.read_text(encoding='utf-8')
                        if args.offline_response_file else None)
            result = execute(request,model=args.llm_model,ollama_url=args.ollama_url,
                             timeout=args.llm_timeout_s,offline_json=response,
                             example=args.request_file is None)
    except Exception as exc:
        result['blockers']=[{'type':type(exc).__name__,'reason':str(exc)[:1000]}]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'mode':result.get('mode'),
                      'liveness':(result.get('liveness') or {}).get('status'),
                      'report':str(args.output)},sort_keys=True))
    return 1 if result['status'].startswith('BLOCKED') else 0


if __name__=='__main__':
    raise SystemExit(main())
