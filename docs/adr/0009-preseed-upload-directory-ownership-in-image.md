# 0009: Pre-seed the upload directory in the image so named-volume ownership is correct

- **Status:** Accepted
- **Date:** 2026-07-03
- **Corrects a claim in:** [0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md)

## Context

While verifying Sprint 2's PHI-detection change against the live
docker-compose stack (unrelated to that change — a routine full-flow smoke
test), document upload started failing with `PermissionError: [Errno 13]
Permission denied: '/app/data/uploads/...'`. Root cause: the
`uploads_data` named volume is mounted at `/app/data/uploads`, a path that
does not exist anywhere in the image. Docker initializes a fresh named
volume from whatever is already at its mount point *in the image* —
content and ownership both. Since the path didn't exist in the image, the
Docker daemon created the mount point itself, as root, independent of the
container's configured `USER appuser`. `appuser` (uid 1000) could then
never write into it.

This directly contradicts a claim made in
[0006](0006-non-root-container-fixed-uid-venv-outside-bind-mount.md), which
observed a similar root-owned artifact on the *host* side (an empty
`data/uploads` mount-point stub) and judged it "confirmed empty ... accepted
as a Docker mechanics limitation, not an application-level security issue."
That was true for the host-side stub specifically, but incomplete: the same
mechanism, applied to the *container*-side mount point for the same named
volume, doesn't just leave a cosmetic empty directory — it silently breaks
every write into that volume. The Sprint 1.5 verification pass that
introduced the non-root user did include an upload smoke test that passed;
in hindsight, it's unclear whether that test ran against a volume created
before the non-root change (i.e., already appuser-writable from an earlier,
different container state) rather than a genuinely fresh one. This
inconsistency itself is evidence the earlier verification wasn't as
rigorous as it should have been for this specific case — a fresh, from-zero
`docker compose down -v && up --build` should have been the standard, not
an assumption.

Separately: the CI `docker` job added in
[0007](0007-ci-validates-docker-build-and-boot.md) did not catch this. Its
smoke test only ever exercised `/health`, which touches Postgres but never
the storage backend. A real upload was never exercised by CI.

## Decision

- `infrastructure/docker/api.Dockerfile`: `RUN mkdir -p /app/data/uploads`
  before the `chown -R appuser:appuser` step, so the path exists in the
  image with correct ownership *before* any named volume is ever mounted
  there. A fresh `uploads_data` volume now inherits `appuser:appuser`
  ownership on first use.
- `.github/workflows/ci.yml`'s `docker` job gained a new step: upload a
  file and process it through the real, live compose stack (real auth
  header, real write to the named volume, real Postgres), asserting the
  document reaches `status: validated`. This exercises the exact code path
  that broke and would not have been caught by a `/health`-only check.

## Consequences

- Verified against a genuinely fresh `docker compose down -v && up --build`
  (not a reused volume): `/app/data/uploads` is `appuser:appuser` from
  first mount, upload → process succeeds end-to-end.
- The general lesson generalizes beyond this one directory: *any* future
  named volume mounted under a non-root container needs its mount point
  pre-created and chowned in the image, or it will silently default to
  root ownership on first use. Worth checking explicitly if
  `docker-compose.yml` ever gains another named volume under `/app`.
- This was found by manual verification, not by the CI job meant to catch
  exactly this class of regression — a reminder that a smoke test is only
  as good as the code paths it actually exercises, not the ones it's
  named after.
