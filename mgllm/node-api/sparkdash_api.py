#!/usr/bin/env python3
"""sparkdash-api: sparkDash's monitoring questions, answered on a DGX Spark without running a command.

sparkDash (https://github.com/MiaAI-Lab/sparkDash) reads a remote Spark by running shell command lines over SSH
(server/collectors/ssh.js, sshExec). mgllm's patch to it sends each line here instead, as
POST /sparkdashapi/exec {"cmd": "<the line>"}. The lines it sends are known (known-commands.json, captured from the
pinned sparkDash by scripts/capture-commands.sh). Each is answered from the sources the line itself reads, in the text
it prints, so sparkDash's own parsers stay unchanged:
  - /proc and /sys files, read directly (no cat, grep or shell);
  - NVML through NVIDIA's own Python binding (nvidia-ml-py, vendor/pynvml.py) where the line runs nvidia-smi, which is
    NVML's front end; NVML reads the driver and allocates no GPU memory;
  - psutil for IPv4 addresses (ip -4 addr), os.statvfs for disk usage (df), the systemd journal binding for the
    kernel log (journalctl -k).
Two lines of mgllm's own drive sparkDash's GPU clock control: "mgllm:gpu-clock" answers the limit in force, and
"mgllm:gpu-clock set <limit>" (unlocked, 2400, 2200, 2000 or 1800 MHz, both Sparks alike) writes a request that the
root-only sparkdash-clock applies through NVML; this service never holds a privilege itself.
Any other line is refused with 403: this service runs nothing a request asks for. Every request needs the bearer
token; who may connect at all is the systemd unit's IPAddressAllow (the gateway over the tunnel, and localhost).

Environment: SPARKDASH_API_TOKEN (required), SPARKDASH_API_BIND (0.0.0.0), SPARKDASH_API_PORT (9701),
SPARKDASH_API_COMMANDS (the known-commands.json beside this file).
"""

from __future__ import annotations

import glob
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
NA = "[N/A]"                                   # what nvidia-smi prints for a value the driver does not give
MAX_BODY = 64 * 1024
DF_SKIP = {"tmpfs", "devtmpfs", "squashfs", "overlay", "efivarfs", "proc", "sysfs", "devpts", "cgroup", "cgroup2"}
CPU_SENSORS = {"coretemp", "k10temp", "zenpower", "acpitz", "soc_thermal"}
# nvmlClocksEventReasons bits, in the order the gpu line asks for them
REASONS = (0x40, 0x20, 0x8, 0x4)               # hw_thermal_slowdown, sw_thermal_slowdown, hw_slowdown, sw_power_cap
# the GPU clock control: its limits (sparkdash_clock.py checks them again), the request this service writes and the
# state sparkdash-clock (root) writes back; both live in /run, so a reboot clears them as it clears the lock
CLOCK_PRESETS = ("unlocked", "2400", "2200", "2000", "1800")
CLOCK_REQUEST = "/run/sparkdash-api/clock-request"
CLOCK_STATE = "/run/sparkdash-clock/state"
_clock_lock = threading.Lock()                 # one clock change at a time: a second waits for the first's answer


