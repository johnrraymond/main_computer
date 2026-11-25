"""Python 3 -> Bend 2 v14: guarded finite-domain semantic lowering.

Adds native paths for integer-key dictionaries, finite integer/string sets,
read-only bytes, nullcontext, and statically decidable stdlib operations.
These are *specializations*, not general implementations of the Python
object protocols or stdlib. Unsupported observable semantics fail closed.
Compiler execution is mandatory for Bend-runtime certification.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import pathlib
import re
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.normalization import NormalizationLowerer, NormalizationPass
from tools.python3_to_bend2.syntax import TranslationError


def _name(name: str, store: bool = False) -> ast.Name:
    return ast.Name(id=name, ctx=ast.Store() if store else ast.Load())


def _constant(value):
    return ast.Constant(value=value)


def _call(fn, *args):
    return ast.Call(func=_name(fn), args=list(args), keywords=[])


def _is_int(node):
    if isinstance(node, ast.Constant):
        return type(node.value) is int
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_int(node.operand)
    return False


def _int_literal(node):
    if not _is_int(node):
        return None
    return ast.literal_eval(node)


def _json_ast(value, *, depth=0):
    if depth > 40:
        raise ValueError('JSON nesting limit')
    if value is None or type(value) in (str, int, bool):
        if type(value) is str and not value.isascii():
            raise ValueError('non-ASCII JSON string requires UTF-8 Bend runtime support')
        return _constant(value)
    if isinstance(value, list):
        return ast.List(elts=[_json_ast(x, depth=depth + 1) for x in value], ctx=ast.Load())
    if isinstance(value, dict) and all(type(k) is str for k in value):
        if any(not k.isascii() for k in value):
            raise ValueError('non-ASCII JSON key requires UTF-8 Bend runtime support')
        if len({type(v) for v in value.values()}) > 1:
            raise ValueError('native JSON dictionary values must share one static type')
        return ast.Dict(keys=[_constant(k) for k in value],
                        values=[_json_ast(v, depth=depth + 1) for v in value.values()])
    raise ValueError('native JSON constants require integer/string/bool/null/list/dict, no floats')


class CollectionPass(NormalizationPass):
    """Pre-normalize safe finite-domain cases before v13's AST normalization."""

    def _constant_library(self, tree):
        imports = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    if alias.name in ('json', 're'):
                        imports[alias.asname or alias.name] = alias.name
            elif isinstance(statement, ast.ImportFrom) and statement.module == 'pathlib':
                for alias in statement.names:
                    if alias.name == 'Path':
                        imports[alias.asname or alias.name] = 'pathlib.Path'
        if not imports:
            return tree
        # An import shadowed anywhere by a user binding is not a compile-time constant.
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in imports:
                self.fail(node, 'compile-time library import is shadowed by a local binding')
            if isinstance(node, ast.arg) and node.arg in imports:
                self.fail(node, 'compile-time library import is shadowed by a parameter')

        outer = self
        class Fold(ast.NodeTransformer):
            def visit_Call(self, node):
                node = self.generic_visit(node)
                if not (isinstance(node.func, ast.Attribute) and
                        isinstance(node.func.value, ast.Name)):
                    return node
                module = imports.get(node.func.value.id)
                if module == 'json' and node.func.attr == 'loads':
                    if (len(node.args) != 1 or node.keywords or
                            not isinstance(node.args[0], ast.Constant) or type(node.args[0].value) is not str):
                        outer.fail(node, 'json.loads requires one literal string for static native lowering')
                    try:
                        decoded = json.loads(node.args[0].value)
                        return ast.copy_location(_json_ast(decoded), node)
                    except (ValueError, RecursionError, json.JSONDecodeError) as exc:
                        outer.fail(node, f'JSON constant cannot be lowered: {exc}')
                if module == 're' and node.func.attr == 'findall':
                    if (len(node.args) != 2 or node.keywords or
                            any(not isinstance(arg, ast.Constant) or type(arg.value) is not str
                                for arg in node.args)):
                        outer.fail(node, 're.findall requires literal pattern and literal input')
                    pattern, string = [a.value for a in node.args]
                    # This linear-time grammar prohibits backtracking attacks at compile time.
                    if (len(pattern) > 80 or len(string) > 2048 or
                            not re.fullmatch(r'[A-Za-z0-9_ +*?.\-]+', pattern) or
                            sum(c in '*+?' for c in pattern) > 1):
                        outer.fail(node, 'regex constant outside bounded linear-time subset')
                    try:
                        items = re.findall(pattern, string)
                    except re.error as exc:
                        outer.fail(node, f'invalid regex: {exc}')
                    if any(type(x) is not str for x in items):
                        outer.fail(node, 'regex capture tuples require general tuple storage')
                    return ast.copy_location(ast.List(elts=[_constant(x) for x in items], ctx=ast.Load()),node)
                return node

            def visit_Attribute(self, node):
                node = self.generic_visit(node)
                if (not isinstance(node.value, ast.Call) or
                        not isinstance(node.value.func, ast.Name) or
                        imports.get(node.value.func.id) != 'pathlib.Path' or
                        node.attr not in ('name', 'stem', 'suffix')):
                    return node
                call = node.value
                if len(call.args) != 1 or call.keywords or not isinstance(call.args[0], ast.Constant) or type(call.args[0].value) is not str:
                    outer.fail(node, 'Path lexical specialization requires one literal path')
                path = call.args[0].value
                if len(path) > 2048 or '\\' in path or ':' in path or path.startswith('//'):
                    outer.fail(node, 'Path semantics are platform dependent for this literal')
                unix = getattr(pathlib.PurePosixPath(path), node.attr)
                windows = getattr(pathlib.PureWindowsPath(path), node.attr)
                if unix != windows:
                    outer.fail(node, 'Path literal differs between POSIX and Windows')
                return ast.copy_location(_constant(unix), node)
        tree = Fold().visit(tree)
        remaining = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        newbody = []
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                aliases = [a for a in stmt.names if (a.asname or a.name) in remaining or a.name not in ('json','re')]
                if aliases: stmt.names = aliases; newbody.append(stmt)
            elif isinstance(stmt, ast.ImportFrom) and stmt.module == 'pathlib':
                aliases = [a for a in stmt.names if a.name != 'Path' or (a.asname or a.name) in remaining]
                if aliases: stmt.names = aliases; newbody.append(stmt)
            else:
                newbody.append(stmt)
        tree.body = newbody
        return tree

    def _nullcontext(self, tree):
        aliases = {
            a.asname or a.name: a.name
            for stmt in tree.body
            if isinstance(stmt, ast.ImportFrom) and stmt.module == 'contextlib'
            for a in stmt.names if a.name in ('nullcontext','suppress')
        }
        if not aliases:
            return tree
        outer = self
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in aliases:
                self.fail(node, 'contextlib imported name is shadowed')
            if isinstance(node, ast.arg) and node.arg in aliases:
                self.fail(node, 'contextlib imported name is shadowed by parameter')
        class Replace(ast.NodeTransformer):
            def visit_With(self, stmt):
                body=[]
                for original in stmt.body:
                    lowered=self.visit(original)
                    if isinstance(lowered,list):body.extend(lowered)
                    else:body.append(lowered)
                for item in reversed(stmt.items):
                    expr=item.context_expr
                    if not (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name)
                            and expr.func.id in aliases):
                        outer.fail(expr, 'only statically imported nullcontext/suppress are supported')
                    kind=aliases[expr.func.id]
                    if kind=='nullcontext':
                        if expr.keywords or len(expr.args)>1:
                            outer.fail(expr,'nullcontext accepts at most one positional argument')
                        value=expr.args[0] if expr.args else _constant(None)
                        if item.optional_vars is not None:
                            if not isinstance(item.optional_vars,ast.Name):
                                outer.fail(item.optional_vars,'nullcontext target requires a name')
                            body=[ast.Assign(targets=[item.optional_vars],value=value),*body]
                        elif expr.args:
                            body=[ast.Expr(value=value),*body]
                    else:
                        if expr.keywords or len(expr.args)>1 or any(not isinstance(arg,ast.Name) or
                                arg.id not in ('ValueError','RuntimeError','ZeroDivisionError','TypeError','KeyError',
                                                'IndexError','AssertionError','Exception') for arg in expr.args):
                            outer.fail(expr,'suppress supports a single known builtin exception class')
                        if expr.args:
                            body=[ast.Try(body=body, handlers=[ast.ExceptHandler(type=expr.args[0],name=None,
                                  body=[ast.Pass()])],orelse=[],finalbody=[])]
                        if item.optional_vars is not None:
                            if not isinstance(item.optional_vars,ast.Name):
                                outer.fail(item.optional_vars,'suppress target must be a single name')
                            body=[ast.Assign(targets=[item.optional_vars],value=_constant(None)),*body]
                return body
        tree=Replace().visit(tree)
        used={n.id for n in ast.walk(tree) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)}
        body=[]
        for stmt in tree.body:
            if isinstance(stmt,ast.ImportFrom) and stmt.module=='contextlib':
                remaining=[a for a in stmt.names if a.name not in ('nullcontext','suppress') or
                           (a.asname or a.name) in used]
                if remaining:stmt.names=remaining;body.append(stmt)
            else:body.append(stmt)
        tree.body=body
        return tree

    def _dynamic_math_import(self, tree):
        # A literal import of math has the same closed-world binding as 'import math'.
        # Arbitrary import hooks and nonconstant module names are not supported.
        outer=self
        class StaticImport(ast.NodeTransformer):
            def visit_Assign(self, node):
                if (len(node.targets)==1 and isinstance(node.targets[0], ast.Name) and
                        isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and
                        node.value.func.id == '__import__' and len(node.value.args)==1 and
                        not node.value.keywords and isinstance(node.value.args[0],ast.Constant) and
                        node.value.args[0].value=='math'):
                    return ast.copy_location(ast.Import(names=[ast.alias(name='math',asname=node.targets[0].id)]),node)
                return self.generic_visit(node)
        return StaticImport().visit(tree)

    def _nonlocal_cells(self, tree):
        """Lower nonescaping mutable integer captures to shared heap cells."""
        new_defs=[]
        retained=[]
        for outer in tree.body:
            if not isinstance(outer, ast.FunctionDef):
                retained.append(outer)
                continue
            nested=[s for s in outer.body if isinstance(s,ast.FunctionDef)
                    and any(isinstance(n,ast.Nonlocal) for n in ast.walk(s))]
            if not nested:
                retained.append(outer)
                continue
            if len(nested)!=1 or any(isinstance(s,ast.FunctionDef) and s not in nested for s in outer.body):
                self.fail(outer,'nonlocal cell lowering requires one direct nested function')
            inner=nested[0]
            if inner.decorator_list or inner.args.vararg or inner.args.kwarg:
                self.fail(inner,'mutable closure decorators and variable signatures are unsupported')
            if any(isinstance(n,(ast.Yield,ast.YieldFrom,ast.Await,ast.Lambda)) for n in ast.walk(inner)):
                self.fail(inner,'suspended nonlocal frame is unsupported')
            decl=[s for s in inner.body if isinstance(s,ast.Nonlocal)]
            if len(decl)!=1 or not decl[0].names or len(set(decl[0].names))!=len(decl[0].names):
                self.fail(inner,'one direct nonlocal declaration is required')
            names=decl[0].names
            initial={}
            for s in outer.body:
                if s is inner: break
                if (isinstance(s,ast.Assign) and len(s.targets)==1 and
                        isinstance(s.targets[0],ast.Name) and s.targets[0].id in names):
                    initial[s.targets[0].id]=s
            if set(initial)!=set(names) or any(not _is_int(s.value) for s in initial.values()):
                self.fail(inner,'captured integers must have dominating literal initializers')
            if any(isinstance(s,ast.Assign) and len(s.targets)==1 and
                   isinstance(s.targets[0],ast.Name) and s.targets[0].id in names and
                   s is not initial[s.targets[0].id] for s in outer.body):
                self.fail(outer,'rebound capture requires a general lexical environment')
            if any(isinstance(n,ast.FunctionDef) and n is not inner for n in ast.walk(inner)):
                self.fail(inner,'nested mutable closures need resumable frames')
            parents={id(child):p for p in ast.walk(outer) for child in ast.iter_child_nodes(p)}
            for n in ast.walk(outer):
                if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load) and n.id==inner.name:
                    p=parents.get(id(n))
                    if not (isinstance(p,ast.Call) and p.func is n):
                        self.fail(n,'mutable nested function cannot escape')
            lifted=self.fresh('nonlocal_'+inner.name)
            cells={}
            for variable in names:
                cls=self.fresh('cell_'+variable)
                ref=self.fresh('ref_'+variable)
                cells[variable]=(cls,ref)
                class_source=(f'class {cls}:\n'
                    '    value: int\n'
                    '    def __init__(self, value: int) -> None:\n'
                    '        self.value = value\n')
                new_defs.append(ast.parse(class_source).body[0])
            class ReplaceNames(ast.NodeTransformer):
                def visit_Name(self, node):
                    if node.id in cells:
                        return ast.copy_location(ast.Attribute(value=_name(cells[node.id][1]),
                            attr='value',ctx=node.ctx),node)
                    return node
            replacement=[]
            for s in outer.body:
                if s is inner: continue
                match=next((name for name in names if s is initial[name]),None)
                if match is not None:
                    cls,ref=cells[match]
                    replacement.append(ast.Assign(targets=[_name(ref,True)],
                        value=ast.Call(func=_name(cls),args=[s.value],keywords=[])))
                else:
                    replacement.append(ReplaceNames().visit(s))
            lift_fn=copy.deepcopy(inner)
            lift_fn.name=lifted
            lift_fn.body=[ReplaceNames().visit(copy.deepcopy(s)) for s in inner.body
                          if not isinstance(s,ast.Nonlocal)]
            lift_fn.args.args=[ast.arg(arg=cells[name][1],annotation=_name(cells[name][0]))
                               for name in names]+lift_fn.args.args
            class RewriteCalls(ast.NodeTransformer):
                def visit_Call(self, node):
                    node=self.generic_visit(node)
                    if isinstance(node.func,ast.Name) and node.func.id==inner.name:
                        node.func=_name(lifted)
                        node.args=[_name(cells[name][1]) for name in names]+node.args
                    return node
            outer.body=[RewriteCalls().visit(s) for s in replacement]
            new_defs.append(RewriteCalls().visit(lift_fn))
            retained.append(outer)
        tree.body=new_defs+retained
        return tree

    def _yield_from_range(self, tree):
        # Exact finite integer streams can use the existing real heap-backed
        # cursor; arbitrary yield-from delegation and send()/throw() cannot.
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or len(fn.body) != 1:
                continue
            stmt = fn.body[0]
            if not (isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.YieldFrom)):
                continue
            iterable=stmt.value.value
            if (isinstance(iterable,ast.Call) and isinstance(iterable.func,ast.Name) and
                    iterable.func.id=='range' and not iterable.keywords):
                expr=iterable
                name=self.fresh('yield')
                fn.body=[ast.For(target=_name(name,True),iter=expr,
                          body=[ast.Expr(value=ast.Yield(value=_name(name)))],orelse=[])]
            elif isinstance(iterable,(ast.List,ast.Tuple)) and all(_is_int(x) for x in iterable.elts):
                values=[ast.literal_eval(x) for x in iterable.elts]
                if not values:
                    start=0; step=1
                elif len(values)==1:
                    start=values[0]; step=1
                else:
                    start=values[0]; step=values[1]-values[0]
                var=self.fresh('yieldfrom')
                if step==0:
                    # Generate a cursor-dependent pure expression with a
                    # constant result, which v11 can suspend faithfully.
                    expr=ast.BinOp(left=ast.BinOp(left=_name(var),op=ast.Mult(),right=_constant(0)),
                                   op=ast.Add(),right=_constant(start))
                    loop=ast.Call(func=_name('range'),args=[_constant(len(values))],keywords=[])
                elif all(value==start+i*step for i,value in enumerate(values)):
                    expr=ast.BinOp(left=ast.BinOp(left=_name(var),op=ast.Mult(),right=_constant(step)),
                                   op=ast.Add(),right=_constant(start))
                    loop=ast.Call(func=_name('range'),args=[_constant(len(values))],keywords=[])
                else:
                    self.fail(stmt,'nonlinear yield-from sequence needs a general suspended frame')
                fn.body=[ast.For(target=_name(var,True),iter=loop,
                          body=[ast.Expr(value=ast.Yield(value=expr))],orelse=[])]
            # Non-literal iterables remain unsupported in v11; do not fake
            # delegation of generators with arbitrary side effects.
        generator_names={f.name for f in tree.body if isinstance(f,ast.FunctionDef) and any(
            isinstance(n,(ast.Yield,ast.YieldFrom)) for n in ast.walk(f))}
        for fn in tree.body:
            if not isinstance(fn,ast.FunctionDef) or fn.name in generator_names:
                continue
            result=[]
            for stmt in fn.body:
                value=stmt.value if isinstance(stmt,(ast.Assign,ast.Return,ast.Expr)) else None
                if (isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and
                        value.func.id=='next' and len(value.args)==1 and not value.keywords and
                        isinstance(value.args[0],ast.Call) and isinstance(value.args[0].func,ast.Name) and
                        value.args[0].func.id in generator_names):
                    tmp=self.fresh('yieldfrom_iterator')
                    result.append(ast.Assign(targets=[_name(tmp,True)],value=value.args[0]))
                    value.args=[_name(tmp)]
                result.append(stmt)
            fn.body=result
        return tree

    def _containers(self, tree):
        outer = self
        for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):

            tags = {}  # name -> (kind, key-kind)
            defs = {}
            for stmt in ast.walk(fn):
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    target = stmt.targets[0].id
                    defs[target] = defs.get(target, 0) + 1
                    v=stmt.value
                    if isinstance(v,ast.Set) and v.elts:
                        typ = 'int' if all(_is_int(x) for x in v.elts) else 'str' if all(isinstance(x,ast.Constant) and type(x.value) is str for x in v.elts) else None
                        if typ: tags[target]=('set',typ)
                    elif isinstance(v,ast.SetComp) and len(v.generators)==1 and isinstance(v.generators[0].iter,ast.Call) and isinstance(v.generators[0].iter.func,ast.Name) and v.generators[0].iter.func.id=='range':
                        tags[target]=('set','int')
                    elif (isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and
                            v.func.id=='set' and not v.keywords and len(v.args)<=1):
                        if not v.args:
                            uses=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and
                                isinstance(n.func,ast.Attribute) and n.func.attr=='add' and
                                isinstance(n.func.value,ast.Name) and n.func.value.id==target]
                            candidate=[]
                            for use in uses:
                                if len(use.args)!=1 or use.keywords:
                                    self.fail(use, 'set.add accepts one argument')
                                candidate.append('int' if _is_int(use.args[0]) else
                                    'str' if isinstance(use.args[0],ast.Constant) and type(use.args[0].value) is str else 'unknown')
                            if 'unknown' in candidate or len(set(candidate))>1:
                                self.fail(v,'set() element type cannot be proven statically')
                            tags[target]=('set', candidate[0] if candidate else 'int')
                        elif (isinstance(v.args[0],(ast.List,ast.Tuple))):
                            values=v.args[0].elts
                            tp='int' if all(_is_int(x) for x in values) else 'str' if all(
                                isinstance(x,ast.Constant) and type(x.value) is str for x in values) else None
                            if tp:
                                tags[target]=('set',tp)
                        elif (isinstance(v.args[0],ast.Call) and isinstance(v.args[0].func,ast.Name)
                              and v.args[0].func.id=='range' and not v.args[0].keywords):
                            tags[target]=('set','int')
                    elif isinstance(v,ast.Dict) and v.keys and all(x is not None and _is_int(x) for x in v.keys):
                        tags[target]=('intdict','int')
                    elif isinstance(v,ast.Constant) and type(v.value) is bytes:
                        tags[target]=('bytes','int')
            if not tags:
                continue
            for key in tags:
                if defs.get(key) != 1:
                    self.fail(fn, f'{key}: specialized container is rebound or aliased')
            # Uses are explicitly validated, so rewriting a Python type into a
            # heap-backed dictionary/list cannot silently alter its protocols.
            parents = {id(child): parent for parent in ast.walk(fn) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(fn):
                if not isinstance(node,ast.Name) or not isinstance(node.ctx,ast.Load) or node.id not in tags:
                    continue
                kind, _ = tags[node.id]
                parent=parents.get(id(node))
                allowed=(isinstance(parent,ast.Call) and parent.func is not node and
                         isinstance(parent.func,ast.Name) and parent.func.id=='len' and parent.args==[node])
                if kind in ('set','intdict'):
                    allowed |= isinstance(parent,ast.Subscript) and parent.value is node and kind=='intdict' and isinstance(parent.ctx,(ast.Load,ast.Store))
                    allowed |= isinstance(parent,ast.Compare) and node in parent.comparators and any(
                        isinstance(op,(ast.In,ast.NotIn)) for op in parent.ops)
                    allowed |= isinstance(parent,ast.Attribute) and parent.value is node and (
                        kind=='set' and parent.attr=='add' or kind=='intdict' and parent.attr=='get')
                if kind=='bytes':
                    allowed |= isinstance(parent,ast.Subscript) and parent.value is node and isinstance(parent.ctx,ast.Load) and not isinstance(parent.slice,ast.Slice)
                if not allowed:
                    self.fail(node, f'{kind} specialization cannot preserve this observable operation')
                if isinstance(parent,ast.Subscript) and kind=='bytes' and not isinstance(parent.ctx,ast.Load):
                    self.fail(node, 'bytes are immutable and cannot be assigned through')

            class Lower(ast.NodeTransformer):
                def _valid_key(self, expr):
                    if _is_int(expr): return True
                    if isinstance(expr,ast.Name):
                        return any(a.arg==expr.id and isinstance(a.annotation,ast.Name) and a.annotation.id=='int'
                                   for a in fn.args.args) or any(
                            isinstance(x,ast.For) and isinstance(x.target,ast.Name) and x.target.id==expr.id and
                            isinstance(x.iter,ast.Call) and isinstance(x.iter.func,ast.Name) and x.iter.func.id=='range'
                            for x in ast.walk(fn))
                    if isinstance(expr,ast.BinOp) and isinstance(expr.op,(ast.Add,ast.Sub,ast.Mult)):
                        return self._valid_key(expr.left) and self._valid_key(expr.right)
                    return False

                def visit_Call(self,node):
                    node=self.generic_visit(node)
                    if (isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name)
                            and tags.get(node.func.value.id,('',''))[0]=='intdict' and node.func.attr=='get'):
                        if not node.args or len(node.args)>2 or node.keywords or not self._valid_key(node.args[0]):
                            outer.fail(node, 'integer dictionary get requires integer key and optional default')
                        node.args[0]=_call('str',node.args[0])
                    return node

                def visit_Assign(self, node):
                    if len(node.targets)==1 and isinstance(node.targets[0], ast.Name):
                        tag=tags.get(node.targets[0].id)
                        if tag:
                            kind,typ=tag
                            if kind=='set' and isinstance(node.value,ast.Set):
                                keys=[_constant(str(ast.literal_eval(x))) if typ=='int' else copy.deepcopy(x) for x in node.value.elts]
                                node.value=ast.Dict(keys=keys,values=[_constant(1) for _ in keys])
                            elif kind=='set' and isinstance(node.value,ast.SetComp):
                                value=node.value
                                if (len(value.generators)!=1 or value.generators[0].is_async or
                                        not isinstance(value.generators[0].target,ast.Name) or
                                        not isinstance(value.elt,(ast.Name,ast.Constant,ast.BinOp,ast.UnaryOp))):
                                    outer.fail(node,'set comprehension requires one integer range generator')
                                gen=value.generators[0]
                                if (any(isinstance(n,ast.Name) and n.id!=gen.target.id for n in ast.walk(value.elt)) or
                                        any(isinstance(n,ast.Call) for n in ast.walk(value.elt)) or
                                        any(isinstance(n,ast.BinOp) and not isinstance(n.op,(ast.Add,ast.Sub,ast.Mult)) for n in ast.walk(value.elt))):
                                    outer.fail(node,'set key requires pure integer arithmetic on range cursor')
                                node.value=ast.DictComp(key=_call('str',value.elt),value=_constant(1),generators=value.generators)
                            elif kind=='set' and isinstance(node.value,ast.Call):
                                expr=node.value
                                if not expr.args:
                                    node.value=ast.Dict(keys=[],values=[])
                                elif isinstance(expr.args[0],(ast.List,ast.Tuple)):
                                    keys=[_constant(str(ast.literal_eval(x))) if typ=='int' else copy.deepcopy(x)
                                          for x in expr.args[0].elts]
                                    node.value=ast.Dict(keys=keys,values=[_constant(1) for _ in keys])
                                elif (isinstance(expr.args[0],ast.Call) and isinstance(expr.args[0].func,ast.Name)
                                      and expr.args[0].func.id=='range'):
                                    variable=outer.fresh('setrange')
                                    node.value=ast.DictComp(key=_call('str',_name(variable)),value=_constant(1),
                                        generators=[ast.comprehension(target=_name(variable,True),
                                            iter=expr.args[0],ifs=[],is_async=0)])
                                else:
                                    outer.fail(node,'set constructor iterable cannot be lowered')
                            elif kind=='intdict' and isinstance(node.value,ast.Dict):
                                node.value.keys=[_constant(str(ast.literal_eval(x))) for x in node.value.keys]
                            elif kind=='bytes' and isinstance(node.value,ast.Constant):
                                node.value=ast.List(elts=[_constant(byte) for byte in node.value.value],ctx=ast.Load())
                    return self.generic_visit(node)

                def visit_Subscript(self, node):
                    node=self.generic_visit(node)
                    if (isinstance(node.value,ast.Name) and tags.get(node.value.id)==('intdict','int')):
                        if isinstance(node.slice, ast.Slice):
                            outer.fail(node,'integer-key dict does not support slicing')
                        if not self._valid_key(node.slice):
                            outer.fail(node,'integer-key dictionary requires proven integer key (not bool/str/float)')
                        node.slice=_call('str', node.slice)
                    return node

                def visit_Compare(self, node):
                    node=self.generic_visit(node)
                    if len(node.ops)==1 and isinstance(node.ops[0],(ast.In,ast.NotIn)) and (
                            isinstance(node.comparators[0],ast.Name) and
                            tags.get(node.comparators[0].id,('',''))[0] in ('set','intdict')):
                        kind, keytype=tags[node.comparators[0].id]
                        if keytype=='int':
                            if not self._valid_key(node.left):
                                outer.fail(node,'integer collection membership requires proven integer key')
                            node.left=_call('str',node.left)
                        elif not (isinstance(node.left,ast.Constant) and type(node.left.value) is str):
                            outer.fail(node,'string set membership requires a proven string key')
                    return node

                def visit_Expr(self, node):
                    if (isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Attribute) and
                            isinstance(node.value.func.value,ast.Name) and
                            tags.get(node.value.func.value.id, ('',''))[0]=='set' and
                            node.value.func.attr=='add'):
                        expr=node.value
                        if len(expr.args)!=1 or expr.keywords:
                            outer.fail(expr,'set.add expects one value')
                        name=expr.func.value.id
                        if tags[name][1]=='int' and not self._valid_key(expr.args[0]):
                            outer.fail(expr, 'integer set.add requires a proven integer value')
                        if tags[name][1]=='str' and not (isinstance(expr.args[0],ast.Constant) and type(expr.args[0].value) is str):
                            outer.fail(expr, 'string set.add requires a proven string value')
                        key = _call('str',expr.args[0]) if tags[name][1]=='int' else expr.args[0]
                        return ast.copy_location(ast.Assign(targets=[ast.Subscript(value=_name(name),slice=key,ctx=ast.Store())],value=_constant(1)),node)
                    return self.generic_visit(node)
            fn.body=[Lower().visit(s) for s in fn.body]
        return tree

    def apply(self, source: str):
        tree=ast.parse(source,self.filename)
        self.reserved.update(n.id for n in ast.walk(tree) if isinstance(n,ast.Name))
        self.reserved.update(n.arg for n in ast.walk(tree) if isinstance(n,ast.arg))
        self.reserved.update(n.name for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.ClassDef)))
        tree=self._constant_library(tree)
        tree=self._nullcontext(tree)
        tree=self._dynamic_math_import(tree)
        tree=self._nonlocal_cells(tree)
        tree=self._yield_from_range(tree)
        tree=self._containers(tree)
        ast.fix_missing_locations(tree)
        return super().apply(ast.unparse(tree))


class CollectionLowerer(NormalizationLowerer):
    def __init__(self, source, source_name='<input>', *, show_return=False, threshold=8):
        self.v14_prepass=CollectionPass(source_name)
        source14=self.v14_prepass.apply(source)
        self.v14_lowered=source14
        super().__init__(source14,source_name,show_return=show_return,threshold=threshold)


def main(argv: Sequence[str] | None=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('-o','--output',type=Path)
    parser.add_argument('--show-return',action='store_true')
    args=parser.parse_args(argv)
    bend=CollectionLowerer(args.file.read_text(encoding='utf-8'),str(args.file),show_return=args.show_return).compile()
    if args.output:
        args.output.write_text(bend,encoding='utf-8')
        print(f'PY3_BEND_V14 SOURCE_EMITTED {args.output} compiler_verified=false')
    else:
        print(bend)
    return 0
