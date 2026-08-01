# MCEL Application Scaffolding

This document specifies the target MCEL application-scaffolding feature and the canonical application template that the feature must generate.

It is the implementation contract for the complete feature. Waves 2 through 5A provide deterministic scaffolding, package discovery, browser-safe projection, SCM-controlled operations, and generic semantic-surface mounting. Wave 6A provides package-local acceptance discovery and app-scoped execution through the existing acceptance authority. Wave 6B provides a generic real-browser package host and operation-linked independent browser observation. Wave 7 composes those independent authorities, exact repository provenance, and `McelAppTruthGate` under an app-scoped proof command. Current implementation status remains controlled by `pretty_docs/mcel-status-and-roadmap.md` and generated repository evidence.

## Purpose

MCEL needs a deterministic way to create a new application whose structure exposes every MCEL authority clearly and consistently.

The target command is the MCEL equivalent of `cargo new`, `django-admin startproject`, or a framework application generator:

```text
mcel app create <app-id>
```

The command must do more than create HTML, JavaScript, and CSS. It must create the smallest complete application package that MCEL can eventually:

- discover;
- inspect;
- execute;
- edit through controlled authorities;
- render;
- observe;
- test;
- prove;
- package;
- deploy through an independently authorized path; and
- upgrade without overwriting user-owned code.

The generated application is also a development fixture. It defines what a complete MCEL 1.0 application should look like even while the current MCEL implementation is still converging on that architecture.

## Governing rule

The canonical template defines the target developer experience. Existing applications provide implementation evidence and reusable components, but their historical application-local glue is not automatically template authority.

Development therefore proceeds in both directions:

```text
ideal generated application
→ target application package
→ target developer APIs
→ target proof workflow
```

and:

```text
existing requirements registry
+ semantic adapters
+ SCM operation guards
+ SemanticSurfaceIR
+ shared layout grammar
+ browser observation
+ acceptance evidence
+ repository truth gate
→ reusable bridges
→ generic application spine
```

The generated fixture is where the two directions must meet.

## Status vocabulary

This document uses the following status labels.

| Label | Meaning |
| --- | --- |
| `live` | The named repository authority exists and has executable coverage. |
| `partial` | A usable implementation exists, but it does not satisfy the complete template boundary. |
| `proposed` | The interface or schema is a target contract and is not yet a live platform guarantee. |
| `fixture-target` | The canonical fixture must eventually exercise this behavior. |
| `deferred` | The behavior is intentionally outside the first implementation cut. |

The repository-local `mcel.application-package.v1` shape is live for scaffold generation, structural validation, repository and browser discovery, SCM-controlled semantic operations, generic browser mounting, package-local acceptance discovery, operation-linked independent browser observation, and app-oriented semantic-runtime proof. The installed product-level `mcel app ...` command remains proposed; the repository command is live.

## Current implementation checkpoint

Wave 7 is live at the app-oriented semantic-runtime proof boundary:

```text
tools/mcel_create_app.py                 live
main_computer.mcel_scaffolding           live
mcel.canonical-application-template.v1   live
structural package validation            live
golden Contract Counter fixture          live
repository package discovery             live
browser-safe package catalog             live
adapter-to-SCM application runtime       live
generic semantic surface projection      live
package-local acceptance discovery       live
operation-linked browser observation     live
app-oriented proof orchestration         implemented
```

The live generator creates the complete target package shape under the repository-root `mcel_apps/` directory by default. `tools/mcel_application_packages.py` discovers and fingerprints canonical packages. `tools/mcel_application_runtime_projection.py` deterministically copies only browser-executable domain, intent, adapter, surface, layout, observation, document, script, and style files into `main_computer/web/applications/mcel-packages/`, with a source-bound runtime manifest. The generated browser catalog carries the matching projection fingerprint and browser URLs. `MCEL.mountApplicationPackage()` verifies package, catalog, and projection identity; loads declared modules; compiles intents through `mcel-scm.js`; validates authored semantic ridges; binds controls to intents; and renders committed state and operation receipts. Package-local acceptance is discovered from the package manifest, requirements, and package-relative binding file. The generic package host mounts the projected application in Chromium, and `mcel_application_observation_runner.py` independently compares committed SCM state and receipts against rendered semantic nodes while proving the five required surface-conformance layers. `mcel_app_prove.py` then reconciles all fingerprints and asks `McelAppTruthGate` for the final `semantic-runtime-proven` verdict.

