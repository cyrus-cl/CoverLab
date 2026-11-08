import LoaderCircle from "lucide-react/icons/loader-circle";
import { useCallback, useState } from "react";
import type { AnalysisJob } from "../api/jobs";
import type { ProjectSummary } from "../api/projects";
import { isAudioArtifact, latestByKind, type ProjectArtifact, type RuntimeStatus } from "../api/processing";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { ProjectArtifacts } from "./ProjectArtifacts";
import { runtimeErrorLabel } from "./processingLabels";

export type GenerationOperation = "singing" | "lyrics" | "separate";

type ProjectWorkspaceProps = {
  project: ProjectSummary;
  job: AnalysisJob | null;
  loadingJob: boolean;
  actionPending: boolean;
  actionError: string | null;
  onProcess: (operation: GenerationOperation, allowCloudUpload?: boolean) => void;
  runtime: RuntimeStatus | null;
  runtimeLoading: boolean;
  runtimeError: string | null;
  artifacts: ProjectArtifact[];
  artifactsLoading: boolean;
  artifactsError: string | null;
  onRefreshArtifacts: () => void;
  onDirtyChange?: (dirty: boolean) => void;
};

export function ProjectWorkspace({
  project, job, loadingJob, actionPending, actionError, onProcess, runtime, runtimeLoading,
  runtimeError, artifacts, artifactsLoading, artifactsError, onRefreshArtifacts, onDirtyChange,
}: ProjectWorkspaceProps) {
  const [captureActive, setCaptureActive] = useState(false);
  const [workspaceDirty, setWorkspaceDirty] = useState(false);
  const [requestedAudioId, setRequestedAudioId] = useState<string | null>(null);
  const [cloudOperation, setCloudOperation] = useState<GenerationOperation | null>(null);
  const latest = latestByKind(artifacts);
  const tracks = latest.filter(isAudioArtifact);
  const backing = tracks.find(item => item.kind === "instrumental");
  const vocals = tracks.find(item => item.kind === "vocals");
  const lyrics = latest.find(item => item.kind === "lyrics");
  const selected = tracks.find(item => item.id === requestedAudioId)
    ?? backing ?? tracks.find(item => item.kind === "normalized_audio") ?? tracks[0];
  const selectedAudioId = selected?.id ?? null;
  const jobActive = Boolean(job && !["succeeded", "failed", "canceled"].includes(job.status));
  const generationBusy = actionPending || (jobActive && job?.operation !== "fingerprint");
  const processingDisabled = actionPending || loadingJob || jobActive || captureActive;
  const cloudLyrics = runtime?.lyrics?.cloud === true;
  const draftModel = lyrics?.metadata.model_id;
  const canUpgradeLyrics = Boolean(backing && vocals && cloudLyrics && lyrics?.metadata.machine_draft === true
    && ["faster-whisper-tiny", "faster-whisper-small", "qwen3-asr-0.6b-hf"].includes(String(draftModel)));
  const needsGeneration = !backing || !lyrics || canUpgradeLyrics;
  // Keep manually imported or corrected lyrics when an older project only needs separation.
  const operation: GenerationOperation = backing && vocals ? "lyrics" : lyrics && !canUpgradeLyrics ? "separate" : "singing";
  const generationError = runtimeError ?? getAvailabilityError(runtime, operation);
  const failed = job?.status === "failed" && !["lyrics_edit", "recording"].includes(job.operation);
  const jobError = failed ? (job.errorMessage || "生成失败，请重试。") : null;
  const error = actionError ?? jobError ?? (needsGeneration ? generationError : null);

  function requestGeneration() {
    if (cloudLyrics && operation !== "separate") setCloudOperation(operation);
    else onProcess(operation);
  }

  function selectAudio(id: string) {
    if (!captureActive && !jobActive) setRequestedAudioId(id);
  }

  const handleDirtyChange = useCallback((dirty: boolean) => {
    setWorkspaceDirty(dirty);
    onDirtyChange?.(dirty);
  }, [onDirtyChange]);

  return (
    <div className="project-workspace project-workspace--tool">
      <header className="project-header">
        <div className="project-header__identity">
          <span className="file-emblem"><img src="/brand/dusk.png" width="54" height="54" alt="" /></span>
          <h2>{project.sourcePath.split(/[\\/]/).pop() || project.name}</h2>
        </div>
        {!cloudOperation && (generationBusy || (needsGeneration && !artifactsLoading)) ? (
          <button
            type="button"
            className="button button--primary"
            disabled={processingDisabled || runtimeLoading || !runtime || Boolean(generationError || artifactsError) || workspaceDirty}
            onClick={requestGeneration}
          >
            {generationBusy ? <><LoaderCircle className="generation-spinner" aria-hidden="true" />正在生成…</> : "生成伴奏和歌词"}
          </button>
        ) : null}
      </header>
      {generationBusy ? <span className="sr-only" role="status">正在生成伴奏和歌词</span> : null}
      {cloudOperation ? (
        <div className="reprocess-confirmation" role="group" aria-label="云端歌词确认">
          <p>分离后的人声将发送至 CoverLab 云端服务及阿里云百炼，用于识别歌词。{lyrics ? "新歌词将替换当前草稿。" : ""}</p>
          <div>
            <button type="button" className="button button--primary" disabled={processingDisabled || workspaceDirty} onClick={() => {
              setCloudOperation(null);
              onProcess(cloudOperation, true);
            }}>上传分离人声以识别歌词</button>
            <button type="button" className="button button--quiet" onClick={() => setCloudOperation(null)}>取消</button>
          </div>
        </div>
      ) : null}
      {error ? <p className="inline-error" role="alert">{error}</p> : null}

      <ProjectArtifacts
        artifacts={artifacts}
        loading={artifactsLoading}
        error={artifactsError}
        onRefresh={onRefreshArtifacts}
        selectedAudioId={selectedAudioId}
        onSelectedAudioChange={selectAudio}
        disabled={captureActive || jobActive}
      />
      <AnalysisWorkspace
        key={project.id}
        projectId={project.id}
        title={project.name.replace(/\.[^.]+$/, "")}
        artifacts={artifacts}
        disabled={processingDisabled}
        onSaved={onRefreshArtifacts}
        onDirtyChange={handleDirtyChange}
        onRecordingChange={setCaptureActive}
      />
    </div>
  );
}

function getAvailabilityError(runtime: RuntimeStatus | null, operation: GenerationOperation): string | null {
  if (!runtime) return null;
  if (!runtime.media.available) return runtimeErrorLabel(runtime.media.errorCode);
  if (operation !== "lyrics" && !runtime.separation.available) return "暂时无法分离伴奏，请重启应用后重试。";
  if (operation !== "separate" && !runtime.lyrics?.available) return "歌词服务暂不可用，请稍后重试。";
  return null;
}
