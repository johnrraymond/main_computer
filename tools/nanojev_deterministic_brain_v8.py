"""V8: seed-replayable, level-local deterministic game/math teacher.

A native-v6 T08 prompt plus optional source-observation bindings and two seeds
produces nine closed checkpoints and a T00-coupled answer. No state_at(),
render08()->begin() restart, v6 step(), mathematical projection(), or oracle
is invoked by mutation planning or descent. The independent oracle is called
only AFTER the T00 expression has actually been executed.

This engine covers the executable native-v6 T08 grammar, not catalog-only games,
v4 pilot prose, or v5 positional-transformation T08 prompts. It is a deterministic
supervision generator, NOT a trained neural head.
"""
from __future__ import annotations

from contextlib import closing

import argparse
import ast
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
from typing import Any

from . import nanojev_game_native_objectives_v6 as native
from . import nanojev_game_mutation_brain_v7 as v7

VERSION = 'nanojev.deterministic-brain.v8'


def structural_domain(f: dict) -> None:
    """Validate the declared input shape, not the answer or an oracle."""
    native._validate_facts(f, check_oracle=False)
    if f['kind'] == 'matrix' and f['mode'].startswith('mixed'):
        m = f['U']
        # Interior mixed equilibrium exists iff each player's payoff
        # preference changes sign with the opposing player's action.
        u = lambda r, c: native.num(m[r][c][0])
        v = lambda r, c: native.num(m[r][c][1])
        row_col0 = u(0, 0) - u(1, 0)
        row_col1 = u(0, 1) - u(1, 1)
        col_row0 = v(0, 0) - v(0, 1)
        col_row1 = v(1, 0) - v(1, 1)
        if row_col0 * row_col1 >= 0 or col_row0 * col_row1 >= 0:
            raise ValueError('NO_INTERIOR_MIXED_NASH_DOMAIN')


def bind_inputs(f: dict, bindings: dict | None) -> dict:
    """Bind only public observations; never permit injected X/Y/OP/Z."""
    f = deepcopy(f)
    if not bindings:
        return f
    if not isinstance(bindings, dict):
        raise ValueError('INPUT_BINDINGS_MUST_BE_OBJECT')
    if f['kind'] == 'scalar':
        if set(bindings) != {'parameters'}:
            raise ValueError('ONLY_PARAMETERS_BINDABLE')
        params = bindings['parameters']
        if not isinstance(params, dict) or not set(params) <= set(f['parameters']):
            raise ValueError('UNRECOGNIZED_PARAMETER')
        for k, value in params.items():
            f['parameters'][k] = float(native.num(value))
    else:
        if set(bindings) != {'U'}:
            raise ValueError('ONLY_MATRIX_PAYOFFS_BINDABLE')
        U = bindings['U']
        if not isinstance(U, list) or len(U) != 2 or any(
            not isinstance(row, list) or len(row) != 2 for row in U
        ) or any(not isinstance(pair, list) or len(pair) != 2 for row in U for pair in row):
            raise ValueError('BAD_MATRIX_BINDING')
        f['U'] = [[[float(native.num(z)) for z in pair] for pair in row] for row in U]
    structural_domain(f)
    return f


def begin(prompt: str, *, inputs: dict | None = None) -> dict:
    # Parsing without scalar-oracle evaluation is distinct from the legacy
    # native.parse08() default (which retains its stronger historical checks).
    f = bind_inputs(native.parse08(prompt, verify_domain=False), inputs)
    structural_domain(f)
    st = {'tier': 8, 'prompt': native.render08(f), 'facts': f,
          'rule': None, 'expressions': None, 'X': None, 'Y': None,
          'OP': None, 'Z': None}
    return st


def state_identity(st: dict) -> str:
    return native.sha(st)


def _path_role(f: dict, path: tuple) -> str:
    """Symbolic dependency footprint, not an oracle-computed output delta."""
    if path[0] == 'param':
        a, b, _ = native._children(native._expr(f['game_id']))
        left = {n.id for n in ast.walk(a) if isinstance(n, ast.Name)}
        right = {n.id for n in ast.walk(b) if isinstance(n, ast.Name)}
        x, y = path[1] in left, path[1] in right
    else:
        _, r, c, player = path
        if f['mode'] == 'best_response':
            x, y = (player == 0 and c == f['selector'] and r == 0,
                    player == 0 and c == f['selector'] and r == 1)
        elif f['mode'] == 'mixed_row':
            # np = v11-v10; dp = v00-v10-v01+v11
            x = player == 1 and r == 1
            y = player == 1
        elif f['mode'] == 'mixed_col':
            # nq = u11-u01; dq = u00-u01-u10+u11
            x = player == 0 and c == 1
            y = player == 0
        else:
            raise ValueError('UNSUPPORTED_NATIVE_MODE')
    return 'XY' if x and y else 'X' if x else 'Y' if y else 'INVARIANT'


