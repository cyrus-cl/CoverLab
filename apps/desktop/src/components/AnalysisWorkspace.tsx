import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type LyricsDocument,
  type TimeRange,
} from "../api/analysis";
import { DEFAULT_MIX_SETTINGS, getRecordingMix } from "../api/recordingMix";
import { useArtifactData } from "../hooks/useArtifactData";
import {
  getArtifactDownloadUrl,
  getArtifactPlaybackUrl,
  isAudioArtifact,
  latestByKind,
  type ProjectArtifact,
} from "../api/processing";
import { AudioTimeline } from "./AudioTimeline";
import { LyricsEditor } from "./LyricsEditor";
import { RecordingPanel } from "./RecordingPanel";
import { lyricRanges } from "../recording/selection";
import { MAX_MIX_SECONDS, playbackPlan } from "../recording/playbackPlan";
import { suspiciousRepetition } from "../recording/lyricReview";
import { claimAudioFocus } from "../recording/audioFocus";
import { focusWorkflowStep } from "./workspaceNavigation";
import "../styles/analysis.css";
import "../styles/workflow.css";

type Props = {
  projectId: string;
  artifacts: ProjectArtifact[];
  disabled: boolean;
  onSaved: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  title?: string;
  onRecordingChange?: (active: boolean) => void;
};

const validLyrics = (text: LyricsDocument) =>
  Boolean(text) &&
  Array.isArray(text.segments) &&
  text.segments.every(
    (s) =>
      Number.isFinite(s.start) &&
      Number.isFinite(s.end) &&
      typeof s.text === "string",
  );