## The four artifacts

The feature consists of four related artifacts. None may silently substitute for another.

### Template specification

The normative template definition is:

```text
mcel.canonical-application-template.v1
```

It defines required files, ownership classes, schemas, relationships, validation rules, and conformance gates.

### Generator

The repository-local generator is live at:

```text
tools/mcel_create_app.py
```

Its job is deterministic scaffolding. It does not use a model to invent application behavior. The installed product-level `mcel app create` command remains proposed.

### Golden fixture

The generator's expected output is recorded under:

```text
tests/fixtures/mcel_application_template_v1/contract-counter/
```

Tests generate a package into a temporary directory and compare every generated file byte-for-byte with this fixture.

### Checked-in reference application

The repository carries one generated browser-mountable reference instance:

```text
contract-counter
```

The checked-in Contract Counter and golden fixture are regenerated from the same template and remain byte-aligned. Its browser-safe projection is generated separately from the canonical package. The package is the canonical `semantic-runtime-proven` template fixture: proof still requires fresh app-scoped acceptance, Chromium observation, exact provenance, and the final truth-gate verdict.

## Target command contract

The live repository command is:

```bat
python tools/mcel_create_app.py contract-counter --title "Contract Counter"
```

The eventual product command is proposed as:

```text
mcel app create contract-counter --title "Contract Counter"
```

### Required initial arguments

| Argument | Meaning |
| --- | --- |
| `app_id` | Stable lowercase application identifier. |
| `--title` | Human-readable title. Defaults from the identifier. |
| `--output-root` | Destination root. Defaults to the canonical application-package root. |
| `--template-version` | Exact template version. Defaults to the current stable template. |
| `--dry-run` | Validate and report output without writing. |
| `--json` | Emit a machine-readable result in addition to the human report. |

### Reserved later arguments

The first implementation may document but omit these operations:

| Argument | Status | Purpose |
| --- | --- | --- |
| `--validate` | proposed | Validate an existing generated package. |
| `--prove` | proposed | Run the package-oriented proof workflow after generation. |
| `--force` | proposed | Permit explicitly authorized replacement of generator-owned files only. |
| `--template` | proposed | Select a future named application template. |

### Identifier rules

The generator must accept identifiers matching:

```text
^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$
```

It must reject:

- absolute or relative filesystem paths;
- path separators;
- empty segments;
- uppercase identifiers;
- whitespace;
- identifiers that escape the output root;
- identifiers that collide with an existing application package unless an explicit upgrade or replacement operation is later defined.

### Exit behavior

The live repository command uses these result classes:

| Exit class | Meaning |
| --- | --- |
| `0` | Generation or dry-run validation succeeded. |
| `2` | Invalid command input. |
| `3` | Destination collision or unsafe output path. |
| `4` | Template or package validation failed. |
| `5` | Filesystem write failed and cleanup was attempted. |

The machine-readable result must carry a stable result code rather than requiring callers to parse prose.

## Canonical generated package

The target package is self-contained and application-oriented:

```text
mcel_apps/
└── contract-counter/
    ├── mcel.app.json
    ├── requirements.md
    ├── blueprint.json
    ├── contracts/
    │   ├── domain.js
    │   ├── intents.js
    │   ├── adapter.js
    │   ├── surface.js
    │   ├── layout.js
    │   ├── observation.js
    │   └── acceptance.js
    ├── src/
    │   ├── index.html
    │   ├── app.js
    │   └── app.css
    └── tests/
        ├── mcel_acceptance_bindings.json
        ├── test_acceptance.py
        ├── test_package.py
        ├── test_operations.py
        ├── test_surface.py
        ├── test_browser.py
        └── test_truth.py
```

