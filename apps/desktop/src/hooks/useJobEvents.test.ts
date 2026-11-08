import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalysisJob } from "../api/jobs";
import { parseSseFrames, useJobEvents } from "./useJobEvents";

const apiMocks = vi.hoisted(() => ({
  getAnalysis: vi.fn(),
  getEngineConnection: vi.fn(),
}));

vi.mock("../api/engine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/engine")>();
  return { ...actual, getEngineConnection: apiMocks.getEngineConnection };
});

vi.mock("../api/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/jobs")>();
  return { ...actual, getAnalysis: apiMocks.getAnalysis };
});

const runningJob: AnalysisJob = {
  id: "analysis-1",
  projectId: "project-1",
  operation: "fingerprint",
  stages: [],
  status: "running",
  progressPercent: 0,
  errorCode: null,
  errorMessage: null,
  stage: {
    id: "stage-1",
    name: "fingerprint_source",
    status: "running",
    progressPercent: 0,
    bytesDone: 0,
    bytesTotal: 100,
    progressUnit: "bytes",
    unitsDone: 0,
    unitsTotal: 100,
    attempts: [{ id: "attempt-1", number: 1, status: "running", processId: 42 }],
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  apiMocks.getAnalysis.mockReset();
  apiMocks.getEngineConnection.mockReset();
  apiMocks.getEngineConnection.mockResolvedValue({
    baseUrl: "http://127.0.0.1:49152",
    token: "stream-secret",
  });
});

describe("parseSseFrames", () => {
  it("parses complete authenticated stream events and retains partial data", () => {
    const input = [
      ": heartbeat\n\n",
      "id: 3\n",
      "event: PROGRESS\n",
      'data: {"sequence":3,"type":"PROGRESS","message":"Fingerprint progress","payload":{"bytesDone":25,"bytesTotal":100,"progressPercent":25},"createdAt":"2026-11-01T12:00:00Z"}\n\n',
      "id: 4\nevent: FINGERPRINT_SUCCEEDED\n",
    ].join("");

    const parsed = parseSseFrames(input);

    expect(parsed.events).toEqual([
      {
        sequence: 3,
        type: "PROGRESS",
        message: "Fingerprint progress",
        payload: { bytesDone: 25, bytesTotal: 100, progressPercent: 25 },
        createdAt: "2026-11-01T12:00:00Z",
      },
    ]);
    expect(parsed.remainder).toBe(
      "id: 4\nevent: FINGERPRINT_SUCCEEDED\n",
    );
  });

  it("drops malformed frames without exposing their raw contents", () => {
    const parsed = parseSseFrames(
      'id: 1\nevent: PROGRESS\ndata: {"token":"secret"}\n\n',
    );

    expect(parsed.events).toEqual([]);
    expect(parsed.remainder).toBe("");
  });
});

describe("useJobEvents", () => {
  it("refreshes processing jobs authoritatively without ending on fingerprint completion", async () => {
    const processingJob = { ...runningJob, operation: "separate" as const };
    const nextStage = {
      ...processingJob,
      progressPercent: 20,
      stage: { ...runningJob.stage, name: "validate_media" },
    };
    apiMocks.getAnalysis
      .mockResolvedValueOnce(processingJob)
      .mockResolvedValue(nextStage);
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(terminalSseResponse());

    const { result } = renderHook(() => useJobEvents("analysis-1"));

    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.job?.status).toBe("running");
    await waitFor(() => expect(result.current.job?.stage.name).toBe("validate_media"));
    expect(result.current.job?.progressPercent).toBe(20);
  });

  it("authenticates SSE with headers and resumes without putting the token in the URL", async () => {
    apiMocks.getAnalysis
      .mockResolvedValueOnce(runningJob)
      .mockResolvedValueOnce({ ...runningJob, progressPercent: 25 })
      .mockResolvedValueOnce({ ...runningJob, status: "succeeded" });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(sseResponse(3))
      .mockResolvedValueOnce(sseResponse());

    const { result } = renderHook(() => useJobEvents("analysis-1"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.job?.status).toBe("succeeded"));

    const [firstUrl, firstInit] = fetchMock.mock.calls[0];
    const [secondUrl, secondInit] = fetchMock.mock.calls[1];
    expect(String(firstUrl)).toBe(
      "http://127.0.0.1:49152/api/v1/analyses/analysis-1/events",
    );
    expect(String(firstUrl)).not.toContain("stream-secret");
    expect(String(secondUrl)).not.toContain("stream-secret");
    expect(new Headers(firstInit?.headers).get("X-AI4Music-Token")).toBe(
      "stream-secret",
    );
    expect(new Headers(firstInit?.headers).get("Last-Event-ID")).toBeNull();
    expect(new Headers(secondInit?.headers).get("Last-Event-ID")).toBe("3");
  });

  it("replays persisted events for a terminal job after app restart", async () => {
    apiMocks.getAnalysis.mockResolvedValue({
      ...runningJob,
      status: "succeeded",
      progressPercent: 100,
      stage: {
        ...runningJob.stage,
        status: "succeeded",
        progressPercent: 100,
        bytesDone: 100,
      },
    });
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(terminalSseResponse());

    const { result } = renderHook(() => useJobEvents("analysis-1"));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(result.current.events.map((event) => event.type)).toContain(
        "FINGERPRINT_SUCCEEDED",
      ),
    );
    expect(result.current.streamState).toBe("idle");
  });
});

function sseResponse(sequence?: number): Response {
  const body = sequence
    ? [
        `id: ${sequence}`,
        "event: PROGRESS",
        `data: ${JSON.stringify({
          sequence,
          type: "PROGRESS",
          message: "Fingerprint progress",
          payload: { bytesDone: 25, bytesTotal: 100, progressPercent: 25 },
          createdAt: "2026-11-01T12:00:00Z",
        })}`,
        "",
        "",
      ].join("\n")
    : "";
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function terminalSseResponse(): Response {
  return new Response(
    [
      "id: 4",
      "event: FINGERPRINT_SUCCEEDED",
      `data: ${JSON.stringify({
        sequence: 4,
        type: "FINGERPRINT_SUCCEEDED",
        message: "Fingerprint completed",
        payload: { bytesDone: 100, bytesTotal: 100, progressPercent: 100 },
        createdAt: "2026-11-01T12:00:01Z",
      })}`,
      "",
      "",
    ].join("\n"),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}
