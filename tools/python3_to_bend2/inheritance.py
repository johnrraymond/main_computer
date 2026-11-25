"""Python 3 -> Bend 2 v12: C3 static MRO and super()-dispatch lowering.

This is a strict native-source backend. It does not use CPython at runtime.
Real Bend checking and differential execution are required for certification.
"""
from __future__ import annotations

import argparse
import ast
import copy
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from tools.python3_to_bend2.syntax import TranslationError, bend_command
from tools.python3_to_bend2.integers import run_bend_gate
from tools.python3_to_bend2.exceptions_attributes import ExceptionAttributeLowerer
from tools.python3_to_bend2.runtime_semantics import RuntimeSemanticPass, RuntimeLowerer
from tools.python3_to_bend2.automatic_gc import RuntimeModuleLinker


class InheritancePass(RuntimeSemanticPass):
    """Flatten static class hierarchies using Python's C3 precedence.

    Existing v11 compilation expects bases removed and inherited methods
    materialized. The C3 specialization preserves that representation, while
    rewriting zero-arg ``super().method(...)`` into runtime-class-specific
    methods whose dispatch target is determined by C3.
    """

    def __init__(self, source_name='<input>'):
        super().__init__(source_name)
        self.mros: dict[str, tuple[str, ...]] = {}

    def _lower_try(self, node, active_exception, outer_final):
        # A return/raise in finally replaces every pending completion from
        # try/except (including a pending error). Evaluate the try expression
        # first; the replacement runs only at the generated completion edges.
        # Restrict the override expression to a literal because a general
        # finally expression needs complete unwind-capable expression CFGs.
        abrupt = node.finalbody
        if len(abrupt) == 1 and isinstance(abrupt[0], (ast.Return, ast.Raise)):
            if outer_final:
                self.fail(node, 'nested overriding finally needs stacked completion records')
            terminator = abrupt[0]
            if isinstance(terminator, ast.Return):
                if not isinstance(terminator.value, ast.Constant):
                    self.fail(terminator, 'finally return override requires a literal result')
                replacement = ast.Return(value=copy.deepcopy(terminator.value))
            else:
                replacement = self._return_raise(terminator, active_exception)
            staged = copy.deepcopy(node)
            staged.finalbody = []
            normal = super()._lower_try(staged, active_exception, None)
            class RewriteCompletion(ast.NodeTransformer):
                def visit_Return(self, old):
                    return ast.copy_location(copy.deepcopy(replacement), old)
            normal = [RewriteCompletion().visit(s) for s in normal]
            normal.append(copy.deepcopy(replacement))
            return normal
        return super()._lower_try(node, active_exception, outer_final)

    def _flatten_inheritance(self, tree: ast.Module) -> None:
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        self.class_names = set(classes)
        own_bases: dict[str, tuple[str, ...]] = {}
        original_methods: dict[str, dict[str, ast.FunctionDef]] = {}
        original_fields: dict[str, dict[str, ast.AnnAssign]] = {}
        for cls in classes.values():
            if cls.keywords:
                self.fail(cls, 'metaclass keywords require a dynamic class runtime')
            parents = []
            for base in cls.bases:
                if not isinstance(base, ast.Name) or base.id not in classes:
                    self.fail(cls, 'base classes must be statically known local classes')
                if base.id in parents:
                    self.fail(cls, 'duplicate base class')
                parents.append(base.id)
            own_bases[cls.name] = tuple(parents)
            original_methods[cls.name] = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
            original_fields[cls.name] = {n.target.id: n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
            if parents:
                self.parents[cls.name] = parents[0]

        def linearize(name: str, active: frozenset[str] = frozenset()) -> tuple[str, ...]:
            if name in self.mros:
                return self.mros[name]
            if name in active:
                self.fail(classes[name], 'cyclic class hierarchy')
            bases = own_bases[name]
            queues = [list(linearize(parent, active | {name})) for parent in bases] + [list(bases)]
            order = [name]
            while any(queues):
                head = next((q[0] for q in queues if q and all(q[0] not in seq[1:] for seq in queues)), None)
                if head is None:
                    self.fail(classes[name], 'inconsistent C3 method resolution order')
                order.append(head)
                for seq in queues:
                    if seq and seq[0] == head:
                        seq.pop(0)
            self.mros[name] = tuple(order)
            return self.mros[name]

        for name in classes:
            linearize(name)

        for name, cls in classes.items():
            mro = self.mros[name]
            # Respect the first field declaration along C3, rejecting a
            # conflicting declaration that cannot be represented in the
            # translator's static record model.
            inherited_fields: dict[str, ast.AnnAssign] = {}
            for provider in mro:
                for field, definition in original_fields[provider].items():
                    if field in inherited_fields:
                        previous = inherited_fields[field]
                        if ast.dump(previous.annotation) != ast.dump(definition.annotation):
                            self.fail(cls, f'conflicting inherited field type: {field}')
                    else:
                        inherited_fields[field] = definition
            inherited = [copy.deepcopy(decl) for field, decl in inherited_fields.items()
                         if field not in original_fields[name]]
            # Skip inherited fields declared in the current class. Inherited
            # methods are copied from the FIRST provider in the C3 MRO.
            selected: dict[str, tuple[str, ast.FunctionDef]] = {}
            for provider in mro:
                for method, definition in original_methods[provider].items():
                    selected.setdefault(method, (provider, definition))

            synthetic: dict[tuple[str, str], str] = {}
            generated: list[ast.FunctionDef] = []

            def make_method(provider: str, method: str, *, named: str | None = None) -> ast.FunctionDef:
                original = (original_methods[provider].get(method)
                            or selected.get(method, (None, None))[1])
                if original is None:
                    self.fail(cls, f'undefined method provider {provider}.{method}')
                fn = copy.deepcopy(original)
                if named is not None:
                    fn.name = named
                    fn.decorator_list = []
                source_provider = provider
                outer = self

                class SuperRewriter(ast.NodeTransformer):
                    def visit_Call(self, node: ast.Call):
                        # Only super().method(...); other forms of super are
                        # rejected rather than emitted with incorrect MRO.
                        if (isinstance(node.func, ast.Attribute) and
                            isinstance(node.func.value, ast.Call) and
                            isinstance(node.func.value.func, ast.Name) and
                            node.func.value.func.id == 'super' and
                            not node.func.value.args and not node.func.value.keywords):
                            called_method = node.func.attr
                            index = mro.index(source_provider)
                            next_provider = next((candidate for candidate in mro[index+1:]
                                if called_method in original_methods[candidate]), None)
                            if next_provider is None:
                                # An explicit super().__init__() can resolve to
                                # object.__init__, which is a no-op with zero
                                # arguments. Other missing methods must fail.
                                if called_method != '__init__' or node.args or node.keywords:
                                    outer.fail(node, f'no super() target for {called_method}')
                                target = ('object', '__init__')
                                if target not in synthetic:
                                    synthetic[target] = f'_py12_super_{outer.sym("m").split("_")[-1]}'
                                    generated.append(ast.FunctionDef(name=synthetic[target],
                                        args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='self')],
                                            vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                                            defaults=[]), body=[ast.Pass()], decorator_list=[],
                                        returns=ast.Constant(value=None), type_comment=None))
                            else:
                                target = (next_provider, called_method)
                                if target not in synthetic:
                                    synthetic[target] = f'_py12_super_{outer.sym("m").split("_")[-1]}'
                                    generated.append(make_method(next_provider, called_method, named=synthetic[target]))
                            return ast.copy_location(ast.Call(
                                func=ast.Attribute(value=ast.Name(id='self', ctx=ast.Load()),
                                    attr=synthetic[target], ctx=ast.Load()),
                                args=[self.visit(a) for a in node.args],
                                keywords=[self.visit(k) for k in node.keywords]), node)
                        return self.generic_visit(node)

                    def visit_Name(self, node):
                        return node

                fn.body = [SuperRewriter().visit(s) for s in fn.body]
                for nested in ast.walk(fn):
                    if (isinstance(nested, ast.Call) and isinstance(nested.func, ast.Name)
                            and nested.func.id == 'super'):
                        outer.fail(nested, 'super() is supported only as a direct method call')
                return fn

            if '__init__' not in selected:
                # Python supplies object.__init__ when there is no inherited
                # constructor. The v4 backend otherwise rejects C().
                init = ast.FunctionDef(name='__init__',
                    args=ast.arguments(posonlyargs=[], args=[ast.arg(arg='self')],
                        vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                        defaults=[]),
                    body=[ast.Pass()], decorator_list=[],
                    returns=ast.Constant(value=None), type_comment=None)
                selected['__init__'] = (name, init)
            own_fields = set(original_fields[name])
            public_methods = [make_method(provider, method) for method, (provider, _) in selected.items()]
            # Ensure the helper namespace cannot collide with a user method.
            if any(method.startswith('_py12_super_') for method in original_methods[name]):
                self.fail(cls, 'reserved super-dispatch method name')
            cls.body = [*inherited, *[copy.deepcopy(d) for d in cls.body if isinstance(d, ast.AnnAssign)
                and isinstance(d.target, ast.Name) and d.target.id in own_fields],
                *public_methods, *generated]
            cls.bases = []


