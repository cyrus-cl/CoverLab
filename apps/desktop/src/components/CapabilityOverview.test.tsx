import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ProjectArtifact } from "../api/processing";
import { CapabilityOverview } from "./CapabilityOverview";
const output = (kind:ProjectArtifact["kind"]):ProjectArtifact => ({id:kind,kind,analysisId:"job",path:kind,byteSize:1,sha256:"a".repeat(64),contentUrl:`/api/v1/artifacts/${kind}/content`,metadata:{}});
describe("contextual results", () => {
  it("replaces generic copy with actual completed results and the next action", () => {
    const navigate=vi.fn();
    render(<CapabilityOverview runtime={null} runtimeLoading={false} runtimeError={null} onRefreshRuntime={vi.fn()} hasProject artifacts={[output("instrumental"),output("lyrics")]} onNavigate={navigate} />);
    expect(screen.getByRole("heading", {name:"下一步：选择歌词"})).toBeVisible();
    expect(screen.getByText("伴奏、歌词已就绪")).toBeVisible();
    fireEvent.click(screen.getByRole("button", {name:"去选歌词"}));
    expect(navigate).toHaveBeenCalledWith("lyrics");
    expect(screen.queryByText(/从一段伴奏开始/)).not.toBeInTheDocument();
  });
});
