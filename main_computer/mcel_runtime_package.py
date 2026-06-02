"""Build the single-file MCEL page runtime used by Website Builder exports.

The generated runtime intentionally reuses the MCEL compiler/law modules from the
Lab without copying the Lab application UI, state bindings, scenario runners, or
diagnostics panels. It is deterministic so tests can prove that the checked-in
``deploy/local-platform/site-runtimes/mcel-runtime.js`` file is current.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MCEL_RUNTIME_VERSION = "mcel-runtime.v0.1.5"

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
  const mcelRuntimeVersion = "mcel-runtime.v0.1.4";
  const runtimeEntry = "runtime.js";
  const runtimeDefaults = Object.freeze({
    mode: "site",
    theme: "theme-machine",
    chrome: "chrome-strict-hierarchy",
    applyLaws: true,
    applyChrome: false,
    applySiteChrome: true,
    renderOptInOnly: true
  });

  let mcelRuntimeLastReport = null;

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
  const runtimeSiteStyleAttribute = "data-mcel-runtime-site-style";
  const runtimeHydratedAttribute = "data-mcel-runtime-hydrated";
  const runtimeCompiledAttribute = "data-mcel-runtime-compiled";

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

  function mcelRuntimeSourceElements(root, {includeHydrated = false} = {}) {
    const target = mcelRuntimeNodeRoot(root);
    if (!target) return [];
    const sourceSelector = mcelRuntimeSourceSelector();
    const sources = [];

    if (mcelRuntimeElementMatches(target, sourceSelector)) sources.push(target);
    target.querySelectorAll?.(sourceSelector).forEach((node) => sources.push(node));

    return sources.filter((node, index) => {
      if (!node || node.nodeType !== 1 || sources.indexOf(node) !== index) return false;
      return includeHydrated || !mcelRuntimeElementMatches(node, `[${runtimeHydratedAttribute}="true"]`);
    });
  }

  function mcelRuntimeSourceIslands(root, options = {}) {
    const target = mcelRuntimeNodeRoot(root);
    if (!target) return [];
    const sourceSelector = mcelRuntimeSourceSelector();
    const sources = mcelRuntimeSourceElements(target, options);

    return sources.filter((node) => {
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
    return mcelRuntimeSourceElements(root, {includeHydrated: true}).length > 0;
  }

  function mcelRuntimeDocumentFor(root) {
    if (root?.nodeType === 9) return root;
    const target = mcelRuntimeNodeRoot(root);
    return target?.ownerDocument || window.document || null;
  }

  function mcelRuntimeSafeToken(value, fallback = "item") {
    return String(value || fallback)
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48) || fallback;
  }

  function mcelRuntimeNormalizeMode(mode) {
    const value = String(mode || runtimeDefaults.mode).trim().toLowerCase();
    if (["replace", "rendered", "full-render", "lab"].includes(value)) return "render";
    if (["observe", "passive", "audit"].includes(value)) return "observe";
    if (["enhance", "website", "powered"].includes(value)) return "site";
    if (value === "render" || value === "site") return value;
    return runtimeDefaults.mode;
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
    const mode = mcelRuntimeNormalizeMode(options.mode);
    return {
      ...runtimeDefaults,
      ...options,
      mode,
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

  function mcelRuntimeMarkReady(root, result = {}) {
    const doc = mcelRuntimeDocumentFor(root);
    const target = mcelRuntimeNodeRoot(root);
    const body = doc?.body || (target?.tagName === "BODY" ? target : null);
    const html = doc?.documentElement || null;
    const changed = result.changed === true;
    const sourceCount = Number(result.sourceCount || 0);
    const hydratedCount = Number(result.hydratedCount || result.hydratedIslandCount || 0);
    const renderedCount = Number(result.renderedCount || 0);
    const mode = mcelRuntimeNormalizeMode(result.mode || runtimeDefaults.mode);
    const theme = result.theme || runtimeDefaults.theme;
    const chrome = result.chromeId || result.chrome || runtimeDefaults.chrome;

    [html, body].forEach((element) => {
      if (!element?.dataset) return;
      element.dataset.mcelRuntime = "mcel";
      element.dataset.mcelRuntimeVersion = mcelRuntimeVersion;
      element.dataset.mcelRuntimeReady = "true";
      element.dataset.mcelRuntimePowered = sourceCount > 0 ? "true" : "false";
      element.dataset.mcelRuntimeChanged = changed ? "true" : "false";
      element.dataset.mcelRuntimeMode = mode;
      element.dataset.mcelRuntimeTheme = theme;
      element.dataset.mcelRuntimeChrome = chrome;
      element.dataset.mcelRuntimeSourceCount = String(sourceCount);
      element.dataset.mcelRuntimeHydratedCount = String(hydratedCount);
      element.dataset.mcelRuntimeRenderedCount = String(renderedCount);
    });

    if (body?.classList) {
      body.classList.add("mcel-runtime-ready");
      body.classList.toggle("mcel-runtime-active", sourceCount > 0);
      body.classList.toggle("mcel-powered-site", sourceCount > 0 && mode !== "observe");
      body.classList.toggle("mcel-runtime-rendered", renderedCount > 0);
    }

    mcelRuntimeLastReport = {
      ...result,
      ready: true,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      sourceCount,
      hydratedCount,
      renderedCount,
      mode,
      theme,
      chrome
    };

    try {
      doc?.dispatchEvent?.(new CustomEvent("mcel-runtime-ready", {detail: mcelRuntimeLastReport}));
    } catch (_error) {
      // CustomEvent may be unavailable in a few test/legacy hosts; readiness markers are still written.
    }
  }

  function mcelRuntimeDebugRequested(doc) {
    const targetDoc = doc || window.document || null;
    const currentScript = targetDoc?.currentScript || window.document?.currentScript || null;
    if (currentScript?.dataset?.mcelRuntimeDebug === "true") return true;
    const location = window.location;
    return Boolean(location && /(?:\?|&|#)mcel(?:-runtime)?-debug(?:=1|=true)?(?:&|$)/i.test(`${location.search || ""}${location.hash || ""}`));
  }

  function mcelRuntimeSiteModeCss() {
    return `
:root[data-mcel-runtime-powered="true"] {
  color-scheme: dark;
  --mcel-runtime-accent: #38bdf8;
  --mcel-runtime-accent-strong: #f59e0b;
  --mcel-runtime-accent-warm: #fb7185;
  --mcel-runtime-ink: #e5eefc;
  --mcel-runtime-muted: #b8c6df;
  --mcel-runtime-faint: #7f91b3;
  --mcel-runtime-bg: #050b16;
  --mcel-runtime-bg-2: #0b1224;
  --mcel-runtime-panel: rgba(15, 23, 42, .74);
  --mcel-runtime-panel-strong: rgba(15, 23, 42, .94);
  --mcel-runtime-panel-soft: rgba(30, 41, 59, .68);
  --mcel-runtime-surface-ring: rgba(125, 211, 252, .24);
  --mcel-runtime-surface-ring-strong: rgba(245, 158, 11, .38);
  --mcel-runtime-surface-shadow: 0 26px 80px rgba(2, 6, 23, .32);
  --mcel-runtime-glow: 0 0 0 1px rgba(125, 211, 252, .16), 0 18px 60px rgba(56, 189, 248, .12);
  --mcel-runtime-radius-xl: 2rem;
  --mcel-runtime-radius-lg: 1.35rem;
  --mcel-runtime-radius-md: .9rem;
  --mcel-runtime-focus-ring: 0 0 0 .22rem rgba(56, 189, 248, .32);
}

body.mcel-powered-site {
  min-height: 100vh;
  color: var(--mcel-runtime-ink);
  background:
    radial-gradient(circle at 16% 4%, rgba(56, 189, 248, .18), transparent 34rem),
    radial-gradient(circle at 90% 12%, rgba(245, 158, 11, .16), transparent 32rem),
    radial-gradient(circle at 70% 72%, rgba(99, 102, 241, .16), transparent 38rem),
    linear-gradient(180deg, #07101f 0%, #08111f 46%, #040813 100%);
  letter-spacing: -.01em;
}

body.mcel-powered-site::before {
  content: "";
  position: fixed;
  inset: 0;
  z-index: -1;
  pointer-events: none;
  background:
    linear-gradient(rgba(255, 255, 255, .026) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, .022) 1px, transparent 1px);
  background-size: 4.5rem 4.5rem;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, .84), transparent 76%);
}

body.mcel-powered-site :where([data-mc][data-mcel-runtime-hydrated="true"]) {
  box-sizing: border-box;
}

/* The page/root source is semantic only. Do not turn the whole page into a grid/card. */
body.mcel-powered-site :where(.mc-site, main[data-mc], [data-mc-component-kind="page"]) {
  display: block;
  width: 100%;
  max-width: none;
  margin: 0;
  padding: 0;
}

body.mcel-powered-site :where(.mc-section) {
  position: relative;
  isolation: isolate;
}

/* Production chrome enhances authored Website Builder sections; it does not replace their layout. */
body.mcel-powered-site :where(section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"], .mc-hero[data-mcel-runtime-hydrated="true"]) {
  color: #f8fbff;
  background:
    radial-gradient(circle at 78% 21%, rgba(245, 158, 11, .26), transparent 18rem),
    radial-gradient(circle at 14% 18%, rgba(56, 189, 248, .24), transparent 20rem),
    linear-gradient(135deg, rgba(2, 6, 23, .98), rgba(15, 23, 42, .9) 52%, rgba(30, 27, 75, .94));
  border-bottom: 1px solid rgba(125, 211, 252, .16);
  box-shadow: inset 0 -1px 0 rgba(255, 255, 255, .05);
  overflow: hidden;
}

body.mcel-powered-site :where(section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"], .mc-hero[data-mcel-runtime-hydrated="true"])::before {
  content: "MCEL powered";
  position: absolute;
  top: clamp(1rem, 2.4vw, 1.65rem);
  right: max(1.5rem, calc((100vw - 1120px) / 2));
  z-index: 2;
  padding: .48rem .72rem;
  border: 1px solid rgba(125, 211, 252, .26);
  border-radius: 999px;
  color: #bae6fd;
  background: rgba(2, 6, 23, .52);
  font: 800 .68rem/1 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  letter-spacing: .14em;
  text-transform: uppercase;
  backdrop-filter: blur(16px);
}

body.mcel-powered-site :where(section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"], .mc-hero[data-mcel-runtime-hydrated="true"]) h1 {
  color: #f8fbff;
  text-wrap: balance;
}

body.mcel-powered-site :where(.mc-eyebrow, [data-mc-slot="meta"]) {
  color: #93c5fd;
}

body.mcel-powered-site :where(.mc-lede) {
  color: #cbd7ef;
}

body.mcel-powered-site :where(.mc-button, a[data-mc-action], button[data-mc-action]) {
  transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease, background-color .16s ease;
}

body.mcel-powered-site :where(.mc-button, a[data-mc-action], button[data-mc-action]):hover {
  transform: translateY(-2px);
  box-shadow: 0 18px 42px rgba(56, 189, 248, .18);
}

body.mcel-powered-site :where(.mc-button, a[data-mc-action], button[data-mc-action]):focus-visible {
  outline: none;
  box-shadow: var(--mcel-runtime-focus-ring);
}

/* Keep the authored media card dimensions; only upgrade its skin. */
body.mcel-powered-site :where(.mc-visual-card[data-mcel-runtime-hydrated="true"], section[data-mc-kind="hero"] .mc-visual-card) {
  color: #f8fbff;
  border: 1px solid rgba(255, 255, 255, .2);
  background:
    radial-gradient(circle at 72% 18%, rgba(250, 204, 21, .84), transparent 7rem),
    radial-gradient(circle at 22% 74%, rgba(56, 189, 248, .52), transparent 11rem),
    linear-gradient(135deg, #0891b2, #4f46e5 48%, #db2777);
  box-shadow: 0 32px 90px rgba(2, 6, 23, .35), inset 0 0 0 1px rgba(255, 255, 255, .1);
}

/* Section-level MCEL surfaces. Restrict rules to sections so the page wrapper never collapses. */
body.mcel-powered-site :where(section[data-mc-kind="proof"][data-mcel-runtime-hydrated="true"]) {
  background:
    linear-gradient(135deg, rgba(14, 165, 233, .16), rgba(99, 102, 241, .1)),
    rgba(15, 23, 42, .86);
  color: var(--mcel-runtime-ink);
  border-block: 1px solid rgba(125, 211, 252, .16);
  box-shadow: var(--mcel-runtime-glow);
}

body.mcel-powered-site :where(section[data-mc-kind="signal"][data-mcel-runtime-hydrated="true"], .mc-blog-widget[data-mcel-runtime-hydrated="true"]) {
  color: var(--mcel-runtime-ink);
  background:
    linear-gradient(180deg, rgba(15, 23, 42, .92), rgba(15, 23, 42, .78)),
    radial-gradient(circle at 0 0, rgba(56, 189, 248, .13), transparent 18rem);
  border-block: 1px solid rgba(125, 211, 252, .14);
}

body.mcel-powered-site :where(section[data-mc-kind="work"][data-mcel-runtime-hydrated="true"], section[data-mc-kind="article"][data-mcel-runtime-hydrated="true"]) {
  color: var(--mcel-runtime-ink);
  background:
    radial-gradient(circle at 100% 0, rgba(56, 189, 248, .1), transparent 20rem),
    rgba(7, 16, 31, .92);
}

body.mcel-powered-site :where(section[data-mc-kind="work"][data-mcel-runtime-hydrated="true"].mc-feature-grid, section[data-mc-kind="article"][data-mcel-runtime-hydrated="true"].mc-feature-grid, section[data-mc-flow="stack"][data-mcel-runtime-hydrated="true"].mc-feature-grid) {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: clamp(1rem, 2vw, 1.25rem);
}

/* Cards get the MCEL theme without being shoved into overlay columns. */
body.mcel-powered-site :where(.mc-feature) {
  color: #dbeafe;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, .08), rgba(255, 255, 255, .035)),
    rgba(15, 23, 42, .82);
  border-color: rgba(148, 163, 184, .2);
  box-shadow: 0 22px 70px rgba(2, 6, 23, .22);
}

body.mcel-powered-site :where(.mc-feature strong, [data-mc-slot="title"]) {
  color: #f8fbff;
}

body.mcel-powered-site :where(.mc-feature p, .mc-blog-widget__placeholder) {
  color: #b9c7df;
  line-height: 1.68;
}

body.mcel-powered-site :where(code) {
  color: #bae6fd;
  background: rgba(14, 165, 233, .1);
  border: 1px solid rgba(14, 165, 233, .16);
  border-radius: .45rem;
  padding: .08rem .28rem;
}

body.mcel-powered-site :where(.mc-blog-widget__placeholder) {
  border-color: rgba(125, 211, 252, .18);
  background: rgba(2, 6, 23, .26);
}

body.mcel-powered-site :where(.mc-cta[data-mcel-runtime-hydrated="true"], section[data-mc="command-row"][data-mcel-runtime-hydrated="true"]) {
  color: #f8fbff;
  background:
    radial-gradient(circle at 50% 0, rgba(56, 189, 248, .18), transparent 20rem),
    linear-gradient(135deg, rgba(15, 23, 42, .98), rgba(2, 6, 23, .94));
  border-block: 1px solid rgba(125, 211, 252, .16);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .05);
}

body.mcel-powered-site :where(.mc-footer[data-mcel-runtime-hydrated="true"], footer[data-mcel-runtime-hydrated="true"]) {
  color: var(--mcel-runtime-muted);
  background: rgba(2, 6, 23, .98);
  border-top: 1px solid rgba(148, 163, 184, .16);
}

body.mcel-powered-site [data-mcel-runtime-hydrated="true"][data-mc-component-kind="island"],
body.mcel-powered-site [data-mcel-runtime-hydrated="true"][data-mc-render="island"] {
  content-visibility: auto;
  contain-intrinsic-size: auto 320px;
}

html[data-mcel-runtime-debug="true"] body.mcel-powered-site::after {
  content: "MCEL " attr(data-mcel-runtime-version) " · " attr(data-mcel-runtime-source-count) " sources · " attr(data-mcel-runtime-hydrated-count) " hydrated";
  position: fixed;
  right: 1rem;
  bottom: 1rem;
  z-index: 2147483647;
  max-width: min(28rem, calc(100vw - 2rem));
  padding: .72rem .9rem;
  border-radius: 999px;
  color: #e0f2fe;
  background: rgba(2, 6, 23, .86);
  border: 1px solid rgba(125, 211, 252, .28);
  box-shadow: 0 18px 60px rgba(2, 6, 23, .35);
  font: 700 .78rem/1.2 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  letter-spacing: .04em;
  backdrop-filter: blur(12px);
  pointer-events: none;
}

@media (max-width: 780px) {
  body.mcel-powered-site :where(section[data-mc-kind="work"][data-mcel-runtime-hydrated="true"].mc-feature-grid, section[data-mc-kind="article"][data-mcel-runtime-hydrated="true"].mc-feature-grid, section[data-mc-flow="stack"][data-mcel-runtime-hydrated="true"].mc-feature-grid) {
    grid-template-columns: 1fr;
  }

  body.mcel-powered-site :where(section[data-mc-kind="hero"][data-mcel-runtime-hydrated="true"], .mc-hero[data-mcel-runtime-hydrated="true"])::before {
    right: 1rem;
  }
}

@media (prefers-reduced-motion: reduce) {
  body.mcel-powered-site :where(.mc-button, a[data-mc-action], button[data-mc-action]) {
    transition: none;
  }

  body.mcel-powered-site :where(.mc-button, a[data-mc-action], button[data-mc-action]):hover {
    transform: none;
  }
}
`;
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

  function mcelRuntimeEnsureSiteChrome(doc) {
    const targetDoc = doc || window.document || null;
    if (!targetDoc?.head?.appendChild || targetDoc.head.querySelector?.(`[${runtimeSiteStyleAttribute}]`)) return false;
    const style = targetDoc.createElement("style");
    style.setAttribute(runtimeSiteStyleAttribute, mcelRuntimeVersion);
    style.textContent = mcelRuntimeSiteModeCss();
    targetDoc.head.appendChild(style);
    return true;
  }

  function mcelRuntimeApplySourceClasses(element) {
    if (!element?.classList) return;
    const type = mcelRuntimeSafeToken(element.getAttribute(mcelRuntimeSourceAttribute()), "source");
    const kind = mcelRuntimeSafeToken(element.getAttribute("data-mc-kind"), "kind");
    const flow = mcelRuntimeSafeToken(element.getAttribute("data-mc-flow"), "flow");
    const rank = mcelRuntimeSafeToken(element.getAttribute("data-mc-rank"), "rank");
    const render = mcelRuntimeSafeToken(element.getAttribute("data-mc-render"), "render");
    element.classList.add("mcel-runtime-source");
    element.classList.add(`mcel-type-${type}`);
    element.classList.add(`mcel-kind-${kind}`);
    element.classList.add(`mcel-flow-${flow}`);
    element.classList.add(`mcel-rank-${rank}`);
    element.classList.add(`mcel-render-${render}`);
  }

  function mcelRuntimeMarkHydrated(nodes, meta = {}) {
    const sourceSelector = mcelRuntimeSourceSelector();
    const mark = (node) => {
      if (!node || node.nodeType !== 1 || !mcelRuntimeElementMatches(node, sourceSelector)) return;
      node.setAttribute(runtimeHydratedAttribute, "true");
      if (meta.compiled !== false) node.setAttribute(runtimeCompiledAttribute, "true");
      if (meta.index != null) node.dataset.mcelRuntimeIndex = String(meta.index);
      if (meta.mode) node.dataset.mcelRuntimeMode = String(meta.mode);
      if (meta.component) node.dataset.mcelRuntimeComponent = String(meta.component);
      if (meta.eventCount != null) node.dataset.mcelRuntimeEventCount = String(meta.eventCount);
      if (meta.sourceCount != null) node.dataset.mcelRuntimeLocalSourceCount = String(meta.sourceCount);
      mcelRuntimeApplySourceClasses(node);
    };

    nodes.forEach((node) => {
      mark(node);
      node.querySelectorAll?.(sourceSelector).forEach((child) => mark(child));
    });
  }

  function mcelRuntimeEnhanceElement(element, compiled, meta = {}) {
    const component = element.getAttribute?.("data-mc-component") || element.getAttribute?.("data-mc") || "mcel-source";
    const eventCount = Array.isArray(compiled?.events) ? compiled.events.length : 0;
    mcelRuntimeMarkHydrated([element], {
      ...meta,
      component,
      eventCount,
      sourceCount: Number(compiled?.sourceCount || 0),
      compiled: Boolean(compiled?.ok)
    });
    element.dataset.mcelRuntimeCompileOk = compiled?.ok ? "true" : "false";
    element.dataset.mcelRuntimeChromeApplied = meta.mode === "render" ? "render" : "site";
    if (compiled?.chrome?.report?.chrome) element.dataset.mcelRuntimeChromeReport = compiled.chrome.report.chrome;
    return element;
  }

  function mcelRuntimeReplaceElement(element, html, meta = {}) {
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
    mcelRuntimeMarkHydrated(nodes, meta);
    return nodes;
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

  function mcelRuntimeElementRenderMode(element, options = {}) {
    const runtimeMode = element?.getAttribute?.("data-mcel-runtime") || element?.dataset?.mcelRuntimeMode || "";
    const renderMode = element?.getAttribute?.("data-mc-render") || "";
    const normalized = mcelRuntimeNormalizeMode(runtimeMode || options.mode || runtimeDefaults.mode);
    if (normalized === "render") return "render";
    if (String(renderMode).toLowerCase() === "render") return "render";
    if (String(runtimeMode).toLowerCase() === "replace") return "render";
    return normalized;
  }

  function mcelRuntimeShouldRender(element, options = {}) {
    if (options.mode === "render" && options.renderOptInOnly === false) return true;
    if (options.forceRender === true) return true;
    return mcelRuntimeElementRenderMode(element, options) === "render";
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

    const opts = mcelRuntimeOptions(options);
    const sources = mcelRuntimeSourceElements(target, {includeHydrated: options.force === true});
    if (options.force !== true && !sources.length) {
      const emptyResult = {
        ok: true,
        runtime: "mcel",
        version: mcelRuntimeVersion,
        mode: opts.mode,
        theme: opts.theme,
        chrome: opts.chrome,
        changed: false,
        sourceCount: 0,
        islandCount: 0,
        hydratedCount: 0,
        renderedCount: 0,
        reason: "no-mcel-source"
      };
      mcelRuntimeMarkReady(root, emptyResult);
      return emptyResult;
    }

    const doc = mcelRuntimeDocumentFor(root);
    if (opts.applySiteChrome !== false && opts.mode !== "observe") {
      mcelRuntimeEnsureSiteChrome(doc);
    }
    if (mcelRuntimeDebugRequested(doc)) {
      doc?.documentElement?.setAttribute("data-mcel-runtime-debug", "true");
    }

    const compiledSources = [];
    const errors = [];
    let compiledCount = 0;
    let hydratedCount = 0;
    let renderedCount = 0;
    let localSourceCount = 0;

    sources.forEach((source, index) => {
      const renderThisSource = mcelRuntimeShouldRender(source, opts);
      const compiled = mcelRuntimeCompile(mcelRuntimeElementHtml(source), {
        ...opts,
        applyChrome: renderThisSource ? opts.applyChrome !== false : false,
        reason: `${opts.reason || "mcel-runtime:hydrate"}:source-${index + 1}`
      });
      if (!compiled.ok) {
        errors.push({index, error: compiled.error || "MCEL source compile failed."});
        mcelRuntimeEnhanceElement(source, compiled, {index: index + 1, mode: opts.mode});
        hydratedCount += 1;
        return;
      }

      compiledCount += 1;
      localSourceCount += Number(compiled.sourceCount || 0);
      compiledSources.push({
        index,
        mode: renderThisSource ? "render" : opts.mode,
        component: source.getAttribute?.("data-mc-component") || source.getAttribute?.("data-mc") || null,
        sourceCount: Number(compiled.sourceCount || 0),
        eventCount: Array.isArray(compiled.events) ? compiled.events.length : 0,
        chrome: compiled.chrome || null
      });

      if (renderThisSource) {
        const nodes = mcelRuntimeReplaceElement(source, compiled.runtimeHtml || "", {
          index: index + 1,
          mode: "render",
          sourceCount: Number(compiled.sourceCount || 0),
          eventCount: Array.isArray(compiled.events) ? compiled.events.length : 0
        });
        if (nodes.length) {
          renderedCount += 1;
          hydratedCount += nodes.filter((node) => node.nodeType === 1).length || 1;
          mcelRuntimeEnsureStyle(doc);
        }
        return;
      }

      mcelRuntimeEnhanceElement(source, compiled, {index: index + 1, mode: opts.mode});
      hydratedCount += 1;
    });

    const result = {
      ok: errors.length === 0,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      mode: opts.mode,
      theme: opts.theme,
      chromeId: opts.chrome,
      changed: sources.length > 0,
      sourceCount: sources.length,
      compiledSourceCount: localSourceCount,
      islandCount: mcelRuntimeSourceIslands(target, {includeHydrated: true}).length,
      hydratedCount,
      renderedCount,
      compiledCount,
      compiledSources,
      errors
    };

    mcelRuntimeMarkReady(root, result);
    return result;
  }

  function mcelRuntimePowerSite(root = window.document, options = {}) {
    return mcelRuntimeHydrate(root, {...options, mode: options.mode || "site", applySiteChrome: options.applySiteChrome !== false});
  }

  function mcelRuntimeDetectSources(root = window.document) {
    const sources = mcelRuntimeSourceElements(root, {includeHydrated: true});
    const hydrated = sources.filter((node) => node.getAttribute?.(runtimeHydratedAttribute) === "true").length;
    return {
      ok: true,
      runtime: "mcel",
      version: mcelRuntimeVersion,
      sourceCount: sources.length,
      islandCount: mcelRuntimeSourceIslands(root, {includeHydrated: true}).length,
      hydratedCount: hydrated,
      ready: true
    };
  }

  function mcelRuntimeReport(root = window.document) {
    const doc = mcelRuntimeDocumentFor(root);
    const detected = mcelRuntimeDetectSources(root);
    return {
      ...(mcelRuntimeLastReport || {}),
      ...detected,
      ready: doc?.documentElement?.dataset?.mcelRuntimeReady === "true",
      powered: doc?.documentElement?.dataset?.mcelRuntimePowered === "true",
      mode: doc?.documentElement?.dataset?.mcelRuntimeMode || mcelRuntimeLastReport?.mode || runtimeDefaults.mode,
      theme: doc?.documentElement?.dataset?.mcelRuntimeTheme || mcelRuntimeLastReport?.theme || runtimeDefaults.theme,
      chrome: doc?.documentElement?.dataset?.mcelRuntimeChrome || mcelRuntimeLastReport?.chrome || runtimeDefaults.chrome
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
    powerSite: mcelRuntimePowerSite,
    report: mcelRuntimeReport,
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
      runtime.powerSite(window.document, {reason: "mcel-runtime:auto-hydrate"});
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