def _read(path: str) -> str | None:
    """A file's text, or None where cat would fail (absent, unreadable, or an attribute the driver refuses)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _sub(path: str) -> str:
    """As $(cat path) gives it: the text without its trailing newlines, "" where cat fails."""
    return (_read(path) or "").rstrip("\n")


def _lines(text: str | None) -> list[str]:
    return [] if not text else text.splitlines()


class Spark:
    """The sources one Spark's answers come from. Tests pass their own; the service uses the host's."""

    def __init__(self, root: str = "/", nvml: Any = None, ipv4: Callable[[], list[tuple[int, str, str, int]]] | None = None,
                 statvfs: Callable[[str], Any] = os.statvfs,
                 kernel_log: Callable[[], Iterable[tuple[int, str, str, str]]] | None = None,
                 ethtool: Callable[[str], tuple[list[tuple[str, int]] | None, tuple[int, int, int] | None]] | None = None,
                 qos: Callable[[str], str] | None = None):
        self.root = root.rstrip("/")
        self._nvml, self._nvml_ready = nvml, False
        self.ipv4 = ipv4 or _host_ipv4
        self.statvfs = statvfs
        self.kernel_log = kernel_log or _host_kernel_log
        self.ethtool = ethtool or _host_ethtool                  # sparkDash 2.50: the ports' counters and pause
        self.qos = qos or _host_qos                              # sparkDash 2.50: mlnx_qos's view of a port
        self.clock_wait = 15.0                                   # seconds sparkdash-clock has to apply a limit

    def p(self, path: str) -> str:
        return self.root + path

    def glob(self, pattern: str) -> list[str]:
        """The pattern's matches in name order, as bash expands it for these ASCII names, without the root."""
        n = len(self.root)
        return [m[n:] for m in sorted(glob.glob(self.root + pattern))]

    # ---- NVML (nvidia-smi's own source) --------------------------------------------------------------------------
    def nvml(self):
        if self._nvml is None:
            sys.path.insert(0, str(HERE / "vendor"))
            import pynvml                                         # NVIDIA's binding, vendored and pinned
            self._nvml = pynvml
        if not self._nvml_ready:
            self._nvml.nvmlInit()
            self._nvml_ready = True
        return self._nvml

    def gpus(self) -> list[Any]:
        """Every GPU's handle, or [] where NVML has none (as nvidia-smi then prints nothing)."""
        try:
            n = self.nvml()
            return [n.nvmlDeviceGetHandleByIndex(i) for i in range(n.nvmlDeviceGetCount())]
        except Exception:
            self._nvml_ready = False                              # the next question initializes again
            return []


def _value(get: Callable[[], Any], fmt: Callable[[Any], str] = str) -> str:
    try:
        return fmt(get())
    except Exception:
        return NA


def _host_ipv4() -> list[tuple[int, str, str, int]]:
    """(interface index, name, address, prefix length) for every IPv4 address, primary first within an interface."""
    import psutil
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AF_INET:
                prefix = bin(int.from_bytes(socket.inet_aton(a.netmask or "0.0.0.0"), "big")).count("1")
                try:
                    index = socket.if_nametoindex(name)
                except OSError:
                    index = 0
                out.append((index, name, a.address, prefix))
    return out


def _host_kernel_log() -> Iterable[tuple[int, str, str, str]]:
    """This boot's kernel messages, oldest first: (microseconds since the epoch, host, identifier, message)."""
    from systemd import journal
    r = journal.Reader()
    r.this_boot()
    r.add_match(_TRANSPORT="kernel")
    for e in r:
        ts = e.get("__REALTIME_TIMESTAMP")                      # a datetime, from the binding
        usec = int(ts.timestamp() * 1e6) if ts is not None else 0
        yield usec, e.get("_HOSTNAME", ""), e.get("SYSLOG_IDENTIFIER", "kernel"), str(e.get("MESSAGE", ""))


# sparkDash 2.50 reads the ConnectX-7 ports with `ethtool -S` / `ethtool -a` and `mlnx_qos -i`. Both are answered as the
# tools get them, without running them: ethtool's own ioctls (SIOCETHTOOL: driver info, stat names, stat values, pause;
# all allowed without a privilege) and Mellanox's own DCB netlink library (dcbnetlink, the module mlnx_qos imports).
SIOCETHTOOL = 0x8946
ETHTOOL_GDRVINFO, ETHTOOL_GSTRINGS, ETHTOOL_GSTATS, ETHTOOL_GPAUSEPARAM = 0x03, 0x1B, 0x1D, 0x12
ETH_SS_STATS, ETH_GSTRING_LEN = 1, 32
_qos_lock = threading.Lock()                   # qos_text captures what dcbnetlink prints: one capture at a time


