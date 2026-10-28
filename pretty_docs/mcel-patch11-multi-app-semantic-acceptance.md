# Patch 11.1–11.4 — Semantic acquisition acceptance and evidence ledger

Status: **BOUNDED OFFLINE PASS**. Main Computer knowledge database expanded using real source contracts, but no hosted application integration has been verified by this patch. MCEL Promise: **INCOMPLETE**.

| Gate | Status | Evidence |
|---|---|---|
| File Explorer author/source mapping | PASS (offline) | Root scope, list/search alternative path, file/directory size distinction, preview readable/unreadable |
| Schedule author/source mapping | PASS (offline) | Original Task Manager schedule plus new declared-only create/delete; no executed claim |
| Worker lease and session mappings | PASS (offline) | Map/list acceptance, generation tuple, candidate-only link, mismatch rejection, policy not permission |
| Shared semantic core reused | PASS (offline) | 48 unique concepts, 14 observation families, 14 operation descriptions; no duplicate IDs |
| Source-preserving interpretation | PASS (offline) | Representative combined source fixture unchanged after `interpret` |
| New acceptance Frob | PASS | `python -m tools.mcel_semantic_knowledge_acquisition_frob` (16/16) |
| Original acceptance Frob | PASS | `python -m tools.mcel_semantic_knowledge_frob` (11/11) |
| Focused semantic tests | PASS | `python -m pytest tests/test_mcel_computer_knowledge.py tests/test_mcel_computer_knowledge_multiapp.py -q` (30) |
| Independent existing regressions | PASS | File Explorer semantic adapter + Scheduler Lab report + Patch 10 producer (14) |
| Broader viewport/Worker module import | BLOCKED BY EXPORT | Missing `game_projects/webgl-demo/game.json` fixture at test collection |
| Live File Explorer and Worker HTTP parity | UNVERIFIED | Hosted server not accessed from this environment |
| Full cross-app authority/effect semantics | UNVERIFIED | No operation execution is implemented |
| IR compiler, inference and authoring economy | UNVERIFIED | Explicitly out of scope |

The original eight Task Manager observation families remain exactly eight. The expanded registry counts are not a claim of eight additional fully covered applications: there are only six new observation mappings, of which three are File Explorer and three are Worker; scheduling reuses Task Manager's existing schedule observation and adds two declared operations.

### Regression and safety checks

No UI or application server files are changed by this patch. Existing MCEL layout, Task Manager live notebook, File Explorer UI and Worker execution remain untouched. Neither the tests nor the CLI call external worker action endpoints or the operating system to execute an effect. The knowledge interpreter is strictly read-only.

### Remaining work before the next level

Audit additional actual computer-domain source producers only where necessary; close critical gaps in standard concept vocabulary and real-source availability semantics; prove cross-application semantic interpretation with live captured but read-only responses; then consider a separate, falsifiable UI/IR consumer gate. Do not treat this database as a complete universal ontology or as proof that unhinted layout is solved.
