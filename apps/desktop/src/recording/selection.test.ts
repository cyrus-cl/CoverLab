import { describe, expect, it } from "vitest";
import { lyricRanges, selectionDuration, sourceTimeAt, validRanges } from "./selection";

const lines = [
  { start: 1, end: 3, text: "第一句" },
  { start: 4, end: 6, text: "第二句" },
  { start: 8, end: 10, text: "第三句" },
  { start: 12, end: 14, text: "第四句" },
];

describe("lyric selection playback", () => {
  it("joins neighboring lyrics with their natural gap and skips unselected lyrics", () => {
    expect(lyricRanges(lines, [3, 0, 1, 1], 20)).toEqual([
      { start: 1, end: 6 }, { start: 12, end: 14 },
    ]);
    expect(lyricRanges(lines, [], 20)).toEqual([]);
  });
  it("maps a continuous recording clock back to song time at each cut", () => {
    const ranges = [{ start: 1, end: 6 }, { start: 12, end: 14 }];
    expect(selectionDuration(ranges)).toBe(7);
    expect(sourceTimeAt(ranges, -2)).toBe(1);
    expect(sourceTimeAt(ranges, 4.5)).toBe(5.5);
    expect(sourceTimeAt(ranges, 5)).toBe(12);
    expect(sourceTimeAt(ranges, 6.5)).toBe(13.5);
    expect(sourceTimeAt(ranges, 99)).toBe(14);
  });
  it("rejects unordered, overlapping, nonfinite or empty ranges", () => {
    expect(validRanges([{ start: 1, end: 3 }, { start: 4, end: 5 }], 10)).toBe(true);
    for (const ranges of [[], [{ start: -1, end: 2 }], [{ start: 3, end: 2 }],
      [{ start: 1, end: Infinity }], [{ start: 1, end: 4 }, { start: 3, end: 5 }]]) {
      expect(validRanges(ranges, 10)).toBe(false);
    }
    expect(validRanges([{ start: 1, end: 11 }], 10)).toBe(false);
  });
});
