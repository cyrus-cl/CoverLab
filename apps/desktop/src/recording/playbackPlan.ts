import type { TimeRange } from "../api/analysis";
import { sourceTimeAt, validRanges } from "./selection";

export const MAX_MIX_SECONDS = 1800;
export type PlaybackBlock = {
  kind: "preparation" | "singing";
  start: number;
  end: number;
  sourceStart: number;
  segmentIndex: number;
};
export type PlaybackPlan = {
  ranges: TimeRange[];
  blocks: PlaybackBlock[];
  preparationSeconds: number;
  leadInSeconds: number;
  totalSeconds: number;
};
export type PlaybackMoment = {
  sourceTime: number;
  preparing: boolean;
  remainingSeconds: number;
  segmentIndex: number;
  finished: boolean;
};

export function playbackPlan(selected: readonly TimeRange[], duration: number, preparation = 3): PlaybackPlan {
  const seconds = Number.isFinite(preparation) ? Math.max(0, Math.min(10, Math.round(preparation))) : 3;
  const ranges = validRanges(selected, duration) ? selected.map(range => ({ ...range })) : [];
  const blocks: PlaybackBlock[] = [];
  let cursor = 0;
  ranges.forEach((range, segmentIndex) => {
    if (seconds) {
      blocks.push({ kind: "preparation", start: cursor, end: cursor + seconds,
        sourceStart: range.start - seconds, segmentIndex });
      cursor += seconds;
    }
    const end = cursor + range.end - range.start;
    blocks.push({ kind: "singing", start: cursor, end, sourceStart: range.start, segmentIndex });
    cursor = end;
  });
  return { ranges, blocks, preparationSeconds: seconds, leadInSeconds: 0, totalSeconds: cursor };
}

export function planMomentAt(plan: PlaybackPlan, elapsed: number): PlaybackMoment {
  const position = Number.isFinite(elapsed) ? Math.max(0, elapsed) : 0;
  const block = plan.blocks.find(candidate => position < candidate.end);
  if (!block) {
    return { sourceTime: plan.ranges.at(-1)?.end ?? 0, preparing: false,
      remainingSeconds: 0, segmentIndex: Math.max(0, plan.ranges.length - 1), finished: true };
  }
  const preparing = block.kind === "preparation";
  return { sourceTime: block.sourceStart + position - block.start, preparing,
    remainingSeconds: preparing ? block.end - position : 0, segmentIndex: block.segmentIndex,
    finished: false };
}

export function planSourceTime(plan: PlaybackPlan | Pick<PlaybackPlan, "ranges" | "leadInSeconds">, elapsed: number): number {
  if ("blocks" in plan) return planMomentAt(plan, elapsed).sourceTime;
  if (elapsed < plan.leadInSeconds) return (plan.ranges[0]?.start ?? 0) - plan.leadInSeconds + Math.max(0, elapsed);
  return sourceTimeAt(plan.ranges, elapsed - plan.leadInSeconds);
}
