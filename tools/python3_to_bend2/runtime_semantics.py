"""Python 3 -> Bend 2 v11: explicit error-control lowering and selected runtime features.

Native Bend source emission, NEVER CPython runtime delegation. This is a
strict subset translator; it is not a complete Python 3 implementation.
Missing semantics fail closed. Actual Bend compiler execution is required to
certify emitted programs.
"""
from __future__ import annotations

import argparse
import ast
import copy
import math
import operator
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.exceptions_attributes import ExceptionAttributeLowerer
from tools.python3_to_bend2.automatic_gc import RuntimeModuleLinker


RESERVED = '__py11_'
EXCEPTIONS = frozenset({
    'ValueError', 'TypeError', 'KeyError', 'IndexError', 'RuntimeError',
    'ZeroDivisionError', 'AssertionError', 'Exception', 'BaseException', 'StopIteration',
})


def N(s: str, *, store: bool = False) -> ast.Name:
    return ast.Name(id=s, ctx=ast.Store() if store else ast.Load())


def C(name: str, *args: ast.expr) -> ast.Call:
    return ast.Call(func=N('_py10_' + name[len('__py10_'):] if name.startswith('__py10_') else name),
                    args=list(args), keywords=[])


def A(name: str, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[N(name, store=True)], value=value)


def If(test: ast.expr, yes: list[ast.stmt], no: list[ast.stmt] | None = None) -> ast.If:
    return ast.If(test=test, body=yes or [ast.Pass()], orelse=no or [])


def has_raising_call(node: ast.AST, risky: set[str]) -> bool:
    return any(isinstance(n, ast.Call) and (
        isinstance(n.func, ast.Name) and n.func.id in risky or
        isinstance(n.func, ast.Attribute) and ('method:' + n.func.attr in risky))
        for n in ast.walk(node))


