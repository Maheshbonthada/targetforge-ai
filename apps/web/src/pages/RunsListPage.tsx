import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { RunStatusPill } from "../components/StatusPill";
import type { RunOut } from "../api/types";

export function RunsListPage() {
  const [runs, setRuns] = useState<RunOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const navigate = useNavigate();

  function loadRuns() {
    api
      .listRuns()
      .then(setRuns)
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : "Failed to load runs"));
  }

  useEffect(loadRuns, []);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const genesRaw = String(form.get("genes") ?? "");
    const candidateGenes = genesRaw
      .split(/[,\n]/)
      .map((g) => g.trim().toUpperCase())
      .filter(Boolean);

    if (candidateGenes.length === 0) {
      setError("Enter at least one gene symbol.");
      return;
    }

    setError(null);
    setSubmitting(true);
    try {
      const run = await api.createRun({
        candidate_genes: candidateGenes,
        top_n_for_experiments: Number(form.get("top_n")) || 3,
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start a run");
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Target discovery runs</h1>
          <p className="subtitle">
            Give a candidate gene list; get back a ranked, cited drug-target report.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Hide form" : "+ New run"}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {showForm && (
        <form className="form-card" onSubmit={handleCreate}>
          <h2>New run</h2>
          <div className="field">
            <label htmlFor="genes">Candidate genes</label>
            <textarea
              id="genes"
              name="genes"
              required
              rows={3}
              placeholder="KRAS, EGFR, TP53&#10;(comma- or newline-separated gene symbols)"
            />
          </div>
          <div className="field field-narrow">
            <label htmlFor="top_n">Experiment proposals for top N targets</label>
            <input id="top_n" name="top_n" type="number" min={1} max={10} defaultValue={3} />
          </div>
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? "Starting…" : "Run pipeline"}
            </button>
          </div>
        </form>
      )}

      {runs === null && !error && <p className="loading-line">Loading runs…</p>}

      {runs && runs.length === 0 && (
        <div className="empty-state">No runs yet. Start one above.</div>
      )}

      {runs && runs.length > 0 && (
        <div className="card-list">
          {runs.map((run) => (
            <Link key={run.id} to={`/runs/${run.id}`} className="entity-card">
              <div className="title-row">
                <div className="title">{run.candidate_genes.join(", ")}</div>
                <RunStatusPill status={run.status} />
              </div>
              <div className="meta">
                {new Date(run.created_at).toLocaleString()} · triggered by {run.triggered_by}
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
