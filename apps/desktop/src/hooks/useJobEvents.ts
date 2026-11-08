import { useCallback, useEffect, useRef, useState } from "react";

import { EngineApiError, getEngineConnection } from "../api/engine";
import { getAnalysis, type AnalysisJob } from "../api/jobs";

export type JobEvent = {
  sequence: number;
  type: string;
  message: string;
  payload: Record<string, unknown>;
  createdAt: string;
};

export type JobStreamState = "idle" | "connecting" | "live" | "reconnecting";

type ParsedSseFrames = {
  events: JobEvent[];
  remainder: string;
};

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "canceled"]);

export function parseSseFrames(buffer: string): ParsedSseFrames {
  const normalized = buffer.replaceAll("\r\n", "\n");
  const frames = normalized.split("\n\n");
  const remainder = frames.pop() ?? "";
  const events: JobEvent[] = [];

  for (const frame of frames) {
    const lines = frame.split("\n");
    const id = fieldValue(lines, "id");
    const eventType = fieldValue(lines, "event");
    const data = lines
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) continue;

    try {
      const parsed: unknown = JSON.parse(data);
      if (!isJobEvent(parsed)) continue;
      if (id && Number(id) !== parsed.sequence) continue;
      if (eventType && eventType !== parsed.type) continue;
      events.push(parsed);
    } catch {
      // Malformed stream records are ignored without retaining raw contents.
    }
  }

  return { events, remainder };
}

export function useJobEvents(analysisId: string | null) {
  const [job, setJob] = useState<AnalysisJob | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [streamState, setStreamState] = useState<JobStreamState>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [refreshSequence, setRefreshSequence] = useState(0);
  const trackedAnalysis = useRef<string | null>(null);
  const lastSequence = useRef(0);

  const refetch = useCallback(() => {
    setJob(null);
    setRefreshSequence((sequence) => sequence + 1);
  }, []);

  useEffect(() => {
    const resume = () => {
      if (document.visibilityState === "visible") refetch();
    };
    document.addEventListener("visibilitychange", resume);
    return () => document.removeEventListener("visibilitychange", resume);
  }, [refetch]);

  useEffect(() => {
    if (trackedAnalysis.current !== analysisId) {
      trackedAnalysis.current = analysisId;
      lastSequence.current = 0;
      setEvents([]);
      setJob(null);
      setErrorMessage(null);
    }
    if (!analysisId) {
      setStreamState("idle");
      return;
    }

    const activeAnalysisId = analysisId;
    let active = true;
    let requestVersion = 0;
    const controller = new AbortController();

    async function refreshJob() {
      const version = ++requestVersion;
      const current = await getAnalysis(activeAnalysisId);
      if (active && version === requestVersion) setJob(current);
      return current;
    }

    async function run() {
      while (active) {
        try {
          setStreamState(lastSequence.current > 0 ? "reconnecting" : "connecting");
          const current = await refreshJob();
          if (!active) return;
          setErrorMessage(null);
          const isTerminal = TERMINAL_STATUSES.has(current.status);
          if (isTerminal && lastSequence.current > 0) {
            setStreamState("idle");
            return;
          }

          await streamEvents(
            activeAnalysisId,
            lastSequence,
            controller.signal,
            async (incoming) => {
              if (!active) return;
              setEvents((existing) => mergeEvents(existing, incoming));
              if (current.operation && current.operation !== "fingerprint") {
                await refreshJob();
                if (!active) return;
              } else {
                setJob((existing) => applyEvents(existing, incoming));
              }
              setStreamState("live");
            },
          );
          if (!active) return;
          if (isTerminal) {
            setStreamState("idle");
            return;
          }
        } catch (error) {
          if (!active || isAbortError(error)) return;
          setErrorMessage(
            error instanceof EngineApiError
              ? error.message
              : "本机引擎连接中断，正在重连",
          );
          setStreamState("reconnecting");
        }
        await pause(500);
      }
    }

    void run();
    return () => {
      active = false;
      controller.abort();
    };
  }, [analysisId, refreshSequence]);

  const isCurrentAnalysis = trackedAnalysis.current === analysisId;
  return {
    job: isCurrentAnalysis ? job : null,
    events: isCurrentAnalysis ? events : [],
    streamState: isCurrentAnalysis
      ? streamState
      : analysisId
        ? "connecting"
        : "idle",
    errorMessage: isCurrentAnalysis ? errorMessage : null,
    refetch,
  };
}

