import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { Markdown } from "../components/Markdown";
import { RunStatusPill, StageStatusPill } from "../components/StatusPill";
import { PIPELINE_STAGES, type RunOut, type StageStatus } from "../api/types";

const POLL_INTERVAL_MS = 3000;

export function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<RunOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    async function poll() {
      try {
        const latest = await api.getRun(runId!);
        if (cancelled) return;
        setRun(latest);
        setError(null);
        // Only keep polling while the run is genuinely in flight — a
        // background task on the API side (see api/routers/runs.py) is
        // still doing real work here, so this isn't just decorative.
        if (latest.status === "running") {
          timerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load run");
      }
    }

    poll();
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [runId]);

  if (error) {
    return (
      <>
        <div className="breadcrumb">
          <Link to="/">← All runs</Link>
        </div>
        <div className="error-banner">{error}</div>
      </>
    );
  }

  if (!run) {
    return <p className="loading-line">Loading run…</p>;
  }

  const report = run.report_json;

  return (
    <>
      <div className="breadcrumb">
        <Link to="/">← All runs</Link>
      </div>

      <div className="page-header">
        <div>
          <h1>{run.candidate_genes.join(", ")}</h1>
          <p className="subtitle">
            Started {new Date(run.created_at).toLocaleString()}
            {run.completed_at && ` · completed ${new Date(run.completed_at).toLocaleString()}`}
          </p>
        </div>
        <RunStatusPill status={run.status} />
      </div>

      <section>
        <h2>Pipeline stages</h2>
        <div className="stage-grid">
          {PIPELINE_STAGES.map((stage) => (
            <div key={stage} className="stage-cell">
              <span className="stage-name">{stage.replace(/_/g, " ")}</span>
              <StageStatusPill status={(run.stage_status_json[stage] as StageStatus) ?? "pending"} />
            </div>
          ))}
        </div>
      </section>

      {run.status === "failed" && (
        <div className="error-banner">
          <strong>Run failed:</strong> {run.error_message ?? "Unknown error."}
        </div>
      )}

      {run.status === "running" && (
        <p className="loading-line">
          Pipeline running — this page updates automatically every few seconds…
        </p>
      )}

      {report && (
        <>
          <section>
            <h2>Ranked targets</h2>
            <div className="table-scroll">
              <table className="doc-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Gene</th>
                    <th>Composite</th>
                    <th>Genetic evidence</th>
                    <th>Druggability</th>
                    <th>Novelty</th>
                  </tr>
                </thead>
                <tbody>
                  {report.ranked_targets.map((t) => (
                    <tr key={t.gene_symbol}>
                      <td className="tabular">{t.rank}</td>
                      <td>
                        <strong>{t.gene_symbol}</strong>
                      </td>
                      <td className="tabular">{t.composite_score.toFixed(0)}</td>
                      <td className="tabular">{t.genetic_evidence_score.toFixed(0)}</td>
                      <td className="tabular">{t.druggability_score.toFixed(0)}</td>
                      <td className="tabular">{t.novelty_score.toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section>
            <h2>Full report</h2>
            <div className="report-card">
              <Markdown text={report.report_markdown} />
            </div>
          </section>
        </>
      )}
    </>
  );
}