The live generator writes packages under the repository-root `mcel_apps/` directory by default. This keeps contracts, requirements, tests, and development metadata outside the public application-serving tree. The package-local responsibility map is normative: creating a new app must not require the author to discover and hand-edit unrelated central registries.

### Package manifest

`mcel.app.json` is the package join point. It identifies the application and references existing authorities; it is not a second requirements language.

Target shape:

```json
{
  "schema": "mcel.application-package.v1",
  "appId": "contract-counter",
  "title": "Contract Counter",
  "template": {
    "id": "mcel.canonical-application-template",
    "version": "1.0.0"
  },
  "requirements": "requirements.md",
  "blueprint": "blueprint.json",
  "contracts": {
    "domain": "contracts/domain.js",
    "intents": "contracts/intents.js",
    "adapter": "contracts/adapter.js",
    "surface": "contracts/surface.js",
    "layout": "contracts/layout.js",
    "observation": "contracts/observation.js",
    "acceptance": "contracts/acceptance.js"
  },
  "runtime": {
    "document": "src/index.html",
    "script": "src/app.js",
    "style": "src/app.css"
  },
  "tests": {
    "root": "tests",
    "acceptanceBindings": "tests/mcel_acceptance_bindings.json"
  }
}
```

The first package schema must remain narrow. It should bind authorities and paths, not duplicate the internal contents of those authorities.

### `requirements.md`

Status: existing requirements language is `live`; package-local acceptance-contract discovery is `live`.

The file contains the documentation-first MCEL application, object, requirement, intent, acceptance, evidence, and boundary blocks required for the reference app.

The generator must create valid identifiers that include the generated application ID and do not collide with other applications.

### `blueprint.json`

Status: blueprint concepts and planners are `live`; package-local canonical loading is `partial`.

The blueprint records the app's declared regions, responsibilities, and implementation aspects in a machine-readable form suitable for MCEL Lab inspection. It must not claim runtime implementation proof.

### `contracts/domain.js`

Status: `fixture-target`.

The domain contract defines canonical application state and invariants. For Contract Counter:

```text
count: nonnegative integer
revision: nonnegative integer
```

It must not own browser elements, layout measurements, or canonical-state commitment.

### `contracts/intents.js`

Status: semantic intent vocabulary is `live`; package-local generated form is `fixture-target`.

The generated reference app declares:

```text
increment       executable mutation
reset           executable mutation
direct-set      prohibited mutation
```

Each executable intent declares input, preconditions, expected effects, and write scope. The prohibited intent demonstrates that obvious direct state assignment cannot masquerade as an authorized MCEL operation.

### `contracts/adapter.js`

Status: semantic adapter toolkit, package loading, and SCM routing are `live` for the canonical application package.

The adapter maps domain vocabulary and intents into executable preflight, transition, effect-validation, evidence, failure, and recovery behavior. It must not create an independent commit authority when the application runtime bridge exists.

### `contracts/surface.js`

Status: SemanticSurfaceIR, authored ridges, and generic package-local mounting are `live`.

The surface identifies at least:

```text
application shell
counter value
counter controls
increment control
reset control
latest operation receipt
```

Stable semantic identities must connect source markup, runtime rendering, observation, and acceptance evidence.

### `contracts/layout.js`

Status: shared layout grammar is `live`; a generic application facade is `proposed`.

The layout contract declares relational constraints, minimum usable sizes, containment, ordering, responsive fallback, and scroll ownership. It must not store browser measurements as source authority.

### `contracts/observation.js`

Status: observation bundle and bounded DOM/authored-accessibility producer are `live`; operation-linked browser comparison is `partial`.

The fixture must eventually declare which semantic nodes are observed after a committed operation and how those observations are compared with canonical state.

The first fixture does not require autonomous browser exploration.

### `contracts/acceptance.js`

Status: acceptance runner, legacy central bindings, and package-local discovery are `live`.

