"""In-place, loss-preserving numerical input adapter for scientific NanoJev V3.

The existing scientific trainer, V3 head implementation, its head parameters,
optimizer moments, training gates, source and frozen-capture cache are unchanged.
Only an opt-in adapter in the decoder's existing input coordinates is added.
The checkpoint is backed up BEFORE its one-time atomic schema extension.
Do not use the old scientific runner on the migrated checkpoint.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile

from tools import nanojev_hawk_dove_million as hd
from tools import nanojev_hawk_dove_scientific_train as science
from tools import nanojev_hawk_dove_cached_train as cached
from tools import nanojev_game_transform_experiment as ex

CONTRACT = 'nanojev.hawk-dove.v3-numeric-input-adapter.v1'
SCALE = 1_000_000.0
FEATURES = 12
HIDDEN = 64
BACKUP = 'latest.pre-numeric-adapter.pt'
MIGRATION = 'numeric-adapter-migration.json'
COUNT = re.compile(r'\d+')
PROSE = re.compile(r'Your opponent plays (Hawk|Dove)\s+(\d+)\s+out of', re.IGNORECASE)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def is_count(item):
    return isinstance(item, str) and COUNT.fullmatch(item) is not None and 0 <= int(item) <= 1_000_000


@lru_cache(maxsize=32768)
def encode(prompt: str) -> tuple[float, ...]:
    """Operand roles, not answers. Validated annotations are removed first.

    Feature order: four per-payoff counts in millions, four presence flags,
    two English-only count values and two English presence flags. Oracle X/Y
    literals in ARGMAX are NEVER encoded. No payoff coefficients are stored.
    """
    cleaned = hd.strip_and_validate_bars(prompt, hd.REP_SCIENCE)
    seen = {}
    prose = {}
    for name, raw in PROSE.findall(cleaned):
        key = name.lower()
        if key in prose or not is_count(raw):
            raise ValueError('NUMERIC_ADAPTER_PROSE_COUNT_INVALID')
        prose[key] = int(raw)
    start = cleaned.find('(ARGMAX')
    if start >= 0:
        tree = hd.parse_lisp(cleaned[start:].strip())
        if not isinstance(tree, tuple) or len(tree) != 3 or tree[0] != 'ARGMAX':
            raise ValueError('NUMERIC_ADAPTER_ARGMAX_INVALID')
        def walk(node):
            if not isinstance(node, tuple):
                return
            if len(node) != 3 or node[0] not in ('ARGMAX', 'ADD', 'MUL'):
                raise ValueError('NUMERIC_ADAPTER_UNSUPPORTED_NODE')
            if node[0] == 'MUL':
                left, right = node[1:]
                pairs = [(left, right), (right, left)]
                matching = [(v, tag) for v, tag in pairs if tag in hd.TAGS and is_count(v)]
                if len(matching) != 1:
                    raise ValueError('NUMERIC_ADAPTER_MUL_UNSUPPORTED')
                value, tag = matching[0]
                if tag in seen and seen[tag] != int(value):
                    raise ValueError('NUMERIC_ADAPTER_OPERAND_CONFLICT')
                seen[tag] = int(value)
                return
            walk(node[1]);walk(node[2])
        walk(tree)
    elif 'ARGMAX' in cleaned:
        raise ValueError('NUMERIC_ADAPTER_BROKEN_ARGMAX')
    elif not (set(prose) == {'hawk', 'dove'}):
        raise ValueError('NUMERIC_ADAPTER_UNKNOWN_PROMPT')
    if prose and set(prose) != {'hawk', 'dove'}:
        raise ValueError('NUMERIC_ADAPTER_INCOMPLETE_PROSE')
    if prose:
        # The prose is a separate input role. It is not an oracle output.
        if sum(prose.values()) != 1_000_000:
            raise ValueError('NUMERIC_ADAPTER_PROSE_COUNTS_INVALID')
        for a, b in ((hd.TAGS[0], hd.TAGS[1]), (hd.TAGS[2], hd.TAGS[3])):
            if a in seen and seen[a] != prose['hawk']:
                raise ValueError('NUMERIC_ADAPTER_PROSE_LISP_MISMATCH')
            if b in seen and seen[b] != prose['dove']:
                raise ValueError('NUMERIC_ADAPTER_PROSE_LISP_MISMATCH')
    values = [seen.get(tag, 0) / SCALE for tag in hd.TAGS]
    flags = [float(tag in seen) for tag in hd.TAGS]
    values += [prose.get('hawk', 0) / SCALE, prose.get('dove', 0) / SCALE]
    flags += [float('hawk' in prose), float('dove' in prose)]
    return tuple(values[:4] + flags[:4] + values[4:] + flags[4:])


def make_adapter(width):
    import torch
    from torch import nn
    class Adapter(nn.Module):
        def __init__(self):
            super().__init__()
            self.first = nn.Linear(FEATURES, HIDDEN)
            self.activation = nn.GELU()
            self.last = nn.Linear(HIDDEN, width, bias=False)
            nn.init.zeros_(self.last.weight)

        def forward(self, features):
            if features.ndim != 2 or features.shape[1] != FEATURES:
                raise RuntimeError('NUMERIC_ADAPTER_FEATURE_SHAPE')
            # Preserve T00 and other prompts with no operand information even
            # after the adapter has been trained and acquired a nonzero bias.
            has_inputs = features[:, 4:8].sum(-1) + features[:, 10:12].sum(-1)
            return self.last(self.activation(self.first(features))) * (has_inputs > 0).to(features.dtype).unsqueeze(-1)
    return Adapter()


def adapter_width(decoder, *, n_ops):
    if decoder.arm != 'attention' or decoder.queries is None:
        raise ValueError('NUMERIC_ADAPTER_REQUIRES_ATTENTION_V3')
    whole = int(decoder.body[0].normalized_shape[0])
    width = whole - (1 + int(n_ops) + 16 + int(decoder.queries.shape[-1]))
    if width <= 0:
        raise ValueError('NUMERIC_ADAPTER_BAD_V3_INPUT_WIDTH')
    return width


def checkpoint_width(ck):
    # Decoder's normalized body input consists of h, parent_xy, operation
    # one-hot, slot embedding, and attention pool. Head weights are preserved.
    from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
    state = ck['decoder']
    whole = len(state['body.0.weight'])
    attn = int(state['queries'].shape[-1])
    width = whole - (1 + len(base.OP_IDS) + 16 + attn)
    if width <= 0:
        raise ValueError('NUMERIC_ADAPTER_BAD_SAVED_WIDTH')
    return width


def attach(model, expected_width=None):
    from tools import nanojev_altreg_independent_math_qwen_precision_v2 as base
    dec = model['decoder']
    if hasattr(dec, 'numeric_adapter'):
        raise ValueError('NUMERIC_ADAPTER_DOUBLE_ATTACHMENT')
    width = adapter_width(dec, n_ops=len(base.OP_IDS))
    if expected_width is not None and width != expected_width:
        raise ValueError('NUMERIC_ADAPTER_WIDTH_DRIFT')
    dec.add_module('numeric_adapter', make_adapter(width).to(model['device']))
    return dec.numeric_adapter


def adapter_contract(width):
    return {'schema': CONTRACT, 'feature_count': FEATURES, 'hidden': HIDDEN,
            'width': int(width), 'module_sha256': digest(Path(__file__)),
            'injection': 'zero-initialized additive adapter on frozen parent h',
            'no_target_literals': True}


def verify_saved(ck):
    contract = ck.get('numeric_adapter_contract')
    if not isinstance(contract, dict):
        raise ValueError('NUMERIC_ADAPTER_UPGRADE_REQUIRED')
    expected = adapter_contract(checkpoint_width(ck))
    if contract != expected:
        raise ValueError('NUMERIC_ADAPTER_CONTRACT_DRIFT')
    model = make_adapter(contract['width'])
    actual_keys = {'numeric_adapter.' + key for key in model.state_dict()}
    if actual_keys != {key for key in ck['decoder'] if key.startswith('numeric_adapter.')}:
        raise ValueError('NUMERIC_ADAPTER_STATE_KEYS_DRIFT')
    if len(ck['optimizer']['param_groups']) != 1:
        raise ValueError('NUMERIC_ADAPTER_OPTIMIZER_GROUPS_INVALID')
    return contract


def load_run_state(run):
    import torch
    run = Path(run).resolve()
    path = run / 'latest.pt'
    if not path.exists():
        raise FileNotFoundError(path)
    ck = torch.load(path, map_location='cpu', weights_only=True)
    conf = json.loads((run / 'config.json').read_text(encoding='utf8'))
    if ck['schema'] != science.CHECKPOINT_SCHEMA or ck['config'] != conf:
        raise ValueError('NUMERIC_ADAPTER_CHECKPOINT_LINEAGE_DRIFT')
    if conf['trainer_sha256'] != digest(Path(science.__file__)):
        raise ValueError('NUMERIC_ADAPTER_SCIENCE_TRAINER_DRIFT')
    if ck['step'] < 0 or ck['focus'] not in hd.TIERS:
        raise ValueError('NUMERIC_ADAPTER_INVALID_CURRICULUM_POSITION')
    return ck, path


def migrate(run):
    """One-time checkpoint extension. Originals are saved verbatim in BACKUP."""
    import torch
    ck, path = load_run_state(run)
    run = path.parent
    backup, manifest = run / BACKUP, run / MIGRATION
    if ck.get('numeric_adapter_contract'):
        verify_saved(ck)
        if not backup.is_file() or not manifest.is_file():
            raise ValueError('NUMERIC_ADAPTER_BACKUP_OR_MANIFEST_MISSING')
        record = json.loads(manifest.read_text(encoding='utf8'))
        if record.get('backup_sha256') != digest(backup) or record.get('step') != ck['step_at_migration']:
            raise ValueError('NUMERIC_ADAPTER_MIGRATION_AUDIT_DRIFT')
        print(json.dumps({'event':'numeric_adapter_migration_reused','step':ck['step'],
                          'original_step':ck['step_at_migration']}),flush=True)
        return
    if backup.exists() or manifest.exists():
        raise ValueError('NUMERIC_ADAPTER_PARTIAL_OR_PREEXISTING_MIGRATION')
    width = checkpoint_width(ck)
    # Original decoder and optimizer parameters are unchanged; new parameters
    # append after the existing decoder parameters in the SAME optimizer group.
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(20261019)
        adapter = make_adapter(width)
    params = list(adapter.parameters())
    groups = ck['optimizer']['param_groups']
    if len(groups) != 1 or not isinstance(groups[0].get('params'), list):
        raise ValueError('NUMERIC_ADAPTER_UNSUPPORTED_OPTIMIZER_LAYOUT')
    old = groups[0]['params']
    # Current V3 has no persistent buffers; every state-dict entry is a
    # trainable parameter, and AdamW's group order matches module order.
    if (len(old) != len(ck['decoder']) or len(set(old)) != len(old) or
            set(ck['optimizer']['state']) - set(old)):
        raise ValueError('NUMERIC_ADAPTER_OPTIMIZER_LAYOUT_DRIFT')
    if any(key.startswith('numeric_adapter.') for key in ck['decoder']):
        raise ValueError('NUMERIC_ADAPTER_UNRECORDED_STATE')
    for key, val in adapter.state_dict().items():
        ck['decoder']['numeric_adapter.' + key] = val.clone()
    next_id = max(old) + 1 if old else 0
    ck['optimizer']['param_groups'][0]['params'] = old + list(range(next_id, next_id + len(params)))
    ck['numeric_adapter_contract'] = adapter_contract(width)
    ck['step_at_migration'] = ck['step']
    original_sha = digest(path)
    with backup.open('xb') as output, path.open('rb') as source:
        shutil.copyfileobj(source, output)
        output.flush(); os.fsync(output.fileno())
    if digest(backup) != original_sha:
        raise ValueError('NUMERIC_ADAPTER_BACKUP_HASH_MISMATCH')
    with tempfile.NamedTemporaryFile('wb', dir=run, prefix='numeric-adapter-', suffix='.pt', delete=False) as tmp:
        target = Path(tmp.name)
    try:
        torch.save(ck, target)
        check = torch.load(target,map_location='cpu',weights_only=True)
        verify_saved(check)
        if check['step'] != ck['step'] or check['focus'] != ck['focus'] or check['rng_cpu'].shape != ck['rng_cpu'].shape:
            raise ValueError('NUMERIC_ADAPTER_UPGRADE_STATE_DRIFT')
        os.replace(target, path)
    finally:
        target.unlink(missing_ok=True)
    record = {'schema':CONTRACT, 'step':ck['step'], 'backup_sha256':original_sha,
              'upgraded_checkpoint_sha256':digest(path), 'contract':ck['numeric_adapter_contract']}
    temp = manifest.with_suffix('.json.tmp')
    temp.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n',encoding='utf8')
    os.replace(temp,manifest)
    print(json.dumps({'event':'numeric_adapter_upgraded_in_place','step':ck['step'],
                      'backup':str(backup),'backup_sha256':original_sha,
                      'head_unchanged':True,'old_optimizer_moments_preserved':True}),flush=True)


@contextmanager
def inject(decoder, prompts, device):
    import torch
    features = torch.tensor([encode(p) for p in prompts], dtype=torch.float32, device=device)
    old_forward = decoder.forward
    def adapted(h, parent_xy, operations, tokens=None, mask=None):
        delta = decoder.numeric_adapter(features.to(h.device))
        if h.shape != delta.shape:
            raise ValueError('NUMERIC_ADAPTER_H_SHAPE_DRIFT')
        return old_forward(h + delta, parent_xy, operations, tokens, mask)
    decoder.forward = adapted
    try:
        yield
    finally:
        decoder.forward = old_forward


def execute(args):
    ck, _ = load_run_state(args.output)
    verify_saved(ck)
    baseline_load = ex.load_current
    baseline_cached = cached.cached_forward
    width = ck['numeric_adapter_contract']['width']
    def patched_load(*a, **kw):
        model = baseline_load(*a, **kw)
        attach(model, expected_width=width)
        return model
    def patched_cached(model, prompts, cache):
        with inject(model['decoder'],prompts,model['device']):
            return baseline_cached(model,prompts,cache)
    ex.load_current = patched_load
    cached.cached_forward = patched_cached
    try:
        cached.main(['--cache-memory-mb',str(args.cache_memory_mb), *args.science_args])
    finally:
        ex.load_current = baseline_load
        cached.cached_forward = baseline_cached


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-memory-mb',type=int,default=1024)
    parser.add_argument('--upgrade-only',action='store_true')
    ours, remaining = parser.parse_known_args(argv)
    args = science.make_parser().parse_args(remaining)
    if args.mode == 'test':
        raise ValueError('NUMERIC_ADAPTER_TEST_SPLIT_SEALED')
    if args.mode == 'preflight':
        cached.main(['--cache-memory-mb',str(ours.cache_memory_mb), *remaining])
        return
    if args.mode == 'train' or ours.upgrade_only:
        migrate(args.output)
    else:
        ck, _ = load_run_state(args.output)
        verify_saved(ck)
    if ours.upgrade_only:
        return
    execute(argparse.Namespace(output=args.output,cache_memory_mb=ours.cache_memory_mb,science_args=remaining))


if __name__ == '__main__':
    main()
