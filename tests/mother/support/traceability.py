from __future__ import annotations

import ast
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

DOC_NAMES = ("mother.md", "mother-o.md", "mother-o-f.md", "mother-o-f-m.md")

REQ_RE = re.compile(r"MOTHER-REQ-\d{3}")
DESIGN_RE = re.compile(r"MOTHER-DESIGN-\d{3}")
OP_RE = re.compile(r"MOTHER-OP-[A-Z0-9-]+")
FUNC_RE = re.compile(r"MOTHER-OF-[A-Z]+-\d{3}")
MODULE_RE = re.compile(r"MOTHER-OFM-[A-Z]+-\d{3}")
METHOD_RE = re.compile(r"MOTHER-OFM-[A-Z]+-\d{3}\.[A-Za-z_][A-Za-z0-9_]*")
OPEN_ERROR_RE = re.compile(r"MOTHER_OPEN_[A-Z0-9_]+")

METHOD_QUALIFIED_CONTRACT_MODULES = frozenset(
    {
        "MOTHER-OFM-CORE-006",
        "MOTHER-OFM-CORE-007",
        "MOTHER-OFM-CORE-008",
        "MOTHER-OFM-CORE-009",
        "MOTHER-OFM-CORE-010",
        "MOTHER-OFM-STATE-001",
        "MOTHER-OFM-STATE-002",
    }
)

_RANGE_SEPARATORS = r"(?:through|[–—])"


def find_repo_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    for parent in (candidate, *candidate.parents):
        if all((parent / name).is_file() for name in DOC_NAMES):
            return parent
    raise RuntimeError("unable to locate repository root containing mother*.md")


@dataclass(frozen=True)
class MotherDocuments:
    root: Path
    mother: str
    operations: str
    functionalities: str
    modules: str

    @classmethod
    def load(cls, root: Path | None = None) -> "MotherDocuments":
        repo = root or find_repo_root()
        return cls(
            root=repo,
            mother=(repo / "mother.md").read_text(encoding="utf-8"),
            operations=(repo / "mother-o.md").read_text(encoding="utf-8"),
            functionalities=(repo / "mother-o-f.md").read_text(encoding="utf-8"),
            modules=(repo / "mother-o-f-m.md").read_text(encoding="utf-8"),
        )


@dataclass(frozen=True)
class RequirementCoverage:
    functionalities: tuple[str, ...]
    operations: tuple[str, ...]
    modules: tuple[str, ...]


@dataclass(frozen=True)
class OperationStage:
    reference: str
    heading: str
    functionalities: tuple[str, ...]


@dataclass(frozen=True)
class ModuleRecord:
    module_id: str
    path: str
    contract: str


@dataclass(frozen=True)
class ContractTrace:
    requirements: tuple[str, ...]
    operations: tuple[str, ...]
    functionalities: tuple[str, ...]
    modules: tuple[str, ...]
    methods: tuple[str, ...] = ()
    mutating: bool = False
    open_error: str | None = None


def section(text: str, start_heading: str, end_heading: str | None = None) -> str:
    try:
        start = text.index(start_heading)
    except ValueError as exc:
        raise AssertionError(f"missing heading: {start_heading}") from exc
    if end_heading is None:
        return text[start:]
    try:
        end = text.index(end_heading, start + len(start_heading))
    except ValueError as exc:
        raise AssertionError(f"missing heading after {start_heading}: {end_heading}") from exc
    return text[start:end]


def table_ids(text: str, pattern: re.Pattern[str]) -> list[str]:
    ids: list[str] = []
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        match = pattern.search(line)
        if match:
            ids.append(match.group(0))
    return ids


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _expand_references(text: str, kind: str) -> list[str]:
    """Expand ordered full and shorthand ranges without reducing them to sets."""

    clean = text.replace("`", "")
    if kind == "functionality":
        full = r"MOTHER-OF-[A-Z]+-\d{3}"
    elif kind == "module":
        full = r"MOTHER-OFM-[A-Z]+-\d{3}"
    elif kind == "operation":
        return OP_RE.findall(clean)
    else:
        raise ValueError(f"unsupported identifier kind: {kind}")

    pattern = re.compile(
        rf"(?P<start>{full})(?:\s*{_RANGE_SEPARATORS}\s*"
        rf"(?P<end>(?:{full})|\d{{3}}))?"
    )
    result: list[str] = []
    for match in pattern.finditer(clean):
        start = match.group("start")
        end = match.group("end")
        if not end:
            result.append(start)
            continue
        base, start_number = start.rsplit("-", 1)
        if end.isdigit():
            end_base = base
            end_number = end
        else:
            end_base, end_number = end.rsplit("-", 1)
        if end_base != base:
            result.extend((start, end))
            continue
        first = int(start_number)
        last = int(end_number)
        if last < first:
            result.extend((start, f"{base}-{last:03d}"))
            continue
        result.extend(f"{base}-{number:03d}" for number in range(first, last + 1))
    return result


