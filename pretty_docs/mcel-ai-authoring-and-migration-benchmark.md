# MCEL AI Authoring and Migration Benchmark

## Status

This document specifies the benchmark that must be implemented and passed before the proposed MCEL DSL may be presented as easier or safer for AI application development.

It does not implement the DSL, compiler, importers, impact planner, evidence-renewal engine, or benchmark runner.

The benchmark compares complete authoring cycles. It does not reward attractive syntax in isolation.

### TL;DR

The DSL wins only when an AI reaches correct, independently proven application behavior more reliably and with less mechanical work.

## 1. What question does the benchmark answer?

The benchmark answers:

> Does the official MCEL vanilla-JavaScript DSL help an AI create, migrate, modify, repair, and prove applications better than the current explicit or application-specific authoring paths without losing existing meaning?

The benchmark does not ask only:

```text
Is the DSL shorter?
Does the source look cleaner?
Can one example compile?
Can the final page render?
```

It asks whether the complete cycle improves:

```text
requirements interpretation
semantic decisions
authoring locality
compiler repair
legacy compatibility
projection correctness
acceptance
browser observation
effect accounting
proof
later modification
evidence renewal
```

### Example

A DSL solution that renders the correct quote but omits supersession policy fails.

A longer solution that declares, observes, and proves supersession correctly may pass.

### TL;DR

The unit of evaluation is a proven application change, not a source file.

## 2. Benchmark authority order

When benchmark artifacts disagree, use this order:

1. benchmark task requirements;
2. hidden semantic obligation manifest;
3. canonical IR and compatibility result;
4. generated projections;
5. acceptance evidence;
6. browser observation;
7. effect-accounting evidence;
8. proof verdict;
9. efficiency measurements.

Efficiency never overrides failed semantics or proof.

### TL;DR

Correct meaning and honest evidence come before speed, tokens, or line count.

## 3. Compared authoring treatments

The benchmark compares paths that exist or are proposed for the same semantic task.

### Treatment A: current explicit or legacy path

Depending on the application family, this may include:

```text
requirements documents
requirements registry
semantic adapters
surface registry
explicit package contracts
current application-specific compiler or extractor
current normalized Workbench definition
```

The task packet must identify the actual baseline path used for that application.

### Treatment B: official DSL path

```text
requirements
-> official mcel.dsl.v1 source
-> canonical MCEL IR
-> generated low-level definition and package projections
-> evidence
-> proof
```

### Treatment C: migration path

For existing applications:

```text
legacy definition importer -> legacy IR
DSL compiler -> DSL IR
feature-level comparison -> compatibility verdict
```

Treatment C measures preservation and retirement readiness, not only fresh creation.

### TL;DR

The benchmark compares real current paths with the proposed DSL and explicitly measures migration between them.

## 4. Hard pass/fail gates

A run is unsuccessful if any of these occur:

```text
missing required semantic decision
wrong state authority
unstable or incorrect identity
undeclared canonical write
unowned consequential effect
illegal capability use
missing refusal or invariant
missing cancellation, concurrency, cleanup, or recovery policy
self-confirming proof claim
unexplained runtime effect
stale or incompatible evidence reused
manual edit to a generated artifact accepted as source
legacy meaning lost without an intentional versioned delta
candidate promoted over the last proven application before proof
repository or source binding mismatch
final proof below the task's required truth status
```

A run that fails a hard gate receives no efficiency score.

### Example

This is not a successful optimization:

```text
Current path: 14 browser scenarios and complete supersession proof
DSL path: 8 scenarios and the UI still looks correct
```

The missing scenarios may represent missing obligations rather than reduced work.

### TL;DR

The benchmark never awards speed for deleting semantics or evidence.

## 5. Lexicographic evaluation

Results are evaluated in this order:

1. **semantic correctness**;
2. **migration preservation**;
3. **effect and proof completeness**;
4. **repair reliability**;
5. **authoring economy**;
6. **execution economy**.

A treatment cannot compensate for a lower tier by performing well on a later tier.

### Example