export function AnalysisWorkspace({
  projectId,
  artifacts,
  disabled,
  onSaved,
  onDirtyChange,
  title = "我的翻唱",
  onRecordingChange,
}: Props) {
  const latest = latestByKind(artifacts);
  const tracks = latest.filter(isAudioArtifact);
  const instrumental = tracks.find((item) => item.kind === "instrumental");
  const hasSeparatedStems = Boolean(instrumental && tracks.some((item) => item.kind === "vocals"));
  const track = instrumental ?? tracks.find((item) => item.kind === "normalized_audio") ?? tracks[0];
  const [mixSettings, setMixSettings] = useState(DEFAULT_MIX_SETTINGS);
  const lyricArtifact = latest.find((item) => item.kind === "lyrics");
  const { data: lyrics, loading, error } = useArtifactData<LyricsDocument>(lyricArtifact, validLyrics);
  const [lyricDirty, setLyricDirty] = useState(false);
  const [recordDirty, setRecordDirty] = useState(false);
  useEffect(() => {
    onDirtyChange?.(lyricDirty || recordDirty);
  }, [lyricDirty, recordDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  const [time, setTime] = useState(0);
  const [previewPreparing, setPreviewPreparing] = useState(false);
  const onPreviewTime = useCallback((sourceTime: number, preparing = false) => {
    setTime(sourceTime);
    setPreviewPreparing(preparing);
  }, []);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([]);
  const [approvedIndices, setApprovedIndices] = useState<number[]>([]);
  const [selectionMode, setSelectionMode] = useState<"lyrics" | "manual">("lyrics");
  const [recordingActive, setRecordingActive] = useState(false);
  useEffect(() => { onRecordingChange?.(recordingActive); }, [recordingActive, onRecordingChange]);
  const [range, setRange] = useState<TimeRange>({
    start: 0,
    end: track?.metadata.duration_seconds ?? 0,
  });
  const [seekRequest, setSeekRequest] = useState<{
    time: number;
    nonce: number;
  } | null>(null);
  const duration = track?.metadata.duration_seconds ?? 0;
  const emptyLyrics = useMemo<LyricsDocument>(
    () => ({
      schemaVersion: 1,
      durationSeconds: duration,
      language: null,
      segments: [],
    }),
    [duration],
  );
  useEffect(() => {
    setRange({ start: 0, end: duration });
  }, [duration]);
  useEffect(() => { setSelectedIndices([]); setApprovedIndices([]); }, [lyricArtifact?.id]);
  const usingLyrics = selectionMode === "lyrics" && Boolean(lyrics?.segments.length);
  const chosenRanges = usingLyrics ? lyricRanges(lyrics?.segments ?? [], selectedIndices, duration) : [range];
  const plan = playbackPlan(chosenRanges, duration, mixSettings.preparationSeconds);
  const canPreviewMix = hasSeparatedStems && mixSettings.includeBacking && plan.totalSeconds <= MAX_MIX_SECONDS;
  const mixKey = JSON.stringify(mixSettings);
  const chosenLyrics = (lyrics?.segments ?? []).filter((line,index) => (usingLyrics ? selectedIndices.includes(index) : line.end > range.start && line.start < range.end) && (approvedIndices.includes(index) || (!suspiciousRepetition(line.text) && !line.review?.excludeFromPrompt)));
  if (!track) return null;
  return (
    <div className="analysis-workspace">
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <div className="lyric-workspace-slot" data-workflow="lyrics" tabIndex={-1}>
      <LyricsEditor
        projectId={projectId}
        document={lyrics ?? emptyLyrics}
        artifacts={latest}
        currentTime={previewPreparing ? -1 : time}
        loading={loading}
        onSeek={position => setSeekRequest({ time: position, nonce: Date.now() })}
        playback={<AudioTimeline
          key={`${track.id}:${track.sha256}`}
          audio={track}
          range={range}
          onRangeChange={setRange}
          onTimeChange={onPreviewTime}
          seekRequest={seekRequest}
          selectionRanges={plan.ranges}
          mixPlan={plan}
          mixKey={mixKey}
          previewDisabled={!canPreviewMix}
          loadMix={canPreviewMix ? async signal => (await getRecordingMix(projectId, plan.ranges, mixSettings, signal)).contentUrl : undefined}
          lyricSelection={usingLyrics}
          disabled={disabled || recordingActive}
          manualOpen={selectionMode === "manual"}
          onManualToggle={() => setSelectionMode(selectionMode === "manual" ? "lyrics" : "manual")}
          onRecord={!lyricDirty && !recordDirty ? () => focusWorkflowStep("record") : undefined}
        />}
        disabled={disabled || recordingActive}
        selectedIndices={selectedIndices}
        onReviewApprove={index => setApprovedIndices(items => [...items,index])}
        onSelectionChange={indices => {
          setSelectedIndices(indices); setSelectionMode("lyrics");
          const ranges = lyricRanges(lyrics?.segments ?? [], indices, duration);
          if (ranges.length) setSeekRequest({ time: ranges[0].start, nonce: Date.now() });
        }}
        onSaved={onSaved}
        onDirtyChange={setLyricDirty}
        onSelect={(selection) => {
          if (recordingActive) return;
          if (!usingLyrics || !selectedIndices.length) { setRange(selection); setSelectionMode("manual"); }
          setSeekRequest({ time: selection.start, nonce: Date.now() });
        }}
      />
      </div>
      <RecordingPanel
        projectId={projectId}
        mixPlan={plan}
        mixSettings={mixSettings}
        hasSeparatedStems={hasSeparatedStems}
        onMixSettingsChange={setMixSettings}
        lyrics={chosenLyrics}
        title={title}
        disabled={disabled}
        onSaved={onSaved}
        onDirtyChange={setRecordDirty}
        onCaptureChange={setRecordingActive}
      />
      <SavedRecordings
        artifacts={artifacts.filter((a) => a.kind === "recording")}
      />
    </div>
  );
}

function SavedRecordings({ artifacts }: { artifacts: ProjectArtifact[] }) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    return () => abort.abort();
  }, []);
  if (!artifacts.length) return null;
  async function load(artifact: ProjectArtifact) {
    setError(null);
    const signal = controller.current?.signal;
    try {
      const url = await getArtifactPlaybackUrl(artifact, signal);
      if (!signal?.aborted) setUrls(previous => ({ ...previous, [artifact.id]: url }));
    } catch {
      if (!signal?.aborted) setError("无法读取已保存的录制文件。");
    }
  }
  async function download(artifact: ProjectArtifact) {
    setError(null);
    const signal = controller.current?.signal;
    try {
      const url = await getArtifactDownloadUrl(artifact, signal);
      if (signal?.aborted) return;
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.path.split(/[\\/]/).pop() ?? "CoverLab.webm";
      anchor.click();
    } catch {
      if (!signal?.aborted) setError("无法下载录制文件，请重试。");
    }
  }
  return (
    <section className="saved-recordings">
      <h2>已保存录制</h2>
      {artifacts.map((artifact, index) => (
        <div className="saved-recording" key={artifact.id}>
          <span>
            录制 {artifacts.length - index} ·{" "}
            {Number(artifact.metadata.duration_seconds ?? 0).toFixed(1)} 秒
          </span>
          {urls[artifact.id] ? (
            <>
              <RecordedMedia
                url={urls[artifact.id]}
                video={Boolean(artifact.metadata.has_video)}
                label={`录制 ${artifacts.length - index} 回放`}
              />
              <button type="button" className="button button--quiet" onClick={() => void download(artifact)}>
                导出 WebM
              </button>
            </>
          ) : (
            <button
              type="button"
              className="button button--secondary"
              onClick={() => void load(artifact)}
            >
              回放与导出
            </button>
          )}
        </div>
      ))}
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}

function RecordedMedia({
  url,
  video,
  label,
}: {
  url: string;
  video: boolean;
  label: string;
}) {
  const retained = useRef<HTMLMediaElement | null>(null);
  const attach = useCallback((node: HTMLMediaElement | null) => {
    if (retained.current && retained.current !== node) {
      retained.current.pause();
      retained.current.removeAttribute("src");
      retained.current.load();
    }
    retained.current = node;
    if (node && node.src !== url) node.src = url;
  }, [url]);
  return video ? (
    <video ref={attach} src={url} controls aria-label={label} onPlay={event => claimAudioFocus(event.currentTarget)} />
  ) : (
    <audio ref={attach} src={url} controls aria-label={label} onPlay={event => claimAudioFocus(event.currentTarget)} />
  );
}
