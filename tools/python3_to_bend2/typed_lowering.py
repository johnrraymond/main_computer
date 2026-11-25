"""Python AST -> Bend 2 emitter, with explicit native and CPython compatibility backends.

Native is intentionally fail-closed: passing the Python oracle does not prove Bend
parses or preserves semantics. Compat delegates to a real Python interpreter via
Bend's Process.run effect; it is not accelerated Bend execution.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

from tools.python3_to_bend2.syntax import (
    Translator, TranslationError, Definition, Signature, Symbol,
    NAT_MAX, U32_MAX, SMOKE_CASES, check_bend,
)


@dataclass(frozen=True)
class Capability:
    name: str
    native: str
    compatibility: str = 'CPython subprocess'


CAPABILITIES = (
    Capability('for range(n) and for range(literal bounds)', 'Nat fuel / tail-recursive state'),
    Capability('while loops', '@unsafe state recursion (no proof)'),
    Capability('list append and fixed-index replacement', 'affine persistent List'),
    Capability('signed unlimited integers', 'not supported natively: requires software BigInt'),
    Capability('unrestricted and mutual recursion', '@unsafe opt-in'),
    Capability('typed record classes', 'Data constructors / method functions'),
    Capability('general Python runtime effects', 'limited IO.print in native'),
)


def mk(node: ast.AST, location: ast.AST) -> ast.AST:
    return ast.copy_location(node, location)


def name_node(name: str, place: ast.AST) -> ast.Name:
    return mk(ast.Name(id=name, ctx=ast.Load()), place)


def definitely_exits(stmts: list[ast.stmt]) -> bool:
    if not stmts:
        return False
    last = stmts[-1]
    return isinstance(last, ast.Return) or (isinstance(last, ast.If) and bool(last.orelse)
                                             and definitely_exits(last.body) and definitely_exits(last.orelse))


class NativeTranslator(Translator):
    """Additional Bend-native lowerings, designed to reject semantic traps."""

    def __init__(self, source: str, source_name: str = '<input>', *,
                 unsafe_recursion: bool = False) -> None:
        super().__init__(source, source_name)
        self.unsafe_recursion = unsafe_recursion
        self.unsafe_defs: set[str] = set()
        self.record_classes: dict[str, tuple[Symbol, ...]] = {}
        self.class_nodes: dict[str, ast.ClassDef] = {}
        self.method_nodes: dict[str, ast.FunctionDef] = {}
        self.list_lengths: dict[str, int | None] = {}
        self.active_list_params: set[str] = set()
        self.loop_serial = 0

    def annotate(self, node: ast.expr | None) -> str:
        if isinstance(node, ast.Name) and node.id == 'PyIO':
            return 'IO(Unit)'
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == 'list':
            element = self.annotate(node.slice)
            if element not in ('U32', 'Nat', 'Bool', 'F32', 'String'):
                self.fail('native lists require Data scalar elements', node)
            return f'List<&2, {element}>'
        if isinstance(node, ast.Name) and node.id in self.record_classes:
            return f'Py{node.id}'
        return super().annotate(node)

    def scan(self) -> None:
        # Extract typed field-only classes into Bend algebraic Data records.
        functions: list[ast.FunctionDef] = []
        for stmt in self.tree.body:
            if not isinstance(stmt, ast.ClassDef):
                continue
            self.identifier(stmt.name, stmt)
            if stmt.bases or stmt.keywords:
                self.fail('native records support no inheritance or custom metaclasses', stmt)
            if stmt.decorator_list and not (len(stmt.decorator_list) == 1 and
                                            isinstance(stmt.decorator_list[0], ast.Name) and
                                            stmt.decorator_list[0].id == 'dataclass'):
                self.fail('only an optional @dataclass record decorator is supported', stmt)
            if stmt.name in self.record_classes:
                self.fail('duplicate class', stmt)
            fields: list[Symbol] = []
            self.record_classes[stmt.name] = ()
            for item in stmt.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is None:
                    fields.append(Symbol(self.identifier(item.target.id, item), self.annotate(item.annotation)))
                elif isinstance(item, ast.FunctionDef) and item.name != '__init__':
                    if not item.args.args or item.args.args[0].arg != 'self':
                        self.fail('record methods require self as their first argument', item)
                    if item.decorator_list:
                        self.fail('decorated methods are not supported', item)
                    new = mk(ast.FunctionDef(name=f'pycls_{stmt.name}_{item.name}',
                        args=ast.arguments(posonlyargs=[], args=[mk(ast.arg(arg='self', annotation=mk(ast.Name(id=stmt.name, ctx=ast.Load()), item)), item)] + item.args.args[1:],
                                           vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
                        body=item.body, decorator_list=[], returns=item.returns, type_comment=None,
                        type_params=[]), item)
                    functions.append(new)
                    self.method_nodes[new.name] = item
                elif isinstance(item, ast.Pass) or (isinstance(item, ast.Expr) and
                         isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)):
                    pass
                else:
                    self.fail('native records require typed fields and pure methods; use --backend compat for __init__/mutation', item)
            if not fields:
                self.fail('class must declare typed fields', stmt)
            self.record_classes[stmt.name] = tuple(fields)
            self.class_nodes[stmt.name] = stmt
        filtered = []
        for stmt in self.tree.body:
            if isinstance(stmt, ast.ClassDef):
                continue
            if isinstance(stmt, ast.ImportFrom) and stmt.module == 'dataclasses' and len(stmt.names) == 1 and stmt.names[0].name == 'dataclass':
                continue
            if isinstance(stmt, ast.FunctionDef) and isinstance(stmt.returns, ast.Constant) and stmt.returns.value is None:
                if stmt.name != 'main' or stmt.args.args:
                    self.fail('native IO currently supports only zero-arg main() -> None', stmt)
                stmt.returns = mk(ast.Name(id='PyIO', ctx=ast.Load()), stmt)
            filtered.append(stmt)
        filtered.extend(functions)
        self.tree.body = filtered
        super().scan()

    def hint(self, expr: ast.expr, env: dict[str, Symbol]) -> str | None:
        if isinstance(expr, ast.List):
            scalar = self.hint(expr.elts[0], env) if expr.elts else None
            return f'List<&2, {scalar or "U32"}>'
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id in self.record_classes:
            return f'Py{expr.func.id}'
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            if isinstance(expr.func.value, ast.Name):
                ob = env.get(expr.func.value.id)
                if ob and ob.typ.startswith('Py'):
                    m = f'pycls_{ob.typ[2:]}_{expr.func.attr}'
                    if m in self.signatures:
                        return self.signatures[m].returns
        return super().hint(expr, env)

    def expression(self, node: ast.expr, env: dict[str, Symbol], owner: Definition,
                   expected: str | None = None) -> tuple[str, str]:
        if isinstance(node, ast.List):
            inferred = expected or self.hint(node, env)
            mat = re.fullmatch(r'List<&2, (U32|Nat|Bool|F32|String)>', inferred or '')
            if not mat:
                self.fail('list literal requires list[scalar] type', node)
            element = mat.group(1)
            elems = [self.expression(x, env, owner, element)[0] for x in node.elts]
            return ('[' + ', '.join(elems) + ']', inferred)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in self.record_classes:
            cls = node.func.id
            fields = self.record_classes[cls]
            if node.keywords or len(node.args) != len(fields):
                self.fail('record construction requires positional field values', node)
            args = [self.expression(x, env, owner, f.typ)[0] for x, f in zip(node.args, fields)]
            typ = 'Py' + cls
            if expected and expected != typ:
                self.fail('record result type mismatch', node)
            return (f'{typ}' + '{' + ', '.join(args) + '}', typ)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            ob_name = node.func.value.id
            ob = env.get(ob_name)
            if ob and ob.typ.startswith('Py'):
                method = f'pycls_{ob.typ[2:]}_{node.func.attr}'
                sig = self.signatures.get(method)
                if not sig:
                    self.fail('unrecognized class method', node)
                if len(node.args) != len(sig.params) - 1 or node.keywords:
                    self.fail('record method argument mismatch', node)
                args = [self.expression(node.func.value, env, owner, sig.params[0].typ)[0]] + [
                    self.expression(x, env, owner, f.typ)[0] for x, f in zip(node.args, sig.params[1:])]
                owner.dependencies.add(method)
                if expected and expected != sig.returns:
                    self.fail('method return type mismatch', node)
                return (f'{method}(' + ', '.join(args) + ')', sig.returns)
        if isinstance(node, ast.Constant) and type(node.value) is int and expected == 'Nat' and 0 <= node.value <= NAT_MAX:
            return (str(node.value) + 'n', 'Nat')
        return super().expression(node, env, owner, expected)

    def _branch_with_continuation(self, node: ast.If, rest: list[ast.stmt], env: dict[str, Symbol],
                                  owner: Definition, expected: str) -> str:
        b = node.body if definitely_exits(node.body) else node.body + rest
        e = node.orelse if definitely_exits(node.orelse) else node.orelse + rest
        if not e:
            self.fail('missing false branch and continuation', node)
        return self.lower_if(mk(ast.If(test=node.test, body=b, orelse=e), node), env, owner, expected)

    def _range_info(self, node: ast.For, env: dict[str, Symbol], owner: Definition) -> tuple[str, str, str]:
        if not isinstance(node.target, ast.Name):
            self.fail('for-range loop requires a name target', node.target)
        call = node.iter
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == 'range' and not call.keywords and 1 <= len(call.args) <= 3):
            self.fail('native for supports range(...) only; use compat for arbitrary iterators', node.iter)
        args = call.args
        if len(args) == 1:
            limit, typ = self.expression(args[0], env, owner, 'U32')
            return (f'U32.to_nat({limit})', '0', '1')
        if not all(isinstance(x, ast.Constant) and type(x.value) is int for x in args):
            self.fail('range(start, stop, step) requires compile-time integer bounds; range(n) supports dynamic n', call)
        ints = [x.value for x in args]
        sequence = range(*ints)
        if any(x < 0 or x > U32_MAX for x in ints) or sequence.step < 1:
            self.fail('native range bounds must be nonnegative U32 and positive stride', call)
        if len(sequence) > U32_MAX or (len(sequence) and sequence[-1] > U32_MAX):
            self.fail('range exceeds U32 bounds', call)
        return (str(len(sequence)) + 'n', str(sequence.start), str(sequence.step))

    def _loop(self, stmt: ast.For | ast.While, rest: list[ast.stmt], env: dict[str, Symbol],
              owner: Definition, expected: str) -> str:
        self.loop_serial += 1
        idx = self.loop_serial
        name = f'pyloop_{idx:04d}'
        args = [Symbol(v.name, v.typ) for v in env.values()]
        logical = list(env)
        if isinstance(stmt, ast.For):
            fuel, start, step = self._range_info(stmt, env, owner)
            cursor = f'pycursor_{idx:04d}'
            remaining = f'pyrem_{idx:04d}'
            fuel_name = f'pyfuel_{idx:04d}'
            args = [Symbol(fuel_name, 'Nat'), Symbol(cursor, 'U32')] + args
            helper = Definition(Signature(name, tuple(args), expected), [], set(), set())
            self.signatures[name] = helper.signature
            stop_env = dict(env)
            done = self.block((stmt.orelse or []) + rest, stop_env, helper, expected)
            active_env = dict(env)
            active_env[remaining] = Symbol(remaining, 'Nat')
            active_env[cursor] = Symbol(cursor, 'U32')
            init = self.assigned(stmt.target, name_node(cursor, stmt), active_env, helper)
            body_prefix = [init]
            # Build the loop recurrence after lowering all body statements, so SSA
            # references name their latest physical versions.
            body_lines, active_env = self._lower_prefix(stmt.body, active_env, helper)
            body_prefix.extend(body_lines)
            if active_env is None:
                self.fail('early return/break/continue inside for requires --backend compat', stmt)
            if any(k not in active_env for k in logical):
                self.fail('loop loses outer state', stmt)
            updated = [active_env[k].name for k in logical]
            inc = f'({cursor} + {step} : U32)' if step != '0' else cursor
            helper.dependencies.add(name)
            body_prefix.append(f'{name}({", ".join([remaining, inc] + updated)})')
            helper.body = [f'match {fuel_name}:', '  case 0n:'] + [
                '    ' + line for line in done] + [f'  case 1n+{remaining}:'] + [
                '    ' + line for line in body_prefix]
            if any(isinstance(n, (ast.If, ast.While, ast.For)) for st in stmt.body for n in ast.walk(st)):
                self.unsafe_defs.add(name)
            self.defs[name] = helper
            owner.dependencies.add(name)
            return f'{name}({", ".join([fuel, start] + [v.name for v in env.values()])})'
        else:
            if stmt.orelse:
                self.fail('while-else currently requires --backend compat', stmt)
            helper = Definition(Signature(name, tuple(args), expected), [], set(), set())
            self.signatures[name] = helper.signature
            step_name = f'pystep_{idx:04d}'
            flag_name = f'pyflag_{idx:04d}'
            step_args = (Symbol(flag_name, 'Bool'),) + tuple(args)
            self.signatures[step_name] = Signature(step_name, step_args, expected)
            step_def = Definition(self.signatures[step_name], [], set(), set())
            cond, _ = self.expression(stmt.test, {k: Symbol(v.name, v.typ) for k, v in env.items()}, helper, 'Bool')
            helper.body = [f'{step_name}({", ".join([cond] + [v.name for v in env.values()])})']
            helper.dependencies.add(step_name)
            # Use an explicit Bool match helper: Bend forbids match on computed expressions.
            active_env = dict(env)
            prefix, active_env = self._lower_prefix(stmt.body, active_env, step_def)
            if active_env is None:
                self.fail('return/break/continue inside while requires --backend compat', stmt)
            step_def.dependencies.add(name)
            yes = prefix + [f'{name}({", ".join(active_env[k].name for k in logical)})']
            no = self.block(rest, dict(env), step_def, expected)
            step_def.body = [f'match {flag_name}:', '  case True{}:'] + [
                '    ' + line for line in yes] + ['  case False{}:'] + ['    ' + line for line in no]
            self.unsafe_defs.update((name, step_name))
            self.defs[name] = helper
            self.defs[step_name] = step_def
            owner.dependencies.add(name)
            return f'{name}({", ".join(v.name for v in env.values())})'

    def _lower_prefix(self, stmts: list[ast.stmt], env: dict[str, Symbol],
                      owner: Definition) -> tuple[list[str], dict[str, Symbol] | None]:
        lines: list[str] = []
        for s in stmts:
            if isinstance(s, ast.Assign):
                if len(s.targets) != 1:
                    self.fail('chained assignment requires compat', s)
                if isinstance(s.targets[0], ast.Subscript):
                    target = s.targets[0]
                    if not (isinstance(target.value, ast.Name) and target.value.id in env):
                        self.fail('list item assignment requires a local list', target)
                    lname = target.value.id
                    if lname in self.active_list_params:
                        self.fail('list parameter item mutation would not update caller aliases; use compat', target)
                    typ = env[lname].typ
                    match = re.fullmatch(r'List<&2, (\w+)>', typ)
                    if not match:
                        self.fail('only list item assignment supported', target)
                    index = target.slice
                    if not (isinstance(index, ast.Constant) and type(index.value) is int and index.value >= 0):
                        self.fail('native list assignment requires static nonnegative index; dynamic checks need compat', index)
                    known = self.list_lengths.get(lname)
                    if known is None or index.value >= known:
                        self.fail('list index must be statically in range', target)
                    value, _ = self.expression(s.value, env, owner, match.group(1))
                    index_expr = str(index.value) + 'n'
                    old = env[lname]
                    dest = self.fresh(lname)
                    env[lname] = Symbol(dest, typ)
                    owner.locals.add(dest)
                    lines.append(f'{dest} = List.set(&2, {match.group(1)}, {old.name}, {index_expr}, {value})')
                else:
                    target = s.targets[0]
                    if isinstance(s.value, ast.Name) and s.value.id in env and env[s.value.id].typ.startswith('List<'):
                        self.fail('list aliasing changes mutation semantics; use --backend compat', s)
                    rendered = self.assigned(target, s.value, env, owner)
                    lines.append(rendered)
                    if isinstance(target, ast.Name):
                        if isinstance(s.value, ast.List):
                            self.list_lengths[target.id] = len(s.value.elts)
                        elif target.id in self.list_lengths:
                            self.list_lengths[target.id] = None
            elif isinstance(s, ast.AnnAssign):
                if s.value is None:
                    self.fail('annotated local requires initial value', s)
                lines.append(self.assigned(s.target, s.value, env, owner, self.annotate(s.annotation)))
                if isinstance(s.target, ast.Name) and isinstance(s.value, ast.List):
                    self.list_lengths[s.target.id] = len(s.value.elts)
            elif isinstance(s, ast.AugAssign):
                if not isinstance(s.target, ast.Name):
                    self.fail('augmented assignment target requires a local name', s)
                value = mk(ast.BinOp(left=name_node(s.target.id, s), op=s.op, right=s.value), s)
                lines.append(self.assigned(s.target, value, env, owner))
            elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and isinstance(s.value.func, ast.Attribute):
                call = s.value
                if call.func.attr != 'append' or not isinstance(call.func.value, ast.Name) or call.keywords or len(call.args) != 1:
                    self.fail('native expression statement supports list.append only', s)
                lname = call.func.value.id
                if lname in self.active_list_params:
                    self.fail('list parameter mutation would not update Python caller aliases; use compat', call)
                if lname not in env:
                    self.fail('append target unbound', call)
                typ = env[lname].typ
                mt = re.fullmatch(r'List<&2, (\w+)>', typ)
                if not mt:
                    self.fail('append requires list[scalar]', call)
                elem = self.expression(call.args[0], env, owner, mt.group(1))[0]
                prev = env[lname]
                dest = self.fresh(lname)
                env[lname] = Symbol(dest, typ)
                owner.locals.add(dest)
                lines.append(f'{dest} = List.append(&2, {mt.group(1)}, {prev.name}, [{elem}])')
                if self.list_lengths.get(lname) is not None:
                    self.list_lengths[lname] += 1
            elif isinstance(s, ast.Pass):
                continue
            elif isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
                continue
            else:
                return lines, None
        return lines, env

    def block(self, stmts: list[ast.stmt], env: dict[str, Symbol], owner: Definition,
              expected: str) -> list[str]:
        pending = [x for x in stmts if not (isinstance(x, ast.Expr) and isinstance(x.value, ast.Constant)
                                           and isinstance(x.value.value, str))]
        if not pending:
            self.fail('empty body/branch', self.tree)
        lines: list[str] = []
        while pending:
            s, rest = pending[0], pending[1:]
            if isinstance(s, ast.Return):
                if rest:
                    self.fail('unreachable statements after return', rest[0])
                if s.value is None:
                    self.fail('bare return unsupported for pure functions', s)
                expr, _ = self.expression(s.value, env, owner, expected)
                return lines + [expr]
            if isinstance(s, ast.If):
                return lines + [self._branch_with_continuation(s, rest, env, owner, expected)]
            if isinstance(s, (ast.For, ast.While)):
                return lines + [self._loop(s, rest, env, owner, expected)]
            prefix, changed = self._lower_prefix([s], env, owner)
            if changed is None:
                self.fail(f'unsupported native statement {type(s).__name__}; use --backend compat', s)
            lines.extend(prefix)
            pending = rest
        self.fail('pure function must end in return', stmts[-1])

    def _io_body(self, func: ast.FunctionDef, env: dict[str, Symbol], owner: Definition) -> list[str]:
        # A limited but genuinely native effect surface. No ambient CPython IO.
        lines = ['do IO<Unit>:']
        for i, stmt in enumerate(func.body):
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and \
               isinstance(stmt.value.func, ast.Name) and stmt.value.func.id == 'print':
                call = stmt.value
                if len(call.args) != 1 or call.keywords:
                    self.fail('native print requires one positional argument', call)
                val, typ = self.expression(call.args[0], env, owner)
                if typ in ('U32', 'Nat'):
                    val = f'{typ}.show({val})'
                elif typ != 'String':
                    self.fail('native print accepts String, U32 or Nat', call)
                lines.append(f'  IO.print({val})')
            elif isinstance(stmt, ast.Return) and (stmt.value is None or isinstance(stmt.value, ast.Constant)
                                                    and stmt.value.value is None):
                if i != len(func.body) - 1:
                    self.fail('IO return must be final', stmt)
                lines.append('  return Unit{}')
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                prefix, changed = self._lower_prefix([stmt], env, owner)
                if changed is None:
                    self.fail('native effect assignment unsupported', stmt)
                for raw in prefix:
                    if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
                        self.fail('native IO variables require annotated assignments', stmt)
                    lines.append(f'  {stmt.target.id} : {self.annotate(stmt.annotation)} = ' + raw.split(' = ', 1)[1])
            elif isinstance(stmt, ast.Pass):
                pass
            else:
                self.fail('native IO supports print, typed assignments and return None only', stmt)
        if len(lines) == 1:
            lines.append('  return Unit{}')
        return lines

    def _compile_records(self) -> str:
        parts = []
        for name, fields in self.record_classes.items():
            props = ', '.join(f'{v.name}: {v.typ}' for v in fields)
            parts.append(f'type Py{name} is Data:\n  Py{name}' + '{' + props + '}')
        return '\n\n'.join(parts)

    def compile(self) -> str:
        self.scan()
        # For now record methods can be emitted only if field access is lowered as a
        # match on self; conversion is done at the AST boundary before block lowering.
        for nm, orig in self.method_nodes.items():
            cls = nm.split('_', 2)[1]
            fields = self.record_classes[cls]
            field_map = {x.name: x.name for x in fields}
            method = next(node for node in self.tree.body if isinstance(node, ast.FunctionDef) and node.name == nm)
            if {a.arg for a in method.args.args[1:]} & set(field_map):
                self.fail('record method argument shadows field name; use compat', method)
            class Rewriter(ast.NodeTransformer):
                def visit_Attribute(self, n: ast.Attribute) -> ast.AST:
                    if (isinstance(n.value, ast.Name) and n.value.id == 'self' and
                            n.attr in field_map and isinstance(n.ctx, ast.Load)):
                        return name_node(field_map[n.attr], n)
                    return self.generic_visit(n)
            method.body = [Rewriter().visit(s) for s in method.body]
        for statement in self.tree.body:
            if not isinstance(statement, ast.FunctionDef):
                continue
            sig = self.signatures[statement.name]
            env = {p.name: p for p in sig.params}
            self.active_list_params = {p.name for p in sig.params if p.typ.startswith('List<')}
            owner = Definition(sig, [], set(), set())
            if statement.name in self.method_nodes:
                cls = statement.name.split('_', 2)[1]
                fields = self.record_classes[cls]
                for field in fields:
                    env[field.name] = Symbol(field.name, field.typ)
                inner = self.block(statement.body, env, owner, sig.returns)
                pattern = f'Py{cls}' + '{' + ', '.join(('+' if sum(len(re.findall(r'(?<![\w])' + re.escape(f.name) + r'(?![\w])', line)) for line in inner) > 1 else '') + f.name for f in fields) + '}'
                owner.body = ['match self:', f'  case {pattern}:'] + ['    ' + x for x in inner]
            elif sig.returns == 'IO(Unit)':
                owner.body = self._io_body(statement, env, owner)
            else:
                owner.body = self.block(statement.body, env, owner, sig.returns)
            self.defs[sig.name] = owner
        ordered: list[str] = []
        marks: dict[str, int] = {}
        stack: list[str] = []
        def visit(name: str) -> None:
            state = marks.get(name, 0)
            if state == 2:
                return
            if state == 1:
                cycle = stack[stack.index(name):]
                structural = len(cycle) == 1 and name.startswith('pyloop_') and name not in self.unsafe_defs
                if not structural:
                    # Compiler-generated while recursion is explicitly unsafe; a
                    # user recursion cycle requires an explicit command-line opt-in.
                    generated_while = all(n.startswith(('pyloop_', 'pystep_')) for n in cycle)
                    if not (generated_while or self.unsafe_recursion):
                        raise TranslationError('recursive call graph requires --unsafe-recursion or --backend compat',
                                               self.tree, self.source_name)
                    self.unsafe_defs.update(cycle)
                return
            marks[name] = 1
            stack.append(name)
            definition = self.defs[name]
            for dep in sorted(definition.dependencies):
                if dep not in self.defs:
                    raise TranslationError(f'missing generated function {dep}', self.tree, self.source_name)
                visit(dep)
            stack.pop()
            marks[name] = 2
            rendered = definition.format()
            if name in self.unsafe_defs:
                rendered = '@unsafe ' + rendered
            ordered.append(rendered)
        for name in list(self.defs):
            visit(name)
        records = self._compile_records()
        banner = '# Generated AST lowering (native; Python integers map to U32)\n# Unsafe definitions are NOT proof-certified.\nimport Base'
        return '\n\n'.join(p for p in [banner, records, '\n\n'.join(ordered)] if p) + '\n'


_STDERR_EFFECT_C = r"""// Bend 2.0.40 custom effect; rebuild with every Bend runtime upgrade.
Term pycompat_write_err_run(Env e, Term* f, IoWork* w) {
  uint64_t n = 0;
  char* bytes = io_cstr(e, f[0], &n);
  io_out(stderr, bytes, n);
  free(bytes);
  return term_pak(CID(Unit), 0);
}
static void __attribute__((constructor)) pycompat_write_err_use(void) {
  io_eff(CID(PyCompat.write_err), pycompat_write_err_run, 0);
}
"""

_STDERR_EFFECT_JS = r"""// Write to stderr without an extra newline.
function pycompat_write_err(text) {
  io_out(2, io_bytes(text));
  return { $: CID(Unit) };
}
io_eff(CID(PyCompat.write_err), pycompat_write_err);
"""


def _compat_program(companion: Path, *, interpreter: str, wsl: bool = False,
                    max_output: int = 8_388_608, timeout_ms: int = 120_000) -> str:
    """All Python semantics delegated to a real CPython interpreter, explicitly."""
    if max_output <= 0 or max_output > U32_MAX or timeout_ms <= 0 or timeout_ms > U32_MAX:
        raise ValueError('runtime output/timeout limits must be positive U32 values')
    python = json.dumps(interpreter)
    target_path = str(companion.resolve()).replace('\\', '/')
    if wsl:
        if sys.platform != 'win32':
            raise OSError('--wsl compatibility builds require Windows Python and wsl.exe')
        target_path = subprocess.run(['wsl.exe', 'wslpath', '-a', str(companion.resolve())],
                     check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    path = json.dumps(target_path)
    return ("# Python compatibility runner: Bend handles subprocess I/O; CPython executes source.\n"
            "# This file DOES NOT translate Python semantics into native Bend.\n"
            "import Base\n\n"
            "def PyCompat.write_err(text: String) -> IO(Unit):\n"
            '  import "./py3bend2_write_err.c"\n'
            '  import "./py3bend2_write_err.js"\n\n'
            "def main() -> IO(Unit):\n"
            "  do IO<Unit>:\n"
            f"    result : U32 & (String & String) <- IO.try(U32 & (String & String), Process.run({python}, [{path}], \"\", {max_output}, {timeout_ms}))\n"
            "    (status, (stdout, stderr)) = result\n"
            "    IO.write(stdout)\n"
            "    PyCompat.write_err(stderr)\n"
            "    IO.exit(status)\n")


def emit_compat(source: str, input_name: str, output: Path, *, interpreter: str = 'python3',
                wsl: bool = False, max_output: int = 8_388_608, timeout_ms: int = 120_000) -> tuple[Path, Path]:
    # Syntax validation does not execute input; runtime will execute it with full
    # Python rights, including imports, files and networking.
    compile(ast.parse(source, filename=input_name), input_name, 'exec')
    output.parent.mkdir(parents=True, exist_ok=True)
    companion = output.with_name(output.stem + '.python_compat.py')
    companion.write_text(source, encoding='utf-8')
    output.write_text(_compat_program(companion, interpreter=interpreter, wsl=wsl,
                        max_output=max_output, timeout_ms=timeout_ms), encoding='utf-8')
    # This effect does not add a newline (IO.print_err would do so).
    # Process.run transports UTF-8 Strings, not arbitrary binary streams.
    (output.parent / 'py3bend2_write_err.c').write_text(_STDERR_EFFECT_C, encoding='utf-8')
    (output.parent / 'py3bend2_write_err.js').write_text(_STDERR_EFFECT_JS, encoding='utf-8')
    return output, companion


EXTENDED_SMOKES: list[tuple[str, str, object, str]] = [
    ('for_literal', 'def main() -> int:\n    total = 0\n    for i in range(2, 7):\n        total += i\n    return total\n', 20, 'native'),
    ('for_dynamic', 'def total(n: int) -> int:\n    out = 0\n    for i in range(n):\n        out += i\n    return out\n\ndef main() -> int:\n    return total(8)\n', 28, 'native'),
    ('while', 'def main() -> int:\n    i = 0\n    s = 0\n    while i < 5:\n        s += i\n        i += 1\n    return s\n', 10, 'unsafe'),
    ('list_append', 'def main() -> list[int]:\n    a: list[int] = [1, 2]\n    a.append(3)\n    return a\n', [1, 2, 3], 'native'),
    ('list_update', 'def main() -> list[int]:\n    a: list[int] = [1, 2, 3]\n    a[1] = 9\n    return a\n', [1, 9, 3], 'native'),
    ('record', 'from dataclasses import dataclass\n@dataclass\nclass Point:\n    x: int\n    y: int\n    def total(self) -> int:\n        return self.x + self.y\n\ndef main() -> int:\n    p = Point(4, 8)\n    return p.total()\n', 12, 'native'),
    ('recursion', 'def fact(n: int) -> int:\n    if n == 0:\n        return 1\n    return n * fact(n - 1)\n\ndef main() -> int:\n    return fact(5)\n', 120, 'unsafe'),
    ('bigint', 'def main():\n    x = 2 ** 130\n    return x * (-3)\n', -(2 ** 130) * 3, 'compat'),
    ('mutable_alias', 'def main():\n    a = [1, 2]\n    b = a\n    b.append(3)\n    return a\n', [1, 2, 3], 'compat'),
    ('class_mutation', 'class Box:\n    def __init__(self):\n        self.x = 3\n    def bump(self):\n        self.x += 1\nb = Box()\nb.bump()\nassert b.x == 4\n', None, 'compat'),
    ('runtime_effects', 'import io\nfrom contextlib import redirect_stdout\nwith redirect_stdout(io.StringIO()) as stream:\n    print("side effect")\nassert stream.getvalue() == "side effect\\n"\n', None, 'compat'),
]


def smoke(*, backend: str, bend: str | None = None, wsl: bool = False, strict_bend: bool = False) -> bool:
    selected = list(SMOKE_CASES) + [(n, s, expected) for n, s, expected, mode in EXTENDED_SMOKES if
                                    backend == 'compat' or mode in ('native', 'unsafe')]
    installed = bool(bend or (shutil.which('bend') if not wsl else 'bend'))
    if strict_bend and not installed:
        print('SMOKE FAIL Bend compiler missing (--strict-bend requires it)')
        return False
    failures = 0
    with tempfile.TemporaryDirectory(prefix='py3bend2extended-') as folder:
        for label, source, expected in selected:
            try:
                ns: dict[str, object] = {'__name__': '__main__'}
                exec(compile(source, label + '.py', 'exec'), ns)
                if 'main' in ns:
                    actual = ns['main']()
                    if actual != expected:
                        raise AssertionError(f'Python oracle {actual!r} != {expected!r}')
                target = Path(folder) / (label + '.bend')
                if backend == 'compat':
                    emit_compat(source, label + '.py', target)
                else:
                    code = NativeTranslator(source, label + '.py', unsafe_recursion=True).compile()
                    target.write_text(code, encoding='utf-8')
                if installed:
                    # Unsafe programs deliberately fail proof-checks. Compiler execution
                    # needs a separate runtime gate; do not misreport them as certified.
                    if backend == 'compat' or '@unsafe' in target.read_text(encoding='utf-8'):
                        print(f'SMOKE {label}: AST=PASS PYTHON=PASS BEND=GENERATED_UNVERIFIED')
                    else:
                        check_bend(target, bend=bend or 'bend', wsl=wsl,
                                   expected=expected if isinstance(expected, int) else None)
                        print(f'SMOKE {label}: AST=PASS PYTHON=PASS BEND=CHECKED')
                else:
                    print(f'SMOKE {label}: AST=PASS PYTHON=PASS BEND=NOT_RUN')
            except Exception as exc:
                failures += 1
                print(f'SMOKE {label}: FAIL {type(exc).__name__}: {exc}')
    print(f'SMOKE SUMMARY backend={backend} total={len(selected)} failures={failures} bend_present={installed}')
    return failures == 0


def auto_semantic_risk(source: str, filename: str) -> str | None:
    """Don't silently substitute 32-bit/F32 arithmetic for Python's types.

    This is a conservative gate, not a theorem of semantic equivalence.
    An exact-native backend eventually needs range and alias proofs.
    """
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Name) and node.id in ('int', 'float')) or (
                isinstance(node, ast.Constant) and type(node.value) in (int, float)):
            return 'Python int/float have different precision or overflow semantics from Bend U32/F32'
        if isinstance(node, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
                             ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.Try,
                             ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal)):
            return f'general Python semantics ({type(node).__name__}) require CPython'
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    translation = sub.add_parser('translate')
    translation.add_argument('input', type=Path)
    translation.add_argument('-o', '--output', type=Path)
    translation.add_argument('--backend', choices=('native', 'compat', 'auto'), default='auto')
    translation.add_argument('--unsafe-recursion', action='store_true')
    translation.add_argument('--interpreter', default='python3')
    translation.add_argument('--check-bend', action='store_true')
    translation.add_argument('--bend', default='bend')
    translation.add_argument('--wsl', action='store_true')
    tests = sub.add_parser('smoke')
    tests.add_argument('--backend', choices=('native', 'compat'), default='native')
    tests.add_argument('--bend')
    tests.add_argument('--wsl', action='store_true')
    tests.add_argument('--strict-bend', action='store_true')
    sub.add_parser('capabilities')
    args = parser.parse_args(argv)
    if args.command == 'capabilities':
        for c in CAPABILITIES:
            print(f'{c.name}: native={c.native}; compat={c.compatibility}')
        return 0
    if args.command == 'smoke':
        return 0 if smoke(backend=args.backend, bend=args.bend, wsl=args.wsl,
                          strict_bend=args.strict_bend) else 1
    source = args.input.read_text(encoding='utf-8')
    output = args.output or args.input.with_suffix('.bend')
    try:
        if args.backend in ('native', 'auto'):
            try:
                risk = auto_semantic_risk(source, str(args.input)) if args.backend == 'auto' else None
                if risk:
                    raise TranslationError(f'auto mode preserves Python semantics: {risk}; native requires --backend native',
                                           source=str(args.input))
                generated = NativeTranslator(source, str(args.input),
                       unsafe_recursion=args.unsafe_recursion).compile()
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(generated, encoding='utf-8')
                print(f'TRANSLATE NATIVE {args.input} -> {output}')
            except TranslationError as exc:
                if args.backend == 'native':
                    raise
                print(f'TRANSLATE NATIVE REJECTED {exc}; switching explicitly to CPython compatibility')
                emit_compat(source, str(args.input), output, interpreter=args.interpreter, wsl=args.wsl)
                print(f'TRANSLATE COMPAT {args.input} -> {output}')
        else:
            emit_compat(source, str(args.input), output, interpreter=args.interpreter, wsl=args.wsl)
            print(f'TRANSLATE COMPAT {args.input} -> {output} (CPython remains the runtime)')
        if args.check_bend:
            if '@unsafe' in output.read_text(encoding='utf-8'):
                raise TranslationError('cannot claim Bend proof certification for @unsafe definitions', source=args.input.as_posix())
            check_bend(output, bend=args.bend, wsl=args.wsl, timeout=60)
            print('BEND CHECK PASS (not a Python equivalence proof)')
        return 0
    except (TranslationError, SyntaxError, OSError, ValueError, subprocess.TimeoutExpired,
            AssertionError) as exc:
        print(f'TRANSLATE FAILED {exc}', file=sys.stderr)
        return 2