```text
DSL A:
  20% fewer tokens
  one wrong authority decision

DSL B:
  5% fewer tokens
  all authority decisions correct
```

DSL B ranks higher.

### TL;DR

Correctness dominates convenience.

## 6. Controlled AI-session protocol

Every comparative run must use a controlled session packet containing:

```text
benchmark task ID
user-level request
allowed repository snapshot
allowed documentation set
available commands and tools
starting application state
time or turn budget
completion criterion
```

The run must record:

```text
AI system and version
system/developer prompt fingerprint
context packet fingerprint
tool configuration
repository fingerprint
compiler/runtime versions
start and end timestamps
token counts when available
commands executed
files read
files authored
files generated
human interventions
```

Fresh sessions are required. A session may not retain the expected answer from an earlier treatment.

The same user-level request and semantic obligation manifest must govern each treatment.

### TL;DR

Compare paths under controlled, repeatable conditions rather than comparing one remembered success with one fresh attempt.

## 7. Visible task packet and hidden obligation manifest

Each benchmark case has two parts.

### Visible task packet

This is what the AI receives. It states the user request and relevant constraints without dictating the implementation.

Example:

```text
Add an optional priority to inventory items.
New items default to normal priority.
The add form permits low, normal, or high.
Every item row shows the priority.
Existing items remain valid.
```

### Hidden semantic obligation manifest

This is used by the evaluator. It records required meaning such as:

```text
model field: priority
schema: enum low|normal|high
default: normal
add-item input source: control
canonical transition: copies priority
surface claim: row displays priority
migration: existing records normalize to normal
required evidence: acceptance + browser observation
```

The hidden manifest must not prescribe incidental source formatting or generated filenames beyond the documented contract.

### TL;DR

The AI receives the problem; the evaluator retains a precise list of what must not be lost.

## 8. Benchmark run artifacts

A future benchmark runner should emit:

```text
runtime/reports/mcel-ai-authoring-benchmark/
  <suite-id>/
    suite.json
    suite.md
    runs/
      <run-id>/
        task.json
        environment.json
        transcript.jsonl
        authored-change-set.json
        diagnostics.json
        compatibility.json
        evidence-impact.json
        evidence-summary.json
        proof-summary.json
        metrics.json
        verdict.json
```

Proposed schemas:

```text
mcel.ai-authoring-benchmark.task.v1
mcel.ai-authoring-benchmark.run.v1
mcel.ai-authoring-benchmark.metrics.v1
mcel.ai-authoring-benchmark.verdict.v1
mcel.ai-authoring-benchmark.suite.v1
```

These are documentation targets, not implemented formats.

### TL;DR

Every score must be reconstructable from preserved task, source, diagnostic, compatibility, evidence, and proof artifacts.

## 9. Benchmark corpus

The benchmark must include all of these roles:

| Application | Role |
|---|---|
| Contract Counter | minimum ceremony and prohibition |
| Inventory reference task | ordinary middle-sized fresh application |
| Contract Workbench | dynamic and asynchronous semantic completeness |
| Git Tools | governed external mutation and recovery |
| Code Editor | stale-safe filesystem mutation and retained draft |
| Document Editor | surface-heavy editing, persistence, and export |

Additional application families may be added, but these cannot be removed from the v1 qualification corpus without a documented replacement that preserves the tested semantics.

### TL;DR

Counter tests economy, Workbench tests completeness, and existing operational apps test migration reality.

## 10. Creation case C1: Contract Counter

### Visible task

Create an application with:

```text
canonical integer count starting at zero
increment
reset
prohibited direct-set
visible count
proof of increment, reset, stale handling, and prohibited direct-set
```

### Required results

```text
one stable count authority
one increment owner
one reset owner
no direct-set mutation path
visible projection bound to canonical count
required acceptance and browser evidence
truth status required by the benchmark configuration
```

### Measurements

```text
authored files
authored semantic declarations
mechanical duplicate declarations
AI tokens
compiler diagnostics
repair iterations
time to proof
```

### TL;DR

Counter proves the DSL does not require Workbench-scale ceremony for a tiny application.

