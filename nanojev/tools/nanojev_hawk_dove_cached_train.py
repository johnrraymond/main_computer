"""Exact, disk-backed frozen-Qwen capture cache for scientific Hawk-Dove runs.

No decoder, optimizer, sampling, eval, tier gates, score, or checkpoint schema
changes. Wraps *only* ex.forward's immutable Qwen capture; computes everything
else with the same operations as the original forward. Full float32 features are
stored losslessly. The underlying scientific trainer source SHA is unchanged.

IMPORTANT: Run all modes through this entrypoint for the same cache identity;
checkpoint remains usable by the original trainer with the original semantics.
"""
from __future__ import annotations

from collections import OrderedDict
from functools import lru_cache
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_game_transform_experiment as ex
from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_game_math_curriculum_v10_runtime as rt

SCHEMA = 'nanojev.hawk-dove.frozen-qwen-capture-cache.v1'
CAPTURE_SCHEMA = 'nanojev.frozen-capture.v1'


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for buf in iter(lambda: stream.read(1 << 20), b''):
            h.update(buf)
    return h.hexdigest()


class FrozenCaptureCache:
    def __init__(self, folder: Path, identity: dict, memory_budget_mb: int = 1024):
        if memory_budget_mb < 0:
            raise ValueError('CACHE_BUDGET_NEGATIVE')
        self.folder = Path(folder)
        self.identity = identity
        self.identity_digest = hashlib.sha256(canonical(identity).encode()).hexdigest()
        self.max_bytes = memory_budget_mb * (1 << 20)
        self.ram: OrderedDict[str, tuple] = OrderedDict()
        self.ram_bytes = 0
        self.hits_ram = self.hits_disk = self.misses = self.writes = 0
        self.folder.mkdir(parents=True, exist_ok=True)
        manifest = self.folder / 'identity.json'
        if manifest.exists():
            saved = json.loads(manifest.read_text(encoding='utf-8'))
            if saved != identity:
                raise RuntimeError('CAPTURE_CACHE_IDENTITY_DRIFT')
        else:
            self._write_json_atomic(manifest, identity)

    @staticmethod
    def _write_json_atomic(target: Path, value: dict):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=target.parent,
                                         prefix='cache-tmp-', suffix='.json', delete=False) as f:
            tmp = Path(f.name)
            f.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        try:
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)

    def key(self, prompts: list[str]) -> str:
        # Identity includes the complete ordered batch, preserving any padding
        # or backend-algorithm effects of the original Qwen forward.
        return hashlib.sha256((self.identity_digest + '\n' + canonical(prompts)).encode('utf-8')).hexdigest()

    def path(self, key: str) -> Path:
        return self.folder / key[:2] / (key + '.pt')

    @staticmethod
    def nbytes(entry: tuple) -> int:
        return sum(v.numel() * v.element_size() for v in entry)

    def _remember(self, key: str, entry: tuple):
        size = self.nbytes(entry)
        if self.max_bytes == 0 or size > self.max_bytes:
            return
        if key in self.ram:
            self.ram_bytes -= self.nbytes(self.ram.pop(key))
        while self.ram and self.ram_bytes + size > self.max_bytes:
            _, old = self.ram.popitem(last=False)
            self.ram_bytes -= self.nbytes(old)
        self.ram[key] = entry
        self.ram_bytes += size

    def _load(self, key: str, batch_size: int):
        import torch
        target = self.path(key)
        if not target.exists():
            return None
        item = torch.load(target, map_location='cpu', weights_only=True)
        if not isinstance(item, dict) or item.get('schema') != CAPTURE_SCHEMA or item.get('key') != key:
            raise RuntimeError('CAPTURE_CACHE_RECORD_DRIFT:' + str(target))
        entry = (item['flat'], item['seq'], item['mask'])
        if (any(v.device.type != 'cpu' for v in entry) or
            entry[0].ndim != 2 or entry[1].ndim != 3 or entry[2].ndim != 2 or
            entry[0].shape[0] != batch_size or
            entry[1].shape[:2] != entry[2].shape or
            entry[1].shape[0] != batch_size or
            entry[0].dtype != torch.float32 or entry[1].dtype != torch.float32 or
            entry[2].dtype != torch.bool):
            raise RuntimeError('CAPTURE_CACHE_SHAPE_OR_DTYPE_DRIFT:' + str(target))
        return entry

    def _save(self, key: str, entry: tuple):
        import torch
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if self._load(key, entry[0].shape[0]) is None:
                raise RuntimeError('CAPTURE_CACHE_RACE')
            return
        with tempfile.NamedTemporaryFile('wb', dir=target.parent, prefix='capture-tmp-',
                                         suffix='.pt', delete=False) as f:
            temp = Path(f.name)
        try:
            torch.save({'schema': CAPTURE_SCHEMA, 'key': key,
                        'flat': entry[0], 'seq': entry[1], 'mask': entry[2]}, temp)
            os.replace(temp, target)
            self.writes += 1
        finally:
            temp.unlink(missing_ok=True)

    def capture(self, model: dict, prompts: list[str]):
        """Exact full_capture reuse for ordered prompt batches.

        Training calls use one prompt. Probe/diagnosis calls keep their original
        prompt batch and padding. Cached float32 CPU tensors are lossless.
        """
        key = self.key(prompts)
        cached = self.ram.get(key)
        if cached is not None:
            self.hits_ram += 1
            self.ram.move_to_end(key)
            return cached
        cached = self._load(key, len(prompts))
        if cached is not None:
            self.hits_disk += 1
            self._remember(key, cached)
            return cached
        from tools import nanojev_altreg_math_v3_decoder_experiment as v3
        flat, seq, mask = v3.full_capture(model['backbone'], model['tokenizer'],
                                           model['device'], prompts,
                                           model['config']['max_prompt_tokens'])
        entry = (flat, seq, mask)
        self._save(key, entry)
        self._remember(key, entry)
        self.misses += 1
        return entry

    def stats(self) -> dict:
        return {'ram_hits': self.hits_ram, 'disk_hits': self.hits_disk,
                'misses': self.misses, 'writes': self.writes,
                'memory_bytes': self.ram_bytes, 'entries_in_ram': len(self.ram)}


