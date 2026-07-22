"""Async HTTP client for a running WebDriverAgent instance (standard,
publicly-documented WDA JSON-wire routes — session create, accessibility
source dump, coordinate tap).
"""
import httpx

from app.core.config import settings

_REQUEST_TIMEOUT = 15.0


class WdaClientError(RuntimeError):
    pass


def _base_url() -> str:
    return f"http://127.0.0.1:{settings.CODE_AGENT_WDA_PORT}"


async def create_session() -> str:
    url = f"{_base_url()}/session"
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json={"capabilities": {}})
        resp.raise_for_status()
        data = resp.json()
        session_id = data.get("sessionId") or (data.get("value") or {}).get("sessionId")
        if not session_id:
            raise WdaClientError("WebDriverAgent did not return a sessionId")
        return session_id


async def get_source(session_id: str) -> str:
    url = f"{_base_url()}/session/{session_id}/source"
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        # WDA wraps the XML accessibility dump in a JSON envelope
        # ({"value": "<?xml ...>", ...}) rather than returning raw XML.
        data = resp.json()
        value = data.get("value")
        if not isinstance(value, str):
            raise WdaClientError("WebDriverAgent did not return XML source in 'value'")
        return value


async def tap(session_id: str, x: float, y: float) -> None:
    # Coordinate tap via the standard W3C WebDriver Actions API (a single
    # touch pointer: move, down, brief pause, up) — the correct
    # element-independent way to tap a point on this WDA build. An earlier
    # version of this client used the legacy `/wda/tap/0` convenience route,
    # which 404s here; verified against a live WebDriverAgent instance.
    url = f"{_base_url()}/session/{session_id}/actions"
    body = {
        "actions": [
            {
                "type": "pointer",
                "id": "finger1",
                "parameters": {"pointerType": "touch"},
                "actions": [
                    {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                    {"type": "pointerDown", "button": 0},
                    {"type": "pause", "duration": 100},
                    {"type": "pointerUp", "button": 0},
                ],
            }
        ]
    }
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()


async def close_session(session_id: str) -> None:
    url = f"{_base_url()}/session/{session_id}"
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        try:
            await client.delete(url)
        except httpx.HTTPError:
            pass
