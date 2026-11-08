import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ProjectSummary } from "./api/projects";
const api=vi.hoisted(()=>({open:vi.fn(),createProject:vi.fn(),createAnalysis:vi.fn()}));
vi.mock("@tauri-apps/plugin-dialog",()=>({open:api.open}));

const base={sourceSize:1024,sourceMtimeNs:1,sourceSha256:null,status:"complete" as const,lastOpenedAt:"2026-11-05T10:00:00Z",createdAt:"2026-11-05T10:00:00Z",updatedAt:"2026-11-05T10:00:00Z"};
const projects=[{...base,id:"p1",name:"第一首",sourcePath:"D:/第一首.mp3",outputDir:"D:/项目/第一首"},{...base,id:"p2",name:"第二首",sourcePath:"D:/第二首.mp3",outputDir:"D:/项目/第二首"}];
const imported={...base,id:"p3",name:"新导入",sourcePath:"D:/新导入.mp3",outputDir:"D:/项目/新导入"};
vi.mock("./api/projects",()=>({listProjects:async()=>projects,createProject:api.createProject}));
vi.mock("./api/jobs",()=>({getLatestAnalysis:async()=>null,createAnalysis:api.createAnalysis,createProcessing:vi.fn(),cancelAnalysis:vi.fn(),retryAnalysis:vi.fn()}));
vi.mock("./api/processing",()=>({getProjectArtifacts:async()=>[],getRuntime:async()=>({media:{available:true},separation:{available:true,model_cached:true,model_id:"m",revision:"r",model_sha256:"",device:"cpu",cpu_supported:true}})}));
vi.mock("./hooks/useJobEvents",()=>({useJobEvents:()=>({job:null,events:[],streamState:"idle",errorMessage:null,refetch:vi.fn()})}));
vi.mock("./components/ProjectWorkspace",()=>({ProjectWorkspace:({project,onDirtyChange}:{project:ProjectSummary;onDirtyChange:(dirty:boolean)=>void})=><section className="lyrics-workspace"><h2>{project.name}</h2><textarea aria-label="未保存歌词" onChange={()=>onDirtyChange(true)}/><button type="button">保存歌词与时间</button></section>}));

describe("unsaved project switching",()=>{
  beforeEach(()=>{
    localStorage.clear();
    api.open.mockReset().mockResolvedValueOnce("D:/新导入.mp3").mockResolvedValueOnce("D:/项目/新导入");
    api.createProject.mockReset().mockResolvedValue(imported);
    api.createAnalysis.mockReset().mockResolvedValue({id:"verify-new",projectId:imported.id,operation:"fingerprint",status:"queued",progressPercent:0,errorCode:null,errorMessage:null,stages:[],stage:{id:"stage-new",name:"fingerprint_source",status:"queued",progressPercent:0,bytesDone:0,bytesTotal:null,unitsDone:0,unitsTotal:null,attempts:[]}});
  });
  it.each(["继续编辑","去保存"])("keeps the restored workspace and its draft when importing then choosing %s",async(action)=>{
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const view=render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
    await screen.findByRole("heading",{name:"第一首"});
    const draft=screen.getByRole("textbox",{name:"未保存歌词"});
    fireEvent.change(draft,{target:{value:"这份校正还没有保存"}});
    fireEvent.click(screen.getByRole("button",{name:"选择本机文件"}));
    expect(await screen.findByRole("dialog",{name:"当前内容尚未保存"})).toBeVisible();
    expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
    expect(screen.getByRole("textbox",{name:"未保存歌词"})).toBe(draft);
    fireEvent.click(screen.getByRole("button",{name:action}));
    if(action==="去保存")await waitFor(()=>expect(screen.getByRole("button",{name:"保存歌词与时间"})).toHaveFocus());
    expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
    expect(draft).toHaveValue("这份校正还没有保存");
    expect(screen.getByRole("textbox",{name:"未保存歌词"})).toBe(draft);
    expect(api.createAnalysis).not.toHaveBeenCalled();
    view.unmount();client.clear();
  });
  it("switches a dirty restored workspace to an imported project only after explicit discard",async()=>{
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const view=render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
    await screen.findByRole("heading",{name:"第一首"});
    const draft=screen.getByRole("textbox",{name:"未保存歌词"});
    fireEvent.change(draft,{target:{value:"明确放弃前保留"}});
    fireEvent.click(screen.getByRole("button",{name:"选择本机文件"}));
    await screen.findByRole("dialog",{name:"当前内容尚未保存"});
    expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
    expect(draft).toHaveValue("明确放弃前保留");
    fireEvent.click(screen.getByRole("button",{name:"放弃并切换"}));
    expect(await screen.findByRole("heading",{name:"新导入"})).toBeVisible();
    expect(screen.getByRole("textbox",{name:"未保存歌词"})).not.toBe(draft);
    await waitFor(()=>expect(api.createAnalysis).toHaveBeenCalledOnce());
    expect(api.createAnalysis.mock.calls[0][0]).toBe(imported.id);
    view.unmount();client.clear();
  });
  it("protects edits made while the native import request is still pending",async()=>{
    let finish!:(project:ProjectSummary)=>void;
    api.createProject.mockImplementationOnce(()=>new Promise<ProjectSummary>(resolve=>{finish=resolve;}));
    const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
    const view=render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
    await screen.findByRole("heading",{name:"第一首"});
    fireEvent.click(screen.getByRole("button",{name:"选择本机文件"}));
    await waitFor(()=>expect(api.createProject).toHaveBeenCalledOnce());
    const draft=screen.getByRole("textbox",{name:"未保存歌词"});
    fireEvent.change(draft,{target:{value:"导入等待期间新增的校正"}});
    finish(imported);
    expect(await screen.findByRole("dialog",{name:"当前内容尚未保存"})).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"继续编辑"}));
    expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
    expect(screen.getByRole("textbox",{name:"未保存歌词"})).toBe(draft);
    expect(draft).toHaveValue("导入等待期间新增的校正");
    expect(api.createAnalysis).not.toHaveBeenCalled();
    view.unmount();client.clear();
  });
  it("lets the user keep editing, locate save, or explicitly discard before switching",async()=>{
    const nativeConfirm=vi.spyOn(window,"confirm").mockReturnValue(false);
    try {
      const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
      render(<QueryClientProvider client={client}><App/></QueryClientProvider>);
      await screen.findByRole("heading",{name:"第一首"});
      fireEvent.change(screen.getByRole("textbox",{name:"未保存歌词"}),{target:{value:"校正"}});
      fireEvent.click(screen.getByRole("button",{name:/第二首/}));
      expect(screen.getByRole("dialog",{name:"当前内容尚未保存"})).toBeVisible();
      fireEvent.click(screen.getByRole("button",{name:"继续编辑"}));
      expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
      fireEvent.click(screen.getByRole("button",{name:/第二首/}));
      fireEvent.click(screen.getByRole("button",{name:"去保存"}));
      await waitFor(()=>expect(screen.getByRole("button",{name:"保存歌词与时间"})).toHaveFocus());
      expect(screen.getByRole("heading",{name:"第一首"})).toBeVisible();
      fireEvent.click(screen.getByRole("button",{name:/第二首/}));
      fireEvent.click(screen.getByRole("button",{name:"放弃并切换"}));
      expect(await screen.findByRole("heading",{name:"第二首"})).toBeVisible();
      expect(nativeConfirm).not.toHaveBeenCalled();
    } finally {nativeConfirm.mockRestore();}
  });
});
