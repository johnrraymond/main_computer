# MCEL System Guide

MCEL is the Main Computer semantic interface layer. It lets a page describe product intent in clean source HTML and then lets the runtime generate, prove, repair, and serialize the working interface without pushing runtime machinery back into the source.

Use this guide when you need to understand whether MCEL is the right layer for a feature, how to verify that it is working, or how to extend it without weakening the proof model.

## The short version

MCEL is not just a component helper. It is a contract boundary between:

- **source meaning**: the author-owned HTML and `data-mc-*` traits that describe components, state, actions, layout, forms, routes, accessibility, and policies;
- **runtime machinery**: generated parts, measurements, browser observations, repairs, proofs, and evidence packets;
- **serialization**: the clean source that can be saved or exported without leaking runtime-only facts.

The working rule is:

> MCEL may replace a legacy UI library only when the feature has source traits, runtime laws, browser evidence, repair behavior, serialization cleanup, and tests that prove the replacement is safer than the old glue.

That is the reason the platform spine exposes the subsumption lattice and the adoption case gate. The lattice says what MCEL wants to replace; the adoption case says what must be proven before that replacement is justified.

## User-space contract and application authoring

The user-facing contract is documented in `pretty_docs/mcel-user-space-contract.md` and exposed by `McelLabContract.buildUserSpaceContract()` / `MCEL.buildUserSpaceContract()`.

The application-authoring model across HTML, application contract data, JavaScript behavior, layout hints, user layout operations, themes, chrome, and FLOG is documented in `pretty_docs/mcel-application-authoring.md`.

The Code Editor requirements contract is documented in `pretty_docs/mcel-code-editor-requirements.md`. It is the documentation-first source for Code Editor product laws, regions, intents, safety boundaries, and acceptance criteria.

The Git Tools requirements contract is documented in `pretty_docs/mcel-git-tools-requirements.md`. It is the documentation-first source for repository evidence, governed push, project-level publishing, remote-sync boundaries, file triage, recovery receipts, semantic intent coverage, and the current scope-limited adapter status.

The Calculator requirements contract is documented in `pretty_docs/mcel-calculator-requirements.md`. It is the documentation-first source for deterministic local compute, graph canvas ownership, Mathics evaluation boundaries, model-helper boundaries, result evidence, and the planned small full-semantic reference adapter.

Application-local layout contracts are now live in Code Editor and Git Tools. They parse `data-mc-layout-*` hints, resolve dock trees, persist semantic preferences, and expose app-specific controllers. They are not yet generic `window.MCEL` guarantees. The authoring guide documents that boundary and the rules for promoting a proven application behavior into the platform contract.

Use the user-space contract, not internal law names, when deciding whether a builder workflow can rely on MCEL. The current planning rule is: source traits are the durable input, generated runtime structure is discardable, serialization is the source firewall, repair is bounded regeneration, reports are gates, browser facts are snapshots, and adoption is narrow and reversible.

## Where MCEL lives

