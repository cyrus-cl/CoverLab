import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_MIX_SETTINGS } from "../api/recordingMix";
import { playbackPlan } from "../recording/playbackPlan";
import type { RecordedTake } from "../recording/browserRecorder";
import { RecordingPanel } from "./RecordingPanel";

type Callbacks = {
  onFinished: (take: RecordedTake) => void;
  onError: (message: string) => void;
  onRecording: (stream: MediaStream) => void;
  onLevel: (level: number, elapsed: number) => void;
  onPosition: (sourceTime: number, moment: {sourceTime: number; preparing: boolean; remainingSeconds: number}) => void;
};
const mocks = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn(), dispose: vi.fn(), getEngineConnection: vi.fn(), getRecordingMix: vi.fn(), loadRecordingMix: vi.fn(), callbacks: [] as Callbacks[], sceneOptions: [] as Array<() => { title: string; lyrics: { text: string }[]; time: number }> }));
vi.mock("../recording/compositor", async importOriginal => ({ ...await importOriginal<typeof import("../recording/compositor")>(), CameraScene: class {
  constructor(_canvas: unknown, _input: unknown, options: () => { title: string; lyrics: { text: string }[]; time: number }) { mocks.sceneOptions.push(options); }
  start = vi.fn(async () => {});
  capture = vi.fn(() => ({}));
  dispose = vi.fn();
} }));
vi.mock("../recording/browserRecorder", async importOriginal => ({ ...await importOriginal<typeof import("../recording/browserRecorder")>(), BrowserRecorder: class {
  constructor(callbacks: Callbacks) { mocks.callbacks.push(callbacks); }
  start = mocks.start;
  stop = mocks.stop;
  dispose = mocks.dispose;
} }));
vi.mock("../api/recordingMix", async importOriginal => ({
  ...await importOriginal<typeof import("../api/recordingMix")>(),
  getRecordingMix: mocks.getRecordingMix,
  loadRecordingMix: mocks.loadRecordingMix,
}));
vi.mock("../api/engine", async importOriginal => ({
  ...await importOriginal<typeof import("../api/engine")>(), getEngineConnection: mocks.getEngineConnection,
}));

const defaults = {
  projectId: "project/a",
  mixPlan: playbackPlan([{ start: 2, end: 5 }], 10),
  mixSettings: DEFAULT_MIX_SETTINGS,
  hasSeparatedStems: true,
  onMixSettingsChange: vi.fn(),
  onSaved: vi.fn(),
};
const assembledMix = {contentUrl:"http://127.0.0.1:8173/api/v1/mixes/one", durationSeconds:6};
const take: RecordedTake = { blob: new Blob(["capture"], { type: "audio/webm" }), mimeType: "audio/webm" };
const fetchMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  mocks.callbacks.length = 0;
  mocks.sceneOptions.length = 0;
  mocks.start.mockResolvedValue(undefined);
  mocks.getRecordingMix.mockResolvedValue(assembledMix);
  mocks.loadRecordingMix.mockResolvedValue(new ArrayBuffer(16));
  mocks.getEngineConnection.mockResolvedValue({ baseUrl: "http://127.0.0.1:8173", token: "local-token" });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("confirm", vi.fn(() => true));
  URL.createObjectURL = vi.fn(() => "blob:take");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function recordTake() {
  fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
  await act(async () => { mocks.callbacks.at(-1)!.onFinished(take); });
}

