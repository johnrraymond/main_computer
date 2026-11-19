#!/usr/bin/env python3
"""Experimental pull-payment compilation + published-source behavior smoke.

Preserves the existing 56/105/138 smoke pipeline. No real contracts deployed.
Live head is tested on assistant-authored fixed semantic cases plus independently
published excerpts (negative / incomplete contract requests). Excerpts are
independently authored, but gold *annotations* and behavioral tests are not.
"""
from __future__ import annotations
import argparse
import copy
import json
import itertools
import re
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from tools import nanojev_contract_solidity_smoke as base
from tools import nanojev_contract_composition_smoke as comp
from tools import nanojev_contract_liveness_smoke as prior
from tools import nanojev_contract_liveness as liv
from tools import nanojev_contract_pull_payments as pull
from tools import nanojev_contract_v2_verifier as graph_verifier

EXTERNAL = Path(__file__).resolve().parent/'fixtures'/'nanojev_contract_pull_external_v1.json'
PIN_EXTERNAL = 'f67c46d2b25c2bdb6423360d8eb971d34275d66aad98972bdec44371b9cf1d18'

PROMPT = '''Translate the user's request into the v2 NanoJev Contract Language or refuse.
Return exactly one JSON object, no explanation or Markdown:
{"status":"contract","contract":{...}} or
{"status":"needs_clarification","questions":["specific missing fact"]}.
This is a bounded, one-time, single-funded-ETH state machine. There are no
self-triggering transactions, arbitrary oracle inputs, counters, recurring
installments, token transfers, off-chain verification, or loops. Do not invent
obligations, actions, roles, payments, or deadlines.
Required EXACT contract keys:
"language":"nanojev.contract.state_machine.v2",
"settlement_mode":"pull", "asset":"native_eth",
"payer":"deployer", "worker":"constructor_worker",
"reviewer":"constructor_reviewer", "amount_wei":positive integer,
"deadline_seconds":positive integer, "rules":list of 2..6 rules.
Payer is deployer; worker and reviewer are distinct constructor addresses.
Only payer funds exactly once using the compiler-provided fund() method;
this starts the deadline and enters state "funded" automatically.
Do NOT represent funding as a rule: NEVER create "fund_agreement",
"initial" -> "funded", or "unfunded" -> "funded". The rules array
contains ONLY user-specified post-funding actions, starting from "funded"
or a state reached after "funded". Funding itself is not a transition
in the Contract Language rules array, even if the request says "deposit".
For example, a cancel/release agreement requires TWO rules, cancel and
release, both starting from "funded"; do not add an initial funding rule.
Each rule has exactly {"name":"snake_case", "actor":"payer|worker|reviewer",
"from":"state", "to":"state",
"when":"always|before_deadline|at_or_after_deadline",
"payments":[{"to":"payer|worker|reviewer","bps":10000}]}.
Payouts must sum to exactly 10000 bps and make terminal states. All transitions
are manually called; deadline guards are relative to funding. No action is
implied or invented. Use explicit user-provided action and state names.
IMPORTANT: A transition entering a state that has any outgoing transition
MUST have "payments":[] (it is NOT a settlement). Only transitions entering
terminal states may carry payments; those payments MUST sum to 10000 bps.
For example, when work is submitted into state "submitted" and approval
may follow, the submission rule MUST have "payments":[]; the later approval
or refund rule may credit the full payout when it ends the state machine.
Do not confuse record-submission actions with credit-creating settlement.
On settlement, payments are recorded as pending ETH credits, not pushed.
Each credited recipient may later independently withdraw or direct ETH to
an alternate receiving address. Settlement is unaffected by a rejecting
recipient. No recipient funds transfer occurs until withdrawal.
1 ETH = 1000000000000000000 wei; 1 day = 86400 seconds.
Unclear, contradictory, out-of-domain, or incomplete descriptions must be
needs_clarification, never completed by inference.
'''


@dataclass(frozen=True)
class Options:
    output: Path
    liveness_report: Path = Path('runtime/reports/nanojev-contract-liveness-smoke-0001.json')
    composition_report: Path = Path('runtime/reports/nanojev-contract-composition-smoke-0001.json')
    expansion_report: Path = Path('runtime/reports/nanojev-contract-language-expansion-smoke-0002.json')
    model: str = base.DEFAULT_MODEL
    ollama_url: str = base.DEFAULT_URL
    foundry_image: str = base.DEFAULT_IMAGE
    network: str = 'bridge'
    llm_timeout: float = 240.0
    forge_timeout: float = 360.0
    compiler_only: bool = False
    dry_run: bool = False
    independent_cases: Path | None = None


def external_sources() -> list[dict]:
    wire=EXTERNAL.read_bytes()
    if base.digest(wire)!=PIN_EXTERNAL:
        raise base.SmokeFailure('preflight','External published-behavior corpus has changed')
    obj=json.loads(wire.decode('utf-8','strict'))
    if obj['schema']!='nanojev.contract.pull.external-published-oracles.v1' or len(obj['cases'])!=3:
        raise base.SmokeFailure('preflight','Missing three separately sourced behavioral requirements')
    if len({x['id'] for x in obj['cases']})!=3:
        raise base.SmokeFailure('preflight','Duplicate published-behavior source')
    for c in obj['cases']:
        if not all(c.get(k) for k in ('source_title','source_url','source_excerpt','gold_claim','forge_test')):
            raise base.SmokeFailure('preflight','External source metadata incomplete')
        if c['llm_expectation']!='needs_clarification' or len(c['source_excerpt'].split())>25:
            raise base.SmokeFailure('preflight','Excerpts must be brief and out-of-domain for positive compilation')
    return obj['cases']


