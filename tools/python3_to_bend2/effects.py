"""Explicit IO lowering for the supported file, lock and environment subset.

Unlike the pure heap emitter, this path sequences host effects in Bend's IO
monad. Linear file handles never enter the Python value heap. A lock is a
one-token channel, not a dummy object. Only straight-line entry-point code is
accepted until the general heap compiler supports effectful continuations.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass

from .integers import BIGINT_BEND, order_runtime_definitions
from .syntax import TranslationError


# Bound external reads, and fail explicitly if the bound is exceeded. Decode
# the supported ASCII text domain strictly, including Python universal newlines.
# Environment enumeration uses the Linux process's initial NUL-delimited block.
_READ_RUNTIME = r'''
def PyEffects.check_bytes(ok: Bool, bytes: List<&2, U32>) -> IO(List<&2, U32>):
  match ok:
    case True{}:
      IO.pure(List<&2, U32>, bytes)
    case False{}:
      IO.die(List<&2, U32>, 1, "unsupported file or environment size: exceeds 1 MiB")

def PyEffects.limit_bytes(+bytes: List<&2, U32>) -> IO(List<&2, U32>):
  PyEffects.check_bytes(Nat.is_le(List.length(&2, U32, bytes), 1048576n), bytes)

def PyEffects.close_read(pair: File & Result<&1, &1, U32 & String, List<&2, U32>>) -> IO(List<&2, U32>):
  (handle, result) = pair
  do IO<List<&2, U32>>:
    File.close(handle)
    bytes : List<&2, U32> <- IO.pass(List<&2, U32>, result)
    checked : List<&2, U32> <- PyEffects.limit_bytes(bytes)
    return checked

def PyEffects.read_open(handle: File) -> IO(List<&2, U32>):
  do IO<List<&2, U32>>:
    pair : File & Result<&1, &1, U32 & String, List<&2, U32>> <- File.read_bytes(handle, 1048577)
    bytes : List<&2, U32> <- PyEffects.close_read(pair)
    return bytes

def PyEffects.read_path(path: String) -> IO(List<&2, U32>):
  do IO<List<&2, U32>>:
    handle : File <- IO.try(File, File.open(path, "r"))
    bytes : List<&2, U32> <- PyEffects.read_open(handle)
    return bytes
'''

_TEXT_RUNTIME = r'''
def PyEffects.prepend(c: U32, rest: Maybe<&2, String>) -> Maybe<&2, String>:
  match rest:
    case None{}:
      None{}
    case Some{text}:
      Some{SCon{Chr{c}, text}}

@unsafe def PyEffects.decode(bytes: List<&2, U32>) -> Maybe<&2, String>:
  match bytes:
    case Nil{}:
      Some{""}
    case Con{+c, tail}:
      PyEffects.decode_char(U32.is_le(c, 127), U32.is_eq(c, 13), c, tail)

@unsafe def PyEffects.decode_char(valid: Bool, cr: Bool, c: U32, tail: List<&2, U32>) -> Maybe<&2, String>:
  match valid cr:
    case False{} _:
      None{}
    case True{} True{}:
      PyEffects.prepend(10, PyEffects.after_cr(tail))
    case True{} False{}:
      PyEffects.prepend(c, PyEffects.decode(tail))

@unsafe def PyEffects.after_cr(bytes: List<&2, U32>) -> Maybe<&2, String>:
  match bytes:
    case Nil{}:
      Some{""}
    case Con{+c, tail}:
      PyEffects.skip_lf(U32.is_eq(c, 10), c, tail)

@unsafe def PyEffects.skip_lf(lf: Bool, c: U32, tail: List<&2, U32>) -> Maybe<&2, String>:
  match lf:
    case True{}:
      PyEffects.decode(tail)
    case False{}:
      PyEffects.decode(c <> tail)

def PyEffects.text_result(decoded: Maybe<&2, String>) -> IO(String):
  match decoded:
    case None{}:
      IO.die(String, 1, "unsupported text encoding: only ASCII file contents are supported")
    case Some{text}:
      IO.pure(String, text)

def PyEffects.read_text(handle: File) -> IO(String):
  do IO<String>:
    bytes : List<&2, U32> <- PyEffects.read_open(handle)
    text : String <- PyEffects.text_result(PyEffects.decode(bytes))
    return text
'''

_ENV_RUNTIME = r'''
def PyEffects.count_entry(zero: Bool, rest: Nat) -> Nat:
  match zero:
    case True{}:
      1n+rest
    case False{}:
      rest

def PyEffects.count_entries(bytes: List<&2, U32>) -> Nat:
  match bytes:
    case Nil{}:
      0n
    case Con{c, tail}:
      PyEffects.count_entry(U32.is_eq(c, 0), PyEffects.count_entries(tail))

def PyEffects.environment_size() -> IO(PyInt):
  do IO<PyInt>:
    bytes : List<&2, U32> <- PyEffects.read_path("/proc/self/environ")
    return PyInt.positive(PyMag.from_nat(PyEffects.count_entries(bytes)))
'''

_LOCK_RUNTIME = r'''
def PyEffects.sent(status: Result<&1, &1, U32, Unit>, chan: Chan(U32)) -> IO(Chan(U32)):
  match status:
    case Done{unit}:
      IO.pure(Chan(U32), chan)
    case Fail{unit}:
      IO.die(Chan(U32), 1, "RuntimeError: lock channel closed")

def PyEffects.release(+chan: Chan(U32)) -> IO(Chan(U32)):
  do IO<Chan(U32)>:
    status : Result<&1, &1, U32, Unit> <- Chan.send(U32, chan, 1)
    result : Chan(U32) <- PyEffects.sent(status, chan)
    return result

def PyEffects.new_lock() -> IO(Chan(U32)):
  do IO<Chan(U32)>:
    chan : Chan(U32) <- Chan.new(U32, 1)
    result : Chan(U32) <- PyEffects.release(chan)
    return result

def PyEffects.received(token: Maybe<&1, U32>, chan: Chan(U32)) -> IO(Chan(U32)):
  match token:
    case Some{unit}:
      IO.pure(Chan(U32), chan)
    case None{}:
      IO.die(Chan(U32), 1, "RuntimeError: lock channel closed")

def PyEffects.acquire(+chan: Chan(U32)) -> IO(Chan(U32)):
  do IO<Chan(U32)>:
    token : Maybe<&1, U32> <- Chan.recv(U32, chan)
    result : Chan(U32) <- PyEffects.received(token, chan)
    return result
'''


@dataclass(frozen=True)
class Value:
    expression: str
    type: str


@dataclass
class Lock:
    channel: str
    held: bool = False


@dataclass
class FileScope:
    handle: str
    consumed: bool = False


def uses_effects(tree: ast.Module) -> bool:
    """Choose the effect backend by AST, never by a smoke-probe name."""
    return any(
        (isinstance(n, ast.Import) and any(a.name in {'os', 'threading'} for a in n.names))
        or (isinstance(n, ast.ImportFrom) and n.module in {'os', 'threading'})
        or (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'open')
        for n in ast.walk(tree)
    )


class EffectCompiler:
    def __init__(self, tree: ast.Module, filename: str, *, show_return: bool):
        self.tree = tree
        self.filename = filename
        self.show_return = show_return
        self.lines: list[str] = []
        self.values: dict[str, Value | Lock] = {}
        self.modules: dict[str, str] = {}
        self.files: dict[str, FileScope] = {}
        self.locks: list[Lock] = []
        self.used: set[str] = set()
        self.serial = 0

    def fail(self, node: ast.AST, message: str):
        raise TranslationError(message, node, self.filename)

    def fresh(self) -> str:
        self.serial += 1
        return f'effect_{self.serial}'

    def bind(self, typ: str, expression: str, *, effect: bool = False) -> Value:
        name = self.fresh()
        # Data locals can be used repeatedly; channel and file handles remain
        # affine in this emitter and are threaded by the effect helpers.
        prefix = '' if typ in {'File', 'Chan(U32)'} else '+'
        operator = '<-' if effect else '='
        # IO binds desugar to ordinary affine lambda binders. Copyable data
        # is explicitly rebound with + in the pure part, not the IO binder.
        if effect:
            self.lines.append(f'{name} : {typ} <- {expression}')
            if prefix:
                reused = self.fresh()
                self.lines.append(f'+{reused} : {typ} = {name}')
                name = reused
        else:
            self.lines.append(f'{prefix}{name} : {typ} {operator} {expression}')
        return Value(name, typ)

    def literal(self, node: ast.Constant) -> Value:
        value = node.value
        if type(value) is int:
            digits = ', '.join(str(d) for d in reversed(str(abs(value)))) if value else ''
            return Value(f'Py{"Neg" if value < 0 else "Pos"}{{[{digits}]}}', 'PyInt')
        if type(value) is bool:
            return Value('True{}' if value else 'False{}', 'Bool')
        if type(value) is str:
            if any(ord(c) < 32 or ord(c) > 126 or c in '"\\' for c in value):
                self.fail(node, 'unsupported effect string literal escape')
            return Value(f'"{value}"', 'String')
        if value is None:
            return Value('Unit{}', 'Unit')
        self.fail(node, 'unsupported effect literal')

    def module_attribute(self, node: ast.AST, module: str, attribute: str) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == attribute
                and isinstance(node.value, ast.Name) and self.modules.get(node.value.id) == module
                and node.value.id not in self.values and node.value.id not in self.files)

    def scalar(self, node: ast.expr) -> Value:
        value = self.expr(node)
        if isinstance(value, Lock):
            self.fail(node, 'lock object cannot escape into a pure value')
        return value

    def expr(self, node: ast.expr) -> Value | Lock:
        if isinstance(node, ast.Constant):
            return self.literal(node)
        if isinstance(node, ast.Name) and node.id in self.values:
            return self.values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            val = self.scalar(node.operand)
            if val.type != 'PyInt':
                self.fail(node, 'unary minus requires an integer')
            return self.bind('PyInt', f'PyInt.sub(PyPos{{[]}}, {val.expression})')
        if isinstance(node, ast.BinOp) and type(node.op) in {ast.Add, ast.Sub, ast.Mult}:
            left, right = self.scalar(node.left), self.scalar(node.right)
            if left.type == right.type == 'PyInt':
                op = {ast.Add: 'add', ast.Sub: 'sub', ast.Mult: 'mul'}[type(node.op)]
                return self.bind('PyInt', f'PyInt.{op}({left.expression}, {right.expression})')
            if left.type == right.type == 'String' and isinstance(node.op, ast.Add):
                return self.bind('String', f'{left.expression} ++ {right.expression}')
            self.fail(node, 'unsupported effect arithmetic types')
        if not isinstance(node, ast.Call) or node.keywords:
            self.fail(node, 'unsupported expression in effectful entry point')
        if isinstance(node.func, ast.Name) and node.func.id == 'len' and len(node.args) == 1 and 'len' not in self.values:
            if self.module_attribute(node.args[0], 'os', 'environ'):
                self.used.add('environment')
                return self.bind('PyInt', 'PyEffects.environment_size()', effect=True)
            value = self.scalar(node.args[0])
            if value.type != 'String':
                self.fail(node, 'effect len supports text or os.environ')
            return self.bind('PyInt', f'PyInt.positive(PyMag.from_nat(String.length({value.expression})))')
        if self.module_attribute(node.func, 'threading', 'Lock') and not node.args:
            self.used.add('lock')
            value = self.bind('Chan(U32)', 'PyEffects.new_lock()', effect=True)
            lock = Lock(value.expression)
            self.locks.append(lock)
            return lock
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            receiver, method = node.func.value.id, node.func.attr
            if receiver in self.files:
                scope = self.files[receiver]
                if method != 'read' or node.args or scope.consumed:
                    self.fail(node, 'file scope supports one complete read() and no escaping handle')
                scope.consumed = True
                return self.bind('String', f'PyEffects.read_text({scope.handle})', effect=True)
            lock = self.values.get(receiver)
            if isinstance(lock, Lock):
                if method == 'locked' and not node.args:
                    return Value('True{}' if lock.held else 'False{}', 'Bool')
                if method == 'acquire' and (not node.args or (len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and type(node.args[0].value) is bool)):
                    blocking = not node.args or node.args[0].value
                    if lock.held:
                        if blocking:
                            self.fail(node, 'blocking re-acquire requires concurrent execution')
                        return Value('False{}', 'Bool')
                    lock.channel = self.bind('Chan(U32)', f'PyEffects.acquire({lock.channel})', effect=True).expression
                    lock.held = True
                    return Value('True{}', 'Bool')
                if method == 'release' and not node.args:
                    if not lock.held:
                        self.fail(node, 'release of unlocked lock is outside supported effect exception handling')
                    lock.channel = self.bind('Chan(U32)', f'PyEffects.release({lock.channel})', effect=True).expression
                    lock.held = False
                    return Value('Unit{}', 'Unit')
        self.fail(node, 'unsupported call in effectful entry point')

    def text(self, value: Value) -> str:
        if value.type == 'PyInt':
            return f'PyInt.show({value.expression})'
        if value.type == 'String':
            return value.expression
        if value.type == 'Bool':
            return f'PyEffects.bool_text({value.expression})'
        return '"None"'

    def close_file(self, scope: FileScope):
        if not scope.consumed:
            self.lines.append(f'File.close({scope.handle})')
            scope.consumed = True

    def finish(self, value: Value):
        for scope in reversed(list(self.files.values())):
            self.close_file(scope)
        for lock in reversed(self.locks):
            if not lock.held:
                lock.channel = self.bind('Chan(U32)', f'PyEffects.acquire({lock.channel})', effect=True).expression
            self.lines.append(f'Chan.close(U32, {lock.channel})')
        if self.show_return:
            self.lines.append(f'IO.print({self.text(value)})')
        else:
            self.lines.append('return Unit{}')

    def block(self, statements: list[ast.stmt]) -> bool:
        for stmt in statements:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Return):
                self.finish(self.scalar(stmt.value) if stmt.value else Value('Unit{}', 'Unit'))
                return True
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                target = stmt.targets[0].id
                if target in self.files or target in self.modules or target in {'open', 'len', 'print'}:
                    self.fail(stmt, 'effect binding shadows a reserved resource or builtin')
                value = self.expr(stmt.value)
                # A fresh SSA binding freezes any literal/expression at this point.
                self.values[target] = self.bind(value.type, value.expression) if isinstance(value, Value) else value
                continue
            if isinstance(stmt, ast.Expr):
                call = stmt.value
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'print' and len(call.args) == 1 and not call.keywords and 'print' not in self.values:
                    self.lines.append(f'IO.print({self.text(self.scalar(call.args[0]))})')
                else:
                    self.expr(call)
                continue
            if isinstance(stmt, ast.With) and len(stmt.items) == 1:
                item = stmt.items[0]
                call = item.context_expr
                if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != 'open'
                    or call.keywords or len(call.args) not in (1, 2) or 'open' in self.values
                    or (len(call.args) == 2 and not (isinstance(call.args[1], ast.Constant) and call.args[1].value == 'r'))
                    or not isinstance(item.optional_vars, ast.Name)):
                    self.fail(stmt, 'only with open(path[, "r"]) as name is supported')
                name = item.optional_vars.id
                if name in self.values or name in self.files or name in self.modules or name in {'open','len','print'}:
                    self.fail(stmt, 'file binding must be fresh and nonescaping')
                path = self.scalar(call.args[0])
                if path.type != 'String':
                    self.fail(stmt, 'file path must be text')
                self.used.add('file')
                handle = self.bind('File', f'IO.try(File, File.open({path.expression}, "r"))', effect=True)
                scope = self.files[name] = FileScope(handle.expression)
                done = self.block(stmt.body)
                self.close_file(scope)
                del self.files[name]
                if done:
                    return True
                continue
            self.fail(stmt, f'unsupported effect statement {type(stmt).__name__}')
        return False

    def compile(self) -> str:
        main = None
        for node in self.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in {'os', 'threading'}:
                        self.fail(node, 'unsupported module in effectful program')
                    key = alias.asname or alias.name
                    if key in self.modules or key in {'open', 'len', 'print'}:
                        self.fail(node, 'ambiguous effect import binding')
                    self.modules[key] = alias.name
            elif isinstance(node, ast.FunctionDef) and node.name == 'main' and main is None:
                main = node
            else:
                self.fail(node, 'effect backend currently supports one synchronous main and effect imports')
        if main is None:
            self.fail(self.tree, 'effect backend requires main()')
        args = main.args
        if main.returns is not None and not (isinstance(main.returns, ast.Name) and main.returns.id in {'int', 'str', 'bool'}):
            if not (isinstance(main.returns, ast.Constant) and main.returns.value is None):
                self.fail(main, 'unsupported effect entry-point annotation')
        if main.decorator_list or args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg:
            self.fail(main, 'effect main must have no arguments or decorators')
        if not self.block(main.body):
            self.finish(Value('Unit{}', 'Unit'))
        parts = [BIGINT_BEND, '''
def PyEffects.bool_text(value: Bool) -> String:
  match value:
    case True{}:
      "True"
    case False{}:
      "False"
''']
        if self.used & {'file', 'environment'}:
            parts.append(_READ_RUNTIME)
        if 'file' in self.used:
            parts.append(_TEXT_RUNTIME)
        if 'environment' in self.used:
            parts.append(_ENV_RUNTIME)
        if 'lock' in self.used:
            parts.append(_LOCK_RUNTIME)
        parts.append('def main() -> IO(Unit):\n  do IO<Unit>:\n' + '\n'.join('    ' + line for line in self.lines))
        return 'import Base\n\n' + order_runtime_definitions('\n'.join(parts)) + '\n'
