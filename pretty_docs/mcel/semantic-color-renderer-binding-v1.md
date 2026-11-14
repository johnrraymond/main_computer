# MCEL Canonical Color Binding v1 — renderer integration (Patch 2)

The normal `McelSemanticOperationalRuntime` renderer now loads the canonical Color Law and a *single* generic `McelSemanticColorPresentation` consumer. All official semantic application hosts load these modules before the operational runtime. Binding uses exact source IR identities (`data-mcel-node` and `data-mcel-region`), never labels or button order.

The Color Law validator runs **before mount DOM mutation**. A declaration with an unknown role or illegal state combination is refused. A declared color also requires the Theme's full computed palette, either inherited from an owning Theme context or supplied explicitly by the host (`colorPalette`); no application color fallback is invented. This is a staging boundary until the shared Theme application path (Patch 3) is implemented.

Once mounted, the renderer automatically binds the authored declaration to a canonical control's real button, a form's submit button, a state-value output, a plot surface, or a declared region. The controller generates native CSS for normal, hover, pressed, focus-visible, high contrast, and forced colors. The application cannot change these physical colors. Availability (`disabled`), selection (`aria-pressed` / `aria-selected` projected from canonical runtime state), and capability request lifecycle (`pending` / terminal outcome) trigger recompilation only on semantic state changes. Pointer motion never invokes JavaScript color handlers or writes inline colors. Theme change is explicit through the host's `setColorTheme(theme,palette)` API.

### Legacy safety

A canonical record without `color` remains on the existing CSS path; `colorPresentation()` returns `legacy-unmigrated` when no authored colors exist. This is deliberate until the Calculator migration and the zero-legacy-color enforcement gate. No implicit operation name, label, or risk is used to guess `action.start` or `action.stop`. Generated layout regions do not necessarily preserve authored region identity; those declarations are explicitly refused until a canonical source binding exists. Source-bound control and form declarations are unaffected by generated composition.

This patch **does not** replace the Lab-only Theme projector or the Evaluate demonstration. Those are retired after the shared Theme integration (Patch 3), and they remain independent of the canonical renderer's new path. It does not recolor E/W/G, graph-series paths, or application-owned legacy controls.

### Acceptance

`tests/test_mcel_semantic_color_renderer_v1.py` proves automatic Evaluate binding from a synthetic authored IR declaration, native hover, retained calculation result, unchanged identity and geometry on Theme change, zero painting for an unmodified Calculator, and early refusal of an invalid role. All official semantic host loaders must include the Color Law and presentation module ahead of `mcel-semantic-operational-runtime.js`.
