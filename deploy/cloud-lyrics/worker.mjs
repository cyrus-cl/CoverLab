const MODEL = "qwen3-asr-flash-filetrans";
const MAX_AUDIO = 480 * 32000 + 44;
const MAX_RESULT = 2 * 1024 * 1024;
const DAY = 86400000;
const TICKET = /^[a-f0-9]{32}$/;
const API_HOST = /^[a-z0-9-]+\.cn-beijing\.maas\.aliyuncs\.com$/;
const OSS_HOST = /^[a-z0-9-]+\.oss-cn-beijing\.aliyuncs\.com$/;

class PublicError extends Error {
  constructor(status, code) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

const reply = (body, status = 200) =>
  Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

export function audioSeconds(bytes) {
  const badAudio = () => new PublicError(422, "cloud_audio_invalid");
  if (bytes.length <= 44 || bytes.length > MAX_AUDIO) throw badAudio();
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u16 = offset => view.getUint16(offset, true);
  const u32 = offset => view.getUint32(offset, true);
  if (
    u32(0) !== 0x46464952 || u32(4) !== bytes.length - 8 ||
    u32(8) !== 0x45564157 || u32(12) !== 0x20746d66 || u32(16) !== 16 ||
    u16(20) !== 1 || u16(22) !== 1 || u32(24) !== 16000 ||
    u32(28) !== 32000 || u16(32) !== 2 || u16(34) !== 16 ||
    u32(36) !== 0x61746164 || u32(40) !== bytes.length - 44 || bytes.length % 2 !== 0
  ) throw badAudio();
  return Math.ceil((bytes.length - 44) / 32000);
}

export function quotaKeys(now, client) {
  const date = new Date(now + 8 * 3600000).toISOString().slice(0, 10);
  return [
    { key: `month:${date.slice(0, 7)}`, limit: 36000, expires: now + 62 * DAY },
    { key: `day:${date}`, limit: 3600, expires: now + 2 * DAY },
    { key: `client:${date}:${client}`, limit: 1200, maxCalls: 8, expires: now + 2 * DAY },
  ];
}

export class Budget {
  constructor(ctx) {
    this.storage = ctx.storage;
    this.sql = ctx.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS usage (
      key TEXT PRIMARY KEY, seconds INTEGER NOT NULL, calls INTEGER NOT NULL,
      expires INTEGER NOT NULL
    )`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS tasks (
      ticket TEXT PRIMARY KEY, state TEXT NOT NULL, upstream TEXT, error TEXT,
      created INTEGER NOT NULL, expires INTEGER NOT NULL
    )`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS polls (
      ticket TEXT PRIMARY KEY, calls INTEGER NOT NULL, next_at INTEGER NOT NULL,
      expires INTEGER NOT NULL
    )`);
  }

  reserve(ticket, client, seconds, now) {
    return this.storage.transactionSync(() => {
      this.sql.exec("DELETE FROM tasks WHERE expires <= ?", now);
      this.sql.exec("DELETE FROM usage WHERE expires <= ?", now);
      this.sql.exec("DELETE FROM polls WHERE expires <= ?", now);
      if (this.find(ticket, now)) return "existing";
      const keys = quotaKeys(now, client);
      for (const item of keys) {
        const used = this.sql.exec("SELECT seconds, calls FROM usage WHERE key = ?", item.key).toArray()[0];
        if ((used?.seconds ?? 0) + seconds > item.limit ||
            (item.maxCalls && (used?.calls ?? 0) >= item.maxCalls)) return "limited";
      }
      for (const item of keys) {
        this.sql.exec(`INSERT INTO usage VALUES (?, ?, 1, ?)
          ON CONFLICT(key) DO UPDATE SET seconds = seconds + excluded.seconds,
          calls = calls + 1, expires = excluded.expires`, item.key, seconds, item.expires);
      }
      this.sql.exec("INSERT INTO tasks VALUES (?, 'submitting', NULL, NULL, ?, ?)",
        ticket, now, now + DAY);
      return "reserved";
    });
  }

  find(ticket, now) {
    return this.sql.exec("SELECT * FROM tasks WHERE ticket = ? AND expires > ?", ticket, now)
      .toArray()[0] ?? null;
  }

  poll(ticket, now) {
    return this.storage.transactionSync(() => {
      const task = this.find(ticket, now);
      if (!task || !["running", "ready"].includes(task.state)) return task;
      const used = this.sql.exec("SELECT calls, next_at FROM polls WHERE ticket = ?", ticket)
        .toArray()[0];
      if ((used?.calls ?? 0) >= (task.state === "ready" ? 8 : 150)) {
        return { ...task, exhausted: true };
      }
      if ((used?.next_at ?? 0) > now) return { ...task, throttled: true };
      this.sql.exec(`INSERT INTO polls VALUES (?, 1, ?, ?)
        ON CONFLICT(ticket) DO UPDATE SET calls = calls + 1, next_at = excluded.next_at`,
        ticket, now + 2000, task.expires);
      return task;
    });
  }

  async fetch(request) {
    const operation = new URL(request.url).pathname;
    const { ticket, client, seconds, upstream, error } = await request.json();
    const now = Date.now();
    if (operation === "/reserve") return reply(this.reserve(ticket, client, seconds, now));
    if (operation === "/get") return reply(this.find(ticket, now));
    if (operation === "/poll") return reply(this.poll(ticket, now));
    if (operation === "/complete") {
      this.storage.transactionSync(() => {
        if (this.find(ticket, now)?.state !== "running") return;
        this.sql.exec("UPDATE tasks SET state = 'ready' WHERE ticket = ?", ticket);
        this.sql.exec("UPDATE polls SET calls = 0 WHERE ticket = ?", ticket);
      });
      return reply(true);
    }
    if (operation === "/finish") {
      this.sql.exec("UPDATE tasks SET state = ?, upstream = ?, error = ? WHERE ticket = ?",
        upstream ? "running" : "failed", upstream ?? null, error ?? null, ticket);
      return reply(true);
    }
    return reply({ error: "not_found" }, 404);
  }
}

async function ledger(env, path, data) {
  const result = await env.BUDGET.get(env.BUDGET.idFromName("public-v1"))
    .fetch(`https://ledger.internal${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
  if (!result.ok) throw new PublicError(503, "cloud_unavailable");
  return result.json();
}

async function clientId(request, secret) {
  const ip = request.headers.get("CF-Connecting-IP");
  if (!ip || ip.length > 80) throw new PublicError(400, "cloud_client_invalid");
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const digest = await crypto.subtle.sign("HMAC", key, encoder.encode(ip));
  return Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
}

async function readSizedAudio(request) {
  const length = Number(request.headers.get("Content-Length"));
  if (!Number.isSafeInteger(length) || length <= 44 || length > MAX_AUDIO || !request.body) {
    throw new PublicError(413, "cloud_audio_limit");
  }
  const bytes = new Uint8Array(length);
  const reader = request.body.getReader();
  let offset = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (offset + value.length > length) throw new PublicError(413, "cloud_audio_limit");
      bytes.set(value, offset);
      offset += value.length;
    }
  } finally {
    if (offset !== length) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (offset !== length) throw new PublicError(422, "cloud_audio_invalid");
  return bytes;
}

function ossUrl(raw) {
  const url = new URL(raw);
  if (!["http:", "https:"].includes(url.protocol) || !OSS_HOST.test(url.host) ||
      url.username || url.password) throw new PublicError(502, "cloud_upstream");
  url.protocol = "https:";
  return url;
}

async function upstream(url, init = {}) {
  let response;
  try {
    response = await fetch(url, { ...init, redirect: "manual", signal: AbortSignal.timeout(25000) });
  } catch (error) {
    console.error("Cloud upstream fetch failed", error?.name ?? "Error", new URL(url).hostname);
    throw new PublicError(502, "cloud_upstream");
  }
  if (!response.ok) throw new PublicError(
    response.status === 429 ? 429 : 502,
    response.status === 429 ? "cloud_limit" : "cloud_upstream",
  );
  return response;
}

async function upstreamJson(url, init) {
  const response = await upstream(url, init);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let size = 0;
  let content = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > MAX_RESULT) throw new PublicError(502, "cloud_upstream");
      content += decoder.decode(value, { stream: true });
    }
    return JSON.parse(content + decoder.decode());
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

