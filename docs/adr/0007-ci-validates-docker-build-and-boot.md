# 0007: CI validates the Docker build and boot, not just the Python package

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

The first CI job (`test`: ruff, black, mypy, pytest) validated the Python
package in isolation and never touched Docker. That job would not have
caught a broken `Dockerfile`, a broken `docker-compose.yml`, or the `api`
container regressing back to running as root (see
[0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md)) — and
it would not have caught the `uvicorn --reload` / migration race condition
discovered manually during Sprint 1 (see
[0003](0003-alembic-migrations-over-implicit-create-all.md)). The thing this
project actually ships is the container; nothing automated verified it even
builds.

## Decision

Add a second CI job, `docker`, that runs on every push and pull request:

1. `docker compose build`
2. `docker compose up --wait --wait-timeout 90` — fails if either service
   doesn't reach its own healthcheck
3. `curl -sf http://localhost:8000/health | grep -q '"status":"healthy"'`
   — smoke test against the real, composed stack
4. `docker compose exec -T api whoami` asserted to not be `root` — directly
   enforces [0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md)
   stays fixed
5. `docker compose logs` on failure, `docker compose down -v` unconditionally
   (`if: always()`)

No Postgres service definition needed in CI beyond what's already in
`docker-compose.yml` — the job uses the real compose file, not a CI-specific
variant.

## Consequences

- Adds roughly 40 seconds to every CI run.
- In exchange, a broken Dockerfile, a broken compose config, or a root-user
  regression fails CI immediately instead of surfacing manually, later, in
  someone's terminal — which is exactly how the two problems this job now
  guards against were originally found.
- The full step sequence was verified against a genuinely fresh `git clone`
  (not just the working tree) before this job was committed, and confirmed
  green on GitHub's actual runners, not just asserted to work locally.
