import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from "react";

import { EngineApiError, getEngineConnection } from "../api/engine";
import { getRecordingMix, loadRecordingMix, type MixSettings } from "../api/recordingMix";
import { BrowserRecorder, type RecordedTake } from "../recording/browserRecorder";
import { captureConstraints } from "../recording/browserRecorder";
import { CameraScene, DEFAULT_LOOK, drawCompositeFrame, type CameraLook, type SceneOptions } from "../recording/compositor";
import { CameraControls, PaperBagToggle } from "../recording/CameraControls";
import { DEFAULT_COMPOSITION, canvasSize, type CompositionSettings } from "../recording/composition";
import { claimAudioFocus } from "../recording/audioFocus";
import { MAX_MIX_SECONDS, planMomentAt, type PlaybackPlan } from "../recording/playbackPlan";
import { RecordingMixControls } from "../recording/RecordingMixControls";
import type { LyricSegment, TimeRange } from "../api/analysis";
import type { HeadTrackingStatus } from "../recording/headTracking";
import "../recording/recording.css";

export type RecordingPanelProps = {
  projectId: string;
  mixPlan: PlaybackPlan;
  mixSettings: MixSettings;
  hasSeparatedStems: boolean;
  onMixSettingsChange: (settings: MixSettings) => void;
  disabled?: boolean;
  onSaved: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  lyrics?: LyricSegment[];
  title?: string;
  onCaptureChange?: (active: boolean) => void;
};

export function RecordingPanel(props: RecordingPanelProps) {
  // Project changes deliberately own a new device/session lifetime.
  return <RecordingSession key={props.projectId} {...props} />;
}

type Phase = "idle" | "preparing" | "recording" | "stopping" | "review" | "saving" | "saved" | "error";
type LocalTake = RecordedTake & {
  range: TimeRange;
  ranges: TimeRange[];
  mixSettings: MixSettings;
  totalSeconds: number;
  url: string;
  composition: CompositionSettings;
};

