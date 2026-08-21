# Production readiness audit

A 22-angle review of TargetForge as it stood before this audit — simulating
20 senior engineers and 2 VPs, each looking at the same codebase through a
different lens. Every finding below is grounded in an actual line of code
read during this audit, not a generic checklist; each is marked **Fixed**
(with what changed and where), or **Deferred** (with the reason it wasn't
safe/cheap to fix in this pass and what the real fix looks like). Nothing
here is filler — an angle with nothing wrong isn't listed just to pad the
count to 22.

## The one finding that mattered most

**`core/security.py` (JWT validation against Entra) was written correctly,
copy-pasted from TrialReady, and never actually wired into a single route.**
Every endpoint — create a run, read any run by ID, list all runs — was fully
unauthenticated regardless of `AUTH_DISABLED_FOR_LOCAL_DEV` or Entra config.
Compounding it: `list_runs`/`get_run` had no ownership filtering at all, so
even with auth wired up, any authenticated caller could read (and, once a
delete endpoint existed, delete) any other caller's runs — including their
candidate gene lists, which for a real biotech/pharma user are exactly the
kind of pre-publication data `docs/compliance-checklist.md` already flags as
sensitive. TrialReady's own routers wire the identical `get_current_user`
dependency into every route; this was a real regression, not a design gap.
**Fixed** — see angles 1, 2, and 18 below.

## Findings by angle

### 1. API Security Engineer — CRITICAL — Fixed
No auth enforcement on any route (see above). **Fix:** `Depends(get_current_user)`
added to every route in `api/routers/runs.py`; `triggered_by` now stores the
real authenticated subject instead of a hardcoded `"api-local-dev"` string.

### 2. Application Security Engineer (input validation) — HIGH — Fixed
`RunCreate.candidate_genes` bounded list length (1-25) but not per-item
length or character set — a single arbitrarily large or control-character
string would have been embedded into every prompt across up to 7 LLM-calling
agents (cost blowup, and an unaudited surface for the prompt itself).
**Fix:** `schemas/api.py`'s `GeneSymbol` type (`max_length=20`,
`pattern=^[A-Za-z0-9\-.]+$`), plus server-side normalize/dedupe — the
frontend's `.toUpperCase()` was a UX nicety, never a validation boundary.

### 3. Cost/FinOps Engineer — HIGH — Fixed
No rate limit or spend cap on `POST /runs`, the one endpoint where a bug in
some future caller (or a leaked token) translates directly into uncapped
OpenAI/Azure OpenAI spend. **Fix:** `core/rate_limit.py`'s in-process
sliding-window limiter (5 runs/10min per caller by default, configurable via
`Settings.run_rate_limit_*`), checked before any DB write. Explicitly
documented as a single-replica-correct first line of defense, not the final
word — see that module's docstring on the Redis/API-Management follow-up
once this API runs more than one replica.

### 4. SRE/Reliability Engineer (background task durability) — HIGH — Fixed
A run's execution lives in a FastAPI `BackgroundTask` on the request-serving
replica; a replica restart/redeploy mid-run left the row `RUNNING` forever
with no signal to on-call. This is a known, documented architectural
limitation (`docs/scaling-to-real-genomes.md` §5) that a queue-based rewrite
eventually fixes properly — but nothing detected the symptom in the
meantime. **Fix:** `main.py`'s `_reconcile_orphaned_runs()`, run at process
startup, marks any row still `RUNNING` from a previous process lifetime as
`FAILED` with an explicit "orphaned at startup" message.

### 5. Concurrency Engineer — MEDIUM — Fixed
`get_supervisor()` built a brand-new LLM SDK client and four new
httpx-backed external clients on *every single request* — unbounded
socket/client churn under load for objects with no per-request state.
**Fix:** `deps.py`'s `_build_supervisor()` is now `@lru_cache`d, the same
pattern `db/session.py`'s `get_engine()` already used.

### 6. Timeout/Resilience Engineer — MEDIUM — Fixed
Individual external HTTP calls already had proper timeouts + tenacity
retries (`external/*.py` — this was solid before the audit), but nothing
bounded the *whole* pipeline run. **Fix:** `_execute_run` now wraps the
supervisor call in `asyncio.wait_for(..., timeout=settings.run_timeout_seconds)`
(default 900s), marking the run `FAILED` with a clear message on timeout
instead of holding the background task open indefinitely.