def validate_liveness_baseline(path: Path) -> str:
    if not path.is_file():
        raise base.SmokeFailure('preflight','Required live 138/138 liveness report missing: '+str(path))
    wire=path.read_bytes()
    try: data=json.loads(wire.decode('utf-8','strict'))
    except (ValueError,UnicodeError) as exc:
        raise base.SmokeFailure('preflight','Invalid prior liveness report') from exc
    s=data.get('stages',{})
    if (data.get('status')!='PASS_SMOKE_NOT_CERTIFIED' or data.get('compiler_only') is not False
            or data.get('expected_foundry_tests')!=138 or data.get('blockers')
            or s.get('head')!='PASS_LIVE' or s.get('forge_tests_passed')!=138
            or s.get('liveness_gate')!='PASS_UNSAFE_BLOCKED_SAFE_CONDITIONAL'
            or s.get('compiled_artifacts')!='PASS'
            or any(not isinstance(s.get('forge_'+op),dict) or s['forge_'+op].get('exit_code')!=0
                   for op in ('build','test'))):
        raise base.SmokeFailure('preflight','Live, not injected, 138/138 prior baseline is required')
    return base.digest(wire)


def real_head(url: str, model: str, request: str, timeout: float, *, prompt: str = PROMPT) -> str:
    endpoint=base.local_url(url)
    if not isinstance(model,str) or not 0<len(model)<=256:
        raise base.SmokeFailure('preflight','Invalid model')
    payload={'model':model,'stream':False,'think':False,'format':'json',
             'options':{'temperature':0,'seed':261118,'num_predict':3500},
             'messages':[{'role':'system','content':prompt},
                         {'role':'user','content':request}]}
    try:
        req=Request(endpoint+'/api/chat',data=base.canonical(payload).encode('utf-8'),
                    headers={'Content-Type':'application/json; charset=utf-8'},method='POST')
        with urlopen(req,timeout=timeout) as r: wire=r.read(400001)
    except Exception as e:
        raise base.SmokeFailure('llm','Local Ollama failed: '+type(e).__name__+': '+str(e)[:250]) from e
    if len(wire)>400000:
        raise base.SmokeFailure('llm','Ollama response too large')
    try: val=json.loads(wire.decode('utf-8','strict'))['message']['content']
    except (ValueError,TypeError,KeyError,UnicodeError) as e:
        raise base.SmokeFailure('llm','Malformed Ollama response') from e
    if not isinstance(val,str) or not val.strip():
        raise base.SmokeFailure('llm','No content in Ollama response')
    return val


def cases() -> list[dict]:
    originals=prior.prepare_cases()
    out=[]
    for item in originals:
        c={**item}
        if c['family']!='needs_clarification':
            c['expected']=pull.upgrade(c['expected'])
        c['request']=(c['request'].rstrip()+
            '\nThe settlement mode is PULL: upon settlement, credit each beneficiary; do not push ETH. '+
            'Each beneficiary can separately withdraw or redirect their own credit. '+
            'Recipient rejection must not stop the settlement transaction.')
        out.append(c)
    return out


def _terminal_outcome_hint(label: str) -> str | None:
    """Conservative check for explicit, human-readable outcome words.

    The graph provides the contractual meaning; names that claim the opposite
    outcome must still be refused.  Unknown outcome names cannot be certified
    as safe synonyms and are left for clarification / future typed IR.
    """
    tokens=set(label.split('_'))
    if tokens & {'refunded','refund','cancelled','canceled'}:
        return 'return_to_payer'
    if tokens & {'settled','approved','released','paid'}:
        return 'successful_settlement'
    return None


def terminal_state_alpha_map(actual: dict, expected: dict) -> dict[str, str] | None:
    """Semantic identity modulo *terminal-only* state labels.

    Contract rules are matched by typed action identifier, actor, source state,
    deadline guard and normalized payments.  Intermediate state names must match.
    Terminal-to-terminal remapping must be total, injective, and consistent
    across all incoming transitions.  Never accept swapped existing gold labels,
    aliased intermediate states, merged terminal states, or changed payouts.

    This equivalence is restricted to the diagnostic IR's internal terminal
    states.  It does not assert ABI identity or authorize changing executable
    operations and does not rely on gold to rewrite the LLM's contract.
    """
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return None
    if {k:v for k,v in actual.items() if k != 'rules'} != {k:v for k,v in expected.items() if k != 'rules'}:
        return None
    a_rules = {r['name']:r for r in actual['rules']}
    e_rules = {r['name']:r for r in expected['rules']}
    if a_rules.keys() != e_rules.keys():
        return None
    a_sources = {r['from'] for r in a_rules.values()}
    e_sources = {r['from'] for r in e_rules.values()}
    if a_sources != e_sources:  # states with outgoing transitions are fixed
        return None
    a_terminals = {r['to'] for r in a_rules.values()} - a_sources
    e_terminals = {r['to'] for r in e_rules.values()} - e_sources
    if len(a_terminals) != len(e_terminals):
        return None
    state_map: dict[str,str] = {}
    for key, gold in e_rules.items():
        rule = a_rules[key]
        if any(rule[k] != gold[k] for k in ('name','actor','from','when','payments')):
            return None
        if gold['to'] in e_terminals:
            if rule['to'] not in a_terminals:
                return None
            # A known canonical terminal label cannot be reassigned to the
            # opposite outcome; this also protects against terminal swaps.
            if rule['to'] in e_terminals and rule['to'] != gold['to']:
                return None
            # If a human-readable label is changed, the new label must
            # unambiguously indicate the same kind of terminal outcome.
            if rule['to'] != gold['to']:
                expected_hint=_terminal_outcome_hint(gold['to'])
                actual_hint=_terminal_outcome_hint(rule['to'])
                if expected_hint is None or actual_hint != expected_hint:
                    return None
            prev = state_map.setdefault(gold['to'],rule['to'])
            if prev != rule['to']:
                return None
        elif rule['to'] != gold['to']:
            return None
    if set(state_map) != e_terminals or set(state_map.values()) != a_terminals:
        return None
    if len(set(state_map.values())) != len(state_map):
        return None
    return state_map