async function submit(env, ticket, bytes) {
  const base = `https://${env.BAILIAN_HOST}/api/v1`;
  const auth = { Authorization: `Bearer ${env.BAILIAN_KEY}` };
  let policy;
  try {
    policy = (await upstreamJson(`${base}/uploads?action=getPolicy&model=${MODEL}`,
      { headers: auth })).data;
  } catch (error) {
    if (error instanceof PublicError && error.status === 429) throw error;
    throw new PublicError(502, "cloud_policy_failed");
  }
  if (!policy || !/^[A-Za-z0-9/_-]+$/.test(policy.upload_dir)) {
    throw new PublicError(502, "cloud_upstream");
  }
  const name = `${ticket}.wav`;
  const key = `${policy.upload_dir.replace(/\/$/, "")}/${name}`;
  const data = new FormData();
  for (const [field, value] of Object.entries({
    OSSAccessKeyId: policy.oss_access_key_id, policy: policy.policy, Signature: policy.signature,
    key, "x-oss-object-acl": policy.x_oss_object_acl,
    "x-oss-forbid-overwrite": policy.x_oss_forbid_overwrite, success_action_status: "200",
  })) data.append(field, value);
  data.append("file", new Blob([bytes], { type: "audio/wav" }), name);
  try {
    await upstream(ossUrl(policy.upload_host), { method: "POST", body: data });
  } catch (error) {
    if (error instanceof PublicError && error.status === 429) throw error;
    throw new PublicError(502, "cloud_upload_failed");
  }
  const result = await upstreamJson(`${base}/services/audio/asr/transcription`, {
    method: "POST",
    headers: {
      ...auth, "Content-Type": "application/json",
      "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable",
    },
    body: JSON.stringify({
      model: MODEL, input: { file_url: `oss://${key}` },
      parameters: { channel_id: [0], enable_words: true, enable_itn: false },
    }),
  });
  const task = result?.output?.task_id;
  if (typeof task !== "string" || !/^[A-Za-z0-9_-]{1,100}$/.test(task)) {
    throw new PublicError(502, "cloud_upstream");
  }
  await ledger(env, "/finish", { ticket, upstream: task });
}

