import { beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifactBlob, getArtifactDownloadUrl, getArtifactPlaybackUrl, type AudioArtifact } from "./processing";

const connection = vi.hoisted(() => ({ getEngineConnection: vi.fn() }));
vi.mock("./engine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./engine")>()),
  getEngineConnection: connection.getEngineConnection,
}));

const artifact: AudioArtifact = {
  id: "saved-vocals",
  analysisId: "completed-job",
  kind: "vocals",
  path: "D:\\Projects\\song\\vocals.wav",
  byteSize: 4,
  sha256: "a".repeat(64),
  contentUrl: "/api/v1/artifacts/saved-vocals/content",
  metadata: { duration_seconds: 1, sample_rate: 44100, channels: 2, codec: "pcm_s16le", container: "wav" },
};

describe("audio artifact content", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    connection.getEngineConnection.mockResolvedValue({
      baseUrl: "http://127.0.0.1:49152",
      token: "local-content-secret",
    });
  });

  it("reads real audio bytes with header authentication and supports cancellation", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(new Uint8Array([1, 2, 3, 4]), { headers: { "Content-Type": "audio/wav" } }),
    );
    const controller = new AbortController();

    const blob = await getArtifactBlob(artifact, controller.signal);

    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual([1, 2, 3, 4]);
    expect(blob.type).toBe("audio/wav");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://127.0.0.1:49152/api/v1/artifacts/saved-vocals/content");
    expect(String(url)).not.toContain("local-content-secret");
    expect(new Headers(init?.headers).get("X-AI4Music-Token")).toBe("local-content-secret");
    expect(init?.signal).toBe(controller.signal);
  });

  it("never sends a local token to an artifact URL on another origin", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    await expect(getArtifactBlob({
      ...artifact,
      contentUrl: "https://external.example/api/v1/artifacts/saved-vocals/content",
    })).rejects.toThrow("invalid_artifact_content_url");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("mints separate loopback playback and download grants without fetching the media", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(Response.json({ contentUrl: "/api/v1/playback/saved-vocals?download=0&signature=one" }))
      .mockResolvedValueOnce(Response.json({ contentUrl: "/api/v1/playback/saved-vocals?download=1&signature=two" }));
    const controller = new AbortController();

    await expect(getArtifactPlaybackUrl(artifact, controller.signal)).resolves.toBe(
      "http://127.0.0.1:49152/api/v1/playback/saved-vocals?download=0&signature=one",
    );
    await expect(getArtifactDownloadUrl(artifact)).resolves.toBe(
      "http://127.0.0.1:49152/api/v1/playback/saved-vocals?download=1&signature=two",
    );
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:49152/api/v1/artifacts/saved-vocals/content/playback");
    expect(fetchMock.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:49152/api/v1/artifacts/saved-vocals/content/playback?download=true");
  });

  it("rejects a playback grant on another origin", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ contentUrl: "https://external.example/api/v1/playback/saved-vocals" }),
    );

    await expect(getArtifactPlaybackUrl(artifact)).rejects.toThrow("invalid_artifact_playback_url");
  });
});
