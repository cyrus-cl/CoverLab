import type { TimeRange } from "../api/analysis";
import { selectionDuration, validRanges } from "./selection";
import { planMomentAt, planSourceTime, type PlaybackMoment, type PlaybackPlan } from "./playbackPlan";

export type RecordedTake = { blob: Blob; mimeType: "audio/webm" | "video/webm" };
type CaptureOptions = {
  video: boolean;
  range: { start: number; end: number };
  ranges?: TimeRange[];
  leadInSeconds?: number;
  mixPlan?: PlaybackPlan;
  cameraDeviceId?: string;
  microphoneDeviceId?: string;
  inputStream?: MediaStream;
  composeVideo?: (input: MediaStream) => Promise<MediaStream>;
  loadBacking?: (signal: AbortSignal) => Promise<ArrayBuffer>;
};
type CaptureCallbacks = {
  onFinished: (take: RecordedTake) => void;
  onError: (message: string) => void;
  onRecording: (stream: MediaStream) => void;
  onLevel: (level: number, elapsedSeconds: number) => void;
  onPosition?: (sourceSeconds: number, moment?: PlaybackMoment) => void;
};

export function captureConstraints(video: boolean, cameraDeviceId = "", microphoneDeviceId = ""): MediaStreamConstraints {
  return { audio: microphoneDeviceId ? { deviceId: { exact: microphoneDeviceId } } : true,
    video: video ? cameraDeviceId ? { deviceId: { exact: cameraDeviceId } } : true : false };
}

export class BrowserRecorder {
  private disposed = false;
  private busy = false;
  private stopping = false;
  private generation = 0;
  private abort: AbortController | null = null;
  private context: AudioContext | null = null;
  private input: MediaStream | null = null;
  private mixed: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private backing: AudioBufferSourceNode[] = [];
  private ranges: TimeRange[] = [];
  private startedAt: number | null = null;
  private leadInSeconds = 0;
  private mixPlan?: PlaybackPlan;
  private lastPosition = 0;
  private microphone: MediaStreamAudioSourceNode | null = null;
  private analyser: AnalyserNode | null = null;
  private clock: ReturnType<typeof setInterval> | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private endedListeners: { track: MediaStreamTrack; listener: () => void }[] = [];

  constructor(private callbacks: CaptureCallbacks) {}

  get moment(): PlaybackMoment | undefined {
    if (this.mixPlan && this.context && this.startedAt !== null) {
      return planMomentAt(this.mixPlan, this.context.currentTime - this.startedAt);
    }
    return undefined;
  }

  get sourceTime(): number {
    const moment = this.moment;
    if (moment) return moment.sourceTime;
    return this.context && this.startedAt !== null
      ? planSourceTime({ ranges: this.ranges, leadInSeconds: this.leadInSeconds }, this.context.currentTime - this.startedAt) : this.lastPosition;
  }

  async start(options: CaptureOptions): Promise<void> {
    if (this.disposed || this.busy) return;
    this.busy = true;
    this.stopping = false;
    const generation = ++this.generation;
    this.abort = new AbortController();
    const isCurrent = () => !this.disposed && generation === this.generation;

    try {
      const { start, end } = options.range;
      if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) {
        throw new Error("请先选择有效的录制起止时间。");
      }
      const ranges = (options.ranges ?? [options.range]).map(range => ({ ...range }));
      if (!validRanges(ranges, end) || ranges[0].start !== start || ranges.at(-1)!.end !== end) throw new Error("请重新选择有效的歌词或录制片段。");
      this.ranges = ranges;
      const leadInSeconds = options.leadInSeconds ?? 0;
      if (!Number.isFinite(leadInSeconds) || leadInSeconds < 0 || leadInSeconds > 3 || (leadInSeconds > 0 && start !== 0)) throw new Error("准备时间无效，请重新选择片段。");
      const { mixPlan } = options;
      if (mixPlan && (leadInSeconds || !Number.isFinite(mixPlan.totalSeconds) || mixPlan.totalSeconds <= 0 ||
        mixPlan.ranges.length !== ranges.length ||
        mixPlan.ranges.some((item, index) => item.start !== ranges[index].start || item.end !== ranges[index].end))) {
        throw new Error("混音时间无效，请重新选择片段。");
      }
      this.mixPlan = mixPlan;
      this.leadInSeconds = leadInSeconds;
      const positionPlan = mixPlan ?? { ranges, leadInSeconds };
      const publishPosition = (elapsed: number) => {
        if (mixPlan) {
          const moment = planMomentAt(mixPlan, elapsed);
          this.callbacks.onPosition?.(moment.sourceTime, moment);
        } else this.callbacks.onPosition?.(planSourceTime(positionPlan, elapsed));
      };
      this.lastPosition = planSourceTime(positionPlan, 0);
      this.startedAt = null;
      const totalDuration = mixPlan?.totalSeconds ?? selectionDuration(ranges) + leadInSeconds;
      const backingRanges = mixPlan ? [{ start: 0, end: totalDuration }] : ranges;
      if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined" || typeof MediaRecorder === "undefined") {
        throw new Error("当前环境不支持媒体录制，请使用支持 Web Audio 和 MediaRecorder 的浏览器或桌面版本。");
      }
      const candidates = options.video
        ? ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"]
        : ["audio/webm;codecs=opus", "audio/webm"];
      const codec = candidates.find(candidate => MediaRecorder.isTypeSupported?.(candidate));
      if (!codec) throw new Error("当前环境无法录制 WebM，请换用支持 WebM 录制的浏览器或桌面版本。");

