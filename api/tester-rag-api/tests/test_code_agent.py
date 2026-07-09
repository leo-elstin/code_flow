import os
import subprocess
import tempfile

import pytest

from app.tools.grep import normalize_search_terms
from app.tools.path_guard import PathGuardError, resolve_read_path


def test_normalize_search_terms_variants():
    terms = normalize_search_terms("Add Multi-Container booking feature")
    assert "multi-container" in terms or "multi" in terms
    assert "booking" in terms


def test_path_traversal_blocked(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "safe.txt").write_text("ok", encoding="utf-8")
    with pytest.raises(PathGuardError):
        resolve_read_path(str(root), "../outside.txt")


def test_worktree_create_and_remove(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    from app.core.config import settings
    from app.services.worktree import create_worktree, remove_worktree

    old_dir = settings.CODE_AGENT_WORKTREES_DIR
    settings.CODE_AGENT_WORKTREES_DIR = "./data/worktrees"
    try:
        info = create_worktree(str(repo), run_id="test-run")
        assert os.path.isdir(info["worktree_path"])
        assert info["worktree_path"].startswith(str(repo))
        assert os.path.isfile(os.path.join(info["worktree_path"], "README.md"))
        remove_worktree(str(repo), info["worktree_path"])
    finally:
        settings.CODE_AGENT_WORKTREES_DIR = old_dir


def test_sync_dirty_files(tmp_path):
    from app.services.worktree import sync_dirty_files

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    
    # 1. Initial commit
    readme = repo / "README.md"
    readme.write_text("initial contents\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # 2. Add uncommitted changes
    readme.write_text("modified contents\n", encoding="utf-8")
    new_file = repo / "NEW.md"
    new_file.write_text("new file contents\n", encoding="utf-8")
    deleted_file = repo / "DELETED.md"
    deleted_file.write_text("to be deleted\n", encoding="utf-8")
    subprocess.run(["git", "add", "DELETED.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add deleted_file"], cwd=repo, check=True)
    os.remove(deleted_file)

    # Create dummy destination worktree directory
    dest = tmp_path / "dest"
    dest.mkdir()
    
    # Write some placeholder files to check deletion and modification behavior
    (dest / "README.md").write_text("initial contents\n", encoding="utf-8")
    (dest / "DELETED.md").write_text("to be deleted\n", encoding="utf-8")

    sync_dirty_files(str(repo), str(dest))

    # Assert modifications copied
    assert (dest / "README.md").read_text(encoding="utf-8") == "modified contents\n"
    # Assert untracked file copied
    assert (dest / "NEW.md").read_text(encoding="utf-8") == "new file contents\n"
    # Assert deleted file deleted
    assert not (dest / "DELETED.md").exists()


def test_resolve_worktree_path_ignores_process_cwd(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.worktree import resolve_worktree_path

    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    old_dir = settings.CODE_AGENT_WORKTREES_DIR
    settings.CODE_AGENT_WORKTREES_DIR = "./data/worktrees"
    try:
        resolved = resolve_worktree_path(str(repo), run_id="abc")
        assert resolved == os.path.realpath(str(repo / "data" / "worktrees" / "abc"))
    finally:
        settings.CODE_AGENT_WORKTREES_DIR = old_dir


def test_verifier_gate_missing_file(tmp_path):
    from app.agents.roles.verifier import compare_to_plan

    worktree = tmp_path / "wt"
    worktree.mkdir()
    plan = {"files_to_create": [{"path": "lib/missing.dart"}]}
    report = compare_to_plan(plan, str(worktree))
    assert report["passed"] is False
    assert report["required_fixes"]


def test_verifier_ignores_unmodified_context_files(tmp_path):
    from app.agents.roles.verifier import compare_to_plan

    project = tmp_path / "project"
    worktree = tmp_path / "wt"
    lib = project / "lib" / "service"
    lib.mkdir(parents=True)
    worktree.mkdir()
    (lib / "weight_repo.dart").write_text("class WeightRepo {}\n", encoding="utf-8")

    plan = {
        "files_to_modify": [
            {"path": "lib/service/weight_repo.dart"},
            {"path": "lib/other/context.dart"},
        ]
    }
    file_changes = [{"path": "lib/feature/new_feature.dart", "action": "create"}]
    (worktree / "lib" / "feature").mkdir(parents=True)
    (worktree / "lib" / "feature" / "new_feature.dart").write_text(
        "class NewFeature {}\n",
        encoding="utf-8",
    )

    report = compare_to_plan(
        plan,
        str(worktree),
        file_changes,
        project_path=str(project),
    )
    assert "Planned file missing in worktree: lib/service/weight_repo.dart" not in report[
        "required_fixes"
    ]
    assert "Planned modification not detected for: lib/other/context.dart" not in report[
        "required_fixes"
    ]
    assert "lib/service/weight_repo.dart" in report["synced_from_project"]


def test_sync_file_from_project(tmp_path):
    from app.tools.worktree_sync import sync_file_from_project

    project = tmp_path / "project"
    worktree = tmp_path / "wt"
    target = project / "lib" / "weight_repo.dart"
    target.parent.mkdir(parents=True)
    target.write_text("class WeightRepo {}\n", encoding="utf-8")
    worktree.mkdir()

    synced = sync_file_from_project(str(project), str(worktree), "lib/weight_repo.dart")
    assert synced is True
    assert (worktree / "lib" / "weight_repo.dart").is_file()


def test_dart_public_method_names():
    from app.tools.dart_tools import dart_public_method_names

    content = """
class DbService {
  Future<void> insertMeal() async {}
  Future<int> addWeight() async { return 0; }
}
"""
    assert dart_public_method_names(content) == {"insertMeal", "addWeight"}


def test_verifier_detects_removed_methods(tmp_path):
    from app.agents.roles.verifier import compare_to_plan

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

    dart_path = repo / "lib" / "service" / "db_service.dart"
    dart_path.parent.mkdir(parents=True)
    dart_path.write_text(
        "class DbService {\n"
        "  Future<void> keepMe() async {}\n"
        "  Future<int> removeMe() async { return 0; }\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    dart_path.write_text(
        "class DbService {\n"
        "  Future<void> keepMe() async {}\n"
        "  Future<void> insertWater() async {}\n"
        "}\n",
        encoding="utf-8",
    )

    plan = {"files_to_modify": [{"path": "lib/service/db_service.dart"}]}
    file_changes = [{"path": "lib/service/db_service.dart", "action": "modify"}]

    report = compare_to_plan(plan, str(repo), file_changes)
    assert report["passed"] is False
    assert any("removeMe" in fix for fix in report["required_fixes"])


def test_graph_routing():
    from app.orchestration.graph import route_after_verifier

    assert route_after_verifier({"status": "qa"}) == "qa"
    assert route_after_verifier({"status": "developing"}) == "dev"


def test_runner_pauses_after_planner_not_before_dev():
    from app.orchestration.runner import CodeAgentRunner

    runner = CodeAgentRunner()

    class _Saver:
        pass

    async def _compile(checkpointer):
        return runner._graph_builder.compile(
            checkpointer=checkpointer,
            interrupt_after=["planner"],
        )

    runner._compile = _compile  # type: ignore[method-assign]
    import asyncio

    async def _assert_interrupts():
        async with runner._checkpointer() as saver:
            graph = await runner._compile(saver)
            assert graph.interrupt_after_nodes == ["planner"]
            assert graph.interrupt_before_nodes == []

    asyncio.run(_assert_interrupts())


def test_build_runner_detection(tmp_path):
    from app.core.config import settings
    from app.tools.dart_tools import (
        is_flutter_project,
        is_generated_dart_path,
        needs_pub_get,
        project_has_build_runner,
        should_run_build_runner,
    )

    project = tmp_path / "flutter_app"
    project.mkdir()
    (project / "pubspec.yaml").write_text(
        "name: demo\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n"
        "dev_dependencies:\n"
        "  build_runner: ^2.4.0\n",
        encoding="utf-8",
    )

    assert is_flutter_project(str(project)) is True
    assert needs_pub_get(str(project)) is True
    assert project_has_build_runner(str(project)) is True
    assert should_run_build_runner(str(project), ["lib/main.dart"]) is True
    assert should_run_build_runner(str(project), []) is False
    assert is_generated_dart_path("lib/model.g.dart") is True
    assert is_generated_dart_path("lib/model.dart") is False

    old_flag = settings.CODE_AGENT_RUN_BUILD_RUNNER
    settings.CODE_AGENT_RUN_BUILD_RUNNER = False
    try:
        assert should_run_build_runner(str(project), ["lib/main.dart"]) is False
    finally:
        settings.CODE_AGENT_RUN_BUILD_RUNNER = old_flag


def test_discover_context_is_grep_first_without_vector(tmp_path):
    from app.services.feature_discovery import discover_context

    project = tmp_path / "app"
    lib_dir = project / "lib" / "water"
    lib_dir.mkdir(parents=True)
    (project / "pubspec.yaml").write_text(
        "name: demo\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n",
        encoding="utf-8",
    )
    (lib_dir / "water_service.dart").write_text(
        "class WaterService {\n"
        "  void addWater() {}\n"
        "}\n",
        encoding="utf-8",
    )

    bundle = discover_context("Add water tracking feature", str(project))

    assert "semantic_hits" not in bundle
    assert bundle["files"]
    assert any("water" in path for path in bundle["files"])
    assert bundle["symbols"]
    assert bundle["symbols"][0]["name"] == "WaterService"


def test_discover_context_loads_agents_md(tmp_path):
    from app.services.feature_discovery import discover_context

    project = tmp_path / "app"
    lib_dir = project / "lib"
    lib_dir.mkdir(parents=True)
    (project / "pubspec.yaml").write_text(
        "name: demo\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n",
        encoding="utf-8",
    )
    (project / "AGENTS.md").write_text(
        "# Layout\n\nUse lib/service/ for repos, lib/ui/<feature>/vm for cubits.\n",
        encoding="utf-8",
    )

    bundle = discover_context("Add feature", str(project))

    assert bundle["project_guide"]["found"] is True
    assert "lib/service/" in bundle["project_guide"]["content"]


def test_parse_analyze_errors_filters_by_target():
    from app.tools.dart_tools import _parse_analyze_errors

    output = (
        "  error • Undefined class 'Foo' • lib/ui/water/water_screen.dart:10:5 • undefined_class\n"
        "  error • Unused import • test/foo_test.dart:1:1 • unused_import\n"
        "  warning • Dead code • lib/ui/water/water_screen.dart:20:1 • dead_code\n"
    )
    count, lines = _parse_analyze_errors(
        output,
        ["lib/ui/water/water_screen.dart"],
    )
    assert count == 1
    assert "water_screen.dart" in lines[0]


def test_analyze_advisory_mode(tmp_path, monkeypatch):
    from app.agents.roles.verifier import compare_to_plan

    worktree = tmp_path / "wt"
    (worktree / "lib").mkdir(parents=True)
    (worktree / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    (worktree / "pubspec.yaml").write_text(
        "name: demo\n"
        "dependencies:\n"
        "  flutter:\n"
        "    sdk: flutter\n",
        encoding="utf-8",
    )

    def fake_analyze(_worktree_path, _targets):
        return {
            "passed": False,
            "blocking_passed": True,
            "advisory_only": True,
            "error_count": 2,
            "error_lines": ["error • Undefined class 'Foo' • lib/main.dart:1:1"],
        }

    monkeypatch.setattr("app.agents.roles.verifier.run_dart_analyze", fake_analyze)
    monkeypatch.setattr(
        "app.agents.roles.verifier.ensure_pub_dependencies",
        lambda _path: {"passed": True, "skipped": True},
    )
    monkeypatch.setattr(
        "app.agents.roles.verifier.maybe_run_build_runner",
        lambda _path, _targets: {"passed": True, "skipped": True},
    )

    report = compare_to_plan(
        {"files_to_create": []},
        str(worktree),
        [{"path": "lib/main.dart", "action": "modify"}],
    )

    assert report["passed"] is True
    assert report["required_fixes"] == []
    assert any(issue.get("severity") == "warning" for issue in report["issues"])


def test_uri_helpers():
    from app.tools.lsp_client import abs_path_to_uri, uri_to_abs_path

    path = "/tmp/example/lib/main.dart"
    uri = abs_path_to_uri(path)
    assert uri_to_abs_path(uri) == os.path.realpath(path)


def test_build_analyze_verifier_report():
    from app.orchestration.manual_retry import build_analyze_verifier_report

    analyze = {
        "error_lines": ["error • Missing import • lib/foo.dart:1:1 • undefined_class"],
        "error_count": 1,
    }
    fix_plan = {
        "summary": "Add missing import",
        "required_fixes": ["Import the Foo class"],
        "steps": ["Edit lib/foo.dart"],
    }
    report = build_analyze_verifier_report(analyze, fix_plan)
    assert report["passed"] is False
    assert "Import the Foo class" in report["required_fixes"]
    assert report["headline"] == "Add missing import"
    assert report["issues"][0]["file"] == "lib/foo.dart"


def test_analyze_targets_prefers_changed_files(monkeypatch):
    from app.orchestration.manual_retry import analyze_targets

    monkeypatch.setattr(
        "app.orchestration.manual_retry.list_changed_files",
        lambda _path: ["lib/b.dart", "test/a_test.dart"],
    )
    targets = analyze_targets("/wt", {"files_to_modify": [{"path": "lib/other.dart"}]})
    assert targets == ["lib/b.dart"]


def test_execute_manual_retry_completes_when_analyze_clean(monkeypatch):
    import asyncio

    from app.orchestration.manual_retry import execute_manual_retry

    state = {
        "run_id": "r1",
        "status": "failed",
        "worktree_path": "/wt",
        "project_path": "/proj",
        "plan": {},
        "acceptance_criteria": [],
        "file_changes": [],
        "diffs": [],
        "user_request": "add water",
    }

    async def get_state():
        return state

    async def patch_state(patch):
        state.update(patch)

    monkeypatch.setattr(
        "app.orchestration.manual_retry.run_dart_analyze",
        lambda _wt, _targets: {"passed": True, "error_count": 0, "error_lines": []},
    )

    async def fake_verifier(**_kwargs):
        return {"verifier_report": {"passed": True}, "diffs": [], "messages": []}

    async def fake_qa(**_kwargs):
        return {"qa_report": {"ok": True}, "messages": []}

    monkeypatch.setattr("app.orchestration.manual_retry.run_verifier", fake_verifier)
    monkeypatch.setattr("app.orchestration.manual_retry.run_qa", fake_qa)

    asyncio.run(execute_manual_retry(get_state=get_state, patch_state=patch_state))
    assert state["status"] == "completed"


def test_execute_manual_retry_exhausts_analyze_loops(monkeypatch):
    import asyncio

    from app.orchestration.manual_retry import execute_manual_retry

    state = {
        "run_id": "r1",
        "status": "failed",
        "worktree_path": "/wt",
        "project_path": "/proj",
        "plan": {},
        "acceptance_criteria": [],
        "file_changes": [],
        "user_request": "add water",
    }

    async def get_state():
        return state

    async def patch_state(patch):
        state.update(patch)

    monkeypatch.setattr(
        "app.orchestration.manual_retry.run_dart_analyze",
        lambda _wt, _targets: {
            "passed": False,
            "error_count": 1,
            "error_lines": ["error • Bad type • lib/foo.dart:2:3 • invalid_assignment"],
        },
    )

    async def fake_plan(**_kwargs):
        return {"summary": "Fix type", "required_fixes": ["Fix assignment"], "steps": ["Edit foo"]}

    async def fake_dev(**_kwargs):
        return {"file_changes": [{"path": "lib/foo.dart", "action": "modify"}], "messages": []}

    monkeypatch.setattr("app.orchestration.manual_retry.plan_analyze_fixes", fake_plan)
    monkeypatch.setattr("app.orchestration.manual_retry.run_dev", fake_dev)
    monkeypatch.setattr("app.orchestration.manual_retry.settings.CODE_AGENT_MAX_MANUAL_RETRY_ITERATIONS", 2)

    asyncio.run(execute_manual_retry(get_state=get_state, patch_state=patch_state))
    assert state["status"] == "failed"
    assert "Manual retry exhausted" in (state.get("error") or "")


def test_validate_manual_retry_eligibility():
    from app.orchestration.manual_retry import validate_manual_retry_eligibility

    with pytest.raises(ValueError, match="failed"):
        validate_manual_retry_eligibility({"status": "completed", "worktree_path": "/wt"})

    with pytest.raises(ValueError, match="Worktree"):
        validate_manual_retry_eligibility({"status": "failed", "worktree_path": None})

    validate_manual_retry_eligibility({"status": "failed", "worktree_path": "/wt"})


def test_run_index_upsert_and_list(tmp_path, monkeypatch):
    from app.services import run_index

    index_path = tmp_path / "run_index.db"
    monkeypatch.setattr(run_index, "_INDEX_PATH", index_path)

    run_index.upsert_run(
        {
            "run_id": "aaa",
            "status": "planning",
            "user_request": "Add water tracking",
            "project_path": "/proj/fitpal",
            "iteration": 0,
        }
    )
    run_index.upsert_run(
        {
            "run_id": "aaa",
            "status": "failed",
            "user_request": "Add water tracking",
            "project_path": "/proj/fitpal",
            "iteration": 3,
            "error": "Verifier retries exhausted",
        }
    )

    all_runs = run_index.list_runs()
    assert len(all_runs) == 1
    assert all_runs[0]["run_id"] == "aaa"
    assert all_runs[0]["status"] == "failed"
    assert all_runs[0]["iteration"] == 3

    filtered = run_index.list_runs(project_path="/proj/fitpal")
    assert len(filtered) == 1

    other = run_index.list_runs(project_path="/proj/other")
    assert other == []


def test_prepare_in_place_workspace(tmp_path):
    from app.services.worktree import prepare_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    info = prepare_workspace(str(repo), run_id="in-place-run", workspace_mode="in_place")
    assert info["workspace_mode"] == "in_place"
    assert info["worktree_path"] == os.path.realpath(str(repo))
    assert info["branch"] is None


def test_prepare_in_place_allows_dirty_repo(tmp_path, monkeypatch):
    # In-place now snapshots files before editing and rolls back from those
    # snapshots, so a dirty tree is allowed and the user's uncommitted work is
    # left intact at workspace prep.
    from app.services import worktree as wt_mod
    from app.services.worktree import prepare_workspace

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    # Uncommitted tracked change present at start.
    (repo / "README.md").write_text("# test (work in progress)", encoding="utf-8")

    monkeypatch.setattr(wt_mod, "ensure_pub_dependencies", lambda *_a, **_k: {"passed": True})
    info = prepare_workspace(str(repo), run_id="dirty-run", workspace_mode="in_place")

    assert info["workspace_mode"] == "in_place"
    # The uncommitted change is preserved (not reset/stashed away).
    assert (repo / "README.md").read_text(encoding="utf-8") == "# test (work in progress)"


def test_merge_rejects_in_place_workspace():
    from app.services.merge_worktree import MergeValidationError, validate_merge_request

    with pytest.raises(MergeValidationError, match="in-place"):
        validate_merge_request(
            {
                "status": "completed",
                "workspace_mode": "in_place",
                "worktree_path": "/proj",
            }
        )


def test_sync_missing_from_checkpoints_empty_db(tmp_path, monkeypatch):
    import asyncio

    from app.core.config import settings
    from app.services import run_index

    index_path = tmp_path / "run_index.db"
    checkpoint_path = tmp_path / "checkpoints.db"
    checkpoint_path.touch()

    monkeypatch.setattr(run_index, "_INDEX_PATH", index_path)
    monkeypatch.setattr(settings, "CODE_AGENT_CHECKPOINT_DB", str(checkpoint_path))

    async def _get_state(_run_id: str):
        return None

    synced = asyncio.run(run_index.sync_missing_from_checkpoints(_get_state))
    assert synced == 0


def _init_git_repo(repo, *, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _create_agent_worktree(repo, run_id: str, monkeypatch):
    from app.core.config import settings
    from app.services.worktree import create_worktree

    monkeypatch.setattr(settings, "CODE_AGENT_WORKTREES_DIR", "./data/worktrees")
    return create_worktree(str(repo), run_id=run_id)


def test_merge_happy_path(tmp_path, monkeypatch):
    from app.services.merge_worktree import apply_merge_to_base
    from app.tools.git_tools import is_repo_clean

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    run_id = "run-merge-ok"
    info = _create_agent_worktree(repo, run_id, monkeypatch)

    feature = repo / "data" / "worktrees" / run_id / "feature.txt"
    feature.parent.mkdir(parents=True, exist_ok=True)
    feature.write_text("agent change\n", encoding="utf-8")

    result = apply_merge_to_base(
        run_id=run_id,
        project_path=str(repo),
        worktree_path=info["worktree_path"],
    )

    assert result["applied"] is True
    assert result["target_branch"] == "main"
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "agent change\n"
    assert is_repo_clean(str(repo))


def test_merge_conflict_aborts_and_keeps_main_clean(tmp_path, monkeypatch):
    from app.services.merge_worktree import apply_merge_to_base
    from app.tools.git_tools import is_repo_clean

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    run_id = "run-merge-conflict"
    info = _create_agent_worktree(repo, run_id, monkeypatch)

    wt_file = repo / "data" / "worktrees" / run_id / "conflict.txt"
    wt_file.write_text("from agent\n", encoding="utf-8")
    (repo / "conflict.txt").write_text("from main\n", encoding="utf-8")
    subprocess.run(["git", "add", "conflict.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "main edit"], cwd=repo, check=True, capture_output=True)

    result = apply_merge_to_base(
        run_id=run_id,
        project_path=str(repo),
        worktree_path=info["worktree_path"],
    )

    assert result["applied"] is False
    assert result["code"] == "merge_conflict"
    assert "conflict.txt" in result["conflict_files"]
    assert (repo / "conflict.txt").read_text(encoding="utf-8") == "from main\n"
    assert is_repo_clean(str(repo))


def test_merge_rejects_dirty_main_repo(tmp_path, monkeypatch):
    from app.services.merge_worktree import MergeValidationError, apply_merge_to_base

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    run_id = "run-dirty-main"
    info = _create_agent_worktree(repo, run_id, monkeypatch)

    wt_file = repo / "data" / "worktrees" / run_id / "feature.txt"
    wt_file.write_text("agent\n", encoding="utf-8")
    (repo / "README.md").write_text("dirty edit\n", encoding="utf-8")

    with pytest.raises(MergeValidationError, match="uncommitted"):
        apply_merge_to_base(
            run_id=run_id,
            project_path=str(repo),
            worktree_path=info["worktree_path"],
        )


def test_validate_merge_request_already_applied():
    from app.services.merge_worktree import MergeValidationError, validate_merge_request

    with pytest.raises(MergeValidationError, match="Already merged"):
        validate_merge_request(
            {
                "status": "completed",
                "worktree_path": "/wt",
                "merge_report": {"applied": True, "target_branch": "main"},
            }
        )
