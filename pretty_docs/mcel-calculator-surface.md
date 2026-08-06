# MCEL Calculator host-bound surface

## Current status

Calculator's current surface is host-bound and DSL-authoritative.

The stable presentation surface is:

```text
/applications/calculator
#calculator-app
main_computer/web/applications/apps/calculator.html
main_computer/web/applications/styles/calculator.css
```

The stable execution facade is:

```text
window.MainComputerCalculatorRuntime
```

The semantic authority is:

```text
mcel_apps/calculator/application.js
→ generated virtual contracts
→ generated host-bound adapter
→ MainComputerCalculatorRuntime
```

Generated contracts are virtual. The Calculator package does not contain copied
HTML, copied CSS, generated contract files, normalized IR snapshots, or a copied
browser package document.

The generic authoring rule for this pattern is documented in
`pretty_docs/mcel-dsl-app-authoring-surface.md`.

## Surface boundary

The Calculator surface is the durable set of DOM affordances that the generated
adapter may bind to without becoming presentation authority itself:

```text
route: /applications/calculator
root: #calculator-app
mode controls
arithmetic expression controls
graph expression controls
Mathics panels
result Q&A controls
embedded calculator chat context
status/result outputs
```

The HTML and CSS continue to own layout, labels, input affordances, focus order,
canvas placement, and user-visible structure. The DSL owns the semantic meaning
of states, intents, effects, capabilities, acceptance scenarios, and proof
obligations.

## Dynamic-output boundary

Runtime values are content projected inside the stable surface. They are not
copied into the static application-layout grammar.

That includes:

- arithmetic results;
- parser diagnostics;
- provider-generated expression text;
- result Q&A answers;
- graph pixels and graph status;
- Mathics output and status;
- embedded chat messages.

Those outputs may declare fit policies such as `wrap`, `scroll`, or
`decorative`, but changing output content must not change the durable source
surface.

## Host-bound mount

The generated Calculator runtime manifest identifies the existing host:

```text
mountMode: host-bound
hostRoute: /applications/calculator
rootSelector: #calculator-app
runtimeFacade: MainComputerCalculatorRuntime
presentationAuthority: existing-host-html
```

When the Applications viewport loads, the virtual MCEL asset layer serves the
Calculator runtime manifest and contracts from memory. The host-bound runtime
mounts the generated adapter onto `#calculator-app` and delegates intent
execution through `MainComputerCalculatorRuntime`.

## Retired bridge

Earlier Calculator work used a handwritten semantic-adapter/surface bridge while
the DSL path was still being proven. That bridge has been retired. Current docs
should not describe it as live runtime authority. Historical references are
useful only when explaining the migration path.

## Invariants

The host-bound Calculator surface is current only while these remain true:

```text
the route remains /applications/calculator
the root remains #calculator-app
Calculator HTML/CSS remain presentation authority
calculator-core.js provides deterministic local arithmetic and graph behavior
MainComputerCalculatorRuntime remains the stable facade
generated contracts are virtual or disposable
the legacy handwritten semantic bridge is absent
normal mounting creates no repository files
```

