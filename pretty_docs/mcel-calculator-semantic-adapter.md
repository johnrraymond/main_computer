# MCEL Calculator generated adapter authority

## Current status

Calculator no longer uses a live handwritten semantic adapter as semantic
authority. The current semantic authority is:

```text
mcel_apps/calculator/application.js
→ canonical Calculator IR
→ generated virtual contracts
→ generated host-bound adapter
→ MainComputerCalculatorRuntime
```

The generated adapter is mounted by the host-bound MCEL runtime onto the existing
Calculator page. The existing HTML/CSS remain the presentation surface; the DSL
and generated adapter own semantic meaning.

The generic authoring-surface rule is documented in
`pretty_docs/mcel-dsl-app-authoring-surface.md`.

## Deterministic local core

`calculator-core.js` is the DOM-independent implementation for local arithmetic
and graph expression parsing. It is loaded before `calculator.js`, which keeps
`window.MainComputerCalculatorRuntime` as the stable browser facade.

The arithmetic grammar accepts only numeric literals, parentheses, unary signs,
`+`, `-`, `*`, `/`, and `%`; `x` or `X` is normalized to multiplication for
keypad compatibility. Parsing produces normalized-expression, grammar,
parse-status, parser-code, token-count, and result evidence. Arbitrary
JavaScript identifiers, member access, assignment, imports, and dynamic code
execution are outside the grammar.

The graph lane uses the same core boundary with its bounded function, constant,
variable, range, and canvas behavior. Neither local lane performs a network,
filesystem, repository, package, shell, or provider operation.

## Execution lanes

| Lane | Authority | Effect boundary |
| --- | --- | --- |
| arithmetic | deterministic core through generated adapter | local, provider-free |
| graphing | deterministic core through generated adapter | local, provider-free |
| mode and clear actions | runtime facade through generated adapter | local UI state |
| model arithmetic assistance | explicit provider capability | remote/provider effect |
| model graph assistance | explicit provider capability | remote/provider effect |
| Mathics assistance | explicit provider capability | remote/provider effect |
| Mathics evaluation | explicit backend capability | backend symbolic effect |
| result Q&A | explicit provider capability | remote/provider effect |

The DSL declares these lanes as intents and capabilities. The generated adapter
binds them to stable facade operations. Proof and evidence close accounting over
the declared effects.

## Browser evidence

The Calculator browser parity/evidence path proves that:

```text
the generated adapter mounts onto #calculator-app
all declared intents are observable
local deterministic lanes remain provider-free
capability lanes are explicit
legacy handwritten semantic authority is retired
the visible route remains /applications/calculator
normal mounting writes no source-tree files
```

## Historical note

Older docs and migration evidence may mention a handwritten Calculator semantic
adapter and Calculator surface script. Those were temporary compatibility
bridges used before the host-bound DSL path was promoted. They should not be
used as current implementation instructions.

