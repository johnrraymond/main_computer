#!/usr/bin/env python3
"""GAMER V1 production Wide/to_numerical shadow against exact operand candidate.

Runs the *existing saved to_numerical/wide.pt* and the certified bounded
exact-operand candidate on the same raw prompt. If their frozen adapters differ,
each head is run on its own verified original adapter with separate passes.
The Wide output remains authoritative: no production weights,
training state, dispatch code, or configuration is changed. Supports the
certified two-surface signed-offset game prompts only; unsupported layouts
are rejected, never rounded/fallback-promoted. All reports are standalone.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import gc
import json
import math
from types import SimpleNamespace
from pathlib import Path

import numpy as np

from tools import nanojev_gamer_v1_handoff as gamer
from tools import nanojev_gamer_v1_s_payoff_operational_acceptance as s_lab
from tools import nanojev_gamer_v1_positional_digit_audit as positional
from tools import nanojev_gamer_v1_exact_belief_lab as exact
from tools import nanojev_gamer_v1_payoff_precision_lab as payoff
from tools import nanojev_gamer_v1_token_localization_lab as token
from tools import nanojev_gamer_v1_compositional_belief_lab as composition
from tools import nanojev_gamer_v1_lora_sign_stress as stress
from tools import nanojev_gamer_v1_c0_sign_head as c0sign

SCHEMA = 'nanojev.gamer.v1.production-wide-shadow-exact-candidate.v1'
PROMOTION = 'BLOCKED_SHADOW_ONLY'
FRESH_COUNT = 112
PROTECTED_COUNT = 48


def _source(closure):
    root = Path(closure).resolve()
    path = root / 'final_report.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    gate = report.get('gate', {})
    if (report.get('schema') != s_lab.SCHEMA or
        report.get('status') != 'COMPLETE_BOUNDED_S_REPAIR_AND_LIVE_ACCEPTANCE' or
        report.get('promotion') != 'BLOCKED_DIAGNOSTIC_ONLY' or
        any(gate.get(k) is not True for k in (
            'old_s_errors_closed', 'fresh_live_acceptance_passed',
            'bounded_candidate_passed')) or
        report.get('frozen_lora_step') != composition.STEP or
        any(report.get(k) is not True for k in (
            'no_lora_training','no_c0_training','no_payoff_head_training',
            'no_zero_detector_mutation'))):
        raise ValueError('GAMER_SHADOW_REQUIRES_COMPLETE_IMMUTABLE_S_ACCEPTANCE')
    source = s_lab._source(Path(report['source_shared_report']).parent)
    if token._sha(source['report_path']) != report['source_shared_sha256']:
        raise ValueError('GAMER_SHADOW_SHARED_REPORT_SHA_CHANGED')
    s_meta = report['s_diagnostic_checkpoint']
    s_path = Path(s_meta['path']).resolve()
    if (not s_path.is_file() or not s_path.is_relative_to(root) or
        token._sha(s_path) != s_meta['sha256'] or
        not s_meta.get('replay_params_identical') or
        s_meta.get('trained_on_s_heldout') is not False or
        s_meta.get('source_q_head_sha256') != source['head_sha256']):
        raise ValueError('GAMER_SHADOW_S_CHECKPOINT_NOT_ATTESTED')
    with np.load(s_path, allow_pickle=False) as chk:
        meta = json.loads(str(chk['metadata'].item()))
        centers = chk['centroids']
        weights = chk['weights']
        if (meta['schema'] != s_lab.SCHEMA or meta['component'] != 'S' or
            meta['source_q_head_sha256'] != source['head_sha256'] or
            meta.get('source_shared_sha256') != source['report_sha256'] or
            not np.array_equal(centers, source['digit_model']['centroids']) or
            not np.array_equal(weights, source['digit_model']['weights'])):
            raise ValueError('GAMER_SHADOW_S_DIGIT_REPLAY_CHANGED')
    s_lab._verify_immutable(source)
    if source['report']['frozen_lora_sha256'] != report['frozen_lora_sha256']:
        raise ValueError('GAMER_SHADOW_LORA_FINGERPRINT_CHANGED')
    return {'root':root,'report':report,'report_path':path,
            'report_sha256':token._sha(path),'s_path':s_path,
            's_sha256':token._sha(s_path),'source':source}


def _wide(wide_path, expected_model, expected_adapter_sha):
    import torch
    from tools.nanojev_qwen_c0_compressive_train import VARIANTS
    path=Path(wide_path).resolve()
    if not path.is_file():
        raise FileNotFoundError('GAMER_SHADOW_WIDE_PT_MISSING: supply --shadow-wide '
            'PATH_TO_HEAD_CYCLE/to_numerical/wide.pt (not latest.pt)')
    # weights_only ensures we do not execute checkpoint pickles.
    state=torch.load(path,map_location='cpu',weights_only=True)
    if (not isinstance(state,dict) or state.get('variant')!='wide' or
        state.get('hidden')!=list(VARIANTS['wide']) or
        not isinstance(state.get('input_width'),int) or state['input_width']<1 or
        state.get('backbone_model')!=expected_model or
        state.get('frozen_adapter_sha256')!=expected_adapter_sha or
        not isinstance(state.get('model'),dict) or not state['model'] or
        not all(isinstance(v,torch.Tensor) for v in state['model'].values()) or
        not isinstance(state.get('dataset_sha256'),str) or
        len(state['dataset_sha256'])!=64):
        raise ValueError('GAMER_SHADOW_WIDE_WRONG_VARIANT_OR_FROZEN_ADAPTER: '
            'Wide checkpoint metadata must match its selected original frozen adapter '
            'and backbone model; do not cross-wire adapter payloads')
    network = gamer.sha_bytes(path.read_bytes())
    return {'path':path,'sha256':network,'state':state,'adapter_sha256':expected_adapter_sha}


def _select_wide(run_root, requested=None):
    """Use the GAMER staircase's completed head-cycle convention, never a
    recursive first-match.  A requested checkpoint stays an exact path.
    """
    run = Path(run_root).resolve()
    if requested:
        path = Path(requested).resolve()
    else:
        direct = run / 'to_numerical' / 'wide.pt'
        cycles = sorted((run / 'staircase').glob('head_cycle_*'), reverse=True)
        # Prefer the latest completed head cycle with its local frozen adapter.
        paired = [cycle / 'to_numerical' / 'wide.pt' for cycle in cycles
                  if (cycle / 'to_numerical' / 'wide.pt').is_file() and
                     (cycle / 'lora' / 'best_adapter').is_dir()]
        path = paired[0] if paired else direct
    if not path.is_relative_to(run):
        raise ValueError('GAMER_SHADOW_WIDE_NOT_INSIDE_SELECTED_PRODUCTION_RUN')
    if not path.is_file():
        raise FileNotFoundError('GAMER_SHADOW_NO_COMPLETED_WIDE: expected '
            'RUN/staircase/head_cycle_NNNN/to_numerical/wide.pt paired with '
            'RUN/staircase/head_cycle_NNNN/lora/best_adapter')
    return path


def _resolve_adapter(wide_path, expected_candidate_sha, allow_separate):
    """Pair Wide with the *same head cycle's* immutable original LoRA.

    Never evaluate an old Wide checkpoint against an unrelated new adapter.
    """
    from tools.nanojev_two_game_spine_smoke import adapter_identity
    adapter = wide_path.parent.parent / 'lora' / 'best_adapter'
    if adapter.is_dir():
        identity = adapter_identity(adapter)
    else:
        identity = None
    if allow_separate:
        if identity is None:
            raise ValueError('GAMER_SHADOW_ORIGINAL_WIDE_ADAPTER_MISSING: '
                'supply a complete head_cycle containing lora/best_adapter')
        strategy = ('SAME_FROZEN_FORWARD_PASS' if identity == expected_candidate_sha
                    else 'SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES')
        return adapter, identity, strategy
    # Old behavior is still permitted when both lineages are identical.  In
    # all other cases refuse rather than accidentally using a mismatched Wide.
    if identity is not None and identity != expected_candidate_sha:
        raise ValueError('GAMER_SHADOW_DIFFERENT_FROZEN_ADAPTERS: '
            'use --shadow-separate-adapters to compare each original head '
            'on its own paired frozen adapter, with two distinct model passes')
    return ((adapter if identity is not None else None), expected_candidate_sha,
            'SAME_FROZEN_FORWARD_PASS')


def _wide_original_features(model_id, adapter, deck, device, batch_size, max_length):
    """Read the Wide baseline on its own frozen original LoRA (no relabeling)."""
    import torch
    from tools.nanojev_qwen_c0_compressive_train import capture_features
    if not adapter or not adapter.is_dir():
        raise ValueError('GAMER_SHADOW_ORIGINAL_ADAPTER_UNAVAILABLE')
    _, tok, model = gamer.model_components(model_id,device,adapter_dir=adapter)
    try:
        if any(len(tok.encode(c.prompt,add_special_tokens=True)) > max_length
               for c in deck):
            raise ValueError('GAMER_SHADOW_WIDE_PROMPT_TRUNCATION_FORBIDDEN')
        cfg=SimpleNamespace(device=device,capture_batch=batch_size,max_length=max_length)
        features=capture_features(torch,model,tok,
                                  [{'prompt':c.prompt} for c in deck],cfg)
        if features.shape[0] != len(deck) or not np.isfinite(features).all():
            raise ValueError('GAMER_SHADOW_ORIGINAL_WIDE_CAPTURE_INVALID')
        return features
    finally:
        del model,tok
        gc.collect()
        if torch.cuda.is_available(): torch.cuda.empty_cache()


def _protected_deck(source):
    old,tags,panels,_=positional._deck(source['source']['underlying']['composition'])
    result=[]; groups=[]
    for panel in ('heldout_zero_anchor','challenge_zero_anchor'):
        indices = panels[panel]
        if len(indices)!=24:raise ValueError('GAMER_SHADOW_PROTECTED_ZERO_PANEL_INCOMPLETE')
        for raw in indices:
            i=int(raw); c=old[i]
            result.append(c)
            groups.append({**tags[i],'panel':'zero_anchor','origin_panel':panel})
    if len(result)!=PROTECTED_COUNT:
        raise ValueError('GAMER_SHADOW_PROTECTED_GROUP_COUNT_MISMATCH')
    return result,groups


def _deck(source):
    fresh, fresh_tags=s_lab._operational_deck()
    protected,protected_tags=_protected_deck(source)
    all_cases=fresh+protected
    tags=fresh_tags+protected_tags
    if (len(fresh)!=FRESH_COUNT or len(all_cases)!=FRESH_COUNT+PROTECTED_COUNT or
        len({c.id for c in all_cases}) != len(all_cases)):
        raise ValueError('GAMER_SHADOW_DECK_DUPLICATE_OR_INCOMPLETE')
    return all_cases,tags


def _wide_predict(wide_checkpoint, features, device, eval_batch):
    from tools.nanojev_qwen_c0_compressive_train import VARIANTS,make_network,eval_heads
    import torch
    state=wide_checkpoint['state']
    x=np.asarray(features, dtype=np.float32)
    if (x.ndim!=2 or x.shape[1]!=state['input_width'] or
        not np.isfinite(x).all() or eval_batch<1):
        raise ValueError('GAMER_SHADOW_INVALID_PRODUCTION_WIDE_FEATURES')
    net=make_network(torch,state['input_width'],VARIANTS['wide']).to(device)
    net.load_state_dict(state['model'],strict=True)
    net.eval()
    try:
        predictions,_=eval_heads(torch,net,x,None,0.,1.,device,eval_batch)
        if predictions.shape!=(len(x),) or not np.isfinite(predictions).all():
            raise ValueError('GAMER_SHADOW_NONFINITE_WIDE_OUTPUT')
        return predictions
    finally:
        del net


def _score_protected(tags, truth_q, qhat, margins, correct_pay, rows):
    """Strict zero-neighborhood scoring for the 48 previously protected cases.

    Unlike the 112-case fresh operational deck, these panels have *no* S-only
    edits and use two different legitimate tag schemas: ``profile`` in the
    older heldout panel, ``group`` in the denominator challenge.  Do not route
    them through s_lab._score_live, whose S-only baseline builder requires
    fresh operational ``profile`` tags.
    """
    indices = list(range(FRESH_COUNT, FRESH_COUNT + PROTECTED_COUNT))
    if (len(tags) != FRESH_COUNT + PROTECTED_COUNT or
        len(rows) != len(tags) or len(truth_q) != len(tags) or
        len(qhat) != len(tags) or len(margins) != len(tags) or
        len(correct_pay) != len(tags)):
        raise ValueError('GAMER_SHADOW_PROTECTED_SCORING_LENGTH_CHANGED')
    partitions = {}
    for name, origin in (('heldout', 'heldout_zero_anchor'),
                         ('challenge', 'challenge_zero_anchor')):
        selected = [i for i in indices if tags[i].get('origin_panel') == origin]
        if len(selected) != 24 or any(tags[i].get('panel') != 'zero_anchor'
                                       for i in selected):
            raise ValueError('GAMER_SHADOW_PROTECTED_ZERO_PANEL_INCOMPLETE')
        partitions[name] = exact._triples(tags, selected, truth_q, qhat,
                                          margins, correct_pay, expected_groups=8)
    if any(tags[i].get('origin_panel') not in
           ('heldout_zero_anchor', 'challenge_zero_anchor') for i in indices):
        raise ValueError('GAMER_SHADOW_PROTECTED_ZERO_PANEL_UNKNOWN_ORIGIN')
    grouped = {
        'triples': sum(t['triples'] for t in partitions.values()),
        'exact_center_q': sum(t['exact_center_q'] for t in partitions.values()),
        'exact_q_all_three': sum(t['exact_q_all_three'] for t in partitions.values()),
        'all_payoffs_exact_all_three': sum(
            t['all_payoffs_exact_all_three'] for t in partitions.values()),
        'strict_exact_full_triples': sum(
            t['strict_exact_full_triples'] for t in partitions.values()),
        'accepted': all(t['accepted'] for t in partitions.values()),
    }
    scored = rows[FRESH_COUNT:]
    failures = [r for r in scored if not (
        r['q_exact'] and r['s_exact'] and r['payoffs_exact'] and r['candidate_exact'])]
    protected = {
        'cases': len(scored),
        'q_exact': sum(r['q_exact'] for r in scored),
        's_exact': sum(r['s_exact'] for r in scored),
        'all_payoffs_exact': sum(r['payoffs_exact'] for r in scored),
        'exact_margin_cases': sum(r['candidate_exact'] for r in scored),
        'strict_zero_triples': grouped,
        's_only_counterfactuals': {'cases': 0, 'exact': 0},
        'passed': not failures and grouped['accepted'],
        'failures': failures,
    }
    return protected, partitions


def _score(deck,tags,qhat,shat,payoffs,wide):
    n=len(deck)
    if not (n==FRESH_COUNT+PROTECTED_COUNT and
            len(tags)==len(qhat)==len(shat)==len(wide)==n and
            all(len(payoffs[k])==n for k in ('R','T','P'))):
        raise ValueError('GAMER_SHADOW_SCORING_LENGTH_CHANGED')
    results=[]; margins=[]; truth_q=[]; correct_pay=[]
    for i,case in enumerate(deck):
        expected=case.target
        operands={k:int(v) for k,v in zip(payoff.PAYOFFS,exact._parse_payoffs(case.prompt))}
        q_true=exact._exact_q([case])[0]
        truth_q.append(q_true)
        pred={'R':int(payoffs['R'][i]),'S':shat[i],
              'T':int(payoffs['T'][i]),'P':int(payoffs['P'][i])}
        operand_ok=all(pred[k]==operands[k] for k in payoff.PAYOFFS)
        correct_pay.append(operand_ok)
        q_ok=qhat[i]==q_true
        candidate=(qhat[i]*(pred['T']-pred['R'])+
                   (1-qhat[i])*(pred['P']-pred['S'])
                   if qhat[i] is not None and shat[i] is not None else None)
        margins.append(candidate)
        w=float(wide[i])
        wide_sign=(w>0)-(w<0)
        desired_sign=(expected>0)-(expected<0)
        row={'case_id':case.id,'panel':tags[i].get('origin_panel',tags[i]['panel']),
             'prompt_sha256':gamer.sha_bytes(case.prompt.encode('utf-8')),
             'q_exact':q_ok,'s_exact':shat[i]==operands['S'],
             'payoffs_exact':operand_ok,
             'candidate_exact':candidate==expected,
             'candidate_result':str(candidate) if candidate is not None else None,
             'oracle_result':str(expected),
             'production_wide_result':w,
             'production_wide_sign_correct':wide_sign==desired_sign,
             'production_wide_exact_1e6':math.isclose(w,float(expected),rel_tol=0,abs_tol=1e-6),
             'shadow_disagrees_with_wide':(candidate is None or
                not math.isclose(w,float(candidate),rel_tol=0,abs_tol=1e-6)),
             'returned_authority':'existing_to_numerical_wide'}
        results.append(row)
    fresh=s_lab._score_live(deck[:FRESH_COUNT],tags[:FRESH_COUNT],
        qhat[:FRESH_COUNT],shat[:FRESH_COUNT],
        {k:payoffs[k][:FRESH_COUNT] for k in ('R','T','P')})
    protected,extra=_score_protected(
        tags,truth_q,qhat,margins,correct_pay,results)
    # Validate both older 8+8 and fresh 16 complete zero-neighborhoods.
    zero_pass=(fresh['strict_zero_triples']['strict_exact_full_triples']==16 and
               protected['strict_zero_triples']['accepted'] and
               protected['strict_zero_triples']['strict_exact_full_triples']==16)
    candidate_ok=(fresh['passed'] and protected['passed'] and zero_pass and
                  all(x['candidate_exact'] and x['q_exact'] and x['payoffs_exact']
                      for x in results))
    shadow={'cases':n,'wide_exact_1e6':sum(x['production_wide_exact_1e6'] for x in results),
        'wide_sign_correct':sum(x['production_wide_sign_correct'] for x in results),
        'candidate_exact':sum(x['candidate_exact'] for x in results),
        'disagreements':sum(x['shadow_disagrees_with_wide'] for x in results),
        'returned_authority':'existing_to_numerical_wide',
        'candidate_was_never_returned':True}
    return {'candidate_passed':bool(candidate_ok),
        'fresh':fresh,'protected':protected,'protected_zero_triples':extra,
        'zero_triples':{'fresh':16,'heldout':8,'challenge':8,
                        'strict_full_triples':fresh['strict_zero_triples']['strict_exact_full_triples']+
                            sum(v['strict_exact_full_triples'] for v in extra.values()),
                        'accepted':bool(zero_pass)},
        'shadow':shadow,'case_records':results}


def _verify_unchanged(src,wide):
    s_lab._verify_immutable(src['source'])
    if (token._sha(src['report_path'])!=src['report_sha256'] or
        token._sha(src['s_path'])!=src['s_sha256'] or
        gamer.sha_bytes(wide['path'].read_bytes())!=wide['sha256']):
        raise ValueError('GAMER_SHADOW_PROTECTED_CHECKPOINT_MUTATED')


def run(args):
    if args.capture_batch<1 or args.eval_batch<1:
        raise ValueError('GAMER_SHADOW_BATCH_MUST_BE_POSITIVE')
    src=_source(args.shadow_closure)
    model_id,snapshot,cfg=s_lab._model_inputs(src['source'])
    payload=stress._verify_snapshot(snapshot,composition.STEP)
    if payload!=src['report']['frozen_lora_sha256']:
        raise ValueError('GAMER_SHADOW_FROZEN_PAYLOAD_MISMATCH')
    head=_select_wide(args.output, args.shadow_wide)
    adapter, wide_adapter_sha, strategy=_resolve_adapter(
        head,payload,getattr(args,'shadow_separate_adapters',False))
    wide=_wide(head,model_id,wide_adapter_sha)
    wide['original_adapter']=adapter
    wide['original_adapter_sha256']=wide_adapter_sha
    wide['forward_strategy']=strategy
    # Never overwrite, nest within, or alter a protected artifact directory.
    blocked=[src['root'],src['source']['root'],snapshot,wide['path'].parent,
             Path(args.output).resolve()]
    out=c0sign._output(args.shadow_report,blocked)
    if out.exists():raise FileExistsError('GAMER_SHADOW_REPORT_DESTINATION_EXISTS')
    _verify_unchanged(src,wide)
    if args.shadow_plan_only:
        plan={'schema':SCHEMA,'status':'READY_FOR_IMMUTABLE_PRODUCTION_WIDE_SHADOW',
            'wide_checkpoint':str(wide['path']),'wide_sha256':wide['sha256'],
            'frozen_adapter_sha256':payload,'model_id':model_id,
            'wide_adapter_sha256':wide_adapter_sha,
            'wide_adapter_dir':str(adapter) if adapter else None,
            'forward_pass_strategy':strategy,
            'expected_prompts':FRESH_COUNT+PROTECTED_COUNT,
            'promotion':PROMOTION,'report_destination':str(out)}
        print('GAMER SHADOW PREFLIGHT: '+json.dumps(plan,sort_keys=True),flush=True)
        return plan
    deck,tags=_deck(src)
    from tools.nanojev_gamer_v1_sign_information_probe import deck_hash
    deck_fingerprint=deck_hash(deck)
    print('GAMER to_numerical SHADOW: frozen Wide baseline + exact candidate; '
          'strategy='+strategy+'; Wide authority unchanged',flush=True)
    _,tok,model=gamer.model_components(model_id,args.device,adapter_dir=snapshot)
    try:
        captured=s_lab._live_capture(model,tok,deck,cfg,args.device,
                                   args.capture_batch,
                                   capture_wide=(strategy=='SAME_FROZEN_FORWARD_PASS'))
        if strategy=='SAME_FROZEN_FORWARD_PASS':
            groups,views,final=captured
        else:
            groups,views=captured
            final=None
        qs, ss=s_lab._predict_live_groups(groups,src['source']['digit_model'])
        pay=s_lab._new_checkpoint_predictions(src['source'],views)
    finally:
        del model,tok
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():torch.cuda.empty_cache()
        except ImportError:pass
    if strategy=='SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES':
        final=_wide_original_features(model_id,adapter,deck,args.device,
                                      args.capture_batch,cfg['max_length'])
    baseline=_wide_predict(wide,final,args.device,args.eval_batch)
    result=_score(deck,tags,qs,ss,pay,baseline)
    if adapter and strategy=='SEPARATE_FROZEN_ADAPTER_FORWARD_PASSES':
        from tools.nanojev_two_game_spine_smoke import adapter_identity
        if adapter_identity(adapter)!=wide_adapter_sha:
            raise ValueError('GAMER_SHADOW_ORIGINAL_ADAPTER_MUTATED_DURING_CAPTURE')
    _verify_unchanged(src,wide)
    candidate_pass=result['candidate_passed']
    out.mkdir(parents=True,exist_ok=False)
    detail=out/'shadow_cases.jsonl'
    with detail.open('w',encoding='utf-8') as stream:
        for row in result['case_records']:
            stream.write(gamer.canonical(row)+'\n')
    report={'schema':SCHEMA,
            'status':'SHADOW_CANDIDATE_PASS' if candidate_pass else 'SHADOW_CANDIDATE_FAIL',
            'promotion':PROMOTION,
            'production_to_numerical_integration':'WIDE_INFERENCE_SHADOW_COMPARE_ONLY',
            'production_output_authority':'existing_to_numerical_wide',
            'candidate_output_authority':False,
            'frozen_lora_sha256':payload,
            'frozen_lora_step':composition.STEP,
            'wide_adapter_sha256':wide_adapter_sha,
            'wide_adapter_dir':str(adapter) if adapter else None,
            'forward_pass_strategy':strategy,
            'wide_checkpoint':str(wide['path']),
            'wide_sha256':wide['sha256'],
            'wide_dataset_sha256':wide['state']['dataset_sha256'],
            's_closure_report':str(src['report_path']),
            's_closure_sha256':src['report_sha256'],
            's_checkpoint_sha256':src['s_sha256'],
            'deck_sha256':deck_fingerprint,
            'case_detail':{'path':str(detail),'sha256':token._sha(detail)},
            'candidate':{'fresh':result['fresh'],
                         'protected':result['protected'],
                         'protected_zero_triples':result['protected_zero_triples'],
                         'zero_triples':result['zero_triples'],
                         'passed':bool(candidate_pass)},
            'wide_shadow':result['shadow'],
            'gate':{'candidate_bounded_passed':bool(candidate_pass),
                    'same_forward_pass_captured':strategy=='SAME_FROZEN_FORWARD_PASS',
                    'each_head_on_its_original_frozen_adapter':True,
                    'original_wide_unchanged':True,
                    'complete_zero_line':result['zero_triples']['accepted'],
                    'shadow_output_kept_wide':True,
                    'promotion':PROMOTION},
            'limitations':('This is a bounded two-surface GAMER prompt shadow, '
                'not a deployed serving route. The production Wide head is '
                'computed and retained as authoritative for comparison. '
                'Separate-adapter comparisons use two frozen model forward '
                'passes on identical prompts, not a shared forward pass. '
                'No cutover is authorized; other game prompt families and '
                'unbounded numeral formats remain unsupported.')}
    gamer.atomic_json(out/'final_report.json',report)
    print('GAMER to_numerical SHADOW COMPLETE:',out/'final_report.json',flush=True)
    print('  candidate:', 'PASS' if candidate_pass else 'FAIL',
          ' strict zero triples:',result['zero_triples']['strict_full_triples'],'/32',
          ' wide discrepancies:',result['shadow']['disagreements'],
          ' production promotion: BLOCKED',flush=True)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path,help='Production Gamer run directory (read only)')
    p.add_argument('--shadow-closure',required=True,type=Path)
    p.add_argument('--shadow-wide',type=Path,help='Explicit Wide checkpoint; otherwise latest completed staircase head cycle')
    p.add_argument('--shadow-separate-adapters',action='store_true',help='Allow different original Wide and certified candidate frozen LoRA adapters, each evaluated separately on the same prompts')
    p.add_argument('--shadow-report',required=True,type=Path,help='New isolated shadow report directory')
    p.add_argument('--shadow-plan-only',action='store_true')
    p.add_argument('--device',default='cuda')
    p.add_argument('--capture-batch',type=int,default=2)
    p.add_argument('--eval-batch',type=int,default=64)
    result=run(p.parse_args(argv))
    return result

if __name__=='__main__':main()