def functionality_references(text: str) -> list[str]:
    return _expand_references(text, "functionality")


def module_references(text: str) -> list[str]:
    return _expand_references(text, "module")


def method_references(text: str) -> list[str]:
    """Return explicit module.method references, including comma shorthand."""

    result: list[str] = []
    pattern = re.compile(
        r"(?P<module>MOTHER-OFM-[A-Z]+-\d{3})"
        r"(?:\.(?P<methods>[A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*))?"
    )
    for match in pattern.finditer(text.replace("`", "")):
        module = match.group("module")
        methods = match.group("methods")
        if not methods:
            continue
        for method in re.split(r"\s*,\s*", methods):
            if method:
                result.append(f"{module}.{method}")
    return result


def requirement_ids(docs: MotherDocuments) -> list[str]:
    index = section(docs.mother, "## Requirements verification index", "## ")
    return table_ids(index, REQ_RE)


def design_ids(docs: MotherDocuments) -> list[str]:
    pattern = re.compile(
        r"^(?:#{1,6}\s+)?`(MOTHER-DESIGN-\d{3}):[^`]+`",
        re.MULTILINE,
    )
    return pattern.findall(docs.mother)


def operation_ids(docs: MotherDocuments) -> list[str]:
    catalog = section(docs.operations, "## 3. Complete operation catalog", "## 4.")
    return table_ids(catalog, OP_RE)


def operation_statuses(docs: MotherDocuments) -> dict[str, str]:
    catalog = section(docs.operations, "## 3. Complete operation catalog", "## 4.")
    result: dict[str, str] = {}
    for line in catalog.splitlines():
        if not line.startswith("| `MOTHER-OP-"):
            continue
        cells = _table_cells(line)
        op_match = OP_RE.search(cells[0])
        if op_match and len(cells) >= 6:
            result[op_match.group(0)] = cells[-1]
    return result


def functionality_ids(docs: MotherDocuments) -> list[str]:
    registry = section(
        docs.functionalities,
        "## 3. Stable functionality registry",
        "## 4. Operation:",
    )
    return table_ids(registry, FUNC_RE)


def gap_ids(docs: MotherDocuments) -> set[str]:
    return set(re.findall(r"MOTHER-OF-GAP-\d{3}", docs.functionalities))


def module_ids(docs: MotherDocuments) -> list[str]:
    registry = section(
        docs.modules,
        "### 5.1 Operation entry modules",
        "### 5.13 Authority-class assignment",
    )
    return table_ids(registry, MODULE_RE)


def _operation_blocks(docs: MotherDocuments) -> list[tuple[str, str, str]]:
    operation_heading = re.compile(
        r"^## (?P<number>\d+)\. Operation:", re.MULTILINE
    )
    top_level_heading = re.compile(r"^## \d+\.", re.MULTILINE)
    blocks: list[tuple[str, str, str]] = []
    for match in operation_heading.finditer(docs.functionalities):
        next_heading = top_level_heading.search(docs.functionalities, match.end())
        end = next_heading.start() if next_heading else len(docs.functionalities)
        block = docs.functionalities[match.start() : end]
        op = OP_RE.search(block)
        if op:
            blocks.append((op.group(0), match.group("number"), block))
    return blocks


def _ordered_stage_functionalities(stage_block: str) -> tuple[str, ...]:
    table_lines = [
        line for line in stage_block.splitlines()
        if line.startswith("|") and FUNC_RE.search(line)
    ]
    if table_lines:
        ordered: list[str] = []
        for line in table_lines:
            ordered.extend(functionality_references(line))
        return tuple(ordered)

    ordered = []
    current: list[str] = []
    for line in stage_block.splitlines():
        if re.match(r"^\d+\.\s+", line):
            if current:
                ordered.extend(functionality_references(" ".join(current)))
            current = [line]
        elif current and (line.startswith("   ") or not line.strip()):
            current.append(line.strip())
        elif current:
            ordered.extend(functionality_references(" ".join(current)))
            current = []
    if current:
        ordered.extend(functionality_references(" ".join(current)))
    return tuple(ordered)


