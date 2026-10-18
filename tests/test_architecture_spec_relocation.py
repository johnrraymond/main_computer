"""Architecture specifications have exactly one current source under pretty_docs."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "pretty_docs"
FAMILIES = ("mother", "fdb", "hub", "hub-fdb", "hub-chain")
SUFFIXES = ("", "-o", "-o-f", "-o-f-m")


def test_specs_export_from_pretty_docs_only():
    exporter = (ROOT / "export-main-computer-test.ps1").read_text(encoding="utf-8")
    indexed = {entry["path"] for entry in json.loads((DOCS / "index.json").read_text(encoding="utf-8"))["documents"]}
    for family in FAMILIES:
        for suffix in SUFFIXES:
            filename = f"{family}{suffix}.md"
            assert (DOCS / filename).is_file(), filename
            assert not (ROOT / filename).exists(), filename
            assert filename in indexed, filename
            assert f'  "{filename}",' not in exporter, filename


def test_testnet_lifecycle_authority_is_not_integrated_supernode_only():
    mother = (DOCS / "mother.md").read_text(encoding="utf-8")
    hub = (DOCS / "hub.md").read_text(encoding="utf-8")
    fdb = (DOCS / "fdb.md").read_text(encoding="utf-8")
    operations = (DOCS / "mother-network-operations.md").read_text(encoding="utf-8")
    assert "A network MAY be born with **chain services only**" in mother
    assert "chain ID `42424241`" in mother
    assert "Hub Control" in hub and "Mother may deploy a chain-only testnet" in hub
    assert "Mother's chain-only testnet birth does not deploy FDB" in fdb
    assert "does **not** implement or authorize chain-only execution" in operations
