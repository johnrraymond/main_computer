"""Bounded GAMER numerical authority, independent of Space Captain action voting.

V29/V30 is certified ONLY for V27-style signed-difference prompts. The legacy
Wide head is not modified, re-trained, or replaced for other numerical games.
The configuration is explicit, locally versioned, fingerprint-bound and
reversible; unsupported prompts fail closed rather than entering an action
selection channel or silently falling back to an oracle.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

SCHEMA = 'nanojev.gamer.v1.bounded-numerical-authority.v32'
REQUEST_SCHEMA = 'game.captainSignedDifferenceRequest.v1'
RESPONSE_SCHEMA = 'game.captainSignedDifferenceResponse.v1'
LEGACY = 'legacy-wide'
V30 = 'v29-v30-signed-difference'
STATE_RELATIVE = Path('runtime/state/gamer/v32_numerical_authority.json')
SCOPE = 'v27-certified-signed-difference-families-only'


def require(ok, reason):
    if not ok:
        raise ValueError('GAMER_V32_' + reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def canonical_source_files(sources):
    """Compare source identities, never lexical relative-vs-absolute spellings.

    V31 accepts repository-relative source paths. V32's activation paths are
    absolute. Both must designate the SAME files with the SAME fingerprints.
    Canonicalizing names is not an exception to the hash/identity checks.
    """
    require(isinstance(sources, dict) and bool(sources), 'SOURCE_FILES_MISSING')
    normalized = {}
    for name, digest in sources.items():
        require(isinstance(name, str) and bool(name) and
                isinstance(digest, str) and len(digest) == 64 and
                all(c in '0123456789abcdef' for c in digest),
                'SOURCE_FILE_RECORD_INVALID')
        key = str(Path(name).resolve())
        require(key not in normalized, 'SOURCE_FILE_PATH_ALIAS_COLLISION')
        normalized[key] = digest
    return normalized


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                     prefix='.v32-', delete=False) as f:
        tmp = Path(f.name)
        json.dump(payload, f, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def state_path(root):
    return Path(root).resolve() / STATE_RELATIVE


def status(root):
    path = state_path(root)
    if not path.exists():
        return {'schema': SCHEMA, 'authority': LEGACY, 'scope': SCOPE,
                'reason': 'NO_V32_CUTOVER_ACTIVE'}
    result = read(path)
    require(result.get('schema') == SCHEMA and
            result.get('authority') in (LEGACY, V30) and
            result.get('scope') == SCOPE, 'INVALID_AUTHORITY_STATE')
    return result


def _exact_deck_verified(v31_run, input_dir, v27_run, v29_run, v30_run):
    """Replay V31 corpus certification and score untouched recorded live outputs."""
    from tools import nanojev_gamer_v1_v31_live_regression as battery
    root = Path(v31_run)
    a_path = root / 'acceptance_report.json'
    b_path = root / 'live_inference_report.json'
    deck_path = root / 'full_live_regression_cases.jsonl'
    require(all(p.is_file() for p in (a_path,b_path,deck_path)), 'V31_EVIDENCE_MISSING')
    a, b = read(a_path), read(b_path)
    require(a.get('schema') == battery.SCHEMA and
            a.get('status') == 'PASS_LIVE_NUMERICAL_REGRESSION_NO_PROMOTION' and
            a.get('all_gates_met') is True and
            a.get('exact') == a.get('total') == 624 and
            a.get('promotion_authorized') is False and
            a.get('production_modified') is False and
            a.get('live_report_sha256') == sha(b_path),
            'V31_ACCEPTANCE_MISSING_OR_CHANGED')
    original_deck, provenance = battery.regenerate(input_dir,v27_run,v29_run,v30_run)
    recorded = [json.loads(x) for x in deck_path.read_text(encoding='utf-8').splitlines()
                if x.strip()]
    require(recorded == original_deck, 'V31_DECK_MUTATED')
    require(a.get('provenance') == provenance, 'V31_PROVENANCE_CHANGED')
    recomputed = battery.summarize(original_deck,b,provenance)
    require(recomputed['all_gates_met'] and recomputed['exact'] == 624 and
            a.get('acceptance_gates') == recomputed['acceptance_gates'] and
            a.get('by_scope_kind') == recomputed['by_scope_kind'] and
            a.get('by_sign') == recomputed['by_sign'] and
            a.get('by_surface') == recomputed['by_surface'] and
            not a.get('failed_cases'), 'V31_REPLAY_NOT_EXACT')
    require(set(b.get('sources', {})) and
            b.get('frozen_adapter_sha256') and
            b.get('selected_alpha') == 0.25,
            'V31_LINEAGE_OR_ALPHA_INCOMPLETE')
    return {'v31_report_sha256':sha(a_path), 'v31_live_sha256':sha(b_path),
            'v31_deck_sha256':sha(deck_path), 'v31_case_count':624,
            'v31_sign_counts':recomputed['by_sign'], 'provenance':provenance}


def certify(args):
    from tools import nanojev_gamer_v1_v30_live_inference as live
    evidence = _exact_deck_verified(args.v31_run,args.input,args.v27_run,
                                    args.v29_run,args.v30_run)
    # Read V29 and V30 artifacts and verify checkpoint + frozen model ancestry.
    artifacts = live.load(SimpleNamespace(v27_run=args.v27_run,
                            v29_run=args.v29_run,v30_run=args.v30_run))
    # Live report must be the one computed from precisely this signed model.
    recorded = read(Path(args.v31_run)/'live_inference_report.json')
    recorded_sources = canonical_source_files(recorded.get('sources'))
    verified_sources = canonical_source_files(artifacts['source_files'])
    require(recorded_sources == verified_sources and
            recorded.get('frozen_adapter_sha256') ==
                artifacts['lineage']['adapter_sha256'] and
            recorded.get('selected_alpha') == artifacts['alpha'],
            'V31_FROZEN_INFERENCE_SOURCE_CHANGED')
    args_dict = {k:str(Path(getattr(args,k)).resolve()) for k in
                 ('run','input','v27_run','v29_run','v30_run','v31_run')}
    require(Path(args_dict['run']).exists(), 'RUN_MISSING')
    return {'schema':SCHEMA, 'authority':V30, 'scope':SCOPE,
            'paths':args_dict, 'evidence':evidence,
            'source_files':verified_sources,
            'adapter_sha256':artifacts['lineage']['adapter_sha256'],
            'selected_alpha':artifacts['alpha'],
            'activation_source':'frozen_base_qwen_layer18_no_lora_adapter',
            'calculation_authority':'exact_fraction_postprocess',
            'legacy_wide_unchanged':True}


def _verify_active(state):
    """Every request attests promotion evidence, not merely its cached status."""
    require(state['authority']==V30, 'LEGACY_AUTHORITY_RETAINED')
    files=canonical_source_files(state.get('source_files'))
    require(files == state['source_files'] and
            all(Path(k).is_file() and sha(k)==v for k,v in files.items()),
            'ACTIVE_SOURCE_CHANGED')
    ev=state.get('evidence') or {}
    r=Path(state['paths']['v31_run'])
    require(all((r/name).is_file() and sha(r/name)==ev[key] for name,key in (
        ('acceptance_report.json','v31_report_sha256'),
        ('live_inference_report.json','v31_live_sha256'),
        ('full_live_regression_cases.jsonl','v31_deck_sha256'))),
        'ACTIVE_CERTIFICATION_MUTATED')
    probe = state.get('preactivation_probe') or {}
    probe_file = Path(probe.get('raw_report', ''))
    require(probe.get('cases') == probe.get('exact') == 3 and
            probe_file.is_file() and sha(probe_file) == probe.get('raw_sha256'),
            'ACTIVE_PREACTIVATION_PROBE_MISSING_OR_CHANGED')
    probe_data = read(probe_file)
    cases = probe_data.get('cases', [])
    require(probe_data.get('status') == 'V31_READ_ONLY_LIVE_INFERENCE_COMPLETE' and
            probe_data.get('total') == 3 and len(cases) == 3 and
            probe_data.get('activation_source') == state.get('activation_source') and
            canonical_source_files(probe_data.get('sources')) == files and
            probe_data.get('frozen_adapter_sha256') == state.get('adapter_sha256') and
            probe_data.get('selected_alpha') == state.get('selected_alpha') and
            probe_data.get('production_modified') is False and
            probe_data.get('wide_head_untouched') is True and
            all(x.get('oracle_used_for_prediction') is False and
                x.get('exact') is True and
                Fraction(x['difference']) == Fraction(x['expected']) for x in cases),
            'ACTIVE_PREACTIVATION_PROBE_INVALID')


def validate_captain_request(request):
    """Do not accept tactical votes or operator-supplied prediction targets."""
    from tools import nanojev_gamer_v1_v30_live_inference as live
    require(type(request) is dict and request.get('schema')==REQUEST_SCHEMA,
            'CAPTAIN_REQUEST_SCHEMA_NOT_SUPPORTED')
    require(set(request)=={'schema','requestId','prompt'}, 'CAPTAIN_REQUEST_UNKNOWN_FIELDS')
    ident=request['requestId']
    require(isinstance(ident,str) and 1<=len(ident)<=120,'CAPTAIN_REQUEST_ID_INVALID')
    live._validate_prompt(request['prompt'])
    return ident


def infer(root,request,*,device='cuda'):
    """Real forward through V29/V30. No expected answer is forwarded or read."""
    from tools import nanojev_gamer_v1_v30_live_inference as live
    from tools import nanojev_gamer_v1_handoff as gamer
    ident=validate_captain_request(request)
    state=status(root)
    _verify_active(state)
    parts=state['paths']
    # Build args via the existing GAMER CLI contract to reuse its validation.
    name=hashlib.sha256((ident+'\0'+request['prompt']).encode('utf-8')).hexdigest()[:24]
    receipt=Path(root).resolve()/'runtime/reports/gamer-v1/v32-authority'/f'{name}.json'
    require(not receipt.exists(),'CAPTAIN_RECEIPT_EXISTS_REFUSE_OVERWRITE')
    argv=['v30-live','--output',parts['run'],'--v27-run',parts['v27_run'],
          '--v29-run',parts['v29_run'],'--v30-run',parts['v30_run'],
          '--v30-prompt',request['prompt'],'--v30-report',str(receipt),
          '--device',device]
    opts=gamer.parser().parse_args(argv)
    gamer.validate_flags(opts)
    result=live.run(opts)
    require(result.get('status')=='V31_READ_ONLY_LIVE_INFERENCE_COMPLETE' and
            result.get('total')==1 and result.get('scored_cases')==0 and
            result.get('activation_source')==state['activation_source'] and
            canonical_source_files(result.get('sources'))==state['source_files'] and
            result.get('frozen_adapter_sha256')==state['adapter_sha256'] and
            result.get('selected_alpha')==state['selected_alpha'] and
            result.get('production_modified') is False and
            result.get('wide_head_untouched') is True,
            'CAPTAIN_LIVE_SOURCE_OR_REPORT_INVALID')
    prediction=result['cases'][0]
    delta=Fraction(prediction['difference'])
    first=Fraction(prediction['first_operand'])
    second=Fraction(prediction['second_operand'])
    require(delta==first-second and
            prediction.get('sign')==int(delta>0)-int(delta<0) and
            prediction.get('zero_exact') is (delta==0) and
            prediction.get('oracle_used_for_prediction') is False and
            prediction.get('calculation_authority')=='exact_fraction_postprocess' and
            prediction.get('operand_authority')=='frozen_v29_selector_and_v30_layer18_decoder' and
            str(delta)==prediction['difference'],
            'CAPTAIN_PREDICTION_INVALID')
    require(all(sha(k)==v for k,v in state['source_files'].items()),
            'CAPTAIN_SOURCE_CHANGED_DURING_FORWARD')
    return {'schema':RESPONSE_SCHEMA,'requestId':ident,
            'difference':prediction['difference'],'sign':prediction['sign'],
            'firstOperand':prediction['first_operand'],
            'secondOperand':prediction['second_operand'],
            'authority':V30,'scope':SCOPE,
            'modelReceipt':{'rawReport':str(receipt), 'rawSha256':sha(receipt),
                           'acceptedRegressionSha256':state['evidence']['v31_report_sha256'],
                           'activationSource':state['activation_source'],
                           'oracleUsedForPrediction':False},
            'captainOrder':None}


def activate(root, config):
    path=state_path(root)
    previous=status(root)
    require(previous['authority']==LEGACY,'ALREADY_ACTIVE_REQUIRE_EXPLICIT_ROLLBACK')
    _verify_active(config)
    atomic_write(path,config)
    return status(root)


def rollback(root):
    path=state_path(root)
    previous=status(root)
    if previous['authority']==LEGACY:
        return previous
    # Never touches the original Wide implementation or its checkpoint.
    result={'schema':SCHEMA,'authority':LEGACY,'scope':SCOPE,
            'reason':'EXPLICIT_ROLLBACK_TO_UNMODIFIED_WIDE_AUTHORITY',
            'previous_v31_report_sha256':previous['evidence']['v31_report_sha256'],
            'legacy_wide_unchanged':True}
    atomic_write(path,result)
    return result