def _ethtool_ioctl(name: str, data: bytearray) -> None:
    """One SIOCETHTOOL call on interface ``name``; the kernel reads and fills ``data`` (struct ifreq's ifr_data)."""
    import ctypes
    import fcntl
    import struct
    buf = (ctypes.c_char * len(data)).from_buffer(data)
    ifreq = struct.pack("16sP", name.encode()[:15], ctypes.addressof(buf)).ljust(40, b"\0")    # struct ifreq: 40 bytes
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
        fcntl.ioctl(sk.fileno(), SIOCETHTOOL, ifreq)


def _host_ethtool(name: str) -> tuple[list[tuple[str, int]] | None, tuple[int, int, int] | None]:
    """``ethtool -S name`` as (stat name, value) pairs and ``ethtool -a name`` as (autoneg, rx, tx); None where it fails."""
    import struct
    try:
        drv = bytearray(struct.pack("I", ETHTOOL_GDRVINFO).ljust(196, b"\0"))             # struct ethtool_drvinfo
        _ethtool_ioctl(name, drv)
        n = struct.unpack_from("I", drv, 180)[0]                                           # its n_stats
        names = bytearray(struct.pack("III", ETHTOOL_GSTRINGS, ETH_SS_STATS, n) + bytes(n * ETH_GSTRING_LEN))
        _ethtool_ioctl(name, names)
        values = bytearray(struct.pack("II", ETHTOOL_GSTATS, n) + bytes(n * 8))
        _ethtool_ioctl(name, values)
        stats = [(names[12 + i * ETH_GSTRING_LEN:12 + (i + 1) * ETH_GSTRING_LEN].split(b"\0", 1)[0].decode(errors="replace"),
                  v) for i, v in enumerate(struct.unpack_from(f"{n}Q", values, 8))]
    except OSError:
        stats = None
    try:
        pause = bytearray(struct.pack("IIII", ETHTOOL_GPAUSEPARAM, 0, 0, 0))
        _ethtool_ioctl(name, pause)
        pp: tuple[int, int, int] | None = struct.unpack_from("III", pause, 4)
    except OSError:
        pp = None
    return stats, pp


def qos_text(ctrl: Any) -> str:
    """What ``mlnx_qos -i <port>`` prints, as far as sparkDash reads it (DCBX mode, trust, the DSCP map, default priority,
    receive buffers, cable length, PFC), from a dcbnetlink DcbController as mlnx_qos gets them where the port has no qos
    sysfs files (the Sparks' kernel has none). Its traffic-class lines are left out: sparkDash does not read them."""
    import contextlib
    import io
    out = io.StringIO()
    with _qos_lock, contextlib.redirect_stdout(out):
        print("DCBX mode: OS controlled" if ctrl.get_dcbx() & 0x1 else "DCBX mode: Firmware controlled")
        apps = ctrl.get_ieee_app_table()
        trust = "pcp" if apps.countAppSelector(5) == 0 else "dscp"                  # 5: IEEE_8021QAZ_APP_SEL_DSCP
        print("Priority trust state: " + trust)
        if trust == "dscp":
            print("dscp2prio mapping:")
            apps.printAppSelector(5)
        print("default priority:")
        apps.printDefaultPriority()
        try:
            prio2buffer, size, total = ctrl.get_ieee_dcb_buffer()
        except Exception:                                                           # mlnx_qos's buffer_error
            prio2buffer = None
        if prio2buffer is not None:
            print("Receive buffer size (bytes): " + "".join("%1d," % size[b] for b in range(8))
                  + ("max_buffer_size=%d" % total if total is not None else ""))
        print("Cable len: %d" % ctrl.get_ieee_pfc_delay())
        pfc = ctrl.get_ieee_pfc_en()
        print("PFC configuration:")
        print("\tpriority    0   1   2   3   4   5   6   7")
        msg = "\tenabled     " + "".join("%1d   " % ((pfc >> up) & 1) for up in range(8))
        if prio2buffer is not None:
            msg += "\n\tbuffer      " + "".join("%1d   " % prio2buffer[up] for up in range(8))
        print(msg)
    return out.getvalue()


