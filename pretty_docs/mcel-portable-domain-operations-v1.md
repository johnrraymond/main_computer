# MCEL independently generated portable domain operations (Patch 17B)

This introduces a second, explicitly opted-in output of the already-existing
standalone package projector. The default 17A presentation remains fail-closed.

## Domain invariant

Each declared intent must resolve to a package-owned pure operation or to a
specific independently authorized capability. Neither kind may call an existing
Calculator or Code Editor application facade. A capability refusal cannot be
coerced into an execution receipt.

## Implementation

`build_standalone_package(repo, app, output, portable=True)` emits the source
package's compiled contracts, the generated presentation, an independent
portable executor, and a fingerprinted copy of the appropriate **pure domain
core**. No original app HTML, CSS, JavaScript host controller, or ViewportServer
controller is included.

Calculator registers six pure state/evaluation operations: `switchMode`,
`enterToken`, `clearExpression`, `evaluateExpression`, `drawGraph`, `resetGraph`.
The five other declared Calculator operations are live, typed dispatch calls
through a separately supplied `capabilities.request(intent, payload)` provider:
three model-assisted expression requests, `evaluateMathics`, and
`askResultQuestion`. They **refuse** until an authorized provider supplies
nonempty `text`, `provider`, and `evidence` fields. These fields are a provider
response contract, not independently verified AI/Mathics correctness.

Code Editor registers three local draft/selection operations (`editDraft`,
`discardDraft`, `closeFile`) and five capability operations (`inspectWorkspace`,
`openFile`, `saveFile`, `previewAiderPlan`, `applyReviewedPatch`). The supplied
`PortableWorkspaceBroker` is an **isolated proof/example capability authority**
created only over an explicitly chosen disposable workspace. It uses UTF-8
source bytes and fresh SHA-256 checks, rejects path traversal/symlinks, and
requires explicit save. Preview generates a random 128-bit one-use review
handle, does not mutate source, and applies only after independent
`reviewed=true` plus `approved=true` or `confirmed=true` evidence. Multi-file
atomic transactions are **not** implemented: reviewed transactions are scoped
to one existing file in this version. Source promotion is not enabled.

## Independent proof

Run:

```powershell
python -m pytest tests/test_mcel_portable_intent_execution.py -q
python tools/mcel_portable_intent_probe.py --output .\runtime\state\mcel\portable-proof.json --json
```

The first command tests the bound 19-name registry and pure operations in Node,
plus real filesystem effects and negative guards in Python. The second builds
independent packages, exercises six Calculator local operations and eight Code
Editor operations in Chromium using a temporary capability broker, and verifies
read/write results. It **deliberately refuses** five external Calculator calls
without installed providers. `status=partial-pass` is not a full behavioral
certificate. The browser leg must run on a machine where Chromium can navigate
to the package-only localhost server.

The default `tools/mcel_standalone_package_probe.py --structural-only` is
unchanged and still tests the 17A presentation/refusal boundary. The existing
host-bound combined certificates are likewise unchanged.

## Authority limits

- `fullSemanticRuntimeProven` remains `false` by design.
- No unapproved source write or implicit save is authorized.
- Package-local pure computation and a proof-only broker do not establish a
  production-granted capability interface, external model/Mathics correctness,
  or full behavioral parity with the legacy UI.
- The generic structural renderer is not yet a full UI component compiler;
  it presents semantic intent controls and structural regions.
- All browser proof outputs require a fresh run; no saved JSON is accepted as
  a substitute for actual browser and filesystem execution.

## Portable probe cross-platform fixture and refusal evidence (17B repair)

The disposable Code Editor source fixture is now seeded with exact LF bytes
using `write_bytes(b'original\n')`, avoiding Windows `write_text()` CRLF
conversion sufficient to produce Chromium's `open bytes wrong` refusal.
The final edited file is also checked as raw bytes, not a newline-normalized
`read_text()` value. Chromium intent exceptions are reported under each
application with its `failedAt`, `error`, completed intents and refusals.
A failing app never receives a local-workspace PASS or full-semantic claim.
The Windows Chromium proof must still be executed afresh after application.

