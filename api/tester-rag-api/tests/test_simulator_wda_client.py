import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.simulator.wda_client import get_source, tap, WdaClientError
from app.services.simulator import hierarchy_parser
import pytest


def test_get_source_unwraps_json_envelope():
    # WDA's /source route wraps the XML in {"value": "<?xml ...>"} — regression
    # test for a bug where we returned resp.text (the whole JSON blob) instead
    # of the unwrapped XML string, which broke hierarchy_parser downstream.
    fake_xml = '<XCUIElementTypeApplication type="XCUIElementTypeApplication" x="0" y="0" width="390" height="844"/>'
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"value": fake_xml, "sessionId": "s1"})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        source = asyncio.run(get_source("s1"))

    assert source == fake_xml
    tree = hierarchy_parser.parse(source)
    assert tree.root_width == 390
    assert tree.root_height == 844


def test_get_source_raises_when_value_missing():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"sessionId": "s1"})

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_response)):
        with pytest.raises(WdaClientError):
            asyncio.run(get_source("s1"))


def test_tap_posts_w3c_pointer_actions():
    # Regression test: an earlier version posted to the legacy `/wda/tap/0`
    # route, which 404s against a live WebDriverAgent instance. Coordinate
    # taps must go through the W3C Actions API instead.
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient.post", new=mock_post):
        asyncio.run(tap("s1", 12.5, 34.0))

    called_url = mock_post.call_args.args[0]
    assert called_url.endswith("/session/s1/actions")
    body = mock_post.call_args.kwargs["json"]
    pointer_actions = body["actions"][0]["actions"]
    move = next(a for a in pointer_actions if a["type"] == "pointerMove")
    assert move["x"] == 12.5
    assert move["y"] == 34.0
    assert any(a["type"] == "pointerDown" for a in pointer_actions)
    assert any(a["type"] == "pointerUp" for a in pointer_actions)