## 11. Creation case C2: ordinary inventory application

### Visible task

Create an inventory application with:

```text
canonical keyed items
renderer-local add form
name, SKU, quantity, and category
validation and structured refusal
add, update quantity, and remove
renderer-local search
derived filter and sort
visible keyed collection
multi-instance local-state isolation
```

### Why this case exists

Counter and Workbench are extremes. Inventory tests ordinary application development without application-specific legacy machinery.

### Required proof

```text
canonical CRUD behavior
stable collection identity
validation refusals
search and sort behavior
row-bound actions
multi-instance isolation
no unexplained effects
```

### TL;DR

The middle-sized task detects a DSL overfit to either trivial examples or the acid application.

## 12. Creation case C3: asynchronous per-item capability

Extend Inventory with a per-item replenishment quote:

```text
request capability
provisional progress
latest-per-item-key concurrency
cancellation
parallel operations for different items
canonical final commit
late-event rejection
cleanup
```

The evaluator must distinguish final-value correctness from lifecycle correctness.

### Required negative case

A superseded request emits a late completion. The older operation must not regain commit authority.

### TL;DR

Async creation passes only when the lifecycle is explained, not merely when the final row looks right.

## 13. Migration case M1: Contract Counter

Compile the existing explicit Counter semantics and the candidate DSL semantics into comparable IR.

Required comparison:

```text
canonical count
increment
reset
direct-set prohibition
surface projection
acceptance obligations
browser obligations
```

A `legacy-evidence` classification may remain for the legacy side, but the DSL side must not borrow a false numerical convergence claim from it.

### TL;DR

Counter migration proves that minimum ceremony does not erase legacy refusals or proof boundaries.

## 14. Migration case M2: Contract Workbench

The DSL candidate must preserve:

```text
all seven intents
all state authorities
all 14 declared browser scenarios
keyed collections
filtering and sorting
provisional quote progress
cancellation
supersession
parallel item operations
multi-instance isolation
intent-complete proof
```

Required result:

```text
legacy/current normalized IR and DSL IR:
  exact or semantically-equivalent for every migrated feature
```

An opaque callback left in the candidate remains explicit migration debt and blocks DSL-v1 completion for that feature.

### TL;DR

Workbench migration proves the DSL can express the complete acid application rather than only its visible happy path.

## 15. Migration case M3: Git Tools

The migration must preserve:

```text
repository and operation identity
preflight
confirmation requirements and scope
remote mutation capability
receipt evidence
uncertain remote result handling
recovery or reconciliation
prohibited or refused paths
```

Required adversarial case:

```text
push request transmitted
connection fails before remote truth is known
```

The candidate must not report success or safe failure without recovery evidence.

### TL;DR

Git Tools tests governed external mutation, uncertainty, and recovery—not just command construction.

## 16. Migration case M4: Code Editor

The migration must preserve:

```text
canonical file version
renderer-local draft
document identity
path containment
stale-source precondition
save refusal
filesystem capability effect
retained draft after refusal
partial-write uncertainty and recovery
```

Required adversarial case:

```text
source changes after draft creation but before save
```

The candidate must refuse the stale write and preserve the draft.

### TL;DR

Code Editor tests whether safer authoring survives a real filesystem mutation boundary.

## 17. Migration case M5: Document Editor

The migration must preserve:

```text
document and region identity
canonical content
local selection and scroll ownership
semantic surface and layout
persistence
export planning
export capability effect
artifact identity and retention or cleanup
proof of visible and external consequences
```

Required adversarial case:

```text
export artifact is created but final publication fails
```

The effect ledger must explain whether the artifact is cleaned, retained, or requires recovery.

### TL;DR

Document Editor tests surface-heavy semantics plus persistent and exported artifacts.

## 18. Modification suite

The benchmark must include these changes from the semantic-impact specification:

