import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserRecorder } from "./browserRecorder";
import { playbackPlan } from "./playbackPlan";

class TestTrack extends EventTarget {
  kind: string;
  readyState = "live";
  stop = vi.fn();
  constructor(kind: string) { super(); this.kind = kind; }
}

class TestStream {
  constructor(private tracks: TestTrack[] = []) {}
  getTracks() { return this.tracks; }
  getAudioTracks() { return this.tracks.filter(track => track.kind === "audio"); }
  getVideoTracks() { return this.tracks.filter(track => track.kind === "video"); }
}

const contextInstances: TestContext[] = [];
const recorders: TestRecorder[] = [];
const mixedTrack = new TestTrack("audio");
const source = { connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null as (() => void) | null, buffer: null };

class TestContext {
  state = "running";
  currentTime = 0;
  destination = {};
  close = vi.fn(async () => { this.state = "closed"; });
  resume = vi.fn(async () => {});
  decodeAudioData = vi.fn(async () => ({ duration: 20 }));
  createMediaStreamDestination = vi.fn(() => ({ stream: new TestStream([mixedTrack]) }));
  createMediaStreamSource = vi.fn(() => ({ connect: vi.fn(), disconnect: vi.fn() }));
  createBufferSource = vi.fn(() => source);
  createAnalyser = vi.fn(() => ({ fftSize: 128, getByteTimeDomainData: (data: Uint8Array) => data.fill(128), connect: vi.fn(), disconnect: vi.fn() }));
  constructor() { contextInstances.push(this); }
}

class TestRecorder {
  static isTypeSupported = vi.fn((mime: string) => mime.includes("webm"));
  state = "inactive";
  mimeType: string;
  onstart: (() => void) | null = null;
  onstop: (() => void) | null = null;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  start = vi.fn(() => { this.state = "recording"; this.onstart?.(); });
  stop = vi.fn(() => {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["real-mocked-media"], { type: this.mimeType }) });
    this.onstop?.();
  });
  constructor(public stream: TestStream, options: { mimeType: string }) {
    this.mimeType = options.mimeType;
    recorders.push(this);
  }
}

const getUserMedia = vi.fn();
const finished = vi.fn();
const onError = vi.fn();
const onRecording = vi.fn();
const onLevel = vi.fn();
const backingBuffer = new ArrayBuffer(4);
let capture: BrowserRecorder;

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  contextInstances.length = 0;
  recorders.length = 0;
  source.onended = null;
  TestRecorder.isTypeSupported.mockImplementation(mime => mime.includes("webm"));
  vi.stubGlobal("MediaStream", TestStream);
  vi.stubGlobal("MediaRecorder", TestRecorder);
  vi.stubGlobal("AudioContext", TestContext);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia } });
  capture = new BrowserRecorder({ onFinished: finished, onError, onRecording, onLevel });
});