### 7. Idempotency Engineer — MEDIUM — Deferred
No idempotency key on `POST /runs` — a double-click or a client retry
produces a duplicate run (duplicate cost). **Why deferred:** a real fix needs
a new unique-constrained DB column and migration, not just application code;
rate limiting (#3) bounds the damage from this in the meantime. **Real fix:**
accept an optional `Idempotency-Key` header; if a run with that key exists,
return it instead of inserting a new row.

### 8. Database Engineer — LOW — Fixed
`list_runs`'s `limit` query param was validated only by an inline
`min(limit, 100)` — a negative value slipped past that and became `LIMIT -5`
at the SQL layer, failing as an ungraceful 500 instead of a clean 422.
**Fix:** `Query(default=20, ge=1, le=100)`, tested in
`test_list_runs_rejects_out_of_range_limit`. (`db_pool_size=5`/`max_overflow=5`
remain reasonable single-replica defaults — noted, not changed.)

### 9. Observability/FinOps Engineer — MEDIUM — Deferred
Per-run OpenAI token usage is visible in raw structlog output but never
aggregated or persisted — answering "how much did this run cost" today means
grepping logs, not querying a column. **Why deferred:** needs a schema change
(a `token_usage_json` column) and plumbing through every agent's LLM call
site — real work, not a quick fix. **Real fix:** capture `CompletionUsage`
from each `complete_json` call in `core/llm.py`, roll it up onto the
persisted `GenomicsReport`.

### 10. Frontend Reliability Engineer — MEDIUM — Fixed
`client.ts`'s `fetch` had no request timeout — a wedged connection (API up,
DB stalled) left the UI's loading spinner running forever with no signal.
**Fix:** `AbortController`-based 15s timeout, surfaced as a clear `ApiError`.

### 11. Frontend Resilience Engineer (polling) — MEDIUM — Fixed
`RunDetailPage`'s poll loop stopped permanently on the first error of any
kind, discarding the already-loaded run and showing a bare error screen —
including for a one-off network blip mid-run. **Fix:** transient errors (not
401/404) now retry with exponential backoff (capped at 5 consecutive
failures) while keeping the last-known run on screen with a quiet
"Reconnecting…" indicator; only a terminal error (or exhausted retries)
replaces the page.

### 12. CI/CD Engineer — MEDIUM — Fixed
`apps/web` had zero CI coverage — a broken frontend build/lint would merge
clean. Also, the `test` job's "no coverage gate yet" comment was stale
(agent-level and HTTP-router integration tests existed by the time of this
audit). **Fix:** new `frontend` CI job (`npm ci`, build, lint, `npm audit`);
`--cov-fail-under=75` added against a measured 80% baseline.

### 13. Supply-chain/Dependency Security Engineer — HIGH — Partially fixed
No dependency vulnerability scanning existed at all. Running `pip-audit` for
the first time found **29 real known CVEs**, most notably **`python-jose`
3.3.0** — the exact JWT library this audit just made load-bearing for
auth — with known signature-handling CVEs (PYSEC-2024-232/233), fixed in
3.4.0. **Fixed:** bumped `python-jose` to 3.4.0 and `python-multipart`
(6 known CVEs at 0.0.12) to 0.0.32 in `pyproject.toml`; both first-party pins
this project fully controls. **Deferred:** the remaining ~23 findings are in
transitive dependencies (`semantic-kernel` 1.9.0, `starlette`, `pytest`,
`pyasn1`) that would require a major-version bump of a carefully-tuned,
already-tested integration — not safe to do blindly inside this pass. CI's
`pip-audit` step is deliberately non-blocking (see its inline comment) so
this stays visible in every run rather than either silently disappearing or
turning CI permanently red over dependency debt that needs its own
dedicated upgrade-and-retest effort. Added `.github/dependabot.yml` so this
gets tracked continuously going forward, not just at audit time.

### 14. Container Security Engineer — LOW — Fixed
No `.dockerignore` — the Dockerfile only `COPY`s specific paths today (never
a blanket `COPY .`), so nothing was a live leak, but a real OpenAI key has
sat in `apps/api/.env` in this exact repo tree, and that safety depended
entirely on no future edit loosening a `COPY` line. **Fix:** repo-root
`.dockerignore` excluding `.venv/`, `.env` (except `.env.example`),
`node_modules/`, caches, and `.git/`.

### 15. Secrets Management Engineer — LOW — Fixed (via #21)
Real secrets live in git-ignored `.env` locally and Key Vault + managed
identity in production (verified in `infra/bicep/`) — already solid.
`.gitignore` is a convention, not an enforcement mechanism, though.
**Fix:** the `secret-scan` CI job (gitleaks, full history) added under #21
below acts as the backstop `.gitignore` alone can't be.

