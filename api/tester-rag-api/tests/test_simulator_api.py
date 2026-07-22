import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from main import app
from app.services.simulator.simctl import SimctlError
from app.services.simulator.session_manager import session_manager, WdaSession
from app.services.simulator.wda_client import WdaClientError


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_session_manager():
    session_manager.reset()
    yield
    session_manager.reset()


def test_list_devices(client):
    fake_devices = [
        {"udid": "ABCD", "name": "iPhone 17 Pro", "state": "Shutdown", "runtime": "iOS 26.5"},
    ]
    with patch("app.api.simulator.routes.simctl.list_devices", return_value=fake_devices):
        response = client.get("/api/simulator/devices")
        assert response.status_code == 200
        assert response.json()["devices"] == fake_devices


def test_list_devices_simctl_unavailable(client):
    with patch(
        "app.api.simulator.routes.simctl.list_devices",
        side_effect=SimctlError("xcrun/simctl not found on PATH"),
    ):
        response = client.get("/api/simulator/devices")
        assert response.status_code == 502
        assert "simctl" in response.json()["detail"]


def test_boot_device(client):
    with patch("app.api.simulator.routes.simctl.boot_device", return_value=None) as mock_boot:
        response = client.post("/api/simulator/devices/ABCD/boot")
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        mock_boot.assert_called_once_with("ABCD")


def test_get_screenshot_returns_png(client):
    png_bytes = b"\x89PNG\r\n\x1a\nfake"
    with patch("app.api.simulator.routes.simctl.capture_screenshot", return_value=png_bytes):
        response = client.get("/api/simulator/devices/ABCD/screenshot")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == png_bytes


def test_wda_status_not_started(client):
    response = client.get("/api/simulator/devices/ABCD/wda/status")
    assert response.status_code == 200
    assert response.json() == {"state": "not_started", "error": None}


def test_start_wda_reports_building(client):
    async def fake_start(udid):
        session = session_manager.get_or_create(udid)
        session.state = "building"
        return session

    with patch("app.api.simulator.routes.wda_bootstrap.start", side_effect=fake_start):
        response = client.post("/api/simulator/devices/ABCD/wda/start")
        assert response.status_code == 200
        assert response.json()["state"] == "building"


def test_ui_tree_requires_ready_wda(client):
    response = client.get("/api/simulator/devices/ABCD/ui-tree")
    assert response.status_code == 409


def test_tap_requires_ready_wda(client):
    response = client.post("/api/simulator/devices/ABCD/tap", json={"x": 1, "y": 2})
    assert response.status_code == 409


def test_ui_tree_returns_parsed_elements(client):
    session = session_manager.get_or_create("ABCD")
    session.state = "ready"
    session.wda_session_id = "sess-1"

    fake_xml = (
        '<AppiumAUT><XCUIElementTypeApplication type="XCUIElementTypeApplication" '
        'label="App" x="0" y="0" width="390" height="844">'
        '<XCUIElementTypeButton type="XCUIElementTypeButton" label="Submit" '
        'x="20" y="700" width="100" height="44"/>'
        "</XCUIElementTypeApplication></AppiumAUT>"
    )
    with patch(
        "app.api.simulator.routes.wda_client.get_source",
        new=AsyncMock(return_value=fake_xml),
    ):
        response = client.get("/api/simulator/devices/ABCD/ui-tree")
        assert response.status_code == 200
        body = response.json()
        assert body["root_width"] == 390
        assert body["root_height"] == 844
        assert len(body["elements"]) == 2
        assert body["elements"][1]["label"] == "Submit"


def test_tap_success(client):
    session = session_manager.get_or_create("ABCD")
    session.state = "ready"
    session.wda_session_id = "sess-1"

    with patch(
        "app.api.simulator.routes.wda_client.tap", new=AsyncMock(return_value=None)
    ) as mock_tap:
        response = client.post("/api/simulator/devices/ABCD/tap", json={"x": 12.5, "y": 34.0})
        assert response.status_code == 200
        assert response.json() == {"ok": True}
        mock_tap.assert_called_once_with("sess-1", 12.5, 34.0)


def test_tap_wda_client_error(client):
    session = session_manager.get_or_create("ABCD")
    session.state = "ready"
    session.wda_session_id = "sess-1"

    with patch(
        "app.api.simulator.routes.wda_client.tap",
        new=AsyncMock(side_effect=WdaClientError("boom")),
    ):
        response = client.post("/api/simulator/devices/ABCD/tap", json={"x": 1, "y": 2})
        assert response.status_code == 502
