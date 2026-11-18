#!/usr/bin/env python3
"""Read-only NanoJev *research-candidate* JSONL inference interface.

This is an integration adapter around the frozen, previously CUDA-tested
semantic extractor. It is NOT a production entrypoint and must never be
represented as a whole-Main-Computer acceptance test.

Wire protocol: one JSON request per stdin line, one JSON response per stdout
line. All model status/progress goes to stderr; no modification of Qwen weights,
production data, or contract seals. Empty/malformed input produces a typed error.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from tools import nanojev_sealed_overlap_external_generalization_bakeoff as ext
from tools import nanojev_sealed_overlap_information_smoke as smoke
from tools import nanojev_sealed_overlap_polarity_layers_frob as layers
from tools import nanojev_sealed_overlap_relation_binding_bakeoff as binding
from tools import nanojev_sealed_overlap_language_stress_bakeoff as stress
from tools import nanojev_sealed_overlap_semantic_interface_bakeoff as interface
from tools import nanojev_sealed_overlap_semantic_interface_repair_bakeoff as repair
from tools import nanojev_sealed_overlap_lexicon_blind_bakeoff as blind
from tools import nanojev_sealed_overlap_scope_obligation_bakeoff as scope
from tools import nanojev_sealed_overlap_negative_consistency_bakeoff as neg

SCHEMA = 'nanojev.system-contract-jsonl.v1'
MAX_TEXT_CHARS = 12000
MAX_REQUEST_BYTES = 65536


def frozen_runtime(device: str, prior_report: Path, external_report: Path, *,
                   allow_download: bool = False, encoder=None):
    """Rebuild in-memory heads using only existing train fixtures and pinned code.

    The frozen candidate has no persisted trained head. Training the 648-example
    ridge decoder is necessary for an actual run; no Qwen weights are touched.
    """
    pins = ext.fingerprint_preflight()
    if not external_report.is_file() or not prior_report.is_file():
        raise ValueError('PRIOR_CUDA_REPORTS_MISSING')
    ext_report = json.loads(external_report.read_text(encoding='utf8'))
    if (ext_report.get('schema') != ext.SCHEMA or ext_report.get('status') != ext.STATUS
        or ext_report.get('source_pins', {}).get('module_sha256') != pins
        or ext_report.get('source_pins', {}).get('fixture_sha256') != ext.FROZEN_HOLDOUT_SHA256
        or ext_report.get('source_pins', {}).get('prior_report_sha256') != sha256(prior_report.read_bytes()).hexdigest()
        or ext_report.get('historical_canary', {}).get('matches_prior_216_of_216') is not True
        or ext_report.get('outcome', {}).get('fresh_valid_generalization_certified') is not False):
        raise ValueError('EXTERNAL_REPORT_UNVERIFIED_OR_CHANGED')
    paths = {'base_fixture_sha256': binding.FIXTURE, 'stress_fixture_sha256': stress.FIXTURE,
             'interface_fixture_sha256': interface.FIXTURE, 'repair_fixture_sha256': repair.FIXTURE,
             'blind_fixture_sha256': blind.FIXTURE, 'scope_fixture_sha256': scope.FIXTURE,
             'new_fixture_sha256': neg.FIXTURE}
    ext.validate_prior(prior_report, paths)
    base = binding.load_fixture(binding.FIXTURE)
    st = stress.load_fixture(stress.FIXTURE, base)
    iface = interface.load_fixture(interface.FIXTURE, base, st)
    rf = repair.load_fixture(repair.FIXTURE, base, iface, st)
    data = stress.derive_data(base, st, seed=261117, train_atoms=216,
                              selection_groups=16, final_groups=24)
    if encoder is None:
        encoder = layers.FrozenLayerEncoder(smoke.MODEL_ID, device,
                    allow_download=allow_download, batch_size=4, max_length=192)
    positions = getattr(encoder, 'positions', None)
    if positions and (positions.get('half') != 14 or positions.get('final') != 28):
        raise ValueError('FROZEN_QWEN_LAYER_DRIFT')
    model = blind.train_frozen(encoder, base, st, iface, rf, data, ridge=.1)
    return {'encoder': encoder, 'model': model, 'base': base, 'rf': rf,
            'fingerprints': pins, 'prior_sha256': sha256(prior_report.read_bytes()).hexdigest(),
            'external_sha256': sha256(external_report.read_bytes()).hexdigest()}


def process(request: Any, runtime: dict, *, prediction: list[tuple] | None = None) -> dict:
    """Process a single input without access to expected labels or test policy."""
    req_id = request.get('request_id') if isinstance(request, dict) else None
    out = {'schema': SCHEMA, 'request_id': req_id, 'decision': 'error',
           'relations': [], 'contradiction': None, 'reasons': []}
    if not isinstance(request, dict) or not isinstance(req_id, str) or not (1 <= len(req_id) <= 128):
        out['reasons'] = ['INVALID_REQUEST_ID']
        return out
    if request.get('operation') != 'extract_contract' or set(request) != {'request_id', 'operation', 'text'}:
        out['reasons'] = ['INVALID_REQUEST_SHAPE']
        return out
    value = request.get('text')
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT_CHARS or '\x00' in value:
        out['reasons'] = ['INVALID_TEXT']
        return out
    text = value.strip()
    out['source_sha256'] = sha256(text.encode('utf8')).hexdigest()
    # Candidate cannot process arbitrary multiline documents without losing
    # clause segmentation, so reject rather than reinterpret them silently.
    if '\n' in text or '\r' in text:
        out.update(decision='abstain', reasons=['UNSUPPORTED_DOCUMENT_SEGMENTATION'])
        return out
    try:
        with redirect_stdout(sys.stderr):
            model = runtime['model']
            base = runtime['base']
            rf = runtime['rf']
            predicted = prediction if prediction is not None else neg.predict_baseline(
                [{'text': text}], model, runtime['encoder'], base, rf)[0]
            accepted, corrected, reasons, _ = neg.grounded_document(
                text, predicted, base, rf['reference_markers'], repair_mode=True)
        if not accepted:
            out.update(decision='abstain', reasons=list(reasons) or ['UNSUPPORTED_GRAMMAR'])
            return out
        clauses = binding.split_document(text)
        if len(clauses) != len(corrected):
            out.update(decision='error', reasons=['INTERNAL_CLAUSE_MISMATCH'])
            return out
        out.update(decision='accepted',
                   relations=[dict(zip(binding.FIELDS, atom)) for atom in corrected],
                   contradiction=bool(repair.contradiction(corrected, base)),
                   reasons=[])
        return out
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        # The candidate's parser has restricted coverage; a parse mismatch
        # must not cause an authoritative relation to be emitted.
        out.update(decision='abstain', reasons=['UNSUPPORTED_CANDIDATE_INPUT'],
                   diagnostic_type=type(exc).__name__)
        return out



def process_batch(requests: list[Any], runtime: dict) -> list[dict]:
    """Optimize inference only: batch decisions and semantics equal process().

    A malformed request is excluded *before* model invocation. If a batch
    cannot be processed, retry each request through the ordinary fail-closed
    path; failure never becomes an accepted semantic graph.
    """
    responses: list[dict | None] = [None] * len(requests)
    eligible = []
    for i, req in enumerate(requests):
        if (not isinstance(req, dict)
            or not isinstance(req.get('request_id'), str)
            or not (1 <= len(req['request_id']) <= 128)
            or set(req) != {'request_id', 'operation', 'text'}
            or req.get('operation') != 'extract_contract'
            or not isinstance(req.get('text'), str) or not req['text'].strip()
            or len(req['text']) > MAX_TEXT_CHARS or '\x00' in req['text']
            or '\n' in req['text'] or '\r' in req['text']):
            responses[i] = process(req, runtime)
            continue
        try:
            binding.split_document(req['text'].strip())
        except (ValueError, TypeError):
            responses[i] = process(req, runtime)
            continue
        eligible.append(i)
    if eligible:
        try:
            with redirect_stdout(sys.stderr):
                predictions = neg.predict_baseline(
                    [{'text': requests[i]['text'].strip()} for i in eligible],
                    runtime['model'], runtime['encoder'], runtime['base'], runtime['rf'])
            if len(predictions) != len(eligible):
                raise ValueError('BATCH_PREDICTION_COUNT_MISMATCH')
            for i, predicted in zip(eligible, predictions):
                responses[i] = process(requests[i], runtime, prediction=predicted)
        except (ValueError, TypeError, KeyError, IndexError):
            for i in eligible:
                responses[i] = process(requests[i], runtime)
    return [x for x in responses if x is not None]


def serve_batch(runtime: dict, inp, stdout, *, chunk_size: int = 128):
    if not 1 <= chunk_size <= 1024:
        raise ValueError('INVALID_BATCH_SIZE')
    raw = inp.readlines()
    for offset in range(0, len(raw), chunk_size):
        chunk = raw[offset:offset + chunk_size]
        decoded = []
        too_large = set()
        for i, line in enumerate(chunk):
            if len(line.encode('utf8')) > MAX_REQUEST_BYTES:
                too_large.add(i)
                decoded.append(None)
            else:
                try:
                    decoded.append(json.loads(line))
                except (ValueError, UnicodeError):
                    decoded.append(None)
        responses = process_batch(decoded, runtime)
        if len(responses) != len(chunk):
            raise RuntimeError('BATCH_RESPONSES_NOT_ONE_FOR_ONE')
        for i, result in enumerate(responses):
            if i in too_large:
                result = {'schema': SCHEMA, 'request_id': None, 'decision': 'error',
                          'relations': [], 'contradiction': None, 'reasons': ['REQUEST_TOO_LARGE']}
            stdout.write(json.dumps(result, sort_keys=True, allow_nan=False) + '\n')
        stdout.flush()


def serve(runtime: dict, inp, stdout):
    for raw in inp:
        if len(raw.encode('utf8')) > MAX_REQUEST_BYTES:
            response = {'schema': SCHEMA, 'request_id': None, 'decision': 'error',
                        'relations': [], 'contradiction': None, 'reasons': ['REQUEST_TOO_LARGE']}
        else:
            try:
                req = json.loads(raw)
            except (ValueError, UnicodeError):
                req = None
            response = process(req, runtime)
        stdout.write(json.dumps(response, sort_keys=True, allow_nan=False) + '\n')
        stdout.flush()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='cuda')
    p.add_argument('--prior-report', type=Path, default=Path('runtime/reports/sealed-overlap-negative-consistency-0001.json'))
    p.add_argument('--external-report', type=Path, default=Path('runtime/reports/sealed-overlap-external-generalization-0001.json'))
    p.add_argument('--allow-model-download', action='store_true')
    p.add_argument('--batch-input', action='store_true',
                   help='Finite stdin stream only; batch Qwen encodes without changing decisions')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    with redirect_stdout(sys.stderr):
        runtime = frozen_runtime(args.device, args.prior_report, args.external_report,
                                 allow_download=args.allow_model_download)
    if args.batch_input:
        serve_batch(runtime, sys.stdin, sys.stdout)
    else:
        serve(runtime, sys.stdin, sys.stdout)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