      // Resume while the initiating click is still active. Only backing is monitored.
      const context = new AudioContext();
      this.context = context;
      const resumed = context.resume().then(() => null, (error: unknown) => error);
      const input = options.inputStream ?? await navigator.mediaDevices.getUserMedia(captureConstraints(options.video, options.cameraDeviceId, options.microphoneDeviceId));
      if (!isCurrent()) {
        input.getTracks().forEach(track => track.stop());
        return;
      }
      this.input = input;
      if (!input.getAudioTracks().length || (options.video && !input.getVideoTracks().length)) {
        throw new Error("所选设备未提供音频或画面，请检查设备后重试。");
      }
      for (const track of input.getTracks()) {
        if (track.readyState === "ended") throw new Error("录制设备已断开，请重新连接后打开预览。");
        const listener = () => this.fail("录制设备已断开。请重新连接麦克风或摄像头后重试。", generation);
        track.addEventListener("ended", listener);
        this.endedListeners.push({ track, listener });
      }
      const resumeError = await resumed;
      if (!isCurrent()) return;
      if (resumeError) throw resumeError;
      if (!input.getAudioTracks().length || (options.video && !input.getVideoTracks().length)) {
        throw new Error("所选设备未提供音频或画面，请检查设备后重试。");
      }

      const destination = context.createMediaStreamDestination();
      this.mixed = new MediaStream(destination.stream.getAudioTracks());
      let videoTracks = options.video ? input.getVideoTracks() : [];
      if (options.video && options.composeVideo) {
        const composed = await options.composeVideo(input);
        if (!isCurrent()) { composed.getTracks().forEach(track => track.stop()); return; }
        videoTracks = composed.getVideoTracks();
        if (!videoTracks.length) throw new Error("歌词画面未能生成，请重新预览后录制。");
      }
      this.mixed = new MediaStream([...destination.stream.getAudioTracks(), ...videoTracks]);
      this.microphone = context.createMediaStreamSource(input);
      this.analyser = context.createAnalyser();
      this.analyser.fftSize = 256;
      this.microphone.connect(this.analyser);
      this.analyser.connect(destination);

      if (options.loadBacking) {
        const encoded = await options.loadBacking(this.abort!.signal);
        if (!isCurrent()) return;
        const buffer = await context.decodeAudioData(encoded);
        if (!isCurrent()) return;
        if (mixPlan ? Math.abs(buffer.duration - totalDuration) > 0.05 : start >= buffer.duration || end > buffer.duration + 0.05) {
          throw new Error("所选片段超出伴奏时长，请重新选择录制起止时间。");
        }
        this.backing = backingRanges.map(() => {
          const source = context.createBufferSource();
          source.buffer = buffer;
          source.connect(destination);
          source.connect(context.destination);
          return source;
        });
      }