class RuntimeSemanticPass:
    """Convert selected Python control flow to v10's explicit PyValue error protocol.

    Emitted `__py10_*` calls are interpreted by the v10 emitter, not imported.
    All unsupported general control-flow cases raise a TranslationError.
    """
    def __init__(self, source_name: str = '<input>') -> None:
        self.source_name = source_name
        self.counter = 0
        self.risky: set[str] = set()
        self.generated: set[str] = set()
        self.generators: dict[str, ast.FunctionDef] = {}
        self.parents: dict[str, str] = {}
        self.descriptors: dict[str, set[str]] = {}
        self.class_names: set[str] = set()

    def fail(self, node: ast.AST, reason: str):
        raise TranslationError(f'{reason} (line {getattr(node, "lineno", "?")})', source=self.source_name)

    def sym(self, prefix: str) -> str:
        self.counter += 1
        return f'{RESERVED}{prefix}_{self.counter}'

    def _validate_reserved(self, tree: ast.Module) -> None:
        for n in ast.walk(tree):
            if isinstance(n, (ast.Name, ast.arg, ast.FunctionDef, ast.ClassDef)):
                spelling = n.id if isinstance(n, ast.Name) else n.arg if isinstance(n, ast.arg) else n.name
                if spelling.startswith(RESERVED) or spelling.startswith('__py10_'):
                    self.fail(n, 'reserved compiler namespace must not be shadowed')

    def _generator_to_loop(self, tree: ast.Module) -> None:
        """Lower pure range generators to heap-owned, resumable cursor classes.

        This deliberately does NOT attempt arbitrary suspended Python frames.
        `for`, repeated `next()`, aliases, and exhaustion of these specialized
        generators retain lazy cursor state in the normal PyHeap object model.
        """
        definitions: dict[str, str] = {}
        generator_nodes: dict[str, ast.FunctionDef] = {}
        synthetic: list[ast.ClassDef] = []
        for stmt in tree.body:
            if not isinstance(stmt, ast.FunctionDef) or not any(
                    isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(stmt)):
                continue
            if stmt.decorator_list or len(stmt.body) != 1 or not isinstance(stmt.body[0], ast.For):
                self.fail(stmt, 'resumable generator requires a single range loop')
            loop = stmt.body[0]
            if not (len(loop.body) == 1 and isinstance(loop.body[0], ast.Expr) and
                    isinstance(loop.body[0].value, ast.Yield) and
                    loop.body[0].value.value is not None and not loop.orelse):
                self.fail(stmt, 'generator needs exactly one yielded expression per iteration')
            if not (isinstance(loop.target, ast.Name) and isinstance(loop.iter, ast.Call) and
                    isinstance(loop.iter.func, ast.Name) and loop.iter.func.id == 'range' and
                    not loop.iter.keywords and 1 <= len(loop.iter.args) <= 3):
                self.fail(stmt, 'generator iteration must use range(start, stop[, step])')
            if len(loop.iter.args) == 3:
                try:
                    step = ast.literal_eval(loop.iter.args[2])
                except (ValueError, TypeError, SyntaxError):
                    step = None
                if type(step) is not int or step == 0:
                    self.fail(stmt, 'generator step must be a nonzero integer constant')
            else:
                step = 1
            if stmt.args.posonlyargs or stmt.args.kwonlyargs or stmt.args.defaults or stmt.args.vararg or stmt.args.kwarg:
                self.fail(stmt, 'generator parameters require simple annotated arguments')
            if any(a.annotation is None or not isinstance(a.annotation, ast.Name) or a.annotation.id != 'int'
                   for a in stmt.args.args):
                self.fail(stmt, 'generator parameters must be annotated int')
            yielded = loop.body[0].value.value
            if any(isinstance(n, (ast.Call, ast.NamedExpr, ast.Await, ast.Yield, ast.YieldFrom,
                                  ast.Attribute, ast.Subscript)) for n in ast.walk(yielded)):
                self.fail(stmt, 'yield body requires pure integer arithmetic')
            allowed = {loop.target.id} | {a.arg for a in stmt.args.args}
            if any(isinstance(n, ast.Name) and n.id not in allowed for n in ast.walk(yielded)):
                self.fail(stmt, 'generator cannot capture global or unbound variables')
            if not any(isinstance(n, ast.Name) and n.id == loop.target.id for n in ast.walk(yielded)):
                self.fail(stmt, 'generator must reference its cursor in the yield expression')
            class_name = f'Py11Gen_{stmt.name}'
            if any(isinstance(n, ast.ClassDef) and n.name == class_name for n in tree.body):
                self.fail(stmt, 'generator class name collision')
            definitions[stmt.name] = class_name
            generator_nodes[stmt.name] = stmt
            args = loop.iter.args
            start, stop = (ast.Constant(0), args[0]) if len(args) == 1 else args[:2]

            class Replace(ast.NodeTransformer):
                def visit_Name(self, node):
                    if node.id == loop.target.id and isinstance(node.ctx, ast.Load):
                        return N('_py11_idx')
                    if node.id in {a.arg for a in stmt.args.args} and isinstance(node.ctx, ast.Load):
                        return ast.Attribute(value=N('self'), attr='saved_' + node.id, ctx=ast.Load())
                    return node

            yield_result = Replace().visit(copy.deepcopy(yielded))
            # Class generated as real Python AST, so ordinary v4 scanning,
            # state passing and GC handle generator identities and aliases.
            constructor_args = [ast.arg(arg='self')] + copy.deepcopy(stmt.args.args)
            assignments: list[ast.stmt] = []
            for arg in stmt.args.args:
                assignments.append(ast.Assign(targets=[ast.Attribute(value=N('self'), attr='saved_' + arg.arg,
                                                                       ctx=ast.Store())], value=N(arg.arg)))
            assignments += [
                ast.Assign(targets=[ast.Attribute(value=N('self'), attr='cursor', ctx=ast.Store())], value=start),
                ast.Assign(targets=[ast.Attribute(value=N('self'), attr='stop', ctx=ast.Store())], value=stop),
            ]
            ctor = ast.FunctionDef(name='__init__', args=ast.arguments(posonlyargs=[], args=constructor_args,
                vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
                body=assignments, decorator_list=[], returns=N('None'), type_comment=None)
            has_next = ast.FunctionDef(name='has_next', args=ast.arguments(posonlyargs=[],
                args=[ast.arg(arg='self')], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
                body=[ast.Return(value=ast.Compare(
                    left=ast.Attribute(value=N('self'), attr='cursor', ctx=ast.Load()),
                    ops=[ast.Lt() if step > 0 else ast.Gt()],
                    comparators=[ast.Attribute(value=N('self'), attr='stop', ctx=ast.Load())]))],
                decorator_list=[], returns=N('bool'), type_comment=None)
            # Cursor increments only when next_value is invoked. A stopped
            # iterator raises StopIteration, not an arbitrary default value.
            take = ast.FunctionDef(name='next_value', args=ast.arguments(posonlyargs=[],
                args=[ast.arg(arg='self')], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[]),
                body=[
                    If(ast.UnaryOp(op=ast.Not(), operand=ast.Call(
                        func=ast.Attribute(value=N('self'), attr='has_next', ctx=ast.Load()),
                        args=[], keywords=[])),
                        [ast.Raise(exc=C('StopIteration'))]),
                    A('_py11_idx', ast.Attribute(value=N('self'), attr='cursor', ctx=ast.Load())),
                    ast.AugAssign(target=ast.Attribute(value=N('self'), attr='cursor', ctx=ast.Store()),
                                  op=ast.Add(), value=ast.Constant(step)),
                    ast.Return(value=yield_result),
                ], decorator_list=[], returns=N('int'), type_comment=None)
            fields: list[ast.stmt] = [
                ast.AnnAssign(target=N('cursor', store=True), annotation=N('int'), value=None, simple=1),
                ast.AnnAssign(target=N('stop', store=True), annotation=N('int'), value=None, simple=1),
            ] + [ast.AnnAssign(target=N('saved_' + a.arg, store=True), annotation=N('int'),
                               value=None, simple=1) for a in stmt.args.args]
            synthetic.append(ast.ClassDef(name=class_name, bases=[], keywords=[], body=[*fields, ctor, has_next, take], decorator_list=[]))

        if not definitions:
            return
        tree.body = [n for n in tree.body if not (isinstance(n, ast.FunctionDef) and n.name in definitions)]
        tree.body = [*synthetic, *tree.body]
        outer = self

        class Rewrite(ast.NodeTransformer):
            def __init__(self):
                self.known_generators: set[str] = set()
                self.assigned: set[str] = set()

            def visit_FunctionDef(self, node):
                old_known, old_assigned = self.known_generators, self.assigned
                self.known_generators, self.assigned = set(), {a.arg for a in node.args.args}
                new_body: list[ast.stmt] = []
                for index, stmt in enumerate(node.body):
                    if isinstance(stmt, ast.For) and isinstance(stmt.target, ast.Name):
                        source = stmt.iter
                        gen_iteration = (
                            isinstance(source, ast.Name) and source.id in self.known_generators or
                            isinstance(source, ast.Call) and isinstance(source.func, ast.Name)
                            and source.func.id in definitions)
                        target = stmt.target.id
                        if gen_iteration and target not in self.assigned:
                            if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                                   and n.id == target for n in ast.walk(source)):
                                outer.fail(stmt, 'unbound generator target used while constructing iterator')
                            if any(isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                                   and n.id == target for remainder in node.body[index+1:]
                                   for n in ast.walk(remainder)):
                                outer.fail(stmt, 'generator target may be unbound after empty iteration')
                    # Capture the original RHS before the NodeTransformer turns
                    # the generator factory into its heap-backed class.
                    is_generator = (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name) and
                        (isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name)
                         and stmt.value.func.id in definitions or
                         isinstance(stmt.value, ast.Name) and stmt.value.id in self.known_generators))
                    updated = self.visit(stmt)
                    new_body.extend(updated if isinstance(updated, list) else [updated])
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                        self.assigned.add(stmt.targets[0].id)
                        if is_generator:
                            self.known_generators.add(stmt.targets[0].id)
                        else:
                            self.known_generators.discard(stmt.targets[0].id)
                node.body = new_body
                self.known_generators, self.assigned = old_known, old_assigned
                return node

            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id in definitions:
                    return ast.Call(func=N(definitions[node.func.id]),
                                    args=[self.visit(a) for a in node.args], keywords=node.keywords)
                if isinstance(node.func, ast.Name) and node.func.id == 'next' and len(node.args) == 1 and not node.keywords:
                    arg = node.args[0]
                    if isinstance(arg, ast.Name) and arg.id in self.known_generators:
                        return ast.Call(func=ast.Attribute(value=N(arg.id), attr='next_value', ctx=ast.Load()),
                                        args=[], keywords=[])
                return self.generic_visit(node)

            def visit_For(self, node):
                source = node.iter
                known = isinstance(source, ast.Name) and source.id in self.known_generators
                direct = isinstance(source, ast.Call) and isinstance(source.func, ast.Name) and source.func.id in definitions
                if not (known or direct):
                    return self.generic_visit(node)
                if not isinstance(node.target, ast.Name):
                    outer.fail(node, 'generator iteration needs a single target')
                original = node.target.id
                if original not in self.assigned:
                    # Only a loop-carrier initialization, not Python-visible
                    # initialization; reading it after empty iteration is
                    # rejected by the existing v5 check when observed.
                    init_target = [A(original, ast.Constant(0))]
                else:
                    init_target = []
                it = outer.sym('generator') if direct else source.id
                start = [A(it, self.visit(source))] if direct else []
                consume = A(original, ast.Call(
                    func=ast.Attribute(value=N(it), attr='next_value', ctx=ast.Load()), args=[], keywords=[]))
                body = [consume, *node.body]
                wh = ast.While(test=ast.Call(
                    func=ast.Attribute(value=N(it), attr='has_next', ctx=ast.Load()), args=[], keywords=[]),
                    body=body, orelse=node.orelse)
                return [*init_target, *start, wh]

        Rewrite().visit(tree)

    def _flatten_inheritance(self, tree: ast.Module):
        classes = {x.name: x for x in tree.body if isinstance(x, ast.ClassDef)}
        self.class_names = set(classes)
        for cls in classes.values():
            if cls.keywords or len(cls.bases) > 1:
                self.fail(cls, 'metaclasses and multiple inheritance require dynamic MRO')
            if not cls.bases:
                continue
            parent = cls.bases[0]
            if not isinstance(parent, ast.Name) or parent.id not in classes or parent.id == cls.name:
                self.fail(cls, 'native inheritance needs a local known base class')
            self.parents[cls.name] = parent.id
        done: set[str] = set()
        def flatten(name: str, stack: set[str]):
            if name in done:
                return
            if name in stack:
                self.fail(classes[name], 'cyclic inheritance')
            if name not in self.parents:
                done.add(name)
                return
            parent_name = self.parents[name]
            flatten(parent_name, stack | {name})
            cls, base = classes[name], classes[parent_name]
            own_method = {s.name for s in cls.body if isinstance(s, ast.FunctionDef)}
            own_fields = {s.target.id for s in cls.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
            inherited: list[ast.stmt] = []
            for item in base.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id not in own_fields:
                    inherited.append(copy.deepcopy(item))
                if isinstance(item, ast.FunctionDef) and item.name not in own_method:
                    inherited.append(copy.deepcopy(item))
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'super'
                   for n in ast.walk(cls)):
                self.fail(cls, 'super() needs a dynamic method-resolution-order frame')
            cls.body = [*inherited, *cls.body]
            cls.bases = []
            done.add(name)
        for name in classes:
            flatten(name, set())


    def _lower_properties(self, tree: ast.Module):
        """Lower known @property data descriptors to typed method dispatch.

        Python's full descriptor protocol permits dynamic methods and arbitrary
        overriding. This specialization only runs when the receiver class is
        statically identified; otherwise it refuses the access.
        """
        getter: dict[str, set[str]] = {}
        setter: dict[str, set[str]] = {}
        for cls in (s for s in tree.body if isinstance(s, ast.ClassDef)):
            getters: set[str] = set()
            setters: set[str] = set()
            for member in cls.body:
                if not isinstance(member, ast.FunctionDef) or not member.decorator_list:
                    continue
                if (len(member.decorator_list) == 1 and isinstance(member.decorator_list[0], ast.Name)
                        and member.decorator_list[0].id == 'property'):
                    if any(isinstance(n, (ast.Raise, ast.Yield, ast.YieldFrom)) for n in ast.walk(member)):
                        self.fail(member, 'raising and generator properties need descriptor unwind')
                    getters.add(member.name)
                    member.name = f'_py11_property_{member.name}'
                    member.decorator_list = []
                elif (len(member.decorator_list) == 1 and isinstance(member.decorator_list[0], ast.Attribute)
                      and member.decorator_list[0].attr == 'setter' and
                      isinstance(member.decorator_list[0].value, ast.Name) and
                      member.decorator_list[0].value.id == member.name):
                    setters.add(member.name)
                    member.name = f'_py11_set_{member.name}'
                    member.decorator_list = []
            if setters - getters:
                self.fail(cls, 'property setter requires a matching getter')
            getter[cls.name] = getters
            setter[cls.name] = setters
        self.descriptors = getter
        if not any(getter.values()):
            return
        outer = self
        class Rewriter(ast.NodeTransformer):
            def __init__(self):
                self.variables: dict[str,str] = {}
                self.class_name: str | None = None

            def class_for(self, receiver: ast.expr) -> str | None:
                return self.variables.get(receiver.id) if isinstance(receiver, ast.Name) else None

            def visit_FunctionDef(self, fn):
                old = self.variables
                self.variables = dict(old)
                if self.class_name is not None:
                    self.variables['self'] = self.class_name
                self.generic_visit(fn)
                self.variables = old
                return fn

            def visit_ClassDef(self, cls):
                old = self.class_name
                self.class_name = cls.name
                self.generic_visit(cls)
                self.class_name = old
                return cls

            def visit_Assign(self, node):
                node = self.generic_visit(node)
                if len(node.targets) != 1:
                    return node
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id in getter:
                        self.variables[target.id] = node.value.func.id
                    elif isinstance(node.value, ast.Name) and node.value.id in self.variables:
                        self.variables[target.id] = self.variables[node.value.id]
                    else:
                        self.variables.pop(target.id, None)
                if isinstance(target, ast.Attribute):
                    kind = self.class_for(target.value)
                    if kind in setter and target.attr in setter[kind]:
                        return ast.Expr(value=ast.Call(
                            func=ast.Attribute(value=target.value, attr=f'_py11_set_{target.attr}', ctx=ast.Load()),
                            args=[node.value], keywords=[]))
                    if kind in getter and target.attr in getter[kind]:
                        outer.fail(node, 'read-only property cannot be assigned')
                return node

            def visit_Call(self, node):
                node = self.generic_visit(node)
                if not isinstance(node.func, ast.Name) or not node.args:
                    return node
                if node.func.id not in ('getattr', 'hasattr', 'setattr', 'delattr') or len(node.args) < 2:
                    return node
                obj, name = node.args[:2]
                kind = self.class_for(obj)
                if kind not in getter or not isinstance(name, ast.Constant) or not isinstance(name.value, str):
                    return node
                prop = name.value
                if prop not in getter[kind]:
                    return node
                if node.func.id == 'getattr' and len(node.args) == 2:
                    return ast.Call(func=ast.Attribute(value=obj, attr=f'_py11_property_{prop}', ctx=ast.Load()), args=[], keywords=[])
                if node.func.id == 'hasattr' and len(node.args) == 2:
                    return ast.Constant(True)
                if node.func.id == 'setattr' and len(node.args) == 3 and prop in setter.get(kind, set()):
                    return ast.Call(func=ast.Attribute(value=obj, attr=f'_py11_set_{prop}', ctx=ast.Load()), args=[node.args[2]], keywords=[])
                outer.fail(node, 'property access requires its registered getter/setter')

            def visit_Attribute(self, node):
                node = self.generic_visit(node)
                if not isinstance(node.ctx, ast.Load):
                    return node
                kind = self.class_for(node.value)
                if kind in getter and node.attr in getter[kind]:
                    return ast.Call(func=ast.Attribute(value=node.value, attr=f'_py11_property_{node.attr}', ctx=ast.Load()), args=[], keywords=[])
                return node

        Rewriter().visit(tree)

    def _normalize_imports(self, tree: ast.Module) -> None:
        """Statically bind pure math/operator imports at module or function scope.

        The imported functions are implemented natively. Module side effects,
        import hooks and nonliteral dynamic imports remain unsupported.
        """
        def parse_import(stmt: ast.stmt) -> dict[str, str] | None:
            imports: dict[str, str] = {}
            if isinstance(stmt, ast.Import) and len(stmt.names) == 1:
                name = stmt.names[0]
                if name.name in ('math', 'operator'):
                    imports[name.asname or name.name] = name.name
                    return imports
            if isinstance(stmt, ast.ImportFrom) and stmt.module in ('math', 'operator'):
                for name in stmt.names:
                    if name.name == '*':
                        self.fail(stmt, 'wildcard standard-library imports are unsupported')
                    imports[name.asname or name.name] = f'{stmt.module}.{name.name}'
                return imports
            return None

        def rewrite_calls(items: list[ast.stmt], aliases: dict[str, str]) -> list[ast.stmt]:
            class Rewrite(ast.NodeTransformer):
                def visit_Call(self, node):
                    node = self.generic_visit(node)
                    if isinstance(node.func, ast.Name) and node.func.id in aliases and '.' in aliases[node.func.id]:
                        module, function = aliases[node.func.id].split('.', 1)
                        node.func = ast.Attribute(value=N(module), attr=function, ctx=ast.Load())
                    elif (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                          and node.func.value.id in aliases):
                        module = aliases[node.func.value.id]
                        if '.' not in module:
                            node.func.value = N(module)
                    return node
            return [Rewrite().visit(item) for item in items]

        module_aliases: dict[str, str] = {}
        required: set[str] = set()
        retained: list[ast.stmt] = []
        for item in tree.body:
            mapping = parse_import(item)
            if mapping is not None:
                module_aliases.update(mapping)
                required.update(v.split('.')[0] for v in mapping.values())
                continue
            retained.append(item)
        new_body: list[ast.stmt] = []
        for item in retained:
            if isinstance(item, ast.FunctionDef):
                local: dict[str, str] = {}
                remaining: list[ast.stmt] = []
                for sub in item.body:
                    mapping = parse_import(sub)
                    if mapping is not None:
                        local.update(mapping)
                        required.update(v.split('.')[0] for v in mapping.values())
                        continue
                    # A conditional import creates a runtime-dependent binding;
                    # silently hoisting it would change UnboundLocalError.
                    if any(isinstance(x, (ast.Import, ast.ImportFrom)) and parse_import(x) is not None
                           for x in ast.walk(sub)):
                        self.fail(sub, 'conditional or nested imports need import-time control flow')
                    remaining.append(sub)
                import_bindings = {**module_aliases, **local}
                for arg in item.args.args:
                    if arg.arg in import_bindings:
                        self.fail(arg, 'import name is shadowed by a function argument')
                for sub in remaining:
                    for name in ast.walk(sub):
                        if (isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)
                            and name.id in import_bindings):
                            self.fail(name, 'import name is shadowed by local assignment')
                item.body = rewrite_calls(remaining, import_bindings)
                new_body.append(item)
            elif isinstance(item, ast.ClassDef):
                for member in item.body:
                    if isinstance(member, ast.FunctionDef):
                        member.body = rewrite_calls(member.body, module_aliases)
                new_body.append(item)
            else:
                new_body.append(item)
        tree.body = [ast.Import(names=[ast.alias(name=mod)]) for mod in sorted(required)] + new_body

    def _runtime_math(self, tree: ast.Module):
        """Use native PyInt arithmetic for dynamic integer math, not compile-time folding."""
        used: set[str] = set()
        roots = {s.names[0].name for s in tree.body if isinstance(s, ast.Import) and len(s.names) == 1}
        outer = self
        supported = {'gcd', 'lcm', 'factorial', 'isqrt', 'comb', 'perm'}
        class Rewrite(ast.NodeTransformer):
            def visit_Call(self, n):
                n = self.generic_visit(n)
                if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == 'math':
                    if 'math' not in roots:
                        outer.fail(n, 'math is not imported')
                    if n.func.attr in supported:
                        if n.keywords:
                            outer.fail(n, 'math helper keywords are not supported')
                        used.add(n.func.attr)
                        n.func = N(f'{RESERVED}math_{n.func.attr}')
                return n
        tree.body = Rewrite().visit(tree).body
        if not used:
            return
        # The helper implementations accept dynamic Python ints and deliberately
        # avoid native bounded Nat arithmetic. Source-level ValueError paths are
        # lowered to PyValue errors by the exception pass below.
        source = '''
def __py11_math_gcd(a: int, b: int) -> int:
    a = abs(a)
    b = abs(b)
    t = 0
    while b != 0:
        t = b
        b = a % b
        a = t
    return a

def __py11_math_lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs((a // __py11_math_gcd(a, b)) * b)

def __py11_math_factorial(n: int) -> int:
    if n < 0:
        raise ValueError('factorial() not defined for negative values')
    p = 1
    k = 2
    while k <= n:
        p = p * k
        k += 1
    return p

def __py11_math_isqrt(n: int) -> int:
    if n < 0:
        raise ValueError('isqrt() argument must be nonnegative')
    lo = 0
    hi = n + 1
    mid = 0
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if mid * mid <= n:
            lo = mid
        else:
            hi = mid
    return lo

def __py11_math_perm(n: int, k: int) -> int:
    if n < 0 or k < 0:
        raise ValueError('negative arguments')
    if k > n:
        return 0
    p = 1
    i = 0
    while i < k:
        p *= n - i
        i += 1
    return p

def __py11_math_comb(n: int, k: int) -> int:
    if n < 0 or k < 0:
        raise ValueError('negative arguments')
    if k > n:
        return 0
    if k > n - k:
        k = n - k
    p = 1
    i = 1
    while i <= k:
        p = (p * (n - i + 1)) // i
        i += 1
    return p
'''
        all_helpers = {n.name: n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
        # Include dependencies and ensure dependencies precede callers.
        dependencies = {'lcm': {'gcd'}}
        for f in tuple(used):
            used.update(dependencies.get(f, set()))
        tree.body = [all_helpers[f'{RESERVED}math_{f}'] for f in ('gcd', 'lcm', 'factorial', 'isqrt', 'perm', 'comb') if f in used] + tree.body

    def _risk_analysis(self, tree: ast.Module):
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        methods = {f'method:{member.name}': member for node in tree.body if isinstance(node, ast.ClassDef)
                   for member in node.body if isinstance(member, ast.FunctionDef)}
        functions = {**funcs, **methods}
        self.risky = {name for name, fn in functions.items()
                      if any(isinstance(n, ast.Raise) or
                             isinstance(n, ast.BinOp) and isinstance(n.op, (ast.FloorDiv, ast.Mod))
                             for n in ast.walk(fn))}
        if 'method:__init__' in self.risky:
            self.fail(methods['method:__init__'], 'exception in __init__ requires constructor-unwind support')
        updated = True
        while updated:
            updated = False
            for name, fn in functions.items():
                if name not in self.risky and has_raising_call(fn, self.risky):
                    self.risky.add(name)
                    updated = True

    def _make_guard(self, symbol: str) -> ast.If:
        return If(C('__py10_is_error', N(symbol)), [ast.Return(value=C('__py10_rethrow', N(symbol)))])

    def _lift_pure_calls(self, expr: ast.expr) -> tuple[list[ast.stmt], ast.expr]:
        """Force ordered evaluation of nonthrowing calls before a risky sibling."""
        if isinstance(expr, ast.Call):
            pre: list[ast.stmt] = []
            args = []
            for arg in expr.args:
                sub, item = self._lift_pure_calls(arg)
                pre.extend(sub)
                args.append(item)
            tmp = self.sym('ordered')
            pre.append(A(tmp, ast.Call(func=expr.func, args=args, keywords=expr.keywords)))
            return pre, N(tmp)
        if isinstance(expr, ast.BinOp):
            a, left = self._lift_pure_calls(expr.left)
            b, right = self._lift_pure_calls(expr.right)
            return a + b, ast.BinOp(left=left, op=expr.op, right=right)
        if isinstance(expr, ast.UnaryOp):
            p, value = self._lift_pure_calls(expr.operand)
            return p, ast.UnaryOp(op=expr.op, operand=value)
        if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
            p, left = self._lift_pure_calls(expr.left)
            q, right = self._lift_pure_calls(expr.comparators[0])
            return p + q, ast.Compare(left=left, ops=expr.ops, comparators=[right])
        if isinstance(expr, ast.Attribute):
            p, recv = self._lift_pure_calls(expr.value)
            return p, ast.Attribute(value=recv, attr=expr.attr, ctx=ast.Load())
        if isinstance(expr, (ast.Name, ast.Constant)):
            return [], expr
        self.fail(expr, 'cannot preserve eager evaluation order before throwing call')

    def _extract(self, expr: ast.expr, *, skip_top: bool = False) -> tuple[list[ast.stmt], ast.expr]:
        """A-normal form for eager expressions with explicitly throwing calls.

        Refuse potentially unsafe reordering of lazy operators. Extract all
        function calls when a risky call is nested; thus earlier calls retain
        Python's left-to-right evaluation and side-effect order.
        """
        has_risk = has_raising_call(expr, self.risky) or any(
            isinstance(n, ast.BinOp) and isinstance(n.op, (ast.FloorDiv, ast.Mod))
            for n in ast.walk(expr))
        if not has_risk:
            return [], expr
        if isinstance(expr, (ast.BoolOp, ast.IfExp, ast.Lambda, ast.NamedExpr,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            self.fail(expr, 'throwing call in lazy/comprehension expression needs CFG lowering')
        if isinstance(expr, ast.Call):
            if expr.keywords or not isinstance(expr.func, (ast.Name, ast.Attribute)):
                self.fail(expr, 'throwing call with dynamic target or keywords needs a call frame')
            pre: list[ast.stmt] = []
            args = []
            for arg in expr.args:
                sub, val = self._extract(arg)
                pre.extend(sub)
                args.append(val)
            new_call = ast.Call(func=expr.func, args=args, keywords=[])
            if not skip_top:
                temp = self.sym('expr')
                pre += [A(temp, new_call), self._make_guard(temp)]
                return pre, N(temp)
            return pre, new_call
        if isinstance(expr, ast.BinOp):
            left, a = self._extract(expr.left)
            right, b = self._extract(expr.right)
            # If the right side may fail, any earlier call on the left must
            # execute first. A-normalize it even when it cannot itself raise
            # an explicit VError.
            if right:
                ordered, a = self._lift_pure_calls(a)
                left.extend(ordered)
            node = ast.BinOp(left=a, op=expr.op, right=b)
            if isinstance(expr.op, (ast.FloorDiv, ast.Mod)):
                tmp = self.sym('div')
                return left + right + [A(tmp, node), self._make_guard(tmp)], N(tmp)
            return left + right, node
        if isinstance(expr, ast.UnaryOp):
            pre, val = self._extract(expr.operand)
            return pre, ast.UnaryOp(op=expr.op, operand=val)
        if isinstance(expr, ast.Compare) and len(expr.ops) == 1:
            lpre, left = self._extract(expr.left)
            rpre, right = self._extract(expr.comparators[0])
            if rpre:
                ordered, left = self._lift_pure_calls(left)
                lpre.extend(ordered)
            return lpre+rpre, ast.Compare(left=left, ops=expr.ops, comparators=[right])
        if isinstance(expr, ast.Attribute):
            pre, val = self._extract(expr.value)
            return pre, ast.Attribute(value=val, attr=expr.attr, ctx=ast.Load())
        self.fail(expr, 'throwing call in unsupported expression context')
        raise AssertionError

    def _return_raise(self, node: ast.Raise, active_exception: str | None):
        if node.cause is not None:
            self.fail(node, 'raise ... from ... needs exception chaining')
        if node.exc is None:
            if active_exception is None:
                self.fail(node, 'bare raise outside except')
            return ast.Return(value=C('__py10_rethrow', N(active_exception)))
        if isinstance(node.exc, ast.Name):
            if active_exception is None or node.exc.id != active_exception:
                self.fail(node, 're-raising exception variables is supported only inside their handler')
            return ast.Return(value=C('__py10_rethrow', N(node.exc.id)))
        if not isinstance(node.exc, ast.Call) or not isinstance(node.exc.func, ast.Name) or node.exc.func.id not in EXCEPTIONS or node.exc.keywords or len(node.exc.args) > 1:
            self.fail(node, 'raise needs a supported built-in exception')
        return ast.Return(value=C('__py10_raise', ast.Constant(node.exc.func.id),
                                  node.exc.args[0] if node.exc.args else ast.Constant('')))

    def _lower_body(self, statements: list[ast.stmt], active_exception: str | None = None,
                    final: list[ast.stmt] | None = None) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for s in statements:
            if isinstance(s, ast.Raise):
                if final:
                    out.extend(copy.deepcopy(final))
                out.append(self._return_raise(s, active_exception))
                break
            if isinstance(s, ast.Return):
                val = s.value or ast.Constant(None)
                pre, new_val = self._extract(val)
                out.extend(pre)
                if final:
                    # Compute return expression before finalizer, as CPython does.
                    tmp = self.sym('return_value')
                    out.append(A(tmp, new_val))
                    out.extend(copy.deepcopy(final))
                    out.append(ast.Return(value=N(tmp)))
                else:
                    out.append(ast.Return(value=new_val))
                break
            if isinstance(s, ast.Try):
                out.extend(self._lower_try(s, active_exception=active_exception, outer_final=final))
                continue
            if isinstance(s, (ast.Assign, ast.AnnAssign)):
                pre, expr = self._extract(s.value)
                out.extend(pre)
                s = copy.deepcopy(s)
                s.value = expr
                out.append(s)
                continue
            if isinstance(s, ast.Expr):
                pre, expr = self._extract(s.value)
                out.extend(pre)
                out.append(ast.Expr(value=expr))
                continue
            if isinstance(s, ast.If):
                pre, cond = self._extract(s.test)
                out.extend(pre)
                out.append(If(cond, self._lower_body(s.body, active_exception, final),
                              self._lower_body(s.orelse, active_exception, final) if s.orelse else []))
                continue
            if isinstance(s, (ast.For, ast.While)):
                if isinstance(s, ast.While) and has_raising_call(s.test, self.risky):
                    self.fail(s, 'throwing call in while test needs repeated unwind state')
                s = copy.deepcopy(s)
                s.body = self._lower_body(s.body, active_exception, final)
                s.orelse = self._lower_body(s.orelse, active_exception, final)
                # The existing v5 while lowering carries only locals already
                # bound at loop entry. Explicit exception temporaries must be
                # seeded before the loop, not conjured at its first iteration.
                seeded = set()
                for inner in ast.walk(s):
                    if isinstance(inner, ast.Assign):
                        for target in inner.targets:
                            if isinstance(target, ast.Name) and target.id.startswith(RESERVED):
                                seeded.add(target.id)
                out.extend(A(name, ast.Constant(0)) for name in sorted(seeded))
                out.append(s)
                continue
            out.append(s)
        return out

    def _lower_try(self, node: ast.Try, active_exception: str | None,
                   outer_final: list[ast.stmt] | None) -> list[ast.stmt]:
        """Explicit error continuation for eager statements and simple branches.

        The error continuation is materialized *inside* the try region, rather
        than returning directly from the enclosing function. This preserves
        handlers and finally for multiple calls and nested eager expressions.
        No implicit Python exceptions, arbitrary loops, or escaping exception
        frames are claimed by this source-level translation.
        """
        if not node.body:
            self.fail(node, 'empty try body')
        if any(isinstance(n, (ast.Return, ast.Break, ast.Continue))
               for s in node.finalbody for n in ast.walk(s)):
            self.fail(node, 'abrupt control flow in finally needs a full completion record')
        if any(isinstance(n, (ast.Raise, ast.Try)) or has_raising_call(n, self.risky)
               for s in node.finalbody for n in ast.walk(s)):
            self.fail(node, 'potentially throwing finally needs a nested unwind frame')
        final = self._lower_body(node.finalbody, active_exception) if node.finalbody else []
        if outer_final:
            final += copy.deepcopy(outer_final)
        for h in node.handlers:
            if any(isinstance(n, (ast.Try, ast.Raise)) or has_raising_call(n, self.risky)
                   for s in h.body for n in ast.walk(s)):
                # Re-raising the current error is supported; dynamically
                # originating *new* errors in a handler needs handler unwind.
                raises = [n for s in h.body for n in ast.walk(s) if isinstance(n, ast.Raise)]
                if not (len(raises) == 1 and raises[0].exc is None and len(h.body) == 1):
                    self.fail(h, 'throwing exception handler requires nested unwind support')
        def exception_handler(error: str) -> list[ast.stmt]:
            errors: list[ast.stmt] = [*copy.deepcopy(final),
                                       ast.Return(value=C('__py10_rethrow', N(error)))]
            for handler in reversed(node.handlers):
                if handler.type is None:
                    match = C('__py10_is_error', N(error))
                elif isinstance(handler.type, ast.Name) and handler.type.id in EXCEPTIONS:
                    match = (C('__py10_is_error', N(error)) if handler.type.id in ('Exception', 'BaseException')
                             else C('__py10_is_exception', N(error), ast.Constant(handler.type.id)))
                else:
                    self.fail(handler, 'except needs a supported built-in exception')
                chosen: list[ast.stmt] = []
                if handler.name:
                    chosen.append(A(handler.name, N(error)))
                body = self._lower_body(handler.body, active_exception=handler.name or error, final=final)
                chosen.extend(body)
                if not any(isinstance(s, ast.Return) for s in body):
                    chosen += copy.deepcopy(final)
                errors = [If(match, chosen, errors)]
            return errors

        success = self._lower_body(node.orelse, active_exception, final)
        if not any(isinstance(s, ast.Return) for s in success):
            success += copy.deepcopy(final)

        def guard_sequence(preamble: list[ast.stmt], tail: list[ast.stmt]) -> list[ast.stmt]:
            """Replace A-normal-form's direct returns by error continuations."""
            result = tail
            for part in reversed(preamble):
                if isinstance(part, ast.If) and (len(part.body) == 1 and
                    isinstance(part.body[0], ast.Return) and
                    isinstance(part.body[0].value, ast.Call) and
                    isinstance(part.body[0].value.func, ast.Name) and
                    part.body[0].value.func.id == '_py10_rethrow'):
                    arg = part.body[0].value.args[0]
                    if not isinstance(arg, ast.Name):
                        self.fail(part, 'unwind error must be held in a variable')
                    result = [If(part.test, exception_handler(arg.id), result)]
                else:
                    result = [part, *result]
            return result

        def chain(stmts: list[ast.stmt], after: list[ast.stmt]) -> list[ast.stmt]:
            if not stmts:
                return after
            s, others = stmts[0], stmts[1:]
            if isinstance(s, (ast.Assign, ast.AnnAssign, ast.Expr)):
                original = s.value
                pre, rewritten = self._extract(original)
                s = copy.deepcopy(s)
                s.value = rewritten
                return guard_sequence(pre, [s, *chain(others, after)])
            if isinstance(s, ast.If):
                pre, condition = self._extract(s.test)
                rest = chain(others, after)
                branch = If(condition, chain(s.body, copy.deepcopy(rest)),
                            chain(s.orelse, copy.deepcopy(rest)))
                return guard_sequence(pre, [branch])
            if isinstance(s, ast.Raise):
                expression = self._return_raise(s, active_exception)
                temp = self.sym('raised')
                return [A(temp, expression.value), *exception_handler(temp)]
            if isinstance(s, ast.Return):
                pre, expression = self._extract(s.value or ast.Constant(None))
                temp = self.sym('try_return')
                return guard_sequence(pre, [A(temp, expression), *copy.deepcopy(final),
                                            ast.Return(value=N(temp))])
            if isinstance(s, ast.Pass):
                return chain(others, after)
            self.fail(s, 'try control flow needs general CFG lowering for this statement')
        return chain(node.body, success)

    def apply(self, source: str) -> str:
        try:
            tree = ast.parse(source, self.source_name)
        except SyntaxError as exc:
            raise TranslationError(str(exc), source=self.source_name) from exc
        self._validate_reserved(tree)
        self._normalize_imports(tree)
        self._generator_to_loop(tree)
        self._flatten_inheritance(tree)
        self._lower_properties(tree)
        self._runtime_math(tree)
        self._risk_analysis(tree)
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                n.body = self._lower_body(n.body)
            elif isinstance(n, ast.ClassDef):
                for member in n.body:
                    if isinstance(member, ast.FunctionDef):
                        member.body = self._lower_body(member.body)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)


class RuntimeLowerer(ExceptionAttributeLowerer):
    def __init__(self, source: str, source_name: str = '<input>', *, show_return=False, threshold=8):
        self.v11_prepass = RuntimeSemanticPass(source_name)
        lowered = self.v11_prepass.apply(source)
        self.v11_lowered = lowered
        super().__init__(lowered, source_name, show_return=show_return, threshold=threshold)

    def expr(self, node, env, heap, lines):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith('_py10_'):
            node = copy.deepcopy(node)
            node.func.id = '__py10_' + node.func.id[len('_py10_'):]
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.FloorDiv, ast.Mod)):
            heap, left, left_type = self.expr(node.left, env, heap, lines)
            heap, right, right_type = self.expr(node.right, env, heap, lines)
            if (left_type, right_type) != ('int', 'int'):
                self.fail('division requires two signed Python integers', node)
            fn = 'safe_floordiv' if isinstance(node.op, ast.FloorDiv) else 'safe_mod'
            return heap, self.bind(f'PyValue.{fn}({left}, {right})', lines), 'int'
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'str':
            if len(node.args) == 1 and not node.keywords:
                # str() is defined here for explicitly represented exceptions.
                heap, value, value_type = self.expr(node.args[0], env, heap, lines)
                if value_type in self.class_fields or value_type.startswith('list[') or value_type == 'object':
                    self.fail('str(object) requires Python-compatible __str__/__repr__ semantics', node)
                return heap, self.bind(f'VStr{{PyExc.message_only({value})}}', lines), 'str'
        return super().expr(node, env, heap, lines)

    def compile(self):
        bend = super().compile()
        helper = '''
def PyValue.safe_floordiv(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{+y}:
      PyValue.safe_div_go(PyInt.is_eq(y, PyPos{Nil{}}), x, y)
    case _ _:
      VError{"TypeError: unsupported operands for //"}

def PyValue.safe_mod(a: PyValue, b: PyValue) -> PyValue:
  match a b:
    case VInt{x} VInt{+y}:
      PyValue.safe_mod_go(PyInt.is_eq(y, PyPos{Nil{}}), x, y)
    case _ _:
      VError{"TypeError: unsupported operands for %"}

def PyValue.safe_div_go(zero: Bool, a: PyInt, b: PyInt) -> PyValue:
  match zero:
    case True{}:
      VError{"ZeroDivisionError: integer division or modulo by zero"}
    case False{}:
      VInt{PyInt.floordiv(a, b)}

def PyValue.safe_mod_go(zero: Bool, a: PyInt, b: PyInt) -> PyValue:
  match zero:
    case True{}:
      VError{"ZeroDivisionError: integer division or modulo by zero"}
    case False{}:
      VInt{PyInt.mod(a, b)}

def PyExc.message_only(value: PyValue) -> String:
  match value:
    case VError{message}:
      PyExc.after_colon(message)
    case VInt{x}:
      PyInt.show(x)
    case VBool{b}:
      PyValue.show_bool(b)
    case VStr{text}:
      text
    case VNone{}:
      "None"
    case VRef{address}:
      "<object>"
    case VWeak{address}:
      "<weakref>"

def PyExc.after_colon(message: String) -> String:
  match message:
    case SNil{}:
      ""
    case SCon{Chr{c}, tail}:
      PyExc.scan_colon(c, tail)

def PyExc.scan_colon(c: U32, rest: String) -> String:
  PyExc.scan_colon_choice(U32.is_eq(c, 58), rest)

def PyExc.scan_colon_choice(found: Bool, rest: String) -> String:
  match found:
    case True{}:
      PyExc.skip_separator(rest)
    case False{}:
      PyExc.after_colon(rest)

def PyExc.skip_separator(rest: String) -> String:
  match rest:
    case SCon{Chr{+c}, tail}:
      PyExc.skip_separator_choice(U32.is_eq(c, 32), c, tail)
    case SNil{}:
      ""

def PyExc.skip_separator_choice(space: Bool, c: U32, tail: String) -> String:
  match space:
    case True{}:
      tail
    case False{}:
      SCon{Chr{c}, tail}
'''
        from tools.python3_to_bend2.declaration_order import reorder_bend_declarations
        bend = bend.replace('def main() -> IO(Unit):', helper + '\n\ndef main() -> IO(Unit):', 1)
        if self.v11_prepass.parents:
            ancestry = []
            for cls in sorted(self.v11_prepass.parents):
                ancestor = self.v11_prepass.parents[cls]
                while ancestor:
                    ancestry.append(f'Bool.and(PyStr.equal(tag, "{cls}"), PyStr.equal(expected, "{ancestor}"))')
                    ancestor = self.v11_prepass.parents.get(ancestor)
            exp = 'PyStr.equal(tag, expected)'
            for candidate in ancestry:
                exp = f'Bool.or({exp}, {candidate})'
            subclass = f'def PyType.is_subclass(+tag: String, +expected: String) -> Bool:\n  {exp}\n'
            if 'PyStr.equal(tag, expected)' not in bend:
                raise AssertionError('subclass support requires an identity predicate')
            bend = bend.replace('PyStr.equal(tag, expected)', 'PyType.is_subclass(tag, expected)', 1)
            bend = bend.replace('def main() -> IO(Unit):', subclass+'\ndef main() -> IO(Unit):', 1)
        bend = reorder_bend_declarations(bend)
        if 'Process.run(' in bend or 'python_compat' in bend:
            raise AssertionError('native runtime must not delegate to CPython')
        return bend


