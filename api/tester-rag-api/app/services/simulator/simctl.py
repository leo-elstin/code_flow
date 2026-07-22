"""Thin wrappers around `xcrun simctl` for listing/booting iOS simulators and
capturing screenshots. No WebDriverAgent involved here — this is the
screenshot-only "Phase A" surface, usable without Xcode building anything.
"""
import json
import os
import subprocess
import tempfile

from app.core.logging_config import get_logger

logger = get_logger("simulator.simctl")

SIMCTL_TIMEOUT = 30
_ALREADY_BOOTED_MARKER = "current state: Booted"


class SimctlError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = SIMCTL_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["xcrun", "simctl", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise SimctlError(
            "xcrun/simctl not found on PATH — Xcode command line tools are required"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SimctlError(f"simctl {' '.join(args)} timed out after {timeout}s") from exc


def _runtime_display_name(runtime_identifier: str) -> str:
    # "com.apple.CoreSimulator.SimRuntime.iOS-26-5" -> "iOS 26.5"
    tail = runtime_identifier.rsplit(".", 1)[-1]
    parts = tail.split("-")
    if len(parts) >= 2:
        platform, version = parts[0], ".".join(parts[1:])
        return f"{platform} {version}"
    return runtime_identifier


def list_devices() -> list[dict]:
    proc = _run(["list", "devices", "--json"])
    if proc.returncode != 0:
        raise SimctlError(proc.stderr.strip() or "simctl list devices failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SimctlError("Could not parse simctl output") from exc

    devices: list[dict] = []
    for runtime_id, entries in data.get("devices", {}).items():
        runtime_name = _runtime_display_name(runtime_id)
        for entry in entries:
            if not entry.get("isAvailable", True):
                continue
            devices.append(
                {
                    "udid": entry["udid"],
                    "name": entry["name"],
                    "state": entry.get("state", "Unknown"),
                    "runtime": runtime_name,
                }
            )
    return devices


def boot_device(udid: str) -> None:
    proc = _run(["boot", udid])
    if proc.returncode != 0 and _ALREADY_BOOTED_MARKER not in proc.stderr:
        raise SimctlError(proc.stderr.strip() or "simctl boot failed")


def capture_screenshot(udid: str) -> bytes:
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        proc = _run(["io", udid, "screenshot", tmp_path])
        if proc.returncode != 0:
            raise SimctlError(proc.stderr.strip() or "simctl screenshot failed")
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