def _host_qos(name: str) -> str:
    """``mlnx_qos -i name`` (stdout, as far as sparkDash reads it), or "" where it fails as the tool would."""
    try:
        if "/usr/lib/python3/dist-packages" not in sys.path:
            sys.path.append("/usr/lib/python3/dist-packages")                     # mlnx-tools' dcbnetlink
        from dcbnetlink import DcbController
        return qos_text(DcbController(name))
    except Exception:
        return ""


# ---- the answers: each returns what its line prints -----------------------------------------------------------------
def gpu_lines(s: Spark) -> str:
    """nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,power.draw,power.limit,clocks.current.sm,clocks.max.sm,
    four clocks_throttle_reasons,index,name,uuid --format=csv,noheader,nounits: one line a GPU."""
    handles = s.gpus()
    if not handles:
        return ""
    n, out = s.nvml(), []
    for h in handles:
        try:
            reasons: int | None = int(n.nvmlDeviceGetCurrentClocksEventReasons(h))
        except Exception:
            reasons = None
        fields = [
            _value(lambda: n.nvmlDeviceGetTemperature(h, n.NVML_TEMPERATURE_GPU)),
            _value(lambda: n.nvmlDeviceGetUtilizationRates(h).gpu),
            _value(lambda: n.nvmlDeviceGetPowerUsage(h), lambda mw: f"{mw / 1000:.2f}"),
            _value(lambda: n.nvmlDeviceGetPowerManagementLimit(h), lambda mw: f"{mw / 1000:.2f}"),
            _value(lambda: n.nvmlDeviceGetClockInfo(h, n.NVML_CLOCK_SM)),
            _value(lambda: n.nvmlDeviceGetMaxClockInfo(h, n.NVML_CLOCK_SM)),
            *[NA if reasons is None else ("Active" if reasons & bit else "Not Active") for bit in REASONS],
            _value(lambda: n.nvmlDeviceGetIndex(h)),
            _value(lambda: n.nvmlDeviceGetName(h)),
            _value(lambda: n.nvmlDeviceGetUUID(h)),
        ]
        out.append(", ".join(fields) + "\n")
    return "".join(out)


def gpu_memory_lines(s: Spark) -> str:
    """nvidia-smi --query-gpu=memory.used,memory.total ... (MiB; [N/A] on GB10, whose memory is the system's)."""
    handles = s.gpus()
    if not handles:
        return ""
    n, out = s.nvml(), []
    for h in handles:
        try:
            m = n.nvmlDeviceGetMemoryInfo(h)
            out.append(f"{m.used // 2**20}, {m.total // 2**20}\n")
        except Exception:
            out.append(f"{NA}, {NA}\n")
    return "".join(out)


