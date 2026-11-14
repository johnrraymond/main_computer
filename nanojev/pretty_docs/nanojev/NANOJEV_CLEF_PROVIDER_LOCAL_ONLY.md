# NanoJev/CLEF local provider — staged integration (V3)

## Scope

This patch adds `nanojev` alongside `ollama`, `openai`, and `hub` as a **locally selectable provider**, but does **not** connect it to the Hub.

**Excluded from this patch:** Stable Hub, Hub client, live WebSocket sessions, worker-pull logic, dispatch/routing on the Hub, capability-market advertising, signing, credits, settlement, and deployment. There are **no changes** to `main_computer/hub.py`, `main_computer/hub_cli_live_session.py`, `main_computer/viewport_routes_energy.py`, or any Hub infrastructure. The provider is not advertised as a Hub worker.

## Native capabilities

| Capability | Local CLEF endpoint | Payload |
|---|---|---|
| `nanojev.clef.evaluate.v1` | `POST /api/evaluate` | Existing `{ "states": [...] }` typed request |
| `nanojev.clef.pairwise-batch.v1` | `POST /api/evaluate-batch` | Existing `nanojev.pairwise-batch.v1` batch request |

The provider checks `GET /api/health`, accepts only a loaded `nanojev-clef` release, validates the native structured response, checks checkpoint manifest consistency, and emits a `main-computer.nanojev-solution.v1` envelope. The checkpoint metadata is **self-reported by CLEF**, not independently attested; solution correctness is not guaranteed.

The shared `LLMProvider.chat` interface is present for registration compatibility, but fails explicitly: CLEF is not `chat.completions`, and it must not guess how to turn conversational instructions into solver questions. Typed requests are invoked through `MainComputer.solve(capability=..., payload=...)` or the local smoke utility.

## Test before any Hub work

From `C:\Users\subsi\main_computer` after applying the patch:

```powershell
python -m pytest -q tests/test_nanojev_provider_local.py
```

The managed NanoJev controller exposes its stable local proxy on **9765**. The CLEF Docker backend is on **9766** and may be stopped by the lazy lifecycle manager after the idle timeout. The provider defaults to **9765** so its health and inference requests wake the backend when needed. Set `--base-url http://127.0.0.1:9766` only for direct-backend testing while the container is running.

Inspect the manager **without** starting the CLEF backend:

```powershell
Invoke-RestMethod http://127.0.0.1:9765/control/status | ConvertTo-Json -Depth 6
```

If port 9765 refuses a connection, launch your existing Main Computer startup (`.\start.bat`) with NanoJev managed mode enabled before attempting the provider smoke. The manager needs its prepared Docker image/container; missing image errors should be diagnosed through its status and image-bootstrap path. The provider does not start a lifecycle manager process itself.

Readiness probe (no inference, but **may start the idle backend**):

```powershell
python -m tools.nanojev_provider_smoke --base-url http://127.0.0.1:9765 --health
```

To execute a native request, save this JSON to `clef_request.json`:

```json
{
  "states": [
    {
      "id": "s1",
      "state": "A spacecraft must select a route.",
      "questions": {
        "route": {
          "type": "choice",
          "instructions": "Select a route.",
          "criteria": {"left": "Go around", "right": "Go through"}
        }
      }
    }
  ]
}
```

Then:

```powershell
python -m tools.nanojev_provider_smoke `
  --base-url http://127.0.0.1:9765 `
  --capability nanojev.clef.evaluate.v1 `
  --payload-file .\clef_request.json
```

This sends a **local CLEF HTTP inference request** but no Hub work request; no Hub credits are spent. The `--health` command performs no inference but can activate a stopped CLEF backend through the lifecycle manager. Environment/default local registration is also supported:

```powershell
$env:MAIN_COMPUTER_PROVIDER = 'nanojev'
$env:MAIN_COMPUTER_NANOJEV_BASE_URL = 'http://127.0.0.1:9765'
python -m main_computer.cli providers
```

`--provider nanojev` also works when constructing `MainComputer`, but chat commands will reject attempts to call CLEF as a conversation model. This patch does not advertise CLEF on the existing Hub worker CLI.

## Future integration (not implemented)

A later, separate patch can adapt the transport-neutral `MainComputer.solve()` envelope to worker capability advertising, Hub work dispatch and settlement, without changing the local solver protocol.
