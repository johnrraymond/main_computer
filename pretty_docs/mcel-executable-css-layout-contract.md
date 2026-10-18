# MCEL executable CSS layout contract (incremental v1)

This contract adds a *nested* structural-CSS authority to the existing shared
MCEL workspace planner. It is opt-in and application-neutral. It does not certify
package-host runtime authority, compile a new layout DSL, or rewrite authored CSS.

## Layout authority and source boundary

- Application source supplies semantic controls and explicit obligations, **not**
  the correct responsive column count. Application CSS remains a visual/theme
  input and a possible structural-conflict source.
- A container opting in with `data-mcel-layout-role="adaptive-control-grid"`
  delegates its grid-template-columns and the atomic text-fitting policy of its
  direct child buttons to MCEL when a validated runtime candidate exists.
- `data-mcel-max-columns` is a positive integer 1–16. `data-mcel-min-target-width`
  is a finite number 24–256 CSS pixels. Invalid declarations **fail closed**.
  Controls may declare `data-mcel-text-fit="allow-intraword-wrap"` to exclude
  their labels from the atomic-word obligation, but pointer width and overflow
  checks still apply.
- Layout inference is recursive in execution: a workspace plan is applied first;
  then all opted-in nested control grids under the root are independently
  reflowed. Its CSS authority is marked `data-mcel-layout-authority` and its
  measured result `data-mcel-layout-proof`.
- Runtime overrides are confined to inline structural CSS and are reversible on
  host unmount; no authored CSS or DSL files are changed. Candidate evaluation
  is read-only with respect to the original control grid.

## Candidate evaluation

For each adaptive grid MCEL tries permitted column counts from most to least
compact. Each candidate is rendered in a temporary, inert, offscreen clone in
its normal styling context and measured in the browser. A candidate is accepted
only if all direct child controls satisfy min target width, content overflow,
atomic text-fitting, and the expected computed track count. All preview nodes
are removed after measurement. Results include control-level evidence and failed
checks for each candidate.

Before applying a selected candidate, MCEL measures it again in isolation.
After applying, it **re-measures the real controls** and rolls back if their
constraints remain unsatisfied. If the independent MCEL self-diagnosis text
integrity collector is present, its verdict must also be free of split-control
violations. A failed or throwing independent diagnosis refuses the candidate.
If that collector is unavailable, the layout is explicitly marked
`observed-only`, not independently certified. A refused candidate does not change the live
layout. No candidate grants source-repair authorization.

Nested grid resize observation reruns this process when the available width
changes. The original styles of the container and children are restored on
host-bound unmount. The independent `McelSelfDiagnosis` visual-integrity
collector remains the health authority: the planner cannot override or suppress
its findings.

## Proof and remaining extensions

The browser regression tests preserve Calculator's existing three-column CSS,
reproduce `comma` wrapping, verify the rejected three-column candidate and
accepted two-column candidate, then check the independent diagnostic again.
They also use unrelated control names to prove generality, demonstrate refusal
under unsatisfiable constraints, and reject malformed contract declarations.

Further stages should move these opt-in obligations from host HTML into the
canonical semantic DSL/IR, generalize beyond homogeneous control grids,
validate at more viewport/font configurations, and incorporate accepted layouts
into independent package-host acceptance. The same accept/refuse contract should
then govern future MCEL-generated structural CSS.

## Group-level allocation and layout quality (Patch 11.1)

The nested adaptive grid is **not** entitled to treat its current width as
immutable when its parent has delegated structural ownership. Opt-in a
*two-region* CSS grid with `data-mcel-layout-role="adaptive-control-group"`.
Exactly one of its two direct children must be an existing
`adaptive-control-grid`. The other region remains a normal, application-defined
panel; its controls are independently checked for label splitting, undersized
pointer targets, and content overflow.

The group evaluator trials bounded sibling-space allocations together with
permitted inner-grid column counts in isolated offscreen DOM. Its report keeps
all candidates and observed failures. Eligible candidates must satisfy the
inner-grid contract **and** the sibling/group containment obligations. Among
eligible candidates, the planner minimizes number of inner-grid rows, then
measured group height, empty grid cells, and deviation from a balanced width
split. The chosen layout is re-trialed and checked after application. No
candidate edits application CSS; a group refusal leaves its prior runtime
styles intact, and unmount restores the authored inline styles.

This initial contract is intentionally confined to opt-in, two-region CSS grids.
It does not infer arbitrary tree topology from an app's DSL, nor does an
optimal row count certify accessibility or aesthetic quality. The next
extensions are declarative DSL/IR ownership, multiple sibling regions,
content-measured intrinsic sizing for non-button regions, and independent
quality diagnostics across responsive and font-scale conditions.

When no side-by-side allocation can preserve a compact inner control grid,
MCEL also evaluates a **stacked** group candidate. This prevents a narrow
parent from forcing a very tall single-column button list when full-width
stacking makes the controls usable. The candidate is still subject to the same
sibling and control-fit checks; no responsive media rule is silently bypassed
without a validated replacement. The emitted result identifies whether the
parent is `side-by-side` or `stacked` and includes rows, height, and unused
cell counts.
