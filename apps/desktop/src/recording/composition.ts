import type { HeadPose } from "./headTracking";

export const ASPECT_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"] as const;
export type AspectRatio = typeof ASPECT_RATIOS[number];
export type CompositionSettings = {
  aspect: AspectRatio;
  layout: "split" | "floating" | "stacked" | "overlay";
  portraitAspect: AspectRatio | "auto";
  fit: "cover" | "contain";
  cropX: number;
  cropY: number;
  floatingShape: "circle" | "square" | "rounded";
  floatingSize: number;
  floatingPosition: number;
  margin: number;
};
export const DEFAULT_COMPOSITION: CompositionSettings = {
  aspect: "16:9", layout: "split", portraitAspect: "auto", fit: "cover", cropX: 50, cropY: 50,
  floatingShape: "rounded", floatingSize: 28, floatingPosition: 8, margin: 4,
};
export type Rect = { x: number; y: number; width: number; height: number };
export type CompositionLayout = {
  width: number; height: number; portrait: Rect; lyrics: Rect; safeMargin: number;
  portraitShape: CompositionSettings["floatingShape"] | "rect";
  lyricExclusion: Rect | null; overlay: boolean;
};
const SIZES: Record<AspectRatio, { width: number; height: number }> = {
  "16:9": { width: 1280, height: 720 }, "9:16": { width: 720, height: 1280 },
  "1:1": { width: 960, height: 960 }, "4:3": { width: 960, height: 720 },
  "3:4": { width: 720, height: 960 }, "3:2": { width: 1080, height: 720 }, "2:3": { width: 720, height: 1080 },
};
export function canvasSize(aspect: AspectRatio) { return { ...(SIZES[aspect] ?? SIZES["16:9"]) }; }
const clamp = (value: number, min: number, max: number, fallback: number) => Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;
const ratioOf = (aspect: AspectRatio) => { const [a, b] = aspect.split(":").map(Number); return a / b; };
function fitRect(region: Rect, ratio: number): Rect {
  const width = Math.min(region.width, region.height * ratio), height = width / ratio;
  return { x: region.x + (region.width - width) / 2, y: region.y + (region.height - height) / 2, width, height };
}

/** The same pixel regions drive the camera, its mask, text and capture size. */
export function computeCompositionLayout(settings: CompositionSettings): CompositionLayout {
  const { width, height } = canvasSize(settings.aspect);
  const full = { x: 0, y: 0, width, height };
  const safeMargin = Math.min(width, height) * clamp(settings.margin, 0, 10, 4) / 100;
  let portrait = full, lyrics = full;
  if (settings.layout === "split") {
    portrait = { x: 0, y: 0, width: width / 2, height };
    lyrics = { x: width / 2, y: 0, width: width / 2, height };
  } else if (settings.layout === "stacked") {
    const dividingLine = Math.round(height * .48);
    portrait = { x: 0, y: 0, width, height: dividingLine };
    lyrics = { x: 0, y: dividingLine, width, height: height - dividingLine };
  } else if (settings.layout === "overlay") {
    const overlayHeight = Math.round(height * .36);
    lyrics = { x: 0, y: height - overlayHeight, width, height: overlayHeight };
  } else {
    const ratio = settings.floatingShape === "rounded" ? ratioOf(settings.portraitAspect === "auto" ? "3:4" : settings.portraitAspect) : 1;
    const floatingWidth = Math.min(width * clamp(settings.floatingSize, 14, 50, 28) / 100, width - safeMargin * 2, (height - safeMargin * 2) * ratio);
    const floatingHeight = floatingWidth / ratio;
    const position = Math.round(clamp(settings.floatingPosition, 0, 8, 8));
    portrait = { x: safeMargin + (width - safeMargin * 2 - floatingWidth) * (position % 3) / 2,
      y: safeMargin + (height - safeMargin * 2 - floatingHeight) * Math.floor(position / 3) / 2,
      width: floatingWidth, height: floatingHeight };
  }
  if (settings.layout !== "floating" && settings.portraitAspect !== "auto") portrait = fitRect(portrait, ratioOf(settings.portraitAspect));
  return { width, height, portrait, lyrics, safeMargin, portraitShape: settings.layout === "floating" ? settings.floatingShape : "rect",
    lyricExclusion: settings.layout === "floating" ? portrait : null, overlay: settings.layout === "overlay" };
}

export type CameraTransform = { viewport: Rect; draw: Rect; mirror: boolean };
export function cameraTransform(viewport: Rect, videoWidth: number, videoHeight: number, options: { zoom: number; mirror: boolean; fit: "cover" | "contain"; cropX: number; cropY: number }): CameraTransform {
  if (![viewport.x, viewport.y, viewport.width, viewport.height, videoWidth, videoHeight, options.zoom].every(Number.isFinite)
    || [viewport.width, viewport.height, videoWidth, videoHeight, options.zoom].some(value => value <= 0)) throw new RangeError("Camera layout requires positive finite geometry.");
  const fit = options.fit === "contain" ? Math.min : Math.max;
  const scale = fit(viewport.width / videoWidth, viewport.height / videoHeight) * options.zoom;
  const width = videoWidth * scale, height = videoHeight * scale;
  return { viewport, mirror: options.mirror, draw: {
    x: viewport.x + (viewport.width - width) * clamp(options.cropX, 0, 100, 50) / 100,
    y: viewport.y + (viewport.height - height) * clamp(options.cropY, 0, 100, 50) / 100, width, height,
  } };
}
export function mapPoseToPortrait(pose: HeadPose, transform: CameraTransform): HeadPose {
  const { draw, viewport, mirror } = transform;
  const x = draw.x + pose.cx * draw.width;
  return { cx: mirror ? viewport.x * 2 + viewport.width - x : x, cy: draw.y + pose.cy * draw.height,
    width: pose.width * draw.width, height: pose.height * draw.height, rotation: mirror ? -pose.rotation : pose.rotation };
}
