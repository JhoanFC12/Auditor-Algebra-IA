# Contract: Domain Routing And Public Smoke

This contract defines when NexumathJF Studio Factory is considered published on
the public domain.

## Route Contract

| Host/Path | Required behavior | Notes |
|-----------|-------------------|-------|
| `https://api.nexumathjf.com/health` | Returns 200 and safe health JSON. | No auth required. |
| `https://nexumathjf.com/studio` | Returns Studio entry or redirects to login/Studio. | 404 is a failure. 401/403 is acceptable if auth is required. |
| `https://nexumathjf.com/studio/factory/bootstrap` | Returns bootstrap JSON or auth challenge. | 404 is a failure. |
| `https://studio.nexumathjf.com/` | Returns Studio entry or redirects to `/studio`. | 404 is a failure. |
| `https://aula.nexumathjf.com/` | Keeps Aula/student workflow available. | Must not be replaced by Factory. |

## Public Safety Rules

- Public responses must not contain Windows paths such as `C:\`, `D:\` or
  `E:\`.
- Public responses must not contain server private paths unless intentionally
  represented as safe storage identifiers.
- Public responses must not contain tokens, passwords, `.env` values, Python
  tracebacks, or raw SQL connection strings.
- Missing authentication may return 401 or 403, but must not expose internals.

## Deployment Contract

The deployed service must use:

- `docker-compose.production.yml` or equivalent process manager;
- PostgreSQL private to the server/network;
- API bound behind reverse proxy or tunnel;
- server storage under `/srv/mathcontentstudio`;
- model root under `/srv/mathcontentstudio/models`;
- OCR Hugging Face settings from environment variables only.

Deployment automation must distinguish the public domain from the SSH target.
`nexumathjf.com`, `api.nexumathjf.com`, `studio.nexumathjf.com`, and
`aula.nexumathjf.com` are public HTTP routes and may resolve through
Cloudflare. SSH/SCP deployment must use `NEXUMATH_SSH_HOST` or `-SshHost` with
the real server IP/host. `NEXUMATH_SERVER_HOST` remains only as a legacy alias
when `NEXUMATH_SSH_HOST` is empty.

## Verification Contract

The domain routing gate passes only when:

1. API health is reachable.
2. Studio entry is not 404.
3. Factory bootstrap route is not 404.
4. Studio subdomain is not 404.
5. Aula route remains reachable or intentionally protected.
6. Public response samples pass the safety rules.

The authenticated remote smoke is a separate gate and must validate:

1. login;
2. Biblioteca/Fabrica home;
3. book and instance visibility;
4. job recovery after refresh;
5. review/promotion on safe data;
6. Word generation/download;
7. no private path or secret leakage.