The fixture declares positive and refusal scenarios. `tests/mcel_acceptance_bindings.json` uses `mcel.package-acceptance-bindings.v1`, maps the package acceptance contract to package-relative pytest selectors, and is validated by the package authority. The existing acceptance runner combines legacy central bindings with package-local bindings, resolves package selectors only beneath the declared tests root, executes the shared SCM-controlled runtime test, and records the package fingerprint in app-scoped evidence. The package file supplies inputs to the existing acceptance authority; it does not replace that authority.

### `src/index.html`

Status: `fixture-target`.

The source document contains ordinary semantic HTML, native controls, accessible labels, stable application identity, and sparse MCEL ridges. Removing MCEL-specific attributes must leave understandable HTML.

### `src/app.js`

Status: `fixture-target` dependent on proposed runtime bridges.

The runtime is deliberately thin. It mounts the package, binds semantic controls to declared intents, renders committed canonical state, and displays the latest operation receipt.

It must not contain direct canonical assignments such as:

```js
canonicalState.count += 1;
```

### `src/app.css`

Status: `fixture-target`.

The stylesheet provides readable default presentation while preserving the shared layout contract as the semantic authority. It must not use generated runtime measurements as source law.

### Package-local tests

Status: `fixture-target`; discovery bridge is `proposed`.

The generated tests prove package validity, operation control, surface construction, browser agreement, and final truth classification. A compatibility compiler may temporarily index these tests into existing central runners, but the package remains their source authority.

## File ownership

Every generated file must declare one ownership class in template metadata or generator policy.

| Ownership | Rule |
| --- | --- |
| `generator-owned` | May be regenerated only when unchanged from the recorded generated base or through an explicit reviewed upgrade. |
| `user-owned` | Created once and never overwritten by ordinary regeneration. |
| `mixed` | Contains explicit generated sections and user sections with deterministic boundaries. Avoid unless necessary. |
| `derived` | Rebuilt from package authorities and never hand-edited. |

Initial recommendation:

| Path | Ownership |
| --- | --- |
| `mcel.app.json` | generator-owned with reviewed upgrades |
| `requirements.md` | user-owned after creation |
| `blueprint.json` | user-owned after initial creation |
| `contracts/*.js` | user-owned after creation |
| `src/*` | user-owned after creation |
| compatibility indexes | derived |
| generator result manifest | derived |

The first generator must not implement regeneration until ownership and upgrade behavior are executable and tested.

## Contract Counter

Contract Counter is the canonical reference behavior because its domain is too small to hide architectural gaps.

### State

```text
count = 0
revision = 0
```

### Executable operations

```text
increment:
  count becomes count + 1
  revision becomes revision + 1

reset:
  count becomes 0
  revision becomes revision + 1
```

### Prohibited operation

```text
direct-set:
  arbitrary assignment to canonical count is refused
```

### Required refusal cases

The fixture must demonstrate:

- stale expected revision refusal;
- duplicate operation-ID refusal or idempotent replay with zero additional effect;
- prohibited-intent refusal;
- undeclared write refusal;
- failed postcondition with no canonical commit.

### Required visible surface

```text
Contract Counter

0

[ Increment ] [ Reset ]

Latest result:
No operation has been executed.
```

After a successful increment, browser-visible state and canonical state must agree:

```text
count 0 → 1
revision 0 → 1
operation committed
```

The evidence panel exists to make the contract path visible. It is not itself proof; the independently verified receipt is proof input.

## Complete operation path

The target fixture must eventually trace one operation through this chain:

```text
authored HTML control
→ semantic surface node
→ declared intent
→ semantic adapter preflight
→ SCM operation identity and revision checks
→ isolated draft transition
→ domain invariant and declared-effect validation
→ atomic canonical commit
→ application operation receipt
→ semantic surface projection
→ bounded browser observation
→ acceptance evidence
→ repository-bound truth verdict
```

No stage may use its own assertion as sufficient proof of itself.

In particular:

- the browser event handler does not commit canonical state;
- the adapter does not certify its own browser result;
- browser text does not prove a canonical mutation;
- a successful mutation receipt does not prove usable rendering;
- an acceptance declaration does not prove its scenario ran;
- declared maturity does not replace repository-bound evidence.

## Fixture mechanics

The fixture operates in two modes.

### Current-capability mode

This mode remains green while reporting target gaps explicitly.

Example:

```text
Template definition                 pass
Deterministic scaffold output       pass
Package schema                      pass
Requirements syntax                 pass
Blueprint syntax                    pass
Golden fixture equivalence          pass
Generated tests collect             pass
Application package discovery       pass
Browser compatibility registration  pass
Adapter-to-SCM application bridge   pass
Generic surface projection          pass
Package-local acceptance discovery  pass
Operation-linked browser observation pass
App-oriented proof command          missing
```

A missing target must be reported as `missing`, `unsupported`, or another explicit non-passing state. It must not be silently skipped or counted as a pass.

### MCEL 1.0 target mode

This mode is expected to fail until the application spine is complete.

Proposed command:

```bat
python main_computer/mcel_app_template_conformance.py tests/fixtures/mcel_application_template_v1 --target mcel-1.0
```

The exact runner name is proposed. The invariant is that the complete target remains executable and cannot be weakened merely to obtain a green result.

## Golden fixture rules

The generator must be deterministic.

Required tests:

```text
same arguments
→ byte-equivalent package output
```

The generator test suite must also prove:

- generation works in an empty temporary directory;
- dry run performs no writes;
- generated paths are repository-relative or remain inside an explicitly supplied output root;
- Windows and POSIX path handling produce equivalent package contents;
- invalid identifiers are refused;
- destination collisions fail closed;
- a failed write does not leave a partially generated package;
- generated JSON is canonical and stable;
- generated imports and referenced paths resolve;
- generated requirements parse cleanly;
- generated tests collect through the currently supported compatibility path;
- output does not contain hidden Contract Counter-only integration outside substituted identifiers and fixture behavior;
- no timestamp, random UUID, hostname, or absolute path makes output nondeterministic;
- no manual central-registry edit is required from the user;
- the exact template version is recorded.

Golden comparison should normalize only properties explicitly declared nondeterministic. The initial template should have no such properties.

## Checked-in reference app rules

The checked-in Contract Counter must be reproducible from the template.

A conformance test should establish one of these relationships:

```text
regenerate reference app in a temporary directory
→ byte-equivalent to checked-in app
```

or, after user-owned evolution is supported:

```text
regenerate generator-owned baseline
→ generator-owned files equivalent
→ user-owned files satisfy package contracts
```

The reference app must not acquire hand-written central hooks that the generator cannot produce or the package loader cannot discover.

## Current-to-target gap map

| Target capability | Current donor | Current state | Missing bridge |
| --- | --- | --- | --- |
| Package identity and joining | generated manifest, structural validator, and repository catalog | live for repository discovery | browser/runtime package loader |
| Package discovery | `mcel_application_packages.py` | live read-only | derived browser compatibility registration |
| Requirements binding | requirements registry | live centrally | package-local source discovery |
| Blueprint binding | app blueprint core and specimen planner | partial | canonical package blueprint loading |
| Semantic adapter | adapter toolkit and registry | live | package registration without hand-edited central code |
| Controlled mutation | `mcel-scm.js` plus `mcel-application-runtime.js` | live for declared application intents | browser/package loading and proof integration |
| Semantic surface | SemanticSurfaceIR and ridges | live | package-derived surface enrollment |
| Layout | shared layout grammar plus app-local facades | partial | generic application layout facade |
| Runtime projection | application-specific JavaScript | partial | state-to-semantic-node and control-to-intent binder |
| Browser observation | bounded producer and observation bundles | partial | operation-linked canonical/browser comparison |
| Acceptance | acceptance runner and binding catalogs | live centrally and package-locally | operation-linked browser evidence |
| Evidence | application runtime receipt plus SCM, browser, acceptance, and truth objects | application operation receipt live for state operations | browser, provenance, acceptance, and truth references |
| Repository provenance | evidence provenance and truth audit | live | package-aware orchestration |
| Full-file project edits | reviewed project-edit transaction | live separately | semantic ownership and app-package integration |
| App proof | separate requirements, FLOG, acceptance, and truth commands | partial | one app-oriented orchestration command |
| Upgrade | canonical template and ownership metadata | partial | ownership-aware template migration contract |

