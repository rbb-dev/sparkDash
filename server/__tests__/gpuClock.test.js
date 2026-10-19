// mgllm: the GPU clock control's routes (server/gpuClock.js), against stand-in sparkdash-api services
import { afterEach, beforeEach, test } from "node:test";
import { strict as assert } from "node:assert";
import http from "node:http";
import express from "express";
import { registerGpuClockRoutes } from "../gpuClock.js";

let units, app, appServer, base;

// one stand-in sparkdash-api: answers the clock lines from its own limit, or fails every line when `down`
async function fakeUnit(id, { down = false } = {}) {
  const u = { id, limit: "unlocked", seen: [] };
  u.server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      const { cmd } = JSON.parse(body);
      u.seen.push(cmd);
      if (down) return res.writeHead(500).end('{"error": "helper did not answer"}');
      const set = /^mgllm:gpu-clock set (\S+)$/.exec(cmd);
      if (set) u.limit = set[1];
      res.end(JSON.stringify({ limit: u.limit, ok: true, error: null, at: 0 }));
    });
  });
  await new Promise((r) => u.server.listen(0, "127.0.0.1", r));
  u.execUrl = `http://127.0.0.1:${u.server.address().port}/sparkdashapi/exec`;
  return u;
}

async function start(sparks, guard = {}) {
  const registry = {
    get publicSparks() { return sparks.map(({ id }) => ({ id })); },
    getSpark: (id) => sparks.find((s) => s.id === id),
  };
  app = express();
  app.use(express.json());
  registerGpuClockRoutes(app, { registry, ...guard });
  appServer = await new Promise((r) => { const s = app.listen(0, "127.0.0.1", () => r(s)); });
  base = `http://127.0.0.1:${appServer.address().port}/api/gpu-clock`;
}

const spark = (u, name) => ({ id: u.id, name, lanIp: "192.0.2.1", ssh: { user: "nobody", auth: "key" }, execUrl: u.execUrl });
const post = (body) => fetch(base, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

beforeEach(async () => {
  units = [await fakeUnit("s1"), await fakeUnit("s2")];
  process.env.SPARKDASH_EXEC_URL_PREFIXES = units.map((u) => `http://127.0.0.1:${u.server.address().port}/`).join(",");
  process.env.SPARKDASH_EXEC_TOKEN = "t".repeat(40);
});

afterEach(async () => {
  delete process.env.SPARKDASH_EXEC_URL_PREFIXES;
  delete process.env.SPARKDASH_EXEC_TOKEN;
  await Promise.all([appServer, ...units.map((u) => u.server)].filter(Boolean).map((s) => new Promise((r) => s.close(r))));
  appServer = null;
});

test("GET reports the limit each unit read through sparkdash-api answers, and the presets", async () => {
  await start([spark(units[0], "spark 1"), spark(units[1], "spark 2")]);
  const body = await (await fetch(base)).json();
  assert.equal(body.available, true);
  assert.deepEqual(body.limits, ["unlocked", "2400", "2200", "2000", "1800"]);
  assert.deepEqual(body.units.map((u) => [u.name, u.limit, u.ok]), [["spark 1", "unlocked", true], ["spark 2", "unlocked", true]]);
});

test("POST sets the one limit on every unit together", async () => {
  await start([spark(units[0], "spark 1"), spark(units[1], "spark 2")]);
  const res = await post({ limit: "2200" });
  assert.equal(res.status, 200);
  assert.deepEqual(units.map((u) => u.seen), [["mgllm:gpu-clock set 2200"], ["mgllm:gpu-clock set 2200"]]);
  assert.deepEqual((await res.json()).units.map((u) => u.limit), ["2200", "2200"]);
});

test("POST refuses anything but a preset and asks no unit", async () => {
  await start([spark(units[0], "spark 1")]);
  for (const limit of ["2300", "0", "unlocked; reboot", undefined]) {
    assert.equal((await post({ limit })).status, 400);
  }
  assert.deepEqual(units[0].seen, []);
});

test("a unit that fails is named with its error (502) while the others are still set", async () => {
  const down = await fakeUnit("s3", { down: true });
  units.push(down);
  process.env.SPARKDASH_EXEC_URL_PREFIXES += `,http://127.0.0.1:${down.server.address().port}/`;
  await start([spark(units[0], "spark 1"), spark(down, "spark 3")]);
  const res = await post({ limit: "2000" });
  assert.equal(res.status, 502);
  const body = await res.json();
  assert.deepEqual(body.units.map((u) => [u.name, u.ok]), [["spark 1", true], ["spark 3", false]]);
  assert.match(body.units[1].error, /answered 500/);
  assert.equal(units[0].limit, "2000");
});

test("units without execUrl are left alone: the control is unavailable and a set has nowhere to go", async () => {
  await start([{ id: "s9", name: "ssh unit", lanIp: "192.0.2.9", ssh: { user: "nobody", auth: "key" } }]);
  assert.deepEqual(await (await fetch(base)).json(), { available: false, limits: ["unlocked", "2400", "2200", "2000", "1800"], units: [] });
  assert.equal((await post({ limit: "2200" })).status, 404);
});

test("POST is rate-limited as the other destructive routes are: refused with 429 and no unit asked", async () => {
  const limited = [];
  await start([spark(units[0], "spark 1")], {
    allowChange: () => false,
    rejectLimited: (res, message) => { limited.push(message); return res.status(429).json({ error: message }); },
  });
  const res = await post({ limit: "2200" });
  assert.equal(res.status, 429);
  assert.equal(limited.length, 1);
  assert.deepEqual(units[0].seen, []);
  assert.equal((await fetch(base)).status, 200);
});
