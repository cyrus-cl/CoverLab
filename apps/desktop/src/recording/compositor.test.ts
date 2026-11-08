import { describe, expect, it, vi } from "vitest";
import { DEFAULT_COMPOSITION, type AspectRatio } from "./composition";
import { DEFAULT_LOOK, drawCompositeFrame } from "./compositor";

describe("composition text visibility", () => {
  it("keeps the fallback mask aligned with head roll before its image has loaded", () => {
    const context = { fillText: vi.fn(), measureText: (text: string) => ({ width: text.length * 20 }), save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), rect: vi.fn(), roundRect: vi.fn(), ellipse: vi.fn(), clip: vi.fn(), fillRect: vi.fn(), translate: vi.fn(), scale: vi.fn(), rotate: vi.fn(), arc: vi.fn(), fill: vi.fn(), stroke: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), drawImage: vi.fn() };
    const canvas = { width: 0, height: 0, getContext: () => context } as unknown as HTMLCanvasElement;
    drawCompositeFrame(canvas, { readyState: 2, videoWidth: 1280, videoHeight: 720 } as HTMLVideoElement, { look: { ...DEFAULT_LOOK, paperBag: true }, title: "纸袋加载中", time: 1, lyrics: [], headPose: { cx: 320, cy: 300, width: 100, height: 140, rotation: .4 } });
    expect(context.rotate).toHaveBeenCalledWith(.4);
  });
  it.each([["9:16", 0], ["9:16", 4], ["3:4", 0], ["3:4", 4]] as [AspectRatio, number][])("keeps the active sentence visible after a very long prior line on %s at grid %s", (aspect, floatingPosition) => {
    const context = { fillText: vi.fn(), measureText: (text: string) => ({ width: text.length * 20 }), save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), rect: vi.fn(), roundRect: vi.fn(), ellipse: vi.fn(), clip: vi.fn(), fillRect: vi.fn(), translate: vi.fn(), scale: vi.fn(), arc: vi.fn(), fill: vi.fn(), stroke: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn() };
    const canvas = { width: 0, height: 0, getContext: () => context } as unknown as HTMLCanvasElement;
    drawCompositeFrame(canvas, null, { look: DEFAULT_LOOK, title: "本次标题", time: 3,
      composition: { ...DEFAULT_COMPOSITION, aspect, layout: "floating", floatingShape: "circle", floatingSize: 50, floatingPosition },
      lyrics: [{ start: 0, end: 2, text: "上一句很长".repeat(90) }, { start: 2, end: 5, text: "这句必须完整显示" }],
    });
    const painted = context.fillText.mock.calls.map(call => call[0]).join("");
    expect(painted).toContain("这句必须完整显示");
  });
});
