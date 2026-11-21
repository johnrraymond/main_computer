"""Authoritative MCEL planning: IR -> source proof -> template graph match -> layout.

Template applicability *is* successful deterministic argument matching. No
metric census, spectral decision, app name, override JSON, or post-render DOM
rearrangement participates in the runtime plan.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path

from .semantic_composition import sha
from .code_argument_resolver import match_code_workbench
from .workspace_plan import plan_workspace, verify_workspace_plan

SCHEMA = "mcel.semantic-template-binding.v1"

# Registration of reusable semantic implementations, not application names.
_TEMPLATE_MATCHERS = {"code": match_code_workbench}


def plan_semantic_workspace(ir: Mapping, repo: Path | None = None) -> dict:
    """Derive all layout bindings from verified compiled IR, without file I/O."""
    base = plan_workspace(ir)
    verify_workspace_plan(ir, base)
    matches = []
    for name, matcher in sorted(_TEMPLATE_MATCHERS.items()):
        binding = matcher(ir, base)
        if binding is not None:
            if binding.get("profile") != name:
                raise ValueError("Template matcher returned a different implementation")
            matches.append((name, binding))
    if not matches:
        return base
    if len(matches) != 1:
        raise ValueError("Ambiguous semantic template match")
    name, args = matches[0]
    result = copy.deepcopy(base)
    result["semanticTemplate"] = {
        "schema": SCHEMA,
        "profile": name,
        "sourceIrHash": ir["fingerprints"]["semantic"],
        "argumentHash": sha(args),
        "arguments": args,
        "authority": "canonical-ir-and-deterministic-graph-match",
    }
    result.pop("fingerprint", None)
    result["fingerprint"] = sha(result)
    return result


def verify_semantic_workspace_plan(ir: Mapping, plan: Mapping, repo: Path | None = None) -> None:
    """Recompute whole source-bound plan independently; never trust labels."""
    if not isinstance(plan, Mapping) or dict(plan) != plan_semantic_workspace(ir, repo):
        raise ValueError("Semantic template plan disagrees with verified IR graph")