def plan(st: dict, mutation_seed: int, input_id: str) -> dict[str, dict]:
    """Schedule leaf edits without descending or evaluating a game oracle."""
    f = st['facts']; tier = st['tier']
    paths = list(v7.candidate_paths(f))
    if f['kind'] == 'scalar':
        for key in sorted(f['parameters']):
            p = ('param', key)
            if p not in paths:
                paths.append(p)
            if len(paths) == 4:
                break
    if len(paths) != len(set(paths)):
        raise ValueError('OVERLAPPING_MUTATION_PATHS')
    changes: dict[str, dict] = {}
    for index, path in enumerate(paths):
        name = 'X' if index == 0 else 'Y' if index == 1 else f'Z{index - 1}'
        original = v7._read(f, path)
        magnitude = min(.1, max(1., abs(original)) * .001)
        rng = v7.stable_rng('v8-mutation', mutation_seed, input_id, tier, index, path)
        sign = 1 if rng.randrange(2) else -1
        found = None
        for k in range(16):
            delta = round(sign * magnitude / (2 ** k), 10)
            value = round(original + delta, 10)
            if value == original:
                continue
            trial = deepcopy(f)
            v7._write(trial, path, value)
            try:
                structural_domain(trial)
            except (ValueError, ArithmeticError):
                continue
            found = value
            break
        if found is None:
            raise ValueError('NO_LEGAL_NUMERIC_MUTATION: ' + str(path))
        changes[name] = {'name': name, 'path': path, 'before': original,
                         'after': found, 'dependency_footprint': _path_role(f, path)}
    return changes


def _refresh_materialized(st: dict) -> None:
    """Update ONLY already-materialized slots at the current level."""
    tier = st['tier']; f = st['facts']
    if tier <= 3:
        st['Y'], _ = native._one_operand(f, 'Y')
    if tier <= 2:
        st['X'], _ = native._one_operand(f, 'X')
    st['prompt'] = native._prompt(st, tier)


def apply_local(st: dict, change: dict) -> dict:
    nxt = deepcopy(st)
    path = tuple(change['path'])
    if v7._read(nxt['facts'], path) != change['before']:
        raise ValueError('STALE_MUTATION_PRECONDITION')
    v7._write(nxt['facts'], path, change['after'])
    structural_domain(nxt['facts'])
    _refresh_materialized(nxt)
    return nxt


def check_checkpoint(st: dict) -> None:
    f = st['facts']; tier = st['tier']
    structural_domain(f)
    if st['prompt'] != native._prompt(st, tier):
        raise ValueError('TIER_PROMPT_INCONSISTENT')
    if tier <= 6:
        rule = 'SCALAR:' + f['formula_id'] if f['kind'] == 'scalar' else 'MATRIX:' + f['mode']
        if st['rule'] != rule:
            raise ValueError('RULE_INCONSISTENT')
    if tier <= 5:
        a, b, op = native._top(f)
        ex = {'X': ast.unparse(a) if isinstance(a, ast.AST) else a,
              'Y': ast.unparse(b) if isinstance(b, ast.AST) else b, 'OP': op}
        if st['OP'] != op or st['expressions'] != ex:
            raise ValueError('OPERAND_EXPRESSION_INCONSISTENT')
    if tier <= 3 and st['Y'] != native._one_operand(f, 'Y')[0]:
        raise ValueError('Y_NOT_LOCAL')
    if tier <= 2 and st['X'] != native._one_operand(f, 'X')[0]:
        raise ValueError('X_NOT_LOCAL')


