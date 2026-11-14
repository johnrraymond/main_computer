"""Deterministic Hawk-Dove continuation head (stdlib only).

This is an executable teacher/oracle, NOT a claim to have trained a neural head.
Four immutable payoff bindings are loaded once per head. Only x/y vary.

Grammar:
    Add(<payoff-continuation>)
    PAYOFF(x|y, <continuation>)
    With(ARGMAX(Add(<payoff-continuation>)))
    END()

Outer Add is delimited by With; inner Add is delimited by END.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any


PAYOFF_ROLES = (
    "HAWK_HAWK_PAYOFF", "HAWK_DOVE_PAYOFF",
    "DOVE_HAWK_PAYOFF", "DOVE_DOVE_PAYOFF",
)
EVENT_BY_PAYOFF = dict(zip(PAYOFF_ROLES, (
    "opponent_hawk", "opponent_dove", "opponent_hawk", "opponent_dove"
)))
HAWK_ROLES = frozenset(PAYOFF_ROLES[:2])
DOVE_ROLES = frozenset(PAYOFF_ROLES[2:])
MACRO_ROLES = frozenset(("HAWK_EXPECTED_PAYOFF", "DOVE_EXPECTED_PAYOFF"))

# No numeric payoff values here; each semantic constant occurs ONCE in the tree.
CANONICAL_EXPRESSION = """Add(
    HAWK_HAWK_PAYOFF(x,
        HAWK_DOVE_PAYOFF(y,
            With(
                ARGMAX(
                    Add(
                        DOVE_HAWK_PAYOFF(x,
                            DOVE_DOVE_PAYOFF(y,
                                END()
                            )
                        )
                    )
                )
            )
        )
    )
)"""


@dataclass(frozen=True)
class Node:
    op: str
    args: tuple["Node | str", ...]


@dataclass(frozen=True)
class PayoffBindings:
    hh: Decimal
    hd: Decimal
    dh: Decimal
    dd: Decimal
    binding_version: str
    demo_only: bool

    @classmethod
    def load(cls, path: str | Path) -> "PayoffBindings":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(payload) != {"binding_version", "demo_only", "payoffs"}:
            raise ValueError("Manifest must contain exactly binding_version, demo_only and payoffs")
        table = payload["payoffs"]
        if set(table) != set(PAYOFF_ROLES):
            raise ValueError("Manifest must specify exactly the four Hawk-Dove payoff tags")
        if not isinstance(payload["binding_version"], str) or not payload["binding_version"]:
            raise ValueError("Nonempty binding_version required")
        if not isinstance(payload["demo_only"], bool):
            raise ValueError("demo_only must be boolean")
        if any(not isinstance(table[k], str) for k in PAYOFF_ROLES):
            raise ValueError("Payoff bindings must be decimal strings to avoid float rounding")
        try:
            vals = [Decimal(table[k]) for k in PAYOFF_ROLES]
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("Payoff bindings must be decimal strings") from exc
        if not all(v.is_finite() for v in vals):
            raise ValueError("Payoff bindings must be finite")
        return cls(*vals, payload["binding_version"], payload["demo_only"])

    def as_dict(self) -> dict[str, Decimal]:
        return dict(zip(PAYOFF_ROLES, (self.hh, self.hd, self.dh, self.dd)))

    def sha256(self) -> str:
        canonical = {
            "version": self.binding_version,
            "payoffs": {tag: str(value) for tag, value in self.as_dict().items()},
        }
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class BeliefDefinitions:
    """Immutable semantic interpretation for reusable x/y references.

    This is NOT a declaration inside the model-facing Lisp program and it
    contains no learned payoff values. Events determine which payoff-column
    uses each variable. Changing the meaning is an explicit new definition
    version, not a silent substitution.
    """
    definition_version: str
    mode: str
    x_event: str
    x_meaning: str
    y_event: str
    y_meaning: str

    @classmethod
    def default(cls, mode: str = "weights") -> "BeliefDefinitions":
        return cls(
            "hawk-dove-beliefs-default-v1", mode,
            "opponent_hawk", "belief weight that the opponent plays Hawk",
            "opponent_dove", "belief weight that the opponent plays Dove",
        )

    @classmethod
    def load(cls, path: str | Path) -> "BeliefDefinitions":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if set(payload) != {"definition_version", "mode", "variables"}:
            raise ValueError("Belief definitions require definition_version, mode and variables")
        variables = payload["variables"]
        if not isinstance(variables, dict) or set(variables) != {"x", "y"}:
            raise ValueError("Exactly x and y must be defined")
        for name in ("x", "y"):
            if not isinstance(variables[name], dict) or set(variables[name]) != {"event", "meaning"}:
                raise ValueError(f"{name} must define event and meaning")
        spec = cls(
            payload["definition_version"], payload["mode"],
            variables["x"]["event"], variables["x"]["meaning"],
            variables["y"]["event"], variables["y"]["meaning"],
        )
        spec.validate()
        return spec

    def validate(self) -> None:
        if not isinstance(self.definition_version, str) or not self.definition_version.strip():
            raise ValueError("A nonempty belief definition version is required")
        if self.mode not in {"weights", "probabilities"}:
            raise ValueError("Belief mode must be weights or probabilities")
        if {self.x_event, self.y_event} != {"opponent_hawk", "opponent_dove"}:
            raise ValueError("Beliefs must map bijectively to opponent_hawk and opponent_dove")
        for name, meaning in (("x", self.x_meaning), ("y", self.y_meaning)):
            if not isinstance(meaning, str) or not meaning.strip() or len(meaning) > 240 or "\n" in meaning:
                raise ValueError(f"{name} needs a nonempty, single-line meaning (max 240 characters)")

    def variable_for_event(self, event: str) -> str:
        self.validate()
        return "x" if self.x_event == event else "y"

    def expected_belief(self, payoff: str) -> str:
        return self.variable_for_event(EVENT_BY_PAYOFF[payoff])

    def value_for_event(self, event: str, x: Decimal, y: Decimal) -> Decimal:
        return x if self.variable_for_event(event) == "x" else y

    def validate_inputs(self, x: Decimal, y: Decimal, force_probabilities: bool = False) -> None:
        self.validate()
        if x + y == 0:
            raise ValueError("At least one of x and y must be positive")
        if (self.mode == "probabilities" or force_probabilities) and x + y != 1:
            raise ValueError("Probability beliefs must satisfy x + y = 1")

    def as_dict(self) -> dict[str, Any]:
        return {"definition_version": self.definition_version, "mode": self.mode,
                "variables": {"x": {"event": self.x_event, "meaning": self.x_meaning},
                              "y": {"event": self.y_event, "meaning": self.y_meaning}}}

    def sha256(self) -> str:
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def context(self, x: Decimal, y: Decimal) -> str:
        constraint = "probabilities; x + y = 1" if self.mode == "probabilities" else "nonnegative belief weights"
        return ("You realize you are playing Hawk–Dove :: "
                + f"x = {fmt(x)} [{self.x_meaning}] :: "
                + f"y = {fmt(y)} [{self.y_meaning}] :: {constraint} ::")


def variable_tree(root: Node, beliefs: BeliefDefinitions) -> Node:
    """Use the definitions to put each variable in its correct payoff column.

    No payoff symbol, occurrence or operator is introduced or duplicated.
    """
    beliefs.validate()
    if root.op in PAYOFF_ROLES:
        _, continuation = root.args
        assert isinstance(continuation, Node)
        return Node(root.op, (beliefs.expected_belief(root.op), variable_tree(continuation, beliefs)))
    if root.op in MACRO_ROLES:
        _, _, continuation = root.args
        assert isinstance(continuation, Node)
        return Node(root.op, (beliefs.variable_for_event("opponent_hawk"),
                              beliefs.variable_for_event("opponent_dove"),
                              variable_tree(continuation, beliefs)))
    if root.op == "END":
        return root
    (continuation,) = root.args
    assert isinstance(continuation, Node)
    return Node(root.op, (variable_tree(continuation, beliefs),))


def game_tree(beliefs: BeliefDefinitions | None = None) -> Node:
    return variable_tree(parse(CANONICAL_EXPRESSION), beliefs or BeliefDefinitions.default())


_TOKEN = re.compile(r"\s*([A-Za-z_][A-Za-z_0-9]*|[(),])")


class Parser:
    def __init__(self, expression: str) -> None:
        tokens: list[str] = []
        pos = 0
        while pos < len(expression):
            if not expression[pos:].strip():
                break
            match = _TOKEN.match(expression, pos)
            if not match:
                raise ValueError(f"Invalid syntax at character {pos}: {expression[pos:pos+20]!r}")
            tokens.append(match.group(1))
            pos = match.end()
        self.tokens = tokens
        self.pos = 0

    def take(self) -> str:
        if self.pos >= len(self.tokens):
            raise ValueError("Unexpected end of expression")
        val = self.tokens[self.pos]
        self.pos += 1
        return val

    def expect(self, value: str) -> None:
        got = self.take()
        if got != value:
            raise ValueError(f"Expected {value!r}, got {got!r}")

    def parse_node(self) -> Node:
        op = self.take()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", op):
            raise ValueError(f"Expected operator, got {op!r}")
        self.expect("(")
        args: list[Node | str] = []
        if self.pos < len(self.tokens) and self.tokens[self.pos] != ")":
            while True:
                if (op in PAYOFF_ROLES and not args) or (op in MACRO_ROLES and len(args) < 2):
                    arg = self.take()
                    if arg not in {"x", "y"}:
                        raise ValueError("Payoff belief must be x or y")
                    args.append(arg)
                else:
                    args.append(self.parse_node())
                if self.pos < len(self.tokens) and self.tokens[self.pos] == ",":
                    self.pos += 1
                else:
                    break
        self.expect(")")
        expected_arity = 3 if op in MACRO_ROLES else 2 if op in PAYOFF_ROLES else 0 if op == "END" else 1 if op in {"Add", "With", "ARGMAX"} else None
        if expected_arity is None or len(args) != expected_arity:
            raise ValueError(f"Unknown operation or wrong arity: {op}({len(args)} arguments)")
        return Node(op, tuple(args))

    def parse(self) -> Node:
        node = self.parse_node()
        if self.pos != len(self.tokens):
            raise ValueError("Unexpected trailing tokens")
        return node


def parse(expression: str) -> Node:
    return Parser(expression).parse()


def render(node: Node, indent: int = 0) -> str:
    if node.op == "END":
        return "END()"
    if node.op in PAYOFF_ROLES:
        belief, continuation = node.args
        assert isinstance(continuation, Node)
        return f"{node.op}({belief},\n{' ' * (indent + 4)}{render(continuation, indent+4)}\n{' ' * indent})"
    if node.op in MACRO_ROLES:
        first, second, continuation = node.args
        assert isinstance(continuation, Node)
        return f"{node.op}({first}, {second},\n{' ' * (indent + 4)}{render(continuation, indent+4)}\n{' ' * indent})"
    child = node.args[0]
    assert isinstance(child, Node)
    return f"{node.op}(\n{' ' * (indent+4)}{render(child, indent+4)}\n{' ' * indent})"


def chain(node: Node, terminal: str, beliefs: BeliefDefinitions | None = None) -> tuple[list[tuple[str, str]], Node]:
    """Extract one Add's contribution chain, enforcing terminal and no cross-scope leaks."""
    beliefs = beliefs or BeliefDefinitions.default()
    items: list[tuple[str, str]] = []
    while node.op in PAYOFF_ROLES:
        belief, continuation = node.args
        if belief != beliefs.expected_belief(node.op):
            raise ValueError(f"Incorrect belief for {node.op}: {belief}")
        assert isinstance(continuation, Node)
        items.append((node.op, belief))
        node = continuation
    if node.op != terminal:
        raise ValueError(f"Add scope must terminate at {terminal}, got {node.op}")
    return items, node


