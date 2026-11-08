import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  cancelAnalysis,
  createAnalysis,
  getAnalysis,
  getLatestAnalysis,
  retryAnalysis,
} from "./jobs";

const engineApi = vi.hoisted(() => ({
  engineFetch: vi.fn(),
}));

vi.mock("./engine", () => ({
  engineFetch: engineApi.engineFetch,
}));

describe("analysis API", () => {
  beforeEach(() => engineApi.engineFetch.mockReset());

  it("uses the durable analysis endpoints", async () => {
    engineApi.engineFetch.mockResolvedValue({ id: "analysis-1" });

    await createAnalysis("project-1");
    await getLatestAnalysis("project-1");
    await getAnalysis("analysis-1");
    await cancelAnalysis("analysis-1");
    await retryAnalysis("analysis-1");

    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/projects/project-1/analyses",
      { method: "POST" },
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/projects/project-1/analyses/latest",
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      3,
      "/api/v1/analyses/analysis-1",
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      4,
      "/api/v1/analyses/analysis-1/cancel",
      { method: "POST" },
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      5,
      "/api/v1/analyses/analysis-1/retry",
      { method: "POST" },
    );
  });
});