## Development waves

These waves define the dependency order. They do not authorize code work; authorization remains in `pretty_docs/mcel-status-and-roadmap.md`.

### Wave 1: Documentation and template contract

Deliver:

- this canonical specification;
- narrow links from current authoring and system guides;
- roadmap status without changing the currently authorized code candidate;
- regression tests preserving documentation authority.

Exit gate:

```text
one indexed canonical scaffolding document
one unambiguous target package
one declared golden fixture
one declared reference app
no false implementation claim
```

### Wave 2: Deterministic generator core — implemented

Live implementation:

```text
tools/mcel_create_app.py
main_computer/mcel_scaffolding/
tests/fixtures/mcel_application_template_v1/contract-counter/
```

Delivered:

- argument and identifier validation;
- deterministic template rendering;
- dry run;
- safe destination creation;
- cleanup after ordinary generation failure;
- package structural validation;
- machine-readable result;
- golden scaffold test.

The first generated package may truthfully report unsupported runtime integrations.

Verified exit gate:

```text
same inputs produce byte-equivalent output
empty-directory generation passes
no overwrite by default
dry run writes nothing
generated package is structurally valid
strict requirements parsing passes
generated package tests collect and pass
write failure leaves no partial package
```

### Wave 3A: Read-only application package authority — implemented

Live implementation:

```text
main_computer/mcel_application_packages.py
tools/mcel_application_packages.py
tests/test_mcel_application_packages.py
```

Delivered:

- direct-child package discovery under `mcel_apps/`;
- structural validation through the canonical package validator;
- repository-relative requirements, blueprint, contract, runtime, and test resolution;
- directory, manifest, and blueprint identity agreement;
- duplicate application-id refusal;
- symlink and unsafe-reference refusal;
- deterministic per-package and catalog fingerprints;
- human and machine-readable catalog output.

Verified exit gate:

```text
new generated app is discovered without a user-authored central registry edit
invalid or ambiguous package blocks catalog validity
package truth requires fresh app-scoped proof
```

### Wave 3B: Browser-safe package catalog — implemented

Live implementation:

```text
main_computer/mcel_application_package_browser_catalog.py
tools/mcel_application_package_browser_catalog.py
main_computer/web/applications/scripts/mcel-application-package-catalog.js
tests/test_mcel_application_package_browser_catalog.py
```

Delivered:

- deterministic projection from the validated repository package catalog;
- browser-safe package identity, paths, conformance, template, and fingerprint metadata;
- read-only `McelApplicationPackages` lookup accessors;
- exact browser/repository catalog fingerprint agreement;
- generated-artifact freshness enforcement in `mcel_sanity_check.py`;
- browser-shell inclusion without adapter import, package-code execution, surface enrollment, or maturity promotion.

Verified exit gate:

```text
validated generated app is visible to browser-side MCEL tooling
without user-authored central registry edits
and remains independently proof-gated
```

### Wave 4: Adapter-to-SCM application runtime — implemented

Live implementation:

```text
main_computer/web/applications/scripts/mcel-application-runtime.js
tests/test_mcel_application_runtime.py
```

The public MCEL facade now exposes:

```js
const definition = MCEL.defineApplication({
  appId: "contract-counter",
  domain,
  intents,
  adapter
});

const app = MCEL.createApplicationInstance(definition);

const result = app.dispatch({
  operationId,
  intentId: "increment",
  expectedRevision,
  payload: {}
});
```

The bridge compiles executable semantic intents into SCM component transitions. The adapter owns preflight, proposed state, and effect validation. SCM owns operation identity, expected revision, isolated drafts, path authority, postconditions, atomic commitment, and evidence.

