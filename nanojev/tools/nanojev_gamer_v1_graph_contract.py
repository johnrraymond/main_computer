"""GAMER graph contract. Game-agnostic, bounded exact executor.

No prompt parser, game identifier, fixed payoff matrix, or game-specific
algorithm appears in this module. A graph without all necessary learned
relationships is *not* executable.
"""
from __future__ import annotations

from fractions import Fraction

SCHEMA = 'nanojev.gamer.graph.v1'
NODE_KINDS = ('actor', 'action', 'quantity', 'condition', 'outcome',
              'state', 'operation', 'objective', 'other')
# These are structural primitives, not names of games or strategies.
SPAN_KINDS = ('actor', 'action', 'quantity', 'condition', 'outcome', 'state', 'other')
BINDINGS = ('owns', 'refers_to', 'measures', 'applies_to')
RELATIONS = ('causes', 'depends_on', 'constrains', 'changes', 'score', 'other')
OPERATORS = ('add', 'sub', 'mul', 'div', 'neg', 'min', 'max', 'abs', 'gt', 'lt', 'eq')
OBJECTIVES = ('maximize', 'minimize', 'feasible', 'compare', 'equilibrium', 'unknown')
STAGES = ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7')
STAGE_NAMES = {
    'G1': 'object_discovery', 'G2': 'semantic_binding',
    'G3': 'relationship_construction', 'G4': 'operation_and_number_grounding',
    'G5': 'objective_construction', 'G6': 'acquisition_and_memory',
    'G7': 'integrated_generalization',
}
MAX_NODES, MAX_EDGES, MAX_OPERANDS, MAX_DEPTH, MAX_NUMBER_BITS = 256, 2048, 8, 64, 8192


def exact_number(value):
    if not isinstance(value, str) or len(value) > 160 or not value.strip():
        raise ValueError('GAMER_GRAPH_BAD_EXACT_NUMBER')
    # Numeric *rendering* conversion is generic execution, not semantic extraction.
    clean = value.strip().replace(',', '').replace('_', '')
    try:
        result = Fraction(clean)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError('GAMER_GRAPH_BAD_EXACT_NUMBER') from exc
    if max(result.numerator.bit_length(), result.denominator.bit_length()) > MAX_NUMBER_BITS:
        raise ValueError('GAMER_GRAPH_NUMERIC_LIMIT')
    return result


def validate_graph(graph, *, complete=False):
    if not isinstance(graph, dict) or graph.get('schema') != SCHEMA:
        raise ValueError('GAMER_GRAPH_BAD_SCHEMA')
    if set(graph) != {'schema', 'nodes', 'edges', 'objective'}:
        raise ValueError('GAMER_GRAPH_BAD_FIELDS')
    nodes, edges, objective = graph['nodes'], graph['edges'], graph['objective']
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise ValueError('GAMER_GRAPH_NODE_LIMIT')
    if not isinstance(edges, list) or len(edges) > MAX_EDGES:
        raise ValueError('GAMER_GRAPH_EDGE_LIMIT')
    ids = {}
    for node in nodes:
        if not isinstance(node, dict) or not {'id', 'kind'} <= node.keys():
            raise ValueError('GAMER_GRAPH_BAD_NODE')
        if set(node) - {'id', 'kind', 'span', 'value', 'operator'}:
            raise ValueError('GAMER_GRAPH_UNEXPECTED_NODE_FIELD')
        ident, kind = node['id'], node['kind']
        if not isinstance(ident, str) or not ident or len(ident) > 64 or ident in ids:
            raise ValueError('GAMER_GRAPH_DUPLICATE_OR_BAD_ID')
        if kind not in NODE_KINDS:
            raise ValueError('GAMER_GRAPH_BAD_NODE_KIND')
        if 'span' in node:
            span = node['span']
            if not isinstance(span, list) or len(span) != 2 or any(type(x) is not int or x < 0 for x in span) or span[0] >= span[1]:
                raise ValueError('GAMER_GRAPH_BAD_SPAN')
        if 'value' in node:
            if kind != 'quantity':
                raise ValueError('GAMER_GRAPH_NON_QUANTITY_VALUE')
            exact_number(node['value'])
        if 'operator' in node:
            if kind != 'operation' or node['operator'] not in OPERATORS:
                raise ValueError('GAMER_GRAPH_BAD_OPERATOR')
        ids[ident] = node
    used = set()
    for edge in edges:
        if not isinstance(edge, dict) or not {'src', 'dst', 'type'} <= edge.keys() or set(edge) - {'src', 'dst', 'type', 'position'}:
            raise ValueError('GAMER_GRAPH_BAD_EDGE')
        src, dst, typ = edge['src'], edge['dst'], edge['type']
        if src not in ids or dst not in ids or typ not in BINDINGS + RELATIONS + ('operand',):
            raise ValueError('GAMER_GRAPH_BAD_EDGE_REFERENCE')
        if typ == 'operand':
            pos = edge.get('position')
            if ids[src]['kind'] != 'operation' or type(pos) is not int or not 0 <= pos < MAX_OPERANDS:
                raise ValueError('GAMER_GRAPH_BAD_OPERAND')
        elif 'position' in edge:
            raise ValueError('GAMER_GRAPH_UNEXPECTED_POSITION')
        key = (src, dst, typ, edge.get('position'))
        if key in used:
            raise ValueError('GAMER_GRAPH_DUPLICATE_EDGE')
        used.add(key)
    if objective is not None:
        if not isinstance(objective, dict) or set(objective) != {'kind', 'candidates'} or objective['kind'] not in OBJECTIVES:
            raise ValueError('GAMER_GRAPH_BAD_OBJECTIVE')
        choices = objective['candidates']
        if not isinstance(choices, list) or len(choices) != len(set(choices)) or any(c not in ids or ids[c]['kind'] != 'action' for c in choices):
            raise ValueError('GAMER_GRAPH_BAD_OBJECTIVE_CANDIDATES')
    if complete:
        if not isinstance(objective, dict) or objective['kind'] not in ('maximize', 'minimize') or not objective['candidates']:
            raise ValueError('GAMER_GRAPH_OBJECTIVE_UNEXECUTABLE')
        for candidate in objective['candidates']:
            if sum(e['src'] == candidate and e['type'] == 'score' for e in edges) != 1:
                raise ValueError('GAMER_GRAPH_ACTION_REQUIRES_ONE_SCORE')
    return graph


