"""V7 seeded within-tier mutation checkpoints for deterministic NanoJev game mathematics.

Each tier admits two distinct source-variable mutations and a dependency-gated
checkpoint validation. Both legal orders are independently executed and must
commute in the canonical source state and T00 projection. Only the completed
checkpoint can descend. The native v6 runner remains the mathematical authority;
this module does NOT train a neural model and makes no universal learnability claim.

ORDER seed changes the legal trace, not the canonical result. MUTATION seed
changes selected numerical perturbations and therefore may change the result.
A separate deterministic seed namespace is used per game, tier and variable.
"""
from __future__ import annotations

from contextlib import closing

from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import random
import sqlite3
from pathlib import Path
from typing import Any

from . import nanojev_game_native_objectives_v6 as native

VERSION = 'nanojev.mutation-checkpoint-brain.v7'


def stable_rng(*parts: Any) -> random.Random:
    raw = json.dumps(parts, sort_keys=True, separators=(',', ':'), default=str)
    return random.Random(int.from_bytes(hashlib.sha256(raw.encode()).digest(), 'big'))


def projection(facts: dict) -> dict:
    x, y, op, z, semantics, _, _ = native._compute(facts)
    return {'X': native.mathlib.out(x), 'Y': native.mathlib.out(y),
            'OP': op, 'Z': native.mathlib.out(z), 'Z_semantics': semantics}


def state_at(facts: dict, tier: int) -> dict:
    """Recompile the canonical level without calling finish()/the final oracle.

    The v6 domain validator is still called by begin() and step(); for scalar
    games it includes its existing domain oracle check. The independent final
    oracle remains in finish(). This is a correctness harness, not an optimized
    incremental executor; the recomputation is deliberately made explicit.
    """
    if tier not in range(9): raise ValueError('TIER_OUT_OF_RANGE')
    st = native.begin(native.render08(facts))
    while st['tier'] > tier:
        st, _ = native.step(st)
    return st


def candidate_paths(f: dict) -> tuple[tuple, tuple]:
    """Choose two distinct source leaves, retaining side-specific leaves where possible."""
    if f['kind'] == 'scalar':
        import ast
        a, b, _ = native._children(native._expr(f['game_id']))
        left = {n.id for n in ast.walk(a) if isinstance(n, ast.Name)}
        right = {n.id for n in ast.walk(b) if isinstance(n, ast.Name)}
        allvars = sorted(f['parameters'])
        xonly, yonly = sorted(left-right), sorted(right-left)
        if xonly and yonly:
            return ('param', xonly[0]), ('param', yonly[0])
        # Genuine coupled XY primitive may be unavoidable (e.g. Bayes posterior).
        a_key = sorted(left)[0]
        b_key = next((v for v in yonly if v != a_key), None)
        if b_key is None: b_key = next((v for v in allvars if v != a_key), None)
        if b_key is None: raise ValueError('NO_TWO_INDEPENDENT_SOURCE_PATHS')
        return ('param', a_key), ('param', b_key)
    if f['mode'] == 'best_response':
        c = f['selector']
        return ('payoff', 0, c, 0), ('payoff', 1, c, 0)
    # In a fully-mixed 2x2 game, a payoff can feed both numerator and
    # denominator. These are source-independent but may be XY-coupled.
    if f['mode'] == 'mixed_row':
        return ('payoff', 0, 0, 1), ('payoff', 1, 1, 1)
    if f['mode'] == 'mixed_col':
        return ('payoff', 0, 0, 0), ('payoff', 1, 1, 0)
    raise ValueError('UNSUPPORTED_MUTATION_GAME')


def _read(f: dict, path: tuple) -> float:
    if path[0] == 'param': return float(f['parameters'][path[1]])
    if path[0] == 'payoff': return float(f['U'][path[1]][path[2]][path[3]])
    raise ValueError('UNKNOWN_PATH')


