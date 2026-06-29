import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

from main import app
from app.orchestration.runner import runner

@pytest.fixture
def client():
    return TestClient(app)

def test_activity_endpoint_not_found(client):
    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = None
        response = client.get("/api/code-agent/runs/missing-run/activity")
        assert response.status_code == 404


def test_activity_endpoint_returns_events(client, tmp_path, monkeypatch):
    from app.services import run_activity

    db_path = tmp_path / "act.db"
    monkeypatch.setattr(run_activity, "_DB_PATH", db_path)

    run_activity.append_activity(
        "run-act",
        type="grep",
        phase="planner",
        title="Grepping",
        files=["lib/a.dart"],
    )

    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = {"run_id": "run-act", "status": "planning"}
        response = client.get("/api/code-agent/runs/run-act/activity")
        assert response.status_code == 200
        body = response.json()
        assert len(body["events"]) == 1
        assert body["events"][0]["title"] == "Grepping"
        assert body["token_usage"]["total_tokens"] == 0


def test_resume_run_endpoint_not_found(client):
    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = None
        
        response = client.post("/api/code-agent/runs/nonexistent-run/resume")
        assert response.status_code == 404
        assert response.json()["detail"] == "Run not found"

def test_resume_run_endpoint_already_running(client):
    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state, \
         patch.object(runner, "resume_run", new_callable=AsyncMock) as mock_resume_run:
        
        mock_get_state.return_value = {
            "run_id": "running-run",
            "status": "developing",
            "project_path": "/path/to/project",
        }
        mock_resume_run.side_effect = ValueError("Run already in progress")
        
        response = client.post("/api/code-agent/runs/running-run/resume")
        assert response.status_code == 409
        assert response.json()["detail"] == "Run already in progress"

def test_resume_run_endpoint_invalid_status(client):
    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state, \
         patch.object(runner, "resume_run", new_callable=AsyncMock) as mock_resume_run:
        
        mock_get_state.return_value = {
            "run_id": "completed-run",
            "status": "completed",
            "project_path": "/path/to/project",
        }
        mock_resume_run.side_effect = ValueError("Cannot resume run in status completed")
        
        response = client.post("/api/code-agent/runs/completed-run/resume")
        assert response.status_code == 409
        assert response.json()["detail"] == "Cannot resume run in status completed"

def test_resume_run_endpoint_success(client):
    with patch.object(runner, "get_state", new_callable=AsyncMock) as mock_get_state, \
         patch.object(runner, "resume_run", new_callable=AsyncMock) as mock_resume_run, \
         patch.object(runner, "_task_running", return_value=True) as mock_task_running:
        
        mock_get_state.return_value = {
            "run_id": "resume-ok",
            "status": "developing",
            "project_path": "/path/to/project",
            "user_request": "add water",
        }
        mock_resume_run.return_value = {
            "run_id": "resume-ok",
            "status": "developing",
            "project_path": "/path/to/project",
            "user_request": "add water",
        }
        
        response = client.post("/api/code-agent/runs/resume-ok/resume")
        assert response.status_code == 202
        body = response.json()
        assert body["run_id"] == "resume-ok"
        assert body["status"] == "developing"
        assert body["is_running"] is True

def test_runner_resume_run_validations():
    import asyncio

    async def _run():
        from app.orchestration.runner import CodeAgentRunner
        
        test_runner = CodeAgentRunner()
        
        # 1. Run not found
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            res = await test_runner.resume_run("nonexistent")
            assert res is None
            
        # 2. Already running
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=True):
            mock_get.return_value = {"status": "developing"}
            with pytest.raises(ValueError, match="Run already in progress"):
                await test_runner.resume_run("some-run")
                
        # 3. Invalid status
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=False):
            mock_get.return_value = {"status": "completed"}
            with pytest.raises(ValueError, match="Cannot resume run in status completed"):
                await test_runner.resume_run("completed-run")
                
        # 4. Success path (calls graph.ainvoke and starts task)
        dummy_task = MagicMock()
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=False), \
             patch("asyncio.create_task", return_value=dummy_task) as mock_create_task:
            
            state = {"status": "developing", "project_path": "/foo", "run_id": "ok-run"}
            mock_get.return_value = state
            
            res = await test_runner.resume_run("ok-run")
            assert res == state
            assert test_runner._tasks["ok-run"] == dummy_task
            mock_create_task.assert_called_once()

    asyncio.run(_run())


def test_runner_retry_run_without_worktree():
    import asyncio

    async def _run():
        from app.orchestration.runner import CodeAgentRunner
        
        test_runner = CodeAgentRunner()
        
        # 1. Run not found
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            res = await test_runner.retry_run("nonexistent")
            assert res is None
            
        # 2. Already running
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=True):
            mock_get.return_value = {"status": "failed", "worktree_path": None}
            with pytest.raises(ValueError, match="Run already in progress"):
                await test_runner.retry_run("some-run")
                
        # 3. Not in failed status
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=False):
            mock_get.return_value = {"status": "developing", "worktree_path": None}
            with pytest.raises(ValueError, match="Retry is only available for failed runs"):
                await test_runner.retry_run("some-run")
                
        # 4. Success path (no worktree): a retry starts a NEW execution with a
        #    fresh run_id, linked to the original via root/parent, and re-plans
        #    from scratch (status planning). Token usage is isolated because the
        #    new run_id has no prior token activity.
        dummy_task = MagicMock()
        mock_graph = AsyncMock()
        with patch.object(test_runner, "get_state", new_callable=AsyncMock) as mock_get, \
             patch.object(test_runner, "_task_running", return_value=False), \
             patch.object(test_runner, "_checkpointer") as mock_chk, \
             patch.object(test_runner, "_compile", new_callable=AsyncMock) as mock_compile, \
             patch("app.orchestration.runner.uuid.uuid4", return_value="new-exec"), \
             patch("app.orchestration.runner.upsert_run") as mock_upsert, \
             patch("app.orchestration.runner.set_ticket_run") as mock_set_ticket, \
             patch("asyncio.create_task", return_value=dummy_task) as mock_create_task:

            mock_compile.return_value = mock_graph
            state = {"status": "failed", "project_path": "/foo", "run_id": "ok-run", "worktree_path": None, "user_request": "add water", "ticket_id": 123}
            mock_get.return_value = state

            res = await test_runner.retry_run("ok-run")
            # get_state is mocked to return the same dict regardless of run_id.
            assert res == state
            # Task is registered under the NEW execution id, not the original.
            assert test_runner._tasks["new-exec"] == dummy_task
            assert "ok-run" not in test_runner._tasks
            mock_compile.assert_called_once()
            mock_graph.aupdate_state.assert_called_once()
            mock_upsert.assert_called_once()
            # New execution, fresh run_id, re-plans from scratch.
            mock_set_ticket.assert_called_once_with(123, "new-exec", status="planning")
            mock_create_task.assert_called_once()

            # The upserted row carries execution lineage back to the original run.
            seeded = mock_upsert.call_args.args[0]
            assert seeded["run_id"] == "new-exec"
            assert seeded["root_run_id"] == "ok-run"
            assert seeded["parent_run_id"] == "ok-run"
            assert seeded["attempt"] == 2

    asyncio.run(_run())
