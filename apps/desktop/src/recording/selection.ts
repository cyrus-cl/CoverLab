import type { LyricSegment, TimeRange } from "../api/analysis";

export function validRanges(ranges: readonly TimeRange[], duration: number): boolean {
  let previous = 0;
  return Number.isFinite(duration) && duration > 0 && ranges.length > 0 && ranges.length <= 500 && ranges.every(range => {
    const valid = Number.isFinite(range.start) && Number.isFinite(range.end)
      && range.start >= previous && range.end > range.start && range.end <= duration + 0.001;
    previous = range.end;
    return valid;
  });
}

export function lyricRanges(segments: readonly Pick<LyricSegment, "start" | "end">[], selected: readonly number[], duration: number): TimeRange[] {
  const chosen = new Set(selected);
  const ranges: TimeRange[] = [];
  let previousIndex = -2;
  segments.forEach((line, index) => {
    if (!chosen.has(index) || !validRanges([line], duration)) return;
    const previous = ranges.at(-1);
    if (previous && (index === previousIndex + 1 || line.start <= previous.end)) previous.end = Math.max(previous.end, line.end);
    else ranges.push({ start: line.start, end: line.end });
    previousIndex = index;
  });
  return ranges;
}

export function selectionDuration(ranges: readonly TimeRange[]): number {
  return ranges.reduce((total, range) => total + range.end - range.start, 0);
}

export function sourceTimeAt(ranges: readonly TimeRange[], elapsed: number): number {
  let remaining = Math.max(0, elapsed);
  for (const range of ranges) {
    const length = range.end - range.start;
    if (remaining < length) return range.start + remaining;
    remaining -= length;
  }
  return ranges.at(-1)?.end ?? 0;
}
