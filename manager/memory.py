"""Read-only macOS memory and process measurements."""

from __future__ import annotations

import re
import socket
import subprocess
import os
from pathlib import Path


SIZE_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def command(args: list[str], timeout: float = 5) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          check=False)


def parse_size(value: str) -> int | None:
    match = re.fullmatch(r"([\d.]+)([BKMGT])(?:i?B)?", value.strip())
    if not match:
        return None
    return int(float(match.group(1)) * SIZE_UNITS[match.group(2)])


def vmmap_summary(pid: int | None) -> dict:
    if not pid:
        return {"footprint_bytes": None}
    try:
        result = command(["vmmap", "-summary", str(pid)], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return {"footprint_bytes": None}
    if result.returncode:
        return {"footprint_bytes": None}
    footprint = None
    for line in result.stdout.splitlines():
        if "Physical footprint:" in line:
            footprint = parse_size(line.split("Physical footprint:", 1)[1].strip())
    return {"footprint_bytes": footprint}


def listener_pid(port: int) -> int | None:
    try:
        result = command(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN",
                          "-Fp"], timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            return int(line[1:])
    return None


def launch_agent_pid(label: str) -> int | None:
    try:
        result = command(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    match = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else None


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def established_connections(port: int) -> int:
    try:
        result = command(["lsof", "-nP", f"-iTCP:{port}",
                          "-sTCP:ESTABLISHED", "-Fp"], timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in result.stdout.splitlines()
               if line.startswith("f") and len(line) > 1 and line[1].isdigit())


def system_memory() -> dict:
    values = {"physical_bytes": None, "free_bytes": None,
              "compressed_bytes": None, "swap_used_bytes": None,
              "pressure_free_percent": None}
    try:
        physical = command(["sysctl", "-n", "hw.memsize"], timeout=2)
        if physical.returncode == 0:
            values["physical_bytes"] = int(physical.stdout.strip())
        swap = command(["sysctl", "vm.swapusage"], timeout=2)
        match = re.search(r"used\s*=\s*([\d.]+[KMG])", swap.stdout)
        if match:
            values["swap_used_bytes"] = parse_size(match.group(1))
        vm = command(["vm_stat"], timeout=2)
        page_match = re.search(r"page size of (\d+) bytes", vm.stdout)
        page_size = int(page_match.group(1)) if page_match else 16384
        pages = {}
        for line in vm.stdout.splitlines():
            match = re.match(r"([^:]+):\s+([\d.]+)\.?", line)
            if match:
                pages[match.group(1)] = int(float(match.group(2)))
        if vm.returncode == 0:
            values["free_bytes"] = (pages.get("Pages free", 0) +
                                    pages.get("Pages speculative", 0)) * page_size
        values["compressed_bytes"] = pages.get("Pages occupied by compressor", 0) * page_size
        pressure = command(["memory_pressure", "-Q"], timeout=4)
        match = re.search(r"System-wide memory free percentage:\s*(\d+)%",
                          pressure.stdout)
        if match:
            values["pressure_free_percent"] = int(match.group(1))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return values


def image_processes() -> list[dict]:
    """Find unmanaged MLX Image Kit jobs without reading their prompt text."""
    try:
        result = command(["ps", "-axo", "pid=,command="], timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, argv = int(parts[0]), parts[1]
        executable = argv.split(None, 1)[0]
        python = Path(executable).name.startswith("python")
        if (executable.endswith("/mlx-image") or
                executable.endswith("/generate.py") or
                python and ("/mlx-image-kit/generate.py" in argv or
                            "/mlx-manager/image_worker.py" in argv)):
            found.append({"pid": pid, "kind": "mlx-image"})
    return found


def disk_mounted(path: Path) -> bool:
    try:
        return path.is_mount()
    except OSError:
        return False