| Area | File or entry point | Purpose |
| --- | --- | --- |
| Lab app shell | `main_computer/web/applications/apps/mcel-lab.html` | User-facing MCEL Lab UI. |
| Lab orchestration | `main_computer/web/applications/scripts/mcel-lab.js` | Binds controls, renders reports, runs diagnostics, and coordinates state. |
| DOM bindings | `main_computer/web/applications/scripts/dom-bindings/mcel-lab.js` | Loads the Lab when the app route is mounted. |
| Public facade | `main_computer/web/applications/scripts/mcel-core.js` | Stable `window.MCEL` API. |
| Contract | `main_computer/web/applications/scripts/mcel-contract.js` | Attribute names, defaults, schemas, and source contract constants. |
| Compiler and repair engine | `main_computer/web/applications/scripts/mcel-engine.js` | Compiles source, rebuilds runtime parts, repairs damage, and serializes clean source. |
| Law registry | `main_computer/web/applications/scripts/mcel-law-registry.js` | Registers and lists law descriptors. |
| Platform spine | `main_computer/web/applications/scripts/mcel-platform-spine.js` | Runs cross-law proof, builds the subsumption lattice, and builds the adoption case. |
| Browser proof | `mcel-browser-observer.js`, `mcel-browser-runner.js` | Captures browser facts and turns them into proof evidence. |
| Code Editor layout contract | `main_computer/web/applications/scripts/code-editor-layout-contract.js` | Live application-local dock tree, semantic preferences, owned-track fill, and generated containment. |
| Git Tools layout contract | `main_computer/web/applications/scripts/git-tools-layout-contract.js` | Live application-local repository workflow layout and semantic preferences. |
| Live Code Editor FLOG | `main_computer/flog_code_editor_live_smoke.py` | Verifies owned center fill, containment, control interception, proof-dock reclamation, and user-layout states. |
| Runtime packager | `main_computer/mcel_runtime_package.py` | Builds the single-file runtime used by Website Builder exports. |
| Checked-in runtime | `deploy/local-platform/site-runtimes/mcel-runtime.js` | Generated browser runtime for local platform exports. |
| Hub runtime copy | `runtime/websites/hub-site/runtime.js` | Runtime copy used by the Hub site. |

## How the Lab is supposed to be used

1. Open **MCEL Lab** from the Applications UI.
2. Start with a scenario or write clean semantic HTML in the source editor.
3. Click **Compile** to turn source into runtime DOM.
4. Use **Runtime**, **Diff**, **Stress**, and **A11y** modes to inspect the generated surface.
5. Use **Repair** after deliberately damaging runtime parts to confirm the source can regenerate them.
6. Use **Serialize** to verify runtime-only facts do not leak back into saved source.
7. Run the diagnostics drawer:
   - **Run Full Contract Suite**
   - **Run Scenario Matrix**
   - **Run Full Acid Suite**
   - **Run Operational Audit**
   - **Build Evidence Packet**
   - **Run Autopilot Proof**
   - **Build Traceability Map**
   - **Build Subsumption Lattice**
   - **Build Adoption Case**
   - **Run Browser Semantic Proof**

The Lab is useful only when these controls agree with one another. A compile that looks good but fails serialization, proof, or repair is not a successful MCEL feature.

## Core concepts

### Clean source

Clean source is the source of truth. It should contain semantic HTML and source-owned `data-mc-*` traits. It should not contain generated parts, browser measurements, proof output, repair state, or internal runtime facts.

### Runtime DOM

The runtime DOM is allowed to be noisy. It can have generated wrappers, parts, measured facts, layout helpers, browser-derived details, and proof metadata. That machinery is acceptable only because MCEL can regenerate or discard it.

Application-local layout compilers may emit `data-mcel-layout-*` traits to describe the resolved runtime hierarchy. Those traits are discardable runtime output. Durable author intent remains in `data-mc-layout-*`.

### Owned layout slots

A layout unit owns a slot, not an arbitrary portion of the whole viewport. Fill and phase-floor measurements must use the owned slot as the denominator.

```text
owned slot
→ semantic unit
→ active pane
→ primary surface
```

Sibling docks and chrome are separate owners. MCEL layout verification must also identify the intentional scroll owner and prevent descendants from painting or forcing intrinsic size outside their owned track.

### Laws

A law is a focused rule system for a domain such as components, state, data, forms, actions, rendering, accessibility, performance, style, layout, or chrome. Laws read source traits, generate runtime facts, produce reports, and define proof obligations.

### Proof obligations

A proof obligation is the specific thing MCEL must show before claiming a domain is handled safely. For example, the form law must prove that validation and error display are accessible and that runtime dirty state does not pollute source.

### Evidence packets

Evidence packets collect contract results, law reports, browser observations, traceability, and risk signals into one artifact. Treat an evidence packet as the minimum useful object for deciding whether an MCEL feature is ready.

### Serialization firewall