def operation_functionality_references(docs: MotherDocuments) -> dict[str, tuple[str, ...]]:
    stages = operation_stage_sequences(docs)
    return {
        operation: tuple(
            functionality
            for stage in operation_stages
            for functionality in stage.functionalities
        )
        for operation, operation_stages in stages.items()
    }


def operation_stage_sequences(docs: MotherDocuments) -> dict[str, tuple[OperationStage, ...]]:
    result: dict[str, tuple[OperationStage, ...]] = {}
    subheading = re.compile(r"^### (?P<ref>\d+\.\d+)\s+(?P<title>.+)$", re.MULTILINE)
    for operation, _number, block in _operation_blocks(docs):
        matches = list(subheading.finditer(block))
        stages: list[OperationStage] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
            stage_block = block[match.start() : end]
            ordered = _ordered_stage_functionalities(stage_block)
            stages.append(
                OperationStage(
                    reference=match.group("ref"),
                    heading=match.group("title").strip(),
                    functionalities=ordered,
                )
            )
        result[operation] = tuple(stages)
    return result


def operation_stage_bindings(docs: MotherDocuments) -> dict[str, tuple[str, ...]]:
    binding = section(docs.modules, "## 8. Operation and stage binding", "### 8.1")
    result: dict[str, tuple[str, ...]] = {}
    for line in binding.splitlines():
        if not line.startswith("|") or not OP_RE.search(line):
            continue
        operation = OP_RE.search(line)
        assert operation is not None
        refs = tuple(re.findall(r"§(\d+\.\d+)", line))
        if operation.group(0) in result:
            raise AssertionError(f"duplicate operation-stage binding: {operation.group(0)}")
        result[operation.group(0)] = refs
    return result


def operation_entry_modules(docs: MotherDocuments) -> dict[str, str]:
    binding = section(docs.modules, "## 8. Operation and stage binding", "### 8.1")
    result: dict[str, str] = {}
    for line in binding.splitlines():
        if not line.startswith("|") or not OP_RE.search(line):
            continue
        cells = _table_cells(line)
        operation = OP_RE.search(cells[1])
        module = MODULE_RE.search(cells[2])
        if operation and module:
            if operation.group(0) in result:
                raise AssertionError(f"duplicate operation entry module: {operation.group(0)}")
            result[operation.group(0)] = module.group(0)
    return result


def functionality_module_rows(docs: MotherDocuments) -> dict[str, tuple[str, ...]]:
    composition = section(
        docs.modules,
        "## 7. Functionality-to-module composition",
        "## 8. Operation and stage binding",
    )
    rows: dict[str, tuple[str, ...]] = {}
    for line in composition.splitlines():
        if not line.startswith("| `MOTHER-OF-"):
            continue
        func = FUNC_RE.search(line)
        if not func:
            continue
        identifier = func.group(0)
        if identifier in rows:
            raise AssertionError(f"duplicate functionality-to-module row: {identifier}")
        cells = _table_cells(line)
        chain_cell = cells[1] if len(cells) > 1 else line
        rows[identifier] = tuple(module_references(chain_cell))
    return rows


def functionality_method_rows(docs: MotherDocuments) -> dict[str, tuple[str, ...]]:
    composition = section(
        docs.modules,
        "## 7. Functionality-to-module composition",
        "## 8. Operation and stage binding",
    )
    rows: dict[str, tuple[str, ...]] = {}
    for line in composition.splitlines():
        if not line.startswith("| `MOTHER-OF-"):
            continue
        func = FUNC_RE.search(line)
        if not func:
            continue
        identifier = func.group(0)
        if identifier in rows:
            raise AssertionError(f"duplicate functionality-to-method row: {identifier}")
        cells = _table_cells(line)
        chain_cell = cells[1] if len(cells) > 1 else line
        rows[identifier] = tuple(method_references(chain_cell))
    return rows