```text
CHG-01 source-only module extraction
CHG-02 button-label rename
CHG-03 additive field with default
CHG-04 restrictive validation change
CHG-05 collection-key migration
CHG-06 state-authority change
CHG-07 new refusal
CHG-08 canonical transition change
CHG-09 async concurrency-policy change
CHG-10 new capability effect
CHG-11 new proof claim
CHG-12 runtime or compiler version change
CHG-13 legacy-to-DSL exact compilation
CHG-14 opaque legacy callback change
```

For every case, evaluate:

```text
correct earliest authoring stage
correct semantic dependency closure
correct generated projection impact
correct evidence invalidation
correct evidence reuse
repair iterations
final proof result
```

### TL;DR

The DSL must make later changes safer and more local, not merely make the first draft shorter.

## 19. Repair suite

The benchmark must inject at least these faults:

```text
REP-01 missing state authority
REP-02 missing collection key
REP-03 undeclared canonical write
REP-04 opaque arbitrary JavaScript callback
REP-05 unowned capability effect
REP-06 self-confirming proof claim
REP-07 late async commit after supersession
REP-08 incompatible Git confirmation policy
REP-09 stale Code Editor save
REP-10 unresolved Document Editor export residue
REP-11 generated-file manual edit
REP-12 stale evidence attached to a new semantic fingerprint
```

For each fault, measure whether the AI:

```text
identifies the root diagnostic
returns to the correct authoring stage
chooses a semantically valid repair
avoids editing generated output
renews the correct evidence
reaches proof without hiding the fault
```

### TL;DR

An AI-friendly DSL must be easier to repair correctly, not merely easier to produce incorrectly.

## 20. Migration-preservation suite

Every migration run must produce a feature ledger with one result per existing obligation:

```text
exact
semantically-equivalent
intentional-versioned-delta
incomplete
conflicting
```

The evaluator must reject application-level “equivalent” when any required feature is incomplete or conflicting.

### Example

```text
Git push happy path: equivalent
confirmation consumption: missing
uncertain remote result recovery: missing
```

Overall migration result:

```text
incomplete
```

not:

```text
mostly equivalent
```

### TL;DR

Migration is complete only at feature-level semantic coverage.

## 21. Proof-independence suite

The benchmark must include candidates that attempt to pass through invalid evidence relationships:

```text
implementation-generated expectation repeated as acceptance
browser assertion derived from the same unobserved state object
receipt treated as proof of an external effect
old evidence reused after semantic change
compiler equivalence treated as runtime proof
final UI value treated as lifecycle proof
```

All must fail with stable diagnostics or proof findings.

### TL;DR

Generated plumbing may connect evidence, but it may not collapse independent authorities into one self-confirming claim.

## 22. Correctness metrics

Record at least:

```text
required semantic obligations
satisfied obligations
missing obligations
conflicting obligations
incorrect authority decisions
incorrect identity decisions
undeclared writes
unowned effects
unresolved effect instances
missing proof claims
false proof passes
false proof failures
final truth status
```

Primary correctness result:

```text
hard-gate pass or fail
```

### TL;DR

Count semantic errors directly; do not infer correctness from a low diagnostic count.

## 23. Authoring-economy metrics

For successful runs, record:

```text
authored files touched
authored lines added or changed
independent semantic decisions declared
mechanical duplicate declarations
repeated semantic IDs or paths
manual generated-file edits attempted
AI input tokens
AI output tokens
tool calls
compiler invocations
repair iterations
human semantic questions
human mechanical interventions
```

A useful ratio is:

```text
mechanical declarations / independent semantic decisions
```

The DSL should reduce this ratio without reducing declared meaning.

### TL;DR

Measure how much plumbing the AI had to repeat per real decision.

## 24. Change-locality metrics

For modification tasks, record:

```text
earliest correct authoring stage
actual authored files changed
minimum justified authored files
unnecessary authored files changed
affected semantic nodes
unnecessary semantic churn
generated artifacts regenerated
evidence authorities renewed
evidence authorities unnecessarily renewed
invalid evidence incorrectly reused
```

### TL;DR

A good authoring system changes exactly the decisions that changed and renews exactly the evidence that became invalid.

## 25. Repair metrics

Record:

