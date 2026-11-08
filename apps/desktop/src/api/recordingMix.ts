import type { TimeRange } from "./analysis";
import { EngineApiError, engineFetch, getEngineConnection } from "./engine";

export type MixSettings = {
  preparationSeconds: number;
  preparationAudio: "instrumental" | "mixed" | "vocals";
  vocalPercent: number;
  includeBacking: boolean;
};

export const DEFAULT_MIX_SETTINGS: MixSettings = {
  preparationSeconds: 3,
  preparationAudio: "instrumental",
  vocalPercent: 0,
  includeBacking: true,
};

export type RecordingMix = { contentUrl: string; durationSeconds: number };

export async function getRecordingMix(
  projectId: string,
  ranges: readonly TimeRange[],
  { preparationSeconds, preparationAudio, vocalPercent }: MixSettings,
  signal?: AbortSignal,
): Promise<RecordingMix> {
  const path = `/api/v1/projects/${encodeURIComponent(projectId)}/recording-mix`;
  const request = { method: "POST", body: JSON.stringify({ ranges, preparationSeconds, preparationAudio, vocalPercent }), signal };
  const data = await requestMix(path, request);
  const { baseUrl } = await getEngineConnection();
  const target = new URL(data.contentUrl, baseUrl);
  if (target.origin !== new URL(baseUrl).origin || !target.pathname.startsWith("/api/v1/mixes/") ||
      target.username || target.password || !Number.isFinite(data.durationSeconds) || data.durationSeconds <= 0) {
    throw new Error("混音响应无效，请重试。");
  }
  return { contentUrl: target.href, durationSeconds: data.durationSeconds };
}

async function requestMix(path: string, request: RequestInit): Promise<RecordingMix> {
  // A canceled preview can leave the engine finishing its current render.
  for (const delay of [250, 500, 1000]) {
    request.signal?.throwIfAborted();
    try {
      return await engineFetch<RecordingMix>(path, request);
    } catch (error) {
      if (!(error instanceof EngineApiError) || error.code !== "recording_mix_busy") throw error;
      await new Promise<void>(resolve => setTimeout(resolve, delay));
    }
  }
  request.signal?.throwIfAborted();
  return engineFetch<RecordingMix>(path, request);
}

export async function loadRecordingMix(mix: RecordingMix, signal: AbortSignal): Promise<ArrayBuffer> {
  const response = await fetch(mix.contentUrl, { signal, redirect: "error", cache: "no-store" });
  if (!response.ok) throw await EngineApiError.fromResponse(response);
  return response.arrayBuffer();
}