async function streamEvents(
  analysisId: string,
  lastSequence: { current: number },
  signal: AbortSignal,
  onEvents: (events: JobEvent[]) => void | Promise<void>,
) {
  const connection = await getEngineConnection();
  const headers = new Headers({
    Accept: "text/event-stream",
    "X-AI4Music-Token": connection.token,
  });
  if (lastSequence.current > 0) {
    headers.set("Last-Event-ID", String(lastSequence.current));
  }

  const response = await fetch(
    `${connection.baseUrl}/api/v1/analyses/${encodeURIComponent(analysisId)}/events`,
    { headers, signal },
  );
  if (!response.ok) throw await EngineApiError.fromResponse(response);
  if (!response.body) throw new Error("local_engine_stream_unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const parsed = parseSseFrames(buffer);
    buffer = parsed.remainder;
    if (parsed.events.length > 0) {
      const fresh = parsed.events.filter(
        (event) => event.sequence > lastSequence.current,
      );
      if (fresh.length > 0) {
        lastSequence.current = Math.max(
          lastSequence.current,
          ...fresh.map((event) => event.sequence),
        );
        await onEvents(fresh);
      }
    }
    if (done) return;
  }
}

function fieldValue(lines: string[], field: string): string | null {
  const prefix = `${field}:`;
  const line = lines.find((candidate) => candidate.startsWith(prefix));
  return line ? line.slice(prefix.length).trimStart() : null;
}

function isJobEvent(value: unknown): value is JobEvent {
  if (typeof value !== "object" || value === null) return false;
  if (!("payload" in value) || !("sequence" in value)) return false;
  const payload = value.payload;
  const sequence = value.sequence;
  return (
    typeof sequence === "number" &&
    Number.isSafeInteger(sequence) &&
    sequence > 0 &&
    "type" in value &&
    typeof value.type === "string" &&
    "message" in value &&
    typeof value.message === "string" &&
    "createdAt" in value &&
    typeof value.createdAt === "string" &&
    typeof payload === "object" &&
    payload !== null &&
    !Array.isArray(payload)
  );
}

function mergeEvents(existing: JobEvent[], incoming: JobEvent[]): JobEvent[] {
  const merged = new Map(existing.map((event) => [event.sequence, event]));
  for (const event of incoming) merged.set(event.sequence, event);
  return [...merged.values()].sort((left, right) => left.sequence - right.sequence);
}

function applyEvents(
  job: AnalysisJob | null,
  incoming: JobEvent[],
): AnalysisJob | null {
  if (!job) return null;
  if (job.operation && job.operation !== "fingerprint") return job;
  let next = job;
  for (const event of incoming) next = applyEvent(next, event);
  return next;
}

function applyEvent(job: AnalysisJob, event: JobEvent): AnalysisJob {
  const progress = safeProgress(event.payload.progressPercent);
  const bytesDone = safeBytes(event.payload.bytesDone);
  const bytesTotal = safeBytes(event.payload.bytesTotal);
  let status = job.status;
  let stageStatus = job.stage.status;

  if (event.type === "FINGERPRINT_STARTED") {
    status = "running";
    stageStatus = "running";
  } else if (event.type === "CANCEL_REQUESTED") {
    status = "canceling";
  } else if (event.type === "JOB_CANCELED") {
    status = "canceled";
    stageStatus = "canceled";
  } else if (event.type === "JOB_FAILED") {
    status = "failed";
    stageStatus = "failed";
  } else if (event.type === "FINGERPRINT_SUCCEEDED") {
    status = "succeeded";
    stageStatus = "succeeded";
  }

  return {
    ...job,
    status,
    progressPercent: progress ?? job.progressPercent,
    stage: {
      ...job.stage,
      status: stageStatus,
      progressPercent: progress ?? job.stage.progressPercent,
      bytesDone: bytesDone ?? job.stage.bytesDone,
      bytesTotal: bytesTotal ?? job.stage.bytesTotal,
    },
  };
}

function safeProgress(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 100
    ? value
    : null;
}

function safeBytes(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function pause(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
