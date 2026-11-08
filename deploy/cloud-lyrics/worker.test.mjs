import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import worker, { audioSeconds, Budget, quotaKeys } from "./worker.mjs";

function wav(frames) {
  const data = Buffer.alloc(44 + frames * 2);
  data.write("RIFF", 0);
  data.writeUInt32LE(data.length - 8, 4);
  data.write("WAVEfmt ", 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(16000, 24);
  data.writeUInt32LE(32000, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write("data", 36);
  data.writeUInt32LE(frames * 2, 40);
  return data;
}

test("budget uses audio frames rather than client-declared duration", () => {
  assert.equal(audioSeconds(wav(16001)), 2);
  const forged = wav(16000);
  forged.writeUInt32LE(4, 40);
  assert.throws(() => audioSeconds(forged));
  const stereo = wav(16000);
  stereo.writeUInt16LE(2, 22);
  assert.throws(() => audioSeconds(stereo));
});

test("monthly counters follow Beijing calendar boundaries", () => {
  const keys = quotaKeys(Date.parse("2026-11-30T16:00:00Z"), "client");
  assert.deepEqual(keys.map((item) => item.key), [
    "month:2026-12", "day:2026-12-01", "client:2026-12-01:client",
  ]);
  assert.deepEqual(keys.map((item) => item.limit), [36000, 3600, 1200]);
});

test("upstream query errors remain resumable and failed tickets are terminal", async (t) => {
  const host = "test.cn-beijing.maas.aliyuncs.com";
  const request = new Request("https://example.test/v1/lyrics/" + "a".repeat(32));
  let saved = { state: "running", upstream: "upstream-task" };
  const env = {
    BAILIAN_HOST: host, BAILIAN_KEY: "test-key",
    BUDGET: {
      idFromName: () => "ledger",
      get: () => ({ fetch: async () => Response.json(saved) }),
    },
  };
  t.mock.method(globalThis, "fetch", async () => new Response("temporary", { status: 503 }));
  let response = await worker.fetch(request, env);
  assert.equal(response.status, 502);
  assert.equal((await response.json()).error, "cloud_unavailable");
  saved = { state: "failed", error: "cloud_policy_failed" };
  response = await worker.fetch(request, env);
  assert.deepEqual(await response.json(), { status: "failed", error: "cloud_transcription_failed" });
});

test("ledger deduplicates reservations and enforces client and polling limits", () => {
  const database = new DatabaseSync(":memory:");
  const sql = {
    exec(statement, ...params) {
      const query = database.prepare(statement);
      if (statement.startsWith("SELECT")) return { toArray: () => query.all(...params) };
      query.run(...params);
      return undefined;
    },
  };
  const storage = {
    sql,
    transactionSync(callback) {
      database.exec("BEGIN IMMEDIATE");
      try {
        const value = callback();
        database.exec("COMMIT");
        return value;
      } catch (error) {
        database.exec("ROLLBACK");
        throw error;
      }
    },
  };
  try {
    const budget = new Budget({ storage });
    const now = Date.parse("2026-11-07T00:00:00Z");
    assert.equal(budget.reserve("one", "client", 480, now), "reserved");
    assert.equal(budget.reserve("one", "client", 480, now), "existing");
    assert.equal(budget.reserve("two", "client", 480, now), "reserved");
    assert.equal(budget.reserve("three", "client", 480, now), "limited");
    sql.exec("UPDATE tasks SET state = 'ready' WHERE ticket = ?", "one");
    assert.equal(budget.poll("one", now).state, "ready");
    assert.equal(budget.poll("one", now + 1).throttled, true);
    for (let index = 1; index < 8; index++) budget.poll("one", now + index * 2000);
    assert.equal(budget.poll("one", now + 16000).exhausted, true);
  } finally {
    database.close();
  }
});
