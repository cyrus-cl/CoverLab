import { beforeEach, describe, expect, it, vi } from "vitest";

import { createProject, getProject, listProjects } from "./projects";

const engineApi = vi.hoisted(() => ({
  engineFetch: vi.fn(),
}));

vi.mock("./engine", () => ({
  engineFetch: engineApi.engineFetch,
}));

describe("project API", () => {
  beforeEach(() => engineApi.engineFetch.mockReset());

  it("uses the local project endpoints", async () => {
    engineApi.engineFetch.mockResolvedValue({ id: "project-1" });

    await createProject({ sourcePath: "D:\\song.wav", outputDir: "D:\\out" });
    await listProjects();
    await getProject("project-1");

    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      1,
      "/api/v1/projects",
      {
        method: "POST",
        body: JSON.stringify({
          sourcePath: "D:\\song.wav",
          outputDir: "D:\\out",
        }),
      },
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      2,
      "/api/v1/projects",
    );
    expect(engineApi.engineFetch).toHaveBeenNthCalledWith(
      3,
      "/api/v1/projects/project-1",
    );
  });
});
