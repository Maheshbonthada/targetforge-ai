import type { RunCreate, RunOut } from "./types";

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";

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
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
        // Real auth (Entra ID) plugs in here once a login flow exists — see
        // infra/bicep/modules/container-app-api.bicep's ENTRA_* env vars.
        // The local API accepts requests without a token while
        // AUTH_DISABLED_FOR_LOCAL_DEV=true.
      },
    });
  } catch {
    throw new ApiError(0, "Could not reach the TargetForge API. Is it running?");
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
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
};
