import { describe, expect, it } from "vitest";
import { playbackPlan, planMomentAt, planSourceTime } from "./playbackPlan";

describe("three-second preparation", () => {
  it("starts the fourth-to-sixth lyric run three seconds early", () => {
    const plan = playbackPlan([{ start: 12, end: 24 }], 60);
    expect(plan.ranges).toEqual([{ start: 12, end: 24 }]);
    expect(plan.totalSeconds).toBe(15);
    expect(planSourceTime(plan, 0)).toBe(9);
    expect(planSourceTime(plan, 2.99)).toBeCloseTo(11.99);
    expect(planSourceTime(plan, 3)).toBe(12);
  });
  it("pads missing source audio with silence so source zero still gets preparation", () => {
    const plan = playbackPlan([{ start: 1, end: 5 }], 10);
    expect(plan.ranges).toEqual([{ start: 1, end: 5 }]);
    expect(plan.totalSeconds).toBe(7);
    expect(planSourceTime(plan, 0)).toBe(-2);
    expect(planSourceTime(plan, 2)).toBe(0);
    expect(planSourceTime(plan, 3)).toBe(1);
  });
  it("keeps preparations distinct even if they overlap in the source", () => {
    const selected = [{ start: 10, end: 14 }, { start: 16, end: 19 }, { start: 30, end: 34 }];
    const plan = playbackPlan(selected, 60);
    expect(plan.ranges).toEqual(selected);
    expect(plan.totalSeconds).toBe(20);
    expect(planMomentAt(plan, 7)).toMatchObject({ sourceTime: 13, preparing: true, remainingSeconds: 3, segmentIndex: 1 });
    expect(planMomentAt(plan, 10)).toMatchObject({ sourceTime: 16, preparing: false });
    expect(planMomentAt(plan, 20)).toMatchObject({ sourceTime: 34, preparing: false, finished: true });
  });
  it("supports no preparation and ten seconds per selection", () => {
    const ranges = [{ start: 1, end: 2 }, { start: 5, end: 6 }];
    const immediate = playbackPlan(ranges, 10, 0);
    expect(immediate.totalSeconds).toBe(2);
    expect(planMomentAt(immediate, 1)).toMatchObject({ sourceTime: 5, preparing: false });
    const prepared = playbackPlan(ranges, 10, 10);
    expect(prepared.totalSeconds).toBe(22);
    expect(planMomentAt(prepared, 0)).toMatchObject({ sourceTime: -9, preparing: true, remainingSeconds: 10 });
    expect(planMomentAt(prepared, 11)).toMatchObject({ sourceTime: -5, preparing: true, remainingSeconds: 10 });
  });
  it("keeps an empty or invalid selection disabled", () => {
    expect(playbackPlan([], 10).ranges).toEqual([]);
    expect(playbackPlan([{ start: 9, end: 15 }], 10).ranges).toEqual([]);
  });
});
