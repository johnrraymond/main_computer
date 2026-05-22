# Local Docker Coolify Smoke

This smoke path is for validating the local-prod idea before wiring it into Main Computer:

```text
Main Computer Dev Preview  -> existing fast local runtime
Main Computer Local Prod   -> one local Coolify running on local Docker
Main Computer Remote Prod  -> user-selected remote Coolify later
```

This intentionally does **not** use WSL, `systemctl`, `sudo`, or the Linux quick installer.

## What this proves

This smoke verifies whether Coolify can run on your local Docker engine well enough for Main Computer to use it as a local-prod controller.

It checks:

```text
Docker CLI is reachable
Docker Compose v2 is reachable
Coolify containers can start from local Docker Compose
Coolify dashboard/health endpoint responds on localhost
Coolify database migrations are complete before login/auth smoke
Coolify root user setup is completed automatically
Coolify first-run onboarding does not block local smoke automation
Generated credentials can log in
A local API token can be created and used for a project API smoke
Coolify can create/deploy a minimal nginx compose service through its API
The nginx smoke site is reachable on http://127.0.0.1:18081 with a deterministic smoke marker
```

## What this does not prove yet

This is not yet Main Computer deployment automation.

It does not yet prove:

```text
Coolify can deploy every site shape we need
Directus + SQLite works through Coolify
Remote Coolify publishing works
```

Those should come after this smoke passes.

## Start local Coolify

From the repository root in PowerShell, prefer the Python entrypoint. It avoids
PowerShell execution-policy/signing issues:

```powershell
python .\tools\local-prod\coolify-local-docker.py preflight
python .\tools\local-prod\coolify-local-docker.py up
python .\tools\local-prod\coolify-local-docker.py status
python .\tools\local-prod\coolify-local-docker.py auth-smoke
python .\tools\local-prod\coolify-local-docker.py api-smoke
python .\tools\local-prod\coolify-local-docker.py deploy-smoke
```

The deployment smoke chooses a free local host port in `19080-19120`, writes the
selected service/port/marker to `runtime/coolify-local-docker/deploy-smoke.json`,
and verifies that exact marker response. It also inspects the matching Coolify
service row before reuse and discards stale local smoke services that were
created before `connect_to_docker_network=true` was included in the API payload.
This avoids waiting on a host port for a service that Coolify accepted but cannot
actually start.


