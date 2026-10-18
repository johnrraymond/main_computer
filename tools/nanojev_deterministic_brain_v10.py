"""V10 cumulative, commuting X/Y *presentation* transformations.

Each mathematical tier has its own assistance lattice. X-first and Y-first
paths for LogBar removal and register removal must reach an identical prompt.
All tier presentations share ONE immutable, independently verified snapshot.
Source-game mutations are not mislabeled operand-presentation mutations.

This generator is CPU-only, has no model dependency, and does not alter V8/V9.
"""
from __future__ import annotations

from copy import deepcopy
import ast
from fractions import Fraction
import hashlib
import json
import math
from typing import Any

from . import nanojev_deterministic_brain_v8 as brain8
from . import nanojev_game_native_objectives_v6 as native

VERSION = 'nanojev.deterministic-brain.cumulative-xy-operand-audited.v10.2'
TIERS = tuple(f'T{i:02}' for i in range(9))
FIRST, SECOND = 'XXXXXX', 'YYYYYYY'  # preserve native V6/V9 register spelling

# Mutation sets, not a list of replacement prompts. Every node encodes the
# closure of inherited transformations, and each edge adds ONE mutation.
MUTATIONS = ('drop_x_logbar', 'drop_y_logbar',
             'drop_x_register', 'drop_y_register')
MUTATION_CLASSES = {
    'drop_x_logbar': ('logbar','X','Y presentation and X/Y/OP/Z truth'),
    'drop_y_logbar': ('logbar','Y','X presentation and X/Y/OP/Z truth'),
    'drop_x_register': ('register','X','Y presentation and X/Y/OP/Z truth'),
    'drop_y_register': ('register','Y','X presentation and X/Y/OP/Z truth'),
    'advance_math_tier_and_restore_assistance': (
        'math_tier','BOTH','same frozen mathematical truth; assistance deliberately reset'),
}
# Complete 3x3 product: each operand has FULL → NO_LOGBAR → RAW.
# Unlike a seven-node ladder, this permits completing all X mutations while
# leaving Y *fully scaffolded*, and vice versa.
NODES = {
    'full': (),                                       # (X0,Y0)
    'logbar_x': ('drop_x_logbar',),                    # (X1,Y0)
    'logbar_y': ('drop_y_logbar',),                    # (X0,Y1)
    'no_logbars': ('drop_x_logbar','drop_y_logbar'),   # (X1,Y1)
    'x_raw_y_full': ('drop_x_logbar','drop_x_register'), # (X2,Y0)
    'y_raw_x_full': ('drop_y_logbar','drop_y_register'), # (X0,Y2)
    'register_x': ('drop_x_logbar','drop_y_logbar','drop_x_register'), # (X2,Y1)
    'register_y': ('drop_x_logbar','drop_y_logbar','drop_y_register'), # (X1,Y2)
    'raw': MUTATIONS,                                 # (X2,Y2)
}
EDGES = (
    ('full', 'logbar_x', 'drop_x_logbar'),
    ('full', 'logbar_y', 'drop_y_logbar'),
    ('logbar_x', 'no_logbars', 'drop_y_logbar'),
    ('logbar_y', 'no_logbars', 'drop_x_logbar'),
    ('logbar_x', 'x_raw_y_full', 'drop_x_register'),
    ('logbar_y', 'y_raw_x_full', 'drop_y_register'),
    ('x_raw_y_full', 'register_x', 'drop_y_logbar'),
    ('y_raw_x_full', 'register_y', 'drop_x_logbar'),
    ('no_logbars', 'register_x', 'drop_x_register'),
    ('no_logbars', 'register_y', 'drop_y_register'),
    ('register_x', 'raw', 'drop_y_register'),
    ('register_y', 'raw', 'drop_x_register'),
)
# Four atomic commuting squares and one *whole-side* X-first/Y-first proof.
DIAMONDS = (
    ('full','logbar_x','logbar_y','no_logbars','drop_x_logbar','drop_y_logbar'),
    ('logbar_x','x_raw_y_full','no_logbars','register_x','drop_x_register','drop_y_logbar'),
    ('logbar_y','no_logbars','y_raw_x_full','register_y','drop_x_logbar','drop_y_register'),
    ('no_logbars','register_x','register_y','raw','drop_x_register','drop_y_register'),
)
WHOLE_SIDE_PATHS = (
    ('drop_x_logbar','drop_x_register','drop_y_logbar','drop_y_register'),
    ('drop_y_logbar','drop_y_register','drop_x_logbar','drop_x_register'),
)