function RecordingSession({
  projectId, mixPlan, mixSettings, hasSeparatedStems, onMixSettingsChange, disabled = false,
  onSaved, onDirtyChange, lyrics = [], title = "我的翻唱", onCaptureChange,
}: RecordingPanelProps) {
  const headingId = useId();
  const panel = useRef<HTMLElement>(null);
  const [camera, setCamera] = useState(true);
  const [cameraId, setCameraId] = useState("");
  const [microphoneId, setMicrophoneId] = useState("");
  const [look, setLook] = useState<CameraLook>(DEFAULT_LOOK);
  const [composition, setComposition] = useState<CompositionSettings>(DEFAULT_COMPOSITION);
  const [previewing, setPreviewing] = useState(false);
  const [requestingPreview, setRequestingPreview] = useState(false);
  const [recordPosition, setRecordPosition] = useState(0);
  const [preparation, setPreparation] = useState(0);
  const scenePreparation = useRef(0);
  const [trackingStatus, setTrackingStatus] = useState<HeadTrackingStatus | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [take, setTake] = useState<LocalTake | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const live = useRef(true);
  const capture = useRef<BrowserRecorder | null>(null);
  const previewUrl = useRef<string | null>(null);
  const saveRequest = useRef<AbortController | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const scene = useRef<CameraScene | null>(null);
  const previewInput = useRef<MediaStream | null>(null);
  const previewGeneration = useRef(0);
  const captureScene = useRef<{ look: CameraLook; composition: CompositionSettings; lyrics: LyricSegment[]; title: string; start: number } | null>(null);
  const playback = useRef<HTMLMediaElement | null>(null);
  const savedCallback = useRef(onSaved);
  savedCallback.current = onSaved;
  const attachPlayback = useCallback((element: HTMLMediaElement | null) => {
    // Ref detachment precedes effect cleanup on unmount; stop the retained element here.
    if (playback.current && playback.current !== element) {
      playback.current.pause();
      playback.current.removeAttribute("src");
      playback.current.load();
    }
    playback.current = element;
    if (element && previewUrl.current && element.src !== previewUrl.current) {
      element.src = previewUrl.current;
    }
  }, []);

  const capturing = phase === "preparing" || phase === "recording" || phase === "stopping";
  useEffect(() => { onDirtyChange?.(capturing || Boolean(take && phase !== "saved")); }, [capturing,take,phase,onDirtyChange]);
  useEffect(() => { onCaptureChange?.(capturing); }, [capturing, onCaptureChange]);
  const captureRanges = mixPlan.ranges;
  const includeBacking = hasSeparatedStems && mixSettings.includeBacking;
  const exceedsMixLimit = includeBacking && mixPlan.totalSeconds > MAX_MIX_SECONDS;
  const rangeValid = captureRanges.length > 0 && !exceedsMixLimit;
  const sceneOptions = useRef<() => SceneOptions>(() => ({ look, composition, lyrics, title, time: 0, standby: true }));
  sceneOptions.current = () => {
    const frozen = captureScene.current;
    const moment = capture.current?.moment;
    const remaining = moment ? Math.ceil(moment.remainingSeconds) : scenePreparation.current;
    return frozen ? { look: frozen.look, composition: frozen.composition, lyrics: frozen.lyrics, title: frozen.title, time: moment?.sourceTime ?? frozen.start, preparation: remaining || undefined }
      : { look, composition, lyrics, title, time: 0, standby: true };
  };

  function closePreview() {
    ++previewGeneration.current;
    scene.current?.dispose(); scene.current = null;
    captureScene.current = null;
    previewInput.current?.getTracks().forEach(track => { if (track.readyState !== "ended") track.stop(); });
    previewInput.current = null;
    if (live.current) { setPreviewing(false); setRequestingPreview(false); setTrackingStatus(null); }
  }

  function resetDeviceError() {
    setError(null);
    if (phase === "error") setPhase("idle");
    setRecordPosition(0);
    setPreparation(0);
  }

  function selectDevices() {
    closePreview(); resetDeviceError();
    const control = panel.current?.querySelector<HTMLSelectElement>('.device-controls select[aria-label="麦克风"]');
    control?.scrollIntoView?.({ block: "nearest" }); control?.focus({ preventScroll: true });
  }

  async function ensureScene(input: MediaStream) {
    if (!canvas.current) throw new Error("录制画面尚未就绪，请重新打开预览。");
    if (!scene.current) {
      const next = new CameraScene(canvas.current, input, () => sceneOptions.current(), status => { if (live.current) setTrackingStatus(status); });
      scene.current = next;
      await next.start();
    }
  }

  async function composeVideo(input: MediaStream) {
    await ensureScene(input);
    if (!scene.current) throw new Error("录制预览已关闭，请重新打开。");
    return scene.current.capture();
  }

  async function togglePreview() {
    if (capturing || disabled || requestingPreview) return;
    if (previewing) { closePreview(); return; }
    const generation = ++previewGeneration.current;
    setRequestingPreview(true); resetDeviceError();
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("当前环境无法打开摄像头，请使用桌面版或支持设备录制的浏览器。");
      const input = await navigator.mediaDevices.getUserMedia(captureConstraints(true, cameraId, microphoneId));
      if (!live.current || generation !== previewGeneration.current) { input.getTracks().forEach(track => track.stop()); return; }
      previewInput.current = input;
      await ensureScene(input);
      if (!live.current || generation !== previewGeneration.current) return;
      setPreviewing(true);
      setError(null); setPhase("idle");
    } catch (caught) {
      if (live.current && generation === previewGeneration.current) {
        closePreview();
        setError(previewError(caught)); setPhase("error");
      }
    } finally { if (live.current && generation === previewGeneration.current) setRequestingPreview(false); }
  }

  function clearPlayback() {
    if (playback.current) {
      playback.current.pause();
      playback.current.removeAttribute("src");
      playback.current.load();
    }
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
  }

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      capture.current?.dispose();
      closePreview();
      saveRequest.current?.abort();
      clearPlayback();
    };
  }, []);

  useEffect(() => {
    if (canvas.current && !scene.current && typeof CanvasRenderingContext2D !== "undefined") drawCompositeFrame(canvas.current, null, sceneOptions.current());
  }, [look, composition, lyrics, title, phase, camera, previewing]);

  useEffect(() => {
    if (!capturing && (!take || phase === "saved")) return;
    const preventLoss = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, [capturing, take, phase]);

  function startRecording() {
    if (disabled || !rangeValid || capturing || take || requestingPreview) return;
    claimAudioFocus(null);
    capture.current?.dispose();
    setError(null);
    setLevel(0);
    setElapsed(0);
    const selectedRanges = captureRanges.map(item => ({ ...item }));
    const selectedRange = { start: selectedRanges[0].start, end: selectedRanges.at(-1)!.end };
    const selectedMix = { ...mixSettings, includeBacking };
    const initial = planMomentAt(mixPlan, 0);
    scenePreparation.current = initial.preparing ? Math.ceil(initial.remainingSeconds) : 0;
    setRecordPosition(initial.sourceTime);
    setPreparation(scenePreparation.current);
    setPhase("preparing");
    const selectedComposition = { ...composition };
    captureScene.current = { look: { ...look }, composition: selectedComposition, lyrics: lyrics.map(line => ({ ...line })), title, start: initial.sourceTime };
    const recorder = new BrowserRecorder({
      onRecording: () => {
        if (!live.current || capture.current !== recorder) return;
        setPhase("recording");
      },
      onPosition: (position, moment) => {
        if (!live.current || capture.current !== recorder) return;
        scenePreparation.current = moment?.preparing ? Math.ceil(moment.remainingSeconds) : 0;
        setPreparation(scenePreparation.current);
        setRecordPosition(position);
      },
      onLevel: (nextLevel, seconds) => {
        if (!live.current || capture.current !== recorder) return;
        setLevel(nextLevel);
        setElapsed(seconds);
      },
      onFinished: recorded => {
        if (!live.current || capture.current !== recorder) return;
        const url = URL.createObjectURL(recorded.blob);
        previewUrl.current = url;
        setTake({ ...recorded, range: selectedRange, ranges: selectedRanges, mixSettings: selectedMix, totalSeconds: mixPlan.totalSeconds, composition: selectedComposition, url });
        closePreview();
        setLevel(0);
        setPhase("review");
      },
      onError: message => {
        if (!live.current || capture.current !== recorder) return;
        closePreview();
        setLevel(0);
        setError(message);
        setPhase("error");
      },
    });
    capture.current = recorder;
    void recorder.start({
      video: camera,
      range: selectedRange,
      ranges: selectedRanges,
      mixPlan,
      cameraDeviceId: cameraId,
      microphoneDeviceId: microphoneId,
      ...(camera ? { composeVideo, ...(previewInput.current ? { inputStream: previewInput.current } : {}) } : {}),
      ...(includeBacking ? { loadBacking: async (signal: AbortSignal) => {
        const mix = await getRecordingMix(projectId, selectedRanges, selectedMix, signal);
        return loadRecordingMix(mix, signal);
      } } : {}),
    });
  }

  function stopRecording() {
    if (phase === "preparing") {
      capture.current?.dispose();
      capture.current = null;
      setPhase("idle");
    } else {
      setPhase("stopping");
      capture.current?.stop();
    }
    if (phase === "preparing") closePreview();
    setLevel(0);
  }

  function discardTake() {
    if (phase === "saving") return;
    if (phase !== "saved" && !window.confirm("丢弃这段尚未保存的录制？此操作无法撤销。")) return;
    clearPlayback();
    setTake(null);
    setError(null);
    setElapsed(0);
    setPhase("idle");
  }

  async function saveTake() {
    if (!take || phase === "saving" || phase === "saved") return;
    setPhase("saving");
    setError(null);
    const controller = new AbortController();
    saveRequest.current = controller;
    try {
      const connection = await getEngineConnection();
      if (controller.signal.aborted || !live.current) return;
      const url = new URL(`/api/v1/projects/${encodeURIComponent(projectId)}/recordings`, connection.baseUrl);
      url.searchParams.set("start", String(take.range.start));
      url.searchParams.set("end", String(take.range.end));
      url.searchParams.set("ranges", JSON.stringify(take.ranges));
      url.searchParams.set("mix", JSON.stringify(take.mixSettings));
      const response = await fetch(url.href, {
        method: "POST",
        headers: { "Content-Type": take.mimeType, "X-AI4Music-Token": connection.token },
        body: take.blob,
        signal: controller.signal,
      });
      if (!response.ok) throw await EngineApiError.fromResponse(response);
      await response.json();
      if (controller.signal.aborted || !live.current) return;
      setPhase("saved");
    } catch (caught) {
      if (!controller.signal.aborted && live.current) {
        setError(caught instanceof EngineApiError
          ? `保存失败：${caught.message}。录制仍保留，可重试保存。`
          : "保存失败，录制仍保留。请检查本机引擎是否运行，再重试保存。");
        setPhase("review");
      }
      return;
    } finally {
      if (saveRequest.current === controller) saveRequest.current = null;
    }
    if (!controller.signal.aborted && live.current) savedCallback.current();
  }

  const dimensions = canvasSize(take?.composition.aspect ?? composition.aspect);
  const previewStyle = { "--recording-ratio": dimensions.width / dimensions.height } as CSSProperties;
  const currentLine = phase === "recording" && !preparation ? lyrics.find(line => recordPosition >= line.start && recordPosition < line.end) : undefined;

  return (
    <section ref={panel} className="recording-panel" aria-labelledby={headingId} data-guide="record" tabIndex={-1}>
      <header className="recording-panel__heading">
        <h2 id={headingId}>录制</h2>
        <span className="recording-panel__range">{(take?.ranges ?? captureRanges).length} 段 · {formatClock(take?.totalSeconds ?? mixPlan.totalSeconds)}</span>
      </header>
      {!take ? <div className="recording-panel__studio">
        <div className="recording-panel__preview">
          {camera ? <>
            <div className="recording-panel__preview-heading">
              <h3>画面预览</h3>
              <span className="recording-canvas-size">{dimensions.width} × {dimensions.height}</span>
            </div>
            <canvas ref={canvas} width={dimensions.width} height={dimensions.height} style={previewStyle} className="recording-panel__canvas" aria-label="人像与备忘录歌词合成预览" />
          </> : null}
          <div className="recording-panel__actions">
            {camera ? <button className="button button--secondary" type="button" disabled={disabled || capturing || requestingPreview} onClick={() => void togglePreview()}>{requestingPreview ? "正在打开设备…" : previewing ? "关闭预览" : "开启摄像头预览"}</button> : null}
            {!capturing ? <button className="button button--primary" type="button" disabled={disabled || !rangeValid || requestingPreview} onClick={startRecording}>开始录制</button> : <button className="button button--secondary" type="button" disabled={phase === "stopping"} onClick={stopRecording}>{phase === "preparing" ? "取消准备" : phase === "stopping" ? "正在生成回放…" : "停止录制"}</button>}
          </div>
          {!rangeValid && !capturing ? <p className="recording-panel__note">
            {exceedsMixLimit ? "背景音最多支持 30 分钟，请缩短选段或关闭背景音。" : "请先选择录制片段。"}
          </p> : null}
          {phase === "preparing" ? <p className="recording-panel__status" role="status">正在准备…</p> : null}
          {phase === "recording" ? <div className="recording-panel__monitor">
            <span className="recording-panel__live" role="status">{preparation && !camera ? `准备 ${preparation}` : "录制中"}</span>
            <output aria-label="录制时长" aria-live="off">{formatClock(elapsed)}</output>
            <label>麦克风输入<meter aria-label="麦克风输入电平" min={0} max={1} value={level} /></label>
          </div> : null}
          {camera && phase === "recording" && (lyrics.length > 0 || preparation > 0) ? <div className={`recording-current-line${currentLine ? " is-active" : ""}`}>
            <span aria-hidden="true">●</span><output aria-label="当前歌词">{preparation ? `准备 ${preparation}` : currentLine?.text ?? "等待下一句"}</output>
          </div> : null}
        </div>
        <div className="recording-panel__settings">
          {camera ? <PaperBagToggle look={look} disabled={disabled || capturing || requestingPreview} onLook={setLook} /> : null}
          <RecordingMixControls
            settings={mixSettings}
            hasSeparatedStems={hasSeparatedStems}
            disabled={disabled || capturing}
            onChange={onMixSettingsChange}
          />
          <CameraControls
            camera={camera}
            cameraId={cameraId}
            microphoneId={microphoneId}
            look={look}
            composition={composition}
            onComposition={setComposition}
            disabled={disabled || capturing}
            previewing={previewing}
            requesting={requestingPreview}
            trackingStatus={trackingStatus}
            onCamera={value => { closePreview(); resetDeviceError(); setCamera(value); }}
            onDevice={(kind, value) => { closePreview(); resetDeviceError(); if (kind === "camera") setCameraId(value); else setMicrophoneId(value); }}
            onLook={setLook}
          />
        </div>
      </div> : null}
      {take ? <div className="recording-panel__review">{take.mimeType === "video/webm"
        ? <video className="recording-panel__video" style={previewStyle} ref={attachPlayback} src={take.url} controls playsInline aria-label="录制回放" onPlay={event => claimAudioFocus(event.currentTarget)} onError={() => setError("此录制无法在当前播放器中播放。可下载 WebM 文件检查，或丢弃后重新录制。")} />
        : <audio ref={attachPlayback} src={take.url} controls aria-label="录制回放" onPlay={event => claimAudioFocus(event.currentTarget)} onError={() => setError("此录制无法在当前播放器中播放。可下载 WebM 文件检查，或丢弃后重新录制。")} />
      }
        <div className="recording-panel__actions">
          {phase === "saved" ? <p className="recording-panel__status" role="status">录制已保存到项目。</p> : <button className="button button--primary" type="button" disabled={phase === "saving"} onClick={() => void saveTake()}>{phase === "saving" ? "正在保存…" : error ? "重试保存" : "保存到项目"}</button>}
          <a className="button button--quiet" href={take.url} download={`CoverLab-${take.mimeType === "video/webm" ? "video" : "audio"}-${projectId}.webm`}>下载录制</a>
          <button className={`button button--quiet${phase === "saved" ? "" : " button--danger"}`} type="button" disabled={phase === "saving"} onClick={discardTake}>{phase === "saved" ? "开始新录制" : "丢弃录制"}</button>
        </div>
      </div> : null}
      {error ? <div className="recording-panel__error" role="alert"><p>{error}</p>{!take && !capturing ? <div className="recording-panel__actions"><button className="button button--secondary" type="button" disabled={disabled || !rangeValid || requestingPreview} onClick={startRecording}>重试录制</button><button className="button button--quiet" type="button" disabled={disabled || requestingPreview} onClick={selectDevices}>重新选择设备</button>{camera ? <button className="button button--quiet" type="button" disabled={disabled || requestingPreview} onClick={() => void togglePreview()}>重试预览</button> : null}</div> : null}</div> : null}
    </section>
  );
}

function formatClock(seconds: number): string {
  const safe = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  const whole = Math.floor(safe);
  return `${Math.floor(whole / 60).toString().padStart(2, "0")}:${(whole % 60).toString().padStart(2, "0")}`;
}

function previewError(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") return "未获得设备权限。请在系统或浏览器设置中允许摄像头与麦克风，然后重试预览。";
    if (error.name === "NotReadableError") return "设备可能正被其他程序占用。关闭占用设备的程序后重试，或重新选择设备。";
    if (error.name === "NotFoundError" || error.name === "OverconstrainedError") return "找不到所选设备。请重新选择摄像头或麦克风。";
  }
  return error instanceof Error && /[\u4e00-\u9fff]/.test(error.message) ? error.message : "无法打开设备。请检查权限或其他程序占用，重试或重新选择设备。";
}