def _write(f: dict, path: tuple, value: float) -> None:
    if path[0] == 'param': f['parameters'][path[1]] = value
    elif path[0] == 'payoff': f['U'][path[1]][path[2]][path[3]] = value
    else: raise ValueError('UNKNOWN_PATH')


def _footprint(before: dict, after: dict) -> str:
    x = before['X'] != after['X']
    y = before['Y'] != after['Y']
    o = before['OP'] != after['OP']
    if o: return 'OP'
    if x and y: return 'XY'
    if x: return 'X'
    if y: return 'Y'
    return 'INVARIANT'


def _safe_edit(f: dict, path: tuple, signed: float, tier: int) -> dict:
    """Select a bounded, nonzero delta valid under the native source domain."""
    original = _read(f, path)
    scale = max(1.0, abs(original))
    # Small exact-decimal perturbations: repeated levels stay near the source.
    magnitude = min(0.1, scale * 0.001)
    for k in range(14):
        d = round(signed * magnitude / (2 ** k), 10)
        if not d: continue
        trial = deepcopy(f)
        new = round(original + d, 10)
        if new == original: continue
        _write(trial, path, new)
        try:
            # Refuse a mutation if ANY intermediate state is mathematically invalid.
            state_at(trial, tier)
            projection(trial)
        except (ValueError, ZeroDivisionError, AssertionError, OverflowError):
            continue
        return trial
    raise ValueError('NO_DOMAIN_VALID_MUTATION: ' + str(path))


def _plan(f: dict, tier: int, mutation_seed: int) -> dict[str, dict]:
    paths = list(candidate_paths(f))
    if f['kind'] == 'scalar':
        # Additional required mutations, bounded to two and independently
        # anchored to distinct variables. Z1/Z2 are NOT the final answer Z.
        for key in sorted(f['parameters']):
            path = ('param', key)
            if path not in paths:
                paths.append(path)
            if len(paths) == 4: break
    if len(set(paths)) != len(paths):raise AssertionError('OVERLAPPING_MUTATION_PATHS')
    out = {}
    for index, path in enumerate(paths):
        rng = stable_rng('magnitude-sign', mutation_seed, f['game_id'], f['mode'],
                         f.get('selector'), tier, index, path)
        sign = 1.0 if rng.randrange(2) else -1.0
        trial = _safe_edit(f, path, sign, tier)
        name = ('X' if index == 0 else 'Y' if index == 1 else f'Z{index-1}')
        out[name] = {'name': name, 'path': path,
                     'before': _read(f, path), 'after': _read(trial, path)}
    return out


def _schedule(changes: dict[str, dict], order_seed: int, f: dict, tier: int) -> tuple[str,...]:
    # A real seeded topological scheduler: independent leaves are eligible
    # in any order; Z_DOMAIN is a gated final dependency barrier.
    deps = {key: set() for key in changes}
    deps['Z_DOMAIN'] = set(changes)
    remaining = set(deps)
    complete = set()
    rng = stable_rng('order', order_seed, f['game_id'], f['mode'], f.get('selector'), tier)
    result = []
    while remaining:
        eligible = sorted(key for key in remaining if deps[key] <= complete)
        if not eligible: raise AssertionError('MUTATION_DEPENDENCY_CYCLE')
        chosen = eligible[rng.randrange(len(eligible))]
        remaining.remove(chosen);complete.add(chosen);result.append(chosen)
    if result[-1] != 'Z_DOMAIN':raise AssertionError('UNCLOSED_CHECKPOINT_PROMOTED')
    return tuple(result)


def _apply(f: dict, item: dict, tier: int) -> dict:
    trial = deepcopy(f)
    if _read(trial, item['path']) != item['before']:
        raise ValueError('STALE_MUTATION_PRECONDITION')
    _write(trial, item['path'], item['after'])
    state_at(trial, tier)
    return trial


