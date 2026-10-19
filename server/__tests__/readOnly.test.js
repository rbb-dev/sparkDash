// mgllm: a read-only deployment (server/readOnly.js), the app's own refusal of writes outside its allowlist
import { test } from "node:test";
import { strict as assert } from "node:assert";
import express from "express";
import { createReadOnlyMiddleware, readOnlyConfig, registerDeploymentRoute } from "../readOnly.js";

async function serve(env) {
  const config = readOnlyConfig(env);
  const app = express();
  app.use(express.json());
  app.use(createReadOnlyMiddleware(config));
  registerDeploymentRoute(app, config);
  app.all("/api/*rest", (req, res) => res.json({ reached: `${req.method} ${req.path}` }));
  const server = await new Promise((r) => { const s = app.listen(0, "127.0.0.1", () => r(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const call = async (method, path) => {
    const res = await fetch(base + path, { method, headers: { "Content-Type": "application/json" }, body: method === "GET" ? undefined : "{}" });
    return [res.status, await res.json()];
  };
  return { call, close: () => new Promise((r) => server.close(r)) };
}

test("read-only: every write is refused except the allowlisted ones; reads pass", async () => {
  const s = await serve({ SPARKDASH_READ_ONLY: "1", SPARKDASH_WRITE_ALLOW: "PUT /api/settings, post /api/gpu-clock" });
  try {
    assert.deepEqual(await s.call("PUT", "/api/settings"), [200, { reached: "PUT /api/settings" }]);
    assert.deepEqual(await s.call("POST", "/api/gpu-clock"), [200, { reached: "POST /api/gpu-clock" }]);
    assert.deepEqual(await s.call("GET", "/api/sparks"), [200, { reached: "GET /api/sparks" }]);
    for (const [method, path] of [["POST", "/api/settings"], ["PUT", "/api/settings/x"], ["POST", "/api/sparks/s1/shutdown"], ["DELETE", "/api/events"], ["PATCH", "/api/gpu-clock"]]) {
      assert.deepEqual(await s.call(method, path), [403, { error: "sparkDash is read-only here" }], `${method} ${path}`);
    }
  } finally {
    await s.close();
  }
});

test("read-only: the page learns which writes remain", async () => {
  const s = await serve({ SPARKDASH_READ_ONLY: "true", SPARKDASH_WRITE_ALLOW: "PUT /api/settings" });
  try {
    assert.deepEqual(await s.call("GET", "/api/deployment"), [200, { readOnly: true, writable: ["PUT /api/settings"] }]);
  } finally {
    await s.close();
  }
});

test("without SPARKDASH_READ_ONLY nothing changes", async () => {
  const s = await serve({});
  try {
    assert.deepEqual(await s.call("POST", "/api/sparks/s1/shutdown"), [200, { reached: "POST /api/sparks/s1/shutdown" }]);
    assert.deepEqual(await s.call("GET", "/api/deployment"), [200, { readOnly: false, writable: [] }]);
  } finally {
    await s.close();
  }
});

// mgllm: the guard only refuses what is registered after it. Her REA uploads (2.40+) are registered ahead of
// express.json and the auth middleware, so a guard placed after those let a file upload through in a read-only
// deployment. Every route that can write must come after the guard in server/index.js.
test("the read-only guard is installed before every route that can write", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../index.js", import.meta.url), "utf8");
  const lines = src.split("\n");
  const guard = lines.findIndex((l) => /app\.use\(createReadOnlyMiddleware\(/.test(l));
  assert.ok(guard >= 0, "the guard is installed");
  const early = lines
    .map((l, i) => ({ l: l.trim(), i }))
    .filter(({ l, i }) => i < guard && (/^register\w*Routes\(app\b/.test(l) || /^app\.(post|put|delete|patch|all)\(/.test(l)))
    .map(({ l, i }) => `${i + 1}: ${l.slice(0, 80)}`);
  assert.deepEqual(early, [], "routes registered before the read-only guard");
});
