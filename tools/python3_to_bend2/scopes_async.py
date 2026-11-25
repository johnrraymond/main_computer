"""Python 3 -> Bend 2 v15: guarded completion and entry-state lowering.

The new lowering cases are deliberately narrow: synchronous execution of
coroutines that are always awaited and have no suspension/effects, module
literal state observable solely in `main`, terminal-raise `finally`, and
single-leaf ExceptionGroup handlers with no bound group object.

These are semantic specializations, not claims of general asynchronous,
exception-group or module-global support. Bend compilation is still mandatory.
"""
from __future__ import annotations

import argparse
import ast
import copy
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError
from tools.python3_to_bend2.collections import CollectionLowerer


class ScopeAsyncPass:
    def __init__(self, filename='<input>'):
        self.filename = filename

    def fail(self, node, reason):
        raise TranslationError(f'{reason} (line {getattr(node, "lineno", "?")})', source=self.filename)

    def _pure_async(self, tree):
        names={n.name for n in tree.body if isinstance(n,ast.AsyncFunctionDef)}
        if not names:
            return tree
        for member in tree.body:
            if not isinstance(member,ast.AsyncFunctionDef):
                continue
            if member.decorator_list or len(member.body)!=1 or not isinstance(member.body[0],ast.Return):
                self.fail(member,'async specialization needs a single pure return and no decorators')
            for inner in ast.walk(member):
                if isinstance(inner,(ast.AsyncFor,ast.AsyncWith,ast.Yield,ast.YieldFrom,ast.Yield,
                                     ast.Try,ast.TryStar,ast.Raise,ast.Global,ast.Nonlocal)):
                    self.fail(inner,'async suspension, effects, or exception semantics require a coroutine runtime')
                if isinstance(inner,ast.Call):
                    if not isinstance(inner.func,ast.Name) or inner.func.id not in names:
                        self.fail(inner,'async specialization permits calls only to other pure async functions')
                if isinstance(inner,ast.Await):
                    if not isinstance(inner.value,ast.Call) or not isinstance(inner.value.func,ast.Name) or inner.value.func.id not in names:
                        self.fail(inner,'await requires a statically known pure async call')
        parents={id(child):parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Load) and node.id in names:
                parent=parents.get(id(node))
                if not (isinstance(parent,ast.Call) and parent.func is node):
                    self.fail(node,'coroutine function must not escape as a first-class value')
                if not isinstance(parents.get(id(parent)),ast.Await):
                    self.fail(parent,'coroutine calls must be awaited before their value is observed')
        class Convert(ast.NodeTransformer):
            def visit_AsyncFunctionDef(self, node):
                converted=ast.FunctionDef(name=node.name,args=node.args,body=node.body,
                    decorator_list=node.decorator_list,returns=node.returns,
                    type_comment=node.type_comment, type_params=getattr(node,'type_params',[]))
                return ast.copy_location(self.generic_visit(converted),node)
            def visit_Await(self,node):
                return ast.copy_location(self.visit(node.value),node)
        tree=Convert().visit(tree)
        # Reject any remaining async machinery or undefined coroutine usage.
        if any(isinstance(n,(ast.Await,ast.AsyncFunctionDef,ast.AsyncFor,ast.AsyncWith)) for n in ast.walk(tree)):
            self.fail(tree,'unsupported remaining coroutine machinery')
        return tree

    def _entry_globals(self, tree):
        assignments=[]; names=set()
        for stmt in tree.body:
            if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name):
                literal=isinstance(stmt.value,ast.Constant) and type(stmt.value.value) in (int,bool,str)
                signed_int=(isinstance(stmt.value,ast.UnaryOp) and isinstance(stmt.value.op,(ast.UAdd,ast.USub))
                            and isinstance(stmt.value.operand,ast.Constant) and type(stmt.value.operand.value) is int)
                if not (literal or signed_int):
                    self.fail(stmt,'module global initializer must be a pure primitive literal')
                name=stmt.targets[0].id
                if name in names:
                    self.fail(stmt,'global variable has multiple module initializers')
                names.add(name); assignments.append(stmt)
        if not names:
            return tree
        if any(isinstance(n,(ast.ImportFrom,ast.Import)) for n in tree.body):
            # Imports may have module side effects, so the initializer cannot
            # generally be relocated after them.
            self.fail(tree,'globals with module imports need module initialization ordering')
        main=next((s for s in tree.body if isinstance(s,ast.FunctionDef) and s.name=='main'),None)
        if main is None:
            self.fail(tree,'module global lowering needs a main entry point')
        globals_in_main=set()
        for stmt in main.body:
            if isinstance(stmt,ast.Global):
                globals_in_main.update(stmt.names)
        if globals_in_main != names:
            self.fail(main,'each module global must be explicitly global in main')
        for function in (n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n is not main):
            for node in ast.walk(function):
                if isinstance(node,ast.Name) and node.id in names:
                    self.fail(node,'global escapes main; needs shared module heap state')
        for node in ast.walk(main):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node is not main:
                self.fail(node,'nested functions with module globals need shared lexical state')
        main.body=[*(copy.deepcopy(s) for s in assignments),
                   *(s for s in main.body if not isinstance(s,ast.Global))]
        tree.body=[s for s in tree.body if s not in assignments]
        return tree

    def _terminal_raise_finally(self, tree):
        outer=self
        class Normalize(ast.NodeTransformer):
            def visit_Try(self,stmt):
                stmt=self.generic_visit(stmt)
                if (stmt.handlers or stmt.orelse or len(stmt.body)!=1 or
                        not isinstance(stmt.body[0],ast.Raise) or not stmt.finalbody):
                    return stmt
                raised=stmt.body[0]
                if not (isinstance(raised.exc,ast.Call) and isinstance(raised.exc.func,ast.Name)
                        and raised.cause is None and not raised.exc.keywords and
                        len(raised.exc.args)<=1 and all(isinstance(a,ast.Constant) for a in raised.exc.args)):
                    return stmt
                if not all(isinstance(x,ast.Assign) and len(x.targets)==1 and
                           isinstance(x.targets[0],ast.Name) and isinstance(x.value,ast.Constant)
                           for x in stmt.finalbody):
                    return stmt
                # The raise expression is constant/pure; evaluating the
                # finalizer's pure assignments before raising is equivalent.
                # Returning a list keeps the surrounding handler's region.
                return [*stmt.finalbody,raised]
        return Normalize().visit(tree)

    def _simple_exception_groups(self,tree):
        outer=self
        class Normalize(ast.NodeTransformer):
            def visit_TryStar(self,node):
                node=self.generic_visit(node)
                if node.orelse or node.finalbody or len(node.body)!=1 or len(node.handlers)!=1:
                    return node
                thrown=node.body[0]
                if not isinstance(thrown,ast.Raise) or thrown.cause is not None:
                    return node
                value=thrown.exc
                if not (isinstance(value,ast.Call) and isinstance(value.func,ast.Name)
                    and value.func.id=='ExceptionGroup' and len(value.args)==2 and not value.keywords
                    and isinstance(value.args[0],ast.Constant) and type(value.args[0].value) is str
                    and isinstance(value.args[1],ast.List) and len(value.args[1].elts)==1):
                    return node
                leaf=value.args[1].elts[0]
                handler=node.handlers[0]
                if not (isinstance(leaf,ast.Call) and isinstance(leaf.func,ast.Name)
                    and isinstance(handler.type,ast.Name) and handler.type.id==leaf.func.id
                    and handler.name is None and not leaf.keywords
                    and all(isinstance(a,ast.Constant) for a in leaf.args)
                    and leaf.func.id in ('ValueError','TypeError','RuntimeError','KeyError',
                                         'IndexError','ZeroDivisionError','AssertionError')):
                    return node
                if any(isinstance(part,(ast.Raise,ast.Return,ast.Try,ast.TryStar,ast.Break,ast.Continue))
                       for stmt in handler.body for part in ast.walk(stmt)):
                    return node
                # With exactly one matching leaf and no bound exception-group
                # object, the handler is entered once. The group and leaf
                # constructors have pure literal arguments.
                return ast.copy_location(ast.Try(body=[ast.Raise(exc=leaf,cause=None)],
                    handlers=[ast.ExceptHandler(type=copy.deepcopy(handler.type), name=None,
                                               body=handler.body)],orelse=[],finalbody=[]),node)
        return Normalize().visit(tree)

    def apply(self,source):
        tree=ast.parse(source,self.filename)
        tree=self._pure_async(tree)
        tree=self._entry_globals(tree)
        tree=self._terminal_raise_finally(tree)
        tree=self._simple_exception_groups(tree)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)


class ScopeAsyncLowerer(CollectionLowerer):
    def __init__(self,source,source_name='<input>',*,show_return=False,threshold=8):
        self.v15_prepass=ScopeAsyncPass(source_name)
        lowered=self.v15_prepass.apply(source)
        self.v15_lowered=lowered
        super().__init__(lowered,source_name,show_return=show_return,threshold=threshold)


def main(argv: Sequence[str]|None=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file',type=Path)
    parser.add_argument('-o','--output',type=Path)
    parser.add_argument('--show-return',action='store_true')
    args=parser.parse_args(argv)
    emitted=ScopeAsyncLowerer(args.file.read_text(encoding='utf-8'),str(args.file),show_return=args.show_return).compile()
    if args.output:
        args.output.write_text(emitted,encoding='utf-8')
        print(f'PY3_BEND_V15 SOURCE_EMITTED {args.output} compiler_verified=false')
    else:
        print(emitted)
    return 0
