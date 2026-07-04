# 0026: Named API keys replace the shared static secret

- **Status:** Accepted
- **Date:** 2026-07-04
- **Follows up on:** `docs/architecture/sprint-3-design-baseline.md`
  (section 7, "Identity model scope" — approved at baseline sign-off as
  "bounded to named API keys, not sessions/OAuth"; section 8 lists
  *"Per-caller identity replaces the shared static API key"* as a
  required ADR). Epic 4 in the baseline's ranked epic list (section 5),
  sequenced after the processing-pipeline epics (ADR-0020–0025, ADR-0022
  now implemented) since it becomes more valuable once there's a real job
  lifecycle to attribute actions to — but does not technically depend on
  them.

## Context

Today, `modules/auth/api_key.py` and `modules/auth/middleware.py`
implement a single **shared static secret**: `Settings.api_keys` is a
comma-separated list of interchangeable key strings, and any request
presenting one of them is treated identically to any other. There is no
concept of *who* is calling — confirmed directly: no `ApiKey` model, no
`caller`/`created_by`/`api_key_id` field anywhere in the codebase, and the
`require_api_key`/`ApiKeyGateMiddleware` functions both resolve to a bare
boolean (valid or not), never a name. The README already states this
plainly: "Auth is a shared static key, not identity... anyone with the
key has full access."

This has been tolerable because nothing downstream has ever needed to
know who acted — but the baseline's own epic 5 (audit trail, not started)
explicitly needs a "who" to attribute actions to, and the baseline
sequences that epic *after* this one for exactly that reason: building
an audit log against an undefined identity model would mean guessing at
its shape now and reworking it later.

**Explicitly bounded scope, per the baseline (section 7, approved at sign
off, restated in section 10's risks): named API keys with a label, not
sessions, not accounts, not OAuth, not RBAC.** No UI and no multi-tenant
requirement exists yet to justify more. This ADR resolves the concrete
mechanics of that bound, not whether to expand it.

## Decision

### 1. Storage: extend the existing env-configured key list, not a new DB table

Named keys are still configured via `Settings.api_keys` (env var / `.env`),
**not** a new `ApiKey` database table. This project has consistently
picked the boring, dependency-minimal option when it clears the bar
(ADR-0008's regex over Presidio, ADR-0021's Postgres queue over Redis) —
the same standard applies here: there is no concrete consumer today that
needs keys to be queryable, creatable, or revocable at runtime (no admin
UI, no self-service issuance flow, no requirement in this sprint's scope).
A DB table would add a model, a migration, and (to be useful at all) some
management surface none of that is justified building speculatively.
Revisit if a real operational need appears — e.g., revoking a single
caller's key without a redeploy — at which point the *existing* env-var
model would need replacing anyway, so building it now would be guessing
at a shape a real requirement hasn't yet specified.

### 2. Format: `label:key` pairs, comma-separated

```
API_KEYS=alice-service:sk-live-abc123,bob-cli:sk-live-def456
```

Each comma-separated entry is split on its **first** colon into
`(label, key)`. Both are required — a bare key with no label is a
configuration error, not a silently-accepted anonymous caller (fails
closed the same way an empty `API_KEYS` already does today). This is a
**breaking change to the `API_KEYS` format**, accepted early on the same
reasoning as ADR-0005 and ADR-0022: no real external callers exist yet to
protect against the break, and a graceful dual-format fallback would be
permanent complexity in service of a transition nobody needs. Labels and
keys must not themselves contain `:` or `,` (both are configuration-time
values this project controls the generation of, not user input to
sanitize). Labels are not required to be unique; if a key string is
accidentally duplicated under two labels, later entries win when the
comma-separated string is parsed into a dict — an edge case documented,
not defended against, consistent with this project's existing posture on
unvalidated tunables (e.g. `job_max_retry_attempts`).

### 3. Resolution stays constant-time, per-candidate

`get_valid_api_keys()` now returns `dict[str, str]` (presented key ->
label) instead of `frozenset[str]`. Matching still compares the
*presented* key against every configured key with `hmac.compare_digest`
(never short-circuiting on a dict/hash lookup, which would leak timing
information about which keys are valid) — the resolution changes from
"is this key in the set" to "which label does this key resolve to," not
the comparison discipline itself.

### 4. The resolved label is available to callers, not just a boolean

`require_api_key` (the FastAPI dependency) now returns the resolved
`caller: str` label instead of `None`. It still raises the same 401/503
per today's fail-closed behavior; call sites that don't need the identity
(most routes) are unaffected, since the router-level
`dependencies=[Depends(require_api_key)]` usage already in place
continues to enforce the gate regardless of whether a route also injects
the return value. Routes that want the label add
`caller: str = Depends(require_api_key)` as a normal parameter — FastAPI
resolves the same cached dependency once per request either way, so this
adds no extra check or request overhead.

`ApiKeyGateMiddleware` (the pre-body-read ASGI gate, ADR-0017) resolves
the same label and stashes it on `scope["state"]["caller"]`, so it's
available via `Request.state.caller` at any point in the stack, not only
through the dependency-injected parameter — kept consistent with the
dependency so there's exactly one resolution rule, expressed at two
enforcement points for the two reasons ADR-0017 already established
(the middleware runs first and skips body reads; the dependency is what
makes the requirement visible in the OpenAPI schema).

### 5. What this ADR does *not* do

- **No audit log.** The label is now resolvable; persisting "who did
  what, when" against document/job actions is epic 5, its own ADR, per
  the baseline's explicit sequencing. This ADR only makes the "who"
  available to whatever consumes it next.
- **No key management endpoints.** Provisioning a new named key is still
  an operator editing `.env`/deployment config and restarting, exactly
  today's operational model for the shared secret — just with a label
  attached now. No CRUD surface, no rotation tooling.
- **No per-key scoping/permissions.** Every named key still has identical
  access to every `/documents*` route. Scoped permissions are explicitly
  out of bound per the baseline (section 7's "full accounts, sessions, or
  OAuth" comparison) and not resolved here.

## Consequences

- **Breaking change to the `API_KEYS` env var format** — `.env`,
  `.env.example`, `docker-compose.yml`'s default, and every README
  example update in the same commit that implements this ADR. A deployment
  upgrading past this change must relabel its configured keys or requests
  will fail closed (empty/malformed `API_KEYS` behaves exactly like today's
  "no keys configured" 503, not a silent downgrade).
- **`get_valid_api_keys()`'s return type changes** (`frozenset[str]` ->
  `dict[str, str]`), and `_matches_any` is replaced by a label-resolving
  equivalent — both are internal to `modules/auth`, so this has no effect
  beyond that module and its tests.
- **Sets up epic 5 (audit trail) cleanly**: once that ADR is written, "who
  performed this action" already has a concrete, resolvable value
  (`caller: str`) to attach to whatever record it defines, rather than
  guessing at an identity shape in advance.
- **Deliberately conservative**: a future contributor finding only labels
  (no DB, no admin API, no scoping) should not read that as an oversight —
  each omission above is named, with its explicit revisit trigger, per
  the baseline's own warning against identity scope creep.
