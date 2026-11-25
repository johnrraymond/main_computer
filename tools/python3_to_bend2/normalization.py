"""Python 3 -> Bend 2 v13: source-level normalization of high-frequency constructs.

No host-Python delegation in the generated Bend program. This pass accepts only
constructions whose evaluation order and binding semantics it can represent.
Bend compilation and differential execution remain mandatory for certification.
"""
from __future__ import annotations

import argparse
import ast
import copy
from pathlib import Path
import shutil
import sys
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.inheritance import InheritanceLowerer, translate_inheritance_file
from tools.python3_to_bend2.heap import Local


def N(name: str, *, store: bool = False) -> ast.Name:
    return ast.Name(id=name, ctx=ast.Store() if store else ast.Load())


def C(value):
    return ast.Constant(value=value)


def call(fn, *args):
    return ast.Call(func=N(fn) if isinstance(fn, str) else fn, args=list(args), keywords=[])


def assign(name: str, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[N(name, store=True)], value=value)


def use_names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


class NormalizationPass:
    def __init__(self, filename='<input>'):
        self.filename = filename
        self.counter = 0
        self.reserved = set()
        self.signatures = {}

    def fail(self, node, message):
        raise TranslationError(f'{message} (line {getattr(node, "lineno", "?")})', source=self.filename)

    def fresh(self, prefix='temp'):
        while True:
            self.counter += 1
            candidate = f'_py13_{prefix}_{self.counter}'
            if candidate not in self.reserved:
                self.reserved.add(candidate)
                return candidate

    def _strings(self, tree: ast.Module):
        """Lower bounded formatting without inventing Python's format protocol."""
        import string
        outer = self
        class Strings(ast.NodeTransformer):
            def _concat(self, chunks):
                if not chunks:
                    return C('')
                result = chunks[0]
                for part in chunks[1:]:
                    result = ast.BinOp(left=result, op=ast.Add(), right=part)
                return result

            def visit_JoinedStr(self, node):
                chunks = []
                for value in node.values:
                    if isinstance(value, ast.Constant) and type(value.value) is str:
                        chunks.append(C(value.value))
                    elif isinstance(value, ast.FormattedValue) and value.format_spec is None and value.conversion in (-1, 115):
                        chunks.append(call('str', self.visit(value.value)))
                    else:
                        outer.fail(value, 'f-string format spec or conversion requires the format runtime')
                return ast.copy_location(self._concat(chunks), node)

            def visit_Call(self, node):
                node = self.generic_visit(node)
                if (not isinstance(node.func, ast.Attribute) or node.func.attr != 'format'
                        or not isinstance(node.func.value, ast.Constant)
                        or type(node.func.value.value) is not str):
                    return node
                if node.keywords:
                    outer.fail(node, 'format keyword fields are not yet supported')
                if any(not isinstance(arg, (ast.Constant, ast.Name)) for arg in node.args):
                    outer.fail(node, 'formatting with effectful arguments requires ordered temporaries')
                formatter = string.Formatter()
                chunks = []
                automatic = 0
                try:
                    fields = list(formatter.parse(node.func.value.value))
                except ValueError as exc:
                    outer.fail(node, f'invalid format string: {exc}')
                for literal, field, format_spec, conversion in fields:
                    if literal:
                        chunks.append(C(literal))
                    if field is not None:
                        if format_spec or conversion:
                            outer.fail(node, 'format specifier/conversion not supported')
                        if field == '':
                            index = automatic
                            automatic += 1
                        elif field.isdecimal():
                            index = int(field)
                        else:
                            outer.fail(node, 'format requires numeric or automatic fields')
                        if index >= len(node.args):
                            outer.fail(node, 'missing format argument')
                        chunks.append(call('str', copy.deepcopy(node.args[index])))
                if not chunks:
                    return C('')
                return ast.copy_location(self._concat(chunks), node)
        return Strings().visit(tree)

    def _varargs(self, tree: ast.Module):
        # Monomorphic read-only varargs can be represented as an owned list.
        # No escaping tuple identity, aliasing, mutation, or **kwargs support.
        varargs = {}
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or fn.args.vararg is None:
                continue
            if fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.defaults or fn.args.kwarg or fn.decorator_list:
                self.fail(fn, 'varargs with mixed signatures require general argument binding')
            if fn.args.args:
                self.fail(fn, 'varargs plus fixed arguments are not yet supported')
            arg = fn.args.vararg
            if not isinstance(arg.annotation, ast.Name) or arg.annotation.id not in ('int','str','bool'):
                self.fail(fn, 'varargs must have a primitive element annotation')
            for node in ast.walk(fn):
                if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == arg.arg and
                    not any(isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                            and parent.func.id == 'len' and node in parent.args for parent in ast.walk(fn))):
                    self.fail(fn, 'varargs tuple can only be observed through len()')
            fn.args.args = [ast.arg(arg=arg.arg, annotation=ast.Subscript(value=N('list'), slice=arg.annotation, ctx=ast.Load()))]
            fn.args.vararg = None
            varargs[fn.name] = 1
        if not varargs:
            return tree
        outer = self
        class Rewrite(ast.NodeTransformer):
            def visit_Call(self, node):
                node = self.generic_visit(node)
                if isinstance(node.func, ast.Name) and node.func.id in varargs:
                    if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                        outer.fail(node, 'varargs call uses keyword or iterable unpack')
                    node.args = [ast.List(elts=node.args, ctx=ast.Load())]
                return node
        return Rewrite().visit(tree)

    def _infer_simple_functions(self, tree: ast.Module):
        # Only a single, unambiguous integer monomorphization is accepted;
        # dynamic dispatch requires runtime values and is not inferred here.
        definitions = {n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        calls = {}
        for fn in definitions.values():
            for callnode in ast.walk(fn):
                if isinstance(callnode,ast.Call) and isinstance(callnode.func,ast.Name) and callnode.func.id in definitions:
                    calls.setdefault(callnode.func.id, []).append(callnode)
        for name, fn in definitions.items():
            missing = [i for i,a in enumerate(fn.args.args) if a.annotation is None]
            if not missing and fn.returns is not None:
                continue
            if fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.defaults or fn.args.posonlyargs:
                continue
            usages = calls.get(name, [])
            if not usages:
                continue
            # Values from a statically typed int parameter may be used too,
            # but that requires typeflow; use literal ints for this pass.
            if all(len(c.args)==len(fn.args.args) and not c.keywords and all(
                    type(c.args[i].value) is int if isinstance(c.args[i],ast.Constant) else False
                    for i in missing) for c in usages):
                for i in missing:
                    fn.args.args[i].annotation = N('int')
                if fn.returns is None and len(fn.body)==1 and isinstance(fn.body[0], ast.Return):
                    # Infer int only for safe integer arithmetic expressions.
                    expr=fn.body[0].value
                    if expr is not None and all(isinstance(n,(ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.FloorDiv,
                        ast.Mod,ast.Pow,ast.UnaryOp,ast.USub,ast.Name,ast.Constant,ast.Load))
                        for n in ast.walk(expr)) and all(type(n.value) is int for n in ast.walk(expr) if isinstance(n,ast.Constant)):
                        fn.returns = N('int')
        return tree

    def _lift_local_functions(self, tree: ast.Module):
        """Lift nonescaping integer closures and pure integer lambdas."""
        lifted = []
        for fn in (n for n in tree.body if isinstance(n,ast.FunctionDef)):
            body=[]
            replacements={}
            locals_int={a.arg for a in fn.args.args if isinstance(a.annotation,ast.Name) and a.annotation.id=='int'}
            for stmt in fn.body:
                if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name) and isinstance(stmt.value,ast.Constant) and type(stmt.value.value) is int:
                    locals_int.add(stmt.targets[0].id)
                if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name) and isinstance(stmt.value,ast.Lambda):
                    lam=stmt.value
                    if lam.args.vararg or lam.args.kwarg or lam.args.kwonlyargs or lam.args.defaults or lam.args.posonlyargs:
                        self.fail(stmt, 'lambda requires a positional-only-free fixed signature')
                    name=stmt.targets[0].id
                    paramnames=[arg.arg for arg in lam.args.args]
                    if (use_names(lam.body) - set(paramnames)):
                        self.fail(stmt, 'capturing lambda needs a closure environment')
                    if not all(isinstance(x,(ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.Name,ast.Constant,ast.Load)) for x in ast.walk(lam.body)):
                        self.fail(stmt, 'lambda result needs supported pure integer arithmetic')
                    target=self.fresh('lambda')
                    lifted.append(ast.FunctionDef(name=target,
                        args=ast.arguments(posonlyargs=[],args=[ast.arg(arg=a,annotation=N('int')) for a in paramnames],
                        kwonlyargs=[],kw_defaults=[],vararg=None,kwarg=None,defaults=[]),
                        body=[ast.Return(value=copy.deepcopy(lam.body))],decorator_list=[],returns=N('int'),type_comment=None))
                    replacements[name]=(target,[])
                    continue
                if isinstance(stmt,ast.FunctionDef):
                    if stmt.decorator_list or stmt.args.vararg or stmt.args.kwarg or stmt.args.defaults or stmt.args.kwonlyargs:
                        self.fail(stmt, 'escaping or flexible nested function requires a closure runtime')
                    if any(isinstance(n,(ast.Nonlocal,ast.Global,ast.Yield,ast.YieldFrom)) for n in ast.walk(stmt)):
                        self.fail(stmt,'mutating closure or generator needs a resumable lexical frame')
                    nested_params={a.arg for a in stmt.args.args}
                    captured=use_names(stmt)-nested_params-set({'int','bool','str','None'})
                    captured.discard(stmt.name)
                    if captured-set(locals_int):
                        self.fail(stmt,'nested function captures non-integer or unbound value')
                    if any(not isinstance(a.annotation,ast.Name) for a in stmt.args.args):
                        self.fail(stmt,'nested function parameters require annotations')
                    target=self.fresh('closure')
                    newfn=copy.deepcopy(stmt)
                    newfn.name=target
                    newfn.args.args=[ast.arg(arg=n,annotation=N('int')) for n in sorted(captured)] + newfn.args.args
                    lifted.append(newfn)
                    replacements[stmt.name]=(target,sorted(captured))
                    continue
                body.append(stmt)
            if not replacements:
                continue
            outer=self
            class Rewrite(ast.NodeTransformer):
                def visit_Call(self,node):
                    node=self.generic_visit(node)
                    if isinstance(node.func,ast.Name) and node.func.id in replacements:
                        target,captured=replacements[node.func.id]
                        if node.keywords:
                            outer.fail(node,'local call with keywords requires call normalization')
                        node.func=N(target)
                        node.args=[N(c) for c in captured] + node.args
                    return node
                def visit_Name(self,node):
                    if isinstance(node.ctx,ast.Load) and node.id in replacements:
                        # Callee names in Call are handled by visit_Call, so
                        # an unhandled appearance would allow function escape.
                        outer.fail(node,'nested callable escapes its lexical scope')
                    return node
            # Separate Call normalization from escape checks to avoid treating
            # the callee identifier as a free value.
            class Guard(ast.NodeVisitor):
                def __init__(self):self.parent=None
                def visit_Call(self,node):
                    if isinstance(node.func,ast.Name) and node.func.id in replacements:
                        for arg in [*node.args,*[k.value for k in node.keywords]]:self.visit(arg)
                        return
                    self.generic_visit(node)
                def visit_Name(self,node):
                    if isinstance(node.ctx,ast.Load) and node.id in replacements:
                        outer.fail(node,'nested callable escapes its lexical scope')
            for stmt in body: Guard().visit(stmt)
            class CallOnly(ast.NodeTransformer):
                def visit_Call(self,node):
                    node=self.generic_visit(node)
                    if isinstance(node.func,ast.Name) and node.func.id in replacements:
                        target,captured=replacements[node.func.id]
                        node.func=N(target)
                        node.args=[N(x) for x in captured]+node.args
                    return node
            fn.body=[CallOnly().visit(stmt) for stmt in body]
        tree.body=lifted+tree.body
        return tree

    def _dataclass_fields(self, tree: ast.Module):
        # Materialize only the field-initialization contract. Other dataclass
        # generated protocols (eq, repr, ordering, frozen, etc.) fail closed.
        has_import = any(isinstance(n,ast.ImportFrom) and n.module == 'dataclasses' and
            any(a.name == 'dataclass' for a in n.names) for n in tree.body)
        if not has_import:
            return tree
        dataclass_names = {n.name for n in tree.body if isinstance(n,ast.ClassDef)
            and any(isinstance(d,ast.Name) and d.id=='dataclass' for d in n.decorator_list)}
        for fn in (n for n in tree.body if isinstance(n,ast.FunctionDef)):
            known_instances=set()
            for node in ast.walk(fn):
                if (isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)):
                    if (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name)
                            and node.value.func.id in dataclass_names):
                        known_instances.add(node.targets[0].id)
                    elif isinstance(node.value,ast.Name) and node.value.id in known_instances:
                        known_instances.add(node.targets[0].id)
            for node in ast.walk(fn):
                if isinstance(node,ast.Compare) and any(isinstance(op,(ast.Eq,ast.NotEq)) for op in node.ops):
                    if any(isinstance(term,ast.Name) and term.id in known_instances or
                           isinstance(term,ast.Call) and isinstance(term.func,ast.Name)
                             and term.func.id in dataclass_names for term in [node.left,*node.comparators]):
                        self.fail(node, 'dataclass structural equality requires generated __eq__ protocol')
        for cls in (n for n in tree.body if isinstance(n,ast.ClassDef)):
            if cls.name not in dataclass_names:
                continue
            if len(cls.decorator_list)!=1 or cls.bases or cls.keywords:
                self.fail(cls, 'dataclass decorators or inheritance unsupported')
            if any(isinstance(x,ast.FunctionDef) and x.name=='__init__' for x in cls.body):
                self.fail(cls, 'dataclass custom __init__ needs decorator-aware constructor handling')
            fields = [x for x in cls.body if isinstance(x,ast.AnnAssign) and isinstance(x.target,ast.Name)]
            if any(x.value is not None for x in fields):
                self.fail(cls, 'dataclass default fields require default_factory semantics')
            assignments = [ast.Assign(targets=[ast.Attribute(value=N('self'),attr=x.target.id,ctx=ast.Store())],
                        value=N(x.target.id)) for x in fields]
            init=ast.FunctionDef(name='__init__',args=ast.arguments(posonlyargs=[],
                args=[ast.arg(arg='self')]+[ast.arg(arg=x.target.id,annotation=copy.deepcopy(x.annotation)) for x in fields],
                vararg=None,kwonlyargs=[],kw_defaults=[],kwarg=None,defaults=[]),
                body=assignments or [ast.Pass()], decorator_list=[],returns=C(None),type_comment=None)
            cls.body.append(init)
            cls.decorator_list=[]
        tree.body = [n for n in tree.body if not (isinstance(n,ast.ImportFrom) and n.module=='dataclasses'
            and all(a.name=='dataclass' for a in n.names))]
        return tree

    def _direct_properties(self, tree: ast.Module):
        properties = {cls.name:{n.name for n in cls.body if isinstance(n,ast.FunctionDef) and
            any(isinstance(d,ast.Name) and d.id=='property' for d in n.decorator_list)}
            for cls in tree.body if isinstance(cls,ast.ClassDef)}
        for fn in (n for n in tree.body if isinstance(n,ast.FunctionDef)):
            new = []
            for stmt in fn.body:
                expression = stmt.value if isinstance(stmt,(ast.Return,ast.Assign)) else None
                if (isinstance(expression,ast.Attribute) and isinstance(expression.value,ast.Call)
                    and isinstance(expression.value.func,ast.Name)
                    and expression.attr in properties.get(expression.value.func.id,set())):
                    temp = self.fresh('property')
                    new.append(assign(temp,expression.value))
                    stmt.value = ast.Attribute(value=N(temp), attr=expression.attr,ctx=ast.Load())
                new.append(stmt)
            fn.body=new
        return tree

    def _signature_normalize(self, tree: ast.Module):
        # Only top-level functions: methods have class-specific dispatch and
        # must not be rewritten without updating constructor resolution.
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            args = node.args
            if args.posonlyargs or args.vararg or args.kwarg or node.decorator_list:
                continue
            positional = args.args
            defaults = args.defaults
            positional_defaults = {p.arg: copy.deepcopy(d) for p, d in zip(positional[len(positional)-len(defaults):], defaults)}
            for default in [*defaults, *args.kw_defaults]:
                if default is not None and not isinstance(default, ast.Constant):
                    self.fail(default, 'default argument must be an immutable literal (definition-time evaluation)')
            if args.kwonlyargs or defaults:
                self.signatures[node.name] = ([(x.arg, x.annotation) for x in positional + args.kwonlyargs],
                    {**positional_defaults, **{a.arg: copy.deepcopy(d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None}},
                    {a.arg for a in args.kwonlyargs}, len(positional))
                args.args = positional + args.kwonlyargs
                args.defaults = []
                args.kwonlyargs = []
                args.kw_defaults = []

        signatures = self.signatures
        outer = self
        class NormalizeCalls(ast.NodeTransformer):
            def visit_Call(self, node):
                node = self.generic_visit(node)
                if not isinstance(node.func, ast.Name) or node.func.id not in signatures:
                    return node
                formal, defaults, kwonly, positional_count = signatures[node.func.id]
                if len(node.args) > positional_count or any(k.arg is None for k in node.keywords):
                    outer.fail(node, 'excess positional args or dynamic **kwargs require a call binder')
                byname = {}
                for (name, _), value in zip(formal, node.args):
                    byname[name] = value
                for keyword in node.keywords:
                    if keyword.arg not in {x[0] for x in formal} or keyword.arg in byname:
                        outer.fail(node, 'unknown or duplicate keyword argument')
                    byname[keyword.arg] = keyword.value
                if any(name not in byname and name not in defaults for name,_ in formal):
                    outer.fail(node, 'missing required argument')
                # Reordering side-effecting argument expressions is unsound.
                original = [*node.args, *[k.value for k in node.keywords]]
                arranged = [byname[name] for name,_ in formal if name in byname]
                if (original != arranged and any(not isinstance(x, (ast.Constant, ast.Name)) for x in original)):
                    outer.fail(node, 'reordered arguments with effects need evaluated argument temporaries')
                node.args = [byname[name] if name in byname else copy.deepcopy(defaults[name])
                             for name,_ in formal]
                node.keywords = []
                return node
        tree = NormalizeCalls().visit(tree)
        return tree

    def _match(self, stmt: ast.Match) -> list[ast.stmt]:
        # Exactly one evaluation of subject. No capture variables, guards, or
        # sequence/class patterns: those need pattern binding and rollback.
        holder = self.fresh('match')
        cases = list(stmt.cases)
        alternative = []
        saw_wildcard = False
        for case in reversed(cases):
            p = case.pattern
            if isinstance(p, ast.MatchAs) and p.name is None and p.pattern is None:
                if saw_wildcard or case.guard is not None:
                    self.fail(case, 'ambiguous or guarded wildcard')
                alternative = case.body
                saw_wildcard = True
                continue
            if isinstance(p, ast.MatchValue) and isinstance(p.value, ast.Constant) and type(p.value.value) in (int, str, bool):
                test = ast.Compare(left=N(holder), ops=[ast.Eq()], comparators=[p.value])
            elif isinstance(p, ast.MatchSingleton) and p.value is None:
                test = ast.Compare(left=N(holder), ops=[ast.Is()], comparators=[C(None)])
            else:
                self.fail(case, 'pattern needs general destructuring/match runtime')
            if case.guard is not None:
                self.fail(case, 'match guards need a pattern-binding scope')
            alternative = [ast.If(test=test, body=case.body, orelse=alternative)]
        return [assign(holder, stmt.subject), *alternative]

    def _walrus_if(self, stmt: ast.If) -> list[ast.stmt]:
        test = stmt.test
        if isinstance(test, ast.NamedExpr) and isinstance(test.target, ast.Name):
            stmt.test = N(test.target.id)
            return [assign(test.target.id, test.value), stmt]
        if (isinstance(test, ast.Compare) and isinstance(test.left, ast.NamedExpr)
                and isinstance(test.left.target, ast.Name)):
            named = test.left
            test.left = N(named.target.id)
            if any(isinstance(n, ast.NamedExpr) for n in ast.walk(test)):
                self.fail(stmt, 'more than one walrus needs expression-level control-flow lowering')
            return [assign(named.target.id, named.value), stmt]
        if any(isinstance(n, ast.NamedExpr) for n in ast.walk(test)):
            self.fail(stmt, 'walrus inside short-circuit tests needs lazy expression lowering')
        return [stmt]

    def _for_list(self, stmt: ast.For, following: list[ast.stmt], initialized: set[str]) -> list[ast.stmt] | None:
        if isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name) and stmt.iter.func.id == 'range':
            return None
        enumerated = (isinstance(stmt.iter, ast.Call) and isinstance(stmt.iter.func, ast.Name)
                      and stmt.iter.func.id == 'enumerate')
        if enumerated:
            if len(stmt.iter.args) not in (1,2) or stmt.iter.keywords:
                self.fail(stmt, 'enumerate expects an iterable and optional start')
            seq = stmt.iter.args[0]
            offset = stmt.iter.args[1] if len(stmt.iter.args) == 2 else C(0)
            if not (isinstance(stmt.target, (ast.Tuple, ast.List)) and len(stmt.target.elts) == 2
                    and all(isinstance(e, ast.Name) for e in stmt.target.elts)):
                self.fail(stmt, 'enumerate requires a two-name target')
            targets = [e.id for e in stmt.target.elts]
        else:
            seq = stmt.iter
            if not isinstance(stmt.target, ast.Name):
                self.fail(stmt, 'list loop target must be a name')
            targets = [stmt.target.id]
            offset = None
        if not (isinstance(seq, (ast.List, ast.Name)) or
                isinstance(seq, ast.Tuple) and all(isinstance(x, ast.Constant) for x in seq.elts)):
            self.fail(stmt, 'general iteration currently requires a known list value')
        if isinstance(seq, ast.Tuple):
            seq = ast.List(elts=seq.elts, ctx=ast.Load())
        # A list iterator consults the list's current length and index on each
        # iteration, including mutations performed by the loop body.
        sequence = self.fresh('iterlist')
        index = self.fresh('iterindex')
        pre = [assign(sequence, seq), assign(index, C(0))]
        for name in targets:
            if name not in initialized:
                if any(name in use_names(n) for n in following):
                    self.fail(stmt, 'list loop target may be unbound after an empty iterable')
                pre.append(assign(name, C(0)))  # invisible outside this lowered loop
        init = []
        if enumerated:
            start = self.fresh('enumeratestart')
            pre.append(assign(start, offset))
            init.append(assign(targets[0], ast.BinOp(left=N(index), op=ast.Add(), right=N(start))))
            init.append(assign(targets[1], ast.Subscript(value=N(sequence), slice=N(index), ctx=ast.Load())))
        else:
            init.append(assign(targets[0], ast.Subscript(value=N(sequence), slice=N(index), ctx=ast.Load())))
        # Advancing the hidden cursor BEFORE the body ensures continue does not
        # skip the increment and breaking skips the else suite.
        init.append(ast.AugAssign(target=N(index,store=True), op=ast.Add(), value=C(1)))
        cond = ast.Compare(left=N(index), ops=[ast.Lt()], comparators=[call('len', N(sequence))])
        return pre + [ast.While(test=cond, body=init + stmt.body, orelse=stmt.orelse)]

    def _unpack_literal(self, stmt: ast.Assign) -> list[ast.stmt] | None:
        if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], (ast.Tuple, ast.List)):
            return None
        target = stmt.targets[0]
        if not isinstance(stmt.value, (ast.Tuple, ast.List)):
            self.fail(stmt, 'dynamic iterable unpacking requires length checks and iterator protocol')
        items = stmt.value.elts
        names = target.elts
        if not all(isinstance(x, (ast.Name, ast.Starred)) and (not isinstance(x, ast.Starred)
                or isinstance(x.value, ast.Name)) for x in names):
            self.fail(stmt, 'nested unpacking requires recursive assignment lowering')
        stars = [i for i, n in enumerate(names) if isinstance(n, ast.Starred)]
        if len(stars) > 1:
            self.fail(stmt, 'only one starred unpacking target is permitted')
        if len(items) < len(names)-bool(stars) or (not stars and len(items) != len(names)):
            # Python raises ValueError; the exception path is not the same as
            # a successful static assignment, so do not silently continue.
            self.fail(stmt, 'unpacking arity mismatch requires a raised ValueError')
        # Evaluate the complete right side before binding any target. This
        # matters for effects such as a, b = (b, a).
        temporaries = [self.fresh('unpack') for _ in items]
        pre = [assign(temp, item) for temp, item in zip(temporaries, items)]
        if not stars:
            return pre + [assign(t.id, N(tmp)) for t, tmp in zip(names, temporaries)]
        star = stars[0]
        before = [assign(names[i].id, N(temporaries[i])) for i in range(star)]
        remaining = len(names)-star-1
        tail = [assign(names[star+1+i].id, N(temporaries[len(items)-remaining+i])) for i in range(remaining)]
        middle = temporaries[star:len(items)-remaining]
        return pre + before + [assign(names[star].value.id, ast.List(elts=[N(n) for n in middle],ctx=ast.Load()))] + tail

    def _sum_generator(self, value: ast.expr, target: str, stmt: ast.stmt) -> list[ast.stmt] | None:
        if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and
                value.func.id == 'sum' and len(value.args) in (1, 2) and not value.keywords
                and isinstance(value.args[0], ast.GeneratorExp)):
            return None
        gen = value.args[0]
        if len(gen.generators) != 1 or gen.generators[0].is_async:
            self.fail(stmt, 'sum generator requires one synchronous generator')
        g = gen.generators[0]
        if not isinstance(g.target, ast.Name):
            self.fail(stmt, 'sum generator target must be a single name')
        start = value.args[1] if len(value.args) == 2 else C(0)
        if not isinstance(start, ast.Constant) or type(start.value) is not int:
            self.fail(stmt, 'sum start requires immutable integer literal')
        accumulation = ast.AugAssign(target=N(target, store=True), op=ast.Add(), value=gen.elt)
        body = [accumulation]
        if g.ifs:
            test = g.ifs[0] if len(g.ifs) == 1 else ast.BoolOp(op=ast.And(), values=g.ifs)
            body = [ast.If(test=test, body=body, orelse=[])]
        return [assign(target, start), ast.For(target=g.target, iter=g.iter, body=body, orelse=[])]

    def _comprehension(self, value: ast.expr, target_name: str, stmt: ast.stmt) -> list[ast.stmt] | None:
        # Only a single generator, with no async iteration. The element is
        # evaluated on each iteration, not before it; list storage is heap-based.
        if not isinstance(value, (ast.ListComp, ast.DictComp, ast.GeneratorExp)):
            return None
        if len(value.generators) != 1 or value.generators[0].is_async:
            self.fail(stmt, 'comprehension requires one synchronous generator')
        generator = value.generators[0]
        if not isinstance(generator.target, ast.Name):
            self.fail(stmt, 'comprehension target must be a single name')
        if isinstance(value, ast.GeneratorExp):
            self.fail(stmt, 'a generator expression must have a recognized eager consumer')
        if isinstance(value, ast.DictComp):
            start = ast.Dict(keys=[], values=[])
            body = [ast.Assign(targets=[ast.Subscript(value=N(target_name),slice=value.key,ctx=ast.Store())], value=value.value)]
        else:
            start = ast.List(elts=[], ctx=ast.Load())
            body = [ast.Expr(value=call(ast.Attribute(value=N(target_name),attr='append',ctx=ast.Load()), value.elt))]
        if generator.ifs:
            check = generator.ifs[0] if len(generator.ifs) == 1 else ast.BoolOp(op=ast.And(), values=generator.ifs)
            body = [ast.If(test=check, body=body, orelse=[])]
        return [assign(target_name, start), ast.For(target=generator.target, iter=generator.iter, body=body, orelse=[])]

    def _lower_suite(self, body: list[ast.stmt], *, inherited: set[str] | None = None) -> list[ast.stmt]:
        initialized = set(inherited or ())
        out: list[ast.stmt] = []
        for pos, stmt in enumerate(body):
            following = body[pos+1:]
            if isinstance(stmt, ast.FunctionDef):
                stmt.body = self._lower_suite(stmt.body, inherited={x.arg for x in stmt.args.args})
                out.append(stmt)
                continue
            if isinstance(stmt, ast.ClassDef):
                for element in stmt.body:
                    if isinstance(element, ast.FunctionDef):
                        element.body = self._lower_suite(element.body, inherited={x.arg for x in element.args.args})
                out.append(stmt)
                continue
            if isinstance(stmt, ast.Assert):
                exc = call('AssertionError', stmt.msg if stmt.msg is not None else C(''))
                stmt = ast.If(test=ast.UnaryOp(op=ast.Not(), operand=stmt.test),
                    body=[ast.Raise(exc=exc, cause=None)], orelse=[])
            if isinstance(stmt, ast.Match):
                out.extend(self._lower_suite(self._match(stmt), inherited=initialized))
                continue
            if isinstance(stmt, ast.If):
                expanded = self._walrus_if(stmt)
                if len(expanded) > 1:
                    first, stmt = expanded
                    out.append(first)
                    initialized.add(first.targets[0].id)
                stmt.body = self._lower_suite(stmt.body, inherited=initialized)
                stmt.orelse = self._lower_suite(stmt.orelse, inherited=initialized)
                out.append(stmt)
                continue
            if isinstance(stmt, ast.For):
                stmt.body = self._lower_suite(stmt.body, inherited=initialized | {n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name)})
                stmt.orelse = self._lower_suite(stmt.orelse, inherited=initialized)
                expanded = self._for_list(stmt, following, initialized)
                if expanded is None:
                    out.append(stmt)
                else:
                    out.extend(self._lower_suite(expanded, inherited=initialized))
                initialized.update(n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name))
                continue
            if isinstance(stmt, ast.While):
                stmt.body = self._lower_suite(stmt.body, inherited=initialized)
                stmt.orelse = self._lower_suite(stmt.orelse, inherited=initialized)
                out.append(stmt)
                continue
            if isinstance(stmt, ast.Try):
                stmt.body = self._lower_suite(stmt.body, inherited=initialized)
                for handler in stmt.handlers:
                    handler.body = self._lower_suite(handler.body, inherited=initialized | ({handler.name} if handler.name else set()))
                stmt.orelse = self._lower_suite(stmt.orelse, inherited=initialized)
                stmt.finalbody = self._lower_suite(stmt.finalbody, inherited=initialized)
                out.append(stmt)
                continue
            if isinstance(stmt, ast.Assign):
                unpack = self._unpack_literal(stmt)
                if unpack is not None:
                    out.extend(self._lower_suite(unpack, inherited=initialized))
                    initialized.update(n.id for n in ast.walk(stmt.targets[0]) if isinstance(n, ast.Name))
                    continue
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
                expansion = self._comprehension(stmt.value, target, stmt)
                if expansion is None:
                    expansion = self._sum_generator(stmt.value, target, stmt)
                if expansion is not None:
                    # Avoid turning comprehension's isolated target into a
                    # visible variable, including when it shadows an outer one.
                    comp = stmt.value.args[0] if isinstance(stmt.value, ast.Call) else stmt.value
                    gen = comp.generators[0]
                    compvar = gen.target.id
                    if compvar in initialized or any(compvar in use_names(s) for s in following):
                        self.fail(stmt, 'comprehension variable scope needs a distinct lexical frame')
                    out.extend(self._lower_suite(expansion, inherited=initialized))
                    initialized.add(target)
                    continue
                initialized.add(target)
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                temporary = self.fresh('sumresult')
                expanded = self._sum_generator(stmt.value, temporary, stmt)
                if expanded is not None:
                    out.extend(self._lower_suite(expanded, inherited=initialized))
                    out.append(ast.Return(value=N(temporary)))
                    continue
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                initialized.add(stmt.target.id)
            if isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
                initialized.add(stmt.target.id)
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Tuple)):
                name = stmt.targets[0].id
                # It is safe to use a heap-backed list representation only
                # when no Python operation can observe tuple identity/type or
                # mutate it. Return/alias/method/identity use fails closed.
                for future in following:
                    parent = {id(child): p for p in ast.walk(future)
                              for child in ast.iter_child_nodes(p)}
                    for node in ast.walk(future):
                        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name:
                            owner = parent.get(id(node))
                            parent_ok = (isinstance(owner, ast.Subscript)
                                and owner.value is node and isinstance(owner.ctx, ast.Load)) or (
                                    isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name)
                                    and owner.func.id == 'len' and node in owner.args)
                            if not parent_ok:
                                self.fail(stmt, 'tuple escapes restricted read-only lowering')
                        if (isinstance(node, (ast.Assign, ast.AugAssign)) and
                            any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store)
                                for n in ast.walk(node))):
                            self.fail(stmt, 'tuple variable is rebound')
                stmt.value = ast.List(elts=stmt.value.elts, ctx=ast.Load())
            out.append(stmt)
        return out

    def apply(self, source: str):
        tree = ast.parse(source, self.filename)
        self.reserved.update(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))
        self.reserved.update(n.arg for n in ast.walk(tree) if isinstance(n, ast.arg))
        tree = self._strings(tree)
        tree = self._dataclass_fields(tree)
        tree = self._direct_properties(tree)
        tree = self._varargs(tree)
        tree = self._signature_normalize(tree)
        tree = self._infer_simple_functions(tree)
        tree = self._lift_local_functions(tree)
        tree.body = self._lower_suite(tree.body)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)


