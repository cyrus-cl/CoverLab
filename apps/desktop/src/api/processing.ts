import { EngineApiError, engineFetch, getEngineConnection } from "./engine";

export type RuntimeStatus = {
  analysis?: { available: boolean };
  lyrics?: { available: boolean; cloud?: boolean; model_cached: boolean; model_id: string; revision: string; download_bytes?: number; quality_notice?: string; errorCode?: string };
  media: { available: boolean; errorCode?: string | null };
  separation: {
    available: boolean;
    errorCode?: string | null;
    model_id: string;
    revision: string;
    model_sha256: string;
    model_cached: boolean;
    device: string;
    cpu_supported: boolean;
  };
};

export type ProjectArtifact = {
  id: string;
  analysisId: string;
  kind: "normalized_audio" | "vocals" | "instrumental" | "waveform" | "beats" | "lyrics" | "lyrics_lrc" | "lyrics_srt" | "lyrics_raw" | "recording";
  path: string;
  byteSize: number;
  sha256: string;
  contentUrl: string;
  metadata: Record<string, unknown>;
};

export type AudioArtifact = Omit<ProjectArtifact, "kind" | "metadata"> & {
  kind: "normalized_audio" | "vocals" | "instrumental";
  metadata: {
    duration_seconds: number;
    sample_rate: number;
    channels: number;
    codec: string;
    container: string;
    device?: string;
    model_id?: string;
    revision?: string;
  };
};

export function getRuntime(): Promise<RuntimeStatus> {
  return engineFetch<RuntimeStatus>("/api/v1/runtime");
}

export function getProjectArtifacts(projectId: string): Promise<ProjectArtifact[]> {
  return engineFetch<ProjectArtifact[]>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/artifacts`,
  );
}

async function fetchArtifact(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  signal?: AbortSignal,
): Promise<Response> {
  const connection = await getEngineConnection();
  const target = new URL(artifact.contentUrl, connection.baseUrl);
  if (
    target.origin !== new URL(connection.baseUrl).origin ||
    !target.pathname.startsWith("/api/v1/artifacts/")
  ) {
    throw new Error("invalid_artifact_content_url");
  }
  const response = await fetch(target.href, {
    headers: { "X-AI4Music-Token": connection.token },
    signal,
  });
  if (!response.ok) throw await EngineApiError.fromResponse(response);
  return response;
}

export async function getArtifactBlob(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  signal?: AbortSignal,
): Promise<Blob> {
  return (await fetchArtifact(artifact, signal)).blob();
}

export async function getArtifactArrayBuffer(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  signal?: AbortSignal,
): Promise<ArrayBuffer> {
  return (await fetchArtifact(artifact, signal)).arrayBuffer();
}

async function getArtifactMediaUrl(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  download: boolean,
  signal?: AbortSignal,
): Promise<string> {
  const connection = await getEngineConnection();
  const endpoint = new URL(artifact.contentUrl, connection.baseUrl);
  endpoint.pathname += "/playback";
  if (download) endpoint.searchParams.set("download", "true");
  const response = await fetchArtifact({ contentUrl: endpoint.href }, signal);
  const grant: { contentUrl: string } = await response.json();
  const target = new URL(grant.contentUrl, connection.baseUrl);
  if (target.origin !== endpoint.origin || !target.pathname.startsWith("/api/v1/playback/")) {
    throw new Error("invalid_artifact_playback_url");
  }
  return target.href;
}

export function getArtifactPlaybackUrl(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  signal?: AbortSignal,
): Promise<string> {
  return getArtifactMediaUrl(artifact, false, signal);
}

export function getArtifactDownloadUrl(
  artifact: Pick<ProjectArtifact, "contentUrl">,
  signal?: AbortSignal,
): Promise<string> {
  return getArtifactMediaUrl(artifact, true, signal);
}

export function isAudioArtifact(artifact: ProjectArtifact): artifact is AudioArtifact {
  return ["normalized_audio", "vocals", "instrumental"].includes(artifact.kind) &&
    typeof artifact.metadata.duration_seconds === "number" && typeof artifact.metadata.sample_rate === "number" &&
    typeof artifact.metadata.channels === "number";
}

export function latestByKind(artifacts: ProjectArtifact[]): ProjectArtifact[] {
  const kinds = new Set<string>();
  return artifacts.filter(artifact => {
    if (kinds.has(artifact.kind)) return false;
    kinds.add(artifact.kind);
    return true;
  });
}
