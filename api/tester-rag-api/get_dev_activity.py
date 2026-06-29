import sqlite3

def run():
    conn = sqlite3.connect("data/code_agent_activity.db")
    cur = conn.cursor()
    cur.execute("SELECT phase, type, title, detail, files_json FROM activity_events WHERE run_id='d435450e-3d7a-46b5-835a-c3264148f6a8' ORDER BY seq ASC")
    
    for phase, type_, title, detail, files in cur.fetchall():
        if phase == "dev" and type_ == "llm":
            pass
        elif phase == "dev":
            print(f"DEV [{type_}]: {title} - {detail} (files: {files})")
        elif phase == "verifier":
            print(f"VERIFIER [{type_}]: {title}")

run()