# Native variable-index lookup traverses list cells by Python's arbitrary-
# precision signed index; this avoids unsafe PyInt -> bounded Nat coercion.
LIST_INDEX_BEND = '''
@unsafe def PyList.get_at(values: List<&2, PyValue>, +index: PyInt) -> PyValue:
  match values:
    case Nil{}:
      VError{"IndexError: list index out of range"}
    case Con{head, tail}:
      PyList.get_choose(PyInt.is_eq(index, PyPos{Nil{}}), head, tail, index)

def PyList.get_choose(zero: Bool, head: PyValue, tail: List<&2, PyValue>, index: PyInt) -> PyValue:
  match zero:
    case True{}:
      head
    case False{}:
      PyList.get_at(tail, PyInt.sub(index, PyPos{[1]}))

def PyList.normalize_index(+index: PyInt, size: Nat) -> PyInt:
  PyList.normalize_case(PyInt.is_lt(index, PyPos{Nil{}}), index, size)

def PyList.normalize_case(negative: Bool, index: PyInt, size: Nat) -> PyInt:
  match negative:
    case True{}:
      PyInt.add(index, PyInt.positive(PyMag.from_nat(size)))
    case False{}:
      index

def PyHeap.list_item_index(h: PyHeap, ref: PyValue, index: PyValue) -> PyValue:
  match ref index:
    case VRef{address} VInt{i}:
      PyObject.list_item_index(PyHeap.read(h, address), i)
    case _ _:
      VError{"TypeError: list indices must be integers"}

def PyObject.list_item_index(object: PyObject, index: PyInt) -> PyValue:
  match object:
    case OList{+values}:
      PyList.get_at(values, PyList.normalize_index(index, List.length(&2, PyValue, values)))
    case _:
      VError{"TypeError: not a list"}
'''