def cached_forward(model: dict, prompts: list[str], cache: FrozenCaptureCache):
    import torch
    flat, seq, mask = cache.capture(model, prompts)
    device = model['device']
    base = model['base']
    # Identical to ex.forward except its frozen capture source.
    with torch.no_grad():
        h = model['parent'].extract(flat.to(device))
        pxy = model['parent'].xy_coord(h)
    neutral = torch.full((len(prompts),), base.OP_IDS['written_sub'], device=device, dtype=torch.long)
    out = model['decoder'](h, pxy, neutral, seq.to(device), mask.to(device))
    logits = model['gamehead'](seq.to(device), mask.to(device))
    return out, logits


def prepare_cache(args, memory_budget_mb: int):
    # Strict version binding: input identities and the underlying *original*
    # trainer SHA are included; no code hashes are rewritten in checkpoints.
    destination = Path(args.output).expanduser().resolve()
    cfg = json.loads((destination / 'config.json').read_text(encoding='utf-8'))
    expected_sha = cfg['trainer_sha256']
    actual_sha = file_hash(Path(science.__file__))
    if expected_sha != actual_sha:
        raise RuntimeError('CACHED_MODE_TRAINER_SHA_DRIFT: ' + actual_sha + ' expected ' + expected_sha)
    if file_hash(Path(args.source)) != cfg['source_sha256']:
        raise RuntimeError('CACHED_MODE_SOURCE_DRIFT')
    info = science.teacher_info(args.teacher)
    if info['sha256'] != cfg['teacher_sha256']:
        raise RuntimeError('CACHED_MODE_TEACHER_DRIFT')
    identity = {'schema': SCHEMA, 'source_sha256': cfg['source_sha256'],
                'teacher_sha256': cfg['teacher_sha256'],
                'trainer_sha256': expected_sha,
                'backbone_id': 'Qwen/Qwen3-0.6B',
                'capture_implementation_sha256': file_hash(Path(ex.__file__)),
                'v3_capture_sha256': file_hash(Path(__file__).with_name('nanojev_altreg_math_v3_decoder_experiment.py')),
                'max_prompt_tokens': None}
    # Frozen-model parameters are pinned by the verified scientific lineage.
    # For identity, use source checkpoint's complete config max_prompt_tokens.
    import torch
    source_cp = torch.load(args.source, map_location='cpu', weights_only=True)
    identity['max_prompt_tokens'] = int(source_cp['config']['max_prompt_tokens'])
    # A separate immutable, human-readable audit record binds acceleration code.
    audit = {'schema': 'nanojev.hawk-dove.cached-execution-audit.v1',
             'wrapper_sha256': file_hash(Path(__file__)),
             'cache_identity': identity,
             'semantics': 'exact frozen full_capture tensor reuse; no optimizer/decoder/math changes'}
    audit_path = destination / 'cached-execution-audit.json'
    if audit_path.exists():
        if json.loads(audit_path.read_text(encoding='utf-8')) != audit:
            raise RuntimeError('CACHED_EXECUTION_AUDIT_DRIFT')
    else:
        FrozenCaptureCache._write_json_atomic(audit_path, audit)
    return FrozenCaptureCache(destination / 'frozen-capture-cache-v1', identity, memory_budget_mb)