def canon(v: Any) -> str:
    return native.canon(v)


def digest(v: Any) -> str:
    return hashlib.sha256(canon(v).encode('utf-8')).hexdigest()


def _value(v: Any) -> Fraction:
    if isinstance(v, dict) and set(v) == {'numerator', 'denominator'}:
        return Fraction(v['numerator'], v['denominator'])
    return native.num(v)


def logbar(v: Any) -> str:
    """Signed log1p tenths encoding, generalized to lossless rational targets.

    V3's int-only `logbar` silently truncates fractions. V10 makes NO int cast:
    the exact value is separately printed and this auxiliary is explicitly
    quantized. For integer values within ordinary range it matches V3.
    """
    n = float(_value(v))
    if not math.isfinite(n):
        raise ValueError('NONFINITE_LOGBAR_VALUE')
    q = int(math.floor(math.log1p(abs(n)) * 10 + 0.5))
    major, remainder = divmod(q, 10)
    if major > 200:
        raise ValueError('LOGBAR_OUT_OF_SUPPORTED_DISPLAY_RANGE')
    return ('-' if n < 0 else '') + '|' * major + '.' + '|' * remainder


def display_number(v: Any) -> str:
    # V8 labels are exact rationals; use V6 rendering to match input grammar.
    return native.fmt(_value(v))


def mutation_sets_valid() -> None:
    if set(MUTATION_CLASSES) != set(MUTATIONS) | {'advance_math_tier_and_restore_assistance'}:
        raise AssertionError('MUTATION_CLASS_REGISTRY_INCOMPLETE')
    if set(MUTATIONS) != set().union(*(set(x) for x in NODES.values())):
        raise AssertionError('MUTATION_UNIVERSE_INCOMPLETE')
    for parent, child, op in EDGES:
        if set(NODES[child]) != set(NODES[parent]) | {op} or op in NODES[parent]:
            raise AssertionError('NONATOMIC_OR_NONCUMULATIVE_EDGE')
    if NODES['raw'] != MUTATIONS:
        raise AssertionError('FINAL_NODE_NOT_FULL_MUTATION_CLOSURE')


def freeze(t08: str, *, mutation_seed: int, order_seed: int) -> dict:
    """Mutate the source ONCE, then descend all levels on the SAME facts.

    Reuses V8 closed-source-mutation execution and final independent oracle,
    but eliminates V9's source change between learner-facing tier snapshots.
    """
    st = brain8.begin(t08)
    input_sha = native.sha(st['prompt'])
    st, source_proof = brain8.execute_checkpoint(
        st, mutation_seed=mutation_seed, order_seed=order_seed, input_id=input_sha)
    snapshot = deepcopy(st['facts'])
    tier_prompts = {8: st['prompt']}
    descent_proofs = []
    while st['tier']:
        st, edge = brain8.descend(st)
        if st['facts'] != snapshot:
            raise AssertionError('SNAPSHOT_CHANGED_ON_DESCENT')
        tier_prompts[st['tier']] = st['prompt']
        descent_proofs.append(edge)
    answer = brain8.coupled_t00(st, verify=True)
    # Independently prove the X/Y values are those of the frozen source.
    x, _ = native._one_operand(snapshot, 'X')
    y, _ = native._one_operand(snapshot, 'Y')
    if native.mathlib.out(x) != answer['X'] or native.mathlib.out(y) != answer['Y']:
        raise AssertionError('FROZEN_SNAPSHOT_TARGET_MISMATCH')
    return {
        'version': VERSION, 'input_sha256': input_sha,
        'mutation_seed': mutation_seed, 'order_seed': order_seed,
        'snapshot': snapshot, 'snapshot_sha256': digest(snapshot),
        'answer': answer, 'answer_sha256': digest(answer),
        'native_prompts': {f'T{tier:02}': tier_prompts[tier] for tier in range(9)},
        'source_proof': source_proof,
        'source_proof_sha256': digest(source_proof),
        'descent_proofs': descent_proofs,
        'oracle_verified': True,
    }