Verified exit gate:

- increment commits exactly once;
- reset commits exactly once;
- stale revision is refused;
- duplicate operation has no additional effect;
- prohibited direct-set is refused;
- undeclared adapter writes are refused;
- failed postcondition leaves canonical state unchanged;
- application state snapshots are immutable;
- application receipts include SCM revisions and evidence.

This wave does not load modules from the package catalog, mount Contract Counter, bind browser controls, or promote the application beyond `structural-only`.

### Wave 5: Generic semantic surface projection — implemented

Live implementation:

```text
main_computer/mcel_application_runtime_projection.py
tools/mcel_application_runtime_projection.py
main_computer/web/applications/mcel-packages/
main_computer/web/applications/scripts/mcel-application-runtime.js
tests/test_mcel_application_runtime_projection.py
```

The projection authority copies only browser-executable package files and emits `mcel.application-runtime-projection.v1` with source-package, package-catalog, and projection fingerprints. `MCEL.mountApplicationPackage()` verifies those identities before loading modules, validates the declared surface and layout against authored DOM ridges, creates the SCM-controlled application instance, binds semantic controls to declared intents, renders only committed canonical state, displays committed or refused receipts, and removes listeners on unmount.

Verified exit gate:

- no direct canonical mutation from browser code;
- no success display before commit;
- source packages remain outside the served tree;
- requirements, tests, and acceptance files are excluded from browser projection; executable observation contracts are included;
- package, catalog, and projection fingerprint mismatches are refused;
- surface identity, regions, nodes, controls, and layout-region declarations are validated;
- mount and unmount are deterministic;
- rendered values derive from committed state;
- semantic identities survive rendering.

This wave does not itself emit independent browser observations or enroll Contract Counter as `semantic-runtime-proven`; the generic launcher and observation authority are supplied by Wave 6B.

### Wave 6A: Package-local acceptance discovery

Live implementation:

- `mcel.package-acceptance-bindings.v1` under each package tests root;
- package-manifest `tests.acceptanceBindings`;
- package-authority validation of app identity, contract identity, runner, selector containment, and file existence;
- acceptance-runner discovery of package `mcel-acceptance` blocks;
- package-relative selector compilation into repository execution selectors;
- app-scoped evidence containing the package fingerprint and binding-file hash.

Verified exit gate:

```text
python main_computer/mcel_acceptance_runner.py --app contract-counter --check
→ evidence_scope: app-scoped
→ enforceable_contracts: 1
→ passed_contracts: 1
```

Contract Counter executes increment, reset, stale, duplicate, prohibited, and failed-postcondition paths through the shared SCM-controlled runtime. No central acceptance-binding edit is required.

### Wave 6B: Operation-linked browser observation — implemented

Live implementation:

- `main_computer/web/mcel-package-host.html`, a generic package host rather than an app-specific route;
- `mcel-application-package-host.js`, which loads the validated runtime projection and actual generated app module;
- `mcel-application-operation-observer.js`, which independently reads semantic nodes and visible receipt data after a committed SCM operation;
- browser-safe projection of the package observation contract;
- `main_computer/mcel_application_observation_runner.py`, which runs the app in Playwright Chromium and writes app-scoped JSON and Markdown evidence.

Verified comparison contract:

```text
accepted increment
→ SCM canonical count 1 at revision 1
→ semantic projection renders count 1
→ independent DOM capture observes count 1
→ visible receipt operation and revision match SCM
→ package, projection, catalog, and repository fingerprints are bound
```

Required command:

```bat
python main_computer/mcel_application_observation_runner.py --app contract-counter --check
```

Tampered state text, tampered receipts, missing semantic nodes, surface mismatch, refused operations, and stale package or runtime-projection fingerprints are hard failures. The observation report also proves semantic-surface, layout-grammar, runtime-ownership, runtime-visual-fit, and diagnostic-no-throw layers for final proof composition.

### Wave 7: App-oriented proof orchestration
### Wave 7: App-oriented proof orchestration — implemented

