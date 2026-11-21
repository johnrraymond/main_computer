#!/usr/bin/env python3
"""Resume the original 8-shard V3 live benchmark without changing its baseline.

Runs only shards 1..7; never reruns, replaces, or fabricates shard 0.
Pins the corpus and model from the original observed 3/5 live result.
Requires the original benchmark and merge tools to already be installed.
No model settings, prompts, cases, gold, compiler, or scoring code are changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

CORPUS_SHA256 = '2650f67fe9a165a43d21198ee6e7941b62219d66fc915299859e40ce060b4d87'
MODEL = 'gemma4:26b-a4b-it-q4_K_M'
SHARD_COUNT = 8
REPORT_SCHEMA = 'nanojev.contract.v3.generalization_evaluation.v1'
DEFAULT_CORPUS = Path(__file__).resolve().parent / 'fixtures/nanojev_v3_generalization/manifest.json'


class ContinuationError(Exception):
    pass


def inspect_report(path: Path, index: int, *, original: bool = False) -> dict:
    if not path.is_file():
        raise ContinuationError(f'Missing shard {index}: {path}')
    if path.stat().st_size > 3_000_000:
        raise ContinuationError(f'Oversized shard report: {path}')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError, OSError) as exc:
        raise ContinuationError(f'Unreadable shard report: {path}') from exc
    if (data.get('schema') != REPORT_SCHEMA or data.get('mode') != 'live'
        or data.get('model') != MODEL or data.get('corpus_sha256') != CORPUS_SHA256):
        raise ContinuationError(f'Shard {index} is not the pinned live benchmark/corpus/model')
    shard = data.get('shard') or {}
    score = data.get('score') or {}
    if shard.get('index') != index or shard.get('count') != SHARD_COUNT:
        raise ContinuationError(f'Shard {index} has a mismatched index/count')
    if (data.get('no_assisted_retries') is not True or
        data.get('native_nanojev_model_used') is not False or
        data.get('independent_expert_gold') is not False or
        data.get('forge_requested') is not False or
        data.get('training_or_improvement_evidence') is not False):
        raise ContinuationError(f'Shard {index} provenance or execution flags differ')
    cases = data.get('cases')
    if (not isinstance(cases, list) or not cases or
        score.get('evaluated') != score.get('selected') or
        score.get('selected') != len(cases) or
        shard.get('selected') != len(cases) or
        any(r.get('correct') not in (True, False) or r.get('model_invoked') is not True for r in cases)):
        raise ContinuationError(f'Shard {index} is incomplete; preserve it for diagnosis')
    if original and (score.get('correct') != 3 or score.get('evaluated') != 5):
        raise ContinuationError('Original shard-0 score differs from the observed 3/5 baseline')
    return data


def main(argv: list[str] | None = None, *, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path,
                        default=Path('runtime/reports/nanojev-v3-generalization'))
    parser.add_argument('--timeout-seconds', type=float, default=600.0)
    parser.add_argument('--python', default=sys.executable,
                        help='Python interpreter containing the NanoJev V3 tools')
    args = parser.parse_args(argv)
    directory = args.report_dir.resolve()
    corpus = DEFAULT_CORPUS
    if args.timeout_seconds <= 0 or args.timeout_seconds > 1200:
        raise ContinuationError('Timeout must be in (0, 1200] seconds')
    if not corpus.is_file() or hashlib.sha256(corpus.read_bytes()).hexdigest() != CORPUS_SHA256:
        raise ContinuationError('Original 40-case corpus missing or altered; refusing to continue')
    inspect_report(directory / 'shard-0.json', 0, original=True)
    print('Verified original shard 0: 3/5, live Gemma, pinned corpus.', flush=True)
    # Preflight all existing reports and workspaces before starting any new inference.
    for index in range(1, SHARD_COUNT):
        path = directory / f'shard-{index}.json'
        workspace = directory / f'shard-{index}.cases'
        if path.exists():
            inspect_report(path, index)
        elif workspace.exists():
            raise ContinuationError(
                f'Shard {index} workspace exists without a complete report: {workspace}. '
                'Preserve it for diagnosis; do not overwrite or count it as scored.'
            )
    output = directory / 'full-40.json'
    if output.exists():
        raise ContinuationError(f'Final report already exists; refusing to overwrite: {output}')
    for index in range(1, SHARD_COUNT):
        path = directory / f'shard-{index}.json'
        if path.exists():
            print(f'Existing complete live shard {index} verified; preserving it.', flush=True)
            continue
        command = [args.python, '-m', 'tools.nanojev_contract_v3_generalization_eval',
                   '--mode', 'live', '--shard-index', str(index),
                   '--shard-count', str(SHARD_COUNT),
                   '--timeout-seconds', str(args.timeout_seconds),
                   '--model', MODEL, '--output', str(path)]
        print(f'Running live shard {index}/{SHARD_COUNT-1}', flush=True)
        proc = run(command)
        if proc.returncode != 0:
            raise ContinuationError(
                f'Shard {index} returned code {proc.returncode}. '
                f'Inspect {path} and its .cases workspace before resuming.')
        result = inspect_report(path, index)
        print(f'Shard {index}: {result["score"]["correct"]}/{result["score"]["evaluated"]} correct', flush=True)
    command = [args.python, '-m', 'tools.nanojev_contract_v3_generalization_merge',
               '--report-dir', str(directory), '--output', str(output)]
    print('Merging eight measured live shards...', flush=True)
    proc = run(command)
    if proc.returncode != 0:
        raise ContinuationError(f'Merge failed (exit {proc.returncode}). Inspect {output} if present.')
    final = json.loads(output.read_text(encoding='utf-8'))
    score = final.get('score') or {}
    if (final.get('status') != 'MEASURED_LIVE_FULL_CORPUS_NOT_CERTIFIED' or
        final.get('corpus_sha256') != CORPUS_SHA256 or
        final.get('mode') != 'live' or
        score.get('selected') != 40 or score.get('evaluated') != 40):
        raise ContinuationError('Merged report did not establish 40 measured live cases')
    print(json.dumps({'status': final['status'], 'correct': score['correct'],
                      'evaluated': 40, 'accuracy': score['accuracy'],
                      'correct_positive': score['correct_positive'],
                      'correct_refusals': score['correct_refusals'],
                      'incorrect_contract_ir': score['incorrect_contract_ir'],
                      'wrong_reason_refusals': score['wrong_reason_refusals'],
                      'report': str(output)}, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ContinuationError, OSError, ValueError) as exc:
        print(json.dumps({'status': 'STOPPED_WITHOUT_CHANGING_BASELINE', 'reason': str(exc)}),
              file=sys.stderr, flush=True)
        raise SystemExit(2)