def translate_runtime_file(path: Path, *, show_return=False, threshold=8) -> str:
    source = path.read_text(encoding='utf-8')
    root = ast.parse(source, str(path))
    pure = {'math', 'operator', 'gc', 'weakref', 'typing', 'dataclasses', '__future__'}
    local = any(
        isinstance(n, ast.Import) and any(alias.name not in pure for alias in n.names) or
        isinstance(n, ast.ImportFrom) and n.module not in pure
        for n in root.body)
    if local:
        if any(isinstance(n, (ast.Import, ast.ImportFrom)) and
               (n.module in {'math', 'operator'} if isinstance(n, ast.ImportFrom) else
                any(a.name in {'math', 'operator'} for a in n.names)) for n in root.body):
            raise TranslationError('mixing native stdlib imports with linked local modules needs unified import resolution', source=str(path))
        source = RuntimeModuleLinker(path).link()
        keep = {a.name for n in root.body if isinstance(n, ast.Import) for a in n.names
                if a.name in {'gc', 'weakref'}}
        source = ''.join(f'import {name}\n' for name in sorted(keep)) + source
    return RuntimeLowerer(source, str(path), show_return=show_return, threshold=threshold).compile()


V11_CASES = {
    'nested_exception_math': '''
def fail(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    return n

def main() -> int:
    try:
        n = fail(-1)
    except ValueError:
        n = 10
    return 5 + n
''',
    'multi_statement_try': '''
def risky(n: int) -> int:
    if n < 0:
        raise ValueError('bad')
    return n

def side() -> int:
    print(19)
    return 2

def main() -> int:
    a = 0
    b = 0
    try:
        a = risky(3)
        b = side() + risky(-1)
    except ValueError as exc:
        b = 9
    finally:
        print(7)
    return a + b
''',
    'nested_exception_caught': '''
def fail(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    return n

def main() -> int:
    try:
        x = fail(-1)
    except ValueError as exc:
        x = 9
    finally:
        print(3)
    return x
''',
    'finally_success': '''
def check(n: int) -> int:
    if n < 0:
        raise ValueError('bad')
    return n

def main() -> int:
    try:
        v = check(6)
    except ValueError:
        v = 99
    else:
        v = v + 2
    finally:
        print(17)
    return v
''',
    'function_local_math_import': '''
def main() -> int:
    import math as m
    n = 8
    return m.factorial(n)
''',
    'dynamic_gcd': '''
import math

def main() -> int:
    a = 104
    b = 40
    return math.gcd(a, b)
''',
    'dynamic_factorial': '''
import math

def main() -> int:
    n = 9
    return math.factorial(n)
''',
    'dynamic_combinations': '''
from math import comb, perm, lcm

def main() -> int:
    a = 25
    b = 7
    return comb(a, b) + perm(8, 3) + lcm(18, 24)
''',
    'dynamic_isqrt': '''
from math import isqrt

def main() -> int:
    n = 12345678987654321
    return isqrt(n)
''',
    'exception_message': '''
def fail(n: int) -> int:
    raise ValueError('precision')

def main() -> int:
    try:
        out = fail(1)
    except ValueError as exc:
        print(str(exc))
        out = 8
    return out
''',
    'reraise': '''
def fail(n: int) -> int:
    raise ValueError('again')

def middle(n: int) -> int:
    try:
        x = fail(n)
    except ValueError:
        raise
    return x

def main() -> int:
    try:
        x = middle(1)
    except ValueError:
        x = 9
    return x
''',
    'method_exception': '''
class Box:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value
    def fail(self) -> int:
        raise ValueError('box error')

def main() -> int:
    b = Box(2)
    try:
        x = b.fail()
    except Exception as error:
        x = 10
    return x
''',
    'inherited_isinstance': '''
class Base:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value
class Derived(Base):
    pass

def main() -> int:
    x = Derived(5)
    if isinstance(x, Base):
        return 1
    return 0
''',
    'single_inheritance': '''
class Base:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value
    def increment(self) -> None:
        self.value += 1

class Derived(Base):
    def twice(self) -> None:
        self.increment()
        self.increment()

def main() -> int:
    x = Derived(5)
    x.twice()
    return x.value
''',
    'property_getter_setter': '''
class Box:
    value: int
    def __init__(self, n: int) -> None:
        self.value = n
    @property
    def doubled(self) -> int:
        return self.value * 2
    @doubled.setter
    def doubled(self, n: int) -> None:
        self.value = n // 2

def main() -> int:
    x = Box(3)
    result = x.doubled
    x.doubled = 20
    return result + x.doubled
''',
    'generator_exhaustion_caught': '''
def squares(n: int):
    for k in range(n):
        yield k * k

def main() -> int:
    gen = squares(1)
    a = next(gen)
    try:
        b = next(gen)
    except StopIteration:
        b = 5
    return a + b
''',
    'generator_alias_next': '''
def squares(n: int):
    for k in range(n):
        yield k * k

def main() -> int:
    gen = squares(5)
    a = next(gen)
    other = gen
    b = next(other)
    c = next(gen)
    return a + b + c
''',
    'generator_pure_range': '''
def squares(n: int):
    for k in range(n):
        yield k * k

def main() -> int:
    total = 0
    for x in squares(6):
        total += x
    return total
''',
}


