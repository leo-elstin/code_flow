import os
import time
import subprocess
import requests

def main():
    project_dir = os.path.abspath("dummy_app_test")
    os.makedirs(os.path.join(project_dir, "lib"), exist_ok=True)
    with open(os.path.join(project_dir, "pubspec.yaml"), "w") as f:
        f.write("name: dummy_app\nenvironment:\n  sdk: '>=3.0.0 <4.0.0'\n")
    with open(os.path.join(project_dir, "lib", "main.dart"), "w") as f:
        f.write("void main() { print('hello'); }\n")
    
    # Needs to be a git repo
    subprocess.run(["git", "init"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=project_dir, check=False)

    server_proc = subprocess.Popen([".venv/bin/uvicorn", "main:app", "--port", "8008"])
    time.sleep(3)

    try:
        resp = requests.post("http://127.0.0.1:8008/api/code-agent/run", json={
            "project_path": project_dir,
            "request": "Edit lib/main.dart. Add a new class `TestClass` but introduce a deliberate syntax error (like missing a semicolon or missing a curly brace). Allow the verifier gate to run dart analyze, catch the error, and then you must fix it automatically."
        })
        resp.raise_for_status()
        run_id = resp.json()["run_id"]
        print(f"Started run: {run_id}")

        while True:
            r = requests.get(f"http://127.0.0.1:8008/api/code-agent/runs/{run_id}")
            state = r.json()
            status = state.get("status")
            print("Status:", status)
            if status in ["awaiting_approval", "rejected", "failed"]:
                break
            time.sleep(2)
        
        if status == "awaiting_approval":
            print("Approving...")
            r = requests.post(f"http://127.0.0.1:8008/api/code-agent/runs/{run_id}/approve", json={"workspace_mode": "in_place"})
            r.raise_for_status()
        
        while True:
            r = requests.get(f"http://127.0.0.1:8008/api/code-agent/runs/{run_id}")
            state = r.json()
            status = state.get("status")
            print("Status:", status)
            if status in ["qa", "completed", "failed"]:
                break
            time.sleep(2)
            
        print("Final status:", status)
        
        act = requests.get(f"http://127.0.0.1:8008/api/code-agent/runs/{run_id}/activity")
        for event in act.json()["events"]:
            print(f"[{event['phase']}] {event['type']}: {event['title']}")
            
    finally:
        server_proc.terminate()
        server_proc.wait()

if __name__ == "__main__":
    main()
