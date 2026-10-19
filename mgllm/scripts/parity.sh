#!/usr/bin/env bash
# Prove sparkdash-api answers as the real lines do: on each Spark, every known line is run for real and through
# node-api/sparkdash_api.py (one-off, read-only, in a temp folder that is removed; nothing installed), then both answers
# go through sparkDash's own SystemCollector (its sshExec stubbed to return them) and the fields that differ are listed.
# Values that move between two reads a moment apart (power, clocks, temperatures, free memory) differ a little;
# nothing else should.
#
#   mgllm/scripts/parity.sh [user@spark ...]       (default: both Sparks)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-$(cd "$HERE/.." && pwd)}                       # this fork's checkout
export PATH=${NODE_BIN:-$HOME/.nvm/versions/node/v22.21.1/bin}:$PATH
TARGETS=("$@")
[[ ${#TARGETS[@]} -gt 0 ]] || read -ra TARGETS <<< "${SPARKS:?set SPARKS='user@spark1 user@spark2'}"
B=$(mktemp -d)
trap 'git -C "$WORK" worktree remove --force "$B/src" 2>/dev/null; rm -rf "$B"' EXIT

# the harness: this fork's commit (COMMIT, default HEAD), with sshExec answering from the captured text
git -C "$WORK" worktree add -q --detach "$B/src" "${COMMIT:-HEAD}"
(cd "$B/src" && npm ci --omit=dev --maxsockets=1 --prefer-offline --no-audit --no-fund --loglevel=error >/dev/null)
python3 - "$B/src/server/collectors/ssh.js" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text()
start = t.index("export async function sshExec(spark, cmd, options = {}) {")
end = t.index("\n}\n", start) + 3
p.write_text(t[:start] + 'export async function sshExec(spark, cmd, options = {}) {\n'
             '  const a = globalThis.__ANSWERS?.[cmd];\n'
             '  if (a === undefined) throw new Error(`no captured answer for: ${cmd.slice(0, 60)}`);\n'
             '  return a;\n}\n' + t[end:])
PY
cat > "$B/src/parity.mjs" <<'JS'
import fs from "node:fs";
import { SystemCollector } from "./server/collectors/SystemCollector.js";
const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const known = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).commands;
const spark = { id: "x", name: "x", kind: "spark", platform: "linux", lanIp: "10.0.0.1", isLocal: false,
  ssh: { user: "u", auth: "key", port: 22 }, llmPorts: [8000], role: "head", disabledDevices: [], disabledInterfaces: [] };
const out = {};
for (const side of ["real", "ours"]) {
  globalThis.__ANSWERS = Object.fromEntries(Object.entries(known).map(([n, line]) => [line, data[n][side]]));
  const c = new SystemCollector(spark), r = {};
  for (const [k, f] of Object.entries({ gpu: () => c.collectGpu(), cpu: () => c.collectCpu(), storage: () => c.collectStorage(),
    network: () => c.collectNetwork(), memory: () => c.collectUnifiedMemory(), kernel: () => c._kernelErrors(), nverr: () => c._nvErrNoMemory(),
    roce: () => c.collectRoce(), models: () => c.collectResidentModels() })) {   // the last two: sparkDash 2.50
    try { r[k] = await f(); } catch (e) { r[k] = { error: String(e.message || e) }; }
  }
  out[side] = r;
}
console.log(JSON.stringify(out));
JS

for t in "${TARGETS[@]}"; do
  d=$(ssh -o BatchMode=yes "$t" 'mktemp -d')
  scp -q "$HERE"/node-api/{sparkdash_api.py,known-commands.json} "$HERE"/scripts/side_by_side.py "$t:$d/"
  ssh "$t" "mkdir -p $d/vendor" && scp -q "$HERE/node-api/vendor/pynvml.py" "$t:$d/vendor/"
  ssh "$t" "cd $d && python3 side_by_side.py $d; rm -rf $d" > "$B/side.json"
  (cd "$B/src" && node parity.mjs "$B/side.json" "$HERE/node-api/known-commands.json") > "$B/parsed.json"
  python3 - "$B/parsed.json" "$t" <<'PY'
import json, sys
def walk(a, b, path, out):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b: out.append((path + "." + k, "only in " + ("real" if k in a else "ours"), a.get(k), b.get(k)))
            else: walk(a[k], b[k], path + "." + k, out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b): out.append((path, "list length", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)): walk(x, y, f"{path}[{i}]", out)
    elif a != b: out.append((path, "differs", a, b))
d = json.load(open(sys.argv[1])); diffs = []
walk(d["real"], d["ours"], "", diffs)
errors = [p for p, _, x, y in diffs if "error" in p]
print(f"parity {sys.argv[2]}: {len(diffs)} differing fields{' (ERRORS: ' + ', '.join(errors) + ')' if errors else ''}")
for p, kind, x, y in diffs: print(f"  {p:45s} real={str(x)[:30]:32s} ours={str(y)[:30]}")
PY
done
