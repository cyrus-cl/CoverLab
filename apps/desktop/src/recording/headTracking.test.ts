import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HeadTracker, headPoseFromLandmarks, mapHeadPose, smoothHeadPose, type HeadPose } from "./headTracking";

const pose: HeadPose = { cx: .25, cy: .4, width: .2, height: .3, rotation: .3 };
const faceLandmarks = Array.from({ length: 478 }, () => ({ x: .5, y: .5 }));
faceLandmarks[33] = { x: .4, y: .5 };
faceLandmarks[263] = { x: .6, y: .5 };
faceLandmarks[10] = { x: .5, y: .2 };
faceLandmarks[152] = { x: .5, y: .8 };
faceLandmarks[234] = { x: .3, y: .5 };
faceLandmarks[454] = { x: .7, y: .5 };

describe("head pose geometry", () => {
  it("maps through the same center-cover crop as the portrait renderer", () => {
    const result = mapHeadPose(pose, 1920, 1080, 640, 720, 1, false);
    expect(result.cx).toBeCloseTo(0);
    expect(result.cy).toBeCloseTo(288);
    expect(result.width).toBeCloseTo(256);
    expect(result.height).toBeCloseTo(216);
    expect(result.rotation).toBe(.3);
  });
  it("applies zoom about the portrait center and mirrors x and roll", () => {
    const original = mapHeadPose(pose, 1280, 720, 640, 720, 1.5, false);
    const mirrored = mapHeadPose(pose, 1280, 720, 640, 720, 1.5, true);
    expect(original.cx).toBeCloseTo(-160);
    expect(original.cy).toBeCloseTo(252);
    expect(mirrored.cx).toBeCloseTo(800);
    expect(mirrored.cy).toBe(original.cy);
    expect(mirrored.rotation).toBe(-.3);
    expect(mirrored.width).toBeCloseTo(384);
  });
  it("handles tall source video and rejects invalid geometry", () => {
    expect(mapHeadPose({ ...pose, cx: .5, cy: .5 }, 720, 1280, 640, 720, 1, false).cx).toBe(320);
    expect(() => mapHeadPose(pose, 0, 720, 640, 720, 1, false)).toThrow();
  });
  it("extracts roll in pixel space rather than distorted normalized axes", () => {
    const landmarks = Array.from({ length: 478 }, () => ({ x: .5, y: .5 }));
    landmarks[33] = { x: .4, y: .4 };
    landmarks[263] = { x: .6, y: .6 };
    landmarks[10] = { x: .5, y: .2 };
    landmarks[152] = { x: .5, y: .8 };
    landmarks[234] = { x: .3, y: .5 };
    landmarks[454] = { x: .7, y: .5 };
    const result = headPoseFromLandmarks(landmarks, 1280, 720);
    expect(result).not.toBeNull();
    expect(result!.rotation).toBeCloseTo(Math.atan2(144, 256));
    expect(result!.cx).toBeCloseTo(.5);
    expect(result!.cy).toBeCloseTo(.5);
    expect(result!.width).toBeGreaterThan(.3);
  });
  it("rejects malformed and tiny detections", () => {
    expect(headPoseFromLandmarks([], 1280, 720)).toBeNull();
    expect(headPoseFromLandmarks(Array.from({ length: 478 }, () => ({ x: .5, y: .5 })), 1280, 720)).toBeNull();
  });
  it("follows motion promptly, expands immediately and shrinks gradually", () => {
    const next = { ...pose, cx: .3, width: .3, height: .2 };
    const result = smoothHeadPose(pose, next, 84);
    expect(result.cx).toBeGreaterThan(.29);
    expect(result.cx).toBeLessThanOrEqual(.3);
    expect(result.width).toBe(.3);
    expect(result.height).toBeGreaterThan(.2);
    expect(result.height).toBeLessThan(.3);
  });
  it("takes the shortest roll path and snaps large movements", () => {
    const result = smoothHeadPose({ ...pose, rotation: Math.PI - .02 }, { ...pose, rotation: -Math.PI + .02 }, 84);
    expect(Math.abs(result.rotation)).toBeGreaterThan(3);
    expect(smoothHeadPose(pose, { ...pose, cx: .9 }, 84).cx).toBe(.9);
  });
});

class FakeWorker {
  static current: FakeWorker;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessageerror: ((event: Event) => void) | null = null;
  postMessage = vi.fn();
  terminate = vi.fn();
  constructor() { FakeWorker.current = this; }
  emit(data: unknown) { this.onmessage?.({ data } as MessageEvent); }
}
const video = { readyState: 2, videoWidth: 1280, videoHeight: 720, currentTime: 1 } as HTMLVideoElement;
const flush = () => new Promise<void>(resolve => queueMicrotask(resolve));

