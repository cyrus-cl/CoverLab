import { invoke } from "@tauri-apps/api/core";

export type EngineConnection = {
  baseUrl: string;
  token: string;
};

type EngineErrorEnvelope = {
  error: {
    code: string;
    message: string;
  };
};

export class EngineApiError extends Error {
  readonly code: string;
  readonly status: number;

  private constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "EngineApiError";
    this.code = code;
    this.status = status;
  }

  static async fromResponse(response: Response): Promise<EngineApiError> {
    const fallback = new EngineApiError(
      `http_${response.status}`,
      "Local engine request failed",
      response.status,
    );

    try {
      const body: unknown = await response.json();
      if (!isEngineErrorEnvelope(body)) return fallback;
      return new EngineApiError(
        body.error.code,
        body.error.message,
        response.status,
      );
    } catch {
      return fallback;
    }
  }
}

export async function getEngineConnection(): Promise<EngineConnection> {
  return invoke<EngineConnection>("engine_connection");
}

export async function engineFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const connection = await getEngineConnection();
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("X-AI4Music-Token", connection.token);

  const response = await fetch(`${connection.baseUrl}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) throw await EngineApiError.fromResponse(response);
  return response.json() as Promise<T>;
}

function isEngineErrorEnvelope(value: unknown): value is EngineErrorEnvelope {
  if (typeof value !== "object" || value === null || !("error" in value)) {
    return false;
  }
  const error = value.error;
  if (typeof error !== "object" || error === null) return false;
  if (!("code" in error) || !("message" in error)) return false;
  if (typeof error.code !== "string" || typeof error.message !== "string") {
    return false;
  }
  return (
    /^[a-z0-9_]{1,64}$/.test(error.code) &&
    error.message.length > 0 &&
    error.message.length <= 240 &&
    !/[\u0000-\u001f\u007f]/.test(error.message)
  );
}