afterEach(() => {
  capture.dispose();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("BrowserRecorder", () => {
  it("plays one packed mix and reports preparation separately for each selected range", async () => {
    const ranges = [{ start: 4, end: 7 }, { start: 12, end: 14 }];
    const mixPlan = playbackPlan(ranges, 20, 3);
    getUserMedia.mockResolvedValue(new TestStream([new TestTrack("audio")]));
    const position = vi.fn();
    capture.dispose();
    capture = new BrowserRecorder({ onFinished: finished, onError, onRecording, onLevel, onPosition: position });
    await capture.start({ video: false, range: { start: 4, end: 14 }, ranges, mixPlan,
      loadBacking: async () => {
        contextInstances[0].decodeAudioData.mockResolvedValueOnce({ duration: 11 });
        return backingBuffer;
      } });
    expect(contextInstances[0].decodeAudioData).toHaveBeenCalledTimes(1);
    expect(source.start).toHaveBeenCalledExactlyOnceWith(0, 0, 11);
    expect(position).toHaveBeenLastCalledWith(1, expect.objectContaining({ preparing: true }));
    contextInstances[0].currentTime = 6;
    await vi.advanceTimersByTimeAsync(100);
    expect(position).toHaveBeenLastCalledWith(9, expect.objectContaining({ preparing: true, segmentIndex: 1 }));
    contextInstances[0].currentTime = 9;
    await vi.advanceTimersByTimeAsync(100);
    expect(position).toHaveBeenLastCalledWith(12, expect.objectContaining({ preparing: false }));
  });
  it("records missing introductory silence and delays backing without lighting source-zero lyrics", async () => {
    getUserMedia.mockResolvedValue(new TestStream([new TestTrack("audio")]));
    const position = vi.fn(); capture.dispose();
    capture = new BrowserRecorder({ onFinished: finished, onError, onRecording, onLevel, onPosition: position });
    await capture.start({ video: false, range: { start: 0, end: 2 }, leadInSeconds: 2, loadBacking: async () => backingBuffer });
    expect(contextInstances[0].decodeAudioData).toHaveBeenCalledWith(backingBuffer);
    expect(source.start).toHaveBeenCalledWith(2, 0, 2);
    expect(position).toHaveBeenLastCalledWith(-2);
    contextInstances[0].currentTime = 1;
    await vi.advanceTimersByTimeAsync(100);
    expect(position).toHaveBeenLastCalledWith(-1);
    contextInstances[0].currentTime = 3;
    await vi.advanceTimersByTimeAsync(100);
    expect(position).toHaveBeenLastCalledWith(1);
    await vi.advanceTimersByTimeAsync(1900);
    expect(finished).not.toHaveBeenCalled();
  });
  it("rejects an already ended preview stream before creating a recorder", async () => {
    const microphone = new TestTrack("audio"); microphone.readyState = "ended";
    getUserMedia.mockResolvedValue(new TestStream([microphone]));
    await capture.start({ video: false, range: { start: 0, end: 2 } });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/断开/));
    expect(recorders).toHaveLength(0);
  });
  it("fails a device disconnect while the backing is still loading", async () => {
    const microphone = new TestTrack("audio");
    getUserMedia.mockResolvedValue(new TestStream([microphone]));
    let resolve!: (buffer: ArrayBuffer) => void;
    const loadBacking = vi.fn(() => new Promise<ArrayBuffer>(done => { resolve = done; }));
    const pending = capture.start({ video: false, range: { start: 0, end: 2 }, loadBacking });
    await vi.waitFor(() => expect(loadBacking).toHaveBeenCalledOnce());
    microphone.dispatchEvent(new Event("ended"));
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/断开/));
    resolve(backingBuffer); await pending;
    expect(recorders).toHaveLength(0);
    expect(finished).not.toHaveBeenCalled();
  });
  it("uses selected devices and records the composed video instead of the raw camera", async () => {
    const microphone = new TestTrack("audio"), camera = new TestTrack("video"), composed = new TestTrack("video");
    getUserMedia.mockResolvedValue(new TestStream([microphone, camera]));
    const composeVideo = vi.fn(async () => new TestStream([composed]) as unknown as MediaStream);
    await capture.start({ video: true, cameraDeviceId: "camera-b", microphoneDeviceId: "mic-b", range: { start: 1, end: 3 }, composeVideo });
    expect(getUserMedia).toHaveBeenCalledWith({ audio: { deviceId: { exact: "mic-b" } }, video: { deviceId: { exact: "camera-b" } } });
    expect(recorders[0].stream.getVideoTracks()).toEqual([composed]);
    capture.stop();
    expect(camera.stop).toHaveBeenCalledOnce();
    expect(composed.stop).toHaveBeenCalledOnce();
  });
  it("schedules disjoint lyric clips on one audio clock and maps the current lyric position", async () => {
    getUserMedia.mockResolvedValue(new TestStream([new TestTrack("audio")]));
    const position = vi.fn();
    capture.dispose();
    capture = new BrowserRecorder({ onFinished: finished, onError, onRecording, onLevel, onPosition: position });
    await capture.start({ video: false, range: { start: 3, end: 9 }, ranges: [{ start: 3, end: 5 }, { start: 8, end: 9 }], loadBacking: async () => backingBuffer });
    expect(source.start).toHaveBeenNthCalledWith(1, 0, 3, 2);
    expect(source.start).toHaveBeenNthCalledWith(2, 2, 8, 1);
    contextInstances[0].currentTime = 2.25;
    await vi.advanceTimersByTimeAsync(100);
    expect(position).toHaveBeenLastCalledWith(8.25);
  });
  it("does not request devices until start and mixes selected backing after recorder starts", async () => {
    const microphone = new TestTrack("audio");
    const camera = new TestTrack("video");
    getUserMedia.mockResolvedValue(new TestStream([microphone, camera]));
    expect(getUserMedia).not.toHaveBeenCalled();
    await capture.start({ video: true, range: { start: 3, end: 5 }, loadBacking: async () => backingBuffer });
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true, video: true });
    expect(recorders[0].stream.getTracks()).toEqual([mixedTrack, camera]);
    expect(source.start).toHaveBeenCalledWith(0, 3, 2);
    expect(recorders[0].start.mock.invocationCallOrder[0]).toBeLessThan(source.start.mock.invocationCallOrder[0]);
    expect(onRecording).toHaveBeenCalledWith(expect.objectContaining({ getVideoTracks: expect.any(Function) }));
    await vi.advanceTimersByTimeAsync(2000);
    expect(finished).toHaveBeenCalledWith(expect.objectContaining({ blob: expect.any(Blob), mimeType: "video/webm" }));
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(camera.stop).toHaveBeenCalledOnce();
    expect(mixedTrack.stop).toHaveBeenCalledOnce();
    expect(contextInstances[0].close).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("releases a permission stream resolving after disposal and never begins recording", async () => {
    let resolve!: (stream: TestStream) => void;
    getUserMedia.mockReturnValue(new Promise<TestStream>(done => { resolve = done; }));
    const pending = capture.start({ video: false, range: { start: 0, end: 2 } });
    capture.dispose();
    const microphone = new TestTrack("audio");
    resolve(new TestStream([microphone]));
    await pending;
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(recorders).toHaveLength(0);
    expect(onError).not.toHaveBeenCalled();
    expect(contextInstances[0].close).toHaveBeenCalledOnce();
  });

  it("rejects unsupported WebM before asking for device permission", async () => {
    TestRecorder.isTypeSupported.mockReturnValue(false);
    await capture.start({ video: false, range: { start: 0, end: 1 } });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/WebM/));
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("shows permission denial and releases the audio context", async () => {
    getUserMedia.mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    await capture.start({ video: false, range: { start: 0, end: 1 } });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/权限/));
    expect(finished).not.toHaveBeenCalled();
    expect(contextInstances[0].close).toHaveBeenCalledOnce();
  });

  it("does not create a fake result after a capture device disconnects", async () => {
    const microphone = new TestTrack("audio");
    getUserMedia.mockResolvedValue(new TestStream([microphone]));
    await capture.start({ video: false, range: { start: 0, end: 2 } });
    microphone.dispatchEvent(new Event("ended"));
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/断开/));
    expect(finished).not.toHaveBeenCalled();
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts backing reads and releases devices when preparation is canceled", async () => {
    const microphone = new TestTrack("audio");
    getUserMedia.mockResolvedValue(new TestStream([microphone]));
    let readSignal: AbortSignal | undefined;
    let resolve!: (buffer: ArrayBuffer) => void;
    const loadBacking = vi.fn((signal: AbortSignal) => {
      readSignal = signal;
      return new Promise<ArrayBuffer>(done => { resolve = done; });
    });
    const pending = capture.start({ video: false, range: { start: 0, end: 2 }, loadBacking });
    await vi.waitFor(() => expect(loadBacking).toHaveBeenCalledOnce());
    capture.stop();
    expect(readSignal?.aborted).toBe(true);
    expect(microphone.stop).toHaveBeenCalledOnce();
    resolve(backingBuffer);
    await pending;
    expect(recorders).toHaveLength(0);
    expect(finished).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it("stops a requested video take without backing and returns audio and camera media", async () => {
    const microphone = new TestTrack("audio");
    const camera = new TestTrack("video");
    getUserMedia.mockResolvedValue(new TestStream([microphone, camera]));
    await capture.start({ video: true, range: { start: 4, end: 7 } });
    capture.stop();
    expect(finished).toHaveBeenCalledOnce();
    expect(finished.mock.calls[0][0].blob.size).toBeGreaterThan(0);
    expect(source.start).not.toHaveBeenCalled();
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(camera.stop).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("releases devices and emits no usable take when backing decoding fails", async () => {
    const microphone = new TestTrack("audio");
    getUserMedia.mockResolvedValue(new TestStream([microphone]));
    const loadBacking = vi.fn(async () => { throw new DOMException("bad", "EncodingError"); });
    await capture.start({ video: false, range: { start: 0, end: 2 }, loadBacking });
    expect(onError).toHaveBeenCalledWith(expect.stringMatching(/伴奏文件/));
    expect(microphone.stop).toHaveBeenCalledOnce();
    expect(finished).not.toHaveBeenCalled();
  });
});
