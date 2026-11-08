import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import RotateCcw from "lucide-react/icons/rotate-ccw";
import { useEffect, useRef, useState } from "react";

import { EngineApiError } from "./api/engine";
import {
  createAnalysis,
  createProcessing,
  getLatestAnalysis,
  retryAnalysis,
} from "./api/jobs";
import { listProjects, type ProjectSummary } from "./api/projects";
import { getProjectArtifacts, getRuntime } from "./api/processing";
import { AppShell } from "./components/AppShell";
import { ImportProject } from "./components/ImportProject";
import { ProjectSidebar } from "./components/ProjectSidebar";
import { ProjectWorkspace, type GenerationOperation } from "./components/ProjectWorkspace";
import { focusUnsavedContent } from "./components/workspaceNavigation";
import { UnsavedProjectDialog } from "./components/UnsavedProjectDialog";

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "canceled"]);
const RESTART_ERRORS = new Set([
  "cloud_task_not_found", "cloud_submit_unknown", "cloud_transcription_failed",
  "cloud_upstream", "cloud_consent_required",
]);

export function App() {
  const queryClient = useQueryClient();
  const [requestedProjectId, setRequestedProjectId] = useState<string | null>(null);
  const [workspaceDirty,setWorkspaceDirty]=useState(false);
  const [pendingSwitch,setPendingSwitch]=useState<{id:string;verify:boolean}|null>(null);
  const currentWorkspace=useRef<{projectId:string|null;dirty:boolean}>({projectId:null,dirty:false});
  function selectProject(id:string,verify=false):boolean {
    if(currentWorkspace.current.dirty){setPendingSwitch({id,verify});return false;}
    setWorkspaceDirty(false);setRequestedProjectId(id);return true;
  }
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const projects = projectsQuery.data ?? [];
  const selectedProjectId = resolveSelectedProjectId(
    projects,
    requestedProjectId,
  );
  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) ?? null;
  currentWorkspace.current={projectId:selectedProjectId,dirty:workspaceDirty};
  const runtimeQuery = useQuery({
    queryKey: ["runtime"],
    queryFn: getRuntime,
    refetchInterval: 15_000,
  });

  const latestAnalysisQuery = useQuery({
    queryKey: ["latest-analysis", selectedProjectId],
    queryFn: () => getLatestAnalysis(selectedProjectId as string),
    enabled: selectedProjectId !== null,
    refetchInterval: (query) => {
      const job = query.state.data;
      return job && !TERMINAL_STATUSES.has(job.status) ? 750 : false;
    },
  });
  const currentJob = latestAnalysisQuery.data?.projectId === selectedProjectId
    ? latestAnalysisQuery.data
    : null;
  const terminalKey =
    currentJob && TERMINAL_STATUSES.has(currentJob.status)
      ? `${currentJob.id}:${currentJob.status}`
      : null;
  const artifactsQuery = useQuery({
    queryKey: ["artifacts", selectedProjectId],
    queryFn: () => getProjectArtifacts(selectedProjectId as string),
    enabled: selectedProjectId !== null,
  });

  const startAnalysis = useMutation({
    mutationFn: createAnalysis,
    onSuccess: (job) => {
      queryClient.setQueryData(["latest-analysis", job.projectId], job);
    },
  });
  const processingAction = useMutation({
    mutationFn: ({ projectId, operation, allowCloudUpload, retryId }: {
      projectId: string;
      operation: GenerationOperation;
      allowCloudUpload?: boolean;
      retryId?: string;
    }) => {
      if (retryId) return retryAnalysis(retryId);
      return allowCloudUpload ? createProcessing(projectId, operation, true) : createProcessing(projectId, operation);
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["latest-analysis", job.projectId], job);
    },
  });

  useEffect(() => {
    if (!terminalKey) return;
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    void queryClient.invalidateQueries({ queryKey: ["artifacts", selectedProjectId] });
    void queryClient.invalidateQueries({ queryKey: ["runtime"] });
  }, [queryClient, selectedProjectId, terminalKey]);

  async function handleImported(project: ProjectSummary) {
    // Keep the restored workspace mounted until its unsaved-content choice is made.
    const activeProjectId=currentWorkspace.current.projectId;
    if (activeProjectId !== null) setRequestedProjectId(activeProjectId);
    queryClient.setQueryData<ProjectSummary[]>(["projects"], (existing = []) => [
      project,
      ...existing.filter((item) => item.id !== project.id),
    ]);
    if(selectProject(project.id,true))await startAnalysis.mutateAsync(project.id);
  }

  function processAudio(operation: GenerationOperation, allowCloudUpload?: boolean) {
    if (!selectedProjectId) return;
    const retryId = currentJob?.operation === operation
      && (currentJob.status === "failed" || currentJob.status === "canceled")
      && !RESTART_ERRORS.has(currentJob.errorCode ?? "") ? currentJob.id : undefined;
    processingAction.mutate({ projectId: selectedProjectId, operation, allowCloudUpload, retryId });
  }

  const currentProcessing = processingAction.variables?.projectId === selectedProjectId;
  const currentImport = startAnalysis.variables === selectedProjectId;
  const actionError = safeErrorMessage(
    (currentProcessing ? processingAction.error : null)
      ?? (currentImport ? startAnalysis.error : null)
      ?? latestAnalysisQuery.error,
  );
  const projectError = safeErrorMessage(projectsQuery.error);

  return (
    <AppShell
      sidebar={
        <ProjectSidebar
          projects={projects}
          selectedId={selectedProjectId}
          onSelect={id=>{if(id!==selectedProjectId)selectProject(id);}}
          onImported={handleImported}
          loading={projectsQuery.isLoading}
        />
      }
    >
      {projectsQuery.isLoading ? <WorkspaceSkeleton /> : null}
      {!projectsQuery.isLoading && projectError ? (
        <WorkspaceError
          message={projectError}
          onRetry={() => void projectsQuery.refetch()}
        />
      ) : null}
      {!projectsQuery.isLoading && !projectError && !selectedProject ? (
        <EmptyWorkspace onImported={handleImported} />
      ) : null}
      {!projectsQuery.isLoading && !projectError && selectedProject ? (
        <ProjectWorkspace
          key={selectedProject.id}
          project={selectedProject}
          job={currentJob}
          loadingJob={latestAnalysisQuery.isLoading || (currentImport && startAnalysis.isPending)}
          actionPending={currentProcessing && processingAction.isPending}
          actionError={actionError}
          onProcess={processAudio}
          runtime={runtimeQuery.data ?? null}
          runtimeLoading={runtimeQuery.isLoading}
          runtimeError={safeErrorMessage(runtimeQuery.error)}
          artifacts={artifactsQuery.data ?? []}
          artifactsLoading={artifactsQuery.isLoading || (artifactsQuery.isFetching && currentJob?.status === "succeeded")}
          artifactsError={safeErrorMessage(artifactsQuery.error)}
          onRefreshArtifacts={() => {void artifactsQuery.refetch();void latestAnalysisQuery.refetch();}}
          onDirtyChange={setWorkspaceDirty}
        />
      ) : null}
      {pendingSwitch?<UnsavedProjectDialog onContinue={()=>setPendingSwitch(null)} onSave={()=>{setPendingSwitch(null);requestAnimationFrame(focusUnsavedContent);}} onDiscard={()=>{setWorkspaceDirty(false);setRequestedProjectId(pendingSwitch.id);if(pendingSwitch.verify)startAnalysis.mutate(pendingSwitch.id);setPendingSwitch(null);}}/>:null}
    </AppShell>
  );
}