def execute_graph(graph):
    """Exact, finite DAG evaluation; never guesses missing semantic relationships."""
    validate_graph(graph, complete=True)
    ids = {n['id']: n for n in graph['nodes']}
    edges = graph['edges']
    argument_edges = {}
    score_edges = {}
    for e in edges:
        if e['type'] == 'operand':
            argument_edges.setdefault(e['src'], []).append(e)
        if e['type'] == 'score':
            score_edges[e['src']] = e['dst']
    cache, active = {}, set()

    def run(ident, depth=0):
        if ident in cache:
            return cache[ident]
        if ident in active or depth > MAX_DEPTH:
            raise ValueError('GAMER_GRAPH_CYCLE_OR_DEPTH')
        active.add(ident)
        node = ids[ident]
        if node['kind'] == 'quantity':
            if 'value' not in node:
                raise ValueError('GAMER_GRAPH_UNGROUNDED_NUMBER')
            result = exact_number(node['value'])
        elif node['kind'] == 'operation':
            op = node.get('operator')
            if op is None:
                raise ValueError('GAMER_GRAPH_MISSING_OPERATOR')
            args = sorted(argument_edges.get(ident, []), key=lambda e: e['position'])
            if len(args) > MAX_OPERANDS or [e['position'] for e in args] != list(range(len(args))):
                raise ValueError('GAMER_GRAPH_NONCONTIGUOUS_OPERANDS')
            numbers = [run(e['dst'], depth + 1) for e in args]
            arity = {'sub': (2,), 'div': (2,), 'neg': (1,), 'abs': (1,),
                     'gt': (2,), 'lt': (2,), 'eq': (2,)}
            if len(numbers) not in arity.get(op, tuple(range(2, MAX_OPERANDS + 1))):
                raise ValueError('GAMER_GRAPH_WRONG_ARITY')
            if op == 'add': result = sum(numbers, Fraction(0))
            elif op == 'mul':
                result = Fraction(1)
                for n in numbers: result *= n
            elif op == 'sub': result = numbers[0] - numbers[1]
            elif op == 'div':
                if numbers[1] == 0: raise ValueError('GAMER_GRAPH_DIVIDE_BY_ZERO')
                result = numbers[0] / numbers[1]
            elif op == 'neg': result = -numbers[0]
            elif op == 'abs': result = abs(numbers[0])
            elif op == 'min': result = min(numbers)
            elif op == 'max': result = max(numbers)
            elif op == 'gt': result = Fraction(int(numbers[0] > numbers[1]))
            elif op == 'lt': result = Fraction(int(numbers[0] < numbers[1]))
            elif op == 'eq': result = Fraction(int(numbers[0] == numbers[1]))
            else: raise ValueError('GAMER_GRAPH_UNSUPPORTED_OPERATOR')
        else:
            raise ValueError('GAMER_GRAPH_NON_NUMERIC_REFERENCE')
        if max(result.numerator.bit_length(), result.denominator.bit_length()) > MAX_NUMBER_BITS:
            raise ValueError('GAMER_GRAPH_RESULT_LIMIT')
        active.remove(ident)
        cache[ident] = result
        return result

    candidates = graph['objective']['candidates']
    scores = {name: run(score_edges[name]) for name in candidates}
    optimize = max if graph['objective']['kind'] == 'maximize' else min
    best = optimize(scores.values())
    return {'status': 'evaluated', 'scores': {k: str(v) for k, v in scores.items()},
            'decisions': [k for k, v in scores.items() if v == best],
            'objective': graph['objective']['kind']}


def empty_graph():
    return {'schema': SCHEMA, 'nodes': [], 'edges': [], 'objective': None}