describe("RecordingPanel", () => {
  it("offers the paper bag directly, keeps preview before settings, and applies portrait canvas dimensions", () => {
    render(<RecordingPanel {...defaults} />);
    const effect = screen.getByRole("checkbox", { name: "使用品牌牛皮纸袋" });
    expect(effect.closest("details")).toBeNull();
    const canvas = screen.getByLabelText("人像与备忘录歌词合成预览");
    expect(canvas.compareDocumentPosition(screen.getByRole("combobox", { name: "麦克风" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox", { name: "画布比例" }), { target: { value: "9:16" } });
    expect(canvas).toHaveAttribute("width", "720"); expect(canvas).toHaveAttribute("height", "1280");
    expect(screen.getAllByText(/720 × 1280/)).toHaveLength(1);
  });
  it("freezes the chosen layout and output dimensions when recording starts", async () => {
    render(<RecordingPanel {...defaults} />);
    fireEvent.change(screen.getByRole("combobox", { name: "画布比例" }), { target: { value: "1:1" } });
    fireEvent.change(screen.getByRole("combobox", { name: "画面布局" }), { target: { value: "floating" } });
    fireEvent.change(screen.getByRole("combobox", { name: "浮动人像形状" }), { target: { value: "circle" } });
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    expect(screen.getByRole("combobox", { name: "画布比例" })).toBeDisabled();
    await act(async () => { await mocks.start.mock.calls[0][0].composeVideo({}); });
    expect(mocks.sceneOptions[0]()).toEqual(expect.objectContaining({ composition: expect.objectContaining({ aspect: "1:1", layout: "floating", floatingShape: "circle" }) }));
  });
  it("shows the three background sound settings and locks them for the capture", () => {
    render(<RecordingPanel {...defaults} />);
    const preparation = screen.getByRole("slider", { name: "准备时长" });
    const vocals = screen.getByRole("slider", { name: "人声音量" });
    const source = screen.getByRole("radio", { name: "伴奏" });
    const include = screen.getByRole("checkbox", { name: "混入背景音" });
    expect(preparation).toHaveAttribute("aria-valuenow", "3");
    expect(preparation).toHaveAttribute("aria-valuemax", "10");
    expect(vocals).toHaveAttribute("aria-valuenow", "0");
    expect(vocals).toHaveAttribute("aria-valuemax", "100");
    expect(source).toBeChecked();
    expect(include).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    expect(preparation).not.toHaveAttribute("tabindex", "0");
    expect(vocals).not.toHaveAttribute("tabindex", "0");
    expect(source).toBeDisabled();
    expect(include).toBeDisabled();
  });

  it("uses the preparation clock without marking a lyric active early", () => {
    render(<RecordingPanel {...defaults} lyrics={[{ start: 2, end: 5, text: "此刻才唱" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => {
      mocks.callbacks[0].onRecording({ getVideoTracks: () => [] } as unknown as MediaStream);
      mocks.callbacks[0].onPosition(2, { sourceTime: 2, preparing: true, remainingSeconds: 1.3 });
    });
    expect(screen.getByLabelText("当前歌词")).toHaveTextContent("准备 2");
    expect(screen.queryByText("此刻才唱")).not.toBeInTheDocument();
    act(() => mocks.callbacks[0].onPosition(2, { sourceTime: 2, preparing: false, remainingSeconds: 0 }));
    expect(screen.getByLabelText("当前歌词")).toHaveTextContent("此刻才唱");
  });
  it("keeps standby lyrics separate from the audition clock without repeating idle and preview state", async () => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => stream) } });
    render(<RecordingPanel {...defaults} lyrics={[{ start: 2, end: 5, text: "刚试听过的歌词" }]} />);
    expect(screen.queryByLabelText("当前歌词")).not.toBeInTheDocument();
    expect(screen.queryByText(/等待开始|机器草稿/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开启摄像头预览" }));
    await screen.findByRole("button", { name: "关闭预览" });
    expect(screen.queryByText(/预览已开启/)).not.toBeInTheDocument();
    expect(mocks.sceneOptions[0]()).toEqual(expect.objectContaining({ standby: true, time: 0 }));
  });
  it("offers direct capture retry and device selection after an error", () => {
    render(<RecordingPanel {...defaults} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => mocks.callbacks[0].onError("录制设备已断开。"));
    fireEvent.click(screen.getByRole("button", { name: "重新选择设备" }));
    expect(screen.getByRole("combobox", { name: "麦克风" })).toHaveFocus();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => mocks.callbacks[1].onError("设备被占用，请重试。"));
    fireEvent.click(screen.getByRole("button", { name: "重试录制" }));
    expect(mocks.start).toHaveBeenCalledTimes(3);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("clears a recording error and leaves the error phase after a successful preview", async () => {
    const stream = { getTracks: () => [] } as unknown as MediaStream;
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn(async () => stream) } });
    render(<RecordingPanel {...defaults} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => mocks.callbacks[0].onError("录制设备已断开。"));
    fireEvent.click(screen.getByRole("button", { name: "开启摄像头预览" }));
    await screen.findByRole("button", { name: "关闭预览" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始录制" })).toBeEnabled();
  });
  it("makes saving the primary review action and exposes the WebM download", async () => {
    render(<RecordingPanel {...defaults} />);
    await recordTake();
    expect(screen.getByRole("button", { name: "保存到项目" })).toHaveClass("button--primary");
    expect(screen.getByRole("link", { name: "下载录制" })).toHaveAttribute("download", expect.stringMatching(/\.webm$/));
    expect(screen.getByRole("button", { name: "丢弃录制" })).toHaveClass("button--danger");
  });
  it("freezes the video lyrics and title even when an earlier save refreshes parent artifacts", async () => {
    const lyrics = [{ start: 2, end: 5, text: "本次录制的歌词" }];
    const view = render(<RecordingPanel {...defaults} lyrics={lyrics} title="本次标题" />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    await act(async () => { await mocks.start.mock.calls[0][0].composeVideo({}); });
    view.rerender(<RecordingPanel {...defaults} mixPlan={playbackPlan([{start:6,end:8}],10)} lyrics={[]} title="后台刷新后的标题" />);
    expect(mocks.sceneOptions[0]()).toEqual(expect.objectContaining({ title: "本次标题", lyrics, time: -1, preparation: 3 }));
  });
  it("persists the captured disjoint queue rather than the parent's later selection", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "saved" }), { status: 201 }));
    const ranges = [{ start: 2, end: 3 }, { start: 6, end: 8 }];
    const view = render(<RecordingPanel {...defaults} mixPlan={playbackPlan(ranges,10)} />);
    await recordTake();
    view.rerender(<RecordingPanel {...defaults} mixPlan={playbackPlan([{start:1,end:9}],10,7)} mixSettings={{...DEFAULT_MIX_SETTINGS,preparationSeconds:7}} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const target = new URL(String(fetchMock.mock.calls[0][0]));
    expect(JSON.parse(target.searchParams.get("ranges")!)).toEqual(ranges);
    expect(target.searchParams.get("start")).toBe("2");
    expect(target.searchParams.get("end")).toBe("8");
    expect(JSON.parse(target.searchParams.get("mix")!)).toEqual(DEFAULT_MIX_SETTINGS);
    expect(target.searchParams.has("lead_in")).toBe(false);
  });
  it("keeps the preview source through StrictMode ref replay", async () => {
    render(<StrictMode><RecordingPanel {...defaults}/></StrictMode>);
    await recordTake();
    expect(screen.getByLabelText("录制回放")).toHaveAttribute("src","blob:take");
  });
  it("starts capture only on a user action with the selected range and optional camera", async () => {
    render(<RecordingPanel {...defaults} />);
    expect(mocks.start).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "同时录制摄像头" })).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    expect(mocks.start).toHaveBeenCalledWith(expect.objectContaining({ video: true, range: { start: 2, end: 5 }, loadBacking: expect.any(Function) }));
    const signal = new AbortController().signal;
    await mocks.start.mock.calls[0][0].loadBacking(signal);
    expect(mocks.getRecordingMix).toHaveBeenCalledWith(defaults.projectId, defaults.mixPlan.ranges, DEFAULT_MIX_SETTINGS, signal);
    expect(mocks.loadRecordingMix).toHaveBeenCalledWith(assembledMix, signal);
    expect(screen.getByRole("button", { name: "取消准备" })).toBeEnabled();
  });

  it("keeps per-section preparation in a microphone-only take", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "saved" }), { status: 201 }));
    render(<RecordingPanel {...defaults} hasSeparatedStems={false} />);
    expect(screen.getByRole("checkbox",{name:"混入背景音"})).toBeDisabled();
    expect(screen.getByRole("checkbox",{name:"混入背景音"})).not.toBeChecked();
    expect(screen.getByRole("slider",{name:"准备时长"})).toHaveAttribute("aria-valuenow","3");
    await recordTake();
    expect(mocks.start).toHaveBeenCalledWith(expect.objectContaining({mixPlan: defaults.mixPlan}));
    expect(mocks.start.mock.calls[0][0].loadBacking).toBeUndefined();
    fireEvent.click(screen.getByRole("button", {name:"保存到项目"}));
    await waitFor(()=>expect(fetchMock).toHaveBeenCalledOnce());
    const query = new URL(String(fetchMock.mock.calls[0][0])).searchParams;
    expect(JSON.parse(query.get("mix")!)).toEqual({...DEFAULT_MIX_SETTINGS,includeBacking:false});
  });

  it("retains a real preview and retries the same binary after local save failure", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: "saved-recording" }), { status: 201 }));
    render(<RecordingPanel {...defaults} />);
    await recordTake();
    expect(screen.getByLabelText("录制回放")).toHaveAttribute("src", "blob:take");
    expect(screen.queryByRole("button", { name: "开始录制" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    await screen.findByRole("button", { name: "重试保存" });
    expect(screen.getByRole("alert")).toHaveTextContent(/保存失败/);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重试保存" }));
    await screen.findByText("录制已保存到项目。");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [url, init] = fetchMock.mock.calls[1];
    const target = new URL(String(url));
    expect(target.pathname).toBe("/api/v1/projects/project%2Fa/recordings");
    expect(target.searchParams.get("start")).toBe("2");
    expect(target.searchParams.get("end")).toBe("5");
    expect(JSON.parse(target.searchParams.get("mix")!)).toEqual(DEFAULT_MIX_SETTINGS);
    expect(init.body).toBe(take.blob);
    expect(new Headers(init.headers).get("Content-Type")).toBe("audio/webm");
    expect(new Headers(init.headers).get("X-AI4Music-Token")).toBe("local-token");
    expect(defaults.onSaved).toHaveBeenCalledOnce();
  });

  it("disposes capture and revokes playback when changing projects", async () => {
    const view = render(<RecordingPanel {...defaults} />);
    await recordTake();
    view.rerender(<RecordingPanel {...defaults} projectId="another-project" />);
    expect(mocks.dispose).toHaveBeenCalled();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:take");
    expect(screen.queryByLabelText("录制回放")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始录制" })).toBeEnabled();
  });

  it("keeps recording save offsets from the captured selection even when the parent selection changes", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: "saved" }), { status: 201 }));
    const view = render(<RecordingPanel {...defaults} />);
    await recordTake();
    view.rerender(<RecordingPanel {...defaults} mixPlan={playbackPlan([{start:6,end:8}],10)} />);
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(String(fetchMock.mock.calls[0][0])).toContain("start=2&end=5");
  });

  it("requires an explicit discard before another recording can replace the unsaved take", async () => {
    render(<RecordingPanel {...defaults} />);
    await recordTake();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "丢弃录制" }));
    expect(screen.getByLabelText("录制回放")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "丢弃录制" }));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:take");
    expect(screen.getByRole("button", { name: "开始录制" })).toBeEnabled();
  });

  it("shows actionable capture errors and permits a fresh user initiated retry", async () => {
    render(<RecordingPanel {...defaults} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => mocks.callbacks[0].onError("请在系统设置中允许麦克风权限后重试。"));
    expect(screen.getByRole("alert")).toHaveTextContent("允许麦克风权限");
    expect(screen.getByRole("button", { name: "开始录制" })).toBeEnabled();
  });

  it("cancels a pending save on unmount without reporting a saved artifact", async () => {
    let saveSignal: AbortSignal | undefined;
    fetchMock.mockImplementation((_url: string, options: RequestInit) => {
      saveSignal = options.signal as AbortSignal;
      return new Promise((_resolve, reject) => saveSignal?.addEventListener("abort", () => reject(new DOMException("abort", "AbortError"))));
    });
    const view = render(<RecordingPanel {...defaults} />);
    await recordTake();
    fireEvent.click(screen.getByRole("button", { name: "保存到项目" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    view.unmount();
    expect(saveSignal?.aborted).toBe(true);
    expect(defaults.onSaved).not.toHaveBeenCalled();
  });

  it("permits stopping an active take even after other workspace actions become disabled", () => {
    const view = render(<RecordingPanel {...defaults} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => {
      mocks.callbacks[0].onRecording({ getVideoTracks: () => [] } as unknown as MediaStream);
      mocks.callbacks[0].onLevel(0.4, 1.2);
    });
    view.rerender(<RecordingPanel {...defaults} disabled />);
    expect(screen.getByLabelText("录制时长")).toHaveTextContent("00:01");
    expect(screen.getByRole("meter", { name: "麦克风输入电平" })).toHaveAttribute("value", "0.4");
    fireEvent.click(screen.getByRole("button", { name: "停止录制" }));
    expect(mocks.stop).toHaveBeenCalledOnce();
  });

  it("does not allow a stale canceled preparation to replace a new session", () => {
    render(<RecordingPanel {...defaults} />);
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    fireEvent.click(screen.getByRole("button", { name: "取消准备" }));
    fireEvent.click(screen.getByRole("button", { name: "开始录制" }));
    act(() => mocks.callbacks[0].onFinished(take));
    expect(screen.queryByLabelText("录制回放")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消准备" })).toBeEnabled();
  });

  it("explicitly stops a local playback element when the panel unmounts", async () => {
    const view = render(<RecordingPanel {...defaults} />);
    await recordTake();
    const replay = screen.getByLabelText("录制回放");
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    view.unmount();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(replay).not.toHaveAttribute("src");
  });
});
