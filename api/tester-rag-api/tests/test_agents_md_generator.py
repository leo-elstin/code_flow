import subprocess

import pytest

from app.services.agents_md_generator import (
    agents_md_exists,
    get_agents_md_status,
    write_agents_md,
    _strip_markdown_fences,
)


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "pubspec.yaml").write_text("name: demo_app\n", encoding="utf-8")
    lib = repo / "lib"
    lib.mkdir()
    (lib / "main.dart").write_text("void main() {}", encoding="utf-8")
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)
    return repo


def test_agents_md_missing(git_repo):
    assert agents_md_exists(str(git_repo)) is False
    status = get_agents_md_status(str(git_repo))
    assert status["exists"] is False


def test_write_agents_md_creates_file(git_repo):
    path = write_agents_md(str(git_repo), "# Demo\n\nUse feature-first layout.")
    assert path == "AGENTS.md"
    assert agents_md_exists(str(git_repo)) is True
    content = (git_repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "feature-first" in content


def test_write_agents_md_refuses_existing(git_repo):
    write_agents_md(str(git_repo), "# First")
    with pytest.raises(FileExistsError):
        write_agents_md(str(git_repo), "# Second")


def test_strip_markdown_fences():
    raw = "```markdown\n# Title\n\nBody\n```"
    assert _strip_markdown_fences(raw) == "# Title\n\nBody"
