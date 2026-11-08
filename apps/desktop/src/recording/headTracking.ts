export type HeadTrackingStatus = "loading" | "tracking" | "lost" | "error";
export type HeadPose = { cx: number; cy: number; width: number; height: number; rotation: number };
const FRAME_INTERVAL_MS = 1000 / 12;
const MAX_POSE_AGE_MS = 250;
const FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109];

/** Input is raw, unmirrored video coordinates. Output is portrait-canvas pixels. */
export function mapHeadPose(pose: HeadPose, videoWidth: number, videoHeight: number, canvasWidth: number, canvasHeight: number, zoom: number, mirror: boolean): HeadPose {
  if (![videoWidth, videoHeight, canvasWidth, canvasHeight, zoom].every(value => Number.isFinite(value) && value > 0)) {
    throw new RangeError("Head pose mapping requires positive finite dimensions and zoom.");
  }
  const scale = Math.max(canvasWidth / videoWidth, canvasHeight / videoHeight) * zoom;
  const cx = canvasWidth / 2 + (pose.cx - .5) * videoWidth * scale;
  return {
    cx: mirror ? canvasWidth - cx : cx,
    cy: canvasHeight / 2 + (pose.cy - .5) * videoHeight * scale,
    width: pose.width * videoWidth * scale,
    height: pose.height * videoHeight * scale,
    rotation: mirror ? -pose.rotation : pose.rotation,
  };
}

/** Fit a rotated face oval, preserving pixel aspect ratio when measuring roll. */
export function headPoseFromLandmarks(landmarks: readonly { x: number; y: number }[], width: number, height: number): HeadPose | null {
  if (landmarks.length < 455 || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  const left = landmarks[33], right = landmarks[263];
  if (!left || !right) return null;
  const dx = (right.x - left.x) * width, dy = (right.y - left.y) * height;
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || Math.hypot(dx, dy) < width * .015) return null;
  const rotation = Math.atan2(dy, dx);
  const cosine = Math.cos(rotation), sine = Math.sin(rotation);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const index of FACE_OVAL) {
    const point = landmarks[index];
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
    const x = point.x * width, y = point.y * height;
    const localX = x * cosine + y * sine, localY = -x * sine + y * cosine;
    minX = Math.min(minX, localX); maxX = Math.max(maxX, localX);
    minY = Math.min(minY, localY); maxY = Math.max(maxY, localY);
  }
  const localX = (minX + maxX) / 2, localY = (minY + maxY) / 2;
  const pose = {
    cx: (localX * cosine - localY * sine) / width,
    cy: (localX * sine + localY * cosine) / height,
    width: (maxX - minX) / width,
    height: (maxY - minY) / height,
    rotation,
  };
  return pose.width >= .025 && pose.height >= .025 && pose.width < 2 && pose.height < 2 ? pose : null;
}

/** Fast center response; grow immediately so smoothing never delays a larger cover. */
export function smoothHeadPose(previous: HeadPose, next: HeadPose, elapsedMs: number): HeadPose {
  if (elapsedMs > MAX_POSE_AGE_MS || Math.hypot(next.cx - previous.cx, next.cy - previous.cy) > Math.max(previous.width, previous.height) * .65) return next;
  const alpha = 1 - Math.exp(-Math.max(1, elapsedMs) / 32);
  const shrinkAlpha = 1 - Math.exp(-Math.max(1, elapsedMs) / 120);
  const rollDelta = Math.atan2(Math.sin(next.rotation - previous.rotation), Math.cos(next.rotation - previous.rotation));
  return {
    cx: previous.cx + (next.cx - previous.cx) * alpha,
    cy: previous.cy + (next.cy - previous.cy) * alpha,
    width: next.width >= previous.width ? next.width : previous.width + (next.width - previous.width) * shrinkAlpha,
    height: next.height >= previous.height ? next.height : previous.height + (next.height - previous.height) * shrinkAlpha,
    rotation: Math.atan2(Math.sin(previous.rotation + rollDelta * alpha), Math.cos(previous.rotation + rollDelta * alpha)),
  };
}

type PendingFrame = { id: number; capturedAt: number; width: number; height: number };
type WorkerMessage = { type: "ready" | "result" | "error"; id?: number; landmarks?: { x: number; y: number }[] | null };

