from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from typing import Iterable

DOC_NAMES = ("mother.md", "mother-o.md", "mother-o-f.md", "mother-o-f-m.md")

REQ_RE = re.compile(r"MOTHER-REQ-\d{3}")
DESIGN_RE = re.compile(r"MOTHER-DESIGN-\d{3}")
OP_RE = re.compile(r"MOTHER-OP-[A-Z0-9-]+")
FUNC_RE = re.compile(r"MOTHER-OF-[A-Z]+-\d{3}")
MODULE_RE = re.compile(r"MOTHER-OFM-[A-Z]+-\d{3}")


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
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
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


def functionality_module_rows(docs: MotherDocuments) -> dict[str, set[str]]:
    composition = section(
        docs.modules,
        "## 7. Functionality-to-module composition",
        "## 8. Operation and stage binding",
    )
    rows: dict[str, set[str]] = {}
    for line in composition.splitlines():
        if not line.startswith("| `MOTHER-OF-"):
            continue
        func = FUNC_RE.search(line)
        if not func:
            continue
        rows[func.group(0)] = set(MODULE_RE.findall(line))
    return rows


def operation_functionality_references(docs: MotherDocuments) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    operation_heading = re.compile(r"^## \d+\. Operation:", re.MULTILINE)
    starts = [match.start() for match in operation_heading.finditer(docs.functionalities)]
    starts.append(len(docs.functionalities))
    for index in range(len(starts) - 1):
        block = docs.functionalities[starts[index] : starts[index + 1]]
        op = OP_RE.search(block)
        if op:
            result[op.group(0)] = set(FUNC_RE.findall(block))
    return result


def blocked_module_ids(docs: MotherDocuments) -> set[str]:
    block = section(docs.modules, "### 15.2 Contract-open", "## 16.")
    return set(MODULE_RE.findall(block))


def hard_contract_open_operations(docs: MotherDocuments) -> set[str]:
    return {
        op
        for op, status in operation_statuses(docs).items()
        if status.lower().startswith("contract-open")
    }


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
