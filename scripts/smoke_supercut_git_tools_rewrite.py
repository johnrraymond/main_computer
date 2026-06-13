#!/usr/bin/env python3
"""
MCEL Supercut Git Tools rewrite-planning smoke test.

Run from the repository root:

    python smoke_supercut_git_tools_rewrite.py --verbose

This does not patch source, click buttons, submit forms, run Git/Gitea actions,
or mutate runtime. It reads the real Git Tools HTML, builds a Supercut-like
blackboard, runs pluggable knowledge packs, and emits a rewrite-preview graph.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


SKIP_TAGS = {"script", "style", "template", "svg", "path", "meta", "link"}
COMPONENT_TAGS = {
    "section", "article", "header", "footer", "nav", "aside", "main",
    "form", "details", "summary", "label", "button", "a", "input",
    "select", "textarea", "output", "pre", "ul", "ol", "li",
}

PURPOSE_RULES = [
    ("project", "git-tools.project-selection", "project-selector"),
    ("wizard", "git-tools.guided-page-wizard", "guided-workflow"),
    ("patch", "git-tools.patch-inventory", "patch-workflow"),
    ("shim", "git-tools.control-shim", "shim-workflow"),
    ("console", "git-tools.manual-console", "command-console"),
    ("server", "git-tools.gitea-server-control", "server-control"),
    ("gitea", "git-tools.gitea-publish-workflow", "gitea-workflow"),
    ("remote", "git-tools.remote-configuration", "remote-config"),
    ("mirror", "git-tools.mirror-publication", "mirror-config"),
    ("push", "git-tools.repository-publication", "publish-action"),
    ("operation", "git-tools.operation-activity", "operation-feed"),
    ("activity", "git-tools.operation-activity", "operation-feed"),
    ("status", "git-tools.status-report", "status-output"),
    ("output", "git-tools.output-feed", "output-feed"),
    ("log", "git-tools.output-feed", "output-feed"),
    ("action", "git-tools.action-surface", "action-surface"),
]

RISK_RULES = [
    (r"(delete|remove|terminate|kill|shutdown|stop)\b", "destructive", "no-click"),
    (r"(restart|reset|cancel)\b", "operational", "no-click"),
    (r"\b(push|publish|mirror|sync)\b", "publication-mutation", "no-click"),
    (r"\b(remote|set-url|configure|apply)\b", "repo-config-mutation", "no-submit"),
    (r"\b(run|command|console|exec)\b", "command-execution", "no-command-execution"),
    (r"\b(start|unlock|lock)\b", "operational", "no-click"),
    (r"\b(refresh|inspect|plan|show|copy|preview|dry run)\b", "safe", "inspect-only"),
]
RISK_RULES = [(re.compile(p, re.I), r, policy) for p, r, policy in RISK_RULES]
BLOCKING_POLICIES = {"no-click", "no-submit", "no-command-execution"}


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug(value: Any, fallback: str = "component") -> str:
    out = re.sub(r"[^a-z0-9]+", "-", norm(value).lower()).strip("-")
    return (out[:96] or fallback)


def stable_hash(text: str) -> str:
    h = 5381
    for ch in text:
        h = ((h << 5) + h) ^ ord(ch)
    return format(h & 0xFFFFFFFF, "x")


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None
    text: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.attrs.get("id", "")

    @property
    def classes(self) -> list[str]:
        return [c for c in self.attrs.get("class", "").split() if c]

    def attr(self, name: str) -> str:
        return self.attrs.get(name, "")

    def append(self, child: "Node") -> None:
        child.parent = self
        self.children.append(child)

    def descendants(self) -> Iterable["Node"]:
        for child in self.children:
            yield child
            yield from child.descendants()

    def text_content(self, limit: int | None = None) -> str:
        chunks = list(self.text)
        for child in self.children:
            chunks.append(child.text_content())
        out = norm(" ".join(chunks))
        return out[:limit] if limit else out

    def label(self) -> str:
        return norm(
            self.attr("aria-label")
            or self.attr("title")
            or self.text_content(140)
            or self.attr("placeholder")
            or self.attr("name")
        )

    def signature(self) -> str:
        return norm(" ".join([
            self.tag,
            self.id,
            " ".join(self.classes),
            self.attr("data-mc-component-id"),
            self.attr("data-mc-widget-id"),
            self.attr("data-mc-component-kind"),
            self.attr("data-mc-component-label"),
            self.attr("aria-label"),
            self.label(),
        ]))

    def find_id(self, target: str) -> "Node | None":
        if self.id == target:
            return self
        for child in self.children:
            found = child.find_id(target)
            if found:
                return found
        return None


class FragmentParser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("__fragment__")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {k.lower(): (v or "") for k, v in attrs})
        self.stack[-1].append(node)
        if node.tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if norm(data):
            self.stack[-1].text.append(data)


@dataclass
class Record:
    node: Node
    index: int
    selector: str
    node_id: str
    tag: str
    text: str
    role: str = ""
    purpose: str = ""
    role_hint: str = ""
    contract: str = ""
    fit: str = ""
    risk: str = ""
    proof_policy: str = "inspect-only"
    confidence: float = 0.0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    laws: list[dict[str, Any]] = field(default_factory=list)
    rewrite: dict[str, Any] = field(default_factory=dict)

    def ev(self, rule: str, kind: str, value: Any, weight: float, note: str) -> None:
        self.evidence.append({"rule": rule, "kind": kind, "value": norm(value), "weight": weight, "note": note})
        self.confidence = min(1.0, self.confidence + weight)


@dataclass
class Blackboard:
    repo_root: Path
    source: Path
    source_hash: str
    root: Node
    records: list[Record] = field(default_factory=list)
    components: list[Record] = field(default_factory=list)
    regions: list[Record] = field(default_factory=list)
    actions: list[Record] = field(default_factory=list)
    rewrite_plan: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    runtime_mutations: list[str] = field(default_factory=list)
    packs: list[str] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class Pack:
    id = "pack"
    version = "0.1.0"
    phases: tuple[str, ...] = ()

    def run(self, phase: str, bb: Blackboard) -> None:
        fn = getattr(self, "phase_" + phase.replace("-", "_"), None)
        if fn:
            fn(bb)


def selector(node: Node, root: Node) -> str:
    if node.id:
        return f"#{node.id}"
    parts: list[str] = []
    cur: Node | None = node
    while cur and cur is not root and cur.tag != "__fragment__":
        if cur.id:
            parts.insert(0, f"{cur.tag}#{cur.id}")
            break
        cls = "." + ".".join(slug(c) for c in cur.classes[:2]) if cur.classes else ""
        nth = ""
        if cur.parent:
            same = [child for child in cur.parent.children if child.tag == cur.tag]
            if len(same) > 1:
                nth = f":nth-of-type({same.index(cur) + 1})"
        parts.insert(0, f"{cur.tag}{cls}{nth}")
        cur = cur.parent
    return " > ".join(parts) or f"#{root.id}"


def controls(node: Node) -> int:
    return sum(1 for d in node.descendants() if d.tag in {"button", "a", "input", "select", "textarea", "output"})


def heading(node: Node) -> str:
    cur: Node | None = node
    while cur:
        for child in cur.children:
            if child.tag in {"h1", "h2", "h3", "h4", "h5", "h6", "strong"} or "eyebrow" in child.classes:
                if child.label():
                    return child.label()
        cur = cur.parent
    return ""


def candidates(root: Node) -> list[Node]:
    out = []
    for node in [root, *list(root.descendants())]:
        if node.tag in SKIP_TAGS:
            continue
        if node is root or node.tag in COMPONENT_TAGS or node.id or node.classes or node.attr("data-mc-component-id"):
            out.append(node)
    return out


class CoreHtml(Pack):
    id = "core-html"
    phases = ("intake", "role-inference", "law-declaration")

    def phase_intake(self, bb: Blackboard) -> None:
        for i, node in enumerate(candidates(bb.root)):
            sel = selector(node, bb.root)
            legacy_id = node.attr("data-mc-component-id") or node.id or sel
            rec = Record(node, i, sel, f"supercut.{slug(legacy_id)}.{i}", node.tag, node.label())
            if node.attr("data-mc-component-id"):
                rec.ev(f"{self.id}.legacy-component", "attribute", node.attr("data-mc-component-id"), 0.25, "Existing MC metadata anchors legacy intent.")
            if node.id:
                rec.ev(f"{self.id}.id", "attribute", node.id, 0.12, "Stable id anchors a component boundary.")
            if rec.text:
                rec.ev(f"{self.id}.label", "text", rec.text, 0.08, "Visible/accessibility text gives purpose evidence.")
            bb.records.append(rec)
        bb.fired.append(f"{self.id}.intake")

    def phase_role_inference(self, bb: Blackboard) -> None:
        for rec in bb.records:
            n = rec.node
            sig = n.signature().lower()
            kind = n.attr("data-mc-component-kind")
            if n is bb.root:
                role = "app-root"
            elif kind:
                role = "legacy-" + slug(kind)
            elif n.tag in {"button", "a", "summary"} or n.attr("role") == "button":
                role = "action-component"
            elif n.tag in {"input", "select", "textarea"}:
                role = "field-control"
            elif n.tag == "label":
                role = "field-shell"
            elif n.tag == "form" or "form" in sig or controls(n) >= 4:
                role = "form-component"
            elif n.tag in {"pre", "output"} or re.search(r"(output|log|status|dashboard|report|feed|activity)", sig):
                role = "feed-component"
            elif n.tag in {"ul", "ol"} or re.search(r"(list|roster|inventory|archive)", sig):
                role = "collection-component"
            elif n.tag == "li":
                role = "collection-item"
            elif n.tag == "details" or re.search(r"(workflow|accordion|step|wizard)", sig):
                role = "workflow-component"
            elif re.search(r"(toolbar|actions|button-row)", sig):
                role = "toolbar-component"
            elif re.search(r"(shell|layout|grid|workspace|hero)", sig):
                role = "layout-component"
            elif re.search(r"(card|pane|panel|widget)", sig):
                role = "panel-component"
            else:
                role = "semantic-container" if len(n.children) >= 2 else "content-component"
            rec.role = role
            rec.ev(f"{self.id}.role", "role", role, 0.10, "Core HTML shape inferred a baseline role.")
        bb.fired.append(f"{self.id}.role-inference")

    def phase_law_declaration(self, bb: Blackboard) -> None:
        for rec in bb.records:
            if rec.role == "action-component":
                law = {"id": "action.must-have-visible-label", "nodeId": rec.node_id, "status": "pass" if rec.text else "fail"}
                rec.laws.append(law)
                if law["status"] != "pass":
                    bb.violations.append(law)
            if rec.role in {"field-control", "field-shell"}:
                law = {"id": "field.should-have-label-or-purpose", "nodeId": rec.node_id, "status": "pass" if rec.text or rec.node.attr("placeholder") else "warn"}
                rec.laws.append(law)
        bb.fired.append(f"{self.id}.law-declaration")


class GitToolsDomain(Pack):
    id = "git-tools-domain"
    phases = ("purpose-inference", "region-detection")

    def phase_purpose_inference(self, bb: Blackboard) -> None:
        for rec in bb.records:
            source = " ".join([rec.node.signature(), heading(rec.node), rec.text]).lower()
            for token, purpose, hint in PURPOSE_RULES:
                if token in source:
                    rec.purpose = purpose
                    rec.role_hint = hint
                    rec.ev(f"{self.id}.{slug(purpose)}", "keyword", token, 0.20, f"Git Tools token matched {purpose}.")
                    break
            if not rec.purpose:
                cid = rec.node.attr("data-mc-component-id")
                rec.purpose = f"component.{slug(cid)}" if cid else "legacy-html.unknown-purpose"
                rec.ev(f"{self.id}.fallback", "fallback", rec.purpose, 0.04, "Preserved legacy identity when no domain token matched.")
        bb.fired.append(f"{self.id}.purpose-inference")

    def phase_region_detection(self, bb: Blackboard) -> None:
        region_purposes = {
            "git-tools.project-selection", "git-tools.gitea-server-control",
            "git-tools.gitea-publish-workflow", "git-tools.operation-activity",
            "git-tools.remote-configuration", "git-tools.manual-console",
            "git-tools.status-report",
        }
        bb.regions = [
            r for r in bb.records
            if r.purpose in region_purposes and r.role not in {"action-component", "field-control", "field-shell"}
        ]
        for rec in bb.regions:
            rec.ev(f"{self.id}.region", "region", rec.purpose, 0.08, "Known Git Tools region/workflow boundary.")
        bb.fired.append(f"{self.id}.region-detection")


class ActionRisk(Pack):
    id = "core-action-risk"
    phases = ("risk-classification", "safety-audit")

    def phase_risk_classification(self, bb: Blackboard) -> None:
        for rec in bb.records:
            if rec.node.tag not in {"button", "a", "summary"} and rec.node.attr("role") != "button":
                continue
            source = " ".join([rec.purpose, rec.role, rec.selector, rec.node.id, " ".join(rec.node.classes), rec.text, heading(rec.node)])
            rec.risk, rec.proof_policy = "safe", "inspect-only"
            for pattern, risk, policy in RISK_RULES:
                if pattern.search(source):
                    rec.risk, rec.proof_policy = risk, policy
                    break
            rec.ev(f"{self.id}.{rec.risk}", "risk", rec.risk, 0.15, f"Action classified with proof policy {rec.proof_policy}.")
            bb.actions.append(rec)
        bb.fired.append(f"{self.id}.risk-classification")

    def phase_safety_audit(self, bb: Blackboard) -> None:
        for action in bb.actions:
            if action.risk != "safe" and action.proof_policy not in BLOCKING_POLICIES:
                bb.violations.append({"id": "dangerous-action.must-not-execute", "nodeId": action.node_id, "risk": action.risk})
        bb.fired.append(f"{self.id}.safety-audit")


class LayoutContracts(Pack):
    id = "mcel-layout-contracts"
    phases = ("contract-assignment", "layout-analysis", "rewrite-preview")

    def phase_contract_assignment(self, bb: Blackboard) -> None:
        for rec in bb.records:
            if rec.role == "app-root":
                contract = "component.root"
            elif rec.role == "action-component":
                contract = "component.action.operational" if rec.risk and rec.risk != "safe" else "component.action.safe"
            elif rec.role in {"field-control", "field-shell"}:
                contract = "component.field"
            elif "workflow" in rec.role or "gitea" in rec.purpose:
                contract = "component.workflow"
            elif "feed" in rec.role or "status" in rec.role or "output" in rec.role:
                contract = "component.status-feed"
            elif "collection" in rec.role or "list" in rec.role:
                contract = "component.collection"
            elif "toolbar" in rec.role:
                contract = "component.toolbar"
            elif "layout" in rec.role:
                contract = "component.layout"
            elif "panel" in rec.role:
                contract = "component.panel"
            else:
                contract = "component.semantic"
            rec.contract = contract
            rec.ev(f"{self.id}.contract", "contract", contract, 0.10, "Assigned rewrite-preview component contract.")
        bb.components = list(bb.records)
        bb.fired.append(f"{self.id}.contract-assignment")

    def phase_layout_analysis(self, bb: Blackboard) -> None:
        for rec in bb.records:
            sig = rec.node.signature().lower()
            if rec.role == "app-root" or re.search(r"(shell|workspace|layout)", sig):
                fit = "runtime-shell"
            elif "toolbar" in rec.role or re.search(r"(actions|toolbar|button-row)", sig):
                fit = "toolbar-wrap"
            elif "field" in rec.role or re.search(r"(fields|composer|settings|form)", sig):
                fit = "field-grid"
            elif "feed" in rec.role or rec.node.tag in {"pre", "output"} or re.search(r"(output|log|dashboard|report|activity)", sig):
                fit = "scroll-feed"
            elif "workflow" in rec.role or re.search(r"(accordion|workflow|step|wizard)", sig):
                fit = "workflow-stack"
            elif "collection" in rec.role:
                fit = "bounded-collection"
            elif rec.role == "action-component":
                fit = "risk-action" if rec.risk and rec.risk != "safe" else "inline-action"
            elif "panel" in rec.role:
                fit = "responsive-panel"
            else:
                fit = "responsive-component"
            rec.fit = fit
            rec.ev(f"{self.id}.fit", "layout-fit", fit, 0.06, "Derived CSS object fit policy.")
        bb.fired.append(f"{self.id}.layout-analysis")

    def phase_rewrite_preview(self, bb: Blackboard) -> None:
        tags = {
            "component.root": "mcel-app",
            "component.workflow": "mcel-workflow",
            "component.field": "mcel-field",
            "component.status-feed": "mcel-output",
            "component.collection": "mcel-collection",
            "component.toolbar": "mcel-toolbar",
            "component.layout": "mcel-layout",
            "component.panel": "mcel-panel",
        }
        for rec in bb.records:
            tag = "mcel-action" if rec.contract.startswith("component.action") else tags.get(rec.contract, "mcel-component")
            rec.rewrite = {
                "sourceSelector": rec.selector,
                "sourceTag": rec.tag,
                "targetTag": tag,
                "componentId": rec.node_id,
                "purpose": rec.purpose,
                "contract": rec.contract,
                "fit": rec.fit,
                "risk": rec.risk or "none",
                "proofPolicy": rec.proof_policy,
                "label": rec.text[:120],
                "evidenceCount": len(rec.evidence),
                "action": "preview-only-no-source-mutation",
            }
            bb.rewrite_plan.append(rec.rewrite)
        bb.fired.append(f"{self.id}.rewrite-preview")


class Explain(Pack):
    id = "mcel-explainability"
    phases = ("explanation-build",)

    def phase_explanation_build(self, bb: Blackboard) -> None:
        for rec in bb.records:
            top = sorted(rec.evidence, key=lambda item: item["weight"], reverse=True)[:5]
            rec.rewrite["explanation"] = [f"{e['kind']}={e['value']} ({e['note']})" for e in top]
        bb.fired.append(f"{self.id}.explanation-build")


PHASES = [
    "intake", "role-inference", "purpose-inference", "region-detection",
    "risk-classification", "contract-assignment", "layout-analysis",
    "law-declaration", "safety-audit", "rewrite-preview", "explanation-build",
]
PACKS = [CoreHtml(), GitToolsDomain(), ActionRisk(), LayoutContracts(), Explain()]


def parse_fragment(path: Path) -> Node:
    parser = FragmentParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return parser.root


def load_blackboard(repo_root: Path) -> Blackboard:
    source = repo_root / "main_computer" / "web" / "applications" / "apps" / "git-tools.html"
    supercut_js = repo_root / "main_computer" / "web" / "applications" / "scripts" / "mcel-supercut.js"
    git_mcel_js = repo_root / "main_computer" / "web" / "applications" / "scripts" / "git-tools-mcel.js"
    for path in [source, supercut_js, git_mcel_js]:
        if not path.exists():
            raise SystemExit(f"Missing required file: {path}")

    supercut_source = supercut_js.read_text(encoding="utf-8")
    git_mcel_source = git_mcel_js.read_text(encoding="utf-8")
    integration_failures = []
    if "global.McelSupercut" not in supercut_source:
        integration_failures.append("mcel-supercut.js must expose global.McelSupercut")
    if "translateRuntime" not in supercut_source:
        integration_failures.append("mcel-supercut.js must expose translateRuntime")
    if "McelSupercut.translateRuntime" not in git_mcel_source:
        integration_failures.append("git-tools-mcel.js must call McelSupercut.translateRuntime")
    if integration_failures:
        raise SystemExit("\n".join(integration_failures))

    text = source.read_text(encoding="utf-8")
    root = parse_fragment(source).find_id("git-tools-app")
    if root is None:
        raise SystemExit("Could not find #git-tools-app in git-tools.html")
    return Blackboard(repo_root=repo_root, source=source, source_hash=stable_hash(text), root=root)


def run(repo_root: Path) -> Blackboard:
    bb = load_blackboard(repo_root)
    bb.packs = [f"{p.id}@{p.version}" for p in PACKS]
    for phase in PHASES:
        for pack in PACKS:
            if phase in pack.phases:
                pack.run(phase, bb)

    original_points = sorted({r.purpose for r in bb.records})
    risks: dict[str, int] = {}
    contracts: dict[str, int] = {}
    fits: dict[str, int] = {}
    for rec in bb.records:
        risks[rec.risk or "none"] = risks.get(rec.risk or "none", 0) + 1
        contracts[rec.contract or "none"] = contracts.get(rec.contract or "none", 0) + 1
        fits[rec.fit or "none"] = fits.get(rec.fit or "none", 0) + 1

    bb.metrics = {
        "sessionId": "supercut-smoke-" + dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S"),
        "sourceHtml": str(bb.source.relative_to(bb.repo_root)),
        "sourceHash": bb.source_hash,
        "packsLoaded": len(bb.packs),
        "rulesFired": len(bb.fired),
        "components": len(bb.components),
        "regions": len(bb.regions),
        "actions": len(bb.actions),
        "unsafeActionsBlocked": sum(1 for a in bb.actions if a.risk != "safe" and a.proof_policy in BLOCKING_POLICIES),
        "originalPoints": len(original_points),
        "originalPointValues": original_points,
        "rewritePreviewNodes": len(bb.rewrite_plan),
        "violations": len(bb.violations),
        "runtimeSourceMutations": len(bb.runtime_mutations),
        "riskCounts": risks,
        "contractCounts": contracts,
        "fitCounts": fits,
    }
    return bb


def preview_html(bb: Blackboard, limit: int = 180) -> str:
    body = []
    for item in bb.rewrite_plan[:limit]:
        attrs = {
            "data-mcel-supercut-preview": "true",
            "data-mcel-supercut-source": item["sourceSelector"],
            "data-mcel-supercut-purpose": item["purpose"],
            "data-mcel-supercut-contract": item["contract"],
            "data-mcel-supercut-risk": item["risk"],
            "data-mcel-supercut-proof-policy": item["proofPolicy"],
        }
        attr_text = " ".join(f'{k}="{html.escape(str(v), quote=True)}"' for k, v in attrs.items())
        label = html.escape(item["label"] or item["sourceSelector"])
        explanation = html.escape("; ".join(item.get("explanation", [])[:2]))
        body.append(
            f'  <{item["targetTag"]} {attr_text}>\n'
            f"    <span>{label}</span>\n"
            f'    <small data-mcel-supercut-explanation="{explanation}">{html.escape(item["contract"])} · {html.escape(item["fit"])}</small>\n'
            f'  </{item["targetTag"]}>'
        )
    return """<!doctype html>