def execute_checkpoint(st: dict, *, order_seed: int, mutation_seed: int,
                       input_id: str) -> tuple[dict, dict]:
    tier = st['tier']; before_hash = state_identity(st)
    changes = plan(st, mutation_seed, input_id)
    chosen_order = v7._schedule(changes, order_seed, st['facts'], tier)
    if chosen_order[-1] != 'Z_DOMAIN':
        raise ValueError('CHECKPOINT_GATE_NOT_LAST')

    def execute(order: tuple[str, ...]) -> tuple[dict, list[dict]]:
        current = deepcopy(st)
        finished = set()
        events = []
        for key in order:
            if key == 'Z_DOMAIN':
                if finished != set(changes):
                    raise ValueError('CHECKPOINT_PROMOTED_PREMATURELY')
                check_checkpoint(current)
                events.append({'mutation': 'Z_DOMAIN', 'status': 'PASS',
                               'depends_on': sorted(finished)})
                finished.add(key)
                continue
            if key not in changes or key in finished:
                raise ValueError('INVALID_MUTATION_SCHEDULE')
            prev = state_identity(current)
            current = apply_local(current, changes[key])
            events.append({'mutation': key, 'path': list(changes[key]['path']),
                           'before': changes[key]['before'], 'after': changes[key]['after'],
                           'dependency_footprint': changes[key]['dependency_footprint'],
                           'state_before': prev, 'state_after': state_identity(current)})
            finished.add(key)
        if finished != set(changes) | {'Z_DOMAIN'}:
            raise ValueError('CHECKPOINT_INCOMPLETE')
        return current, events

    result, events = execute(chosen_order)
    canonical, _ = execute(tuple(sorted(changes)) + ('Z_DOMAIN',))
    if state_identity(result) != state_identity(canonical):
        raise AssertionError('ORDER_ALTERED_CLOSED_CHECKPOINT')
    proof = {'tier': tier, 'state_before': before_hash,
             'order': list(chosen_order), 'changes': [changes[k] for k in sorted(changes)],
             'steps': events, 'checkpoint_sha256': state_identity(result),
             'checkpoint_closed': True, 'commutes_with_canonical_order': True}
    return result, proof


def descend(st: dict) -> tuple[dict, dict]:
    """One local tier transition. No higher-tier reparse or oracle call."""
    check_checkpoint(st)
    tier = st['tier']
    if tier == 0:
        raise ValueError('T00_HAS_NO_LOWER_TIER')
    nxt = deepcopy(st); f = nxt['facts']; ops = []
    if tier == 8:
        ops.append({'operator': 'CONSUME_TYPED_OBSERVATIONS'})
    elif tier == 7:
        nxt['rule'] = 'SCALAR:' + f['formula_id'] if f['kind'] == 'scalar' else 'MATRIX:' + f['mode']
        ops.append({'operator': 'SELECT_NATIVE_RULE', 'rule': nxt['rule']})
    elif tier == 6:
        a, b, op = native._top(f)
        nxt['expressions'] = {'X': ast.unparse(a) if isinstance(a, ast.AST) else a,
                              'Y': ast.unparse(b) if isinstance(b, ast.AST) else b,
                              'OP': op}
        nxt['OP'] = op
        ops.append({'operator': 'CONSTRUCT_SYMBOLIC_OPERANDS'})
    elif tier == 5:
        ops.append({'operator': 'BIND_NATIVE_OPERANDS'})
    elif tier == 4:
        nxt['Y'], steps = native._one_operand(f, 'Y')
        ops.extend(steps)
    elif tier == 3:
        nxt['X'], steps = native._one_operand(f, 'X')
        ops.extend(steps)
    elif tier == 2:
        if nxt['X'] is None or nxt['Y'] is None:
            raise ValueError('MISSING_NUMERICAL_OPERANDS')
        ops.append({'operator': 'BIND_REGISTERS'})
    elif tier == 1:
        ops.append({'operator': 'EXPOSE_OPERATION', 'OP': nxt['OP']})
    nxt['tier'] = tier - 1
    nxt['prompt'] = native._prompt(nxt, tier - 1)
    check_checkpoint(nxt)
    return nxt, {'from': tier, 'to': tier - 1, 'operator_steps': ops,
                 'state_before': state_identity(st), 'state_after': state_identity(nxt)}


def coupled_t00(st: dict, verify: bool = True) -> dict:
    if st['tier'] != 0:
        raise ValueError('T00_REQUIRED')
    check_checkpoint(st)
    x, y, op = st['X'], st['Y'], st['OP']
    if op == 'ARGMAX':
        z = native.mathlib.execute(op, [x, y])
        semantics = st['facts']['row_actions'][z]
    else:
        z = native.mathlib.execute(op, x, y)
        semantics = ('scalar_value' if st['facts']['kind'] == 'scalar'
                     else 'mixed_strategy_probability')
    answer = {'X': native.mathlib.out(x), 'Y': native.mathlib.out(y),
              'OP': op, 'Z': native.mathlib.out(z), 'Z_semantics': semantics,
              'objective': st['facts']['mode'], 'game_id': st['facts']['game_id']}
    if verify:
        # The independent oracle is invoked ONLY after the T00 code runner.
        oracle = native.finish(st)
        if answer != oracle:
            raise AssertionError('T00_INDEPENDENT_ORACLE_DISAGREEMENT')
    return answer