function EmptyWorkspace({
  onImported,
}: {
  onImported: (project: ProjectSummary) => void | Promise<void>;
}) {
  return (
    <section className="empty-workspace" aria-labelledby="empty-title">
      <div className="import-surface">
        <img className="import-still" src="/brand/coverlab-studio-still-v1.png" width="2172" height="724" alt="" aria-hidden="true" />
        <h2 id="empty-title">选择本机音频</h2>
        <ImportProject onImported={onImported} />
        <span className="import-formats">MP3 · WAV · FLAC · M4A 等音频文件</span>
      </div>
    </section>
  );
}

function WorkspaceSkeleton() {
  return (
    <div className="workspace-skeleton" aria-label="正在读取本机工作区" role="status">
      <span /><span /><span /><span />
    </div>
  );
}

function WorkspaceError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <section className="workspace-message" role="alert">
      <h2>暂时无法读取本机项目</h2>
      <p>{message}</p>
      <button className="button button--secondary" type="button" onClick={onRetry}>
        <RotateCcw aria-hidden="true" />
        重新读取
      </button>
    </section>
  );
}

function resolveSelectedProjectId(
  projects: ProjectSummary[],
  requestedId: string | null,
): string | null {
  if (requestedId && projects.some((project) => project.id === requestedId)) {
    return requestedId;
  }
  return projects[0]?.id ?? null;
}

function safeErrorMessage(error: unknown): string | null {
  if (!error) return null;
  return error instanceof EngineApiError
    ? error.message
    : "本机引擎暂时不可用，请稍后重试";
}