class InheritanceLowerer(RuntimeLowerer):
    def __init__(self, source: str, source_name: str = '<input>', *, show_return=False, threshold=8):
        self.v11_prepass = InheritancePass(source_name)
        lowered = self.v11_prepass.apply(source)
        self.v11_lowered = lowered
        ExceptionAttributeLowerer.__init__(self, lowered, source_name, show_return=show_return, threshold=threshold)

    def compile(self) -> str:
        text = super().compile()
        if not self.v11_prepass.parents:
            return text
        # V11 emits a single-parent predicate. The v12 C3 specialization
        # replaces it with transitive membership across *all* bases.
        substitutions = ['PyStr.equal(tag, expected)']
        for name, mro in sorted(self.v11_prepass.mros.items()):
            for ancestor in mro[1:]:
                substitutions.append(
                    f'Bool.and(PyStr.equal(tag, "{name}"), PyStr.equal(expected, "{ancestor}"))')
        expression = substitutions[0]
        for member in substitutions[1:]:
            expression = f'Bool.or({expression}, {member})'
        lines = text.splitlines(keepends=True)
        pos = next((i for i, line in enumerate(lines) if line.startswith('def PyType.is_subclass(')), None)
        if pos is None:
            raise AssertionError('missing emitted subtype predicate')
        end = pos + 1
        while end < len(lines) and not (lines[end].startswith('def ') or lines[end].startswith('type ') or lines[end].startswith('@unsafe def ')):
            end += 1
        lines[pos:end] = ['def PyType.is_subclass(+tag: String, +expected: String) -> Bool:\n',
                          f'  {expression}\n', '\n']
        return ''.join(lines)


