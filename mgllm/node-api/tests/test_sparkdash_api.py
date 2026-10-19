"""sparkdash-api's answers on a made-up Spark: each test states what the shell line prints for the same files."""

import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sparkdash_api as api  # noqa: E402

KNOWN = Path(__file__).resolve().parents[1] / "known-commands.json"


def tree(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            p.write_text(text)


class NvmlError(Exception):
    pass


class FakeNvml:
    """Two GPUs: the first gives every value, the second refuses the power limit and memory, as GB10 does."""
    NVML_TEMPERATURE_GPU, NVML_CLOCK_SM = 0, 1

    def nvmlInit(self): pass
    def nvmlDeviceGetCount(self): return 2
    def nvmlDeviceGetHandleByIndex(self, i): return i
    def nvmlDeviceGetTemperature(self, h, kind): return 79 - h
    def nvmlDeviceGetUtilizationRates(self, h): return SimpleNamespace(gpu=96)
    def nvmlDeviceGetPowerUsage(self, h): return 68390
    def nvmlDeviceGetClockInfo(self, h, kind): return 2418
    def nvmlDeviceGetMaxClockInfo(self, h, kind): return 3003
    def nvmlDeviceGetCurrentClocksEventReasons(self, h): return 0x40 | 0x4 if h == 0 else 0
    def nvmlDeviceGetIndex(self, h): return h
    def nvmlDeviceGetName(self, h): return "NVIDIA GB10"
    def nvmlDeviceGetUUID(self, h): return f"GPU-{h}"
    def nvmlSystemGetProcessName(self, pid): return "/usr/bin/python3"

    def nvmlDeviceGetPowerManagementLimit(self, h):
        if h == 1:
            raise NvmlError("not supported")
        return 240000

    def nvmlDeviceGetMemoryInfo(self, h):
        if h == 1:
            raise NvmlError("not supported")
        return SimpleNamespace(used=3 * 2**30, total=8 * 2**30)

    def nvmlDeviceGetComputeRunningProcesses(self, h):
        return [SimpleNamespace(pid=2747736, usedGpuMemory=98067 * 2**20)] if h == 0 else \
               [SimpleNamespace(pid=7, usedGpuMemory=None)]


def spark(tmp_path, **kw):
    return api.Spark(root=str(tmp_path), nvml=kw.pop("nvml", FakeNvml()), ipv4=kw.pop("ipv4", lambda: []),
                     statvfs=kw.pop("statvfs", os.statvfs), kernel_log=kw.pop("kernel_log", lambda: []))


MEMINFO = "MemTotal:       127535252 kB\nMemFree:  1 kB\nMemAvailable:   10075192 kB\nBuffers: 2 kB\n"


def test_gpu_answer_is_nvidia_smis_four_parts_with_na_where_the_driver_gives_nothing(tmp_path):
    tree(tmp_path, {"/proc/meminfo": MEMINFO})
    assert api.answer_gpu(spark(tmp_path)) == (
        "79, 96, 68.39, 240.00, 2418, 3003, Active, Not Active, Not Active, Active, 0, NVIDIA GB10, GPU-0\n"
        "78, 96, 68.39, [N/A], 2418, 3003, Not Active, Not Active, Not Active, Not Active, 1, NVIDIA GB10, GPU-1\n"
        "---\n"
        "3072, 8192\n"
        "[N/A], [N/A]\n"
        "---\n"
        "2747736, /usr/bin/python3, 98067, GPU-0\n"
        "7, /usr/bin/python3, [N/A], GPU-1\n"
        "---\n"
        "MemTotal:       127535252 kB\nMemAvailable:   10075192 kB\n")


def test_without_nvml_the_gpu_parts_are_empty_as_failed_nvidia_smi_calls_print_nothing(tmp_path):
    class Broken(FakeNvml):
        def nvmlDeviceGetCount(self): raise NvmlError("driver gone")
    tree(tmp_path, {"/proc/meminfo": MEMINFO})
    assert api.answer_gpu(spark(tmp_path, nvml=Broken())) == "---\n---\n---\nMemTotal:       127535252 kB\nMemAvailable:   10075192 kB\n"


def test_cpu_answer_keeps_the_loops_quirks(tmp_path):
    tree(tmp_path, {
        "/proc/stat": "cpu  10 20 30 40 0 0 0 0 0 0\ncpu0 1 2 3 4 0 0 0 0 0 0\n",
        "/proc/cpuinfo": "processor\t: 0\nBogoMIPS\t: 2000.00\nCPU architecture: 8\nCPU architecture: 8\n",
        "/sys/class/hwmon/hwmon0/name": "acpitz\n",
        "/sys/class/hwmon/hwmon0/temp1_input": "41000\n",
        "/sys/class/hwmon/hwmon0/temp2_input": "99000\n",          # only the first input counts: the loop breaks
        "/sys/class/hwmon/hwmon1/name": "nvme\n",                   # not a CPU sensor
        "/sys/class/hwmon/hwmon1/temp1_input": "30000\n",
        "/sys/class/hwmon/hwmon2/name": "soc_thermal\n",
        "/sys/class/hwmon/hwmon2/temp1_input": "",                  # empty first input: nothing, even with a second
        "/sys/class/hwmon/hwmon2/temp2_input": "50000\n",
        "/sys/class/thermal/thermal_zone0/type": "cpu-thermal\n",
        "/sys/class/thermal/thermal_zone0/temp": "55000\n",
        "/sys/class/thermal/thermal_zone1/type": "gpu-thermal\n",   # no temp file: skipped
    })
    assert api.answer_cpu(spark(tmp_path)) == (
        "cpu  10 20 30 40 0 0 0 0 0 0\n---\nCPU architecture: 8\n---\nacpitz 41000\ncpu-thermal 55000\n")


def test_net_answer_copies_the_files_and_formats_the_loops(tmp_path):
    dev = "Inter-|   Receive\n face |bytes\n  eth0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n"
    route = "Iface\tDestination\neth0\t00000000\n"
    tree(tmp_path, {
        "/proc/net/dev": dev, "/proc/net/route": route,
        "/sys/class/net/eth0/operstate": "up\n", "/sys/class/net/eth0/speed": "10000\n",
        "/sys/class/net/lo/operstate": "unknown\n", "/sys/class/net/lo/speed": None,   # unreadable speed: "lo:"
        "/sys/class/net/enP7s7/operstate": "down\n",
        "/sys/class/net/enP7s7/address": "4c:bb:47:2a:00:01\n",
    })
    (tmp_path / "sys/class/net/lo/speed").mkdir(parents=True)                         # reading it fails, as EINVAL does
    ipv4 = lambda: [(2, "eth0", "10.0.0.5", 24), (2, "eth0", "10.0.0.6", 24), (1, "lo", "127.0.0.1", 8)]
    assert api.answer_net(spark(tmp_path, ipv4=ipv4)) == (
        dev + "---\n" + route + "---\n"
        "1: lo: <> mtu 0\n    inet 127.0.0.1/8 scope global lo\n"
        "2: eth0: <> mtu 0\n    inet 10.0.0.5/24 scope global eth0\n    inet 10.0.0.6/24 scope global eth0\n"
        "---\nenP7s7:down\neth0:up\nlo:unknown\n"
        "---\n4c:bb:47:2a:00:01\n---\n"
        "eth0:10000\nlo:\n")


def test_df_answer_is_one_local_filesystem_a_device_with_the_shortest_mount(tmp_path):
    tree(tmp_path, {"/proc/1/mounts": (
        "/dev/nvme0n1p2 / ext4 rw 0 0\n"
        "/dev/nvme0n1p2 /var/lib/docker/x ext4 rw 0 0\n"       # the same device again: df shows it once
        "/dev/nvme0n1p1 /boot/efi vfat rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n"                             # excluded type
        "overlay /var/lib/docker/overlay2/m overlay rw 0 0\n"
        "server:/x /mnt/nfs nfs rw 0 0\n")})                   # not a local device
    sizes = {"/": (1000, 400, 300), "/boot/efi": (10, 9, 9)}   # blocks, free, available (1-byte fragments)
    statvfs = lambda m: SimpleNamespace(f_frsize=1, f_blocks=sizes[m][0], f_bfree=sizes[m][1], f_bavail=sizes[m][2])
    assert api.answer_df(spark(tmp_path, statvfs=statvfs)) == (
        "Filesystem Type 1B-blocks Used Available Use% Mounted on\n"
        "/dev/nvme0n1p2 ext4 1000 600 300 67% /\n"            # 600 / (600 + 300) = 66.7 -> 67 (df rounds up)
        "/dev/nvme0n1p1 vfat 10 1 9 10% /boot/efi\n")


def test_kernel_log_answers_count_and_quote_the_last_xid(tmp_path):
    log = lambda: [
        (1_760_000_000_000_000, "spark", "kernel", "NVRM: Xid (PCI:0000:01:00): 31, pid=1"),
        (1_760_000_001_000_000, "spark", "kernel", "NVRM: nvAssertFailed NV_ERR_NO_MEMORY"),
        (1_760_000_002_000_000, "spark", "kernel", "NVRM: Xid (PCI:0000:01:00): 43, pid=2"),
        (1_760_000_003_000_000, "spark", "kernel", "Out of memory: Killed process 5 (python3)"),
        (1_760_000_004_000_000, "spark", "kernel", "python3 invoked oom-kill: gfp_mask=0x0"),
    ]
    s = spark(tmp_path, kernel_log=log)
    assert api.answer_nverr(s) == "1\n"
    first, last, end = api.answer_kernel_errors(s).split("\n")
    assert (first, end) == ("xid=2 oom=2", "")
    assert last.endswith(" spark kernel: NVRM: Xid (PCI:0000:01:00): 43, pid=2")
    assert api.answer_kernel_errors(spark(tmp_path)) == "xid=0 oom=0\n\n"


def test_every_known_line_has_an_answer_and_an_unknown_name_stops_the_start(tmp_path):
    lines = api.load_commands(KNOWN)
    assert sorted(lines.values()) == sorted(api.ANSWERS)
    doc = json.loads(KNOWN.read_text())
    doc["commands"]["new_one"] = "uname -a"
    (tmp_path / "k.json").write_text(json.dumps(doc))
    with pytest.raises(SystemExit):
        api.load_commands(tmp_path / "k.json")


def request(path: str, body: dict | None, token: str = "t" * 40) -> tuple[int, str]:
    """One POST through the handler, without a socket."""
    raw = json.dumps(body).encode() if body is not None else b""
    handler = api.Api.__new__(api.Api)
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.client_address, handler.request_version = path, ("192.0.2.1", 1), "HTTP/1.1"
    handler.command, handler.requestline = "POST", f"POST {path} HTTP/1.1"
    handler.headers = {"Authorization": f"Bearer {token}", "Content-Length": str(len(raw))}
    handler.do_POST()
    head, _, payload = handler.wfile.getvalue().decode().partition("\r\n\r\n")
    return int(head.split()[1]), payload


@pytest.fixture
def service(tmp_path):
    tree(tmp_path, {"/proc/uptime": "123.45 678.90\n"})
    api.Api.token, api.Api.commands = "t" * 40, api.load_commands(KNOWN)
    api.Api.spark = spark(tmp_path)
    return api.Api


def test_a_known_line_is_answered(service):
    assert request("/sparkdashapi/exec", {"cmd": "cat /proc/uptime"}) == (200, "123.45 678.90\n")


def test_anything_else_is_refused_and_never_run(service):
    for cmd in ("rm -rf /", "cat /proc/uptime; id", "sudo shutdown -h now", "cat /etc/shadow"):
        assert request("/sparkdashapi/exec", {"cmd": cmd})[0] == 403


def test_a_wrong_token_is_refused(service):
    assert request("/sparkdashapi/exec", {"cmd": "cat /proc/uptime"}, token="wrong")[0] == 401


def test_any_other_path_is_not_found(service):
    assert request("/sparkdashapi/relay/x/exec", {"cmd": "cat /proc/uptime"})[0] == 404


# ---- the GPU clock control -----------------------------------------------------------------------------------------
import threading  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sparkdash_clock  # noqa: E402
from test_sparkdash_clock import FakeNvml as ClockNvml  # noqa: E402


def clock_spark(tmp_path):
    (tmp_path / "run/sparkdash-api").mkdir(parents=True)
    (tmp_path / "run/sparkdash-clock").mkdir(parents=True)
    s = spark(tmp_path)
    s.clock_wait = 3.0
    return s


def helper_once(tmp_path, nvml):
    """sparkdash-clock as its .path unit runs it: once the request file appears, on that file."""
    req, state = tmp_path / "run/sparkdash-api/clock-request", tmp_path / "run/sparkdash-clock/state"
    def go():
        for _ in range(300):
            if req.exists():
                sparkdash_clock.main(req, state, nvml)
                return
            threading.Event().wait(0.01)
    t = threading.Thread(target=go)
    t.start()
    return t


def test_the_clock_is_unlocked_before_anything_was_set(tmp_path):
    assert json.loads(api.answer_clock(clock_spark(tmp_path)))["limit"] == "unlocked"


def test_setting_a_limit_answers_what_the_root_helper_applied(tmp_path):
    s, nvml = clock_spark(tmp_path), ClockNvml()
    t = helper_once(tmp_path, nvml)
    answer = json.loads(api.ANSWERS["clock_set_2200"](s))
    t.join()
    assert (answer["limit"], answer["ok"]) == ("2200", True)
    assert ("lock", 0, 0, 2200) in nvml.calls
    assert json.loads(api.answer_clock(s))["limit"] == "2200"


def test_a_refusal_by_the_helper_fails_the_request(tmp_path):
    s = clock_spark(tmp_path)
    t = helper_once(tmp_path, ClockNvml(fail="Insufficient Permissions"))
    with pytest.raises(RuntimeError, match="Insufficient Permissions"):
        api.ANSWERS["clock_set_1800"](s)
    t.join()


def test_without_the_helper_the_request_fails_in_time(tmp_path):
    s = clock_spark(tmp_path)
    s.clock_wait = 0.3
    with pytest.raises(RuntimeError, match="did not answer"):
        api.ANSWERS["clock_set_2400"](s)


def test_only_the_five_limits_are_known_lines():
    lines = api.load_commands(KNOWN)
    assert {l for l in lines if l.startswith("mgllm:")} == {"mgllm:gpu-clock"} | {
        f"mgllm:gpu-clock set {p}" for p in ("unlocked", "2400", "2200", "2000", "1800")}


def test_any_other_clock_line_is_refused(service):
    for cmd in ("mgllm:gpu-clock set 9999", "mgllm:gpu-clock set 2200 ", "mgllm:gpu-clock set 2200; reboot"):
        assert request("/sparkdashapi/exec", {"cmd": cmd})[0] == 403
