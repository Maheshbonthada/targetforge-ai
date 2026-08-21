# Runbook

## Local development

```
docker compose up -d                      # Postgres on localhost:5434
cd apps/api
py -3.11 -m venv .venv && source .venv/Scripts/activate   # Windows/Git Bash
pip install -e ".[dev]"
cp .env.example .env                      # fill in OPENAI_API_KEY (or leave AI_PROVIDER=azure_openai + endpoint for Azure)
python -m alembic upgrade head
python -m pytest -q
python -m uvicorn targetforge.main:app --reload --port 8000
# API at http://localhost:8000, docs at http://localhost:8000/docs
```

With `AI_PROVIDER=openai` and a real `OPENAI_API_KEY`, every reasoning agent
(6-12) calls the real model. With no Batch account configured
(`BATCH_ACCOUNT_URL` unset), alignment/variant-calling fall back to
`FakeAlignmentBackend`/`FakeVariantCallingBackend` (`deps.py`) — a full
candidate-gene-list run can be exercised with just an OpenAI key and no other
Azure credentials at all, which is exactly how this repo has been tested.

Trigger a real run:
```
curl -X POST http://localhost:8000/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"candidate_genes": ["KRAS", "EGFR", "TP53"], "top_n_for_experiments": 2}'
curl http://localhost:8000/api/v1/runs/<id-from-above>
```

## Docker build/run locally

```
docker build -f apps/api/Dockerfile -t targetforge-api .
docker run --rm -p 8000:8000 --network <compose-network> \
  -e DATABASE_URL=postgresql+asyncpg://targetforge:targetforge@targetforge-postgres:5432/targetforge \
  -e AI_PROVIDER=openai -e OPENAI_API_KEY=<key> \
  targetforge-api
```
Build context must be the repo root (`-f apps/api/Dockerfile .`), not
`apps/api/` — the image needs both `apps/api/src` and the top-level
`data/knowledge/pathways.yaml`.

## Frontend local development

```
cp apps/web/.env.example apps/web/.env.local   # points at http://localhost:8000 by default
cd apps/web
npm install
npm run dev
```
Open `http://localhost:5173`. Submitting the form POSTs to the API above,
then polls `GET /api/v1/runs/{id}` every few seconds until the run finishes,
rendering the pipeline-stage grid, ranked-target table, and the full cited
markdown report. If the API is running on a different port/host, edit
`VITE_API_BASE_URL` in `.env.local` — and if the web dev server's own origin
differs from the API's `CORS_ALLOWED_ORIGINS` (`config.py`), the browser will
reject every request with a CORS error, not a network error, so check that
first if the app loads but shows "Could not reach the TargetForge API."

## First-time Azure environment setup

1. `az group create -n rg-targetforge-dev -l eastus2`
2. Generate a strong Postgres admin password, store as a GitHub Actions
   secret (`POSTGRES_ADMIN_PASSWORD`) — never in `.bicepparam`.
3. Set up federated OIDC credentials for GitHub Actions → Azure. Store
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` as repo
   secrets.
4. Validate the template before first deploy —
   `az bicep build --file infra/bicep/main.bicep` — this repo's Bicep has not
   yet been compiled against a real `az` install; treat the first real run of
   this command as part of setup, not a formality.
5. First deploy uses the placeholder `mcr.microsoft.com/k8se/quickstart`
   image (`main.bicep`'s `containerImage` default) so the environment stands
   up before CI has published a real image.
6. Run `.github/workflows/cd.yml` (`workflow_dispatch`, `environment: dev`)
   once the Azure secrets above are in place, or deploy manually:
   `az deployment group create -g rg-targetforge-dev -f infra/bicep/main.bicep -p infra/bicep/parameters/dev.bicepparam`.
7. **Known first-deploy quirk** (same as TrialReady's stack): the container
   app's managed identity is granted `AcrPull` in the same deployment that
   tries to pull the image with it. If the first revision fails with an
   image-pull error, wait ~5 minutes for RBAC propagation and restart the
   revision rather than re-running the whole deployment.
8. Run the migration job once:
   `az containerapp job start --name tf-dev-migrate --resource-group rg-targetforge-dev`.
9. Leave `deployBatchCompute = false` until you have a real reference genome
   and container image — see `docs/scaling-to-real-genomes.md`.

## Rollback

Container Apps keeps prior revisions:
```
az containerapp revision list --name tf-prod-api --resource-group rg-targetforge-prod -o table
az containerapp ingress traffic set --name tf-prod-api --resource-group rg-targetforge-prod \
  --revision-weight <previous-revision>=100
```
This does not roll back a database migration — write migrations
additive/backward-compatible for the same reason as TrialReady's stack.

## On-call basics

- `/healthz` — liveness, never touches the DB. Failure here means restart the
  container.
- `/readyz` — readiness, touches Postgres. Failure here with liveness healthy
  means investigate the DB connection.
- A run stuck at `status: "running"` past a few minutes with no stage
  progressing in `stage_status_json` most likely means the replica executing
  its `BackgroundTask` was recycled mid-run (see
  `docs/scaling-to-real-genomes.md` §5 — this is the known limitation of the
  current execution model, not a mystery to debug from scratch each time).
  Check Container Apps revision history for a restart around the run's
  `created_at` timestamp. On the next process startup, any such row is
  automatically reconciled to `FAILED` (`main.py`'s
  `_reconcile_orphaned_runs`) — it will not silently stay "running" forever,
  but it also will not resume; the run has to be resubmitted.
- A burst of `429 Too Many Requests` on `POST /runs` from one caller is the
  rate limiter (`core/rate_limit.py`) doing its job, not a bug — check
  `Settings.run_rate_limit_max_calls`/`run_rate_limit_window_seconds` before
  assuming something's broken. Remember it's in-process and per-replica; at
  more than one replica it under-counts (see that module's docstring).
- A run taking noticeably longer than usual and then failing with "exceeded
  the Ns time budget" is the wall-clock timeout (`Settings.run_timeout_seconds`,
  default 900s) — check whether an external API (PubMed/DGIdb/ClinicalTrials.gov/
  OpenAI) was degraded around that time before assuming the pipeline itself
  regressed.
- Application Insights end-to-end transaction view traces a single run from
  the API through to the exact PubMed/DGIdb/ClinicalTrials.gov/OpenAI call
  that was slow.

## Not yet built (tracked here so it isn't silently assumed done)

- Static Web Apps (or equivalent) hosting for the frontend — `apps/web` is
  built and proven locally (real API, real browser, Playwright-verified) but
  has no `infra/bicep` module or CD job deploying it yet; TrialReady's
  `static-web-app.bicep` + `web-cd.yml` are a direct template to adapt.
- **A real login flow for the frontend.** Every API route now requires
  authentication (`docs/production-readiness-audit.md`, finding #1) unless
  `AUTH_DISABLED_FOR_LOCAL_DEV=true` — which is what local dev runs against,
  so nothing above changes today. But the frontend has no MSAL/Entra sign-in
  built, so it cannot successfully call a real deployment
  (`AUTH_DISABLED_FOR_LOCAL_DEV=false`, the `bicep` default) until one
  exists. Deploying the frontend as-is against a production API would show
  every request failing with 401 — that's expected given the current state,
  not a bug to chase.
- The real upload path and queue-based execution model in
  `docs/scaling-to-real-genomes.md`.
- Idempotency keys on `POST /runs` and per-run token-usage tracking — see
  `docs/production-readiness-audit.md`, findings #7 and #9.