def _execute_order(f: dict, tier: int, changes: dict, order: tuple[str,...]) -> tuple[dict, list]:
    current = deepcopy(f)
    steps = []
    completed = set()
    for label in order:
        if label == 'Z_DOMAIN':
            if completed != set(changes):
                raise ValueError('CHECKPOINT_PROMOTED_BEFORE_REQUIRED_MUTATIONS')
            cp = state_at(current, tier)
            steps.append({'mutation': 'Z_DOMAIN', 'depends_on': sorted(changes),
                          'checkpoint_sha256': native.sha(cp), 'status': 'PASS'})
            completed.add(label)
            continue
        if label not in changes:raise ValueError('UNKNOWN_MUTATION_CLASS')
        if label in completed:raise ValueError('DUPLICATE_MUTATION')
        item = changes[label]
        before = projection(current)
        current = _apply(current, item, tier)
        after = projection(current)
        completed.add(label)
        steps.append({'mutation': label, 'source_path': list(item['path']),
                      'before_value': item['before'], 'after_value': item['after'],
                      'operand_footprint': _footprint(before, after),
                      'before': before, 'after': after})
    if completed != set(changes) | {'Z_DOMAIN'}:
        raise ValueError('INCOMPLETE_CHECKPOINT')
    return current, steps


def checkpoint(f: dict, tier: int, order_seed: int, mutation_seed: int) -> dict:
    changes = _plan(f, tier, mutation_seed)
    chosen_order = _schedule(changes, order_seed, f, tier)
    chosen, trace = _execute_order(f, tier, changes, chosen_order)
    # A canonical independent reference order, not merely agreement of Z.
    canonical_order = tuple(sorted(changes)) + ('Z_DOMAIN',)
    canonical, canonical_trace = _execute_order(f, tier, changes, canonical_order)
    if native.canon(chosen) != native.canon(canonical):raise AssertionError('NONCOMMUTING_FACTS')
    if native.sha(state_at(chosen, tier)) != native.sha(state_at(canonical, tier)):
        raise AssertionError('NONCOMMUTING_CHECKPOINT')
    if projection(chosen) != projection(canonical):raise AssertionError('NONCOMMUTING_T00')
    return {'tier': tier, 'order': list(chosen_order),
            'changes': [changes[k] for k in sorted(changes)],
            'steps': trace, 'facts': chosen, 'state': state_at(chosen, tier),
            'commutation': 'PASS', 'checkpoint_complete': True}


def run(prompt: str, *, order_seed: int, mutation_seed: int) -> dict:
    f = native.parse08(prompt)
    original = projection(f)
    original_id = native.sha(prompt)
    history = []
    for tier in range(8, -1, -1):
        cp = checkpoint(f, tier, order_seed, mutation_seed)
        f = cp['facts']
        st = cp['state']
        before_next = None
        if tier:
            descended, edge = native.step(st)
            target = state_at(f, tier-1)
            if native.sha(descended) != native.sha(target):
                raise AssertionError('CHECKPOINT_DESCENT_NOT_COMMUTATIVE')
            before_next = edge['after_hash']
        else:
            # The only stage that produces the authoritative game-native answer.
            final = native.finish(st)
            if final['Z'] != projection(f)['Z']:
                raise AssertionError('COUPLED_T00_WRONG')
        history.append({'tier': tier, 'order': cp['order'],
                        'changes': cp['changes'], 'steps': cp['steps'],
                        'checkpoint_hash': native.sha(st),
                        'prompt': st['prompt'], 'downward_hash': before_next,
                        'X_Y_OP_Z': projection(f)})
    return {'version': VERSION, 'input_sha256': original_id,
            'order_seed': order_seed, 'mutation_seed': mutation_seed,
            'original': original, 'final': final,
            'final_projection': projection(f), 'levels': history,
            'checkpoint_count': 9, 'single_source_mutations': sum(len(h['changes']) for h in history),
            'composition_proven': True, 'descent_proven': True,
            'native_oracle_checked_at_T00': True, 'model_trained': False}


