import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";

import type { LyricsDocument, WaveformData } from "../api/analysis";
import type { AudioArtifact, ProjectArtifact } from "../api/processing";
import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { AudioTimeline } from "./AudioTimeline";
import type { RecordingPanelProps } from "./RecordingPanel";
import * as processing from "../api/processing";
import * as recordingMix from "../api/recordingMix";
import { RecordingMixControls } from "../recording/RecordingMixControls";

const mocks = vi.hoisted(() => ({ readArtifactJSON: vi.fn(), recordingProps: vi.fn() }));
vi.mock("../api/analysis", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/analysis")>()),
  readArtifactJSON: mocks.readArtifactJSON,
}));
vi.mock("./RecordingPanel", () => ({
  RecordingPanel: (props: RecordingPanelProps) => {
    mocks.recordingProps(props);
    return <section data-guide="record" tabIndex={-1}>
      <div className="recording-panel__actions"><button type="button">开始录制</button></div>
      <RecordingMixControls settings={props.mixSettings} hasSeparatedStems={props.hasSeparatedStems}
        disabled={false} onChange={props.onMixSettingsChange} />
    </section>;
  },
}));

const audio: AudioArtifact = {
  id: "audio",
  analysisId: "prepare",
  kind: "normalized_audio",
  path: "D:/audio.wav",
  byteSize: 1024,
  sha256: "a".repeat(64),
  contentUrl: "/api/v1/artifacts/audio/content",
  metadata: {
    duration_seconds: 10,
    sample_rate: 44100,
    channels: 2,
    codec: "pcm",
    container: "wav",
  },
};
const tracks: AudioArtifact[] = [
  { ...audio, id:"backing", kind:"instrumental" },
  { ...audio, id:"voice", kind:"vocals" },
  audio,
];

const artifact = (
  id: string,
  kind: ProjectArtifact["kind"],
): ProjectArtifact => ({
  id,
  kind,
  analysisId: "analysis",
  path: `D:/${id}.json`,
  byteSize: 128,
  sha256: "b".repeat(64),
  contentUrl: `/api/v1/artifacts/${id}/content`,
  metadata: {},
});
const lyrics: LyricsDocument = {
  schemaVersion: 1,
  durationSeconds: 10,
  language: "zh",
  segments: [{ start: 1, end: 5, text: "原始歌词", words: [] }],
};
const waveform: WaveformData = {
  schemaVersion: 1,
  durationSeconds: 10,
  sampleRate: 44100,
  channels: 2,
  peaks: [[-0.2, 0.4]],
};

beforeEach(() => {
  mocks.readArtifactJSON
    .mockReset()
    .mockImplementation(async (item: ProjectArtifact) => {
      if (item.kind === "lyrics") return structuredClone(lyrics);
      if (item.kind === "waveform") return waveform;
      return {
        schemaVersion: 1,
        durationSeconds: 10,
        bpm: item.id === "beats-2" ? 90 : 120,
        beats: [1, 1.5, 2],
        confidence: 0.8,
      };
    });
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
});
afterEach(() => vi.restoreAllMocks());