def module_public_method_rows(docs: MotherDocuments) -> dict[str, tuple[str, ...]]:
    """Return explicitly named public methods from the stable module registry."""

    core = section(docs.modules, "### 5.2 Core modules", "### 5.3")
    methods: dict[str, list[str]] = {}
    for line in core.splitlines():
        if not line.startswith("| `MOTHER-OFM-"):
            continue
        cells = _table_cells(line)
        if len(cells) < 3:
            continue
        module_match = MODULE_RE.search(cells[0])
        if not module_match:
            continue
        module_id = module_match.group(0)
        api_cell = cells[2].replace("`", "")
        names = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\(|,|$)", api_cell)
        if names:
            methods.setdefault(module_id, []).extend(names)

    for references in functionality_method_rows(docs).values():
        for reference in references:
            module_id, method_name = reference.split(".", 1)
            methods.setdefault(module_id, []).append(method_name)

    return {
        module_id: tuple(dict.fromkeys(names))
        for module_id, names in methods.items()
    }

def requirement_coverage(docs: MotherDocuments) -> dict[str, RequirementCoverage]:
    function_section = section(
        docs.functionalities,
        "## 25. Requirement-to-functionality coverage",
        "## 26.",
    )
    module_section = section(
        docs.modules,
        "## 13. Requirement-to-module coverage",
        "## 14.",
    )
    function_map: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for line in function_section.splitlines():
        if not line.startswith("| `MOTHER-REQ-"):
            continue
        cells = _table_cells(line)
        requirement = REQ_RE.search(cells[0])
        if not requirement:
            continue
        funcs = tuple(functionality_references(cells[1])) if len(cells) > 1 else ()
        operations = tuple(OP_RE.findall(cells[1] + " " + (cells[2] if len(cells) > 2 else "")))
        function_map[requirement.group(0)] = (funcs, operations)

    module_map: dict[str, tuple[str, ...]] = {}
    for line in module_section.splitlines():
        if not line.startswith("| `MOTHER-REQ-"):
            continue
        cells = _table_cells(line)
        requirement = REQ_RE.search(cells[0])
        if requirement:
            module_map[requirement.group(0)] = tuple(
                module_references(cells[1] if len(cells) > 1 else "")
            )

    return {
        requirement: RequirementCoverage(
            functionalities=function_map.get(requirement, ((), ()))[0],
            operations=function_map.get(requirement, ((), ()))[1],
            modules=module_map.get(requirement, ()),
        )
        for requirement in requirement_ids(docs)
    }


def blocked_module_ids(docs: MotherDocuments) -> set[str]:
    block = section(docs.modules, "### 15.2 Contract-open", "## 16.")
    return set(MODULE_RE.findall(block))


def hard_contract_open_operations(docs: MotherDocuments) -> set[str]:
    return {
        op
        for op, status in operation_statuses(docs).items()
        if status.lower().startswith("contract-open")
    }