<meta charset="utf-8">
<title>MCEL Supercut Git Tools Rewrite Preview Smoke</title>
<style>
body { font-family: system-ui, sans-serif; margin: 24px; line-height: 1.35; }
mcel-app, mcel-workflow, mcel-panel, mcel-layout, mcel-toolbar, mcel-action,
mcel-field, mcel-output, mcel-collection, mcel-component {
  display: block; border: 1px solid #ccd; border-radius: 10px; margin: 8px 0; padding: 10px;
}
mcel-action { display: inline-block; margin-right: 8px; }
[data-mcel-supercut-risk]:not([data-mcel-supercut-risk="none"]):not([data-mcel-supercut-risk="safe"]) { border-style: dashed; }
small { display: block; opacity: 0.72; margin-top: 4px; }
</style>
<h1>MCEL Supercut Git Tools Rewrite Preview Smoke</h1>
<p>Preview only: no source changes, no button clicks, no submits, no Git/Gitea actions.</p>
<section>
""" + "\n".join(body) + "\n</section>\n"


def write_outputs(bb: Blackboard, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "git_tools_supercut_rewrite_smoke.json"
    html_path = out_dir / "git_tools_supercut_rewrite_preview.html"
    payload = {
        "status": "pass" if not bb.violations else "fail",
        "createdAt": dt.datetime.now(dt.UTC).isoformat(),
        "mode": "rewrite-preview-smoke-no-source-mutation",
        "metrics": bb.metrics,
        "packsLoaded": bb.packs,
        "rulesFired": bb.fired,
        "violations": bb.violations,
        "sampleComponents": bb.rewrite_plan[:40],
        "actions": [
            {
                "selector": a.selector,
                "label": a.text,
                "risk": a.risk,
                "proofPolicy": a.proof_policy,
                "purpose": a.purpose,
                "contract": a.contract,
            }
            for a in bb.actions
        ],
        "rewritePlan": bb.rewrite_plan,
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    html_path.write_text(preview_html(bb), encoding="utf-8")
    return json_path, html_path


def failures(bb: Blackboard, min_components: int, min_actions: int, min_purposes: int) -> list[str]:
    m = bb.metrics
    out = []
    if m["components"] < min_components:
        out.append(f"Expected at least {min_components} components; found {m['components']}.")
    if m["actions"] < min_actions:
        out.append(f"Expected at least {min_actions} actions; found {m['actions']}.")
    if m["originalPoints"] < min_purposes:
        out.append(f"Expected at least {min_purposes} purpose buckets; found {m['originalPoints']}.")
    if m["rewritePreviewNodes"] != m["components"]:
        out.append("Rewrite preview did not cover every component.")
    if m["runtimeSourceMutations"] != 0:
        out.append("Smoke recorded runtime/source mutations; this must remain preview-only.")
    required = {
        "git-tools.project-selection",
        "git-tools.gitea-server-control",
        "git-tools.gitea-publish-workflow",
        "git-tools.remote-configuration",
        "git-tools.manual-console",
    }
    missing = sorted(required - set(m["originalPointValues"]))
    if missing:
        out.append("Missing required purpose buckets: " + ", ".join(missing))
    risky_unblocked = [
        a.selector for a in bb.actions
        if a.risk not in {"", "safe"} and a.proof_policy not in BLOCKING_POLICIES
    ]
    if risky_unblocked:
        out.append("Risky actions without blocking proof policy: " + ", ".join(risky_unblocked[:8]))
    out.extend(f"Violation: {v}" for v in bb.violations)
    return out


def report(bb: Blackboard, json_path: Path, html_path: Path, fail: list[str], verbose: bool) -> None:
    m = bb.metrics
    print("\nMCEL Supercut Git Tools rewrite smoke")
    print("=" * 48)
    print(f"status: {'PASS' if not fail else 'FAIL'}")
    print(f"source: {m['sourceHtml']} hash={m['sourceHash']}")
    print(f"packs: {', '.join(bb.packs)}")
    print(f"rules fired: {m['rulesFired']}")
    print(f"components: {m['components']}")
    print(f"regions: {m['regions']}")
    print(f"actions classified: {m['actions']}")
    print(f"unsafe actions blocked: {m['unsafeActionsBlocked']}")
    print(f"original-purpose buckets: {m['originalPoints']}")
    print(f"rewrite-preview nodes: {m['rewritePreviewNodes']}")
    print(f"violations: {m['violations']}")
    print(f"runtime/source mutations: {m['runtimeSourceMutations']}")
    print(f"json: {json_path}")
    print(f"preview: {html_path}")

    print("\nTop original-purpose buckets:")
    for purpose in m["originalPointValues"][:20]:
        print(f"  - {purpose}")

    print("\nRisk counts:")
    for risk, count in sorted(m["riskCounts"].items()):
        print(f"  - {risk}: {count}")

    if verbose:
        print("\nSample rewrite-preview components:")
        for item in bb.rewrite_plan[:12]:
            print(f"  - {item['targetTag']} {item['sourceSelector']} -> {item['contract']} [{item['purpose']}] risk={item['risk']}")

    if fail:
        print("\nFailures:")
        for item in fail:
            print(f"  - {item}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("supercut_smoke_output"))
    parser.add_argument("--min-components", type=int, default=100)
    parser.add_argument("--min-actions", type=int, default=20)
    parser.add_argument("--min-original-points", type=int, default=8)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    bb = run(repo_root)
    out_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    json_path, html_path = write_outputs(bb, out_dir)
    fail = failures(bb, args.min_components, args.min_actions, args.min_original_points)
    report(bb, json_path, html_path, fail, args.verbose)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
