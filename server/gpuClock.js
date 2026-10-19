// mgllm: the GPU clock control. Every unit read through its sparkdash-api (execUrl) is asked with mgllm's own lines:
// "mgllm:gpu-clock" answers the limit in force, "mgllm:gpu-clock set <limit>" has the unit's root-only helper
// (sparkdash-clock) lock its GPUs to 0..limit MHz through NVML, or release them. The limit goes to all of them
// together: a tensor-parallel pair runs at its slower GPU's pace. Units without execUrl are left alone. A change is
// rate-limited as the server's other destructive routes are (allowChange / rejectLimited from index.js).
import { sshExec } from "./collectors/ssh.js";

export const GPU_CLOCK_LIMITS = ["unlocked", "2400", "2200", "2000", "1800"];

function clockUnits(registry) {
  return registry.publicSparks.map((p) => registry.getSpark(p.id)).filter((s) => s?.execUrl);
}

async function ask(spark, line) {
  try {
    const state = JSON.parse(await sshExec(spark, line, { timeoutMs: 20_000 }));
    return { id: spark.id, name: spark.name, limit: state.limit ?? null, ok: state.ok !== false, error: state.error ?? null };
  } catch (err) {
    return { id: spark.id, name: spark.name, limit: null, ok: false, error: String(err?.message || err) };
  }
}

export function registerGpuClockRoutes(app, { registry, allowChange = () => true, rejectLimited }) {
  app.get("/api/gpu-clock", async (_req, res) => {
    const units = clockUnits(registry);
    res.json({ available: units.length > 0, limits: GPU_CLOCK_LIMITS, units: await Promise.all(units.map((s) => ask(s, "mgllm:gpu-clock"))) });
  });

  app.post("/api/gpu-clock", async (req, res) => {
    if (!allowChange(req)) return rejectLimited(res, "Too many GPU clock changes; try again shortly");
    const limit = String(req.body?.limit ?? "");
    if (!GPU_CLOCK_LIMITS.includes(limit)) {
      return res.status(400).json({ error: `limit must be one of ${GPU_CLOCK_LIMITS.join(", ")}` });
    }
    const units = clockUnits(registry);
    if (!units.length) return res.status(404).json({ error: "no unit is read through sparkdash-api" });
    const results = await Promise.all(units.map((s) => ask(s, `mgllm:gpu-clock set ${limit}`)));
    res.status(results.every((r) => r.ok) ? 200 : 502).json({ limit, units: results });
  });
}
