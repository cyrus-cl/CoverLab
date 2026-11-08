import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AudioArtifact } from "../api/processing";
import * as processing from "../api/processing";
import { ProjectArtifacts } from "./ProjectArtifacts";
import { claimAudioFocus } from "../recording/audioFocus";

const make=(id:string,kind:AudioArtifact["kind"],analysisId="latest"):AudioArtifact=>({id,analysisId,kind,path:`D:/outputs/${id}.wav`,byteSize:100,sha256:"a".repeat(64),contentUrl:`/api/v1/artifacts/${id}/content`,metadata:{duration_seconds:5,sample_rate:44100,channels:2,codec:"pcm_f32le",container:"wav"}});
const outputs=[make("normalized","normalized_audio"),make("vocals","vocals"),make("instrumental","instrumental")];
const props={loading:false,error:null,onRefresh:vi.fn(),onSelectedAudioChange:vi.fn()};
beforeEach(()=>{vi.spyOn(HTMLMediaElement.prototype,"play").mockResolvedValue(undefined);});
afterEach(()=>vi.restoreAllMocks());

describe("single selected audio result",()=>{
  it("does not steal playback when an earlier file read finishes after another player starts",async()=>{
    let resolve!:(url:string)=>void;
    vi.spyOn(processing,"getArtifactPlaybackUrl").mockReturnValue(new Promise<string>(done=>{resolve=done;}));
    render(<ProjectArtifacts {...props} selectedAudioId="vocals" artifacts={outputs}/>);
    fireEvent.click(screen.getByRole("button",{name:"试听人声"}));
    act(()=>claimAudioFocus(document.createElement("audio")));
    await act(async()=>resolve("http://localhost/late"));
    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled();
    expect(screen.getByRole("button",{name:"试听人声"})).toBeEnabled();
  });
  it("does not replace the media source after requesting the first audition",async()=>{
    vi.spyOn(processing,"getArtifactPlaybackUrl").mockResolvedValue("http://localhost/audio");
    vi.mocked(HTMLMediaElement.prototype.play).mockImplementation(function(this:HTMLMediaElement){return new Promise<void>((resolve,reject)=>{
      const observer=new MutationObserver(()=>{observer.disconnect();reject(new DOMException("play interrupted by a new load","AbortError"));});
      observer.observe(this,{attributes:true,attributeFilter:["src"]});
      setTimeout(()=>{observer.disconnect();resolve();},10);
    });});
    render(<ProjectArtifacts {...props} selectedAudioId="vocals" artifacts={outputs}/>);
    fireEvent.click(screen.getByRole("button",{name:"试听人声"}));
    await screen.findByRole("button",{name:"暂停人声"});
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("keeps stems from earlier successful processing selectable but renders only the selected file",()=>{
    render(<ProjectArtifacts {...props} selectedAudioId="instrumental" artifacts={[make("normalized","normalized_audio","analysis"),make("vocals","vocals","separated"),make("instrumental","instrumental","separated")]} />);
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getByRole("article",{name:"伴奏文件"})).toBeVisible();
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.queryByRole("article",{name:"人声文件"})).not.toBeInTheDocument();
  });
  it("shows only the latest file for each track and controls the shared selection",()=>{
    const change=vi.fn();
    render(<ProjectArtifacts {...props} onSelectedAudioChange={change} selectedAudioId="vocals" artifacts={[...outputs,make("old-vocals","vocals","old")]} />);
    expect(screen.getByRole("button",{name:"试听人声"})).toBeVisible();
    expect(screen.queryByText("vocals.wav")).not.toBeInTheDocument();
    expect(screen.queryByText("文件详情")).not.toBeInTheDocument();
    expect(screen.queryByText("old-vocals.wav")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox",{name:"音轨"}),{target:{value:"normalized"}});
    expect(change).toHaveBeenCalledExactlyOnceWith("normalized");
  });
  it("stops and releases the old file audition when the selected track changes",async()=>{
    const read=vi.spyOn(processing,"getArtifactPlaybackUrl").mockResolvedValue("http://localhost/audio");
    const view=render(<ProjectArtifacts {...props} selectedAudioId="vocals" artifacts={outputs} />);
    fireEvent.click(screen.getByRole("button",{name:"试听人声"}));
    await waitFor(()=>expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce());
    const player=screen.getByLabelText("人声试听");
    view.rerender(<ProjectArtifacts {...props} selectedAudioId="instrumental" artifacts={outputs} />);
    expect(player).not.toHaveAttribute("src");
    expect(read).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText("人声试听")).not.toBeInTheDocument();
  });
  it("locks track choice and stops file audition while recording is active",async()=>{
    vi.spyOn(processing,"getArtifactPlaybackUrl").mockResolvedValue("http://localhost/audio");
    const view=render(<ProjectArtifacts {...props} selectedAudioId="vocals" artifacts={outputs} />);
    fireEvent.click(screen.getByRole("button",{name:"试听人声"}));
    await waitFor(()=>expect(HTMLMediaElement.prototype.play).toHaveBeenCalledOnce());
    view.rerender(<ProjectArtifacts {...props} selectedAudioId="vocals" artifacts={outputs} disabled />);
    expect(screen.getByRole("combobox",{name:"音轨"})).toBeDisabled();
    expect(screen.getByLabelText("人声试听")).not.toHaveAttribute("controls");
    expect(screen.getByRole("button",{name:/试听人声|暂停人声/})).toBeDisabled();
  });
});