def run_smoke(*, bend='bend', wsl=False, source_only=False, threshold=8) -> int:
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_V11 BLOCKED bend-executable-missing native_executed=0')
        return 2
    generated = executed = failed = 0
    with tempfile.TemporaryDirectory(prefix='pybend-v11-') as td:
        for name, source in V11_CASES.items():
            p = Path(td) / (name + '.py')
            b = p.with_suffix('.bend')
            try:
                p.write_text(source, encoding='utf-8')
                b.write_text(translate_runtime_file(p, show_return=True, threshold=threshold), encoding='utf-8')
                generated += 1
                if source_only:
                    print(f'PY3_BEND_V11 {name} SOURCE_ONLY')
                    continue
                ok, verdict = run_bend_gate(b, bend=bend, wsl=wsl)
                if not ok:
                    raise RuntimeError('BEND_CHECK_FAILED ' + verdict)
                oracle = subprocess.run([sys.executable, '-B', '-c',
                    'import runpy; ns=runpy.run_path(' + repr(str(p)) + '); print(ns["main"]())'],
                    capture_output=True, text=True, timeout=30)
                if oracle.returncode:
                    # A deliberately uncaught Python exception is not eligible
                    # for stdout differential testing in this harness.
                    raise RuntimeError('PY_ORACLE_FAILED ' + oracle.stderr[-800:])
                actual = subprocess.run(bend_command(b, bend=bend, wsl=wsl), capture_output=True,
                                        text=True, timeout=120)
                if actual.returncode or actual.stdout != oracle.stdout:
                    raise RuntimeError(f'DIFFERENTIAL_FAILED {actual.returncode=} {actual.stdout=!r} {oracle.stdout=!r}')
                executed += 1
                print(f'PY3_BEND_V11 {name} BEND_NATIVE_PASS')
            except (OSError, AssertionError, RuntimeError, TranslationError, ValueError,
                    subprocess.TimeoutExpired) as exc:
                failed += 1
                print(f'PY3_BEND_V11 {name} FAIL {exc}')
    print(f'PY3_BEND_V11 SUMMARY generated={generated} executed={executed} failed={failed} certified={executed == len(V11_CASES) and failed == 0}')
    return 0 if failed == 0 and (source_only or executed == len(V11_CASES)) else 2


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    tr = subs.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--threshold', type=int, default=8)
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    sm = subs.add_parser('smoke')
    sm.add_argument('--source-only', action='store_true')
    sm.add_argument('--bend', default='bend')
    sm.add_argument('--wsl', action='store_true')
    sm.add_argument('--threshold', type=int, default=8)
    a = p.parse_args(argv)
    if a.command == 'smoke':
        return run_smoke(bend=a.bend, wsl=a.wsl, source_only=a.source_only, threshold=a.threshold)
    try:
        source = translate_runtime_file(a.input, show_return=a.show_return, threshold=a.threshold)
        target = a.output or a.input.with_suffix('.bend')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding='utf-8')
        print(f'PY3_BEND_V11 SOURCE_EMITTED {target} certified=false')
        if a.require_bend:
            ok, verdict = run_bend_gate(target, a.bend, wsl=a.wsl)
            print('PY3_BEND_V11 ' + verdict)
            return 0 if ok else 2
        return 0
    except (OSError, SyntaxError, TranslationError, AssertionError, ValueError) as exc:
        print('PY3_BEND_V11 ERROR ' + str(exc), file=sys.stderr)
        return 2
