"""The lines sparkDash 2.50 (3e6d4ef) added or changed, answered on a made-up Spark: each test states what the shell line
prints for the same sources. Captured 2026-10-18 by scripts/capture-commands.sh: a GPU line in sections (NVIDIA, AMD,
Intel, memory, her clock-cap marker), df with the block devices' I/O counters, the RoCE devices and their links, the
ConnectX-7 ports' counters, pause and QoS, and the vLLM containers' models."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sparkdash_api as api  # noqa: E402

KNOWN = Path(__file__).resolve().parents[1] / "known-commands.json"


def tree(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        if text is not None:
            p.write_text(text)


class FakeNvml:
    NVML_TEMPERATURE_GPU, NVML_CLOCK_SM = 0, 1
    def nvmlInit(self): pass
    def nvmlDeviceGetCount(self): return 1
    def nvmlDeviceGetHandleByIndex(self, i): return i
    def nvmlDeviceGetTemperature(self, h, kind): return 61
    def nvmlDeviceGetUtilizationRates(self, h): return SimpleNamespace(gpu=93)
    def nvmlDeviceGetPowerUsage(self, h): return 41120
    def nvmlDeviceGetPowerManagementLimit(self, h): raise RuntimeError("not supported")
    def nvmlDeviceGetClockInfo(self, h, kind): return 2200
    def nvmlDeviceGetMaxClockInfo(self, h, kind): return 3003
    def nvmlDeviceGetCurrentClocksEventReasons(self, h): return 0
    def nvmlDeviceGetIndex(self, h): return 0
    def nvmlDeviceGetName(self, h): return "NVIDIA GB10"
    def nvmlDeviceGetUUID(self, h): return "GPU-a"
    def nvmlDeviceGetMemoryInfo(self, h): raise RuntimeError("not supported")
    def nvmlDeviceGetComputeRunningProcesses(self, h): return [SimpleNamespace(pid=4242, usedGpuMemory=None)]
    def nvmlSystemGetProcessName(self, pid): return "python3"


MEMINFO = "MemTotal:       125556892 kB\nMemFree:  1 kB\nMemAvailable:   9876543 kB\n"


def spark(tmp_path, **kw):
    return api.Spark(root=str(tmp_path), nvml=FakeNvml(), **kw)


def test_gpu_line_in_her_sections(tmp_path):
    tree(tmp_path, {"/proc/meminfo": MEMINFO, "/proc/sys/kernel/random/boot_id": "b00t-1d\n"})
    assert api.answer_gpu2(spark(tmp_path)) == (
        "\n__NVIDIA__\n61, 93, 41.12, [N/A], 2200, 3003, Not Active, Not Active, Not Active, Not Active, 0, NVIDIA GB10, GPU-a\n"
        "\n__NVIDIA_VRAM__\n[N/A], [N/A]\n"
        "\n__NVIDIA_APPS__\n4242, python3, [N/A], GPU-a\n"
        "\n__INTEL__\n"
        "\n__MEMINFO__\nMemTotal:       125556892 kB\nMemAvailable:   9876543 kB\n"
        "\n__CLOCK_CAP__\nb00t-1d\n")


def test_gpu_line_reads_her_clock_cap_marker_when_one_exists(tmp_path):
    tree(tmp_path, {"/proc/meminfo": MEMINFO, "/proc/sys/kernel/random/boot_id": "b00t-1d\n",
                    "/run/sparkdash/gpu-clock-cap": "2200 b00t-1d\nsecond line\n"})
    assert api.answer_gpu2(spark(tmp_path)).endswith("\n__CLOCK_CAP__\n2200 b00t-1d\nb00t-1d\n")


def test_df_line_adds_the_block_devices_io(tmp_path):
    tree(tmp_path, {"/proc/1/mounts": "/dev/nvme0n1p2 / ext4 rw 0 0\n",
                    "/sys/block/loop0/stat": "  1 0 2 0\n", "/sys/block/nvme0n1/stat": "  10 20 30 40\n",
                    "/sys/block/sda/nothing": ""})
    st = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=4096)
    s = spark(tmp_path, statvfs=lambda path: st)
    assert api.answer_df2(s) == api.answer_df(s) + (
        "---sparkdash-block-io---\nloop0   1 0 2 0\nnvme0n1   10 20 30 40\nsda \n")


def test_roce_line(tmp_path):
    ib, net = "/sys/class/infiniband/mlx5_0", "/sys/class/net/enp1s0f0np0"
    tree(tmp_path, {ib + "/ports/1/state": "4: ACTIVE\n", ib + "/ports/1/phys_state": "5: LinkUp\n",
                    ib + "/ports/1/rate": "200 Gb/sec (2X NDR)\n", ib + "/ports/1/link_layer": "Ethernet\n",
                    ib + "/ports/1/hw_counters/out_of_buffer": "0\n", ib + "/ports/1/hw_counters/rx_icrc_encapsulated": "3\n",
                    ib + "/device/net/enp1s0f0np0/x": "",
                    net + "/operstate": "up\n", net + "/mtu": "9000\n", net + "/speed": "200000\n",
                    net + "/statistics/rx_bytes": "11\n", net + "/statistics/tx_bytes": "22\n",
                    net + "/statistics/rx_errors": "0\n", net + "/statistics/tx_errors": "0\n",
                    net + "/statistics/rx_dropped": "1\n", net + "/statistics/tx_dropped": "2\n",
                    "/sys/class/infiniband/mlx5_1/ports/1/state": "1: DOWN\n"})
    assert api.answer_roce(spark(tmp_path)) == (
        "DEV|mlx5_0|4: ACTIVE|5: LinkUp|200 Gb/sec (2X NDR)|Ethernet|enp1s0f0np0\n"
        "CNT|mlx5_0|out_of_buffer|0\nCNT|mlx5_0|rx_icrc_encapsulated|3\n"
        "NET|enp1s0f0np0|up|9000|200000|11|22|0|0|1|2\n"
        "DEV|mlx5_1|1: DOWN||||\n")


QOS = "DCBX mode: OS controlled\nPriority trust state: pcp\n"


def test_nic_line_counters_pause_and_qos(tmp_path):
    stats = [("rx_packets", 9), ("rx_discards_phy", 4), ("tx_prio3_pause", 7), ("rx_prio9_pause", 1), ("rx_out_of_buffer", 0)]
    s = spark(tmp_path, ethtool=lambda n: (stats, (0, 1, 1)) if n == "enp1s0f0np0" else (None, None),
              qos=lambda n: QOS if n == "enp1s0f0np0" else "")
    out = api.answer_nic(s)
    assert out.startswith(
        "ETH|enp1s0f0np0\n     rx_discards_phy: 4\n     tx_prio3_pause: 7\n     rx_out_of_buffer: 0\n"
        "FC|enp1s0f0np0|Pause parameters for enp1s0f0np0: Autonegotiate:\toff RX:\t\ton TX:\t\ton  \n"
        "QOS|enp1s0f0np0\nDCBX mode: OS controlled\nPriority trust state: pcp\nENDQOS\n"
        "ETH|enp1s0f1np1\nFC|enp1s0f1np1|\nQOS|enp1s0f1np1\nENDQOS\n")
    assert out.count("ENDQOS\n") == 4


class FakeApps:
    def __init__(self, dscp): self.dscp = dscp
    def countAppSelector(self, sel): return len(self.dscp) if sel == 5 else 0
    def printAppSelector(self, sel): print("\tprio:3 dscp:24,26,")
    def printDefaultPriority(self): pass


class FakeDcb:
    def __init__(self, dscp=()): self.apps = FakeApps(dscp)
    def get_dcbx(self): return 0x1 | 0x4
    def get_ieee_app_table(self): return self.apps
    def get_ieee_dcb_buffer(self): return [0, 1, 1, 1, 1, 1, 1, 1], [0, 523296, 0, 0, 0, 0, 0, 0], 2039616
    def get_ieee_pfc_delay(self): return 7
    def get_ieee_pfc_en(self): return 0b00001000


def test_qos_text_as_mlnx_qos_prints_it():
    assert api.qos_text(FakeDcb()) == (
        "DCBX mode: OS controlled\nPriority trust state: pcp\ndefault priority:\n"
        "Receive buffer size (bytes): 0,523296,0,0,0,0,0,0,max_buffer_size=2039616\nCable len: 7\nPFC configuration:\n"
        "\tpriority    0   1   2   3   4   5   6   7\n"
        "\tenabled     0   0   0   1   0   0   0   0   \n\tbuffer      0   1   1   1   1   1   1   1   \n")
    dscp = api.qos_text(FakeDcb(dscp=(24, 26)))
    assert "Priority trust state: dscp\ndscp2prio mapping:\n\tprio:3 dscp:24,26,\ndefault priority:\n" in dscp


def test_models_line_is_her_empty_list():
    assert api.answer_models(None) == "[]\n"


def test_known_commands_hold_her_new_lines_beside_the_old():
    doc = json.loads(KNOWN.read_text())
    for name in ("gpu", "df", "gpu2", "df2", "roce", "nic", "models"):
        assert name in doc["commands"] and name in api.ANSWERS, name
    assert "__NVIDIA__" in doc["commands"]["gpu2"] and "sparkdash-block-io" in doc["commands"]["df2"]
    assert "infiniband" in doc["commands"]["roce"] and "mlnx_qos" in doc["commands"]["nic"]
    assert "docker" in doc["commands"]["models"]
