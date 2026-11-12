# MCEL — isolated semantic-composition negotiation smoke

**Status:** Experimental feasibility test only. It makes **no production changes**.
Do not promote this solver or its browser injection directly into the MCEL runtime.

## Purpose

Test whether authentic application semantics can choose a useful composition by
negotiating spatial requirements, without assigning all results to region 3 or
shipping Calculator-specific CSS. The 1/2/3 structure is an *available candidate*,
not an authored assignment or a predetermined purpose-to-column mapping.

## Execute (from repository root)

```powershell
python -m pytest -q tests/test_mcel_composition_negotiation_smoke.py
python -m tools.mcel_composition_negotiation_smoke --browser --report runtime/reports/mcel/composition-negotiation-smoke.json --screenshots runtime/reports/mcel/composition-negotiation-images
```

The second command launches the real paired Calculator DSL, renders it with the
real operational runtime, runs the actual graph capability, applies temporary
experimental layout changes to the browser document, and runs the capability
again. It does not modify source files or persistent application state.

## What is proved—and not proved

* The **producer/result relationship** is derived from compiled intent
  `resultBindings` and state reads, not headings, sourceName, app ID, or DOM order.
* The experimental solver enumerates stacked, two-region and three-region
  alternatives and ranks them using typed minimums and diminishing returns to
  extra width. Scalar results may remain a compact working unit.
* The smoke rejects ambiguous or broken dataflow, illegal dimensions, clipped
  navigation labels, overlapping form fields, CSS-visible `hidden` regions,
  horizontal viewport overflow, and nonfunctional graph updates.
* A second, semantically different result presentation (collection) is tested.
  Labels/app identity may be changed without changing decisions.
* Viewport and intrinsic-size behavior are tested and the width sweep must not
  oscillate between incompatible arrangements.
* A live Chromium trial verifies the actual graph still redraws, mode selection
  still works, and the computed right-hand result surface is usable.

This **does not prove** the production renderer already negotiates. It does not.
The temporary JS reparenting and prototype scoring are disposable proof-of-concept
mechanisms. The negotiated score weights and intrinsic contracts are hypotheses,
not a final product objective function. Nor is this a full cross-app layout
certification; it is a smoke gate preceding deeper acceptance work.

## Baseline defect exposed

In the captured snapshot, a Basic-mode result is marked `hidden` while the
Calculator is in Graphing mode, but an `!important` compact-results CSS rule
still renders it. The browser smoke detects the **baseline defect**, then applies
an explicit test-only hidden-attribute correction in its temporary trial and
verifies all hidden regions remain visually hidden through mode transitions.
Production must fix the underlying CSS contract before promotion.

The original graphing form also becomes unusable if squeezed into a narrow
column without negotiating its child fields. The test reflows fields using
**field kinds** from the compiled IR (text spans, numeric fields pair), and
asserts no visible field-label/action overlaps. The production form-layout
engine must own that transformation, rather than copying browser-test code.

## Promotion gates

Before implementation is accepted in MCEL production, require:

1. A single composition owner shared by natural and workspace-planned mounting,
   with the original authenticated IR and semantic capability authority intact.
2. Generic widget intrinsic/usable dimensions and child reflow contracts,
   extracted from actual rendered elements and typed metadata, not hardcoded
   Calculator identities, field names, or fixed viewport breakpoints.
3. A bounded deterministic candidate search with explicit rejection reasons,
   stable results around thresholds, and no changes to application operations.
4. Browser acceptance for this graphing/Basic contrast **and independent
   applications** (File Explorer, Task Manager, Code Editor, Chat Console, SLH).
5. Correct visibility (`hidden`), accessibility/navigation, scrolling, no
   clipped or overlapping child elements, and no unexpected cross-app geometry
   changes. All tests pass both in shell and standalone modes.
6. Only after those gates: a separate production patch. This smoke carries no
   permission to change the production renderer.

## Full canonical application battery / blast-radius gate

The original calculator-only smoke is NOT a production readiness gate. The
cross-app gate now enumerates the actual canonical registry and tests **every
one of its 21 destinations** at 390/800/1260px, plus the two SLH local/network
targets which live outside the shell registry.

Run the complete smoke with the **same command** as before:

```powershell
python -m tools.mcel_composition_negotiation_smoke --browser --report runtime/reports/mcel/negotiation-full-battery.json
```

Or run the battery alone, omitting the previous Calculator browser trial:

```powershell
python -m tools.mcel_composition_battery_smoke --browser --report runtime/reports/mcel/negotiation-battery-only.json
```

A `PRODUCTION GATE: BLOCKED` result deliberately exits **2**, even if every
baseline guard passes. Exit 1 means a genuine guard or browser test failure.
`--calculator-only` on the original command runs the earlier isolated
experiment and is **never** authority for production promotion.

The battery checks:

- Exact canonical application inventory and stable browser-native links for
  21 destinations × 3 viewport widths; no invented or skipped apps.
- The actual HTML/JS semantic hosts for Calculator, File Explorer, Task
  Manager, Code Editor, and Chat Console in Chromium at each width, with
  captured layout zones, visible regions, scrolling, overflow, mount state,
  and a byte-verified, no-mutation geometry baseline.