SLICE_BEND = '''
# Indexed list access and unit-step slices, with signed Python bounds.
@unsafe def PyList.slice_go(values: List<&2, PyValue>, +start: PyInt, +stop: PyInt, +at: PyInt) -> List<&2, PyValue>:
  match values:
    case Nil{}:
      Nil{}
    case Con{head, tail}:
      PyList.slice_select(Bool.and(PyInt.is_ge(at, start), PyInt.is_lt(at, stop)), head, PyList.slice_go(tail, start, stop, PyInt.add(at, PyPos{[1]})))

def PyList.slice_select(keep: Bool, head: PyValue, tail: List<&2, PyValue>) -> List<&2, PyValue>:
  match keep:
    case True{}:
      head <> tail
    case False{}:
      tail

def PyList.slice_start(start: PyValue, size: Nat) -> PyInt:
  match start:
    case VNone{}:
      PyPos{Nil{}}
    case VInt{i}:
      PyList.normalize_index(i, size)
    case _:
      PyPos{Nil{}}

def PyList.slice_stop(stop: PyValue, size: Nat) -> PyInt:
  match stop:
    case VNone{}:
      PyInt.positive(PyMag.from_nat(size))
    case VInt{i}:
      PyList.normalize_index(i, size)
    case _:
      PyPos{Nil{}}

def PyObject.slice_list(object: PyObject, start: PyValue, stop: PyValue) -> List<&2, PyValue>:
  match object:
    case OList{+values}:
      PyList.slice_go(values, PyList.slice_start(start, List.length(&2, PyValue, values)), PyList.slice_stop(stop, List.length(&2, PyValue, values)), PyPos{Nil{}})
    case _:
      Nil{}

def PyHeap.list_slice(+h: PyHeap, value: PyValue, start: PyValue, stop: PyValue) -> PyHeap & PyValue:
  match value:
    case VRef{address}:
      PyHeap.new_list(h, PyObject.slice_list(PyHeap.read(h, address), start, stop))
    case _:
      (h, VError{"TypeError: not a list"})

@unsafe def PyStr.slice_go(text: String, +start: PyInt, +stop: PyInt, +at: PyInt) -> String:
  match text:
    case SNil{}:
      SNil{}
    case SCon{head, tail}:
      PyStr.slice_select(Bool.and(PyInt.is_ge(at, start), PyInt.is_lt(at, stop)), head, PyStr.slice_go(tail, start, stop, PyInt.add(at, PyPos{[1]})))

def PyStr.slice_select(keep: Bool, head: Char, tail: String) -> String:
  match keep:
    case True{}:
      SCon{head, tail}
    case False{}:
      tail

def PyValue.str_slice(value: PyValue, start: PyValue, stop: PyValue) -> PyValue:
  match value:
    case VStr{+text}:
      VStr{PyStr.slice_go(text, PyList.slice_start(start, String.length(text)), PyList.slice_stop(stop, String.length(text)), PyPos{Nil{}})}
    case _:
      VError{"TypeError: not a string"}

def PyStr.lower_char(+chr: U32) -> U32:
  PyStr.lower_choice(Bool.and(U32.is_ge(chr, 65), U32.is_le(chr, 90)), chr)

def PyStr.lower_choice(change: Bool, chr: U32) -> U32:
  match change:
    case True{}:
      (chr + 32 : U32)
    case False{}:
      chr

def PyStr.lower(text: String) -> String:
  match text:
    case SNil{}:
      SNil{}
    case SCon{Chr{chr}, tail}:
      SCon{Chr{PyStr.lower_char(chr)}, PyStr.lower(tail)}

def PyStr.space(+chr: U32) -> Bool:
  Bool.or(U32.is_eq(chr, 32), Bool.or(U32.is_eq(chr, 9), Bool.or(U32.is_eq(chr, 10), U32.is_eq(chr, 13))))

def PyStr.trim_lead(text: String) -> String:
  match text:
    case SNil{}:
      SNil{}
    case SCon{Chr{+chr}, tail}:
      PyStr.trim_lead_select(PyStr.space(chr), chr, tail)

def PyStr.trim_lead_select(is_space: Bool, chr: U32, tail: String) -> String:
  match is_space:
    case True{}:
      PyStr.trim_lead(tail)
    case False{}:
      SCon{Chr{chr}, tail}

def PyStr.trim_end(text: String) -> String:
  match text:
    case SNil{}:
      SNil{}
    case SCon{Chr{chr}, tail}:
      PyStr.trim_end_select(chr, PyStr.trim_end(tail))

def PyStr.trim_end_select(+chr: U32, +tail: String) -> String:
  PyStr.trim_end_pick(Bool.and(PyStr.space(chr), Nat.is_eq(String.length(tail), 0n)), chr, tail)

def PyStr.trim_end_pick(drop: Bool, chr: U32, tail: String) -> String:
  match drop:
    case True{}:
      tail
    case False{}:
      SCon{Chr{chr}, tail}

def PyStr.strip(text: String) -> String:
  PyStr.trim_end(PyStr.trim_lead(text))
'''


