# 0006: Non-root container, fixed UID 1000, venv outside the bind mount

- **Status:** Accepted
- **Date:** 2026-07-03

## Context

The API container had no `USER` directive and ran as root — flagged as a
mandatory gap in the Sprint 1 review before any "production-ready" claim
could stand. Root-causing it surfaced a second, more concrete problem:
`docker-compose.yml` bind-mounts the host repository over `/app`
(`volumes: .:/app`), which silently shadows the virtualenv built into the
image at `/app/.venv` during `docker build`. At container start, `uv run`
detected the shadowed/missing venv and rebuilt it — as root, directly into
the bind-mounted host directory. This is precisely how a root-owned
`.venv/` ended up on the host filesystem, requiring a workaround at the very
start of this engagement, before the cause was understood.

## Decision

- Create a fixed `appuser` at UID/GID 1000 in the image — the common
  first-user UID convention on Linux dev hosts, chosen so files the
  container writes into the bind-mounted directory remain owned by the
  actual host user rather than an arbitrary/root UID.
- Switch to `appuser` via `USER appuser` before `CMD`.
- Move the virtualenv to `/opt/venv` via `ENV UV_PROJECT_ENVIRONMENT=/opt/venv`,
  entirely outside `/app`, so the bind mount can never shadow it and `uv run`
  never needs write access to the bind-mounted host directory for the venv
  itself.

## Consequences

- Verified via `whoami`/`id` inside both the freshly built image and the
  live, bind-mounted running container: `appuser`, `uid=1000`, not root.
- Verified no new root-owned files appear on the host after a fresh
  `docker compose up`.
- One residual artifact remains outside this decision's control: Docker's
  own daemon creates an empty root-owned mount-point stub
  (`./data/uploads`) on the host when it sets up the nested `uploads_data`
  named-volume-inside-a-bind-mount, *before* the container's `USER` ever
  applies. Confirmed empty (actual uploads live in the named volume) and
  already `.gitignore`d — accepted as a Docker mechanics limitation, not an
  application-level security issue.

  **Correction (see [0009](0009-preseed-upload-directory-ownership-in-image.md)):**
  this same mechanism, applied to the *container*-side mount point rather
  than the host-side stub, is not merely cosmetic — it left
  `/app/data/uploads` root-owned inside the container on a fresh volume,
  which broke every upload. The "not an application-level issue" judgment
  above was wrong; it was a functional regression that a from-zero
  `docker compose down -v && up --build` verification would have caught at
  the time. Fixed in 0009 by pre-creating and chowning the directory in the
  image.
- UID 1000 is hardcoded, not parameterized via a build `ARG`. This is the
  standard convention (matches the default first user on most Linux
  distributions and this project's dev host), but is not guaranteed to
  match every possible host UID — accepted as sufficient for this project's
  current dev/CI environments rather than adding build-arg plumbing for a
  problem that hasn't materialized.
