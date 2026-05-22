# Main Computer local website platform

This Docker Compose platform is the direct-port foundation for the future
Applications → Website Builder workflow.

It intentionally does **not** use WSL, SSH, Coolify, Caddy, Traefik,
hostnames, HTTPS, port 80, or port 443. A reverse proxy can be added later
after the Docker-shaped local workflow is stable.

## Direct local URLs

- Hub Site local publish: <http://0.0.0.0:18080/>
- Blog Site local publish: <http://0.0.0.0:18081/>
- Hub Site dev publish: <http://0.0.0.0:18082/>
- Blog Site dev publish: <http://0.0.0.0:18083/>

## Commands

Start all local-platform services:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\up-local-platform.ps1
```

Publish/restart one registered website lane:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\publish-website.ps1 -SiteId hub-site -Lane dev
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\publish-website.ps1 -SiteId hub-site -Lane local
```

The `local` lane is the current local/prod-style direct-port lane. The Python
publish tool also accepts `prod` or `production` as aliases for `local`.


Manage one website Docker lane with the registry-backed lifecycle command:

```powershell
python .\tools\local-platform\website-docker.py install hub-site
python .\tools\local-platform\website-docker.py start hub-site --lane dev
python .\tools\local-platform\website-docker.py stop hub-site --lane dev
python .\tools\local-platform\website-docker.py publish hub-site --lane dev
python .\tools\local-platform\website-docker.py verify hub-site --lane dev
python .\tools\local-platform\website-docker.py logs hub-site --lane dev
```

The lifecycle command uses:

```text
runtime/local-platform/sites.json
deploy/local-platform/generated/docker-compose.websites.yml
```

The older `publish-website.py` and `publish-website.ps1` commands remain
available for compatibility while the Website Builder is being wired to the
new lifecycle command.

Verify endpoints:

```powershell
python .\tools\local-platform\verify-local-platform.py
```

Stop the platform:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-platform\down-local-platform.ps1
```

Generate the registry-backed website Compose file:

```powershell
python .\tools\local-platform\generate-websites-compose.py --repo-root .
```

The generated file is written to:

```text
deploy/local-platform/generated/docker-compose.websites.yml
```

The current publish wrapper still uses the existing fixed Compose services for
compatibility. The generated file is the Phase 4 foundation for the later
website Docker lifecycle command and new-site provisioning flow.

The site server reads files from `runtime/websites/<site-id>/`, so edits saved
by the Website Builder are visible to the local Docker service after publish.
Responses are sent with `Cache-Control: no-store` so the embedded preview updates
after a save/publish loop instead of showing stale CSS or HTML.
