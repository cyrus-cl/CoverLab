import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProjectArtifact, RuntimeStatus } from "../api/processing";
import { ProjectWorkspace } from "./ProjectWorkspace";

vi.mock("./AnalysisWorkspace", () => ({AnalysisWorkspace: ({onRecordingChange}:{onRecordingChange:(active:boolean)=>void}) => <section className="lyrics-workspace"><input type="checkbox" data-guide="lyrics" aria-label="选择第一句" /><button type="button" onClick={()=>onRecordingChange(true)}>模拟录制状态</button></section>}));
const project = {id:"p",name:"示例歌曲",sourcePath:"C:/fixtures/示例歌曲.mp3",sourceSize:1024,sourceMtimeNs:1,sourceSha256:null,outputDir:"C:/fixtures/projects/示例歌曲-2",status:"complete" as const,lastOpenedAt:"2026-11-05T10:00:00Z",createdAt:"2026-11-05T10:00:00Z",updatedAt:"2026-11-05T10:00:00Z"};
const runtime:RuntimeStatus = {media:{available:true},separation:{available:true,model_cached:true,model_id:"htdemucs",revision:"pinned",model_sha256:"a".repeat(64),device:"cpu",cpu_supported:true},analysis:{available:true},lyrics:{available:true,model_cached:true,model_id:"small",revision:"pinned"}};
const output = (kind:ProjectArtifact["kind"]):ProjectArtifact => ({id:kind,kind,analysisId:"job",path:`D:/${kind}`,byteSize:1,sha256:"a".repeat(64),contentUrl:`/api/v1/artifacts/${kind}/content`,metadata:{}});
const audioMetadata = {duration_seconds:10,sample_rate:44100,channels:2,codec:"pcm",container:"wav"};
const props = {project,job:null,loadingJob:false,actionPending:false,actionError:null,runtime,runtimeLoading:false,runtimeError:null,artifactsLoading:false,artifactsError:null,onRefreshArtifacts:vi.fn()};

describe("stateful preparation", () => {
  it("requires confirmation before a cloud upload and can cancel it", () => {
    const process = vi.fn();
    const cloud = {...runtime, lyrics: {available:true, cloud:true, model_cached:false,
      model_id:"qwen3-asr-flash-filetrans", revision:"cloud", download_bytes:0}};
    render(<ProjectWorkspace {...props} runtime={cloud} artifacts={[]} onProcess={process} />);
    const prepare = screen.getByRole("button", {name:"生成伴奏和歌词"});
    fireEvent.click(prepare);
    expect(process).not.toHaveBeenCalled();
    expect(screen.getByRole("group", {name:"云端歌词确认"})).toHaveTextContent("CoverLab 云端服务及阿里云百炼");
    fireEvent.click(screen.getByRole("button", {name:"取消"}));
    expect(process).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name:"生成伴奏和歌词"}));
    fireEvent.click(screen.getByRole("button", {name:"上传分离人声以识别歌词"}));
    expect(process).toHaveBeenCalledExactlyOnceWith("singing", true);
    expect(screen.queryByText(/下载所需模型/)).not.toBeInTheDocument();
  });
  it("keeps one full-track selector and locks it during capture",()=>{
    const audioOutputs=["normalized_audio","vocals","instrumental"].map(kind=>({...output(kind as ProjectArtifact["kind"]),metadata:audioMetadata}));
    render(<ProjectWorkspace {...props} artifacts={audioOutputs} onProcess={vi.fn()}/>);
    expect(screen.queryByRole("navigation",{name:"翻唱制作阶段"})).not.toBeInTheDocument();
    expect(screen.getAllByRole("combobox",{name:"音轨"})).toHaveLength(1);
    expect(screen.getByRole("article",{name:"伴奏文件"})).toBeVisible();
    fireEvent.change(screen.getByRole("combobox",{name:"音轨"}),{target:{value:"vocals"}});
    expect(screen.getByRole("article",{name:"人声文件"})).toBeVisible();
    expect(screen.getAllByRole("article")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button",{name:"模拟录制状态"}));
    expect(screen.getByRole("combobox",{name:"音轨"})).toBeDisabled();
  });
  it("preserves existing lyrics when all generation results are ready", () => {
    const process = vi.fn();
    const old = {...output("lyrics"),metadata:{model_id:"faster-whisper-tiny",machine_draft:true}};
    render(<ProjectWorkspace {...props} artifacts={[{...output("instrumental"), metadata:audioMetadata}, {...output("vocals"), metadata:audioMetadata}, old]} onProcess={process} />);
    expect(screen.getByRole("checkbox", {name:"选择第一句"})).toBeVisible();
    expect(screen.queryByRole("button", {name:"生成伴奏和歌词"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button", {name:"升级歌词识别"})).not.toBeInTheDocument();
    expect(process).not.toHaveBeenCalled();
  });
  it("fills missing lyrics from existing vocals without repeating separation", () => {
    const process = vi.fn();
    const tracks = ["vocals", "instrumental"].map(kind => ({
      ...output(kind as ProjectArtifact["kind"]),
      metadata: audioMetadata,
    }));
    render(<ProjectWorkspace {...props} artifacts={tracks} onProcess={process} />);
    fireEvent.click(screen.getByRole("button", {name:"生成伴奏和歌词"}));
    expect(process).toHaveBeenCalledExactlyOnceWith("lyrics");
  });
  it("uses actual outputs rather than a generic complete status to select the primary action", () => {
    const process = vi.fn();
    render(<ProjectWorkspace {...props} artifacts={[]} onProcess={process} />);
    fireEvent.click(screen.getByRole("button", {name:"生成伴奏和歌词"}));
    expect(process).toHaveBeenCalledExactlyOnceWith("singing");
  });
});
