"""Bounded, non-production witnesses for the Task Manager design-to-layout bridge."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.task_manager.semantic_layout_probe import (
    DESIGN, canonical_tree, compile_through_dsl, infer_direct, load_design,
    semantic_projection,
)


def test_design_references_and_canonical_regions(tmp_path: Path):
    design=load_design(DESIGN)
    surface,grammar=semantic_projection(design)
    children=design["hierarchy"]["children"]
    assert len(children)==6
    assert len(surface["regions"])==7
    assert len(grammar["regions"])==7
    tree=canonical_tree(design)
    assert tree["schema"]=="mcel.layout-tree.v1"
    assert {x["id"] for x in tree["root"]["children"]}=={x["id"] for x in children}
    assert len(tree["root"]["variants"])==2
    result=compile_through_dsl(tree,tmp_path)
    assert result["valid"] and result["compiledTreeSame"]


def test_auto_inference_does_not_claim_priority_at_upper_left(tmp_path:Path):
    design=load_design(DESIGN)
    surface,grammar=semantic_projection(design)
    results=infer_direct(surface,grammar,tmp_path)
    priority=next(x["id"] for x in design["hierarchy"]["children"] if x["role"]=="status-and-attention")
    primary=next(x["id"] for x in design["hierarchy"]["children"] if x["role"]=="primary-content")
    for width in ("1440","390"):
        assert results[width]["order"][0]==primary
        assert results[width]["order"][0]!=priority
        assert set(results[width]["order"])==set(x["id"] for x in design["hierarchy"]["children"])


def test_semantic_contract_refuses_invalid_row_and_ambiguous_attention(tmp_path:Path):
    design=load_design(DESIGN)
    bad=copy.deepcopy(design)
    bad["rows"]["types"]["process-row"]["entity"]="not-defined"
    path=tmp_path/"broken.yaml"
    import yaml
    path.write_text(yaml.safe_dump(bad),encoding="utf-8")
    with pytest.raises(ValueError,match="unknown entity"):
        load_design(path)
    bad=copy.deepcopy(design)
    bad["hierarchy"]["children"].append(dict(bad["hierarchy"]["children"][0],id="duplicate-attention"))
    path.write_text(yaml.safe_dump(bad),encoding="utf-8")
    with pytest.raises(ValueError,match="Ambiguous application hierarchy role"):
        load_design(path)
