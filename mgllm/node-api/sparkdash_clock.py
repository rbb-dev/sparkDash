#!/usr/bin/env python3
"""sparkdash-clock: set or release this Spark's GPU clock lock for sparkDash's GPU clock control (mgllm).

Root and oneshot, started by sparkdash-clock.path whenever sparkdash-api (unprivileged) writes a request to
/run/sparkdash-api/clock-request: {"limit": <preset>, "id": <request id>}. Only these limits exist: "unlocked" (the
driver's default) and 2400 / 2200 / 2000 / 1800 MHz, each a lock of 0..limit on every GPU, as
`nvidia-smi -lgc 0,<limit>` sets it (NVML nvmlDeviceSetGpuLockedClocks / nvmlDeviceResetGpuLockedClocks). Anything
else is refused and nothing changes. What was applied goes to /run/sparkdash-clock/state (world-readable), which a
reboot clears exactly as it clears the lock. The driver itself reports no lock anywhere (checked 2026-10-10: neither
`nvidia-smi -q` nor NVML's getters change), so that file is the only record of it.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PRESETS = ("unlocked", "2400", "2200", "2000", "1800")
REQUEST = Path("/run/sparkdash-api/clock-request")
STATE = Path("/run/sparkdash-clock/state")
MAX_REQUEST = 1024


def load_nvml() -> Any:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))
    import pynvml                                                # NVIDIA's binding, vendored beside sparkdash-api
    return pynvml


def read_request(path: Path) -> tuple[str, str]:
    """(limit, id) from the request file, or ValueError: a preset and a short id, nothing else."""
    raw = path.read_bytes()
    if len(raw) > MAX_REQUEST:
        raise ValueError("request too large")
    req = json.loads(raw)
    limit, rid = req.get("limit"), req.get("id")
    if limit not in PRESETS:
        raise ValueError(f"limit {limit!r} is not one of {', '.join(PRESETS)}")
    if not isinstance(rid, str) or not 0 < len(rid) <= 64:
        raise ValueError("bad request id")
    return limit, rid


def apply(limit: str, nvml: Any) -> int:
    """Lock every GPU to 0..limit MHz, or release them all; the number of GPUs."""
    nvml.nvmlInit()
    try:
        count = nvml.nvmlDeviceGetCount()
        for i in range(count):
            h = nvml.nvmlDeviceGetHandleByIndex(i)
            if limit == "unlocked":
                nvml.nvmlDeviceResetGpuLockedClocks(h)
            else:
                nvml.nvmlDeviceSetGpuLockedClocks(h, 0, int(limit))
        return count
    finally:
        nvml.nvmlShutdown()


def write_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state) + "\n")
    tmp.chmod(0o644)
    os.replace(tmp, path)


def main(request: Path = REQUEST, state: Path = STATE, nvml: Any = None) -> int:
    try:
        limit, rid = read_request(request)
    except (OSError, ValueError) as exc:
        print(f"sparkdash-clock: refused: {exc}", flush=True)
        write_state(state, {"limit": None, "id": None, "ok": False, "error": f"refused: {exc}", "at": time.time()})
        return 1
    try:
        gpus = apply(limit, nvml or load_nvml())
    except Exception as exc:                                     # the driver refused: say so, as the dashboard shows it
        print(f"sparkdash-clock: {limit} failed: {exc}", flush=True)
        write_state(state, {"limit": limit, "id": rid, "ok": False, "error": str(exc), "at": time.time()})
        return 1
    print(f"sparkdash-clock: {limit} on {gpus} GPU(s)", flush=True)
    write_state(state, {"limit": limit, "id": rid, "ok": True, "error": None, "at": time.time()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
