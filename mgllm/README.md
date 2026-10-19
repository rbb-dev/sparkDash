# sparkDash off the Sparks: sparkdash-api and its deployment

This folder runs sparkDash on a separate host (a gateway or any server near the Sparks) instead of on a Spark, and lets
it read the Sparks **without SSH and without running a command on them**. sparkDash normally reads a remote unit by
running shell lines over SSH (`server/collectors/ssh.js`, `sshExec`). A unit with an `execUrl` sends those lines to
**sparkdash-api** on that Spark instead. sparkdash-api knows the lines sparkDash sends, answers each from the sources
the line itself reads, in the text it prints, and refuses anything else, so sparkDash's own parsers run unchanged.

```
browser --https+password--> reverse proxy /sparkdash/ --> sparkDash (dashboard host, docker, 127.0.0.1:5555)
                                                              |  POST {"cmd": <a known line>}, bearer token
                                                              v
                                          sparkdash-api on each Spark (:9701), reachable from the dashboard host only
                                          answers from /proc, /sys, NVML, the kernel journal, ethtool's ioctls and
                                          Mellanox's DCB library; any other line: 403
```

## What sparkdash-api answers

- `/proc` and `/sys` files, read directly (no `cat`, `grep` or shell).
- NVML through NVIDIA's own Python binding (`node-api/vendor/pynvml.py`, nvidia-ml-py, BSD) where the line runs
  `nvidia-smi`, NVML's front end.
- psutil for IPv4 addresses (`ip -4 addr`), `os.statvfs` for `df`, the systemd journal binding for `journalctl -k`.
- The ConnectX-7 ports (sparkDash 2.50's RoCE panel): `ethtool -S` / `ethtool -a` through ethtool's own ioctls
  (`SIOCETHTOOL`, allowed without a privilege) and `mlnx_qos -i` through Mellanox's `dcbnetlink` module (the library
  `mlnx_qos` imports, used in place; nothing is run). The traffic-class lines of `mlnx_qos` are left out: sparkDash does
  not read them.
- The vLLM-container models line answers `[]`: sparkdash-api has no docker access (docker's group equals root).

`node-api/known-commands.json` lists the known lines (captured by `scripts/capture-commands.sh`). A line is matched by
its exact text, so lines of two sparkDash releases can be known side by side while one replaces the other.

## GPU clock cap

One dropdown on the head unit's card (GPU auto, or GPU ≤ 2400 / 2200 / 2000 / 1800 MHz) sets the same limit on every
Spark read through sparkdash-api: a tensor-parallel pair runs at its slower GPU's pace, so they are never set apart.

```
dropdown --POST /api/gpu-clock--> sparkDash --> each sparkdash-api: "mgllm:gpu-clock set <limit>"
  --> writes /run/sparkdash-api/clock-request (unprivileged)
  --> sparkdash-clock.path starts sparkdash-clock.service (root, oneshot, no network): NVML lock 0..limit, or release
  --> /run/sparkdash-clock/state, which "mgllm:gpu-clock" reads back
```

Nothing is remembered: a reboot returns the GPUs to the driver's default. sparkDash's own clock cap (Edit unit, over SSH
with sudo) steps aside on these Sparks and says why.

## Read-only deployments

`SPARKDASH_READ_ONLY=1` makes the server refuse every write except those in `SPARKDASH_WRITE_ALLOW` (method and exact
path, e.g. `PUT /api/settings,POST /api/gpu-clock`), and the page hides the controls that would send a refused write
(shutdown, benchmarks, tools, launchers, add/edit, alert channels...). The guard runs before every route that writes.

## Files

| Path | What |
|---|---|
| `node-api/` | sparkdash-api, its known lines, its systemd unit; the clock helper (`sparkdash_clock.py`, `sparkdash-clock.path` + `.service`); tests (pytest) |
| `deploy/` | the dashboard host's image, unit, example Spark list (`sparks.json`) and environment (`env`), first settings, reverse-proxy helper |
| `scripts/install-node-api.sh user@spark` | install or update sparkdash-api on one Spark (makes the token on first use) |
| `scripts/deploy-gateway.sh` | build this fork's sparkDash at `/sparkdash/` and run it on the dashboard host |
| `scripts/capture-commands.sh [commit]` | run a sparkDash commit for 90 s against two Sparks and list the lines sparkdash-api does not know |
| `scripts/parity.sh` | on each Spark, run every known line for real and through sparkdash-api, and compare what sparkDash's own collectors read from each |

## Setting it up

1. Edit `deploy/sparks.json` (each Spark's address and `execUrl`) and `deploy/env` (`SPARKDASH_EXEC_URL_PREFIXES`: the
   only URLs the token is ever sent to; `SPARKDASH_ALLOWED_HOSTS`: your public host name). The examples use the
   documentation addresses 192.0.2.x.
2. In `node-api/sparkdash-api.service`, set `IPAddressAllow` to the dashboard host's address.
3. `scripts/install-node-api.sh user@spark` for each Spark. The token is kept in `$SPARKDASH_API_ENV` (default
   `~/.config/sparkdash-api.env`, outside the repository).
4. `GATEWAY=user@dashboard-host [GATEWAY_SSH_PORT=22] scripts/deploy-gateway.sh`.

Checks: `SPARK1_HOST=... SPARK2_HOST=... SPARK_USER=... scripts/capture-commands.sh` (no NEW line), and
`SPARKS='user@spark1 user@spark2' scripts/parity.sh` (only moving values differ: power, clocks, temperatures, free
memory, counters).

## Tests

`node-api/tests` (pytest); in sparkDash, `server/collectors/__tests__/ssh.execurl.test.js`, `server/__tests__/gpuClock.test.js`,
`server/__tests__/readOnly.test.js`, `server/gpuclock/__tests__/execurl.test.js`, two in
`server/collectors/__tests__/LlmProbe.tensorfold.test.js`, `src/api/base.test.ts`, `src/api/deployment.test.ts` and
`src/components/OverviewPage/ClockCap.test.ts`, with sparkDash's own suites.
