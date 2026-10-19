"""sparkdash-clock (root): only the five limits, applied to every GPU through NVML, or nothing at all."""

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sparkdash_clock as clock  # noqa: E402


class FakeNvml:
    def __init__(self, gpus=2, fail=None):
        self.gpus, self.fail, self.calls = gpus, fail, []
    def nvmlInit(self): self.calls.append("init")
    def nvmlShutdown(self): self.calls.append("shutdown")
    def nvmlDeviceGetCount(self): return self.gpus
    def nvmlDeviceGetHandleByIndex(self, i): return i
    def nvmlDeviceSetGpuLockedClocks(self, h, lo, hi):
        if self.fail:
            raise RuntimeError(self.fail)
        self.calls.append(("lock", h, lo, hi))
    def nvmlDeviceResetGpuLockedClocks(self, h): self.calls.append(("reset", h))


def run(tmp_path, request, nvml):
    req, state = tmp_path / "clock-request", tmp_path / "state"
    req.write_text(request if isinstance(request, str) else json.dumps(request))
    code = clock.main(req, state, nvml)
    return code, json.loads(state.read_text()), state


def test_a_limit_locks_every_gpu_from_zero_to_it(tmp_path):
    nvml = FakeNvml()
    code, state, _ = run(tmp_path, {"limit": "2200", "id": "a1"}, nvml)
    assert code == 0 and (state["limit"], state["id"], state["ok"], state["error"]) == ("2200", "a1", True, None)
    assert [c for c in nvml.calls if c not in ("init", "shutdown")] == [("lock", 0, 0, 2200), ("lock", 1, 0, 2200)]


def test_unlocked_releases_every_gpu(tmp_path):
    nvml = FakeNvml()
    code, state, _ = run(tmp_path, {"limit": "unlocked", "id": "b2"}, nvml)
    assert code == 0 and state["limit"] == "unlocked" and state["ok"]
    assert [c for c in nvml.calls if c not in ("init", "shutdown")] == [("reset", 0), ("reset", 1)]


def test_anything_else_is_refused_and_nothing_changes(tmp_path):
    for request in ({"limit": "9999", "id": "c"}, {"limit": "300", "id": "c"}, {"limit": "2200; reboot", "id": "c"},
                    {"limit": 2200, "id": "c"}, {"limit": "2200"}, {"limit": "2200", "id": "x" * 65},
                    "not json", json.dumps({"limit": "2200", "id": "c", "pad": "x" * 2000})):
        nvml = FakeNvml()
        code, state, _ = run(tmp_path, request, nvml)
        assert code == 1 and state["ok"] is False and state["error"].startswith("refused")
        assert nvml.calls == [], request


def test_a_driver_error_is_reported_not_hidden(tmp_path):
    code, state, _ = run(tmp_path, {"limit": "1800", "id": "d4"}, FakeNvml(fail="Insufficient Permissions"))
    assert code == 1 and state["ok"] is False and "Insufficient Permissions" in state["error"] and state["id"] == "d4"


def test_the_state_is_readable_by_sparkdash_api(tmp_path):
    _, _, state = run(tmp_path, {"limit": "2400", "id": "e5"}, FakeNvml())
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o644


def test_the_two_services_agree_on_the_limits():
    import sparkdash_api
    assert clock.PRESETS == sparkdash_api.CLOCK_PRESETS