- Both SLH target pages using real shared semantic runtime and generated
  composition, with all browser network requests refused; no SLH commands,
  administrator login, or worker effects are exercised.
- Real authored legacy markup in an **inert** browser DOM for each available
  legacy source, with no execution of legacy scripts or backend actions.
  This verifies source integrity, not full legacy application functionality.
- The original form/result solver remains as a negative-control baseline.
  The new generalized solver generates candidates for every semantic target
  and trials those arrangements on the real bound browser surface.
- The prior Calculator graphing browser trial still moves real bound nodes and
  verifies computation, navigation and graph redrawing after reflow.

Current snapshot-specific gaps, to be resolved before production promotion:

1. The seven semantic-target trial paths now have generic candidate families
   and browser reflow evidence, but they remain *experimental* and are not
   production-level operational/workflow certification.
2. `game_projects/webgl-demo` and `applications/apps/onlyoffice.html` are absent
   from this `main` snapshot, so the live WebGL/ONLYOFFICE surfaces cannot be
   certified here. The report records them as NOT_VERIFIED. Re-run against
   complete app/game exports before interpreting an eventual green gate.
3. The sandbox blocks local browser navigation. Route selections and their
   canonical iframe URLs are checked in Chromium, but children are rendered
   through the original standalone HTML in a real browser document, not over
   an authenticated live local HTTP server. A later host integration lane
   must verify those exact routes and authenticated service boundaries.
4. Legacy fragments are parsed without running their scripts; features that
   depend on provider services, external libraries, or deployment hardware
   require a separate live integration/acceptance lane.
5. Browser network effects are refused in this diagnostic. This does not
   prove operational continuity for applications that require backend calls.

**Promotion policy:** A green experimental semantic battery does not authorize
production. Every target needs production-owned composition plus live
operation-level parity, and the separate legacy/canonical integration checks
must be certified. This smoke always reports production promotion blocked. Never
silently replace an unsupported application with a stub composition.

## Generalized multi-region trial (v2, isolated)

The experiment now also includes `tools/mcel_general_negotiation_smoke.py`.
Unlike the original single input/result prototype, the generalized solver
accepts **all seven semantic targets** from their compiled Application IR:
Calculator, File Explorer, Task Manager, Code Editor, Chat Console, and both
SLH local/network views. It requires no app-ID, route, English heading, or
fixed Calculator placement rule in its candidate search.

- Existing HTML `purpose`, generated DSL `role`, and nested parent/child
  hierarchy normalize into navigation, main, and delta **proposals**, not
  mandatory column addresses. Multi-region groups aggregate horizontal demand
  by usable minimum; they are not incorrectly summed across vertical siblings.
- Bounded-content controls and visual/collection/document surfaces have distinct
  typed size/growth curves. Stacked, two-region and three-region compositions
  compete under a deterministic score. These costs are experimental hypotheses.
- Each semantic browser host is trialed at **390, 800, and 1260px**. The 390px
  layouts preserve the existing single-column fallback; the other two widths
  trial actual track allocations using the real rendered DOM. Some hosts change
  from a two-to-three-column or one-to-two-column topology, where their semantic
  zones allow it. Chat Console's nested thread/workspace grid is handled through
  the same parent-role resolver, not a Chat-specific selector.
- The visual-result test physically moves the **original plot region** into its
  allocated result surface, then reflows its typed text/numeric form fields.
  The graph's bound state and events survive moving the same DOM node. The
  browser redraws `sin(x)` as `cos(x)` after the change, proving operational
  continuity for that bounded Calculator path.
- Browser acceptance compares region identities, visible regions, interactive
  element counts, child-form overlaps, independent-region overlaps, clipped
  controls, and horizontal overflow **before and after** every trial. A changed
  allocation, rather than an inert render, is required to count a `TRIALED`.
  Unknown host structures or new defects are rejected, not skipped as passes.
- Negative tests reject missing/cyclic hierarchy, dangling DSL relations,
  forged surface-node membership, invalid viewports and incorrect surface
  contracts. Renaming an app or heading does not change the chosen allocation.

**Two separate outcomes are reported.** `EXPERIMENTAL GATE: PASS` means all
seven semantic targets completed the controlled trials at the tested widths
without new measured layout defects. `PRODUCTION GATE: BLOCKED` remains in force
because the production renderer has **not** adopted the negotiator, the 15
legacy apps have only inert-markup or shell checks, two legacy fragments are
absent from this export, and the other six semantic targets have not received
full workflow/effect-level acceptance against live services.

Exit code **2** remains intentional when running the complete `--browser`
command while production promotion is blocked. Exit code **1** identifies a
failed browser/geometry guard. Consult the JSON report's distinct
`experimentalGate`, `productionGate`, `runtime`, and `negotiationTrial` fields.

Run:

```powershell
python -m pytest -q tests/test_mcel_general_negotiation_smoke.py tests/test_mcel_composition_battery_smoke.py tests/test_mcel_composition_negotiation_smoke.py
python -m tools.mcel_composition_negotiation_smoke --browser --report runtime/reports/mcel/general-negotiation-experimental.json
```

**The experimental browser injection is not production code.** Its reparenting,
child-field CSS, topology changes, and score weights must be replaced with a
single MCEL-owned negotiation pipeline, followed by full cross-application
operation and authorization tests before any production switch.