# Pure character predicates. Membership of an iterated string character in a
# literal string or literal set of characters has no observable side effects.
CHAR_PREDICATES_BEND = r''' 
@unsafe def PyStr.has_char(+ch: U32, allowed: String) -> Bool:
  match allowed:
    case SNil{}:
      False{}
    case SCon{Chr{c}, tail}:
      Bool.or(U32.is_eq(ch, c), PyStr.has_char(ch, tail))

@unsafe def PyStr.all_chars(text: String, +allowed: String) -> Bool:
  match text:
    case SNil{}:
      True{}
    case SCon{Chr{c}, tail}:
      PyStr.all_chars_step(PyStr.has_char(c, allowed), tail, allowed)

@unsafe def PyStr.all_chars_step(ok: Bool, tail: String, allowed: String) -> Bool:
  match ok:
    case True{}:
      PyStr.all_chars(tail, allowed)
    case False{}:
      False{}
'''.strip()


class NormalizationLowerer(InheritanceLowerer):
    def __init__(self, source: str, source_name='<input>', *, show_return=False, threshold=8):
        self.use_char_predicates = False
        self.v13_prepass = NormalizationPass(source_name)
        lowered = self.v13_prepass.apply(source)
        self.v13_lowered = lowered
        super().__init__(lowered, source_name, show_return=show_return, threshold=threshold)

    def _list_element_type(self, node, env):
        if isinstance(node, ast.List):
            types=set()
            for element in node.elts:
                if isinstance(element, ast.UnaryOp) and isinstance(element.op, ast.USub) and isinstance(element.operand,ast.Constant) and type(element.operand.value) is int:
                    types.add('int')
                elif isinstance(element,ast.Constant):
                    types.add({int:'int',bool:'bool',str:'str'}.get(type(element.value),'unknown'))
                elif isinstance(element,ast.Name) and element.id in env:
                    types.add(env[element.id].typ)
                else:
                    types.add('unknown')
            if len(types)==1 and 'unknown' not in types:
                return next(iter(types))
        return super()._list_element_type(node,env)

    def expr(self, node, env, heap, lines):
        if (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], (ast.In, ast.NotIn))
                and isinstance(node.comparators[0], ast.Set)
                and all(isinstance(e, ast.Constant) and type(e.value) is str
                        for e in node.comparators[0].elts)):
            heap, value, typ = self.expr(node.left, env, heap, lines)
            if typ != 'str':
                self.fail('literal string set membership requires a string', node)
            terms = []
            for item in node.comparators[0].elts:
                heap, member, _ = self.expr(item, env, heap, lines)
                terms.append(f'PyStr.equal(PyExc.message({value}), PyExc.message({member}))')
            expr = 'False{}'
            for term in reversed(terms):
                expr = f'Bool.or({term}, {expr})'
            if isinstance(node.ops[0], ast.NotIn):
                expr = f'Bool.not({expr})'
            return heap, self.bind(f'VBool{{{expr}}}', lines), 'bool'
        # Recognize a pure, single-generator character predicate. Do not
        # generalize to arbitrary iterables, expressions or shadowed builtins.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ('all', 'any') and len(node.args) == 1
                and not node.keywords and isinstance(node.args[0], ast.GeneratorExp)):
            name = node.func.id
            gen = node.args[0]
            if name in env or name in self.signatures or name in self.class_fields:
                self.fail('shadowed quantifier builtin', node)
            if len(gen.generators) == 1:
                g = gen.generators[0]
                pred = gen.elt
                if (not g.is_async and not g.ifs and isinstance(g.target, ast.Name)
                        and isinstance(pred, ast.Compare) and len(pred.ops) == 1
                        and isinstance(pred.left, ast.Name) and pred.left.id == g.target.id
                        and ((name == 'all' and isinstance(pred.ops[0], ast.In))
                             or (name == 'any' and isinstance(pred.ops[0], ast.NotIn)))):
                    domain = pred.comparators[0]
                    if isinstance(domain, ast.Constant) and type(domain.value) is str:
                        allowed = domain.value
                    elif isinstance(domain, ast.Set) and all(
                            isinstance(e, ast.Constant) and type(e.value) is str and len(e.value) == 1
                            for e in domain.elts):
                        allowed = ''.join(sorted({e.value for e in domain.elts}))
                    else:
                        self.fail('character quantifier requires a literal character domain', node)
                    heap, text, typ = self.expr(g.iter, env, heap, lines)
                    if typ != 'str':
                        self.fail('character quantifier requires a string iterable', g.iter)
                    heap, alphabet, _ = self.expr(C(allowed), env, heap, lines)
                    self.use_char_predicates = True
                    result = f'PyStr.all_chars(PyExc.message({text}), PyExc.message({alphabet}))'
                    if name == 'any':
                        result = f'Bool.not({result})'
                    return heap, self.bind(f'VBool{{{result}}}', lines), 'bool'
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'startswith' and len(node.args) == 1
                and not node.keywords):
            heap, receiver, receiver_type = self.expr(node.func.value, env, heap, lines)
            heap, prefix, prefix_type = self.expr(node.args[0], env, heap, lines)
            if receiver_type != 'str' or prefix_type != 'str':
                self.fail('startswith requires a string receiver and string prefix', node)
            return heap, self.bind(
                f'VBool{{PyStr.starts_with(PyExc.message({receiver}), PyExc.message({prefix}))}}',
                lines), 'bool'
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                and isinstance(node.slice, ast.Slice)):
            if node.slice.step is not None and not (isinstance(node.slice.step, ast.Constant)
                    and type(node.slice.step.value) is int and node.slice.step.value == 1):
                self.fail('extended slice step requires a general slicing iterator', node)
            if not isinstance(node.value, ast.Name) or node.value.id not in env:
                self.fail('slice receiver must be a statically typed local', node)
            heap, receiver, typ = self.expr(node.value, env, heap, lines)
            bounds=[]
            for bound in (node.slice.lower,node.slice.upper):
                if bound is None:
                    heap,bound_value,_=self.expr(C(None),env,heap,lines)
                else:
                    heap,bound_value,bound_type=self.expr(bound,env,heap,lines)
                    if bound_type != 'int':
                        self.fail('slice bound must be an integer', bound)
                bounds.append(bound_value)
            if typ.startswith('list['):
                heap, result = self.pair(f'PyHeap.list_slice({heap}, {receiver}, {bounds[0]}, {bounds[1]})', lines)
                return heap,result,typ
            if typ=='str':
                return heap,self.bind(f'PyValue.str_slice({receiver}, {bounds[0]}, {bounds[1]})', lines),'str'
            self.fail('slicing supports lists and ASCII strings',node)
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                and not isinstance(node.slice, ast.Slice) and isinstance(node.value, ast.Name)
                and node.value.id in env and env[node.value.id].typ.startswith('list[')):
            heap, receiver, typ = self.expr(node.value, env, heap, lines)
            if typ.startswith('list['):
                heap, key, key_type = self.expr(node.slice, env, heap, lines)
                if key_type != 'int':
                    self.fail('list index requires integer', node)
                return heap, self.bind(f'PyHeap.list_item_index({heap}, {receiver}, {key})', lines), typ[5:-1]
            # Other types including dictionaries are handled by parents.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ('lower','strip') and not node.args and not node.keywords):
            heap,receiver,typ=self.expr(node.func.value,env,heap,lines)
            if typ!='str':
                self.fail('ASCII string method requires string receiver', node)
            method=node.func.attr
            return heap,self.bind(f'VStr{{PyStr.{method}(PyExc.message({receiver}))}}',lines),'str'
        if (isinstance(node,ast.Subscript) and isinstance(node.ctx,ast.Load)
                and isinstance(node.value,ast.Name) and node.value.id in env
                and env[node.value.id].typ.startswith('dict[')):
            heap,receiver,typ=self.expr(node.value,env,heap,lines)
            heap,key,key_type=self.expr(node.slice,env,heap,lines)
            if key_type!='str':
                self.fail('dict indexing requires string key',node)
            return heap,self.bind(f'PyHeap.dict_get({heap}, {receiver}, {key}, VError{{"KeyError: key not found"}})',lines),typ.split(',',1)[1][:-1]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'len' and len(node.args) == 1 and not node.keywords:
            # Strings are immutable Bend strings, not heap objects.
            heap, arg, typ = self.expr(node.args[0], env, heap, lines)
            if typ == 'str':
                return heap, self.bind(f'VInt{{PyInt.positive(PyMag.from_nat(String.length(PyExc.message({arg}))))}}', lines), 'int'
            # Avoid evaluating non-string argument twice by calling parent.
            if typ.startswith(('list[', 'dict[')):
                return heap, self.bind(f'PyHeap.{"list" if typ.startswith("list[") else "dict"}_length({heap}, {arg})', lines), 'int'
            self.fail('len requires string, list, or dictionary', node)
        return super().expr(node, env, heap, lines)

    def compile(self):
        from tools.python3_to_bend2.integers import order_runtime_definitions
        source = super().compile()
        runtime = LIST_INDEX_BEND + '\n' + SLICE_BEND
        if self.use_char_predicates:
            runtime += '\n' + CHAR_PREDICATES_BEND
        return source.replace('def main() -> IO(Unit):', order_runtime_definitions(runtime) + '\n\ndef main() -> IO(Unit):', 1)


def translate_normalized_file(path: Path, *, show_return=False, threshold=8):
    # Mirror v12's local module linking, but process syntax after linking.
    source = path.read_text(encoding='utf-8')
    from tools.python3_to_bend2.automatic_gc import RuntimeModuleLinker
    root = ast.parse(source,str(path))
    pure = {'math','operator','gc','weakref','typing','dataclasses','__future__'}
    local = any(isinstance(n,ast.Import) and any(a.name not in pure for a in n.names)
                or isinstance(n,ast.ImportFrom) and n.module not in pure for n in root.body)
    if local:
        source = RuntimeModuleLinker(path).link()
    return NormalizationLowerer(source,str(path),show_return=show_return,threshold=threshold).compile()


def main(argv: Sequence[str] | None = None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file',type=Path)
    ap.add_argument('-o','--output',type=Path)
    ap.add_argument('--show-return',action='store_true')
    a=ap.parse_args(argv)
    source=translate_normalized_file(a.file,show_return=a.show_return)
    if a.output:
        a.output.write_text(source,encoding='utf-8')
        print(f'PY3_BEND_V13 SOURCE_EMITTED {a.output} compiler_verified=false')
    else:
        print(source)
    return 0
