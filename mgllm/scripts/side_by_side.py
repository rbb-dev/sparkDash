"""One-off, read-only: each known sparkDash line run for real (bash, as SSH would) next to sparkdash-api's answer."""
import json, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
import sparkdash_api as api
known = json.load(open(sys.argv[1] + "/known-commands.json"))["commands"]
s, out = api.Spark(), {}
for name, line in known.items():
    t0 = time.time(); real = subprocess.run(["bash", "-c", line], capture_output=True, text=True).stdout; t1 = time.time()
    ours = api.ANSWERS[name](s); t2 = time.time()
    out[name] = {"real": real.strip(), "ours": ours.strip(), "real_ms": round((t1 - t0) * 1000), "ours_ms": round((t2 - t1) * 1000)}
print(json.dumps(out))
