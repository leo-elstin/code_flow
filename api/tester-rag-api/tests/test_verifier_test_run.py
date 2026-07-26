import os
import pytest
from unittest.mock import patch, MagicMock
from app.tools.dart_tools import run_flutter_test
from app.agents.roles.verifier import _build_llm_payload, compare_to_plan

@patch("subprocess.run")
def test_run_flutter_test_success(mock_run):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "All tests passed."
    mock_proc.stderr = ""
    mock_run.return_value = mock_proc
    
    with patch("app.tools.dart_tools.needs_pub_get", return_value=False), \
         patch("app.tools.dart_tools.is_flutter_project", return_value=True):
        res = run_flutter_test("/tmp/project", ["test/some_test.dart"])
        assert res["passed"] is True
        assert res["exit_code"] == 0

@patch("subprocess.run")
def test_compare_to_plan_triggers_tests(mock_run, tmp_path):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Test run results"
    mock_proc.stderr = ""
    mock_run.return_value = mock_proc

    # Create dummy plan & dummy files in tmp_path
    plan = {
        "files_to_create": [{"path": "test/my_widget_test.dart"}]
    }
    
    test_file = tmp_path / "test" / "my_widget_test.dart"
    os.makedirs(test_file.parent, exist_ok=True)
    test_file.write_text("void main() {}", encoding="utf-8")

    with patch("app.agents.roles.verifier.sync_planned_files_from_project", return_value=[]), \
         patch("app.agents.roles.verifier.ensure_pub_dependencies", return_value={"passed": True}), \
         patch("app.agents.roles.verifier.maybe_run_build_runner", return_value={"passed": True, "skipped": True}), \
         patch("app.agents.roles.verifier.run_dart_analyze", return_value={"passed": True}), \
         patch("app.tools.dart_tools.is_flutter_project", return_value=True):
         
        gate = compare_to_plan(plan, str(tmp_path), file_changes=[{"path": "test/my_widget_test.dart"}])
        assert gate["passed"] is True
        assert gate["test_results"]["passed"] is True
        assert gate["test_results"]["skipped"] is False


def test_build_llm_payload_drops_plan_markdown_but_keeps_structured_fields():
    """plan_markdown is a prose restatement of fields already in the same plan
    dict — it's for the human plan review UI, not the verifier's LLM review,
    and was being resent on every verifier call (up to 3x per run)."""
    plan = {
        "feature_summary": "Add the frobnicator widget.",
        "files_to_create": [],
        "plan_markdown": "# Frobnicator\n\nA VERY long human-readable restatement",
    }

    payload = _build_llm_payload(plan, ["AC1"], [], [], {"passed": True})

    assert "plan_markdown" not in payload["plan"]
    assert payload["plan"]["feature_summary"] == "Add the frobnicator widget."