async function result(env, ticket) {
  const task = await ledger(env, "/poll", { ticket });
  if (!task) return reply({ error: "cloud_task_not_found" }, 404);
  if (task.state === "failed") return reply({ status: "failed", error: "cloud_transcription_failed" });
  if (task.exhausted) return reply({ status: "failed", error: "cloud_task_not_found" });
  if (task.throttled) return reply({ status: "pending" });
  if (task.state === "submitting") return reply(Date.now() - task.created < 120000
    ? { status: "pending" } : { status: "failed", error: "cloud_submit_unknown" });
  try {
    const status = (await upstreamJson(
      `https://${env.BAILIAN_HOST}/api/v1/tasks/${task.upstream}`,
      { headers: { Authorization: `Bearer ${env.BAILIAN_KEY}` } },
    )).output;
    if (["PENDING", "RUNNING"].includes(status?.task_status)) return reply({ status: "pending" });
    if (["FAILED", "CANCELED", "UNKNOWN"].includes(status?.task_status)) {
      await ledger(env, "/finish", { ticket, error: "cloud_transcription_failed" });
      return reply({ status: "failed", error: "cloud_transcription_failed" });
    }
    if (status?.task_status !== "SUCCEEDED") throw new Error("Unexpected task response");
    const data = await upstreamJson(ossUrl(status.result.transcription_url));
    if (!Array.isArray(data.transcripts)) throw new Error("Invalid transcription");
    await ledger(env, "/complete", { ticket });
    return reply({ status: "succeeded", result: { transcripts: data.transcripts } });
  } catch {
    return reply({ error: "cloud_unavailable" }, 502);
  }
}

export default {
  async fetch(request, env) {
    try {
      const path = new URL(request.url).pathname;
      const configured = Boolean(env.BAILIAN_KEY && API_HOST.test(env.BAILIAN_HOST));
      if (path === "/health" && request.method === "GET") {
        return reply({ available: configured, version: 1 }, configured ? 200 : 503);
      }
      if (!configured) throw new PublicError(503, "cloud_unavailable");
      if (request.method === "GET" && path.startsWith("/v1/lyrics/")) {
        const ticket = path.slice("/v1/lyrics/".length);
        if (!TICKET.test(ticket)) throw new PublicError(404, "not_found");
        return await result(env, ticket);
      }
      if (request.method !== "POST" || path !== "/v1/lyrics") {
        throw new PublicError(404, "not_found");
      }
      const ticket = request.headers.get("Idempotency-Key") ?? "";
      if (!TICKET.test(ticket)) throw new PublicError(400, "cloud_task_invalid");
      if (await ledger(env, "/get", { ticket })) return reply({ status: "pending" }, 202);
      const bytes = await readSizedAudio(request);
      const seconds = audioSeconds(bytes);
      const client = await clientId(request, env.BAILIAN_KEY);
      const reserved = await ledger(env, "/reserve", { ticket, client, seconds });
      if (reserved === "limited") throw new PublicError(429, "cloud_limit");
      if (reserved === "reserved") {
        try {
          await submit(env, ticket, bytes);
        } catch (error) {
          const code = error instanceof PublicError ? error.code : "cloud_upstream";
          await ledger(env, "/finish", { ticket, error: code });
          throw new PublicError(502, "cloud_transcription_failed");
        }
      }
      return reply({ status: "pending" }, 202);
    } catch (error) {
      return error instanceof PublicError ? reply({ error: error.code }, error.status)
        : reply({ error: "cloud_unavailable" }, 503);
    }
  },
};
