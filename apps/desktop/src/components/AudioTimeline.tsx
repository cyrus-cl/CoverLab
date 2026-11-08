import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { TimeRange } from "../api/analysis";
import { getArtifactPlaybackUrl, type AudioArtifact } from "../api/processing";
import { selectionDuration } from "../recording/selection";
import { claimAudioFocus } from "../recording/audioFocus";
import { planMomentAt, type PlaybackPlan } from "../recording/playbackPlan";
import "../recording/timeline.css";

type AudioPreviewState = "idle" | "loading" | "preparing" | "playing" | "paused" | "ended";

type Props = {
  audio: AudioArtifact;
  range: TimeRange;
  onRangeChange: (range: TimeRange) => void;
  onTimeChange: (time: number, preparing?: boolean) => void;
  seekRequest?: { time: number; nonce: number } | null;
  selectionRanges?: TimeRange[];
  leadInSeconds?: number;
  mixPlan?: PlaybackPlan;
  mixKey?: string;
  loadMix?: (signal: AbortSignal) => Promise<string>;
  previewDisabled?: boolean;
  lyricSelection?: boolean;
  disabled?: boolean;
  manualOpen?: boolean;
  onManualToggle?: () => void;
  onRecord?: () => void;
};

export function AudioTimeline({
  audio,
  range,
  onRangeChange,
  onTimeChange,
  seekRequest,
  selectionRanges,
  leadInSeconds = 0,
  mixPlan,
  mixKey,
  loadMix,
  previewDisabled = false,
  lyricSelection = Boolean(selectionRanges),
  disabled = false,
  manualOpen = !lyricSelection,
  onManualToggle,
  onRecord,
}: Props) {
  const duration = audio.metadata.duration_seconds;
  const rangeControlsId = useId();
  const [previewState, setPreviewState] = useState<AudioPreviewState>("idle");
  const state = useRef<AudioPreviewState>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [segmentIndex, setSegmentIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(0);
  const preparationTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const preparationRemaining = useRef(0);
  const preparationBeganAt = useRef(0);
  const preparationLength = useRef(0);
  const media = useRef<HTMLAudioElement>(null);
  const attachMedia = useCallback((node: HTMLAudioElement | null) => {
    if (media.current && media.current !== node) {
      media.current.pause();
      media.current.removeAttribute("src");
      media.current.load();
    }
    media.current = node;
  }, []);
  const playbackUrl = useRef<string | null>(null);
  const activeMix = useRef<PlaybackPlan | null>(null);
  const controller = useRef<AbortController | null>(null);
  const current = useRef(0);
  const stopAt = useRef<number | null>(null);
  const queue = useRef<TimeRange[]>([]);
  const queueIndex = useRef(0);
  const frame = useRef<number | null>(null);
  const playbackGeneration = useRef(0);
  const playAttempt = useRef(0);
  const disabledRef = useRef(disabled || previewDisabled);
  disabledRef.current = disabled || previewDisabled;
  const selectionKey = `${leadInSeconds}|${mixPlan?.preparationSeconds ?? ""}|${mixKey ?? ""}|` +
    (selectionRanges ?? [range]).map(item => `${item.start}:${item.end}`).join(",");
  const selected = selectionRanges ?? [range];
  const totalSeconds = mixPlan?.totalSeconds ?? selectionDuration(selected) + leadInSeconds;
  const loading = previewState === "loading";
  function changeState(next: AudioPreviewState) { state.current = next; setPreviewState(next); }
  function clearPreparationTimer() {
    if (preparationTimer.current !== null) clearInterval(preparationTimer.current);
    preparationTimer.current = null;
  }
  function cancelFrame() {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }
  function stopSelection() {
    ++playbackGeneration.current;
    ++playAttempt.current;
    controller.current?.abort();
    clearPreparationTimer();
    preparationRemaining.current = 0;
    media.current?.pause(); cancelFrame();
    if (activeMix.current) {
      activeMix.current = null;
      media.current?.removeAttribute("src");
      media.current?.load();
      onTimeChange(-1, false);
    }
    queue.current = []; stopAt.current = null;
    setPreparing(0); setElapsed(0); setSegmentIndex(0);
    changeState("idle");
  }
  useEffect(() => {
    return () => {
      controller.current?.abort();
      ++playbackGeneration.current;
      clearPreparationTimer();
      media.current?.pause();
      cancelFrame();
    };
  }, []);
  useEffect(() => {
    stopSelection();
  }, [disabled, previewDisabled, selectionKey]);
  useEffect(() => {
    const focus = (event: Event) => {
      if ((event as CustomEvent<{ active: HTMLMediaElement | null }>).detail.active !== media.current
          && state.current !== "idle" && state.current !== "ended") stopSelection();
    };
    window.addEventListener("coverlab:audio-focus", focus);
    return () => window.removeEventListener("coverlab:audio-focus", focus);
  }, []);

  function seek(value: number) {
    const next = Math.max(0, Math.min(duration, value));
    current.current = next;
    onTimeChange(next);
    if (media.current?.src) media.current.currentTime = next;
  }
  useEffect(() => {
    if (seekRequest) { stopSelection(); seek(seekRequest.time); }
  }, [seekRequest]); // explicit user seek requests
  async function playSelection() {
    if (loading || disabledRef.current || selectionRanges?.length === 0 || (mixPlan && !loadMix)) return;
    claimAudioFocus(media.current);
    media.current?.pause();
    setError(null);
    cancelFrame(); clearPreparationTimer();
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    changeState("loading");
    setElapsed(0); setSegmentIndex(0); setPreparing(0);
    const generation = ++playbackGeneration.current;
    const signal = abort.signal;
    const isCurrent = () => !disabledRef.current && generation === playbackGeneration.current && !signal.aborted;
    try {
      if (mixPlan && loadMix) {
        const url = await loadMix(signal);
        if (!media.current || !isCurrent()) return;
        activeMix.current = mixPlan;
        media.current.src = url;
        media.current.currentTime = 0;
        publishMixPosition(mixPlan, 0);
        await playMedia(generation);
        return;
      }
      const next = playbackUrl.current ?? await getArtifactPlaybackUrl(audio, signal);
      if (!media.current || !isCurrent()) return;
      playbackUrl.current = next;
      if (media.current.src !== next) media.current.src = next;
      queue.current = (selectionRanges ?? [range]).map(item => ({ ...item }));
      queueIndex.current = 0;
      stopAt.current = queue.current[0].end;
      preparationLength.current = leadInSeconds;
      preparationRemaining.current = leadInSeconds;
      if (leadInSeconds > 0) {
        beginPreparation(generation);
      } else { seek(queue.current[0].start); await playMedia(generation); }
    } catch {
      if (isCurrent()) { stopSelection(); setError("无法播放此音轨，请检查结果文件或重试。"); }
    }
  }
  async function playMedia(generation: number) {
    if (disabledRef.current || generation !== playbackGeneration.current || !media.current) return;
    changeState("playing");
    const attempt = ++playAttempt.current;
    try { await media.current.play(); }
    catch {
      if (generation === playbackGeneration.current && attempt === playAttempt.current) { playbackUrl.current = null; stopSelection(); setError("无法播放此音轨，请重试。"); }
    }
  }
  function publishPreparation() {
    const remaining = Math.max(0, preparationRemaining.current - (performance.now() - preparationBeganAt.current) / 1000);
    setPreparing(Math.ceil(remaining));
    setElapsed(preparationLength.current - remaining);
    onTimeChange((queue.current[0]?.start ?? 0) - remaining);
    return remaining;
  }
  function beginPreparation(generation: number) {
    changeState("preparing");
    preparationBeganAt.current = performance.now();
    publishPreparation();
    preparationTimer.current = setInterval(() => {
      if (generation !== playbackGeneration.current) { clearPreparationTimer(); return; }
      if (publishPreparation() > .001) return;
      clearPreparationTimer(); preparationRemaining.current = 0; setPreparing(0);
      seek(queue.current[0].start); void playMedia(generation);
    }, 50);
  }
  function pauseSelection() {
    ++playAttempt.current;
    if (state.current === "preparing") {
      preparationRemaining.current = publishPreparation();
      clearPreparationTimer();
    } else { updatePosition(); if (state.current === "ended") return; }
    media.current?.pause(); cancelFrame(); changeState("paused");
  }
  async function togglePlayback() {
    if (disabledRef.current) return;
    if (state.current === "loading") { stopSelection(); return; }
    if (state.current === "playing" || state.current === "preparing") { pauseSelection(); return; }
    if (state.current === "paused") {
      claimAudioFocus(media.current);
      if (preparationRemaining.current > 0) beginPreparation(playbackGeneration.current);
      else await playMedia(playbackGeneration.current);
      return;
    }
    await playSelection();
  }
  function publishMixPosition(plan: PlaybackPlan, elapsedSeconds: number) {
    const moment = planMomentAt(plan, elapsedSeconds);
    setElapsed(Math.min(plan.totalSeconds, elapsedSeconds));
    setPreparing(Math.ceil(moment.remainingSeconds));
    setSegmentIndex(moment.segmentIndex);
    onTimeChange(moment.sourceTime, moment.preparing);
  }
  function updatePosition() {
    if (preparationTimer.current || state.current === "paused") return;
    const node = media.current;
    if (!node) return;
    if (activeMix.current) {
      if (state.current === "playing") publishMixPosition(activeMix.current, node.currentTime);
      return;
    }
    let position = node.currentTime;
    if (stopAt.current !== null && position >= stopAt.current) {
      const next = queue.current[queueIndex.current + 1];
      if (next) {
        ++queueIndex.current;
        setSegmentIndex(queueIndex.current);
        stopAt.current = next.end;
        position = next.start;
        node.currentTime = position;
      } else {
        position = stopAt.current;
        node.pause();
        stopAt.current = null;
        cancelFrame();
        setElapsed(selectionDuration(queue.current) + preparationLength.current);
        changeState("ended");
      }
    }
    if (stopAt.current !== null && queue.current.length) {
      const before = selectionDuration(queue.current.slice(0, queueIndex.current));
      const active = queue.current[queueIndex.current];
      setElapsed(preparationLength.current + before + Math.max(0, Math.min(active.end, position) - active.start));
    }
    if (Math.abs(position - current.current) >= 0.035) {
      current.current = position;
      onTimeChange(position);
    }
  }
  return (
    <div className="audio-timeline">
      <div className="timeline-controls" role="toolbar" aria-label="选段操作">
        <button
          type="button"
          className="button button--secondary"
          disabled={disabled || previewDisabled || selectionRanges?.length === 0}
          onClick={() => void togglePlayback()}
        >
          {loading ? "取消读取" : previewState === "playing" || previewState === "preparing" ? "暂停试听" : previewState === "paused" ? "继续试听" : lyricSelection ? "试听选段" : "试听片段"}
        </button>
        {previewState !== "idle" && previewState !== "ended" && !loading ? <button type="button" className="button button--quiet" onClick={stopSelection}>停止试听</button> : null}
        {onRecord ? <button type="button" className="button button--primary" disabled={disabled || !selected.length} onClick={onRecord}>去录制</button> : null}
        {onManualToggle ? (
          <button type="button" className="button button--quiet" aria-expanded={manualOpen} aria-controls={rangeControlsId} disabled={disabled} onClick={onManualToggle}>
            手动起止时间
          </button>
        ) : null}
        {selected.length ? <>
          <span className="timeline-segment" aria-live="polite">{previewState === "paused" ? "已暂停 · " : previewState === "ended" ? "试听结束 · " : preparing > 0 ? `准备 ${preparing} · ` : ""}{`第 ${segmentIndex + 1} / ${selected.length} 段`}</span>
          <output className="timeline-time" aria-label="所选片段试听时长">{formatTime(elapsed)} / {formatTime(totalSeconds)}</output>
        </> : null}
      </div>
      {selected.length ? <progress className="timeline-selection-progress" aria-label="所选片段试听进度" value={elapsed} max={Math.max(.001, totalSeconds)} /> : null}
      <audio
        ref={attachMedia}
        hidden
        preload="metadata"
        aria-label="时间轴播放器"
        onLoadedMetadata={() => {
          if (media.current && !activeMix.current) media.current.currentTime = current.current;
        }}
        onPlay={() => {
          if (disabledRef.current || state.current !== "playing") { media.current?.pause(); return; }
          claimAudioFocus(media.current);
          const tick = () => { updatePosition(); if (media.current && !media.current.paused) frame.current = requestAnimationFrame(tick); };
          if (frame.current !== null) cancelAnimationFrame(frame.current);
          frame.current = requestAnimationFrame(tick);
        }}
        onPause={cancelFrame}
        onEnded={() => {
          if (!activeMix.current) { updatePosition(); return; }
          setElapsed(activeMix.current.totalSeconds);
          setPreparing(0);
          onTimeChange(-1, false);
          cancelFrame();
          changeState("ended");
        }}
        onTimeUpdate={() => {
          updatePosition();
        }}
        onError={() => {
          if (playbackUrl.current || activeMix.current) { playbackUrl.current = null; stopSelection(); setError("音轨解码失败，请重试。"); }
        }}
      />
      <div className="range-controls" id={rangeControlsId} hidden={!manualOpen}>
        <label>
          片段开始（秒）
          <input
            disabled={disabled}
            type="number"
            min="0"
            max={range.end - 0.01}
            step="0.01"
            value={Number(range.start.toFixed(2))}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n))
                onRangeChange({
                  start: Math.max(0, Math.min(range.end - 0.01, n)),
                  end: range.end,
                });
            }}
          />
        </label>
        <label>
          片段结束（秒）
          <input
            disabled={disabled}
            type="number"
            min={range.start + 0.01}
            max={duration}
            step="0.01"
            value={Number(range.end.toFixed(2))}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isFinite(n))
                onRangeChange({
                  start: range.start,
                  end: Math.min(duration, Math.max(range.start + 0.01, n)),
                });
            }}
          />
        </label>
        <button
          type="button"
          className="button button--quiet"
          disabled={disabled}
          onClick={() => onRangeChange({ start: 0, end: duration })}
        >
          选择整曲
        </button>
      </div>
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function formatTime(value: number): string {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  return `${Math.floor(safe / 60)
    .toString()
    .padStart(2, "0")}:${(safe % 60).toFixed(2).padStart(5, "0")}`;
}
