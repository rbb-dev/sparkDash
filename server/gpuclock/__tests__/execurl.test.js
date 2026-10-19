// mgllm: a Spark read through sparkdash-api (execUrl) has no SSH or sudo for her clock-cap programs; its cap is mgllm's
// own control beside the head unit's role pill. Her section says so instead of offering controls, and every action is
// refused before anything is sent (sparkdash-api would refuse her programs anyway: they are not lines it knows).
import { test } from "node:test";
import { strict as assert } from "node:assert";
import { GpuClockManager, assertSupported } from "../GpuClockManager.js";

const spark = { id: "s", name: "s", kind: "spark", platform: "linux", execUrl: "http://192.0.2.1:9701/sparkdashapi/exec" };

test("a Spark read through sparkdash-api reads as not supported, with the reason", async () => {
  const sent = [];
  const m = new GpuClockManager();
  m._exec = async (...a) => { sent.push(a); return { RESULT: "ok" }; };
  const st = await m.status(spark);
  assert.equal(st.supported, false);
  assert.match(st.reason, /sparkdash-api/);
  assert.deepEqual(sent, []);
});

test("her clock actions are refused on a Spark read through sparkdash-api", async () => {
  const sent = [];
  const m = new GpuClockManager();
  m._exec = async (...a) => { sent.push(a); return { RESULT: "ok" }; };
  assert.throws(() => assertSupported(spark), (e) => e.status === 409 && /sparkdash-api/.test(e.message));
  await assert.rejects(m.apply(spark, { maxMHz: 2200 }), (e) => e.status === 409);
  await assert.rejects(m.reset(spark, {}), (e) => e.status === 409);
  assert.throws(() => m.command(spark, { action: "reset" }), (e) => e.status === 409);
  assert.deepEqual(sent, []);
});

test("a Spark read over SSH is unchanged", () => {
  assert.doesNotThrow(() => assertSupported({ ...spark, execUrl: undefined }));
});
