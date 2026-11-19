# MCEL Icon v1: canonical icon library

## Authority and scope

`main_computer/web/applications/icons/mcel-icons.v1.json` is the **single canonical geometry and identity registry**. It uses `mcel.icon-registry.v1`, a fixed 24×24 coordinate system, and a deliberately limited set of SVG primitives and attributes. Neither Python nor JavaScript has an independent list of drawings.

The initial catalog contains 102 identities in seven families:

| Family | IDs | Purpose |
| --- | ---: | --- |
| `application` | 21 | All original desktop icons; existing SVG shapes preserved |
| `frober` | 14 | Frob identities, e.g. layout, functional, CSS, regression |
| `action` | 21 | Run, pause, stop, inspect, reconcile, deploy, edit, etc. |
| `resource` | 11 | Files, folders, processes, Workers, hubs, network |
| `status` | 9 | Passed, failed, pending, warning, offline, etc. |
| `navigation` | 10 | Back, forward, home, expand, close, etc. |
| `interface` | 16 | Selection, grid, list, calendar, lock, help, etc. |

**Icon meaning:** `action.deploy` and `action.undeploy` represent the lifecycle of a service instance using server-and-plus and server-and-cross badges, respectively. `action.download` and `action.upload` exclusively represent file/data transfer, retaining their arrow designs. `frober.accessibility` uses a universal-access figure enclosed in a circle, with no extra centerline below the hips.

**The object icon is not the object's current status.** For example `frober.layout` remains the same icon when its execution result changes. If the interface needs a result indicator, it composes a separate `status.pass` or `status.fail` icon. Likewise, MCEL Icon does not control hover, pressed states, navigation, action dispatch, permissions, theme profiles, or application execution.

## Rendering API

Python (server-generated markup):

```python
from main_computer.mcel_icon import icon_ids, get_icon, render_icon

icon_ids(family="frober")
get_icon("application.calculator")
markup = render_icon("frober.layout", size=24)                  # decorative
markup = render_icon("frober.layout", aria_label="Layout frober") # announced
```

Browser (independent script, canonical JSON supplied by caller):

```html
<script id="mcel-icon-registry" type="application/json">...canonical JSON...</script>
<script src="/applications/scripts/mcel-icon.js"></script>
<script>
  const icons = MCELIcon.fromDocument();
  const markup = icons.svg("action.run", {size: 20});
  const svg = icons.element("resource.folder", {size: 24});
  icons.mount(document.getElementById("target"), "status.pass", {size: 16});
</script>
```

Use the installed application's real asset routing for the JS, CSS, and registry. Promoted semantic applications now receive this registry as inert JSON via the *shared MCEL host*, so no application-specific fetch or icon annotation is required. Standalone catalog consumers still use the explicit adapter API.

`mcel-icon.css` provides neutral default sizing and color inheritance. Parent controls own their visual states and focus behavior. Rendering is decorative (`aria-hidden`) by default when placed inside an already-labeled button, link, or tree item. Pass an explicit `ariaLabel` / `aria_label` only if the icon itself needs an accessible name.

Unknown IDs, duplicate IDs, unsafe SVG attributes, and malformed options are refused instead of silently substituted. No external icon fonts, CDNs, fetches, inline event handlers, or external SVG resources are used.

## Preview and verification

```powershell
python -m tools.mcel_icon_catalog --output runtime/reports/mcel-icon-catalog.html
python -m pytest -q tests/test_mcel_icon.py
```

Open the generated `runtime/reports/mcel-icon-catalog.html` in a browser to inspect all icons, filter by family or name, and change preview sizes. It is a standalone, offline catalog. It has no effect on the current desktop. The acceptance suite checks the 21 imported drawings against their source, Python/Node parity across all 102 icons, registry validation, and a browser catalog smoke test when Playwright/Chromium are available.

## Desktop integration (v1 first consumer)

The canonical `/applications` desktop (including the MCEL Applications Shell
preview) now renders its 21 application SVGs by calling the Python
`render_icon("application.<app>", class_name="desktop-glyph-svg")` adapter.
This is the **production icon authority** for the desktop, not a copied SVG
map. Each SVG exposes its semantic `data-mcel-icon-id` and retains the existing
`desktop-glyph-svg` class, visual sizing, native anchor, desktop grouping,
accent palette, and launch behavior. Missing icons raise `IconRegistryError`
rather than silently rendering a legacy glyph. The backend emits inline SVG,
so it does not depend on a browser fetching a registry or icon script.

`desktop-icons.js` and its SVG drawings remain **only as source-bound legacy
reference material** for the independent visual parity oracle and the older
native child page. The modern `/applications` desktop does not render those
legacy drawings. No pinned legacy reference sources, routes, or CSS files have
been rewritten for this integration.

```powershell
python -m pytest -q tests/test_mcel_desktop_icon_integration.py tests/test_mcel_icon.py tests/test_mcel_desktop_clusters.py
```

## Automatic semantic icon core (November 18 pilot)

The shared `mcel-semantic-icon-core.js` resolves icon identities from canonical
operation metadata and existing exact runtime node/intent bindings. It never
matches button labels, app names, DOM order, CSS class names, or an oracle file.
MCEL Core owns the icon identity and insertion; Theme, Form, and Chrome provide
independent appearance overrides via inherited CSS variables. No `icon` profile
is introduced. Unknown/conflicting operations receive no icon, not a guess.

The common promoted runtime host injects the existing JSON registry, icon CSS,
and JS adapter and invokes the icon core through the established unified
presentation controller. Application DSL, UI components, and operation code
remain untouched. Managed slots are decorative, idempotent, and removed on
shared-controller release. Missing optional icon assets do not block the
existing three presentation laws or application operations.

The independent `tests/fixtures/mcel-semantic-icon-oracle.v1.json` prescribes
expected icons (including deliberate unmapped nodes) for every canonical
control and form in **File Manager and Task Manager**. Chromium mounts the
actual promoted applications with mapping active, compares the rendered
canonical SVG IDs with the oracle, checks profile changes and click handlers,
and verifies incremental replacement and cleanup. The present pilot certifies
13 mappings and 9 intentional non-mappings in these two applications.

```powershell
python -m pytest -q tests/test_mcel_semantic_icon_oracle.py tests/test_mcel_system_presentation_rollout.py tests/test_mcel_icon.py
```

The remaining opportunities include more specific semantic classification for
unmapped controls, the older native child desktop's browser renderer, file-tree
icons, and frober identity/status displays. Those should receive their own
independent oracles before expanding automatic mappings. Preserve the original
reference fixtures instead of treating legacy geometry code as authority.
