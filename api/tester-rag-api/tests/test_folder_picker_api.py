from unittest.mock import patch

from fastapi.testclient import TestClient

from main import app


def test_pick_folder_returns_selected_path(tmp_path):
    client = TestClient(app)
    selected = str(tmp_path)

    with patch("app.api.code_agent.projects_routes.pick_folder", return_value=selected):
        response = client.post("/api/code-agent/pick-folder", json={})

    assert response.status_code == 200
    assert response.json() == {"path": selected}


def test_pick_folder_returns_204_when_cancelled():
    client = TestClient(app)

    with patch("app.api.code_agent.projects_routes.pick_folder", return_value=None):
        response = client.post("/api/code-agent/pick-folder", json={})

    assert response.status_code == 204


def test_pick_folder_ignores_invalid_initial_dir(tmp_path):
    client = TestClient(app)
    selected = str(tmp_path)

    with patch("app.api.code_agent.projects_routes.pick_folder", return_value=selected) as mock_pick:
        response = client.post(
            "/api/code-agent/pick-folder",
            json={"initial_dir": "/does/not/exist"},
        )

    assert response.status_code == 200
    mock_pick.assert_called_once_with(initial_dir=None)