/** Lazy-owned by the paper-bag effect. No frame queue, camera ownership, or network service. */
export class HeadTracker {
  private worker: Worker | null = null;
  private disposed = false;
  private ready = false;
  private status: HeadTrackingStatus = "loading";
  private pending: PendingFrame | null = null;
  private pose: HeadPose | null = null;
  private poseAt = -Infinity;
  private lastCaptureAt = -Infinity;
  private lastVideoTime = -Infinity;
  private nextId = 0;
  private loadTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(private onStatus?: (status: HeadTrackingStatus) => void) {
    this.onStatus?.("loading");
    try {
      if (typeof Worker === "undefined" || typeof createImageBitmap === "undefined") throw new Error("Head tracking is unavailable.");
      const url = new URL(`${import.meta.env.BASE_URL}tracking/head-tracker.worker.js`, window.location.href);
      this.worker = new Worker(url, { name: "coverlab-head-tracking" });
      this.worker.onmessage = event => this.handleMessage(event.data as WorkerMessage);
      this.worker.onerror = () => this.fail();
      this.worker.onmessageerror = () => this.fail();
      this.loadTimer = setTimeout(() => this.fail(), 20000);
      this.worker.postMessage({ type: "init" });
    } catch { this.fail(); }
  }

  update(video: HTMLVideoElement, now: number): void {
    if (this.disposed || !this.ready || !this.worker || this.pending || !Number.isFinite(now)) return;
    this.getPose(now);
    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight || video.currentTime === this.lastVideoTime || now - this.lastCaptureAt < FRAME_INTERVAL_MS) return;
    const pending = { id: ++this.nextId, capturedAt: now, width: video.videoWidth, height: video.videoHeight };
    this.pending = pending;
    this.lastCaptureAt = now;
    this.lastVideoTime = video.currentTime;
    // Reserve the slot before the promise starts: RAF cannot accumulate captures.
    let capture: Promise<ImageBitmap>;
    try { capture = createImageBitmap(video); } catch { this.fail(); return; }
    void capture.then(bitmap => {
      if (this.disposed || !this.ready || !this.worker || this.pending !== pending) { bitmap.close(); return; }
      try {
        this.worker.postMessage({ type: "frame", id: pending.id, timestamp: pending.capturedAt, bitmap }, [bitmap]);
      } catch { bitmap.close(); this.fail(); }
    }).catch(() => { if (!this.disposed) this.fail(); });
  }

  getPose(now: number): HeadPose | null {
    if (this.disposed || this.status === "error") return null;
    if (this.pose && (!Number.isFinite(now) || now < this.poseAt || now - this.poseAt > MAX_POSE_AGE_MS)) {
      this.pose = null;
      this.setStatus("lost");
    }
    return this.pose;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    clearTimeout(this.loadTimer);
    this.pose = null;
    this.pending = null;
    this.ready = false;
    if (this.worker) {
      this.worker.onmessage = null;
      this.worker.onerror = null;
      this.worker.onmessageerror = null;
      // Termination releases WASM and any transferred frame immediately, even mid-inference.
      this.worker.terminate();
      this.worker = null;
    }
  }

  private handleMessage(message: WorkerMessage): void {
    if (this.disposed || this.status === "error") return;
    if (message.type === "error") { this.fail(); return; }
    if (message.type === "ready") { clearTimeout(this.loadTimer); this.ready = true; this.setStatus("lost"); return; }
    if (message.type !== "result" || !this.pending || message.id !== this.pending.id) return;
    const pending = this.pending;
    this.pending = null;
    const next = message.landmarks ? headPoseFromLandmarks(message.landmarks, pending.width, pending.height) : null;
    if (!next) { this.pose = null; this.setStatus("lost"); return; }
    this.pose = this.pose ? smoothHeadPose(this.pose, next, pending.capturedAt - this.poseAt) : next;
    this.poseAt = pending.capturedAt;
    this.setStatus("tracking");
  }

  private setStatus(status: HeadTrackingStatus): void {
    if (this.status !== status) { this.status = status; this.onStatus?.(status); }
  }

  private fail(): void {
    if (this.disposed) return;
    clearTimeout(this.loadTimer);
    this.ready = false;
    this.pose = null;
    this.pending = null;
    this.worker?.terminate();
    this.worker = null;
    this.setStatus("error");
  }
}