Live command:

```bat
python main_computer/mcel_app_prove.py --app contract-counter --check
```

The runner composes, without collapsing, these authorities:

- validated repository package and browser catalog;
- fresh browser-safe runtime projection;
- strict package requirements contract;
- SCM-backed package-local acceptance evidence;
- real-Chromium operation-linked observation;
- five-layer semantic-surface and runtime conformance;
- exact package, catalog, projection, and repository provenance;
- the browser-side `McelAppTruthGate`.

It writes app-scoped proof under:

```text
runtime/reports/mcel-app-proof/apps/<app-id>/
```

Exit gate:

```text
Package                     pass
Application discovery       pass
Generated artifacts         pass
Operation conformance       pass
Surface conformance         pass
Acceptance evidence         pass
Browser observation         pass
Repository binding          exact
Truth status                semantic-runtime-proven
```

The runner fails closed when evidence is absent, stale, app-mismatched, package-mismatched, projection-mismatched, repository-mismatched, missing a required surface layer, or rejected by `McelAppTruthGate`.

### Wave 8: Template upgrade support

Deferred until the first template is stable.

Deliver:

- generated-base recording;
- ownership-aware comparison;
- reviewed upgrade plan;
- no overwrite of user-owned files;
- rollback or replacement artifact suitable for the reviewed project-edit transaction.

## Required MCEL changes after the first working scaffold

A working generator is not completion. Once the first package can be emitted, development must move the platform toward the template rather than embedding compatibility hacks in Contract Counter.

Required principles:

1. Package discovery replaces manual user edits to central registries.
2. Compatibility indexes are derived artifacts, not source authority.
3. The semantic adapter and SCM do not remain competing mutation engines.
4. Browser runtime code stays a projection layer rather than a canonical state owner.
5. Acceptance contracts become package-discoverable while the acceptance runner remains authoritative.
6. Runtime, acceptance, and repository evidence remain distinct even when one command orchestrates them.
7. The template fixture may add stronger gates, but existing gates may not be weakened to preserve a current implementation shortcut.
8. App-specific global facades are implementation donors, not required template dependencies.
9. The project-edit transaction is integrated only after semantic source ownership and reviewed edit planning are specified.
10. No application maturity changes merely because the generator can create files.

## Non-goals for the first cut

The first generator does not need to provide:

- model-authored business logic;
- autonomous browser exploration;
- unreviewed source edits;
- deployment;
- template upgrades;
- delete or rename operations;
- a marketplace of templates;
- generic database selection;
- arbitrary framework generation;
- automatic maturity promotion;
- proof claims for unsupported integration layers.

The first cut creates a deterministic, inspectable destination for later MCEL work.

## Verification strategy

The documentation patch should be guarded by documentation-authority tests.

The generator wave should add focused tests in this order:

```text
generator input validation
generator deterministic output
golden fixture equivalence
package schema validation
package discovery
operation conformance
surface construction
browser projection
acceptance binding
truth orchestration
```

Each platform wave must add the smallest failing fixture assertion before implementing the bridge that makes it pass.

## Completion definition

The scaffolding program is complete when this conceptual command succeeds without app-specific integration work:

```text
mcel app create contract-counter --prove
```

Expected result:

```text
MCEL application created: contract-counter

Scaffold conformance       pass
Application discovery      pass
Operation conformance      pass
Browser conformance        pass
Acceptance evidence        pass
Repository binding         exact
Truth status               semantic-runtime-proven
```

This does not mean that MCEL can merely display a counter.

It means that MCEL can generate the canonical structure of a new application, connect every platform contract, control accepted and refused operations, prove the browser result, and classify the complete application without bespoke central integration work.

## Bottom line

The generator, fixture, and reference application form one test-driven development instrument:

```text
canonical template
→ deterministic generated package
→ explicit target failures
→ bounded MCEL platform patches
→ complete application proof
```

When a new MCEL application can be created, understood, operated, observed, and proven through that package, MCEL will have a repeatable application-development architecture rather than a collection of individually integrated applications.
