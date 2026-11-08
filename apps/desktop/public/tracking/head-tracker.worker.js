/* CoverLab local head tracking. MediaPipe runtime: 0.10.32, Apache-2.0.
 * Provenance and hashes: docs/licenses/mediapipe/PROVENANCE.md.
 * Classic worker is intentional: Emscripten's WASM loader uses importScripts. */
"use strict";

const assetRoot = new URL("./", self.location.href);
const nativeFetch = self.fetch.bind(self);
const nativeImportScripts = self.importScripts.bind(self);
function localAsset(value) {
  const url = new URL(value, assetRoot);
  if (url.origin !== assetRoot.origin || !url.pathname.startsWith(assetRoot.pathname)) {
    throw new Error("Head tracking may only load its bundled assets.");
  }
  return url.href;
}
// Contain the unmodified third-party runtime: neither frames nor metrics can
// leave this worker through network APIs, including when upstream code changes.
self.fetch = (input, options) => nativeFetch(input instanceof Request ? (localAsset(input.url), input) : localAsset(input), options);
self.importScripts = (...urls) => nativeImportScripts(...urls.map(localAsset));
const NativeXHR = self.XMLHttpRequest;
if (NativeXHR) self.XMLHttpRequest = class extends NativeXHR {
  open(method, url, ...args) { return super.open(method, localAsset(url), ...args); }
};
for (const transport of ["WebSocket", "EventSource"]) {
  if (self[transport]) self[transport] = class { constructor() { throw new Error("Network transport is disabled for head tracking."); } };
}

let landmarker = null;
let initializing = false;
let lastTimestamp = -1;
async function initialize() {
  if (landmarker || initializing) return;
  initializing = true;
  try {
    // The npm CJS bundle only needs `exports`; it does not require Node APIs.
    self.exports = {};
    self.importScripts(localAsset("./vendor/vision_bundle.js"));
    const { FaceLandmarker, FilesetResolver } = self.exports;
    const fileset = await FilesetResolver.forVisionTasks(localAsset("./vendor/wasm"));
    landmarker = await FaceLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: localAsset("./face_landmarker.task"), delegate: "CPU" },
      runningMode: "VIDEO",
      numFaces: 1,
      minFaceDetectionConfidence: .6,
      minFacePresenceConfidence: .6,
      minTrackingConfidence: .6,
      outputFaceBlendshapes: false,
      outputFacialTransformationMatrixes: false,
    });
    self.postMessage({ type: "ready" });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "Tracking initialization failed." });
  }
}

self.onmessage = event => {
  const message = event.data;
  if (message.type === "init") { void initialize(); return; }
  if (message.type !== "frame") return;
  const bitmap = message.bitmap;
  try {
    if (!landmarker) throw new Error("Tracking model is not initialized.");
    const timestamp = Math.max(message.timestamp, lastTimestamp + .01);
    lastTimestamp = timestamp;
    const result = landmarker.detectForVideo(bitmap, timestamp);
    const face = result.faceLandmarks[0];
    // Geometry is tiny and tested on the main thread; all ML stays in this worker.
    self.postMessage({ type: "result", id: message.id, landmarks: face ? face.map(point => ({ x: point.x, y: point.y })) : null });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "Tracking inference failed." });
  } finally { bitmap?.close(); }
};
