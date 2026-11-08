import { describe, expect, it } from "vitest";
import { suspiciousRepetition } from "./lyricReview";
describe("recognition review", () => {
  it("flags long repeated credits without deleting the underlying text", () => {
    expect(suspiciousRepetition("作词 作曲 " + "作曲 ".repeat(100))).toBe(true);
  });
  it("preserves normal refrain and short interjections", () => {
    expect(suspiciousRepetition("啊啊啊啊啊啊")).toBe(false);
    expect(suspiciousRepetition("有什么难过 为什么难过 有什么难过 为什么难过")).toBe(false);
  });
});