# Rule names are emitted as externally visible Solidity ABI method names.
# Semantic comparison may accept only an added/removed *non-operative* noun
# qualifier, never a different operation verb or unrelated method name.
_ACTION_QUALIFIERS = frozenset({
    'agreement', 'contract', 'work', 'task', 'delivery', 'project',
    'request', 'payment', 'funds', 'deposit', 'review', 'payout', 'claim',
})


def _qualified_action_alias(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    a, e = actual.split('_'), expected.split('_')
    short, long = (a, e) if len(a) < len(e) else (e, a)
    suffix = long[len(short):]
    return bool(suffix) and len(suffix) <= 2 and long[:len(short)] == short and all(
        token in _ACTION_QUALIFIERS for token in suffix)


def semantic_alpha_maps(actual: dict, expected: dict) -> tuple[dict[str,str], dict[str,str]] | None:
    """Match typed behavior before accepting qualified ABI action aliases.

    Return (terminal_state_gold_to_model, action_name_gold_to_model), where
    the second mapping includes only changed names.  A candidate must pass the
    strict v2 validator before entry.  Every rule must have one unique pairing,
    and the original terminal-alias checker revalidates *all* typed fields
    after the action-name comparison.  Ambiguous bijections are refused.

    This is semantic behavior equivalence, NOT Solidity ABI equivalence.
    """
    exact = terminal_state_alpha_map(actual, expected)
    if exact is not None:
        return {k:v for k,v in exact.items() if k != v}, {}
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return None
    a_rules = actual.get('rules')
    e_rules = expected.get('rules')
    if not isinstance(a_rules, list) or not isinstance(e_rules, list) or len(a_rules) != len(e_rules):
        return None
    if not 2 <= len(a_rules) <= 6:
        return None
    matches=[]
    for pairing in itertools.permutations(a_rules):
        if not all(_qualified_action_alias(candidate['name'], gold['name'])
                   for candidate,gold in zip(pairing,e_rules)):
            continue
        renamed = {candidate['name']:gold['name'] for candidate,gold in zip(pairing,e_rules)}
        if len(renamed) != len(e_rules):
            continue
        # Recompute only action *identifiers* for comparison; neither the
        # actual IR nor the independently specified gold is mutated.
        comparable = copy.deepcopy(actual)
        for rule in comparable['rules']:
            rule['name']=renamed[rule['name']]
        state_map=terminal_state_alpha_map(comparable,expected)
        if state_map is None:
            continue
        action_map={gold['name']:candidate['name'] for candidate,gold in zip(pairing,e_rules)
                    if gold['name'] != candidate['name']}
        key=(tuple(sorted(state_map.items())),tuple(sorted(action_map.items())))
        if key not in [item[0] for item in matches]:
            matches.append((key,state_map,action_map))
        if len(matches)>1:
            return None
    if len(matches)!=1:
        return None
    _,state_map,action_map=matches[0]
    return {k:v for k,v in state_map.items() if k != v},action_map


def gold_with_verified_alpha_aliases(expected: dict, actual: dict) -> dict:
    """Preserve independent gold expectations while binding only verified ABI labels.

    Used strictly to call the generated LLM methods by their actual names in
    gold-derived Forge tests.  No payments, roles, states, or guards are
    supplied from the model to the gold tests, and no gold values are supplied
    to the model or the Solidity emitter.
    """
    maps = semantic_alpha_maps(actual, expected)
    if maps is None:
        raise base.SmokeFailure('semantic_gold','Cannot remap independent oracle: non-equivalent transition graphs')
    state_map, action_map = maps
    oracle = copy.deepcopy(expected)
    for rule in oracle['rules']:
        if rule['to'] in state_map:
            rule['to'] = state_map[rule['to']]
        if rule['name'] in action_map:
            rule['name'] = action_map[rule['name']]
    if pull.validate(oracle) != pull.validate(actual):
        raise base.SmokeFailure('semantic_gold','Semantic alias mapping failed exact revalidation')
    return oracle


def gold_with_verified_terminal_aliases(expected: dict, actual: dict) -> dict:
    """Compatibility name for the checked semantic/ABI oracle adapter."""
    return gold_with_verified_alpha_aliases(expected, actual)


def validate_head(case: dict, raw: str) -> dict | None:
    obj=comp.parse(raw)
    if case['family']=='needs_clarification':
        if set(obj)!={'status','questions'} or obj['status']!='needs_clarification':
            raise base.SmokeFailure('semantic_gold','Unsupported/ambiguous request accepted: '+case['id'])
        questions=obj['questions']
        if not isinstance(questions,list) or not 1<=len(questions)<=12 or any(
                not isinstance(q,str) or not q.strip() or len(q)>400 for q in questions):
            raise base.SmokeFailure('semantic_gold','Malformed clarification: '+case['id'])
        return None
    if set(obj)!={'status','contract'} or obj['status']!='contract':
        raise base.SmokeFailure('semantic_gold','Representable request refused: '+case['id'])
    actual=pull.validate(obj['contract'])
    expected=pull.validate(case['expected'])
    if semantic_alpha_maps(actual,expected) is None:
        diff={k:{'actual':actual[k],'gold':expected[k]} for k in actual if actual[k]!=expected[k]}
        raise base.SmokeFailure('semantic_gold','Wrong semantics '+case['id']+': '+base.canonical(diff)[:1500])
    return actual


def _structural_retry_kind(invalid_json: str, error: base.SmokeFailure) -> str | None:
    """Recognize only two evidenced LLM construction mistakes.

    Never normalize or discard LLM rules. The validator and semantic-gold
    comparator remain authoritative after a single model-side correction.
    """
    if error.stage != 'contract_validation':
        return None
    if error.reason == 'Transitions after settlement forbidden':
        return 'NONTERMINAL_PAYMENT'
    if error.reason != 'Unsafe, duplicate or invalid identifier':
        return None
    try:
        candidate = comp.parse(invalid_json)
    except base.SmokeFailure:
        return None
    if candidate.get('status') != 'contract' or not isinstance(candidate.get('contract'),dict):
        return None
    rules = candidate['contract'].get('rules')
    if not isinstance(rules,list):
        return None
    # A post-funding rules array must never recreate the built-in fund().
    # Require an explicit initial/unfunded -> funded edge, as seen in the
    # live failure, rather than retrying unrelated bad identifiers.
    forbidden = [r for r in rules if isinstance(r,dict)
                 and r.get('from') in ('initial','unfunded')
                 and r.get('to') == 'funded'
                 and isinstance(r.get('name'),str)
                 and isinstance(r.get('payments'),list)
                 and r['payments'] == []]
    return 'DUPLICATE_IMPLICIT_FUNDING' if len(forbidden) == 1 else None


def structural_retry_request(original_request: str, invalid_json: str, error: base.SmokeFailure) -> str:
    """Supply a precise diagnostic, not the gold or a silently edited IR."""
    kind = _structural_retry_kind(invalid_json,error)
    if kind is None:
        raise ValueError('No eligible, identified structural Contract Language defect')
    if kind == 'NONTERMINAL_PAYMENT':
        instruction = (
            'An intermediate transition (a state with outgoing transitions) '
            'must have "payments":[]; payout credits belong only to terminal transitions.\n'
            'Do not confuse a work-submission event with settlement.\n')
    else:
        instruction = (
            'The compiler already supplies fund() as the ONLY funding operation. '
            'The built-in fund() moves from unfunded to funded and starts the '
            'deadline. Do NOT include a funding transition in rules, including '
            'an initial/unfunded -> funded rule or fund_agreement. '
            'Rules contain only post-funding actions starting from funded.\n'
            'Do not add an equivalent replacement funding rule.\n')
    return (original_request +
            '\n\nCOMPILER DIAGNOSTIC (for correction; not a change to user intent):\n' +
            'The previous JSON was rejected by the strict Contract Language validator.\n' +
            'Validator error: ' + error.reason[:500] + '\n' +
            'Identified issue: ' + kind + '\n' + instruction +
            'Do not invent, omit, reorder semantically, or change any '
            'user-specified obligations. If the requested behavior cannot be '
            'expressed unambiguously, return needs_clarification.\n' +
            'Previous invalid model output:\n' + invalid_json[:12000] + '\n' +
            'Return one complete corrected JSON object with the original meaning.')


def infer_with_bounded_structural_retry(case: dict, infer: Callable[[str],str], entry: dict) -> dict | None:
    """One bounded model-side repair; never retry a semantic-gold mismatch."""
    raw=infer(case['request'])
    entry['llm_attempts']=1
    entry['llm_output_sha256']=base.digest(raw)
    entry['llm_output']=raw[:4000]
    try:
        return validate_head(case,raw)
    except base.SmokeFailure as failure:
        kind = _structural_retry_kind(raw,failure)
        if kind is None:
            raise
        entry['first_attempt_validation_error']=failure.reason
        entry['first_attempt_output_sha256']=base.digest(raw)
        entry['first_attempt_output']=raw[:4000]
        entry['structural_retry_kind']=kind
        print('[PULL] LLM structural correction '+case['id']+' ('+kind+')',flush=True)
        correction=structural_retry_request(case['request'],raw,failure)
        entry['structural_retry_prompt_sha256']=base.digest(correction)
        repaired=infer(correction)
        entry['llm_attempts']=2
        entry['llm_output_sha256']=base.digest(repaired)
        entry['llm_output']=repaired[:4000]
        result=validate_head(case,repaired)
        entry['structural_retry_result']='PASSED_ORIGINAL_VALIDATORS'
        return result


def external_tests(spec: dict, name: str) -> tuple[str,int]:
    """Tests from three published, externally authored behavioral REQUIREMENTS.
    Test design remains assistant-authored. Only multi-payee rules can satisfy
    the independently published split-payee examples.
    """
    allowed=[r for r in spec['rules'] if len(r['payments'])>=2 and r['from']=='funded'
             and r['actor'] in ('payer','reviewer') and r['when']=='at_or_after_deadline'
             and {'worker','reviewer'} <= {x['to'] for x in r['payments']}]
    if not allowed:
        return '',0
    action=allowed[0]['name']
    actor='REVIEWER' if allowed[0]['actor']=='reviewer' else 'address(this)'
    snippet=f'''    function testExternalWithdrawalPatternRejectorIsolation() public {{
        {name} c = funded(true);
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank({actor});
        c.{action}();
        require(c.pendingWithdrawals(address(c.worker())) > 0, "rejector blocked credit");
        uint256 reviewerCredit = c.pendingWithdrawals(REVIEWER);
        uint256 previous = DESTINATION.balance;
        vm.prank(REVIEWER);
        c.withdrawTo(DESTINATION);
        require(DESTINATION.balance - previous == reviewerCredit, "rejector blocked independent beneficiary");
    }}
    function testExternalOpenZeppelinDeferredPayment() public {{
        {name} c = funded(false);
        vm.warp(c.fundedAt() + DEADLINE);
        uint256 beforeWorker = WORKER.balance;
        uint256 beforeReviewer = REVIEWER.balance;
        vm.prank({actor});
        c.{action}();
        require(WORKER.balance == beforeWorker && REVIEWER.balance == beforeReviewer, "pushed funds at settlement");
        require(address(c).balance == AMOUNT && c.totalPending() == AMOUNT, "failed deferred custody");
    }}
    function testExternalIndependentPayeeWithdrawals() public {{
        {name} c = funded(false);
        vm.warp(c.fundedAt() + DEADLINE);
        vm.prank({actor});
        c.{action}();
        uint256 reviewerCredit = c.pendingWithdrawals(REVIEWER);
        vm.prank(WORKER);
        c.withdrawTo(DESTINATION);
        require(c.pendingWithdrawals(REVIEWER) == reviewerCredit && reviewerCredit > 0,
                "one withdrawal changed the other's share");
    }}
'''
    return snippet,3


def add_recovery_tests(spec:dict,name:str) -> tuple[str,int]:
    """Recovery witnesses from the independently specified v1 state graph.
    Test settlement = credit liabilities, NOT contract ETH balance dropping to zero.
    """
    v1=pull.old(spec)
    paths=liv.reachable(v1)
    rules={r['name']:r for r in v1['rules']}
    snippets=[]
    for (state,phase),arrival in sorted(paths.items()):
        for blocked in liv.ROLES:
            recovery=liv.recovery_path(v1,(state,phase),blocked)
            if recovery is None:
                raise base.SmokeFailure('liveness','No recovery route for '+str((state,phase,blocked)))
            actions=arrival+recovery
            for rejecting_worker in (False,True):
                title=('RejectingRecipient' if rejecting_worker else '')
                lines=[f'    function testRecovery{state.title().replace("_","")}{phase.title().replace("_","")}{blocked.title()}{title}() public {{',
                       f'        {name} c = funded({str(rejecting_worker).lower()});']
                for a in actions:
                    if a=='wait_until_deadline':lines+=['        vm.warp(c.fundedAt() + DEADLINE);']
                    else:
                        rule=rules[a]
                        who={'payer':'address(this)','worker':'address(c.worker())','reviewer':'REVIEWER'}[rule['actor']]
                        lines += [f'        vm.prank({who});',f'        c.{a}();']
                lines += ['        require(c.totalPending() == AMOUNT, "recovery did not credit all funds");',
                          '        require(address(c).balance == AMOUNT, "recovery pushed ETH");',
                          '    }']
                snippets.append('\n'.join(lines))
    return '\n'.join(snippets)+'\n',len(snippets)


def insert_tests(test_source: str, append: str) -> str:
    if not test_source.rstrip().endswith('}'):
        raise base.SmokeFailure('solidity_emit','Malformed test source')
    return test_source.rstrip()[:-1]+'\n'+append+'}\n'


def run(options: Options, *, head: Callable[[str],str] | None=None,
        docker: Callable[[list[str],float],dict] | None=None,
        docker_available: bool | None=None) -> dict:
    output=options.output.resolve()
    workspace=output.with_name(output.stem+'.workspace')
    result={'schema':'nanojev.contract-pull-smoke.v1','status':'BLOCKED_NOT_VERIFIED',
            'started_at':base.now(), 'mode':'READ_ONLY_NO_KEYS_NO_DEPLOYMENT_NO_MODEL_MUTATION',
            'implementation':'v2_credit_ledger_+_permissioned_withdrawTo',
            'model':options.model,'compiler_only':options.compiler_only,
            'liveness_baseline_sha256':None,'composition_baseline_sha256':None,
            'expansion_baseline_sha256':None,
            'published_source_sha256':base.digest(EXTERNAL.read_bytes()),
            'runner_sha256':base.digest(Path(__file__).read_bytes()),
            'independent_expert_gold':'NOT_PROVIDED_OR_VERIFIED',
            'external_published_source_authorship':'DOCUMENTED_EXTERNAL_SOURCES_GOLD_TRANSLATION_AUTHOR_GENERATED',
            'workspace':str(workspace),'stages':{},'cases':[],'external_sources':[],
            'generated':{},'blockers':[]}
    try:
        sources=external_sources()
        result['external_sources']=[{'id':x['id'],'title':x['source_title'],
             'url':x['source_url'],'author':x['source_author'],
             'source_text_sha256':base.digest(x['source_excerpt']),
             'gold_claim':x['gold_claim'],'gold_origin':'experiment_author_translation'} for x in sources]
        original=cases()
        for x in sources:
            original.append({'id':x['id'],'family':'needs_clarification',
                'origin':'externally_authored_published_excerpt_not_complete_contract',
                'request':x['source_excerpt'],'reason':x['reason']})
        extras,extra_sha=prior.load_independent(options.independent_cases,{x['id'] for x in original})
        for x in extras:
            item={**x,'origin':'user_supplied_authorship_unverified'}
            if item['family']!='needs_clarification':item['expected']=pull.upgrade(item['expected'])
            original.append(item)
        result['user_supplied_corpus_sha256']=extra_sha
        result['user_supplied_cases']=len(extras)
        result['expansion_baseline_sha256']=comp.baseline(options.expansion_report)
        result['composition_baseline_sha256']=prior.composition_baseline(options.composition_report)
        result['liveness_baseline_sha256']=validate_liveness_baseline(options.liveness_report)
        if options.dry_run:
            result['status']='PLAN_ONLY_NOT_EXECUTED'
            result['planned_cases']=[{'id':x['id'],'family':x['family']} for x in original]
            return result
        if output.exists() or workspace.exists():
            raise base.SmokeFailure('preflight','Existing report/workspace: choose a new output name')
        if options.llm_timeout<=0 or options.forge_timeout<=0:
            raise base.SmokeFailure('preflight','Timeouts must be positive')
        if docker_available is None: docker_available=shutil.which('docker') is not None
        if not docker_available:
            raise base.SmokeFailure('preflight','Docker CLI unavailable; cannot verify Forge')
        base.local_url(options.ollama_url)
        result['stages']['preflight']='PASS_VERIFIED_56_105_138_LIVE_BASELINES'
        (workspace/'src').mkdir(parents=True)
        (workspace/'test').mkdir()
        (workspace/'foundry.toml').write_text(
            '[profile.default]\nsrc="src"\ntest="test"\nout="out"\nlibs=[]\nsolc_version="0.8.24"\noptimizer=true\noptimizer_runs=200\n',encoding='utf-8')
        accepted={}
        for c in original:
            entry={'id':c['id'],'origin':c['origin'],'family':c['family'],
                   'request_sha256':base.digest(c['request']), 'gold_excluded_from_head':True}
            result['cases'].append(entry)
            if options.compiler_only:
                inferred=c.get('expected') if c['family']!='needs_clarification' else None
                entry['semantic_result']='LLM_BYPASSED'
            else:
                print('[PULL] LLM '+c['id'],flush=True)
                def invoke(request: str) -> str:
                    return head(request) if head else real_head(options.ollama_url,options.model,request,options.llm_timeout)
                inferred=infer_with_bounded_structural_retry(c,invoke,entry)
                if inferred is None:
                    entry['semantic_result']='CORRECT_REFUSAL'
                else:
                    aliases=semantic_alpha_maps(inferred,pull.validate(c['expected']))
                    if aliases is None:
                        raise base.SmokeFailure('semantic_gold','Accepted graph has no valid gold mapping: '+c['id'])
                    terminal_aliases, action_aliases=aliases
                    distinct={k:v for k,v in terminal_aliases.items() if k!=v}
                    entry['semantic_result']=(
                        'TERMINAL_AND_ACTION_ALPHA_EQUIVALENT' if distinct and action_aliases else
                        'ACTION_NAME_ALPHA_EQUIVALENT_ABI_CHANGED' if action_aliases else
                        'TERMINAL_ALPHA_EQUIVALENT' if distinct else 'EXACT_GOLD_MATCH')
                    if distinct:
                        entry['terminal_state_alpha_map_gold_to_model']=distinct
                    if action_aliases:
                        entry['action_name_alpha_map_gold_to_model']=action_aliases
                        entry['abi_name_compatibility']='DIFFERS_FROM_GOLD_REQUIRES_INTERFACE_REVIEW'
            if inferred is None:
                entry['policy_action']='REFUSE_UNDERSPECIFIED'
                continue
            spec=pull.validate(inferred)
            graph_audit=graph_verifier.verify(spec)
            eval_live=graph_audit['liveness']
            entry['single_unavailable_role_liveness']={
                'status':eval_live['status'],
                'traps':eval_live['single_role_traps'],
                'checked_role_unavailability_scenarios':eval_live['checked_single_role_scenarios']}
            entry['generic_v2_graph_audit']={
                'status':graph_audit['status'], 'blockers':graph_audit['blockers'],
                'reachable_nodes':graph_audit['graph']['reachable_count'],
                'divergent_conflicts':len(graph_audit['conflicts']['divergent']),
                'conservation':graph_audit['conservation']['status'],
                'authorization':graph_audit['authorization']['status']}
            if c['family']=='unsafe_compilable':
                if eval_live['status']!='FAIL_TRAPPED_FUNDS_RISK':
                    raise base.SmokeFailure('liveness','Unsafe conditional contract not blocked: '+c['id'])
                entry['policy_action']='BLOCK_UNSAFE_CONTRACT'
            else:
                if eval_live['status']!='PASS_CONDITIONAL':
                    raise base.SmokeFailure('liveness','Liveness trap not repaired: '+c['id'])
                if not graph_audit['emission_authorized_by_graph_audit']:
                    raise base.SmokeFailure('graph_completeness',
                        'Generic graph audit refused emission for '+c['id']+': '+','.join(graph_audit['blockers']))
                entry['policy_action']='EMIT_CREDITED_SETTLEMENT'
                accepted[c['id']]=spec
        if len(accepted)!=4+sum(c['family']=='safe_positive' for c in extras):
            raise base.SmokeFailure('liveness','Expected four bundled safe results + external safe')
        if semantic_alpha_maps(accepted['safe_reversible_split'],accepted['safe_reversible_paraphrase']) is None:
            raise base.SmokeFailure('semantic_gold','Paraphrases yielded different meanings')
        result['stages']['head']='BYPASSED_COMPILER_ONLY' if options.compiler_only else 'PASS_LIVE' if head is None else 'PASS_INJECTED'
        result['stages']['semantic_gold']='NOT_TESTED' if options.compiler_only else 'PASS_BUNDLED_TYPED_SEMANTICS_AND_PUBLISHED_REFUSALS'
        result['stages']['terminal_state_alias_cases']=sum(bool(x.get('terminal_state_alpha_map_gold_to_model')) for x in result['cases'])
        result['stages']['action_name_alias_cases']=sum(bool(x.get('action_name_alpha_map_gold_to_model')) for x in result['cases'])
        result['stages']['liveness']='PASS_UNSAFE_BLOCKED_SINGLE_ACTOR'
        result['stages']['recipient_acceptance']='NOT_REQUIRED_FOR_SETTLEMENT_WITHDRAWAL_CAN_STILL_FAIL'
        count=0
        external_count=0
        for i,(ident,spec) in enumerate(accepted.items(),1):
            if i>99:raise base.SmokeFailure('solidity_emit','Too many contracts')
            name=f'NanoJevPull{i:02d}'
            solidity=pull.source(spec,name)
            gold=next(c['expected'] for c in original if c['id']==ident)
            oracle=gold_with_verified_alpha_aliases(gold,spec)
            foundry,n=pull.tests(oracle,name)
            recovery,nrecover=add_recovery_tests(oracle,name)
            extrasuite,ne=external_tests(oracle,name)
            foundry=insert_tests(foundry,recovery+extrasuite)
            count+=n+nrecover+ne
            external_count+=ne
            (workspace/'src'/f'{name}.sol').write_text(solidity,encoding='utf-8')
            (workspace/'test'/f'{name}.t.sol').write_text(foundry,encoding='utf-8')
            result['generated'][name]={'case':ident,'solidity_sha256':base.digest(solidity),
                'gold_derived_forge_sha256':base.digest(foundry),
                'tests':n+nrecover+ne,'external_published_oracle_tests':ne,
                'recovery_witnesses':nrecover}
        actual_count=sum(len(re.findall(r'\bfunction\s+test\w*\s*\(',p.read_text(encoding='utf-8')))
             for p in (workspace/'test').glob('*.t.sol'))
        if actual_count!=count or count<100 or external_count<3:
            raise base.SmokeFailure('solidity_emit',f'Unexpected test count: expected {count}, found {actual_count}; external {external_count}')
        result['expected_foundry_tests']=count
        result['stages']['external_published_behavior_tests']=f'GENERATED_{external_count}_ACROSS_{len(sources)}_SOURCED_REQUIREMENTS'
        result['stages']['solidity_emit']='PASS_NEW_V2_CREDIT_SETTLEMENT'
        result['stages']['independent_semantic_correctness']='NOT_ESTABLISHED_NO_INDEPENDENT_EXPERT_GOLD'
        for op in ('build','test'):
            print('[PULL] Docker Forge '+op,flush=True)
            proc=docker(base.docker_args(workspace,options.foundry_image,options.network,op),options.forge_timeout) if docker else base.run_command(base.docker_args(workspace,options.foundry_image,options.network,op),options.forge_timeout)
            result['stages']['forge_'+op]=proc
            if not isinstance(proc,dict) or type(proc.get('exit_code')) is not int:
                raise base.SmokeFailure('forge_'+op,'Malformed Docker outcome')
            if proc['exit_code']!=0:
                raise base.SmokeFailure('forge_'+op, 'Forge exited '+str(proc['exit_code']))
            if op=='test':
                summaries=re.findall(r'Ran\s+\d+\s+test suites?[^\n]*:\s*(\d+)\s+tests?\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped',proc.get('stdout_tail',''))
                if not summaries or tuple(map(int,summaries[-1]))!=(count,0,0):
                    raise base.SmokeFailure('forge_test','Forge final suite total did not match expected exact tests')
                result['stages']['forge_tests_passed']=count
        for name in result['generated']:
            artifact=workspace/'out'/f'{name}.sol'/f'{name}.json'
            if docker is None and not artifact.is_file():
                raise base.SmokeFailure('compiled_artifacts','Missing artifact '+name)
            if artifact.is_file():
                doc=json.loads(artifact.read_text(encoding='utf-8'))
                methods={x.get('name') for x in doc.get('abi',[]) if x.get('type')=='function'}
                required={'fund','state','fundedAt','pendingWithdrawals','totalPending','withdraw','withdrawTo'}
                if not required<=methods or not isinstance(doc.get('bytecode',{}).get('object'),str) or not doc['bytecode']['object']:
                    raise base.SmokeFailure('compiled_artifacts','Missing credit ABI/bytecode '+name)
                result['generated'][name]['artifact_sha256']=base.digest(artifact.read_bytes())
        result['stages']['compiled_artifacts']='PASS' if docker is None else 'INJECTED_NOT_VERIFIED'
        result['status']=('PASS_INJECTED_OFFLINE_ONLY' if head is not None or docker is not None else
                          'PASS_COMPILER_ONLY_NOT_LLM_GENERALIZATION' if options.compiler_only else
                          'PASS_SMOKE_NOT_CERTIFIED')
    except base.SmokeFailure as e:
        result['blockers'].append({'stage':e.stage,'reason':e.reason})
    except Exception as e:
        result['blockers'].append({'stage':'internal','reason':type(e).__name__+': '+str(e)[:500]})
        result['traceback_tail']=traceback.format_exc()[-2500:]
    finally:
        result['stages']['structural_retries']=sum(x.get('llm_attempts',0)==2 for x in result['cases'])
        result['finished_at']=base.now()
        if not options.dry_run and not output.exists():
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--liveness-report',type=Path,default=Options.__dataclass_fields__['liveness_report'].default)
    p.add_argument('--composition-report',type=Path,default=Options.__dataclass_fields__['composition_report'].default)
    p.add_argument('--expansion-report',type=Path,default=Options.__dataclass_fields__['expansion_report'].default)
    p.add_argument('--llm-model',default=base.DEFAULT_MODEL)
    p.add_argument('--ollama-url',default=base.DEFAULT_URL)
    p.add_argument('--foundry-image',default=base.DEFAULT_IMAGE)
    p.add_argument('--docker-network',choices=('bridge','none'),default='bridge')
    p.add_argument('--llm-timeout-s',type=float,default=240.0)
    p.add_argument('--forge-timeout-s',type=float,default=360.0)
    p.add_argument('--compiler-only',action='store_true')
    p.add_argument('--independent-cases',type=Path,default=None)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--audit-from-report',type=Path,default=None,
                   help='Replay a pinned prior LIVE special-case response through independent completeness checks; no LLM or Forge')
    p.add_argument('--special-case',choices=('milestone_escrow',),default=None,
                   help='Run a bounded Contract Language v2 expressiveness/refusal challenge; no Solidity or Forge')
    a=p.parse_args(argv)
    if a.audit_from_report is not None and not a.special_case:
        p.error('--audit-from-report requires --special-case milestone_escrow')
    if a.special_case:
        if a.compiler_only or a.independent_cases is not None or (a.dry_run and a.audit_from_report is not None):
            p.error('--special-case cannot be combined with --compiler-only or --independent-cases')
        from tools import nanojev_contract_milestone_escrow_case as milestone
        result=milestone.run(milestone.SpecialOptions(
            output=a.output, model=a.llm_model, ollama_url=a.ollama_url,
            llm_timeout=a.llm_timeout_s, dry_run=a.dry_run,
            audit_from_report=a.audit_from_report))
        sysaudit=result.get('independent_system_audit',{})
        print(base.canonical({'status':result['status'],'case_id':result['case_id'],
              'stages':result['stages'],'blockers':result['blockers'],
              'head_missing_topics':result.get('translation',{}).get('missing_topics'),
              'system_audit':{'head_independent':sysaudit.get('head_independent'),
                   'unsupported_capabilities':len(sysaudit.get('unsupported_capabilities',[])),
                   'unresolved_recovery_checkpoints':sysaudit.get('nonresponse_recovery',{}).get('checkpoint_count'),
                   'emission_authorized':sysaudit.get('emission_authorized')},
              'forge':result['forge'],'report':str(a.output)}))
        return 0 if result['status'].startswith('PASS_') or result['status']=='PLAN_ONLY_NOT_EXECUTED' else 1
    r=run(Options(output=a.output,liveness_report=a.liveness_report,
          composition_report=a.composition_report,expansion_report=a.expansion_report,
          model=a.llm_model,ollama_url=a.ollama_url,foundry_image=a.foundry_image,
          network=a.docker_network,llm_timeout=a.llm_timeout_s,forge_timeout=a.forge_timeout_s,
          compiler_only=a.compiler_only,dry_run=a.dry_run,independent_cases=a.independent_cases))
    summary={k:v.get('exit_code') if isinstance(v,dict) else v for k,v in r['stages'].items()}
    print(base.canonical({'status':r['status'],'blockers':r['blockers'],'stages':summary,
          'expected_foundry_tests':r.get('expected_foundry_tests'),'report':str(a.output)}))
    return 0 if r['status'].startswith('PASS_') or r['status']=='PLAN_ONLY_NOT_EXECUTED' else 1

if __name__=='__main__':
    raise SystemExit(main())