```text
root diagnostic identified
cascading diagnostics acted on prematurely
safe repair selected
unsafe automatic semantic choice attempted
iterations to valid IR
iterations to compatibility
iterations to proof
last proven application preserved
candidate promoted prematurely
```

### TL;DR

Repair quality includes knowing what not to change.

## 26. Migration metrics

Record:

```text
legacy features inventoried
legacy features mapped
features exact
features semantically equivalent
intentional deltas
incomplete features
conflicts
opaque callbacks remaining
legacy compiler retirement eligibility
```

### TL;DR

Migration economy never excuses unmapped legacy meaning.

## 27. Evidence metrics

Record:

```text
acceptance units required and renewed
browser scenarios required and renewed
effect instances opened and closed
proof claims required and satisfied
evidence reused by exact equivalence
evidence reused by proven independence
unnecessary renewals
incorrect reuses
stale evidence attempts
source and repository binding results
```

### TL;DR

The benchmark measures both under-testing and needless re-testing.

## 28. Time measurements

Wall-clock time may be recorded, but it is secondary because tool speed, hardware, and service availability vary.

Prefer stable work measurements:

```text
AI turns
tool calls
compiler attempts
repair cycles
evidence runs
human interventions
```

When wall-clock time is compared, use the same machine class and service configuration.

### TL;DR

Measure work first and elapsed time second.

## 29. Repetition and statistical protocol

Minimum design-qualification run:

```text
3 fresh sessions per task per treatment
```

DSL-v1 release qualification:

```text
5 fresh sessions per task per treatment
at least 2 independently configured AI systems or model versions
```

Report:

```text
completion rate
hard-gate failure rate
median
interquartile range
worst successful run
all unsafe false-pass events
```

Do not report only the best run.

### TL;DR

A language intended for AI authoring must work repeatedly, not only in a curated demonstration.

## 30. Human intervention accounting

Every intervention must be classified:

```text
semantic clarification
policy decision
missing repository access
mechanical correction
tool failure workaround
benchmark administration
```

A human-provided semantic answer is not an AI failure when the task was genuinely ambiguous.

A human fixing syntax, generated files, or missed proof obligations is authoring assistance and must be counted.

### TL;DR

Do not hide human rescue inside the benchmark result.

## 31. Ambiguity handling

The benchmark should contain both:

```text
fully specified tasks
intentionally ambiguous consequential tasks
```

For consequential ambiguity, the correct behavior is to stop at the relevant authoring stage and request or record the missing decision.

For harmless presentation choices with documented deterministic defaults, the AI may proceed.

### Example

```text
"Search the items"
```

may leave layout styling defaultable.

It does not authorize the compiler to decide whether search is canonical, local, or URL state.

### TL;DR

The benchmark rewards visible semantic questions and penalizes silent consequential guesses.

## 32. Generated-artifact integrity

The benchmark must deliberately edit a generated projection and verify that:

```text
drift is detected
authored source remains authoritative
the candidate is not promoted
regeneration restores the deterministic artifact
proof is renewed when the binding changed
```

### TL;DR

The DSL is not successful if AIs still repair applications by patching generated output.

## 33. Last-proven preservation

At least one task must produce a candidate that fails compilation, one that fails compatibility, and one that fails runtime proof.

In every case:

```text
last proven application remains available
candidate artifacts remain isolated
prior evidence remains bound to prior fingerprints
failed candidate cannot masquerade as current truth
```

### TL;DR

A failed authoring attempt must not destroy the working application.

## 34. Benchmark scoring

Use a gated scorecard rather than one blended number.

### Gate A: semantic and safety qualification

Required:

```text
zero unsafe false passes
zero promoted candidates with missing or conflicting obligations
zero unexplained consequential effects in successful runs
zero accepted stale-evidence reuse
zero accepted generated-file authority violations
```

### Gate B: migration qualification

Required:

```text
all required corpus applications inventoried
all benchmarked features exact, semantically equivalent, or approved versioned deltas
no required feature silently dropped
legacy compiler retirement only after renewed proof
```

