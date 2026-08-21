import type { RunStatus, StageStatus } from "../api/types";

const RUN_STYLE: Record<RunStatus, string> = {
  running: "pill-warning",
  succeeded: "pill-success",
  failed: "pill-danger",
};

export function RunStatusPill({ status }: { status: RunStatus }) {
  return <span className={`pill ${RUN_STYLE[status]}`}>{status}</span>;
}

const STAGE_STYLE: Record<StageStatus, string> = {
  pending: "pill-neutral",
  running: "pill-warning",
  succeeded: "pill-success",
  failed: "pill-danger",
  skipped: "pill-neutral",
};

export function StageStatusPill({ status }: { status: StageStatus }) {
  return <span className={`pill ${STAGE_STYLE[status]} pill-small`}>{status}</span>;
}
