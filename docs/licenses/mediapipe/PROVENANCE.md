# MediaPipe head tracking assets

Retrieved and verified 2026-11-03. These assets ship with the desktop frontend;
enabling the paper-bag effect loads them from the application's own origin.
There is no runtime CDN, model download service, or audio backend dependency.

## Runtime

- Package: `@mediapipe/tasks-vision@0.10.32` (pinned stable release).
- [Official npm tarball](https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-0.10.32.tgz).
- npm integrity: `sha512-3tiAZnmKloYnRXYoO3dKltTUGnqeCwzC4lV03uY0vCsE+aveJTyEVQyZHOlQGQNsjK+gRHzkf9q08C99Qm2K0Q==`.
- Tarball SHA-256: `b362a2b56aafcce7857047c25dc8fd69612fd8e75018e264717d24290e7815c1`.
- `vision_bundle.cjs` was copied byte-for-byte to
  `apps/desktop/public/tracking/vendor/vision_bundle.js`. Only the filename changed.
- Both original SIMD and no-SIMD JavaScript/WASM pairs are included under
  `vendor/wasm`; `FilesetResolver` chooses the supported pair.
- [Runtime source tag](https://github.com/google-ai-edge/mediapipe/tree/v0.10.32).
  npm declares `Apache-2.0`; the exact upstream tag's full LICENSE is stored here
  and at `public/tracking/LICENSE-APACHE-2.0.txt` for packaged redistribution.

## Model

- Fixed [Face Landmarker float16 revision 1](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task), not the mutable `latest` URL.
- SHA-256: `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff`.
- Complete unmodified bundle at `apps/desktop/public/tracking/face_landmarker.task`.
- [Official bundle overview](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/index#models) lists the detector, Face Mesh V2, and blendshape models.
- Their official model cards each identify Apache License 2.0:
  [BlazeFace short range](https://storage.googleapis.com/mediapipe-assets/MediaPipe%20BlazeFace%20Model%20Card%20%28Short%20Range%29.pdf),
  [Face Mesh V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20MediaPipe%20Face%20Mesh%20V2.pdf),
  [Blendshape V2](https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Blendshape%20V2.pdf).
  A local copy of the Face Mesh card is retained here. The full task also contains
  geometry metadata; this notice preserves the original bundle and upstream
  attribution rather than representing it as a separately authored model.

## Integration and privacy

The [official web guide](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker/web_js)
states that video inference blocks its calling thread. CoverLab runs
`detectForVideo` in a classic dedicated Worker with the CPU delegate, one face,
and blendshape/matrix outputs disabled. Classic workers support the Emscripten
`importScripts` loader without requiring a build-time npm dependency.

The application submits at most one transferred ImageBitmap at a time and at
most 12 frames per second. Capture timestamps determine the 250ms pose expiry,
so late inference does not revive an old frame. Negative detection immediately
clears the pose. Closing the effect terminates the Worker and WASM allocation;
a bitmap that finishes capture after closure is explicitly closed.

The unmodified runtime is contained by same-origin, tracking-directory-only
fetch/importScripts/XHR guards. WebSocket/EventSource transports are disabled
inside this dedicated worker. The app does not configure telemetry or persist
frames/landmarks. This fixed runtime's npm README does not include the privacy
notice present in the newer upstream main branch; do not use newer-version
claims as evidence about these pinned bytes. The smoke test observed no external
requests, including model initialization and 35 video-frame responses.

Required CSP is `script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'` with
same-origin fetch allowed. No `unsafe-eval`, remote CDN, cross-origin isolation,
or SharedArrayBuffer permission is required by this integration. MediaPipe's
image preprocessing still creates a WebGL2 context even with CPU inference.

## Verification and limitations

- `npm test -- src/recording/headTracking.test.ts`: 13 tests passed, after witnessed
  failing assertions for missing geometry/lifecycle behavior and synchronous
  bitmap-capture failure. Covers center-cover crop, zoom, mirroring, pixel-aspect
  roll, invalid geometry, fast smoothing, immediate cover expansion, throttling,
  capture-time expiry, negative detection, worker failure, and cancellation.
- `npx tsc --noEmit`: passed during implementation.
- Chromium real model initialization and direct official portrait ImageBitmap:
  478 landmarks; CPU XNNPACK delegate logged.
- A canvas-captured 1280x720 video changed x/y, scale, and roll across three
  phases. All three settled poses tracked movement; mirrored roll changed sign.
  Large instantaneous jumps briefly caused a negative detection as expected.
  A 300ms pause and blank frames returned `null`; no stale pose was exposed.
- Executed under `default-src 'self'; script-src 'self' 'wasm-unsafe-eval';
  worker-src 'self'; connect-src 'self'`. All seven runtime/fixture/module URLs
  were same-origin; external request list was empty.
- Reproduction scripts, official portrait fixture, and CLI evidence are under
  `.runtime/mediapipe-0.10.32/` and `output/playwright/`; these are development
  evidence, not packaged public assets. The source portrait was downloaded from
  [MediaPipe's official test asset](https://storage.googleapis.com/mediapipe-assets/portrait.jpg)
  (SHA-256 `a6f11efaa834706db23f275b6115058fa87fc7f14362681e6abe14e82749de3e`).
- This is a synthetic motion stream from a still portrait, not physical webcam
  latency, dark-scene, extreme occlusion, or multi-person validation. Small,
  distant faces can be missed; the compositor must fully cover the portrait
  whenever `getPose` returns `null`. The module returns a face oval, not hair or
  headwear segmentation, so the compositor adds conservative bag margins.
- The no-SIMD files are included unchanged but this Chromium run selected SIMD;
  no-SIMD hardware compatibility remains separately unmeasured.

`SHA256SUMS.json` records exact shipped bytes; the vendor sources, WASM and model
are not modified by application glue. `NOTICE.txt` and the full license are
included under the public tracking directory.
