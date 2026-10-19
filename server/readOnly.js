// mgllm: a read-only deployment. With SPARKDASH_READ_ONLY=1 the server refuses every write (any method but GET,
// HEAD and OPTIONS) except those named in SPARKDASH_WRITE_ALLOW ("PUT /api/settings,POST /api/gpu-clock": method
// and exact path), whoever sends it, and GET /api/deployment tells the page which writes remain so it can hide the
// controls it would refuse. Without SPARKDASH_READ_ONLY nothing changes.

const READS = new Set(["GET", "HEAD", "OPTIONS"]);

export function readOnlyConfig(env = process.env) {
  const readOnly = /^(1|true|yes)$/i.test(String(env.SPARKDASH_READ_ONLY || "").trim());
  const writable = String(env.SPARKDASH_WRITE_ALLOW || "")
    .split(",")
    .map((entry) => entry.trim().split(/\s+/))
    .filter((parts) => parts.length === 2 && parts[1].startsWith("/"))
    .map(([method, path]) => `${method.toUpperCase()} ${path}`);
  return { readOnly, writable };
}

export function createReadOnlyMiddleware({ readOnly, writable }) {
  return function readOnlyGuard(req, res, next) {
    const method = (req.method || "GET").toUpperCase();
    if (!readOnly || READS.has(method) || writable.includes(`${method} ${req.path}`)) return next();
    res.status(403).json({ error: "sparkDash is read-only here" });
  };
}

export function registerDeploymentRoute(app, { readOnly, writable }) {
  app.get("/api/deployment", (_req, res) => res.json({ readOnly, writable }));
}
