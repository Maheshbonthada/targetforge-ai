import type { RunCreate, RunOut } from "./types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

// A run submission itself can legitimately take minutes (see
// api/routers/runs.py's RUN_TIMEOUT_SECONDS), but that work happens in a
// background task *after* this request returns 202 — so every request this
// client makes is a fast metadata read/write, and a generous-but-finite
// timeout here exists purely to stop a wedged connection (API up, DB or
// network stalled) from spinning the UI's loading state forever with no way
// for the person watching it to know something's actually wrong.
const REQUEST_TIMEOUT_MS = 15_000;

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
        // Real auth (Entra ID) plugs in here once a login flow exists — see
        // infra/bicep/modules/container-app-api.bicep's ENTRA_* env vars.
        // Every route now requires it server-side (api/routers/runs.py) except
        // when AUTH_DISABLED_FOR_LOCAL_DEV=true, which is what local dev runs
        // against — this frontend cannot successfully call a production
        // deployment until a real login flow exists here. See docs/runbook.md.
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "The TargetForge API took too long to respond.");
    }
    throw new ApiError(0, "Could not reach the TargetForge API. Is it running?");
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (response.status === 401) {
      throw new ApiError(401, "Not signed in (or your session expired). Sign-in isn't wired up in this build yet.");
    }
    if (response.status === 429) {
      throw new ApiError(429, body?.detail ?? "Too many runs started recently — please wait a bit and try again.");
    }
    throw new ApiError(response.status, body?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  listRuns: () => request<RunOut[]>("/api/v1/runs"),
  createRun: (payload: RunCreate) =>
    request<RunOut>("/api/v1/runs", { method: "POST", body: JSON.stringify(payload) }),
  getRun: (runId: string) => request<RunOut>(`/api/v1/runs/${runId}`),
  deleteRun: (runId: string) => request<void>(`/api/v1/runs/${runId}`, { method: "DELETE" }),
};
