# Cost model

Estimates, not quotes — validate against actual Azure retail pricing for your
region/subscription before committing budget. All figures are monthly, USD,
`eastus2`, as designed in `infra/bicep/`. Split into two very different cost
profiles because this platform genuinely has two different workloads.

## Profile A: candidate-gene-list runs (the fully real, deployable-today path)

A handful of R&D scientists triggering `POST /api/v1/runs` with a gene list —
each run makes ~4-7 live external calls (PubMed, DGIdb, ClinicalTrials.gov,
several OpenAI calls) and finishes in 1-3 minutes. No sequencing files, no
Azure Batch compute. This is what `infra/bicep/main.bicep` provisions with
`deployBatchCompute = false` (the default).

| Line item | Tier chosen | Why | Est. $/mo |
|---|---|---|---|
| Container Apps (API) | Consumption, 0.5 vCPU/1GiB, min 1 replica in prod | 1 warm replica in prod avoids a background-task-killed-by-scale-down failure mode (see `container-app-api.bicep`); scale-to-zero in dev/staging | $15–35 |
| Container Apps (migration job) | Consumption, on-demand only | Zero cost when not deploying | ~$0 |
| Postgres Flexible Server | Burstable B2s, 64GB | Stores run metadata + the final JSON report, not genomic files — a light metadata workload | $55–70 |
| Azure OpenAI | S0, gpt-4o-mini (6 of 7 reasoning agents) + gpt-4o (prioritization + report) | Model routing by task (see `core/llm.py`) is the single biggest lever — routing every agent through gpt-4o would roughly double this line | $20–60 (usage-based; scales with runs, ~$0.05-0.15/run in raw token cost at current pricing) |
| External bioinformatics APIs (PubMed, DGIdb, ClinicalTrials.gov, Ensembl VEP) | Free, keyless | Public research infrastructure — the one line item that's genuinely $0 regardless of volume | $0 |
| Blob Storage | Standard LRS (ZRS in prod) | Only holding generated reports at this profile, not raw genomic files | $1–3 |
| Container Registry | Basic | A few image pushes a week | ~$5 |
| Key Vault | Standard, secrets only | Negligible at this volume | <$1 |
| Log Analytics + App Insights | PerGB2018, 30-day retention | Bounded by `LOG_LEVEL=INFO` in prod | $10–20 |
| **Total (prod, steady state)** | | | **~$110–195/mo** |
| **Total (dev, scale-to-zero)** | | | **~$15–35/mo** (mostly Postgres idle minimum) |

## Profile B: real sequencing runs (`run_full_from_fastq`, once wired up)

This is the profile that changes the math entirely, and it is **not what
gets provisioned by default** — `deployBatchCompute = false` until this is
actually needed (see `modules/batch.bicep`'s docstring). Real numbers depend
heavily on cohort size and coverage depth; the ranges below are industry
rule-of-thumb, not a quote:

| Line item | Driver | Est. cost |
|---|---|---|
| Azure Batch compute (BWA-MEM2 alignment + GATK variant calling) | ~$1–3 per 30x whole-genome sample on spot/low-priority `Standard_D8s_v5` nodes, run once per sample | Scales linearly with cohort size — a 100-sample cohort is a few-hundred-dollar compute run, not a monthly recurring cost, since the pool scales to zero between runs |
| Blob Storage (raw FASTQ/BAM) | ~90-150GB per 30x whole-genome sample before lifecycle tiering | $2-4/sample/mo at Hot tier, dropping ~50% at 30 days (Cool) and ~80% at 90 days (Archive) per the lifecycle policy in `storage.bicep` |
| Everything in Profile A | Unchanged | See above |

The reason this is split out rather than blended into one number: a team
evaluating this platform on candidate-gene-list runs should not be quoted a
per-genome compute price they're not paying yet, and a team about to run real
cohorts should not be lulled by Profile A's number into underbudgeting.

## The three biggest levers if this needs to run cheaper

1. **Drop the prod warm replica** (`minReplicas: 0` in
   `container-app-api.bicep`) — reintroduces the background-task-killed-on-
   scale-down risk (see that module's comment), so only do this once run
   execution has moved off the request-serving replica onto a queue-triggered
   job (see `docs/scaling-to-real-genomes.md`).
2. **Route more agents through gpt-4o-mini, fewer through gpt-4o** — only
   `TargetPrioritizationAgent` and `ReportGenerationAgent` use the reasoning
   tier (`deps.py`); every other agent already uses the fast tier by design.
3. **Batch spot pricing, already the default** (`batch.bicep`'s autoscale
   formula targets low-priority nodes) — alignment/variant-calling are
   per-sample, retry-safe jobs, exactly the workload spot pricing exists for.

## The three biggest levers if this needs to run faster (spend to buy latency/throughput)

1. Raise Azure OpenAI deployment TPM capacity (`openai.bicep`) — running
   several pipeline stages concurrently across multiple in-flight runs is a
   throughput question, and under-provisioning here fails as 429s, not slow
   responses.
2. Raise `maxDedicatedNodes` in `batch.bicep` and/or mix in dedicated
   (non-spot) nodes for time-sensitive cohort runs — spot capacity isn't
   guaranteed available at all times.
3. Move pipeline-run execution off FastAPI `BackgroundTasks` onto a proper
   queue-triggered worker (see `docs/scaling-to-real-genomes.md`) — lets the
   API scale independently of run throughput instead of the two competing
   for the same container's resources.
