import { getTauriMocks } from "./test/tauri";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

getTauriMocks();

const projectApi = vi.hoisted(() => ({
  listProjects: vi.fn(),
  getProject: vi.fn(),
}));
const jobApi = vi.hoisted(() => ({
  createAnalysis: vi.fn(),
  createProcessing: vi.fn(),
  getLatestAnalysis: vi.fn(),
  cancelAnalysis: vi.fn(),
  retryAnalysis: vi.fn(),
}));
const processingApi = vi.hoisted(() => ({
  getRuntime: vi.fn(),
  getProjectArtifacts: vi.fn(),
  getArtifactPlaybackUrl: vi.fn(),
  getArtifactDownloadUrl: vi.fn(),
}));

vi.mock("./api/projects", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/projects")>()),
  listProjects: projectApi.listProjects,
  getProject: projectApi.getProject,
}));
vi.mock("./api/jobs", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api/jobs")>()),
  createAnalysis: jobApi.createAnalysis,
  createProcessing: jobApi.createProcessing,
  getLatestAnalysis: jobApi.getLatestAnalysis,
  cancelAnalysis: jobApi.cancelAnalysis,
  retryAnalysis: jobApi.retryAnalysis,
}));
vi.mock("./api/processing", async importOriginal => ({...await importOriginal<typeof import("./api/processing")>(),...processingApi}));
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

const runtimeFixture = {
  media: { available: true },
  separation: {
    available: true,
    model_id: "htdemucs",
    revision: "test-revision",
    model_sha256: "a".repeat(64),
    model_cached: false,
    device: "auto",
    cpu_supported: true,
  },
  analysis: { available: true },
  lyrics: { available: true, model_cached: false, model_id: "lyrics", revision: "test" },
};

const processingJobFixture = {
  id: "processing-1",
  projectId: "project-1",
  operation: "separate",
  status: "queued",
  progressPercent: 0,
  errorCode: null,
  errorMessage: null,
  stages: [],
  stage: {
    id: "stage-process-1", name: "fingerprint_source", status: "queued",
    progressPercent: 0, bytesDone: 0, bytesTotal: null,
    progressUnit: "bytes", unitsDone: 0, unitsTotal: null, attempts: [],
  },
};