## Windows regression and 14-intent accounting correction (17B follow-up)

The Python workspace tests now seed files with `write_bytes` and inspect their
exact bytes instead of letting Windows `write_text` translate LF into CRLF. The
workspace broker still returns unmodified UTF-8 content and hashes source
*bytes*. Deliberate CRLF sources must not be silently normalized.

The browser proof reports successful **calls** separately from **distinct
intents**. Code Editor legitimately calls `editDraft` three times, so its ten
successful dispatches prove eight distinct operations. Calculator proves six.
An unapproved `applyReviewedPatch` is a separately checked `safetyRefusals`
entry, not an absent implementation. Browser failures cannot qualify as
expected refusals: each expected refusal must contain its specific denial
reason. Calculator's five missing provider implementations remain in `refusals`
and `missingExternalProviderProof`. The verdict requires the exact 14 local
intent inventory, the expected five external refusals, the review safety
refusal, and two real Code Editor mutations. A `partial-pass` never asserts
`passed=true` or `fullSemanticRuntimeProven=true`.

Verification on Windows after applying the patch:

```powershell
python -m pytest tests/test_mcel_portable_intent_execution.py -q
python tools/mcel_portable_intent_probe.py --output .\runtime\state\mcel\port-proof.json --json
```

## Patch 18 — independently authorized external Calculator capabilities

The portable Calculator's five external intents have a concrete **optional**
local authority in `main_computer/mcel_portable_calculator_external.py`.
Nothing is implicitly granted: `PortableWorkspaceBroker` still refuses them
when constructed without external handlers. The opt-in proof CLI constructs
those handlers from an exact local Ollama model ID and a Mathics3 worker.
The existing Calculator application host, application HTTP routes, and
mainnet services are not invoked or altered.

The model calls go to an explicitly configured **loopback HTTP** Ollama
`/api/chat` endpoint. Non-loopback hosts, HTTPS, credentials, alternate URL
paths, redirect attempts, and ambient proxy routing are refused. The model
response must identify the requested model and end successfully. The Mathics
capability executes a restricted, allowlisted expression grammar through an
independent subprocess (`tools.mcel_portable_mathics_worker`); failures and
timeouts produce refusals, never fabricated outputs. Mathics3 must be installed
in the Python interpreter running the proof.

For each successful external operation, the authority records an exact intent,
backend identity, input hash, and output hash. The portable JavaScript runtime
checks the response schema, intent, provider identity, and both hashes **before**
changing Calculator state. The Chromium proof independently compares these
receipts against records made by the Python-side authority and checks the
following bounded, explicit semantics:

- Arithmetic model request: generated expression must evaluate to exactly 5.
- Graph model request: generated f(x) must evaluate to 6 at x = 3.
- Mathics-expression model request: Mathics evaluation of its generated
  expansion must equal the reference Mathics expansion of `(x + 1)^2`.
- Mathics execution: `2 + 3` must actually return `5`.
- Result Q&A: the answer to two plus three must be `5`.

Missing grants, malformed input, unauthorized symbolic syntax, invalid external
receipts, wrong answers, and missing backend evidence fail closed. This is
**bounded semantic-fixture certification** of the declared 19-intent inventory,
not a theorem of general correctness for arbitrary model outputs, not a full
behavioral-parity claim with the old host UI, and not a production capability
grant. Ordinary local/workspace-only runs remain `partial-pass` with
`fullSemanticRuntimeProven=false`.

### Windows live command

Install Mathics3 in the active virtual environment if not already available,
and make sure the selected Ollama model is running locally. With the repository
root as the current directory:

```powershell
python -m pytest tests/test_mcel_portable_intent_execution.py tests/test_mcel_portable_external_authority.py tests/test_mcel_standalone_package_projection.py -q

python tools/mcel_portable_intent_probe.py `
  --authorize-external `
  --model gemma4:26b-a4b-it-q4_K_M `
  --ollama-url http://127.0.0.1:11434 `
  --external-timeout-s 90 `
  --output .\runtime\state\mcel\port-proof-live.json `
  --json
