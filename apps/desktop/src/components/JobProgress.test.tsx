import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { AnalysisJob } from "../api/jobs";
import { JobProgress } from "./JobProgress";

const jobFixture: AnalysisJob = {
  id: "analysis-1",
  projectId: "project-1",
  operation: "fingerprint",
  stages: [],
  status: "running",
  progressPercent: 25,
  errorCode: null,
  errorMessage: null,
  stage: {
    id: "stage-1",
    name: "fingerprint",
    status: "running",
    progressPercent: 25,
    bytesDone: 25,
    bytesTotal: 100,
    progressUnit: "bytes",
    unitsDone: 25,
    unitsTotal: 100,
    attempts: [
      { id: "attempt-1", number: 1, status: "running", processId: null },
    ],
  },
};

describe("JobProgress", () => {
  it("keeps compact active progress and cancellation while collapsing successful task prose",()=>{
    const view=render(<JobProgress job={jobFixture} compact onCancel={vi.fn()}/>);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow","25");
    expect(screen.getByRole("button",{name:"取消任务"})).toBeVisible();
    view.rerender(<JobProgress job={{...jobFixture,status:"succeeded",stage:{...jobFixture.stage,status:"succeeded"}}} compact/>);
    expect(screen.getByText("源文件校验完成")).toBeVisible();
    expect(screen.queryByText(/当前校验任务已结束/)).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
  it("ends file preparation explicitly without implying further processing", () => {
    render(
      <JobProgress
        job={{
          ...jobFixture,
          status: "succeeded",
          progressPercent: 100,
          stage: { ...jobFixture.stage, status: "succeeded", bytesDone: 100, progressPercent: 100 },
        }}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("源文件校验完成")).toBeVisible();
    expect(screen.getByText(/当前校验任务已结束/)).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.queryByRole("button", { name: "取消任务" })).not.toBeInTheDocument();
  });

  it("renders persisted byte progress and not a fake animation", () => {
    render(<JobProgress job={jobFixture} />);

    expect(screen.getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "25",
    );
    expect(screen.getByText("25%")).toBeVisible();
    expect(screen.getByText("25 B / 100 B")).toBeVisible();
  });

  it("shows actual separation units separately from the overall stage fraction", () => {
    const processingJob = {
      ...jobFixture,
      operation: "separate" as const,
      progressPercent: 80,
      stages: ["fingerprint_source", "validate_media", "normalize_audio", "prepare_model", "separate_stems"].map((name, index) => ({
        ...jobFixture.stage, id: `stage-${index}`, name,
        status: index < 4 ? "succeeded" as const : "running" as const,
      })),
      stage: {
        ...jobFixture.stage,
        name: "separate_stems",
        progressUnit: "segments" as const,
        unitsDone: 1,
        unitsTotal: 4,
        bytesDone: 0,
        bytesTotal: null,
      },
    };
    render(<JobProgress job={processingJob} />);

    expect(screen.getByText("1 / 4 个片段")).toBeVisible();
    expect(screen.getByText("已完成阶段 4 / 5 · 80%")).toBeVisible();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25");
    expect(screen.queryByText(/实际读取字节/)).not.toBeInTheDocument();
  });

  it("keeps model loading indeterminate until measured units are available", () => {
    const processingJob = {
      ...jobFixture,
      operation: "separate" as const,
      stage: {
        ...jobFixture.stage,
        name: "prepare_model",
        progressPercent: 0,
        progressUnit: "files" as const,
        unitsDone: 0,
        unitsTotal: null,
        bytesDone: 0,
        bytesTotal: null,
      },
    };
    render(<JobProgress job={processingJob} />);

    expect(screen.getByRole("progressbar")).not.toHaveAttribute("value");
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText("等待实际处理进度")).toBeVisible();
  });

  it("offers cancellation only while a job can still be canceled", () => {
    const onCancel = vi.fn();
    const { rerender } = render(
      <JobProgress job={jobFixture} onCancel={onCancel} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    expect(onCancel).toHaveBeenCalledOnce();

    rerender(
      <JobProgress
        job={{ ...jobFixture, status: "succeeded" }}
        onCancel={onCancel}
      />,
    );
    expect(
      screen.queryByRole("button", { name: "取消任务" }),
    ).not.toBeInTheDocument();
  });

  it("shows a stable retry action for failed jobs", () => {
    const onRetry = vi.fn();
    render(
      <JobProgress
        job={{
          ...jobFixture,
          status: "failed",
          errorCode: "source_missing",
          errorMessage: "Source file no longer exists",
        }}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText("source_missing")).not.toBeVisible();
    fireEvent.click(screen.getByText("错误详情"));
    expect(screen.getByText("source_missing")).toBeVisible();
    expect(screen.getByText("找不到源文件，请确认文件仍在原位置后重试。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试任务" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
