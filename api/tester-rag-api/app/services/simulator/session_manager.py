"""In-memory state for the single active WebDriverAgent session this backend
drives at a time. A local dev tool — one simulator session is enough for v1.
"""
import asyncio
from dataclasses import dataclass


@dataclass
class WdaSession:
    udid: str
    state: str = "not_started"  # not_started | building | ready | failed
    error: str | None = None
    process: "asyncio.subprocess.Process | None" = None
    wda_session_id: str | None = None
    task: "asyncio.Task | None" = None


class SimulatorSessionManager:
    def __init__(self) -> None:
        self._session: WdaSession | None = None

    def get(self) -> WdaSession | None:
        return self._session

    def get_or_create(self, udid: str) -> WdaSession:
        if self._session is None or self._session.udid != udid:
            self._session = WdaSession(udid=udid)
        return self._session

    def reset(self) -> None:
        self._session = None


session_manager = SimulatorSessionManager()