The serialization firewall is the rule that source remains clean even after the runtime has generated structure, observed browser facts, or repaired damage. If serialization leaks runtime-only data, the feature has failed an important MCEL boundary.

### Repair

Repair is runtime recovery, not source mutation. MCEL can rebuild canonical generated parts when they are missing or damaged, but that does not mean it should silently change the author-owned source.

### Subsumption lattice

The subsumption lattice maps old libraries and glue patterns to MCEL law axes. It is a planning artifact, not proof by itself. It says what MCEL is trying to make obsolete and which proof surfaces are required.

### Adoption case

The adoption case is the gate that keeps the lattice honest. MCEL should be adopted for a replacement only when the claim is backed by law plans, proof obligations, evidence packets, browser facts, traceability, tests, and supervisor gating.

A healthy adoption case has this shape:

```text
legacy library or glue pattern
  -> MCEL law axis
  -> source traits
  -> compiler hook
  -> runtime law
  -> browser observation
  -> acid proof
  -> evidence packet
  -> serialization check
  -> supervisor gate
  -> adoption verdict
```

## Law domains

| Law | File | Replaces | What must be proven |
| --- | --- | --- | --- |
| Component / Slot / Prop Law | `mcel-component-law.js` | React, Vue, Svelte, Lit/Web Components | Component identity is source-owned, slots stay stable, generated decorations stay runtime-owned, and serialized source stays clean. |
| State Ownership / Replay Law | `mcel-state-law.js` | Redux Toolkit, Zustand, MobX, XState-only islands | Mutable state has an owner, derived state is not serialized, state boundaries are explicit, and replay can emit evidence. |
| Data Query / Cache / Sync Law | `mcel-data-law.js` | TanStack Query, SWR, Apollo cache glue, ad hoc fetch effects | Queries have cache policy, mutations have sync/error policy, runtime freshness is not serialized, and offline/optimistic behavior emits evidence. |
| Form / Validation / Error Law | `mcel-form-law.js` | React Hook Form, Formik, Yup-only wiring, custom dirty-state code | Validation and error policies are visible and accessible, dirty state is runtime-only, and generated errors do not pollute source. |
| Action / Event / Swap Law | `mcel-action-law.js` | htmx attributes without proof, imperative event handlers, manual DOM swaps | Actions have event policy, targets are named, swaps stay in lawful regions, and runtime action safety is stripped on serialize. |
| Route / Render / Hydration Law | `mcel-render-law.js` | Next.js render modes, Astro islands, manual hydration boundaries, framework file routing | Interactive islands declare hydration policy, cache policy is named, offline render has state/data policy, and hydration proof is runtime-only. |
| Accessibility / Focus Law | `mcel-a11y-law.js` | Late lint-only accessibility, manual focus traps, ARIA afterthoughts | Strict surfaces have labels, focus traps have boundaries, scroll regions are keyboard reachable, and generated decoration is hidden from assistive tech. |
| Performance / Security Budget Law | `mcel-performance-law.js` | Ad hoc Lighthouse cleanup, budget plugins without semantics, manual CSP notes | Critical budgets avoid eager hydration, user-content surfaces are explicit, networked regions declare data policy, and budget risk is runtime-only. |
| Layout / Overflow / Scroll Law | `mcel-layout-law.js` | Nested-scroll glue and layout overflow fixes | Scroll ownership, overflow policy, containment, and observed layout facts agree with source intent. |
| CSS Law Runtime | `mcel-style-law.js` | Theme drift and manual token sprawl | Theme tokens, density, spacing, and generated CSS remain lawful and serializable. |
| Chrome Law | `mcel-chrome-law.js` | Hard-coded page chrome variants | Chrome hierarchy, editorial flow, object growth, and object reshape policies stay consistent. |

## Public API quick reference

The stable browser entry point is `window.MCEL`.

