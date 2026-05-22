#!/usr/bin/env python3
"""
Smoke test: typed bounded mutator chain.

This deterministic smoke locks down the distinction:

  verified excerpt proposal != full-file replacement != patch artifact

It also checks that a RAG/proposal loop is halted by an external budget, not by
trusting the mutator to eventually stop.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


MODE = "rag_typed_mutator_chain_smoke"

SOURCE = """function renderRunningAiCell(cell, controls) {
  if (cell.type === "ai" && cell.status === "running") controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));
}
"""
OLD = 'controls.append(chatConsoleButton("Stop", () => stopChatConsoleAiRequest(cell.id)));'
NEW = 'controls.append(chatConsoleButton("Cancel", () => stopChatConsoleAiRequest(cell.id)));'


@dataclass
class State:
    objects: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    terminal: bool = False
    status: str = "running"
    issues: list[str] = field(default_factory=list)

    def clone(self) -> "State":
        return State(
            objects=json.loads(json.dumps(self.objects)),
            events=json.loads(json.dumps(self.events)),
            terminal=self.terminal,
            status=self.status,
            issues=list(self.issues),
        )


@dataclass
class Result:
    ok: bool
    status: str
    state: State
    issues: list[str] = field(default_factory=list)
    terminal: bool = False


Mutator = Callable[[State], Result]


def event(state: State, mutation: str, status: str, **extra: Any) -> None:
    state.events.append({"index": len(state.events) + 1, "mutation": mutation, "status": status, **extra})


def accept(state: State, mutation: str, **extra: Any) -> Result:
    event(state, mutation, "accepted", **extra)
    return Result(True, "accepted", state)


def reject(state: State, mutation: str, issue: str, **extra: Any) -> Result:
    state.issues.append(issue)
    event(state, mutation, "rejected", issue=issue, **extra)
    return Result(False, "rejected", state, [issue])


def budget_stop(state: State, issue: str) -> Result:
    state.status = "budget_exhausted"
    state.terminal = True
    state.issues.append(issue)
    event(state, "EXTERNAL_DRIVER", "budget_exhausted", issue=issue)
    return Result(False, "budget_exhausted", state, [issue], terminal=True)


def run_chain(mutators: list[Mutator], *, max_steps: int, max_seconds: float) -> Result:
    state = State()
    start = time.monotonic()
    for step, mutator in enumerate(mutators, start=1):
        if step > max_steps:
            return budget_stop(state, f"max_steps exceeded before {mutator.__name__}")
        if time.monotonic() - start > max_seconds:
            return budget_stop(state, f"max_seconds exceeded before {mutator.__name__}")
        result = mutator(state)
        state = result.state
        if not result.ok or result.terminal:
            return result
    return Result(True, "accepted_nonterminal", state)


def run_repeating(mutator: Mutator, *, max_steps: int, max_seconds: float) -> Result:
    state = State(objects={"rag_command": "proposal=RAG(prompt(RAG(...)))"})
    start = time.monotonic()
    for _ in range(max_steps):
        if time.monotonic() - start > max_seconds:
            return budget_stop(state, "max_seconds exhausted during repeating mutator")
        result = mutator(state)
        state = result.state
        if not result.ok or result.terminal:
            return result
    return budget_stop(state, "max_steps exhausted while mutator requested continuation")


def SELECT(state: State) -> Result:
    state.objects["target"] = {"path": "synthetic/chat-console-fragment.js"}
    return accept(state, "SELECT", output="target")


def RETRIEVE(state: State) -> Result:
    if "target" not in state.objects:
        return reject(state, "RETRIEVE", "missing target")
    state.objects["source_file"] = {"path": state.objects["target"]["path"], "content": SOURCE}
    return accept(state, "RETRIEVE", output="source_file")


def ANCHOR(state: State) -> Result:
    source = state.objects.get("source_file", {}).get("content", "")
    count = source.count(OLD)
    if count != 1:
        return reject(state, "ANCHOR", f"expected exactly one old excerpt, got {count}")
    state.objects["anchor"] = {"exact_text": OLD, "occurrences": count}
    return accept(state, "ANCHOR", output="anchor")


def ASSERT(state: State) -> Result:
    if "anchor" not in state.objects:
        return reject(state, "ASSERT", "missing anchor")
    state.objects["grounding"] = {
        "change": "Stop label becomes Cancel",
        "preserve": [
            "stopChatConsoleAiRequest(cell.id)",
            "controls.append(chatConsoleButton(...))",
        ],
    }
    return accept(state, "ASSERT", output="grounding")


def PROPOSE(state: State) -> Result:
    if "grounding" not in state.objects:
        return reject(state, "PROPOSE", "missing grounding")
    state.objects["edit_proposal"] = {
        "scope": "verified_excerpt",
        "old_excerpt": OLD,
        "new_excerpt": NEW,
        "terminal_object": False,
    }
    return accept(state, "PROPOSE", output="edit_proposal")


def VERIFY_PROPOSAL(state: State) -> Result:
    proposal = state.objects.get("edit_proposal")
    if not proposal:
        return reject(state, "VERIFY", "missing edit_proposal")
    new = proposal["new_excerpt"]
    failures = []
    if '"Cancel"' not in new:
        failures.append("Cancel label missing")
    if "stopChatConsoleAiRequest(cell.id)" not in new:
        failures.append("handler not preserved")
    if "controls.append(chatConsoleButton(" not in new:
        failures.append("append structure not preserved")
    if proposal.get("scope") != "verified_excerpt":
        failures.append("proposal scope is not verified_excerpt")
    if failures:
        return reject(state, "VERIFY", "; ".join(failures))
    state.objects["edit_proposal_verified"] = {"ok": True, "scope": "verified_excerpt"}
    return accept(state, "VERIFY", output="edit_proposal_verified")


def PROMOTE(state: State) -> Result:
    source = state.objects.get("source_file", {}).get("content", "")
    proposal = state.objects.get("edit_proposal", {})
    old, new = proposal.get("old_excerpt"), proposal.get("new_excerpt")
    if not source or not old or not new:
        return reject(state, "PROMOTE", "missing source/proposal")
    count = source.count(old)
    if count != 1:
        return reject(state, "PROMOTE", f"ambiguous excerpt occurrence count={count}")
    state.objects["replacement_file"] = {
        "scope": "full_file_replacement",
        "path": state.objects["source_file"]["path"],
        "content": source.replace(old, new, 1),
    }
    return accept(state, "PROMOTE", output="replacement_file")


def VERIFY_REPLACEMENT(state: State) -> Result:
    replacement = state.objects.get("replacement_file")
    if not replacement:
        return reject(state, "VERIFY", "missing replacement_file")
    content = replacement["content"]
    failures = []
    if OLD in content:
        failures.append("old excerpt still present")
    if content.count(NEW) != 1:
        failures.append("new excerpt count is not exactly one")
    if "stopChatConsoleAiRequest(cell.id)" not in content:
        failures.append("handler missing")
    if "controls.append(chatConsoleButton(" not in content:
        failures.append("append structure missing")
    if failures:
        return reject(state, "VERIFY", "; ".join(failures))
    state.objects["replacement_verified"] = {"ok": True, "scope": "full_file_replacement"}
    return accept(state, "VERIFY", output="replacement_verified")


def PACKAGE(state: State) -> Result:
    if not state.objects.get("replacement_verified", {}).get("ok"):
        return reject(state, "PACKAGE", "replacement was not verified")
    replacement = state.objects["replacement_file"]
    state.objects["artifact"] = {
        "mode": "replacement_file_artifact",
        "files": [{"path": replacement["path"], "content": replacement["content"]}],
    }
    return accept(state, "PACKAGE", output="artifact")


def VERIFY_DRY_RUN(state: State) -> Result:
    artifact = state.objects.get("artifact")
    if not artifact or len(artifact.get("files", [])) != 1:
        return reject(state, "VERIFY", "artifact is missing or malformed")
    state.objects["dry_run"] = {"ok": True, "verification": "exact"}
    state.objects["artifact_promotable"] = True
    state.terminal = True
    state.status = "accepted_terminal_artifact"
    event(state, "HALT", "accepted_terminal_artifact", terminal_object="artifact")
    return Result(True, "accepted_terminal_artifact", state, terminal=True)


def NONHALTING_RAG_PROPOSAL(state: State) -> Result:
    count = int(state.objects.get("loop_count", 0)) + 1
    state.objects["loop_count"] = count
    event(
        state,
        "PROPOSE",
        "continue",
        output="nonterminal_rag_command",
        loop_count=count,
        note="mutator requested another RAG/prompt/proposal cycle",
    )
    return Result(True, "continue", state, terminal=False)


def artifact_promotable(state: State) -> bool:
    return bool(
        state.objects.get("edit_proposal_verified", {}).get("ok")
        and state.objects.get("replacement_verified", {}).get("ok")
        and state.objects.get("artifact")
        and state.objects.get("dry_run", {}).get("verification") == "exact"
    )


def no_intermediate_promotable_leak(state: State) -> tuple[bool, list[str]]:
    issues = []
    proposal_scope = state.objects.get("edit_proposal", {}).get("scope")
    claimed = bool(state.objects.get("artifact_promotable"))
    if proposal_scope == "verified_excerpt" and claimed and not state.objects.get("replacement_file"):
        issues.append("verified excerpt proposal marked promotable without replacement_file")
    if claimed and not state.objects.get("artifact"):
        issues.append("promotable true without artifact")
    if claimed and state.objects.get("dry_run", {}).get("verification") != "exact":
        issues.append("promotable true without exact dry-run verification")
    return not issues, issues


def inspect_existing_pipeline(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "main_computer" / "rag_generated_editor_discovery_grounding_smoke.py"
    out: dict[str, Any] = {"checked": True, "path": str(path), "ok": True, "issues": [], "warnings": []}
    if not path.exists():
        out.update({"checked": False, "warnings": ["pipeline file not found; skipped"]})
        return out

    text = path.read_text(encoding="utf-8", errors="replace")
    assignments = re.findall(r"(?m)^\s*promotable\s*=\s*(.+)$", text)
    out["promotable_assignments"] = assignments

    for expr in assignments:
        lower = expr.lower()
        has_patch_ok = "patch_result.ok" in expr
        has_terminal_gate = any(
            token in lower
            for token in (
                "full_file_promotion",
                "promotion_result",
                "replacement",
                "artifact",
                "mutation_result",
                "materialization",
            )
        )
        if has_patch_ok and not has_terminal_gate:
            out["issues"].append(
                "promotable is derived from patch_result.ok without requiring promotion/materialization/artifact readiness"
            )

    if "verified_excerpt_not_full_file" in text and not any(
        token in text
        for token in (
            "full_file_promotion",
            "promotion_result",
            "replacement_materialization",
            "mutation_result",
            "artifact_ready",
            "promoted_full_file",
        )
    ):
        out["issues"].append(
            "verified_excerpt_not_full_file exists without a visible promotion/materialization contract"
        )

    if "--require-promotable" in text and "patch proposal passes all checks" in text:
        out["issues"].append(
            "--require-promotable help text still describes patch proposal checks, not terminal artifact readiness"
        )

    out["ok"] = not out["issues"]
    return out


def case_verified_excerpt_is_nonterminal() -> dict[str, Any]:
    result = run_chain(
        [SELECT, RETRIEVE, ANCHOR, ASSERT, PROPOSE, VERIFY_PROPOSAL],
        max_steps=8,
        max_seconds=1.0,
    )
    state = result.state
    leaked = state.clone()
    leaked.objects["artifact_promotable"] = True
    leak_ok, leak_issues = no_intermediate_promotable_leak(leaked)

    ok = result.ok and artifact_promotable(state) is False and leak_ok is False
    return {
        "name": "verified_excerpt_is_nonterminal",
        "ok": ok,
        "result_status": result.status,
        "computed_artifact_promotable": artifact_promotable(state),
        "semantic_leak_rejected": not leak_ok,
        "semantic_leak_issues": leak_issues,
        "events": state.events,
        "issues": [] if ok else ["verified excerpt was allowed to behave like a terminal artifact"],
    }


def case_happy_path_reaches_terminal_artifact() -> dict[str, Any]:
    result = run_chain(
        [
            SELECT,
            RETRIEVE,
            ANCHOR,
            ASSERT,
            PROPOSE,
            VERIFY_PROPOSAL,
            PROMOTE,
            VERIFY_REPLACEMENT,
            PACKAGE,
            VERIFY_DRY_RUN,
        ],
        max_steps=12,
        max_seconds=1.0,
    )
    state = result.state
    semantic_ok, semantic_issues = no_intermediate_promotable_leak(state)
    ok = (
        result.ok
        and result.terminal
        and result.status == "accepted_terminal_artifact"
        and artifact_promotable(state)
        and semantic_ok
    )
    return {
        "name": "happy_path_reaches_terminal_artifact",
        "ok": ok,
        "result_status": result.status,
        "computed_artifact_promotable": artifact_promotable(state),
        "terminal": result.terminal,
        "semantic_issues": semantic_issues,
        "events": state.events,
        "issues": [] if ok else ["full chain did not reach a verified terminal artifact"],
    }


def case_external_budget_controls_nonhalting_loop(max_steps: int) -> dict[str, Any]:
    result = run_repeating(NONHALTING_RAG_PROPOSAL, max_steps=max_steps, max_seconds=1.0)
    state = result.state
    ok = (
        result.status == "budget_exhausted"
        and state.terminal
        and not artifact_promotable(state)
        and state.objects.get("loop_count") == max_steps
    )
    return {
        "name": "external_budget_controls_nonhalting_rag_loop",
        "ok": ok,
        "result_status": result.status,
        "loop_count": state.objects.get("loop_count"),
        "terminal": state.terminal,
        "events": state.events,
        "issues": [] if ok else ["external budget failed to halt the nonterminal RAG loop"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--skip-source-contract", action="store_true")
    parser.add_argument("--nonhalting-max-steps", type=int, default=3)
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    cases = [
        case_verified_excerpt_is_nonterminal(),
        case_happy_path_reaches_terminal_artifact(),
        case_external_budget_controls_nonhalting_loop(args.nonhalting_max_steps),
    ]
    source_contract = (
        {"checked": False, "ok": True, "issues": [], "warnings": []}
        if args.skip_source_contract
        else inspect_existing_pipeline(repo_root)
    )

    report = {
        "mode": MODE,
        "ok": all(case["ok"] for case in cases) and bool(source_contract["ok"]),
        "repo_root": str(repo_root),
        "lexicon": [
            "SELECT",
            "RETRIEVE",
            "ANCHOR",
            "ASSERT",
            "PROPOSE",
            "VERIFY",
            "PROMOTE",
            "PACKAGE",
            "RUN",
            "OBSERVE",
            "HALT",
        ],
        "terminal_contract": {
            "verified_excerpt_is_terminal": False,
            "proposal_verified_is_promotable": False,
            "external_driver_owns_halting": True,
            "promotable_requires": [
                "edit_proposal_verified",
                "replacement_verified",
                "artifact",
                "exact_dry_run",
            ],
        },
        "cases": cases,
        "source_contract": source_contract,
    }

    output_root = Path(args.output_root) if args.output_root else (
        repo_root / "debug_assets" / MODE / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "final_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())