### Gate C: reliability qualification

Required for DSL-v1 release:

```text
at least 90% successful completion across fresh-session runs
100% deterministic compiler rejection for the injected invalid programs
no treatment-specific benchmark task below 80% successful completion
```

### Gate D: economy qualification

Compared with the applicable current path, the DSL must:

```text
reduce median mechanical declarations per semantic decision by at least 30%
reduce or preserve median authored-file count
reduce or preserve median repair iterations
reduce or preserve median human mechanical interventions
show no more than a 10% regression in any other primary economy metric
```

It must improve at least three of these primary economy metrics:

```text
authored files
mechanical declarations
AI tokens
tool calls
repair iterations
human mechanical interventions
```

### TL;DR

DSL v1 needs zero unsafe false passes, complete migration, repeatable success, and measurable reduction in mechanical authoring work.

## 35. Baseline fairness

The current path receives its best documented workflow, not a deliberately awkward implementation.

The DSL path receives only documented DSL behavior, not hand-written compiler internals or hidden migration mappings.

Both treatments may use:

```text
same repository search tools
same test runners
same browser evidence tools
same proof tools
same user clarifications
```

Differences must be intrinsic to the authoring path.

### TL;DR

The benchmark must compare the best honest form of each path.

## 36. Preventing benchmark overfitting

The benchmark corpus has public and held-out portions.

Public tasks establish conformance and are suitable for development.

Held-out variants change names, schemas, item identities, refusal conditions, capability event shapes, or workflow order while preserving the semantic category.

The DSL or diagnostics may not contain application-specific branches for benchmark IDs.

### TL;DR

The authoring system must generalize beyond memorized Counter and Workbench source.

## 37. Benchmark review packet

A release review should include:

```text
suite summary
all hard-gate failures
all unsafe false-pass attempts
per-task completion rates
per-treatment efficiency distributions
migration feature ledgers
evidence reuse and renewal summaries
human intervention summaries
held-out task results
known gaps
recommended authorization boundary
```

Raw run artifacts remain available for audit.

### TL;DR

The review must show failures and distributions, not only headline averages.

## 38. What counts as benchmark success?

The benchmark supports an implementation authorization only when:

1. all Gate A safety and semantic requirements pass;
2. all Gate B migration requirements pass for the claimed scope;
3. Gate C reliability thresholds pass;
4. Gate D shows real authoring economy;
5. Counter remains economical;
6. Workbench remains semantically complete;
7. Git Tools, Code Editor, and Document Editor preserve their claimed migrated semantics;
8. modification tasks renew the correct evidence;
9. repair tasks return the AI to the correct stage;
10. held-out tasks do not expose app-specific overfitting.

Benchmark success does not itself authorize unrestricted migration of every application. The later completeness review must name the exact implementation and migration authorization boundary.

### TL;DR

The DSL earns implementation and migration authority by proven correctness, preservation, reliability, and economy together.

## 39. What the benchmark must not claim before implementation

Until the compiler and benchmark runner exist, this document establishes only:

```text
required tasks
required controls
required metrics
required artifacts
required gates
```

It does not establish:

```text
that the DSL is easier
that the DSL compiler is correct
that legacy applications are migrated
that any efficiency threshold has passed
that DSL v1 is authorized
```

### TL;DR

This is the benchmark contract, not a benchmark result.

## 40. Documentation completeness result

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` completes the final review. It confirms that this benchmark contract agrees with the IR, DSL, migration, diagnostics, effect-accounting, authoring-cycle, and change-impact specifications.

The review does not turn this benchmark contract into benchmark results. It permits only an explicitly authorized bounded IR-kernel implementation as the next code step.

### TL;DR

The benchmark is specified and cross-reviewed; no DSL benefit may be claimed until the implemented benchmark actually passes.

# Governing rule

> MCEL may claim that its DSL is better for AI authoring only when controlled, repeatable benchmark runs show that AIs preserve application meaning, account for consequential effects, repair errors correctly, renew evidence honestly, reach independent proof reliably, and perform less mechanical work than the current authoring path.