def assert_seed_contract(prompt: str, mutation_seed: int = 17,
                         order_seeds: tuple[int, int] = (11, 29)) -> dict:
    a = run(prompt, order_seed=order_seeds[0], mutation_seed=mutation_seed)
    b = run(prompt, order_seed=order_seeds[1], mutation_seed=mutation_seed)
    if a['final'] != b['final'] or a['final_projection'] != b['final_projection']:
        raise AssertionError('ORDER_SEED_ALTERED_RESULT')
    if [t['checkpoint_hash'] for t in a['levels']] != [t['checkpoint_hash'] for t in b['levels']]:
        raise AssertionError('ORDER_SEED_ALTERED_CHECKPOINT')
    c = run(prompt, order_seed=order_seeds[0], mutation_seed=mutation_seed + 1)
    changes = a['final_projection'] != c['final_projection']
    return {'status': 'PASS', 'game_id': native.parse08(prompt)['game_id'],
            'objective': native.parse08(prompt)['mode'],
            'order_traces_different': any(x['order'] != y['order'] for x, y in zip(a['levels'], b['levels'])),
            'same_final_for_order_variations': True,
            'different_projection_for_parameter_variations': changes,
            'baseline_final': a['final'], 'parameter_mutated_final': c['final'],
            'order_seed_pair': list(order_seeds),
            'mutation_seed_pair': [mutation_seed, mutation_seed+1],
            'checkpoints_per_run': 9}


def database_audit(conn: sqlite3.Connection, *, max_per_game: int = 1) -> dict:
    # Use database-backed T08 prompts only; never infer an oracle for catalog-only games.
    rows = conn.execute('''SELECT c.game_id,t.prompt FROM native_v6_cases c
       JOIN native_v6_tiers t ON t.case_id=c.case_id AND t.tier=8
       ORDER BY c.game_id,c.case_id''').fetchall()
    seen = {}; failures = []
    for gid, prompt in rows:
        if seen.get(gid, 0) >= max_per_game: continue
        seen[gid] = seen.get(gid, 0) + 1
        try: assert_seed_contract(prompt)
        except Exception as exc: failures.append({'game_id': gid, 'reason': str(exc)})
    if failures: raise AssertionError('MUTATION_AUDIT_FAILED: '+native.canon(failures))
    return {'status': 'PASS', 'games_proven': len(seen),
            'cases_proven': sum(seen.values()), 'tiers_per_case': 9,
            'legal_orders_per_checkpoint': '2..24 depending on auxiliary mutations',
            'checkpoint_closure': 'PASS', 'T00_native_oracle': 'PASS',
            'cross_tier_consistency': 'PASS', 'catalog_only_gold_created': False,
            'proof_scope': 'seeded_source-leaf_mutations_within_v6_native_domain',
            'model_trained': False}


