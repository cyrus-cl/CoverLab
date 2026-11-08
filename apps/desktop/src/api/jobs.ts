import { EngineApiError, engineFetch } from "./engine";

export type AnalysisStatus =
  | "created"
  | "queued"
  | "running"
  | "canceling"
  | "succeeded"
  | "failed"
  | "canceled";

export type StageStatus =
  | "pending"
  | "queued"
  | "running"
  | "retry_wait"
  | "succeeded"
  | "failed"
  | "canceled";

export type StageAttempt = {
  id: string;
  number: number;
  status:
    | "running"
    | "succeeded"
    | "failed"
    | "canceled"
    | "worker_lost"
    | "timed_out";
  processId: number | null;
};

export type AnalysisStage = {
  id: string;
  name: string;
  status: StageStatus;
  progressPercent: number;
  bytesDone: number;
  bytesTotal: number | null;
  progressUnit: "bytes" | "seconds" | "segments" | "files" | "steps";
  unitsDone: number;
  unitsTotal: number | null;
  progressPhase?: string | null;
  attempts: StageAttempt[];
};

export type AnalysisJob = {
  id: string;
  projectId: string;
  operation: "fingerprint" | "prepare" | "separate" | "analyze" | "lyrics" | "lyrics_edit" | "recording" | "singing";
  status: AnalysisStatus;
  progressPercent: number;
  errorCode: string | null;
  errorMessage: string | null;
  stage: AnalysisStage;
  stages: AnalysisStage[];
};

export function createProcessing(
  projectId: string,
  operation: "prepare" | "separate" | "analyze" | "lyrics" | "singing",
  allowCloudUpload = false,
): Promise<AnalysisJob> {
  return engineFetch<AnalysisJob>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/process`,
    { method: "POST", body: JSON.stringify({ operation, ...(allowCloudUpload && {allowCloudUpload}) }) },
  );
}

export function createAnalysis(projectId: string): Promise<AnalysisJob> {
  return engineFetch<AnalysisJob>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/analyses`,
    { method: "POST" },
  );
}

export function getAnalysis(analysisId: string): Promise<AnalysisJob> {
  return engineFetch<AnalysisJob>(
    `/api/v1/analyses/${encodeURIComponent(analysisId)}`,
  );
}

export async function getLatestAnalysis(
  projectId: string,
): Promise<AnalysisJob | null> {
  try {
    return await engineFetch<AnalysisJob>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/analyses/latest`,
    );
  } catch (error) {
    if (
      error instanceof EngineApiError &&
      error.status === 404 &&
      error.code === "analysis_not_found"
    ) {
      return null;
    }
    throw error;
  }
}

export function cancelAnalysis(analysisId: string): Promise<AnalysisJob> {
  return engineFetch<AnalysisJob>(
    `/api/v1/analyses/${encodeURIComponent(analysisId)}/cancel`,
    { method: "POST" },
  );
}

export function retryAnalysis(analysisId: string): Promise<AnalysisJob> {
  return engineFetch<AnalysisJob>(
    `/api/v1/analyses/${encodeURIComponent(analysisId)}/retry`,
    { method: "POST" },
  );
}