def structure(root: Node, beliefs: BeliefDefinitions | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    beliefs = beliefs or BeliefDefinitions.default()
    if root.op != "Add":
        raise ValueError("The complete game begins with Add")
    child = root.args[0]
    assert isinstance(child, Node)
    hawk, with_node = chain(child, "With", beliefs)
    cmp_node = with_node.args[0]
    if not isinstance(cmp_node, Node) or cmp_node.op != "ARGMAX":
        raise ValueError("With must preserve the Hawk total for ARGMAX")
    inner = cmp_node.args[0]
    if not isinstance(inner, Node) or inner.op != "Add":
        raise ValueError("ARGMAX must compare with a second Add scope")
    inner_child = inner.args[0]
    assert isinstance(inner_child, Node)
    dove, end_node = chain(inner_child, "END", beliefs)
    if end_node.args:
        raise ValueError("END has no arguments")
    if len(hawk) != 2 or {t for t, _ in hawk} != HAWK_ROLES:
        raise ValueError("First Add must contain the two Hawk payoffs exactly once")
    if len(dove) != 2 or {t for t, _ in dove} != DOVE_ROLES:
        raise ValueError("Second Add must contain the two Dove payoffs exactly once")
    return hawk, dove


def _make_chain(items: list[tuple[str, str]], terminator: Node) -> Node:
    result = terminator
    for tag, variable in reversed(items):
        result = Node(tag, (variable, result))
    return result


def collapse_rows(root: Node, beliefs: BeliefDefinitions | None = None) -> Node:
    """Certified game-specific contraction of TWO independently computed row sums.

    Does not rewrite payoff numerics; macro expansion recovers the precise tree.
    """
    beliefs = beliefs or BeliefDefinitions.default()
    hawk, dove = structure(root, beliefs)
    outer = root.args[0]
    assert isinstance(outer, Node)
    _, with_node = chain(outer, "With", beliefs)
    cmp_node = with_node.args[0]
    assert isinstance(cmp_node, Node)
    inner = cmp_node.args[0]
    assert isinstance(inner, Node)
    end_child = inner.args[0]
    assert isinstance(end_child, Node)
    _, end_node = chain(end_child, "END", beliefs)
    # Preserve the original row ordering by expanding these exact saved terms;
    # algebraic row sums are invariant to their within-row permutation.
    if {tag for tag, _ in hawk} != HAWK_ROLES or {tag for tag, _ in dove} != DOVE_ROLES:
        raise ValueError("Cannot collapse an invalid payoff grouping")
    var_h = beliefs.variable_for_event("opponent_hawk")
    var_d = beliefs.variable_for_event("opponent_dove")
    macro_dove = Node("DOVE_EXPECTED_PAYOFF", (var_h, var_d, end_node))
    macro_hawk = Node("HAWK_EXPECTED_PAYOFF", (
        var_h, var_d, Node("With", (Node("ARGMAX", (Node("Add", (macro_dove,)),)),))
    ))
    return Node("Add", (macro_hawk,))


def expand_macros(node: Node, beliefs: BeliefDefinitions | None = None) -> Node:
    """Unfold game-specific row macros to the four learned payoff occurrences."""
    beliefs = beliefs or BeliefDefinitions.default()
    if node.op in MACRO_ROLES:
        var_h, var_d, continuation = node.args
        if (var_h != beliefs.variable_for_event("opponent_hawk")
                or var_d != beliefs.variable_for_event("opponent_dove")
                or not isinstance(continuation, Node)):
            raise ValueError("Macro operands must match the defined opponent beliefs")
        row = PAYOFF_ROLES[:2] if node.op == "HAWK_EXPECTED_PAYOFF" else PAYOFF_ROLES[2:]
        items = [(tag, beliefs.expected_belief(tag)) for tag in row]
        return _make_chain(items, expand_macros(continuation, beliefs))
    if node.op in PAYOFF_ROLES:
        belief, continuation = node.args
        assert isinstance(continuation, Node)
        return Node(node.op, (belief, expand_macros(continuation, beliefs)))
    if node.op == "END":
        return node
    child = node.args[0]
    assert isinstance(child, Node)
    return Node(node.op, (expand_macros(child, beliefs),))


def variant(hawk_reverse: bool = False, dove_reverse: bool = False, beliefs: BeliefDefinitions | None = None) -> Node:
    """Four certified structural arrangements; each payoff occurs exactly once."""
    beliefs = beliefs or BeliefDefinitions.default()
    hawk = [(tag, beliefs.expected_belief(tag)) for tag in PAYOFF_ROLES[:2]]
    dove = [(tag, beliefs.expected_belief(tag)) for tag in PAYOFF_ROLES[2:]]
    if hawk_reverse:
        hawk.reverse()
    if dove_reverse:
        dove.reverse()
    dove_sum = Node("Add", (_make_chain(dove, Node("END", ())),))
    saved = Node("With", (Node("ARGMAX", (dove_sum,)),))
    result = Node("Add", (_make_chain(hawk, saved),))
    structure(result, beliefs)
    return result


def decimal_input(value: str | int | Decimal, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name} must be a finite nonnegative weight")
    return result


def fmt(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _sum_terms(items: list[tuple[str, str]], x: Decimal, y: Decimal, bindings: PayoffBindings) -> tuple[Decimal, list[dict[str, str]]]:
    weights = {"x": x, "y": y}
    table = bindings.as_dict()
    total = Decimal("0")
    trace = []
    for tag, belief in items:
        term = weights[belief] * table[tag]
        total += term
        trace.append({"payoff": tag, "belief": belief, "belief_value": fmt(weights[belief]), "contribution": fmt(term), "running_sum": fmt(total)})
    return total, trace


def evaluate(root: Node, x: str | int | Decimal, y: str | int | Decimal, bindings: PayoffBindings, *, probability_mode: bool = False, expected_binding_hash: str | None = None, belief_definitions: BeliefDefinitions | None = None, expected_belief_definition_hash: str | None = None) -> dict[str, Any]:
    """Execute the continuation grammar. A fixed binding hash can lock a learned model."""
    belief_definitions = belief_definitions or BeliefDefinitions.default()
    hawk_items, dove_items = structure(expand_macros(root, belief_definitions), belief_definitions)
    x_d, y_d = decimal_input(x, "x"), decimal_input(y, "y")
    belief_definitions.validate_inputs(x_d, y_d, probability_mode)
    if (expected_belief_definition_hash is not None
            and belief_definitions.sha256() != expected_belief_definition_hash):
        raise ValueError("Belief meanings changed: explicit new definition/learning required")
    identity = bindings.sha256()
    if expected_binding_hash is not None and identity != expected_binding_hash:
        raise ValueError("Payoff binding version/hash changed: relearning required")
    hawk, hawk_trace = _sum_terms(hawk_items, x_d, y_d, bindings)
    dove, dove_trace = _sum_terms(dove_items, x_d, y_d, bindings)
    winner = "HAWK" if hawk > dove else "DOVE" if dove > hawk else "TIE"
    return {
        "beliefs": {"x": fmt(x_d), "y": fmt(y_d)},
        "belief_definitions": belief_definitions.as_dict(),
        "belief_definition_sha256": belief_definitions.sha256(),
        "hawk_expected": fmt(hawk),
        "dove_expected": fmt(dove),
        "difference_hawk_minus_dove": fmt(hawk-dove),
        "argmax": winner,
        "hawk_add_until_with": hawk_trace,
        "dove_add_until_end": dove_trace,
        "binding_sha256": identity,
        "binding_version": bindings.binding_version,
        "demo_only_payoffs": bindings.demo_only,
    }


def direct_oracle(x: Decimal, y: Decimal, b: PayoffBindings, belief_definitions: BeliefDefinitions | None = None) -> tuple[Decimal, Decimal]:
    """Independent matrix-vector reference, not an execution of the AST."""
    beliefs = belief_definitions or BeliefDefinitions.default()
    h = beliefs.value_for_event("opponent_hawk", x, y)
    d = beliefs.value_for_event("opponent_dove", x, y)
    return (h * b.hh + d * b.hd, h * b.dh + d * b.dd)


def symbolic_signature(root: Node, belief_definitions: BeliefDefinitions | None = None) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Exact coefficient identity for all x/y, independent of sample points."""
    beliefs = belief_definitions or BeliefDefinitions.default()
    hawk, dove = structure(expand_macros(root, beliefs), beliefs)
    return tuple(sorted(hawk)), tuple(sorted(dove))


def certify(root: Node, b: PayoffBindings, belief_definitions: BeliefDefinitions | None = None) -> dict[str, Any]:
    beliefs = belief_definitions or BeliefDefinitions.default()
    signature = symbolic_signature(root, beliefs)
    if signature != symbolic_signature(game_tree(beliefs), beliefs):
        raise ValueError("Not symbolically equivalent to canonical Hawk-Dove game")
    sample_count = 0
    x_values = ("0", "0.2", "0.35", "0.6", "0.8", "1", "1.75")
    y_values = ("0", "0.2", "0.4", "0.65", "0.8", "1", "2.25")
    pairs = ([(x, str(Decimal(1) - Decimal(x))) for x in x_values if Decimal(x) <= 1]
             if beliefs.mode == "probabilities"
             else [(x, y) for x in x_values for y in y_values if (x, y) != ("0", "0")])
    for x, y in pairs:
        result = evaluate(root, x, y, b, belief_definitions=beliefs)
        h, d = direct_oracle(Decimal(x), Decimal(y), b, beliefs)
        assert Decimal(result["hawk_expected"]) == h
        assert Decimal(result["dove_expected"]) == d
        sample_count += 1
    return {"structural_proof": "four exact symbolic coefficients in matching rows", "numerical_probes": sample_count, "passed": True}


def evaluate_fragment(node: Node, x: str | int | Decimal, y: str | int | Decimal, b: PayoffBindings, belief_definitions: BeliefDefinitions | None = None) -> Decimal:
    """Execute an isolated, smaller learnable unit with END as its terminator."""
    item = node.args[0] if node.op == "Add" else node
    if not isinstance(item, Node):
        raise ValueError("Malformed fragment")
    if node.op != "Add" and node.op not in PAYOFF_ROLES:
        raise ValueError("A fragment is a payoff chain, optionally enclosed by Add")
    terms, terminal = chain(item, "END", belief_definitions)
    if len(terms) == 0 or terminal.args:
        raise ValueError("A fragment needs one or more payoff contributions")
    xx, yy = decimal_input(x, "x"), decimal_input(y, "y")
    value, _ = _sum_terms(terms, xx, yy, b)
    return value


def fragments(root: Node, x: Decimal, y: Decimal, b: PayoffBindings, belief_definitions: BeliefDefinitions | None = None) -> list[dict[str, Any]]:
    """Generate executable, supervised units in binding -> operation -> game order.

    Numerical target fields are TEACHER ONLY. Model-facing text exposes neither
    numerical payoff constants nor a BIND declaration.
    """
    belief_definitions = belief_definitions or BeliefDefinitions.default()
    belief_definitions.validate_inputs(x, y)
    hawk, dove = structure(root, belief_definitions)
    all_units: list[dict[str, Any]] = []

    def add_unit(stage: str, group: str, unit: Node, xx: Decimal, yy: Decimal) -> None:
        value = evaluate_fragment(unit, xx, yy, b, belief_definitions)
        all_units.append({
            "stage": stage,
            "group": group,
            "beliefs": {"x": fmt(xx), "y": fmt(yy)},
            "unit": render(unit),
            "belief_definition_sha256": belief_definitions.sha256(),
            "belief_definitions": belief_definitions.as_dict(),
            "model_prompt": belief_definitions.context(xx, yy) + "\n" + render(unit),
            "target": fmt(value),
        })

    # One unit-coefficient exposure for every fixed binding. Each example still
    # provides BOTH injection slots; the unused belief is explicitly zero.
    for group, items in (("HAWK", hawk), ("DOVE", dove)):
        for tag, belief in items:
            xx, yy = (Decimal(1), Decimal(0)) if belief == "x" else (Decimal(0), Decimal(1))
            add_unit("binding", group, Node(tag, (belief, Node("END", ()))), xx, yy)

    for group, items in (("HAWK", hawk), ("DOVE", dove)):
        for tag, belief in items:
            add_unit("weighted_payoff", group, Node(tag, (belief, Node("END", ()))), x, y)
        sum_node = Node("Add", (_make_chain(items, Node("END", ())),))
        add_unit("add_scope", group, sum_node, x, y)

    whole = evaluate(root, x, y, b, belief_definitions=belief_definitions)
    all_units.append({
        "stage": "argmax", "group": "BOTH", "beliefs": {"x": fmt(x), "y": fmt(y)},
        "unit": render(root), "belief_definition_sha256": belief_definitions.sha256(),
        "belief_definitions": belief_definitions.as_dict(),
        "model_prompt": prompt(root, fmt(x), fmt(y), "full", belief_definitions),
        "hawk_target": whole["hawk_expected"], "dove_target": whole["dove_expected"],
        "winner_diagnostic": whole["argmax"],
    })
    all_units.append({
        "stage": "game_semantics", "group": "BOTH", "beliefs": {"x": fmt(x), "y": fmt(y)},
        "unit": "COLLAPSED_GAME", "belief_definition_sha256": belief_definitions.sha256(),
        "belief_definitions": belief_definitions.as_dict(),
        "model_prompt": prompt(root, fmt(x), fmt(y), "game", belief_definitions),
        "hawk_target": whole["hawk_expected"], "dove_target": whole["dove_expected"],
        "winner_diagnostic": whole["argmax"],
    })
    return all_units


def prompt(root: Node, x: str, y: str, level: str, belief_definitions: BeliefDefinitions | None = None) -> str:
    """Presentation-only collapse. Oracle always executes the retained source AST.

    The 'unitary' form intentionally omits x and y. It cannot determine numeric
    outcomes unless belief inputs are supplied through a separate context.
    """
    belief_definitions = belief_definitions or BeliefDefinitions.default()
    x_d, y_d = decimal_input(x, "x"), decimal_input(y, "y")
    belief_definitions.validate_inputs(x_d, y_d)
    context = belief_definitions.context(x_d, y_d)
    if level == "full":
        return context + "\n" + render(root)
    if level == "rows":
        return context + "\n" + render(collapse_rows(root, belief_definitions))
    if level == "game":
        return context + "\nWhich action has the larger expected payoff?"
    if level == "unitary":
        return "You realize you are playing Hawk–Dove. Which action has the larger expected payoff?"
    raise ValueError("Unknown rendering level")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="Locked four-payoff manifest; sample included")
    ap.add_argument("--x", default="0.2")
    ap.add_argument("--y", default="0.8")
    ap.add_argument("--probabilities", action="store_true", help="Legacy shorthand for probability definitions when no --belief-definitions is supplied")
    ap.add_argument("--belief-definitions", type=Path, help="Explicit semantic meanings, column events and probability/weight domain for x and y")
    ap.add_argument("--expected-binding-hash", help="Reject mutated payoff bindings")
    ap.add_argument("--expected-belief-definition-hash", help="Reject a changed x/y semantic contract")
    ap.add_argument("--show-payoffs", action="store_true", help="Diagnostic ONLY; never include in a model-facing prompt")
    ap.add_argument("--level", choices=("full", "rows", "game", "unitary"), default="full")
    ap.add_argument("--write-training", type=Path, help="Write generated subunit supervision as JSONL")
    ap.add_argument("--write-trace", type=Path, help="Write simulation trace as JSON")
    args = ap.parse_args()
    bindings = PayoffBindings.load(args.manifest)
    if args.belief_definitions and args.probabilities:
        ap.error("Choose --belief-definitions or --probabilities; the file already sets the belief domain")
    beliefs = (BeliefDefinitions.load(args.belief_definitions) if args.belief_definitions
               else BeliefDefinitions.default("probabilities" if args.probabilities else "weights"))
    root = game_tree(beliefs)
    cert = certify(root, bindings, beliefs)
    results = evaluate(root, args.x, args.y, bindings, belief_definitions=beliefs,
                       expected_binding_hash=args.expected_binding_hash,
                       expected_belief_definition_hash=args.expected_belief_definition_hash)
    x, y = decimal_input(args.x, "x"), decimal_input(args.y, "y")
    cases = fragments(root, x, y, bindings, beliefs)
    print("=== DETERMINISTIC HAWK–DOVE CONTINUATION ===")
    print(f"Binding version: {bindings.binding_version} | SHA256: {bindings.sha256()}")
    print(f"Belief definitions: {beliefs.definition_version} | mode={beliefs.mode} | SHA256: {beliefs.sha256()}")
    print(f"Payoffs: {'DEMONSTRATION FIXTURE (not actual learned values)' if bindings.demo_only else 'external user-supplied fixed bindings'}")
    if args.show_payoffs:
        print("Teacher-only values:", {k: fmt(v) for k, v in bindings.as_dict().items()})
    print("\nModel-facing prompt:\n" + prompt(root, args.x, args.y, args.level, beliefs))
    print("\nIndividual contributions:")
    for group, key in (("Hawk", "hawk_add_until_with"), ("Dove", "dove_add_until_end")):
        print(group)
        for t in results[key]:
            print(f"  {t['payoff']}({t['belief']}) = {t['contribution']}   [accumulator {t['running_sum']}]")
    print(f"\nOuter Add, stopped at With: {results['hawk_expected']}")
    print(f"Inner Add, stopped at END: {results['dove_expected']}")
    print(f"ARGMAX: {results['argmax']}; HAWK minus DOVE = {results['difference_hawk_minus_dove']}")
    print(f"Symbolically equivalent swap variants: 4; independently checked oracle cases per variant: {cert['numerical_probes']}")
    if args.write_training:
        args.write_training.parent.mkdir(parents=True, exist_ok=True)
        tree_sha = hashlib.sha256(render(root).encode("utf-8")).hexdigest()
        records = [{"binding_sha256": bindings.sha256(), "belief_definition_sha256": beliefs.sha256(), "source_tree_sha256": tree_sha, **unit} for unit in cases]
        args.write_training.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"Training units written: {args.write_training} ({len(records)} units)")
    if args.write_trace:
        args.write_trace.parent.mkdir(parents=True, exist_ok=True)
        args.write_trace.write_text(json.dumps({"source_expression": render(root), "source_tree_sha256": hashlib.sha256(render(root).encode("utf-8")).hexdigest(), "belief_definitions": beliefs.as_dict(), "result": results, "certificate": cert}, indent=2), encoding="utf-8")
        print(f"Execution trace written: {args.write_trace}")


if __name__ == "__main__":
    main()
