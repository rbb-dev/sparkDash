#!/usr/bin/env bash
# The shell lines a sparkDash release sends to remote Sparks: run it here for CAPTURE_SECONDS (90) against both
# Sparks over SSH, its own transport, with sshExec logging every line; then compare with node-api/known-commands.json.
# Read-only (it only monitors); nothing is installed anywhere.
#
#   mgllm/scripts/capture-commands.sh [sparkDash commit]     (default: this fork's HEAD)
#
# Prints the lines known-commands.json lacks and writes the full capture to ./captured-commands.json, ready to
# replace known-commands.json once every new line has an answer in sparkdash_api.py and scripts/parity.sh agrees.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${WORK:-$(cd "$HERE/.." && pwd)}                       # this fork's checkout
export PATH=${NODE_BIN:-$HOME/.nvm/versions/node/v22.21.1/bin}:$PATH
COMMIT=${1:-HEAD}
B=$(mktemp -d)
trap 'git -C "$WORK" worktree remove --force "$B/src" 2>/dev/null; rm -rf "$B"' EXIT
git -C "$WORK" cat-file -e "$COMMIT^{commit}" 2>/dev/null || git -C "$WORK" fetch -q origin
git -C "$WORK" worktree add -q --detach "$B/src" "$COMMIT"
(cd "$B/src" && npm ci --omit=dev --maxsockets=1 --prefer-offline --no-audit --no-fund --loglevel=error >/dev/null)
python3 - "$B/src/server/collectors/ssh.js" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); t = p.read_text()
head = "export async function sshExec(spark, cmd, options = {}) {\n"
assert t.count(head) == 1, "sshExec moved: update this script"
p.write_text(t.replace(head, head + '  (await import("node:fs")).appendFileSync(process.env.SPARKDASH_CMD_LOG, '
                       'JSON.stringify({ spark: spark?.id, cmd }) + "\\n");\n'))
PY
cat > "$B/sparks.json" <<JSON
{"sparks": [
 {"id": "spark1", "name": "spark 1", "lanIp": "${SPARK1_HOST:?set SPARK1_HOST}", "ssh": {"user": "${SPARK_USER:?set SPARK_USER}", "auth": "key", "port": 22}, "llmPorts": [8000], "role": "head"},
 {"id": "spark2", "name": "spark 2", "lanIp": "${SPARK2_HOST:?set SPARK2_HOST}", "ssh": {"user": "${SPARK_USER}", "auth": "key", "port": 22}, "role": "worker", "workerHeadId": "spark1"}
]}
JSON
echo "capture-commands: running sparkDash ${COMMIT:0:7} for ${CAPTURE_SECONDS:-90} s against both Sparks"
(cd "$B/src" && PORT=5599 BIND_HOST=127.0.0.1 NODE_ENV=production SPARKS_JSON_PATH="$B/sparks.json" \
   SPARKDASH_CMD_LOG="$B/cmds.jsonl" timeout "${CAPTURE_SECONDS:-90}" node server/index.js >/dev/null 2>&1) || true
python3 - "$B/cmds.jsonl" "$HERE/node-api/known-commands.json" "$COMMIT" <<'PY'
import collections, json, sys
seen = collections.Counter(json.loads(l)["cmd"] for l in open(sys.argv[1]))
known = json.load(open(sys.argv[2]))
lines = set(known["commands"].values())
new = [c for c in seen if c not in lines]
print(f"capture-commands: {len(seen)} distinct lines ({sum(seen.values())} runs); known {len(seen) - len(new)}, new {len(new)}")
for c in new:
    print("  NEW:", " ".join(c.split())[:300])
gone = sorted(n for n, l in known["commands"].items() if l not in seen and n != "ping")
if gone:
    print("  not seen in this run (rare, or gone):", ", ".join(gone))
json.dump({"commit": sys.argv[3], "lines": sorted(seen)}, open("captured-commands.json", "w"), indent=1)
PY