def export_proof_bank(conn: sqlite3.Connection, path: Path, *, seed: int) -> dict:
    """Source-generated proof bank; never touches an active training table."""
    rows = conn.execute("SELECT c.game_id,t.prompt FROM native_v6_cases c "
        "JOIN native_v6_tiers t ON t.case_id=c.case_id AND t.tier=8 "
        "ORDER BY c.game_id,c.case_id").fetchall()
    selected = {}
    for gid, prompt in rows:
        selected.setdefault(gid, prompt)
    if len(selected) != 33:
        raise AssertionError('EXPECTED_33_EXECUTABLE_GAMES')
    # Transaction completion alone does not close SQLite connections on Windows.
    # Close before the source smoke promotes or cleans up its staging directory.
    with closing(sqlite3.connect(path)) as out, out:
        out.executescript("""PRAGMA foreign_keys=ON;
        CREATE TABLE mutation_v7_runs(
          run_id TEXT PRIMARY KEY,game_id TEXT NOT NULL,objective TEXT NOT NULL,
          input_sha256 TEXT NOT NULL,order_seed INTEGER NOT NULL,
          mutation_seed INTEGER NOT NULL,original_json TEXT NOT NULL,
          final_json TEXT NOT NULL,status TEXT NOT NULL CHECK(status='PASS'));
        CREATE TABLE mutation_v7_checkpoints(
          run_id TEXT NOT NULL REFERENCES mutation_v7_runs(run_id),
          tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8),
          order_json TEXT NOT NULL,changes_json TEXT NOT NULL,
          steps_json TEXT NOT NULL,projection_json TEXT NOT NULL,
          checkpoint_sha256 TEXT NOT NULL,downward_sha256 TEXT,
          PRIMARY KEY(run_id,tier));""")
        pairs, numerical_variants, output_variants, order_variations, extras = 0, 0, 0, 0, 0
        for gid, prompt in sorted(selected.items()):
            runs = []
            for index in (0, 1):
                realization = run(prompt, order_seed=seed+101+index,
                                  mutation_seed=seed+211+index)
                runs.append(realization)
                run_id = native.sha([gid,realization['order_seed'],realization['mutation_seed']])
                out.execute('INSERT INTO mutation_v7_runs VALUES (?,?,?,?,?,?,?,?,?)',
                    (run_id,gid,native.parse08(prompt)['mode'],realization['input_sha256'],
                     realization['order_seed'],realization['mutation_seed'],
                     native.canon(realization['original']),native.canon(realization['final']),'PASS'))
                for h in realization['levels']:
                    extras += max(0,len(h['changes'])-2)
                    out.execute('INSERT INTO mutation_v7_checkpoints VALUES (?,?,?,?,?,?,?,?)',
                        (run_id,h['tier'],native.canon(h['order']),native.canon(h['changes']),
                         native.canon(h['steps']),native.canon(h['X_Y_OP_Z']),
                         h['checkpoint_hash'],h['downward_hash']))
            numerical_variants += runs[0]['final_projection'] != runs[1]['final_projection']
            output_variants += runs[0]['final']['Z'] != runs[1]['final']['Z']
            pairs += 1
            order_variations += sum(a['order'] != b['order']
                                    for a,b in zip(runs[0]['levels'],runs[1]['levels']))
        out.commit()
        if out.execute('PRAGMA foreign_key_check').fetchall():
            raise AssertionError('MUTATION_FOREIGN_KEYS')
        if out.execute('SELECT COUNT(*) FROM mutation_v7_checkpoints').fetchone()[0] != 18*pairs:
            raise AssertionError('MUTATION_CHECKPOINT_LOSS')
    return {'status':'PASS','games_proven':pairs,'realizations':2*pairs,
            'checkpoints':18*pairs,'source_mutations':36*pairs+extras,
            'auxiliary_source_mutations':extras,
            'mutated_operand_or_output_variants':numerical_variants,
            'distinct_final_Z_variants':output_variants,
            'order_trace_variations':order_variations,
            'order_seed_changes_math':False,
            'parameter_seed_can_change_math':True,
            'checkpoint_gate_verified':True,
            'verified_against_canonical_order':True,
            'source_generated':True,'catalog_only_gold_created':False,
            'training_performed':False,
            'proof_scope':'bounded_valid_source_leaf_mutations_on_native_v6_cases'}


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', required=True)
    p.add_argument('--case')
    p.add_argument('--order-seed', type=int, default=11)
    p.add_argument('--mutation-seed', type=int, default=17)
    p.add_argument('--audit', action='store_true')
    a = p.parse_args(argv)
    with sqlite3.connect(a.database) as db:
        if a.audit:
            print(json.dumps(database_audit(db), indent=2, sort_keys=True));return 0
        if a.case:
            row = db.execute('SELECT prompt FROM native_v6_tiers WHERE case_id=? AND tier=8',(a.case,)).fetchone()
        else:
            row = db.execute('SELECT t.prompt FROM native_v6_tiers t JOIN native_v6_cases c USING(case_id) WHERE t.tier=8 ORDER BY c.case_id LIMIT 1').fetchone()
        if row is None:raise ValueError('NO_NATIVE_CASE')
        result = run(row[0], order_seed=a.order_seed, mutation_seed=a.mutation_seed)
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__': raise SystemExit(main())
