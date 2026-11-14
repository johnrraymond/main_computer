# MCEL Semantic Color Law v2 — complete visual-state foundation

**Status:** Pure, additive resolver and CSS compiler with a read-only Lab inspection. **No application-action consumer is activated.** The accepted E/W/G widget continues to use Color Law v1. The standalone Calculator, SLH administration, desktop icon clusters, and Default theme behavior are unchanged.

## Ownership and intent

MCEL applications declare **what an element does** and any persistent operational state. Interaction state is owned by the input/browser layer. Theme owns the palette. Color Law composes their meanings into colors, not Form geometry or Layout placement.

`McelLabStyleLaw.resolveColorTreatment(...)` is a pure decision function; `McelLabStyleLaw.compileColorStateCss(...)` is a pure CSS-generation function. They accept no DOM root and perform no mutations or event binding. Neither is called by any existing application control.

The existing `resolveColor({theme,role,state,surface,palette})` is unchanged, including its E/W/G treatment. The new API does **not** infer purpose or consequence from a label, icon, HTML tag or CSS class.

## Inputs and independent state dimensions

```js
const treatment = McelLabStyleLaw.resolveColorTreatment({
  theme: "theme-machine",
  role: "action.stop",
  surface: "control",
  palette: computedThemePalette,
  consequence: "reversible",
  availability: "available",
  selection: "on",
  operation: "pending",
  context: "toolbar",
  interaction: {hovered:true, pressed:false, focused:true, focusVisible:true},
  environment: {contrast:"normal",colorScheme:"auto",forcedColors:false,reducedMotion:false}
});
```

| Dimension | Accepted meanings | Authority |
|---|---|---|
| `role` | Existing canonical Color Law roles, e.g. `action.start`, `action.stop`, `diagnostic.warning` | Canonical application semantics |
| `consequence` | `unspecified`, `routine`, `reversible`, `destructive`, `critical` | Application's explicit consequence, **not** a guessed risk from action name |
| `availability` | `available`, `disabled`, `unavailable` | Operational control availability |
| `selection` | `off`, `on`, `mixed` | Persistent selection semantics |
| `operation` | `idle`, `pending`, `succeeded`, `failed` | Runtime operation state |
| `context` | `default`, `toolbar`, `dialog`, `form`, `navigation`, `nested`, `inline` | Semantic presentation context |
| `interaction` | Booleans `hovered`, `pressed`, `focused`, `focusVisible` | Browser/native input; keyboard focus-visible is distinct from focus |
| `environment` | `contrast: normal/more`, `colorScheme: auto/light/dark`, `forcedColors: boolean`, `reducedMotion: boolean` | User/environment and the selected Theme |

The resolver returns semantic provenance, the recognized states, and independent **foreground, background, border, accent, focusRing, selection, and status** color channels. The latter two channels preserve the meaning of a selected or pending/failed control even when its surface is hovered or pressed.

A `disabled` button is not the same as an `action.disable` control. A `stop` action is **not assumed destructive**: its consequence must be explicitly declared. Interaction `pressed` on an unavailable control and `focusVisible` without focus are refused. Unknown inputs, unsupported enum values, and consequence applied to a non-action role are refused rather than silently given colors.

## Deterministic composition contract

The solver composes a bounded base, selection, pointer, and operational-status emphasis into surface colors. Hover changes a selected surface without erasing selection; press takes precedence over hover; focus ring is a separate independent channel. Availability blocks actionable pointer coloring but does not erase an operational-status or selection signal. Color alone is never a substitute for accessible labels or the application’s semantic/operational state.

For normal Theme palettes, diagnostic foreground color is used only when its contrast against the *composed* tinted surface is sufficient. A pure deterministic sRGB calculation checks the known CSS hex/rgb(a) tokens, conservatively against black and white backing surfaces for translucent cards. The decision uses a safety margin above 4.5:1 and otherwise uses the Theme's readable ink, **not** a widget-defined emergency color. Diagnostic meaning remains in status and border channels. Unknown palette formats cannot be emitted as paint-capable CSS; custom palette support requires a corresponding contrast oracle. The browser oracle validates 4.5:1 actual contrast across eight current palettes and composed states.

`compileColorStateCss({id,theme,role,surface,palette,consequence,availability,selection,operation,context})` produces scoped rules for `[data-mcel-color-state="<id>"]` where `<id>` is a strictly validated token. A **concrete computed Theme palette is required** before emitting CSS; token expressions alone may be inspected but not emitted as a verified stylesheet.

The compiler generates deterministic CSS for resting, hover, active, focus-visible, and their permitted combinations. Hover applies only under `(hover: hover) and (pointer: fine)`; coarse touch remains able to show `:active` without a sticky hover appearance. `@media (prefers-contrast: more)` and `@media (forced-colors: active)` honor system preferences. No mouse/keyboard/touch event listener or runtime DOM style-write loop is used. Selection, availability, and operational state are compiler inputs: a future consumer will recompile on *semantic state changes*, not on pointer motion.

Focus-ring **color** is resolved here. The owning Form implementation must supply suitable focus outline width/style so that the color is visible. Likewise, color transitions are not enabled in this foundation; reduced-motion preference is recorded but no motion is introduced. `colorScheme` records environment intent; physical light/dark palette selection still belongs to the selected Theme, not a synthetic second palette inside Color Law.

## Oracle and migration gate

`tests/test_mcel_semantic_color_visual_state.py` checks:

- State combinations, precedence, invalid combinations, deterministic repeated results, frozen outputs and CSS identity safety.
- Actual Chrome `:hover`, mouse-down/up, keyboard Tab focus-visible and Space activation without JS painting or DOM remounting.
- Coarse touch without persistent hover painting and operating-system forced colors.
- Selection and pending/failure/success channels retained while semantic state changes; no geometry changes.
- All eight actual Theme palettes, including translucent cards, with rendered foreground/background contrast under selected, hover, pressed, failed, and destructive combinations.
- A read-only, collapsed Action visual-state matrix nested inside the existing Lab Color Law inspector; no changes to the mounted Calculator/E/W/G computed styles.

**Gate for the next patch:** A real action-control consumer must source its role, consequence, availability, selection, operation, and context from canonical semantics; update generated rules only when persistent state changes; remove competing legacy application colors; coordinate focus outline *shape* with Form; and meet full keyboard/mouse/touch/contrast acceptance. Do not migrate Start/Stop/Enable/Disable/Inspect in this patch.
