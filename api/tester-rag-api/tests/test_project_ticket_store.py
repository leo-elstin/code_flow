"""Tests for the dev_engine per-project setting in project_ticket_store.py."""

import tempfile
from pathlib import Path

import pytest

from app.services import project_ticket_store as project_store


@pytest.fixture
def isolated_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "projects.db"
        monkeypatch.setattr(project_store, "_DB_PATH", db_path)
        yield tmp


@pytest.fixture
def project(isolated_store, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return project_store.upsert_project(str(repo))


def test_dev_engine_defaults_to_api(project):
    assert project_store.get_dev_engine(project["id"]) == "api"
    assert project["dev_engine"] == "api"


def test_dev_engine_unknown_project_defaults_to_api(isolated_store):
    assert project_store.get_dev_engine(999999) == "api"


def test_update_dev_engine_round_trips(project):
    updated = project_store.update_dev_engine(project["id"], "claude_code_cli")
    assert updated["dev_engine"] == "claude_code_cli"
    assert project_store.get_dev_engine(project["id"]) == "claude_code_cli"

    # get_project also reflects the new value.
    assert project_store.get_project(project["id"])["dev_engine"] == "claude_code_cli"


def test_update_dev_engine_rejects_unknown_value(project):
    with pytest.raises(ValueError):
        project_store.update_dev_engine(project["id"], "bogus")


def test_update_dev_engine_missing_project_returns_none(isolated_store):
    assert project_store.update_dev_engine(999999, "api") is None
