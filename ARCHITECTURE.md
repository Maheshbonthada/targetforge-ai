# Architecture

## What this system does

A biotech/pharma R&D team has a cohort of sequenced samples (e.g., tumor vs.
normal, cases vs. controls) and a research question: *which genes are
plausible, tractable drug targets given this genetic evidence?* Answering that
today means stitching together a bioinformatics pipeline (QC → alignment →
variant calling → annotation) by hand, then a human spending weeks reading
literature and cross-referencing druggability databases to turn a list of
variants into a ranked, defensible target hypothesis. TargetForge automates
that full path: raw sequencing data in, a ranked, cited target-discovery
report out.

## Two kinds of agents, on purpose

This system has **13 agents**, split into two categories with fundamentally
different design constraints — conflating them is the single most common
mistake in bioinformatics-plus-AI systems, so it's worth stating up front.

**Compute-stage agents (1–6)** wrap deterministic, industry-standard
bioinformatics tools (BWA-MEM2, GATK/DeepVariant, VEP). Their job is
orchestration and quality judgment *about* a deterministic tool's output, not
open-ended reasoning — a variant caller's output is not something an LLM
should be asked to reproduce or second-guess at the base-call level. Where
these agents use an LLM at all, it's to summarize/triage tool output in
plain language, never to replace the tool.

**Reasoning-stage agents (7–12)** are genuinely LLM-driven: reading literature,
weighing evidence, generating and ranking hypotheses, writing prose. This is
exactly the kind of work LLMs are suited for, and where "agentic" behavior
(the agent deciding what to search next, what evidence to weigh) is
appropriate rather than a liability.

**The Supervisor (13)** coordinates handoffs between stages and persists run
state — it does not itself make scientific judgments.

This mirrors the design decision made in TrialReady (the previous project):
LLM agents for perception/reasoning over unstructured or judgment-laden input,
deterministic tools for anything with a hard technical or evidentiary
ground truth.

## The pipeline

```
Raw FASTQ / VCF upload
        |
        v
[1] QC Agent ─────────────────── runs FastQC/MultiQC, flags samples needing re-sequencing
        |
        v
[2] Alignment Orchestration Agent ── submits BWA-MEM2 jobs to compute backend, tracks completion
        |
        v
[3] Variant Calling Agent ────── orchestrates GATK/DeepVariant, joint genotyping across cohort
        |
        v
[4] Variant Annotation Agent ─── runs VEP, cross-refs ClinVar/gnomAD/dbSNP
        |
        v
[5] Cohort/Association Agent ─── case-vs-control association analysis, candidate variant list
        |
        v
[6] Pathway Enrichment Agent ─── maps candidate genes to KEGG/Reactome/GO pathways
        |
        v
   ┌────┴─────────────────────────────────────┐
   v                                            v
[7] Literature Mining Agent          [8] Druggability Assessment Agent
   (PubMed/bioRxiv evidence per gene)   (DGIdb/ChEMBL/Open Targets tractability)
   |                                            |
   └──────────────────┬─────────────────────────┘
                       v
          [9] Competitive Intelligence Agent
              (existing drugs/trials against this target — avoid duplicating a program)
                       |
                       v
          [10] Target Prioritization Agent
              (combines genetic strength + druggability + novelty + freedom-to-operate into a ranked list)
                       |
                       v
          [11] Experiment Design Agent
              (suggests validation assays per top-ranked target)
                       |
                       v
          [12] Report Generation Agent
              (synthesizes everything into a structured, cited report)

[13] Supervisor Agent orchestrates every handoff above and persists run state
     (see orchestration/supervisor.py) — it is invoked at every arrow, not shown
     as a pipeline stage of its own.
```

## Agent roster (full contract: inputs, outputs, tool calls, LLM usage)

See [docs/agents.md](docs/agents.md) for the complete per-agent specification.
Summary:

| # | Agent | LLM-driven? | Primary tool/data dependency |
|---|---|---|---|
| 1 | QC | Summarization only | FastQC / MultiQC |
| 2 | Alignment Orchestration | No | BWA-MEM2, Azure Batch |
| 3 | Variant Calling | No | GATK / DeepVariant |
| 4 | Variant Annotation | Summarization only | VEP, ClinVar, gnomAD |
| 5 | Cohort/Association | No (statistics) | PLINK-style association test |
| 6 | Pathway Enrichment | No (statistics) | KEGG, Reactome, GO |
| 7 | Literature Mining | **Yes** | PubMed E-utilities, bioRxiv API |
| 8 | Druggability Assessment | **Yes** | DGIdb, ChEMBL, Open Targets |
| 9 | Competitive Intelligence | **Yes** | ClinicalTrials.gov, patent search |
| 10 | Target Prioritization | **Yes** | Outputs of 5, 6, 7, 8, 9 |
| 11 | Experiment Design | **Yes** | Output of 10 |
| 12 | Report Generation | **Yes** | Outputs of all prior agents |
| 13 | Supervisor | No | Orchestration only |

## What's real vs. what's a documented stand-in (be honest about this)

- **Agents 7–12 (reasoning stage) are fully implemented and run against a real
  LLM** (Azure OpenAI in production, OpenAI directly in local dev — same
  provider-swap pattern as TrialReady). These are genuinely testable today.
- **Agents 1–6 (compute stage) have real orchestration code and real
  tool-integration contracts** (container definitions, Azure Batch job specs),
  but are exercised in tests and local dev against a **tiny synthetic
  reference genome and synthetic reads** (`data/reference/`), not a real human
  reference genome — downloading and aligning against GRCh38 needs
  multi-hour, multi-GB compute this environment doesn't have. The interfaces
  are production-shaped; swapping the synthetic reference for a real one and
  pointing Azure Batch at real compute is a configuration change, not a
  rewrite — see `docs/scaling-to-real-genomes.md`.
- **External evidence APIs** (PubMed, DGIdb, ClinicalTrials.gov, etc.) are
  called for real where the API is free and keyless; a few (ChEMBL, Open
  Targets GraphQL) are stubbed behind the same fake-implementation pattern
  used throughout, pending API key provisioning.

## Why a Supervisor agent, not a hardcoded pipeline script

A plain sequential script would be simpler for the happy path. The Supervisor
exists because real pipeline runs fail partway through (a sample fails QC, an
external API times out, a Batch job needs a retry) and because different runs
need different agent subsets (a team with only literature — no sequencing data
— should be able to run agents 7–12 alone). The Supervisor persists per-stage
state in Postgres so a run can be inspected, resumed, or partially re-run
without starting over — see `orchestration/supervisor.py`.