```

Use `python -m pip install Mathics3` only if that interpreter does not already
have Mathics3. A successful **fresh** Chromium/browser plus real model/Mathics
run reports `status=pass`, `passed=true`, `externalProviderProven=true`,
`fullSemanticRuntimeProven=true`, and records the five backend receipt digests;
all five conditions are contingent on actual live evidence. Failure to access Chromium or to complete the local portion is `refused`;
failure of Ollama, Mathics3, or model semantics *after the 14 local operations*
preserves `partial-pass` for those local operations but returns exit code 4 for
the incomplete live certificate. The previous no-grant command remains
valid and expects `partial-pass` (exit code 0); it is not silently upgraded.

Mocks in pytest and Node prove contract handling only. They never promote the
live proof. The existing generated `presentation.json` deliberately keeps
`fullSemanticRuntimeProven=false`, since artifact generation alone cannot
establish live execution truth.

## Patch 18 follow-up — strict model-format recovery

A Windows live run of Patch 18 returned `partial-pass` after its first model call
(`askModelForExpression`) produced `external-unbounded-result`. The response
was not stored in the proof, so its precise content is unknown. The previous
strict one-line parser refused model prose, formatting, and multi-line answers;
it did not attempt to obtain a compliant answer.

The external authority now requests non-thinking output from Ollama, accepts a
single expression enclosed in an *entire* code fence (but never searches prose
for a plausible expression), and makes no more than **three actual Ollama calls**
for a formatting-invalid model result. Each retry repeats the original user
request with stronger one-line output constraints. The code never substitutes
an expected fixture answer. Unsafe Mathics identifiers do not trigger retries.
All responses still require the exact model identity and completed generation.

A new `externalModelAttempts` diagnostic in the Calculator proof row records
accepted/rejected attempts, length, line count, error class, and SHA-256 of the
raw model output **without persisting its contents**. Only a successfully
validated response generates a provider receipt. The existing semantic browser
checks independently reject numerically or symbolically incorrect answers,
and the original 14-intent verdict remains independently preserved.

Run the exact Patch 18 live command again after applying this patch. Three
rejected responses still yield `partial-pass`, never a simulated success; the
new diagnostics will reveal which validation rule is blocking certification.


## Patch 18 follow-up — executable graph grammar gate (Windows proof v2)

The second authorized Windows run successfully exercised `askModelForExpression`
with `2+3` and progressed to `askModelForGraphExpression`. Ollama generated
`2x`; Patch 18's character allowlist accepted it, but the actual pure
Calculator graph parser requires `2*x` and refused it with `unexpected token: x`.
The evidence remained `partial-pass`, preserving all 14 independent local and
workspace operations while refusing full certification.

The external provider now applies a bounded, non-evaluating graph-syntax
preflight before granting a receipt. It deliberately rejects implicit
multiplication, unsupported identifiers/functions, or non-executable syntax.
Existing real Ollama retries request explicit `*` when a graph result fails
preflight; three invalid model answers still refuse without a provider receipt.
No heuristic expression extraction or synthetic model result is permitted.

As a separate final authority, the **standalone** Calculator runtime verifies
external graph text using its own pure `calculator-core.js` parser before
mutating graph state. Chromium still samples the returned graph against
expected numeric behavior, so well-formed but semantically wrong expressions
cannot satisfy the live proof. This patch does not modify the original
Calculator host or the mainnet runtime. Unit-test simulated providers remain
non-certifying; a fresh Windows Chromium/Ollama/Mathics run is required for
full 19-intent certification.

## Patch 19 repository-bound certification

The live bounded 19-intent pass becomes portable-package promotion-eligible only
after a separate canonical certificate is emitted and independently rechecked by
`main_computer/mcel_truth_audit.py`. The new proof carries repository source
provenance, generated contract/core/presentation hashes, a precise source
manifest binding, final observed Calculator domain state, and a generation
stamp. Any drift invalidates the promotion gate. See
`pretty_docs/mcel-repository-truth-audit.md` for the live run and certificate
commands. This is a portable execution certification, not automatic transfer of
production or authoring authority.
