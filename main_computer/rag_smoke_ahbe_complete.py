from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime
import heapq
import json
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.rag_retriever import DeterministicRagRetriever, RagRetrieverConfig


AHBE_COMPLETE_REFERENCE_DOC = """~doc id=rag-smoke-ahbe-complete title="Ahbe Complete Seed" v=2
!grammar id=ahbe-complete features=[lex,refs,lists,maps,route]
!lex cd="Canal Dock" va="Velin Archive" mb="Moon Bridge" vt="Vault Atrium" bt="brass token" bs="blue seal" rz="red zone"

#actor id=mira n:"Mira" role:keeper
#item id=orbix n:"Orbix" color:blue owner:@mira unlocker:@mira when:sunset seal:$bs
#place id=cd n:$cd
#place id=va n:$va floor:7 access:$bt
#place id=mb n:$mb
#place id=vt n:$vt chamber:archive
#edge id=e1 from:@cd to:@va cost:3 via:stairs req:[] tags:[safe]
#edge id=e2 from:@va to:@mb cost:2 via:gantry req:[$bt] tags:[safe]
#edge id=e3 from:@mb to:@vt cost:4 via:lens req:[] tags:[safe]
#edge id=d1 from:@cd to:@vt cost:1 via:$rz req:[] tags:[red,forbidden]
#problem id=p1 start:@cd goal:@vt item:@orbix have:[$bt] avoid:[red,forbidden] metric:cost ask:"Decode Ahbe, solve the route, and identify who unlocks Orbix."
#answer id=a1 problem:@p1 route:[@cd,@va,@mb,@vt] cost:9 unlocker:@mira carry:[$bt] seal:$bs
"""


SMOKE_PROMPT = (
    "Using only the Ahbe Complete document, decode the compact grammar, solve the "
    "safe route problem, and identify who unlocks Orbix."
)

SMOKE_QUERIES = [
    "rag_smoke_ahbe_complete Ahbe Complete grammar lex refs lists maps route",
    "Orbix Mira blue seal after sunset unlocker",
    "Canal Dock Velin Archive Moon Bridge Vault Atrium brass token safe route",
    "red zone forbidden avoid compact Ahbe problem",
]

AHBE_COMPLETE_RELATIVE_PATH = "knowledge/rag_smoke_ahbe_complete.ahbe"
SCENARIO = "rag_smoke_ahbe_complete"


@dataclass(frozen=True)
class AhbeRef:
    target: str

    def to_dict(self) -> dict[str, str]:
        return {"ref": self.target}


@dataclass(frozen=True)
class AhbeDirective:
    name: str
    values: dict[str, Any]
    line: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "values": _jsonify(self.values),
            "line": self.line,
        }


