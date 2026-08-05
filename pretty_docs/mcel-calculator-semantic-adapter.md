# MCEL Calculator semantic adapter

## Status

`CalculatorSemanticAdapter` is the executable MCEL domain adapter for Calculator's current application scope.

- Adapter ID: `calculator-domain-adapter`
- Version: `calculator-semantic-adapter-v1`
- Runtime scope: `calculator-compute-and-helper-lanes-v1`
- Registry authority: `McelDomainAdapterRegistry`

The adapter is loaded after the domain-adapter registry and before truth-gate consumers. The live Calculator exposes `MainComputerCalculatorRuntime`; the adapter binds each semantic intent to one explicit runtime method.

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

The graph lane uses the same core boundary with its existing bounded function,
constant, variable, range, and canvas behavior. Neither local lane performs a
network, filesystem, repository, package, shell, or provider operation.

## Execution lanes

| Lane | Intents | Authority |
|---|---|---|
| Local UI | `switchMode` | Calculator DOM state |
| Local arithmetic | `enterToken`, `clearExpression`, `evaluateExpression` | bounded arithmetic evaluator |
| Local graph | `drawGraph`, `resetGraph` | graph parser and canvas renderer |
| Model arithmetic | `askModelForExpression` | explicit `/api/chat` request |
| Model graph | `askModelForGraphExpression` | explicit `/api/chat` request |
| Model Mathics | `askModelForMathicsExpression` | explicit Mathics ask endpoint |
| Mathics | `evaluateMathics` | explicit Mathics evaluation endpoint |
| Result Q&A | `askResultQuestion` | explicit calculator Q&A endpoint |

No execution lane silently falls through into another. Local arithmetic and graphing do not invoke providers. Provider and Mathics failures preserve the visible deterministic calculator state.

## Safety boundary

Every current-scope intent is classified as executable and non-mutating with respect to files, repositories, shells, packages, revisions, checkpoints, and publishing. Payload keys that request those operations are blocked before a runtime method is called.

Receipts record:

- intent and lane;
- preflight decision;
- execution binding;
- result or failure class;
- recovery guidance;
- `mutationAllowed: false`;
- `mutationAttempted: false`;
- `hiddenMutationDetected: false`.

## Readiness proof

The adapter implements the registry-required methods:

- `getState`
- `listObjects`
- `listIntents`
- `preflightIntent`
- `executeIntent`
- `buildReceipt`
- `mapEvidence`
- `classifyFailure`
- `buildRecoveryOptions`
- `getRecoveryCoverage`
- `getIntentCoverage`

Intent coverage is derived from the eleven current Calculator intents. Recovery coverage is derived from the adapter's complete failure-class catalog. The adapter may claim `fullApplicationSemanticReady` only while both audits pass.

## Verification

Run:

```powershell
python -m pytest tests/test_mcel_calculator_semantic_adapter.py -q
python main_computer/flog_mcel_runtime_smoke.py
python main_computer/mcel_acceptance_runner.py
python main_computer/mcel_truth_audit.py --release-gate
```

The repository truth audit is the combined authority because FLOG alone does not include the separately generated acceptance report.
