"""Experimental Python 3 AST -> Bend 2.0 source translator.

Conservative pure/typed subset. Translation is not equivalence certification.
Run `python -m tools.python3_to_bend2.syntax smoke` from the repository root.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

U32_MAX = (1 << 32) - 1
NAT_MAX = (1 << 48) - 1
TYPE_MAP = {"int": "U32", "float": "F32", "bool": "Bool", "str": "String",
            "U32": "U32", "Nat": "Nat", "F32": "F32", "Bool": "Bool", "String": "String"}
BIN_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.FloorDiv: "/",
           ast.Mod: "%", ast.Div: "/"}
CMP_OPS = {ast.Eq: "is_eq", ast.NotEq: "is_ne", ast.Lt: "is_lt",
           ast.LtE: "is_le", ast.Gt: "is_gt", ast.GtE: "is_ge"}
RESERVED = {"Base", "U32", "Nat", "F32", "Bool", "String", "True", "False", "IO", "Unit", "List", "Data"}


class TranslationError(Exception):
    """Located rejection rather than guessed semantics."""

    def __init__(self, message: str, node: ast.AST | None = None, source: str = "<input>") -> None:
        self.message = message
        self.line = getattr(node, "lineno", 0)
        self.column = getattr(node, "col_offset", 0) + 1 if node is not None else 0
        self.source = source
        super().__init__(f"{source}:{self.line}:{self.column}: {message}")


@dataclass(frozen=True)
class Symbol:
    name: str
    typ: str


@dataclass(frozen=True)
class Signature:
    name: str
    params: tuple[Symbol, ...]
    returns: str


@dataclass
class Definition:
    signature: Signature
    body: list[str]
    dependencies: set[str] = field(default_factory=set)
    locals: set[str] = field(default_factory=set)

    def format(self) -> str:
        raw_body = "\n".join(self.body)
        def occurrences(name: str) -> int:
            return len(re.findall(r"(?<![\w])" + re.escape(name) + r"(?![\w])", raw_body))
        parts = []
        for p in self.signature.params:
            prefix = "+" if occurrences(p.name) > 1 else ""
            parts.append(f"{prefix}{p.name}: {p.typ}")
        lines = [f"def {self.signature.name}({', '.join(parts)}) -> {self.signature.returns}:"]
        for raw in self.body:
            line = raw
            for name in self.locals:
                # A fresh binding has one definition; all later occurrences are reads.
                if occurrences(name) >= 3:
                    line = re.sub(r"^(\s*)" + re.escape(name) + r"(\s*=)",
                                  lambda m: m.group(1) + "+" + name + m.group(2), line, count=1)
            lines.append("  " + line)
        return "\n".join(lines)


class Translator:
    def __init__(self, source: str, source_name: str = "<input>") -> None:
        self.source = source
        self.source_name = source_name
        self.tree = ast.parse(source, filename=source_name)
        self.signatures: dict[str, Signature] = {}
        self.defs: dict[str, Definition] = {}
        self.counters: dict[str, int] = {}
        self.helper_counter = 0

    def fail(self, message: str, node: ast.AST) -> None:
        raise TranslationError(message, node, self.source_name)

    def identifier(self, name: str, node: ast.AST) -> str:
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name)
                or "__" in name or name in RESERVED or name.startswith(("pyif_", "pyssa_", "pycond_"))):
            self.fail(f"identifier {name!r} not supported in Bend source", node)
        return name

    def annotate(self, node: ast.expr | None) -> str:
        if not isinstance(node, ast.Name) or node.id not in TYPE_MAP:
            self.fail("requires a scalar annotation (int/U32/Nat/bool/float/str or Bend equivalents)", node or self.tree)
        return TYPE_MAP[node.id]

    def scan(self) -> None:
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef,)):
                self.identifier(node.name, node)
                if node.name in self.signatures:
                    self.fail(f"duplicate function {node.name}", node)
                if node.decorator_list or node.type_params:
                    self.fail("decorators and generic functions are not supported", node)
                args = node.args
                if (args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg
                        or args.defaults or any(d is not None for d in args.kw_defaults)):
                    self.fail("only annotated positional parameters without defaults are supported", node)
                params = tuple(Symbol(self.identifier(a.arg, a), self.annotate(a.annotation)) for a in args.args)
                if len(set(p.name for p in params)) != len(params):
                    self.fail("duplicate parameter", node)
                self.signatures[node.name] = Signature(node.name, params, self.annotate(node.returns))
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                pass  # module docstring
            elif (isinstance(node, ast.ImportFrom) and node.module == "__future__" and
                  all(a.name == "annotations" for a in node.names)):
                pass
            else:
                self.fail(f"top-level {type(node).__name__} cannot be translated", node)
        if not self.signatures:
            self.fail("no functions found", self.tree)

    def fresh(self, stem: str) -> str:
        count = self.counters.get(stem, 0) + 1
        self.counters[stem] = count
        return f"pyssa_{stem}_{count}"

    def hint(self, expr: ast.expr, env: dict[str, Symbol]) -> str | None:
        if isinstance(expr, ast.Name):
            return env.get(expr.id, Symbol("", "")).typ or None
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            sig = self.signatures.get(expr.func.id)
            return sig.returns if sig else None
        if isinstance(expr, ast.Constant):
            if type(expr.value) is bool:
                return "Bool"
            if type(expr.value) is float:
                return "F32"
            if type(expr.value) is str:
                return "String"
        if isinstance(expr, ast.Compare) or isinstance(expr, ast.BoolOp):
            return "Bool"
        if isinstance(expr, ast.BinOp):
            return self.hint(expr.left, env) or self.hint(expr.right, env)
        if isinstance(expr, ast.IfExp):
            return self.hint(expr.body, env) or self.hint(expr.orelse, env)
        return None

    def expression(self, node: ast.expr, env: dict[str, Symbol], owner: Definition,
                   expected: str | None = None) -> tuple[str, str]:
        if isinstance(node, ast.Constant):
            value = node.value
            if type(value) is bool:
                result = ("True{}" if value else "False{}", "Bool")
            elif type(value) is int:
                typ = expected or "U32"
                if typ not in ("U32", "Nat"):
                    self.fail("integer literal requires U32/Nat context", node)
                if value < 0 or value > U32_MAX:
                    self.fail(f"integer literal outside nonnegative {typ} range", node)
                result = (f"{value}n" if typ == "Nat" else str(value), typ)
            elif type(value) is float:
                if not math.isfinite(value):
                    self.fail("nonfinite floating literal", node)
                s = repr(value)
                result = (s, "F32")
            elif type(value) is str:
                import json
                result = (json.dumps(value, ensure_ascii=True), "String")
            else:
                self.fail(f"literal {value!r} not supported", node)
        elif isinstance(node, ast.Name):
            if node.id not in env:
                self.fail(f"unbound or nonlocal name {node.id!r}", node)
            v = env[node.id]
            result = (v.name, v.typ)
        elif isinstance(node, ast.BinOp):
            if type(node.op) not in BIN_OPS:
                self.fail(f"unsupported binary operation {type(node.op).__name__}", node)
            typ = expected or self.hint(node.left, env) or self.hint(node.right, env) or "U32"
            if typ not in ("U32", "Nat", "F32", "String"):
                self.fail("arithmetic requires numeric/String operands", node)
            op = BIN_OPS[type(node.op)]
            if typ == "String" and type(node.op) is not ast.Add:
                self.fail("only String concatenation is supported", node)
            if typ == "Nat" and type(node.op) is ast.Sub:
                self.fail("Nat subtraction needs a proof of nonnegativity", node)
            if type(node.op) is ast.Div and typ != "F32":
                self.fail("Python '/' produces floats; only F32 division can be lowered", node)
            if type(node.op) is ast.FloorDiv and typ == "F32":
                self.fail("F32 floor division is unsupported", node)
            if type(node.op) is ast.Mod and typ == "F32":
                self.fail("F32 modulo is unsupported", node)
            if type(node.op) in (ast.Mod, ast.FloorDiv, ast.Div):
                # Unlike Python, Bend U32 division by zero returns a value.
                # No function preconditions exist yet, so accept only an obvious
                # nonzero denominator for those operators.
                divisor = node.right
                if not (isinstance(divisor, ast.Constant)
                        and type(divisor.value) in (int, float) and divisor.value != 0):
                    self.fail("division/modulo needs a nonzero literal divisor in v0 (zero behavior differs)", divisor)
            left, lt = self.expression(node.left, env, owner, typ)
            right, rt = self.expression(node.right, env, owner, typ)
            if lt != typ or rt != typ:
                self.fail(f"binary operation operand types {lt}, {rt} disagree with {typ}", node)
            if typ == "String":
                result = (f"({left} ++ {right})", "String")
            else:
                result = (f"({left} {op} {right} : {typ})", typ)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            operand, typ = self.expression(node.operand, env, owner, "Bool")
            if typ != "Bool":
                self.fail("'not' requires Bool", node)
            result = (f"Bool.not({operand})", "Bool")
        elif isinstance(node, ast.BoolOp):
            op = " && " if isinstance(node.op, ast.And) else " || "
            vals = [self.expression(v, env, owner, "Bool") for v in node.values]
            if any(t != "Bool" for _, t in vals):
                self.fail("and/or require Bool operands, not Python truthy values", node)
            result = ("(" + op.join(x for x, _ in vals) + ")", "Bool")
        elif isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1 or type(node.ops[0]) not in CMP_OPS:
                self.fail("only a single U32/Nat comparison is supported", node)
            typ = self.hint(node.left, env) or self.hint(node.comparators[0], env) or "U32"
            if typ not in ("U32", "Nat"):
                self.fail("comparison currently supported for U32/Nat only", node)
            left, lt = self.expression(node.left, env, owner, typ)
            right, rt = self.expression(node.comparators[0], env, owner, typ)
            if lt != typ or rt != typ:
                self.fail("comparison operand types mismatch", node)
            result = (f"{typ}.{CMP_OPS[type(node.ops[0])]}({left}, {right})", "Bool")
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in self.signatures:
                self.fail("only calls to locally declared pure functions are supported", node)
            if node.keywords:
                self.fail("keyword calls are not supported", node)
            sig = self.signatures[node.func.id]
            if len(node.args) != len(sig.params):
                self.fail(f"{sig.name} expects {len(sig.params)} parameters", node)
            arg_exprs = []
            for arg, param in zip(node.args, sig.params):
                s, typ = self.expression(arg, env, owner, param.typ)
                if typ != param.typ:
                    self.fail(f"{sig.name} argument {param.name} requires {param.typ}, got {typ}", arg)
                arg_exprs.append(s)
            owner.dependencies.add(sig.name)
            result = (f"{sig.name}({', '.join(arg_exprs)})", sig.returns)
        elif isinstance(node, ast.IfExp):
            fake = ast.If(test=node.test,
                          body=[ast.copy_location(ast.Return(value=node.body), node.body)],
                          orelse=[ast.copy_location(ast.Return(value=node.orelse), node.orelse)])
            ast.copy_location(fake, node)
            result = (self.lower_if(fake, env, owner, expected), expected or self.hint(node.body, env) or "U32")
        else:
            self.fail(f"unsupported expression {type(node).__name__}", node)
        if expected is not None and result[1] != expected:
            self.fail(f"expected {expected}, got {result[1]}", node)
        return result

    def assigned(self, target: ast.expr, value: ast.expr, env: dict[str, Symbol],
                 owner: Definition, explicit: str | None = None) -> str:
        if not isinstance(target, ast.Name):
            self.fail("only assignment to a simple variable is supported", target)
        logical = self.identifier(target.id, target)
        if logical in self.signatures:
            self.fail("local binding shadows a function name", target)
        if explicit is not None and logical in env and env[logical].typ != explicit:
            self.fail("reassignment changes the variable's type", target)
        need = explicit or (env[logical].typ if logical in env else None)
        rhs, typ = self.expression(value, env, owner, need)
        physical = logical if logical not in env else self.fresh(logical)
        env[logical] = Symbol(physical, typ)
        owner.locals.add(physical)
        return f"{physical} = {rhs}"

    def lower_if(self, node: ast.If, env: dict[str, Symbol], owner: Definition,
                 expected: str | None) -> str:
        if not node.orelse:
            self.fail("if requires an else branch; no implicit fallthrough", node)
        cond, typ = self.expression(node.test, env, owner, "Bool")
        if typ != "Bool":
            self.fail("if condition must be Bool", node.test)
        self.helper_counter += 1
        name = f"pyif_{self.helper_counter:04d}"
        flag = f"pycond_{self.helper_counter:04d}"
        # Capture only previously bound variables needed inside the arms.
        reads = {n.id for arm in (node.body + node.orelse) for n in ast.walk(arm)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        captures = [(logical, v) for logical, v in env.items() if logical in reads]
        args = [Symbol(flag, "Bool")] + [Symbol(v.name, v.typ) for _, v in captures]
        ret = expected or self.guess_branch_return(node, env)
        helper = Definition(Signature(name, tuple(args), ret), [], set(), set())
        helper_env = {logical: Symbol(v.name, v.typ) for logical, v in captures}
        true_body = self.block(node.body, dict(helper_env), helper, ret)
        false_body = self.block(node.orelse, dict(helper_env), helper, ret)
        helper.body = [f"match {flag}:", "  case True{}:"]
        helper.body += ["    " + s for s in true_body]
        helper.body += ["  case False{}:"]
        helper.body += ["    " + s for s in false_body]
        self.defs[name] = helper
        owner.dependencies.add(name)
        return f"{name}({', '.join([cond] + [v.name for _, v in captures])})"

    def guess_branch_return(self, node: ast.If, env: dict[str, Symbol]) -> str:
        for branch in (node.body, node.orelse):
            for statement in reversed(branch):
                if isinstance(statement, ast.Return) and statement.value is not None:
                    return self.hint(statement.value, env) or "U32"
        return "U32"

    def block(self, stmts: list[ast.stmt], env: dict[str, Symbol], owner: Definition,
              expected: str) -> list[str]:
        lines: list[str] = []
        stmts = [s for s in stmts if not (isinstance(s, ast.Expr)
                  and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))]
        if not stmts:
            self.fail("empty function or branch", self.tree)
        for index, statement in enumerate(stmts):
            terminal = index == len(stmts) - 1
            if isinstance(statement, ast.Assign):
                if len(statement.targets) != 1:
                    self.fail("chained assignments are unsupported", statement)
                lines.append(self.assigned(statement.targets[0], statement.value, env, owner))
            elif isinstance(statement, ast.AnnAssign):
                if statement.value is None:
                    self.fail("annotated declarations require an initial value", statement)
                lines.append(self.assigned(statement.target, statement.value, env, owner,
                                           self.annotate(statement.annotation)))
            elif isinstance(statement, ast.AugAssign):
                if not isinstance(statement.target, ast.Name) or statement.target.id not in env:
                    self.fail("augmented assignment requires an existing local", statement)
                expr = ast.BinOp(left=ast.copy_location(ast.Name(id=statement.target.id,
                              ctx=ast.Load()), statement.target), op=statement.op,
                              right=statement.value)
                ast.copy_location(expr, statement)
                lines.append(self.assigned(statement.target, expr, env, owner))
            elif isinstance(statement, ast.Return):
                if not terminal:
                    self.fail("return must be terminal in this subset", statement)
                if statement.value is None:
                    self.fail("bare return is unsupported", statement)
                expr, _ = self.expression(statement.value, env, owner, expected)
                lines.append(expr)
            elif isinstance(statement, ast.If):
                if not terminal:
                    self.fail("if must terminate the current block", statement)
                lines.append(self.lower_if(statement, env, owner, expected))
            else:
                self.fail(f"unsupported statement {type(statement).__name__}", statement)
        if not isinstance(stmts[-1], (ast.Return, ast.If)):
            self.fail("function must end with return or a returning if/else", stmts[-1])
        return lines

    def compile(self) -> str:
        self.scan()
        for statement in self.tree.body:
            if not isinstance(statement, ast.FunctionDef):
                continue
            sig = self.signatures[statement.name]
            env = {p.name: p for p in sig.params}
            owner = Definition(sig, [], set(), set())
            owner.body = self.block(statement.body, env, owner, sig.returns)
            self.defs[sig.name] = owner
        ordered = []
        marks: dict[str, int] = {}
        def visit(name: str) -> None:
            if marks.get(name) == 1:
                raise TranslationError(f"call dependency cycle involving {name}; recursion needs a structural termination lowering",
                                       self.tree, self.source_name)
            if marks.get(name) == 2:
                return
            marks[name] = 1
            for dep in sorted(self.defs[name].dependencies):
                visit(dep)
            marks[name] = 2
            ordered.append(self.defs[name].format())
        for name in self.defs:
            visit(name)
        banner = "# Generated by python3_to_bend2_ast (conservative subset)\n# int -> U32: nonnegative 32-bit wrapping, NOT general Python int\nimport Base"
        return banner + "\n\n" + "\n\n".join(ordered) + "\n"


SMOKE_CASES = [
    ("arithmetic", "def main() -> int:\n    x = 5\n    x += 3\n    return x * 7\n", 56),
    ("cross_function", "def main() -> int:\n    return square(7)\n\ndef square(x: int) -> int:\n    return x * x\n", 49),
    ("branch", "def choose(x: int, y: int) -> int:\n    if x > y:\n        return x - y\n    else:\n        return y - x\n\ndef main() -> int:\n    return choose(17, 9)\n", 8),
    ("nested", "def main() -> int:\n    a = 6\n    b = 3\n    if a > b:\n        if b > 1:\n            return a * b\n        else:\n            return a\n    else:\n        return 0\n", 18),
    ("ternary", "def clamp(x: int, limit: int) -> int:\n    return limit if x > limit else x\n\ndef main() -> int:\n    return clamp(17, 9)\n", 9),
]


def bend_command(path: Path, *, bend: str = "bend", wsl: bool = False,
                 check_only: bool = False) -> list[str]:
    """Create a Bend command. WSL mode translates Windows paths explicitly."""
    if wsl:
        if sys.platform != "win32":
            raise OSError("--wsl requires Windows Python and wsl.exe")
        mapped = subprocess.run(["wsl.exe", "wslpath", "-a", str(path.resolve())],
                                capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        base = ["wsl.exe", bend, mapped]
    else:
        base = [bend, str(path)]
    return base + (["--check-only"] if check_only else [])


def check_bend(path: Path, *, bend: str = "bend", wsl: bool = False,
               timeout: float = 25.0, expected: int | None = None) -> None:
    """Require checker success; optionally require the exact pure-main result."""
    check = subprocess.run(bend_command(path, bend=bend, wsl=wsl, check_only=True),
                           capture_output=True, text=True, timeout=timeout, check=False)
    if check.returncode != 0 or "ALL PROOFS CHECK" not in check.stdout:
        raise AssertionError(f"Bend check exit {check.returncode}: {(check.stdout + check.stderr)[-1400:]}")
    if expected is not None:
        run = subprocess.run(bend_command(path, bend=bend, wsl=wsl),
                             capture_output=True, text=True, timeout=timeout, check=False)
        actual = run.stdout.strip()
        if run.returncode != 0 or actual != str(expected):
            raise AssertionError(f"Bend result {actual!r} != {expected!r}; exit={run.returncode}; stderr={run.stderr[-700:]}")


def smoke(bend: str | None = None, timeout: float = 25.0, *, wsl: bool = False) -> bool:
    checks = 0
    bad = 0
    path = (bend or "bend") if wsl else (bend or shutil.which("bend"))
    with tempfile.TemporaryDirectory(prefix="py3bend2-") as temp:
        for label, source, expected in SMOKE_CASES:
            try:
                translated = Translator(source, label + ".py").compile()
                ns: dict[str, object] = {}
                exec(compile(source, label + ".py", "exec"), ns)
                actual = ns["main"]()
                if actual != expected:
                    raise AssertionError(f"Python oracle got {actual}, expected {expected}")
                checks += 1
                if path:
                    target = Path(temp) / (label + ".bend")
                    target.write_text(translated, encoding="utf-8")
                    check_bend(target, bend=path, wsl=wsl, timeout=timeout, expected=expected)
                    print(f"SMOKE {label} AST=PASS PYTHON={actual} BEND=CHECK_PASS RESULT_MATCH")
                else:
                    print(f"SMOKE {label} AST=PASS PYTHON={actual} BEND=NOT_RUN")
            except (Exception,) as exc:
                bad += 1
                print(f"SMOKE {label} FAIL {type(exc).__name__}: {exc}")
        rejects = [
            ("negative", "def main() -> int:\n    return -1\n"),
            ("untyped", "def f(x):\n    return x\n"),
            ("import_effect", "import random\ndef main() -> int:\n    return 3\n"),
            ("infinite", "def loop(x: int) -> int:\n    return loop(x)\n"),
            ("implicit_branch", "def f(x: int) -> int:\n    if x > 1:\n        return 1\n"),
        ]
        for label, source in rejects:
            try:
                Translator(source, label + ".py").compile()
                bad += 1
                print(f"SMOKE reject_{label} FAIL unexpectedly translated")
            except TranslationError as exc:
                checks += 1
                print(f"SMOKE reject_{label} PASS {exc.message}")
    print(f"SMOKE TOTAL checks={checks} failures={bad} bend={'AVAILABLE' if path else 'UNAVAILABLE'}")
    if not path:
        print("NOTE: no Bend binary: generated Bend has NOT been parsed/typechecked/executed by Bend.")
    return bad == 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("translate", help="Translate typed Python pure functions into Bend 2")
    build.add_argument("input", type=Path)
    build.add_argument("-o", "--output", type=Path)
    build.add_argument("--check-bend", action="store_true", help="Run the installed Bend compiler on generated output")
    build.add_argument("--bend", default="bend", help="Bend binary executable")
    tests = sub.add_parser("smoke", help="Run AST/differential smoke cases and optional Bend checks")
    tests.add_argument("--bend", default=None, help="explicit Bend binary")
    tests.add_argument("--wsl", action="store_true", help="invoke Bend inside WSL on Windows")
    build.add_argument("--wsl", action="store_true", help="invoke Bend inside WSL on Windows")
    args = parser.parse_args(argv)
    if args.command == "smoke":
        return 0 if smoke(args.bend, wsl=args.wsl) else 1
    try:
        source = args.input.read_text(encoding="utf-8")
        result = Translator(source, str(args.input)).compile()
        target = args.output or args.input.with_suffix(".bend")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result, encoding="utf-8")
        print(f"TRANSLATE OK {args.input} -> {target} ({len(result.splitlines())} lines)")
        if args.check_bend:
            check_bend(target, bend=args.bend, wsl=args.wsl, timeout=60)
            print("BEND CHECK PASS (not an equivalence proof)")
        return 0
    except (TranslationError, SyntaxError, OSError, subprocess.TimeoutExpired,
            subprocess.CalledProcessError, AssertionError) as exc:
        print(f"TRANSLATE FAILED {exc}", file=sys.stderr)
        return 2
