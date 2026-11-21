"""Read-only paired-authoring proof: named nested groups remain source-owned.

No Code Editor operation or presentation script is edited by these fixtures.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.workspace_plan import plan_workspace, verify_workspace_plan
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mcel_apps" / "code-editor"


def _nested_candidate(tmp_path: Path, *, reverse: bool = False):
    html = (SOURCE / "application.html").read_text(encoding="utf-8")
    js = (SOURCE / "application.js").read_text(encoding="utf-8")
    original_html = next(x for x in html.splitlines() if 'data-mcel-id="aider"' in x)
    prefix = original_html[: original_html.index("<h2>")]
    head = "<h2>Aider command preview, jobs and context</h2>"
    sections = {
        "preview": '<section data-mcel-id="aider-preview" data-mcel-purpose="inspection"><h3>Plan preview</h3></section>',
        "execution": '<section data-mcel-id="aider-execution" data-mcel-purpose="commands"><h3>Aider execution</h3></section>',
        "session": '<details data-mcel-id="aider-session" data-mcel-purpose="commands"><h3>Session management</h3></details>',
    }
    names = ["preview", "execution", "session"]
    if reverse:
        names.reverse()
    html = html.replace(original_html, prefix + head + "".join(sections[k] for k in names) + "</section>")
    start = js.index("    aider:[")
    end = js.index("    review:[", start)
    block = js[start:end]
    raw = block[len("    aider:[") :].strip()
    assert raw.endswith("],")
    entries = re.split(r",\s*\n\s*(?=dsl\.surface\.)", raw[:-2])
    assert len(entries) == 11, entries
    assert all(entry.startswith("dsl.surface.") for entry in entries)
    preview = entries[0:2]
    execution = [entries[2], entries[8]]
    session = entries[3:8] + entries[9:]
    assert len(set(preview + execution + session)) == 11
    new_bindings = "".join(
        f'    "aider-{name}":[{", ".join(group)}],\n'
        for name, group in [("preview", preview), ("execution", execution), ("session", session)]
    )
    js = js[:start] + new_bindings + js[end:]
    target = tmp_path / "code-editor"
    target.mkdir(parents=True, exist_ok=True)
    (target / "application.html").write_text(html, encoding="utf-8")
    (target / "application.js").write_text(js, encoding="utf-8")
    report = compile_dsl_application(target / "application.js")
    assert report.valid, report.diagnostics
    return report.normalized_ir


def test_existing_authoring_contract_supports_nested_semantic_groups(tmp_path):
    ir = _nested_candidate(tmp_path)
    assert len(ir["intents"]) == 39
    assert len(ir["capabilities"]) == 13
    assert len(ir["effects"]) == 36
    baseline = compile_dsl_application(SOURCE / "application.js").normalized_ir
    assert baseline is not None
    for section in ("intents", "effects", "capabilities", "states", "models"):
        # Presentation grouping must not be allowed to rewrite operation,
        # capability, state, or effect authority, even when source paths move.
        def without_source(records):
            return [{k:v for k,v in record.items() if k != "source"} for record in records]
        assert without_source(ir[section]) == without_source(baseline[section]), section
    grouped = {r["id"].split(".")[-1]: r for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert len(grouped["aider-execution"]["members"]) == 2
    assert len(grouped["aider-preview"]["members"]) == 2
    assert len(grouped["aider-session"]["members"]) == 7
    assert all(grouped[n]["parentId"] == grouped["aider"]["id"]
               for n in ("aider-execution", "aider-preview", "aider-session"))
    assert grouped["aider"]["members"] == []
    assert grouped["aider-session"]["nativeTag"] == "details"
    plan = plan_workspace(ir)
    verify_workspace_plan(ir, plan)
    assert plan["documentWorkbench"]["companion"] == [grouped["aider"]["id"], grouped["documentation"]["id"]]
    assert {x["id"] for x in plan["nestedGroups"]} == {grouped[n]["id"] for n in
        ("aider-execution", "aider-preview", "aider-session")}
    assert not any(x["kind"] == "coarse-region" and x["region"] == grouped["aider"]["id"]
                   for x in plan["unresolved"])


def test_reordering_groups_does_not_change_membership(tmp_path):
    original = _nested_candidate(tmp_path / "one")
    reordered = _nested_candidate(tmp_path / "two", reverse=True)
    def mapping(ir):
        return {r["id"]: sorted(m["ref"] for m in r["members"])
                for r in ir["surfaces"][0]["interfaceIntent"]["regions"]}
    assert mapping(original) == mapping(reordered)
    def authority_records(ir, key):
        return [{k:v for k,v in record.items() if k != "source"} for record in ir[key]]
    assert authority_records(original,"intents") == authority_records(reordered,"intents")
    assert authority_records(original,"effects") == authority_records(reordered,"effects")
    assert authority_records(original,"capabilities") == authority_records(reordered,"capabilities")
    assert plan_workspace(reordered)["documentWorkbench"]["companion"] == plan_workspace(original)["documentWorkbench"]["companion"]


def test_nested_side_conflict_fails_closed(tmp_path):
    ir = _nested_candidate(tmp_path)
    wrong = copy.deepcopy(ir)
    nested = next(r for r in wrong["surfaces"][0]["interfaceIntent"]["regions"] if r["id"].endswith(".aider-session"))
    nested["preferences"] = {"side": "bottom"}
    with pytest.raises(ValueError, match="cannot independently prefer"):
        plan_workspace(wrong)
    plan = plan_workspace(ir)
    plan["nestedGroups"][0]["parentId"] = "interface-region:code-editor.navigation"
    with pytest.raises(ValueError, match="disagrees"):
        verify_workspace_plan(ir, plan)


@pytest.mark.parametrize("reverse", [False, True], ids=["authored-order", "reordered-groups"])
def test_browser_mount_preserves_operation_identity_across_nested_groups(tmp_path, reverse):
    ir = _nested_candidate(tmp_path, reverse=reverse)
    # The host's authenticated operation contracts are unchanged; only swap
    # in the proven paired-source IR to produce a disposable browser page.
    with patch("main_computer.mcel_semantic_other_apps_host.compile_semantic_app", return_value=ir):
        html = render_semantic_page("code-editor", ROOT, promoted=True)
    with sync_playwright() as playwright:
        browser, _ = launch_semantic_browser(playwright)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 860})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.set_content(html)
            assert page.locator("#mcel-workbench-status").inner_text() == "Semantic application ready"
            assert page.locator('[data-mcel-region$=".aider-preview"] [data-intent="intent:preview-aider-plan"]').count() == 1
            assert page.locator('[data-mcel-region$=".aider-execution"] [data-intent="intent:run-aider"]').count() == 1
            assert page.locator('[data-mcel-region$=".aider-session"] [data-intent="intent:reset-aider-context"]').count() == 1
            assert page.locator('[data-mcel-region$=".aider-session"] button').count() >= 3
            assert page.locator('[data-mcel-region$=".aider-session"] summary').inner_text() == "Session management"
            assert page.locator('[data-mcel-region$=".aider-execution"]').count() == 1
            assert page.locator("[data-mcel-document-workbench]").count() == 1
            assert not errors, errors
        finally:
            browser.close()
