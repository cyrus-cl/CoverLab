import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ProjectSidebar } from "./ProjectSidebar";
const base = {name:"示例歌曲",sourcePath:"C:/fixtures/示例歌曲.mp3",sourceSize:1024,sourceMtimeNs:1,sourceSha256:null,status:"complete" as const,lastOpenedAt:"2026-11-05T10:00:00Z",createdAt:"2026-11-05T10:00:00Z",updatedAt:"2026-11-05T10:00:00Z"};
const projects = [{...base,id:"p1",outputDir:"C:/fixtures/projects/示例歌曲"},{...base,id:"p2",outputDir:"C:/fixtures/projects/示例歌曲-2"}];
describe("project identity", () => {
  it("distinguishes duplicate names by their project folder and exposes the full location", () => {
    render(<ProjectSidebar projects={projects} selectedId="p1" onSelect={vi.fn()} onImported={vi.fn()} loading={false} />);
    expect(screen.queryByText("项目与任务记录自动保存")).not.toBeInTheDocument();
    expect(screen.queryByText("示例歌曲.mp3")).not.toBeInTheDocument();
    expect(screen.getByText("示例歌曲-2", {exact:false})).toBeVisible();
    const second = screen.getByRole("button", {name:/示例歌曲-2/});
    expect(second).toHaveAttribute("title", "C:/fixtures/projects/示例歌曲-2");
    fireEvent.change(screen.getByRole("searchbox", {name:"搜索项目"}), {target:{value:"示例歌曲-2"}});
    expect(screen.queryByRole("button", {name:/示例歌曲 ·/})).not.toBeInTheDocument();
    expect(screen.getByRole("button", {name:/示例歌曲-2/})).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name:"清空项目搜索"}));
    expect(screen.getAllByRole("button", {pressed:false}).length).toBeGreaterThan(0);
  });
});