def compute_app_lines(s: Spark) -> str:
    """nvidia-smi --query-compute-apps=pid,process_name,used_gpu_memory,gpu_uuid ...: one line a process a GPU."""
    handles = s.gpus()
    if not handles:
        return ""
    n, out = s.nvml(), []
    for h in handles:
        uuid = _value(lambda: n.nvmlDeviceGetUUID(h))
        try:
            procs = n.nvmlDeviceGetComputeRunningProcesses(h)
        except Exception:
            continue
        for pr in procs:
            name = _value(lambda: n.nvmlSystemGetProcessName(pr.pid))
            used = NA if pr.usedGpuMemory is None else str(pr.usedGpuMemory // 2**20)
            out.append(f"{pr.pid}, {name}, {used}, {uuid}\n")
    return "".join(out)


def meminfo_lines(s: Spark) -> str:
    """grep -E 'MemTotal|MemAvailable' /proc/meminfo"""
    return "".join(l + "\n" for l in _lines(_read(s.p("/proc/meminfo"))) if re.search(r"MemTotal|MemAvailable", l))


def answer_gpu(s: Spark) -> str:
    return (gpu_lines(s) + "---\n" + gpu_memory_lines(s) + "---\n" + compute_app_lines(s) + "---\n"
            + meminfo_lines(s))


def answer_cpu(s: Spark) -> str:
    stat = _lines(_read(s.p("/proc/stat")))
    out = (stat[0] + "\n" if stat else "") + "---\n"
    arch = [l for l in _lines(_read(s.p("/proc/cpuinfo"))) if re.search(r"CPU architecture|aarch64", l)]
    out += (arch[0] + "\n" if arch else "") + "---\n"
    for h in s.glob("/sys/class/hwmon/*"):
        name = _sub(s.p(h + "/name"))
        if name in CPU_SENSORS:
            temps = s.glob(h + "/temp*_input")
            value = _sub(s.p(temps[0])) if temps else ""        # the loop breaks after the first input
            if value:
                out += f"{name} {value}\n"
    for z in s.glob("/sys/class/thermal/thermal_zone*"):
        value = _sub(s.p(z + "/temp"))
        if value:
            out += f"{_sub(s.p(z + '/type'))} {value}\n"
    return out


def ip4_text(s: Spark) -> str:
    """ip -4 addr show, as far as sparkDash reads it: a block an interface ("N: name:"), its "inet a.b.c.d/n" lines."""
    blocks: dict[tuple[int, str], list[str]] = {}
    for index, name, addr, prefix in s.ipv4():
        blocks.setdefault((index, name), []).append(f"    inet {addr}/{prefix} scope global {name}\n")
    return "".join(f"{i}: {name}: <> mtu 0\n" + "".join(lines) for (i, name), lines in sorted(blocks.items()))


def answer_net(s: Spark) -> str:
    out = (_read(s.p("/proc/net/dev")) or "") + "---\n" + (_read(s.p("/proc/net/route")) or "") + "---\n"
    out += ip4_text(s) + "---\n"
    for d in s.glob("/sys/class/net/*/operstate"):
        out += f"{Path(d).parent.name}:{_sub(s.p(d))}\n"
    out += "---\n" + (_read(s.p("/sys/class/net/enP7s7/address")) or "") + "---\n"
    for d in s.glob("/sys/class/net/*/speed"):
        out += f"{Path(d).parent.name}:{_sub(s.p(d))}\n"
    return out


def answer_meminfo_apps(s: Spark) -> str:
    return meminfo_lines(s) + "---\n" + compute_app_lines(s)


def answer_df(s: Spark) -> str:
    """df -l -B1 -T -x <the line's pseudo types>: one local filesystem a device, the shortest mount point for it."""
    seen: dict[str, tuple[str, str, str]] = {}
    for line in _lines(_read(s.p("/proc/1/mounts")) or _read(s.p("/proc/self/mounts"))):
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mount, fstype = parts[0], parts[1].replace("\\040", " "), parts[2]
        if fstype in DF_SKIP or not dev.startswith("/dev/"):
            continue
        if dev not in seen or len(mount) < len(seen[dev][0]):
            seen[dev] = (mount, fstype, dev)
    rows = [("Filesystem", "Type", "1B-blocks", "Used", "Available", "Use%", "Mounted on")]
    for mount, fstype, dev in sorted(seen.values(), key=lambda v: v[0]):
        try:
            st = s.statvfs(mount)
        except OSError:
            continue
        size, free, avail = st.f_blocks * st.f_frsize, st.f_bfree * st.f_frsize, st.f_bavail * st.f_frsize
        used = size - free
        pct = -(-100 * used // (used + avail)) if used + avail else 0           # df rounds up
        rows.append((dev, fstype, str(size), str(used), str(avail), f"{pct}%", mount))
    return "".join(" ".join(r) + "\n" for r in rows)


def answer_df2(s: Spark) -> str:
    """sparkDash 2.50's df line: the df table, then ---sparkdash-block-io--- and a line a block device: its name and
    its /sys/block/<dev>/stat as cat gives it."""
    io_lines = "".join(f"{d.rsplit('/', 1)[1]} {_sub(s.p(d + '/stat'))}\n" for d in s.glob("/sys/block/*"))
    return answer_df(s) + "---sparkdash-block-io---\n" + io_lines


def answer_gpu2(s: Spark) -> str:
    """sparkDash 2.50's GPU line, in its sections: NVIDIA (the gpu, memory and compute-app queries), AMD (none here: no
    amd-smi), Intel (none: no card on the xe driver), /proc/meminfo, then her clock-cap marker's first line (absent where
    the cap is mgllm's own: its state is not her marker) and the boot id."""
    out = ("\n__NVIDIA__\n" + gpu_lines(s) + "\n__NVIDIA_VRAM__\n" + gpu_memory_lines(s)
           + "\n__NVIDIA_APPS__\n" + compute_app_lines(s) + "\n__INTEL__\n")
    out += "\n__MEMINFO__\n" + meminfo_lines(s) + "\n__CLOCK_CAP__\n"
    first = _lines(_read(s.p("/run/sparkdash/gpu-clock-cap")))      # her root marker (her per-user one: no such user here)
    if first:
        out += first[0] + "\n"
    boot = _sub(s.p("/proc/sys/kernel/random/boot_id"))
    return out + (boot + "\n" if boot else "")


def answer_roce(s: Spark) -> str:
    """sparkDash 2.50's RoCE line: a DEV line a device (port 1's state, physical state, rate, link layer, its netdev),
    a CNT line a hardware counter, and the netdev's NET line (state, MTU, speed, bytes, errors, drops)."""
    out = ""
    for d in s.glob("/sys/class/infiniband/*"):
        if not os.path.isdir(s.p(d)):
            continue
        n, p = d.rsplit("/", 1)[1], d + "/ports/1"
        nets = s.glob(d + "/device/net/*")
        nd = nets[0].rsplit("/", 1)[1] if nets else ""
        out += "|".join(["DEV", n, *(_sub(s.p(f"{p}/{k}")) for k in ("state", "phys_state", "rate", "link_layer")), nd]) + "\n"
        for c in s.glob(p + "/hw_counters/*"):
            for line in _lines(_read(s.p(c))):
                out += f"CNT|{n}|{c.rsplit('/', 1)[1]}|{line}\n"
        if nd:
            sn = "/sys/class/net/" + nd
            keys = ("operstate", "mtu", "speed", *(f"statistics/{k}" for k in
                    ("rx_bytes", "tx_bytes", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped")))
            out += "|".join(["NET", nd, *(_sub(s.p(f"{sn}/{k}")) for k in keys)]) + "\n"
    return out


# the ConnectX-7 ports sparkDash 2.50's port line names, and the counters its grep keeps
NIC_PORTS = ("enp1s0f0np0", "enp1s0f1np1", "enP2p1s0f0np0", "enP2p1s0f1np1")
NIC_SHOWN = re.compile(r"^ +(rx_discards_phy|tx_discards_phy|rx_crc_errors_phy|rx_symbol_err_phy|rx_pause_ctrl_phy|"
                       r"tx_pause_ctrl_phy|rx_prio[0-7]_pause|tx_prio[0-7]_pause|rx_prio[0-7]_discards|rx_out_of_buffer):")


def answer_nic(s: Spark) -> str:
    """sparkDash 2.50's port line, a block a port: ETH (ethtool -S's lines its grep keeps), FC (ethtool -a on one line,
    as tr leaves it), and QOS ... ENDQOS (mlnx_qos -i, its first 80 lines)."""
    onoff = lambda v: "on" if v else "off"                                            # noqa: E731
    out = ""
    for n in NIC_PORTS:
        stats, pause = s.ethtool(n)
        out += f"ETH|{n}\n" + "".join(f"     {k}: {v}\n" for k, v in stats or () if NIC_SHOWN.match(f"     {k}: {v}"))
        fc = "" if pause is None else (f"Pause parameters for {n}:\nAutonegotiate:\t{onoff(pause[0])}\n"
                                       f"RX:\t\t{onoff(pause[1])}\nTX:\t\t{onoff(pause[2])}\n\n").replace("\n", " ")
        out += f"FC|{n}|{fc}\n" + f"QOS|{n}\n" + "".join(l + "\n" for l in _lines(s.qos(n))[:80]) + "ENDQOS\n"
    return out


def answer_models(s: Spark | None) -> str:
    """sparkDash 2.50's models line lists the models of running containers whose image names vLLM. None runs on these
    Sparks (the engine is TensorFold, which sparkDash reads over HTTP), so it prints what her probe prints here: [].
    This service has no docker access (docker's group is root's equal), so a vLLM container started later is not seen."""
    return "[]\n"


def _short(usec: int, host: str, ident: str, msg: str) -> str:
    """A journal entry as journalctl's short output prints it."""
    return f"{time.strftime('%b %d %H:%M:%S', time.localtime(usec / 1e6))} {host} {ident}: {msg}"


def answer_nverr(s: Spark) -> str:
    """journalctl -k --grep=NV_ERR_NO_MEMORY | grep -c NV_ERR_NO_MEMORY"""
    return f"{sum(1 for e in s.kernel_log() if 'NV_ERR_NO_MEMORY' in e[3])}\n"


def answer_kernel_errors(s: Spark) -> str:
    """xid=<NVRM: Xid lines> oom=<OOM kill lines>, then the last Xid line (journalctl -k, this boot)."""
    xid, oom = [], 0
    for e in s.kernel_log():
        if "NVRM: Xid" in e[3]:
            xid.append(_short(*e))
        if re.search(r"Out of memory: Killed process|oom-kill:", e[3]):
            oom += 1
    return f"xid={len(xid)} oom={oom}\n{xid[-1] if xid else ''}\n"


def _clock_view(state: dict) -> str:
    return json.dumps({k: state.get(k) for k in ("limit", "ok", "error", "at")}) + "\n"


def answer_clock(s: Spark) -> str:
    """mgllm:gpu-clock: the limit sparkdash-clock last applied here, as JSON; "unlocked" before any (the driver's own)."""
    text = _read(s.p(CLOCK_STATE))
    if not text:
        return _clock_view({"limit": "unlocked", "ok": True, "error": None, "at": None})
    try:
        return _clock_view(json.loads(text))
    except ValueError:
        raise RuntimeError("sparkdash-clock's state is unreadable")


def clock_setter(limit: str) -> Callable[[Spark], str]:
    """mgllm:gpu-clock set <limit>: ask sparkdash-clock (root) for it and answer what it applied, or fail as it did."""
    def answer(s: Spark) -> str:
        with _clock_lock:
            rid = secrets.token_hex(8)
            with open(s.p(CLOCK_REQUEST), "w") as f:              # closing it starts sparkdash-clock (its .path unit)
                f.write(json.dumps({"limit": limit, "id": rid}))
            deadline = time.monotonic() + s.clock_wait
            while time.monotonic() < deadline:
                text = _read(s.p(CLOCK_STATE))
                try:
                    state = json.loads(text) if text else {}
                except ValueError:
                    state = {}
                if state.get("id") == rid:
                    if not state.get("ok"):
                        raise RuntimeError(f"sparkdash-clock: {state.get('error') or 'refused'}")
                    return _clock_view(state)
                time.sleep(0.1)
            raise RuntimeError("sparkdash-clock did not answer (is sparkdash-clock.path enabled?)")
    return answer


ANSWERS: dict[str, Callable[[Spark], str]] = {
    "gpu": answer_gpu, "cpu": answer_cpu, "net": answer_net, "meminfo": meminfo_lines,
    "meminfo_apps": answer_meminfo_apps, "uptime": lambda s: _read(s.p("/proc/uptime")) or "", "df": answer_df,
    "nverr": answer_nverr, "kernel_errors": answer_kernel_errors, "ping": lambda s: "ok\n",
    "clock": answer_clock, **{f"clock_set_{p}": clock_setter(p) for p in CLOCK_PRESETS},
    # sparkDash 2.50 (3e6d4ef): its new and changed lines, beside the old ones (the deployed release still sends those)
    "gpu2": answer_gpu2, "df2": answer_df2, "roce": answer_roce, "nic": answer_nic, "models": answer_models,
}
# mgllm's own lines (not sparkDash's): the GPU clock control
MGLLM_LINES = {"mgllm:gpu-clock": "clock", **{f"mgllm:gpu-clock set {p}": f"clock_set_{p}" for p in CLOCK_PRESETS}}


def load_commands(path: Path) -> dict[str, str]:
    """The known lines (exact text -> answer name); every name must have an answer, or the service does not start."""
    doc = json.loads(path.read_text())
    missing = set(doc["commands"]) - set(ANSWERS)
    if missing:
        raise SystemExit(f"sparkdash-api: no answer for {sorted(missing)} in {path}")
    return {**{line: name for name, line in doc["commands"].items()}, **MGLLM_LINES}


# ---- HTTP ------------------------------------------------------------------------------------------------------------
class Api(BaseHTTPRequestHandler):
    server_version = "sparkdash-api"
    spark: Spark
    commands: dict[str, str]
    token: str

    def log_message(self, format: str, *args: Any) -> None:         # journald keeps the refusals only
        pass

    def _send(self, code: int, body: str, kind: str = "text/plain; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _refuse(self, code: int, why: str) -> None:
        print(f"sparkdash-api: {code} {why} ({self.client_address[0]} {self.path})", flush=True)
        self._send(code, json.dumps({"error": why}), "application/json")

    def _authorized(self) -> bool:
        given = self.headers.get("Authorization", "")
        return hmac.compare_digest(given.encode(), f"Bearer {self.token}".encode())

    def do_GET(self) -> None:
        if self.path != "/sparkdashapi/health":
            return self._refuse(404, "not found")
        self._send(200, json.dumps({"ok": True, "commands": len(self.commands)}), "application/json")

    def do_POST(self) -> None:
        if not self._authorized():
            return self._refuse(401, "bad token")
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._refuse(400, "body size")
        body = self.rfile.read(length)
        if self.path != "/sparkdashapi/exec":
            return self._refuse(404, "not found")
        try:
            cmd = json.loads(body)["cmd"]
        except (ValueError, KeyError, TypeError):
            return self._refuse(400, "expected {\"cmd\": ...}")
        name = self.commands.get(cmd) if isinstance(cmd, str) else None
        if name is None:
            return self._refuse(403, "not a known sparkDash line")
        try:
            self._send(200, ANSWERS[name](self.spark))
        except Exception as exc:                                         # a source failed: the line's command would
            self._refuse(500, f"{name}: {exc!r}")                        # have failed too


def main() -> None:
    token = os.environ.get("SPARKDASH_API_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("sparkdash-api: SPARKDASH_API_TOKEN (32+ characters) is required")
    Api.token = token
    Api.commands = load_commands(Path(os.environ.get("SPARKDASH_API_COMMANDS", HERE / "known-commands.json")))
    Api.spark = Spark()
    bind, port = os.environ.get("SPARKDASH_API_BIND", "0.0.0.0"), int(os.environ.get("SPARKDASH_API_PORT", "9701"))
    print(f"sparkdash-api: {len(Api.commands)} known lines, on {bind}:{port}", flush=True)
    ThreadingHTTPServer((bind, port), Api).serve_forever()


if __name__ == "__main__":
    main()