describe("AnalysisWorkspace", () => {
  it("can enter mic-only recording without using the original audio as a background mix", async () => {
    render(<AnalysisWorkspace projectId="p" artifacts={[audio,artifact("lyrics","lyrics")]} disabled={false} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole("checkbox",{name:"选择第 1 句"}));
    const record=within(within(screen.getByRole("region",{name:"歌词备忘录"})).getByRole("toolbar",{name:"选段操作"})).getByRole("button",{name:"去录制"});
    expect(record).toBeEnabled();
    expect(screen.getByRole("button",{name:"试听选段"})).toBeDisabled();
    fireEvent.click(record);
    expect(screen.getByRole("button",{name:"开始录制"})).toHaveFocus();
  });
  it("uses the same keyboard-edited settings and original ranges for audition and capture", async () => {
    const request = vi.spyOn(recordingMix,"getRecordingMix")
      .mockResolvedValue({contentUrl:"http://localhost/mix",durationSeconds:15});
    vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);
    render(<AnalysisWorkspace projectId="p" artifacts={[...tracks,artifact("lyrics","lyrics")]} disabled={false} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole("checkbox",{name:"选择第 1 句"}));
    const prepare = screen.getByRole("slider",{name:"准备时长"});
    fireEvent.keyDown(prepare,{key:"End"});
    expect(prepare).toHaveAttribute("aria-valuenow","10");
    fireEvent.click(screen.getByRole("radio",{name:"混合"}));
    fireEvent.keyDown(screen.getByRole("slider",{name:"人声音量"}),{key:"ArrowRight"});
    const settings = {preparationSeconds:10,preparationAudio:"mixed",vocalPercent:1,includeBacking:true};
    expect(mocks.recordingProps).toHaveBeenLastCalledWith(expect.objectContaining({
      mixSettings:settings, mixPlan:expect.objectContaining({ranges:[{start:1,end:5}],totalSeconds:14}),
    }));
    fireEvent.click(screen.getByRole("button",{name:"试听选段"}));
    await waitFor(()=>expect(request).toHaveBeenCalledWith("p",[{start:1,end:5}],settings,expect.any(AbortSignal)));
    fireEvent.click(screen.getByRole("checkbox",{name:"混入背景音"}));
    expect(screen.getByRole("button",{name:"试听选段"})).toBeDisabled();
    expect(screen.getByRole("button",{name:"去录制"})).toBeEnabled();
  });
  it("keeps selected actions reachable, uses the existing preview, and focuses recording without starting it", async () => {
    mocks.readArtifactJSON.mockImplementation(async (item:ProjectArtifact) => item.kind === "lyrics" ? {...lyrics,segments:[{start:4,end:6,text:"所选歌词",words:[]}]} : waveform);
    vi.spyOn(recordingMix,"getRecordingMix").mockResolvedValue({contentUrl:"http://localhost/mix",durationSeconds:5});
    vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);
    render(<AnalysisWorkspace projectId="p" artifacts={[...tracks,artifact("words","lyrics")]} disabled={false} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByRole("checkbox",{name:"选择第 1 句"}));
    const actions=within(screen.getByRole("region",{name:"歌词备忘录"})).getByRole("toolbar",{name:"选段操作"});
    expect(screen.getByText("已选 1 / 1 句")).toBeVisible();
    fireEvent.click(within(actions).getByRole("button",{name:"试听选段"}));
    await waitFor(()=>expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce());
    expect(screen.getAllByLabelText("时间轴播放器")).toHaveLength(1);
    fireEvent.click(within(actions).getByRole("button",{name:"去录制"}));
    expect(screen.getByRole("button",{name:"开始录制"})).toHaveFocus();
    expect(within(actions).getByRole("button",{name:"手动起止时间"})).toHaveAttribute("aria-expanded","false");
    expect(screen.getByLabelText("片段开始（秒）")).not.toBeVisible();
    fireEvent.click(within(actions).getByRole("button", {name:"手动起止时间"}));
    expect(screen.getByLabelText("片段开始（秒）")).toBeVisible();
    fireEvent.change(screen.getByLabelText("片段开始（秒）"), {target:{value:"6"}});
    expect(mocks.recordingProps).toHaveBeenLastCalledWith(expect.objectContaining({mixPlan:expect.objectContaining({ranges:[{start:6,end:10}]})}));
  });
  it("stops an assembled preview when a mix setting changes without silently playing the new mix",async()=>{
    mocks.readArtifactJSON.mockImplementation(async()=>({...lyrics,segments:[{start:4,end:6,text:"所选歌词",words:[]}]}));
    const read=vi.spyOn(recordingMix,"getRecordingMix").mockResolvedValue({contentUrl:"http://localhost/mix",durationSeconds:5});
    vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);
    render(<AnalysisWorkspace projectId="p" artifacts={[...tracks,artifact("words","lyrics")]} disabled={false} onSaved={vi.fn()}/>);
    fireEvent.click(await screen.findByRole("checkbox",{name:"选择第 1 句"}));
    fireEvent.click(screen.getByRole("button",{name:"试听选段"}));
    await waitFor(()=>expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("radio",{name:"人声"}));
    expect(screen.getByRole("button",{name:"试听选段"})).toBeEnabled();
    expect(read).toHaveBeenCalledOnce();
    expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce();
  });
  it("stops saved recording playback when its workspace is closed", async () => {
    vi.spyOn(processing, "getArtifactPlaybackUrl").mockResolvedValue("http://localhost/recording");
    const recording = {
      ...artifact("take", "recording"),
      metadata: { duration_seconds: 2, has_video: false },
    };
    const view = render(
      <StrictMode><AnalysisWorkspace
        projectId="project"
        artifacts={[audio, recording]}
        disabled={false}
        onSaved={vi.fn()}
      /></StrictMode>,
    );
    fireEvent.click(screen.getByRole("button", { name: "回放与导出" }));
    const playback = await screen.findByLabelText("录制 1 回放");
    expect(playback).toHaveAttribute("src","http://localhost/recording");
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    view.unmount();
    expect(playback).not.toHaveAttribute("src");
  });
  it("stops timeline playback when its workspace is closed", async () => {
    vi.spyOn(processing, "getArtifactPlaybackUrl").mockResolvedValue("http://localhost/audio");
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    const view = render(
      <AudioTimeline
        audio={audio}
        range={{ start: 0, end: 10 }}
        onRangeChange={vi.fn()}
        onTimeChange={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "试听片段" }));
    await waitFor(() =>
      expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce(),
    );
    const playback = screen.getByLabelText("时间轴播放器");
    vi.mocked(HTMLMediaElement.prototype.pause).mockClear();
    view.unmount();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(playback).not.toHaveAttribute("src");
  });
  it("preserves an unsaved lyric correction when waveform and beat artifacts refresh", async () => {
    const initial = [
      audio,
      artifact("lyrics-1", "lyrics"),
      artifact("wave-1", "waveform"),
      artifact("beats-1", "beats"),
    ];
    const view = render(
      <AnalysisWorkspace
        projectId="project"
        artifacts={initial}
        disabled={false}
        onSaved={vi.fn()}
      />,
    );
    await screen.findByRole("button", { name: /原始歌词/ });
    fireEvent.click(screen.getByRole("button", { name: "校正歌词" }));
    fireEvent.change(screen.getByLabelText("第 1 句歌词"), {
      target: { value: "尚未保存的校正" },
    });

    view.rerender(
      <AnalysisWorkspace
        projectId="project"
        artifacts={[
          audio,
          initial[1],
          artifact("wave-2", "waveform"),
          artifact("beats-2", "beats"),
        ]}
        disabled={false}
        onSaved={vi.fn()}
      />,
    );

    expect(mocks.readArtifactJSON).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/BPM/)).not.toBeInTheDocument();
    expect(screen.queryByRole("slider", {name:"波形定位"})).not.toBeInTheDocument();
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("尚未保存的校正");
    expect(
      screen.getByRole("button", { name: "保存歌词与时间" }),
    ).toBeEnabled();
  });
});