def translate_inheritance_file(path: Path, *, show_return=False, threshold=8) -> str:
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
            raise TranslationError('mixing native stdlib and linked local imports needs unified resolution',
                                   source=str(path))
        source = RuntimeModuleLinker(path).link()
        keep = {a.name for n in root.body if isinstance(n, ast.Import) for a in n.names
                if a.name in {'gc', 'weakref'}}
        source = ''.join(f'import {name}\n' for name in sorted(keep)) + source
    return InheritanceLowerer(source, str(path), show_return=show_return, threshold=threshold).compile()


V12_CASES = {
    'c3_diamond': '''
class Root:
    def value(self) -> int: return 1
class Left(Root):
    def value(self) -> int: return 10 + super().value()
class Right(Root):
    def value(self) -> int: return 100 + super().value()
class Child(Left, Right):
    def value(self) -> int: return 1000 + super().value()
def main() -> int:
    obj = Child()
    return obj.value()
''',
    'c3_default_constructor': '''
class A:
    def value(self) -> int: return 4
class B:
    def value(self) -> int: return 9
class C(A, B):
    pass
def main() -> int:
    obj = C()
    return obj.value()
''',
    'finally_return_overrides_error': '''
def fail(n: int) -> int:
    if n < 0:
        raise ValueError('bad')
    return n

def main() -> int:
    try:
        out = fail(-1)
    except KeyError:
        out = 5
    finally:
        return 17
''',
}


def bootstrap_bend_wsl() -> str:
    """Opt-in installer for Bend 2.0.40 inside Windows WSL.

    The official installer verifies release tarball checksums. Do not
    automatically install software during ordinary smoke tests.
    """
    if sys.platform != 'win32':
        raise RuntimeError('Bend bootstrap through WSL requires Windows Python')
    script = (
        'set -eu; '
        'if [ ! -x "$HOME/.bend/bin/bend" ]; then '
        'curl -fsSL https://bend-lang.com/install.sh -o /tmp/pybend-v12-install.sh; '
        'sh /tmp/pybend-v12-install.sh; '
        'rm -f /tmp/pybend-v12-install.sh; fi; '
        '"$HOME/.bend/bin/bend" version; '
        'printf "PYBEND_PATH=%s\\n" "$HOME/.bend/bin/bend"'
    )
    try:
        result = subprocess.run(['wsl.exe', 'sh', '-lc', script], capture_output=True,
                                text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f'WSL_BEND_BOOTSTRAP_FAILED {error}') from error
    if result.returncode:
        raise RuntimeError('WSL_BEND_BOOTSTRAP_FAILED ' + (result.stdout + result.stderr)[-1200:])
    if '2.0.40' not in result.stdout:
        raise RuntimeError('BEND_VERSION_NOT_VERIFIED ' + result.stdout[-500:])
    matches = [line.partition('=')[2].strip() for line in result.stdout.splitlines()
               if line.startswith('PYBEND_PATH=')]
    if len(matches) != 1 or not matches[0].startswith('/'):
        raise RuntimeError('BEND_INSTALL_PATH_UNVERIFIED')
    return matches[0]


