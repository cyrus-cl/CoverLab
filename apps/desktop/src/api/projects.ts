import { engineFetch } from "./engine";

export type ProjectStatus =
  | "ready"
  | "analyzing"
  | "complete"
  | "error"
  | "archived";

export type ProjectSummary = {
  id: string;
  name: string;
  sourcePath: string;
  sourceSize: number;
  sourceMtimeNs: number;
  sourceSha256: string | null;
  outputDir: string;
  status: ProjectStatus;
  lastOpenedAt: string;
  createdAt: string;
  updatedAt: string;
};

export type CreateProjectRequest = {
  sourcePath: string;
  outputDir: string;
  name?: string;
};

export function createProject(
  request: CreateProjectRequest,
): Promise<ProjectSummary> {
  return engineFetch<ProjectSummary>("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export function listProjects(): Promise<ProjectSummary[]> {
  return engineFetch<ProjectSummary[]>("/api/v1/projects");
}

export function getProject(projectId: string): Promise<ProjectSummary> {
  return engineFetch<ProjectSummary>(
    `/api/v1/projects/${encodeURIComponent(projectId)}`,
  );
}
