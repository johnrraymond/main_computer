from __future__ import annotations

import re

import pytest

from tests.mother.support.traceability import MotherDocuments, balanced_fences


pytestmark = pytest.mark.mother_specification


def test_authority_chain_ends_in_traced_tests() -> None:
    docs = MotherDocuments.load()
    expected = """mother.md requirement
  -> mother-o.md operation
    -> mother-o-f.md functionality placement
      -> mother-o-f-m.md module contract
        -> traced contract test
          -> implementation"""
    assert expected in docs.modules


def test_api_registry_is_not_in_the_authority_chain() -> None:
    docs = MotherDocuments.load()
    combined = "\n".join(
        (docs.operations, docs.functionalities, docs.modules)
    )
    forbidden = (
        "api_registry.yaml",
        "validate_api_registry.py",
        "api_registry.schema.json",
        "normative-reviewed",
        "mother-spec",
    )
    assert not [term for term in forbidden if term in combined]
    required_prohibitions = (
        "An API registry MUST NOT be required to collect, execute, or pass tests.",
        "Runtime Mother code MUST NOT read an API registry.",
        "CI MUST NOT treat registry absence as a failure.",
        "A derived registry MUST NOT override documentation, tests, or code contracts",
    )
    normalized = " ".join(docs.modules.split())
    for sentence in required_prohibitions:
        assert sentence in normalized


def test_markdown_fences_are_balanced() -> None:
    docs = MotherDocuments.load()
    for name, text in (
        ("mother.md", docs.mother),
        ("mother-o.md", docs.operations),
        ("mother-o-f.md", docs.functionalities),
        ("mother-o-f-m.md", docs.modules),
    ):
        assert balanced_fences(text), name


def test_downstream_normative_modal_words_are_uppercase() -> None:
    docs = MotherDocuments.load()
    for name, text in (
        ("mother-o.md", docs.operations),
        ("mother-o-f.md", docs.functionalities),
        ("mother-o-f-m.md", docs.modules),
    ):
        outside_fences = []
        in_fence = False
        for line in text.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence:
                outside_fences.append(re.sub(r"`[^`]*`", "", line))
        prose = "\n".join(outside_fences)
        assert re.search(r"\b(?:must|should|may)\b", prose) is None, name