@dataclass(frozen=True)
class AhbeRecord:
    kind: str
    slots: dict[str, Any]
    line: int

    @property
    def record_id(self) -> str:
        return str(self.slots.get("id", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "slots": _jsonify(self.slots),
            "line": self.line,
        }


@dataclass(frozen=True)
class AhbeCompleteDocument:
    attrs: dict[str, Any]
    directives: list[AhbeDirective]
    lexicon: dict[str, str]
    records: list[AhbeRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "attrs": _jsonify(self.attrs),
            "directives": [directive.to_dict() for directive in self.directives],
            "lexicon": dict(sorted(self.lexicon.items())),
            "records": [record.to_dict() for record in self.records],
        }

    def by_id(self) -> dict[str, AhbeRecord]:
        return {
            record.record_id: record
            for record in self.records
            if record.record_id
        }

    def records_of(self, kind: str) -> list[AhbeRecord]:
        return [record for record in self.records if record.kind == kind]


@dataclass(frozen=True)
class AhbeCompleteSolution:
    route_ids: list[str]
    route_names: list[str]
    cost: int
    carry: list[str]
    avoid: list[str]
    item: str
    item_owner: str
    unlocker: str
    unlock_when: str
    seal: str
    expected_answer: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AhbeCompleteRagSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    corpus_dir: str
    ahbe_path: str
    prompt: str
    queries: list[str]
    retrieved_paths: list[str]
    chunk_paths: list[str]
    encoded_chars: int
    decoded_chars: int
    compression_ratio: float
    solution: dict[str, Any]
    parsed_document: dict[str, Any]
    retrieval: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class _ValueParser:
    def __init__(self, text: str, lexicon: dict[str, str]) -> None:
        self.text = text
        self.lexicon = lexicon
        self.index = 0

    def parse(self) -> Any:
        value = self._parse_value()
        self._skip_ws()
        if self.index != len(self.text):
            raise ValueError(f"Unexpected trailing Ahbe value syntax: {self.text[self.index:]!r}")
        return value

    def _peek(self) -> str:
        return self.text[self.index] if self.index < len(self.text) else ""

    def _consume(self, expected: str) -> None:
        if self._peek() != expected:
            raise ValueError(f"Expected {expected!r} in Ahbe value: {self.text!r}")
        self.index += 1

    def _skip_ws(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _parse_value(self) -> Any:
        self._skip_ws()
        char = self._peek()
        if not char:
            raise ValueError("Ahbe value is empty")

        if char == '"':
            return self._parse_string()

        if char == "@":
            self.index += 1
            return AhbeRef(self._parse_identifier("reference"))

        if char == "$":
            self.index += 1
            key = self._parse_identifier("lexicon key")
            if key not in self.lexicon:
                raise ValueError(f"Unknown Ahbe lexicon key: ${key}")
            return self.lexicon[key]

        if char == "[":
            return self._parse_list()

        if char == "{":
            return self._parse_map()

        return self._parse_atom()

    def _parse_string(self) -> str:
        self._consume('"')
        result: list[str] = []

        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1

            if char == '"':
                return "".join(result)

            if char == "\\":
                if self.index >= len(self.text):
                    raise ValueError("Unterminated escape in Ahbe string")
                escaped = self.text[self.index]
                self.index += 1
                result.append(
                    {
                        "n": "\n",
                        "r": "\r",
                        "t": "\t",
                        '"': '"',
                        "\\": "\\",
                    }.get(escaped, escaped)
                )
            else:
                result.append(char)

        raise ValueError("Unterminated Ahbe string literal")

    def _parse_identifier(self, label: str) -> str:
        match = _IDENTIFIER_RE.match(self.text, self.index)
        if not match:
            raise ValueError(f"Expected Ahbe {label} at: {self.text[self.index:]!r}")
        self.index = match.end()
        return match.group(0)

    def _parse_list(self) -> list[Any]:
        self._consume("[")
        result: list[Any] = []
        self._skip_ws()

        if self._peek() == "]":
            self.index += 1
            return result

        while True:
            result.append(self._parse_value())
            self._skip_ws()
            char = self._peek()

            if char == ",":
                self.index += 1
                continue

            if char == "]":
                self.index += 1
                return result

            raise ValueError(f"Expected ',' or ']' in Ahbe list: {self.text!r}")

    def _parse_map(self) -> dict[str, Any]:
        self._consume("{")
        result: dict[str, Any] = {}
        self._skip_ws()

        if self._peek() == "}":
            self.index += 1
            return result

        while True:
            self._skip_ws()
            key = self._parse_string() if self._peek() == '"' else self._parse_identifier("map key")
            self._skip_ws()

            if self._peek() not in {":", "="}:
                raise ValueError(f"Expected ':' or '=' after Ahbe map key: {self.text!r}")

            self.index += 1
            result[key] = self._parse_value()
            self._skip_ws()
            char = self._peek()

            if char == ",":
                self.index += 1
                continue

            if char == "}":
                self.index += 1
                return result

            raise ValueError(f"Expected ',' or '}}' in Ahbe map: {self.text!r}")

    def _parse_atom(self) -> Any:
        start = self.index

        while self.index < len(self.text) and self.text[self.index] not in ",]} \t\r\n":
            self.index += 1

        atom = self.text[start:self.index]
        if not atom:
            raise ValueError(f"Expected Ahbe atom at: {self.text[start:]!r}")

        if atom == "true":
            return True
        if atom == "false":
            return False
        if atom == "null":
            return None
        if _NUMBER_RE.fullmatch(atom):
            return float(atom) if "." in atom else int(atom)

        return atom


def _jsonify(value: Any) -> Any:
    if isinstance(value, AhbeRef):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonify(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonify(item) for key, item in value.items()}
    return value


def _parse_value(text: str, lexicon: dict[str, str]) -> Any:
    return _ValueParser(text.strip(), lexicon).parse()


def _read_inline_value(text: str, start: int) -> tuple[str, int]:
    index = start
    depth = 0
    in_string = False
    escaped = False

    while index < len(text):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"Unbalanced Ahbe inline value: {text[start:index + 1]!r}")
        elif char.isspace() and depth == 0:
            break

        index += 1

    return text[start:index], index


def _parse_inline_pairs(text: str, lexicon: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    index = 0

    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1

        if index >= len(text):
            break

        match = _IDENTIFIER_RE.match(text, index)
        if not match:
            raise ValueError(f"Expected Ahbe key near: {text[index:]!r}")

        key = match.group(0)
        index = match.end()

        if index >= len(text) or text[index] not in {":", "="}:
            raise ValueError(f"Expected ':' or '=' after Ahbe key {key!r}")

        index += 1
        raw_value, index = _read_inline_value(text, index)

        if not raw_value:
            raise ValueError(f"Missing value for Ahbe key {key!r}")

        values[key] = _parse_value(raw_value, lexicon)

    return values


def parse_ahbe_complete_document(text: str) -> AhbeCompleteDocument:
    """Parse the fuller Ahbe smoke-test grammar.

    Supported syntax:
      * ~doc key=value...
      * !grammar and other directives using inline key:value or key=value pairs
      * !lex directives that define $short aliases
      * #record inline slots, for example #edge id=e1 from:@a to:@b req:[$token]
      * quoted strings, numbers, booleans, nulls, @references, $aliases, lists, and maps
      * // comments and blank lines
    """

    doc_attrs: dict[str, Any] | None = None
    directives: list[AhbeDirective] = []
    lexicon: dict[str, str] = {}
    records: list[AhbeRecord] = []
    current: AhbeRecord | None = None

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()

        if not line or line.startswith("//"):
            continue

        if line.startswith("~doc"):
            if doc_attrs is not None:
                raise ValueError(f"Line {line_number}: duplicate ~doc header")
            doc_attrs = _parse_inline_pairs(line[len("~doc"):].strip(), lexicon)
            current = None
            continue

        if line.startswith("!"):
            rest = line[1:].strip()
            if not rest:
                raise ValueError(f"Line {line_number}: missing Ahbe directive name")

            name, _, pairs_text = rest.partition(" ")
            values = _parse_inline_pairs(pairs_text, lexicon) if pairs_text.strip() else {}

            if name == "lex":
                for key, value in values.items():
                    if not isinstance(value, str):
                        raise ValueError(f"Line {line_number}: lexicon value for ${key} must be a string")
                    lexicon[key] = value

            directives.append(AhbeDirective(name=name, values=values, line=line_number))
            current = None
            continue

        if line.startswith("#"):
            rest = line[1:].strip()
            if not rest:
                raise ValueError(f"Line {line_number}: missing Ahbe record kind")

            kind, _, pairs_text = rest.partition(" ")
            slots = _parse_inline_pairs(pairs_text, lexicon) if pairs_text.strip() else {}
            current = AhbeRecord(kind=kind, slots=slots, line=line_number)
            records.append(current)
            continue

        if ":" in line and current is not None:
            key, raw_value = line.split(":", 1)
            key = key.strip()

            if not key:
                raise ValueError(f"Line {line_number}: empty Ahbe slot key")

            current.slots[key] = _parse_value(raw_value, lexicon)
            continue

        raise ValueError(f"Line {line_number}: unrecognized Ahbe Complete syntax: {raw_line!r}")

    if doc_attrs is None:
        raise ValueError("Ahbe Complete document is missing a ~doc header")

    return AhbeCompleteDocument(
        attrs=doc_attrs,
        directives=directives,
        lexicon=lexicon,
        records=records,
    )


def _default_run_id() -> str:
    return "rag_ahbe_complete_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _require_record(document: AhbeCompleteDocument, record_id: str) -> AhbeRecord:
    record = document.by_id().get(record_id)
    if record is None:
        raise ValueError(f"Ahbe Complete record id not found: {record_id}")
    return record


def _ref_id(value: Any) -> str:
    if isinstance(value, AhbeRef):
        return value.target
    raise ValueError(f"Expected Ahbe @id reference, got: {value!r}")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Expected Ahbe list, got: {value!r}")
    return [str(item) for item in value]


def _place_name(document: AhbeCompleteDocument, record_id: str) -> str:
    record = _require_record(document, record_id)
    return str(record.slots.get("n", record_id))


def _actor_name(document: AhbeCompleteDocument, record_id: str) -> str:
    record = _require_record(document, record_id)
    return str(record.slots.get("n", record_id))


def _edge_req_satisfied(edge: AhbeRecord, have: set[str]) -> bool:
    requirements = _string_list(edge.slots.get("req", []))
    return set(requirements).issubset(have)


def _edge_is_allowed(edge: AhbeRecord, have: set[str], avoid: set[str]) -> bool:
    tags = set(_string_list(edge.slots.get("tags", [])))
    return not tags.intersection(avoid) and _edge_req_satisfied(edge, have)


def _shortest_safe_route(
    document: AhbeCompleteDocument,
    *,
    start_id: str,
    goal_id: str,
    have: set[str],
    avoid: set[str],
) -> tuple[int, list[str]]:
    adjacency: dict[str, list[tuple[int, str, str]]] = {}

    for edge in document.records_of("edge"):
        if not _edge_is_allowed(edge, have, avoid):
            continue

        source = _ref_id(edge.slots["from"])
        target = _ref_id(edge.slots["to"])
        cost = int(edge.slots["cost"])
        adjacency.setdefault(source, []).append((cost, target, edge.record_id))

    heap: list[tuple[int, str, list[str]]] = [(0, start_id, [start_id])]
    best: dict[str, int] = {start_id: 0}

    while heap:
        cost, node, route = heapq.heappop(heap)

        if node == goal_id:
            return cost, route

        if cost > best.get(node, sys.maxsize):
            continue

        for edge_cost, target, _edge_id in adjacency.get(node, []):
            next_cost = cost + edge_cost

            if next_cost >= best.get(target, sys.maxsize):
                continue

            best[target] = next_cost
            heapq.heappush(heap, (next_cost, target, route + [target]))

    raise ValueError(f"No safe Ahbe route from {start_id!r} to {goal_id!r}")


def solve_ahbe_complete_problem(
    document: AhbeCompleteDocument,
    problem_id: str = "p1",
) -> AhbeCompleteSolution:
    problem = _require_record(document, problem_id)
    item = _require_record(document, _ref_id(problem.slots["item"]))

    have = set(_string_list(problem.slots.get("have", [])))
    avoid = set(_string_list(problem.slots.get("avoid", [])))
    start_id = _ref_id(problem.slots["start"])
    goal_id = _ref_id(problem.slots["goal"])

    cost, route_ids = _shortest_safe_route(
        document,
        start_id=start_id,
        goal_id=goal_id,
        have=have,
        avoid=avoid,
    )

    route_names = [_place_name(document, record_id) for record_id in route_ids]

    owner_id = _ref_id(item.slots["owner"])
    unlocker_id = _ref_id(item.slots["unlocker"])

    item_name = str(item.slots["n"])
    owner_name = _actor_name(document, owner_id)
    unlocker_name = _actor_name(document, unlocker_id)
    unlock_when = str(item.slots["when"])
    seal = str(item.slots["seal"])
    carry = sorted(have)
    avoid_list = sorted(avoid)

    expected_answer = (
        f"Use {' -> '.join(route_names)} (cost {cost}). "
        f"Carry {', '.join(carry)}. "
        f"{unlocker_name} unlocks {item_name} after {unlock_when} with the {seal}."
    )

    return AhbeCompleteSolution(
        route_ids=route_ids,
        route_names=route_names,
        cost=cost,
        carry=carry,
        avoid=avoid_list,
        item=item_name,
        item_owner=owner_name,
        unlocker=unlocker_name,
        unlock_when=unlock_when,
        seal=seal,
        expected_answer=expected_answer,
    )


def validate_ahbe_complete_document(document: AhbeCompleteDocument) -> list[str]:
    failures: list[str] = []
    by_id = document.by_id()

    required_ids = {
        "mira",
        "orbix",
        "cd",
        "va",
        "mb",
        "vt",
        "e1",
        "e2",
        "e3",
        "d1",
        "p1",
        "a1",
    }

    missing = sorted(required_ids.difference(by_id))
    if missing:
        failures.append("Missing required Ahbe Complete records: " + ", ".join(missing))
        return failures

    if not any(directive.name == "grammar" for directive in document.directives):
        failures.append("Missing !grammar directive.")

    if "bt" not in document.lexicon or document.lexicon["bt"] != "brass token":
        failures.append("Lexicon key $bt must decode to 'brass token'.")

    if "vt" not in document.lexicon or document.lexicon["vt"] != "Vault Atrium":
        failures.append("Lexicon key $vt must decode to 'Vault Atrium'.")

    solution = solve_ahbe_complete_problem(document)
    answer = by_id["a1"]

    expected_route = [_ref_id(item) for item in answer.slots["route"]]

    if solution.route_ids != expected_route:
        failures.append(f"Solved route {solution.route_ids!r} did not match answer route {expected_route!r}.")

    if solution.cost != answer.slots["cost"]:
        failures.append(f"Solved cost {solution.cost!r} did not match answer cost {answer.slots['cost']!r}.")

    if solution.unlocker != "Mira":
        failures.append("Solved unlocker must decode to Mira.")

    if solution.expected_answer != (
        "Use Canal Dock -> Velin Archive -> Moon Bridge -> Vault Atrium (cost 9). "
        "Carry brass token. Mira unlocks Orbix after sunset with the blue seal."
    ):
        failures.append("Composed answer does not match the Ahbe Complete reference solution.")

    return failures


def decoded_problem_payload(
    document: AhbeCompleteDocument,
    solution: AhbeCompleteSolution,
) -> dict[str, Any]:
    problem = _require_record(document, "p1")
    item = _require_record(document, _ref_id(problem.slots["item"]))

    return {
        "grammar": document.attrs,
        "lexicon": dict(sorted(document.lexicon.items())),
        "problem": _jsonify(problem.slots),
        "decoded": {
            "start": _place_name(document, _ref_id(problem.slots["start"])),
            "goal": _place_name(document, _ref_id(problem.slots["goal"])),
            "item": str(item.slots["n"]),
            "have": _string_list(problem.slots.get("have", [])),
            "avoid": _string_list(problem.slots.get("avoid", [])),
        },
        "solution": solution.to_dict(),
    }


def write_fixture_corpus(corpus_dir: Path) -> Path:
    ahbe_path = corpus_dir / AHBE_COMPLETE_RELATIVE_PATH
    ahbe_path.parent.mkdir(parents=True, exist_ok=True)
    ahbe_path.write_text(AHBE_COMPLETE_REFERENCE_DOC, encoding="utf-8")

    decoy_path = corpus_dir / "knowledge" / "rag_smoke_ahbe_complete_decoy.txt"
    decoy_path.write_text(
        "Decoy: a one-hop red zone route looks cheaper, but the Ahbe problem forbids red and forbidden tags.\n",
        encoding="utf-8",
    )

    notes_path = corpus_dir / "knowledge" / "rag_smoke_ahbe_complete_notes.md"
    notes_path.write_text(
        "# Ahbe Complete Notes\n\n"
        "The smoke test must decode the .ahbe grammar before solving the route.\n",
        encoding="utf-8",
    )

    return ahbe_path


def run_rag_smoke_ahbe_complete(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    run_id: str | None = None,
    strict: bool = False,
    verbose: bool = True,
) -> AhbeCompleteRagSmokeReport:
    run_id = run_id or _default_run_id()
    output_dir = (output_root or (repo_dir / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    corpus_dir = output_dir / "corpus"
    ahbe_path = write_fixture_corpus(corpus_dir)

    warnings: list[str] = []
    failures: list[str] = []

    document = parse_ahbe_complete_document(AHBE_COMPLETE_REFERENCE_DOC)
    failures.extend(validate_ahbe_complete_document(document))

    solution = solve_ahbe_complete_problem(document)
    decoded_payload = decoded_problem_payload(document, solution)

    encoded_chars = len(AHBE_COMPLETE_REFERENCE_DOC)
    decoded_text = _json_dumps(decoded_payload)
    decoded_chars = len(decoded_text)
    compression_ratio = round(encoded_chars / decoded_chars, 4) if decoded_chars else 1.0

    if encoded_chars >= decoded_chars:
        failures.append(
            f"Ahbe Complete fixture did not compress the decoded payload: {encoded_chars}/{decoded_chars} chars"
        )

    retriever = DeterministicRagRetriever(
        RagRetrieverConfig(
            repo_dir=corpus_dir,
            max_context_chars=16_000,
            max_candidates=8,
            max_chunks=5,
        )
    )

    retrieval = retriever.retrieve(SMOKE_QUERIES, extra_paths=[AHBE_COMPLETE_RELATIVE_PATH])
    retrieved_paths = [candidate.path for candidate in retrieval.candidates]
    chunk_paths = [chunk.path for chunk in retrieval.chunks]
    context_text = "\n".join(chunk.content for chunk in retrieval.chunks)

    if AHBE_COMPLETE_RELATIVE_PATH not in retrieved_paths:
        failures.append(
            f"Ahbe Complete seed file was not selected as a retrieval candidate: {AHBE_COMPLETE_RELATIVE_PATH}"
        )

    if AHBE_COMPLETE_RELATIVE_PATH not in chunk_paths:
        failures.append(
            f"Ahbe Complete seed file did not produce a context chunk: {AHBE_COMPLETE_RELATIVE_PATH}"
        )

    required_needles = [
        "!grammar",
        "!lex",
        "#problem",
        "Orbix",
        "brass token",
        "Vault Atrium",
        "red,forbidden",
        "blue seal",
    ]

    missing_needles = [needle for needle in required_needles if needle not in context_text]
    if missing_needles:
        failures.append(
            "Retrieved context is missing expected Ahbe Complete syntax/facts: "
            + ", ".join(missing_needles)
        )

    if retrieved_paths and retrieved_paths[0] != AHBE_COMPLETE_RELATIVE_PATH:
        warnings.append(
            f"Ahbe Complete seed was retrieved but not ranked first; first candidate was {retrieved_paths[0]!r}."
        )

    if retrieval.used_chars > retrieval.context_budget_chars:
        failures.append(
            f"Retriever exceeded context budget: {retrieval.used_chars}/{retrieval.context_budget_chars} chars"
        )

    if strict and warnings:
        failures.extend(warnings)

    _write_json(output_dir / "parsed_ahbe_complete.json", document.to_dict())
    _write_json(output_dir / "decoded_problem.json", decoded_payload)
    _write_json(output_dir / "solution.json", solution.to_dict())
    _write_json(output_dir / "retrieval.json", retrieval.as_dict())
    (output_dir / "expected_answer.txt").write_text(solution.expected_answer + "\n", encoding="utf-8")

    report_path = output_dir / "ahbe_complete_rag_smoke_report.json"

    report = AhbeCompleteRagSmokeReport(
        ok=not failures,
        run_id=run_id,
        scenario=SCENARIO,
        output_dir=str(output_dir),
        report_path=str(report_path),
        corpus_dir=str(corpus_dir),
        ahbe_path=str(ahbe_path),
        prompt=SMOKE_PROMPT,
        queries=list(SMOKE_QUERIES),
        retrieved_paths=retrieved_paths,
        chunk_paths=chunk_paths,
        encoded_chars=encoded_chars,
        decoded_chars=decoded_chars,
        compression_ratio=compression_ratio,
        solution=solution.to_dict(),
        parsed_document=document.to_dict(),
        retrieval=retrieval.as_dict(),
        warnings=warnings,
        failures=failures,
        strict=strict,
    )

    _write_json(report_path, report.to_dict())

    if verbose:
        print("[rag-smoke-ahbe-complete] validation report:")
        print(
            _json_dumps(
                {
                    "ok": report.ok,
                    "run_id": report.run_id,
                    "scenario": report.scenario,
                    "retrieved_paths": report.retrieved_paths,
                    "chunk_paths": report.chunk_paths,
                    "encoded_chars": report.encoded_chars,
                    "decoded_chars": report.decoded_chars,
                    "compression_ratio": report.compression_ratio,
                    "warnings": report.warnings,
                    "failures": report.failures,
                    "expected_answer": solution.expected_answer,
                    "report_path": report.report_path,
                }
            )
        )

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the complete Ahbe grammar RAG smoke test.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for diagnostics output. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_runs.",
    )
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose smoke diagnostics.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    report = run_rag_smoke_ahbe_complete(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        run_id=args.run_id,
        strict=args.strict,
        verbose=not args.quiet,
    )

    solution = report.solution

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Corpus: {report.corpus_dir}")
    print(f"Ahbe Complete seed: {report.ahbe_path}")
    print(f"Report: {report.report_path}")
    print(f"Retrieved paths: {report.retrieved_paths}")
    print(f"Encoded chars: {report.encoded_chars}")
    print(f"Decoded chars: {report.decoded_chars}")
    print(f"Compression ratio: {report.compression_ratio}")
    print(f"Expected answer: {solution['expected_answer']}")
    print(f"Status: {'passed' if report.ok else 'failed'}")

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")

    if report.failures:
        print("Failures:")
        for failure in report.failures:
            print(f"  - {failure}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())