import tempfile
from pathlib import Path

import pytest

from app.services import llm_config as config_module
from app.services import llm_settings_store as llm_store
from app.services import project_ticket_store as project_store


@pytest.fixture
def isolated_store(monkeypatch):
    """Point both stores at one throwaway DB — they share a file in production."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "projects.db"
        monkeypatch.setattr(project_store, "_DB_PATH", db_path)
        monkeypatch.setattr(llm_store, "_DB_PATH", db_path)
        yield tmp


@pytest.fixture
def project(isolated_store, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return project_store.upsert_project(str(repo))


@pytest.fixture(autouse=True)
def clear_active_project():
    config_module.set_active_project(None)
    yield
    config_module.set_active_project(None)


# --- global config ----------------------------------------------------------


def test_global_config_is_none_until_saved(isolated_store):
    assert llm_store.get_global_config() is None


def test_save_and_read_global_config(isolated_store):
    llm_store.save_global_config(
        provider="anthropic",
        api_key="sk-ant-secret",
        chat_model="claude-sonnet-5",
    )
    saved = llm_store.get_global_config()
    assert saved["provider"] == "anthropic"
    assert saved["api_key"] == "sk-ant-secret"
    assert saved["chat_model"] == "claude-sonnet-5"


def test_saving_twice_updates_the_single_row(isolated_store):
    llm_store.save_global_config(provider="anthropic", api_key="one", chat_model="a")
    llm_store.save_global_config(provider="openai", api_key="two", chat_model="b")

    saved = llm_store.get_global_config()
    assert saved["provider"] == "openai"
    assert saved["api_key"] == "two"


def test_omitting_api_key_preserves_the_stored_one(isolated_store):
    """The UI round-trips a masked key, which must never overwrite the real one."""
    llm_store.save_global_config(provider="anthropic", api_key="sk-real", chat_model="a")
    llm_store.save_global_config(provider="anthropic", api_key=None, chat_model="b")

    saved = llm_store.get_global_config()
    assert saved["api_key"] == "sk-real"
    assert saved["chat_model"] == "b"


def test_empty_api_key_clears_it(isolated_store):
    llm_store.save_global_config(provider="anthropic", api_key="sk-real")
    llm_store.save_global_config(provider="anthropic", api_key="")
    assert llm_store.get_global_config()["api_key"] == ""


def test_unknown_provider_is_rejected(isolated_store):
    with pytest.raises(ValueError, match="Unknown provider"):
        llm_store.save_global_config(provider="gemini")


# --- project overrides ------------------------------------------------------


def test_project_has_no_override_by_default(project):
    assert llm_store.get_project_override(project["id"]) is None


def test_save_and_clear_project_override(project):
    llm_store.save_project_override(
        project["id"], provider="openai", api_key="sk-proj", chat_model="gpt-5.5"
    )
    override = llm_store.get_project_override(project["id"])
    assert override["provider"] == "openai"
    assert override["api_key"] == "sk-proj"

    llm_store.clear_project_override(project["id"])
    assert llm_store.get_project_override(project["id"]) is None


def test_override_for_unknown_project_returns_none(isolated_store):
    assert llm_store.save_project_override(9999, provider="openai") is None


# --- resolution precedence --------------------------------------------------


def test_resolution_falls_back_to_env_when_nothing_saved(isolated_store, monkeypatch):
    monkeypatch.setattr(config_module.settings, "OPENAI_API_KEY", "sk-env")
    monkeypatch.setattr(config_module.settings, "OPENAI_CHAT_MODEL", "gpt-env")
    monkeypatch.setattr(config_module.settings, "LITELLM_API_BASE", "https://gateway.example")

    resolved = config_module.resolve_llm_config()
    assert resolved.provider == "openai_gateway"
    assert resolved.api_key == "sk-env"
    assert resolved.base_url == "https://gateway.example"


def test_env_without_gateway_resolves_to_direct_openai(isolated_store, monkeypatch):
    monkeypatch.setattr(config_module.settings, "LITELLM_API_BASE", None)
    assert config_module.resolve_llm_config().provider == "openai"


def test_global_config_beats_env(isolated_store):
    llm_store.save_global_config(
        provider="anthropic", api_key="sk-ant", chat_model="claude-sonnet-5"
    )
    resolved = config_module.resolve_llm_config()
    assert resolved.provider == "anthropic"
    assert resolved.api_key == "sk-ant"


def test_project_override_beats_global(project):
    llm_store.save_global_config(
        provider="anthropic", api_key="sk-ant", chat_model="claude-sonnet-5"
    )
    llm_store.save_project_override(
        project["id"], provider="openai", api_key="sk-proj", chat_model="gpt-5.5"
    )

    config_module.set_active_project(project["path"])
    resolved = config_module.resolve_llm_config()
    assert resolved.provider == "openai"
    assert resolved.chat_model == "gpt-5.5"


def test_override_inherits_blank_fields_from_same_provider(project):
    llm_store.save_global_config(
        provider="anthropic", api_key="sk-ant-global", chat_model="claude-sonnet-5"
    )
    # Same provider, model-only override — the key should carry over.
    llm_store.save_project_override(
        project["id"], provider="anthropic", api_key="", chat_model="claude-opus-4-8"
    )

    config_module.set_active_project(project["path"])
    resolved = config_module.resolve_llm_config()
    assert resolved.api_key == "sk-ant-global"
    assert resolved.chat_model == "claude-opus-4-8"


def test_override_does_not_inherit_across_providers(project):
    """An OpenAI key leaking into an Anthropic override would only confuse."""
    llm_store.save_global_config(
        provider="openai", api_key="sk-openai", chat_model="gpt-5.5"
    )
    llm_store.save_project_override(
        project["id"], provider="anthropic", api_key="", chat_model="claude-sonnet-5"
    )

    config_module.set_active_project(project["path"])
    resolved = config_module.resolve_llm_config()
    assert resolved.provider == "anthropic"
    assert resolved.api_key == ""


def test_unrelated_active_project_falls_back_to_global(isolated_store):
    llm_store.save_global_config(provider="anthropic", api_key="sk-ant", chat_model="c")
    config_module.set_active_project("/path/that/is/not/a/project")

    assert config_module.resolve_llm_config().provider == "anthropic"


def test_dev_model_falls_back_to_chat_model(isolated_store):
    """Matches the CODE_AGENT_DEV_MODEL default: never silently degrade."""
    llm_store.save_global_config(
        provider="anthropic", api_key="sk-ant", chat_model="claude-sonnet-5", dev_model=None
    )
    assert config_module.resolve_llm_config().resolved_dev_model == "claude-sonnet-5"
