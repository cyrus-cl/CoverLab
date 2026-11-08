import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AudioTimeline } from "./AudioTimeline";
import type { AudioArtifact } from "../api/processing";
import { claimAudioFocus } from "../recording/audioFocus";
import { playbackPlan } from "../recording/playbackPlan";
const mocks = vi.hoisted(() => ({ getArtifactPlaybackUrl: vi.fn() }));
vi.mock("../api/processing", async importOriginal => ({ ...await importOriginal<typeof import("../api/processing")>(), getArtifactPlaybackUrl: mocks.getArtifactPlaybackUrl }));
afterEach(() => vi.restoreAllMocks());

const audio: AudioArtifact = {
  id: "a",
  analysisId: "j",
  kind: "normalized_audio",
  path: "D:/a.wav",
  byteSize: 10,
  sha256: "a".repeat(64),
  contentUrl: "/api/v1/artifacts/a/content",
  metadata: {
    duration_seconds: 10,
    sample_rate: 44100,
    channels: 2,
    codec: "pcm_f32le",
    container: "wav",
  },
};

describe("AudioTimeline", () => {
  it("plays the packed guide continuously and marks each preparation without highlighting lyrics", async () => {
    const ranges = [{ start: 1, end: 3 }, { start: 5, end: 7 }];
    const plan = playbackPlan(ranges, 10, 3);
    const loadMix = vi.fn(async () => "http://127.0.0.1:3000/api/v1/mixes/fixture");
    const onTimeChange = vi.fn();
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    render(<AudioTimeline audio={audio} range={{ start: 1, end: 7 }} selectionRanges={ranges}
      mixPlan={plan} loadMix={loadMix} onRangeChange={vi.fn()} onTimeChange={onTimeChange} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    const player = screen.getByLabelText("时间轴播放器") as HTMLAudioElement;
    expect(loadMix).toHaveBeenCalledOnce();
    expect(onTimeChange).toHaveBeenLastCalledWith(-2, true);
    player.currentTime = 3;
    fireEvent.timeUpdate(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(1, false);
    player.currentTime = 5;
    fireEvent.timeUpdate(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(2, true);
    expect(screen.getByText("准备 3 · 第 2 / 2 段")).toBeVisible();
    player.currentTime = 8;
    fireEvent.timeUpdate(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(5, false);
    expect(player.currentTime).toBe(8);
    fireEvent.ended(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(-1, false);
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "10");
  });
  it("cancels silent preparation when another player takes focus, without stealing it back", async () => {
    vi.useFakeTimers();
    try {
      mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
      const play=vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);
      render(<AudioTimeline audio={audio} range={{start:0,end:2}} leadInSeconds={3} lyricSelection onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
      await act(async()=>{fireEvent.click(screen.getByRole("button",{name:"试听选段"}));});
      expect(screen.getByText(/准备 3/)).toBeVisible();
      act(()=>claimAudioFocus(document.createElement("audio")));
      await act(async()=>vi.advanceTimersByTimeAsync(4000));
      expect(play).not.toHaveBeenCalled();
      expect(screen.getByRole("button",{name:"试听选段"})).toBeEnabled();
    } finally {vi.useRealTimers();}
  });
  it("does not start a delayed audio load after losing playback focus", async () => {
    let resolve!: (url:string)=>void;
    mocks.getArtifactPlaybackUrl.mockReturnValueOnce(new Promise<string>(done=>{resolve=done;}));
    const play=vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);
    render(<AudioTimeline audio={audio} range={{start:1,end:2}} lyricSelection onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("button",{name:"试听选段"}));
    act(()=>claimAudioFocus(document.createElement("audio")));
    await act(async()=>resolve("http://localhost/late"));
    expect(play).not.toHaveBeenCalled();
    expect(screen.getByRole("button",{name:"试听选段"})).toBeEnabled();
  });
  it("shows cumulative selected-queue progress and pauses, resumes and stops without whole-song controls", async () => {
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    render(<AudioTimeline audio={audio} range={{ start: 1, end: 9 }} selectionRanges={[{ start: 1, end: 3 }, { start: 8, end: 9 }]} onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
    const player = screen.getByLabelText("时间轴播放器") as HTMLAudioElement;
    expect(player).not.toHaveAttribute("controls");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    player.currentTime = 2; fireEvent.timeUpdate(player);
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "1");
    expect(screen.getByLabelText("所选片段试听时长")).toHaveTextContent("00:01.00 / 00:03.00");
    expect(screen.getByText("第 1 / 2 段")).toBeVisible();
    player.currentTime = 3; fireEvent.timeUpdate(player);
    expect(player.currentTime).toBe(8);
    expect(screen.getByText("第 2 / 2 段")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "暂停试听" }));
    player.currentTime = 8.1; fireEvent.timeUpdate(player);
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "2");
    player.currentTime = 8;
    const beforeResume = play.mock.calls.length;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "继续试听" })); });
    expect(play).toHaveBeenCalledTimes(beforeResume + 1);
    expect(player.currentTime).toBe(8);
    player.currentTime = 8.5; fireEvent.timeUpdate(player);
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "2.5");
    player.currentTime = 9; fireEvent.timeUpdate(player);
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "3");
    expect(screen.getByText(/试听结束/)).toBeVisible();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    fireEvent.click(screen.getByRole("button", { name: "停止试听" }));
    expect(screen.getByLabelText("所选片段试听进度")).toHaveAttribute("value", "0");
    expect(screen.getByRole("button", { name: "试听选段" })).toBeEnabled();
  });
  it("keeps an intentional pause when its pending play promise is interrupted", async () => {
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    let reject!: (error: unknown) => void;
    vi.spyOn(HTMLMediaElement.prototype, "play").mockImplementationOnce(() => new Promise<void>((_resolve, fail) => { reject = fail; }));
    render(<AudioTimeline audio={audio} range={{ start: 1, end: 3 }} onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听片段" })); });
    fireEvent.click(screen.getByRole("button", { name: "暂停试听" }));
    await act(async () => { reject(new DOMException("play interrupted by pause", "AbortError")); });
    expect(screen.getByRole("button", { name: "继续试听" })).toBeEnabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("exposes one preview state and returns to a retryable state on a decode error", async () => {
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    render(<AudioTimeline audio={audio} range={{ start: 1, end: 3 }} onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听片段" })); });
    expect(screen.getByRole("button", { name: "暂停试听" })).toBeEnabled();
    fireEvent.error(screen.getByLabelText("时间轴播放器"));
    expect(screen.getByRole("alert")).toHaveTextContent("解码失败");
    expect(screen.getByRole("button", { name: "试听片段" })).toBeEnabled();
  });
  it("can pause and resume introductory silence without replaying its completed portion", async () => {
    vi.useFakeTimers();
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const view = render(<AudioTimeline audio={audio} range={{ start: 0, end: 2 }} selectionRanges={[{ start: 0, end: 2 }]} leadInSeconds={2} onRangeChange={vi.fn()} onTimeChange={vi.fn()} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    fireEvent.click(screen.getByRole("button", { name: "暂停试听" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(play).not.toHaveBeenCalled();
    expect(Number(screen.getByLabelText("所选片段试听进度").getAttribute("value"))).toBeCloseTo(1, 1);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "继续试听" })); await vi.advanceTimersByTimeAsync(1000); });
    expect(play).toHaveBeenCalledOnce();
    view.unmount(); vi.useRealTimers();
  });
  it("starts custom playback at the prepared range even after seeking to the chosen lyric", async () => {
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const onTimeChange = vi.fn();
    const longer = { ...audio, metadata: { ...audio.metadata, duration_seconds: 30 } };
    render(<AudioTimeline audio={longer} range={{ start: 12, end: 18 }} selectionRanges={[{ start: 9, end: 18 }]} seekRequest={{ time: 12, nonce: 1 }} onRangeChange={vi.fn()} onTimeChange={onTimeChange} />);
    const player = screen.getByLabelText("时间轴播放器") as HTMLAudioElement;
    player.setAttribute("src", "blob:already-loaded");
    player.currentTime = 12;
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    expect(player.currentTime).toBe(9);
    expect(onTimeChange).toHaveBeenLastCalledWith(9);
  });
  it("pauses an earlier audition before restarting preparation and rejects its late timeupdates", async () => {
    vi.useFakeTimers();
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const onTimeChange = vi.fn();
    const view = render(<AudioTimeline audio={audio} range={{ start: 0, end: 2 }} selectionRanges={[{ start: 0, end: 2 }]} leadInSeconds={2} onRangeChange={vi.fn()} onTimeChange={onTimeChange} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    fireEvent.click(screen.getByRole("button", { name: "停止试听" }));
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(onTimeChange).toHaveBeenLastCalledWith(-2);
    const player = screen.getByLabelText("时间轴播放器") as HTMLAudioElement;
    player.currentTime = .5; fireEvent.timeUpdate(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(-2);
    view.unmount(); vi.useRealTimers();
  });
  it("holds source-zero playback until the missing preparation silence completes", async () => {
    vi.useFakeTimers();
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const onTimeChange = vi.fn();
    const view = render(<AudioTimeline audio={audio} range={{ start: 0, end: 2 }} selectionRanges={[{ start: 0, end: 2 }]} leadInSeconds={2} onRangeChange={vi.fn()} onTimeChange={onTimeChange} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    expect(play).not.toHaveBeenCalled();
    expect(onTimeChange).toHaveBeenCalledWith(-2);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(play).toHaveBeenCalledOnce();
    expect(onTimeChange).toHaveBeenLastCalledWith(0);
    view.unmount(); vi.useRealTimers();
  });
  it("does not restart a pending audition when recording disables the timeline", async () => {
    let resolve!: (url: string) => void;
    mocks.getArtifactPlaybackUrl.mockReturnValueOnce(new Promise<string>(done => { resolve = done; }));
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const props = { audio, range: { start: 1, end: 3 }, onRangeChange: vi.fn(), onTimeChange: vi.fn() };
    const view = render(<AudioTimeline {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "试听片段" }));
    view.rerender(<AudioTimeline {...props} disabled />);
    await act(async () => { resolve("http://localhost/audio"); });
    expect(play).not.toHaveBeenCalled();
  });
  it("stops the old queue when the checked lyric ranges change", async () => {
    mocks.getArtifactPlaybackUrl.mockResolvedValueOnce("http://localhost/audio");
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const onTimeChange = vi.fn();
    const props = { audio, range: { start: 1, end: 9 }, onRangeChange: vi.fn(), onTimeChange };
    const view = render(<AudioTimeline {...props} selectionRanges={[{ start: 1, end: 3 }, { start: 8, end: 9 }]} />);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "试听选段" })); });
    const player = screen.getByLabelText("时间轴播放器") as HTMLAudioElement;
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    view.rerender(<AudioTimeline {...props} selectionRanges={[{ start: 4, end: 6 }]} />);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled();
    player.currentTime = 4.5; fireEvent.timeUpdate(player);
    expect(onTimeChange).toHaveBeenLastCalledWith(4.5);
    expect(player.currentTime).toBe(4.5);
  });
  it("clamps selected end to actual duration", () => {
    const change = vi.fn();
    render(
      <AudioTimeline
        audio={audio}
        range={{ start: 1, end: 8 }}
        onRangeChange={change}
        onTimeChange={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText("片段结束（秒）"), {
      target: { value: "999" },
    });
    expect(change).toHaveBeenCalledWith({ start: 1, end: 10 });
  });
});
