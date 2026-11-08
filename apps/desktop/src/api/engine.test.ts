import { invoke } from "@tauri-apps/api/core";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EngineApiError, engineFetch, getEngineConnection } from "./engine";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

const invokeMock = vi.mocked(invoke);

describe("local engine client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    invokeMock.mockReset();
  });

  it("gets the in-memory connection from the Tauri command", async () => {
    const connection = {
      baseUrl: "http://127.0.0.1:49152",
      token: "launch-token",
    };
    invokeMock.mockResolvedValue(connection);

    await expect(getEngineConnection()).resolves.toEqual(connection);
    expect(invokeMock).toHaveBeenCalledWith("engine_connection");
  });

  it("authenticates requests and does not permit callers to replace the token", async () => {
    invokeMock.mockResolvedValue({
      baseUrl: "http://127.0.0.1:49152",
      token: "real-launch-token",
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok", mode: "local" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      engineFetch<{ status: string }>("/api/v1/health/live", {
        headers: { "X-AI4Music-Token": "caller-token" },
      }),
    ).resolves.toEqual({ status: "ok", mode: "local" });

    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:49152/api/v1/health/live",
    );
    expect(new Headers(request.headers).get("X-AI4Music-Token")).toBe(
      "real-launch-token",
    );
  });

  it("keeps API errors limited to stable, user-safe fields", async () => {
    invokeMock.mockResolvedValue({
      baseUrl: "http://127.0.0.1:49152",
      token: "must-not-leak",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: "invalid_local_token",
              message: "Local engine access denied",
            },
          }),
          {
            status: 401,
            headers: {
              "Content-Type": "application/json",
              "X-Debug-Secret": "must-not-leak",
            },
          },
        ),
      ),
    );

    const error = await engineFetch("/api/v1/health/live").catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(EngineApiError);
    expect(error).toMatchObject({
      code: "invalid_local_token",
      message: "Local engine access denied",
      status: 401,
    });
    expect(JSON.stringify(error)).not.toContain("must-not-leak");
  });

  it("does not expose malformed server response bodies", async () => {
    const response = new Response("raw debug output: secret-value", {
      status: 500,
    });

    const error = await EngineApiError.fromResponse(response);
    expect(error).toMatchObject({
      code: "http_500",
      message: "Local engine request failed",
      status: 500,
    });
    expect(error.message).not.toContain("secret-value");
  });
});