      const recorder = new MediaRecorder(this.mixed, { mimeType: codec });
      this.recorder = recorder;
      const chunks: Blob[] = [];
      const mimeType = options.video ? "video/webm" : "audio/webm";
      recorder.ondataavailable = event => {
        if (isCurrent() && event.data.size) chunks.push(event.data);
      };
      recorder.onerror = () => this.fail("录制过程中发生错误。请检查设备与权限后重新录制。", generation);
      recorder.onstop = () => {
        if (!isCurrent()) return;
        const blob = new Blob(chunks, { type: mimeType });
        this.release();
        if (blob.size) this.callbacks.onFinished({ blob, mimeType });
        else this.callbacks.onError("没有采集到可回放的录制，请检查麦克风后重试。");
      };
      recorder.onstart = () => {
        if (!isCurrent() || this.stopping) return;
        try {
          // Source offsets are exact requests, not a promise of latency compensation.
          this.startedAt = context.currentTime;
          let offset = leadInSeconds;
          this.backing.forEach((source, index) => {
            const range = backingRanges[index];
            source.start(this.startedAt! + offset, range.start, range.end - range.start);
            offset += range.end - range.start;
          });
          const finalSource = this.backing.at(-1);
          if (finalSource) finalSource.onended = () => this.stop();
          publishPosition(0);
          const samples = new Uint8Array(this.analyser!.fftSize);
          this.clock = setInterval(() => {
            this.analyser?.getByteTimeDomainData(samples);
            let energy = 0;
            for (const sample of samples) energy += ((sample - 128) / 128) ** 2;
            const elapsed = Math.max(0, context.currentTime - this.startedAt!);
            this.callbacks.onLevel(Math.min(1, Math.sqrt(energy / samples.length)), elapsed);
            publishPosition(elapsed);
          }, 100);
          this.deadline = setTimeout(() => this.stop(), totalDuration * 1000);
          this.callbacks.onRecording(input);
        } catch (error) {
          this.fail(recordingError(error), generation);
        }
      };
      recorder.start(250);
    } catch (error) {
      if (isCurrent()) this.fail(recordingError(error), generation);
    }
  }

  stop(): void {
    this.lastPosition = this.sourceTime;
    this.stopping = true;
    if (this.recorder && this.recorder.state !== "inactive") {
      try {
        this.recorder.stop();
      } catch {
        this.fail("无法正常结束录制，请检查设备后重试。", this.generation);
      }
      // Release devices immediately; the recorder's final data/stop events may follow.
      this.releaseCapture();
    } else if (this.busy) {
      ++this.generation;
      this.release();
    }
  }

  dispose(): void {
    this.disposed = true;
    ++this.generation;
    this.release();
  }

  private fail(message: string, generation: number): void {
    if (this.disposed || generation !== this.generation) return;
    ++this.generation;
    this.release();
    this.callbacks.onError(message);
  }

  private releaseCapture(): void {
    if (this.clock !== null) clearInterval(this.clock);
    if (this.deadline !== null) clearTimeout(this.deadline);
    this.clock = null;
    this.deadline = null;
    for (const { track, listener } of this.endedListeners) track.removeEventListener("ended", listener);
    this.endedListeners = [];
    for (const source of new Set(this.backing)) {
      source.onended = null;
      try { source.stop(); } catch { /* A source may not have started yet. */ }
      source.disconnect();
    }
    this.backing = [];
    this.microphone?.disconnect();
    this.microphone = null;
    this.analyser?.disconnect();
    this.analyser = null;
    const tracks = new Set([...(this.input?.getTracks() ?? []), ...(this.mixed?.getTracks() ?? [])]);
    tracks.forEach(track => track.stop());
    this.input = null;
    this.mixed = null;
    this.startedAt = null;
    if (this.context) {
      void this.context.close().catch(() => {});
      this.context = null;
    }
  }

  private release(): void {
    this.abort?.abort();
    this.abort = null;
    if (this.recorder) {
      this.recorder.onstart = null;
      this.recorder.ondataavailable = null;
      this.recorder.onstop = null;
      this.recorder.onerror = null;
      if (this.recorder.state !== "inactive") {
        try { this.recorder.stop(); } catch { /* Device may have already ended. */ }
      }
      this.recorder = null;
    }
    this.releaseCapture();
    this.busy = false;
  }
}

function recordingError(error: unknown): string {
  if (error instanceof DOMException) {
    switch (error.name) {
      case "NotAllowedError": case "SecurityError":
        return "未获得麦克风或摄像头权限。请在系统或浏览器设置中允许访问，再点击开始录制。";
      case "NotFoundError": case "OverconstrainedError":
        return "找不到可用的录制设备。请连接麦克风或关闭摄像头选项后重试。";
      case "NotReadableError":
        return "设备可能正被其他程序占用。请关闭占用麦克风或摄像头的程序后重试。";
      case "AbortError":
        return "设备启动已中断。请检查设备连接，再点击开始录制。";
    }
  }
  if (error instanceof Error && /[\u4e00-\u9fff]/.test(error.message)) return error.message;
  return "无法开始录制。请检查伴奏文件、设备连接及权限后重试。";
}
