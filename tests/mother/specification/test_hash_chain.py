from __future__ import annotations

import pytest

from tests.mother.support.traceability import MotherDocuments, file_sha256, pinned_hash


pytestmark = pytest.mark.mother_specification


def test_parent_source_hash_chain() -> None:
    docs = MotherDocuments.load()
    assert pinned_hash(docs.operations, "mother.md") == file_sha256(docs.root / "mother.md")
    assert pinned_hash(docs.functionalities, "mother.md") == file_sha256(docs.root / "mother.md")
    assert pinned_hash(docs.functionalities, "mother-o.md") == file_sha256(docs.root / "mother-o.md")
    assert pinned_hash(docs.modules, "mother.md") == file_sha256(docs.root / "mother.md")
    assert pinned_hash(docs.modules, "mother-o.md") == file_sha256(docs.root / "mother-o.md")
    assert pinned_hash(docs.modules, "mother-o-f.md") == file_sha256(docs.root / "mother-o-f.md")