describe("HeadTracker lifecycle", () => {
  beforeEach(() => {
    vi.stubGlobal("Worker", FakeWorker);
    vi.stubGlobal("createImageBitmap", vi.fn(async () => ({ close: vi.fn(), width: 1280, height: 720 })));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("waits for initialization, limits work to one frame and throttles to 12Hz", async () => {
    const tracker = new HeadTracker();
    tracker.update(video, 100);
    expect(createImageBitmap).not.toHaveBeenCalled();
    FakeWorker.current.emit({ type: "ready" });
    tracker.update(video, 100);
    tracker.update(video, 110);
    await flush();
    expect(createImageBitmap).toHaveBeenCalledTimes(1);
    const request = FakeWorker.current.postMessage.mock.calls.find(call => call[0].type === "frame")![0];
    FakeWorker.current.emit({ type: "result", id: request.id, timestamp: request.timestamp, landmarks: faceLandmarks });
    tracker.update({ ...video, currentTime: 2 } as HTMLVideoElement, 150);
    expect(createImageBitmap).toHaveBeenCalledTimes(1);
    tracker.update({ ...video, currentTime: 2 } as HTMLVideoElement, 185);
    await flush();
    expect(createImageBitmap).toHaveBeenCalledTimes(2);
    tracker.dispose();
  });

  it("expires the captured frame rather than the result arrival time", async () => {
    const statuses: string[] = [];
    const tracker = new HeadTracker(status => statuses.push(status));
    FakeWorker.current.emit({ type: "ready" });
    tracker.update(video, 100);
    await flush();
    const request = FakeWorker.current.postMessage.mock.calls.find(call => call[0].type === "frame")![0];
    FakeWorker.current.emit({ type: "result", id: request.id, timestamp: request.timestamp, landmarks: faceLandmarks });
    expect(tracker.getPose(349)).not.toBeNull();
    expect(tracker.getPose(351)).toBeNull();
    expect(statuses.at(-1)).toBe("lost");
    tracker.dispose();
  });

  it("immediately covers a negative detection and reports worker errors", async () => {
    const statuses: string[] = [];
    const tracker = new HeadTracker(status => statuses.push(status));
    FakeWorker.current.emit({ type: "ready" });
    tracker.update(video, 100);
    await flush();
    const request = FakeWorker.current.postMessage.mock.calls.find(call => call[0].type === "frame")![0];
    FakeWorker.current.emit({ type: "result", id: request.id, timestamp: request.timestamp, landmarks: null });
    expect(tracker.getPose(110)).toBeNull();
    expect(statuses.at(-1)).toBe("lost");
    FakeWorker.current.emit({ type: "error" });
    expect(statuses.at(-1)).toBe("error");
    tracker.dispose();
  });

  it("closes a bitmap resolving after disposal without posting to a dead worker", async () => {
    let resolveBitmap!: (bitmap: ImageBitmap) => void;
    vi.stubGlobal("createImageBitmap", vi.fn(() => new Promise<ImageBitmap>(resolve => { resolveBitmap = resolve; })));
    const tracker = new HeadTracker();
    const worker = FakeWorker.current;
    worker.emit({ type: "ready" });
    tracker.update(video, 100);
    tracker.dispose();
    const bitmap = { close: vi.fn() } as unknown as ImageBitmap;
    resolveBitmap(bitmap);
    await flush();
    expect(bitmap.close).toHaveBeenCalledOnce();
    expect(worker.postMessage.mock.calls.filter(call => call[0].type === "frame")).toHaveLength(0);
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(tracker.getPose(110)).toBeNull();
  });

  it("fails closed when bitmap capture rejects", async () => {
    vi.stubGlobal("createImageBitmap", vi.fn().mockRejectedValue(new Error("device closed")));
    const statuses: string[] = [];
    const tracker = new HeadTracker(status => statuses.push(status));
    FakeWorker.current.emit({ type: "ready" });
    tracker.update(video, 100);
    await flush();
    expect(tracker.getPose(101)).toBeNull();
    expect(statuses.at(-1)).toBe("error");
    tracker.dispose();
  });

  it("fails closed when capture throws synchronously", () => {
    vi.stubGlobal("createImageBitmap", vi.fn(() => { throw new TypeError("invalid video"); }));
    const statuses: string[] = [];
    const tracker = new HeadTracker(status => statuses.push(status));
    FakeWorker.current.emit({ type: "ready" });
    expect(() => tracker.update(video, 100)).not.toThrow();
    expect(tracker.getPose(101)).toBeNull();
    expect(statuses.at(-1)).toBe("error");
    tracker.dispose();
  });
});