describe("App", () => {
  beforeEach(() => {
    HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
    window.localStorage.clear();
    projectApi.listProjects.mockReset().mockResolvedValue([]);
    projectApi.getProject.mockReset();
    jobApi.createAnalysis.mockReset();
    jobApi.createProcessing.mockReset().mockResolvedValue(processingJobFixture);
    jobApi.getLatestAnalysis.mockReset().mockResolvedValue(null);
    jobApi.cancelAnalysis.mockReset();
    jobApi.retryAnalysis.mockReset();
    processingApi.getRuntime.mockReset().mockResolvedValue(runtimeFixture);
    processingApi.getProjectArtifacts.mockReset().mockResolvedValue([]);
    processingApi.getArtifactPlaybackUrl.mockReset().mockResolvedValue("http://localhost/preview");
    processingApi.getArtifactDownloadUrl.mockReset().mockResolvedValue("http://localhost/download");
  });

  it("opens directly as a tool without first-run guide, help entry or workflow navigation", async () => {
    renderApp();
    await screen.findByRole("heading",{name:"选择本机音频"});
    expect(screen.queryByRole("region",{name:"翻唱上手引导"})).not.toBeInTheDocument();
    expect(screen.queryByRole("button",{name:"上手引导"})).not.toBeInTheDocument();
    expect(screen.queryByRole("navigation",{name:"翻唱制作阶段"})).not.toBeInTheDocument();
    expect(jobApi.createProcessing).not.toHaveBeenCalled();
    expect(jobApi.createAnalysis).not.toHaveBeenCalled();
  });

  it("opens the empty workspace with the import action and without a separate status bar", async () => {
    renderApp();

    expect(screen.getByRole("heading", { name: "CoverLab" })).toBeVisible();
    expect(await screen.findByRole("heading", { name: "选择本机音频" })).toBeVisible();
    expect(screen.getByRole("button", { name: "选择本机文件" })).toBeVisible();
    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
    expect(screen.queryByText("文件与项目详情")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开始录制|生成伴奏和歌词|上传/ })).not.toBeInTheDocument();
  });

  it("restores persisted projects without displaying project file details", async () => {
    projectApi.listProjects.mockResolvedValue([projectFixture]);
    renderApp();

    expect(await screen.findByRole("heading", { name: "示例 歌曲.wav" })).toBeVisible();
    expect(screen.queryByText("文件与项目详情")).not.toBeInTheDocument();
    expect(screen.queryByText(projectFixture.sourcePath)).not.toBeInTheDocument();
    expect(jobApi.getLatestAnalysis).toHaveBeenCalledWith("project-1");
  });

  it("generates accompaniment and lyrics with one action", async () => {
    projectApi.listProjects.mockResolvedValue([projectFixture]);
    renderApp();

    const generate = await screen.findByRole("button", { name: "生成伴奏和歌词" });
    await waitFor(() => expect(generate).toBeEnabled());
    expect(jobApi.createProcessing).not.toHaveBeenCalled();
    expect(screen.queryByText("单项处理")).not.toBeInTheDocument();
    expect(screen.queryByText(/下载所需模型/)).not.toBeInTheDocument();
    fireEvent.click(generate);

    await waitFor(() => expect(jobApi.createProcessing).toHaveBeenCalledWith("project-1", "singing"));
    expect(await screen.findByRole("button", { name: "正在生成…" })).toBeDisabled();
  });

  it.each([["cloud_timeout", true], ["cloud_task_not_found", false]])(
    "reuses a failed cloud job when its server task can continue (%s)", async (errorCode, reuse) => {
      projectApi.listProjects.mockResolvedValue([projectFixture]);
      processingApi.getRuntime.mockResolvedValue({
        ...runtimeFixture, lyrics: {...runtimeFixture.lyrics, cloud: true},
      });
      const failed = {...processingJobFixture, operation: "singing", status: "failed", errorCode, errorMessage: "生成失败，请重试。"};
      jobApi.getLatestAnalysis.mockResolvedValue(failed);
      jobApi.retryAnalysis.mockResolvedValue({...failed, status: "queued"});
      renderApp();

      const generate = await screen.findByRole("button", {name: "生成伴奏和歌词"});
      await waitFor(() => expect(generate).toBeEnabled());
      expect(await screen.findByRole("alert")).toHaveTextContent(failed.errorMessage);
      fireEvent.click(generate);
      expect(jobApi.createProcessing).not.toHaveBeenCalled();
      expect(jobApi.retryAnalysis).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", {name: "上传分离人声以识别歌词"}));
      await waitFor(() => reuse
        ? expect(jobApi.retryAnalysis).toHaveBeenCalledWith(failed.id)
        : expect(jobApi.createProcessing).toHaveBeenCalledWith("project-1", "singing", true));
    },
  );

  it("surfaces an actionable error when generation is unavailable", async () => {
    projectApi.listProjects.mockResolvedValue([projectFixture]);
    processingApi.getRuntime.mockResolvedValue({
      ...runtimeFixture,
      media: { available: false, errorCode: "ffmpeg_missing" },
    });
    renderApp();

    const generate = await screen.findByRole("button", { name: "生成伴奏和歌词" });
    expect(generate).toBeDisabled();
    expect(await screen.findByRole("alert")).toHaveTextContent("未检测到 FFmpeg");
  });

  it("previews and downloads a persisted audio artifact and clears its media source", async () => {
    projectApi.listProjects.mockResolvedValue([projectFixture]);
    processingApi.getProjectArtifacts.mockResolvedValue([{
      id: "artifact-vocals", analysisId: "completed-processing", kind: "vocals",
      path: "D:\\AI4Music Projects\\示例 歌曲\\vocals.wav",
      byteSize: 4096, sha256: "b".repeat(64),
      contentUrl: "/api/v1/artifacts/artifact-vocals/content",
      metadata: { duration_seconds: 8.5, sample_rate: 44100, channels: 2, codec: "pcm_s16le", container: "wav", device: "cpu", model_id: "htdemucs" },
    }]);
    const { unmount } = renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "试听人声" }));
    const audio = await screen.findByLabelText("人声试听");
    expect(audio).toHaveAttribute("src", "http://localhost/preview");
    expect(audio).toHaveAttribute("controls");
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      fireEvent.click(screen.getByRole("button", { name: "下载人声" }));
      await waitFor(() => expect(click).toHaveBeenCalledOnce());
      expect(click.mock.instances[0]).toHaveAttribute("href", "http://localhost/download");
      expect(click.mock.instances[0]).toHaveAttribute("download", "vocals.wav");
      expect(processingApi.getArtifactPlaybackUrl).toHaveBeenCalledOnce();
    } finally {
      click.mockRestore();
    }

    unmount();
    expect(audio).not.toHaveAttribute("src");

  });

  it("never renders a task that belongs to another selected project", async () => {
    projectApi.listProjects.mockResolvedValue([projectFixture]);
    jobApi.getLatestAnalysis.mockResolvedValue({
      ...processingJobFixture,
      projectId: "project-old",
      status: "failed",
      errorMessage: "另一个项目的错误",
    });
    renderApp();

    const generate = await screen.findByRole("button", { name: "生成伴奏和歌词" });
    await waitFor(() => expect(generate).toBeEnabled());
    expect(screen.queryByText("另一个项目的错误")).not.toBeInTheDocument();
  });
});

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}