def documented_open_error_ancestry(
    docs: MotherDocuments,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    """Return exact documented open errors by operation, functionality, and module."""

    function_errors: dict[str, set[str]] = {}
    module_errors: dict[str, set[str]] = {}

    heading = re.compile(r"^### 7\.\d+\s+", re.MULTILINE)
    matches = list(heading.finditer(docs.modules))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else docs.modules.find("## 8.", match.start())
        if end < 0:
            end = len(docs.modules)
        block = docs.modules[match.start() : end]
        errors = set(OPEN_ERROR_RE.findall(block))
        if not errors:
            continue
        for functionality in functionality_references(block):
            function_errors.setdefault(functionality, set()).update(errors)
        for module in module_references(block):
            module_errors.setdefault(module, set()).update(errors)

    registry = section(
        docs.modules,
        "### 5.1 Operation entry modules",
        "### 5.13 Authority-class assignment",
    )
    for line in registry.splitlines():
        errors = set(OPEN_ERROR_RE.findall(line))
        if not errors:
            continue
        for module in module_references(line):
            module_errors.setdefault(module, set()).update(errors)

    operation_errors: dict[str, set[str]] = {}
    op_functions = operation_functionality_references(docs)
    for operation, functions in op_functions.items():
        errors: set[str] = set()
        for functionality in functions:
            errors.update(function_errors.get(functionality, set()))
        entry = operation_entry_modules(docs).get(operation)
        if entry:
            errors.update(module_errors.get(entry, set()))
        if errors:
            operation_errors[operation] = errors

    return operation_errors, function_errors, module_errors


def module_records(docs: MotherDocuments) -> dict[str, ModuleRecord]:
    registry = section(
        docs.modules,
        "### 5.1 Operation entry modules",
        "### 5.13 Authority-class assignment",
    )
    records: dict[str, ModuleRecord] = {}
    for line in registry.splitlines():
        if not line.startswith("| `MOTHER-OFM-"):
            continue
        cells = _table_cells(line)
        module = MODULE_RE.search(cells[0])
        if not module:
            continue
        identifier = module.group(0)
        if identifier in records:
            raise AssertionError(f"duplicate module registry row: {identifier}")
        path = cells[1].strip("`") if len(cells) > 1 else ""
        contract = " ".join(cells[2:]) if len(cells) > 2 else ""
        records[identifier] = ModuleRecord(identifier, path, contract)
    return records


def external_effect_owner_ids(docs: MotherDocuments) -> tuple[str, ...]:
    assignment = section(
        docs.modules,
        "### 5.13 Authority-class assignment",
        "## 6.",
    )
    owners: list[str] = []
    for line in assignment.splitlines():
        if line.startswith("|") and "live-adapter" in line.lower():
            owners.extend(module_references(line))
    return tuple(owners)


def faultpoint_bearing_functionalities(docs: MotherDocuments) -> set[str]:
    """Return the explicit CORE-013 functionality set documented in section 3.3.1."""

    declared = section(
        docs.modules,
        "#### 3.3.1 Explicit CORE-013 faultpoint-bearing functionalities",
        "## 4. Shared type and error contract",
    )
    result: set[str] = set()
    for line in declared.splitlines():
        if not line.startswith("|"):
            continue
        result.update(functionality_references(line))
    return result


def implicit_shared_core_modules(
    docs: MotherDocuments,
    functionality: str,
    chain: Sequence[str],
) -> set[str]:
    """Return only the shared-core ancestry explicitly documented in section 3.3."""

    del chain  # Explicit functionality authority, not broad module-class inference.
    implicit = {"MOTHER-OFM-CORE-001", "MOTHER-OFM-CORE-002"}
    if functionality in faultpoint_bearing_functionalities(docs):
        implicit.add("MOTHER-OFM-CORE-013")
    return implicit


def module_layer(module_id: str) -> str:
    family = module_id.split("-")[2]
    if family == "APP":
        return "operation"
    if family == "CORE":
        return "core"
    if family in {"STATE", "OBS", "XPORT", "ID", "SVC", "NET"}:
        return "state-adapter"
    if family in {"CTL", "AUTH", "RB", "MEM", "REC", "MAINT"}:
        return "control-protocol"
    raise AssertionError(f"unclassified module family: {module_id}")


def _resolve_imported_mother_path(
    source_path: str,
    node: ast.Import | ast.ImportFrom,
) -> str | None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("tools.mother."):
                return alias.name.removeprefix("tools.mother.").replace(".", "/") + ".py"
        return None

    module = node.module or ""
    if node.level == 0:
        if module.startswith("tools.mother."):
            return module.removeprefix("tools.mother.").replace(".", "/") + ".py"
        return None

    source_parts = Path(source_path).with_suffix("").parts
    package = list(source_parts[:-1])
    if node.level > len(package) + 1:
        return None
    base = package[: len(package) - node.level + 1]
    if module:
        base.extend(module.split("."))
    return "/".join(base) + ".py"


def module_dependency_violations(docs: MotherDocuments) -> list[str]:
    """Check implemented Mother imports against section 3.2 when files exist."""

    records = module_records(docs)
    path_to_id = {record.path: identifier for identifier, record in records.items()}
    allowed_targets = {
        "core": {"core"},
        "state-adapter": {"core"},
        "control-protocol": {"state-adapter", "core"},
        "operation": {"operation", "control-protocol", "state-adapter", "core"},
    }
    violations: list[str] = []

    for source_id, record in records.items():
        path = docs.root / "tools" / "mother" / record.path
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(f"{source_id}: invalid Python syntax: {exc}")
            continue
        source_layer = module_layer(source_id)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            imported_path = _resolve_imported_mother_path(record.path, node)
            target_id = path_to_id.get(imported_path or "")
            if not target_id:
                continue
            target_layer = module_layer(target_id)
            if target_layer not in allowed_targets[source_layer]:
                violations.append(
                    f"{source_id} ({source_layer}) imports {target_id} ({target_layer})"
                )
    return violations


def _is_subsequence(values: Sequence[str], canonical: Sequence[str]) -> bool:
    iterator = iter(canonical)
    return all(any(candidate == value for candidate in iterator) for value in values)


def validate_contract_trace(
    trace: ContractTrace,
    docs: MotherDocuments,
    fixture_names: Iterable[str] = (),
    direct_methods: Iterable[str] = (),
) -> list[str]:
    """Validate identifier existence, ancestry, order, and contract-open guards."""

    errors: list[str] = []
    metadata: Mapping[str, tuple[str, ...]] = {
        "requirements": trace.requirements,
        "operations": trace.operations,
        "functionalities": trace.functionalities,
        "modules": trace.modules,
    }
    known: Mapping[str, set[str]] = {
        "requirements": set(requirement_ids(docs)),
        "operations": set(operation_ids(docs)),
        "functionalities": set(functionality_ids(docs)),
        "modules": set(module_ids(docs)),
    }

    for key, identifiers in metadata.items():
        if not identifiers:
            errors.append(f"mother_contract requires at least one {key[:-1]} ID")
        duplicate_values = duplicates(identifiers)
        if duplicate_values:
            errors.append(f"duplicate {key}: {sorted(duplicate_values)}")
        unknown = [identifier for identifier in identifiers if identifier not in known[key]]
        if unknown:
            errors.append(f"unknown {key}: {unknown}")

    if errors:
        return errors

    gaps = gap_ids(docs)
    gap_refs = [identifier for identifier in trace.functionalities if identifier in gaps]
    if gap_refs:
        errors.append(
            f"documented gaps cannot be referenced as resolved functionalities: {gap_refs}"
        )

    op_functions = operation_functionality_references(docs)
    for functionality in trace.functionalities:
        parents = [
            operation
            for operation in trace.operations
            if functionality in op_functions.get(operation, ())
        ]
        if not parents:
            errors.append(
                f"{functionality} is not in any claimed operation pipeline: "
                f"{list(trace.operations)}"
            )

    for operation in trace.operations:
        claimed = [
            functionality
            for functionality in trace.functionalities
            if functionality in op_functions.get(operation, ())
        ]
        if claimed and not _is_subsequence(claimed, op_functions[operation]):
            errors.append(
                f"functionalities are out of order for {operation}: {claimed}"
            )

    function_modules = functionality_module_rows(docs)
    function_methods = functionality_method_rows(docs)
    entry_modules = operation_entry_modules(docs)
    claimed_entries = {
        entry_modules[operation]
        for operation in trace.operations
        if operation in entry_modules
    }
    for module in trace.modules:
        if module in claimed_entries:
            continue
        parents = [
            functionality
            for functionality in trace.functionalities
            if (
                module in function_modules.get(functionality, ())
                or module
                in implicit_shared_core_modules(
                    docs,
                    functionality,
                    function_modules.get(functionality, ()),
                )
            )
        ]
        if not parents:
            errors.append(
                f"{module} is not in any claimed functionality chain and is not "
                "an explicitly documented shared-core dependency or the claimed "
                "operation entry module"
            )

    for functionality in trace.functionalities:
        canonical_chain = function_modules[functionality]
        implicit = implicit_shared_core_modules(docs, functionality, canonical_chain)
        claimed_explicit = [
            module
            for module in trace.modules
            if module in canonical_chain
        ]
        if claimed_explicit and not _is_subsequence(claimed_explicit, canonical_chain):
            errors.append(
                f"modules are out of order for {functionality}: {claimed_explicit}"
            )
        unsupported_implicit = [
            module
            for module in trace.modules
            if module.startswith("MOTHER-OFM-CORE-")
            and module not in canonical_chain
            and module not in implicit
        ]
        if unsupported_implicit:
            errors.append(
                f"unsupported implicit core ancestry for {functionality}: "
                f"{unsupported_implicit}"
            )

    method_pattern = re.compile(
        r"^MOTHER-OFM-[A-Z]+-\d{3}\.[A-Za-z_][A-Za-z0-9_]*$"
    )
    method_qualified_modules = tuple(
        module
        for module in trace.modules
        if module in METHOD_QUALIFIED_CONTRACT_MODULES
    )
    if method_qualified_modules and not trace.methods:
        errors.append(
            "mother_contract requires methods metadata for method-qualified modules: "
            f"{list(method_qualified_modules)}"
        )

    duplicate_methods = duplicates(trace.methods)
    if duplicate_methods:
        errors.append(f"duplicate methods: {sorted(duplicate_methods)}")
    for method in trace.methods:
        if not method_pattern.fullmatch(method):
            errors.append(f"invalid method reference: {method!r}")
            continue
        module_id = method.split(".", 1)[0]
        if module_id not in trace.modules:
            errors.append(
                f"{method} belongs to {module_id}, which is not claimed in modules"
            )
            continue
        parents = [
            functionality
            for functionality in trace.functionalities
            if method in function_methods.get(functionality, ())
        ]
        if not parents:
            errors.append(
                f"{method} is not in any claimed functionality method chain: "
                f"{list(trace.functionalities)}"
            )

    direct_method_tuple = tuple(direct_methods)
    duplicate_direct_methods = duplicates(direct_method_tuple)
    if duplicate_direct_methods:
        errors.append(
            f"duplicate direct public-method calls: {sorted(duplicate_direct_methods)}"
        )
    for method in direct_method_tuple:
        if method not in trace.methods:
            errors.append(
                f"direct public-method call {method} is omitted from methods metadata"
            )

    for functionality in trace.functionalities:
        canonical_methods = function_methods.get(functionality, ())
        claimed_methods = [
            method for method in trace.methods if method in canonical_methods
        ]
        if claimed_methods and not _is_subsequence(claimed_methods, canonical_methods):
            errors.append(
                f"methods are out of order for {functionality}: {claimed_methods}"
            )

    coverage = requirement_coverage(docs)
    for requirement in trace.requirements:
        item = coverage[requirement]
        if item.functionalities:
            if not any(value in item.functionalities for value in trace.functionalities):
                errors.append(
                    f"{requirement} has no documented functionality ancestry in "
                    f"{list(trace.functionalities)}"
                )
        elif item.operations:
            if not any(value in item.operations for value in trace.operations):
                errors.append(
                    f"{requirement} has no documented operation ancestry in "
                    f"{list(trace.operations)}"
                )
        elif item.modules:
            if not any(value in item.modules for value in trace.modules):
                errors.append(
                    f"{requirement} has no documented module ancestry in "
                    f"{list(trace.modules)}"
                )

    blocked_modules = blocked_module_ids(docs)
    open_operations = hard_contract_open_operations(docs)
    touches_open = bool(
        set(trace.operations) & open_operations
        or set(trace.modules) & blocked_modules
    )
    if trace.mutating and touches_open:
        fixtures = set(fixture_names)
        if "mother_open_contract_guard" not in fixtures:
            errors.append(
                "contract-open mutation tests must request "
                "mother_open_contract_guard"
            )
        op_errors, function_errors, module_errors = documented_open_error_ancestry(docs)
        allowed_errors: set[str] = set()
        for operation in trace.operations:
            allowed_errors.update(op_errors.get(operation, set()))
        for functionality in trace.functionalities:
            allowed_errors.update(function_errors.get(functionality, set()))
        for module in trace.modules:
            allowed_errors.update(module_errors.get(module, set()))
        if trace.open_error is None:
            errors.append(
                "contract-open mutation tests must declare open_error with the "
                "exact documented MOTHER_OPEN_* code"
            )
        elif trace.open_error not in allowed_errors:
            errors.append(
                f"open_error {trace.open_error!r} is not documented for this trace; "
                f"allowed: {sorted(allowed_errors)}"
            )

    return errors


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def pinned_hash(document: str, source_name: str) -> str:
    patterns = (
        rf"{re.escape(source_name)}\s*\nSHA-256:\s*([0-9a-f]{{64}})",
        rf"Source reviewed:\s*`{re.escape(source_name)}`\s*SHA-256\s*\n`([0-9a-f]{{64}})`",
    )
    for pattern in patterns:
        match = re.search(pattern, document)
        if match:
            return match.group(1)
    raise AssertionError(f"missing source hash pin for {source_name}")


def outside_fenced_blocks(text: str) -> str:
    kept: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            kept.append(line)
    return "\n".join(kept)


def balanced_fences(text: str) -> bool:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("```")) % 2 == 0


def duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return duplicate