The PowerShell wrapper is only a convenience:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-prod\coolify-local-docker-control.ps1 preflight
powershell -ExecutionPolicy Bypass -File .\tools\local-prod\coolify-local-docker-control.ps1 up
powershell -ExecutionPolicy Bypass -File .\tools\local-prod\coolify-local-docker-control.ps1 status
```

The script creates local state under:

```text
runtime/coolify-local-docker/
```

The generated `.env` file is managed by the tool. Do not hand-edit it; rerunning
`python .\tools\local-prod\coolify-local-docker.py up` repairs required local Docker
host values such as `DB_HOST=postgres` and `REDIS_HOST=redis`, plus the pinned local realtime image tag `LATEST_REALTIME_VERSION=1.4-16-debian`.

The Coolify container can report healthy before every Laravel migration needed by
login/API smoke has completed. The tool checks the local Coolify database schema
before it logs in. If required columns are missing, it runs
`php artisan migrate --force` inside only the local smoke Coolify container and
re-checks the schema. If Coolify reports `INFO Nothing to migrate.` but the
current local image still writes the known `users.currentTeam` field during
registration/login, the tool applies a narrow local-only compatibility repair by
adding nullable `users."currentTeam"` JSON storage. Unknown schema gaps still
fail instead of being guessed.

You can run the migration check explicitly:

```powershell
python .\tools\local-prod\coolify-local-docker.py migrate
```

The tool also repairs generated root bootstrap values and automatically completes
Coolify's local first-run Boot User Setup form when Coolify renders it. It submits
the generated credentials to the local `/register` form with the page CSRF token,
so users should not create the root account manually in the browser. The root
email uses a DNS-valid domain, and the generated root password includes uppercase,
lowercase, number, and symbol characters.

The generated dashboard credentials are written to:

```text
runtime/coolify-local-docker/credentials.txt
```

If the stack is already running and the browser shows Boot User Setup, rerun
`up`, `status`, or the explicit bootstrap action. The tool will use the local
form to create the generated root account and then fail only if the form remains
visible:

```powershell
python .\tools\local-prod\coolify-local-docker.py bootstrap
python .\tools\local-prod\coolify-local-docker.py status
```


After the root account is created, Coolify may show the normal "Welcome to
Coolify" first-run onboarding page. For local smoke, the tool disables that
onboarding state in only this local smoke instance's Coolify database. Coolify
keeps the current team as both a row in `teams` and a JSON snapshot in
`users."currentTeam"`, so the local skip updates both copies. Some Coolify builds
can still render browser routes such as `/` or `/projects` as onboarding after
those flags are disabled, so the script treats the bearer-token API path as the
local smoke gate. Status/onboard pass when the generated credentials can
authenticate and the local API token can list/create the smoke project. It does
not change any remote Coolify server.

You can run the onboarding/auth/API phases explicitly:

```powershell
python .\tools\local-prod\coolify-local-docker.py onboard
python .\tools\local-prod\coolify-local-docker.py auth-smoke
python .\tools\local-prod\coolify-local-docker.py api-smoke
python .\tools\local-prod\coolify-local-docker.py deploy-smoke
```

The API smoke enables the local Coolify API, writes a local bearer token to:

```text
runtime/coolify-local-docker/api-token.txt
```

and verifies that the token can list projects and create or find the
`Main Computer Local Smoke` project through the Coolify API. The API smoke uses
the local database bootstrap/onboarding checks plus a bearer-token request path,
rather than repeated browser login attempts, so it is less likely to trip
Coolify's login rate limit. If `auth-smoke` is run repeatedly and Coolify returns
HTTP 429, the tool waits once for the local login throttle window before retrying.

If bootstrap state becomes inconsistent, clear only the local Coolify smoke state
and start again:

```powershell
python .\tools\local-prod\coolify-local-docker.py down
python .\tools\local-prod\coolify-local-docker.py reset
python .\tools\local-prod\coolify-local-docker.py up
python .\tools\local-prod\coolify-local-docker.py status
```

The dashboard defaults to:

```text
http://127.0.0.1:8000
```

## Deploy smoke

After `api-smoke` passes, run:

```powershell
python .\tools\local-prod\coolify-local-docker.py deploy-smoke
```

This command is still local-smoke-only. It uses the generated local API token, the
local smoke project, the localhost server/destination that Coolify seeds for its
own Docker host, and:

```text
deploy/coolify/local-docker/smoke-nginx.compose.yml
```

It creates a local smoke service through Coolify's service API, requests a
deployment, drains the local Coolify queue once for queued start/deploy work, and
then waits for a deterministic marker page. The script no longer uses a fixed
smoke port, because other local Main Computer services may already be listening
on ports such as `18080` or `18081`. Instead it chooses a free port in
`19080-19120`, stores the selected service/port/marker in:

```text
runtime/coolify-local-docker/deploy-smoke.json
```

and verifies the exact marker from that state file. Before requesting the
deployment, it also ensures the local Coolify destination Docker network exists.
A `200` response from some other local site is treated as a stale/conflicting
smoke state and the next run will allocate a new free port. The direct host port
mapping is intentional for this smoke because it proves Coolify can drive Docker
deployment before Main Computer starts relying on Coolify proxy/domain routing.

If Coolify's queued `StartService` job fails, the command now fails immediately
instead of waiting for the port timeout. The failure output includes the local
Coolify service record, the latest matching `failed_jobs` exception, recent
Coolify container logs, and Docker container/published-port diagnostics for the
generated smoke service. That keeps the next correction tied to the exact local
Coolify error instead of guessing.

## Stop or reset

Stop containers while keeping state:

```powershell
python .\tools\local-prod\coolify-local-docker.py down
```

Destroy this smoke stack's containers, named volumes, and local smoke state:

```powershell
python .\tools\local-prod\coolify-local-docker.py reset
```

The reset command is intentionally scoped to this repository's local Coolify
smoke project and `runtime/coolify-local-docker/`.

## Notes

Coolify's official quick installer is still the recommended production Linux-server install path. This smoke is intentionally narrower: it tests whether local Docker can host a useful local-prod Coolify controller for Main Computer development.