def run_smoke(*, bend='bend', wsl=False, source_only=False) -> int:
    if not source_only and not wsl and shutil.which(bend) is None:
        print('PY3_BEND_V12 BLOCKED missing-bend-compiler native_executed=0')
        return 2
    generated = executed = failed = 0
    with tempfile.TemporaryDirectory(prefix='pybend-v12-') as directory:
        for name, source in V12_CASES.items():
            py_path = Path(directory) / (name + '.py')
            bend_path = py_path.with_suffix('.bend')
            try:
                py_path.write_text(source, encoding='utf-8')
                bend_path.write_text(translate_inheritance_file(py_path, show_return=True), encoding='utf-8')
                generated += 1
                if source_only:
                    print(f'PY3_BEND_V12 {name} SOURCE_ONLY')
                    continue
                checked, report = run_bend_gate(bend_path, bend, wsl=wsl)
                if not checked:
                    raise RuntimeError('BEND_CHECK_FAILED ' + report)
                oracle = subprocess.run([sys.executable, '-B', '-c',
                    'import runpy; mod=runpy.run_path(' + repr(str(py_path)) + '); print(mod["main"]())'],
                    capture_output=True, text=True, timeout=30)
                if oracle.returncode:
                    raise RuntimeError('PY_ORACLE_FAILED ' + oracle.stderr[-1000:])
                observed = subprocess.run(bend_command(bend_path, bend=bend, wsl=wsl),
                                          capture_output=True, text=True, timeout=120)
                if observed.returncode != 0 or observed.stdout != oracle.stdout:
                    raise RuntimeError('BEND_DIFFERENTIAL_MISMATCH ' + repr({
                        'returncode': observed.returncode, 'observed': observed.stdout,
                        'expected': oracle.stdout, 'stderr': observed.stderr[-1000:]}))
                executed += 1
                print(f'PY3_BEND_V12 {name} BEND_NATIVE_PASS')
            except (OSError, RuntimeError, TranslationError, AssertionError, ValueError,
                    subprocess.TimeoutExpired) as error:
                failed += 1
                print(f'PY3_BEND_V12 {name} FAIL {error}')
    certified = executed == len(V12_CASES) and failed == 0
    print(f'PY3_BEND_V12 SUMMARY generated={generated} executed={executed} failed={failed} certified={certified}')
    return 0 if failed == 0 and (source_only or certified) else 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    tr = sub.add_parser('translate')
    tr.add_argument('input', type=Path)
    tr.add_argument('-o', '--output', type=Path)
    tr.add_argument('--show-return', action='store_true')
    tr.add_argument('--require-bend', action='store_true')
    tr.add_argument('--bend', default='bend')
    tr.add_argument('--wsl', action='store_true')
    tr.add_argument('--bootstrap-bend', action='store_true')
    sm = sub.add_parser('smoke')
    sm.add_argument('--bend', default='bend')
    sm.add_argument('--wsl', action='store_true')
    sm.add_argument('--source-only', action='store_true')
    sm.add_argument('--bootstrap-bend', action='store_true')
    args = parser.parse_args(argv)
    if args.bootstrap_bend:
        if not args.wsl:
            print('PY3_BEND_V12 ERROR --bootstrap-bend requires --wsl', file=sys.stderr)
            return 2
        try:
            args.bend = bootstrap_bend_wsl()
        except RuntimeError as error:
            print('PY3_BEND_V12 ERROR ' + str(error), file=sys.stderr)
            return 2
    if args.command == 'smoke':
        return run_smoke(bend=args.bend, wsl=args.wsl, source_only=args.source_only)
    try:
        code = translate_inheritance_file(args.input, show_return=args.show_return)
        path = args.output or args.input.with_suffix('.bend')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding='utf-8')
        print(f'PY3_BEND_V12 SOURCE_EMITTED {path} compiler_verified=false')
        if args.require_bend:
            passed, report = run_bend_gate(path, args.bend, wsl=args.wsl)
            print(f'PY3_BEND_V12 {report}')
            return 0 if passed else 2
        return 0
    except (OSError, SyntaxError, TranslationError, ValueError, AssertionError) as exc:
        print('PY3_BEND_V12 ERROR ' + str(exc), file=sys.stderr)
        return 2
