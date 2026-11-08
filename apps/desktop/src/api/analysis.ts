import { engineFetch } from "./engine";
import { getArtifactBlob, type ProjectArtifact } from "./processing";

export type TimeRange = { start: number; end: number };
export type WaveformData = {
  schemaVersion: number;
  durationSeconds: number;
  sampleRate: number;
  channels: number;
  peaks: [number, number][];
};
export type BeatData = {
  schemaVersion: number;
  durationSeconds: number;
  bpm: number | null;
  beats: number[];
  confidence: number;
  reason?: string;
};
export type LyricSegment = {
  start: number;
  end: number;
  text: string;
  review?: { required: boolean; reasons: string[]; excludeFromPrompt?: boolean };
  words?: { start: number; end: number; word: string; probability?: number }[];
};
export type LyricsDocument = {
  schemaVersion: number;
  durationSeconds: number;
  language: string | null;
  segments: LyricSegment[];
};

export async function readArtifactJSON<T>(
  artifact: ProjectArtifact,
  signal?: AbortSignal,
): Promise<T> {
  const blob = await getArtifactBlob(artifact, signal);
  if (blob.size > 16 * 1024 * 1024) throw new Error("分析结果超过读取上限");
  return JSON.parse(await blob.text()) as T;
}

export function saveLyrics(
  projectId: string,
  document: LyricsDocument,
): Promise<ProjectArtifact[]> {
  return engineFetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/lyrics`,
    { method: "POST", body: JSON.stringify(document) },
  );
}

export function importLRC(
  projectId: string,
  text: string,
  durationSeconds: number,
): Promise<ProjectArtifact[]> {
  return engineFetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/lyrics/import`,
    { method: "POST", body: JSON.stringify({ text, durationSeconds }) },
  );
}
