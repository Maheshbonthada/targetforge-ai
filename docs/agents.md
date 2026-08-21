# Agent roster — full contracts

Each agent is a class implementing a narrow `Protocol` (see
`apps/api/src/targetforge/agents/base.py`), takes a typed input, returns a
typed output, and — for reasoning-stage agents — logs its reasoning trace to
the run's audit record. No agent calls another agent directly; all handoffs
go through the Supervisor (`orchestration/supervisor.py`), which is what
makes runs resumable and inspectable.

---

## 1. QC Agent
**Input:** raw FASTQ file(s) per sample.
**Tool:** FastQC (containerized), MultiQC for cohort-level rollup.
**Output:** `QcResult` — per-sample pass/fail, flagged issues (adapter
contamination, low Q30, duplication rate), plain-language summary.
**LLM use:** summarization of MultiQC output into a one-paragraph, non-expert
readable flag list. The pass/fail threshold itself is a deterministic
numeric rule, not an LLM judgment.

## 2. Alignment Orchestration Agent
**Input:** QC-passed FASTQ files, reference genome build (e.g. GRCh38).
**Tool:** BWA-MEM2, submitted as an Azure Batch job per sample.
**Output:** `AlignmentResult` — BAM file location, alignment rate, mean
coverage, job status.
**LLM use:** none. Retries and failure classification are deterministic
(exit code / Batch task state).

## 3. Variant Calling Agent
**Input:** aligned BAMs for the cohort.
**Tool:** GATK HaplotypeCaller (or DeepVariant) + joint genotyping (GenotypeGVCFs).
**Output:** `VariantCallResult` — cohort VCF location, variant count, Ti/Tv
ratio (a standard QC sanity check for a variant set).
**LLM use:** none.

## 4. Variant Annotation Agent
**Input:** cohort VCF.
**Tool:** Ensembl VEP, cross-referenced against ClinVar, gnomAD, dbSNP.
**Output:** `AnnotatedVariantSet` — each variant annotated with consequence
(missense/nonsense/etc.), gnomAD population frequency, ClinVar significance
if known, affected gene/transcript.
**LLM use:** summarization only — "N variants affect M genes, of which X are
rare (gnomAD AF < 0.01) and predicted high-impact."

## 5. Cohort/Association Agent
**Input:** annotated variant set + sample phenotype/group labels (case/control,
tumor/normal).
**Tool:** deterministic association statistics (Fisher's exact / logistic
regression per variant, matching PLINK's standard approach), not an LLM.
**Output:** `AssociationResult` — ranked variant/gene list by association
strength (odds ratio, p-value, multiple-testing-corrected q-value).
**LLM use:** none.

## 6. Pathway Enrichment Agent
**Input:** gene list from agent 5.
**Tool:** hypergeometric enrichment test against KEGG/Reactome/GO gene sets
(deterministic statistics).
**Output:** `PathwayEnrichmentResult` — enriched pathways ranked by adjusted
p-value, with member genes.
**LLM use:** none.

---

## 7. Literature Mining Agent
**Input:** candidate gene list (from agents 5/6).
**Tool:** PubMed E-utilities (esearch/efetch, free/keyless), bioRxiv API.
**Output:** `LiteratureEvidence` per gene — relevant paper count, a short
LLM-generated summary of what's known (mechanism, disease association,
existing drug programs if mentioned), each claim tagged with the PMID it
came from.
**LLM use:** genuinely agentic — the agent decides which returned abstracts
are relevant enough to summarize, and iterates its PubMed query if the first
search returns too little. Every generated claim carries a citation; the
report generator (12) refuses to include an uncited claim.

## 8. Druggability Assessment Agent
**Input:** candidate gene list.
**Tool:** DGIdb (drug-gene interaction database, free API), ChEMBL, Open
Targets tractability scores.
**Output:** `DruggabilityAssessment` per gene — known ligand/drug class if
any, protein family druggability tier (well-precedented / emerging modality /
undruggable-by-small-molecule), structural tractability notes.
**LLM use:** synthesizes the structured API responses into a plain-language
tractability verdict per gene; the underlying scores themselves are the
database's, not invented.

## 9. Competitive Intelligence Agent
**Input:** candidate gene list.
**Tool:** ClinicalTrials.gov API (free/keyless) for trials targeting this
gene/pathway; a lightweight patent-landscape note (heuristic keyword search
over public patent search, flagged as directional, not legal freedom-to-
operate advice).
**Output:** `CompetitiveLandscape` per gene — existing trials/approved drugs
targeting it, development phase, sponsor. This is what stops a team from
spending a quarter on a target someone already has in Phase 3.
**LLM use:** summarizes/deduplicates trial records into "this target already
has N active programs, most advanced is Phase 2" — a judgment call worth an
LLM, grounded entirely in retrieved records.

## 10. Target Prioritization Agent
**Input:** outputs of agents 5 (genetic association), 6 (pathway), 7
(literature), 8 (druggability), 9 (competitive landscape).
**Output:** `TargetRanking` — every candidate gene scored and ranked, with an
explicit, itemized rationale (not just a number) citing which inputs drove
the score up or down.
**LLM use:** genuinely agentic multi-criteria reasoning — this is the agent
whose whole job is judgment under multiple, sometimes conflicting signals
(strong genetics but already heavily competed vs. novel but weak genetic
signal). Its reasoning trace is stored in full so a scientist can see *why*,
not just the final rank — this is the one place in the pipeline where "trust
the AI's ranking without an explanation" would be a real credibility problem.

## 11. Experiment Design Agent
**Input:** top-N ranked targets from agent 10.
**Output:** `ExperimentProposal` per target — suggested validation approach
(CRISPR knockout/knockdown screen, in vitro assay class, relevant cell
line/model organism precedent from literature), explicitly framed as a
starting proposal for a scientist to refine, not a lab protocol to execute
unreviewed.
**LLM use:** yes — grounded in the literature evidence already gathered by
agent 7, not free-invented.

## 12. Report Generation Agent
**Input:** the full run's outputs from every prior agent.
**Output:** a structured Markdown/PDF report: executive summary, methodology
(which tools/versions/reference build were used — required for
reproducibility), ranked target list with rationale, per-target evidence
appendix, all citations resolved to real PMIDs/trial IDs.
**LLM use:** yes — but every factual claim it includes must trace back to a
prior agent's structured output; it is not permitted to introduce new claims
not present upstream (enforced by construction: the prompt only supplies
prior agents' structured JSON, not open-ended "write about this gene").

---

## 13. Supervisor Agent
Not a pipeline stage — orchestrates all of the above. Responsibilities:
- Persists a `PipelineRun` with per-stage status (pending/running/succeeded/
  failed/skipped) so a run is inspectable mid-flight, not a black box.
- Retries transient failures (a Batch job timeout, an external API rate
  limit) with backoff; surfaces permanent failures for human triage rather
  than silently continuing with partial data.
- Supports partial runs: a literature-only team can run agents 7–12 directly
  against a manually-provided gene list, skipping 1–6 entirely.
- Does **not** make scientific judgments — if it did, it would be a
  fourteenth reasoning agent wearing an orchestration hat, which is exactly
  the kind of scope-blur this document exists to prevent.