| API | Use it for |
| --- | --- |
| `MCEL.compile(source, options)` | Compile clean source into runtime DOM and reports. |
| `MCEL.serialize(root, options)` | Return clean source without runtime-only generated parts. |
| `MCEL.repair(root, options)` | Rebuild missing generated runtime parts. |
| `MCEL.audit(root, options)` | Run operational checks across the current runtime. |
| `MCEL.inspect(root, options)` | Inspect source/runtime facts for debugging. |
| `MCEL.buildEvidencePacket(root, options)` | Build a combined evidence artifact for the current source/runtime. |
| `MCEL.runProof(root, options)` | Run platform-level proof over the available laws. |
| `MCEL.buildSubsumptionLattice()` | Describe which legacy libraries/glue patterns each law intends to replace. |
| `MCEL.buildAdoptionCase(options)` | Convert the replacement thesis into a gate-based adoption verdict. |
| `MCEL.runScenarioMatrix(options)` | Exercise scenario coverage from the Lab. |
| `MCEL.runAcidTests(options)` | Run selected or full acid-test coverage. |

Do not bypass the facade from app code unless you are working inside the Lab or a law module. The facade exists so Website Builder exports and tests can depend on a stable surface while internal modules keep changing.

## Runtime package workflow

After changing MCEL runtime modules, rebuild the checked-in runtime:

```powershell
python tools/build_mcel_runtime.py
```

Expected output includes the runtime path and version, for example:

```text
wrote deploy\local-platform\site-runtimes\mcel-runtime.js (... bytes)
version mcel-runtime.v0.1.9
```

Then run the targeted tests:

```powershell
python -m pytest tests/test_mcel_architecture_boundaries.py tests/test_mcel_lab_app.py tests/test_mcel_runtime_package.py -q
```

On machines where browser-conditional checks are unavailable, some tests may skip. Skips are acceptable only when they are explicit environment skips, not failures.

## Extension checklist

Use this checklist before adding or modifying an MCEL feature.

- Define the source-owned traits first.
- Decide which law owns the behavior.
- Register descriptors through the law registry when the behavior is cross-cutting.
- Keep browser observations runtime-only.
- Add or update proof obligations.
- Add an evidence-packet path that shows the proof result.
- Add a repair path only for generated runtime machinery.
- Prove serialization removes runtime-only facts.
- Add tests for the Lab control, the facade, the law boundary, and the runtime package when the module is bundled.
- Rebuild `deploy/local-platform/site-runtimes/mcel-runtime.js`.
- Confirm the Hub runtime copy is current when the code path is expected there.

## Adoption checklist

Use this checklist before saying MCEL is better than a legacy library or custom glue.

- The old behavior has been named.
- The MCEL replacement law has a clear axis.
- Required source traits exist.
- The compiler creates the runtime structure deterministically.
- Browser proof observes the real runtime behavior.
- Acid tests cover failure and repair.
- Evidence packets show the result.
- Serialization proves the source is clean.
- The supervisor gate can block adoption when proof is incomplete.
- The documentation explains what is proven and what is still only a claim.

## Failure signs

Treat these as signs that the MCEL implementation or documentation is not ready:

- The source has to contain generated runtime parts.
- A repair changes source meaning instead of rebuilding runtime machinery.
- A law report claims success without naming proof obligations.
- The subsumption lattice says MCEL replaces a library, but no evidence packet backs it.
- The UI has a diagnostic button that is not mentioned in docs.
- The runtime package changes without a test proving the checked-in bundle is current.
- Browser observations are serialized into source.
- A feature works in the Lab but not in the packaged runtime path that Website Builder uses.

## What MCEL does not prove automatically

MCEL can prove its own source/runtime/serialization boundaries. It does not automatically prove that a product design is good, that a backend is correct, that network data is trustworthy, or that a business workflow is complete. Those claims need separate tests and evidence.

MCEL documentation should therefore avoid broad claims like “MCEL is better.” The safe claim is narrower:

> MCEL is appropriate here when the replacement behavior passes the law-specific proof obligations and produces evidence that the old glue did not produce.
