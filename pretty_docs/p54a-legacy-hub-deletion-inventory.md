# P5.4a — Legacy Hub deletion inventory (read-only)

**Disposition: no code deletions approved.** The P5.3a–P5.3i source extractions have substantially reduced the old Hub facade, but a source scan does not establish that its remaining import aliases, routes, and protocol implementations can be removed safely.

## Source and scope

This inventory was prepared from the repository snapshot `main_computer_test-20261108-165657.zip`, overlaid in order with the supplied P5.3a, P5.3b, P5.3c, P5.3c Windows repair, P5.3d, P5.3e, P5.3f, P5.3g, P5.3h, and P5.3i patch archives. The user's Windows acceptance for P5.3i was **281 passed, zero failed**, including previous P5.2/P5.3 regression checks.

The snapshot-plus-patch reconstruction is a reproducible source basis, **not proof that the reconstructed tree is identical to every unshared change in the user's current checkout**. The included read-only tool can run against the actual local repository.

The static inventory scanned **3,014 Python files** under `main_computer`, `tools`, `scripts` and `tests`. It identified **16 distinct historical `main_computer.hub` symbols imported directly** and two test files importing the Hub module itself. It also scanned literal legacy-protocol references in those sources. These counts are source references, not observed network traffic or evidence of operational reachability.

The independent P5.3i verifier has separately established **139 historical facade exports**, with exactly two locally defined functions: `serve_hub` and `serve_hub_worker`. Those exports are retained deliberately for compatibility.

## Dependency and deletion classification

| Component | Observed dependency | P5.4a disposition |
|---|---|---|
| `main_computer/hub.py` | `main_computer/cli.py` imports legacy server entry points, Worker client and port symbols. `hub_cli_live_session.py` imports its chain-ID constant. Two test files import the Hub module directly. | **Retain.** Entry points and compatibility surface are still referenced. |
| `hub_registry_json.py` | The FoundationDB Dev-Test registry inherits the extracted JSON-registry base in `exp_fdb_hub_state.py`. | **Retain.** Registry base still serves Dev-Test code. |
| `hub_registry_shared.py` | Models, offer/pricing and transport contracts shared by registry implementations. | **Retain.** Shared live dependency. |
| `hub_http_runtime.py` and `hub_http_shared.py` | Dev-Test inherits the extracted HTTP server and handler framing. Legacy Hub continues to use them. | **Retain.** Shared infrastructure, not obsolete code. |
| `hub_http_legacy_handler.py` and `hub_http_legacy_support.py` | Dev-Test explicitly delegates approved routes through the legacy handler; MSK, session, funding and Dev-Test simulation behavior is still defined there. | **Retain.** Route ownership must be migrated/retired separately, per endpoint. |
| `hub_worker_client.py` | `main_computer/cli.py` still calls registered Worker and pull-client entry points. Scripts and tests use legacy REST Worker endpoints. | **Retain pending supported-use decision.** No evidence justifies deleting the client protocol. |
| `hub_worker_http.py` | `serve_hub_worker` instantiates the Worker HTTP server; smoke scripts import it directly. | **Retain pending supported-use decision.** |
| `hub_facade_helpers.py` | Historical message and identity helper objects are re-exported; source and import identities are under test. | **Retain.** |
| `providers/hub.py`, `remote_overflow.py`, `hub_plex_service.py` | The old `HubProvider` remains reachable through routing and Chat Console code; session and Dev-Test safe-chat paths are referenced. | **Retain.** Dev-Test simulation is not equivalent to real Stable Hub paid execution. |
| `hub_credit_*`, `hub_resource_limits.py`, `hub_private_https.py`, Stable Hub modules | Shared by production Stable Hub and/or retained Dev-Test functionality. | **Protected.** Not candidates for legacy cleanup. |

## Legacy protocol inventory

| Route family | Reachability evidence | Decision |
|---|---|---|
| `/api/hub/v1/workers/register`, `/heartbeat`, `/poll`, `/results` | Legacy handler and Worker client implement these routes. Additional lab/smoke scripts still reference them. P5.3a explicitly **blocks** inherited versions on the FDB Dev-Test Hub. | Do not remove standalone legacy-Hub behavior without a deliberate compatibility/CLI/smoke migration. |
| `/api/hub/sessions/start` and `/api/hub/sessions/chat` | Used by `HubProvider` and `hub_plex_service`, and permitted on Dev-Test under P5.3a. | Active compatibility paths; retain. |
| `/api/hub/remote-overflow/safe-chat` | Used by the Dev-Test simulation provider and explicitly permitted by Dev-Test allowlist. P5.2 prevents this simulation from masquerading as real paid Stable Hub execution. | Retain until any deliberate replacement is separately tested. |
| MSK, funding and request authorization routes | Implemented through legacy handler/support and intersect the Dev-Test allowlist. | Protected pending individual route contract review. |
| Unlisted inherited routes | P5.3a rejects them by default (HTTP 404). | Already closed to Dev-Test; no blanket deletion needed for this safety boundary. |

**Important distinction:** The presence of a route string or import establishes a source dependency, but does not prove a live network caller; conversely, the absence of a source reference does not prove that external clients, deployment scripts, plugins, or direct HTTP clients never use an endpoint.

## Read-only Windows command

From the repository root:

```powershell
python -m tools.hub_legacy_deletion_inventory
python -m pytest -q tests/test_hub_legacy_deletion_inventory_p54a.py
python -m tools.hub_facade_verify
```

The inventory tool emits JSON with caller files, shared-module edges, protocol reference groups, and an explicit empty `removal_approved` list. It opens no network connection, mutates no state, and does not access FoundationDB or payment systems. The tests inspect only source files.

## Next decision gate — P5.4b

Before a deletion patch, enumerate the supported operational surfaces of `main-computer hub`, `main-computer hub-worker`, Dev-Test Hub, production Stable Hub, their CLI callers, smoke scripts, and any external integrations. For each proposed deletion, identify its replacement or explicit retirement decision, verify that the Dev-Test route allowlist and the P5.2 requester boundary remain correct, and run targeted end-to-end and cross-interface acceptance. Keep the P5.3i historical facade exports intact until individual compatibility obligations are discharged.

**No deletion is justified by P5.4a alone.** If the remaining code is still required, the correct result may be to retain the now-separated modules and stop cleanup there rather than remove functioning protocols for the sake of reducing file count.
