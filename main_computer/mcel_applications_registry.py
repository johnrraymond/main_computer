"""Canonical MCEL Applications registry and system presentation policy.

The shell's existing registry is the only application inventory. All promoted
MCEL runtimes, including SLH, inherit its Theme, Form, and Chrome
policy through MCEL-owned presentation dependencies, not app-authored assets.
This Python module assembles the runtime; browser laws own visual mutations.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
import re

from main_computer.mcel_phase2c_candidate_host import CandidateHostError

REGISTRY_PATH = Path("tools/fixtures/mcel_semantic_applications_shell/registry.json")
CANONICAL_ROUTE_PATH = Path("main_computer/viewport_state.py")
SEMANTIC_APPS = frozenset({"calculator", "file-explorer", "task-manager", "code-editor", "chat-console", "slh"})
PROPERTIES = ("theme", "form", "chrome")
POLICY_SCHEMA = "mcel.presentation-policy.v1"
SELECTION_SCHEMA = "mcel.presentation-selection.v1"
HOOK_JS = Path("main_computer/web/applications/scripts/mcel-application-presentation-registry.js")
CONTROLLER_JS = Path("main_computer/web/applications/scripts/mcel-unified-presentation-controller.js")
LOGGER = logging.getLogger(__name__)


def canonical_route_names(repo: Path) -> frozenset[str]:
    """Use the existing canonical route allowlist without importing the SPA."""
    tree = ast.parse((repo / CANONICAL_ROUTE_PATH).read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APPLICATION_ROUTE_NAMES"
            for target in statement.targets
        ):
            value = ast.literal_eval(statement.value)
            if isinstance(value, set) and all(isinstance(item, str) for item in value):
                return frozenset(value)
            break
    raise CandidateHostError("Canonical application route allowlist is unavailable")


def _profile_value(prop: str, value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(rf"{prop}-[a-z0-9]+(?:-[a-z0-9]+)*", value))


def _validate_presentation(presentation: object, apps_by_id: dict) -> None:
    if not isinstance(presentation, dict) or set(presentation) != {"schema", "system", "rollout"}:
        raise CandidateHostError("MCEL registry presentation contract invalid")
    if presentation["schema"] != POLICY_SCHEMA:
        raise CandidateHostError("MCEL registry presentation schema invalid")
    system = presentation["system"]
    if not isinstance(system, dict) or set(system) != set(PROPERTIES):
        raise CandidateHostError("MCEL registry system presentation is incomplete")
    for prop in PROPERTIES:
        if not _profile_value(prop, system[prop]):
            raise CandidateHostError(f"MCEL registry system {prop} profile invalid")
    rollout = presentation["rollout"]
    if not isinstance(rollout, dict) or set(rollout) != {"defaultEnabled", "apps"}:
        raise CandidateHostError("MCEL registry presentation rollout contract invalid")
    if not isinstance(rollout["defaultEnabled"], bool) or not isinstance(rollout["apps"], dict):
        raise CandidateHostError("MCEL registry presentation rollout values invalid")
    for app_id, enabled in rollout["apps"].items():
        if app_id not in SEMANTIC_APPS or app_id not in apps_by_id or not isinstance(enabled, bool):
            raise CandidateHostError("MCEL registry presentation rollout app invalid")


def read_registry(repo: Path, *, validate_presentation: bool = True) -> dict:
    """Validate the shared inventory, optionally validating presentation.

    The Applications shell consumes app identity and routes only.  A malformed
    *presentation* field must not take the launcher down; presentation
    consumers validate it separately and fail open to the existing appearance.
    """
    registry = json.loads((repo / REGISTRY_PATH).read_text(encoding="utf-8"))
    apps = registry.get("apps")
    runtime_apps = registry.get("runtimeApps", [])
    if not isinstance(runtime_apps, list):
        raise CandidateHostError("MCEL runtime app registry invalid")
    if registry.get("schema") != "mcel.applications-shell.registry.v1" or not isinstance(apps, list):
        raise CandidateHostError("MCEL shell registry schema invalid")
    indexed = {}
    for app in apps:
        if not isinstance(app, dict) or not {"id", "title", "description", "route", "mount"}.issubset(app) or set(app) - {"id", "title", "description", "route", "mount", "presentation"}:
            raise CandidateHostError("MCEL shell registry entry invalid")
        slug = app["id"]
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", slug) or slug in indexed:
            raise CandidateHostError("MCEL shell registry has unknown or duplicate app")
        indexed[slug] = app
        if app["route"] != ("/applications" if slug == "desktop" else f"/applications/{slug}"):
            raise CandidateHostError("MCEL shell registry route must be canonical")
        expected = "overview" if slug == "desktop" else "semantic" if slug in SEMANTIC_APPS else "legacy"
        if app["mount"] != expected:
            raise CandidateHostError("MCEL shell registry cannot misclassify app implementation")
        for key in ("title", "description"):
            if not isinstance(app[key], str) or not 0 < len(app[key]) <= 160:
                raise CandidateHostError("MCEL shell registry text invalid")
        if validate_presentation and "presentation" in app:
            override = app["presentation"]
            if app["mount"] != "semantic" or not isinstance(override, dict) or not override or set(override) - set(PROPERTIES):
                raise CandidateHostError("MCEL shell registry app presentation override invalid")
            for prop, value in override.items():
                if not _profile_value(prop, value):
                    raise CandidateHostError("MCEL shell registry app presentation value invalid")
    # Runtime registrations may opt into the canonical launcher without
    # mutating the pinned legacy launcher/icon oracle source files.
    for app in runtime_apps:
        if (not isinstance(app, dict) or not {"id", "route", "mount"}.issubset(app) or
                set(app) - {"id", "route", "mount", "launcher", "title", "description"} or
                not isinstance(app["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]*", app["id"]) or
                app["id"] in indexed or app["route"] != "/applications/" + app["id"] or
                app["mount"] not in {"semantic", "legacy"} or
                (app["mount"] == "legacy" and app.get("launcher") is not True)):
            raise CandidateHostError("MCEL runtime registry invalid")
        if app.get("launcher", False):
            if (app.get("launcher") is not True or
                not all(isinstance(app.get(k), str) and 0 < len(app[k]) <= 160
                        for k in ("title", "description"))):
                raise CandidateHostError("Launcher runtime identity requires display metadata")
        elif "title" in app or "description" in app:
            raise CandidateHostError("Non-launcher runtime cannot claim launcher text")
        indexed[app["id"]] = app
    if set(indexed) != canonical_route_names(repo):
        raise CandidateHostError("MCEL shell registry must cover all canonical app routes")
    if set(registry) - {"schema", "apps", "runtimeApps", "presentation"}:
        raise CandidateHostError("MCEL shell registry top-level contract invalid")
    if validate_presentation:
        _validate_presentation(registry.get("presentation"), indexed)
    return registry


def resolve_presentation(registry: dict, app_id: str, *, promoted: bool = True,
                         semantic_candidate: bool = False) -> dict:
    """Pure resolution: system defaults, then sparse per-app overrides."""
    app = next((a for a in [*registry["apps"], *registry.get("runtimeApps", [])] if a["id"] == app_id), None)
    if app is None:
        raise CandidateHostError(f"Unknown MCEL application registry key: {app_id}")
    profiles = dict(registry["presentation"]["system"])
    profiles.update(app.get("presentation", {}))
    rollout = registry["presentation"]["rollout"]
    # The native Worker candidate is hosted on its own semantic route while
    # /applications/worker is still the canonical legacy marketplace Worker.
    # Opt-in is intentionally explicit and restricted to this one application;
    # never promote the production registry entry to make styling work.
    if semantic_candidate and (app_id != "worker" or app["mount"] != "legacy"):
        raise CandidateHostError("Unknown semantic presentation candidate")
    eligible = promoted and (app["mount"] == "semantic" or semantic_candidate)
    enabled = bool(eligible and rollout["apps"].get(app_id, rollout["defaultEnabled"]))
    return {
        "schema": SELECTION_SCHEMA,
        "appId": app_id,
        "profiles": profiles,
        "eligible": bool(eligible),
        "rolloutEnabled": enabled,
    }


# The controller is a core MCEL runtime dependency, never an optional script
# a particular application has to remember to include. All operational hosts
# inline the same shared runtime, so the same pre-runtime insertion works for
# Calculator, workbench applications, and the independently hosted SLH.
PRESENTATION_RUNTIME_ASSETS = (
    "main_computer/web/applications/scripts/mcel-contract.js",
    "main_computer/web/applications/scripts/mcel-law-registry.js",
    "main_computer/web/applications/scripts/mcel-style-law.js",
    "main_computer/web/applications/scripts/mcel-semantic-color-presentation.js",
    "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js",
    "main_computer/web/applications/scripts/mcel-semantic-presentation-bridge.js",
    "main_computer/web/applications/scripts/mcel-semantic-form-presentation-law.js",
    "main_computer/web/applications/scripts/mcel-semantic-chrome-presentation-law.js",
    str(CONTROLLER_JS),
)
ICON_RUNTIME_ASSETS = (
    "main_computer/web/applications/scripts/mcel-icon.js",
    "main_computer/web/applications/scripts/mcel-semantic-icon-core.js",
)
RUNTIME_JS = Path("main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js")


def _load_core_presentation(page: str, repo: Path) -> str:
    """Insert missing core MCEL presentation dependencies before runtime mount.

    This is a shared host assembly contract; no app-specific dependency lists.
    The operational runtime itself has no network loader or eval fallback.
    """
    runtime_source = (repo / RUNTIME_JS).read_text(encoding="utf-8")
    # Icon data and paint are optional *relative to* the established three
    # presentation laws. A corrupt/missing icon catalog must never suppress
    # the shared Theme/Form/Chrome dependencies or the operational mount.
    icon_fragments = []
    try:
        registry_source = (repo / "main_computer/web/applications/icons/mcel-icons.v1.json").read_text(encoding="utf-8")
        icon_registry = json.dumps(json.loads(registry_source), separators=(",", ":")).replace("<", "\\u003c")
        icon_css = (repo / "main_computer/web/applications/styles/mcel-icon.css").read_text(encoding="utf-8")
        if "</style" in icon_css.lower():
            raise CandidateHostError("Unsafe MCEL icon stylesheet")
        icon_fragments = [
            '<script id="mcel-icon-registry" type="application/json">' + icon_registry + '</script>\n',
            '<style>\n' + icon_css + '\n</style>\n',
        ]
        for asset in ICON_RUNTIME_ASSETS:
            source = (repo / asset).read_text(encoding="utf-8")
            if "</script" in source.lower():
                raise CandidateHostError("Unsafe optional MCEL icon runtime dependency")
            icon_fragments.append("<script>\n" + source + "\n</script>\n")
    except (OSError, ValueError, CandidateHostError) as error:
        LOGGER.warning("MCEL optional icon assets unavailable: %s", error)
    anchor = "<script>\n" + runtime_source[:100]
    if anchor not in page:
        # The canonical region-only MCEL host has no operational runtime;
        # it still receives the identical core presentation dependencies.
        head = re.search(r"</head>\s*<body\b", page, re.I)
        if not head or 'data-mcel-region-id' not in page:
            return page
        fragments = list(icon_fragments)
        for relative in PRESENTATION_RUNTIME_ASSETS:
            source = (repo / relative).read_text(encoding="utf-8")
            if "</script" in source.lower():
                raise CandidateHostError("Unsafe MCEL native presentation dependency")
            if source[:120] not in page:
                fragments.append("<script>\n" + source + "\n</script>\n")
        return page[:head.start()] + "".join(fragments) + page[head.start():]
    fragments = list(icon_fragments)
    for relative in PRESENTATION_RUNTIME_ASSETS:
        source = (repo / relative).read_text(encoding="utf-8")
        if "</script" in source.lower():
            raise CandidateHostError("Unsafe MCEL runtime presentation dependency")
        if source[:120] not in page:
            fragments.append("<script>\n" + source + "\n</script>\n")
    return page.replace(anchor, "".join(fragments) + anchor, 1)


def inert_presentation_mount(repo: Path, app_id: str, root_id: str, *, controller_preloaded: bool = False) -> str:
    """Opt-in HTML appended only to promoted pages, never to candidate pages.

    Lookup failures are reported to the browser hook but never break mounting.
    Neither registry hook nor Stage-2 controller can apply presentation, even
    when rolloutEnabled=true: Stage 3 must explicitly add that capability.
    """
    try:
        selection = resolve_presentation(read_registry(repo), app_id)
    except (OSError, ValueError, CandidateHostError) as err:
        selection = {"schema": SELECTION_SCHEMA, "appId": app_id,
                     "eligible": False, "rolloutEnabled": False,
                     "status": "registry-unavailable", "reason": str(err)}
    payload = json.dumps(selection, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    source = (repo / HOOK_JS).read_text(encoding="utf-8")
    if "</script" in source.lower():
        raise CandidateHostError("Unsafe presentation registry hook asset")
    # A missing optional controller must not block the existing app or the
    # Stage-1 registry lookup; the latter reports controller-unavailable.
    try:
        controller_source = "" if controller_preloaded else (repo / CONTROLLER_JS).read_text(encoding="utf-8")
        if "</script" in controller_source.lower():
            raise CandidateHostError("Unsafe presentation controller asset")
        controller_script = ('<script>\n' + controller_source + '\n</script>') if controller_source else ''
    except (OSError, CandidateHostError) as error:
        LOGGER.warning("MCEL presentation controller unavailable: %s", error)
        controller_script = ""
    # These scripts run after the original mount and cannot throw into it.
    # They never invoke the operational Color Law or the Lab's Form/Chrome laws.
    call = json.dumps({"appId": app_id, "rootId": root_id}, separators=(",", ":"))
    return (f'<script id="mcel-application-presentation-selection" type="application/json">{payload}</script>'
            + controller_script + '<script>\n' + source + '\n</script>'
            + '<script>(function(){try{window.__mcelPresentationSelection=' 
            + f'window.McelApplicationPresentationRegistry.connect({call});'
            + '}catch(e){window.__mcelPresentationSelection=Object.freeze({status:"registry-unavailable",reason:String(e)});}})();</script>')


def attach_inert_presentation(page: str, repo: Path, app_id: str, root_id: str, *,
                                promoted: bool, semantic_candidate: bool = False) -> str:
    """Mount the system presentation for a registered promoted MCEL app.

    Presentation is a core MCEL runtime facility, not an app-owned dependency.
    The old name is retained for host compatibility; this is now active.
    """
    if not promoted:
        return page
    marker = "</body></html>"
    if page.count(marker) != 1:
        raise CandidateHostError("MCEL presentation page boundary invalid")
    # MCEL normally injects all core presentation laws. A damaged or missing
    # optional asset may degrade presentation but never stop the operation host.
    try:
        page = _load_core_presentation(page, repo)
    except (OSError, CandidateHostError) as error:
        LOGGER.warning("MCEL presentation assets unavailable for %s: %s", app_id, error)
    # A damaged/unavailable policy cannot prevent an otherwise valid MCEL app
    # from mounting. Disable presentation and retain incumbent rendering.
    try:
        selection = resolve_presentation(read_registry(repo), app_id,
                                             semantic_candidate=semantic_candidate)
    except (OSError, ValueError, CandidateHostError) as error:
        LOGGER.warning("MCEL presentation registry unavailable for %s: %s", app_id, error)
        selection = {"schema": SELECTION_SCHEMA, "appId": app_id,
                     "eligible": False, "rolloutEnabled": False,
                     "status": "registry-unavailable", "reason": str(error)}
    payload = json.dumps(selection, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    preflight = (f'<script id="mcel-application-presentation-selection" type="application/json">{payload}</script>'
                 f'<script>window.__mcelSystemPresentationSelection={payload};'
                 f'window.__mcelSemanticColorRollout={str(selection["rolloutEnabled"]).lower()};</script>')
    head_boundary = re.search(r"</head>\s*<body\b", page, re.I)
    if not head_boundary or re.search(r"</head>\s*<body\b", page[head_boundary.end():], re.I):
        raise CandidateHostError("MCEL presentation head boundary invalid")
    page = page[:head_boundary.start()] + preflight + page[head_boundary.start():]
    try:
        source = (repo / HOOK_JS).read_text(encoding="utf-8")
        if "</script" in source.lower():
            raise CandidateHostError("Unsafe MCEL presentation selection hook")
    except (OSError, CandidateHostError) as error:
        LOGGER.warning("MCEL presentation hook unavailable for %s: %s", app_id, error)
        return page
    call = json.dumps({"appId":app_id,"rootId":root_id},separators=(",", ":"))
    hook = ('<script>\n' + source + '\n</script>' +
            '<script>(function(){try{window.__mcelPresentationSelection=' +
            f'window.McelApplicationPresentationRegistry.connect({call});' +
            '}catch(e){window.__mcelPresentationSelection={status:"registry-unavailable",reason:String(e)};}})();</script>')
    return page.replace(marker, hook + marker)
