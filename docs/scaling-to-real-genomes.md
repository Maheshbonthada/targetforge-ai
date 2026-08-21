# Scaling to real genomes

This document is the honest map from "what runs today" to "what a real
whole-genome-sequencing cohort run needs" — referenced from several code
docstrings (`bioinformatics/alignment.py`, `bioinformatics/variant_calling.py`,
`orchestration/supervisor.py`, `deps.py`) rather than duplicated in each of
them. Read it before flipping `deployBatchCompute = true` or wiring a real
upload path to `run_full_from_fastq`.

## What's real today vs. what this section closes

Agents 6-12 (pathway enrichment through report generation) are fully real —
they call live PubMed, DGIdb, ClinicalTrials.gov, and Ensembl VEP, and are
exercised end-to-end by `tests/` and by triggering a real run through
`POST /api/v1/runs`. Agents 1-3 (QC, alignment, variant calling) are real
*orchestration* code — genuine Azure Batch job-submission shapes
(`AzureBatchAlignmentBackend`, `AzureBatchVariantCallingBackend`) — pointed at
a `FakeAlignmentBackend`/`FakeVariantCallingBackend` in every environment this
repo has actually run in, because a real run needs three things this sandbox
doesn't have: a reference genome, a BWA-MEM2/GATK container image, and GPU/
CPU-hour budget. This document is the checklist for supplying those three
things for real.

## 1. Reference genome and index files

- Download a reference build (GRCh38 is the default assumed throughout
  `schemas/pipeline.py` and `orchestration/supervisor.py`) from a public
  source (e.g., NCBI, Ensembl, or the Broad's GATK resource bundle).
- Build BWA-MEM2 index files (`bwa-mem2 index`) and a GATK sequence
  dictionary (`gatk CreateSequenceDictionary`) once, store the result in the
  `genomic-files` Storage container (`infra/bicep/modules/storage.bicep`)
  under a versioned prefix (e.g., `reference/GRCh38/`), and reference that
  path from `AlignmentBackend`/`VariantCallingBackend` job parameters instead
  of a placeholder.
- Known known-sites VCFs for GATK base quality score recalibration (dbSNP,
  Mills indels) come from the same GATK resource bundle — same storage
  treatment.

## 2. A real BWA-MEM2/GATK container image

`modules/batch.bicep`'s pool deliberately does not bake bioinformatics tools
into the node image — that's a deploy-time artifact, not infrastructure.
Build (or adopt an existing) container image with BWA-MEM2 and GATK
installed, push it to the same Azure Container Registry this stack already
provisions (`modules/container-registry.bicep`), and point the Batch pool's
start task or per-task container settings at it.
`AzureBatchAlignmentBackend`/`AzureBatchVariantCallingBackend`
(`bioinformatics/alignment.py`, `variant_calling.py`) already submit tasks
shaped for a container-based Batch task (`TaskContainerSettings`); the image
reference itself is the piece left as a config value, not hardcoded, so this
is a deploy-time decision, not a code change.

## 3. Wiring stages 3 → 4 → 5 together from a real cohort

Today, `run_full_from_fastq` accepts `variant_counts` and `called_variants`
as direct parameters (see its docstring in `orchestration/supervisor.py`)
because there's no real GATK output to parse in this sandbox. Once real
variant calling produces an actual joint-genotyped VCF:

1. Parse the VCF (a `pysam`/`cyvcf2` dependency this repo doesn't carry yet —
   add it) into `CalledVariant` records for `VariantAnnotationAgent`.
2. Derive per-variant `VariantCounts` (case/control allele counts) from the
   same VCF plus a case/control sample manifest, for
   `CohortAssociationAgent`.

This is a parsing/data-mapping step between real Batch output and the
already-real statistics/reasoning agents downstream — not a change to
`association_stats.py`'s Fisher's-exact-test logic, which is genuinely
tested today against synthetic `VariantCounts` and works unchanged against
real ones.

## 4. Upload path

There is currently no HTTP endpoint that accepts raw FASTQ uploads —
`api/routers/runs.py` only exposes the candidate-gene-list entry point. Add
an upload flow (presigned/SAS blob upload directly to the `genomic-files`
container is the standard pattern — keeps multi-GB files off the API
container entirely) before exposing `run_full_from_fastq` over HTTP. Revisit
`docs/compliance-checklist.md`'s access-control item at the same time — a
real upload endpoint is exactly where per-run/per-study scoping needs to
exist, not broad `Storage Blob Data Contributor` access.

## 5. Execution model: move off FastAPI BackgroundTasks

`api/routers/runs.py`'s background-task pattern is fine for a 1-3 minute
candidate-gene-list run. A real sequencing run's alignment + variant-calling
stages alone can run for hours (bounded by cohort size and Batch pool
capacity, not by anything in this codebase). At that duration, holding the
run inside a FastAPI `BackgroundTask` on a Container Apps replica is the
wrong shape — a replica restart (deploy, scale-down, node patch) silently
kills the run.

The fix is a queue-triggered Container Apps Job (or equivalent): the API
enqueues a run (Service Bus, or a `pending` row in `pipeline_runs` polled by
a separate worker) and returns immediately; a separately-scaled worker picks
it up, survives independently of the API's own replica lifecycle, and
updates the same `PipelineRun` row on completion. This also removes the
reason `container-app-api.bicep` currently pins `minReplicas: 1` in prod
(see that module's comment) — once run execution isn't riding on the API
replica, the API can scale to zero like everything else.

## 6. Cost and compliance are not afterthoughts here

Flipping `deployBatchCompute = true` is the one infra change in this stack
that turns on real per-run spend the moment a node allocates (see
`docs/cost-model.md`, "Profile B"). Wiring a real upload path is the moment
`docs/compliance-checklist.md`'s go-live checklist stops being theoretical.
Do both deliberately, with those two documents open, not as a quick flag
flip to see if the pipeline runs.
