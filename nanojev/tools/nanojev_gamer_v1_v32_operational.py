#!/usr/bin/env python3
"""Certify, activate and rollback a BOUNDED GAMER V29/V30 numerical authority.

The old Wide head stays byte-for-byte unchanged. This can only become the
signed-difference authority for V27's two certified prompt families; the real
Space Captain v6 multi-action tactical voting adapter is NOT compatible and
remains separately authoritative. A Captain numerical request is a typed,
read-only calculation, never a tactical order.

Promotion verifies the complete V31 624/624 live deck against the original
fingerprinted corpus, runs new CUDA sign-triplet probes, activates a reversible
authority file, and tests the real TacticalAIService numerical entry point.
On any failed post-activation probe, roll back automatically.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

from main_computer import gamer_numerical_authority as auth

# A fresh three-sign probe, never fitted and never selected by a DEV result.
# The exact same certified payoff-difference prompt family is used, with
# reordered label positions and new signed operand contrasts.
GAME_TEMPLATE = ('Two actions: C (cooperate) or D (defect). Your payoffs: '
                 'DD=-5, CC=160, CD=-3, DC={dc}. The first letter is your '
                 'action. Compute T-R (defection gain when the other cooperates). '
                 'Answer with ONE number only.')
PROBE = (
    {'id':'captain.negative', 'prompt':GAME_TEMPLATE.format(dc=159), 'expected':'-1'},
    {'id':'captain.zero', 'prompt':GAME_TEMPLATE.format(dc=160), 'expected':'0'},
    {'id':'captain.positive', 'prompt':GAME_TEMPLATE.format(dc=161), 'expected':'1'},
)


def sign_triplet(config, output, *, device):
    from tools import nanojev_gamer_v1_handoff as gamer
    from tools import nanojev_gamer_v1_v30_live_inference as live
    from tools.nanojev_gamer_v1_precision_continuation_v19 import _sha
    path=Path(output).resolve()
    auth.require(not path.exists(), 'OUTPUT_ALREADY_EXISTS')
    path.mkdir(parents=True, exist_ok=False)
    deck=path/'captain_signed_difference_probe.jsonl'
    deck.write_text(''.join(json.dumps(r, sort_keys=True)+'\n' for r in PROBE),encoding='utf-8')
    for row in PROBE:
        live._validate_prompt(row['prompt'])
    raw=path/'fresh_model_report.json'
    opts=gamer.parser().parse_args(['v30-live','--output',config['paths']['run'],
          '--v27-run',config['paths']['v27_run'],
          '--v29-run',config['paths']['v29_run'],
          '--v30-run',config['paths']['v30_run'],
          '--v30-cases-jsonl',str(deck),'--v30-report',str(raw),'--device',device])
    gamer.validate_flags(opts)
    result=live.run(opts)
    actual=result.get('cases', [])
    auth.require(result.get('status')=='V31_READ_ONLY_LIVE_INFERENCE_COMPLETE' and
                 result.get('production_modified') is False and
                 result.get('wide_head_untouched') is True and
                 result.get('activation_source')==config['activation_source'] and
                 auth.canonical_source_files(result.get('sources'))==config['source_files'] and
                 result.get('frozen_adapter_sha256')==config['adapter_sha256'] and
                 result.get('selected_alpha')==config['selected_alpha'] and
                 result.get('total')==result.get('scored_cases')==3 and
                 len(actual)==3 and
                 result.get('exact_cases')==3 and
                 result.get('all_scored_exact') is True,
                 'FRESH_TRIPLET_NOT_CERTIFIED')
    for target, observed in zip(PROBE,actual):
        auth.require(observed.get('id')==target['id'] and
                     observed.get('expected')==target['expected'] and
                     observed.get('exact') is True and
                     Fraction(observed['difference']) == Fraction(target['expected']) and
                     observed.get('oracle_used_for_prediction') is False and
                     observed.get('calculation_authority')=='exact_fraction_postprocess',
                     'FRESH_TRIPLET_PREDICTION_INCORRECT')
    return {'cases':3, 'exact':3, 'raw_report':str(raw),
            'raw_sha256':_sha(raw), 'deck_sha256':_sha(deck)}


def promote(args):
    auth.require(args.output is not None and args.v31_run and args.v27_run and
                 args.v29_run and args.v30_run and args.input and args.run,
                 'PROMOTION_ARGUMENTS_REQUIRED')
    auth.require(auth.status(args.root)['authority']==auth.LEGACY,
                 'ALREADY_PROMOTED')
    config=auth.certify(args)
    probe=sign_triplet(config,args.output,device=args.device)
    config['preactivation_probe']=probe
    # Verify all pinned evidence again before writing active state.
    auth._verify_active(config)
    auth.activate(args.root,config)
    try:
        # Exercise Space Captain's actual controller API, not a direct
        # diagnostic-only function. This tests a numerical handoff only.
        from main_computer.tactical_ai_service import TacticalAIService
        question={'schema':auth.REQUEST_SCHEMA, 'requestId':'v32.captain-zero',
                  'prompt':PROBE[1]['prompt']}
        response=TacticalAIService(Path(args.root)).evaluate_gamer_signed_difference(
            question,device=args.device)
        auth.require(response['difference']=='0' and response['sign']==0 and
                     response['captainOrder'] is None and
                     response['authority']==auth.V30 and
                     response['modelReceipt']['oracleUsedForPrediction'] is False,
                     'CAPTAIN_NUMERICAL_HANDOFF_FAILED')
        summary={'status':'PASS_BOUNDED_GAMER_NUMERICAL_CUTOVER',
                 'scope':auth.SCOPE, 'authority':auth.V30,
                 'v31_live_regression':config['evidence']['v31_case_count'],
                 'fresh_sign_triplet':probe, 'captain_numerical_handoff':True,
                 'captain_tactical_v6_handoff':False,
                 'wide_head_untouched':True,
                 'state_file':str(auth.state_path(args.root)),
                 'rollback':'python -m tools.nanojev_gamer_v1_v32_operational rollback',
                 'limitations':('The Captain tactical-v6 adapter uses action-vote answers, '
                               'not signed payoff differences. This bounded numerical '
                               'authority does not change tactical choices or game release status.')}
        summary_path=Path(args.output)/'cutover_acceptance.json'
        auth.atomic_write(summary_path,summary)
        return summary
    except BaseException:
        auth.rollback(args.root)
        raise


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('status','certify','promote','infer','rollback'))
    p.add_argument('--root',default='.')
    p.add_argument('--run')
    p.add_argument('--input')
    p.add_argument('--v27-run')
    p.add_argument('--v29-run')
    p.add_argument('--v30-run')
    p.add_argument('--v31-run')
    p.add_argument('--output',help='NEW output folder for fresh pre-activation acceptance')
    p.add_argument('--device',default='cuda')
    p.add_argument('--prompt')
    p.add_argument('--request-id',default='gamer.numerical.001')
    return p


def run(args):
    if args.stage=='status':
        return auth.status(args.root)
    if args.stage=='rollback':
        return auth.rollback(args.root)
    if args.stage=='infer':
        auth.require(bool(args.prompt),'INFER_PROMPT_REQUIRED')
        return auth.infer(args.root,{'schema':auth.REQUEST_SCHEMA,
            'requestId':args.request_id,'prompt':args.prompt}, device=args.device)
    if args.stage=='promote':
        return promote(args)
    auth.require(all(getattr(args,k,None) for k in
        ('run','input','v27_run','v29_run','v30_run','v31_run')),
        'CERTIFICATION_ARGUMENTS_REQUIRED')
    return {'status':'PASS_V32_CERTIFICATION_PLAN_NO_CUTOVER',
            'active':False, 'candidate':auth.certify(args),
            'current':auth.status(args.root)}


def main(argv=None):
    result=run(parser().parse_args(argv))
    print('GAMER_V32_'+result.get('status',result.get('authority','UNKNOWN'))+' '+
          json.dumps(result,sort_keys=True,ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