### 16. API Design/Versioning Engineer — LOW — Reviewed, no change
`health.py`'s routes live outside `/api/v1` while `runs.py` is under it.
This is intentional (health checks are infra-level, not part of the
versioned API surface) and matches common convention — noted here so a
future reviewer doesn't "fix" an inconsistency that isn't one.

### 17. Data Retention/Privacy Engineer — MEDIUM — Fixed
Ties directly to `docs/compliance-checklist.md`'s existing "right to
deletion" gap: even for today's candidate-gene-only entry point, there was
no way to purge a run someone regrets submitting. **Fix:**
`DELETE /api/v1/runs/{id}` (auth- and ownership-scoped), exposed in the
frontend as a Delete action on each run card.

### 18. Multi-tenancy Engineer — HIGH — Fixed
Even once auth is wired up (#1), nothing previously scoped a run to the
caller who created it — any authenticated user could read or delete any
other user's runs. **Fix:** every read/delete route filters by
`PipelineRun.triggered_by == user.subject`; a run belonging to someone else
returns 404, not 403, so an unauthorized caller can't even confirm the run
ID exists. No roles/admin concept exists yet — this is strict per-caller
isolation, matching TrialReady's simplicity level.

### 19. Genomics Domain SME — LOW — Fixed (via #2)
Gene-symbol normalization (`.upper()`) happened client-side only — the
server must never trust that. Folded into #2's fix: normalization now also
happens server-side, so any caller (curl, a future integration) gets
consistent behavior.

### 20. Test Engineer — HIGH — Fixed
No test exercised the HTTP router layer itself — every existing test called
an agent or the Supervisor directly. A regression in the FastAPI wiring (a
route not actually requiring auth, a missing ownership filter, a validation
constraint not attached to the schema) would have passed the entire suite,
which is exactly what had happened. **Fix:** new
`tests/integration/test_runs_api.py` (12 tests, real ASGI app over HTTP,
real Postgres, background execution stubbed) plus
`tests/unit/test_security.py` (3 tests for the auth-rejection path itself).
Two of these tests caught real bugs on first run — see "Bugs this audit
caught by testing, not by reading" below.

### 21. VP Engineering (org/operational risk) — CRITICAL — Fixed
The single worst real-world failure mode was findings #1 + #18 + #3 stacked
together: as originally deployable, this API would let anyone who could
reach it read every run ever submitted and rack up unbounded LLM spend, with
the audit trail crediting it all to a hardcoded string. This is the one
class of finding that blocks calling something "production," full stop —
not a nice-to-have to schedule later. All three are fixed; the `secret-scan`
CI job (gitleaks) was added under this angle as the same category of
blast-radius control.

### 22. VP Product/Legal (business risk) — HIGH — Fixed
Sharpens `docs/compliance-checklist.md`'s existing gap: if this is ever used
against a real pharma partner's unpublished candidate-gene shortlist
(commercially sensitive, pre-patent information), no-auth + no-per-user-
isolation + no-deletion was a genuine liability. All three are fixed by #1,
#18, #17. `docs/compliance-checklist.md` has been updated to reflect that
operator-level access control now exists (network isolation, the BAA, and
the other go-live items remain open, as before).

## Bugs this audit caught by testing, not by reading

Two real bugs shipped in the fixes above and were caught within minutes by
the tests written alongside them, not by re-reading the code:

1. **Schema validation order bug:** the new per-gene validator trimmed
   whitespace *after* pydantic's pattern check ran, so a legitimately
   whitespace-padded input like `" kras "` (exactly what pasting
   "KRAS, EGFR, TP53" into the frontend's textarea produces) was rejected as
   invalid. Caught by
   `test_candidate_genes_are_normalized_and_deduped_server_side`. Fixed by
   moving normalization into a `mode="before"` validator.
2. **Rate limiter test bug (in the test itself, not the feature):** the
   first version of the 429 test overrode `get_run_rate_limiter` with a
   lambda that constructed a *new* limiter instance per call, so it never
   accumulated call history and the limit never actually triggered. Fixed by
   sharing one instance across the override.

Both are exactly the category of bug a design review would not have caught
and a real test run did.

## What this audit did not touch

Scoped to the API/frontend/CI layer that could be verified for real in this
pass. Two categories are explicitly out of scope here and tracked
elsewhere: the Azure Batch/real-sequencing compute path
(`docs/scaling-to-real-genomes.md`) and the full genomic-data compliance
checklist (`docs/compliance-checklist.md`) — this audit closes the
*operator access control* item on that checklist, not the IRB/consent/BAA
items, which were never claimed to be closed.
