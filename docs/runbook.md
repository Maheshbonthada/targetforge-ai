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
6. Run the CD workflow (once written — see "Not yet built" below) or deploy
   manually: `az deployment group create -g rg-targetforge-dev -f infra/bicep/main.bicep -p infra/bicep/parameters/dev.bicepparam`.
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
  `created_at` timestamp.
- Application Insights end-to-end transaction view traces a single run from
  the API through to the exact PubMed/DGIdb/ClinicalTrials.gov/OpenAI call
  that was slow.

## Not yet built (tracked here so it isn't silently assumed done)

- CI (`.github/workflows/ci.yml`) and CD (`cd.yml`) pipelines — TrialReady's
  are a direct template to adapt once this repo is pushed to GitHub.
- A frontend — not yet requested for this project.
- The real upload path and queue-based execution model in
  `docs/scaling-to-real-genomes.md`.
