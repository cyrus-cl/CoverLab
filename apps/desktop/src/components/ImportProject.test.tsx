import { getTauriMocks } from "../test/tauri";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImportProject } from "./ImportProject";

const tauriMocks = getTauriMocks();

const projectApi = vi.hoisted(() => ({
  createProject: vi.fn(),
}));

vi.mock("../api/projects", () => ({
  createProject: projectApi.createProject,
}));

const projectFixture = {
  id: "project-1",
  name: "示例 歌曲",
  sourcePath: "D:\\Music\\示例 歌曲.wav",
  sourceSize: 1024,
  sourceMtimeNs: 1,
  sourceSha256: null,
  outputDir: "D:\\AI4Music Projects\\示例 歌曲",
  status: "ready" as const,
  lastOpenedAt: "2026-11-01T12:00:00Z",
  createdAt: "2026-11-01T12:00:00Z",
  updatedAt: "2026-11-01T12:00:00Z",
};

describe("ImportProject", () => {
  beforeEach(() => {
    tauriMocks.open.mockReset();
    projectApi.createProject.mockReset();
  });

  it("imports a selected local file without an upload step", async () => {
    tauriMocks.open
      .mockResolvedValueOnce("D:\\Music\\示例 歌曲.wav")
      .mockResolvedValueOnce("D:\\AI4Music Projects");
    projectApi.createProject.mockResolvedValue(projectFixture);
    const onImported = vi.fn();
    render(<ImportProject onImported={onImported} />);

    fireEvent.click(screen.getByRole("button", { name: "选择本机文件" }));

    await waitFor(() => {
      expect(projectApi.createProject).toHaveBeenCalledWith({
        sourcePath: "D:\\Music\\示例 歌曲.wav",
        outputDir: "D:\\AI4Music Projects",
      });
    });
    expect(onImported).toHaveBeenCalledWith(projectFixture);
    expect(screen.queryByText(/上传/)).not.toBeInTheDocument();
  });

  it("stops cleanly when either native dialog is canceled", async () => {
    tauriMocks.open.mockResolvedValueOnce(null);
    render(<ImportProject />);

    fireEvent.click(screen.getByRole("button", { name: "选择本机文件" }));

    await waitFor(() => expect(tauriMocks.open).toHaveBeenCalledTimes(1));
    expect(projectApi.createProject).not.toHaveBeenCalled();
  });

  it("shows a stable error when the native dialog cannot open", async () => {
    tauriMocks.open.mockRejectedValueOnce(new Error("dialog unavailable"));
    render(<ImportProject />);

    fireEvent.click(screen.getByRole("button", { name: "选择本机文件" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "无法创建本机项目，请检查文件与输出位置",
    );
    expect(projectApi.createProject).not.toHaveBeenCalled();
  });
});
