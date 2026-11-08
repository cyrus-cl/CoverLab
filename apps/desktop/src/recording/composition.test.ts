import { describe, expect, it } from "vitest";
import { ASPECT_RATIOS, DEFAULT_COMPOSITION, canvasSize, computeCompositionLayout, cameraTransform, mapPoseToPortrait } from "./composition";

describe("recording composition", () => {
  it.each(ASPECT_RATIOS)("uses an even, encoded canvas for %s", aspect => {
    const [a, b] = aspect.split(":").map(Number), size = canvasSize(aspect);
    expect(size.width / size.height).toBeCloseTo(a / b, 8);
    expect(size.width % 2).toBe(0); expect(size.height % 2).toBe(0);
    expect(Math.min(size.width, size.height)).toBeGreaterThanOrEqual(720);
  });
  it("separates equal columns, stacked rows, and a portrait with a lower lyric overlay", () => {
    const split = computeCompositionLayout(DEFAULT_COMPOSITION);
    expect(split.portrait).toEqual({ x: 0, y: 0, width: 640, height: 720 });
    expect(split.lyrics).toEqual({ x: 640, y: 0, width: 640, height: 720 });
    const stacked = computeCompositionLayout({ ...DEFAULT_COMPOSITION, layout: "stacked" });
    expect(stacked.portrait.y + stacked.portrait.height).toBe(stacked.lyrics.y);
    expect(stacked.lyrics.y + stacked.lyrics.height).toBe(stacked.height);
    const overlay = computeCompositionLayout({ ...DEFAULT_COMPOSITION, layout: "overlay" });
    expect(overlay.portrait.width).toBe(overlay.width);
    expect(overlay.lyrics.y).toBeGreaterThan(overlay.height / 2);
  });
  it("keeps every floating size and nine-grid position inside the safe frame at every aspect", () => {
    for (const aspect of ASPECT_RATIOS) for (let position = 0; position < 9; position++) {
      const layout = computeCompositionLayout({ ...DEFAULT_COMPOSITION, aspect, layout: "floating", floatingPosition: position, floatingSize: 50, margin: 8, floatingShape: "circle" });
      expect(layout.portrait.width).toBeCloseTo(layout.portrait.height);
      expect(layout.portrait.x).toBeGreaterThanOrEqual(layout.safeMargin - .001);
      expect(layout.portrait.y).toBeGreaterThanOrEqual(layout.safeMargin - .001);
      expect(layout.portrait.x + layout.portrait.width).toBeLessThanOrEqual(layout.width - layout.safeMargin + .001);
      expect(layout.portrait.y + layout.portrait.height).toBeLessThanOrEqual(layout.height - layout.safeMargin + .001);
    }
  });
  it("gives the portrait an independent aspect without stretching the allocated region", () => {
    const layout = computeCompositionLayout({ ...DEFAULT_COMPOSITION, portraitAspect: "4:3" });
    expect(layout.portrait.width / layout.portrait.height).toBeCloseTo(4 / 3);
    expect(layout.portrait.x + layout.portrait.width).toBeLessThanOrEqual(layout.lyrics.x);
  });
  it("maps a raw face through the exact offset, cover crop, zoom and mirror used by video", () => {
    const viewport = { x: 100, y: 200, width: 320, height: 400 };
    const pose = { cx: .3, cy: .4, width: .2, height: .3, rotation: .3 };
    const transform = cameraTransform(viewport, 1280, 720, { zoom: 1.5, mirror: true, fit: "cover", cropX: 25, cropY: 75 });
    const mapped = mapPoseToPortrait(pose, transform);
    const rawX = transform.draw.x + pose.cx * transform.draw.width;
    expect(mapped.cx).toBeCloseTo(viewport.x * 2 + viewport.width - rawX);
    expect(mapped.cy).toBeCloseTo(transform.draw.y + pose.cy * transform.draw.height);
    expect(mapped.width).toBeCloseTo(pose.width * transform.draw.width);
    expect(mapped.height).toBeCloseTo(pose.height * transform.draw.height);
    expect(mapped.rotation).toBe(-.3);
  });
  it("letterboxes contain mode and rejects invalid source geometry", () => {
    const viewport = { x: 10, y: 20, width: 300, height: 400 };
    const contain = cameraTransform(viewport, 1200, 600, { zoom: 1, mirror: false, fit: "contain", cropX: 50, cropY: 50 });
    expect(contain.draw).toEqual({ x: 10, y: 145, width: 300, height: 150 });
    expect(() => cameraTransform(viewport, 0, 600, { zoom: 1, mirror: false, fit: "cover", cropX: 50, cropY: 50 })).toThrow();
  });
});