def run(prompt: str, *, mutation_seed: int, order_seed: int,
        inputs: dict | None = None, verify: bool = True) -> dict:
    st = begin(prompt, inputs=inputs)
    input_sha = native.sha(st['prompt'])
    checkpoints = []
    transitions = []
    for tier in range(8, -1, -1):
        if st['tier'] != tier:
            raise AssertionError('TIER_SKIPPED')
        st, cp = execute_checkpoint(st, order_seed=order_seed,
                                     mutation_seed=mutation_seed, input_id=input_sha)
        checkpoints.append(cp)
        if tier:
            st, edge = descend(st)
            cp['downward_state_sha256'] = edge['state_after']
            transitions.append(edge)
    answer = coupled_t00(st, verify=verify)
    return {'version': VERSION, 'input_sha256': input_sha,
            'mutation_seed': mutation_seed, 'order_seed': order_seed,
            'game_id': answer['game_id'], 'objective': answer['objective'],
            'final': answer, 'canonical_T00': st['prompt'],
            'checkpoints': checkpoints, 'transitions': transitions,
            'closed_checkpoints': 9, 'transition_count': 8,
            'T00_executed': True, 'independent_oracle_verified': verify,
            'restarted_from_T08_during_walk': False, 'trained_model': False}


def audit_seed_contract(prompt: str, *, mutation_seed: int = 17,
                        order_seeds: tuple[int, int] = (11, 29)) -> dict:
    a = run(prompt, mutation_seed=mutation_seed, order_seed=order_seeds[0])
    b = run(prompt, mutation_seed=mutation_seed, order_seed=order_seeds[1])
    if a['final'] != b['final']:
        raise AssertionError('ORDER_SEED_CHANGED_FINAL')
    if [p['checkpoint_sha256'] for p in a['checkpoints']] != [p['checkpoint_sha256'] for p in b['checkpoints']]:
        raise AssertionError('ORDER_SEED_CHANGED_CHECKPOINT')
    if a != run(prompt, mutation_seed=mutation_seed, order_seed=order_seeds[0]):
        raise AssertionError('REPLAY_NONDETERMINISTIC')
    return {'status': 'PASS', 'same_final': True, 'same_checkpoint_states': True,
            'replay_exact': True, 'order_variants': sum(
                x['order'] != y['order'] for x, y in zip(a['checkpoints'], b['checkpoints']))}


