import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LyricsEditor } from "./LyricsEditor";
import type { LyricsDocument } from "../api/analysis";
import { saveLyrics, importLRC } from "../api/analysis";

vi.mock("../api/analysis", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  saveLyrics: vi.fn().mockResolvedValue([]),
  importLRC: vi.fn().mockResolvedValue([]),
}));
const doc: LyricsDocument = {
  schemaVersion: 1,
  durationSeconds: 10,
  language: "zh",
  segments: [{ start: 1, end: 5, text: "第一句歌词", words: [] }],
};

describe("LyricsEditor", () => {
  it("keeps an unsaved batch edit until discarding it is explicitly confirmed", () => {
    render(<LyricsEditor projectId="p" document={doc} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button",{name:"校正歌词"}));
    fireEvent.change(screen.getByLabelText("第 1 句歌词"),{target:{value:"未保存的歌词"}});
    fireEvent.click(screen.getByRole("button",{name:"退出校正"}));
    expect(screen.getByRole("alertdialog",{name:"保留歌词校正"})).toBeVisible();
    fireEvent.click(screen.getByRole("button",{name:"继续校正"}));
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("未保存的歌词");
    fireEvent.click(screen.getByRole("button",{name:"退出校正"}));
    fireEvent.click(screen.getByRole("button",{name:"放弃本次校正"}));
    expect(screen.getByRole("button",{name:"校正歌词"})).toBeVisible();
  });
  it("does not select a long hallucinated loop through select-all without confirmation", () => {
    const select=vi.fn();
    const repeated={...doc,segments:[...doc.segments,{start:6,end:9,text:"作曲 ".repeat(100),words:[]}]};
    render(<LyricsEditor projectId="p" document={repeated} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} selectedIndices={[]} onSelectionChange={select} />);
    expect(screen.getByRole("checkbox",{name:"选择第 2 句"})).toBeDisabled();
    fireEvent.click(screen.getByRole("button",{name:"全选歌词"}));
    expect(select).toHaveBeenLastCalledWith([0]);
    fireEvent.click(screen.getByRole("button",{name:"确认保留第 2 句"}));
    expect(screen.getByRole("checkbox",{name:"选择第 2 句"})).not.toBeDisabled();
  });
  it("supports batch replacement, simplification, undo and reset without changing timestamps", () => {
    const traditional = {...doc, segments:[{...doc.segments[0],text:"陽光與謊言"}]};
    render(<LyricsEditor projectId="p" document={traditional} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button",{name:"校正歌词"}));
    fireEvent.click(screen.getByRole("button",{name:"全文转简体"}));
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("阳光与谎言");
    fireEvent.change(screen.getByLabelText("查找文字"),{target:{value:"谎言"}});
    fireEvent.change(screen.getByLabelText("替换为"),{target:{value:"承诺"}});
    fireEvent.click(screen.getByRole("button",{name:"全部替换"}));
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("阳光与承诺");
    fireEvent.click(screen.getByRole("button",{name:"撤销"}));
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("阳光与谎言");
    fireEvent.click(screen.getByRole("button",{name:"重置本次校正"}));
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("陽光與謊言");
    expect(screen.getByLabelText("第 1 句开始")).toHaveValue(1);
  });
  it("filters to selected lines without renumbering the underlying selection", () => {
    const two={...doc,segments:[...doc.segments,{start:6,end:9,text:"第二句歌词",words:[]}]};
    render(<LyricsEditor projectId="p" document={two} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} selectedIndices={[1]} onSelectionChange={vi.fn()} />);
    fireEvent.click(screen.getByRole("checkbox",{name:"只看已选"}));
    expect(screen.queryByRole("checkbox",{name:"选择第 1 句"})).not.toBeInTheDocument();
    expect(screen.getByRole("checkbox",{name:"选择第 2 句"})).toBeChecked();
  });
  it("reads a real LRC file and submits its timestamps", async () => {
    render(<LyricsEditor projectId="p" document={doc} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} />);
    const file=new File(["[00:01.00]导入的一句"],"歌词.lrc",{type:"text/plain"});
    fireEvent.change(screen.getByLabelText("选择 LRC 文件"),{target:{files:[file]}});
    expect(await screen.findByDisplayValue("[00:01.00]导入的一句")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button",{name:"导入并保存"}));
    expect(importLRC).toHaveBeenCalledWith("p","[00:01.00]导入的一句",10);
  });
  it("marks only uncertain words actually present in the saved recognition text without inventing accuracy scores", () => {
    const recognized = {...doc,segments:[{...doc.segments[0],words:[{start:1,end:2,word:"第一句",probability:.95},{start:2,end:5,word:"歌词",probability:.3}]}]};
    render(<LyricsEditor projectId="p" document={recognized} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} />);
    const review = screen.getByText("歌词");
    expect(review.tagName).toBe("MARK");
    expect(review).toHaveAttribute("title", "建议校对：识别模型对此处不确定");
    expect(screen.getByRole("button", {name:/第一句歌词/})).toBeVisible();
    expect(screen.queryByText(/30%|95%/)).not.toBeInTheDocument();
  });
  it("keeps uncertain repeated words at their actual occurrence and preserves punctuation", () => {
    const recognized = {...doc,segments:[{...doc.segments[0],text:"爱，爱。",words:[{start:1,end:2,word:" 爱",probability:.95},{start:2,end:5,word:"爱",probability:.3}]}]};
    const {container} = render(<LyricsEditor projectId="p" document={recognized} artifacts={[]} currentTime={2} onSelect={vi.fn()} onSaved={vi.fn()} />);
    const line = container.querySelector(".timed-lyrics button > span")!;
    expect(line.textContent).toBe("爱，爱。");
    expect(line.innerHTML).toMatch(/^爱，<mark/);
  });
  it("locks text seeking as well as checkboxes while capture is active", () => {
    const select = vi.fn();
    render(<LyricsEditor projectId="p" document={doc} artifacts={[]} currentTime={2} onSelect={select} onSaved={vi.fn()} disabled selectedIndices={[]} onSelectionChange={vi.fn()} />);
    const line = screen.getByRole("button", { name: /第一句歌词/ });
    expect(line).toBeDisabled(); fireEvent.click(line);
    expect(select).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "选择第 1 句" })).toBeDisabled();
  });
  it("selects multiple lines independently and clears the set", () => {
    const select = vi.fn();
    const two = { ...doc, segments: [...doc.segments, { start: 6, end: 9, text: "第二句歌词", words: [] }] };
    const props = { projectId: "p", document: two, artifacts: [], currentTime: 2, onSelect: vi.fn(), onSaved: vi.fn(), onSelectionChange: select };
    const view = render(<LyricsEditor {...props} selectedIndices={[]} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择第 1 句" }));
    expect(select).toHaveBeenLastCalledWith([0]);
    view.rerender(<LyricsEditor {...props} selectedIndices={[0]} />);
    fireEvent.click(screen.getByRole("checkbox", { name: "选择第 2 句" }));
    expect(select).toHaveBeenLastCalledWith([0, 1]);
    fireEvent.click(screen.getByRole("button", { name: "清空选择" }));
    expect(select).toHaveBeenLastCalledWith([]);
  });
  it("locks text and timestamp edits while the submitted revision is saving", () => {
    vi.mocked(saveLyrics).mockReturnValueOnce(new Promise(() => {}));
    render(
      <LyricsEditor
        projectId="p"
        document={doc}
        artifacts={[]}
        currentTime={3}
        onSelect={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "校正歌词" }));
    fireEvent.click(screen.getByRole("button", { name: "保存歌词与时间" }));
    expect(screen.getByLabelText("第 1 句歌词")).toBeDisabled();
    expect(screen.getByLabelText("第 1 句开始")).toBeDisabled();
  });
  it("selects actual timed lines as playback/recording ranges", () => {
    const select = vi.fn();
    render(
      <LyricsEditor
        projectId="p"
        document={doc}
        artifacts={[]}
        currentTime={2}
        onSelect={select}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /第一句歌词/ }));
    expect(select).toHaveBeenCalledWith({ start: 1, end: 5 });
  });
  it("splits text at the chosen caret and actual playhead", () => {
    render(
      <LyricsEditor
        projectId="p"
        document={doc}
        artifacts={[]}
        currentTime={3}
        onSelect={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "校正歌词" }));
    const text = screen.getByLabelText("第 1 句歌词") as HTMLTextAreaElement;
    text.setSelectionRange(3, 3);
    fireEvent.click(
      screen.getByRole("button", { name: "在播放位置切分第 1 句" }),
    );
    expect(screen.getByLabelText("第 1 句歌词")).toHaveValue("第一句");
    expect(screen.getByLabelText("第 2 句歌词")).toHaveValue("歌词");
    expect(screen.getByLabelText("第 1 句结束")).toHaveValue(3);
    expect(screen.getByLabelText("第 2 句开始")).toHaveValue(3);
  });
});
