# Compliance checklist

## Current state: candidate-gene-list and public/reference data only

This codebase's fully-real, fully-tested entry point
(`run_from_candidate_genes` — see `orchestration/supervisor.py`) takes a list
of gene symbols, not raw sequencing reads from a person. Every external call
it makes (PubMed, DGIdb, ClinicalTrials.gov, Ensembl VEP) queries public
reference data, not subject-identifiable information. **No individual's
sequencing data has touched this system.**

The complete pipeline (`run_full_from_fastq`) accepts raw FASTQ files — that
entry point is where every item below actually starts to matter, and it is
not yet wired to a real upload path or real alignment/variant-calling compute
(see `docs/scaling-to-real-genomes.md`). Treat this document as the checklist
to complete *before* that entry point takes a real person's sequencing data,
not a list of things already broken.

Do not point `run_full_from_fastq` at a real research participant's or
patient's raw sequencing data until every item below is checked.

## Why genomic data doesn't get the usual "de-identify it" answer

The standard HIPAA move for other health data — strip the 18 identifiers,
call it de-identified, apply lighter controls — does not fully work for raw
sequencing data. A person's genome is itself a unique identifier: with a
reference sample (a relative's consumer DNA-test upload, a forensic database,
a future re-sequencing), a "de-identified" FASTQ/VCF can in principle be
re-linked to the person it came from. Treat any file containing an
individual's actual sequence data as identifiable indefinitely, regardless of
what metadata was stripped alongside it, and design access controls
accordingly — not as a one-time de-identification step that then relaxes
handling.

## Go-live checklist (before `run_full_from_fastq` touches real subject data)

- [x] **API-level authentication and per-caller isolation** — every route now
      requires `Depends(get_current_user)` and every read/delete is scoped to
      `triggered_by == caller`; a run belonging to a different caller returns
      404, not the data (see `docs/production-readiness-audit.md`, findings
      #1/#18). This was a real gap until that audit: `core/security.py`
      existed from the start but was never wired into a router, so any
      caller could read or delete any other caller's candidate gene lists —
      exactly the kind of pre-publication data this section is about. Closed
      at the API layer; the Storage-file-level item below (genomic files
      specifically) remains open.
- [ ] **IRB approval and informed consent** — any pipeline run against a real
      research participant's or patient's sequencing data needs an
      Institutional Review Board-approved protocol and documented informed
      consent covering *this specific use* (target-discovery research, not
      clinical diagnosis — see the scope note below). Not a technical control;
      gates everything else here, same role the BAA plays for TrialReady's
      PHI checklist.
- [ ] **GINA and state genetic-privacy law review** — the Genetic Information
      Nondiscrimination Act (2008) restricts how genetic information can be
      used in employment and health-insurance decisions; several states
      (e.g., California's Genetic Information Privacy Act) add stricter
      consent and deletion-request requirements on top. Confirm the
      consent language and downstream data use satisfy both before ingesting
      real samples.
- [ ] **This is a research-discovery tool, not a clinical or diagnostic
      one — keep it that way, or budget for a different regulatory track.**
      `ReportGenerationAgent`'s output (`schemas/pipeline.py:GenomicsReport`)
      ranks candidate drug targets for an R&D team's own follow-up
      experiments; nothing in this pipeline is validated, or intended, to
      inform a clinical decision about an identifiable patient. If that scope
      ever changes, it likely triggers FDA regulation as a clinical decision
      support / diagnostic tool (a different, much heavier compliance
      program than anything below) — a scope decision to make deliberately,
      not drift into.
- [ ] **Executed Business Associate Agreement (BAA) with Microsoft** —
      required if any covered entity's PHI-adjacent genomic data flows
      through this stack. Azure OpenAI, Storage, and Postgres Flexible
      Server are HIPAA-eligible services under Microsoft's BAA.
- [ ] **Network isolation** — flip `publicNetworkAccess` to `Disabled` on
      Azure OpenAI (`infra/bicep/modules/openai.bicep`) and the Storage
      account (`storage.bicep`), add private endpoints, and VNet-integrate
      the Container Apps environment, Postgres Flexible Server, and (if
      deployed) the Batch pool (`batch.bicep`) — all currently
      `publicNetworkAccess: 'Enabled'` as a pre-real-data simplification.
- [ ] **Postgres authentication** — move from password auth
      (`administratorPassword` in `postgres.bicep`) to Entra-ID-only
      authentication.
- [ ] **Encryption and key management for raw genomic files** — the Storage
      account (`storage.bicep`) uses Microsoft-managed keys today. Before
      real subject sequencing data, evaluate customer-managed keys (CMK) in
      Key Vault, and confirm the lifecycle policy that tiers files to
      Cool/Archive (same module) doesn't outlive the consent's stated
      retention period.
- [ ] **Access control and audit logging on genomic files specifically** —
      today, any principal with `Storage Blob Data Contributor` on the
      account (granted broadly to the API's managed identity in
      `main.bicep`) can read every blob in the `genomic-files` container.
      Before real subject data, scope access per-run or per-study (SAS
      tokens with narrow expiry, or per-container RBAC) and turn on Storage
      Analytics logging so every read of a real sample is attributable.
- [ ] **Right to deletion / consent withdrawal** — if a participant withdraws
      consent, there is currently no built-in workflow to purge their FASTQ/
      BAM/VCF from Storage, their `PipelineRun` rows from Postgres, and any
      derived report referencing their sample. Build this before it's needed
      under a live deletion request, not during one.
- [ ] **Log content review** — confirm gene symbols and PMIDs in
      `structlog`/Application Insights output (`core/logging.py`,
      `orchestration/supervisor.py`'s per-stage logging) never carry a
      sample identifier traceable back to a specific person. Today, run IDs
      are opaque UUIDs and candidate genes are just gene symbols — keep it
      that way; add a log-scrubbing test if a future feature ties a run to a
      participant identifier.
- [ ] **Data residency and cross-border transfer** — confirm the deployment
      region satisfies each research site's/sponsor's data residency
      requirements; genomic data governance (e.g., China's Human Genetic
      Resources rules, GDPR for EU participants) can be stricter than
      general health-data rules.
- [ ] **Incident response plan specific to genomic data exposure** — a leaked
      genome is a permanent, non-rotatable exposure (unlike a leaked
      password or even most PHI) for the person and their blood relatives.
      Write a response plan that accounts for that severity before real
      subject data is in scope, not after an incident.

## Already built, and why it's there even during the reference-data-only phase

The candidate-gene-list entry point never touches real subject data by
construction — that's a scope decision, not an accident, and it's what makes
this system usable for real biotech/pharma R&D work today without waiting on
the checklist above. The moment `run_full_from_fastq` is wired to a real
upload path (`docs/scaling-to-real-genomes.md`), every item above becomes
load-bearing, and several of them (IRB approval, consent language, the
Storage access-control redesign) take real calendar time to put in place —
start them before the compute side is ready, not after.
