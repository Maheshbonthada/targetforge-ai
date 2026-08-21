// Mirrors apps/api/src/targetforge/schemas/{api.py,pipeline.py} exactly.
// Kept hand-in-sync deliberately, same call as TrialReady's frontend makes —
// not worth a codegen build step for two endpoints.

export type RunStatus = "running" | "succeeded" | "failed";

export interface RunCreate {
  candidate_genes: string[];
  top_n_for_experiments: number;
}

export type StageStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";

export const PIPELINE_STAGES = [
  "qc",
  "alignment",
  "variant_calling",
  "annotation",
  "association",
  "pathway",
  "literature",
  "druggability",
  "competitive",
  "prioritization",
  "experiment_design",
  "report",
] as const;

export type TargetScore = {
  gene_symbol: string;
  rank: number;
  composite_score: number;
  genetic_evidence_score: number;
  druggability_score: number;
  novelty_score: number;
  rationale: string;
};

export interface GenomicsReport {
  run_id: string;
  executive_summary: string;
  methodology: string;
  ranked_targets: TargetScore[];
  report_markdown: string;
  generated_at: string;
}

export interface RunOut {
  id: string;
  candidate_genes: string[];
  status: RunStatus;
  stage_status_json: Record<string, StageStatus>;
  report_json: GenomicsReport | null;
  error_message: string | null;
  triggered_by: string;
  created_at: string;
  completed_at: string | null;
}
