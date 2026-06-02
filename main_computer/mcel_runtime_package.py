"""Build the single-file MCEL page runtime used by Website Builder exports.

The generated runtime intentionally reuses the MCEL compiler/law modules from the
Lab without copying the Lab application UI, state bindings, scenario runners, or
diagnostics panels. It is deterministic so tests can prove that the checked-in
``deploy/local-platform/site-runtimes/mcel-runtime.js`` file is current.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MCEL_RUNTIME_VERSION = "mcel-runtime.v0.1.1"

MCEL_RUNTIME_MODULES: tuple[str, ...] = (
    "main_computer/web/applications/scripts/mcel-contract.js",
    "main_computer/web/applications/scripts/mcel-engine.js",
    "main_computer/web/applications/scripts/mcel-law-registry.js",
    "main_computer/web/applications/scripts/mcel-editor.js",
    "main_computer/web/applications/scripts/mcel-style-law.js",
    "main_computer/web/applications/scripts/mcel-browser-observer.js",
    "main_computer/web/applications/scripts/mcel-layout-law.js",
    "main_computer/web/applications/scripts/mcel-component-law.js",
    "main_computer/web/applications/scripts/mcel-state-law.js",
    "main_computer/web/applications/scripts/mcel-data-law.js",
    "main_computer/web/applications/scripts/mcel-form-law.js",
    "main_computer/web/applications/scripts/mcel-action-law.js",
    "main_computer/web/applications/scripts/mcel-render-law.js",
    "main_computer/web/applications/scripts/mcel-a11y-law.js",
    "main_computer/web/applications/scripts/mcel-performance-law.js",
    "main_computer/web/applications/scripts/mcel-platform-spine.js",
    "main_computer/web/applications/scripts/mcel-chrome-law.js",
)

MCEL_LAB_HELPER_FILE = "main_computer/web/applications/scripts/mcel-lab.js"
MCEL_LAB_HELPER_FUNCTIONS: tuple[str, ...] = ("isolatedSiteCss",)

DEFAULT_MCEL_RUNTIME_OUTPUT = "deploy/local-platform/site-runtimes/mcel-runtime.js"


@dataclass(frozen=True)
class McelRuntimePackageResult:
    """Description of a generated MCEL runtime artifact."""

    output_path: Path
    version: str
    source_files: tuple[str, ...]
    helper_functions: tuple[str, ...]
    size_bytes: int


class JavascriptExtractionError(ValueError):
    """Raised when a JavaScript helper cannot be extracted safely."""


def _skip_string(source: str, index: int, quote: str) -> int:
    """Return the first index after a quoted JavaScript string/template literal."""

    index += 1
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    raise JavascriptExtractionError("Unterminated JavaScript string while extracting helper function.")


def _skip_line_comment(source: str, index: int) -> int:
    end = source.find("\n", index + 2)
    return len(source) if end == -1 else end + 1


def _skip_block_comment(source: str, index: int) -> int:
    end = source.find("*/", index + 2)
    if end == -1:
        raise JavascriptExtractionError("Unterminated JavaScript block comment while extracting helper function.")
    return end + 2


def extract_javascript_function(source: str, function_name: str) -> str:
    """Extract a named JavaScript function while ignoring braces inside strings.

    The MCEL Lab CSS helper is a large template literal containing many CSS
    braces, so a simple ``str.find("}")`` would be unsafe. This scanner is
    intentionally small, but it handles the string/comment cases needed by the
    Lab helper source.
    """

    marker = f"function {function_name}"
    start = source.find(marker)
    if start == -1:
        raise JavascriptExtractionError(f"Could not find JavaScript function {function_name!r}.")
    brace = source.find("{", start)
    if brace == -1:
        raise JavascriptExtractionError(f"Could not find body for JavaScript function {function_name!r}.")

    index = brace
    depth = 0
    while index < len(source):
        char = source[index]
        next_pair = source[index : index + 2]
        if next_pair == "//":
            index = _skip_line_comment(source, index)
            continue
        if next_pair == "/*":
            index = _skip_block_comment(source, index)
            continue
        if char in ("'", '"', "`"):
            index = _skip_string(source, index, char)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1].strip()
        index += 1

    raise JavascriptExtractionError(f"Could not find end of JavaScript function {function_name!r}.")


def _read_repo_file(repo_root: Path, relative_path: str) -> str:
    path = repo_root / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Required MCEL runtime source is missing: {relative_path}")
    return path.read_text(encoding="utf-8").strip()


def build_mcel_runtime_text(repo_root: Path) -> str:
    """Return the deterministic single-file MCEL runtime JavaScript payload."""

    repo_root = Path(repo_root)
    modules = [(relative_path, _read_repo_file(repo_root, relative_path)) for relative_path in MCEL_RUNTIME_MODULES]
    lab_source = _read_repo_file(repo_root, MCEL_LAB_HELPER_FILE)
    helpers = [(name, extract_javascript_function(lab_source, name)) for name in MCEL_LAB_HELPER_FUNCTIONS]
    source_manifest = "\n".join(f" * - {relative_path}" for relative_path, _ in modules)
    helper_manifest = "\n".join(f" * - {MCEL_LAB_HELPER_FILE}::{name}()" for name, _ in helpers)

    sections: list[str] = [
        "/*",
        f" * Main Computer MCEL Runtime ({MCEL_RUNTIME_VERSION})",
        " * Generated by main_computer.mcel_runtime_package.",
        " *",
        " * Compiler/law sources:",
        source_manifest,
        " *",
        " * Lab helpers:",
        helper_manifest,
        " */",
        "(function (global) {",
        '  "use strict";',
        "  if (!global) return;",
        "  const window = global;",
        "",
    ]
    for relative_path, source in modules:
        sections.append(f"  // BEGIN {relative_path}")
        sections.append(source)
        sections.append(f"  // END {relative_path}")
        sections.append("")
    for name, helper in helpers:
        sections.append(f"  // BEGIN {MCEL_LAB_HELPER_FILE}::{name}")
        sections.append(helper)
        sections.append(f"  // END {MCEL_LAB_HELPER_FILE}::{name}")
        sections.append("")

    sections.append(_MCEL_RUNTIME_WRAPPER.strip())
    sections.append('})(typeof window !== "undefined" ? window : (typeof globalThis !== "undefined" ? globalThis : null));')
    return "\n".join(sections) + "\n"


def package_mcel_runtime(
    repo_root: Path,
    output_path: Path | str | None = None,
) -> McelRuntimePackageResult:
    """Write the MCEL runtime bundle and return package metadata."""

    repo_root = Path(repo_root)
    target = repo_root / (output_path or DEFAULT_MCEL_RUNTIME_OUTPUT)
    runtime_text = build_mcel_runtime_text(repo_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(runtime_text, encoding="utf-8")
    return McelRuntimePackageResult(
        output_path=target,
        version=MCEL_RUNTIME_VERSION,
        source_files=MCEL_RUNTIME_MODULES + (MCEL_LAB_HELPER_FILE,),
        helper_functions=MCEL_LAB_HELPER_FUNCTIONS,
        size_bytes=len(runtime_text.encode("utf-8")),
    )


_MCEL_RUNTIME_WRAPPER = r'''
  const mcelRuntimeVersion = "mcel-runtime.v0.1.1";
  const runtimeEntry = "runtime.js";
  const runtimeDefaults = Object.freeze({
    theme: "theme-machine",
    chrome: "chrome-strict-hierarchy",
    applyLaws: true,
    applyChrome: true
  });

  function mcelRuntimeModule(name) {
    return window[name] || null;
  }

  function mcelRuntimeContract() {
    return mcelRuntimeModule("McelLabContract");
  }

  function mcelRuntimeEngine() {
    return mcelRuntimeModule("McelLabEngine");
  }

  function mcelRuntimeEditor() {
    return mcelRuntimeModule("McelLabEditor");
  }

  function mcelRuntimeStyleLaw() {
    return mcelRuntimeModule("McelLabStyleLaw");
  }

  function mcelRuntimeChromeLaw() {
    return mcelRuntimeModule("McelLabChromeLaw");
  }

  function mcelRuntimeLayoutLaw() {
    return mcelRuntimeModule("McelLabLayoutLaw");
  }

  function mcelRuntimePlatformSpine() {
    return mcelRuntimeModule("McelLabPlatformSpine");
  }

  function mcelRuntimeRoot(html) {
    const root = window.document?.createElement ? window.document.createElement("div") : null;
    if (root) root.innerHTML = String(html || "");
    return root;
  }

  function mcelRuntimeNodeRoot(root) {
    if (!root) return null;
    if (root.nodeType === 9) return root.body || root.documentElement || null;
    return root;
  }

  const runtimeStyleAttribute = "data-mcel-runtime-style";
  const runtimeHydratedAttribute = "data-mcel-runtime-hydrated";

  function mcelRuntimeSourceHtml(root) {
    const target = mcelRuntimeNodeRoot(root);
    if (!target) return "";
    if (typeof target.innerHTML === "string") return target.innerHTML;
    return String(target.textContent || "");
  }

  function mcelRuntimeSourceAttribute() {
    const contract = mcelRuntimeContract();
    return contract?.attributes?.type || "data-mc";
  }

  function mcelRuntimeSourceSelector() {
    return `[${mcelRuntimeSourceAttribute()}]`;
  }

  function mcelRuntimeElementMatches(element, selector) {
    return Boolean(element?.matches?.(selector));
  }

  function mcelRuntimeElementHtml(element) {
    if (!element) return "";
    if (typeof element.outerHTML === "string") return element.outerHTML;
    const wrapper = element.ownerDocument?.createElement?.("div");
    if (!wrapper) return String(element.textContent || "");
    wrapper.appendChild(element.cloneNode(true));
    return wrapper.innerHTML;
  }

  function mcelRuntimeSourceIslands(root) {
    const target = mcelRuntimeNodeRoot(root);
    if (!target) return [];
    const sourceSelector = mcelRuntimeSourceSelector();
    const hydratedSelector = `[${runtimeHydratedAttribute}="true"]`;
    const sources = [];

    if (mcelRuntimeElementMatches(target, sourceSelector)) sources.push(target);
    target.querySelectorAll?.(sourceSelector).forEach((node) => sources.push(node));

    return sources.filter((node, index) => {
      if (!node || sources.indexOf(node) !== index) return false;
      if (mcelRuntimeElementMatches(node, hydratedSelector) || node.closest?.(hydratedSelector)) return false;

      let parent = node.parentElement;
      while (parent && parent !== target.parentElement) {
        if (parent !== node && mcelRuntimeElementMatches(parent, sourceSelector)) return false;
        if (parent === target) break;
        parent = parent.parentElement;
      }
      return true;
    });
  }

  function mcelRuntimeHasSource(root) {
    return mcelRuntimeSourceIslands(root).length > 0;
  }

  function mcelRuntimeDocumentFor(root) {
    if (root?.nodeType === 9) return root;
    const target = mcelRuntimeNodeRoot(root);
    return target?.ownerDocument || window.document || null;
  }

  function mcelRuntimeMarkReady(root, result = {}) {
    const doc = mcelRuntimeDocumentFor(root);
    const target = mcelRuntimeNodeRoot(root);
    const body = doc?.body || (target?.tagName === "BODY" ? target : null);
    const html = doc?.documentElement || null;
    const changed = result.changed === true;
    const sourceCount = Number(result.sourceCount || 0);

    [html, body].forEach((element) => {
      if (!element?.dataset) return;
      element.dataset.mcelRuntime = "mcel";
      element.dataset.mcelRuntimeVersion = mcelRuntimeVersion;
      element.dataset.mcelRuntimeReady = "true";
      element.dataset.mcelRuntimeChanged = changed ? "true" : "false";
      element.dataset.mcelRuntimeSourceCount = String(sourceCount);
    });

    if (body?.classList) {
      body.classList.add("mcel-runtime-ready");
      body.classList.toggle("mcel-runtime-active", changed);
    }
  }

  function mcelRuntimeEnsureStyle(doc) {
    const targetDoc = doc || window.document || null;
    if (!targetDoc?.head?.appendChild || targetDoc.head.querySelector?.(`[${runtimeStyleAttribute}]`)) return false;
    const style = targetDoc.createElement("style");
    style.setAttribute(runtimeStyleAttribute, mcelRuntimeVersion);
    style.textContent = isolatedSiteCss();
    targetDoc.head.appendChild(style);
    return true;
  }

  function mcelRuntimeMarkHydrated(nodes) {
    const sourceSelector = mcelRuntimeSourceSelector();
    nodes.forEach((node) => {
      if (!node || node.nodeType !== 1) return;
      if (mcelRuntimeElementMatches(node, sourceSelector)) {
        node.setAttribute(runtimeHydratedAttribute, "true");
      }
      node.querySelectorAll?.(sourceSelector).forEach((child) => {
        child.setAttribute(runtimeHydratedAttribute, "true");
      });
    });
  }

  function mcelRuntimeReplaceElement(element, html) {
    const doc = element?.ownerDocument || window.document || null;
    if (!element || !doc?.createElement) return [];
    const template = doc.createElement("template");
    template.innerHTML = String(html || "").trim();
    const nodes = Array.from(template.content?.childNodes || []);
    if (!nodes.length) {
      element.remove?.();
      return [];
    }
    if (element.replaceWith) {
      element.replaceWith(...nodes);
    } else if (element.parentNode) {
      nodes.forEach((node) => element.parentNode.insertBefore(node, element));
      element.parentNode.removeChild(element);
    }
    mcelRuntimeMarkHydrated(nodes);
    return nodes;
  }

  function mcelRuntimeNormalizeTheme(theme) {
    const styleLaw = mcelRuntimeStyleLaw();
    return styleLaw?.normalizeTheme ? styleLaw.normalizeTheme(theme || runtimeDefaults.theme) : runtimeDefaults.theme;
  }

  function mcelRuntimeNormalizeChrome(chrome) {
    const chromeLaw = mcelRuntimeChromeLaw();
    return chromeLaw?.normalizeChrome ? chromeLaw.normalizeChrome(chrome || runtimeDefaults.chrome) : runtimeDefaults.chrome;
  }

  function mcelRuntimeOptions(options = {}) {
    return {
      ...runtimeDefaults,
      ...options,
      reason: options.reason || "mcel-runtime",
      theme: mcelRuntimeNormalizeTheme(options.theme),
      chrome: mcelRuntimeNormalizeChrome(options.chrome)
    };
  }

  function mcelRuntimeUnavailableResult(operation) {
    return {
      ok: false,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      operation,
      error: "MCEL runtime modules are unavailable."
    };
  }

  function mcelRuntimeCompile(sourceHtml, options = {}) {
    const engine = mcelRuntimeEngine();
    const contract = mcelRuntimeContract();
    const editor = mcelRuntimeEditor();
    if (!engine?.compileSource || !contract) return mcelRuntimeUnavailableResult("compile");

    const opts = mcelRuntimeOptions(options);
    const defaultSource = options.useDefaultSource === true ? contract.defaultSource : "";
    const source = editor?.canonicalSource
      ? editor.canonicalSource(String(sourceHtml || defaultSource))
      : String(sourceHtml || defaultSource);
    const compiled = engine.compileSource(source, {reason: opts.reason});
    const root = mcelRuntimeRoot(compiled.runtimeHtml);
    const laws = {
      cssLaw: null,
      layoutLaw: null,
      platform: null
    };

    if (root && opts.applyLaws !== false) {
      const styleLaw = mcelRuntimeStyleLaw();
      const layoutLaw = mcelRuntimeLayoutLaw();
      const platformSpine = mcelRuntimePlatformSpine();
      laws.cssLaw = styleLaw?.applyRuntimeLaw ? styleLaw.applyRuntimeLaw(root, {theme: opts.theme, reason: opts.reason}) : null;
      laws.layoutLaw = layoutLaw?.applyRuntimeLaw ? layoutLaw.applyRuntimeLaw(root, {reason: opts.reason}) : null;
      laws.platform = platformSpine?.applyPlatformLaws ? platformSpine.applyPlatformLaws(root, {reason: opts.reason}) : null;
    }

    const lawHtml = root?.innerHTML?.trim() || compiled.runtimeHtml || "";
    const chrome = opts.applyChrome === false ? null : mcelRuntimeApplyChrome(lawHtml, opts);
    return {
      ok: true,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      contractVersion: contract.contractVersion,
      sourceHtml: source,
      runtimeHtml: chrome?.html || lawHtml,
      runtimeHtmlBeforeChrome: lawHtml,
      sourceCount: compiled.sourceCount || 0,
      events: compiled.events || [],
      laws,
      chrome: chrome?.report || null
    };
  }

  function mcelRuntimeTransform(sourceHtml, options = {}) {
    return mcelRuntimeCompile(sourceHtml, options).runtimeHtml || String(sourceHtml || "");
  }

  function mcelRuntimeSerialize(runtimeRootOrHtml, options = {}) {
    const engine = mcelRuntimeEngine();
    if (!engine?.serializeRuntimeRoot) return mcelRuntimeUnavailableResult("serialize");
    const root = typeof runtimeRootOrHtml === "string" ? mcelRuntimeRoot(runtimeRootOrHtml) : mcelRuntimeNodeRoot(runtimeRootOrHtml);
    return engine.serializeRuntimeRoot(root, {reason: options.reason || "mcel-runtime:serialize"});
  }

  function mcelRuntimeRepair(runtimeRootOrHtml, options = {}) {
    const engine = mcelRuntimeEngine();
    if (!engine?.repairRuntimeRoot) return mcelRuntimeUnavailableResult("repair");
    const root = typeof runtimeRootOrHtml === "string" ? mcelRuntimeRoot(runtimeRootOrHtml) : mcelRuntimeNodeRoot(runtimeRootOrHtml);
    const generatedRepair = engine.repairRuntimeRoot(root, {reason: options.reason || "mcel-runtime:repair"});
    const layoutLaw = mcelRuntimeLayoutLaw();
    const layoutRepair = layoutLaw?.repairRuntimeLaw ? layoutLaw.repairRuntimeLaw(root, {reason: options.reason || "mcel-runtime:repair"}) : null;
    return {
      ok: true,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      generatedRepair,
      layoutRepair,
      runtimeHtml: root?.innerHTML || ""
    };
  }

  function mcelRuntimeAudit(sourceHtml, runtimeRootOrHtml = null, options = {}) {
    const runtimeRoot = typeof runtimeRootOrHtml === "string" ? mcelRuntimeRoot(runtimeRootOrHtml) : mcelRuntimeNodeRoot(runtimeRootOrHtml);
    const contract = mcelRuntimeContract();
    const layoutLaw = mcelRuntimeLayoutLaw();
    const platformSpine = mcelRuntimePlatformSpine();
    const registry = mcelRuntimeModule("McelLabLawRegistry");
    const layoutProof = runtimeRoot && layoutLaw?.proveRuntime ? layoutLaw.proveRuntime(runtimeRoot, {reason: options.reason || "mcel-runtime:audit"}) : null;
    const platformProof = runtimeRoot && platformSpine?.provePlatform ? platformSpine.provePlatform(runtimeRoot, {reason: options.reason || "mcel-runtime:audit"}) : null;
    const lawProof = runtimeRoot && registry?.prove ? registry.prove(runtimeRoot, {reason: options.reason || "mcel-runtime:audit"}) : null;
    return {
      ok: true,
      kind: "mcel-runtime-audit",
      runtime: "mcel",
      version: mcelRuntimeVersion,
      contractVersion: contract?.contractVersion || null,
      sourceLength: String(sourceHtml || "").length,
      runtimeLength: runtimeRoot?.innerHTML?.length || 0,
      layoutProof,
      platformProof,
      lawProof,
      failed: Boolean(layoutProof?.failed || platformProof?.failed || lawProof?.failed)
    };
  }

  function mcelRuntimeApplyChrome(runtimeHtml, options = {}) {
    const chromeLaw = mcelRuntimeChromeLaw();
    const opts = mcelRuntimeOptions(options);
    const html = String(runtimeHtml || "");
    if (!chromeLaw?.applyChromeHtml) {
      return {
        html,
        report: {
          kind: "mcel-chrome-report",
          contractVersion: null,
          chrome: opts.chrome,
          changed: false,
          generatedContainers: 0,
          movedSourceElements: 0,
          warnings: ["mcel-runtime:chrome-law-unavailable"]
        }
      };
    }
    return chromeLaw.applyChromeHtml(html, {theme: opts.theme, chrome: opts.chrome, reason: opts.reason});
  }

  function mcelRuntimeEscapeMeta(value, fallback = "") {
    return String(value || fallback).replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function mcelRuntimeRenderDocument(runtimeHtml, options = {}) {
    const opts = mcelRuntimeOptions(options);
    const reason = mcelRuntimeEscapeMeta(opts.reason, "render-document");
    const nonce = mcelRuntimeEscapeMeta(options.nonce, "0");
    const hash = mcelRuntimeEscapeMeta(options.hash, "none");
    const chromeResult = opts.applyChrome === false
      ? {html: String(runtimeHtml || ""), report: {chrome: opts.chrome, changed: false}}
      : mcelRuntimeApplyChrome(runtimeHtml, opts);
    return `<!doctype html>
<html data-mcel-frame-generation="${nonce}" data-mcel-frame-reason="${reason}" data-mcel-frame-hash="${hash}" data-mcel-theme="${opts.theme}" data-mcel-chrome="${opts.chrome}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${mcelRuntimeEscapeMeta(options.title, "MCEL rendered site")}</title>
<style>${isolatedSiteCss()}</style>
</head>
<body class="mcel-site-theme ${opts.theme}" data-mcel-chrome="${opts.chrome}">
  <!-- MCEL runtime render: reason=${reason}; nonce=${nonce}; hash=${hash}; theme=${opts.theme}; chrome=${opts.chrome} -->
  <div class="mcel-runtime-preview ${opts.theme}" data-mcel-theme="${opts.theme}" data-mcel-chrome="${opts.chrome}">
    ${chromeResult.html || ""}
  </div>
</body>
</html>`;
  }

  function mcelRuntimeMountPreview(target, sourceHtml, options = {}) {
    if (!target) {
      return {
        ok: false,
        runtime: "mcel",
        version: mcelRuntimeVersion,
        error: "No preview target was provided."
      };
    }
    const compiled = mcelRuntimeCompile(sourceHtml, options);
    if (!compiled.ok) return compiled;
    if (String(target.tagName || "").toLowerCase() === "iframe") {
      target.srcdoc = mcelRuntimeRenderDocument(compiled.runtimeHtmlBeforeChrome || compiled.runtimeHtml, options);
    } else {
      target.innerHTML = compiled.runtimeHtml || "";
    }
    return {
      ...compiled,
      mounted: true,
      target: String(target.tagName || "element").toLowerCase()
    };
  }

  function mcelRuntimeHydrate(root = window.document, options = {}) {
    const target = mcelRuntimeNodeRoot(root);
    if (!target) {
      return {
        ok: false,
        runtime: "mcel",
        version: mcelRuntimeVersion,
        changed: false,
        error: "No hydrate target was available."
      };
    }

    const islands = mcelRuntimeSourceIslands(target);
    if (options.force !== true && !islands.length) {
      const emptyResult = {
        ok: true,
        runtime: "mcel",
        version: mcelRuntimeVersion,
        changed: false,
        sourceCount: 0,
        islandCount: 0,
        reason: "no-mcel-source"
      };
      mcelRuntimeMarkReady(root, emptyResult);
      return emptyResult;
    }

    const doc = mcelRuntimeDocumentFor(root);
    const hydrationOptions = {
      ...options,
      reason: options.reason || "mcel-runtime:hydrate",
      applyChrome: options.applyChrome === true
    };
    const compiledIslands = [];
    const errors = [];
    let changedCount = 0;
    let sourceCount = 0;

    islands.forEach((island, index) => {
      const compiled = mcelRuntimeCompile(mcelRuntimeElementHtml(island), {
        ...hydrationOptions,
        reason: `${hydrationOptions.reason}:island-${index + 1}`
      });
      if (!compiled.ok) {
        errors.push({index, error: compiled.error || "MCEL island compile failed."});
        return;
      }
      sourceCount += Number(compiled.sourceCount || 0);
      compiledIslands.push({
        index,
        sourceCount: Number(compiled.sourceCount || 0),
        eventCount: Array.isArray(compiled.events) ? compiled.events.length : 0,
        chrome: compiled.chrome || null
      });
      if (options.applyToDom !== false) {
        const nodes = mcelRuntimeReplaceElement(island, compiled.runtimeHtml || "");
        if (nodes.length) changedCount += 1;
      }
    });

    const result = {
      ok: errors.length === 0,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      changed: options.applyToDom !== false && changedCount > 0,
      sourceCount,
      islandCount: islands.length,
      hydratedIslandCount: options.applyToDom !== false ? changedCount : 0,
      compiledIslands,
      errors
    };

    if (result.changed) mcelRuntimeEnsureStyle(doc);
    mcelRuntimeMarkReady(root, result);
    return result;
  }

  function mcelRuntimeDetectSources(root = window.document) {
    const islands = mcelRuntimeSourceIslands(root);
    return {
      ok: true,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      sourceCount: islands.length,
      islandCount: islands.length,
      ready: true
    };
  }

  function mcelRuntimeListThemes() {
    const styleLaw = mcelRuntimeStyleLaw();
    if (Array.isArray(styleLaw?.themeCatalog)) return styleLaw.themeCatalog.map((definition) => ({...definition}));
    if (Array.isArray(styleLaw?.themes)) return styleLaw.themes.map((id) => ({id, label: id}));
    return [];
  }

  function mcelRuntimeListChromes() {
    const chromeLaw = mcelRuntimeChromeLaw();
    if (Array.isArray(chromeLaw?.chromeCatalog)) return chromeLaw.chromeCatalog.map((definition) => ({...definition}));
    if (Array.isArray(chromeLaw?.chromes)) return chromeLaw.chromes.map((id) => ({id, label: id}));
    return [];
  }

  const runtime = Object.freeze({
    id: "mcel",
    name: "MCEL Runtime",
    version: mcelRuntimeVersion,
    entry: runtimeEntry,
    compile: mcelRuntimeCompile,
    transform: mcelRuntimeTransform,
    serialize: mcelRuntimeSerialize,
    repair: mcelRuntimeRepair,
    audit: mcelRuntimeAudit,
    applyChrome: mcelRuntimeApplyChrome,
    renderDocument: mcelRuntimeRenderDocument,
    mountPreview: mcelRuntimeMountPreview,
    hydrate: mcelRuntimeHydrate,
    detectSources: mcelRuntimeDetectSources,
    listThemes: mcelRuntimeListThemes,
    listChromes: mcelRuntimeListChromes,
    normalizeTheme: mcelRuntimeNormalizeTheme,
    normalizeChrome: mcelRuntimeNormalizeChrome,
    modules: Object.freeze({
      contract: "McelLabContract",
      engine: "McelLabEngine",
      editor: "McelLabEditor",
      laws: "McelLabLawRegistry",
      styleLaw: "McelLabStyleLaw",
      layoutLaw: "McelLabLayoutLaw",
      chromeLaw: "McelLabChromeLaw",
      platformSpine: "McelLabPlatformSpine"
    })
  });

  Object.defineProperty(window, "MCELRuntime", {
    value: runtime,
    configurable: true,
    writable: false
  });

  Object.defineProperty(window, "WebsiteBuilderRuntime", {
    value: runtime,
    configurable: true,
    writable: false
  });

  function mcelRuntimeAutoHydrate() {
    try {
      runtime.hydrate(window.document, {reason: "mcel-runtime:auto-hydrate"});
    } catch (error) {
      window.console?.warn?.("MCELRuntime auto-hydrate failed", error);
    }
  }

  const currentScript = window.document?.currentScript || null;
  const autoHydrate = currentScript?.dataset?.mcelRuntimeAuto !== "false";
  if (autoHydrate && window.document) {
    if (window.document.readyState === "loading") {
      window.document.addEventListener("DOMContentLoaded", mcelRuntimeAutoHydrate, {once: true});
    } else {
      window.setTimeout(mcelRuntimeAutoHydrate, 0);
    }
  }
'''
