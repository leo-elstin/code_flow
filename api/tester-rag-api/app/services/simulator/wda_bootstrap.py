"""Owns the WebDriverAgent (WDA) process lifecycle for one simulator at a time.

Clones the upstream, third-party Appium WebDriverAgent project (not related to
any other local tooling) on first use, then runs its `WebDriverAgentRunner`
test target against the target simulator via `xcodebuild`. This is the slow,
Xcode-build-on-first-run part of the feature — later runs are fast because the
Xcode project stays built.
"""
import asyncio
import os
import subprocess
import time

import httpx

from app.core.config import settings
from app.core.logging_config import get_logger
from app.services.simulator.session_manager import WdaSession, session_manager

logger = get_logger("simulator.wda_bootstrap")

WDA_REPO_URL = "https://github.com/appium/WebDriverAgent.git"
WDA_CLONE_TIMEOUT = 300
WDA_POLL_INTERVAL = 2.0


class WdaBootstrapError(RuntimeError):
    pass


def _ensure_repo() -> str:
    wda_dir = settings.CODE_AGENT_WDA_DIR
    project_path = os.path.join(wda_dir, "WebDriverAgent.xcodeproj")
    if os.path.isdir(project_path):
        return wda_dir

    parent = os.path.dirname(wda_dir)
    if parent:
        os.makedirs(parent, exist_ok=True)
    logger.info("wda_clone_start url=%s dest=%s", WDA_REPO_URL, wda_dir)
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", WDA_REPO_URL, wda_dir],
            capture_output=True,
            text=True,
            timeout=WDA_CLONE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise WdaBootstrapError("Cloning WebDriverAgent timed out") from exc
    if proc.returncode != 0:
        raise WdaBootstrapError(f"Failed to clone WebDriverAgent: {proc.stderr.strip()}")
    if not os.path.isdir(project_path):
        raise WdaBootstrapError("WebDriverAgent.xcodeproj not found after clone")
    return wda_dir


async def _wait_for_ready(timeout: float) -> bool:
    url = f"http://127.0.0.1:{settings.CODE_AGENT_WDA_PORT}/status"
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=3.0) as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(WDA_POLL_INTERVAL)
    return False


def _build_log_path(wda_dir: str, udid: str) -> str:
    return os.path.join(os.path.dirname(wda_dir), f"wda_build_{udid}.log")


def _tail(path: str, max_chars: int = 2000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_chars * 4))
            return f.read().decode("utf-8", errors="replace")[-max_chars:]
    except OSError:
        return ""


async def _run_bootstrap(session: WdaSession) -> None:
    try:
        wda_dir = await asyncio.to_thread(_ensure_repo)
        log_path = _build_log_path(wda_dir, session.udid)
        # IMPORTANT: xcodebuild produces far more output than a pipe's OS buffer
        # holds. If nothing drains asyncio.subprocess.PIPE, the child blocks on
        # write() forever once the buffer fills — a silent hang, not a build
        # failure. Redirect straight to a file instead so the OS never applies
        # backpressure, and keep the log around for post-mortem debugging.
        with open(log_path, "wb") as log_file:
            process = await asyncio.create_subprocess_exec(
                "xcodebuild",
                "-project",
                os.path.join(wda_dir, "WebDriverAgent.xcodeproj"),
                "-scheme",
                "WebDriverAgentRunner",
                "-destination",
                f"id={session.udid}",
                "-allowProvisioningUpdates",
                "test",
                cwd=wda_dir,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
            )
            session.process = process
            ready = await _wait_for_ready(settings.CODE_AGENT_WDA_BOOTSTRAP_TIMEOUT)

        if ready:
            session.state = "ready"
            logger.info("wda_ready udid=%s", session.udid)
        else:
            session.state = "failed"
            tail = _tail(log_path)
            session.error = (
                "WebDriverAgent did not report ready before "
                f"{settings.CODE_AGENT_WDA_BOOTSTRAP_TIMEOUT}s. Build log "
                f"({log_path}) tail:\n{tail}"
                if tail
                else f"WebDriverAgent did not report ready before "
                f"{settings.CODE_AGENT_WDA_BOOTSTRAP_TIMEOUT}s — check Xcode "
                "signing/team settings on the WebDriverAgentRunner target and "
                "whether the simulator needed a one-time developer-trust prompt."
            )
            logger.warning("wda_bootstrap_timeout udid=%s log=%s", session.udid, log_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("wda_bootstrap_failed udid=%s", session.udid)
        session.state = "failed"
        session.error = str(exc)


async def start(udid: str) -> WdaSession:
    session = session_manager.get_or_create(udid)
    if session.state in ("building", "ready"):
        return session
    session.state = "building"
    session.error = None
    session.task = asyncio.create_task(_run_bootstrap(session))
    return session


async def stop(udid: str) -> None:
    session = session_manager.get()
    if session is None or session.udid != udid:
        return
    if session.task is not None and not session.task.done():
        session.task.cancel()
    if session.process is not None:
        try:
            session.process.terminate()
            await asyncio.wait_for(session.process.wait(), timeout=10)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
    session_manager.reset()