def operand_formulas(snapshot: dict) -> dict[str, str]:
    """Derive X and Y from the native game oracle's *symbolic* definitions.

    No training labels or computed operand values are used to construct these
    expressions. Mixed Nash expressions follow native._mixed's conventions.
    """
    f = snapshot
    if f['kind'] == 'scalar':
        x, y, _ = native._top(f)
        return {'X': ast.unparse(x), 'Y': ast.unparse(y)}
    if f['mode'] == 'best_response':
        return {'X': 'U[0][selector][0]', 'Y': 'U[1][selector][0]'}
    if f['mode'] == 'mixed_row':
        return {
            'X': 'U[1][1][1] - U[1][0][1]',
            'Y': 'U[0][0][1] - U[1][0][1] - U[0][1][1] + U[1][1][1]',
        }
    if f['mode'] == 'mixed_col':
        return {
            'X': 'U[1][1][0] - U[0][1][0]',
            'Y': 'U[0][0][0] - U[0][1][0] - U[1][0][0] + U[1][1][0]',
        }
    raise ValueError('UNKNOWN_MATRIX_OBJECTIVE')


def evaluate_operand_formula(expression: str, snapshot: dict) -> Fraction:
    """Evaluate the literal printed expression using only supplied GAME_FACTS.

    Tiny whitelisted expression interpreter: no Python eval, attribute access,
    comprehensions, free function calls, or access to target labels.
    """
    f = snapshot
    environment = (f['parameters'] if f['kind'] == 'scalar' else
                   {'U': f['U'], 'selector': f.get('selector')})
    def evaluate(node):
        if isinstance(node, ast.Name) and node.id in environment:
            return environment[node.id]
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return native.num(node.value)
        if isinstance(node, ast.Subscript):
            seq = evaluate(node.value)
            index = evaluate(node.slice)
            if not isinstance(seq, (tuple, list)) or index not in (0, 1):
                raise ValueError('UNSUPPORTED_OR_OUT_OF_RANGE_INDEX')
            return seq[int(index)]
        if isinstance(node, ast.BinOp) and type(node.op) in (
                ast.Add, ast.Sub, ast.Mult, ast.Div):
            a, b = native.num(evaluate(node.left)), native.num(evaluate(node.right))
            if isinstance(node.op, ast.Add): return a + b
            if isinstance(node.op, ast.Sub): return a - b
            if isinstance(node.op, ast.Mult): return a * b
            return a / b
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -native.num(evaluate(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == 'max' and len(node.args) == 2
                and not node.keywords):
            return max(native.num(evaluate(node.args[0])),
                       native.num(evaluate(node.args[1])))
        raise ValueError('UNSUPPORTED_OPERAND_EXPRESSION:' + ast.dump(node))
    return native.num(evaluate(ast.parse(expression, mode='eval').body))


def prove_operand_formulas(frozen: dict) -> dict[str, dict]:
    """Every X/Y expression must be computable from prompt facts, not labels."""
    formulas = operand_formulas(frozen['snapshot'])
    result = {}
    for side in ('X', 'Y'):
        calculated = evaluate_operand_formula(formulas[side], frozen['snapshot'])
        oracle, _ = native._one_operand(frozen['snapshot'], side)
        if calculated != oracle or native.mathlib.out(calculated) != frozen['answer'][side]:
            raise AssertionError('OPERAND_DERIVATION_ORACLE_MISMATCH:' + side)
        result[side] = {'expression': formulas[side],
                        'expression_sha256': digest(formulas[side]),
                        'evaluation_sha256': digest(native.mathlib.out(calculated))}
    return result


def _core(frozen: dict, tier: str, removed: frozenset[str]) -> str:
    """Rebuild the task while keeping all omitted operands independently solvable.

    T00-T02 are numeric presentation exercises. T03-T08 must expose a
    machine-evaluable formula for *each* removed side (never its label), with
    the source facts needed to execute it. Native anchors remain byte-identical
    when both registers are present; existing grammar bridges are preserved.
    """
    if tier not in TIERS:
        raise ValueError('UNKNOWN_TIER')
    ans = frozen['answer']
    if tier in ('T00', 'T01', 'T02'):
        left = display_number(ans['X']) if 'drop_x_register' in removed else FIRST
        right = display_number(ans['Y']) if 'drop_y_register' in removed else SECOND
        if tier == 'T00':
            return f'OP: {ans["OP"]}\nCompute {ans["OP"]} on {left} and {right}.\nAnswer:'
        if tier == 'T01':
            return f'Compute {ans["OP"]} on {left} and {right}.\nAnswer:'
        return f'Compute {ans["OP"]} using {left} and {right}.\nAnswer:'
    hide_x = 'drop_x_register' in removed
    hide_y = 'drop_y_register' in removed
    if not (hide_x or hide_y):
        return frozen['native_prompts'][tier]
    f = frozen['snapshot']
    formulas = operand_formulas(f)
    expression_x = formulas['X']
    expression_y = formulas['Y']
    facts = 'GAME_FACTS: ' + canon(f)
    if tier == 'T03':
        # The original native T03 has X symbolic and Y as an *inline number*.
        # Remove that number when removing Y's register. No other hidden copies.
        x_operand = canon(expression_x if hide_x else native._top(f)[0]
                         if isinstance(native._top(f)[0], str) else ast.unparse(native._top(f)[0]))
        y_operand = canon(expression_y) if hide_y else display_number(ans['Y'])
        task = f'COMPUTE {ans["OP"]} WITH {x_operand} AND {y_operand}\n{facts}'
    elif tier in ('T04', 'T05'):
        original = native._top(f)
        operands = {'X': expression_x if hide_x else (
                        ast.unparse(original[0]) if isinstance(original[0], ast.AST) else original[0]),
                    'Y': expression_y if hide_y else (
                        ast.unparse(original[1]) if isinstance(original[1], ast.AST) else original[1]),
                    'OP': ans['OP']}
        if tier == 'T04':
            task = 'COMPUTE ' + ans['OP'] + ' WITH ' + canon(operands) + '\n' + facts
        else:
            rule = ('SCALAR:'+f['formula_id'] if f['kind']=='scalar'
                    else 'MATRIX:'+f['mode'])
            task = ('OPERANDS: '+canon(operands)+'\nRULE: '+rule+'\n'+facts)
    elif tier in ('T06', 'T07'):
        task = frozen['native_prompts'][tier]
    else:  # T08: keep source story, provide the missing mathematical rule.
        task = frozen['native_prompts'][tier]
    # For T03-T05 the instruction already includes the mathematical expressions;
    # for T06-T08 state the equation explicitly, while retaining the unmodified
    # source game facts or story. Both register-removal orders commute exactly.
    if tier in ('T06', 'T07', 'T08'):
        defs = []
        if hide_x: defs.append('first operand = ' + expression_x)
        if hide_y: defs.append('second operand = ' + expression_y)
        explanation = ('Compute the requested result using ' + ans['OP'] +
                       ' on these game-derived operands: ' + '; '.join(defs) + '.')
        task += '\nMATHEMATICAL_RULE: ' + explanation
        if tier == 'T08' and f['kind'] == 'matrix':
            task += ('\nPAYOFF_INDEXING: U[row action index][rival action index]'
                     '[player], where indices are zero-based, player 0 is you'
                     ' and player 1 is the rival. For best response, selector'
                     ' is the index of the rival action chosen in the story.')
        # T08 is a natural-language game with an explicit mathematical rule.
        # Never append a machine-readable GAME_FACTS JSON block to the story.
    elif f['kind'] == 'matrix':
        task += ('\nPAYOFF_INDEXING: U[row action][rival column action]'
                 '[player], where player 0 is you and player 1 is the rival.')
    return task

def _render(frozen: dict, tier: str, applied: frozenset[str]) -> str:
    if not applied <= set(MUTATIONS):
        raise ValueError('UNKNOWN_PRESENTATION_MUTATION')
    if 'drop_x_register' in applied and 'drop_x_logbar' not in applied:
        raise ValueError('REMOVE_X_LOGBAR_BEFORE_REGISTER')
    if 'drop_y_register' in applied and 'drop_y_logbar' not in applied:
        raise ValueError('REMOVE_Y_LOGBAR_BEFORE_REGISTER')
    fields = []
    for side, label in (('x', FIRST), ('y', SECOND)):
        if f'drop_{side}_register' in applied:
            continue
        n = display_number(frozen['answer'][side.upper()])
        if f'drop_{side}_logbar' not in applied:
            n += ', ' + logbar(frozen['answer'][side.upper()])
        fields.append(label + ' = ' + n)
    task = _core(frozen, tier, applied)
    return ('\n'.join(fields) + '\n' if fields else '') + task


def render(frozen: dict, tier: str, node: str) -> str:
    if node not in NODES:
        raise ValueError('UNKNOWN_ASSISTANCE_NODE')
    return _render(frozen, tier, frozenset(NODES[node]))


def apply(frozen: dict, tier: str, applied: frozenset[str], operation: str) -> tuple[frozenset[str], str]:
    """Commutative presentation-state transition, not a text regex rewrite."""
    if operation not in MUTATIONS or operation in applied:
        raise ValueError('MUTATION_NOT_NEW')
    nxt = applied | {operation}
    return nxt, _render(frozen, tier, nxt)


def prove_tier(frozen: dict, tier: str) -> dict:
    mutation_sets_valid()
    if not frozen.get('oracle_verified'):
        raise ValueError('UNVERIFIED_MATH_SNAPSHOT')
    if digest(frozen['snapshot']) != frozen['snapshot_sha256'] or digest(frozen['answer']) != frozen['answer_sha256']:
        raise ValueError('MATHEMATICAL_SNAPSHOT_TAMPERED')
    operand_proof = prove_operand_formulas(frozen)
    presented = {node: render(frozen, tier, node) for node in NODES}
    if tier in ('T03','T04','T05','T06','T07','T08'):
        for node, mutations in NODES.items():
            for side in ('X','Y'):
                if 'drop_'+side.lower()+'_register' in mutations:
                    if operand_proof[side]['expression'] not in presented[node]:
                        raise AssertionError('HIDDEN_OPERAND_FORMULA_MISSING:'+tier+'/'+node+'/'+side)
            if tier == 'T03' and 'drop_y_register' in mutations:
                if (' AND '+display_number(frozen['answer']['Y'])+'\n') in presented[node]:
                    raise AssertionError('T03_Y_INLINE_TARGET_LEAKAGE')
    if len(set(presented.values())) != len(presented):
        raise AssertionError('NOOP_OR_DUPLICATE_TRANSFORMATION')
    for parent, child, op in EDGES:
        new, content = apply(frozen, tier, frozenset(NODES[parent]), op)
        if new != frozenset(NODES[child]) or content != presented[child]:
            raise AssertionError('EDGE_DOES_NOT_REPLAY')
    diamonds = []
    for parent, after_x, after_y, merge, xop, yop in DIAMONDS:
        before = frozenset(NODES[parent])
        s_x, rendered_x = apply(frozen, tier, before, xop)
        s_xy, rendered_xy = apply(frozen, tier, s_x, yop)
        s_y, rendered_y = apply(frozen, tier, before, yop)
        s_yx, rendered_yx = apply(frozen, tier, s_y, xop)
        if s_xy != s_yx or rendered_xy != rendered_yx or rendered_xy != presented[merge]:
            raise AssertionError('XY_ORDER_PATHS_NOT_EQUIVALENT')
        # The other operand's complete scaffold field is byte-identical to
        # its parent field. This also catches accidental both-side edits.
        def field(side: str, state: frozenset[str]) -> str:
            if f'drop_{side}_register' in state:
                return ''
            label = FIRST if side == 'x' else SECOND
            value = display_number(frozen['answer'][side.upper()])
            if f'drop_{side}_logbar' not in state:
                value += ', ' + logbar(frozen['answer'][side.upper()])
            return label + ' = ' + value
        xside = xop.split('_')[1]
        yside = yop.split('_')[1]
        if (xside == yside or
                field(yside, before) != field(yside, s_x) or
                field(xside, before) != field(xside, s_y)):
            raise AssertionError('SINGLE_SIDE_MUTATION_CHANGED_OTHER_SIDE')
        diamonds.append({'parent': parent, 'x_first': after_x,
                         'y_first': after_y, 'merge': merge,
                         'x_mutation': xop, 'y_mutation': yop,
                         'merged_prompt_sha256': digest(rendered_xy),
                         'paths_equivalent': True})
    whole_side = []
    for path in WHOLE_SIDE_PATHS:
        state = frozenset()
        for op in path:
            state, content = apply(frozen,tier,state,op)
        if state != frozenset(MUTATIONS) or content != presented['raw']:
            raise AssertionError('FULL_X_FIRST_Y_FIRST_PATHS_DIFFER')
        whole_side.append({'operations': list(path), 'merged_prompt_sha256':digest(content)})
    if whole_side[0]['merged_prompt_sha256'] != whole_side[1]['merged_prompt_sha256']:
        raise AssertionError('WHOLE_SIDE_MUTATION_CLASSES_NOT_EQUIVALENT')
    if tier == 'T08' and not presented['raw'].startswith(native.render08(frozen['snapshot'])+'\nMATHEMATICAL_RULE: '):
        raise AssertionError('T08_RAW_NOT_GROUNDED_STORY')
    if FIRST in presented['raw'] or SECOND in presented['raw'] or 'LogBar' in presented['raw']:
        raise AssertionError('RAW_STILL_HAS_SCAFFOLDING')
    return {'tier': tier, 'prompts': presented,
            'prompt_hashes': {k: digest(v) for k, v in presented.items()},
            'mutation_sets': {k: list(v) for k, v in NODES.items()},
            'diamonds': diamonds, 'whole_side_paths': whole_side,
            'snapshot_sha256': frozen['snapshot_sha256'],
            'answer_sha256': frozen['answer_sha256'],
            'operand_derivation_proof': operand_proof}



# Curriculum bridge from the actual V8/V9 prompt grammar to the V10 lattice.
# Stages are deliberately distinct from the nine assistance states: the bridge
# ADDS scaffolding; the established lattice REMOVES it. Separate node IDs keep
# the directed stage graph acyclic even where two prompts are identical.
BRIDGE_NODES = {
    'T00': ('v9_anchor','grammar_equals_x','grammar_equals_y','grammar_equals','grammar_compact','grammar_compute',
            'grammar_answer','bridge_logbar_x','bridge_logbar_y'),
    'T01': ('v9_anchor','grammar_equals_x','grammar_equals_y','grammar_equals','grammar_compact','grammar_answer',
            'bridge_logbar_x','bridge_logbar_y'),
    'T02': ('v9_anchor','grammar_answer','bridge_register_x',
            'bridge_register_y','bridge_registered','bridge_logbar_x','bridge_logbar_y'),
    **{f'T{n:02}': ('v9_anchor','bridge_register_x','bridge_register_y',
                  'bridge_registered','bridge_logbar_x','bridge_logbar_y')
       for n in range(3, 9)},
}
BRIDGE_CLASSES = {
    'grammar_field_equals_x': ('grammar','X','only X register punctuation'),
    'grammar_field_equals_y': ('grammar','Y','only Y register punctuation'),
    'grammar_compact_whitespace': ('grammar','BOTH','only blank-line removal'),
    'grammar_add_compute': ('grammar','BOTH','only one explicit compute instruction'),
    'grammar_answer_marker': ('grammar','BOTH','only response marker'),
    'grammar_append_answer': ('grammar','BOTH','only response marker appended'),
    'bridge_add_x_register': ('register','X','mathematical answers and Y assistance unchanged'),
    'bridge_add_y_register': ('register','Y','mathematical answers and X assistance unchanged'),
    'bridge_add_x_logbar': ('logbar','X','mathematical answers and Y assistance unchanged'),
    'bridge_add_y_logbar': ('logbar','Y','mathematical answers and X assistance unchanged'),
}


def bridge_edges(tier: str) -> tuple[tuple[str,str,str], ...]:
    """Directed, one-change edges, with explicit X/Y commuting diamonds."""
    if tier == 'T00':
        prefix = [('v9_anchor','grammar_equals_x','grammar_field_equals_x'),
                  ('v9_anchor','grammar_equals_y','grammar_field_equals_y'),
                  ('grammar_equals_x','grammar_equals','grammar_field_equals_y'),
                  ('grammar_equals_y','grammar_equals','grammar_field_equals_x'),
                  ('grammar_equals','grammar_compact','grammar_compact_whitespace'),
                  ('grammar_compact','grammar_compute','grammar_add_compute'),
                  ('grammar_compute','grammar_answer','grammar_answer_marker')]
        base = 'grammar_answer'
    elif tier == 'T01':
        prefix = [('v9_anchor','grammar_equals_x','grammar_field_equals_x'),
                  ('v9_anchor','grammar_equals_y','grammar_field_equals_y'),
                  ('grammar_equals_x','grammar_equals','grammar_field_equals_y'),
                  ('grammar_equals_y','grammar_equals','grammar_field_equals_x'),
                  ('grammar_equals','grammar_compact','grammar_compact_whitespace'),
                  ('grammar_compact','grammar_answer','grammar_answer_marker')]
        base = 'grammar_answer'
    elif tier == 'T02':
        prefix = [('v9_anchor','grammar_answer','grammar_append_answer')]
        base = 'grammar_answer'
    elif tier in TIERS:
        prefix = []
        base = 'v9_anchor'
    else:
        raise ValueError('UNKNOWN_TIER')
    if tier not in ('T00','T01'):
        prefix.extend(((base,'bridge_register_x','bridge_add_x_register'),
                       (base,'bridge_register_y','bridge_add_y_register'),
                       ('bridge_register_x','bridge_registered','bridge_add_y_register'),
                       ('bridge_register_y','bridge_registered','bridge_add_x_register')))
        base = 'bridge_registered'
    return tuple(prefix + [
        (base,'bridge_logbar_x','bridge_add_x_logbar'),
        (base,'bridge_logbar_y','bridge_add_y_logbar'),
        ('bridge_logbar_x','full','bridge_add_y_logbar'),
        ('bridge_logbar_y','full','bridge_add_x_logbar'),
    ])


def bridge_prompts(frozen: dict, tier: str) -> dict[str,str]:
    """Produce exact legacy anchor and one-change steps to the existing full.

    This bridge is independent of the X/Y-removal lattice, and all grammar
    variants share the immutable mathematical answer of the same source case.
    """
    if tier not in TIERS:
        raise ValueError('UNKNOWN_TIER')
    original = frozen['native_prompts'][tier]
    x, y = (display_number(frozen['answer'][key]) for key in ('X','Y'))
    op = frozen['answer']['OP']
    # Note: anchor is byte-for-byte the original native V8/V9 grammar, not a
    # reconstruction using a new formatting convention.
    output = {'v9_anchor': original}
    if tier in ('T00','T01'):
        oldx,oldy = FIRST+': '+x, SECOND+': '+y
        if oldx not in original or oldy not in original:
            raise AssertionError('LEGACY_REGISTER_ANCHOR_NOT_FOUND')
        output['grammar_equals_x'] = original.replace(oldx,FIRST+' = '+x,1)
        output['grammar_equals_y'] = original.replace(oldy,SECOND+' = '+y,1)
        updated = output['grammar_equals_x'].replace(oldy,SECOND+' = '+y,1)
        if updated != output['grammar_equals_y'].replace(oldx,FIRST+' = '+x,1):
            raise AssertionError('GRAMMAR_PUNCTUATION_NONCOMMUTATIVE')
        output['grammar_equals']=updated
        compact = '\n'.join(line for line in updated.split('\n') if line)
        output['grammar_compact']=compact
        if tier=='T00':
            find='OP: '+op+'\nReturn the game-native result.'
            changed='OP: '+op+'\nCompute '+op+' on '+FIRST+' and '+SECOND+'.\nReturn the game-native result.'
            if find not in compact: raise AssertionError('T00_GRAMMAR_COMPUTE_PRECONDITION')
            compact=compact.replace(find,changed,1)
            output['grammar_compute']=compact
        end='Return the game-native result.' if tier=='T00' else 'Compute '+op+' on '+FIRST+' and '+SECOND+'.'
        if not compact.endswith(end):raise AssertionError('GRAMMAR_RESPONSE_PRECONDITION')
        output['grammar_answer']=(compact[:-len(end)]+'Answer:' if tier=='T00'
                                   else compact+'\nAnswer:')
        base=output['grammar_answer']
    else:
        if tier=='T02':
            output['grammar_answer']=original+'\nAnswer:'
            base=output['grammar_answer']
            xliteral,yliteral= x,y
            if base != f'Compute {op} using {xliteral} and {yliteral}.\nAnswer:':
                raise AssertionError('LEGACY_T02_GRAMMAR_DRIFT')
            body_x=f'Compute {op} using {FIRST} and {yliteral}.\nAnswer:'
            body_y=f'Compute {op} using {xliteral} and {SECOND}.\nAnswer:'
            body_both=f'Compute {op} using {FIRST} and {SECOND}.\nAnswer:'
        else:
            base=original
            body_x=body_y=body_both=base
        output['bridge_register_x']=FIRST+' = '+x+'\n'+body_x
        output['bridge_register_y']=SECOND+' = '+y+'\n'+body_y
        output['bridge_registered']=FIRST+' = '+x+'\n'+SECOND+' = '+y+'\n'+body_both
        base=output['bridge_registered']
    xhead=FIRST+' = '+x
    yhead=SECOND+' = '+y
    if not base.startswith(xhead+'\n'+yhead+'\n'):
        raise AssertionError('BRIDGE_REGISTER_ORDER_DRIFT')
    output['bridge_logbar_x']=base.replace(xhead, xhead+', '+logbar(frozen['answer']['X']), 1)
    output['bridge_logbar_y']=base.replace(yhead, yhead+', '+logbar(frozen['answer']['Y']), 1)
    if output['bridge_logbar_x'] != render(frozen,tier,'logbar_y'):
        raise AssertionError('BRIDGE_X_LOGBAR_NOT_LATTICE_EQUIVALENT')
    if output['bridge_logbar_y'] != render(frozen,tier,'logbar_x'):
        raise AssertionError('BRIDGE_Y_LOGBAR_NOT_LATTICE_EQUIVALENT')
    if (tier in ('T00','T01') and base != render(frozen,tier,'no_logbars')) or (
        tier == 'T02' and output['grammar_answer'] != render(frozen,tier,'raw')) or (
        tier not in ('T00','T01') and base != render(frozen,tier,'no_logbars')):
        raise AssertionError('BRIDGE_BASE_NOT_LATTICE_EQUIVALENT')
    return output


def prove_bridge(frozen: dict,tier: str) -> dict:
    prompts=bridge_prompts(frozen,tier)
    edges=bridge_edges(tier)
    if set(prompts)!=set(BRIDGE_NODES[tier]):
        raise AssertionError('BRIDGE_STAGE_SET_DRIFT')
    stages={**prompts,'full':render(frozen,tier,'full')}
    for a,b,op in edges:
        if a not in stages or b not in stages or stages[a]==stages[b]:
            raise AssertionError('BRIDGE_NOOP_OR_MISSING_EDGE:'+op)
    # Two paths must merge at identical *bytes*, including whitespace.
    if len([e for e in edges if e[1]=='full'])!=2:
        raise AssertionError('BRIDGE_FULL_MERGE_NOT_TWO_PATHS')
    # Directly independently construct target by adding the absent LogBar.
    lx=prompts['bridge_logbar_x']; ly=prompts['bridge_logbar_y']
    final=stages['full']
    if (lx.replace(SECOND+' = '+display_number(frozen['answer']['Y']),
                  SECOND+' = '+display_number(frozen['answer']['Y'])+', '+logbar(frozen['answer']['Y']),1)!=final or
        ly.replace(FIRST+' = '+display_number(frozen['answer']['X']),
                   FIRST+' = '+display_number(frozen['answer']['X'])+', '+logbar(frozen['answer']['X']),1)!=final):
        raise AssertionError('BRIDGE_XY_PATHS_DO_NOT_COMMUTE')
    if tier in ('T00','T01'):
        orig=prompts['v9_anchor']
        x=FIRST+': '+display_number(frozen['answer']['X'])
        y=SECOND+': '+display_number(frozen['answer']['Y'])
        if (prompts['grammar_equals_x'].replace(y,SECOND+' = '+display_number(frozen['answer']['Y']),1)
                != prompts['grammar_equals'] or
            prompts['grammar_equals_y'].replace(x,FIRST+' = '+display_number(frozen['answer']['X']),1)
                != prompts['grammar_equals']):
            raise AssertionError('GRAMMAR_REGISTER_PUNCTUATION_PATHS_DIVERGE')
    if tier not in ('T00','T01'):
        # Both orderings of register insertion also commute.
        if prompts['bridge_registered'] != render(frozen,tier,'no_logbars'):
            raise AssertionError('BRIDGE_REGISTER_XY_PATHS_DO_NOT_COMMUTE')
    return {'prompts':prompts,'prompt_hashes':{k:digest(v) for k,v in prompts.items()},
            'edges':edges,'answer_sha256':frozen['answer_sha256'],
            'merge_prompt_sha256':digest(final)}


def prove_all(frozen: dict) -> dict:
    by_tier = {tier: prove_tier(frozen, tier) for tier in TIERS}
    bridges = {tier: prove_bridge(frozen,tier) for tier in TIERS}
    return {'tiers': by_tier, 'bridges': bridges, 'level_count': len(by_tier),
            'presentation_count': len(TIERS) * len(NODES) + sum(len(v) for v in BRIDGE_NODES.values()),
            'commuting_diamonds': len(TIERS) * len(DIAMONDS),
            'equivalent_whole_side_pairs': len(TIERS),
            'snapshot_sha256': frozen['snapshot_sha256'],
            'answer_sha256': frozen['answer_sha256']}