def export_proof_bank(conn: sqlite3.Connection, path: Path, seed: int) -> dict:
    # One real native-v6 T08 per executable game, two seeded realizations.
    rows = conn.execute('''SELECT c.game_id,t.prompt FROM native_v6_cases c
        JOIN native_v6_tiers t ON t.case_id=c.case_id AND t.tier=8
        ORDER BY c.game_id,c.case_id''').fetchall()
    prompts = {}
    for gid, prompt in rows:
        prompts.setdefault(gid, prompt)
    if len(prompts) != 33:
        raise ValueError('EXPECTED_33_EXECUTABLE_NATIVE_GAMES')
    # Transaction completion alone does not close SQLite connections on Windows.
    # Close before the source smoke promotes or cleans up its staging directory.
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript('''PRAGMA foreign_keys=ON;
            CREATE TABLE deterministic_v8_runs(
                run_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
                input_sha256 TEXT NOT NULL, mutation_seed INTEGER NOT NULL,
                order_seed INTEGER NOT NULL, answer_json TEXT NOT NULL,
                T00_text TEXT NOT NULL, oracle_verified INTEGER NOT NULL CHECK(oracle_verified=1));
            CREATE TABLE deterministic_v8_checkpoints(
                run_id TEXT NOT NULL REFERENCES deterministic_v8_runs(run_id),
                tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 8),
                checkpoint_hash TEXT NOT NULL, schedule_json TEXT NOT NULL,
                mutations_json TEXT NOT NULL, trace_json TEXT NOT NULL,
                PRIMARY KEY(run_id,tier));''')
        total, order_changes, distinct_answers = 0, 0, 0
        for gid, prompt in sorted(prompts.items()):
            variants = []
            for i in (0, 1):
                result = run(prompt, order_seed=seed+101+i, mutation_seed=seed+211+i)
                variants.append(result)
                rid = native.sha([gid, result['input_sha256'], result['order_seed'], result['mutation_seed']])
                db.execute('INSERT INTO deterministic_v8_runs VALUES (?,?,?,?,?,?,?,?)',
                           (rid, gid, result['input_sha256'], result['mutation_seed'],
                            result['order_seed'], native.canon(result['final']),
                            result['canonical_T00'], 1))
                for cp in result['checkpoints']:
                    db.execute('INSERT INTO deterministic_v8_checkpoints VALUES (?,?,?,?,?,?)',
                               (rid, cp['tier'], cp['checkpoint_sha256'], native.canon(cp['order']),
                                native.canon(cp['changes']), native.canon(cp['steps'])))
                total += 1
            order_changes += sum(a['order'] != b['order'] for a, b in zip(
                variants[0]['checkpoints'], variants[1]['checkpoints']))
            distinct_answers += variants[0]['final']['Z'] != variants[1]['final']['Z']
        db.commit()
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise AssertionError('BROKEN_V8_FOREIGN_KEYS')
        if db.execute('SELECT COUNT(*) FROM deterministic_v8_checkpoints').fetchone()[0] != 9 * total:
            raise AssertionError('INCOMPLETE_V8_CHECKPOINT_EXPORT')
    return {'status': 'PASS', 'games': len(prompts), 'runs': total,
            'closed_checkpoints': 9 * total, 'order_trace_variations': order_changes,
            'distinct_Z_pairs': distinct_answers, 'restart_from_T08': False,
            'T00_coupled': True, 'oracle_only_after_T00': True,
            'catalog_only_gold_created': False, 'model_trained': False,
            'proof_scope': 'native_v6_T08_grammar_level_local_seeded_source_mutations'}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--database', type=Path, help='Native-v6 cases SQLite; optionally --case')
    source.add_argument('--prompt-file', type=Path, help='T08 question text')
    case_choice = p.add_mutually_exclusive_group()
    case_choice.add_argument('--case', help='native_v6_cases.case_id (default first case)')
    case_choice.add_argument('--game-id', help='Select first executable native T08 for game_id')
    p.add_argument('--inputs', type=Path, help='JSON file with public parameter or matrix bindings')
    p.add_argument('--mutation-seed', type=int, default=17)
    p.add_argument('--order-seed', type=int, default=11)
    p.add_argument('--trace', action='store_true', help='Print full checkpoints and transitions')
    p.add_argument('--verify', dest='verify', action='store_true', default=True, help='Independently verify after T00 (default)')
    p.add_argument('--no-verify', dest='verify', action='store_false', help='Skip post-T00 oracle check')
    a = p.parse_args(argv)
    if a.prompt_file:
        if a.case or a.game_id:
            p.error('--case/--game-id requires --database')
        prompt = a.prompt_file.read_text(encoding='utf-8')
        if prompt.endswith('\n'):
            prompt = prompt[:-1]
    else:
        with sqlite3.connect(a.database) as db:
            if a.case:
                row = db.execute('SELECT prompt FROM native_v6_tiers WHERE tier=8 AND case_id=?',
                                 (a.case,)).fetchone()
            elif a.game_id:
                row = db.execute('SELECT t.prompt FROM native_v6_tiers t JOIN native_v6_cases c USING(case_id) WHERE t.tier=8 AND c.game_id=? ORDER BY c.case_id LIMIT 1',
                                 (a.game_id,)).fetchone()
            else:
                row = db.execute('SELECT prompt FROM native_v6_tiers WHERE tier=8 ORDER BY case_id LIMIT 1').fetchone()
        if row is None:
            raise ValueError('NATIVE_T08_CASE_NOT_FOUND')
        prompt = row[0]
    bindings = json.loads(a.inputs.read_text(encoding='utf-8')) if a.inputs else None
    result = run(prompt, mutation_seed=a.mutation_seed, order_seed=a.order_seed,
                 inputs=bindings, verify=a.verify)
    view = result if a.trace else {k: v for k, v in result.items() if k not in ('checkpoints', 'transitions')}
    if not a.trace:
        view['mutation_orders'] = {f'T{cp["tier"]:02}': cp['order'] for cp in result['checkpoints']}
        view['checkpoint_hashes'] = {f'T{cp["tier"]:02}': cp['checkpoint_sha256'] for cp in result['checkpoints']}
    print(json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
