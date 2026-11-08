import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EventRail } from "./EventRail";
describe("event summaries",()=>{
  it("aggregates repeated historical progress instead of filling the result view with stale processing rows",()=>{
    const events=[1,2,3].map(sequence=>({sequence,type:"STAGE_PROGRESS",message:"progress",createdAt:"2026-11-05T10:00:00Z",payload:{stageName:"transcribe_lyrics"}}));
    render(<EventRail events={events} streamState="idle" errorMessage={null} runtime={null} runtimeLoading={false} runtimeError={null} onRefreshRuntime={vi.fn()} />);
    expect(screen.getAllByText("合并 3 条记录")).toHaveLength(2);
    for(const label of screen.getAllByText("合并 3 条记录"))expect(label).not.toBeVisible();
  });
});