def main(argv=None):
    # The parser and all science.run arguments are inherited unchanged.
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--cache-memory-mb', type=int, default=1024)
    ours, rest = parser.parse_known_args(argv)
    args = science.make_parser().parse_args(rest)
    if args.mode == 'preflight':
        science.main(rest)
        return
    cache = prepare_cache(args, ours.cache_memory_mb)
    baseline = ex.forward
    original_read_rows = hd.read_rows
    original_eligible = rt.token_eligible
    # Teacher DB has already been SHA-verified by prepare_cache. Science only
    # ever samples immutable rows; do not reopen/reparse SQLite every step.
    @lru_cache(maxsize=64)
    def memo_rows(path, split, tier=None, limit=None):
        return original_read_rows(path, split, tier, limit)
    @lru_cache(maxsize=65536)
    def memo_eligible(prompt: str) -> bool:
        # Eligibility depends only on frozen tokenizer + fixed token window.
        return original_eligible(_active_model[0], prompt)
    _active_model = [None]
    def memo_eligible_call(model, prompt):
        if _active_model[0] is None:
            _active_model[0] = model
        elif _active_model[0] is not model:
            raise RuntimeError('CACHED_EXECUTION_MODEL_CHANGED')
        return memo_eligible(prompt)
    hd.read_rows = memo_rows
    rt.token_eligible = memo_eligible_call
    calls = [0]
    def forward_with_cache(model, prompts):
        calls[0] += 1
        out = cached_forward(model, prompts, cache)
        if calls[0] % 10000 == 0:
            print(json.dumps({'event': 'hawk_dove_cache_progress',
                              'forward_calls': calls[0], 'cache': cache.stats(),
                              'row_cache': memo_rows.cache_info()._asdict(),
                              'token_eligibility_cache': memo_eligible.cache_info()._asdict()},
                             sort_keys=True), flush=True)
        return out
    ex.forward = forward_with_cache
    try:
        science.main(rest)
    finally:
        ex.forward = baseline
        hd.read_rows = original_read_rows
        rt.token_eligible = original_eligible
        print(json.dumps({'event': 'hawk_dove_frozen_feature_cache',
                          'row_cache': memo_rows.cache_info()._asdict(),
                          'token_eligibility_cache': memo_eligible.cache_info()._asdict(),
                          'stats': cache.stats()},
                         sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
