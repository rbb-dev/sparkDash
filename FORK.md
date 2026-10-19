# About this fork

This is a fork of [MiaAI-Lab/sparkDash](https://github.com/MiaAI-Lab/sparkDash). Its `main` is sparkDash's `main` plus one
commit: it runs sparkDash on a host near the Sparks instead of on a Spark, reads the Sparks without SSH through a small
service on each, and can run read-only behind a reverse proxy. Every other file is sparkDash's.

## What the commit changes in sparkDash

| Change | What it does |
|---|---|
| Read through sparkdash-api | A unit with an `execUrl` sends sparkDash's shell lines to the sparkdash-api on that Spark instead of SSH (`server/collectors/ssh.js`). The token goes only to URLs under `SPARKDASH_EXEC_URL_PREFIXES`, compared as parsed URLs, with no redirects. |
| Served under a sub-path | `VITE_BASE` (e.g. `/sparkdash/`): requests, links, reloads, deep links and the service worker stay under it. |
| GPU clock cap | One control on the head unit's card sets the same cap on every Spark read through sparkdash-api (no SSH or sudo); sparkDash's own Edit-unit clock cap steps aside on those Sparks and says why. |
| TensorFold reads Active | A reachable TensorFold engine reads Active on the Engine tile (it has no sleep state). |
| Read-only deployments | `SPARKDASH_READ_ONLY` refuses every write outside `SPARKDASH_WRITE_ALLOW`, before every route that writes; the page hides the controls those writes come from. |

## What the commit adds

`mgllm/`: sparkdash-api (the Spark-side service and its root clock helper), the dashboard host's deployment, and the
scripts that capture sparkDash's lines and check sparkdash-api's answers against the real ones. See `mgllm/README.md`.

## Credits

sparkDash is by [MiaAI-Lab](https://github.com/MiaAI-Lab). sparkdash-api uses NVIDIA's NVML binding (nvidia-ml-py, BSD,
vendored as `mgllm/node-api/vendor/pynvml.py`) and, where a Spark has it, Mellanox's `dcbnetlink` module from mlnx-tools
(imported from the system, not shipped). This fork's changes: by [rbb-dev](https://github.com/rbb-dev), developed with
[Claude Code](https://claude.com/claude-code).
